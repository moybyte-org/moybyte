#!/usr/bin/env python3
"""Attribute per-frame HEAP ALLOCATION -- and GC pauses -- to the code responsible.

Four modes, because "why did this frame take 100ms instead of 26" turned out to be
four separate questions, and answering one with the wrong mode gives a confident
wrong answer (this file's own history is the cautionary tale -- see DEAD ENDS):

  (default)   EXACT      who allocates, in bytes, per phase
  --cadence   CADENCE    how often a collect lands, and how big frames really are
  --natural   NATURAL    which phase a collect lands inside
  --pregrow N HEAP A/B   emulate a bigger MICROPY_GC_INITIAL_HEAP_SIZE, no rebuild

EXACT -- who allocates
----------------------
Two facts about MicroPython make exact attribution possible: there is NO
refcounting (mark-sweep only), so between collects `gc.mem_alloc()` is strictly
MONOTONIC and a delta is exactly "bytes allocated here"; and forcing
`gc.collect()` at frame START leaves the full free heap, so no collect can land
mid-frame to corrupt a delta. The tool asserts that rather than trusting it -- any
negative delta increments `_bad` and is reported.

Attribution is EXCLUSIVE ("self bytes") via a stack: a call books its own total
minus what its wrapped children reported, and adds its total to its parent's
accumulator. Nesting wrappers is therefore safe, and the column sums to the frame
total minus what is unwrapped (printed as "unattributed" -- how you learn your
wrap set is too shallow).

Bytes-per-call FINGERPRINTS the object kind: ~16B is a boxed float (REPR_A),
~24-40B a small tuple, ~64B+ a dict or a grown list. 4KB over 14 calls is building
containers; 4KB over 300 calls is boxing scalars.

Its numbers are RELATIVE ONLY. Each wrapped call adds ~32B of its own (measured
and printed as "instrumentation overhead"), so with ~100 wraps the reported frame
total runs ~2.5x the truth. Rank with EXACT; take absolute totals from CADENCE.

CADENCE -- how often, and how big
--------------------------------
Zero per-frame retention: fixed counters and preallocated histograms only. This
mode exists because the EXACT hook retains a tuple + two dicts per frame, which is
itself ~1KB/frame of live data -- enough to change the GC cadence it is measuring.
Reports collects per frame, garbage between them, and ms + KB histograms.

Usage
-----
  python tools/p4_alloc.py                        # settings + picker, default wraps
  python tools/p4_alloc.py --cadence --frames 5
  python tools/p4_alloc.py --drill                # + fine-grained settings wraps
  python tools/p4_alloc.py --extra "ws.layout::settings_panel::lay.panel"

An --extra/--drill spec is `EXPR::ATTR::TAG` with EXPR evaluated on the device
(`ws`/`wm` in scope), so anything reachable from the console can be wrapped
without editing this file.

DEAD ENDS (2026-07-26, P4 on glass -- do not re-derive these)
------------------------------------------------------------
What this tool actually established about the P4's ~100ms UI frames, including
four models it DISPROVED. Each was plausible and each was wrong:

  * "Collects fire every ~15 frames." An artifact of measuring three short
    gestures back to back. ONE continuous 200-frame gesture collects exactly
    TWICE -- on its first and last painted frame -- and not once in between.
    Gesture length is irrelevant: it is always 2.
  * "So allocate less." Halving the per-frame allocation (the _settings_rows
    memo: 4-5KB/frame -> 2-3KB) changed the collect count by ZERO. Worth doing on
    its own merits; it does not touch this.
  * "The heap is too small, so it collects instead of growing." gc.c:919 really
    does collect before growing, and the initial heap really is 56KB -- but by
    the time the desk is up the heap is 2MB with 1.4MB FREE, and --pregrow to 6MB
    changed nothing. Note `gc.mem_free()` adds `max_new_split` (potential PSRAM),
    so its 28MB is not headroom; `micropython.mem_info()` gives the real total.
  * "A big allocation trips it." With `gc.disable()` -- which makes gc_alloc grow
    instead of collect, so mem_alloc goes strictly monotonic -- the largest single
    allocation anywhere in the loop is 5.7KB. There is no big allocation. And the
    collects STILL happen, which means they are EXPLICIT `gc.collect()` calls
    (an explicit collect ignores the auto-collect flag) or not collects at all: a
    shrinking `gc_realloc` also drops mem_alloc.

Where it actually points: those two frames per gesture are FULL REPAINTS. The
device's own PERF line reads `wmw=83..95ms` on them versus ~26ms for a mid-drag
frame, and p4_hitch.py attributes them to the desk/content draw. The press edge
and the release edge each re-render what the frames between them manage to skip,
so this is a DAMAGE-TRACKING gap, not a GC problem. Layer buffers live OUTSIDE the
gc heap (moy_alloc), which is exactly why a 1.2MB layer shows up as ~0 bytes here
and why an allocation tool was the wrong instrument for it.

One trap worth keeping in mind when extending this: `run_desktop` calls
`ws.handle_input()` and `ws.handle_pointer()` OUTSIDE `ws.frame(dt)`, so a hook
that resets its accumulator at frame entry silently discards everything the input
path allocates -- which is where a press/release edge does its work. The reset
below happens at frame END for that reason.

THREE MORE (2026-07-27, the #107 celeste hunt -- each cost a probe cycle):

  * Do NOT drill a Lua cart by `moy_lua.register`-ing wrapped verbs. The extra
    per-upcall garbage tips the split heap into releasing+re-adding SEGMENTS
    mid-frame (the forced collect at frame start frees them), and `mem_alloc`
    then jumps by segment-sized phantoms booked to whatever wrapped call is
    open -- a NO-OP verb read as 430KB/call. Mute at the LUA level instead
    (`moy_lua.exec("map = function() end")`) and diff the frame total.
  * A one-line `py` command runs in a FRESH env (see pyexec's docstring), so
    `py import moy_lua` + a later one-liner using it dies on a silent
    NameError -- and P4Board's default log swallows the PY ERR line. A whole
    bisect round returned baseline noise this way. Always send Lua execs
    through multi-line pyexec (the ws._g path), check its return value, and
    verify the mute took (a canary global + moy_lua.has()).
  * A sample window longer than ~2s must PUMP serial (drain, not sleep): the
    board's PERF prints fill the host-side buffer and the loop stalls on the
    blocking write, which reads as a fake 3fps slowdown.
"""

