"""The shown ring (docs/surface_model_v1.md §14.3): what each physical buffer
of the root holds of the focused window's content, and the protocol a
content speaks to use it -- Paint, on a desk window, on one, two and three
buffers presented in turn as a board's framebuffers are.

Each case runs a scripted stroke twice: once as it ships, and once with
every partial path off (Paint paints whole frames, the WM stamps the whole
window), and holds every presented frame of the first to the second's
bytes. A pointer stroke goes through direct render, the way a P4 desk takes
it (no cursor, a still wallpaper); a held-key stroke goes through the
window's buffer and its stamp."""

import pytest

from runtime import host_app
from ws_helpers import quiesce


def _desk(tmp_path, monkeypatch, bufs, ref, full=False):
    from runtime import paint_app, wm_windowed
    if ref:
        monkeypatch.setattr(paint_app, "_loop", None)
        monkeypatch.setattr(paint_app.PaintAppLayer, "HELD", False)
        monkeypatch.setattr(wm_windowed.WindowedWM, "_shown_chain", raising=False, value=
                            lambda self, win, f, c: False)
    ws = host_app.build_workstation(str(tmp_path / "carts"), sys_size=(1024, 600),
                                    font_scale=2, windowed=True, panel_diagonal_in=7.0)
    ws.open_desk()
    ws.wallpaper.is_animating = lambda dt: False
    assert ws.open_app(ws.artwork_app)
    ws.input.begin_frame()
    ws.frame(1 / 30)
    if full:
        assert ws.wm.full_key()       # full screen: the WM's row over the app's band
        ws.frame(1 / 30)
    quiesce(ws)
    app = ws.artwork_app
    drv = host_app.ConsoleDriver(ws)
    root = ws.wm._root_canvas
    root.RETAINED_FRAMES = bufs
    held = []

    def frame():
        n = ws._frames_drawn
        drv.frame(1 / 30)
        if ws._frames_drawn == n:
            return False
        if bufs > 1:
            held.append(bytes(root._buf))
            root._buf[:] = held[-bufs] if len(held) >= bufs else held[0]
        return True
    held.append(bytes(root._buf))
    app.tool = paint_app.BRUSH
    app.size_i = 0
    for _ in range(3 + bufs):
        ws._dirty = True
        frame()
    win = ws.wm._wins["artwork"]
    was = app.layout
    app.layout = win.ctx.artwork_layout
    x, y, w, h = app._spec()[:4]
    app.layout = was
    ox, oy = win.content_rect()[:2]
    top = oy * root.w * 2

    def shot():
        """The glass: the buffer last presented."""
        return bytes((held[-1] if bufs > 1 else root._buf)[top:])
    return ws, app, drv, root, win, frame, shot, (ox + x, oy + y, w, h)


def _spy(monkeypatch, ws, app, root, win):
    """Count, from here on: Paint's stroke frames and whole ones, the WM's
    direct renders, and the bytes its stamps of the window copied."""
    from runtime import wm_windowed
    got = {"frames": [], "direct": 0, "stamped": 0}
    real = app._stroke_frame

    def counted(cv, th):
        r = real(cv, th)
        got["frames"].append(r)
        return r
    app._stroke_frame = counted
    rd = ws.wm._direct_render

    def direct(w, dt):
        r = rd(w, dt)
        got["direct"] += 1 if r else 0
        return r
    ws.wm._direct_render = direct
    bs = root.blit_strip
    bsr = root.blit_strip_rect

    def strip(layer, x=0, y=0):
        if layer is win.buf:
            got["stamped"] += layer.w * layer.h
        return bs(layer, x, y)

    def strip_rect(layer, dx, dy, rx, ry, rw, rh):
        if layer is win.buf:
            got["stamped"] += rw * rh
        return bsr(layer, dx, dy, rx, ry, rw, rh)
    monkeypatch.setattr(root, "blit_strip", strip)
    monkeypatch.setattr(root, "blit_strip_rect", strip_rect)
    assert wm_windowed  # the module the ring lives in
    return got


def _pointer_stroke(drv, frame, shot, view, n, dy=0):
    vx, vy, vw, vh = view
    pts = [(vx + 60 + i * 9, vy + 50 + dy + (i * 5) % (vh - 100)) for i in range(n)]
    shots = []
    drv.touch(*pts[0])
    frame()
    shots.append(shot())
    for p in pts[1:]:
        drv.touch_drag(*p)
        frame()
        shots.append(shot())
    drv.touch_up()
    frame()
    shots.append(shot())
    return shots


