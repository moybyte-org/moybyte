"""The unified serial dev channel (runtime/dev_channel.py).

Until 2026-08-17 the channel existed three times: this module (extracted for
the fork, which was then deleted -- zero importers), a verbatim copy inside the
T-Deck's moy_runtime, and an older inline loop in the P4's. Both boards
construct `DevChannel` now, so this file is the host half of the regression
net; the on-glass halves are tests/test_p4_on_glass.py and
tests/test_tdeck_on_glass.py, which drive the same commands over real serial.

Everything here runs against stub objects on CPython -- the channel is
deliberately importable with no board and no console (its device_util import
falls back to a self-contained shim), which is what makes this testable at all.
"""

import hashlib
import json

from runtime.dev_channel import DevChannel, _remote_state


class FakePointer:
    def __init__(self):
        self.down = False
        self.fresh = False
        self.click = False
        self.placed = []

    def place(self, x, y):
        self.placed.append((x, y))


class FakeIdle:
    """The IdleBlank surface `power` drives (device_boot.IdleBlank's shape)."""

    def __init__(self, timeout_ms=300000):
        self.timeout_ms = timeout_ms
        self.asleep = False
        self.blanked = False
        self.woken = 0

    def blank(self):
        self.blanked = True

    def wake(self, now):
        self.woken += 1
        self.asleep = False


class FullscreenWM:
    _stack = ["home"]


class Win:
    def __init__(self, x, y, w, h):
        self.x, self.y, self.w, self.h = x, y, w, h
        self.title_h = 18
        self.kind = "settings"
        self.minimized = False
        self.buf = None
        self.ctx = None


class WindowedWM:
    def __init__(self):
        self._order = ["settings"]
        self._focus = "settings"
        self._wins = {"settings": Win(100, 80, 640, 400)}

    def desk_open(self):
        return True


class _FakeCarts:
    """`ws.carts` narrowed to the roster the `state` snapshot walks (#209
    landing C)."""

    def __init__(self):
        self.all = []


class FakeWS:
    def __init__(self, wm=None):
        self.wm = wm or FullscreenWM()
        self.screen = "home"
        self.wifi = None
        self.carts = _FakeCarts()
        self._apps = ()
        self._dirty = False
        self._psave_ms = 300000
        self._psave_asleep = False


def make(ws=None, **kw):
    ws = ws or FakeWS()
    return ws, DevChannel(ws, FakePointer(), **kw)


# -- the state snapshot --------------------------------------------------------


def test_state_carries_both_tiers_shapes(capsys):
    """ONE snapshot for every tier: the fullscreen back-stack when the WM has
    one, the windowed fields when it has windows -- the P4 suite's keys
    (psave/desk/order/wins) and the T-Deck's (stack) from the same function."""
    full = _remote_state(FakeWS())
    assert full["stack"] == ["home"]
    assert full["psave"] == [False, 300]
    assert "wins" not in full

    windowed = _remote_state(FakeWS(wm=WindowedWM()))
    assert windowed["desk"] is True
    assert windowed["order"] == ["settings"]
    assert windowed["wins"]["settings"][:4] == [100, 80, 640, 400]
    assert "stack" not in windowed


def test_state_is_one_line_json(capsys):
    ws, ch = make()
    ch.run(ws, "state")
    out = capsys.readouterr().out
    line = [l for l in out.splitlines() if l.startswith("STATE ")][0]
    st = json.loads(line.split("STATE ", 1)[1])
    assert st["screen"] == "home"


# -- gesture scripts -----------------------------------------------------------


def test_swipe_is_press_hold_release(capsys):
    """i==0 press edge, held interpolation, i==n a real RELEASE sample at the
    end point (down=False) -- the shape the fling estimators need."""
    ws, ch = make()
    ch.run(ws, "swipe 0 0 100 0 5")
    samples = []
    while ch._swipe is not None:
        ch.click = False
        ch._scripts()
        if ch._swipe is not None or samples[-1:] != []:
            pass
        samples.append((ch.pointer.placed[-1] if ch.pointer.placed else None,
                        ch.pointer.down, ch.click))
    out = capsys.readouterr().out
    assert "REMOTE swipe 0,0 -> 100,0 frames=5" in out
    assert "REMOTE swipe done" in out
    # 6 pointer frames for n=5 (0..5), then the done frame cleared the script.
    xs = [p[0][0] for p in samples if p[0] is not None]
    assert xs[0] == 0 and xs[-1] == 100
    press = samples[0]
    assert press[1] is True and press[2] is True          # press edge clicks
    release = samples[5]
    assert release[1] is False                            # real release sample
    assert all(s[1] is True for s in samples[1:5])        # held in between
    assert all(s[2] is False for s in samples[1:])        # click frame 0 only
    assert all(s[0] is not None for s in samples[:6])
    assert ch.pointer.fresh is True                       # scripted = fresh


