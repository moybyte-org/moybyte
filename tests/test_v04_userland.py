"""Tests for the v0.4 userland runtime (host): canvas, cartridge model, desktop."""

import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from runtime import (  # noqa: E402
    Cartridge, CartridgeError, Catalog, DesktopRuntime, DesktopShell, Launcher,
    Workstation, palette,
)
from runtime.canvas import Canvas, Image  # noqa: E402

SYSTEM_CARTS = ROOT / "system_carts"
SPACE_CART = SYSTEM_CARTS / "wallpaper_space.kcart"


# -- palette ---------------------------------------------------------------

def test_palette_has_64_valid_colors():
    assert len(palette.KID64) == 64
    for rgb in palette.KID64:
        assert len(rgb) == 3
        assert all(0 <= ch <= 255 for ch in rgb)
    assert palette.color("white") == 7
    assert palette.color(10) == 10
    assert palette.color(99) == 99 & 63  # indices wrap into 0-63


# -- canvas ----------------------------------------------------------------

def test_canvas_cls_pset_and_rgb_resolution():
    cv = Canvas(20, 10)
    cv.cls(1)
    assert set(cv.buf) == {1}
    cv.pset(5, 5, 7)
    assert cv.pget(5, 5) == 7
    cv.pset(-1, 0, 8)  # off-canvas is a no-op
    cv.pset(20, 0, 8)
    rgb = cv.to_rgb888()
    assert len(rgb) == 20 * 10 * 3
    # pixel (5,5) resolves to palette[7]
    off = (5 * 20 + 5) * 3
    assert tuple(rgb[off:off + 3]) == palette.KID64[7]


def test_canvas_rectfill_clips_to_bounds():
    cv = Canvas(16, 16)
    cv.cls(0)
    cv.rectfill(-4, -4, 8, 8, 5)  # partially off the top-left
    assert cv.pget(0, 0) == 5
    assert cv.pget(3, 3) == 5
    assert cv.pget(4, 4) == 0  # outside the 8x8 from (-4,-4) -> covers 0..3


def test_canvas_spr_scaled_blit_with_transparency():
    cv = Canvas(10, 10)
    cv.cls(0)
    img = Image.from_ascii(["#.", ".#"], {"#": 8}, transparent=".")
    cv.spr(img, 0, 0, scale=2)
    # top-left 2x2 block is color 8, the transparent cell stays 0
    assert cv.pget(0, 0) == 8 and cv.pget(1, 1) == 8
    assert cv.pget(2, 0) == 0  # transparent source pixel


# -- cartridge -------------------------------------------------------------

def test_space_cartridge_loads_and_validates():
    cart = Cartridge.load(str(SPACE_CART))
    assert cart.title == "Space Desktop"
    assert cart.type == "wallpaper"
    assert cart.runtime == "python"
    assert cart.system is True
    assert cart.config["star_count"] == 80


def test_cartridge_missing_fields_raise(tmp_path):
    cart_dir = tmp_path / "bad.kcart"
    cart_dir.mkdir()
    (cart_dir / "manifest.json").write_text(json.dumps({"title": "x"}), encoding="utf-8")
    (cart_dir / "main.py").write_text("", encoding="utf-8")
    with pytest.raises(CartridgeError):
        Cartridge.load(str(cart_dir))


def test_cartridge_duplicate_is_editable_and_persists_config(tmp_path):
    cart = Cartridge.load(str(SPACE_CART))
    dest = tmp_path / "my_space.kcart"
    dup = cart.duplicate(str(dest), new_title="My Space")
    assert dup.title == "My Space"
    assert dup.system is False
    dup.save_config({"star_count": 200})
    # config.json was written and is reflected on reload
    saved = json.loads((dest / "config.json").read_text(encoding="utf-8"))
    assert saved["star_count"] == 200
    assert Cartridge.load(str(dest)).config["star_count"] == 200


def test_system_cartridge_refuses_save():
    cart = Cartridge.load(str(SPACE_CART))
    with pytest.raises(CartridgeError):
        cart.save_config({"star_count": 1})


# -- desktop runtime -------------------------------------------------------

def test_desktop_runs_and_config_drives_content():
    cart = Cartridge.load(str(SPACE_CART))
    cart.config["star_count"] = 12  # the "edit" before "run"
    rt = DesktopRuntime()
    assert rt.load(cart) is True
    for _ in range(20):
        rt.step(1 / 30)
    assert rt.error is None
    assert len(rt.ns["stars"]) == 12          # config changed the world
    # something was drawn (not a blank screen)
    assert len(set(rt.canvas.buf)) > 1
    # stars actually moved after stepping
    assert any(s[1] != int(s[1]) or s[1] > 0 for s in rt.ns["stars"])


def test_desktop_recovers_from_bad_cartridge(tmp_path):
    cart_dir = tmp_path / "boom.kcart"
    cart_dir.mkdir()
    (cart_dir / "manifest.json").write_text(json.dumps({
        "format": "kidcode-cart-v1", "title": "Boom", "type": "app",
        "runtime": "python", "main": "main.py",
    }), encoding="utf-8")
    (cart_dir / "main.py").write_text(
        "def _draw():\n    raise ValueError('boom')\n", encoding="utf-8"
    )
    cart = Cartridge.load(str(cart_dir))
    rt = DesktopRuntime()
    rt.load(cart)
    rt.step(1 / 30)  # must not raise
    assert rt.error is not None
    assert "boom" in str(rt.error)


