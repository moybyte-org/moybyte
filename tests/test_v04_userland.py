"""Tests for the v0.4 userland: the indexed canvas, the shared SpriteSheet /
CodeEditor cores, the `.moy` store (moy_carts), and the shared console run on the
host via host_app -- the same console code the device runs."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from runtime import palette  # noqa: E402
from runtime.canvas import Canvas, Image, SpriteSheet  # noqa: E402
from runtime.editors import TileMap  # noqa: E402

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
    assert len(palette.MOY64) == 64
    for rgb in palette.MOY64:
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
    sheet = SpriteSheet()
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
    sheet = SpriteSheet()
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
    assert set(cv.buf) == {8}

    cv.cls(0)
    api["draw_layer"](bg, 20, 0)            # window at world x=20 -> all right half (11)
    assert set(cv.buf) == {11}

    cv.cls(0)
    api["draw_layer"](bg, 999, 0)          # past the edge -> clamped to x=20 -> 11
    assert set(cv.buf) == {11}

    cv.cls(0)
    api["draw_layer"](bg, -5, 0)           # negative -> clamped to x=0 -> 8
    assert set(cv.buf) == {8}


def test_spr_batch_matches_individual_spr_calls():
    # spr_batch (#43) must draw the SAME pixels as the equivalent sequence of spr()
    # calls (the device collapses it to one C call; the host is the per-item reference,
    # and the two paths must agree pixel-for-pixel). Build two canvases, draw a few
    # tiles (one flipped) via each path, and assert the buffers are identical.
    sheet = SpriteSheet()
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

    assert cv_batch.buf == cv_indiv.buf
    assert len(set(cv_batch.buf)) > 1        # sanity: it actually drew something


def test_spr_batch_no_op_when_sheet_is_none():
    # make_api.spr_batch is a no-op (no crash) for a cart with no sheet.
    from runtime import host_app
    cv = Canvas(20, 20)
    cv.cls(0)

    class _Input:
        def held(self, n):
            return False

        def pressed(self, n):
            return False

    api = host_app.make_api(cv, _Input(), {}, sheet=None)
    api["spr_batch"]([(0, 0, 0)], 0, 2)      # must not raise
    assert set(cv.buf) == {0}                 # nothing drawn


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
    assert len(set(ws.canvas.buf)) > 1      # the map() blit drew the ground


def test_battle_city_runs_with_tilemap_and_autoplay_progresses(tmp_path):
    # Battle City (#35): a top-down tank battle drawn over the cart's brick/steel
    # tilemap (map.moymap) with map()/mget()/mset(). Verify it loads its tilemap and
    # spawns a wave, then that the attract auto-pilot runs many frames without error
    # and actually PROGRESSES -- destroys enemies (score climbs) and the round ends
    # (a wave is cleared or the base/lives are lost, then it resets).
    from runtime import host_app
    ws = host_app.build_workstation(str(tmp_path / "carts"))
    _open_cart(ws, "Battle City")
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
    assert len(set(ws.canvas.buf)) > 3      # the map()/sprites drew the battlefield


def test_battle_city_brick_crumbles_steel_stops(tmp_path):
    # Bullet vs walls: a player bullet into a BRICK cell clears it (mset -> empty),
    # while a STEEL cell is never destroyed. Drive a couple of shots straight into
    # each wall kind and check the tilemap before/after.
    from runtime import host_app
    ws = host_app.build_workstation(str(tmp_path / "carts"))
    _open_cart(ws, "Battle City")
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
    def __init__(self, x=0, y=0, click=False):
        self.x = x
        self.y = y
        self.click = click


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
    # touch() still returns its own 3-tuple shape unchanged.
    assert api["touch"]() == (40, 70, True)


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
    # A no-change write must NOT re-persist (no per-frame SD hammering).
    n_before = len(writes)
    pm.cell(0, 1234)
    assert len(writes) == n_before
    assert writes[-1][0] == 1234 and writes[-1][255] == 7

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


def test_host_console_map_open_place_and_render(tmp_path):
    from runtime import console as C
    from runtime import host_app
    ws = host_app.build_workstation(str(tmp_path / "carts"))
    drv = host_app.ConsoleDriver(ws)
    drv.press("run")
    drv.frame(1 / 30)
    drv.click(C._MAP_BTN[0] + 2, C._MAP_BTN[1] + 2)      # open the MAP overlay button
    drv.frame(1 / 30)
    assert ws.menu_view == "map" and ws.mapedit is not None
    # pick the 2nd palette tile (id == map_page + 1) ...
    px = C._TP_X0 + 1 * C._TP_CELL
    py = C._TP_Y0
    drv.click(px + 2, py + 2)
    drv.frame(1 / 30)
    assert ws.mapedit.n == ws.map_page + 1
    # ... then stamp it onto the top-left visible map cell and confirm mget reflects it
    drv.click(C._MV_X0 + 2, C._MV_Y0 + 2)
    drv.frame(1 / 30)
    cx = ws.mapedit.cam_x
    cy = ws.mapedit.cam_y
    assert ws.tilemap.mget(cx, cy) == ws.mapedit.n
    assert len(set(drv.rgb888())) > 1                    # the map view rendered


def test_host_console_map_erase_and_pan(tmp_path):
    from runtime import console as C
    from runtime import host_app
    ws = host_app.build_workstation(str(tmp_path / "carts"))
    drv = host_app.ConsoleDriver(ws)
    drv.press("run"); drv.frame(1 / 30)
    drv.click(C._MAP_BTN[0] + 2, C._MAP_BTN[1] + 2); drv.frame(1 / 30)
    ws.mapedit.n = 3
    drv.click(C._MV_X0 + 2, C._MV_Y0 + 2); drv.frame(1 / 30)   # stamp tile 3 at (0,0)
    assert ws.tilemap.mget(0, 0) == 3
    drv.click(C._MAP_ERASE[0] + 2, C._MAP_ERASE[1] + 2); drv.frame(1 / 30)  # ERASE on
    assert ws.map_erase
    drv.click(C._MV_X0 + 2, C._MV_Y0 + 2); drv.frame(1 / 30)   # now a tap erases
    assert ws.tilemap.mget(0, 0) == TileMap.EMPTY
    # Zoom IN so the map is bigger than the view, then pan right (the fit-both default
    # shows the whole map -> the camera is pinned at 0 with nothing to scroll).
    ws.map_zoom = len(C._MV_ZOOMS) - 1
    x0m, y0m, cell, cols, rows = ws._mv_metrics()
    assert ws.tilemap.w > cols                                 # room to pan at this zoom
    drv.click(C._PAN_RT[0] + 2, C._PAN_RT[1] + 2); drv.frame(1 / 30)        # pan right
    assert ws.mapedit.cam_x == 1


def test_host_console_map_save_roundtrips(tmp_path):
    from runtime import console as C
    from runtime import host_app, moy_carts
    carts_dir = str(tmp_path / "carts")
    ws = host_app.build_workstation(carts_dir)
    drv = host_app.ConsoleDriver(ws)
    drv.press("run"); drv.frame(1 / 30)
    cart_path = ws.cart["path"]
    drv.click(C._MAP_BTN[0] + 2, C._MAP_BTN[1] + 2); drv.frame(1 / 30)
    ws.mapedit.n = 6
    drv.click(C._MV_X0 + 2, C._MV_Y0 + 2); drv.frame(1 / 30)   # stamp tile 6 at (0,0)
    drv.click(C._MAP_SAVE[0] + 2, C._MAP_SAVE[1] + 2); drv.frame(1 / 30)    # SAVE
    assert ws.save_status == "SAVED"
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
    assert ws.menu_view == "map" and ws.mapedit is not None
    return C, ws, host_app.ConsoleDriver(ws)


def test_map_default_zoom_fits_whole_shipped_maps(tmp_path):
    # The DEFAULT (most zoomed-OUT) zoom MUST show the ENTIRE map of both shipped
    # games with the camera at (0,0) and zero panning: battle_city is 15x15 and
    # platformer is 20x13. So the default view must hold >= the map's cols AND rows.
    from runtime import console as C
    for name, w, h in (("battle_city", 15, 15), ("platformer", 20, 13)):
        _C, ws, _drv = _open_cart_map(tmp_path / name, name)
        assert (ws.tilemap.w, ws.tilemap.h) == (w, h)
        assert ws.map_zoom == 0                          # opens at the fit-both default
        assert (ws.mapedit.cam_x, ws.mapedit.cam_y) == (0, 0)   # cam pinned to origin
        x0, y0, cell, cols, rows = ws._mv_metrics()
        assert cols >= w and rows >= h                   # the whole map is on screen
        # Every map cell maps to a pixel inside the visible map-view rectangle, so no
        # cell is off-screen at the default zoom.
        area = ws._mv_area()
        for cy in (0, h - 1):
            for cx in (0, w - 1):
                px = x0 + cx * cell + cell // 2
                py = y0 + cy * cell + cell // 2
                assert C._in(px, py, area)
                assert ws._map_cell_at(px, py) == (cx, cy)


def test_map_cycle_zoom_increases_cell_and_shrinks_view(tmp_path):
    # Cycling the zoom steps IN: the cell size strictly grows and the visible cell
    # count strictly shrinks, level by level, until it wraps back to the default.
    from runtime import console as C
    _C, ws, drv = _open_cart_map(tmp_path, "battle_city")
    seen = []
    for _ in range(len(C._MV_ZOOMS)):
        x0, y0, cell, cols, rows = ws._mv_metrics()
        seen.append((ws.map_zoom, cell, cols * rows))
        drv.click(C._MAP_ZOOM[0] + 2, C._MAP_ZOOM[1] + 2)   # tap ZOOM -> next level
        drv.frame(1 / 30)
    # Back to the default after a full cycle.
    assert ws.map_zoom == 0
    # Ascending cell size, descending visible-cell count across the levels.
    for k in range(1, len(seen)):
        assert seen[k][1] > seen[k - 1][1]               # bigger cells
        assert seen[k][2] < seen[k - 1][2]               # fewer visible cells


def test_map_tap_and_sky_hit_right_cell_after_zoom(tmp_path):
    # After a zoom change tap-paint and the SKY (empty) brush still land on the cell
    # under the pointer -- hit-testing follows the live cell size.
    from runtime import console as C
    _C, ws, drv = _open_cart_map(tmp_path, "battle_city")
    ws.map_zoom = 2                                       # a zoomed-IN level
    ws.mapedit.cam_x = ws.mapedit.cam_y = 0
    x0, y0, cell, cols, rows = ws._mv_metrics()
    # Tap cell (2, 1) with a real brush tile -> exactly that cell gets the tile.
    ws.mapedit.n = 5
    px = x0 + 2 * cell + cell // 2
    py = y0 + 1 * cell + cell // 2
    drv.touch(px, py); drv.frame(1 / 30); drv.touch_up(); drv.frame(1 / 30)
    assert ws.tilemap.mget(2, 1) == 5
    # Pick SKY then tap the same cell -> it clears to EMPTY (the right cell, at zoom).
    drv.click(C._TP_SKY[0] + 2, C._TP_SKY[1] + 2); drv.frame(1 / 30)
    assert ws.mapedit.n < 0
    drv.touch(px, py); drv.frame(1 / 30); drv.touch_up(); drv.frame(1 / 30)
    assert ws.tilemap.mget(2, 1) == ws.tilemap.EMPTY


def test_map_pan_works_zoomed_in_and_clamps(tmp_path):
    # Zoomed IN (map bigger than the view), the d-pad pans the camera; panning is
    # clamped so the camera never scrolls the map off the window.
    from runtime import console as C
    _C, ws, drv = _open_cart_map(tmp_path, "battle_city")
    ws.map_zoom = len(C._MV_ZOOMS) - 1                   # most zoomed-in
    x0, y0, cell, cols, rows = ws._mv_metrics()
    assert ws.tilemap.w > cols and ws.tilemap.h > rows   # room to pan
    drv.click(C._PAN_RT[0] + 2, C._PAN_RT[1] + 2); drv.frame(1 / 30)
    assert ws.mapedit.cam_x == 1
    drv.click(C._PAN_DN[0] + 2, C._PAN_DN[1] + 2); drv.frame(1 / 30)
    assert ws.mapedit.cam_y == 1
    # Pan hard right past the edge: clamps so the last column stays visible.
    for _ in range(40):
        drv.click(C._PAN_RT[0] + 2, C._PAN_RT[1] + 2); drv.frame(1 / 30)
    assert ws.mapedit.cam_x == ws.tilemap.w - cols       # clamped to the right edge
    assert ws.mapedit.cam_x + cols == ws.tilemap.w


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
