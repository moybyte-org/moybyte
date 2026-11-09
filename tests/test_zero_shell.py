"""The Zero's terminal (firmware/seeed_xiao_esp32s3_zero/modules/zero_shell.py)
on the host: its door answers the shell's crossings it can (`passwd`, `df`,
`mem`, `ps`) and hands every other back as a console with its VM stopped
does, and ssh_service serves on it without a WiFi lease."""

import sys
from pathlib import Path

import pytest

from runtime import net_binding, ssh_service

ROOT = Path(__file__).resolve().parent.parent
sys.path.append(str(ROOT / "firmware" / "seeed_xiao_esp32s3_zero" / "modules"))

import zero_shell                                               # noqa: E402

DOOR = zero_shell.SH_DOOR


class IO:
    def __init__(self):
        self.out = {1: b"", 2: b""}

    def write(self, fd, b):
        self.out[fd] += b


class Server:
    def __init__(self):
        self.calls = []

    def parked(self):
        return True

    def start(self, path):
        self.calls.append(("start", path))

    def stop(self):
        self.calls.append(("stop",))

    def stats(self):
        return (0, 0, 0, 0, 0, 0, 0, bool(self.calls) and self.calls[-1][0] == "start")


@pytest.fixture
def zero(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "ssh_service", ssh_service)
    monkeypatch.setattr(ssh_service._docs, "system_store_path",
                        lambda root=None: str(tmp_path / "settings"))
    server = Server()
    monkeypatch.setattr(ssh_service, "_N", server)
    monkeypatch.setattr(ssh_service, "_state",
                        {"serving": False, "name": None, "default": None, "park": None})

    def no_radio(*a):
        raise AssertionError("the Zero's radio and name are the board's")
    monkeypatch.setitem(sys.modules, "moy_net", type("N", (), {
        "wifi_mdns": staticmethod(no_radio), "pw_hash": staticmethod(net_binding.pw_hash)}))
    ws = zero_shell.Console(str(tmp_path / "carts"), "Pocket Zero")
    io = IO()
    return zero_shell.Door(ws, io), io, server, tmp_path


def cross(door, *fields):
    return door(0, DOOR, b"\0".join(f.encode() for f in fields))


def test_passwd_crosses_the_door_and_serves_without_a_lease(zero):
    door, io, server, tmp = zero
    assert cross(door, "role", "j1", "passwd") == 0
    assert io.out[1].startswith(b"password not set\n")
    assert cross(door, "role", "j1", "passwd", "short") == 1
    assert cross(door, "role", "j1", "passwd", "s3cret-pw") == 0
    assert (tmp / "password").read_text().startswith("$moy2$")
    assert server.calls == [("start", str(tmp))]
    io.out[1] = b""
    assert cross(door, "role", "j1", "passwd") == 0
    assert io.out[1].startswith(b"password set\n"
                                b"ssh serving as pocket-zero.local, 0 session(s), 0 refused\n"
                                b"host key ")
    assert b"browser(s) paired" not in io.out[1]      # this Console has no webhost
    assert cross(door, "role", "j1", "passwd", "-l") == 1
    assert cross(door, "role", "j1", "passwd", "-x") == 2
    assert b"usage: passwd" in io.out[2]
    assert cross(door, "role", "j1", "passwd", "-d") == 0
    assert not (tmp / "password").exists() and server.calls[-1] == ("stop",)


def test_what_only_a_console_answers_is_handed_back(zero):
    door, io, _, _ = zero
    for fields in (("role", "j1", "settings", "list"), ("role", "j1", "python"),
                   ("lookup", "hello"), ("kill", "1", "15"), ("read", "/proc/console"),
                   ("list", "/dev"), ("spawn", "0", "hello")):
        assert cross(door, *fields) == zero_shell.NEEDS_VM, fields
    assert door(0, 3, b"") == zero_shell.ABSENT


def test_the_board_s_own_figures(zero, tmp_path):
    door, _, _, _ = zero
    total, free = (int(x) for x in cross(door, "df", str(tmp_path)).split())
    assert total >= free > 0
    row = cross(door, "ps").split()
    assert row[:4] == ["1", "0", "shell", "running"] and row[5:] == ["-", "zero_host"]
    assert cross(door, "rescan", "moybyte.hop") == 0


def test_passwd_lists_and_forgets_the_browsers_its_webhost_paired(zero):
    door, io, _, _ = zero

    class Web:
        def __init__(self):
            self.kids = {"k1": (86400, 2 * 86400, "Laptop")}

        def browsers(self):
            return [(k,) + v for k, v in self.kids.items()]

        def forget(self, kid):
            return 1 if self.kids.pop(kid, None) else 0

    door.ws.webhost = Web()
    assert cross(door, "role", "j1", "passwd", "-l") == 0
    assert io.out[1].startswith(b"k1  paired 1970-01-0") and io.out[1].endswith(b"Laptop\n")
    assert cross(door, "role", "j1", "passwd", "-r", "k1") == 0
    assert cross(door, "role", "j1", "passwd", "-r", "k1") == 1


def test_df_says_absent_for_a_folder_no_volume_holds(zero, tmp_path):
    door, _, _, _ = zero
    assert cross(door, "df", str(tmp_path / "sd")) == zero_shell.ABSENT
