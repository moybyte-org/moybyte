"""Tests for the responsive code + block editors (#39, step 2): both editors now
render on the SYSTEM canvas at the panel's native size (not the fixed 320x240 game
viewport), deriving their layout from (canvas.w, canvas.h, font_scale) -- exactly
the _base-verbatim pattern step 1 used for the desktop.

Driven through the SAME shared console the device runs (runtime.host_app +
ConsoleDriver / Workstation), so these assert host==device behavior.

The point of this step is host-verifiable:
  * GRACEFUL DEGRADATION -- at a 320x240 system canvas + font scale 1, every drawn
    pixel of each editor is byte-identical to today (the T-Deck path is unchanged).
  * RESPONSIVE -- a larger system canvas shows MORE lines + WIDER columns in the
    code editor and MORE rows + WIDER blocks in the block editor; a bigger font
    scales everything; nav/edit still works at any size.
  * Sprite/paint + map editors stay a 320x240 viewport (step 3) -- not touched here.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _open_first_cart(ws):
    ws.launcher.sel = 0
    ws.open()
    return ws


def _quiesce(ws):
    """Hide the cursor + clear the achievement toast so a rendered frame is the pure
    editor (the same quiescing test_two_domain_seam.py does)."""
    if ws.pointer is not None:
        ws.pointer.visible = False
    ws.ach.toast = None
    ws.ach.toast_until = 0


def _ws_shared(tmp_path):
    """The default (T-Deck) build: one shared 320x240 canvas == today."""
    from runtime import host_app
    return _open_first_cart(host_app.build_workstation(str(tmp_path / "carts")))


def _ws_distinct_320(tmp_path):
    """A console with a DISTINCT 320x240 system canvas, forcing the editors onto the
    system-canvas path (vs. the shared-object degradation). Both must render pixel-
    identically -- that's the byte-for-byte degradation guarantee."""
    from runtime import host_app
    from runtime.canvas import SystemCanvas
    ws = host_app.build_workstation(str(tmp_path / "carts"))
    ws._sys_canvas = SystemCanvas(320, 240, font_scale=1)
    ws._relayout()
    ws.pointer = host_app.console.Pointer(320, 240)
    ws.input.pointer = ws.pointer
    return _open_first_cart(ws)


def _enter(ws, view):
    """Enter an editor sub-view exactly as the shell does (screen -> menu, then the
    sub-view), so frame() dispatches to the editor draw (not the desktop)."""
    ws.screen = "menu"
    ws.set_menu_view(view)


def _render_editor(ws, view):
    _enter(ws, view)
    _quiesce(ws)
    ws.frame(1 / 30)
    return bytes(ws.sys_canvas.buf)


# ---------------------------------------------------------------------------
# Graceful degradation: 320x240 / font 1x is byte-identical to today.
# ---------------------------------------------------------------------------

def test_code_editor_320x240_is_byte_identical(tmp_path):
    """The code editor on a DISTINCT 320x240 system canvas (font 1x) renders exactly
    the same pixels as the shared-canvas (today) path -- the baseline can't drift."""
    a = _render_editor(_ws_shared(tmp_path / "a"), "code")
    b = _render_editor(_ws_distinct_320(tmp_path / "b"), "code")
    assert a == b


def test_block_editor_320x240_is_byte_identical(tmp_path):
    """Same byte-identical guarantee for the block editor's structured outline."""
    a = _render_editor(_ws_shared(tmp_path / "a"), "blocks")
    b = _render_editor(_ws_distinct_320(tmp_path / "b"), "blocks")
    assert a == b


def test_block_menu_320x240_is_byte_identical(tmp_path):
    """The modal insert menu (the +/ADD picker) is also byte-identical at 320x240."""
    a_ws = _ws_shared(tmp_path / "a")
    b_ws = _ws_distinct_320(tmp_path / "b")
    bufs = []
    for ws in (a_ws, b_ws):
        _enter(ws, "blocks")
        ws.block_ui._blk_open_categories()          # open the modal insert menu
        _quiesce(ws)
        ws.frame(1 / 30)
        bufs.append(bytes(ws.sys_canvas.buf))
    assert bufs[0] == bufs[1]


