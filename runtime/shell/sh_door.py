"""The C shell's crossings into the console (native/moy_sh/moy_sh_board.h):
the ROLE door's row `moy_sh.DOOR`, `verb NUL arg NUL ...`, answered here.

What the console knows and the kernel does not: its processes (the task
manager's rows and the scheduler's, as /proc names them), /proc and /dev
(runtime/system/devfs.py, the one implementation), df, the Python heap, the
installed commands and the role commands (runtime/shell/sh_roles.py). A
process is ended as the task manager ends it: a service is stopped, a run's
window closed.

THE INSTALLED COMMANDS (docs/os_2026-11/system.md section 1) are the carts
whose manifest says `"type": "command"`, each on the PATH (/bin) under its
id's name (`local.hello` is `hello`; where two carts share a name the first id
in order holds it) and as `<id>/<name>`. A spawn runs one as a process of its
own: a pid from the log, a row of the scheduler's (kind "command") holding
the permissions its consent grants (the scheduler's `prepared`, as a service
starts), its main.py run with `moybyte`, `print` and `input` in its namespace
and then `main(argv)`, whose answer is the exit code (None 0, SystemExit's
code). Its streams are the session's, through the door (`moy_sh.write`,
`moy_sh.read`): `print` writes fd 1 (`file=` another stream), `input` reads
a line of fd 0. It runs under the guard for COMMAND_MS at most, and its end
sweeps what it held (`moybyte.WAKES.end`). A command run with `&` runs to its
end before the door answers, its output into the OS log under its pid; the
pid answers, and `wait` reads its exit code (`status`).
"""

import gc

try:
    import devfs as _devfs
except ImportError:  # host: the runtime package
    from runtime import devfs as _devfs

try:
    import scheduler as _sched
except ImportError:  # host: the runtime package
    from runtime import scheduler as _sched

try:
    import moybyte as _moybyte
except ImportError:  # host: the runtime package
    from runtime import moybyte as _moybyte

try:
    import moy_log as _L
except ImportError:  # pragma: no cover - an image without the kernel
    _L = None

try:
    import moy_guard as _guard
except ImportError:  # CPython and the browser: no VM abort
    _guard = None

ABSENT = -8
EPERM, ESRCH, EIO = 1, 3, 5
SHELL = 1
COMMAND_MS = 30000              # a command's or a REPL's run, at most
NOT_RUN = 126                   # the exit code of a command refused its start
KILLED = 137
EXITS = 8                       # background commands' exit codes kept for `wait`
LINE = 256                      # what a stdin read takes at a time


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


def _io():
    try:
        import moy_sh
        return moy_sh
    except ImportError:  # host: no C shell; a test hands the door its own
        return None


# -- a program's streams ---------------------------------------------------------


class Out:
    """Stream fd 1 or 2 of the session through the door; for a background
    command (`pid`), its lines into the OS log under that pid instead."""

    def __init__(self, io, fd, pid=None):
        self.io = io
        self.fd = fd
        self.pid = pid
        self.held = ""

    def write(self, s):
        if self.pid is None:
            b = s.encode() if isinstance(s, str) else bytes(s)
            if b and self.io is not None:
                self.io.write(self.fd, b)
            return len(s)
        text = self.held + (s if isinstance(s, str) else bytes(s).decode("utf-8", "replace"))
        lines = text.split("\n")
        self.held = lines.pop()
        for line in lines:
            if _L is not None:
                _L.write(_L.INFO if self.fd == 1 else _L.ERR, self.pid, line)
        return len(s)

    def flush(self):
        if self.pid is not None and self.held:
            self.held += "\n"
            self.write("")


class In:
    """Stream fd 0 of the session: read whole, or a line at a time."""

    def __init__(self, io):
        self.io = io
        self.buf = b""
        self.eof = io is None

    def _more(self):
        if self.eof:
            return False
        got = self.io.read(0, LINE)
        if not got:
            self.eof = True
            return False
        self.buf += got
        return True

    def readline(self):
        """The next line with its newline, the last without one, "" at the end."""
        while b"\n" not in self.buf and self._more():
            pass
        i = self.buf.find(b"\n")
        line, self.buf = (self.buf, b"") if i < 0 else (self.buf[:i + 1], self.buf[i + 1:])
        return line.decode("utf-8", "replace")

    def read(self):
        """Every byte left."""
        while self._more():
            pass
        b, self.buf = self.buf, b""
        return b


def stdio_names(stdin, stdout, stderr):
    """The names a program's namespace gets for its streams: print, input."""

    def _print(*args, sep=" ", end="\n", file=None, flush=False):
        (file or stdout).write(sep.join([str(a) for a in args]) + end)

    def _input(prompt=""):
        if prompt:
            stdout.write(prompt)
        line = stdin.readline()
        if not line:
            raise EOFError
        return line[:-1] if line[-1:] == "\n" else line

    return {"print": _print, "input": _input}


def exit_code(v, stderr):
    """A program's answer or SystemExit's argument as an exit code."""
    if v is None:
        return 0
    if not isinstance(v, int):
        stderr.write(str(v) + "\n")
        return 1
    return v & 0xFF


def traceback_text(exc):
    try:
        from os_log import traceback_text as tt
    except ImportError:  # host: the runtime package
        from runtime.os_log import traceback_text as tt
    return tt(exc) or repr(exc)


