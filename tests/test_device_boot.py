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
import sys
from pathlib import Path

import pytest

from board_source import runtime_text, wiring_chain

ROOT = Path(__file__).resolve().parent.parent
TDECK = ROOT / "firmware" / "lilygo_t_deck_plus_mainline" / "modules"
P4 = ROOT / "firmware" / "esp32_p4_wifi6_touch_lcd_7b" / "modules"

from runtime import boot_carts, device_boot  # noqa: E402


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


class FakePlayer:
    """The one thing the loop asks the Player: the cart's tick period, 0 when
    no game is paced (#217)."""

    def __init__(self, tick_ms=0):
        self.tick_ms = tick_ms


class FakeWs:
    def __init__(self, frames=0, tick_ms=0):
        self._frames_drawn = frames
        self.player = FakePlayer(tick_ms)
        self.armed = 0
        self.announced = 0

    def arm_splash(self):
        self.armed += 1

    def announce_update(self):
        self.announced += 1


class FakeStore:
    CARTS_DIR = "/sd/moybyte/carts"

    def __init__(self, carts=None, raise_on=None, dead_root=None):
        self.carts = carts if carts is not None else [{"title": "a"}]
        self.raise_on = raise_on
        # A store that is dead at ONE root and fine at another: an empty card
        # slot, which is not "storage is broken" but "storage is elsewhere".
        self.dead_root = dead_root
        self.calls = []

    def ensure_dirs(self, root):
        self.calls.append(("ensure_dirs", root))
        if self.raise_on == "ensure_dirs" or root == self.dead_root:
            raise OSError("no card")

    def sweep_store(self, root, seed=None):
        self.calls.append(("sweep_store", root))

    def seed(self, seed, root, shelf, progress=None):
        # The real store dispatches on the roster's FORM (moy_seed.seed_any);
        # what the boot spine has to get right is that it seeds AFTER the
        # scan, against the shelf the scan read.
        self.calls.append(("seed_builtins", root, len(seed)))
        if self.raise_on == "seed_builtins":
            raise OSError("write failed")
        if progress is not None:
            for i in range(len(seed)):
                progress(i, len(seed), seed[i].get("title"))
        return shelf

    def embedded_floor(self, seed):
        return [dict(c) for c in seed]

    def catalogue(self, root):
        self.calls.append(("catalogue", root))
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


class _Clock:
    """The boot's ms clock, moved by hand: a cart the seed writes takes
    ~550ms, one it only checks takes none."""

    def __init__(self, monkeypatch, step):
        self.now, self.step = 1000, step
        monkeypatch.setattr(boot_carts, "_ticks_ms", self.tick)

    def tick(self):
        self.now += self.step
        return self.now


def test_a_progress_repaint_moves_the_bar_and_keeps_the_wire_quiet(capsys,
                                                                   monkeypatch):
    _Clock(monkeypatch, 550)
    boot, canvas, _ = _boot()
    boot.seed_progress(0, 32, "cart")
    boot.seed_progress(1, 32, "cart")

    out = capsys.readouterr().out
    # Every eighth cart reaches serial; a repaint says nothing to someone
    # watching the wire, and one line per cart would drown the boot log.
    assert out == "Moybyte boot: loading cartridges 1/32\n"
    assert canvas.paints == 2


def test_a_seed_that_writes_nothing_paints_the_bar_once(capsys, monkeypatch):
    """A warm boot checks every built-in and writes none: a repaint per cart
    there was half the seed step on an S3 (#224). The wire keeps its line
    every eighth cart."""
    _Clock(monkeypatch, 2)
    boot, canvas, _ = _boot()
    for i in range(31):
        boot.seed_progress(i, 31, "cart")
    assert canvas.paints == 1
    assert capsys.readouterr().out.count("loading cartridges") == 4


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


def _raises_session(fn):
    """The T-Deck's `with_sd_live` against an empty slot: the mount itself is
    what fails, before fn() is ever reached."""
    return fn()


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
    assert [c[0] for c in store.calls] == ["ensure_dirs", "sweep_store", "catalogue",
                                           "seed_builtins"]
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


