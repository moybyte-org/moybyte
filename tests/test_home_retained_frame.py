"""The retained home-frame stamp (launcher_layer, 2026-07-27): a repeat home
visit whose pixels are provably unchanged presents the captured frame in one
blit instead of rebuilding wallpaper + panel + cards + bar (~150ms on P4 glass
-- the last remaining transition frame after the cover warm-up).

Pins:
  * a re-entry with an unchanged _retained_key does NOT re-run the grid draw;
  * the stamped frame is PIXEL-IDENTICAL to a live render of the same state;
  * selection / title changes invalidate (the silent-cache §2.1 net);
  * the paint-continuity guard: foreign paints since the last home draw reset
    the drag-partial streak (the retained ping-pong buffers are foreign).
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _ws(tmp_path):
    from runtime import host_app
    ws = host_app.build_workstation(str(tmp_path / "carts"))
    ws.launcher.sel = next(i for i, it in enumerate(ws.launcher.items)
                           if it.get("path"))
    return ws


def _drv(ws):
    from runtime import host_app
    return host_app.ConsoleDriver(ws)


def _quiesce(ws):
    ws.pointer.visible = False
    ws.ach.toast = None
    ws.ach.toast_until = 0


def _count_grid(ws):
    calls = [0]
    orig = ws.launcher.draw
    ws.launcher.draw = lambda cv, sf=None: (calls.__setitem__(0, calls[0] + 1),
                                            orig(cv, sf))[1]
    return calls


def _settle_home(tmp_path):
    """Boot to the home shelf, one settled paint captured."""
    ws = _ws(tmp_path)
    drv = _drv(ws)
    drv.frame(0.0)
    _quiesce(ws)
    ws._dirty = True
    drv.frame(0.0)
    assert ws.launcher_layer._lib_key is not None    # capture landed
    return ws, drv


def test_reentry_stamps_instead_of_redrawing(tmp_path):
    ws, drv = _settle_home(tmp_path)
    ws.open_settings()
    drv.frame(0.0)
    calls = _count_grid(ws)
    ws.go_home()
    _quiesce(ws)          # navigation can fire an achievement toast; an active
    drv.frame(0.0)        # toast reads as _animating and refuses the stamp
    assert ws.wm.top_kind() == "launcher"
    assert calls[0] == 0                             # stamped, not re-rendered


def test_stamped_frame_matches_live_render(tmp_path):
    ws, drv = _settle_home(tmp_path)
    ws.open_settings()
    drv.frame(0.0)
    ws.go_home()
    _quiesce(ws)
    drv.frame(0.0)                                   # the stamped re-entry
    stamped = bytes(ws.sys_canvas._buf)
    ws.launcher_layer._lib_key = None                # force the live path
    ws._dirty = True
    drv.frame(0.0)
    live = bytes(ws.sys_canvas._buf)
    assert stamped == live


def test_selection_change_invalidates(tmp_path):
    ws, drv = _settle_home(tmp_path)
    ws.open_settings()
    drv.frame(0.0)
    ws.go_home()
    _quiesce(ws)
    drv.frame(0.0)
    calls = _count_grid(ws)
    nxt = next(i for i, it in enumerate(ws.launcher.items)
               if it.get("path") and i != ws.launcher.sel)
    ws.launcher.sel = nxt
    ws._dirty = True
    drv.frame(0.0)
    assert calls[0] == 1                             # live redraw, ring + all


def test_title_change_invalidates(tmp_path):
    """A rename with an unchanged item COUNT must still repaint -- the key
    carries per-item titles precisely because len() alone cannot see it."""
    ws, drv = _settle_home(tmp_path)
    it = next(x for x in ws.launcher.items if x.get("path"))
    it["title"] = "RENAMED"
    calls = _count_grid(ws)
    ws._dirty = True
    drv.frame(0.0)
    assert calls[0] == 1


def test_foreign_paints_reset_the_partial_streak(tmp_path):
    """After a visit elsewhere the retained ping-pong buffers hold the OTHER
    screen -- the drag partial's streak must re-arm from zero (latent on the
    device root before this; masked while transitions painted many frames)."""
    ws, drv = _settle_home(tmp_path)
    ws._dirty = True
    drv.frame(0.0)                                   # second consecutive paint
    assert ws.launcher_layer._full_streak == 2
    ws.open_settings()
    drv.frame(0.0)                                   # a foreign paint
    ws.go_home()
    _quiesce(ws)
    drv.frame(0.0)                                   # re-entry paint no. 1
    assert ws.launcher_layer._full_streak == 1       # NOT the inherited 2
    ws._dirty = True
    drv.frame(0.0)                                   # consecutive paint no. 2
    assert ws.launcher_layer._full_streak == 2
