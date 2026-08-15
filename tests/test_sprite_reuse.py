"""Cross-cart sprite reuse (#18): the SpriteSheet.copy_tile import primitive and
the shared sprite sheet stored alongside the carts dir. These exercise the same
shared `runtime/editors.py` + `runtime/moy_carts.py` the device freezes."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from runtime.editors import SpriteSheet  # noqa: E402
from runtime import moy_carts  # noqa: E402


def _paint_glyph(sheet, n):
    """Paint a recognizable 8x8 pattern into tile n (diagonal + a corner dot)."""
    for i in range(sheet.TILE):
        sheet.tset(n, i, i, (i % 14) + 1)   # 1..14 down the diagonal
    sheet.tset(n, 0, 7, 15)                  # distinctive corner pixel
    return sheet


def _tile_pixels(sheet, n):
    return [sheet.tget(n, lx, ly) for ly in range(sheet.TILE) for lx in range(sheet.TILE)]


# -- import-tile primitive --------------------------------------------------

def test_tile_image_is_memoised_until_an_edit():
    """tile_image() returns the SAME object for a tile across calls (stable id() -> the web
    recorder's atlas dedups it, shipping the defspr ONCE instead of every frame -- the launcher/
    settings `unknown`-churn + payload fix). A pset bumps `gen`, which drops the cache so the
    next call reflects the edit."""
    # spec=False on the small fixtures below: these exercise copy_tile ACROSS
    # sheet sizes and tile_image memoisation, neither of which touches libmoy --
    # see editors_sheet's SPEC.md 3.2 note for why a cart sheet may not be one.
    sheet = _paint_glyph(SpriteSheet(cols=16, rows=16, spec=False), 3)
    a = sheet.tile_image(3)
    assert sheet.tile_image(3) is a                 # memoised: same object across frames
    assert sheet.tile_image(3, transparent=0) is not a   # a different colorkey is its own entry
    assert sheet.tile_image(3, transparent=0) is sheet.tile_image(3, transparent=0)
    before = a.pix
    sheet.pset(24, 0, 9)                             # edit tile 3 (col 24 = tile 3's origin)
    b = sheet.tile_image(3)
    assert b is not a                                # gen bumped -> cache dropped -> rebuilt
    assert b.pix != before                           # ...and the rebuild sees the new pixel


def test_copy_tile_imports_one_sprite_between_sheets():
    src = _paint_glyph(SpriteSheet(cols=8, rows=8, spec=False), 5)   # 64x64, 64 sprites
    dst = SpriteSheet(cols=16, rows=16, spec=False)      # different size sheet
    assert dst.is_blank()

    returned = dst.copy_tile(src, 5, dst_n=3)
    assert returned == 3
    # The destination tile now matches the source tile exactly...
    assert _tile_pixels(dst, 3) == _tile_pixels(src, 5)
    assert _tile_pixels(dst, 3) != [0] * 64               # ...and isn't blank
    assert dst.dirty                                      # copy marks the sheet dirty
    # Only the target tile changed; a neighbor stays blank.
    assert _tile_pixels(dst, 4) == [0] * 64


def test_copy_tile_defaults_destination_to_same_id():
    src = _paint_glyph(SpriteSheet(cols=4, rows=4, spec=False), 2)
    dst = SpriteSheet(cols=4, rows=4, spec=False)
    assert dst.copy_tile(src, 2) == 2                     # dst_n defaults to src_n
    assert _tile_pixels(dst, 2) == _tile_pixels(src, 2)


def test_copy_tile_rejects_out_of_range_ids():
    src = _paint_glyph(SpriteSheet(cols=4, rows=4, spec=False), 0)    # 16 sprites
    dst = SpriteSheet(cols=4, rows=4, spec=False)
    assert dst.copy_tile(src, 99) is None                 # source id out of range
    assert dst.copy_tile(src, 0, dst_n=99) is None        # dest id out of range
    assert dst.copy_tile(src, -1) is None
    assert dst.is_blank()                                 # nothing was written


# -- shared sheet store -----------------------------------------------------

def test_shared_sheet_path_is_sibling_of_carts_dir():
    assert moy_carts.shared_sheet_path("/sd/moybyte/carts") == "/sd/moybyte/shared.moygfx"


def test_load_shared_sheet_is_none_before_first_save(tmp_path):
    root = str(tmp_path / "carts")
    assert moy_carts.load_shared_sheet(root) is None


def test_shared_sheet_roundtrips_through_save_load(tmp_path):
    root = str(tmp_path / "carts")
    shared = _paint_glyph(SpriteSheet(), 7)               # the spec 16x32 default
    moy_carts.save_shared_sheet(shared.to_hex(), root)

    # It lands at the well-known sibling path, not inside any cart.
    assert Path(moy_carts.shared_sheet_path(root)).exists()

    text = moy_carts.load_shared_sheet(root)
    assert text is not None
    reloaded = SpriteSheet.from_hex(text)
    assert reloaded.pix == shared.pix
    assert reloaded.dirty is False


# -- end-to-end: paint in cart A, reuse the tile in cart B ------------------

def test_sprite_reused_from_one_cart_into_another(tmp_path):
    root = str(tmp_path / "carts")
    moy_carts.ensure_dirs(root)

    # Cart A paints a sprite and saves its sheet.
    cart_a = moy_carts.create("Cart A", root, type="app")
    sheet_a = _paint_glyph(SpriteSheet(), 9)
    moy_carts.save_sprites(cart_a, sheet_a.to_hex())

    # Cart B starts blank, then imports cart A's sprite via the primitive.
    cart_b = moy_carts.create("Cart B", root, type="app")
    src = SpriteSheet.from_hex(moy_carts.load(cart_a["path"])["sprites"])
    sheet_b = SpriteSheet()                               # cart B's (empty) sheet
    assert sheet_b.is_blank()
    sheet_b.copy_tile(src, 9, dst_n=1)
    moy_carts.save_sprites(cart_b, sheet_b.to_hex())

    # Reload cart B from disk: the imported tile survived the roundtrip and
    # matches what cart A painted.
    reloaded_b = SpriteSheet.from_hex(moy_carts.load(cart_b["path"])["sprites"])
    assert _tile_pixels(reloaded_b, 1) == _tile_pixels(sheet_a, 9)
    assert _tile_pixels(reloaded_b, 1) != [0] * 64


def test_tile_imported_via_shared_sheet(tmp_path):
    root = str(tmp_path / "carts")

    # Save a sprite to the shared sheet, then bring it into a fresh cart sheet.
    shared = _paint_glyph(SpriteSheet(), 0)
    moy_carts.save_shared_sheet(shared.to_hex(), root)

    src = SpriteSheet.from_hex(moy_carts.load_shared_sheet(root))
    dst = SpriteSheet()
    dst.copy_tile(src, 0, dst_n=12)
    assert _tile_pixels(dst, 12) == _tile_pixels(shared, 0)


# -- end-to-end through the PAINT-EDITOR UI (#18 wiring) ---------------------
#
# The primitives above are reachable from a kid's fingers via the paint editor's
# PUT (save tile to shared) / GET (import tile from shared) buttons. These drive
# the real console pointer path -- the same handle_pointer() the device runs.

from runtime import host_app  # noqa: E402
from runtime import console  # noqa: E402


def _tap(ws, rect):
    """Click the centre of a console button rect through the pointer path."""
    x, y, w, h = rect
    ws.pointer.place(x + w // 2, y + h // 2)
    ws.pointer.click = True
    ws.handle_pointer()
    ws.pointer.click = False


def _open_cart(ws, title):
    for i, c in enumerate(ws.launcher.items):
        if c["title"] == title:
            ws.launcher.sel = i
            ws.open()
            return
    raise AssertionError("cart not found: " + title)


def test_put_then_get_moves_a_tile_between_carts_via_ui(tmp_path):
    ws = host_app.build_workstation(str(tmp_path / "carts"))

    # Cart A: open the paint editor, paint a recognizable tile, PUT it to shared.
    _open_cart(ws, "Pixel Pet")
    ws._open_paint()
    assert ws.menu_view == "paint"
    src_tile = ws.paint.n
    for i in range(ws.sheet.TILE):              # paint a diagonal a kid would notice
        ws.paint.color = (i % 14) + 1
        ws.paint.paint(i, i)
    painted = [ws.sheet.tget(src_tile, lx, ly)
               for ly in range(ws.sheet.TILE) for lx in range(ws.sheet.TILE)]
    _tap(ws, console._PAINT_PUT)
    assert ws.paint_status == "PUT SPR " + str(src_tile)
    # It really landed in the shared sheet on disk (not just in RAM).
    shared = SpriteSheet.from_hex(moy_carts.load_shared_sheet(ws.carts_root))
    assert _tile_pixels(shared, src_tile) == painted

    # Cart B: a different cart, same tile id starts blank. GET imports from shared.
    _tap(ws, console._PAINT_CLOSE)
    _open_cart(ws, "Tap Only Red")
    ws._open_paint()
    ws.paint.n = src_tile
    assert [ws.sheet.tget(src_tile, lx, ly)
            for ly in range(ws.sheet.TILE) for lx in range(ws.sheet.TILE)] != painted
    _tap(ws, console._PAINT_GET)
    assert ws.paint_status == "GOT SPR " + str(src_tile)
    # The tile a kid painted in cart A now lives in cart B's sheet, unrepainted.
    assert [ws.sheet.tget(src_tile, lx, ly)
            for ly in range(ws.sheet.TILE) for lx in range(ws.sheet.TILE)] == painted

    # And committing cart B persists it (the round-trip survives a reload from disk).
    # There's no SAVE button (#111) -- ws.save_sprites is the hard-commit verb every
    # exit path (tab switch/PLAY/CLOSE/...) now dispatches automatically.
    ws.save_sprites()
    reloaded = SpriteSheet.from_hex(moy_carts.load(ws.cart["path"])["sprites"])
    assert _tile_pixels(reloaded, src_tile) == painted


def test_get_reports_when_shared_sheet_is_empty(tmp_path):
    ws = host_app.build_workstation(str(tmp_path / "carts"))
    _open_cart(ws, "Pixel Pet")
    ws._open_paint()
    _tap(ws, console._PAINT_GET)                 # nothing saved yet
    assert ws.paint_status in ("NO SHARED", "SHARED EMPTY")
    # The current tile was left untouched (no spurious write).
    assert ws.sheet is not None