def _key_stroke(drv, frame, shot, n):
    shots = []
    drv.hold("a", True)
    drv.hold("right", True)
    for i in range(n):
        drv.hold("down", i % 3 == 0)
        frame()
        shots.append(shot())
    drv.hold("down", False)
    drv.hold("right", False)
    drv.hold("a", False)
    frame()
    shots.append(shot())
    return shots


def _run(tmp_path, monkeypatch, bufs, keys, ref, full=False):
    ws, app, drv, root, win, frame, shot, view = _desk(tmp_path, monkeypatch, bufs, ref, full)
    # A first stroke leaves the drawing unsaved and with history, so the
    # stroke measured changes nothing but marks from its press to its release.
    _pointer_stroke(drv, frame, shot, view, 6, dy=120)
    for _ in range(6):
        frame()
    app.cur[0] = app.doc.W // 4
    app.cur[1] = app.doc.H // 4
    got = _spy(monkeypatch, ws, app, root, win)
    shots = _key_stroke(drv, frame, shot, 30) if keys else \
        _pointer_stroke(drv, frame, shot, view, 30)
    return shots, got, bytes(app.doc.pix), win


@pytest.mark.parametrize("bufs", [1, 2, 3])
@pytest.mark.parametrize("keys", [False, True], ids=["pointer", "keys"])
@pytest.mark.parametrize("full", [False, True], ids=["window", "full"])
def test_a_desk_stroke_is_stroke_frames_from_press_to_release(tmp_path, monkeypatch,
                                                              bufs, keys, full):
    """On a desk window, framed or full screen under the WM's row, every
    frame of a stroke -- its press, its release and every frame between -- is
    a stroke frame on every buffer count, and each presented frame is the one
    full repaints present. The pointer's frames are direct renders; the keys'
    stamp only what the stroke changed."""
    whole, _, pix0, _ = _run(tmp_path / "a", monkeypatch, bufs, keys, True, full)
    monkeypatch.undo()
    part, got, pix1, win = _run(tmp_path / "b", monkeypatch, bufs, keys, False, full)
    assert (win.saved is not None) == full
    assert pix0 == pix1
    assert len(whole) == len(part)
    for i, (a, b) in enumerate(zip(whole, part)):
        assert a == b, "the glass after frame %d differs from a full repaint's" % i
    frames = got["frames"]
    assert frames and all(frames), "whole frames in the stroke: %s" % frames
    area = win.buf.w * win.buf.h
    if keys:
        assert got["direct"] == 0
        # each frame stamps what the last few steps changed, never the window
        assert got["stamped"] < area * len(frames) // 20, (got["stamped"], area)
    else:
        assert got["direct"] >= len(frames) - 2, got
        # the release's stamp brings the window's buffer back: what the stroke
        # changed, never the window once a frame
        assert got["stamped"] < area, (got["stamped"], area)


@pytest.mark.parametrize("bufs", [2, 3])
def test_a_whole_frame_settles_every_buffer_before_the_next_press(tmp_path, monkeypatch,
                                                                  bufs):
    """A whole frame of a window's content (a brush size picked) leaves the root's
    other buffers without its page. The WM asks for the frames that bring
    each of them to it while the board is idle, so the next press is a
    stroke frame with nothing stamped under it."""
    ws, app, drv, root, win, frame, shot, view = _desk(tmp_path, monkeypatch, bufs, False)
    _pointer_stroke(drv, frame, shot, view, 6, dy=120)
    for _ in range(4):
        frame()
    app.size_i = 1
    app._damage.all()
    painted = sum(1 for _ in range(8) if frame())
    # the page's frames, then the desk's own debt (`debt_owed`): each a
    # frame that changes nothing, and then the board rests
    assert bufs <= painted <= 2 * bufs - 1, "the idle board painted %d frames" % painted
    assert not frame()
    got = _spy(monkeypatch, ws, app, root, win)
    _pointer_stroke(drv, frame, shot, view, 4)
    assert all(got["frames"]), got["frames"]
    assert got["stamped"] < win.buf.w * win.buf.h, got


def test_a_content_that_says_nothing_is_stamped_whole(tmp_path, monkeypatch):
    """A window whose content does not speak the protocol keeps the whole
    stamp on every frame it renders."""
    ws, app, drv, root, win, frame, shot, view = _desk(tmp_path, monkeypatch, 2, False)
    monkeypatch.setattr(type(app), "HELD", False)
    got = _spy(monkeypatch, ws, app, root, win)
    n = ws._frames_drawn
    _key_stroke(drv, frame, shot, 6)
    painted = ws._frames_drawn - n
    assert painted and got["stamped"] >= win.buf.w * win.buf.h * painted
