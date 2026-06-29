"""Settings list scrolls to keep the selection visible (#53 added enough rows --
UPDATE FW / CHANNEL / UPDATE ONLINE -- that the panel overflows). Before the fix the
bottom rows were simply unreachable. The host has no OTA updater (6 rows, which fit
exactly), so we flip the cached availability flags to force the 9-row case the device
actually hits.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


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


def test_host_six_rows_fit_without_scrolling(tmp_path):
    ws = _ws(tmp_path)
    rows = ws._settings_rows()
    assert len(rows) <= ws._settings_visible()          # the base set fits one screen
    ws.set_msel = len(rows) - 1
    ws._settings_scroll()
    assert ws.set_top == 0                              # never scrolls when it all fits
    assert all(ws._settings_row_visible(i) for i in range(len(rows)))


def test_overflow_scrolls_selection_into_view(tmp_path):
    ws = _ws(tmp_path)
    _force_ota_rows(ws)
    rows = ws._settings_rows()
    vis = ws._settings_visible()
    last = len(rows) - 1
    assert last >= vis                                  # genuinely overflows the panel
    assert not ws._settings_row_visible(last)           # bottom rows start hidden
    # Selecting the last row scrolls it into view, and the top row scrolls off.
    ws.set_msel = last
    ws._settings_scroll()
    assert ws._settings_row_visible(last)
    assert ws.set_top == len(rows) - vis
    assert not ws._settings_row_visible(0)
    # Back to the top scrolls back.
    ws.set_msel = 0
    ws._settings_scroll()
    assert ws.set_top == 0
    assert ws._settings_row_visible(0)


def test_offscreen_rows_are_not_tappable(tmp_path):
    """A row scrolled out of the window must not be hit by a tap (the pointer loop
    skips non-visible rows), so its computed rect can't steal a tap."""
    ws = _ws(tmp_path)
    _force_ota_rows(ws)
    ws.set_msel = len(ws._settings_rows()) - 1
    ws._settings_scroll()
    assert ws.set_top > 0
    # row 0 is now above the window -> not visible -> excluded from hit-testing
    assert not ws._settings_row_visible(0)


def test_update_online_reachable_by_keyboard(tmp_path):
    """Walking the d-pad down reaches the formerly-off-screen UPDATE ONLINE row."""
    from runtime import host_app
    ws = _ws(tmp_path)
    _force_ota_rows(ws)
    rows = ws._settings_rows()
    assert "UPDATE ONLINE" in [r[1] for r in rows]      # the row that used to be cut off
    drv = host_app.ConsoleDriver(ws)
    last = len(rows) - 1
    for _ in range(last):
        drv.press("down")
        drv.frame(1 / 30)
        drv.frame(1 / 30)
    assert ws.set_msel == last
    assert ws._settings_row_visible(last)               # scrolled into view, reachable
