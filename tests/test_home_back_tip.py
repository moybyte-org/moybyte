"""The console home's way back to the desk says where it goes: a mouse
resting on the bar's chevron shows the "Desk" tooltip (design review m03,
owner 2026-10-19); a touch, which has no hover, never does."""

from runtime import launcher_layer, ui_draw
from ws_helpers import build_desktop_ws


def _console_home(tmp_path):
    ws = build_desktop_ws(tmp_path)
    ws.go_home()
    ws.open_library()                     # console mode over the parked desk
    ws.frame(1 / 30)
    r = ws.bar_layer.back_rect("home")
    assert r is not None, "console mode over a desk draws the way back"
    return ws, r


def test_a_mouse_on_the_chevron_shows_the_desk_tooltip(tmp_path):
    ws, r = _console_home(tmp_path)
    home = ws.launcher_layer
    drawn = []
    real = home._draw_back_tip
    home._draw_back_tip = lambda cv: (drawn.append(1), real(cv))
    ws.pointer.down = False
    home.handle_pointer(r[0] + r[2] // 2, r[1] + r[3] // 2, False)
    assert home._back_tip
    ws.mark_dirty()
    ws.frame(1 / 30)
    assert drawn, "the tooltip draws while the mouse rests there"
    home.handle_pointer(r[0] + r[2] + 200, r[1] + 300, False)
    assert not home._back_tip, "moving off takes it away"
    assert launcher_layer.BACK_TIP == "Desk"


def test_a_touch_has_no_hover_and_no_tooltip(tmp_path, monkeypatch):
    ws, r = _console_home(tmp_path)
    monkeypatch.setattr(ui_draw, "input_kind", lambda name=None: "touch")
    ws.pointer.down = False
    ws.launcher_layer.handle_pointer(r[0] + r[2] // 2, r[1] + r[3] // 2, False)
    assert not ws.launcher_layer._back_tip
