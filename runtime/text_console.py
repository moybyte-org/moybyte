"""The TEXT CONSOLE -- a script's screen (step 6 of docs/text_editing_2026-09.md).

A script is a cart with no folder, and this is the surface it runs on: a
bounded scrollback that `print()` appends to, and a prompt line the shared
`CodeEditor` edits so typing here is the typing the Code tab already teaches --
the T-Deck keyboard, and the same tappable symbol palette on a board whose
keyboard has no `= [ ] { } < > %`.

The shell owns the pixels, not the cart. A script never gets a `_draw`: the
Player takes `TextConsole.draw` as the run's draw hook (`Workstation.run_script`),
so a script cannot paint over the prompt it is reading from, and the surface
composites through the ordinary running-cart path every other cart uses.

## How `input()` works, and why

A script is `type: "script"`, which is tool-shaped: ONE tick per loop frame,
unpaced. The loop cannot block, so `input()` cannot either. It is a POLL:

    name = input("what is your name? ")   # None until a line is entered

The prompt is shown once and stays on the prompt line; every tick until the
person presses enter, `input()` answers None; the tick it lands, it answers the
line -- once -- and the console echoes `prompt + line` into the scrollback. A
script that only prints needs no `_update` at all (its body runs once, like any
cart's); a script that reads writes an `_update` and checks for None.

`_update` MAY be a generator function, which is the linear way to write the
same thing: the driver `next()`s it once per frame, so a bare `yield` means
"wait a frame", and the script ENDS when the generator does.

    def _update(dt):
        name = None
        while name is None:
            name = input("what is your name? ")
            yield
        print("hello " + name)

## The scrollback is a RING

`LINES` rows, preallocated, wrapped to the view's columns AS THEY ARE WRITTEN.
Nothing grows: the oldest row is overwritten, and a resize does not reflow rows
that are already in the ring (a terminal does not either) -- new writes take
the new width.

## What the terminal (#115) inherits

All of it. `on_line` is the seam: set it and every entered line goes to that
handler instead of an `input()` call, which is what a REPL prompt is. `write`
is its scrollback, `draw` is its surface, and `api()` is what `run` hands a
script it starts.
"""

try:
    from editors import CodeEditor, KeyEdge
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime.editors import CodeEditor, KeyEdge

try:
    from code_layer import symbols_for, draw_symbol_keys
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime.code_layer import symbols_for, draw_symbol_keys


# Lua has no `input` of its own and libmoy owns `print` (the DRAW verb), so a
# registered Python `print` would never reach the cart -- lua_ext.LIBMOY_VERBS
# denies it by name, correctly. The console's two verbs therefore register
# under reserved names and this prelude binds them over the top, in the same
# window PRELUDE_HANDLES uses: after registration, before the cart loads. Both
# Lua glues (runtime/lua_host.py, device/moycore_glue.py) exec whatever
# `_moy_prelude` the namespace carries, so this is one string, not two.
LUA_PRELUDE = """
do
  local say, ask = __moy_say, __moy_ask
  print = function(...)
    local n = select("#", ...)
    local parts = {}
    for i = 1, n do parts[i] = tostring((select(i, ...))) end
    say(table.concat(parts, " "))
  end
  input = function(prompt) return ask(prompt) end
end
"""

# A capability a script's manifest does not name has no name in its namespace
# (runtime/system_api.py). That is a NameError inside the cart, which is the
# right mechanism and the wrong words for a kid, so these names get a sentence
# instead when one escapes into the console.
GATED = {
    "wifi": "the internet",
    "net": "playing with a friend",
    "carts": "making carts",
    "artwork": "the drawing store",
    "shell": "the console itself",
}


