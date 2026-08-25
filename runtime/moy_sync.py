"""Commit-shaped store sync between the wasm head and a board (#197 mode 2,
moycore plan 3.4 -- the PUSH half; the pull half is moy_webhost's
GET /carts.json + GET /files.json).

The unit of sync is the COMMIT, and the design rides what already exists: the
console has no SAVE button, so a cart's durable state changes only at the
#111 commit points (typing-idle debounce + every exit path), each atomic and
whole-file-shaped. Sync therefore never needs an operation log or a session
model (the buried docked-mode machinery, plan 3.4): the browser WATCHES its
own store for files whose bytes changed, and ships the changed files.
Per-file last-writer-wins, done.

THE JOURNAL LIVES WITH THE STORE OF RECORD (owner call 2026-08-25). This
replaces "both sides keep their own journal", which was true and useless: the
browser's VFS is a scratch copy that dies with the tab, so a kid's undo history
lived in the one place it could not survive, and a board that took a browser's
edits had no record of them at all. There is exactly ONE durable journal per
cart, and it sits wherever that cart durably LIVES:

  * a page served BY A BOARD -- the board's store is of record, so the RECEIVER
    journals: `apply_ops(..., journal=True)` appends a #111 commit for every
    carts-root file a batch publishes, in the shape the console's own commits
    use, so on-glass UNDO walks back through work done in the browser. The
    browser's VFS journal is still written locally and still never crosses; it
    is now correctly read as session-local undo.
  * a page on a STATIC host -- the browser's OPFS is of record (#193), so the
    journal persists THERE: web_boot builds its carts watcher with
    `skip=skip_keep_journal` and the journal files ride the ordinary sweep into
    the local store, coming back on the next visit with the carts.

The wire predicate itself never moves: `_skip` refuses journal paths, so a
BOARD-mode batch is byte-identical to what it always was and no board is ever
sent somebody else's history. The site-mode relaxation is a watcher argument,
not a change of what "the wire" means.

The #108 files root is deliberately NOT part of this. Its undo lives in
`files/.history/` op sidecars (a different mechanism with a different shape),
and no half of it is journaled by the receiver: a drawing pushed from a browser
lands as a file and nothing else. That asymmetry is recorded rather than fixed
because the two histories are not the same object.

TWO ROOTS since 2026-08-25 (owner call, "they should get synced"): the carts
root, and the #108 user-files layer beside it -- the kid's drawings, docs,
tables, sprite sheets, songs and recordings. One protocol, one watcher class,
one apply; a batch carries which root it speaks for and never mixes the two.

One body, three consumers, so the two sides cannot disagree about the wire:

  * `StoreWatcher` -- the BROWSER half (web_boot constructs one per root over
    the wasm VFS). A stat-walk sweep (~1/s, driven by worker.js) detects
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
    onto a board that has its own log. `skip_keep_journal` is the SITE-MODE
    variant, and it is a watcher argument rather than a second wire rule.

What deliberately does NOT sync, recorded so it is not read as a gap:
  * Top-level files beside the cart folders (system.json, wifi.json, the
    shared sheet) -- system state, not the kid's work, and wifi.json is a
    secret. The pull skips non-directories at the root for the same reason;
    `apply_ops` refuses single-segment file paths outright.
  * In the files root, anything whose first segment is not one of
    moy_carts.FILE_KINDS -- which is how `files/.history/` and `files/trash/`
    stay home, in BOTH directions, with no second skip list to keep in step.
    `.history` is the files layer's journal-equivalent (#111 op sidecars), so
    it stays for the same reason journal/ does: each side keeps its own undo
    history. `trash` is a LOCAL recovery bin, and syncing a deletion that is
    still recoverable here but not there turns last-writer-wins into data
    loss -- a peer would land the trashed copy back as a live file, or drop
    the only copy the kid could still restore.
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

A files batch is the same ops under a VERSION BUMP that names its root:

    {"v": 2, "root": "files", "ops": [
      {"p": "drawings/sunset.moyimg", "t": "<whole text>"},
      {"p": "recordings/take_1", "dc": 1}]}                  # a folder item

The bump is the back-compat mechanism, and it is a safety property rather
than politeness. A board flashed before this reads `v != 1` and answers 400
"bad batch"; without the bump its `apply_ops` would happily take
`drawings/sunset.moyimg` for a cart-relative path and write it into the CARTS
root, inventing a `drawings` cart on the kid's shelf. A refusal is the only
acceptable failure. `v: 1` keeps the carts batch byte-identical to what those
boards already parse, so the pull-and-push loop they have keeps working
untouched -- and a v1 batch that tries to smuggle a `root` key is refused too.
In practice no files batch is ever aimed at such a board: the browser only
builds a files watcher when the board answered GET /files.json (web_boot),
so the version check is the second line of defense, not the first.

A chunked file spans REQUESTS when it must (a 200KB main.lua cannot fit one
64KB POST): parts accumulate in `<path>.tmp` on the receiver and only `pub`
publishes, atomically, so a dropped connection mid-file leaves the previous
good copy untouched and the retry simply restarts at part 0.
"""

