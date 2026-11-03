"""The task manager (runtime/system/tasks_ui.py, docs/os_2026-11/processes.md
§6): the accounting held to a cart of known cost, the rows and their verbs,
the handheld's running set (the pause card's Minimise, a launch beside a
minimised game, Switch to) and the console's key held to open it."""

import subprocess
import tracemalloc

import pytest

from runtime import procs as procs_mod
from runtime import scheduler as S
from runtime import tasks_ui
from runtime.moy_input import InputTable
from unix_mp import require_unix_mp, runtime_tree
from ws_helpers import (build_desktop_ws, build_ws, build_ws_with_cart, boot_on_fixture,
                        make_drv, open_cart)

_DT = 1 / 30


@pytest.fixture(autouse=True)
def _night(monkeypatch):
    boot_on_fixture(monkeypatch)


# -- the accounting -------------------------------------------------------------

KNOWN = """
big = None
def _init():
    global big
    big = bytearray(200000)
def _update(dt=None):
    spend(2000)
def _draw():
    spend(1000)
    cls(1)
"""


def _clocked(monkeypatch):
    """The Player's microsecond clock and the running set's millisecond
    clock, moved only by the cart (`spend`) and the test."""
    from runtime import player as player_mod
    us = [10 ** 6]
    ms = [10 ** 4]
    monkeypatch.setattr(player_mod, "_ticks_us", lambda: us[0])
    monkeypatch.setattr(procs_mod, "_ticks_ms", lambda: ms[0])

    def spend(n):
        us[0] += n
    return us, ms, spend


def test_a_cart_of_known_cost_reads_its_cpu_and_memory(tmp_path, monkeypatch):
    tracemalloc.start()
    try:
        ws = build_ws_with_cart(tmp_path, KNOWN.replace("spend(", "_spend("),
                                title="Known", type="game")
    finally:
        tracemalloc.stop()
    pl = ws.player
    assert 200000 <= pl.mem < 240000, pl.mem   # its `_init`'s bytearray, about
    us, ms, spend = _clocked(monkeypatch)
    pl.ns["_spend"] = spend
    drv = make_drv(ws)
    drv.frame(_DT)                     # the sample window opens here
    ms[0] += procs_mod.METER_MS
    ws.procs.account(ms[0])
    pl.us_upd = pl.us_draw = 0
    for _ in range(30):
        drv.frame(_DT)
    assert pl.us_upd == 30 * 2000 and pl.us_draw == 30 * 1000
    ms[0] += 1000
    ws.procs.account(ms[0] + procs_mod.METER_MS)   # one sample over 3000 ms
    assert pl.cpu == 90000 // (1000 + procs_mod.METER_MS)
    assert pl.us_upd == 0 and pl.us_draw == 0
    row = ws.procs.rows()[0]
    assert row[:4] == ("desktop", "Known", "game", "running")
    assert row[4] == pl.cpu and row[5] == pl.mem


def test_memory_is_absent_where_nothing_counts_it(tmp_path):
    assert not tracemalloc.is_tracing()
    ws = build_ws_with_cart(tmp_path, KNOWN.replace("spend(", "int("), title="Known",
                            type="game")
    assert ws.player.mem is None
    assert tasks_ui.mem_text(None) == "-" and tasks_ui.cpu_text(None) == "-"
    assert tasks_ui.mem_text(200000) == "~196K"
    assert tasks_ui.mem_text(3 << 20 | 1 << 19) == "~3.5M"
    assert tasks_ui.cpu_text(125) == "13%"


def test_a_services_callbacks_are_its_cpu_and_its_start_its_memory(tmp_path):
    import moybyte
    ws = build_ws(tmp_path)
    sched = S.SCHED
    tracemalloc.start()
    try:
        pid = sched.start({"id": "t.svc", "title": "Svc", "permissions": [],
                           "src": "import moybyte\nkeep = bytearray(50000)\n"
                                  "moybyte.every(1000, lambda: None)\n"})
    finally:
        tracemalloc.stop()
    try:
        sp = sched.procs[pid]
        assert 50000 <= sp.mem < 70000
        sp.cost_us += 4000
        ws.procs._acct_t = 1
        ws.procs.account(1 + 2000)
        assert sp.cpu == 2
        rows = [r for r in ws.procs.rows() if r[2] == "service"]
        assert rows == [("svc:%d" % pid, "Svc", "service", "background", 2, sp.mem, pid)]
        assert tasks_ui.verbs(rows[0]) == ["Stop"]
        ws.procs.stop_service(pid)
        assert [r for r in ws.procs.rows() if r[2] == "service"] == []
    finally:
        sched.reset()
        moybyte.WAKES.end(pid)


