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

A COMPILED COMMAND (`"runtime": "wasm"`) is a WASI program: the same process
row and consent, its module's `_start` run by the wasm runtime's `command`
(native/moy_wasm/moy_wasi.h) over the session's own descriptors, with the
folder its `files` permission names (`wasi_roots`) as its only files and
COMMAND_MS as its budget; a trap's exit code is TRAPPED, a module that will
not run NOT_RUN.

A NATIVE COMMAND (`"moybyte": {"native": [chips]}`) is the same door over
the native tier (native/moy_elf/moy_os.h): its module's `main` on a task of
its own, the same roots and budget, run only once "full_access" is granted
(`app_perms.check`), and its socket rows only when "network" is.

Either tier's command in the foreground is a job, as a role command is: its
module runs on a thread of its own, the door answers RUNNING and each `step
JOB` serves its session's calls for STEP_MS, so the frame goes on while it
runs; `stop JOB` (Ctrl-C) ends it with 130. Both runtimes answer one
contract: start(cart, argv, roots, pid, budget_ms) -> (0, None) or
(a negative code, why), step(pid, ms) and cancel(pid) -> (code, why), RUNNING
while it goes on. A command run with `&` is waited out in the door, the
board's task watchdog fed meanwhile.
"""

import gc

try:
    import devfs as _devfs
except ImportError:  # host: the runtime package
    from runtime import devfs as _devfs

try:
    from ticks import _ticks_ms, _ticks_diff
except ImportError:  # host: the runtime package
    from runtime.ticks import _ticks_ms, _ticks_diff

try:
    import scheduler as _sched
except ImportError:  # host: the runtime package
    from runtime import scheduler as _sched

try:
    import moy_app as _moy_app
except ImportError:  # host: the runtime package
    from runtime import moy_app as _moy_app

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
RUNNING = -100                  # a role command goes on: the session steps it again
EPERM, ESRCH, EIO = 1, 3, 5
SHELL = 1
COMMAND_MS = 30000              # a command's or a REPL's run, at most
STEP_MS = 8                     # a role command's steps in one crossing, at most this long
# The process table's exit statuses (native/moy_kernel/moy_proc_exit.h): a
# command refused its start, ended by Ctrl-C, trapped, killed.
NOT_RUN, INTERRUPTED, TRAPPED, KILLED = _L.NOT_RUN, _L.INTERRUPTED, _L.TRAPPED, _L.KILLED
WASI_TRAPPED = -1               # the wasm runtime's `command` answer for a trap
EXITS = 8                       # background commands' exit codes kept for `wait`
LINE = 256                      # what a stdin read takes at a time


def _cron():
    try:
        import cron
    except ImportError:  # host: the runtime package
        from runtime import cron
    return cron


def _module(name):
    try:
        return __import__(name)
    except ImportError:  # host: the runtime package
        return getattr(__import__("runtime." + name), name)


def console_text(ws):
    """/proc/console: what only the console knows of itself, one `key: value`
    a line, a row it cannot answer left out (neofetch's, native/moy_sh)."""
    rows = []

    def row(key, fn):
        try:
            v = fn()
        except Exception:  # noqa: BLE001 -- a row the tier cannot answer
            v = None
        if v:
            rows.append("%s: %s\n" % (key, str(v).replace("\n", " ")))

    row("name", lambda: _module("ssh_service").hostname(ws))
    row("board", lambda: _module("settings_layer").board_name())
    row("firmware", lambda: ws.updater.version_label() if ws.updater is not None else None)
    row("display", lambda: "%dx%d" % (ws.glass_canvas.w, ws.glass_canvas.h))
    row("theme", lambda: ws.look.theme_name)

    def wifi():
        if ws.wifi is None:
            return None
        connected, ssid, ip = ws.wifi.status()
        return "%s (%s)" % (ssid, ip) if connected and ip else ssid if connected else "off"

    row("wifi", wifi)
    return "".join(rows)


def _rows(ws):
    """[(pid, ppid, kind, state, name)]: the console's processes."""
    out = []
    for pid, (name, kind, state) in sorted(_devfs._procs().items()):
        out.append((pid, 0 if pid == SHELL else SHELL, kind, state, name))
    return out


def _figures(ws):
    """{pid: (cpu, mem)}: what the task manager measures, per mille of a core
    and bytes, None where unmeasured (runtime/shell/procs.py's rows)."""
    out = {}
    for p in _devfs._sched.SCHED.procs.values():
        out[p.pid] = (p.cpu, getattr(p, "mem", None))
    procs = getattr(ws, "procs", None)
    rows = getattr(procs, "rows", None)
    if rows is not None:
        for r in rows():
            if r[6] is not None:
                out[r[6]] = (r[4], r[5])
    return out


def _row(ws, pid):
    for r in ws.procs.rows():
        if r[6] == pid:
            return r
    return None


def _io():
    try:
        import moy_sh
        moy_sh.DOOR
        return moy_sh
    except (ImportError, AttributeError):   # a folder of that name on CPython's path
        pass
    try:                # CPython: the ctypes binding (runtime/host/moy_sh.py)
        from runtime import moy_sh
        return moy_sh
    except ImportError:  # the browser and the desktop MicroPython: no C shell
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


def _noticed():
    """What a compiled program sent from its own thread to a process the VM
    serves (moy_send) is served at the next frame's start, as a send from
    Python is (moybyte's MESSAGES)."""
    if _sched._E.ready():
        _moybyte.WAKES.queued = 1


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


def wasi_roots(granted):
    """The folder a compiled command's granted permissions reach, relative to
    the store: the one the C policy (`moy_app.policy`) makes an app's files
    grant for, by its kind's place on disk (moy_store_base.FILE_KIND_DIRS).
    No folder is none: a manifest naming two is refused before it runs
    (`moy_app.manifest_error`), and Downloads, `.Trash` and `.history` are
    no grant's."""
    try:
        from moy_store_base import FILE_KIND_DIRS
    except ImportError:  # host: the runtime package
        from runtime.moy_store_base import FILE_KIND_DIRS
    kind = _moy_app.policy(granted)[1]
    return [] if kind is None else ["files/" + FILE_KIND_DIRS[kind]]


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
        self.jobs = {}                  # JOB -> (name, generator): role commands going on

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
        figs = _figures(self.ws)
        out = []
        for pid, ppid, kind, state, name in _rows(self.ws):
            cpu, mem = figs.get(pid, (None, None))
            out.append("%d %d %s %s %d %s %s\n" % (pid, ppid, kind, state, mem or 0,
                                                 "-" if cpu is None else int(cpu),
                                                 name.replace(" ", "_")))
        return "".join(out)

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
            if pid in _devfs._procs():
                return 0 if sig == 0 or _cron().CRON.kill(pid) else ESRCH
            return ESRCH
        if sig == 0:
            return 0
        if row[2] == "service":
            self.ws.procs.stop_service(pid)
        else:
            self.ws.tasks_close(row[0])
        self.ws._dirty = True
        return 0

    def v_read(self, path):
        if path == "/proc/console":
            return console_text(self.ws).encode()
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
        return ("".join(n + ("/\n" if folder else "\n") for n in names)
                + ("console\n" if folder else ""))

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

    def v_rescan(self, *names):
        """The cart folders SFTP changed, by name: the shelf re-reads them, as
        it does a browser sync's (`ws.rescan_carts`)."""
        self.ws.rescan_carts(list(names) or None)
        return 0

    def v_lookup(self, name):
        return 1 if find_command(self.ws, name) is not None else 0

    def v_spawn(self, bg, *argv):
        cart = find_command(self.ws, argv[0]) if argv else None
        if cart is None:
            return ABSENT
        name = argv[0][argv[0].find("/") + 1:]
        bg, _, job = bg.partition(":")
        return self.run_command(cart, name, [name] + list(argv[1:]), bg == "1", job or None)

    def run_command(self, cart, name, argv, bg, job=None):
        """Command `cart` run as `argv`: its exit code, or in the background
        its pid (the code kept for `status`). A foreground compiled command
        given a `job` (the session's, as `role` names it) answers RUNNING and
        goes on in `step JOB`s."""
        try:
            import app_perms
        except ImportError:  # host: the runtime package
            from runtime import app_perms
        tier = app_perms.tier_of(cart)
        if tier in ("wasm", "elf"):
            return self.run_compiled(cart, name, argv, bg, tier, job)
        got = _sched.prepared(self.ws, cart)
        if got is None:
            self.err(name, "no source")
            return NOT_RUN
        run, rec, have, env = got
        rec = self._first_run(run, rec, bg)
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

    def _first_run(self, run, rec, bg):
        """The record command `run` runs under, from `rec` (`Consent.for_run`'s):
        where it is asked first, the install prompt's lines and "Open it?"
        in the foreground's terminal, a yes recording the consent as an
        install does and the record it made coming back; in the background,
        or on a no, `rec` as it was (UNASKED: the launch refuses)."""
        try:
            import app_perms
        except ImportError:  # host: the runtime package
            from runtime import app_perms
        if rec is not app_perms.UNASKED or bg or self.io is None:
            return rec
        self.out("".join(ln + "\n" for ln in app_perms.prompt(run)))
        self.out("Open it? [y/N] ")
        line = In(self.io).readline().strip().lower()
        if not line:
            self.out("\n")
        if line not in ("y", "yes"):
            return rec
        consent = self.ws.consent
        aid = app_perms.app_id(run)
        consent.record(aid, list(run.get("permissions") or ()))
        return consent.get(aid)

    def run_compiled(self, cart, name, argv, bg, tier, job=None):
        """Compiled command `cart` run as `argv`: a WASI module's `_start`
        through the wasm runtime's `command` (native/moy_wasm/moy_wasi.h), a
        native module's `main` through the native runtime's
        (native/moy_elf/moy_os.h), a process of its own holding what its
        consent grants, the folder its `files` permission names its only
        files, its app calls answered for a grant of its own
        (`compiled_run`). Its exit code, or in the background its pid (the code kept for
        `status`). In the foreground with a `job` it is a job the door steps
        (`_compiled_steps`): RUNNING. Its process has an event queue (its app
        id and its manifest's `receives`), which the program reads as its
        event descriptor (native/moy_kernel/moy_evq.h) and its end closes."""
        try:
            import app_perms
        except ImportError:  # host: the runtime package
            from runtime import app_perms
        rt = (getattr(self.ws, "runtimes", None) or {}).get(tier)
        if rt is None or not hasattr(rt, "command"):
            self.err(name, "needs the %s tier, which this console lacks"
                     % ("compiled" if tier == "wasm" else "native"))
            return NOT_RUN
        carts = getattr(self.ws, "carts", None)
        run = carts.rehydrate(cart) if carts is not None else cart
        consent = getattr(self.ws, "consent", None)
        rec = self._first_run(run, consent.for_run(run), bg) if consent is not None else None
        have = app_perms.services(self.ws) if consent is not None else ()
        granted, _absent, refusal = app_perms.check(run, rec, have)
        if refusal is None:
            refusal = _moy_app.manifest_error(run.get("permissions"))
        if refusal is not None:
            self.err(name, refusal)
            return NOT_RUN
        S = _sched.SCHED
        pid = _L.spawn(name) if _L is not None else S._next_pid()
        p = S.procs[pid] = _sched.Proc(pid, _sched.COMMAND, name, run)
        p.granted = tuple(granted)
        S._open(pid, False)
        roots = wasi_roots(granted)
        app = _sched.compiled_run(self.ws, p, run)
        net = ("network" in granted, app) if tier == "elf" else (app,)
        if job is not None and not bg and hasattr(rt, "start"):
            self.v_stop(job)
            code, why = self._as_pid(pid, rt.start, run, argv, roots, pid, COMMAND_MS, *net)
            if why is None:
                self.jobs[job] = (name, self._compiled_steps(rt, name, pid))
                return self.v_step(job)
            return self._compiled_end(name, pid, code, why, False)
        try:
            if tier == "elf":
                code, why = self._as_pid(pid, rt.command, run, argv, roots, pid,
                                         pid if bg else 0, COMMAND_MS, *net)
            else:
                code, why = self._as_pid(pid, rt.command, run, argv, roots, pid if bg else 0,
                                         COMMAND_MS, app)
        except BaseException:
            self._compiled_gone(pid)
            raise
        _noticed()
        code = self._compiled_end(name, pid, code, why, bg)
        if not bg:
            return code
        self.exits = ([(pid, code)] + self.exits)[:EXITS]
        return pid

    def _compiled_steps(self, rt, name, pid):
        """A running compiled command as a job, either tier's: each step
        serves its session's calls for STEP_MS, as process `pid`; its return
        is the exit code, and its close (`stop JOB`, Ctrl-C) ends the
        process with 130."""
        code, why = rt.RUNNING, None
        try:
            while code == rt.RUNNING:
                code, why = self._as_pid(pid, rt.step, pid, STEP_MS)
                _sched.SCHED.calls()
                _noticed()
                if code == rt.RUNNING:
                    yield False
        finally:
            if code == rt.RUNNING:
                code, why = self._as_pid(pid, rt.cancel, pid)
            code = self._compiled_end(name, pid, code, why, False)
        return code

    @staticmethod
    def _as_pid(pid, fn, *args):
        was = _L.current() if _L is not None else None
        if _L is not None:
            _L.current(pid)
        try:
            return fn(*args)
        finally:
            if _L is not None:
                _L.current(was)

    @staticmethod
    def _compiled_gone(pid):
        # The row first: the wakes' end drops a command's row (the
        # scheduler's _ended), and its grant would outlive it.
        p = _sched.SCHED.procs.pop(pid, None)
        _moybyte.WAKES.end(pid)
        ctx = getattr(p, "ctx", None)
        if ctx is not None:
            p.ctx = None
            ctx.end()

    def _compiled_end(self, name, pid, code, why, bg):
        """Process `pid`'s end at runtime answer (`code`, `why`): what it held
        swept, the reason said (on fd 2, or in the background the log's), the
        exit logged; its exit code (a trap's TRAPPED, a refusal's NOT_RUN)."""
        self._compiled_gone(pid)
        if why is not None:
            text = "%s: %s" % (name, why)
            if bg and _L is not None:
                _L.write(_L.ERR, pid, text)
            else:
                self.err(name, why)
            code = TRAPPED if code == WASI_TRAPPED else NOT_RUN
        if _L is not None:
            _L.write(_L.INFO, pid, "exit %d" % code)
        return code

    # A ROLE COMMAND (runtime/shell/sh_roles.py's r_<name>) answers its exit
    # code, or is a generator: one that waits yields between its polls, and
    # its `return` is the code. `role JOB NAME ARGS...` starts it and steps
    # it; while it goes on the door answers RUNNING and the session (JOB
    # names it) crosses `step JOB` again a frame later, each crossing
    # stepping it for STEP_MS at most (a bare `yield` ends the crossing: it
    # waits on something), so no frame waits on it; `stop JOB` (Ctrl-C)
    # closes it, its `finally` giving back what it took.

    def v_role(self, job, *argv):
        if not argv:
            return ABSENT
        try:
            import sh_roles
        except ImportError:  # host: the runtime package
            from runtime import sh_roles
        fn = getattr(sh_roles, "r_" + argv[0], None)
        if fn is None:
            return ABSENT
        self.v_stop(job)
        r = self._as_shell(argv[0], lambda: fn(self, list(argv[1:])))
        if not hasattr(r, "send"):
            return r
        self.jobs[job] = (argv[0], r)
        return self.v_step(job)

    def v_step(self, job):
        got = self.jobs.get(job)
        if got is None:
            return ABSENT
        name, gen = got
        t0 = _ticks_ms()

        def step():
            while True:
                if not next(gen) or _ticks_diff(_ticks_ms(), t0) >= STEP_MS:
                    return RUNNING
        r = self._as_shell(name, step)
        if r != RUNNING:
            self.jobs.pop(job, None)
        return r

    def v_stop(self, job):
        got = self.jobs.pop(job, None)
        if got is None:
            return ABSENT
        self._as_shell(got[0], got[1].close)
        return INTERRUPTED

    def _as_shell(self, name, fn):
        """fn() as the shell (pid 1): its answer, a generator's return value
        at its end, 1 for its failure (said on fd 2)."""
        try:
            import sh_roles
        except ImportError:  # host: the runtime package
            from runtime import sh_roles
        was = _L.current() if _L is not None else None
        if _L is not None:
            _L.current(SHELL)
        try:
            return fn()
        except StopIteration as e:
            return e.args[0] if e.args and e.args[0] is not None else 0
        except OSError as e:
            self.err(name, sh_roles.why(e))
            return 1
        except Exception as e:  # noqa: BLE001 -- a role's failure is its exit
            self.err(name, traceback_text(e).rstrip("\n"))
            return 1
        finally:
            if _L is not None:
                _L.current(was)
