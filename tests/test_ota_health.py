"""Did the update actually work? (#53)

Two mechanisms, and they interlock:

  * `confirm_when_healthy` -- the rollback confirm, deferred until the console
    has really PAINTED. The bootloader can only revert an image that never said
    it was fine, so the entire value of the safety net is decided by where that
    claim is made. Made at "the desktop object exists", it confirms firmware
    that has never drawn a pixel -- which is precisely the failure this project
    shipped once already (#56: every boot print appeared, the panel stayed dark).

  * the pending marker -- written at finish() with the slot we pointed the
    bootloader at, read on the next boot to say whether that slot is the one
    now running. Without it a rollback is silent: the kid sits through a
    download, an install and a reboot, lands on the firmware they started with,
    and nothing anywhere says so.

The interlock is the marker's lifetime: read at boot, cleared only at CONFIRM.
An image that boots, reports its verdict and then dies still carries a marker
into the boot after the rollback, so that second failure gets reported too.
"""

import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

TDECK = ROOT / "firmware" / "lilygo_t_deck_plus_mainline" / "modules"
P4 = ROOT / "firmware" / "esp32_p4_wifi6_touch_lcd_7b" / "modules"
# moy_ota lives in the shared device tier at the repo root (the boards' modules/
# dirs only hold gitignored build-staged copies -- absent on a fresh checkout).
DEVICE = ROOT / "device"