def test_a_crashed_service_offers_its_log(tmp_path):
    ws = build_ws(tmp_path)
    sched = S.SCHED
    try:
        pid = sched.start({"id": "t.bad", "title": "Bad", "permissions": [],
                           "src": "raise ValueError('no')\n"})
        assert pid is None
        sp = next(p for p in sched.procs.values() if p.name == "t.bad")
        row = next(r for r in ws.procs.rows() if r[2] == "service")
        assert row[3] == "crashed"
        assert tasks_ui.verbs(row) == ["Details", "Close"]
        opened = []
        ws.open_log = opened.append
        ws.tasks_layer._do("Details", row)
        assert opened == [sp.pid]
        ws.tasks_layer._do("Close", row)
        assert [r for r in ws.procs.rows() if r[2] == "service"] == []
    finally:
        sched.reset()


def test_on_the_vm_the_guard_counts_what_a_call_allocates(tmp_path):
    exe = require_unix_mp("moy_input", "moy_log", why="moy_guard.allocated on the VM")
    script = tmp_path / "alloc.py"
    script.write_text(
        "import sys\nsys.path[:0] = [%r]\nimport scheduler as S\n"
        "a = S.allocated()\nx = bytearray(100000)\nb = S.allocated()\n"
        "print('BIG', 100000 <= (b - a) & S.ALLOC_WRAP < 101000)\n"
        "def f():\n    a = S.allocated()\n    n = 0\n    while n < 1000:\n        n += 1\n"
        "    b = S.allocated()\n    return (b - a) & S.ALLOC_WRAP\n"
        "f()\nprint('NONE', f())\n" % runtime_tree())
    done = subprocess.run([str(exe), str(script)], capture_output=True, text=True,
                          timeout=60)
    assert done.returncode == 0, done.stdout + done.stderr
    assert "BIG True\n" in done.stdout and "NONE 0\n" in done.stdout, done.stdout


# -- the desk -------------------------------------------------------------------


def _desk_two(tmp_path):
    ws = build_desktop_ws(tmp_path)
    drv = make_drv(ws)
    ws.open_desk()
    drv.frame(_DT)
    for t in ("Star Catcher", "Brick Siege"):
        open_cart(ws, t)
        drv.frame(_DT)
        win = ws.wm._wins["desktop"]
        if win.saved is not None:
            ws.wm._toggle_max(win)
        drv.frame(_DT)
    return ws, drv


def test_the_desk_lists_both_games_and_switches_and_closes(tmp_path):
    ws, drv = _desk_two(tmp_path)
    ws.open_tasks()
    drv.frame(_DT)
    assert "tasks" in ws.wm._wins
    rows = ws.procs.rows()
    assert [(r[0], r[1], r[3]) for r in rows] == [
        ("desktop", "Brick Siege", "running"), ("run:1", "Star Catcher", "throttled")]
    assert tasks_ui.verbs(rows[0]) == ["Switch to", "Close"]
    assert tasks_ui.verbs(rows[1]) == ["Switch to", "Close"]
    ws.tasks_layer._do("Switch to", rows[1])
    assert ws.cart["title"] == "Star Catcher"
    ws.tasks_layer._do("Close", ws.procs.rows()[1])
    assert ws.procs.list == [] and ws.cart["title"] == "Star Catcher"
    ws.close_tasks()
    drv.frame(_DT)
    assert "tasks" not in ws.wm._wins


def test_the_system_menu_offers_the_task_manager(tmp_path):
    ws = build_ws(tmp_path)
    drv = make_drv(ws)
    drv.frame(_DT)
    ws.toggle_sysmenu()
    row = next(r for r in ws.sysmenu.items if r[0] == "item" and r[4] == "Tasks")
    row[2]()
    drv.frame(_DT)
    assert ws.screen == "tasks"


# -- the handheld's running set -------------------------------------------------


def _hand(tmp_path):
    ws = build_ws(tmp_path)
    drv = make_drv(ws)
    for _ in range(3):
        drv.frame(_DT)
    return ws, drv


def _minimise(ws, drv):
    ws.system_key()
    assert ws.pause.open and ws.pause.MINIMISE in ws.pause.rows
    ws.pause.choose(ws.pause.rows.index(ws.pause.MINIMISE))
    drv.frame(_DT)