def test_a_cardless_board_keeps_a_writable_store_on_internal_flash(capsys):
    """The T-Deck bug: no card in the slot meant a None root, which
    `wire_workstation_core` turns into can_manage=False -- a console showing
    every cart and able to save, edit or import none of them."""
    boot, _, _ = _boot()
    store = FakeStore(dead_root="/sd/moybyte/carts")

    carts, root = boot.load_carts(store, [{"title": "s"}], session=_raises_session,
                                  media="SD", fallback_root="/moy/carts")

    assert root == "/moy/carts", ("a board with no card must still get a real "
                                  "root -- that is what keeps it writable")
    assert ("seed_builtins", "/moy/carts", 1) in store.calls, \
        "the fallback store must be SEEDED, not just scanned"
    out = capsys.readouterr().out
    assert "Moybyte SD carts unavailable: no card" in out
    assert "Moybyte loaded 1 carts from flash" in out
    assert "using built-in carts" not in out, \
        "the embedded read-only floor is for when BOTH stores are gone"


def test_the_fallback_store_runs_without_the_boards_session():
    """The session wrapper exists for the bus that just failed. Internal flash
    shares nothing with the panel, and routing it through the SD bracket would
    mount a card that is not there."""
    boot, _, _ = _boot()
    seen = []

    def session(fn):
        seen.append("used")
        raise OSError("no card")

    boot.load_carts(FakeStore(), [{"title": "s"}], session=session,
                    fallback_root="/moy/carts")
    assert seen == ["used"], "the primary attempt uses it exactly once"


def test_both_stores_gone_still_reaches_the_read_only_floor(capsys):
    boot, _, _ = _boot()
    store = FakeStore(raise_on="ensure_dirs")
    seed = [{"title": "built-in"}]

    carts, root = boot.load_carts(store, seed, fallback_root="/moy/carts")

    assert root is None and carts == seed and carts[0] is not seed[0]
    out = capsys.readouterr().out
    assert "Moybyte flash carts unavailable: no card" in out
    assert "Moybyte using built-in carts" in out


def test_a_working_card_is_never_second_guessed():
    """No fallback attempt when the primary store answered: two live stores is
    the arrangement where a save lands in the one nobody is looking at."""
    boot, _, _ = _boot()
    store = FakeStore()
    carts, root = boot.load_carts(store, [{"title": "s"}],
                                  fallback_root="/moy/carts")
    assert root == store.CARTS_DIR
    assert not any("/moy/carts" in str(c) for c in store.calls)


def test_an_empty_store_also_falls_back(capsys):
    boot, _, _ = _boot()
    carts, root = boot.load_carts(FakeStore(carts=[]), [{"title": "b"}])
    assert (len(carts), root) == (1, None)


def test_the_seed_progress_bar_is_wired_into_the_store_call(monkeypatch):
    _Clock(monkeypatch, 550)
    boot, canvas, _ = _boot()
    boot.load_carts(FakeStore(), [{"title": "a"}, {"title": "b"}])
    # One repaint per cart written: free against ~550ms of flash writes each,
    # and the only stretch of a first boot that knows how much of itself is
    # left.
    assert canvas.paints == 2


# -- the cart runtimes --------------------------------------------------------


def test_a_build_without_moycore_says_absent_rather_than_failing(capsys, monkeypatch):
    # The host has its moycore (runtime/moycore.py); a board built without
    # the native modules has none, which is this.
    monkeypatch.setitem(sys.modules, "moycore_glue", None)
    boot, _, _ = _boot()
    # No `moycore_glue` on the host: exactly the shape of a board built without
    # the native modules, where a `runtime: lua` or `runtime: wasm` cart opens
    # the Player's runtime-missing panel instead of crashing -- an absent key.
    assert boot.runtimes(FakeWs()) == {}
    out = capsys.readouterr().out
    assert "Moybyte lua runtime ABSENT" in out
    assert "Moybyte wasm runtime ABSENT" in out