import json
import time

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


# The carts batch keeps v1 FOREVER: it is the shape every already-flashed
# board parses, and a carts push must keep working across an old board and a
# new browser. A batch for any other root carries v2 + an explicit `root`, so
# a v1 receiver refuses it instead of applying files paths into the carts
# store (see the module docstring's back-compat note).
PROTOCOL_V = 1
PROTOCOL_V_ROOTED = 2
CARTS_ROOT_ID = "carts"
FILES_ROOT_ID = "files"
# ROOT_IDS is derived from the registry (below files_root, which a Root needs to
# name its path); it stays importable under this name for callers that had it.

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


# The SITE-MODE store's skip list: the same rule minus the journal. In mode 1
# the browser's OPFS is the store of record, so its undo history has to travel
# with it or die at the next reload -- which is #193's "with its undo history",
# and the whole of what the relaxation buys. thumbs/ stays out (a regenerable
# per-size cache), and .bak/.tmp stay out because they are moy_fs's crash-safety
# artifacts and this module's own chunk staging: re-shipping a .tmp would hand
# the store a half-written file under a name the next sweep would then re-read.
SITE_SKIP_DIRS = ("thumbs", "__pycache__")
SITE_SKIP_FILES = ()


def _skip(name):
    if name in SKIP_DIRS or name in SKIP_FILES:
        return True
    for suf in SKIP_SUFFIXES:
        if name.endswith(suf):
            return True
    return False


def skip_keep_journal(name):
    """`_skip` with journal/ + journal.jsonl allowed through -- the predicate a
    SITE-MODE watcher takes, and nothing else ever does.

    Never the wire's rule and never a receiver's: `safe_segments` still refuses
    a journal path outright, so a board cannot be handed one no matter which
    watcher built the batch. What this changes is only which files a browser
    sweeps out of its own VFS and into its own OPFS.
    """
    if name in SITE_SKIP_DIRS or name in SITE_SKIP_FILES:
        return True
    for suf in SKIP_SUFFIXES:
        if name.endswith(suf):
            return True
    return False


# ---------------------------------------------------------------------------
# The #108 files layer, reached through ONE window so the guarded import (frozen
# `moy_carts` on a board, `runtime.moy_carts` on the host) is written once. The
# push sweep, the receiving apply and moy_webhost's pull walker all come here
# rather than importing moy_carts themselves.
# ---------------------------------------------------------------------------

_CARTS = []


def _moy_carts():
    if not _CARTS:
        try:
            import moy_carts as mc
        except ImportError:                  # host / CPython: the runtime package
            try:
                from runtime import moy_carts as mc
            except ImportError:              # pragma: no cover -- no store at all
                return None
        _CARTS.append(mc)
    return _CARTS[0]


_JOURNAL = []


def _moy_journal():
    """The #111 journal module, through the same guarded window `moy_carts`
    rides. None where there is none -- a receiver with no journal module (the
    convergence harness, an old headless store) simply does not journal, which
    is a missing history and never a refused write."""
    if not _JOURNAL:
        try:
            import moy_journal as mj
        except ImportError:              # host / CPython: the runtime package
            try:
                from runtime import moy_journal as mj
            except ImportError:          # pragma: no cover -- no journal at all
                return None
        _JOURNAL.append(mj)
    return _JOURNAL[0]


def file_kinds():
    """The names a files-root path may start with -- moy_carts.FILE_KINDS, never
    a second copy of it, so a kind added there reaches the wire by default.

    This ONE allowlist is also what keeps `files/.history/` and `files/trash/`
    home in both directions: neither is a kind, so no walker descends into them
    and no op naming one is ever applied. Returns None when there is no store
    module to ask, which every caller reads as "refuse", never as "allow".
    """
    mc = _moy_carts()
    return None if mc is None else mc.FILE_KINDS


