"""The page held under a dialog (`ui_draw.HeldPage`, docs/surface_model_v1.md
§14.5): while the Editor's Cart info dialog or a Blocks entry prompt is up,
or the pause or stop card stands over a run, a frame puts the page under it
back washed instead of drawing the page and its wash.

Each case runs a scripted session twice, on one and two buffers presented in
turn, as a board's framebuffers are: once as it ships, and once with the
hold off and the dialog washing the page itself (every frame draws the
page, as before there was a hold), and holds the first's
glass, picture by picture, to the second's bytes -- and that the hold put
the page back on most of the session's frames, so it cannot silently stop."""

import pytest

from runtime import host_app, ui_draw
from ws_helpers import no_unlock_banner, open_cart, quiesce

# A game whose frames are a function of its frame count alone.
_GAME = (
    "t = 0\n"
    "def _update(dt):\n"
    "    global t\n"
    "    t += 1\n"
    "def _draw():\n"
    "    cls(1)\n"
    "    for i in range(8):\n"
    "        rect((t * 3 + i * 40) % 300, i * 28, 30, 20, 8 + i)\n")


class _Key:
    last_key = 0

    def __init__(self, n):
        self.n = n

    def pressed(self, n):
        return n == self.n


def _cart(ws, title):
    for cart in ws.carts.all:
        if cart.get("title") == title:
            return cart
    raise AssertionError(title)


def _console(tmp_path, monkeypatch, bufs, ref, windowed):
    puts = [0]
    real = ui_draw.HeldPage.put

    def put(self, *a, **k):
        if ref:
            return False
        got = real(self, *a, **k)
        puts[0] += got
        return got
    monkeypatch.setattr(ui_draw.HeldPage, "put", put)
    if ref:
        # the dialog washes the page itself, as one with no held page did
        monkeypatch.setattr(ui_draw, "wash_page", lambda: None)
        monkeypatch.setattr(ui_draw, "NO_WASH", None)
    carts = str(tmp_path / "carts")
    host_app.moy_carts.ensure_dirs(carts)
    host_app.moy_carts.create("Stripes", carts, src=_GAME, type="game")
    if windowed:
        ws = host_app.build_workstation(carts, sys_size=(1024, 600),
                                        font_scale=1, windowed=True, panel_diagonal_in=7.0)
    else:
        ws = host_app.build_workstation(carts)
    ws.bar_layer._clock_text = lambda: "10:30"
    no_unlock_banner(ws)
    quiesce(ws)
    drv = host_app.ConsoleDriver(ws)
    root = ws.wm._root_canvas if windowed else ws.sys_canvas
    root.RETAINED_FRAMES = bufs
    held = [bytes(root._buf)]
    shots = []

    def frame(n=1):
        for _ in range(n):
            k = ws._frames_drawn
            ws._dirty = True
            drv.frame(1 / 30)
            if ws._frames_drawn == k:
                continue
            held.append(bytes(root._buf))
            shots.append(held[-1])
            if bufs > 1:
                root._buf[:] = held[-bufs] if len(held) > bufs else held[0]
    return ws, frame, shots, puts


def _dialogs(ws, frame):
    """The Blocks number and name prompts typed into, then Cart info typed
    into and its fields moved, every presented frame."""
    ws.open_in_editor(_cart(ws, "Star Catcher"))
    ws.set_menu_view("blocks")
    frame(4)
    bu = ws.block_ui
    for kind, opener, typed in (
            ("num", lambda: bu._blk_open_kbd("num", cur=5, block={"t": "set", "p": {}},
                                             slot="value", allow_block=True), "1234"),
            ("var", lambda: bu._blk_open_kbd("var", var="score", slot_target=None), "abcd")):
        opener()
        frame(3)
        for ch in typed:
            bu._blk_kbd_key(ord(ch))
            frame(2)
        bu._blk_kbd_cancel()
        frame(3)
    ws.set_menu_view("cards")
    frame(4)
    cl = ws.cards_layer
    cl._open_meta()
    frame(3)
    for ch in "Hi there":
        cl._prompt_key(ord(ch))
        frame(2)
    for n in ("down", "up", "down"):
        cl._prompt_input(_Key(n))
        frame(2)
    cl._close_prompt()
    frame(3)


