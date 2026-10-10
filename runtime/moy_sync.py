# Map (grep -n a name to jump there):
#   Root                one syncable store, described by data
#   root_by_id          the Root for a wire root id
#   parse_batch         a POST body -> (ops, pin, root id)
#   apply_ops           apply one batch into a store (the kernel's C)
#   StoreWatcher        detect a store's changes and queue them as batches
#   StoreWatcher.sweep  one pass over the store
#   StoreWatcher.take   the next wire batch
#   StoreWatcher.ack    settle the batch in flight
"""Commit-shaped store sync between the wasm head and a board (#197 mode 2,
moycore plan 3.4 -- the PUSH half; the pull half is the webhost's
GET /carts.json + GET /files.json, native/moy_net/moy_webhost.c).

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
sprite sheets, songs and recordings. One protocol, one watcher class,
one apply; a batch carries which root it speaks for and never mixes the two.

One body, three consumers, so the two sides cannot disagree about the wire:

  * `StoreWatcher` -- the BROWSER half (web_boot constructs one per root over
    the wasm VFS). A stat-walk sweep (~1/s, driven by worker.js) detects
    changed/new/deleted files with no hook into the store at all -- the store
    writes through several funnels (moy_fs atomic dances, raw journal
    appends, os.rename), and watching the filesystem catches every writer by
    construction where a verb-level wrapper would miss the next one added.
  * `apply_ops` -- the RECEIVING half, which is C: native/moy_net's
    moy_sync_apply.c, over native/moy_store. The board's webhost applies a
    POST /sync through it with no Python in the way, and the dev server
    (web_runner/serve.py --carts) and the convergence harness call it here;
    every landed file goes through moy_fs's crash-safe publish. Which store a
    batch speaks for (`parse_batch`) is the same C's rule.
  * `_skip` -- the ONE predicate for what never crosses the wire
    (moy_store_skip, native/moy_store/moy_fs.c), shared by the pull walker
    and the push sweep. journal/ + thumbs/ + .bak stay home in BOTH directions: the pull
    decision (2026-08-22, "the browser gets carts, not their history") and
    its mirror -- a pushed journal line could only replay browser-era ops
    onto a board that has its own log. `skip_keep_journal` is the SITE-MODE
    variant, and it is a watcher argument rather than a second wire rule.

What deliberately does NOT sync, recorded so it is not read as a gap:
  * Top-level files beside the cart folders (system.json, wifi.json, the
    shared sheet) -- system state, not the kid's work, and wifi.json is a
    secret. The pull skips non-directories at the root for the same reason;
    `apply_ops` refuses single-segment file paths outright.
  * In the files root, anything whose first segment is not one of the
    user-files kinds (moy_store_base.FILE_KIND_NAMES) -- which is how `files/.history/` and `files/trash/`
    stay home, in BOTH directions, with no second skip list to keep in step.
    `.history` is the files layer's journal-equivalent (#111 op sidecars), so
    it stays for the same reason journal/ does: each side keeps its own undo
    history. `trash` is a LOCAL recovery bin, and syncing a deletion that is
    still recoverable here but not there turns last-writer-wins into data
    loss -- a peer would land the trashed copy back as a live file, or drop
    the only copy the kid could still restore.
  * Binary files but one -- the wire is JSON text, same rule as the pull.
    That includes a compiled cart's module (its `main.wasm`, and a board's
    compiled `<main>.<chip>.aot` beside it): a "runtime": "wasm" cart's
    manifest, assets and `src/` cross like any cart's and its module does not,
    so a compiled cart does NOT sync between a browser and a board. It plays
    where its module was put (docs/wasm_tier_plan_2026-09.md). The one binary
    file that DOES cross is a cart's cover, `cover.png` (SPEC.md 3.6,
    BINARY_FILES): as base64, `"b"` where text rides as `"t"`.

Wire shape (one POST per batch, bounded so it fits the transport's 64KB
request cap; the client sends ONE batch at a time and waits for the answer,
so ops apply in order):

    {"v": 1, "pin": "<optional>", "ops": [
      {"p": "cart.moy/main.py", "t": "<whole text>"},        # atomic write
      {"p": "cart.moy/big.lua", "t": "<piece>", "part": 0},  # chunked: begin
      {"p": "cart.moy/big.lua", "t": "<piece>", "part": 1},  #   ...append
      {"p": "cart.moy/big.lua", "pub": 1},                   #   ...publish
      {"p": "cart.moy/cover.png", "b": "<base64>"},          # a binary file
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

import time

try:
    import os
except ImportError:  # pragma: no cover
    os = None

import json as _json

try:
    from moy_fs import _crc32
except ImportError:  # host / CPython: the runtime package
    from runtime.moy_fs import _crc32

try:
    from moy_net import encode_batch, sync_batch, sync_apply
except ImportError:  # pragma: no cover - host package lane
    from runtime.net_binding import (encode_batch, sync_batch,
                                     sync_apply)
try:
    from moy_store_base import store_path
except ImportError:  # host / CPython: the runtime package
    from runtime.moy_store_base import store_path

try:
    import binascii as _binascii
except ImportError:  # pragma: no cover -- every target ships binascii
    import ubinascii as _binascii


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
# The webhost (MOY_HTTP_REQ_MAX, native/moy_net) cuts a request at 64KB as
# an OOM guard, and JSON escaping expands source text, so both stay well
# under it.
PART_MAX = 16 * 1024
BATCH_BUDGET = 32 * 1024

# The binary files that DO cross, by name: a cart's cover (SPEC.md 3.6), in
# base64 -- `"b"` in a batch op where text is `"t"`, `{"b": ...}` as a value in a
# pull bundle. A binary chunk is BINARY_PART bytes, which is PART_MAX characters
# of base64 exactly, so every piece decodes on its own. Every other binary file
# stays home (the module docstring says why). firmware/web_runner/moy_store.mjs
# carries the JS mirror, pinned by tests/test_web_store.py.
BINARY_FILES = ("cover.png",)
BINARY_PART = PART_MAX * 3 // 4


def is_binary(path):
    """True when the file at `path` (a name, a rel or a full path) crosses the
    wire as bytes."""
    return path[path.rfind("/") + 1:] in BINARY_FILES


def b64(data):
    """`data` as base64 text, no newline."""
    out = _binascii.b2a_base64(data)
    if not isinstance(out, str):
        out = out.decode("ascii")
    return out.strip()


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


# The predicate is the native store's wherever it is linked (moy_pack.c's
# moy_store_skip, which the archive takes too); this body is its twin.
try:
    from moy_store import skip as _native_skip
except ImportError:
    _native_skip = None


def _skip(name):
    if _native_skip is not None:
        return _native_skip(name, False)
    if name in SKIP_DIRS or name in SKIP_FILES:
        return True
    for suf in SKIP_SUFFIXES:
        if name.endswith(suf):
            return True
    return False


def skip_keep_journal(name):
    """`_skip` with journal/ + journal.jsonl allowed through -- the predicate a
    SITE-MODE watcher takes, and nothing else ever does.

    Never the wire's rule and never a receiver's: the receiving C
    (moy_sync_apply.c) still refuses a journal path outright, so a board cannot be handed one no matter which
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


