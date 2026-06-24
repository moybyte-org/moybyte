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
from runtime.canvas import Canvas, Image, SpriteSheet  # noqa: E402

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

def test_canvas_cls_pix_and_rgb_resolution():
    cv = Canvas(20, 10)
    cv.cls(1)
    assert set(cv.buf) == {1}
    cv.pix(5, 5, 7)             # three args writes
    assert cv.pix(5, 5) == 7    # two args reads (TIC-80)
    cv.pix(-1, 0, 8)  # off-canvas is a no-op
    cv.pix(20, 0, 8)
    rgb = cv.to_rgb888()
    assert len(rgb) == 20 * 10 * 3
    # pixel (5,5) resolves to palette[7]
    off = (5 * 20 + 5) * 3
    assert tuple(rgb[off:off + 3]) == palette.KID64[7]


def test_canvas_rect_is_filled_and_clips_to_bounds():
    cv = Canvas(16, 16)
    cv.cls(0)
    cv.rect(-4, -4, 8, 8, 5)  # TIC-80 rect = FILLED, partially off the top-left
    assert cv.pix(0, 0) == 5
    assert cv.pix(3, 3) == 5
    assert cv.pix(4, 4) == 0  # outside the 8x8 from (-4,-4) -> covers 0..3


def test_canvas_rectb_draws_outline_only():
    cv = Canvas(16, 16)
    cv.cls(0)
    cv.rectb(2, 2, 6, 6, 9)   # TIC-80 rectb = border/outline
    assert cv.pix(2, 2) == 9 and cv.pix(7, 7) == 9  # corners on the border
    assert cv.pix(4, 4) == 0                        # interior stays clear


def test_sprite_sheet_tiles_and_hex_roundtrip():
    sh = SpriteSheet(cols=4, rows=4)        # 32x32, 16 sprites
    assert sh.count == 16 and (sh.w, sh.h) == (32, 32)
    assert sh.is_blank()
    assert sh.tile_origin(5) == (8, 8)      # sprite 5 = col 1, row 1
    sh.tset(5, 1, 2, 9)                     # sprite 5, local (1,2) -> color 9
    assert sh.pget(9, 10) == 9 and sh.tget(5, 1, 2) == 9
    assert not sh.is_blank() and sh.dirty
    # hex serialization round-trips and resets dirty
    sh2 = SpriteSheet.from_hex(sh.to_hex(), cols=4, rows=4)
    assert sh2.pix == sh.pix and sh2.dirty is False
    # tile_image extracts an 8x8 Image with the transparency key
    img = sh.tile_image(5, transparent=0)
    assert (img.w, img.h, img.transparent) == (8, 8, 0)
    assert img.pix[2 * 8 + 1] == 9          # local (1,2)


def test_spr_renders_indexed_sprite_and_still_accepts_image():
    cart = Cartridge.load(str(SPACE_CART))
    cart.sheet.tset(0, 0, 0, 8)             # sprite 0, top-left pixel -> red
    rt = DesktopRuntime()
    assert rt.load(cart) is True
    rt.ns["spr"](0, 50, 60)                 # TIC-80 indexed spr from the sheet
    assert rt.canvas.pix(50, 60) == 8
    img = Image.from_ascii(["#"], {"#": 11})
    rt.ns["spr"](img, 10, 10)               # Image still blits (back-compat)
    assert rt.canvas.pix(10, 10) == 11


def test_cartridge_save_and_reload_sprites(tmp_path):
    cart = Cartridge.load(str(SPACE_CART))
    dup = cart.duplicate(str(tmp_path / "my.kcart"), new_title="My")
    dup.sheet.tset(3, 4, 5, 12)
    dup.save_sprites()
    reloaded = Cartridge.load(str(tmp_path / "my.kcart"))
    assert reloaded.sheet.tget(3, 4, 5) == 12
    assert reloaded.sheet.dirty is False


def test_system_cartridge_refuses_sprite_save():
    cart = Cartridge.load(str(SPACE_CART))
    with pytest.raises(CartridgeError):
        cart.save_sprites()


def test_canvas_spr_scaled_blit_with_transparency():
    cv = Canvas(10, 10)
    cv.cls(0)
    img = Image.from_ascii(["#.", ".#"], {"#": 8}, transparent=".")
    cv.spr(img, 0, 0, scale=2)
    # top-left 2x2 block is color 8, the transparent cell stays 0
    assert cv.pix(0, 0) == 8 and cv.pix(1, 1) == 8
    assert cv.pix(2, 0) == 0  # transparent source pixel


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


def test_workstation_launcher_frame_renders():
    # Regression: the launcher's frame() must use the TIC-80 canvas API (rect/rectb),
    # not the removed rectfill -- exercised here so it can't silently break again.
    ws = Workstation([str(SYSTEM_CARTS)])
    assert ws.screen == "launcher"
    ws.frame(1 / 30)
    assert len(set(ws.rgb888())) > 1   # gallery drawn, not a blank screen


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


