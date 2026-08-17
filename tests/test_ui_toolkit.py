"""runtime/ui.py -- the shared immediate-mode widget toolkit (visual identity
v1 Phase 3): rect algebra, the draw==tap Hits registry, themed widgets, and the
one ScrollRegion model. Pixel checks run on a real SystemCanvas so the drawing
path is the one every tier shares."""

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

from runtime import ui
from runtime.host_canvas import make_system_canvas as SystemCanvas
from runtime.chrome import theme_colors


TH = theme_colors("machine")


# -- rect algebra -------------------------------------------------------------

def test_inset_and_cuts():
    r = (10, 20, 100, 50)
    assert ui.inset(r, 5) == (15, 25, 90, 40)
    assert ui.inset(r, 60) == (70, 80, 0, 0)          # clamped, never negative
    band, rest = ui.cut_top(r, 12)
    assert band == (10, 20, 100, 12) and rest == (10, 32, 100, 38)
    band, rest = ui.cut_bottom(r, 12)
    assert band == (10, 58, 100, 12) and rest == (10, 20, 100, 38)
    band, rest = ui.cut_left(r, 30)
    assert band == (10, 20, 30, 50) and rest == (40, 20, 70, 50)
    band, rest = ui.cut_right(r, 30)
    assert band == (80, 20, 30, 50) and rest == (10, 20, 70, 50)
    band, rest = ui.cut_top(r, 999)                    # oversized cut clamps
    assert band == r and rest[3] == 0


def test_splits_cover_without_overlap():
    r = (0, 0, 103, 30)
    cols = ui.hsplit(r, 3, gap=2)
    assert len(cols) == 3
    assert cols[0][0] == 0
    for a, b in zip(cols, cols[1:]):
        assert a[0] + a[2] + 2 == b[0]                 # gap respected
    assert cols[-1][0] + cols[-1][2] == 103            # slack goes to the last
    rows = ui.vsplit((0, 0, 10, 91), 4)
    assert rows[-1][1] + rows[-1][3] == 91


# -- Hits ----------------------------------------------------------------------

def test_hits_topmost_wins_and_clear():
    h = ui.Hits()
    h.add((0, 0, 100, 100), "under", 1)
    h.add((10, 10, 20, 20), "over", 2)
    assert h.at(15, 15) == ("over", 2)                 # drawn later = on top
    assert h.at(50, 50) == ("under", 1)
    assert h.at(200, 200) is None
    h.clear()
    assert h.at(15, 15) is None


# -- widgets --------------------------------------------------------------------

def _cv(w=320, h=120, fs=1):
    return SystemCanvas(w, h, font_scale=fs)


def test_button_kinds_paint_their_tokens():
    cv = _cv()
    ui.button(cv, TH, (10, 10, 80, 20), "PLAY", kind="play")
    assert cv.pix(12, 12) == TH["play"]
    assert cv.pix(10, 10) == 0                         # dark edge
    ui.button(cv, TH, (10, 50, 80, 20), "CHANGE", kind="normal")
    assert cv.pix(12, 52) == TH["surface"]
    ui.button(cv, TH, (10, 80, 80, 20), "DEL", kind="danger")
    assert cv.pix(12, 82) == TH["danger"]
    ui.button(cv, TH, (110, 10, 80, 20), "ON", on=True)
    assert cv.pix(112, 12) == TH["accent"]


def test_chip_states_match_the_legacy_button_pixels():
    """ui.chip replaced the four per-app `_button` copies -- pin the three
    states to the exact colors those drew (panel/title_ink/dim quiet chip,
    accent/black/edge toggle, danger/white armed action)."""
    cv = _cv()
    ui.chip(cv, TH, (10, 10, 80, 20), "SAVE")
    assert cv.pix(12, 12) == TH["panel"]
    assert cv.pix(10, 10) == TH["dim"]                 # quiet border
    ui.chip(cv, TH, (10, 40, 80, 20), "IMAGES", on=True)
    assert cv.pix(12, 42) == TH["accent"]
    assert cv.pix(10, 40) == TH["edge"]                # toggle border
    ui.chip(cv, TH, (10, 70, 80, 20), "DELETE?", hot=True)
    assert cv.pix(12, 72) == TH["danger"]


