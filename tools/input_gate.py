#!/usr/bin/env python3
# Map (grep -n a name to jump there):
#   -- the images        Image: a worktree's built image, its commit, its key
#   -- the runs          Sel, flash, bench, Runner: a fresh flash, then input_bench, per board in parallel
#   -- the baselines     Store: dev runs by commit, board and date; coverage; the sentinel's rows and drift
#   -- the compare       load_run, verdict, compare, groups, render: the p90 table and its verdicts
#   -- like-for-like     probed, lfl_p90: a row's frames re-classed with its probe ignored
#   gate_board           one board's gate: baseline or sentinel, candidate runs, the third run
#   main                 the CLI: run, baseline, sentinel, gate, iter, compare
"""A surface's input gate: `tools/input_bench.py`'s p90 per row, a candidate
image against same-day dev on fresh boots, on every board named.

    python3 tools/input_gate.py gate --board tdeck,p4 --dev WT_DEV --cand WT_CAND --out DIR \\
        [--only Code,"Text console"] [--inputs fling,type] [--runs 2] [--tol MS]
    python3 tools/input_gate.py iter --board tdeck --cand WT_CAND --rows "Code:fling,Code:type"
    python3 tools/input_gate.py sentinel --board p4 --dev WT_DEV
    python3 tools/input_gate.py baseline --board tdeck,p4 --dev WT_DEV [--only ...] [--fresh]
    python3 tools/input_gate.py run --board p4 --image dev=WT_DEV --image ui1=WT_CAND \\
        --out DIR --runs 2 --alternate
    python3 tools/input_gate.py compare DIR [--base dev] [--cand ui1] [--board p4] [--tol MS]

The rule it applies is the owner's: a surface moves only when every row of it
holds its p90 on every board, against same-day dev on fresh boots. A row is
one surface under one input; its number is the row's `gate` p90 (input_bench's
presented interval over the live, coast and glide frames), and a side's number
is the MEDIAN over its runs. The line is dev's number plus `--tol` (0: the
candidate's p90 may not exceed dev's).

    pass   at or under the line
    near   over it by 1 ms (`--near`) or less, or noisy: a side with fewer
           than three runs whose p90s spread more than 2 ms (`--noise`). A
           third run's median settles a noisy row; a near row is the one to
           re-run, and one still near after it does not hold
    fail   more than 1 ms over it

A surface's verdict is its worst row's, a gate's per surface is its worst
board's. A side with three runs or more whose p90s still spread more than
twice `--noise` is marked unstable: its median decides, and the note says the
row has two modes (a collection, a whole frame) a median cannot average.

**Runs.** Each run is a fresh flash of a worktree's built image (`tools/board.py
BOARD flash`, run in that worktree, so its own dist and board.toml), then one
input_bench run into `DIR/LABEL_BOARD_N`, with a `meta.json` of the image's
commit and sha256, the selection, the rows it asked for and the seconds each
step took. input_bench itself runs from this checkout (`--bench` names another),
so both images are timed by one bench. Boards run in parallel, one thread
each.

**Baselines.** A board's dev runs are kept under `--store` (default
`~/.cache/moybyte/input_gate`, or MOY_INPUT_GATE_STORE), keyed by dev's commit
(`-dirty` when the worktree has tracked changes), the board and the local
date. A later gate against the same dev commit on the same day reuses them for
every row they cover with `--runs` runs, and benches dev only for the rest.
Before it reuses one, a SENTINEL flashes dev and plays a few of the
baseline's steadiest rows (three surfaces' least spread rows, or
`--sentinel-rows`): when their median change passes `--drift` ms (1.0) the
board no longer measures what the baseline did (a P4 slows with run order),
and the gate stops and asks for `--fresh-baseline`, which retires the stored
runs and benches dev beside the candidate, alternating.

**The third run.** After the runs, the near rows are played once more, alone
(`input_bench --rows`): the candidate, and dev where its side of the row has
fewer than three runs (that dev run joins the baseline).

**Like-for-like.** Where a row's probe reads on one image and not the other
(dev's Text console has no scroll probe), the two images class the frames
differently: unprobed, every painted frame after a release is `coast`;
probed, only the frames that moved are. The row says so and adds a second
number, both images' frames re-classed with the probe ignored. The gated
number and its verdict stay the bench's.

**Iteration.** `iter` is one run of the rows given, compared with the stored
baseline: a fix's inner loop. It flashes the candidate first (`--no-flash`
benches the board as it is).

**The flash.** A cable flash writes the merged image (bootloader, partition
table, app) and erases otadata; the store's partition (`vfs`) or tail lies
past both OTA slots and survives it while the table is unchanged
(`tools/board_flash.py`). The app is all but the first 56 KiB (P4) or 128
KiB (T-Deck, mostly padding that compresses away) of the image, so an
app-only write would save about 1% of a flash's time, and lose the table
check that erases a moved store; there is no `--app-only`. The bench resets
its own Bench Scratch content (input_bench's cleanup).

Exit status: 0 every surface passes, 1 one fails or stays near, 3 a sentinel
drifted or a step failed.
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
import re
import shutil
import statistics
import subprocess
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import input_bench as ib  # noqa: E402

NEAR_MS = 1.0          # over the line by this or less: near
NOISE_MS = 2.0         # a side's run spread past this, under SURE_RUNS runs: noisy
SURE_RUNS = 3          # runs whose median settles a noisy side
DRIFT_MS = 1.0         # the sentinel's median change past this: a fresh baseline
SENTINEL_ROWS = 3
STORE_ENV = "MOY_INPUT_GATE_STORE"
STORE_DEFAULT = os.path.join("~", ".cache", "moybyte", "input_gate")
RANK = {"pass": 0, "near": 1, "missing": 2, "fail": 3}


def today():
    return datetime.date.today().isoformat()


# -- the images --------------------------------------------------------------------

def _git(wt, *args):
    return subprocess.check_output(["git", "-C", wt] + list(args), text=True).strip()


def _py(root):
    p = os.path.join(root, ".venv", "bin", "python")
    return p if os.path.exists(p) else sys.executable


class Image:
    """A worktree's built image for one board: where it is, what it hashes
    to, the commit it was built at, and the key its dev baseline is kept
    under."""

    def __init__(self, label, wt, board, need=True):
        import board_config
        import p4_autotest
        self.label = label
        self.wt = os.path.abspath(wt)
        self.board = board
        dirs = p4_autotest.board_dirs(self.wt)
        if board not in dirs:
            raise SystemExit("%s: no board %r in %s" % (label, board, self.wt))
        fl = board_config.load(dirs[board]).get("flash") or {}
        self.path = os.path.join(self.wt, fl["image"])
        self.commit = _git(self.wt, "rev-parse", "HEAD")
        self.dirty = bool(_git(self.wt, "status", "--porcelain", "--untracked-files=no"))
        self.key = self.commit[:12] + ("-dirty" if self.dirty else "")
        self.warnings = []
        self.mtime = self.sha256 = None
        if not os.path.exists(self.path):
            if not need:
                return
            raise SystemExit("%s: no image at %s -- build it (`tools/board.py %s flash "
                             "--build` in %s)" % (label, self.path, board, self.wt))
        self.mtime = os.path.getmtime(self.path)
        committed = int(_git(self.wt, "log", "-1", "--format=%ct"))
        if self.mtime < committed:
            self.warnings.append("%s's %s image is older than its HEAD %s: build it first"
                                 % (label, board, self.commit[:8]))
        if self.dirty:
            self.warnings.append("%s has uncommitted tracked changes: its runs are keyed %s"
                                 % (label, self.key))
        h = hashlib.sha256()
        with open(self.path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        self.sha256 = h.hexdigest()

    def meta(self):
        return {"label": self.label, "worktree": self.wt, "commit": self.commit,
                "dirty": self.dirty, "key": self.key, "image": self.path,
                "sha256": self.sha256,
                "built": self.mtime and datetime.datetime.fromtimestamp(self.mtime).isoformat(
                    timespec="seconds")}


# -- the runs ----------------------------------------------------------------------

class Sel:
    """A run's selection, as input_bench takes it."""

    def __init__(self, only="", inputs="", rows=None):
        self.only = only or ""
        self.inputs = inputs or ""
        self.rows = rows or {}

    @classmethod
    def of_rows(cls, keys):
        rows = {}
        for s, i in keys:
            rows.setdefault(s, []).append(i)
        return cls(rows=rows)

    def args(self):
        a = []
        if self.only:
            a += ["--only", self.only]
        if self.inputs:
            a += ["--inputs", self.inputs]
        if self.rows:
            a += ["--rows", ",".join("%s:%s" % (s, i) for s, ins in self.rows.items()
                                     for i in ins)]
        return a

    def planned(self, board, windowed):
        only = [x.strip().lower() for x in self.only.split(",") if x.strip()]
        inputs = [x.strip() for x in self.inputs.split(",") if x.strip()]
        return ib.planned(board, windowed, only, inputs, self.rows or None)

    def text(self):
        return " ".join(self.args()) or "every row"


