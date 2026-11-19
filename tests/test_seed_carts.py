"""Tests for the seed game cartridges (#5, #19): Star Catcher, Harpoon Pop and
Tap Only Red (the touch mini-game made with blocks, whose play
tests/test_blocks.py drives). Each loads through the SHARED
console via host_app (the same code path the device runs) and is driven headless
for several frames -- exercising attract-mode auto-play and, where relevant, the
input contract (buttons + the touch() api). Kept in its own file so it doesn't
collide with the existing test_v04_userland.py suite.
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

import canvas_probe as probe  # noqa: E402  (pixel-width-agnostic "it drew" probes)

SYSTEM_CARTS = ROOT / "system_carts"

NEW_CARTS = ("Star Catcher", "Tap Only Red")


from ws_helpers import open_cart as _open_cart


def _run(ws, frames, dt=1 / 30):
    for _ in range(frames):
        ws.input.begin_frame()
        ws.frame(dt)


# -- the carts exist as well-formed .moy folders --------------------------

def test_seed_cart_folders_present_and_valid():
    import json

    for folder in ("moybyte.star_catcher", "moybyte.harpoon_pop"):
        d = SYSTEM_CARTS / (folder + ".moy")
        assert (d / "manifest.json").is_file(), folder
        assert (d / "main.py").is_file(), folder
        man = json.loads((d / "manifest.json").read_text(encoding="utf-8"))
        assert man["format"] == "moy-1"          # SPEC.md 3.1
        assert man["moybyte"]["type"] == "game"
        assert man["main"] == "main.py"
        assert man["moybyte"]["edit"], folder + " has no Make-it-mine cards"
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
        assert ws.ns is not None and ws.run.player.update and ws.run.player.draw
        _run(ws, 90)                            # attract-mode auto-play, no crash
        assert probe.drew_something(ws.run.canvas), title + " drew nothing"
        ws.home.go_home()


def test_carts_are_lively_in_attract_mode(tmp_path):
    # AUTOPLAY now defaults OFF (the kid plays), so the attract self-play is an
    # opt-in: turn it on, then the cart must animate itself with no input -- this
    # is the path the simulator demo GIF uses.
    from runtime import host_app

    ws = host_app.build_workstation(str(tmp_path / "carts"))
    for title in NEW_CARTS:
        _open_cart(ws, title)
        ws.run.project.config["autoplay"] = 1
        ws.saves.apply()
        snaps = set()
        for _ in range(120):
            ws.input.begin_frame()
            ws.frame(1 / 30)
            snaps.add(probe.pixel_sample(ws.run.canvas, 97))   # cheap sparse snapshot
        assert len(snaps) > 3, title + " is static in attract mode"
        ws.home.go_home()


# -- character art lives in EDITABLE sprite-sheet tiles (#15 follow-up) ------
#
# The carts whose characters used to be inline ASCII Images in main.py now keep
# them in the cart's sprites.moygfx, so the paint/sprite editor actually shows art
# to edit (the original bug: "I don't see the sprites that the games use"). These
# assert the sheet the editor loads is non-empty and the cart still runs.

# Folder -> the sprite tile ids its sprites.moygfx must paint (the editor surface).
# The first pass converted the HEROES; the 2026-09 pass converted what the hero
# interacts with -- the collectible, the obstacle and the goal were still circ/rect
# in code, which is the same defect one rung down.
CONVERTED_SHEETS = {
    "moybyte.star_catcher": (0, 1, 2, 3, 6),   # catchers, star, basket span, life heart
    "moybyte.harpoon_pop": (0, 2, 5, 8, 12, 64),   # both hunters, then one tile per bubble size
    "moybyte.letter_blitz": (1, 2, 3, 16, 18),     # brick, turret, star, the two tank spans
    "moybyte.tap_only_red": (1, 2, 3, 4, 5),       # the red bubble, then the four lures
    # The bench twins' folded ray scene: one wall texture per maze tile id, and
    # +16 is that wall's dim side-on face (they were ray_test/ray_lua's tiles).
    "moybyte.bench": (64, 65, 66, 67, 80, 81, 82, 83),
    "moybyte.bench_lua": (64, 65, 66, 67, 80, 81, 82, 83),
}
# Letter Blitz is the partial case worth stating: its PROPS are sheet tiles above,
# but its 26 letters deliberately stay GLYPH_ROWS in main.py -- the trace bonus
# reads which pixels are ink and no verb reads the sheet back (there is no sget),
# so a sheet copy would be a second source of truth, not an editable one.
GLYPH_DATA_CARTS = {"moybyte.letter_blitz": "GLYPH_ROWS"}

def test_converted_carts_have_nonempty_sprite_sheets():
    from runtime.editors_sheet import SpriteSheet

    for folder, tiles in CONVERTED_SHEETS.items():
        f = SYSTEM_CARTS / (folder + ".moy") / "sprites.moygfx"
        assert f.is_file(), folder + " is missing sprites.moygfx"
        hexs = f.read_text(encoding="utf-8")
        sheet = SpriteSheet.from_hex(hexs)
        assert not sheet.is_blank(), folder + " sprite sheet is blank (editor shows nothing)"
        for n in tiles:                              # each expected character tile is painted
            pix = [sheet.tget(n, lx, ly) for ly in range(8) for lx in range(8)]
            assert any(pix), "%s tile %d is blank" % (folder, n)


def test_glyph_data_carts_keep_their_table_in_source():
    # The one table that must NOT become sheet tiles: the cart reads its pixels as
    # DATA, and nothing reads the sheet back, so moving it would fork the source.
    for folder, name in GLYPH_DATA_CARTS.items():
        src = (SYSTEM_CARTS / (folder + ".moy") / "main.py").read_text(encoding="utf-8")
        assert name + " = {" in src, "%s lost %s" % (folder, name)


def test_converted_carts_draw_no_hand_rolled_art():
    # The pass this ratchet records: the collectible/obstacle/goal art moved out of
    # main.py, so these carts no longer reach for a primitive where a tile belongs.
    for folder in ("moybyte.star_catcher", "moybyte.harpoon_pop"):
        src = (SYSTEM_CARTS / (folder + ".moy") / "main.py").read_text(encoding="utf-8")
        assert "spr(" in src, folder + " draws no sprites"


def test_converted_carts_load_their_sheet_and_run_headless(tmp_path):
    from runtime import host_app

    title_for = {"moybyte.star_catcher": "Star Catcher", "moybyte.harpoon_pop": "Harpoon Pop",
                 "moybyte.letter_blitz": "Letter Blitz", "moybyte.bench": "Bench",
                 "moybyte.bench_lua": "Bench Lua", "moybyte.tap_only_red": "Tap Only Red"}
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
        assert probe.drew_something(ws.run.canvas), folder
        ws.home.go_home()


# -- the wallpaper carts also moved to editable sprite tiles (#15/#18) --------
#
# Space Desktop (frog/robot pet) used to hardcode its character as an inline
# image() ASCII blob in main.py; now it keeps it in the cart's sprites.moygfx and draws with spr(<int tile id>, ...). These assert the
# sheet the editor loads is non-empty and the draw is a real sprite-tile draw.
import re  # noqa: E402

# Folder -> the sprite tile ids its sprites.moygfx must paint.
WALLPAPER_SHEETS = {
    "moybyte.space_desktop": (0, 1),    # frog / robot pet faces (copied from star_catcher, #18)
}


def test_wallpaper_carts_have_nonempty_sprite_sheets():
    from runtime.editors_sheet import SpriteSheet

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

    title_for = {"moybyte.space_desktop": "Space Desktop"}
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
        assert probe.drew_something(ws.run.canvas), folder
        ws.home.go_home()


def test_space_pet_picker_selects_a_sprite_tile(tmp_path):
    from runtime import host_app
    from ws_helpers import settings_tile

    ws = host_app.build_workstation(str(tmp_path / "carts"))
    _open_cart(ws, "Space Desktop")
    ws.text.open_menu()
    pet = [f for f in ws.cart["edit"] if f["key"] == "pet"][0]
    assert pet["display"] == "sprite-tiles"
    assert len(pet["choices"]) == 2
    cx, cy = settings_tile(ws, "pet", 1)
    ws.pointer.place(cx, cy)
    ws.pointer.click = True
    ws.router.handle_pointer()
    assert ws.run.project.config["pet"] == 1
    ws.saves.apply()
    assert ws.cart_error is None
    assert ws.ns["pet"] == 1                             # the running cart uses the picked tile


# -- Harpoon Pop (#79 stage 1: single-player seed cart) -------------------

def test_harpoon_pop_opens_and_is_lively(tmp_path):
    from runtime import host_app

    ws = host_app.build_workstation(str(tmp_path / "carts"))
    _open_cart(ws, "Harpoon Pop")
    assert ws.screen == "desktop"                        # _start succeeded
    ws.run.project.config["autoplay"] = 1
    ws.saves.apply()
    snaps = set()
    for _ in range(150):
        ws.input.begin_frame()
        ws.frame(1 / 30)
        snaps.add(probe.pixel_sample(ws.run.canvas, 97))     # bubbles always move -> lively
    assert len(snaps) > 3, "Harpoon Pop is static in attract mode"


def test_harpoon_pop_harpoon_pops_and_splits(tmp_path):
    # A harpoon fired under a size-2 bubble pops it (scores) and splits it into
    # two smaller (size-1) bubbles -- the core split mechanic.
    from runtime import host_app

    ws = host_app.build_workstation(str(tmp_path / "carts"))
    _open_cart(ws, "Harpoon Pop")
    ns = ws.ns
    ns["bubbles"][:] = [[160.0, 120.0, 0.0, 0.0, 2]]     # one size-2 bubble, centred
    hu = ns["hunters"][0]
    hu[ns["H_X"]] = 160.0 - ns["PW"] / 2                 # player directly under it
    hu[ns["H_ROPE"]] = None
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


def test_harpoon_pop_smallest_bubble_pops_outright(tmp_path):
    from runtime import host_app

    ws = host_app.build_workstation(str(tmp_path / "carts"))
    _open_cart(ws, "Harpoon Pop")
    ns = ws.ns
    ns["bubbles"][:] = [[160.0, 120.0, 0.0, 0.0, 0]]     # a size-0 bubble
    hu = ns["hunters"][0]
    hu[ns["H_X"]] = 160.0 - ns["PW"] / 2
    hu[ns["H_ROPE"]] = None
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


def test_harpoon_pop_contact_costs_a_life(tmp_path):
    from runtime import host_app

    ws = host_app.build_workstation(str(tmp_path / "carts"))
    _open_cart(ws, "Harpoon Pop")
    ns = ws.ns
    hu = ns["hunters"][0]
    hu[ns["H_LIVES"]] = 3
    hu[ns["H_INV"]] = 0.0
    hu[ns["H_DEAD"]] = 0.0
    ns["over"] = 0.0
    ns["bubbles"][:] = [[hu[ns["H_X"]] + ns["PW"] / 2, ns["PLAYER_TOP"] + 2,
                         0.0, 10.0, 1]]
    ws.input.begin_frame()
    ws.frame(1 / 30)
    assert hu[ns["H_LIVES"]] == 2, "touching a bubble must cost a life"


def test_harpoon_pop_high_score_persists(tmp_path):
    # The best score rides pmem(0), so it survives a restart of the cart.
    from runtime import host_app

    ws = host_app.build_workstation(str(tmp_path / "carts"))
    _open_cart(ws, "Harpoon Pop")
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


def test_harpoon_pop_runs_long_without_error(tmp_path):
    # A plain endurance smoke: 300+ frames in attract mode (spawns, splits,
    # bounces, level clears all fire) with no cart crash.
    from runtime import host_app

    ws = host_app.build_workstation(str(tmp_path / "carts"))
    _open_cart(ws, "Harpoon Pop")
    ws.run.project.config["autoplay"] = 1
    ws.saves.apply()
    for _ in range(360):
        ws.input.begin_frame()
        ws.frame(1 / 30)
    assert ws.cart_error is None, "Harpoon Pop crashed during a long run"


def test_harpoon_pop_is_deterministic_from_a_seed(tmp_path):
    # The #65/#7 lockstep contract this cart exists to prove out: two runs
    # seeded the same way must reach BIT-IDENTICAL state. The cart's own
    # randomness is 100% behind rnd(), which the host wires straight to
    # Python's random module (see host_app.make_api), so seeding that module
    # before each run pins the whole simulation -- spawn positions, split
    # directions, bounce timing, everything the attract-mode autopilot then
    # reacts to deterministically.
    import random

    from runtime import host_app

    def _play(tag, frames=300):
        random.seed(12345)
        ws = host_app.build_workstation(str(tmp_path / ("carts_" + tag)))
        _open_cart(ws, "Harpoon Pop")
        ws.run.project.config["autoplay"] = 1
        ws.saves.apply()
        for _ in range(frames):
            ws.input.begin_frame()
            ws.frame(1 / 30)
        ns = ws.ns
        bubbles = tuple(
            tuple(round(v, 9) if isinstance(v, float) else v for v in b)
            for b in ns["bubbles"]
        )
        hunters = tuple(
            (round(hu[ns["H_X"]], 9), hu[ns["H_LIVES"]],
             round(hu[ns["H_INV"]], 9), round(hu[ns["H_DEAD"]], 9))
            for hu in ns["hunters"]
        )
        return (hunters, ns["score"], ns["level"], bubbles)

    a = _play("a")
    b = _play("b")
    assert a == b, "same seed must replay to identical state (the stage-3 lockstep contract)"
