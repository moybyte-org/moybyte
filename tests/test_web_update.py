"""Updating the board that serves the browser console (#41/#53, 2026-08-29).

The Zero has no screen, so the browser console it serves IS its screen -- and
"update this board" therefore has to live there. What that turned into is two
pin-gated JSON routes on the SHARED webhost plus two backends behind them, and
the split between those two is the whole design:

  * a board WITH GLASS hands the glass back. `moy_webhost.ConsoleUpdate` turns
    wasm mode off and opens the board's own update screen, which has installed
    a chunk per painted frame since #53 and takes its two confirms there. The
    browser starts NOTHING, so no second driver for one OtaUpdater exists, and
    no install work lands in `poll_webhost` -- which on the T-Deck is the frame
    tail, where an sdspi transaction is the documented panic.
  * a HEADLESS board drives its own install in its own poll loop
    (`zero_host.ZeroUpdate`, whose own suite is tests/test_zero_update.py),
    because it has no glass to hand back to and no shared bus to fear.

So what belongs HERE is: the shared route and its gate, the shared status
document both backends build, the console backend's hand-off, and the two
guards that the asymmetry above is real -- that the console path touches no
storage, and that every board gets the wiring without a per-board line.
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "device"))
sys.path.insert(0, str(ROOT / "runtime"))

import moy_webhost                                                # noqa: E402

sys.path.insert(0, str(ROOT))
import host_app                                                   # noqa: E402


class _FakeOta:
    """The slice of OtaUpdater the /update path reads. Nothing is flashed here
    and nothing needs to be: the console backend's whole job is to open a
    screen, and the screen's own install has its own suites."""

    def __init__(self, available=True):
        self._available = available
        self.boot_verdict = None
        self.dl_done = self.dl_total = 0
        self.done = self.total = 0

    def available(self):
        return self._available

    def version(self):
        return 5

    def version_label(self):
        return "0.8"

    def channel(self):
        return "stable"

    def slot(self):
        return "ota_0"


def _ws(tmp_path, ota=True):
    """A REAL Workstation, because what the hand-off has to do -- unpark the
    glass, push the update screen -- is only true of the real WM and the real
    WebConsole. A fake ws here would pass while the board did nothing."""
    ws = host_app.build_workstation(str(tmp_path / "carts"))
    ws.updater = _FakeOta() if ota else None
    return ws


class _Gate:
    """A `with_sd` that COUNTS. The T-Deck's store is on a card sharing the
    panel's SPI host and this endpoint runs at the frame tail, so "did this
    path take the storage gate at all" is the question, not "was it correct"."""

    def __init__(self):
        self.entries = 0

    def __call__(self, fn):
        self.entries += 1
        return fn()


def _host(ws, tmp_path, pin="1234", gate=None):
    h = moy_webhost.WebHost(str(tmp_path / "carts"), str(tmp_path / "web"),
                            pin=pin, with_sd=gate,
                            update=moy_webhost.ConsoleUpdate(ws))
    return h


def _reply(resp):
    body = resp.decode() if isinstance(resp, bytes) else resp
    head, _, payload = body.partition("\r\n\r\n")
    return int(head.split()[1]), payload


# -- the shared document -----------------------------------------------------


def test_both_backends_build_one_document():
    """ONE shape on the wire, whichever board answered. The page has a single
    reader, and the fields that mean the same thing everywhere -- the running
    firmware, the previous install's verdict -- cannot drift between two
    backends that each hand-rolled a dict."""
    ota = _FakeOta()
    ota.boot_verdict = ("rolled_back", "put 0.8 back")
    doc = moy_webhost.update_status(ota, "idle", True)
    assert doc["running"] == {"version": 5, "label": "0.8",
                              "channel": "stable", "slot": "ota_0",
                              "board": moy_webhost._ota_board()}
    assert doc["last"] == {"result": "rolled_back", "detail": "put 0.8 back"}
    assert json.loads(json.dumps(doc))["screen"] is True


def test_screen_is_stated_in_both_directions():
    """`screen` is a HARDWARE FACT and both values are said out loud. The
    repo's "a board with no lever reports None, never 0" rule is about
    measurements; here an OMITTED key would make a headless board and a board
    too old to have this route read identically, and the page branches on the
    difference before it offers anything."""
    ota = _FakeOta()
    assert moy_webhost.update_status(ota, "idle", False)["screen"] is False
    assert moy_webhost.update_status(ota, "idle", True)["screen"] is True