_say_lock = threading.Lock()


def say(board, msg):
    with _say_lock:
        print("%s [%s] %s" % (time.strftime("%H:%M:%S"), board, msg), flush=True)


def next_n(d, label, board):
    pat = re.compile(r"^%s_%s_(\d+)$" % (re.escape(label), re.escape(board)))
    ns = [int(m.group(1)) for m in map(pat.match, os.listdir(d) if os.path.isdir(d) else ())
          if m]
    return max(ns, default=0) + 1


def flash(board, wt, log):
    """`tools/board.py BOARD flash` in the worktree: its image, its toml."""
    with open(log, "w") as f:
        return subprocess.call([_py(wt), os.path.join(wt, "tools", "board.py"), board, "flash"],
                               cwd=wt, stdout=f, stderr=subprocess.STDOUT)


def bench(board, out, sel, bench_root, log):
    with open(log, "w") as f:
        return subprocess.call([_py(bench_root), os.path.join(bench_root, "tools",
                                                              "input_bench.py"),
                                "--board", board, "--out", out] + sel.args(),
                               cwd=bench_root, stdout=f, stderr=subprocess.STDOUT)


class StepFailed(RuntimeError):
    pass


def run_one(board, image, out, sel, bench_root, do_flash=True, label=None):
    """A fresh flash of `image`, then one bench of `sel`: DIR/LABEL_BOARD_N."""
    label = label or image.label
    os.makedirs(out, exist_ok=True)
    d = os.path.join(out, "%s_%s_%d" % (label, board, next_n(out, label, board)))
    os.makedirs(d)
    meta = dict(image.meta(), board=board, date=today(), selection=sel.args(),
                started=datetime.datetime.now().isoformat(timespec="seconds"))
    name = os.path.basename(d)
    if do_flash:
        t = time.time()
        say(board, "%s: flashing %s (%s)" % (name, image.label, image.commit[:8]))
        rc = flash(board, image.wt, os.path.join(d, "flash.log"))
        meta["flash_s"] = round(time.time() - t)
        if rc:
            meta["failed"] = "flash"
            _write_meta(d, meta)
            raise StepFailed("%s: the flash failed (%s)" % (name, os.path.join(d, "flash.log")))
    t = time.time()
    say(board, "%s: benching %s" % (name, sel.text()))
    rc = bench(board, d, sel, bench_root, os.path.join(d, "bench.log"))
    meta["bench_s"] = round(time.time() - t)
    rows = load_run(d, board)
    windowed = any(r.get("windowed") for r in rows.values())
    meta["windowed"] = windowed
    meta["requested"] = [list(k) for k in sel.planned(board, windowed)]
    if rc or not rows:
        meta["failed"] = "bench"
        _write_meta(d, meta)
        raise StepFailed("%s: the bench failed (%s)" % (name, os.path.join(d, "bench.log")))
    _write_meta(d, meta)
    say(board, "%s: %d rows (flash %ss, bench %ss)" % (name, len(rows), meta.get("flash_s", 0),
                                                      meta["bench_s"]))
    return d


