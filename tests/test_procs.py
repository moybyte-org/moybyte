"""Several apps at once (runtime/shell/procs.py, docs/os_2026-11/processes.md
§1 and §6): the running set beside the front run, its window states, the
promotion a focus makes, and admission over the running set on each board's
share."""

import pytest

from ws_helpers import build_desktop_ws, make_drv, open_cart, boot_on_fixture


@pytest.fixture(autouse=True)
def _night(monkeypatch):
    boot_on_fixture(monkeypatch)


def _windowed(ws, drv):
    """The front game's window out of full screen, where the desk plays it."""
    drv.frame(1 / 30)
    win = ws.wm._wins["desktop"]
    if win.saved is not None:
        ws.wm._toggle_max(win)
    drv.frame(1 / 30)
    return win


def _two(tmp_path, a="Star Catcher", b="Brick Siege"):
    ws = build_desktop_ws(tmp_path)
    drv = make_drv(ws)
    ws.open_desk()
    drv.frame(1 / 30)
    open_cart(ws, a)
    wa = _windowed(ws, drv)
    open_cart(ws, b)
    wb = _windowed(ws, drv)
    return ws, drv, wa, wb


def test_a_second_game_joins_the_running_set_in_its_own_window(tmp_path):
    ws, drv, wa, wb = _two(tmp_path)
    assert [p.key for p in ws.procs.list] == ["run:1"]
    p = ws.procs.list[0]
    assert ws.wm._wins["run:1"] is wa and ws.wm._wins["desktop"] is wb
    assert p.title() == "Star Catcher" and ws.cart["title"] == "Brick Siege"
    assert ws.wm.stack.kinds()[-2:] == ["run:1", "desktop"]
    # its own owner tag, canvas and audio session
    assert p.player.owner != ws.run.player.owner
    assert p.run.canvas is not ws.run.canvas
    assert p.run.audio is not ws.run.audio
    # a background game reads no input
    assert p.player.detached and p.player.ns["btn"]("left") is False
    assert ws.wm._win_title(wa) == "Star Catcher"


def test_a_shown_game_is_throttled_and_presents_its_last_frame(tmp_path):
    ws, drv, wa, wb = _two(tmp_path)
    p = ws.procs.list[0]
    assert ws.wm.game_shown(p.key)
    draws, ups = p.draws, p.ups
    for _ in range(40):
        drv.frame(1 / 30)
    assert p.state == "throttled"
    assert p.ups - ups == 40                  # every frame's update
    assert p.draws - draws == 40 // ws.procs.n  # every Nth draw
    assert ws.procs.n == 2


def test_a_hidden_game_is_held_and_hears_it(tmp_path):
    ws, drv, wa, wb = _two(tmp_path)
    p = ws.procs.list[0]
    pl = p.player
    heard = []
    pl.ns["_minimise"] = lambda: heard.append("min")
    pl.ns["_restore"] = lambda: heard.append("res")
    ws.wm._minimize(wa, True)
    ups, draws = p.ups, p.draws
    for _ in range(20):
        drv.frame(1 / 20)
    assert p.state == "minimised" and heard == ["min"]
    assert p.draws == draws and p.ups == ups      # paused: no update, no draw
    assert pl.paused                            # its clock held
    ws.wm._minimize(wa, False)
    drv.frame(1 / 30)
    assert heard == ["min", "res"] and p.state == "throttled"
    assert not pl.paused and p.ups > ups


def _app_proc(ws, drv, p):
    """Make Proc `p` an app cart's run: its manifest says `"type": "app"`."""
    p.run.project.cart = dict(p.run.project.cart, type="app")
    p.named()
    assert p.app


def test_a_hidden_app_updates_every_frame_and_never_draws(tmp_path):
    ws, drv, wa, wb = _two(tmp_path)
    p = ws.procs.list[0]
    _app_proc(ws, drv, p)
    ws.wm._minimize(wa, True)
    drv.frame(1 / 30)
    ups, draws = p.ups, p.draws
    for _ in range(20):
        drv.frame(1 / 30)
    assert p.state == "minimised" and not p.player.paused
    assert p.ups - ups == 20 and p.draws == draws


