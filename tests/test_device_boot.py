"""The device boot spine and frame pump, executed (#161 Phase 4/5).

WHY THIS FILE MATTERS MORE THAN THE USUAL GREPS. `moy_runtime.py` was the last
thing in this console written twice, and neither copy is reachable from CI: both
import `esp32`/`machine`, so every existing net over them is a string match on
source. That is why `_pace_debt` could ship into one board's loop and not the
other's for four days with nothing to say so.

`runtime/device_boot.py` deliberately imports no board module -- every hardware
object arrives as an argument -- which means the shared half of both boards'
boot is ORDINARY PYTHON and can simply be run. So this file runs it: the splash
lines, the seed-progress cadence, the cart fallback, the OTA verdict and
confirm, and the pacing arithmetic on an injected `elapsed`.

That mattered concretely when it was written. The T-Deck was not connected and
its USB-CDC RX is dead under the desktop anyway, so the S3 half of the
extraction shipped with no on-glass verification at all. These assertions ARE
that board's verification: they pin the exact serial strings and the exact order
that board printed before the change.
"""

import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
TDECK = ROOT / "firmware" / "lilygo_t_deck_plus_mainline" / "modules"
P4 = ROOT / "firmware" / "esp32_p4_wifi6_touch_lcd_7b" / "modules"

from runtime import device_boot  # noqa: E402


# -- fakes --------------------------------------------------------------------


class FakeCanvas:
    """Just enough surface for console.draw_splash, plus a paint counter."""

    w, h = 320, 240

    def __init__(self):
        self.syncs = 0
        self.paints = 0

    def sync_back(self):
        self.syncs += 1

    def cls(self, c=0):
        self.paints += 1

    def spr(self, *a, **k):
        pass

    def print(self, *a, **k):
        pass

    def rect(self, *a, **k):
        pass

    def rectb(self, *a, **k):
        pass


class FakeComp:
    def __init__(self):
        self.flushes = 0

    def flush(self):
        self.flushes += 1


class FakeWs:
    def __init__(self, frames=0, cap=60):
        self._frames_drawn = frames
        self._cap = cap
        self.armed = 0
        self.announced = 0

    def arm_splash(self):
        self.armed += 1

    def announce_update(self):
        self.announced += 1

    def frame_cap_fps(self):
        return self._cap


class FakeStore:
    CARTS_DIR = "/sd/moybyte/carts"

    def __init__(self, carts=None, raise_on=None):
        self.carts = carts if carts is not None else [{"title": "a"}]
        self.raise_on = raise_on
        self.calls = []

    def ensure_dirs(self, root):
        self.calls.append(("ensure_dirs", root))
        if self.raise_on == "ensure_dirs":
            raise OSError("no card")

    def seed_builtins(self, seed, root, progress=None):
        self.calls.append(("seed_builtins", root, len(seed)))
        if self.raise_on == "seed_builtins":
            raise OSError("write failed")
        if progress is not None:
            for i in range(len(seed)):
                progress(i, len(seed), seed[i].get("title"))

    def scan(self, root):
        self.calls.append(("scan", root))
        return list(self.carts)


def _boot(label="Moybyte", lights=None):
    canvas, comp = FakeCanvas(), FakeComp()
    b = device_boot.DeviceBoot(
        canvas, comp,
        (lights.append if lights is not None else None), label)
    return b, canvas, comp


# -- the splash ---------------------------------------------------------------


def test_a_stage_paints_a_frame_and_says_so_on_the_wire(capsys):
    lights = []
    boot, canvas, comp = _boot(lights=lights)
    boot.note("starting")

    assert capsys.readouterr().out == "Moybyte boot: starting\n"
    # sync_back is load-bearing, not hygiene: flush() rotates the back buffer
    # (three of them on the P4), so a splash that skips it repaints a buffer the
    # panel is not showing -- two frames in three stale, i.e. a strobe.
    assert (canvas.syncs, canvas.paints, comp.flushes) == (1, 1, 1)
    assert lights == [True] and boot.lit is True


def test_the_backlight_is_lit_once_and_only_by_the_first_composed_frame():
    lights = []
    boot, _, _ = _boot(lights=lights)
    boot.note("starting")
    boot.note("loading cartridges")
    boot.note("building the desktop")

    assert lights == [True], "the panel must be lit exactly once (#45)"


def test_a_progress_repaint_moves_the_bar_and_keeps_the_wire_quiet(capsys):
    boot, canvas, _ = _boot()
    boot.seed_progress(0, 32, "cart")
    boot.seed_progress(1, 32, "cart")

    out = capsys.readouterr().out
    # Every eighth cart reaches serial; a repaint says nothing to someone
    # watching the wire, and one line per cart would drown the boot log.
    assert out == "Moybyte boot: loading cartridges 1/32\n"
    assert canvas.paints == 2


def test_the_p4_label_is_the_only_thing_that_differs_in_the_wire_format(capsys):
    boot, _, _ = _boot(label="Moybyte P4")
    boot.note("starting")
    assert capsys.readouterr().out == "Moybyte P4 boot: starting\n"


def test_a_splash_that_cannot_draw_never_fails_the_boot(capsys):
    class Broken(FakeCanvas):
        def sync_back(self):
            raise RuntimeError("no framebuffer")

    boot = device_boot.DeviceBoot(Broken(), FakeComp(), None, "Moybyte")
    boot.note("starting")

    out = capsys.readouterr().out
    assert "Moybyte boot: starting" in out
    assert "Moybyte splash unavailable: no framebuffer" in out
    assert boot.lit is False    # ...which is what makes start_frames arm the logo


def test_the_desktop_takes_the_glass_and_the_splash_stops_painting(capsys):
    boot, canvas, _ = _boot()
    ws = FakeWs(frames=0)
    boot.start_frames(ws)
    before = canvas.paints

    assert boot.first_frame(ws) is False       # nothing painted yet
    ws._frames_drawn = 1
    assert boot.first_frame(ws) is True
    assert boot.first_frame(ws) is False       # once, not every frame

    boot.note("this must not reach the glass")
    assert canvas.paints == before
    assert "Moybyte first frame in " in capsys.readouterr().out


def test_the_boot_logo_is_armed_only_when_the_splash_never_came_up():
    boot, _, _ = _boot()
    ws = FakeWs()
    boot.note("starting")                      # the splash IS up
    boot.start_frames(ws)
    assert ws.armed == 0, ("arming it again replays the splash and delays the "
                           "launcher -- the picture is the same one")


def test_a_splash_that_never_composed_still_shows_the_logo():
    class Broken(FakeCanvas):
        def sync_back(self):
            raise RuntimeError("nope")

    boot = device_boot.DeviceBoot(Broken(), FakeComp(), None, "Moybyte")
    ws = FakeWs()
    boot.start_frames(ws)
    assert ws.armed == 1, "the one case where the logo would otherwise go unseen"


# -- the cart store -----------------------------------------------------------


