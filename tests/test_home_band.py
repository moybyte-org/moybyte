"""The home's band frames (`launcher_layer._band_frame`): a drag, a fling,
a key, repeat or trackball step, a settle draw the band alone, its
arrangement's table of parts (`Launcher.band_pass`, `Carousel.band_pass`)
over the backdrop put back under it -- the plain ground (`bg`), a baked
backdrop (`bd`) or a picture -- and the parts outside the band the
selection changes (the hint strip, the icons' caption band) where the
buffer holds another selection's.

Each case runs a scripted session twice on one, two and three buffers
presented in turn, as a board's framebuffers are: once as it ships, and
once with every band path off (each frame a whole repaint), and holds the
first's glass, picture by picture, to the second's bytes."""

import pytest

from runtime import cover_cache, host_app, launcher_layer, moy_carts, ui_draw
from ws_helpers import cover_bytes, no_unlock_banner, quiesce


@pytest.fixture(autouse=True)
def _keys():
    was = ui_draw.input_kind()
    ui_draw.input_kind("keys")
    yield
    ui_draw.input_kind(was)


class _Key:
    """A frame's keys: `n` stepped `k` times (a press, or a held key's
    repeats and a roll's steps beside it)."""
    last_key = 0

    def __init__(self, n, k=1):
        self.n = n
        self.k = k

    def steps(self, n):
        return self.k if n == self.n else 0

    def pressed(self, n):
        return n == self.n


_TIERS = {
    "handheld": dict(sys_size=(320, 240), font_scale=1),
    "desk": dict(sys_size=(1024, 600), font_scale=1, windowed=True, panel_diagonal_in=7.0),
}


def _picture(ws, w, h):
    blob = moy_carts.encode_image(w, h, bytes((x * 7 + y * 3) % 60
                                              for y in range(h) for x in range(w)))
    moy_carts.save_copy(blob.encode() if isinstance(blob, str) else blob, ws.carts_root)
    assert ws.look.select_wallpaper(ws.look.PICTURE) == 0
    ws.look.tick(0.0)
    ws.look.tick(ws.look.AUTOSAVE_S)


def _home(tmp_path, monkeypatch, tier, arrangement, theme, bufs, ref, n=40, cursor=False,
          tab="mine"):
    L = launcher_layer.LauncherHomeLayer
    if ref:
        monkeypatch.setattr(L, "_try_still_frame", lambda self, cv, dt: False, raising=False)
        monkeypatch.setattr(L, "_try_band_frame", lambda self, cv, dt: False)
        monkeypatch.setattr(L, "_try_stamp_retained", lambda self, cv, dt: False)
    carts = str(tmp_path / "carts")
    ws = host_app.build_workstation(carts, **_TIERS[tier])
    for i in range(n):
        moy_carts.create("Extra %02d" % i, carts, src="def _draw():\n    cls(1)\n",
                         type="game")
    ws.carts.rescan()
    if theme == "picture":
        w, h = ws.sys_canvas.w, ws.sys_canvas.h
        _picture(ws, w, h)
    else:
        ws.look.set_theme(theme, persist=False)
        ws.look.select_wallpaper(None, persist=False)
    if tier == "desk":
        ws.open_library()
    else:
        ws.go_home()
    ws.set_home_arrangement(arrangement)
    ws.set_home_tab(tab)                   # Mine: the made carts, "+ New" first
    ws.launcher.sel = 0
    ws.launcher.scroll = 0
    ws.wallpaper.is_animating = lambda dt: False
    ws.bar_layer._clock_text = lambda: "10:30"
    no_unlock_banner(ws)
    quiesce(ws)
    if cursor:                             # the cursor over the band: the one overdraw
        bx, by, bw, bh = ws.launcher.band_rect()
        ws.pointer.visible = True
        ws.pointer.x, ws.pointer.y = bx + bw // 3, by + bh // 3
    drv = host_app.ConsoleDriver(ws)
    root = ws.wm._root_canvas if tier == "desk" else ws.sys_canvas
    root.RETAINED_FRAMES = bufs
    for _ in range(3):
        ws._dirty = True
        drv.frame(1 / 30)
    assert ws.wallpaper.restore_key(True)[0] == {"moy": "bg", "blocks": "bd",
                                                  "picture": "pic"}[theme]
    held = [bytes(root._buf)]
    stats = {"frames": 0, "band": 0}

    def frame():
        n0 = ws._frames_drawn
        drv.frame(1 / 30)
        if ws._frames_drawn == n0:
            return None
        stats["frames"] += 1
        held.append(bytes(root._buf))
        if bufs > 1:
            root._comp.last = held[-1]
            root._buf[:] = held[-bufs] if len(held) > bufs else held[0]
        return held[-1]

    for _ in range(40):
        ws._dirty = True
        frame()
    if not ref:
        # the band's frames, and the desk's still
        for name in ("_try_band_frame", "_try_still_frame"):
            def counted(cv, dt, real=getattr(ws.launcher_layer, name)):
                r = real(cv, dt)
                if r:
                    stats["band"] += 1
                return r
            setattr(ws.launcher_layer, name, counted)
    stats["frames"] = 0
    return ws, drv, frame, stats