def test_the_front_minimised_holds_a_game_and_steps_an_app(tmp_path):
    ws, drv, wa, wb = _two(tmp_path)
    pl = ws.run.player
    n = [0]
    pl._update = lambda dt: n.__setitem__(0, n[0] + 1)
    ws.wm._minimize(wb, True)
    for _ in range(10):
        drv.frame(1 / 30)
    assert ws.procs.front_minimised() and pl.paused and n[0] == 0
    ws.wm._minimize(wb, False)
    drv.frame(1 / 30)
    assert not pl.paused and n[0] >= 1
    ws.cart["type"] = "app"
    ws.wm._minimize(wb, True)
    drv.frame(1 / 30)
    n[0] = 0
    for _ in range(10):
        drv.frame(1 / 30)
    assert not pl.paused and n[0] == 10


def test_focusing_a_background_game_promotes_it(tmp_path):
    ws, drv, wa, wb = _two(tmp_path)
    rect_a, rect_b = (wa.x, wa.y), (wb.x, wb.y)
    ws.wm._move_focus("run:1")
    assert ws.cart["title"] == "Star Catcher" and ws.wm._focus == "desktop"
    assert ws.wm._wins["desktop"] is wa and ws.wm._wins["run:1"] is wb
    assert (wa.x, wa.y) == rect_a and (wb.x, wb.y) == rect_b
    p = ws.procs.list[0]
    assert p.title() == "Brick Siege" and p.player.detached
    assert not ws.run.player.detached and ws.run.player.ns["btn"] is not None
    assert ws.run.player.ns["btn"].__name__ == "btn"
    drv.frame(1 / 30)
    assert ws.procs.list == [p]


def test_closing_a_background_game_releases_its_run(tmp_path):
    ws, drv, wa, wb = _two(tmp_path)
    p = ws.procs.list[0]
    pl = p.player
    ws.wm.close_window_kind("run:1")
    drv.frame(1 / 30)
    assert ws.procs.list == [] and "run:1" not in ws.wm._wins
    assert pl.ns is None and not pl.detached
    assert ws.procs.spare is p.run          # kept for the next one
    assert ws.cart["title"] == "Brick Siege"
    assert "run:1" not in ws.wm.stack.kinds()


def test_quit_in_a_background_game_closes_it_alone(tmp_path):
    ws, drv, wa, wb = _two(tmp_path)
    p = ws.procs.list[0]
    p.player.ns["_update"] = None
    p.player._update = lambda dt: p.player.ns["quit"]()
    drv.frame(1 / 30)
    assert ws.procs.list == [] and ws.cart["title"] == "Brick Siege"
    assert "desktop" in ws.wm._wins


def test_a_crash_in_a_background_game_closes_it_with_a_notice(tmp_path):
    ws, drv, wa, wb = _two(tmp_path)
    p = ws.procs.list[0]

    def boom(dt):
        raise ValueError("boom")
    p.player._update = boom
    drv.frame(1 / 30)
    assert ws.procs.list == [] and ws.cart_error is None
    assert ws.notice_active()


def test_launching_a_running_game_brings_it_forward(tmp_path):
    ws, drv, wa, wb = _two(tmp_path)
    open_cart(ws, "Star Catcher")
    assert ws.cart["title"] == "Star Catcher" and len(ws.procs.list) == 1
    assert ws.procs.list[0].title() == "Brick Siege"


