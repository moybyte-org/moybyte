"""Notes' frames on its text view (docs/scroll_and_text_2026-10.md §5): a
key typed into a note, a caret step, a selection grown and the frames a
change owes after it draw the rows of the note that changed, never the view
whole.

A frame whose view moved -- a drag, a fling, the wheel, the trackball, a
held key, the caret leaving the view -- moves the rows the frame before drew
(the engine's shift frame, R the view) and draws the rows it brings and the
rows that changed.

Each case runs one session on two consoles: one as it ships and one whose
run's host never plans a frame, so every frame draws whole; every presented
picture of the first is held to the second's bytes, on one, two and three
buffers presented in turn (the full-screen console) and on the desk. The
share of painted frames that drew rows alone is held above a floor (L8's
silent-disable direction), and the engine's record audit fails a rows frame
whose stops moved outside its floor.
"""

from array import array

import pytest

from runtime import host_app, moy_carts, ui_draw
from ws_helpers import no_unlock_banner, quiesce, select_on_home

_DT = 1 / 30
_C = ui_draw._C
_NOW = [100000]
_BODY = "\n".join(
    ["# Plans", "", "- [ ] buy milk", "- [x] write the [[other note]]"]
    + ["Line %d of the note, with words enough to wrap past the edge of the paper" % i
       for i in range(36)])


@pytest.fixture(autouse=True)
def clock(monkeypatch):
    """One clock for both consoles, a frame's worth a frame: the idle save
    lands on the same frame of each."""
    import sys
    from runtime import editor_handle, ticks  # noqa: F401 -- loaded, then patched
    for mod in list(sys.modules.values()):
        f = getattr(mod, "__file__", None) or ""
        if "/runtime/" in f.replace("\\", "/") and hasattr(mod, "_ticks_ms"):
            monkeypatch.setattr(mod, "_ticks_ms", lambda: _NOW[0])
    yield


@pytest.fixture(autouse=True)
def audit():
    _C.api_audit(1)
    yield
    failed = _C.api_audit(0)
    assert failed == 0, "%d rows frames drew stops unlike the last frame's outside the floor" % failed


def _console(tmp_path, name, ships, desk):
    root = tmp_path / name
    kw = {"sys_size": (1024, 600), "font_scale": 2, "windowed": True} if desk else {}
    ws = host_app.build_workstation(str(root / "carts"), **kw)
    ws.bar_layer._clock_text = lambda: "10:30"
    ws.show_fps = False
    no_unlock_banner(ws)
    quiesce(ws)
    ws.pointer.visible = False
    moy_carts.save_file("docs", "alpha", _BODY, ws.carts_root)
    assert select_on_home(ws, "Notes") is not None
    ws.open()
    if not ships:
        # the run's host plans nothing: every frame draws whole
        h = ws.player._uh
        run = h.run

        def whole(*a, **k):
            h.whole()
            return run(*a, **k)
        h.run = whole
    return ws


