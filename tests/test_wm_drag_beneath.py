"""A window dragged over another: every presented frame shows the window
beneath exactly as a fresh render of its content draws it.

The window beneath is not re-rendered while another moves over it -- its
retained buffer is stamped (wm_windowed._draw_app_window) -- so whatever that
buffer missed when it was drawn stays missing for as long as the window sits
in the background. Calc's backspace key is `ui.icon("backspace")`, a mask of
the look's icon set, which the C engine draws only once the set is installed
(`iconsets.use_parts`); the look installs it as it publishes
(`Appearance.publish`), before anything draws under the new generation.

Each case starts as a fresh process does, with no icon set installed
(`iconsets.reset`), in one of two ways the window beneath was last drawn:

- `together`: it and Settings open in one navigation, so it is drawn once,
  as it is created behind Settings (`WindowedWM._prewarm`);
- `look`: the look switches while it sits under Settings, and it re-renders
  under the new one as a background window.
"""

import pytest

from runtime import iconsets
from ws_helpers import build_desktop_ws, make_drv, quiesce, use_theme

_APPS = ("calc", "files", "storybook", "artwork", "appearance", "getcarts")
# A window created behind another is drawn once, and these two apps' one
# render is not their settled one: Files' covers decode over the frames
# after it, and Appearance's hints follow the stop the render before focused.
_ONE_RENDER_UNSETTLED = ("files", "appearance")
_DRAGGED = "settings"
SETTLE = 4              # frames each window is focused before the next opens

_CASES = [(app, "look") for app in _APPS] + [
    (app, "together") for app in _APPS if app not in _ONE_RENDER_UNSETTLED]


def _desk(tmp_path, beneath, how):
    """The desk with `beneath` open under Settings: opened together, in one
    navigation (`together`), or each settled in turn and the look switched
    while `beneath` sits under Settings (`look`)."""
    iconsets.reset()
    ws = build_desktop_ws(tmp_path, font_scale=1, panel_diagonal_in=7.0)
    drv = make_drv(ws)
    if how == "look":
        use_theme(ws, "night", "dark")
    ws.open_desk()
    ws.open_app(ws._apps_by_id[beneath])
    if how == "look":
        _frames(ws, drv)
    ws.open_settings()
    _frames(ws, drv)
    if how == "look":
        ws.look.set_theme("moy", persist=False, variant="light")
        _frames(ws, drv)
    return ws, drv


def _frames(ws, drv, n=SETTLE):
    for _ in range(n):
        quiesce(ws)
        drv.frame(1 / 30)


def _uncovered(row_x0, row_x1, cover):
    """[x0, x1) runs of row_x0..row_x1 outside the box `cover` (x0, x1)."""
    cx0, cx1 = cover
    if cx1 <= row_x0 or cx0 >= row_x1:
        return [(row_x0, row_x1)]
    out = []
    if cx0 > row_x0:
        out.append((row_x0, cx0))
    if cx1 < row_x1:
        out.append((cx1, row_x1))
    return out


@pytest.mark.parametrize("beneath,how", _CASES)
def test_a_window_beneath_a_drag_shows_its_fresh_render(tmp_path, beneath, how):
    ws, drv = _desk(tmp_path, beneath, how)
    wm = ws.wm
    under, top = wm._wins[beneath], wm._wins[_DRAGGED]
    assert wm._order[-1] == _DRAGGED
    cv = ws.sys_canvas
    x_was = top.x
    sx, sy = top.x + top.w // 3, top.y + top.title_h // 2
    # Sweep the dragged window right and down across the one beneath, then
    # let go and idle: the frames the drag presents and the ones after.
    steps = [("touch", sx, sy)]
    steps += [("drag", sx + 12 * i, sy + 6 * i) for i in range(1, 31)]
    steps += [("up", 0, 0)] + [("idle", 0, 0)] * 2
    shown = []
    for op, x, y in steps:
        if op == "touch":
            drv.touch(x, y)
        elif op == "drag":
            drv.touch_drag(x, y)
        elif op == "up":
            drv.touch_up()
        drv.frame(1 / 30)
        ext = wm._win_extent(top)
        shown.append((bytes(cv._buf), ext))
    assert top.x == x_was + 12 * 30, "the drag moved the window all the way"
    # The truth: the window beneath rendered afresh, into its own buffer.
    wm._prewarm(under)
    ref = bytes(under.buf._buf)
    cx, cy, cw, ch = under.content_rect()
    w = cv.w
    for i, (frame, (ex, ey, ew, eh)) in enumerate(shown):
        bad = 0
        first = None
        for y in range(max(cy, 0), min(cy + ch, cv.h)):
            cover = (ex, ex + ew) if ey <= y < ey + eh else (0, 0)
            for x0, x1 in _uncovered(max(cx, 0), min(cx + cw, w), cover):
                a = frame[(y * w + x0) * 2:(y * w + x1) * 2]
                b = ref[((y - cy) * cw + x0 - cx) * 2:((y - cy) * cw + x1 - cx) * 2]
                if a != b:
                    for k in range(0, len(a), 2):
                        if a[k:k + 2] != b[k:k + 2]:
                            bad += 1
                            first = first or (x0 + k // 2, y)
        assert bad == 0, ("frame %d: %d pixels of %s differ from its fresh "
                          "render, the first at %s" % (i, bad, beneath, first))
