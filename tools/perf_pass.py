#!/usr/bin/env python3
# Map (grep -n a name to jump there):
#   -- the record           metric, record, history_path, append, history, previous
#   -- the comparison       band, compare, block
#   -- the measurement      measure (desk idle, heap, window drag, the roster, the compiled cart)
#   Stage                   one board's stage: its phases, metrics and errors
#   -- the command line     main (`tools/board.py perf-baseline` and the pass's perf stage)
"""A board pass's perf stage: one board's fixed measurement, recorded every
run and set beside the run before it.

    tools/board.py perf-baseline tdeck         measure the board as it is,
                                               record it, print the block
    python3 tools/perf_pass.py tdeck --port P --out FILE --image STAMP
                                               what `tools/board.py pass`
                                               runs after a board's suite

WHAT IT MEASURES, with the meters the `perf` skill names and nothing new:

    desk idle       drawn fps off the kernel loop's drawn-frame counter
                    (`pump.drawn`, PERF DIAG off) and the collector's stops
                    from `gc.pauses()` over the same windows; then, under
                    PERF DIAG, the mean work of a loop frame from the
                    kernel's LOOP line (`ms=`, the pacing sleep left out),
                    because an idle desk may draw no frame at all
    heap            the HEAPCAPS line at the idle desk: internal SRAM free and
                    its largest block, PSRAM free, the gc heap held and live
    window drag     on a windowed tier, the dev channel's `drag` (the top
                    window's title strip oscillated, a window opened for it
                    when none is) under PERF DIAG: drawn fps and frame ms
    the roster      `tools/p4_perf.py`'s DEFAULT_ROSTER, the carts #66 tracks
                    on every console board: each run from the launcher, its
                    drawn fps as `p4_perf.measure` takes it (PERF DIAG off,
                    the shipping number the ledger records), then PERF DIAG
                    switched on in the same run for its frame ms: LOOP's mean loop
                    frame work times PERF's loop frames per drawn frame, the
                    work behind one frame on the glass (a paced cart's
                    tick-only frames included), absent where the loop spins
                    between frames (a run in the kernel's front). A title
                    that is not a run tile on the board's home is not run:
                    `run` would start the first tile holding the name (Sakura
                    is a wallpaper, and `run Sakura` starts Sakura Lua)
    compiled cart   Jet Teapot (`tools/jet_cart.py`), built for the board's
                    chip, pushed, run uncapped as the suites' floor runs it,
                    measured as a roster cart and removed again; absent (with
                    the reason) when the OTA signing key is not on this machine

and leaves the board as it found it: no cart, diag, uncap and the FPS chip as
they were, the window it opened closed, the cart it pushed gone.

THE RECORD. One JSON object a run (`record`): the board, the date, the commit
the image was built from, the image (its stamp, sha256 and whether this pass
flashed it), the tree that measured, how long the stage took, the conditions
(WiFi holders) and each metric's value, unit, sample count and samples. It is
appended to `~/.cache/moybyte-perf/<board>.jsonl` (MOYBYTE_PERF_DIR moves the
directory), outside the repo because the numbers are this machine's boards',
and written to `--out`, the pass's log dir.

THE COMPARISON. Each metric beside the newest earlier record at a DIFFERENT
commit (a rerun of the same image compares with what came before it), with its
change in %. A change is flagged past the metric's noise band: the larger of
the unit's default (DEFAULT_BAND) and three times the samples' relative median
absolute deviation in either run, and only when it also moves by more than the
unit's absolute floor (MIN_MOVE), so a 3 ms frame cannot flag on a 10%
swing of 0.3 ms. The block is a report: the pass's exit code stays its suites'.

The committed numbers are `docs/perf/ledger.md`, which tools/perf_ledger.py
writes from these records when someone chooses; this tool measures and
remembers them for the next pass, it does not publish them.
"""

from __future__ import annotations

import argparse
import contextlib
import datetime
import hashlib
import io
import json
import os
import re
import shutil
import statistics
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, ROOT)

from radio_room import radio_room  # noqa: E402 -- HERE is on sys.path above

