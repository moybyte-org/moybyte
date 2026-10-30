"""The shell's half of the rotated compositor's rects (docs/shell_layout_2026-10.md
§13 step 2): the bar strips it presents are a rect list the shell publishes on
relayout, each repaint that changes a strip's pixels says so, and the
fullscreen home's drag partial hands its band as the frame's damage.

The host has no rotated compositor, so a recording root stands in: it takes
`set_strips` / `strip_moved` / `note_damage` the way device/p4_canvas.py binds
them from the compositor. The compositor's own side -- the list, the moved
bits, the budget -- is pinned in tests/test_p4_display.py.
"""

from ws_helpers import build_desktop_ws, build_ws_with_shelf, make_drv, quiesce


class _Root:
    """Records what the shell hands a rotated root."""

    def __init__(self, cv):
        self.strips = []
        self.moved = []
        self.damage = []
        cv.set_strips = self.strips.append
        cv.strip_moved = lambda *r: self.moved.append(r)
        cv.note_damage = lambda *r: self.damage.append(r)


def _published(ws):
    root = _Root(ws.sys_canvas)
    ws.bar_layer.publish_strips(ws.sys_canvas)
    return root


def _bar_h(ws):
    return max(ws.layout.status_h, ws.bar_layer._bar_h("desktop"))


def test_the_strips_are_the_tallest_bar_and_are_republished_on_relayout(tmp_path):
    ws = build_desktop_ws(tmp_path)
    root = _published(ws)
    assert root.strips == [[(0, 0, ws.sys_canvas.w, _bar_h(ws))]]
    ws.look.set_font_scale(1, persist=False)
    assert len(root.strips) == 2, "the relayout publishes again"
    assert root.strips[-1] == [(0, 0, ws.sys_canvas.w, _bar_h(ws))]


def test_a_root_without_the_verbs_is_left_alone(tmp_path):
    ws = build_desktop_ws(tmp_path)
    ws.bar_layer.publish_strips(ws.sys_canvas)      # no set_strips: nothing
    ws._relayout()
    drv = make_drv(ws)
    drv.frame(0.0)


def test_a_bar_render_moves_the_strip_and_a_steady_blit_does_not(tmp_path):
    ws = build_ws_with_shelf(tmp_path, 4)
    ws.bar_layer._clock_text = lambda: "00:00"
    root = _published(ws)
    drv = make_drv(ws)
    drv.frame(0.0)
    assert (0, 0, ws.sys_canvas.w, ws.layout.status_h) in root.moved
    for _ in range(3):
        quiesce(ws)
        ws.mark_dirty()
        drv.frame(0.0)
    root.moved.clear()
    ws.mark_dirty()
    drv.frame(0.0)                                  # the same bar, blitted again
    assert root.moved == []
    ws.bar_layer._clock_text = lambda: "00:01"
    ws.bar_layer._bar_cache_gen += 1                # the minute is in the key
    ws.mark_dirty()
    drv.frame(0.0)
    assert root.moved == [(0, 0, ws.sys_canvas.w, ws.layout.status_h)]


def test_the_desk_clock_cell_moves_the_strip_only_when_its_text_changes(tmp_path):
    ws = build_desktop_ws(tmp_path)
    root = _published(ws)
    bl = ws.bar_layer
    bl._clock_text = lambda: "12:00"
    bl.redraw_clock("desk")
    assert len(root.moved) == 1
    bl.redraw_clock("desk")
    assert len(root.moved) == 1, "the same text repainted: no new pixels"
    bl._clock_text = lambda: "12:01"
    bl.redraw_clock("desk")
    assert len(root.moved) == 2
    x, y, w, h = ws.layout.clock_hit()
    assert root.moved[-1] == (x, y, w, h)


def test_a_chip_change_on_the_os_bar_moves_the_strip(tmp_path):
    ws = build_desktop_ws(tmp_path)
    drv = make_drv(ws)
    ws.open_settings()
    drv.frame(0.0)
    root = _published(ws)
    quiesce(ws)
    ws.mark_dirty()
    drv.frame(0.0)
    root.moved.clear()
    ws.mark_dirty()
    drv.frame(0.0)
    assert root.moved == [], "the same chips: nothing moved"
    wm = ws.wm
    wm._chip_tap(wm._focus)                         # minimise: the chip dims
    drv.frame(0.0)
    assert (0, 0, ws.sys_canvas.w, ws.layout.status_h) in root.moved


