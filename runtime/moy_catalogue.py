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
  seed_any(seed, root, progress)   seed the built-in roster (packed or listed)
  embedded_floor(seed)             the read-only built-ins, for a board with no
                                   writable store: carts with no path and no "h"
  catalogue(root) -> [entry]       the shelf: one entry per cart folder, in
                                   folder order, each with its "h"; the index
                                   then holds exactly these rows (see below)
  entry(h) -> entry | None         one cart's entry, read again
  load(h) -> cart | None           one cart whole: its entry and its payloads
  path(h) -> str                   the cart's folder
  handle(path) -> h                the row for a cart folder, made if absent
  valid(h) -> bool                 whether h names a live row
  create(title, root, **fields)    a new cart folder -> its cart, with "h"
  new(root, title)                 a new cart from the template -> its cart
  duplicate(h, root, new_title)    a copy of the cart on disk -> its cart
  delete(h)                        remove the cart's folder; h goes stale

A call that takes a handle raises StaleHandle before it touches the card. A
store that cannot be written raises OSError, ENOSPC when it is full (an index
with no free row is full too); a cart folder that will not read is None, as it
always was, so one bad folder never takes the shelf down.

`catalogue` is the index's reconcile. A folder the index already names keeps
its handle -- an open cart survives a rescan -- a new folder takes a row, and
every row the scan did not find is released, so a cart deleted behind the
store's back (the sync wire, a card swap) goes stale at the next scan. The
index holds one store at a time: a catalogue of another root releases the
rows of the last one. A root that will not list changes nothing and reads as
an empty shelf.
"""

try:
    import moy_carts
    from moy_index import Index, StaleHandle  # noqa: F401 (re-exported)
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime import moy_carts
    from runtime.moy_index import Index, StaleHandle  # noqa: F401

CARTS_DIR = moy_carts.CARTS_DIR
ensure_dirs = moy_carts.ensure_dirs
seed_any = moy_carts.seed_any
embedded_floor = moy_carts.embedded_floor

_index = Index()


def _stamp(cart):
    if cart:
        cart["h"] = _index.intern(cart["path"])
    return cart


def catalogue(root=CARTS_DIR):
    found = {}

    def _read(path):
        e = moy_carts.entry(path)
        if e:
            e["h"] = found[path] = _index.intern(path)
        return e

    items = moy_carts._each(root, _read)
    if items is None:
        return []
    for h in _index.handles():
        if _index.path(h) not in found:
            _index.release(h)
    return items


def entry(h):
    e = moy_carts.entry(_index.path(h))
    if e:
        e["h"] = h
    return e


def load(h):
    c = moy_carts.load(_index.path(h))
    if c:
        c["h"] = h
    return c


def path(h):
    return _index.path(h)


def handle(path):
    return _index.intern(path)


def valid(h):
    return _index.valid(h)


def create(title, root=CARTS_DIR, **fields):
    return _stamp(moy_carts.create(title, root, **fields))


def new(root=CARTS_DIR, title="New Cart"):
    return _stamp(moy_carts.new_from_template(root, title))


def duplicate(h, root=CARTS_DIR, new_title=None):
    src = load(h)
    if src is None:
        return None
    return _stamp(moy_carts.duplicate(src, root, new_title))


def delete(h):
    p = _index.path(h)
    moy_carts.delete({"path": p})
    _index.release(h)
