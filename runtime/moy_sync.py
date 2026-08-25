"""Commit-shaped cart sync between the wasm head and a board's store (#197
mode 2, moycore plan 3.4 -- the PUSH half; the pull half is moy_webhost's
GET /carts.json, which shipped 2026-08-15).

The unit of sync is the COMMIT, and the design rides what already exists: the
console has no SAVE button, so a cart's durable state changes only at the
#111 commit points (typing-idle debounce + every exit path), each atomic and
whole-file-shaped. Sync therefore never needs an operation log or a session
model (the buried docked-mode machinery, plan 3.4): the browser WATCHES its
own carts root for files whose bytes changed, and ships the changed files.
Per-file last-writer-wins, both sides keep their own journal, done.

One body, three consumers, so the two sides cannot disagree about the wire:

  * `StoreWatcher` -- the BROWSER half (web_boot constructs one over the wasm
    VFS carts root). A stat-walk sweep (~1/s, driven by worker.js) detects
    changed/new/deleted files with no hook into the store at all -- the store
    writes through several funnels (moy_fs atomic dances, raw journal
    appends, os.rename), and watching the filesystem catches every writer by
    construction where a verb-level wrapper would miss the next one added.
  * `apply_ops` -- the RECEIVING half: the board (moy_webhost POST /sync),
    the dev server (web_runner/serve.py --carts), and the convergence harness all apply
    a batch through this one function. MicroPython-safe (os + json only);
    every landed file goes through moy_fs's crash-safe publish.
  * `_skip` -- the ONE predicate for what never crosses the wire, now shared
    by the pull walkers (moy_webhost imports it from here) and the push
    sweep. journal/ + thumbs/ + .bak stay home in BOTH directions: the pull
    decision (2026-08-22, "the browser gets carts, not their history") and
    its mirror -- a pushed journal line could only replay browser-era ops
    onto a board that has its own log.

What deliberately does NOT sync, recorded so it is not read as a gap:
  * Top-level files beside the cart folders (system.json, wifi.json, the
    shared sheet) -- system state, not the kid's work, and wifi.json is a
    secret. The pull skips non-directories at the root for the same reason;
    `apply_ops` refuses single-segment file paths outright.
  * The user-files layer (#108 files/) -- a SIBLING of the carts root, so
    neither walker ever sees it. The day drawings sync, they ride this same
    protocol with files_root as a second watched root.
  * Binary/unreadable files -- the wire is JSON text, same rule as the pull.

Wire shape (one POST per batch, bounded so it fits the transport's 64KB
request cap; the client sends ONE batch at a time and waits for the answer,
so ops apply in order):

    {"v": 1, "pin": "<optional>", "ops": [
      {"p": "cart.moy/main.py", "t": "<whole text>"},        # atomic write
      {"p": "cart.moy/big.lua", "t": "<piece>", "part": 0},  # chunked: begin
      {"p": "cart.moy/big.lua", "t": "<piece>", "part": 1},  #   ...append
      {"p": "cart.moy/big.lua", "pub": 1},                   #   ...publish
      {"p": "cart.moy/old.py", "d": 1},                      # delete file
      {"p": "cart.moy", "dc": 1}]}                           # delete cart

    -> {"ok": <applied count>, "err": [[index, "reason"], ...]}

A chunked file spans REQUESTS when it must (a 200KB main.lua cannot fit one
64KB POST): parts accumulate in `<path>.tmp` on the receiver and only `pub`
publishes, atomically, so a dropped connection mid-file leaves the previous
good copy untouched and the retry simply restarts at part 0.
"""

import json

try:
    import os
except ImportError:  # pragma: no cover
    os = None

try:
    from moy_fs import _mkdir, _write, _remove, _exists, _copy, _write_atomic
except ImportError:  # host / CPython: the runtime package
    from runtime.moy_fs import (_mkdir, _write, _remove, _exists, _copy,
                                _write_atomic)

try:
    from binascii import crc32 as _crc32
except ImportError:  # pragma: no cover -- every target ships binascii
    def _crc32(data, seed=0):
        h = seed
        for b in data:
            h = (h * 31 + b) & 0xFFFFFFFF
        return h


PROTOCOL_V = 1

