"""GT911 shared core (#202 Phase C): the pieces both boards' touch drivers are
the same about, promoted the day the rule's "second consumer" test was noticed
to have been satisfied all along -- the T-Deck and the Waveshare P4 BOTH carry
a GT911, and their drivers cross-referenced each other's hold logic in prose
while maintaining it twice. That block had already drifted once: the P4 copy
"had the hold and neither guard until 2026-08-15" (its own comment), i.e. it
held the point without the staleness flag or the release bound that make
holding safe.

What is SHARED here: the register map and the no-news contract (`HeldPoint`).
What deliberately is NOT: everything each board learned on its own glass --
the T-Deck's #74 INT-pin gate, per-phase I2C latency stats and poller-thread
hook (bus-contention medicine for a GT911 sharing I2C0 with the keyboard C3),
and the P4's raw-coordinate simplicity (self-configured controller, no INT
wired). A third GT911 board (the Guition P4 lineup, #202) starts from this
core plus whichever half its wiring resembles.

THE NO-NEWS CONTRACT, once, with all three of its clauses -- the point of
promoting it is that a future driver cannot take one clause without the
others:

  1. HOLD the point while the finger is down and the hardware said nothing
     this pass -- a phantom release mid-drag ENDS the gesture (ui.DragTap runs
     drag_end, which can launch a kinetic fling by itself, #113), and with the
     T-Deck's #74 stall rate (75-90% of finger-down reads take 20-45ms) that
     happened on roughly every other frame.
  2. Mark the repeat STALE (`fresh=False`) -- the kinetic scroller must not
     average a delta the hardware never measured into a fling's velocity.
  3. BOUND the hold (HOLD_SAMPLE_MS) -- a missed finger-up report must free
     the pointer in well under a second, never wedge it down.
"""

try:                                    # device: staged flat namespace
    from ticks import _ticks_ms, _ticks_diff
except ImportError:                     # host tests
    from runtime.ticks import _ticks_ms, _ticks_diff

# The GT911 register map both boards read.
REG_STATUS = 0x814E       # bit7 = buffer ready, low nibble = point count
REG_POINT0 = 0x8150       # first touch point (byte order is BOARD-specific:
                          # the T-Deck's part reports y(lo,hi) x(lo,hi), the
                          # Waveshare P4's x(lo,hi) y(lo,hi) -- read the byte
                          # dump on new glass, never assume)
ADDRS = (0x5D, 0x14)      # default / alternate I2C addresses (INT strap)


class HeldPoint:
    """The held-finger state machine behind a touch poll() -- see the module
    docstring for the three clauses. Drivers call exactly one of sample() /
    release() / hold() per poll pass and forward the return; `fresh` is read
    by the frame loop after every pass (pointer.fresh).

    EXTRAPOLATION (opt-in, 2026-08-19 -- born in the Guition's axs_touch and
    promoted here the day the T-Deck became its second consumer): a held
    (no-news) pass returns the last point ADVANCED along the finger's
    measured velocity instead of frozen. On the T-Deck, #74's stall gaps
    freeze-then-jump the pointer on most drag frames; on the Guition the
    <=90ms lift window read as a stall in the middle of every flick. Both
    are the same artifact, and gliding through the gap fixes both -- the
    next real sample corrects the few pixels of error. Extrapolated passes
    keep fresh=False: this moves PIXELS, never the kinetic velocity EMA
    (console._tick_pointer_dt's rules stay honest). `w`/`h` clamp the glide
    to the glass; a press edge resets the velocity so a previous gesture's
    speed cannot leak."""

    HOLD_SAMPLE_MS = 400   # #74-measured: rides out the 20-45ms stall clusters,
                           # frees a missed release in well under a second
    VEL_EMA = 0.5          # per fresh sample; matches ui.ScrollRegion's feel

    def __init__(self, extrapolate=False, w=0, h=0, damp=1.0):
        self.down = False
        self.last = None          # (x, y) most recently measured
        self._ms = 0              # when `last` landed (the bound's clock)
        self.fresh = True
        self.extrapolate = extrapolate
        self._w = w
        self._h = h
        # Glide fraction of the measured velocity. 1.0 where a gap is never
        # CORRECTED (the Guition: its only long gap is the lift window, which
        # ends in a release, not a late sample). BELOW 1.0 where gaps end in
        # late real samples (the T-Deck's #74 stalls): a full-speed glide
        # overshoots whenever the EMA outruns the finger, and the correcting
        # sample then lands BEHIND it -- felt as forward-back ripple in slow
        # drags (owner, first T-Deck flash of this lever). At 0.5 the
        # correction lands forward unless the estimate is 2x wrong, so the
        # ripple dies while the freeze-jump smoothing stays.
        self.damp = damp
        self._vx = 0.0            # px/ms over fresh samples (mapped space)
        self._vy = 0.0
        self._gx = None           # last DISPLAYED glide point, while recovering

    def sample(self, x, y):
        """A fresh finger-down sample -> (x, y, press_edge)."""
        edge = not self.down
        now = _ticks_ms()
        out_x, out_y = x, y
        if self.extrapolate:
            if edge or self.last is None:
                self._vx = 0.0
                self._vy = 0.0
            else:
                dt = _ticks_diff(now, self._ms)
                if 0 < dt <= 100:
                    self._vx += ((x - self.last[0]) / dt - self._vx) * self.VEL_EMA
                    self._vy += ((y - self.last[1]) / dt - self._vy) * self.VEL_EMA
            # MONOTONIC RECOVERY (the ripple fix, T-Deck 2026-08-19): a
            # stalled read delivers a position MEASURED 20-45ms ago, so the
            # sample that ends a glide can land BEHIND the displayed point
            # along the motion -- snapping to it is the forward-back ripple
            # slow drags showed (damping the glide cannot fix staleness).
            # While recovering, a small backward step along the velocity is
            # NOT displayed; the next, current sample catches up. Big steps
            # (a real reversal) pass through untouched.
            if self._gx is not None and not edge:
                bx = self._gx[0] - x
                by = self._gx[1] - y
                behind = bx * self._vx + by * self._vy   # >0: sample trails
                if behind > 0 and bx * bx + by * by <= 1024:   # <=32px
                    out_x, out_y = self._gx
            self._gx = None
        self.down = True
        self.last = (x, y)        # velocity stays honest: TRUE samples only
        self._ms = now
        self.fresh = True
        return (out_x, out_y, edge)

    def release(self):
        """The controller reported zero points -> None. A release IS news."""
        self.down = False
        self.last = None
        self._gx = None
        self.fresh = True
        return None

    def hold(self):
        """No news this pass -> the held point (stale, bounded, extrapolated
        when opted in) or None."""
        if self.down and self.last is not None:
            dt = _ticks_diff(_ticks_ms(), self._ms)
            if dt < self.HOLD_SAMPLE_MS:
                self.fresh = False
                x, y = self.last
                if self.extrapolate and dt > 0:
                    x = int(x + self._vx * self.damp * dt)
                    y = int(y + self._vy * self.damp * dt)
                    self._gx = (x, y)   # the recovery clamp's anchor
                    if self._w:
                        if x < 0:
                            x = 0
                        elif x >= self._w:
                            x = self._w - 1
                    if self._h:
                        if y < 0:
                            y = 0
                        elif y >= self._h:
                            y = self._h - 1
                return (x, y, False)
            self.down = False         # missed release: never wedge the pointer
            self.last = None
        self.fresh = True
        return None