def test_the_runtime_status_can_be_routed_to_a_boards_own_log(monkeypatch):
    monkeypatch.setitem(sys.modules, "moycore_glue", None)
    boot, _, _ = _boot()
    lines = []
    boot.runtimes(FakeWs(), log=lines.append)
    # The T-Deck sends it to the offline diag ring: that board's USB-CDC RX is
    # dead under the desktop, so a status that is not recorded cannot be asked
    # for afterwards.
    assert lines == ["lua runtime ABSENT", "wasm runtime ABSENT"]


# -- the internal-SRAM census -------------------------------------------------


def test_the_sram_census_names_its_six_stages_in_boot_order(monkeypatch):
    """One census, every board (#66/#67, 2026-09-08).

    It was four hand-placed calls in the T-Deck's `run_desktop` and nowhere
    else, so the one question a Lua cart's PSRAM fallback raises -- who took
    the internal SRAM, and at which stage -- could be asked only on the board
    that happened to have the calls. The deltas between the lines are the
    whole point: any single line is a number without an owner. "scanned"
    splits the store's scan from the seed that follows it, which the memory
    census (`device/mem_census.py`) marks too.
    """
    seen = []
    monkeypatch.setattr(device_boot, "sram_census", seen.append)
    monkeypatch.setattr(boot_carts, "sram_census", seen.append)
    boot, _, _ = _boot()

    boot.load_carts(FakeStore(), [{"title": "s"}])
    boot.runtimes(FakeWs())
    boot.start_frames(FakeWs())

    assert seen == ["rd-entry", "scanned", "seeded", "carts", "console",
                    "desktop-up"]


def test_a_store_that_fails_still_weighs_the_heap_the_scan_fragmented(monkeypatch):
    """`carts` is the reading the scan is measured BY, so it has to survive the
    scan failing -- a board that fell back to its embedded carts is exactly the
    one whose heap someone is about to ask about."""
    seen = []
    monkeypatch.setattr(device_boot, "sram_census", seen.append)
    monkeypatch.setattr(boot_carts, "sram_census", seen.append)
    boot, _, _ = _boot()

    boot.load_carts(FakeStore(raise_on="ensure_dirs"), [{"title": "b"}])

    assert seen == ["rd-entry", "carts"]


def test_the_census_is_a_no_op_off_board():
    """The host has one region and no `device_util` staged, so the fallback has
    to be callable and silent -- every board method above calls it
    unconditionally."""
    assert device_boot.sram_census("anything") is None
    assert boot_carts.sram_census("anything") is None


# -- the OTA verdict ----------------------------------------------------------
#
# The boot reads it before anything can overwrite the evidence; the confirm is
# the kernel loop's (tests/test_moy_loop.py, tests/test_ota_health.py).


class FakeUpdater:
    def __init__(self, verdict=None):
        self.verdict = verdict

    def boot_check(self):
        return self.verdict


def test_the_boot_verdict_reaches_both_the_log_and_the_desktop():
    ws = FakeWs()
    ws.updater = FakeUpdater(verdict=("rolled_back", "the update did not stick"))
    lines = []
    device_boot.report_update(ws, lines.append)
    assert lines == ["last update rolled_back (the update did not stick)"]
    assert ws.announced == 1, "a verdict nobody sees on the glass is not a verdict"


def test_no_verdict_says_nothing():
    ws = FakeWs()
    ws.updater = FakeUpdater(verdict=None)
    lines = []
    device_boot.report_update(ws, lines.append)
    assert lines == [] and ws.announced == 0


def test_a_board_with_no_updater_is_silent_and_harmless():
    ws = FakeWs()
    lines = []
    device_boot.report_update(ws, lines.append)
    assert lines == []


def test_a_throwing_boot_check_never_blocks_the_desktop():
    class Angry(FakeUpdater):
        def boot_check(self):
            raise OSError("marker unreadable")

    ws = FakeWs()
    ws.updater = Angry()
    lines = []
    device_boot.report_update(ws, lines.append)
    assert lines == ["boot_check failed: marker unreadable"]


# -- both boards drive the same spine ----------------------------------------


def _fn(path, name):
    src = path.read_text(encoding="utf-8")
    for node in ast.walk(ast.parse(src, filename=str(path))):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError("%s has no %s()" % (path, name))


