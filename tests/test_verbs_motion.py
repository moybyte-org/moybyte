"""The scroll model on the verbs engine's nests (docs/scroll_and_text_2026-10.md
§2.1, §7): a page, a `ui.scroll` region and a placed `ui.table`'s rows each
carry a `moy_ui_scroll_t`, so a touch drag let go at speed coasts and a key
step glides, on the frames' injected clock (`ui_draw.frame_dt`, the input's
`MOY_UI_IN_DT`). A frame whose input only moved the focus or a scroll owes
nothing (`settled`), a page moving on its own is `api_moving`, and with no
clock every glide lands at once -- the frames at rest are the same either
way, so no golden moves."""

import re
from pathlib import Path

import pytest

from runtime import ui, ui_draw
from runtime.host_canvas import make_system_canvas
from ws_helpers import build_ws

ROOT = Path(__file__).resolve().parent.parent
ROWS = tuple("Row %d" % i for i in range(40))
CELLS = tuple(("Cell %d" % i, str(i)) for i in range(40))
_DOWN, _UP = 1 << 3, 1 << 2
_C = ui_draw._C


@pytest.fixture
def th(tmp_path):
    return build_ws(Path(tmp_path)).theme_colors


class Page:
    """One verbs app on a 320x240 canvas: `kind` "page" (40 buttons on the
    page), "scroll" (a heading, then 40 buttons in a `ui.scroll` region) or
    "table" (a table of 40 rows placed by `ui.at`)."""

    def __init__(self, th, kind="page", dt=33):
        self.th = th
        self.kind = kind
        self.dt = dt
        self.cv = make_system_canvas(320, 240)
        self.host = ui_draw.AppHost()
        self.tapped = []
        self.cur = 0

    def frame(self, keys=0):
        ui_draw.frame_dt(self.dt)
        h = self.host
        h.inp[0] = h.inp[1] = keys
        settled = h.begin(self.cv, self.th, (0, 0, 320, 240))
        if self.kind == "table":
            ui.page("Table")
            ui.at(10, 30, 300, 180)
            self.cur, _opened = ui.table("", ("Name", "N"), CELLS, self.cur)
        elif self.kind == "stacked":
            ui.page("Stacked")
            ui.label("Above the table")
            self.cur, _opened = ui.table("", ("Name", "N"), CELLS, self.cur)
        else:
            ui.page("Rows")
            if self.kind == "scroll":
                ui.label("Above the region")
                ui.scroll("region")
            for r in ROWS:
                if ui.button(r):
                    self.tapped.append(r)
            if self.kind == "scroll":
                ui.scroll()
        ui.end()
        return settled

    def offset(self, x=160, y=150):
        return _C.api_offset(self.host.ctx, x, y)

    def press(self, x, y):
        self.host.point(x, y, True, True)
        self.frame()

    def move(self, x, y):
        self.host.point(x, y, False, True)
        self.frame()

    def release(self, x, y):
        self.host.point(x, y, False, False)
        self.frame()

    def fling(self, y0=200, dy=-30, n=4, x=160):
        self.press(x, y0)
        y = y0
        for _ in range(n):
            y += dy
            self.move(x, y)
        self.release(x, y)

    def settle(self, most=200):
        """Frames until nothing moves; how many it took."""
        for k in range(most):
            if not self.host.moving:
                return k
            self.frame()
        raise AssertionError("the page never came to rest")

    def pixels(self):
        return bytes(self.cv._buf)


def _ready(p, n=3):
    for _ in range(n):
        p.frame()
    return p


def test_a_table_opens_on_its_current_row(th):
    """A placed table's FIRST frame shows the row the app made current, at
    once: an app started again on the row it left (Notes back from a Run)
    shows that row, not the table's top."""
    p = Page(th, "table")
    p.cur = 30
    p.frame()
    at = p.offset()
    assert at > 0, "the table opened at its top, row 30 out of sight"
    assert not p.host.moving, "a jump, not a glide"
    for _ in range(3):
        p.frame()
    assert p.offset() == at and p.cur == 30, "and it stays there"
    top = Page(th, "table")
    top.frame()
    assert top.offset() == 0, "row 0 current: the top, as before"


