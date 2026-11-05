#!/usr/bin/env python3
"""The kernel's sprint-2 gate on glass (docs/history/kernel_spine_2026-10.md §10), on
one console board.

    tools/kernel_gate.py BOARD [crash] [hang] [floor] [safe] [update] [card] [crashes]   # default: all

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
         `KERNEL recovery reason=vm_start`; the crc32 of its screen is the
         host's render of the lines it printed (tests/test_recovery_floor.py), and
         `retry` brings the console back with mode 'start'.
  safe   the same floor, then `safe`: mode 'safe' with no wallpaper cart, then
         a reboot to an ordinary start.
  update the console joins its network (the driver keeps it), then the same
         floor, then `update`: with no VM the kernel's updater reaches this
         build's channel and reads what it offers (`KERNEL update offers`),
         installing nothing; `retry` brings the console back.
  card   the same floor, then `sh` lines: with no VM ever run in the boot,
         the kernel brings the card up from C (`KERNEL card mounted`), the C
         shell reads system/settings off it (`settings`, a `cat` of the file
         beside it agreeing) and lists what pkg installed (`pkg list`);
         `retry` brings the console back.
  crashes the console crashes with nothing open (`moy_crash.panic`, every
         role's OPEN id cleared first) as many boots in a row as the crash
         count's limit (`moy_crash.crash_limit`): the boots before the last come
         back to the desk with the count climbing, the last lands on the
         floor saying `reason=crashes`, its screen's crc32 the host's render
         of the lines it printed (tests/test_recovery_floor.py), an `sh` line
         runs there with no VM, a command typed into the screen (`type`,
         `key 13`, through the kernel's input table) shows its output on it
         (`screen`), and a tap on Try again (`tap` at the button's centre)
         brings the console back with the count at 0.

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

STEPS = ("crash", "hang", "floor", "safe", "update", "card", "crashes")
OFFER = re.compile(r"KERNEL update (offers version=\d+ channel=\w* size=\d+|none published)")
REPORT = re.compile(r"KERNEL recovery reason=(\w+) sel=(\w+) crc=([0-9a-f]{8})")


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
    """(reason, selected, crc, lines, hint, rows, input) from the floor's next
    full report: rows {word: (x, y, w, h)}, the input line's words."""
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
            lines, hint, rows, inp = [], None, {}, ""
            for nxt in got[i + 1:]:
                if nxt.startswith("KERNEL line "):
                    lines.append(nxt.split(" ", 3)[3] if nxt.count(" ") >= 3 else "")
                elif nxt.startswith("KERNEL row "):
                    _k, _r, _i, word, x, y, w, h = nxt.split()
                    rows[word] = (int(x), int(y), int(w), int(h))
                elif nxt.startswith("KERNEL input "):
                    inp = nxt[len("KERNEL input "):]
                elif nxt.startswith("KERNEL hint "):
                    hint = nxt[len("KERNEL hint "):]
                    break
            if hint is not None:
                return m.group(1), m.group(2), int(m.group(3), 16), lines, hint, rows, inp
    raise GateError("%s: no recovery report within %.0fs" % (name, timeout))


def _host_crc(name, lines, hint):
    """The crc of the floor the host renders for these lines and hint, with the
    same C (tests/test_recovery_floor.py). MOY_FLOOR_SHOT=<dir> keeps the
    render as <dir>/floor_<board>.png: where the crcs agree it is the board's
    screen, pixel for pixel (the floor has no `shot`)."""
    import test_recovery_floor as tf
    d = os.environ.get("MOY_FLOOR_SHOT")
    return tf.render(name, lines, hint,
                     png=os.path.join(d, "floor_%s.png" % name) if d else None)["crc"]


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
    why, _sel, crc, lines, hint, _rows, _inp = _floor(name, dirs)
    if why != "vm_start":
        raise GateError("%s: the floor says reason=%s" % (name, why))
    host = _host_crc(name, lines, hint)
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
    if mode != "safe" or wp is not None:
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


def _sh(name, dirs, line, secs=6.0):
    """(status, lines) of `sh LINE` on the floor, and what the kernel said."""
    got = _send(name, dirs, "sh " + line, secs=secs)
    out, status, kernel = [], None, []
    for ln in got:
        if ln.startswith("SH end ") or ln.startswith("SH exit "):
            status = int(ln.rsplit(" ", 1)[1])
        elif ln.startswith("SH "):
            out.append(ln[3:])
        elif ln.startswith("KERNEL card"):
            kernel.append(ln)
    return status, out, kernel


