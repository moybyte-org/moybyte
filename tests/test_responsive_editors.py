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
    assert lay.run_btn == C._ED_RUN and lay.save_btn == C._ED_SAVE
    assert lay.close_btn == C._ED_CLOSE
    assert lay.code_area() == C._CODE_AREA
    assert lay.sym_area == C._SYM_AREA


def test_block_editor_baseline_layout_constants(tmp_path):
    """The BlockLayout at (320, 240, 1) reproduces the frozen module constants."""
    from runtime import console as C
    lay = C.BlockLayout(320, 240, 1)
    assert lay._base
    assert lay.rows == C._BLK_ROWS and lay.menu_rows == C._BLK_MENU_ROWS
    assert lay.add_btn == C._BLK_ADD and lay.close_btn == C._BLK_CLOSE
    assert lay.save_btn == C._BLK_SAVE and lay.code_btn == C._BLK_CODE
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
# Sprite/paint + map editors stay a 320x240 viewport (step 3, NOT this step).
# ---------------------------------------------------------------------------

def test_paint_and_map_still_use_the_game_viewport(tmp_path):
    """The paint + map editors still draw on the fixed 320x240 GAME canvas and are
    composited as a viewport -- they are explicitly OUT OF SCOPE for step 2. Proof:
    a running paint/map frame composites (the game buffer differs from a flat fill)
    while the system canvas shows the centered viewport with a letterbox bezel."""
    from runtime import host_app
    from runtime import console as C
    ws = _ws(tmp_path, sys_size=(960, 600))
    drv = host_app.ConsoleDriver(ws)
    ws._open_paint()
    drv.frame(1 / 30)
    ox, oy, scale = ws._viewport()
    assert scale == 2                                  # the fixed-aspect 320x240 viewport
    # The bezel corner (outside the viewport) is the solid bezel color (letterboxed).
    assert ws.sys_canvas.buf[0] == C._VIEWPORT_BEZEL
    # The paint editor drew on the GAME canvas (not the system canvas directly).
    assert len(set(ws.canvas.buf)) > 4
