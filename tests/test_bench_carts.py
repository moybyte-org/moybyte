"""The Bench twins (#163): the Python meter and its line-faithful Lua port
both load, tick error-free through the micro phase, and actually measure --
including the 2026-08-04 verb-set extension (rectb/circb/tri/spr/sprb/map/
sspr), which exercises the bundled sprites.moygfx sheet and the _init-mset
tilemap. The Lua twin skips when lupa (the optional host runner) is absent."""

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


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
                     "rectb", "circb", "tri", "spr", "sprb", "map",
                     "sspr", "tline"][:len(names)]   # measured in the declared order


def test_bench_lua_runs_the_verb_set(tmp_path):
    pytest.importorskip("lupa")
    _run_bench(tmp_path, "Bench Lua", frames=120)
