"""The VM-free census (tools/vm_free_census.py): `moy_play_vm_free`, the
Player's rule in C (native/moy_play/moy_play_rule.c), over the catalogue
entry the store's C reads, pinned against the real manifests -- every seed
cart, the compiled carts' manifests, and the manifest the PICO-8 porter
writes for every import (docs/kernel_cartpath_2026-10.md §2)."""

import json
import os
import shutil
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))

from runtime import native_build  # noqa: E402

if native_build.cc() is None:
    pytest.skip("no C compiler: the Player's rule is C", allow_module_level=True)

import vm_free_census as census  # noqa: E402
from runtime import moy_play  # noqa: E402

# The seed carts the Player runs with no VM. Every other seed is Python.
SEED_FREE = {"moybyte.bench_lua.moy", "moybyte.brick_siege_lua.moy",
             "moybyte.bullet_storm.moy", "moybyte.sakura_lua.moy"}


def _cart(tmp_path, name, man, main="main.lua", extra=()):
    d = tmp_path / name
    d.mkdir()
    (d / "manifest.json").write_text(json.dumps(man))
    (d / main).write_bytes(b"\0asm" if main.endswith(".wasm") else b"")
    for f in extra:
        (d / f).write_text("")
    return str(d)


def test_every_seed_cart_has_its_verdict():
    rows = census.census(sorted(
        os.path.join(ROOT, "system_carts", n) for n in os.listdir(os.path.join(ROOT, "system_carts"))
        if n.endswith(".moy")))
    assert len(rows) == len([n for n in os.listdir(os.path.join(ROOT, "system_carts"))
                             if n.endswith(".moy")])
    for name, rt, free, why in rows:
        if name in SEED_FREE:
            assert (rt, free, why) == ("lua", True, "free"), name
        else:
            assert (rt, free, why) == ("python", False, "runtime"), name


def test_a_pico8_import_runs_with_no_vm(tmp_path):
    import p8_lua_port
    for mouse in (False, True):
        man = p8_lua_port.build_manifest("Port", icon=3, fps=60, mouse=mouse)
        d = _cart(tmp_path, "p8_%d.moy" % mouse, man, extra=("p8.lua",))
        assert moy_play.census(d) == ("lua", True, "free")


def test_the_compiled_carts_run_with_no_vm(tmp_path):
    mans = [os.path.join(ROOT, "ports", "jet", n, "manifest.json") for n in ("teapot.moy", "esp88.moy")]
    fixtures = os.path.join(ROOT, "tests", "fixtures", "wasm")
    mans += [os.path.join(fixtures, n, "manifest.json") for n in sorted(os.listdir(fixtures))
             if os.path.exists(os.path.join(fixtures, n, "manifest.json"))]
    assert len(mans) > 4
    for i, path in enumerate(mans):
        with open(path) as fh:
            man = json.load(fh)
        d = _cart(tmp_path, "c%d.moy" % i, man, main=man.get("main", "main.wasm"))
        assert moy_play.census(d) == ("wasm", True, "free"), path
        assert moy_play.census(d, wasm=False) == ("wasm", False, "absent"), path


def test_each_clause_keeps_the_vm_by_name(tmp_path):
    base = {"format": "moy-1", "title": "T", "runtime": "lua", "main": "main.lua"}

    def verdict(name, **ven):
        man = dict(base)
        if ven:
            man["moybyte"] = ven
        return moy_play.census(_cart(tmp_path, name, man))

    assert verdict("a.moy") == ("lua", True, "free")
    assert verdict("b.moy", type="app") == ("lua", False, "type")
    assert verdict("c.moy", type="tool") == ("lua", False, "type")
    for perm in ("network", "console", "pins", "files", "files:pictures", "prefs", "telepathy"):
        assert verdict("p_%s.moy" % perm.replace(":", "_"),
                       permissions=["graphics", perm]) == ("lua", False, "permission"), perm
    assert verdict("d.moy", permissions=["graphics", "input", "audio", "multiplayer"]) \
        == ("lua", True, "free")
    assert verdict("e.moy", permissions="graphics") == ("lua", False, "permission")
    d = _cart(tmp_path, "f.moy", dict(base))
    assert moy_play.census(d, lua=False) == ("lua", False, "absent")


def test_the_store_defaults_decide_a_manifest_that_names_nothing(tmp_path):
    # A spec cart with no runtime is Lua and with no type a game; a manifest
    # with no "format" is a moybyte cart: Python, an app.
    spec = _cart(tmp_path, "s.moy", {"format": "moy-1", "title": "S", "main": "main.lua"})
    assert moy_play.census(spec) == ("lua", True, "free")
    old = _cart(tmp_path, "o.moy", {"title": "O", "runtime": "lua", "main": "main.lua"})
    assert moy_play.census(old) == ("lua", False, "type")
    py = _cart(tmp_path, "y.moy", {"title": "Y"}, main="main.py")
    assert moy_play.census(py) == ("python", False, "runtime")


def test_the_tool_prints_one_line_a_cart(capsys, tmp_path):
    shutil.copytree(os.path.join(ROOT, "system_carts", "moybyte.sakura_lua.moy"),
                    str(tmp_path / "moybyte.sakura_lua.moy"))
    assert census.main([str(tmp_path / "moybyte.sakura_lua.moy")]) == 0
    out = capsys.readouterr().out.split("\n")
    assert out[0].split() == ["moybyte.sakura_lua.moy", "lua", "free", "free"]
    assert out[1] == "1 carts, 1 VM-free"


def test_every_run_reports_the_census_verdict_for_its_cart(tmp_path):
    """The Player decides each run's verdict with the same rule over the same
    entry (player._verdict, moy_play.census), and `state` reports it beside
    the frame's upcalls: every seed game a kid launches from the shelf agrees
    with the census of its folder."""
    from runtime.dev_channel import _remote_state
    from ws_helpers import build_ws

    ws = build_ws(tmp_path)
    assert _remote_state(ws)["run"] is None
    games = [(i, c) for i, c in enumerate(ws.launcher.items)
             if c.get("type", "game") == "game" and c.get("path")]
    assert any(c["runtime"] == "lua" for _, c in games if "runtime" in c)
    seen = 0
    for i, cart in games:
        ws.launcher.sel = i
        ws.open()
        want = moy_play.census(cart["path"])
        run = _remote_state(ws)["run"]
        # Where the Player has the run, its stop verdict too: no seed game
        # stops the VM (the host has no stop; on a board the `need` policy
        # keeps it for every seed, which fits).
        stop = {k: run.pop(k) for k in ("stop", "vm_down") if k in run}
        assert stop in ({}, {"stop": "lever", "vm_down": False},
                        {"stop": "rule", "vm_down": False}), (cart["title"], stop)
        assert run == {"runtime": want[0], "vm_free": want[1], "why": want[2]}, \
            (cart["title"], run, want)
        if os.path.basename(cart["path"].rstrip("/")) in SEED_FREE:
            assert run["vm_free"] is True, cart["title"]
            seen += 1
        ws.exit()
    assert seen >= 3, "the shelf no longer carries the VM-free seed games"