def _boot_body(path):
    """The function that runs a board's boot: the LAST link of its delegation
    chain (the shared spine's `build_desktop`), since the board's own
    run_desktop is the arguments, not the boot."""
    spine, name = wiring_chain(path)[-1]
    return _fn(spine, name)


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


GUITION = ROOT / "firmware" / "guition_jc3248w535" / "modules"
GUITION_P4 = ROOT / "firmware" / "guition_jc8012p4a1c" / "modules"
BOARDS = {"tdeck": TDECK / "moy_runtime.py", "p4": P4 / "moy_runtime.py",
          "guition": GUITION / "moy_runtime.py",
          "guition_p4": GUITION_P4 / "moy_runtime.py"}
SPINE = ROOT / "device" / "desktop_spine.py"


@pytest.mark.parametrize("board", sorted(BOARDS))
def test_each_board_imports_the_shared_spine(board):
    src = runtime_text(BOARDS[board])
    lines = src.splitlines()
    boot = [l for l in lines if l.startswith("from device_boot import")]
    assert boot, "%s does not import the shared spine" % board
    # Somewhere down the chain the boot's import carries DeviceBoot; the
    # frame is the kernel's, which a board reaches through `Desktop.run`.
    assert any("DeviceBoot" in l for l in boot), (
        "%s does not import DeviceBoot: %s" % (board, boot))
    assert "frame_loop" not in src
    # The staged names are flat, never the host package path -- there is no
    # `runtime` package on a board.
    assert "from runtime.device_boot" not in src


@pytest.mark.parametrize("board", sorted(BOARDS))
def test_every_board_takes_the_one_boot_body(board):
    """The invariant Phase 4 buys, stated as a test.

    Four boards, one console: the difference between their boots should be
    HARDWARE (an esp_lcd strip flush vs a DPI scan-out, a trackball vs BLE
    HID), never the order in which the shared steps happen or whether one of
    them happens at all. So the boot body is ONE function, every board's
    chain ends in it, and a board's own run_desktop makes no boot step of its
    own beside it.
    """
    chain = wiring_chain(BOARDS[board])
    assert (SPINE, "build_desktop") in chain, (board, chain)
    # The spine is the last body that boots: what follows it is the board's
    # input module and the spine's providers, which construct and boot nothing.
    after = chain[chain.index((SPINE, "build_desktop")) + 1:]
    for spine, fname in after:
        assert _calls_on(_fn(spine, fname), "boot") == [], (board, spine)
    own = _fn(BOARDS[board], "run_desktop")
    assert _calls_on(own, "boot") == [], (
        "%s: run_desktop drives boot steps beside the shared spine" % board)


def test_the_boot_steps_run_in_ONE_order():
    """...and the order itself, pinned where it lives. When this fails, read
    the diff before changing the test -- either a board grew a real reason to
    reorder, or a step went missing, which is the failure this whole phase
    exists to make loud."""
    seq = [m for m, _ in _calls_on(_fn(SPINE, "build_desktop"), "boot")]
    assert seq == ["note", "note", "load_carts", "note",
                   "runtimes", "start_frames"], seq


def test_both_boards_hand_the_console_to_the_one_kernel_loop():
    """The frame is the kernel's (native/moy_kernel/moy_loop.c): every board's
    run_desktop ends by handing its Desktop to the loop, the spine registers
    the console's upcalls exactly once, and no board drives a pump or a frame
    of its own beside it."""
    for name, path in BOARDS.items():
        for spine, fname in wiring_chain(path):
            calls = _calls_on(_fn(spine, fname), "pump")
            assert calls == [], (
                "%s: %s drives a pump beside the kernel's loop -- %s"
                % (name, spine.name, calls))
        src_txt = runtime_text(path)
        assert "d.run(" in src_txt, "%s never hands the console over" % name
        assert "while True" not in src_txt.split("def run_desktop")[1].split("\ndef ")[0]
    spine_src = SPINE.read_text(encoding="utf-8")
    assert spine_src.count("moy_loop.register(") == 1


