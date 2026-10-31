"""A table that keeps its parts across frames (`o.keep`, `ui.table_floor`)
and the parts a page names per buffer (`ui.held`): docs/ui_api_2026-10.md
§2, A table of parts. The engine keeps a record of each paint a context
begins (moy_ui_api.c's kept ring), and a frame draws only what the buffer it
paints lacks of the table: the parts whose cells, gen or marks changed since
that buffer's paint and what was drawn over that paint, or the view whole
where the buffer is unknown, the scroll moved or the key changed."""

from array import array
from pathlib import Path

import pytest

from runtime import ui, ui_draw
from runtime.host_canvas import make_system_canvas
from ws_helpers import build_ws

_ROWS = [["a"], ["b"], ["c"], ["d"], ["e"], ["f"]]
_VIEW = (10, 20, 300, 60)               # six parts 50 wide side by side


@pytest.fixture
def th(tmp_path):
    return build_ws(Path(tmp_path)).theme_colors


class Rig:
    """A kept row of parts on an app host over a canvas presenting `bufs`
    buffers in turn, one painted frame a call."""

    def __init__(self, th, bufs, axis="x"):
        self.th = th
        self.cv = make_system_canvas(320, 240)
        self.cv.RETAINED_FRAMES = bufs
        self.host = ui_draw.AppHost()
        self.n = 100
        self.axis = axis
        self.r = array("i", [0] * 4)

    def frame(self, rows=_ROWS, cur=0, key=7, offset=0, over=None, whole=False, gen=0,
              gap=False, named=()):
        """One painted frame: (the floor's rects, its end, the parts
        answered, what `held` answered for each of `named`)."""
        self.n += 2 if gap else 1
        ui_draw.paint_frame(self.n, True)
        self.host.whole()
        self.host.begin(self.cv, self.th, None, False, False, True)
        held = [ui.held(k, v) for k, v in named]
        ui.at(*_VIEW)
        part = [50, 0] if self.axis == "x" else [0, 10]
        ui.table("", ("",), rows, cur, {"id": "t", "part": part, "axis": self.axis,
                                         "offset": offset, "keep": key, "spill": 4,
                                         "gen": gen, "current": False, "mark": False,
                                         "over": over, "whole": whole})
        floor = []
        v = ui.table_floor(self.r)
        while v > 0:
            floor.append(tuple(self.r))
            v = ui.table_floor(self.r)
        parts = []
        i = ui.table_part(self.r)
        while i >= 0:
            parts.append(i)
            i = ui.table_part(self.r)
        ui.end()
        return floor, v, parts, held


def _settle(rig, n, **kw):
    for _ in range(n):
        out = rig.frame(**kw)
    return out


@pytest.mark.parametrize("bufs", [1, 2, 3])
def test_a_kept_table_draws_whole_then_nothing_once_every_buffer_holds_it(th, bufs):
    rig = Rig(th, bufs)
    floor, end, parts, _h = rig.frame()
    assert floor == [_VIEW] and parts == [0, 1, 2, 3, 4, 5]
    for k in range(1, bufs):                # the buffers not yet painted
        floor, end, parts, _h = rig.frame()
        assert floor == [_VIEW] and end == (-1 if k < bufs - 1 else 0)
    floor, end, parts, _h = rig.frame()
    assert floor == [] and parts == [] and end == 0


@pytest.mark.parametrize("bufs", [1, 2, 3])
def test_a_changed_row_draws_in_each_buffer_its_reach_alone(th, bufs):
    rig = Rig(th, bufs)
    _settle(rig, bufs + 1)
    rows = [r[:] for r in _ROWS]
    rows[3] = ["D"]
    for k in range(bufs):
        floor, end, parts, _h = rig.frame(rows=rows)
        # row 3's cell, 4 px of spill past it on either side, the view's height
        assert floor == [(10 + 150 - 4, 20, 58, 60)], (k, floor)
        assert parts == [2, 3, 4]               # the neighbours its reach meets
        assert end == (0 if k == bufs - 1 else -1)
    assert rig.frame(rows=rows)[0] == []