def _drag_home(ws, drv, root):
    """Settle the home, then drag its shelf until the partial path runs; the
    damage noted on each drag frame, in order."""
    for _ in range(80):
        drv.frame(1 / 30)
    quiesce(ws)
    ws.mark_dirty()
    drv.frame(1 / 30)
    assert root.damage == [], "a full home paint describes nothing"
    gx, gy, gw, gh = ws.layout.lib_grid
    cx, cy = gx + gw - 10, gy + gh // 2
    drv.touch(cx, cy)
    drv.frame(1 / 30)
    ws.pointer.visible = False                      # a finger: no cursor sprite
    noted = []
    for step in range(1, 10):
        root.damage.clear()
        drv.touch_drag(cx - step * 9, cy)
        drv.frame(1 / 30)
        noted.append(list(root.damage))
    assert ws.launcher.dragging
    return noted


def test_the_fullscreen_home_hands_its_band_on_a_band_frame(tmp_path):
    ws = build_ws_with_shelf(tmp_path, 14)
    root = _Root(ws.sys_canvas)
    drv = make_drv(ws)
    noted = _drag_home(ws, drv, root)
    band = ws.launcher.band_rect()
    assert noted[-1] == [band], "the band frame's band is the frame's damage"


def test_the_console_mode_home_hands_its_band_too(tmp_path):
    ws = build_desktop_ws(tmp_path)
    ws.wm.enter_console()
    assert ws.wm.mode == "console" and ws.screen == "launcher"
    while len(ws.launcher.items) < 30:
        ws.launcher.items.append(dict(ws.launcher.items[-1]))
    root = _Root(ws.sys_canvas)
    drv = make_drv(ws)
    noted = _drag_home(ws, drv, root)
    assert noted[-1] == [ws.launcher.band_rect()]


def test_an_overlay_or_a_cursor_leaves_the_frame_undescribed(tmp_path):
    ws = build_ws_with_shelf(tmp_path, 14)
    root = _Root(ws.sys_canvas)
    quiesce(ws)
    wm = ws.wm
    cv = ws.sys_canvas
    wm.draw_stack()
    assert wm._cache_sig == 0
    wm.hand_content_damage(cv, 1, 2, 3, 4)
    assert root.damage == [(1, 2, 3, 4)]
    ws.pointer.visible = True
    wm.hand_content_damage(cv, 1, 2, 3, 4)
    ws.pointer.visible = False
    ws._deferred.append((lambda: None, True, True))  # a LOADING pill is owed
    wm.hand_content_damage(cv, 1, 2, 3, 4)
    ws._deferred.clear()
    wm._cache_sig = 32                              # a toast is on the stack
    wm.hand_content_damage(cv, 1, 2, 3, 4)
    assert root.damage == [(1, 2, 3, 4)]


def test_desktop_mode_content_draws_in_window_coordinates_and_hands_nothing(tmp_path):
    ws = build_desktop_ws(tmp_path)
    root = _Root(ws.sys_canvas)
    wm = ws.wm
    assert wm.mode == "desktop"
    wm.draw_stack()
    wm.hand_content_damage(ws.sys_canvas, 1, 2, 3, 4)
    assert root.damage == []


def test_the_p4_root_binds_the_compositors_strip_verbs():
    """device/p4_canvas.py hands a rotated compositor's strip verbs to the
    root the bar publishes on, as it hands note_damage; a compositor without
    them leaves the root without them."""
    import importlib.util as ilu
    from pathlib import Path
    from runtime import host_canvas
    host_canvas.install()
    spec = ilu.spec_from_file_location(
        "p4_canvas_strips", Path(__file__).resolve().parent.parent / "device" / "p4_canvas.py")
    mod = ilu.module_from_spec(spec)
    spec.loader.exec_module(mod)
    comp = host_canvas.HostCompositor(320, 200)
    got = []
    comp.note_damage = lambda *r: got.append(("damage",) + r)
    comp.set_strips = lambda rs: got.append(("strips", rs))
    comp.strip_moved = lambda *r: got.append(("moved",) + r)
    cv = mod.P4SystemCanvas(comp, font_scale=1)
    cv.set_strips([(0, 0, 320, 18)])
    cv.strip_moved(0, 0, 10, 10)
    assert got == [("strips", [(0, 0, 320, 18)]), ("moved", 0, 0, 10, 10)]
    plain = mod.P4SystemCanvas(host_canvas.HostCompositor(320, 200), font_scale=1)
    assert getattr(plain, "set_strips", None) is None
    assert getattr(plain, "strip_moved", None) is None
