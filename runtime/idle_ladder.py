"""The idle ladder's console half (docs/kernel_survival_2026-10.md section 7.2,
section 13 answers 1 and 7): the Settings rows that set its rungs, and the
screensaver its SAVER rung shows.

The ladder itself is the kernel's (native/moy_kernel/moy_idle.c): it decides
when the screen dims, when the saver comes and when the light goes off, it
swallows the touch that wakes the screen, and it moves the kernel epoch so the
console repaints what the saver covered. What lives here is what only the
console can do:

  IdleLadder  the rows' values -- seconds of no input, OFF a value -- stepped
              from Settings, persisted in the system store and handed to the
              kernel (`moy_loop.idle`). A rung the board's light cannot show
              (DIM on a binary backlight) has no row.
  Saver       what the screen shows on the SAVER rung: a cover cycle where
              the console is fullscreen (the S3s), the desk's wallpaper cart
              where there is a desk (the P4s). Never over a running cart:
              the game keeps the glass, and the ladder still dims and blanks.
"""

try:
    import ui_draw as _ui_draw
except ImportError:  # host: the runtime package
    from runtime import ui_draw as _ui_draw
try:
    from chrome import _ticks_ms, _ticks_diff
except ImportError:  # host: the runtime package
    from runtime.chrome import _ticks_ms, _ticks_diff

DIM, SAVER, BLANK = 1, 2, 3

# The values a rung steps through, in seconds; 0 is OFF.
STEPS = (0, 30, 60, 120, 300, 600, 1800)

# (system key, rung, label, default). The blank rung's default is the board's
# (its POWER_SAVE_MS); the others ship OFF.
ROWS = (
    ("idle_dim", DIM, "DIM AFTER", 0),
    ("idle_saver", SAVER, "SAVER AFTER", 0),
    ("idle_blank", BLANK, "SCREEN OFF AFTER", 300),
)


def label(secs):
    """A rung's value as the row shows it: OFF, 30S, 5M."""
    if not secs:
        return "OFF"
    if secs % 60 == 0:
        return "%dM" % (secs // 60)
    return "%dS" % secs


class IdleLadder:
    """The rungs as Settings rows, over the kernel's ladder (`loop`, the
    module `moy_loop`)."""

    def __init__(self, ws, loop, blank_default=300):
        self.ws = ws
        self.loop = loop
        self.defaults = {"idle_blank": blank_default}
        self.can_dim = bool(loop.idle_can_dim())
        for key, rung, _label, default in ROWS:
            secs = self.defaults.get(key, default)
            try:
                v = ws.system.get(key, secs)
                secs = int(v) if v is not None else secs
            except (TypeError, ValueError):
                pass
            loop.idle(rung, secs)

    def rows(self):
        """The Settings rows this board's light can show."""
        return tuple((key, text, "idle") for key, rung, text, _d in ROWS
                     if rung != DIM or self.can_dim)

    def value(self, key):
        for k, rung, _t, _d in ROWS:
            if k == key:
                return self.loop.idle()[rung]
        return 0

    def label(self, key):
        return label(self.value(key))

    def steps(self):
        """The values a rung takes, in seconds (0 is OFF)."""
        return STEPS

    def step(self, key, d):
        """The next value up (d > 0) or down, persisted and live."""
        cur = self.value(key)
        i = 0
        while i < len(STEPS) and STEPS[i] < cur:
            i += 1
        i = max(0, min(len(STEPS) - 1, i + (1 if d > 0 else -1)))
        return self.set(key, STEPS[i])

    def sleep(self):
        """The BLANK rung now (the system menu's Sleep): the light goes off
        until the next input, which the ladder swallows as it wakes."""
        self.loop.power(False)

    def set(self, key, secs):
        """Rung `key` at `secs`, persisted and live."""
        for k, rung, _t, _d in ROWS:
            if k == key:
                self.loop.idle(rung, secs)
                self.ws.system.set(key, secs)
        self.ws._dirty = True
        return secs


# The saver's inks, the same in every theme: MOY64's white, light grey and
# dark blue's frame.
_INK, _DIM, _FRAME = 7, 6, 1


class Saver:
    """The SAVER rung's screen. `frame(dt)` paints the system canvas and
    answers True on a frame that drew (the console flushes it), False on one
    that shows what is already there."""

    PERIOD_MS = 6000

    def __init__(self, ws):
        self.ws = ws
        self.wallpaper = bool(getattr(ws.wm, "has_desk", False))
        self._i = -1
        self._next = None

    def frame(self, dt):
        ws = self.ws
        if self.wallpaper:
            ws.wallpaper.draw(dt)       # the desk's wallpaper cart, the whole glass
            return True
        now = _ticks_ms()
        if self._next is not None and _ticks_diff(self._next, now) > 0:
            return False
        self._next = now + self.PERIOD_MS
        carts = [c for c in ws.carts.all if c.get("type") == "game"] or ws.carts.all
        cv = ws.sys_canvas or ws.canvas
        cv.cls(0)
        if carts:
            self._i = (self._i + 1) % len(carts)
            self._cover(cv, carts[self._i])
        return True

    def _cover(self, cv, cart):
        """The saver in every theme (docs/look_2026-10.md step 33): on black,
        the cart's cover at its largest whole scale in a 1 px frame, its
        title in the title face under it, the clock small in the corner; a
        cart with no cover is its title alone."""
        ws = self.ws
        img = ws.covers.cover_for(cart)
        w, h = cv.w, cv.h
        fs = getattr(cv, "font_scale", 1) or 1
        fid, _fw, line, _asc = _ui_draw.font("title")
        was = _ui_draw.print_font(fid)
        try:
            title_h = line * fs + 16 * fs
            top = 30 * fs
            if img is not None:
                room = h - top - title_h - 12 * fs
                s = max(1, min((w - 32 * fs) // img.w, room // img.h))
                sw, sh = img.w * s, img.h * s
                x, y = (w - sw) // 2, top + (room - sh) // 2
                cv.blit565(img.pix, img.w, img.h, x, y, s)
                cv.rectb(x - fs, y - fs, sw + 2 * fs, sh + 2 * fs, _FRAME)
                ty = y + sh + 14 * fs
            else:
                ty = (h - line * fs) // 2
            name = str(cart.get("title") or "?")
            cv.print(name, max(0, (w - _ui_draw.text_width(name, fs, "title")) // 2), ty,
                     _INK, 1)
            _ui_draw.print_font(_ui_draw.font("ui")[0])
            clock = ws.bar_layer._clock_text()
            cv.print(clock, w - _ui_draw.text_width(clock, fs, "ui") - 10 * fs, 8 * fs, _DIM, 1)
        finally:
            _ui_draw.print_font(was)
