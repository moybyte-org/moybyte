"""`moycore` on CPython: the module surface the boards' glue drives
(device/moycore_glue.py over native/moycore/modmoycore.c), by ctypes over the
host's Lua run (runtime/lua_binding.py), so the host runs a Lua cart through
the same glue, the same C and the kernel's Player as a board. A VM's
`import moycore` is the native module, which always wins over this file.

The Lua half only: a compiled cart's run on the host is runtime/wasm_host.py's.
Raises ImportError where the host cannot build the binding, as a build without
the native module has none.
"""

from __future__ import annotations

try:
    from runtime import lua_binding as _lb
except ImportError:                      # loaded flat, runtime/ on sys.path
    import lua_binding as _lb            # type: ignore[no-redef]

if not _lb.HostLuaRun.available():
    raise ImportError("moycore: no host Lua binding (a C compiler builds it)")

SNAP_LEN = _lb.SNAP_LEN
for _n in dir(_lb):
    if _n.startswith(("SNAP_", "AQ_")):
        globals()[_n] = getattr(_lb, _n)
TICK_DRAW = 1

_RUN = [None]


def _run():
    r = _RUN[0]
    if r is None:
        raise RuntimeError("moycore: no run")
    return r


class _Map:
    __slots__ = ("cells", "w", "h")

    def __init__(self, cells, w, h):
        self.cells, self.w, self.h = cells, w, h


def run_begin(fb, w, h, wire, sheet_pix, map_cells, map_w, map_h, snap, aq,
              pmem_img, cfg, flags, vm):
    """The console over the caller's buffers and the VM opened on it, as the
    boards' run_begin; `cfg` is lua_ext.cfg_blob's bytes."""
    if not vm:
        raise RuntimeError("moycore: a compiled cart runs on wasm_host here")
    # One run at a time, as on a board; a host harness that dropped its
    # console without closing the run leaves it here, and the next one ends it.
    close()
    r = _lb.HostLuaRun(fb, w, h, sheet_pix,
                       _Map(map_cells, map_w, map_h) if map_cells is not None else None,
                       wire=wire, flags=flags, snap=snap, aq=aq,
                       cfg=cfg)
    if pmem_img is not None:
        r.pmem_load(pmem_img)
    _RUN[0] = r


def register(name, fn):
    _run().register(name, fn)


def image_put(name, text):
    _run().image_put(name, text)


def scene_put(name, text):
    _run().scene_put(name, text)


def layer_bind(buf, w, h):
    _run().layer_bind(buf, w, h)


def layer_restore(state):
    _run().layer_restore(state)


def exec(src, name):  # noqa: A001 -- the module's verb is named exec on a board
    return _run().exec(src, name)


def load(chunks):
    return _run().load(list(chunks))


def tick(dt, draw=True):
    return _run().tick(dt, draw)


def tick_split(out=None):
    u, d = _lb.ctypes.c_uint32(0), _lb.ctypes.c_uint32(0)
    _lb.play_lib().hl_split(_lb.ctypes.byref(u), _lb.ctypes.byref(d))
    if out is None:
        return (u.value, d.value)
    out[0], out[1] = u.value, d.value
    return out


def retarget(buf):
    _run().retarget(buf)


def view():
    r = _RUN[0]
    return r.view() if r is not None else None


def active():
    return _RUN[0] is not None


def get_global(name):
    r = _RUN[0]
    return r.get_global(name) if r is not None else None


def get_global_len(name):
    r = _RUN[0]
    return r.get_global_len(name) if r is not None else None


def pmem_image(out):
    """Fill `out` (256 int32) with the run's pmem; whether it moved."""
    r = _RUN[0]
    if r is None:
        return False
    dirty, img = r.pmem()
    for i in range(min(len(out), 256)):
        out[i] = img[i]
    return dirty


def close():
    r = _RUN[0]
    _RUN[0] = None
    if r is not None:
        r.close()
