"""The tick model (#217): ONE scheduler for a cart's logic and its draw.

Logic runs at the cart's declared rate -- 30, or 60 by manifest -- and is
never reduced, because reducing it IS slowdown for a frame-counted cart (every
imported PICO-8 cart, and the kid cart that does `x += 1`). A late frame runs
extra ticks to catch up only while a tick is CHEAP (under half the period,
measured per tick); past that line a late frame slows time, as PICO-8's does,
and debt past one period is written off and REPORTED as a miss.

Draw runs on an integer divisor N of the tick: 30-from-60 is perfectly even,
where "draw 45 of 60" is 1-1-2 delivery that judders. N is chosen from an
honest account of a draw cycle -- one drawing frame plus N-1 frames that only
tick, D + (N-1)*T, which must fit N periods -- with D and T as slow EMAs of
what the loop's frames cost, never a last sample. N steps up only when the
tick was not held (debt written off, or ticks running as catch-up bursts) AND
a larger N would fit; it steps down when N-1 fits with room. A loop whose
tick-only frame already costs a period (T >= P) cannot be helped by drawing
less often, so N pins at 1 and the misses say so. STEADY and FREE are one
parameter -- how long the scheduler remembers before it re-decides N: a
rolling window, or every draw frame.

Pure arithmetic on an INJECTED dt, never a clock, so a test can walk an exact
trajectory; and allocation-free per call, because the Player runs it on every
loop frame of an ESP32.
"""

MAX_CATCHUP = 4       # logic ticks one frame may run to catch up
MAX_DIV = 4           # past this, drawing less often buys nothing a kid would keep
EPS = 0.02            # of a period: absorbs an integer-ms host frame (33 vs 33.33)
STEADY_S = 2.0        # STEADY re-decides N once per window of this many seconds
MARGIN = 0.85         # a lower N is taken only when its cycle fits with this much room
LATE_SHARE = 8        # the tick was not held when more than 1/8 of a window's ticks came late
ALPHA = 0.125         # the frame-cost EMAs: a hitch is one sample in eight


