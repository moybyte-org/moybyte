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

The log draws through `ui.text_view` (docs/scroll_and_text_2026-10.md §5): a
drag scrolls it by pixels and its release coasts, and while it shows its foot
new rows glide it down to the new foot; scrolled up, it stays where it is.

## What the terminal (#115) inherits

All of it. `on_line` is the seam: set it and every entered line goes to that
handler instead of an `input()` call, which is what a REPL prompt is. `write`
is its scrollback, `draw` is its surface, and `api()` is what `run` hands a
script it starts.
"""

try:
    from editors_code import CodeEditor
    from editors_base import KeyEdge
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime.editors_code import CodeEditor
    from runtime.editors_base import KeyEdge

try:
    from code_layer import symbols_for, draw_symbol_keys
    import ui as _ui
    import ui_draw as _ui_draw
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime.code_layer import symbols_for, draw_symbol_keys
    from runtime import ui as _ui
    from runtime import ui_draw as _ui_draw

# Where an echoed line's answer starts in its row: what was typed draws in
# `value` (`draw`), and the row as read has no mark.
_ANSWER = "\x01"
_ENDED = "The script ended."
_KEYS_READING = ("confirm", "Send", "cancel", "Stop")
_KEYS_ENDED = ("confirm", "Run again", "back", "Back")
_LOG = {"follow": True}             # the log's text view: it follows its foot


# Lua has no `input` of its own and libmoy owns `print` (the DRAW verb), so a
# registered Python `print` would never reach the cart -- lua_ext.LIBMOY_VERBS
# denies it by name, correctly. The console's two verbs therefore register
# under reserved names and this prelude binds them over the top, in the same
# window PRELUDE_HANDLES uses: after registration, before the cart loads. Both
# Lua glue (device/moycore_glue.py, the host's too) execs whatever
# `_moy_prelude` the namespace carries.
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

    def __init__(self, ws, names):
        self.ws = ws
        self.NAMES = names
        self.cols = 38
        self._cap = self.LINES
        self._buf = [""] * self._cap
        self._start = 0
        self._n = 0
        self._open = False        # the newest row is unterminated (print(end=""))
        self._gen = 0             # moves with every write: the view's rows are made again
        self._shown = []          # ...the rows as the log's text view takes them
        self._shown_gen = -1
        self.top = 0              # the first row the log shows, as its view last drew
        self.ed = CodeEditor("", cols=self.cols, rows=1)
        self._ekey = KeyEdge()
        self._armed = False       # a script is waiting on a line
        self._prompt = ""
        self._answer = None       # the entered line, until the script takes it
        self.on_line = None       # #115: a REPL takes every line instead
        self.title = ""
        self._lang = "python"
        self._sym_rects = ()      # built by draw(), read by the tick's pointer
        self.ended = False        # the script's run is over (its prompt band says so)
        # the page's UI context: the log's text view (a drag scrolls it and
        # its release coasts, new rows glide it to its foot) and the hints
        self._host = _ui_draw.AppHost()

    # -- the ring -------------------------------------------------------------

    def clear(self):
        for i in range(self._cap):
            self._buf[i] = ""
        self._start = 0
        self._n = 0
        self._open = False
        self._gen += 1

    def count(self):
        return self._n

    def row(self, i):
        """Row `i`, oldest first."""
        if i < 0 or i >= self._n:
            return ""
        return self._buf[(self._start + i) % self._cap].replace(_ANSWER, "")

    def text(self):
        """The whole scrollback as one string -- what a test reads."""
        return "\n".join(self.row(i) for i in range(self._n))

    def _push(self, row):
        self._gen += 1
        if self._n < self._cap:
            self._buf[(self._start + self._n) % self._cap] = row
            self._n += 1
            return
        self._buf[self._start] = row
        self._start = (self._start + 1) % self._cap

    def _last(self):
        return self._buf[(self._start + self._n - 1) % self._cap]

    def _set_last(self, row):
        self._gen += 1
        self._buf[(self._start + self._n - 1) % self._cap] = row

    def _rows(self):
        """The log as its text view takes it, made again after a write: an
        echoed line's prompt in the dim ink and its answer in `value` (SGR
        2 and 36, `ui.text_view`'s)."""
        if self._shown_gen != self._gen:
            out = []
            for i in range(self._n):
                raw = self._buf[(self._start + i) % self._cap]
                cut = raw.find(_ANSWER)
                if cut >= 0:
                    raw = "\x1b[2m" + raw[:cut] + "\x1b[36m" + raw[cut + 1:]
                out.append(raw)
            self._shown = out
            self._shown_gen = self._gen
        return self._shown

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
        self.write(self._prompt + _ANSWER + line + "\n")
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
        """One keyboard byte. Enter submits; everything else edits the line.
        After the script ended, Enter runs it again."""
        if code in (0x0D, 0x0A) and self.ended and not self.reading():
            self.ws._reset_canvas_state()
            self.ws._start()
            return True
        if not code or not self.reading():
            return False
        if code in (0x0D, 0x0A):
            self.submit()
            return True
        if self.ed.key(code):
            self._dirty()
            return True
        return False

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
        self._host.reset()                # the log's view starts afresh, at its foot
        self.ended = False

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
                if not self.ended and not self.reading():
                    self.ended = True     # a body that only printed, or a run that stopped
                    self._dirty()
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
        n = getattr(inp, "key_repeats", 0)     # a held key types again, and
        if n:                                  # Enter, which enters, does not
            r = inp.repeat_key
            if r in (8, 9, 127) or 32 <= r < 127:
                for _ in range(n):
                    self.key(r)
        gp = getattr(inp, "game_pointer", None)
        if gp is not None:
            self._pointer(gp[0], gp[1], bool(gp[2]),
                          bool(gp[3]) if len(gp) > 3 else False)
        if not self._host.still():
            self._dirty()                       # the log coasts or glides

    def _pointer(self, px, py, click, down):
        if click:
            for i, r in enumerate(self._sym_rects):
                if (r[0] <= px < r[0] + r[2]) and (r[1] <= py < r[1] + r[3]):
                    self.key(ord(self._symbols()[i]))
                    return
        if self._host.point(px, py, click, down):   # a drag scrolls the log
            self._dirty()

    def _symbols(self):
        return symbols_for(self._lang)

    # -- the surface ----------------------------------------------------------

    def _dirty(self):
        self.ws._dirty = True

    def _fs(self):
        return max(1, int(getattr(self.ws.canvas, "font_scale", 1) or 1))

    def draw(self):
        """The run's draw hook: the whole console, on whatever surface the
        Player bound for this script (docs/look_2026-10.md step 33): the code
        surface, the scrollback in the code face, what was typed in `value`,
        the prompt line on a band at the foot with the caret in `accent` --
        or, after the script ended, that it did -- and the hint strip under
        it. Takes no arguments -- it is called exactly where a cart's `_draw`
        would be."""
        # the shell's pixels, not the script's: its fonts and its colours,
        # whatever the app-cart boundary is at
        was = _ui_draw.cart_text(False)
        try:
            self._draw()
        finally:
            _ui_draw.cart_text(was)

    def _draw(self):
        cv = self.ws.canvas
        th = self.ws.theme_colors
        fs = self._fs()
        fid, cell, line, _asc = _ui_draw.font("mono")
        fw = cell * fs
        lh = line * fs + 4 * fs
        dim, value, accent = th["ink_dim"], th["value"], th["accent"]
        cx, top, cw, ch = self.ws.app_content_rect()   # under the host's strip
        host = self._host
        moved = False
        if self._shown_gen != self._gen:
            # rows came: a log at its foot moves to the new foot as this
            # frame's shift
            moved = _ui_draw.text_aim(host, "log", -1, len(self._rows()))
        inp = host.inp
        if not (moved or host.hold or inp[7] or inp[8] or host.moving):
            # the log neither scrolls nor follows (a typed prompt): its rows
            # draw whole for less than the copy of them a shift would make
            host.whole()
        host.begin(cv, th, (cx, top, cw, ch))
        touch = _ui.input_kind() == "touch"
        foot = 0
        if _ui_draw.hint_kind() != "touch":
            keys = _KEYS_READING if self.reading() else _KEYS_ENDED if self.ended else ()
            if keys:
                foot = _ui_draw.metrics("value_row")[16] * fs
                cv.rect(cx, top + ch - foot, cw, foot, th["panel"])
                _ui.at(cx, top + ch - foot, cw, foot)
                _ui.hints(keys)
        bottom = top + ch - foot
        pad = 8 * fs
        sym_h = 12 * fs + 2 * 3 * fs if touch else 0
        band = lh + 2 * pad
        by = bottom - band - sym_h
        self.cols = max(self.MIN_COLS, (cw - 2 * pad) // fw)
        self.ed.set_view_size(self.cols, 1)
        self.ed._scroll()                # the one-line prompt follows its caret
        # The log: a text view from under the pad above it to the prompt's
        # band, which follows its foot as rows come.
        cv.rect(cx, top, cw, pad, th["surface"])
        if sym_h:
            cv.rect(cx, bottom - sym_h, cw, sym_h, th["surface"])
        _ui.at(cx, top + pad, cw, max(0, by - top - pad))
        self.top = _ui.text_view("log", self._rows(), _LOG)[0]
        was = _ui_draw.print_font(fid)
        try:
            self._sym_rects = ()
            cv.rect(cx, by, cw, band, th["surface_alt"])
            cv.rect(cx, by, cw, fs, th["border"])
            ty = by + pad
            if self.reading():
                typed = self.ed.text()
                ask = self._prompt[-self.cols:]
                room = max(0, self.cols - len(ask))
                typed = typed[-room:] if room and len(typed) > room else typed[:room]
                cv.print(ask, cx + pad, ty, dim, 1)
                x = cx + pad + len(ask) * fw
                cv.print(typed, x, ty, value, 1)
                cv.rect(x + len(typed) * fw + fs, ty, fs, line * fs, accent)
                if touch:
                    self._draw_symbols(cv, bottom - sym_h, sym_h, fs)
            elif self.ended:
                _ui_draw.print_font(_ui_draw.font("ui")[0])
                cv.print(_ENDED, cx + pad, ty, dim, 1)
        finally:
            _ui_draw.print_font(was)

    def _draw_symbols(self, cv, y, h, fs):
        """The Code tab's tappable symbol palette, under the prompt: the same
        characters through the same renderer and tone map, because it is the
        same keyboard that cannot type them. The keys share the width; the
        symbol is re-centred in a key shorter than the Code tab's."""
        syms = self._symbols()
        cell = max(8 * fs + 2, cv.w // len(syms))
        syms = syms[:max(1, cv.w // cell)]
        rects = []
        draw_symbol_keys(cv, self.ws.theme_colors, syms, 0, y, cell, h, fs, rects)
        self._sym_rects = tuple(rects)


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
