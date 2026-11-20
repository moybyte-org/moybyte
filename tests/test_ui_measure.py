"""The toolkit's measure, a `fit` button and the label's alignment and card
(native/moy_ui/moy_ui_api.h's moy_ui_api_measure, moy_ui_api_space,
moy_ui_api_label): each a new option that leaves every existing call's
pixels as they were."""

from runtime import ui, ui_draw, ui_host

from test_ui_public import _Run

_WRAP = {"wrap": True, "tone": "dim"}
_LONG = " ".join(["a sentence long enough to wrap across the row"] * 6)


def _px(r, x, y):
    cv = r.cv
    return bytes(cv._buf[2 * (y * cv.w + x):2 * (y * cv.w + x) + 2])


def test_a_measure_answers_the_box_the_next_call_takes_and_draws_nothing():
    seen = {}

    def draw():
        ui.page("M")
        ui.label("first")
        seen["label"] = ui.measure("label", _LONG, _WRAP)
        seen["button"] = ui.measure("button", "Go")
        seen["fit"] = ui.measure("button", "Go", {"fit": True})
        ui.label(_LONG, _WRAP)
        seen["after_label"] = ui.measure("button", "Go")
        ui.button("Go", "normal", {"fit": True})
        seen["after_fit"] = ui.measure("button", "Go")
        ui.button("Go", "normal", {"id": "go2"})
        ui.at(10, 20, 100, 30)
        seen["at"] = ui.measure("label", "x")
        ui.label("x")

    r = _Run(draw)
    r.frame()
    x, y, w, h, step = seen["label"]
    assert step >= h > 0
    assert seen["after_label"][1] == y + step       # the label took what it said
    bx, by, bw, bh, bstep = seen["button"]
    fx, fy, fw, fh, fstep = seen["fit"]
    assert (bx, by) == (x, y) and bw < w and fh > bh and fw > bw
    assert seen["after_fit"][1] == seen["after_label"][1] + fstep
    assert seen["at"][:2] == (10, 20) and seen["at"][4] == 0    # ui.at's box: no step
    go, go2 = ui_host._C.api_box(r.ctx, "Go"), ui_host._C.api_box(r.ctx, "go2")
    assert go[2] < go2[2]                           # the fit button is as wide as it needs


def test_space_moves_the_stack_and_draws_nothing():
    seen = {}

    def draw():
        ui.page("S")
        a = ui.measure("label", "x")[1]
        ui.space(37)
        seen["moved"] = ui.measure("label", "x")[1] - a

    r = _Run(draw)
    r.frame()
    assert seen["moved"] == 37


def _label_frame(o, group=False):
    def draw():
        ui.page("L")
        if group:
            ui.group("Card")
        ui.label("Word", o)
        if group:
            ui.label("plain")
            ui.group()
    r = _Run(draw)
    r.frame()
    return r


def _ink_xs(r, y0, y1, ground):
    return [x for y in range(y0, y1) for x in range(r.cv.w) if _px(r, x, y) != ground]


