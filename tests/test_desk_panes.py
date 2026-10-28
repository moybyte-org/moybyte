"""A desk Editor pane's own frames (docs/scroll_and_text_2026-10.md §15
P5): while the focused pane shows Blocks, Settings, the Map or the Sound
pane, a frame whose input reached that pane alone draws the pane alone and
names it as what it changed, as the Code pane's do.

Each case runs one session on two desks: one as it ships, its root
presenting one, two or three buffers in turn, and one that draws every
frame whole on one buffer; every presented picture of the first is held to
the second's bytes, and the share of its key frames that were the pane's
alone is held above a floor (a mechanism that quietly never engages fails)."""

import pytest

from runtime import host_app
from runtime import moy_input as mi
from ws_helpers import no_unlock_banner, quiesce

DT = 1 / 30
_NOW = [100000]


@pytest.fixture(autouse=True)
def clock(monkeypatch):
    """One clock for both desks, a frame's worth a frame."""
    import sys
    from runtime import ticks  # noqa: F401 -- loaded, then patched
    for mod in list(sys.modules.values()):
        f = getattr(mod, "__file__", None) or ""
        if "/runtime/" in f.replace("\\", "/") and hasattr(mod, "_ticks_ms"):
            monkeypatch.setattr(mod, "_ticks_ms", lambda: _NOW[0])
    yield


# surface -> (cart, workspace, tab)
SURFACES = {"blocks": ("Coin Quest", "code", "blocks"),
            "cards": ("Coin Quest", "settings", "cards"),
            "cards pictures": ("Hop Quest", "settings", "cards"),
            "music": ("Sky Run", "sound", "music"),
            "map": ("Sky Run", "art", "map")}


def _desk(path, cart, key, tab, ships, window=False):
    ws = host_app.build_workstation(str(path / "carts"), sys_size=(1024, 600),
                                    panel_diagonal_in=7.0, font_scale=1, windowed=True)
    ws.bar_layer._clock_text = lambda: "10:30"
    no_unlock_banner(ws)
    quiesce(ws)
    ws.open_desk()
    ws.open_in_editor(next(c for c in ws.carts.all if c.get("title") == cart))
    drv = host_app.ConsoleDriver(ws)
    for _ in range(3):
        drv.frame(DT)
    if window:
        # a window short of the glass: the WM asks HELD in the root's layout,
        # whose arrangement of the panes is not the window's
        win = ws.wm._wins["menu"]
        if win.saved is None:
            win.saved = (120, 70, 892, 509)
        ws.wm._toggle_max(win)
        drv.frame(DT)
    d = ws.editor_desk
    d.switch(key)
    ws.editor_app.set_tab(tab)
    ws.pointer.visible = False
    if not ships:
        d._pane_rows = lambda own, dt: False
    return ws, drv


class Pair:
    def __init__(self, tmp_path, surface, bufs, window=False):
        cart, key, tab = SURFACES[surface]
        self.a, self.da = _desk(tmp_path / "a", cart, key, tab, True, window)
        self.b, self.db = _desk(tmp_path / "b", cart, key, tab, False, window)
        self.d = self.a.editor_desk
        self.bufs = bufs
        root = self.a.wm._root_canvas
        root.RETAINED_FRAMES = bufs
        self.held = [bytes(root._buf)]
        self.own = []                   # each painted frame: the pane's alone
        for _ in range(6):
            self.frame(check=False)
        for ws in (self.a, self.b):
            ws._toast_until = 0
            ws._dirty = True
        for _ in range(3):
            self.frame()
        del self.own[:]

    def frame(self, check=True):
        _NOW[0] += 33
        a, b = self.a, self.b
        n = a._frames_drawn
        self.da.frame(DT)
        self.db.frame(DT)
        if a._frames_drawn == n:
            return
        root = a.wm._root_canvas
        shot = bytes(root._buf)
        if check:
            want = bytes(b.wm._root_canvas._buf)
            if shot != want:
                w = root.w
                px = [i // 2 for i in range(0, len(shot), 2) if shot[i:i + 2] != want[i:i + 2]]
                xs = [q % w for q in px]
                ys = [q // w for q in px]
                raise AssertionError("frame %d (%d buffers) differs from a whole draw in "
                                     "x %d-%d, y %d-%d" % (a._frames_drawn, self.bufs, min(xs),
                                                           max(xs), min(ys), max(ys)))
        ch = self.d.changed()
        self.own.append(ch is not None and ch != [0, 0, root.w, root.h])
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

    def drag(self, x, y0, dy, steps=6):
        self.both("touch", x, y0)
        self.frame()
        for i in range(1, steps + 1):
            self.both("touch_drag", x, y0 + dy * i // steps)
            self.frame()
        self.both("touch_up")
        for _ in range(4):
            self.frame()

    def pane(self):
        own = self.d._own_pane()
        assert own is not None
        return own[4]


@pytest.mark.parametrize("bufs", (1, 2, 3))
@pytest.mark.parametrize("surface", tuple(SURFACES))
def test_a_panes_key_frames_are_its_own_and_a_whole_draws(tmp_path, surface, bufs):
    p = Pair(tmp_path, surface, bufs)
    if surface == "music":
        p.key(mi.KEY_RIGHT, 3)
        p.key(mi.KEY_LEFT, 3)
        p.key(mi.KEY_RIGHT, 1, held=20)
    else:
        p.key(mi.KEY_DOWN, 6)
        p.key(mi.KEY_UP, 3)
        p.key(mi.KEY_DOWN, 1, held=20)
    if surface == "map":
        p.key(mi.KEY_RIGHT, 6)
    assert sum(p.own) >= len(p.own) // 2, p.own


@pytest.mark.parametrize("surface", ("blocks", "cards", "music"))
def test_a_window_short_of_the_glass_keeps_the_panes_frames_its_own(tmp_path, surface):
    p = Pair(tmp_path, surface, 2, window=True)
    assert p.a.wm._wins["menu"].content_rect()[2] < p.a.wm._root_canvas.w
    p.key(mi.KEY_RIGHT if surface == "music" else mi.KEY_DOWN, 6)
    assert sum(p.own) >= len(p.own) // 2, p.own


@pytest.mark.parametrize("bufs", (1, 2))
@pytest.mark.parametrize("surface", ("blocks", "map", "cards"))
def test_a_drag_in_a_pane_draws_as_a_whole_draw_does(tmp_path, surface, bufs):
    p = Pair(tmp_path, surface, bufs)
    x, y, w, h = p.pane()
    p.drag(x + w // 2, y + h * 3 // 4, -h // 3)
    p.drag(x + w // 2, y + h // 4, h // 3)


@pytest.mark.parametrize("bufs", (1, 2))
def test_an_edit_in_the_sound_pane_reaches_the_sounds_list(tmp_path, bufs):
    """A note moved is an undo step: the Sounds list's sketch of the sound
    changes with it, so that frame draws whole and the glass is a whole
    draw's."""
    p = Pair(tmp_path, "music", bufs)
    p.key(mi.KEY_UP, 3)
    p.key(mi.KEY_RIGHT)
    p.key(mi.KEY_DOWN, 2)


@pytest.mark.parametrize("bufs", (1, 2))
def test_an_edit_in_the_blocks_pane_reaches_the_made_code(tmp_path, bufs):
    """A block moved down the script is an undo step: the made code beside
    it changes with it."""
    p = Pair(tmp_path, "blocks", bufs)
    p.key(mi.KEY_DOWN, 2)
    for ws in (p.a, p.b):
        ws.block_ui.page().act("down")
        ws._dirty = True
    p.frame()
    p.frame()
    p.key(mi.KEY_UP, 2)
