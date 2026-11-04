"""The `camera` device API (runtime/system/dev_camera.py;
docs/os_2026-11/hardware.md section 1) on the host's stand-in camera
(runtime/host/camera_sim.py), the only camera there is: no board has one
fitted, so the API is None on every board. Its size, a frame captured into a
buffer and as bytes, one holder and its end, the permission and absence."""

import errno

import pytest

from runtime import app_perms as P
from runtime import devices, moybyte
from runtime import moy_camera as N
from runtime import moy_log as L
from runtime.host import camera_sim
from unix_mp import ROOT


@pytest.fixture(autouse=True)
def camera():
    devices.grants = devices.console = None     # a console an earlier test built
    camera_sim.install(4, 2)
    yield
    while N.owner():
        moybyte.end(N.owner())
    devices.grants = None
    camera_sim.remove()


def _as(pid, fn):
    was = L.current()
    L.current(pid)
    try:
        return fn()
    finally:
        L.current(was)


def _errno(fn):
    with pytest.raises(OSError) as e:
        fn()
    return e.value.errno


def test_a_frame_captured_into_a_buffer_and_as_bytes():
    pid = L.spawn("t.cam")
    cam = moybyte.camera
    assert _as(pid, cam.size) == (4, 2)
    assert _as(pid, cam.capture) == b""                 # before the first frame
    frame = bytes(range(16))
    camera_sim.frame(frame)
    buf = bytearray(16)
    assert _as(pid, lambda: cam.capture(buf)) == 16 and bytes(buf) == frame
    assert _as(pid, cam.capture) == frame
    assert _errno(lambda: _as(pid, lambda: cam.capture(bytearray(8)))) == errno.EINVAL
    assert cam.holder() == pid


def test_one_holder_and_its_end():
    a, b = L.spawn("t.a"), L.spawn("t.b")
    _as(a, moybyte.camera.size)
    assert _errno(lambda: _as(b, moybyte.camera.size)) == errno.EBUSY
    moybyte.end(a)
    assert moybyte.camera.holder() == 0
    _as(b, moybyte.camera.size)


def test_the_camera_needs_its_permission_and_is_absent_without_one():
    pid = L.spawn("t.cam")
    devices.grants = lambda p: ("mic",) if p == pid else None
    assert _errno(lambda: _as(pid, moybyte.camera.size)) == errno.EPERM
    assert P.line("camera") == "Take pictures with the camera"

    class Ws:
        wifi = net = gpio = None
    assert "camera" in P.services(Ws())
    camera_sim.remove()
    assert moybyte.camera is None and "camera" not in P.services(Ws())


def test_no_board_carries_a_camera_driver():
    """The module is the host's stand-in alone: no native tree registers it."""
    for c in (ROOT / "native").rglob("*.c"):
        assert "MP_QSTR_moy_camera" not in c.read_text(encoding="utf-8", errors="replace"), c