def test_drag_declines_without_windows_and_runs_with(capsys):
    ws, ch = make()                                        # fullscreen tier
    ch.run(ws, "drag")
    assert "REMOTE drag: no window open" in capsys.readouterr().out
    assert ch._drag is None

    ws2, ch2 = make(ws=FakeWS(wm=WindowedWM()))
    ch2.run(ws2, "drag 12 3")
    out = capsys.readouterr().out
    assert "REMOTE drag win=settings" in out and "frames=12 step=3" in out
    n = 0
    while ch2._drag is not None:
        ch2._scripts()
        n += 1
    assert "REMOTE drag done" in capsys.readouterr().out
    assert n == 13                                        # 12 frames + done
    assert ch2.pointer.down is False                      # released at the end


# -- board extras and the py env -----------------------------------------------


def test_extras_dispatch_after_builtins_and_cannot_shadow(capsys):
    calls = []
    ws, ch = make(extra={"bt": lambda ws, p, l: calls.append(l),
                         "state": lambda ws, p, l: calls.append("SHADOW")})
    ch.run(ws, "bt status")
    assert calls == ["bt status"]
    ch.run(ws, "state")                                   # built-in wins
    assert calls == ["bt status"]
    assert "STATE " in capsys.readouterr().out


def test_unknown_command_echoes(capsys):
    ws, ch = make()
    ch.run(ws, "frobnicate 1")
    assert "REMOTE ? frobnicate 1" in capsys.readouterr().out


def test_py_env_reaches_injected_names(capsys):
    ws, ch = make(env={"marker": 41})
    ch.run(ws, "py marker + 1")
    assert "PY 42" in capsys.readouterr().out


# -- power over the injected IdleBlank ------------------------------------------


def test_power_retune_off_and_disable(capsys):
    idle = FakeIdle()
    ws, ch = make(idle=idle)
    ch.run(ws, "power 3")
    assert idle.timeout_ms == 3000
    assert ws._psave_ms == 3000                # `state`'s psave stays live
    ch.run(ws, "power off")
    assert idle.blanked is True                # explicit blank is a REQUEST...
    ch.run(ws, "power 0")
    assert idle.timeout_ms == 0
    out = capsys.readouterr().out
    assert "REMOTE power timeout=3s asleep=False" in out
    assert "REMOTE power off" in out
    assert "REMOTE power timeout=0s asleep=False" in out


def test_power_without_idle_declines(capsys):
    ws, ch = make()
    ch.run(ws, "power 3")
    assert "no idle blank" in capsys.readouterr().out


def test_bl_without_backlight_declines_and_with_it_drives(capsys):
    ws, ch = make()
    ch.run(ws, "bl 0")
    assert "no backlight control" in capsys.readouterr().out
    lit = []
    idle = FakeIdle()
    ws2, ch2 = make(set_backlight=lit.append, idle=idle)
    ch2.run(ws2, "bl 0")
    ch2.run(ws2, "bl 1")
    assert lit == [False, True]
    assert idle.asleep is False and idle.woken == 1


# -- `recv`: the raw upload, off the board -------------------------------------
#
# The loop is driven here through the two objects it actually talks to -- the
# 8-bit stdin and the poll -- so what runs is the real body: the windowing, the
# ack ordering, the idle timeout, the interrupt-char switch and the read-back
# hash. What a host CANNOT model is the ISR underneath (a UART ring that drops
# a byte with no error is hardware); those live in tests/test_push_cart.py as
# what the TOOL does about them, and on glass.


class FakeRawIn:
    """`sys.stdin.buffer`: bytes, and only the ones that have ARRIVED.

    `over_read` is the guarantee the idle timeout rests on -- every read is
    preceded by a poll that already promised a byte, because a bulk read on a
    real board blocks inside `mp_hal_stdin_rx_chr` with no timeout at all."""

    def __init__(self, data=b""):
        self.data = bytearray(data)
        self.over_read = False

    def readinto(self, buf):
        if not self.data:
            self.over_read = True
            return 0
        buf[0] = self.data.pop(0)
        return 1


class FakePoll:
    """POLLIN on the stream above. `on_dry` fires the moment the board asks for
    a byte that has not arrived -- which is where the host is waiting for an
    ack, and so where the ordering can be observed."""

    def __init__(self, raw, on_dry=None):
        self.raw = raw
        self.on_dry = on_dry
        self.waits = []

    def ipoll(self, timeout=-1):
        if self.raw.data:
            return ((None, 1),)
        self.waits.append(timeout)
        if self.on_dry is not None:
            self.on_dry()
        return ()