def test_the_fifth_run_is_refused_naming_the_oldest(tmp_path):
    ws, drv, wa, wb = _two(tmp_path)
    for t in ("Coin Quest", "Hop Quest"):
        open_cart(ws, t)
        _windowed(ws, drv)
    assert len(ws.procs.list) == 3
    open_cart(ws, "Pin Light")
    assert ws.cart["title"] == "Hop Quest" and len(ws.procs.list) == 3
    assert ws.confirm.open and ws.confirm.title == "Close Star Catcher?"
    assert ws.confirm.buttons == ("Cancel", "Close")
    # Close closes it and opens the one refused
    act = ws.confirm._act
    ws.confirm.cancel()
    act()
    assert ws.cart["title"] == "Pin Light"
    assert sorted(p.title() for p in ws.procs.list) == [
        "Brick Siege", "Coin Quest", "Hop Quest"]


LUA_GAME = """
N = 0
function _update()
  N = N + 1
  if CRASH_AT and N >= CRASH_AT then error("boom at " .. N) end
end
function _draw() cls(%d) end
"""


def _lua_game(carts, title, colour, crash_at=None):
    import json
    d = carts / (title.lower().replace(" ", "_") + ".moy")
    d.mkdir(parents=True, exist_ok=True)
    (d / "manifest.json").write_text(json.dumps(
        {"title": title, "canvas": "160x120", "runtime": "lua", "main": "main.lua",
         "moybyte": {"type": "game", "permissions": ["graphics"]}}))
    src = LUA_GAME % colour
    if crash_at is not None:
        src = "CRASH_AT = %d\n" % crash_at + src
    (d / "main.lua").write_text(src)
    (d / "config.json").write_text("{}")


def _lua_desk(tmp_path, *games):
    carts = tmp_path / "carts"
    for g in games:
        _lua_game(carts, *g)
    ws = build_desktop_ws(tmp_path)
    drv = make_drv(ws)
    ws.open_desk()
    drv.frame(1 / 30)
    return ws, drv


def _n(pl):
    return pl._lua.get_global("N")


def test_a_lua_game_minimised_beside_a_python_game_keeps_its_state(tmp_path):
    """A Lua game joins the running set like a Python one: held while its
    window is hidden, its run (its own Lua state) as it was, and it goes
    on from there when it comes back."""
    ws, drv = _lua_desk(tmp_path, ("Lua Count", 8))
    open_cart(ws, "Lua Count")
    wl = _windowed(ws, drv)
    for _ in range(5):
        drv.frame(1 / 30)
    open_cart(ws, "Star Catcher")
    _windowed(ws, drv)
    assert [p.title() for p in ws.procs.list] == ["Lua Count"]
    p = ws.procs.list[0]
    assert p.player._lua is not None and p.player.detached
    ws.wm._minimize(wl, True)
    drv.frame(1 / 30)
    held = _n(p.player)
    for _ in range(20):
        drv.frame(1 / 30)
    assert p.state == "minimised" and p.player.paused
    assert _n(p.player) == held, "a held Lua game ran"
    assert ws.run.player._lua is None and ws.cart["title"] == "Star Catcher"
    ws.wm._minimize(wl, False)
    for _ in range(4):
        drv.frame(1 / 30)
    assert p.state == "throttled" and not p.player.paused
    assert _n(p.player) > held
    # brought to the front, it is the one that plays and reads the keys
    ws.wm._move_focus("run:1")
    assert ws.cart["title"] == "Lua Count" and not ws.run.player.detached
    n = _n(ws.run.player)
    drv.frame(1 / 30)
    assert _n(ws.run.player) > n