def test_a_stacked_table_opens_its_page_on_its_current_row_in_that_frame(th):
    """On its page's first frame a stacked table's current row shows in THAT
    frame: the page moves before the rows draw, and what was drawn above at
    the top is painted over -- the frame is the one a whole redraw at that
    scroll paints, with nothing owed after it."""
    p = Page(th, "stacked")
    p.cur = 30
    p.frame()
    at = p.offset()
    assert at > 0, "the page opened at its top, row 30 out of sight"
    assert not p.host.moving and not p.host.owes(), "no next frame to finish it"
    first = p.pixels()
    p.host.whole()
    p.frame()
    assert p.offset() == at and p.cur == 30
    assert p.pixels() == first, "the moved frame is the one a whole redraw paints"


@pytest.mark.parametrize("kind", ("page", "scroll", "table"))
def test_a_fling_coasts_every_nest_and_comes_to_rest(th, kind):
    p = _ready(Page(th, kind, dt=16))
    p.fling()
    at = p.offset()
    assert at > 0, "the drag scrolled the %s" % kind
    assert p.host.moving, "the release flung it"
    assert not p.tapped, "a drag taps nothing"
    seen = [at]
    while p.host.moving and len(seen) < 300:
        p.frame()
        seen.append(p.offset())
    assert not p.host.moving
    assert seen[-1] > at, "it coasted on past where the finger let go"
    assert seen == sorted(seen), "a coast moves one way"
    assert len(seen) > 5, "a coast takes frames, not a jump"
    rest = seen[-1]
    for _ in range(5):
        p.frame()
    assert p.offset() == rest, "at rest it stays"
    assert p.host.still()


def test_a_coast_stops_dead_at_the_clamp(th):
    p = _ready(Page(th, dt=16))
    for _ in range(3):
        p.fling(dy=-60)
        while p.host.moving:
            last = p.offset()
            p.frame()
            if p.offset() == last and p.host.moving:
                raise AssertionError("a coast that does not move must have stopped")
    end = p.offset()
    p.fling(dy=-60)
    assert p.offset() == end and not p.host.moving, "at the end, a fling stops dead"


def test_a_press_catches_a_coast_and_taps_nothing(th):
    p = _ready(Page(th, dt=16))
    p.fling()
    p.frame()
    assert p.host.moving
    p.press(160, 120)
    assert not p.host.moving, "the press caught it"
    at = p.offset()
    p.release(160, 120)
    p.frame()
    assert p.offset() == at
    assert not p.tapped, "the press that caught a coast is no tap"


def test_with_no_clock_a_release_does_not_fling(th):
    p = _ready(Page(th, dt=0))
    p.fling()
    assert not p.host.moving


def _walk(p, n):
    """Press down n times, a frame each; the offsets drawn."""
    out = []
    for _ in range(n):
        p.frame(_DOWN)
        out.append(p.offset())
    return out


@pytest.mark.parametrize("kind", ("page", "scroll", "table"))
def test_a_key_step_glides_and_rests_where_no_clock_lands(th, kind):
    """A step past the view's foot: on the clock the scroll eases there over
    a few frames; with none it is there at once; at rest the two pages are
    the same pixels."""
    a = _ready(Page(th, kind, dt=33))
    b = _ready(Page(th, kind, dt=0))
    steps = 0
    while b.offset() <= 0 and steps < 30:
        _walk(b, 1)
        steps += 1
    target = b.offset()
    assert target > 0, "the walk went past the view"
    _walk(a, steps - 1)
    a.settle()
    before = a.offset()
    a.frame(_DOWN)
    assert a.host.moving, "the step glides"
    first = a.offset()
    frames = a.settle()
    assert a.offset() == target, "the glide lands where no clock lands at once"
    assert before < first < target or first == target
    assert 2 <= frames + 1 <= 12, "a row's glide takes a few frames: %d" % (frames + 1)
    a.frame()
    b.frame()
    assert a.pixels() == b.pixels(), "at rest, the frames are the same pixels"


