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

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

import canvas_probe as probe  # noqa: E402  (pixel-width-agnostic "it drew" probes)
from ws_helpers import select_first_game  # noqa: E402
import pytest
from ws_helpers import boot_on_fixture

@pytest.fixture(autouse=True)
def _on_the_night_fixture(monkeypatch):
    """These pin the lookless pixel bar and its coordinates: every
    workstation here boots on the `night` fixture (ws_helpers)."""
    boot_on_fixture(monkeypatch)


def _open_first_cart(ws):
    select_first_game(ws)
    ws.open()
    return ws


def _quiesce(ws):
    """Hide the cursor + clear the achievement toast so a rendered frame is the pure
    editor (the same quiescing test_two_domain_seam.py does), and PIN THE CLOCK.

    The OS bar prints a live HH:MM. Each byte-identical test below builds two whole
    workstations -- seeding ~30 carts each -- and compares their rendered frames, so
    a minute boundary falling between the two renders changed 21 bytes of clock text
    and failed the assertion. That is a real flake, not a real difference: it took
    down CI on Python 3.11 while 3.10 passed the same commit (2026-07-29).

    Pinning the per-second cache does NOT work -- bar_layer hits it with
    `now_s == self._clock_at`, so a sentinel that never equals the current second
    forces a recompute every frame, which is the opposite of what you want. Bind
    the accessor instead. No production code needs a test hook for this.
    """
    if ws.pointer is not None:
        ws.pointer.visible = False
    ws._toast_until = 0
    ws.bar_layer._clock_text = lambda: "00:00"


def _ws_shared(tmp_path):
    """The default (T-Deck) build: one shared 320x240 canvas == today."""
    from runtime import host_app
    return _open_first_cart(host_app.build_workstation(str(tmp_path / "carts")))


def _ws_distinct_320(tmp_path):
    """A console with a DISTINCT 320x240 system canvas, forcing the editors onto the
    system-canvas path (vs. the shared-object degradation). Both must render pixel-
    identically -- that's the byte-for-byte degradation guarantee."""
    from runtime import host_app
    from runtime.host_canvas import make_system_canvas
    ws = host_app.build_workstation(str(tmp_path / "carts"))
    ws._sys_canvas = make_system_canvas(320, 240, font_scale=1)
    ws._relayout()
    ws.set_pointer(host_app.console.Pointer(320, 240))
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
    return bytes(ws.sys_canvas._buf)


# ---------------------------------------------------------------------------
# A distinct 320x240 system canvas draws what the shared canvas draws.
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
        bufs.append(bytes(ws.sys_canvas._buf))
    assert bufs[0] == bufs[1]


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
    assert probe.distinct_pixels_in(buf, 3) > 4     # editor drew, not a flat fill


def test_block_editor_renders_without_error_on_large_canvas(tmp_path):
    """The block editor fills a 960x600 canvas (font 2x) and writes valid pixels."""
    from runtime import host_app
    ws = _ws(tmp_path, sys_size=(960, 600), font_scale=2)
    drv = host_app.ConsoleDriver(ws)
    _enter(ws, "blocks")
    drv.frame(1 / 30)
    buf = drv.rgb888()
    assert len(buf) == 960 * 600 * 3
    assert probe.distinct_pixels_in(buf, 3) > 4


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
    tx = ws.code_layer._text_x0(lay, ed) + 5 * lay.cell + 1
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
    from runtime import ui_draw
    ws = _ws(tmp_path, sys_size=(960, 600))
    drv = host_app.ConsoleDriver(ws)
    _enter(ws, "code")
    ui_draw.input_kind("touch")              # the keys show after touch (D-17)
    ws.editor.set_text("")
    drv.frame(1 / 30)
    lay = ws.code_layout
    assert lay.symbols
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
    ws.look.set_font_scale(2, persist=False)
    assert ws.editor.COLS != cols1                    # the window reflowed
    assert ws.editor.text() == "hello world"          # buffer intact


