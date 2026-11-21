"""`slot`: a rect a placed page reserves for content another owner draws (a
window's body, another process's page). The one stacking rule places it as
a fill, its rect reads back (`rect_of`), and neither engine draws in it.
And a growing button (`grow=`): a fill of its placed row, as a window's
title takes what its buttons leave."""

import uitree as ui

s = ui.state(title="Notes")
for _n in ("head", "close"):
    ui.action(_n, _n.title(), lambda: None)


def _foot(cv, x, y, w, h):
    pass


def build():
    with ui.page(""):
        with ui.row("strip", pad=2, gap=2):
            ui.button("head", label="{title}", icon="notes", kind="bar")
            ui.button("close", icon="close", alone=True, kind="bar")
        ui.slot("body")
        ui.canvas("foot", _foot, h=20)


def _placed(engine, rect, unit):
    import uitree_app
    import uitree_editor
    uitree_app.use_engine(engine)
    try:
        p = uitree_editor.page(build, None, None)
        p.unit = unit
        p.place(rect, desk=True)
        return {k: p.rect_of(k) for k in ("strip", "head", "close", "body", "foot")}
    finally:
        uitree_app.use_engine("c")


def test_a_slot_is_a_fill_of_its_stack_read_back_alike_on_both_engines():
    for rect in ((10, 20, 400, 300), (0, 0, 1024, 600), (5, 5, 120, 90)):
        for unit in (1, 2):
            c = _placed("c", rect, unit)
            assert c == _placed("reference", rect, unit), (rect, unit)
            x, y, w, h = rect
            sx, sy, sw, sh = c["body"]
            assert (sx, sw) == (x, w)
            assert sy == c["strip"][1] + c["strip"][3]          # under the strip
            assert sy + sh == c["foot"][1]                       # down to the canvas under it
            assert c["foot"][1] + c["foot"][3] == y + h          # the fill took the rest


def build_grown():
    with ui.page(""):
        with ui.row("strip", pad=2, gap=2, grow=True):
            ui.button("head", label="{title}", icon="notes", kind="bar", grow=True)
            ui.button("close", icon="close", alone=True, kind="bar")


def test_a_growing_bar_item_takes_what_the_row_leaves_alike_on_both_engines():
    import uitree_app
    import uitree_editor
    got = []
    for engine in ("c", "reference"):
        uitree_app.use_engine(engine)
        try:
            p = uitree_editor.page(build_grown, None, None)
            p.unit = 2
            p.place((0, 0, 300, 40), desk=True)
            got.append((p.rect_of("head"), p.rect_of("close")))
        finally:
            uitree_app.use_engine("c")
    assert got[0] == got[1]
    (hx, _, hw, _), (cx, _, cw, _) = got[0]
    assert hx == 4 and cx + cw == 296 and hx + hw + 4 == cx       # pad and gap 2 units of 2: the head fills
