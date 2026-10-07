"""The updater on the host: `device/moy_ota.py`'s face over the kernel's
updater and client (native/moy_net/moy_ota.c), run through
runtime/net_binding.py against a scripted HTTP server on 127.0.0.1
(tests/ota_http.py) and the host's slot (moy_net_port.c: memory the tests read
back, checked at close by its first byte).

What has its own net and is not repeated here: the signature and its policy
(`tests/test_ota_signing.py`), the confirm and the pending marker's lifetime
(`tests/test_ota_health.py`).

WHAT STAYS DEVICE-ONLY: the flash write and its erase, `esp_image_verify`'s
walk of the segment table, the bootloader and its rollback, TLS (the host's
connection is plain TCP and refuses https), the C6's slave OTA, and the store
gate on a board whose card shares the panel's bus. The board suites and the
on-glass gates of docs/kernel_survival_2026-10.md section 6.9 own those.
"""

import hashlib
import json
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

from ota_http import Server  # noqa: E402
from test_ota_signing import TEST_KEYS, sign_with_test_key  # noqa: E402
import moy_ota_health  # noqa: E402  (the health half: its thresholds and marker)
from runtime import net_binding as nb  # noqa: E402

nb.install()


def _fresh():
    """A private copy of the module per test: several tests write its globals."""
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "moy_ota_unit", ROOT / "device" / "moy_ota.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def app_image(payload=0, magic=0xE9):
    """Bytes the host's slot takes for an app image: the magic byte first."""
    return bytes([magic]) + bytes((i * 7 + 3) & 0xFF for i in range(payload))


class _Esp32:
    """`esp32`'s Partition, for the health half: the running slot's label and
    the rollback confirm."""

    def __init__(self, running="ota_0"):
        self.marked = 0
        self.mark_error = None
        esp = self

        class _Running:
            def info(self):
                return (0, 0x10, 0x20000, 4 << 20, running, False)

        class Partition:
            RUNNING = "RUNNING"

            def __new__(cls, which):
                return _Running()

            @staticmethod
            def mark_app_valid_cancel_rollback():
                if esp.mark_error is not None:
                    raise esp.mark_error
                esp.marked += 1

        self.Partition = Partition


class _Card:
    """The injected `with_sd`: counts sessions, can fail."""

    def __init__(self):
        self.sessions = 0
        self.fail = None

    def __call__(self, fn):
        if self.fail is not None:
            raise self.fail
        self.sessions += 1
        return fn()


def _install_esp32(monkeypatch, esp):
    mod = types.ModuleType("esp32")
    mod.Partition = esp.Partition
    monkeypatch.setitem(sys.modules, "esp32", mod)
    return mod


def _install_machine(monkeypatch):
    mod = types.ModuleType("machine")
    mod.resets = []
    mod.reset = lambda: mod.resets.append(1)
    monkeypatch.setitem(sys.modules, "machine", mod)
    return mod


def _fake_time(ticks=False, sleep_ms=True):
    """`time`, fast. Unknown names fall through to the real module so anything
    else importing `time` while this is installed still works."""
    import time as real_time

    m = types.ModuleType("time")
    m.slept = []
    if ticks:
        m.now = 1000
        m.ticks_ms = lambda: m.now
        m.ticks_diff = lambda a, b: a - b
    if sleep_ms:
        def _sleep_ms(ms):
            m.slept.append(ms)
            if ticks:
                m.now += ms
        m.sleep_ms = _sleep_ms

    def _sleep(sec):
        m.slept.append(int(sec * 1000))
    m.sleep = _sleep
    hidden = set()
    if not ticks:
        hidden |= {"ticks_ms", "ticks_diff"}
    if not sleep_ms:
        hidden.add("sleep_ms")

    def __getattr__(name):
        if name in hidden:
            raise AttributeError(name)
        return getattr(real_time, name)
    m.__getattr__ = __getattr__
    return m


@pytest.fixture
def board(tmp_path, monkeypatch):
    """An updater over the kernel's updater, with a fake esp32 for the health
    half, a real staging directory and a card that counts its sessions."""
    nb.ota_cancel()
    nb._slot_reset()
    nb._trust(None)
    mod = _fresh()
    card = _Card()
    esp = _Esp32()
    _install_esp32(monkeypatch, esp)
    d = tmp_path / "update"
    d.mkdir()
    u = mod.OtaUpdater(card, update_dir=str(d))
    yield types.SimpleNamespace(mod=mod, u=u, esp=esp, card=card, dir=d)
    nb.ota_cancel()
    nb.net_vm_stop()
    nb._trust(None)
    nb._c6(enabled=False)


def _staged(board, payload=9000, name="firmware.bin", magic=0xE9):
    blob = app_image(payload, magic)
    p = board.dir / name
    p.write_bytes(blob)
    return str(p), blob


def _run_install(u):
    """update_ui's own drive: `more = u.step()` until it says nothing is left."""
    steps = 0
    while u.step():
        steps += 1
        assert steps < 10000, "the install never terminated"
    return steps


def _manifest(m, blob, url, **kw):
    out = dict(board=m.BOARD, channel="stable", version=m.FIRMWARE_VERSION + 1,
               size=len(blob), sha256=hashlib.sha256(blob).hexdigest(), url=url)
    out.update(kw)
    return out


# == a copied image ==============================================================

def test_step_says_true_while_more_remains_and_false_at_the_end(board):
    path, blob = _staged(board, payload=100000)
    assert board.u.begin(path) == len(blob)
    assert board.u.step() is True
    more = 1 + _run_install(board.u)
    assert more == len(blob) // board.mod.INSTALL_CHUNK
    assert board.u.staged_in_slot() is True
    assert nb._slot()[0] == blob


