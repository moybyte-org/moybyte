#!/usr/bin/env python3
"""On-glass P4 test driver: the host half of the serial test harness.

The P4 desktop's serial dev commands (`swipe` / `tap` / `open` / `state` /
`drag` / `run` / `diag`, see firmware/esp32_p4_wifi6_touch_lcd_7b/modules/
moy_runtime.py) exercise the REAL console -- gestures ride the same pointer
feed as the glass, `state` answers with a one-line JSON snapshot -- so a host
script can drive every menu, option and scroll on the actual hardware and
assert on the console's state, not on pixels.

Two entry points:
  * `P4Board` -- the reusable driver (tests/test_p4_on_glass.py builds on it).
  * `python tools/p4_autotest.py [--port /dev/ttyACM0]` -- a standalone tour:
    boot, open each surface, scroll Settings, report PASS/FAIL + PERF lines.

The board is left rebooted onto the desk afterwards, ready for a human.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time

try:
    import serial
except ImportError:  # pyserial is the `device` extra -- hardware only. The
    serial = None    # data half below must still import under a host suite.

BAUD = 115200
BOOT_BANNER = "desktop running"

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
P4_BOARD_DIR = os.path.join(ROOT, "firmware", "esp32_p4_wifi6_touch_lcd_7b")

sys.path.insert(0, ROOT)
from runtime.perf_line import parse_perf                          # noqa: E402


def board_dirs(root=ROOT):
    """Short board name -> its directory, DISCOVERED by globbing board.toml.

    The short name is the board file's own `[board] ota` id -- already inside a
    signed OTA manifest, so a published identifier rather than a nickname. A
    board file with no `ota` id is not a flashable board (`firmware/web_runner`
    is the browser build) and drops out by itself.

    Here rather than in a tool, because every tool that DRIVES a board over
    serial needs it and a hand-kept dict per tool is the shape this repo keeps
    paying for: nothing fails when a board is missing from one -- it simply
    cannot be driven by that tool, and no test notices. (`tools/push_cart.py`
    still carries its own older copy; folding it into this one is a one-line
    follow-up.)"""
    import glob
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import board_config
    out = {}
    for path in sorted(glob.glob(os.path.join(root, "firmware", "*",
                                              "board.toml"))):
        d = os.path.dirname(path)
        name = board_config.load(d).get("board", {}).get("ota")
        if name:
            out[name] = d
    return out


def declared_serial(board_dir=P4_BOARD_DIR):
    """A board's [serial] block: the line state at open, whether it may be
    reset at all, and the upload chunk.

    Every one of these is PER BOARD for a hardware reason and every one is
    load-bearing (see the blocks themselves): an open with the wrong dtr/rts
    chip-resets an S3 under its own handle, a reset pulse on a board that
    declares attach_only strands it, and the P4's flow-control-free UART ring
    silently drops an over-long line. tools/push_cart.py and the on-glass
    suites read the SAME declaration -- hand-writing them per caller is what
    put three copies of the same measurement in the tree.

    Missing keys fall back to the P4's, because a driver that refuses to start
    is worse than one on a default."""
    # `serial_number` is the tiebreak for a board that shares a USB id with
    # others and cannot be ASKED which it is -- the headless Zero, since it took
    # the USB-Serial/JTAG promotion and joined the console boards on 303a:1001.
    out = {"dtr": False, "rts": False, "attach_only": False, "chunk": None,
           "usb": None, "serial_number": None}
    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import board_config
        ser = board_config.load(board_dir).get("serial", {})
    except Exception:  # noqa: BLE001 -- any parse/IO failure means "use defaults"
        return out
    out.update({k: v for k, v in ser.items() if k in out})
    return out


def declared_chunk(board_dir=P4_BOARD_DIR, default=256):
    """The board's upload chunk, or `default` if it declares none."""
    return int(declared_serial(board_dir)["chunk"] or default)


