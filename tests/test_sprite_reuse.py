"""Cross-cart sprite reuse (#18): the SpriteSheet.copy_tile import primitive and
the shared sprite sheet stored alongside the carts dir. These exercise the same
shared `runtime/editors.py` + `runtime/moy_carts.py` the device freezes.

The sprite tab in the 2026-10 look (docs/look_2026-10.md step 24) is held at
the foot: a clear pixel on the grey checker, the ring cursor the arrows move
and `a` paints at, the current mark on the tool, and the pickers -- the
sprite and its size in a `ui.popover` -- which a tile picks from and a press
outside or Esc closes; a theme without a depth keeps today's layout."""

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

from runtime.editors import SpriteSheet  # noqa: E402
from runtime import moy_carts  # noqa: E402
import pytest
from ws_helpers import boot_on_fixture

@pytest.fixture(autouse=True)
def _on_the_night_fixture(monkeypatch):
    """These pin the lookless pixel bar and its coordinates: every
    workstation here boots on the `night` fixture (ws_helpers)."""
    boot_on_fixture(monkeypatch)


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


def _tap(ws, rect):
    """Click the centre of a console button rect through the pointer path."""
    x, y, w, h = rect
    ws.pointer.place(x + w // 2, y + h // 2)
    ws.pointer.click = True
    ws.handle_pointer()
    ws.pointer.click = False


from ws_helpers import open_cart as _open_cart


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
    _tap(ws, ws.paint_layer.layout.put_btn)
    assert ws.paint_status == "PUT SPR " + str(src_tile)
    # It really landed in the shared sheet on disk (not just in RAM).
    shared = SpriteSheet.from_hex(moy_carts.load_shared_sheet(ws.carts_root))
    assert _tile_pixels(shared, src_tile) == painted

    # Cart B: a different cart, same tile id starts blank. GET imports from shared.
    _tap(ws, ws.paint_layer.layout.close_btn)
    _open_cart(ws, "Tap Only Red")
    ws._open_paint()
    ws.paint.n = src_tile
    assert [ws.sheet.tget(src_tile, lx, ly)
            for ly in range(ws.sheet.TILE) for lx in range(ws.sheet.TILE)] != painted
    _tap(ws, ws.paint_layer.layout.get_btn)
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
    _tap(ws, ws.paint_layer.layout.get_btn)                 # nothing saved yet
    assert ws.paint_status in ("NO SHARED", "SHARED EMPTY")
    # The current tile was left untouched (no spurious write).
    assert ws.sheet is not None


# -- the sprite tab in the 2026-10 look (docs/look_2026-10.md step 24) ------------------


def _look_tab(tmp_path, theme="moy", variant="light"):
    from runtime import chrome
    ws = host_app.build_workstation(str(tmp_path / "carts"))
    ws.look.set_theme(theme, persist=False, variant=variant)
    _open_cart(ws, "Star Catcher")
    ws._open_paint()
    drv = host_app.ConsoleDriver(ws)
    ws.pointer.visible = False
    _frames(drv, 2)
    return ws, drv, chrome


def _frames(drv, n=1):
    for _ in range(n):
        drv.ws._dirty = True
        drv.frame(1 / 30)


def _px(ws, x, y):
    cv = ws.sys_canvas
    o = 2 * (y * cv.w + x)
    return cv._buf[o] << 8 | cv._buf[o + 1]


def _tap_at(drv, x, y):
    drv.touch(x, y)
    _frames(drv)
    drv.touch_up()
    _frames(drv, 2)


def _cell(lay, pe, lx, ly):
    cell = lay.pg_span // pe.dim
    return lay.pg_x0 + lx * cell, lay.pg_y0 + ly * cell, cell


def test_a_theme_with_a_depth_takes_the_looks_layout_and_one_without_keeps_todays(tmp_path):
    ws, drv, _c = _look_tab(tmp_path)
    lay = ws.paint_layer.layout
    assert lay.look and lay.pg_span == 144, "the grid is the largest 48 that fits"
    assert lay.sw_x0 > lay.pg_x0 + lay.pg_span, "the palette lies beside the grid"
    for r in list(lay.tool_btns) + [lay.close_btn, lay.files_btn]:
        assert r[1] + r[3] <= ws.sys_canvas.h, r
    ws.look.set_theme("night", persist=False, variant="dark")
    _frames(drv)
    assert not ws.paint_layer.layout.look


def test_a_clear_pixel_lies_on_the_grey_checker_and_a_colour_fills_its_cell(tmp_path):
    ws, drv, chrome = _look_tab(tmp_path)
    pe = ws.paint
    ox, oy = pe.sheet.tile_origin(pe.n)
    pe.sheet.pset(ox, oy, 0)
    pe.sheet.pset(ox + 1, oy, 9)
    _frames(drv)
    lay = ws.paint_layer.layout
    x, y, cell = _cell(lay, pe, 0, 0)
    greys = {chrome.rgb565_of(v) for v in ws.look.live.fixed[:2]}
    assert {_px(ws, x + 1, y + 1), _px(ws, x + cell // 2 + 1, y + 1)} == greys
    x, y, cell = _cell(lay, pe, 1, 0)
    assert _px(ws, x + cell // 2, y + cell // 2) == chrome.rgb565_of(9)


def test_the_arrows_move_the_ring_cursor_and_a_paints_at_it(tmp_path):
    ws, drv, chrome = _look_tab(tmp_path)
    pe = ws.paint
    pe.color = 12
    ws.paint_layer._cur[:] = [0, 0]
    before = bytes(ws.sys_canvas._buf)
    for k in ("right", "right", "down"):
        drv.press(k)
        _frames(drv, 2)
    assert ws.paint_layer._cur == [2, 1]
    assert bytes(ws.sys_canvas._buf) != before, "the cursor moved on the glass"
    drv.press("a")
    _frames(drv)
    ox, oy = pe.sheet.tile_origin(pe.n)
    assert pe.sheet.pget(ox + 2, oy + 1) == 12
    x, y, cell = _cell(ws.paint_layer.layout, pe, 4, 4)
    _tap_at(drv, x + cell // 2, y + cell // 2)
    assert ws.paint_layer._cur == [4, 4], "the cursor follows the pointer"


def test_the_tool_in_use_wears_the_current_mark(tmp_path):
    from runtime import paint_layer as P
    ws, drv, _c = _look_tab(tmp_path)
    lay = ws.paint_layer.layout
    x, y, w, h = lay.tool_btns[P._TOOLS.index("pen")]
    ws.paint.tool = ws.paint.PEN
    _frames(drv)
    on = _px(ws, x + 3, y + 3)
    ws.paint.tool = ws.paint.FILL
    _frames(drv)
    assert _px(ws, x + 3, y + 3) != on


@pytest.mark.parametrize("theme,variant", [("moy", "dark"), ("blocks", "light"),
                                           ("contrast", "light")])
def test_clear_wears_the_danger_buttons_ink_and_idle_copy_the_disabled(tmp_path, theme,
                                                                      variant):
    from runtime import paint_layer as P, ui_draw
    ws, drv, chrome = _look_tab(tmp_path, theme, variant)
    lay = ws.paint_layer.layout
    th = ws.theme_colors
    ws.paint.sel = None
    _frames(drv)

    def inks(tid):
        x, y, w, h = lay.tool_btns[P._TOOLS.index(tid)]
        return {_px(ws, i, j) for i in range(x, x + w) for j in range(y, y + h)}

    assert chrome.rgb565_of(ui_draw.button_ink(th, "danger")) in inks("clear")
    assert chrome.rgb565_of(ui_draw.disabled_ink(th)) in inks("copy")


@pytest.mark.parametrize("theme,variant", [("moy", "light"), ("moy", "dark"),
                                           ("blocks", "light"), ("contrast", "dark")])
def test_the_caption_size_line_and_tool_rows_keep_their_room(tmp_path, theme, variant):
    """The grid starts under the caption's ink and the preview under the
    Size line's, the Size line is centred on its column, and the two tool
    rows are one toolbar: one pitch, each row centred on its span."""
    from runtime import paint_layer as P
    from runtime import ui_draw
    ws, _drv, _c = _look_tab(tmp_path, theme, variant)
    lay = ws.paint_layer.layout
    fs = lay.fs
    asc = ui_draw.font("ui")[3] * fs
    assert lay.pg_y0 >= lay.title_xy[1] + asc + 3 * fs
    assert lay.prev_rect[1] >= lay.size_xy[1] + asc + 4 * fs
    assert lay.size_xy[0] == lay.close_btn[0] + lay.close_btn[2] // 2
    assert lay.pg_span == 144
    n1 = len(P._TOOL_ROW1)
    row1, row2 = lay.tool_btns[:n1], lay.tool_btns[n1:]
    pitches = {b[0] - a[0] for row in (row1, row2) for a, b in zip(row, row[1:])}
    assert len(pitches) == 1
    pitch = pitches.pop()
    # On the handheld the actions span the content less its pad each side.
    end = lay.cx + lay.cw - (lay.pg_x0 - lay.cx)
    left = row2[0][0] - lay.pg_x0
    right = end - (row2[-1][0] + pitch)
    assert abs(left - right) <= 1


def test_the_colour_in_use_and_the_cursor_are_two_toned(tmp_path):
    """The colour in use is ringed in `focus` with `surface` inside the ring,
    and the cursor's ring is edged in `surface`, so neither vanishes on a
    swatch or a cell of the focus colour's own."""
    from runtime import ui_draw
    ws, drv, chrome = _look_tab(tmp_path)
    lay = ws.paint_layer.layout
    pe = ws.paint
    th = ws.theme_colors
    pe.color = 11
    ws.paint_layer._cur[:] = [3, 3]
    _frames(drv, 2)
    x = lay.sw_x0 + (pe.color % lay.sw_cols) * lay.sw
    y = lay.sw_y0 + (pe.color // lay.sw_cols) * lay.sw
    t = 2 * lay.fs
    focus, surface = chrome.rgb565_of(th["focus"]), chrome.rgb565_of(th["surface"])
    assert _px(ws, x, y + lay.sw // 2) == focus
    assert _px(ws, x + lay.sw - 1, y + lay.sw // 2) == focus
    assert _px(ws, x + t, y + lay.sw // 2) == surface
    cx, cy, cell = _cell(lay, pe, 3, 3)
    reach = ui_draw.mark_reach("part")
    for o in (1, reach + 1):
        assert _px(ws, cx - 1 - o, cy + cell // 2) == surface, o


def test_the_size_opens_the_pickers_and_a_tile_of_the_sheet_becomes_the_sprite(tmp_path):
    ws, drv, _c = _look_tab(tmp_path)
    pl = ws.paint_layer
    lay = pl.layout
    pe = ws.paint
    x, y, w, h = lay.size_btn
    _tap_at(drv, x + w // 2, y + h // 2)
    assert pl._pop, "a tap on the size opens the pickers"
    _frames(drv, 2)
    box = pl._host.box("Sheet")
    assert box is not None
    t = pe.sheet.TILE * lay.fs
    m = 2 * lay.fs
    _tap_at(drv, box[0] + m + 3 * t + 2, box[1] + m + 1 * t + 2)
    assert pe.n == 1 * pe.sheet.cols + 3
    assert not pl._pop, "a pick closes them"


def test_the_pickers_size_seg_sets_the_sprites_size(tmp_path):
    ws, drv, _c = _look_tab(tmp_path)
    pl = ws.paint_layer
    x, y, w, h = pl.layout.prev_rect
    _tap_at(drv, x + w // 2, y + h // 2)
    _frames(drv, 2)
    drv.press("right")
    _frames(drv, 2)
    assert ws.paint.size == 2 and pl._pop


def test_a_press_outside_the_pickers_or_esc_closes_them(tmp_path):
    ws, drv, _c = _look_tab(tmp_path)
    pl = ws.paint_layer
    pe = ws.paint
    n = pe.n
    x, y, w, h = pl.layout.size_btn
    _tap_at(drv, x + w // 2, y + h // 2)
    _frames(drv, 2)
    gx, gy = pl.layout.pg_x0 + 2, pl.layout.pg_y0 + 2
    ox, oy = pe.sheet.tile_origin(pe.n)
    pixel = pe.sheet.pget(ox, oy)
    _tap_at(drv, gx, gy)
    assert not pl._pop and pe.n == n
    assert pe.sheet.pget(ox, oy) == pixel, "the press that closed them drew nothing"
    _tap_at(drv, x + w // 2, y + h // 2)
    _frames(drv, 2)
    drv.type_char(27)
    _frames(drv, 2)
    assert not pl._pop
