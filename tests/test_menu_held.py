"""The handheld home held under the system menu (`SystemMenuUI.put_home`,
docs/surface_model_v1.md §14.5): while the menu's card stands over the home
alone, a frame puts the home back washed instead of painting it and the
scrim, and the home's retained frame stays the one before the menu, which
the menu's close stamps.

Each case runs a scripted session twice on one and two buffers presented in
turn: once as it ships, and once with the hold off (every frame paints the
home, the scrim and the card), and holds the first's glass, picture by
picture, to the second's bytes -- and that the hold put the home back on
most of the menu's frames and the close stamped, so neither can silently
stop."""

import pytest

from runtime import console_notices, host_app
from ws_helpers import no_unlock_banner, quiesce


class _Key:
    last_key = 0

    def __init__(self, n):
        self.n = n

    def steps(self, n):
        return int(bool(self.pressed(n)))

    def pressed(self, n):
        return n == self.n


def _home(tmp_path, monkeypatch, bufs, ref, theme):
    if ref:
        UI = console_notices.SystemMenuUI
        monkeypatch.setattr(UI, "holds_home", lambda self: False)
    ws = host_app.build_workstation(str(tmp_path / "carts"))
    if theme is not None:
        ws.look.set_theme(theme, persist=False)
        ws.look.select_wallpaper(None, persist=False)   # its backdrop, not a cart
    ws.go_home()
    ws.wallpaper.is_animating = lambda dt: False
    ws.bar_layer._clock_text = lambda: "10:30"
    no_unlock_banner(ws)
    quiesce(ws)
    drv = host_app.ConsoleDriver(ws)
    root = ws.sys_canvas
    root.RETAINED_FRAMES = bufs
    held = [bytes(root._buf)]

    def frame():
        n = ws._frames_drawn
        drv.frame(1 / 30)
        if ws._frames_drawn == n:
            return None
        held.append(bytes(root._buf))
        if bufs > 1:
            root._comp.last = held[-1]
            root._buf[:] = held[-bufs] if len(held) > bufs else held[0]
        return held[-1]

    for _ in range(20):
        ws._dirty = True
        frame()
    ws.pause.prealloc()            # the idle frame's mint of the held layer
    return ws, frame


def _session(ws, frame):
    shots = []

    def run(n):
        for _ in range(n):
            s = frame()
            if s is not None:
                shots.append(s)

    def menu_round():
        ws.toggle_sysmenu()
        run(3)
        for k in ("down", "down", "up", "down"):
            ws._sysmenu_layer.handle_input(_Key(k))
            ws.mark_dirty()
            run(2)
        ws.toggle_sysmenu()
        run(3)

    menu_round()
    menu_round()                   # again over the same home
    ws.launcher_layer.handle_input(_Key("right"))
    ws.mark_dirty()
    run(20)
    menu_round()                   # over a home that moved
    for k in ("left", "left"):
        ws.launcher_layer.handle_input(_Key(k))
        ws.mark_dirty()
        run(20)
    menu_round()                   # over one that moved the other way
    ws.toggle_sysmenu()
    run(2)
    ws.bar_layer._clock_text = lambda: "10:31"   # the minute turns under the menu
    ws.mark_dirty()
    run(3)
    ws._sysmenu_layer.handle_input(_Key("down"))
    ws.mark_dirty()
    run(2)
    ws.toggle_sysmenu()
    run(3)
    return shots


@pytest.mark.parametrize("bufs", [1, 2])
@pytest.mark.parametrize("theme", [None, "night", "blocks"], ids=["shipped", "night", "blocks"])
def test_every_presented_frame_is_a_whole_repaints(tmp_path, monkeypatch, bufs, theme):
    with monkeypatch.context() as m:      # (the conftest's patches stay)
        ws, frame = _home(tmp_path / "a", m, bufs, True, theme)
        whole = _session(ws, frame)
    ws, frame = _home(tmp_path / "b", monkeypatch, bufs, False, theme)
    ui = ws.menu_ui
    home = ws.launcher_layer
    puts, stamps = [], []
    real_put = ui.put_home
    real_stamp = home._try_stamp_retained
    ui.put_home = lambda cv, dt: puts.append(real_put(cv, dt)) or puts[-1]
    home._try_stamp_retained = lambda cv, dt: stamps.append(real_stamp(cv, dt)) or stamps[-1]
    part = _session(ws, frame)
    assert len(whole) == len(part)
    for i, (a, b) in enumerate(zip(whole, part)):
        assert a == b, "the glass's picture %d differs from a full repaint's" % i
    # the menu's moves and its second open over the same home
    assert sum(puts) >= 13, puts
    # each close of the menu stamps the frame before it, but the last: the
    # minute turned under it
    assert sum(stamps) >= 3, stamps
