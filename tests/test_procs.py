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
    assert p.player.owner != ws.player.owner
    assert p.world.canvas is not ws.canvas
    assert p.world.audio is not ws.audio
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


def test_a_hidden_game_is_minimised_and_hears_it(tmp_path, monkeypatch):
    from runtime import procs as procs_mod
    ws, drv, wa, wb = _two(tmp_path)
    p = ws.procs.list[0]
    heard = []
    p.player.ns["_minimise"] = lambda: heard.append("min")
    p.player.ns["_restore"] = lambda: heard.append("res")
    ws.wm._minimize(wa, True)
    now = [500000]
    monkeypatch.setattr(procs_mod, "_ticks_ms", lambda: now[0])
    ups, draws = p.ups, p.draws
    for _ in range(20):                       # 1 s at 50 ms a frame
        now[0] += 50
        drv.frame(1 / 20)
    assert p.state == "minimised" and heard == ["min"]
    assert p.draws == draws
    assert 9 <= p.ups - ups <= 10
    ws.wm._minimize(wa, False)
    drv.frame(1 / 30)
    assert heard == ["min", "res"] and p.state == "throttled"


def test_focusing_a_background_game_promotes_it(tmp_path):
    ws, drv, wa, wb = _two(tmp_path)
    rect_a, rect_b = (wa.x, wa.y), (wb.x, wb.y)
    ws.wm._move_focus("run:1")
    assert ws.cart["title"] == "Star Catcher" and ws.wm._focus == "desktop"
    assert ws.wm._wins["desktop"] is wa and ws.wm._wins["run:1"] is wb
    assert (wa.x, wa.y) == rect_a and (wb.x, wb.y) == rect_b
    p = ws.procs.list[0]
    assert p.title() == "Brick Siege" and p.player.detached
    assert not ws.player.detached and ws.player.ns["btn"] is not None
    assert ws.player.ns["btn"].__name__ == "btn"
    drv.frame(1 / 30)
    assert ws.procs.list == [p]


def test_closing_a_background_game_releases_its_world(tmp_path):
    ws, drv, wa, wb = _two(tmp_path)
    p = ws.procs.list[0]
    pl = p.player
    ws.wm.close_window_kind("run:1")
    drv.frame(1 / 30)
    assert ws.procs.list == [] and "run:1" not in ws.wm._wins
    assert pl.ns is None and not pl.detached
    assert ws.procs.spare is p.world          # kept for the next one
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


def test_a_lua_game_runs_alone(tmp_path, monkeypatch):
    ws = build_desktop_ws(tmp_path)
    drv = make_drv(ws)
    ws.open_desk()
    open_cart(ws, "Star Catcher")
    _windowed(ws, drv)
    lua = next(c for c in ws.carts.all if c.get("runtime") == "lua"
               and c.get("type", "game") == "game")
    ws.open(lua)
    assert ws.confirm.open and ws.cart["title"] == "Star Catcher"
    assert ws.procs.list == []


# -- admission on each board's share (player_fit.admit) ------------------------

MIB = 1 << 20


@pytest.mark.parametrize("board", ["tdeck", "guition_s3"])
def test_two_big_games_do_not_fit_an_s3(board):
    from runtime import player_fit as fit
    share = fit.board_share(board)
    assert share == 4 * MIB
    doom = 3 * MIB + 512 * 1024              # Doom's load footprint, about
    big = doom + fit.PY_WORLD
    assert fit.admit((big, False), [], share, 4) is None
    assert fit.admit((big, False), [("desktop", big, False)], share, 4) == ["desktop"]
    # a big compiled game beside a Python one: the share names it
    py = fit.app_need({"title": "Star Catcher"})
    assert fit.admit((big, False), [("run:1", py, True)], share, 4) == ["run:1"]
    # two Python games fit
    assert fit.admit((py, True), [("desktop", py, True)], share, 4) is None


def test_the_p4s_share_holds_a_big_game_beside_python_ones():
    from runtime import player_fit as fit
    share = fit.board_share("p4")
    big = 3 * MIB + 512 * 1024 + fit.PY_WORLD
    py = fit.app_need({"title": "Star Catcher"})
    run = [("run:1", py, True), ("desktop", py, True)]
    assert fit.admit((big, False), run[:1], share, 4) == ["run:1"]  # the tier: alone
    assert fit.admit((py, True), run, share, 4) is None
    assert fit.admit((big, False), [("desktop", big, False)], share, 4) == ["desktop"]
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