class TickScheduler:
    def __init__(self):
        self.rate = 0
        self.period = 0.0
        self.tick_ms = 0
        self.steady = True
        self.div = 1
        self.n = 0            # plan()'s answer: logic ticks this frame
        self.draw = False     # plan()'s answer: this frame draws
        self.reset()

    def reset(self):
        self.acc = 0.0
        self.phase = 0
        self.ticks = 0
        self.draws = 0
        self.misses = 0       # frames whose debt past one period was written off
        self.tick_cost = 0.0  # the last logic tick, seconds (the CHEAP rule)
        self.draw_frame = 0.0  # D: a drawing loop frame, wall seconds (EMA)
        self.tick_frame = 0.0  # T: a loop frame that did not draw, wall seconds (EMA)
        self.late = 0.0       # share of ticks a draw cycle ran beyond its N (EMA)
        self._d_known = False
        self._t_known = False
        self._primed = False
        self._drew = False
        self._ticked = False
        self._cyc_ticks = 0   # ticks since the last draw
        self._cyc_misses = 0  # periods written off since the last draw
        self._win_s = 0.0
        self._win_ticks = 0
        self._win_late = 0
        self.n = 0
        self.draw = False

    def start(self, rate, steady=True):
        """Arm for a run at `rate` Hz (60 opt-in; anything else is the 30 the
        console guarantees), with a fresh divisor of 1."""
        self.rate = 60 if rate == 60 else 30
        self.period = 1.0 / self.rate
        self.tick_ms = 1000 // self.rate
        self.steady = bool(steady)
        self.div = 1
        self.reset()

    def steady_mode(self, on):
        self.steady = bool(on)
        self._reset_window()

    def note_tick(self, seconds):
        """What the logic tick that just ran cost, on the host clock."""
        self.tick_cost = seconds

    def plan(self, dt):
        """Account one loop frame of `dt` seconds. Sets `n` (logic ticks to run
        now) and `draw` (whether this frame draws), and returns `draw`."""
        per = self.period
        # The previous frame's cost arrives as this frame's dt. A drawing
        # frame teaches D; a frame that only ticked teaches T; an idle frame
        # is the loop's floor, which a tick would add its own cost to. The
        # first frame's dt is the time since whatever ran before the cart.
        if self._primed:
            if self._drew:
                self._ema_d(dt)
            elif self._ticked:
                self._ema_t(dt)
            else:
                self._ema_t(dt + self.tick_cost)
        self._primed = True
        acc = self.acc + dt
        n = 0
        floor = per * (1.0 - EPS)
        if acc >= floor:
            acc -= per
            n = 1
            if self.tick_cost * 2.0 < per:
                while n < MAX_CATCHUP and acc >= floor:
                    acc -= per
                    n += 1
            if acc > per:            # what cannot be paid is written off
                acc = per
                self.misses += 1
                self._cyc_misses += 1
        self.acc = acc
        self.n = n
        draw = False
        if n:
            self.ticks += n
            self._win_ticks += n
            self._cyc_ticks += n
            ph = self.phase + n
            if ph >= self.div:
                draw = True
                ph %= self.div
            self.phase = ph
        if draw:
            self.draws += 1
            # A draw cycle is N ticks by design. Ticks beyond that between
            # two draws ran as catch-up the design did not plan for, and a
            # period written off is a tick that never ran at all: either way
            # the draw came late. Bursts INSIDE a cycle are the design --
            # after a 40ms draw frame the next frame owes two ticks -- so
            # they do not count; nor does a host that always runs two per
            # draw. One late cycle in a window is a hitch; one in eight ticks
            # is the tick not held.
            cyc = self._cyc_ticks
            extra = (cyc - self.div if cyc > self.div else 0) + self._cyc_misses
            self._cyc_ticks = 0
            self._cyc_misses = 0
            self._win_late += extra
            self.late += (extra / cyc - self.late) * ALPHA
        self.draw = draw
        self._drew = draw
        self._ticked = n > 0 and not draw
        if self.steady:
            self._win_s += dt
            if self._win_s >= STEADY_S:
                self._decide(self._win_late * LATE_SHARE > self._win_ticks)
                self._reset_window()
        elif draw:
            self._decide(self.late * LATE_SHARE > 1.0)
        return draw

    def fits(self, div, margin=1.0):
        """Whether a draw cycle at `div` -- one drawing frame and div-1 frames
        that only tick -- fits its div periods, with `margin` of them to use."""
        t = self.tick_frame if self._t_known else 0.0
        return self.draw_frame + (div - 1) * t <= margin * div * self.period

    def _ema_d(self, x):
        if self._d_known:
            self.draw_frame += (x - self.draw_frame) * ALPHA
        else:
            self.draw_frame = x
            self._d_known = True

    def _ema_t(self, x):
        if self._t_known:
            self.tick_frame += (x - self.tick_frame) * ALPHA
        else:
            self.tick_frame = x
            self._t_known = True

    def _reset_window(self):
        self._win_s = 0.0
        self._win_ticks = 0
        self._win_late = 0

    def _decide(self, late):
        """One verdict on N from a window's evidence: at most one step."""
        per = self.period
        if (self._t_known and self.tick_frame >= per) or self.tick_cost >= per:
            # No divisor helps a loop whose non-drawing frame already costs a
            # period, or a logic tick that does: the misses report it.
            if self.div != 1:
                self._set_div(1)
            return
        if late:
            d = self.div + 1
            while d <= MAX_DIV:
                if self.fits(d):
                    self._set_div(d)
                    return
                d += 1
            return
        if self.div > 1 and self.fits(self.div - 1, MARGIN):
            self._set_div(self.div - 1)

    def _set_div(self, div):
        self.div = div
        self.phase %= div
        self._cyc_ticks = 0
        self._cyc_misses = 0
        self.late = 0.0          # the old N's lateness says nothing about this one
