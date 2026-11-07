"""The `mic` device API (runtime/system/dev_mic.py over
native/moy_audio/moy_mic.h; docs/os_2026-11/hardware.md section 1): samples
the stand-in microphone pushes read back by the process holding it, one holder
at a time, drops counted, the permission, the sweep on a process's end, and the
row's absence on a console with no microphone."""

import errno
from array import array

import pytest

from runtime import app_perms as P
from runtime import devices, moybyte
from runtime import moy_log as L
from runtime import moy_mic as N
from runtime.host import mic_sim


@pytest.fixture(autouse=True)
def mic():
    devices.grants = devices.console = None     # a console an earlier test built
    mic_sim.install(22050)
    yield
    for pid in range(1, 400):
        if N.owner() == 0:
            break
        moybyte.end(N.owner())
    devices.grants = None
    mic_sim.remove()


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


def test_the_holder_reads_what_the_microphone_heard():
    pid = L.spawn("t.mic")
    buf = array("h", [0] * 64)
    assert _as(pid, lambda: moybyte.mic.read(buf)) == 0       # the first read takes it
    assert N.owner() == pid and moybyte.mic.holder() == pid
    sent = mic_sim.tone(440, 100)
    assert _as(pid, lambda: moybyte.mic.read(buf)) == 64
    assert list(buf) == sent[:64]
    assert _as(pid, lambda: moybyte.mic.read(buf)) == 36
    assert list(buf[:36]) == sent[64:]
    assert moybyte.mic.rate() == 22050


def test_nothing_is_kept_while_no_one_holds_it():
    mic_sim.tone(440, 50)
    pid = L.spawn("t.mic")
    assert _as(pid, moybyte.mic.open) == 22050
    assert N.pending() == 0


def test_one_process_holds_it_and_its_end_lets_it_go():
    a, b = L.spawn("t.a"), L.spawn("t.b")
    _as(a, moybyte.mic.open)
    _as(a, moybyte.mic.open)                                   # its holder again: nothing
    assert _errno(lambda: _as(b, moybyte.mic.open)) == errno.EBUSY
    assert _errno(lambda: _as(b, lambda: moybyte.mic.read(array("h", [0] * 4)))) == errno.EBUSY
    moybyte.end(a)
    assert N.owner() == 0
    _as(b, moybyte.mic.open)
    assert N.owner() == b


def test_a_slow_reader_loses_the_oldest_samples_and_they_are_counted():
    pid = L.spawn("t.mic")
    _as(pid, moybyte.mic.open)
    mic_sim.push(range(N.RING + 10))
    buf = array("h", [0] * 4)
    assert _as(pid, lambda: moybyte.mic.read(buf)) == 4 and list(buf) == [10, 11, 12, 13]
    assert moybyte.mic.drops() == 10


def test_close_lets_it_go_and_a_kept_reader_reads_nothing():
    pid = L.spawn("t.mic")
    _as(pid, moybyte.mic.open)
    _as(pid, moybyte.mic.close)
    assert N.owner() == 0
    assert N.read(pid, array("h", [0] * 4)) == -errno.EBADF


def test_the_microphone_needs_the_permission():
    pid = L.spawn("t.mic")
    devices.grants = lambda p: ("audio",) if p == pid else None
    assert _errno(lambda: _as(pid, moybyte.mic.open)) == errno.EPERM
    devices.grants = lambda p: ("mic",) if p == pid else None
    _as(pid, moybyte.mic.open)


def test_the_row_and_its_absence_without_a_microphone():
    assert P.line("mic") == "Record with the microphone"
    cart = {"id": "t.m", "runtime": "python", "permissions": ["mic"], "required": ["mic"]}

    class Ws:
        wifi = net = gpio = None
    assert "mic" in P.services(Ws())
    assert P.check(cart, None, P.services(Ws()))[0] == ["mic"]
    mic_sim.remove()
    assert moybyte.mic is None
    assert "mic" not in P.services(Ws())
    assert P.check(cart, None, P.services(Ws()))[2] is not None


def test_an_image_without_the_driver_has_no_microphone_and_ends_clean(monkeypatch):
    """A console whose image has no audio (the Guition S3) stages the face but
    not moy_mic: the face is absent, and every process's end still runs its
    sweep through moybyte.ENDS."""
    from runtime import dev_mic
    monkeypatch.setattr(dev_mic, "_N", None)
    assert not dev_mic.present() and moybyte.mic is None
    pid = L.spawn("t.m")
    assert dev_mic.sweep(pid) == 0
    moybyte.end(pid)


