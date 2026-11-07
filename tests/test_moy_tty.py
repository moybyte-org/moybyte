"""The web terminal on the host (native/moy_tty, docs/os_2026-11/system.md
section 3): the kernel's webhost with a pin, its GET /term upgrade handing the
socket to the bridge, and the board's shell on the face behind it, built with
the host's compiler (native/moy_tty/host/tty_main.c), driven by a WebSocket
client written here.

What it holds: the handshake (the accept key, the `moy.sh` subprotocol); a
wrong pin refused before any upgrade, and a source past its free wrong pins
answered 429 {"error":"wait"} whatever pin it sends, the right one included;
the plain GET /term the page asks why a socket failed; keys in and their echo
out, gathered into few frames; a resize the shell sees; Ctrl-C ending a
command; `exit` closing the socket 1000 with its status; a page that closes
freeing the session for the next; and the face another transport holds
refusing the page with 1013 "busy".
"""

import base64
import hashlib
import json
import os
import shutil
import socket
import struct
import subprocess
import sys
import time

import pytest

sys.path.insert(0, os.path.dirname(__file__))
import test_moy_sh as SH  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NATIVE = os.path.join(ROOT, "native")
TTY = os.path.join(NATIVE, "moy_tty")
NET = os.path.join(NATIVE, "moy_net")
STORE = os.path.join(NATIVE, "moy_store")
SPINE = os.path.join(NATIVE, "moy_spine")
WEB = os.path.join(NATIVE, "moy_web")
PIN = "4321"
FILES = "sd/moybyte/files"

TTY_SOURCES = ("moy_tty.c", "moy_tty_sys.c", "moy_tty_sh.c", "moy_tty_ws.c", "host/tty_main.c")
NET_SOURCES = [os.path.join(NET, n) for n in (
    "moy_http.c", "moy_net_port.c", "moy_sync.c", "moy_sync_apply.c", "moy_webhost.c",
    "moy_ota.c", "moy_webconsole.c", "moy_gpio.c", "moy_dns.c", "moy_net_host.c")]
NET_SOURCES.append(os.path.join(STORE, "moy_journal.c"))

pytestmark = pytest.mark.skipif(shutil.which("cc") is None, reason="needs a C compiler")


def _build(out):
    cc = shutil.which("cc")
    shell = [o for o in SH._objects(cc, str(out))
             if os.path.basename(o) not in ("sh_main.o", "board_main.o")]
    inc = ["-I" + d for d in (TTY, NET, STORE, SPINE, WEB, SH.SH, SH.KERNEL)]
    flags = ["-std=gnu99", "-g", "-O1", "-fsanitize=address,undefined", "-w",
             "-D_GNU_SOURCE"] + inc
    objs, procs = [], []
    for i, src in enumerate([os.path.join(TTY, n) for n in TTY_SOURCES] + NET_SOURCES):
        obj = str(out / ("tty%d.o" % i))
        procs.append((src, subprocess.Popen([cc] + flags + ["-c", src, "-o", obj],
                                            stderr=subprocess.PIPE)))
        objs.append(obj)
    for src, p in procs:
        _o, err = p.communicate()
        assert p.returncode == 0, (src, err.decode())
    exe = str(out / "tty_main")
    subprocess.run([cc] + SH.CFLAGS + ["-o", exe] + objs + shell + ["-lpthread"], check=True)
    return exe


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    out = tmp_path_factory.mktemp("tty")
    return _build(out)


class Host:
    def __init__(self, exe, out):
        tree = out / "tree"
        for sub in ("carts", "files", "system"):
            (tree / "sd" / "moybyte" / sub).mkdir(parents=True, exist_ok=True)
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            self.port = s.getsockname()[1]
        self.tree = tree
        self.logf = open(out / "tty.log", "w+")
        env = dict(os.environ, MOY_SH_ROOT=str(tree), ASAN_OPTIONS="detect_leaks=0")
        self.proc = subprocess.Popen(
            [exe, str(self.port), PIN, str(tree / "sd" / "moybyte" / "carts")],
            stdin=subprocess.PIPE, stdout=self.logf, stderr=subprocess.STDOUT, env=env)
        for _ in range(200):
            if "listening" in self.log():
                break
            time.sleep(0.05)
        assert "listening" in self.log(), self.log()

    def log(self):
        self.logf.seek(0)
        return self.logf.read()

    def say(self, line):
        self.proc.stdin.write(line.encode() + b"\n")
        self.proc.stdin.flush()

    def stats(self):
        lines = [ln for ln in self.log().splitlines() if ln.startswith("STATS ")]
        return dict(kv.split("=") for kv in lines[-1].split()[1:])

    def end(self):
        self.proc.kill()
        self.proc.wait()
        self.logf.close()


