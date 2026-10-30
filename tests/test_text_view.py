"""The text view (native/moy_ui/moy_ui_text.h, `ui.text_view`;
docs/scroll_and_text_2026-10.md §5): the Code tab's and theme Config's rows,
the desk's made code and an app's terminal, drawn in C through the verb, and
only the rows that changed.

Each Code session runs on two consoles fed the same input: one as it ships,
and one whose Code tab draws every frame whole (its host never shifts) -- on
one, two and three buffers presented in turn as a board's are. Every
presented picture of the first is held to the second's bytes, so every rows
and shift frame is a whole repaint's; the share of floor frames is held
above a floor, so the mechanism cannot silently stop. The C's colour runs
are held to the Python highlighter's (`tests/text_twin.py`), and the Code
tab's pixels at rest to the goldens (tests/test_shell_goldens.py)."""

import re
from pathlib import Path

import pytest

import text_twin
from runtime import code_layer, host_app, moy_input, ui, ui_draw
from ws_helpers import no_unlock_banner, quiesce

_C = ui_draw._C
ROOT = Path(__file__).resolve().parent.parent


def test_the_options_are_the_headers():
    """ui.text_view's options and the host binding's defaults are
    moy_ui_api.h's MOY_UI_TXO_*."""
    src = (ROOT / "native" / "moy_ui" / "moy_ui_api.h").read_text()
    body = src[src.index("MOY_UI_TXO_ROWS = 0"):src.index("MOY_UI_TXO_LEN")]
    names = re.findall(r"MOY_UI_TXO_([A-Z0-9_]+)", body)
    from tools import moy_ui_binding
    assert len(names) == moy_ui_binding._TXO_LEN == len(ui._TXO)
    words = re.search(r"#define MOY_UI_TXO_DEFAULTS \{([^}]*)\}", src).group(1).split(",")
    want = tuple(ui_draw.SYN_SGR if w.strip() == "MOY_UI_SYN_SGR" else int(w) for w in words)
    assert want == moy_ui_binding._TXO_DEFAULTS


def test_the_syntaxes_are_named_alike_in_every_face():
    """A syntax's name is its MOY_UI_SYN_* number's place among the names,
    in the Python face, the Lua face and ui_draw's table alike."""
    head = (ROOT / "native" / "moy_ui" / "moy_ui_text.h").read_text()
    body = head[head.index("MOY_UI_SYN_NONE = 0"):head.index("MOY_UI_SYN_N\n")]
    syns = re.findall(r"MOY_UI_SYN_([A-Z]+)", body)
    lua = (ROOT / "native" / "moycore" / "moycore_ui.h").read_text()
    names = re.search(r"syn\[\] = \{([^}]*)\}", lua).group(1)
    names = tuple(n.strip().strip('"') for n in names.split(","))
    assert names == ui._SYNTAXES
    assert len(names) == len(syns)
    for i, name in enumerate(names[1:], 1):
        assert syns[i] == name.upper()
        assert ui_draw.SYNTAX[name] == i


# -- the runs ------------------------------------------------------------------------

def _c_runs(line, syn):
    """The C's runs as (first column, column after, class)."""
    w = _C.text_runs(line, syn)
    b = line.encode()
    out = []
    for k in range(0, len(w), 4):
        a, e, col, tone = w[k:k + 4]
        out.append((col, col + len(b[a:e].decode()), code_layer._HL_CLASSES[tone]))
    return out


def _py_runs(runs):
    return [(runs[k], runs[k + 1], runs[k + 2]) for k in range(0, len(runs), 4)]


def _seed_lines():
    lines = ['s = "a\\"b" + \'c\'', 'x = "open', "n = 0x1F + 1.5e3 - .5",
             "a--b # c", "-- lua comment", "#len", "x1y2 = _z_9", "é = 'ü' # ö",
             "", " ", "print(\"\\\\\") -- end", "x = '\\", "a-b-c--d", "def end local and",
             "x = 'é\\é' + \"\\"]
    for p in sorted((ROOT / "system_carts").glob("*.moy/main.*")):
        lines += p.read_text(encoding="utf-8").splitlines()
    return lines


