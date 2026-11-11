"""`moycore` on CPython: the module surface the boards' glue drives
(device/moycore_glue.py over native/moycore/modmoycore.c), by ctypes over the
host's Lua run (runtime/host/lua_binding.py), so the host runs a Lua cart through
the same glue, the same C and the kernel's Player as a board. A VM's
`import moycore` is the native module, which always wins over this file.

The Lua half only: a compiled cart's run on the host is runtime/host/wasm_host.py's.
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

_RUNS = {}          # handle -> HostLuaRun, the runs open at once
_CUR = [0]          # the current run's handle: what every verb below acts on


def _prune():
    for h in [h for h, r in _RUNS.items() if r.handle() != h]:
        del _RUNS[h]               # the C took its row back (moyhost_lua.c)
        if _CUR[0] == h:
            _CUR[0] = 0


def _cur():
    return _RUNS.get(_CUR[0])


def _run():
    r = _cur()
    if r is None:
        raise RuntimeError("moycore: no run")
    return r


class _Map:
    __slots__ = ("cells", "w", "h")

    def __init__(self, cells, w, h):
        self.cells, self.w, self.h = cells, w, h


def run_begin(fb, w, h, wire, sheet_pix, map_cells, map_w, map_h, snap, aq,
              pmem_img, cfg, flags, vm, app=None):
    """The console over the caller's buffers and the VM opened on it, as the
    boards' run_begin, in a row of its own beside the runs open: its handle,
    which `use` makes current again. `cfg` is lua_ext.cfg_blob's bytes,
    `app` the app's run (moy_app's `run()`) or None."""
    if not vm:
        raise RuntimeError("moycore: a compiled cart runs on wasm_host here")
    r = _lb.HostLuaRun(fb, w, h, sheet_pix,
                       _Map(map_cells, map_w, map_h) if map_cells is not None else None,
                       wire=wire, flags=flags, snap=snap, aq=aq,
                       cfg=cfg, app=app)
    if pmem_img is not None:
        r.pmem_load(pmem_img)
    _prune()
    h = r.handle()
    _RUNS[h] = r
    _CUR[0] = h
    return h


def use(h):
    """Run `h` made current: True, or False when it has closed."""
    r = _RUNS.get(h)
    if r is None or r.handle() != h:
        _prune()
        return False
    _CUR[0] = h
    return True


def runs():
    return _lb.play_lib().hl_runs()


def heap(h):
    r = _RUNS.get(h)
    return r.heap() if r is not None and r.handle() == h else None


# -- the Lua tier's services (native/moycore/moycore_svc.h) --------------------

_SVC_ERR = (0, 9, 28, 12, 22)          # STALE EBADF, FULL ENOSPC, NOMEM, BAD EINVAL


def _svc_check(rc):
    if rc:
        import errno
        import os
        e = _SVC_ERR[rc] if 0 < rc < len(_SVC_ERR) else errno.EIO
        raise OSError(e, os.strerror(e))


def _bytes(s):
    return s.encode() if isinstance(s, str) else bytes(s)


def svc_start(pid, name, src, env=None, app=None):
    """Service `pid` started (moycore_svc_start): its handle. `src` is its
    one script, or its scripts as (text, chunkname) pairs in load order
    (a None chunkname the service's own);
    `env` what the console hands it, the JSON text [values, calls], or None;
    `app` its app's run, moy_app's `run()` bytes, or None."""
    pairs = [(src, None)] if isinstance(src, (str, bytes)) else list(src)
    chunks = (_lb.SvcChunk * len(pairs))()
    for i, (text, chunk) in enumerate(pairs):
        b = _bytes(text)
        chunks[i].src, chunks[i].n = b, len(b)
        chunks[i].name = _bytes(chunk) if chunk is not None else None
    e = env.encode() if isinstance(env, str) else env
    h = _lb.ctypes.c_uint32()
    _svc_check(_lb.play_lib().moycore_svc_start(pid, name.encode(), chunks, len(pairs), e,
                                                len(e) if e is not None else 0,
                                                bytes(app) if app is not None else None,
                                                _lb.ctypes.byref(h)))
    return h.value


def svc_stop(h):
    _svc_check(_lb.play_lib().moycore_svc_stop(h))


def svc_info(h):
    """(state, exit, heap, cpu_us, strikes, why, wakes, mail), or None for a
    stale handle."""
    i = _lb.SvcInfo()
    if _lb.play_lib().moycore_svc_info(h, _lb.ctypes.byref(i)):
        return None
    return (i.state, i.exit, i.heap, i.cpu_us, i.strikes, i.why.decode("utf-8", "replace"),
            i.wakes, i.mail)


def svc_reap(h):
    _svc_check(_lb.play_lib().moycore_svc_reap(h))


def svc_tasks():
    return bool(_lb.play_lib().moycore_svc_tasks())


def svc_step(now_ms):
    return _lb.play_lib().moycore_svc_step(now_ms & 0xFFFFFFFF)


def svc_reset():
    _lb.play_lib().moycore_svc_reset()


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


def load(chunks, layout=None):
    return _run().load(list(chunks), layout)


def layout(w, h, fs):
    r = _cur()
    return r.layout(w, h, fs) if r is not None else False


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


def flag_xor(n, mask):
    r = _cur()
    if r is not None:
        r.flag_xor(n, mask)


def view():
    r = _cur()
    return r.view() if r is not None else None


def active():
    return _cur() is not None


def get_global(name):
    r = _cur()
    return r.get_global(name) if r is not None else None


def get_global_len(name):
    r = _cur()
    return r.get_global_len(name) if r is not None else None


def pmem_image(out):
    """Fill `out` (256 int32) with the run's pmem; whether it moved."""
    r = _cur()
    if r is None:
        return False
    dirty, img = r.pmem()
    for i in range(min(len(out), 256)):
        out[i] = img[i]
    return dirty


def close():
    """The current run closed."""
    r = _RUNS.pop(_CUR[0], None)
    _CUR[0] = 0
    if r is not None:
        r.close()
