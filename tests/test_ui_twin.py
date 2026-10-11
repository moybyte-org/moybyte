"""The toolkit against its oracle (docs/kernel_toolkit_2026-10.md §7, §10).

`tests/ui_twin.py` is the toolkit's Python as it was before the crossing. Two
nets hold the shipped toolkit (`runtime/ui.py` and what it calls) to it:

* **The fuzz.** Every widget, drawn over random cases -- boxes smaller than
  their label, negative and float coordinates, ASCII and UTF-8 labels, font
  scales 1 to 3, both skins, every state -- once through the twin and once
  through the toolkit, each case on its own canvas, compared pixel by pixel
  and op count by op count. A failure names the widget, the case and the
  bounding box of the pixels that differ.
* **The op budgets.** Each representative widget call issues a pinned number
  of fills and texts, read off the canvas row's counters
  (`DeviceCanvas.gate_counts`) -- the ops a C primitive must also issue, so a
  body that draws more or fewer than the one it replaced fails here.
"""

import hashlib
import random

import pytest

import ui_twin as twin
from runtime import ui
from runtime.chrome import THEME_VARIANTS, THEMES, theme_colors
from runtime.host_canvas import indices_of
from runtime.host_canvas import make_system_canvas as SystemCanvas


class _Img:
    """A 16x16 indexed tile, duck-typed as the system canvas's spr takes it."""

    def __init__(self, seed=3):
        self.w = 16
        self.h = 16
        self.pix = bytearray(((i * 7 + seed) & 63) for i in range(256))
        self.transparent = -1
        self._paint = True


IMG = _Img()


def _glyph_draw(kind, rect, color, cv):
    x, y, w, h = rect
    cv.rect(x + 1, y + 1, max(1, w - 2), max(1, h - 2), color)
    cv.rectb(x, y, w, h, color)


def _raster(kind, rect, color, cv):
    """The real C glyph raster: the twin draws its glyphs through it, as the
    toolkit does (§7)."""
    ui.glyph(cv, kind, rect, color)


def _gd(m, c):
    """A case's glyph_draw: none without a glyph; the twin always passes the
    raster; the toolkit half the time passes none and draws its own."""
    if not c["glyph"]:
        return None
    if m is ui and c["flag"]:
        return None
    return _raster


LABELS = ("", "A", "OK", "PLAY", "CHANGE", "Config", "DELETE FOREVER",
          "a label much wider than any box it is drawn into",
          "café", "Ünïcödé Läbël",
          "日本語", "x€y\U0001f600z")
THEME_SETS = [(n, v) for n, _t in THEMES for v in THEME_VARIANTS]
STATE_ARGS = (None, "hover", "pressed")


def _coord(r, lo, hi):
    v = r.randint(lo, hi)
    return v + 0.5 if r.random() < 0.15 else v


def _box(r):
    return (_coord(r, -12, 70), _coord(r, -8, 44),
            _coord(r, 0, 110), _coord(r, 0, 40))


def _case(r):
    name, variant = r.choice(THEME_SETS)
    return {
        "th": (name, variant), "fs": r.randint(1, 3),
        "skin": r.choice(("default", "outline")),
        "box": _box(r), "box2": _box(r), "label": r.choice(LABELS),
        "label2": r.choice(LABELS), "on": r.random() < 0.3,
        "hot": r.random() < 0.2, "disabled": r.random() < 0.2,
        "state": r.choice(STATE_ARGS), "kind": r.choice(
            ("row", "row_menu", "row_list", "row_chrome", "row_cta", "cell",
             "chip", "nosuchkind")),
        "bkind": r.choice(("normal", "play", "author", "danger", "nosuch")),
        "icon": r.random() < 0.25, "glyph": r.random() < 0.25,
        "n": r.randint(0, 4), "int": r.randint(-3, 9), "flag": r.random() < 0.5,
        "pad": r.choice((None, 0, 4, 6)), "dy": r.choice((None, 0, 5)),
        "scale": r.choice((1, 1, 2)),
    }


# One driver per widget: draw case `c` through toolkit module `m`.

def _button(m, cv, th, c):
    m.button(cv, th, c["box"], c["label"], kind=c["bkind"], on=c["on"],
             icon_img=IMG if c["icon"] else None,
             glyph="run" if c["glyph"] else None,
             glyph_draw=_gd(m, c),
             disabled=c["disabled"], state=c["state"])


