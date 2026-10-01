"""`tools/push_cart.py` -- the upload protocol, and the facts it must read.

Never imported by anything (#208), and the protocol it drives is the dev
channel's RAW receive (`recv`, `runtime/dev_channel.py`'s `_recv`): the host
writes one window of 8-bit payload, waits for the board's ack, and renames the
`.new` only once the board's read-back sha256 agrees. **There is no base64
chunk push** -- one transport, so an image that predates `recv` is refused by
name rather than served slowly.

The board here is a fake console. It EVALUATES the `py` lines the tool still
sends -- the already-current hash, the mkdir, the remove/rename -- the way
`dev_channel` does, against an in-memory filesystem: fresh env per command,
eval falling back to exec, `PY ERR <exc>` on a raise. And it speaks the raw
protocol: armed by a line, then fed bytes, acking every window and hashing the
file it wrote.

The failures modelled are the ones the wire actually produces -- a byte the
P4's ring dropped, a byte that arrived flipped, a host that died inside a
window -- with the board's five-second wait compressed out, because the shape
under test is what the tool DOES about each, not how long it waits.
"""

import builtins as _builtins
import hashlib
import os
import shutil
import sys
import types

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))

import p4_autotest                                              # noqa: E402
import push_cart                                                # noqa: E402
from runtime.dev_channel import (RECV_DEAD_WINDOWS,              # noqa: E402
                                 RECV_RETRIES, RECV_SYNC)

BOARD_DIRS = {
    "p4": os.path.join(ROOT, "firmware", "esp32_p4_wifi6_touch_lcd_7b"),
    "tdeck": os.path.join(ROOT, "firmware", "lilygo_t_deck_plus_mainline"),
    "guition_s3": os.path.join(ROOT, "firmware", "guition_jc3248w535"),
    "guition_p4": os.path.join(ROOT, "firmware", "guition_jc8012p4a1c"),
}


class _FakeFile:
    def __init__(self, fs, path, mode):
        self.fs, self.path, self.mode = fs, path, mode
        self.pos = 0
        if "w" in mode:
            self.buf = b""
        elif path in fs.files:
            self.buf = fs.files[path]
        else:
            raise OSError("ENOENT: " + path)

    def write(self, data):
        self.buf += data
        return len(data)

    def read(self, n=-1):
        # A REAL cursor, not "return the whole buffer every call": `_sha`
        # (push_cart.HELPERS) reads a file in 8KB pieces, and a mock that
        # answered every `read(8192)` with the full buffer would either loop
        # forever or hide exactly the bug a chunked reader exists to avoid.
        if n is None or n < 0:
            out = self.buf[self.pos:]
        else:
            out = self.buf[self.pos:self.pos + n]
        self.pos += len(out)
        return out

    def close(self):
        if "w" in self.mode:
            self.fs.files[self.path] = (self.buf + b"!" if self.fs.corrupt
                                        else self.buf)


class _FakeFS:
    """The device's storage: `open` plus the os verbs the tool reaches for.
    `corrupt` appends a byte to every file that is closed, which is what a
    dropped upload chunk looks like from up here -- a hash that does not match.
    `free` is the room the store reports (`statvfs`); None is a board that
    cannot say."""

    def __init__(self, files=None, corrupt=False, free=None):
        self.files = dict(files or {})
        self.dirs = set()
        self.corrupt = corrupt
        self.free = free

    def statvfs(self, path):
        if self.free is None:
            raise OSError("EINVAL")
        return (4096, 4096, 1024, self.free // 4096, self.free // 4096,
                0, 0, 0, 0, 255)

    def stat(self, path):
        if path not in self.files:
            raise OSError("ENOENT: " + path)
        return (0x8000, 0, 0, 0, 0, 0, len(self.files[path]), 0, 0, 0)

    def open(self, path, mode="r"):
        return _FakeFile(self, path, mode)

    def mkdir(self, path):
        if path in self.dirs:
            raise OSError("EEXIST: " + path)
        self.dirs.add(path)

    def remove(self, path):
        if path not in self.files:
            raise OSError("ENOENT: " + path)
        del self.files[path]

    def rename(self, src, dst):
        self.files[dst] = self.files.pop(src)


class _WS:
    def __init__(self, carts_root):
        self.carts_root = carts_root
        self.wm = None