def test_the_current_row_moving_draws_both_rows(th):
    rig = Rig(th, 1)
    _settle(rig, 2)
    floor, _e, parts, _h = rig.frame(cur=4)
    assert floor == [(10, 20, 54, 60), (10 + 200 - 4, 20, 58, 60)]
    assert parts == [0, 1, 3, 4, 5]
    floor, _e, parts, _h = rig.frame(cur=5)    # neighbours: one rect, to the view's edge
    assert floor == [(10 + 200 - 4, 20, 104, 60)]
    assert rig.frame(cur=5, gen=3)[0] == [(10 + 250 - 4, 20, 54, 60)]   # its gen


def test_scroll_key_gap_and_whole_draw_the_view_whole(th):
    rig = Rig(th, 2)
    _settle(rig, 3)
    assert rig.frame(offset=5)[0] == [_VIEW]
    _settle(rig, 3, offset=5)
    assert rig.frame(offset=5, key=8)[0] == [_VIEW]
    _settle(rig, 3)
    assert rig.frame(gap=True)[0] == [_VIEW]
    _settle(rig, 3)
    assert rig.frame(whole=True)[0] == [_VIEW]
    _settle(rig, 3)
    assert rig.frame(key=-1)[0] == [_VIEW]      # keeps nothing...
    assert rig.frame()[0] == []                 # (the other buffer holds the table)
    assert rig.frame()[0] == [_VIEW]            # ...so the buffer it painted is unknown


@pytest.mark.parametrize("bufs", [1, 2, 3])
def test_what_was_drawn_over_the_table_draws_in_the_buffer_that_holds_it(th, bufs):
    rig = Rig(th, bufs)
    _settle(rig, bufs + 1)
    stamp = (100, 30, 8, 13)
    floor, end, _p, _h = rig.frame(over=stamp)
    assert floor == [] and end == -2           # no frame of the table's brings it back
    for _ in range(bufs - 1):
        assert rig.frame()[0] == []
    floor, end, _p, _h = rig.frame()           # the buffer the stamp lies in
    assert floor == [(100, 20, 8, 60)] and end == 0


def test_a_vertical_kept_table(th):
    rig = Rig(th, 1, axis="y")
    _settle(rig, 2)
    rows = [r[:] for r in _ROWS]
    rows[1] = ["B"]
    floor, _e, parts, _h = rig.frame(rows=rows)
    assert floor == [(10, 20 + 10 - 4, 300, 18)] and parts == [0, 1, 2]


@pytest.mark.parametrize("bufs", [1, 2, 3])
def test_held_answers_what_the_buffer_holds(th, bufs):
    rig = Rig(th, bufs)
    for _ in range(bufs):
        assert rig.frame(named=(("hint", 1),))[3] == [False]
    assert rig.frame(named=(("hint", 1),))[3] == [True]
    assert rig.frame(named=(("hint", 2),))[3] == [False]
    for _ in range(bufs - 1):
        assert rig.frame(named=(("hint", 2),))[3] == [False]
    assert rig.frame(named=(("hint", 2),))[3] == [True]
    assert rig.frame(named=(("hint", 2),), gap=True)[3] == [False]
    # a part named in a frame that kept nothing is not held
    _settle(rig, bufs + 1, named=(("hint", 2),))
    rig.frame(named=(("hint", 2),), key=-1)
    for _ in range(bufs - 1):
        rig.frame(named=(("hint", 2),))
    assert rig.frame(named=(("hint", 2),))[3] == [False]


def test_a_part_lagging_is_in_the_floors_end(th):
    """The floor's end counts the parts `held` named before it: a part a
    buffer holds under another key lags as a row would."""
    rig = Rig(th, 2)
    _settle(rig, 3, named=(("hint", 1),))
    assert rig.frame(named=(("hint", 1),))[1] == 0
    assert rig.frame(named=(("hint", 2),))[1] == -1
    assert rig.frame(named=(("hint", 2),))[1] == 0


def test_a_table_that_keeps_nothing_answers_its_view(th):
    cv = make_system_canvas(320, 240)
    host = ui_draw.AppHost()
    r = array("i", [0] * 4)
    ui_draw.paint_frame(1, True)
    host.begin(cv, th, None, False, False, True)
    ui.at(*_VIEW)
    ui.table("", ("",), _ROWS, 0, {"id": "t", "part": [50, 0], "axis": "x", "offset": 0})
    assert ui.table_floor(r) == 1 and tuple(r) == _VIEW
    assert ui.table_floor(r) == 0
    ui.end()
