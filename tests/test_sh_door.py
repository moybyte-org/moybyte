"""The C shell's door into the console (runtime/shell/sh_door.py): the
verbs native/moy_sh/moy_sh_board.c crosses with, answered over the console's
own process rows and devfs; the commands it serves (runtime/shell/sh_roles.py)
and the installed ones, over a real console and on the desktop MicroPython."""

import json
import subprocess

import pytest

from runtime import devfs
from runtime import moy_log as L
from runtime import scheduler as S
from runtime.shell_servers import SH_DOOR, RoleDoor
from runtime.sh_door import ABSENT, EPERM, ESRCH, RUNNING, ShellDoor


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
    """A door crossing; a role command started (`role JOB ...`) and stepped
    to its end, as the session does between frames."""
    if fields[:1] == ("role",):
        fields = ("role", "t") + fields[1:]
    r = door(b"\0".join(f.encode() for f in fields))
    while fields[0] == "role" and r == RUNNING:
        r = door(b"step\0t")
    return r


def test_ps_names_the_console_and_its_runs(monkeypatch):
    _ws, door = _door(monkeypatch)
    assert call(door, "ps") == ("1 0 shell running 0 - shell\n"
                                "12 1 game running 0 - Coin_Quest\n")


def test_kill_ends_a_run_as_the_task_manager_does(monkeypatch):
    ws, door = _door(monkeypatch)
    assert call(door, "kill", "1", "15") == EPERM
    assert call(door, "kill", "99", "15") == ESRCH
    assert call(door, "kill", "12", "0") == 0 and ws.procs.closed == []
    assert call(door, "kill", "12", "15") == 0 and ws.procs.closed == ["desktop"]
    assert call(door, "kill", "13", "9") == 0 and ws.procs.stopped == [13]
    assert call(door, "status", "12") == "running"
    assert call(door, "status", "99") == ABSENT


def test_rescan_hands_sftp_s_cart_folders_to_the_shelf(monkeypatch):
    """`rescan NAME...` (native/moy_tty/moy_tty_sftp.c): the folders by name,
    as the browser sync's on_sync hands them; none, every folder."""
    ws, door = _door(monkeypatch)
    seen = []
    ws.rescan_carts = seen.append
    assert call(door, "rescan", "a.moy", "b.moy") == 0
    assert door(b"rescan") == 0
    assert seen == [["a.moy", "b.moy"], None]


def test_proc_reads_and_lists_through_devfs(monkeypatch):
    _ws, door = _door(monkeypatch)
    assert call(door, "list", "/proc") == "1/\n12/\nconsole\n"
    assert call(door, "list", "/proc/12") == "log\nstatus\n"
    assert call(door, "list", "/") == ABSENT
    assert call(door, "list", "/nowhere") == ABSENT
    assert b"Name:\tCoin Quest" in call(door, "read", "/proc/12/status")
    assert call(door, "read", "/proc/77/status") == ABSENT


def test_unknown_verbs_and_roles_are_absent(monkeypatch):
    _ws, door = _door(monkeypatch)
    assert call(door, "role", "nonsense") == ABSENT
    assert call(door, "nonsense") == ABSENT


def test_the_role_door_hands_the_shell_row_to_the_shell(monkeypatch):
    ws, _door_ = _door(monkeypatch)
    rd = RoleDoor(ws, {}, [])
    assert rd(0, SH_DOOR, b"lookup\0x") == 0


# -- the served commands: a door over a real console ---------------------------------


class _IO:
    """moy_sh's door streams: fd 1 and 2 kept, fd 0 read from `stdin`."""

    def __init__(self, stdin=b""):
        self.stdin = stdin
        self.fds = {1: b"", 2: b""}

    def write(self, fd, data):
        self.fds[fd] += bytes(data)
        return len(data)

    def read(self, fd, n):
        assert fd == 0
        got, self.stdin = self.stdin[:n], self.stdin[n:]
        return got

    @property
    def out(self):
        return self.fds[1].decode()

    @property
    def err(self):
        return self.fds[2].decode()


HELLO = """
def main(argv):
    print("hello", *argv[1:])
    name = input("who? ")
    print("hi", name, file=None)
    return 3
"""