# -- desktop shell (Make it mine) ------------------------------------------

def test_shell_make_it_mine_edit_and_run():
    cart = Cartridge.load(str(SPACE_CART))
    shell = DesktopShell(cart)
    assert shell.mode == "desktop"
    shell.press("menu")
    assert shell.mode == "menu"
    # first field is star_count (80); +step(10) twice -> 100
    shell.press("right")
    shell.press("right")
    assert cart.config["star_count"] == 100
    # Run applies: back to desktop, wallpaper re-run with the new count
    shell.press("run")
    assert shell.mode == "desktop"
    assert len(shell.rt.ns["stars"]) == 100


def test_shell_choice_field_cycles():
    cart = Cartridge.load(str(SPACE_CART))
    shell = DesktopShell(cart)
    shell.press("menu")
    shell.press("down")
    shell.press("down")  # -> field index 2 == "pet"
    assert shell.edit[shell.sel]["key"] == "pet"
    shell.press("right")
    assert cart.config["pet"] == "robot"


def test_shell_int_field_clamps_to_max():
    cart = Cartridge.load(str(SPACE_CART))
    shell = DesktopShell(cart)
    shell.press("menu")
    for _ in range(100):       # spam right past the max (300)
        shell.press("right")
    assert cart.config["star_count"] == 300


def test_shell_save_duplicates_system_cart(tmp_path):
    cart = Cartridge.load(str(SPACE_CART))
    shell = DesktopShell(cart, save_dir=str(tmp_path))
    shell.press("menu")
    shell.press("right")       # star_count -> 90
    shell.press("save")
    assert shell.last_save is not None
    assert os.path.isdir(shell.last_save)
    assert shell.cart.system is False  # now editing the user copy
    reloaded = Cartridge.load(shell.last_save)
    assert reloaded.config["star_count"] == 90
    assert reloaded.title.startswith("My ")


# -- launcher / workstation ------------------------------------------------

def test_catalog_scans_system_carts():
    items = Catalog.scan([str(SYSTEM_CARTS)])
    titles = {c.title for c in items}
    assert {"Space Desktop", "Star Catcher", "Ocean Desktop"} <= titles
    assert all(c.system for c in items)            # all are protected system carts
    assert [c.system for c in items] == sorted([c.system for c in items], reverse=True)


def test_launcher_navigation_wraps():
    items = Catalog.scan([str(SYSTEM_CARTS)])
    lz = Launcher(items)
    assert lz.sel == 0
    lz.press("right")
    assert lz.sel == 1
    lz.press("left")
    lz.press("left")                                # wrap below 0
    assert lz.sel == len(items) - 1
    assert lz.selected() is items[lz.sel]


def test_workstation_open_and_home(tmp_path):
    ws = Workstation([str(SYSTEM_CARTS), str(tmp_path)], save_dir=str(tmp_path))
    assert ws.screen == "launcher"
    ws.press("run")                                 # open the selected cart
    assert ws.screen == "desktop"
    assert ws.shell is not None
    ws.press("home")
    assert ws.screen == "launcher"
    assert ws.shell is None


def test_workstation_runs_game_cartridge(tmp_path):
    ws = Workstation([str(SYSTEM_CARTS), str(tmp_path)], save_dir=str(tmp_path))
    game_idx = next(i for i, it in enumerate(ws.launcher.items) if it.type == "game")
    ws.launcher.sel = game_idx
    ws.press("run")
    assert ws.shell.cart.type == "game"
    for _ in range(240):                            # auto-play (attract mode)
        ws.frame(1 / 30)
    assert ws.shell.rt.error is None
    assert ws.shell.rt.ns["score"] >= 1            # the catcher caught some stars


def test_workstation_home_rescans_for_saved_cart(tmp_path):
    ws = Workstation([str(SYSTEM_CARTS), str(tmp_path)], save_dir=str(tmp_path))
    before = len(ws.launcher.items)
    ws.press("run")                                 # open a system wallpaper
    ws.press("menu")
    ws.press("right")                               # edit a value
    ws.press("save")                                # -> writes a user cart into tmp_path
    ws.press("home")                                # rescan
    assert len(ws.launcher.items) == before + 1
    assert any(not c.system for c in ws.launcher.items)


# -- cards editor ----------------------------------------------------------

def test_cards_render_from_templates():
    cart = Cartridge.load(str(SPACE_CART))
    shell = DesktopShell(cart)
    shell.press("menu")
    assert shell.card_text(0) == "ADD 80 STARS"
    shell.press("right")                            # star_count 80 -> 90
    assert shell.card_text(0) == "ADD 90 STARS"
    pet_idx = next(i for i, f in enumerate(shell.edit) if f["key"] == "pet")
    assert shell.card_text(pet_idx) == "PET IS A FROG"


def test_see_the_code_toggle_and_content():
    cart = Cartridge.load(str(SPACE_CART))
    shell = DesktopShell(cart)
    shell.press("menu")
    assert shell.code_view is False
    shell.press("code")
    assert shell.code_view is True
    lines = shell.code_lines()
    assert "WHEN START:" in lines
    assert any("STAR_COUNT = 80" in ln for ln in lines)
    assert any("CARTRIDGE = SPACE DESKTOP" in ln for ln in lines)
    shell.press("code")
    assert shell.code_view is False
