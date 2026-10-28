"""Shift frames in the shipped apps (docs/scroll_and_text_2026-10.md §3.3,
§3.4, §13), on the full-screen console the T-Deck runs: Settings' rows, its
Licences and Achievements pages, My Art's drawings and Get Carts' shelf,
dragged, flung and stepped through by keys, on one, two and three buffers
presented in turn.

Each case runs the same session on two consoles: one as it ships, its
frames free to shift (the content's own ring vouching, §3.4), and one whose
WM vouches for nothing, so every frame draws whole; every presented picture
of the first is held to the second's bytes, the share of its painted frames
that shifted or drew rows alone is held above a floor (L8's silent-disable
direction), and the engine's record audit fails a shift frame whose stops
moved outside its floor (L8's correctness direction)."""

import os

import pytest

from cart_store_fixtures import MemNet, Repo
from runtime import host_app, moy_carts, ui_draw
from ws_helpers import no_unlock_banner, quiesce

_DT = 1 / 30
_C = ui_draw._C


_NOW = [100000]


@pytest.fixture(autouse=True)
def clock(monkeypatch):
    """One clock for both consoles, a frame's worth a frame: what an app
    times (a cover's budget, a toast) times the same on each."""
    import sys
    from runtime import getcarts_app, myart_app, ticks  # noqa: F401 -- loaded, then patched
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
    assert failed == 0, "%d shift frames drew stops unlike the last frame's outside the floor" % failed


def _shelf_net():
    repo = Repo("https://mit.example/carts", "Test carts")
    for i in range(24):
        repo.add("c%02d" % i, name="Cart %02d" % i)
    return repo, MemNet(repo.routes())


class _SameFree:
    """The store's role, its free space one number on both consoles (the
    shelf's caption says it, and two stores' measured room differs)."""

    def __init__(self, inst):
        self._inst = inst

    def __getattr__(self, name):
        return getattr(self._inst, name)

    def free(self):
        return (1 << 30, 4096)


def _settle_mtimes(root):
    """The drawings' files dated one second apart in their names' order:
    My Art lists them newest first, and two stores written in the same
    second would otherwise order them as the clock fell."""
    import os
    for dirpath, _dirs, files in os.walk(str(root)):
        for f in files:
            if f.lower().startswith("drawing"):
                n = int("".join(ch for ch in f if ch.isdigit()) or 0)
                os.utime(os.path.join(dirpath, f), (1700000000 + n, 1700000000 + n))


def _console(tmp_path, app, ships):
    import json
    from runtime import cart_index as ci
    root = tmp_path / ("ships" if ships else "whole")
    ws = host_app.build_workstation(str(root / "carts"))
    ws.bar_layer._clock_text = lambda: "10:30"
    no_unlock_banner(ws)
    quiesce(ws)
    ws.pointer.visible = False
    if not ships:
        ws.wm.CONTENT_RING = False
    if app in ("settings", "licences", "achievements"):
        ws.open_settings()
        if app != "settings":
            ws.settings_layer.open_page(app)
    elif app == "myart":
        for i in range(18):
            moy_carts.save_file("drawings", "Drawing %02d" % i,
                                moy_carts.encode_moyimg(32, 24, bytes([8 + i % 8]) * 768),
                                ws.carts_root)
        _settle_mtimes(os.path.dirname(str(ws.carts_root).rstrip("/")))
        assert ws.open_app(ws._apps_by_id["myart"])
    elif app == "getcarts":
        repo, net = _shelf_net()
        (root / ci.INDEXES_NAME).write_text(json.dumps({"indexes": [repo.url("index.json")]}))
        ws.cart_net = net
        app_ = ws._apps_by_id["getcarts"]
        app_._inst = _SameFree(app_._inst)
        assert ws.open_app(app_) is not False
    return ws


def _owes(ws):
    """Whether an app of the console has idle work owed."""
    for app, _t in ws._apps:
        if getattr(app, "idle_owed", False):
            return True
    return False