def test_shell_code_editor_edits_source_runs_and_saves(tmp_path):
    from runtime import CodeEditor
    cart = Cartridge.load(str(SPACE_CART))
    dup = cart.duplicate(str(tmp_path / "my.kcart"), new_title="My")
    shell = DesktopShell(dup)
    shell.press("menu")
    shell.press("code")
    assert shell.code_view is True and isinstance(shell.editor, CodeEditor)
    shell.editor.row, shell.editor.col = 0, 0       # type a comment at the top
    for ch in "x = 1\n":
        shell.type_char(ord(ch))
    assert shell.editor.text().startswith("x = 1\n")
    shell.press("run")                              # applies + persists (user cart)
    assert shell.mode == "desktop"
    assert dup.main_source.startswith("x = 1\n")
    assert Cartridge.load(str(tmp_path / "my.kcart")).main_source.startswith("x = 1\n")


def test_shell_paint_editor_clicks_paint_and_save(tmp_path):
    from runtime.shell import _PG_X, _PG_Y, _PSW_X, _PSW_Y, _PSW
    cart = Cartridge.load(str(SPACE_CART))
    dup = cart.duplicate(str(tmp_path / "p.kcart"), new_title="P")
    shell = DesktopShell(dup)
    shell.press("menu")
    shell.press("paint")
    assert shell.view == "paint" and shell.paint is not None
    shell.click(_PSW_X + 1, _PSW_Y + 6 * _PSW + 1)  # palette swatch index 12
    assert shell.paint.color == 12
    shell.click(_PG_X + 1, _PG_Y + 1)               # paint sprite 0, pixel (0,0)
    assert dup.sheet.tget(0, 0, 0) == 12
    shell.press("save")
    assert Cartridge.load(str(tmp_path / "p.kcart")).sheet.tget(0, 0, 0) == 12


def test_shell_editor_save_refuses_system_cart_without_raising():
    cart = Cartridge.load(str(SPACE_CART))           # protected system cart
    shell = DesktopShell(cart)
    shell.press("menu")
    shell.press("code")
    shell.type_char(ord("z"))
    shell.press("save")                              # must not raise
    assert "CANT SAVE" in shell.status


def test_host_runs_shared_console_at_320x240(tmp_path):
    from runtime import host_app
    ws = host_app.build_workstation(str(tmp_path / "carts"))
    drv = host_app.ConsoleDriver(ws)
    assert (ws.canvas.w, ws.canvas.h) == (320, 240)     # same surface as the device
    assert ws.launcher.items                            # seeded system carts
    drv.frame(1 / 30)
    assert len(set(drv.rgb888())) > 1                   # launcher renders
    drv.press("run")
    drv.frame(1 / 30)
    assert ws.screen == "desktop"                       # opened a cart
    drv.press("b")
    drv.frame(1 / 30)
    if ws.menu_view == "cards":
        drv.press("a")
        drv.frame(1 / 30)
    assert ws.menu_view == "code" and ws.editor is not None
    ws.editor.row, ws.editor.col = 0, 0
    for ch in "Z=9":
        drv.type_char(ord(ch))
        drv.frame(1 / 30)
    assert ws.editor.lines[0].startswith("Z=9")         # typed into the real source
    drv.frame(1 / 30)
    assert len(set(drv.rgb888())) > 1                   # code editor renders


def test_code_editor_scrolloff_and_horizontal_scroll():
    # Caret-follow with a 1-cell margin, in both axes (the "1 line/col from the edge").
    from runtime import CodeEditor
    ed = CodeEditor("\n".join("x" * 60 for _ in range(60)))   # 60 long lines
    assert (ed.top, ed.left) == (0, 0)
    ed.move(ed.ROWS, 0)                                # caret down ROWS rows
    # caret stays >= MARGIN from the bottom edge, not flush against it
    assert ed.top + 1 <= ed.row <= ed.top + ed.ROWS - 1 - ed.MARGIN
    ed.move(0, ed.COLS)                                # caret right COLS cols
    assert ed.left > 0                                 # horizontal scroll engaged (right!)
    assert ed.left + 1 <= ed.col <= ed.left + ed.COLS - 1 - ed.MARGIN
    # drag-scroll pans the viewport without moving the caret
    r0, c0 = ed.row, ed.col
    top0 = ed.top
    ed.scroll(3, 0)
    assert ed.top == top0 + 3 and (ed.row, ed.col) == (r0, c0)


