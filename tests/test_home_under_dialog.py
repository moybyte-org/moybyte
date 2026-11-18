"""A dialog over the handheld home lets the console idle
(runtime/home/launcher_layer.py, `LauncherHomeLayer._band_owed`): the home
under it is the retained frame stamped, and its band, not the home's alone on
the glass, keeps no keys, so its note answers that a buffer lags. That is no
debt a frame can pay: asked for anyway, every frame asked for the next, and
the console painted the same frame at its full rate while the dialog stood."""

from runtime import host_app, moy_carts
from ws_helpers import build_ws, no_unlock_banner, quiesce, shelf

DT = 1 / 30


def test_the_delete_confirm_over_make_goes_idle(tmp_path):
    ws = build_ws(tmp_path)
    moy_carts.create("Extra", str(ws.carts_root), src="def _draw():\n    cls(1)\n", type="game")
    ws.carts.apply(shelf(str(ws.carts_root)))
    ws.home.set_home_tab("make")
    ws.bar_layer.clock_text = lambda: "10:30"
    no_unlock_banner(ws)
    quiesce(ws)
    ws.pointer.visible = False
    drv = host_app.ConsoleDriver(ws)
    for _ in range(20):
        drv.frame(DT)
    n = ws.frame_state.frames_drawn
    for _ in range(10):
        drv.frame(DT)
    assert ws.frame_state.frames_drawn == n, "the home alone idles"
    ws.confirm.ask(next(c for c in ws.carts.all if c.get("title") == "Extra"))
    for _ in range(10):
        drv.frame(DT)
    assert ws.confirm.open
    n = ws.frame_state.frames_drawn
    for _ in range(30):
        drv.frame(DT)
    assert ws.frame_state.frames_drawn == n, "the dialog over it idles too"