def test_a_still_progress_bar_is_no_progress_bar():
    """`progress` is ABSENT while nothing is moving rather than 0/0. A frozen
    zero is what a BROKEN transfer looks like too, and that exact ambiguity is
    what `fold=0` cost this repo weeks of a perf hunt."""
    ota = _FakeOta()
    assert "progress" not in moy_webhost.update_status(ota, "idle", False)
    doc = moy_webhost.update_status(ota, "downloading", False,
                                    progress={"done": 1, "total": 9})
    assert doc["progress"] == {"done": 1, "total": 9}


def test_nothing_published_is_not_the_same_news_as_up_to_date():
    ota = _FakeOta()
    assert "absent" not in moy_webhost.update_status(ota, "none", False)
    assert moy_webhost.update_status(ota, "none", False, absent=True)["absent"]


# -- the shared route --------------------------------------------------------


def test_the_route_is_the_shared_hosts_and_not_one_boards(tmp_path):
    """The endpoints used to exist on `zero_host` alone, which made "the
    browser can update the board that serves it" true on exactly one board of
    four. A plain WebHost answers them now."""
    h = _host(_ws(tmp_path), tmp_path)
    code, payload = _reply(h.handle_http("GET", "/update?pin=1234", None))
    assert code == 200
    assert json.loads(payload)["screen"] is True


def test_both_methods_need_the_pin(tmp_path):
    """No read-half exemption. A GET says which firmware a specific board on
    somebody's home network is running -- a shopping list for whoever wants to
    hand it an image -- and the write half replaces the board."""
    h = _host(_ws(tmp_path), tmp_path)
    for method, body in (("GET", None), ("POST", '{"action":"check"}')):
        code, payload = _reply(h.handle_http(method, "/update", body))
        assert code == 403, method
        assert json.loads(payload) == {"error": "pin"}


def test_a_board_that_cannot_update_says_so_differently_from_one_that_cannot_hear(
        tmp_path):
    """The page's probe has exactly two failure meanings and they must not
    collapse: 404 = not a board that speaks this (a static host, an older
    image), 503 = a board, with nothing to update."""
    h = _host(_ws(tmp_path, ota=False), tmp_path)
    code, payload = _reply(h.handle_http("GET", "/update?pin=1234", None))
    assert code == 503
    assert json.loads(payload) == {"error": "no updater"}
    assert h.handle_http("GET", "/updates?pin=1234", None) is None


def test_the_update_route_does_not_shadow_the_store(tmp_path):
    h = _host(_ws(tmp_path), tmp_path)
    code, _ = _reply(h.handle_http("GET", "/carts.json", None))
    assert code == 403, "the store's own gate still applies"
    assert h.handle_http("GET", "/sync", None) is not None


def test_junk_and_the_wrong_verb_are_refused_distinctly(tmp_path):
    h = _host(_ws(tmp_path), tmp_path)
    code, _ = _reply(h.handle_http("POST", "/update?pin=1234", "not json"))
    assert code == 400
    code, payload = _reply(h.handle_http("PUT", "/update?pin=1234", "{}"))
    assert code == 405
    # The refusal NAMES the write paths: a bare 405 reads like a wrong url.
    assert "/update" in payload


def test_a_body_with_no_action_looks_rather_than_installs(tmp_path):
    """The harmless reading is the only honest default for a verb that
    replaces the board."""
    ws = _ws(tmp_path)
    h = _host(ws, tmp_path)
    code, payload = _reply(h.handle_http("POST", "/update?pin=1234", "{}"))
    assert code == 200
    assert json.loads(payload)["ok"] is True


# -- the console backend: a HAND-OFF -----------------------------------------


def _parked(ws, host):
    """A board serving the web console with its glass parked on the connection
    screen -- the state every request in this section arrives in, and the one
    that made a browser-driven install impossible: the update screen is not up,
    so nothing would have advanced it."""
    host.serving = True
    ws.webhost = host
    ws.web.park()
    assert ws.web.parked
    return ws