def test_the_carts_load_through_the_boards_storage_session(capsys):
    boot, _, _ = _boot()
    store = FakeStore(carts=[{"title": "a"}, {"title": "b"}])
    opened = []

    def session(fn):
        # The T-Deck's SD shares the panel's SPI host, so the mount must bracket
        # the WHOLE seed+scan -- not each op.
        opened.append("in")
        try:
            return fn()
        finally:
            opened.append("out")

    carts, root = boot.load_carts(store, [{"title": "seed"}], session=session)

    assert (len(carts), root) == (2, store.CARTS_DIR)
    assert opened == ["in", "out"]
    assert [c[0] for c in store.calls] == ["ensure_dirs", "seed_builtins", "scan"]
    assert "Moybyte loaded 2 carts from SD" in capsys.readouterr().out


def test_the_p4_needs_no_session_and_says_flash(capsys):
    boot, _, _ = _boot(label="Moybyte P4")
    store = FakeStore()
    carts, root = boot.load_carts(store, [{"title": "s"}], root="/moy/carts",
                                  media="flash")

    assert root == "/moy/carts"
    assert store.calls[0] == ("ensure_dirs", "/moy/carts")
    assert "Moybyte P4 loaded 1 carts from flash" in capsys.readouterr().out


def test_an_unreadable_store_degrades_to_the_embedded_carts(capsys):
    boot, _, _ = _boot()
    store = FakeStore(raise_on="ensure_dirs")
    seed = [{"title": "built-in"}]

    carts, root = boot.load_carts(store, seed)

    assert root is None, ("a None root is what wire_workstation_core turns into "
                          "can_manage=False -- the console must not offer edits "
                          "it cannot save")
    assert carts == seed and carts[0] is not seed[0], "the seed must be copied"
    out = capsys.readouterr().out
    assert "Moybyte SD carts unavailable: no card" in out
    assert "Moybyte using built-in carts" in out


def test_an_empty_store_also_falls_back(capsys):
    boot, _, _ = _boot()
    carts, root = boot.load_carts(FakeStore(carts=[]), [{"title": "b"}])
    assert (len(carts), root) == (1, None)


def test_the_seed_progress_bar_is_wired_into_the_store_call():
    boot, canvas, _ = _boot()
    boot.load_carts(FakeStore(), [{"title": "a"}, {"title": "b"}])
    # One repaint per cart: free against ~550ms of flash writes each, and the
    # only stretch of a first boot that knows how much of itself is left.
    assert canvas.paints == 2


# -- the Lua runtime ----------------------------------------------------------


def test_a_build_without_moycore_says_absent_rather_than_failing(capsys):
    boot, _, _ = _boot()
    # No `moycore_glue` on the host: exactly the shape of a board built without
    # the native module, where a `runtime: lua` cart opens the Player's
    # runtime-missing panel instead of crashing.
    assert boot.lua_runtime(FakeWs()) is None
    assert "Moybyte lua runtime ABSENT" in capsys.readouterr().out


def test_the_lua_status_can_be_routed_to_a_boards_own_log():
    boot, _, _ = _boot()
    lines = []
    boot.lua_runtime(FakeWs(), log=lines.append)
    # The T-Deck sends it to the offline diag ring: that board's USB-CDC RX is
    # dead under the desktop, so a status that is not recorded cannot be asked
    # for afterwards.
    assert lines == ["lua runtime ABSENT"]


# -- the OTA verdict + confirm ------------------------------------------------


class FakeUpdater:
    def __init__(self, verdict=None, healthy_at=1):
        self.verdict = verdict
        self.healthy_at = healthy_at
        self.confirmed = False
        self.asked = []

    def boot_check(self):
        return self.verdict

    def confirm_when_healthy(self, frames):
        self.asked.append(frames)
        if frames >= self.healthy_at:
            self.confirmed = True
            return True
        return False

    def slot(self):
        return "ota_1"


def test_the_boot_verdict_reaches_both_the_log_and_the_desktop():
    ws = FakeWs()
    ws.updater = FakeUpdater(verdict=("rolled_back", "the update did not stick"))
    lines = []
    health = device_boot.OtaHealth(ws, log=lines.append)
    health.boot_check()

    assert lines == ["last update rolled_back (the update did not stick)"]
    assert ws.announced == 1, "a verdict nobody sees on the glass is not a verdict"


def test_no_verdict_says_nothing():
    ws = FakeWs()
    ws.updater = FakeUpdater(verdict=None)
    lines = []
    device_boot.OtaHealth(ws, log=lines.append).boot_check()
    assert lines == [] and ws.announced == 0


def test_a_board_with_no_updater_is_silent_and_harmless():
    ws = FakeWs()
    lines = []
    health = device_boot.OtaHealth(ws, log=lines.append)
    health.boot_check()
    health.tick()
    assert lines == []


def test_the_confirm_waits_for_painted_frames_then_disarms():
    ws = FakeWs(frames=0)
    up = FakeUpdater(healthy_at=1)
    ws.updater = up
    lines = []
    health = device_boot.OtaHealth(ws, log=lines.append)

    health.tick()
    assert up.asked == [0] and lines == []
    ws._frames_drawn = 1
    health.tick()
    assert lines == ["marked app valid (slot ota_1)"]
    health.tick()
    health.tick()
    assert up.asked == [0, 1], "confirmed once -- the loop stops asking (#53)"


def test_a_throwing_updater_never_breaks_a_frame():
    class Angry(FakeUpdater):
        def confirm_when_healthy(self, frames):
            raise RuntimeError("partition gone")

    ws = FakeWs(frames=5)
    ws.updater = Angry()
    lines = []
    health = device_boot.OtaHealth(ws, log=lines.append)
    health.tick()
    health.tick()
    assert lines == ["confirm failed: partition gone"], "and it stops asking"


def test_a_throwing_boot_check_never_blocks_the_desktop():
    class Angry(FakeUpdater):
        def boot_check(self):
            raise OSError("marker unreadable")

    ws = FakeWs()
    ws.updater = Angry()
    lines = []
    device_boot.OtaHealth(ws, log=lines.append).boot_check()
    assert lines == ["boot_check failed: marker unreadable"]


# -- the frame pump -----------------------------------------------------------


def _pump(cap=60, ota=None):
    boot, _, _ = _boot()
    return device_boot.FramePump(boot, ota, cap)


def test_dt_is_seconds_and_clamped_so_a_hitch_cannot_teleport_a_cart():
    pump = _pump()
    pump.last = device_boot._ticks_ms() - 5000     # a 5s stall
    _, dt = pump.begin()
    assert dt == 0.1, "clamped to 100ms: physics must not jump a whole second"


def test_a_frame_inside_its_budget_sleeps_the_remainder():
    pump = _pump()
    assert pump.pace(FakeWs(cap=60), 5) == 11      # 16ms slot
    assert pump.debt == 0


def test_a_running_game_paces_to_its_own_cadence_never_faster_than_the_cap():
    pump = _pump(cap=60)
    # #63: a GAME locks to 30 (the SNES rule -- a locked 30 beats a 38-55 swing)
    assert pump.pace(FakeWs(cap=30), 10) == 23
    # ...and a cart that asks for MORE than the loop cap still gets the cap.
    assert pump.pace(FakeWs(cap=120), 2) == 14


