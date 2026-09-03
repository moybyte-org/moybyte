"""First-run setup on the Zero (#41): the AP a board hosts when it has no way in.

The interesting half of this feature is not the radio -- it is what happens to
a form typed by somebody standing at a headless board with one chance to get it
right. So what is pinned here is the parsing, the refusals, the shapes that
reach flash, and the ONE branch that decides a board hosts an AP at all. The
radio itself (SoftAP up, STA scan beside it) is hardware and is verified on
hardware; see the board README for exactly what was and was not.
"""

import json
import socket
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "device"))            # moy_webserver
# APPENDED, not inserted, since the Zero became a build target (2026-08-29):
# that directory now holds the board's own modules AND the copies its build
# stages there, and a staged `moy_webserver.py` is exactly the untracked stale
# copy tests/test_staging_closure.py exists to stop anything reading. `device/`
# stays first, so only the board-AUTHORED names resolve out of here.
sys.path.append(str(ROOT / "firmware" / "seeed_xiao_esp32s3_zero" / "modules"))

import zero_setup                                              # noqa: E402
import zero_host                                               # noqa: E402


def _form(**kw):
    fields = {"name": "zero", "pin": "1234", "ssid": "home", "password": ""}
    fields.update(kw)
    return fields


# -- the form ---------------------------------------------------------------


def test_the_body_a_plain_html_form_posts_is_what_parses():
    """No JavaScript in the path that matters. The page's script only fills the
    network list; the form itself is a plain POST, so a phone with a broken or
    blocked script can still configure the board by typing the SSID.

    The accented SSID is the point of the case: percent escapes are BYTES, and
    decoding them one at a time yields a network name that looks right in a log
    and never matches the network."""
    got = zero_setup.parse_form(
        b"name=attic+zero&pin=0042&ssid=Caf%C3%A9%20Wifi&password=hunter2%26co")
    assert got == {"name": "attic zero", "pin": "0042",
                   "ssid": "Café Wifi", "password": "hunter2&co"}


def test_a_junk_body_parses_to_nothing_rather_than_raising():
    assert zero_setup.parse_form(b"") == {}
    assert zero_setup.parse_form(b"garbage&&=x") == {"": "x"}


@pytest.mark.parametrize("fields, word", [
    ({"ssid": ""}, "network"),
    ({"ssid": "   "}, "network"),
    ({"pin": ""}, "4 digits"),
    ({"pin": "12"}, "4 digits"),
    ({"pin": "12345"}, "4 digits"),
    ({"pin": "12a4"}, "4 digits"),
    ({"name": ""}, "name"),
    ({"name": "---"}, "name"),          # nothing survives the label clean
    ({"password": "short"}, "8 and 63"),
    ({"ssid": "x" * 33}, "too long"),
])
def test_a_bad_form_is_refused_with_a_sentence(fields, word):
    clean, why = zero_setup.validate(_form(**fields))
    assert clean is None
    assert word in why


def test_an_open_network_needs_no_password():
    clean, why = zero_setup.validate(_form(password=""))
    assert why is None and clean["password"] == ""


def test_the_name_becomes_an_mdns_label():
    """It ends up in `network.hostname()`, so it takes what a DNS label takes.
    Spaces and capitals are what a person types; both are folded rather than
    refused, because refusing "Attic Zero" would be pedantry."""
    clean, _ = zero_setup.validate(_form(name="  Attic Zero!  "))
    assert clean["name"] == "atticzero"
    assert zero_setup.clean_name("-x-") == "x"
    assert zero_setup.clean_name("A" * 40) == "a" * 24


# -- what reaches flash ------------------------------------------------------


def test_the_saved_network_goes_first_and_replaces_its_own_old_copy():
    """connect() walks the list in order, so the network somebody just typed in
    has to be the one tried first -- and the others stay, because a board set
    up at a friend's house should still come up at home."""
    doc = {"networks": [{"ssid": "home", "password": "old"},
                        {"ssid": "school", "password": "s"}]}
    out = zero_setup.merge_network(doc, "home", "new")
    assert out == {"networks": [{"ssid": "home", "password": "new"},
                                {"ssid": "school", "password": "s"}]}


def test_merge_survives_a_missing_or_odd_wifi_file():
    assert zero_setup.merge_network(None, "a", "b") == {
        "networks": [{"ssid": "a", "password": "b"}]}
    assert zero_setup.merge_network([{"ssid": "x"}], "a", "b")["networks"][1] \
        == {"ssid": "x"}


