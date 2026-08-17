"""Settings list scrolls to keep the selection visible (#53 added enough rows --
UPDATE FW / CHANNEL / UPDATE ONLINE -- that the panel overflows). Before the fix the
bottom rows were simply unreachable. The base set itself is 7 rows since #68 added
PERF DIAG (vs 6 visible at fs=1), so even the host without an OTA updater scrolls
by one; the availability flags force the 10-row device case on top of that.
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _ws(tmp_path):
    from runtime import host_app
    ws = host_app.build_workstation(str(tmp_path / "carts"))
    ws.open_settings()
    return ws


def _force_ota_rows(ws):
    # No real updater on the host; flip the cached availability flags so _settings_rows
    # appends UPDATE FW + CHANNEL + UPDATE ONLINE (the device's overflowing 9-row case).
    ws._updater_ok = True
    ws._online_ok = True


def test_host_base_rows_all_reachable(tmp_path):
    # The base set (7 rows since #68's PERF DIAG) may exceed one screen; the #53
    # scroll machinery must keep every row reachable, top and bottom.
    ws = _ws(tmp_path)
    rows = ws.settings_layer._settings_rows()
    ws.settings_layer.set_msel = len(rows) - 1
    ws.settings_layer._settings_scroll()
    assert ws.settings_layer._settings_row_visible(len(rows) - 1)      # bottom row scrolls into view
    ws.settings_layer.set_msel = 0
    ws.settings_layer._settings_scroll()
    assert ws.settings_layer.set_top == 0                              # ... and back to the top
    assert ws.settings_layer._settings_row_visible(0)


def test_overflow_scrolls_selection_into_view(tmp_path):
    ws = _ws(tmp_path)
    _force_ota_rows(ws)
    rows = ws.settings_layer._settings_rows()
    vis = ws.settings_layer._settings_visible()
    last = len(rows) - 1
    assert last >= vis                                  # genuinely overflows the panel
    assert not ws.settings_layer._settings_row_visible(last)           # bottom rows start hidden
    # Selecting the last row scrolls it into view, and the top row scrolls off.
    ws.settings_layer.set_msel = last
    ws.settings_layer._settings_scroll()
    assert ws.settings_layer._settings_row_visible(last)
    assert ws.settings_layer.set_top == len(rows) - vis
    assert not ws.settings_layer._settings_row_visible(0)
    # Back to the top scrolls back.
    ws.settings_layer.set_msel = 0
    ws.settings_layer._settings_scroll()
    assert ws.settings_layer.set_top == 0
    assert ws.settings_layer._settings_row_visible(0)


def test_offscreen_rows_are_not_tappable(tmp_path):
    """A row scrolled out of the window must not be hit by a tap (the pointer loop
    skips non-visible rows), so its computed rect can't steal a tap."""
    ws = _ws(tmp_path)
    _force_ota_rows(ws)
    ws.settings_layer.set_msel = len(ws.settings_layer._settings_rows()) - 1
    ws.settings_layer._settings_scroll()
    assert ws.settings_layer.set_top > 0
    # row 0 is now above the window -> not visible -> excluded from hit-testing
    assert not ws.settings_layer._settings_row_visible(0)


def _feed(ws, drv, x, y, *, press=False, down=True):
    """One pointer sample through the REAL console path (the device loop's
    handle_pointer + frame), so these exercise the same routing the glass does."""
    if press:
        drv.touch(x, y)
    elif down:
        drv.touch_drag(x, y)
    else:
        ws.pointer.place(int(x), int(y))
        drv.touch_up()
    drv.frame(1 / 30)


