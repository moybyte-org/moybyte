"""The readability floor over what the toolkit actually draws
(docs/theming_2026-09.md §7): `theme.pairs` holds an ink to its backgrounds
at `contrast`, and the seeds clear it (tests/test_theme_loader.py). These
pin that the pairs are the ones the widgets and the frame paint with, and
that every seed in both variants reads through the real C resolution:

* every kind's state that inks a role field with a floor ink names a pair
  of `theme.pairs`, so the derivation and the unreadable bits cover it for
  every theme, a kid's included;
* every seed x variant resolves each such state above the floor;
* every seed x variant draws its window titles, focused and not, above it;
* a chrome part's surface that leaves out its fill or names a bare border
  paints the part's own colours (the bar's window chips, the desk's tiles),
  and a button's bare border is a role, not black.
"""

import json
import os

import pytest

import runtime  # noqa: F401  (registers the host's moy_ui)
from runtime import ui
from runtime.chrome import theme_colors
from runtime.host_canvas import indices_of
from runtime.host_canvas import make_system_canvas as SystemCanvas
from tools.gen_ui_tables import _luminance

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
with open(os.path.join(ROOT, "native", "moy_ui", "style.json")) as _f:
    STYLE = json.load(_f)
TH = STYLE["theme"]
TOKENS = set(STYLE["tokens"])
PAIRS = {tuple(p) for p in TH["pairs"]}
FLOOR_INKS = {ink for _bg, ink in PAIRS}
LUM = _luminance()
STATES = {"rest": ui.REST, "hover": ui.HOVER, "on": ui.ON, "hot": ui.HOT,
          "pressed": ui.PRESSED}
VARIANTS = ("dark", "light")


def _clears(a, b):
    la, lb = LUM[a], LUM[b]
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 500) * 10 >= TH["contrast"] * (lo + 500)


def _role(spec):
    """The role a token spec tries first (an alias resolves to its own
    first), or None for a palette index or null."""
    first = spec[0] if isinstance(spec, list) else spec
    if not isinstance(first, str):
        return None
    alias = STYLE["aliases"].get(first)
    return alias[0] if alias else first


# The chrome rows' selection fills `hilite` and inks it `selection_ink`, which
# the floor holds to `selection`: the two are one colour in every seed but
# machine (where the pair still reads), and the accent derivation moves them
# apart (selection takes the accent, hilite keeps its tint), so no one ink can
# be held to both. Named here so a fourth does not join them unseen.
UNPAIRED = {("row_menu", "on"), ("row_list", "on"), ("row_chrome", "on")}


def _floor_states():
    """(kind, state, field role, ink role) for every state that inks a role
    field with an ink the floor holds -- but disabled, dim by design, and hot,
    the danger field every other kind inks with a palette index."""
    out = []
    for kind, states in STYLE["kinds"].items():
        for st, (field, ink, _edge) in states.items():
            f, i = _role(field), _role(ink)
            if st not in ("disabled", "hot") and f in TOKENS and i in FLOOR_INKS:
                out.append((kind, st, f, i))
    return out


def test_every_kind_inks_its_field_with_a_floor_pair():
    """A widget that inks its field with another surface's ink is read by no
    check: the Files list's title_ink on panel was black on navy in five
    dark seeds while every pair cleared."""
    bad = {(k, st) for k, st, f, i in _floor_states() if (f, i) not in PAIRS}
    assert bad == UNPAIRED
    assert ("row", "rest", "panel", "chrome_ink") in _floor_states()


@pytest.mark.parametrize("variant", VARIANTS)
@pytest.mark.parametrize("seed", TH["seeds"])
def test_every_seed_reads_every_kind(seed, variant):
    th = theme_colors(seed, variant)
    bad = []
    for kind, st, _f, _i in _floor_states():
        field, ink, _edge = ui.state_colors(th, kind, STATES[st])
        if field is not None and ink is not None and not _clears(field, ink):
            bad.append((kind, st, field, ink))
    assert not bad