def _chip(m, cv, th, c):
    m.chip(cv, th, c["box"], c["label"], on=c["on"], hot=c["hot"],
           fs=c["fs"] if c["flag"] else None,
           glyph="edit" if c["glyph"] else None,
           glyph_draw=_gd(m, c),
           disabled=c["disabled"], state=c["state"])


def _row(m, cv, th, c):
    hits = m.Hits()
    m.row(cv, th, c["box"], c["label"], kind=c["kind"], on=c["on"],
          hot=c["hot"], disabled=c["disabled"], state=c["state"],
          icon_img=IMG if c["icon"] else None,
          glyph="gear" if c["glyph"] else None,
          glyph_draw=_gd(m, c),
          value=c["label2"] if c["flag"] else None, edge=c["n"] != 0,
          pad=c["pad"], text_dy=c["dy"], hits=hits, verb="row", arg=c["int"],
          scale=c["scale"])
    return [(it[0], it[1], it[2]) for it in hits._items]


def _cell(m, cv, th, c):
    return m.cell(cv, th, c["box"], label=c["label"] if c["flag"] else None,
                  kind=c["kind"], on=c["on"], hot=c["hot"],
                  disabled=c["disabled"], state=c["state"],
                  band=c["n"] > 2, pad=c["pad"],
                  icon_img=IMG if c["icon"] else None,
                  glyph="map" if c["glyph"] else None,
                  glyph_draw=_gd(m, c),
                  text_dy=c["dy"], edge_last=c["hot"])


TABS = (("cfg", "Config", "gear"), ("code", "Code", "edit"),
        ("map", "Map", "map"), ("u", "café", "paint"))


def _tab_row(m, cv, th, c):
    hits = m.Hits()
    rects = m.tab_row(cv, th, c["box"], TABS[:c["n"]], "code",
                      icon_for=(lambda k: IMG) if c["icon"] else None,
                      hits=hits, ink=c["int"] if c["flag"] else None)
    return rects, [(it[0], it[1], it[2]) for it in hits._items]


def _status_row(m, cv, th, c):
    m.status_row(cv, th, c["box"], (c["label"], c["label2"], "Ln 3")[:c["n"]])


def _game_family(m, cv, th, c):
    m.game_btn(cv, c["box"], c["label"], c["int"] & 63)
    m.game_icon_btn(cv, c["box2"], "run", c["label2"], 11,
                    glyph_draw=_raster if c["glyph"] else None)
    m.mini_btn(cv, c["box"], c["label2"], 9,
               label_rect=c["box2"] if c["flag"] else None)


def _frames(m, cv, th, c):
    m.toolbar(cv, th, c["box2"])
    m.dialog(cv, c["box"], ring=c["int"] if c["flag"] else None)
    content = m.panel(cv, th, c["box2"], title=c["label"] or None,
                      fs=c["fs"] if c["hot"] else None)
    m.focus_ring(cv, th, c["box"], fs=c["fs"] if c["on"] else None)
    return content


def _text_field(m, cv, th, c):
    m.text_field(cv, c["box"], c["label"], c["label2"],
                 colors=(1, 2, 3, 4, 5) if c["on"] else None,
                 fs=c["fs"] if c["flag"] else None)


def _scroll(m, cv, th, c):
    sr = m.ScrollRegion(horizontal=c["flag"])
    sr.set(c["box"], c["int"] * 37 + 50)
    sr.scroll_by(c["n"] * 23)
    sr.draw_bar(cv, th)
    m.scroll_cues(cv, c["box"][:2], c["box2"][:2], c["on"], c["hot"],
                  c["int"] & 63, c["scale"])
    m.fill_uncovered(cv, c["box"], c["box2"], c["int"] & 63)
    return sr.bar_rect(c["fs"]), sr.offset


def _algebra(m, cv, th, c):
    b = tuple(int(v) for v in c["box"])
    out = [m.inset(b, c["int"]), m.cut_top(b, c["n"] * 5),
           m.cut_bottom(b, c["n"] * 5), m.cut_left(b, c["int"] * 3),
           m.cut_right(b, c["int"] * 3), m.hsplit(b, c["n"], c["fs"]),
           m.vsplit(b, c["n"], c["fs"]), m.wrap_words(c["label"], c["n"] + 2),
           m.tab_row_rects(b, [(t[0], t[1]) for t in TABS[:c["n"]]], c["fs"]),
           m.row_content_rect(b, c["fs"], c["pad"], None, c["icon"],
                              c["glyph"]),
           m.cell_art_rect(b, c["fs"], c["pad"], c["n"] * 4)]
    return out