def _load_moy_ota():
    spec = importlib.util.spec_from_file_location("moy_ota_health", DEVICE / "moy_ota.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


class _FakePart:
    """The inactive slot. info() is (type, subtype, addr, size, label, encrypted)."""

    def __init__(self, label="ota_1", size=4 * 1024 * 1024):
        self.label = label
        self.size = size
        self.booted = False

    def info(self):
        return (0, 0x10, 0x20000, self.size, self.label, False)

    def set_boot(self):
        self.booted = True


def _updater(tmp_path, running="ota_0"):
    """An updater whose 'SD' is a tmp dir and whose running slot is a plain string.

    mark_valid is replaced by a counter: the real one reaches into esp32 and, off
    the device, would just swallow an ImportError -- which would make "did it try
    to confirm?" untestable, the one thing these tests are about.
    """
    mod = _load_moy_ota()
    d = tmp_path / "update"
    d.mkdir(exist_ok=True)      # a second updater on the same 'card' is the reboot case
    u = mod.OtaUpdater(lambda fn: fn(), update_dir=str(d))
    u._running_label = lambda: running
    u.marked = 0

    def _mark():
        u.marked += 1
        return True

    u.mark_valid = _mark
    return mod, u


# -- the deferred confirm ----------------------------------------------------

def test_a_freshly_built_desktop_is_not_yet_proof_of_a_good_image(tmp_path):
    # The whole point: zero frames is not health. This is the state the old call
    # site confirmed in, and a board that boots to a black screen sits here.
    mod, u = _updater(tmp_path)
    assert u.confirm_when_healthy(0) is False
    assert u.confirmed is False
    assert u.marked == 0


def test_an_image_that_never_paints_is_never_confirmed(tmp_path):
    """The #56 failure: the board boots, prints everything, and stays dark.

    Loop iterations alone must not be enough, or the safety net would confirm
    exactly the image it exists to catch.
    """
    mod, u = _updater(tmp_path)
    for _ in range(mod.HEALTHY_LOOPS * 10):
        assert u.confirm_when_healthy(0) is False
    assert u.marked == 0


def test_the_confirm_waits_for_the_loop_to_keep_running(tmp_path):
    # One painted frame is not enough on its own either: an image that draws
    # once and dies is still a broken image.
    mod, u = _updater(tmp_path)
    for i in range(mod.HEALTHY_LOOPS - 1):
        assert u.confirm_when_healthy(mod.HEALTHY_PAINTS) is False, "at loop %d" % i
    assert u.marked == 0
    assert u.confirm_when_healthy(mod.HEALTHY_PAINTS) is True
    assert u.marked == 1
    assert u.confirmed is True


def test_a_quiet_desktop_still_confirms(tmp_path):
    """The bug the P4 caught: the console repaints only when something changes.

    MEASURED on glass -- an idle desktop had drawn exactly ONE frame six seconds
    after boot. Any paint threshold above 1 leaves every untouched board
    unconfirmed, which rolls back every update that nobody happens to be poking
    at when it lands.
    """
    mod, u = _updater(tmp_path)
    assert mod.HEALTHY_PAINTS == 1
    fired = [u.confirm_when_healthy(1) for _ in range(mod.HEALTHY_LOOPS)]
    assert fired[-1] is True


def test_the_confirm_fires_exactly_once(tmp_path):
    # It runs every frame forever after, so a second mark_app_valid (or a second
    # SD touch to clear the marker) would be a permanent per-frame cost.
    mod, u = _updater(tmp_path)
    fired = [u.confirm_when_healthy(9999) for _ in range(mod.HEALTHY_LOOPS + 200)]
    assert fired.count(True) == 1
    assert u.marked == 1


def test_a_board_that_boots_slowly_still_confirms(tmp_path):
    # The counters are monotonic loop iterations, not wall clock, so a slow board
    # simply takes longer to get there -- it is never disqualified for being slow.
    mod, u = _updater(tmp_path)
    fired = [u.confirm_when_healthy(1) for _ in range(mod.HEALTHY_LOOPS)]
    assert fired.count(True) == 1


# -- the pending marker ------------------------------------------------------

def test_finish_records_the_slot_it_pointed_the_bootloader_at(tmp_path):
    mod, u = _updater(tmp_path, running="ota_0")
    u._part = _FakePart("ota_1")
    assert u.finish() is True
    assert u._part.booted is True
    rec = json.loads(Path(u._pending_path()).read_text(encoding="utf-8"))
    assert rec["slot"] == "ota_1"          # where we are going
    assert rec["version"] == mod.FIRMWARE_VERSION   # what we are leaving
    assert rec["channel"] == mod.FIRMWARE_CHANNEL


def test_a_refused_set_boot_records_nothing(tmp_path):
    # ESP_ERR_OTA_VALIDATE_FAILED on a truncated image: the bootloader was never
    # repointed, so there is no pending update and the next boot must say nothing.
    mod, u = _updater(tmp_path)
    part = _FakePart("ota_1")

    def _boom():
        raise OSError("ESP_ERR_OTA_VALIDATE_FAILED")

    part.set_boot = _boom
    u._part = part
    assert u.finish() is False
    assert not Path(u._pending_path()).exists()
    assert u.boot_check() is None


def test_the_new_image_running_reads_as_success(tmp_path):
    mod, u = _updater(tmp_path, running="ota_0")
    u._part = _FakePart("ota_1")
    u.finish()
    # ...reboot: the board comes up on the slot we asked for.
    u._running_label = lambda: "ota_1"
    kind, text = u.boot_check()
    assert kind == "ok"
    assert u.boot_verdict == (kind, text)


def test_the_old_image_running_reads_as_a_rollback(tmp_path):
    mod, u = _updater(tmp_path, running="ota_0")
    u._part = _FakePart("ota_1")
    u.finish()
    # ...reboot: the bootloader gave up on ota_1 and put ota_0 back.
    kind, _text = u.boot_check()
    assert kind == "rolled_back"


def test_an_ordinary_boot_has_no_verdict(tmp_path):
    mod, u = _updater(tmp_path)
    assert u.boot_check() is None
    assert u.boot_verdict is None


def test_a_torn_marker_is_not_a_verdict(tmp_path):
    # The marker is written to SD/flash on a board that may lose power mid-write.
    # Half a JSON object must read as "nothing to report", never as a crash on
    # the boot path.
    mod, u = _updater(tmp_path)
    Path(u._pending_path()).write_text('{"slot": "ota_', encoding="utf-8")
    assert u.boot_check() is None


def test_the_marker_survives_the_report_and_dies_at_the_confirm(tmp_path):
    """The interlock. Reading the verdict must NOT consume the marker.

    An image that boots, says "it worked", and then dies before confirming gets
    rolled back -- and if the marker had been eaten by the report, that second,
    worse failure would be the silent one.
    """
    mod, u = _updater(tmp_path, running="ota_0")
    u._part = _FakePart("ota_1")
    u.finish()
    u._running_label = lambda: "ota_1"
    assert u.boot_check()[0] == "ok"
    assert Path(u._pending_path()).exists(), "the report consumed the evidence"

    # It dies here -- no confirm -- and the bootloader reverts to ota_0.
    u2 = _updater(tmp_path, running="ota_0")[1]
    assert u2.boot_check()[0] == "rolled_back"

    # A run that DOES reach the confirm clears it, so the next ordinary boot
    # reports nothing.
    u2._running_label = lambda: "ota_1"
    for _ in range(mod.HEALTHY_LOOPS):
        u2.confirm_when_healthy(mod.HEALTHY_PAINTS)
    assert not Path(u2._pending_path()).exists()
    assert u2.boot_check() is None


def test_an_ordinary_boot_never_touches_the_card(tmp_path):
    """No marker -> the confirm must not open an SD session to delete nothing.

    On the T-Deck that card shares its bus with the panel, and nearly every boot
    is an ordinary one -- paying for a delete that can only fail is a cost with
    no case where it helps.
    """
    mod, u = _updater(tmp_path)
    touched = []
    u._with_sd = lambda fn: (touched.append(1), fn())[1]
    assert u.boot_check() is None
    touched.clear()
    for _ in range(mod.HEALTHY_LOOPS):
        u.confirm_when_healthy(1)
    assert u.confirmed is True
    assert touched == [], "the confirm opened an SD session for nothing"


def test_a_boot_that_did_see_a_marker_clears_it(tmp_path):
    mod, u = _updater(tmp_path, running="ota_0")
    u._part = _FakePart("ota_1")
    u.finish()
    assert u.boot_check()[0] == "rolled_back"
    for _ in range(mod.HEALTHY_LOOPS):
        u.confirm_when_healthy(1)
    assert not Path(u._pending_path()).exists()


def test_a_marker_write_failure_never_costs_the_update(tmp_path):
    # Best-effort by design: losing the verdict is a missing message; failing the
    # install because the message could not be written would be a real regression.
    mod, u = _updater(tmp_path)
    u._part = _FakePart("ota_1")
    u.update_dir = "/nonexistent-device-path/update"
    assert u.finish() is True


# -- the kid is told ---------------------------------------------------------

class _StubUpdater:
    """Just enough updater for the screen: the host build injects none."""

    def __init__(self, verdict=None):
        self.boot_verdict = verdict
        self.done = 0
        self.total = 0
        self.error = None

    def find_bin(self):
        return None

    def slot(self):
        return "ota_0"

    def version(self):
        return 2

    def version_label(self):
        return "v2"

    def channel(self):
        return "stable"

    def cancel(self):
        pass

    def download_cancel(self):
        pass


def _ws_with(tmp_path, verdict):
    from runtime import host_app
    ws = host_app.build_workstation(str(tmp_path / "carts"))
    ws.updater = _StubUpdater(verdict)
    return ws


def test_the_update_screen_leads_with_the_rollback_verdict(tmp_path):
    ws = _ws_with(tmp_path, ("rolled_back", "put v2 back"))
    ws.update_ui.open_update()
    assert ws.update_ui._upd_phase == "rolledback"
    assert ws.update_ui._upd_msg == "put v2 back"


def test_a_successful_update_is_stated_out_loud(tmp_path):
    ws = _ws_with(tmp_path, ("ok", "v2 -> v3"))
    ws.update_ui.open_update()
    assert ws.update_ui._upd_phase == "updated"


def test_the_online_entry_reports_it_too(tmp_path):
    # Whichever door the kid comes back through, the verdict is what they meet
    # first -- an online check that walked straight past it to "CHECKING..."
    # would lose the message for exactly the kid who did an online update.
    ws = _ws_with(tmp_path, ("rolled_back", "put v2 back"))
    ws.update_ui.open_update_online()
    assert ws.update_ui._upd_phase == "rolledback"


def test_the_verdict_interrupts_only_once(tmp_path):
    ws = _ws_with(tmp_path, ("ok", "v2 -> v3"))
    ws.update_ui.open_update()
    ws.update_ui.open_update()
    assert ws.update_ui._upd_phase != "updated"    # back to the ordinary scan


def test_the_verdict_screens_draw(tmp_path):
    # They are the two branches nothing else renders, and a typo in either would
    # otherwise surface as a crash on the boot after a failed update -- the worst
    # possible moment.
    from runtime import host_app
    for verdict in (("ok", "v2 -> v3"), ("rolled_back", "put v2 back")):
        ws = _ws_with(tmp_path, verdict)
        drv = host_app.ConsoleDriver(ws)
        ws.update_ui.open_update()
        for _ in range(3):
            drv.frame(1.0 / 30)
        assert ws.screen == "update"


def test_the_desktop_says_the_update_landed(tmp_path):
    """An update lands during a REBOOT, so nothing on screen ever asked for it.

    Without the machine volunteering the news, a successful update is
    indistinguishable from a slow reboot -- and a rolled-back one from the same.
    """
    ws = _ws_with(tmp_path, ("ok", "v2 -> v3"))
    assert ws.announce_update() is True
    assert ws.notice_active() is True
    title, sub, kind = ws._notice
    assert "UPDATED" in title
    assert "v2" in sub                    # the stub's version_label
    assert kind == "ok"


def test_the_desktop_says_when_the_update_was_undone(tmp_path):
    ws = _ws_with(tmp_path, ("rolled_back", "put v2 back"))
    ws.announce_update()
    assert ws._notice[2] == "warn"


def test_the_banner_leaves_the_verdict_for_the_update_screen(tmp_path):
    # Two surfaces, one fact: the banner is glanceable and vanishes, the screen
    # is there for the kid who missed it. Only the screen consumes it.
    ws = _ws_with(tmp_path, ("ok", "v2 -> v3"))
    ws.announce_update()
    assert ws.updater.boot_verdict is not None
    ws.update_ui.open_update()
    assert ws.update_ui._upd_phase == "updated"
    assert ws.updater.boot_verdict is None


def test_an_ordinary_boot_shows_no_banner(tmp_path):
    ws = _ws_with(tmp_path, None)
    assert ws.announce_update() is False
    assert ws.notice_active() is False


def test_the_banner_expires_on_its_own(tmp_path):
    # No input dismisses it, so the timer is the only way out -- a notice that
    # needed dismissing would be a modal in front of a kid who wanted to play.
    ws = _ws_with(tmp_path, ("ok", "v2 -> v3"))
    ws.notice("HELLO", "there", "ok", ms=100)
    assert ws.notice_active(now=ws._notice_until - 1) is True
    assert ws.notice_active(now=ws._notice_until + 1) is False
    assert ws._notice is None


def test_the_banner_joins_the_overlay_stack_and_keeps_the_screen_alive(tmp_path):
    # Two gates, and both matter: the layer has to be IN the stack to draw at
    # all, and _animating has to hold the redraw gate open or the banner would
    # be painted once and then sit there frozen past its own expiry.
    ws = _ws_with(tmp_path, ("ok", "v2 -> v3"))
    ws.announce_update()
    assert ws._notice_layer in ws._overlay_stack()
    assert ws._animating(1.0 / 30) is True
    ws._notice = None
    assert ws._notice_layer not in ws._overlay_stack()


def test_the_banner_draws_at_both_tiers(tmp_path):
    # 320x240 at 1x and a big windowed desktop: the banner is sized off `layout`
    # precisely so the second one doesn't render a 320-wide strip in a corner.
    from runtime import host_app
    for kw in ({}, {"sys_size": (1024, 600), "font_scale": 2, "windowed": True}):
        ws = host_app.build_workstation(str(tmp_path / "carts"), **kw)
        ws.updater = _StubUpdater(("ok", "v2 -> v3"))
        drv = host_app.ConsoleDriver(ws)
        ws.announce_update()
        for _ in range(3):
            drv.frame(1.0 / 30)
        assert ws.notice_active()


def test_dismissing_the_verdict_leaves_the_screen(tmp_path):
    ws = _ws_with(tmp_path, ("rolled_back", "put v2 back"))
    ws.update_ui.open_update()
    ws.update_ui._update_pointer(160, 120, True)   # a tap anywhere but the X
    assert ws.screen != "update"


# -- a channel with nothing on it yet ----------------------------------------

def test_a_missing_manifest_is_not_a_failed_update(tmp_path):
    """404 on the manifest means the channel has nothing for this board yet.

    That is the normal state of a channel before its first release -- and it is
    what a P4 on `stable` met, because the stable release predates per-board
    manifests entirely. Reporting it as "Update didn't finish ... http 404"
    blames the kid's console for a file missing from a server.
    """
    mod, u = _updater(tmp_path)
    u._wifi = object()
    u.wifi_online = lambda: True
    u._manifest_source = lambda ch=None: ("https://h/latest-p4.json", False)

    def _get(url, limit=8192):
        u.error = "http 404"
        return None

    u._http_get_text = _get
    assert u.check_online("stable") is None
    assert u.absent is True
    assert u.error is None, "a missing manifest is not an error"


def test_a_real_http_failure_stays_an_error(tmp_path):
    # 500/403 are not "nothing published" -- something is actually wrong, and
    # silently calling that "nothing new" would hide a broken release.
    mod, u = _updater(tmp_path)
    u._wifi = object()
    u.wifi_online = lambda: True
    u._manifest_source = lambda ch=None: ("https://h/latest-p4.json", False)

    def _get(url, limit=8192):
        u.error = "http 500"
        return None

    u._http_get_text = _get
    assert u.check_online("stable") is None
    assert u.absent is False
    assert u.error == "http 500"


def test_the_screen_says_nothing_new_rather_than_didnt_finish(tmp_path):
    ws = _ws_with(tmp_path, None)

    class _Absent(_StubUpdater):
        absent = False

        def check_online(self, ch=None):
            self.absent = True
            return None

    ws.updater = _Absent()
    ws.update_ui.open_update_online()
    ws.update_ui._pump_update(0.0)      # the one-frame CHECKING gate
    ws.update_ui._pump_update(0.0)      # the fetch
    assert ws.update_ui._upd_phase == "nopublish"


def test_the_nothing_new_screen_draws_and_dismisses(tmp_path):
    from runtime import host_app
    ws = _ws_with(tmp_path, None)
    drv = host_app.ConsoleDriver(ws)
    ws.update_ui.open_update_online()
    ws.update_ui._upd_phase = "nopublish"
    for _ in range(3):
        drv.frame(1.0 / 30)
    assert ws.screen == "update"
    ws.update_ui._update_pointer(160, 120, True)
    assert ws.screen != "update"


# -- which channel am I on? --------------------------------------------------

def test_the_default_channel_is_the_one_this_firmware_was_built_on(tmp_path):
    """A board that took a beta is RUNNING unstable.

    Defaulting it to stable meant every check compared the two, found them
    different, and offered the install -- a downgrade, on every check, forever,
    since installing it is the only thing that would ever make them agree.
    """
    ws = _ws_with(tmp_path, None)

    class _Beta(_StubUpdater):
        def channel(self):
            return "unstable"

    ws.updater = _Beta()
    assert ws._ota_channel() == "unstable"


def test_a_stable_build_still_defaults_to_stable(tmp_path):
    ws = _ws_with(tmp_path, None)
    assert ws._ota_channel() == "stable"


def test_a_deliberate_choice_beats_the_running_channel(tmp_path):
    # The setting is a departure from the channel you are on, so once made it
    # wins -- that is how a beta board asks to come back to stable.
    ws = _ws_with(tmp_path, None)

    class _Beta(_StubUpdater):
        def channel(self):
            return "unstable"

    ws.updater = _Beta()
    ws.system["ota_channel"] = "stable"
    assert ws._ota_channel() == "stable"


def test_no_updater_at_all_defaults_to_stable(tmp_path):
    from runtime import host_app
    ws = host_app.build_workstation(str(tmp_path / "carts"))
    assert ws.updater is None                 # the host injects none
    assert ws._ota_channel() == "stable"


# -- both boards are wired to it ---------------------------------------------

def test_both_boards_confirm_from_the_frame_loop_not_the_boot_path():
    """WHERE each half runs, asserted structurally rather than by grep.

    Both boards drive one shared implementation now (`runtime/device_boot.py`'s
    OtaHealth + FramePump, #161 Phase 4/5), which makes the old string match
    both weaker and misleading: a literal that lives in a third file says
    nothing about whether a board reached it, and the whole claim here is about
    PLACEMENT. So this walks each `run_desktop` and asserts the boot verdict is
    read OUTSIDE the frame loop while the confirm is pumped INSIDE it -- the
    distinction #56 was: made where the desktop is merely CONSTRUCTED, the
    confirm certifies an image that has never drawn a pixel.

    These two files are never imported by the host (they import esp32/machine),
    so the source is all CI can reach -- same house rule as the rest of the
    frozen-module suite.
    """
    import ast

    for mod_path in (TDECK / "moy_runtime.py", P4 / "moy_runtime.py"):
        src = mod_path.read_text(encoding="utf-8")
        fn = None
        for node in ast.walk(ast.parse(src, filename=str(mod_path))):
            if isinstance(node, ast.FunctionDef) and node.name == "run_desktop":
                fn = node
        assert fn is not None, mod_path

        seen = {}

        def walk(node, in_loop):
            for child in ast.iter_child_nodes(node):
                loop = in_loop or isinstance(node, (ast.While, ast.For))
                if isinstance(child, ast.Call):
                    name = (child.func.attr
                            if isinstance(child.func, ast.Attribute)
                            else getattr(child.func, "id", None))
                    if name:
                        seen.setdefault(name, set()).add(loop)
                walk(child, loop)

        walk(fn, False)
        assert seen.get("OtaHealth") == {False}, (
            "%s: the OTA health reporter must be built on the boot path" % mod_path)
        assert seen.get("boot_check") == {False}, (
            "%s: the boot verdict is read once, before the loop" % mod_path)
        # #202 Phase B: the frame loop itself is SHARED (device_boot.FrameLoop
        # calls pump.tail every frame -- asserted against the spine below), so
        # the per-board placement claim becomes: run_desktop hands the pump to
        # a FrameLoop and runs it, and does NOT drive pump.tail beside it.
        assert "FrameLoop" in seen, (
            "%s: run_desktop no longer constructs the shared frame loop"
            % mod_path)
        assert seen.get("run") is not None, mod_path
        assert seen.get("tail") is None, (
            "%s: pump.tail driven beside the shared loop -- two cadences"
            % mod_path)
        # The old unconditional confirm at desktop-construction time is gone.
        assert "ws.updater.mark_valid()" not in src, mod_path

    # And the shared half really does confirm on painted frames, not on boot.
    spine = (ROOT / "runtime" / "device_boot.py").read_text(encoding="utf-8")
    assert 'confirm_when_healthy(getattr(self.ws, "_frames_drawn", 0))' in spine
    # ...and the shared FrameLoop is what pumps it, after the ws phase.
    step = spine[spine.index("def step(self):"):]
    assert step.index("ws.frame(dt)") < step.index("self.pump.tail(ws)")
    tree = ast.parse(spine)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "boot_check":
            assert "confirm_when_healthy" not in ast.dump(node), (
                "device_boot.OtaHealth.boot_check must NOT confirm -- reaching "
                "the boot path proves only that the desktop was constructed")
