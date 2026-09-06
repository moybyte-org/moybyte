"""The tick model (#217): one scheduler in the Player for a cart's logic and
its draw. Logic at the cart's declared rate, never reduced; draw on an integer
divisor chosen from what draw frames cost; STEADY / FREE is how long the
divisor remembers. `runtime/tick_model.py` is pure arithmetic on an injected
dt, so the first half walks exact trajectories; the second half drives the
real Player through `ws.frame`."""

import pytest

from runtime import tick_model
from runtime.tick_model import TickScheduler, MAX_CATCHUP, MAX_DIV


# -- the scheduler, as arithmetic ---------------------------------------------

W = tick_model.STEADY_S


def _sched(rate=30, steady=True):
    s = TickScheduler()
    s.start(rate, steady)
    return s


def _loop(s, secs, D, T, tick_cost=0.0005, idle=None, stall_every=None,
          stall=0.7, t0=0.0):
    """Drive the scheduler the way a free-running board loop does: a frame
    that drew costs D, a frame that only ticked costs T, an idle frame costs
    `idle` (the loop's floor; T by default), and every `stall_every` seconds
    one frame stalls for `stall` seconds (a radio scan, a cart load).
    Returns (time, div) per frame, with time counted from `t0`."""
    t = 0.0
    out = []
    next_stall = stall_every
    while t < secs:
        if s.draw:
            dt = D
        elif s.n:
            dt = T
        else:
            dt = T if idle is None else idle
        if next_stall is not None and t >= next_stall:
            dt = stall
            next_stall += stall_every
        s.plan(dt)
        if s.n:
            s.note_tick(tick_cost)
        t += dt
        out.append((t0 + t, s.div))
    return out


def _changes(trace):
    """The (time, new N) of every change in a trace."""
    out = []
    last = trace[0][1]
    for t, d in trace:
        if d != last:
            out.append((t, d))
            last = d
    return out


def test_a_thirty_cart_on_a_sixty_loop_ticks_every_other_frame():
    s = _sched(30)
    plan, ns = [], []
    for _ in range(60):
        plan.append(s.plan(1 / 60))
        ns.append(s.n)
    assert s.ticks == 30 and s.draws == 30 and s.misses == 0
    assert max(ns) == 1, "never two ticks in one frame at this pair of rates"
    # every drawing frame is a ticking frame, and they alternate evenly
    assert plan[1::2] == [True] * 30 and plan[0::2] == [False] * 30


def test_a_sixty_cart_on_a_thirty_loop_runs_two_ticks_per_draw():
    """PICO-8's own degraded mode, and not ours to improve on: a 60fps cart on
    a 30fps host runs two ticks inside one frame and draws once, every
    frame, however long it runs (the host simulator paces exactly like this).
    N may read 2 there -- two ticks per draw IS the cycle -- and that changes
    no frame: every frame still draws."""
    s = _sched(60)
    ns, draws = [], []
    for _ in range(30 * 20):
        draws.append(s.plan(1 / 30))
        ns.append(s.n)
    assert ns == [2] * 600 and all(draws)
    assert s.ticks == 1200 and s.draws == 600 and s.misses == 0
    assert s.div <= 2


def test_anything_but_sixty_is_the_thirty_the_console_guarantees():
    for rate in (0, 30, 45, 120, None):
        assert _sched(rate).rate == 30
    assert _sched(60).rate == 60
    assert _sched(30).tick_ms == 33 and _sched(60).tick_ms == 16


def test_the_draw_never_precedes_the_first_tick():
    """A frame shorter than one period ticks nothing and so draws nothing --
    dank tomb positioned its player in the first update and its first draw
    indexed nil on every board whose first frame came in under a period."""
    s = _sched(60)
    assert s.plan(1 / 125) is False and s.n == 0
    assert s.plan(1 / 125) is False and s.n == 0
    assert s.plan(1 / 125) is True and s.n == 1


def test_a_late_frame_catches_up_only_while_a_tick_is_cheap():
    """The 2026-09-02 rule: extra ticks while a tick costs under half the
    period; past that line a late frame slows time instead of snowballing."""
    s = _sched(30)
    s.note_tick(0.002)
    s.plan(3 / 30)
    assert s.n == 3 and s.misses == 0
    s = _sched(30)
    s.note_tick(0.020)                       # over half of 33ms: not cheap
    s.plan(3 / 30)
    assert s.n == 1 and s.misses == 1        # two periods owed: one kept, one written off
    s.plan(0.001)
    assert s.n == 1                          # the kept period is paid next frame
    s.plan(0.001)
    assert s.n == 0