WIDGETS = {
    "button": _button, "chip": _chip, "row": _row, "cell": _cell,
    "tab_row": _tab_row, "status_row": _status_row,
    "game_family": _game_family, "frames": _frames,
    "text_field": _text_field, "scroll": _scroll, "algebra": _algebra,
}
CASES = 48


# The faces convert coordinates toward zero before the C call, as the print
# gate's gate_num does (§7); the twin's bodies slice with them, so the oracle
# draws the truncated case. False while the toolkit is still the Python.
FACE_TRUNCATES = False


def _truncated(c):
    t = dict(c)
    t["box"] = tuple(int(v) for v in c["box"])
    t["box2"] = tuple(int(v) for v in c["box2"])
    return t


def _draw(m, use, widget, c):
    use(c["skin"])
    th = theme_colors(*c["th"])
    cv = SystemCanvas(128, 72, font_scale=c["fs"])
    cv.gate_counts_reset()
    ret = WIDGETS[widget](m, cv, th, c)
    return indices_of(cv), cv.gate_counts()[:2], ret


def _bbox(a, b, w=128):
    xs, ys = [], []
    for i in range(len(a)):
        if a[i] != b[i]:
            xs.append(i % w)
            ys.append(i // w)
    return (min(xs), min(ys), max(xs) - min(xs) + 1, max(ys) - min(ys) + 1)


@pytest.fixture
def _skins():
    yield
    ui.use_skin("default")
    twin.use(twin.DEFAULT)


@pytest.mark.parametrize("widget", sorted(WIDGETS))
def test_the_toolkit_draws_what_the_twin_drew(widget, _skins):
    r = random.Random("ui-twin:" + widget)
    for i in range(CASES):
        c = _case(r)
        want, want_ops, want_ret = _draw(twin, twin.use, widget, _truncated(c))
        got, got_ops, got_ret = _draw(ui, ui.use_skin, widget,
                                      c if FACE_TRUNCATES else _truncated(c))
        if got != want:
            raise AssertionError("%s case %d %r: pixels differ in %r (hash %s, "
                                 "twin %s)" % (
                                     widget, i, c, _bbox(got, want),
                                     hashlib.sha256(got).hexdigest()[:12],
                                     hashlib.sha256(want).hexdigest()[:12]))
        assert got_ops == want_ops, "%s case %d %r: ops %r, twin %r" % (
            widget, i, c, got_ops, want_ops)
        assert got_ret == want_ret, "%s case %d %r: returned %r, twin %r" % (
            widget, i, c, got_ret, want_ret)


# --- the op budgets -----------------------------------------------------------

def _b_button(m, cv, th):
    m.button(cv, th, (8, 14, 90, 22), "PLAY", kind="play")


def _b_button_icon(m, cv, th):
    m.button(cv, th, (8, 14, 90, 22), "ICON", icon_img=IMG)


def _b_button_glyph(m, cv, th):
    m.button(cv, th, (8, 14, 90, 22), "GLYPH", glyph="run",
             glyph_draw=_glyph_draw)


def _b_chip(m, cv, th):
    m.chip(cv, th, (8, 14, 60, 18), "QUIET")


def _b_chip_glyph(m, cv, th):
    m.chip(cv, th, (8, 14, 60, 18), "G", glyph="edit", glyph_draw=_glyph_draw)


def _b_row(m, cv, th):
    m.row(cv, th, (0, 20, 200, 18), "ROW", value="ON")


def _b_row_menu(m, cv, th):
    m.row(cv, th, (0, 20, 200, 18), "ITEM", kind="row_menu", edge=False)


def _b_row_menu_on(m, cv, th):
    m.row(cv, th, (0, 20, 200, 18), "ITEM", kind="row_menu", on=True,
          edge=False)


def _b_row_glyph(m, cv, th):
    m.row(cv, th, (0, 20, 200, 18), "BADGE", glyph="trophy",
          glyph_draw=_glyph_draw)


def _b_cell(m, cv, th):
    m.cell(cv, th, (0, 0, 60, 60), label="FILE")


def _b_cell_band(m, cv, th):
    m.cell(cv, th, (0, 0, 60, 60), label="WALL", band=True, icon_img=IMG)


def _b_tab_row(m, cv, th):
    m.tab_row(cv, th, (0, 0, 200, 20), TABS[:3], "code",
              icon_for=lambda k: IMG)


def _b_status(m, cv, th):
    m.status_row(cv, th, (0, 0, 200, 14), ("Ln 13, Col 1", "No issues"))


def _b_game_btn(m, cv, th):
    m.game_btn(cv, (0, 0, 70, 20), "GO", 11)


def _b_game_icon_btn(m, cv, th):
    m.game_icon_btn(cv, (0, 0, 70, 20), "run", "GO", 11,
                    glyph_draw=_glyph_draw)


def _b_mini_btn(m, cv, th):
    m.mini_btn(cv, (0, 0, 30, 12), "-", 9)


def _b_toolbar(m, cv, th):
    m.toolbar(cv, th, (0, 0, 140, 16))


def _b_dialog(m, cv, th):
    m.dialog(cv, (0, 0, 130, 40))


def _b_text_field(m, cv, th):
    m.text_field(cv, (0, 0, 120, 14), "hi", "")


def _b_panel(m, cv, th):
    m.panel(cv, th, (4, 4, 60, 60), title="TOOLS")


def _b_focus(m, cv, th):
    m.focus_ring(cv, th, (8, 8, 40, 20))


def _b_scrollbar(m, cv, th):
    sr = m.ScrollRegion()
    sr.set((0, 0, 60, 100), 400)
    sr.scroll_by(120)
    sr.draw_bar(cv, th)


def _b_scroll_cues(m, cv, th):
    m.scroll_cues(cv, (4, 4), (4, 50), True, True, 10, 2)


def _b_fill_uncovered(m, cv, th):
    m.fill_uncovered(cv, (0, 20, 120, 40), (10, 25, 60, 20), 3)


def _b_glyph(m, cv, th):
    if m is twin:
        twin.blit_glyph(cv, "run", (4, 4, 40, 40), 7)
    else:
        m.glyph(cv, "run", (4, 4, 40, 40), 7)


# (fills, texts) per call at font scale 2 on the machine theme: what the twin
# issues, and so what a C primitive must issue.
OP_BUDGETS = {
    "button": (_b_button, (2, 1)),
    "button_icon": (_b_button_icon, (2, 1)),
    "button_glyph": (_b_button_glyph, (4, 1)),
    "chip": (_b_chip, (2, 1)),
    "chip_glyph": (_b_chip_glyph, (4, 0)),
    "row": (_b_row, (2, 2)),
    "row_menu": (_b_row_menu, (0, 1)),
    "row_menu_on": (_b_row_menu_on, (1, 1)),
    "row_glyph": (_b_row_glyph, (4, 1)),
    "cell": (_b_cell, (2, 1)),
    "cell_band": (_b_cell_band, (3, 1)),
    "tab_row": (_b_tab_row, (1, 0)),
    "status": (_b_status, (2, 1)),
    "game_btn": (_b_game_btn, (2, 1)),
    "game_icon_btn": (_b_game_icon_btn, (4, 1)),
    "mini_btn": (_b_mini_btn, (1, 1)),
    "toolbar": (_b_toolbar, (1, 0)),
    "dialog": (_b_dialog, (2, 0)),
    "text_field": (_b_text_field, (3, 1)),
    "panel": (_b_panel, (4, 1)),
    "focus": (_b_focus, (2, 0)),
    "scrollbar": (_b_scrollbar, (2, 0)),
    "scroll_cues": (_b_scroll_cues, (0, 2)),
    "fill_uncovered": (_b_fill_uncovered, (4, 0)),
    "glyph": (_b_glyph, (10, 0)),                 # one fill per span
}


def _ops(m, fn):
    th = theme_colors("machine")
    cv = SystemCanvas(320, 240, font_scale=2)
    cv.gate_counts_reset()
    fn(m, cv, th)
    return cv.gate_counts()[:2]


@pytest.mark.parametrize("name", sorted(OP_BUDGETS))
def test_each_widget_issues_its_budgeted_ops(name):
    fn, budget = OP_BUDGETS[name]
    assert _ops(twin, fn) == budget, "the twin's count is the budget"
    assert _ops(ui, fn) == budget, (
        "%s issues %r ops, budgeted %r: a body drawing more or fewer ops "
        "than the one it replaced" % (name, _ops(ui, fn), budget))