def test_the_post_answers_and_the_hand_off_happens_after(tmp_path):
    """The answer has to get OUT first. Turning wasm mode off closes the very
    socket this request arrived on, so doing it inside the handler would race
    the one message the page needs in order to say what just happened to it."""
    ws = _ws(tmp_path)
    h = _host(ws, tmp_path)
    _parked(ws, h)
    code, payload = _reply(h.handle_http("POST", "/update?pin=1234",
                                         '{"action":"check"}'))
    assert code == 200
    doc = json.loads(payload)
    assert doc["ok"] is True and doc["screen"] is True
    # ...and NOTHING has moved yet: still serving, still parked.
    assert h.serving and ws.web.parked


def test_the_hand_off_gives_the_glass_back_and_opens_the_update_screen(tmp_path):
    """The whole feature on a board with a screen. The browser triggers; the
    board's own update screen -- the proven one, which advances the flash a
    chunk per painted frame -- is what runs."""
    ws = _ws(tmp_path)
    h = _host(ws, tmp_path)
    _parked(ws, h)
    h.handle_http("POST", "/update?pin=1234", '{"action":"check"}')
    h.update.step()
    assert not h.serving, "wasm mode is still on"
    assert not ws.web.parked, "the glass never came back"
    assert ws.wm.top_kind() == "update", ws.wm.top_kind()
    assert ws.update_ui._upd_phase == "checking"
    assert h.update.state == "glass"


def test_the_screen_is_pushed_after_the_unpark_and_not_before(tmp_path):
    """Order, not taste: unparking routes HOME, so an update screen pushed
    first would be popped by the thing that was meant to reveal it."""
    ws = _ws(tmp_path)
    h = _host(ws, tmp_path)
    _parked(ws, h)
    h.update.request("install")
    h.update.step()
    assert ws.wm.top_kind() == "update"


def test_a_second_request_is_refused_rather_than_re_opening(tmp_path):
    ws = _ws(tmp_path)
    h = _host(ws, tmp_path)
    _parked(ws, h)
    assert h.update.request("check")[0] is True
    ok, msg = h.update.request("check")
    assert ok is False and "already" in msg
    h.update.step()
    ok, msg = h.update.request("check")
    assert ok is False and "already" in msg


def test_cancel_belongs_to_whoever_is_holding_the_board(tmp_path):
    """There is nothing here to cancel: what a request starts lives on the
    glass, and its cancel is the X on that screen."""
    ws = _ws(tmp_path)
    h = _host(ws, tmp_path)
    ok, msg = h.update.request("cancel")
    assert ok is False and "screen" in msg
    code, _ = _reply(h.handle_http("POST", "/update?pin=1234",
                                   '{"action":"cancel"}'))
    assert code == 409


def test_an_unknown_action_is_refused_by_name(tmp_path):
    ws = _ws(tmp_path)
    h = _host(ws, tmp_path)
    ok, msg = h.update.request("wipe")
    assert ok is False and "check" in msg


def test_a_board_that_cannot_take_an_ota_refuses_the_request(tmp_path):
    ws = _ws(tmp_path, ota=False)
    h = _host(ws, tmp_path)
    assert h.update.status() is None
    assert h.update.request("check")[0] is False


def test_a_failed_hand_off_is_readable_and_never_breaks_the_frame(tmp_path):
    """This runs at the frame tail on three boards. A backend that raised
    there would take the desktop down from a request off the network."""
    ws = _ws(tmp_path)
    h = _host(ws, tmp_path)
    _parked(ws, h)

    def _boom():
        raise RuntimeError("no screen today")

    ws.update_ui.open_update_online = _boom
    h.update.request("check")
    h.update.step()                       # must not raise
    assert h.update.state == "error"
    assert "no screen today" in (h.update.error or "")
    code, payload = _reply(h.handle_http("GET", "/update?pin=1234", None))
    assert code == 200
    assert "no screen today" in json.loads(payload)["error"]


# -- the two guards on the asymmetry -----------------------------------------


def test_the_console_path_never_takes_the_storage_gate(tmp_path):
    """THE T-DECK GUARD. That board's store is on a card sharing the panel's
    SPI host, and `poll_webhost` runs at the frame TAIL -- after `kick()`, with
    the feeder possibly still shipping bands -- where an sdspi transaction is
    the documented Cache/MMU panic. The reason this endpoint is safe there is
    not care: it is that the console backend does no storage work AT ALL, and
    that is what this asserts. If a future change makes the browser drive the
    install, this test is the one that must be argued with first."""
    ws = _ws(tmp_path)
    gate = _Gate()
    h = _host(ws, tmp_path, gate=gate)
    _parked(ws, h)
    h.handle_http("GET", "/update?pin=1234", None)
    h.handle_http("POST", "/update?pin=1234", '{"action":"install"}')
    h.update.step()
    assert gate.entries == 0, "the update endpoint reached storage"
    # ...and the counter is LIVE, so the zero above is a fact about /update and
    # not about a gate nothing was ever going to call.
    h.handle_http("GET", "/carts.json?pin=1234", None)
    assert gate.entries == 1