FAILS = """
def main(argv):
    raise ValueError("no luck")
"""

EXITS = """
import moybyte
def main(argv):
    from runtime import scheduler, moy_log
    me = scheduler.SCHED.procs[moy_log.current()]
    print(me.kind, me.name, " ".join(me.granted))
    moybyte.every(1000, lambda: None)
    raise SystemExit(5)
"""

SVC = """
import moybyte
def moy_start():
    moybyte.every(1000, lambda: None)
"""


def _cart(root, cid, kind, src, perms=(), req=()):
    d = root / (cid + ".moy")
    d.mkdir(parents=True)
    (d / "manifest.json").write_text(json.dumps({
        "format": "moy-1", "title": cid, "id": cid, "runtime": "python",
        "main": "main.py", "moybyte": {"type": kind, "permissions": list(perms),
                                       "required": list(req)}}))
    (d / "main.py").write_text(src)


@pytest.fixture
def console(tmp_path):
    from runtime.host_app import build_workstation
    root = tmp_path / "carts"
    _cart(root, "local.hello", "command", HELLO)
    _cart(root, "other.hello", "command", FAILS)
    _cart(root, "local.fails", "command", FAILS)
    _cart(root, "local.exits", "command", EXITS, ["http", "notifications"])
    _cart(root, "local.needy", "command", HELLO, ["mic"], ["mic"])
    _cart(root, "local.svc", "service", SVC, ["http"])
    S.SCHED.reset()
    ws = build_workstation(str(root))

    def door(stdin=b""):
        io = _IO(stdin)
        return io, ShellDoor(ws, io)
    yield ws, door
    S.SCHED.reset()


def test_bin_lists_the_command_carts_and_lookup_finds_them(console):
    _ws, door = console
    _io, d = door()
    assert call(d, "list", "/bin") == "exits\nfails\nhello\nneedy\n"
    assert call(d, "lookup", "hello") == 1
    assert call(d, "lookup", "other.hello/hello") == 1
    assert call(d, "lookup", "svc") == 0
    assert call(d, "lookup", "local.svc/svc") == 0
    assert call(d, "spawn", "0", "nothing") == ABSENT


def test_a_command_runs_main_over_the_session_streams(console):
    _ws, door = console
    io, d = door(b"Ada\nmore\n")
    assert call(d, "spawn", "0", "hello", "a", "b") == 3
    assert io.out == "hello a b\nwho? hi Ada\n"
    assert io.stdin == b""              # read in LINE-sized gulps, kept by the run
    io, d = door()
    assert call(d, "spawn", "0", "hello") == 1      # input() at the end: EOFError
    assert "EOFError" in io.err
    io, d = door()
    assert call(d, "spawn", "0", "other.hello/hello") == 1
    assert "ValueError: no luck" in io.err


def test_a_command_is_a_process_under_its_own_consent(console):
    _ws, door = console
    io, d = door()
    assert call(d, "spawn", "0", "exits") == 5
    kind, name, granted = io.out.split(" ", 2)
    assert (kind, name) == ("command", "exits")
    assert granted.split() == ["http", "notifications"]
    assert not [p for p in S.SCHED.procs.values() if p.kind == S.COMMAND]
    assert not [w for w in moybyte_wakes() if w[1] not in (1,)
                and w[1] not in S.SCHED.procs]          # its wakes swept
    io, d = door()
    assert call(d, "spawn", "0", "needy") == 126
    assert io.err.startswith('needy: needs "mic"')


def moybyte_wakes():
    from runtime import moybyte
    return moybyte.WAKES.list


def test_a_background_command_logs_and_answers_its_pid(console):
    _ws, door = console
    io, d = door()
    since = L.next()
    pid = call(d, "spawn", "1", "hello", "x")
    assert pid > 1 and io.out == "" and io.err == ""
    assert call(d, "status", str(pid)) == "exited 1"
    lines = [r[4] for r in L.read(since, 64) if r[3] == pid]
    assert "hello x" in lines and "exit 1" in lines


