"""The tick model (#217): ONE scheduler for a cart's logic and its draw.

Logic runs at the cart's declared rate -- 30, or 60 by manifest -- and is
never reduced, because reducing it IS slowdown for a frame-counted cart (every
imported PICO-8 cart, and the kid cart that does `x += 1`). A late frame runs
extra ticks to catch up only while a tick is CHEAP (under half the period,
measured per tick); past that line a late frame slows time, as PICO-8's does,
and debt past one period is written off and REPORTED as a miss.

Draw runs on an integer divisor N of the tick: 30-from-60 is perfectly even,
where "draw 45 of 60" is 1-1-2 delivery that judders. Whether an N HOLDS is
measured, never predicted: a draw cycle is N ticks by design, and every tick
it runs beyond that (catch-up the design did not plan for), every period
written off, and every stall is a late event; a window is late when its
cycles ran more than a third of a tick late on average, and VERY late when
they averaged most of a whole one -- a cart losing a third of its time or
more, which a second window of waiting would spend again. N steps UP after
two late windows in a row, or after ONE very late window, to the smallest
larger N whose cycle -- one drawing frame plus N-1 tick-only frames, D +
(N-1)*T on slow EMAs -- fits its periods; that is the only thing `fits`
predicts. N steps DOWN only by PROBING: after K clean windows try N-1 for
one window, and keep it only if that window came back CLEAN, a stricter bar
than the one that HOLDS an N. A failed probe records what its cycle cost and
the next probe waits for EVIDENCE -- that cycle costing a fifth less, so the
scene really did get cheaper -- or for a long back-off ceiling, whichever
comes first, so a cart held in one heavy scene stops probing instead of
dropping into slow motion for a window every back-off period; and the first
late window after a probe PASSED steps back at once, because that probe was
the optimistic one. A loop whose tick-only frame already costs a period
(T >= P) cannot be helped by drawing less often, so N pins at 1 and the
misses say so. The first window after a
start is warm-up: it teaches nothing and decides nothing. STEADY and FREE are
one parameter -- how long a window is.

Pure arithmetic on an INJECTED dt, never a clock, so a test can walk an exact
trajectory; and allocation-free per call, because the Player runs it on every
loop frame of an ESP32.
"""

MAX_CATCHUP = 4       # logic ticks one frame may run to catch up
MAX_DIV = 4           # past this, drawing less often buys nothing a kid would keep
EPS = 0.02            # of a period: absorbs an integer-ms host frame (33 vs 33.33)
STEADY_S = 2.0        # a STEADY window, seconds
FREE_S = 0.25         # a FREE window: follows load in a quarter second, judders for it
LATE_CYCLE = 3        # a window is late when its cycles averaged over 1/3 tick late
VERY_LATE = 0.8       # ...and VERY late at four fifths of a whole one: step up on ONE
STALL = 4             # a frame over this many periods is ONE late event, not many ticks
PROBE_K = 2           # clean windows at N before N-1 is tried
PROBE_LATE = 16       # a probe holds only under 1/16 of a tick late per cycle
PROBE_BACK = 32       # windows a FAILED probe waits before time alone retries it
PROBE_MAX = 64        # the ceiling on that wait: ~2 minutes of STEADY windows
PROBE_DROP = 0.2      # ...or sooner, once the cycle it failed on costs this much less
ALPHA = 0.125         # the frame-cost EMAs: a hitch is one sample in eight