def declared_board_id(board_dir=P4_BOARD_DIR):
    """The board's `[board] ota` id ("p4"/"tdeck"/"guition_s3") -- the name the
    board itself answers with (`_ota_build.BOARD`), so it is what identity
    verification compares against. None if the file declares none."""
    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import board_config
        return board_config.load(board_dir).get("board", {}).get("ota")
    except Exception:  # noqa: BLE001
        return None


# ---------------------------------------------------------------------------
# Which /dev/tty* IS this board? ttyACM numbering shuffles whenever a board
# resets or replugs, and on 2026-08-24 a whole measurement session drove the
# T-Deck believing it was the P4 -- the port number was remembered from two
# days earlier. Resolution is two layers, both data:
#   1. the [serial] usb id (vid:pid) narrows the candidates -- it fully
#      resolves the P4 (the only CH343), and CANNOT split the two S3s (both
#      are the SoC's own 303a:1001);
#   2. the board's own `_ota_build.BOARD` answer settles it -- asked over the
#      dev channel, which is also what P4Board.verify_board() re-checks after
#      every attach/reset, so even an explicit --port is caught when it points
#      at the wrong board.
# ---------------------------------------------------------------------------


def usb_id_of(port, sys_tty="/sys/class/tty"):
    """The "vid:pid" of the USB device behind a tty, or None (not USB, or not
    Linux). Walks up from the tty's sysfs node to the first ancestor carrying
    idVendor/idProduct -- the interface sits one or two levels below them."""
    node = os.path.realpath(
        os.path.join(sys_tty, os.path.basename(str(port)), "device"))
    for _ in range(6):
        vid = os.path.join(node, "idVendor")
        pid = os.path.join(node, "idProduct")
        try:
            if os.path.exists(vid) and os.path.exists(pid):
                return "%s:%s" % (open(vid).read().strip().lower(),
                                  open(pid).read().strip().lower())
        except OSError:
            return None
        nxt = os.path.dirname(node)
        if nxt == node:
            break
        node = nxt
    return None


def serial_ports():
    """The serial device nodes worth considering, sorted."""
    import glob
    return sorted(glob.glob("/dev/ttyACM*") + glob.glob("/dev/ttyUSB*"))


def _probe_identity(port, board_dir, log):
    """Attach to `port` with `board_dir`'s line discipline and ask who it is.
    Only ever called for attach_only declarations, where an open is side-effect
    free; opening a CH343 reboots its board, which a resolver must not do to a
    bystander."""
    try:
        b = P4Board(port, board_dir=board_dir)
    except Exception as exc:  # noqa: BLE001 -- busy/permission -> not this one
        log("  %s: cannot open (%s)" % (port, exc))
        return None
    try:
        b.drain(0.6)
        return b.identify(timeout=4.0)
    finally:
        b.close()