def files_root(carts_root):
    """The user-files root beside `carts_root` (moy_carts' own sibling rule), or
    None when the store module is missing."""
    mc = _moy_carts()
    return None if mc is None else mc.files_root(carts_root)


# ---------------------------------------------------------------------------
# The SYNC ROOT REGISTRY. A syncable store is DATA here -- not an `if files:`
# branch scattered through the wire parser, the apply, the webhost endpoints,
# the browser watchers and the OPFS store. Every per-root behaviour that used to
# be such a branch is a field on its Root, so ADDING A STORE is one entry and
# nothing else moves. The two we ship are carts and the #108 user files; a third
# (a shared-projects store, say) would be a third descriptor and no new plumbing.
#
# The JS side mirrors this list (firmware/web_runner/moy_store.mjs ROOTS), pinned
# by tests/test_web_store.py, because a STATIC host has no server to ask for it --
# the roots are ours and few, so a mirrored constant is honest where a fetched
# manifest would only move the coupling.
# ---------------------------------------------------------------------------

# The files whose write can change what the LAUNCHER shows -- a cover sheet or a
# manifest (title/order). Only a shelf-bearing root consults this.
_SHELF_FILES = ("manifest.json", "sheet.json")


class Root:
    """One syncable store, described by data. The fields are exactly the places
    the two stores used to differ:

      wire     the protocol version a batch for this store carries. Carts is 1
               (the shape every flashed board already parses); every other root
               is 2 + an explicit `root`, which is what makes an old board REFUSE
               it rather than misapply its paths.
      path     (carts_root) -> where this store lives. Carts IS carts_root; the
               files root is its sibling.
      kinds    the first path segment must be a `moy_carts.FILE_KINDS` name --
               which is also what keeps `.history`/`trash` home, in both
               directions, with no second skip list.
      journals a publish here writes #111 history when the receiver is of record
               (the board journaling a browser's pushes).
      shelf    a change here can dirty the launcher (a cart born/dying, a
               manifest/sheet edit). The files root never touches the shelf.
      ensure   mkdir the root on the first write -- the files root is created
               lazily, the first thing a peer sends may BE its first drawing.
      dc_min/  a whole-folder delete names a path this many segments deep. A cart
      dc_max   is exactly one segment; a files item is a kind/name (2) or a
               recording folder below it (>=2, so dc_max is None).
      site_keep_journal
               in SITE mode (the browser's OPFS is of record) this root's watcher
               sweeps `journal/` too, so the kid's undo history survives the tab.
               Only carts relaxes; the files layer's history is `.history/`
               sidecars, a different mechanism that stays home either way.
    """

    def __init__(self, id, wire, path, endpoint, kinds=False, journals=False,
                 shelf=False, ensure=False, dc_min=1, dc_max=1,
                 site_keep_journal=False):
        self.id = id
        self.wire = wire
        self._path = path
        self.endpoint = endpoint
        self.kinds = kinds
        self.journals = journals
        self.shelf = shelf
        self.ensure = ensure
        self.dc_min = dc_min
        self.dc_max = dc_max
        self.site_keep_journal = site_keep_journal

    def path(self, carts_root):
        return self._path(carts_root)

    def watch_skip(self, site):
        """What a watcher over this root will NOT sweep -- the wire's `_skip` by
        default (None lets StoreWatcher supply it), relaxed to keep the journal
        only for the one root+mode that is of record in the browser."""
        return skip_keep_journal if (site and self.site_keep_journal) else None


CARTS_ROOT = Root(
    CARTS_ROOT_ID, PROTOCOL_V, lambda carts_root: carts_root, "/carts.json",
    shelf=True, journals=True, dc_min=1, dc_max=1, site_keep_journal=True)

FILES_ROOT = Root(
    # A lambda, not the bare `files_root` reference, so the name resolves at CALL
    # time: a host with no `moy_carts` (the headless XIAO cart store) answers None
    # here and the endpoint 404s, exactly as it should, per call rather than per
    # registry construction.
    FILES_ROOT_ID, PROTOCOL_V_ROOTED, lambda carts_root: files_root(carts_root),
    "/files.json", kinds=True, ensure=True, dc_min=2, dc_max=None)

