"""The sprite pickers' frames (docs/scroll_and_text_2026-10.md §9, §15
P7b): while the pickers are up the page under them does not change but by
the input that reaches them, so a frame whose buffer holds that page as the
frames before left it draws the pickers' body alone -- on the handheld's
full screen where the last RETAINED_FRAMES frames each drew them over the
same page, inside the desk's Sprite pane where the WM vouches for the
buffer -- and the sheet in their part is one `blit_indices_scale` of the
rows the body shows.

Each case runs one session on two consoles: one as it ships, its root
presenting one, two or three buffers in turn, and one that draws every
frame whole; every presented picture of the first is held to the second's
bytes, and the share of its frames that drew the pickers alone is held
above a floor (a mechanism that quietly never engages fails)."""

import pytest

from runtime import host_app, ui_draw
from runtime import moy_input as mi
from ws_helpers import no_unlock_banner, quiesce

DT = 1 / 30
_NOW = [100000]


@pytest.fixture(autouse=True)
def clock(monkeypatch):
    """One clock for both consoles, a frame's worth a frame."""
    import sys
    from runtime import ticks  # noqa: F401 -- loaded, then patched
    for mod in list(sys.modules.values()):
        f = getattr(mod, "__file__", None) or ""
        if "/runtime/" in f.replace("\\", "/") and hasattr(mod, "_ticks_ms"):
            monkeypatch.setattr(mod, "_ticks_ms", lambda: _NOW[0])
    yield