def test_the_spine_imports_no_board_module():
    """What lets this file live in `runtime/` and be tested at all.

    Every hardware object arrives as an argument. If a board import creeps in,
    `runtime/device_boot.py` stops importing on the host (killing every
    assertion above) and starts being un-stageable to the wasm head, whose
    `DENY` glob does not exclude it.
    """
    # `moycore_glue` and `device_util` are device-tier LEAVES, not board
    # modules: both are staged to every board, neither knows a panel or a pin,
    # and both are imported behind an ImportError guard with a working
    # off-board answer. A firmware/ module never belongs here -- nor in the
    # cart step the spine takes (boot_carts.py), whose store is an argument.
    # `crash_guard` is a runtime leaf, and `moy_kernel` is the kernel's binding
    # (native/moy_kernel), guarded the same way: absent, the first frame
    # proves nothing to nobody.
    allowed = {"console", "runtime", "chrome", "ticks", "moycore_glue",
               "device_util", "perf_line", "time", "gc", "boot_carts",
               "crash_guard", "moy_kernel"}
    seen = set()
    for name in ("device_boot.py", "boot_carts.py"):
        src = (ROOT / "runtime" / name).read_text(encoding="utf-8")
        for node in ast.walk(ast.parse(src)):
            if isinstance(node, ast.Import):
                seen |= {a.name.split(".")[0] for a in node.names}
            elif isinstance(node, ast.ImportFrom) and node.module:
                seen.add(node.module.split(".")[0])
    assert seen <= allowed, "device_boot imports board modules: %s" % sorted(
        seen - allowed)


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

from runtime.moy_loop import perf_format as format_perf  # noqa: E402
from runtime.perf_line import ABSENT, FIELDS, parse_perf  # noqa: E402

