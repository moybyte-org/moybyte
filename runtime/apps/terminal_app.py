"""The TERMINAL -- the C shell's screen face (docs/os_2026-11/system.md section 1).

The Terminal system app, a session of native/moy_sh whose output fills a cell
grid in C (`moy_sh.Screen`: native/moy_sh/moy_sh_screen.h, the VT100 subset of
moy_term.h), drawn here through `ui.text_view` and fed by a prompt line the
shared `CodeEditor` edits. The grid's lines are the log; the prompt is a
one-row text view under it, its caret the view's. A line is echoed into the
grid (the prompt dim, what was typed in `value`) and run; a run holds the VM's
thread until its commands end, and Ctrl-C reaches it in C (the screen face's
poll reads Ctrl+C, the T-Deck's Mic or a gamepad's B from the input latches).

Tab completes the word left of the caret: a command's name from the shell's
built-ins (moy_sh_complete), a path's from its folder. Left and right move the
caret, up and down step through the lines run before. A desk has a keyboard
and draws no key row. A handheld draws a KEY ROW under the prompt, and a
completion with more than one candidate shows them there to pick:
- the T-Deck's (its keyboard's hint input, whatever the last input was) holds
  only what its keyboard cannot type: Tab and `TDECK_SYMBOLS`. Its trackball
  moves the caret and walks the history, Enter runs and Mic stops, so those
  keys are not on it. Down past the newest line enters the row: the trackball
  then moves along it, Enter presses a key, and up (or a typed key) goes back
  to the prompt;
- a gamepad's and a finger's hold every key the line needs (Tab, Ctrl-C, the
  arrows, Run), which is how they write a command, and under touch a row of
  the shell's symbols sits under it.

The grid takes the log view's columns and rows, and its lines are taken when
its generation moves, the prompt row made again when what it shows does: an
idle frame makes nothing.
"""

from array import array as _array

try:
    from editors_code import CodeEditor
    import ui as _ui
    import ui_draw as _ui_draw
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime.editors_code import CodeEditor
    from runtime import ui as _ui
    from runtime import ui_draw as _ui_draw

try:
    import layout_base as _lb
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime import layout_base as _lb

try:
    import moy_sh as _sh
    _sh.Screen
except (ImportError, AttributeError):   # a folder of that name on CPython's path
    try:                    # CPython: the ctypes binding (runtime/host/moy_sh.py)
        from runtime import moy_sh as _sh
    except ImportError:     # the browser and the desktop MicroPython: no C shell
        _sh = None

_DONE, _MORE, _EXIT = 0, 1, 2
_INTERRUPTED = 130
_HISTORY = 64
_CANDS = 24                 # the candidates a key row offers, at most
_KEYS = ("Tab", "^C", "left", "right", "up", "down", "Run")
_SIGNS = {"left": "←", "right": "→", "up": "↑", "down": "↓"}
_ASCII = {"left": "<", "right": ">", "up": "^", "down": "v"}
SYMBOLS = "|><&;$~/-*\"'"
TDECK_SYMBOLS = "|><&~\\{}[]="  # the shell's symbols the T-Deck's keyboard has no key for
_TDECK_ROW = ("Tab",) + tuple(TDECK_SYMBOLS)
_ROW_HINTS = ("confirm", "Press")
_HINTS = {
    "keys": ("confirm", "Run", "Tab", "Complete", "Ctrl C", "Stop"),
    "tdeck": ("confirm", "Run", "cancel", "Stop"),
    "pad": ("confirm", "Press", "back", "Back"),
}
_ECHO = "\x1b[2m"
_TYPED = "\x1b[0;36m"
_PLAIN = "\x1b[0m"
_SEPS = " ;|&()<>"
_MIN_COLS = 8


class TerminalLayout:
    """The terminal's page: the content rect, the hint band, the key rows."""

    MIN_W = 200
    MIN_H = 120

    def __init__(self, w, h, fs=1, windowed=False, cs=None):
        self.w, self.h = int(w), int(h)
        self.fs = max(1, int(fs))
        self.cs = max(self.fs, int(cs)) if cs else self.fs
        self.desk = _lb.metric_class() == _lb.DESK
        self.windowed = bool(windowed)
        self.cx, self.cy, self.cw, self.ch = _lb.content_of(
            self.w, self.h, 0 if windowed else _lb.bar_rows(self.cs))
        self.key_h = (32 if self.desk else 22) * self.fs


