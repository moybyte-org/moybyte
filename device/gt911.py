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
    """The held-finger state machine behind a GT911 poll() -- see the module
    docstring for the three clauses. Drivers call exactly one of sample() /
    release() / hold() per poll pass and forward the return; `fresh` is read
    by the frame loop after every pass (pointer.fresh)."""

    HOLD_SAMPLE_MS = 400   # #74-measured: rides out the 20-45ms stall clusters,
                           # frees a missed release in well under a second

    def __init__(self):
        self.down = False
        self.last = None          # (x, y) most recently measured
        self._ms = 0              # when `last` landed (the bound's clock)
        self.fresh = True

    def sample(self, x, y):
        """A fresh finger-down sample -> (x, y, press_edge)."""
        edge = not self.down
        self.down = True
        self.last = (x, y)
        self._ms = _ticks_ms()
        self.fresh = True
        return (x, y, edge)

    def release(self):
        """The controller reported zero points -> None. A release IS news."""
        self.down = False
        self.last = None
        self.fresh = True
        return None

    def hold(self):
        """No news this pass -> the held point (stale, bounded) or None."""
        if self.down and self.last is not None:
            if _ticks_diff(_ticks_ms(), self._ms) < self.HOLD_SAMPLE_MS:
                self.fresh = False
                return (self.last[0], self.last[1], False)
            self.down = False         # missed release: never wedge the pointer
            self.last = None
        self.fresh = True
        return None
