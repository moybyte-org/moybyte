"""The host's stand-in camera (docs/os_2026-11/hardware.md section 1): a
camera of a given size whose frames a test hands it, so a test sees what an
app on a console with a camera would.

  install(w=320, h=240)   the console has a camera from now on
  frame(data)             the camera's latest frame: w * h RGB565 pixels,
                          big-endian
  remove()                no camera again"""

try:                                    # the instance the face imports, its state
    from runtime import moy_camera as _N    # being Python's, not a C library's
except ImportError:  # loaded flat
    import moy_camera as _N


def install(w=320, h=240):
    _N.sim(True, w, h)


def frame(data):
    w, h = _N.size()
    if len(data) != w * h * 2:
        raise ValueError("a frame is %d bytes" % (w * h * 2))
    _N.sim_frame(data)


def remove():
    _N.sim(False)
