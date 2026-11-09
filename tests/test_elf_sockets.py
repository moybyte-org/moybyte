"""The native tier's sockets on the host (native/moy_elf/moy_os.h, SOCKETS):
an unmodified netcat built by the SDK (tests/fixtures/elf/elf_nc.moy) run as
a command in the Terminal's session, against a Python socket either way;
the "network" permission; the radio's "native" hold from the first socket
to the end; a blocked accept ended by Ctrl-C and its socket swept."""

import socket
import threading
import time

from tests.test_elf_commands import built, out, term  # noqa: F401 -- the fixtures


def _port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _peer(handle, port=None):
    """A Python side on a thread: `handle(conn)` once a connection is made
    (listening when `port` is None, else dialling it); its answers in a
    list, with the listening port when it listens."""
    got = []
    srv = None
    if port is None:
        srv = socket.socket()
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)
        got.append(srv.getsockname()[1])

    def go():
        if srv is not None:
            srv.settimeout(10)
            conn, _ = srv.accept()
            srv.close()
        else:
            t0 = time.monotonic()
            while True:
                try:
                    conn = socket.create_connection(("127.0.0.1", port), timeout=10)
                    break
                except OSError:
                    if time.monotonic() - t0 > 10:
                        raise
                    time.sleep(0.02)
        with conn:
            got.append(handle(conn))

    th = threading.Thread(target=go, daemon=True)
    th.start()
    return got, th


def _read_all(conn):
    data = b""
    while True:
        b = conn.recv(256)
        if not b:
            return data
        data += b


def test_nc_connects_and_carries_both_ways(term):
    _ws, app = term

    def handle(conn):
        data = _read_all(conn)
        conn.sendall(b"world\n")
        return data

    got, th = _peer(handle)
    lines = out(app, "echo hello | elf.nc/nc 127.0.0.1 %d; echo status $?" % got[0])
    th.join(5)
    assert got[1] == b"hello\n"
    assert lines == ["world", "status 0"]


def test_nc_listens_and_takes_one_connection(term):
    _ws, app = term
    port = _port()

    def handle(conn):
        conn.sendall(b"pong\n")
        return _read_all(conn)

    got, th = _peer(handle, port)
    lines = out(app, "echo ping | elf.nc/nc -l -p %d; echo status $?" % port)
    th.join(5)
    assert got == [b"ping\n"]
    assert lines == ["pong", "status 0"]


def test_nc_reports_a_refused_connection(term):
    _ws, app = term
    port = _port()
    got = out(app, "elf.nc/nc 127.0.0.1 %d; echo status $?" % port)
    assert "".join(got[:-1]) == "nc: connect to 127.0.0.1 port %d: Connection refused" % port
    assert got[-1] == "status 1"


def test_sockets_need_the_network_permission(term):
    _ws, app = term
    assert out(app, "elf.ncblind/ncblind -l 1; echo status $?") == [
        "nc: socket: Permission denied", "status 1"]


def test_the_first_socket_holds_the_radio_until_the_end(term, monkeypatch):
    ws, app = term
    calls = []
    monkeypatch.setattr(ws, "wifi_hold", lambda tag, pid: calls.append(("hold", tag, pid)) or True)
    monkeypatch.setattr(ws, "wifi_release", lambda tag, pid: calls.append(("release", tag, pid)))
    seen = []

    def handle(conn):
        seen.append(list(calls))
        return _read_all(conn)

    got, th = _peer(handle)
    assert out(app, "echo x | elf.nc/nc 127.0.0.1 %d; echo status $?" % got[0]) == [
        "status 0"]
    th.join(5)
    assert len(calls) == 2 and calls[0][2] > 1             # held under the process's pid
    pid = calls[0][2]
    assert seen == [[("hold", "native", pid)]]
    assert calls == [("hold", "native", pid), ("release", "native", pid)]
    assert not ws.runtimes["elf"].netted
    calls.clear()
    assert out(app, "elf.hello/hello; echo status $?")[-1] == "status 0"
    assert calls == []


def test_ctrl_c_ends_a_blocked_accept_and_sweeps_its_socket(term):
    from runtime import elf_binding, moy_sh
    _ws, app = term
    port = _port()
    before = elf_binding.stats()
    app.face.set_line("elf.nc/nc -l -p %d" % port)
    app.face.submit()
    t0 = time.monotonic()
    while time.monotonic() - t0 < 0.5:
        moy_sh.jobs()
        time.sleep(0.002)
    assert app.face.busy()
    app.face.interrupt()
    t0 = time.monotonic()
    while app.face.busy() and time.monotonic() - t0 < 10:
        moy_sh.jobs()
        time.sleep(0.002)
    assert not app.face.busy() and app.face.status() == 130
    after = elf_binding.stats()
    assert after["leaked"] == before["leaked"]
    assert after["swept"] >= before["swept"] + 1
    s = socket.socket()
    s.bind(("127.0.0.1", port))         # the listening socket is gone
    s.close()
