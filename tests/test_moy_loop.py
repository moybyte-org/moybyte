"""The kernel's frame loop (native/moy_kernel/moy_loop.c), on CPython through
its ctypes binding and the trace tier (moy_loop_host.c): a fake clock whose
stages cost what a test says, scripted inputs, a byte queue for the dev
channel, and every stage the loop drives logged as a token.

What is pinned here is what the loop DECIDES -- the order, the upcall count,
the idle ladder, the pump's arithmetic, the stage meters, the OTA confirm's
two thresholds, the dev channel's reader and the PERF line's format. That the
same C runs on every tier is tests/test_semantic_traces.py's loop trace
(CPython, the desktop MicroPython in both object models, a second thread).
"""

import pytest

from runtime import moy_loop as L
from runtime.perf_line import parse_perf

INPUTS, POINTER, PRESENT, TAIL, FENCE = range(5)
CONSOLE, APP, DRIVER, SERVICE = range(4)
STAGES = ("inputs", "dev", "idle", "pointer", "present", "frame", "backlight",
          "pump_tail", "tail", "pace", "account")


class Console:
    """Three upcalls that note themselves; `frame` paints when told to."""

    def __init__(self, paint=True):
        self.paint = paint
        self.drawn = 0
        self.lines = []
        self.raise_in = None
        self.cost_us = 0
        self.service_bits = []
        self.keep = 0

    def handle_input(self):
        if self.raise_in == "hi":
            raise ValueError("boom")
        if self.raise_in == "ctrl-c":
            raise KeyboardInterrupt

    def handle_pointer(self):
        pass

    def frame(self, dt):
        L.trace_advance(self.cost_us)
        L.trace_note("frame:%d" % int(dt * 1000 + 0.5))
        if self.paint:
            self.drawn += 1
        return self.drawn

    def words(self, line):
        self.lines.append(line)
        if line == "bye":
            return True
        return False

    def service(self, bits):
        self.service_bits.append(bits)
        return self.keep


@pytest.fixture
def loop():
    L.trace_init(20, True, 1000)        # a 50 ms slot
    c = Console()
    L.register(c.handle_input, c.handle_pointer, c.frame, c.words, c.service)
    yield c
    L.unregister()


def frame(clock=None, active=False):
    if clock is not None:
        L.trace_clock(clock)
    L.trace_input(active, active)
    r = L.step()
    return r, L.trace_log()


# -- the order and the upcalls --------------------------------------------------

def test_a_frame_is_the_invariant_order(loop):
    r, log = frame(1000, active=True)
    assert r == L.OK
    toks = log.split()
    want = ["inputs", "pointer:click", "present", "frame:0", "fence", "light=255",
            "first_light", "tail:drew", "feed"]
    pos = [toks.index(t) for t in want]
    assert pos == sorted(pos), log


def test_every_frame_makes_exactly_three_console_upcalls_and_no_other(loop):
    for i in range(20):
        frame(1000 + 50 * i)
        assert L.upcalls()[0] == (3, 0, 0, 0, 0)
    assert L.upcalls()[1] == (60, 0, 0, 0, 0)


def test_a_service_is_called_up_only_while_it_is_live(loop):
    frame(1000)
    assert loop.service_bits == []
    L.services(L.SVC_WEB)
    loop.keep = L.SVC_WEB
    frame(1050)
    frame(1100)
    assert loop.service_bits == [L.SVC_WEB, L.SVC_WEB]
    assert L.upcalls()[0] == (3, 0, 0, 1, 0)
    loop.keep = 0                       # the webhost said goodbye: the bit goes
    frame(1150)
    frame(1200)
    assert loop.service_bits == [L.SVC_WEB] * 3
    assert L.upcalls()[0] == (3, 0, 0, 0, 0)


def test_a_dev_line_is_one_more_console_upcall_on_the_frame_it_lands(loop):
    L.trace_feed(b"state\n")
    frame(1000)
    assert loop.lines == ["state"]
    assert L.upcalls()[0] == (4, 0, 0, 0, 0)


def test_an_upcall_with_no_vm_is_refused_and_counted(loop):
    frame(1000)
    t0 = L.upcalls()[1]
    L.vm(False)
    try:
        assert L.word("state") == -3            # ABSENT
        assert loop.lines == []
    finally:
        L.vm(True)
    t1 = L.upcalls()[1]
    assert t1[4] == t0[4] + 1 and t1[:4] == t0[:4]