# ---------------------------------------------------------------------------
# Entering a tab RE-DERIVES its geometry (#216) -- and must not move the view.
#
# Owner on the T-Deck: "code tab is now buggy while moving around the menu".
# _relayout_tab runs on EVERY entry into a tab, and the Code tab's relayout handed
# the editor a view size unconditionally; set_view_size re-runs the caret-follow
# scroll, so walking the tab ladder (or re-tapping CODE while already on it) yanked
# the view back to the caret. Driven at the T-Deck's own 320x240/1x.
# ---------------------------------------------------------------------------

_LONG_SRC = "\n".join("# line %d" % i for i in range(90))


def _scrolled_code_ws(tmp_path, **kw):
    """The Code tab, open in the EDITOR, with the caret on one line and the view
    panned AWAY from it -- what a kid has after tapping a line and drag-scrolling."""
    from runtime import host_app
    ws = host_app.build_workstation(str(tmp_path / "carts"), **kw)
    select_first_game(ws)
    ws.open_in_editor()
    ws.set_menu_view("code")
    ed = ws.editor
    ed.set_text(_LONG_SRC)
    ed.row, ed.col = 20, 3
    ed.scroll(60, 0)                        # the drag-scroll verb: view only
    assert ed.top >= ed.row + ed.ROWS, "the caret must be off-screen for this test"
    return ws, ed


def _view(ed):
    return (ed.row, ed.col, ed.top, ed.left)


def test_leaving_and_re_entering_code_keeps_the_view(tmp_path):
    ws, ed = _scrolled_code_ws(tmp_path)
    was = _view(ed)

    ws.set_menu_view("paint")
    ws.set_menu_view("code")

    assert ws.editor is ed                  # the same buffer came back...
    assert _view(ed) == was, "entering the Code tab must not move the view (#216)"


def test_re_entering_the_tab_already_shown_keeps_the_view(tmp_path):
    """Tapping CODE while already on CODE is a set_tab call like any other -- it
    re-derives the layout, and that is not a reason to scroll."""
    ws, ed = _scrolled_code_ws(tmp_path)
    was = _view(ed)

    ws.set_menu_view("code")

    assert _view(ed) == was


def test_re_entry_keeps_the_view_with_the_gutter_on(tmp_path):
    """The line-number gutter NARROWS the editor's own window every draw, so a gate
    that compared the editor's live window against the layout would re-widen it on
    entry and scroll after all. The gate compares LAYOUTS."""
    ws, ed = _scrolled_code_ws(tmp_path)
    ws.code_layer._gutter = True
    ws.code_layer._apply_gutter(ws.code_layout, ed)
    ed.scroll(40, 0)                        # _apply_gutter re-clamped; pan away again
    narrowed = ed.COLS
    was = _view(ed)

    ws.set_menu_view("map")
    ws.set_menu_view("code")

    assert _view(ed) == was
    assert ed.COLS == narrowed              # ...and the gutter's window survived


def test_a_pending_commit_does_not_move_the_view_either(tmp_path):
    """Moving around the ladder with an edit still inside its debounce window: the
    switch owes the commit and the frame behind it pays (#154), and nothing about
    the view jumps -- neither the caret nor the scroll. The edit is HALF-TYPED,
    which is what a kid moving around the menu actually has: it is kept and
    badged, and badging must not walk the caret onto the error the way the run
    gate does."""
    from runtime import moy_carts
    ws, ed = _scrolled_code_ws(tmp_path)
    path = ws.cart["path"]
    ed.row, ed.col = len(ed.lines) - 1, len(ed.lines[-1])
    for ch in b"\nx = (":                   # a line the kid has not finished
        ed.key(ch)
    ed.scroll(-40, 0)                       # ...then look somewhere else
    was = _view(ed)

    ws.set_menu_view("music")
    ws.frame(1 / 30)                        # the frame that pays the owed commit
    ws.set_menu_view("code")

    assert _view(ed) == was
    assert moy_carts.load(path)["src"] == ed.text()
    assert (ws.save_status or "").startswith("SYNTAX"), ws.save_status