def test_chip_clips_an_overlong_label_inside_its_rect():
    """#174: a label wider than its chip must clip (draw-what-fits), never
    escape -- the desk pill overflow landed title_ink on the wallpaper
    (invisible black-on-black on Open Machine's field)."""
    cv = _cv()
    cv.rect(0, 0, 320, 120, 7)                     # a field escaped ink shows on
    r = (10, 10, 40, 20)
    ui.chip(cv, TH, r, "STORYBOOK")
    for x in range(r[0] + r[2], 320):
        for y in range(120):
            assert cv.pix(x, y) == 7               # nothing escapes the rect
    # The truncated label still draws inside the chip.
    assert any(cv.pix(x, y) == TH["title_ink"]
               for y in range(r[1], r[1] + r[3])
               for x in range(r[0], r[0] + r[2]))


def test_apps_button_delegates_to_chip(tmp_path):
    """The Appearance app's toolbar button (one of the four migrated copies)
    still paints its exact legacy pixels through the delegate."""
    from runtime import host_app
    ws = host_app.build_workstation(str(tmp_path / "carts"))
    cv = ws.sys_canvas
    ws.appearance_app._button(cv, "CARTS", (10, 100, 60, 16), on=True)
    th = ws.theme_colors
    assert cv.pix(12, 102) == th["accent"]
    assert cv.pix(10, 100) == th["edge"]
    ws.appearance_app._button(cv, "CARTS", (10, 120, 60, 16), on=False)
    assert cv.pix(12, 122) == th["panel"]
    assert cv.pix(10, 120) == th["dim"]


def test_tab_row_geometry_and_overflow():
    tabs = [("cards", "CONFIG"), ("code", "CODE"), ("map", "MAP")]
    wide = ui.tab_row_rects((0, 0, 400, 18), tabs, 1)
    assert [t[0] for t in wide] == ["cards", "code", "map"]
    assert all(labels_on for _t, _r, labels_on in wide)
    # Too narrow for labels -> icon-only chips for EVERY tab (uniform collapse).
    slim = ui.tab_row_rects((0, 0, 90, 18), tabs, 1)
    assert slim and not any(labels_on for _t, _r, labels_on in slim)
    # Rects never leave the row.
    for _t, (x, y, w, h), _l in slim:
        assert x + w <= 90
    # Narrower still -> trailing chips drop (draw-what-fits), never overlap.
    tiny = ui.tab_row_rects((0, 0, 40, 18), tabs, 1)
    assert len(tiny) < len(tabs)


def test_tab_row_draws_active_selection_and_hits():
    cv = _cv(w=420)
    hits = ui.Hits()
    tabs = [("cards", "CONFIG", "edit"), ("code", "CODE", "code")]
    rects = ui.tab_row(cv, TH, (0, 0, 420, 18), tabs, "code", hits=hits)
    assert [r[0] for r in rects] == ["cards", "code"]
    _tid, (x, y, w, h), _l = rects[1]
    assert cv.pix(x + 1, y + 1) == TH["selection"]     # active tab field
    assert hits.at(x + 2, y + 2) == ("tab", "code")
    assert hits.at(rects[0][1][0] + 2, 2) == ("tab", "cards")


def test_panel_and_status_row_paint():
    cv = _cv()
    content = ui.panel(cv, TH, (20, 20, 200, 80), title="INSPECTOR")
    assert cv.pix(22, 40) in (TH["surface"], TH["title"])
    assert content[1] > 21 and content[3] < 80         # title strip consumed
    ui.status_row(cv, TH, (0, 100, 320, 14), ("Ln 3, Col 7", "SAVED"))
    assert cv.pix(2, 108) == TH["surface_alt"]


def test_focus_ring_paints_focus_token():
    cv = _cv()
    ui.focus_ring(cv, TH, (50, 50, 40, 30))
    assert cv.pix(50 - 2, 50 - 2) == TH["focus"]


def test_is_light_gate():
    """The presentation class is explicit, independent of palette choices."""
    assert ui.is_light(theme_colors("machine"))
    assert not ui.is_light(theme_colors("night"))
    assert ui.is_light({"surface_light": True, "ink": 1})
    assert not ui.is_light({"surface_light": False, "ink": 0})


def test_scroll_cues_draw_what_can_move():
    cv = _cv()
    ui.scroll_cues(cv, (10, 10), (10, 100), True, False, 7)
    # '^' drew at (10,10); 'v' suppressed at (10,100).
    assert any(cv.pix(10 + dx, 10 + dy) == 7
               for dx in range(8) for dy in range(8))
    assert not any(cv.pix(10 + dx, 100 + dy) == 7
                   for dx in range(8) for dy in range(8))


