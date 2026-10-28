"""Notes' rows frames (docs/scroll_and_text_2026-10.md §5, §13's text view):
a key typed into a note, a caret step, a selection grown and the frames a
change owes after it draw the rows of the note that changed and the page's
floor under them, never the page whole.

Each case runs one session on two consoles: one as it ships and one whose
Player never begins a rows frame, so every frame draws whole; every
presented picture of the first is held to the second's bytes, on one, two
and three buffers presented in turn (the full-screen console) and on the
desk, where the window composites what the frame changed. The share of
painted frames that drew rows alone is held above a floor (L8's
silent-disable direction), and the engine's record audit fails a rows frame
whose stops moved outside its floor.
"""

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
        ws.player._rows_begin = lambda *a: None
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
        self.whole = 0                  # the window's whole composites
        if desk:
            blit = self.a.wm._blit_game

            def counted(*a, **k):
                self.whole += 1
                return blit(*a, **k)
            self.a.wm._blit_game = counted
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


@pytest.mark.parametrize("bufs", (1, 2, 3))
def test_the_desk_draws_and_composites_rows_byte_for_byte(tmp_path, bufs):
    """On the desk the window copies onto the glass what the frame changed
    and what the buffer it paints lags, not the canvas."""
    p = Pair(tmp_path, bufs, desk=True)
    p.whole = 0
    _session(p)
    assert p.share() > 0.5, "Notes drew rows on %.2f of its painted frames: %r" % (
        p.share(), p.kinds)
    assert p.whole < len(p.kinds) // 2, "the window composited %d of %d frames whole" % (
        p.whole, len(p.kinds))


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


def test_an_app_that_draws_its_note_alone_draws_rows_by_its_counters(tmp_path):
    """On a canvas only its run draws (the desk's window), an app that draws
    nothing but its note has its rows drawn where the canvas's counters say
    nothing wrote it since the note's last draw."""
    p = Pair(tmp_path, 1, desk=True)
    drew = []
    for ws in (p.a, p.b):
        pl = ws.player
        ed = pl.ns["ed"]
        if ws is p.b:
            ed._frame = None             # the twin draws its note whole
        x, y, w, h = ed._rect

        def alone(ed=ed, x=x, y=y, w=w, h=h, ws=ws):
            ed.draw(x, y, w, h)
            if ws is p.a:
                drew.append(ed.rows_only)
        pl._draw = alone
    p.settle(2)
    p.typed("hello")
    p.keys("down", 2)
    p.settle()
    assert sum(drew) > len(drew) // 2, drew
