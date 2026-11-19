"""A compiled GAME reaching what a Python or Lua game holds by name
(docs/moy_cart_api.md, "A compiled cart's Moybyte verbs"), on the host's
WAMR, through the same C a board runs (native/moycore/moycore_wasm_ext.h):

  * its run holds a grant of its own, as a user app's does, so its imports
    from "moybyte.app" award its badges and open a device handle under its
    permissions and their consent;
  * scene, load_scene, scene_name and scene_flags read its scenes as
    Python's Scenes reads them;
  * image, image_size and image_spr reach its paint image;
  * mouse answers Python's mouse(), and a press on a console frame that
    runs no tick reaches the next tick (the kernel Player's pointer latch);
  * game_prefs_get, game_prefs_set and game_prefs_clear keep the game's prefs
    across a restart of its run, from the grant its run holds;
  * a module importing from an extension its manifest does not declare is
    refused at load, the import named;
  * the VM-free rule keeps the VM for a compiled cart declaring scenes or
    paint images, whose data the Python console hands over.

tests/fixtures/wasm/game_ext.moy/src/main.c lists the pmem slots it reports in.
"""

import json
import os
import shutil

import pytest

from runtime import host_app
from tools import wasm_cart
from ws_helpers import make_drv, open_cart

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GAME = os.path.join(ROOT, "tests", "fixtures", "wasm", "game_ext.moy")
_DT = 1.0 / 30.0


def _binding_or_skip():
    from runtime import wasm_binding, wasm_host
    if wasm_host.available():
        return
    why = wasm_binding.why_unavailable() or "unknown"
    if os.environ.get("CI") or os.environ.get("MOYBYTE_REQUIRE_HOST_WASM"):
        pytest.fail("the host wasm binding did not build: %s" % why)
    pytest.skip("no host wasm binding: %s" % why)


def _ws(tmp_path, edit=None):
    _binding_or_skip()
    built = str(tmp_path / "built" / "game_ext.moy")
    try:
        wasm_cart.build(GAME, built)
    except wasm_cart.ToolchainMissing as exc:
        pytest.skip(str(exc))
    if edit is not None:
        p = os.path.join(built, "manifest.json")
        with open(p) as f:
            man = json.load(f)
        edit(man)
        with open(p, "w") as f:
            json.dump(man, f)
    root = str(tmp_path / "carts")
    host_app.moy_carts.ensure_dirs(root)
    shutil.copytree(built, os.path.join(root, "game_ext.moy"))
    return host_app.build_workstation(root)


def _pmem(ws):
    run = ws.run.player.lua
    run.flush_pmem()
    return list(ws.run.project.pmem.cells)


@pytest.fixture
def game(tmp_path):
    ws = _ws(tmp_path)
    open_cart(ws, "Game Ext Wasm")
    assert ws.run.player.cart_error is None, ws.run.player.cart_error
    drv = make_drv(ws)
    for _ in range(3):
        drv.frame(_DT)
    assert ws.run.player.cart_error is None, ws.run.player.cart_error
    yield ws, drv
    ws.spine.exit()


def test_a_compiled_game_awards_its_badges_and_opens_a_device_handle(game):
    ws, _drv = game
    p = _pmem(ws)
    assert p[0] >= 0 and p[1] >= 0, p[:2]
    from ws_helpers import settle_badges
    settle_badges(ws)
    assert ws.ach.has("local.game_ext", "started"), "the badge it awards is found"
    assert ws.ach.has("local.game_ext", "steps"), "progress reaching its goal finds it"
    assert p[18] == -4, "a console with no pins leaves `pins` absent: DENIED, %r" % p[18]


def test_a_compiled_game_opens_a_device_handle_its_permission_grants(tmp_path):
    """On a console with pins (the Waveshare P4's lease table stood in), the
    game's `pins` permission opens a handle, as an app's does."""
    import sys
    sys.path.insert(0, os.path.join(ROOT, "tools"))
    import board_config
    from runtime import leases
    from runtime.host import bus_sim, lease_sim
    leases.stand_in(lease_sim, board_config.lease_spec(
        os.path.join(ROOT, "firmware", "esp32_p4_wifi6_touch_lcd_7b")))
    lease_sim.reset()
    bus_sim.reset()
    try:
        ws = _ws(tmp_path)
        try:
            open_cart(ws, "Game Ext Wasm")
            assert ws.run.player.cart_error is None, ws.run.player.cart_error
            assert _pmem(ws)[18] == 1, "its pins permission opens a handle"
        finally:
            ws.spine.exit()
    finally:
        leases.stand_in(None, None)
        lease_sim.reset()


def test_a_compiled_game_keeps_its_prefs_across_a_restart(game):
    """moybyte.prefs's get, set and clear (docs/app_api_v1.md section 5):
    the first run finds no "runs", sets it, and the next run reads it back."""
    ws, _drv = game
    p = _pmem(ws)
    assert p[24:29] == [-8, 0, 0, -7, -8], p[24:29]
    ws.spine.exit()
    open_cart(ws, "Game Ext Wasm")
    assert ws.run.player.cart_error is None, ws.run.player.cart_error
    p = _pmem(ws)
    assert p[24:27] == [1, 1, 0], p[24:27]


