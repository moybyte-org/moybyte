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
from runtime import ui_draw
from runtime.chrome import live_colors, theme_colors
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
STATES = {"rest": ui_draw.REST, "hover": ui_draw.HOVER, "on": ui_draw.ON, "hot": ui_draw.HOT,
          "pressed": ui_draw.PRESSED}
VARIANTS = ("dark", "light")


def _lum(v):
    """An index's luminance, or a block slot's or an RGB value's (the C's)."""
    return LUM[v] if v < 64 else ui_draw._C.theme_rgb(v)[1]


def _clears(a, b):
    la, lb = _lum(a), _lum(b)
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
    """Through the live placement: an RGB role reads in its block slot."""
    live = live_colors(seed, variant)
    ui_draw._C.theme_block(live.block, live.used)
    th = live.tokens
    bad = []
    for kind, st, _f, _i in _floor_states():
        field, ink, _edge = ui_draw.state_colors(th, kind, STATES[st])
        if field is not None and ink is not None and not _clears(field, ink):
            bad.append((kind, st, field, ink))
    assert not bad


@pytest.mark.parametrize("variant", VARIANTS)
@pytest.mark.parametrize("seed", TH["seeds"])
def test_every_seed_reads_every_button_kind(seed, variant):
    """Each button kind's ink on its field, in every state but disabled,
    through the live placement and the seed's own button leaves
    (docs/look_2026-10.md step 10): a derived ink that misses the floor took
    its kind's floor form, and no seed's `buttons` reads under it. A seed
    with marks draws `on` as its current mark, never the ON look (step 11)."""
    live = live_colors(seed, variant)
    ui_draw._C.theme_block(live.block, live.used)
    ui_draw.use_buttons(live)
    th = live.tokens
    look = ui_draw._look(th)
    bad = []
    ui_draw.use_depth(seed, variant == "light")
    marks = ui_draw.marks()[0]
    ui_draw.use_depth(None, False)
    try:
        for kind in ui_draw.BUTTON_KINDS:
            kid = ui_draw._kind_id(ui_draw._button_kind(kind))
            for st in STATES.values():
                if marks and st == "on":
                    continue
                field, ink = ui_draw._C.button_style(look, kid, ui_draw._STATE_ID[st], -1)[:2]
                if field is not None and ink is not None and not _clears(field, ink):
                    bad.append((kind, st, field, ink))
        assert ui_draw.button_unreadable(th) == []
    finally:
        ui_draw.use_buttons(None)
    assert not bad


@pytest.mark.parametrize("variant", VARIANTS)
@pytest.mark.parametrize("seed", TH["seeds"])
def test_a_seeds_danger_button_reads_as_text(seed, variant):
    """A danger button's label (Calc's C) is text on its field at rest: held
    to 4.5:1, text's floor, not the 3:1 a part's colour is. A seed whose
    `danger` cannot be both that and a fill white reads on draws its danger
    buttons in its ink (`buttons.danger.ink`: Moy, as its target does, and
    Blocks)."""
    live = live_colors(seed, variant)
    ui_draw._C.theme_block(live.block, live.used)
    ui_draw.use_buttons(live)
    try:
        look = ui_draw._look(live.tokens)
        kid = ui_draw._kind_id(ui_draw._button_kind("danger"))
        field, ink = ui_draw._C.button_style(look, kid, ui_draw._REST_ID, -1)[:2]
    finally:
        ui_draw.use_buttons(None)
    la, lb = _lum(field), _lum(ink)
    hi, lo = max(la, lb), min(la, lb)
    assert (hi + 500) * 10 >= 45 * (lo + 500), (field, ink)


@pytest.mark.parametrize("variant", VARIANTS)
@pytest.mark.parametrize("seed", TH["seeds"])
def test_a_seeds_disabled_ink_is_quiet_and_seen(seed, variant):
    """Every seed names its `disabled` ink, and it is seen (2.5:1 or more on
    the surface) and quieter than the ink and its dim, so a disabled control
    is neither invisible (Moy, Blocks) nor brighter than an enabled one
    (Contrast, where ink_dim is the ink). It is what every disabled part
    inks in (`ui_draw.use_buttons`)."""
    live = live_colors(seed, variant)
    ui_draw._C.theme_block(live.block, live.used)
    th = live.tokens
    dis = live.look[0].get("disabled")
    assert dis is not None and dis >= 0

    def ratio(a, b):
        la, lb = _lum(a), _lum(b)
        return (max(la, lb) + 500) / (min(la, lb) + 500)
    assert ratio(dis, th["surface"]) >= 2.5
    assert ratio(dis, th["surface"]) < ratio(th["ink_dim"], th["surface"])
    ui_draw.use_buttons(live)
    try:
        assert ui_draw.disabled_ink(th) == dis
        look = ui_draw._look(th)
        assert ui_draw._C.colors(look, ui_draw._kind_id("button"),
                                 ui_draw._STATE_ID["disabled"])[1] == dis
    finally:
        ui_draw.use_buttons(None)
    assert ui_draw.disabled_ink(th) == th["ink_dim"]