class Pair:
    """The session on both consoles, frame by frame; the first's buffers
    presented in turn, `bufs` of them."""

    def __init__(self, tmp_path, app, bufs):
        self.a = _console(tmp_path, app, True)
        self.b = _console(tmp_path, app, False)
        self.da = host_app.ConsoleDriver(self.a)
        self.db = host_app.ConsoleDriver(self.b)
        self.bufs = bufs
        root = self.a.sys_canvas
        root.RETAINED_FRAMES = bufs
        self.held = [bytes(root._buf)]
        self.kinds = []
        self.n = 0
        # the open and the idle work it owes (My Art's pictures, made one an
        # idle frame) settle on both before any frame is held to the other
        for _ in range(200):
            self.frame(idle=True, check=False)
            if not (_owes(self.a) or _owes(self.b)):
                break
        for _ in range(3):
            self.frame(check=False)
        for _ in range(3):
            self.frame()

    def frame(self, idle=False, check=True):
        """One frame of both, every frame painted unless `idle` (the gate's
        to decide, as a board's); `check`: its picture held to the other's."""
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
        self.n += 1
        self.kinds.append(a.frame_change)
        shot, want = bytes(a.sys_canvas._buf), bytes(b.sys_canvas._buf)
        if check and shot != want:
            w = a.sys_canvas.w
            at = next(i for i in range(0, len(shot), 2) if shot[i:i + 2] != want[i:i + 2]) // 2
            raise AssertionError("frame %d (%s, %d buffers) differs from a whole repaint at (%d, %d)"
                                 % (self.n, a.frame_change, self.bufs, at % w, at // w))
        self.held.append(shot)
        if self.bufs > 1:
            root = a.sys_canvas
            root._comp.last = shot
            root._buf[:] = self.held[-self.bufs] if len(self.held) > self.bufs else self.held[0]

    def both(self, verb, *args):
        getattr(self.da, verb)(*args)
        getattr(self.db, verb)(*args)

    def drag(self, x, y0, dy, steps=6):
        self.both("touch", x, y0)
        self.frame()
        for i in range(1, steps + 1):
            self.both("touch_drag", x, y0 + dy * i // steps)
            self.frame()
        self.both("touch_up")
        self.frame()

    def settle(self, most=200):
        for _ in range(most):
            self.frame(idle=True)

    def keys(self, name, n):
        for _ in range(n):
            self.both("press", name)
            self.frame()
            self.frame(idle=True)

    def share(self):
        moved = [k for k in self.kinds if k is not None]
        return len(moved) / max(1, len(self.kinds))


APPS = ("settings", "licences", "achievements", "myart", "getcarts")


@pytest.mark.parametrize("bufs", (1, 2, 3))
@pytest.mark.parametrize("app", APPS)
def test_drags_flings_and_key_steps_shift_byte_for_byte(tmp_path, app, bufs):
    p = Pair(tmp_path, app, bufs)
    lay = p.a.layout
    x = lay.cx + lay.cw // 2
    y = lay.cy + lay.ch * 3 // 4
    del p.kinds[:]
    p.drag(x, y, -lay.ch // 2)
    p.settle()
    p.drag(x, y, -lay.ch // 3, steps=3)
    p.settle()
    p.drag(x, lay.cy + lay.ch // 4, lay.ch // 3, steps=3)
    p.settle()
    p.keys("down", 6)
    p.keys("up", 3)
    p.settle()
    assert p.share() > 0.5, "%s shifted or drew rows on %.2f of its painted frames: %r" % (
        app, p.share(), p.kinds)


def _settings_session(p):
    lay = p.a.layout
    x = lay.cx + lay.cw // 2
    y = lay.cy + lay.ch * 3 // 4
    del p.kinds[:]
    p.drag(x, y, -lay.ch // 2)
    p.settle()
    p.keys("down", 6)
    p.settle()
    return p.share()


@pytest.mark.parametrize("cost", (None, 700, 1000))
def test_a_canvas_whose_copy_costs_the_raster_draws_whole(tmp_path, cost):
    """A canvas declares what its `shift_last` costs against the raster it
    saves (`SHIFT_COST`, the T-Deck's CPU copy beside the feeder): a frame
    whose floor leaves too little of R undrawn to pay for the copy draws
    whole, and every frame is still a whole repaint's bytes."""
    p = Pair(tmp_path / str(cost), "settings", 2)
    p.a.sys_canvas.SHIFT_COST = cost
    share = _settings_session(p)
    if cost == 1000:
        assert share == 0
    else:
        assert share > 0
    if cost == 700:
        q = Pair(tmp_path / "free", "settings", 2)
        assert share < _settings_session(q)


def test_a_move_that_leaves_pictures_undrawn_pays_whatever_the_copy_costs(tmp_path):
    """A picture's rows cost more to draw than any copy (`ui_draw._pays`):
    My Art's drawings, dragged on a canvas whose copy costs a whole row's
    raster, still shift where the floor leaves a picture undrawn."""
    p = Pair(tmp_path, "myart", 2)
    p.a.sys_canvas.SHIFT_COST = 1000
    lay = p.a.layout
    del p.kinds[:]
    p.drag(lay.cx + lay.cw // 2, lay.cy + lay.ch * 3 // 4, -lay.ch // 2)
    p.settle()
    assert p.share() > 0
