"""Calc -- the reference SYSTEM APP for the app API (docs/app_api_v1.md), drawn
with the public UI API's verbs (docs/ui_api_2026-10.md) and nothing under them.

  * a cartridge IDENTITY (system_carts/moybyte.calc.moy: manifest + a fallback
    main.py an older shell runs as a plain cart) whose manifest carries the
    `app` block the shell registers from -- there is no per-app line in
    console.py;
  * one content-Layer class here (id/domain/draw/handle_input/handle_pointer +
    the app protocol: is_app / open / relayout), declaring the shell roles it
    uses in NEEDS and reaching the shell ONLY through the AppContext it is
    constructed with (runtime/app_context.py), `UI = True` giving it the host
    side of its verbs as `ctx.ui`;
  * geometry from the rect algebra over the content rect `ui.page` answers,
    the keys as `ui.button`s of their kinds placed with `ui.at`, and the tape
    on the display inset a `ui.part` is, which Calc writes into. The engine
    owns focus, the arrows, Enter and taps.

Kid-facing behavior: a plain integer calculator. Digits build the entry, an
operator banks it, "=" computes and writes the sum on the tape, "C" clears
the entry, the backspace key drops its last digit; division is integer
division, % its remainder, and dividing by zero just says OOPS (no crash,
kid-friendly). The keyboard types the same digits and operators. State and
tape persist while the app stays open, and reset on a fresh open.
"""

try:
    from layout_base import bar_rows, content_of, metric_class, DESK
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime.layout_base import bar_rows, content_of, metric_class, DESK

try:
    from moy_store_base import builtin_name
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime.moy_store_base import builtin_name

try:
    import ui
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime import ui

# The keypad, 4 x 5, row by row; None is the 0's second column. A key is its
# operation: digits, the operators, "C", "<" (backspace), "." and "=".
KEYS = (("C", "<", "%", "/"), ("7", "8", "9", "*"), ("4", "5", "6", "-"),
        ("1", "2", "3", "+"), ("0", None, ".", "="))
# What a key shows in a face that has the signs, and in one that has not;
# backspace is the icon set's `backspace` (⌫) wherever the set draws one.
SIGNS = {"/": "÷", "*": "×", "-": "−", "<": "←"}
TAPE_KEEP = 8           # the sums the tape holds


def kind_of(key):
    """Calc's one per-key decision (DESIGN.md, "Button kinds")."""
    if key == "=":
        return "primary"
    if key == "C":
        return "danger"
    if key in "/*-+%":
        return "tonal"
    return "normal"


