#!/usr/bin/env python3
"""The test files of one CI shard: `.github/workflows/ci.yml`'s test job runs
the suite as N shards a Python version, each serially, and asks this which
files are its own.

    tools/ci_shard.py N K            shard K of N (1-based): its files, one line
    tools/ci_shard.py N --table      every shard's files and seconds
    tools/ci_shard.py --weigh LOG    weights from a serial run's --durations=0
                                     output, as JSON for WEIGHTS

Every `tests/test_*.py` lands in exactly one shard, except EXCLUDED, which
the workflow runs on its own (`tests/test_ci_shard.py` pins both). A file is
weighed by the seconds WEIGHTS records for it, measured from one serial run
of the whole suite (setup, call and teardown summed per file); a file not
there weighs the median. The files go, heaviest first, to the lightest shard
so far, so a shard's wall time is its weight sum plus the job's setup. A
weight drifts as tests change; re-weigh when the shards' times in CI spread:

    .venv/bin/python -m pytest -q --durations=0 \\
        --ignore=tests/test_redraw_on_change.py > /tmp/serial.log
    tools/ci_shard.py --weigh /tmp/serial.log > tools/ci_shard_weights.json
"""

import argparse
import glob
import json
import os
import re
import statistics
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
WEIGHTS = os.path.join(HERE, "ci_shard_weights.json")
# Asserts exact repaint counts against the wall clock: CI runs it alone.
EXCLUDED = ("tests/test_redraw_on_change.py",)
DURATION_RE = re.compile(r"^\s*([\d.]+)s\s+(?:setup|call|teardown)\s+(tests/[^:\s]+\.py)::")


def test_files(root=ROOT):
    """Every test file the suite collects, but EXCLUDED, repo-relative."""
    found = sorted(os.path.relpath(p, root).replace(os.sep, "/")
                   for p in glob.glob(os.path.join(root, "tests", "test_*.py")))
    return [f for f in found if f not in EXCLUDED]


def load_weights(path=WEIGHTS):
    with open(path) as f:
        return json.load(f)


def shards(n, files=None, weights=None):
    """[(seconds, [file, ...]), ...] for shards 1..n."""
    files = test_files() if files is None else files
    weights = load_weights() if weights is None else weights
    known = [weights[f] for f in files if f in weights]
    default = statistics.median(known) if known else 1.0
    out = [[0.0, []] for _ in range(n)]
    for f in sorted(files, key=lambda f: (-weights.get(f, default), f)):
        lightest = min(range(n), key=lambda i: (out[i][0], i))
        out[lightest][0] += weights.get(f, default)
        out[lightest][1].append(f)
    return [(round(s, 1), sorted(fs)) for s, fs in out]


def weigh(lines):
    """{file: seconds} from pytest's --durations=0 lines."""
    w = {}
    for line in lines:
        m = DURATION_RE.match(line)
        if m:
            w[m.group(2)] = w.get(m.group(2), 0.0) + float(m.group(1))
    return dict((f, round(s, 2)) for f, s in sorted(w.items()))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("n", nargs="?", type=int, help="how many shards")
    ap.add_argument("k", nargs="?", type=int, help="which shard, 1-based")
    ap.add_argument("--table", action="store_true", help="every shard's files and seconds")
    ap.add_argument("--weigh", metavar="LOG", help="weights from a --durations=0 log")
    a = ap.parse_args(argv)
    if a.weigh:
        with open(a.weigh) as f:
            json.dump(weigh(f), sys.stdout, indent=1)
        print()
        return 0
    if not a.n or a.n < 1:
        ap.error("name the number of shards")
    out = shards(a.n)
    if a.table:
        for i, (s, fs) in enumerate(out, 1):
            print("shard %d: %4d files, %7.1f s" % (i, len(fs), s))
        return 0
    if not a.k or not 1 <= a.k <= a.n:
        ap.error("name a shard from 1 to %d" % a.n)
    print(" ".join(out[a.k - 1][1]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