def _write_meta(d, meta):
    with open(os.path.join(d, "meta.json"), "w") as f:
        json.dump(meta, f, indent=1)


def read_meta(d):
    try:
        with open(os.path.join(d, "meta.json")) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def parallel(boards, fn):
    """fn(board) on a thread per board; {board: result or the exception}."""
    out = {}

    def go(b):
        try:
            out[b] = fn(b)
        except (StepFailed, SystemExit) as e:
            say(b, "stopped: %s" % e)
            out[b] = e
        except Exception as e:  # noqa: BLE001 -- one board's failure, said; the others go on
            say(b, "stopped: %s: %s" % (type(e).__name__, e))
            out[b] = e

    ts = [threading.Thread(target=go, args=(b,)) for b in boards]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    return out


# -- the baselines -----------------------------------------------------------------

class Store:
    """Dev runs, ROOT/KEY/BOARD/DATE/dev_BOARD_N, each a run dir as `run_one`
    makes it."""

    def __init__(self, root=None):
        self.root = os.path.expanduser(root or os.environ.get(STORE_ENV) or STORE_DEFAULT)

    def dir(self, key, board, day):
        return os.path.join(self.root, key, board, day)

    def runs(self, key, board, day):
        d = self.dir(key, board, day)
        pat = re.compile(r"^dev_%s_(\d+)$" % re.escape(board))
        found = []
        for name in os.listdir(d) if os.path.isdir(d) else ():
            m = pat.match(name)
            if m and os.path.exists(os.path.join(d, name, "%s_input.json" % board)):
                found.append((int(m.group(1)), os.path.join(d, name)))
        return [p for _n, p in sorted(found)]

    def add(self, key, board, day, run_dir):
        d = self.dir(key, board, day)
        os.makedirs(d, exist_ok=True)
        dst = os.path.join(d, "dev_%s_%d" % (board, next_n(d, "dev", board)))
        shutil.copytree(run_dir, dst)
        return dst

    def retire(self, key, board, day):
        """A fresh baseline: the stored runs move under `retired_HHMMSS/`."""
        runs = self.runs(key, board, day)
        if runs:
            dst = os.path.join(self.dir(key, board, day), time.strftime("retired_%H%M%S"))
            os.makedirs(dst)
            for r in runs:
                shutil.move(r, dst)
        return len(runs)

    def keys(self, board, day):
        """The keys with runs for the board that day, the newest first."""
        out = []
        for key in os.listdir(self.root) if os.path.isdir(self.root) else ():
            runs = self.runs(key, board, day)
            if runs:
                out.append((max(os.path.getmtime(r) for r in runs), key))
        return [k for _t, k in sorted(out, reverse=True)]

    def scratch(self, what):
        return os.path.join(self.root, "scratch", today(), what)


def coverage(run_dirs, board):
    """{row: how many of the runs asked for it}: their meta's `requested`,
    else the rows each run holds."""
    n = {}
    for d in run_dirs:
        req = read_meta(d).get("requested")
        keys = [tuple(k) for k in req] if req is not None else list(load_run(d, board))
        for k in keys:
            n[k] = n.get(k, 0) + 1
    return n


def windowed_of(run_dirs, board):
    for d in run_dirs:
        m = read_meta(d)
        if "windowed" in m:
            return m["windowed"]
        rows = load_run(d, board)
        if rows:
            return any(r.get("windowed") for r in rows.values())
    return None


