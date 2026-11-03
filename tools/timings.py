#!/usr/bin/env python3
"""Where the time goes: every timed run on this machine, in one log.

    tools/timings.py                     # recent runs, then each lane's median
    tools/timings.py --tool pass         # one tool's runs
    tools/timings.py --lane tdeck        # lanes containing "tdeck"
    tools/timings.py add --tool build tdeck=312.4 tdeck/compile=201.0 \
        [--status fail] [--ccache HITS,MISSES]

The log is `~/.cache/moybyte/timings.csv` (MOYBYTE_TIMINGS overrides), beside
the build lock and outside every checkout, so the main checkout and each
worktree append to the same file. One line a lane:

    date, checkout, rev, tool, lane, seconds, status, ccache_hits, ccache_misses

`tool` is what timed it: `preflight`, `build` (a board's build.sh) or `pass`
(`tools/board.py pass`). A lane with no `/` is a whole -- a preflight, one
board's build, one board's pass, a pass's wall time -- and `lane/part` is a
step inside it. The ccache counts are the machine-wide delta over the run, so
a build overlapping another counts the other's hits too.

Stdlib only: a board build calls this with the system python3.
"""

import argparse
import csv
import fcntl
import os
import statistics
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIELDS = ["date", "checkout", "rev", "tool", "lane", "seconds", "status",
          "ccache_hits", "ccache_misses"]
# A lane's last run this much over its median is flagged in the summary.
SLOW = 1.25
WINDOW = 10


def log_path():
    return os.environ.get("MOYBYTE_TIMINGS") or os.path.join(
        os.path.expanduser("~"), ".cache", "moybyte", "timings.csv")


def checkout_and_rev(root=ROOT):
    """("main" or the worktree's directory name, the short commit)."""
    try:
        out = subprocess.run(
            ["git", "-C", root, "rev-parse", "--path-format=absolute",
             "--show-toplevel", "--git-dir", "--git-common-dir",
             "--short", "HEAD"],
            capture_output=True, text=True, check=True).stdout.split()
        top, gdir, common, rev = out
    except (OSError, subprocess.CalledProcessError, ValueError):
        return os.path.basename(root), "?"
    same = os.path.realpath(gdir) == os.path.realpath(common)
    return ("main" if same else os.path.basename(top)), rev


def record(tool, lanes, status="ok", ccache=None, root=ROOT, path=None,
           when=None):
    """Append one line per (lane, seconds) in `lanes`. `ccache` is
    (hits, misses) and goes on the first line, the run's whole."""
    path = path or log_path()
    checkout, rev = checkout_and_rev(root)
    date = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(when))
    rows = []
    for i, (lane, secs) in enumerate(lanes):
        hits, misses = (ccache if ccache and i == 0 else ("", ""))
        rows.append([date, checkout, rev, tool, lane, "%.1f" % secs, status,
                     hits, misses])
    if not rows:
        return
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    with open(path, "a", newline="") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            f.seek(0, os.SEEK_END)
            w = csv.writer(f)
            if f.tell() == 0 and path != os.devnull:
                w.writerow(FIELDS)
            w.writerows(rows)
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


def read(path=None):
    path = path or log_path()
    try:
        with open(path, newline="") as f:
            rows = list(csv.DictReader(f))
    except FileNotFoundError:
        return []
    out = []
    for r in rows:
        try:
            r["seconds"] = float(r["seconds"])
        except (TypeError, ValueError, KeyError):
            continue
        out.append(r)
    return out


def dur(secs):
    """14m32s, 3.2s, 1h02m."""
    if secs < 60:
        return "%.1fs" % secs
    m, s = divmod(int(round(secs)), 60)
    if m < 60:
        return "%dm%02ds" % (m, s)
    return "%dh%02dm" % divmod(m, 60)


def _cols(lines):
    widths = [max(len(ln[i]) for ln in lines) for i in range(len(lines[0]))]
    return ["  ".join(c.ljust(w) for c, w in zip(ln, widths)).rstrip()
            for ln in lines]


def summary(rows, tool=None, lane=None, recent=15, window=WINDOW):
    """The last `recent` wholes, then per (tool, lane) the median of its last
    `window` passing runs beside its last run, flagged when it is slow."""
    rows = [r for r in rows if (not tool or r["tool"] == tool)
            and (not lane or lane in r["lane"])]
    if not rows:
        return "no timed runs yet (%s)" % log_path()
    out = []
    wholes = [r for r in rows if "/" not in r["lane"]][-recent:]
    if wholes:
        out.append("recent runs:")
        lines = [["date", "checkout", "rev", "tool", "lane", "time", "status",
                  "ccache hit/miss"]]
        for r in wholes:
            cc = ("%s/%s" % (r["ccache_hits"], r["ccache_misses"])
                  if r.get("ccache_hits") else "")
            lines.append([r["date"][:16], r["checkout"], r["rev"], r["tool"],
                          r["lane"], dur(r["seconds"]), r["status"], cc])
        out += ["  " + ln for ln in _cols(lines)]
        out.append("")
    groups = {}
    for r in rows:
        if r["status"] == "ok":
            groups.setdefault((r["tool"], r["lane"]), []).append(r["seconds"])
    out.append("median of the last %d passing runs:" % window)
    lines = [["tool", "lane", "n", "median", "last", ""]]
    # Each tool's wholes in turn, a whole's parts in the order they ran.
    order = sorted(groups.items(), key=lambda kv: (kv[0][0],
                                                   kv[0][1].split("/")[0]))
    for (t, ln), secs in order:
        secs = secs[-window:]
        med = statistics.median(secs)
        flag = ("slow: %.1fx" % (secs[-1] / med)
                if len(secs) >= 3 and med > 0 and secs[-1] > SLOW * med else "")
        lines.append([t, ln, str(len(secs)), dur(med), dur(secs[-1]), flag])
    out += ["  " + ln for ln in _cols(lines)]
    return "\n".join(out)


def _lane(s):
    lane, sep, secs = s.rpartition("=")
    if not sep or not lane:
        raise argparse.ArgumentTypeError("want LANE=SECONDS, got %r" % s)
    return lane, float(secs)


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == ["add"]:
        ap = argparse.ArgumentParser(prog="tools/timings.py add")
        ap.add_argument("--tool", required=True)
        ap.add_argument("--status", default="ok")
        ap.add_argument("--ccache", help="HITS,MISSES over the run")
        ap.add_argument("lanes", nargs="+", type=_lane, metavar="LANE=SECONDS")
        a = ap.parse_args(argv[1:])
        cc = tuple(a.ccache.split(",", 1)) if a.ccache else None
        try:
            record(a.tool, a.lanes, a.status, cc)
        except OSError as exc:
            # A log that cannot be written never fails the run it times.
            print("timings: not recorded (%s)" % exc, file=sys.stderr)
        return 0
    ap = argparse.ArgumentParser(prog="tools/timings.py",
                                 description="Recent timed runs and each "
                                             "lane's median.")
    ap.add_argument("--tool", help="preflight, build or pass")
    ap.add_argument("--lane", help="only lanes containing this")
    ap.add_argument("--recent", type=int, default=15)
    ap.add_argument("--window", type=int, default=WINDOW)
    a = ap.parse_args(argv)
    print(summary(read(), a.tool, a.lane, a.recent, a.window))
    return 0


if __name__ == "__main__":
    sys.exit(main())