def test_pacing_never_kills_the_loop_when_the_console_throws():
    class Broken(FakeWs):
        def frame_cap_fps(self):
            raise ValueError("no wm")

    assert _pump(cap=60).pace(Broken(), 4) == 12    # falls back to the loop cap


def test_the_frameskip_pair_lands_on_cadence_instead_of_overshooting():
    """#77's celeste measurement, as arithmetic.

    A per-frame clamp can only slow FAST frames. With frameskip on and a full
    frame at 50ms against a 33ms budget, the padded skip frame made the PAIR
    83ms -- the game 20% slow at 12fps, worse on both axes than no skip at all.
    The debt makes the pair 50 + 16 = 66ms, i.e. two 30fps slots: true 30Hz
    logic, an even 15fps of render.
    """
    pump = _pump(cap=60)
    ws = FakeWs(cap=30)                    # frameskip implies the 30 cap

    assert pump.pace(ws, 50) == 0          # the full frame overruns
    assert pump.debt == 50 - 33
    assert pump.pace(ws, 16) == 0          # the skip frame pays the debt down
    assert pump.debt == 0
    # 50 + 16 = 66ms for the pair == two 33ms slots, which is the whole claim.


def test_the_debt_is_capped_at_one_pair_so_a_real_hitch_is_not_repaid_forever():
    pump = _pump(cap=60)
    ws = FakeWs(cap=30)
    pump.pace(ws, 300)                     # a 200ms+ GC collect
    assert pump.debt == 2 * 33, "unpayable: just run flat out"
    # Repaid within a couple of frames rather than eating a second of sleeps.
    assert pump.pace(ws, 0) == 0
    assert pump.pace(ws, 0) == 0
    assert pump.pace(ws, 0) == 33 - 0


def test_the_debt_is_inert_while_frames_fit_their_budget():
    """Which is what makes it safe to hand to a board nobody has run it on.

    The P4 gained the debt with this extraction (it shipped into the T-Deck's
    loop alone). On a console screen holding its cap, the arithmetic is
    identical to the plain clamp it replaced.
    """
    pump = _pump(cap=60)
    ws = FakeWs(cap=60)
    for elapsed in (0, 3, 8, 15, 16):
        expect = 16 - elapsed if elapsed < 16 else 0
        assert pump.pace(ws, elapsed) == expect
        assert pump.debt == 0


def test_the_tail_reports_the_first_frame_and_confirms_the_update():
    boot, _, _ = _boot()
    ws = FakeWs(frames=1)
    ws.updater = FakeUpdater(healthy_at=1)
    health = device_boot.OtaHealth(ws, log=lambda m: None)
    pump = device_boot.FramePump(boot, health, 60)
    boot.start_frames(ws)

    pump.tail(ws)
    assert boot.done is True and ws.updater.confirmed is True


def test_the_tail_is_harmless_on_a_build_with_no_ota():
    boot, _, _ = _boot()
    pump = device_boot.FramePump(boot, None, 60)
    boot.start_frames(FakeWs())
    pump.tail(FakeWs(frames=1))            # must not raise


# -- both boards drive the same spine ----------------------------------------


def _run_desktop(path):
    src = path.read_text(encoding="utf-8")
    for node in ast.walk(ast.parse(src, filename=str(path))):
        if isinstance(node, ast.FunctionDef) and node.name == "run_desktop":
            return node
    raise AssertionError("%s has no run_desktop()" % path)


def _calls_on(fn, receiver):
    """Ordered `<receiver>.<method>(...)` calls, with whether each is in a loop."""
    out = []

    def walk(node, in_loop):
        for child in ast.iter_child_nodes(node):
            loop = in_loop or isinstance(node, (ast.While, ast.For))
            if (isinstance(child, ast.Call)
                    and isinstance(child.func, ast.Attribute)
                    and isinstance(child.func.value, ast.Name)
                    and child.func.value.id == receiver):
                out.append((child.func.attr, loop))
            walk(child, loop)

    walk(fn, False)
    return out


BOARDS = {"tdeck": TDECK / "moy_runtime.py", "p4": P4 / "moy_runtime.py"}


@pytest.mark.parametrize("board", sorted(BOARDS))
def test_each_board_imports_the_shared_spine(board):
    src = BOARDS[board].read_text(encoding="utf-8")
    line = [l for l in src.splitlines()
            if l.startswith("from device_boot import")]
    assert line, "%s does not import the shared spine" % board
    for name in ("DeviceBoot", "FramePump", "FrameLoop"):
        assert name in line[0], "%s does not import %s" % (board, name)
    # The staged name is flat (`device_boot`), never the host package path --
    # there is no `runtime` package on a board.
    assert "from runtime.device_boot" not in src


def test_both_boards_run_the_boot_steps_in_ONE_order():
    """The invariant Phase 4 buys, stated as a test.

    Two boards, one console: the difference between their boots should be
    HARDWARE (an esp_lcd strip flush vs a DPI scan-out, a trackball vs BLE HID),
    never the order in which the shared steps happen or whether one of them
    happens at all. When this fails, read the diff before changing the test --
    either a board grew a real reason to reorder, or a step went missing from
    one of them, which is the failure this whole phase exists to make loud.
    """
    seqs = {name: [m for m, _ in _calls_on(_run_desktop(path), "boot")]
            for name, path in BOARDS.items()}
    assert seqs["tdeck"] == seqs["p4"], seqs
    assert seqs["tdeck"] == ["note", "note", "load_carts", "note",
                             "lua_runtime", "start_frames"], seqs["tdeck"]


def test_both_boards_pump_the_frame_the_same_way():
    """Since #202 Phase B the pump is driven by the SHARED FrameLoop
    (device_boot), whose begin -> tail -> pace order the FrameLoop tests below
    pin directly -- so the per-board claim inverts: a board's run_desktop must
    CONSTRUCT the loop and must not drive the pump itself (a board that calls
    pump.begin beside the loop is running two cadences)."""
    for name, path in BOARDS.items():
        rd = _run_desktop(path)
        calls = _calls_on(rd, "pump")
        assert calls == [], (
            "%s: run_desktop drives the pump beside the shared loop -- %s"
            % (name, calls))
        src_txt = BOARDS[name].read_text(encoding="utf-8")
        assert "loop = FrameLoop(" in src_txt, (
            "%s never constructs the shared frame loop" % name)
        assert "loop.run()" in src_txt


def test_the_spine_imports_no_board_module():
    """What lets this file live in `runtime/` and be tested at all.

    Every hardware object arrives as an argument. If a board import creeps in,
    `runtime/device_boot.py` stops importing on the host (killing every
    assertion above) and starts being un-stageable to the wasm head, whose
    `DENY` glob does not exclude it.
    """
    src = (ROOT / "runtime" / "device_boot.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    allowed = {"console", "runtime", "chrome", "ticks", "moycore_glue",
               "perf_line", "time", "gc"}
    seen = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            seen |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            seen.add(node.module.split(".")[0])
    assert seen <= allowed, "device_boot imports board modules: %s" % sorted(
        seen - allowed)


# -- FrameLoop: the invariant order, pinned (#202 Phase B) --------------------


