"""The OS log's Python half (native/moy_kernel/moy_log.h; the design is
docs/os_2026-11/processes.md section 9): a run as a process of the log, a
process's print, the crash intake, and what a reader asks of the ring.

  begin(name)        a run starts: its pid, current until `end`
  end(pid, why)      it ends; the shell is current again
  printed(text)      a process's print: a record under the current pid,
                     mirrored to serial
  crashed(pid, exc, text)   a run's failure at `crit`: a Python exception's
                     traceback, else `text` (the panel's words); its seq
  crash_of(pid)      the newest `crit` record of `pid`: (seq, text) or None.
                     What "this app crashed: details" and a background app's
                     crash notification open
  records(since, n)  the ring from `since` on, oldest first
  next_seq()         the seq the next record takes
  install_logging()  a VM with no `logging` gets moy_logging under that name
                     (run at import)

Every function is a quiet no-op where the image has no `moy_log`.
"""

try:
    import moy_log as _L
except ImportError:  # pragma: no cover - an image without the kernel
    _L = None

if _L is not None:
    _L.current(_L.SHELL)     # a VM starts in the shell, a stopped run's pid done

try:
    import sys as _sys
    import io as _io
except ImportError:  # pragma: no cover
    _sys = _io = None

CRIT = 2
INFO = 6
SHELL = 1


def begin(name):
    if _L is None:
        return SHELL
    pid = _L.spawn(name)
    _L.current(pid)
    _L.write(_L.INFO, pid, "start", name)
    return pid


def end(pid, why="exit"):
    if _L is None or pid is None or pid == SHELL:
        return
    _L.write(_L.INFO, pid, why)
    _L.current(_L.SHELL)


def printed(text):
    """`text` (one print's whole output, its end included) as a record of the
    current process, and on serial as print would have put it."""
    try:
        print(text, end="")
    except Exception:  # noqa: BLE001 -- a print never fails its caller
        pass
    if _L is not None:
        t = text[:-1] if text.endswith("\n") else text
        _L.write(_L.INFO, None, t)


def traceback_text(exc):
    """`exc`'s traceback as the VM prints it, or None."""
    try:
        if hasattr(_sys, "print_exception"):          # MicroPython
            buf = _io.StringIO()
            _sys.print_exception(exc, buf)
            return buf.getvalue().rstrip("\n")
        import traceback                                # CPython
        return "".join(traceback.format_exception(
            type(exc), exc, exc.__traceback__)).rstrip("\n")
    except Exception:  # noqa: BLE001 -- the panel's words stand in
        return None


def crashed(pid, exc=None, text=None):
    """A run's failure into the ring at `crit`, under `pid` (None: current).
    Answers the record's seq, or 0."""
    if _L is None:
        return 0
    body = traceback_text(exc) if isinstance(exc, BaseException) else None
    if not body:
        body = text if text else "crashed"
    seq = _L.next()
    _L.write(_L.CRIT, pid, body)
    return seq


def failed(pid, text):
    """A run refused or stopped before its code ran, at `err`."""
    if _L is not None and text:
        _L.write(_L.ERR, pid, text)


def crash_of(pid):
    if _L is None or pid is None:
        return None
    got = None
    since = 0
    while True:
        recs = _L.read(since, 32)
        if not recs:
            return got
        for seq, _t, level, p, text in recs:
            if p == pid and level <= _L.CRIT:
                got = (seq, text)
        since = recs[-1][0] + 1


def records(since=0, n=64):
    return _L.read(since, n) if _L is not None else []


def next_seq():
    return _L.next() if _L is not None else 0


def name(pid):
    return _L.name(pid) if _L is not None else ""


def level_name(level):
    return _L.level_name(level) if _L is not None else ""


def install_logging():
    """micropython-lib's `logging` for an app, as moy_logging: on a VM that has
    none of its own, `import logging` finds it. CPython's standard library
    keeps the name."""
    try:
        import logging  # noqa: F401
        return
    except ImportError:
        pass
    import moy_logging
    _sys.modules["logging"] = moy_logging


install_logging()