def test_quit_returns_before_the_frame_runs(loop):
    L.trace_feed(b"quit\n")
    r, log = frame(1000)
    assert r == L.QUIT
    assert "frame:" not in log
    L.trace_feed(b"bye\n")              # a console word may ask for it too
    r, log = frame(1050)
    assert r == L.QUIT


def test_a_frame_error_is_survived_and_a_ctrl_c_ends_the_loop(loop, capsys):
    loop.raise_in = "hi"
    r, log = frame(1000)
    assert r == L.OK
    assert "Moybyte frame error: boom" in capsys.readouterr().out
    loop.raise_in = "ctrl-c"
    r, log = frame(1050)
    assert r == L.INTERRUPT
    assert "frame:" not in log


def test_a_system_exit_in_a_word_ends_the_loop_for_the_vms_reset(loop):
    # `kstop N`: the console's word raises SystemExit, which a board's VM
    # service turns into its soft reset.
    def words(line):
        raise SystemExit
    L.register(loop.handle_input, loop.handle_pointer, loop.frame, words)
    L.trace_feed(b"kstop 3\n")
    r, log = frame(1000)
    assert r == L.EXIT
    assert "frame:" not in log


def test_a_loop_with_nothing_registered_runs_no_frame(loop):
    L.unregister()
    r, log = frame(1000)
    assert r == L.STOPPED
    assert log == ""


def test_with_no_vm_every_stage_runs_and_each_upcall_is_refused(loop):
    """A VM stop (docs/kernel_cartpath_2026-10.md section 4): the frame goes
    on with no VM -- the inputs, the dev channel, the tail -- and each of the
    console's three upcalls is refused and counted REFUSED, never made."""
    L.vm(False)
    try:
        r, log = frame(1000)
        assert r == L.OK
        assert "frame:" not in log
        assert log.split()[0] == "inputs"
        assert L.upcalls()[0] == (0, 0, 0, 0, 3)
    finally:
        L.vm(True)
        L.register(loop.handle_input, loop.handle_pointer, loop.frame,
                   loop.words, loop.service)
    r, log = frame(1050)
    assert r == L.OK and "frame:" in log


# -- first light ------------------------------------------------------------------

def test_the_panel_is_lit_once_by_the_first_drawn_frame_behind_a_fence():
    L.trace_init(20, True, 1000)
    c = Console(paint=False)
    L.register(c.handle_input, c.handle_pointer, c.frame)
    _, log = frame(1000)
    assert "light=" not in log          # nothing composed yet: power-on noise
    c.paint = True
    _, log = frame(1050)
    toks = log.split()
    assert toks.index("fence") < toks.index("light=255")
    _, log = frame(1100)
    assert "light=" not in log and "fence" not in log
    L.unregister()


def test_first_light_never_lights_a_panel_the_ladder_darkened():
    L.trace_init(20, True, 1000)
    c = Console(paint=False)
    L.register(c.handle_input, c.handle_pointer, c.frame)
    L.idle(L.BLANK, 1)
    frame(1000, active=True)
    frame(2100)                         # blank: the board renders while dark
    c.paint = True
    _, log = frame(2150)
    assert "light=" not in log
    L.unregister()


def test_a_splash_that_lit_the_panel_is_not_lit_again(loop):
    L.lit(True)
    _, log = frame(1000)
    assert "light=" not in log


# -- the idle ladder ------------------------------------------------------------------

def ladder(dim=0, saver=0, blank=0, can_dim=True):
    L.trace_init(20, can_dim, 1000)
    c = Console()
    L.register(c.handle_input, c.handle_pointer, c.frame)
    L.lit(True)
    L.idle(L.DIM, dim)
    L.idle(L.SAVER, saver)
    L.idle(L.BLANK, blank)
    return c


def walk(ms_from, ms_to, step=250, active_at=()):
    out = []
    t = ms_from
    while t <= ms_to:
        _, log = frame(t, active=t in active_at)
        out.append((t, L.idle()[0], log))
        t += step
    return out


def test_the_rungs_come_in_order_each_at_its_own_time():
    ladder(dim=1, saver=2, blank=3)
    seen = {}
    for t, state, log in walk(1000, 5000):
        seen.setdefault(state, t)
    assert seen == {0: 1000, 1: 2000, 2: 3000, 3: 4000}
    L.unregister()


def test_a_rung_set_to_off_is_skipped():
    ladder(dim=0, saver=2, blank=0)
    states = {s for _t, s, _l in walk(1000, 6000)}
    assert states == {0, 2}
    L.unregister()