# One chunk of a large file per op, and the total text budget of one batch.
# The transport (`moy_webserver._recv_request`) refuses requests past 64KB as
# an OOM guard, and JSON escaping expands source text, so both stay well
# under it.
PART_MAX = 16 * 1024
BATCH_BUDGET = 32 * 1024

# What never crosses the wire, in either direction. journal/ is the durable
# undo history (each side keeps its own); thumbs/ is a regenerable per-size
# cache; .bak/.tmp are moy_fs's crash-safety artifacts (and .tmp is also this
# module's own chunk-assembly staging, which a sweep must never re-ship).
SKIP_DIRS = ("thumbs", "__pycache__", "journal")
SKIP_FILES = ("journal.jsonl",)
SKIP_SUFFIXES = (".bak", ".tmp")


def _skip(name):
    if name in SKIP_DIRS or name in SKIP_FILES:
        return True
    for suf in SKIP_SUFFIXES:
        if name.endswith(suf):
            return True
    return False


def _entries(path, _listdir=None, _isdir=None):
    """(name, is_dir) for everything in `path`, in ONE directory traversal.

    `os.ilistdir` yields the TYPE alongside the name, which is the whole point:
    the obvious `listdir` + `stat`-each costs a full path resolution per entry,
    and on littlefs that is not a small constant.

    MEASURED ON P4 GLASS, 2026-08-14 (this walker was moy_webhost's until the
    push half made it shared) -- littlefs walks from the root on every path
    operation, so the cost is linear in how many entries the parent holds:

        stat /moy                          5.3 ms   (depth 1)
        stat /moy/carts                   28.9 ms   (depth 2, 46 entries)
        stat /moy/carts/<cart>/main.py    59.4 ms   (depth 4)

    A stat cost MORE than opening and reading an 11KB file (44.0 ms). The store
    walk was doing 271 of them, ~16s of a 27s pack, to learn something ilistdir
    hands over for free.

    The injected callables are for host tests with no real filesystem.
    """
    ils = getattr(os, "ilistdir", None)
    if ils is not None and _listdir is None:
        for e in ils(path):
            yield e[0], (e[1] & 0x4000) != 0
        return
    ld = _listdir or os.listdir
    isd = _isdir or _is_dir
    for name in ld(path):
        yield name, isd(path + "/" + name)


def _is_dir(path):
    try:
        return (os.stat(path)[0] & 0x4000) != 0
    except OSError:
        return False


def _read_text(path):
    try:
        with open(path, "r") as f:
            return f.read()
    except (OSError, UnicodeError, ValueError):
        return None


def _stat_file(path):
    """(size, mtime) or None. mtime is whatever the VFS reports -- the sweep
    only ever compares a file's mtime against its own previous value and
    against other mtimes from the same VFS, never against a wall clock, so
    second-granularity filesystems and MEMFS's JS epoch both work."""
    try:
        st = os.stat(path)
        return (st[6], st[8])
    except OSError:
        return None


# ---------------------------------------------------------------------------
# Path validation -- everything the receiving side trusts about a path.
# ---------------------------------------------------------------------------


def safe_segments(rel):
    """`rel` split into validated path segments, or None if it has no business
    being applied. An allowlist of shape, not a blocklist of tricks: forward
    slashes only, no empty/dot segments, no separators or control bytes inside
    a segment, and nothing the skip predicate keeps off the wire (a client
    that asks to write journal/ or a .bak is malformed by definition)."""
    if not isinstance(rel, str) or not rel or len(rel) > 256:
        return None
    parts = rel.split("/")
    for seg in parts:
        if not seg or seg in (".", ".."):
            return None
        for ch in ("\\", "\0", "\r", "\n"):
            if ch in seg:
                return None
        if _skip(seg):
            return None
    return parts


def _full(root, parts):
    return root + "/" + "/".join(parts)


# ---------------------------------------------------------------------------
# The receiving half.
# ---------------------------------------------------------------------------


