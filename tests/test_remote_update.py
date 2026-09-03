"""The SHARED update screen, driven over a wire (#41/#53, 2026-08-29).

The browser console is the same console every board runs, so the update SCREEN
in it is `runtime/update_ui.py` -- the one that ships on glass, frozen into the
wasm bundle and dead only while `ws.updater` is None.
`firmware/web_runner/update_link.py` fills that seam from a board's pin-gated
/update, which makes Settings -> UPDATE ONLINE in a browser the screen a kid
sees on a T-Deck, with the same two confirms and the same error text.

WHAT THIS FILE EXISTS FOR is the one seam where the two do not obviously fit.
`UpdateUI` was written for a backend it DRIVES: `download_step()` and `step()`
mean "move one chunk", called once per painted frame, and the counters advance
because of it. A remote backend moves nothing -- the BOARD downloads over its
own radio and writes its own flash -- so those calls become reads of a polled
state. Every way that can go wrong is cheap to catch here and expensive
anywhere else, so it is caught here:

  * a check that has not been answered must READ AS WAITING, not as "no
    manifest". That is the `pending` probe, and without it the very first
    frame reports an error against a board that is simply still looking.
  * the two confirms must gate two real halves of the board's work. That is
    why the board grew a `ready` state; if it flashed off the first tap the
    screen would be drawing "A = INSTALL" over a board already writing its app
    partition.
  * progress must be the BOARD's, never invented, and never advanced by the
    act of asking.

No wire and no browser: the real `RemoteUpdater` is looped back onto the real
`ZeroUpdate` / `ConsoleUpdate` through the real request/status vocabulary, so
this also pins that the two halves agree about the verbs.
"""

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "device"))
sys.path.insert(0, str(ROOT / "runtime"))
sys.path.insert(0, str(ROOT / "firmware" / "web_runner"))
sys.path.append(str(ROOT / "firmware" / "seeed_xiao_esp32s3_zero" / "modules"))

import moy_webhost                                                # noqa: E402
import update_link                                               # noqa: E402
import zero_host                                                 # noqa: E402

sys.path.insert(0, str(ROOT))
import host_app                                                   # noqa: E402

MANIFEST = {"version": 6, "label": "0.9", "channel": "stable",
            "size": 2153792, "url": "https://example/latest.bin"}