def test_a_label_aligned_to_its_end_draws_at_the_rows_end():
    start = _label_frame({"tone": "dim"})
    end = _label_frame({"tone": "dim", "align": "end"})
    ground = _px(start, start.cv.w // 2, 230)
    a = _ink_xs(start, 0, 40, ground)
    b = _ink_xs(end, 0, 40, ground)
    assert a and b
    assert min(b) > start.cv.w // 2 > max(a)
    assert max(a) - min(a) == max(b) - min(b)       # the same word, moved


def test_a_wrapped_label_in_a_group_lies_on_its_card():
    """The card's band runs behind a text-option label's row as behind a
    plain one's: beside the word, inside the card, the row is the card's
    colour, not the page's ground; outside a group it is the ground."""
    on = _label_frame(_WRAP, group=True)
    off = _label_frame(_WRAP)
    ground = _px(off, off.cv.w // 2, 230)
    # the label's row, right of its one word, inside the card's inset
    assert {_px(off, 200, y) for y in range(4, 14)} == {ground}
    assert any(_px(on, 200, y) != ground for y in range(20, 40))


_PARAS = ["Paragraph %d: %s" % (i, " ".join(["words that wrap across the view"] * 4))
          for i in range(12)]


def _view(o):
    seen = {}

    def draw():
        ui.page("V")
        ui.button("Above")
        ui.at(10, 60, 300, 120)
        seen["out"] = ui.text_view("prose", _PARAS, dict(o))
    return draw, seen


def _scroll(r):
    return ui_host._C.api_offset(r.ctx, 100, 100)


def test_a_ui_face_view_draws_paragraphs_of_prose_and_scrolls():
    draw, seen = _view({"face": "ui"})
    r = _Run(draw)
    r.frame()
    first, shown = seen["out"]
    assert first == 0 and 0 < shown < len(_PARAS)
    ground = _px(r, 5, 200)
    assert any(_px(r, x, y) != ground for y in range(62, 80) for x in range(20, 200))


def test_a_view_with_keys_is_a_stop_its_arrows_and_pages_scroll():
    """L7: Down steps focus onto the view, then scrolls it a line; PgDn
    scrolls a page; Up at its top steps focus back off it."""
    for face in ("ui", "mono"):
        draw, seen = _view({"face": face, "keys": True, "wrap": True})
        r = _Run(draw)
        r.frame()
        assert ui.focus() == "Above"
        r.frame(press=("down",))
        r.frame()
        assert ui.focus() == "prose", face
        assert _scroll(r) == 0
        r.frame(press=("down",))
        r.frame()
        line = _scroll(r)
        assert line > 0, face
        r.frame(key=0x115)
        r.frame()
        assert _scroll(r) > line * 3, face
        r.frame(key=0x114)
        r.frame()
        r.frame(press=("up",))
        r.frame()
        assert _scroll(r) == 0, face
        r.frame(press=("up",))
        r.frame()
        assert ui.focus() == "Above", face


def test_page_keys_page_the_first_key_view_while_focus_is_elsewhere():
    """A list beside a text (the Licences desk): PgDn pages the view while
    focus stays on the list's row."""
    draw, seen = _view({"face": "ui", "keys": True})
    r = _Run(draw)
    r.frame()
    r.frame(key=0x115)
    r.frame()
    assert ui.focus() == "Above" and _scroll(r) > 0


def test_a_view_without_keys_is_no_stop():
    draw, seen = _view({"face": "ui"})
    r = _Run(draw)
    r.frame()
    r.frame(press=("down",))
    r.frame()
    assert ui.focus() == "Above" and _scroll(r) == 0


def test_the_page_keys_are_moy_inputs():
    from runtime import moy_input
    assert (moy_input.KEY_PGUP, moy_input.KEY_PGDN) == (0x114, 0x115)


def _link_rows(label, value, ws):
    """A link row of `label` and `value` drawn at each width of `ws`, one
    under the next; the measure of the row, of its value alone, and the
    rows' tops."""
    seen = {}

    def draw():
        ui.page("R")
        seen["w"] = ui.measure("link", label + "\t" + value)[2]
        seen["value"] = ui.measure("link", "\t" + value)[2]
        seen["none"] = ui.measure("link", "\t")[2]
        seen["h"] = ui.measure("link", label + "\t" + value)[3]
        seen["label"] = ui.measure("label", label)[2]       # the text and a row pad each side
        ui.at(0, 20, 100, seen["h"])
        ui.link("focus", "", {"id": "focus"})        # the focus mark's, so no row below wears it
        for i, w in enumerate(ws(seen["w"])):
            ui.at(0, 60 + i * 40, w, seen["h"])
            ui.link(label, value, {"id": "r%d" % i})
    r = _Run(draw)
    r.frame()
    r.frame()
    return r, seen


def test_a_link_rows_measure_is_the_width_that_draws_its_label_whole():
    """`measure("link", label + TAB + value)` answers the width that draws
    the label and the value whole: at that width the label's pixels are the
    ones a wider row draws; a narrower row cuts its label, an ellipsis at
    its cut."""
    label, value = "The console", "FSL-1.1-MIT"
    r, seen = _link_rows(label, value, lambda w: (w + 60, w, w - 8))
    assert seen["w"] > seen["value"] > seen["none"] > 0
    tw = ui_draw.text_width(label, 1, "ui")
    lw = (seen["label"] + tw) // 2 + 1                 # the row's pad and its label

    def cols(i, n):
        y0 = 60 + i * 40
        return [[_px(r, x, y) for x in range(n)] for y in range(y0, y0 + seen["h"])]
    assert cols(1, lw) == cols(0, lw)
    assert cols(2, lw) != cols(0, lw)