SYNC_ROOTS = (CARTS_ROOT, FILES_ROOT)
ROOT_IDS = tuple(r.id for r in SYNC_ROOTS)
_ROOT_BY_ID = {r.id: r for r in SYNC_ROOTS}


def root_by_id(root_id):
    """The Root for a wire root id, or None."""
    return _ROOT_BY_ID.get(root_id)


def root_for_wire(v, named):
    """The Root a batch (version `v`, optional `named` root field) speaks for, or
    None when the pair is not one this build serves -- which a receiver treats as
    "refuse", never "guess". A v1 batch is carts BY DEFINITION and one that
    smuggles a different root is refused rather than read as carts."""
    if v == PROTOCOL_V:
        if named is not None and named != CARTS_ROOT_ID:
            return None
        return CARTS_ROOT
    if v == PROTOCOL_V_ROOTED:
        r = _ROOT_BY_ID.get(named)
        return r if (r is not None and r.wire == PROTOCOL_V_ROOTED) else None
    return None


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
        # Materialize the whole listing (not lazily) so a transient OSError is
        # RETRYABLE: a removable card (the Guition's TF store) can EIO a read
        # that lands seconds into a socket-paced pull, and a bare `for e in
        # ils(...)` would abort the store stream mid-body -- the browser then
        # gets a truncated carts.json and a dead boot. EIO on removable media
        # is the textbook retry case; a re-listing almost always succeeds.
        for e in _retry_io(lambda: list(ils(path)), ()):
            yield e[0], (e[1] & 0x4000) != 0
        return
    ld = _listdir or os.listdir
    isd = _isdir or _is_dir
    for name in ld(path):
        yield name, isd(path + "/" + name)


# How many times a store-walk read is re-tried before it is given up on, and
# how long between tries. Small on purpose: a healthy card never retries, and a
# card so flaky it fails three reads in a row is one to replace, not to wait on.
_IO_RETRIES = 3
_IO_BACKOFF_MS = 20


def _retry_io(fn, default):
    """Run `fn`, retrying a bounded number of times on OSError (a transient
    card EIO), then return `default` rather than propagate -- an aborted store
    walk is strictly worse than an omitted entry, because the abort truncates
    the whole chunked response. Non-OSError propagates: only I/O is transient."""
    last = None
    for i in range(_IO_RETRIES):
        try:
            return fn()
        except OSError as exc:              # noqa: BLE001 -- transient card I/O
            last = exc
            _sleep = getattr(time, "sleep_ms", None)
            if _sleep is not None:
                _sleep(_IO_BACKOFF_MS)
            elif i + 1 < _IO_RETRIES:
                time.sleep(_IO_BACKOFF_MS / 1000.0)
    try:
        print("moy_sync: read gave up after %d tries: %s" % (_IO_RETRIES, last))
    except Exception:                        # noqa: BLE001 -- a log is never fatal
        pass
    return default


def _is_dir(path):
    try:
        return (os.stat(path)[0] & 0x4000) != 0
    except OSError:
        return False


def _read_text(path):
    # Retried on OSError for the same reason _entries is: a flaky-card read a
    # few seconds into a store pull should re-try, not silently drop the file
    # from the browser's copy. UnicodeError/ValueError are NOT retried -- a
    # binary file is binary every time -- so they stay None (skip, never crash).
    def _open():
        try:
            with open(path, "r") as f:
                return f.read()
        except (UnicodeError, ValueError):
            return None                      # binary/unreadable: skip, permanent
    return _retry_io(_open, None)


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
    """The POST body -> (ops, pin, root_id) or (None, None, None) on anything
    malformed. Tolerant of bytes (the transport hands bytes).

    `root_id` is which store the ops speak for: "carts" for the v1 shape every
    flashed board already parses, or whatever a v2 batch names. An unknown root
    is malformed on purpose -- a receiver must never guess where a path lands.
    """
    if isinstance(body, bytes):
        try:
            body = body.decode("utf-8")
        except Exception:  # noqa: BLE001
            return None, None, None
    try:
        doc = json.loads(body)
    except Exception:  # noqa: BLE001
        return None, None, None
    if not isinstance(doc, dict):
        return None, None, None
    root = root_for_wire(doc.get("v"), doc.get("root"))
    if root is None:
        return None, None, None
    ops = doc.get("ops")
    if not isinstance(ops, list):
        return None, None, None
    return ops, doc.get("pin"), root.id