@pytest.fixture
def host(built, tmp_path):
    h = Host(built, tmp_path)
    yield h
    h.end()


def get(h, target):
    """A plain request: (status, body)."""
    with socket.create_connection(("127.0.0.1", h.port), timeout=5) as c:
        c.sendall(("GET %s HTTP/1.1\r\nHost: x\r\n\r\n" % target).encode())
        data = b""
        while True:
            k = c.recv(4096)
            if not k:
                break
            data += k
    head, _, body = data.partition(b"\r\n\r\n")
    return int(head.split()[1]), body


class WS:
    """A page's WebSocket: the upgrade, masked frames out, frames in."""

    def __init__(self, h, pin=PIN, cols=80, rows=24, proto="moy.sh"):
        self.c = socket.create_connection(("127.0.0.1", h.port), timeout=5)
        key = base64.b64encode(os.urandom(16)).decode()
        req = ("GET /term?pin=%s&cols=%d&rows=%d HTTP/1.1\r\nHost: x\r\nUpgrade: websocket\r\n"
               "Connection: Upgrade\r\nSec-WebSocket-Key: %s\r\nSec-WebSocket-Version: 13\r\n"
               % (pin, cols, rows, key))
        if proto:
            req += "Sec-WebSocket-Protocol: %s\r\n" % proto
        self.c.sendall((req + "\r\n").encode())
        data = b""
        while b"\r\n\r\n" not in data:
            k = self.c.recv(4096)
            if not k:
                break
            data += k
        head, _, self.buf = data.partition(b"\r\n\r\n")
        self.head = head.decode()
        self.status = int(self.head.split()[1]) if self.head else 0
        self.accept = base64.b64encode(hashlib.sha1(
            (key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()).digest()).decode()
        self.frames = 0
        self.closed = None

    def send(self, data, op=2):
        if isinstance(data, str):
            data = data.encode()
        mask = os.urandom(4)
        n = len(data)
        head = bytes([0x80 | op])
        head += bytes([0x80 | n]) if n < 126 else bytes([0x80 | 126]) + struct.pack(">H", n)
        self.c.sendall(head + mask + bytes(b ^ mask[i & 3] for i, b in enumerate(data)))

    def _frame(self):
        while True:
            if len(self.buf) >= 2:
                n, hn = self.buf[1] & 0x7F, 2
                if n == 126 and len(self.buf) >= 4:
                    n, hn = struct.unpack(">H", self.buf[2:4])[0], 4
                if n != 126 and len(self.buf) >= hn + n:
                    op, pl = self.buf[0] & 0x0F, self.buf[hn:hn + n]
                    self.buf = self.buf[hn + n:]
                    return op, pl
            k = self.c.recv(65536)
            if not k:
                return None
            self.buf += k

    def read_until(self, want, timeout=5.0):
        """The face's bytes until `want` shows (or the socket closes)."""
        got = b""
        self.c.settimeout(timeout)
        end = time.monotonic() + timeout
        while want not in got and time.monotonic() < end:
            try:
                f = self._frame()
            except socket.timeout:
                break
            if f is None:
                break
            op, pl = f
            if op == 8:
                self.closed = (struct.unpack(">H", pl[:2])[0], pl[2:].decode()) if pl else (1005, "")
                break
            if op == 2:
                self.frames += 1
                got += pl
        return got

    def close(self):
        try:
            self.send(b"\x03\xe8", op=8)
        except OSError:
            pass
        self.c.close()


def wait_stat(h, key, value, timeout=5.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if h.stats().get(key) == str(value):
            return
        time.sleep(0.05)
    assert h.stats().get(key) == str(value), h.log()[-2000:]


def test_the_upgrade_answers_the_key_and_the_subprotocol(host):
    ws = WS(host)
    assert ws.status == 101, ws.head
    assert "Sec-WebSocket-Accept: %s" % ws.accept in ws.head
    assert "Sec-WebSocket-Protocol: moy.sh" in ws.head
    assert b"$ " in ws.read_until(b"$ ")
    ws.close()
    plain = WS(host, proto=None)
    assert plain.status == 101 and "Sec-WebSocket-Protocol" not in plain.head
    plain.close()


def test_a_wrong_pin_is_refused_before_any_upgrade(host):
    ws = WS(host, pin="0000")
    assert ws.status == 403
    assert get(host, "/term?pin=0000") == (403, b'{"error":"pin"}')
    assert get(host, "/term?pin=%s" % PIN) == (200, b'{"term":1}')
    assert host.stats()["sessions"] == "0"


def test_a_source_past_its_free_pins_waits_whatever_pin_it_sends(host):
    for _ in range(10):
        assert get(host, "/term?pin=0000")[0] == 403
    status, body = get(host, "/term?pin=0000")
    assert status == 403
    status, body = get(host, "/term?pin=%s" % PIN)
    assert status == 429, body
    doc = json.loads(body)
    assert doc["error"] == "wait" and 0 < doc["ms"] <= 1000
    ws = WS(host)
    assert ws.status == 429


def test_keys_go_in_and_their_echo_comes_back_in_few_frames(host):
    ws = WS(host)
    ws.read_until(b"$ ")
    frames = ws.frames
    for ch in b"echo hello":
        ws.send(bytes([ch]))
        assert ws.read_until(bytes([ch]), 2.0).endswith(bytes([ch]))
    ws.send(b"\r")
    out = ws.read_until(b"hello\r\n")
    assert b"hello\r\n" in out
    ws.read_until(b"$ ")
    wait_stat(host, "in", 11)
    s = host.stats()
    # an echo is a frame; a command's output and its prompt come gathered
    assert ws.frames - frames <= 14, s
    ws.close()


def test_a_resize_reaches_the_shell(host):
    """A full-screen app lays out on the page's size: `top`'s rows run to the
    resized terminal's last."""
    ws = WS(host, cols=80, rows=24)
    ws.read_until(b"$ ")
    ws.send('{"size":[100,30]}', op=1)
    time.sleep(0.4)
    ws.send(b"top\r")
    out = ws.read_until(b"\x1b[30;1H", 5.0)
    assert b"\x1b[30;1H" in out and b"\x1b[31;1H" not in out, out[-400:]
    ws.send(b"q")
    assert b"$ " in ws.read_until(b"$ ", 5.0)
    ws.close()


def test_ctrl_c_ends_a_command_and_exit_closes_with_its_status(host):
    ws = WS(host)
    ws.read_until(b"$ ")
    ws.send(b"sleep 30\r")
    time.sleep(0.5)
    t0 = time.monotonic()
    ws.send(b"\x03")
    out = ws.read_until(b"$ ", 5.0)
    assert b"^C" in out and time.monotonic() - t0 < 2.0, out
    ws.send(b"exit 3\r")
    ws.read_until(b"\xff", 5.0)
    assert ws.closed == (1000, "exit 3"), ws.closed
    wait_stat(host, "live", 0)


def test_a_page_that_closes_frees_the_session_for_the_next(host):
    ws = WS(host)
    ws.read_until(b"$ ")
    ws.close()
    wait_stat(host, "live", 0)
    again = WS(host)
    assert again.status == 101
    assert b"$ " in again.read_until(b"$ ")
    again.close()
    wait_stat(host, "sessions", 2)
    wait_stat(host, "live", 0)


def test_a_face_another_transport_holds_refuses_the_page_busy(host):
    host.say("ssh")
    end = time.monotonic() + 5
    while "SSH 0" not in host.log() and time.monotonic() < end:
        time.sleep(0.05)
    ws = WS(host)
    assert ws.status == 101
    ws.read_until(b"\xff", 5.0)
    assert ws.closed == (1013, "busy"), ws.closed
    host.say("nossh")
    end = time.monotonic() + 5
    while "NOSSH" not in host.log() and time.monotonic() < end:
        time.sleep(0.05)
    again = WS(host)
    assert b"$ " in again.read_until(b"$ ")
    again.close()


def test_neofetch_twice_in_one_session_is_whole_both_times(host):
    """The spike's 379-byte stop: a hop's held output sent in pieces the
    loopback dropped. Both runs end at the prompt, the same length."""
    ws = WS(host, cols=100, rows=30)
    ws.read_until(b"$ ")
    runs = []
    for _ in range(2):
        ws.send(b"neofetch\r")
        runs.append(ws.read_until(b"$ ", 5.0))
    assert all(r.endswith(b"$ ") and b"host harness" in r for r in runs), runs
    assert len(runs[0]) == len(runs[1]), runs
    ws.close()


def test_tab_completes_on_the_web_face_too(host):
    (host.tree / FILES / "webby.txt").write_text("mew\n")
    ws = WS(host)
    ws.read_until(b"$ ")
    ws.send(b"cat web\t")
    assert b"by.txt " in ws.read_until(b"by.txt ")
    ws.send(b"\r")
    assert b"mew" in ws.read_until(b"mew")
    ws.close()