def test_catch_up_is_capped_and_the_rest_is_reported():
    s = _sched(30)
    s.note_tick(0.001)
    s.plan(10 / 30)
    assert s.n == MAX_CATCHUP and s.misses == 1


# The trajectories the boards measured (#217, 2026-09-05), as dt sequences.
# D = a drawing loop frame, T = a frame that only ticked (the loop's own
# floor -- flush wait, chrome, I2C), tick = the logic tick itself.

def test_hop_quest_on_the_p4_settles_at_one_through_a_slow_first_window():
    """D=21 against a 16.67 period: one 21ms draw frame, then two cheap
    ticks the next -- 60Hz logic, ~47 drawn, no misses. `fits(1)` is false at
    any margin, which is why the first cut could never step back DOWN once a
    slow start had pushed it to 2 (4 of 10 cart starts); whether N=1 holds is
    known only by trying it. The first window is warm-up: twice as slow here,
    and it decides nothing."""
    s = _sched(60)
    trace = _loop(s, W, D=0.042, T=0.015, tick_cost=0.001)
    trace += _loop(s, 20.0, D=0.021, T=0.0075, tick_cost=0.001, t0=W)
    assert all(d == 1 for t, d in trace if t > 2 * W)
    assert s.div == 1
    assert s.misses <= 1
    assert s.draws / 22.0 > 44


def test_hop_quest_on_the_tdeck_holds_sixty_at_one():
    """D=17 against 16.67 is an overrun catch-up absorbs -- one extra tick
    every ~50 frames, ~58 drawn -- and T=16 means a tick-only frame costs a
    whole period, so N=2 could only be worse. The first cut's budget shrank
    as N grew and read 30/30 for a cart that had run 60/58."""
    s = _sched(60)
    trace = _loop(s, 14.0, D=0.017, T=0.016, tick_cost=0.00024)
    assert all(d == 1 for _t, d in trace)
    assert s.misses <= 1
    assert s.draws / 14.0 > 56


def test_bunnysurvivor_on_the_p4_does_not_hunt_on_a_periodic_stall():
    """D=11, T=5 fit a period with room; a 700ms stall every 11s (the BLE
    scan) wrote off many ticks and counted as many late events, so one hitch
    flipped a window and a solo run hunted 1-2-1-2. A stall is ONE event."""
    s = _sched(60)
    trace = _loop(s, 40.0, D=0.011, T=0.005, tick_cost=0.001, stall_every=11.0)
    assert all(d == 1 for _t, d in trace)
    assert 2 <= s.misses <= 4, "the stalls themselves, and nothing else"


def test_celeste_on_the_tdeck_holds_thirty_at_one():
    s = _sched(30)
    trace = _loop(s, 14.0, D=0.027, T=0.0005, tick_cost=0.0055)
    assert all(d == 1 for _t, d in trace)
    assert s.misses == 0
    assert abs(s.draws / 14.0 - 30) < 1


def test_moss_moss_pins_at_one_and_reports_misses():
    """92 of 107ms is logic: T=49 exceeds the 33ms period, so no divisor
    helps. It runs one tick per frame at N=1 and the misses say so.

    T cannot be measured while every frame draws (no idle frame, no
    tick-only frame), so the first very late window buys ONE step to N=2; its
    tick-only frames price T, the next window pins N=1, and it stays there --
    T is only ever re-sampled by frames that do not draw."""
    s = _sched(30)
    trace = _loop(s, 20.0, D=0.037, T=0.049, tick_cost=0.0278)
    assert max(d for _t, d in trace) <= 2, "one probe step, never a ratchet"
    assert all(d == 1 for t, d in trace if t > 5 * W)
    assert s.div == 1 and s.misses > 0
    assert s.rate == 30


# The owner's two reports from the T-Deck (#217, 2026-09-06) and what they
# asked of the model: a probe is gated on evidence, and a very late window
# does not wait for a second one.