def test_a_held_key_reads_as_one_movement(th):
    """A step each frame re-aims the glide from where the view is: the
    offset only ever moves on, never back, and the view lags its target by
    one glide, never more."""
    p = _ready(Page(th, dt=16))
    seen = _walk(p, 30)
    p.settle()
    seen.append(p.offset())
    assert seen == sorted(seen), seen
    assert seen[-1] > 0


def test_a_key_step_owes_no_second_paint(th):
    """The focus move and the reveal apply at begin, so the press frame is
    settled and, once the glide rests, nothing is due (§7: one paint a
    press). With no clock the reveal lands in the press frame itself."""
    p = _ready(Page(th, dt=0))
    for _ in range(12):
        assert p.frame(_DOWN), "a key step owes nothing after it"
        assert _C.api_moved(p.host.ctx), "the focus moved"
        assert p.host.still(), "nothing is due after the press frame"


def test_a_list_row_step_reveals_at_begin(th):
    """An unplaced list's row step reveals its row at begin (the row's box
    is last frame's at the next index), so the step owes no frame."""
    cv = make_system_canvas(320, 240)
    host = ui_draw.AppHost()
    items = tuple("Item %d" % i for i in range(30))
    cur = [0]

    def frame(keys=0):
        host.inp[0] = host.inp[1] = keys
        settled = host.begin(cv, th, (0, 0, 320, 240))
        ui.page("List")
        ui.label("Above")
        cur[0] = ui.list("Items", items, cur[0])
        ui.end()
        return settled

    for _ in range(3):
        frame()
    for _ in range(12):
        assert frame(_DOWN)
        assert not _C.api_owes(host.ctx), "the step's reveal was begin's"
    assert _C.api_offset(host.ctx, 160, 150) > 0, "the page scrolled with the row"


def test_the_ease_lives_in_one_place():
    """The glide's arithmetic is native/moy_ui_interact.c's: no Python
    surface keeps its own half-life or ease-out (§2.1's grep guard)."""
    pat = re.compile(r"GLIDE_HALF|_SLIDE_MS|0\.5 \*\* \(|\) \*\* 3\b")
    bad = []
    for path in sorted((ROOT / "runtime").glob("*.py")):
        for n, line in enumerate(path.read_text().splitlines(), 1):
            if pat.search(line):
                bad.append("%s:%d: %s" % (path.name, n, line.strip()))
    assert not bad, "\n".join(bad)



def test_a_walk_to_the_end_over_a_hint_band_comes_to_rest(tmp_path):
    """Settings' page has a hint band at its foot, and its stop's box is
    what shows above the band: the begin's glide and the pass agree on the
    end, so a key walk there lands and the page stops moving (it once
    aimed past the end by the band's height and glided on every frame)."""
    from runtime import host_app
    was = ui_draw.input_kind()
    ui_draw.input_kind("tdeck")
    try:
        ws = build_ws(Path(tmp_path))
        drv = host_app.ConsoleDriver(ws)
        ws.open_settings()
        for _ in range(5):
            drv.frame(1 / 30)
        h = ws.settings_layer._input_host()
        for _ in range(30):
            drv.hold("down", True)
            drv.frame(1 / 30)
            drv.hold("down", False)
            drv.frame(1 / 30)
        for _ in range(40):
            drv.frame(1 / 30)
        assert _C.api_offset(h.ctx, 160, 150) > 0
        assert not _C.api_moving(h.ctx), "the page came to rest at its end"
        drawn = ws._frames_drawn
        for _ in range(10):
            drv.frame(1 / 30)
        assert ws._frames_drawn == drawn, "and paints nothing more"
    finally:
        ui_draw.input_kind(was)
