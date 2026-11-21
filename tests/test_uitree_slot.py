"""`slot`: a rect a placed page reserves for content another owner draws (a
window's body, another process's page). The one stacking rule places it as
a fill, its rect reads back (`rect_of`), and the engine draws nothing in it.
And a growing button (`grow=`): a fill of its placed row, as a window's
title takes what its buttons leave."""

import pytest

import uitree as ui
import uitree_reference as R

s = ui.state(title="Notes")
for _n in ("head", "close"):
    ui.action(_n, _n.title(), lambda: None)


@pytest.fixture(autouse=True, scope="module")
def _console(tmp_path_factory):
    """A console built first: the theme and icon set its pages measure by
    are a booted console's, whatever ran before in this process."""
    from runtime import host_app
    host_app.build_workstation(str(tmp_path_factory.mktemp("console")), sys_size=(320, 240), font_scale=1)


def _foot(cv, x, y, w, h):
    pass


def build():
    with ui.page(""):
        with ui.row("strip", pad=2, gap=2):
            ui.button("head", label="{title}", icon="notes", kind="bar")
            ui.button("close", icon="close", alone=True, kind="bar")
        ui.slot("body")
        ui.canvas("foot", _foot, h=20)


def _placed(rect, unit):
    import uitree_editor
    p = uitree_editor.page(build, None, None)
    p.unit = unit
    p.place(rect, desk=True)
    return {k: p.rect_of(k) for k in ("strip", "head", "close", "body", "foot")}


def test_a_slot_is_a_fill_of_its_stack_read_back_as_the_reference_did():
    for rect in ((10, 20, 400, 300), (0, 0, 1024, 600), (5, 5, 120, 90)):
        for unit in (1, 2):
            c = _placed(rect, unit)
            R.held(__file__, "slot/%r/%d" % (rect, unit), c)
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


def test_a_growing_bar_item_takes_what_the_row_leaves_as_the_reference_did():
    import uitree_editor
    p = uitree_editor.page(build_grown, None, None)
    p.unit = 2
    p.place((0, 0, 300, 40), desk=True)
    got = (p.rect_of("head"), p.rect_of("close"))
    R.held(__file__, "grown", got)
    (hx, _, hw, _), (cx, _, cw, _) = got
    assert hx == 4 and cx + cw == 296 and hx + hw + 4 == cx       # pad and gap 2 units of 2: the head fills
