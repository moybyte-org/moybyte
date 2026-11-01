"""A field's row and a dialog's rows frames (docs/scroll_and_text_2026-10.md
§5, P3; docs/surface_model_v1.md §14.5 and §14.7): a key typed into a
field repaints the field's row and its caret, not the page; under a dialog
over a held page it repaints the dialog's changed rows and leaves the page
as it is -- no put back on the handheld, no Editor under it on the desk.

Each session runs on two consoles fed the same input: one as it ships, and
one whose dialogs never take a rows frame (`Hold.ok` False: the page
put back, or the desk Editor drawn, and the dialog drawn whole, every
frame), on one, two and three buffers presented in turn as a board's are.
Every presented picture of the first is held to the second's bytes, what
the dialogs leave behind (a slot's value, a cart's title) to the second's,
and the share of rows frames above a floor, so the mechanism cannot
silently stop. The engine's record audit fails a rows frame whose stops
moved outside its floor."""

import pytest

from runtime import host_app, moy_input, ui, ui_draw
from ws_helpers import no_unlock_banner, quiesce

_C = ui_draw._C


@pytest.fixture(autouse=True)
def audit():
    _C.api_audit(1)
    yield
    failed = _C.api_audit(0)
    assert failed == 0, "%d rows frames drew stops unlike the last frame's outside the floor" % failed


def _console(tmp_path, windowed):
    carts = str(tmp_path / "carts")
    host_app.moy_carts.ensure_dirs(carts)
    if windowed:
        ws = host_app.build_workstation(carts, sys_size=(1024, 600), font_scale=1,
                                        windowed=True, panel_diagonal_in=7.0)
    else:
        ws = host_app.build_workstation(carts)
    ws.bar_layer._clock_text = lambda: "10:30"
    no_unlock_banner(ws)
    quiesce(ws)
    ws.pointer.visible = False
    return ws


