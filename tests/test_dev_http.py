"""The `http` device API (runtime/system/dev_http.py over
native/moy_net/moy_fetch.h; docs/os_2026-11/hardware.md section 1): a GET
served off the VM's thread by the kernel's client against a local server, its
NET record waking the process, https refused where the console cannot verify
a certificate (the host; a redirect's hop too), the permission, the sweep, and the radio lease taken on every way in and let
go on every way out."""

import errno
import http.server
import threading

import pytest

from runtime import app_perms as P
from runtime import dev_http, devices, moybyte
from runtime import moy_http as N
from runtime import moy_input as mi
from runtime import moy_log as L


class _H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802 -- the stdlib's name
        if self.path == "/hello":
            body = b"hello, moybyte"
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/tls":
            self.send_response(302)
            self.send_header("Location", "https://example.com/")
            self.end_headers()
        else:
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()

    def log_message(self, *a):
        pass


@pytest.fixture(scope="module")
def server():
    s = http.server.HTTPServer(("127.0.0.1", 0), _H)
    t = threading.Thread(target=s.serve_forever, daemon=True)
    t.start()
    yield "http://127.0.0.1:%d" % s.server_address[1]
    s.shutdown()


@pytest.fixture
def ws(tmp_path):
    from ws_helpers import build_ws
    w = build_ws(tmp_path)
    devices.grants = None                   # a bare pid is not a run of this console
    yield w
    for pid in list({r.owner for r in dev_http._open.values()}):
        dev_http.sweep(pid)
    dev_http.auto_run = True
    devices.grants = None
    N.reset()


def _as(pid, fn):
    was = L.current()
    L.current(pid)
    try:
        return fn()
    finally:
        L.current(was)


def test_a_get_returns_the_body_and_holds_the_radio_while_it_runs(ws, server):
    pid = L.spawn("t.http")
    dev_http.auto_run = False
    r = _as(pid, lambda: moybyte.http.get(server + "/hello"))
    assert r.state() is None
    assert "http" in ws.leases.holders() and ws.wifi.radio is True
    N.run()
    assert r.state() == (200, 14)
    assert r.body() == b"hello, moybyte"
    assert ws.leases.holders() == [] and ws.wifi.radio is False
    r.close()
    assert N.count(0) == 0


def test_a_404_is_a_response_and_a_refused_connection_an_errno(ws, server):
    pid = L.spawn("t.http")
    r = _as(pid, lambda: moybyte.http.get(server + "/nope"))
    assert r.state() == (404, 0)
    r2 = _as(pid, lambda: moybyte.http.get("http://127.0.0.1:1/"))
    with pytest.raises(OSError) as e:
        r2.state()
    assert e.value.errno == errno.ECONNREFUSED
    assert ws.leases.holders() == []


def test_https_is_refused_where_nothing_verifies_and_so_is_a_redirect_to_it(ws, server):
    assert moybyte.http.tls() is False and N.tls() == 0      # the host's client is plain TCP
    pid = L.spawn("t.http")
    with pytest.raises(OSError) as e:
        _as(pid, lambda: moybyte.http.get("https://example.com/"))
    assert e.value.errno == errno.EPROTONOSUPPORT
    assert ws.leases.holders() == []
    r = _as(pid, lambda: moybyte.http.get(server + "/tls"))
    with pytest.raises(OSError) as e:
        r.state()
    assert e.value.errno == errno.EPROTONOSUPPORT
    assert ws.leases.holders() == []


def test_the_finished_request_wakes_its_process(ws, server):
    was = moybyte.WAKES.table
    moybyte.bind(mi.kernel())
    try:
        pid = L.spawn("t.http")
        dev_http.auto_run = False
        seen = []
        r = _as(pid, lambda: moybyte.http.get(server + "/hello", on_done=seen.append))
        out = [0] * 8
        assert moybyte.WAKES.due(0, out) == 0
        N.run()
        n = moybyte.WAKES.due(0, out)
        for wid in out[:n]:
            _as(pid, lambda: moybyte.WAKES.call(wid))
        assert seen == [r] and r.body() == b"hello, moybyte"
        moybyte.end(pid)
        assert moybyte.WAKES.count(pid) == 0 and N.count(0) == 0
    finally:
        moybyte.WAKES.table = None
        if was is not None:
            moybyte.bind(was)


def test_a_process_ending_mid_request_closes_it_and_lets_the_radio_go(ws, server):
    pid = L.spawn("t.http")
    dev_http.auto_run = False
    r = _as(pid, lambda: moybyte.http.get(server + "/hello"))
    assert "http" in ws.leases.holders()
    moybyte.end(pid)
    assert N.count(pid) == 0 and ws.leases.holders() == [] and ws.wifi.radio is False
    with pytest.raises(OSError) as e:
        r.readinto(bytearray(4))
    assert e.value.errno == errno.EBADF


def test_close_mid_request_lets_the_radio_go(ws, server):
    pid = L.spawn("t.http")
    dev_http.auto_run = False
    r = _as(pid, lambda: moybyte.http.get(server + "/hello"))
    r.close()
    assert ws.leases.holders() == [] and N.count(0) == 0
    N.run()
    assert N.count(0) == 0


def test_a_get_needs_the_permission(ws, server):
    pid = L.spawn("t.http")
    devices.grants = lambda p: ("network",) if p == pid else None
    with pytest.raises(OSError) as e:
        _as(pid, lambda: moybyte.http.get(server + "/hello"))
    assert e.value.errno == errno.EPERM and ws.leases.holders() == []
    devices.grants = lambda p: ("http",) if p == pid else None
    assert _as(pid, lambda: moybyte.http.get(server + "/hello")).state()[0] == 200


def test_the_permission_row_and_its_absence_without_wifi(ws):
    assert P.line("http") == "Fetch pages from the internet"
    assert P.row("http")[P.NEEDS] == "wifi"
    cart = {"id": "t.h", "runtime": "python", "permissions": ["http"], "required": ["http"]}
    ok, _why = P.check(cart, None, P.services(ws))[:2]
    assert ok
    ws.wifi = None
    assert moybyte.http is None
    assert not P.check(cart, None, P.services(ws))[0]
