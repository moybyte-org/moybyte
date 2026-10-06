"""The store's interface: carts by handle.

What the native store exposes (docs/native_kernel_2026-09.md, sprint 1b), call
for call, written over the Python store: every cart the shelf, the boot and the
roles reach as a CART goes through here, and a cart is named by a handle from
the store's index (runtime/moy_index.py) -- never by an object the store keeps.
A cart dict this module returns is the caller's, built for the call; it carries
its handle as "h" beside its "path".

A path still names a FILE: a cart's scripts and assets are read and written by
path (moy_carts' save_* and load_* verbs, the journal, the sync wire), because
none of that leaves state in the store between calls. The index is the one
thing the store holds across them, so its rows are what take handles.

  CARTS_DIR                        the store's default root
  StaleHandle                      raised by every call below given a handle
                                   whose row is gone (a ValueError)
  ensure_dirs(root)                make the root and its parent
  sweep_store(root)                the once-per-store sweep of retired seeds,
                                   which a boot runs before its catalogue
  seed(seed, root, shelf, progress) -> [entry]
                                   seed the built-in roster (packed or listed)
                                   against `shelf`, the root's catalogue: the
                                   entries' versions say what to write, and
                                   the shelf comes back with each folder the
                                   seed wrote read again
  embedded_floor(seed)             the read-only built-ins, for a board with no
                                   writable store: carts with no path and no "h"
  catalogue(root) -> [entry]       the shelf: one entry per cart folder, in
                                   folder order, each with its "h"; the index
                                   then holds exactly these rows of the root
                                   (see below)
  entry(h) -> entry | None         one cart's entry, read again
  load(h) -> cart | None           one cart whole: its entry and its payloads
  path(h) -> str                   the cart's folder, composed from its root
                                   and its folder name (moy_store_base)
  handle(path) -> h                the row for a cart folder, made if absent,
                                   its path split against the roots
  valid(h) -> bool                 whether h names a live row
  rows() -> int                    how many rows the index holds, every root's
  create(title, root, **fields)    a new cart folder -> its cart, with "h"
  new(root, title, ns)             a new cart from the template -> its cart
  duplicate(h, root, new_title, ns)
                                   a copy of the cart on disk -> its cart
                                   (a made cart's folder is `<ns>.<name>.moy`,
                                   #162; `ns` is the Settings author)
  delete(h)                        remove the cart's folder; h goes stale

The cart's undo journal (runtime/moy_journal.py), by handle:

  journal_append(h, file, data, grad=None, ops=None) -> seq | None
  journal_undo(h, files=None) / journal_redo(h, files=None) -> file | None
  journal_can_undo(h, files=None) / journal_can_redo(h, files=None) -> bool
  journal_compact(h)
  journal_list(h, file=None) -> [entry]    #136: a file's timeline, oldest first
  journal_snap(h, seq) -> text | None      one entry's snapshot
  journal_restore(h, seq) -> seq | None    an old snapshot as a new commit

A call that takes a handle raises StaleHandle before it touches the card. A
store that cannot be written raises OSError, ENOSPC when it is full (an index
with no free row is full too); a cart folder that will not read is None, as it
always was, so one bad folder never takes the shelf down.

A row's key is its root's id and its folder name (docs/kernel_store_2026-10.md
section 4), never a path: the index's ABI compares keys bytewise and is the
same for both. Roots are the index's table of ROOTS, a store's id its slot
plus one, taken the first time a call names the store; when every slot is
taken, the root named longest ago gives its slot up and every row it held is
released.

`catalogue` is the index's reconcile, one root at a time. A folder the index
already names keeps its handle -- an open cart survives a rescan -- a new
folder takes a row, and every row of that root the scan did not find is
released, so a cart deleted behind the store's back (the sync wire, a card
swap) goes stale at the next scan; the rows of other roots are left as they
are. A root that will not list changes nothing and reads as an empty shelf.
"""

try:
    import moy_carts
    import moy_journal
    from moy_index import Index, ROOTS, StaleHandle  # noqa: F401 (re-exported)
    from moy_store_base import cart_path, USER_NS, USER_NS_KEY  # noqa: F401
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime import moy_carts
    from runtime import moy_journal
    from runtime.moy_index import Index, ROOTS, StaleHandle  # noqa: F401
    from runtime.moy_store_base import cart_path, USER_NS, USER_NS_KEY  # noqa: F401

