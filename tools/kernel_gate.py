#!/usr/bin/env python3
"""The kernel's sprint-2 gate on glass (docs/kernel_spine_2026-10.md §10), on
one console board.

    tools/kernel_gate.py BOARD [crash] [hang] [floor] [safe] [update]   # default: all five

Each step reboots the board, which a suite's held-open session cannot survive
on an attach-only board, so the gate is a tool that opens the port per step:

  crash  `kcrash kernel-test fault`: after the reboot `state`'s `crash` names
         kernel-test and a fault, the notice banner is up and Settings has its LAST CRASH row. The strike the
         dev word took is forgiven.
  hang   `khang kernel-test`: the console's frame never ends, the task
         watchdog (its timeout is the board's sdkconfig) panics the board, and
         after the reboot `state`'s `crash` is a `task_wdt` naming kernel-test with
         the reason `console hung`, and the notice banner is up (#160).
  floor  `kfail vm_start`: the board lands on the recovery screen, which says
         `KERNEL recovery reason=vm_start`; the crc32 of its framebuffer is the
         host's render of the lines it printed (tests/test_moy_kernel.py), and
         `retry` brings the console back with mode 'start'.
  safe   the same floor, then `safe`: mode 'safe' with no wallpaper cart, then
         a reboot to an ordinary start.
  update the console joins its network (the driver keeps it), then the same
         floor, then `update`: with no VM the kernel's updater reaches this
         build's channel and reads what it offers (`KERNEL update offers`),
         installing nothing; `retry` brings the console back.

One line a step, exit 1 on the first failure. The board is left at its
launcher, on an ordinary start, whatever happened.
"""

import os
import re
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))

import board as bd  # noqa: E402

STEPS = ("crash", "hang", "floor", "safe", "update")
OFFER = re.compile(r"KERNEL update (offers version=\d+ channel=\w* size=\d+|none published)")
REPORT = re.compile(r"KERNEL recovery reason=(\w+) sel=(\w+) crc=([0-9a-f]{8})")
CHOICES = ("RETRY", "SAFE", "REPL")


class GateError(RuntimeError):
    pass


def _open(name, dirs, retries=20):
    """The board's port, once it is there again after a reset."""
    for _ in range(retries):
        try:
            return bd.attach(name, dirs, force=True)
        except Exception:  # noqa: BLE001 -- the node is coming back
            time.sleep(1.0)
    raise GateError("%s: no port after the reset" % name)


def _send(name, dirs, line, secs=1.5):
    b = _open(name, dirs)
    try:
        n0 = len(b.lines)
        b._write_line(line)
        b.drain(secs)
        return b.lines[n0:]
    finally:
        b.close()


def _vals(name, dirs, *exprs):
    b = _open(name, dirs)
    try:
        out = []
        for e in exprs:
            v = b.pyval(e, timeout=15.0)
            if b.last_error:
                raise GateError("%s: %s -> %s" % (name, e, b.last_error))
            out.append(v)
        return out
    finally:
        b.close()


def _floor(name, dirs, timeout=60.0):
    """(reason, selected, crc, lines, hint) from the floor's next full report."""
    end = time.time() + timeout
    while time.time() < end:
        try:
            b = bd.attach(name, dirs, force=True)
        except Exception:  # noqa: BLE001
            time.sleep(1.0)
            continue
        try:
            got = b.drain(6.0)
        finally:
            b.close()
        for i, ln in enumerate(got):
            m = REPORT.search(ln)
            if not m:
                continue
            lines, hint = [], None
            for nxt in got[i + 1:]:
                if nxt.startswith("KERNEL line "):
                    lines.append(nxt.split(" ", 3)[3] if nxt.count(" ") >= 3 else "")
                elif nxt.startswith("KERNEL hint "):
                    hint = nxt[len("KERNEL hint "):]
                    break
            if hint is not None:
                return m.group(1), m.group(2), int(m.group(3), 16), lines, hint
    raise GateError("%s: no recovery report within %.0fs" % (name, timeout))


def _host_crc(name, lines, hint, sel):
    import test_moy_kernel as tk
    return tk.render(tk._lib(), name, lines, hint, CHOICES.index(sel))[0]


def step_crash(name, dirs):
    _send(name, dirs, "kcrash kernel-test fault")
    bd.wait_for_desk(name, dirs, quiet=True)
    crash, notice, rows = _vals(name, dirs, "__import__('moy_crash').last()", "ws._notice",
                                "[r[0] for r in ws.settings_layer._settings_rows()]")
    _vals(name, dirs, "ws.app_guard.forgive('kernel-test')")
    if "crash" not in rows:
        raise GateError("%s: Settings has no LAST CRASH row" % name)
    if not crash or crash.get("id") != "kernel-test" or crash.get("kind") != "fault":
        raise GateError("%s: state's crash is %r" % (name, crash))
    if not notice or "kernel-test" not in notice[1]:
        raise GateError("%s: the notice is %r" % (name, notice))
    return "fault in %s at pc=%08x cause=%d addr=%08x, notice up" % (
        crash["task"], crash["pc"], crash["cause"], crash["addr"])


