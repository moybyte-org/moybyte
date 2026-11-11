#!/usr/bin/env python3
"""A board pass in one command: build each board's image from THIS tree, flash
it, wait for the fresh boot, run its on-glass suite, print one table.

    tools/board.py pass tdeck p4            build, flash, suite, on two boards
    tools/board.py pass --all -k wasm       every console board; -k goes to pytest
    tools/board.py pass tdeck --skip-build  flash the image already in dist/
    tools/board.py pass tdeck --no-flash    the suite only, on the image the board has
    tools/board.py pass --all --shot        ...and a screenshot of each at the end
    tools/board.py pass --all --build-only  every board's image, the Zero's too;
                                            no board attached (preflight's lane)

It is ONE job however many boards it covers: start it in the background and
take its one completion notice. It prints a line naming the log directory,
then nothing until the table and its wall time, which are always its last
lines.

One row a board: the commit its image was built from (`+` when the tree had
tracked changes on top), the OTA-slot headroom the build printed, the suite's
passed/skipped/failed counts, where its time went (the build's wait for the
configure lock, build, flash, boot to the desk, suite, the board's total) and the
failing tests by name. Under the table, the pass's wall time; every time is
also appended to the machine's timing log (tools/timings.py, tool `pass`).
Every step's full output goes to a log file under one directory, whose path
is printed.

A PASS THAT BUILDS builds the browser console first
(`firmware/web_runner/build.sh`, once), because every image bakes this tree's
bundle and one built without it fails its suite's baked-console check.

THE MAIN CHECKOUT IS REFUSED FOR A BUILD (`--main` overrides): a build writes
`dist/` and each board's `.build/`, and those are the images every other
session flashes from. Build in a worktree (`tools/worktree.py new NAME`).

EVERY BOARD BUILDS AT ONCE, their configure steps one at a time. Every ESP32
build checks out the same git component from the component manager's one
cache while it configures, so two builds configuring at the same moment race
on its index.lock (`.claude/rules/boards.md`). The build itself holds an
flock under ~/.cache/moybyte while it configures and lets go at its first
compiled step (tools/esp32_build_lib.sh `moybyte_configure_lock`), across
every pass and worktree on the machine; the wait is the row's `wait` column.
A build that lost the race anyway is run again (twice at most, after a
pause). Each board's whole run (build, flash, suite) goes in its own thread;
the ports are resolved once, up front, and passed to every step, because
four boards share one usb id.

THE BOARD IS LEFT AT ITS LAUNCHER (`board.py desk` after the suite), the
T-Deck at volume 0 (the dev channel's `vol 0`, before and after the suite).
Only console boards are passed: the headless Zero is never touched. A port
another process holds skips its board, naming the holder.
"""

import argparse
import json
import os
import random
import re
import shlex
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import board                                                       # noqa: E402
import timings                                                     # noqa: E402

BOARD_PY = os.path.join(HERE, "board.py")
# Every image bakes the browser console from this tree's bundle, so a pass
# that builds images builds the bundle first.
WEB_BUILD = os.path.join(ROOT, "firmware", "web_runner", "build.sh")
# Boards whose speaker stays silent on this desk.
QUIET = ("tdeck",)
# What a build that lost the component cache's index.lock prints, CMake's
# wrap between the two halves included; such a build runs again after a pause
# drawn from RETRY_PAUSE_S.
LOST_RACE = "index.lock':\n  File exists"
LOST_RACE_RE = re.compile(r"index\.lock':\s+File exists")
RETRIES = 2
RETRY_PAUSE_S = (5.0, 20.0)
# The build's wait for the configure lock, as its phase table prints it.
LOCK_RE = re.compile(r"^== time: lock (?:(\d+)m)?([\d.]+)s$", re.M)
FLASH_RE = re.compile(r"board flash: written in ([\d.]+)s")
HEADROOM_RE = re.compile(r"App image: \d+ bytes of a \d+-byte \S+ slot -- "
                         r"(-?\d+) bytes headroom")
