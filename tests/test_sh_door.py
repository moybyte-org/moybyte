"""The C shell's door into the console (runtime/shell/sh_door.py): the
verbs native/moy_sh/moy_sh_board.c crosses with, answered over the console's
own process rows and devfs."""

from runtime import devfs
from runtime.shell_servers import SH_DOOR, RoleDoor
from runtime.sh_door import ABSENT, EPERM, ESRCH, ShellDoor


class _Procs:
    def __init__(self):
        self.closed, self.stopped = [], []

    def rows(self):
        return [("desktop", "Coin Quest", "game", "running", None, None, 12),
                ("svc:13", "Clock", "service", "running", None, None, 13)]

    def stop_service(self, pid):
        self.stopped.append(pid)


class _WS:
    def __init__(self):
        self.procs = _Procs()
        self._dirty = False

    def tasks_close(self, key):
        self.procs.closed.append(key)


def _door(monkeypatch):
    ws = _WS()
    monkeypatch.setattr(devfs._sched.SCHED, "procs", {})   # another test's services
    monkeypatch.setattr(devfs, "RUNS", lambda: [(12, "Coin Quest", "game", "running")])
    return ws, ShellDoor(ws)


def call(door, *fields):
    return door(b"\0".join(f.encode() for f in fields))


def test_ps_names_the_console_and_its_runs(monkeypatch):
    _ws, door = _door(monkeypatch)
    assert call(door, "ps") == ("1 0 shell running 0 shell\n"
                                "12 1 game running 0 Coin_Quest\n")


def test_kill_ends_a_run_as_the_task_manager_does(monkeypatch):
    ws, door = _door(monkeypatch)
    assert call(door, "kill", "1", "15") == EPERM
    assert call(door, "kill", "99", "15") == ESRCH
    assert call(door, "kill", "12", "0") == 0 and ws.procs.closed == []
    assert call(door, "kill", "12", "15") == 0 and ws.procs.closed == ["desktop"]
    assert call(door, "kill", "13", "9") == 0 and ws.procs.stopped == [13]
    assert call(door, "status", "12") == "running"
    assert call(door, "status", "99") == ABSENT


def test_proc_reads_and_lists_through_devfs(monkeypatch):
    _ws, door = _door(monkeypatch)
    assert call(door, "list", "/proc") == "1/\n12/\n"
    assert call(door, "list", "/proc/12") == "log\nstatus\n"
    assert call(door, "list", "/") == ABSENT
    assert call(door, "list", "/nowhere") == ABSENT
    assert b"Name:\tCoin Quest" in call(door, "read", "/proc/12/status")
    assert call(door, "read", "/proc/77/status") == ABSENT


def test_unserved_commands_are_absent(monkeypatch):
    _ws, door = _door(monkeypatch)
    assert call(door, "role", "perm", "list") == ABSENT
    assert call(door, "spawn", "0", "hello") == ABSENT
    assert call(door, "lookup", "hello") == 0
    assert call(door, "nonsense") == ABSENT


def test_the_role_door_hands_the_shell_row_to_the_shell(monkeypatch):
    ws, _door_ = _door(monkeypatch)
    rd = RoleDoor(ws, {}, [])
    assert rd(0, SH_DOOR, b"lookup\0x") == 0
