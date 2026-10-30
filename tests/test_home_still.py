"""The desk home's still-backdrop frames (`LauncherHomeLayer._try_still_frame`):
a frame of the console home on a desk repaints only the parts the buffer it
paints lacks -- the hero with its details, the shelf's band, the bar -- over
the wallpaper's own pixels, and the retained frame copies only the parts that
moved.

Each case runs a scripted session twice on one, two and three buffers
presented in turn, as a board's framebuffers are: once as it ships, and once
with every partial path off (each frame a whole repaint), and holds the
first's glass, picture by picture, to the second's bytes."""

import pytest

from runtime import host_app
from ws_helpers import no_unlock_banner, quiesce


class _Key:
    last_key = 0

    def __init__(self, n):
        self.n = n

    def steps(self, n):
        return int(bool(self.pressed(n)))

    def pressed(self, n):
        return n == self.n


def _home(tmp_path, monkeypatch, bufs, ref, theme=None):
    from runtime import launcher_layer
    if ref:
        L = launcher_layer.LauncherHomeLayer
        monkeypatch.setattr(L, "_try_still_frame", lambda self, cv, dt: False, raising=False)
        monkeypatch.setattr(L, "_try_band_frame", lambda self, cv, dt: False)
        monkeypatch.setattr(L, "_try_stamp_retained", lambda self, cv, dt: False)
    ws = host_app.build_workstation(str(tmp_path / "carts"), sys_size=(1024, 600),
                                    font_scale=1, windowed=True, panel_diagonal_in=7.0)
    if theme is not None:
        from runtime.chrome import THEME_NAMES
        from ws_helpers import use_theme
        if theme in THEME_NAMES:
            ws.look.set_theme(theme, persist=False)
        else:
            use_theme(ws, theme)
        ws.look.select_wallpaper(None, persist=False)   # its backdrop, not a cart
    ws.open_library()
    ws.wallpaper.is_animating = lambda dt: False
    ws.bar_layer._clock_text = lambda: "10:30"
    no_unlock_banner(ws)          # a banner's deadline is the wall clock's
    quiesce(ws)
    drv = host_app.ConsoleDriver(ws)
    root = ws.wm._root_canvas
    root.RETAINED_FRAMES = bufs
    held = [bytes(root._buf)]
    stats = {"frames": 0}

    def frame():
        n = ws._frames_drawn
        drv.frame(1 / 30)
        if ws._frames_drawn == n:
            return None
        stats["frames"] += 1
        held.append(bytes(root._buf))
        if bufs > 1:
            root._comp.last = held[-1]
            root._buf[:] = held[-bufs] if len(held) > bufs else held[0]
        return held[-1]

    for _ in range(40):
        ws._dirty = True
        frame()
    return ws, drv, frame, stats


def _session(ws, drv, frame):
    """The home's interactions, every presented frame: held-key presses
    through the shelf, a tab round, a drag and a fling, the buttons'
    focus, and back."""
    shots = []

    def run(n):
        for _ in range(n):
            s = frame()
            if s is not None:
                shots.append(s)

    home = ws.launcher_layer
    for k in range(8):
        home.handle_input(_Key("right"))
        ws.mark_dirty()
        run(3 if k % 2 else 1)
    run(20)
    ws.bar_layer._clock_text = lambda: "10:31"   # the bar moves, the parts do not
    ws.mark_dirty()
    run(6)
    home.handle_input(_Key("left"))
    ws.mark_dirty()
    run(6)
    for t in ("mine", "apps", "make", "games"):
        ws.set_home_tab(t)
        run(6)
    bx, by, bw, bh = ws.launcher.band_rect()
    cy = by + bh // 2
    drv.touch(bx + bw - 40, cy)
    run(1)
    for i in range(1, 10):
        drv.touch_drag(bx + bw - 40 - i * 30, cy)
        run(1)
    drv.touch_up()
    run(40)
    for k in ("left", "left", "down", "right", "right", "up"):
        home.handle_input(_Key(k))
        ws.mark_dirty()
        run(2)
    run(10)
    return shots