def _drag_rows(ws, drv, x, y0, dy, steps=12):
    """Press at (x, y0), drag by dy over `steps` samples, release at the end."""
    _feed(ws, drv, x, y0, press=True)
    for i in range(1, steps + 1):
        _feed(ws, drv, x, y0 + dy * i // steps)
    _feed(ws, drv, x, y0 + dy, down=False)


def test_touch_drag_scrolls_the_rows(tmp_path):
    """A finger drag up scrolls the list (the on-glass P4 report: the rows
    would not move at all). The per-frame keep-selection-visible clamp used to
    yank set_top back to the selected row on EVERY frame, so a drag could never
    accumulate: set_top went 0 -> (drag) -> 0."""
    from runtime import host_app
    ws = _ws(tmp_path)
    _force_ota_rows(ws)
    drv = host_app.ConsoleDriver(ws)
    drv.frame(1 / 30)
    lay = ws.layout
    row_h = lay.set_row_h
    x = lay.set_x + lay.set_w // 2
    _drag_rows(ws, drv, x, lay.set_row_y0 + 4 * row_h, -3 * row_h)
    assert ws.settings_layer.set_top > 0


def test_scroll_position_survives_the_release(tmp_path):
    """Letting go must leave the list where the finger put it -- the second
    half of the on-glass report ('when i drag and let go i get thrown at the
    start')."""
    from runtime import host_app
    ws = _ws(tmp_path)
    _force_ota_rows(ws)
    drv = host_app.ConsoleDriver(ws)
    drv.frame(1 / 30)
    lay = ws.layout
    row_h = lay.set_row_h
    x = lay.set_x + lay.set_w // 2
    _drag_rows(ws, drv, x, lay.set_row_y0 + 4 * row_h, -3 * row_h)
    top = ws.settings_layer.set_top
    assert top > 0
    for _ in range(20):                     # idle frames: nothing may re-snap it
        drv.frame(1 / 30)
    assert ws.settings_layer.set_top == top


def test_drag_carries_the_selection_into_view(tmp_path):
    """The highlighted row follows the scrolled view, so the next d-pad press
    doesn't yank the list back to where the selection was left behind."""
    from runtime import host_app
    ws = _ws(tmp_path)
    _force_ota_rows(ws)
    drv = host_app.ConsoleDriver(ws)
    drv.frame(1 / 30)
    lay = ws.layout
    row_h = lay.set_row_h
    x = lay.set_x + lay.set_w // 2
    _drag_rows(ws, drv, x, lay.set_row_y0 + 4 * row_h, -3 * row_h)
    sl = ws.settings_layer
    assert sl._settings_row_visible(sl.set_msel)
    top = sl.set_top
    drv.press("down")                       # a d-pad step must not jump the view
    drv.frame(1 / 30)
    assert abs(sl.set_top - top) <= 1


def test_windowed_settings_rows_scroll(tmp_path):
    """The same drag inside the P4/desktop tier's Settings WINDOW (window-local
    coords under the window's own layout context) -- the tier the bug was
    reported on."""
    from runtime import host_app
    ws = host_app.build_workstation(str(tmp_path / "carts"),
                                    sys_size=(1024, 600), font_scale=2,
                                    windowed=True)
    drv = host_app.ConsoleDriver(ws)
    ws.open_settings()
    drv.frame(1 / 30)
    _force_ota_rows(ws)
    win = ws.wm._wins["settings"]
    lay = win.ctx.layout                    # window-local geometry
    row_h = lay.set_row_h
    ox, oy = win.x + 1, win.y + 1 + win.title_h
    x = ox + lay.set_x + lay.set_w // 2
    y0 = oy + lay.set_row_y0 + 4 * row_h
    _drag_rows(ws, drv, x, y0, -3 * row_h)
    top = ws.settings_layer.set_top
    assert top > 0
    for _ in range(20):
        drv.frame(1 / 30)
    assert ws.settings_layer.set_top == top


def test_update_online_reachable_by_keyboard(tmp_path):
    """Walking the d-pad down reaches the formerly-off-screen UPDATE ONLINE row."""
    from runtime import host_app
    ws = _ws(tmp_path)
    _force_ota_rows(ws)
    rows = ws.settings_layer._settings_rows()
    assert "UPDATE ONLINE" in [r[1] for r in rows]      # the row that used to be cut off
    drv = host_app.ConsoleDriver(ws)
    last = len(rows) - 1
    for _ in range(last):
        drv.press("down")
        drv.frame(1 / 30)
        drv.frame(1 / 30)
    assert ws.settings_layer.set_msel == last
    assert ws.settings_layer._settings_row_visible(last)               # scrolled into view, reachable


# -- the row set is memoized (P4 allocation, 2026-07-26) ------------------------

def test_settings_rows_are_memoized(tmp_path):
    """_settings_rows must return the SAME tuple until a capability flips.

    It reads as a cheap accessor, so callers treat it as one: the draw loop asks
    once per ROW and the pointer/scroll paths ask again, ~15 times per frame, and
    each call used to rebuild the tuple through up to four concatenations. On P4
    glass that was ~2.3KB of the ~4.5KB a Settings frame allocated (measured with
    tools/p4_alloc.py). Identity is the assertion because it is what proves no
    rebuild happened -- equality would pass on a fresh copy."""
    ws = _ws(tmp_path)
    lay = ws.settings_layer
    first = lay._settings_rows()
    assert lay._settings_rows() is first
    assert lay._settings_rows() is first


def test_a_capability_appearing_rebuilds_the_rows(tmp_path):
    """...and the memo must not outlive the flags it was built from: an updater
    that becomes available mid-session has to show up."""
    ws = _ws(tmp_path)
    lay = ws.settings_layer
    before = lay._settings_rows()
    assert "UPDATE FW" not in [r[1] for r in before]
    _force_ota_rows(ws)
    after = lay._settings_rows()
    assert after is not before
    assert "UPDATE FW" in [r[1] for r in after]
    assert lay._settings_rows() is after          # ...and the new set caches too