from __future__ import annotations

import argparse
import sys

sys.path.insert(0, __file__.rsplit("/", 1)[0])
from p4_autotest import P4Board            # noqa: E402


# -- device half ----------------------------------------------------------------

# EXCLUSIVE per-tag attribution. `_stk` holds one child-bytes accumulator per
# active wrapped call; a wrapper adds its OWN total to its parent's accumulator
# and books (total - children) to itself.
PROBE = """
import gc, time
ws._al = []
ws._rec = False
ws._cur = {}
ws._cnt = {}
ws._stk = []
ws._bad = 0
ws._badtags = []
ws._wrapped = set()
def _wrapa(obj, nm, tag):
    k = (id(obj), nm)
    if k in ws._wrapped:
        return
    f = getattr(obj, nm, None)
    if f is None:
        return
    def g(*a, **k2):
        if not ws._rec:
            return f(*a, **k2)
        m0 = gc.mem_alloc()
        ws._stk.append(0)
        try:
            return f(*a, **k2)
        finally:
            kids = ws._stk.pop()
            tot = gc.mem_alloc() - m0
            if tot < 0:
                # mem_alloc only ever DROPS across a collect (no refcounting),
                # so a negative delta names the phase the collect ran inside.
                ws._bad += 1
                ws._badtags.append(tag)
            ws._cur[tag] = ws._cur.get(tag, 0) + (tot - kids)
            ws._cnt[tag] = ws._cnt.get(tag, 0) + 1
            if ws._stk:
                ws._stk[-1] += tot
    ws._wrapped.add(k)
    setattr(obj, nm, g)
ws._wrapa = _wrapa
"""