class Pair:
    """The session on both consoles, frame by frame; the first's buffers
    presented in turn, `bufs` of them (the full-screen console's glass)."""

    def __init__(self, tmp_path, bufs, desk=False):
        self.a = _console(tmp_path, "ships", True, desk)
        self.b = _console(tmp_path, "whole", False, desk)
        self.da = host_app.ConsoleDriver(self.a)
        self.db = host_app.ConsoleDriver(self.b)
        self.bufs = bufs
        self.desk = desk
        root = self.a.sys_canvas
        root.RETAINED_FRAMES = bufs
        self.held = [bytes(root._buf)]
        self.kinds = []
        for _ in range(4):
            self.frame(check=False)
        for ws in (self.a, self.b):
            ns = ws.player.ns
            if ns["ed"] is None:
                ns["_open"]("alpha")
        if desk:
            # the mouse rests on the note, over no stop
            cx, cy, cw, ch = self.a.wm._wins["desktop"].content_rect()
            self.both("hover", cx + cw * 3 // 4, cy + ch // 2)
        for _ in range(6):
            self.frame(check=False)
        del self.kinds[:]

    def frame(self, idle=False, check=True):
        _NOW[0] += 33
        for ws, drv in ((self.a, self.da), (self.b, self.db)):
            if not idle:
                ws._dirty = True
            ws.pointer.visible = False
            k = ws._frames_drawn
            drv.frame(_DT)
            ws.painted = ws._frames_drawn != k
        a, b = self.a, self.b
        assert a.painted == b.painted, "the two consoles' gates parted"
        if not a.painted:
            return
        self.kinds.append(a.frame_change)
        shot, want = bytes(a.sys_canvas._buf), bytes(b.sys_canvas._buf)
        if check and shot != want:
            w = a.sys_canvas.w
            at = next(i for i in range(0, len(shot), 2) if shot[i:i + 2] != want[i:i + 2]) // 2
            raise AssertionError("frame %d (%s, %d buffers) differs from a whole draw at (%d, %d)"
                                 % (len(self.kinds), a.frame_change, self.bufs, at % w, at // w))
        self.held.append(shot)
        if self.bufs > 1:
            root = a.sys_canvas
            root._comp.last = shot
            root._buf[:] = self.held[-self.bufs] if len(self.held) > self.bufs else self.held[0]

    def both(self, verb, *args):
        getattr(self.da, verb)(*args)
        getattr(self.db, verb)(*args)

    def settle(self, n=4):
        for _ in range(n):
            self.frame(idle=True)

    def typed(self, text):
        for ch in text:
            self.both("type_char", ord(ch) if isinstance(ch, str) else ch)
            self.frame()
            self.frame(idle=True)

    def keys(self, name, n):
        for _ in range(n):
            self.both("press", name)
            self.frame()
            self.frame(idle=True)

    def share(self):
        rows = [k for k in self.kinds if k == "rows"]
        return len(rows) / max(1, len(self.kinds))


def _session(p):
    p.keys("down", 3)
    p.typed("hello there")
    p.keys("right", 4)
    p.keys("left", 2)
    p.typed([0x08, 0x08])           # backspace, backspace
    p.keys("up", 2)
    p.typed("!")
    p.settle()
    p.typed([0x0D])                 # a split: the rows below move
    p.typed("new line")
    p.typed([0x08] * 3)
    p.keys("down", 12)              # past the view's foot: the view scrolls
    p.keys("up", 3)
    p.settle()


@pytest.mark.parametrize("bufs", (1, 2, 3))
def test_typing_and_caret_steps_draw_rows_byte_for_byte(tmp_path, bufs):
    p = Pair(tmp_path, bufs)
    _session(p)
    assert p.share() > 0.5, "Notes drew rows on %.2f of its painted frames: %r" % (
        p.share(), p.kinds)


def test_two_consoles_runs_are_two_lifetimes(tmp_path):
    """Every case here drives two consoles in one process, and each console's
    run is its own "cart" lifetime (device_canvas `_owners`): the second
    console starting its run returns nothing the first's run draws into. One
    lifetime for both would hand the first window canvas's pixels back at the
    second start -- to the pool, which lends them to the second, or, with the
    pool full, to the allocator, and the first run's next frame writes freed
    memory."""
    from runtime import glass_binding
    a = _console(tmp_path, "ships", True, True)
    cva = a._run_canvas
    assert cva is not None and a.canvas is cva, "Notes on the desk draws its own canvas"
    ha = cva._comp.buf_h
    b = _console(tmp_path, "whole", False, True)
    assert cva._comp._b.live, "the second console's run returned the first's canvas"
    hb = b._run_canvas._comp.buf_h
    assert hb != ha
    assert glass_binding.row(ha)[3] != glass_binding.row(hb)[3], \
        "two consoles' runs share one owner"


@pytest.mark.parametrize("bufs", (1, 2, 3))
def test_the_desk_draws_rows_byte_for_byte(tmp_path, bufs):
    """On the desk the run's canvas is its window's own (`Player.holds`):
    it holds the page as the run's last frame left it."""
    p = Pair(tmp_path, bufs, desk=True)
    _session(p)
    assert p.share() > 0.5, "Notes drew rows on %.2f of its painted frames: %r" % (
        p.share(), p.kinds)


def test_a_selection_undo_and_redo_stay_byte_for_byte(tmp_path):
    """A selection grown by keys and an undo and redo (each moves the
    page's gen: the tools and the foot change) on two buffers."""
    p = Pair(tmp_path, 2)
    p.keys("down", 2)
    p.typed("abc")
    for _ in range(3):
        p.both("key_down", ord("x"))     # a held key's repeats: one row a frame
        p.frame()
    p.both("key_up", ord("x"))
    p.frame()
    p.typed([0x01])                 # Ctrl+A: everything selected
    p.keys("right", 2)
    p.typed([0x1A, 0x1A, 0x19])     # undo, undo, redo
    p.settle()
    assert "rows" in p.kinds


def test_a_cart_that_clears_draws_whole(tmp_path):
    """A clear under the floor breaks the frame: the pass draws whole from
    the clear on, and a run whose page clears every frame never draws
    rows."""
    p = Pair(tmp_path, 1)
    for ws in (p.a, p.b):
        pl = ws.player
        draw, cls = pl._draw, pl.ns["cls"]

        def cleared(draw=draw, cls=cls):
            cls(0)
            draw()
        pl._draw = cleared
    p.typed("hi")
    p.keys("down", 2)
    p.settle()
    assert p.share() == 0, p.kinds


def test_an_app_that_draws_its_note_alone_draws_rows(tmp_path):
    """An app whose pass draws nothing but its note has its frames planned
    as any: the rows the note changed, drawn alone."""
    p = Pair(tmp_path, 1, desk=True)
    for ws in (p.a, p.b):
        pl = ws.player
        ed = pl.ns["ed"]
        sheet = tuple(pl.ns["sheet"])

        def alone(ed=ed, sheet=sheet):
            ed.draw(*sheet)
        pl._draw = alone
    p.settle(2)
    del p.kinds[:]
    p.typed("hello")
    p.keys("down", 2)
    p.settle()
    assert p.share() > 0.5, p.kinds


# -- shift frames: the view moved ------------------------------------------------


def _at(p, desk, fx, fy):
    """The glass point a fraction (fx, fy) across the note's sheet."""
    sx, sy, sw, sh = p.a.player.ns["sheet"]
    ox = oy = 0
    if desk:
        ox, oy, _w, _h = p.a.wm._wins["desktop"].content_rect()
    return ox + sx + int(sw * fx), oy + sy + int(sh * fy)


def _drag(p, rows, steps, desk):
    """A finger on the note dragged `rows` rows' worth up (negative: down)
    over `steps` frames, then lifted: the view follows it."""
    for ws in (p.a, p.b):
        ws.pointer.hovers = False       # a finger, which pans; a mouse selects
    lh = p.a.player.ns["ed"]._lh()
    sh = p.a.player.ns["sheet"][3]
    d = rows * lh
    d = max(-sh * 7 // 10, min(sh * 7 // 10, d))     # the finger stays on the note
    x, y0 = _at(p, desk, 0.5, 0.85 if rows > 0 else 0.15)
    p.both("touch", x, y0)
    p.frame()
    for i in range(1, steps + 1):
        p.both("touch_drag", x, y0 - d * i // steps)
        p.frame()
    p.both("touch_up")
    p.frame()
    p.settle(3)


def _wheel(p, notches):
    """A mouse over the note, its wheel turned a notch a frame."""
    x, y = _at(p, True, 0.6, 0.5)
    p.both("hover", x, y)
    p.frame()
    for _ in range(abs(notches)):
        p.both("wheel", 0, 1 if notches > 0 else -1)
        p.frame()
    p.settle(2)


def _ball(p, dy, frames):
    """The trackball rolled (the host's arrows held): the caret moves."""
    p.both("pan", 0, dy)
    for _ in range(frames):
        p.frame()
    p.both("pan", 0, 0)
    p.frame()
    p.settle(2)


def _held(p, name, frames):
    p.both("hold", name, True)
    for _ in range(frames):
        p.frame()
    p.both("hold", name, False)
    p.frame()
    p.settle(2)


def _scrolled(p, desk=False):
    _drag(p, 6, 6, desk)            # a slow drag: a row a frame
    _drag(p, -4, 4, desk)           # ...and back
    _drag(p, 10, 2, desk)           # a fling: rows a frame
    _drag(p, -9, 3, desk)
    p.keys("down", 14)              # the caret leaves the view's foot: the view follows it
    if not desk:
        # a held key's repeats, and the trackball: the desk's ball moves its
        # cursor, and its held key's repeats part the two consoles' gates
        _held(p, "down", 12)
        _ball(p, 1, 8)
        _ball(p, -1, 10)
    p.keys("up", 18)                # the caret leaves the view's top
    if desk:
        _wheel(p, 3)
        _wheel(p, -2)
    p.typed([0x01])                 # everything selected, then scrolled under it
    _drag(p, 5, 5, desk)
    _drag(p, -3, 3, desk)
    if desk:
        _wheel(p, 2)
    p.keys("right", 1)
    p.settle()


def _moves(p):
    """Every region a shift frame moves (x, y, w, h, dy), kept as the
    session runs."""
    pl = p.a.player
    h = pl._uh
    run = h.run
    got = []
    out = array("i", [0] * 8)

    def kept(*a, **k):
        r = run(*a, **k)
        if h.shifting and h._dy:
            ui_draw._C.api_shift_plan(h.ctx, out)
            got.append(tuple(out[:5]))
        return r
    h.run = kept
    return got


def _check(p, moves):
    assert p.a.player.cart_error is None, p.a.player.cart_error
    shifts = p.kinds.count("shift")
    assert shifts > len(p.kinds) // 4, "Notes shifted %d of %d painted frames: %r" % (
        shifts, len(p.kinds), p.kinds)
    assert p.kinds.count(None) < len(p.kinds) // 8, p.kinds
    ed = p.a.player.ns["ed"]
    sx, sy, sw, sh = p.a.player.ns["sheet"]
    for mx, my, mw, mh, dy in moves:
        # the region moved is the view, inside the paper's straight sides
        assert (mx, my, mw, mh) == tuple(ed._vr) and dy and mh > abs(dy), (moves, ed._vr)
        assert sx <= mx and mx + mw <= sx + sw and sy <= my and my + mh <= sy + sh


@pytest.mark.parametrize("bufs", (1, 2, 3))
def test_scrolling_the_note_shifts_byte_for_byte(tmp_path, bufs):
    """The handheld: a drag, a fling, the caret leaving the view either
    way, a held key, the trackball and a selection scrolled, on paper with
    round corners."""
    p = Pair(tmp_path, bufs)
    moves = _moves(p)
    _scrolled(p)
    _check(p, moves)
    assert moves


@pytest.mark.parametrize("bufs", (1, 2, 3))
def test_the_desk_shifts_byte_for_byte(tmp_path, bufs):
    """The desk: a finger's drag and fling, a mouse's wheel, the caret
    leaving the view and a selection scrolled."""
    p = Pair(tmp_path, bufs, desk=True)
    moves = _moves(p)
    _scrolled(p, True)
    _check(p, moves)
    assert moves


def test_a_drag_on_the_note_scrolls_it_and_coasts(tmp_path):
    """A press on the note and a drag from it scroll the view by the rows
    the finger moved, and its release coasts on."""
    p = Pair(tmp_path, 1)
    ed = p.a.player.ns["ed"]
    _drag(p, 4, 4, False)
    assert p.a.player.cart_error is None, p.a.player.cart_error
    assert ed.ed.top > 4


def _chip(p):
    """The FPS chip on, both consoles: its readout a function of the frame,
    so its width changes as the session runs."""
    for ws in (p.a, p.b):
        ws.show_fps = True
        hud = ws.perf_ui
        draw = hud.draw

        def counted(ws=ws, draw=draw):
            n = ws._frames_drawn
            ws._fps = (n * 7) % 120
            ws._upd_ms = ws._draw_ms = ws._flush_ms = 1.0
            draw()
        hud.draw = counted


@pytest.mark.parametrize("desk", (False, True))
@pytest.mark.parametrize("bufs", (1, 2, 3))
def test_the_fps_chip_keeps_the_rows(tmp_path, bufs, desk):
    """The FPS chip draws over the page after its pass: what it covered is
    the next frame's floor, and the frame's change holds it."""
    p = Pair(tmp_path, bufs, desk=desk)
    _chip(p)
    del p.kinds[:]
    _session(p)
    _scrolled(p, desk)
    assert p.a.player.cart_error is None
    moved = p.kinds.count("rows") + p.kinds.count("shift")
    assert moved > len(p.kinds) * 2 // 3, p.kinds


@pytest.mark.parametrize("bufs", (1, 2))
def test_a_second_note_keeps_the_rows(tmp_path, bufs):
    """A note opened after another leaves the first closed, which draws
    nothing: the rows are the open one's."""
    p = Pair(tmp_path, bufs)
    for ws in (p.a, p.b):
        moy_carts.save_file("docs", "beta", _BODY, ws.carts_root)
        ws.player.ns["_open"]("beta")
    p.settle(3)
    del p.kinds[:]
    _session(p)
    assert [e.live for e in p.a.player._editors] == [False, True]
    moved = p.kinds.count("rows") + p.kinds.count("shift")
    assert moved > len(p.kinds) * 2 // 3, p.kinds
