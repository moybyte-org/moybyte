"""Tests for tools/gen_ota_manifest.py (#53 Phase 3): the OTA manifest generator that
emits latest.json (version/url/size/sha256) from a built firmware image."""

import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import gen_ota_manifest as gom  # noqa: E402


def test_build_manifest_computes_size_and_sha256(tmp_path):
    blob = b"\xe9moybyte firmware bytes"
    b = tmp_path / "fw.bin"
    b.write_bytes(blob)
    m = gom.build_manifest(str(b), "http://host/fw.bin", 7)
    assert m["version"] == 7
    assert m["url"] == "http://host/fw.bin"
    assert m["size"] == len(blob)
    assert m["sha256"] == hashlib.sha256(blob).hexdigest()
    assert m["filename"] == "fw.bin"


def test_read_firmware_version_matches_moy_ota():
    # The manifest version defaults to the firmware's FIRMWARE_VERSION so they can't drift.
    v = gom.read_firmware_version()
    assert isinstance(v, int) and v >= 1


def test_resolve_url_appends_filename_to_base():
    class A:
        url = None
        base_url = "https://h.example.com/moybyte/"
        port = 8000
    assert gom._resolve_url(A(), Path("/d/moybyte_micropython_tdeck.bin")) == \
        "https://h.example.com/moybyte/moybyte_micropython_tdeck.bin"


def test_main_writes_manifest_with_explicit_url_and_version(tmp_path):
    b = tmp_path / "moybyte_micropython_tdeck.bin"
    b.write_bytes(b"\xe9" + b"\x00" * 1000)
    out = tmp_path / "latest.json"
    rc = gom.main([str(b), "--url", "https://x/y.bin", "--version", "3", "--out", str(out)])
    assert rc == 0
    m = json.loads(out.read_text(encoding="utf-8"))
    assert m["version"] == 3
    assert m["url"] == "https://x/y.bin"
    assert m["size"] == 1001
    assert m["sha256"] == hashlib.sha256(b.read_bytes()).hexdigest()


def test_main_defaults_version_from_moy_ota(tmp_path):
    b = tmp_path / "img.bin"
    b.write_bytes(b"\xe9abc")
    out = tmp_path / "m.json"
    gom.main([str(b), "--url", "http://h/img.bin", "--out", str(out)])
    m = json.loads(out.read_text(encoding="utf-8"))
    assert m["version"] == gom.read_firmware_version()


def test_build_manifest_includes_channel_and_label(tmp_path):
    # #53 two-channel: the manifest carries channel + a human label (a beta's version
    # is an epoch int, so the label keeps the update screen readable).
    b = tmp_path / "f.bin"
    b.write_bytes(b"\xe9x")
    m = gom.build_manifest(str(b), "http://h/f.bin", 7)
    assert m["channel"] == "stable" and m["label"] == "v7"        # defaults
    m2 = gom.build_manifest(str(b), "u", 1700000000, channel="unstable", label="beta z")
    assert m2["channel"] == "unstable" and m2["label"] == "beta z"


def test_publish_mode_stages_channel_dir(tmp_path):
    # --root publish: copy the image to ROOT/<channel>/firmware.bin + a matching manifest,
    # url pointing at the bin. This is what `make ota-publish-unstable` runs.
    b = tmp_path / "app.bin"
    b.write_bytes(b"\xe9" + b"\x00" * 500)
    root = tmp_path / "ota"
    rc = gom.main([str(b), "--root", str(root), "--channel", "unstable",
                   "--version", "1700000000", "--label", "beta x", "--base-url", "http://h:8000"])
    assert rc == 0
    man = json.loads((root / "unstable" / "latest.json").read_text(encoding="utf-8"))
    assert man["channel"] == "unstable"
    assert man["label"] == "beta x"
    assert man["url"] == "http://h:8000/unstable/firmware.bin"
    assert man["size"] == 501
    assert (root / "unstable" / "firmware.bin").read_bytes() == b.read_bytes()


def _load_moy_ota():
    import importlib.util
    p = ROOT / "device" / "moy_ota.py"
    spec = importlib.util.spec_from_file_location("moy_ota", p)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_moy_ota_offer_logic_is_channel_aware():
    # The device offers an install when the manifest is a different channel (a switch,
    # incl. beta->stable) OR a newer version within the running channel. Host default
    # (no _ota_build stamp) is stable / FIRMWARE_VERSION.
    m = _load_moy_ota()
    u = m.OtaUpdater(with_sd=lambda fn: fn())
    assert u.channel() == "stable"
    # The LABEL is the release NAME, not the counter -- FIRMWARE_VERSION is an
    # ordering key the device compares with `>`, and nobody should have to read it.
    assert u.version_label() == m.FIRMWARE_NAME
    assert u.offers({"version": m.FIRMWARE_VERSION + 1, "channel": "stable"}) is True
    assert u.offers({"version": m.FIRMWARE_VERSION, "channel": "stable"}) is False
    assert u.offers({"version": 0, "channel": "unstable"}) is True      # switch to beta
    assert u.offers({"version": 0}, "unstable") is True                 # channel from arg


