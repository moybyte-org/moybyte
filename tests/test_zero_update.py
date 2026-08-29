"""Firmware updates on a board with no screen (the Zero, #41/#53).

Every other board triggers and reports an update on its own glass: Settings ->
UPDATE ONLINE draws a CHECKING screen, a progress bar, and the verdict of the
last install. This board has none, so the SAME `OtaUpdater` is driven by
`zero_host.ZeroUpdate` and reported as JSON on two pin-gated endpoints. What is
worth pinning is therefore not the OTA machinery -- `tests/test_moy_ota.py` and
`tests/test_ota_health.py` own that -- but the three things the headless shape
introduces:

  * the POST ANSWERS and the work happens later. A 2MB download inside the
    request would make every outcome look like a browser timeout, which on a
    board you cannot see is the same as no report at all.
  * BOTH METHODS ARE GATED, with no read-half exemption. What a GET reveals is
    which firmware a specific board on somebody's home network is running, and
    the write half replaces the board. This is the same call `/gpio`'s GET was
    brought under on 2026-08-25.
  * a failure has to be READABLE afterwards, because nobody is watching when it
    happens.
"""

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "device"))            # moy_webhost, moy_webserver
sys.path.insert(0, str(ROOT / "runtime"))           # moy_sync + its leaves
# APPENDED: that directory holds the board's own modules AND the copies its
# build stages there, and `device/` must win for every shared name.
sys.path.append(str(ROOT / "firmware" / "seeed_xiao_esp32s3_zero" / "modules"))

import zero_host                                               # noqa: E402


class _FakeOta:
    """The OtaUpdater surface ZeroUpdate drives, with the flash taken out.

    A fake rather than the real updater with a fake board under it (which
    `tests/test_moy_ota.py` already builds): what is under test here is the
    SEQUENCING -- who is called, in what order, and what a caller can read
    between the calls -- and a fake is the only way to assert that the install
    really is spread across poll iterations instead of running to completion
    inside one.
    """

    def __init__(self, manifest=None, absent=False):
        self.manifest = manifest
        self.absent = absent
        self.error = None
        self.boot_verdict = None
        self.confirmed = False
        self.dl_total = 0
        self.dl_done = 0
        self.total = 0
        self.done = 0
        self.calls = []
        self.reset_called = 0
        self.download_slices = 3
        self.install_slices = 3
        self.finish_ok = True
        self.offers_it = True
        self.checked_channel = "-"
        self.offered_against = "-"

    # -- what ZeroUpdate reads
    def version(self):
        return 5

    def version_label(self):
        return "0.8"

    def channel(self):
        return "stable"

    def slot(self):
        return "ota_0"

    def offers(self, manifest, channel=None):
        self.offered_against = channel
        return self.offers_it

    # -- what ZeroUpdate calls
    def boot_check(self):
        self.calls.append("boot_check")
        return self.boot_verdict

    def check_online(self, channel=None):
        self.calls.append("check")
        self.checked_channel = channel
        return self.manifest

    def begin_download(self, manifest):
        self.calls.append("begin_download")
        if self.error:
            raise ValueError(self.error)
        self.dl_total = int(manifest.get("size", 0) or 0)
        self.dl_done = 0

    def download_step(self):
        self.calls.append("download_step")
        self.download_slices -= 1
        self.dl_done = self.dl_total - max(self.download_slices, 0) * 100
        return self.download_slices > 0

    def download_finish(self):
        self.calls.append("download_finish")
        return None if self.error else "/moy/update/firmware.bin"

    def begin(self, path):
        self.calls.append("begin")
        self.total = 1000
        return self.total

    def step(self):
        self.calls.append("install_step")
        self.install_slices -= 1
        self.done = self.total - max(self.install_slices, 0) * 100
        return self.install_slices > 0

    def finish(self):
        self.calls.append("finish")
        return self.finish_ok

    def reset(self):
        self.reset_called += 1

    def cancel(self):
        self.calls.append("cancel")

    def download_cancel(self):
        self.calls.append("download_cancel")


MANIFEST = {"version": 6, "label": "0.9", "channel": "stable",
            "size": 2153792, "url": "https://example/latest.bin"}


def _drive(task, limit=200):
    """Pump step() the way serve()'s loop does, and count the iterations."""
    for i in range(limit):
        if not task.step():
            return i
    return limit


# -- the check ---------------------------------------------------------------