def test_an_image_that_is_an_exact_multiple_of_the_chunk(board):
    path, blob = _staged(board, payload=board.mod.INSTALL_CHUNK * 2 - 1)
    board.u.begin(path)
    _run_install(board.u)
    assert board.u.staged_in_slot() is True
    assert nb._slot()[0] == blob


def test_the_copied_image_installs_by_activating_and_records_the_slot(board):
    path, blob = _staged(board)
    board.u.begin(path)
    _run_install(board.u)
    assert board.u.finish() is True
    assert nb._slot()[1] == "ota_1"
    rec = json.loads(Path(board.u._pending_path()).read_text(encoding="utf-8"))
    assert rec["slot"] == "ota_1"
    assert board.u.staged_in_slot() is False, "activated twice is not a state"


def test_each_slice_is_read_in_its_own_card_session(board):
    path, _blob = _staged(board, payload=board.mod.INSTALL_CHUNK * 3)
    board.u.begin(path)
    before = board.card.sessions
    _run_install(board.u)
    assert board.card.sessions - before >= 4


def test_a_step_before_begin_reports_nothing_left_to_do(board):
    assert board.u.step() is False
    assert board.u.error is None


def test_a_failed_read_cancels_the_install_and_names_the_error(board):
    path, _blob = _staged(board)
    board.u.begin(path)
    board.card.fail = OSError(5)
    assert board.u.step() is False
    assert board.u.error == board.mod.NO_WRITE
    assert board.u.staged_in_slot() is False
    assert board.u.finish() is False


def test_begin_refuses_an_image_bigger_than_the_slot(board):
    nb._slot_reset(cap=64 * 1024)
    path, _blob = _staged(board, payload=200 * 1024)
    with pytest.raises(ValueError, match=r"image 200K > slot 64K"):
        board.u.begin(path)
    assert board.u._f is None


def test_begin_refuses_an_empty_image(board):
    p = board.dir / "empty.bin"
    p.write_bytes(b"")
    with pytest.raises(ValueError, match="empty image"):
        board.u.begin(str(p))


def test_a_file_that_is_not_an_app_image_is_refused_at_its_first_byte(board):
    path, _blob = _staged(board, magic=0x00)
    board.u.begin(path)
    assert board.u.step() is False
    assert board.u.error == "not an app image"
    assert board.u.finish() is False
    assert nb._slot()[1] == ""


def test_cancel_releases_the_file_and_leaves_nothing_to_activate(board):
    path, _blob = _staged(board, payload=100000)
    board.u.begin(path)
    board.u.step()
    f = board.u._f
    board.u.cancel()
    assert board.u._f is None and f.closed
    assert board.u.finish() is False
    assert nb._slot()[1] == ""


def test_finish_with_nothing_verified_reports_failure(board):
    assert board.u.finish() is False
    assert board.u.error == "nothing verified to install"


def test_find_bin_picks_the_biggest_image_in_the_staging_directory(board):
    """The Phase-2 path: a kid drags a .bin onto the card. Biggest wins, which
    is how a complete image beats the half-copied one beside it."""
    (board.dir / "small.bin").write_bytes(b"\xe9" + b"\x00" * 100)
    (board.dir / "big.bin").write_bytes(b"\xe9" + b"\x00" * 5000)
    assert board.u.find_bin() == (str(board.dir / "big.bin"), 5001)


def test_find_bin_ignores_everything_that_is_not_an_image(board):
    """`ota.json` and `pending.json` live in this directory, and so does
    whatever else the owner put on the card."""
    (board.dir / "ota.json").write_text("{}", encoding="utf-8")
    (board.dir / "notes.txt").write_bytes(b"x" * 99999)
    (board.dir / "fw.BIN").write_bytes(b"\xe9" + b"\x00" * 10)
    assert board.u.find_bin() == (str(board.dir / "fw.BIN"), 11)


def test_find_bin_on_an_empty_or_missing_directory(board, tmp_path):
    """A board that has never been updated has no staging directory, which is
    the ORDINARY state -- so it must read as "nothing to install", never as an
    error the update screen puts in front of the kid."""
    assert board.u.find_bin() is None
    board.u.update_dir = str(tmp_path / "never-made")
    assert board.u.find_bin() is None
    assert board.u.error is None


def test_find_bin_skips_an_entry_it_cannot_stat(board):
    """A torn directory entry. One unreadable name must not hide the good
    image beside it, and must not raise on the path a kid reaches by tapping
    UPDATE FW.

    (A DIRECTORY named `*.bin` is a different case and is NOT skipped -- it
    stats fine, so it can win on size and be handed to `begin`, which fails at
    `open()` and surfaces as an error on the update screen. Recorded rather
    than asserted: the behaviour is confusing, not dangerous.)"""
    (board.dir / "gone.bin").symlink_to(board.dir / "nothing-here")
    (board.dir / "real.bin").write_bytes(b"\xe9" + b"\x00" * 40)
    assert board.u.find_bin() == (str(board.dir / "real.bin"), 41)


def test_find_bin_reports_a_card_that_will_not_mount(board):
    board.card.fail = OSError(5)
    assert board.u.find_bin() is None
    assert board.u.error == "OSError 5"


# -- teardown verbs ------------------------------------------------------------


def test_reset_reboots_the_board(board, monkeypatch):
    machine = _install_machine(monkeypatch)
    board.u.reset()
    assert machine.resets == [1]


# == capability and identity ===================================================


# == capability and identity ===================================================

def test_an_ota_build_names_the_slot_it_is_running(board):
    assert board.u.available() is True
    assert board.u.slot() == "ota_0"