def step_hang(name, dirs):
    armed, timeout_ms, _gap, frames = _vals(
        name, dirs, "__import__('moy_kernel').watchdog()")[0]
    if not armed:
        raise GateError("%s: the console's loop has not armed the watchdog" % name)
    _send(name, dirs, "khang kernel-test")
    bd.wait_for_desk(name, dirs, quiet=True)
    crash, notice = _vals(name, dirs, "__import__('moy_crash').last()", "ws._notice")
    _vals(name, dirs, "ws.app_guard.forgive('kernel-test')")
    if not crash or crash.get("kind") != "task_wdt" or crash.get("id") != "kernel-test":
        raise GateError("%s: state's crash is %r" % (name, crash))
    if "console hung" not in (crash.get("what") or ""):
        raise GateError("%s: the reason is %r" % (name, crash.get("what")))
    if not notice or "kernel-test" not in notice[1]:
        raise GateError("%s: the notice is %r" % (name, notice))
    return "task_wdt in %s after %ds (%s), pc=%08x, notice up" % (
        crash["task"], timeout_ms // 1000, crash["what"], crash["pc"])


def _to_floor(name, dirs):
    _send(name, dirs, "kfail vm_start")
    why, sel, crc, lines, hint = _floor(name, dirs)
    if why != "vm_start":
        raise GateError("%s: the floor says reason=%s" % (name, why))
    host = _host_crc(name, lines, hint, sel)
    if host != crc:
        raise GateError("%s: the floor's crc %08x is not the host's %08x"
                        % (name, crc, host))
    return crc


def step_floor(name, dirs):
    crc = _to_floor(name, dirs)
    _send(name, dirs, "retry")
    bd.wait_for_desk(name, dirs, quiet=True)
    mode, = _vals(name, dirs, "__import__('moy_kernel').mode()")
    if mode != "start":
        raise GateError("%s: RETRY started %r" % (name, mode))
    return "reason=vm_start crc=%08x = host render, RETRY -> start" % crc


def step_safe(name, dirs):
    _to_floor(name, dirs)
    _send(name, dirs, "safe")
    bd.wait_for_desk(name, dirs, quiet=True)
    mode, wp = _vals(name, dirs, "__import__('moy_kernel').mode()", "ws.look.wallpaper_id")
    _reboot(name, dirs)
    if mode != "safe" or not str(wp).startswith("fill:"):
        raise GateError("%s: SAFE started %r with wallpaper %r" % (name, mode, wp))
    return "SAFE -> safe, wallpaper %s; rebooted to start" % wp


def step_update(name, dirs):
    online, = _vals(name, dirs, "(ws.wifi_hold('update'), ws.updater.ensure_online(), "
                                "__import__('moy_net').wifi_status()[2], "
                                "ws.wifi_release('update'))[2]")
    if not online:
        raise GateError("%s: the console did not join its network" % name)
    _to_floor(name, dirs)
    got = _send(name, dirs, "update", secs=45.0)
    said = [ln for ln in got if ln.startswith("KERNEL update")]
    _send(name, dirs, "retry")
    bd.wait_for_desk(name, dirs, quiet=True)
    offer = [m.group(1) for m in map(OFFER.search, said) if m]
    if not offer:
        raise GateError("%s: the floor's update said %r" % (name, said))
    return "no VM: %s" % offer[0]


def _reboot(name, dirs):
    bd.main([name, "reboot"])


def main(argv=None):
    import argparse
    dirs = bd.boards()
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("board", choices=sorted(dirs), help="a console board's id")
    ap.add_argument("steps", nargs="*", metavar="step",
                    help="crash, hang, floor, safe (default: all four)")
    a = ap.parse_args(argv)
    name, steps = a.board, a.steps or list(STEPS)
    if not bd.is_console(dirs[name]):
        ap.error("%s is not a console board" % name)
    bad = [s for s in steps if s not in STEPS]
    if bad:
        ap.error("unknown step %s (crash, hang, floor, safe, update)" % ", ".join(bad))
    run = {"crash": step_crash, "hang": step_hang, "floor": step_floor,
           "safe": step_safe, "update": step_update}
    for s in steps:
        try:
            print("%-6s %-10s ok  %s" % (s, name, run[s](name, dirs)))
        except Exception as exc:  # noqa: BLE001 -- the board goes back to a desk
            print("%-6s %-10s FAIL  %s" % (s, name, exc))
            try:
                _send(name, dirs, "retry")
                _reboot(name, dirs)
            except Exception:  # noqa: BLE001
                pass
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