def run_program(pid, fn, stderr):
    """fn() as process `pid`, under the guard: its exit code."""
    was = _L.current() if _L is not None else None
    if _L is not None:
        _L.current(pid)
    try:
        if _guard is not None:
            r = _guard.call(fn, COMMAND_MS * 1000)
            if r is _guard.ABORTED:
                stderr.write("killed: ran past %d s\n" % (COMMAND_MS // 1000))
                return KILLED
        else:
            r = fn()
        return exit_code(r, stderr)
    except SystemExit as e:
        return exit_code(e.args[0] if e.args else None, stderr)
    except Exception as e:  # noqa: BLE001 -- a program's crash is its exit
        stderr.write(traceback_text(e).rstrip("\n") + "\n")
        return 1
    finally:
        if _L is not None:
            _L.current(was)


# -- the installed commands ---------------------------------------------------------


def command_name(cart):
    """The name a command cart puts on the PATH, or None for any other cart."""
    if cart.get("type") != "command":
        return None
    cid = cart.get("id") or ""
    i = cid.find(".")
    return cid[i + 1:] if i > 0 else None


def commands(ws):
    """[(name, cart)] the PATH holds, by name; a name two carts share is the
    first id's."""
    carts = getattr(ws, "carts", None)
    seen = {}
    for c in (carts.all if carts is not None else ()):
        n = command_name(c)
        if n is not None and (n not in seen or c["id"] < seen[n]["id"]):
            seen[n] = c
    return sorted(seen.items())


def find_command(ws, name):
    if "/" in name:
        cid, _, n = name.partition("/")
        carts = getattr(ws, "carts", None)
        for c in (carts.all if carts is not None else ()):
            if c.get("id") == cid and command_name(c) == n:
                return c
        return None
    for n, c in commands(ws):
        if n == name:
            return c
    return None


class ShellDoor:
    """`door(fields)` -> int or bytes, for RoleDoor's MOY_SH_DOOR row."""

    def __init__(self, ws, io=None):
        self.ws = ws
        self.io = io if io is not None else _io()
        self.exits = []                 # [(pid, code)] of background commands

    def __call__(self, args):
        fields = bytes(args).split(b"\0")
        verb = fields[0].decode()
        argv = [f.decode("utf-8", "replace") for f in fields[1:]]
        fn = getattr(self, "v_" + verb, None)
        if fn is None:
            return ABSENT
        return fn(*argv)

    def out(self, text):
        if self.io is not None and text:
            self.io.write(1, text.encode())

    def err(self, name, text):
        if self.io is not None:
            self.io.write(2, ("%s: %s\n" % (name, text)).encode())

    def v_ps(self):
        return "".join("%d %d %s %s 0 %s\n" % (pid, ppid, kind, state, name.replace(" ", "_"))
                       for pid, ppid, kind, state, name in _rows(self.ws))

    def v_status(self, pid):
        pid = int(pid)
        for p, code in self.exits:
            if p == pid:
                return "exited %d" % code
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
        if path == "/":
            return ABSENT
        if path.rstrip("/") == "/bin":
            return "".join(n + "\n" for n, _c in commands(self.ws))
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
        return 1 if find_command(self.ws, name) is not None else 0

    def v_spawn(self, bg, *argv):
        cart = find_command(self.ws, argv[0]) if argv else None
        if cart is None:
            return ABSENT
        name = argv[0][argv[0].find("/") + 1:]
        return self.run_command(cart, name, [name] + list(argv[1:]), bg == "1")

    def run_command(self, cart, name, argv, bg):
        """Command `cart` run as `argv`: its exit code, or in the background
        its pid (the code kept for `status`)."""
        try:
            import app_perms
        except ImportError:  # host: the runtime package
            from runtime import app_perms
        got = _sched.prepared(self.ws, cart)
        if got is None:
            self.err(name, "no source")
            return NOT_RUN
        run, rec, have, env = got
        granted, absent, refusal = app_perms.check(run, rec, have)
        if refusal is not None:
            self.err(name, refusal)
            return NOT_RUN
        S = _sched.SCHED
        pid = _L.spawn(name) if _L is not None else S._next_pid()
        p = S.procs[pid] = _sched.Proc(pid, _sched.COMMAND, name, run)
        p.granted = tuple(granted)
        stdin = In(None if bg else self.io)
        stdout = Out(self.io, 1, pid if bg else None)
        stderr = Out(self.io, 2, pid if bg else None)
        ns = {"__name__": "__main__", "moybyte": _moybyte}
        if env:
            ns.update(env)
        for n in app_perms.absent_names(absent, rec):
            ns[n] = None
        ns.update(stdio_names(stdin, stdout, stderr))
        src = run["src"]

        def body():
            exec(compile(src, "<%s>" % name, "exec"), ns)
            main = ns.get("main")
            return main(argv) if main is not None else None

        try:
            code = run_program(pid, body, stderr)
        finally:
            stdout.flush()
            stderr.flush()
            _moybyte.WAKES.end(pid)
            S.procs.pop(pid, None)
        if _L is not None:
            _L.write(_L.INFO, pid, "exit %d" % code)
        if not bg:
            return code
        self.exits = ([(pid, code)] + self.exits)[:EXITS]
        return pid

    def v_role(self, *argv):
        if not argv:
            return ABSENT
        try:
            import sh_roles
        except ImportError:  # host: the runtime package
            from runtime import sh_roles
        fn = getattr(sh_roles, "r_" + argv[0], None)
        if fn is None:
            return ABSENT
        was = _L.current() if _L is not None else None
        if _L is not None:
            _L.current(SHELL)
        try:
            return fn(self, list(argv[1:]))
        except OSError as e:
            self.err(argv[0], sh_roles.why(e))
            return 1
        except Exception as e:  # noqa: BLE001 -- a role's failure is its exit
            self.err(argv[0], traceback_text(e).rstrip("\n"))
            return 1
        finally:
            if _L is not None:
                _L.current(was)
