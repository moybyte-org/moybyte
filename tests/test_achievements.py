"""Tests for the achievements system + hidden Easter eggs (#21).

Driven through the SAME shared console the device runs (runtime.host_app +
ConsoleDriver: mouse == touch, arrows == trackball), plus direct unit tests of the
backend-agnostic Achievements helper and the kid_carts achievements.json store --
so these assert host==device behavior, not a host-only path.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# host_app registers the bare `audio`/`editors` module aliases that console.py
# imports (its frozen device names), so import it BEFORE console.
from runtime import host_app, kid_carts  # noqa: E402
from runtime import console as C  # noqa: E402


def _ws(tmp_path):
    return host_app.build_workstation(str(tmp_path / "carts"))


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


# -- the kid_carts achievements.json store ----------------------------------

def test_achievements_store_roundtrip(tmp_path):
    carts = str(tmp_path / "carts")
    assert kid_carts.load_achievements(carts) == []       # nothing earned yet
    kid_carts.save_achievements(["first_open", "konami"], carts)
    assert kid_carts.load_achievements(carts) == ["first_open", "konami"]


def test_achievements_store_dedupes_and_ignores_garbage(tmp_path):
    carts = str(tmp_path / "carts")
    kid_carts.save_achievements(["a", "a", "b"], carts)
    assert kid_carts.load_achievements(carts) == ["a", "b"]
    # A corrupt store must never crash -> empty list.
    path = kid_carts.achievements_store_path(carts)
    with open(path, "w") as f:
        f.write("{not json")
    assert kid_carts.load_achievements(carts) == []


# -- the Achievements helper (award-once + toast) ---------------------------

def test_award_once_and_toast():
    saved = []
    ach = C.Achievements(on_save=lambda ids: saved.append(list(ids)))
    assert ach.award("first_open") is True       # fresh unlock
    assert ach.award("first_open") is False      # repeat: no-op
    assert ach.has("first_open")
    assert ach.count() == 1
    assert saved == [["first_open"]]             # persisted exactly once
    # The fresh unlock raised a toast naming the achievement.
    assert ach.toast is not None
    assert ach.toast[0] == "first_open"
    assert ach.toast_active()


def test_unknown_achievement_is_not_awarded():
    ach = C.Achievements()
    assert ach.award("nope_not_real") is False
    assert ach.count() == 0


def test_play_five_counts_distinct_carts():
    ach = C.Achievements()
    for i in range(C._PLAY_GOAL - 1):
        ach.note("open", "cart_%d" % i)
    assert not ach.has("play_five")
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


# -- the toast + achievements view render without error ---------------------

def test_toast_renders(tmp_path):
    ws = _ws(tmp_path)
    drv = host_app.ConsoleDriver(ws)
    ws.launcher.sel = 0
    ws.open()                                # raises the "First Steps" toast
    assert ws.ach.toast_active()
    drv.frame(1 / 30)                        # frame() draws the toast overlay
    assert len(set(drv.rgb888())) > 4        # not a blank frame


def test_achievements_view_hides_secrets(tmp_path):
    ws = _ws(tmp_path)
    drv = host_app.ConsoleDriver(ws)
    ws.open_settings()
    # Open the achievements view via its trophy button, then render it.
    _tap(drv, C._SET_ACH)
    assert ws.show_achievements
    drv.frame(1 / 30)
    assert len(set(drv.rgb888())) > 4
    # A locked HIDDEN (Easter-egg) achievement must stay hidden in the catalog
    # contract: it carries hidden=True so the view shows "???" not its name.
    hidden = [a for a in C.ACHIEVEMENTS if a[3]]
    assert hidden and all(not ws.ach.has(a[0]) for a in hidden)
    # A tap dismisses the modal.
    drv.click(160, 120)
    drv.frame(1 / 30)
    assert not ws.show_achievements


# -- Easter eggs ------------------------------------------------------------

def test_konami_egg_fires_and_awards(tmp_path):
    carts = str(tmp_path / "carts")
    ws = host_app.build_workstation(carts)
    drv = host_app.ConsoleDriver(ws)
    assert ws.screen == "launcher"
    for b in C.Workstation._KONAMI:
        _press(drv, b)
    assert ws.ach.has("konami")              # hidden "Secret Coder" awarded
    assert ws.egg_msg is not None            # "OH! YOU FOUND ME!" popup is up
    drv.frame(1 / 30)                        # confetti + egg + toast all render
    assert len(set(drv.rgb888())) > 4
    # Persisted across reboot.
    ws2 = host_app.build_workstation(carts)
    assert ws2.ach.has("konami")


def test_konami_wrong_key_restarts(tmp_path):
    ws = _ws(tmp_path)
    drv = host_app.ConsoleDriver(ws)
    _press(drv, "up")
    _press(drv, "up")
    _press(drv, "left")                      # breaks the sequence
    assert ws._konami_pos == 0
    assert not ws.ach.has("konami")


def test_clock_tap_egg(tmp_path):
    ws = _ws(tmp_path)
    drv = host_app.ConsoleDriver(ws)
    # The egg hit regions now come from the responsive layout (#39); at the 320x240
    # baseline they are exactly the old _CLOCK_HIT / _SET_TITLE_HIT.
    for _ in range(ws._CLOCK_TAP_GOAL):
        _tap(drv, ws.layout.clock_hit())
    assert ws.ach.has("clock_tinker")        # hidden "Time Traveler"
    assert ws.egg_msg is not None


def test_secret_door_egg_in_settings(tmp_path):
    ws = _ws(tmp_path)
    drv = host_app.ConsoleDriver(ws)
    ws.open_settings()
    for _ in range(ws._SECRET_TAP_GOAL):
        _tap(drv, ws.layout.set_title_hit)
    assert ws.ach.has("secret_door")         # hidden "Secret Finder"
    assert ws.egg_msg is not None


def test_eggs_do_not_block_normal_nav(tmp_path):
    """An in-progress Konami sequence is a passive observer: the launcher still
    navigates normally (the egg never swallows input)."""
    ws = _ws(tmp_path)
    drv = host_app.ConsoleDriver(ws)
    start = ws.launcher.sel
    _press(drv, "right")                     # first key is "up", so this resets to 0
    assert ws._konami_pos == 0
    assert ws.launcher.sel != start or len(ws.launcher.items) == 1