FORMAT = 1

COMPILED = "Jet Teapot"
COMPILED_FOLDER = "jet_teapot.moy"

# Seconds of drawn-frame counter a cart is read over (2 s windows), after
# p4_perf.measure's own 4 s settle; PERF samples taken under PERF DIAG after
# the first, which straddles the switch.
FPS_SECS = 6.0
PERF_SAMPLES = 2
IDLE_SECS = 6.0
DRAG_FRAMES = 300
# Loop frames per drawn frame past which a loop spins rather than paces.
SPIN_RATIO = 4.0

# The default noise band a unit's change must pass to be flagged, in %, and the
# least absolute move that can flag at all.
DEFAULT_BAND = {"fps": 5.0, "ms": 10.0, "us": 25.0, "KiB": 5.0, "/min": 25.0}
MIN_MOVE = {"fps": 1.0, "ms": 1.0, "us": 500.0, "KiB": 32.0, "/min": 2.0}
# How many MADs of spread a change must clear when the samples are noisier
# than the default band.
MAD_K = 3.0

HIGHER = "higher"
LOWER = "lower"


# -- the record ----------------------------------------------------------------


def metric(value, unit, better, samples=None, n=None):
    """One measurement: `value` None when the board could not take it (a
    lever it lacks is absent, never 0)."""
    samples = [round(float(s), 3) for s in (samples or ())
               if isinstance(s, (int, float))]
    return {"value": None if value is None else round(float(value), 2),
            "unit": unit, "better": better,
            "n": len(samples) if n is None else n, "samples": samples}