def _rgb(v):
    """A colour index's (r, g, b) as the glass shows it (RGB565, widened)."""
    c = ui_draw._C.theme_rgb(v)[0]
    return ((c >> 11) * 255 // 31, ((c >> 5) & 63) * 255 // 63, (c & 31) * 255 // 31)


def _wcag(a, b):
    def lum(c):
        lin = [x / 255 / 12.92 if x / 255 <= 0.03928 else ((x / 255 + 0.055) / 1.055) ** 2.4
               for x in c]
        return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]
    la, lb = lum(a), lum(b)
    return (max(la, lb) + 0.05) / (min(la, lb) + 0.05)


@pytest.mark.parametrize("variant", VARIANTS)
@pytest.mark.parametrize("seed", TH["seeds"])
def test_the_code_roles_read_on_the_current_line(seed, variant):
    """The code editor marks its current line with the current mark: where
    that is a plate behind the line (`current.style` plate, `current.row`
    plate), every code role holds text's 4.5:1 on the plate as well as on
    the code surface (theme.code). Contrast outlines the row instead, so its
    line keeps the surface the code roles are held to."""
    live = live_colors(seed, variant)
    ui_draw._C.theme_block(live.block, live.used)
    lk = live.look[0]
    if lk["current.style"] != 1 or lk["current.row"] != 0:
        return
    tokens = STYLE["tokens"]
    s, val = _rgb(live.tokens[TH["code"]["bg"]]), _rgb(live.tokens[tokens[lk["current.color"]]])
    k = lk["current.alpha"] / 100
    plate = tuple(round(s[i] + (val[i] - s[i]) * k) for i in range(3))
    low = {r: round(_wcag(_rgb(live.content[r]), plate), 2) for r in TH["code"]["roles"]
           if _wcag(_rgb(live.content[r]), plate) < 4.5}
    assert not low, (plate, low)


@pytest.mark.parametrize("variant", VARIANTS)
@pytest.mark.parametrize("seed", TH["seeds"])
def test_the_accent_as_ink_clears_the_code_surface(seed, variant):
    """`ink_accent` is the accent where it is an ink: the accent derivation
    picks the first of the accent, its deep and the ink that clears the
    surface, and every seed holds it there too. A mark drawn on the code
    surface in the accent (a caret) reads only through this role: Contrast
    Light's `accent` is its yellow, 1.27:1 on white."""
    live = live_colors(seed, variant)
    ui_draw._C.theme_block(live.block, live.used)
    s, ink = _rgb(live.tokens[TH["code"]["bg"]]), _rgb(live.tokens["ink_accent"])
    assert _wcag(ink, s) >= 3.0, (seed, variant, round(_wcag(ink, s), 2))


def _title_pixels(seed, variant, active, fs):
    """The window title's pixels and the strip's colour beside them, from the
    real frame: a titled frame against the same frame untitled."""
    ui_draw.use_shape(seed)
    th = theme_colors(seed, variant)
    out = []
    for title in ("FILES", ""):
        cv = SystemCanvas(240, 100)
        cv.cls(9)
        ui_draw.frame(cv, th, (10, 10, 200, 70), 18 * fs, title, active, True, fs)
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
        ui_draw.use_shape(None)
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
    pkg = {"format": 2, "name": name, "surfaces": surfaces}
    if colors:
        pkg["colors"] = colors
    rc, _notes = moy_ui.theme_install(json.dumps(pkg).encode())
    assert rc == 0
    ui_draw.use_shape(name)


def _black_and_white():
    """The palette Contrast had before its look (docs/look_2026-10.md step
    26c): black panels, white ink and edges, yellow accent."""
    th = dict(theme_colors("night"))
    th.update(panel=0, edge=7, chrome_ink=7, accent=10, shade_dark=7, ink=7)
    return th


def test_a_chrome_part_without_a_fill_paints_its_own_colours():
    """Contrast's palette seed had `bar.item: border`, which painted nothing,
    so the focused window's chip was black ink on the black bar; its
    `desk.icon` bare border was no line at all."""
    th = _black_and_white()
    try:
        _install("parts", {"bar.item": {"rest": "border"},
                           "desk.icon": {"rest": "fill panel, border"}})
        for state, field, edge in ((ui_draw.REST, th["panel"], th["chrome_ink"]),
                                   (ui_draw.ON, th["accent"], th["chrome_ink"])):
            cv = SystemCanvas(40, 20)
            cv.cls(9)
            assert ui_draw.paint(cv, th, (4, 4, 30, 12), "bar.item", state)
            px = indices_of(cv)
            assert px[4 * 40 + 10] == edge and px[10 * 40 + 10] == field
        cv = SystemCanvas(40, 40)
        cv.cls(9)
        assert ui_draw.paint(cv, th, (4, 4, 30, 30), "desk.icon")
        px = indices_of(cv)
        assert px[4 * 40 + 10] == th["edge"] and px[15 * 40 + 15] == th["panel"]
    finally:
        ui_draw.use_shape(None)


def test_a_bare_button_border_is_the_shade_dark_role():
    """Contrast's palette seed's `button: border` drew a black edge on its
    black keys."""
    th = _black_and_white()
    try:
        _install("keys", {"button": {"rest": "border", "pressed": "border"}})
        cv = SystemCanvas(60, 40)
        cv.cls(th["panel"])
        ui_draw.button(cv, th, (5, 5, 40, 24), "7")
        px = indices_of(cv)
        assert px[5 * 60 + 20] == th["shade_dark"] != th["panel"]
    finally:
        ui_draw.use_shape(None)
