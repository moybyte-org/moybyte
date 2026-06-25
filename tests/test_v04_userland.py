"""Tests for the v0.4 userland: the indexed canvas, the shared SpriteSheet /
CodeEditor cores, the `.kcart` store (kid_carts), and the shared console run on the
host via host_app -- the same console code the device runs."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from runtime import palette  # noqa: E402
from runtime.canvas import Canvas, Image, SpriteSheet  # noqa: E402

SYSTEM_CARTS = ROOT / "system_carts"


def _open_cart(ws, title):
    """Select a seeded system cart by title and open it in the shared console."""
    for i, c in enumerate(ws.launcher.items):
        if c["title"] == title:
            ws.launcher.sel = i
            break
    ws.open()


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


def test_canvas_spr_scaled_blit_with_transparency():
    cv = Canvas(10, 10)
    cv.cls(0)
    img = Image.from_ascii(["#.", ".#"], {"#": 8}, transparent=".")
    cv.spr(img, 0, 0, scale=2)
    assert cv.pix(0, 0) == 8 and cv.pix(1, 1) == 8
    assert cv.pix(2, 0) == 0  # transparent source pixel


# -- sprite sheet ----------------------------------------------------------

def test_sprite_sheet_tiles_and_hex_roundtrip():
    sh = SpriteSheet(cols=4, rows=4)        # 32x32, 16 sprites
    assert sh.count == 16 and (sh.w, sh.h) == (32, 32)
    assert sh.is_blank()
    assert sh.tile_origin(5) == (8, 8)      # sprite 5 = col 1, row 1
    sh.tset(5, 1, 2, 9)                     # sprite 5, local (1,2) -> color 9
    assert sh.pget(9, 10) == 9 and sh.tget(5, 1, 2) == 9
    assert not sh.is_blank() and sh.dirty
    sh2 = SpriteSheet.from_hex(sh.to_hex(), cols=4, rows=4)
    assert sh2.pix == sh.pix and sh2.dirty is False
    img = sh.tile_image(5, transparent=0)
    assert (img.w, img.h, img.transparent) == (8, 8, 0)
    assert img.pix[2 * 8 + 1] == 9          # local (1,2)


# -- code editor core ------------------------------------------------------

def test_code_editor_scrolloff_and_horizontal_scroll():
    # Caret-follow with a 1-cell margin, in both axes (the "1 line/col from the edge").
    from runtime import CodeEditor
    ed = CodeEditor("\n".join("x" * 60 for _ in range(60)))   # 60 long lines
    assert (ed.top, ed.left) == (0, 0)
    ed.move(ed.ROWS, 0)                                # caret down ROWS rows
    assert ed.top + 1 <= ed.row <= ed.top + ed.ROWS - 1 - ed.MARGIN
    ed.move(0, ed.COLS)                                # caret right COLS cols
    assert ed.left > 0                                 # horizontal scroll engaged
    assert ed.left + 1 <= ed.col <= ed.left + ed.COLS - 1 - ed.MARGIN
    r0, c0, top0 = ed.row, ed.col, ed.top
    ed.scroll(3, 0)                                    # drag-scroll: pan, keep caret
    assert ed.top == top0 + 3 and (ed.row, ed.col) == (r0, c0)


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


# -- cartridge API (the polymorphic spr the carts use) ----------------------

def test_spr_indexed_and_image_via_make_api():
    from runtime import host_app
    cv = Canvas(64, 64)
    cv.cls(0)
    sheet = SpriteSheet()
    sheet.tset(0, 0, 0, 8)                  # sprite 0, top-left pixel -> red

    class _Input:
        def held(self, n):
            return False

        def pressed(self, n):
            return False

    api = host_app.make_api(cv, _Input(), {}, sheet)
    api["spr"](0, 50, 60)                   # TIC-80 indexed spr from the sheet
    assert cv.pix(50, 60) == 8
    api["spr"](Image.from_ascii(["#"], {"#": 11}), 10, 10)  # Image still blits
    assert cv.pix(10, 10) == 11


# -- .kcart store (kid_carts) ----------------------------------------------

def test_kid_carts_store_roundtrip(tmp_path):
    from runtime import kid_carts
    root = str(tmp_path / "carts")
    kid_carts.ensure_dirs(root)
    c = kid_carts.create("My Cart", root, src="def _draw():\n    cls(1)\n",
                         cfg={"a": 1}, type="app")
    assert c["title"] == "My Cart" and c["src"].startswith("def _draw")
    assert any(i["title"] == "My Cart" for i in kid_carts.scan(root))
    # save edited code + a sprite sheet; reload reflects both
    kid_carts.save_code(c, "def _draw():\n    cls(2)\n")
    kid_carts.save_sprites(c, "012\n345\n")
    reloaded = kid_carts.load(c["path"])
    assert "cls(2)" in reloaded["src"]
    assert reloaded["sprites"].startswith("012")
    # duplicate makes an independent editable copy
    dup = kid_carts.duplicate(c, root, new_title="Copy")
    assert dup["title"] == "Copy" and dup["path"] != c["path"]


# -- shared console: carts run, cards edit (host == device) -----------------

def test_console_runs_wallpaper_and_config_drives_content(tmp_path):
    from runtime import host_app
    ws = host_app.build_workstation(str(tmp_path / "carts"))
    _open_cart(ws, "Space Desktop")
    assert ws.screen == "desktop" and ws.ns is not None
    for _ in range(10):
        ws.frame(1 / 30)
    assert len(ws.ns["stars"]) == ws.config.get("star_count", 80)  # config drove it
    assert len(set(ws.canvas.buf)) > 1                             # drew something


def test_console_runs_game_cart_and_scores(tmp_path):
    from runtime import host_app
    ws = host_app.build_workstation(str(tmp_path / "carts"))
    _open_cart(ws, "Star Catcher")
    assert ws.cart["type"] == "game"
    ws.config["autoplay"] = 1               # opt into attract mode, then re-run
    ws.apply()
    for _ in range(240):                    # attract-mode auto-play
        ws.frame(1 / 30)
    # `best` survives the game-over reset, so it's the honest "did it score" check.
    assert ws.ns["best"] >= 1


def test_console_cards_make_it_mine_edit_and_run(tmp_path):
    from runtime import host_app
    ws = host_app.build_workstation(str(tmp_path / "carts"))
    _open_cart(ws, "Space Desktop")
    ws._open_menu()                         # Space has an edit schema -> cards
    assert ws.menu_view == "cards"
    before = ws.config.get("star_count", 80)
    ws.adjust(1)                            # +step on the first field (star_count, +10)
    assert ws.config["star_count"] == before + 10
    ws.apply()                              # re-run with the new config
    assert ws.screen == "desktop"
    assert len(ws.ns["stars"]) == before + 10


# -- shared console: editors via host_app (mouse=touch, arrows=trackball) ---

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


def test_code_view_arrows_move_caret_and_scroll(tmp_path):
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
    assert ed.left > 0


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
    drv.click(C._ED_RUN[0] + 2, C._ED_RUN[1] + 2)   # top-bar RUN icon applies it
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