# name -> (board dir, whether it hands the sampler an overlap source)
PERF_BOARDS = {
    "tdeck": (TDECK, False),
    "p4": (P4, True),
    "guition": (ROOT / "firmware" / "guition_jc3248w535" / "modules", False),
    "guition_p4": (GUITION_P4, True),
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
#   p4_dark  the P4 in a sample the windowed WM did not run in: nothing wrote
#            _pf_wm_*, so those read `-` -- "not measured" and "measured
#            zero" are different answers.
PERF_CASES = {
    "p4": (
        {"cart": None, "fps": (0, 62), "net": None, "busy": 2,
         "draw": 33.0, "flush": 1.0, "logic": 0.0, "render": 0.0,
         "chrome": 33.0, "wmr": 28, "wmw": 1, "wms": 0,
         "ppa": (0, 0, 0, 0, 0), "fence_ms": 0.0, "gfence_ms": 0.0,
         "home": None},
        "PERF cart=- fps=0/62 net=- tick=- miss=- busy=2ms draw=33 flush=1 "
        "logic=0 render=0 chrome=33 wmr=28 wmw=1 wms=0 ppa=0/0/0/0/0 "
        "fence_ms=0.0 gfence_ms=0.0 home=- gc=-"),
    "guition": (
        {"cart": None, "fps": (0, 61), "net": None, "busy": 4,
         "draw": 72.0, "flush": 0.0, "logic": 0.0, "render": 0.0,
         "chrome": 72.0, "home": None},
        "PERF cart=- fps=0/61 net=- tick=- miss=- busy=4ms draw=72 flush=0 "
        "logic=0 render=0 chrome=72 wmr=- wmw=- wms=- ppa=- fence_ms=- "
        "gfence_ms=- home=- gc=-"),
    "tdeck": (
        {"cart": "Sakura Lua", "fps": (53, 55), "net": None, "busy": 18,
         "draw": 14.0, "flush": 0.0, "logic": 3.0, "render": 9.0,
         "chrome": 2.0, "home": None},
        "PERF cart=Sakura_Lua fps=53/55 net=- tick=- miss=- busy=18ms draw=14 "
        "flush=0 logic=3 render=9 chrome=2 wmr=- wmw=- wms=- ppa=- fence_ms=- "
        "gfence_ms=- home=- gc=-"),
    "p4_dark": (
        {"cart": None, "fps": (0, 62), "net": None, "busy": 2,
         "draw": 33.0, "flush": 1.0, "logic": 0.0, "render": 0.0,
         "chrome": 33.0, "ppa": (0, 0, 0, 0, 0), "fence_ms": 0.0,
         "gfence_ms": 0.0, "home": None},
        "PERF cart=- fps=0/62 net=- tick=- miss=- busy=2ms draw=33 flush=1 "
        "logic=0 render=0 chrome=33 wmr=- wmw=- wms=- ppa=0/0/0/0/0 "
        "fence_ms=0.0 gfence_ms=0.0 home=- gc=-"),
}

# Every column populated and every value DISTINCT: the captures above are idle
# and mostly zeros, which cannot tell a swapped pair of fields from a correct
# one, nor `%.0f` from `%d`. Each rounding here is one a plain int cast gets
# wrong, and the cart title carries a space the tokeniser must not see.
PERF_LOUD = (
    {"cart": "Brick Siege", "fps": (20, 31), "net": 30, "tick": (60, 2),
     "miss": 3, "busy": 8,
     "draw": 3.6, "flush": 1.4, "logic": 5.4, "render": 12.7, "chrome": 2.2,
     "wmr": 7, "wmw": 8, "wms": 9, "ppa": (1, 2, 3, 4, 0),
     "fence_ms": 2.5, "gfence_ms": 0.7, "home": (3, 4, 1),
     "gc": (6, 41530, 11200)},
    "PERF cart=Brick_Siege fps=20/31 net=30 tick=60/2 miss=3 busy=8ms draw=4 "
    "flush=1 logic=5 render=13 chrome=2 wmr=7 wmw=8 wms=9 ppa=1/2/3/4/0 "
    "fence_ms=2.5 gfence_ms=0.7 home=3/4/1 gc=6/41530/11200")


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


# -- what each board declares ---------------------------------------------------


@pytest.mark.parametrize("board", sorted(PERF_BOARDS))
def test_every_board_emits_through_the_one_sampler(board):
    """No board writes a PERF line of its own. It had three producers with three
    shapes; the one writer is the kernel's (moy_perf.c), sampled from the
    loop's account stage on every board, and the console only pushes its half
    (`ws.perf_push`) on the frame a sample is due."""
    path = PERF_BOARDS[board][0] / "moy_runtime.py"
    src = runtime_text(path)
    # A FORMAT is a string literal starting "PERF " -- AST, so the prose about
    # why this rule exists does not satisfy the rule.
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            assert not node.value.startswith("PERF "), (board, node.value)
    assert "PerfSampler" not in src, board
    spine = SPINE.read_text(encoding="utf-8")
    assert spine.count("ws.perf_push(moy_loop)") == 1


# -- the frame's pointer sample, EXECUTED (#208 rank 5) ---------------------------
#
# Every board's input stage ends the same way: each source has written its
# pointer sample (the touch driver every frame, the dev channel's gestures),
# begin_frame merges them, and the table's apply_pointer hands the frame's
# sample to the shared pointer. The routing greps (a board must still CALL it)
# are tests/test_board_routing.py's; what runs here is the body.


def _pointer(w=320, h=240):
    from runtime.moy_input import Pointer

    return Pointer(w, h)


def _frame(inp, p, *writes):
    """One frame: the sources' samples, the merge, the frame's sample into
    the pointer. Returns (touched, clicked)."""
    from runtime import moy_input as mi

    for src, sample in writes:
        src.point(*sample)
    inp.begin_frame()
    f = inp.apply_pointer(p)
    return bool(f & mi.P_HELD), bool(f & mi.P_CLICK)


def _table():
    from runtime.moy_input import InputTable

    inp = InputTable()
    return inp, inp.source("touch")


def test_a_touch_sample_places_the_pointer_and_reports_the_tap():
    inp, t = _table()
    p = _pointer()
    assert _frame(inp, p, (t, (40, 90, True, True))) == (True, True)
    assert (p.x, p.y) == (40, 90)
    assert p.down is True


def test_the_tap_flag_is_the_press_EDGE_not_the_level():
    """A held finger reports down every pass and taps once. Returning the level
    as the click would re-fire the launcher's open on every frame of a drag."""
    inp, t = _table()
    p = _pointer()
    passes = [_frame(inp, p, (t, s)) for s in
              ((10, 10, True, True), (12, 10, True, False), (14, 10, True, False))]
    assert passes == [(True, True), (True, False), (True, False)]
    assert p.down is True


def test_an_edge_is_delivered_once_even_if_the_source_writes_nothing_more():
    inp, t = _table()
    p = _pointer()
    assert _frame(inp, p, (t, (10, 10, True, True))) == (True, True)
    assert _frame(inp, p) == (True, False)


def test_a_pass_with_no_finger_lifts_the_pointer():
    inp, t = _table()
    p = _pointer()
    _frame(inp, p, (t, (5, 5, True, True)))
    assert _frame(inp, p, (t, (0, 0, False))) == (False, False)
    assert p.down is False
    assert (p.x, p.y) == (5, 5)      # the position is not reset by a lift


def test_pointer_down_is_a_LEVEL_so_a_held_finger_survives_a_stale_pass():
    """#74: the GT911 hands over ~20-30 samples/s against a 30-60fps loop, so
    MOST frames of a real drag are repeats. The driver holds the point and the
    frame must read it as still-down, or the gesture ends mid-swipe."""
    inp, t = _table()
    p = _pointer()
    downs = []
    for s in ((100, 50, True, True, True), (100, 50, True, False, False),
              (108, 50, True, False, True)):
        _frame(inp, p, (t, s))
        downs.append((p.down, p.fresh, p.x))
    assert downs == [(True, True, 100), (True, False, 100), (True, True, 108)]


def test_the_stale_mark_is_carried_even_on_the_lift_pass():
    """Kinetic scrolling reads `fresh` on the release frame too, and a lift
    that left it stale-cleared would charge the fling a delta the hardware
    never measured (#113)."""
    inp, t = _table()
    p = _pointer()
    p.fresh = True
    _frame(inp, p, (t, (0, 0, False, False, False)))
    assert p.fresh is False


def test_a_sample_is_fresh_unless_its_source_says_otherwise():
    inp, t = _table()
    p = _pointer()
    p.fresh = False
    _frame(inp, p, (t, (1, 2, True)))
    assert p.fresh is True


def test_no_sample_leaves_the_pointer_alone():
    """A board with no touch writes no sample: the trackball's pointer is
    nobody's to lift."""
    from runtime.moy_input import InputTable

    inp = InputTable()
    p = _pointer()
    p.down = True
    assert _frame(inp, p) == (False, False)
    assert p.down is True


def test_a_source_that_is_down_outranks_one_that_is_not():
    """The dev channel's scripted gesture beside a touch driver reporting no
    finger: the merge prefers the source that is down, so a scripted swipe is
    indistinguishable from a finger."""
    inp, t = _table()
    dev = inp.source("devch")
    p = _pointer()
    assert _frame(inp, p, (t, (0, 0, False)), (dev, (60, 70, True, True))) == (True, True)
    assert (p.x, p.y) == (60, 70)
    assert _frame(inp, p, (t, (0, 0, False))) == (True, False)   # dev still down
    assert _frame(inp, p, (dev, (60, 70, False))) == (False, False)


def test_the_placed_point_is_clamped_to_the_canvas():
    inp, t = _table()
    p = _pointer(320, 240)
    _frame(inp, p, (t, (999, -4, True)))
    assert (p.x, p.y) == (319, 0)


def test_a_compound_perf_field_renders_an_absent_component_as_a_dash():
    # A compositor that cannot measure one slot reports None there; the line
    # keeps its siblings' numbers and the reader gets None back, never 0.
    from runtime.perf_line import parse_perf
    line = format_perf({"ppa": (1, None, 2, 3, 0), "fence_ms": None})
    assert "ppa=1/-/2/3/0" in line and "fence_ms=-" in line
    got = parse_perf(line)
    assert got["ppa"] == (1.0, None, 2.0, 3.0, 0.0) and got["fence_ms"] is None
