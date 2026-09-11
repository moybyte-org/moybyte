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

from runtime.dev_channel import (DevChannel, _remote_state, luaprof_line,
                                 shim_line_range, verbs_line)


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


def test_state_reports_every_frame_stage_with_its_budget_and_misses():
    """#210's route. `state` is the one every board serves -- the Guition
    stages no device_diag and has no PUMP line -- so the per-stage deadline
    meters ride it, in the loop's invariant order and with its field shape."""
    from runtime import device_boot

    class CapWS(FakeWS):
        def __init__(self):
            FakeWS.__init__(self)
            self.perf_capture = True
            self.stage_meters = device_boot.StageMeters(self)

    ws = CapWS()
    m = ws.stage_meters
    m.start(m.slot_ms)
    m.mark(device_boot._S_FRAME)

    st = _remote_state(ws)
    assert list(st["stages"]) == list(device_boot.STAGE_ORDER)
    for row in st["stages"].values():
        assert sorted(row) == ["avg_us", "budget_us", "last_us", "max_us",
                               "misses", "n"]
    frame = st["stages"]["frame"]
    assert frame["n"] == 1 and frame["budget_us"] == 16 * 780
    # A stage no hook filled, and one with no deadline to miss: None either
    # way, never the 0 that reads identically to a broken meter.
    assert st["stages"]["inputs"]["last_us"] is None
    assert st["stages"]["tail"]["budget_us"] is None


def test_state_reports_no_stages_at_all_where_no_shared_loop_runs():
    """The host simulator and the wasm head run their own loops, so there are
    no stage meters to dump -- and that is None, not eleven zeroed rows."""
    assert _remote_state(FakeWS())["stages"] is None