class _Rec:
    """A recording stub-kit for one FrameLoop frame."""

    def __init__(self, frames_drawn_after=1, serial=None, idle=None,
                 frame_raises=None):
        self.calls = []
        self.frames_drawn = 0
        self._after = frames_drawn_after
        self._raises = frame_raises
        rec = self

        class Comp:
            def sync(self):
                pass

        class Pump:
            def begin(self):
                rec.calls.append("begin")
                return 1000, 0.016

            def tail(self, ws):
                rec.calls.append("pump.tail")

            def pace(self, ws, elapsed):
                rec.calls.append("pace")
                return 0

        class WS:
            comp = Comp()

            @property
            def _frames_drawn(self):
                return rec.frames_drawn

            def handle_input(self):
                rec.calls.append("handle_input")

            def handle_pointer(self):
                rec.calls.append("handle_pointer")

            def frame(self, dt):
                rec.calls.append("frame")
                if rec._raises is not None:
                    raise rec._raises
                rec.frames_drawn = rec._after

        class Pointer:
            click = False

            def tick(self, now):
                rec.calls.append("pointer.tick")

        self.pump = Pump()
        self.ws = WS()
        self.pointer = Pointer()
        self.serial = serial
        self.idle = idle

    def poll_inputs(self, now):
        self.calls.append("poll_inputs")
        return False, False

    def present(self):
        self.calls.append("present")

    def tail(self, now):
        self.calls.append("tail")

    def account(self, now, elapsed, sleep_ms):
        self.calls.append("account")

    def loop(self, **kw):
        from runtime.device_boot import FrameLoop
        return FrameLoop(self.ws, self.pump, self.pointer, self.poll_inputs,
                         idle=self.idle, serial=self.serial,
                         present=self.present, tail=self.tail,
                         account=self.account, **kw)


def test_frameloop_order_is_the_invariant():
    """THE pin this class exists for: the order that lives in one shared file
    instead of N per-board copies. #56 was an order bug; so was PURR's F13;
    so is the idle-after-every-input rule and present-before-frame. A board
    cannot re-discover any of them on glass if the order cannot vary."""
    r = _Rec()
    r.loop().step()
    assert r.calls == ["begin", "poll_inputs", "pointer.tick", "present",
                       "handle_input", "handle_pointer", "frame",
                       "pump.tail", "tail", "pace", "account"]


def test_frameloop_serial_and_idle_slot_between_inputs_and_pointer():
    order = []

    class Serial:
        click = False
        quit = False

        def poll(self, ws):
            order.append("serial")
            return True                      # a dev command ran

    class Idle:
        asleep = False

        def tick(self, now, active, ws, pointer, click):
            order.append("idle")
            # A dev command counts as activity even with every other input
            # quiet -- the unattended-harness rule.
            assert active is True
            return click

    r = _Rec(serial=Serial(), idle=Idle())
    r.loop().step()
    i = r.calls.index
    assert (i("poll_inputs") < r.calls.index("pointer.tick")
            and order == ["serial", "idle"])
    # ...and both ran after inputs, before the pointer reaches the console.
    full = ["poll_inputs", "serial", "idle", "pointer.tick"]
    merged = [c for c in ["poll_inputs"] + order + ["pointer.tick"]]
    assert merged == full


def test_frameloop_quit_returns_before_the_frame_runs():
    class Serial:
        click = False
        quit = True

        def poll(self, ws):
            return True

    r = _Rec(serial=Serial())
    assert r.loop().step() == "quit"
    assert "frame" not in r.calls and "handle_input" not in r.calls


def test_frameloop_frame_errors_are_contained_and_ctrl_c_is_not():
    import pytest

    errs = []
    r = _Rec(frame_raises=ValueError("boom"))
    lp = r.loop(frame_error=errs.append)
    lp.step()
    assert len(errs) == 1 and "pump.tail" in r.calls   # the frame survived
    r2 = _Rec(frame_raises=KeyboardInterrupt())
    with pytest.raises(KeyboardInterrupt):
        r2.loop().step()                    # Ctrl-C -> shell -> REPL, always


def test_frameloop_backlight_gate_fires_once_after_the_first_drawn_frame():
    lit = []
    r = _Rec(frames_drawn_after=0)          # first frame draws nothing
    lp = r.loop(set_backlight=lit.append, lit=False)
    lp.step()
    assert lit == [] and lp.drew is False   # nothing composed -> stay dark
    r._after = 1                            # now a frame reaches the glass
    lp.step()
    assert lit == [True] and lp.drew is True
    lp.step()
    assert lit == [True], "the gate is a one-shot boot hand-over"


def test_frameloop_backlight_gate_fences_before_it_lights():
    """#45, the same fence `DeviceBoot.note`'s gate takes. On the two banded
    boards flush() returns with most of the frame still on the wire, so a light
    without a drain shows power-on GRAM noise. This gate is the one that runs
    when the splash never lit the panel."""
    order = []
    r = _Rec(frames_drawn_after=0)
    r.ws.comp.sync = lambda: order.append("sync")
    lp = r.loop(set_backlight=lambda on: order.append("light"), lit=False)
    lp.step()
    assert order == [], "nothing composed yet -- no fence, no light"
    r._after = 1
    lp.step()
    assert order == ["sync", "light"]
    lp.step()
    assert order == ["sync", "light"], "one-shot: no fence on later frames"


def test_frameloop_gate_survives_a_backend_with_no_sync():
    lit = []
    r = _Rec(frames_drawn_after=1)
    del type(r.ws).comp          # a compositor-less ws (the web tier's shape)
    lp = r.loop(set_backlight=lit.append, lit=False)
    lp.step()
    assert lit == [True]


def test_frameloop_gate_respects_a_deliberate_blank():
    class Idle:
        asleep = True

        def tick(self, now, active, ws, pointer, click):
            return click

    lit = []
    r = _Rec(idle=Idle())
    lp = r.loop(set_backlight=lit.append, lit=False)
    lp.step()
    assert lit == [], "a blanked panel must not be re-lit by the boot gate"


# -- FramePump slack: sleep-overshoot feedback (#202, 2026-08-17) -------------


def test_pump_slack_converges_on_a_constant_sleep_overshoot(monkeypatch):
    """The regression this pins, measured on the P4: FREERTOS_HZ=100 makes
    every paced sleep overshoot ~4ms, and a MEMORYLESS pace() paid that every
    frame -- a roster that ran 74fps uncapped paced itself to 48. The slack
    walker learns the overshoot from begin()'s real periods and pre-pays it,
    so the cadence converges back to the cap; on an exact-sleep platform it
    stays 0 and nothing changes."""
    from runtime import device_boot

    clock = [0]
    monkeypatch.setattr(device_boot, "_ticks_ms", lambda: clock[0])
    monkeypatch.setattr(device_boot, "_ticks_diff", lambda a, b: a - b)

    class WS:
        def frame_cap_fps(self):
            return 60

    pump = device_boot.FramePump(boot=None, ota=None, fps_cap=60)
    pump.last = clock[0]
    ws = WS()

    BUSY, OVER = 10, 4
    periods = []
    for _ in range(30):
        t0 = clock[0]
        pump.begin()
        clock[0] += BUSY                       # the frame's work
        sleep = pump.pace(ws, BUSY)
        if sleep:
            clock[0] += sleep + OVER           # the platform oversleeps
        periods.append(clock[0] - t0)
    # Converged: the last frames sit on the 16ms slot (+-1ms of walker
    # dither), not the 20ms the overshoot dictated un-compensated.
    tail = periods[-10:]
    assert max(tail) <= 17 and min(tail) >= 15, tail
    assert 3 <= pump.slack <= 5, pump.slack