def test_python_runs_code_and_a_repl_over_stdin(console):
    _ws, door = console
    io, d = door()
    assert call(d, "role", "python", "-c", "print(6 * 7)") == 0 and io.out == "42\n"
    src = (b"x = 6\nx * 7\n'text'\nfor i in range(2):\n    print(i)\n\n"
           b"def f(a):\n    return a + 1\nf(1)\nd = {\n 'k': 1}\nd['k']\n"
           b"1 / 0\nraise SystemExit(4)\nprint('never')\n")
    io, d = door(src)
    assert call(d, "role", "python") == 4
    assert io.out == "42\n'text'\n0\n1\n2\n1\n"
    assert "ZeroDivisionError" in io.err
    io, d = door(b"1 / 0\n2\n")
    assert call(d, "role", "python") == 1 and io.out == "2\n"
    io, d = door()
    assert call(d, "role", "python", "-x") == 2


def test_perm_lists_and_turns_an_apps_permissions(console):
    ws, door = console
    io, d = door()
    assert call(d, "role", "perm", "list", "local.svc") == 0
    assert io.out == "local.svc http on\n"
    io, d = door()
    assert call(d, "role", "perm", "off", "local.svc", "http") == 0
    assert not ws.consent.is_on("local.svc", "http")
    io, d = door()
    assert call(d, "role", "perm") == 0
    assert "local.svc http off\n" in io.out and "local.exits notifications on\n" in io.out
    io, d = door()
    assert call(d, "role", "perm", "on", "local.svc", "mic") == 1
    assert io.err == "perm: local.svc does not ask for mic\n"
    assert call(d, "role", "perm", "on", "nobody", "mic") == 1
    assert call(d, "role", "perm", "flip") == 2


def test_notify_posts_lists_opens_and_clears(console, monkeypatch):
    from runtime import notifications as N
    monkeypatch.setattr(N, "NOTES", N.Notifications())
    _ws, door = console
    io, d = door()
    assert call(d, "role", "notify", "send", "Backup done", "42 files") == 0
    nid = int(io.out)
    assert N.NOTES.banner is not None and N.NOTES.banner.summary == "Backup done"
    io, d = door()
    assert call(d, "role", "notify", "send", "-u", "low", "Quiet") == 0
    io, d = door()
    assert call(d, "role", "notify") == 0
    assert io.out == "%d Moybyte: Quiet\n%d Moybyte: Backup done\n" % (nid + 1, nid)
    assert call(d, "role", "notify", "open", str(nid)) == 0
    assert [n.id for n in N.NOTES.list] == [nid + 1]
    assert call(d, "role", "notify", "clear", "99") == 1
    assert call(d, "role", "notify", "clear") == 0 and N.NOTES.list == []
    assert call(d, "role", "notify", "send", "-u", "loud", "x") == 2


def test_service_starts_stops_and_says_its_state(console):
    _ws, door = console
    io, d = door()
    assert call(d, "role", "service", "status", "local.svc") == 3
    assert io.out.split() == ["-", "stopped", "local.svc"]
    assert call(d, "role", "service", "start", "local.svc") == 0
    pid = S.SCHED.find("local.svc").pid
    io, d = door()
    assert call(d, "role", "service", "status", str(pid)) == 0
    assert io.out.split() == [str(pid), "background", "local.svc"]
    assert call(d, "role", "service", "restart", "local.svc") == 0
    again = S.SCHED.find("local.svc").pid
    assert again != pid
    assert call(d, "role", "service", "stop", "local.svc") == 0
    assert S.SCHED.find("local.svc") is None
    io, d = door()
    assert call(d, "role", "service") == 0
    assert "-     exited 0   local.svc\n" in io.out
    assert call(d, "role", "service", "stop", "local.svc") == 1
    assert call(d, "role", "service", "start", "local.nope") == 1


def test_service_policy_sets_and_shows_and_lives_in_the_settings(console):
    ws, door = console
    io, d = door()
    assert call(d, "role", "service", "policy", "local.svc") == 0
    assert io.out.split() == ["never", "local.svc"]
    assert call(d, "role", "service", "policy", "local.svc", "on-crash") == 0
    assert ws.system.get(S.RESTART_KEY) == {"local.svc": "on-crash"}
    io, d = door()
    assert call(d, "role", "service", "policy") == 0
    assert "on-crash  local.svc\n" in io.out
    assert call(d, "role", "service", "policy", "local.svc", "sometimes") == 2
    assert call(d, "role", "service", "policy", "local.nope") == 1
    S.SCHED.pending["local.svc"] = S.SCHED.wakes.after(10000, lambda: None)
    io, d = door()
    assert call(d, "role", "service", "status", "local.svc") == 3
    assert io.out.split() == ["-", "restarting", "local.svc"]
    assert call(d, "role", "service", "stop", "local.svc") == 0   # the wait dropped
    assert not S.SCHED.pending


