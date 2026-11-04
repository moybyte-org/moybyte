"""The device APIs (docs/os_2026-11/hardware.md sections 1 and 4): what a
board's `class` rows name, each a C driver with a Python face, reached as
`moybyte.http`, `moybyte.mic`, `moybyte.lora`, `moybyte.ble`,
`moybyte.usb`, `moybyte.midi` and `moybyte.camera`.

  api(name)       the face of device API `name` ("http", "mic", "lora",
                  "ble", "usb", "midi", "camera"), or None where this console has not the class
  present(name)   whether it has
  apis()          the device APIs this console has, as the permission
                  table's `needs` names them (runtime/shell/app_perms.py)

Each face checks its permission against the calling process's grants (the
`grants` hook: the console's Procs.granted; the shell, pid 1, is not gated),
refusing with OSError(EPERM) naming it, and keeps what a process opened as
that process's: `moybyte.ENDS` sweeps it when the process ends (exit, crash,
kill, a run's end), in C first, then its Python state.

A face is imported on first ask, so a console pays nothing for an API no
process uses. The host has `http` over the real client and the others where a
stand-in is installed (runtime/host/*_sim.py, a face's `stand_in`)."""

try:
    import moy_log as _L
except ImportError:  # pragma: no cover - an image without the kernel
    _L = None

try:
    import moybyte as _moybyte
except ImportError:  # host: the runtime package
    from runtime import moybyte as _moybyte

EPERM, EBADF, ENODEV, EBUSY, EINVAL = 1, 9, 19, 16, 22
SHELL = 1
NAMES = ("http", "mic", "lora", "ble", "usb", "midi", "camera")
# The permission each face checks.
PERM = {"http": "http", "mic": "mic", "lora": "lora", "ble": "ble", "usb": "usb",
        "midi": "midi", "camera": "camera"}

# fn(pid) -> the granted permissions, None for an ungated process: the
# console's Procs.granted. None: nothing is gated (a bare host).
grants = None
# The console (its wifi_hold/wifi_release and its wifi and cart_net), which
# `http` takes the radio lease through; None: no radio to hold.
console = None

_FACES = {}

# The pid holding the microphone, 0 for none: dev_mic keeps it, and the
# shell's recording indicator (runtime/wm/wm.py's overlay signature) reads it
# every frame without a call.
MIC = [0]


def pid():
    return _L.current() if _L is not None else SHELL


def may(p, perm):
    g = grants
    if g is None or p == SHELL:
        return True
    have = g(p)
    return have is None or perm in have


def need(name):
    """The calling process's pid, or OSError(EPERM) without `name`'s
    permission."""
    p = pid()
    perm = PERM[name]
    if not may(p, perm):
        raise OSError(EPERM, 'needs "%s"' % perm)
    return p


def _face(name):
    f = _FACES.get(name)
    if f is None:
        if name not in NAMES:
            return None
        try:
            f = __import__("dev_" + name)
        except ImportError:  # host: the runtime package
            try:
                f = getattr(__import__("runtime.dev_" + name), "dev_" + name)
            except ImportError:  # an image without the face
                return None
        _FACES[name] = f
        if getattr(f, "SWEEP_FIRST", False):
            _moybyte.ENDS.insert(0, f.sweep)     # ahead of the leases it ends with
        else:
            _moybyte.ENDS.append(f.sweep)
    return f


def present(name):
    f = _face(name)
    return f is not None and f.present()


def api(name):
    f = _face(name)
    return f if f is not None and f.present() else None


def apis():
    return tuple(n for n in NAMES if n != "http" and present(n))


def error(e):
    """OSError with errno `e`, as both Pythons read it."""
    return OSError(e, "errno %d" % e)


def check(rc):
    """`rc` from a driver: itself when >= 0, else OSError(-rc)."""
    if rc < 0:
        raise error(-rc)
    return rc