def test_the_runs_are_the_pythons_per_character_scan():
    """Every line of every seed cart's code, in its own language and the
    other, and the edges (escapes, an unclosed string, numbers, either
    comment marker, a name with digits, characters past ASCII): the C's
    runs are the Python highlighter's colours grouped."""
    lines = _seed_lines()
    assert len(lines) > 1000
    for lua in (False, True):
        syn = ui_draw.SYN_LUA if lua else ui_draw.SYN_PYTHON
        for line in lines:
            assert _c_runs(line, syn) == _py_runs(text_twin._highlight_runs(line, lua)), line


def test_the_json_runs_are_the_theme_modes():
    lines = ['{"name": "Moy", "n": -1.5e3, "on": true, "off": false, "x": null}',
             '  "a\\"b" : "c\\\\"', '"open', "[1, 2, -x, nullx, truth]", "", "  ",
             '{"é": "ü"}']
    for p in sorted((ROOT / "native" / "moy_ui" / "themes").glob("*.json")):
        lines += p.read_text(encoding="utf-8").splitlines()
    for line in lines:
        want = text_twin._runs_of(line, text_twin._highlight_json(line))
        assert _c_runs(line, ui_draw.SYN_JSON) == _py_runs(want), line


def test_sgr_escapes_take_no_column():
    w = _C.text_runs("a\x1b[31mred\x1b[0m b\x1b[92mg", ui_draw.SYN_SGR)
    runs = [tuple(w[k:k + 4]) for k in range(0, len(w), 4)]
    # (bytes, column, tone): text, danger, text, string
    assert [(r[2], r[3]) for r in runs] == [(0, 0), (1, 7), (4, 0), (6, 2)]


def _md_runs(line):
    """The C's Markdown runs as (text, tone name)."""
    w = _C.text_runs(line, ui_draw.SYN_MARKDOWN)
    b = line.encode()
    names = ("text", "keyword", "string", "number", "comment", "builtin")
    return [(b[w[k]:w[k + 1]].decode(), names[w[k + 3]]) for k in range(0, len(w), 4)]


def test_the_markdown_runs_colour_a_notes_marks():
    """A note's source, coloured as Code colours a program: its marks dim
    (the comment's tone), a heading's words in the number's, a task's open
    box in the keyword's (the seed themes' accent) and a ticked one in the
    string's with its words dim, a link's name in the builtin's (their
    blue). Each run is the line's own, read with nothing past it."""
    want = {
        "## Plan [[x]]": [("##", "comment"), (" Plan [[x]]", "number")],
        "#": [("#", "comment")],
        "#hashtag": [("#hashtag", "text")],
        "####### seven": [("####### seven", "text")],
        "- [ ] buy [[Milk]]": [("- ", "comment"), ("[ ]", "keyword"), (" buy ", "text"),
                               ("[[", "comment"), ("Milk", "builtin"), ("]]", "comment")],
        "  * [x] done **now**": [("  ", "text"), ("* ", "comment"), ("[x]", "string"),
                                 (" done **now**", "comment")],
        "- [X]": [("- ", "comment"), ("[X]", "string")],
        "- [y] odd": [("- ", "comment"), ("[y] odd", "text")],
        "12. step `x = 1` **b**": [("12. ", "comment"), ("step ", "text"), ("`", "comment"),
                                   ("x = 1", "text"), ("`", "comment"), (" ", "text"),
                                   ("**", "comment"), ("b", "text"), ("**", "comment")],
        "> quote - no": [("> ", "comment"), ("quote - no", "text")],
        "---": [("---", "comment")],
        "* * *": [("* * *", "comment")],
        "see [[open": [("see ", "text"), ("[[", "comment"), ("open", "builtin")],
        "a `b": [("a ", "text"), ("`", "comment"), ("b", "text")],
        "é [[ü]]": [("é ", "text"), ("[[", "comment"), ("ü", "builtin"), ("]]", "comment")],
        "-x and 1.5": [("-x and 1.5", "text")],
        "": [],
    }
    for line, runs in want.items():
        assert _md_runs(line) == runs, line
    # a wrapped row's runs are its line's up to the row's end
    line = "- [ ] call [[Ana]] today"
    whole = _md_runs(line)
    for end in range(len(line) + 1):
        part = _md_runs(line[:end])
        got = "".join(t for t, _ in part)
        assert got == line[:end]
        for (t, tone), (wt, wtone) in zip(part[:-1], whole):
            assert (t, tone) == (wt, wtone), line[:end]