def test_a_legacy_factory_build_has_nowhere_to_write(board, monkeypatch):
    """One app partition means no second slot, so the console hides the row
    rather than offering an update that would overwrite the running image."""
    esp = _Esp32(running="factory")
    _install_esp32(monkeypatch, esp)
    assert board.u.available() is False
    assert board.u.slot() == "factory"


def test_a_board_with_no_esp32_declines_instead_of_raising(board, monkeypatch):
    """The host imports this module (these tests do). Every esp32 touch is
    swallowed, so the shared console simply never shows the row."""
    monkeypatch.setitem(sys.modules, "esp32", None)
    assert board.u.available() is False
    assert board.u.slot() == "?"
    assert board.u.online_available() is False


def test_mark_valid_cancels_the_pending_rollback(board):
    assert board.u.mark_valid() is True
    assert board.esp.marked == 1


def test_mark_valid_swallows_a_board_that_cannot(board):
    """Already-valid, or not an OTA build at all. Neither is a failure worth
    stopping a boot for."""
    board.esp.mark_error = OSError("ESP_ERR_INVALID_STATE")
    assert board.u.mark_valid() is False


def test_the_online_row_needs_both_a_slot_and_a_radio(board):
    assert board.u.online_available() is False        # no wifi injected
    board.u.set_wifi(object())
    assert board.u.online_available() is True


def test_set_wifi_keeps_the_existing_autoconnect_unless_told(board):
    """`go_online=None` is "leave it alone", not "clear it" -- a board that
    re-injects its radio must not silently lose the saved-credentials dial."""
    dial = lambda: None                                # noqa: E731
    board.u.set_wifi(object(), dial)
    board.u.set_wifi(object())
    assert board.u._go_online is dial
    board.u.set_wifi(object(), lambda: "other")
    assert board.u._go_online is not dial


def test_the_version_label_prefers_the_stamped_one(board):
    m = board.mod
    assert board.u.version_label() == m.FIRMWARE_NAME
    m.FIRMWARE_LABEL = "beta 2026-08-29"
    assert board.u.version_label() == "beta 2026-08-29"
    m.FIRMWARE_LABEL = None
    m.FIRMWARE_NAME = None
    assert board.u.version_label() == "v%d" % m.FIRMWARE_VERSION
    assert board.u.version() == m.FIRMWARE_VERSION


def test_the_build_stamp_is_what_gives_an_image_its_identity(monkeypatch):
    """`build.sh` writes a gitignored `_ota_build` so the channel is a BUILD
    choice, not a per-branch source edit. Nothing else in the suite executes
    that import -- conftest neutralises it process-wide, precisely so a machine
    that has run build.sh does not read differently from one that has not.

    It matters because all four fields steer the update: the channel decides
    what a check is compared against, the board decides which manifest is even
    fetched (an app-partition image is Xtensa or RISC-V), the version orders
    two betas, and the label is the only string a human reads.
    """
    stamp = types.ModuleType("_ota_build")
    stamp.CHANNEL = "unstable"
    stamp.VERSION = 1785659788                      # a beta stamps a build epoch
    stamp.LABEL = "beta 2026-08-29"
    stamp.BOARD = "guition_s3"
    monkeypatch.setitem(sys.modules, "_ota_build", stamp)
    m = _fresh()
    u = m.OtaUpdater(lambda fn: fn())
    assert (u.channel(), u.version()) == ("unstable", 1785659788)
    assert u.version_label() == "beta 2026-08-29"
    assert m.BOARD == "guition_s3"
    assert m.default_manifest_url("unstable").endswith("/latest-guition_s3.json")


def test_an_empty_build_stamp_keeps_the_committed_identity(monkeypatch):
    """A half-written stamp must not blank the identity out: an image whose
    channel or board went missing would fetch the wrong manifest, or none."""
    stamp = types.ModuleType("_ota_build")
    stamp.CHANNEL = ""
    stamp.LABEL = ""
    stamp.BOARD = ""
    monkeypatch.setitem(sys.modules, "_ota_build", stamp)
    m = _fresh()
    assert (m.FIRMWARE_CHANNEL, m.BOARD) == ("stable", "tdeck")
    assert m.OtaUpdater(lambda fn: fn()).version_label() == m.FIRMWARE_NAME


def test_a_manifest_with_a_junk_version_is_not_newer(board):
    """Whatever arrives off the wire lands in int(). A crash here would be a
    denied update, and a silently-huge one would be a forced downgrade."""
    u = board.u
    assert u.offers({"version": "not a number", "channel": "stable"}) is False
    assert u.offers({"version": None, "channel": "stable"}) is False
    assert u.offers({}) is False                       # channel defaults to ours
    assert u.offers({"version": board.mod.FIRMWARE_VERSION + 1}) is True



# == how long "healthy" takes ==================================================
#
# `tests/test_ota_health.py` owns the confirm's SHAPE, and does it entirely
# through the two constants -- which is correct for the shape and leaves the
# VALUES unpinned: HEALTHY_LOOPS could be 1 and every one of those tests would
# still pass. These two are about the numbers.

def test_the_loop_threshold_is_a_real_wait_not_a_formality(board):
    """The confirm cancels the rollback, so it is the last moment the board can
    be saved from an image that comes up and then dies. A handful of iterations
    would confirm inside the boot itself; the constant is ~2-4s of frames on
    either board -- long enough that an ordinary crash lands inside it, short
    enough that nobody power-cycles first."""
    assert moy_ota_health.HEALTHY_LOOPS >= 60
    for _ in range(30):
        assert board.u.confirm_when_healthy(5) is False
    assert board.esp.marked == 0