def _fill(sheet):
    """Every pixel of the sheet a colour, none clear: a sheet that shows
    where it is drawn (a clear sheet would hide a draw past the card)."""
    pix = sheet.pix
    w = sheet.w
    for i in range(len(pix)):
        pix[i] = 1 + (i % w // 3 + i // w // 5) % 15
    sheet.gen += 1


def _console(path, desk, ships):
    if desk:
        ws = host_app.build_workstation(str(path / "carts"), sys_size=(1024, 600),
                                        panel_diagonal_in=7.0, font_scale=1, windowed=True)
    else:
        ws = host_app.build_workstation(str(path / "carts"))
    ws.bar_layer._clock_text = lambda: "10:30"
    no_unlock_banner(ws)
    quiesce(ws)
    if desk:
        ws.open_desk()
    ws.open_in_editor(next(c for c in ws.carts.all if c.get("title") == "Star Catcher"))
    drv = host_app.ConsoleDriver(ws)
    for _ in range(3):
        drv.frame(DT)
    if desk:
        ws.editor_desk.switch("art")
        if not ships:
            ws.editor_desk._pane_rows = lambda own, dt: False
    elif not ships:
        ws.wm.CONTENT_RING = False
    ws.editor_app.set_tab("paint")
    ws.pointer.visible = False
    _fill(ws.paint.sheet)
    ws.paint.n = 37
    for _ in range(3):
        drv.frame(DT)
    ws.paint_layer._open_pickers()
    return ws, drv


class Pair:
    def __init__(self, tmp_path, desk, bufs):
        self.desk = desk
        self.a, self.da = _console(tmp_path / "a", desk, True)
        self.b, self.db = _console(tmp_path / "b", desk, False)
        self.bufs = bufs
        self._state = {}
        root = self._root(self.a)
        root.RETAINED_FRAMES = bufs
        self.held = [bytes(root._buf)]
        self.alone = []
        for _ in range(6):
            self.frame(check=False)
        for _ in range(3):
            self.frame()
        del self.alone[:]

    def _root(self, ws):
        return ws.wm._root_canvas if self.desk else ws.sys_canvas

    def _drive(self, ws, drv):
        """One loop pass of `ws` with the toolkit's per-console state its
        own, as on a board with one console."""
        st = self._state.setdefault(id(ws), [None, [-1, False, None] + [-1] * (
            len(ui_draw._PAINT) - 3), False])
        ui_draw._KEYS_HOST[0] = st[0]
        ui_draw._PAINT[:] = st[1]
        ui_draw._EDGE[0] = st[2]
        try:
            ws._dirty = True
            drv.frame(DT)
        finally:
            st[0] = ui_draw._KEYS_HOST[0]
            st[1] = list(ui_draw._PAINT)
            st[2] = ui_draw._EDGE[0]

    def frame(self, check=True):
        _NOW[0] += 33
        a, b = self.a, self.b
        n = a._frames_drawn
        self._drive(a, self.da)
        self._drive(b, self.db)
        if a._frames_drawn == n:
            return
        root = self._root(a)
        shot = bytes(root._buf)
        if check:
            want = bytes(self._root(b)._buf)
            if shot != want:
                w = root.w
                px = [i // 2 for i in range(0, len(shot), 2) if shot[i:i + 2] != want[i:i + 2]]
                xs = [q % w for q in px]
                ys = [q // w for q in px]
                raise AssertionError("frame %d (%d buffers) differs from a whole draw in "
                                     "x %d-%d, y %d-%d" % (a._frames_drawn, self.bufs, min(xs),
                                                           max(xs), min(ys), max(ys)))
        self.alone.append(a.paint_layer.alone_rect is not None)
        self.held.append(shot)
        if self.bufs > 1:
            root._comp.last = shot
            root._buf[:] = (self.held[-self.bufs] if len(self.held) > self.bufs
                            else self.held[0])

    def both(self, verb, *args):
        getattr(self.da, verb)(*args)
        getattr(self.db, verb)(*args)

    def key(self, code, n=1, held=1):
        for _ in range(n):
            self.both("key_down", code)
            for _ in range(held):
                self.frame()
            self.both("key_up", code)
            self.frame()
            self.frame()

    def drag(self, x, y0, dy, steps=6, rest=4):
        self.both("touch", x, y0)
        self.frame()
        for i in range(1, steps + 1):
            self.both("touch_drag", x, y0 + dy * i // steps)
            self.frame()
        self.both("touch_up")
        for _ in range(rest):
            self.frame()

    def body(self):
        """The pickers' card's band on the glass (the body it shows)."""
        cv = self.a.sys_canvas
        card = self.a.paint_layer._host.box("Sprites")
        assert card is not None
        x, y, w, h = ui_draw.popover_band(card, cv, self.a.paint_layer.layout.desk)
        if self.desk:
            vp = self.a.editor_desk
            own = vp._own_pane()
            assert own is not None and own[0] == "paint"
            x += own[4][0]
            y += own[4][1]
            content = self.a.wm._wins["menu"].content_rect()
            x += content[0]
            y += content[1]
        return x, y, w, h

    def share(self):
        return sum(self.alone) / max(1, len(self.alone))


def _session(p):
    x, y, w, h = p.body()
    p.drag(x + w // 2, y + h * 3 // 4, -h // 2)
    p.drag(x + w // 2, y + h * 3 // 4, -h // 3, steps=2, rest=30)     # a fling, coasting
    p.drag(x + w // 2, y + h // 4, h // 2)
    p.key(mi.KEY_DOWN, 4)
    p.key(mi.KEY_UP, 2)
    p.key(mi.KEY_DOWN, 1, held=20)


@pytest.mark.parametrize("bufs", (1, 2, 3))
@pytest.mark.parametrize("desk", (False, True), ids=("handheld", "desk"))
def test_the_pickers_frames_draw_their_body_alone_as_a_whole_draw_does(tmp_path, desk, bufs):
    p = Pair(tmp_path, desk, bufs)
    _session(p)
    assert p.a.paint_layer._pop == "pickers"
    assert p.share() > 0.6, p.alone


@pytest.mark.parametrize("desk", (False, True), ids=("handheld", "desk"))
def test_a_size_picked_and_a_sprite_tapped_draw_the_page_again(tmp_path, desk):
    """The size seg changes the page under the pickers and a tapped tile
    closes them: each draws what a whole draw does."""
    p = Pair(tmp_path, desk, 2)
    p.key(mi.KEY_UP, 3)
    p.key(mi.KEY_RIGHT)
    assert p.a.paint.size == p.b.paint.size != 1
    p.key(mi.KEY_DOWN, 2)
    x, y, w, h = p.body()
    p.both("touch", x + w // 2, y + h // 2)
    p.frame()
    p.both("touch_up")
    for _ in range(4):
        p.frame()
    assert not p.a.paint_layer._pop
    assert p.a.paint.n == p.b.paint.n


@pytest.mark.parametrize("desk", (False, True), ids=("handheld", "desk"))
def test_the_sheet_draws_inside_the_pickers_body_alone(tmp_path, desk):
    """The sheet is taller than the card's body: it is drawn through a clip
    inside the body's band, so what the body does not show of it draws
    nowhere, whole frames and the body's alone alike."""
    p = Pair(tmp_path, desk, 2)
    cv = p.a.sys_canvas
    clips = []
    blit = cv.blit_indices_scale

    def spy(*a, **k):
        card = p.a.paint_layer._host.box("Sprites")
        band = ui_draw.popover_band(card, cv, p.a.paint_layer.layout.desk)
        clips.append(((cv._clip_x0 - cv._ox, cv._clip_y0 - cv._oy,
                       cv._clip_x1 - cv._ox, cv._clip_y1 - cv._oy), band))
        return blit(*a, **k)

    cv.blit_indices_scale = spy
    x, y, w, h = p.body()
    p.drag(x + w // 2, y + h * 3 // 4, -h // 2, rest=30)
    p.drag(x + w // 2, y + h // 4, h // 3)
    assert len(clips) > 10
    for (x0, y0, x1, y1), b in clips:
        assert b[0] <= x0 < x1 <= b[0] + b[2] and b[1] <= y0 < y1 <= b[1] + b[3], (
            (x0, y0, x1, y1), b)


@pytest.mark.parametrize("kind", ("touch", "keys"))
def test_the_sprite_panes_layout_is_kept_whichever_input_is_in_use(tmp_path, monkeypatch, kind):
    """The desk keeps one layout of the Sprite pane per hint kind (its hint
    strip comes and goes with the keys): one built for the other kind would
    be built again every frame, and no frame of the pickers could hold the
    page under them."""
    p = Pair(tmp_path, True, 2)
    monkeypatch.setattr(ui_draw, "hint_kind", lambda: kind)
    for _ in range(3):
        p.frame()
    del p.alone[:]
    lays = []
    for _ in range(4):
        p.frame()
        lays.append(p.a.paint_layer._pick_ring.key(0)[0])
    assert all(lay is lays[0] for lay in lays)
    assert all(p.alone), p.alone