def test_a_compiled_game_reads_its_scenes(game):
    ws, _drv = game
    p = _pmem(ws)
    assert p[2:7] == [2, 40, 50, 3, 1], p[2:7]       # main: count, x, y, tile, tag
    assert p[7:9] == [4, 1], p[7:9]                  # scene_name(0) "main", (1) "second"
    assert p[9:13] == [3, 3, -1, 1], p[9:13]         # load_scene, scene(), unknown, tag 7
    assert p[13] == len('{"speed": 2}') and p[14] == 0, p[13:15]


def test_a_compiled_game_places_its_paint_image(game):
    ws, _drv = game
    p = _pmem(ws)
    assert p[15] == 1 and p[16] == (4 << 16 | 2) and p[17] == 0, p[15:18]
    import struct
    from runtime.moyimg import decode_moyimg
    with open(os.path.join(GAME, "images", "dot.moyimg")) as f:
        w, h, idx = decode_moyimg(f.read())
    c = ws.run.canvas
    for y in range(h):
        for x in range(w):
            i = idx[y * w + x]
            if i >= 64:
                continue
            at = ((100 + y) * c.w + 100 + x) * 2
            assert struct.unpack_from("<H", c._buf, at)[0] == c._wire[i], (x, y, i)


def test_a_compiled_games_mouse_is_pythons(game):
    ws, drv = game
    want = []
    for x, y in ((60, 70), (61, 72)):
        drv.touch(x, y)
        drv.frame(_DT)
        want.append(ws.run.player.ns["mouse"]())
    drv.touch_up()
    drv.frame(_DT)
    p = _pmem(ws)
    assert p[19] >= 2 and p[20] == sum(1 for w in want if w[2]), (p[19:23], want)
    assert (p[21], p[22]) == tuple(want[-1][:2]), (p[21:23], want)


def test_a_press_on_a_frame_with_no_tick_reaches_the_next_tick(game, monkeypatch):
    """A paced run takes no tick on some of the console's frames: the press
    edge such a frame carries waits for the next tick (native/moy_play/
    moy_play.c's pointer latch), and that one tick alone reads it."""
    ws, _drv = game
    run = ws.run.player.lua
    if not getattr(run, "_prun", 0):
        pytest.skip("the host's Player does not drive this run's frame")
    import sys
    state = [7]                                        # live, held, the press edge

    def pointer(_inp, out):
        out[0], out[1], out[2], out[3] = 60, 70, state[0], 0
        return out
    monkeypatch.setattr(sys.modules[type(run).__module__], "pointer_state", pointer)
    player = ws.run.player
    before = _pmem(ws)[20]
    player._run_ticks(0, _DT, True)                    # the press, no tick
    state[0] = 3
    player._run_ticks(1, _DT, True)
    assert _pmem(ws)[20] == before + 1, "the press reached the next tick"
    player._run_ticks(2, _DT, True)
    assert _pmem(ws)[20] == before + 1, "and no tick after it"


def test_an_undeclared_extension_is_refused_naming_the_import(tmp_path):
    def drop(man):
        man["extensions"] = [e for e in man["extensions"] if e != "moybyte.mouse"]
    ws = _ws(tmp_path, drop)
    try:
        open_cart(ws, "Game Ext Wasm")
        assert "moybyte.mouse" in (ws.run.player.cart_error or ""), ws.run.player.cart_error
    finally:
        ws.spine.exit()


def test_a_game_that_declares_no_app_extension_imports_none_of_it(tmp_path):
    """Its grant is the prefs role's alone, which its module reaches through
    "moybyte.prefs" only: an import from "moybyte.app" is refused at load."""
    def drop(man):
        man["extensions"] = [e for e in man["extensions"]
                             if e not in ("moybyte.app", "moybyte.prefs")]
    ws = _ws(tmp_path, drop)
    try:
        open_cart(ws, "Game Ext Wasm")
        assert "moybyte.app" in (ws.run.player.cart_error or ""), ws.run.player.cart_error
    finally:
        ws.spine.exit()


def test_the_vm_free_rule_keeps_the_vm_for_scenes_and_images(tmp_path):
    """The census (native/moy_play/moy_play_rule.c): the scenes and paint
    images keep the VM, the extension named; the mouse and a game's app
    extension with C-served rows alone do not."""
    from runtime import moy_play
    built = str(tmp_path / "game_ext.moy")
    try:
        wasm_cart.build(GAME, built)
    except wasm_cart.ToolchainMissing as exc:
        pytest.skip(str(exc))
    man_path = os.path.join(built, "manifest.json")

    def census(exts, perms=()):
        with open(man_path) as f:
            man = json.load(f)
        man["extensions"] = list(exts)
        man["moybyte"]["permissions"] = list(perms)
        with open(man_path, "w") as f:
            json.dump(man, f)
        return moy_play.census(built)

    full = ["moybyte.app", "moybyte.scenes", "moybyte.images", "moybyte.mouse"]
    assert census(full) == ("wasm", False, "import moybyte.scenes")
    assert census(["moybyte.app", "moybyte.images"]) == ("wasm", False, "import moybyte.images")
    assert census(["moybyte.mouse"]) == ("wasm", True, "free")