def _title_pixels(seed, variant, active, fs):
    """The window title's pixels and the strip's colour beside them, from the
    real frame: a titled frame against the same frame untitled."""
    ui.use_shape(seed)
    th = theme_colors(seed, variant)
    out = []
    for title in ("FILES", ""):
        cv = SystemCanvas(240, 100)
        cv.cls(9)
        ui.frame(cv, th, (10, 10, 200, 70), 18 * fs, title, active, True, fs)
        out.append(indices_of(cv))
    titled, bare = out
    left = 10 + 1 + 3 * fs                   # past classic's identity stripe
    diff = [i for i in range(len(titled))
            if titled[i] != bare[i] and i % 240 >= left]
    return titled, diff


@pytest.mark.parametrize("active", [True, False])
@pytest.mark.parametrize("variant", VARIANTS)
@pytest.mark.parametrize("seed", TH["seeds"])
def test_every_seed_reads_its_window_titles(seed, variant, active):
    """Win95 light inked its unfocused title chrome_ink_dim on a
    title_inactive of the same grey: the title was gone."""
    try:
        px, diff = _title_pixels(seed, variant, active, 1)
    finally:
        ui.use_shape(None)
    assert diff, "the title draws nothing"
    xs = [i % 240 for i in diff]
    ys = [i // 240 for i in diff]
    box = [px[y * 240 + x] for y in range(min(ys), max(ys) + 1)
           for x in range(min(xs), max(xs) + 1)]
    strip = max(set(box), key=box.count)
    inks = {px[i] for i in diff} - {strip}
    assert inks and all(_clears(c, strip) for c in inks), (inks, strip)


def test_the_inactive_title_is_a_floor_pair():
    assert ("title_active", "title_ink") in PAIRS
    assert ("title_inactive", "chrome_ink_dim") in PAIRS


def _install(name, surfaces, colors=None):
    import moy_ui
    pkg = {"format": 1, "name": name, "surfaces": surfaces}
    if colors:
        pkg["colors"] = colors
    rc, _notes = moy_ui.theme_install(json.dumps(pkg).encode())
    assert rc == 0
    ui.use_shape(name)


def test_a_chrome_part_without_a_fill_paints_its_own_colours():
    """The Contrast seed's `bar.item: border` painted nothing, so the focused
    window's chip was black ink on the black bar; its `desk.icon` bare
    border was no line at all."""
    th = theme_colors("contrast")
    try:
        _install("parts", {"bar.item": {"rest": "border"},
                           "desk.icon": {"rest": "fill panel, border"}})
        for state, field, edge in ((ui.REST, th["panel"], th["chrome_ink"]),
                                   (ui.ON, th["accent"], th["chrome_ink"])):
            cv = SystemCanvas(40, 20)
            cv.cls(9)
            assert ui.paint(cv, th, (4, 4, 30, 12), "bar.item", state)
            px = indices_of(cv)
            assert px[4 * 40 + 10] == edge and px[10 * 40 + 10] == field
        cv = SystemCanvas(40, 40)
        cv.cls(9)
        assert ui.paint(cv, th, (4, 4, 30, 30), "desk.icon")
        px = indices_of(cv)
        assert px[4 * 40 + 10] == th["edge"] and px[15 * 40 + 15] == th["panel"]
    finally:
        ui.use_shape(None)


def test_a_bare_button_border_is_the_shade_dark_role():
    """Contrast's `button: border` drew a black edge on its black keys."""
    th = theme_colors("contrast")
    try:
        ui.use_shape("contrast")
        cv = SystemCanvas(60, 40)
        cv.cls(th["panel"])
        ui.button(cv, th, (5, 5, 40, 24), "7")
        px = indices_of(cv)
        assert px[5 * 60 + 20] == th["shade_dark"] != th["panel"]
    finally:
        ui.use_shape(None)