def test_the_paint_threshold_is_exactly_one(board):
    """MEASURED on the P4: an idle desktop had drawn ONE frame six seconds
    after boot, because the console repaints only when something changes. Any
    higher threshold rolls back every update that lands while nobody is poking
    at the console -- and one painted frame is already the whole of what #56
    was missing."""
    assert moy_ota_health.HEALTHY_PAINTS == 1
    fired = [board.u.confirm_when_healthy(1)
             for _ in range(moy_ota_health.HEALTHY_LOOPS)]
    assert fired.count(True) == 1
    assert board.esp.marked == 1


# == where the manifest comes from =============================================



def _card_cfg(board, cfg):
    (board.dir / "ota.json").write_text(json.dumps(cfg), encoding="utf-8")


def test_a_leftover_ota_json_reroutes_every_check(board):
    """The card WINS, deliberately -- it is how a classroom points a board at a
    LAN mirror. It is also why a forgotten ota.json makes a board that looks
    online silently never see the real channel again."""
    _card_cfg(board, {"channels": {"stable": "http://192.168.1.9:8000/l.json"}})
    url, from_card = board.u._manifest_source()
    assert (url, from_card) == ("http://192.168.1.9:8000/l.json", True)
    assert board.u.manifest_url() == url
    assert "github" not in url


def test_the_card_answers_for_the_channel_that_was_asked_for(board):
    _card_cfg(board, {"channels": {"stable": "http://h/s.json",
                                   "unstable": "http://h/u.json"}})
    assert board.u.manifest_url("unstable") == "http://h/u.json"
    assert board.u.manifest_url("stable") == "http://h/s.json"


def test_a_card_missing_this_channel_falls_back_within_itself(board):
    """A hand-written ota.json naming one host: asking for a channel it does
    not list must reach that host, not jump back to GitHub behind the owner."""
    _card_cfg(board, {"channels": {"lan": "http://h/only.json"}})
    url, from_card = board.u._manifest_source("unstable")
    assert (url, from_card) == ("http://h/only.json", True)


def test_the_running_channel_wins_over_stable_on_the_card(board):
    _card_cfg(board, {"channels": {"stable": "http://h/s.json",
                                   "unstable": "http://h/u.json"}})
    board.mod.FIRMWARE_CHANNEL = "unstable"
    assert board.u._manifest_source()[0] == "http://h/u.json"


def test_a_legacy_single_url_card_still_works(board):
    """The Phase-3 shape, from before there were two channels. A board in a
    drawer with that file on its card must not stop updating."""
    _card_cfg(board, {"manifest_url": "http://h/latest.json"})
    assert board.u._manifest_source() == ("http://h/latest.json", True)


def test_no_card_entry_means_the_baked_channel_url(board):
    url, from_card = board.u._manifest_source("unstable")
    assert from_card is False
    assert url == board.mod.default_manifest_url("unstable")
    assert url.endswith("/latest-tdeck.json")


@pytest.mark.parametrize("bad", ["not json at all", "{}", '{"channels": {}}'])
def test_an_unusable_card_file_falls_back_to_the_baked_url(board, bad):
    (board.dir / "ota.json").write_text(bad, encoding="utf-8")
    url, from_card = board.u._manifest_source()
    assert from_card is False and "github.com" in url


def test_a_card_that_cannot_be_read_at_all_falls_back(board):
    """No SD, or a mount that failed. The baked url is what makes a board
    straight off the flasher updatable with no host of the owner's."""
    board.card.fail = OSError(5)
    url, from_card = board.u._manifest_source()
    assert from_card is False and "github.com" in url


def test_a_board_with_no_channel_of_that_name_has_no_url(board):
    assert board.mod.default_manifest_url("nonesuch") is None
    assert board.u.manifest_url("nonesuch") is None


# == getting online ============================================================


class _Wifi:
    """The injected radio service. `status()[0]` is the truthy "connected"."""

    def __init__(self, up_after=0, error=None):
        self.up_after = up_after
        self.error = error
        self.asked = 0

    def status(self):
        self.asked += 1
        if self.error is not None:
            raise self.error
        return (self.asked > self.up_after, "192.168.1.5")


def test_a_network_that_comes_up_late_is_not_reported_offline(board, monkeypatch):
    """MEASURED on the P4 (2026-08-02, saved network, cold reset): connect()
    polls for 4s and gives up, and the link came up 1.5s AFTER it did. Without
    this wait a perfectly good network reads as "wifi offline"."""
    monkeypatch.setitem(sys.modules, "time", _fake_time())
    dialled = []
    board.u.set_wifi(_Wifi(up_after=6), lambda: dialled.append(1))
    assert board.u.ensure_online() is True
    assert dialled == [1]
    assert board.u.wifi_online() is True


def test_the_wait_is_bounded_and_gives_up(board, monkeypatch):
    """A console cannot sit behind a CHECKING screen forever."""
    fake = _fake_time()
    monkeypatch.setitem(sys.modules, "time", fake)
    board.u.set_wifi(_Wifi(up_after=10 ** 9), lambda: None)
    assert board.u.ensure_online() is False
    assert sum(fake.slept) <= board.mod.ONLINE_WAIT_MS


def test_an_already_connected_board_neither_dials_nor_waits(board, monkeypatch):
    fake = _fake_time()
    monkeypatch.setitem(sys.modules, "time", fake)
    dialled = []
    board.u.set_wifi(_Wifi(up_after=0), lambda: dialled.append(1))
    assert board.u.ensure_online() is True
    assert dialled == [] and fake.slept == []


def test_an_autoconnect_that_throws_still_gets_its_wait(board, monkeypatch):
    """The dial is best-effort. A radio that raises on connect may still be
    associating, and the wait is what finds out."""
    monkeypatch.setitem(sys.modules, "time", _fake_time())

    def _boom():
        raise OSError("ESP_ERR_WIFI_CONN")

    board.u.set_wifi(_Wifi(up_after=4), _boom)
    assert board.u.ensure_online() is True