def test_code_editor_baseline_layout_constants(tmp_path):
    """The CodeLayout at (320, 240, 1) reproduces the frozen module constants."""
    from runtime import console as C
    lay = C.CodeLayout(320, 240, 1)
    assert lay._base
    assert lay.cols == C.CodeEditor.COLS and lay.rows == C.CodeEditor.ROWS
    # The RUN/SAVE/CLOSE top-band icons were dissolved into the unified bar (Stage-4
    # rollout): CodeLayout no longer carries their rects (PLAY/SAVE/X live in the bar).
    assert not hasattr(lay, "run_btn")
    assert lay.code_area() == C._CODE_AREA
    assert lay.sym_area == C._SYM_AREA


def test_block_editor_baseline_layout_constants(tmp_path):
    """The BlockLayout at (320, 240, 1) reproduces the frozen module constants."""
    from runtime import console as C
    lay = C.BlockLayout(320, 240, 1)
    assert lay._base
    assert lay.rows == C._BLK_ROWS and lay.menu_rows == C._BLK_MENU_ROWS
    assert lay.add_btn == C._BLK_ADD and lay.code_btn == C._BLK_CODE
    # SAVE/CLOSE dissolved into the unified bar (Stage-4 rollout): the outline shifted
    # below the bar + a hint/status strip (y0=30, 10 rows), and BlockLayout no longer
    # carries save_btn/close_btn (SAVE -> bar SAVE, CLOSE -> bar context X).
    assert not hasattr(lay, "save_btn") and not hasattr(lay, "close_btn")
    assert lay.area() == C._BLK_AREA and lay.menu == C._BLK_MENU


# ---------------------------------------------------------------------------
# Responsive: a larger system canvas shows more content.
# ---------------------------------------------------------------------------

def _ws(tmp_path, **kw):
    from runtime import host_app
    return _open_first_cart(host_app.build_workstation(str(tmp_path / "carts"), **kw))


def test_code_editor_shows_more_lines_and_columns(tmp_path):
    """At 960x600 the code editor shows MORE visible lines + WIDER columns than the
    320x240 baseline (the editor's COLS/ROWS adopt the reflowed layout)."""
    from runtime import console as C
    ws = _ws(tmp_path, sys_size=(960, 600))
    _enter(ws, "code")
    assert ws.code_layout.cols > C.CodeEditor.COLS    # wider than 38
    assert ws.code_layout.rows > C.CodeEditor.ROWS    # more than 20 lines
    assert ws.editor.COLS == ws.code_layout.cols
    assert ws.editor.ROWS == ws.code_layout.rows


def test_code_editor_font_scale_grows_text(tmp_path):
    """At a larger font the code cell + line height scale with the font (text grows),
    even as fewer-but-bigger lines/columns fit."""
    from runtime import console as C
    ws = _ws(tmp_path, sys_size=(960, 600), font_scale=2)
    _enter(ws, "code")
    lay = ws.code_layout
    assert lay.fs == 2
    assert lay.cell == 16 and lay.lh == C._CODE_LH * 2


def test_block_editor_shows_more_rows_and_wider_blocks(tmp_path):
    """At 960x600 the block outline shows MORE rows + a WIDER block area."""
    from runtime import console as C
    ws = _ws(tmp_path, sys_size=(960, 600))
    _enter(ws, "blocks")
    assert ws.block_ui.block_layout.rows > C._BLK_ROWS         # more than 11 rows
    assert ws.block_ui.block_layout.outline_w > C._BLK_W       # wider than 308


