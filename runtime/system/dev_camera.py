"""`moybyte.camera`: the `camera` device API's face (docs/os_2026-11/hardware.md
section 1) over its driver, the module `moy_camera`. No console has a camera
(the P4s' MIPI-CSI sockets are empty), so no board carries the module and
`moybyte.camera` is None on every board; the host has one under its stand-in
(runtime/host/camera_sim.py). Needs the `camera` permission; one process
holds it, taken at its first verb and let go at its end.

  open()            the camera held before its first verb
  size() -> (w, h)
  capture(into=None) -> n | bytes
                    the latest frame, w * h RGB565 pixels big-endian (the
                    glass's byte order): into `into`, its length, or a new
                    bytes; an empty bytes (or 0) before the first frame
  holder() -> pid   the process holding the camera, or 0
  close()"""

try:
    import moy_camera as _N
except ImportError:  # host, or a console with no camera
    _N = None
if _N is None:
    try:
        from runtime import moy_camera as _N
    except ImportError:  # a console with no camera
        _N = None

import devices as _D


def _take():
    p = _D.need("camera")
    if _N.owner() != p:
        _D.check(_N.open(p))
    return p


def open():  # noqa: A001 -- the verb's name
    _take()


def size():
    _take()
    return _N.size()


def capture(into=None):
    p = _take()
    if into is not None:
        return _D.check(_N.capture(p, into))
    w, h = _N.size()
    buf = bytearray(w * h * 2)
    return bytes(buf[:_D.check(_N.capture(p, buf))])


def holder():
    return _N.owner()


def close():
    sweep(_D.pid())


def present():
    return _N is not None and bool(_N.present())


def sweep(pid):
    return _N.sweep(pid) if _N is not None else 0