# -- two consoles, one drawing the Python's whole ------------------------------------

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


def _whole(host):
    """`host`'s frames draw whole: it never plans a shift or rows frame."""
    def shift(_cv, _th):
        host.shifting = False
    host._shift = shift


class Pair:
    """The session on both consoles, frame by frame: `a` as it ships, `b`
    drawing the Code tab's rows whole; `a`'s buffers presented in turn."""

    def __init__(self, tmp_path, bufs, windowed):
        self.a = _console(tmp_path / "ships", windowed)
        self.b = _console(tmp_path / "whole", windowed)
        _whole(self.b.code_layer._host)
        _whole(self.b.theme_config_layer._host)
        self.da = host_app.ConsoleDriver(self.a)
        self.db = host_app.ConsoleDriver(self.b)
        self.bufs = bufs
        self.windowed = windowed
        root = self.root(self.a)
        root.RETAINED_FRAMES = bufs
        self.held = [bytes(root._buf)]
        self.kinds = []
        self.n = 0

    @staticmethod
    def root(ws):
        return ws.wm._root_canvas if hasattr(ws.wm, "_root_canvas") else ws.sys_canvas

    def frame(self, n=1, force=None):
        # on the desk a board's buffers keep the WM asking for frames the
        # one-buffer twin does not: every frame is painted there
        force = self.windowed if force is None else force
        for _ in range(n):
            for ws, drv in ((self.a, self.da), (self.b, self.db)):
                if force:
                    ws._dirty = True
                ws.pointer.visible = False
                k = ws._frames_drawn
                ws.code_layer._host.shifting = ws.theme_config_layer._host.shifting = False
                drv.frame(1 / 30)
                ws.painted = ws._frames_drawn != k
            a, b = self.a, self.b
            assert a.painted == b.painted, "the two consoles' gates parted"
            if not a.painted:
                continue
            self.n += 1
            self.kinds.append(a.code_layer._host.shifting or a.theme_config_layer._host.shifting)
            ra = self.root(a)
            shot, want = bytes(ra._buf), bytes(self.root(b)._buf)
            if shot != want:
                w = ra.w
                at = next(i for i in range(0, len(shot), 2) if shot[i:i + 2] != want[i:i + 2]) // 2
                raise AssertionError("frame %d (%s, %d buffers) differs from a whole draw at "
                                     "(%d, %d)" % (self.n, self.kinds[-1], self.bufs,
                                                   at % w, at // w))
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
            self.key(ord(ch), frames)

    def key(self, code, frames=2, mods=0):
        self.both("key_down", code, mods)
        self.frame()
        self.both("key_up", code, mods)
        self.frame(frames - 1)

    def held_key(self, code, frames=24, mods=0):
        self.both("key_down", code, mods)
        self.frame(frames)
        self.both("key_up", code, mods)
        self.frame(2)

    def floors(self):
        return sum(1 for k in self.kinds if k)


_BS = 8
_ENTER = 13
_ESC = 27


def _code(p, title="Star Catcher"):
    p.run("ws.open_in_editor([c for c in ws.carts.all if c.get('title') == %r][0])\n"
          "ws.editor_app.set_tab('code')\n"
          "_d = ws.editor_desk\n"
          "if _d is not None and _d.active():\n"
          "    [_d.focus_pane(n) for n, (pane, _r, _p) in enumerate(_d.panes()) if 'code' in pane]"
          % title)
    p.frame(6, force=True)


def _fling(p):
    """A finger dragged up the code and let go: the view follows it by
    pixels and coasts on after the release; a key brings the caret back."""
    a = p.a.editor
    lay = p.a.code_layout
    x, y = lay.x0 + 60, lay.y0 + 8 * lay.lh
    top = a.top
    p.both("touch", x, y)
    p.frame()
    for k in range(1, 7):
        p.both("touch_drag", x, y - k * lay.lh)
        p.frame()
    p.both("touch_up")
    p.frame()
    let_go = a.top
    assert let_go > top, "the drag did not scroll the code"
    p.frame(4)
    assert a.top > let_go, "the release did not coast"
    p.frame(40)
    p.key(moy_input.KEY_DOWN)                   # the caret, out of view, brings it back


def _session(p):
    """Typing, caret moves, a held key, a selection and a paste, a long
    line scrolled across, find past the view, completion and the crash
    popover, a gutter growing past 99 and 999 lines, a theme change."""
    _code(p)
    a, b = p.a.editor, p.b.editor
    p.type("x = 1")
    p.key(_ENTER)
    p.type("# a comment")
    p.key(moy_input.KEY_UP)
    p.key(moy_input.KEY_LEFT)
    p.held_key(moy_input.KEY_DOWN, 30)          # a held key: the view scrolls
    p.held_key(moy_input.KEY_UP, 12)
    if not p.windowed:
        _fling(p)                               # a drag scrolls, its release coasts
    p.held_key(_BS, 10)                         # Backspace held
    p.key(moy_input.KEY_DOWN, mods=moy_input.SHIFT)     # a selection
    p.key(moy_input.KEY_DOWN, mods=moy_input.SHIFT)
    p.key(0x03)                                 # copy
    p.key(moy_input.KEY_RIGHT)
    p.key(0x16)                                 # paste
    p.type("long = '" + "abcdefghij" * 8 + "'")     # past the view: it scrolls across
    p.held_key(moy_input.KEY_LEFT, 40)
    p.key(_ENTER)
    p.key(0x06)                                 # find
    p.type("coin")
    for _ in range(6):
        p.key(_ENTER)                           # next, past the view
    p.key(_ESC)
    p.type("pr")
    p.key(0x05)                                 # completion over the rows
    p.key(moy_input.KEY_DOWN)
    p.type("i")
    p.key(_ESC)
    p.run("ws.code_err_row = ws.editor.row; ws.code_err = 'NameError: x'\n"
          "ws.crash_popup = 'NameError: x is not defined'")
    p.frame(3)
    p.key(_ESC)
    p.run("ws.code_err_row = None; ws.code_err = None")
    p.frame(2)
    p.run("ws.look.set_theme('moy', persist=False, variant='light')")
    p.frame(3)
    p.type("y")
    assert a.lines == b.lines and (a.row, a.col, a.top, a.left) == (b.row, b.col, b.top, b.left)


def _gutter_session(p):
    """The gutter grows past 99 and 999 lines: the frames it widens draw
    whole, the rows between them as rows."""
    _code(p)
    p.run("ws.editor.set_text('\\n'.join('v%d = %d' % (i, i) for i in range(96)))\n"
          "ws.editor.row = 95")
    p.frame(3)
    for _ in range(6):
        p.key(_ENTER)
    p.run("ws.editor.set_text('\\n'.join('v%d = %d' % (i, i) for i in range(996)))\n"
          "ws.editor.row = 995; ws.editor._scroll()")
    p.frame(3)
    for _ in range(6):
        p.key(_ENTER)
    assert p.a.editor.lines == p.b.editor.lines


@pytest.mark.parametrize("bufs", (1, 2, 3))
@pytest.mark.parametrize("windowed", (False, True))
def test_the_code_tab_draws_the_pythons_pixels(tmp_path, bufs, windowed):
    p = Pair(tmp_path, bufs, windowed)
    _session(p)
    assert p.floors() >= 0.6 * len(p.kinds), p.kinds


@pytest.mark.parametrize("windowed", (False, True))
def test_a_key_step_past_the_foot_lands_as_one_shift(tmp_path, windowed):
    """The editor's top, moved by a key past the view's foot, lands in the
    key's own frame as a shift (`ui_draw.text_aim` before the
    frame begins): no glide frames follow it, and it draws the Python's
    pixels."""
    p = Pair(tmp_path, 2, windowed)
    _code(p)
    a = p.a.editor
    p.run("ws.editor.row = ws.editor.top + ws.editor.ROWS - 1; ws.mark_dirty()")
    p.frame(4)
    top = a.top
    p.both("key_down", moy_input.KEY_DOWN, 0)
    p.frame()
    landed = a.top
    assert landed > top, "the key's frame did not show the caret's line"
    assert p.kinds[-1], "the key's frame drew whole"
    assert p.a.code_layer._host.still(), "the view glides on after the key"
    p.both("key_up", moy_input.KEY_DOWN, 0)
    p.frame(2)
    assert a.top == landed


def test_a_held_keys_steps_draw_each_row_once(tmp_path):
    """A held key's steps past the foot: each frame's floor is the row the
    move uncovered and the rows the caret left and entered, in touching
    pieces. The view grounds them under one fill and lays out a row that
    crosses from one piece into the next once (it was a row twice and a
    page ground under the view's own: 16 fills and up to 21 texts a frame).
    A dearer step needs a reason before this budget moves."""
    p = Pair(tmp_path, 1, False)
    _code(p)
    p.run("ws.editor.row = ws.editor.top + ws.editor.ROWS - 1; ws.mark_dirty()")
    p.frame(4)
    cv = p.root(p.a)
    got = []
    for _ in range(10):
        cv.gate_counts_reset()
        p.both("key_down", moy_input.KEY_DOWN, 0)
        p.frame()
        assert p.kinds[-1], "the step's frame drew whole"
        got.append(tuple(cv.gate_counts()[:2]))
        p.both("key_up", moy_input.KEY_DOWN, 0)
        p.frame()
    budget = [(9, 10), (9, 14), (9, 14), (9, 16), (9, 16), (9, 14), (9, 12), (9, 10), (9, 10),
              (9, 9)]
    over = [(i, g, b) for i, (g, b) in enumerate(zip(got, budget)) if g[0] > b[0] or g[1] > b[1]]
    assert not over, "steps drew more (fills, texts) than their budget: %r" % (over,)


@pytest.mark.parametrize("bufs", (2, 3))
def test_a_whole_frame_mid_drag_leaves_each_buffer_one_catch_up(tmp_path, bufs):
    """On the desk, a frame that draws the Editor whole mid-drag (here the
    pane's own key moved) leaves the buffers behind it lagging the whole
    desk. Each catches up in a whole frame that changed the pane alone, and
    the pane's frames are its own again: one whole frame never becomes a
    gesture of them. The code moves its rows on every frame after the one
    whose key moved, the catch-ups too."""
    p = Pair(tmp_path, bufs, True)
    _code(p)
    d = p.a.editor_desk
    whole = []
    draw = d._draw

    def counted(dt):
        whole.append(p.n)
        draw(dt)
    d._draw = counted
    cx, cy, cw, ch = p.a.wm._wins["menu"].content_rect()
    x, y = cx + cw // 5, cy + 3 * ch // 5
    p.both("touch", x, y)
    p.frame()
    p.both("touch_drag", x, y - 20)
    p.frame()
    del whole[:]
    d._surround = p.b.editor_desk._surround = None
    n = len(p.kinds)
    for k in range(2, 14):
        p.both("touch_drag", x, y - 20 * k)
        p.frame()
    assert whole == list(range(n, n + bufs)), whole
    assert not p.kinds[n] and all(p.kinds[n + 1:]), p.kinds[n:]


@pytest.mark.parametrize("bufs", (1, 2, 3))
def test_the_gutter_widening_draws_whole(tmp_path, bufs):
    p = Pair(tmp_path, bufs, False)
    _gutter_session(p)
    assert p.floors() >= 8, p.kinds


def _config_session(p):
    """theme Config: typing, the caret moving through the JSON, a held key
    scrolling it, Backspace held."""
    p.run("ws.open_app(ws._apps_by_id['appearance'])")
    p.frame(3)
    p.run("assert ws.look.edit_file()")
    p.frame(4)
    assert p.a.menu_view == "theme_config"
    p.held_key(moy_input.KEY_DOWN, 30)
    p.type('"x": 1,')
    p.key(_ENTER)
    p.held_key(moy_input.KEY_UP, 10)
    p.held_key(_BS, 6)
    p.type("ab")
    assert p.a.editor.lines == p.b.editor.lines


@pytest.mark.parametrize("bufs", (1, 2, 3))
@pytest.mark.parametrize("windowed", (False, True))
def test_theme_config_draws_the_pythons_pixels(tmp_path, bufs, windowed):
    p = Pair(tmp_path, bufs, windowed)
    _config_session(p)
    assert p.floors() >= 0.6 * len(p.kinds), p.kinds


# -- the verb: ui.text_view as an app's terminal ---------------------------------------

def _first_diff(a, b):
    i = next(i for i in range(0, len(a), 2) if a[i:i + 2] != b[i:i + 2]) // 2
    return (i % 320, i // 320)


class Term:
    """A terminal on the verb: a scrollback that follows its foot, names in
    SGR colours, an input line with its caret."""

    def __init__(self, n=3):
        self.log = ["\x1b[36mmoy\x1b[0m: hello", "\x1b[32mana\x1b[0m: hi there",
                    "\x1b[90m-- joined --\x1b[0m"][:n]
        self.typed = "ls"
        self.out = None

    def add(self, k=1):
        for _ in range(k):
            n = len(self.log)
            self.log.append("\x1b[3%dmuser%d\x1b[0m: line %d of the log, a little longer than "
                            "most of them" % (1 + n % 6, n % 7, n))

    def draw(self, cv):
        ui.page("Term")
        self.out = ui.text_view("log", self.log, {"rows": 9, "follow": True, "wrap": True})
        ui.text_view("input", ["> " + self.typed], {"rows": 1, "syntax": "plain",
                                                     "caret": (0, 2 + len(self.typed))})
        ui.end()


class TermRig:
    """The terminal drawn twice a frame: on `bufs` buffers presented in turn,
    its frames free to shift and draw rows, and on one buffer, whole."""

    def __init__(self, th, bufs=1):
        from runtime.host_canvas import make_system_canvas
        self.th = th
        self.app = Term()
        self.bufs = bufs
        self.cv = make_system_canvas(320, 240)
        self.cv.RETAINED_FRAMES = bufs
        self.ref = make_system_canvas(320, 240)
        self.a = ui_draw.AppHost()
        self.b = ui_draw.AppHost()
        self.n = 0
        self.held = [bytes(self.cv._buf)]
        self.kinds = []
        self.texts = []

    def frame(self):
        ui_draw.frame_dt(16)
        for host, cv, alone in ((self.a, self.cv, True), (self.b, self.ref, False)):
            ui_draw.paint_frame(self.n, alone)
            cv.gate_counts_reset()
            host.begin(cv, self.th, (0, 0, 320, 240))
            self.app.draw(cv)
            if alone:
                self.kinds.append(ui_draw.change_take())
                self.texts.append(cv.gate_counts()[1])
        self.n += 1
        shot, want = bytes(self.cv._buf), bytes(self.ref._buf)
        assert shot == want, "frame %d (%s, %d buffers) differs from a whole draw at %s" % (
            self.n, self.kinds[-1], self.bufs, _first_diff(shot, want))
        self.held.append(shot)
        if self.bufs > 1:
            self.cv._comp.last = shot
            self.cv._buf[:] = self.held[-self.bufs] if len(self.held) > self.bufs else self.held[0]

    def point(self, x, y, click, down):
        self.a.point(x, y, click, down)
        self.b.point(x, y, click, down)


@pytest.fixture
def th(tmp_path):
    from ws_helpers import build_ws
    return build_ws(tmp_path).theme_colors


@pytest.mark.parametrize("bufs", (1, 2, 3))
def test_a_terminal_appends_follows_and_scrolls(th, bufs):
    """Lines come while the view is at its foot: it glides down to the new
    foot; a drag scrolls the scrollback up and its release coasts; typed
    keys change the input line alone. Every frame is a whole draw's."""
    r = TermRig(th, bufs)
    for _ in range(3):
        r.frame()
    for _ in range(12):                 # lines come: the view follows its foot
        r.app.add()
        for _ in range(4):
            r.frame()
    top = r.app.out[0]
    assert top > 0, "the view did not follow its foot"
    r.point(160, 60, True, True)        # a drag down the scrollback: older lines
    r.frame()
    for y in range(70, 140, 10):
        r.point(160, y, False, True)
        r.frame()
    r.point(160, 140, False, False)
    for _ in range(30):
        r.frame()
    assert r.app.out[0] < top, "the drag did not scroll the scrollback"
    for ch in "la":                     # typing on the input line
        r.app.typed += ch
        r.frame()
    r.app.add(2)                        # lines come while scrolled up: it stays
    held = r.app.out[0]
    for _ in range(4):
        r.frame()
    assert r.app.out[0] == held
    floors = sum(1 for k in r.kinds if k in ("rows", "shift"))
    assert floors >= len(r.kinds) // 2, r.kinds


def test_a_line_added_below_the_view_draws_no_history(th):
    """While the view is scrolled up, a line added below it draws none of the
    history the view shows: the frame's text is the page's title and the
    input line, drawn every frame outside the region, and the rows under
    the foot's fade, which the rows' growth moved -- two where the view
    stands part way down a row, three colour runs each."""
    r = TermRig(th, 1)
    r.app.add(30)
    for _ in range(40):
        r.frame()
    r.point(160, 60, True, True)
    r.frame()
    for y in range(80, 200, 20):
        r.point(160, y, False, True)
        r.frame()
    r.point(160, 200, False, False)
    for _ in range(60):
        r.frame()
    r.texts.clear()
    r.app.add()
    r.frame()
    assert r.texts[-1] <= 2 + 2 * 3, r.texts


def _cart_pixels(ws):
    ns = ws.player.ns
    cv = ns["screen"]()
    x, y, w, h = ns["content_rect"]()
    buf = cv._buf
    out = bytearray()
    for j in range(y, y + h):
        out += buf[2 * (j * cv.w + x):2 * (j * cv.w + x + w)]
    return bytes(out)


def _cart_pmem(ws):
    lua = getattr(ws.player, "_lua", None)
    if lua is not None and hasattr(lua._run, "pmem"):
        return list(lua._run.pmem()[1])[:3]
    if lua is not None:
        import sys
        from array import array
        img = array("i", bytearray(4 * 256))
        sys.modules[type(lua).__module__]._moycore.pmem_image(img)
        return list(img)[:3]
    pm = ws.player.ns["pmem"]
    return [pm(i) for i in range(3)]


def test_the_terminal_is_one_app_in_python_and_lua(tmp_path):
    """tests/fixtures/ui_term's terminal, in Python and in Lua: lines come and
    the view follows them, a drag scrolls it back; the two answer the same
    lines shown every frame, and draw the same pixels wherever nothing
    moves (a glide's or a coast's steps are each runtime's clock: the
    Python app's the loop's tick, the Lua run's its logic ticks')."""
    import shutil
    from ws_helpers import make_drv, open_cart
    root = str(tmp_path / "carts")
    host_app.moy_carts.ensure_dirs(root)
    for d in ("term_py.moy", "term_lua.moy"):
        shutil.copytree(str(ROOT / "tests" / "fixtures" / "ui_term" / d),
                        str(Path(root) / d))
    runs = {}
    for name, title in (("python", "UI Term Py"), ("lua", "UI Term Lua")):
        ws = host_app.build_workstation(root)
        ws.look.set_theme("moy", persist=False, variant="dark")
        ws.pointer.visible = False
        open_cart(ws, title)
        drv = make_drv(ws)
        shots = []
        for k in range(130):
            if k == 70:
                drv.touch(160, 60)
            elif 70 < k < 77:
                drv.touch_drag(160, 60 + (k - 70) * 12)
            elif k == 77:
                drv.touch_up()
            quiesce(ws)
            ws._dirty = True
            drv.frame(1 / 30)
            assert ws.player.cart_error is None, (name, ws.player.cart_error)
            shots.append((_cart_pixels(ws), _cart_pmem(ws)))
        runs[name] = shots
        ws.exit()
    py, lua = runs["python"], runs["lua"]
    for k in range(len(py)):
        assert py[k][1][0] == lua[k][1][0], (k, py[k][1], lua[k][1])
    for k in list(range(6)) + [69, 129]:          # before the lines come; settled
        assert py[k][1] == lua[k][1], (k, py[k][1], lua[k][1])
        assert py[k][0] == lua[k][0], "frame %d: the Lua terminal drew other pixels" % k
    assert py[69][1][1] > 0, "the view did not follow its lines"
    assert py[129][1][1] < py[69][1][1], "the drag did not scroll it back"


# -- a note's caret revealed (ui_draw.text_reveal, Notes' EditorHandle.aim) ----------


class Note:
    """A wrapped note on the verb, its caret where the test puts it."""

    def __init__(self):
        self.lines = ["Line %d of the note, with words enough to wrap past the edge" % i
                      for i in range(40)]
        self.caret = (0, 0)
        self.out = None

    def draw(self, cv):
        ui.page("Note")
        self.out = ui.text_view("note", self.lines, {"rows": 8, "wrap": True,
                                                     "syntax": "plain", "caret": self.caret})
        ui.end()


class NoteRig(TermRig):
    def __init__(self, th, bufs=1):
        TermRig.__init__(self, th, bufs)
        self.app = Note()

    def reveal(self, line, col):
        got = [ui_draw.text_reveal(h, "note", self.app.lines, line, col)
               for h in (self.a, self.b)]
        assert got[0] == got[1]
        return got[0]

    def ends(self):
        """The (line, column) the view's top and foot pixel rows show, as
        its last pass drew it."""
        got = [ui_draw.text_at(self.a, "note", 40, y) for y in range(240)]
        shown = [g for g in got if g is not None]
        return shown[0], shown[-1]


@pytest.mark.parametrize("bufs", (1, 2, 3))
def test_a_caret_revealed_moves_the_view_only_as_far_as_its_row(th, bufs):
    """A wrapped note's caret, put on a row under the view, brings the view
    down until that row is its last, as the frame's shift; a caret it shows
    already moves nothing; a line asked for first (col -1) shows its first
    row at the top; and a caret above the view brings its row to the top.
    Every frame is a whole draw's."""
    r = NoteRig(th, bufs)
    for _ in range(3):
        r.frame()
    assert r.app.out[0] == 0
    n = len(r.app.lines[4])
    r.app.caret = (4, n)                # the last row of line 4: just under the view
    assert r.reveal(4, n)
    r.frame()
    assert r.kinds[-1] == "shift", r.kinds
    top, foot = r.ends()
    assert foot[0] == 4 and foot[1] > 40, "its row, line 4's second, is the view's last"
    assert top[0] > 0
    assert not r.reveal(4, n), "a caret the view shows moves nothing"
    r.frame()
    assert r.reveal(20, -1)             # line 20 first
    r.frame()
    assert r.ends()[0][0] == 20 and r.ends()[0][1] < 40 and r.app.out[0] == 20
    assert r.reveal(2, 0)               # a caret above: its row at the top
    r.frame()
    assert r.ends()[0][0] == 2 and r.ends()[0][1] < 40