def test_a_boot_check_runs_once_and_installs_nothing_by_default():
    """THE TRIGGER IS A REQUEST, NOT A TIMER (the decision this board makes).

    A boot check that installed would mean the board holding a kid's only copy
    of their carts replaces its own firmware unattended, with nobody watching
    and no screen to say so. So the boot check LOOKS and reports; installing is
    a request carrying the pin -- the same act of consent that gates every other
    write to this board.
    """
    ota = _FakeOta(MANIFEST)
    task = zero_host.ZeroUpdate(ota)
    assert task.boot_check_once() is True
    assert task.boot_check_once() is False, "the boot check must run once"
    _drive(task)
    assert "check" in ota.calls
    assert "begin_download" not in ota.calls
    assert task.state == "offer"
    assert task.status()["available"]["label"] == "0.9"


def test_no_setting_turns_the_boot_check_into_an_install():
    """`"ota_auto": true` in /moy/zero.json used to, and it was DELETED (owner
    call 2026-08-29). No other board in this tree has an auto-install concept:
    a console board takes two deliberate human acts, opening the update screen
    and confirming. This board's screen is the browser it serves, and the
    request will come from there.

    Pinned as an absence because that is the failure mode -- a flag re-added
    "off by default" is unattended firmware replacement on the board holding a
    kid's only local copy of their carts, reachable by editing a JSON file.
    """
    ota = _FakeOta(MANIFEST)
    with pytest.raises(TypeError):
        zero_host.ZeroUpdate(ota, auto=True)
    task = zero_host.ZeroUpdate(ota)
    assert not hasattr(task, "auto")
    assert "auto" not in task.status()
    task.boot_check_once()
    _drive(task)
    assert "begin_download" not in ota.calls
    assert task.state == "offer"


def test_an_empty_channel_is_not_an_error():
    """A channel with nothing published for this board yet is the NORMAL state
    before its first release -- and this board is brand new to the channels.
    Reporting it as a failure would put a red line on the only surface anybody
    reads."""
    ota = _FakeOta(None, absent=True)
    task = zero_host.ZeroUpdate(ota)
    task.request("check")
    task.step()
    assert task.state == "none"
    assert "error" not in task.status()


def test_a_real_check_failure_is_readable_afterwards():
    ota = _FakeOta(None)
    ota.error = "wifi offline"
    task = zero_host.ZeroUpdate(ota)
    task.request("check")
    task.step()
    assert task.state == "error"
    assert task.status()["error"] == "wifi offline"


def test_up_to_date_is_reported_as_up_to_date_and_not_as_an_offer():
    """`none` is the verdict; the manifest is the EVIDENCE and is still
    reported (2026-08-29). The shared update screen draws "UP TO DATE" from
    what the check found, and a board that threw the manifest away the moment
    it judged it not-newer left that screen with nothing to print."""
    ota = _FakeOta(MANIFEST)
    ota.offers_it = False
    task = zero_host.ZeroUpdate(ota)
    task.request("check")
    task.step()
    assert task.state == "none"
    assert task.offered is False
    assert task.status()["available"]["label"] == "0.9"


def test_the_requested_channel_reaches_the_check():
    """The browser's CHANNEL row is the ONLY place this choice can be made --
    a headless board has no Settings of its own -- so it travels with the
    request rather than being a setting somebody could have written here."""
    ota = _FakeOta(MANIFEST)
    task = zero_host.ZeroUpdate(ota)
    task.request("check", "unstable")
    task.step()
    assert ota.checked_channel == "unstable"
    assert ota.offered_against == "unstable", \
        "the offer was judged against a different channel than was checked"


def test_no_channel_means_the_running_builds_own():
    """A boot check and a bare curl both mean "whatever I am on"."""
    ota = _FakeOta(MANIFEST)
    task = zero_host.ZeroUpdate(ota)
    task.request("check")
    task.step()
    assert ota.checked_channel is None


# -- the two acts ------------------------------------------------------------


def _to_offer(task):
    task.request("check")
    task.step()
    assert task.state == "offer"


