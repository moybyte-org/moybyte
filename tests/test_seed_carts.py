"""Tests for the new seed cartridges (#5, #19): Pixel Pet, Tiny Runner, Hop Quest
(platformer), and Tap Only Red (touch mini-game). Each loads through the SHARED
console via host_app (the same code path the device runs) and is driven headless
for several frames -- exercising attract-mode auto-play and, where relevant, the
input contract (buttons + the touch() api). Kept in its own file so it doesn't
collide with the existing test_v04_userland.py suite.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

SYSTEM_CARTS = ROOT / "system_carts"

NEW_CARTS = ("Pixel Pet", "Tiny Runner", "Hop Quest", "Tap Only Red")


def _open_cart(ws, title):
    for i, c in enumerate(ws.launcher.items):
        if c["title"] == title:
            ws.launcher.sel = i
            ws.open()
            return
    # Not in the launcher run-grid (a WALLPAPER leaves it, spec shell_ux_v1.md): still a
    # real editable cart in the store, so open it by reference (as ws.open() does).
    cart = next((c for c in ws._all_carts if c["title"] == title), None)
    if cart is None:  # pragma: no cover - guards a typo in the title
        raise AssertionError("seed cart not found: " + title)
    ws._open_workspace(cart)
    ws.run(ws.project, ws.launcher_layer)


def _run(ws, frames, dt=1 / 30):
    for _ in range(frames):
        ws.input.begin_frame()
        ws.frame(dt)


# -- the carts exist as well-formed .moy folders --------------------------

def test_seed_cart_folders_present_and_valid():
    import json

    for folder in ("pet", "tiny_runner", "platformer", "tap_red", "bubble_trouble"):
        d = SYSTEM_CARTS / (folder + ".moy")
        assert (d / "manifest.json").is_file(), folder
        assert (d / "main.py").is_file(), folder
        man = json.loads((d / "manifest.json").read_text(encoding="utf-8"))
        assert man["format"] == "moybyte-cart-v1"
        assert man["type"] == "game"
        assert man["main"] == "main.py"
        assert man["edit"], folder + " has no Make-it-mine cards"
        # main.py must at least define the cart entrypoints + be compilable
        src = (d / "main.py").read_text(encoding="utf-8")
        compile(src, str(d / "main.py"), "exec")
        assert "_init" in src and "_update" in src and "_draw" in src


# -- each loads through the shared console + runs headless without error ----

def test_all_new_carts_open_and_run_headless(tmp_path):
    from runtime import host_app

    ws = host_app.build_workstation(str(tmp_path / "carts"))
    titles = {c["title"] for c in ws.launcher.items}
    for title in NEW_CARTS:
        assert title in titles, "seed cart missing from gallery: " + title

    for title in NEW_CARTS:
        _open_cart(ws, title)
        assert ws.screen == "desktop", title  # _start succeeded (else still launcher)
        assert ws.ns is not None and ws._update and ws._draw
        _run(ws, 90)                            # attract-mode auto-play, no crash
        assert len(set(ws.canvas.buf)) > 1, title + " drew nothing"
        ws.go_home()


def test_carts_are_lively_in_attract_mode(tmp_path):
    # AUTOPLAY now defaults OFF (the kid plays), so the attract self-play is an
    # opt-in: turn it on, then the cart must animate itself with no input -- this
    # is the path the simulator demo GIF uses.
    from runtime import host_app

    ws = host_app.build_workstation(str(tmp_path / "carts"))
    for title in NEW_CARTS:
        _open_cart(ws, title)
        ws.config["autoplay"] = 1
        ws.apply()
        snaps = set()
        for _ in range(120):
            ws.input.begin_frame()
            ws.frame(1 / 30)
            snaps.add(bytes(ws.canvas.buf[::97]))   # cheap sparse snapshot
        assert len(snaps) > 3, title + " is static in attract mode"
        ws.go_home()


# -- per-cart gameplay sanity ------------------------------------------------

def test_pet_feeding_and_playing_raise_meters(tmp_path):
    from runtime import host_app

    ws = host_app.build_workstation(str(tmp_path / "carts"))
    _open_cart(ws, "Pixel Pet")
    ws.ns["food"] = 10.0
    ws.ns["joy"] = 10.0
    ws.input.set_held("left", True)             # LEFT = feed
    ws.input.begin_frame()
    ws.frame(1 / 30)
    ws.input.set_held("left", False)
    assert ws.ns["food"] > 10.0
    ws.input.set_held("right", True)            # RIGHT = play
    ws.input.begin_frame()
    ws.frame(1 / 30)
    ws.input.set_held("right", False)
    assert ws.ns["joy"] > 10.0


def test_tiny_runner_collision_resets_and_keeps_best(tmp_path):
    from runtime import host_app

    ws = host_app.build_workstation(str(tmp_path / "carts"))
    _open_cart(ws, "Tiny Runner")
    ws.ns["score"] = 50.0
    ws.ns["hero_y"] = 0.0
    ws.ns["vel"] = 0.0
    ws.ns["obs"][:] = [[ws.ns["HERO_X"], 10, 30]]   # an obstacle on top of the hero
    ws.input.begin_frame()
    ws.frame(1 / 30)
    assert ws.ns["score"] == 0.0                    # run reset on the hit
    assert ws.ns["best"] >= 50                       # best run preserved


def test_platformer_collect_all_and_reach_goal_wins(tmp_path):
    from runtime import host_app

    ws = host_app.build_workstation(str(tmp_path / "carts"))
    _open_cart(ws, "Hop Quest")
    for coin in ws.ns["coins"]:
        coin[2] = True                               # collect everything
    gx, gy = ws.ns["goal"]
    ts = ws.ns["TS"]
    ws.ns["px"] = float(gx * ts)
    ws.ns["py"] = float(gy * ts)
    ws.input.begin_frame()
    ws.frame(1 / 30)
    assert ws.ns["won"] > 0.0                        # standing on the goal -> win


def test_platformer_falling_off_respawns(tmp_path):
    from runtime import host_app

    ws = host_app.build_workstation(str(tmp_path / "carts"))
    _open_cart(ws, "Hop Quest")
    spawn = ws.ns["spawn"]
    ts = ws.ns["TS"]
    ws.ns["py"] = float(len(ws.ns["LEVEL"]) * ts + 200)   # well below the level
    ws.input.begin_frame()
    ws.frame(1 / 30)
    assert ws.ns["py"] == float(spawn[1] * ts)            # back at the spawn tile


def test_platformer_level_rows_are_equal_width():
    # The invisible side walls depend on every row being the same length; ragged
    # rows used to give some rows a wall 1 col closer than others (#19 review).
    import re

    src = (SYSTEM_CARTS / "platformer.moy" / "main.py").read_text(encoding="utf-8")
    block = src.split("LEVEL = [", 1)[1].split("]", 1)[0]
    rows = re.findall(r'"([^"]*)"', block)
    assert rows, "could not parse the LEVEL map"
    widths = {len(r) for r in rows}
    assert widths == {20}, "ragged LEVEL rows, widths seen: " + repr(sorted(widths))


def test_platformer_attract_collects_coins_and_completes(tmp_path):
    # The attract auto-pilot (no input) must make REAL progress, not sit in a
    # dead limit-cycle: over a long headless run it has to collect every coin and
    # actually complete a round (#19 review: it used to win 0/10 forever). This is
    # the same code path the device runs.
    from runtime import host_app

    ws = host_app.build_workstation(str(tmp_path / "carts"))
    _open_cart(ws, "Hop Quest")
    ws.config["autoplay"] = 1                             # opt into the auto-pilot
    ws.apply()
    ncoins = len(ws.ns["coins"])
    assert ncoins > 0

    max_got = 0
    full_wins = 0
    for _ in range(3000):                                 # long run, no input
        ws.input.begin_frame()                            # nothing held -> attract
        ws.frame(1 / 30)
        got = sum(1 for c in ws.ns["coins"] if c[2])
        if got > max_got:
            max_got = got
        if ws.ns["won"] >= 1.5:                           # the frame a round is won
            full_wins += 1

    assert max_got == ncoins, "attract only collected %d/%d coins" % (max_got, ncoins)
    assert full_wins >= 1, "attract never completed a round in 3000 frames"


def test_tap_red_scores_red_and_penalizes_other(tmp_path):
    from runtime import host_app

    ws = host_app.build_workstation(str(tmp_path / "carts"))
    _open_cart(ws, "Tap Only Red")
    col = ws.ns["col"]
    cx, cy = ws.canvas.w // 2, ws.canvas.h // 2

    # tap a RED bubble -> +1 score (touch() reads the pointer via the api)
    ws.ns["bubbles"][:] = [[float(cx), float(cy), 16, col("red"), True]]
    s0 = ws.ns["score"]
    ws.pointer.place(cx, cy)
    ws.pointer.click = True
    ws.input.begin_frame()
    ws.frame(1 / 30)
    assert ws.ns["score"] == s0 + 1

    # tap a non-red bubble -> a miss
    ws.ns["bubbles"][:] = [[float(cx), float(cy), 16, col("blue"), False]]
    m0 = ws.ns["misses"]
    ws.pointer.place(cx, cy)
    ws.pointer.click = True
    ws.input.begin_frame()
    ws.frame(1 / 30)
    assert ws.ns["misses"] == m0 + 1


# -- character art lives in EDITABLE sprite-sheet tiles (#15 follow-up) ------
#
# The carts whose characters used to be inline ASCII Images in main.py now keep
# them in the cart's sprites.moygfx, so the paint/sprite editor actually shows art
# to edit (the original bug: "I don't see the sprites that the games use"). These
# assert the sheet the editor loads is non-empty and the cart still runs.

# Folder -> the sprite tile ids its sprites.moygfx must paint (the editor surface).
CONVERTED_SHEETS = {
    "pet": (0, 1, 2),            # frog / cat / robot pet faces
    "tiny_runner": (0, 1),       # two runner heroes
    "platformer": (0, 1),        # two hop heroes
}
# Tap Only Red stays PRIMITIVE on purpose: its bubbles are variable-radius
# circles whose color IS the gameplay (red vs lure), set per-bubble at spawn --
# a fixed 8x8 tile can't express that, so it has no sprites.moygfx.
PRIMITIVE_CARTS = ("tap_red",)


def test_converted_carts_have_nonempty_sprite_sheets():
    from runtime.canvas import SpriteSheet

    for folder, tiles in CONVERTED_SHEETS.items():
        f = SYSTEM_CARTS / (folder + ".moy") / "sprites.moygfx"
        assert f.is_file(), folder + " is missing sprites.moygfx"
        hexs = f.read_text(encoding="utf-8")
        sheet = SpriteSheet.from_hex(hexs)
        assert not sheet.is_blank(), folder + " sprite sheet is blank (editor shows nothing)"
        for n in tiles:                              # each expected character tile is painted
            pix = [sheet.tget(n, lx, ly) for ly in range(8) for lx in range(8)]
            assert any(pix), "%s tile %d is blank" % (folder, n)


def test_primitive_cart_has_no_sprite_sheet():
    # The genuinely-primitive cart is intentionally left without a sheet.
    for folder in PRIMITIVE_CARTS:
        assert not (SYSTEM_CARTS / (folder + ".moy") / "sprites.moygfx").exists(), folder


def test_converted_carts_load_their_sheet_and_run_headless(tmp_path):
    from runtime import host_app

    title_for = {"pet": "Pixel Pet", "tiny_runner": "Tiny Runner",
                 "platformer": "Hop Quest"}
    ws = host_app.build_workstation(str(tmp_path / "carts"))
    for folder, tiles in CONVERTED_SHEETS.items():
        _open_cart(ws, title_for[folder])
        assert ws.screen == "desktop", folder            # started cleanly
        assert ws.cart_error is None, folder
        # the editor would load THIS sheet -> it must carry the painted tiles
        assert ws.sheet is not None and not ws.sheet.is_blank(), folder
        for n in tiles:
            assert ws.sheet.tile_image(n).pix.count(0) < 64, "%s tile %d blank" % (folder, n)
        _run(ws, 90)                                     # attract mode, no crash
        assert ws.cart_error is None, folder
        assert len(set(ws.canvas.buf)) > 1, folder
        ws.go_home()


def test_pet_picker_selects_a_sprite_tile(tmp_path):
    from runtime import host_app

    ws = host_app.build_workstation(str(tmp_path / "carts"))
    _open_cart(ws, "Pixel Pet")
    ws._open_menu()
    rows = ws.cards_layer._card_layout()
    pet = [r for r in rows if r["f"]["key"] == "pet"][0]
    assert pet["display"] == "sprite-tiles"
    cells = ws.cards_layer._choice_cells(pet)
    assert len(cells) == 3                               # frog / cat / robot
    _, (cx, cy, cw, ch) = cells[2]                       # tap the robot tile
    ws.pointer.place(cx + cw // 2, cy + ch // 2)
    ws.pointer.click = True
    ws.handle_pointer()
    assert ws.config["pet"] == 2
    ws.apply()
    assert ws.cart_error is None
    assert ws.ns["pet"] == 2                             # the running cart uses the picked tile


# -- the wallpaper carts also moved to editable sprite tiles (#15/#18) --------
#
# Space Desktop (frog/robot pet) and Ocean Desktop (fish) used to hardcode their
# character as an inline image() ASCII blob in main.py; now each keeps it in the
# cart's sprites.moygfx and draws with spr(<int tile id>, ...). These assert the
# sheet the editor loads is non-empty and the draw is a real sprite-tile draw.
import re  # noqa: E402

# Folder -> the sprite tile ids its sprites.moygfx must paint.
WALLPAPER_SHEETS = {
    "wallpaper_space": (0, 1),    # frog / robot pet faces (copied from star_catcher, #18)
    "ocean": (0,),                # the fish
}


def test_wallpaper_carts_have_nonempty_sprite_sheets():
    from runtime.canvas import SpriteSheet

    for folder, tiles in WALLPAPER_SHEETS.items():
        f = SYSTEM_CARTS / (folder + ".moy") / "sprites.moygfx"
        assert f.is_file(), folder + " is missing sprites.moygfx"
        sheet = SpriteSheet.from_hex(f.read_text(encoding="utf-8"))
        assert not sheet.is_blank(), folder + " sprite sheet is blank (editor shows nothing)"
        for n in tiles:
            pix = [sheet.tget(n, lx, ly) for ly in range(8) for lx in range(8)]
            assert any(pix), "%s tile %d is blank" % (folder, n)


def test_wallpaper_carts_draw_via_integer_sprite_tile():
    # The character is no longer an inline image(): main.py must spr() an integer
    # tile id (and not reference the old image()/FROG/ROBOT/FISH blobs).
    for folder in WALLPAPER_SHEETS:
        src = (SYSTEM_CARTS / (folder + ".moy") / "main.py").read_text(encoding="utf-8")
        assert "image(" not in src, folder + " still builds an inline image()"
        # at least one spr() call whose first arg is an int literal or the `pet` var
        assert re.search(r"spr\(\s*(?:\d+|pet)\b", src), folder + " has no spr(<int tile>, ...)"


def test_wallpaper_carts_load_their_sheet_and_run_headless(tmp_path):
    from runtime import host_app

    title_for = {"wallpaper_space": "Space Desktop", "ocean": "Ocean Desktop"}
    ws = host_app.build_workstation(str(tmp_path / "carts"))
    for folder, tiles in WALLPAPER_SHEETS.items():
        _open_cart(ws, title_for[folder])
        assert ws.screen == "desktop", folder
        assert ws.cart_error is None, folder
        assert ws.sheet is not None and not ws.sheet.is_blank(), folder
        for n in tiles:
            assert ws.sheet.tile_image(n).pix.count(0) < 64, "%s tile %d blank" % (folder, n)
        _run(ws, 90)                                     # attract mode, no crash
        assert ws.cart_error is None, folder
        assert len(set(ws.canvas.buf)) > 1, folder
        ws.go_home()


def test_space_pet_picker_selects_a_sprite_tile(tmp_path):
    from runtime import host_app

    ws = host_app.build_workstation(str(tmp_path / "carts"))
    _open_cart(ws, "Space Desktop")
    ws._open_menu()
    rows = ws.cards_layer._card_layout()
    pet = [r for r in rows if r["f"]["key"] == "pet"][0]
    assert pet["display"] == "sprite-tiles"
    cells = ws.cards_layer._choice_cells(pet)
    assert len(cells) == 2                               # frog / robot
    _, (cx, cy, cw, ch) = cells[1]                       # tap the robot tile
    ws.pointer.place(cx + cw // 2, cy + ch // 2)
    ws.pointer.click = True
    ws.handle_pointer()
    assert ws.config["pet"] == 1
    ws.apply()
    assert ws.cart_error is None
    assert ws.ns["pet"] == 1                             # the running cart uses the picked tile


# -- Bubble Trouble (#79 stage 1: single-player seed cart) -------------------

def test_bubble_trouble_opens_and_is_lively(tmp_path):
    from runtime import host_app

    ws = host_app.build_workstation(str(tmp_path / "carts"))
    _open_cart(ws, "Bubble Trouble")
    assert ws.screen == "desktop"                        # _start succeeded
    ws.config["autoplay"] = 1
    ws.apply()
    snaps = set()
    for _ in range(150):
        ws.input.begin_frame()
        ws.frame(1 / 30)
        snaps.add(bytes(ws.canvas.buf[::97]))            # bubbles always move -> lively
    assert len(snaps) > 3, "Bubble Trouble is static in attract mode"


def test_bubble_trouble_harpoon_pops_and_splits(tmp_path):
    # A harpoon fired under a size-2 bubble pops it (scores) and splits it into
    # two smaller (size-1) bubbles -- the core Pang mechanic.
    from runtime import host_app

    ws = host_app.build_workstation(str(tmp_path / "carts"))
    _open_cart(ws, "Bubble Trouble")
    ns = ws.ns
    ns["bubbles"][:] = [[160.0, 120.0, 0.0, 0.0, 2]]     # one size-2 bubble, centred
    ns["px"] = 160.0 - ns["PW"] / 2                      # player directly under it
    ns["harpoon"] = None
    ns["score"] = 0
    ws.input.set_held("a", True)                         # fire
    ws.input.begin_frame()
    ws.frame(1 / 30)
    ws.input.set_held("a", False)
    for _ in range(60):                                  # let the harpoon rise and hit
        ws.input.begin_frame()
        ws.frame(1 / 30)
        if ns["score"] > 0:
            break
    assert ns["score"] > 0, "harpoon never popped the bubble"
    assert len(ns["bubbles"]) == 2, "a size-2 bubble must split into two"
    assert all(b[4] == 1 for b in ns["bubbles"]), "children must be one size smaller"


def test_bubble_trouble_smallest_bubble_pops_outright(tmp_path):
    from runtime import host_app

    ws = host_app.build_workstation(str(tmp_path / "carts"))
    _open_cart(ws, "Bubble Trouble")
    ns = ws.ns
    ns["bubbles"][:] = [[160.0, 120.0, 0.0, 0.0, 0]]     # a size-0 bubble
    ns["px"] = 160.0 - ns["PW"] / 2
    ns["harpoon"] = None
    for _ in range(4):                                   # fire and let it rise
        ws.input.set_held("a", True)
        ws.input.begin_frame()
        ws.frame(1 / 30)
        ws.input.set_held("a", False)
        for _ in range(60):
            ws.input.begin_frame()
            ws.frame(1 / 30)
            if not ns["bubbles"]:
                break
        if not ns["bubbles"]:
            break
    assert not ns["bubbles"], "the smallest bubble must vanish, not split"


def test_bubble_trouble_contact_costs_a_life(tmp_path):
    from runtime import host_app

    ws = host_app.build_workstation(str(tmp_path / "carts"))
    _open_cart(ws, "Bubble Trouble")
    ns = ws.ns
    ns["lives"] = 3
    ns["invuln"] = 0.0
    ns["dead_t"] = 0.0
    ns["over"] = 0.0
    ns["bubbles"][:] = [[ns["px"] + ns["PW"] / 2, ns["PLAYER_TOP"] + 2, 0.0, 10.0, 1]]
    ws.input.begin_frame()
    ws.frame(1 / 30)
    assert ns["lives"] == 2, "touching a bubble must cost a life"


def test_bubble_trouble_high_score_persists(tmp_path):
    # The best score rides pmem(0), so it survives a restart of the cart.
    from runtime import host_app

    ws = host_app.build_workstation(str(tmp_path / "carts"))
    _open_cart(ws, "Bubble Trouble")
    ns = ws.ns
    ns["bubbles"][:] = [[160.0, 120.0, 0.0, 0.0, 0]]     # pop one -> scores -> new best
    ns["px"] = 160.0 - ns["PW"] / 2
    ns["harpoon"] = None
    ns["score"] = 0
    for _ in range(4):
        ws.input.set_held("a", True)
        ws.input.begin_frame()
        ws.frame(1 / 30)
        ws.input.set_held("a", False)
        for _ in range(60):
            ws.input.begin_frame()
            ws.frame(1 / 30)
            if ns["score"] > 0:
                break
        if ns["score"] > 0:
            break
    assert ns["score"] > 0
    assert ns["best"] == ns["score"]
    assert ns["pmem"](0) == ns["best"], "best score must be written to pmem"
