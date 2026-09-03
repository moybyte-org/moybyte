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
import sys
import types

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))

import p4_autotest                                              # noqa: E402
import push_cart                                                # noqa: E402

BOARD_DIRS = {
    "p4": os.path.join(ROOT, "firmware", "esp32_p4_wifi6_touch_lcd_7b"),
    "tdeck": os.path.join(ROOT, "firmware", "lilygo_t_deck_plus_mainline"),
    "guition_s3": os.path.join(ROOT, "firmware", "guition_jc3248w535"),
}


class _FakeFile:
    def __init__(self, fs, path, mode):
        self.fs, self.path, self.mode = fs, path, mode
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
        return self.buf

    def close(self):
        if "w" in self.mode:
            self.fs.files[self.path] = (self.buf + b"!" if self.fs.corrupt
                                        else self.buf)


class _FakeFS:
    """The device's storage: `open` plus the three os verbs the tool reaches
    for. `corrupt` appends a byte to every file that is closed, which is what a
    dropped upload chunk looks like from up here -- a hash that does not match."""

    def __init__(self, files=None, corrupt=False):
        self.files = dict(files or {})
        self.dirs = set()
        self.corrupt = corrupt

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
    """

    def __init__(self, board="p4", carts_root="/moy/carts", files=None,
                 corrupt=False, has_recv=True, max_window=32768,
                 drop_at=None, flip_at=None, stall_at=None):
        self.port = "/dev/fake"
        self.fs = _FakeFS(files, corrupt)
        self.ws = _WS(carts_root)
        self.board = board
        self.has_recv = has_recv        # False = an image from before `recv`
        self.max_window = max_window
        self.drop_at = drop_at          # a byte the ring swallowed: not counted
        self.flip_at = flip_at          # a byte that arrived wrong: counted
        self.stall_at = stall_at        # the host stops writing here
        self.sent = []          # every complete line the tool wrote
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
        if self._rx is not None:            # armed by `recv`: this is payload
            self._feed(data)
            return len(data)
        self._in += data                    # the writer PACES long lines
        while b"\n" in self._in:
            raw, self._in = self._in.split(b"\n", 1)
            self._run(raw.decode())
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
            return self._say("RECV caps max=%d idle=5000" % self.max_window)
        total, window = int(parts[1]), int(parts[2])
        if window > self.max_window:
            return self._say("RECV ERR window %d (max %d)"
                             % (window, self.max_window))
        tmp = line.split(None, 3)[3] + ".new"
        f = self.fs.open(tmp, "wb")
        self._say("RECV ready %d %d %s" % (total, window, tmp))
        if total == 0:                  # an empty file: nothing to wait for
            f.close()
            return self._say("RECV done %s 0"
                             % hashlib.sha256(b"").hexdigest()[:12])
        self._rx = {"n": total, "window": window, "tmp": tmp, "got": 0,
                    "sent": 0, "f": f}

    def _feed(self, data):
        rx = self._rx
        for byte in data:
            i = rx["sent"]
            rx["sent"] += 1
            if self.stall_at is not None and i >= self.stall_at:
                continue                    # the host died: nothing arrives
            if i == self.drop_at:
                continue                    # the ring dropped it, silently
            if i == self.flip_at:
                byte ^= 0xFF                # a framing error: count intact
            rx["f"].write(bytes([byte]))
            rx["got"] += 1
            if rx["got"] % rx["window"] and rx["got"] != rx["n"]:
                continue
            self.acks.append(rx["got"])
            self._say("RECV ack %d" % rx["got"])
            if rx["got"] == rx["n"]:
                self._rx = None
                rx["f"].close()             # `corrupt` lands here
                return self._say(
                    "RECV done %s %d"
                    % (hashlib.sha256(self.fs.files.get(rx["tmp"], b""))
                       .hexdigest()[:12], rx["got"]))
        if self._rx is None or (rx["got"] % rx["window"] == 0
                                or rx["got"] == rx["n"]):
            return
        # The host has stopped writing with this window short, so the byte the
        # board is waiting on is never coming: on glass that is the idle
        # timeout, RECV_IDLE_MS later. The wait is what is compressed here.
        self._rx = None
        rx["f"].close()
        self.fs.files.pop(rx["tmp"], None)
        self._say("RECV ERR timeout after %d of %d bytes" % (rx["got"], rx["n"]))

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
        self._out += text.encode() + b"\r\n"

    @property
    def uploaded(self):
        """Payload bytes this board took."""
        return (self.acks[-1:] or [0])[-1]


def _driver(device, board="p4"):
    b = p4_autotest.P4Board(None, ser=device, board_dir=BOARD_DIRS[board])
    b.CHUNK = int(push_cart.serial_cfg(board)["chunk"])
    b.PACE_GAP_S = 0            # the write pacing is p4_autotest's, not this
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
    board.toml's call: 4096 on the P4, whose UART has no flow control and whose
    ack is the only backpressure there is; 16384 on the USB boards, where the
    window buys round trips rather than safety."""
    dev = _FakeConsole(board=board)
    b, window = _raw(dev, board)
    src = _cart(tmp_path, {"main.lua": BIG}) + "/main.lua"
    dst = "/moy/carts/demo.moy/main.lua"
    assert push_cart.push_file_raw(b, src, dst, window) is True
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
    push_cart.push_file_raw(b, src, "/moy/carts/demo.moy/main.lua", window)
    assert dev.fs.files["/moy/carts/demo.moy/main.lua"] == payload