def test_two_lua_games_are_two_task_manager_rows_with_their_own_heaps(tmp_path):
    ws, drv = _lua_desk(tmp_path, ("Lua One", 3), ("Lua Two", 9))
    open_cart(ws, "Lua One")
    _windowed(ws, drv)
    open_cart(ws, "Lua Two")
    _windowed(ws, drv)
    for _ in range(6):
        drv.frame(1 / 30)
    one = ws.procs.list[0].player
    one._lua.exec("BIG = {} for i = 1, 4000 do BIG[i] = i end")
    rows = {r[1]: r for r in ws.procs.rows() if r[2] != "service"}
    assert set(rows) == {"Lua One", "Lua Two"}
    a, b = rows["Lua One"], rows["Lua Two"]
    assert a[6] != b[6] and a[6] is not None, "two runs, two pids"
    assert a[5] and b[5] and a[5] > b[5] + 16000, (a[5], b[5])
    assert a[5] == one._lua.heap()
    # each is a row of the kernel's process table: its pid, the Lua tier, a
    # game, running, its engine bound
    from runtime import moy_play
    rows = [moy_play.proc_row(p.player._run_handle) for p in ws.procs.list] + [
        moy_play.proc_row(ws.run.player._run_handle)]
    assert [r[:4] for r in rows] == [(p, 2, 2, 1) for p in (a[6], b[6])], rows
    assert all(r[4] for r in rows)


def test_a_crash_in_one_lua_game_names_its_pid_and_leaves_the_other_running(tmp_path):
    from runtime.system import os_log
    ws, drv = _lua_desk(tmp_path, ("Lua Steady", 3), ("Lua Boom", 9, 6))
    open_cart(ws, "Lua Boom")
    _windowed(ws, drv)
    boom = ws.run.player
    pid = boom.pid
    open_cart(ws, "Lua Steady")
    _windowed(ws, drv)
    steady = ws.run.player
    assert [p.title() for p in ws.procs.list] == ["Lua Boom"]
    for _ in range(30):
        drv.frame(1 / 30)
    assert ws.procs.list == [], "the crashed game kept its row"
    rec = os_log.crash_of(pid)
    assert rec is not None and "boom at" in rec[1], rec
    from runtime import moy_play
    assert moy_play.proc_row(boom._run_handle) is None, "the crashed run kept its process row"
    assert moy_play.proc_row(steady._run_handle)[:4] == (steady.pid, 2, 2, 1)
    assert steady.cart_error is None and ws.run.player is steady
    n = _n(steady)
    drv.frame(1 / 30)
    assert _n(steady) > n
    assert os_log.crash_of(steady.pid) is None


def _wasm_game(carts, title):
    """A compiled game counting its updates into pmem cell 1 (test_wasm_cart's
    COUNTER), built where the store finds it."""
    import json
    import os
    from tools import wat
    from test_wasm_cart import COUNTER
    d = carts / (title.lower().replace(" ", "_") + ".moy")
    d.mkdir(parents=True, exist_ok=True)
    (d / "manifest.json").write_text(json.dumps(
        {"format": "moy-1", "title": title, "runtime": "wasm", "main": "main.wasm",
         "memory": 1, "moybyte": {"type": "game"}}))
    (d / "main.wasm").write_bytes(wat.assemble(COUNTER))
    assert os.path.isfile(str(d / "main.wasm"))


def _ticks(pl):
    return pl._lua._run.pmem()[1][1]


def test_two_compiled_games_run_side_by_side_each_in_its_session(tmp_path):
    """A compiled game joins the running set as a Lua one does: two at once,
    each its own engine session and process row, the held one's world as it
    was until it is given back."""
    from test_wasm_cart import _binding_or_skip
    from runtime import moy_play
    _binding_or_skip()
    carts = tmp_path / "carts"
    _wasm_game(carts, "Count One")
    _wasm_game(carts, "Count Two")
    ws = build_desktop_ws(tmp_path)
    drv = make_drv(ws)
    ws.open_desk()
    drv.frame(1 / 30)
    open_cart(ws, "Count One")
    w1 = _windowed(ws, drv)
    for _ in range(5):
        drv.frame(1 / 30)
    open_cart(ws, "Count Two")
    _windowed(ws, drv)
    assert [p.title() for p in ws.procs.list] == ["Count One"]
    one, two = ws.procs.list[0].player, ws.run.player
    assert ws.cart["title"] == "Count Two" and two.cart_error is None, two.cart_error
    rows = [moy_play.proc_row(pl._run_handle) for pl in (one, two)]
    assert [r[1:4] for r in rows] == [(3, 2, 1)] * 2 and rows[0][0] != rows[1][0], rows
    ws.wm._minimize(w1, True)
    drv.frame(1 / 30)
    held = _ticks(one)
    n2 = _ticks(two)
    for _ in range(10):
        drv.frame(1 / 30)
    assert _ticks(one) == held, "a held compiled game ran"
    assert _ticks(two) > n2
    ws.wm._minimize(w1, False)
    for _ in range(4):
        drv.frame(1 / 30)
    assert _ticks(one) > held