def test_a_board_with_no_autoconnect_hook_still_waits(board, monkeypatch):
    monkeypatch.setitem(sys.modules, "time", _fake_time())
    board.u.set_wifi(_Wifi(up_after=3))
    assert board.u.ensure_online() is True


def test_a_micropython_without_sleep_ms_uses_plain_sleep(board, monkeypatch):
    """The host runs this module too, and CPython has no `time.sleep_ms`."""
    fake = _fake_time(sleep_ms=False)
    monkeypatch.setitem(sys.modules, "time", fake)
    board.u.set_wifi(_Wifi(up_after=3), lambda: None)
    assert board.u.ensure_online() is True
    assert fake.slept[:1] == [250]


def test_a_radio_that_throws_reads_as_offline(board):
    board.u.set_wifi(_Wifi(error=OSError("no netif")))
    assert board.u.wifi_online() is False


def test_no_radio_at_all_reads_as_offline(board):
    assert board.u.wifi_online() is False


# == the manifest check ========================================================

def test_check_online_reports_a_channel_with_no_url(board):
    board.u._manifest_source = lambda channel=None: (None, False)
    assert board.u.check_online() is None
    assert board.u.error == "no manifest url"


def test_check_online_reports_an_offline_board(board, monkeypatch):
    monkeypatch.setitem(sys.modules, "time", _fake_time())
    board.u.set_wifi(_Wifi(up_after=10 ** 9), lambda: None)
    assert board.u.check_online() is None
    assert board.u.error == "wifi offline"


# == the manifest check, over the kernel's client =================================

def _route_manifest(srv, path, manifest):
    srv.routes[path] = (200, {}, json.dumps(manifest).encode())


def test_check_online_fetches_and_parses_through_the_kernel(board):
    board.u.ensure_online = lambda: True
    with Server() as srv:
        m = {"version": 99, "channel": "stable", "board": board.mod.BOARD}
        _route_manifest(srv, "/l.json", m)
        board.u._manifest_source = lambda channel=None: (srv.url("/l.json"), True)
        assert board.u.check_online() == m
        assert board.u.from_card is True
        assert srv.seen[0][1]["User-Agent"] == "moybyte-ota"


@pytest.mark.parametrize("status, absent, error", [
    (404, True, None), (410, True, None), (500, False, "http 500"),
    (403, False, "http 403")])
def test_a_missing_manifest_is_absence_and_any_other_status_an_error(
        board, status, absent, error):
    board.u.ensure_online = lambda: True
    with Server({"/l.json": (status, {}, b"no")}) as srv:
        board.u._manifest_source = lambda channel=None: (srv.url("/l.json"), True)
        assert board.u.check_online() is None
    assert board.u.absent is absent
    assert board.u.error == error


def test_a_manifest_that_is_not_json_is_an_error_not_a_crash(board):
    board.u.ensure_online = lambda: True
    with Server({"/l.json": (200, {}, b"<html>404 not found</html>")}) as srv:
        board.u._manifest_source = lambda channel=None: (srv.url("/l.json"), True)
        assert board.u.check_online() is None
    assert board.u.error == "bad manifest"


def test_an_oversized_manifest_stops_at_the_cap(board):
    """MOY_OTA_MANIFEST_MAX: a manifest is a few hundred bytes, and a body past
    8 KB is not one -- its cut-off JSON is refused, never buffered whole."""
    big = json.dumps({"version": 1, "pad": "x" * 20000}).encode()
    board.u.ensure_online = lambda: True
    with Server({"/l.json": (200, {}, big)}) as srv:
        board.u._manifest_source = lambda channel=None: (srv.url("/l.json"), True)
        assert board.u.check_online() is None
    assert board.u.error == "bad manifest"


def test_an_unreachable_host_is_named(board):
    board.u.ensure_online = lambda: True
    board.u._manifest_source = lambda channel=None: ("http://127.0.0.1:1/l.json", True)
    assert board.u.check_online() is None
    assert board.u.error.startswith("net error")


def test_https_is_the_boards_and_the_host_says_so(board):
    board.u.ensure_online = lambda: True
    board.u._manifest_source = lambda channel=None: ("https://127.0.0.1:1/l.json", True)
    assert board.u.check_online() is None
    assert board.u.error == "net error %d" % 93          # EPROTONOSUPPORT


# == the client ====================================================================

CSP = ("default-src 'none'; " * 180).strip()


def _get(url, agent="t"):
    h, status, clen = nb.http_open(url, agent)
    out = bytearray()
    buf = bytearray(777)
    try:
        while True:
            k = nb.http_readinto(h, buf)
            if not k:
                break
            out += buf[:k]
    finally:
        nb.http_close(h)
    return status, clen, bytes(out)


def test_a_redirect_is_followed_to_the_body():
    with Server() as srv:
        srv.routes["/a"] = (302, {"Location": srv.url("/b")}, b"")
        srv.routes["/b"] = (200, {}, b"the body")
        assert _get(srv.url("/a")) == (200, 8, b"the body")
        assert [p for p, _h in srv.seen] == ["/a", "/b"]


def test_a_location_after_githubs_huge_csp_is_still_found():
    """GitHub's release redirect measured 5147 bytes of head, 3626 of them one
    Content-Security-Policy, with the Location after it in some orderings."""
    with Server() as srv:
        srv.routes["/a"] = (302, {"Content-Security-Policy": CSP,
                                  "Location": srv.url("/b")}, b"")
        srv.routes["/b"] = (200, {"Content-Security-Policy": CSP}, b"ok")
        assert _get(srv.url("/a"))[2] == b"ok"