def test_code_editor_renders_without_error_on_large_canvas(tmp_path):
    """The code editor fills a 960x600 canvas (font 2x) and writes valid pixels."""
    from runtime import host_app
    ws = _ws(tmp_path, sys_size=(960, 600), font_scale=2)
    drv = host_app.ConsoleDriver(ws)
    _enter(ws, "code")
    drv.frame(1 / 30)
    buf = drv.rgb888()
    assert len(buf) == 960 * 600 * 3
    assert len(set(buf)) > 4                           # editor drew, not a flat fill


def test_block_editor_renders_without_error_on_large_canvas(tmp_path):
    """The block editor fills a 960x600 canvas (font 2x) and writes valid pixels."""
    from runtime import host_app
    ws = _ws(tmp_path, sys_size=(960, 600), font_scale=2)
    drv = host_app.ConsoleDriver(ws)
    _enter(ws, "blocks")
    drv.frame(1 / 30)
    buf = drv.rgb888()
    assert len(buf) == 960 * 600 * 3
    assert len(set(buf)) > 4


# ---------------------------------------------------------------------------
# Interaction still works at native size (touch + keyboard + trackball).
# ---------------------------------------------------------------------------

def test_code_editor_touch_places_caret_in_system_coords(tmp_path):
    """A tap in the code area lands at the right (col, row) in SYSTEM coords on a big
    canvas -- the hit-test uses the layout cell/line, not the game viewport."""
    from runtime import host_app
    ws = _ws(tmp_path, sys_size=(960, 600))
    drv = host_app.ConsoleDriver(ws)
    _enter(ws, "code")
    ed = ws.editor
    ed.set_text("\n".join("line %d here" % i for i in range(40)))
    drv.frame(1 / 30)
    lay = ws.code_layout
    # Tap the cell at visible (col=5, row=3).
    tx = lay.x0 + 5 * lay.cell + 1
    ty = lay.y0 + 3 * lay.lh + 1
    drv.touch(tx, ty)
    drv.frame(1 / 30)
    assert ed.row == ed.top + 3
    assert ed.col == ed.left + 5


def test_code_editor_typing_inserts(tmp_path):
    """Keyboard typing still inserts into the buffer on a large canvas."""
    from runtime import host_app
    ws = _ws(tmp_path, sys_size=(960, 600))
    drv = host_app.ConsoleDriver(ws)
    _enter(ws, "code")
    ws.editor.set_text("")
    for ch in "hi":
        drv.type_char(ord(ch))
        drv.frame(1 / 30)
    assert ws.editor.text() == "hi"


def test_code_editor_symbol_palette_tap(tmp_path):
    """Tapping a symbol-palette cell inserts that symbol (the palette reflows to the
    scaled cells at native size)."""
    from runtime import host_app
    from runtime import console as C
    ws = _ws(tmp_path, sys_size=(960, 600))
    drv = host_app.ConsoleDriver(ws)
    _enter(ws, "code")
    ws.editor.set_text("")
    drv.frame(1 / 30)
    lay = ws.code_layout
    # Tap the first symbol cell ('=').
    drv.touch(lay.sym_area[0] + 1, lay.sym_y + 1)
    drv.frame(1 / 30)
    assert ws.editor.text() == C._CODE_SYMBOLS[0]