def test_moss_moss_on_the_tdeck_stops_probing_until_the_scene_gets_cheaper():
    """"quite buggy and slow". At 30/2 it holds -- a 46ms drawing frame and a
    20ms tick-only one inside two 33ms periods -- but the model probed N=1
    back on a timer, and each probe ran the cart at half speed for a window
    before it was undone. One probe fails; the next waits for the SCENE to
    get a fifth cheaper, which in a heavy state never comes."""
    s = _sched(30)
    trace = _loop(s, 6 * W, D=0.046, T=0.020, tick_cost=0.020)
    changes = _changes(trace)
    assert [d for _t, d in changes] == [2, 1, 2], "up, one probe, back"
    assert changes[2][0] - changes[1][0] < 1.6 * W, "the probe cost one window"
    misses = s.misses
    trace = _loop(s, 60.0, D=0.046, T=0.020, tick_cost=0.020, t0=6 * W)
    assert _changes(trace) == [], "a minute of the same scene, and no probe"
    assert s.misses == misses, "N=2 held its tick throughout"
    _loop(s, 3 * W, D=0.032, T=0.020, tick_cost=0.014, t0=6 * W + 60)
    assert s.div == 1, "a 30% cheaper scene re-opens the probe, and it holds"


def test_a_probe_needs_a_clean_window_and_a_fresh_one_reverts_at_once():
    """Two halves of one rule. A probe that lands on an N-1 which is MILDLY
    late -- D=21 against a 16.7ms period is a quarter tick per cycle, under
    the threshold that holds an N -- is not passed: a probe has to come back
    clean, or the kid gets the slow motion the threshold tolerates. And when
    a probe DID pass and the very next window is late, the probe was
    optimistic, so N goes back without waiting for a second window."""
    s = _sched(60)
    _loop(s, 4 * W, D=0.025, T=0.0075, tick_cost=0.001)
    assert s.div == 2
    trace = _loop(s, 8 * W, D=0.021, T=0.0075, tick_cost=0.001, t0=4 * W)
    changes = _changes(trace)
    assert [d for _t, d in changes] == [1, 2], "the probe is tried and refused"
    assert changes[1][0] - changes[0][0] < 1.6 * W, "after one window"
    assert s.div == 2

    s = _sched(60)
    _loop(s, 4 * W, D=0.025, T=0.0075, tick_cost=0.001)
    _loop(s, 4 * W, D=0.010, T=0.002, tick_cost=0.001, t0=4 * W)
    assert s.div == 1, "onto a clean N=1 the probe passes"
    trace = _loop(s, 2 * W, D=0.025, T=0.0075, tick_cost=0.001, t0=8 * W)
    changes = _changes(trace)
    assert [d for _t, d in changes] == [2]
    assert changes[0][0] - 8 * W < 1.3 * W, "one late window, not two"


def test_bunnysurvivor_steps_up_inside_the_wave_and_comes_back_after():
    """"performance drops hard when there is a lot going on the screen and
    the game slows down even though STEADY is on". A wave doubles both halves
    of the frame -- the drawing frame 11 -> 21ms, the logic tick 6 -> 12 --
    and at N=1 that writes off a period every frame. A window of that is very
    late, so N=2 lands INSIDE the wave rather than after it and the tick
    holds for the rest; when the wave ends one probe brings N back."""
    s = _sched(60)
    _loop(s, 3 * W, D=0.011, T=0.006, tick_cost=0.006)
    assert s.div == 1 and s.misses == 0
    _loop(s, 2.1, D=0.021, T=0.012, tick_cost=0.012, t0=3 * W)
    assert s.div == 2, "one window, inside a three-second wave"
    held = s.misses
    _loop(s, 0.9, D=0.021, T=0.012, tick_cost=0.012, t0=3 * W + 2.1)
    assert s.misses - held <= 1, "and N=2 holds the tick for the rest of it"
    trace = _loop(s, 10.0, D=0.011, T=0.006, tick_cost=0.006, t0=3 * W + 3.0)
    assert [d for _t, d in _changes(trace)] == [1], "back to 1, once, no hunting"


def test_one_stall_is_not_a_very_late_window():
    """A stall is ONE late event, and it must not become a very late WINDOW
    either. A FREE window is a quarter second, so a stall that long is the
    only cycle its window has, and every cycle of that window lost a tick --
    which is the very late reading, from a radio scan. Swept across the phase
    of the window the stall lands in, because how many cycles it shares that
    window with is exactly what the reading depends on."""
    for steady in (True, False):
        for stall in (0.300, 0.700):
            for k in range(90):
                s = _sched(60, steady=steady)
                _loop(s, 4 * W, D=0.011, T=0.002, tick_cost=0.001)
                for _ in range(k):
                    s.plan(0.011 if s.draw else 0.002)
                    if s.n:
                        s.note_tick(0.001)
                s.plan(stall)
                trace = _loop(s, 2 * W, D=0.011, T=0.002, tick_cost=0.001)
                assert s.div == 1 and all(d == 1 for _t, d in trace), (steady, stall, k)


