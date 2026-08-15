"""Tests for the userland: the canvas, the shared SpriteSheet / CodeEditor
cores, the `.moy` store (moy_carts), and the shared console run on the host via
host_app -- the same console code the device runs.

The canvas here is `runtime.host_canvas.make_canvas`, i.e. the BOARDS'
`device_canvas.DeviceCanvas` over a host compositor: one raster, three
architectures. Its buffer is RGB565, so a pixel is two bytes and never a palette
index -- `cv.pix(x, y)` reads an index back, and `canvas_probe` reads the buffer
at the canvas's real pixel width."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from runtime import palette  # noqa: E402
from runtime.editors import SpriteSheet, TileMap  # noqa: E402
from runtime.host_canvas import make_canvas as Canvas  # noqa: E402
from runtime.host_canvas import make_system_canvas as SysCanvas  # noqa: E402
from runtime.moy_image import Image  # noqa: E402

import canvas_probe as probe  # noqa: E402  (pixel-width-agnostic "it drew" probes)

SYSTEM_CARTS = ROOT / "system_carts"


def _open_cart(ws, title):
    """Open a seeded system cart by title in the shared console. Games/tools/apps live in
    the launcher run-grid; a WALLPAPER leaves it (spec shell_ux_v1.md) but stays a real
    editable cart in the store, so fall back to opening it by reference (as ws.open() does)."""
    for i, c in enumerate(ws.launcher.items):
        if c["title"] == title:
            ws.launcher.sel = i
            ws.open()
            return
    cart = next((c for c in ws._all_carts if c["title"] == title), None)
    assert cart is not None, "seed cart not found: " + title
    ws._open_workspace(cart)
    ws.run(ws.project, ws.launcher_layer)


# -- palette ---------------------------------------------------------------

def test_palette_has_64_valid_colors():
    assert len(palette.MOY64) == 64
    for rgb in palette.MOY64:
        assert len(rgb) == 3
        assert all(0 <= ch <= 255 for ch in rgb)
    assert palette.color("white") == 7
    assert palette.color(10) == 10
    assert palette.color(99) == 99 & 63  # indices wrap into 0-63


# -- canvas ----------------------------------------------------------------

def test_canvas_cls_pix_and_rgb_resolution():
    # A SYSTEM canvas, because `to_rgb888` is part of the system-surface contract
    # (HostSystemCanvas) rather than of the raster: only the host has a screen to
    # hand to pygame or a GIF. At font_scale 1 it draws byte-identically to the
    # plain game canvas, so nothing else about this test changes.
    cv = SysCanvas(20, 10)
    cv.cls(1)
    assert set(probe.pixels(cv)) == probe.words_of({1}, cv)
    cv.pix(5, 5, 7)             # three args writes
    assert cv.pix(5, 5) == 7    # two args reads (TIC-80)
    cv.pix(-1, 0, 8)  # off-canvas is a no-op
    cv.pix(20, 0, 8)
    rgb = cv.to_rgb888()
    assert len(rgb) == 20 * 10 * 3
    off = (5 * 20 + 5) * 3
    assert tuple(rgb[off:off + 3]) == palette.MOY64[7]


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


# -- tilemap (#32) ---------------------------------------------------------

def test_tilemap_mget_mset_empty_and_roundtrip():
    tm = TileMap(4, 3)
    assert (tm.w, tm.h) == (4, 3) and tm.is_blank()
    assert tm.mget(0, 0) == -1               # blank cells read as EMPTY (-1)
    assert tm.mget(99, 0) == -1              # out of range is EMPTY, not a crash
    tm.mset(1, 2, 9)                         # place tile 9
    assert tm.mget(1, 2) == 9 and not tm.is_blank() and tm.dirty
    g0 = tm.gen
    tm.mset(0, 0, 0)                         # tile id 0 is a real tile, not "empty"
    assert tm.mget(0, 0) == 0 and tm.gen == g0 + 1
    tm.mset(1, 2, -1)                        # a negative id clears the cell
    assert tm.mget(1, 2) == -1
    tm.mset(3, 0, 5)
    tm2 = TileMap.from_hex(tm.to_hex())      # header carries dims; blob round-trips
    assert (tm2.w, tm2.h) == (4, 3)
    assert tm2.mget(0, 0) == 0 and tm2.mget(3, 0) == 5 and tm2.mget(1, 2) == -1
    assert tm2.cells == tm.cells and tm2.dirty is False


def test_tilemap_clamps_tile_id_to_byte():
    tm = TileMap(2, 2)
    tm.mset(0, 0, TileMap.MAX_ID + 50)       # over the ceiling -> clamped, never wraps
    assert tm.mget(0, 0) == TileMap.MAX_ID


def test_canvas_map_blits_tiles_at_scale():
    cv = Canvas(40, 40)
    cv.cls(0)
    # SPEC.md 3.2's sheet: 16 x 32 tiles. NOT the SpriteSheet default (16 x 16) --
    # libmoy's sheet verbs (map/blit_map, spr_batch/blit_batch, sspr, tline) refuse
    # a sheet that is not exactly 128 x 256 and draw NOTHING, on both boards and now
    # on the host. The old pure-Python host raster accepted any size, which is how a
    # test could pass here and have drawn nothing on glass.
    sheet = SpriteSheet(16, 32)
    sheet.tset(7, 0, 0, 8)                   # tile 7: a single red pixel at its (0,0)
    sheet.tset(7, 7, 7, 9)                   # ... and green at (7,7)
    tm = TileMap(2, 2)
    tm.mset(1, 1, 7)                         # one tile at cell (1,1), rest empty
    cv.map(tm, sheet, 0, 0, 2, 2, 0, 0, -1, 2)   # scale 2 -> each cell is 16px
    # cell (1,1) lands at screen (16,16); tile pixel (0,0) -> a 2x2 red block there
    assert cv.pix(16, 16) == 8 and cv.pix(17, 17) == 8
    # its (7,7) pixel -> green 2x2 block at (16+14, 16+14)
    assert cv.pix(30, 30) == 9
    # empty cells drew nothing
    assert cv.pix(0, 0) == 0 and cv.pix(8, 8) == 0


def test_map_mget_mset_via_make_api():
    from runtime import host_app
    cv = Canvas(40, 40)
    cv.cls(0)
    sheet = SpriteSheet(16, 32)          # the spec sheet -- see map's note above
    sheet.tset(3, 0, 0, 11)
    tm = TileMap(3, 3)

    class _Input:
        def held(self, n):
            return False

        def pressed(self, n):
            return False

    api = host_app.make_api(cv, _Input(), {}, sheet, None, tm)
    api["mset"](1, 1, 3)                     # cart-facing mset writes the shared map
    assert api["mget"](1, 1) == 3 and tm.mget(1, 1) == 3
    api["map"](0, 0, 3, 3, 0, 0, -1, 1)      # cart-facing map() draws it (scale 1)
    assert cv.pix(8, 8) == 11                # tile 3 at cell (1,1), pixel (0,0) -> 8,8


def test_make_layer_and_draw_layer_via_make_api():
    # #54 scroll engine through the cart-facing api: make_layer() returns a draw
    # target (a wider off-screen layer) carrying the full verb set; draw_layer()
    # window-copies its visible region into the screen canvas at a CLAMPED camera.
    from runtime import host_app

    class _Input:
        def held(self, n):
            return False

        def pressed(self, n):
            return False

    cv = Canvas(20, 16)
    api = host_app.make_api(cv, _Input(), {})
    bg = api["make_layer"](40, 16)           # a 2x-wide world
    assert (bg.W, bg.H) == (40, 16)
    bg.rect(0, 0, 20, 16, 8)                  # left half = 8
    bg.rect(20, 0, 20, 16, 11)               # right half = 11

    cv.cls(0)
    api["draw_layer"](bg, 0, 0)              # window at world x=0 -> all left half (8)
    assert set(probe.pixels(cv)) == probe.words_of({8}, cv)

    cv.cls(0)
    api["draw_layer"](bg, 20, 0)            # window at world x=20 -> all right half (11)
    assert set(probe.pixels(cv)) == probe.words_of({11}, cv)

    cv.cls(0)
    api["draw_layer"](bg, 999, 0)          # past the edge -> clamped to x=20 -> 11
    assert set(probe.pixels(cv)) == probe.words_of({11}, cv)

    cv.cls(0)
    api["draw_layer"](bg, -5, 0)           # negative -> clamped to x=0 -> 8
    assert set(probe.pixels(cv)) == probe.words_of({8}, cv)


def test_the_auto_batch_gate_matches_individual_spr_calls():
    # Canvas.spr_batch is no longer a cart verb (deleted 2026-08-14, plan 6.10) but it
    # is still the FLUSH the auto-batch gate calls when a run of plain spr()s ends, so
    # its pixels still have to equal the per-item reference -- that equality is the
    # whole promise of "just write the loop". Build two canvases, draw a few tiles (one
    # flipped) through each path, and assert the buffers are identical.
    sheet = SpriteSheet(16, 32)               # the spec sheet -- blit_batch gates on it
    sheet.tset(1, 0, 0, 8)                    # tile 1: red at (0,0)
    sheet.tset(1, 7, 0, 9)                    # ... and green at (7,0) (asymmetric -> flip shows)
    sheet.tset(2, 3, 3, 11)                   # tile 2: a centre pixel

    items = [(1, 5, 6), (2, 20, 8, 0), (1, 12, 14, 1)]   # last one h-flipped

    cv_batch = Canvas(40, 40)
    cv_batch.cls(0)
    cv_batch.spr_batch(sheet, items, colorkey=-1, scale=2)

    cv_indiv = Canvas(40, 40)
    cv_indiv.cls(0)
    for it in items:
        flip = it[3] if len(it) > 3 else 0
        cv_indiv.spr(sheet.tile_image(it[0], -1), it[1], it[2], 2, flip)

    assert probe.buffer_of(cv_batch) == probe.buffer_of(cv_indiv)
    assert probe.drew_something(cv_batch)    # sanity: it actually drew something


def test_the_batch_verbs_are_gone_from_the_cart_namespace():
    """spr_batch / rect_batch / spans must not be reachable from a cart.

    Deleted 2026-08-14 (plan 6.10): they bought <=1ms at realistic call counts and
    cost a split vocabulary, because a Lua trampoline cannot marshal a list or a
    span buffer -- so the same game in the two languages needed two draw loops.

    This is a pin, not a formality. The verbs were closures inside make_api and the
    namespace is a dict literal, so a re-add is one line in a 700-line function and
    would read as a helpful restoration. The reason it must not come back is a
    product decision, not a performance one, and nothing else in the suite states
    it.
    """
    from runtime import host_app
    cv = Canvas(20, 20)

    class _Input:
        def held(self, n):
            return False

        def pressed(self, n):
            return False

    api = host_app.make_api(cv, _Input(), {}, sheet=None)
    for gone in ("spr_batch", "rect_batch", "spans"):
        assert gone not in api, gone
    assert "spr" in api and "rect" in api      # the survivors, so this can't pass empty


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


# -- .moy store (moy_carts) ----------------------------------------------

def test_moy_carts_store_roundtrip(tmp_path):
    from runtime import moy_carts
    root = str(tmp_path / "carts")
    moy_carts.ensure_dirs(root)
    c = moy_carts.create("My Cart", root, src="def _draw():\n    cls(1)\n",
                         cfg={"a": 1}, type="app")
    assert c["title"] == "My Cart" and c["src"].startswith("def _draw")
    assert any(i["title"] == "My Cart" for i in moy_carts.scan(root))
    # save edited code + a sprite sheet; reload reflects both
    moy_carts.save_code(c, "def _draw():\n    cls(2)\n")
    moy_carts.save_sprites(c, "012\n345\n")
    moy_carts.save_map(c, TileMap(3, 2).to_hex())   # tilemap blob persists (#32)
    reloaded = moy_carts.load(c["path"])
    assert "cls(2)" in reloaded["src"]
    assert reloaded["sprites"].startswith("012")
    assert reloaded["map"] is not None
    assert TileMap.from_hex(reloaded["map"]).w == 3  # the saved map.moymap round-trips
    # duplicate makes an independent editable copy
    dup = moy_carts.duplicate(c, root, new_title="Copy")
    assert dup["title"] == "Copy" and dup["path"] != c["path"]


def test_moyimg_asset_roundtrip_and_image_accessor(tmp_path):
    # Paint-image assets (#63 Fold 3): a .moyimg blob saves to images/<name>.moyimg,
    # reloads as cart["images"], and the make_api image(name) accessor decodes it into
    # a big Image whose pixels are the original MOY64 indices -- placed with spr(img,...).
    import binascii
    import zlib
    from runtime import moy_carts, host_app

    root = str(tmp_path / "carts")
    moy_carts.ensure_dirs(root)
    c = moy_carts.create("Arty", root, src="def _draw():\n    cls(0)\n")

    # A 4x3 paint image: indices 0..11, zlib+base64 wrapped (the .moyimg envelope).
    w, h = 4, 3
    raw = bytes(range(w * h))
    import json as _json
    blob = _json.dumps({"format": "moyimg-v1", "w": w, "h": h,
                        "data": binascii.b2a_base64(zlib.compress(raw)).decode().strip()})
    moy_carts.save_image(c, "pic", blob)
    reloaded = moy_carts.load(c["path"])
    assert reloaded["images"] == {"pic": blob}          # the asset round-trips on disk

    # image("pic") returns the SAME Image across calls (memoised, so its bake cache is
    # stable), tagged _paint, with the decoded index pixels; image(name) misses -> None.
    class _Input:
        def held(self, n):
            return False

        def pressed(self, n):
            return False

    cv = Canvas(8, 8)
    api = host_app.make_api(cv, _Input(), {}, images=reloaded["images"])
    im = api["image"]("pic")
    assert im is not None and im.w == w and im.h == h
    assert bytes(im.pix) == raw and getattr(im, "_paint", False) is True
    assert api["image"]("pic") is im                    # memoised: same object
    assert api["image"]("missing") is None              # unknown asset -> None
    # The ASCII-art form of image() still works (dispatch on str vs rows list).
    ascii_im = api["image"](["#."], {"#": 8})
    assert ascii_im.w == 2

    # spr(paint image) places it opaquely in index space (a background at 0,0).
    cv.cls(0)
    api["spr"](im, 0, 0)
    assert [cv.pix(x, 0) for x in range(w)] == [0, 1, 2, 3]
    assert cv.pix(0, 1) == 4 and cv.pix(0, 2) == 8


# -- shared console: carts run, cards edit (host == device) -----------------

def test_console_runs_wallpaper_and_config_drives_content(tmp_path):
    from runtime import host_app
    ws = host_app.build_workstation(str(tmp_path / "carts"))
    _open_cart(ws, "Space Desktop")
    assert ws.screen == "desktop" and ws.ns is not None
    for _ in range(10):
        ws.frame(1 / 30)
    assert len(ws.ns["stars"]) == ws.config.get("star_count", 80)  # config drove it
    assert probe.drew_something(ws.canvas)                         # drew something


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


def _select_by_title(ws, title):
    for i, c in enumerate(ws.launcher.items):
        if c["title"] == title:
            ws.launcher.sel = i
            return
    raise AssertionError("no seed cart titled " + title)


def test_enter_in_config_plays_the_cart(tmp_path):
    """Fix 2: in the Config (cards) tab, the RUN/Enter key must PLAY the cart (re-run with
    the freshly-tuned config), NOT open the code editor. The device keyboard delivers Enter
    as the "a" button and the host delivers it as "run", so BOTH must play -- _leave_menu()
    -> EditorApp.leave()'s cards branch (_start with the tuned config + _save_config + run).
    The old cards handler mapped "a" -> the code editor, which is why a device tap of Enter
    "just entered code" instead of playing."""
    from runtime import host_app
    ws = host_app.build_workstation(str(tmp_path / "carts"))
    drv = host_app.ConsoleDriver(ws)

    # Open a GAME with edit cards in the Editor -> lands on the Config (cards) tab.
    _select_by_title(ws, "Star Catcher")
    ws.open_in_editor()
    assert ws.screen == "menu" and ws.menu_view == "cards"

    # Tweak a Config card, then press RUN (== the host's Enter): the cart PLAYS and the
    # tuned value is committed (leave()'s cards branch = GO's old job), NOT the code editor.
    ws.cards_layer.msel = 0
    before = dict(ws.config)
    ws.adjust(1)
    assert dict(ws.config) != before                 # a card value changed
    drv.press("run"); drv.frame(1 / 30); drv.frame(1 / 30)
    assert ws.screen == "desktop"                    # PLAYED the cart...
    assert ws.menu_view != "code"                    # ...did NOT open the code editor
    assert ws.cart["cfg"] == ws.config               # ...and the tuned config was saved

    # The device delivers Enter as the "a" button (0x0D -> "a"): it must ALSO play, which
    # is the exact bug the owner hit (Enter -> "a" -> code editor).
    ws.go_home()
    _select_by_title(ws, "Star Catcher")
    ws.open_in_editor()
    assert ws.menu_view == "cards"
    drv.press("a"); drv.frame(1 / 30); drv.frame(1 / 30)
    assert ws.screen == "desktop"                    # "a" (the device's Enter) PLAYED...
    assert ws.menu_view != "code"                    # ...did NOT "just enter code"


def test_paint_and_map_editors_fully_cover_the_running_cart(tmp_path):
    """Fix 3: the Sprites (paint) and Map editor tabs must fully cover the content area
    below the 18px bar -- no previously-running cart bleeding through. Both tabs draw over
    _draw_menu_backdrop()'s frozen cart frame, and their panels only span x 8..312 / y
    16..220, so before the fix the running cart leaked through the 8px side + 20px bottom
    strips. (Cards/code/blocks/music never leaked -- they fill the whole area.)"""
    from runtime import host_app, moy_carts
    carts_dir = str(tmp_path / "carts")
    ws = host_app.build_workstation(carts_dir)
    # A cart whose _draw floods the ENTIRE screen with a bright, unmistakable color.
    FLOOD = 8                                  # a non-black palette index; editor bg is 0
    moy_carts.create("Flood", carts_dir, src="def _draw():\n    cls(%d)\n" % FLOOD,
                     type="game", edit=[{"key": "c", "type": "int", "min": 0, "max": 9,
                                         "card": "C {value}"}])
    ws.launcher.set_items(moy_carts.scan(carts_dir))
    ws.launcher.sel = next(i for i, it in enumerate(ws.launcher.items)
                           if it["title"] == "Flood")
    ws.open_in_editor()                        # starts the cart, lands on the cards tab
    # The leak zones: 8px left/right strips (in the content area) + the 20px bottom strip.
    strips = [(3, 100), (316, 100), (3, 230), (160, 235)]
    for open_tab in (ws._open_paint, ws._open_map):
        open_tab()
        ws.mark_dirty()
        ws.frame(1 / 30)
        cv = ws.canvas
        for (x, y) in strips:
            assert cv.pix(x, y) != FLOOD, \
                "%s leaked the cart at %d,%d" % (open_tab.__name__, x, y)


def test_hop_quest_uses_tilemap_and_still_plays(tmp_path):
    # Hop Quest now draws its level with map() and reads collision with mget() (#32).
    # Verify the cart loads its tilemap, draws ground through map(), and the attract
    # auto-pilot still clears every coin + reaches the win banner (gameplay intact).
    from runtime import host_app
    ws = host_app.build_workstation(str(tmp_path / "carts"))
    _open_cart(ws, "Hop Quest")
    assert ws.cart["type"] == "game"
    assert ws.cart.get("map") and not ws.tilemap.is_blank()    # the level is a tilemap
    assert ws.ns["_solid"](0, 12) and not ws.ns["_solid"](0, 0)  # mget collision works
    ws.config["autoplay"] = 1
    ws.apply()
    coins = len(ws.ns["coins"])
    most, won = 0, False
    for _ in range(900):                    # attract-mode auto-play climbs the stair
        ws.frame(1 / 30)
        most = max(most, ws.ns.get("got", 0))
        if ws.ns.get("won", 0.0) > 0.0:
            won = True
    assert most == coins and won            # collected every coin and won the round
    assert probe.drew_something(ws.canvas)  # the map() blit drew the ground


def test_brick_siege_runs_with_tilemap_and_autoplay_progresses(tmp_path):
    # Brick Siege (#35): a top-down tank battle drawn over the cart's brick/steel
    # tilemap (map.moymap) with map()/mget()/mset(). Verify it loads its tilemap and
    # spawns a wave, then that the attract auto-pilot runs many frames without error
    # and actually PROGRESSES -- destroys enemies (score climbs) and the round ends
    # (a wave is cleared or the base/lives are lost, then it resets).
    from runtime import host_app
    ws = host_app.build_workstation(str(tmp_path / "carts"))
    _open_cart(ws, "Brick Siege")
    assert ws.cart["type"] == "game"
    assert ws.cart_error is None
    assert ws.cart.get("map") and not ws.tilemap.is_blank()     # the field is a tilemap
    # brick + steel are both present in the field (the two wall kinds)
    field = [ws.tilemap.mget(x, y) for y in range(ws.tilemap.h) for x in range(ws.tilemap.w)]
    assert 8 in field and 9 in field                            # brick (8) + steel (9)
    import random
    random.seed(1234)                       # deterministic attract run (spawns + auto-pilot use rnd())
    ws.config["autoplay"] = 1
    ws.apply()
    assert ws.ns["spawn_q"] + ws.ns["_alive_enemies"]() == ws.config["enemies"]
    best_score, states = 0, set()
    for _ in range(2400):                   # attract-mode auto-play hunts enemies (headroom to end a round)
        ws.frame(1 / 30)
        assert ws.cart_error is None        # never crash a frame
        best_score = max(best_score, ws.ns["score"])
        states.add(ws.ns["state"])
    assert best_score > 0                   # destroyed at least one enemy (scored)
    assert states != {0}                    # reached a win or game-over (round ended)
    assert probe.distinct_pixels(ws.canvas) > 3   # the map()/sprites drew the battlefield


def test_brick_siege_brick_crumbles_steel_stops(tmp_path):
    # Bullet vs walls: a player bullet into a BRICK cell clears it (mset -> empty),
    # while a STEEL cell is never destroyed. Drive a couple of shots straight into
    # each wall kind and check the tilemap before/after.
    from runtime import host_app
    ws = host_app.build_workstation(str(tmp_path / "carts"))
    _open_cart(ws, "Brick Siege")
    ns = ws.ns
    TS = ns["TS"]
    # find a brick and a steel cell in the loaded field
    brick = steel = None
    for y in range(ws.tilemap.h):
        for x in range(ws.tilemap.w):
            v = ws.tilemap.mget(x, y)
            if v == 8 and brick is None:
                brick = (x, y)
            elif v == 9 and steel is None:
                steel = (x, y)
    assert brick and steel
    # fire a bullet directly at the brick cell's center (owner 0 = player) and step
    bx, by = brick
    ns["bullets"].append([bx * TS + TS // 2, by * TS + TS // 2 - TS, 1, 0])  # heading down
    for _ in range(20):
        ws.frame(1 / 30)
        if ws.tilemap.mget(bx, by) < 0:
            break
    assert ws.tilemap.mget(bx, by) < 0           # brick crumbled to empty
    # a bullet into steel leaves it intact
    sx, sy = steel
    ns["bullets"].append([sx * TS + TS // 2, sy * TS + TS // 2 - TS, 1, 0])
    for _ in range(20):
        ws.frame(1 / 30)
    assert ws.tilemap.mget(sx, sy) == 9          # steel never destroyed


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
    assert probe.distinct_pixels_in(drv.rgb888(), 3) > 1                   # launcher renders
    drv.press("run")
    drv.frame(1 / 30)
    assert ws.screen == "desktop"                       # opened a cart (plays)
    drv.press("b")                                      # B belongs to the GAME while it
    drv.frame(1 / 30)                                   # plays -- no chrome, stays playing
    assert ws.screen == "desktop"
    # Stage 5 exit model: a running game exits via hold-BACKSPACE (covered in
    # test_desktop_shell); here we just leave to the launcher via go_home() (the ≡ HOME
    # action) -- robust regardless of the seed cart's type -- then open it in the Editor.
    ws.go_home()
    assert ws.screen == "launcher"                      # left the cart, back home
    ws.open_in_editor()                                 # maker landing -> the Editor
    if ws.menu_view == "cards":
        ws.set_menu_view("code")
    assert ws.menu_view == "code" and ws.editor is not None
    ws.editor.row, ws.editor.col = 0, 0
    for ch in "Z=9":
        drv.type_char(ord(ch))
        drv.frame(1 / 30)
    assert ws.editor.lines[0].startswith("Z=9")         # typed into the real source
    drv.frame(1 / 30)
    assert probe.distinct_pixels_in(drv.rgb888(), 3) > 1                   # code editor renders


def test_code_view_arrows_move_caret_and_scroll(tmp_path):
    from runtime import host_app
    ws = host_app.build_workstation(str(tmp_path / "carts"))
    drv = host_app.ConsoleDriver(ws)
    drv.press("run"); drv.frame(1 / 30)
    assert ws.screen == "desktop"            # launcher RUN plays the cart
    ws._open_menu()                          # Stage 5: reach the Editor (maker path -- pause
    if ws.menu_view == "cards":              # is gone, so no pause+B to open the menu)
        ws.set_menu_view("code")
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
    assert ws.screen == "desktop"            # launcher RUN plays the cart
    ws._open_menu()                          # Stage 5: reach the Editor (maker path -- pause
    if ws.menu_view == "cards":              # is gone, so no pause+B to open the menu)
        ws.set_menu_view("code")
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
    assert ws.screen == "desktop"            # launcher RUN plays the cart
    ws._open_menu()                          # Stage 5: reach the Editor (maker path -- pause
    if ws.menu_view == "cards":              # is gone, so no pause+B to open the menu)
        ws.set_menu_view("code")
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
    # RUN dissolved into the unified bar (Stage-4 rollout): the code tab shows the SAME
    # zoned bar as every editor, so PLAY (in the bar's lent left zone) runs the cart.
    # The code tab is system-canvas, so the bar uses the responsive layout.zone_left.
    from runtime import editor_app as EA, bar_layer as BL
    play_i = [t for t, _g in EA._ZONE_TABS].index(None)     # PLAY entry (tab is None)
    zx, zy, _zw, _zh = ws.layout.zone_left
    drv.click(zx + play_i * EA._ZONE_STRIDE + BL._BAR_ICON // 2, zy + BL._BAR_ICON // 2)
    drv.frame(1 / 30)
    assert ws.screen == "desktop"


def test_host_console_paint_via_mouse(tmp_path):
    from runtime import console as C
    from runtime import host_app
    ws = host_app.build_workstation(str(tmp_path / "carts"))
    drv = host_app.ConsoleDriver(ws)
    drv.press("run")
    drv.frame(1 / 30)
    ws.cart_error = "boom"   # Stage 5: the in-cart bar is CRASH chrome (pause retired)
    ws._dirty = True
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


# -- TIC-80-idiomatic Game Lab API (#11): mouse/time/key/keyp/pmem -----------

class _Stub:
    """Minimal canvas/input stubs for direct make_api unit tests (no display)."""
    w = 320
    h = 240

    def __getattr__(self, name):
        return lambda *a, **k: 0


class _StubInput:
    def held(self, n):
        return False

    def pressed(self, n):
        return False


class _Pointer:
    def __init__(self, x=0, y=0, click=False, down=False):
        self.x = x
        self.y = y
        self.click = click
        self.down = down


def test_mouse_aliases_touch_as_tic80_tuple():
    from runtime import host_app
    inp = _StubInput()
    api = host_app.make_api(_Stub(), inp, {})
    # No pointer -> all-zero 7-tuple (never None, unlike touch()).
    assert api["mouse"]() == (0, 0, False, False, False, 0, 0)
    inp.pointer = _Pointer(40, 70, click=True)
    x, y, left, mid, right, sx, sy = api["mouse"]()
    assert (x, y, left) == (40, 70, True)              # touch (x,y,tapped) -> x,y,left
    assert (mid, right, sx, sy) == (False, False, 0, 0)  # no middle/right/scroll
    # touch() returns (x, y, tapped, held): `held` mirrors pointer.down so a
    # cart can follow a DRAG (drawing); a bare tap edge reads held=False.
    assert api["touch"]() == (40, 70, True, False)
    inp.pointer = _Pointer(41, 71, click=False, down=True)   # finger dragging
    assert api["touch"]() == (41, 71, False, True)


def test_time_advances_with_cart_clock():
    from runtime import host_app
    from runtime import console as C
    inp = _StubInput()
    inp.cart_start_ms = C._ticks_ms()
    api = host_app.make_api(_Stub(), inp, {})
    t0 = api["time"]()
    assert t0 >= 0
    # Move the start back 50ms; time() = now - start must grow accordingly.
    inp.cart_start_ms = C._ticks_diff(inp.cart_start_ms, 50)
    assert api["time"]() >= t0 + 50


def test_key_and_keyp_reflect_current_frame_key():
    from runtime import host_app
    inp = _StubInput()
    api = host_app.make_api(_Stub(), inp, {})
    a, b = ord("a"), ord("b")
    # No key held this frame.
    assert api["key"]() == 0 and api["key"](a) is False
    assert api["keyp"]() == 0 and api["keyp"](a) is False
    # Workstation.frame sets cart_key (held this frame) + cart_keyp (the edge).
    inp.cart_key = a
    inp.cart_keyp = a
    assert api["key"]() == a and api["key"](a) is True and api["key"](b) is False
    assert api["keyp"](a) is True
    # Next frame the same key is still held but the edge is gone.
    inp.cart_keyp = 0
    assert api["key"](a) is True and api["keyp"](a) is False


def test_pmem_round_trips_and_persists_to_pmem_json(tmp_path):
    from runtime import moy_carts
    from runtime import console as C
    root = str(tmp_path / "carts")
    moy_carts.ensure_dirs(root)
    cart = moy_carts.create("Saver", root, src="def _draw():\n    pass\n")
    # Fresh cart: pmem reads all zero.
    cells = moy_carts.load_pmem(cart["path"])
    assert cells == [0] * 256

    writes = []
    pm = C.Pmem(cells, on_write=lambda values: writes.append(list(values)))
    assert pm.cell(0) == 0
    assert pm.cell(0, 1234) == 1234 and pm.cell(0) == 1234   # write then read back
    assert pm.cell(255, 7) == 7                              # last cell works
    assert pm.cell(256, 9) == 0 and pm.cell(-1, 9) == 0      # out of range -> 0, no-op
    # Writes are RAM-only until flush() (#66 deferred persistence: the per-write
    # SD save was Letter Blitz's measured 81-130ms mid-play hitch).
    assert writes == []
    assert pm.flush() is True and len(writes) == 1
    assert writes[-1][0] == 1234 and writes[-1][255] == 7
    # A no-change write must NOT re-dirty (a clean flush never touches the SD).
    pm.cell(0, 1234)
    assert pm.flush() is False and len(writes) == 1

    # Persist for real + reload sees it (no pmem.json existed before).
    moy_carts.save_pmem(cart, pm.cells)
    reloaded = moy_carts.load_pmem(cart["path"])
    assert reloaded[0] == 1234 and reloaded[255] == 7
    # 32-bit unsigned mask: values are stored & re-read masked.
    pm2 = C.Pmem(reloaded)
    assert pm2.cell(1, 0x1_0000_0001) == 1                   # wraps to 32 bits


def test_pmem_persists_through_workstation_open(tmp_path):
    from runtime import host_app
    from runtime import moy_carts
    carts_dir = str(tmp_path / "carts")
    # Create a cart whose _init bumps a high-score counter in pmem, before the
    # workstation scans the dir, so the launcher picks it up.
    moy_carts.ensure_dirs(carts_dir)
    src = (
        "def _init():\n"
        "    pmem(0, pmem(0) + 1)\n"
        "def _draw():\n"
        "    pass\n"
    )
    moy_carts.create("Counter", carts_dir, src=src)

    ws = host_app.build_workstation(carts_dir)
    _open_cart(ws, "Counter")
    assert ws.screen == "desktop" and ws.cart_error is None
    assert ws.pmem.cell(0) == 1                  # _init bumped it once
    # It persisted to pmem.json: reopening loads the saved value and bumps again.
    ws.open()                                    # re-open the same selected cart
    assert ws.pmem.cell(0) == 2


def test_key_keyp_plumbed_through_console_driver(tmp_path):
    # End-to-end: a cart reads key()/keyp(); the host ConsoleDriver feeds a typed
    # byte into input.last_key and Workstation.frame resolves the held/edge state.
    from runtime import host_app
    from runtime import moy_carts
    carts_dir = str(tmp_path / "carts")
    moy_carts.ensure_dirs(carts_dir)
    src = (
        "log = []\n"
        "def _update(dt):\n"
        "    log.append((key(), keyp(), key(ord('a')), keyp(ord('a'))))\n"
        "def _draw():\n"
        "    pass\n"
    )
    moy_carts.create("Keys", carts_dir, src=src)
    ws = host_app.build_workstation(carts_dir)
    drv = host_app.ConsoleDriver(ws)
    _open_cart(ws, "Keys")
    assert ws.screen == "desktop" and ws.cart_error is None
    a = ord("a")
    drv.type_char(a); drv.frame(1 / 30)        # frame 1: press edge
    drv.type_char(a); drv.frame(1 / 30)        # frame 2: still held, no edge
    drv.frame(1 / 30)                          # frame 3: released
    log = ws.ns["log"]
    assert log[0] == (a, a, True, True)        # held + pressed-this-frame
    assert log[1] == (a, 0, True, False)       # held, edge gone
    assert log[2] == (0, 0, False, False)      # released


# -- map (tilemap) editor (#32) --------------------------------------------

def test_map_editor_place_erase_select_pick_pan():
    from runtime.editors import MapEditor
    tm = TileMap(20, 15)
    sheet = SpriteSheet()
    me = MapEditor(tm, sheet)
    me.n = 7
    me.place(2, 3)                          # stamp the current tile
    assert tm.mget(2, 3) == 7
    me.erase(2, 3)                          # erase clears it back to empty
    assert tm.mget(2, 3) == TileMap.EMPTY
    me.select(1)                            # step the brush through the sheet ids
    assert me.n == 8
    tm.mset(5, 5, 12)
    me.pick(5, 5)                           # pick samples a placed cell into the brush
    assert me.n == 12
    me.pick(0, 0)                           # a tap on an empty cell leaves the brush
    assert me.n == 12
    me.pan(3, 2)
    assert (me.cam_x, me.cam_y) == (3, 2)
    me.pan(-10, -10)                        # clamps at the map edge (never < 0)
    assert (me.cam_x, me.cam_y) == (0, 0)
    me.pan(999, 999)                        # clamps to the last cell (never off the map)
    assert me.cam_x == tm.w - 1 and me.cam_y == tm.h - 1


def test_map_editor_size_brush_stamps_and_erases_blocks():
    # #57: SIZE=2 places a 16x16 sprite's 4 CONSECUTIVE tile ids in one tap --
    # n / n+1 / n+cols / n+cols+1, the same contiguous layout spr(w=2, h=2) and
    # tile_span_image read -- and erase clears the whole block. Size 1 stays the
    # old single-cell behavior; cycle_size wraps 1 -> 2 -> 3 -> 1.
    from runtime.editors import MapEditor
    tm = TileMap(20, 15)
    sheet = SpriteSheet()                    # 16 cols -> the row stride is 16
    me = MapEditor(tm, sheet)
    assert me.size == 1                      # default: no behavior change
    me.n = 7
    me.cycle_size()
    assert me.size == 2
    me.place(2, 3)
    assert tm.mget(2, 3) == 7 and tm.mget(3, 3) == 8
    assert tm.mget(2, 4) == 7 + 16 and tm.mget(3, 4) == 8 + 16
    me.erase(2, 3)                           # the eraser covers the same block
    for dy in range(2):
        for dx in range(2):
            assert tm.mget(2 + dx, 3 + dy) == TileMap.EMPTY
    me.cycle_size()
    assert me.size == 3
    me.cycle_size()                          # wraps back to a single tile
    assert me.size == 1
    # The EMPTY brush with SIZE > 1 clears the block (place == erase, like before).
    me.size = 2
    for dy in range(2):
        for dx in range(2):
            tm.mset(8 + dx, 8 + dy, 9)
    me.n = -1
    me.place(8, 8)
    for dy in range(2):
        for dx in range(2):
            assert tm.mget(8 + dx, 8 + dy) == TileMap.EMPTY


def test_map_editor_size_brush_clamps_at_map_and_sheet_edges():
    # #57 acceptance: the stamp clamps at BOTH edges. Cells past the map edge are
    # simply dropped, and a brush near the sheet's right/bottom edge must never
    # wrap ids into the next tile row -- the span clamps exactly like
    # tile_span_image, so the stamp still matches what spr() would draw.
    from runtime.editors import MapEditor
    sheet = SpriteSheet()                    # 16x16 tiles
    tm = TileMap(20, 15)
    me = MapEditor(tm, sheet)
    me.size = 2
    me.n = 0
    me.place(19, 14)                         # bottom-right map corner
    assert tm.mget(19, 14) == 0              # the in-map cell landed, the rest dropped
    me.n = 15                                # last sheet COLUMN: only 1 tile fits right
    assert me.stamp_span() == (1, 2)
    me.place(5, 5)
    assert tm.mget(5, 5) == 15 and tm.mget(5, 6) == 15 + 16
    assert tm.mget(6, 5) == TileMap.EMPTY    # no id wrap into the next sheet row
    me.n = 15 * 16                           # last sheet ROW: only 1 tile fits down
    assert me.stamp_span() == (2, 1)
    me.place(10, 10)
    assert tm.mget(10, 10) == 240 and tm.mget(11, 10) == 241
    assert tm.mget(10, 11) == TileMap.EMPTY


def test_map_size_stamp_renders_identical_to_spr_multitile():
    # #57 acceptance: the stamped block renders byte-identical to the code-drawn
    # spr(n, x, y, w=2, h=2) -- map() over the stamped cells vs one spr() of the
    # same tile span at the same pixel origin.
    from runtime.editors import MapEditor
    sheet = SpriteSheet(16, 32)              # the spec sheet -- map() gates on it
    for t, c in ((7, 1), (8, 2), (7 + 16, 3), (8 + 16, 4)):
        for py in range(8):
            for px in range(8):
                sheet.tset(t, px, py, c)
    tm = TileMap(4, 4)
    me = MapEditor(tm, sheet)
    me.n = 7
    me.size = 2
    me.place(1, 1)
    a = Canvas(40, 40)
    a.cls(0)
    a.map(tm, sheet, 0, 0, 4, 4, 0, 0, -1, 1)
    b = Canvas(40, 40)
    b.cls(0)
    b.spr(sheet.tile_span_image(7, 2, 2, -1), 8, 8, 1)
    pix_a = [a.pix(x, y) for y in range(40) for x in range(40)]
    pix_b = [b.pix(x, y) for y in range(40) for x in range(40)]
    assert pix_a == pix_b
    assert set(pix_a) >= {0, 1, 2, 3, 4}     # all four tiles actually rendered


# -- map editor undo/redo + rect/flood tools + resize (#91) -----------------

def test_map_editor_undo_redo_across_gestures():
    # #91: each COMPLETED gesture (bracketed by begin_edit/end_edit) is ONE undo
    # step recording only the changed cells; undo/redo walk the stack, redo is
    # dropped by a fresh edit, and the depth is bounded.
    from runtime.editors import MapEditor
    tm = TileMap(8, 8)
    me = MapEditor(tm, SpriteSheet())
    me.n = 3
    me.begin_edit(); me.place(1, 1); me.end_edit()   # gesture 1
    me.n = 5
    me.begin_edit(); me.place(2, 2); me.end_edit()   # gesture 2
    assert tm.mget(1, 1) == 3 and tm.mget(2, 2) == 5
    assert me.can_undo() and not me.can_redo()

    assert me.undo() is True                          # undo gesture 2
    assert tm.mget(2, 2) == TileMap.EMPTY and tm.mget(1, 1) == 3
    assert me.undo() is True                          # undo gesture 1
    assert tm.mget(1, 1) == TileMap.EMPTY
    assert me.undo() is False                          # floor: nothing left
    assert me.can_redo()

    assert me.redo() is True and tm.mget(1, 1) == 3   # redo gesture 1
    assert me.redo() is True and tm.mget(2, 2) == 5   # redo gesture 2
    assert me.redo() is False

    # A brand-new edit after an undo drops the redo branch (classic model).
    me.undo()                                          # back to just gesture 1
    assert me.can_redo()
    me.n = 9
    me.begin_edit(); me.place(4, 4); me.end_edit()
    assert not me.can_redo() and tm.mget(4, 4) == 9

    # An empty gesture (no cell changed) is not pushed as a step.
    depth = len(me._undo)
    me.begin_edit(); me.end_edit()
    assert len(me._undo) == depth


def test_map_editor_undo_depth_is_bounded():
    # #91: the undo stack never grows past UNDO_MAX (device RAM); the oldest step
    # is dropped so recent edits stay undoable.
    from runtime.editors import MapEditor
    tm = TileMap(64, 4)
    me = MapEditor(tm, SpriteSheet())
    me.n = 1
    for x in range(me.UNDO_MAX + 10):
        me.begin_edit(); me.place(x, 0); me.end_edit()
    assert len(me._undo) == me.UNDO_MAX


def test_map_editor_rect_fill_bounds_and_erase():
    # #91: fill_rect fills the inclusive rectangle with the brush, normalizes the
    # corners (any drag direction), clamps to the map, and honors the eraser.
    from runtime.editors import MapEditor
    tm = TileMap(10, 8)
    me = MapEditor(tm, SpriteSheet())
    me.n = 4
    me.begin_edit(); me.fill_rect(4, 3, 2, 1); me.end_edit()   # corners reversed
    for y in range(1, 4):
        for x in range(2, 5):
            assert tm.mget(x, y) == 4
    assert tm.mget(1, 1) == TileMap.EMPTY                       # just outside
    assert tm.mget(5, 3) == TileMap.EMPTY
    changed = sum(1 for c in tm.cells if c)
    assert changed == 3 * 3                                     # exactly the 3x3 block

    # A rect that runs off the map clamps to the edge (no error, fills what fits).
    me.n = 6
    me.begin_edit(); me.fill_rect(8, 6, 99, 99); me.end_edit()
    assert tm.mget(9, 7) == 6 and tm.mget(8, 6) == 6

    # ERASE (or the EMPTY brush) fills a rectangle with sky.
    me.begin_edit(); me.fill_rect(0, 0, 9, 7, erase=True); me.end_edit()
    assert tm.is_blank()

    # The whole fill is ONE undo step.
    assert me.undo() is True and not tm.is_blank()


def test_map_editor_flood_fill_same_tile_noop_and_full_map():
    # #91: flood fills the contiguous same-tile region iteratively; a same-tile
    # fill is a no-op, and a blank map floods entirely.
    from runtime.editors import MapEditor
    tm = TileMap(6, 5)
    me = MapEditor(tm, SpriteSheet())

    # Full-map flood on a blank grid: every cell becomes the brush.
    me.n = 2
    me.begin_edit(); me.flood(0, 0); me.end_edit()
    assert all(tm.mget(x, y) == 2 for y in range(5) for x in range(6))

    # Same-tile flood is a no-op (target == brush) -- no undo step recorded.
    depth = len(me._undo)
    me.begin_edit(); me.flood(3, 3); me.end_edit()
    assert len(me._undo) == depth

    # A bounded region: flood only the contiguous same-tile blob, stopping at a
    # different-tile wall.
    tm2 = TileMap(5, 1)
    me2 = MapEditor(tm2, SpriteSheet())
    tm2.mset(2, 0, 9)                       # a wall splits the row into [0,1] | [3,4]
    me2.n = 4
    me2.begin_edit(); me2.flood(0, 0); me2.end_edit()
    assert tm2.mget(0, 0) == 4 and tm2.mget(1, 0) == 4
    assert tm2.mget(2, 0) == 9             # the wall is untouched
    assert tm2.mget(3, 0) == TileMap.EMPTY  # the far side never floods
    assert me2.undo() is True and tm2.mget(0, 0) == TileMap.EMPTY  # one step


def test_map_editor_big_batch_compacts_to_bounded_snapshot():
    # #91 review fix: a gesture touching a large share of the map (a full-map
    # FLOOD/RECT -- up to 9216 cells on the 96x96 UI max) must NOT retain one
    # (idx, prev, new) tuple per cell (~hundreds of KB on MicroPython). end_edit
    # compacts such a batch to a ("snap", w, h, before, after) whole-map snapshot
    # -- 2 bytes/cell, bounded -- and undo/redo replay it exactly like a delta.
    from runtime.editors import MapEditor
    tm = TileMap(96, 96)
    me = MapEditor(tm, SpriteSheet())
    tm.mset(10, 10, 7)                       # pre-existing content to restore
    me.n = 2
    me.begin_edit(); me.flood(0, 0); me.end_edit()      # full-map flood
    assert tm.mget(0, 0) == 2 and tm.mget(95, 95) == 2
    assert tm.mget(10, 10) == 7             # the walled cell survived the flood

    step = me._undo[-1]
    assert step[0] == "snap"                             # #111: snapshot op form
    assert (step[1], step[2]) == (96, 96)
    # 2 HEX blobs, 2 chars/cell (#111: JSON-able payloads, not raw bytes())
    assert len(step[3]) == 96 * 96 * 2 and len(step[4]) == 96 * 96 * 2

    assert me.undo() is True                 # snapshot undo restores the whole map
    assert tm.mget(0, 0) == TileMap.EMPTY and tm.mget(95, 95) == TileMap.EMPTY
    assert tm.mget(10, 10) == 7
    assert me.redo() is True                 # ...and redo re-applies it
    assert tm.mget(0, 0) == 2 and tm.mget(95, 95) == 2 and tm.mget(10, 10) == 7

    # A small gesture stays the cheap delta form (no snapshot for a 1-cell stamp).
    me.n = 4                                 # different tile so the stamp changes it
    me.begin_edit(); me.place(5, 5); me.end_edit()
    assert type(me._undo[-1]) is list

    # A big RECT compacts the same way and round-trips.
    me.n = 9
    me.begin_edit(); me.fill_rect(0, 0, 95, 95); me.end_edit()
    assert me._undo[-1][0] == "snap"
    assert me.undo() is True and tm.mget(0, 0) == 2      # back to the flood state
    assert me.redo() is True and tm.mget(0, 0) == 9


def test_tilemap_resize_preserves_content_and_roundtrips():
    # #91: resize grows/shrinks in place, preserving the overlapping top-left
    # content; the new dims serialize + round-trip through map.moymap (to_hex has
    # a `w h` header, so from_hex reconstructs the grown/shrunk grid).
    tm = TileMap(4, 3)
    for y in range(3):
        for x in range(4):
            tm.mset(x, y, x + y * 4)        # a recognizable pattern
    gen0 = tm.gen

    tm.resize(6, 5)                          # GROW: content kept, new cells empty
    assert (tm.w, tm.h) == (6, 5)
    assert tm.gen > gen0 and tm.dirty
    for y in range(3):
        for x in range(4):
            assert tm.mget(x, y) == x + y * 4
    assert tm.mget(5, 4) == TileMap.EMPTY    # a fresh cell is empty
    assert tm.mget(4, 0) == TileMap.EMPTY

    rt = TileMap.from_hex(tm.to_hex())       # serialize round-trip at the new dims
    assert (rt.w, rt.h) == (6, 5)
    for y in range(3):
        for x in range(4):
            assert rt.mget(x, y) == x + y * 4

    tm.resize(2, 2)                          # SHRINK: the surviving corner is kept
    assert (tm.w, tm.h) == (2, 2)
    assert tm.mget(0, 0) == 0 and tm.mget(1, 1) == 1 + 4
    rt2 = TileMap.from_hex(tm.to_hex())
    assert (rt2.w, rt2.h) == (2, 2) and rt2.mget(1, 1) == 5

    tm.resize(0, 0)                          # clamps to a minimum 1x1
    assert (tm.w, tm.h) == (1, 1)


def test_host_console_map_size_brush_stamps_block_via_taps(tmp_path):
    # #57 in the shell: the SIZE button cycles the stamp size, one tap with
    # SIZE=2 places the sprite's 4 tiles, and an ERASE tap clears the block.
    from runtime import console as C
    from runtime import host_app
    ws = host_app.build_workstation(str(tmp_path / "carts"))
    ws.launcher.sel = 0
    ws.open()
    ws._open_map()
    assert ws.menu_view == "map"
    drv = host_app.ConsoleDriver(ws)
    me = ws.map_ui.mapedit
    drv.click(C._MAP_SIZE[0] + 2, C._MAP_SIZE[1] + 2)
    drv.frame(1 / 30)
    assert me.size == 2
    me.n = 5
    cols = ws.project.sheet.cols
    cx, cy = me.cam_x, me.cam_y
    drv.click(C._MV_X0 + 2, C._MV_Y0 + 2)    # tap the top-left visible cell
    drv.frame(1 / 30)
    assert ws.tilemap.mget(cx, cy) == 5 and ws.tilemap.mget(cx + 1, cy) == 6
    assert ws.tilemap.mget(cx, cy + 1) == 5 + cols
    assert ws.tilemap.mget(cx + 1, cy + 1) == 6 + cols
    drv.click(C._MAP_ERASE[0] + 2, C._MAP_ERASE[1] + 2)
    drv.frame(1 / 30)
    drv.click(C._MV_X0 + 2, C._MV_Y0 + 2)    # the eraser clears the whole block
    drv.frame(1 / 30)
    for dy in range(2):
        for dx in range(2):
            assert ws.tilemap.mget(cx + dx, cy + dy) == TileMap.EMPTY


def test_host_console_map_open_place_and_render(tmp_path):
    from runtime import console as C
    from runtime import host_app
    ws = host_app.build_workstation(str(tmp_path / "carts"))
    drv = host_app.ConsoleDriver(ws)
    drv.press("run")
    drv.frame(1 / 30)
    ws.cart_error = "boom"   # Stage 5: the in-cart bar is CRASH chrome (pause retired)
    ws._dirty = True
    drv.click(C._MAP_BTN[0] + 2, C._MAP_BTN[1] + 2)      # open the MAP overlay button
    drv.frame(1 / 30)
    assert ws.menu_view == "map" and ws.map_ui.mapedit is not None
    # pick the 2nd palette tile (id == map_page + 1) ...
    px = C._TP_X0 + 1 * C._TP_CELL
    py = C._TP_Y0
    drv.click(px + 2, py + 2)
    drv.frame(1 / 30)
    assert ws.map_ui.mapedit.n == ws.map_ui.map_page + 1
    # ... then stamp it onto the top-left visible map cell and confirm mget reflects it
    drv.click(C._MV_X0 + 2, C._MV_Y0 + 2)
    drv.frame(1 / 30)
    cx = ws.map_ui.mapedit.cam_x
    cy = ws.map_ui.mapedit.cam_y
    assert ws.tilemap.mget(cx, cy) == ws.map_ui.mapedit.n
    assert probe.distinct_pixels_in(drv.rgb888(), 3) > 1                    # the map view rendered


def test_host_console_map_erase_and_pan(tmp_path):
    from runtime import console as C
    from runtime import host_app
    ws = host_app.build_workstation(str(tmp_path / "carts"))
    drv = host_app.ConsoleDriver(ws)
    drv.press("run"); drv.frame(1 / 30)
    ws.cart_error = "boom"   # Stage 5: the in-cart bar is CRASH chrome (pause retired)
    ws._dirty = True
    drv.click(C._MAP_BTN[0] + 2, C._MAP_BTN[1] + 2); drv.frame(1 / 30)
    ws.map_ui.mapedit.n = 3
    drv.click(C._MV_X0 + 2, C._MV_Y0 + 2); drv.frame(1 / 30)   # stamp tile 3 at (0,0)
    assert ws.tilemap.mget(0, 0) == 3
    drv.click(C._MAP_ERASE[0] + 2, C._MAP_ERASE[1] + 2); drv.frame(1 / 30)  # ERASE on
    assert ws.map_ui.map_erase
    drv.click(C._MV_X0 + 2, C._MV_Y0 + 2); drv.frame(1 / 30)   # now a tap erases
    assert ws.tilemap.mget(0, 0) == TileMap.EMPTY
    # Zoom IN so the map is bigger than the view, then pan right (the fit-both default
    # shows the whole map -> the camera is pinned at 0 with nothing to scroll).
    ws.map_ui.map_zoom = len(C._MV_ZOOMS) - 1
    x0m, y0m, cell, cols, rows = ws.map_ui._mv_metrics()
    assert ws.tilemap.w > cols                                 # room to pan at this zoom
    drv.click(C._PAN_RT[0] + 2, C._PAN_RT[1] + 2); drv.frame(1 / 30)        # pan right
    assert ws.map_ui.mapedit.cam_x == 1


def test_host_console_map_save_roundtrips(tmp_path):
    from runtime import console as C
    from runtime import host_app, moy_carts
    carts_dir = str(tmp_path / "carts")
    ws = host_app.build_workstation(carts_dir)
    drv = host_app.ConsoleDriver(ws)
    drv.press("run"); drv.frame(1 / 30)
    cart_path = ws.cart["path"]
    ws.cart_error = "boom"   # Stage 5: the in-cart bar is CRASH chrome (pause retired)
    ws._dirty = True
    drv.click(C._MAP_BTN[0] + 2, C._MAP_BTN[1] + 2); drv.frame(1 / 30)
    ws.map_ui.mapedit.n = 6
    drv.click(C._MV_X0 + 2, C._MV_Y0 + 2); drv.frame(1 / 30)   # stamp tile 6 at (0,0)
    # No SAVE button (#111) -- ws.save_map is the hard-commit verb every exit path
    # (tab switch/PLAY/CLOSE/...) now dispatches automatically.
    ws.save_map()
    assert ws.save_status is None      # invisible save: no failure, no "SAVED"
    reloaded = moy_carts.load(cart_path)                  # map.moymap persisted on disk
    assert reloaded["map"] is not None
    assert TileMap.from_hex(reloaded["map"]).mget(0, 0) == 6


def test_map_edit_seen_by_running_cart_via_gen(tmp_path):
    # An mset bumps tilemap.gen, the parity hook a running cart's map cache watches,
    # so a placement made in the editor is reflected immediately (the editor edits
    # the SAME TileMap object the cart's map()/mget() read).
    from runtime import console as C
    from runtime import host_app
    ws = host_app.build_workstation(str(tmp_path / "carts"))
    drv = host_app.ConsoleDriver(ws)
    drv.press("run"); drv.frame(1 / 30)
    tm = ws.tilemap
    before = tm.gen
    ws.cart_error = "boom"   # Stage 5: the in-cart bar is CRASH chrome (pause retired)
    ws._dirty = True
    drv.click(C._MAP_BTN[0] + 2, C._MAP_BTN[1] + 2); drv.frame(1 / 30)
    drv.click(C._MV_X0 + 2, C._MV_Y0 + 2); drv.frame(1 / 30)   # stamp a cell
    assert tm.gen > before                                # mset bumped gen (live pickup)
    assert tm is ws.tilemap                               # same object the cart's api holds


# -- map editor zoom levels (#37 follow-up) --------------------------------

def _open_cart_map(tmp_path, cart_name):
    """Build a workstation, open the `<cart_name>.moy` seed cart, then enter the
    map editor. Returns (C, ws, drv)."""
    import os
    from runtime import console as C
    from runtime import host_app
    ws = host_app.build_workstation(str(tmp_path / "carts"))
    want = cart_name + ".moy"
    sel = None
    for i in range(len(ws.launcher.items)):
        path = ws.launcher.items[i].get("path") or ""
        if os.path.basename(path) == want:
            sel = i
            break
    assert sel is not None, "cart %r not seeded" % cart_name
    ws.launcher.sel = sel
    ws.open()
    ws._open_map()
    assert ws.menu_view == "map" and ws.map_ui.mapedit is not None
    return C, ws, host_app.ConsoleDriver(ws)


def test_map_default_zoom_fits_whole_shipped_maps(tmp_path):
    # The DEFAULT (most zoomed-OUT) zoom MUST show the ENTIRE map of both shipped
    # games with the camera at (0,0) and zero panning: brick_siege is 15x15 and
    # platformer is 20x13. So the default view must hold >= the map's cols AND rows.
    from runtime import console as C
    for name, w, h in (("brick_siege", 15, 15), ("platformer", 20, 13)):
        _C, ws, _drv = _open_cart_map(tmp_path / name, name)
        assert (ws.tilemap.w, ws.tilemap.h) == (w, h)
        assert ws.map_ui.map_zoom == 0                          # opens at the fit-both default
        assert (ws.map_ui.mapedit.cam_x, ws.map_ui.mapedit.cam_y) == (0, 0)   # cam pinned to origin
        x0, y0, cell, cols, rows = ws.map_ui._mv_metrics()
        assert cols >= w and rows >= h                   # the whole map is on screen
        # Every map cell maps to a pixel inside the visible map-view rectangle, so no
        # cell is off-screen at the default zoom.
        area = ws.map_ui._mv_area()
        for cy in (0, h - 1):
            for cx in (0, w - 1):
                px = x0 + cx * cell + cell // 2
                py = y0 + cy * cell + cell // 2
                assert C._in(px, py, area)
                assert ws.map_ui._map_cell_at(px, py) == (cx, cy)


def test_map_cycle_zoom_increases_cell_and_shrinks_view(tmp_path):
    # Cycling the zoom steps IN: the cell size strictly grows and the visible cell
    # count strictly shrinks, level by level, until it wraps back to the default.
    from runtime import console as C
    _C, ws, drv = _open_cart_map(tmp_path, "brick_siege")
    seen = []
    for _ in range(len(C._MV_ZOOMS)):
        x0, y0, cell, cols, rows = ws.map_ui._mv_metrics()
        seen.append((ws.map_ui.map_zoom, cell, cols * rows))
        drv.click(C._MAP_ZOOM[0] + 2, C._MAP_ZOOM[1] + 2)   # tap ZOOM -> next level
        drv.frame(1 / 30)
    # Back to the default after a full cycle.
    assert ws.map_ui.map_zoom == 0
    # Ascending cell size, descending visible-cell count across the levels.
    for k in range(1, len(seen)):
        assert seen[k][1] > seen[k - 1][1]               # bigger cells
        assert seen[k][2] < seen[k - 1][2]               # fewer visible cells


def test_map_tap_and_sky_hit_right_cell_after_zoom(tmp_path):
    # After a zoom change tap-paint and the SKY (empty) brush still land on the cell
    # under the pointer -- hit-testing follows the live cell size.
    from runtime import console as C
    _C, ws, drv = _open_cart_map(tmp_path, "brick_siege")
    ws.map_ui.map_zoom = 2                                       # a zoomed-IN level
    ws.map_ui.mapedit.cam_x = ws.map_ui.mapedit.cam_y = 0
    x0, y0, cell, cols, rows = ws.map_ui._mv_metrics()
    # Tap cell (2, 1) with a real brush tile -> exactly that cell gets the tile.
    ws.map_ui.mapedit.n = 5
    px = x0 + 2 * cell + cell // 2
    py = y0 + 1 * cell + cell // 2
    drv.touch(px, py); drv.frame(1 / 30); drv.touch_up(); drv.frame(1 / 30)
    assert ws.tilemap.mget(2, 1) == 5
    # Pick SKY then tap the same cell -> it clears to EMPTY (the right cell, at zoom).
    drv.click(C._TP_SKY[0] + 2, C._TP_SKY[1] + 2); drv.frame(1 / 30)
    assert ws.map_ui.mapedit.n < 0
    drv.touch(px, py); drv.frame(1 / 30); drv.touch_up(); drv.frame(1 / 30)
    assert ws.tilemap.mget(2, 1) == ws.tilemap.EMPTY


def test_map_pan_works_zoomed_in_and_clamps(tmp_path):
    # Zoomed IN (map bigger than the view), the d-pad pans the camera; panning is
    # clamped so the camera never scrolls the map off the window.
    from runtime import console as C
    _C, ws, drv = _open_cart_map(tmp_path, "brick_siege")
    ws.map_ui.map_zoom = len(C._MV_ZOOMS) - 1                   # most zoomed-in
    x0, y0, cell, cols, rows = ws.map_ui._mv_metrics()
    assert ws.tilemap.w > cols and ws.tilemap.h > rows   # room to pan
    drv.click(C._PAN_RT[0] + 2, C._PAN_RT[1] + 2); drv.frame(1 / 30)
    assert ws.map_ui.mapedit.cam_x == 1
    drv.click(C._PAN_DN[0] + 2, C._PAN_DN[1] + 2); drv.frame(1 / 30)
    assert ws.map_ui.mapedit.cam_y == 1
    # Pan hard right past the edge: clamps so the last column stays visible.
    for _ in range(40):
        drv.click(C._PAN_RT[0] + 2, C._PAN_RT[1] + 2); drv.frame(1 / 30)
    assert ws.map_ui.mapedit.cam_x == ws.tilemap.w - cols       # clamped to the right edge
    assert ws.map_ui.mapedit.cam_x + cols == ws.tilemap.w


# -- TIC-80 draw verbs cluster 2 (#11): clip / camera / spr-flip / pal / palt -

def test_clip_suppresses_out_of_rect_and_resets():
    cv = Canvas(32, 24)
    cv.cls(0)
    cv.clip(8, 8, 8, 8)            # only [8,16)x[8,16) draws
    cv.rect(0, 0, 32, 24, 8)       # full-screen fill, clipped to the rect
    assert cv.pix(8, 8) == 8 and cv.pix(15, 15) == 8
    assert cv.pix(7, 8) == 0 and cv.pix(16, 8) == 0     # just outside -> suppressed
    assert cv.pix(0, 0) == 0 and cv.pix(31, 23) == 0
    cv.clip()                      # reset to full screen
    cv.rect(0, 0, 32, 24, 11)
    assert cv.pix(0, 0) == 11 and cv.pix(31, 23) == 11  # now everything draws


def test_clip_also_bounds_pix_line_circ_spr():
    cv = Canvas(32, 24)
    cv.cls(0)
    cv.clip(10, 10, 4, 4)
    cv.pix(2, 2, 8)                # outside -> dropped
    cv.pix(11, 11, 9)             # inside -> drawn
    cv.circ(11, 11, 20, 14)       # huge circle, clipped to the 4x4 box
    cv.line(0, 0, 31, 23, 7)      # diagonal, only its in-box pixels survive
    assert cv.pix(2, 2) == 0
    # every set pixel must lie within the clip rect
    for y in range(24):
        for x in range(32):
            if cv.pix(x, y) != 0:
                assert 10 <= x < 14 and 10 <= y < 14, (x, y)


def test_camera_offsets_all_primitives_and_resets():
    cv = Canvas(32, 24)
    cv.cls(0)
    cv.camera(10, 5)               # world (10,5) -> screen (0,0)
    cv.rect(10, 5, 4, 4, 8)        # draws at SCREEN (0,0)..(3,3)
    cv.camera()                    # reset; now pix() reads in screen space
    assert cv.pix(0, 0) == 8 and cv.pix(3, 3) == 8   # landed at screen origin
    assert cv.pix(10, 5) == 0      # NOT at the world coords
    cv.rect(10, 5, 2, 2, 11)       # no camera now -> world == screen
    assert cv.pix(10, 5) == 11


def test_camera_returns_previous_offset():
    cv = Canvas(16, 16)
    assert cv.camera(3, 4) == (0, 0)   # returns prior offset (TIC-80)
    assert cv.camera(0, 0) == (3, 4)


def test_spr_flip_horizontal_vertical_both():
    # Asymmetric 2x2 so each flip is distinguishable: index 8 top-left only.
    img = Image.from_ascii(["8.", ".."], {"8": 8})
    # no flip: 8 at top-left
    cv = Canvas(8, 8); cv.cls(0); cv.spr(img, 0, 0, 1, 0)
    assert cv.pix(0, 0) == 8 and cv.pix(1, 0) == 0 and cv.pix(0, 1) == 0
    # h flip: 8 moves to top-right
    cv = Canvas(8, 8); cv.cls(0); cv.spr(img, 0, 0, 1, 1)
    assert cv.pix(1, 0) == 8 and cv.pix(0, 0) == 0
    # v flip: 8 moves to bottom-left
    cv = Canvas(8, 8); cv.cls(0); cv.spr(img, 0, 0, 1, 2)
    assert cv.pix(0, 1) == 8 and cv.pix(0, 0) == 0
    # both: 8 moves to bottom-right
    cv = Canvas(8, 8); cv.cls(0); cv.spr(img, 0, 0, 1, 3)
    assert cv.pix(1, 1) == 8 and cv.pix(0, 0) == 0


def test_spr_flip_default_is_unchanged_for_old_callsites():
    # spr without a flip arg must behave exactly as before (regression guard).
    img = Image.from_ascii(["89", "AB"], {"8": 8, "9": 9, "A": 10, "B": 11})
    cv = Canvas(8, 8); cv.cls(0); cv.spr(img, 0, 0)
    assert cv.pix(0, 0) == 8 and cv.pix(1, 0) == 9
    assert cv.pix(0, 1) == 10 and cv.pix(1, 1) == 11


def test_pal_remaps_draw_index_and_resets():
    cv = Canvas(8, 8)
    cv.cls(0)
    cv.pal(8, 11)                  # draw "8" as "11"
    cv.rect(0, 0, 4, 4, 8)
    assert cv.pix(0, 0) == 11      # remapped
    cv.pal()                       # reset to identity
    cv.rect(4, 4, 2, 2, 8)
    assert cv.pix(4, 4) == 8       # back to literal


def test_pal_applies_to_sprite_pixels():
    img = Image.from_ascii(["8"], {"8": 8})
    cv = Canvas(8, 8); cv.cls(0)
    cv.pal(8, 14)                  # recolour the sprite's "8" to 14
    cv.spr(img, 0, 0)
    assert cv.pix(0, 0) == 14


def test_palt_makes_index_transparent_for_spr():
    img = Image.from_ascii(["89"], {"8": 8, "9": 9})
    cv = Canvas(8, 8); cv.cls(3)   # background 3
    cv.palt(8, True)               # index 8 transparent
    cv.spr(img, 0, 0)
    assert cv.pix(0, 0) == 3       # the "8" pixel let the background through
    assert cv.pix(1, 0) == 9       # the "9" pixel still drew
    cv.palt()                      # reset -> all opaque again
    cv.cls(3); cv.spr(img, 0, 0)
    assert cv.pix(0, 0) == 8


def test_reset_state_clears_camera_clip_pal_palt():
    cv = Canvas(16, 16)
    cv.camera(5, 5); cv.clip(2, 2, 4, 4); cv.pal(8, 11); cv.palt(9, True)
    cv.reset_state()
    cv.cls(0)
    cv.rect(0, 0, 16, 16, 8)       # camera/clip gone -> fills, pal gone -> literal 8
    assert cv.pix(0, 0) == 8 and cv.pix(15, 15) == 8


def test_make_api_exposes_cluster2_verbs_with_identical_keyset():
    # The cart namespace gains clip/camera/pal/palt; spr takes a flip arg. The host
    # and device make_api key-sets stay identical (the device spike test pins it too).
    from runtime import host_app
    api = host_app.make_api(Canvas(32, 24), _StubInput(), {})
    for name in ("clip", "camera", "pal", "palt"):
        assert name in api and callable(api[name])


def test_cart_using_clip_camera_flip_runs_and_resets_between_frames(tmp_path):
    # A cart that sets camera/clip/pal must NOT leak that draw state into the
    # console's own UI overlays: the Workstation resets canvas state after the cart
    # frame. Drive a tiny inline cart through the shared console.
    import os
    from runtime import host_app
    carts = tmp_path / "carts"
    os.makedirs(carts / "clipper.moy")
    (carts / "clipper.moy" / "manifest.json").write_text(
        '{"title": "Clipper", "type": "game", "runtime": "python", "main": "main.py"}')
    (carts / "clipper.moy" / "main.py").write_text(
        "def _draw():\n"
        "    cls(0)\n"
        "    camera(5, 5)\n"
        "    clip(0, 0, 20, 20)\n"
        "    pal(8, 11)\n"
        "    rect(0, 0, W, H, 8)\n")
    (carts / "clipper.moy" / "config.json").write_text("{}")
    ws = host_app.build_workstation(str(carts))
    drv = host_app.ConsoleDriver(ws)
    _open_cart(ws, "Clipper")
    drv.frame(1 / 30)
    assert ws.cart_error is None
    # After the frame the canvas draw state is back to defaults (UI drew clean).
    assert ws.canvas._cam_x == 0 and ws.canvas._cam_y == 0
    assert ws.canvas._clip_x1 == ws.canvas.w and ws.canvas._clip_y1 == ws.canvas.h
    assert list(ws.canvas._pal_map) == list(range(64))


def test_background_declares_color_and_image_backdrops():
    # #63 fast-by-default: background(x) declares the backdrop ONCE; the api's
    # _moy_restore_bg hook repaints it (a color cls, or a baked full-screen layer)
    # so a naive cart never writes a per-frame cls/backdrop blit.
    from runtime import host_app, palette
    cv = Canvas(64, 48)
    api = host_app.make_api(cv, _StubInput(), {})
    red = palette.color("red")
    white = palette.color("white")
    api["background"](red)
    api["_moy_restore_bg"]()
    assert cv.pix(0, 0) == red and cv.pix(63, 47) == red
    cv.rect(0, 0, 8, 8, white)                    # an actor scribbles the frame...
    api["_moy_restore_bg"]()
    assert cv.pix(0, 0) == red, "the color backdrop must reclaim the frame"
    # An Image backdrop bakes to a hidden layer once and restores by window copy.
    img = api["image"](["##", "##"], {"#": white})
    api["background"](img)
    api["_moy_restore_bg"]()
    assert cv.pix(0, 0) == white, "the image pixels land at (0,0)"
    cv.rect(0, 0, 4, 4, red)
    api["_moy_restore_bg"]()
    assert cv.pix(0, 0) == white, "the image backdrop must reclaim the frame"
    # background() clears the declaration -> the restore is a no-op.
    api["background"]()
    cv.rect(0, 0, 4, 4, red)
    api["_moy_restore_bg"]()
    assert cv.pix(0, 0) == red, "a cleared declaration must not repaint"


def test_player_restores_declared_background_each_frame(tmp_path):
    # The Player calls the restore hook BEFORE the cart's frame, so a cart with
    # background() and NO cls still gets a clean backdrop every frame.
    from runtime import host_app, palette
    ws = host_app.build_workstation(str(tmp_path / "carts"))
    # A GAME: it owns all 320x240, so pixel (0, 0) is the cart's. An app runs with
    # the bar over that row. Which cart sorts first is alphabetical, so say "game".
    ws.launcher.sel = next(i for i, it in enumerate(ws.launcher.items)
                           if it.get("path") and it.get("type") == "game")
    ws.open()
    ws.project.cart["src"] = (
        "def _init():\n"
        "    background(col('red'))\n"
        "def _draw():\n"
        "    rect(0, 0, 8, 8, col('white'))\n"
    )
    assert ws._start()
    ws.player.tick(1 / 30)
    red = palette.color("red")
    white = palette.color("white")
    assert ws.canvas.pix(0, 0) == white           # the actor drew over the backdrop
    assert ws.canvas.pix(100, 100) == red         # backdrop painted with NO cls in the cart
    ws.canvas.pix(100, 100, white)                # scribble...
    ws.player.tick(1 / 30)
    assert ws.canvas.pix(100, 100) == red, (
        "the declared background must reclaim the frame before each tick")
