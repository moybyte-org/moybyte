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
import sys
import time

import serial

BAUD = 115200
BOOT_BANNER = "desktop running"


class P4Board:
    """Serial driver for the P4 desktop's dev commands."""

    def __init__(self, port, log=None, timeout=0.2):
        self.log = log if log is not None else (lambda s: None)
        self.ser = serial.Serial()
        self.ser.port = port
        self.ser.baudrate = BAUD
        self.ser.timeout = timeout
        # dtr/rts LOW before open: opening must never glitch the CH343's
        # auto-reset circuit (reset is explicit, below).
        self.ser.dtr = False
        self.ser.rts = False
        self.ser.open()
        self._buf = b""
        self.lines = []           # full transcript (PERF lines included)

    def close(self):
        self.ser.close()

    # -- plumbing ---------------------------------------------------------

    def _pump(self):
        chunk = self.ser.read(4096)
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

    def cmd(self, text, wait_for=None, timeout=8.0):
        """Send one dev command; optionally wait for an echo line."""
        self.ser.write(text.encode() + b"\n")
        self.ser.flush()
        if wait_for is None:
            wait_for = "REMOTE"
        return self.wait_line(wait_for, timeout)

    # -- lifecycle --------------------------------------------------------

    def reset(self, boot_timeout=40.0, settle=3.0):
        """Hard-reset via the CH343 RTS pulse and wait for the desktop."""
        self.ser.rts = True
        time.sleep(0.1)
        self.ser.rts = False
        line = self.wait_line(BOOT_BANNER, timeout=boot_timeout)
        if line is None:
            raise RuntimeError("P4 did not reach the desktop after reset")
        self.drain(settle)        # splash + first frames
        return line

    # -- verbs ------------------------------------------------------------

    def state(self, timeout=8.0):
        """The `state` snapshot as a dict (see moy_runtime._remote_state)."""
        self.ser.write(b"state\n")
        self.ser.flush()
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

    def open(self, what, timeout=8.0):
        """`open settings|picker|appearance|wifi`; returns the echo line."""
        return self.cmd("open %s" % what, timeout=timeout)

    def perf_lines(self, since=0):
        return [ln for ln in self.lines[since:] if ln.startswith("PERF ")]

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
    ap.add_argument("--port", default="/dev/ttyACM0")
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
