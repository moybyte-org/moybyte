"""The store's index: one row per cart folder, named by a handle.

A handle is an int, never 0: the row's slot in the low SLOT_BITS and the
slot's generation above them. Every call that takes one checks it against the
table, so a handle whose row was released -- or released and taken again by
another folder -- raises StaleHandle instead of naming the wrong cart
(docs/native_kernel_2026-09.md section 4.3: handles, not objects). A row holds
a key -- the catalogue's root id and folder name (runtime/moy_catalogue.py),
which this table compares as it would any string -- and nothing a runtime
owns; the generation is what tells two tenants of one slot apart. The calls
below name the key `path`, as the native ABI does.

The layout keeps every handle below 2**30, a small int on every VM the console
runs (a board's 32-bit build boxes anything larger on the heap), and leaves
4096 rows: a slot's generation counts 1 .. GEN_MAX and wraps to 1, so a handle
held across 262143 reuses of its slot is the one a check cannot catch.

The interface, which the native index exposes call for call:

  Index()                 an empty table
  intern(path) -> h       the row for `path`, made in the lowest free slot
                          when absent; OSError(ENOSPC) when all SLOTS are taken
  find(path) -> h         the row's handle, or 0 when `path` has none
  path(h) -> str          the row's path
  valid(h) -> bool        whether `h` names a live row (never raises)
  release(h)              the row goes; `h`, and every copy of it, is stale
  handles() -> [h]        every live row, in slot order
  count() -> int          how many rows are live

A handle that is not an int raises TypeError; an int that names no live row
raises StaleHandle (a ValueError). No call does I/O: what a folder holds is the
catalogue's business (runtime/moy_catalogue.py), and this table only names it.
"""

SLOT_BITS = 12
SLOTS = 1 << SLOT_BITS
GEN_MAX = (1 << 18) - 1

_SLOT_MASK = SLOTS - 1
_ENOSPC = 28


class StaleHandle(ValueError):
    """A handle that names no live row: released, reused, or never made."""


class Index:
    """The table. Rows live in parallel lists indexed by slot: `_path[s]` is
    the folder (None while the slot is free) and `_gen[s]` the slot's current
    generation, which moves on when the row is released."""

    def __init__(self):
        self._path = []
        self._gen = []
        self._slot = {}           # path -> slot, for the live rows
        self._free = 0            # free slots below len(_path)

    def _check(self, h):
        """The slot `h` names, or StaleHandle."""
        s = h & _SLOT_MASK
        if (h <= 0 or s >= len(self._path) or self._path[s] is None
                or self._gen[s] != h >> SLOT_BITS):
            raise StaleHandle("stale store handle %d" % h)
        return s

    def intern(self, path):
        s = self._slot.get(path)
        if s is None:
            if self._free:
                s = self._path.index(None)
                self._free -= 1
            elif len(self._path) < SLOTS:
                s = len(self._path)
                self._path.append(None)
                self._gen.append(1)
            else:
                raise OSError(_ENOSPC, "store index full")
            self._path[s] = path
            self._slot[path] = s
        return (self._gen[s] << SLOT_BITS) | s

    def find(self, path):
        s = self._slot.get(path)
        if s is None:
            return 0
        return (self._gen[s] << SLOT_BITS) | s

    def path(self, h):
        return self._path[self._check(h)]

    def valid(self, h):
        try:
            self._check(h)
        except (StaleHandle, TypeError):
            return False
        return True

    def release(self, h):
        s = self._check(h)
        del self._slot[self._path[s]]
        self._path[s] = None
        g = self._gen[s] + 1
        self._gen[s] = 1 if g > GEN_MAX else g
        self._free += 1

    def handles(self):
        return [(self._gen[s] << SLOT_BITS) | s
                for s in range(len(self._path)) if self._path[s] is not None]

    def count(self):
        return len(self._slot)
