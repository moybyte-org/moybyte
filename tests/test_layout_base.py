"""`LayoutBase` is the ONE home of the frozen-baseline predicate (#209 W2).

Eight responsive `*Layout` classes hand-copied the same four-line `__init__`
head and the same `self._base = (self.w == _BASE_W and self.h == _BASE_H and
fs == 1)` test -- `chrome.Layout`, `chrome.CodeLayout` and the six editor
layouts in their own files (cards/paint/map/music/scene/block). `_base` is what
decides whether a layout reproduces the pre-#39 constants VERBATIM or runs the
reflow formulas, so eight copies of it is eight chances for the T-Deck's
byte-identical guarantee to become a discipline rather than a structure
(`docs/history/console_architecture_2026-08.md` §6).

Two halves, deliberately:

  * BEHAVIOUR -- every class's `_base` still equals the expression it replaced,
    over a probe matrix that straddles the baseline on each axis (and, for the
    two classes that extend the predicate with `bounds is None`, on that one
    too). A structural check alone would pass against a base whose predicate
    had drifted.
  * ANTI-REFORK -- no other shared module re-declares `_BASE_W = 320` or writes
    its own `self._base = (...)`. A behavioural check alone would pass against
    a ninth class that quietly grew its own copy, which is exactly how the
    eight happened.

The app tier is a DECLARED second vocabulary and is not covered here:
`app_shell.ListShellLayout` and the calc/appearance/artwork heads speak
`(w, h, fs, windowed=)` with no `_base` branch at all (§6).
"""

import ast
import os
import re

import pytest

from runtime import layout_base
from runtime.block_editor_ui import BlockLayout
from runtime.cards_layer import CardsLayout
from runtime.chrome import CodeLayout, Layout
from runtime.map_editor_ui import MapLayout
from runtime.music_editor_ui import MusicLayout
from runtime.paint_layer import PaintLayout
from runtime.scene_editor_ui import SceneLayout

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASE_REL = os.path.join("runtime", "layout_base.py")

# (class, takes a `bounds` argument that extends the predicate)
LAYOUTS = [
    (Layout, False),
    (CodeLayout, False),
    (CardsLayout, False),
    (PaintLayout, False),
    (MapLayout, False),
    (MusicLayout, False),
    (SceneLayout, True),
    (BlockLayout, True),
]

# Straddles the baseline on each axis on its own -- 320x240/1 (the one True
# case), one pixel off in each dimension, the bigger tiers the goldens render
# (480x320 Guition, 1024x600 windowed), and font scales 2/3. The non-positive
# scales pin the `max(1, ...)` clamp: fs=0 must READ as 1, not fall through and
# make some other size the baseline.
MATRIX = [
    (320, 240, 1), (320, 240, 2), (320, 240, 3),
    (321, 240, 1), (319, 240, 1), (320, 241, 1), (320, 239, 1),
    (480, 320, 1), (480, 320, 2), (1024, 600, 1), (1024, 600, 3),
    (640, 480, 1), (240, 320, 1),
    (320, 240, 0), (320, 240, -3),
]

BOUNDS = [None, (0, 0, 320, 240), (10, 10, 500, 400)]


def _read(rel):
    with open(os.path.join(ROOT, rel), "r", encoding="utf-8") as f:
        return f.read()


def _expected(w, h, font_scale, bounds=None):
    """The predicate as the eight classes each spelled it, before the base."""
    w, h = int(w), int(h)
    fs = max(1, int(font_scale))
    return (w == 320 and h == 240 and fs == 1 and bounds is None)


@pytest.mark.parametrize("cls,has_bounds", LAYOUTS,
                         ids=[c.__name__ for c, _ in LAYOUTS])
def test_every_layout_subclasses_the_base(cls, has_bounds):
    assert issubclass(cls, layout_base.LayoutBase)


@pytest.mark.parametrize("cls,has_bounds", LAYOUTS,
                         ids=[c.__name__ for c, _ in LAYOUTS])
def test_the_base_predicate_is_what_it_replaced(cls, has_bounds):
    for (w, h, fs) in MATRIX:
        for bounds in (BOUNDS if has_bounds else [None]):
            lay = cls(w, h, fs, bounds) if has_bounds else cls(w, h, fs)
            want = _expected(w, h, fs, bounds)
            assert lay._base is want, (cls.__name__, w, h, fs, bounds)
            # The head the base took over, too: a subclass that stopped
            # delegating could still pass the predicate check by luck.
            assert (lay.w, lay.h, lay.fs) == (int(w), int(h), max(1, int(fs)))


def test_bounds_only_ever_narrows_the_baseline():
    """The extension is an AND term: it can turn `_base` off, never on."""
    for cls in (SceneLayout, BlockLayout):
        assert cls(320, 240, 1, None)._base is True
        assert cls(320, 240, 1, (0, 0, 320, 240))._base is False
        # ... and no `bounds` can rescue a non-baseline size.
        assert cls(1024, 600, 1, None)._base is False


def test_the_defaults_are_the_baseline():
    for cls, _ in LAYOUTS:
        assert cls()._base is True
        assert (cls().w, cls().h, cls().fs) == (layout_base.BASE_W,
                                                layout_base.BASE_H, 1)


def test_the_base_is_a_leaf():
    """`layout_base` imports NOTHING at all.

    That is the whole reason it is not in `chrome.py`: chrome imports its
    geometry constants FROM bar_layer/settings_layer/code_layer, so a base
    living beside `chrome.Layout` is a cycle for exactly the editor surfaces
    that subclass it. An import added here re-opens that cycle.
    """
    tree = ast.parse(_read(BASE_REL))
    found = [n for n in ast.walk(tree)
             if isinstance(n, (ast.Import, ast.ImportFrom))]
    assert not found, ("layout_base grew %d import(s) and is no longer a leaf"
                       % len(found))


def _shared_sources():
    """Every authored shared/device module -- not the gitignored staged copies
    under firmware/*/modules/, which are rebuilt from these."""
    for tier in ("runtime", "device"):
        d = os.path.join(ROOT, tier)
        for name in sorted(os.listdir(d)):
            rel = os.path.join(tier, name)
            if name.endswith(".py") and rel != BASE_REL:
                yield rel, _read(rel)


_DECL = re.compile(r"^_?BASE_[WH]\s*=\s*\d+", re.M)
_BASELINE_NAMES = {"BASE_W", "BASE_H", "_BASE_W", "_BASE_H"}


def test_nothing_else_declares_the_baseline_constants():
    for rel, src in _shared_sources():
        assert not _DECL.search(src), (
            "%s re-declares the 320x240 baseline; import BASE_W/BASE_H from "
            "runtime/layout_base.py instead" % rel)


def _geometry_base_assignments(src):
    """`self._base = <expr mentioning BASE_W/BASE_H>` -- the geometry predicate.

    Matched on the right-hand side rather than the attribute name, because
    `_base` is not reserved: `op_history.History._base` is the undo keyframe
    snapshot and has nothing to do with screen size.
    """
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        targets = [t for t in node.targets
                   if isinstance(t, ast.Attribute) and t.attr == "_base"]
        if not targets:
            continue
        names = {n.id for n in ast.walk(node.value) if isinstance(n, ast.Name)}
        if names & _BASELINE_NAMES:
            yield node.lineno


def test_nothing_else_writes_its_own_base_predicate():
    for rel, src in _shared_sources():
        lines = list(_geometry_base_assignments(src))
        assert not lines, (
            "%s builds its own baseline predicate at line(s) %s; subclass "
            "LayoutBase (and pass base_extra= for an extra term) instead"
            % (rel, lines))
