"""The hidden Easter-egg subsystem + achievement/egg DRAWING (#21), extracted
from Workstation (runtime/console.py).

The split keeps the achievement *core* on Workstation and moves the egg
*subsystem* + all the celebration drawing here:
  * STAYS on Workstation: the `ach` Achievements object, the `show_achievements`
    overlay flag, and load_achievements / _save_achievements /
    _achievement_unlocked (the store wiring + on_save/on_unlock callbacks). Tests
    drive ws.ach.* and ws.show_achievements, and the device calls
    ws.load_achievements(); the core has NO back-dependency on the egg subsystem
    (_achievement_unlocked only beeps).
  * AchievementsUI (here): the three hidden eggs (Konami on the desktop, 7 clock
    taps, 5 SETTINGS-title "secret door" taps) -- their trigger state
    (_konami_pos / _clock_taps / _secret_taps), the egg popup + confetti state
    (egg_msg / egg_until / _confetti_until), the trigger steppers
    (_konami_step / _tap_clock / _tap_secret_door / _show_egg / _egg_active), and
    the drawing (_draw_egg / _draw_confetti / _draw_achievements). Each egg awards
    a hidden achievement via ws.ach.award(...).

Dependency profile (the facade lens, shell_architecture_v1.md §2) -- through
`self.ws`, all shared / non-privileged:
  * ws.ach (award/has/count), ws.sys_canvas, ws._glyph

`NAMES` + the `ACHIEVEMENTS` catalogue are injected at construction (circular-import
reason as the other UIs: console.py builds the one AchievementsUI a Workstation
holds). `_ticks_ms` / `_ticks_diff` are duplicated (time-only). The `_KONAMI` /
tap-goal constants and every method body are kept byte-for-byte identical to the
pre-extraction versions (each aliases NAMES / ACHIEVEMENTS from the injected
values), so the eggs + drawing are unchanged (host == device).
"""


try:
    import ui as _ui
except ImportError:  # pragma: no cover - host fallback
    from runtime import ui as _ui

try:                                    # device: ticks is frozen flat
    from ticks import _ticks_ms, _ticks_diff
except ImportError:                     # host: the runtime package
    from runtime.ticks import _ticks_ms, _ticks_diff


