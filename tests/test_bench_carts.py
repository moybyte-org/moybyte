"""The Bench twins (#163): the Python meter and its line-faithful Lua port both
load, tick error-free through the micro phase, and actually measure -- including
the 2026-08-04 verb-set extension (rectb/circb/tri/spr/map/sspr), which
exercises the bundled sprites.moygfx sheet and the _init-mset tilemap.

Since 2026-09-06 these two carts are the WHOLE bench shelf: Ray Test, Ray Lua
and Layer Test folded in as the ray/tetra/scroll/layer phases, so the phase
table is asserted here rather than one cart per measurement.
"""

import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SYSTEM_CARTS = ROOT / "system_carts"

# Every phase both carts run, in order. Adding one here is the reminder that
# tools/p4_cart_bench.py's PHASE_NAMES and both carts' PHASE_ORDER move with it
# (tests/test_bench_pmem_report.py is the lock-step guard on those three).
PHASES = ("idle", "logic", "draw", "silent", "sound",
          "ray", "tetra", "scroll", "layer")


def _run_bench(tmp_path, title, frames):
    from runtime import host_app
    ws = host_app.build_workstation(str(tmp_path / "carts"))
    drv = host_app.ConsoleDriver(ws)
    for i, it in enumerate(ws.launcher.items):
        if it.get("title") == title:
            ws.launcher.sel = i
            break
    else:
        raise AssertionError(title + " not seeded")
    ws.open()
    assert ws.cart_error is None, ws.cart_error
    for _ in range(frames):
        drv.frame(1 / 30)
        assert ws.cart_error is None, ws.cart_error
    return ws


def test_bench_python_measures_the_verb_set(tmp_path):
    ws = _run_bench(tmp_path, "Bench", frames=120)
    state = ws.player.ns["state"]
    # The micro phase is under way and recording (its list only grows once a
    # verb's 8 reps complete -- 120 frames is comfortably a few verbs in).
    assert state["micro"], "micro phase recorded nothing"
    names = [m[0] for m in state["micro"]]
    assert names == ["cls", "rect", "circ", "line", "pix", "print",
                     "rectb", "circb", "tri", "spr", "map",
                     "sspr", "tline"][:len(names)]   # measured in the declared order


def test_bench_python_runs_every_folded_scene(tmp_path):
    # Drive the phase machine straight at each scene -- the micro pass alone is
    # thousands of frames -- so a scene that raises (a missing map, a layer the
    # host will not make, a verb the fold got wrong) fails here rather than on
    # glass twenty minutes into a run.
    ws = _run_bench(tmp_path, "Bench", frames=2)
    from runtime import host_app
    drv = host_app.ConsoleDriver(ws)
    ns = ws.player.ns
    for phase, label in ((6, "ray"), (7, "tetra"), (8, "scroll"), (9, "layer")):
        ns["state"]["phase"] = phase
        ns["state"]["frame"] = 0
        ns["state"]["dts"] = []
        ns["state"]["warm"] = 0
        assert ns["state"]["scenes"][phase][0] == label
        for _ in range(6):
            drv.frame(1 / 30)
            assert ws.cart_error is None, label + ": " + str(ws.cart_error)
        assert ns["state"]["dts"], label + " recorded no frame times"
    assert ns["state"]["lay"], "the layer scene never built its layer"


def test_bench_python_reports_every_phase(tmp_path):
    # The report is what #66 reads. Hand it a full stats table and assert every
    # phase reaches serial and pmem -- a phase that measures and never prints is
    # the failure this whole cart exists to avoid.
    ws = _run_bench(tmp_path, "Bench", frames=2)
    ns = ws.player.ns
    ns["state"]["stats"] = {p: {"n": 90, "p50": 20.0, "p90": 21.0, "p99": 22.0,
                                "worst": 30.0, "best": 19.0, "fps": 50.0}
                            for p in PHASES}
    lines = []
    ns["_p"] = lines.append
    ns["_serial_report"]()
    got = [ln.split("phase=")[1].split()[0] for ln in lines if "phase=" in ln]
    assert got == list(PHASES)
    ns["_pmem_report"]()
    for pid, name in enumerate(PHASES):
        assert ns["pmem"](64 + pid * 8) == pid, name


def test_the_bench_carries_the_assets_its_folded_scenes_read():
    # The ray scene marches map.moymap and textures itself off sheet tiles
    # 64..67; the scroll pair needs the map to be 64 tiles wide (512px, the
    # layer's width). Both carts ship the same two files.
    from runtime.editors_sheet import TileMap

    for folder in ("bench.moy", "bench_lua.moy"):
        d = SYSTEM_CARTS / folder
        tm = TileMap.from_hex((d / "map.moymap").read_text())
        assert (tm.w, tm.h) == (64, 30), folder
        walls = {tm.mget(x, y) for y in range(8, 30) for x in range(12)}
        assert walls - {-1} and max(walls) < 68, folder   # the maze, tiles 64..67
        # the maze's border is solid, which is what bounds the DDA march
        assert all(tm.mget(x, 8) >= 0 and tm.mget(x, 29) >= 0 for x in range(12))
        assert all(tm.mget(0, y) >= 0 and tm.mget(11, y) >= 0 for y in range(8, 30))
        # the scroll window (cam 96 -> cols 12..52) never shows the maze
        assert all(tm.mget(x, y) < 64 for y in range(30) for x in range(12, 53))


def test_the_retired_bench_carts_are_gone():
    # One bench per language and nothing else: the three carts that folded in
    # must not come back as folders, or the shelf grows the duplicates again.
    for folder in ("ray_test.moy", "ray_lua.moy", "layer_test.moy"):
        assert not (SYSTEM_CARTS / folder).exists(), folder
    import sys
    sys.path.insert(0, str(ROOT / "runtime"))
    import moy_carts
    titles = {json.loads((d / "manifest.json").read_text())["title"]
              for d in SYSTEM_CARTS.glob("*.moy")}
    # a retired title is one nothing ships -- otherwise the sweep deletes a cart
    # the roster puts straight back
    assert not (set(moy_carts.RETIRED) & titles)


def test_bench_lua_runs_the_verb_set(tmp_path):
    from runtime.lua_binding import HostLuaRun
    if not HostLuaRun.available():
        pytest.skip("host lua binding not built (needs a C compiler)")
    _run_bench(tmp_path, "Bench Lua", frames=120)