def apply_ops(root, ops, root_id=CARTS_ROOT_ID, journal=False):
    """Apply one batch into the store at path `root`, whose SHAPE is `root_id`
    ("carts" or "files" -- what parse_batch returned).

    Returns (applied, errors, shelf_dirty): how many ops landed, [(index,
    reason)] for the ones that did not (a bad op skips, it never aborts the
    batch -- the client's retry would just replay the same poison forever),
    and whether the SHELF needs a re-scan -- a cart appeared or disappeared,
    or a manifest/sheet changed (covers + titles live in RAM on the boards; a
    main.py edit needs no scan because code is read at cart open). A files
    batch never dirties the shelf: the launcher renders no drawings, and the
    Files app scans its kinds when it opens.

    `journal=True` says THIS STORE IS OF RECORD, so every carts-root file the
    batch publishes also gets a #111 commit (see the module docstring). The
    boards pass it; the browser's own OPFS apply is JavaScript and journals by
    sweeping the files rather than by calling this.

    The cost is honest and worth stating where it is paid: `journal_append`
    writes a FULL SNAPSHOT per file plus a log line plus an atomic cursor
    rewrite, so a three-file cart commit is ~a dozen small littlefs/FAT
    operations. That is fine at cart sizes -- the console's own commits have
    always paid exactly this -- and it is why the flag exists rather than the
    behaviour being unconditional: a receiver that is a scratch directory (the
    convergence harness) should not grow a history nobody will ever walk.
    """
    desc = root_by_id(root_id)
    if desc is None:                     # parse_batch gates this; belt to braces
        return 0, [(i, "unknown root") for i in range(len(ops))], False
    applied = 0
    errors = []
    shelf_dirty = False
    for i, op in enumerate(ops):
        try:
            reason, shelf = _apply_one(root, op, desc, journal)
        except Exception as exc:  # noqa: BLE001 -- one bad op never kills a batch
            reason, shelf = ("%s: %s" % (type(exc).__name__, exc)), False
        if reason is None:
            applied += 1
            shelf_dirty = shelf_dirty or shelf
        else:
            errors.append((i, reason))
    return applied, errors, shelf_dirty


def _files_shape(parts, op):
    """None when `parts` is a legal user-files path, else the refusal reason.

    Tighter than the carts root because the files root has a fixed vocabulary:
    the first segment must be a kind moy_carts knows, which refuses `.history`
    and `trash` by construction. `dc` is a whole-FOLDER delete, so it is refused
    on a kind dir (that would wipe every drawing the kid owns because one item
    left our copy) and allowed from depth 2 down, where a folder-valued
    recording is exactly one item.
    """
    kinds = file_kinds()
    if kinds is None:                    # no store module: refuse, never guess
        return "no files layer"
    if parts[0] not in kinds:
        return "not a file kind"
    if len(parts) < 2:
        return "dc wants an item" if op.get("dc") else "not a file"
    return None


def _journal_commit(root, parts, text):
    """Record a #111 commit for a carts-root file this batch just published.

    The exact call `Project.commit_*` makes -- `journal_append(cart_dir,
    rel_file, new_bytes)` -- so a browser-made edit is indistinguishable from a
    keyboard-made one to the Editor's UNDO, which is the whole point. One batch
    is one commit's worth of files, which is already the shape a sweep hands
    over.

    Guarded end to end: the FILE HAS ALREADY LANDED by the time this runs, so a
    journal that cannot be written costs a history entry and must never cost the
    write. A store with no journal module simply has no history.
    """
    mj = _moy_journal()
    if mj is None:
        return
    try:
        mj.journal_append(root + "/" + parts[0], "/".join(parts[1:]), text)
    except Exception as exc:  # noqa: BLE001 -- the file is already durable
        print("SYNC journal failed:", exc)


def _shelf_dirty(desc, parts, new_item):
    """Whether landing `parts` in `desc` should make the launcher re-scan -- a
    root without a shelf never does; one with a shelf does for a new top-level
    folder or a change to a shelf-visible file (a cover sheet, a manifest)."""
    return desc.shelf and (new_item or parts[-1] in _SHELF_FILES)


