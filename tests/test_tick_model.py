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

def _sched(rate=30, steady=True):
    s = TickScheduler()
    s.start(rate, steady)
    return s


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
    a 30fps host runs two ticks inside one frame and draws once."""
    s = _sched(60)
    ns = []
    for _ in range(30):
        s.plan(1 / 30)
        ns.append(s.n)
    assert ns == [2] * 30
    assert s.ticks == 60 and s.draws == 30 and s.misses == 0


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


def test_free_follows_load_on_every_draw_frame():
    s = _sched(30, steady=False)
    s.plan(1 / 30)                           # tick + draw
    assert s.draw and s.div == 1
    s.plan(0.045)                            # that draw frame cost 45ms: over 33
    assert s.div == 2
    # Now the frame between draws is cheap and the draw frame fits one period
    # with room: back to 1 as soon as one draw frame says so.
    s.plan(0.005)                            # logic-only frame's cost lands here
    while not s.draw:
        s.plan(0.005)
    s.plan(0.020)                            # the draw frame cost 20ms <= 0.85 * 33
    assert s.div == 1


def _window(s, cost, frames=None, secs=tick_model.STEADY_S):
    """Feed draw frames costing `cost` (and 3ms logic-only ones) for `secs`."""
    t = 0.0
    while t < secs + 0.01:
        dt = cost if s.draw else 0.003
        s.plan(dt)
        t += dt


def test_steady_holds_through_a_hitch_and_follows_a_scene():
    s = _sched(30)
    _window(s, 0.020)
    assert s.div == 1
    s.plan(0.200)                            # one GC-class hitch
    _window(s, 0.020)
    assert s.div == 1, "one hitch in a window is not a scene change"
    _window(s, 0.045)                        # a boss fight: every draw overruns
    assert s.div == 2
    _window(s, 0.045)
    assert s.div == 2, "and it holds there: 45ms fits two periods with a 3ms tick between"
    _window(s, 0.015)                        # back to the menu: the first
    _window(s, 0.015)                        # window still tails the fight
    assert s.div == 1


def test_steady_re_decides_once_a_window_not_every_frame():
    s = _sched(30)
    for _ in range(10):
        s.plan(1 / 30)
        s.plan(0.045)
    assert s.div == 1, "STEADY needs a whole window before it moves"


def test_the_divisor_never_leaves_its_bounds():
    s = _sched(30, steady=False)
    for _ in range(40):
        s.plan(0.500)                        # every frame draws, and overruns
    assert s.div == MAX_DIV
    for _ in range(400):
        s.plan(0.001)                        # every draw frame fits with room
    assert s.div == 1


def test_a_logic_bound_cart_reports_the_miss_and_keeps_its_rate():
    """moss moss: 92 of 107ms is logic on the S3 boards. No divisor saves it,
    so the scheduler runs as fast as it can, keeps the declared rate, and
    REPORTS -- it does not halve the logic to look steady."""
    s = _sched(30)
    s.note_tick(0.090)
    ns = []
    for _ in range(60):
        s.plan(0.100)
        ns.append(s.n)
    assert ns == [1] * 60, "one tick per frame: never zero, never a burst"
    assert s.misses > 0
    assert s.rate == 30 and s.div == 1


def test_switching_steady_live_resets_only_the_window():
    s = _sched(30)
    s.plan(1 / 30)
    s.plan(0.045)
    s.steady_mode(False)
    assert s.div == 1 and s.steady is False
    s.plan(0.003)
    while not s.draw:
        s.plan(0.003)
    s.plan(0.045)
    assert s.div == 2


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


def _ws(tmp_path):
    from runtime import host_app
    return host_app.build_workstation(str(tmp_path / "carts"))


def _frames(ws, n, dt):
    for _ in range(n):
        ws.input.begin_frame()
        ws.frame(dt)


def test_a_game_holds_its_rate_on_a_faster_loop(tmp_path):
    ws = _ws(tmp_path)
    _open(ws, "Star Catcher")
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
    _open(ws, "Star Catcher")
    _frames(ws, 240, 1 / 60)
    assert 29 <= ws._fps <= 31


def test_a_tool_ticks_with_the_loop_it_serves(tmp_path):
    ws = _ws(tmp_path)
    tool = next(c["title"] for c in ws.launcher.items
                if c.get("type") in ("tool", "app"))
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
    _open(ws, "Star Catcher")

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
    _open(ws, "Star Catcher")
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
    _open(ws, "Star Catcher")
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
    _open(ws, "Star Catcher")
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
    _open(ws, "Star Catcher")
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
    _open(ws, "Star Catcher")
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
    _open(ws, "Star Catcher")
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
    _open(ws, "Star Catcher")
    pl = ws.player
    for fps, rate in ((60, 60), (30, 30), (0, 30), (None, 30), ("junk", 30),
                      (45, 30)):
        pl._arm_pacing({"fps": fps})
        assert pl.sched.rate == rate, fps
    pl._arm_pacing({})
    assert pl.sched.rate == 30