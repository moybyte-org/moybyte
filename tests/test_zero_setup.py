"""First-run setup on the Zero (#41): the AP a board hosts when it has no way in.

The interesting half of this feature is not the radio -- it is what happens to
a form typed by somebody standing at a headless board with one chance to get it
right. So what is pinned here is the parsing, the refusals, the shapes that
reach flash, and the ONE branch that decides a board hosts an AP at all. The
radio itself (SoftAP up, STA scan beside it) is hardware and is verified on
hardware; see the board README for exactly what was and was not.
"""

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "device"))            # moy_webserver
sys.path.insert(0, str(ROOT / "firmware" / "seeed_xiao_esp32s3_zero"))

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


def test_an_unknown_path_is_a_404_from_the_transport():
    assert _server().handle_http("GET", "/generate_204", b"") is None


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