def test_setup_writes_the_consoles_own_wifi_shape_and_this_boards_identity():
    """wifi.json is the CONSOLE's document, not a new one -- zero_host reads it
    with the same code a board's WiFi panel writes it with, which is why a
    creds file copied off a console board already works."""
    written = {}
    zero_setup.save_setup({"name": "attic", "pin": "4242",
                           "ssid": "home", "password": "secret"},
                          "/moy/wifi.json", "/moy/zero.json",
                          read=lambda p: None,
                          write=lambda p, d: written.__setitem__(p, d))
    assert written["/moy/wifi.json"] == {
        "networks": [{"ssid": "home", "password": "secret"}]}
    assert written["/moy/zero.json"] == {"name": "attic", "pin": "4242"}


def test_a_real_round_trip_through_files(tmp_path):
    wifi = tmp_path / "wifi.json"
    zero = tmp_path / "zero.json"
    wifi.write_text('{"networks": [{"ssid": "school", "password": "s"}]}')
    zero_setup.save_setup({"name": "attic", "pin": "0000",
                           "ssid": "home", "password": ""},
                          str(wifi), str(zero))
    assert json.loads(wifi.read_text())["networks"][0]["ssid"] == "home"
    assert json.loads(zero.read_text()) == {"name": "attic", "pin": "0000"}


# -- the server --------------------------------------------------------------


def _server(saved=None, nets=()):
    return zero_setup.SetupServer(
        "moybyte-zero-ab12",
        (saved if saved is not None else (lambda clean: None)),
        lambda: list(nets))


def test_the_form_is_served_and_names_the_ap_you_joined():
    body = _server().handle_http("GET", "/", b"")
    assert b"200 OK" in body and b"text/html" in body
    assert b"moybyte-zero-ab12" in body
    assert b'action="/setup"' in body


# -- the form, on the thing it is actually read on ---------------------------


def test_the_form_still_works_with_the_page_script_dead():
    """The accessibility floor, and it is deliberate: this page is reached from
    an unknown phone in the one flow a headless board has no other channel for.
    Everything the script touches has a correct no-JavaScript state IN THE
    MARKUP -- the SSID is an input and not a <select>, the hint under it already
    says what to do, and the two containers the script fills render as nothing
    while they are empty. A control that a broken script leaves inert is a
    control that lies."""
    body = _server().handle_http("GET", "/", b"")
    assert b'<input name="ssid"' in body and b"<select" not in body
    assert b"Type its name exactly." in body
    assert b'<div id="nets"></div>' in body
    assert b'<div id="reveal"></div>' in body


def test_the_form_is_sized_for_a_thumb_and_asks_for_the_right_keyboard():
    """44px is the tap target both platforms ask for, and 16px is the input
    size below which iOS ZOOMS on focus -- a zoomed page needs horizontal
    scrolling to fill in, one-handed, next to a board you cannot see."""
    body = _server().handle_http("GET", "/", b"")
    assert b'name="viewport"' in body and b"width=device-width" in body
    assert b"maximum-scale" not in body, "pinch-zoom must stay available"
    assert body.count(b"min-height:44px") >= 1
    assert b"min-height:48px" in body              # the submit button
    assert b"font-size:1rem" in body
    assert b'inputmode="numeric"' in body and b'pattern="[0-9]{4}"' in body


def test_the_wifi_password_can_be_revealed_and_is_never_second_guessed():
    """A WiFi key typed blind, once, by somebody who cannot see the board is
    the likeliest way this whole flow fails -- and it fails minutes later, as a
    board that never comes back, with no way to tell a typo from a dead radio.
    The four off-switches matter for the same reason, and they matter MORE once
    the field is revealed: a revealed field is a text field, and a phone will
    capitalise and autocorrect one."""
    body = _server().handle_http("GET", "/", b"")
    assert b'type="password"' in body
    assert b'cb.type="checkbox"' in body
    assert b'pw.type=cb.checked?"text":"password"' in body
    pw = body.split(b'name="password"')[1].split(b"</label>")[0]
    for off in (b'autocapitalize="off"', b'autocorrect="off"',
                b'autocomplete="off"', b'spellcheck="false"'):
        assert off in pw, off