@pytest.mark.parametrize("bufs", [1, 2, 3])
@pytest.mark.parametrize("theme", [None, "night", "blocks"], ids=["shipped", "night", "blocks"])
def test_every_presented_frame_is_a_whole_repaints(tmp_path, monkeypatch, bufs, theme):
    with monkeypatch.context() as m:      # (the conftest's patches stay)
        ws, drv, frame, _ = _home(tmp_path / "a", m, bufs, True, theme)
        whole = _session(ws, drv, frame)
    ws, drv, frame, _ = _home(tmp_path / "b", monkeypatch, bufs, False, theme)
    calls = {"still": 0, "strip": 0}
    home = ws.launcher_layer
    real = home._try_still_frame
    strip = ws.bar_layer._draw_status_strip

    def counted(cv, dt):
        n = calls["strip"]
        r = real(cv, dt)
        if r:
            calls["still"] += 1
        else:
            calls["strip"] = n          # a whole frame's strip is not counted
        return r

    def strips(where):
        calls["strip"] += 1
        return strip(where)
    home._try_still_frame = counted
    ws.bar_layer._draw_status_strip = strips
    copied = []
    cv = ws.sys_canvas
    real_copy = cv.copy_last
    cv.copy_last = lambda *r: copied.append(r) or real_copy(*r)
    part = _session(ws, drv, frame)
    # A frame that brings a buffer to the glass's frame (`_still_debt`)
    # presents what the glass already shows: the glass's sequence of
    # pictures is the full repaints'.
    whole, part = _changes(whole), _changes(part)
    assert len(whole) == len(part)
    for i, (a, b) in enumerate(zip(whole, part)):
        assert a == b, "the glass's picture %d differs from a full repaint's" % i
    assert calls["still"] > len(part) // 2, (calls, len(part))
    # a still frame draws the bar's strip only where its buffer lacks it
    assert calls["strip"] < calls["still"] // 2, calls
    # ...and a part the last frame painted as this one would is copied from
    # its buffer (§14.6): each press's later frames
    assert len(copied) >= 8, len(copied)


def _changes(shots):
    out = []
    for s in shots:
        if not out or out[-1] != s:
            out.append(s)
    return out


@pytest.mark.parametrize("bufs", [2, 3])
def test_a_change_settles_every_buffer_then_rests(tmp_path, monkeypatch, bufs):
    """A tab switch is painted once and then once more for each buffer that
    lacks it, while the board is idle; then the board rests, and the
    retained frame holds the glass's picture."""
    ws, drv, frame, _ = _home(tmp_path, monkeypatch, bufs, False)
    for t in ("apps", "games"):           # the tab's covers in the cache
        ws.set_home_tab(t)
        for _ in range(20):
            frame()
    ws.set_home_tab("apps")
    shots = [s for s in (frame() for _ in range(10)) if s is not None]
    assert len(shots) == bufs, len(shots)
    assert frame() is None
    home = ws.launcher_layer
    root = ws.wm._root_canvas
    assert home._lib_key == home._retained_key(root)
    assert bytes(home._lib_cache._buf) == shots[-1]


@pytest.mark.parametrize("bufs", [2, 3])
def test_the_retained_frame_waits_for_an_idle_frame(tmp_path, monkeypatch, bufs):
    """The frame that brings the last buffer to a press captures nothing: an
    idle frame after it takes the retained frame, which then holds the
    glass's picture; a press first drops it."""
    ws, drv, frame, _ = _home(tmp_path, monkeypatch, bufs, False)
    home = ws.launcher_layer
    caps = []
    real = home._capture_retained
    monkeypatch.setattr(home, "_capture_retained",
                        lambda cv, dt, parts=None: caps.append(1) or real(cv, dt, parts))
    for _ in range(2):
        home.handle_input(_Key("right"))
        ws.mark_dirty()
        shots = [frame() for _ in range(bufs)]
        assert None not in shots and caps == []
    for _ in range(4):
        assert frame() is None
    assert caps == [1]
    assert bytes(home._lib_cache._buf) == shots[-1]
    assert home._lib_key == home._retained_key(ws.wm._root_canvas)


@pytest.mark.parametrize("bufs", [2, 3])
def test_coming_back_settles_every_buffer(tmp_path, monkeypatch, bufs):
    """Back on the home from another surface, the home paints until every
    buffer holds it, so the first press after is a still frame."""
    ws, drv, frame, _ = _home(tmp_path, monkeypatch, bufs, False)
    ws.open_settings()
    for _ in range(10):
        ws._dirty = True
        frame()
    ws.open_library()
    shots = [s for s in (frame() for _ in range(10)) if s is not None]
    assert len(shots) == bufs, len(shots)
    home = ws.launcher_layer
    still = []
    real = home._try_still_frame
    home._try_still_frame = lambda cv, dt: still.append(real(cv, dt)) or still[-1]
    home.handle_input(_Key("right"))
    ws.mark_dirty()
    assert frame() is not None and still == [True]
