#!/usr/bin/env python3
"""The memory census, driven from the host: who holds a console board's memory.

    tools/mem_census.py BOARD arm [lite|live]  # the next boots record their trace
    tools/mem_census.py BOARD disarm
    tools/mem_census.py BOARD boot             # reboot, wait, the trace and a snap
    tools/mem_census.py BOARD snap             # heaps now (one collect)
    tools/mem_census.py BOARD offheap          # moy_alloc's buffers by owner, and
                                               # what holds any the walk misses
    tools/mem_census.py BOARD session          # the scripted session, snaps around it
    tools/mem_census.py BOARD fit [TITLE]      # run a compiled cart (Doom): PSRAM at
                                               # the Player's fit check, did it load
    tools/mem_census.py BOARD scan             # the shelf scan again: its live cost
    tools/mem_census.py BOARD sram             # WiFi and BLE up: internal SRAM
    tools/mem_census.py BOARD launcher         # launcher build time, exit-to-launcher
    tools/mem_census.py BOARD pauses [TITLE]   # PERF's gc= field, diag on
    tools/mem_census.py BOARD cut              # DESTRUCTIVE live-bytes census; reboots
    tools/mem_census.py BOARD cut-boot         # the same on a fresh boot
    tools/mem_census.py BOARD fresh|after      # one protocol boot (below)

The board half is `device/mem_census.py`, whose header says what each reading
is. Every reading is one JSON object, printed and appended to `--out` (default
`/tmp/mem_census/BOARD.jsonl`) with the board, the verb and the wall time, so
a run of boots is one file to read back.

A protocol boot: `fresh` reboots, takes the trace, a snap, the off-heap owners
and the compiled cart's fit at the Player's start, then leaves it; `after`
reboots, runs the scripted session between two snaps, then the same owners and
fit. Every board command opens the port once for the whole verb, so lines the
board prints between commands are read, never lost.
"""

import argparse
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, ROOT)

import board as bd                                                  # noqa: E402

M = "__import__('mem_census')"

# The scripted session: each step is (kind, what, seconds). A cart is run by
# title and left with `ws.exit()`; an app is opened by id and left with
# `ws.go_home()`. Titles a store lacks are skipped and the reading says so.
SESSION = (
    ("cart", "Star Catcher", 6),
    ("cart", "Sky Run", 6),
    ("cart", "Hop Quest", 6),
    ("lua", None, 6),
    ("app", "artwork", 4),
    ("app", "files", 4),
    ("app", "calc", 3),
    ("settings", None, 3),
    ("editor", "Star Catcher", 4),
    ("cart", "Brick Siege", 6),
)

DOOM = "Doom"


def emit(out, board, verb, data):
    rec = {"board": board, "verb": verb, "t": time.strftime("%Y-%m-%dT%H:%M:%S"),
           "data": data}
    line = json.dumps(rec, sort_keys=True, default=repr)
    print(line)
    if out:
        os.makedirs(os.path.dirname(out), exist_ok=True)
        with open(out, "a") as f:
            f.write(line + "\n")
    return data


def val(b, expr, timeout=60):
    got = b.pyval(expr, timeout=timeout)
    if b.last_error:
        raise bd.BoardError("%s -> %s" % (expr[:100], b.last_error))
    return got


def run(b, code, timeout=60):
    if not b.pyexec(code, timeout=timeout):
        raise bd.BoardError("%s -> %s" % (code[:100], b.last_error))


def titles(b):
    return val(b, "[(c.get('title'), c.get('runtime')) for c in ws.carts.all]")


def settle_cart(b, title, secs, timeout=20.0):
    """Run `title`, let it play `secs`, return its state (cart, error,
    notice) as the board reports it."""
    line = b.cmd("run %s" % title.lower(), wait_for="REMOTE run", timeout=timeout)
    if line is None or "no cart match" in line:
        return {"title": title, "ran": False, "reply": line}
    b.drain(secs)
    st = b.state() or {}
    return {"title": title, "ran": True, "cart": st.get("cart"),
            "error": st.get("cart_error"), "notice": st.get("notice"),
            "frames": st.get("frames")}


def home(b):
    st = b.state() or {}
    if st.get("cart"):
        b.leave_cart()
    st = b.state() or {}
    stack = st.get("stack") or []
    if stack and stack[-1] not in ("launcher", "desk"):
        val(b, "ws.go_home() or 1")
        b.drain(0.8)
        st = b.state() or {}
    return st.get("stack")


def snap(b):
    return val(b, M + ".snap()")


# The walk's roots: the console, then what the frame loop holds beside it
# (the `py` verb's scope), so a buffer only the loop reaches is still named.
ROOTS = "{'ws': ws, 'game': game, 'comp': comp, 'boot': boot, 'touch': touch, 'pump': pump}"


