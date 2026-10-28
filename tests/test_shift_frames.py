"""Shift frames in the verbs engine (docs/scroll_and_text_2026-10.md §3.3,
§13): a frame whose change is one page's scroll plus rows the engine knows
of moves the last frame's pixels of that page (a canvas's `shift_last`) and
draws only its floor -- the band the move exposed, the rows whose marks
moved, the fades -- and every presented frame is byte for byte the frame a
whole repaint draws.

Each case plays one script on two contexts: one whose frames may shift, on
one, two and three buffers presented in turn (the host compositor's `last`,
as a board's rotation), and one drawn whole every frame on one buffer; every
presented frame of the first is held to the second's bytes, and the share
of frames that shifted is held above a floor, so the mechanism cannot
quietly stop engaging (L8's silent-disable direction). The engine's record
audit runs throughout: a shift frame whose stops differ from the last
frame's outside the floor fails it (L8's correctness direction)."""

from array import array
from pathlib import Path

import pytest

from runtime import ui, ui_draw
from runtime.host_canvas import make_system_canvas
from ws_helpers import build_ws

_C = ui_draw._C
_DOWN, _UP = 1 << 3, 1 << 2
W, H = 320, 240


@pytest.fixture
def th(tmp_path):
    return build_ws(Path(tmp_path)).theme_colors


@pytest.fixture(autouse=True)
def audit():
    _C.api_audit(1)
    yield
    failed = _C.api_audit(0)
    assert failed == 0, ("%d shift frames drew stops unlike the last frame's outside the floor, "
                         "or a stop's reach into R outside it" % failed)