def test_a_relative_location_resolves_against_the_current_host():
    with Server() as srv:
        srv.routes["/a"] = (301, {"Location": "/b"}, b"")
        srv.routes["/b"] = (200, {}, b"rel")
        assert _get(srv.url("/a"))[2] == b"rel"


def test_a_redirect_loop_gives_up_rather_than_spinning():
    with Server() as srv:
        srv.routes["/a"] = (302, {"Location": "/a"}, b"")
        status, _clen, _body = _get(srv.url("/a"))
        assert status == 302
        assert len(srv.seen) == 5                 # the first and MOY_HTTPC_HOPS more


def test_a_head_past_the_cap_is_refused_not_buffered():
    with Server() as srv:
        srv.routes["/a"] = (200, {"X-Big": "y" * 20000}, b"z")
        with pytest.raises(OSError):
            nb.http_open(srv.url("/a"), "t")


def test_the_body_read_with_the_head_comes_first():
    """A big first read carries the head and the start of the body together;
    those body bytes are the handle's, not lost."""
    body = bytes(range(256)) * 300
    with Server({"/a": (200, {}, body)}) as srv:
        assert _get(srv.url("/a"))[2] == body


@pytest.mark.parametrize("url", ["ftp://h/x", "h/x", "http://", "http://h:0/",
                                 "http://h:70000/", "http://h:8x/"])
def test_a_url_that_is_not_one_is_refused(url):
    with pytest.raises(OSError) as e:
        nb.http_open(url, "t")
    assert e.value.errno == 22


def test_a_closed_handle_reads_as_a_bad_descriptor():
    with Server({"/a": (200, {}, b"x")}) as srv:
        h, _s, _c = nb.http_open(srv.url("/a"), "t")
        nb.http_close(h)
        with pytest.raises(OSError) as e:
            nb.http_readinto(h, bytearray(4))
        assert e.value.errno == 9


def test_the_vm_stop_closes_what_the_vm_left_open():
    """The kernel's teardown (moy_net_vm_stop): a handle the old VM held is
    dead to the next one, and the slots are free again."""
    with Server({"/a": (200, {}, b"x" * 10)}) as srv:
        handles = [nb.http_open(srv.url("/a"), "t")[0] for _ in range(2)]
        with pytest.raises(OSError):
            nb.http_open(srv.url("/a"), "t")      # both slots held
        nb.net_vm_stop()
        for h in handles:
            with pytest.raises(OSError):
                nb.http_readinto(h, bytearray(4))
        h, _s, _c = nb.http_open(srv.url("/a"), "t")
        nb.http_close(h)


# == the download, into the slot ==================================================

def _download(board, blob, manifest=None, serve=None, sizes=None):
    m = board.mod
    with Server() as srv:
        srv.routes["/fw.bin"] = (200, {}, blob if serve is None else serve)
        man = manifest or _manifest(m, blob, srv.url("/fw.bin"))
        man["url"] = srv.url("/fw.bin")
        board.u.begin_download(man)
        steps = 0
        while board.u.download_step():
            steps += 1
            assert steps < 10000
        return board.u.download_finish(), steps


def test_a_slot_download_reassembles_the_image_byte_for_byte(board):
    blob = app_image(100000)
    path, steps = _download(board, blob)
    assert path == board.mod.SLOT_STAGED
    assert nb._slot()[0] == blob
    assert steps == (len(blob) + board.mod.DL_CHUNK - 1) // board.mod.DL_CHUNK
    assert (board.u.dl_done, board.u.dl_total) == (len(blob), len(blob))
    assert board.u.staged_in_slot() is True
    assert list(board.dir.iterdir()) == [], "a slot download wrote a file"


def test_a_slot_download_needs_no_second_pass_to_install(board):
    blob = app_image(5000)
    _download(board, blob)
    assert board.u.finish() is True
    assert nb._slot()[1] == "ota_1"


def test_a_short_download_is_refused(board):
    blob = app_image(5000)
    path, _ = _download(board, blob, manifest=_manifest(board.mod, blob + b"x" * 10, ""))
    assert path is None
    assert board.u.error == "size 5001/5011"
    assert board.u.finish() is False


def test_a_corrupted_download_is_refused_by_the_signed_hash(board):
    blob = app_image(5000)
    bad = bytearray(blob)
    bad[3000] ^= 0xFF
    path, _ = _download(board, blob, serve=bytes(bad))
    assert path is None
    assert board.u.error == "sha256 mismatch"
    assert board.u.staged_in_slot() is False
    assert board.u.finish() is False
    assert nb._slot()[1] == ""


def test_a_manifest_with_no_hash_is_accepted_on_size_alone(board):
    blob = app_image(3000)
    man = _manifest(board.mod, blob, "")
    man["sha256"] = ""
    assert _download(board, blob, manifest=man)[0] == board.mod.SLOT_STAGED


def test_the_size_falls_back_to_content_length(board):
    blob = app_image(3000)
    man = _manifest(board.mod, blob, "")
    man["size"] = 0
    assert _download(board, blob, manifest=man)[0] == board.mod.SLOT_STAGED
    assert board.u.dl_total == len(blob)


def test_a_manifest_with_no_url_cannot_start_a_download(board):
    with pytest.raises(ValueError, match="manifest has no url"):
        board.u.begin_download({"size": 1})


def test_a_refused_download_names_the_status(board):
    with Server({"/x": (403, {}, b"")}) as srv:
        with pytest.raises(ValueError, match="http 403"):
            board.u.begin_download({"url": srv.url("/x"), "size": 1})
    assert board.u.error == "http 403"