def test_a_draw_heavy_sixty_cart_settles_at_three_and_stops_probing():
    """D=40, T=2 at 60Hz: N=2 does not fit (40+2 > 33), N=3 does (40+4 <=
    50). At N=1 the cycles ran 2-3 ticks each -- game time caught up, motion
    did not, which is a whole tick lost per cycle -- so ONE window steps
    straight to 3. N=2 is then tried ONCE; its cycles alternate 2 and 3
    ticks, so the probe fails, and because the scene never gets cheaper it is
    not tried again for a minute of windows: a kid does not get a slow-motion
    window every back-off period. Once the scene DOES get cheaper the gate
    opens on the evidence, without waiting the back-off out."""
    s = _sched(60)
    trace = _loop(s, 60.0, D=0.040, T=0.002, tick_cost=0.001)
    changes = _changes(trace)
    assert changes[0][1] == 3, "straight to the smallest N that fits"
    assert changes[0][0] < 2.2 * W, "one very late window is enough"
    assert s.div == 3
    probes = [t for t, d in changes if d == 2]
    assert len(probes) == 1, "tried once, and the failure is remembered"
    back = [tb for tb, d in changes if d == 3 and tb > probes[0]][0]
    assert W * 0.9 < back - probes[0] < W * 1.6, "the probe cost one window"
    assert all(d != 1 for t, d in trace if t > 2.1 * W), "never back to a 1 that failed"
    trace = _loop(s, 20.0, D=0.028, T=0.002, tick_cost=0.001, t0=60.0)
    assert s.div == 2, "a 30% cheaper scene re-opens the probe on evidence"
    assert _changes(trace)[0][0] < 60.0 + 5 * W


def test_steady_changes_n_at_most_once_per_window():
    s = _sched(60)
    trace = []
    t = 0.0
    # a load that flips between light and heavy every second, on purpose
    # faster than a window can follow
    while t < 40.0:
        heavy = int(t) % 2 == 1
        trace += _loop(s, 1.0, D=0.040 if heavy else 0.010, T=0.002,
                       tick_cost=0.001, t0=t)
        t += 1.0
    changes = [ct for ct, _d in _changes(trace)]
    assert changes, "the load did move N at all"
    gaps = [b - a for a, b in zip(changes, changes[1:])]
    assert all(g >= W - 0.05 for g in gaps), gaps


def test_a_step_up_needs_two_late_windows_in_a_row():
    """The ordinary case: a MILDLY late window (D=25 against a 16.7ms period
    runs two ticks every other cycle -- half a tick late) is not yet a scene,
    and the second one in a row is."""
    s = _sched(60)
    _loop(s, 2 * W, D=0.010, T=0.002)         # warm-up + one clean window
    assert s.div == 1
    _loop(s, W, D=0.025, T=0.0075, tick_cost=0.001)
    assert s.div == 1, "one late window is not yet a scene"
    _loop(s, W, D=0.025, T=0.0075, tick_cost=0.001)
    assert s.div == 2


def test_a_very_late_window_steps_up_on_its_own():
    """The second threshold: a window whose cycles each lost most of a whole
    tick is not waited out -- the cart is losing a third of its time and the
    scene may be over before a second window closes."""
    s = _sched(60)
    _loop(s, 2 * W, D=0.010, T=0.002)
    assert s.div == 1
    _loop(s, W, D=0.040, T=0.002, tick_cost=0.001)
    assert s.div == 3, "one window, and straight to the N that fits"


def test_steady_holds_through_a_hitch():
    s = _sched(60)
    _loop(s, 2.5 * W, D=0.012, T=0.002)
    assert s.div == 1
    for _ in range(3):
        s.plan(0.200)                        # GC-class hitches, one per window
        trace = _loop(s, W, D=0.012, T=0.002)
        assert all(d == 1 for _t, d in trace), "a hitch is one late event"


def test_steady_follows_a_scene_both_ways():
    s = _sched(60)
    _loop(s, 2 * W, D=0.012, T=0.002)
    assert s.div == 1
    _loop(s, 3 * W, D=0.040, T=0.002, tick_cost=0.001)      # the boss fight
    assert s.div == 3
    _loop(s, 8 * W, D=0.010, T=0.002, tick_cost=0.001)      # back to the menu
    assert s.div == 1, "each probe down held, so each stuck"


