"""Tests for the Letter Blitz cart (a letter-recognition shooting gallery for a
young kid): a stationary cannon pops wandering "letter tanks" that match a
FIND target, via either a touchscreen tap or the matching keyboard key. There
is no lose-state -- wrong picks just mute the sound and start a short input
cooldown. Mirrors the `_open_cart`/headless-run idiom in test_seed_carts.py.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

SYSTEM_CARTS = ROOT / "system_carts"


def _open_cart(ws, title):
    for i, c in enumerate(ws.launcher.items):
        if c["title"] == title:
            ws.launcher.sel = i
            break
    else:  # pragma: no cover - guards a typo in the title
        raise AssertionError("seed cart not found: " + title)
    ws.open()


def _run(ws, frames, dt=1 / 30):
    for _ in range(frames):
        ws.input.begin_frame()
        ws.frame(dt)


def _run_until_pop(ws, want, max_frames=40, dt=1 / 30):
    """Step frames until pop_count reaches `want` (the turret aims for a few
    frames, then the bullet flies ~0.18s -- exact frame counts would be
    brittle). Returns at the pop frame so hitstop is still observable."""
    for _ in range(max_frames):
        ws.input.begin_frame()
        ws.frame(dt)
        if ws.ns["pop_count"] >= want:
            return
    raise AssertionError("no pop within %d frames" % max_frames)


def test_letter_blitz_folder_present_and_valid():
    import json

    d = SYSTEM_CARTS / "letter_blitz.moy"
    assert (d / "manifest.json").is_file()
    assert (d / "main.py").is_file()
    man = json.loads((d / "manifest.json").read_text(encoding="utf-8"))
    assert man["format"] == "moybyte-cart-v1"
    assert man["type"] == "game"
    assert man["main"] == "main.py"
    assert man["edit"], "no Make-it-mine cards"
    src = (d / "main.py").read_text(encoding="utf-8")
    compile(src, str(d / "main.py"), "exec")
    assert "_init" in src and "_update" in src and "_draw" in src
    # a pure vector + procedural-glyph cart, like beeper.moy -- no sprite sheet
    assert not (d / "sprites.moygfx").exists()


def test_letter_blitz_opens_and_runs_headless(tmp_path):
    from runtime import host_app

    ws = host_app.build_workstation(str(tmp_path / "carts"))
    titles = {c["title"] for c in ws.launcher.items}
    assert "Letter Blitz" in titles

    _open_cart(ws, "Letter Blitz")
    assert ws.screen == "desktop"
    assert ws.ns is not None and ws._update and ws._draw
    _run(ws, 300)  # ~10s headless, tanks wandering, no input -- must not crash


def test_letter_blitz_correct_touch_tap_scores(tmp_path):
    from runtime import host_app

    ws = host_app.build_workstation(str(tmp_path / "carts"))
    _open_cart(ws, "Letter Blitz")

    ws.ns["tanks"][:] = [[100.0, 100.0, 0.0, 0.0, "A", 0.0, 5.0, 0, 0, 0, 5.0]]
    ws.ns["wanted"] = "A"
    lifetime0 = ws.ns["lifetime"]
    pop0 = ws.ns["pop_count"]

    ws.pointer.place(100, 100)
    ws.pointer.click = True
    ws.input.begin_frame()
    ws.frame(1 / 30)
    ws.pointer.click = False  # a real tap is a one-frame edge, not a hold
    # the pick starts the turret SWIVELING toward the tank -- no instant shot
    assert ws.ns["aiming"] is not None
    assert ws.ns["bullet"] is None

    _run_until_pop(ws, pop0 + 1)  # aim locks, the shot flies, the pop lands
    assert ws.ns["lifetime"] == lifetime0 + 1
    assert ws.ns["bullet"] is None
    assert ws.ns["aiming"] is None


def test_letter_blitz_correct_keypress_scores(tmp_path):
    from runtime import host_app

    ws = host_app.build_workstation(str(tmp_path / "carts"))
    _open_cart(ws, "Letter Blitz")

    ws.ns["tanks"][:] = [[100.0, 100.0, 0.0, 0.0, "B", 0.0, 5.0, 0, 0, 0, 5.0]]
    ws.ns["wanted"] = "B"
    lifetime0 = ws.ns["lifetime"]

    ws.input.last_key = ord("b")  # lowercase, as a real keypress would send
    ws.input.begin_frame()
    ws.frame(1 / 30)
    ws.input.last_key = 0

    _run(ws, 25)  # aim + shot + pop
    assert ws.ns["lifetime"] == lifetime0 + 1


def test_letter_blitz_wrong_pick_no_score_and_cooldown(tmp_path):
    from runtime import host_app

    ws = host_app.build_workstation(str(tmp_path / "carts"))
    _open_cart(ws, "Letter Blitz")

    ws.ns["tanks"][:] = [[150.0, 120.0, 0.0, 0.0, "B", 0.0, 5.0, 0, 0, 0, 5.0]]
    ws.ns["wanted"] = "Z"
    ws.ns["cooldown_t"] = 0.0
    lifetime0 = ws.ns["lifetime"]

    ws.pointer.place(150, 120)
    ws.pointer.click = True
    ws.input.begin_frame()
    ws.frame(1 / 30)

    assert ws.ns["lifetime"] == lifetime0        # no score for a wrong pick
    assert ws.ns["bullet"] is None                # no explosion
    assert ws.ns["aiming"] is None                # the turret doesn't even turn
    assert ws.ns["cooldown_t"] > 0.0               # mashing is blocked briefly

    # a second tap during the cooldown is a no-op even on the CORRECT tank
    ws.ns["tanks"][0][4] = "Z"
    ws.pointer.place(150, 120)
    ws.pointer.click = True
    ws.input.begin_frame()
    ws.frame(1 / 30)
    assert ws.ns["bullet"] is None
    assert ws.ns["aiming"] is None
    assert ws.ns["lifetime"] == lifetime0


def test_letter_blitz_letters_unlocked_stepper_expands_pool(tmp_path):
    from runtime import host_app

    ws = host_app.build_workstation(str(tmp_path / "carts"))
    _open_cart(ws, "Letter Blitz")

    assert ws.ns["_unlocked"]() == "ABCDEF"       # default starting batch

    ws.config["letters_unlocked"] = 26
    assert ws.ns["_unlocked"]() == "ABCDEFGHIJKLMNOPQRSTUVWXYZ"

    # every glyph a tank could show must actually be drawable
    for ch in ws.ns["ALPHABET"]:
        img = ws.ns["_glyph"](ch)
        assert img.w == 5 and img.h == 7, ch


def test_letter_blitz_pop_celebration_fx(tmp_path):
    """A correct pop must FEEL like a jackpot: hitstop, rings, the letter
    floating up, and a queued arpeggio -- not just a lone beep."""
    from runtime import host_app

    ws = host_app.build_workstation(str(tmp_path / "carts"))
    _open_cart(ws, "Letter Blitz")

    ws.ns["tanks"][:] = [[104.0, 104.0, 0.0, 0.0, "A", 0.0, 5.0, 0, 0, 0, 5.0]]
    ws.ns["wanted"] = "A"

    ws.pointer.place(104, 104)
    ws.pointer.click = True
    ws.input.begin_frame()
    ws.frame(1 / 30)
    ws.pointer.click = False
    _run_until_pop(ws, 1)  # aim locks, bullet travels, the pop lands

    assert ws.ns["pop_count"] == 1
    assert ws.ns["freeze_t"] > 0.0            # hitstop
    assert ws.ns["rings"]                      # explosion + spawn rings
    assert ws.ns["rising"]                     # the letter floats up
    assert ws.ns["rising"][0][0] == "A"
    assert ws.ns["melody"]                     # arpeggio notes still queued
    _run(ws, 60)                               # fx must all decay cleanly
    assert not ws.ns["rings"] and not ws.ns["rising"] and not ws.ns["melody"]


def test_letter_blitz_milestone_every_ten_finds(tmp_path):
    from runtime import host_app

    ws = host_app.build_workstation(str(tmp_path / "carts"))
    _open_cart(ws, "Letter Blitz")

    ws.ns["tanks"][:] = [[104.0, 104.0, 0.0, 0.0, "C", 0.0, 5.0, 0, 0, 0, 5.0]]
    ws.ns["wanted"] = "C"
    ws.ns["lifetime"] = 9  # next find is the 10th

    ws.pointer.place(104, 104)
    ws.pointer.click = True
    ws.input.begin_frame()
    ws.frame(1 / 30)
    ws.pointer.click = False
    _run_until_pop(ws, 1)

    assert ws.ns["lifetime"] == 10
    assert any(f[0] == "10 FOUND!" for f in ws.ns["fx_text"])


def test_letter_blitz_tanks_lane_snap_on_retarget(tmp_path):
    """Battle-City rule: when a tank picks a direction it snaps its
    perpendicular axis to the 16px lane center, so it never clips corners."""
    from runtime import host_app

    ws = host_app.build_workstation(str(tmp_path / "carts"))
    _open_cart(ws, "Letter Blitz")

    # off-lane on both axes, retarget timer already expired
    ws.ns["tanks"][:] = [[100.0, 100.0, 0.0, 0.0, "A", 0.0, 0.0, 0, 0, 0, 5.0]]
    ws.input.begin_frame()
    ws.frame(1 / 30)

    t = ws.ns["tanks"][0]
    # whichever axis is now perpendicular to travel sits on a lane center
    # (x lanes: c*16+8; y lanes: 32+r*16+8 -- both are ==8 mod 16)
    assert t[0] % 16 == 8.0 or t[1] % 16 == 8.0


def test_letter_blitz_arena_refresh_without_trace(tmp_path):
    """With TRACE toggled off, the maze still rebuilds every TRACE_EVERY pops
    (minus any brick that would land on a tank) so the arena never decays."""
    from runtime import host_app

    ws = host_app.build_workstation(str(tmp_path / "carts"))
    _open_cart(ws, "Letter Blitz")
    ws.config["trace_bonus"] = 0

    ws.ns["walls"].clear()  # a fully chewed-up arena
    ws.ns["pop_count"] = 3  # the next pop is the 4th
    ws.ns["tanks"][:] = [[104.0, 104.0, 0.0, 0.0, "A", 0.0, 5.0, 0, 0, 0, 5.0]]
    ws.ns["wanted"] = "A"

    ws.pointer.place(104, 104)
    ws.pointer.click = True
    ws.input.begin_frame()
    ws.frame(1 / 30)
    ws.pointer.click = False
    _run_until_pop(ws, 4)

    assert ws.ns["mode"] == "gallery"  # no trace screen when it's off
    assert len(ws.ns["walls"]) > 0     # ...but the bricks came back
    # Battle-City generator invariant: the outer ring stays an open patrol
    # lane, whatever the random layout inside
    for (r, c) in ws.ns["walls"]:
        assert 1 <= r <= 8 and 1 <= c <= 18, (r, c)


def _tap(ws, x, y):
    ws.pointer.place(x, y)
    ws.pointer.click = True
    ws.input.begin_frame()
    ws.frame(1 / 30)
    ws.pointer.click = False


def test_letter_blitz_streak_and_record_board(tmp_path):
    """Correct picks build a streak; a wrong pick that ends a record run flips
    into the arcade initials screen (celebration, not punishment) and persists
    best + initials via pmem."""
    from runtime import host_app

    ws = host_app.build_workstation(str(tmp_path / "carts"))
    _open_cart(ws, "Letter Blitz")

    ws.ns["tanks"][:] = [[104.0, 104.0, 0.0, 0.0, "A", 0.0, 9.0, 0, 0, 0, 9.0]]
    ws.ns["wanted"] = "A"
    _tap(ws, 104, 104)
    _run_until_pop(ws, 1)
    assert ws.ns["streak"] == 1

    # a wrong pick below the record threshold just resets the streak
    ws.ns["tanks"][:] = [[104.0, 104.0, 0.0, 0.0, "B", 0.0, 9.0, 0, 0, 0, 9.0]]
    ws.ns["wanted"] = "Z"
    _run(ws, 10)  # let the pop's hitstop fully elapse first
    _tap(ws, 104, 104)
    assert ws.ns["streak"] == 0
    assert ws.ns["mode"] == "gallery"

    # a record-worthy streak opens the initials screen instead
    ws.ns["streak"] = 5
    ws.ns["best_streak"] = 0
    ws.ns["cooldown_t"] = 0.0
    _tap(ws, 104, 104)
    assert ws.ns["mode"] == "record"

    # tap A, B, C on the grid, then OK (grid cell centers, _update_record math)
    for k in (0, 1, 2, 27):
        cx = 41 + (k % 7) * 34 + 17
        cy = 118 + (k // 7) * 26 + 13
        _tap(ws, cx, cy)
    assert ws.ns["mode"] == "gallery"
    assert ws.ns["best_streak"] == 5
    assert ws.ns["initials"] == "ABC"
    assert ws.ns["pmem"](1) == 5
    assert ws.ns["streak"] == 0


def test_letter_blitz_boss_every_26_finds(tmp_path):
    """The 26th find drops the walls and rolls in a 3-hp double-size boss; the
    kill rebuilds the maze in the next mood and counts as a find."""
    from runtime import host_app

    ws = host_app.build_workstation(str(tmp_path / "carts"))
    _open_cart(ws, "Letter Blitz")

    ws.ns["lifetime"] = 25
    ws.ns["tanks"][:] = [[104.0, 104.0, 0.0, 0.0, "A", 0.0, 9.0, 0, 0, 0, 9.0]]
    ws.ns["wanted"] = "A"
    _tap(ws, 104, 104)
    _run_until_pop(ws, 1)

    boss = ws.ns["boss"]
    assert boss is not None                  # lifetime hit 26 -> boss time
    assert len(ws.ns["walls"]) == 0          # the walls came down for it
    assert ws.ns["wanted"] == boss[4]        # the boss letter is the target
    assert boss[11] == 3

    mood0 = ws.ns["mood_idx"]
    for hp_left in (2, 1, 0):
        _run(ws, 12)                         # hitstop + entrance fanfare settle
        boss = ws.ns["boss"]
        boss[2] = boss[3] = 0.0              # pin it for a deterministic tap
        boss[6] = 99.0
        _tap(ws, boss[0], boss[1])
        assert ws.ns["aiming"] is boss
        _run(ws, 25)                         # aim, shot, hit
        if hp_left:
            assert ws.ns["boss"][11] == hp_left
        else:
            assert ws.ns["boss"] is None     # third hit pops it

    assert ws.ns["lifetime"] == 27           # the boss counts as a find
    assert len(ws.ns["walls"]) > 0           # fresh maze...
    assert ws.ns["mood_idx"] == mood0 + 1    # ...in the next arena mood


def test_letter_blitz_trace_bonus_every_fourth_pop(tmp_path):
    from runtime import host_app

    ws = host_app.build_workstation(str(tmp_path / "carts"))
    _open_cart(ws, "Letter Blitz")

    # pop 4 tanks in a row. Pin a single stationary tank showing the wanted
    # letter each round -- with the full wandering pack, another tank can
    # drift into the 10px tap radius and steal the hit as a wrong pick.
    # Real taps are a one-frame event (the driver clears its edge each frame,
    # host_app.py:753/761) -- ws.pointer.click must be reset the same way here,
    # or it reads as a stuck "held" tap for every frame that follows.
    for i in range(4):
        wanted = ws.ns["wanted"]
        ws.ns["tanks"][:] = [[104.0, 104.0, 0.0, 0.0, wanted, 0.0, 9.0, 0, 0, 0, 9.0]]
        ws.pointer.place(104, 104)
        ws.pointer.click = True
        ws.input.begin_frame()
        ws.frame(1 / 30)
        ws.pointer.click = False
        _run_until_pop(ws, i + 1)  # aim + travel + hitstop, then the pop
        _run(ws, 6)                # let the hitstop fully elapse
        ws.ns["cooldown_t"] = 0.0

    assert ws.ns["pop_count"] == 4
    assert ws.ns["mode"] == "trace"

    # drawing during the bonus: a HELD drag lays one continuous crayon stroke
    # (touch()'s 4th `held` element), not gallery hits and not one-dot stamps
    ws.pointer.place(120, 120)
    ws.pointer.click = True
    ws.pointer.down = True
    ws.input.begin_frame()
    ws.frame(1 / 30)
    ws.pointer.click = False
    for x in range(126, 181, 6):  # drag right across the glyph's middle band
        ws.pointer.place(x, 120)
        ws.input.begin_frame()
        ws.frame(1 / 30)
    ws.pointer.down = False
    strokes = ws.ns["trace_strokes"]
    assert len(strokes) == 1                 # one stroke, following the drag
    assert len(strokes[0]) >= 5
    assert ws.ns["trace_covered"]            # it crossed the letter's ink

    _run(ws, 340)  # TRACE_DURATION (10s) at 1/30 -- must return to gallery on its own
    assert ws.ns["mode"] == "gallery"


def test_letter_blitz_trace_completes_early_when_letter_drawn(tmp_path):
    """Tracing (nearly) every ink cell of the guide letter ends the bonus with
    the GREAT! cheer early -- the reward for actually drawing the letter."""
    from runtime import host_app

    ws = host_app.build_workstation(str(tmp_path / "carts"))
    _open_cart(ws, "Letter Blitz")

    # force a trace round directly with a deterministic letter
    ws.ns["mode"] = "trace"
    ws.ns["trace_letter"] = "L"
    ws.ns["trace_t"] = ws.ns["TRACE_DURATION"]
    ws.ns["trace_strokes"] = []
    ws.ns["trace_covered"] = set()
    ws.ns["trace_was_held"] = False
    ws.ns["trace_done"] = False

    gx, gy = ws.ns["_trace_origin"]()
    sc = ws.ns["TRACE_SCALE"]
    rows = ws.ns["GLYPH_ROWS"]["L"]
    ws.pointer.down = True                   # finger stays on the glass
    for r in range(7):
        for c in range(5):
            if rows[r][c] == "#":
                ws.pointer.place(gx + c * sc + sc // 2, gy + r * sc + sc // 2)
                ws.input.begin_frame()
                ws.frame(1 / 30)
    ws.pointer.down = False

    assert ws.ns["trace_t"] <= ws.ns["TRACE_GREAT"]   # cheer triggered early
    ws.input.begin_frame()
    ws.frame(1 / 30)
    assert ws.ns["trace_done"]
    _run(ws, 45)                              # the cheer plays out...
    assert ws.ns["mode"] == "gallery"         # ...and we're back in the gallery


def test_letter_blitz_exit_button_quits_to_launcher(tmp_path):
    """Letter Blitz is a TEXT-mode typing game (it calls textmode(True)), so the console's
    hold-BACKSPACE game exit can't reach it (BACKSPACE is a typed delete, and the T-Deck
    keyboard has no autorepeat). The cart provides its OWN exit: a tap-anytime X button in
    the top-right corner that calls quit(). Tapping it pops back to the launcher."""
    from runtime import host_app

    ws = host_app.build_workstation(str(tmp_path / "carts"))
    _open_cart(ws, "Letter Blitz")
    assert ws.screen == "desktop"
    assert ws.input.text_mode is True              # the typing game asked for text input

    ex, ey, ew, eh = ws.ns["_exit_rect"]()         # the X's rect (top-right corner)
    _tap(ws, ex + ew // 2, ey + eh // 2)           # tap its center
    assert ws.screen == "launcher"                 # quit() popped the game home


def test_letter_blitz_exit_button_sits_above_the_maze(tmp_path):
    """The exit X lives in the strip above the HUD (row < HUD_Y) and above the maze (which
    starts at GRID_Y0), so it never overlaps a letter-tank -- a tap there is unambiguous and
    the console's own 18px top bar (which auto-hides while a cart plays) is gone."""
    from runtime import host_app

    ws = host_app.build_workstation(str(tmp_path / "carts"))
    _open_cart(ws, "Letter Blitz")
    ex, ey, ew, eh = ws.ns["_exit_rect"]()
    assert ey + eh <= ws.ns["HUD_Y"]               # entirely above the cart's HUD band
    assert ey + eh <= ws.ns["GRID_Y0"]             # ...and above the maze (no tank overlap)