def test_a_light_that_cannot_dim_has_no_dim_rung():
    ladder(dim=1, saver=0, blank=3, can_dim=False)
    states = [s for _t, s, _l in walk(1000, 5000)]
    assert 1 not in states and 3 in states
    L.unregister()


def test_the_light_follows_the_rungs():
    ladder(dim=1, saver=2, blank=3)
    lights = [tok for _t, _s, log in walk(1000, 5000) for tok in log.split()
              if tok.startswith("light=")]
    assert lights == ["light=48", "light=0"]     # the saver keeps the dim light
    L.unregister()


def test_the_touch_that_wakes_a_saver_or_a_dark_panel_is_swallowed():
    for rung in ("saver", "blank"):
        ladder(**{rung: 1})
        rows = walk(1000, 2500, active_at=(2500,))
        t, state, log = rows[-1]
        assert state == 0
        assert "pointer:swallow" in log and "repaint" in log, (rung, log)
        L.unregister()


def test_a_tap_on_a_dimmed_screen_is_a_tap():
    ladder(dim=1)
    rows = walk(1000, 2500, active_at=(2500,))
    _t, state, log = rows[-1]
    assert state == 0
    assert "pointer:click" in log and "repaint" not in log
    assert "light=255" in log
    L.unregister()


def test_power_off_outranks_the_activity_of_the_line_that_asked():
    ladder(blank=300)
    L.trace_feed(b"power off\n")
    _, log = frame(1000)
    assert L.idle()[0] == 3
    assert "light=0" in log
    L.trace_feed(b"power on\n")
    frame(1050)
    assert L.idle()[0] == 0
    L.unregister()


def test_power_secs_sets_the_blank_rung_and_off_is_zero(capsys):
    ladder()
    L.trace_feed(b"power 7\n")
    _, log = frame(1000)
    assert L.idle()[3] == 7
    assert "REMOTE_power_timeout=7s_asleep=False" in log
    L.trace_feed(b"power 0\n")
    frame(1050)
    assert L.idle()[3] == 0
    walk(1100, 20000, step=1000)
    assert L.idle()[0] == 0
    L.trace_feed(b"power saver 2\n")
    frame(21000)
    assert L.idle()[2] == 2
    L.unregister()


def test_entering_a_rung_says_so_once():
    ladder(blank=1)
    logs = " ".join(log for _t, _s, log in walk(1000, 4000))
    assert logs.count("Moybyte_power_save:_blank") == 1
    L.unregister()


# -- the pump ----------------------------------------------------------------------

def test_dt_is_clamped_so_a_hitch_cannot_teleport_a_cart(loop):
    frame(1000)
    _, log = frame(1600)                # a 600 ms hitch
    assert "frame:100" in log


def test_a_frame_inside_its_budget_sleeps_the_remainder(loop):
    assert L.pace(20) == 30


def test_a_paced_game_never_sleeps_and_its_tick_is_the_slot(loop):
    L.tick(100)
    assert L.pace(5) == 0
    assert L.pump()[1] == 100
    L.tick(0)
    assert L.pace(5) == 45
    assert L.pump()[1] == 50


def test_the_debt_is_capped_at_one_pair():
    L.trace_init(60, True, 1000)        # a 16 ms slot
    assert L.pace(500) == 0
    assert L.pump()[2] == 32            # two slots, not the whole hitch
    assert L.pace(10) == 0              # the debt eats the sleep
    assert L.pump()[2] == 26


def test_the_debt_is_inert_while_frames_fit(loop):
    for _ in range(10):
        assert L.pace(30) == 20
    assert L.pump()[2] == 0


def test_the_slack_converges_on_a_constant_sleep_overshoot(loop):
    # Every sleep overshoots by 4 ms: the walker learns it and pre-pays it.
    t = 1000
    for _ in range(30):
        _, log = frame(t)
        sleep = int([x for x in log.split() if x.startswith("sleep=")][0][6:])
        t += sleep + 4
    assert L.pump()[3] in (3, 4, 5)


# -- the stage meters ------------------------------------------------------------------

def test_the_stages_are_the_pinned_order(loop):
    assert L.stages() == STAGES


def test_the_budgets_spend_one_whole_slot_and_the_tail_has_none(loop):
    m = L.meters()
    budgets = [m[s][0] for s in STAGES]
    assert budgets[STAGES.index("tail")] is None
    # Four fifths of every slot is supposed to reach the glass.
    assert sum(b for b in budgets if b is not None) == 50000
    assert m["frame"][0] == 39000