def test_the_scan_failing_or_finding_nothing_says_so_on_the_page():
    """Every answer the network list can give ends in the same instruction,
    because typing the name is always the way out -- including the one for a
    captive-portal webview too old to have `fetch`, which would otherwise
    leave the hint reading "Looking for networks..." forever. The reveal is
    wired up BEFORE any of this for the same reason: a script that dies at the
    scan must not take the password field's only affordance with it."""
    body = _server().handle_http("GET", "/", b"")
    assert b"No networks in range - type its name exactly." in body
    assert b"Could not look for networks - type its name exactly." in body
    assert b"Tap yours, or type its name exactly." in body
    assert b"Cannot list networks here - type its name exactly." in body
    assert body.index(b'reveal").appendChild') < body.index(b'fetch("scan")')


def test_the_done_page_hands_back_an_address_and_a_way_to_find_it_anyway():
    """mDNS is the happy path and is not universal -- an Android that cannot
    resolve `.local` would otherwise leave a person with an address that does
    not work and no second idea. The router's own device list is the second
    idea, and it lists the board under the name just chosen."""
    srv = _server()
    body = srv.handle_http(
        "POST", "/setup", b"name=attic&pin=4242&ssid=home&password=hunter2xyz")
    assert b"?pin=4242" in body
    assert b"attic.local" in body
    assert b"router" in body and body.count(b"attic") >= 2


def test_a_good_post_saves_once_and_arms_the_reboot():
    got = []
    srv = _server(saved=got.append)
    body = srv.handle_http(
        "POST", "/setup", b"name=attic&pin=4242&ssid=home&password=hunter2xyz")
    assert b"200 OK" in body
    assert got == [{"name": "attic", "pin": "4242",
                    "ssid": "home", "password": "hunter2xyz"}]
    assert srv.saved == got[0]
    assert srv.reboot_at is not None
    # The DONE page hands back the pinned url, because a pin nobody is told is
    # a board that silently refuses every edit made on it.
    assert b"?pin=4242" in body


def test_a_bad_post_re_serves_the_form_with_the_reason_and_saves_nothing():
    got = []
    srv = _server(saved=got.append)
    body = srv.handle_http("POST", "/setup", b"name=attic&pin=9&ssid=home")
    assert b"400 " in body and b"4 digits" in body
    assert got == [] and srv.saved is None and srv.reboot_at is None


def test_a_failed_write_is_a_500_that_says_nothing_changed_not_a_reboot():
    def boom(clean):
        raise OSError(28, "ENOSPC")

    srv = _server(saved=boom)
    body = srv.handle_http(
        "POST", "/setup", b"name=attic&pin=4242&ssid=home&password=")
    assert b"500 " in body and b"Nothing was changed" in body
    assert srv.reboot_at is None       # a board that saved nothing must not
    assert srv.saved is None           # reboot into the same empty state


def test_scan_reports_the_strongest_copy_of_each_network():
    nets = [(b"home", b"", 1, -70, 3, False),
            (b"home", b"", 6, -41, 3, False),      # the repeater in the hall
            (b"open", b"", 1, -55, 0, False),
            (b"", b"", 1, -30, 3, False)]          # hidden: nothing to tap
    doc = json.loads(zero_setup.scan_json(nets))
    assert [n["ssid"] for n in doc["nets"]] == ["home", "open"]
    assert doc["nets"][0]["rssi"] == -41
    assert doc["nets"][0]["lock"] == 1 and doc["nets"][1]["lock"] == 0


def test_a_failed_scan_is_an_empty_list_not_a_broken_page():
    """The form is still usable by typing the network name, which is the whole
    reason the SSID field is an input with a datalist and not a <select>."""
    def boom():
        raise OSError("radio busy")

    srv = zero_setup.SetupServer("ap", lambda c: None, boom)
    assert json.loads(srv.scan_cached()) == {"nets": []}


def test_the_ap_name_carries_the_mac_tail():
    assert zero_setup.ap_ssid(b"\xaa\xbb\xcc\xdd\xee\xff") == \
        "moybyte-zero-eeff"
    assert zero_setup.ap_ssid(None) == "moybyte-zero-0000"


def test_an_unserved_post_is_a_404_from_the_transport():
    """Only the GET half became a redirect (below). A POST to a path this
    server does not know is still nothing."""
    assert _server().handle_http("POST", "/whatever", b"x=1") is None


# -- the captive portal (2026-08-29) -----------------------------------------
#
# This module's docstring used to DECLINE the DNS hijack, and it carries the
# reversal, its date and its price. What is pinned here is the mechanism: the
# probe reaches this board because it resolves here, and it opens the form
# because the answer is not the one the phone was expecting.