def _apply_one(root, op, desc, journal=False):
    """Apply one op into `root` (the store path) whose SHAPE is `desc` (a Root).
    -> (error_reason_or_None, shelf_dirty). Every branch that used to test
    `root_id == FILES_ROOT_ID` now reads a field off `desc`."""
    if not isinstance(op, dict):
        return "not an op", False
    parts = safe_segments(op.get("p", ""))
    if parts is None:
        return "bad path", False
    if desc.kinds:
        # A kinded root (files) has a fixed vocabulary: the first segment must be
        # a known kind, which also refuses `.history`/`trash` by construction.
        bad = _files_shape(parts, op)
        if bad:
            return bad, False
    if op.get("dc"):
        # Whole-folder delete: the receiver removes everything under it INCLUDING
        # what never crossed the wire (a cart's journal, its thumbs) -- a deleted
        # cart's history dies with it, the same as the on-device picker. The
        # folder is a CART (one segment) in the carts root, an ITEM (>=2) in the
        # files root -- `desc.dc_min/dc_max` say which.
        n = len(parts)
        if n < desc.dc_min or (desc.dc_max is not None and n > desc.dc_max):
            return "bad dc target", False
        _rmtree(_full(root, parts))
        return None, desc.shelf
    if len(parts) < 2:
        # Never a top-level file: system.json / wifi.json / the shared sheet are
        # system state beside the store, not the kid's work.
        return "not a store file", False
    full = _full(root, parts)
    new_item = desc.shelf and not _exists(root + "/" + parts[0])
    if op.get("d"):
        _remove(full)
        return None, desc.shelf and parts[-1] in _SHELF_FILES
    if op.get("pub"):
        _publish(full)
        if journal and desc.journals:
            # Read the file BACK rather than re-assembling the chunks: they
            # arrived across several requests and were never all resident here,
            # which is the point of chunking. One bounded read is the cheap half
            # of the snapshot this is about to write anyway.
            _journal_commit(root, parts, _read_text(full))
        return None, _shelf_dirty(desc, parts, new_item)
    text = op.get("t")
    if not isinstance(text, str):
        return "no text", False
    if len(text) > PART_MAX * 2:
        return "op too large", False
    if desc.ensure:
        # A lazily-created root (files) may not exist until the first write, and
        # the first thing a peer sends may BE that. Its parent is the carts
        # root's parent, which is already there.
        _mkdir(root)
    _mkdirs(root, parts[:-1])
    part = op.get("part")
    if part is None:
        _write_atomic(full, text)
        if journal and desc.journals:
            _journal_commit(root, parts, text)
    elif part == 0:
        _write(full + ".tmp", text)
        return None, False           # nothing published yet
    else:
        with open(full + ".tmp", "a") as f:
            f.write(text)
        return None, False
    return None, _shelf_dirty(desc, parts, new_item)


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
# The browser half: watch a root, ship what changed.
# ---------------------------------------------------------------------------