def test_pump_slack_stays_zero_on_exact_sleeps(monkeypatch):
    from runtime import device_boot

    clock = [0]
    monkeypatch.setattr(device_boot, "_ticks_ms", lambda: clock[0])
    monkeypatch.setattr(device_boot, "_ticks_diff", lambda a, b: a - b)

    class WS:
        def frame_cap_fps(self):
            return 60

    pump = device_boot.FramePump(boot=None, ota=None, fps_cap=60)
    pump.last = clock[0]
    ws = WS()
    for _ in range(20):
        pump.begin()
        clock[0] += 10
        sleep = pump.pace(ws, 10)
        clock[0] += sleep                      # exact platform
    assert pump.slack == 0
    assert pump.debt == 0


def test_pump_slack_never_charges_a_hitch(monkeypatch):
    """A 200ms GC on a slept frame must not slam the walker -- the per-frame
    step is +-1 and the cap is 8, so a hitch is one tick of slack and stays
    debt's business."""
    from runtime import device_boot

    clock = [0]
    monkeypatch.setattr(device_boot, "_ticks_ms", lambda: clock[0])
    monkeypatch.setattr(device_boot, "_ticks_diff", lambda a, b: a - b)

    class WS:
        def frame_cap_fps(self):
            return 60

    pump = device_boot.FramePump(boot=None, ota=None, fps_cap=60)
    pump.last = clock[0]
    ws = WS()
    pump.begin()
    clock[0] += 10
    sleep = pump.pace(ws, 10)
    clock[0] += sleep + 200                    # a GC lands in the sleep
    pump.begin()
    assert pump.slack <= 1


def test_pump_slack_recovers_after_swallowing_the_whole_sleep(monkeypatch):
    """The stuck case, measured on glass: once slack >= the whole sleep, a
    sleep-gated learner froze at its ceiling and the loop ran PAST the cap
    (73fps under 60). A fully-cut sleep stays learnable, so the walker steps
    back down and the cadence re-converges on the slot."""
    from runtime import device_boot

    clock = [0]
    monkeypatch.setattr(device_boot, "_ticks_ms", lambda: clock[0])
    monkeypatch.setattr(device_boot, "_ticks_diff", lambda a, b: a - b)

    class WS:
        def frame_cap_fps(self):
            return 60

    pump = device_boot.FramePump(boot=None, ota=None, fps_cap=60)
    pump.last = clock[0]
    pump.slack = 8                             # walker at its ceiling
    ws = WS()
    BUSY, OVER = 12, 2                         # true overshoot far below slack
    periods = []
    for _ in range(30):
        t0 = clock[0]
        pump.begin()
        clock[0] += BUSY
        sleep = pump.pace(ws, BUSY)
        if sleep:
            clock[0] += sleep + OVER
        periods.append(clock[0] - t0)
    tail = periods[-10:]
    assert max(tail) <= 17 and min(tail) >= 15, (tail, pump.slack)



# -- the PERF line (#206 item 2) -------------------------------------------------
#
# ONE FORMAT, ONE BODY, THREE BOARDS (owner call 2026-08-28). It was three
# formats under one name: the P4's, the Guition's hand copy of it (which said so
# in its own comment), and the T-Deck's, which went through the offline diag
# ring in a fourth field order and carried that ring's `Moybyte <uptime> ` stamp
# -- so both readers, filtering on `startswith("PERF ")`, threw every T-Deck
# sample away. The board whose fps most needed measuring was invisible to the
# tool that measures it.
#
# WIRE_* below are real serial captures from the boards' PRE-CHANGE firmware
# (2026-08-28, all three attached). They are what the values were; the expected
# lines are those same values in the one format.

from runtime.perf_line import ABSENT, FIELDS, format_perf, parse_perf  # noqa: E402

# name -> (board dir, whether it hands the sampler an overlap source)
PERF_BOARDS = {
    "tdeck": (TDECK, False),
    "p4": (P4, True),
    "guition": (ROOT / "firmware" / "guition_jc3248w535" / "modules", False),
}

# The console meters + loop accumulators one sample was taken from, per board,
# and the line the unified formatter must produce from them.
#
#   p4       captured idle with `diag on`: every column it can measure, most of
#            them legitimately zero, and fps=0 because an idle desk paints
#            nothing (the invariant this line is also the witness for).
#   guition  captured idle: no windowed WM and no PPA on this board, so those
#            columns are ABSENT rather than zero -- `wmr=0 ppa=0/0/0/0/0` is
#            indistinguishable from the P4's meters having died, which is the
#            `fold=0` bug one format over.
#   tdeck    the values behind `Moybyte 2698583 PERF cart=Sakura_Lua fps=53
#            net=- flush=0 draw=14`, plus the columns its old five-field line
#            never carried. The slug and the `-` come straight from it.
#   p4_dark  the P4 with the deep meters OFF, which is how tools/p4_perf.py
#            measures: nothing writes _pf_wm_*, so those read `-` -- "not
#            measured" and "measured zero" are different answers.
PERF_CASES = {
    "p4": (
        {"cart": None, "fps": (0, 62), "net": None, "busy": 2,
         "draw": 33.0, "flush": 1.0, "logic": 0.0, "render": 0.0,
         "chrome": 33.0, "wmr": 28, "wmw": 1, "wms": 0,
         "ppa": (0, 0, 0, 0, 0), "fence_ms": 0.0, "gfence_ms": 0.0,
         "home": None},
        "PERF cart=- fps=0/62 net=- busy=2ms draw=33 flush=1 logic=0 render=0 "
        "chrome=33 wmr=28 wmw=1 wms=0 ppa=0/0/0/0/0 fence_ms=0.0 "
        "gfence_ms=0.0 home=-"),
    "guition": (
        {"cart": None, "fps": (0, 61), "net": None, "busy": 4,
         "draw": 72.0, "flush": 0.0, "logic": 0.0, "render": 0.0,
         "chrome": 72.0, "home": None},
        "PERF cart=- fps=0/61 net=- busy=4ms draw=72 flush=0 logic=0 render=0 "
        "chrome=72 wmr=- wmw=- wms=- ppa=- fence_ms=- gfence_ms=- home=-"),
    "tdeck": (
        {"cart": "Sakura Lua", "fps": (53, 55), "net": None, "busy": 18,
         "draw": 14.0, "flush": 0.0, "logic": 3.0, "render": 9.0,
         "chrome": 2.0, "home": None},
        "PERF cart=Sakura_Lua fps=53/55 net=- busy=18ms draw=14 flush=0 "
        "logic=3 render=9 chrome=2 wmr=- wmw=- wms=- ppa=- fence_ms=- "
        "gfence_ms=- home=-"),
    "p4_dark": (
        {"cart": None, "fps": (0, 62), "net": None, "busy": 2,
         "draw": 33.0, "flush": 1.0, "logic": 0.0, "render": 0.0,
         "chrome": 33.0, "ppa": (0, 0, 0, 0, 0), "fence_ms": 0.0,
         "gfence_ms": 0.0, "home": None},
        "PERF cart=- fps=0/62 net=- busy=2ms draw=33 flush=1 logic=0 render=0 "
        "chrome=33 wmr=- wmw=- wms=- ppa=0/0/0/0/0 fence_ms=0.0 "
        "gfence_ms=0.0 home=-"),
}