def _query(name, qtype=1, qclass=1, qid=0x1234, flags=b"\x01\x00", qd=1):
    labels = b""
    for label in name.split("."):
        labels += bytes([len(label)]) + label.encode()
    return (bytes([qid >> 8, qid & 0xFF]) + flags + bytes([0, qd])
            + b"\x00" * 6 + labels + b"\x00"
            + bytes([qtype >> 8, qtype & 0xFF, qclass >> 8, qclass & 0xFF]))


@pytest.mark.parametrize("probe", [
    "connectivitycheck.gstatic.com",           # Android
    "captive.apple.com",                       # iOS / macOS
    "www.msftconnecttest.com",                 # Windows
    "moybyte.example",                         # and anything else at all
])
def test_every_name_a_phone_asks_for_resolves_to_this_board(probe):
    q = _query(probe)
    reply = zero_setup.dns_reply(q, "192.168.4.1")
    assert reply[:2] == q[:2]                  # the id, echoed
    assert reply[2] & 0x80 and reply[2] & 0x04  # QR=1, AA=1
    assert reply[3] == 0                       # RCODE 0 (NOERROR)
    assert reply[4:6] == b"\x00\x01" and reply[6:8] == b"\x00\x01"
    assert reply[12:len(q)] == q[12:]          # the question, verbatim
    rr = reply[len(q):]
    assert rr[:2] == b"\xc0\x0c"               # NAME -> the question's copy
    assert rr[2:6] == b"\x00\x01\x00\x01"      # A, IN
    assert rr[6:10] == b"\x00\x00\x00\x00", (
        "TTL must be 0 -- a hijacked name that outlives the phone's stay on "
        "this AP is harm we caused on somebody's real network")
    assert rr[10:] == b"\x00\x04\xc0\xa8\x04\x01"


def test_a_question_we_cannot_answer_is_empty_and_never_nxdomain():
    """A phone asks AAAA before A. NXDOMAIN would say the name does not exist
    at all rather than "not over IPv6 here" -- a probe that never falls back
    and a form that never opens."""
    for qtype, qclass in ((28, 1), (1, 3), (255, 1)):
        reply = zero_setup.dns_reply(
            _query("captive.apple.com", qtype=qtype, qclass=qclass),
            "192.168.4.1")
        assert reply[3] == 0, "RCODE must stay NOERROR"
        assert reply[6:8] == b"\x00\x00", "and carry no records"


@pytest.mark.parametrize("bad, why", [
    (_query("x.example", flags=b"\x81\x80"), "a REPLY: two responders on one "
                                             "AP would trade packets forever"),
    (_query("x.example", flags=b"\x09\x00"), "not a standard QUERY"),
    (_query("x.example", qd=2), "more than one question"),
    (_query("x.example")[:20], "truncated mid-name"),
    (_query("x.example")[:-1], "truncated QCLASS"),
    (b"\x12\x34\x01\x00\x00\x01" + b"\x00" * 6 + b"\xc0\x0c\x00\x01\x00\x01",
     "a compression pointer in the QUESTION -- illegal, and the reply echoes "
     "the question, so following one is the only way this emits what it never "
     "read"),
    (b"", "nothing"),
    (b"\x12\x34", "a fragment"),
    (_query("x.example") + b"\x00" * 600, "larger than a UDP DNS query can be"),
])
def test_a_datagram_that_is_not_a_plain_question_is_dropped(bad, why):
    assert zero_setup.dns_reply(bad, "192.168.4.1") is None, why


def test_the_responder_answers_over_a_real_socket_and_lets_go_of_it():
    dns = zero_setup.DnsRedirect("192.168.4.1", port=0)
    assert dns.start() is True
    port = dns.sock.getsockname()[1]
    client = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    client.settimeout(3)
    try:
        client.sendto(_query("connectivitycheck.gstatic.com"),
                      ("127.0.0.1", port))
        for _ in range(200):
            if dns.poll():
                break
            time.sleep(0.005)
        assert dns.answered == 1
        reply, _ = client.recvfrom(512)
        assert reply.endswith(b"\xc0\xa8\x04\x01")
        assert dns.poll() == 0            # and it does not spin on an empty
    finally:                              # socket
        client.close()
        dns.stop()
    assert dns.sock is None
    assert dns.poll() == 0                # a stopped responder is inert


