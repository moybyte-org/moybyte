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
    assert "from device_boot import DeviceBoot, FramePump" in src
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
    for name, path in BOARDS.items():
        calls = _calls_on(_run_desktop(path), "pump")
        assert calls == [("begin", True), ("tail", True), ("pace", True)], (
            "%s: the pump's head, tail and cadence all belong INSIDE the frame "
            "loop, in that order -- %s" % (name, calls))


def test_the_spine_imports_no_board_module():
    """What lets this file live in `runtime/` and be tested at all.

    Every hardware object arrives as an argument. If a board import creeps in,
    `runtime/device_boot.py` stops importing on the host (killing every
    assertion above) and starts being un-stageable to the wasm head, whose
    `DENY` glob does not exclude it.
    """
    src = (ROOT / "runtime" / "device_boot.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    allowed = {"console", "runtime", "chrome", "moycore_glue", "time", "gc"}
    seen = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            seen |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            seen.add(node.module.split(".")[0])
    assert seen <= allowed, "device_boot imports board modules: %s" % sorted(
        seen - allowed)