class TickScheduler:
    def __init__(self):
        self.rate = 0
        self.period = 0.0
        self.tick_ms = 0
        self.steady = True
        # UNCAPPED (a DIAG knob, serial `uncap 1`, never persisted): every
        # loop frame draws while logic keeps its rate. The game runs at its
        # own speed and the draw+present path runs flat out -- the number a
        # board's "free-running fps" question is asking for, without the
        # governor that makes the answer 30. Off, this file is unchanged.
        self.uncapped = False
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
        self.late = 0.0       # late events per draw cycle (EMA, a diagnostic)
        self.probing = False  # this window tries N-1; `_probe_from` is the N it left
        self._probe_from = 0
        self._probe_k = PROBE_K
        self._fail_cost = 0.0  # what the cycle cost when a probe last failed here
        self._fresh_from = 0   # a probe just PASSED: the N one late window returns to
        self._clean = 0       # clean windows in a row at this N
        self._late_wins = 0   # late windows in a row at this N
        self._warm = True     # the first window teaches and decides nothing
        self._d_known = False
        self._t_known = False
        self._primed = False
        self._drew = False
        self._ticked = False
        self._cyc_ticks = 0   # ticks since the last draw
        self._cyc_misses = 0  # periods written off since the last draw
        self._cyc_stall = False
        self._win_s = 0.0
        self._win_cycles = 0
        self._win_late = 0
        self._win_stalls = 0
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

    def uncap_mode(self, on):
        self.uncapped = bool(on)
        self._reset_window()

    def note_tick(self, seconds):
        """What the logic tick that just ran cost, on the host clock."""
        self.tick_cost = seconds

    def plan(self, dt):
        """Account one loop frame of `dt` seconds. Sets `n` (logic ticks to run
        now) and `draw` (whether this frame draws), and returns `draw`."""
        per = self.period
        stall = dt > STALL * per
        # The previous frame's cost arrives as this frame's dt. A drawing
        # frame teaches D; a frame that only ticked teaches T; an idle frame
        # is the loop's floor, which a tick would add its own cost to. Not
        # during warm-up, and never the first frame (its dt is the time since
        # whatever ran before the cart). A stall teaches too: one sample in
        # eight decays before a window closes, and a cart whose EVERY frame
        # stalls is not stalling, it is that slow.
        if self._primed and not self._warm:
            if self._drew:
                self._ema_d(dt)
            elif self._ticked:
                self._ema_t(dt)
            else:
                self._ema_t(dt + self.tick_cost)
        self._primed = True
        if stall:
            self._cyc_stall = True
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
            self._cyc_ticks += n
            ph = self.phase + n
            if ph >= self.div:
                draw = True
                ph %= self.div
            self.phase = ph
        if self.uncapped:
            # DIAG: draw this frame whatever the divisor says, and teach the
            # divisor nothing -- the cycles it would measure are not cycles.
            self.draws += 1
            self.draw = True
            self._drew = True
            self._ticked = False
            self._win_s += dt
            if self._win_s >= (STEADY_S if self.steady else FREE_S):
                self._warm = False
                self._reset_window()
            return True
        if draw:
            self.draws += 1
            # A draw cycle is N ticks by design. Ticks beyond that ran as
            # catch-up the design did not plan for, and a period written off
            # is a tick that never ran: either way the draw came late.
            # Bursts INSIDE a cycle are the design -- after a 40ms draw frame
            # the next frame owes two ticks -- so they do not count, nor does
            # a host that always runs two per draw. A stall (a 700ms radio
            # scan, a cart load) is one event however many ticks it cost.
            cyc = self._cyc_ticks
            late = (cyc - self.div if cyc > self.div else 0) + self._cyc_misses
            if self._cyc_stall:
                if late > 1:
                    late = 1
                self._win_stalls += late
            self._cyc_ticks = 0
            self._cyc_misses = 0
            self._cyc_stall = False
            self._win_cycles += 1
            self._win_late += late
            self.late += (late - self.late) * ALPHA
        self.draw = draw
        self._drew = draw
        self._ticked = n > 0 and not draw
        self._win_s += dt
        if self._win_s >= (STEADY_S if self.steady else FREE_S):
            if self._warm:
                self._warm = False
            else:
                self._decide()
            self._reset_window()
        return draw

    def cycle(self, div):
        """What a draw cycle at `div` costs on the slow EMAs: one drawing
        frame and div-1 frames that only tick."""
        t = self.tick_frame if self._t_known else 0.0
        return self.draw_frame + (div - 1) * t

    def fits(self, div):
        """Whether a draw cycle at `div` fits its div periods. A prediction,
        and the only one the scheduler makes: it picks the N to step UP to."""
        return self.cycle(div) <= div * self.period

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
        self._win_cycles = 0
        self._win_late = 0
        self._win_stalls = 0

    def _probe_reset(self):
        """A divisor arrived at rather than probed down to owes nothing to
        the probe that failed at the one before it."""
        self._probe_k = PROBE_K
        self._fail_cost = 0.0

    def _may_probe(self):
        """Whether N-1 has earned a window. A probe that FAILED here is not
        repeated until the cycle it failed on costs PROBE_DROP less -- the
        scene got cheaper, so N-1 might hold now -- or until the back-off
        ceiling runs out. A cart held in one heavy state therefore stops
        probing after a failure or two, instead of dropping into slow motion
        for a window every time a timer comes round."""
        if self._clean >= self._probe_k:
            return True
        return (self._fail_cost > 0.0
                and self.cycle(self.div - 1) <= self._fail_cost * (1.0 - PROBE_DROP))

    def _probe_failed(self, back):
        """N-1 did not hold: back to `back`, remember what its cycle cost
        while it did not, and wait longer before asking again."""
        k = min(max(self._probe_k * 2, PROBE_BACK), PROBE_MAX)
        self._set_div(back)
        self._fail_cost = self.cycle(back - 1)
        self._probe_k = k

    def _decide(self):
        """One verdict on N from the closing window's evidence: at most one
        step."""
        cyc = self._win_cycles
        n_late = self._win_late
        late = n_late * LATE_CYCLE > cyc
        # A stall is one late event and never evidence about a scene, so it
        # comes off before asking whether the window was VERY late -- else a
        # single 700ms hitch is a whole late cycle of a quarter-second window.
        very = cyc > 0 and (n_late - self._win_stalls) >= cyc * VERY_LATE
        per = self.period
        if late and ((self._t_known and self.tick_frame >= per)
                     or self.tick_cost >= per):
            # No divisor helps a loop whose non-drawing frame already costs a
            # period, or a logic tick that does: the misses report it. Only
            # when the window WAS late -- an N that held is not moved by a
            # stall that passed through T's average.
            if self.div != 1:
                self._set_div(1)
            self.probing = False
            self._probe_reset()
            self._clean = 0
            self._late_wins = 0
            return
        if self.probing:
            # The window that tried N-1 is in. It keeps N-1 only if it came
            # back CLEAN: the ordinary threshold would hold an N-1 that loses
            # a tick every third cycle, and that is the slow motion a kid
            # reads as a bug.
            self.probing = False
            self._late_wins = 0
            if n_late * PROBE_LATE > cyc:
                self._probe_failed(self._probe_from)
            else:
                self._probe_reset()
                self._fresh_from = self._probe_from
                self._clean = 1
            return
        if not late:
            self._late_wins = 0
            self._clean += 1
            if self.div > 1 and self._clean >= PROBE_K and self._may_probe():
                self.probing = True
                self._probe_from = self.div
                self._clean = 0
                self._set_div(self.div - 1)
            return
        self._clean = 0
        if self._fresh_from:
            # The first late window after a probe passed: that probe was
            # optimistic, so go back now rather than spend a second window
            # proving what this one already showed.
            self._late_wins = 0
            self._probe_failed(self._fresh_from)
            return
        self._late_wins += 1
        if not very and self._late_wins < 2:
            return
        self._late_wins = 0
        d = self.div + 1
        while d <= MAX_DIV:
            if self.fits(d):
                self._set_div(d)
                self._probe_reset()
                return
            d += 1

    def _set_div(self, div):
        self.div = div
        self.phase %= div
        self._cyc_ticks = 0
        self._cyc_misses = 0
        self._cyc_stall = False
        self._fresh_from = 0
        self.late = 0.0          # the old N's lateness says nothing about this one