def test_a_responder_that_cannot_have_its_socket_is_not_fatal(capsys):
    """The form is the thing that must come up. A board that cannot bind :53
    -- something already there, a port this build refuses -- serves the form
    exactly as it did before this feature existed, and says so once."""
    dns = zero_setup.DnsRedirect("192.168.4.1", port=999999)
    assert dns.start() is False
    assert "no captive portal" in capsys.readouterr().out
    assert dns.poll() == 0
    dns.stop()                            # and stopping a dead one is a no-op


@pytest.mark.parametrize("path", [
    "/generate_204",                      # Android wants a 204 and no body
    "/gen_204",
    "/hotspot-detect.html",               # Apple wants a body reading Success
    "/library/test/success.html",
    "/connecttest.txt",                   # Windows
    "/favicon.ico",
    "/setup",                             # a GET of the POST-only path
    "/moy/carts",                         # or anything a person half-remembers
])
def test_a_probe_is_answered_with_the_form_instead_of_what_it_wanted(path):
    """Which is the whole trick: a phone reads any unexpected answer to its own
    probe as "there is a portal here" and OPENS it. The 302 rather than the
    form's own bytes so that a person in a real browser ends up looking at the
    address they will need again."""
    srv = zero_setup.SetupServer("moybyte-zero-ab12", lambda c: None,
                                 lambda: [], ip="192.168.9.1")
    body = srv.handle_http("GET", path, b"")
    assert b"302 Found" in body
    assert b"Location: http://192.168.9.1/\r\n" in body
    assert b"204" not in body.split(b"\r\n\r\n")[0]
    assert b"Success" not in body


# -- the whole first run, end to end -----------------------------------------


class _Rebooted(Exception):
    """`machine.reset()` on a host. The end of `run()`, and of the test."""


class _FakeAP:
    MAC = b"\x24\x6f\x28\xaa\x97\x3d"

    def __init__(self, log):
        self.log = log
        self.up = False
        self.configured = {}

    def active(self, on=None):
        if on is None:
            return self.up
        self.up = on
        self.log.append("ap up" if on else "ap down")

    def config(self, *args, **kw):
        if args:
            return self.MAC if args[0] == "mac" else None
        self.configured.update(kw)

    def ifconfig(self):
        return ("192.168.4.1", "255.255.255.0", "192.168.4.1", "192.168.4.1")


class _FakeSTA:
    def __init__(self, log):
        self.log = log
        self.up = False

    def active(self, on=None):
        if on is None:
            return self.up
        self.up = on
        self.log.append("sta up" if on else "sta down")

    def scan(self):
        return [(b"home", b"", 6, -41, 3, False)]

    def connect(self, *args):               # setup must never join anything:
        self.log.append("sta connect")      # it saves and reboots instead


class _FakeNetwork:
    AP_IF = 1
    STA_IF = 0
    AUTH_OPEN = 0

    def __init__(self, ap, sta):
        self._ap = ap
        self._sta = sta

    def WLAN(self, iface):
        return self._ap if iface == self.AP_IF else self._sta


class _FakeMachine:
    def __init__(self, log, on_reset):
        self.log = log
        self.on_reset = on_reset

    def reset(self):
        self.log.append("reset")
        self.on_reset()
        raise _Rebooted()


class _FakeTime:
    """MicroPython's `time` for `run()`'s loop: real, plus `sleep_ms`, which is
    the hook the test drives a phone through."""

    def __init__(self, on_sleep):
        self._on_sleep = on_sleep

    def sleep_ms(self, ms):
        self._on_sleep()

    def __getattr__(self, name):
        return getattr(time, name)


