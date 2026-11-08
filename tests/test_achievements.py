"""Tests for the achievements on the console + the hidden Easter eggs (#21).

Driven through the SAME shared console the device runs (runtime.host_app +
ConsoleDriver: mouse == touch, arrows == trackball), plus direct unit tests of
the strict readers both ends share (runtime/system/achievements.py). The
process itself is tests/test_achievements_proc.py's.
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

import canvas_probe as probe  # noqa: E402  (pixel-width-agnostic "it drew" probes)

# host_app registers the bare `audio`/`editors_*` module aliases that console.py
# imports (its frozen device names), so import it BEFORE console.
import json  # noqa: E402

from runtime import host_app, moy_carts  # noqa: E402
from runtime import achievements as A  # noqa: E402


from ws_helpers import build_ws as _ws, settle_badges


def _tap(drv, rect):
    x, y, w, h = rect
    drv.click(x + w // 2, y + h // 2)
    drv.frame(1 / 30)


def _press(drv, name):
    """One press with a following key-up frame -- the realistic input pattern the
    device produces (a key isn't held across frames), so repeated same-button
    presses register as distinct edges."""
    drv.press(name)
    drv.frame(1 / 30)
    drv.frame(1 / 30)


# -- the readers ---------------------------------------------------------------

GOOD = [{"id": "a", "title": "A", "icon": "star", "hint": "Do a"},
        {"id": "b", "title": "B", "icon": "run", "hint": "Do b", "goal": 3, "secret": True}]


def test_a_declaration_reads_strictly():
    assert A.declared(GOOD) == [
        {"id": "a", "title": "A", "icon": "star", "hint": "Do a", "goal": None, "secret": False},
        {"id": "b", "title": "B", "icon": "run", "hint": "Do b", "goal": 3, "secret": True}]
    bad = [None, [], {}, "a", [GOOD[0], GOOD[0]],
           [dict(GOOD[0], extra=1)], [dict(GOOD[0], title="")], [dict(GOOD[0], icon=3)],
           [dict(GOOD[0], goal=0)], [dict(GOOD[0], goal=2.5)], [dict(GOOD[0], goal=True)],
           [dict(GOOD[0], secret=1)], [dict(GOOD[0], id="x" * (A.ID_MAX + 1))],
           [{"id": "a", "title": "A", "icon": "star"}]]
    for raw in bad:
        assert A.declared(raw) is None, raw


def test_the_badges_row_reads_strictly():
    row = {"system": {"name": "Moybyte", "found": {"konami": [20261108, "S", "spark", "h"]}},
           "local.g": {"name": "G", "value": {"b": 2}}}
    got = A.read_badges(row)
    assert got["system"]["value"] == {} and got["local.g"]["found"] == {}
    assert got["local.g"]["value"] == {"b": 2}
    for bad in (None, [], {"x": []}, {"x": {"found": {}}},
                {"x": {"name": "X", "found": {"a": [1, "t", "i"]}}},
                {"x": {"name": "X", "found": {"a": ["1", "t", "i", "h"]}}},
                {"x": {"name": "X", "value": {"a": 1.5}}},
                {"x": {"name": "X", "count": {}}}):
        assert A.read_badges(bad) is None, bad


def test_the_shells_own_badges_read_as_a_declaration():
    got = A.declared(list(A.SHELL))
    assert [a["id"] for a in got][:2] == ["first_open", "first_run"]
    assert {a["id"]: a["goal"] for a in got if a["goal"]} == {"play_five": 5, "toolbox": 3}
    assert [a["id"] for a in got if a["secret"]] == ["konami", "clock_tinker", "secret_door"]


def test_a_manifests_achievements_reach_the_cart(tmp_path):
    root = tmp_path / "g.moy"
    root.mkdir()
    (root / "main.py").write_text("def _draw():\n    pass\n")
    man = {"format": "moy-1", "title": "G", "id": "local.g", "main": "main.py",
           "moybyte": {"type": "game", "achievements": GOOD}}
    (root / "manifest.json").write_text(json.dumps(man))
    assert moy_carts.load(str(root))["achievements"] == GOOD
    man["moybyte"].pop("achievements")
    (root / "manifest.json").write_text(json.dumps(man))
    assert moy_carts.load(str(root))["achievements"] is None


# -- integration: an event unlocks + persists across reboot -----------------

def test_open_cart_unlocks_and_persists(tmp_path):
    carts = str(tmp_path / "carts")
    ws = host_app.build_workstation(carts)
    assert not ws.ach.has(A.SYSTEM, "first_open")
    ws.launcher.sel = 0
    ws.open()
    host_app.ConsoleDriver(ws).frame(1 / 30)   # the process's message, its prefs write
    assert ws.ach.has(A.SYSTEM, "first_open")
    # Reboot: a fresh workstation on the same store remembers it.
    ws2 = host_app.build_workstation(carts)
    assert ws2.ach.has(A.SYSTEM, "first_open")


def test_save_code_unlocks_code_wizard(tmp_path):
    carts = str(tmp_path / "carts")
    ws = host_app.build_workstation(carts)
    ws.launcher.sel = 0
    ws.open()
    ws.set_menu_view("code")
    assert ws.editor is not None
    ws.save_code()
    settle_badges(ws)
    assert ws.ach.has(A.SYSTEM, "first_code")


def test_save_sprites_unlocks_little_artist(tmp_path):
    ws = _ws(tmp_path)
    ws.launcher.sel = 0
    ws.open()
    ws.set_menu_view("paint")
    ws.save_sprites()
    settle_badges(ws)
    assert ws.ach.has(A.SYSTEM, "first_paint")


# -- the banner and the page render ------------------------------------------

def test_toast_renders(tmp_path):
    ws = _ws(tmp_path)
    drv = host_app.ConsoleDriver(ws)
    ws.launcher.sel = 0
    ws.open()                                # raises the "First Steps" banner
    settle_badges(ws)                        # ...through the achievements process
    assert ws.ach.toast is not None
    assert ws._toast_until                   # the unlock armed the shell overlay
    drv.frame(1 / 30)                        # frame() draws the banner
    assert probe.distinct_pixels_in(drv.rgb888(), 3) > 4        # not a blank frame


def test_the_achievements_page_opens_from_about(tmp_path):
    """About's Achievements row says the count and opens the page, a page
    of Settings: its cards by game, the console's own first, a secret named
    Secret until found; Enter opens a badge's popover, back closes it, back
    again leaves the page."""
    ws = _ws(tmp_path)
    drv = host_app.ConsoleDriver(ws)
    ws.ach.seed({"first_open": 20261012})
    ws.open_settings()
    sl = ws.settings_layer
    sl._want = "Achievements"
    ws.mark_dirty()
    drv.frame(1 / 30)
    drv.frame(1 / 30)
    _tap(drv, sl._host.box("Achievements"))
    drv.frame(1 / 30)
    assert sl.page == "achievements" and sl.page_title() == "Achievements"
    drv.frame(1 / 30)
    page = sl.achievements
    titles = {c.title for _cap, rows in page._groups for _g, _o, cards in rows for c in cards}
    assert "First Steps" in titles and "Secret" in titles and "Secret Coder" not in titles
    assert [g[0] for g in page._groups] == ["Moybyte", "Harpoon Pop"]
    assert page._head == "1 of 15 found"
    assert sl._ach_words == "1 of 15"
    page._focus = "Badge system first_open"
    drv.frame(1 / 30)
    _press(drv, "a")
    assert page.pop is not None and page._pop_lines[1] == "Found 12 October"
    sl.back()
    assert page.pop is None and sl.page == "achievements"
    sl.back()
    assert sl.page is None


def test_a_goal_says_how_far(tmp_path):
    """A goal under way says its count on its card and, with its bar, in
    its popover."""
    ws = _ws(tmp_path)
    for k in ("a", "b", "c"):
        ws.ach.played(k)
    settle_badges(ws)
    ws.open_settings()
    sl = ws.settings_layer
    sl.open_page("achievements")
    ws.frame(1 / 30)
    page = sl.achievements
    card = page._by_id["Badge system play_five"]
    assert card.sub == "3 of 5" and card.goal == 5
    page._open(card)
    assert page._pop_lines == ("Play 5 carts", "3 of 5")
    ws.frame(1 / 30)


# -- Easter eggs ------------------------------------------------------------

def test_konami_egg_fires_and_awards(tmp_path):
    carts = str(tmp_path / "carts")
    ws = host_app.build_workstation(carts)
    drv = host_app.ConsoleDriver(ws)
    assert ws.screen == "launcher"
    armed = []
    hook = ws._achievement_unlocked
    ws._achievement_unlocked = lambda b: (armed.append(b), hook(b))
    for b in ws.ach_ui._KONAMI:
        _press(drv, b)
    assert ws.ach.has(A.SYSTEM, "konami")    # hidden "Secret Coder" awarded
    assert (A.HEAD_SECRET, "Secret Coder", "spark", True) in armed
    drv.frame(1 / 30)                        # the banner and its confetti render
    assert probe.distinct_pixels_in(drv.rgb888(), 3) > 4
    # Found a second time: nothing shows -- no banner, no beep.
    del armed[:]
    for b in ws.ach_ui._KONAMI:
        ws.ach_ui._konami_step(b)
    settle_badges(ws)
    assert armed == []
    # Persisted across reboot.
    ws2 = host_app.build_workstation(carts)
    assert ws2.ach.has(A.SYSTEM, "konami")


def test_konami_wrong_key_restarts(tmp_path):
    ws = _ws(tmp_path)
    drv = host_app.ConsoleDriver(ws)
    _press(drv, "up")
    _press(drv, "up")
    _press(drv, "left")                      # breaks the sequence
    assert ws.ach_ui._konami_pos == 0
    assert not ws.ach.has(A.SYSTEM, "konami")


def test_clock_tap_egg(tmp_path):
    ws = _ws(tmp_path)
    drv = host_app.ConsoleDriver(ws)
    # The egg hit regions come from the responsive layout (#39).
    for _ in range(ws.ach_ui._CLOCK_TAP_GOAL):
        _tap(drv, ws.layout.clock_hit())
    assert ws.ach.has(A.SYSTEM, "clock_tinker")     # hidden "Time Traveler"
    assert ws.ach.toast[1] == "Time Traveler"


def test_secret_door_egg_in_settings(tmp_path):
    ws = _ws(tmp_path)
    drv = host_app.ConsoleDriver(ws)
    ws.open_settings()
    drv.frame(1 / 30)
    bar = ws.bar_layer
    bx, by = bar.cell("settings", "zone")[0][1][:2]
    x, y, w, h = bar._zone_rect("settings")
    title = (bx + x + h + 4, by + y, w - h - 4, h)     # the zone past the chevron
    for _ in range(ws.ach_ui._SECRET_TAP_GOAL):
        _tap(drv, title)
    assert ws.ach.has(A.SYSTEM, "secret_door")      # hidden "Secret Finder"
    assert ws.ach.toast[1] == "Secret Finder"


def test_eggs_do_not_block_normal_nav(tmp_path):
    """An in-progress Konami sequence is a passive observer: the launcher still
    navigates normally (the egg never swallows input)."""
    ws = _ws(tmp_path)
    drv = host_app.ConsoleDriver(ws)
    start = ws.launcher.sel
    _press(drv, "right")                     # first key is "up", so this resets to 0
    assert ws.ach_ui._konami_pos == 0
    for _ in range(60):                      # the row glides, then settles
        drv.frame(1 / 30)
    assert ws.launcher.sel != start or len(ws.launcher.items) == 1


def test_a_settled_achievements_page_paints_nothing(tmp_path):
    """With no input the redraw gate draws no frame of the page, and a
    press that moves nothing draws one frame of the same pixels."""
    from ws_helpers import quiesce
    ws = _ws(tmp_path)
    ws.ach.seed({"first_open": 20261012, "first_run": 0})
    ws.open_settings()
    ws.settings_layer.open_page("achievements")
    quiesce(ws)
    drv = host_app.ConsoleDriver(ws)
    for _ in range(6):
        drv.frame(1 / 30)
    drawn = ws.frame_state.frames_drawn
    for _ in range(10):
        drv.frame(1 / 30)
    assert ws.frame_state.frames_drawn == drawn, "an idle Achievements page painted"
    before = bytes(ws.sys_canvas._buf)
    drv.press("left")                   # on the first card: nothing to move to
    for _ in range(6):
        drv.frame(1 / 30)
    assert ws.frame_state.frames_drawn <= drawn + 1, "one frame for the press, then none"
    assert bytes(ws.sys_canvas._buf) == before


def test_the_unlock_banner_over_a_game_sits_at_the_glass_top(tmp_path):
    """Over a fullscreen game, which has no bar, the banner's card stands at
    the glass's top centre; over the home, under the bar."""
    from runtime import ui_draw
    ws = _ws(tmp_path)
    got = []
    real = ui_draw.unlock
    ui_draw.unlock = lambda cv, th, cx, y, *a, **k: got.append((cx, y)) or real(cv, th, cx, y, *a, **k)
    try:
        ws.ach.award("decorator")
        settle_badges(ws)
        ws._draw_toast()
        assert got[-1] == (ws.sys_canvas.w // 2, ws.layout.cy + 6)
        ws.launcher.sel = [it.get("title") for it in ws.launcher.items].index("Star Catcher")
        ws.open()
        for _ in range(3):
            ws.frame(1 / 30)
        assert not ws._running_cart_shows_bar() and ws.wm.top_is_player()
        ws._draw_toast()
        assert got[-1] == (ws.sys_canvas.w // 2, 6)
    finally:
        ui_draw.unlock = real
