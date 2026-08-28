"""`LayoutBase` -- the frozen-baseline geometry contract, in ONE body.

Every responsive `*Layout` class in the shell (#39) answers the same question
first: is this the frozen 320x240 / font-scale-1 baseline, or a canvas that has
to reflow? `self._base` IS that answer, and the whole degradation contract
rests on it -- at (320, 240, 1) a layout reproduces the pre-#39 constants
VERBATIM rather than re-deriving them, so no reflow formula's integer floor can
drift a T-Deck pixel. Eight classes hand-copied the same four-line `__init__`
head and the same predicate; this is that head, and nothing else.

What the base owns: `w`, `h`, `fs` (clamped to >= 1) and `_base`. What it
deliberately does NOT own is everything the subclasses diverge on immediately
after -- the per-class `font_w`/`cell`/`lh` cell metrics, and the panel-rect
block that map/paint/scene share (`px, py = 8 * fs ...`). Those stay per class:
this base is the predicate's home, not a geometry library.

`base_extra` is how a subclass ANDs one more term into the predicate without
re-declaring it. `SceneLayout` and `BlockLayout` pass `bounds is None`: a
layout confined to a sub-rect of the system canvas is a big-screen feature and
must never take the frozen branch.

**This module is a LEAF and has to stay one.** It imports nothing from the
runtime tree, because the obvious home -- beside `chrome.Layout` -- is not
importable by the classes that need it: `chrome.py` imports its geometry
constants FROM `bar_layer`/`settings_layer`/`code_layer`, so a base living
there is a cycle for exactly the editor surfaces that subclass it
(`docs/history/console_architecture_2026-08.md` §6).

The APP tier is a declared second vocabulary and is not here:
`app_shell.ListShellLayout` and the calc/appearance/artwork heads speak
`(w, h, fs, windowed=)` with no `_base` branch at all.
"""

# The baseline the responsive layouts reproduce EXACTLY (#39 graceful
# degradation) -- the T-Deck's system canvas, and every layout's default size.
BASE_W = 320
BASE_H = 240


class LayoutBase:
    """The common `__init__` head + the `_base` predicate. Constructed at init
    and relayout only, never per frame."""

    def __init__(self, w=BASE_W, h=BASE_H, font_scale=1, base_extra=True):
        self.w = int(w)
        self.h = int(h)
        self.fs = max(1, int(font_scale))
        self._base = (self.w == BASE_W and self.h == BASE_H and self.fs == 1
                      and base_extra)