def test_the_recording_indicator_stands_while_a_process_holds_the_microphone():
    """The shell's flag follows the holder through open, close and the end
    (the `rec` overlay reads it, its pixels the shell goldens' rec_indicator),
    and the kernel's copy draws over a run it drives, at the frame's top
    right in the theme's `danger`, while the microphone is held."""
    import ctypes
    from runtime import moy_play
    from runtime.chrome import NAMES, theme_colors
    from runtime import chrome_ops
    pid = L.spawn("t.rec")
    w, h = 64, 48
    pal = [0x1000 + i for i in range(64)]     # every index a colour of its own
    th = theme_colors("moy", "dark")
    chrome_ops.chrome_inks(th, NAMES)
    now = 5000000
    moy_play.chrome_notice("", "", True, now)
    moy_play.chrome_note("", "", "", 0)
    raw = (ctypes.c_uint16 * (w * h))()
    assert devices.MIC[0] == 0
    assert not moy_play.chrome_overlay_draw(raw, w, h, pal, now)
    _as(pid, moybyte.mic.open)
    assert devices.MIC[0] == pid
    assert moy_play.chrome_overlay_draw(raw, w, h, pal, now)
    lit = [i for i in range(w * h) if raw[i]]
    assert lit and all(i % w >= w - 12 and i // w < 12 for i in lit)
    _as(pid, moybyte.mic.close)
    assert devices.MIC[0] == 0
    _as(pid, moybyte.mic.open)
    moybyte.end(pid)
    assert devices.MIC[0] == 0 and N.owner() == 0
    raw = (ctypes.c_uint16 * (w * h))()
    assert not moy_play.chrome_overlay_draw(raw, w, h, pal, now)


def _mic_lib():
    import ctypes
    from runtime.host import host_native
    lib = ctypes.CDLL(host_native.build())
    lib.moy_mic_open.argtypes = [ctypes.c_uint32]
    lib.moy_mic_close.argtypes = [ctypes.c_uint32]
    return lib


def _shell(tmp_path):
    from runtime.host_app import build_workstation
    from test_terminal import plain, run
    ws = build_workstation(str(tmp_path / "carts"))
    app = ws.terminal_app
    app.open()
    return app, lambda line: (run(app, line), plain(app))[1]


def test_the_shell_and_a_compiled_process_are_refused_while_a_python_app_records(tmp_path):
    """The microphone's one holder is its lease (native/moy_kernel/moy_lease.h,
    MOY_LEASE_MIC), whoever asks: while a Python app holds it, the shell's
    `mic read` (served in C, pid 1) and a process of another tier (the
    driver's open under its pid, as a wasm or ELF process's import is) are
    refused busy; its end lets the lease go, and the shell reads."""
    lib = _mic_lib()
    app_pid, wasm_pid = L.spawn("t.mic"), L.spawn("t.wasm")
    _as(app_pid, moybyte.mic.open)
    assert N.owner() == app_pid
    assert lib.moy_mic_open(wasm_pid) == -errno.EBUSY
    term, line = _shell(tmp_path)
    try:
        out = line("mic read 50")
        assert out[-1] == "mic: busy" and term.face.status() == 1, out
        assert line("mic")[-1] == "rate 22050 holder %d drops 0" % app_pid
        moybyte.end(app_pid)
        assert N.owner() == 0
        mic_sim.tone(440, 22050 // 10)
        line("mic read 50")
        assert term.face.status() == 0 and N.owner() == 0
    finally:
        term.close_session()


def test_a_python_app_and_a_compiled_process_are_refused_while_the_shell_records():
    """The reverse: the shell holding the microphone (pid 1, as its `mic
    read` does while it reads) refuses a Python app's face and another
    tier's process, and lets them in once it lets go."""
    lib = _mic_lib()
    app_pid, wasm_pid = L.spawn("t.mic"), L.spawn("t.wasm")
    assert lib.moy_mic_open(1) == 0 and N.owner() == 1
    try:
        assert _errno(lambda: _as(app_pid, moybyte.mic.open)) == errno.EBUSY
        assert lib.moy_mic_open(wasm_pid) == -errno.EBUSY
    finally:
        assert lib.moy_mic_close(1) == 0
    assert lib.moy_mic_open(wasm_pid) == 0 and N.owner() == wasm_pid
    from runtime import leases
    assert [r[:3] for r in leases.LEASES.rows() if r[4] == "device"] == [("mic", (), wasm_pid)]
    moybyte.end(wasm_pid)                       # its end sweeps the lease, in C
    assert N.owner() == 0
    _as(app_pid, moybyte.mic.open)
    assert N.owner() == app_pid