def parse_batch(body):
    """The POST body -> (ops, pin) or (None, None) on anything malformed.
    Tolerant of bytes (the transport hands bytes)."""
    if isinstance(body, bytes):
        try:
            body = body.decode("utf-8")
        except Exception:  # noqa: BLE001
            return None, None
    try:
        doc = json.loads(body)
    except Exception:  # noqa: BLE001
        return None, None
    if not isinstance(doc, dict) or doc.get("v") != PROTOCOL_V:
        return None, None
    ops = doc.get("ops")
    if not isinstance(ops, list):
        return None, None
    return ops, doc.get("pin")


def apply_ops(root, ops):
    """Apply one batch into the store at `root`.

    Returns (applied, errors, shelf_dirty): how many ops landed, [(index,
    reason)] for the ones that did not (a bad op skips, it never aborts the
    batch -- the client's retry would just replay the same poison forever),
    and whether the SHELF needs a re-scan -- a cart appeared or disappeared,
    or a manifest/sheet changed (covers + titles live in RAM on the boards; a
    main.py edit needs no scan because code is read at cart open).
    """
    applied = 0
    errors = []
    shelf_dirty = False
    for i, op in enumerate(ops):
        try:
            reason, shelf = _apply_one(root, op)
        except Exception as exc:  # noqa: BLE001 -- one bad op never kills a batch
            reason, shelf = ("%s: %s" % (type(exc).__name__, exc)), False
        if reason is None:
            applied += 1
            shelf_dirty = shelf_dirty or shelf
        else:
            errors.append((i, reason))
    return applied, errors, shelf_dirty


def _apply_one(root, op):
    """-> (error_reason_or_None, shelf_dirty)."""
    if not isinstance(op, dict):
        return "not an op", False
    parts = safe_segments(op.get("p", ""))
    if parts is None:
        return "bad path", False
    if op.get("dc"):
        # Whole-cart delete: exactly one segment, and the receiver removes
        # everything under it INCLUDING what never crossed the wire (its
        # journal, its thumbs) -- a deleted cart's history dies with it, the
        # same as the on-device picker's delete.
        if len(parts) != 1:
            return "dc wants a cart folder", False
        _rmtree(_full(root, parts))
        return None, True
    if len(parts) < 2:
        # Never a top-level file: system.json / wifi.json / the shared sheet
        # are system state beside the carts, not the kid's work.
        return "not a cart file", False
    full = _full(root, parts)
    new_cart = not _exists(root + "/" + parts[0])
    if op.get("d"):
        _remove(full)
        return None, parts[-1] in ("manifest.json", "sheet.json")
    if op.get("pub"):
        _publish(full)
        shelf = (new_cart or parts[-1] in ("manifest.json", "sheet.json"))
        return None, shelf
    text = op.get("t")
    if not isinstance(text, str):
        return "no text", False
    if len(text) > PART_MAX * 2:
        return "op too large", False
    _mkdirs(root, parts[:-1])
    part = op.get("part")
    if part is None:
        _write_atomic(full, text)
    elif part == 0:
        _write(full + ".tmp", text)
        return None, False           # nothing published yet
    else:
        with open(full + ".tmp", "a") as f:
            f.write(text)
        return None, False
    shelf = (new_cart or parts[-1] in ("manifest.json", "sheet.json"))
    return None, shelf


def _publish(path):
    """Publish `<path>.tmp` as `path` -- steps 2-3 of moy_fs._write_atomic's
    crash-safe dance (the .tmp already holds the full new bytes), FAT
    rename-can't-clobber fallback included."""
    tmp = path + ".tmp"
    bak = path + ".bak"
    if not _exists(tmp):
        raise OSError("no staged tmp for " + path)
    if _exists(path):
        _remove(bak)
        try:
            os.rename(path, bak)
        except OSError:
            try:
                _copy(path, bak)
            except Exception:  # noqa: BLE001
                pass
    try:
        os.rename(tmp, path)
    except OSError:
        _copy(tmp, path)
        _remove(tmp)


def _mkdirs(root, parts):
    cur = root
    for seg in parts:
        cur = cur + "/" + seg
        _mkdir(cur)


def _rmtree(path, depth=0):
    """Recursive delete, bounded -- a cart folder is at most a few levels."""
    if depth > 6:
        return
    try:
        entries = list(_entries(path))
    except OSError:
        return
    for name, isdir in entries:
        full = path + "/" + name
        if isdir:
            _rmtree(full, depth + 1)
        else:
            _remove(full)
    try:
        os.rmdir(path)
    except OSError:
        pass


