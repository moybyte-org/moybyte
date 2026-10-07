"""The glass as the kernel's native `moy_glass` will expose it: its Python twin
(docs/kernel_survival_2026-10.md section 3.1).

  Glass            the tables: OWNER, BUF, CANVAS and SURF rows over
                   moy_spine.Table, the layer pool, and the loans
  present          the compositor's present of a finished frame
  fence            wait until nothing the compositor started is in flight
  present_pending  finish a deferred present before the canvases re-point

An OWNER is a lifetime that holds loans -- a cart run, the wallpaper, the map
cache, the Paint document -- named by a tag and minted on its first loan. A
BUF row is one off-heap buffer on loan to an owner: the buffer, its size, its
ROLE (a layer, a bake), its ORIGIN (where it came from, so giving it back
knows whether it returns to the pool or is freed) and its HOLDER (the canvas
that minted a layer, the image a bake belongs to). The POOL is keyed by byte
size: a layer buffer a dead owner gave back waits there for the next layer of
its size. A full BUF table is a MemoryError, never a silent fallback.

device/device_canvas.py holds one Glass for the canvases of its module; the
compositors answer the three verbs.
"""

try:
    from moy_spine import KIND_BUF, KIND_CANVAS, KIND_OWNER, KIND_SURF, Table
except ImportError:                     # host: the runtime package
    from runtime.moy_spine import (KIND_BUF, KIND_CANVAS, KIND_OWNER,
                                   KIND_SURF, Table)

ROLE_LAYER, ROLE_BAKE, ROLE_SCRATCH, ROLE_CACHE, ROLE_PAINT = 1, 2, 3, 4, 5

# Where a buffer came from: the gc heap, the layer pool, moy_alloc's freeing
# allocator, or its malloc_dma lane, which cannot free.
ORIGIN_HEAP, ORIGIN_POOL, ORIGIN_ALLOC, ORIGIN_DMA = 0, 1, 2, 3

# A BUF row: [buf, nbytes, role, origin, owner handle, holder].
BUF, NBYTES, ROLE, ORIGIN, OWNER, HOLDER = 0, 1, 2, 3, 4, 5


class Glass:
    """The glass's tables. One per canvas module."""

    def __init__(self):
        self.owners = Table(KIND_OWNER, "owner")
        self.bufs = Table(KIND_BUF, "buf")
        self.canvases = Table(KIND_CANVAS, "canvas")
        self.surfs = Table(KIND_SURF, "surf")
        self.pool = {}          # nbytes -> [buf]
        self._tags = {}         # owner tag -> its OWNER handle
        self._loans = {}        # owner tag -> [BUF handle], in lending order

    # -- owners --------------------------------------------------------------

    def owner(self, tag):
        """The OWNER row for `tag`, minted on first use."""
        h = self._tags.get(tag)
        if h is None:
            h = self._tags[tag] = self.owners.new(tag)
        return h

    def owner_end(self, tag):
        """End `tag`'s lifetime: its row goes, once it holds no loan."""
        if self._loans.get(tag):
            raise ValueError("owner %r still holds loans" % (tag,))
        self._loans.pop(tag, None)
        h = self._tags.pop(tag, None)
        if h is not None:
            self.owners.release(h)

    # -- loans ---------------------------------------------------------------

    def lend(self, buf, nbytes, role, origin, tag, holder=None):
        """Record `buf` on loan to `tag`; returns its BUF handle."""
        try:
            h = self.bufs.new([buf, nbytes, role, origin, self.owner(tag),
                               holder])
        except OSError:
            raise MemoryError("the glass's buffer table is full")
        lst = self._loans.get(tag)
        if lst is None:
            lst = self._loans[tag] = []
        lst.append(h)
        return h

    def row(self, h):
        return self.bufs.get(h)

    def loans(self, tag, role, holder=None):
        """`tag`'s BUF handles of `role` (held by `holder`, when given), in
        lending order."""
        out = []
        for h in self._loans.get(tag, ()):
            r = self.bufs.get(h)
            if r[ROLE] == role and (holder is None or r[HOLDER] is holder):
                out.append(h)
        return out

    def held(self, tag, role):
        """[(holder, buf)] of `tag`'s loans in `role`, in lending order."""
        return [(self.bufs.get(h)[HOLDER], self.bufs.get(h)[BUF])
                for h in self.loans(tag, role)]

    def give_back(self, h):
        """End one loan; returns its row."""
        r = self.bufs.release(h)
        tag = self.owners.get(r[OWNER])
        lst = self._loans.get(tag)
        if lst is not None:
            lst.remove(h)
            if not lst:
                del self._loans[tag]
        return r

    def lent(self):
        """{owner tag: [buf]} of every loan, for the census's walk: the rows
        are a native table's on a board, which a walk cannot see into."""
        return {tag: [self.bufs.get(h)[BUF] for h in lst]
                for tag, lst in self._loans.items()}

    def lent_bytes(self, role):
        """{owner tag: bytes on loan in `role`}, for the census."""
        out = {}
        for tag, lst in self._loans.items():
            n = 0
            for h in lst:
                r = self.bufs.get(h)
                if r[ROLE] == role:
                    n += len(r[BUF])
            if n:
                out[tag] = n
        return out

    # -- the pool ------------------------------------------------------------

    def pool_take(self, nbytes):
        """A pooled buffer of exactly `nbytes`, or None."""
        free = self.pool.get(nbytes)
        return free.pop() if free else None

    def pool_put(self, nbytes, buf):
        self.pool.setdefault(nbytes, []).append(buf)


# -- the three verbs the compositors answer -----------------------------------

def present(comp):
    """Show the finished frame (the compositor's flush)."""
    comp.flush()


def fence(comp):
    """Wait until nothing the compositor started is in flight; a compositor
    with nothing asynchronous has no fence."""
    f = getattr(comp, "sync", None)
    if f is not None:
        f()


def present_pending(comp):
    """Finish a present the last frame deferred (the P4's async composite) --
    before the canvases re-point at the buffer it frees."""
    comp.present_pending()
