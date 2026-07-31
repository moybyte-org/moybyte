"""Surface model v1 -- retained WM surfaces with explicit generation dirty.

The contract is docs/surface_model_v1.md; this module is its §2 object. It is a
LEAF: imported by wm_windowed.py and the web serve glue ONLY -- never by wm.py
or console.py, which the S3 build freezes verbatim and executes unchanged (L6).
The S3 build.sh denylists this file so the degenerate tier doesn't even carry
the bytes.

Three rules are load-bearing (§2, learned at review):

* Gens come from ONE monotonic per-WM mint, never per-object counters. Windows
  are destroyed wholesale (`on_relayout` clears `_wins`; every world flip
  rebuilds) while per-client caches (`SurfaceDelta._last`) persist -- a reborn
  surface counting from 0 could alias a client's stored gen into a wrong
  `{"same":1}` replay of a dead window. A monotonic mint makes recreation safe
  for free: fresh surface, fresh higher gen. Compare with `!=`, never `>`.

* sids are minted from WM REGISTRY keys ("win:make"), never content kinds
  ("picker") -- the shipped `key == win.kind` comparison silently never matched
  the shared make group and disabled its drag freeze everywhere (#113).

* The un-attributed dirty epoch is SET-LEVEL: `epoch()` bumps one gen that
  `content_gen()` folds into every surface (known or not), so a `ws._dirty`
  write nobody attributed means "everything changed" -- safe, never wrong,
  merely unprofitable. Per-surface attribution (`touch`) is opt-in per audited
  site (§3 Class A); `animating` is the §3 Class B declaration.
"""


class Surface:
    """One WM surface: identity + local content size + placement + gens.
    Placement is WM-owned (L1); content code never reads or writes it."""

    __slots__ = ("sid", "domain", "w", "h", "x", "y", "scale", "z",
                 "_content_gen", "place_gen", "animating")

    def __init__(self, sid, domain, gen):
        self.sid = sid
        self.domain = domain
        self.w = 0
        self.h = 0
        self.x = 0
        self.y = 0
        self.scale = 1
        self.z = 0
        self._content_gen = gen
        self.place_gen = gen
        self.animating = False

    def place(self):
        """The wire-shape placement (§6): [x, y, scale, z]."""
        return [self.x, self.y, self.scale, self.z]


class SurfaceSet:
    """The WM's surface registry + the one monotonic gen mint (§2/L3)."""

    def __init__(self):
        self._gen = 1          # never 0: a client's "no last-seen" is 0
        self._epoch = 1        # the un-attributed Class A signal (set-level)
        self.surfaces = {}     # sid -> Surface

    def mint(self):
        self._gen += 1
        return self._gen

    def get(self, sid, domain="system"):
        """The surface record for sid, created on demand with FRESH gens (a
        reborn sid must read as changed to every consumer -- §2)."""
        s = self.surfaces.get(sid)
        if s is None:
            s = self.surfaces[sid] = Surface(sid, domain, self.mint())
        return s

    def drop(self, sid):
        self.surfaces.pop(sid, None)

    def sync(self, alive, prefix="win:"):
        """Drop every `prefix`-sid not in `alive` (mirrors the WM's window-slot
        sync; a survivor keeps its gens, a returner is reborn with fresh ones).
        Non-prefixed records (layer-id surfaces: desk / bar / cursor / chips)
        are never dropped here -- they live as long as the WM."""
        for sid in list(self.surfaces):
            if sid.startswith(prefix) and sid not in alive:
                del self.surfaces[sid]

    # -- the three §3 signals ------------------------------------------------
    def touch(self, sid, domain="system"):
        """Class A, attributed: this surface's content changed."""
        self.get(sid, domain)._content_gen = self.mint()

    def move(self, sid, domain="system"):
        """Placement changed (drag/resize/restack/show/hide). L1: never
        invalidates content."""
        self.get(sid, domain).place_gen = self.mint()

    def epoch(self):
        """Class A, un-attributed: something changed and nobody said what.
        Every surface -- including sids with no record -- reads as changed."""
        self._epoch = self.mint()

    # -- consumer reads ------------------------------------------------------
    def content_gen(self, sid):
        """The effective content gen a consumer compares (!=): the surface's
        own gen folded with the set epoch. Unknown sids ride the epoch alone,
        so un-attributed changes cover them too."""
        s = self.surfaces.get(sid)
        g = s._content_gen if s is not None else 0
        return g if g > self._epoch else self._epoch

    def is_animating(self, sid):
        s = self.surfaces.get(sid)
        return s.animating if s is not None else False