def offheap(b):
    """The registry by owner; a buffer the walk does not reach is followed
    through gc.refs() to whatever holds it."""
    owners = val(b, M + ".offheap(%s)" % ROOTS, timeout=120)
    held_by = {}
    for addr, _n in (owners or {}).get("unowned", ()):
        held_by[addr] = val(b, M + ".referrers(%d)" % addr, timeout=120)
    return {"owners": owners, "pool": val(b, M + ".pool()"),
            "unowned_referrers": held_by}


def brief(b):
    """One step's figures: PSRAM and internal SRAM (total, free, largest,
    low-water), the registry, and the gc heap held/live/areas."""
    return val(b, "(lambda m: (m.caps(0x400), m.caps(0x800), m._registry(), "
                  "__import__('gc').areas(), (__import__('gc').collect(), "
                  "__import__('gc').mem_alloc())[1], m.pool()))(%s)" % M)


def trace(b):
    return {"marks": val(b, M + ".marks()"), "imports": val(b, M + ".imports()"),
            "growths": val(b, M + ".growths()"),
            "carts": val(b, "len(ws.carts.all)"),
            "root": val(b, "str(ws.carts_root)")}


# The Player's fit check reads the compiled runtime's memory(); this wrapper
# records what it returned, the moment it was asked, and the heaps then.
FIT_HOOK = """
import gc as _gc
_rt = ws.runtimes.get('wasm')
_mc = __import__('mem_census')
if _rt is not None and not hasattr(_rt, '_census_mem'):
    _rt._census_mem = _rt.memory
    _rt._census_seen = []
    def _mem(_rt=_rt, _mc=_mc):
        got = _rt._census_mem()
        _rt._census_seen.append((got, _mc.caps(0x400), _gc.areas() if hasattr(_gc, 'areas') else None))
        return got
    _rt.memory = _mem
if _rt is not None:
    _rt._census_seen = []
"""


def fit(b, title=DOOM, secs=8):
    """Run the compiled cart `title` from the launcher: what memory() said at
    the Player's fit check (free, largest), the PSRAM caps then, its footprint,
    and whether it loaded (no notice, no error, frames advancing)."""
    if not any(t == title for t, _r in titles(b)):
        return {"title": title, "on_store": False}
    run(b, FIT_HOOK)
    need = val(b, "(lambda c, r: r.footprint(c))([c for c in ws.carts.all "
                  "if c['title'] == %r][0], ws.runtimes['wasm'])" % title)
    st0 = b.state() or {}
    got = settle_cart(b, title, secs)
    seen = val(b, "ws.runtimes['wasm']._census_seen")
    f1 = (b.state() or {}).get("frames")
    got.update({"need": need, "seen": seen,
                "frames_moved": (f1 or 0) - (got.get("frames") or 0),
                "frames_before": st0.get("frames"),
                "lua_up": val(b, "ws.player._lua is not None")})
    home(b)
    return got


def lua_title(b):
    for t, r in titles(b):
        if r == "lua":
            return t
    return None


def session(b):
    steps = []
    for kind, what, secs in SESSION:
        t0 = time.time()
        if kind == "cart":
            if not any(t == what for t, _r in titles(b)):
                steps.append({"step": what, "skipped": "not on the store"})
                continue
            r = settle_cart(b, what, secs)
        elif kind == "lua":
            what = lua_title(b)
            if what is None:
                steps.append({"step": "lua", "skipped": "no Lua cart"})
                continue
            r = settle_cart(b, what, secs)
        elif kind == "app":
            ok = val(b, "ws.open_app(ws._apps_by_id[%r]) if %r in ws._apps_by_id "
                        "else None" % (what, what))
            b.drain(secs)
            r = {"opened": ok, "stack": (b.state() or {}).get("stack")}
        elif kind == "settings":
            b.open("settings")
            b.drain(secs)
            r = {"stack": (b.state() or {}).get("stack")}
        elif kind == "editor":
            ok = val(b, "(lambda c: ws.open_in_editor(c) or 1)([c for c in "
                        "ws.carts.all if c['title'] == %r][0])" % what)
            b.drain(secs)
            r = {"opened": ok, "stack": (b.state() or {}).get("stack")}
        r["back"] = home(b)
        b.drain(1.0)
        r.update({"step": "%s %s" % (kind, what), "s": round(time.time() - t0, 1),
                  "after": brief(b)})
        steps.append(r)
    b.drain(5.0)
    return steps


