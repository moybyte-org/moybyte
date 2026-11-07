"""Keystroke->echo over a board's /term WebSocket (native/moy_tty/moy_tty_ws.c),
the plain-HTTP round trip a request-per-key design would pay, a few commands'
time to the prompt (neofetch twice), Ctrl-C on a sleep, top and edit. The
board's web console is on (the dev channel's `web` prints the paired url):
    python3 experiments/webterm/ws_probe.py http://IP/?pin=NNNN"""
import base64, os, socket, statistics, sys, time, urllib.parse, urllib.request

url = urllib.parse.urlparse(sys.argv[1])
host, port = url.hostname, url.port or 80
pin = urllib.parse.parse_qs(url.query).get("pin", [""])[0]
N = int(os.environ.get("N", "40"))


def pct(xs):
    xs = sorted(xs)
    return "n=%d min %.1f med %.1f p90 %.1f max %.1f ms" % (
        len(xs), xs[0], statistics.median(xs), xs[int(len(xs) * 0.9) - 1], xs[-1])


# 1. a fresh HTTP request per key: GET /sync (open, answered in C at the poll)
http = []
for _ in range(N):
    t = time.perf_counter()
    urllib.request.urlopen("http://%s:%d/sync" % (host, port), timeout=5).read()
    http.append((time.perf_counter() - t) * 1000)
print("HTTP GET /sync          ", pct(http))


class WS:
    def __init__(self, cols=80, rows=24):
        self.s = socket.create_connection((host, port), timeout=10)
        self.s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        key = base64.b64encode(os.urandom(16)).decode()
        self.s.sendall(("GET /term?pin=%s&cols=%d&rows=%d HTTP/1.1\r\nHost: %s\r\n"
                        "Upgrade: websocket\r\nConnection: Upgrade\r\n"
                        "Sec-WebSocket-Key: %s\r\nSec-WebSocket-Version: 13\r\n\r\n"
                        % (pin, cols, rows, host, key)).encode())
        head = b""
        while b"\r\n\r\n" not in head:
            head += self.s.recv(1)
        assert b" 101 " in head.split(b"\r\n")[0], head
        self.buf = b""

    def send(self, data, op=2):
        m = os.urandom(4)
        n = len(data)
        h = bytes([0x80 | op]) + (bytes([0x80 | n]) if n < 126 else bytes([0xFE, n >> 8, n & 255]))
        self.s.sendall(h + m + bytes(b ^ m[i & 3] for i, b in enumerate(data)))

    def recv_frame(self, timeout):
        self.s.settimeout(timeout)
        while True:
            if len(self.buf) >= 2:
                n = self.buf[1] & 0x7F
                hn = 2
                if n == 126 and len(self.buf) >= 4:
                    n, hn = self.buf[2] << 8 | self.buf[3], 4
                if n != 126 and len(self.buf) >= hn + n:
                    p = self.buf[hn:hn + n]
                    self.buf = self.buf[hn + n:]
                    return p
            c = self.s.recv(4096)
            if not c:
                raise EOFError
            self.buf += c

    def read_until(self, want, timeout=10):
        got = b""
        t = time.perf_counter()
        while want not in got:
            try:
                got += self.recv_frame(max(0.05, timeout - (time.perf_counter() - t)))
            except socket.timeout:
                print("TIMEOUT waiting for", want, "got", len(got), repr(got[-160:]), "buf", repr(self.buf[:40]))
                raise
        return got, (time.perf_counter() - t) * 1000

    def drain(self, quiet=0.3):
        got = b""
        try:
            while True:
                got += self.recv_frame(quiet)
        except socket.timeout:
            return got


t = time.perf_counter()
ws = WS()
first, _ = ws.read_until(b"files$ ", 15)
print("connect -> prompt        %.1f ms" % ((time.perf_counter() - t) * 1000))
echo = []
for i in range(N):
    t = time.perf_counter()
    ws.send(b"a" if i % 2 == 0 else b"\x7f")
    ws.recv_frame(5)
    echo.append((time.perf_counter() - t) * 1000)
print("key -> echo (shell line) ", pct(echo))

runs = {}
for cmd in ("ls", "ps", "neofetch", "neofetch"):
    ws.drain(0.2)
    t = time.perf_counter()
    ws.send(cmd.encode() + b"\r")
    out, ms = ws.read_until(b"files$ ", 15)
    runs[cmd] = (ms, len(out))
    print("%-24s %.1f ms, %d bytes" % (cmd + " -> prompt", ms, len(out)))

ws.drain(0.2)
ws.send(b"sleep 30\r")
time.sleep(1.0)
t = time.perf_counter()
ws.send(b"\x03")
ws.read_until(b"files$ ", 10)
print("Ctrl-C on sleep -> prompt %.1f ms" % ((time.perf_counter() - t) * 1000))

ws.drain(0.2)
t = time.perf_counter()
ws.send(b"top\r")
scr = ws.recv_frame(10)
t1 = (time.perf_counter() - t) * 1000
more = ws.drain(0.4)
print("top -> first frame      %.1f ms; first screen %d bytes" % (t1, len(scr) + len(more)))
end = time.perf_counter() + 5
n = 0
while time.perf_counter() < end:
    try:
        n += len(ws.recv_frame(0.5))
    except socket.timeout:
        pass
print("top steady              %.0f bytes/s" % (n / 5))
ws.send(b"q")
ws.read_until(b"files$ ", 15)

ws.drain(0.2)
t = time.perf_counter()
ws.send(b"edit /tmp_webterm_probe.txt\r")
scr = ws.recv_frame(10)
print("edit -> first frame     %.1f ms" % ((time.perf_counter() - t) * 1000))
ws.drain(0.5)
ed = []
for i in range(N):
    t = time.perf_counter()
    ws.send(b"x" if i % 2 == 0 else b"\x7f")
    ws.recv_frame(5)
    ed.append((time.perf_counter() - t) * 1000)
    time.sleep(0.3)
    ws.drain(0.02)
print("key -> echo (edit)       ", pct(ed))
ws.send(b"\x18")                # ^X, and n: leave without saving
ws.drain(0.5)
ws.send(b"n")
ws.read_until(b"files$ ", 10)
try:
    ws.send(b"exit\r")
except OSError:
    pass
