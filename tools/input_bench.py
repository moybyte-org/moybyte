#!/usr/bin/env python3
# Map (grep -n a name to jump there):
#   -- the console side      DEVICE: the hook, the play, the null floor; what cleanup puts back
#   -- the plan              the inputs on wall-clock time
#   Plan                     an input schedule, and the finger it moves
#   build_plan               the named inputs, at the rate a hand makes them
#   -- the numbers           FIELDS, presented, classes
#   analyse                  a row's numbers: the gates, the first three, the finger
#   -- the arithmetic        docs/scroll_and_text_2026-10.md §14's terms
#   phase_of                 which phase reaches a row, and the frame it makes
#   expected                 §14's arithmetic for a row, from its null-floor pass
#   Bench                    one board's run: install, scratch, rows, cleanup
#   Bench.play               arm a schedule, wait it out, read the frames back
#   Bench.row                one surface x one input, and its null-floor play
#   Surface                  a scrolling or text surface: setup, probe, inputs
#   surfaces                 every surface, on the boards that have it
#   run_surface              a surface's rows, its setup and teardown
#   selected, planned        what --only / --inputs / --rows pick, and the rows that plays
#   report                   a run's rows as markdown, the furthest over 33 ms first
#   main                     the CLI
"""Every scrolling and text surface, under every input a board has, timed as
presented frames.

    python3 tools/input_bench.py --board p4 --out DIR [--only code,home] [--inputs fling,type]
    python3 tools/input_bench.py --board tdeck --out DIR --no-null
    python3 tools/input_bench.py --board p4 --out DIR --only home --theme blocks   # a backdrop
    python3 tools/input_bench.py --board p4 --out DIR --only home --theme picture  # a picture
    python3 tools/input_bench.py --board tdeck --out DIR --split      # PERF DIAG lines, apart
    python3 tools/input_bench.py --board p4 --out DIR --rows "Code:fling,Text console:drag"
    python3 tools/input_bench.py --report DIR/p4_input.json [--reanalyse]

`tools/input_gate.py` runs this against a dev image for a surface's gate.

The design this measures is docs/scroll_and_text_2026-10.md; §14 says what
the tool is for. Numbers live in #66.

**Input on wall-clock time.** The console plays a schedule against its own
clock from inside its frame loop (`ws._ib`, installed over `ws.frame`,
`ws.ball` and `ws.comp`), through an input-table source of its own
("bench"): a finger is where a finger moving at that speed would be when a
frame starts, a key goes down and up on its schedule, trackball pulses reach
the console's own `ball`. A slow frame gets fewer, larger finger steps, as a
real one does, and the host sends nothing while a row runs. Keys are the
board's: on the T-Deck a direction is its matrix button and a typed key a
one-shot (`moy_kbd.c`); elsewhere a BLE keyboard's, an arrow its button held
plus its key event, a typed character its byte as the key level while down
(`moy_hid.c`).

**A scratch project.** Every row that types, pastes, finds, draws or edits
runs on `Bench Scratch` (a copy of Star Catcher) or `Bench Scratch Blocks` (a
copy of Coin Quest), which the bench makes at setup; the long note, the
console script and My Art's drawings are `Bench Scratch` too. A seed cart is never opened for
writing. A `finally` that runs on a failure and on Ctrl-C removes the copies,
the note, the script and every doc the run made, and puts back the home's
arrangement and tab, the theme's text and a `--theme` run's theme or wallpaper, the console's name, the clipboard,
full-screen windows, PERF DIAG, and every wrapper; it leaves the board on
its home.

**Presented frames.** Each painted frame (one that reached `comp.flush`, a
ring's debt copy included) keeps its start and end, its `ws.frame` time and
where that went -- the WM's window pass (`windows`) and inside it the
focused window's `content` draw and its `stamp`; the home; every `other`
layer; `copy` (`copy_last`, a ring's debt frame); `present` (`comp.flush`);
the DSI fences -- and when it was PRESENTED: on a P4 its buffer switch (the
flush's, or the next loop's pre-frame hook where the flush deferred it), on
an S3 the feeder's completion (the kick plus the panel's kick-to-out span,
`moy_lcd.stats()`). On a full-screen tier the content is the base layer.

**The row.** Frames are split by what moved: `live` (a finger down; the
first frame after a key, a roll or a typed key), `coast` (after a release,
the view moving), `glide` (after a step, the view moving), `settle` (the
rest: the paints and copies a press costs after the view stopped). The gate
is the p90 of the presented interval over live, coast and glide: from the
previous presentation where the frames ran back to back, else from where
the frame's own loop began (its start less the loop's measured share before
a frame), so a press frame after idle loops is its upcalls, its paint and
its present, never the pacing sleep before it; each class has its own p90. `first3` is the max
of the first three presented intervals of every gesture (the press frame is
its own gate). Per press: `lat` (the input to its first presentation) and
`calm` (to its last frame before the next input: the debt and the refill).
Where the probe is a pixel offset (`px`), the finger's scheduled travel
against the content's at each presentation (`lag`, px) and the release
velocity against the finger's. `shift%` / `rows%` are the fraction of
painted frames the frame's own change answered as a shift or as rows
(`shift_last` answering True, or `ws.frame_change`), None where nothing on
the console speaks it.

**The null floor.** Each row is played a second time with every content
raster clipped to an empty floor: the content's canvases get an empty clip
they cannot widen, and `cls` and the PPA fill do nothing. What the content
draw still costs is its PASS (its Python, the engine's C layout, the
clipped verbs' calls): the shift frame's lower bound, and the `pass` term
of §14's arithmetic, which `expected` prices beside each row, per phase.

`--split` plays each surface's long inputs under PERF DIAG instead and keeps
the LOOP / PUMP / PERF / HITCH lines (the diag costs ~4 ms a frame, so it is
a run of its own, never the numbers).
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

TARGET_MS = 33.0
SCRATCH = "Bench Scratch"
MATRIX_BOARDS = ("tdeck",)       # a keyboard matrix and a trackball
STAR = "Star Catcher"
COIN = "Coin Quest"

# -- the console side ------------------------------------------------------------

DEVICE = r"""
import time, array
_T = time.ticks_us
_D = time.ticks_diff
_NS = 18

class _IB:
    pass

def _nop(*a):
    return None

def _yes(*a):
    return True

class _CompProxy:
    def __init__(s, c):
        s._c = c
    def flush(s, *a):
        ib = ws._ib
        t = _T()
        r = s._c.flush(*a)
        e = _T()
        if ib.on:
            ib.acc[5] += _D(e, t)
            ib.tf = _D(e, ib.t0)
            st = ib.lcd
            if st is not None:
                v = st()
                ib.ff = v[0]
                ib.fs = v[1]
        return r
    def __getattr__(s, n):
        return getattr(s._c, n)

ib = getattr(ws, '_ib', None)
if ib is None:
    ib = ws._ib = _IB()
    ib.cap = 1500
    ib.rec = array.array('i', bytearray(4 * _NS * ib.cap))
    ib.bt = array.array('i', bytearray(4 * 600))
    ib.acc = [0, 0, 0, 0, 0, 0, 0]     # content stamp windows home other present copy
    ib.on = False
    ib.done = True
    ib.null = False
    ib.n = ib.bn = ib.ei = 0
    ib.ev = []
    ib.end = 0
    ib.t0 = _T()
    ib.src = ws.input.source('bench')
    ib.wrapped = []                    # (object, attribute name) the bench set
    ib.ncv = []
    ib.of = ws.frame
    ib.ob = ws.ball
    ib.oc = ws.comp
    ib.ov = getattr(ws.comp, 'overlap_stats', None)
    _lcd = getattr(ws.comp, '_lcd', None)
    ib.lcd = getattr(_lcd, 'stats', None) if _lcd is not None else None
    ws.comp = _CompProxy(ws.comp)

def _set(o, n, v):
    d = getattr(o, '__dict__', None)
    had = d is not None and n in d
    ws._ib.wrapped.append((o, n, had, d[n] if had else None))
    setattr(o, n, v)

def _null_on(ib):
    for cv in ib.ncv:
        cv._ib_c = (cv._clip_x0, cv._clip_y0, cv._clip_x1, cv._clip_y1)
        cv._clip_x1 = cv._clip_x0
        cv._clip_y1 = cv._clip_y0
        cv._sync_gate_state()
        cv.clip = _nop
        cv.cls = _nop
        if hasattr(cv, 'ppa_fill'):
            cv.ppa_fill = _yes

def _null_off(ib):
    for cv in ib.ncv:
        c = cv._ib_c
        cv._clip_x0 = c[0]
        cv._clip_y0 = c[1]
        cv._clip_x1 = c[2]
        cv._clip_y1 = c[3]
        for n in ('clip', 'cls', 'ppa_fill'):
            try:
                delattr(cv, n)
            except AttributeError:
                pass
        cv._sync_gate_state()

def _timed(o, slot, null=False):
    _od = o.draw
    def f(dt, _od=_od, slot=slot, null=null):
        ib = ws._ib
        nul = null and ib.null and ib.on
        if nul:
            _null_on(ib)
        t = _T()
        try:
            r = _od(dt)
        finally:
            ib.acc[slot] += _D(_T(), t)
            if nul:
                _null_off(ib)
        return r
    _set(o, 'draw', f)
    _set(o, '_ib_d', True)

def _canvases():
    out = []
    wm = ws.wm
    for c in (getattr(ws, 'canvas', None), getattr(ws, 'sys_canvas', None),
              getattr(wm, '_root_canvas', None)):
        if c is not None and hasattr(c, '_clip_x0') and not any(c is o for o in out):
            out.append(c)
    for w in (getattr(wm, '_wins', None) or {}).values():
        b = getattr(w, 'buf', None)
        if b is not None and hasattr(b, '_clip_x0') and not any(b is o for o in out):
            out.append(b)
    return out