def test_the_pause_cards_minimise_keeps_the_game_held_and_goes_home(tmp_path):
    ws, drv = _hand(tmp_path)
    open_cart(ws, "Star Catcher")
    for _ in range(3):
        drv.frame(_DT)
    pl = ws.player
    n = pl.ns["_update"]
    heard = []
    pl.ns["_minimise"] = lambda: heard.append("min")
    _minimise(ws, drv)
    assert ws.screen == "launcher" and not ws.pause.open
    assert [p.title() for p in ws.procs.list] == ["Star Catcher"]
    p = ws.procs.list[0]
    assert p.state == "minimised" and p.player.paused and heard == ["min"]
    assert p.player.ns["_update"] is n and p.player is pl
    ups = p.ups
    for _ in range(10):
        drv.frame(_DT)
    assert p.ups == ups                      # a minimised game is paused


def test_a_launch_beside_a_minimised_game_and_switching_back(tmp_path):
    ws, drv = _hand(tmp_path)
    open_cart(ws, "Star Catcher")
    drv.frame(_DT)
    _minimise(ws, drv)
    open_cart(ws, "Brick Siege")
    for _ in range(3):
        drv.frame(_DT)
    assert ws.cart["title"] == "Brick Siege" and ws.screen == "desktop"
    assert [p.title() for p in ws.procs.list] == ["Star Catcher"]
    ws.open_tasks()
    drv.frame(_DT)
    assert ws.screen == "tasks" and ws.player.paused   # it holds the front game
    rows = ws.procs.rows()
    assert [(r[1], r[3]) for r in rows] == [("Brick Siege", "paused"),
                                            ("Star Catcher", "minimised")]
    assert tasks_ui.verbs(rows[1]) == ["Switch to", "Close"]
    ws.tasks_layer._do("Switch to", rows[1])
    drv.frame(_DT)
    assert ws.screen == "desktop" and ws.cart["title"] == "Star Catcher"
    assert not ws.player.paused and not ws.player.detached
    assert [p.title() for p in ws.procs.list] == ["Brick Siege"]
    assert ws.procs.list[0].state == "minimised" and ws.procs.list[0].player.paused
    n0 = ws.player.ns.get("score", 0)
    for _ in range(5):
        drv.frame(_DT)
    assert ws.screen == "desktop"
    # launching the minimised one again is a switch, not a second run
    ws.system_key()
    ws.pause.choose(ws.pause.rows.index("Quit"))
    drv.frame(_DT)
    open_cart(ws, "Brick Siege")
    drv.frame(_DT)
    assert ws.cart["title"] == "Brick Siege" and ws.procs.list == []
    assert n0 is not None


def test_the_task_manager_goes_back_to_the_game_it_held_and_home_keeps_it(tmp_path):
    ws, drv = _hand(tmp_path)
    open_cart(ws, "Star Catcher")
    drv.frame(_DT)
    ws.open_tasks()
    drv.frame(_DT)
    assert ws.player.paused
    ws.tasks_layer.back()
    drv.frame(_DT)
    assert ws.screen == "desktop" and not ws.player.paused
    ws.open_tasks()
    drv.frame(_DT)
    rows = ws.procs.rows()
    ws.tasks_layer._do("Home", rows[0])
    drv.frame(_DT)
    assert ws.screen == "launcher"
    assert [p.title() for p in ws.procs.list] == ["Star Catcher"]
    assert ws.procs.list[0].state == "minimised"


def test_closing_the_front_run_keeps_the_task_manager(tmp_path):
    ws, drv = _hand(tmp_path)
    open_cart(ws, "Star Catcher")
    drv.frame(_DT)
    ws.open_tasks()
    drv.frame(_DT)
    ws.tasks_layer._do("Close", ws.procs.rows()[0])
    drv.frame(_DT)
    assert ws.screen == "tasks" and ws.procs.rows() == []
    assert not ws.wm.stack.has("desktop")
    ws.tasks_layer.back()
    assert ws.screen == "launcher"


def test_a_minimised_app_keeps_updating_on_a_handheld(tmp_path):
    src = "n = 0\ndef _update(dt=None):\n    global n\n    n += 1\ndef _draw():\n    cls(1)\n"
    ws = build_ws_with_cart(tmp_path, src, title="Ticker", type="app")
    drv = make_drv(ws)
    drv.frame(_DT)
    assert ws.procs._detachable()
    assert ws.procs.detach()
    ws.go_home()
    p = ws.procs.list[0]
    assert p.app and p.state == "minimised" and not p.player.paused
    n = p.player.ns["n"]
    for _ in range(10):
        drv.frame(_DT)
    assert p.player.ns["n"] == n + 10