class AchievementsUI:
    _KONAMI = ("up", "up", "down", "down", "left", "right", "left", "right", "b", "a")
    _CLOCK_TAP_GOAL = 7
    _SECRET_TAP_GOAL = 5

    def __init__(self, ws, names, ach_defs):
        self.ws = ws
        # Injected instead of imported back from console.py (see module docstring).
        self._NAMES = names
        self._ACHDEFS = ach_defs
        # Easter-egg trigger + popup state (was Workstation's).
        self._konami_pos = 0          # how far into the Konami sequence we are (desktop)
        self._clock_taps = 0          # clock taps on the status strip (Time Traveler)
        self._secret_taps = 0         # SETTINGS-title taps (Secret Finder door)
        self.egg_msg = None           # (line, glyph) of the live Easter-egg popup, or None
        self.egg_until = 0            # _ticks_ms the egg popup hides at
        self._confetti_until = 0      # _ticks_ms the Konami confetti effect ends

    def _show_egg(self, line, glyph="smile", ms=2600):
        """Pop a non-blocking Easter-egg banner (drawn over the current screen for
        `ms`). Purely cosmetic + self-expiring."""
        self.egg_msg = (line, glyph)
        self.egg_until = _ticks_ms() + ms

    def _konami_step(self, name):
        """Advance the desktop Konami sequence on a button press; the full code in
        order fires the confetti egg + awards "Secret Coder". A wrong key restarts
        (but still counts if it's the sequence's first key, so a fresh start works)."""
        seq = self._KONAMI
        if name == seq[self._konami_pos]:
            self._konami_pos += 1
        else:
            # restart; the press may itself be the (new) first step
            self._konami_pos = 1 if name == seq[0] else 0
        if self._konami_pos >= len(seq):
            self._konami_pos = 0
            self._confetti_until = _ticks_ms() + 3000
            self._show_egg("OH! YOU FOUND ME!", "smile", ms=3000)
            self.ws.ach.award("konami")

    def _tap_clock(self):
        """Count a clock tap; the _CLOCK_TAP_GOAL'th in a row fires the time egg +
        awards "Time Traveler". Any other desktop tap resets the run."""
        self._clock_taps += 1
        if self._clock_taps >= self._CLOCK_TAP_GOAL:
            self._clock_taps = 0
            self._show_egg("TICK TOCK... TIME TRAVELER!", "smile")
            self.ws.ach.award("clock_tinker")

    def _tap_secret_door(self):
        """Count a SETTINGS-title tap (the hidden door); the _SECRET_TAP_GOAL'th
        knocks it open -> "Secret Finder"."""
        self._secret_taps += 1
        if self._secret_taps >= self._SECRET_TAP_GOAL:
            self._secret_taps = 0
            self._show_egg("KNOCK KNOCK... OH! YOU FOUND ME!", "key", ms=3000)
            self.ws.ach.award("secret_door")

    def _egg_active(self, now=None):
        if self.egg_msg is None:
            return False
        if now is None:
            now = _ticks_ms()
        if _ticks_diff(self.egg_until, now) <= 0:
            self.egg_msg = None
            return False
        return True

    def _draw_egg(self):
        """A non-blocking Easter-egg popup: a friendly character glyph + the secret
        message, centered low so it reads as a surprise without covering the action.
        Self-expiring (egg_until); cosmetic only -- touches no cart data."""
        NAMES = self._NAMES
        cv = self.ws.sys_canvas
        line, glyph = self.egg_msg
        w = min(cv.w - 16, 24 + len(line) * 8 + 8)
        x = (cv.w - w) // 2
        y = 150
        h = 30
        _ui.dialog(cv, (x, y, w, h), ring=NAMES["pink"], fill=NAMES["black"])
        self.ws._glyph(glyph, (x + 4, y + 7, 16, 16), NAMES["peach"], cv)
        # The banner's own 16x16 character glyph is wider than ui.row's 14px
        # leading slot, so the caller draws it and hands `row` the rect that is
        # LEFT -- the same "caller keeps its picture" split the grid cells use.
        # fs=1 deliberately: this popup is sized in 8px glyph units (`w` above),
        # so its clip budget is the frozen one, not the canvas font scale.
        _ui.row(cv, self.ws.theme_colors, (x + 24, y, w - 24, h), line,
                colors=(None, NAMES["white"], None), edge=False, pad=0,
                text_dy=11, fs=1)

    def _draw_confetti(self):
        """The Konami egg's celebration: a scatter of colored spark glyphs that
        drift down with the elapsed time. Cheap + deterministic (no RNG state),
        purely cosmetic, gone when _confetti_until passes."""
        NAMES = self._NAMES
        cv = self.ws.sys_canvas
        t = (_ticks_diff(_ticks_ms(), 0) // 80) % 240
        cols = (NAMES["red"], NAMES["yellow"], NAMES["green"], NAMES["blue"],
                NAMES["pink"], NAMES["orange"])
        for k in range(18):
            sx = (k * 53 + 7) % (cv.w - 6)
            sy = (k * 37 + t + (k * k)) % (cv.h - 6)
            self.ws._glyph("spark", (sx, sy, 8, 8), cols[k % len(cols)], cv)

    def _draw_achievements(self):
        """The achievements view (#21): a full panel listing every achievement,
        unlocked ones with their name + glyph in bright color, locked ones greyed
        with a lock + "???" (so a hidden secret stays a surprise). A two-column grid
        so all ~11 fit at 320x240. Tap anywhere to dismiss (see _settings_pointer).
        Indexed API + the shared glyph vocabulary only (host == device)."""
        NAMES = self._NAMES
        ACHIEVEMENTS = self._ACHDEFS
        cv = self.ws.sys_canvas
        th = self.ws.theme_colors
        light = th.get("bar_light", False)
        ink = th["ink"] if light else th["chrome_ink"]
        dim = th["chrome_ink_dim"]
        _ui.dialog(cv, (6, 14, 308, 212), ring=th["accent"], fill=th["panel"])
        self.ws._glyph("trophy", (12, 16, 14, 14), th["accent"], cv)
        cv.print("ACHIEVEMENTS", 30, 18, ink, 2)
        cv.print("%d / %d" % (self.ws.ach.count(), len(ACHIEVEMENTS)), 240, 20,
                 th["accent"], 1)
        col_w = 150
        row_h = 18
        x0 = 12
        y0 = 36
        per_col = 6
        for k in range(len(ACHIEVEMENTS)):
            ach_id, title, glyph, hidden = ACHIEVEMENTS[k]
            col = k // per_col
            row = k % per_col
            x = x0 + col * col_w
            y = y0 + row * row_h
            got = self.ws.ach.has(ach_id)
            if got:
                g_col, label, row_ink = th["accent"], title[:16], ink
            else:
                # A hidden (Easter-egg) achievement stays "???"; a normal locked one
                # shows its name greyed so a kid knows what's there to earn.
                glyph = "lock"
                g_col = th["ink_dim"] if light else NAMES["dark_grey"]
                label = "???" if hidden else title[:16]
                row_ink = dim
            # The glyph and the label carry DIFFERENT inks here (a bright badge
            # beside quiet text), which `row`'s single-ink leading slot cannot
            # express -- so the caller draws the badge and `row` takes the rect
            # after it. `disabled` states the locked semantics for the Phase 4
            # skin; the frozen chrome_ink palette rides `colors` until there is
            # a skin entry for it. fs=1: this view is frozen 320x240 geometry.
            self.ws._glyph(glyph, (x, y, 14, 14), g_col, cv)
            _ui.row(cv, th, (x + 16, y, col_w - 16, 14), label,
                    disabled=not got, colors=(None, row_ink, None),
                    edge=False, pad=0, text_dy=3, fs=1)
        cv.print("TAP TO CLOSE", 110, 210, dim, 1)
