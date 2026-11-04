"""The C shell's crossings into the console (native/moy_sh/moy_sh_board.h):
the ROLE door's row `moy_sh.DOOR`, `verb NUL arg NUL ...`, answered here.

What the console knows and the kernel does not: its processes (the task
manager's rows and the scheduler's, as /proc names them), /proc and /dev
(runtime/system/devfs.py, the one implementation), df and the Python heap.
A process is ended as the task manager ends it: a service is stopped, a run's
window closed. The installed commands and the console's role commands answer
ABSENT until they are served: the shell then says "not served here".
"""

import gc

try:
    import devfs as _devfs
except ImportError:  # host: the runtime package
    from runtime import devfs as _devfs

ABSENT = -8
EPERM, ESRCH, EIO = 1, 3, 5
SHELL = 1


def _rows(ws):
    """[(pid, ppid, kind, state, name)]: the console's processes."""
    out = []
    for pid, (name, kind, state) in sorted(_devfs._procs().items()):
        out.append((pid, 0 if pid == SHELL else SHELL, kind, state, name))
    return out


def _row(ws, pid):
    for r in ws.procs.rows():
        if r[6] == pid:
            return r
    return None


class ShellDoor:
    """`door(fields)` -> int or bytes, for RoleDoor's MOY_SH_DOOR row."""

    def __init__(self, ws):
        self.ws = ws

    def __call__(self, args):
        fields = bytes(args).split(b"\0")
        verb = fields[0].decode()
        argv = [f.decode("utf-8", "replace") for f in fields[1:]]
        fn = getattr(self, "v_" + verb, None)
        if fn is None:
            return ABSENT
        return fn(*argv)

    def v_ps(self):
        return "".join("%d %d %s %s 0 %s\n" % (pid, ppid, kind, state, name.replace(" ", "_"))
                       for pid, ppid, kind, state, name in _rows(self.ws))

    def v_status(self, pid):
        pid = int(pid)
        procs = _devfs._procs()
        if pid not in procs:
            return ABSENT
        return "running"

    def v_kill(self, pid, sig):
        pid, sig = int(pid), int(sig)
        if pid == SHELL:
            return EPERM
        row = _row(self.ws, pid)
        if row is None:
            return 0 if sig == 0 and pid in _devfs._procs() else ESRCH
        if sig == 0:
            return 0
        if row[2] == "service":
            self.ws.procs.stop_service(pid)
        else:
            self.ws.tasks_close(row[0])
        self.ws._dirty = True
        return 0

    def v_read(self, path):
        try:
            with _devfs.open(path, "rb") as f:
                data = f.read()
        except OSError as e:
            return ABSENT if e.args and e.args[0] == _devfs.ENOENT else -EIO
        return data if isinstance(data, bytes) else data.encode()

    def v_write(self, path, data):
        try:
            with _devfs.open(path, "wb") as f:
                f.write(data.encode())
        except OSError as e:
            return e.args[0] if e.args and e.args[0] != _devfs.ENOENT else ABSENT
        return 0

    def v_list(self, path):
        if path in ("/", "/bin"):
            return ABSENT if path == "/" else ""
        try:
            names = _devfs.listdir(path)
        except OSError:
            return ABSENT
        folder = path.rstrip("/") == "/proc"
        return "".join(n + ("/\n" if folder else "\n") for n in names)

    def v_df(self, path):
        try:
            import os
            st = os.statvfs(path)
        except (OSError, AttributeError):
            return ABSENT
        return "%d %d" % (st[1] * st[2], st[1] * st[3])

    def v_mem(self):
        free = gc.mem_free() if hasattr(gc, "mem_free") else 0
        used = gc.mem_alloc() if hasattr(gc, "mem_alloc") else 0
        return "%d %d" % (free + used, free) if free + used else ABSENT

    def v_lookup(self, name):
        return 0

    def v_spawn(self, bg, *argv):
        return ABSENT

    def v_role(self, *argv):
        return ABSENT