def step_card(name, dirs):
    _to_floor(name, dirs)
    try:
        code, rows, said = _sh(name, dirs, "settings", secs=10.0)
        if said != ["KERNEL card mounted"] or code != 0:
            raise GateError("%s: the floor's card said %r, settings %r %r"
                            % (name, said, code, rows))
        code2, text, _ = _sh(name, dirs, "cat /sd/moybyte/system/settings")
        if code2 != 0 or [r for r in rows if r] != [t for t in text if t]:
            raise GateError("%s: settings %r but the file reads %r" % (name, rows, text))
        code3, pkgs, _ = _sh(name, dirs, "pkg list")
        if code3 != 0:
            raise GateError("%s: pkg list ended %r: %r" % (name, code3, pkgs))
    finally:
        _send(name, dirs, "retry")
        bd.wait_for_desk(name, dirs, quiet=True)
    return "no VM: card mounted from C, settings %d rows, pkg list %d carts" % (
        len(rows), len(pkgs))


_SHELL_CRASH = ("py (__import__('moy_crash').arm(1, None), __import__('moy_crash').arm(2, None), "
                "__import__('moy_crash').arm(3, None), __import__('moy_crash').panic('fault'))")


def step_crashes(name, dirs):
    limit, count = _vals(name, dirs, "__import__('moy_crash').crash_limit()")[0]
    if count:
        raise GateError("%s: the count is %d before the step" % (name, count))
    for i in range(1, limit + 1):
        _send(name, dirs, _SHELL_CRASH)
        if i < limit:
            bd.wait_for_desk(name, dirs, quiet=True)
            got = _vals(name, dirs, "__import__('moy_crash').crash_limit()[1]")[0]
            if got != i:
                raise GateError("%s: after %d crashes the count is %r" % (name, i, got))
    why, _sel, crc, lines, hint, rows, inp = _floor(name, dirs)
    if why != "crashes":
        raise GateError("%s: the floor says reason=%s" % (name, why))
    host = _host_crc(name, lines, hint)
    if host != crc:
        raise GateError("%s: the floor's crc %08x is not the host's %08x" % (name, crc, host))
    try:
        code, out, _said = _sh(name, dirs, "echo floor-$((6 * 7))", secs=10.0)
        if code != 0 or "floor-42" not in out:
            raise GateError("%s: the floor's shell said %r %r" % (name, code, out))
        typed = "echo screen-$((6 * 7))"
        _send(name, dirs, "type " + typed, secs=0.5)
        _screen(name, dirs, lambda s: "KERNEL screen line " + typed in s, "the line typed")
        _send(name, dirs, "key 13", secs=0.5)
        _screen(name, dirs, lambda s: any("screen-42" in ln and "$((" not in ln for ln in s),
                "the command's output")
        x, y, w, h = rows["retry"]
        _send(name, dirs, "tap %d %d" % (x + w // 2, y + h // 2), secs=0.5)
    except Exception:
        _send(name, dirs, "retry")
        bd.wait_for_desk(name, dirs, quiet=True)
        raise
    bd.wait_for_desk(name, dirs, quiet=True)
    mode, after = _vals(name, dirs, "__import__('moy_kernel').mode()",
                        "__import__('moy_crash').crash_limit()[1]")
    if mode != "start" or after != 0:
        raise GateError("%s: Try again started %r with the count at %r" % (name, mode, after))
    return ("%d crashes -> floor reason=crashes (%s), crc %08x = host, sh ran, a typed command "
            "ran on screen, a tap on Try again -> start, count 0; input %s" % (
                limit, lines[0] if lines else "?", crc, inp))


def _screen(name, dirs, ok, what, timeout=20.0):
    """What the floor's screen shows (`screen`), asked until `ok` of its lines."""
    end = time.time() + timeout
    shown = []
    while time.time() < end:
        shown = [ln for ln in _send(name, dirs, "screen", secs=1.0)
                 if ln.startswith("KERNEL screen ")]
        if ok(shown):
            return shown
    raise GateError("%s: %s never showed on screen: %r" % (name, what, shown))


def _reboot(name, dirs):
    bd.main([name, "reboot"])


def main(argv=None):
    import argparse
    dirs = bd.boards()
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("board", choices=sorted(dirs), help="a console board's id")
    ap.add_argument("steps", nargs="*", metavar="step",
                    help="crash, hang, floor, safe, update, card, crashes (default: all)")
    a = ap.parse_args(argv)
    name, steps = a.board, a.steps or list(STEPS)
    if not bd.is_console(dirs[name]):
        ap.error("%s is not a console board" % name)
    bad = [s for s in steps if s not in STEPS]
    if bad:
        ap.error("unknown step %s (%s)" % (", ".join(bad), ", ".join(STEPS)))
    run = {"crash": step_crash, "hang": step_hang, "floor": step_floor,
           "safe": step_safe, "update": step_update, "card": step_card,
           "crashes": step_crashes}
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