def _wrap():
    # The layers and contents are made again per world and relayout: wrap
    # what shows now.
    wm = ws.wm
    lay = wm.visible_stack_rev()
    desk = bool(getattr(wm, 'has_desk', False))
    base = lay[len(lay) - 1] if lay else None
    for l in lay:
        if getattr(l, '_ib_d', False):
            continue
        nm = getattr(l, 'id', '')
        if nm == 'cursor':
            continue
        if desk:
            slot = 2 if nm == 'windows' else 3 if nm in ('launcher', 'desktop') else 4
            _timed(l, slot, nm == 'launcher')
        else:
            _timed(l, 0 if l is base else 4, l is base)
    cf = getattr(wm, '_content_for', None)
    for k in list(getattr(wm, '_order', []) or []):
        w = wm._wins.get(k)
        c = cf(w.kind) if (w is not None and cf is not None) else None
        if c is not None and not getattr(c, '_ib_d', False):
            try:
                _timed(c, 0, True)
            except Exception:
                pass
    rc = getattr(wm, '_root_canvas', None) or getattr(ws, 'canvas', None)
    if rc is not None and not getattr(rc, '_ib_d', False):
        for nm, slot in (('blit_strip', 1), ('blit_strip_rect', 1), ('copy_last', 6)):
            f = getattr(rc, nm, None)
            if f is not None:
                _set(rc, nm, _timed_call(f, slot))
        sl = getattr(rc, 'shift_last', None)
        if sl is not None:
            _set(rc, 'shift_last', _counted_shift(sl))
        _set(rc, '_ib_d', True)

def _timed_call(f, slot):
    def g(*a):
        t = _T()
        r = f(*a)
        ws._ib.acc[slot] += _D(_T(), t)
        return r
    return g

def _counted_shift(sl):
    def h(*a):
        r = sl(*a)
        if r:
            ws._ib.shifted = True
        return r
    return h

def _fr(dt):
    ib = ws._ib
    if not ib.on:
        return ib.of(dt)
    a = _T()
    if ib.dfr >= 0:
        # the last frame's show waited for this loop's pre-frame hook
        ib.rec[ib.dfr * _NS + 12] = _D(a, ib.t0)
        ib.dfr = -1
    now = _D(a, ib.t0) // 1000
    if now >= ib.end:
        ib.on = False
        ib.done = True
        ib.seg = None
        ib.src.release_all()
        ib.src.point(0, 0, False, False, False)
        ib.src.last_key = 0
        return ib.of(dt)
    _play(ib, now)
    pl = ib.le
    acc = ib.acc
    for i in range(7):
        acc[i] = 0
    ov = ib.ov
    o0 = ov() if ov is not None else None
    ib.shifted = False
    ib.tf = -1
    n0 = ws._frames_drawn
    r = ib.of(dt)
    e = _T()
    if ws._frames_drawn != n0 and ib.n < ib.cap:
        o1 = ov() if ov is not None else None
        i = ib.n * _NS
        rec = ib.rec
        rec[i] = _D(a, ib.t0)
        rec[i + 1] = _D(e, ib.t0)
        rec[i + 2] = _D(e, a)
        rec[i + 3] = acc[0]
        rec[i + 4] = acc[1]
        rec[i + 5] = acc[2]
        rec[i + 6] = acc[3]
        rec[i + 7] = acc[4]
        rec[i + 8] = acc[5]
        rec[i + 9] = acc[6]
        rec[i + 10] = (o1[3] - o0[3]) if o1 is not None else -1
        pr = ib.probe
        v = -99999
        if pr is not None:
            try:
                v = int(pr())
            except Exception:
                v = -99999
        rec[i + 11] = v
        rec[i + 12] = ib.tf if ib.tf >= 0 else rec[i + 1]
        dfd = (o1[0] - o0[0]) if o1 is not None else 0
        rec[i + 13] = dfd
        if dfd > 0:
            ib.dfr = ib.n
        rec[i + 14] = ib.ff if ib.lcd is not None else -1
        rec[i + 15] = ib.fs if ib.lcd is not None else -1
        fc = getattr(ws, 'frame_change', None)
        rec[i + 16] = 1 if (ib.shifted or fc == 'shift') else 2 if fc == 'rows' else 0
        rec[i + 17] = pl
        ib.n += 1
    ib.le = _D(_T(), ib.t0)
    return r

def _play(ib, now):
    ev = ib.ev
    src = ib.src
    while ib.ei < len(ev) and ev[ib.ei][0] <= now:
        e = ev[ib.ei]
        ib.ei += 1
        k = e[1]
        if k == 1:
            pass
        elif k == 2 or k == 5:
            if ib.ts is not None:
                ws.input_used(ib.ts)
        elif ib.ks is not None:
            ws.input_used(ib.ks)
        if k == 0:
            src.key(e[2])
        elif k == 1:
            ib.px += e[2]
            ib.py += e[3]
            ws.ball_owed = True
        elif k == 2:
            ib.seg = e
            ib.fresh = e[7]
        elif k == 3:
            src.set_button(e[2], e[3])
        elif k == 4:
            src.last_key = e[2]
        elif k == 5:
            ib.seg = None
            src.point(e[2], e[3], False, False, True)
    s = ib.seg
    if s is not None:
        u = now - s[0]
        if u >= s[6]:
            x = s[4]
            y = s[5]
        else:
            x = s[2] + (s[4] - s[2]) * u // s[6]
            y = s[3] + (s[5] - s[3]) * u // s[6]
        src.point(x, y, True, ib.fresh, True)
        ib.fresh = False

try:
    from chrome import _cursor_delta as _cd
except ImportError:
    _cd = None

def _bl(dx, dy, click=False):
    ib = ws._ib
    ix = iy = 0
    if ib.on and (ib.px or ib.py):
        ix = ib.px
        iy = ib.py
        dx += ix
        dy += iy
        ib.px = ib.py = 0
    was = ws.ball_stepped
    r = ib.ob(dx, dy, click)
    if (ix or iy) and not r & 1 and _cd is not None and ws.pointer is not None:
        ws.pointer.move(_cd(ix), _cd(iy))
    if ib.on:
        k = ws.ball_stepped - was
        if not k and r & 1 and (dx or dy) and not ws.ball_steps():
            k = 1
        t = _D(_T(), ib.t0) // 1000
        for _ in range(k):
            if ib.bn < 600:
                ib.bt[ib.bn] = t
            ib.bn += 1
    return r

ws.frame = _fr
ws.ball = _bl

def _ib_arm(ev, probe, ms, null=False):
    ib = ws._ib
    _wrap()
    ib.ncv = _canvases() if null else []
    ib.null = null
    ib.ev = ev
    ib.ei = 0
    ib.n = 0
    ib.bn = 0
    ib.px = ib.py = 0
    ib.seg = None
    ib.fresh = False
    ib.dfr = -1
    ib.tf = -1
    ib.ff = ib.fs = -1
    ib.le = 0
    ib.probe = probe
    ib.p0 = -99999
    if probe is not None:
        try:
            ib.p0 = int(probe())
        except Exception:
            pass
    # the board's sources a key and a finger stand for: the input in use
    # follows what the bench plays, as it follows a real keyboard or touch
    ik = getattr(ws, 'input_kinds', None) or {}
    ib.ks = None
    ib.ts = None
    for _s in ik:
        if ik[_s] == 'touch':
            ib.ts = _s
        elif ib.ks is None:
            ib.ks = _s
    ib.done = False
    ib.t0 = _T()
    ib.end = ms
    ib.on = True

def _ib_take(a, b):
    return list(ws._ib.rec[a * _NS:b * _NS])

def _ib_tail():
    # the last record's show (a deferral the run ended on), and the panel's
    # span for the last frame it shipped
    ib = ws._ib
    st = ib.lcd() if ib.lcd is not None else (-1, -1)
    return (ib.n, ib.bn, ib.p0, st[0], st[1])

def _ib_speaks():
    rc = getattr(ws.wm, '_root_canvas', None) or getattr(ws, 'canvas', None)
    return hasattr(ws, 'frame_change') or (rc is not None and hasattr(rc, 'shift_last'))

def _ib_host(name):
    if name == 'settings':
        return ws.settings_layer._input_host()
    a = ws._apps_by_id.get(name)
    return getattr(a, '_ui', None)

import moy_ui as _mu

def _ib_off(name, x, y):
    h = _ib_host(name)
    return _mu.api_offset(h.ctx, x, y) if h is not None else -99999

def _ib_home(arr):
    if getattr(ws.wm, 'has_desk', False):
        ws.open_library()
    else:
        ws.go_home()
    ws.set_home_arrangement(arr)
    ws.set_home_tab('games')
    ws.launcher.sel = 0
    ws.launcher._scroll_to_sel()
    ws.mark_dirty()

def _ib_desk():
    ws.go_home()
    if getattr(ws.wm, 'has_desk', False) and ws.wm.top_kind() != 'desktop':
        try:
            ws.open_desk()
        except Exception:
            pass

def _ib_app(app):
    _ib_desk()
    if app == 'settings':
        ws.open_settings()
    else:
        ws.open_app(ws._apps_by_id[app])

def _ib_cart(title):
    for c in ws.carts.all:
        if c.get('title') == title:
            return c
    return None

def _ib_editor(title, tab=None, ws_key=None):
    _ib_desk()
    ws.open_in_editor(_ib_cart(title))
    if ws_key is not None:
        d = getattr(ws.editor_app, '_desk', None)
        if d is not None and d() is not None:
            d().switch(ws_key)
    if tab is not None:
        ws.editor_app.set_tab(tab)
    ws.mark_dirty()

def _ib_window():
    # the Editor a window, whatever this board last left it as (`win_full`):
    # the window is the WM's once a frame drew it, so this is a step after
    # _ib_editor; "Code full screen" is the rows that take it full (_ib_full)
    _w = (getattr(ws.wm, '_wins', None) or {}).get('menu')
    if _w is not None and getattr(_w, 'full', None) is not None and _w.full():
        ws.wm.bring_forward('menu')
        ws.wm.full_key()
        ws.mark_dirty()

def _ib_focus(tab):
    # the keys on the desk pane that shows `tab`, found in the arrangement
    # of the window the desk drew in
    d = getattr(ws.editor_app, '_desk', None)
    d = d() if d is not None else None
    if d is None or not d.active() or d._drew_in is None:
        return
    _lay = ws.layout
    ws.layout = d._drew_in
    try:
        for _n, _pane, _r in d._shown_panes():
            if tab in _pane:
                d.focus_pane(_n)
                break
    finally:
        ws.layout = _lay
    ws.mark_dirty()