GATE_RE = re.compile(r"""on_glass\.gate\(\s*["'](MOYBYTE_\w+_PORT)["']""")
DIR_RE = re.compile(r"""ROOT\s*/\s*["']firmware["']\s*/\s*["'](\w+)["']""")


class PassError(RuntimeError):
    """A pass that cannot start, in words a person can act on."""


# A row's time columns, in the order a board takes them.
STEPS = ("wait", "build", "flash", "boot", "suite", "total")


class Row:
    """One board's result. `note` names the step that stopped it, if one did."""

    def __init__(self, name):
        self.name = name
        self.image = "-"
        self.headroom = "-"
        self.passed = self.skipped = self.failed = None
        self.failing = []
        self.note = ""
        self.logs = []
        # Seconds per step, None for a step the board did not take.
        self.times = dict.fromkeys(STEPS)

    @property
    def ok(self):
        return not self.note and self.failed in (0, None)

    def cells(self):
        def n(v):
            return "-" if v is None else str(v)
        def t(v):
            return "-" if v is None else timings.dur(v)
        why = self.note or ", ".join(self.failing)
        return ([self.name, self.image, self.headroom, n(self.passed),
                 n(self.skipped), n(self.failed)]
                + [t(self.times[k]) for k in STEPS] + [why])


# -- what a pass covers --------------------------------------------------------


def in_main_checkout(root=ROOT):
    """True when `root` is the repository's main checkout, not a worktree."""
    try:
        out = subprocess.run(
            ["git", "-C", root, "rev-parse", "--path-format=absolute",
             "--git-dir", "--git-common-dir"],
            capture_output=True, text=True, check=True).stdout.split()
    except (OSError, subprocess.CalledProcessError):
        return False
    return len(out) == 2 and os.path.realpath(out[0]) == os.path.realpath(out[1])


def suites(root=ROOT, dirs=None):
    """{board: (suite path relative to root, the port variable it is gated
    on)}, read from each `tests/test_*_on_glass.py`: the variable its
    `on_glass.gate(...)` names and the board dir its session opens."""
    dirs = board.boards(root) if dirs is None else dirs
    by_dir = {os.path.basename(d): name for name, d in dirs.items()}
    out = {}
    tests = os.path.join(root, "tests")
    for f in sorted(os.listdir(tests)):
        if not (f.startswith("test_") and f.endswith("_on_glass.py")):
            continue
        with open(os.path.join(tests, f)) as fh:
            src = fh.read()
        gate, bdir = GATE_RE.search(src), DIR_RE.search(src)
        if gate and bdir and bdir.group(1) in by_dir:
            out[by_dir[bdir.group(1)]] = ("tests/" + f, gate.group(1))
    return out


def plan(a, dirs, main_checkout):
    """The boards this pass runs, in tree order; raises PassError on a
    refusal."""
    consoles = [n for n in sorted(dirs) if board.is_console(dirs[n])]
    if a.all and a.boards:
        raise PassError("name boards or pass --all, not both")
    if a.build_only and (a.skip_build or a.no_flash or a.shot or a.k):
        raise PassError("--build-only builds, and nothing else")
    if a.build_only:
        consoles = sorted(dirs)
    names = consoles if a.all else a.boards
    if not names:
        raise PassError("which boards? name them (%s) or pass --all"
                        % ", ".join(consoles))
    for n in names:
        if n not in dirs:
            raise PassError("no board %r -- one of %s" % (n, ", ".join(consoles)))
        if n not in consoles:
            raise PassError("%s has no on-glass suite and is never passed" % n)
    builds = not (a.skip_build or a.no_flash)
    if builds and main_checkout and not a.main:
        raise PassError(
            "this is the main checkout: a build here overwrites dist/ and the "
            "boards' .build/ trees, the images every other session flashes. "
            "Run it from a worktree (`tools/worktree.py new NAME`), or pass "
            "--main to build here on purpose.")
    return list(dict.fromkeys(names))


# -- the steps ----------------------------------------------------------------