SRAM = """
import time as _t
from device_wifi import autoconnect_wifi as _ac
ws._census_wifi = None
try:
    ws.wifi_hold('census')
except Exception as e:
    ws._census_wifi = 'hold: %s' % e
try:
    _ac(ws.wifi)
except Exception as e:
    ws._census_wifi = 'connect: %s' % e
_k = getattr(ws, 'ble_keyboard', None) or getattr(ws, 'keyboard', None)
ws._census_ble = None
try:
    if _k is not None and getattr(_k, 'start', None) is not None:
        _k.start()
        ws._census_ble = 'started'
except Exception as e:
    ws._census_ble = 'ble: %s' % e
"""


SCAN = """
import gc as _gc, time as _t
_mc = __import__('moy_carts')
def _sz(v, d=0):
    if isinstance(v, (str, bytes, bytearray)):
        return len(v)
    if d > 6:
        return 0
    if isinstance(v, dict):
        return sum(_sz(x, d + 1) for x in v.values())
    if isinstance(v, (list, tuple)):
        return sum(_sz(x, d + 1) for x in v)
    return 0
_gc.collect()
_a0 = _gc.mem_alloc()
_t0 = _t.ticks_ms()
_r = ws._with_sd(lambda: _mc.scan(str(ws.carts_root), src=False))
_dt = _t.ticks_diff(_t.ticks_ms(), _t0)
_gc.collect()
_a1 = _gc.mem_alloc()
_per = sorted([(c.get('title'), _sz(c)) for c in _r], key=lambda x: -x[1])
_keys = {}
for _c in _r:
    for _k, _v in _c.items():
        _keys[_k] = _keys.get(_k, 0) + _sz(_v)
_live = sum(_sz(c) for c in ws.carts.all)
ws._census_scan = (len(_r), _a1 - _a0, _dt, _per[:20], sorted(_keys.items(), key=lambda x: -x[1])[:16], _live)
_r = _per = _keys = None
"""


def scan(b):
    """The shelf's scan run again on the live console, its result held: the
    live bytes the full catalogue costs (what the boot holds before
    `carts.slim()`), its time, the heaviest carts and fields by string
    bytes, and the string bytes the slimmed catalogue keeps."""
    run(b, SCAN, timeout=180)
    n, live, ms, per, keys, kept = val(b, "ws._census_scan")
    val(b, "setattr(ws, '_census_scan', None) or 1")
    return {"carts": n, "live_bytes": live, "ms": ms, "top_carts": per,
            "fields": keys, "slim_string_bytes": kept}


def sram(b, secs=15):
    """Internal SRAM on this boot with WiFi and BLE both up: the caps before,
    and after both radios have been up `secs`."""
    before = val(b, M + ".caps(0x800), " + M + ".caps(0x808)")
    run(b, SRAM, timeout=60)
    b.drain(secs)
    wifi = val(b, "ws.wifi.status()")
    ble = val(b, "ws._census_ble, ws._census_wifi")
    s = snap(b)
    return {"before": before, "wifi_connected": wifi, "radios": ble,
            "sram": s["sram"], "dma": s["dma"], "psram": s["psram"]}


LAUNCH = """
import time as _t
_c = __import__('console')
_s = _t.ticks_us()
_items = ws._launcher_items(ws.carts.all)
_i = _t.ticks_diff(_t.ticks_us(), _s)
_s = _t.ticks_us()
_l = _c.Launcher(_items, ws.layout, _c.NAMES, _c._blit_glyph)
_k = _t.ticks_diff(_t.ticks_us(), _s)
ws._census_launch = (_i, _k, len(_items))
_items = _l = None
"""

EXIT_HOOK = """
import time as _t
ws._census_exit = None
_f0 = ws._frames_drawn
ws._census_t0 = _t.ticks_ms()
ws.exit()
ws._census_t1 = _t.ticks_ms()
ws._census_f0 = _f0
"""


def launcher(b, title="Star Catcher"):
    """The launcher's item list and constructor, timed on the live console;
    and exit-to-launcher from a running cart: ws.exit()'s own time, then the
    host's view of when the launcher's next frame was drawn."""
    run(b, LAUNCH)
    built = val(b, "ws._census_launch")
    rows = []
    for _ in range(3):
        st = settle_cart(b, title, 4)
        if not st.get("ran"):
            break
        run(b, EXIT_HOOK)
        t_host = time.time()
        frames = None
        while time.time() - t_host < 10:
            got = val(b, "(ws.screen, ws._frames_drawn - ws._census_f0, "
                         "__import__('time').ticks_diff(__import__('time').ticks_ms(), "
                         "ws._census_t0), ws._census_t1 - ws._census_t0)")
            if got[0] == "launcher" and got[1] > 0:
                frames = got
                break
        rows.append({"exit_call_ms": frames[3] if frames else None,
                     "launcher_frame_by_ms": frames[2] if frames else None,
                     "frames": frames[1] if frames else None})
        home(b)
        b.drain(1.0)
    return {"items_us": built[0], "ctor_us": built[1], "items": built[2],
            "exit": rows}