CARTS_DIR = moy_carts.CARTS_DIR
ensure_dirs = moy_carts.ensure_dirs
sweep_store = moy_carts.sweep_store
embedded_floor = moy_carts.embedded_floor

_index = Index()


def _rid(root):
    """The id of `root` in the index's root table (`moy_index_root`), taken if
    absent: a free slot, else the slot of the root named longest ago, whose
    rows go first."""
    return _index.root(root)


def _key(rid, folder):
    return chr(rid) + folder


def _intern(root, path):
    """The row for the cart folder at `path` in `root`."""
    return _index.intern(_key(_rid(root), path[len(root) + 1:]))


def _stamp(cart, root):
    if cart:
        cart["h"] = _intern(root, cart["path"])
    return cart


def catalogue(root=CARTS_DIR):
    items = moy_carts.entries(root)
    if items is None:
        return []
    rid = _rid(root)
    found = set()
    for e in items:
        e["h"] = h = _index.intern(_key(rid, e["path"][len(root) + 1:]))
        found.add(h)
    for h in _index.rows(rid):
        if h not in found:
            _index.release(h)
    return items


def seed(seed, root, shelf, progress=None):
    present = {}
    for e in shelf:
        present[e["path"][len(root) + 1:]] = e["version"]
    wrote = moy_carts.seed_any(seed, root, present, progress)
    if not wrote:
        return shelf
    known = set(f.lower() for f in present)
    for name in wrote:
        if name not in present and name.lower() in known:
            return catalogue(root)    # a folder FAT matched by another case
    rid = _rid(root)
    byname = {}
    for e in shelf:
        byname[e["path"][len(root) + 1:]] = e
    for name in sorted(wrote):
        e = moy_carts.entry(cart_path(root, name))
        if e:
            e["h"] = _index.intern(_key(rid, name))
            byname[name] = e
        elif name in byname:
            del byname[name]
            h = _index.find(_key(rid, name))
            if h:
                _index.release(h)
    return [byname[name] for name in sorted(byname)]


def entry(h):
    e = moy_carts.entry(path(h))
    if e:
        e["h"] = h
    return e


def load(h):
    c = moy_carts.load(path(h))
    if c:
        c["h"] = h
    return c


def path(h):
    key = _index.path(h)
    return cart_path(_index.root_path(ord(key[0])), key[1:])


def handle(path):
    cut = path.rfind("/")
    root = path[:cut]
    for rid in range(1, ROOTS + 1):
        r = _index.root_path(rid)
        if r is not None and path.startswith(r + "/") and "/" not in path[len(r) + 1:]:
            root = r
            break
    return _intern(root, path)


def valid(h):
    return _index.valid(h)


def rows():
    return _index.count()


def create(title, root=CARTS_DIR, **fields):
    return _stamp(moy_carts.create(title, root, **fields), root)


def new(root=CARTS_DIR, title="New Cart", ns=USER_NS):
    return _stamp(moy_carts.new_from_template(root, title, ns), root)


def duplicate(h, root=CARTS_DIR, new_title=None, ns=USER_NS):
    src = load(h)
    if src is None:
        return None
    return _stamp(moy_carts.duplicate(src, root, new_title, ns), root)


def delete(h):
    moy_carts.delete({"path": path(h)})
    _index.release(h)


def journal_append(h, file, data, grad=None, ops=None):
    p = path(h)
    return moy_journal.journal_append(p, file, data, grad=grad, ops=ops)


def journal_undo(h, files=None):
    p = path(h)
    return moy_journal.journal_undo(p, files)


def journal_redo(h, files=None):
    p = path(h)
    return moy_journal.journal_redo(p, files)


def journal_can_undo(h, files=None):
    p = path(h)
    return moy_journal.journal_can_undo(p, files)


def journal_can_redo(h, files=None):
    p = path(h)
    return moy_journal.journal_can_redo(p, files)


def journal_compact(h):
    p = path(h)
    return moy_journal.journal_compact(p)


def journal_list(h, file=None):
    p = path(h)
    return moy_journal.journal_list(p, file)


def journal_snap(h, seq):
    p = path(h)
    return moy_journal.journal_snap(p, seq)


def journal_restore(h, seq):
    p = path(h)
    return moy_journal.journal_restore(p, seq)
