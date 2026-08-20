"""Frame-time breakdown HUD (#43/#44 perf): a toggleable on-device overlay that
shows, in ms, the per-frame split of a running cart -- flush (panel DMA), draw
(everything else), total. Driven through the SAME shared console the device runs
(runtime.host_app + ConsoleDriver), so this asserts host==device behaviour.

Note: the host's _NullComp.flush is a near-zero no-op (no real panel), so flush-ms
reads ~0 here -- the real flush-vs-draw numbers come from the device. These tests
prove the plumbing: the fields populate, the HUD toggles via an FPS-readout tap
through the driver, drawing it doesn't crash, and it never forces an idle redraw.
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

from runtime import host_app  # noqa: E402

DT = 1.0 / 30


def _running_cart(tmp_path):
    """Open a seeded GAME so a cart is running on the desktop (it animates every
    frame -- the perf HUD lives on this running-cart screen).

    Pick the game by TYPE, not by position: a game runs fullscreen while an app
    runs with the bar, and which cart sorts first is alphabetical."""
    ws = host_app.build_workstation(str(tmp_path / "carts"))
    drv = host_app.ConsoleDriver(ws)
    assert ws.launcher.items, "system carts should be seeded"
    ws.launcher.sel = next(i for i, it in enumerate(ws.launcher.items)
                           if it.get("path") and it.get("type") == "game")
    ws.open()
    assert ws.screen == "desktop"
    assert ws._update is not None or ws._draw is not None
    return ws, drv


def test_perf_hud_off_by_default(tmp_path):
    """The HUD is off by default so it never clutters normal play."""
    ws, _ = _running_cart(tmp_path)
    assert ws.perf_hud is False


def test_perf_fields_populate_after_frames_when_on(tmp_path):
    """With the HUD on, frame() must record the flush/draw split each frame."""
    ws, drv = _running_cart(tmp_path)
    ws.perf_hud = True
    for _ in range(10):
        drv.frame(DT)
    # draw-ms is the bulk of the frame on host (flush is the ~0 no-op _NullComp);
    # it must be populated (>= 0, and a real number) after running frames.
    assert isinstance(ws._draw_ms, float)
    assert ws._draw_ms >= 0.0
    assert isinstance(ws._flush_ms, float)
    assert ws._flush_ms >= 0.0


def test_drawing_the_hud_does_not_crash(tmp_path):
    """Toggling the HUD on and rendering many running-cart frames must not raise --
    a crash here would hang the device silently."""
    ws, drv = _running_cart(tmp_path)
    ws.perf_hud = True
    for _ in range(30):
        drv.frame(DT)   # _draw_fps + _draw_perf_hud run each frame; must be clean
    assert ws.screen == "desktop"


def test_fps_tap_toggles_the_hud_through_the_driver(tmp_path):
    """Tapping the FPS readout (bottom-right corner) toggles the perf HUD -- the
    keyboard-free, kid-can't-trip-it-by-accident affordance. Driven through the
    ConsoleDriver (mouse == device touch), so it exercises the real pointer path."""
    ws, drv = _running_cart(tmp_path)
    for _ in range(3):
        drv.frame(DT)               # settle
    assert ws.perf_hud is False
    rx, ry, rw, rh = ws.perf_ui._fps_tap_rect()
    tx, ty = rx + rw // 2, ry + rh // 2
    drv.click(tx, ty)
    drv.frame(DT)
    assert ws.perf_hud is True, "tapping the FPS readout should turn the HUD on"
    drv.click(tx, ty)
    drv.frame(DT)
    assert ws.perf_hud is False, "tapping it again should turn the HUD off"


def test_a_top_bar_tap_does_not_toggle_the_hud(tmp_path):
    """A tap on the cart's top-bar tool row (HOME etc.) must not be mistaken for an
    FPS tap -- the tap target is the bottom-right corner only."""
    import runtime.console as C
    ws, drv = _running_cart(tmp_path)
    for _ in range(3):
        drv.frame(DT)
    x, y, w, h = C._HOME_BTN
    # Tapping HOME leaves the desktop; assert it didn't flip perf_hud on the way.
    drv.click(x + w // 2, y + h // 2)
    drv.frame(DT)
    assert ws.perf_hud is False


def test_hud_does_not_force_an_idle_redraw(tmp_path):
    """Respect redraw-on-change (#44): the HUD lives on the running-cart desktop,
    which already animates every frame, so it must not add any extra redraws. With a
    cart running, every frame draws (60 of 60) whether the HUD is on or off."""
    ws, drv = _running_cart(tmp_path)
    ws.ach.toast = None
    ws.perf_hud = True
    for _ in range(3):
        drv.frame(DT)
    ws.ach.toast = None
    drv.frame(DT)
    base = ws._frames_drawn
    for _ in range(60):
        drv.frame(DT)
    drawn = ws._frames_drawn - base
    assert drawn == 60, "running-cart HUD frame drew %d of 60 (should be 60)" % drawn
