"""`LayoutBase` is the ONE common head of the responsive `*Layout` classes
(#209 W2): `w`, `h`, `fs` (clamped to >= 1) and the chrome scale `cs`.

Eight classes -- `chrome.Layout`, `chrome.CodeLayout` and the six editor
layouts (cards/paint/map/music/scene/block) -- subclass it. Each lays out
every canvas, the T-Deck's 320x240 included, through the same formulas: the
frozen 320x240 branch they once kept (`_base`) retired with theming phase 3
step 4 (docs/theming_2026-09.md §9), and this file holds that retirement:

  * no layout carries a `_base` attribute, and no module reads one;
  * no other shared module re-declares the 320x240 default size.

The app tier is a DECLARED second vocabulary and is not covered here:
`app_shell.ListShellLayout` and the calc/appearance/artwork heads speak
`(w, h, fs, windowed=)` (§6).
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

def _read(rel):
    with open(os.path.join(ROOT, rel), "r", encoding="utf-8") as f:
        return f.read()


@pytest.mark.parametrize("cls,has_bounds", LAYOUTS)
def test_every_layout_subclasses_the_base(cls, has_bounds):
    assert issubclass(cls, layout_base.LayoutBase)


@pytest.mark.parametrize("cls,has_bounds", LAYOUTS)
def test_no_layout_keeps_a_frozen_branch(cls, has_bounds):
    for args in ((), (320, 240, 1), (480, 320, 1), (1024, 600, 2)):
        lay = cls(*args)
        assert not hasattr(lay, "_base"), (cls.__name__, args)
    assert (cls().w, cls().h, cls().fs) == (layout_base.BASE_W, layout_base.BASE_H, 1)


def test_the_scales_clamp():
    for cls, _ in LAYOUTS:
        assert cls(320, 240, 0).fs == 1 and cls(320, 240, -3).fs == 1
        assert cls(320, 240, 1, chrome_scale=2).cs == 2
        assert cls(320, 240, 2, chrome_scale=1).cs == 2


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


def test_no_module_reads_a_layout_base():
    """A `layout._base` read is a frozen 320x240 branch coming back.
    `op_history.History._base` is the undo keyframe and is not a layout."""
    pat = re.compile(r"(layout|lay|L|self)\._base\b")
    for rel, src in _shared_sources():
        if rel.endswith("op_history.py"):
            continue
        assert not pat.search(src), "%s reads a layout's `_base`" % rel