def _drag(ws, layer_pointer, x, y0, y1, steps=6):
    """Feed a held vertical drag through a layer's pointer handler."""
    ws.pointer.down = True
    layer_pointer(x, y0, False)
    for i in range(1, steps + 1):
        layer_pointer(x, y0 + (y1 - y0) * i // steps, False)
    ws.pointer.down = False
    layer_pointer(x, y1, False)


def test_cards_drag_scrolls(tmp_path):
    """A held drag on the Config card column scrolls mtop (one card per base
    card height of travel)."""
    from runtime import host_app
    ws = host_app.build_workstation(str(tmp_path / "carts"))
    target = None
    for i, it in enumerate(ws.launcher.items):
        if it.get("path") and it.get("edit"):
            ws.launcher.sel = i
            target = it
            break
    assert target is not None
    ws.change_selected()
    # Guarantee overflow: a long edit schema on the OPEN cart (the workspace
    # rehydrates from the store on open, so mutate after) -- the list scrolls.
    ws.cart["edit"] = [{"key": "k%d" % i, "label": "KNOB %d" % i,
                       "min": 0, "max": 9, "default": 1} for i in range(14)]
    cl = ws.cards_layer
    assert cl._cards_scrollable()
    lay = cl.layout
    x = lay.card_x + 10
    y0 = lay.view_bottom - 6
    _drag(ws, cl.handle_pointer, x, y0, y0 - 4 * (lay.card_h + lay.gap))
    assert cl.mtop > 0


def test_blocks_outline_drag_scrolls(tmp_path):
    """A held drag on the Blocks outline scrolls blk_top when rows overflow."""
    from runtime import host_app
    ws = host_app.build_workstation(str(tmp_path / "carts"))
    for i, it in enumerate(ws.launcher.items):
        if it.get("path"):
            ws.launcher.sel = i
            break
    ws.change_selected()
    ws.set_menu_view("blocks")
    bu = ws.block_ui
    be = bu.blocks_ed
    if be is None:
        import pytest
        pytest.skip("no block editor for this cart")
    lay = bu.block_layout
    # Guarantee overflow: pad the program past one screen of rows.
    while len(be.rows) <= lay.rows + 3:
        be.rows.append(be.rows[-1])
    x = lay.x0 + 4
    y0 = lay.y0 + lay.rows * lay.row_h - 4
    _drag(ws, bu._blocks_pointer, x, y0, y0 - 4 * lay.row_h)
    assert bu.blk_top > 0


def test_settings_rows_drag_scrolls(tmp_path):
    """The Settings list rides the shared DragTap machine: a held drag on the
    rows moves the scroll window (set_top stays the row-slot state of record).
    Drags anchor on the press EDGE, exactly as the hardware delivers them."""
    from runtime import host_app
    ws = host_app.build_workstation(str(tmp_path / "carts"))
    ws.open_settings()
    sl = ws.settings_layer
    rows = len(sl._settings_rows())
    vis = sl._settings_visible()
    assert rows > vis                      # the #53 OTA rows overflow one screen
    view = sl._scroll_region().view
    cx = view[0] + view[2] // 2
    cy = view[1] + view[3] - 4
    ws.pointer.down = True
    sl.handle_pointer(cx, cy, True)                        # press edge anchors
    sl.handle_pointer(cx, cy - 3 * ws.layout.set_row_h, False)   # pull up 3 rows
    assert sl.set_top > 0
    ws.pointer.down = False
    sl.handle_pointer(cx, cy, False)                       # release after a drag
    top = sl.set_top
    ws.pointer.down = True
    sl.handle_pointer(cx, view[1] + 4, True)               # new press anchor
    sl.handle_pointer(cx, view[1] + 4 + 10 * ws.layout.set_row_h, False)
    assert sl.set_top < top                                # drag down -> back up


def test_settings_rows_drag_accumulates_small_pointer_moves(tmp_path):
    """Real touch samples arrive a few pixels apart; their remainder must survive
    between events until it crosses a row boundary."""
    from runtime import host_app
    ws = host_app.build_workstation(str(tmp_path / "carts"))
    ws.open_settings()
    sl = ws.settings_layer
    view = sl._scroll_region().view
    cx = view[0] + view[2] // 2
    cy = view[1] + view[3] - 4
    ws.pointer.down = True
    sl.handle_pointer(cx, cy, True)                        # press edge anchors
    step = 5
    moves = ws.layout.set_row_h // step + 2
    for i in range(moves):
        sl.handle_pointer(cx, cy - (i + 1) * step, False)
    assert sl.set_top > 0


def test_settings_drag_release_never_clicks_a_row(tmp_path):
    """The bug the shared machine retires: scrolling the Settings rows and
    letting go must NOT activate the row under the finger (WIFI used to open,
    steppers used to step). A clean tap (press + release, no travel) still
    activates the row it landed on."""
    from runtime import host_app
    ws = host_app.build_workstation(str(tmp_path / "carts"))
    ws.open_settings()
    sl = ws.settings_layer
    view = sl._scroll_region().view
    cx = view[0] + view[2] // 2
    cy = view[1] + view[3] - 4
    ws.pointer.down = True
    sl.handle_pointer(cx, cy, True)                        # press on a row
    sl.handle_pointer(cx, cy - 3 * ws.layout.set_row_h, False)  # drag = scroll
    assert sl.set_top > 0
    ws.pointer.down = False
    sl.handle_pointer(cx, cy - 3 * ws.layout.set_row_h, False)  # let go
    assert not sl.wifi_view                # no row fired on the release
    assert ws.screen == "settings"
    # A clean tap (press + release, no travel) still selects the row it landed
    # on -- activation just moved from the press edge to the release.
    sl.set_top = 0
    sl.set_msel = 0
    idx = 1                                # WALLPAPER (row 0 is WIFI -- avoid it)
    assert sl._settings_row_visible(idx)
    x, y, w, h = sl._settings_row_rect(idx)
    ws.pointer.down = True
    sl.handle_pointer(x + w // 2, y + h // 2, True)        # press...
    ws.pointer.down = False
    sl.handle_pointer(x + w // 2, y + h // 2, False)       # ...clean release
    assert sl.set_msel == idx              # the tap landed (release-fired)


# -- ScrollRegion ------------------------------------------------------------------

def test_scroll_region_clamps_and_shows():
    sr = ui.ScrollRegion()
    sr.set((0, 0, 100, 50), 200)
    sr.scroll_by(-10)
    assert sr.offset == 0                              # clamped at the top
    sr.scroll_by(500)
    assert sr.offset == 150                            # content - view extent
    sr.scroll_to_show(0, 10)
    assert sr.offset == 0
    sr.scroll_to_show(120, 10)
    assert sr.offset == 80                             # bottom edge visible
    sr.set((0, 0, 100, 50), 30)                        # fits -> no bar, offset 0
    assert sr.offset == 0
    assert sr.bar_rect() is None


def test_scroll_region_drag_and_bar():
    sr = ui.ScrollRegion()
    sr.set((0, 0, 100, 50), 200)
    sr.drag_start(40)
    assert sr.drag_move(30)                            # finger up -> content down
    assert sr.offset == 10
    sr.drag_end()
    assert not sr.drag_move(10)                        # ended drag is inert
    bar = sr.bar_rect()
    assert bar is not None
    x, y, w, h = bar
    assert x + w <= 100 and h >= 8
    cv = _cv()
    sr.draw_bar(cv, TH)                                # draws without error


def test_scroll_region_horizontal_axis():
    """The Library shelf's axis: offsets clamp against the view WIDTH, drags
    track x, and the bar lies along the bottom edge."""
    sr = ui.ScrollRegion(horizontal=True)
    sr.set((10, 20, 100, 50), 300)
    sr.scroll_by(999)
    assert sr.offset == 200                            # content - view width
    sr.drag_start(80)
    sr.drag_move(60)                                   # finger left -> content right
    assert sr.offset == 200                            # already at the far end
    sr.offset = 0
    sr.drag_start(60)
    sr.drag_move(35)
    assert sr.offset == 25
    sr.drag_end()
    bar = sr.bar_rect()
    assert bar is not None
    x, y, w, h = bar
    assert y + h == 20 + 50                            # bottom edge
    assert w >= 8 and h == ui.ScrollRegion.BAR_W


def test_drag_tap_disambiguates():
    """ui.DragTap: a clean press+release fires the tap; travel past the slop
    scrolls the region instead and the release is inert."""
    sr = ui.ScrollRegion()
    sr.set((0, 0, 100, 100), 400)
    dt = ui.DragTap(sr)
    # Clean tap: press, hold without travel, release -> the press point.
    assert dt.frame(50, 50, True, True) is None
    assert dt.frame(51, 50, False, True) is None
    assert dt.frame(51, 50, False, False) == (50, 50)
    assert sr.offset == 0
    # Drag: press, travel past the slop, release -> None, offset moved.
    assert dt.frame(50, 90, True, True) is None
    assert dt.frame(50, 60, False, True) is None
    assert dt.dragging
    assert dt.frame(50, 60, False, False) is None
    assert sr.offset == 30
    # A press OUTSIDE the view arms nothing.
    assert dt.frame(200, 200, True, True) is None
    assert dt.frame(200, 200, False, False) is None