def sentinel_rows(base_runs, keys, k=SENTINEL_ROWS):
    """The baseline's steadiest rows, one per surface: every run has a
    number, the least spread first."""
    ranked = []
    for key in keys:
        vals = [value(r[key]) for r in base_runs if key in r]
        if len(vals) == len(base_runs) and vals and None not in vals:
            ranked.append((max(vals) - min(vals), key))
    out, seen = [], set()
    for _spread, key in sorted(ranked):
        if key[0] not in seen:
            out.append(key)
            seen.add(key[0])
        if len(out) == k:
            break
    return out


def drift(base_runs, run, keys):
    """[(row, baseline median, today, change)] and the changes' median."""
    rows = []
    for key in keys:
        vals = [value(r[key]) for r in base_runs if key in r and value(r[key]) is not None]
        now = value(run.get(key)) if key in run else None
        if vals and now is not None:
            b = round(statistics.median(vals), 1)
            rows.append((key, b, now, round(now - b, 1)))
    med = round(statistics.median([r[3] for r in rows]), 1) if rows else None
    return rows, med


# -- the compare -------------------------------------------------------------------

def load_run(d, board):
    """{(surface, input): row} of a run dir's BOARD_input.json, in the order
    the bench played them."""
    path = os.path.join(d, "%s_input.json" % board)
    if not os.path.exists(path):
        return {}
    with open(path) as f:
        rows = json.load(f)
    return dict(((r["surface"], r["input"]), r) for r in rows if r.get("input") != "split")


def value(row):
    return ((row or {}).get("gate") or {}).get("p90")


def _med(vals):
    return round(statistics.median(vals), 1)


def verdict(base, cand, tol=0.0, near=NEAR_MS, noise=NOISE_MS):
    """A row's verdict from each side's per-run p90s (None: no number)."""
    base = [v for v in base if v is not None]
    cand = [v for v in cand if v is not None]
    if not base or not cand:
        return {"verdict": "missing", "base": _med(base) if base else None,
                "cand": _med(cand) if cand else None, "delta": None, "noisy": []}
    b, c = _med(base), _med(cand)
    d = round(c - (b + tol), 1)
    noisy = [side for side, v in (("base", base), ("cand", cand))
             if len(v) < SURE_RUNS and max(v) - min(v) > noise + 1e-9]
    if noisy or 0 < d <= near + 1e-9:
        v = "near"
    else:
        v = "fail" if d > 0 else "pass"
    unstable = [side for side, v_ in (("base", base), ("cand", cand))
                if len(v_) >= SURE_RUNS and max(v_) - min(v_) > 2 * noise + 1e-9]
    return {"verdict": v, "base": b, "cand": c, "delta": d, "noisy": noisy,
            "unstable": unstable}