class _FakeOta:
    """An OtaUpdater with the radio and the flash taken out. Its counters move
    only when the BOARD steps, which is what lets a test prove the screen never
    moved them."""

    def __init__(self, manifest=None, absent=False):
        self.manifest = manifest
        self.absent = absent
        self.error = None
        self.boot_verdict = None
        self.dl_done = self.dl_total = 0
        self.done = self.total = 0
        self.slices = 3
        self.calls = []
        self.reset_called = 0
        self.offers_it = True
        self.checked_channel = "-"

    def version(self):
        return 5

    def version_label(self):
        return "0.8"

    def channel(self):
        return "stable"

    def slot(self):
        return "ota_0"

    def available(self):
        return True

    def offers(self, manifest, channel=None):
        return self.offers_it

    def boot_check(self):
        return self.boot_verdict

    def check_online(self, channel=None):
        self.calls.append("check")
        self.checked_channel = channel
        return self.manifest

    def begin_download(self, manifest, to_slot=False):
        # `to_slot` streams into the inactive partition instead of a staging
        # file (the Zero's whole filesystem is smaller than its own image). The
        # BOARD chooses it; this double only has to accept it, since what these
        # tests drive is the screen above the wire.
        self.to_slot = to_slot
        if self.error:
            raise ValueError(self.error)
        self.dl_total = int(manifest.get("size") or 0)
        self.dl_done = 0

    def download_step(self):
        self.calls.append("dl")
        self.dl_done = min(self.dl_total,
                           self.dl_done + self.dl_total // self.slices + 1)
        return self.dl_done < self.dl_total

    def download_finish(self):
        if self.error:
            return None
        return "<slot>" if getattr(self, "to_slot", False) \
            else "/moy/update/firmware.bin"

    def staged_in_slot(self):
        return bool(getattr(self, "to_slot", False))

    def begin(self, path):
        self.total = 1000
        self.done = 0
        return self.total

    def step(self):
        self.calls.append("flash")
        self.done = min(self.total, self.done + self.total // self.slices + 1)
        return self.done < self.total

    def finish(self):
        return True

    def reset(self):
        self.reset_called += 1

    def cancel(self):
        pass

    def download_cancel(self):
        pass


class _Link:
    """The worker's pump, in process. ONE request in flight, answered with the
    board's own status document -- the same two shapes worker.js moves."""

    def __init__(self, board):
        self.board = board
        self.remote = update_link.RemoteUpdater(board.status(), log=lambda *a: None)
        self.posts = 0
        self.actions = []
        self.offline = False

    def pump(self):
        """One worker tick: send a queued request, else poll if asked to."""
        body = self.remote.take_json()
        if body:
            self.posts += 1
            self.actions.append(json.loads(body).get("action"))
            if self.offline:
                self.remote.ack(False)
                return
            doc = json.loads(body)
            ok, msg = self.board.request(doc.get("action"), doc.get("channel"))
            out = self.board.status()
            out["ok"] = ok
            out["message"] = msg
            self.remote.ack(True, json.dumps(out))
            return
        if self.remote.wants_poll():
            if self.offline:
                self.remote.ack(False)
                return
            self.remote.ack(True, json.dumps(self.board.status()))

    def tick(self):
        self.board.step()


def _ws(tmp_path, remote):
    ws = host_app.build_workstation(str(tmp_path / "carts"))
    ws.updater = remote
    return ws


def _zero(manifest=MANIFEST, **kw):
    ota = _FakeOta(manifest, **kw)
    return ota, zero_host.ZeroUpdate(ota)


def _frames(ws, link, n=1, board=True):
    """n painted frames of the update screen, with the wire and the board's own
    poll loop turning beside them. `board=False` freezes the board, which is how
    a test proves the SCREEN never moved anything."""
    for _ in range(n):
        ws.update_ui._pump_update(0.016)
        link.pump()
        if board:
            link.tick()


def _tap(ws):
    """A tap anywhere but the X -- what the confirm screens read as yes."""
    ws.update_ui._update_pointer(160, 150, True)


# -- the capability gate -----------------------------------------------------


def test_the_rows_a_remote_backend_offers(tmp_path):
    """UPDATE ONLINE appears; UPDATE FW does NOT.

    `available()` is False on purpose and it inverts the local invariant: on a
    board it means "there are two app slots" and gates installing a `.bin` a
    human put on the card. No verb across this wire names a local file, so the
    row is expressed as ABSENT rather than as a row that always fails.
    """
    _ota, board = _zero()
    link = _Link(board)
    ws = _ws(tmp_path, link.remote)
    assert ws._online_update_available() is True
    assert ws._update_available() is False
    keys = [r[0] for r in ws.settings_layer._settings_rows()]
    assert "update_online" in keys
    assert "update" not in keys
    # ...and UPDATE ONLINE is LAST, which is what makes it reachable from a
    # fresh Settings open with one ArrowUp (the browser E2E leans on this).
    assert keys[-1] == "update_online"


def test_a_page_no_board_served_has_no_updater_and_no_rows(tmp_path):
    """Site mode (moybyte.com, an export): the worker never answers the probe,
    so nothing is injected and both queries are False -- the way every
    capability row in this console already gates."""
    ws = host_app.build_workstation(str(tmp_path / "carts"))
    assert ws.updater is None
    assert ws._online_update_available() is False
    keys = [r[0] for r in ws.settings_layer._settings_rows()]
    assert "update_online" not in keys and "ota_channel" not in keys


# -- the check ---------------------------------------------------------------


def test_a_check_that_has_not_been_answered_reads_as_waiting(tmp_path):
    """THE seam. A local `check_online` blocks and returns a verdict; a remote
    one can only ask. Without the `pending` probe the first frame's empty
    answer is reported as "no manifest" -- an error message about a board that
    is still looking, on the screen a kid stares at."""
    _ota, board = _zero()
    link = _Link(board)
    ws = _ws(tmp_path, link.remote)
    ws.update_ui.open_update_online()
    assert ws.update_ui._upd_phase == "checking"
    # Several frames with the board's own loop FROZEN: the request cannot have
    # been answered, and the screen must sit still rather than fail.
    _frames(ws, link, n=6, board=False)
    assert ws.update_ui._upd_phase == "checking", ws.update_ui._upd_msg
    assert not ws.update_ui._upd_msg
    # Now let the board work.
    _frames(ws, link, n=6)
    assert ws.update_ui._upd_phase == "confirm_online"
    assert ws.update_ui._online_manifest["label"] == "0.9"


def test_up_to_date_lands_on_up_to_date(tmp_path):
    ota, board = _zero()
    ota.offers_it = False
    link = _Link(board)
    ws = _ws(tmp_path, link.remote)
    ws.update_ui.open_update_online()
    _frames(ws, link, n=8)
    assert ws.update_ui._upd_phase == "uptodate"


def test_a_channel_with_nothing_on_it_is_not_an_error(tmp_path):
    _ota, board = _zero(manifest=None, absent=True)
    link = _Link(board)
    ws = _ws(tmp_path, link.remote)
    ws.update_ui.open_update_online()
    _frames(ws, link, n=8)
    assert ws.update_ui._upd_phase == "nopublish"


def test_a_board_failure_reaches_the_screen_in_the_boards_own_words(tmp_path):
    _ota, board = _zero(manifest=None)
    link = _Link(board)
    ws = _ws(tmp_path, link.remote)
    ws.update_ui.open_update_online()
    _frames(ws, link, n=8)
    assert ws.update_ui._upd_phase == "error"
    assert "check failed" in ws.update_ui._upd_msg


def test_the_channel_row_travels_with_the_request(tmp_path):
    """A headless board has no Settings of its own, so the browser's CHANNEL
    row is the only place this choice can be made at all."""
    ota, board = _zero()
    link = _Link(board)
    ws = _ws(tmp_path, link.remote)
    ws.system["ota_channel"] = "unstable"
    ws.update_ui.open_update_online()
    _frames(ws, link, n=8)
    assert ota.checked_channel == "unstable"


def test_the_previous_installs_verdict_interrupts_once(tmp_path):
    ota, board = _zero()
    ota.boot_verdict = ("rolled_back", "put 0.8 back")
    link = _Link(board)
    ws = _ws(tmp_path, link.remote)
    ws.update_ui.open_update_online()
    assert ws.update_ui._upd_phase == "rolledback"
    assert ws.update_ui._upd_msg == "put 0.8 back"
    # Reading it clears it: the screen behaves normally on the next open, even
    # though the board keeps reporting `last` until its next install.
    _frames(ws, link, n=3)
    ws.update_ui.open_update_online()
    assert ws.update_ui._upd_phase == "checking"


# -- the two acts ------------------------------------------------------------


def _to_offer(ws, link):
    ws.update_ui.open_update_online()
    _frames(ws, link, n=8)
    assert ws.update_ui._upd_phase == "confirm_online", ws.update_ui._upd_msg


def test_the_two_taps_gate_the_two_halves_of_the_boards_work(tmp_path):
    """Same two confirms as on glass, and each one gates something real.

    The board stops at `ready` between them precisely so the second tap has
    something to authorise -- a board that flashed off the first would leave
    this screen drawing "A = INSTALL" over an app partition already being
    written."""
    ota, board = _zero()
    link = _Link(board)
    ws = _ws(tmp_path, link.remote)
    _to_offer(ws, link)

    _tap(ws)                                   # ACT ONE: download
    assert ws.update_ui._upd_phase == "downloading"
    _frames(ws, link, n=20)
    assert ws.update_ui._upd_phase == "confirm"
    assert board.state == "ready"
    # NOTHING has been activated, and the screen is what is holding it back.
    # `<slot>` rather than a path because this board streams into the inactive
    # partition -- the bytes are down and verified, and the running slot is
    # still what a reboot would come back on.
    assert "flash" not in ota.calls
    assert ws.update_ui._upd_bin[0] == "<slot>"
    assert ws.update_ui._upd_bin[1] == MANIFEST["size"]

    # Frames keep painting while the kid decides. Still nothing flashed.
    _frames(ws, link, n=20)
    assert "flash" not in ota.calls
    assert ws.update_ui._upd_phase == "confirm"

    _tap(ws)                                   # ACT TWO: install
    assert ws.update_ui._upd_phase == "install"
    _frames(ws, link, n=20)
    assert "flash" in ota.calls
    assert ws.update_ui._upd_phase == "done"


def test_the_screen_never_advances_a_chunk(tmp_path):
    """The mismatch, asserted directly. `download_step()` is called once per
    painted frame and moves NOTHING: with the board's poll loop frozen, forty
    frames of the downloading screen leave every counter where it was."""
    ota, board = _zero()
    link = _Link(board)
    ws = _ws(tmp_path, link.remote)
    _to_offer(ws, link)
    _tap(ws)
    _frames(ws, link, n=3)                     # let the request land
    before = (list(ota.calls), ota.dl_done)
    _frames(ws, link, n=40, board=False)
    assert (ota.calls, ota.dl_done) == before, \
        "the screen moved the board's transfer by looking at it"
    assert ws.update_ui._upd_phase == "downloading"


def test_progress_is_the_boards_own(tmp_path):
    """Never invented. The number under the bar is the board's, at worst one
    poll old -- and before a byte has moved it is the manifest's size, which is
    what the board is about to fetch rather than a guess."""
    ota, board = _zero()
    link = _Link(board)
    ws = _ws(tmp_path, link.remote)
    _to_offer(ws, link)
    assert link.remote.dl_total == MANIFEST["size"]
    assert link.remote.dl_done == 0
    _tap(ws)
    _frames(ws, link, n=6)
    assert 0 < link.remote.dl_done <= ota.dl_done
    _frames(ws, link, n=20)
    assert link.remote.dl_done == ota.dl_done == ota.dl_total


def test_leaving_the_screen_cancels_on_the_board(tmp_path):
    ota, board = _zero()
    link = _Link(board)
    ws = _ws(tmp_path, link.remote)
    _to_offer(ws, link)
    _tap(ws)
    _frames(ws, link, n=3)
    ws.update_ui._exit_update()
    _frames(ws, link, n=3)
    assert board.state == "idle"


def test_a_screen_that_asked_for_nothing_cancels_nothing(tmp_path):
    """`_exit_update` fires both cancels on EVERY exit, including a B press on
    a screen that never started anything. That must not become a POST.

    Opening the screen DOES speak to the board -- it asks it to check, which is
    a real request with a real side effect (the board fetches a manifest over
    its own WiFi). So the invariant is about the CANCELS, not about silence:
    `_cancel` returns early when `_asked` is None, and this is what pins that.
    Counting every post would just be re-counting the check."""
    _ota, board = _zero()
    link = _Link(board)
    ws = _ws(tmp_path, link.remote)
    ws.update_ui.open_update_online()
    ws.update_ui._exit_update()
    _frames(ws, link, n=3)
    assert "cancel" not in link.actions, link.actions


def test_the_reboot_is_the_boards_and_the_screen_does_not_pretend(tmp_path):
    """`reset()` is a no-op here. A page cannot reboot a board; the board
    resets itself after the grace its own machine keeps, and the screen sits on
    "rebooting..." because that is what is true."""
    ota, board = _zero()
    link = _Link(board)
    ws = _ws(tmp_path, link.remote)
    _to_offer(ws, link)
    _tap(ws)
    _frames(ws, link, n=20)
    _tap(ws)
    _frames(ws, link, n=30)
    assert ws.update_ui._upd_phase == "done"
    ws.update_ui._upd_at = 0                   # past the 1200ms hold
    _frames(ws, link, n=3)                     # calls reset() -- must not raise
    assert ws.update_ui._upd_phase == "done"
    assert ota.reset_called == 0, "the browser rebooted the board"


# -- a board WITH GLASS ------------------------------------------------------


def test_a_console_with_glass_hands_the_glass_back(tmp_path):
    """No progress UI in the browser, and the screen says why.

    This is not a policy choice: a console advances the flash one chunk per
    PAINTED FRAME of its update screen, and while a browser is driving, that
    board's glass is parked on the WEB CONSOLE connection screen. An install
    driven from here would sit at 0% forever unless the chunk work moved into
    `poll_webhost` -- the frame tail, where the T-Deck's shared SPI bus makes
    an sdspi transaction the documented panic.
    """
    board_ws = host_app.build_workstation(str(tmp_path / "board"))
    board_ws.updater = _FakeOta(MANIFEST)
    board = moy_webhost.ConsoleUpdate(board_ws)
    link = _Link(board)
    ws = _ws(tmp_path, link.remote)
    assert link.remote.screen() is True
    ws.update_ui.open_update_online()
    _frames(ws, link, n=8)
    assert ws.update_ui._upd_phase == "handed"
    # ...and the board really did take it: its glass is showing the screen.
    assert board_ws.wm.top_kind() == "update"


# -- the link itself ---------------------------------------------------------


def test_a_board_that_stops_answering_says_so(tmp_path):
    """A FLASHING board legitimately goes quiet for seconds, so the threshold
    is far above gpio_link's -- but silence forever is a board that has gone,
    and the screen has to stop waiting and say something."""
    _ota, board = _zero()
    link = _Link(board)
    ws = _ws(tmp_path, link.remote)
    ws.update_ui.open_update_online()
    link.offline = True
    _frames(ws, link, n=update_link.MAX_FAILS + 4, board=False)
    assert link.remote.dead is True
    assert ws.update_ui._upd_phase == "error"
    assert "stopped answering" in ws.update_ui._upd_msg


def test_one_request_in_flight_at_a_time(tmp_path):
    """gpio_link's rule, and for the same reason: the far end answers one body
    at a time, and a second in flight would be settled by the wrong ack."""
    _ota, board = _zero()
    link = _Link(board)
    ws = _ws(tmp_path, link.remote)
    ws.update_ui.open_update_online()
    ws.update_ui._pump_update(0.016)
    ws.update_ui._pump_update(0.016)            # arms, then asks
    assert link.remote.take_json()              # the queued check
    assert link.remote.take_json() == "", "a second body went out unanswered"


def test_the_polling_stops_when_nobody_is_watching(tmp_path):
    """A GET a second costs a board nothing while a kid is on the update
    screen, and would be a request a second forever if it never turned off."""
    _ota, board = _zero()
    link = _Link(board)
    assert link.remote.wants_poll() == ""
    ws = _ws(tmp_path, link.remote)
    ws.update_ui.open_update_online()
    _frames(ws, link, n=4)
    assert link.remote.wants_poll() == "1"


def test_the_two_halves_agree_about_the_verbs():
    """The loopback above is only worth anything if it speaks what the board
    speaks. Both backends accept the same three, and the link sends only those.
    """
    _ota, board = _zero()
    for verb in ("check", "download", "install", "cancel"):
        assert board.request(verb)[1] is not None
    assert board.request("reformat")[0] is False
    src = (ROOT / "firmware" / "web_runner" / "update_link.py").read_text()
    for verb in ("check", "download", "install", "cancel"):
        assert '"%s"' % verb in src or "'%s'" % verb in src
