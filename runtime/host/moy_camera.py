"""The `camera` device API's driver on the host: the stand-in camera
(runtime/host/camera_sim.py installs it), the module `moy_camera` as a board
with a camera would carry it. No console carries one: the P4s' MIPI-CSI
sockets are empty and no other board has a camera interface, so the module is
the host's alone and `moybyte.camera` is None on every board.

  present() open(owner) close(owner) sweep(owner) owner()   -> 0, or -errno
  size() -> (w, h)
  capture(owner, into) -> n     the latest frame, RGB565 big-endian (the
                                glass's byte order), into `into`"""

EBUSY, ENODEV, EINVAL, EBADF = 16, 19, 22, 9

_S = {"on": False, "owner": 0, "w": 0, "h": 0, "frame": b""}


def present():
    return _S["on"]


def open(owner):  # noqa: A001 -- the board module's name
    if not _S["on"]:
        return -ENODEV
    if _S["owner"] not in (0, owner):
        return -EBUSY
    _S["owner"] = owner
    return 0


def close(owner):
    if _S["owner"] != owner:
        return -EBADF
    _S["owner"] = 0
    return 0


def sweep(owner):
    if _S["owner"] == owner and owner:
        _S["owner"] = 0
        return 1
    return 0


def owner():
    return _S["owner"]


def size():
    return (_S["w"], _S["h"])


def capture(who, into):
    if _S["owner"] != who or not who:
        return -EBADF
    f = _S["frame"]
    if not f:
        return 0
    if len(into) < len(f):
        return -EINVAL
    into[:len(f)] = f
    return len(f)


def sim(on, w=0, h=0):
    _S.update(on=bool(on), owner=0, w=w, h=h, frame=b"")


def sim_frame(data):
    _S["frame"] = bytes(data)
