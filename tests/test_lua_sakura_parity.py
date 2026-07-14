"""Golden parity: the sakura Lua port must match main.py bit-for-bit (#67).

Runs experiments/lua_bridge/host_parity.py's harness: the real main.py and
main.lua under one deterministic fake API (shared PRNG, scripted touch),
comparing every draw call and the final petal state. Exact float equality --
both runtimes are IEEE doubles, so any epsilon is a porting bug.

Skips when `lupa` (the optional #67 Phase 3 host-runner dep) isn't installed.
"""

import os
import sys

import pytest

pytest.importorskip("lupa")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..",
                                "experiments", "lua_bridge"))
from host_parity import run_parity  # noqa: E402


def test_sakura_lua_parity():
    assert run_parity(frames=600, verbose=True)