def test_the_backend_is_pumped_by_the_host_and_not_by_a_board(tmp_path):
    """ONE pump for every board. The Zero's loop used to call `task.step()`
    itself; it is `WebHost.poll()`'s now, so the fourth board and the three
    console boards advance their backends through the same line -- and a
    console board gets its hand-off with no per-board wiring at all."""
    ws = _ws(tmp_path)
    h = _host(ws, tmp_path)
    _parked(ws, h)
    h.update.request("check")
    h.poll()                              # no socket: the transport no-ops
    assert h.update.state == "glass"


def test_a_backend_that_raises_cannot_take_the_poll_down(tmp_path):
    ws = _ws(tmp_path)
    h = _host(ws, tmp_path)

    class _Boom:
        state = "idle"

        def step(self):
            raise RuntimeError("nope")

        def status(self):
            return None

    h.update = _Boom()
    h.poll()                              # must not raise


def test_every_board_is_wired_by_make_webhost(tmp_path):
    """The injection is written ONCE. `ensure_online`'s docstring records what
    the alternative costs: the web console shipped on the P4 with every shared
    piece in place and the T-Deck still did not have it, because the one
    per-board line was never written there."""
    ws = _ws(tmp_path)
    h = moy_webhost.make_webhost(ws, str(tmp_path / "carts"),
                                 str(tmp_path / "web"))
    assert isinstance(h.update, moy_webhost.ConsoleUpdate)
    assert h.update.status()["screen"] is True


def test_the_two_backends_are_not_one_behind_a_flag():
    """Recorded so it is not "simplified" later. `moy_ota` keeps two rollback
    confirms -- frames drawn, and poll iterations served -- specifically
    because the argument list is where the claim about the hardware lives, and
    a flag would hide it. The same holds here: one backend opens a screen and
    the other writes flash, and they share exactly the wire document."""
    src = (ROOT / "device" / "moy_webhost.py").read_text()
    assert "class ConsoleUpdate" in src
    zero = (ROOT / "firmware" / "seeed_xiao_esp32s3_zero" / "modules"
            / "zero_host.py").read_text()
    assert "class ZeroUpdate" in zero
    # ...and the Zero no longer carries a route of its own.
    assert "def _update(" not in zero
    assert "update_status" in zero, "the Zero stopped sharing the document"


def test_binding_an_updater_late_still_grows_the_settings_rows(tmp_path):
    """The web console binds its updater LATE and the cache did not know.

    `_update_available`/`_online_update_available` cache for the boot, which is
    right on a board: the answer is a fact about the partition table, settled
    before anything can ask. But `web_boot.update_enable` binds a RemoteUpdater
    from the WORKER, after the /update probe answers -- so anything that asked
    first (a Settings screen drawn during the probe, a future caller) cached
    False, and CHANNEL / UPDATE ONLINE could then never appear however healthy
    the bridge was. On a headless board those rows are the only update UI there
    is.

    A property setter, so the next thing that injects an updater cannot forget.
    """
    import tempfile
    from runtime import host_app

    ws = host_app.build_workstation(tempfile.mkdtemp(dir=str(tmp_path)))

    def update_rows():
        return [r[1] for r in ws.settings_layer._settings_rows()
                if r[1] in ("UPDATE FW", "CHANNEL", "UPDATE ONLINE")]

    assert update_rows() == [], "no updater yet, so no rows -- and NOW it is cached"

    class Remote:
        dead = False

        def available(self):
            return False              # no local .bin over a wire; see update_link

        def online_available(self):
            return not self.dead

    ws.updater = Remote()
    assert update_rows() == ["CHANNEL", "UPDATE ONLINE"], (
        "the bridge bound and Settings never noticed -- a headless console with "
        "no way to update itself")

    # ...and the invalidation is not a one-way door: losing the updater has to
    # take the rows with it, or a dead link leaves rows that cannot work.
    ws.updater = None
    assert update_rows() == []
