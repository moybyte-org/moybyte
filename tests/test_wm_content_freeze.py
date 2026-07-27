"""The focused-window CONTENT FREEZE (wm_windowed._content_static -- the first
slice of the surface-granularity damage model, docs/ui_damage_model_v1.md §5.0).

A painted frame that provably did not change the focused window's content skips
its re-render and presents the retained win.buf stamp instead (the map tab's
draw is ~70ms on P4 glass, the stamp ~14ms). "Provably" = nothing marked the UI
dirty, no pointer down/click this frame or last (the drag-driven handlers --
paint strokes, map pans, scrolls -- mutate content WITHOUT marking dirty and
rely on pointer-state arming), the buffer isn't stale, and the content isn't a
self-animating surface (music preview / bluetooth panel / update screen).

These pin:
  * a cursor-move-only paint does NOT re-render the focused content;
  * pixels of that frozen frame == a live re-render (the freeze is invisible);
  * dirty, pointer-down, and release-edge frames DO re-render;
  * a stale buffer (fresh build / _direct_render bypass) forces one refill;
  * the self-animating exclusions re-render on animation-armed frames.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _ws(tmp_path, **kw):
    from runtime import host_app
    kw.setdefault("sys_size", (1024, 600))
    kw.setdefault("font_scale", 2)
    kw.setdefault("windowed", True)
    ws = host_app.build_workstation(str(tmp_path / "carts"), **kw)
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


def _count_draws(ws, kind):
    """Wrap the content Layer's draw with a counter (instance-attr shadow)."""
    layer = ws._content_layers[kind]
    calls = [0]
    orig = layer.draw
    layer.draw = lambda dt: (calls.__setitem__(0, calls[0] + 1), orig(dt))[1]
    return calls


def _settled_settings(tmp_path):
    """Settings open in its window, one dirty paint settled, toast quiesced.
    Returns (ws, drv, win)."""
    ws = _ws(tmp_path)
    drv = _drv(ws)
    ws.open_settings()
    drv.frame(0.0)
    _quiesce(ws)
    ws._dirty = True
    drv.frame(0.0)                    # settle: buf filled, _buf_stale cleared
    return ws, drv, ws.wm._wins["settings"]


def test_cursor_move_skips_focused_content_render(tmp_path):
    ws, drv, win = _settled_settings(tmp_path)
    assert win._buf_stale is False
    calls = _count_draws(ws, "settings")
    drawn = ws._frames_drawn
    drv.pan(1, 0)                     # trackball nudge: visible cursor moves
    drv.frame(0.0)
    drv.pan(0, 0)
    assert ws._frames_drawn == drawn + 1   # the frame DID paint (not gated off)
    assert calls[0] == 0                   # ...but the content did not re-render


def test_frozen_frame_matches_live_render(tmp_path):
    """The freeze is invisible: a cursor-armed frozen frame's pixels equal a
    forced live re-render of the same frame (same pointer position)."""
    ws, drv, _win = _settled_settings(tmp_path)
    drv.pan(1, 0)
    drv.frame(0.0)                    # frozen paint (content stamped from buf)
    drv.pan(0, 0)
    frozen = bytes(ws.sys_canvas.buf)
    ws._dirty = True
    drv.frame(0.0)                    # live re-render, identical pointer state
    live = bytes(ws.sys_canvas.buf)
    assert frozen == live


def test_dirty_frame_rerenders(tmp_path):
    ws, drv, _win = _settled_settings(tmp_path)
    calls = _count_draws(ws, "settings")
    ws.mark_dirty()
    drv.frame(0.0)
    assert calls[0] == 1


def test_pointer_down_and_release_edge_rerender(tmp_path):
    """Down frames and the release-edge frame after them must render live: the
    drag-driven handlers mutate content without marking dirty, and release
    handlers may consume p.click before the draw sees it -- only the NEXT
    position-only frame may freeze again."""
    ws, drv, win = _settled_settings(tmp_path)
    calls = _count_draws(ws, "settings")
    cx, cy, cw, ch = win.content_rect()
    drv.touch(cx + cw // 2, cy + ch - 4)   # press in the window's content
    drv.frame(0.0)
    down_calls = calls[0]
    assert down_calls >= 1                 # down frame rendered live
    drv.touch_up()
    drv.frame(0.0)                         # release edge: last frame was down
    assert calls[0] > down_calls
    _quiesce(ws)
    ws._dirty = True
    drv.frame(0.0)                         # settle whatever the tap changed
    after_settle = calls[0]
    drv.pan(1, 0)
    drv.frame(0.0)                         # position-only: freeze resumes
    drv.pan(0, 0)
    assert calls[0] == after_settle


def test_stale_buffer_forces_one_refill(tmp_path):
    """A buffer left behind the truth (fresh _build_content, or a gesture's
    _direct_render that painted past it) forces ONE live render on the next
    paint, then the freeze resumes."""
    ws, drv, win = _settled_settings(tmp_path)
    calls = _count_draws(ws, "settings")
    win._buf_stale = True
    drv.pan(1, 0)
    drv.frame(0.0)
    assert calls[0] == 1                   # refill render
    assert win._buf_stale is False
    drv.frame(0.0)                         # cursor still moving: frozen again
    drv.pan(0, 0)
    assert calls[0] == 1


def test_bluetooth_animation_excluded_from_freeze(tmp_path):
    """The settings window's async bluetooth states repaint themselves on
    animation-armed frames (nothing marks dirty) -- the freeze must not
    swallow them."""
    ws, drv, _win = _settled_settings(tmp_path)
    calls = _count_draws(ws, "settings")
    ws.settings_layer.bluetooth_animating = lambda: True
    drv.frame(0.0)                         # animation-armed, pointer idle
    drv.frame(0.0)
    assert calls[0] == 2


def test_music_preview_excluded_from_freeze(tmp_path):
    """A playing music preview animates the Editor window's content (playhead +
    PLAY/STOP) without marking dirty -- _content_static must exclude it."""
    ws = _ws(tmp_path)
    drv = _drv(ws)
    ws.open_picker()
    drv.frame(0.0)
    ws.picker.sel = next(i for i, it in enumerate(ws.picker.items)
                         if it.get("type") != "new")
    ws.pick_selected()
    drv.frame(0.0)
    ws.editor_app.set_tab("music")
    drv.frame(0.0)
    _quiesce(ws)
    ws._dirty = True
    drv.frame(0.0)                         # settle
    win = ws.wm._wins["make"]
    assert win.kind == "menu"
    assert ws.wm._content_static(win) is True
    ws.music_ui.music_preview = object()
    assert ws.wm._content_static(win) is False
    ws.music_ui.music_preview = None
    assert ws.wm._content_static(win) is True
