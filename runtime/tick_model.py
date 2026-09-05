"""The tick model (#217): ONE scheduler for a cart's logic and its draw.

Logic runs at the cart's declared rate -- 30, or 60 by manifest -- and is
never reduced, because reducing it IS slowdown for a frame-counted cart (every
imported PICO-8 cart, and the kid cart that does `x += 1`). A late frame runs
extra ticks to catch up only while a tick is CHEAP (under half the period,
measured per tick); past that line a late frame slows time, as PICO-8's does,
and debt past one period is written off and REPORTED as a miss.

Draw runs on an integer divisor N of the tick, chosen from what the last draw
frames cost: 30-from-60 is perfectly even, where "draw 45 of 60" is 1-1-2
delivery that judders. STEADY and FREE are one parameter -- how long the
scheduler remembers before it re-decides N: a rolling window with hysteresis,
or every draw frame.

Pure arithmetic on an INJECTED dt, never a clock, so a test can walk an exact
trajectory; and allocation-free per call, because the Player runs it on every
loop frame of an ESP32.
"""

MAX_CATCHUP = 4       # logic ticks one frame may run to catch up
MAX_DIV = 4           # past this, drawing less often buys nothing a kid would keep
EPS = 0.02            # of a period: absorbs an integer-ms host frame (33 vs 33.33)
STEADY_S = 2.0        # STEADY re-decides N once per window of this many seconds
MARGIN = 0.85         # a lower N is taken only when the window's draws fit it with room
MISS_SHARE = 8        # STEADY steps N up when more than 1/8 of a window's draws overran


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
        self.tick_frame = 0.0  # the last logic-only loop frame, wall seconds
        self.draw_frame = 0.0  # the last drawing loop frame, wall seconds
        self._drew = False
        self._ticked = False
        self._win_s = 0.0
        self._win_draws = 0
        self._win_over = 0
        self._win_max = 0.0
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
        self._win_s = 0.0
        self._win_draws = 0
        self._win_over = 0
        self._win_max = 0.0

    def note_tick(self, seconds):
        """What the logic tick that just ran cost, on the host clock."""
        self.tick_cost = seconds

    def plan(self, dt):
        """Account one loop frame of `dt` seconds. Sets `n` (logic ticks to run
        now) and `draw` (whether this frame draws), and returns `draw`."""
        per = self.period
        # The previous frame's cost arrives as this frame's dt: a drawing
        # frame teaches the divisor, a logic-only one prices the ticks the
        # divisor's budget must leave room for.
        if self._drew:
            self._learn(dt)
        elif self._ticked:
            self.tick_frame = dt
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
        self.acc = acc
        self.n = n
        draw = False
        if n:
            self.ticks += n
            ph = self.phase + n
            if ph >= self.div:
                draw = True
                ph %= self.div
            self.phase = ph
        if draw:
            self.draws += 1
        self.draw = draw
        self._drew = draw
        self._ticked = n > 0 and not draw
        if self.steady:
            self._win_s += dt
            if self._win_s >= STEADY_S:
                self._settle()
        return draw

    def _budget(self, div):
        """The wall time a drawing frame may take under divisor `div`: its
        share of the tick, less the logic-only frames between draws."""
        return div * self.period - (div - 1) * self.tick_frame

    def _learn(self, cost):
        self.draw_frame = cost
        over = cost > self._budget(self.div)
        if self.steady:
            self._win_draws += 1
            if over:
                self._win_over += 1
            if cost > self._win_max:
                self._win_max = cost
            return
        if over:
            self._step_up()
        elif self.div > 1 and cost <= MARGIN * self._budget(self.div - 1):
            self._step(-1)

    def _settle(self):
        """A STEADY window closed: one verdict from the whole of it."""
        draws = self._win_draws
        if draws:
            if self._win_over * MISS_SHARE > draws:
                self._step_up()
            elif self.div > 1 and self._win_max <= MARGIN * self._budget(self.div - 1):
                self._step(-1)
        self._win_s = 0.0
        self._win_draws = 0
        self._win_over = 0
        self._win_max = 0.0

    def _step_up(self):
        """Drawing less often buys a cadence back only while the logic tick
        itself fits a period. A logic-bound cart (moss moss: 92 of 107ms is
        its update) keeps its divisor and its misses say so."""
        if self.tick_cost < self.period:
            self._step(1)

    def _step(self, d):
        div = self.div + d
        if div < 1:
            div = 1
        elif div > MAX_DIV:
            div = MAX_DIV
        self.div = div
        self.phase %= div