def run_logged(cmd, log, env=None):
    """Run `cmd` from the tree's root with its output appended to `log`."""
    with open(log, "a") as out:
        out.write("$ %s\n" % shlex.join(cmd))
        out.flush()
        return subprocess.call(cmd, stdout=out, stderr=subprocess.STDOUT,
                               cwd=ROOT, env=env)


def lock_wait(log):
    """Seconds the build in `log` waited for the configure lock, summed over
    its attempts; 0.0 when it printed none."""
    try:
        with open(log, errors="replace") as f:
            text = f.read()
    except OSError:
        return 0.0
    return sum(int(m or 0) * 60 + float(s) for m, s in LOCK_RE.findall(text))


def build(run, board_dir, log):
    """build.sh, run again when it lost the component cache's lock."""
    for attempt in range(RETRIES + 1):
        start = os.path.getsize(log) if os.path.exists(log) else 0
        rc = run(["bash", os.path.join(board_dir, "build.sh")], log)
        if rc == 0 or attempt == RETRIES:
            return rc
        with open(log, errors="replace") as f:
            f.seek(start)
            if not LOST_RACE_RE.search(f.read()):
                return rc
        with open(log, "a") as f:
            f.write("board pass: lost the component cache's lock; again\n")
        time.sleep(random.uniform(*RETRY_PAUSE_S))
    return rc


def image_path(board_dir):
    return os.path.join(ROOT, board.board_file(board_dir).get("flash", {})
                        .get("image", ""))


def image_built_since(board_dir, since):
    """Whether the image in dist/ was written at or after `since` (wall
    seconds, a second's slack for the filesystem's coarse clock): a build
    that answered 0 without linking leaves the last one, which is not this
    tree's."""
    try:
        return os.path.getmtime(image_path(board_dir)) >= since - 1.0
    except OSError:
        return False


def image_stamp(board_dir):
    """The commit the image in dist/ was built from, from the build's
    `ota_build.json` beside it; `?` from a build that did not stamp one."""
    path = os.path.join(os.path.dirname(image_path(board_dir)), "ota_build.json")
    try:
        with open(path) as f:
            return json.load(f).get("commit") or "?"
    except (OSError, ValueError):
        return "?"