def test_free_follows_the_same_rule_in_a_quarter_second_window():
    s = _sched(60, steady=False)
    _loop(s, 1.0, D=0.040, T=0.002, tick_cost=0.001)
    assert s.div == 3, "FREE reaches the fitting N inside a second"
    _loop(s, 3.0, D=0.010, T=0.002, tick_cost=0.001)
    assert s.div == 1, "and probes back down within a few"
    s = _sched(60, steady=False)
    trace = _loop(s, 6.0, D=0.021, T=0.0075, tick_cost=0.001)
    assert all(d == 1 for _t, d in trace), \
        "a cycle a quarter tick late on average is held, in FREE too"


def test_a_cart_nothing_fits_stays_at_one_and_reports():
    """Drawing less often cannot buy a cadence back when even four periods
    do not hold one draw: N stays 1 rather than pretending."""
    s = _sched(60)
    _loop(s, 8.0, D=0.500, T=0.002, tick_cost=0.001)
    assert s.div == 1 and s.misses > 0
    s = _sched(60)
    _loop(s, 8.0, D=0.060, T=0.002, tick_cost=0.001)
    assert s.div == MAX_DIV


def test_the_first_window_teaches_nothing_and_the_costs_are_slow_averages():
    s = _sched(60)
    _loop(s, W * 0.95, D=0.042, T=0.015)
    assert s.draw_frame == 0.0 and s.tick_frame == 0.0, "warm-up: no samples"
    _loop(s, 3.0, D=0.010, T=0.002, t0=W)
    assert 0.0095 < s.draw_frame < 0.0105
    while not s.draw:
        s.plan(0.002)
    d0 = s.draw_frame
    s.plan(0.050)                            # a drawing frame that took 50ms
    assert d0 < s.draw_frame < 0.02, "one heavy frame is one sample in eight"
    while not s.draw:
        s.plan(0.002)
    d1 = s.draw_frame
    s.plan(0.700)                            # a stall is one sample in eight too
    assert abs(s.draw_frame - (d1 + (0.7 - d1) * tick_model.ALPHA)) < 1e-9


def test_switching_steady_live_keeps_n_and_the_costs():
    s = _sched(60)
    _loop(s, 3 * W, D=0.040, T=0.002, tick_cost=0.001)
    assert s.div == 3
    d = s.draw_frame
    s.steady_mode(False)
    assert s.div == 3 and s.draw_frame == d and s.steady is False
    _loop(s, 2.0, D=0.010, T=0.002, tick_cost=0.001)
    assert s.div == 1


# -- the Player, through ws.frame ---------------------------------------------

def _open(ws, title):
    for i, c in enumerate(ws.launcher.items):
        if c["title"] == title:
            ws.launcher.sel = i
            ws.open()
            assert ws.player.cart_error is None, ws.player.cart_error
            return
    raise AssertionError("no seed cart titled " + title)


def _count(ws):
    calls = {"upd": 0, "draw": 0, "dts": []}
    u0, d0 = ws.player._update, ws.player._draw

    def upd(dt):
        calls["upd"] += 1
        calls["dts"].append(dt)
        if u0:
            u0(dt)

    def draw():
        calls["draw"] += 1
        if d0:
            d0()

    ws.player._update = upd
    ws.player._draw = draw
    return calls


# The paced-game fixture. It was Star Catcher until 2026-09-06, when that cart
# declared `"fps": "free"` (SPEC 5's opt-out: dt-scaled logic, unpaced) -- so
# a seed game that still carries the guaranteed 30 stands in.
PACED_GAME = "Coin Quest"


def _ws(tmp_path):
    from runtime import host_app
    return host_app.build_workstation(str(tmp_path / "carts"))


def _frames(ws, n, dt):
    for _ in range(n):
        ws.input.begin_frame()
        ws.frame(dt)


def test_a_game_holds_its_rate_on_a_faster_loop(tmp_path):
    ws = _ws(tmp_path)
    _open(ws, PACED_GAME)
    assert ws.cart["type"] == "game" and ws.player.tick_ms == 33
    calls = _count(ws)
    drawn0 = ws._frames_drawn
    _frames(ws, 60, 1 / 60)
    assert calls["upd"] == 30                     # 30Hz logic on a 60Hz loop
    assert calls["draw"] == 30
    assert ws._frames_drawn - drawn0 == 30        # a frame that does not draw does not flush
    assert set(calls["dts"]) == {1 / 30}          # the tick period, not the loop's dt


