"""The Bench pmem report (2026-08-17): the Lua twin's sandbox has no serial
print, so both Bench carts write their numbers into a fixed pmem layout that
tools/p4_cart_bench.py reads live through moycore.pmem_image. The layout lives
in THREE hand-copies (bench.moy/main.py, bench_lua.moy/main.lua, the tool) --
these tests are the lock-step guard the comments in all three promise, plus a
real writer->reader roundtrip through the Python cart's own namespace."""

import ast
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import p4_cart_bench as bench_tool                     # noqa: E402

PY_CART = (ROOT / "system_carts" / "bench.moy" / "main.py").read_text()
LUA_CART = (ROOT / "system_carts" / "bench_lua.moy" / "main.lua").read_text()


def _lua_verb_ids():
    block = re.search(r"local VERB_ID = \{(.*?)\}", LUA_CART, re.S).group(1)
    return {m.group(1): int(m.group(2))
            for m in re.finditer(r"(\w+)\s*=\s*(\d+)", block)}


def _py_verb_ids():
    lit = re.search(r"_VERB_ID = (\{.*?\})", PY_CART, re.S).group(1)
    return ast.literal_eval(lit)


def _lua_phase_ids():
    block = re.search(r"local PHASE_ORDER = \{(.*?)\n\n", LUA_CART, re.S).group(1)
    return {m.group(1): int(m.group(2))
            for m in re.finditer(r'\{ "(\w+)", (\d+) \}', block)}


def _py_phase_ids():
    lit = re.search(r"_PHASE_ORDER = (\(.*?\)\))", PY_CART, re.S).group(1)
    return dict(ast.literal_eval(lit))


def test_the_three_layout_copies_agree():
    tool_ids = {name: i for i, name in enumerate(bench_tool.VERB_NAMES)}
    assert _py_verb_ids() == tool_ids
    assert _lua_verb_ids() == tool_ids
    tool_phases = {name: i for i, name in enumerate(bench_tool.PHASE_NAMES)}
    assert _py_phase_ids() == tool_phases
    assert _lua_phase_ids() == tool_phases
    # the magic + version literals, as each writer emits them
    assert "pmem(0, %d)" % bench_tool.PMEM_MAGIC in PY_CART
    assert "pmem(0, %d)" % bench_tool.PMEM_MAGIC in LUA_CART
    assert PY_CART.count("pmem(1, 1)") == 1
    assert LUA_CART.count("pmem(1, 1)") == 1
    # both writers arm the done flag at init and set it LAST
    for src in (PY_CART, LUA_CART):
        assert "pmem(3, 0)" in src
        assert src.rstrip().find("pmem(3, 1)") > src.find("pmem(3, 0)")


def test_the_verb_roster_matches_what_the_carts_measure():
    # VERB_NAMES must be the cart's _verbs() roster in order, or a report row
    # gets a wrong name and a diff compares two different verbs.
    names = re.findall(r'\("(\w+)", v_\w+, \d+\)', PY_CART)
    assert tuple(names) == bench_tool.VERB_NAMES
    lua_names = re.findall(r'\{ name = "(\w+)", k0 = \d+', LUA_CART)
    assert tuple(lua_names) == bench_tool.VERB_NAMES


def _cells(**over):
    cells = [0] * 256
    cells[0] = bench_tool.PMEM_MAGIC
    cells[1] = 1
    cells[2] = 2
    cells[3] = 1
    cells[8:11] = [0, 4, 27]         # cls x4 = 27ms
    cells[11:14] = [9, 500, 31]      # spr x500 = 31ms
    base = 64 + 2 * 8                # phase draw
    cells[base:base + 7] = [2, 195, 160, 170, 181, 250, 625]
    for k, v in over.items():
        cells[int(k)] = v
    return cells


def test_roundtrip_pmem_to_parse():
    res = bench_tool.parse(bench_tool.pmem_lines(_cells()))
    assert res["verbs"]["cls"] == 27 / 4
    assert res["verbs"]["spr"] == 31 / 500
    assert res["phases"]["draw"] == {"n": "195", "p50": "16.0", "p90": "17.0",
                                     "p99": "18.1", "worst": "25.0",
                                     "fps": "62.5"}


def test_python_cart_writes_what_the_tool_reads(tmp_path):
    # The real writer: boot the Python Bench under the host console, hand its
    # _pmem_report a known state, and read the cells back out of the cart's
    # own pmem verb -- byte-for-byte what the synthetic block above encodes.
    from runtime import host_app
    ws = host_app.build_workstation(str(tmp_path / "carts"))
    for i, it in enumerate(ws.launcher.items):
        if it.get("title") == "Bench":
            ws.launcher.sel = i
            break
    else:
        raise AssertionError("Bench not seeded")
    ws.open()
    assert ws.cart_error is None, ws.cart_error
    ns = ws.player.ns
    ns["state"]["micro"] = [("cls", 4, 27, 27, 30), ("spr", 500, 31, 32, 40)]
    ns["state"]["stats"] = {"draw": {"n": 195, "p50": 16.04, "p90": 17.0,
                                     "p99": 18.19, "worst": 25.0,
                                     "fps": 62.55}}
    ns["_pmem_report"]()
    cells = [ns["pmem"](i) for i in range(256)]
    assert bench_tool.pmem_lines(cells) == bench_tool.pmem_lines(_cells())


def test_stale_or_foreign_cells_read_as_absent():
    assert bench_tool.pmem_lines([0] * 256) == []
    assert bench_tool.pmem_lines(_cells(**{"0": 12345})) == []
    # a phase row whose id cell does not match its slot is stale, not data
    lines = bench_tool.pmem_lines(_cells(**{str(64 + 2 * 8): 4}))
    assert not any("phase=" in ln for ln in lines)