def test_the_meters_sleep_until_the_diag_arms_them(loop):
    for i in range(5):
        frame(1000 + 50 * i)
    m = L.meters()
    assert all(m[s][5] == 0 and m[s][1] is None for s in STAGES)


def test_every_stage_is_metered_once_a_frame_under_the_diag(loop):
    L.capture(True)
    for i in range(5):
        frame(1000 + 50 * i)
    m = L.meters()
    assert all(m[s][5] == 5 for s in STAGES), m


def test_a_stage_over_budget_counts_a_miss_and_lifts_the_max(loop):
    L.capture(True)
    L.trace_cost(PRESENT, 4000)         # present's budget is 30/1000 of 50 ms
    frame(1000)
    m = L.meters()["present"]
    assert m[0] == 1500 and m[4] == 1 and m[3] == 4000


def test_a_stage_with_no_deadline_never_misses(loop):
    L.capture(True)
    L.trace_cost(TAIL, 99000)
    frame(1000)
    m = L.meters()["tail"]
    assert m[0] is None and m[4] is None and m[3] == 99000


def test_the_frame_that_resets_is_not_a_sample(loop):
    L.capture(True)
    frame(1000)
    L.meters_reset()
    m = L.meters()
    assert all(m[s][5] == 0 for s in STAGES)
    frame(1050)
    assert L.meters()["frame"][5] == 1


def test_the_budgets_follow_a_paced_carts_slot(loop):
    L.capture(True)
    L.tick(100)
    frame(1000)
    frame(1050)
    assert L.meters()["frame"][0] == 78000


# -- the OTA confirm's two thresholds ------------------------------------------------

HEALTHY_LOOPS = 120


def test_an_image_that_never_paints_is_never_confirmed():
    L.trace_init(20, True, 1000)
    c = Console(paint=False)
    L.register(c.handle_input, c.handle_pointer, c.frame)
    L.health(True)
    logs = " ".join(frame(1000 + i)[1] for i in range(HEALTHY_LOOPS * 3))
    assert "healthy" not in logs
    L.unregister()


def test_one_painted_frame_then_the_loops_confirm_once(loop):
    L.health(True)
    fired = []
    for i in range(HEALTHY_LOOPS + 50):
        _, log = frame(1000 + i)
        if "healthy" in log:
            fired.append(i)
    assert fired == [HEALTHY_LOOPS - 1]
    assert loop.service_bits == [L.SVC_HEALTHY]


def test_a_build_with_no_update_pending_never_confirms(loop):
    logs = " ".join(frame(1000 + i)[1] for i in range(HEALTHY_LOOPS * 2))
    assert "healthy" not in logs


# -- the dev channel's reader ----------------------------------------------------------

def test_a_line_that_is_not_utf8_costs_only_that_line(loop):
    L.trace_feed(b"st\xffate\nmoy?\n")
    frame(1000)
    assert loop.lines == ["moy?"]


def test_a_command_in_utf8_arrives_whole(loop):
    L.trace_feed("run Café\n".encode())
    frame(1000)
    assert loop.lines == ["run Café"]


def test_an_overlong_line_is_dropped_and_the_next_one_runs(loop):
    for k in range(3):
        L.trace_feed(b"x" * 2000)
        for i in range(5):
            frame(1000 + 10 * k + i)
    L.trace_feed(b"\nmoy?\n")
    frame(2000)
    assert loop.lines == ["moy?"]


def test_bytes_handed_back_are_read_before_the_stream(loop):
    L.devch_unread(b"moy?\n")
    frame(1000)
    assert loop.lines == ["moy?"]


def test_a_tap_is_a_press_then_a_release_a_frame_apart(loop):
    L.tap(10, 20)
    _, log0 = frame(1000)
    _, log1 = frame(1050)
    _, log2 = frame(1100)
    assert "pt=10,20,1,1" in log0       # pressed by the word itself
    assert "pt=10,20,0,0" not in log0
    assert "pt=10,20,0,0" in log1
    assert "pt=" not in log2


def test_a_swipe_presses_holds_and_releases_at_its_end(loop):
    L.swipe(0, 0, 100, 0, 5)
    pts = []
    for i in range(8):
        _, log = frame(1000 + 50 * i)
        pts += [t for t in log.split() if t.startswith("pt=")]
    assert pts[0] == "pt=0,0,1,1"
    assert pts[-1] == "pt=100,0,0,0"
    assert len(pts) == 6