def find_port(board_dir=P4_BOARD_DIR, log=None, ports=None, usb_of=None,
              prober=None):
    """Resolve a board directory to the serial port it is plugged into.

    Raises RuntimeError with the full candidate picture on anything short of
    one confident answer -- a guessed port is exactly the bug this exists to
    remove. `ports`/`usb_of`/`prober` are injectable for the host tests."""
    log = log or (lambda s: None)
    ser = declared_serial(board_dir)
    want_usb = ser.get("usb")
    if not want_usb:
        raise RuntimeError(
            "%s declares no [serial] usb id -- cannot resolve a port for it"
            % board_dir)
    usb_of = usb_of or usb_id_of
    allp = list(ports) if ports is not None else serial_ports()
    cands = [p for p in allp if usb_of(p) == want_usb]
    if not cands:
        raise RuntimeError(
            "no serial port matches %s's usb id %s (saw: %s)"
            % (board_dir, want_usb,
               ", ".join("%s=%s" % (p, usb_of(p)) for p in allp) or "none"))
    want_id = declared_board_id(board_dir)
    if len(cands) == 1:
        # Unique on the bus -- but NOT necessarily unique by design: both S3s
        # declare 303a:1001, so with one of them unplugged the survivor
        # "uniquely" matches the OTHER board's name too. Where asking is free
        # (attach_only), ask; a mismatch is an answer, and a silent board is
        # accepted here because verify_board() re-checks downstream.
        if ser.get("attach_only") and want_id:
            prober = prober or (lambda q: _probe_identity(q, board_dir, log))
            got = prober(cands[0])
            if got is not None and got != want_id:
                raise RuntimeError(
                    "the only %s port (%s) answers as %r, not %r -- is that "
                    "board plugged in?" % (want_usb, cands[0], got, want_id))
            log("resolved %s -> %s (usb %s, identity %s)"
                % (board_dir, cands[0], want_usb, got or "unconfirmed"))
        else:
            log("resolved %s -> %s (usb %s)" % (board_dir, cands[0], want_usb))
        return cands[0]
    # Twins on the bus. Ask each -- but only where an open is side-effect free.
    if not ser.get("attach_only"):
        raise RuntimeError(
            "%d ports share usb id %s (%s) and this board's open is not "
            "side-effect free, so probing would reboot bystanders -- pass "
            "--port explicitly" % (len(cands), want_usb, ", ".join(cands)))
    if not want_id:
        raise RuntimeError(
            "%d ports share usb id %s and %s declares no [board] ota id to "
            "tell them apart" % (len(cands), want_usb, board_dir))
    prober = prober or (lambda p: _probe_identity(p, board_dir, log))
    seen = {}
    for p in cands:
        seen[p] = prober(p)
        log("  probed %s -> %s" % (p, seen[p]))
        if seen[p] == want_id:
            return p
    raise RuntimeError(
        "no candidate answered as %r: %s -- is that board's desktop running?"
        % (want_id, ", ".join("%s=%s" % kv for kv in seen.items())))


class DeviceError(RuntimeError):
    """The board answered a `py` command with an exception (or with nothing).

    Carries the device's OWN text, so a failure on the far side is reported by
    the board's words rather than by the absence of a value."""