class _FakeConsole:
    """A board on the far end of the serial line.

    Two protocols, the way the console has them. `py` lines mirror
    `dev_channel`'s handler exactly -- a FRESH env per command (which is why
    the tool stashes its helpers in `ws._g`), eval with an exec fallback for
    statements, and the device's own words on a raise. `recv` mirrors `_recv`:
    a line arms it, everything after that line is payload until `n` bytes have
    landed, an ack goes out after each window's file write, and the `done` line
    carries the hash of the file AS STORED (not of the bytes received -- which
    is what makes the `corrupt` case a mismatch rather than an agreement).

    `has_recv=False` is an image from before the command: it answers
    `REMOTE ? recv` from the same dispatcher, and since the base64 chunk push
    was deleted the tool has nothing to fall back to and must refuse.

    `rate` is a console UART the board can switch (the Waveshare's), at that
    rate; `recv rate=` then runs the payload at another, with the syncs. The
    wire carries only what both ends say at the same rate -- `baudrate` is the
    HOST's, which the tool sets -- and garbles the rest. `rate_deaf` is a
    board that switches and never hears the host at the new rate.
    """

    def __init__(self, board="p4", carts_root="/moy/carts", files=None,
                 corrupt=False, has_recv=True, max_window=32768,
                 drop_at=None, flip_at=None, stall_at=None, free=None,
                 full_at=None, rate=None, rate_deaf=False):
        self.port = "/dev/fake"
        self.baudrate = rate or 115200  # the host's end, as the tool sets it
        self.write_timeout = 2.0
        self.console = rate             # None: a board that cannot switch
        self.rate = rate or 115200      # the board's end
        self.rate_deaf = rate_deaf
        self.switches = []
        self.fs = _FakeFS(files, corrupt, free)
        self.full_at = full_at          # the store runs out of room here
        self.ws = _WS(carts_root)
        self.board = board
        self.has_recv = has_recv        # False = an image from before `recv`
        self.max_window = max_window
        self.drop_at = drop_at          # a byte the ring swallowed: not counted
        self.flip_at = flip_at          # a byte that arrived wrong: counted
        self.stall_at = stall_at        # the host stops writing here
        self.sent = []          # every complete line the tool wrote
        self.said = []          # every line this board answered with
        self.acks = []          # the byte counts `recv` acked, in order
        self.closed = 0
        self._rx = None         # the live `recv`, when one is armed
        self._in = b""
        self._out = b""
        self._builtins = {k: getattr(_builtins, k) for k in dir(_builtins)}
        self._builtins["open"] = self.fs.open
        self._builtins["__import__"] = self._import

    # -- the wire ---------------------------------------------------------

    def write(self, data):
        if self.baudrate != self.rate:
            if (self._rx is not None and "sync" in self._rx
                    and self.baudrate == self.console):
                # The host gave the rate up, and its wait is the longer one:
                # by now the board has given up too and is back at its own --
                # and said so while the host was still listening at the
                # payload rate, so nobody heard it.
                self.said.append("RECV ERR no sync at %d" % self._rx["fast"])
                self._set_rate(self.console)
                self.fs.files.pop(self._rx["tmp"], None)
                self._rx = None
            else:
                data = b"\xfe" * len(data)   # what a mismatched UART makes
        if self._rx is not None:            # armed by `recv`: this is payload
            self._feed(data)
            return len(data)
        self._in += data                    # the writer PACES long lines
        while b"\n" in self._in:
            raw, self._in = self._in.split(b"\n", 1)
            self._run(raw.decode("utf-8", "replace"))
        return len(data)

    def flush(self):
        pass

    def read(self, n):
        out, self._out = self._out[:n], self._out[n:]
        return out

    def close(self):
        self.closed += 1

    # -- the console ------------------------------------------------------

    def _import(self, name, *rest):
        if name == "os":
            return self.fs
        if name == "_ota_build":
            return types.SimpleNamespace(BOARD=self.board)
        return __import__(name, *rest)

    def _recv(self, line, parts):
        """`recv` -- dev_channel's raw receive, from the board's end."""
        if not self.has_recv:
            # The dispatcher's own answer on an image without the command, and
            # the whole capability handshake: a positive no.
            return self._say("REMOTE ? " + line)
        if len(parts) < 4:
            return self._say("RECV caps max=%d idle=5000%s"
                             % (self.max_window, " rate=%d" % self.console
                                if self.console else ""))
        total, window = int(parts[1]), int(parts[2])
        if window > self.max_window:
            return self._say("RECV ERR window %d (max %d)"
                             % (window, self.max_window))
        path = line.split(None, 3)[3]
        fast = None
        if path.startswith("rate="):
            tok, _, path = path.partition(" ")
            if self.console:
                fast = int(tok[5:])
        tmp = path + ".new"
        f = self.fs.open(tmp, "wb")
        self._say("RECV ready %d %d %s%s"
                  % (total, window, "rate=%d " % fast if fast else "", tmp))
        if total == 0:                  # an empty file: nothing to wait for
            f.close()
            return self._say("RECV done %s 0"
                             % hashlib.sha256(b"").hexdigest()[:12])
        self._rx = {"n": total, "window": window, "tmp": tmp, "got": 0,
                    "sent": 0, "f": f, "buf": bytearray(), "left":
                    RECV_RETRIES, "empty": 0}
        if fast:
            self._set_rate(fast)
            self._rx.update(sync=b"", fast=fast)

    def _set_rate(self, rate):
        self.rate = rate
        self.switches.append(rate)

    def _synced(self, rx, key, data):
        """Scan for RECV_SYNC under `key`; the bytes after it, or None."""
        rx[key] += data
        if self.rate_deaf or RECV_SYNC not in rx[key]:
            return None
        rest = rx[key].split(RECV_SYNC, 1)[1]
        del rx[key]
        return rest

    def _feed(self, data):
        """A window at a time, because that is what the board COMMITS.

        The real `_recv` fills a buffer and writes it whole, which is the only
        reason a short window can be thrown away and asked for again -- the
        file is always on a window boundary. Writing byte-by-byte here would
        model a board that cannot retry."""
        rx = self._rx
        if "sync" in rx:
            data = self._synced(rx, "sync", data)
            if data is None:
                return
            self._say("RECV sync %d" % rx["fast"])
            if not data:
                return
        if "end" in rx:
            if self._synced(rx, "end", data) is not None:
                self._done(rx)
            return
        acked = False
        for byte in data:
            i = rx["sent"]
            rx["sent"] += 1
            if self.stall_at is not None and i >= self.stall_at:
                continue                    # the host died: nothing arrives
            if i == self.drop_at:
                continue                    # the ring dropped it, silently
            if i == self.flip_at:
                byte ^= 0xFF                # a framing error: count intact
            rx["buf"].append(byte)
            want = rx["window"]
            if rx["n"] - rx["got"] < want:
                want = rx["n"] - rx["got"]
            if len(rx["buf"]) < want:
                continue
            if self.full_at is not None and rx["got"] + want > self.full_at:
                # dev_channel's answer when the store refuses a write: the
                # tmp goes and the words say why, plainly.
                self._rx = None
                self.fs.files.pop(rx["tmp"], None)
                self._say("RECV ERR store full after %d of %d bytes"
                          % (rx["got"], rx["n"]))
                return self._back(rx)
            rx["f"].write(bytes(rx["buf"]))
            rx["got"] += len(rx["buf"])
            del rx["buf"][:]
            rx["empty"] = 0
            acked = True
            self.acks.append(rx["got"])
            self._say("RECV ack %d" % rx["got"])
            if rx["got"] == rx["n"]:
                if rx.get("fast"):
                    # Back at the console rate behind the last ack; `done`
                    # waits for the host's sync at that rate.
                    self._set_rate(self.console)
                    rx["end"] = b""
                    return
                return self._done(rx)
        if self._rx is None or (acked and not rx["buf"]):
            return                          # on a boundary: the host's turn
        # The host has stopped writing with this window short, so the byte the
        # board is waiting on is never coming: on glass that is the idle
        # timeout, RECV_IDLE_MS later. The wait is what is compressed here.
        # Nothing of this window reached the file, so `got` is still a boundary
        # and the board can ask for it again rather than lose the cart.
        rx["empty"] = rx["empty"] + 1 if not rx["buf"] else 0
        del rx["buf"][:]
        rx["left"] -= 1
        if rx["left"] < 0 or rx["empty"] >= RECV_DEAD_WINDOWS:
            self._rx = None
            rx["f"].close()
            self.fs.files.pop(rx["tmp"], None)
            self._say("RECV ERR timeout after %d of %d bytes"
                      % (rx["got"], rx["n"]))
            return self._back(rx)
        self._say("RECV retry %d" % rx["got"])

    def _back(self, rx):
        """After an ERR said at the payload rate, the console's own again."""
        if rx.get("fast") and self.rate != self.console:
            self._set_rate(self.console)

    def _done(self, rx):
        self._rx = None
        rx["f"].close()                     # `corrupt` lands here
        self._say("RECV done %s %d"
                  % (hashlib.sha256(self.fs.files.get(rx["tmp"], b""))
                     .hexdigest()[:12], rx["got"]))

    def _run(self, line):
        self.sent.append(line)
        parts = line.split()
        if parts and parts[0] == "recv":
            return self._recv(line, parts)
        if not line.startswith("py "):
            # dev_channel's fallthrough, and load-bearing: it is how the tool
            # learns a board does NOT have a command.
            return self._say("REMOTE ? " + line)
        code = line[3:]
        env = {"ws": self.ws, "wm": self.ws.wm, "pointer": None,
               "__builtins__": self._builtins}
        try:
            try:
                value = repr(eval(code, env))                    # noqa: S307
            except SyntaxError:
                exec(code, env)                                  # noqa: S102
                value = "ok"
        except Exception as exc:                                 # noqa: BLE001
            return self._say("PY ERR %s: %s" % (type(exc).__name__, exc))
        self._say("PY " + value)

    def _say(self, text):
        self.said.append(text)
        if self.rate != self.baudrate:
            self._out += b"\xfe" * len(text) + b"\r\n"
            return
        self._out += text.encode() + b"\r\n"

    @property
    def uploaded(self):
        """Payload bytes this board took."""
        return (self.acks[-1:] or [0])[-1]


