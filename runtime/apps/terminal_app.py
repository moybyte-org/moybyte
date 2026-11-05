"""The TERMINAL -- the C shell's screen face (docs/os_2026-11/system.md section 1).

The Terminal system app launches the Terminal's face, which is C
(`moy_sh.Face`: native/moy_sh/moy_sh_face.h): a session of native/moy_sh
whose output fills a cell grid, the prompt line under it, the history,
completion, the key rows and their input, drawn through the UI engine. The
kernel's recovery console opens the same face with no VM. This layer hands
it the frame: the host's context and canvas, the content rect under the
bar, and the frame's keys; the face answers what the host owes after it.
"""

from array import array as _array

try:
    import ui as _ui
    import ui_draw as _ui_draw
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime import ui as _ui
    from runtime import ui_draw as _ui_draw

try:
    import layout_base as _lb
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime import layout_base as _lb

try:
    import moy_sh as _sh
    _sh.Face
except (ImportError, AttributeError):   # a folder of that name on CPython's path
    try:                    # CPython: the ctypes binding (runtime/host/moy_sh.py)
        from runtime import moy_sh as _sh
    except ImportError:     # the browser and the desktop MicroPython: no C shell
        _sh = None

_HIT, _KEYS = 1, 2                      # the face's input answer
_AGAIN, _DAMAGE = 1, 2                  # its draw answer
_ROWS = (None, "tdeck", "pad", "touch")
_BAR = "\0".join(_ui_draw.BAR_KEYS).encode()
_NONE = ["No shell on this console."]


class TerminalLayout:
    """The terminal's page: the content rect under the host's bar."""

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
        self.face = None            # made at the first open
        self._log = _ui.text_options({"follow": True})
        self._into = _array("i", [0, 0])
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
        f = self.ensure()
        if f is not None:
            f.open()
        self._ui.reset()
        self._damage.all()

    def relayout(self, w, h, fs, cs=None):
        self.layout = TerminalLayout(w, h, fs, self._surf.windowed(), cs)

    def close_session(self):
        """The session given back, its arena and grid with it: the next open
        makes a new one."""
        if self.face is not None:
            self.face.close_session()

    def ensure(self):
        """The face, made the first time it is needed: None where this
        console has no C shell (the browser)."""
        if self.face is None and _sh is not None:
            try:
                if getattr(_sh, "HOST", False):
                    self.face = _sh.Face(_host_root(self._install.root()))
                else:
                    self.face = _sh.Face()
            except OSError:
                self.face = None
        return self.face

    def row_kind(self):
        """Which key row the page draws: None (a desk, a keyboard), "tdeck",
        "pad" or "touch"."""
        return _ROWS[_sh.face_row(self.layout.desk)] if _sh is not None else None

    # -- input --------------------------------------------------------------------

    def handle_input(self, inp):
        f = self.face
        if f is None:
            return True
        k = getattr(inp, "last_key", 0) or 0
        if not isinstance(k, int):
            k = ord(k[0]) if k else 0
        n = getattr(inp, "key_repeats", 0)
        r = f.input(self.layout.desk, k, n, inp.repeat_key if n else 0,
                    inp.steps("left"), inp.steps("right"), inp.steps("up"), inp.steps("down"))
        hit = r & _HIT
        if r & _KEYS and self._ui.keys(inp):
            hit = True
        if hit:
            self._damage.all()
        return True

    def handle_pointer(self, px, py, click):
        if self._ui.point(px, py, click, bool(getattr(self._surf.pointer(), "down", False))):
            self._damage.all()
        return True

    # -- the surface ----------------------------------------------------------------

    def draw(self, dt):
        cv = self._surf.canvas()
        lay = self.layout
        f = self.face
        self._ui.begin(cv, self._theme.colors(), (0, 0, cv.w, cv.h), lay.desk, lay.windowed,
                       f is None or f.elsewhere(lay.desk))
        if f is None:
            _ui.page("")
            _ui.at(lay.cx, lay.cy, lay.cw, lay.ch)
            _ui.text_view("log", _NONE, self._log, None, None, self._into)
            return
        t = _ui._T
        r = f.draw(t[0], t[1], t[2], lay.cx, lay.cy, lay.cw, lay.ch, lay.fs, lay.desk,
                   _BAR if _ui_draw.on_bar() else None)
        if r & _AGAIN:
            self._ui.again()
        if r & _DAMAGE:
            self._damage.all()


def _host_root(carts_root):
    """The files tree a host session sees: the folder whose `sd/moybyte/carts`
    is the carts root, as on a board, else the carts root's own folder."""
    r = (carts_root or "").rstrip("/")
    tail = "/sd/moybyte/carts"
    if r.endswith(tail):
        return r[:-len(tail)] or "/"
    i = r.rfind("/")
    return r[:i] if i > 0 else r