def test_a_sixty_cart_runs_two_ticks_per_draw_on_a_thirty_loop(tmp_path):
    ws = _ws(tmp_path)
    _open(ws, "Hop Quest")
    assert ws.cart["fps"] == 60 and ws.player.tick_ms == 16
    calls = _count(ws)
    _frames(ws, 30, 1 / 30)
    assert calls["upd"] == 60 and calls["draw"] == 30
    assert set(calls["dts"]) == {1 / 60}


def test_the_fps_chip_reads_the_drawn_rate(tmp_path):
    ws = _ws(tmp_path)
    _open(ws, PACED_GAME)
    _frames(ws, 240, 1 / 60)
    assert 29 <= ws._fps <= 31


def test_a_tool_ticks_with_the_loop_it_serves(tmp_path):
    ws = _ws(tmp_path)
    # A tool/app cart the PLAYER runs -- one a registered system app claims
    # opens that app's layer instead, and ticks nothing here.
    tool = next(c["title"] for c in ws.launcher.items
                if c.get("type") in ("tool", "app")
                and not ws.is_system_app(c))
    _open(ws, tool)
    assert ws.player.tick_ms == 0
    calls = _count(ws)
    drawn0 = ws._frames_drawn
    _frames(ws, 20, 1 / 60)
    assert calls["upd"] == 20 and calls["draw"] == 20
    assert ws._frames_drawn - drawn0 == 20
    assert set(calls["dts"]) == {1 / 60}


def test_a_crash_disarms_the_pacing_and_the_panel_paints_every_frame(tmp_path):
    ws = _ws(tmp_path)
    _open(ws, PACED_GAME)

    def boom(dt):
        raise RuntimeError("kaboom")

    ws.player._update = boom
    _frames(ws, 4, 1 / 60)
    assert ws.cart_error is not None
    assert ws.player.tick_ms == 0 and not ws.input._kept
    drawn0 = ws._frames_drawn
    ws._dirty = True
    ws.frame(1 / 60)
    assert ws._frames_drawn - drawn0 == 1


def test_exit_disarms_the_pacing_and_the_launcher_paces_to_the_loop(tmp_path):
    ws = _ws(tmp_path)
    _open(ws, PACED_GAME)
    assert ws.player.tick_ms == 33
    ws.go_home()
    assert ws.player.tick_ms == 0 and not ws.input._kept
    ws._dirty = True
    drawn0 = ws._frames_drawn
    ws.frame(1 / 60)
    assert ws._frames_drawn - drawn0 == 1


def test_a_press_between_ticks_reaches_the_next_tick_exactly_once(tmp_path):
    """Both directions that bit the p8 shim, now the host's: a 30Hz cart on a
    60Hz loop must not lose the edge that landed on the frame it did not tick
    (half of all presses), and two ticks inside one frame must not both see
    it (one tap moved two menu slots)."""
    ws = _ws(tmp_path)
    _open(ws, PACED_GAME)
    btnp = ws.player.ns["btnp"]
    seen = []
    u0 = ws.player._update

    def upd(dt):
        seen.append(bool(btnp("right")))
        u0(dt)

    ws.player._update = upd
    _frames(ws, 2, 1 / 60)                        # frames 1-2: one tick, on frame 2
    ws.input.set_held("right", True)
    ws.input.begin_frame()
    ws.frame(1 / 60)                              # frame 3: the edge lands, no tick
    assert len(seen) == 1 and seen == [False]
    ws.input.begin_frame()
    ws.frame(1 / 60)                              # frame 4: the tick takes it
    assert seen == [False, True]
    ws.input.set_held("right", False)
    _frames(ws, 4, 1 / 60)
    assert seen[2:] == [False, False]
    # Two ticks in one frame: the second sees nothing.
    ws.input.set_held("right", True)
    ws.input.begin_frame()
    ws.frame(2 / 30)
    assert seen[-2:] == [True, False]


def test_a_game_parked_under_a_menu_keeps_no_edges_for_later(tmp_path):
    """The shell owns the input while an Editor or menu covers a running
    game: it must read fresh edges every frame (the block editor's menu
    stepped twice when a stale latch survived), and the game must not resume
    on a press made before it was covered."""
    ws = _ws(tmp_path)
    _open(ws, PACED_GAME)
    _frames(ws, 2, 1 / 60)
    ws.input.set_held("a", True)
    ws.input.begin_frame()
    ws.frame(1 / 60)                              # no tick: the edge is kept
    assert "a" in ws.input._kept
    ws.input.set_held("a", False)
    ws.open_picker()                              # a shell surface over the game
    assert not ws.wm.top_is_player()
    ws.input.begin_frame()
    ws.frame(1 / 60)
    assert not ws.input._kept and ws.player._keyp_latch == 0
    ws.input.begin_frame()
    assert not ws.input.pressed("a"), "the shell reads this frame's edges only"