def test_code_view_arrows_move_caret_and_scroll(tmp_path):
    # Host arrows (= device trackball) move the CARET; the view follows it, and the
    # mouse (= touchscreen) drag pans the viewport.
    from runtime import host_app
    ws = host_app.build_workstation(str(tmp_path / "carts"))
    drv = host_app.ConsoleDriver(ws)
    drv.press("run"); drv.frame(1 / 30)
    drv.press("b"); drv.frame(1 / 30)
    if ws.menu_view == "cards":
        drv.press("a"); drv.frame(1 / 30)
    ed = ws.editor
    ed.set_text("\n".join(("c" * 60) + " r%02d" % i for i in range(40)))
    drv.pan(0, 1)                                      # hold "down" -> caret moves down
    for _ in range(60):
        drv.frame(1 / 30)
        if ed.row >= 25:
            break
    drv.pan(0, 0)
    assert ed.row >= 25 and ed.top > 0                 # caret moved + view followed
    drv.pan(1, 0)                                      # hold "right" -> horizontal scroll
    for _ in range(60):
        drv.frame(1 / 30)
        if ed.left > 0:
            break
    drv.pan(0, 0)
    assert ed.left > 0                                 # right-scroll works now


def test_typing_keeps_caret_on_screen():
    # Regression: typing past the visible width must scroll so the caret stays in view.
    from runtime import CodeEditor
    ed = CodeEditor("")
    for _ in range(ed.COLS + 10):          # type a line longer than the window
        ed.key(ord("x"))
    assert ed.left > 0                      # view scrolled right to follow typing
    assert ed.left <= ed.col < ed.left + ed.COLS   # caret stayed on screen
    for _ in range(ed.COLS):                # backspacing follows the caret back
        ed.key(0x08)
    assert ed.left <= ed.col < ed.left + ed.COLS


def test_code_editor_drag_scrolls_without_crashing(tmp_path):
    # Regression: press+drag inside the code area on the first frame must not blow up
    # (self._drag was read before init), and should pan the viewport.
    from runtime import console as C
    from runtime import host_app
    ws = host_app.build_workstation(str(tmp_path / "carts"))
    drv = host_app.ConsoleDriver(ws)
    drv.press("run"); drv.frame(1 / 30)
    drv.press("b"); drv.frame(1 / 30)
    if ws.menu_view == "cards":
        drv.press("a"); drv.frame(1 / 30)
    ws.editor.set_text("\n".join("line %02d" % i for i in range(40)))
    drv.touch(C._CODE_X0 + 20, C._CODE_Y0 + 100); drv.frame(1 / 30)   # finger down
    drv.touch_drag(C._CODE_X0 + 20, C._CODE_Y0 + 10); drv.frame(1 / 30)  # drag up
    drv.touch_drag(C._CODE_X0 + 20, C._CODE_Y0 + 0); drv.frame(1 / 30)
    drv.touch_up()
    assert ws.editor.top > 0                  # viewport panned, no crash


def test_code_editor_symbol_palette_inserts(tmp_path):
    # The keyboard can't type = [ ] { } < > % ; the tappable palette supplies them.
    from runtime import console as C
    from runtime import host_app
    ws = host_app.build_workstation(str(tmp_path / "carts"))
    drv = host_app.ConsoleDriver(ws)
    drv.press("run"); drv.frame(1 / 30)
    drv.press("b"); drv.frame(1 / 30)
    if ws.menu_view == "cards":
        drv.press("a"); drv.frame(1 / 30)
    ed = ws.editor
    ed.set_text("")
    ed.row = ed.col = 0
    drv.type_char(ord("x"))                 # keyboard char ...
    drv.frame(1 / 30)
    for sym in "=[]":                       # ... + palette symbols the keyboard lacks
        i = C._CODE_SYMBOLS.index(sym)
        drv.click(C._SYM_AREA[0] + i * C._SYM_CELL + 4, C._SYM_Y + 6)
        drv.frame(1 / 30)
    assert ed.text() == "x=[]"
    # tapping the top-bar RUN icon applies the (valid) edited source
    drv.click(C._ED_RUN[0] + 2, C._ED_RUN[1] + 2)
    drv.frame(1 / 30)
    assert ws.screen == "desktop"


def test_host_console_paint_via_mouse(tmp_path):
    from runtime import console as C
    from runtime import host_app
    ws = host_app.build_workstation(str(tmp_path / "carts"))
    drv = host_app.ConsoleDriver(ws)
    drv.press("run")
    drv.frame(1 / 30)
    bx, by = C._PAINT_BTN[0], C._PAINT_BTN[1]            # click the PAINT overlay button
    drv.click(bx + 2, by + 2)
    drv.frame(1 / 30)
    assert ws.menu_view == "paint" and ws.paint is not None
    sx = C._SW_X0 + (12 % C._SW_COLS) * C._SW            # palette swatch for color 12
    sy = C._SW_Y0 + (12 // C._SW_COLS) * C._SW
    drv.click(sx + 2, sy + 2)
    drv.frame(1 / 30)
    assert ws.paint.color == 12
    drv.click(C._PG_X0 + 2, C._PG_Y0 + 2)               # paint sprite 0, pixel (0,0)
    drv.frame(1 / 30)
    assert ws.sheet.tget(0, 0, 0) == 12


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