def git_head(root=ROOT):
    """This tree's commit, `+` when tracked files differ from it."""
    try:
        head = subprocess.run(["git", "-C", root, "rev-parse", "--short=8", "HEAD"],
                              capture_output=True, text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "?"
    dirty = subprocess.run(["git", "-C", root, "diff", "--quiet", "HEAD", "--"],
                           capture_output=True).returncode != 0
    return head + ("+" if dirty else "")


def image_facts(path):
    """(sha256 prefix, bytes) of the image file, (None, None) without one."""
    try:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        return h.hexdigest()[:12], os.path.getsize(path)
    except OSError:
        return None, None


def record(board, metrics, commit, image=None, secs=None, conditions=None,
           errors=None, date=None, tree=None):
    return {
        "format": FORMAT,
        "board": board,
        "date": date or datetime.datetime.now().isoformat(timespec="seconds"),
        "commit": commit or "?",
        "image": image or {},
        "tree": tree or git_head(),
        "secs": None if secs is None else round(secs, 1),
        "conditions": conditions or {},
        "metrics": metrics,
        "errors": errors or {},
    }


def history_dir():
    return os.environ.get("MOYBYTE_PERF_DIR") or os.path.join(
        os.path.expanduser("~"), ".cache", "moybyte-perf")


def history_path(board):
    return os.path.join(history_dir(), "%s.jsonl" % board)


def append(rec, path=None):
    path = path or history_path(rec["board"])
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a") as f:
        f.write(json.dumps(rec) + "\n")
    return path


def history(board, path=None):
    """Every record of `board`, oldest first; a line that does not parse is
    skipped."""
    path = path or history_path(board)
    out = []
    try:
        with open(path) as f:
            for line in f:
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                if isinstance(rec, dict) and rec.get("format") == FORMAT:
                    out.append(rec)
    except OSError:
        pass
    return out


def previous(records, rec):
    """The newest record before `rec` taken at a different commit, or None."""
    for r in reversed(records):
        if r is rec or r == rec:
            continue
        if r.get("commit") != rec.get("commit"):
            return r
    return None


# -- the comparison ------------------------------------------------------------


def rel_mad(samples):
    """The samples' median absolute deviation over their median's size, or
    None when there are too few samples (fewer than 3) to say."""
    s = [x for x in samples or () if isinstance(x, (int, float))]
    if len(s) < 3:
        return None
    med = statistics.median(s)
    if not med:
        return None
    return statistics.median(abs(x - med) for x in s) / abs(med)


def band(m, prev=None):
    """The % a change of metric `m` must pass to be flagged."""
    b = DEFAULT_BAND.get(m["unit"], 10.0)
    for x in (m, prev):
        r = rel_mad((x or {}).get("samples"))
        if r is not None:
            b = max(b, MAD_K * 100.0 * r)
    return b


def compare(rec, prev):
    """[(name, metric, previous value, change %, band %, flag)] in the
    record's order. `flag` is "WORSE", "better" or "" -- a move past the band
    and the unit's absolute floor, read by the metric's direction."""
    rows = []
    old = (prev or {}).get("metrics", {})
    for name, m in rec["metrics"].items():
        p = old.get(name)
        pv = None if p is None else p.get("value")
        v = m.get("value")
        change = flag = None
        bd = band(m, p)
        if v is not None and pv is not None:
            if pv:
                change = 100.0 * (v - pv) / abs(pv)
            moved = abs(v - pv) > MIN_MOVE.get(m["unit"], 0.0)
            if moved and (change is None or abs(change) > bd):
                up = v > pv
                flag = "better" if up == (m.get("better") == HIGHER) else "WORSE"
        rows.append((name, m, pv, change, bd, flag or ""))
    return rows


def _num(v, unit):
    if v is None:
        return "-"
    if unit in ("us", "KiB"):
        return "%d" % round(v)
    return "%.1f" % v


def block(rec, prev):
    """The perf block a pass prints under its table for one board."""
    head = "%s perf  (%s, image %s, %s)" % (
        rec["board"], rec.get("date", "?"), rec.get("commit", "?"),
        "stage %ss" % ("%.0f" % rec["secs"] if rec.get("secs") is not None else "?"))
    if prev is None:
        head += "  -- no earlier run at another commit: this is the baseline"
    else:
        head += "  vs %s (%s)" % (prev.get("commit", "?"), prev.get("date", "?"))
    rows = [("metric", "unit", "value", "n", "prev", "change", "band", "")]
    flagged = 0
    for name, m, pv, change, bd, flag in compare(rec, prev):
        if flag == "WORSE":
            flagged += 1
        rows.append((name, m["unit"], _num(m.get("value"), m["unit"]),
                     str(m.get("n", "")), _num(pv, m["unit"]),
                     "-" if change is None else "%+.1f%%" % change,
                     "%.0f%%" % bd,
                     {"WORSE": "<<< WORSE", "better": "better"}.get(flag, "")))
    widths = [max(len(r[i]) for r in rows) for i in range(len(rows[0]) - 1)]
    lines = [head]
    for r in rows:
        lines.append(("  " + "  ".join(c.rjust(w) if i in (2, 3, 4, 5, 6)
                                        else c.ljust(w)
                                        for i, (c, w) in enumerate(zip(r, widths)))
                      + "  " + r[-1]).rstrip())
    for name, why in sorted((rec.get("errors") or {}).items()):
        lines.append("  ! %s: %s" % (name, why))
    if flagged:
        lines.append("  %d metric%s moved WORSE past %s band"
                     % (flagged, "" if flagged == 1 else "s",
                        "its" if flagged == 1 else "their"))
    return "\n".join(lines)


# -- the measurement -----------------------------------------------------------


# One read between two frames: the board's clock at the top of this frame, the
# frames drawn before it, and the collector's (collections, pause us, longest
# us since the last read), None on a build without gc.pauses().
IDLE_READ = ("(pump.last, pump.drawn, "
             "getattr(__import__('gc'), 'pauses', lambda: None)())")

_WRAP = 1 << 32


def idle_metrics(reads):
    """{name: metric} from IDLE_READ reads taken across equal windows."""
    import p4_perf
    fps, coll, longest, secs = [], 0, [], 0.0
    have_gc = all(isinstance(r[2], (list, tuple)) for r in reads)
    for a, b in zip(reads, reads[1:]):
        f = p4_perf.window_fps(a, b)
        if f is not None:
            fps.append(f)
        secs += ((b[0] - a[0]) % p4_perf._TICKS_PERIOD) / 1000.0
        if have_gc:
            coll += (b[2][0] - a[2][0]) % _WRAP
            longest.append(b[2][2])
    out = {"desk idle fps": metric(statistics.median(fps) if fps else None,
                                   "fps", HIGHER, fps)}
    out["desk idle gc collections"] = metric(
        coll * 60.0 / secs if have_gc and secs else None, "/min", LOWER,
        n=len(longest))
    out["desk idle gc longest pause"] = metric(
        max(longest) if longest else None, "us", LOWER, longest)
    return out


def heap_metrics(line):
    """{name: metric} from a HEAPCAPS line (shell_bench.parse_heapcaps)."""
    import shell_bench
    hc = shell_bench.parse_heapcaps(line)

    def kib(key, i):
        v = hc.get(key)
        return metric(None if v is None or len(v) <= i else v[i] / 1024.0,
                      "KiB", HIGHER, n=1)
    out = {"heap sram free": kib("sram", 1),
           "heap sram largest": kib("sram", 2),
           "heap psram free": kib("psram", 1)}
    for name, i in (("heap gc held", 0), ("heap gc live", 1)):
        m = kib("gc", i)
        m["better"] = LOWER
        out[name] = m
    return out


LOOP_RE = re.compile(r"LOOP n=(\d+) ms=([\d.]+)")


def pair_loop(lines):
    """Each PERF line among `lines` parsed, with `loop_ms` -- the LOOP line
    the kernel writes after it for the same period: the mean work of a loop
    frame, the pacing sleep left out, in tenths of a ms -- or None where no
    LOOP line followed before the next PERF."""
    from runtime.perf_line import parse_perf
    out = []
    for ln in lines:
        p = parse_perf(ln)
        if p is not None:
            p["loop_ms"] = None
            out.append(p)
            continue
        m = LOOP_RE.search(ln)
        if m and out and out[-1]["loop_ms"] is None:
            out[-1]["loop_ms"] = float(m.group(2))
    return out


def loop_ms(s):
    """A sample's mean loop-frame work in ms: LOOP's, else PERF's `busy=`
    (whole ms)."""
    v = s.get("loop_ms")
    if v is None:
        v = s.get("busy")
    return v if isinstance(v, (int, float)) else None


def spins(s):
    """Whether the sample's loop spun between drawn frames instead of
    sleeping: a run in the kernel's front loops thousands of times a second
    with no pacing sleep, and LOOP's mean is then the spin's."""
    f = s.get("fps")
    return bool(f) and f[1] > SPIN_RATIO * max(f[0], 1.0)


def frame_ms(s):
    """The work behind one DRAWN frame in a sample, in ms: the loop frames'
    mean work times the loop frames a drawn frame took (a paced cart's
    tick-only frames and a game's divisor included). None for a sample that
    drew nothing or whose loop spun."""
    v = loop_ms(s)
    f = s.get("fps")
    if v is None or not f or not f[0] or spins(s):
        return None
    return v * f[1] / f[0]


def perf_metrics(samples, prefix):
    """{name: metric} of PERF samples: drawn fps and ms per drawn frame."""
    fps = [s["fps"][0] for s in samples if s.get("fps")]
    ms = [v for v in map(frame_ms, samples) if v is not None]
    return {
        "%s fps" % prefix: metric(statistics.median(fps) if fps else None,
                                  "fps", HIGHER, fps),
        "%s frame" % prefix: metric(statistics.median(ms) if ms else None,
                                    "ms", LOWER, ms),
    }


class Stage:
    """One board's measurement over an attached P4Board `b`."""

    def __init__(self, b, name, board_dir, log=print):
        self.b = b
        self.name = name
        self.board_dir = board_dir
        self.log = log
        self.metrics = {}
        self.errors = {}
        self.phases = {}

    def _phase(self, label, fn):
        t = time.monotonic()
        try:
            fn()
        except Exception as exc:  # noqa: BLE001 -- one phase's failure is its row
            self.errors[label] = str(exc).splitlines()[0][:160] if str(exc) else repr(exc)
            self.log("%s: %s" % (label, self.errors[label]))
        self.phases[label] = round(time.monotonic() - t, 1)

    def diag(self, on):
        self.b.cmd("diag %d" % (1 if on else 0), wait_for="REMOTE diag")
        if not on:
            # `diag` switches the FPS chip with it; a chip left on would be
            # drawn into every diag-off number after this one.
            self.b.pyexec("ws.show_fps = %r" % self.chip, strict=True)

    def perf_samples(self, cart, n=PERF_SAMPLES, since=None, until=None,
                     timeout=None):
        """PERF samples (pair_loop's, LOOP's work beside each) naming `cart`
        (None: the desk) seen after `since`, waiting for n+1 of them and
        their LOOP lines and dropping the first, which straddles the switch
        that armed the line."""
        from runtime.perf_line import slug
        b = self.b
        want = None if cart is None else slug(cart)
        n0 = len(b.lines) if since is None else since
        end = time.monotonic() + (timeout or (n + 2) * 2.5 + 2.0)

        def got():
            lines = b.lines[n0:until] if until is not None else b.lines[n0:]
            return [p for p in pair_loop(lines) if p.get("cart") == want]
        while until is None and time.monotonic() < end:
            have = got()
            if len(have) >= n + 1 and have[n]["loop_ms"] is not None:
                break
            b.drain(0.5)
        return got()[1:]

    # -- phases ----------------------------------------------------------

    def desk_idle(self):
        b = self.b
        b.drain(1.0)
        reads = [b.pyval(IDLE_READ, timeout=10.0, strict=True)]
        for _ in range(int(round(IDLE_SECS / 2.0))):
            b.drain(2.0)
            reads.append(b.pyval(IDLE_READ, timeout=10.0, strict=True))
        self.metrics.update(idle_metrics(reads))

    def heap(self):
        line = self.b.cmd("heapcaps", wait_for="HEAPCAPS", timeout=20.0)
        if line is None:
            raise RuntimeError("no HEAPCAPS reply")
        self.metrics.update(heap_metrics(line))

    def desk_loop(self):
        """The idle desk's loop frame: it may draw nothing, so its cost is
        the mean work of a loop frame, not of a drawn one."""
        n0 = len(self.b.lines)
        self.diag(True)
        try:
            ms = [v for v in map(loop_ms, self.perf_samples(None, since=n0))
                  if v is not None]
        finally:
            self.diag(False)
        self.metrics["desk idle loop"] = metric(
            statistics.median(ms) if ms else None, "ms", LOWER, ms)

    def drag(self):
        b = self.b
        st = b.state()
        opened = not [k for k in st.get("order") or () if k != "desktop"]
        if opened:
            b.open("settings")
            b.drain(2.0)
        self.diag(True)
        try:
            n0 = len(b.lines)
            line = b.cmd("drag %d 6" % DRAG_FRAMES, wait_for="REMOTE drag ",
                         timeout=15.0)
            if line is None or "no window" in line or "no kernel" in line:
                raise RuntimeError("the drag verb declined: %r" % (line,))
            if b.wait_line("drag done", 90.0) is None:
                raise RuntimeError("the drag never finished")
            done = len(b.lines)
            samples = self.perf_samples(None, since=n0, until=done)
            self.metrics.update(perf_metrics(samples, "window drag"))
            b.drain(0.5)
        finally:
            self.diag(False)
            if opened:
                b.open("settings")
                b.drain(1.0)

    def cart(self, title, label=None):
        with radio_room(title):
            self._cart(title, label)

    def _cart(self, title, label):
        import p4_perf
        b = self.b
        label = label or title
        try:
            r = p4_perf.measure(b, title, FPS_SECS, lambda *x: None)
            if r is None:
                raise RuntimeError("not on this board's shelf")
            if r["title"] != title:
                raise RuntimeError("`run %s` started %s" % (title, r["title"]))
            if r.get("linked") is not None:
                # A second console in the same two-player cart made it a
                # match on the shared tick: the match's numbers, not the cart's.
                raise RuntimeError("LINKED: a peer made it a match (net=%.0f), "
                                   "not measured" % r["linked"])
            fps = self._windows(r)
            self.metrics["%s fps" % label] = metric(r["fps"], "fps", HIGHER,
                                                    fps, n=r["n"])
            n0 = len(b.lines)
            self.diag(True)
            try:
                samples = self.perf_samples(r["title"], since=n0)
            finally:
                self.diag(False)
            self.metrics["%s frame" % label] = perf_metrics(samples, label)[
                "%s frame" % label]
            if samples and all(spins(s) for s in samples):
                self.errors["%s frame" % label] = (
                    "the loop spins between drawn frames (%d loop frames a "
                    "second): no frame cost to read off LOOP"
                    % samples[-1]["fps"][1])
        finally:
            b.leave_cart()

    @staticmethod
    def _windows(r):
        """The window fps a p4_perf result carries, else its median alone."""
        return r.get("windows") or [r["fps"]]

    def compiled(self):
        """Jet Teapot: built for this chip, pushed, measured uncapped, removed."""
        from tools import board_config, jet_cart, wasm_module
        if wasm_module.signing_key() is None:
            raise RuntimeError("no OTA signing key on this machine: a board "
                               "runs only modules signed with the key it trusts")
        chip = board_config.load(self.board_dir)["board"]["chip"]
        work = tempfile.mkdtemp(prefix="moy_perf_jet_")
        try:
            self._compiled(jet_cart.build(work, chips=(chip,)))
        finally:
            shutil.rmtree(work, ignore_errors=True)

    def _compiled(self, local):
        import push_cart
        from tools import board_config
        b = self.b
        had = {f for f, _m in (push_cart.store_folders(b) or ())}
        root = str(b.pyval("str(ws.carts_root)", timeout=20, strict=True))
        dest = root.rstrip("/") + "/" + COMPILED_FOLDER
        ser = board_config.load(self.board_dir)["serial"]
        names = push_cart.cart_files(local)
        link = push_cart.raw_link(b, ser)
        if not b.pyexec(push_cart.HELPERS):
            raise RuntimeError("could not install the upload helpers")
        t = time.monotonic()
        with contextlib.redirect_stdout(io.StringIO()):
            push_cart.push_files(b, {n: os.path.join(local, n) for n in names},
                                 names, dest, link)
        b.pyval("len(ws.carts.synced() or ())", timeout=60)
        b.pyval(push_cart.CONSENT % dest, timeout=60)
        self.phases["compiled push"] = round(time.monotonic() - t, 1)
        uncap_was = bool(b.pyval("getattr(ws, 'uncap', False)", timeout=10))
        try:
            b.cmd("uncap 1", wait_for="REMOTE uncap")
            self.cart(COMPILED, "%s uncapped" % COMPILED)
        finally:
            b.cmd("uncap %d" % uncap_was, wait_for="REMOTE uncap")
            if COMPILED_FOLDER not in had:
                push_cart.remove_folders(b, [COMPILED_FOLDER])
                b.pyval("len(ws.carts.synced() or ())", timeout=60)

    # -- the whole stage ---------------------------------------------------

    def run(self, roster, compiled=True):
        b = self.b
        st = b.state()
        if st.get("cart"):
            b.leave_cart()
            st = b.state()
        self.windowed = st.get("desk") is not None
        self.diag_was = bool(st.get("diag"))
        self.chip = bool(b.pyval("bool(ws.show_fps)", timeout=10, strict=True))
        self.conditions = {"wifi_held": st.get("wifi_held"),
                           "windowed": self.windowed}
        if self.diag_was:
            self.diag(False)
        try:
            self._phase("desk idle", self.desk_idle)
            self._phase("heap", self.heap)
            self._phase("desk idle loop", self.desk_loop)
            if self.windowed:
                self._phase("window drag", self.drag)
            # an image from before phase 9 has no ws.home: its console's own
            tiles = set(b.pyval("[c.get('title') for c in (getattr(getattr(ws, 'home', None), "
                                "'runnable_items', None) or ws._runnable_items)()]",
                                timeout=20, strict=True) or ())
            for title in roster:
                if title not in tiles:
                    self.errors[title] = ("not a run tile on this board's "
                                          "home: not measured")
                    continue
                self._phase(title, lambda t=title: self.cart(t))
            if compiled:
                self._phase(COMPILED, self.compiled)
        finally:
            if b.state().get("cart"):
                b.leave_cart()
            if self.diag_was:
                b.cmd("diag 1", wait_for="REMOTE diag")
            b.pyexec("ws.show_fps = %r" % self.chip)
        return self.metrics


# -- the command line ----------------------------------------------------------


def parser():
    ap = argparse.ArgumentParser(
        prog="tools/board.py perf-baseline",
        description="Measure one board's perf set, record it, print the "
                    "block beside the previous run.")
    import board
    ap.add_argument("board", choices=sorted(board.boards()),
                    help="the board id (the [board] ota id)")
    ap.add_argument("--port", help="its port (default: resolved)")
    ap.add_argument("--force", action="store_true",
                    help="open a port another process holds (your own stale one)")
    ap.add_argument("--out", help="also write the record to this file")
    ap.add_argument("--image", help="the commit the board's image was built "
                                    "from; required unless --flashed, since "
                                    "the image carries no commit")
    ap.add_argument("--flashed", action="store_true",
                    help="this pass flashed the image in dist/")
    ap.add_argument("--carts", help="comma-separated titles in place of the "
                                    "#66 roster")
    ap.add_argument("--no-compiled", action="store_true",
                    help="skip the compiled cart")
    ap.add_argument("--no-history", action="store_true",
                    help="write --out only, append nothing to the history")
    ap.add_argument("-v", "--verbose", action="store_true")
    return ap


def main(argv=None):
    a = parser().parse_args(argv)
    import board
    import board_pass
    import p4_perf
    dirs = board.boards()
    if a.board not in dirs or not board.is_console(dirs[a.board]):
        print("perf: %r is not a console board (%s)"
              % (a.board, ", ".join(sorted(n for n in dirs
                                           if board.is_console(dirs[n])))),
              file=sys.stderr)
        return 2
    d = dirs[a.board]
    if not a.flashed and not a.image:
        # The image carries no commit (tools/esp32_build_lib.sh stamps it beside
        # the build, not into it), so dist/ names what the board holds only
        # when this pass just flashed it.
        print("perf: say which image %s holds: --image COMMIT (dist/ is this "
              "tree's build, not necessarily the board's)" % a.board, file=sys.stderr)
        return 2
    image = board_pass.image_path(d)
    sha, size = image_facts(image) if a.flashed else (None, None)
    commit = a.image or board_pass.image_stamp(d)
    roster = ([t.strip() for t in a.carts.split(",") if t.strip()]
              if a.carts else list(p4_perf.DEFAULT_ROSTER))
    t0 = time.monotonic()
    try:
        b = board.attach(a.board, dirs, a.port, a.force,
                         log=(lambda s: print("  | " + s)) if a.verbose else None)
    except board.BoardError as exc:
        print(exc, file=sys.stderr)
        return 2
    try:
        if board.state_or_none(b) is None:
            print("perf: %s is not at its console" % a.board, file=sys.stderr)
            return 2
        stage = Stage(b, a.board, d, log=lambda s: print("perf: " + s, flush=True))
        stage.run(roster, compiled=not a.no_compiled)
    finally:
        b.close()
    rec = record(a.board, stage.metrics, commit,
                 image={"stamp": commit, "sha256": sha, "bytes": size,
                        "flashed": bool(a.flashed)},
                 secs=time.monotonic() - t0,
                 conditions=dict(stage.conditions, phases=stage.phases),
                 errors=stage.errors)
    if a.out:
        os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
        with open(a.out, "w") as f:
            json.dump(rec, f, indent=1)
    if not a.no_history:
        path = append(rec)
        print("perf: recorded in %s" % path)
    print(block(rec, previous(history(a.board), rec)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
