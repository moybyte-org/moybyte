"""Settings' rows page scrolls (docs/look_2026-10.md step 19): the public
UI API's page (runtime/ui.py) keeps the focused row in view as the keys move
it, a finger drag scrolls it and is no tap, and a clean tap activates the row
under it. The rows are the session's (`_settings_rows`, memoized on the
capabilities), the page its groups of them.
"""

import hashlib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_DT = 1 / 30


def _ws(tmp_path, **kw):
    from runtime import host_app
    ws = host_app.build_workstation(str(tmp_path / "carts"), **kw)
    ws.open_settings()
    return ws


def _force_ota_rows(ws):
    # No real updater on the host; flip the cached availability flags so
    # _settings_rows appends UPDATE FW + CHANNEL + UPDATE ONLINE.
    ws._updater_ok = True
    ws._online_ok = True


def _drv(ws):
    from runtime import host_app
    drv = host_app.ConsoleDriver(ws)
    drv.frame(_DT)
    drv.frame(_DT)
    return drv


def _band(ws):
    """A digest of the page's content rect, the bar's rows left out."""
    cv = ws.sys_canvas
    lay = ws.layout
    buf = bytes(cv._buf)
    row = 2 * cv.w
    return hashlib.sha1(buf[lay.cy * row:(lay.cy + lay.ch) * row]).hexdigest()


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
    drv.frame(_DT)


def _drag(ws, drv, x, y0, dy, steps=12):
    _feed(ws, drv, x, y0, press=True)
    for i in range(1, steps + 1):
        _feed(ws, drv, x, y0 + dy * i // steps)
    _feed(ws, drv, x, y0 + dy, down=False)


def test_every_row_is_reachable_by_keyboard(tmp_path):
    """Walking down reaches the page's last stop, scrolled into view, and
    walking back up reaches the first with the page back at its top."""
    ws = _ws(tmp_path)
    _force_ota_rows(ws)
    drv = _drv(ws)
    sl = ws.settings_layer
    top = _band(ws)
    assert sl._focused == "Wi-Fi"
    seen = [sl._focused]
    for _ in range(40):
        drv.press("down")
        drv.frame(_DT)
        drv.frame(_DT)
        if sl._focused == seen[-1]:
            break
        seen.append(sl._focused)
    assert "Update online" in seen, "the update rows below the fold are reached"
    assert seen[-1] == "Licences", "About's Licences link is the last stop"
    assert _band(ws) != top, "the page scrolled to keep focus in view"
    for _ in range(len(seen)):
        drv.press("up")
        drv.frame(_DT)
        drv.frame(_DT)
    assert sl._focused == "Wi-Fi"
    assert _band(ws) == top, "back at the top, the page is as it opened"


def test_set_msel_reads_and_moves_the_focused_row(tmp_path):
    ws = _ws(tmp_path)
    drv = _drv(ws)
    sl = ws.settings_layer
    rows = sl._settings_rows()
    i = [r[0] for r in rows].index("font_scale")
    sl.set_msel = i
    drv.frame(_DT)
    assert sl._focused == "Text size" and sl.set_msel == i


def test_a_drag_scrolls_the_page_and_is_no_tap(tmp_path):
    ws = _ws(tmp_path)
    _force_ota_rows(ws)
    drv = _drv(ws)
    sl = ws.settings_layer
    lay = ws.layout
    top = _band(ws)
    x = lay.cx + lay.cw // 2
    _drag(ws, drv, x, lay.cy + lay.ch * 2 // 3, -lay.ch // 2)
    assert _band(ws) != top, "the rows moved under the finger"
    assert not (sl.wifi_view or sl.bt_view or sl.crash_view or sl.confirm_key), \
        "letting go of a drag opened nothing"
    moved = _band(ws)
    for _ in range(10):
        drv.frame(_DT)
    assert _band(ws) == moved, "the page rests where the finger left it"
    assert sl.rows_flinging() is False


def test_a_tap_on_a_row_activates_it(tmp_path):
    ws = _ws(tmp_path)
    drv = _drv(ws)
    lay = ws.layout
    sl = ws.settings_layer
    # The first group's first row: under its caption, at the content's top.
    y = lay.cy + 19 * lay.fs + 10 * lay.fs + 8 * lay.fs
    _feed(ws, drv, lay.cx + lay.cw // 2, y, press=True)
    _feed(ws, drv, lay.cx + lay.cw // 2, y, down=False)
    drv.frame(_DT)
    assert sl.wifi_view, "the tapped Wi-Fi row opened its panel"


def test_the_windowed_page_scrolls(tmp_path):
    """The same drag inside the desk's Settings WINDOW (window-local coords
    under the window's own layout)."""
    ws = _ws(tmp_path, sys_size=(1024, 600), font_scale=2, windowed=True)
    _force_ota_rows(ws)
    drv = _drv(ws)
    win = ws.wm._wins["settings"]
    lay = win.ctx.layout
    ox, oy = win.x + 1, win.y + 1 + win.title_h
    sl = ws.settings_layer
    before = hashlib.sha1(bytes(ws.sys_canvas._buf)).hexdigest()
    _drag(ws, drv, ox + lay.cx + lay.cw // 2, oy + lay.cy + lay.ch * 2 // 3, -lay.ch // 2)
    after = hashlib.sha1(bytes(ws.sys_canvas._buf)).hexdigest()
    assert after != before
    assert not sl.wifi_view


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


def test_the_c6_row_is_capability_gated_and_opens_the_flow(tmp_path):
    """UPGRADE C6 RADIO (#7/#58) appears exactly where ws.c6_updater is
    injected (the P4) and nowhere else -- the same gating as BLUETOOTH/CRISP,
    so every other board's Settings rows and frozen pixels stay untouched."""
    ws = _ws(tmp_path)
    keys = [r[0] for r in ws.settings_layer._settings_rows()]
    assert "update_c6" not in keys, "no capability -> no row (goldens depend on it)"

    ws.c6_updater = object()
    keys = [r[0] for r in ws.settings_layer._settings_rows()]
    assert keys[-1] == "update_c6", "the row joins the update group at the bottom"

    opened = []
    ws.update_ui.open_update_c6 = lambda: opened.append(1)
    ws.settings_layer._activate_settings_action("update_c6")
    assert opened == [1]