def test_a_typed_key_edge_latches_to_the_tick(tmp_path):
    ws = _ws(tmp_path)
    _open(ws, PACED_GAME)
    keyp = ws.player.ns["keyp"]
    seen = []
    u0 = ws.player._update

    def upd(dt):
        seen.append(keyp())
        u0(dt)

    ws.player._update = upd
    _frames(ws, 2, 1 / 60)
    ws.input.last_key = ord("x")
    ws.input.begin_frame()
    ws.frame(1 / 60)                              # lands between ticks
    ws.input.begin_frame()
    ws.frame(1 / 60)                              # the tick reads it
    ws.input.last_key = 0
    _frames(ws, 2, 1 / 60)
    assert seen == [0, ord("x"), 0]


def test_steady_persists_and_reaches_the_running_cart(tmp_path):
    ws = _ws(tmp_path)
    assert ws.steady is True                      # the kid default
    _open(ws, PACED_GAME)
    assert ws.player.sched.steady is True
    ws.set_steady(False)
    assert ws.system.get("steady") is False
    assert ws.player.sched.steady is False
    keys = [r[0] for r in ws.settings_layer._settings_rows()]
    assert "steady" in keys and "frameskip" not in keys


def test_a_stored_frameskip_key_is_dropped_on_load(tmp_path):
    ws = _ws(tmp_path)
    ws.system["frameskip"] = True
    ws.prefs.persist()
    ws.load_system()
    assert "frameskip" not in ws.system
    assert ws.steady is True


class _FakeSession:
    """The lockstep session's face the Player reads: it owns the clock."""
    dt = 1 / 30
    config = None
    waiting = False

    def __init__(self):
        self.on = False
        self.advanced = 0
        self.resent = 0

    def pending(self, now):
        return self.on

    def due(self, now):
        return self.on

    def advance(self, mask, now):
        self.advanced += 1
        return True

    def resend(self):
        self.resent += 1


def test_lockstep_owns_the_tick_and_pins_the_divisor(tmp_path):
    """#65 composes by owning the clock: a frame the session's tick is not
    due for neither simulates nor draws (it re-sends), a due frame advances
    once and draws once, and the scheduler's divisor stays out of it."""
    ws = _ws(tmp_path)
    _open(ws, PACED_GAME)
    np = _FakeSession()
    ws.player._netplay = np
    calls = _count(ws)
    drawn0 = ws._frames_drawn
    _frames(ws, 5, 1 / 30)
    assert calls["upd"] == 0 and ws._frames_drawn == drawn0
    assert np.resent == 5
    np.on = True
    _frames(ws, 3, 1 / 30)
    assert calls["upd"] == 3 and np.advanced == 3
    assert ws._frames_drawn - drawn0 == 3
    assert set(calls["dts"]) == {np.dt}
    assert ws.player.sched.div == 1 and ws.player.sched.ticks == 0
    ws.player._netplay = None


# -- the loop and the wire ----------------------------------------------------

def test_frame_slot_ms_is_the_carts_tick_and_a_paced_game_never_sleeps():
    from runtime import device_boot

    class Paced:
        def __init__(self, tick_ms):
            self.player = type("P", (), {"tick_ms": tick_ms})()

    assert device_boot.frame_slot_ms(Paced(33), 16) == 33
    assert device_boot.frame_slot_ms(Paced(16), 16) == 16
    assert device_boot.frame_slot_ms(Paced(0), 16) == 16
    assert device_boot.frame_slot_ms(object(), 16) == 16
    pump = device_boot.FramePump(boot=None, fps_cap=60)
    assert pump.pace(Paced(33), 4) == 0 and pump.slot == 33
    assert pump.pace(Paced(33), 50) == 0 and pump.debt == 0
    assert pump.pace(Paced(0), 4) == 12


def test_the_state_snapshot_carries_the_tick_model(tmp_path):
    from runtime.dev_channel import _remote_state
    ws = _ws(tmp_path)
    assert _remote_state(ws)["tick"] is None
    _open(ws, "Hop Quest")
    assert _remote_state(ws)["tick"] == [60, 1, 0, True]