def test_a_byte_the_ring_dropped_stops_the_push_and_names_the_file(tmp_path):
    """The P4's failure, exactly: a byte arrives with the 260-byte ring full
    and is gone with no error. The board is then one byte short of the window
    for ever, its idle timeout fires, it removes the tmp and says how far it
    got -- and the push stops there, by name, with the old cart untouched."""
    dst = "/moy/carts/demo.moy/main.lua"
    dev = _FakeConsole(files={dst: b"the cart that still works\n"},
                       drop_at=5000)
    b, window = _raw(dev)
    src = _cart(tmp_path, {"main.lua": BIG}) + "/main.lua"
    with pytest.raises(RuntimeError) as exc:
        push_cart.push_file_raw(b, src, dst, window)
    assert "main.lua" in str(exc.value) and "timeout" in str(exc.value)
    assert dev.fs.files == {dst: b"the cart that still works\n"}


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
        push_cart.push_file_raw(b, src, dst, window)
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
        push_cart.push_file_raw(b, src, dst, window)
    assert "main.lua" in str(exc.value)
    assert "6000 of 10000" in str(exc.value)
    assert dev.fs.files == {dst: b"the cart that still works\n"}


def test_the_pushed_bytes_arrive_intact(tmp_path):
    """End to end: armed, windowed, acked, hashed on the board by reading the
    file back, and renamed only then."""
    dev = _FakeConsole()
    b, window = _raw(dev)
    src = _cart(tmp_path, {"main.lua": SOURCE}) + "/main.lua"
    dst = "/moy/carts/demo.moy/main.lua"
    assert push_cart.push_file_raw(b, src, dst, window) is True
    assert dev.fs.files == {dst: SOURCE}


def test_a_first_push_survives_the_remove_of_a_file_that_is_not_there(tmp_path):
    """The pre-rename remove is a no-op by design: on a first push the device
    raises ENOENT and the push must carry on regardless."""
    dev = _FakeConsole()
    b, window = _raw(dev)
    src = _cart(tmp_path, {"main.lua": SOURCE}) + "/main.lua"
    dst = "/moy/carts/demo.moy/main.lua"
    push_cart.push_file_raw(b, src, dst, window)
    assert any("remove(%r)" % dst in line for line in dev.sent)
    assert dev.fs.files[dst] == SOURCE


def test_a_file_whose_hash_already_matches_is_not_uploaded(tmp_path):
    """What makes a re-run cheap and a half-finished push resumable."""
    dst = "/moy/carts/demo.moy/main.lua"
    dev = _FakeConsole(files={dst: SOURCE})
    b, window = _raw(dev)
    src = _cart(tmp_path, {"main.lua": SOURCE}) + "/main.lua"
    assert push_cart.push_file_raw(b, src, dst, window) is False
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
        push_cart.push_file_raw(b, src, dst, window)
    assert "main.lua" in str(exc.value) and SHA in str(exc.value)
    assert dev.fs.files == {dst: b"the cart that still works\n"}


def test_an_empty_file_needs_no_window_at_all(tmp_path):
    """A cart can carry one, and a protocol that waits for an ack that is not
    coming would hang on it. Zero bytes: armed, nothing sent, hash of nothing."""
    dev = _FakeConsole()
    b, window = _raw(dev)
    src = _cart(tmp_path, {"config.json": b""}) + "/config.json"
    dst = "/moy/carts/demo.moy/config.json"
    assert push_cart.push_file_raw(b, src, dst, window) is True
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
        push_cart.raw_window(b, 16384)
    assert "no answer to the `recv` probe" in str(exc.value)


def test_the_boards_own_ceiling_wins_over_the_declaration(tmp_path):
    """The board allocates the window, so its `max=` is the one that binds --
    a board.toml that outgrows a future image must not push it over."""
    dev = _FakeConsole(board="tdeck", max_window=1024)
    b = _driver(dev, "tdeck")
    assert push_cart.raw_window(b, 16384) == 1024


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


# -- main: identity, the discovered store, and the whole walk -----------------


def _factory(device):
    def make(port, log=None, board_dir=None):
        b = p4_autotest.P4Board(None, ser=device, board_dir=board_dir)
        b.PACE_GAP_S = 0
        return b
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