def raw_channel(data=b"", on_dry=None):
    """A channel whose stdin is `data`. Returns (ws, channel, stdin, poll)."""
    ws, ch = make()
    raw = FakeRawIn(data)
    poll = FakePoll(raw, on_dry)
    ch._rawin = raw
    ch._poll = poll
    ch._ipoll = poll.ipoll
    return ws, ch, raw, poll


def _said(capsys, prefix="RECV "):
    return [l for l in capsys.readouterr().out.splitlines()
            if l.startswith(prefix)]


EVERY_BYTE = bytes(range(256)) * 5          # 0x03 and both newlines included


def test_recv_writes_every_byte_value_and_hashes_what_it_wrote(
        tmp_path, capsys):
    """8 BITS, no base64: the interrupt char, CR and LF all ride through. On a
    board the first is swallowed by the RX ISR and CR is rewritten by the TEXT
    stdin -- which is why the loop reads `stdin.buffer` with kbd_intr off."""
    ws, ch, raw, _poll = raw_channel(EVERY_BYTE)
    dst = str(tmp_path / "main.lua")
    ch.run(ws, "recv %d 512 %s" % (len(EVERY_BYTE), dst))
    lines = _said(capsys)
    assert (tmp_path / "main.lua.new").read_bytes() == EVERY_BYTE
    assert lines[0] == "RECV ready %d 512 %s.new" % (len(EVERY_BYTE), dst)
    assert lines[-1] == "RECV done %s %d" % (
        hashlib.sha256(EVERY_BYTE).hexdigest()[:12], len(EVERY_BYTE))
    assert [l for l in lines if l.startswith("RECV ack")] == [
        "RECV ack %d" % min(n, len(EVERY_BYTE))
        for n in range(512, len(EVERY_BYTE) + 512, 512)]
    assert ch.raw == len(EVERY_BYTE)
    assert raw.over_read is False


def test_the_ack_comes_after_the_write_never_before(
        tmp_path, capsys, monkeypatch):
    """The host puts the next window on the wire the moment it reads the ack,
    and on the P4 that window has no flow control behind it -- so the file
    write happens while nothing is in flight, and the ack is what ends that
    quiet. Observed at the one instant it can be: the board asking for the
    first byte of window two, which is where the host is still waiting."""
    import builtins

    real = builtins.open
    wrote = []
    seen = {}

    class Noted:
        def __init__(self, f):
            self.f = f

        def write(self, data):
            wrote.append(len(data))
            return self.f.write(bytes(data))

        def __getattr__(self, name):
            return getattr(self.f, name)

    def on_dry():
        seen["wrote"] = list(wrote)
        seen["out"] = capsys.readouterr().out

    ws, ch, _raw, _poll = raw_channel(EVERY_BYTE[:512], on_dry=on_dry)
    monkeypatch.setattr(builtins, "open",
                        lambda p, m="r", *a, **k: Noted(real(p, m, *a, **k))
                        if "w" in m else real(p, m, *a, **k))
    ch.run(ws, "recv %d 512 %s" % (len(EVERY_BYTE), str(tmp_path / "main.lua")))
    assert seen["wrote"] == [512]                  # written before the wait
    assert "RECV ack 512" in seen["out"]           # and acked before it


def test_a_host_that_goes_quiet_takes_the_tmp_with_it(tmp_path, capsys):
    """A dead host must not park the frame loop, and must not leave a half cart
    behind either. The wait is bounded by RECV_IDLE_MS per byte, refreshed by
    every byte that does arrive, so a slow host is not a dead one."""
    from runtime.dev_channel import RECV_IDLE_MS

    ws, ch, _raw, poll = raw_channel(EVERY_BYTE[:700])
    dst = str(tmp_path / "main.lua")
    ch.run(ws, "recv 5000 512 %s" % dst)
    assert _said(capsys)[-1] == "RECV ERR timeout after 700 of 5000 bytes"
    assert not (tmp_path / "main.lua.new").exists()
    assert poll.waits == [RECV_IDLE_MS]


def test_the_hash_is_of_the_file_not_of_the_bytes_that_went_in(
        tmp_path, capsys, monkeypatch):
    """`open(p,'wb')` has reported a byte count on this console for a file that
    read back EMPTY (push_cart's header, item 2). A hash taken from the buffer
    would agree with the host about a cart that is not on the store, so the
    file is read back through the same window buffer and hashed from there."""
    import builtins

    real = builtins.open

    class Lossy:
        def __init__(self, f):
            self.f = f

        def write(self, data):
            return self.f.write(bytes(data)[:-1])      # the store eats one

        def __getattr__(self, name):
            return getattr(self.f, name)

    def fake_open(path, mode="r", *a, **kw):
        f = real(path, mode, *a, **kw)
        return Lossy(f) if "w" in mode else f

    monkeypatch.setattr(builtins, "open", fake_open)
    ws, ch, _raw, _poll = raw_channel(EVERY_BYTE[:512])
    ch.run(ws, "recv 512 512 %s" % (tmp_path / "main.lua"))
    monkeypatch.undo()
    landed = (tmp_path / "main.lua.new").read_bytes()
    assert len(landed) == 511
    assert _said(capsys)[-1].split()[2] == hashlib.sha256(
        landed).hexdigest()[:12]