class CalcLayout:
    """Responsive Calc geometry: beside each other on a wide page (the tape's
    display on the left of the keypad), stacked on a tall one (a window)."""

    MIN_W = 170          # ui.py min-size convention (fs-scaled; WM clamps resizes)
    MIN_H = 190

    def __init__(self, w, h, fs=1, windowed=False, cs=None):
        self.w = int(w)
        self.h = int(h)
        self.fs = max(1, int(fs))
        fs = self.fs
        # The chrome scale (#203) sizes only the OS bar band above this app;
        # a window has no bar over its content.
        self.cs = max(fs, int(cs)) if cs else fs
        self.desk = metric_class() == DESK
        self.windowed = bool(windowed)     # its page lies in a window's body
        self.cx, self.cy, self.cw, self.ch = content_of(
            self.w, self.h, 0 if windowed else bar_rows(self.cs))
        self.place((self.cx, self.cy, self.cw, self.ch))

    def place(self, page):
        """The display and the keys inside `page`, the content rect."""
        fs = self.fs
        self.page = page
        x, y, w, h = page
        self.side = w * 3 > h * 4
        if self.side:
            pad, g, between = 8 * fs, 6 * fs, 8 * fs
            body = (x + pad, y + 4 * fs, max(0, w - 2 * pad), max(0, h - 10 * fs))
            kw = (body[2] - between) * 184 // 296
            self.display, rest = ui.cut_left(body, body[2] - kw - between)
            _gap, grid = ui.cut_left(rest, between)
        else:
            pad = 16 if self.desk else 8 * fs
            g = 10 if self.desk else 6 * fs
            body = ui.inset(page, pad)
            dh = min(100 if self.desk else 46 * fs, body[3] // 4)
            self.display, rest = ui.cut_top(body, dh)
            _gap, grid = ui.cut_top(rest, pad if self.desk else 8 * fs)
        cells = [ui.hsplit(row, 4, gap=g) for row in ui.vsplit(grid, 5, gap=g)]
        self.labels = []
        self.keys = []
        for r, row in enumerate(KEYS):
            for c, key in enumerate(row):
                if key is None:
                    continue
                kx, ky, kw, kh = cells[r][c]
                if key == "0":
                    kw = cells[r][1][0] + cells[r][1][2] - kx
                self.labels.append(key)
                self.keys.append((kx, ky, kw, kh))


class CalcAppLayer:
    """The Calc content Layer + app protocol (see the module docstring)."""

    id = "calc"
    domain = "system"
    TITLE = "Calc"
    # The shell roles this app uses (runtime/app_context.py). The context
    # carries these and nothing else, so an undeclared reach-in is an
    # AttributeError here rather than a coupling nobody sees.
    NEEDS = ("surface", "theme", "damage")
    UI = True

    def __init__(self, ctx, names):
        self.ctx = ctx
        # Roles used on every drawn frame are bound ONCE here, not looked up
        # per draw -- the hoist mandate (ui_refactor_2026-08 Section 2.4).
        self._surf = ctx.surface
        self._theme = ctx.theme
        self._damage = ctx.damage
        self._ui = ctx.ui
        self.entry = "0"              # the number being typed
        self.acc = None               # banked left operand
        self.op = None                # pending operator
        self.tape = []                # (a, op, b, result) per sum, oldest first
        self.shown = False            # the display shows the tape's last sum
        self._first = True
        sc = self._surf.canvas()
        self.layout = CalcLayout(sc.w, sc.h, getattr(sc, "font_scale", 1),
                                 cs=self._surf.chrome_scale())

    # -- the app protocol (docs/app_api_v1.md) --------------------------------

    @staticmethod
    def is_app(cart):
        """True only for the shipped Calc identity cart (the shipped-identity pattern:
        title + a marker permission + the slug, never a renamed copy)."""
        if (not cart or cart.get("title") != "Calc"
                or "calc" not in (cart.get("permissions") or ())):
            return False
        path = cart.get("path")
        if not path:                 # embedded fallback cart (no writable store)
            return int(cart.get("version", 0)) >= 1
        return builtin_name(path) == "moybyte.calc.moy"

    def open(self):
        self.entry = "0"
        self.acc = None
        self.op = None
        self.tape = []
        self.shown = False
        self._first = True
        self._ui.reset()
        self._damage.all()

    def relayout(self, w, h, fs, cs=None):
        self.layout = CalcLayout(w, h, fs, self._surf.windowed(), cs)

    # -- draw -------------------------------------------------------------------

    def draw(self, dt):
        cv = self._surf.canvas()
        lay = self.layout
        th = self._theme.colors()
        self._ui.begin(cv, th, (0, 0, cv.w, cv.h), lay.desk, lay.windowed)
        ui.page("")
        page = (lay.cx, lay.cy, lay.cw, lay.ch)
        if not lay.desk:
            band, page = ui.cut_bottom(page, 20 * lay.fs)
            ui.at(*band)
            ui.hints(("enter", "Press", "Esc", "Back"))
        if page != lay.page:
            lay.place(page)
        if self._first:
            ui.focus("5")
            self._first = False
        signs = SIGNS if ui.has_symbols() else None
        pressed = None
        labels = lay.labels
        keys = lay.keys
        for n in range(len(labels)):
            key = labels[n]
            ui.at(*keys[n])
            text = signs.get(key, key) if signs else key
            o = _DOT if key == "." else None
            if key == "<" and ui.icon("backspace"):
                text, o = "", _BACK          # the icon alone, the set's ⌫
            if ui.button(text, kind_of(key), o):
                pressed = key
        ui.at(*lay.display)
        x, y, w, h, _st = ui.part("tape")
        self._draw_tape(cv, th, x, y, w, h, signs)
        if pressed is not None:
            self._key(pressed)

    def _draw_tape(self, cv, th, x, y, w, h, signs):
        lay = self.layout
        fs = lay.fs
        dim, ink, rule, value = th["ink_dim"], th["ink"], th["border"], th["lcd_ink"]
        # One inset on every side of the display: the text never meets its
        # frame, and the result sits as far from the bottom as from the right.
        pad = 14 if lay.desk else 10 * fs
        right = x + w - pad
        room = right - x - pad
        line = ui.text_size("0")[1]
        # The result: in the theme's result face where it has one that draws
        # it, else the largest scale up to the display's that fits.
        text = self.entry
        if signs:
            text = text.replace("-", signs["-"])
        if ui.face("result", text):
            big = fs
        else:
            text = self.entry
            big = 5 if lay.desk else 3 * fs
            while big > 1 and ui.text_size(text, big)[0] > room:
                big -= 1
        text = _fit(text, room, big)
        tw, rh = ui.text_size(text, big)
        # The bottom inset is the digits' (a line's last fifth is the
        # descent under them, which hangs into it).
        ry = y + h - pad - rh + rh // 5
        cv.print_scaled(text, right - tw, ry, value, big)
        ui.face()
        expr = self._expr(signs)
        # The sum line over the result, never closer to the frame than the
        # inset at the top either.
        ey = max(y + pad - line // 4, ry - line - (10 if lay.desk else 8 * fs))
        if expr:
            expr = _fit(expr, room, 1)
            cv.print(expr, right - ui.text_size(expr)[0], ey, dim)
        if not lay.side:
            return
        # The tape: the sums before the one shown, newest lowest, as many as fit.
        pitch = 2 * line + 14 * fs
        ty = y + 4 * fs
        fit = max(0, (ey - 4 * fs - ty) // pitch)
        sums = self.tape[:-1] if self.shown else self.tape
        for a, op, b, res in sums[max(0, len(sums) - fit):]:
            s = _fit(_sum(a, op, b, signs), room, 1)
            cv.print(s, right - ui.text_size(s)[0], ty + 3 * fs, dim)
            r = _fit("= " + res, room, 1)
            cv.print(r, right - ui.text_size(r)[0], ty + line + 7 * fs, ink)
            cv.rect(right - room, ty + 2 * line + 11 * fs, room, fs, rule)
            ty += pitch

    def _expr(self, signs):
        """The display's line over the result: the sum just made, or the
        operand and operator banked."""
        if self.shown and self.tape:
            a, op, b, _res = self.tape[-1]
            return _sum(a, op, b, signs) + " ="
        if self.op is not None and self.acc is not None:
            return str(self.acc) + " " + _sign(self.op, signs)
        return ""

    # -- input -----------------------------------------------------------------

    def handle_input(self, i):
        ch = i.last_key
        if ch:
            c = chr(ch) if isinstance(ch, int) else str(ch)
            if c.isdigit() or c in "+-*/=%":
                self._key(c)
            elif c in ("\b", "\x7f"):
                self._key("<")
        if self._ui.keys(i):
            self._damage.all()
        return True

    def handle_pointer(self, px, py, click):
        if self._ui.point(px, py, click):
            self._damage.all()
        return True

    # -- the calculator itself ---------------------------------------------------

    def _key(self, label):
        fresh = self.shown              # a digit after a sum starts a new entry
        self.shown = False
        if label == "C":
            self.entry, self.acc, self.op = "0", None, None
        elif label == "<":
            self.entry = self.entry[:-1] if len(self.entry) > 1 and self.entry != "OOPS" else "0"
        elif label.isdigit():
            if fresh or self.entry in ("0", "OOPS"):
                self.entry = label
            elif len(self.entry) < 12:
                self.entry += label
        elif label in "+-*/%":
            self.acc = self._value()
            self.op = label
            self.entry = "0"
        elif label == "=":
            self._compute()
        self._damage.all()

    def _value(self):
        try:
            return int(self.entry)
        except ValueError:
            return 0

    def _compute(self):
        if self.op is None or self.acc is None:
            return
        a, op, b = self.acc, self.op, self._value()
        if op == "+":
            out = a + b
        elif op == "-":
            out = a - b
        elif op == "*":
            out = a * b
        elif b == 0:                          # division by zero: kid-friendly
            self.entry, self.acc, self.op = "OOPS", None, None
            return
        elif op == "/":
            out = a // b
        else:
            out = a % b
        self.entry = str(out)
        self.acc = None
        self.op = None
        self.tape.append((a, op, b, self.entry))
        if len(self.tape) > TAPE_KEEP:
            del self.tape[0]
        self.shown = True


# Calc counts in whole numbers, so its point key is there and never activates.
_DOT = {"disabled": True}
# The backspace key drawn as its icon: no label, so it names its stop.
_BACK = {"id": "<"}


def _sign(op, signs):
    return signs.get(op, op) if signs else op


def _sum(a, op, b, signs):
    return str(a) + " " + _sign(op, signs) + " " + str(b)


def _fit(text, room, scale):
    """`text`'s tail that fits `room` pixels at `scale`: a number keeps its
    last digits."""
    while len(text) > 1 and ui.text_size(text, scale)[0] > room:
        text = text[1:]
    return text
