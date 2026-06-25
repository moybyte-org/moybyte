"""Cross-cart sprite reuse (#18): the SpriteSheet.copy_tile import primitive and
the shared sprite sheet stored alongside the carts dir. These exercise the same
shared `runtime/editors.py` + `runtime/kid_carts.py` the device freezes."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from runtime.canvas import SpriteSheet  # noqa: E402
from runtime import kid_carts  # noqa: E402


def _paint_glyph(sheet, n):
    """Paint a recognizable 8x8 pattern into tile n (diagonal + a corner dot)."""
    for i in range(sheet.TILE):
        sheet.tset(n, i, i, (i % 14) + 1)   # 1..14 down the diagonal
    sheet.tset(n, 0, 7, 15)                  # distinctive corner pixel
    return sheet


def _tile_pixels(sheet, n):
    return [sheet.tget(n, lx, ly) for ly in range(sheet.TILE) for lx in range(sheet.TILE)]


# -- import-tile primitive --------------------------------------------------

def test_copy_tile_imports_one_sprite_between_sheets():
    src = _paint_glyph(SpriteSheet(cols=8, rows=8), 5)   # 64x64, 64 sprites
    dst = SpriteSheet(cols=16, rows=16)                  # different size sheet
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
    src = _paint_glyph(SpriteSheet(cols=4, rows=4), 2)
    dst = SpriteSheet(cols=4, rows=4)
    assert dst.copy_tile(src, 2) == 2                     # dst_n defaults to src_n
    assert _tile_pixels(dst, 2) == _tile_pixels(src, 2)


def test_copy_tile_rejects_out_of_range_ids():
    src = _paint_glyph(SpriteSheet(cols=4, rows=4), 0)    # 16 sprites
    dst = SpriteSheet(cols=4, rows=4)
    assert dst.copy_tile(src, 99) is None                 # source id out of range
    assert dst.copy_tile(src, 0, dst_n=99) is None        # dest id out of range
    assert dst.copy_tile(src, -1) is None
    assert dst.is_blank()                                 # nothing was written


# -- shared sheet store -----------------------------------------------------

def test_shared_sheet_path_is_sibling_of_carts_dir():
    assert kid_carts.shared_sheet_path("/sd/kidcode/carts") == "/sd/kidcode/shared.kgfx"


def test_load_shared_sheet_is_none_before_first_save(tmp_path):
    root = str(tmp_path / "carts")
    assert kid_carts.load_shared_sheet(root) is None


def test_shared_sheet_roundtrips_through_save_load(tmp_path):
    root = str(tmp_path / "carts")
    shared = _paint_glyph(SpriteSheet(), 7)               # default 16x16 sheet
    kid_carts.save_shared_sheet(shared.to_hex(), root)

    # It lands at the well-known sibling path, not inside any cart.
    assert Path(kid_carts.shared_sheet_path(root)).exists()

    text = kid_carts.load_shared_sheet(root)
    assert text is not None
    reloaded = SpriteSheet.from_hex(text)
    assert reloaded.pix == shared.pix
    assert reloaded.dirty is False


# -- end-to-end: paint in cart A, reuse the tile in cart B ------------------

def test_sprite_reused_from_one_cart_into_another(tmp_path):
    root = str(tmp_path / "carts")
    kid_carts.ensure_dirs(root)

    # Cart A paints a sprite and saves its sheet.
    cart_a = kid_carts.create("Cart A", root, type="app")
    sheet_a = _paint_glyph(SpriteSheet(), 9)
    kid_carts.save_sprites(cart_a, sheet_a.to_hex())

    # Cart B starts blank, then imports cart A's sprite via the primitive.
    cart_b = kid_carts.create("Cart B", root, type="app")
    src = SpriteSheet.from_hex(kid_carts.load(cart_a["path"])["sprites"])
    sheet_b = SpriteSheet()                               # cart B's (empty) sheet
    assert sheet_b.is_blank()
    sheet_b.copy_tile(src, 9, dst_n=1)
    kid_carts.save_sprites(cart_b, sheet_b.to_hex())

    # Reload cart B from disk: the imported tile survived the roundtrip and
    # matches what cart A painted.
    reloaded_b = SpriteSheet.from_hex(kid_carts.load(cart_b["path"])["sprites"])
    assert _tile_pixels(reloaded_b, 1) == _tile_pixels(sheet_a, 9)
    assert _tile_pixels(reloaded_b, 1) != [0] * 64


def test_tile_imported_via_shared_sheet(tmp_path):
    root = str(tmp_path / "carts")

    # Save a sprite to the shared sheet, then bring it into a fresh cart sheet.
    shared = _paint_glyph(SpriteSheet(), 0)
    kid_carts.save_shared_sheet(shared.to_hex(), root)

    src = SpriteSheet.from_hex(kid_carts.load_shared_sheet(root))
    dst = SpriteSheet()
    dst.copy_tile(src, 0, dst_n=12)
    assert _tile_pixels(dst, 12) == _tile_pixels(shared, 0)