def _cards(ws, frame):
    """The pause card over a running game moved along, Options and back,
    resumed; then the stop card over a run that crashed, moved along."""
    open_cart(ws, "Stripes")
    frame(6)
    ws.pause.show()
    frame(3)
    for n in ("down", "down", "up"):
        ws.pause.handle_input(_Key(n))
        frame(2)
    ws.pause.choose(2)                  # Options: a smaller card over the frame
    frame(3)
    ws.pause.back()
    frame(3)
    ws.pause.close()
    frame(4)
    ws.cart_error = "boom"
    frame(4)
    assert ws.pause.open and ws.pause.stopped
    for n in ("down", "up", "down"):
        ws.pause.handle_input(_Key(n))
        frame(2)


@pytest.mark.parametrize("bufs", (1, 2))
@pytest.mark.parametrize("windowed", (False, True))
def test_a_held_page_under_a_dialog_is_the_page_drawn(tmp_path, monkeypatch, bufs, windowed):
    got = []
    for ref in (False, True):
        ws, frame, shots, puts = _console(tmp_path / str(ref), monkeypatch, bufs, ref,
                                          windowed)
        _dialogs(ws, frame)
        got.append((shots, puts[0]))
    (shots, puts), (want, _none) = got
    assert len(shots) == len(want)
    for i, (a, b) in enumerate(zip(shots, want)):
        assert a == b, "frame %d of %d differs from the page drawn" % (i, len(shots))
    if windowed:
        # the desk's Editor draws its pages into a viewport of the window's
        # buffer, which the hold does not copy from
        assert puts == 0
    else:
        assert puts >= 20, "the hold put the page back on %d frames" % puts


@pytest.mark.parametrize("bufs", (1, 2))
def test_the_cards_over_a_run_keep_its_frame_washed(tmp_path, monkeypatch, bufs):
    got = []
    for ref in (False, True):
        ws, frame, shots, puts = _console(tmp_path / str(ref), monkeypatch, bufs, ref,
                                          False)
        _cards(ws, frame)
        got.append((shots, puts[0]))
    (shots, puts), (want, _none) = got
    assert len(shots) == len(want)
    for i, (a, b) in enumerate(zip(shots, want)):
        assert a == b, "frame %d of %d differs from the frame washed" % (i, len(shots))
    assert puts >= 12, "the washed frame was put back on %d frames" % puts


def test_the_fit_check_releases_the_held_page_and_a_dialog_mints_it_again(tmp_path,
                                                                          monkeypatch):
    ws, frame, shots, puts = _console(tmp_path, monkeypatch, 1, False, False)
    ws.open_in_editor(_cart(ws, "Star Catcher"))
    ws.set_menu_view("cards")
    frame(3)
    ws.cards_layer._open_meta()
    frame(3)
    assert ws.held_pages[0] is not None and puts[0] > 0
    ws.cards_layer._close_prompt()
    frame(1)
    open_cart(ws, "Stripes")
    frame(3)
    ws.pause.show()
    frame(2)
    ws.pause.close()
    frame(2)
    assert ws.pause._under_lay is not None
    while ws.wm.release_cache():
        pass
    assert ws.held_pages[0] is None and ws.pause._under_lay is None
    ws.exit()
    ws.open_in_editor(_cart(ws, "Star Catcher"))
    ws.set_menu_view("cards")
    frame(2)
    ws.cards_layer._open_meta()
    n = puts[0]
    frame(1)                            # the page drawn, and held again
    frame(2)
    assert ws.held_pages[0] is not None and puts[0] > n