# Every column populated and every value DISTINCT: the captures above are idle
# and mostly zeros, which cannot tell a swapped pair of fields from a correct
# one, nor `%.0f` from `%d`. Each rounding here is one a plain int cast gets
# wrong, and the cart title carries a space the tokeniser must not see.
PERF_LOUD = (
    {"cart": "Brick Siege", "fps": (20, 31), "net": 30, "busy": 8,
     "draw": 3.6, "flush": 1.4, "logic": 5.4, "render": 12.7, "chrome": 2.2,
     "wmr": 7, "wmw": 8, "wms": 9, "ppa": (1, 2, 3, 4, 0),
     "fence_ms": 2.5, "gfence_ms": 0.7, "home": (3, 4, 1)},
    "PERF cart=Brick_Siege fps=20/31 net=30 busy=8ms draw=4 flush=1 logic=5 "
    "render=13 chrome=2 wmr=7 wmw=8 wms=9 ppa=1/2/3/4/0 fence_ms=2.5 "
    "gfence_ms=0.7 home=3/4/1")


@pytest.mark.parametrize("case", sorted(PERF_CASES))
def test_the_PERF_line_is_one_format_on_every_board(case):
    values, expected = PERF_CASES[case]
    assert format_perf(values) == expected


def test_the_PERF_line_keeps_its_field_order_and_its_conversions():
    values, expected = PERF_LOUD
    assert format_perf(values) == expected


def test_a_field_a_board_cannot_measure_prints_a_dash_and_never_a_zero():
    """THE DOCTRINE (2026-08-22), which `fold=0` cost weeks by breaking: a
    frozen 0 is also exactly what a broken lever looks like, so absence has to
    look different. There is deliberately no way to say "absent" with a number
    -- a missing key and an explicit None both render `-`, and every present
    value renders through its declared spec."""
    absent = format_perf({})
    assert absent == "PERF " + " ".join(n + "=" + ABSENT for n, _s, _u in FIELDS)
    zero = format_perf({"wmr": 0, "ppa": (0, 0, 0, 0, 0), "fence_ms": 0.0,
                        "net": 0})
    assert "wmr=0 " in zero and "ppa=0/0/0/0/0 " in zero
    assert "fence_ms=0.0 " in zero and "net=0 " in zero
    assert zero != absent


def test_the_cart_title_is_one_token_because_the_readers_split_on_whitespace():
    """`cart=Brick Siege` would arrive as a field `cart=Brick` and a stray word
    -- and every field AFTER it would still parse, so the corruption would be
    silent. The T-Deck's diag has slugged titles for this reason since it had
    any; the rule is the formatter's now, not each caller's."""
    assert "cart=Brick_Siege" in format_perf({"cart": "Brick Siege"})
    assert "cart=a_b" in format_perf({"cart": "a\nb"})
    assert "cart=?" in format_perf({"cart": ""})
    for name, value in (("loud", PERF_LOUD[1]),) + tuple(
            (k, v[1]) for k, v in PERF_CASES.items()):
        for tok in value.split()[1:]:
            assert "=" in tok, (name, tok)


@pytest.mark.parametrize("case", sorted(PERF_CASES))
def test_the_reader_and_the_writer_are_the_same_module(case):
    """`tools/p4_perf.py` parses with what emits, so the two halves of the
    contract cannot drift. An absent field comes back None -- never 0."""
    values, line = PERF_CASES[case]
    got = parse_perf(line)
    for name, _spec, _unit in FIELDS:
        want = values.get(name)
        if want is None:
            assert got[name] is None, name
        elif name == "cart":
            assert got[name] == want.replace(" ", "_")
        elif isinstance(want, tuple):
            assert got[name] == tuple(float(x) for x in want), name
        else:
            assert got[name] == float(want), name


def test_the_reader_strips_the_diag_rings_uptime_stamp():
    """THE BUG (#206 item 2). The T-Deck rings every sample for its offline SD
    log and replays the ring to serial at the next boot, so `Moybyte <ms> PERF
    ...` is a legitimate line -- and both readers filtered on
    `startswith("PERF ")`, which is why that board was invisible to the tool
    that produces #66's numbers."""
    line = PERF_CASES["tdeck"][1]
    assert parse_perf("Moybyte 2698583 " + line) == parse_perf(line)
    assert parse_perf("PERF") is None
    assert parse_perf("Moybyte 123 LOOP skip=0 n=188") is None
    assert parse_perf("Moybyte BLE keyboard: scanning") is None


# -- the sampler: one measurement path feeding that format ----------------------


class PerfWs:
    """The Workstation surface the sampler reads, and nothing else. Absent
    attributes are how a board says it has no lever, so this sets only what the
    case names."""

    def __init__(self, net=None, cart=None, meters=(), diag_live=False):
        self._frames_drawn = 0
        self.diag_live = diag_live
        self.perf_capture = False
        self.cart = {"title": cart} if cart else None
        self._net = net
        for k, v in dict(meters).items():
            setattr(self, k, v)

    def perf_net(self):
        return self._net


def _drive(monkeypatch, sampler, ws, frames, elapsed, drawn):
    """One whole period through the FrameLoop.account hook."""
    for i in range(frames):
        if i == frames - 1:
            ws._frames_drawn = drawn
            device_boot._ticks_ms.at[0] = sampler._at   # the period expires
        sampler.account(0, elapsed, 0)


def _clock(monkeypatch):
    at = [0]

    def now():
        return at[0]
    now.at = at
    monkeypatch.setattr(device_boot, "_ticks_ms", now)
    monkeypatch.setattr(device_boot, "_ticks_diff", lambda a, b: a - b)
    return at


def test_the_sampler_measures_the_P4s_captured_idle_line(monkeypatch):
    """The whole path, executed: the loop's frame/busy/drawn accumulators, the
    shared console meters, this board's cumulative overlap counters turned into
    per-sample deltas -- and the bytes that came off the wire."""
    _clock(monkeypatch)
    ov = [(0,) * 7]
    ws = PerfWs(meters={"_draw_ms": 33.0, "_flush_ms": 1.0, "_upd_ms": 0.0,
                        "_cart_ms": 0.0, "_chrome_ms": 33.0,
                        "_pf_wm_restore": 28, "_pf_wm_windows": 1,
                        "_pf_wm_stamp": 0, "_pf_home": None})
    out = []
    s = device_boot.PerfSampler(ws, overlap=lambda: ov[0], emit=out.append)
    _drive(monkeypatch, s, ws, 124, 2, 0)
    assert out == [PERF_CASES["p4"][1]]