class P4Board:
    """Serial driver for the P4 desktop's dev commands."""

    def __init__(self, port, log=None, timeout=0.2, dtr=None, rts=None,
                 chunk=None, board_dir=None, ser=None):
        """`board_dir` supplies dtr/rts/chunk/attach_only from that board's
        [serial] block; explicit arguments still win. Without one the P4's
        defaults apply -- it is the board this driver was written for.

        `ser` takes an already-open serial-like object instead of opening the
        port -- how the host tests drive the reply reader (read/write/flush and
        a `port` name are the whole contract) with no board on the desk."""
        decl = declared_serial(board_dir or P4_BOARD_DIR)
        self.log = log if log is not None else (lambda s: None)
        self.attach_only = bool(decl["attach_only"])
        self.expect_board = declared_board_id(board_dir or P4_BOARD_DIR)
        self.last_error = None    # why the last `py` round trip yielded nothing
        if ser is not None:
            self.ser = ser
            self._init_reader(chunk or decl["chunk"])
            return
        if port in (None, "auto"):
            port = find_port(board_dir or P4_BOARD_DIR, log=self.log)
        if serial is None:
            raise RuntimeError(
                "driving a board needs pyserial: pip install -e '.[device]'")
        self.ser = serial.Serial()
        self.ser.port = port
        self.ser.baudrate = BAUD
        self.ser.timeout = timeout
        # The line state AT OPEN is board-specific and load-bearing:
        #   P4 (CH343, external USB-UART): dtr/rts LOW, so opening never
        #     glitches the auto-reset circuit (reset is explicit, below).
        #   T-Deck (USB-Serial/JTAG, on the SoC): the OPPOSITE -- an open with
        #     both lines LOW is a CHIP RESET (rst:0x15 USB_UART_CHIP_RESET,
        #     measured 2026-08-17), after which the USB device re-enumerates
        #     under the open handle and every read returns nothing forever.
        #     Opening with both HIGH (pyserial's default, what miniterm does)
        #     attaches to the running console cleanly.
        want_dtr = bool(decl["dtr"]) if dtr is None else dtr
        want_rts = bool(decl["rts"]) if rts is None else rts
        # ORDER, not just state: the kernel raises BOTH lines when the tty
        # opens, and pyserial then applies dtr before rts. On the CH343 board
        # lowering DTR while RTS is still raised IS the auto-reset circuit's
        # EN pulse -- the P4 power-cycled on every open and spent 60s booting
        # before it could answer. So open with DTR
        # raised, let pyserial lower RTS, then lower DTR; both end where the
        # board declared them and reset() still works from that rest state.
        self.ser.dtr = True
        self.ser.rts = want_rts
        self.ser.open()
        if not want_dtr:
            self.ser.dtr = False
        self._init_reader(chunk or decl["chunk"])

    def _init_reader(self, chunk):
        self._buf = b""
        self.lines = []           # full transcript (PERF lines included)
        # Per instance, because the boards' rings differ (see declared_serial).
        if chunk:
            self.CHUNK = int(chunk)

    def close(self):
        self.ser.close()

    # -- plumbing ---------------------------------------------------------

    # UART boards have NO FLOW CONTROL: the P4's CH343 feeds a ~256-byte
    # stdin ring that the console drains once per ~20ms frame, so a long line
    # written in one burst (115200 baud = ~11.5 bytes/ms) overflows the ring
    # mid-line and arrives corrupted -- measured 2026-08-17: 768-byte `py`
    # lines failed 3/3 in one write and passed 3/3 sliced at 128B/20ms. The
    # old device readline masked this by blocking mid-frame and draining
    # continuously; the shared dev channel drains per frame, so the WRITER
    # must respect the ring. Short lines (a burst under the ring size) go out
    # in one write. USB boards (T-Deck) have host-side backpressure and never
    # need this, but the pacing costs them nothing on short commands.
    # 96B/40ms, not the 128B/20ms that first measured clean: under PERF DIAG
    # the loop drops toward ~25fps (40ms frames), and two 128B slices landing
    # inside one frame gap total 256B -- exactly the ring, zero margin. The
    # suite's longest line (a 512-char junk-signature probe) failed right
    # there. 96B per 40ms keeps the worst in-window arrival under half a ring
    # at any loop rate the console actually runs.
    PACE_SLICE = 96            # bytes per write burst (ring/2 - headroom)
    PACE_GAP_S = 0.04          # a diag-slowed frame period between bursts

    def _write_line(self, text):
        data = text.encode() + b"\n"
        if len(data) <= self.PACE_SLICE:
            self.ser.write(data)
            self.ser.flush()
            return
        for i in range(0, len(data), self.PACE_SLICE):
            self.ser.write(data[i:i + self.PACE_SLICE])
            self.ser.flush()
            time.sleep(self.PACE_GAP_S)


    def _pump(self):
        # ASK HOW MUCH IS THERE FIRST. `pyserial.read(n)` loops until it has n
        # bytes or the port timeout expires -- it does not return early on a
        # short read -- so `read(4096)` against a board that answered with one
        # 20-byte line costs the WHOLE 200ms timeout. That is most of the 201ms
        # a round trip was measured at, and it is paid once per window of a
        # cart push. `in_waiting` turns the common case into an exact read that
        # returns at once; read(1) is the wait when nothing has arrived yet.
        try:
            avail = min(self.ser.in_waiting, 4096)
        except Exception:  # noqa: BLE001 -- a fake/closed port: fall back
            avail = 0
        chunk = self.ser.read(avail or 1)
        if not chunk:
            return
        self._buf += chunk
        while b"\n" in self._buf:
            raw, self._buf = self._buf.split(b"\n", 1)
            line = raw.decode("utf-8", "replace").rstrip("\r")
            self.lines.append(line)
            self.log(line)

    def drain(self, secs):
        """Pump serial for `secs`; returns the lines that arrived."""
        n0 = len(self.lines)
        end = time.time() + secs
        while time.time() < end:
            self._pump()
        return self.lines[n0:]

    def wait_line(self, needle, timeout=10.0):
        """Pump until a line containing `needle` arrives; returns it or None."""
        n0 = len(self.lines)
        end = time.time() + timeout
        while time.time() < end:
            self._pump()
            for line in self.lines[n0:]:
                if needle in line:
                    return line
                n0 += 1
        return None

    def cmd(self, text, wait_for=None, timeout=8.0, retry=True):
        """Send one dev command; optionally wait for an echo line.

        A reply goes missing every so often -- measured at 2 in 5 for one size
        and 0 in 5 for six others, so it is loss, not truncation, and no chunk
        size avoids it. One resend costs 200ms and turns a failed suite into a
        passing one.

        THE RESEND IS ONLY SAFE FOR IDEMPOTENT COMMANDS, because a lost REPLY
        does not mean the command did not run. Every caller here is a probe, an
        assignment or an exec of an already-uploaded buffer; the one place that
        was not -- pyexec's `ws._up += part` -- is now an indexed store for
        exactly this reason. Pass retry=False for anything that accumulates."""
        self._write_line(text)
        if wait_for is None:
            wait_for = "REMOTE"
        line = self.wait_line(wait_for, timeout)
        if line is None and retry:
            self.log("no reply; resending: " + text[:60])
            self._write_line(text)
            line = self.wait_line(wait_for, timeout)
        return line

    # -- lifecycle --------------------------------------------------------

    def reset(self, boot_timeout=40.0, settle=3.0):
        """Hard-reset via the CH343 RTS pulse and wait for the desktop.

        CH343-ONLY. On a board whose USB-Serial/JTAG is on the SoC the pulse
        re-enumerates the USB device under this open handle and every read
        afterwards returns nothing, forever -- indistinguishable from a dead
        board. Those boards declare attach_only in their [serial] block."""
        if self.attach_only:
            raise RuntimeError(
                "this board declares attach_only: it is attached to, never "
                "reset (its USB serial is on the SoC -- a reset strands the "
                "handle)")
        self.ser.rts = True
        time.sleep(0.1)
        self.ser.rts = False
        line = self.wait_line(BOOT_BANNER, timeout=boot_timeout)
        if line is None:
            raise RuntimeError("P4 did not reach the desktop after reset")
        self.drain(settle)        # splash + first frames
        self.verify_board()
        return line

    def identify(self, timeout=6.0):
        """What the board says it IS: its `_ota_build.BOARD` ("p4"/"tdeck"/
        "guition_s3" -- the same id a signed OTA manifest names). One short
        line, so it is chunk-safe on every board. None if it does not answer
        (channel not armed, or a pre-#53-stamp image)."""
        got = self.pyval('__import__("_ota_build").BOARD', timeout=timeout)
        return got if isinstance(got, str) else None

    def verify_board(self, strict=True):
        """Compare the board's own identity against the board_dir this driver
        was configured for. A POSITIVE mismatch raises -- driving board A with
        board B's line discipline, chunk size and expectations is precisely the
        2026-08-24 bug (a session measured the T-Deck as "the P4" after the
        ttyACM numbers shuffled). A board that does not ANSWER only warns:
        identity needs a running desk, and half this driver's callers attach to
        boards in unknown states."""
        want = self.expect_board
        if not want:
            return None
        got = self.identify()
        if got is None:
            self.log("identity unverified: %s did not answer (expected %r)"
                     % (self.ser.port, want))
            return None
        if got != want and strict:
            raise RuntimeError(
                "wrong board on %s: it answers as %r, this driver is set up "
                "for %r -- the ttyACM numbering has probably shuffled "
                "(port=auto resolves it)" % (self.ser.port, got, want))
        return got

    # -- verbs ------------------------------------------------------------

    def state(self, timeout=8.0):
        """The `state` snapshot as a dict (see moy_runtime._remote_state)."""
        self._write_line("state")
        line = self.wait_line("STATE ", timeout)
        if line is None:
            raise RuntimeError("no STATE reply")
        return json.loads(line.split("STATE ", 1)[1])

    def tap(self, x, y, settle=0.4):
        self.cmd("tap %d %d" % (x, y))
        self.drain(settle)

    def swipe(self, x0, y0, x1, y1, frames=20, timeout=15.0):
        """Synthetic touch gesture; blocks until the playback finishes."""
        self.cmd("swipe %d %d %d %d %d" % (x0, y0, x1, y1, frames))
        if self.wait_line("swipe done", timeout) is None:
            raise RuntimeError("swipe never finished")
        self.drain(0.3)

    def swipe_async(self, x0, y0, x1, y1, frames=200):
        """Start a swipe and return immediately, so the caller can probe the
        console MID-gesture (the only way to observe gesture-only state such as
        the desk-cache serve or the chrome freeze). Spread the travel over many
        frames to keep it moving while you sample; finish with
        wait_line("swipe done"). NB a ZERO-motion hold does not count as a
        content gesture -- the scroll region reports one only once the finger
        travels -- so pass a real distance."""
        self.ser.write(("swipe %d %d %d %d %d\n"
                        % (x0, y0, x1, y1, frames)).encode())
        self.ser.flush()

    def open(self, what, timeout=8.0):
        """`open settings|picker|appearance|wifi`; returns the echo line."""
        return self.cmd("open %s" % what, timeout=timeout)

    # -- the `py` probe hook ----------------------------------------------

    # The device reads dev commands with one sys.stdin.readline() per frame, so
    # a command must fit the USB-CDC RX ring, and multi-line snippets upload in
    # chunks and exec once.
    #
    # 120 was set after a ~1KB one-liner came back truncated (2026-07-26) and
    # the size was never re-measured. It was expensive: ONE ROUND TRIP COSTS
    # 201ms (measured 2026-08-07 -- the device answers one command per frame,
    # and the desktop's frame is not fast), so the conformance harness spent
    # ~85 round trips a scene, most of them uploading 120 characters at a time.
    #
    # Re-measured, five tries per size: 120, 400, 512, 640, 768, 900 and 1000
    # all pass 5/5 -- and 256 passes 3/5. So the 2026-07-26 failure was not
    # length at all, it was the INTERMITTENT lost reply that shows up at every
    # size. `cmd` retries once for that (below), and the chunk was sized for
    # round trips instead: 768 is 6x fewer.
    #
    # 768 WAS WRONG, and the 5/5 above is why it survived a fortnight: this
    # UART's stdin is a ~256-byte ring with NO flow control, so an over-long
    # line is dropped as NOISE with no error -- the failure is silent, and it
    # only bites once the frame loop is slow enough (a cart running, PERF diag
    # streaming) that the ring fills between drains. The board.toml measurement
    # of 2026-08-19 caught it on a 44KB cart push (five failures, a different
    # bad hash each time; clean first try at 256), and the same size is what
    # the conformance harness and the RSA-verifier test upload through -- both
    # failed here as `SyntaxError: invalid syntax` / `ValueError: incorrect
    # padding` from a corrupted chunk, which names nothing that is wrong.
    #
    # So the size is READ from the board's own [serial] declaration rather than
    # kept as a second copy of the number. The literal below is only the
    # fallback for a checkout where board.toml cannot be read.
    CHUNK = declared_chunk()          # the P4's; USB boards pass their own

    def _fail(self, why, strict):
        """Record why a `py` round trip produced no value -- and, when the
        caller asked, RAISE it.

        The device answers an exception with its own text (`PY ERR NameError:
        name 'verify_sig' isn't defined`), and until 2026-08-27 this driver
        discarded it: a device exception, a lost reply and an unparseable value
        were all the same None. So the far side asserted `None is False`, named
        nothing, and three OTA tests were misfiled for a fortnight as a flaky
        upload while the wire said NameError once per command. Three outcomes,
        three messages."""
        self.last_error = why
        self.log("device: " + why)
        if strict:
            raise DeviceError(why)
        return None

    def pyval(self, expr, timeout=30.0, strict=False):
        """Evaluate a short expression on the device; returns the repr'd value
        (parsed back with eval), or None if the device raised / never answered.

        `strict=True` raises DeviceError carrying the device's own text instead
        -- use it wherever None is not a legal answer, which is every caller
        that goes on to assert on the result. Default False because
        `identify()` and the probe callers read None as "this board did not
        answer", which is data."""
        self.last_error = None
        line = self.cmd("py " + expr, wait_for="PY", timeout=timeout)
        if line is None:
            return self._fail("no reply to `py %s` within %gs"
                              % (expr[:120], timeout), strict)
        if "PY ERR" in line:
            return self._fail(line.strip(), strict)
        try:
            return eval(line.split("PY ", 1)[1])   # noqa: S307 (our own repr)
        except Exception as exc:  # noqa: BLE001
            return self._fail("unparseable reply %r (%s)"
                              % (line[:120], exc), strict)

    def pyexec(self, code, timeout=30.0, strict=False):
        """Run a multi-line snippet on the device (`ws`/`wm`/`pointer` in scope),
        uploaded in RX-safe chunks. Returns True if it ran clean, and leaves the
        reason on `self.last_error` otherwise (`strict=True` raises it).

        Snippets share ONE persistent namespace (`ws._g`), so a later upload can
        use names an earlier one defined. The device's `py` handler builds a
        FRESH env per command, which silently broke composed probes: a helper
        defined by upload A referencing a module imported by upload B raised
        NameError once per frame (measured 2026-07-26 -- an empty profile)."""
        self.last_error = None
        code = code.strip("\n")
        if len(code) <= self.CHUNK and "\n" not in code:
            line = self.cmd("py " + code, wait_for="PY", timeout=timeout)
            if line is None:
                self._fail("no reply to `py %s`" % code[:120], strict)
                return False
            if "PY ERR" in line:
                self._fail(line.strip(), strict)
                return False
            return True
        self.cmd("py setattr(ws, '_up', {}) or 1", wait_for="PY")
        # NB: plain getattr-or, not ws.__dict__.setdefault -- a MicroPython
        # instance __dict__ is not a full dict (no setdefault; raises TypeError).
        self.cmd("py ws._g = getattr(ws, '_g', None) or {'ws': ws, 'wm': ws.wm}",
                 wait_for="PY")
        # A DICT keyed by chunk index, not a string being appended to: `+=` is
        # not idempotent, so a resend after a lost reply would double a chunk
        # and exec a corrupted snippet. Indexed stores make the resend a no-op.
        for k, i in enumerate(range(0, len(code), self.CHUNK)):
            part = code[i:i + self.CHUNK]
            line = self.cmd("py ws._up.__setitem__(%d, %r) or 1" % (k, part),
                            wait_for="PY", timeout=timeout) or ""
            if "PY ERR" in line:
                self._fail("chunk %d rejected: %s" % (k, line.strip()), strict)
                return False
        line = self.cmd(
            "py exec(''.join(ws._up[k] for k in sorted(ws._up)), ws._g)",
            wait_for="PY", timeout=timeout) or ""
        if "PY ERR" in line:
            self._fail(line.strip(), strict)
            return False
        return True

    def perf_lines(self, since=0):
        """Every PERF sample seen since `since`.

        Through `perf_line.parse_perf`, which strips the diag ring's
        `Moybyte <uptime> ` stamp: this used to be `startswith("PERF ")`, and
        the T-Deck rings every sample, so that board's samples were silently
        dropped by both readers from the day it had any (#206 item 2)."""
        return [ln for ln in self.lines[since:] if parse_perf(ln) is not None]

    # -- geometry helpers -------------------------------------------------

    def settings_geometry(self, st=None):
        """Screen-space geometry of the Settings rows from a state snapshot:
        (center_x, row_y(i) fn, row_h). Needs the settings window open."""
        st = st or self.state()
        win = st["wins"]["settings"]
        lay = st["settings"]["lay"]          # window-local [x, y0, w, row_h]
        ox, oy = win[0] + 1, win[1] + 1 + win[4]   # content origin on screen
        cx = ox + lay[0] + lay[2] // 2

        def row_y(i):
            return oy + lay[1] + i * lay[3] + lay[3] // 2

        return cx, row_y, lay[3]