class TerminalAppLayer:
    """The Terminal: the C shell's screen face (see the section above)."""

    id = "terminal"
    domain = "system"
    TITLE = "Terminal"
    NEEDS = ("surface", "theme", "damage", "install")
    UI = True

    def __init__(self, ctx, names):
        self.ctx = ctx
        self._surf = ctx.surface
        self._theme = ctx.theme
        self._damage = ctx.damage
        self._install = ctx.install
        self._ui = ctx.ui
        self.scr = None             # the session, made at the first open
        self.ed = None              # the prompt's editor, likewise
        self.lines = []             # the grid's lines as the log's view takes them
        self._oldest = 0
        self._gen = -1
        self._v = _array("i", [0, 0, 0])
        self._cur = _array("i", [0, 0, 0])
        self._log = _ui.text_options({"follow": True})
        self._line = _ui.text_options({"caret": (0, 0)})
        self._into = _array("i", [0, 0])
        self._prompt_row = [""]
        self._shown = None          # (prompt, text, caret) the prompt row was made of
        self._key = 0
        self._swallow = False       # the Ctrl-C that stopped a run, still latched
        self.more = False
        self.history = []
        self._hi = 0
        self.cands = []             # a completion's candidates, for the key row
        self.in_row = False         # the T-Deck's keys on its key row, not the prompt
        self._enter = False         # a typed Enter owed to the row's focused key
        self.cols, self.rows = 40, 12
        sc = self._surf.canvas()
        self.layout = TerminalLayout(sc.w, sc.h, getattr(sc, "font_scale", 1),
                                     self._surf.windowed(), self._surf.chrome_scale())

    # -- the app protocol (docs/app_api_v1.md) ------------------------------------

    @staticmethod
    def is_app(cart):
        if (not cart or cart.get("title") != "Terminal"
                or "terminal" not in (cart.get("permissions") or ())):
            return False
        path = cart.get("path")
        if not path:
            return int(cart.get("version", 0)) >= 1
        try:
            from moy_store_base import builtin_name
        except ImportError:  # host: the runtime package
            from runtime.moy_store_base import builtin_name
        return builtin_name(path) == "moybyte.terminal.moy"

    def open(self):
        if self.ed is None:
            self.ed = CodeEditor("", cols=40, rows=1)
        self.in_row = False
        self._ui.reset()
        self._gen = -1
        self.ensure()
        self._damage.all()

    def relayout(self, w, h, fs, cs=None):
        self.layout = TerminalLayout(w, h, fs, self._surf.windowed(), cs)

    def close_session(self):
        """The session given back, its arena and grid with it: the next open
        makes a new one."""
        if self.scr is not None:
            self.scr.close()
            self.scr = None
        self.lines = []
        self._oldest = 0
        self._gen = -1
        self.more = False
        self.cands = []

    def ensure(self):
        """The session, made the first time it is needed: None where this
        console has no C shell (the browser)."""
        if self.scr is None and _sh is not None:
            try:
                if getattr(_sh, "HOST", False):
                    self.scr = _sh.Screen(self.cols, self.rows, _host_root(self._install.root()))
                else:
                    self.scr = _sh.Screen(self.cols, self.rows)
            except OSError:
                self.scr = None
        return self.scr

    # -- the session ----------------------------------------------------------------

    def prompt(self):
        s = self.scr
        return s.prompt(self.more) if s is not None else ""

    def _fresh_line(self):
        s = self.scr
        s.cursor(self._cur)
        if self._cur[1]:
            s.write("\n")

    def _echo(self, text):
        self._fresh_line()
        self.scr.write(_ECHO + self.prompt() + _TYPED + text + _PLAIN + "\n")

    def submit(self):
        """Enter: the line echoed into the grid and run."""
        s = self.ensure()
        if s is None:
            return
        text = self.ed.text()
        self.ed.set_text("")
        self.cands = []
        self._echo(text)
        if text.strip() and (not self.history or self.history[-1] != text):
            self.history.append(text)
            if len(self.history) > _HISTORY:
                self.history.pop(0)
        self._hi = len(self.history)
        r = s.run(text + "\n")
        self._fresh_line()
        self.more = r == _MORE
        if r == _EXIT:
            code = s.status()
            s.close()
            self.scr = None
            self.more = False
            self.ensure()
            if self.scr is not None:
                self.scr.write("%s[the shell exited %d]%s\n" % (_ECHO, code, _PLAIN))
        elif s.status() == _INTERRUPTED:
            self._swallow = True
        self._gen = -1
        self._damage.all()

    def interrupt(self):
        """Ctrl-C at the prompt: the line dropped, and what `more` held."""
        s = self.ensure()
        if s is None:
            return
        self._fresh_line()
        s.write(_ECHO + self.prompt() + _TYPED + self.ed.text() + _PLAIN + "^C\n")
        s.cancel()
        self.ed.set_text("")
        self.more = False
        self.cands = []
        self._hi = len(self.history)
        self._damage.all()

    def recall(self, d):
        """Up (-1) or down (+1) through the lines run before."""
        n = len(self.history)
        if not n:
            return
        self._hi = max(0, min(n, self._hi + d))
        self.ed.set_text(self.history[self._hi] if self._hi < n else "")
        self.ed.move(0, len(self.ed.text()))
        self.cands = []

    def complete(self):
        """Tab: the word left of the caret completed, or its candidates."""
        s = self.ensure()
        if s is None:
            return
        text, col = self.ed.text(), self.ed.col
        head = text[:col]
        start = col
        while start > 0 and head[start - 1] not in _SEPS:
            start -= 1
        word = head[start:]
        before = head[:start].rstrip(" ")
        if "/" not in word and (not before or before[-1] in _SEPS):
            names = sorted(set(s.complete(word)))
            stem = ""
        else:
            stem = word[:word.rfind("/") + 1]
            names = [stem + n for n in _entries(s, self._root(), stem, word[len(stem):])]
        if not names:
            return
        if len(names) == 1:
            end = "" if names[0].endswith("/") else " "
            self._insert(names[0][len(word):] + end)
            return
        common = _common(names)
        if len(common) > len(word):
            self._insert(common[len(word):])
            return
        self._echo(text)
        s.write("  ".join(n[len(stem):] for n in names) + "\n")
        self.cands = [(n, len(word), n[len(stem):]) for n in names[:_CANDS]]
        self._gen = -1

    def pick(self, i):
        """A candidate from the key row: the rest of its word typed."""
        name, at, _label = self.cands[i]
        self.cands = []
        self._insert(name[at:] + ("" if name.endswith("/") else " "))

    def _insert(self, s):
        for ch in s:
            self.ed.insert(ch)
        self.cands = []

    def _root(self):
        return _host_root(self._install.root()) if getattr(_sh, "HOST", False) else ""

    def key(self, code):
        """One typed key."""
        if code == 3:
            if self._swallow:
                self._swallow = False
            else:
                self.interrupt()
            return True
        self._swallow = False
        if code in (0x0D, 0x0A):
            self.submit()
            return True
        if code == 0x09:
            self.complete()
            return True
        if self.ed.key(code):
            self.cands = []
            return True
        return False

    def press(self, name):
        """A key of the key row."""
        if name == "Tab":
            self.complete()
        elif name == "^C":
            self.interrupt()
        elif name == "Run":
            self.submit()
        elif name in ("left", "right"):
            self.ed.move(0, -1 if name == "left" else 1)
        elif name in ("up", "down"):
            self.recall(-1 if name == "up" else 1)
        else:
            self._insert(name)
        self._damage.all()

    # -- input --------------------------------------------------------------------

    def row_kind(self):
        """Which key row the page draws: None (a desk, a keyboard), "tdeck",
        "pad" or "touch"."""
        if self.layout.desk:
            return None
        if _ui_draw.hint_kind() == "tdeck":
            return "tdeck"
        kind = _ui.input_kind()
        return None if kind == "keys" else kind

    def handle_input(self, inp):
        k = getattr(inp, "last_key", 0) or 0
        if not isinstance(k, int):
            k = ord(k[0]) if k else 0
        row = self.row_kind()
        if row != "tdeck":
            self.in_row = False
        hit = False
        enter = False
        if k and k != self._key:
            if self.in_row and k in (0x0D, 0x0A):
                enter = self._enter = True  # the draw presses the row's focused key
                hit = True
            else:
                if self.in_row:
                    self.in_row = False
                hit = self.key(k)
        self._key = k
        n = getattr(inp, "key_repeats", 0)
        if n:
            r = inp.repeat_key
            if r in (8, 127) or 32 <= r < 127:
                for _ in range(n):
                    hit = self.ed.key(r) or hit
        if row == "pad":
            if self._ui.keys(inp):
                hit = True
        elif self.in_row:
            if inp.steps("up"):
                self.in_row = False
                hit = True
            elif not enter and self._ui.keys(inp):
                hit = True
        else:
            dx = inp.steps("right") - inp.steps("left")
            dy = inp.steps("down") - inp.steps("up")
            if dx:
                self.ed.move(0, dx)
                hit = True
            if dy > 0 and row == "tdeck" and self._hi >= len(self.history):
                self.in_row = True
                hit = True
            elif dy:
                self.recall(1 if dy > 0 else -1)
                hit = True
        if hit:
            self._damage.all()
        return True

    def handle_pointer(self, px, py, click):
        if self._ui.point(px, py, click, bool(getattr(self._surf.pointer(), "down", False))):
            self._damage.all()
        return True

    # -- the surface ----------------------------------------------------------------

    def _take(self):
        """The grid's lines into the log's, those that changed alone."""
        s = self.scr
        if s is None:
            if self._gen != -2:
                self.lines = ["No shell on this console."]
                self._gen = -2
                return True
            return False
        g = s.gen()
        if g == self._gen:
            return False
        self._gen = g
        v = self._v
        s.take(v)
        base, count, low = v[0], v[1], v[2]
        c = self._cur
        s.cursor(c)
        if count > 1 and c[0] == count - 1 and c[1] == 0:
            count -= 1                  # the cursor's empty line: the prompt row is it
        lines = self.lines
        if base > self._oldest:
            del lines[:min(len(lines), base - self._oldest)]
        self._oldest = base
        if len(lines) > count:
            del lines[count:]
        first = max(0, min(low - base, len(lines)))
        for i in range(first, count):
            t = s.line(i)
            if i < len(lines):
                lines[i] = t
            else:
                lines.append(t)
        return True

    def _prompt_line(self, cols):
        """The prompt row and its caret's column, the row made again only
        when what it shows moved."""
        p = self.prompt()
        t = self.ed.text()
        c = self.ed.col
        key = self._shown
        if key is None or key[0] != p or key[1] != t or key[2] != c or key[3] != cols:
            at = len(p) + c
            left = max(0, at - cols + 2) if cols > 2 else 0
            self._prompt_row[0] = _ECHO + p + _TYPED + t + _PLAIN
            self._line[_ui.TXO_CARET] = 0
            self._line[_ui.TXO_CARET + 1] = at
            self._line[_ui.TXO_LEFT] = left
            self._shown = (p, t, c, cols)
        return self._prompt_row

    def draw(self, dt):
        cv = self._surf.canvas()
        lay = self.layout
        th = self._theme.colors()
        fs = lay.fs
        rk = self.row_kind()
        self._ui.begin(cv, th, (0, 0, cv.w, cv.h), lay.desk, lay.windowed,
                       rk != "pad" and not self.in_row)
        ui = _ui
        ui.page("")
        page = (lay.cx, lay.cy, lay.cw, lay.ch)
        hints = _ROW_HINTS if self.in_row else _HINTS.get(ui.input_kind())
        if hints and not lay.desk and ui.hints_width(hints) > 0:
            band, page = ui.cut_bottom(page, 20 * fs)
            ui.at(*band)
            ui.hints(hints)
        if rk is not None:
            if rk == "touch":
                row, page = ui.cut_bottom(page, lay.key_h)
                self._row(row, SYMBOLS, None)
            row, page = ui.cut_bottom(page, lay.key_h)
            labels = [label for _n, _at, label in self.cands] if self.cands \
                else _TDECK_ROW if rk == "tdeck" else _KEYS
            self._row(row, labels, bool(self.cands))
            if self._enter:
                self._enter = False
                at = ui.focus()
                if self.in_row and at in labels:
                    if self.cands:
                        self.pick(labels.index(at))
                    else:
                        self.press(at)
                    self._ui.again()
        fid, cell, line, _asc = _ui_draw.font("mono")
        fw = max(1, cell * fs)
        pad = 8 * fs
        cols = max(_MIN_COLS, (page[2] - pad) // fw)
        lh = (line + 2) * fs
        band, log = ui.cut_bottom(page, lh + 4 * fs)
        moved = self._take()
        if moved:
            _ui_draw.text_aim(self._ui, "log", -1, len(self.lines))
        ui.at(*log)
        got = ui.text_view("log", self.lines, self._log, None, None, self._into)
        rows = max(1, got[1])
        ui.at(*band)
        ui.text_view("prompt", self._prompt_line(cols), self._line, None, None, self._into)
        if self.scr is not None and (cols != self.cols or rows != self.rows):
            if self.scr.resize(cols, rows):
                self.cols, self.rows = cols, rows
                self._gen = -1
                self._ui.again()
        self.ed.set_view_size(cols, 1)

    def _row(self, rect, labels, cands):
        """A row of keys across `rect`: the key row, the candidates, the
        symbols."""
        n = len(labels)
        if not n:
            return
        fs = self.layout.fs
        gap = 2 * fs
        if cands:
            n = min(n, max(1, rect[2] // (24 * fs)))
            cells = _ui.hsplit(rect, n, gap=gap)
        else:
            # a key of a word (Tab, Run) two units wide, a glyph's one
            units = 0
            for name in labels:
                units += 2 if len(name) > 2 else 1
            unit = (rect[2] - gap * (n - 1)) // units
            cells = []
            rest = rect
            for i in range(n):
                w = unit * (2 if len(labels[i]) > 2 else 1)
                if i == n - 1:
                    w = rest[2]
                cell, rest = _ui.cut_left(rest, w)
                cells.append(cell)
                if i < n - 1:
                    rest = _ui.cut_left(rest, gap)[1]
        signs = _SIGNS if _ui.has_symbols() else _ASCII
        for i in range(len(cells)):
            name = labels[i]
            _ui.at(*cells[i])
            if _ui.button(signs.get(name, name)):
                if cands:
                    self.pick(i)
                    self._damage.all()
                else:
                    self.press(name)


def _host_root(carts_root):
    """The files tree a host session sees: the folder whose `sd/moybyte/carts`
    is the carts root, as on a board, else the carts root's own folder."""
    r = (carts_root or "").rstrip("/")
    tail = "/sd/moybyte/carts"
    if r.endswith(tail):
        return r[:-len(tail)] or "/"
    i = r.rfind("/")
    return r[:i] if i > 0 else r


def _common(names):
    p = names[0]
    for n in names[1:]:
        i = 0
        while i < len(p) and i < len(n) and p[i] == n[i]:
            i += 1
        p = p[:i]
    return p


def _entries(s, root, stem, part):
    """The names in shell folder `stem` (against the session's folder)
    starting with `part`, a folder's ending in `/`."""
    import os
    folder = stem if stem.startswith("/") else (s.cwd().rstrip("/") + "/" + stem)
    path = root + (folder.rstrip("/") or "/") if root else (folder.rstrip("/") or "/")
    out = []
    try:
        if hasattr(os, "ilistdir"):
            for e in os.ilistdir(path):
                if e[0].startswith(part):
                    out.append(e[0] + ("/" if e[1] == 0x4000 else ""))
        else:
            for n in os.listdir(path):
                if n.startswith(part):
                    out.append(n + ("/" if os.path.isdir(os.path.join(path, n)) else ""))
    except OSError:
        return []
    return sorted(out)