class App:
    """A verbs app the cases script: `kind` "page" (groups of rows on the
    page), "scroll" (a heading, then the rows in a placed `ui.scroll`),
    "grid" (cards in a placed `ui.scroll`, each picture the app's) or
    "band" (the grid, skipping the cards outside `ui.band` with
    `ui.keep`). `gen` is the page's and the pictures' generation (None:
    none given, so nothing may shift)."""

    def __init__(self, th, kind="page", rows=40):
        self.th = th
        self.kind = kind
        self.rows = ["Row %d" % i for i in range(rows)]
        self.gen = 1
        self.pic_gen = {}
        self.toggles = {}
        self.toast = False
        self.pop = False
        self.focus_to = None
        self.geo = None                 # the grid's (columns, first top, pitch, card height)
        self.drawn = {}                 # per canvas: the picture gen each card last drew

    def learn(self, host):
        """The grid's geometry in the region's coordinates, from where the
        last frame (at scroll 0) drew its cards: what an app that skips by
        `ui.band` lays its cards out by."""
        b0 = host.box(self.rows[0])
        cols = 1
        while host.box(self.rows[cols])[1] == b0[1]:
            cols += 1
        pitch = host.box(self.rows[cols])[1] - b0[1]
        self.geo = (cols, b0[1] - 40, pitch, b0[3])

    def draw(self, cv):
        g = self.gen
        page_o = None if g is None else {"gen": g}
        if self.kind == "page":
            ui.page("Rows", page_o)
            ui.hints(("confirm", "Open", "back", "Back"))
            for i, r in enumerate(self.rows):
                if i % 8 == 0:
                    ui.group("Group %d" % (i // 8))
                if i % 5 == 3:
                    self.toggles[r] = ui.toggle(r, self.toggles.get(r, False))
                elif i % 5 == 4:
                    ui.info(r, str(i * 7))
                else:
                    ui.link(r, "v%d" % i)
            ui.group()
        else:
            ui.page("")
            if self.focus_to is not None:
                ui.focus(self.focus_to)
            ui.label("Above the region")
            ui.at(8, 40, W - 16, H - 60)
            ui.scroll("region", page_o)
            if self.kind == "scroll":
                for r in self.rows:
                    ui.button(r)
            else:
                ui.grid("cards", 56, 42)
                band = ui.band() if self.kind == "band" else None
                for i, r in enumerate(self.rows):
                    drawn = self.drawn.setdefault(id(cv), {})
                    if (band is not None and self.geo is not None
                            and drawn.get(r) == self.pic_gen.get(r, 0)):
                        cols, top0, pitch, ch = self.geo
                        top = top0 + (i // cols) * pitch
                        if top + ch <= band[1] or top >= band[1] + band[3]:
                            ui.keep(r)      # outside the band, its picture as drawn
                            continue
                    o = {"id": r}
                    pg = self.pic_gen.get(r, 0) if g is not None else None
                    if pg is not None:
                        o["gen"] = pg
                    x, y, w, h, _st = ui.card(r, "c%d" % i, o)
                    if w:
                        drawn[r] = self.pic_gen.get(r, 0)
                        k = ui.card_clip(array("i", (8, 40, W - 16, H - 60)))
                        cv.clip(k[0], k[1], k[2], k[3])
                        cv.rect(x, y, w, h, 8 + (i + self.pic_gen.get(r, 0)) % 8)
                        cv.rect(x + 3, y + 3, w // 2, h // 3, 1 + i % 5)
                        cv.clip()
                ui.grid()
            ui.scroll()
            if self.pop:
                ui.popover("pick", 120)
                ui.button("One")
                ui.button("Two")
                ui.popover()
            if self.toast:
                ui.toast("Saved")
        ui.end()


class Rig:
    """`app` drawn twice a frame: on `bufs` buffers presented in turn, its
    frames free to shift, and on one buffer, whole; `frame` holds the first's
    presented picture to the second's."""

    def __init__(self, th, kind="page", bufs=1, dt=16, rows=40):
        self.th = th
        self.app = App(th, kind, rows)
        self.bufs = bufs
        self.dt = dt
        self.cv = make_system_canvas(W, H)
        self.cv.RETAINED_FRAMES = bufs
        self.ref = make_system_canvas(W, H)
        self.a = ui_draw.AppHost()
        self.b = ui_draw.AppHost()
        self.n = 0
        self.held = [bytes(self.cv._buf)]
        self.kinds = []
        self.wide = 0

    def frame(self, keys=0, skip=False, gap=False):
        """One frame of both; `gap`: another surface painted the frame
        before (its number skipped), `skip`: the frame is not painted at
        all (the gate closed)."""
        if skip:
            return
        if gap:
            self.n += 1
        ui_draw.frame_dt(self.dt)
        for host, cv, alone in ((self.a, self.cv, True), (self.b, self.ref, False)):
            ui_draw.paint_frame(self.n, alone)
            host.inp[0] = host.inp[1] = keys
            host.begin(cv, self.th, (0, 0, W, H))
            self.app.draw(cv)
            if alone:
                self.kinds.append(ui_draw.change_take())
                if _C.api_shift_drawn(host.ctx) & 8:
                    self.wide += 1          # a stop unlike the last frame's: R from it down
        self.app.focus_to = None
        self.n += 1
        shot = bytes(self.cv._buf)
        want = bytes(self.ref._buf)
        assert shot == want, "frame %d (%s, %d buffers) differs from a whole repaint at %s" % (
            self.n, self.kinds[-1], self.bufs, _first_diff(shot, want))
        self.held.append(shot)
        if self.bufs > 1:
            self.cv._comp.last = shot
            self.cv._buf[:] = self.held[-self.bufs] if len(self.held) > self.bufs else self.held[0]

    def point(self, x, y, click, down):
        self.a.point(x, y, click, down)
        self.b.point(x, y, click, down)

    def drag(self, y0, dy, n, x=160, release=True):
        self.point(x, y0, True, True)
        self.frame()
        y = y0
        for _ in range(n):
            y += dy
            self.point(x, y, False, True)
            self.frame()
        if release:
            self.point(x, y, False, False)
            self.frame()

    def coast(self, most=300):
        for _ in range(most):
            if not self.a.moving:
                return
            self.frame()
        raise AssertionError("never came to rest")

    def shifted(self):
        return sum(1 for k in self.kinds if k == "shift")

    def share(self, kind="shift"):
        return sum(1 for k in self.kinds if k == kind) / max(1, len(self.kinds))


def _first_diff(a, b):
    for i in range(0, min(len(a), len(b)), 2):
        if a[i:i + 2] != b[i:i + 2]:
            p = i // 2
            return "(%d, %d)" % (p % W, p // W)
    return "length"


def _ready(r, n=3):
    for _ in range(n):
        r.frame()
    if r.app.kind == "band":
        r.app.learn(r.a)
    return r


BUFS = (1, 2, 3)


@pytest.mark.parametrize("bufs", BUFS)
@pytest.mark.parametrize("kind", ("page", "scroll", "grid", "band"))
def test_a_drag_and_its_fling_shift_byte_for_byte(th, kind, bufs):
    r = _ready(Rig(th, kind, bufs))
    r.drag(200, -14, 6)
    r.coast()
    r.drag(60, 9, 5)                      # and back up
    r.coast()
    assert r.share() > 0.6, "the drag and the coast shifted %.2f of the frames" % r.share()
    assert r.wide == 0, "a plain gesture widened the floor to R's foot"


@pytest.mark.parametrize("bufs", BUFS)
@pytest.mark.parametrize("kind", ("page", "scroll"))
def test_key_steps_move_the_focus_rows_and_glide_byte_for_byte(th, kind, bufs):
    r = _ready(Rig(th, kind, bufs, dt=33))
    for _ in range(14):
        r.frame(_DOWN)
        r.frame()
    for _ in range(6):
        r.frame(_UP)
    r.coast()
    moved = sum(1 for k in r.kinds if k is not None)
    assert moved > len(r.kinds) * 0.6, "steps and glides drew rows: %d of %d" % (moved, len(r.kinds))
    assert r.wide == 0, "a key step widened the floor to R's foot"


@pytest.mark.parametrize("bufs", BUFS)
def test_a_held_key_glides_one_movement(th, bufs):
    r = _ready(Rig(th, "page", bufs, dt=33))
    for _ in range(25):
        r.frame(_DOWN)
    r.coast()
    assert r.shifted() > 5


@pytest.mark.parametrize("bufs", BUFS)
@pytest.mark.parametrize("dy", (-1, -3, -50, -199, 1, 7, 120))
def test_every_move_from_a_pixel_to_the_view(th, bufs, dy):
    r = _ready(Rig(th, "page", bufs))
    if dy > 0:
        r.drag(200, -40, 6, release=False)
        r.point(160, 0, False, False)
        r.frame()
        r.coast()
    y0 = 120 if abs(dy) < 100 else (230 if dy < 0 else 10)
    r.drag(y0, dy, 3)
    r.coast()


@pytest.mark.parametrize("bufs", BUFS)
def test_the_clamp_at_either_end(th, bufs):
    r = _ready(Rig(th, "page", bufs))
    for _ in range(4):
        r.drag(220, -60, 3)
        r.coast()
    for _ in range(4):
        r.drag(30, 60, 3)
        r.coast()


@pytest.mark.parametrize("bufs", BUFS)
def test_a_frame_that_is_a_shift_and_a_content_change(th, bufs):
    """The page's gen moves mid-fling: its region draws whole that frame,
    the next ones shift again."""
    r = _ready(Rig(th, "page", bufs))
    r.drag(200, -14, 4)
    r.frame()
    r.app.rows[22] = "Row 22, renamed"
    r.app.gen += 1
    r.frame()
    r.coast()
    r.app.rows.insert(30, "A new row")    # the gen says so: the next frame is whole
    r.app.gen += 1
    r.drag(200, -14, 4)
    r.coast()
    assert r.shifted() > 4


@pytest.mark.parametrize("bufs", BUFS)
@pytest.mark.parametrize("kind", ("grid", "band"))
def test_a_cover_landing_mid_fling_draws_its_card(th, kind, bufs):
    r = _ready(Rig(th, kind, bufs))
    r.drag(200, -12, 4)
    for i in range(6):
        r.app.pic_gen["Row %d" % (4 + 3 * i)] = 1
        r.frame()
    r.coast()
    assert r.shifted() > 4


@pytest.mark.parametrize("bufs", BUFS)
def test_an_overlay_mid_gesture_paints_whole_and_matches(th, bufs):
    r = _ready(Rig(th, "scroll", bufs))
    r.drag(200, -12, 3, release=False)
    r.app.toast = True
    r.drag(150, -10, 3)
    r.app.toast = False
    r.frame()
    r.coast()
    r.app.pop = True
    r.frame()
    r.frame()
    r.app.pop = False
    r.frame()
    r.drag(200, -12, 4)
    r.coast()


@pytest.mark.parametrize("bufs", BUFS)
def test_a_theme_change_mid_coast(th, tmp_path, bufs):
    r = _ready(Rig(th, "page", bufs))
    r.drag(200, -16, 4)
    r.frame()
    other = dict(th)
    other["panel"] = th.get("ink", 0)
    r.th = other
    r.frame()
    r.th = th
    ui_draw.look_moved()
    r.frame()
    r.coast()


@pytest.mark.parametrize("bufs", BUFS)
def test_a_gap_or_a_dropped_frame(th, bufs):
    """A frame the gate skips leaves both buffers as they were (the shifts
    go on); a frame another surface painted breaks the chain (whole)."""
    r = _ready(Rig(th, "page", bufs))
    r.drag(200, -12, 3, release=False)
    r.frame(skip=True)
    r.drag(160, -12, 3)
    r.frame(gap=True)
    r.coast()


def test_no_gen_nothing_shifts(th):
    r = _ready(Rig(th, "page", 2))
    r.app.gen = None
    r.drag(200, -14, 6)
    r.coast()
    assert r.shifted() == 0


def test_an_app_focus_move_before_the_region(th):
    r = _ready(Rig(th, "scroll", 2, dt=33))
    r.drag(200, -14, 4)
    r.app.focus_to = "Row 9"
    r.frame()
    r.coast()


@pytest.mark.parametrize("bufs", BUFS)
@pytest.mark.parametrize("change", ("tail", "middle"))
def test_stops_that_change_with_no_gen_widen_the_floor(th, bufs, change):
    """A stop unlike the last frame's -- a row gone from R's tail, a row
    come in its middle -- with the gen not moved: the floor takes R from
    that stop down (the tail's ground filled), never a second pass."""
    r = _ready(Rig(th, "scroll", bufs))
    r.drag(200, -14, 4)
    if change == "tail":
        del r.app.rows[12:]
    else:
        r.app.rows.insert(8, "Row 7b")
    r.frame()
    r.coast()


@pytest.mark.parametrize("what", ("at", "part"))
def test_a_placed_stop_or_a_part_with_no_gen_never_shifts(th, monkeypatch, what):
    r = _ready(Rig(th, "scroll", 2))
    real = ui.button

    def button(label, *a, **k):
        if label == "Row 5":
            if what == "at":
                ui.at(10, 60, 100, 20)
                return real(label, *a, **k)
            ui.part(label, 0, 20)
            return False
        return real(label, *a, **k)
    monkeypatch.setattr(ui, "button", button)
    r.app.gen += 1                      # the app changed what it draws: it says so
    for _ in range(2):
        r.frame()
    del r.kinds[:]
    r.drag(200, -14, 4)
    r.coast()
    assert r.shifted() == 0, "a %s in the region: whole frames" % what


def _deferred(cv):
    """The canvas's shift lands at its fence, as the P4's 2D-DMA may: a
    pass that drew a row before the fence would have it overwritten."""
    real = cv.shift_last
    queued = []

    def shift_last(*a):
        queued.append(a)
        return True

    def shift_fence():
        while queued:
            real(*queued.pop(0))
    cv.shift_last = shift_last
    cv.shift_fence = shift_fence


@pytest.mark.parametrize("bufs", BUFS)
@pytest.mark.parametrize("kind", ("page", "grid"))
def test_a_copy_landing_at_its_fence_draws_the_same_frames(th, kind, bufs):
    """Two emulated engines -- the copy landing at its submit (the S3s',
    one buffer's) and at the fence -- draw the same frames: the floor is
    drawn after the fence."""
    r = Rig(th, kind, bufs)
    _deferred(r.cv)
    _ready(r)
    r.drag(200, -14, 6)
    r.coast()
    for _ in range(6):
        r.frame(_DOWN)
    r.coast()
    assert r.shifted() > 5


def test_one_ring_body():
    """The painted-frame rings are `ui_draw.PaintRing` (docs/scroll_and_text_
    2026-10.md §3.1), one class: no other list of painted frames in the
    shell or the toolkit. A full-screen app's page, a ScrollRegion (the
    home's band among them) and the desk home's still frames (§14.4) each
    keep one; the windowed WM's `_shown` is the windows' ring."""
    import re
    root = Path(__file__).resolve().parent.parent
    src = {p.name: p.read_text(encoding="utf-8") for p in (root / "runtime").glob("*.py")}
    assert sum(s.count("class PaintRing") for s in src.values()) == 1
    found = []
    for name, text in sorted(src.items()):
        for m in re.finditer(r"self\.(_\w*(?:painted|ring)\w*) = \[\]", text):
            found.append("%s: %s" % (name, m.group(1)))
    assert not found, found
    assert isinstance(ui_draw.ScrollRegion().ring, ui_draw.PaintRing)
    assert isinstance(ui_draw.AppHost().ring, ui_draw.PaintRing)
    assert "self._still_ring = _ui_draw.PaintRing()" in src["launcher_layer.py"]


def test_the_band_is_the_floor_in_the_pages_coordinates(th, monkeypatch):
    """`ui.band()`: on a whole frame what shows of the page, on a shift
    frame the floor's bounding rect -- the band the move uncovered and the
    region's edges within it -- in the page's coordinates (y from its rows'
    top); the floor itself a small part of the view."""
    r = _ready(Rig(th, "scroll", 1))
    r.app.focus_to = "Row 3"               # a mark off the view's edges
    r.frame()
    r.frame()
    seen = []
    floors = []
    real = ui.scroll

    def scroll(id=None, o=None):
        real(id, o)
        if id and ui._T[1] is r.cv:
            seen.append((ui.band(), ui_draw._C.api_offset(r.a.ctx, 160, 100)))
            rects = _C.api_floor(r.a.ctx)
            floors.append(rects or ())
            if rects:                      # the band is the floor's bounding rect
                y0 = min(f[1] for f in rects) - 40 + seen[-1][1]
                y1 = max(f[1] + f[3] for f in rects) - 40 + seen[-1][1]
                assert (seen[-1][0][1], seen[-1][0][3]) == (y0, y1 - y0), (seen[-1], rects)
    monkeypatch.setattr(ui, "scroll", scroll)
    r.frame()
    assert seen[-1][0][3] == 0, "a frame that changes nothing in the page draws none of it"
    r.frame(gap=True)                      # whole: another surface painted the frame before
    whole = seen[-1][0]
    assert whole[1] == 0 and whole[3] == H - 60, "a whole frame's band is the view: %r" % (whole,)
    r.drag(200, -14, 3)
    assert r.kinds[-2] == "shift"          # the last move's frame
    band, at = seen[-2]
    assert band[1] + band[3] >= at + H - 60 - 1, "it reaches the view's foot, which the move uncovered"
    assert sum(f[3] for f in floors[-2]) < (H - 60) // 2, "the floor is the most of the view: %r" % (
        floors[-2],)
