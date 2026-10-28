"""A press a UI context took is its own until its release. The Blocks tab
routed each pointer sample by where it fell: one under the outline went to
the outline, so a press made on the page (under the outline, on the page's
foot) and released over the outline never reached the page. Its AppHost then
held the tap for good, `page.owed()` marked the console dirty every frame,
and the desk repainted whole at the frame rate until the next input -- on
the Waveshare P4 a ~90 ms frame ten times a second, with the WM's buffer
debt reset to N-1 by each and never paid."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from runtime import host_app, ui_draw                     # noqa: E402
from ws_helpers import no_unlock_banner, quiesce          # noqa: E402

DT = 1 / 30


def test_a_press_from_the_page_released_over_the_outline_lets_the_desk_rest(tmp_path):
    ws = host_app.build_workstation(str(tmp_path / "carts"), sys_size=(1024, 600),
                                    font_scale=2, windowed=True, panel_diagonal_in=7.0)
    ws.open_desk()
    no_unlock_banner(ws)
    quiesce(ws)
    ws.wallpaper.is_animating = lambda dt: False
    ws.bar_layer._clock_text = lambda: "10:30"
    drv = host_app.ConsoleDriver(ws)
    root = ws.wm._root_canvas
    root.RETAINED_FRAMES = 3

    def run(n):
        drawn = ws._frames_drawn
        for _ in range(n):
            drv.frame(DT)
        return ws._frames_drawn - drawn

    ws.open_in_editor(next(c for c in ws.carts.all if c["title"] == "Coin Quest"))
    ws.editor_app.set_tab("blocks")
    ws.mark_dirty()
    run(10)
    was = ui_draw.input_kind()
    ui_draw.input_kind("touch")
    try:
        bui = ws.block_ui
        ax, ay, aw, ah = bui.block_layout.area()
        x = ax + aw // 2
        drv.touch(x, ay + ah + 4)                   # on the page, under the outline
        run(1)
        page = bui._page
        assert page is not None and page.host.hold, "the page did not take the press"
        for y in range(ay + ah - 20, ay + ah // 2, -40):
            drv.touch_drag(x, y)                    # ...up over the outline
            run(1)
        drv.touch_up()                              # ...and up there
        run(1)
        assert not page.host.hold and page.host.tap == 0, "the page never saw the release"
        run(10)
        assert run(30) == 0, "the desk kept painting after the gesture"
        assert ws.wm._full_debt <= 0 and not ws.wm.debt_owed()
    finally:
        ui_draw.input_kind(was)


def test_a_press_on_the_find_strip_released_over_the_code_reaches_the_strip(tmp_path):
    """The Code tab's find strip took only the samples over it, so a press
    made on it and released over the code left its UI context holding."""
    ws = host_app.build_workstation(str(tmp_path / "carts"))
    ws.launcher.sel = 0
    ws.open()
    no_unlock_banner(ws)
    quiesce(ws)
    ws.screen = "menu"
    ws.set_menu_view("code")
    drv = host_app.ConsoleDriver(ws)
    was = ui_draw.input_kind()
    ui_draw.input_kind("touch")
    try:
        drv.frame(DT)
        cl = ws.code_layer
        cl._toggle_find()
        ws.mark_dirty()
        drv.frame(DT)
        fx, fy, fw, fh = cl._find_rect(ws.code_layout)
        x, y = fx + fw // 3, fy + fh // 2
        drv.touch(x, y)
        drv.frame(DT)
        assert cl._ov_host.hold, "the strip did not take the press"
        for dy in (20, 60, 100):
            drv.touch_drag(x, y + dy)               # down over the code
            drv.frame(DT)
        drv.touch_up()
        drv.frame(DT)
        assert not cl._ov_host.hold and cl._ov_host.tap == 0, "the strip never saw the release"
    finally:
        ui_draw.input_kind(was)