def headroom_of(log):
    try:
        with open(log, errors="replace") as f:
            hits = HEADROOM_RE.findall(f.read())
    except OSError:
        return "-"
    return "%d KB" % (int(hits[-1]) // 1024) if hits else "-"


def junit_counts(path):
    """(passed, skipped, failed, [failing test names]) from pytest's junit
    XML, or None when there is none (the run never got that far)."""
    try:
        tree = ET.parse(path)
    except (OSError, ET.ParseError):
        return None
    passed = skipped = 0
    failing = []
    for tc in tree.iter("testcase"):
        tags = {c.tag for c in tc}
        if tags & {"failure", "error"}:
            failing.append(tc.get("name") or "?")
        elif "skipped" in tags:
            skipped += 1
        else:
            passed += 1
    return passed, skipped, len(failing), failing


def run_board(name, a, ctx):
    """One board, start to finish. Never raises: a stopped step is the row's
    note, with the log that says why."""
    t0 = time.monotonic()
    row = Row(name)
    try:
        _run_board(row, a, ctx)
    except Exception as exc:  # noqa: BLE001 -- one board's crash is its row
        row.note = "pass error: %s" % (str(exc).splitlines() or [repr(exc)])[0]
    row.times["total"] = time.monotonic() - t0
    return row


def flash_split(log, start, secs):
    """(flash, boot) of a `board.py flash` that took `secs`, from the line it
    prints when the image is written; (secs, None) without one."""
    with open(log, errors="replace") as f:
        f.seek(start)
        m = FLASH_RE.search(f.read())
    if not m:
        return secs, None
    wrote = min(float(m.group(1)), secs)
    return wrote, secs - wrote


def _run_board(row, a, ctx):
    name = row.name
    d, port, run = ctx["dirs"][name], ctx["ports"][name], ctx["run"]
    logs = ctx["logdir"]

    def log(step):
        path = os.path.join(logs, "%s.%s.log" % (name, step))
        row.logs.append(path)
        return path

    def stop(why, path):
        with open(path, "a") as f:
            f.write("board pass: %s\n" % why)
        row.note = "%s (%s)" % (why, os.path.basename(path))
        return row

    def verb(*args):
        return [sys.executable, BOARD_PY, name] + list(args) + ["--port", port]

    if not a.no_flash and not a.skip_build:
        path = log("build")
        t, began = time.monotonic(), time.time()
        rc = build(run, d, path)
        row.times["wait"] = lock_wait(path)
        row.times["build"] = max(0.0, time.monotonic() - t - row.times["wait"])
        row.headroom = headroom_of(path)
        if rc != 0:
            return stop("build failed", path)
        if not image_built_since(d, began):
            return stop("no image built (the one in dist/ predates this build)", path)
        if a.build_only:
            row.image = image_stamp(d)
            return row
    if not a.no_flash:
        path = log("flash")
        if not os.path.isfile(image_path(d)):
            return stop("no image built", path)
        row.image = image_stamp(d)
        if board.holders(port):
            return stop("port held", path)
        start = os.path.getsize(path) if os.path.exists(path) else 0
        t = time.monotonic()
        rc = run(verb("flash"), path)
        row.times["flash"], row.times["boot"] = flash_split(
            path, start, time.monotonic() - t)
        if rc != 0:
            return stop("flash failed", path)
    path = log("suite")
    t = time.monotonic()
    if board.holders(port):
        return stop("port held", path)
    run(verb("desk"), path)
    if name in QUIET:
        run(verb("tail", "1", "--send", "vol 0", "--grep", "REMOTE"), path)
    suite, var = ctx["suites"][name]
    xml = os.path.join(logs, "%s.junit.xml" % name)
    cmd = [sys.executable, "-m", "pytest", suite, "-q", "-p", "no:cacheprovider",
           "--junitxml", xml]
    if a.k:
        cmd += ["-k", a.k]
    env = dict(os.environ, **{var: port})
    if os.path.exists(xml):
        os.remove(xml)
    run(cmd, path, env=env)
    row.times["suite"] = time.monotonic() - t
    got = junit_counts(xml)
    if got is None:
        stop("suite did not run", path)
    else:
        row.passed, row.skipped, row.failed, row.failing = got
    if not board.holders(port):
        run(verb("desk"), path)
        if name in QUIET:
            run(verb("tail", "1", "--send", "vol 0", "--grep", "REMOTE"), path)
        if a.shot:
            run(verb("shot", os.path.join(logs, "%s.png" % name)), path)
    return row


# -- the table ----------------------------------------------------------------


HEAD = (["board", "image", "headroom", "passed", "skipped", "failed"]
        + list(STEPS) + ["failing / stopped at"])


def table(rows):
    lines = [HEAD] + [r.cells() for r in rows]
    widths = [max(len(ln[i]) for ln in lines) for i in range(len(HEAD) - 1)]
    out = []
    for ln in lines:
        out.append("  ".join(c.ljust(w) for c, w in zip(ln, widths))
                   + "  " + ln[-1])
    return "\n".join(s.rstrip() for s in out)


# -- the command line ---------------------------------------------------------


def parser():
    ap = argparse.ArgumentParser(
        prog="tools/board.py pass",
        description="Build, flash and suite each board; print one table.")
    ap.add_argument("boards", nargs="*", help="board ids (the [board] ota id)")
    ap.add_argument("--all", action="store_true", help="every console board")
    ap.add_argument("--skip-build", action="store_true",
                    help="flash the image already built")
    ap.add_argument("--no-flash", action="store_true",
                    help="the suite only, on the image each board has")
    ap.add_argument("--shot", action="store_true",
                    help="a screenshot of each board at the end")
    ap.add_argument("-k", help="passed to pytest: only the tests it selects")
    ap.add_argument("--build-only", action="store_true",
                    help="build each image and stop: no port, no flash, no "
                         "suite; with --all, every board the tree builds")
    ap.add_argument("--main", action="store_true",
                    help="build in the main checkout anyway")
    ap.add_argument("--logs", help="the log directory (default: one under "
                                   "$TMPDIR/board-pass)")
    return ap


def default_logdir():
    base = os.path.join(os.environ.get("TMPDIR", "/tmp"), "board-pass")
    return os.path.join(base, "%s-%s" % (os.path.basename(ROOT),
                                         time.strftime("%Y%m%d-%H%M%S")))


def resolve_ports(names, dirs):
    """{board: port} and the rows of boards that cannot be reached."""
    ports, stopped = {}, []
    for n in names:
        try:
            p = board.stable(board.resolve(n, dirs)[0])
        except board.BoardError as exc:
            row = Row(n)
            row.note = str(exc).split(" -- ")[0]
            stopped.append(row)
            continue
        held = board.holders(p)
        if held:
            row = Row(n)
            row.note = "port held by pid %d: %s" % (held[0][0], held[0][1][:60])
            stopped.append(row)
            continue
        ports[n] = p
    return ports, stopped


def record(rows, wall, web, ok):
    """The pass's times into the machine's timing log. Never fails a pass."""
    try:
        whole = [("pass", wall)] + ([("pass/web", web)] if web is not None
                                    else [])
        timings.record("pass", whole, "ok" if ok else "fail")
        for r in rows:
            lanes = [(r.name, r.times["total"])] if r.times["total"] else []
            lanes += [("%s/%s" % (r.name, k), r.times[k])
                      for k in STEPS[:-1] if r.times[k] is not None]
            timings.record("pass", lanes, "ok" if r.ok else "fail")
    except OSError as exc:
        print("board pass: times not recorded (%s)" % exc)


def main(argv=None, run=run_logged, dirs=None, main_checkout=None):
    t0 = time.monotonic()
    a = parser().parse_args(argv)
    dirs = board.boards() if dirs is None else dirs
    if main_checkout is None:
        main_checkout = in_main_checkout()
    try:
        names = plan(a, dirs, main_checkout)
    except PassError as exc:
        print(exc, file=sys.stderr)
        return 2
    if a.build_only:
        ports, stopped = {n: None for n in names}, []
    else:
        ports, stopped = resolve_ports(names, dirs)
    logdir = a.logs or default_logdir()
    os.makedirs(logdir, exist_ok=True)
    print("board pass: %s  (logs: %s)" % (", ".join(names), logdir), flush=True)
    ctx = {"dirs": dirs, "ports": ports, "run": run, "logdir": logdir,
           "suites": suites(ROOT, dirs)}
    todo = [n for n in names if n in ports]
    rows = {r.name: r for r in stopped}
    web = None
    if todo and not (a.skip_build or a.no_flash):
        web_log = os.path.join(logdir, "web.build.log")
        t = time.monotonic()
        rc = run(["bash", WEB_BUILD], web_log)
        web = time.monotonic() - t
        if rc != 0:
            for n in todo:
                rows[n] = Row(n)
                rows[n].note = "web build failed (web.build.log)"
                rows[n].logs.append(web_log)
            todo = []
    if todo:
        with ThreadPoolExecutor(max_workers=len(todo)) as pool:
            for row in pool.map(lambda n: run_board(n, a, ctx), todo):
                rows[row.name] = row
    ordered = [rows[n] for n in names]
    ok = all(r.ok for r in ordered)
    wall = time.monotonic() - t0
    record(ordered, wall, web, ok)
    print(table(ordered))
    for r in ordered:
        if (r.note or r.failed) and r.logs:
            print("%s: %s" % (r.name, r.logs[-1]))
    print("board pass: wall %s%s" % (
        timings.dur(wall),
        "" if web is None else ", browser console build %s" % timings.dur(web)))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