class _Req:
    def __init__(self, status, body):
        self.st, self.b, self.closed, self.polls = (status, len(body)), body, False, 0

    def state(self):
        self.polls += 1
        return self.st if self.polls > 2 else None

    def body(self):
        return self.b

    def close(self):
        self.closed = True


class _Http:
    CAP_MAX = 65536

    def __init__(self, status):
        self.status, self.reqs = status, []

    def get(self, url, cap):
        self.reqs.append((url, cap, _Req(self.status, b"<html>" + url.encode())))
        return self.reqs[-1][2]


class _Media:
    def __init__(self):
        self.calls = []
        self.vol = 50

    def play(self, src):
        self.calls.append(("play", src))

    def stop(self):
        self.calls.append(("stop",))

    def now(self):
        return {"state": "playing", "volume": self.vol} if self.calls else None

    def volume(self, v=None):
        if v is not None:
            self.vol = v
        return self.vol

    def next(self):
        return False


def _faces(monkeypatch, **faces):
    from runtime import devices
    monkeypatch.setattr(devices, "api", lambda name: faces.get(name))


def test_device_faces_answer_through_the_door(console, monkeypatch):
    http, media = _Http(200), _Media()
    _faces(monkeypatch, http=http, media=media)
    _ws, door = console
    io, d = door()
    assert call(d, "role", "http", "get", "http://example.com/") == 0
    assert io.out == "<html>http://example.com/" and http.reqs[0][1] == 65536
    assert http.reqs[0][2].closed
    http.status = 404
    io, d = door()
    assert call(d, "role", "http", "get", "http://x/", "10") == 1
    assert io.err == "http: status 404\n" and http.reqs[1][1] == 10
    assert call(d, "role", "http", "http://x/") == 2
    io, d = door()
    assert call(d, "role", "media") == 0 and io.out == "state idle\n"
    assert call(d, "role", "media", "play", "/music/a.mp3") == 0
    io, d = door()
    assert call(d, "role", "media", "volume", "30") == 0 and io.out == "30\n"
    assert call(d, "role", "media", "next") == 1
    assert call(d, "role", "media", "stop") == 0
    assert media.calls == [("play", "/music/a.mp3"), ("stop",)]
    io, d = door()
    assert call(d, "role", "lora", "recv") == 1
    assert io.err == "lora: this console has none\n"


class _Stoppable:
    """A device face whose wait the session's Ctrl-C ends: `opened` until
    the role gives it back."""

    CAP_MAX = 65536

    def __init__(self):
        self.opened = 0

    def get(self, url, cap):
        self.opened += 1
        face = self

        class R:
            def state(self):
                return None

            def close(self):
                face.opened -= 1
        return R()

    def open(self):
        self.opened += 1
        return 16000

    def read(self, buf):
        return 0

    def listen(self, on):
        self.opened += 1

    def recv(self, buf):
        return None

    def scan(self, ms):
        self.opened += 1

    def close(self):
        self.opened -= 1


@pytest.mark.parametrize("argv", [("http", "get", "http://x/"),
                                  ("lora", "recv", "5000"), ("ble", "scan", "5000")])
def test_ctrl_c_ends_a_waiting_command_and_gives_back_its_device(console, monkeypatch, argv):
    """A role command waiting on a device asks the session's face for Ctrl-C
    between polls (moy_sh.interrupted, the serial face's 0x03 or the screen
    face's keys): it exits 130 having closed what it opened."""
    face = _Stoppable()
    _faces(monkeypatch, **{argv[0]: face})
    _ws, door = console
    io, d = door()
    polls = []

    def interrupted():
        polls.append(1)
        return len(polls) > 3
    io.interrupted = interrupted
    assert call(d, "role", *argv) == 130
    assert face.opened == 0 and len(polls) == 4