# The wrapper itself allocates (the arg tuple for f(*a), the dict/list growth),
# and that lands in the wrapped tag's own column. Measure it once so a
# high-call-count tag can be read net of its own instrumentation.
CAL = """
class _T:
    def m(self, a, b):
        return None
ws._t = _T()
ws._wrapa(ws._t, 'm', '_cal')
def _cal():
    ws._cur = {}
    ws._cnt = {}
    ws._stk = []
    ws._rec = True
    gc.collect()
    m0 = gc.mem_alloc()
    for _i in range(100):
        ws._t.m(1, 2)
    d = gc.mem_alloc() - m0
    ws._rec = False
    return d // 100
ws._g['_cal'] = _cal
"""

# Two modes, because they answer different questions:
#
#   ws._force = True  (EXACT)   -- collect at frame START, so no collect can land
#       mid-frame and every delta is an exact byte count. Answers "who allocates".
#   ws._force = False (NATURAL) -- leave the GC alone, so the collects that
#       actually hitch on glass still happen. Their phase is recorded via the
#       negative delta (_badtags). Answers "who TRIPS the collect", which is a
#       different question whenever the trigger is an explicit gc.collect() call
#       rather than heap pressure -- and with 28MB free on this board, it is.
HOOK = """
ws._force = True
if not hasattr(ws, '_ahook'):
    _of = ws.frame
    def _f(dt, _of=_of):
        if not ws._rec:
            return _of(dt)
        if ws._force:
            gc.collect()
        n0 = ws._frames_drawn
        m0 = gc.mem_alloc()
        r = _of(dt)
        if ws._frames_drawn != n0:
            ws._al.append((gc.mem_alloc() - m0, ws._cur, ws._cnt))
        # Reset at frame END, not entry: handle_input/handle_pointer run OUTSIDE
        # ws.frame (moy_runtime run_desktop), so resetting on entry threw away
        # everything the input path -- i.e. a press/release edge -- allocated.
        # Booked into the next frame's bucket instead, which leaves the aggregate
        # right.
        ws._cur = {}
        ws._cnt = {}
        ws._stk = []
        return r
    ws.frame = _f
    ws._ahook = True
"""

# -- cadence mode ---------------------------------------------------------------
#
# The attribution hook above RETAINS a tuple + two dicts per frame, which is
# itself ~1KB/frame of live data and list growth -- enough to change the GC
# cadence it is trying to measure. (The 2026-07-26 "collect every 14 frames"
# reading came from a probe that did exactly this; at the REPL the same board
# accumulated 1.4MB of garbage between collects.) So the cadence question gets
# its own hook that allocates NOTHING per frame: fixed counters plus a
# preallocated histogram.
CADENCE = """
import array
ws._nf = 0
ws._nc = 0
ws._sum = 0
ws._gapmin = 1 << 30
ws._gapmax = 0
ws._gapsum = 0
ws._hist = array.array('I', bytes(4 * 64))     # frame ms // 4, capped
ws._ahist = array.array('I', bytes(4 * 64))    # frame alloc KB, capped
ws._amax = 0
ws._worst = 0
ws._crec = False
if not hasattr(ws, '_chook'):
    _ofc = ws.frame
    def _fc(dt, _ofc=_ofc):
        if not ws._crec:
            return _ofc(dt)
        t = time.ticks_ms()
        m0 = gc.mem_alloc()
        n0 = ws._frames_drawn
        r = _ofc(dt)
        if ws._frames_drawn != n0:
            ms = time.ticks_diff(time.ticks_ms(), t)
            m1 = gc.mem_alloc()
            ws._nf += 1
            b = ms >> 2
            if b > 63:
                b = 63
            ws._hist[b] += 1
            if ms > ws._worst:
                ws._worst = ms
            if m1 >= m0:
                d = m1 - m0
                ws._sum += d
                kb = d >> 10
                ws._ahist[63 if kb > 63 else kb] += 1
                if d > ws._amax:
                    ws._amax = d
            else:
                # mem_alloc dropped: a collect ran inside this frame. _sum is
                # the garbage accumulated since the previous one.
                ws._nc += 1
                if ws._sum < ws._gapmin:
                    ws._gapmin = ws._sum
                if ws._sum > ws._gapmax:
                    ws._gapmax = ws._sum
                ws._gapsum += ws._sum
                ws._sum = 0
        return r
    ws.frame = _fc
    ws._chook = True
def _creset():
    ws._nf = 0
    ws._nc = 0
    ws._sum = 0
    ws._gapmin = 1 << 30
    ws._gapmax = 0
    ws._gapsum = 0
    ws._worst = 0
    ws._amax = 0
    for _i in range(64):
        ws._hist[_i] = 0
        ws._ahist[_i] = 0
def _cagg():
    return (ws._nf, ws._nc, ws._gapmin // 1024, ws._gapmax // 1024,
            (ws._gapsum // max(1, ws._nc)) // 1024, ws._worst,
            [(i * 4, ws._hist[i]) for i in range(64) if ws._hist[i]],
            ws._amax, [(i, ws._ahist[i]) for i in range(64) if ws._ahist[i]])
ws._g['_creset'] = _creset
ws._g['_cagg'] = _cagg
"""

