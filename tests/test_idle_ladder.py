"""The idle ladder's console half (runtime/idle_ladder.py): the Settings rows
over the kernel's ladder, and the saver its SAVER rung shows. The ladder's
own decisions are tests/test_moy_loop.py's."""

from runtime import idle_ladder
from runtime import moy_loop as L
from ws_helpers import build_desktop_ws, build_ws, open_cart


def _ladder(ws, can_dim=False, blank=300):
    L.trace_init(30, can_dim, 1000)
    lad = idle_ladder.IdleLadder(ws, L, blank)
    ws.idle_ladder = lad
    return lad


def test_the_rows_are_the_rungs_the_light_can_show(tmp_path):
    ws = build_ws(tmp_path)
    assert [r[0] for r in _ladder(ws, can_dim=False).rows()] == ["idle_saver", "idle_blank"]
    assert [r[0] for r in _ladder(ws, can_dim=True).rows()] == [
        "idle_dim", "idle_saver", "idle_blank"]


def test_a_rung_steps_through_its_values_and_off_is_one(tmp_path):
    ws = build_ws(tmp_path)
    lad = _ladder(ws)
    assert lad.label("idle_saver") == "OFF" and lad.label("idle_blank") == "5M"
    assert lad.step("idle_saver", 1) == 30
    assert L.idle()[2] == 30 and ws.system.get("idle_saver") == 30
    assert lad.step("idle_saver", 1) == 60 and lad.label("idle_saver") == "1M"
    lad.step("idle_saver", -1)
    lad.step("idle_saver", -1)
    assert lad.label("idle_saver") == "OFF" and L.idle()[2] == 0
    assert lad.step("idle_saver", -1) == 0              # OFF is the floor


def test_the_store_wins_over_the_boards_default(tmp_path):
    ws = build_ws(tmp_path)
    ws.system.set("idle_blank", 600)
    ws.system.set("idle_saver", 120)
    _ladder(ws, blank=300)
    assert L.idle()[2:] == (120, 600)


def test_settings_shows_the_rows_only_where_the_kernel_drives_frames(tmp_path):
    ws = build_ws(tmp_path)
    keys = [r[0] for r in ws.settings_layer._settings_rows()]
    assert "idle_blank" not in keys                       # a host console: frozen rows
    _ladder(ws)
    keys = [r[0] for r in ws.settings_layer._settings_rows()]
    assert "idle_saver" in keys and "idle_blank" in keys
    ws.open_settings()
    ws.frame(1 / 30)                                      # the rows draw


def test_the_saver_cycles_covers_on_a_fullscreen_console(tmp_path):
    ws = build_ws(tmp_path)
    ws.frame(1 / 30)
    n = ws._frames_drawn
    ws.saver_state(True)
    assert ws._saver is not None and not ws._saver.wallpaper
    ws.frame(1 / 30)
    assert ws._frames_drawn == n + 1                      # the first card drew
    ws.frame(1 / 30)
    assert ws._frames_drawn == n + 1                      # held until its period ends
    ws.saver_state(False)
    assert ws._saver is None and ws._dirty
    ws.frame(1 / 30)
    assert ws._frames_drawn == n + 2                      # the console repaints


def test_the_saver_is_the_wallpaper_on_a_desk(tmp_path):
    ws = build_desktop_ws(tmp_path)
    ws.saver_state(True)
    assert ws._saver is not None and ws._saver.wallpaper
    n = ws._frames_drawn
    ws.frame(1 / 30)
    ws.frame(1 / 30)
    assert ws._frames_drawn == n + 2


def test_the_saver_never_covers_a_running_cart(tmp_path):
    ws = build_ws(tmp_path)
    open_cart(ws, "Star Catcher")
    ws.saver_state(True)
    assert ws._saver is None