class TextConsole:
    """The script surface: a scrollback ring, a prompt line, and the two verbs
    a script talks to them with."""

    LINES = 200          # scrollback capacity, in WRAPPED rows
    MIN_COLS = 8
    MIN_ROWS = 2

    def __init__(self, ws, names):
        self.ws = ws
        self.NAMES = names
        self.cols = 38
        self.rows = 16
        self._cap = self.LINES
        self._buf = [""] * self._cap
        self._start = 0
        self._n = 0
        self._open = False        # the newest row is unterminated (print(end=""))
        self.back = 0             # rows scrolled up from the bottom
        self.ed = CodeEditor("", cols=self.cols, rows=1)
        self._ekey = KeyEdge()
        self._armed = False       # a script is waiting on a line
        self._prompt = ""
        self._answer = None       # the entered line, until the script takes it
        self.on_line = None       # #115: a REPL takes every line instead
        self.title = ""
        self._lang = "python"
        self._drag = None         # (y, back) while a finger scrolls the log
        self._sym_rects = ()      # built by draw(), read by the tick's pointer

    # -- the ring -------------------------------------------------------------

    def clear(self):
        for i in range(self._cap):
            self._buf[i] = ""
        self._start = 0
        self._n = 0
        self._open = False
        self.back = 0

    def count(self):
        return self._n

    def row(self, i):
        """Row `i`, oldest first."""
        if i < 0 or i >= self._n:
            return ""
        return self._buf[(self._start + i) % self._cap]

    def text(self):
        """The whole scrollback as one string -- what a test reads."""
        return "\n".join(self.row(i) for i in range(self._n))

    def _push(self, row):
        if self._n < self._cap:
            self._buf[(self._start + self._n) % self._cap] = row
            self._n += 1
            return
        self._buf[self._start] = row
        self._start = (self._start + 1) % self._cap

    def _last(self):
        return self._buf[(self._start + self._n - 1) % self._cap]

    def _set_last(self, row):
        self._buf[(self._start + self._n - 1) % self._cap] = row

    def _append(self, seg):
        """Add `seg` to the open row, opening rows as it fills them."""
        while seg:
            if not self._open or self._n == 0:
                self._push("")
                self._open = True
            room = self.cols - len(self._last())
            if room <= 0:
                self._open = False        # the row is full -> wrap
                continue
            self._set_last(self._last() + seg[:room])
            seg = seg[room:]

    def write(self, text):
        """Append raw text (newlines break rows, everything wraps at `cols`)."""
        text = str(text)
        i = 0
        n = len(text)
        while i <= n:
            j = text.find("\n", i)
            self._append(text[i:] if j < 0 else text[i:j])
            if j < 0:
                break
            if not self._open:
                self._push("")            # a bare newline is a blank row
            self._open = False
            i = j + 1
        self.back = 0                     # writing follows the tail
        self._dirty()

    # -- the prompt -----------------------------------------------------------

    def reading(self):
        """True when the console is taking a line -- a script inside `input()`,
        or a REPL with an `on_line` handler."""
        return self._armed or self.on_line is not None

    def ask(self, prompt=""):
        """`input()`: the line, or None until one is entered."""
        if self._answer is not None:
            line = self._answer
            self._answer = None
            self._armed = False
            self._prompt = ""
            return line
        if not self._armed:
            self._armed = True
            self._prompt = str(prompt)
            self.ed.set_text("")
            self._dirty()
        return None

    def submit(self):
        """Enter: echo the line, then hand it to `on_line` or to `input()`."""
        line = self.ed.text()
        self.write(self._prompt + line + "\n")
        self.ed.set_text("")
        if self.on_line is not None:
            self._armed = False
            self._prompt = ""
            self.on_line(line)
        elif self._armed:
            self._answer = line
        return line

    def seed_key(self, code):
        """Swallow the byte that opened this surface. Taking the keyboard is a
        screen change and the key that caused it is still in `last_key` when
        the first frame runs -- the code editor's rule (`_set_text_mode`),
        which the prompt needs for exactly the same reason."""
        self._ekey.prev = code or 0

    def key(self, code):
        """One keyboard byte. Enter submits; everything else edits the line."""
        if not code or not self.reading():
            return False
        if code in (0x0D, 0x0A):
            self.submit()
            return True
        if self.ed.key(code):
            self._dirty()
            return True
        return False

    def scroll(self, rows):
        """Scroll the log by `rows` (positive = back through history)."""
        top = max(0, self._n - self.rows)
        self.back = max(0, min(top, self.back + int(rows)))
        self._dirty()

    # -- the run --------------------------------------------------------------

    def start(self, title, lang="python"):
        """A fresh run: empty scrollback, no pending line, no REPL handler."""
        self.clear()
        self.ed.set_text("")
        self._armed = False
        self._prompt = ""
        self._answer = None
        self.on_line = None
        self.title = str(title)
        self._lang = lang
        self._ekey = KeyEdge()
        self._drag = None

    def api(self):
        """The names a cart with the `console` permission gets. `print` SHADOWS
        the draw verb of the same name: a script's screen is this, not a
        raster, and both tiers say so with the same word."""
        def _print(*args, **kw):
            sep = kw.get("sep", " ")
            end = kw.get("end", "\n")
            self.write(sep.join(str(a) for a in args) + end)

        def _input(prompt=""):
            return self.ask(prompt)

        return {"print": _print, "input": _input,
                # Lua: registered under reserved names, bound by the prelude.
                "__moy_say": lambda s="": self.write(str(s) + "\n"),
                "__moy_ask": lambda prompt="": self.ask(prompt or ""),
                "_moy_prelude": LUA_PRELUDE}

    def driver(self, update):
        """Wrap the script's `_update` as the shell-owned tick.

        Every frame it pumps the keyboard and the pointer FIRST -- a script that
        never ticks (a body that only printed) still gets a live prompt -- then
        runs the script. A generator `_update` is stepped once per frame and
        ends the run when it finishes; a raise is printed into the console
        rather than thrown at the Player, because the console IS this run's
        error surface (there is no folder to open in the Editor)."""
        state = [update, None]        # [callable, live generator]

        def _step(gen):
            try:
                next(gen)
            except StopIteration:
                state[0] = state[1] = None
            except Exception as exc:  # noqa: BLE001 -- into the console
                state[0] = state[1] = None
                self.crash(exc)

        def _tick(dt):
            self.pump()
            fn, gen = state
            if gen is not None:
                _step(gen)
                return
            if fn is None:
                return
            try:
                got = fn(dt)
            except Exception as exc:  # noqa: BLE001 -- into the console
                state[0] = None
                self.crash(exc)
                return
            if hasattr(got, "__next__"):
                # A GENERATOR `_update`. Calling it ran none of its body, so
                # its first step belongs to THIS frame -- otherwise a script
                # whose first line is an `input()` shows no prompt until the
                # frame after the one that started it.
                state[0] = None
                state[1] = got
                _step(got)
        return _tick

    def crash(self, exc):
        """A script's failure, in the console. `exc` may be an exception or the
        text the Player already turned one into."""
        text = exc if isinstance(exc, str) else _err_text(exc)
        name = _missing_name(text)
        self.write("\n" + text + "\n")
        if name in GATED:
            self.write("a script can't use %s (%s)\n" % (name, GATED[name]))
        self._dirty()

    # -- input pump (the tick's, not a Layer's) -------------------------------

    def pump(self):
        """Take this frame's keyboard byte and pointer. Called from the tick the
        Player runs, because that is what a script IS -- the console is not a
        content Layer of its own, it is the running cart's surface."""
        inp = self.ws.input
        code = getattr(inp, "last_key", 0) or 0
        if self._ekey.hit(code):
            self.key(code)
        gp = getattr(inp, "game_pointer", None)
        if gp is not None:
            self._pointer(gp[0], gp[1], bool(gp[2]),
                          bool(gp[3]) if len(gp) > 3 else False)

    def _pointer(self, px, py, click, down):
        if click:
            for i, r in enumerate(self._sym_rects):
                if (r[0] <= px < r[0] + r[2]) and (r[1] <= py < r[1] + r[3]):
                    self.key(ord(self._symbols()[i]))
                    return
        if down:
            if self._drag is None:
                self._drag = (py, self.back)
            else:
                lh = self._lh()
                moved = (py - self._drag[0]) // lh if lh else 0
                top = max(0, self._n - self.rows)
                self.back = max(0, min(top, self._drag[1] + moved))
                self._dirty()
        else:
            self._drag = None

    def _symbols(self):
        return symbols_for(self._lang)

    # -- the surface ----------------------------------------------------------

    def _dirty(self):
        self.ws._dirty = True

    def _fs(self):
        return max(1, int(getattr(self.ws.canvas, "font_scale", 1) or 1))

    def _lh(self):
        return 10 * self._fs()

    def draw(self):
        """The run's draw hook: the whole console, on whatever surface the
        Player bound for this script. Takes no arguments -- it is called
        exactly where a cart's `_draw` would be."""
        cv = self.ws.canvas
        th = self.ws.theme_colors
        fs = self._fs()
        fw = 8 * fs
        lh = 10 * fs
        bg, out_ink, in_ink, accent = self._tones(th)
        top = self.ws.app_bar_h()
        pad = 3 * fs
        cv.rect(0, top, cv.w, cv.h - top, bg)
        # The band the prompt + its symbol palette own, reserved whether or not
        # a line is being read so the log does not jump as a script starts and
        # stops asking.
        sym_h = 12 * fs + 2 * pad
        band = lh + 2 * pad + sym_h
        body_h = cv.h - top - band
        self.cols = max(self.MIN_COLS, (cv.w - 2 * pad) // fw)
        self.rows = max(self.MIN_ROWS, body_h // lh)
        self.ed.set_view_size(self.cols, 1)
        # The log, oldest-visible first. `back` is how far up from the tail.
        first = max(0, self._n - self.rows - self.back)
        y = top + pad
        for i in range(first, min(self._n, first + self.rows)):
            cv.print(self.row(i), pad, y, out_ink, 1)
            y += lh
        self._sym_rects = ()
        py = cv.h - band + pad
        if not self.reading():
            return
        line = self._prompt + self.ed.text()
        show = line[-self.cols:] if len(line) > self.cols else line
        cv.print(show, pad, py, in_ink, 1)
        cv.rect(pad + len(show) * fw, py, fw, 8 * fs, accent)
        self._draw_symbols(cv, cv.h - sym_h, sym_h, fs)

    def _draw_symbols(self, cv, y, h, fs):
        """The Code tab's tappable symbol palette, under the prompt: the same
        characters through the same renderer and tone map, because it is the
        same keyboard that cannot type them. The keys share the width; the
        symbol is re-centred in a key shorter than the Code tab's."""
        syms = self._symbols()
        cell = max(8 * fs + 2, cv.w // len(syms))
        syms = syms[:max(1, cv.w // cell)]
        rects = []
        draw_symbol_keys(cv, self.ws.code_layer._tones(), syms, 0, y, cell, h,
                         fs, (cell - 1 - 8 * fs) // 2 - 8 * fs,
                         (h - 1 - 8 * fs) // 2 - 8 * fs, rects)
        self._sym_rects = tuple(rects)

    def _tones(self, th):
        """(background, output ink, typed ink, caret) -- the code editor's two
        branches, because the console is the same kind of surface."""
        NAMES = self.NAMES
        if th.get("ink", NAMES["white"]) != 0:          # dark chrome
            return (th.get("surface", NAMES["black"]), NAMES["light_grey"],
                    NAMES["white"], th.get("accent", NAMES["yellow"]))
        return (th["surface"], th.get("ink_dim", th["ink"]), th["ink"],
                th.get("accent", NAMES["yellow"]))


def _err_text(exc):
    """An exception as one line, never raising itself (the Player's rule: a
    cart whose `__str__` raises must not become a silent hang)."""
    try:
        return "%s: %s" % (type(exc).__name__, exc)
    except Exception:  # noqa: BLE001
        return "error"


def _missing_name(text):
    """The identifier a NameError names, or "" -- both tiers spell it in
    quotes ("name 'wifi' isn't defined")."""
    a = text.find("'")
    b = text.find("'", a + 1)
    return text[a + 1:b] if 0 <= a < b else ""