def pauses(b, title=None, secs=20):
    """PERF lines with diag on, at the launcher or under `title`: the gc
    field of each (collections, pause us, longest us)."""
    if title:
        st = settle_cart(b, title, 2)
        if not st.get("ran"):
            return {"title": title, "ran": False}
    n0 = len(b.lines)
    b.cmd("diag 1", wait_for="REMOTE")
    b.drain(secs)
    b.cmd("diag 0", wait_for="REMOTE")
    perf = [ln for ln in b.lines[n0:] if ln.startswith("PERF ")]
    if title:
        home(b)
    return {"title": title, "perf": perf}


def cut(b):
    """The destructive live-bytes census. The reply and every row come back
    on this one handle; the board is rebooted after."""
    n0 = len(b.lines)
    line = b.cmd("py " + M + ".cut(ws)", wait_for="PY", timeout=600, retry=False)
    rows = None
    if line and line.startswith("PY ") and "PY ERR" not in line:
        try:
            rows = eval(line.split("PY ", 1)[1])          # noqa: S307
        except Exception:  # noqa: BLE001
            rows = None
    return {"rows": rows, "reply": None if rows else line,
            "lines": b.lines[n0:][-20:] if rows is None else None}


def attach(name, dirs, port=None):
    return bd.attach(name, dirs, port=port)


def reboot(name, dirs, port=None):
    a = argparse.Namespace(port=port, force=False, soft=False, timeout=180)
    bd.cmd_reboot(name, dirs, a)


def main(argv=None):
    dirs = bd.boards()
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("board", choices=sorted(dirs))
    ap.add_argument("verb")
    ap.add_argument("arg", nargs="?")
    ap.add_argument("--port")
    ap.add_argument("--out")
    a = ap.parse_args(argv)
    out = a.out or "/tmp/mem_census/%s.jsonl" % a.board
    name = a.board

    if a.verb in ("boot", "fresh", "after", "cut-boot"):
        reboot(name, dirs, a.port)
    b = attach(name, dirs, a.port)
    try:
        b.drain(1.0)
        v = a.verb
        if v == "arm":
            mode = a.arg or "lite"
            run(b, "f = open(%r, 'w'); f.write(%r); f.close()"
                % ("/mem_census", mode))
            emit(out, name, v, mode)
        elif v == "disarm":
            val(b, "[__import__('os').remove('/' + n) for n in "
                   "__import__('os').listdir('/') if n == 'mem_census']")
            emit(out, name, v, None)
        elif v == "boot":
            emit(out, name, "trace", trace(b))
            emit(out, name, "snap", snap(b))
        elif v == "snap":
            emit(out, name, v, snap(b))
        elif v == "offheap":
            emit(out, name, v, offheap(b))
        elif v == "session":
            emit(out, name, "snap-before", snap(b))
            emit(out, name, v, session(b))
            emit(out, name, "snap-after", snap(b))
            emit(out, name, "offheap-after", offheap(b))
        elif v == "fit":
            emit(out, name, v, fit(b, a.arg or DOOM))
        elif v == "sram":
            emit(out, name, v, sram(b))
        elif v == "scan":
            emit(out, name, v, scan(b))
        elif v == "launcher":
            emit(out, name, v, launcher(b))
        elif v == "pauses":
            emit(out, name, v, pauses(b, a.arg))
        elif v in ("cut", "cut-boot"):
            emit(out, name, "cut", cut(b))
        elif v == "fresh":
            emit(out, name, "trace", trace(b))
            b.drain(10.0)
            emit(out, name, "snap-fresh", snap(b))
            emit(out, name, "offheap-fresh", offheap(b))
            emit(out, name, "fit-fresh", fit(b))
            emit(out, name, "snap-post-fit", snap(b))
        elif v == "after":
            emit(out, name, "trace", trace(b))
            b.drain(10.0)
            emit(out, name, "snap-fresh", snap(b))
            emit(out, name, "offheap-fresh", offheap(b))
            emit(out, name, "session", session(b))
            emit(out, name, "snap-after", snap(b))
            emit(out, name, "offheap-after", offheap(b))
            emit(out, name, "fit-after", fit(b))
            emit(out, name, "snap-post-fit", snap(b))
        else:
            ap.error("unknown verb %r" % v)
    finally:
        b.close()
    if a.verb in ("cut", "cut-boot"):
        reboot(name, dirs, a.port)
    return 0


if __name__ == "__main__":
    sys.exit(main())