def test_a_gesture_is_activity_for_the_ladder():
    ladder(blank=1)
    L.drag(50, 10, 20, 2)
    states = [s for _t, s, _l in walk(1000, 8000)]
    assert states[:20] == [0] * 20 and 3 in states


# -- the PERF line ---------------------------------------------------------------------

def test_the_perf_line_is_the_parsers_format():
    line = L.perf_format({"cart": "Brick Siege", "fps": (30, 61), "busy": 12,
                          "draw": 2.5, "flush": 7.4, "ppa": (1, None, 3, 4, 5),
                          "fence_ms": 1.25, "home": (1, 2, 3), "gc": (2, 1500, 900)})
    v = parse_perf(line)
    assert v["cart"] == "Brick_Siege"
    assert v["fps"] == (30.0, 61.0)
    assert v["busy"] == 12.0
    assert v["draw"] == 3.0 and v["flush"] == 7.0
    assert v["ppa"] == (1.0, None, 3.0, 4.0, 5.0)
    assert v["fence_ms"] == 1.3
    for absent in ("net", "tick", "miss", "logic", "render", "chrome", "wmr",
                   "wmw", "wms", "gfence_ms"):
        assert v[absent] is None, absent
    assert " " not in line.split("cart=")[1].split(" ")[0]


def test_nothing_is_printed_while_the_diag_is_off(loop):
    logs = " ".join(frame(1000 + 100 * i)[1] for i in range(50))
    assert "PERF" not in logs


def test_the_sample_carries_the_consoles_half_and_its_own(loop):
    L.capture(True)
    logs = []
    for i in range(30):
        L.trace_clock(1000 + 100 * i)
        if L.perf_due():
            L.perf_cart("Star Catcher")
            L.perf("draw", 4)
            L.perf("tick", (30, 1))
        logs.append(frame()[1])
    lines = [t for log in logs for t in log.split() if t.startswith("say[PERF")]
    assert lines, logs
    v = parse_perf(lines[0][4:-1].replace("_", " ").replace("cart=Star Catcher",
                                                               "cart=Star_Catcher")
                   .replace("fence ms", "fence_ms"))
    assert v["cart"] == "Star_Catcher"
    assert v["draw"] == 4.0 and v["tick"] == (30.0, 1.0)
    assert v["fps"][0] > 0


# -- HITCH and LOOP -----------------------------------------------------------------------

DIAG_STAGES = STAGES[:STAGES.index("tail") + 1]


def _fields(line):
    return dict(t.split("=", 1) for t in line.split()[1:])


def test_a_frame_past_the_hitch_threshold_names_every_stage(loop):
    """HITCH is the kernel's (moy_loop.c): every stage inside the frame's work,
    by the meters, in ms -- never -1 for a stage that moved into C."""
    L.capture(True)
    frame(1000)
    L.diag_take()
    loop.cost_us = 95000
    log = frame(1100)[1]
    hitch, _ = L.diag_take()
    assert hitch is not None and "say[HITCH" in log, log
    f = _fields(hitch)
    assert int(f["ms"]) >= 80
    assert list(f)[1:] == list(DIAG_STAGES)
    assert all(f[s] != "-" and float(f[s]) >= 0 for s in DIAG_STAGES)
    assert float(f["frame"]) >= 90.0
    loop.cost_us = 1000
    frame(1300)
    assert L.diag_take() == (None, None)


def test_no_hitch_or_loop_while_the_diag_is_off(loop):
    loop.cost_us = 95000
    logs = " ".join(frame(1000 + 200 * i)[1] for i in range(30))
    assert "HITCH" not in logs and "LOOP" not in logs
    assert L.diag_take() == (None, None)


def test_loop_is_the_periods_average_frame_by_stage(loop):
    L.capture(True)
    loop.cost_us = 10000
    logs = []
    for i in range(60):
        logs.append(frame(1000 + 60 * i)[1])
    _, line = L.diag_take()
    assert line is not None and any("say[LOOP" in g for g in logs), logs
    f = _fields(line)
    assert int(f["n"]) > 0
    for s in DIAG_STAGES:
        assert f[s] != "-", (s, line)
    assert list(f)[:2] == ["n", "ms"] and list(f)[2:-2] == list(DIAG_STAGES)
    assert 9.5 <= float(f["frame"]) <= 11.0, line
    assert float(f["sleep"]) >= 0 and float(f["other"]) >= 0