def probed(row):
    """Whether the row's probe read anything (None: no frames kept)."""
    raw = (row or {}).get("raw")
    if not raw:
        return None
    k, n = ib.FIELDS.index("probe"), len(ib.FIELDS)
    return any(raw[i * n + k] != ib.NONE_PROBE for i in range(len(raw) // n))


def lfl_p90(row):
    """The row's gate p90 with its frames re-classed as if no probe read:
    after a release every painted frame coasts, after a step only the first
    frame is live."""
    raw = (row or {}).get("raw")
    if not raw:
        return None
    fr = ib.frames_of(raw)
    for f in fr:
        f["probe"] = ib.NONE_PROBE
    pres = ib.presented(fr, row.get("tail"))
    cl = ib.classes(fr, pres, [tuple(m) for m in row["plan"]["marks"]])
    st = ib._st([c[2] for c in cl if c[1] in ("live", "coast", "glide")])
    return st["p90"] if st else None


def compare(base_runs, cand_runs, tol=0.0, near=NEAR_MS, noise=NOISE_MS, keys=None):
    """Each row the candidate played (or `keys`), both sides' runs, the
    verdict, and like-for-like where the probe reads on one side only."""
    if keys is None:
        keys = []
        for r in cand_runs:
            keys += [k for k in r if k not in keys]
    out = []
    for key in keys:
        b_rows = [r[key] for r in base_runs if key in r]
        c_rows = [r[key] for r in cand_runs if key in r]
        row = {"surface": key[0], "input": key[1],
               "base_runs": [value(r) for r in b_rows], "cand_runs": [value(r) for r in c_rows]}
        row.update(verdict(row["base_runs"], row["cand_runs"], tol, near, noise))
        bp = [p for p in map(probed, b_rows) if p is not None]
        cp = [p for p in map(probed, c_rows) if p is not None]
        if bp and cp and any(bp) != any(cp):
            row["probe"] = {"base": any(bp), "cand": any(cp)}
            lb, lc = [lfl_p90(r) for r in b_rows], [lfl_p90(r) for r in c_rows]
            row["lfl"] = dict(verdict(lb, lc, tol, near, noise), base_runs=lb, cand_runs=lc)
        out.append(row)
    return out


def groups(rows):
    """{surface: its worst row's verdict}, in the rows' order."""
    out = {}
    for r in rows:
        v = r["verdict"]
        if RANK[v] >= RANK.get(out.get(r["surface"]), -1):
            out[r["surface"]] = v
    return out


def worst(verdicts):
    vs = [v for v in verdicts if v]
    return max(vs, key=RANK.get) if vs else None


def _runs_txt(vals):
    return " ".join("-" if v is None else "%.1f" % v for v in vals)


def _num(v):
    return "-" if v is None else "%.1f" % v


def _delta(v):
    return "-" if v is None else "%+.1f" % v


def render(board, rows, base_label, cand_label, tol=0.0, head=None):
    """A board's table: the runs, the medians, Δ, the verdict; then each
    surface's verdict."""
    nb = max((len(r["base_runs"]) for r in rows), default=0)
    nc = max((len(r["cand_runs"]) for r in rows), default=0)
    out = ["%s: %s (%d runs) against %s (%d runs); a row holds at %s p90 <= %s p90%s"
           % (board, cand_label, nc, base_label, nb, cand_label, base_label,
              " + %g ms" % tol if tol else "")]
    if head:
        out += head
    cols = ("surface", "input", "%s runs" % base_label, "%s runs" % cand_label,
            base_label, cand_label, "Δ", "verdict", "")
    table = []
    for r in rows:
        note = []
        if r["noisy"]:
            note.append("noisy: " + ", ".join(base_label if s == "base" else cand_label
                                              for s in r["noisy"]))
        if r.get("unstable"):
            note.append("unstable: " + ", ".join(base_label if s == "base" else cand_label
                                                 for s in r["unstable"]))
        if "probe" in r:
            lf = r["lfl"]
            note.append("probe reads on %s only; like-for-like %s -> %s (%s %s)" % (
                cand_label if r["probe"]["cand"] else base_label, _num(lf["base"]),
                _num(lf["cand"]), _delta(lf["delta"]), lf["verdict"]))
        table.append((r["surface"], r["input"], _runs_txt(r["base_runs"]),
                      _runs_txt(r["cand_runs"]), _num(r["base"]), _num(r["cand"]),
                      _delta(r["delta"]), r["verdict"], "; ".join(note)))
    w = [max(len(c[i]) for c in [cols] + table) for i in range(len(cols))]
    right = (4, 5, 6)
    for line in [cols] + table:
        out.append("  " + "  ".join((c.rjust(w[i]) if i in right else c.ljust(w[i]))
                                    for i, c in enumerate(line)).rstrip())
    g = groups(rows)
    out.append("  surfaces: " + " | ".join("%s %s" % (s, v) for s, v in g.items()))
    return "\n".join(out)


def summary(per_board):
    """A surface's gate over the boards: {surface: {board: verdict}}."""
    surfaces = []
    for rows in per_board.values():
        surfaces += [s for s in groups(rows) if s not in surfaces]
    bs = list(per_board)
    out = ["gate: a surface moves when it passes on every board that has it"]
    w = max([len(s) for s in surfaces] + [7])
    out.append("  %s  %s  verdict" % ("surface".ljust(w), "  ".join(b.ljust(7) for b in bs)))
    for s in surfaces:
        vs = [groups(per_board[b]).get(s) for b in bs]
        out.append("  %s  %s  %s" % (s.ljust(w), "  ".join((v or "-").ljust(7) for v in vs),
                                     worst(vs)))
    return "\n".join(out)


def near_rows(rows):
    return [(r["surface"], r["input"]) for r in rows if r["verdict"] == "near"]


# -- one board's gate --------------------------------------------------------------

class Ctx:
    def __init__(self, a):
        self.a = a
        self.store = Store(a.store)
        self.sel = Sel(a.only, a.inputs, ib.parse_rows(getattr(a, "rows", "") or ""))
        self.bench_root = os.path.abspath(a.bench or ROOT)
        self.day = today()


def gate_board(ctx, board):
    a = ctx.a
    dev = Image("dev", a.dev, board)
    cand = Image(a.cand_label or os.path.basename(os.path.abspath(a.cand)), a.cand, board)
    for w in dev.warnings + cand.warnings:
        say(board, "! " + w)
    out = a.out
    store, key, day = ctx.store, dev.key, ctx.day
    head = ["  dev %s (image %s), %s %s; selection: %s" % (dev.commit[:8], dev.sha256[:10], cand.label,
                                                     cand.commit[:8], ctx.sel.text())]
    if a.fresh_baseline:
        n = store.retire(key, board, day)
        if n:
            say(board, "retired %d stored dev runs" % n)
    stored = store.runs(key, board, day)
    windowed = windowed_of(stored, board)
    want = ctx.sel.planned(board, windowed) if windowed is not None else None
    have = coverage(stored, board)
    covered = [k for k in want or () if have.get(k, 0) >= a.runs]
    missing = None if want is None else [k for k in want if have.get(k, 0) < a.runs]
    # the sentinel: does the stored baseline still hold on today's board?
    if covered:
        base_runs = [load_run(d, board) for d in stored]
        keys = (list(ib.planned(board, windowed, (), (), ib.parse_rows(a.sentinel_rows)))
                if a.sentinel_rows else sentinel_rows(base_runs, covered))
        d = run_one(board, dev, out, Sel.of_rows(keys), ctx.bench_root, label="sentinel")
        rows, med = drift(base_runs, load_run(d, board), keys)
        for k, b, now, ch in rows:
            say(board, "sentinel %s / %s: stored %.1f, today %.1f (%+.1f)" % (k + (b, now, ch)))
        head.append("  sentinel: %s median %+.1f ms over %d rows (limit %g)" % (
            "drifted," if med is None or abs(med) > a.drift else "holds,",
            med or 0.0, len(rows), a.drift))
        if med is None or abs(med) > a.drift:
            raise StepFailed("the stored dev baseline (%s, %s) drifted %s ms on today's %s: "
                             "run again with --fresh-baseline" % (key, day, med, board))
        say(board, "reusing %d stored dev runs for %d rows" % (len(stored), len(covered)))
    # the runs: dev where the baseline lacks rows (alternating), the candidate's always
    steps = []
    for i in range(a.runs):
        if missing is None:
            steps.append(("dev", ctx.sel))
        elif missing:
            steps.append(("dev", Sel.of_rows(missing)))
        steps.append(("cand", ctx.sel))
    cand_dirs = run_steps(board, steps, dev, cand, ctx)
    # the third run: the near rows alone
    rows = _gate_compare(ctx, board, key, cand_dirs)
    near = near_rows(rows)
    if near and not a.no_third:
        stored = store.runs(key, board, day)
        cov = coverage(stored, board)
        steps = []
        dev_rows = [k for k in near if cov.get(k, 0) < SURE_RUNS]
        if dev_rows:
            steps.append(("dev", Sel.of_rows(dev_rows)))
        cand_n = coverage(cand_dirs, board)
        cand_rows = [k for k in near if cand_n.get(k, 0) < SURE_RUNS]
        if cand_rows:
            steps.append(("cand", Sel.of_rows(cand_rows)))
        if steps:
            say(board, "third run: %d near rows" % len(near))
            cand_dirs += run_steps(board, steps, dev, cand, ctx)
            rows = _gate_compare(ctx, board, key, cand_dirs)
    return {"rows": rows, "head": head, "cand": cand.label}


def run_steps(board, steps, dev, cand, ctx):
    """Each (image, selection) in order; dev runs join the store. The
    candidate's run dirs."""
    out = []
    for which, sel in steps:
        img = dev if which == "dev" else cand
        d = run_one(board, img, ctx.a.out, sel, ctx.bench_root)
        if which == "dev":
            ctx.store.add(dev.key, board, ctx.day, d)
        else:
            out.append(d)
    return out


def _gate_compare(ctx, board, key, cand_dirs):
    base = [load_run(d, board) for d in ctx.store.runs(key, board, ctx.day)]
    cand = [load_run(d, board) for d in cand_dirs]
    return compare(base, cand, ctx.a.tol, ctx.a.near, ctx.a.noise)


# -- the CLI -----------------------------------------------------------------------

def _boards(s):
    return [b.strip() for b in s.split(",") if b.strip()]


def discover(d, board):
    """{label: [run dir, ...]} under a round's dir, by `LABEL_BOARD_N`."""
    pat = re.compile(r"^(.+)_%s_(\d+)$" % re.escape(board))
    out = {}
    for name in sorted(os.listdir(d)):
        m = pat.match(name)
        if m and os.path.exists(os.path.join(d, name, "%s_input.json" % board)):
            out.setdefault(m.group(1), []).append((int(m.group(2)), os.path.join(d, name)))
    return dict((k, [p for _n, p in sorted(v)]) for k, v in out.items())


def found_boards(d):
    """The boards a round's run dirs hold rows for: LABEL_BOARD_N/BOARD_input.json."""
    out = set()
    for name in os.listdir(d):
        if not os.path.isdir(os.path.join(d, name)):
            continue
        for f in os.listdir(os.path.join(d, name)):
            board = f[:-len("_input.json")]
            if f.endswith("_input.json") and re.match(r"^.+_%s_\d+$" % re.escape(board), name):
                out.add(board)
    return sorted(out)


def _pick(found, spec, what):
    """`LABEL` or `LABEL:N,N` from a round's labels."""
    label, _, ns = spec.partition(":")
    if label not in found:
        raise SystemExit("no %s runs labelled %r (found: %s)" % (what, label,
                                                                 ", ".join(found) or "none"))
    dirs = found[label]
    if ns:
        keep = set(int(n) for n in ns.split(","))
        dirs = [p for p in dirs if int(p.rsplit("_", 1)[1]) in keep]
    return label, dirs


def cmd_compare(a):
    per_board, texts = {}, []
    boards = _boards(a.board) if a.board else found_boards(a.dir)
    for board in boards:
        found = discover(a.dir, board)
        if a.base.split(":")[0] == "store":
            st = Store(a.store)
            key = a.base.partition(":")[2] or (st.keys(board, today()) or [None])[0]
            if not key:
                raise SystemExit("%s: no stored baseline today under %s" % (board, st.root))
            blabel, bdirs = "dev", st.runs(key, board, today())
        else:
            blabel, bdirs = _pick(found, a.base, "base")
        cands = [k for k in found if k != blabel and k != "sentinel"]
        if not a.cand and len(cands) != 1:
            raise SystemExit("%s: name the candidate with --cand (found: %s)" % (
                board, ", ".join(cands)))
        clabel, cdirs = _pick(found, a.cand or cands[0], "candidate")
        rows = compare([load_run(d, board) for d in bdirs], [load_run(d, board) for d in cdirs],
                       a.tol, a.near, a.noise)
        per_board[board] = rows
        texts.append(render(board, rows, blabel, clabel, a.tol, head=[
            "  %s: %s" % (blabel, ", ".join(os.path.basename(d) for d in bdirs)),
            "  %s: %s" % (clabel, ", ".join(os.path.basename(d) for d in cdirs))]))
    print("\n\n".join(texts + [summary(per_board)]))
    if a.json:
        with open(a.json, "w") as f:
            json.dump(per_board, f, indent=1, default=list)
    return _status(per_board)


def _status(per_board):
    vs = [v for rows in per_board.values() for v in groups(rows).values()]
    return 0 if vs and all(v == "pass" for v in vs) else 1


def cmd_run(a):
    ctx = Ctx(a)
    images = []
    for spec in a.image:
        label, sep, wt = spec.partition("=")
        if not sep:
            raise SystemExit("--image LABEL=WORKTREE, not %r" % spec)
        images.append((label, wt))
    labels = [x[0] for x in images]
    if a.order:
        order = [x.strip() for x in a.order.split(",")]
        unknown = [x for x in order if x not in labels]
        if unknown:
            raise SystemExit("--order names %s, not an --image" % ", ".join(unknown))
    elif a.alternate:
        order = labels * a.runs
    else:
        order = [x for x in labels for _ in range(a.runs)]

    def one(board):
        imgs = dict((label, Image(label, wt, board, need=not a.no_flash))
                    for label, wt in images)
        for img in imgs.values():
            for w in img.warnings:
                say(board, "! " + w)
        return [run_one(board, imgs[label], a.out, ctx.sel, ctx.bench_root,
                        do_flash=not a.no_flash) for label in order]

    res = parallel(_boards(a.board), one)
    return 3 if any(isinstance(v, BaseException) for v in res.values()) else 0


def cmd_baseline(a):
    ctx = Ctx(a)

    def one(board):
        dev = Image("dev", a.dev, board)
        for w in dev.warnings:
            say(board, "! " + w)
        if a.fresh:
            say(board, "retired %d stored dev runs" % ctx.store.retire(dev.key, board, ctx.day))
        out = ctx.store.scratch("baseline")
        for _ in range(a.runs):
            d = run_one(board, dev, out, ctx.sel, ctx.bench_root)
            say(board, "stored as %s" % ctx.store.add(dev.key, board, ctx.day, d))
            shutil.rmtree(d)

    res = parallel(_boards(a.board), one)
    return 3 if any(isinstance(v, BaseException) for v in res.values()) else 0


def cmd_sentinel(a):
    ctx = Ctx(a)

    def one(board):
        dev = Image("dev", a.dev, board)
        stored = ctx.store.runs(dev.key, board, ctx.day)
        if not stored:
            raise StepFailed("no stored dev baseline for %s on %s today (%s)" % (
                dev.key, board, ctx.store.root))
        base = [load_run(d, board) for d in stored]
        keys = (list(ib.planned(board, windowed_of(stored, board), (), (),
                                ib.parse_rows(a.sentinel_rows)))
                if a.sentinel_rows else sentinel_rows(base, list(coverage(stored, board))))
        d = run_one(board, dev, ctx.store.scratch("sentinel"), Sel.of_rows(keys),
                    ctx.bench_root, label="sentinel")
        rows, med = drift(base, load_run(d, board), keys)
        for k, b, now, ch in rows:
            say(board, "%s / %s: stored %.1f, today %.1f (%+.1f)" % (k + (b, now, ch)))
        ok = med is not None and abs(med) <= a.drift
        say(board, "%s: median %s ms over %d rows (limit %g)%s" % (
            "holds" if ok else "DRIFTED", _delta(med), len(rows), a.drift,
            "" if ok else " -- make a fresh baseline: input_gate.py baseline --fresh"))
        return ok

    res = parallel(_boards(a.board), one)
    return 0 if all(v is True for v in res.values()) else 3


def cmd_iter(a):
    ctx = Ctx(a)
    per_board, texts = {}, []

    def one(board):
        cand = Image(a.cand_label or os.path.basename(os.path.abspath(a.cand)), a.cand, board,
                     need=not a.no_flash)
        if a.dev:
            key = Image("dev", a.dev, board, need=False).key
        elif a.commit:
            key = next((k for k in ctx.store.keys(board, ctx.day) if k.startswith(a.commit)),
                       a.commit)
        else:
            key = (ctx.store.keys(board, ctx.day) or [None])[0]
        stored = ctx.store.runs(key, board, ctx.day) if key else []
        if not stored:
            raise StepFailed("no stored dev baseline on %s today (%s): make one with "
                             "`input_gate.py baseline` or a gate" % (board, ctx.store.root))
        d = run_one(board, cand, a.out or ctx.store.scratch("iter"), ctx.sel, ctx.bench_root,
                    do_flash=not a.no_flash)
        keys = list(load_run(d, board))
        rows = compare([load_run(s, board) for s in stored], [load_run(d, board)],
                       a.tol, a.near, a.noise, keys=keys)
        per_board[board] = rows
        texts.append(render(board, rows, "dev", cand.label, a.tol, head=[
            "  dev %s: %d stored runs; %s: %s" % (key, len(stored), cand.label, d)]))

    res = parallel(_boards(a.board), one)
    if texts:
        print("\n\n".join(texts))
    if any(isinstance(v, BaseException) for v in res.values()):
        return 3
    return _status(per_board)


def cmd_gate(a):
    ctx = Ctx(a)
    os.makedirs(a.out, exist_ok=True)
    res = parallel(_boards(a.board), lambda b: gate_board(ctx, b))
    per_board, texts = {}, []
    for board, r in res.items():
        if isinstance(r, BaseException):
            texts.append("%s: stopped -- %s" % (board, r))
            continue
        per_board[board] = r["rows"]
        texts.append(render(board, r["rows"], "dev", r["cand"], a.tol, head=r["head"]))
    text = "\n\n".join(texts + ([summary(per_board)] if per_board else []))
    print("\n" + text)
    with open(os.path.join(a.out, "gate.txt"), "w") as f:
        f.write(text + "\n")
    with open(os.path.join(a.out, "gate.json"), "w") as f:
        json.dump(per_board, f, indent=1, default=list)
    if any(isinstance(v, BaseException) for v in res.values()):
        return 3
    return _status(per_board)


def parser():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    def common(p, sel=True, rule=True):
        p.add_argument("--store", help="the baselines' root (default %s, or $%s)"
                                       % (STORE_DEFAULT, STORE_ENV))
        if sel:
            p.add_argument("--board", required=True, help="boards, comma separated")
            p.add_argument("--only", default="", help="input_bench --only")
            p.add_argument("--inputs", default="", help="input_bench --inputs")
            p.add_argument("--rows", default="", help="input_bench --rows SURFACE:INPUT,...")
            p.add_argument("--bench", help="the checkout whose input_bench runs (default: this)")
        if rule:
            p.add_argument("--tol", type=float, default=0.0,
                           help="ms the candidate may exceed dev by (default 0)")
            p.add_argument("--near", type=float, default=NEAR_MS)
            p.add_argument("--noise", type=float, default=NOISE_MS)

    p = sub.add_parser("gate", help="a candidate against same-day dev on every board")
    common(p)
    p.add_argument("--dev", required=True, help="dev's worktree (its built image)")
    p.add_argument("--cand", required=True, help="the candidate's worktree")
    p.add_argument("--cand-label", help="its runs' label (default: the worktree's name)")
    p.add_argument("--out", required=True, help="the round's directory")
    p.add_argument("--runs", type=int, default=2)
    p.add_argument("--fresh-baseline", action="store_true",
                   help="retire today's stored dev runs and bench dev beside the candidate")
    p.add_argument("--no-third", action="store_true", help="no third run of the near rows")
    p.add_argument("--drift", type=float, default=DRIFT_MS)
    p.add_argument("--sentinel-rows", default="", help="SURFACE:INPUT,... (default: the "
                                                       "baseline's steadiest)")
    p.set_defaults(fn=cmd_gate)

    p = sub.add_parser("iter", help="one run of the rows given against the stored baseline")
    common(p)
    p.add_argument("--cand", required=True)
    p.add_argument("--cand-label")
    p.add_argument("--dev", help="dev's worktree: its commit picks the baseline")
    p.add_argument("--commit", help="the baseline's commit (prefix); default: today's newest")
    p.add_argument("--out", help="where the run lands (default: the store's scratch)")
    p.add_argument("--no-flash", action="store_true", help="bench the board as it is")
    p.set_defaults(fn=cmd_iter)

    p = sub.add_parser("sentinel", help="does today's board still measure the stored baseline")
    common(p, rule=False)
    p.add_argument("--dev", required=True)
    p.add_argument("--drift", type=float, default=DRIFT_MS)
    p.add_argument("--sentinel-rows", default="")
    p.set_defaults(fn=cmd_sentinel)

    p = sub.add_parser("baseline", help="bench dev into the store")
    common(p, rule=False)
    p.add_argument("--dev", required=True)
    p.add_argument("--runs", type=int, default=2)
    p.add_argument("--fresh", action="store_true", help="retire today's stored runs first")
    p.set_defaults(fn=cmd_baseline)

    p = sub.add_parser("run", help="flash and bench images in turn, into a round's directory")
    common(p, rule=False)
    p.add_argument("--image", action="append", required=True, help="LABEL=WORKTREE")
    p.add_argument("--out", required=True)
    p.add_argument("--runs", type=int, default=1, help="runs of each image")
    p.add_argument("--alternate", action="store_true", help="a, b, a, b rather than a, a, b, b")
    p.add_argument("--order", help="labels in the order to run them (overrides --runs)")
    p.add_argument("--no-flash", action="store_true", help="bench the board as it is")
    p.set_defaults(fn=cmd_run)

    p = sub.add_parser("compare", help="a round's runs: the table and the verdicts")
    common(p, sel=False)
    p.add_argument("dir", help="the round's directory (LABEL_BOARD_N run dirs)")
    p.add_argument("--board", help="boards, comma separated (default: every one found)")
    p.add_argument("--base", default="dev",
                   help="LABEL[:N,N] or store[:KEY] (today's stored baseline)")
    p.add_argument("--cand", help="LABEL[:N,N] (default: the one other label)")
    p.add_argument("--json", help="write the rows and verdicts here")
    p.set_defaults(fn=cmd_compare)
    return ap


def main(argv=None):
    a = parser().parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