def test_lease_lists_the_boards_rows(console, monkeypatch):
    from runtime import leases
    monkeypatch.setattr(leases, "pins", lambda: "uart1  17,18     -")
    _ws, door = console
    io, d = door()
    assert call(d, "role", "lease") == 0 and io.out == "uart1  17,18     -\n"


def test_a_role_runs_as_the_shell(console):
    _ws, door = console
    io, d = door()
    L.current(9)
    try:
        assert call(d, "role", "python", "-c",
                    "from runtime import moy_log; print(moy_log.current())") == 0
    finally:
        L.current(1)
    assert io.out == "1\n"


VM_SCRIPT = r'''
import sys
sys.path[:0] = [@TREE@]
import moy_input as I
import moybyte
import sh_door
moybyte.bind(I.HostInputTable())


class IO:
    def __init__(self, stdin=b""):
        self.stdin = stdin
        self.fds = {1: b"", 2: b""}

    def write(self, fd, data):
        self.fds[fd] += bytes(data)
        return len(data)

    def read(self, fd, n):
        got = self.stdin[:n]
        self.stdin = self.stdin[n:]
        return got


class Carts:
    def __init__(self, all):
        self.all = all

    def rehydrate(self, cart):
        return cart

    def reslim(self, cart):
        pass


class WS:
    consent = None

    def __init__(self, carts):
        self.carts = Carts(carts)


HELLO = "def main(argv):\n    print('hello', *argv[1:], sep='-')\n    print(input('? '))\n    return 3\n"
SPIN = "def main(argv):\n    while True:\n        pass\n"
ws = WS([{"id": "local.hello", "type": "command", "src": HELLO},
         {"id": "local.spin", "type": "command", "src": SPIN}])


def door(stdin=b""):
    io = IO(stdin)
    return io, sh_door.ShellDoor(ws, io)


io, d = door(b"Ada\n")
print("LIST", repr(d(b"list\0/bin")), d(b"lookup\0hello"))
print("HELLO", d(b"spawn\x000\0hello\0a\0b"), repr(io.fds[1]))
sh_door.COMMAND_MS = 50
io, d = door()
print("SPIN", d(b"spawn\x000\0spin"), repr(io.fds[2]))
io, d = door(b"x = 6\nx * 7\nfor i in range(2):\n    print(i)\n\n1 / 0\nraise SystemExit(4)\n")
print("REPL", d(b"role\0t\0python"), repr(io.fds[1]), b"ZeroDivisionError" in io.fds[2])
'''


def test_on_the_vm_commands_and_the_repl_run_under_the_guard(tmp_path):
    from unix_mp import require_unix_mp, runtime_tree
    exe = require_unix_mp("moy_input", "moy_log", "moy_guard",
                          why="the shell's commands under MicroPython and its VM abort")
    script = tmp_path / "sh_vm.py"
    script.write_text(VM_SCRIPT.replace("@TREE@", repr(runtime_tree())))
    done = subprocess.run([exe, str(script)], capture_output=True, text=True, timeout=120)
    out = done.stdout
    assert done.returncode == 0, out + done.stderr
    assert "LIST 'hello\\nspin\\n' 1\n" in out, out
    assert "HELLO 3 b'hello-a-b\\n? Ada\\n'\n" in out, out
    assert "SPIN 137 b'killed: ran past 0 s\\n'\n" in out, out
    assert "REPL 4 b'42\\n0\\n1\\n' True\n" in out, out


def test_every_role_the_board_names_is_served():
    import os
    import re
    from runtime import sh_roles
    src = open(os.path.join(os.path.dirname(__file__), "..", "native", "moy_sh",
                            "moy_sh_board.c")).read()
    table = src[src.index("ROLES[] = {"):]
    names = re.findall(r'"([a-z]+)"', table[:table.index("NULL")])
    in_c = src[src.index("C_ROLES[] = {"):]
    in_c = re.findall(r'"([a-z]+)"', in_c[:in_c.index("NULL")])
    served = sorted(n[2:] for n in dir(sh_roles) if n.startswith("r_"))
    assert sorted(names) == sorted(served + in_c) and not set(in_c) & set(served)