# -- heap-size A/B (no rebuild) --------------------------------------------------
#
# The esp32 port starts the GC heap at MICROPY_GC_INITIAL_HEAP_SIZE (56-64KB) and
# GROWS it only when a full collect fails to satisfy an allocation (py/gc.c:919 --
# collect first, add an area second). So the heap settles at roughly live+slack
# and then collects every single time that slack fills, forever. With ~500KB live
# and ~12KB/frame that is a 55ms mark-sweep every ~15 frames.
#
# Raising the initial heap is a build constant, but a rebuild+flash per candidate
# size is slow. This emulates one: allocate ballast to force the doubling growth,
# and keep ONE small object per chunk so the sweep cannot reclaim the new areas
# ("free any empty area, aside from the first one", gc.c:727). What is left is a
# big heap with a small live set -- exactly what a big MICROPY_GC_INITIAL_HEAP_SIZE
# gives, so the cadence measured after this predicts the real thing.
PREGROW = """
def _pregrow(mb):
    ws._pin = []
    junk = None
    n = (mb * 1024 * 1024) // 100000
    for _i in range(n):
        junk = bytearray(100000)
        ws._pin.append(bytearray(32))    # pins the area the growth just added
    junk = None
    gc.collect()
    t = time.ticks_ms()
    gc.collect()
    return (gc.mem_alloc() // 1024, len(ws._pin),
            time.ticks_diff(time.ticks_ms(), t))
ws._g['_pregrow'] = _pregrow
"""

AGG = """
def _agg():
    tot = {}
    cnt = {}
    for (_t, cur, c) in ws._al:
        for k in cur:
            tot[k] = tot.get(k, 0) + cur[k]
        for k in c:
            cnt[k] = cnt.get(k, 0) + c[k]
    n = len(ws._al)
    whole = 0
    for f in ws._al:
        whole += f[0]
    items = sorted(tot.items(), key=lambda kv: -kv[1])
    bt = {}
    for k in ws._badtags:
        bt[k] = bt.get(k, 0) + 1
    return (n, whole, ws._bad, items, [(k, cnt[k]) for (k, _v) in items],
            sorted(bt.items(), key=lambda kv: -kv[1]))
ws._g['_agg'] = _agg
"""

# Coarse wraps: the frame's structural phases. Enough to say WHICH surface.
WRAPS = """
def _wrap_phases():
    ws._wrapa(ws.wm, '_draw_windows', 'wm.windows')
    ws._wrapa(ws.wm, '_win_chrome', 'wm.chrome')
    ws._wrapa(ws.wm, '_blit_backdrop_cache', 'desk.cache')
    ws._wrapa(ws, '_composite_game', 'composite')
    ws._wrapa(ws, '_journal_idle_tick', 'journal')
    ws._wrapa(ws, 'handle_input', 'input')
    ws._wrapa(ws, 'handle_pointer', 'pointer')
    for _l in ws.wm.visible_stack_rev():
        _n = getattr(_l, 'id', None) or type(_l).__name__
        ws._wrapa(_l, 'draw', 'layer:' + str(_n))
    _wp = getattr(ws, 'wallpaper', None)
    if _wp is not None:
        ws._wrapa(_wp, 'draw', 'wallpaper')
    for _k in getattr(ws.wm, '_wins', {}):
        try:
            ws._wrapa(ws.wm._content_for(ws.wm._wins[_k].kind), 'draw',
                      'content:' + _k)
        except Exception:
            pass
ws._g['_wrap_phases'] = _wrap_phases
"""