def test_a_whole_first_run_lands_on_flash_and_reboots_in_that_order(
        tmp_path, monkeypatch):
    """THE LEG THAT HAD NEVER RUN (board README, "NOT verified on hardware").

    The on-glass verification of 2026-08-25 posted a real form at a real AP,
    but it saved into a LIST and never called `machine.reset()` -- running the
    genuine `run()` on the desk would have overwritten that board's real
    credentials with a made-up network. So four pieces were each proven and the
    SEQUENCE was not, and the sequence is the half that reboots a board.

    Here `run()` is the real one: the real transport over a real socket, the
    real page, the real parse, the real merge, real files. The radio and the
    reset are the only injections, which is exactly what a bench has and a host
    does not. What it pins:

      * a refusal changes NOTHING on flash and does not arm the reboot;
      * the phone has the whole answer BEFORE the board resets (the files are
        read inside `machine.reset()`, so "before" is not inferred from the
        end state);
      * the new network goes FIRST and the old one is KEPT -- `connect()`
        walks the list in order, and a board set up at a friend's house still
        comes up at home;
      * the AP and both sockets are taken down before the reset, in that order.
    """
    wifi = tmp_path / "wifi.json"
    zero = tmp_path / "zero.json"
    old = '{"networks": [{"ssid": "school", "password": "sssssssss"}]}'
    wifi.write_text(old)

    log = []
    seen = {}
    clients = []
    made = []
    real_server = zero_setup.SetupServer

    class _SpyServer(real_server):
        """The real server, so the test drives real HTTP; the spy is only how
        the test learns the ephemeral port and sees the close."""

        def __init__(self, *a, **kw):
            real_server.__init__(self, *a, **kw)
            made.append(self)

        def stop(self):
            log.append("server stop")
            real_server.stop(self)

    class _SpyDns(zero_setup.DnsRedirect):
        polls = 0

        def start(self):
            log.append("dns start %s" % self.ip)
            return True

        def poll(self):
            _SpyDns.polls += 1
            return 0

        def stop(self):
            log.append("dns stop")

    def _send(request):
        c = socket.socket()
        c.settimeout(5)
        c.connect(("127.0.0.1", made[0].sock.getsockname()[1]))
        c.sendall(request.encode("utf-8"))
        clients.append(c)

    def _get(path):
        _send("GET %s HTTP/1.1\r\nHost: 192.168.4.1\r\n\r\n" % path)

    def _post(body):
        _send("POST /setup HTTP/1.1\r\nHost: 192.168.4.1\r\n"
              "Content-Type: application/x-www-form-urlencoded\r\n"
              "Content-Length: %d\r\n\r\n%s" % (len(body), body))

    def _read(which):
        out = b""
        while True:
            chunk = clients[which].recv(4096)
            if not chunk:
                return out
            out += chunk

    def _read_bad():
        seen["bad"] = _read(2)
        seen["bad_left_wifi"] = wifi.read_text()
        seen["bad_wrote_zero"] = zero.exists()

    steps = [
        lambda: _get("/"),
        lambda: seen.__setitem__("form", _read(0)),
        lambda: _get("/generate_204"),
        lambda: seen.__setitem__("probe", _read(1)),
        lambda: _post("name=attic&pin=99&ssid=home&password=hunter2xyz"),
        _read_bad,
        # A real form as a phone sends it: `+` for the space, and an SSID whose
        # percent escapes are BYTES (decoding them one at a time is a network
        # name that looks right in a log and never matches the network).
        lambda: _post("name=Attic+Zero&pin=4242&ssid=Caf%C3%A9"
                      "&password=hunter2xyz"),
    ]

    def _on_sleep():
        if steps:
            steps.pop(0)()

    def _on_reset():
        # READ INSIDE THE RESET. This is what makes "before" a fact rather than
        # something inferred from the state at the end of the test.
        seen["wifi"] = json.loads(wifi.read_text())
        seen["zero"] = json.loads(zero.read_text())
        seen["done"] = _read(3)

    ap = _FakeAP(log)
    sta = _FakeSTA(log)
    monkeypatch.setattr(zero_setup, "SetupServer", _SpyServer)
    monkeypatch.setattr(zero_setup, "DnsRedirect", _SpyDns)
    assert zero_setup.REBOOT_MS >= 500, (
        "the shipped grace has to outlive lwIP pushing the last bytes")
    # Taken out for the test: the ORDER it guards is structural (the response
    # is written inside poll(), before due() is ever read), so sleeping through
    # 1.2s would buy nothing.
    monkeypatch.setattr(zero_setup, "REBOOT_MS", 0)
    monkeypatch.setitem(sys.modules, "machine", _FakeMachine(log, _on_reset))
    monkeypatch.setitem(sys.modules, "network", _FakeNetwork(ap, sta))
    monkeypatch.setitem(sys.modules, "time", _FakeTime(_on_sleep))
    try:
        with pytest.raises(_Rebooted):
            zero_setup.run(str(wifi), str(zero), port=0)
    finally:
        for c in clients:
            c.close()

    assert ap.configured == {"essid": "moybyte-zero-973d", "authmode": 0}
    assert b"moybyte-zero-973d" in seen["form"]
    assert b'action="/setup"' in seen["form"]
    assert b"302 Found" in seen["probe"]
    assert b"Location: http://192.168.4.1/" in seen["probe"]

    assert b"400 " in seen["bad"] and b"4 digits" in seen["bad"]
    assert seen["bad_left_wifi"] == old, "a refusal wrote to flash"
    assert seen["bad_wrote_zero"] is False

    assert b"200 OK" in seen["done"] and b"</html>" in seen["done"]
    assert b"?pin=4242" in seen["done"]
    assert seen["wifi"]["networks"] == [
        {"ssid": "Café", "password": "hunter2xyz"},
        {"ssid": "school", "password": "sssssssss"}]
    assert seen["zero"] == {"name": "atticzero", "pin": "4242"}

    assert log == ["ap up", "sta up", "dns start 192.168.4.1",
                   "server stop", "dns stop", "ap down", "reset"]
    assert made[0].sock is None
    assert _SpyDns.polls >= len(steps), "the portal is not pumped by the loop"