def _ib_picker():
    # the sheet where the open pickers' body shows it, in the content's
    # pixels: offset by the desk pane's body the Sprite tab draws in
    _pl = ws.paint_layer
    if not _pl._pop or _pl._host is None:
        return None
    _s = getattr(_pl, 'sheet_shown', 0)
    if _s == 0:
        # an image before the pickers kept it: the part inside the card's
        # inset (the value row's, moy_ui_modal_band)
        _c = _pl._host.box('Sprites')
        _p = _pl._host.box('Sheet')
        if _c is None or _p is None:
            return None
        _m = (18 if _pl.layout.desk else 10) * max(1, getattr(ws.sys_canvas, 'font_scale', 1))
        _s = (max(_c[0], _p[0]), max(_c[1] + _m, _p[1]), min(_c[0] + _c[2], _p[0] + _p[2]),
              min(_c[1] + _c[3] - _m, _p[1] + _p[3]))
    if _s is None or _s[2] <= _s[0] or _s[3] <= _s[1]:
        return None
    _d = getattr(ws, 'editor_desk', None)
    _o = _d._own_pane() if _d is not None and _d.active() else None
    _ox, _oy = (_o[4][0], _o[4][1]) if _o is not None else (0, 0)
    return (_s[0] + _ox, _s[1] + _oy, _s[2] - _s[0], _s[3] - _s[1])

def _ib_full():
    # the window full screen (never a toggle: _ib_window left it a window)
    _w = (getattr(ws.wm, '_wins', None) or {}).get('menu')
    f = getattr(ws.wm, 'full_key', None)
    if f is not None and not (_w is not None and getattr(_w, 'full', None) is not None
                              and _w.full()):
        f()
        ws.mark_dirty()
"""

UNINSTALL = r"""
_ib = getattr(ws, '_ib', None)
if _ib is not None:
    _ib.on = False
    _ib.src.release_all()
    _ib.src.point(0, 0, False, False, False)
    _ib.src.last_key = 0
    for _o, _n, _had, _old in reversed(_ib.wrapped):
        try:
            if _had:
                setattr(_o, _n, _old)
            else:
                delattr(_o, _n)
        except AttributeError:
            pass
    ws.comp = _ib.oc
    for _n in ('frame', 'ball'):
        try:
            delattr(ws, _n)
        except AttributeError:
            pass
    ws._ib = None
"""

# What the run found at setup, to put back (`SNAPSHOT`) and the putting back
# (`CLEANUP`), each step on its own so one that fails does not keep the rest.
SNAPSHOT = r"""
import moy_catalogue as _mc
_ib_s = ws._ib_s = {}
_ib_s['docs'] = set(ws.carts_store.list_files('docs', ws.carts_root) or ())
_ib_s['drawings'] = set(ws.carts_store.list_files('drawings', ws.carts_root) or ())
_ib_s['carts'] = set(c.get('path') for c in ws.carts.all)
_ib_s['name'] = ws.system.get('name', '')
_ib_s['home'] = type(ws.launcher)
_ib_s['tab'] = ws.launcher.tab
_ib_s['theme'] = ws.look.file_text()
_cl = getattr(ws, 'clipboard', None)
_ib_s['clip'] = _cl.text() if _cl is not None else None
_ib_s['full'] = set(k for k, w in (getattr(ws.wm, '_wins', None) or {}).items()
                    if getattr(w, 'full', None) is not None and w.full())
"""

SCRATCH_MAKE = r"""
def _ib_scratch(src, title):
    c = _ib_cart(title)
    if c is not None:
        return c
    s = _ib_cart(src)
    ws.carts.apply(ws.carts.store.call(lambda: (
        _mc.duplicate(s['h'], ws.carts_root, new_title=title, ns=ws.carts._ns()),
        _mc.catalogue(ws.carts_root))[1]))
    return _ib_cart(title)