def test_the_download_stops_and_waits_for_the_second_consent():
    """THE REASON THIS BOARD GREW A `ready` STATE (2026-08-29).

    `install` used to mean the whole job -- check, download and flash off one
    request -- and the shared update screen cannot mirror that: it asks TWICE,
    once before the download and again before the flash, exactly as it does for
    an image found on a card. A board that flashed off the first tap would
    leave the second confirm gating nothing, and the screen would be drawing an
    "A = INSTALL" prompt over a board already writing its app partition.

    So the download stops. The image is on the filesystem, verified, and the
    running slot is untouched until somebody asks again.
    """
    ota = _FakeOta(MANIFEST)
    task = zero_host.ZeroUpdate(ota)
    _to_offer(task)
    task.request("download")
    _drive(task)
    assert task.state == "ready"
    assert task.staged == "/moy/update/firmware.bin"
    assert task.status()["staged"] == "/moy/update/firmware.bin"
    # The flash has NOT begun and cannot have: nothing touched the slot.
    assert "begin" not in ota.calls and "install_step" not in ota.calls
    # ...and the size the second confirm prints is the transfer that happened.
    assert task.status()["progress"]["total"] == MANIFEST["size"]


def test_installing_without_a_download_is_refused_by_name():
    """The second act cannot be taken first. A caller that asks to flash with
    nothing staged is told what is missing, not silently handed a check."""
    task = zero_host.ZeroUpdate(_FakeOta(MANIFEST))
    ok, msg = task.request("install")
    assert ok is False and "download" in msg


def test_the_install_is_spread_across_poll_iterations():
    """The shape the endpoint depends on. If any of this ran to completion
    inside one call, the POST would either block for a minute or the board would
    stop serving carts while it updated -- and it is the store host being UP
    that this image's rollback confirm is measured on."""
    ota = _FakeOta(MANIFEST)
    task = zero_host.ZeroUpdate(ota)
    _to_offer(task)
    ok, _ = task.request("download")
    assert ok
    task.step()
    assert task.state == "downloading"
    assert ota.calls.count("download_step") == 0, (
        "begin_download and the first slice must not be the same iteration")
    task.step()
    assert task.status()["progress"]["total"] == MANIFEST["size"]
    _drive(task)
    assert ota.calls.count("download_step") >= 3
    assert task.state == "ready"
    task.request("install")
    _drive(task)
    assert ota.calls.count("install_step") >= 3
    assert "finish" in ota.calls
    assert task.state == "reboot"


def test_the_board_keeps_serving_for_a_while_before_it_reboots():
    """The reset is deferred in POLL ITERATIONS, not slept: the GET that asked
    for this has to be able to read `"state": "reboot"` and learn the board is
    rebooting rather than simply gone."""
    ota = _FakeOta(MANIFEST)
    task = zero_host.ZeroUpdate(ota)
    task.request("download")
    _drive(task)
    task.request("install")
    for _ in range(20):
        task.step()
    assert task.state == "reboot"
    assert ota.reset_called == 0, "it rebooted before anyone could read the state"
    _drive(task, limit=500)
    assert ota.reset_called == 1


def test_downloading_without_an_offer_checks_first():
    """A caller that knows it wants the newest build should not have to make two
    requests, and the check is the thing that decides whether there IS one.
    This is the convenience the old chained `install` existed for; it belongs to
    the FETCH, which is reversible, and never to the flash."""
    ota = _FakeOta(MANIFEST)
    task = zero_host.ZeroUpdate(ota)
    task.request("download")
    task.step()
    assert ota.calls[:2] == ["check", "begin_download"]


def test_a_busy_updater_refuses_a_second_request_rather_than_interleaving():
    ota = _FakeOta(MANIFEST)
    task = zero_host.ZeroUpdate(ota)
    task.request("download")
    task.step()
    task.step()
    assert task.state == "downloading"
    ok, msg = task.request("download")
    assert ok is False and "busy" in msg


def test_a_refused_download_says_which_half_refused_it():
    ota = _FakeOta(MANIFEST)
    task = zero_host.ZeroUpdate(ota)
    task.request("check")
    task.step()
    ota.error = "http 403"
    task.request("download")
    task.step()
    assert task.state == "error"
    assert task.status()["error"] == "http 403"
    assert "download_step" not in ota.calls


def test_a_failed_set_boot_never_reads_as_a_finished_install():
    ota = _FakeOta(MANIFEST)
    ota.finish_ok = False
    task = zero_host.ZeroUpdate(ota)
    task.request("download")
    _drive(task)
    task.request("install")
    _drive(task)
    assert task.state == "error"
    assert ota.reset_called == 0


def test_cancel_stops_the_transfer_and_returns_to_idle():
    ota = _FakeOta(MANIFEST)
    task = zero_host.ZeroUpdate(ota)
    task.request("download")
    task.step()
    task.step()
    task.request("cancel")
    assert task.state == "idle"
    assert "download_cancel" in ota.calls and "cancel" in ota.calls