# -- the console's key held ------------------------------------------------------


def _start_frames(ws, monkeypatch, held_ms):
    from runtime import console_input
    now = [50000]
    monkeypatch.setattr(console_input, "_ticks_ms", lambda: now[0])
    was = ws.input
    i = InputTable()
    ws.input = i
    try:
        i.source("kbd").set_button("start", True)
        i.begin_frame()
        ws.handle_input()
        t = 0
        while t < held_ms:
            now[0] += 100
            t += 100
            i.begin_frame()
            ws.handle_input()
        i.source("kbd").set_button("start", False)
        i.begin_frame()
        ws.handle_input()
    finally:
        ws.input = was


def test_holding_the_console_key_opens_the_task_manager(tmp_path, monkeypatch):
    ws, drv = _hand(tmp_path)
    open_cart(ws, "Star Catcher")
    drv.frame(_DT)
    _start_frames(ws, monkeypatch, 300)
    assert ws.pause.open and ws.screen == "desktop"     # a tap: the pause card
    ws.pause.close()
    _start_frames(ws, monkeypatch, 600)
    assert ws.screen == "tasks" and not ws.pause.open and not ws.sysmenu.open


def test_holding_it_in_a_text_field_opens_the_task_manager_and_keeps_the_field(
        tmp_path, monkeypatch):
    from runtime import ui_draw
    ws, drv = _hand(tmp_path)
    monkeypatch.setattr(ui_draw, "input_kind", lambda *a: "tdeck")
    cancelled = []
    ws.cancel_field = lambda: cancelled.append(1) or True
    _start_frames(ws, monkeypatch, 600)
    assert ws.screen == "tasks" and cancelled == []
    _start_frames(ws, monkeypatch, 0)
    assert cancelled == [1]                 # a tap is the field's Cancel


def test_a_compile_beside_the_running_set_keeps_the_exec_arena(tmp_path, monkeypatch):
    """A board's native carts share one exec arena (Player._compile_python):
    a new cart's compile frees it only while no other world runs, or the
    minimised game's machine code is gone under it (a T-Deck panic,
    2026-11-03)."""
    import sys
    import types
    from runtime import player as player_mod
    freed = []
    gfx = sys.modules.get("moy_gfx")
    if gfx is None:
        gfx = types.ModuleType("moy_gfx")
        monkeypatch.setitem(sys.modules, "moy_gfx", gfx)
    monkeypatch.setattr(gfx, "native_code_free_all", lambda: freed.append(1), raising=False)
    monkeypatch.setattr(player_mod, "NATIVE_CARTS", True)
    monkeypatch.setattr(player_mod, "_micropython", types.SimpleNamespace(native=lambda f: f))
    monkeypatch.setattr(player_mod, "_CODE_CACHE", {})
    ws, drv = _hand(tmp_path)
    open_cart(ws, "Star Catcher")
    drv.frame(_DT)
    assert freed == [1]
    _minimise(ws, drv)
    open_cart(ws, "Coin Quest")
    drv.frame(_DT)
    assert freed == [1], "the minimised game's code stays in the arena"
    ws.procs.close(ws.procs.list[0])
    ws.system_key()
    ws.pause.choose(ws.pause.rows.index("Quit"))
    open_cart(ws, "Brick Siege")
    assert freed == [1, 1]


def test_a_chord_on_the_console_key_spends_it(tmp_path, monkeypatch):
    from runtime import console_input
    ws, drv = _hand(tmp_path)
    now = [70000]
    monkeypatch.setattr(console_input, "_ticks_ms", lambda: now[0])
    was = ws.input
    i = InputTable()
    ws.input = i
    try:
        i.source("kbd").set_button("start", True)
        i.begin_frame()
        ws.handle_input()
        i.source("kbd").set_button("up", True)      # another key while it is held
        now[0] += 100
        i.begin_frame()
        ws.handle_input()
        now[0] += 900
        i.begin_frame()
        ws.handle_input()
        i.source("kbd").set_button("start", False)
        i.source("kbd").set_button("up", False)
        i.begin_frame()
        ws.handle_input()
    finally:
        ws.input = was
    assert ws.screen == "launcher" and not ws.sysmenu.open