_ib_scratch(%r, %r)
_ib_scratch(%r, %r)
ws._ib_note = ws.carts_store.save_file('docs', %r, %r, ws.carts_root)
ws._ib_script = ws.carts_store.save_file('docs', %r, %r, ws.carts_root)
import moyimg as _mimg
_w, _h = 320, 240
_row = bytes(((x * 7) // 13 + x // 40) %% 16 for x in range(_w + 64))
for _i in range(%d):
    _pix = bytearray(_w * _h)
    for _y in range(_h):
        _o = (_y * (3 + _i)) %% 64
        _pix[_y * _w:(_y + 1) * _w] = _row[_o:_o + _w]
    ws.carts_store.save_file('drawings', '%s %%d' %% _i, _mimg.encode_moyimg(_w, _h, bytes(_pix)),
                             ws.carts_root)
_pix = _row = None
"""

# A picture wallpaper reaches the glass through a theme: the pick forks the
# theme in force into the store's `custom` package (any custom before is
# replaced) and copies the wallpaper copy into it. So the run keeps the
# custom package's files, the wallpaper copy and the look it found, and
# PICTURE_PUT_BACK writes them back.
PICTURE_MAKE = """
import os as _ib_os
import moy_carts as _ib_mcarts
def _ib_files(d):
    out = {}
    for _n in _ib_os.listdir(d):
        try:
            with open(d + '/' + _n, 'rb') as _f:
                out[_n] = _f.read()
        except OSError:
            pass                          # (a folder: the thumbnails, rebuilt)
    return out
_d = ws.look._pkg_paths.get('custom')
ws._ib_s['pic'] = (ws.look.theme_name, ws.look.theme_variant, ws.look.wallpaper_id,
                   _ib_files(_d) if _d is not None else None,
                   _ib_mcarts.load_copy(ws.carts_root))
_w, _h = ws.sys_canvas.w, ws.sys_canvas.h
_row = bytes((x * 7) % 60 for x in range(_w + 64))
_pix = bytearray(_w * _h)
for _y in range(_h):
    _o = (_y * 3) % 60
    _pix[_y * _w:(_y + 1) * _w] = _row[_o:_o + _w]
_ib_mcarts.save_copy(_ib_mcarts.encode_image(_w, _h, bytes(_pix)), ws.carts_root)
_pix = _row = None
ws.look.select_wallpaper(ws.look.PICTURE)
ws.look.tick(0.0)
ws.look.tick(ws.look.AUTOSAVE_S)
ws.mark_dirty()
"""

PICTURE_PUT_BACK = """
import os as _ib_os
import moy_carts as _ib_mcarts
_tn, _tv, _wp, _cf, _cp = ws._ib_s.pop('pic')
_d = ws.look._pkg_paths.get('custom')
if _d is not None:
    for _n in _ib_os.listdir(_d):
        if _cf is None or _n not in _cf:
            try:
                _ib_os.remove(_d + '/' + _n)
            except OSError:
                pass
    if _cf is None:
        try:
            _ib_os.rmdir(_d + '/thumbs')
        except OSError:
            pass
        try:
            _ib_os.rmdir(_d)
        except OSError:
            pass
    else:
        for _n, _b in _cf.items():
            with open(_d + '/' + _n, 'wb') as _f:
                _f.write(_b)
_a = ws.carts_root.rsplit('/', 1)[0] + '/artwork.moyimg'
if _cp is None:
    for _n in (_a, _a + '.bak'):
        try:
            _ib_os.remove(_n)
        except OSError:
            pass
else:
    _ib_mcarts.save_copy(_cp, ws.carts_root)
ws.carts.rescan()
ws.look.store_moved()
ws.look.set_theme(_tn, persist=True, variant=_tv)
ws.look.tick(0.0)
ws.look.tick(ws.look.AUTOSAVE_S)
if ws.look.wallpaper_id != _wp:
    ws.look.select_wallpaper(_wp, persist=False)
ws.mark_dirty()
"""

CLEANUP_STEPS = (
    "_ib_desk()",
    # a --theme picture run's custom package, wallpaper copy and look back
    "if 'pic' in ws._ib_s:\n"
    + "".join("    " + _l + "\n" for _l in PICTURE_PUT_BACK.strip().split("\n")),
    # the theme's text as it was, and Config clean, so no exit writes it
    "_d = getattr(ws.editor_app, 'theme', None)\n"
    "if _d is not None:\n"
    "    _d.ed.dirty = False\n"
    "    _d.saved = True\n"
    "if ws._ib_s.get('theme') is not None and ws.look.file_text() != ws._ib_s['theme']:\n"
    "    ws.look.file_write(ws._ib_s['theme'])",
    "ws.settings_layer._name = None\n"
    "if ws.system.get('name', '') != ws._ib_s['name']:\n"
    "    ws.system.set('name', ws._ib_s['name'])",
    "_cl = getattr(ws, 'clipboard', None)\n"
    "if _cl is not None and ws._ib_s.get('clip') is not None:\n"
    "    _cl.put_text(ws._ib_s['clip'])",
    "for _k, _w in list((getattr(ws.wm, '_wins', None) or {}).items()):\n"
    "    if getattr(_w, 'full', None) is not None and _w.full() and _k not in ws._ib_s['full']:\n"
    "        ws.wm.bring_forward(_k)\n"
    "        ws.wm.full_key()",
    "_ib_desk()",
    "for _c in [c for c in ws.carts.all if c.get('path') not in ws._ib_s['carts']]:\n"
    "    ws.carts.delete(_c)",
    "import os as _os\n"
    "for _k in ('docs', 'drawings'):\n"
    "    for _n in (ws.carts_store.list_files(_k, ws.carts_root) or ()):\n"
    "        if _n in ws._ib_s[_k]:\n"
    "            continue\n"
    "        for _f in (ws.carts_store.file_path, ws.carts_store.history_path):\n"
    "            try:\n"
    "                _os.remove(_f(_k, _n, ws.carts_root))\n"
    "            except OSError:\n"
    "                pass",
    "for _k, _v in (('carousel', None), ('grid', None), ('icons', None)):\n"
    "    ws.set_home_arrangement(_k)\n"
    "    if type(ws.launcher) is ws._ib_s['home']:\n"
    "        break",
    "ws.set_home_tab(ws._ib_s['tab'])",
    "if 'tn' in ws._ib_s:\n"
    "    ws.look.set_theme(ws._ib_s['tn'], persist=False)",
    "ws.go_home()\nws.mark_dirty()",
)

# A note long enough to scroll (under the editor handle's 8000 characters,
# past which it types nothing) and a script that prints a scrollback and then
# waits on input().
NOTE = "\n".join("Line %d of the bench's long note, with words to wrap across the "
                 "width of the page." % i for i in range(70))
SCRIPT = ("for i in range(200):\n"
          "    print('line %d of the bench scrollback' % i)\n"
          "def _update(*a):\n"
          "    s = input('> ')\n"
          "    if s is not None:\n"
          "        print('you typed ' + s)\n")
DRAWINGS = 12                  # My Art's cards: drawings of their own, a P4 page and more
NOTE_FILE = SCRATCH             # the store names them (`save_file`'s answer)
SCRIPT_FILE = SCRATCH + ".py"


# -- the plan ----------------------------------------------------------------------

ARROW = {"left": 0x110, "right": 0x111, "up": 0x112, "down": 0x113}


class Plan:
    """An input schedule: (t_ms, kind, ...) events the console plays at their
    time. Kinds: 0 a typed one-shot key, 1 trackball pulses (dx, dy), 2 a
    finger segment (x0, y0, x1, y1, ms, press edge), 3 a button held or let
    go, 4 the key level, 5 the finger up at (x, y). `marks` are the gestures
    a person would count: (t_ms, "touch" | "key" | "ball" | "type"); a touch
    mark carries its release, and `segs` the finger's path for the host's
    finger check."""

    def __init__(self, matrix=False, lead=150):
        self.ev = []
        self.t = float(lead)
        self.matrix = matrix
        self.marks = []
        self.segs = []          # (t0, x0, y0, x1, y1, ms)
        self.releases = []      # (t_ms, velocity px/ms along the move)
        self.coast = 800

    def end(self):
        return int(self.t + self.coast)

    def wait(self, ms):
        self.t += ms
        return self

    def drag(self, x0, y0, x1, y1, ms, hold=0):
        """A finger from (x0, y0) to (x1, y1) in `ms` at a steady speed, still
        for `hold`, then up (no hold: a fling)."""
        t = int(self.t)
        self.ev.append((t, 2, int(x0), int(y0), int(x1), int(y1), int(ms), True))
        self.segs.append((t, x0, y0, x1, y1, int(ms)))
        up = t + int(ms) + int(hold)
        self.ev.append((up, 5, int(x1), int(y1)))
        d = ((x1 - x0) ** 2 + (y1 - y0) ** 2) ** 0.5
        self.releases.append((up, 0.0 if hold else d / max(1, ms)))
        self.marks.append((t, "touch", up))
        self.t = up
        return self

    def taps(self, name, n, per_s, hold=60):
        """`n` presses of direction `name`, each held `hold` ms."""
        step = 1000.0 / per_s
        for i in range(n):
            t = int(self.t + i * step)
            self._press(t, name, hold)
            self.marks.append((t, "key", t + hold))
        self.t += n * step
        return self

    def hold(self, name, ms):
        t = int(self.t)
        self._press(t, name, ms)
        self.marks.append((t, "key", t + ms))
        self.t += ms
        return self

    def _press(self, t, name, hold):
        self.ev.append((t, 3, name, True))
        self.ev.append((t + hold, 3, name, False))
        if not self.matrix and name in ARROW:
            self.ev.append((t, 4, ARROW[name]))
            self.ev.append((t + hold, 4, 0))

    def keys(self, text, per_s, hold=None):
        """Typed keys (a str, or codes), `per_s` a second."""
        step = 1000.0 / per_s
        hold = int(hold if hold is not None else step / 2)
        for i, ch in enumerate(text):
            t = int(self.t + i * step)
            code = ord(ch) if isinstance(ch, str) else int(ch)
            if self.matrix:
                self.ev.append((t, 0, code))
            else:
                self.ev.append((t, 4, code))
                self.ev.append((t + hold, 4, 0))
            self.marks.append((t, "type", t + hold))
        self.t += len(text) * step
        return self

    def roll(self, axis, pulses, per_s):
        """`pulses` trackball pulses along `axis` ("x", "-x", "y", "-y"),
        `per_s` a second; a step is two pulses (`BALL_STEP`)."""
        step = 1000.0 / per_s
        sgn = -1 if axis.startswith("-") else 1
        for i in range(pulses):
            t = int(self.t + i * step)
            self.ev.append((t, 1, sgn if axis[-1] == "x" else 0, sgn if axis[-1] == "y" else 0))
            if i % 2 == 1:
                self.marks.append((t, "ball", t))
        self.t += pulses * step
        return self

    def sorted(self):
        return sorted(self.ev, key=lambda e: (e[0], e[1]))

    def finger(self, t):
        """Where the scheduled finger is at `t` ms: (x, y), or None while up."""
        for (t0, x0, y0, x1, y1, ms), (_m, _k, up) in zip(
                self.segs, [m for m in self.marks if m[1] == "touch"]):
            if t0 <= t <= up:
                u = min(1.0, (t - t0) / max(1, ms))
                return (x0 + (x1 - x0) * u, y0 + (y1 - y0) * u)
        return None


def _along(rect, axis, f0, f1):
    """Two points across `rect` (x, y, w, h) along `axis`, at fractions f0, f1."""
    x, y, w, h = rect
    if axis == "h":
        return (x + int(w * f0), y + h // 2, x + int(w * f1), y + h // 2)
    return (x + w // 2, y + int(h * f0), x + w // 2, y + int(h * f1))


TEXT = {
    "type": ("score = score + 1  # typed\r", 10),
    "type_fast": ("lives = lives - 1  # again\r", 20),
    "type_line": ("x = " + "+".join(str(i) for i in range(1, 40)) + "\r", 20),
}


def build_plan(name, surf, rect, matrix):
    """The named input on surface `surf`, its finger in `rect` (glass
    pixels), at the rate a hand makes it."""
    p = Plan(matrix)
    fwd, back = surf.keys
    ax = "x" if surf.axis == "h" else "y"
    if name == "fling":
        p.drag(*_along(rect, surf.axis, 0.72, 0.28), ms=120)
        p.coast = 2500
    elif name == "drag":
        p.drag(*_along(rect, surf.axis, 0.28, 0.72), ms=900, hold=200)
        p.coast = 1500
    elif name == "key_steps":
        p.taps(fwd, 8, 4)
    elif name == "key_held":
        p.hold(fwd, 2000)
    elif name == "key_20":
        p.taps(back, 16, 20, hold=25)
    elif name in TEXT:
        text, rate = surf.text.get(name, TEXT[name])
        p.keys(text, rate)
    elif name == "ball_slow":
        p.roll(ax, 24, 12)                # 6 steps a second for 2 s
    elif name == "ball_fast":
        p.roll(ax, 60, 60)                # 30 steps a second for 1 s
    elif name == "find":
        p.keys("score", 5).wait(300).keys("\r\r\r", 3)
    elif name == "paste":
        p.keys([0x16], 2)
        p.coast = 1500
    elif name == "long":                  # --split: a drag kept moving, there and back
        a = _along(rect, surf.axis, 0.75, 0.25)
        t = int(p.t)
        p.ev.append((t, 2, a[0], a[1], a[2], a[3], 1600, True))
        p.ev.append((t + 1600, 2, a[2], a[3], a[0], a[1], 1600, False))
        p.ev.append((t + 3200, 5, a[0], a[1]))
        p.marks.append((t, "touch", t + 3200))
        p.t = t + 3200
    else:
        raise KeyError(name)
    return p


# -- the numbers -------------------------------------------------------------------

FIELDS = ("t0", "t1", "total", "content", "stamp", "windows", "home", "other", "present",
          "copy", "fence", "probe", "show", "deferred", "flushes", "span", "kind", "prev")
NONE_PROBE = -99999


def frames_of(raw):
    n = len(raw) // len(FIELDS)
    return [dict(zip(FIELDS, raw[i * len(FIELDS):(i + 1) * len(FIELDS)])) for i in range(n)]


def presented(fr, tail=None):
    """Each frame's presentation, ms into the run: its buffer switch where
    the board has one (`show`), else the S3 feeder's completion -- the kick
    plus the span the panel reports once the NEXT frame is kicked (`tail`:
    the counters read after the run, for the last one)."""
    out = []
    for i, f in enumerate(fr):
        t = f["show"] / 1000.0
        if f["flushes"] >= 0:
            nxt = fr[i + 1] if i + 1 < len(fr) else None
            span = None
            if nxt is not None and nxt["flushes"] == f["flushes"] + 1:
                span = nxt["span"]
            elif nxt is None and tail is not None and tail[3] == f["flushes"] + 1:
                span = tail[4]
            if span is not None and span >= 0:
                t += span / 1000.0
        out.append(t)
    return out


def _st(vals):
    v = sorted(x for x in vals if x is not None)
    if not v:
        return None
    n = len(v)
    return {"n": n, "med": round(statistics.median(v), 1),
            "p90": round(v[min(n - 1, int(n * 0.9))], 1), "max": round(v[-1], 1)}


def classes(fr, pres, marks):
    """Per frame: its gesture (the index of the last mark at or before its
    start, -1 before the first), its class and its presented interval."""
    probed = any(f["probe"] != NONE_PROBE for f in fr)
    # A frame whose loop came right after the last painted one presents
    # `pres - pres_prev` after it. One after idle loops starts its interval
    # where its own loop began: its start less the loop's own share before a
    # frame (its upcalls and present wait), the median of that share where
    # frames ran back to back, so the idle loops' pacing sleep is left out.
    cont = [i > 0 and abs(f["prev"] - fr[i - 1]["t1"]) < 1000 for i, f in enumerate(fr)]
    pre = [(f["t0"] - f["prev"]) / 1000.0 for i, f in enumerate(fr) if cont[i]]
    up = statistics.median(pre) if pre else 0.0
    out = []
    mi = -1
    first_of = {}
    for i, f in enumerate(fr):
        t = f["t0"] / 1000.0
        while mi + 1 < len(marks) and marks[mi + 1][0] <= t:
            mi += 1
        prev = pres[i - 1] if i > 0 else None
        if mi < 0:
            out.append((-1, "before", None))
            continue
        m = marks[mi]
        if cont[i]:
            iv = pres[i] - prev
        else:
            iv = pres[i] - max(prev if prev is not None else 0.0, f["t0"] / 1000.0 - up)
        moved = (f["probe"] != NONE_PROBE and i > 0 and fr[i - 1]["probe"] != NONE_PROBE
                 and f["probe"] != fr[i - 1]["probe"])
        if m[1] == "touch":
            paint = f["content"] or f["windows"] or f["home"] or f["other"]
            cls = "live" if t <= m[2] else (
                "coast" if moved or (not probed and paint) else "settle")
        else:
            if mi not in first_of:
                first_of[mi] = i
                cls = "live"
            else:
                cls = "glide" if moved else "settle"
        out.append((mi, cls, iv))
    return out


def analyse(raw, plan_d, tail=None, steps=None, rect=None, px=False, axis="v"):
    """A row's numbers from its frames (`raw`, FIELDS each) and its plan's
    marks, segments and releases (`plan_d`, as `Bench.play` keeps them)."""
    fr = frames_of(raw)
    marks = [tuple(m) for m in plan_d["marks"]]
    out = {"painted": len(fr), "gestures": len(marks)}
    if not fr:
        return out
    pres = presented(fr, tail)
    cl = classes(fr, pres, marks)
    for i, f in enumerate(fr):
        f["cls"] = cl[i][1]
        f["iv"] = cl[i][2]
        f["gi"] = cl[i][0]
        f["pres"] = pres[i]
    moving = [f for f in fr if f["cls"] in ("live", "coast", "glide")]
    out["gate"] = _st([f["iv"] for f in moving])
    for c in ("live", "coast", "glide", "settle"):
        fs = [f for f in fr if f["cls"] == c]
        out[c] = _st([f["iv"] for f in fs])
        out[c + "_ms"] = _st([f["total"] / 1000.0 for f in fs])
    copies = [f for f in fr if not (f["content"] or f["windows"] or f["home"] or f["other"])]
    out["copies"] = len(copies)
    # the first three presented intervals of every gesture
    by_g = {}
    for f in fr:
        if f["gi"] >= 0:
            by_g.setdefault(f["gi"], []).append(f)
    f3 = [max(x["iv"] for x in fs[:3]) for fs in by_g.values() if fs]
    if f3:
        out["first3"] = round(max(f3), 1)
        out["first3_med"] = round(statistics.median(f3), 1)
    # per press: its first presentation and its last frame before the next input
    lat, calm = [], []
    for gi, fs in by_g.items():
        m = marks[gi]
        lat.append(fs[0]["pres"] - m[0])
        calm.append(fs[-1]["pres"] - m[0])
    out["lat"] = _st(lat)
    out["calm"] = _st(calm)
    out["coalesced"] = sum(1 for gi in range(len(marks)) if gi not in by_g)
    # where the time went: each part's mean over the moving frames
    out["painted_ms"] = _st([f["total"] / 1000.0 for f in moving])
    # the outliers by name: the three longest moving intervals and their parts
    out["worst"] = [dict([("t", round(f["t0"] / 1000.0)), ("iv", round(f["iv"], 1)),
                          ("cls", f["cls"])] + [(k, round(f[k] / 1000.0, 1)) for k in (
                              "total", "content", "stamp", "windows", "home", "other",
                              "present", "copy")])
                    for f in sorted(moving, key=lambda f: -f["iv"])[:3]]
    if moving:
        sp = {}
        for k in ("total", "content", "stamp", "windows", "home", "other", "present", "copy"):
            sp[k] = round(sum(f[k] for f in moving) / 1000.0 / len(moving), 1)
        fences = [f["fence"] for f in moving if f["fence"] >= 0]
        sp["fence"] = round(sum(fences) / 1000.0 / len(fences), 1) if fences else None
        out["split"] = sp
    kinds = [f["kind"] for f in fr]
    out["shift_frac"] = round(kinds.count(1) / len(kinds), 2)
    out["rows_frac"] = round(kinds.count(2) / len(kinds), 2)
    # the view's motion
    pv = [f["probe"] for f in fr if f["probe"] != NONE_PROBE]
    if pv:
        out["moved"] = pv[-1] - pv[0]
        if px and plan_d.get("segs"):
            out.update(_finger(fr, plan_d, axis))
    if steps is not None:
        out["steps"] = steps
        out["steps_asked"] = sum(1 for m in marks if m[1] == "ball")
    return out


def _finger(fr, plan_d, axis):
    """The scheduled finger's travel against the content's at each live
    presentation (px; the content scrolls against the finger), and the
    release velocities."""
    i0 = 0 if axis == "h" else 1
    segs = plan_d["segs"]
    t0, x0, y0, x1, y1, ms = segs[0]
    start = (x0, y0)[i0]
    p0 = plan_d.get("p0")
    live = [f for f in fr if f["cls"] == "live" and f["probe"] != NONE_PROBE]
    if p0 is None or p0 == NONE_PROBE:
        p0 = live[0]["probe"] if live else None
    out = {}
    if p0 is None or not live:
        return out
    lags = []
    for f in live:
        t = f["pres"]
        u = min(1.0, max(0.0, (t - t0) / max(1, ms)))
        finger = (x0 + (x1 - x0) * u, y0 + (y1 - y0) * u)[i0] - start
        lags.append(abs(-finger - (f["probe"] - p0)))
    out["lag"] = _st(lags)
    rel = plan_d.get("releases") or []
    if rel and rel[0][1] > 0 and len(live) >= 3:
        a, b = live[-3], live[-1]
        dt = b["pres"] - a["pres"]
        if dt > 0:
            out["v_finger"] = round(rel[0][1], 2)
            out["v_content"] = round(abs(b["probe"] - a["probe"]) / dt, 2)
    return out


# -- the arithmetic ----------------------------------------------------------------

# docs/scroll_and_text_2026-10.md §14's terms that are not this run's to
# measure: the design's configuration and #113's verb costs, (low, high) ms.
DIRECT_STAMP = 1.1
SHIFT_MS = {"p4": (0.04, 0.04), "s3": (4.3, 5.0), "one": (0.0, 0.0)}
FENCE_MS = {"p4": (0.0, 5.0)}
CONTENTION = {"p4": 6.0}       # the band's raster beside a landing 2D-DMA
BAND = 0.10                    # a band's share of R where no pixel probe says it
FOCUS_ROWS = 0.15              # a key step's focus rows (old, new, the rows after)
LINE = 0.06                    # a typed line's row and the caret's

P4_BOARDS = ("p4", "guition_p4")
S3_BOARDS = ("tdeck", "guition_s3")

VERBS = ("Settings", "Achievements", "Licences", "Files", "Get Carts", "Storybook",
         "My Art", "Calc", "Appearance")
FIELDS_SURF = ("Blocks number prompt", "Blocks name prompt", "Cart info", "New script",
               "Settings Name", "Notes new-note name")
EDITOR_TEXT = ("Code", "Theme Config text")
PANES = ("Blocks outline", "Config cards", "Music steps", "Map")
HOME = ("home carousel", "home grid", "home icons", "PROJECTS")


def phase_of(surface, inp, board):
    """(phase, kind) of the design phase that reaches `surface` x `inp` on
    `board` (§15's table), kind the frame it makes: "shift" (a translation
    and its band), "rows" (the focus rows; a glide's frames shift), "line"
    (a typed row), "whole" (a page change: the stamp alone goes); (None,
    None) where no phase reaches it."""
    base = surface.replace(" full screen", "")
    p4 = board in P4_BOARDS
    motion = inp in ("fling", "drag")
    typing = inp.startswith("type")
    if base in VERBS:
        ph = "P1d" if p4 else "P1c"
        return ph, "shift" if motion else "line" if typing else "rows"
    if base in FIELDS_SURF:
        return "P3", "line" if typing else "rows"
    if base in EDITOR_TEXT:
        if inp in ("find", "paste"):
            return "P4", "whole"
        return "P4", "shift" if motion else "line" if typing else "rows"
    if base in PANES:
        return "P5", "shift" if motion else "rows"
    if base in HOME:
        if "tabs" in inp:
            return None, None
        return "P6", "shift" if motion else "rows"
    if base == "Notes":
        return "P8", "shift" if motion else "line" if typing else "rows"
    if base == "My Art cover landing":
        return "P0", "rows"
    return None, None


def _con(f, home):
    """A frame's content draw: the console home's on a home row of the
    windowed tier, where the home is its own layer."""
    return f["content"] or (f["home"] if home else 0)


def _tier(board):
    return "p4" if board in P4_BOARDS else "s3" if board in S3_BOARDS else "one"


def expected(row, null_row, board):
    """§14's arithmetic for `row`, a frame at a time over its moving frames:

        total - content - stamp + DIRECT_STAMP (where it stamped) + pass
          + shift + fence (shift frames) + rows

    `pass` is the null-floor run's content median for the frame's class
    (40% of the content where there is no null run), the raster the content
    less its pass, the rows that raster's share for the frame's kind (x6
    beside a landing DMA on a P4, never more than the raster; a frame that
    moved a view or more paints whole). Presented: the frame's interval with its
    paint swapped for the expectation, never under the panel's own span.
    Returns {"phase", "kind", "pass", "lo", "hi", "painted_lo", "painted_hi"}
    (p90s), or None."""
    ph, kind = phase_of(row["surface"], row["input"], board)
    if ph is None:
        return None
    fr = frames_of(row.get("raw") or [])
    if not fr:
        return None
    a = analyse(row["raw"], row["plan"], row.get("tail"), px=row.get("px", False),
                axis=row.get("axis", "v"))
    pres = presented(fr, row.get("tail"))
    cl = classes(fr, pres, [tuple(m) for m in row["plan"]["marks"]])
    pass_by = {}
    home = row["surface"].startswith("home")
    if null_row is not None and null_row.get("raw"):
        nf = frames_of(null_row["raw"])
        npres = presented(nf, null_row.get("tail"))
        ncl = classes(nf, npres, [tuple(m) for m in null_row["plan"]["marks"]])
        for c in ("live", "coast", "glide", "settle"):
            v = [_con(f, home) / 1000.0 for f, k in zip(nf, ncl) if k[1] == c and _con(f, home)]
            if v:
                pass_by[c] = statistics.median(v)
        allv = [_con(f, home) / 1000.0 for f in nf if _con(f, home)]
        if allv:
            pass_by["all"] = statistics.median(allv)
    tier = _tier(board)
    spans = [f["span"] / 1000.0 for f in fr if f["span"] > 0]
    floor = statistics.median(spans) if spans else 0.0
    view = (row.get("rect") or (0, 0, 0, 0))[3 if row.get("axis", "v") == "v" else 2] or 0
    lo, hi, plo, phi, passes = [], [], [], [], []
    for i, (f, k) in enumerate(zip(fr, cl)):
        if k[1] not in ("live", "coast", "glide"):
            continue
        tot = f["total"] / 1000.0
        con = _con(f, home) / 1000.0
        stamp = f["stamp"] / 1000.0
        ps = pass_by.get(k[1], pass_by.get("all"))
        if ps is None:
            ps = 0.4 * con
        ps = min(ps, con) if con else ps
        passes.append(ps)
        raster = max(0.0, con - ps)
        base = tot - con - stamp + (DIRECT_STAMP if stamp > 0 else 0.0)
        if kind == "whole":
            e = (base + con, base + con)
        else:
            shifting = kind == "shift" or (kind == "rows" and k[1] == "glide")
            if shifting:
                frac = BAND
                if row.get("px") and view and i > 0 and f["probe"] != NONE_PROBE \
                        and fr[i - 1]["probe"] != NONE_PROBE:
                    frac = min(1.0, abs(f["probe"] - fr[i - 1]["probe"]) / float(view))
                if frac >= 1.0:
                    e = (base + con, base + con)     # moved a view or more: no shift
                else:
                    # the band beside a landing DMA, never more than the raster
                    rows = min(raster, raster * frac * CONTENTION.get(tier, 1.0))
                    s = SHIFT_MS[tier]
                    fe = FENCE_MS.get(tier, (0.0, 0.0))
                    e = (base + ps + s[0] + fe[0] + rows, base + ps + s[1] + fe[1] + rows)
            else:
                frac = LINE if kind == "line" else FOCUS_ROWS
                rows = raster * frac
                e = (base + ps + rows, base + ps + rows)
        gap = (k[2] if k[2] is not None else tot) - tot
        lo.append(max(gap + e[0], floor))
        hi.append(max(gap + e[1], floor))
        plo.append(e[0])
        phi.append(e[1])
    if not lo:
        return None
    return {"phase": ph, "kind": kind, "pass": round(statistics.median(passes), 1),
            "lo": _st(lo)["p90"], "hi": _st(hi)["p90"],
            "painted_lo": _st(plo)["p90"], "painted_hi": _st(phi)["p90"],
            "today": (a.get("gate") or {}).get("p90")}


# -- the bench ---------------------------------------------------------------------

class Bench:
    def __init__(self, b, board, out, null=True):
        self.b = b
        self.board = board
        self.out = out
        self.null = null
        self.matrix = board in MATRIX_BOARDS
        self.rows = []
        if out and os.path.exists(out):
            with open(out) as f:
                self.rows = json.load(f)
        self.ex(DEVICE, strict=True)
        self.windowed = bool(self.val("bool(getattr(ws.wm, 'has_desk', False))"))
        self.speaks = bool(self.val("ws._g['_ib_speaks']()"))
        self.W, self.H = self.val("(ws.sys_canvas.w, ws.sys_canvas.h)")
        # an image built before moy_ui.api_offset has no verbs page probe
        self.offset = bool(self.val("hasattr(__import__('moy_ui'), 'api_offset')"))
        if not self.offset:
            print("  ! this image has no moy_ui.api_offset: verbs pages measure no "
                  "finger against content (an older image than this tree?)", flush=True)

    def ex(self, code, settle=0.0, strict=False):
        """Statements in the namespace the DEVICE half was defined in
        (`ws._g`): a one-line snippet is a `py` line, which evaluates in a
        fresh one, so it goes through `exec` there."""
        code = code.strip("\n")
        if "\n" not in code:
            code = "exec(%r, ws._g)" % code
        ok = self.b.pyexec(code, timeout=90, strict=strict)
        if not ok:
            print("      ! %s: %s" % (code.strip().splitlines()[0][:70], self.b.last_error),
                  flush=True)
        if settle:
            self.b.drain(settle)
        return ok

    def val(self, expr, timeout=30):
        return self.b.pyval(expr, timeout=timeout)

    def save(self):
        if self.out:
            with open(self.out, "w") as f:
                json.dump(self.rows, f)

    def setup(self, theme=None):
        self.ex(SNAPSHOT, strict=True)
        if theme == "picture":
            # a picture wallpaper the size of the glass, over the theme in
            # force; the wallpaper it had goes back at cleanup
            self.ex(PICTURE_MAKE, settle=2.0, strict=True)
        elif theme:
            # shown for the run only (persist=False), the theme's own
            # ground under the home: no wallpaper cart over it
            self.ex("ws._ib_s['tn'] = ws.look.theme_name\n"
                    "ws.look.set_theme(%r, persist=False)\n"
                    "ws.look.select_wallpaper(None, persist=False)\n"
                    "ws.mark_dirty()" % theme, settle=1.0, strict=True)
        self.ex(SCRATCH_MAKE % (STAR, SCRATCH, COIN, SCRATCH + " Blocks",
                                NOTE_FILE, NOTE, SCRIPT_FILE, SCRIPT, DRAWINGS, SCRATCH),
                settle=1.0, strict=True)

    def cleanup(self):
        try:
            self.b.leave_cart()
        except Exception as exc:  # noqa: BLE001 -- the rest of the cleanup still runs
            print("      ! leave_cart: %s" % exc, flush=True)
        for step in CLEANUP_STEPS:
            self.ex(step, settle=0.3)
        self.ex(UNINSTALL)
        self.b.drain(1.0)

    def settle_covers(self, still=0.5, limit=30.0):
        """Until the cover pipeline rests: the scratch carts' rescan dropped
        every cover, and each painted frame landing the next one moves the
        covers' gen, which repaints a home's band whole -- a cost of the
        setup, not of the row. The home is painted until the gen holds."""
        t_end = time.time() + limit
        last, same = None, 0
        while time.time() < t_end and same < 3:
            g = self.val("(ws.mark_dirty(), ws.covers.gen)[1]")
            same = same + 1 if g == last else 0
            last = g
            self.b.drain(still)

    def content(self):
        """The top window's content rect in glass pixels, else the glass."""
        if self.windowed:
            st = self.b.state()
            keys = [k for k in st.get("order", []) if k != "desktop"]
            if keys and keys[-1] in st.get("wins", {}):
                w = st["wins"][keys[-1]]
                return (w[0] + 1, w[1] + 1 + w[4], w[2], w[3] - w[4])
        return (0, 0, self.W, self.H)

    def play(self, plan, probe, null=False):
        """Arm the schedule, wait it out, read the frames back."""
        b = self.b
        ev = plan.sorted()
        lam = ("lambda: (%s)" % probe) if probe else "None"
        if not self.ex("_ib_arm(%r, %s, %d, %r)" % (ev, lam, plan.end(), null)):
            return None
        b.drain(plan.end() / 1000.0 + 0.3)
        for _ in range(40):
            if self.val("ws._ib.done"):
                break
            b.drain(0.25)
        tail = self.val("ws._g['_ib_tail']()")
        if not tail:
            return None
        n = tail[0]
        raw = []
        for a in range(0, n, 25):
            got = self.val("ws._g['_ib_take'](%d, %d)" % (a, min(n, a + 25)), timeout=40)
            if got is None:
                return None
            raw += got
        return {"raw": raw, "tail": tail, "steps": tail[1] if self.matrix else None}

    def row(self, surf, inp, rect):
        plan = build_plan(inp, surf, rect, self.matrix)
        probe = surf.probe_expr(rect, self)
        plan_d = {"marks": plan.marks, "segs": plan.segs, "releases": plan.releases}
        got = self.play(plan, probe)
        if got is None:
            print("  %-34s %-10s (no record)" % (surf.name, inp), flush=True)
            return None
        tail = got["tail"]
        plan_d["p0"] = tail[2]
        ball = inp.startswith("ball")
        row = {"surface": surf.name, "input": inp, "board": self.board,
               "windowed": self.windowed, "api_offset": self.offset, "speaks": self.speaks, "rect": rect,
               "axis": surf.axis, "px": surf.px, "plan": plan_d, "tail": tail,
               "raw": got["raw"]}
        row.update(analyse(got["raw"], plan_d, tail, got["steps"] if ball else None,
                           rect, surf.px, surf.axis))
        if self.null:
            ng = self.play(plan, probe, null=True)
            if ng is not None:
                nrow = {"surface": surf.name, "input": inp, "plan": plan_d,
                        "tail": ng["tail"], "raw": ng["raw"], "rect": rect,
                        "axis": surf.axis, "px": surf.px}
                row["null"] = nrow
        ex = expected(row, row.get("null"), self.board)
        if ex is not None:
            row["expected"] = ex
        self.rows = [r for r in self.rows if (r["surface"], r["input"]) != (surf.name, inp)]
        self.rows.append(row)
        self.save()
        print("  " + line_of(row), flush=True)
        return row

    def split(self, surf, rect):
        """The surface's long input under PERF DIAG: the LOOP / PUMP / PERF
        lines, a run of its own."""
        plan = build_plan("long", surf, rect, self.matrix)
        b = self.b
        b.cmd("diag 1")
        n0 = len(b.lines)
        self.ex("_ib_arm(%r, None, %d)" % (plan.sorted(), plan.end()))
        b.drain(plan.end() / 1000.0 + 2.5)
        b.cmd("diag 0")
        keep = [ln.strip() for ln in b.lines[n0:]
                if any(k in ln for k in ("LOOP ", "PUMP ", "PERF ", "HITCH "))]
        print("%s\n  %s" % (surf.name, "\n  ".join(keep)), flush=True)
        self.rows = [r for r in self.rows if (r["surface"], r["input"]) != (surf.name, "split")]
        self.rows.append({"surface": surf.name, "input": "split", "split": keep})
        self.save()


# -- the surfaces ------------------------------------------------------------------

SCROLL = ("fling", "drag", "key_steps", "key_held", "key_20", "ball_slow", "ball_fast")
TEXT_IN = ("fling", "drag", "key_steps", "key_held", "type", "type_fast", "type_line",
           "ball_slow", "ball_fast", "paste")


class Surface:
    """A scrolling or text surface: `setup` console steps ((code, settle s)),
    the `probe` (console code read each painted frame: an offset, a caret, a
    selection; `px` when it is a pixel offset the finger moves, and
    "OFF:<app>" for a verbs page's offset under the finger), the `axis` it
    scrolls on and the `keys` that step it (forward, back), the `inputs` it
    takes, its `teardown`, the finger's `rect` as fractions of the content
    rect ("pane": inside the desk Editor's focused Code or theme Config pane,
    which a press outside would leave; CODE_RECT on a handheld), and `where` it exists ("desk": the windowed tier, "full": a
    windowed tier's window made full screen)."""

    def __init__(self, name, setup, probe=None, px=False, axis="v", keys=("down", "up"),
                 inputs=SCROLL, teardown=None, rect=None, where=None, text=None):
        self.name = name
        self.setup = setup
        self.probe = probe
        self.px = px
        self.axis = axis
        self.keys = keys
        self.inputs = inputs
        self.teardown = teardown
        self.rect = rect
        self.where = where
        self.text = text or {}

    def probe_expr(self, rect, bench):
        p = self.probe
        if p is None or not p.startswith("OFF:"):
            return p
        app = p[4:]
        cx, cy, _w, _h = bench._crect
        x = rect[0] + rect[2] // 2 - cx
        y = rect[1] + rect[3] // 2 - cy
        return "_ib_off(%r, %d, %d)" % (app, x, y)


def _home(arr):
    return [("_ib_home(%r)" % arr, 3.0)]


def _app(app):
    return [("_ib_app(%r)" % app, 4.0)]


def _ed(title, tab=None, key=None):
    return [("_ib_editor(%r, %r, %r)" % (title, tab, key), 4.0),
            ("_ib_window()", 1.0),
            ("_ib_focus(%r)" % tab, 1.0)]


_CODE0 = ("ws.editor.row = 0\nws.editor.col = 0\nws.editor.top = 0\n"
          "ws._ib_clip = '\\n'.join('# pasted line %d of sixty' % i for i in range(60))\n"
          "_cl = getattr(ws, 'clipboard', None)\n"
          "_cl is not None and _cl.put_text(ws._ib_clip[:4000])\n"
          "ws.editor.clipboard = ws._ib_clip\nws.mark_dirty()", 1.0)
_FIND = ("ws.code_layer._find_open or ws.code_layer._toggle_find()\n"
         "ws.code_layer._find_q = ''\nws.mark_dirty()", 1.0)
_UNFIND = "ws.code_layer._find_open and ws.code_layer._toggle_find()"
_EDPROBE = "ws.editor.top * 100000 + ws.editor.row * 1000 + ws.editor.col"
_FULL = [("_ib_full()", 1.5)]
# The sound under the Music tab grown to 32 steps in memory (never saved), so
# its rows scroll; put back after.
_MUSIC32 = ("_me = ws.music_ui.musicedit\n_s = _me.cur_sfx()\nws._ib_n0 = len(_s.steps)\n"
            "_s.steps[ws._ib_n0:] = [[36 + _i % 12, _i % 8, 1 + _i % 7] for _i in range(32 - ws._ib_n0)]\n"
            "_me.step = 0\nws.mark_dirty()", 1.0)
_MUSIC0 = ("_me = ws.music_ui.musicedit\n_s = _me.cur_sfx()\n"
           "del _s.steps[getattr(ws, '_ib_n0', len(_s.steps)):]\n_me.step = 0\nws.mark_dirty()")
CODE_RECT = (0.02, 0.18, 0.58, 0.62)
LEFT_PANE = (0.01, 0.17, 0.59, 0.71)
RIGHT_PANE = (0.385, 0.17, 0.6, 0.71)
PICKER = (0.055, 0.27, 0.285, 0.33)
_NOTES = [("_ib_desk()", 1.0), ("@run Notes", 4.0),
          ("ws._ib_last = ws.player.ns['prefs'].get('last')\n"
           "ws.player.ns['_open'](ws._ib_note)\nws.mark_dirty()", 2.0)]
_NOTES_OUT = ("_p = ws.player.ns['prefs']\n"
              "(_p.set('last', ws._ib_last) if ws._ib_last is not None else _p.clear('last'))")


def surfaces():
    s = []
    for arr in ("carousel", "grid", "icons"):
        h = arr == "carousel"
        s.append(Surface("home " + arr, _home(arr), "ws.launcher._region.offset", px=True,
                         axis="h" if h else "v", keys=("right", "left") if h else ("down", "up"),
                         rect="band"))
    s.append(Surface("PROJECTS", [("_ib_desk()\nws.open_projects()", 6.0)],
                     "ws.projects_grid._region.offset", px=True, axis="h",
                     keys=("right", "left"), where="desk", rect="projects"))
    for app, label in (("settings", "Settings"), ("files", "Files"), ("getcarts", "Get Carts"),
                       ("storybook", "Storybook"), ("myart", "My Art"), ("calc", "Calc"),
                       ("appearance", "Appearance")):
        ins = SCROLL + (("type",) if app == "calc" else ())
        txt = {"type": ("12+34*5=\r", 10)} if app == "calc" else None
        s.append(Surface(label, _app(app), "OFF:" + app, px=True, inputs=ins, text=txt))
        s.append(Surface(label + " full screen", _app(app) + _FULL, "OFF:" + app, px=True,
                         inputs=("fling", "drag", "key_steps", "key_20"), where="full",
                         teardown="_ib_full()"))
    for page, label in (("achievements", "Achievements"), ("licences", "Licences")):
        s.append(Surface(label, _app("settings") + [
            ("ws.settings_layer.open_page(%r)" % page, 2.0)], "OFF:settings", px=True,
            inputs=("fling", "drag", "key_steps", "key_20", "ball_slow", "ball_fast")))
    s.append(Surface("Settings Name", _app("settings") + [
        ("ws.settings_layer._want = 'Name'\nws.mark_dirty()", 1.0)],
        inputs=("type", "type_fast"), text={"type": ("Bench Scratch", 10),
                                            "type_fast": (" again", 20)},
        teardown="ws.settings_layer._name = None"))
    s.append(Surface("Code", _ed(SCRATCH, "code") + [_CODE0], _EDPROBE,
                     inputs=TEXT_IN, rect=CODE_RECT, teardown=_UNFIND))
    s.append(Surface("Code find", _ed(SCRATCH, "code") + [_FIND], _EDPROBE,
                     inputs=("find",), rect=CODE_RECT, teardown=_UNFIND))
    s.append(Surface("Code full screen", _ed(SCRATCH, "code") + _FULL, _EDPROBE,
                     inputs=("fling", "drag", "key_steps", "type_fast"), where="full",
                     rect=(0.02, 0.17, 0.5, 0.66), teardown="_ib_full()"))
    s.append(Surface("Code long line", _ed(SCRATCH, "code") + [
        ("ws.editor.lines[0] = 'x' * 200 + ws.editor.lines[0]\nws.editor.row = 0\n"
         "ws.editor.col = 0\nws.mark_dirty()", 1.0)], "ws.editor.left * 1000 + ws.editor.col",
        axis="h", keys=("right", "left"), inputs=("key_steps", "ball_slow", "ball_fast"),
        rect=CODE_RECT))
    s.append(Surface("Theme Config text", [("_ib_desk()\nws.edit_theme()", 4.0),
                                           ("ws._ib_theme0 = ws.editor.text()\n"
                                            "ws.editor.row = 0\nws.editor.col = 0\n"
                                            "ws.editor.top = 0\nws.mark_dirty()", 1.0)],
                     _EDPROBE, inputs=("fling", "drag", "key_steps", "key_held", "type",
                                       "type_fast", "ball_slow", "ball_fast"),
                     rect="pane",
                     teardown=("_d = ws.editor_app.theme\n_d.ed.set_text(ws._ib_theme0)\n"
                               "_d.ed.dirty = False\n_d.saved = True\n"
                               "ws.history.drop_code_history()")))
    s.append(Surface("Blocks outline", _ed(SCRATCH + " Blocks", "blocks", "code"),
                     rect=LEFT_PANE))
    s.append(Surface("Config cards", _ed(SCRATCH + " Blocks", "cards", "settings"),
                     rect=LEFT_PANE))
    s.append(Surface("Blocks number prompt", _ed(SCRATCH + " Blocks", "blocks", "code") + [
        ("ws.block_ui._blk_open_kbd('num', cur=5, block={'t': 'set', 'p': {}}, "
         "slot='value', allow_block=True)\nws.mark_dirty()", 1.5)],
        inputs=("type", "type_fast"), text={"type": ("1234567890", 10),
                                            "type_fast": ("0987654321", 20)},
        teardown="ws.block_ui.blk_kbd and ws.block_ui._blk_kbd_cancel()\nws.mark_dirty()"))
    s.append(Surface("Blocks name prompt", _ed(SCRATCH + " Blocks", "blocks", "code") + [
        ("ws.block_ui._blk_open_kbd('var', var='score', slot_target=None)\nws.mark_dirty()",
         1.5)], inputs=("type", "type_fast"), text={"type": ("abcdefghij", 10),
                                                    "type_fast": ("klmnopqrst", 20)},
        teardown="ws.block_ui.blk_kbd and ws.block_ui._blk_kbd_cancel()\nws.mark_dirty()"))
    s.append(Surface("Cart info", _ed(SCRATCH + " Blocks", "cards", "settings") + [
        ("ws.cards_layer._open_meta()\nws.mark_dirty()", 2.0)],
        inputs=("type", "type_fast", "key_steps"), text={"type": ("Hello world", 10),
                                                         "type_fast": ("Hello again", 20)},
        teardown="ws.cards_layer.prompt and ws.cards_layer._close_prompt()"))
    s.append(Surface("New script", _ed(SCRATCH + " Blocks", "cards", "settings") + [
        ("ws.cards_layer._open_newf()\nws.mark_dirty()", 2.0)],
        inputs=("type", "type_fast"), text={"type": ("helpers", 10),
                                            "type_fast": ("more_helpers", 20)},
        teardown="ws.cards_layer.prompt and ws.cards_layer._close_prompt()"))
    s.append(Surface("Pause card", [("_ib_desk()", 1.0), ("@run Star Catcher", 4.0),
                                    ("ws.pause.show()\nws.mark_dirty()", 1.5)],
                     "ws.pause.sel", inputs=("key_steps", "key_20"),
                     teardown="ws.pause.open and ws.pause.close()\nws.exit()"))
    s.append(Surface("Music steps", _ed(SCRATCH, "music", "sound"), axis="h",
                     keys=("right", "left"), rect=LEFT_PANE,
                     inputs=("key_steps", "key_held", "key_20", "drag", "ball_slow")))
    # the same over 32 steps; "v" walks the steps' cursor, so its rows scroll
    s.append(Surface("Music steps 32", _ed(SCRATCH, "music", "sound") + [_MUSIC32], axis="h",
                     keys=("right", "left"), rect=LEFT_PANE, teardown=_MUSIC0,
                     inputs=("key_steps", "key_held", "key_20", "drag", "ball_slow")))
    s.append(Surface("Music steps v", _ed(SCRATCH, "music", "sound") + [_MUSIC32],
                     "ws.music_ui.musicedit.step", rect=LEFT_PANE, teardown=_MUSIC0,
                     inputs=("key_steps", "key_held", "key_20")))
    s.append(Surface("Sprite pickers", _ed(SCRATCH, "paint", "art") + [
        ("ws.paint_layer._open_pickers()\nws.mark_dirty()", 1.5)],
        rect="picker", teardown="ws.paint_layer._pop = False\nws.mark_dirty()"))
    s.append(Surface("Map", _ed(SCRATCH, "map", "art"), rect=RIGHT_PANE,
                     inputs=("key_steps", "key_held", "key_20", "drag", "ball_slow")))
    s.append(Surface("Notes", _NOTES, "(lambda h: h.ed.top * 100000 + h.ed.row * 1000 + "
                     "h.ed.col if h else -1)(ws.player._focus_editor)",
                     inputs=("fling", "drag", "key_steps", "key_held", "type", "type_fast",
                             "type_line", "ball_slow"),
                     rect=(0.4, 0.2, 0.55, 0.6), teardown=_NOTES_OUT + "\nws.exit()"))
    s.append(Surface("Text console", [("_ib_desk()", 1.0),
                                      ("ws.run_script('docs', ws._ib_script)", 4.0)],
                     "ws.script_console.top", inputs=("fling", "drag", "type", "type_fast",
                                                       "ball_slow"),
                     teardown="ws.exit()"))
    return s


# -- the run -----------------------------------------------------------------------

def _rect(bench, surf):
    x, y, w, h = bench.content()
    bench._crect = (x, y, w, h)
    r = surf.rect
    if r == "band":
        b = bench.val("ws.launcher.band_rect()")
        return tuple(b) if b else (x, y, w, h)
    if r == "projects":
        v = bench.val("ws.projects_grid._region.view")
        return (x + v[0], y + v[1], v[2], v[3]) if v else (x, y, w, h)
    if r == "pane":
        b = bench.val("(lambda d, c: c and c[4])(ws.editor_desk, ws.editor_desk is not None "
                      "and ws.editor_desk.active() and ws.editor_desk._own_pane())")
        if b:
            return (x + b[0] + b[2] // 16, y + b[1] + b[3] // 16, b[2] * 7 // 8, b[3] * 7 // 8)
        r = CODE_RECT                   # a handheld's Editor: the code fills the content
    if r == "picker":
        b = bench.val("ws._g['_ib_picker']()")
        if b:
            return (x + b[0], y + b[1], b[2], b[3])
        r = PICKER
    if r is None:
        return (x, y, w, h)
    return (x + int(w * r[0]), y + int(h * r[1]), int(w * r[2]), int(h * r[3]))


def run_surface(bench, surf, inputs=None, split=False):
    if surf.where in ("desk", "full") and not bench.windowed:
        return
    for code, settle in surf.setup:
        if code.startswith("@"):
            bench.b.cmd(code[1:])
            bench.b.drain(settle)
        else:
            bench.ex(code, settle)
    if surf.name in HOME:
        bench.settle_covers()
    rect = _rect(bench, surf)
    try:
        if split:
            bench.split(surf, rect)
            return
        for inp in surf.inputs:
            if inputs and inp not in inputs:
                continue
            if inp.startswith("ball") and not bench.matrix:
                continue
            bench.row(surf, inp, rect)
    finally:
        if surf.teardown:
            bench.ex(surf.teardown, 1.0)


def _port(board):
    try:
        return subprocess.check_output([sys.executable, os.path.join(HERE, "board.py"),
                                        board, "port"], text=True).strip()
    except subprocess.CalledProcessError:
        raise SystemExit("%s: its port is held or absent (`tools/board.py ports`)" % board)


# -- the report --------------------------------------------------------------------

def _v(st, k="p90"):
    return "-" if not st else "%g" % st[k]


def line_of(r):
    ex = r.get("expected") or {}
    sp = r.get("split") or {}
    return ("%-28s %-10s n=%-4s gate=%-5s live=%-5s coast=%-5s glide=%-5s first3=%-5s "
            "lat=%s calm=%s copies=%s content=%s pass=%s lag=%s %s%s" % (
                r["surface"], r["input"], r.get("painted"), _v(r.get("gate")),
                _v(r.get("live")), _v(r.get("coast")), _v(r.get("glide")), r.get("first3", "-"),
                _v(r.get("lat"), "med"), _v(r.get("calm"), "max"), r.get("copies"),
                sp.get("content", "-"), ex.get("pass", "-"), _v(r.get("lag")),
                ("steps %s/%s " % (r["steps"], r["steps_asked"])) if "steps" in r else "",
                ("-> %s %s-%s" % (ex["phase"], ex["lo"], ex["hi"])) if ex else ""))


def report(path, md=True):
    with open(path) as f:
        rows = [r for r in json.load(f) if r.get("input") != "split"]
    rows.sort(key=lambda r: -((r.get("gate") or {}).get("p90") or 0))
    out = ["| surface | input | gate p90 | live / coast / glide p90 | first 3 max | "
           "lat med / calm max | painted p90 | content / pass | shift / rows | lag p90 px | "
           "kept up | phase: expected p90, presented (painted) |",
           "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        ex = r.get("expected")
        sp = r.get("split") or {}
        kept = []
        if "steps" in r:
            kept.append("%s of %s steps" % (r["steps"], r["steps_asked"]))
        if r.get("coalesced"):
            kept.append("%s shown with the next" % r["coalesced"])
        if r.get("v_finger"):
            kept.append("release %s of %s px/ms" % (r.get("v_content"), r["v_finger"]))
        frac = ("%d%% / %d%%" % (100 * r["shift_frac"], 100 * r["rows_frac"])
                if r.get("speaks") and "shift_frac" in r else
                "-" if r.get("speaks") else "None")      # "-": no frame painted
        out.append("| %s | %s | %s | %s / %s / %s | %s | %s / %s | %s | %s / %s | %s | %s | %s | %s |"
                   % (r["surface"], r["input"], _v(r.get("gate")), _v(r.get("live")),
                      _v(r.get("coast")), _v(r.get("glide")), r.get("first3", "-"),
                      _v(r.get("lat"), "med"), _v(r.get("calm"), "max"),
                      _v(r.get("painted_ms")),
                      sp.get("content") or (sp.get("home") if r["surface"].startswith("home")
                                             else sp.get("content", "-")),
                      ex["pass"] if ex else "-", frac,
                      _v(r.get("lag")), ", ".join(kept) or "-",
                      ("%s: %s-%s (painted %s-%s)" % (
                          ex["phase"], ex["lo"], ex["hi"], ex.get("painted_lo"),
                          ex.get("painted_hi"))) if ex else "-"))
    over = [r for r in rows if ((r.get("gate") or {}).get("p90") or 0) > TARGET_MS]
    if over:
        out += ["", "The three longest moving intervals of each row over %g ms: t ms into the "
                "row, its interval, class, then the frame's parts (ms)." % TARGET_MS, ""]
        for r in over:
            out.append("- %s / %s: %s" % (r["surface"], r["input"], "; ".join(
                "t%s %s %s (total %s = content %s, stamp %s, windows %s, home %s, other %s, "
                "present %s, copy %s)" % (w["t"], w["iv"], w["cls"], w["total"], w["content"],
                                         w["stamp"], w["windows"], w["home"], w["other"],
                                         w["present"], w["copy"])
                for w in r.get("worst") or ())))
    return "\n".join(out)


def reanalyse(path):
    with open(path) as f:
        rows = json.load(f)
    for r in rows:
        if not r.get("raw"):
            continue
        r.update(analyse(r["raw"], r["plan"], r.get("tail"),
                         r.get("steps"), r.get("rect"), r.get("px", False), r.get("axis", "v")))
        ex = expected(r, r.get("null"), r.get("board", ""))
        if ex is not None:
            r["expected"] = ex
    with open(path, "w") as f:
        json.dump(rows, f)


def selected(only=(), inputs=(), rows=None):
    """The surfaces `--only` (lower-case substrings), `--inputs` and `--rows`
    pick, each with the inputs it plays (None: every one it takes)."""
    for surf in surfaces():
        if only and not any(o in surf.name.lower() for o in only):
            continue
        want = list(inputs) or None
        if rows:
            if surf.name not in rows:
                continue
            want = [i for i in rows[surf.name] if not inputs or i in inputs]
            if not want:
                continue
        yield surf, want


def planned(board, windowed, only=(), inputs=(), rows=None):
    """The (surface, input) rows a run with that selection plays on `board`:
    `selected` less what `run_surface` skips there."""
    out = []
    for surf, want in selected(only, inputs, rows):
        if surf.where in ("desk", "full") and not windowed:
            continue
        for inp in surf.inputs:
            if want and inp not in want:
                continue
            if inp.startswith("ball") and board not in MATRIX_BOARDS:
                continue
            out.append((surf.name, inp))
    return out


def parse_rows(spec):
    """`SURFACE:INPUT,...` as {surface: [input, ...]}, in the order given."""
    rows = {}
    for item in spec.split(","):
        if not item.strip():
            continue
        surf, sep, inp = item.rpartition(":")
        if not sep or not surf.strip() or not inp.strip():
            raise SystemExit("--rows: %r is not SURFACE:INPUT" % item)
        rows.setdefault(surf.strip(), []).append(inp.strip())
    return rows


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--board")
    ap.add_argument("--out", help="the run's directory (its rows: BOARD_input.json)")
    ap.add_argument("--only", default="", help="surface names, comma separated (substring)")
    ap.add_argument("--inputs", default="", help="input names, comma separated")
    ap.add_argument("--rows", default="",
                    help="exact rows, comma separated SURFACE:INPUT (a gate's re-run); "
                         "with --only/--inputs, a row runs when it is in both")
    ap.add_argument("--no-null", action="store_true", help="skip the null-floor plays")
    ap.add_argument("--split", action="store_true",
                    help="each surface's long drag under PERF DIAG instead: LOOP/PUMP/PERF")
    ap.add_argument("--theme", help="a theme shown for the run, over its own ground "
                                    "(no wallpaper): `moy` a plain fill, `blocks` a backdrop; "
                                    "`picture`: a picture wallpaper over the theme in force")
    ap.add_argument("--report", help="a run's rows file: print its table")
    ap.add_argument("--reanalyse", action="store_true",
                    help="with --report: take every kept record's numbers again first")
    a = ap.parse_args()
    if a.report:
        if a.reanalyse:
            reanalyse(a.report)
        print(report(a.report))
        return
    if not a.board or not a.out:
        ap.error("--board and --out")
    from p4_autotest import P4Board, board_dirs
    os.makedirs(a.out, exist_ok=True)
    path = os.path.join(a.out, "%s_input.json" % a.board)
    b = P4Board(_port(a.board), board_dir=board_dirs()[a.board])
    only = [x.strip().lower() for x in a.only.split(",") if x.strip()]
    inputs = [x.strip() for x in a.inputs.split(",") if x.strip()]
    rows = parse_rows(a.rows)
    bench = None
    try:
        bench = Bench(b, a.board, path, null=not a.no_null)
        bench.setup(a.theme)
        for surf, want in selected(only, inputs, rows):
            print("== %s" % surf.name, flush=True)
            run_surface(bench, surf, want, a.split)
    finally:
        if bench is not None:
            bench.cleanup()
        b.close()
    if not a.split:
        print("\n" + report(path))


if __name__ == "__main__":
    main()