def _driver(device, board="p4"):
    b = p4_autotest.P4Board(None, ser=device, board_dir=BOARD_DIRS[board])
    b.CHUNK = int(push_cart.serial_cfg(board)["chunk"])
    return b


def _cart(tmp_path, files):
    d = tmp_path / "demo.moy"
    d.mkdir()
    for name, data in files.items():
        (d / name).write_bytes(data)
    return str(d)


SOURCE = b"".join(bytes([i % 251]) for i in range(700))
SHA = hashlib.sha256(SOURCE).hexdigest()[:12]


def _install_helpers(b):
    assert b.pyexec(push_cart.HELPERS) is True


# Bigger than the P4's declared window, so the windowing is exercised rather
# than asserted about: 700 bytes would be one window on every board.
BIG = bytes((i * 7 + i // 251) % 256 for i in range(10000))
BIG_SHA = hashlib.sha256(BIG).hexdigest()[:12]


def _raw(dev, board="p4"):
    b = _driver(dev, board)
    _install_helpers(b)
    return b, int(push_cart.serial_cfg(board)["window"])


# -- the declarations the tool reads ------------------------------------------


def test_the_boards_are_discovered_from_the_board_files():
    """A hand-kept map here would be the fourth list of the boards and the one
    that rots silently: a board missing from it simply cannot be pushed to."""
    found = push_cart._boards()
    for name, board_dir in BOARD_DIRS.items():
        assert found[name] == os.path.relpath(board_dir, ROOT)
    assert "web_runner" not in found
    assert "web_runner" not in " ".join(found.values())


def test_an_unknown_board_names_the_ones_it_knows():
    with pytest.raises(SystemExit) as exc:
        push_cart.serial_cfg("tdek")
    assert "tdek" in str(exc.value) and "guition_s3" in str(exc.value)


def test_a_board_with_no_serial_declaration_is_refused(monkeypatch, tmp_path):
    """Not defaulted on purpose: a wrong guess either chip-resets the board
    mid-write or silently truncates the upload."""
    board_dir = tmp_path / "firmware" / "mystery"
    board_dir.mkdir(parents=True)
    (board_dir / "board.toml").write_text(
        '[board]\nchip = "esp32s3"\nota = "mystery"\n', encoding="utf-8")
    monkeypatch.setattr(push_cart, "ROOT", str(tmp_path))
    monkeypatch.setattr(push_cart, "BOARDS", {"mystery": "firmware/mystery"})
    with pytest.raises(SystemExit) as exc:
        push_cart.serial_cfg("mystery")
    assert "no [serial] section" in str(exc.value)


def test_each_board_reads_its_own_transport():
    assert push_cart.serial_cfg("p4")["attach_only"] is False
    assert push_cart.serial_cfg("tdeck")["attach_only"] is True
    assert push_cart.serial_cfg("p4")["chunk"] != \
        push_cart.serial_cfg("tdeck")["chunk"]


def test_the_board_argument_is_required(tmp_path):
    """A default is a silent wrong transport on every board but one."""
    cart = _cart(tmp_path, {"main.py": b"x = 1\n"})
    with pytest.raises(SystemExit) as exc:
        push_cart.main([cart])
    assert exc.value.code == 2


# -- push_file_raw: the raw upload protocol -----------------------------------


@pytest.mark.parametrize("board", sorted(BOARD_DIRS))
def test_the_raw_upload_is_windowed_at_the_boards_own_declaration(
        tmp_path, board):
    """The host may not run ahead of the ack, and how far ahead it may run is
    board.toml's call: 3072 on the P4, whose UART has no flow control, so its
    ack is the only backpressure there is and a window has to fit its stdin
    ring; 16384 on the USB boards, where the window buys round trips rather
    than safety."""
    dev = _FakeConsole(board=board)
    b, window = _raw(dev, board)
    src = _cart(tmp_path, {"main.lua": BIG}) + "/main.lua"
    dst = "/moy/carts/demo.moy/main.lua"
    assert push_cart.push_file_raw(b, src, dst, push_cart.Link(window)) is True
    assert dev.fs.files == {dst: BIG}
    assert dev.acks == [min((k + 1) * window, len(BIG))
                        for k in range((len(BIG) + window - 1) // window)]


def test_the_raw_upload_carries_every_byte_value(tmp_path):
    """8 BITS, no base64. The interrupt char and both newline bytes are in
    here: on glass they are the ones a text stream or an RX ISR eats, which is
    what dev_channel's `_recv` turns kbd_intr off and reads stdin.buffer for."""
    payload = bytes(range(256)) * 20
    dev = _FakeConsole()
    b, window = _raw(dev)
    src = _cart(tmp_path, {"main.lua": payload}) + "/main.lua"
    push_cart.push_file_raw(b, src, "/moy/carts/demo.moy/main.lua", push_cart.Link(window))
    assert dev.fs.files["/moy/carts/demo.moy/main.lua"] == payload


def test_a_byte_the_ring_dropped_costs_its_window_not_the_cart(tmp_path):
    """A UART's failure, exactly: a byte arrives with the stdin ring full and
    is gone with no error. The board is then one byte short of the window for
    ever and its idle timeout fires -- but nothing of that window reached the
    file, so it asks for the window again instead of losing the cart.

    Measured on the P4's stock 260-byte ring before this existed: a handful of
    bytes lost about once every 300 windows, which failed a 120KB push one
    push in five, on the only transport a cart has to that board."""
    dst = "/moy/carts/demo.moy/main.lua"
    dev = _FakeConsole(files={dst: b"the cart that still works\n"},
                       drop_at=5000)
    b, window = _raw(dev)
    src = _cart(tmp_path, {"main.lua": BIG}) + "/main.lua"
    assert push_cart.push_file_raw(b, src, dst, push_cart.Link(window)) is True
    assert dev.fs.files[dst] == BIG                     # byte-exact, hash agreed
    # The boundaries come from the board's DECLARED window, not a number typed
    # here: that value is a tuning knob (the P4's board.toml carries its
    # measurements), and a test that pins it fails on the day it moves
    # while saying nothing about the retry this is here to check.
    assert [l for l in dev.said if l.startswith("RECV retry")] == [
        "RECV retry %d" % (5000 // window * window)]     # the window it was in
    # and the re-send is the ONLY extra work: every window still acks once
    assert dev.acks == list(range(window, len(BIG), window)) + [len(BIG)]


def test_a_byte_that_arrived_wrong_is_caught_by_the_hash(tmp_path):
    """A framing error, where the count still adds up: every window acks, the
    board reports what it wrote, and the hash is what disagrees. The .new goes
    and the cart that works stays -- the same guarantee the chunk path has."""
    dst = "/moy/carts/demo.moy/main.lua"
    dev = _FakeConsole(files={dst: b"the cart that still works\n"},
                       flip_at=1234)
    b, window = _raw(dev)
    src = _cart(tmp_path, {"main.lua": BIG}) + "/main.lua"
    with pytest.raises(RuntimeError) as exc:
        push_cart.push_file_raw(b, src, dst, push_cart.Link(window))
    assert "main.lua" in str(exc.value) and BIG_SHA in str(exc.value)
    assert dev.fs.files == {dst: b"the cart that still works\n"}


def test_a_host_that_dies_inside_a_window_leaves_the_board_and_the_cart_whole(
        tmp_path):
    """The board-side timeout from the other end: the host stopped writing
    mid-window (a Ctrl-C, a dead cable), so the board gives up on its own,
    removes the tmp and prints why -- rather than parking the frame loop in a
    blocking read that no byte will ever finish."""
    dst = "/moy/carts/demo.moy/main.lua"
    dev = _FakeConsole(files={dst: b"the cart that still works\n"},
                       stall_at=6000)
    b, window = _raw(dev)
    src = _cart(tmp_path, {"main.lua": BIG}) + "/main.lua"
    with pytest.raises(RuntimeError) as exc:
        push_cart.push_file_raw(b, src, dst, push_cart.Link(window))
    assert "main.lua" in str(exc.value)
    # The last WHOLE window, not 6000: the bytes of the short window were
    # thrown away and never reached the file, and naming them sends a reader
    # looking for a cart that does not exist. The board offers the window back
    # first, so a host that is merely quiet is not mistaken for one that
    # dropped a byte.
    assert "%d of %d" % (6000 // window * window, len(BIG)) in str(exc.value)
    assert len([l for l in dev.said if l.startswith("RECV retry")]) \
        == RECV_DEAD_WINDOWS
    assert dev.fs.files == {dst: b"the cart that still works\n"}


def test_the_pushed_bytes_arrive_intact(tmp_path):
    """End to end: armed, windowed, acked, hashed on the board by reading the
    file back, and renamed only then."""
    dev = _FakeConsole()
    b, window = _raw(dev)
    src = _cart(tmp_path, {"main.lua": SOURCE}) + "/main.lua"
    dst = "/moy/carts/demo.moy/main.lua"
    assert push_cart.push_file_raw(b, src, dst, push_cart.Link(window)) is True
    assert dev.fs.files == {dst: SOURCE}


def test_a_push_retires_the_stamped_backup_the_board_kept(tmp_path):
    """moy_fs's invariant (#154): the store leaves a stamped `<file>.bak` beside
    everything it publishes, and it describes the file it published. A push puts
    different bytes there, so leaving that stamp behind would have the board's
    next read "recover" the kid's own last save over what was just pushed."""
    from runtime import moy_fs
    dev = _FakeConsole()
    b, window = _raw(dev)
    src = _cart(tmp_path, {"main.lua": SOURCE}) + "/main.lua"
    dst = "/moy/carts/demo.moy/main.lua"
    kid = "-- the kid's own save, made on the board\n"
    dev.fs.files[dst] = kid
    dev.fs.files[dst + ".bak"] = moy_fs._stamp_line(kid) + kid

    assert push_cart.push_file_raw(b, src, dst, push_cart.Link(window)) is True

    assert dev.fs.files == {dst: SOURCE}


def test_a_first_push_survives_the_remove_of_a_file_that_is_not_there(tmp_path):
    """The pre-rename remove is a no-op by design: on a first push the device
    raises ENOENT and the push must carry on regardless."""
    dev = _FakeConsole()
    b, window = _raw(dev)
    src = _cart(tmp_path, {"main.lua": SOURCE}) + "/main.lua"
    dst = "/moy/carts/demo.moy/main.lua"
    push_cart.push_file_raw(b, src, dst, push_cart.Link(window))
    assert any("_put'](%r, %r)" % (dst + ".new", dst) in line
               for line in dev.sent)
    assert dev.fs.files[dst] == SOURCE


def test_a_file_whose_hash_already_matches_is_not_uploaded(tmp_path):
    """What makes a re-run cheap and a half-finished push resumable."""
    dst = "/moy/carts/demo.moy/main.lua"
    dev = _FakeConsole(files={dst: SOURCE})
    b, window = _raw(dev)
    src = _cart(tmp_path, {"main.lua": SOURCE}) + "/main.lua"
    assert push_cart.push_file_raw(b, src, dst, push_cart.Link(window)) is False
    assert dev.acks == []


def test_a_big_files_already_current_check_reads_in_pieces(tmp_path):
    """`_sha` (push_cart.HELPERS) reads 8KB at a time, not the file whole: a 4MB
    WAD read in one `open(p, 'rb').read()` measured 71s on the Waveshare P4
    against 5.5s chunked -- long enough that `cmd`'s resend doubled the wait and
    starved the `recv` meant to follow it, which is what "did not arm the raw
    upload (no reply)" actually was. BIG is bigger than one 8KB chunk, so this
    exercises more than one `read(8192)` -- a reader that only consumed the
    first piece would hash a truncated prefix and wrongly decide to re-upload."""
    dst = "/moy/carts/demo.moy/main.lua"
    assert len(BIG) > 8192
    dev = _FakeConsole(files={dst: BIG})
    b, window = _raw(dev)
    src = _cart(tmp_path, {"main.lua": BIG}) + "/main.lua"
    assert push_cart.push_file_raw(b, src, dst, push_cart.Link(window)) is False
    assert dev.acks == []


def test_a_corrupt_upload_leaves_the_old_file_in_place(tmp_path):
    """The .new is verified BEFORE the rename. A half-written main.lua is a
    cart that will not load, and the board is not where you want to find out.

    The corruption here is in the STORE (a byte more than was sent lands in the
    file), which is why the board hashes by reading the file back instead of
    hashing the buffer it received -- from RAM the two would have agreed."""
    dst = "/moy/carts/demo.moy/main.lua"
    dev = _FakeConsole(files={dst: b"the cart that still works\n"},
                       corrupt=True)
    b, window = _raw(dev)
    src = _cart(tmp_path, {"main.lua": SOURCE}) + "/main.lua"
    with pytest.raises(RuntimeError) as exc:
        push_cart.push_file_raw(b, src, dst, push_cart.Link(window))
    assert "main.lua" in str(exc.value) and SHA in str(exc.value)
    assert dev.fs.files == {dst: b"the cart that still works\n"}


def test_an_empty_file_needs_no_window_at_all(tmp_path):
    """A cart can carry one, and a protocol that waits for an ack that is not
    coming would hang on it. Zero bytes: armed, nothing sent, hash of nothing."""
    dev = _FakeConsole()
    b, window = _raw(dev)
    src = _cart(tmp_path, {"config.json": b""}) + "/config.json"
    dst = "/moy/carts/demo.moy/config.json"
    assert push_cart.push_file_raw(b, src, dst, push_cart.Link(window)) is True
    assert dev.fs.files == {dst: b""}
    assert dev.acks == []


# -- the capability probe -----------------------------------------------------


def test_an_older_image_is_refused_by_name_and_nothing_is_sent(
        monkeypatch, tmp_path):
    """`REMOTE ? recv` is the whole handshake, and it is a DEFINITE no: the
    dispatcher every board already runs answers it. There is no second transport
    to fall back to, so the run ends there -- with one line saying the firmware
    is too old, before a byte of cart is sent."""
    dev = _FakeConsole(board="tdeck", carts_root="/sd/carts", has_recv=False)
    monkeypatch.setattr(push_cart, "P4Board", _factory(dev))
    cart = _cart(tmp_path, {"main.lua": SOURCE})
    with pytest.raises(SystemExit) as exc:
        push_cart.main([cart, "--board", "tdeck"])
    assert "too old" in str(exc.value) and "REMOTE ? recv" in str(exc.value)
    assert dev.acks == []
    assert dev.fs.files == {}


class _Deaf:
    """A board that takes writes and never answers -- a wedged console."""

    port = "/dev/fake"

    def write(self, data):
        return len(data)

    def flush(self):
        pass

    def read(self, n):
        return b""

    def close(self):
        pass


def test_a_board_that_says_nothing_at_all_is_refused_too(monkeypatch):
    """Silence is read as no. Guessing YES at an unknown board puts kilobytes
    of payload into a reader that is still splitting lines."""
    monkeypatch.setattr(push_cart, "RAW_PROBE_S", 0.2)
    b = _driver(_FakeConsole())
    b.ser = _Deaf()
    with pytest.raises(SystemExit) as exc:
        push_cart.raw_link(b, {"window": 16384})
    assert "no answer to the `recv` probe" in str(exc.value)


def test_the_boards_own_ceiling_wins_over_the_declaration(tmp_path):
    """The board allocates the window, so its `max=` is the one that binds --
    a board.toml that outgrows a future image must not push it over."""
    dev = _FakeConsole(board="tdeck", max_window=1024)
    b = _driver(dev, "tdeck")
    assert push_cart.raw_link(b, {"window": 16384}).window == 1024


def test_the_probe_runs_once_for_the_whole_cart(monkeypatch, tmp_path):
    """`recv` is a property of the IMAGE, not of a file: asking per file spends
    a round trip to learn the same thing."""
    dev = _FakeConsole(board="tdeck", carts_root="/sd/carts")
    monkeypatch.setattr(push_cart, "P4Board", _factory(dev))
    cart = _cart(tmp_path, {"main.lua": SOURCE, "config.json": b"{}\n",
                            "manifest.json": b'{"title": "Demo"}\n'})
    assert push_cart.main([cart, "--board", "tdeck"]) == 0
    assert dev.sent.count("recv") == 1
    assert len(dev.fs.files) == 3


# -- `recv rate=`: the payload at another UART rate ---------------------------


def _rate_link(b, rate=2000000):
    return push_cart.raw_link(b, {"window": 3072, "recv_baud": rate})


def test_a_big_file_goes_at_the_declared_rate_and_the_console_comes_back(
        tmp_path):
    """The Waveshare's console UART stays at its own rate for every line --
    `moy push`, a terminal and the boot log expect it -- and a file bigger
    than a window crosses at the declared payload rate, between the syncs."""
    dst = "/moy/carts/demo.moy/main.lua"
    dev = _FakeConsole(rate=115200)
    b, _w = _raw(dev)
    link = _rate_link(b)
    assert (link.window, link.console, link.rate) == (3072, 115200, 2000000)
    src = _cart(tmp_path, {"main.lua": BIG}) + "/main.lua"
    assert push_cart.push_file_raw(b, src, dst, link) is True
    assert dev.fs.files == {dst: BIG}
    assert dev.switches == [2000000, 115200]
    assert dev.baudrate == 115200
    assert "RECV sync 2000000" in dev.said
    assert link.rate == 2000000


def test_a_file_inside_one_window_stays_at_the_console_rate(tmp_path):
    """Two switches and two syncs buy nothing for a file one window carries."""
    dev = _FakeConsole(rate=115200)
    b, _w = _raw(dev)
    src = _cart(tmp_path, {"main.lua": SOURCE}) + "/main.lua"
    assert push_cart.push_file_raw(b, src, "/moy/carts/demo.moy/main.lua",
                                   _rate_link(b)) is True
    assert dev.switches == []


def test_a_rate_the_board_does_not_answer_at_is_given_up_for_the_session(
        monkeypatch, tmp_path):
    """A rate the link cannot carry shows as no `RECV sync`. This side waits
    longer than the board does, so both ends are back at the console rate when
    it gives up; the file goes again there, and so does the rest of the push."""
    monkeypatch.setattr(push_cart, "RATE_SYNC_S", 0.2)
    monkeypatch.setattr(push_cart, "RATE_SETTLE_S", 0.05)
    dst = "/moy/carts/demo.moy/main.lua"
    dev = _FakeConsole(rate=115200, rate_deaf=True)
    b, _w = _raw(dev)
    link = _rate_link(b)
    src = _cart(tmp_path, {"main.lua": BIG}) + "/main.lua"
    assert push_cart.push_file_raw(b, src, dst, link) is True
    assert dev.fs.files == {dst: BIG}
    assert link.rate is None
    assert dev.switches == [2000000, 115200]
    assert "RECV ERR no sync at 2000000" in dev.said


def test_a_failure_at_the_payload_rate_brings_both_ends_back(tmp_path):
    """The board says ERR at the payload rate, where this side is listening,
    and goes back; this side follows and ends whatever the switch left in the
    board's line reader."""
    dst = "/moy/carts/demo.moy/main.lua"
    dev = _FakeConsole(rate=115200, full_at=5000)
    b, _w = _raw(dev)
    src = _cart(tmp_path, {"main.lua": BIG}) + "/main.lua"
    with pytest.raises(push_cart.StoreFull):
        push_cart.push_file_raw(b, src, dst, _rate_link(b))
    assert dev.switches == [2000000, 115200]
    assert dev.baudrate == 115200
    assert b.pyval("1+1") == 2


def test_a_board_that_cannot_switch_is_never_asked_to(tmp_path):
    """A USB board's caps line names no console rate, so a declared payload
    rate stays unused there."""
    dev = _FakeConsole(board="tdeck")
    b = _driver(dev, "tdeck")
    assert _rate_link(b).rate is None


def test_only_the_board_that_can_switch_declares_a_payload_rate():
    """The rate is the Waveshare's alone: the other three carry their serial
    on the SoC's USB, where a UART rate means nothing."""
    rates = {name: push_cart.serial_cfg(name).get("recv_baud")
             for name in BOARD_DIRS}
    assert {k for k, v in rates.items() if v} == {"p4"}


# -- main: identity, the discovered store, and the whole walk -----------------


def _factory(device):
    def make(port, log=None, board_dir=None):
        return p4_autotest.P4Board(None, ser=device, board_dir=board_dir)
    return make


def test_the_store_path_comes_from_the_console_not_from_the_tool(
        monkeypatch, tmp_path):
    """`ws.carts_root` is asked, never declared: the Guition's store is a TF
    card when one is in the slot and the internal VFS when it is not, so a
    hardcoded path would be wrong on that board half the time."""
    dev = _FakeConsole(board="guition_s3", carts_root="/sd/carts")
    monkeypatch.setattr(push_cart, "P4Board", _factory(dev))
    cart = _cart(tmp_path, {"main.py": b"print('hi')\n",
                            "manifest.json": b'{"title": "Demo"}\n'})
    assert push_cart.main([cart, "--board", "guition_s3"]) == 0
    assert dev.fs.files == {
        "/sd/carts/demo.moy/main.py": b"print('hi')\n",
        "/sd/carts/demo.moy/manifest.json": b'{"title": "Demo"}\n'}
    assert "/sd/carts/demo.moy" in dev.fs.dirs
    assert dev.closed == 1


@pytest.mark.parametrize("board", ["p4", "tdeck"])
def test_the_whole_walk_windows_at_the_boards_declared_size(
        monkeypatch, tmp_path, board):
    """The declaration read where it is read -- through main(). One tool, two
    transports underneath: 4096 on the P4, whose ack is the only backpressure
    its UART has, 16384 on the USB boards, which backpressure for real."""
    monkeypatch.setattr(p4_autotest.P4Board, "reset",   # the CH343 line pulse
                        lambda self, **kw: None)        # is p4_autotest's
    dev = _FakeConsole(board=board, carts_root="/moy/carts")
    monkeypatch.setattr(push_cart, "P4Board", _factory(dev))
    cart = _cart(tmp_path, {"main.lua": BIG})
    assert push_cart.main([cart, "--board", board]) == 0
    window = int(push_cart.serial_cfg(board)["window"])
    assert dev.acks == [min((k + 1) * window, len(BIG))
                        for k in range((len(BIG) + window - 1) // window)]
    assert dev.fs.files == {"/moy/carts/demo.moy/main.lua": BIG}


def test_an_attach_only_board_is_asked_who_it_is_and_never_reset(
        monkeypatch, tmp_path):
    """Liveness is not identity: the two S3s share a usb id and both answer.
    A cart pushed to the wrong board's store is a silent wrong outcome."""
    dev = _FakeConsole(board="tdeck", carts_root="/sd/carts")
    monkeypatch.setattr(push_cart, "P4Board", _factory(dev))
    cart = _cart(tmp_path, {"main.py": b"x = 1\n"})
    with pytest.raises(SystemExit) as exc:
        push_cart.main([cart, "--board", "guition_s3"])
    assert "tdeck" in str(exc.value) and "guition_s3" in str(exc.value)
    assert dev.fs.files == {}


def test_a_board_that_does_not_answer_at_all_is_not_pushed_to(
        monkeypatch, tmp_path):
    """An attach_only board is never pulsed awake, so a silent one means the
    console is not running and the push has nowhere to land."""
    dev = _FakeConsole()
    monkeypatch.setattr(push_cart, "P4Board", _factory(dev))
    monkeypatch.setattr(p4_autotest.P4Board, "cmd",
                        lambda self, text, **kw: None)
    cart = _cart(tmp_path, {"main.py": b"x = 1\n"})
    with pytest.raises(SystemExit) as exc:
        push_cart.main([cart, "--board", "tdeck"])
    assert "not responding" in str(exc.value)
    assert dev.fs.files == {}


def test_force_re_uploads_a_file_the_board_already_has(monkeypatch, tmp_path):
    dev = _FakeConsole(board="tdeck", carts_root="/sd/carts",
                       files={"/sd/carts/demo.moy/main.py": b"x = 1\n"})
    monkeypatch.setattr(push_cart, "P4Board", _factory(dev))
    cart = _cart(tmp_path, {"main.py": b"x = 1\n"})
    assert push_cart.main([cart, "--board", "tdeck"]) == 0
    assert dev.uploaded == 0
    assert push_cart.main([cart, "--board", "tdeck", "--force"]) == 0
    assert dev.uploaded and dev.fs.files["/sd/carts/demo.moy/main.py"] == b"x = 1\n"


def test_only_pushes_the_named_file_and_refuses_one_the_cart_lacks(
        monkeypatch, tmp_path):
    dev = _FakeConsole(board="tdeck", carts_root="/sd/carts")
    monkeypatch.setattr(push_cart, "P4Board", _factory(dev))
    cart = _cart(tmp_path, {"main.py": b"x = 1\n", "config.json": b"{}\n"})
    assert push_cart.main([cart, "--board", "tdeck",
                           "--only", "main.py"]) == 0
    assert list(dev.fs.files) == ["/sd/carts/demo.moy/main.py"]
    with pytest.raises(SystemExit) as exc:
        push_cart.main([cart, "--board", "tdeck", "--only", "sprites.json"])
    assert "sprites.json" in str(exc.value)


def test_only_is_repeatable_and_pushes_every_named_file(monkeypatch, tmp_path):
    """`--only` is `action="append"`, so it names several files by being given
    more than once -- not a comma-separated list, and not capped at one."""
    dev = _FakeConsole(board="tdeck", carts_root="/sd/carts")
    monkeypatch.setattr(push_cart, "P4Board", _factory(dev))
    cart = _cart(tmp_path, {"main.py": b"x = 1\n", "config.json": b"{}\n",
                            "manifest.json": b'{"title": "Demo"}\n'})
    assert push_cart.main([cart, "--board", "tdeck", "--only", "main.py",
                           "--only", "config.json"]) == 0
    assert sorted(dev.fs.files) == ["/sd/carts/demo.moy/config.json",
                                    "/sd/carts/demo.moy/main.py"]


def _sub(cart, rel, data):
    path = os.path.join(cart, *rel.split("/"))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(data)


def test_a_carts_subfolders_travel_with_it(monkeypatch, tmp_path):
    """A cart is a TREE. `scenes/`, `images/` and `tables/` are as much the
    cart as main.py is, and a listdir walk left every one of them on the host:
    the cart landed on the board without the assets it needs, and the folders
    were never made there either."""
    dev = _FakeConsole(board="tdeck", carts_root="/sd/carts")
    monkeypatch.setattr(push_cart, "P4Board", _factory(dev))
    cart = _cart(tmp_path, {"main.lua": SOURCE,
                            "manifest.json": b'{"title": "Demo"}\n'})
    scene = b'{"actors": [], "w": 40}\n'
    _sub(cart, "scenes/x.moyscene", scene)
    _sub(cart, "images/tiles/a.moyimg", b"IMG\n")

    assert push_cart.main([cart, "--board", "tdeck"]) == 0

    assert dev.fs.files["/sd/carts/demo.moy/scenes/x.moyscene"] == scene
    assert dev.fs.files["/sd/carts/demo.moy/images/tiles/a.moyimg"] == b"IMG\n"
    # ... and the folders were created, parents first -- `_mkdirs` makes them
    # with one os.mkdir each on the board, which does not make parents.
    assert "/sd/carts/demo.moy/scenes" in dev.fs.dirs
    assert "/sd/carts/demo.moy/images/tiles" in dev.fs.dirs
    mk = [line for line in dev.sent if line.startswith("py ws._g['_mkdirs']")]
    assert len(mk) == 1
    assert mk[0].index("'/sd/carts/demo.moy/images'") < \
        mk[0].index("'/sd/carts/demo.moy/images/tiles'")


def test_only_reaches_a_file_inside_a_subfolder(monkeypatch, tmp_path):
    """`--only` names a path inside the cart, so the assets it exists to
    re-push one of are reachable by it."""
    dev = _FakeConsole(board="tdeck", carts_root="/sd/carts")
    monkeypatch.setattr(push_cart, "P4Board", _factory(dev))
    cart = _cart(tmp_path, {"main.lua": SOURCE})
    _sub(cart, "scenes/x.moyscene", b"{}\n")
    assert push_cart.main([cart, "--board", "tdeck",
                           "--only", "scenes/x.moyscene"]) == 0
    assert list(dev.fs.files) == ["/sd/carts/demo.moy/scenes/x.moyscene"]


# -- a store without the room ---------------------------------------------------


def test_a_cart_the_store_cannot_hold_is_refused_before_a_byte_is_sent(
        monkeypatch, tmp_path):
    """What the push adds against the room the store reports, before the first
    window: one plain line, no traceback, and nothing on the board -- the
    Guition P4's internal store and a 5.7 MB cart are the case this is for."""
    dev = _FakeConsole(board="tdeck", carts_root="/sd/carts", free=8192)
    monkeypatch.setattr(push_cart, "P4Board", _factory(dev))
    cart = _cart(tmp_path, {"main.lua": BIG, "manifest.json": b"{}\n"})
    with pytest.raises(SystemExit) as exc:
        push_cart.main([cart, "--board", "tdeck"])
    msg = str(exc.value)
    assert msg.startswith("STORE FULL: the cart does not fit"), msg
    assert "/sd/carts" in msg and "free" in msg
    assert [l for l in dev.sent if l.startswith("recv ")] == []
    assert dev.fs.files == {}


def test_a_file_that_replaces_one_counts_only_what_it_adds(monkeypatch, tmp_path):
    dev = _FakeConsole(board="tdeck", carts_root="/sd/carts", free=4096,
                       files={"/sd/carts/demo.moy/main.lua": b"x" * 9000})
    monkeypatch.setattr(push_cart, "P4Board", _factory(dev))
    cart = _cart(tmp_path, {"main.lua": BIG})
    assert push_cart.main([cart, "--board", "tdeck"]) == 0
    assert dev.fs.files["/sd/carts/demo.moy/main.lua"] == BIG


def test_a_store_that_fills_during_the_push_says_so_plainly(monkeypatch, tmp_path):
    """The room check can pass and the store still fill (a card's clusters
    round every file up): the board's `store full` is the same plain line."""
    dev = _FakeConsole(board="tdeck", carts_root="/sd/carts", full_at=5000)
    monkeypatch.setattr(push_cart, "P4Board", _factory(dev))
    cart = _cart(tmp_path, {"main.lua": BIG})
    with pytest.raises(SystemExit) as exc:
        push_cart.main([cart, "--board", "tdeck"])
    msg = str(exc.value)
    assert msg.startswith("STORE FULL: main.lua did not fit: the board's store "
                          "is full"), msg
    assert "/sd/carts/demo.moy/main.lua" not in dev.fs.files


def test_any_other_failure_is_one_line_too(monkeypatch, tmp_path):
    dev = _FakeConsole(board="tdeck", carts_root="/sd/carts", corrupt=True)
    monkeypatch.setattr(push_cart, "P4Board", _factory(dev))
    cart = _cart(tmp_path, {"main.lua": SOURCE})
    with pytest.raises(SystemExit) as exc:
        push_cart.main([cart, "--board", "tdeck"])
    assert "hash" in str(exc.value) and "left the old file" in str(exc.value)


# -- a compiled cart gets the module its board runs it from ----------------------


def _compilers_here():
    from tools import wasm_module as wm
    return all(os.path.isfile(os.path.join(wm.DIST, p["file"]))
               for p in wm.COMPILERS.values())


needs_wamrc = pytest.mark.skipif(
    not _compilers_here(), reason="no pinned wamrc in experiments/wasm_aot/"
    "toolchain/dist (tools/wasm_module.py compilers fetches them)")


def _compiled_cart(tmp_path):
    """moy-spec's `moy build` output, as far as a board is concerned: a
    manifest, main.wasm, and no module."""
    from tools import wasm_cart
    out = str(tmp_path / "demo.moy")
    wasm_cart.build(os.path.join(ROOT, "tests", "fixtures", "wasm", "hello.moy"), out)
    for extra in ("src",):
        shutil.rmtree(os.path.join(out, extra), ignore_errors=True)
    return out


def test_a_lua_cart_has_no_module_to_build(tmp_path):
    cart = _cart(tmp_path, {"manifest.json": b'{"title": "Demo"}\n',
                            "main.lua": b"x = 1\n"})
    assert push_cart.compiled_module(cart, "esp32s3", str(tmp_path)) is None


def test_a_compiled_cart_with_no_main_wasm_says_to_build_it(tmp_path):
    cart = _cart(tmp_path, {"manifest.json": b'{"title": "D", "runtime": "wasm", '
                                             b'"memory": 1}\n'})
    with pytest.raises(SystemExit) as exc:
        push_cart.compiled_module(cart, "esp32s3", str(tmp_path))
    assert "moy build" in str(exc.value)


@needs_wamrc
@pytest.mark.parametrize("unknown_sources", [False, True])
def test_a_compiled_cart_without_a_module_gets_one_compiled_and_pushed(
        monkeypatch, tmp_path, capsys, unknown_sources):
    """The board runs the cart only from a module for its chip, and a cart
    made with `moy build` carries none: the push compiles one, unsigned, and
    sends it beside main.wasm, leaving the cart folder as it was. Unsigned
    runs only with Unknown sources on, which the tool asks the console about
    and says plainly before the push when it is off."""
    from tools import wasm_module as wm
    cart = _compiled_cart(tmp_path)
    before = sorted(os.listdir(cart))
    dev = _FakeConsole(board="tdeck", carts_root="/sd/carts")
    dev.ws.unknown_sources = unknown_sources
    monkeypatch.setattr(push_cart, "P4Board", _factory(dev))
    assert push_cart.main([cart, "--board", "tdeck"]) == 0
    module = dev.fs.files["/sd/carts/demo.moy/main.esp32s3.f%s.aot"
                          % wm.format_version()]
    with open(os.path.join(cart, "main.wasm"), "rb") as f:
        wasm = f.read()
    assert dev.fs.files["/sd/carts/demo.moy/main.wasm"] == wasm
    assert wm.key_matches(module, wasm, "esp32s3")
    assert wm.split(module)[1] is None                  # unsigned
    assert sorted(os.listdir(cart)) == before           # the cart is untouched
    out = capsys.readouterr().out
    assert ("Unknown sources off" in out) is (not unknown_sources)
    assert out.index("compiling one, unsigned") < out.index("demo.moy ->")


@needs_wamrc
def test_a_module_built_for_another_main_wasm_is_replaced(monkeypatch, tmp_path,
                                                          capsys):
    """A stale module -- main.wasm rebuilt since -- is one the board refuses
    by its key, so the push compiles a fresh one in its place."""
    from tools import wasm_cart, wasm_module as wm
    cart = _compiled_cart(tmp_path)
    name = wasm_cart.aot_name("main.wasm", "esp32p4")   # this tree's own format
    stale = os.path.join(cart, name)
    wm.build(b"\0asm\1\0\0\0", "esp32p4", stale,
             signed=False)
    dev = _FakeConsole(board="p4", carts_root="/moy/carts")
    dev.ws.unknown_sources = True
    monkeypatch.setattr(p4_autotest.P4Board, "reset", lambda self, **kw: None)
    monkeypatch.setattr(push_cart, "P4Board", _factory(dev))
    assert push_cart.main([cart, "--board", "p4"]) == 0
    with open(os.path.join(cart, "main.wasm"), "rb") as f:
        wasm = f.read()
    assert wm.key_matches(dev.fs.files["/moy/carts/demo.moy/" + name],
                          wasm, "esp32p4")
    assert "built for another main.wasm" in capsys.readouterr().out


@needs_wamrc
def test_a_module_for_another_chip_is_left_unpushed(monkeypatch, tmp_path):
    """A portable cart may carry a module per chip and format
    (docs/wasm_tier_plan_2026-09.md, "A cart survives its firmware"); a push
    takes only the one THIS board's chip wants and leaves every other one
    sitting in the cart folder, unpushed -- it is not this board's module to
    carry, and nothing about pushing it should evict it either."""
    from tools import wasm_cart
    cart = _compiled_cart(tmp_path)
    with open(os.path.join(cart, "main.wasm"), "rb") as f:
        wasm = f.read()
    other = os.path.join(cart, wasm_cart.aot_name("main.wasm", "esp32p4"))
    from tools import wasm_module as wm
    wm.build(wasm, "esp32p4", other, signed=False)
    dev = _FakeConsole(board="tdeck", carts_root="/sd/carts")
    monkeypatch.setattr(push_cart, "P4Board", _factory(dev))
    assert push_cart.main([cart, "--board", "tdeck"]) == 0
    pushed = [p for p in dev.fs.files if p.startswith("/sd/carts/demo.moy/")]
    assert not any(p.endswith(".esp32p4.f%s.aot" % wm.format_version())
                   for p in pushed), pushed
    assert any(p.endswith(".esp32s3.f%s.aot" % wm.format_version())
              for p in pushed), pushed
    # unpushed, not deleted: still on disk, right where it was built
    assert os.path.isfile(other)
