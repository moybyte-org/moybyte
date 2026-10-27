"""The page held under a dialog (`ui_draw.HeldPage`, docs/surface_model_v1.md
§14.5): while the Editor's Cart info dialog or a Blocks entry prompt is up,
or the pause or stop card stands over a run, a frame puts the page under it
back washed instead of drawing the page and its wash.

Each case runs a scripted session twice, on one and two buffers presented in
turn, as a board's framebuffers are: once as it ships, and once with the
hold off and the dialog washing the page itself (every frame draws the
page, as before there was a hold), and holds the first's
glass, picture by picture, to the second's bytes -- and that the hold put
the page back on most of the session's frames, so it cannot silently stop.
The dialogs the keys open run on frames the gate lets through, as a
board's, and their first frames take the page from the last frame's
buffer (`HeldPage.last`) where it may."""

import pytest

from runtime import block_editor_ui, host_app, ui_draw
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


def _console(tmp_path, monkeypatch, bufs, ref, windowed, force=True):
    """A console whose presented frames are kept (`shots`) on `bufs`
    buffers presented in turn; `ref`: the page drawn on every frame and
    the dialog washing it. `force`: every frame paints (else only those
    the gate lets through, as on a board). `puts` counts the frames that
    put the held page back, and the first frames that took the page from
    the last frame (`HeldPage.last`)."""
    puts = [0, 0]
    real = ui_draw.HeldPage.put
    real_last = ui_draw.HeldPage.last

    def put(self, *a, **k):
        if ref:
            return False
        got = real(self, *a, **k)
        puts[0] += got
        return got

    def last(self, *a, **k):
        if ref:
            return False
        got = real_last(self, *a, **k)
        puts[1] += got
        return got
    monkeypatch.setattr(ui_draw.HeldPage, "put", put)
    monkeypatch.setattr(ui_draw.HeldPage, "last", last)
    if ref:
        # the dialog washes the page itself, as one with no held page did
        monkeypatch.setattr(ui_draw, "wash_page", lambda *a: (0, 0, 0, 0))
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
            if force:
                ws._dirty = True
            drv.frame(1 / 30)
            if ws._frames_drawn == k:
                continue
            held.append(bytes(root._buf))
            shots.append(held[-1])
            if bufs > 1:
                root._comp.last = held[-1]
                root._buf[:] = held[-bufs] if len(held) > bufs else held[0]
    frame.drv = drv
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


def _keyed(ws, frame):
    """The dialogs as the keys open them: A on each Blocks slot down a
    script, a prompt typed into and cancelled, a menu backed out of; then
    Cart info, A on its door, typed into and cancelled."""
    drv = frame.drv
    ws.open_in_editor(_cart(ws, "Tap Game"))
    ws.set_menu_view("blocks")
    frame(4)
    bu = ws.block_ui
    prompts = menus = 0
    opens = None                        # a row whose first slot A prompts for
    for _ in range(14):
        drv.press("down")
        frame(3)
        for step in (False, True):
            if step:
                drv.press("right")
                frame(3)
            drv.press("a")
            frame(3)
            if bu.blk_kbd is not None:
                prompts += 1
                if not step:
                    opens = bu.blocks_ed.cur
                for ch in "12":
                    drv.type_char(ord(ch))
                    frame(2)
                bu._blk_kbd_cancel()
                frame(3)
                break
            m = bu.blk_menu
            if m is not None and block_editor_ui._NUM_LITERAL_ITEM in m["items"]:
                # "type a number" from the chooser: the prompt over the
                # page the menu has left
                for _ in range(m["items"].index(block_editor_ui._NUM_LITERAL_ITEM)
                               - m["sel"]):
                    drv.press("down")
                    frame(2)
                drv.press("a")
                frame(3)
                assert bu.blk_kbd is not None
                menus += 1
                bu._blk_kbd_cancel()
                frame(3)
                break
            if m is not None:
                drv.press("b")
                frame(3)
    assert prompts >= 2 and menus >= 1
    # the prompt opened with a move among its keys, after a move no frame
    # painted, and under the trackball's cursor
    assert opens is not None
    for case in ("keys", "unpainted", "cursor"):
        while bu.blocks_ed.cur != opens + 1:
            drv.press("down" if bu.blocks_ed.cur < opens + 1 else "up")
            frame(3)
        if case == "keys":
            drv.press("up")
        elif case == "unpainted":
            bu._blk_move_cursor(-1)
            ws._dirty = True
        else:
            drv.press("up")
            frame(3)
            p = ws.pointer
            p.move(12 - p.x, 190 - p.y)  # over the page, clear of the dialog
            frame(3)
            assert ws.pointer.visible
        drv.press("a")
        frame(3)
        assert bu.blk_kbd is not None and bu.blocks_ed.cur == opens
        drv.type_char(ord("7"))
        if case == "cursor":
            p.move(280, 0)              # off what it covered
        frame(3)
        bu._blk_kbd_cancel()
        frame(3)
    ws.pointer.visible = False
    ws._dirty = True
    frame(2)
    ws.exit()
    ws.open_in_editor(_cart(ws, "Star Catcher"))
    ws.set_menu_view("cards")
    frame(4)
    page = ws.cards_layer.page()
    for _ in range(12):
        if page._focus == "Cart info":
            break
        drv.press("down")
        frame(3)
    assert page._focus == "Cart info"
    drv.press("a")
    frame(3)
    assert ws.cards_layer.prompt is not None
    for ch in "ab":
        drv.type_char(ord(ch))
        frame(2)
    ws.cards_layer._close_prompt()
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
def test_a_dialog_the_keys_open_takes_the_page_from_the_last_frame(tmp_path, monkeypatch,
                                                                   bufs):
    got = []
    for ref in (False, True):
        ws, frame, shots, puts = _console(tmp_path / str(ref), monkeypatch, bufs, ref,
                                          False, force=False)
        _keyed(ws, frame)
        got.append((shots, puts))
    (shots, puts), (want, _none) = got
    assert len(shots) == len(want)
    for i, (a, b) in enumerate(zip(shots, want)):
        assert a == b, "frame %d of %d differs from the page drawn" % (i, len(shots))
    assert puts[1] >= 3, "the last frame's page was taken on %d first frames" % puts[1]
    assert puts[0] >= 6, "the hold put the page back on %d frames" % puts[0]


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