def test_an_image_too_big_for_the_slot_is_refused_before_the_transfer(board):
    nb._slot_reset(cap=8192)
    with Server({"/x": (200, {}, app_image(20000))}) as srv:
        with pytest.raises(ValueError, match=r"image 19K > slot 8K"):
            board.u.begin_download({"url": srv.url("/x"), "size": 20001})


def test_a_download_that_is_not_an_app_image_stops_at_its_first_byte(board):
    blob = app_image(3000, magic=0x00)
    path, _ = _download(board, blob)
    assert path is None
    assert board.u.error == "not an app image"


def test_a_download_step_before_a_download_reports_nothing(board):
    assert board.u.download_step() is False
    assert board.u.error is None


def test_cancelling_a_download_clears_the_bar_and_the_slot(board):
    blob = app_image(100000)
    with Server({"/fw.bin": (200, {}, blob)}) as srv:
        board.u.begin_download(_manifest(board.mod, blob, srv.url("/fw.bin")))
        board.u.download_step()
        assert board.u.dl_done > 0
        board.u.download_cancel()
    assert (board.u.dl_done, board.u.done) == (0, 0)
    assert board.u.staged_in_slot() is False
    assert board.u.finish() is False


def test_a_vm_stop_mid_download_drops_it(board):
    blob = app_image(100000)
    with Server({"/fw.bin": (200, {}, blob)}) as srv:
        board.u.begin_download(_manifest(board.mod, blob, srv.url("/fw.bin")))
        board.u.download_step()
        nb.net_vm_stop()
        assert board.u.download_step() is False
    assert board.u.download_finish() is None
    assert board.u.finish() is False


def test_a_slot_that_will_not_take_the_bytes_says_so_in_words(board):
    nb._slot_reset(cap=4096)
    blob = app_image(10000)
    man = _manifest(board.mod, blob, "")
    man["size"] = 1                               # past the cap check, then short of room
    path, _ = _download(board, blob, manifest=man)
    assert path is None
    assert board.u.error == board.mod.NO_ROOM


# == the whole chain ==============================================================

def test_an_online_update_from_the_manifest_to_the_next_boot(board, monkeypatch):
    """Everything a real update does but the flash cells and the reboot: check
    the signed manifest, stream the image into the inactive slot, verify it,
    point the bootloader at it, reboot -- then come back up on the new slot
    and confirm from the frame loop."""
    m = board.mod
    m.OTA_PUBLIC_KEYS = TEST_KEYS
    nb._trust(TEST_KEYS)
    machine = _install_machine(monkeypatch)
    blob = app_image(70000)
    board.u.ensure_online = lambda: True
    with Server() as srv:
        manifest = _manifest(m, blob, srv.url("/fw.bin"), label="0.9")
        manifest["sig"] = sign_with_test_key(manifest)
        _route_manifest(srv, "/latest.json", manifest)
        srv.routes["/fw.bin"] = (200, {}, blob)
        board.u._manifest_source = lambda channel=None: (srv.url("/latest.json"), False)
        got = board.u.check_online()
        assert got is not None and board.u.offers(got, "stable") is True
        board.u.begin_download(got)
        while board.u.download_step():
            pass
        assert board.u.download_finish() == m.SLOT_STAGED
    assert board.u.finish() is True
    assert nb._slot() == (blob, "ota_1")
    board.u.reset()
    assert machine.resets == [1]

    nxt = m.OtaUpdater(board.card, update_dir=str(board.dir))
    esp2 = _Esp32(running="ota_1")
    _install_esp32(monkeypatch, esp2)
    assert nxt.boot_check()[0] == "ok"
    fired = [nxt.confirm_when_healthy(1) for _ in range(moy_ota_health.HEALTHY_LOOPS)]
    assert fired.count(True) == 1
    assert esp2.marked == 1


def _screen_over(tmp_path, board, srv, blob):
    from runtime import host_app

    manifest = _manifest(board.mod, blob, srv.url("/fw.bin"))
    srv.routes["/fw.bin"] = (200, {}, blob)
    u = board.u
    u.check_online = lambda ch=None: manifest
    u.offers = lambda mm, ch=None: True
    ws = host_app.build_workstation(str(tmp_path / "carts"))
    ws.updater = u
    uu = ws.update_ui
    uu.open_update_online()
    uu._pump_update(0.0)                 # the one-frame CHECKING gate
    uu._pump_update(0.0)                 # the check
    assert uu._upd_phase == "confirm_online"
    uu._update_pointer(160, 120, True)   # a tap: download
    return uu


def test_the_update_screen_streams_into_the_slot_and_installs_by_activating(
        tmp_path, board):
    blob = app_image(4096 * 9 + 100)
    with Server() as srv:
        uu = _screen_over(tmp_path, board, srv, blob)
        while uu._upd_phase == "downloading":
            uu._pump_update(0.0)
    assert uu._upd_phase == "confirm", uu._upd_msg
    assert uu._upd_bin[0] == board.mod.SLOT_STAGED
    assert nb._slot() == (blob, ""), "the second consent was skipped"
    uu._draw_update(0.0)
    uu._update_pointer(160, 120, True)   # a tap: install
    assert uu._upd_phase == "done", uu._upd_msg
    assert nb._slot()[1] == "ota_1"
    assert (board.dir / moy_ota_health.PENDING_NAME).exists(), \
        "no pending marker: a rollback would be silent"


def test_leaving_before_the_install_leaves_the_slot_unactivatable(tmp_path, board):
    with Server() as srv:
        uu = _screen_over(tmp_path, board, srv, app_image(4096 * 2))
        while uu._upd_phase == "downloading":
            uu._pump_update(0.0)
    assert uu._upd_phase == "confirm"
    uu._exit_update()
    assert board.u.staged_in_slot() is False
    assert board.u.finish() is False
    assert nb._slot()[1] == ""