def _session(ws, drv, frame):
    """The home's interactions, every presented frame: steps along the
    cells and across them, a held key's presses, the favourite star, a drag
    and a fling, and back."""
    shots = []

    def run(n):
        for _ in range(n):
            s = frame()
            if s is not None:
                shots.append(s)

    home = ws.launcher_layer
    g = ws.launcher
    fwd, back = ("down", "up") if not g.horizontal else ("right", "left")
    for k in range(12):
        home.handle_input(_Key(fwd))
        ws.mark_dirty()
        run(3 if k % 3 == 0 else 1)
    run(12)
    for _ in range(3):                     # a held key: the repeats a frame carries
        home.handle_input(_Key(fwd, 2))
        ws.mark_dirty()
        run(1)
    run(6)
    for k in ("down", "up", "down", "right", "left") if g.horizontal else ("right", "left"):
        home.handle_input(_Key(k))
        ws.mark_dirty()
        run(2)
    home.toggle_favourite()
    ws.mark_dirty()
    run(4)
    home.toggle_favourite()
    ws.mark_dirty()
    run(4)
    bx, by, bw, bh = g.band_rect()
    if g.horizontal:
        x, y, dx, dy = bx + bw - 40, by + bh // 2, -30, 0
    else:
        x, y, dx, dy = bx + bw // 2, by + bh - 20, 0, -24
    drv.touch(x, y)
    run(1)
    for i in range(1, 8):
        drv.touch_drag(x + i * dx, y + i * dy)
        run(1)
    drv.touch_up()
    run(40)
    for _ in range(6):
        home.handle_input(_Key(back))
        ws.mark_dirty()
        run(2)
    run(10)
    return shots


def _edits(ws, drv, frame):
    """What a card is drawn from changing under the band's frames: steps, a cart renamed, a cover landing on a card the band
    shows, the theme changed and back, and the keys going up to the tab
    row and down again (the selected card loses its focus and gets it
    back), with steps between."""
    shots = []

    def run(n):
        for _ in range(n):
            s = frame()
            if s is not None:
                shots.append(s)

    home = ws.launcher_layer
    g = ws.launcher
    fwd, back = ("down", "up") if not g.horizontal else ("right", "left")

    def step(k, n=2):
        home.handle_input(_Key(k))
        ws.mark_dirty()
        run(n)

    for _ in range(3):
        step(fwd)
    run(6)
    carts = [it for it in g.items if it.get("path")]
    assert moy_carts.save_manifest_meta(carts[1]["path"], title="Renamed Cart")
    ws.carts.rescan()
    ws.mark_dirty()
    run(6)
    assert any(it.get("title") == "Renamed Cart" for it in ws.launcher.items)
    step(fwd)
    slice_ms = cover_cache._COVER_SLICE_MS
    cover_cache._COVER_SLICE_MS = -1      # a decode's 16 rows a frame: it lands later
    try:
        moy_carts.save_cover(carts[2], cover_bytes(9, stripes=12))
        ws.carts.rescan()
        ws.mark_dirty()
        run(23)
    finally:
        cover_cache._COVER_SLICE_MS = slice_ms
    step(back)
    step(back)
    was = ws.look.theme_name
    ws.look.set_theme("contrast" if was != "contrast" else "moy", persist=False)
    ws.mark_dirty()
    run(6)
    step(fwd)
    ws.look.set_theme(was, persist=False)
    ws.mark_dirty()
    run(6)
    for _ in range(4):
        step("up")
    run(4)
    for _ in range(4):
        step("down")
    run(4)
    step(fwd)
    run(8)
    return shots