def test_a_board_with_no_overlap_source_reports_the_PPA_columns_absent(
        monkeypatch):
    """The S3 boards pass no `overlap`, and that is the whole of their
    declaration: no argument, no columns, `-` rather than a zero."""
    _clock(monkeypatch)
    ws = PerfWs(meters={"_draw_ms": 72.0, "_flush_ms": 0.0, "_upd_ms": 0.0,
                        "_cart_ms": 0.0, "_chrome_ms": 72.0})
    out = []
    s = device_boot.PerfSampler(ws, emit=out.append)
    _drive(monkeypatch, s, ws, 122, 4, 0)
    assert out == [PERF_CASES["guition"][1]]


def test_the_overlap_counters_arrive_as_deltas_over_one_sample(monkeypatch):
    """`overlap_stats` is CUMULATIVE, and the mapping from its seven counters to
    ppa=/fence_ms/gfence_ms is the FORMAT's business, so it lives once -- a
    second board with an overlap engine hands over the same shape and says
    nothing about layout."""
    _clock(monkeypatch)
    # us, and large enough that the fence columns survive their one decimal.
    reads = [tuple(i * 10000 for i in range(1, 8)),
             tuple(i * 20000 for i in range(1, 8))]
    ws = PerfWs()
    out = []
    s = device_boot.PerfSampler(ws, overlap=lambda: reads.pop(0),
                                emit=out.append)
    _drive(monkeypatch, s, ws, 2, 0, 0)
    got = parse_perf(out[0])
    d = [i * 10000 for i in range(1, 8)]
    assert got["ppa"] == (d[0], d[1], d[2], d[4], d[6])
    assert got["fence_ms"] == d[3] / 1000.0
    assert got["gfence_ms"] == d[5] / 1000.0


def test_the_absent_lockstep_marker_is_a_dash_and_never_a_zero(monkeypatch):
    """`-` is NO SESSION; 0 is a real reading (matched but frozen). A mutant
    that folds them together (`if not net`) dies here. Read BARE where every
    field beside it is a getattr, because `-` is legitimate: a getattr default
    would let a renamed meter forge "no match" forever."""
    seen = {}
    for net in (None, 0, 30):
        _clock(monkeypatch)
        out = []
        ws = PerfWs(net=net)
        s = device_boot.PerfSampler(ws, emit=out.append)
        _drive(monkeypatch, s, ws, 2, 0, 0)
        seen[net] = out[0].split("net=")[1].split(" ")[0]
    assert seen == {None: "-", 0: "0", 30: "30"}


def test_a_raising_meter_costs_the_sample_and_not_the_loop(monkeypatch):
    """What dropped the P4 to the REPL two seconds after boot: this hook runs
    after pace, OUTSIDE the frame `try`, so a rename in the shared
    runtime/console.py used to end the loop with a traceback naming the
    sampler. It must PRINT instead -- and the timer must reset either way, or a
    broken sample becomes a per-frame retry flooding the serial it is measured
    over."""
    def boom():
        raise AttributeError("overlap_stats")

    _clock(monkeypatch)
    out = []
    ws = PerfWs()
    s = device_boot.PerfSampler(ws, overlap=lambda: (0,) * 7, emit=out.append)
    s._overlap = boom
    _drive(monkeypatch, s, ws, 40, 1, 7)
    assert out == ["PERF sample failed: AttributeError: overlap_stats"]
    assert (s._n, s._busy, s._drawn) == (0, 0, 7)


@pytest.mark.parametrize("frames", [40])
def test_the_sampler_emits_once_per_period_and_never_once_per_frame(frames,
                                                                    monkeypatch):
    """The timer reset must run on the SUCCESS path too. A sampler that emits
    and does not reset prints every frame afterwards, flooding the serial it is
    measured over -- and `tools/p4_perf.py` would median hundreds of samples
    per cart without noticing."""
    at = _clock(monkeypatch)
    out = []
    ws = PerfWs()
    s = device_boot.PerfSampler(ws, emit=out.append)
    for period in range(2):
        at[0] = s._at
        for _ in range(frames):
            s.account(0, 1, 0)
        assert len(out) == period + 1, out


def test_the_meters_follow_PERF_DIAG_live(monkeypatch):
    """Settings -> PERF DIAG (#68) arms the deep meters, and flipping it must
    need no reboot. The BOOT arm stays in each run_desktop (a service
    assignment tests/test_board_service_parity.py reads); this is the re-sync,
    which all three copies carried and which is now written once."""
    for live in (True, False):
        _clock(monkeypatch)
        ws = PerfWs(diag_live=live)
        ws.perf_capture = not live
        s = device_boot.PerfSampler(ws, emit=lambda _l: None)
        _drive(monkeypatch, s, ws, 2, 0, 0)
        assert ws.perf_capture is live


# -- what each board declares ---------------------------------------------------


def _perf_call(board):
    """The board's `PerfSampler(...)` construction, as AST. Static because these
    modules import `machine`; the emitter they hand it to is executed above."""
    path = PERF_BOARDS[board][0] / "moy_runtime.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id == "PerfSampler"):
            return node
    raise AssertionError("%s constructs no PerfSampler" % path)


@pytest.mark.parametrize("board", sorted(PERF_BOARDS))
def test_every_board_emits_through_the_one_sampler(board):
    """No board writes a PERF line of its own. It had three producers with three
    shapes; a fourth board would have copied one of them. `FrameLoop.account` is
    the home -- it runs after pace, which is where frame accounting belongs."""
    path = PERF_BOARDS[board][0] / "moy_runtime.py"
    src = path.read_text(encoding="utf-8")
    # A FORMAT is a string literal starting "PERF " -- AST, so the prose about
    # why this rule exists does not satisfy the rule. `diag.ring("PERF", ...)`
    # passes the ring's TAG, which has no trailing space and is not a format.
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            assert not node.value.startswith("PERF "), (board, node.value)
    assert "PerfSampler(" in src and "_perf.account" in src, board


@pytest.mark.parametrize("board", sorted(PERF_BOARDS))
def test_only_the_board_with_a_PPA_declares_an_overlap_source(board):
    """The one per-board argument, and the ONLY one: `overlap` is a compositor
    that counts async work, which is the P4's DSI/PPA path and nothing else.
    The windowed-WM columns need no argument -- wm_windowed stamps them on the
    Workstation and a board that does not stage it never has them, so the
    getattr IS the capability probe."""
    kw = {k.arg for k in _perf_call(board).keywords}
    assert ("overlap" in kw) is PERF_BOARDS[board][1], (board, sorted(kw))
    assert not (kw - {"overlap", "emit"}), \
        "%s declares per-board FIELDS again: %s" % (board, sorted(kw))