def test_the_code_tab_still_reflows_when_the_window_really_changes(tmp_path):
    """The gate must not cost the reflow it guards: a font-scale change is a REAL
    new window, so the editor adopts it and re-clamps onto the caret. (The 320x240
    tier cannot show this -- its effective font scale degrades back to 1.)"""
    ws = _ws(tmp_path, sys_size=(960, 600))
    _enter(ws, "code")
    ed = ws.editor
    ed.set_text(_LONG_SRC)
    ed.row, ed.col = 80, 0
    ed._scroll()
    rows1 = ed.ROWS
    assert ed.top > 0

    ws.look.set_font_scale(2, persist=False)

    assert ed.ROWS != rows1                 # the window really reflowed
    assert ed.top <= ed.row < ed.top + ed.ROWS, "a reflow re-clamps onto the caret"


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
        bufs.append(bytes(ws.sys_canvas._buf))
    assert bufs[0] == bufs[1]


def test_paint_layout_fits_the_320x240_canvas():
    """The T-Deck's Sprites tab (docs/theming_2026-09.md §9, phase 3 step 4):
    the 144px grid, the tool rows under the grid and the palette, clear of the
    button column, and GET / PUT / FILE stacked clear of CLOSE."""
    from runtime import paint_layer as P
    lay = P.PaintLayout(320, 240, 1)
    assert lay.pg_span == 144
    gx, gy, gw, gh = lay.pg_area
    col_x = min(b[0] for b in (lay.get_btn, lay.put_btn, lay.files_btn, lay.close_btn))
    assert len(lay.tool_btns) == len(P._TOOLS)
    for (x, y, w, h) in lay.tool_btns:
        assert y >= gy + gh and x + w <= col_x and y + h <= 240
    for b in (lay.get_btn, lay.put_btn, lay.files_btn):
        assert b[1] + b[3] <= lay.close_btn[1]
    assert lay.status_xy[1] + 8 <= lay.panel[1] + lay.panel[3]


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
    assert probe.distinct_pixels_in(buf, 3) > 4


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
        bufs.append(bytes(ws.sys_canvas._buf))
    assert bufs[0] == bufs[1]


def test_map_editor_shows_more_cells_on_large_canvas(tmp_path):
    """At 960x600 the map view rectangle grows, and the extra pixels buy MORE MAP:
    the cell is the map field size and does not change with the screen."""
    from runtime import map_editor_ui as M
    from runtime import host_app
    ws = _ws(tmp_path, sys_size=(960, 600))
    drv = host_app.ConsoleDriver(ws)
    ws._open_map()
    lay = ws.map_ui.layout
    base = M.MapLayout(320, 240, 1)
    assert lay.mv_avail_w > M._MV_AVAIL_W and lay.mv_avail_h > M._MV_AVAIL_H
    assert lay.zooms[0] == base.zooms[0]           # same field size on every tier
    x0, y0, cell, cols, rows = ws.map_ui._mv_metrics()
    assert cols > base.mv_avail_w // base.zooms[0]   # ...so the bigger view shows more
    assert rows > base.mv_avail_h // base.zooms[0]
    drv.frame(1 / 30)
    buf = drv.rgb888()
    assert len(buf) == 960 * 600 * 3
    assert probe.distinct_pixels_in(buf, 3) > 4


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
        bufs.append(bytes(ws.sys_canvas._buf))
    assert bufs[0] == bufs[1]


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
    assert probe.distinct_pixels_in(buf, 3) > 4


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
    assert probe.distinct_pixels_in(buf, 3) > 4


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
        bufs.append(bytes(ws.sys_canvas._buf))
    assert bufs[0] == bufs[1]
