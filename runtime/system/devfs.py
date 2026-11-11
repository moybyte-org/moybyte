"""The device and process files (docs/os_2026-11/system.md section 5): the
smallest file face the shell mounts, over the lease table and the process
table.

  /dev/uart<n>        a leased UART's bytes: read what arrived, write to send
  /dev/gpio<n>        a pin's level: reads "0\\n" or "1\\n" once an open, a write of
                      "0" or "1" drives it
  /proc/<pid>/status  the process's name, pid, kind, state and leases
  /proc/<pid>/log     its lines of the OS log

`open(path, mode)` answers a file (read, write, close, a context manager);
`listdir(path)` names what is under "/dev", "/proc" or "/proc/<pid>"; `cat`
reads a file whole, as the command prints it. Opening a device takes its lease
for the opening process (runtime/system/leases.py: refused EBUSY where another
holds it, EPERM without `lease:<row>`) and closing gives it back; a process
that already holds the lease reads and writes through its own. An SPI host
has no byte stream and no file.

The process rows are the shell (pid 1), the scheduler's services and what
`RUNS` answers, the console's runs: fn() -> [(pid, name, kind, state)].
"""

try:
    import leases as _leases
except ImportError:  # host: the runtime package
    from runtime import leases as _leases

try:
    import scheduler as _sched
except ImportError:  # host: the runtime package
    from runtime import scheduler as _sched

try:
    import moy_log as _L
except ImportError:  # pragma: no cover - an image without the kernel
    _L = None

ENOENT, EISDIR, EINVAL = 2, 21, 22
SHELL = 1
LOG_LINES = 64

RUNS = None


def _pid():
    return _L.current() if _L is not None else SHELL


def _procs():
    """{pid: (name, kind, state)} for every live process."""
    out = {SHELL: ("shell", "shell", "running")}
    for p in _sched.SCHED.procs.values():
        if p.state != _sched.EXITED:
            out[p.pid] = (p.name, p.kind, p.state)
    if RUNS is not None:
        for pid, name, kind, state in RUNS():
            if pid is not None:
                out[pid] = (name, kind, state)
    return out


def _held(pid):
    return [o.lease_name for o in _leases.LEASES.held.get(pid, ())
            if _leases._N.live(o._h)]


def status(pid):
    p = _procs().get(pid)
    if p is None:
        return None
    name, kind, state = p
    return "Name:\t%s\nPid:\t%d\nKind:\t%s\nState:\t%s\nLeases:\t%s\n" % (
        name, pid, kind, state, " ".join(_held(pid)) or "-")


def log(pid, n=LOG_LINES):
    if _L is None:
        return ""
    lines = [r[4] for r in _L.read(0, 1 << 30) if r[3] == pid]
    return "".join(t + "\n" for t in lines[-n:])


class _Text:
    """A file over a text made at open."""

    def __init__(self, text):
        self._b = text.encode()
        self._at = 0

    def read(self, n=-1):
        b = self._b[self._at:] if n is None or n < 0 else self._b[self._at:self._at + n]
        self._at += len(b)
        return b

    def write(self, data):
        raise OSError(EINVAL, "read-only")

    def close(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


class _Dev:
    """A device file over a lease: the opener's own, or one taken for the
    file and given back by its close."""

    def __init__(self, name, mode):
        L = _leases.LEASES
        pid = _pid()
        self._obj = L.of(pid, name)
        self._own = self._obj is None
        self._gpio = name.startswith("gpio")
        self._read = False              # a pin's level reads once an open
        if self._own:
            cfg = None
            if self._gpio:
                cfg = {"mode": _leases.Pin.OUT if "w" in mode else _leases.Pin.IN}
            self._obj = L.take(pid, name, cfg)

    def read(self, n=-1):
        if self._gpio:
            if self._read:
                return b""
            self._read = True
            return ("%d\n" % self._obj.value()).encode()
        got = self._obj.read() if n is None or n < 0 else self._obj.read(n)
        return got or b""

    def write(self, data):
        if self._gpio:
            v = bytes(data).strip()
            if v not in (b"0", b"1"):
                raise OSError(EINVAL, "a pin takes 0 or 1")
            self._obj.value(int(v))
            return len(data)
        return self._obj.write(data)

    def close(self):
        if self._own and self._obj is not None:
            _leases.LEASES.drop(self._obj)
        self._obj = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def _parts(path):
    return [p for p in path.split("/") if p]


def _devices():
    return [n for n in _leases.LEASES.names() if not n.startswith("spi")]


def listdir(path):
    parts = _parts(path)
    if parts == []:
        return ["dev", "proc"]
    if parts == ["dev"]:
        return _devices()
    if parts == ["proc"]:
        return [str(p) for p in sorted(_procs())]
    if len(parts) == 2 and parts[0] == "proc" and parts[1].isdigit() \
            and int(parts[1]) in _procs():
        return ["log", "status"]
    raise OSError(ENOENT, path)


def open(path, mode="r"):  # noqa: A001 -- the file system's verb
    parts = _parts(path)
    if len(parts) == 2 and parts[0] == "dev" and parts[1] in _devices():
        return _Dev(parts[1], mode)
    if len(parts) == 3 and parts[0] == "proc" and parts[1].isdigit():
        pid = int(parts[1])
        if parts[2] == "status":
            text = status(pid)
            if text is not None:
                return _Text(text)
        elif parts[2] == "log" and pid in _procs():
            return _Text(log(pid))
    if parts in (["dev"], ["proc"]) or (len(parts) == 2 and parts[0] == "proc"):
        raise OSError(EISDIR, path)
    raise OSError(ENOENT, path)


def cat(path):
    """`path` read whole, as text: what `cat` prints."""
    with open(path) as f:
        out = []
        while True:
            b = f.read(256)
            if not b:
                break
            out.append(b)
    return b"".join(out).decode("utf-8", "replace")
