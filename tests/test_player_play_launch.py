"""The Player launches a runtime cart's run in the kernel's Player BEFORE the
runtime loads (docs/kernel_cartpath_2026-10.md §6 step 1): the load and _init
are then in the run's books, which the on-glass zero-crossing check reads from
before the launch. A run whose runtime will not load ends as a crash; one
whose runtime offers no C frame ends at once and ticks through Python."""

import os
import shutil

from runtime import player as player_mod
from ws_helpers import build_ws, open_cart

FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "placement_lua.moy")


class FakePlay:
    END_QUIT, END_CRASH = 1, 3

    def __init__(self, log):
        self.log = log

    def launch(self, path, paced):
        self.log.append(("launch", os.path.basename(path), paced))
        return 0x15A

    def end(self, run, why=END_QUIT):
        self.log.append(("end", run, why))


def _ws(tmp_path, monkeypatch, log, make=None):
    root = tmp_path / "carts"
    root.mkdir(parents=True)
    shutil.copytree(FIXTURE, str(root / "placement_lua.moy"))
    ws = build_ws(tmp_path)
    monkeypatch.setattr(player_mod, "_moy_play", FakePlay(log))
    real = ws.runtimes["lua"]

    def factory(ns, src):
        log.append(("load",))
        if make is not None:
            return make(ns, src)
        return real(ns, src)

    ws.runtimes = dict(ws.runtimes, lua=factory)
    return ws


def test_the_run_is_launched_before_its_runtime_loads(tmp_path, monkeypatch):
    log = []
    ws = _ws(tmp_path, monkeypatch, log)
    open_cart(ws, "Placement Lua")
    assert ws.player.cart_error is None
    assert log[0] == ("launch", "placement_lua.moy", True)
    assert log[1] == ("load",)
    # The runtime offers the C frame (play_begin), so the run is the
    # runtime's from here, ended at its close.
    assert ws.player._lua._run == 0x15A
    assert len(log) == 2


def test_a_runtime_that_will_not_load_ends_its_run_as_a_crash(tmp_path, monkeypatch):
    log = []

    def broken(ns, src):
        raise RuntimeError("cart:1: boom")

    ws = _ws(tmp_path, monkeypatch, log, broken)
    open_cart(ws, "Placement Lua")
    assert ws.player.cart_error
    assert log == [("launch", "placement_lua.moy", True), ("load",),
                   ("end", 0x15A, FakePlay.END_CRASH)]