# -- the HTTP client the GitHub-hosted channels depend on --------------------
#
# moy_ota fetches its manifest and streams its image from the release each
# branch publishes (DEFAULT_CHANNEL_URLS), and a release download is a 302 to
# GitHub's CDN. Two things about those responses are hostile to a small client:
# the redirect must be followed, and GitHub's header block is nothing like
# "small" -- 5147 bytes when measured on 2026-08-02, 3626 of it one
# Content-Security-Policy header. None of this can be exercised on the device
# without a network, but the parsing is pure.

CSP = b"Content-Security-Policy: " + b"default-src 'none'; " * 180
CDN = "https://release-assets.githubusercontent.com/x/y?sig=abc%2Fdef&jwt=eyJ0"


def _response(status, headers=(), body=b""):
    lines = [b"HTTP/1.1 %d SOMETHING" % status]
    lines.extend(headers)
    return b"\r\n".join(lines) + b"\r\n\r\n" + body


class _FakeSock:
    def __init__(self, data):
        self.data, self.pos, self.closed, self.sent = data, 0, False, b""

    def settimeout(self, _):
        pass

    def connect(self, _):
        pass

    def write(self, b):
        self.sent += b

    def read(self, n=1):
        chunk = self.data[self.pos:self.pos + n]
        self.pos += len(chunk)
        return chunk

    def close(self):
        self.closed = True


class _FakeNet:
    """Stands in for both `socket` and `ssl`, handing out one scripted
    response per connection in order."""
    AF_INET, SOCK_STREAM, IPPROTO_TCP = 2, 1, 6

    def __init__(self, *responses):
        self.responses = list(responses)
        self.made = []
        self.hosts = []

    def getaddrinfo(self, host, port):
        self.hosts.append((host, port))
        return [(2, 1, 6, "", (host, port))]

    def socket(self, *a):
        s = _FakeSock(self.responses.pop(0))
        self.made.append(s)
        return s

    def wrap_socket(self, sock, server_hostname=None):
        return sock


def _updater_on(monkeypatch, net):
    m = _load_moy_ota()
    monkeypatch.setitem(sys.modules, "socket", net)
    monkeypatch.setitem(sys.modules, "ssl", net)
    return m.OtaUpdater(with_sd=lambda fn: fn())


def test_a_redirect_is_followed_to_the_body(monkeypatch):
    net = _FakeNet(_response(302, [b"Location: " + CDN.encode(), CSP]),
                   _response(200, [b"Content-Length: 11"], b"the payload"))
    u = _updater_on(monkeypatch, net)
    sock, code, clen, rest = u._http_open("https://github.com/o/r/releases/x")
    assert (code, clen) == (200, 11)
    assert sock.read(11) == b"the payload"
    assert net.made[0].closed          # the redirected connection is not leaked
    # It really went to the CDN host, not back to github.com.
    assert net.hosts[1][0] == "release-assets.githubusercontent.com"


def test_a_location_after_githubs_huge_csp_is_still_found(monkeypatch):
    """The ordering hazard. Location came first when this was measured, which
    is the only reason a 4096-byte cap ever worked -- so pin the other order."""
    net = _FakeNet(_response(302, [CSP, b"Location: " + CDN.encode()]),
                   _response(200, [b"Content-Length: 2"], b"ok"))
    assert len(CSP) > 3500
    u = _updater_on(monkeypatch, net)
    sock, code, clen, rest = u._http_open("https://github.com/o/r/releases/x")
    assert code == 200 and sock.read(2) == b"ok"


def test_the_body_survives_a_header_block_of_any_size(monkeypatch):
    """Headers are read a byte at a time precisely so the first bytes of a 4 MB
    image are not swallowed with them: the reader stops ON the blank line and
    leaves the body in the socket for download_step to stream."""
    net = _FakeNet(_response(200, [CSP, b"Content-Length: 6"], b"\xe9IMAGE"))
    u = _updater_on(monkeypatch, net)
    sock, code, clen, rest = u._http_open("http://h/fw.bin")
    assert (code, clen, rest) == (200, 6, b"")
    assert sock.read(6) == b"\xe9IMAGE"


def test_a_redirect_loop_gives_up_rather_than_spinning(monkeypatch):
    net = _FakeNet(*[_response(302, [b"Location: http://h/again"])
                     for _ in range(9)])
    u = _updater_on(monkeypatch, net)
    sock, code, clen, rest = u._http_open("http://h/start", hops=3)
    assert code == 302                 # returned, not chased forever
    assert len(net.made) == 4          # the first request plus `hops` follows


def test_a_relative_location_resolves_against_the_current_host(monkeypatch):
    net = _FakeNet(_response(302, [b"Location: /elsewhere/fw.bin"]),
                   _response(200, [b"Content-Length: 1"], b"!"))
    u = _updater_on(monkeypatch, net)
    sock, code, clen, rest = u._http_open("https://host.example:8443/a/b")
    assert code == 200
    assert net.hosts[1] == ("host.example", 8443)
    assert b"GET /elsewhere/fw.bin " in net.made[1].sent