def test_an_unknown_action_is_refused_by_name():
    task = zero_host.ZeroUpdate(_FakeOta(MANIFEST))
    ok, msg = task.request("reformat")
    assert ok is False and "check" in msg


# -- what a human reads ------------------------------------------------------


def test_the_status_carries_the_previous_installs_verdict():
    """The headless replacement for the console's notice banner. Without it a
    rollback is silent on this board in the strongest sense: the update was
    tried, undone, and there is no screen that could ever have said so."""
    ota = _FakeOta(MANIFEST)
    ota.boot_verdict = ("rolled_back", "put 0.8 back")
    task = zero_host.ZeroUpdate(ota)
    task.boot_check()
    got = task.status()
    assert got["last"] == {"result": "rolled_back", "detail": "put 0.8 back"}
    assert got["running"]["label"] == "0.8"
    assert got["running"]["slot"] == "ota_0"
    # It has to survive a round trip through the wire it is written for.
    assert json.loads(json.dumps(got))["last"]["result"] == "rolled_back"


# -- the endpoints -----------------------------------------------------------


@pytest.fixture
def host(tmp_path):
    carts = tmp_path / "carts"
    carts.mkdir()
    web = tmp_path / "web"
    web.mkdir()
    h = zero_host.zero_host_class()(str(carts), str(web), pin="1234")
    h.update = zero_host.ZeroUpdate(_FakeOta(MANIFEST))
    return h


def _status(resp):
    body = resp.decode() if isinstance(resp, bytes) else resp
    head, _, payload = body.partition("\r\n\r\n")
    return int(head.split()[1]), payload


def test_both_methods_need_the_pin(host):
    """No read-half exemption, and this is the deliberate part.

    `/gpio`'s GET was open until 2026-08-25 on the reasoning that it changes
    nothing; what it hands over is a fact about somebody's house. The same
    argument lands harder here -- a GET says which firmware version a specific
    board on a home network is running, which is a shopping list for whoever
    wants to hand it an image.
    """
    for method, body in (("GET", None), ("POST", '{"action":"check"}')):
        code, payload = _status(host.handle_http(method, "/update", body))
        assert code == 403, method
        assert json.loads(payload) == {"error": "pin"}


def test_a_get_with_the_pin_answers_the_status(host):
    code, payload = _status(host.handle_http("GET", "/update?pin=1234", None))
    assert code == 200
    assert json.loads(payload)["running"]["board"] is not None


def test_a_post_queues_and_answers_immediately(host):
    code, payload = _status(
        host.handle_http("POST", "/update?pin=1234", '{"action":"check"}'))
    assert code == 200
    doc = json.loads(payload)
    assert doc["ok"] is True
    # ANSWERED, not done: the work is still queued for the poll loop.
    assert host.update.ota.calls == []
    host.update.step()
    assert "check" in host.update.ota.calls


def test_a_post_with_no_action_defaults_to_looking_not_installing(host):
    _status(host.handle_http("POST", "/update?pin=1234", "{}"))
    host.update.step()
    assert host.update.ota.calls == ["check"]


def test_junk_and_the_wrong_verb_are_refused_distinctly(host):
    code, _ = _status(host.handle_http("POST", "/update?pin=1234", "not json"))
    assert code == 400
    code, _ = _status(host.handle_http("PUT", "/update?pin=1234", "{}"))
    assert code == 405


def test_a_board_whose_updater_would_not_build_still_serves_its_carts(host):
    """A failure constructing the updater costs the update endpoints and
    NOTHING ELSE -- the kid's carts are the product, the firmware endpoint is
    not."""
    host.update = None
    code, payload = _status(host.handle_http("GET", "/update?pin=1234", None))
    assert code == 503
    assert json.loads(payload) == {"error": "no updater"}
    # ...and the store endpoints are untouched by the absence.
    assert host.handle_http("GET", "/sync", None) is not None


def test_the_update_route_does_not_shadow_the_store(host):
    """`/update` is a fourth arm on a subclass, and the three it sits beside
    have to keep working: an override that swallowed everything would be a
    board that serves no carts and passes every test in this file."""
    assert host.handle_http("GET", "/updates?pin=1234", None) is None
    code, _ = _status(host.handle_http("GET", "/carts.json", None))
    assert code == 403, "the store's own gate still applies"