def test_state_reports_the_sram_headroom_the_run_had_or_none():
    """#211's route, and it is `state` for the same reason #210's is: every
    board serves it. A run that tipped into PSRAM reports the regime change as
    a boolean beside the low-water mark it tipped at; a Python cart and a tier
    whose allocator has one region report None -- never zeros, which is also
    what a meter that stopped working looks like."""

    class PlayerWS(FakeWS):
        def __init__(self, report):
            FakeWS.__init__(self)
            self.player = type("P", (), {"sram_report": lambda _s: report})()

    tipped = {"sram_free_min": 21504, "psram_fallback": True, "floor": 24576}
    assert _remote_state(PlayerWS(tipped))["sram"] == tipped
    assert _remote_state(PlayerWS(None))["sram"] is None


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
        # The board asks again after each re-send offer, so this fires more
        # than once now; the instant being observed is the FIRST one -- the
        # board waiting on window two, with window one already on disk.
        if "wrote" in seen:
            return
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
    every byte that does arrive, so a slow host is not a dead one.

    It now OFFERS the window back first -- a short window is a dropped byte far
    more often than a dead host -- and only gives up once RECV_DEAD_WINDOWS of
    them arrive completely empty. The count it reports is what LANDED IN THE
    FILE (512 here, one whole window) rather than what had been buffered when
    the stream stopped: 188 bytes of a window that was thrown away were never
    part of the cart, and naming them sent a reader looking for a file that
    was 700 bytes long."""
    from runtime.dev_channel import RECV_DEAD_WINDOWS, RECV_IDLE_MS

    ws, ch, _raw, poll = raw_channel(EVERY_BYTE[:700])
    dst = str(tmp_path / "main.lua")
    ch.run(ws, "recv 5000 512 %s" % dst)
    said = _said(capsys)
    assert said[-1] == "RECV ERR timeout after 512 of 5000 bytes"
    assert [l for l in said if l.startswith("RECV retry")] == [
        "RECV retry 512"] * RECV_DEAD_WINDOWS
    assert not (tmp_path / "main.lua.new").exists()
    assert poll.waits == [RECV_IDLE_MS] * (RECV_DEAD_WINDOWS + 1)


class FeedingPoll(FakePoll):
    """FakePoll, but a host that WROTE during `on_dry` is answered.

    The base class fires the hook and then reports not-ready regardless, which
    is right when the hook only observes. Here the hook IS the host: it puts a
    window on the wire, and a poll that ignored it would make every window look
    dropped."""

    def ipoll(self, timeout=-1):
        ready = super().ipoll(timeout)
        if not ready and self.raw.data:
            return ((None, 1),)
        return ready


class ReSendingHost:
    """The push loop in miniature, over the same FakeRawIn the board reads.

    It writes one window, waits, and does what the board's last line asks:
    `retry <n>` re-sends the window at n, `ack` moves on. `drop` names the
    windows the WIRE eats a byte from -- which is the whole failure this
    exists to model, because the host cannot see it happen and neither can the
    board until the stream stops."""

    def __init__(self, payload, window, drop=()):
        self.payload, self.window = payload, window
        self.drop = set(drop)
        self.raw = FakeRawIn()
        self.sent = 0
        self.nth = 0
        self.resends = 0
        self.said = []

    def _feed(self):
        blk = self.payload[self.sent:self.sent + self.window]
        # The host always believes it wrote the whole window; the ring is what
        # loses the byte, silently, which is why `sent` advances by the FULL
        # window even on a dropped one.
        self.sent += len(blk)
        if self.nth in self.drop:
            self.drop.discard(self.nth)
            blk = blk[:-1]
        self.nth += 1
        self.raw.data.extend(blk)

    def on_dry(self, capsys):
        new = [l for l in capsys.readouterr().out.splitlines()
               if l.startswith("RECV ")]
        self.said += new
        if not new:
            # The board is still waiting inside a window this host already
            # finished writing -- which is exactly the shape of a dropped
            # byte, and a real host would be blocked on the reply. Writing
            # here would hand it the NEXT window as the tail of this one.
            return
        last = new[-1]
        if last.startswith("RECV retry "):
            self.sent = int(last.split()[2])
            self.resends += 1
        elif last.startswith("RECV ERR") or last.startswith("RECV done"):
            return
        if self.sent < len(self.payload):
            self._feed()


def test_a_dropped_byte_costs_its_window_and_not_the_cart(tmp_path, capsys):
    """The failure this whole retry exists for, end to end.

    A UART ring with no flow control drops a byte with no error when the board
    falls behind for ~25ms. Measured on the P4: a handful of bytes lost about
    once every 300 windows, which killed a 120KB push one time in five. The
    file only ever advances by WHOLE windows, so the board can throw the short
    one away and name the boundary it is still standing on -- and the cart
    lands byte-exact, hash and all, having paid one window."""
    payload = EVERY_BYTE                      # 1280 B = 5 windows of 256
    host = ReSendingHost(payload, 256, drop=(1, 3))
    ws, ch = make()
    poll = FeedingPoll(host.raw, lambda: host.on_dry(capsys))
    ch._rawin, ch._poll, ch._ipoll = host.raw, poll, poll.ipoll
    dst = str(tmp_path / "main.lua")

    ch.run(ws, "recv %d 256 %s" % (len(payload), dst))
    host.said += [l for l in capsys.readouterr().out.splitlines()
                  if l.startswith("RECV ")]

    assert (tmp_path / "main.lua.new").read_bytes() == payload
    assert host.said[-1] == "RECV done %s %d" % (
        hashlib.sha256(payload).hexdigest()[:12], len(payload))
    # one re-send per dropped window, each at the boundary the file was on
    assert [l for l in host.said if l.startswith("RECV retry")] == [
        "RECV retry 256", "RECV retry 512"]
    assert host.resends == 2
    assert ch.raw == len(payload)


def test_a_wire_that_drops_every_window_gives_up_rather_than_crawling(
        tmp_path, capsys):
    """The budget. A cable that eats a byte from EVERY window is broken, and
    saying so beats re-sending forever -- so the retries are counted, and the
    count is what ends it rather than the dead-host rule (every window here
    arrives nearly full, so none of them is empty)."""
    from runtime.dev_channel import RECV_RETRIES

    payload = EVERY_BYTE
    host = ReSendingHost(payload, 256, drop=range(200))    # every window
    ws, ch = make()
    poll = FeedingPoll(host.raw, lambda: host.on_dry(capsys))
    ch._rawin, ch._poll, ch._ipoll = host.raw, poll, poll.ipoll

    ch.run(ws, "recv %d 256 %s" % (len(payload), str(tmp_path / "main.lua")))
    host.said += [l for l in capsys.readouterr().out.splitlines()
                  if l.startswith("RECV ")]

    assert len([l for l in host.said if l.startswith("RECV retry")]) \
        == RECV_RETRIES
    assert host.said[-1] == "RECV ERR timeout after 0 of %d bytes" % len(payload)
    assert not (tmp_path / "main.lua.new").exists()


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


# -- the VERBS line (the Lua/p8 per-verb profiler's report) -------------------


def test_the_verbs_line_reports_per_frame_not_per_window():
    """The shape the line has to make legible: 180 cheap calls and one
    expensive one are different problems, and per-window totals hide which is
    which behind whatever sample length the host happened to choose."""
    line = verbs_line(1000000, 100, [
        ("spr", 18000, 1400000),        # 180/frame, 14ms/frame
        ("map", 100, 1400000),          #   1/frame, 14ms/frame
    ])
    assert "frames=100" in line
    assert "spr n=180.0 t=14.00" in line
    assert "map n=1.0 t=14.00" in line
    assert "tot=28.00" in line


def test_the_verbs_line_sorts_by_time_and_keeps_the_top():
    """The first row is nearly always the whole answer, and a serial line has
    to end somewhere -- so the cut is by cost, never by name or arrival."""
    rows = [("v%d" % i, 10, i * 1000) for i in range(20)]
    line = verbs_line(1000000, 10, rows, top=3)
    names = [p.split()[0] for p in line.split(" | ")[1:]]
    assert names == ["v19", "v18", "v17"]


def test_a_window_with_no_frames_says_so_instead_of_dividing_by_it():
    """`verbs` read before a cart has ticked is the ordinary operator mistake;
    it must answer, not raise inside the frame loop."""
    assert verbs_line(1000000, 0, [("spr", 5, 5)]) == "VERBS frames=0"
    assert verbs_line(0, 10, [("spr", 5, 5)]) == "VERBS frames=0"


def test_verbs_declines_on_a_board_with_no_moycore(capsys):
    """Every board freezes this channel; only the ones with the Lua tier can
    answer. The decline is a line, not an ImportError into the loop."""
    ws, ch = make()
    ch.run(ws, "verbs")
    assert "no moycore on this board" in _said(capsys, "REMOTE ")[0]


def test_a_verb_that_runs_lua_under_it_is_not_charged_for_it():
    """moss moss' foreach: five calls a frame, ten milliseconds INCLUSIVE, and
    none of it foreach's own. Charged inclusively it reads as the slowest thing
    in the cart and points a fix at the wrong file, so `t` is self and `in`
    carries the frame that ran underneath."""
    line = verbs_line(1000000, 100, [
        ("__moy_foreach", 500, 12000, 1016000),
        ("cls", 100, 44000, 44000),
    ])
    assert "__moy_foreach n=5.0 t=0.12 in=10.16" in line
    assert "cls n=1.0 t=0.44" in line
    cls_cell = [c for c in line.split(" | ") if c.startswith("cls")][0]
    assert "in=" not in cls_cell                 # no callees, no second number
    assert "tot=0.56" in line                    # SELF, so foreach's Lua is out
    # and the sort is by SELF, so the cheap-but-inclusive verb drops below
    assert line.index("cls") < line.index("__moy_foreach")


def test_the_verbs_line_still_reads_a_three_field_row():
    """The formatter predates the self/inclusive split and the on-glass tools
    parse its output; a row with no inclusive column means "no callees", not a
    crash."""
    assert "spr n=1.0 t=1.00" in verbs_line(1000000, 10, [("spr", 10, 10000)])


# -- the Lua-tier sampling profiler ------------------------------------------

def _shim_cart(tmp_path, before=25, body=("function p8_go() end",)):
    """A ported cart's main.lua in miniature: `before` lines of data tables,
    then the generator's two shim markers, then the cart's own code. The line
    OFFSET is the point -- the data tables above the shim vary per cart, which
    is why the range is read from the file instead of being a constant."""
    d = tmp_path / "port.moy"
    d.mkdir(parents=True)
    lines = ["-- data %d" % i for i in range(1, before)]
    lines.append("-- =========================")            # the banner
    lines.append("-- PICO-8 compatibility shim (generated by tools/p8_lua_port.py)")
    lines += ["  function shim_fn_%d() end" % i for i in range(40)]
    lines.append("-- ============== end shim =============")
    lines += list(body)
    (d / "main.lua").write_text("\n".join(lines) + "\n")
    return str(d / "main.lua")


def test_the_shim_range_is_read_from_the_cart_that_is_loaded(tmp_path):
    """The emitted block is a fixed 1,348 lines but it starts wherever that
    cart's data tables ended -- 26 lines into moss moss, 163 into a cart that
    needs the raw sheet. A constant here would file 137 lines of one cart's
    generated code as its own."""
    a = shim_line_range(_shim_cart(tmp_path / "a", before=25))
    b = shim_line_range(_shim_cart(tmp_path / "b", before=140))
    assert a == (25, 67), a
    assert b == (140, 182), b
    assert b[1] - b[0] == a[1] - a[0]           # same shim, different offset


def test_the_shim_range_reader_never_holds_the_file(tmp_path):
    """It is read in blocks with a carry because the board being asked has that
    same ~100KB cart resident and barely fitting. The carry is where such a
    reader goes wrong, so the markers are found identically at a block size
    smaller than either marker."""
    p = _shim_cart(tmp_path, before=30)
    assert shim_line_range(p, block=3) == shim_line_range(p, block=1 << 20)


def test_a_cart_with_no_shim_reports_no_range(tmp_path):
    """A hand-written Lua cart is not a port and has no generated half. That is
    "no split to make", which the profiler must say rather than guess at."""
    d = tmp_path / "plain.moy"
    d.mkdir()
    (d / "main.lua").write_text("function _draw() cls(0) end\n")
    assert shim_line_range(str(d / "main.lua")) is None
    assert shim_line_range(str(tmp_path / "gone.moy" / "main.lua")) is None


def _stats(rows, smp=1000, shim_smp=600, cyc=2000, shim_cyc=900, pinned=True,
           srcs=("cart",)):
    tot = (smp, cyc, 4000, 3000, shim_smp, shim_cyc, 2400, 0, 31, pinned)
    return (1000000, 100, 1024, tot, rows, srcs)


def test_the_luaprof_line_splits_the_emitted_shim_from_the_cart():
    """The question the whole instrument exists for: of the interpreter time a
    ported cart spends, how much is the 1,348 lines the importer emitted into
    it. `shim=` is that share of SAMPLES, with the wall-clock share beside it
    -- the two differ exactly where the collector and the C verbs are."""
    line = luaprof_line(_stats([(0, 1200, 6410, 300, 900000),
                                (0, 1600, 120, 100, 300000)]), (26, 1373))
    assert "shim=60%/45%" in line
    # 4000 Lua calls a frame-window over 100 frames, 2400 of them into the shim.
    assert "n=40 sn=24 c=30" in line
    # s/c is the row's own side, from its linedefined -- 1200 is inside the
    # shim's lines, 1600 is the cart's own code below them.
    assert "s1200 30.0% n=64.1 t=9.00" in line
    assert "c1600 10.0% n=1.2 t=3.00" in line


def test_a_refused_pin_reports_no_split_rather_than_a_wrong_one():
    """The range is checked against the shim's own `_draw` on the device. When
    they disagree the two are looking at different files, and a confident 60%
    would be a number about the wrong cart."""
    line = luaprof_line(_stats([(0, 1200, 10, 100, 1000)], pinned=False),
                        (26, 1373))
    assert "shim=n/a" in line
    assert "unpinned" in line
    assert "c1200" in line                      # nothing is claimed as shim


def test_the_luaprof_line_survives_a_window_with_no_frames():
    """Read before the cart has ticked -- "measured nothing", which is a real
    answer and not a division by zero in the loop's own print path."""
    assert "smp=0" in luaprof_line((1000000, 0, 1024, (0,) * 10, (), ()), None)


def test_luaprof_declines_on_a_board_without_the_lua_tier(capsys):
    """Same shape as `verbs`: a decline is a line, not an ImportError thrown
    into the frame loop."""
    ws, ch = make()
    ch.run(ws, "luaprof")
    assert "no moycore on this board" in _said(capsys, "REMOTE ")[0]
    ch.run(ws, "luagc")
    assert "no moycore on this board" in _said(capsys, "REMOTE ")[0]
