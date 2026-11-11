"""Two Lua runs at once (docs/os_2026-11.md section 7, phase 5 step 3;
docs/os_2026-11/tiers.md section 2).

A Lua run is a row of the runs open at once (native/moycore/moycore_lua.h,
THE RUNS ARE ROWS): its own VM, console, layers, images and scenes, its p8
machine and its grant. Every call makes its run's row current first, so two
runs interleave call by call and neither sees the other:

  * two runs on the host's binding, ticked in turn: each keeps its own
    globals, draws only on its own canvas, counts its own heap, and goes on
    when the other closes;
  * the same through the module surface the boards' glue drives
    (runtime/host/moycore.py over the C, `run_begin` answering a handle,
    `use` making it current);
  * two Lua apps' runs, each reaching only its own grant's prefs.
"""

import pytest

from runtime import lua_binding as lb
from runtime import host_app

import moy_app

pytestmark = pytest.mark.skipif(not lb.HostLuaRun.available(),
                                reason="no host Lua binding (a C compiler builds it)")

DT = 1.0 / 30.0

CART = """
N = 0
function _init() N = %d end
function _update() N = N + 1 end
function _draw() cls(%d) end
"""


def _run(colour, start, w=32, h=24, app=None):
    buf = bytearray(w * h * 2)
    r = lb.HostLuaRun(buf, w, h, app=app)
    assert r.load([(CART % (start, colour), "@cart")]) is None
    return r, buf


def test_two_lua_runs_tick_in_turn_each_with_its_own_world():
    a, abuf = _run(8, 100)
    b, bbuf = _run(12, 500)
    try:
        assert a.handle() and b.handle() and a.handle() != b.handle()
        for _ in range(5):
            assert a.tick(DT) is None
            assert b.tick(DT) is None
        b.tick(DT)
        assert a.get_global("N") == 105
        assert b.get_global("N") == 506
        # Each drew on its own canvas only.
        assert abuf[:2] != bbuf[:2]
        assert len(set(bytes(abuf[i:i + 2]) for i in range(0, len(abuf), 2))) == 1
        assert len(set(bytes(bbuf[i:i + 2]) for i in range(0, len(bbuf), 2))) == 1
        # Each heap is its own state's, Lua's own count.
        assert a.heap() > 0 and b.heap() > 0
        a.exec("BIG = {} for i = 1, 2000 do BIG[i] = i end")
        assert a.heap() > b.heap() + 8000
    finally:
        a.close()
    # The other run goes on, its row its own.
    assert a.handle() == 0
    assert b.handle()
    assert b.tick(DT) is None
    assert b.get_global("N") == 507
    b.close()


def test_the_module_surface_names_each_run_by_its_handle():
    from array import array

    from runtime.host import moycore as mc
    runs = []
    try:
        for colour in (3, 9):
            fb = bytearray(16 * 16 * 2)
            snap = array("i", bytearray(4 * mc.SNAP_LEN))
            aq = array("i", bytearray(4 * (1 + mc.AQ_SLOTS * mc.AQ_MAX)))
            h = mc.run_begin(fb, 16, 16, None, None, None, 0, 0, snap, aq, None,
                             None, None, True)
            assert mc.load([("K = %d function _draw() cls(K) end" % colour, "@cart")]) is None
            runs.append((h, fb))
        (ha, fa), (hb, fb_) = runs
        assert ha != hb and mc.runs() >= 2
        assert mc.use(ha) and mc.get_global("K") == 3
        assert mc.use(hb) and mc.get_global("K") == 9
        assert mc.heap(ha) and mc.heap(hb)
        mc.use(ha)
        mc.close()
        assert not mc.use(ha), "a closed run's handle named a row"
        assert mc.heap(ha) is None
        assert mc.use(hb) and mc.get_global("K") == 9
    finally:
        for h, _ in runs:
            if mc.use(h):
                mc.close()


LUA_PREFS = """
function _init() prefs.set("k", "%s") end
function _update() GOT = prefs.get("k", "none") end
function _draw() end
"""


def test_two_lua_apps_each_reach_only_their_own_grant(tmp_path):
    ws = host_app.build_workstation(str(tmp_path / "carts"))
    app = ws.app_abi
    roles, kind = moy_app.policy(("prefs",))
    runs = []
    try:
        for name in ("alpha", "beta"):
            g = app.grant("test." + name, roles, kind=kind, ns="test." + name, run=True)
            buf = bytearray(16 * 16 * 2)
            r = lb.HostLuaRun(buf, 16, 16, app=app.run(g, roles))
            assert r.load([(LUA_PREFS % name, "@cart")]) is None
            runs.append((r, g, buf))
        (a, ga, _), (b, gb, _) = runs
        for _ in range(3):
            a.tick(DT)
            b.tick(DT)
            assert a.get_global("GOT") == "alpha"
            assert b.get_global("GOT") == "beta"
        assert moy_app.Prefs(app, ga).get("k") == "alpha"
        assert moy_app.Prefs(app, gb).get("k") == "beta"
    finally:
        for r, _, _ in runs:
            r.close()


DRIVER = r'''
import gc, moycore
from array import array

def begin(colour):
    fb = bytearray(16 * 16 * 2)
    snap = array("i", bytearray(4 * moycore.SNAP_LEN))
    aq = array("i", bytearray(4 * (1 + moycore.AQ_SLOTS * moycore.AQ_MAX)))
    h = moycore.run_begin(fb, 16, 16, None, None, None, 0, 0, snap, aq, None,
                          None, None, True, None)
    moycore.register("ask", lambda n, c=colour: n * 100 + c)
    print("LOAD", moycore.load([("N = 0 function _update() N = ask(N) end "
                                 "function _draw() cls(%d) end" % colour, "@cart")]))
    return h, fb, snap, aq

a = begin(3)
b = begin(9)
print("RUNS", moycore.runs(), a[0] != b[0])
for _ in range(2):
    moycore.use(a[0]); moycore.tick(1 / 30)
    moycore.use(b[0]); moycore.tick(1 / 30)
moycore.use(a[0]); print("A", moycore.get_global("N"), moycore.heap(a[0]) > 0)
moycore.use(b[0]); print("B", moycore.get_global("N"))
moycore.use(a[0]); moycore.close()
gc.collect()
print("USE", moycore.use(a[0]), moycore.use(b[0]), moycore.runs())
moycore.tick(1 / 30)
print("B", moycore.get_global("N"))
moycore.close()
print("RUNS", moycore.runs())
'''


def test_two_runs_on_the_boards_binding():
    """The boards' module (native/moycore/modmoycore.c) on the desktop
    MicroPython: each run keeps its own registered Python verbs against the
    gc, ticks on its own row, and goes on when the other closes."""
    import subprocess

    from unix_mp import require_unix_mp
    exe = require_unix_mp(
        "moycore",
        why="Without it nothing runs two Lua runs through the boards' binding.")
    p = subprocess.run([exe, "-c", DRIVER], capture_output=True, text=True, timeout=120)
    assert p.returncode == 0, p.stdout + p.stderr
    lines = p.stdout.splitlines()
    assert lines[:2] == ["LOAD None", "LOAD None"], lines
    assert lines[2] == "RUNS 2 True", lines
    assert lines[3] == "A 303 True", lines
    assert lines[4] == "B 909", lines
    assert lines[5] == "USE False True 1", lines
    assert lines[6] == "B 90909", lines
    assert lines[7] == "RUNS 0", lines
