"""Calc -- the reference SYSTEM APP for the app API (docs/app_api_v1.md).

Deliberately tiny and built ONLY on the public seams, so it doubles as the
"how to write an app" example:

  * a cartridge IDENTITY (system_carts/calc.moy: manifest + a fallback main.py
    an older shell runs as a plain cart);
  * one content-Layer class here (id/domain/draw/handle_input/handle_pointer +
    the app protocol: is_app / open / relayout);
  * ONE `ws.register_app(CalcAppLayer(...))` call -- launcher dispatch, the
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
    import ui as _ui
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime import ui as _ui

_in = _ui.rect_in


class CalcLayout:
    """Responsive Calc geometry: a display band over a 4x4 key grid -- a pure
    rect-algebra stack (the layout style the toolkit recommends for NEW apps)."""

    MIN_W = 170          # ui.py min-size convention (fs-scaled; WM clamps resizes)
    MIN_H = 190

    def __init__(self, w, h, fs=1, windowed=False):
        self.w = int(w)
        self.h = int(h)
        self.fs = max(1, int(fs))
        fs = self.fs
        self.bar_h = 0 if windowed else 18 * fs
        body = _ui.inset((0, self.bar_h, self.w, self.h - self.bar_h), 6 * fs)
        self.display, grid = _ui.cut_top(body, 26 * fs)
        _pad, grid = _ui.cut_top(grid, 4 * fs)
        self.keys = [_ui.hsplit(row, 4, gap=3 * fs)
                     for row in _ui.vsplit(grid, 4, gap=3 * fs)]


class CalcAppLayer:
    """The Calc content Layer + app protocol (see the module docstring)."""

    id = "calc"
    domain = "system"
    TITLE = "CALC"

    _KEYS = (("7", "8", "9", "/"),
             ("4", "5", "6", "*"),
             ("1", "2", "3", "-"),
             ("C", "0", "=", "+"))

    def __init__(self, ws, names, in_rect):
        self.ws = ws
        self._NAMES = names
        self._in = in_rect
        self.hits = _ui.Hits()
        self.entry = "0"              # the number being typed
        self.acc = None               # banked left operand
        self.op = None                # pending operator
        sc = ws.sys_canvas
        self.layout = CalcLayout(sc.w, sc.h, getattr(sc, "font_scale", 1))

    # -- the app protocol (docs/app_api_v1.md) --------------------------------

    @staticmethod
    def is_app(cart):
        """True only for the shipped Calc identity cart (the Writer pattern:
        title + a marker permission + the slug, never a renamed copy)."""
        if (not cart or cart.get("title") != "Calc"
                or "calc" not in (cart.get("permissions") or ())):
            return False
        path = cart.get("path")
        if not path:                 # embedded fallback cart (no writable store)
            return int(cart.get("version", 0)) >= 1
        return str(path).replace("\\", "/").rsplit("/", 1)[-1] == "calc.moy"

    def open(self):
        self.entry = "0"
        self.acc = None
        self.op = None
        self.ws._dirty = True

    def relayout(self, w, h, fs):
        self.layout = CalcLayout(w, h, fs, self.ws.windowed_chrome)

    # -- draw (the draw pass IS the hit map: ui.Hits) --------------------------

    def draw(self, dt):
        ws = self.ws
        NAMES = self._NAMES
        cv = ws.sys_canvas
        th = ws.theme_colors
        lay = self.layout
        fs = lay.fs
        light = ws.light_chrome()
        cv.cls(th["surface"] if light else th["panel"])
        # The display: a dark field with the value right-aligned (readable on
        # either chrome), showing the pending operator as a small left cue.
        x, y, w, h = lay.display
        cv.rect(x, y, w, h, NAMES["black"])
        cv.rectb(x, y, w, h, th["border"] if light else th["dim"])
        if self.op is not None:
            cv.print(self.op, x + 3 * fs, y + (h - 8 * fs) // 2, th["author"], 1)
        text = self.entry[-14:]
        fw = 8 * fs
        cv.print(text, x + w - 3 * fs - len(text) * fw,
                 y + (h - 8 * fs) // 2, NAMES["white"], 1)
        # The key grid through the toolkit's button vocabulary; every key rect
        # registers in the SAME pass the pixels land (draw == tap).
        self.hits.clear()
        for r, row in enumerate(self._KEYS):
            for c, label in enumerate(row):
                rect = lay.keys[r][c]
                kind = "normal"
                if label == "=":
                    kind = "play"
                elif label == "C":
                    kind = "danger"
                elif label in "+-*/":
                    kind = "author"
                _ui.button(cv, th, rect, label, kind=kind)
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
        self.ws._dirty = True

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