def test_block_editor_action_bar_tap_opens_menu(tmp_path):
    """Tapping ADD opens the insert menu at native size (the bar rect is layout-
    derived, hit-tested in system coords)."""
    from runtime import host_app
    ws = _ws(tmp_path, sys_size=(960, 600))
    drv = host_app.ConsoleDriver(ws)
    _enter(ws, "blocks")
    drv.frame(1 / 30)
    lay = ws.block_ui.block_layout
    bx, by, bw, bh = lay.add_btn
    drv.touch(bx + bw // 2, by + bh // 2)
    drv.frame(1 / 30)
    assert ws.block_ui.blk_menu is not None and ws.block_ui.blk_menu["mode"] == "cat"


def test_block_editor_cursor_nav_works(tmp_path):
    """Block cursor nav (the A/move model) still scrolls the reflowed outline."""
    from runtime import host_app
    ws = _ws(tmp_path, sys_size=(960, 600))
    _enter(ws, "blocks")
    be = ws.block_ui.blocks_ed
    start = be.cur
    ws.block_ui._blk_move_cursor(1)
    assert be.cur != start or len(be.rows) <= 1


def test_font_scale_change_reflows_open_code_editor(tmp_path):
    """Bumping the font size while the code editor is open reflows it live (the
    editor adopts the new visible window) without losing the buffer."""
    ws = _ws(tmp_path, sys_size=(960, 600))
    _enter(ws, "code")
    ws.editor.set_text("hello world")
    cols1 = ws.editor.COLS
    ws.set_font_scale(2, persist=False)
    assert ws.editor.COLS != cols1                    # the window reflowed
    assert ws.editor.text() == "hello world"          # buffer intact


# ---------------------------------------------------------------------------
# Step 3: the PAINT + MAP editors are system-domain responsive too.
# ---------------------------------------------------------------------------


def test_paint_editor_320x240_is_byte_identical(tmp_path):
    """The paint editor on a DISTINCT 320x240 system canvas (font 1x) renders
    exactly the same pixels as the shared-canvas (today) path."""
    a_ws = _ws_shared(tmp_path / "a")
    b_ws = _ws_distinct_320(tmp_path / "b")
    bufs = []
    for ws in (a_ws, b_ws):
        ws._open_paint()
        _quiesce(ws)
        ws.frame(1 / 30)
        bufs.append(bytes(ws.sys_canvas.buf))
    assert bufs[0] == bufs[1]


def test_paint_layout_baseline_constants(tmp_path):
    """PaintLayout at (320, 240, 1) reproduces the frozen module constants. No
    save_btn (#111 -- the SAVE button was removed, not just relocated)."""
    from runtime import paint_layer as P
    lay = P.PaintLayout(320, 240, 1)
    assert lay._base
    assert not hasattr(lay, "save_btn")
    assert lay.pg_area == P._PG_AREA and lay.pg_span == P._PG_SPAN
    assert lay.sw_area == P._SW_AREA
    assert lay.spr_prev == P._SPR_PREV and lay.spr_next == P._SPR_NEXT
    assert lay.size_btn == P._PAINT_SIZE
    assert lay.get_btn == P._PAINT_GET and lay.put_btn == P._PAINT_PUT
    assert lay.close_btn == P._PAINT_CLOSE
    # The #90 two tool rows: one flat list (row 1 then row 2), aligned to _TOOLS, all
    # inside the panel and clear of the pixel grid.
    assert len(lay.tool_btns) == len(P._TOOLS)
    assert lay.tool_btns[0][1] == P._TOOL_ROW1_Y          # row 1 first
    assert lay.tool_btns[len(P._TOOL_ROW1)][1] == P._TOOL_ROW2_Y   # row 2 starts
    grid_bottom = P._PG_Y0 + P._PG_SPAN
    for (x, y, w, h) in lay.tool_btns:
        assert y >= grid_bottom and y + h <= P._PAINT_CLOSE[1]


def test_paint_files_button_clears_the_sprite_preview(tmp_path):
    """#111 layout nit: the FILE button (#108, send-to-Files) used to sit flush
    against the sprite preview thumbnail at 320x240 -- a snug fit that read as an
    overlap. It must now clear the preview box with no overlap."""
    from runtime import paint_layer as P
    lay = P.PaintLayout(320, 240, 1)
    fx, fy, fw, fh = lay.files_btn
    px, py = lay.prev_xy
    pw = ph = lay.prev_box
    # Standard axis-aligned rect overlap test: NOT overlapping iff separated on
    # some axis.
    overlaps = not (fx + fw <= px or px + pw <= fx or fy + fh <= py or py + ph <= fy)
    assert not overlaps, "FILE button must not overlap the sprite preview"


def test_paint_editor_grid_grows_on_large_canvas(tmp_path):
    """At 960x600 the zoomed pixel grid grows (a multiple of 48px, so every sprite
    size still edits in whole on-screen pixels) and the editor renders valid pixels
    on the system canvas with no viewport bezel."""
    from runtime import paint_layer as P
    from runtime import host_app
    ws = _ws(tmp_path, sys_size=(960, 600))
    drv = host_app.ConsoleDriver(ws)
    ws._open_paint()
    lay = ws.paint_layer.layout
    assert lay.pg_span > P._PG_SPAN and lay.pg_span % 48 == 0
    drv.frame(1 / 30)
    buf = drv.rgb888()
    assert len(buf) == 960 * 600 * 3
    assert len(set(buf)) > 4


def test_paint_editor_tap_paints_in_system_coords(tmp_path):
    """A tap inside the (reflowed) grid paints the right sheet pixel, hit-tested in
    SYSTEM coords -- and a swatch tap picks a color at the layout's position."""
    from runtime import host_app
    ws = _ws(tmp_path, sys_size=(960, 600))
    drv = host_app.ConsoleDriver(ws)
    ws._open_paint()
    drv.frame(1 / 30)
    pe = ws.paint
    lay = ws.paint_layer.layout
    # Pick color 12 from the swatch column.
    sx = lay.sw_x0 + (12 % lay.sw_cols) * lay.sw + 1
    sy = lay.sw_y0 + (12 // lay.sw_cols) * lay.sw + 1
    drv.touch(sx, sy)
    drv.frame(1 / 30)
    assert pe.color == 12
    # Paint pixel (0, 0) of the tile via the top-left grid cell.
    drv.touch(lay.pg_x0 + 2, lay.pg_y0 + 2)
    drv.frame(1 / 30)
    ox, oy = ws.project.sheet.tile_origin(pe.n)
    assert ws.project.sheet.pget(ox, oy) == 12


def test_map_editor_320x240_is_byte_identical(tmp_path):
    """The map editor on a DISTINCT 320x240 system canvas (font 1x) renders
    exactly the same pixels as the shared-canvas (today) path."""
    a_ws = _ws_shared(tmp_path / "a")
    b_ws = _ws_distinct_320(tmp_path / "b")
    bufs = []
    for ws in (a_ws, b_ws):
        ws._open_map()
        _quiesce(ws)
        ws.frame(1 / 30)
        bufs.append(bytes(ws.sys_canvas.buf))
    assert bufs[0] == bufs[1]


def test_map_layout_baseline_constants(tmp_path):
    """MapLayout at (320, 240, 1) reproduces the frozen module constants. No
    save_btn (#111 -- the SAVE button was removed, not just relocated)."""
    from runtime import map_editor_ui as M
    lay = M.MapLayout(320, 240, 1)
    assert lay._base
    assert not hasattr(lay, "save_btn")
    assert (lay.mv_x0, lay.mv_y0) == (M._MV_X0, M._MV_Y0)
    assert (lay.mv_avail_w, lay.mv_avail_h) == (M._MV_AVAIL_W, M._MV_AVAIL_H)
    assert lay.zooms == tuple(M._MV_ZOOMS)
    assert lay.tp_area == M._TP_AREA and lay.tp_page == M._TP_PAGE
    assert lay.tp_prev == M._TP_PREV and lay.tp_next == M._TP_NEXT
    assert lay.sky_btn == M._TP_SKY and lay.zoom_btn == M._MAP_ZOOM
    assert lay.size_btn == M._MAP_SIZE
    assert (lay.pan_up, lay.pan_lf, lay.pan_rt, lay.pan_dn) == \
        (M._PAN_UP, M._PAN_LF, M._PAN_RT, M._PAN_DN)
    assert (lay.erase_btn, lay.close_btn) == (M._MAP_ERASE, M._MAP_CLOSE)


def test_map_editor_shows_more_cells_on_large_canvas(tmp_path):
    """At 960x600 the map view rectangle grows (more visible cells at the fit zoom)
    and the fit-both default cell is recomputed for the bigger view."""
    from runtime import map_editor_ui as M
    from runtime import host_app
    ws = _ws(tmp_path, sys_size=(960, 600))
    drv = host_app.ConsoleDriver(ws)
    ws._open_map()
    lay = ws.map_ui.layout
    assert lay.mv_avail_w > M._MV_AVAIL_W and lay.mv_avail_h > M._MV_AVAIL_H
    assert lay.zooms[0] > M._MV_ZOOMS[0]           # bigger fit cell on a bigger view
    x0, y0, cell, cols, rows = ws.map_ui._mv_metrics()
    assert cols >= M._MV_FIT_COLS and rows >= M._MV_FIT_ROWS
    drv.frame(1 / 30)
    buf = drv.rgb888()
    assert len(buf) == 960 * 600 * 3
    assert len(set(buf)) > 4


def test_map_editor_tap_paints_in_system_coords(tmp_path):
    """A tap inside the reflowed map view stamps the brush at the right cell,
    hit-tested in SYSTEM coords."""
    from runtime import host_app
    ws = _ws(tmp_path, sys_size=(960, 600))
    drv = host_app.ConsoleDriver(ws)
    ws._open_map()
    drv.frame(1 / 30)
    me = ws.map_ui.mapedit
    tm = ws.project.tilemap
    if me is None or tm is None:
        return                                     # cart without a map -- nothing to pin
    me.n = 3                                       # pick a brush tile
    x0, y0, cell, cols, rows = ws.map_ui._mv_metrics()
    drv.touch(x0 + 2, y0 + 2)                      # stamp cell (0, 0)
    drv.frame(1 / 30)
    assert tm.mget(0, 0) == 3


def test_music_editor_320x240_is_byte_identical(tmp_path):
    """The music editor on a DISTINCT 320x240 system canvas (font 1x) renders
    exactly the same pixels as the shared-canvas (today) path."""
    a_ws = _ws_shared(tmp_path / "a")
    b_ws = _ws_distinct_320(tmp_path / "b")
    bufs = []
    for ws in (a_ws, b_ws):
        ws._open_music()
        _quiesce(ws)
        ws.frame(1 / 30)
        bufs.append(bytes(ws.sys_canvas.buf))
    assert bufs[0] == bufs[1]


def test_music_layout_baseline_constants(tmp_path):
    """MusicLayout at (320, 240, 1) reproduces the frozen module constants."""
    from runtime import music_editor_ui as M
    lay = M.MusicLayout(320, 240, 1)
    assert lay._base
    assert lay.title_y == M._MU_TITLE_Y and lay.view_btn == M._MU_VIEW
    assert (lay.obj_prev, lay.obj_next) == (M._MU_OBJ_PREV, M._MU_OBJ_NEXT)
    assert (lay.speed_dn, lay.speed_up) == (M._MU_SPEED_DN, M._MU_SPEED_UP)
    assert lay.list_area == M._MU_LIST_AREA and lay.rows == M._MU_ROWS
    assert lay.pad_rect(0, 0) == M._mu_pad_rect(0, 0)
    assert lay.pad_rect(1, 3) == M._mu_pad_rect(1, 3)
    assert (lay.play_btn, lay.loop_btn) == (M._MU_PLAY, M._MU_LOOP)


def test_music_editor_shows_more_rows_on_large_canvas(tmp_path):
    """At 960x600 the tracker list shows MORE visible rows than the baseline 10,
    and the editor renders valid pixels on the system canvas."""
    from runtime import music_editor_ui as M
    from runtime import host_app
    ws = _ws(tmp_path, sys_size=(960, 600))
    drv = host_app.ConsoleDriver(ws)
    ws._open_music()
    assert ws.music_ui.layout.rows > M._MU_ROWS
    drv.frame(1 / 30)
    buf = drv.rgb888()
    assert len(buf) == 960 * 600 * 3
    assert len(set(buf)) > 4


def test_music_editor_tap_works_in_system_coords(tmp_path):
    """Tapping the PLAY button at the layout's position starts a preview on a big
    canvas (system-coord hit-testing)."""
    from runtime import host_app
    ws = _ws(tmp_path, sys_size=(960, 600))
    drv = host_app.ConsoleDriver(ws)
    ws._open_music()
    drv.frame(1 / 30)
    if ws.music_ui.musicedit is None:
        return                                     # no audio bank -- nothing to pin
    lay = ws.music_ui.layout
    bx, by, bw, bh = lay.play_btn
    drv.touch(bx + bw // 2, by + bh // 2)
    drv.frame(1 / 30)
    assert ws.music_ui.music_preview is not None


def test_cards_editor_320x240_is_byte_identical(tmp_path):
    """The Config/cards editor on a DISTINCT 320x240 system canvas (font 1x)
    renders exactly the same pixels as the shared-canvas (today) path."""
    a = _render_editor(_ws_shared(tmp_path / "a"), "cards")
    b = _render_editor(_ws_distinct_320(tmp_path / "b"), "cards")
    assert a == b


def test_cards_layout_baseline_constants(tmp_path):
    """CardsLayout at (320, 240, 1) reproduces the frozen module constants."""
    from runtime import cards_layer as CL
    lay = CL.CardsLayout(320, 240, 1)
    assert lay._base
    assert (lay.card_x, lay.card_w) == (CL._CARD_X, CL._CARD_W)
    assert (lay.card_y0, lay.card_h) == (CL._CARD_Y0, CL._CARD_H)
    assert lay.view_bottom == CL._CARD_VIEW_BOTTOM
    assert (lay.scroll_up, lay.scroll_dn) == (CL._CARD_SCROLL_UP, CL._CARD_SCROLL_DN)


def test_cards_editor_shows_more_cards_on_large_canvas(tmp_path):
    """At 960x600 the cards view band grows (more cards visible before scrolling)
    and the cards span the full width."""
    from runtime import cards_layer as CL
    from runtime import host_app
    ws = _ws(tmp_path, sys_size=(960, 600))
    drv = host_app.ConsoleDriver(ws)
    _enter(ws, "cards")
    lay = ws.cards_layer.layout
    assert lay.view_bottom - lay.card_y0 > CL._CARD_VIEW_BOTTOM - CL._CARD_Y0
    assert lay.card_w > CL._CARD_W
    drv.frame(1 / 30)
    buf = drv.rgb888()
    assert len(buf) == 960 * 600 * 3
    assert len(set(buf)) > 4


def test_cards_editor_tap_steps_in_system_coords(tmp_path):
    """A tap on a plain card's right half increments its config value, hit-tested
    in SYSTEM coords on a big canvas."""
    from runtime import host_app
    ws = _ws(tmp_path, sys_size=(960, 600))
    drv = host_app.ConsoleDriver(ws)
    _enter(ws, "cards")
    drv.frame(1 / 30)
    rows = ws.cards_layer._card_layout()
    plain = next((r for r in rows if r["display"] is None), None)
    if plain is None:
        return                                     # no plain stepper card to pin
    f = plain["f"]
    before = ws.project.config.get(f["key"], f.get("default"))
    drv.touch(plain["x"] + plain["w"] - 20, plain["y"] + 2)   # right half -> +1
    drv.frame(1 / 30)
    after = ws.project.config.get(f["key"], f.get("default"))
    assert after != before


def test_theme_editor_320x240_is_byte_identical(tmp_path):
    """EDIT ICONS (the theme reuse of the paint renderer) is byte-identical at
    320x240 too."""
    a_ws = _ws_shared(tmp_path / "a")
    b_ws = _ws_distinct_320(tmp_path / "b")
    bufs = []
    for ws in (a_ws, b_ws):
        ws.open_theme()
        _quiesce(ws)
        ws.frame(1 / 30)
        bufs.append(bytes(ws.sys_canvas.buf))
    assert bufs[0] == bufs[1]