# == the companion C6 (the P4s) ====================================================

def _c6_manifest(m, blob, url, version=3, **kw):
    out = dict(board=m.BOARD, channel="stable", version=1, size=1, sha256="",
               url="http://x/app.bin",
               c6={"version": version, "size": len(blob),
                   "sha256": hashlib.sha256(blob).hexdigest(), "url": url})
    out.update(kw)
    return out


def _c6_check(board, manifest, installed=-1):
    nb._c6(enabled=True, version=installed)
    cu = board.mod.C6Updater(board.u)
    board.u.ensure_online = lambda: True
    srv = Server().__enter__()
    try:
        _route_manifest(srv, "/l.json", manifest)
        board.u._manifest_source = lambda channel=None: (srv.url("/l.json"), True)
        return cu, cu.check("stable")
    finally:
        srv.__exit__()


def test_a_stock_or_v1_slave_reads_as_older_than_everything(board):
    cu, verdict = _c6_check(board, _c6_manifest(board.mod, b"x", "http://x/c6.bin"))
    assert verdict == "offer" and cu.installed is None


def test_a_current_shim_is_up_to_date_and_a_newer_manifest_offers(board):
    m = _c6_manifest(board.mod, b"x", "http://x/c6.bin", version=3)
    assert _c6_check(board, m, installed=3)[1] == "uptodate"
    assert _c6_check(board, m, installed=2)[1] == "offer"


def test_a_manifest_without_the_block_is_nothing_to_offer(board):
    m = _c6_manifest(board.mod, b"x", "http://x/c6.bin")
    del m["c6"]
    assert _c6_check(board, m)[1] == "nopublish"


def test_a_baked_manifest_needs_the_blocks_own_signature(board):
    m = _c6_manifest(board.mod, b"x", "http://x/c6.bin")
    nb._c6(enabled=True)
    cu = board.mod.C6Updater(board.u)
    board.u.ensure_online = lambda: True
    board.mod.OTA_PUBLIC_KEYS = TEST_KEYS
    nb._trust(TEST_KEYS)
    m["sig"] = sign_with_test_key(m)
    with Server() as srv:
        _route_manifest(srv, "/l.json", m)
        board.u._manifest_source = lambda channel=None: (srv.url("/l.json"), False)
        assert cu.check("stable") == "error"
    assert cu.error == "unsigned c6 image"


def test_the_c6_image_streams_into_the_radio_in_rpc_sized_chunks(board):
    blob = bytes(range(256)) * 40                 # 10240 bytes
    with Server() as srv:
        srv.routes["/c6.bin"] = (200, {}, blob)
        cu, verdict = _c6_check(board, _c6_manifest(board.mod, blob, srv.url("/c6.bin")))
        assert verdict == "offer"
        cu.begin_download()
        while cu.download_step():
            pass
        assert cu.download_finish() == board.mod.C6_STAGED
    data, ended, active, writes = nb._c6_record()
    assert data == blob
    assert writes == 7                            # 6 of 1500, then the 1240 tail
    assert (ended, active) == (0, 0), "nothing is handed over before the commit"
    cu.begin_flash(None)
    assert cu.flash_step() is False and cu.finish_flash() is True
    assert cu.activate() is True
    assert nb._c6_record()[1:3] == (1, 1)


def test_a_c6_image_that_fails_its_hash_is_never_handed_over(board):
    blob = b"c6" * 3000
    with Server() as srv:
        srv.routes["/c6.bin"] = (200, {}, blob[:-1] + b"X")
        cu, _v = _c6_check(board, _c6_manifest(board.mod, blob, srv.url("/c6.bin")))
        cu.begin_download()
        while cu.download_step():
            pass
        assert cu.download_finish() is None
    assert cu.error == "sha256 mismatch"
    assert cu.activate() is False
    assert nb._c6_record()[1:3] == (0, 0)


def test_a_radio_that_refuses_a_write_stops_the_stream_with_it_named(board):
    blob = b"r" * 6000
    with Server() as srv:
        srv.routes["/c6.bin"] = (200, {}, blob)
        cu, _v = _c6_check(board, _c6_manifest(board.mod, blob, srv.url("/c6.bin")))
        nb._c6(enabled=True, fail_at=1)
        cu.begin_download()
        while cu.download_step():
            pass
    assert board.u.error == "c6 write refused"
    assert cu.download_finish() is None


# == helpers =======================================================================

def test_an_error_string_keeps_the_class_of_a_bare_errno(board):
    """An OSError's str() is often just "113", which on the glass reads as a
    number with no noun. The screen has room for 30 characters."""
    m = board.mod
    assert m._short(OSError(113)) == "OSError 113"
    assert m._short(ValueError("2 big")) == "ValueError 2 big"
    assert m._short(RuntimeError("")) == "RuntimeError"
    assert m._short(ValueError("not an app image")) == "not an app image"
    assert len(m._short(OSError("x" * 200))) == 48



def test_logging_can_never_break_an_update(board, capsys):
    """The serial trace is the only window into the WiFi path on a board whose
    RX is dead under the desktop -- and a print that throws must not be what
    ends an install."""
    class _Explodes:
        def __str__(self):
            raise RuntimeError("nope")

    board.mod._log("fine")
    board.mod._log("bad", _Explodes())
    assert "Moybyte OTA: fine" in capsys.readouterr().out




def test_the_store_words_are_for_store_errors_only(board):
    m = board.mod
    assert m._store_short(OSError(28)) == m.NO_ROOM
    assert m._store_short(OSError(5)) == m.NO_WRITE
    assert m._store_short(ValueError("not an app image")) == "not an app image"