def test_skip_and_gov_decline_and_name_the_knob(tmp_path, capsys):
    from runtime.dev_channel import DevChannel
    from tests.test_dev_channel import FakePointer
    ws = _ws(tmp_path)
    ch = DevChannel(ws, FakePointer())
    capsys.readouterr()
    for word in ("skip 1", "gov 0"):
        ch.run(ws, word)
        out = capsys.readouterr().out
        assert "retired" in out and "steady" in out
    ch.run(ws, "steady 0")
    assert capsys.readouterr().out.strip() == "REMOTE steady off"
    assert ws.steady is False and "steady" not in ws.system


def test_the_perf_line_carries_the_tick_and_the_misses():
    from runtime.perf_line import format_perf, parse_perf
    line = format_perf({"tick": (60, 2), "miss": 3})
    assert " tick=60/2 miss=3 " in line
    assert " tick=- miss=- " in format_perf({})
    assert parse_perf(line)["tick"] == (60.0, 2.0)


def test_an_imported_p8_cart_is_paced_by_the_host_now(tmp_path):
    """The Lua start path arms the scheduler too. With the shim a passthrough
    (moy-spec tick-model), an unpaced Lua cart would tick once per LOOP frame
    -- a 60fps cart at half speed on a 30Hz host, and the repeat pins in
    tests/test_import_p8.py are what first showed it."""
    pytest.importorskip("runtime.lua_binding")
    from tools import import_p8
    from runtime import moy_carts
    ws = _ws(tmp_path)
    if getattr(ws, "lua_runtime", None) is None:
        pytest.skip("no host Lua binding")
    src = ("pico-8 cartridge // http://www.pico-8.com\nversion 42\n__lua__\n"
           "ticks = 0\nfunction _update60() ticks = ticks + 1 end\n"
           "function _draw() cls(0) end\n")
    (tmp_path / "probe.p8").write_text(src, encoding="utf-8")
    out = str(tmp_path / "carts" / "probe.moy")
    import_p8.import_p8(str(tmp_path / "probe.p8"), out)
    cart = moy_carts.load(out)
    ws.launcher.items.append(cart)
    ws.launcher.sel = len(ws.launcher.items) - 1
    ws.open()
    assert ws.player.cart_error is None, ws.player.cart_error
    assert ws.player.tick_ms == 16
    _frames(ws, 30, 1 / 30)
    assert ws.player._lua.get_global("ticks") == 60
    assert ws.player.sched.misses == 0


def test_the_rate_comes_from_the_manifest_and_junk_is_thirty(tmp_path):
    ws = _ws(tmp_path)
    _open(ws, PACED_GAME)
    pl = ws.player
    for fps, rate in ((60, 60), (30, 30), (0, 30), (None, 30), ("junk", 30),
                      (45, 30)):
        pl._arm_pacing({"fps": fps})
        assert pl.sched.rate == rate, fps
    pl._arm_pacing({})
    assert pl.sched.rate == 30


def test_uncapped_draws_every_frame_while_logic_keeps_its_rate():
    """The DIAG uncap (serial `uncap 1`): a 30-cart on a 120Hz loop still
    ticks 30 times a second, but every loop frame draws -- the draw path
    flat out, the game at its own speed -- and the divisor learns nothing."""
    s = TickScheduler()
    s.start(30)
    s.uncap_mode(True)
    draws = ticks = 0
    for _ in range(120):
        draws += 1 if s.plan(1 / 120) else 0
        ticks += s.n
    assert draws == 120
    assert ticks == 30
    assert s.div == 1
    # Off again: the ordinary schedule, drawing only on ticks.
    s.uncap_mode(False)
    draws = 0
    for _ in range(120):
        draws += 1 if s.plan(1 / 120) else 0
    assert draws == 30


def test_a_free_seed_game_runs_with_the_loop(tmp_path):
    """Star Catcher declares `"fps": "free"`: no tick, every loop frame ticks
    AND draws, and dt is the loop's own -- the whole point of the opt-out."""
    ws = _ws(tmp_path)
    _open(ws, "Star Catcher")
    assert ws.player.tick_ms == 0 and ws.player._free is True
    calls = _count(ws)
    drawn0 = ws._frames_drawn
    _frames(ws, 20, 1 / 45)
    assert calls["upd"] == 20 and calls["draw"] == 20
    assert ws._frames_drawn - drawn0 == 20
    assert set(calls["dts"]) == {1 / 45}