def test_the_T_Deck_still_rings_its_samples_for_the_offline_log():
    """TWO SINKS, ONE LINE. That board's serial RX was dead for months and the
    SD ring is why anything was known about it, so the sample is persisted --
    but through `diag.ring`, not `diag.log`, which would put it on the wire a
    second time. And it is PRINTED now, like every other board: through the ring
    alone it carried the `Moybyte <ms> ` stamp that made both readers drop it."""
    src = (TDECK / "moy_runtime.py").read_text(encoding="utf-8")
    assert "def _perf_emit(line):" in src
    assert 'diag.ring("PERF", line[5:])' in src
    assert "print(line)" in src
    assert "_diag_perf_sample" not in src
    diag = (ROOT / "device" / "moybyte_diag.py").read_text(encoding="utf-8")
    assert "def ring(tag, msg):" in diag
    assert "def format_perf(" not in diag and "def log_perf(" not in diag


# -- the two shared frame-loop verbs, EXECUTED (#208 rank 5) --------------------
#
# `apply_touch` and `poll_webhost` are shared by all three boards and were
# asserted only as source STRINGS in test_micropython_spike.py -- the shape #208
# exists to stop. The routing greps there stay (a board must still CALL them);
# what runs here is the body.


class _Touch:
    """A `device_input.Touch`-shaped source: `poll()` returns (x, y, tap), False
    is never returned to this verb (the driver folds it into None), and `fresh`
    marks a repeat of a sample the hardware never re-took."""

    def __init__(self, samples, fresh=None):
        self._samples = list(samples)
        self._fresh = list(fresh) if fresh is not None else None
        self.fresh = True
        self.polls = 0

    def poll(self):
        if self._fresh is not None:
            self.fresh = self._fresh[self.polls]
        self.polls += 1
        return self._samples[self.polls - 1]


def _pointer(w=320, h=240):
    from runtime.widgets import Pointer

    return Pointer(w, h)


def test_a_touch_sample_places_the_pointer_and_reports_the_tap():
    p = _pointer()
    touched, clicked = device_boot.apply_touch(_Touch([(40, 90, True)]), p)
    assert (touched, clicked) == (True, True)
    assert (p.x, p.y) == (40, 90)
    assert p.down is True


def test_the_tap_flag_is_the_press_EDGE_not_the_level():
    """A held finger reports down every pass and taps once. Returning the level
    as the click would re-fire the launcher's open on every frame of a drag."""
    t = _Touch([(10, 10, True), (12, 10, False), (14, 10, False)])
    p = _pointer()
    passes = [device_boot.apply_touch(t, p) for _ in range(3)]
    assert passes == [(True, True), (True, False), (True, False)]
    assert p.down is True


def test_a_pass_with_no_sample_lifts_the_pointer():
    p = _pointer()
    device_boot.apply_touch(_Touch([(5, 5, True)]), p)
    assert device_boot.apply_touch(_Touch([None]), p) == (False, False)
    assert p.down is False
    assert (p.x, p.y) == (5, 5)      # the position is not reset by a lift


def test_pointer_down_is_a_LEVEL_so_a_held_finger_survives_a_stale_pass():
    """#74: the GT911 hands over ~20-30 samples/s against a 30-60fps loop, so
    MOST frames of a real drag are repeats. The driver holds the point and this
    verb must read it as still-down, or the gesture ends mid-swipe."""
    t = _Touch([(100, 50, True), (100, 50, False), (108, 50, False)],
               fresh=[True, False, True])
    p = _pointer()
    downs = []
    for _ in range(3):
        device_boot.apply_touch(t, p)
        downs.append((p.down, p.fresh, p.x))
    assert downs == [(True, True, 100), (True, False, 100), (True, True, 108)]


def test_the_stale_mark_is_carried_even_on_the_lift_pass():
    """The mark is set BEFORE the None bail: kinetic scrolling reads `fresh` on
    the release frame too, and a lift that left it stale-cleared would charge
    the fling a delta the hardware never measured (#113)."""
    t = _Touch([None], fresh=[False])
    p = _pointer()
    p.fresh = True
    device_boot.apply_touch(t, p)
    assert p.fresh is False


def test_a_backend_with_no_stale_mark_reads_as_always_fresh():
    """The host mouse and the P4's own feed report a level every frame; absence
    of the attribute must mean fresh, not stale, or every host frame would bank
    its time instead of measuring."""
    class _NoFresh:
        def poll(self):
            return (1, 2, False)

    p = _pointer()
    p.fresh = False
    device_boot.apply_touch(_NoFresh(), p)
    assert p.fresh is True


def test_the_placed_point_is_clamped_to_the_canvas():
    p = _pointer(320, 240)
    device_boot.apply_touch(_Touch([(999, -4, False)]), p)
    assert (p.x, p.y) == (319, 0)


# -- poll_webhost --------------------------------------------------------------


class _Host:
    def __init__(self, serving=True, error=None):
        self.serving = serving
        self.error = error
        self.polls = 0

    def poll(self):
        self.polls += 1
        if self.error is not None:
            raise self.error


class _WSWeb:
    def __init__(self, webhost=None):
        self.webhost = webhost


def test_the_webhost_is_polled_once_a_frame_while_it_serves():
    ws = _WSWeb(_Host())
    device_boot.poll_webhost(ws)
    device_boot.poll_webhost(ws)
    assert ws.webhost.polls == 2


def test_a_bound_listener_that_is_not_serving_is_left_alone():
    """`serving` is the gate, not `is not None`: a webhost object exists from
    boot on every board, and polling one that never started would spend a
    syscall per frame on all three."""
    ws = _WSWeb(_Host(serving=False))
    assert device_boot.poll_webhost(ws) == 0
    assert ws.webhost.polls == 0


def test_a_board_with_no_webhost_at_all_costs_nothing():
    assert device_boot.poll_webhost(_WSWeb(None)) == 0
    assert device_boot.poll_webhost(object()) == 0


def test_a_failing_poll_never_breaks_the_frame(capsys):
    """It runs at the frame TAIL of a single-threaded loop; an escaped exception
    there is the desktop dropping to the REPL. It reports and carries on."""
    ws = _WSWeb(_Host(error=RuntimeError("socket gone")))
    assert device_boot.poll_webhost(ws) >= 0
    assert "WEB ERR RuntimeError: socket gone" in capsys.readouterr().out
    device_boot.poll_webhost(ws)
    assert ws.webhost.polls == 2      # and the next frame still polls


def test_the_elapsed_ms_is_measured_around_the_poll(monkeypatch):
    """The T-Deck's HITCH line carries this as `web=`; a serve that stalls the
    desktop must be visible as the cost it is."""
    clock = [0]
    monkeypatch.setattr(device_boot, "_ticks_ms", lambda: clock[0])

    class _Slow(_Host):
        def poll(self):
            _Host.poll(self)
            clock[0] += 47

    assert device_boot.poll_webhost(_WSWeb(_Slow())) == 47


def test_a_failing_poll_still_reports_the_time_it_burned(monkeypatch):
    """The stall is the interesting number precisely when the transfer died."""
    clock = [0]
    monkeypatch.setattr(device_boot, "_ticks_ms", lambda: clock[0])

    class _SlowBoom(_Host):
        def poll(self):
            clock[0] += 12
            raise OSError(104)

    assert device_boot.poll_webhost(_WSWeb(_SlowBoom())) == 12