def test_a_first_run_that_cannot_write_neither_reboots_nor_forgets_the_form(
        tmp_path, monkeypatch):
    """The other end of the same leg. A full or broken filesystem must leave a
    board that is still hosting the AP and still says what happened -- the
    alternative is a reboot into the same credential-less state, which is a
    board that loops instead of asking again."""
    calls = []
    log = []

    def _boom(clean):
        calls.append(clean)
        raise OSError(28, "ENOSPC")

    srv = zero_setup.SetupServer("moybyte-zero-973d", _boom, lambda: [])
    body = srv.handle_http("POST", "/setup",
                           b"name=attic&pin=4242&ssid=home&password=")
    assert b"500 " in body and b"Nothing was changed" in body
    assert b'action="/setup"' in body, "the form has to still be there"
    assert srv.due() is False and srv.saved is None
    assert len(calls) == 1
    assert log == []


# -- the branch that decides any of this runs --------------------------------


def test_a_boot_with_no_joinable_network_hosts_the_setup_ap(monkeypatch):
    ran = []
    monkeypatch.setattr(zero_host, "_mkdir", lambda p: None)
    monkeypatch.setattr(zero_host, "connect", lambda **kw: None)
    monkeypatch.setattr(zero_host, "identity", lambda: {})
    monkeypatch.setattr(zero_setup, "run", lambda *a, **k: ran.append(a))
    zero_host.serve()
    assert ran == [(zero_host.WIFI_STORE, zero_host.ZERO_STORE)]


def test_a_boot_that_joins_serves_and_never_hosts_the_ap(monkeypatch):
    """...and carries the configured name and pin into the host it starts. The
    pin is the whole consent story for this board's write half, so a boot that
    quietly forgot it would be an open port with a padlock drawn on it."""
    class _Stop(Exception):
        pass

    class _Host:
        def __init__(self, *a, **kw):
            self.kw = kw

        def start(self, ip):
            raise _Stop("%s|%s" % (ip, self.kw.get("pin")))

    ran = []
    monkeypatch.setattr(zero_host, "_mkdir", lambda p: None)
    monkeypatch.setattr(zero_host, "connect",
                        lambda **kw: kw.get("hostname") and "192.168.1.9")
    monkeypatch.setattr(zero_host, "identity",
                        lambda: {"name": "attic", "pin": "4242"})
    monkeypatch.setattr(zero_host, "zero_host_class", lambda: _Host)
    monkeypatch.setattr(zero_setup, "run", lambda *a, **k: ran.append(a))
    with pytest.raises(_Stop) as exc:
        zero_host.serve()
    assert str(exc.value) == "192.168.1.9|4242"
    assert ran == []


def test_identity_is_absent_rather_than_fatal_on_a_usb_provisioned_board(
        tmp_path, monkeypatch):
    """A board whose creds were pushed over USB has no zero.json, and that is
    a supported arrangement -- not every Zero goes through the AP."""
    monkeypatch.setattr(zero_host, "ZERO_STORE", str(tmp_path / "nope.json"))
    assert zero_host.identity() == {}
    p = tmp_path / "zero.json"
    p.write_text('{"name": "attic", "pin": "4242"}')
    monkeypatch.setattr(zero_host, "ZERO_STORE", str(p))
    assert zero_host.identity()["pin"] == "4242"
    p.write_text("not json at all")
    assert zero_host.identity() == {}
