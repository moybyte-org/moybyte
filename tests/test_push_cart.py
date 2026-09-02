"""`tools/push_cart.py` -- the upload protocol, and the facts it must read.

Never imported by anything (#208), including the number the tool was rewritten
for: `push_file` slices the base64 payload at the board's DECLARED chunk, and
768 corrupts silently on the P4 (a ~256-byte stdin ring with no flow control --
five pushes, a different bad hash each time, clean at 256; measured 2026-08-19).
Nothing catches an over-long line but the final hash, which is why the size is
data and why it needs a test that runs the slicing.

The board here is a fake console that EVALUATES the `py` lines the tool sends,
the way `runtime/dev_channel.py` does -- fresh env per command, eval falling
back to exec, `PY ERR <exc>` on a raise -- against an in-memory filesystem. So
the tool's own device-side HELPERS are uploaded and executed for real, and the
bytes that land are observable.
"""

import base64
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

    Mirrors `dev_channel`'s `py` handler exactly: a FRESH env per command (which
    is why the tool stashes its helpers in `ws._g`), eval with an exec fallback
    for statements, and the device's own words on a raise.
    """

    def __init__(self, board="p4", carts_root="/moy/carts", files=None,
                 corrupt=False, reject_chunk=None):
        self.port = "/dev/fake"
        self.fs = _FakeFS(files, corrupt)
        self.ws = _WS(carts_root)
        self.board = board
        self.reject_chunk = reject_chunk
        self.sent = []          # every complete line the tool wrote
        self.chunks = []        # the slices `_wr` decoded into a file
        self.closed = 0
        self._staged = []
        self._in = b""
        self._out = b""
        self._builtins = {k: getattr(_builtins, k) for k in dir(_builtins)}
        self._builtins["open"] = self.fs.open
        self._builtins["__import__"] = self._import

    # -- the wire ---------------------------------------------------------

    def write(self, data):
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

    def _run(self, line):
        self.sent.append(line)
        if not line.startswith("py "):
            return
        code = line[3:]
        if "ws._up.__setitem__" in code and self.reject_chunk is not None:
            if ("__setitem__(%d," % self.reject_chunk) in code:
                return self._say("PY ERR ValueError: rejected")
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
        if "ws._up.__setitem__" in code:
            self._staged.append(self.ws._up[max(self.ws._up)])
        elif "_wr'](" in code:          # a payload upload, not the helper one
            self.chunks.extend(self._staged)
            del self._staged[:]
        elif "'_up', {}" in code:
            del self._staged[:]
        self._say("PY " + value)

    def _say(self, text):
        self._out += text.encode() + b"\r\n"


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
SOURCE_B64 = base64.b64encode(SOURCE).decode()
SHA = hashlib.sha256(SOURCE).hexdigest()[:12]


def _install_helpers(b):
    assert b.pyexec(push_cart.HELPERS) is True


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


# -- push_file: the upload protocol -------------------------------------------


def test_the_upload_is_sliced_at_the_p4s_measured_chunk(tmp_path):
    """The number the tool exists at this size for. 768 on this board's UART is
    dropped as noise with no error; only the final hash notices, and it did --
    five times, with a different bad hash each attempt (2026-08-19)."""
    dev = _FakeConsole(board="p4")
    b = _driver(dev, "p4")
    _install_helpers(b)
    src = _cart(tmp_path, {"main.lua": SOURCE}) + "/main.lua"
    assert push_cart.push_file(b, src, "/moy/carts/demo.moy/main.lua") is True
    assert max(len(c) for c in dev.chunks) <= 256
    assert [len(c) for c in dev.chunks] == [256, 256, 256, 168]


@pytest.mark.parametrize("board", sorted(BOARD_DIRS))
def test_the_slice_is_the_boards_own_declaration(tmp_path, board):
    """Not a constant here, and not one shared number either: the S3 boards
    backpressure over USB and push at 768, three times fewer round trips."""
    chunk = int(push_cart.serial_cfg(board)["chunk"])
    dev = _FakeConsole(board=board)
    b = _driver(dev, board)
    _install_helpers(b)
    src = _cart(tmp_path, {"main.lua": SOURCE}) + "/main.lua"
    push_cart.push_file(b, src, "/moy/carts/demo.moy/main.lua")
    assert max(len(c) for c in dev.chunks) == chunk
    assert "".join(dev.chunks) == SOURCE_B64


def test_the_pushed_bytes_arrive_intact(tmp_path):
    """End to end through the tool's own device helpers: sliced, reassembled,
    decoded, written and renamed."""
    dev = _FakeConsole()
    b = _driver(dev)
    _install_helpers(b)
    src = _cart(tmp_path, {"main.lua": SOURCE}) + "/main.lua"
    dst = "/moy/carts/demo.moy/main.lua"
    assert push_cart.push_file(b, src, dst) is True
    assert dev.fs.files == {dst: SOURCE}


def test_a_first_push_survives_the_remove_of_a_file_that_is_not_there(tmp_path):
    """The pre-rename remove is a no-op by design: on a first push the device
    raises ENOENT and the push must carry on regardless."""
    dev = _FakeConsole()
    b = _driver(dev)
    _install_helpers(b)
    src = _cart(tmp_path, {"main.lua": SOURCE}) + "/main.lua"
    dst = "/moy/carts/demo.moy/main.lua"
    push_cart.push_file(b, src, dst)
    assert any("remove(%r)" % dst in line for line in dev.sent)
    assert dev.fs.files[dst] == SOURCE


def test_a_file_whose_hash_already_matches_is_not_uploaded(tmp_path):
    """What makes a re-run cheap and a half-finished push resumable."""
    dst = "/moy/carts/demo.moy/main.lua"
    dev = _FakeConsole(files={dst: SOURCE})
    b = _driver(dev)
    _install_helpers(b)
    src = _cart(tmp_path, {"main.lua": SOURCE}) + "/main.lua"
    assert push_cart.push_file(b, src, dst) is False
    assert dev.chunks == []


def test_a_corrupt_upload_leaves_the_old_file_in_place(tmp_path):
    """The .new is verified BEFORE the rename. A half-written main.lua is a
    cart that will not load, and the board is not where you want to find out."""
    dst = "/moy/carts/demo.moy/main.lua"
    dev = _FakeConsole(files={dst: b"the cart that still works\n"},
                       corrupt=True)
    b = _driver(dev)
    _install_helpers(b)
    src = _cart(tmp_path, {"main.lua": SOURCE}) + "/main.lua"
    with pytest.raises(RuntimeError) as exc:
        push_cart.push_file(b, src, dst)
    assert "main.lua" in str(exc.value) and SHA in str(exc.value)
    assert dev.fs.files == {dst: b"the cart that still works\n"}


def test_a_rejected_chunk_stops_the_push_and_names_the_chunk(tmp_path):
    dst = "/moy/carts/demo.moy/main.lua"
    dev = _FakeConsole(files={dst: b"the cart that still works\n"})
    b = _driver(dev)
    _install_helpers(b)
    # ARMED AFTER the helpers, not at construction: they are a pyexec upload
    # too, and they outgrew two chunks (db57817), so a rejection armed early
    # fires on the fixture instead of on the file this test is about.
    dev.reject_chunk = 2
    src = _cart(tmp_path, {"main.lua": SOURCE}) + "/main.lua"
    with pytest.raises(RuntimeError) as exc:
        push_cart.push_file(b, src, dst)
    assert "chunk 3/4" in str(exc.value)
    assert dev.fs.files == {dst: b"the cart that still works\n"}


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
def test_the_whole_walk_slices_at_the_boards_declared_chunk(
        monkeypatch, tmp_path, board):
    """The same fact through main(), which is where the declaration is read:
    one tool, two transports, and the P4's 256 must survive the walk.

    Deleting main's `b.CHUNK = chunk` is an EQUIVALENT MUTANT and stays green:
    `P4Board.__init__` already sizes the instance from the same board_dir's
    [serial] block. Assigning a WRONG size there is red, which is the half that
    matters.
    """
    monkeypatch.setattr(p4_autotest.P4Board, "reset",   # the CH343 line pulse
                        lambda self, **kw: None)        # is p4_autotest's
    dev = _FakeConsole(board=board, carts_root="/moy/carts")
    monkeypatch.setattr(push_cart, "P4Board", _factory(dev))
    cart = _cart(tmp_path, {"main.lua": SOURCE})
    assert push_cart.main([cart, "--board", board]) == 0
    assert max(len(c) for c in dev.chunks) == \
        int(push_cart.serial_cfg(board)["chunk"])
    assert dev.fs.files == {"/moy/carts/demo.moy/main.lua": SOURCE}


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
    assert dev.chunks == []
    assert push_cart.main([cart, "--board", "tdeck", "--force"]) == 0
    assert dev.chunks and dev.fs.files["/sd/carts/demo.moy/main.py"] == b"x = 1\n"


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