# ---------------------------------------------------------------------------
# The browser half: watch a carts root, ship what changed.
# ---------------------------------------------------------------------------


class StoreWatcher:
    """Detect changes to a carts root by sweeping it, and hand them out as
    wire batches. One in-flight batch at a time (`take` returns None until the
    caller `ack`s), so ops arrive at the receiver in the order they were
    taken and a failed send simply requeues.

    The snapshot holds (size, mtime, crc) per file. The fast path is the stat
    walk alone; content is only read (and crc'd) when size/mtime moved, or for
    files still "hot" -- whose mtime sat at the sweep's newest observed second
    -- because a same-second second write can leave size and mtime both
    unchanged on a second-granularity VFS. The crc also keeps a byte-identical
    rewrite (the reload path re-writing every pulled file) from re-shipping
    the whole store.
    """

    def __init__(self, root, listdir=None, isdir=None, read=None):
        self.root = root
        self._listdir = listdir      # injected for host tests; None = real fs
        self._isdir = isdir
        self._read = read or _read_text
        self._snap = {}              # rel -> (size, mtime, crc)
        self._pending = {}           # rel -> "w" | "d"
        self._pending_dc = []        # cart folders to delete, in order
        self._inflight = None        # (paths, dcs) awaiting ack
        self._partial = None         # (rel, text, next_part) mid-file resume
        self._hot = ()
        self.shipped = 0             # batches acked ok (diag)
        self.rebase()

    # -- baseline ------------------------------------------------------------

    def rebase(self):
        """Adopt the store AS IS -- nothing pending. Called at boot (the pull
        just wrote the board's own state, there is nothing to tell it) and
        after a reload re-pull (deliberate LWW: the board's copy was just
        chosen, so local unpushed edits are dropped, not replayed over it)."""
        self._snap = {}
        self._pending = {}
        self._pending_dc = []
        self._inflight = None
        self._partial = None
        self._hot = ()
        for rel, size, mtime in self._walk():
            text = self._read(self.root + "/" + rel)
            if text is not None:
                self._snap[rel] = (size, mtime, _crc(text))

    # -- change detection ----------------------------------------------------

    def sweep(self):
        """One pass over the store; queue every difference. Returns True when
        anything is pending (including carried-over failures)."""
        seen = {}
        carts = set()
        maxm = 0
        for rel, size, mtime in self._walk():
            seen[rel] = True
            carts.add(rel.split("/", 1)[0])
            if mtime > maxm:
                maxm = mtime
            old = self._snap.get(rel)
            if old is not None and old[0] == size and old[1] == mtime \
                    and rel not in self._hot:
                continue                      # the fast path: nothing moved
            text = self._read(self.root + "/" + rel)
            if text is None:
                continue                      # binary/unreadable: not synced
            c = _crc(text)
            if old is not None and old[2] == c:
                self._snap[rel] = (size, mtime, c)
                continue                      # touched, not changed
            self._snap[rel] = (size, mtime, c)
            self._pending[rel] = "w"
        for rel in list(self._snap):
            if rel in seen:
                continue
            del self._snap[rel]
            self._pending.pop(rel, None)
            top = rel.split("/", 1)[0]
            if top not in carts:
                if top not in self._pending_dc:
                    self._pending_dc.append(top)
            else:
                self._pending[rel] = "d"
        # Files written in the newest observed second get re-read next sweep:
        # a second write inside that same second is invisible to stat.
        self._hot = tuple(rel for rel, v in self._snap.items()
                          if v[1] >= maxm - 1)
        return bool(self._pending or self._pending_dc or self._partial)

    def _walk(self):
        """Yield (rel, size, mtime) for every syncable file: cart DIRS only
        (top-level files are system state), skip-filtered at every level."""
        for cart, isdir in self._sorted_entries(self.root):
            if not isdir or _skip(cart):
                continue
            for item in self._walk_dir(self.root + "/" + cart, cart, 0):
                yield item

    def _walk_dir(self, path, prefix, depth):
        if depth > 6:
            return
        for name, isdir in self._sorted_entries(path):
            if _skip(name):
                continue
            full = path + "/" + name
            rel = prefix + "/" + name
            if isdir:
                for item in self._walk_dir(full, rel, depth + 1):
                    yield item
                continue
            st = _stat_file(full) if self._listdir is None else (0, 0)
            if st is not None:
                yield rel, st[0], st[1]

    def _sorted_entries(self, path):
        try:
            return sorted(_entries(path, self._listdir, self._isdir))
        except OSError:
            return []

    # -- shipping ------------------------------------------------------------

    def take(self):
        """The next wire batch as a list of ops, or None (nothing pending, or
        a batch is already in flight). Marks what it takes; `ack` settles it.
        Reads file content NOW -- the freshest bytes win, which IS the
        conflict rule."""
        if self._inflight is not None:
            return None
        ops = []
        budget = BATCH_BUDGET
        paths = []
        dcs = []
        # Resume a file whose parts span batches first: its text was captured
        # when shipping began, so the receiver assembles one consistent
        # version even if the file changes again mid-flight (the change is
        # still pending and ships next).
        if self._partial is not None:
            rel, text, idx = self._partial
            budget = self._emit_parts(ops, rel, text, idx, budget)
            if self._partial is not None:        # still not done: batch full
                self._inflight = ([], [])
                return ops
            paths.append(rel)
        for cart in self._pending_dc:
            ops.append({"p": cart, "dc": 1})
            dcs.append(cart)
        for rel in sorted(self._pending):
            if budget <= 0:
                break
            kind = self._pending[rel]
            if kind == "d":
                ops.append({"p": rel, "d": 1})
                paths.append(rel)
                continue
            text = self._read(self.root + "/" + rel)
            if text is None:                     # vanished since the sweep
                ops.append({"p": rel, "d": 1})
                paths.append(rel)
                continue
            if len(text) > PART_MAX:
                budget = self._emit_parts(ops, rel, text, 0, budget)
                if self._partial is not None:
                    del self._pending[rel]
                    self._inflight = (paths, dcs)
                    return ops
                paths.append(rel)
                continue
            ops.append({"p": rel, "t": text})
            budget -= len(text)
            paths.append(rel)
        if not ops:
            return None
        for rel in paths:
            self._pending.pop(rel, None)
        self._pending_dc = [c for c in self._pending_dc if c not in dcs]
        self._inflight = (paths, dcs)
        return ops

    def _emit_parts(self, ops, rel, text, idx, budget):
        """Emit chunk ops for `text` from part `idx` until done or the budget
        runs out; sets/clears self._partial accordingly."""
        total = (len(text) + PART_MAX - 1) // PART_MAX
        while idx < total and budget > 0:
            piece = text[idx * PART_MAX:(idx + 1) * PART_MAX]
            ops.append({"p": rel, "t": piece, "part": idx})
            budget -= len(piece)
            idx += 1
        if idx < total:
            self._partial = (rel, text, idx)
        else:
            ops.append({"p": rel, "pub": 1})
            self._partial = None
        return budget

    def take_json(self, pin=None):
        ops = self.take()
        if not ops:
            return ""
        doc = {"v": PROTOCOL_V, "ops": ops}
        if pin:
            doc["pin"] = pin
        return json.dumps(doc)

    def ack(self, ok):
        """Settle the in-flight batch. ok=False requeues everything it carried
        (a mid-file partial restarts from part 0 -- the receiver's part-0 op
        truncates the staging tmp, so a retry is always clean)."""
        fl = self._inflight
        self._inflight = None
        if fl is None:
            return
        paths, dcs = fl
        if ok:
            self.shipped += 1
            return
        if self._partial is not None:
            # The failed batch carried part of a file still mid-flight: its
            # rel lives in neither `paths` nor pending (take() consumed it),
            # so requeue it here or it is silently never shipped.
            self._pending[self._partial[0]] = "w"
            self._partial = None
        for rel in paths:
            if rel not in self._pending:
                self._pending[rel] = "w" if rel in self._snap else "d"
        for cart in dcs:
            if cart not in self._pending_dc:
                self._pending_dc.append(cart)


def _crc(text):
    return _crc32(text.encode("utf-8")) & 0xFFFFFFFF
