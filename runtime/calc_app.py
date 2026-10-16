"""Calc -- the reference SYSTEM APP for the app API (docs/app_api_v1.md).

Deliberately tiny and built ONLY on the public seams, so it doubles as the
"how to write an app" example:

  * a cartridge IDENTITY (system_carts/moybyte.calc.moy: manifest + a fallback main.py
    an older shell runs as a plain cart) whose manifest carries the `app` block
    the shell registers from -- there is no per-app line in console.py;
  * one content-Layer class here (id/domain/draw/handle_input/handle_pointer +
    the app protocol: is_app / open / relayout), declaring the shell roles it
    uses in NEEDS and reaching the shell ONLY through the AppContext it is
    constructed with (runtime/app_context.py) -- launcher dispatch, the
    back-stack/window kind, taskbar chip, WM title ("CALC" from TITLE), the
    per-window layout context AND the exitable bar (the host draws the strip
    after draw() and routes its taps before handle_pointer()) all follow from
    the registration;
  * geometry from the ui rect algebra (cut/inset/vsplit/hsplit), drawing from
    the ui widgets, and taps resolved through ui.Hits -- the draw pass IS the
    hit-map, the toolkit's draw==tap contract.

Kid-facing behavior: a plain integer calculator. Digits build the entry, an
operator banks it, "=" computes, "C" clears; division is integer division and
dividing by zero just says OOPS (no crash, kid-friendly). State persists while
the app stays open (a real calculator's feel), and resets on a fresh open.
"""

try:
    from layout_base import bar_rows, content_of
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime.layout_base import bar_rows, content_of

try:
    from moy_store_base import builtin_name
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime.moy_store_base import builtin_name

try:
    import ui as _ui
    import ui_draw as _ui_draw
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime import ui as _ui
    from runtime import ui_draw as _ui_draw

_in = _ui.rect_in


class CalcLayout:
    """Responsive Calc geometry: a display band over a 4x4 key grid -- a pure
    rect-algebra stack (the layout style the toolkit recommends for NEW apps)."""

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
        self.cx, self.cy, self.cw, self.ch = content_of(
            self.w, self.h, 0 if windowed else bar_rows(self.cs))
        body = _ui.inset((self.cx, self.cy, self.cw, self.ch), 6 * fs)
        self.display, grid = _ui.cut_top(body, 26 * fs)
        _pad, grid = _ui.cut_top(grid, 4 * fs)
        self.keys = [_ui.hsplit(row, 4, gap=3 * fs)
                     for row in _ui.vsplit(grid, 4, gap=3 * fs)]


class CalcAppLayer:
    """The Calc content Layer + app protocol (see the module docstring)."""

    id = "calc"
    domain = "system"
    TITLE = "CALC"
    # The shell roles this app uses (runtime/app_context.py). The context
    # carries these and nothing else, so an undeclared reach-in is an
    # AttributeError here rather than a coupling nobody sees.
    NEEDS = ("surface", "theme", "damage")

    _KEYS = (("7", "8", "9", "/"),
             ("4", "5", "6", "*"),
             ("1", "2", "3", "-"),
             ("C", "0", "=", "+"))

    def __init__(self, ctx, names):
        self.ctx = ctx
        # Roles used on every drawn frame are bound ONCE here, not looked up
        # per draw -- the hoist mandate (ui_refactor_2026-08 Section 2.4).
        self._surf = ctx.surface
        self._theme = ctx.theme
        self._damage = ctx.damage
        self._NAMES = names
        self.hits = _ui_draw.Hits()
        self.entry = "0"              # the number being typed
        self.acc = None               # banked left operand
        self.op = None                # pending operator
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
        self._damage.all()

    def relayout(self, w, h, fs, cs=None):
        self.layout = CalcLayout(w, h, fs, self._surf.windowed(), cs)

    # -- draw (the draw pass IS the hit map: ui.Hits) --------------------------

    def draw(self, dt):
        NAMES = self._NAMES
        theme = self._theme
        cv = self._surf.canvas()
        th = theme.colors()
        lay = self.layout
        fs = lay.fs
        light = theme.light()
        cv.cls(th["surface"] if light else th["panel"])
        # The display: an LCD, black with the value right-aligned in green on
        # every theme, showing the pending operator as a small left cue.
        x, y, w, h = lay.display
        cv.rect(x, y, w, h, NAMES["black"])
        cv.rectb(x, y, w, h, th["border"] if light else th["dim"])
        if self.op is not None:
            cv.print(self.op, x + 3 * fs, y + (h - 8 * fs) // 2, th["author"], 1)
        text = self.entry[-14:]
        cv.print(text, x + w - 3 * fs - _ui_draw.print_width(text, fs),
                 y + (h - 8 * fs) // 2, NAMES["green"], 1)
        # The key grid through the toolkit's button vocabulary; every key rect
        # registers in the SAME pass the pixels land (draw == tap).
        self.hits.clear()
        for r, row in enumerate(self._KEYS):
            for c, label in enumerate(row):
                rect = lay.keys[r][c]
                kind = "normal"
                if label == "=":
                    kind = "primary"
                elif label == "C":
                    kind = "danger"
                elif label in "+-*/":
                    kind = "tonal"
                _ui_draw.button(cv, th, rect, label, kind=kind, app=self.id)
                self.hits.add(rect, "key", label)

    # -- input -----------------------------------------------------------------

    def handle_input(self, i):
        ch = i.last_key
        if ch:
            c = chr(ch) if isinstance(ch, int) else str(ch)
            if c.isdigit() or c in "+-*/=":
                self._key(c)
            elif c in ("\r", "\n"):
                self._key("=")
        if i.pressed("a") or i.pressed("run"):
            self._key("=")
        return True

    def handle_pointer(self, px, py, click):
        if not click:
            return True
        hit = self.hits.at(px, py)
        if hit is not None:
            self._key(hit[1])
        return True

    # -- the calculator itself ---------------------------------------------------

    def _key(self, label):
        if label == "C":
            self.entry, self.acc, self.op = "0", None, None
        elif label.isdigit():
            if self.entry in ("0", "OOPS") or self.entry.startswith("="):
                self.entry = label
            elif len(self.entry) < 12:
                self.entry += label
        elif label in "+-*/":
            self.acc = self._value()
            self.op = label
            self.entry = "0"
        elif label == "=":
            self._compute()
        self._damage.all()

    def _value(self):
        try:
            return int(self.entry.lstrip("="))
        except ValueError:
            return 0

    def _compute(self):
        if self.op is None or self.acc is None:
            return
        b = self._value()
        if self.op == "+":
            out = self.acc + b
        elif self.op == "-":
            out = self.acc - b
        elif self.op == "*":
            out = self.acc * b
        elif b == 0:                          # division by zero: kid-friendly
            self.entry, self.acc, self.op = "OOPS", None, None
            return
        else:
            out = self.acc // b
        self.entry = str(out)
        self.acc = None
        self.op = None