# Fine wraps for the Settings surface -- the one that still hitches. Each is a
# hypothesis about where the bytes are; the tool exists to rank them.
DRILL_SETTINGS = [
    "ws.wm._content_for('settings')::_settings_rows::set.rows",
    "ws.wm._content_for('settings')::_draw_settings_row::set.row_draw",
    "ws.wm._content_for('settings')::_settings_row_rect::set.row_rect",
    "ws.wm._content_for('settings')::_settings_row_visible::set.row_vis",
    "ws.wm._content_for('settings')::_settings_visible::set.visible",
    "ws.wm._content_for('settings')::_scroll_region::set.scroll",
    "ws.bar_layer::_draw_status_strip::bar.status",
    "ws.bar_layer::_draw_dock::bar.dock",
    "ws.sys_canvas::print::cv.print",
    "ws.sys_canvas::rect::cv.rect",
]


def _wrap_extra(b, specs):
    for spec in specs:
        expr, attr, tag = spec.split("::")
        b.pyexec("ws._wrapa(%s, %r, %r)" % (expr, attr, tag))


# -- host half ------------------------------------------------------------------

def report(b, name, natural=False):
    b.pyexec("ws._rec = False")
    n, whole, bad, items, counts, badtags = \
        b.pyval("ws._g['_agg']()") or (0, 0, 0, [], [], [])
    if not n:
        print("  %s: no painted frames" % name)
        return
    cnt = dict(counts)
    named = sum(v for _k, v in items)
    print("\n== %s%s ==  %d painted frames   %.1f KB/frame total"
          % (name, " [natural GC]" if natural else "", n, whole / n / 1024.0))
    if bad and natural:
        print("   collects landed in: %s   (1 per %.1f frames)"
              % ("  ".join("%s x%d" % (k, v) for k, v in badtags),
                 n / float(bad)))
    elif bad:
        print("   !! %d negative deltas -- a collect landed mid-frame, numbers"
              " are NOT exact: %s" % (bad, badtags))
    print("   %-22s %9s %9s %8s" % ("phase (exclusive)", "B/frame", "calls/fr",
                                    "B/call"))
    for tag, total in items:
        c = cnt.get(tag, 0)
        print("   %-22s %9d %9.1f %8.1f"
              % (tag, total // n, c / float(n), total / float(c or 1)))
    print("   %-22s %9d" % ("unattributed", (whole - named) // n))
    return whole / float(n)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", default="/dev/ttyACM0")
    ap.add_argument("--surface", default="both",
                    choices=("settings", "picker", "both"))
    ap.add_argument("--drill", action="store_true",
                    help="add the fine-grained Settings wraps")
    ap.add_argument("--pregrow", type=int, default=0, metavar="MB",
                    help="force the GC heap to grow to ~MB first, emulating a"
                         " bigger MICROPY_GC_INITIAL_HEAP_SIZE without a rebuild")
    ap.add_argument("--cadence", action="store_true",
                    help="zero-retention run: how often a collect lands and how"
                         " much garbage buys one (no per-phase attribution)")
    ap.add_argument("--natural", action="store_true",
                    help="leave the GC alone and report which phase trips a"
                         " collect (instead of exact per-phase bytes)")
    ap.add_argument("--extra", action="append", default=[],
                    metavar="EXPR::ATTR::TAG")
    ap.add_argument("--frames", type=int, default=3, help="gestures to record")
    args = ap.parse_args()

    b = P4Board(args.port)
    try:
        b.reset()
        b.pyexec(PROBE)
        b.pyexec(CAL)
        b.pyexec(HOOK)
        b.pyexec(AGG)
        b.pyexec(WRAPS)
        b.pyexec(CADENCE)
        b.pyexec(PREGROW)
        if args.pregrow:
            live, pins, ms = b.pyval("ws._g['_pregrow'](%d)" % args.pregrow,
                                     240) or (0, 0, 0)
            print("pregrown ~%dMB heap: live %d KB, %d pins, collect now %dms"
                  % (args.pregrow, live, pins, ms))
        over = b.pyval("ws._g['_cal']()")
        print("instrumentation overhead: %s B per wrapped call" % over)

        def gesture(x0, y0, x1, y1, n=30):
            b.ser.write(("swipe %d %d %d %d %d\n" % (x0, y0, x1, y1, n)).encode())
            b.ser.flush()
            b.wait_line("swipe done", 60)
            b.drain(1.2)

        def cadence(label, gest):
            """How OFTEN does a collect land, and how much garbage buys one?"""
            gest()                                   # warm
            b.pyexec("ws._g['_creset']()")
            b.pyexec("ws._crec = True")
            for _ in range(args.frames):
                gest()
            b.pyexec("ws._crec = False")
            nf, nc, gmin, gmax, gavg, worst, hist, amax, ahist = \
                b.pyval("ws._g['_cagg']()") or (0, 0, 0, 0, 0, 0, [], 0, [])
            print("\n== %s [cadence] ==  %d painted frames" % (label, nf))
            if nc:
                print("   %d collects -> 1 per %.1f frames; garbage between:"
                      " %d/%d/%d KB (min/avg/max)"
                      % (nc, nf / float(nc), gmin, gavg, gmax))
            else:
                print("   NO collect in %d frames" % nf)
            # Percentiles off the 4ms-bucket histogram: a bucket's LOW edge, so
            # these read as "at least this fast" and never flatter the result.
            tot_n = sum(v for _b, v in hist)
            def _pct(p):
                seen = 0
                for ms_, v in hist:
                    seen += v
                    if seen >= tot_n * p:
                        return ms_
                return worst
            print("   median %dms  p90 %dms  p99 %dms  worst %dms"
                  % (_pct(0.5), _pct(0.9), _pct(0.99), worst))
            print("   ms histogram: %s" % "  ".join(
                "%d-%d:%d" % (ms, ms + 3, n_) for ms, n_ in hist))
            print("   biggest frame alloc %d B;  KB/frame histogram: %s"
                  % (amax, "  ".join("%dK:%d" % (kb, n_) for kb, n_ in ahist)))

        def record(label, gest):
            if args.cadence:
                # Deliberately NO wraps: the point is a frame loop the probe has
                # not perturbed.
                return cadence(label, gest)
            # NB: called through ws._g -- a SHORT one-line `py` command execs in
            # a FRESH env, so a name the multi-line uploads defined is only
            # reachable via the persistent namespace.
            b.pyexec("ws._g['_wrap_phases']()")
            if args.drill and label.startswith("settings"):
                _wrap_extra(b, DRILL_SETTINGS)
            if args.extra:
                _wrap_extra(b, args.extra)
            gest()                                   # warm (caches, covers)
            b.pyexec("ws._al.clear()")
            b.pyexec("ws._bad = 0")
            b.pyexec("del ws._badtags[:]")
            b.pyexec("ws._force = %r" % (not args.natural))
            b.pyexec("ws._rec = True")
            for _ in range(args.frames):
                gest()
            report(b, label, args.natural)

        if args.surface in ("settings", "both"):
            b.open("settings")
            b.drain(3.0)
            w = b.state()["wins"]["settings"]
            cx = w[0] + 1 + w[2] // 2
            ct = w[1] + 1 + w[4]
            lo, hi = ct + (w[3] - w[4]) - 50, ct + 50
            record("settings scroll", lambda: gesture(cx, lo, cx, hi))
            # A reset, not go_home: go_home lands in the fullscreen Library
            # where the picker is not a window (no 'make' key), and it would carry
            # Settings' warm caches into the picker's numbers.
            b.reset()
            for _s in (PROBE, CAL, HOOK, AGG, WRAPS, CADENCE, PREGROW):
                b.pyexec(_s)

        if args.surface in ("picker", "both"):
            b.open("picker")
            b.drain(14.0)                            # cover pop-in (#155)
            g = b.pyval("ws.wm._wins['make'].ctx.layout.lib_grid")
            if g is None or "make" not in b.state()["wins"]:
                print("\n  picker did not open (no 'make' window) -- skipped")
                return 0
            w = b.state()["wins"]["make"]
            ox, oy = w[0] + 1, w[1] + 1 + w[4]
            gx, gy, gw, gh = g
            cy = oy + gy + gh // 2
            record("picker drag",
                   lambda: gesture(ox + gx + gw - 40, cy, ox + gx + 60, cy))
    finally:
        b.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
