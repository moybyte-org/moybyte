"""Tests for the achievements system + hidden Easter eggs (#21).

Driven through the SAME shared console the device runs (runtime.host_app +
ConsoleDriver: mouse == touch, arrows == trackball), plus direct unit tests of the
backend-agnostic Achievements helper and the moy_carts achievements.json store --
so these assert host==device behavior, not a host-only path.
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

import canvas_probe as probe  # noqa: E402  (pixel-width-agnostic "it drew" probes)

# host_app registers the bare `audio`/`editors` module aliases that console.py
# imports (its frozen device names), so import it BEFORE console.
from runtime import host_app, moy_carts  # noqa: E402
from runtime import console as C  # noqa: E402


from ws_helpers import build_ws as _ws


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


# -- the moy_carts achievements.json store ----------------------------------

def test_achievements_store_roundtrip(tmp_path):
    carts = str(tmp_path / "carts")
    assert moy_carts.load_achievements(carts) is None     # nothing earned yet
    doc = {"found": {"first_open": 20261012, "konami": 0},
           "count": {"play_five": ["a", "b", "c"]}}
    moy_carts.save_achievements(doc, carts)
    assert moy_carts.load_achievements(carts) == doc


def test_achievements_store_is_strict(tmp_path):
    """No migration (CLAUDE.md): the ids-only document before this format,
    and any document not of it, reads as absent; a corrupt store recovers
    from its crash backup (#154)."""
    carts = str(tmp_path / "carts")
    path = moy_carts.achievements_store_path(carts)
    moy_carts.ensure_dirs(carts)
    with open(path, "w") as f:
        f.write('{"unlocked": ["first_open"]}')
    assert moy_carts.load_achievements(carts) is None
    for bad in ('{"v": 2, "found": ["first_open"]}', '{"v": 2, "found": {"a": "x"}}',
                '{"v": 3, "found": {}}'):
        with open(path, "w") as f:
            f.write(bad)
        assert moy_carts.load_achievements(carts) is None, bad
    moy_carts.save_achievements({"found": {"a": 1}, "count": {}}, carts)
    with open(path, "w") as f:
        f.write("{not json")
    assert moy_carts.load_achievements(carts) == {"found": {"a": 1}, "count": {}}


# -- the Achievements helper (award-once + toast) ---------------------------

def test_award_once_and_toast():
    saved = []
    armed = []
    ach = C.Achievements(on_save=saved.append, on_unlock=armed.append,
                         today=lambda: 20261012)
    assert ach.award("first_open") is True       # fresh unlock
    assert ach.award("first_open") is False      # repeat: no-op
    assert ach.has("first_open")
    assert ach.count() == 1
    assert ach.found_on("first_open") == 20261012
    assert saved == [{"found": {"first_open": 20261012}, "count": {}}]   # persisted once
    # The fresh unlock raised a toast naming the achievement, and fired the
    # unlock hook EXACTLY once -- the hook is what arms the shell's overlay
    # deadline (#209 landing B), so a second firing would re-raise a banner the
    # kid already saw and a missing one would leave the payload undrawn.
    assert ach.toast is not None
    assert ach.toast[0] == "first_open"
    assert armed == ["first_open"]


def test_unknown_achievement_is_not_awarded():
    ach = C.Achievements()
    assert ach.award("nope_not_real") is False
    assert ach.count() == 0


def test_play_five_counts_distinct_carts():
    ach = C.Achievements()
    for i in range(C._PLAY_GOAL - 1):
        ach.note("open", "cart_%d" % i)
    assert not ach.has("play_five")
    assert ach.progress("play_five") == (C._PLAY_GOAL - 1, C._PLAY_GOAL)
    ach.note("open", "cart_%d" % (C._PLAY_GOAL - 1))      # the 5th distinct cart
    assert ach.has("play_five")
    # Repeats of an already-played cart don't double-count.
    n = ach.count()
    ach.note("open", "cart_0")
    assert ach.count() == n


def test_toolbox_needs_all_three_editors():
    ach = C.Achievements()
    ach.note("editor", "code")
    ach.note("editor", "paint")
    assert not ach.has("toolbox")
    ach.note("editor", "cards")                           # not an editor -> ignored
    assert not ach.has("toolbox")
    ach.note("editor", "map")
    assert ach.has("toolbox")


def test_progress_and_days_survive_a_reboot():
    """A counter's progress and the day each badge was found are the
    document's, so a console that restarts says "3 of 5" and "Found 12
    October" as it did."""
    saved = []
    ach = C.Achievements(on_save=saved.append, today=lambda: 20261012)
    for k in ("a", "b", "c"):
        ach.note("open", k)
    ach.note("editor", "code")
    again = C.Achievements(saved[-1])
    assert again.progress("play_five") == (3, C._PLAY_GOAL)
    assert again.progress("toolbox") == (1, 3)
    assert again.found_on("first_open") == 20261012
    assert again.progress("first_run") is None


def test_a_badge_from_another_console_keeps_its_day():
    ach = C.Achievements(today=lambda: 20261020)
    assert ach.award("decorator", quiet=True, day=20261003)
    assert ach.found_on("decorator") == 20261003
    assert ach.toast is None                       # quiet: no banner


# -- integration: an event unlocks + persists across reboot -----------------

def test_open_cart_unlocks_and_persists(tmp_path):
    carts = str(tmp_path / "carts")
    ws = host_app.build_workstation(carts)
    assert not ws.ach.has("first_open")
    ws.launcher.sel = 0
    ws.open()
    assert ws.ach.has("first_open")
    # Reboot: a fresh workstation on the same store remembers it.
    ws2 = host_app.build_workstation(carts)
    assert ws2.ach.has("first_open")


def test_save_code_unlocks_code_wizard(tmp_path):
    carts = str(tmp_path / "carts")
    ws = host_app.build_workstation(carts)
    ws.launcher.sel = 0
    ws.open()
    ws.set_menu_view("code")
    assert ws.editor is not None
    ws.save_code()
    assert ws.ach.has("first_code")


def test_save_sprites_unlocks_little_artist(tmp_path):
    ws = _ws(tmp_path)
    ws.launcher.sel = 0
    ws.open()
    ws.set_menu_view("paint")
    ws.save_sprites()
    assert ws.ach.has("first_paint")


# -- the banner and the page render ------------------------------------------

def test_toast_renders(tmp_path):
    ws = _ws(tmp_path)
    drv = host_app.ConsoleDriver(ws)
    ws.launcher.sel = 0
    ws.open()                                # raises the "First Steps" banner
    assert ws.ach.toast is not None
    assert ws._toast_until                   # the unlock armed the shell overlay
    drv.frame(1 / 30)                        # frame() draws the banner
    assert probe.distinct_pixels_in(drv.rgb888(), 3) > 4        # not a blank frame


def test_the_achievements_page_opens_from_about(tmp_path):
    """About's Achievements row says the count and opens the page, a page
    of Settings: its cards in Found and To find, a secret named Secret
    until found; Enter opens a badge's popover, back closes it, back again
    leaves the page."""
    ws = _ws(tmp_path)
    drv = host_app.ConsoleDriver(ws)
    ws.ach.unlocked = {"first_open": 20261012}
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
    assert [g[0] for g in page._groups] == ["Found", "To find"]
    assert page._head == "1 of 11 found"
    page._focus = "Badge first_open"
    drv.frame(1 / 30)
    _press(drv, "a")
    assert page.pop is not None and page._pop_lines[1] == "Found 12 October"
    sl.back()
    assert page.pop is None and sl.page == "achievements"
    sl.back()
    assert sl.page is None


def test_a_counter_badge_says_how_far(tmp_path):
    ws = _ws(tmp_path)
    for k in ("a", "b", "c"):
        ws.ach.note("open", k)
    ws.open_settings()
    sl = ws.settings_layer
    sl.open_page("achievements")
    ws.frame(1 / 30)
    page = sl.achievements
    page._open(page._by_id["Badge play_five"])
    assert page._pop_lines == ("Play 5 carts", "3 of 5")


# -- Easter eggs ------------------------------------------------------------

def test_konami_egg_fires_and_awards(tmp_path):
    carts = str(tmp_path / "carts")
    ws = host_app.build_workstation(carts)
    drv = host_app.ConsoleDriver(ws)
    assert ws.screen == "launcher"
    armed = []
    hook = ws.ach._on_unlock
    ws.ach._on_unlock = lambda i: (armed.append(i), hook(i))
    for b in ws.ach_ui._KONAMI:
        _press(drv, b)
    assert ws.ach.has("konami")              # hidden "Secret Coder" awarded
    assert "konami" in armed                 # its banner, a secret's ("You found a secret!")
    drv.frame(1 / 30)                        # the banner and its confetti render
    assert probe.distinct_pixels_in(drv.rgb888(), 3) > 4
    # Found a second time: nothing shows -- no banner, no beep.
    del armed[:]
    for b in ws.ach_ui._KONAMI:
        ws.ach_ui._konami_step(b)
    assert armed == []
    # Persisted across reboot.
    ws2 = host_app.build_workstation(carts)
    assert ws2.ach.has("konami")


def test_konami_wrong_key_restarts(tmp_path):
    ws = _ws(tmp_path)
    drv = host_app.ConsoleDriver(ws)
    _press(drv, "up")
    _press(drv, "up")
    _press(drv, "left")                      # breaks the sequence
    assert ws.ach_ui._konami_pos == 0
    assert not ws.ach.has("konami")


def test_clock_tap_egg(tmp_path):
    ws = _ws(tmp_path)
    drv = host_app.ConsoleDriver(ws)
    # The egg hit regions come from the responsive layout (#39).
    for _ in range(ws.ach_ui._CLOCK_TAP_GOAL):
        _tap(drv, ws.layout.clock_hit())
    assert ws.ach.has("clock_tinker")        # hidden "Time Traveler"
    assert ws.ach.toast[0] == "clock_tinker"


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
    assert ws.ach.has("secret_door")         # hidden "Secret Finder"
    assert ws.ach.toast[0] == "secret_door"


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
    ws.ach.unlocked = {"first_open": 20261012, "first_run": 0}
    ws.open_settings()
    ws.settings_layer.open_page("achievements")
    quiesce(ws)
    drv = host_app.ConsoleDriver(ws)
    for _ in range(6):
        drv.frame(1 / 30)
    drawn = ws._frames_drawn
    for _ in range(10):
        drv.frame(1 / 30)
    assert ws._frames_drawn == drawn, "an idle Achievements page painted"
    before = bytes(ws.sys_canvas._buf)
    drv.press("left")                   # on the first card: nothing to move to
    for _ in range(6):
        drv.frame(1 / 30)
    assert ws._frames_drawn <= drawn + 1, "one frame for the press, then none"
    assert bytes(ws.sys_canvas._buf) == before


def test_the_unlock_banner_over_a_game_sits_at_the_glass_top(tmp_path):
    """Over a fullscreen game, which has no bar, the banner's card stands at
    the glass's top centre; over the home, under the bar."""
    from runtime import achievements_ui, ui_draw
    ws = _ws(tmp_path)
    got = []
    real = ui_draw.unlock
    ui_draw.unlock = lambda cv, th, cx, y, *a, **k: got.append((cx, y)) or real(cv, th, cx, y, *a, **k)
    try:
        ws.ach.award("decorator")
        achievements_ui.unlock_banner(ws)
        assert got[-1] == (ws.sys_canvas.w // 2, ws.layout.cy + 6)
        ws.launcher.sel = [it.get("title") for it in ws.launcher.items].index("Star Catcher")
        ws.open()
        for _ in range(3):
            ws.frame(1 / 30)
        assert not ws._running_cart_shows_bar() and ws.wm.top_is_player()
        achievements_ui.unlock_banner(ws)
        assert got[-1] == (ws.sys_canvas.w // 2, 6)
    finally:
        ui_draw.unlock = real