class Pair:
    """The session on both consoles, frame by frame: `a` as it ships, `b`
    drawing every dialog whole; `a`'s buffers presented in turn."""

    def __init__(self, tmp_path, monkeypatch, bufs, windowed):
        real = ui_draw.Hold.ok
        self.a = _console(tmp_path / "ships", windowed)
        self.b = _console(tmp_path / "whole", windowed)
        b = self.b

        def ok(rows, plan):
            return False if rows.ws_of is b else real(rows, plan)
        for ws in (self.a, self.b):
            for layer in (ws.block_ui, ws.cards_layer):
                layer._hold.ws_of = ws
        monkeypatch.setattr(ui_draw.Hold, "ok", ok)
        self.da = host_app.ConsoleDriver(self.a)
        self.db = host_app.ConsoleDriver(self.b)
        self.bufs = bufs
        root = self.root(self.a)
        root.RETAINED_FRAMES = bufs
        self.held = [bytes(root._buf)]
        self.kinds = []
        self.n = 0

    @staticmethod
    def root(ws):
        return ws.wm._root_canvas if hasattr(ws.wm, "_root_canvas") else ws.sys_canvas

    def frame(self, n=1, force=True):
        for _ in range(n):
            for ws, drv in ((self.a, self.da), (self.b, self.db)):
                if force:
                    ws._dirty = True
                ws.pointer.visible = False
                k = ws._frames_drawn
                drv.frame(1 / 30)
                ws.painted = ws._frames_drawn != k
            a, b = self.a, self.b
            assert a.painted == b.painted, "the two consoles' gates parted"
            if not a.painted:
                continue
            self.n += 1
            self.kinds.append(a.frame_change)
            ra = self.root(a)
            shot, want = bytes(ra._buf), bytes(self.root(b)._buf)
            if shot != want:
                w = ra.w
                at = next(i for i in range(0, len(shot), 2) if shot[i:i + 2] != want[i:i + 2]) // 2
                raise AssertionError("frame %d (%s, %d buffers) differs from a whole draw at "
                                     "(%d, %d)" % (self.n, a.frame_change, self.bufs, at % w,
                                                   at // w))
            self.held.append(shot)
            if self.bufs > 1:
                ra._comp.last = shot
                ra._buf[:] = self.held[-self.bufs] if len(self.held) > self.bufs else self.held[0]

    def both(self, verb, *args):
        getattr(self.da, verb)(*args)
        getattr(self.db, verb)(*args)

    def run(self, code):
        for ws in (self.a, self.b):
            exec(code, {"ws": ws})
            ws.mark_dirty()

    def type(self, text, frames=2):
        for ch in text:
            self.both("key_down", ord(ch))
            self.frame()
            self.both("key_up", ord(ch))
            self.frame(frames - 1)

    def key(self, code, frames=3):
        self.both("key_down", code)
        self.frame()
        self.both("key_up", code)
        self.frame(frames - 1)

    def held_key(self, code, frames=24):
        """A key held down `frames` frames: it types once, then repeats
        after the delay at the rate, on the frames' clock."""
        self.both("key_down", code)
        self.frame(frames)
        self.both("key_up", code)
        self.frame(2)

    def rows(self):
        return sum(1 for k in self.kinds if k == "rows")


_BS = 8
_TAB = 9
_ENTER = 13
_ESC = 27


def _editor(p, title, tab):
    p.run("ws.open_in_editor([c for c in ws.carts.all if c.get('title') == %r][0])\n"
          "ws.editor_app.set_tab(%r)" % (title, tab))
    p.frame(6, force=True)


def _blocks_session(p):
    """The Blocks number prompt typed into, a held digit, Backspace held, a
    long value, Enter; the name prompt typed into, Tab to its buttons and
    back, Escape."""
    _editor(p, "Coin Quest", "blocks")
    # on the desk, the keys on the palette's pane beside the Blocks pane, as
    # a workspace opens
    p.run("_d = ws.editor_desk\n_d is not None and _d.active() and _d.focus_pane(0)")
    p.frame(3)
    p.run("ws.block_ui._blk_open_kbd('num', cur=5, block={'t': 'set', 'p': {}}, "
          "slot='value', allow_block=True)")
    p.frame(4)
    p.type("1234")
    assert p.a.block_ui.blk_kbd["entry"].text == "1234"     # the prompt takes the keys
    p.held_key(ord("7"))
    p.held_key(_BS, 12)
    p.type("98765432109876543210")      # past the field's width, up to its cap
    p.key(_ENTER)
    p.run("ws.block_ui._blk_open_kbd('var', var='score', slot_target=None)")
    p.frame(4)
    p.type("lives")
    assert p.a.block_ui.blk_kbd["entry"].text == "lives"
    p.key(_BS)
    p.key(_TAB)
    p.key(_TAB)
    p.key(_TAB)
    p.type("x")
    p.key(_ESC)
    p.frame(4)


def _cards_session(p):
    """Cart info opened by its door over its page, typed into, its fields moved between, its title emptied and Enter (the line
    under the fields says why it stayed open; the next key clears it),
    then saved; New script typed into and cancelled."""
    _editor(p, "Coin Quest", "cards")
    page = p.a.cards_layer.page()
    for _ in range(16):
        if page._focus == "Cart info":
            break
        p.both("press", "down")
        p.frame(3)
    assert page._focus == "Cart info"
    p.both("press", "a")                # its door: the dialog over the page
    p.frame(4)
    assert p.a.cards_layer.prompt is not None
    p.type(" two")
    p.key(moy_input.KEY_DOWN)
    p.type("Ana")
    p.key(moy_input.KEY_UP)
    p.held_key(_BS, 40)
    p.key(_ENTER)
    assert p.a.cards_layer.prompt.msg is not None
    p.type("Coins")
    p.key(_ENTER)
    assert p.a.cards_layer.prompt is None
    p.frame(3)
    p.run("ws.cards_layer._open_newf()")
    p.frame(4)
    p.type("helpers")
    p.key(_ESC)
    p.frame(4)


@pytest.mark.parametrize("bufs", (1, 2, 3))
@pytest.mark.parametrize("windowed", (False, True))
def test_the_blocks_prompts_draw_their_rows_alone(tmp_path, monkeypatch, bufs, windowed):
    p = Pair(tmp_path, monkeypatch, bufs, windowed)
    _blocks_session(p)
    a, b = p.a.block_ui.blocks_ed, p.b.block_ui.blocks_ed
    assert a.program == b.program
    assert p.rows() >= 30, "the prompts drew their rows alone on %d frames: %r" % (
        p.rows(), p.kinds)


@pytest.mark.parametrize("bufs", (1, 2, 3))
@pytest.mark.parametrize("windowed", (False, True))
def test_cart_info_and_new_script_draw_their_rows_alone(tmp_path, monkeypatch, bufs, windowed):
    p = Pair(tmp_path, monkeypatch, bufs, windowed)
    _cards_session(p)
    assert p.a.project.cart.get("title") == p.b.project.cart.get("title") == "Coins"
    assert p.a.project.cart.get("author") == "Ana"
    assert p.rows() >= 25, "the dialogs drew their rows alone on %d frames: %r" % (
        p.rows(), p.kinds)


@pytest.mark.parametrize("bufs", (1, 2, 3))
def test_a_field_on_a_page_draws_its_row(tmp_path, monkeypatch, bufs):
    """Settings' Name: a key typed into a page's field draws the field's
    row (its text and caret), a held key's repeats too, and the field typed
    into while the page glides draws whole as a glide's frames do."""
    p = Pair(tmp_path, monkeypatch, bufs, False)
    p.run("ws.open_settings()")
    p.frame(4)
    for _ in range(3):
        p.both("press", "down")         # the page glides...
        p.frame(1)
    p.run("ws.settings_layer._want = 'Name'")   # ...and the Name field is typed into
    p.type("Moy")
    p.frame(12)
    del p.kinds[:]
    p.type("Bench")
    p.held_key(ord("e"))
    p.held_key(_BS, 16)
    p.type("ah")
    assert p.a.settings_layer._name == p.b.settings_layer._name
    assert p.rows() >= 0.8 * len(p.kinds), p.kinds


# -- the engine's dialog rows frames, under any app ---------------------------------

W, H = 320, 240


class DialogApp:
    """A page under a dialog over it: the page drawn only on frames that
    are not the dialog's rows frames (as a held page is put back), the
    dialog given `wash` False and its gen."""

    def __init__(self):
        self.title = "Name it"
        self.text = "Moy"
        self.msg = None
        self.gen = 1

    def draw(self, cv, page):
        if page:
            cv.rect(0, 0, W, H, 3)
            for i in range(10):
                cv.print("the page under the dialog %d" % i, 8, 8 + 22 * i, 7)
        ui.dialog(self.title, ("Cancel", "OK"), "primary", {"wash": False, "gen": self.gen})
        ui.label("What its name is")
        self.text, _done = ui.field("Name", self.text, 24, {"id": "name"})
        if self.msg:
            ui.label(self.msg)
        ui.dialog()


class DialogRig:
    """One app drawn by a host whose dialogs may take rows frames, on
    `bufs` buffers presented in turn, and by one drawing whole every frame."""

    def __init__(self, th, bufs):
        from runtime.host_canvas import make_system_canvas
        self.th = th
        self.apps = (DialogApp(), DialogApp())
        self.bufs = bufs
        self.cv = make_system_canvas(W, H)
        self.cv.RETAINED_FRAMES = bufs
        self.ref = make_system_canvas(W, H)
        self.a = ui_draw.AppHost()
        self.a.held = lambda p: p[7] >= bufs    # every buffer holds the page
        self.b = ui_draw.AppHost()
        self.n = 0
        self.held = [bytes(self.cv._buf)]
        self.kinds = []

    def both(self, **kw):
        for app in self.apps:
            for k, v in kw.items():
                setattr(app, k, v)

    def frame(self, key=0, buttons=0, late=False):
        """One frame of both; `late`: this frame shows the last frame's
        picture (a dialog unlike the one planned draws nothing)."""
        for host, cv, app, alone in ((self.a, self.cv, self.apps[0], True),
                                     (self.b, self.ref, self.apps[1], False)):
            ui_draw.paint_frame(self.n, alone)
            host.inp[0] = host.inp[1] = buttons
            host.inp[5] = key
            host.begin(cv, self.th, (0, 0, W, H))
            app.draw(cv, not (alone and host.shifting))
            if alone:
                self.kinds.append(ui_draw.change_take())
        self.n += 1
        shot = bytes(self.cv._buf)
        want = self.held[-1] if late else bytes(self.ref._buf)
        assert shot == want, "frame %d (%s, %d buffers) differs" % (self.n, self.kinds[-1],
                                                                   self.bufs)
        self.held.append(bytes(self.ref._buf) if late else shot)
        if self.bufs > 1:
            self.cv._comp.last = shot
            self.cv._buf[:] = self.held[-self.bufs] if len(self.held) > self.bufs else self.held[0]


@pytest.fixture
def th(tmp_path):
    from ws_helpers import build_ws
    return build_ws(tmp_path).theme_colors


@pytest.mark.parametrize("bufs", (1, 2, 3))
def test_a_dialog_over_a_held_page_draws_its_rows_alone(th, bufs):
    r = DialogRig(th, bufs)
    for _ in range(4):
        r.frame()
    del r.kinds[:]
    for ch in "Bench":
        r.frame(ord(ch))
        r.frame()
    r.frame(_BS)
    r.frame(_TAB)                       # onto its buttons, and back to its field
    r.frame(_TAB)
    r.frame(_TAB)
    r.frame()
    r.both(text="Set by the app")       # its own text: the field's row again
    r.frame()
    r.frame()
    assert r.kinds.count("rows") >= 14, r.kinds


@pytest.mark.parametrize("bufs", (1, 2))
def test_a_dialog_unlike_the_one_planned_draws_nothing_and_owes_the_next(th, bufs):
    r = DialogRig(th, bufs)
    for _ in range(4):
        r.frame()
    r.frame(ord("a"))
    r.both(msg="Give it a name.", gen=2)   # a line the app adds, its gen moved
    r.frame(late=True)
    for _ in range(bufs + 1):
        r.frame()
    r.both(title="Name it again")       # another dialog
    r.frame(late=True)
    for _ in range(bufs + 1):
        r.frame()
    r.frame(ord("b"))
    assert r.kinds[-1] == "rows"