def file_kinds():
    """The names a files-root path may start with: the user-files layer's
    kinds as the on-card layout names them (moy_store_base.FILE_KIND_NAMES,
    which tests/test_userfiles_parity.py holds to native/moy_store's registry),
    so a kind added there reaches the wire by default. The layout and not the
    layer, because the Zero serves and takes these folders without carrying
    the layer.

    This ONE allowlist is also what keeps `files/.history/` and `files/trash/`
    home in both directions: neither is a kind, so no walker descends into them
    and no op naming one is ever applied. Returns None when there is no store
    module to ask, which every caller reads as "refuse", never as "allow".
    """
    mc = _moy_carts()
    return None if mc is None else mc.FILE_KIND_NAMES


def files_root(carts_root):
    """The user-files root beside `carts_root` (the store's own sibling rule), or
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



class Root:
    """One syncable store, described by data. The fields are exactly the places
    the two stores used to differ:

      wire     the protocol version a batch for this store carries. Carts is 1
               (the shape every flashed board already parses); every other root
               is 2 + an explicit `root`, which is what makes an old board REFUSE
               it rather than misapply its paths.
      path     (carts_root) -> where this store lives. Carts IS carts_root; the
               files root is its sibling.
      kinds    the first path segment must be a user-files kind (`file_kinds`) --
               which is also what keeps `.history`/`trash` home, in both
               directions, with no second skip list.
      site_keep_journal
               in SITE mode (the browser's OPFS is of record) this root's watcher
               sweeps `journal/` too, so the kid's undo history survives the tab.
               Only carts relaxes; the files layer's history is `.history/`
               sidecars, a different mechanism that stays home either way.

    What a receiver does per root -- the carts root journals and dirties the
    shelf, a whole-cart delete is one segment, the files root is made on its
    first write and a files item is two or more -- is the apply's, in C
    (native/moy_net/moy_sync_apply.c).
    """

    def __init__(self, id, wire, path, endpoint, kinds=False,
                 site_keep_journal=False):
        self.id = id
        self.wire = wire
        self._path = path
        self.endpoint = endpoint
        self.kinds = kinds
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
    site_keep_journal=True)

FILES_ROOT = Root(
    # A lambda, not the bare `files_root` reference, so the name resolves at CALL
    # time: a host with no `moy_carts` (the headless XIAO cart store) answers None
    # here and the endpoint 404s, exactly as it should, per call rather than per
    # registry construction.
    FILES_ROOT_ID, PROTOCOL_V_ROOTED, lambda carts_root: files_root(carts_root),
    "/files.json", kinds=True)

SYNC_ROOTS = (CARTS_ROOT, FILES_ROOT)
ROOT_IDS = tuple(r.id for r in SYNC_ROOTS)
_ROOT_BY_ID = {r.id: r for r in SYNC_ROOTS}


def root_by_id(root_id):
    """The Root for a wire root id, or None."""
    return _ROOT_BY_ID.get(root_id)


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


def _read_payload(path):
    """What a watcher ships of the file at `path`: its text, its BYTES for a
    BINARY_FILES name, or None (skip it). Retried on OSError like `_read_text`."""
    if not is_binary(path):
        return _read_text(path)

    def _open():
        with open(path, "rb") as f:
            return f.read()
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
# The receiving half: native/moy_net's C (moy_sync.c's batch rule and
# moy_sync_apply.c), which the board's webhost runs with no Python between.
# ---------------------------------------------------------------------------


def parse_batch(body):
    """The POST body -> (ops, pin, root_id) or (None, None, None) on anything
    malformed. Which store a batch speaks for is the C's rule: a v1 batch is
    carts by definition (one that names another root is refused, never read as
    carts), a v2 batch names a v2 root, and anything else is refused -- a
    receiver never guesses where a path lands."""
    got = sync_batch(body)
    if got is None:
        return None, None, None
    root_id, pin, ops = got
    return _json.loads(ops), pin, root_id


def apply_ops(root, ops, root_id=CARTS_ROOT_ID, journal=False):
    """Apply one batch's `ops` into the store at path `root`, whose SHAPE is
    `root_id` ("carts" or "files", what parse_batch returned).

    Returns (applied, errors, shelf_dirty): how many ops landed, [(index,
    reason)] for the first eight that did not (a bad op skips, it never aborts
    the batch -- the client's retry would replay the same poison forever), and
    whether the SHELF needs a re-scan (a cart born or dying, a manifest, cover
    or cover sheet changed). A files batch never dirties the shelf.

    `journal=True` says THIS STORE IS OF RECORD: every carts-root text file the
    batch publishes is also a #111 commit, so on-glass UNDO walks back through
    an edit made in a browser. The cost is the journal's (a snapshot, a log
    line and a cursor per file), which is why a scratch receiver (the
    convergence harness) passes False.
    """
    kinds = file_kinds()
    applied, errors, shelf, _refused = sync_apply(
        root, root if root_id == FILES_ROOT_ID else None,
        None if kinds is None else tuple(kinds), root_id, _json.dumps(ops),
        journal)
    return applied, errors, shelf


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

    A file the wire cannot carry (a binary one other than a cover: a compiled
    cart's main.wasm, a data file, a WAD) is in the snapshot too, its crc None,
    so the stat walk passes it like any unchanged file: read once, it is not
    read again until it moves, and it never becomes an op, written or deleted.
    Left out, it was read whole on EVERY sweep, which is seconds of a frame
    per sweep for a cart that carries megabytes.
    """

    def __init__(self, root, listdir=None, isdir=None, read=None,
                 root_id=CARTS_ROOT_ID, skip=None):
        self.root = root
        self.root_id = root_id
        self.skip = skip or _skip    # what this watcher will not sweep
        self._listdir = listdir      # injected for host tests; None = real fs
        self._isdir = isdir
        self._read = read or _read_payload
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
            self._snap[rel] = (size, mtime, self._crc_of(rel))

    def _crc_of(self, rel):
        """The crc of `rel`'s payload, or None for one the wire cannot carry."""
        text = self._read(store_path(self.root, rel))
        return None if text is None else _crc(text)

    def adopt(self, unit):
        """Take the cart folder `unit` AS IS, with nothing pending for it: its
        files reached the store of record by another road (an install the
        browser's keeper committed whole, runtime/cart_index.py), so shipping
        them again would only rewrite what is there -- and the files the wire
        cannot carry would be missing from it."""
        prefix = unit + "/"
        for rel in list(self._pending):
            if rel.startswith(prefix):
                del self._pending[rel]
        if unit in self._pending_dc:
            self._pending_dc.remove(unit)
        for rel in list(self._snap):
            if rel.startswith(prefix):
                del self._snap[rel]
        for rel, size, mtime in self._walk_dir(store_path(self.root, unit), unit, 0):
            self._snap[rel] = (size, mtime, self._crc_of(rel))

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
            c = self._crc_of(rel)
            if c is None:                     # unreadable, or binary: not synced
                self._snap[rel] = (size, mtime, None)
                continue
            if old is not None and old[2] == c:
                self._snap[rel] = (size, mtime, c)
                continue                      # touched, not changed
            self._snap[rel] = (size, mtime, c)
            self._pending[rel] = "w"
        for rel in list(self._snap):
            if rel in seen:
                continue
            gone = self._snap.pop(rel)
            self._pending.pop(rel, None)
            if gone[2] is None:
                continue                      # never crossed: nothing to delete
            unit = self._unit(rel)
            if unit is not None and unit not in units:
                if unit not in self._pending_dc:
                    self._pending_dc.append(unit)
            else:
                self._pending[rel] = "d"
        # Files written in the newest observed second get re-read next sweep:
        # a second write inside that same second is invisible to stat. Not a
        # file the wire cannot carry: there is nothing such a write could ship.
        self._hot = tuple(rel for rel, v in self._snap.items()
                          if v[1] >= maxm - 1 and v[2] is not None)
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
            for item in self._walk_dir(store_path(self.root, top), top, 0):
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
            text = self._read(store_path(self.root, rel))
            if text is None:                     # vanished since the sweep
                ops.append({"p": rel, "d": 1})
                paths.append(rel)
                continue
            binary = not isinstance(text, str)
            if len(text) > (BINARY_PART if binary else PART_MAX):
                budget = self._emit_parts(ops, rel, text, 0, budget)
                if self._partial is not None:
                    del self._pending[rel]
                    self._inflight = (paths, dcs)
                    return ops
                paths.append(rel)
                continue
            if binary:
                text = b64(text)
            # A whole-file op (<=PART_MAX) subtracts AFTER it is appended, so
            # left unguarded it can push a batch past BATCH_BUDGET by a whole
            # file -- and a ~48KB batch, JSON-escaped, can breach the transport's
            # 64KB request cap, truncating the body to invalid JSON and wedging
            # the client on a permanent 400/requeue. Defer a file that would
            # overshoot to the next batch; a single op on an EMPTY batch always
            # ships (it is <=PART_MAX, comfortably under the cap).
            if ops and len(text) > budget:
                break
            ops.append({"p": rel, ("b" if binary else "t"): text})
            budget -= len(text)
            paths.append(rel)
        if not ops:
            return None
        for rel in paths:
            self._pending.pop(rel, None)
        self._inflight = (paths, dcs)
        return ops

    def _emit_parts(self, ops, rel, text, idx, budget):
        """Emit chunk ops for `text` (or a binary file's bytes, BINARY_PART at
        a time, each piece its own base64) from part `idx` until done or the
        budget runs out; sets/clears self._partial accordingly."""
        binary = not isinstance(text, str)
        step = BINARY_PART if binary else PART_MAX
        total = (len(text) + step - 1) // step
        while idx < total and budget > 0:
            piece = text[idx * step:(idx + 1) * step]
            if binary:
                piece = b64(piece)
                ops.append({"p": rel, "b": piece, "part": idx})
            else:
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
            return encode_batch(PROTOCOL_V, None, ops, pin)
        return encode_batch(root.wire, root.id, ops, pin)

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
    if not isinstance(text, str):
        return _crc32(text) & 0xFFFFFFFF           # a BINARY_FILES file's bytes
    return _crc32(text.encode("utf-8")) & 0xFFFFFFFF