class StoreWatcher:
    """Detect changes to a store root by sweeping it, and hand them out as
    wire batches. One in-flight batch at a time (`take` returns None until the
    caller `ack`s), so ops arrive at the receiver in the order they were
    taken and a failed send simply requeues.

    `root_id` picks which store this watches -- "carts" (cart folders, the
    default) or "files" (the #108 user-files root). It is the only difference
    between the two: which top-level names are legal, what a whole-folder
    delete is a folder OF, and the protocol version the batch is stamped with.

    `skip` is what this watcher will not sweep, defaulting to the wire's own
    `_skip`. The ONE caller that passes anything else is a SITE-MODE web_boot,
    which hands it `skip_keep_journal` so the browser's undo history reaches the
    browser's own store (see the module docstring). A board-mode watcher takes
    the default and is byte-identical to what it always was.

    The snapshot holds (size, mtime, crc) per file. The fast path is the stat
    walk alone; content is only read (and crc'd) when size/mtime moved, or for
    files still "hot" -- whose mtime sat at the sweep's newest observed second
    -- because a same-second second write can leave size and mtime both
    unchanged on a second-granularity VFS. The crc also keeps a byte-identical
    rewrite (the reload path re-writing every pulled file) from re-shipping
    the whole store.
    """

    def __init__(self, root, listdir=None, isdir=None, read=None,
                 root_id=CARTS_ROOT_ID, skip=None):
        self.root = root
        self.root_id = root_id
        self.skip = skip or _skip    # what this watcher will not sweep
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
        units = set()
        maxm = 0
        for rel, size, mtime in self._walk():
            seen[rel] = True
            unit = self._unit(rel)
            if unit is not None:
                units.add(unit)
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
            unit = self._unit(rel)
            if unit is not None and unit not in units:
                if unit not in self._pending_dc:
                    self._pending_dc.append(unit)
            else:
                self._pending[rel] = "d"
        # Files written in the newest observed second get re-read next sweep:
        # a second write inside that same second is invisible to stat.
        self._hot = tuple(rel for rel, v in self._snap.items()
                          if v[1] >= maxm - 1)
        return bool(self._pending or self._pending_dc or self._partial)

    def _unit(self, rel):
        """The FOLDER a vanished `rel` would be deleted as part of, or None when
        the file is its own unit and a plain `d` is the right op.

        In the carts root the unit is the cart folder. In the files root it is
        the ITEM -- `<kind>/<name>` -- and only for a path deeper than that: a
        flat drawing is one file, and shipping `dc drawings/x.moyimg` would ask
        the receiver to rmtree a file (which quietly does nothing), while a kind
        dir is never a unit at all, or losing one recording would delete the
        peer's whole recordings folder.
        """
        parts = rel.split("/")
        if self.root_id != FILES_ROOT_ID:
            return parts[0]
        return parts[0] + "/" + parts[1] if len(parts) > 2 else None

    def _walk(self):
        """Yield (rel, size, mtime) for every syncable file, skip-filtered at
        every level. Top-level FILES are never syncable (system state beside the
        store), and in the files root a top-level dir must be a known kind --
        which is what leaves `.history` and `trash` unwalked."""
        kinds = file_kinds() if self.root_id == FILES_ROOT_ID else None
        for top, isdir in self._sorted_entries(self.root):
            if not isdir or self.skip(top):
                continue
            if kinds is not None and top not in kinds:
                continue
            for item in self._walk_dir(self.root + "/" + top, top, 0):
                yield item

    def _walk_dir(self, path, prefix, depth):
        if depth > 6:
            return
        for name, isdir in self._sorted_entries(path):
            if self.skip(name):
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

    def busy(self):
        """True while a batch is out and unanswered. `take` already declines in
        that state; a caller watching SEVERAL roots needs to ask before it moves
        on to the next one, because the one-batch-in-flight rule is about the
        transport, not about any single root."""
        return self._inflight is not None

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
        for unit in self._pending_dc:
            ops.append({"p": unit, "dc": 1})
            dcs.append(unit)
        # CLEAR the dcs the moment they are emitted, not at the completion path
        # below -- a batch that then starts a big file returns early (mid-file),
        # and leaving them pending re-emits the same dc on the next batch. That
        # is a no-op if the cart stays deleted, but re-deletes a cart that was
        # RECREATED between the two batches. `dcs` rides `_inflight`, so ack(False)
        # requeues them.
        self._pending_dc = []
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
            # A whole-file op (<=PART_MAX) subtracts AFTER it is appended, so
            # left unguarded it can push a batch past BATCH_BUDGET by a whole
            # file -- and a ~48KB batch, JSON-escaped, can breach the transport's
            # 64KB request cap, truncating the body to invalid JSON and wedging
            # the client on a permanent 400/requeue. Defer a file that would
            # overshoot to the next batch; a single op on an EMPTY batch always
            # ships (it is <=PART_MAX, comfortably under the cap).
            if ops and len(text) > budget:
                break
            ops.append({"p": rel, "t": text})
            budget -= len(text)
            paths.append(rel)
        if not ops:
            return None
        for rel in paths:
            self._pending.pop(rel, None)
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
        """The next batch as wire JSON. A carts batch keeps the v1 shape older
        boards parse; any other root rides v2 + an explicit `root`, which is
        what makes those boards REFUSE it instead of misapplying its paths."""
        ops = self.take()
        if not ops:
            return ""
        root = root_by_id(self.root_id) or CARTS_ROOT
        if root.wire == PROTOCOL_V:
            doc = {"v": PROTOCOL_V, "ops": ops}
        else:
            doc = {"v": root.wire, "root": root.id, "ops": ops}
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
        for unit in dcs:
            if unit not in self._pending_dc:
                self._pending_dc.append(unit)


def _crc(text):
    return _crc32(text.encode("utf-8")) & 0xFFFFFFFF
