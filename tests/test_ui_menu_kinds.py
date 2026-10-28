"""A menu's danger rows (`ui.menu(..., kinds=)`, moy_ui_api_menu_danger): a
verb that destroys (the system menu's Delete) inks its label in the danger
buttons' ink, every other row in the row's own; and the engine draws Moy,
pixel art in every theme, where a part names its icon."""

from array import array
from pathlib import Path

from runtime import ui, ui_draw
from runtime.host_canvas import make_system_canvas
from ws_helpers import build_ws


def _menu(th, kinds):
    cv = make_system_canvas(320, 240)
    ctx = ui_draw.ui_context()
    inp = array("i", [0] * ui_draw.UI_IN_LEN)
    for _ in range(2):
        ui_draw.ui_frame(ctx, cv, th, None, inp)
        ui.page("")
        ui.at(0, 0, 320, 80)
        ui.menu("", ["Keep it", "Delete it"], ["", ""], kinds=kinds)
    return _px(cv)


def _px(cv):
    """The canvas's pixels, row by row, as RGB565 words."""
    b = cv._buf
    return [b[i] << 8 | b[i + 1] for i in range(0, 2 * cv.w * cv.h, 2)]


def test_a_danger_row_inks_in_the_danger_buttons_ink(tmp_path):
    ws = build_ws(Path(tmp_path))
    ws.look.set_theme("contrast", persist=False, variant="dark")
    th = ws.theme_colors
    look = ui_draw._look(th)
    kid = ui_draw._kind_id(ui_draw._button_kind("danger"))
    danger = ui_draw._C.theme_rgb(ui_draw._C.button_style(look, kid, ui_draw._REST_ID, -1)[1])[0]
    plain, marked = _menu(th, ()), _menu(th, ("", "danger"))
    row0 = slice(0, 20 * 320)
    assert plain[row0] == marked[row0], "a plain row is as it was"
    assert danger not in plain and danger in marked, "Delete's label is in the danger ink"
    assert _menu(th, ()) == plain, "the kinds are one menu's: the next is plain"


def test_moy_draws_where_a_part_names_it(tmp_path):
    ws = build_ws(Path(tmp_path))
    ws.look.set_theme("moy", persist=False, variant="dark")
    ws.icon_spec()
    th = ws.theme_colors

    def page(icon):
        cv = make_system_canvas(320, 240)
        ctx = ui_draw.ui_context()
        for _ in range(2):
            ui_draw.ui_frame(ctx, cv, th, None, array("i", [0] * ui_draw.UI_IN_LEN))
            ui.page("")
            if icon:
                ui.icon("moy")
            ui.info("Star Catcher", "Paused")
        return _px(cv)
    assert page(True) != page(False), "Moy's pixels draw before the label"