def _changes(shots):
    out = []
    for s in shots:
        if not out or out[-1] != s:
            out.append(s)
    return out


def _parity(tmp_path, monkeypatch, tier, arrangement, theme, bufs, cursor=False, tab="mine",
            session=_session):
    with monkeypatch.context() as m:      # (the conftest's patches stay)
        ws, drv, frame, _ = _home(tmp_path / "a", m, tier, arrangement, theme, bufs, True,
                                  cursor=cursor, tab=tab)
        whole = session(ws, drv, frame)
    ws, drv, frame, stats = _home(tmp_path / "b", monkeypatch, tier, arrangement, theme,
                                  bufs, False, cursor=cursor, tab=tab)
    part = session(ws, drv, frame)
    whole, part = _changes(whole), _changes(part)
    assert len(whole) == len(part), (len(whole), len(part))
    for i, (a, b) in enumerate(zip(whole, part)):
        assert a == b, "the glass's picture %d differs from a full repaint's" % i
    return ws, stats


@pytest.mark.parametrize("bufs", [1, 2, 3])
@pytest.mark.parametrize("theme", ["moy", "blocks", "picture"])
@pytest.mark.parametrize("arrangement", ["grid", "icons", "carousel"])
def test_handheld_band_frames_are_whole_repaints(tmp_path, monkeypatch, arrangement, theme,
                                                  bufs):
    _ws, stats = _parity(tmp_path, monkeypatch, "handheld", arrangement, theme, bufs)
    # L8's silent-disable direction: most of the session's frames are band frames
    assert stats["band"] * 2 > stats["frames"], stats


@pytest.mark.parametrize("bufs", [1, 3])
@pytest.mark.parametrize("theme", ["moy", "blocks"])
@pytest.mark.parametrize("arrangement", ["grid", "icons", "carousel"])
def test_desk_band_frames_are_whole_repaints(tmp_path, monkeypatch, arrangement, theme, bufs):
    _ws, stats = _parity(tmp_path, monkeypatch, "desk", arrangement, theme, bufs)
    assert stats["band"] * 2 > stats["frames"], stats


@pytest.mark.parametrize("bufs", [2, 3])
@pytest.mark.parametrize("theme", ["moy", "blocks"])
@pytest.mark.parametrize("arrangement", ["grid", "icons", "carousel"])
def test_band_frames_under_the_cursor_are_whole_repaints(tmp_path, monkeypatch, arrangement,
                                                         theme, bufs):
    """The cursor's stamp, over the band, is repainted where each buffer
    holds it, through steps and drags."""
    _ws, stats = _parity(tmp_path, monkeypatch, "handheld", arrangement, theme, bufs, True)
    assert stats["band"] * 2 > stats["frames"], stats


@pytest.mark.parametrize("theme", ["moy", "blocks", "picture"])
@pytest.mark.parametrize("arrangement", ["grid", "icons"])
def test_band_frames_over_covers_are_whole_repaints(tmp_path, monkeypatch, arrangement, theme):
    """The seed games' covers: each picture paints its square opaque but for
    its corners' rows, so a band repaint restores the ground around them."""
    _ws, stats = _parity(tmp_path, monkeypatch, "handheld", arrangement, theme, 2, tab="games")
    assert stats["band"] * 2 > stats["frames"], stats


@pytest.mark.parametrize("bufs", [1, 2, 3])
@pytest.mark.parametrize("theme", ["moy", "blocks", "picture"])
@pytest.mark.parametrize("arrangement", ["grid", "icons"])
def test_band_frames_follow_what_the_cards_show(tmp_path, monkeypatch, arrangement, theme,
                                                bufs):
    """Through a rename, a cover landing, a theme change and the focus
    leaving the cards for the tab row, every picture byte-equal to whole
    repaints."""
    ws, stats = _parity(tmp_path, monkeypatch, "handheld", arrangement, theme, bufs,
                        session=_edits)
    assert stats["band"] * 4 > stats["frames"], stats      # (a rescan paints whole)


@pytest.mark.parametrize("theme", ["moy", "blocks", "picture"])
@pytest.mark.parametrize("arrangement", ["grid", "icons", "carousel"])
def test_desk_band_frames_follow_what_the_cards_show(tmp_path, monkeypatch, arrangement, theme):
    _parity(tmp_path, monkeypatch, "desk", arrangement, theme, 3, session=_edits)