def test_the_interrupt_char_goes_off_for_the_transfer_and_comes_back(
        tmp_path, capsys, monkeypatch):
    """A payload byte equal to it never reaches the ring -- both esp32 RX ISRs
    swallow it, and the CDC path empties the ring as well. Restored in a
    `finally`, so the timeout path leaves the board interruptible too."""
    import runtime.dev_channel as dc

    calls = []
    monkeypatch.setattr(dc, "_micropython",
                        type("M", (), {"kbd_intr": staticmethod(calls.append)}))
    ws, ch, _raw, _poll = raw_channel(EVERY_BYTE[:64])
    ch.run(ws, "recv 64 64 %s" % (tmp_path / "a.lua"))
    assert calls == [-1, 3]
    del calls[:]
    ws, ch, _raw, _poll = raw_channel(b"")
    ch.run(ws, "recv 64 64 %s" % (tmp_path / "b.lua"))
    assert "RECV ERR timeout" in _said(capsys)[-1]
    assert calls == [-1, 3]


def test_a_build_that_cannot_go_8_bit_declines_instead(tmp_path, capsys):
    """One fallback, not two: a board with no way to disable the interrupt char
    refuses the raw mode, and the host pushes through base64 as before, rather
    than shipping 255 of 256 byte values and finding out from the hash."""
    import runtime.dev_channel as dc

    ws, ch, _raw, _poll = raw_channel(EVERY_BYTE)
    was = dc.RECV_8BIT
    dc.RECV_8BIT = False
    try:
        ch.run(ws, "recv 64 64 %s" % (tmp_path / "a.lua"))
    finally:
        dc.RECV_8BIT = was
    assert "RECV ERR no 8-bit route" in _said(capsys)[0]
    assert not (tmp_path / "a.lua.new").exists()


def test_bare_recv_is_the_capability_line(capsys):
    """The whole handshake. An image without the command answers `REMOTE ?
    recv` from the same dispatcher, which is a positive no -- see
    tools/push_cart.raw_window."""
    from runtime.dev_channel import RECV_IDLE_MS, RECV_MAX_WINDOW

    ws, ch, _raw, _poll = raw_channel()
    ch.run(ws, "recv")
    assert _said(capsys) == ["RECV caps max=%d idle=%d"
                             % (RECV_MAX_WINDOW, RECV_IDLE_MS)]


def test_a_window_the_board_cannot_hold_is_refused(tmp_path, capsys):
    """The window is also the buffer the board allocates for it, so its own
    ceiling binds -- and it refuses BEFORE arming, while the host is still
    reading lines rather than blasting bytes at a reader that moved on."""
    from runtime.dev_channel import RECV_MAX_WINDOW

    ws, ch, _raw, _poll = raw_channel(EVERY_BYTE)
    ch.run(ws, "recv 64 %d %s" % (RECV_MAX_WINDOW * 2, tmp_path / "a.lua"))
    assert _said(capsys)[0].startswith("RECV ERR")
    assert not (tmp_path / "a.lua.new").exists()


def test_bytes_the_line_reader_already_swallowed_are_not_lost(
        tmp_path, capsys):
    """Empty by construction -- the reader dispatches on the newline, so its
    partial buffer holds nothing when `recv` runs. Taken anyway: a byte it DID
    swallow is one the payload would never see, and a silent one-byte shift is
    the failure this whole path is hashed to catch."""
    ws, ch, _raw, _poll = raw_channel(b"llo")
    ch.buf = "he"
    ch.run(ws, "recv 5 512 %s" % (tmp_path / "a.lua"))
    assert (tmp_path / "a.lua.new").read_bytes() == b"hello"
    assert ch.buf == ""
    assert "RECV done" in _said(capsys)[-1]


def test_a_channel_with_no_8_bit_stdin_declines_the_probe_too(capsys):
    """The decline comes BEFORE the caps line, whatever was asked: a board that
    cannot carry every byte value must not advertise a window. push_cart reads
    `RECV ERR` as a no exactly like `REMOTE ? recv`."""
    ws, ch = make()
    ch._rawin = None
    ch.run(ws, "recv")
    assert "RECV ERR no 8-bit route" in _said(capsys)[0]
