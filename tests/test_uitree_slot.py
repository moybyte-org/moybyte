"""`slot`: a rect a placed page reserves for content another owner draws (a
window's body, another process's page). The one stacking rule places it as
a fill, its rect reads back (`rect_of`), and neither engine draws in it."""

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