# ---------------------------------------------------------------------------
# Standalone tour
# ---------------------------------------------------------------------------

def _tour(board):
    """The standard console tour: boot, surfaces, scroll, wifi. Returns a
    list of (name, ok, detail) results."""
    results = []

    def check(name, ok, detail=""):
        results.append((name, bool(ok), detail))
        print("%-38s %s  %s" % (name, "PASS" if ok else "FAIL", detail))

    board.reset()
    st = board.state()
    check("boot: desk world, no windows",
          st.get("desk") is True and not st.get("order"),
          "desk=%s order=%s" % (st.get("desk"), st.get("order")))
    check("boot: wifi status readable", "wifi_err" not in st,
          str(st.get("wifi", st.get("wifi_err"))))

    board.cmd("diag 1")
    board.open("settings")
    board.drain(0.5)
    st = board.state()
    check("open settings -> window", "settings" in st.get("order", ()),
          str(st.get("order")))

    # Scroll the rows: swipe up by ~2.5 rows, expect set_top to advance and
    # SURVIVE the release (the on-glass "thrown back to the start" bug).
    cx, row_y, row_h = board.settings_geometry(st)
    board.swipe(cx, row_y(4), cx, row_y(4) - int(2.5 * row_h), frames=25)
    st = board.state()
    top_after = (st.get("settings") or {}).get("set_top")
    check("settings rows scroll on swipe", (top_after or 0) > 0,
          "set_top=%s" % top_after)
    board.drain(1.0)
    st2 = board.state()
    check("scroll position survives release",
          (st2.get("settings") or {}).get("set_top") == top_after,
          "set_top=%s (was %s)" % (
              (st2.get("settings") or {}).get("set_top"), top_after))

    line = board.open("appearance")
    board.drain(0.5)
    st = board.state()
    check("appearance opens", "appearance" in st.get("order", ()),
          "%s | cart=%s" % (line, st.get("appearance_cart")))

    board.open("picker")
    board.drain(6.0)              # cover pop-in settles
    st = board.state()
    check("picker opens", "make" in st.get("order", ()), str(st.get("order")))

    n0 = len(board.lines)
    board.drain(4.0)
    perf = board.perf_lines(n0)
    check("idle PERF flows", len(perf) >= 1,
          perf[-1] if perf else "no PERF lines")
    return results


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--port", default="auto",
                    help="serial port, or 'auto' (default): resolve from the "
                         "board's [serial] usb id -- ttyACM numbers shuffle")
    ap.add_argument("--verbose", action="store_true",
                    help="echo every serial line")
    args = ap.parse_args()
    log = (lambda s: print("  | " + s)) if args.verbose else None
    board = P4Board(args.port, log=log)
    try:
        results = _tour(board)
    finally:
        try:                       # leave the board fresh on the desk
            board.ser.write(b"\r\x03")
            time.sleep(0.5)
            board.ser.write(b"\x04")
            board.drain(1.0)
        except Exception:  # noqa: BLE001
            pass
        board.close()
    failed = [r for r in results if not r[1]]
    print("\n%d/%d passed" % (len(results) - len(failed), len(results)))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