# -- admission on each board's share (player_fit.admit) ------------------------

MIB = 1 << 20


@pytest.mark.parametrize("board", ["tdeck", "guition_s3"])
def test_two_big_games_do_not_fit_an_s3(board):
    from runtime import player_fit as fit
    share = fit.board_share(board)
    assert share == 4 * MIB
    doom = 3 * MIB + 512 * 1024              # Doom's load footprint, about
    big = doom + fit.PY_WORLD
    assert fit.shares_runtime({"runtime": "wasm"})
    assert fit.admit((big, True), [], share, 4) is None
    assert fit.admit((big, True), [("desktop", big, True)], share, 4) == ["desktop"]
    # a big compiled game beside a Python one: the share names it
    py = fit.app_need({"title": "Star Catcher"})
    assert fit.admit((big, True), [("run:1", py, True)], share, 4) == ["run:1"]
    # two small compiled games fit: the share decides, not the tier
    small = 1 * MIB + fit.PY_WORLD
    assert fit.admit((small, True), [("desktop", small, True)], share, 4) is None
    # two Python games fit
    assert fit.admit((py, True), [("desktop", py, True)], share, 4) is None


def test_the_p4s_share_holds_a_big_compiled_game_beside_python_ones():
    from runtime import player_fit as fit
    share = fit.board_share("p4")
    big = 3 * MIB + 512 * 1024 + fit.PY_WORLD
    py = fit.app_need({"title": "Star Catcher"})
    run = [("run:1", py, True), ("desktop", py, True)]
    assert fit.admit((big, True), run, share, 4) is None
    assert fit.admit((py, True), run, share, 4) is None
    # two big compiled games are more than the share
    assert fit.admit((big, True), [("desktop", big, True)], share, 4) == ["desktop"]
    # a runtime that holds one run runs alone (the browser's compiled tier)
    assert fit.admit((big, False), run[:1], share, 4) == ["run:1"]
    assert fit.board_share("guition_p4") == share
    assert fit.board_share("xiao_zero") is None   # a board with no share


def test_the_share_closes_the_fewest_largest_first():
    from runtime import player_fit as fit
    run = [("run:1", 1 * MIB, True), ("run:2", 2 * MIB, True), ("desktop", 512 * 1024, True)]
    assert fit.admit((2 * MIB, True), run, 4 * MIB, 4) == ["run:2"]


def test_the_audio_rows_refuse_a_fifth_source():
    from runtime import player_fit as fit
    run = [("run:%d" % i, MIB // 2, True) for i in range(3)]
    assert fit.admit((MIB // 2, True), run, None, 4) is None
    assert fit.admit((MIB // 2, True), run, None, 3) == ["run:0"]


def test_the_divisor_follows_the_loops_period(tmp_path):
    """The throttled games draw every Nth frame, N rising to THROTTLE_MAX
    while the loop misses the front's period and falling back to
    THROTTLE_MIN once it runs ahead of it: the period is the loop's `dt`."""
    from runtime import procs
    ws, drv, wa, wb = _two(tmp_path)
    for _ in range(40):
        drv.frame(1 / 10)
    assert ws.procs.n == procs.THROTTLE_MAX
    for _ in range(120):
        drv.frame(1 / 60)
    assert ws.procs.n == procs.THROTTLE_MIN
