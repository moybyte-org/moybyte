"""Achievements as a background process (docs/os_2026-11/processes.md section 8,
docs/app_api_v1.md section 9, runtime/system/achievements.py,
system_carts/moybyte.achievements.moy).

The console starts the process when it loads its store. A game declares its
badges in its manifest and unlocks them with `award` and `progress` messages
(`moybyte.award`, `moybyte.progress`); the console's own badges are the
`"system"` sender's. The OS stamps the sender, so a game reaches only the
badges it declares. The process keeps what is found, and each goal's last
value, in its prefs, and posts a notification on a fresh find, which the
console shows as the unlock banner naming the game. A message wake with
nothing queued costs a frame nothing, and with the process stopped a message
goes nowhere while the reads answer its prefs.

THE TIER-CONFORMANCE TEST (docs/os_2026-11.md, phase 5): every test over the
`ws` fixture runs once per tier, the same body over the same process ported
to it -- the same messages in, the same prefs row, the same notifications
out. The Python process is the seed cart; the Lua one is a service of its
own state (native/moycore/moycore_svc.h), tests/fixtures/achievements_lua.moy,
put in the seed's place in the store.
"""

import json
import os
import shutil

import pytest

from runtime import moy_evq, moybyte, notifications, os_log, scheduler as S
from runtime import moy_log as L
from runtime import achievements as A

from ws_helpers import build_ws, make_drv, open_cart, settle_badges

LUA_TWIN = os.path.join(os.path.dirname(__file__), "fixtures", "achievements_lua.moy")
WASM_WHY = ("the scheduler starts a WASI service (native/moy_wasm/moy_wasi.h, A SERVICE), "
            "but the achievements process is not ported to it, and a WASI program "
            "imports no console call to post its notifications with")
GAME = "moybyte.harpoon_pop"            # a seed game that declares four badges
_SINCE = [0]


def _lua_twin(ws):
    """The Lua process in the seed's folder, and the console's load of it."""
    if S._svc() is None:
        pytest.skip("no Lua runtime on this host")
    dst = os.path.join(ws.carts_root, "moybyte.achievements.moy")
    shutil.rmtree(dst)
    shutil.copytree(LUA_TWIN, dst)
    ws.carts.rescan()
    ws.load_achievements()
    assert S.SCHED.procs[ws.ach.pid].native


@pytest.fixture(params=["python", "lua", pytest.param(
    "wasm", marks=pytest.mark.xfail(run=False, reason=WASM_WHY))])
def ws(request, tmp_path):
    notifications.NOTES.reset()
    w = build_ws(tmp_path)
    if request.param == "lua":
        _lua_twin(w)
    _SINCE[0] = L.next()
    yield w
    S.SCHED.stop(w.ach.pid) if w.ach.pid is not None else None
    notifications.NOTES.reset()


def _as(app, name, *args, pid=4242):
    """`moybyte.send` to the process from a process of app `app`: the OS
    stamps the queue's app id, never the sender's word."""
    moy_evq.open(pid, app, "", True)
    was = L.current()
    L.current(pid)
    try:
        return moybyte.send(A.PROC, name, *args)
    finally:
        L.current(was)
        moy_evq.close(pid)


def _row(ws):
    """The process's prefs row, as it stands in system/settings."""
    t = ws.system.text(ws.ach.key)
    return json.loads(t) if t else None


def _log(ws):
    return [t for _s, _t, _l, q, t in os_log.records(_SINCE[0], 128) if q == ws.ach.pid]


def test_the_console_starts_the_process(ws):
    p = S.SCHED.procs[ws.ach.pid]
    assert p.kind == S.SERVICE and p.state == S.BACKGROUND
    assert p.name == A.PROC and p.cart["type"] == "service"
    assert "notifications" in p.granted and "prefs" in p.granted
    assert p.cart["receives"] == ["award", "progress"]
    # it holds a wake on each of its messages, and nothing a frame serves
    assert S.SCHED.wakes_of(ws.ach.pid) == 2
    assert not [w for w in moybyte.WAKES.list if w[1] == ws.ach.pid]
    rows = [r for r in ws.procs.rows() if r[6] == ws.ach.pid]
    assert rows and rows[0][1] == "Achievements" and rows[0][2] == "service"


def test_a_shell_award_reaches_the_prefs_and_posts(ws):
    """Paint's save, through the notify role: a message nothing serves until
    the console's next frame, before it draws; the process records it in
    its prefs and posts, and the console shows the post as the unlock
    banner, keeping it in the list."""
    notify = ws.app_context("probe", ("notify",)).notify
    notify.award("first_paint")
    assert not ws.ach.has(A.SYSTEM, "first_paint") and S.SCHED.mail_of(ws.ach.pid) == 1
    assert ws._toast_until == 0
    make_drv(ws).frame(1 / 30)
    assert ws.ach.has(A.SYSTEM, "first_paint") and S.SCHED.mail_of(ws.ach.pid) == 0
    assert ws.ach.toast == (A.HEAD_BADGE, "Little Artist", "paint", False)
    assert ws._toast_until
    notes = notifications.NOTES
    assert [(n.app, n.summary, n.body, n.pid) for n in notes.list] == [
        ("Achievements", A.HEAD_BADGE, "Little Artist", ws.ach.pid)]
    assert notes.banner is None             # the unlock banner is its banner
    day, title, icon, hint = _row(ws)["system"]["found"]["first_paint"]
    assert (title, icon, hint) == ("Little Artist", "paint", "Save a drawing")
    assert day == ws._today_ymd()


def test_the_tick_serves_a_message_the_frame_did_not(ws):
    ws.ach.award("first_run")
    S.SCHED.tick(0)
    assert ws.ach.has(A.SYSTEM, "first_run")


def test_a_secret_raises_the_secrets_banner_once(ws):
    assert ws.ach.award("konami") is True
    S.SCHED.tick(0)
    assert ws.ach.toast == (A.HEAD_SECRET, "Secret Coder", "spark", True)
    assert ws.ach.award("konami") is False      # found: no message
    assert len(notifications.NOTES.list) == 1


def test_the_shells_counted_milestones_report_progress(ws):
    """The shell counts its own distinct carts and editors in its own row
    and reports the count; the process stores the last value and finds the
    badge at its goal."""
    for k in ("a", "b", "a", "c"):
        ws.ach.played(k)
    settle_badges(ws)
    assert ws.system.get(A.MILESTONES)["played"] == ["a", "b", "c"]
    assert _row(ws)["system"]["value"] == {"play_five": 3}
    assert ws.ach.has(A.SYSTEM, "first_open")
    for v in ("code", "cards", "paint", "map"):
        ws.ach.used(v)
    settle_badges(ws)
    rec = _row(ws)["system"]
    assert "toolbox" in rec["found"] and "toolbox" not in rec.get("value", {})
    ws.ach.played("d")
    ws.ach.played("e")
    settle_badges(ws)
    assert "play_five" in _row(ws)["system"]["found"]
    assert "value" not in _row(ws)["system"]


def test_a_game_unlocks_its_own_badges_and_the_banner_names_it(ws):
    assert _as(GAME, "award", "first_clear") == 1
    settle_badges(ws)
    assert ws.ach.toast == ("Harpoon Pop: badge found!", "Bubble Buster", "star", False)
    assert ws.ach.has(GAME, "first_clear") and not ws.ach.has(A.SYSTEM, "first_clear")
    _as(GAME, "award", "untouchable")
    settle_badges(ws)
    assert ws.ach.toast == ("Harpoon Pop: secret found!", "Untouchable", "heart", True)
    assert _row(ws)[GAME]["name"] == "Harpoon Pop"


def test_a_goal_stores_the_last_value_and_unlocks_at_the_goal(ws):
    _as(GAME, "progress", "high_tide", 3)
    settle_badges(ws)
    assert _row(ws)[GAME] == {"name": "Harpoon Pop", "value": {"high_tide": 3}}
    assert ws.ach.toast is None and not notifications.NOTES.list
    _as(GAME, "progress", "high_tide", 2)       # the last value, not the best
    settle_badges(ws)
    assert _row(ws)[GAME]["value"] == {"high_tide": 2}
    _as(GAME, "progress", "high_tide", 7)
    settle_badges(ws)
    assert "high_tide" in _row(ws)[GAME]["found"] and "value" not in _row(ws)[GAME]
    assert ws.ach.toast[1] == "High Tide"


def test_what_the_sender_does_not_declare_is_refused_and_logged(ws):
    """A game reaches only its own manifest's badges: another game's id, the
    console's own, an id nobody declares, a progress on a badge with no goal
    or with no number, and any id from an app that declares none."""
    _as(GAME, "award", "konami")                # the console's
    _as(GAME, "award", "nope")
    _as(GAME, "progress", "first_clear", 1)     # no goal
    _as(GAME, "progress", "high_tide", "far")   # not a whole number
    _as("local.other", "award", "first_clear")  # Harpoon Pop's, from another app
    settle_badges(ws)
    assert _row(ws) is None and not notifications.NOTES.list
    log = "\n".join(_log(ws))
    for words in ("moybyte.harpoon_pop's konami refused: not declared",
                  "moybyte.harpoon_pop's nope refused: not declared",
                  "moybyte.harpoon_pop's first_clear refused: no goal",
                  "moybyte.harpoon_pop's high_tide refused: not a whole number",
                  "local.other's first_clear refused: not declared"):
        assert words in log, log


def test_an_app_that_does_not_list_the_names_cannot_send_them(ws):
    """The process's manifest lists `award` and `progress` under `receives`,
    and only those: another name from another app is not taken."""
    assert _as(GAME, "award", "first_clear") == 1
    assert _as(GAME, "reset") == 0
    settle_badges(ws)


def test_the_page_lists_by_game_and_keeps_a_deleted_games_badges(ws):
    _as(GAME, "award", "first_clear")
    _as(GAME, "progress", "high_tide", 2)
    ws.ach.award("first_run")
    settle_badges(ws)
    groups = ws.ach.groups()
    assert [g[:2] for g in groups] == [(A.SYSTEM, "Moybyte"), (GAME, "Harpoon Pop")]
    hp = {b[0]: b for b in groups[1][2]}
    assert hp["first_clear"][6] == ws._today_ymd() and hp["high_tide"][4:] == (5, False, None, 2)
    assert hp["untouchable"][5] is True and hp["untouchable"][6] is None
    assert ws.ach.count() == 2 and ws.ach.total() == len(A.SHELL) + 4
    cart = next(c for c in ws.carts.all if c.get("id") == GAME)
    shutil.rmtree(cart["path"])
    ws.carts.rescan()
    groups = ws.ach.groups()
    assert [g[:2] for g in groups] == [(A.SYSTEM, "Moybyte"), (GAME, "Harpoon Pop")]
    assert [b[:4] for b in groups[1][2]] == [("first_clear", "Bubble Buster", "star",
                                              "Clear level 1")]
    assert ws.ach.total() == len(A.SHELL) + 1


def test_with_the_process_stopped_messages_go_nowhere_and_reads_hold(ws):
    ws.ach.award("first_run")
    S.SCHED.tick(0)
    S.SCHED.stop(ws.ach.pid)                # the task manager's Stop
    assert not ws.ach.running() and S.SCHED.wakes_of(ws.ach.pid) == 0
    ws.ach.played("x")
    assert S.SCHED.mail_of(ws.ach.pid) == 0
    assert ws.ach.award("konami") is False
    assert ws.ach.has(A.SYSTEM, "first_run") and ws.ach.count() == 1   # its prefs
    assert ws.ach.today() == ws._today_ymd()
    ws.load_achievements()                  # a store load starts it again
    assert ws.ach.running() and ws.ach.has(A.SYSTEM, "first_run")


def test_a_load_restarts_the_process_and_keeps_what_it_found(ws):
    first = ws.ach.pid
    ws.ach.award("first_run")
    S.SCHED.tick(0)
    ws.load_achievements()
    assert ws.ach.pid != first and S.SCHED.procs[first].state == S.EXITED
    assert ws.ach.has(A.SYSTEM, "first_run")


def test_harpoon_pop_awards_its_badges_as_it_plays(tmp_path):
    """The seed game end to end: a cleared first level with no life lost is
    Bubble Buster and the secret Untouchable, and High Tide's bar moves to
    the level reached, which the game keeps in its own pmem."""
    notifications.NOTES.reset()
    ws = build_ws(tmp_path)
    try:
        open_cart(ws, "Harpoon Pop")
        drv = make_drv(ws)
        drv.frame(1 / 30)
        del ws.ns["bubbles"][:]             # the arena cleared
        drv.frame(1 / 30)
        settle_badges(ws)
        rec = ws.ach.doc()[GAME]
        assert sorted(rec["found"]) == ["first_clear", "untouchable"]
        assert rec["value"] == {"high_tide": 2}
        assert ws.ns["pmem"](1) == 2
    finally:
        S.SCHED.stop(ws.ach.pid)
        notifications.NOTES.reset()


def test_an_idle_message_wake_costs_a_frame_nothing():
    """A message wake is no wake a tick or a board's upcall serves until a
    message is queued on its process's event queue, and neither it nor a
    post asks a board for the service upcall: the console serves what is
    queued at its frame's start (a compiled run the kernel drives makes no
    crossing for it)."""
    w = moybyte.Wakes()
    s = S.Scheduler(w, None)
    got = []
    upcalls = []
    w.added = lambda: upcalls.append(1)     # a board's: the service upcall wanted
    pid = L.spawn("t.idle")
    was = L.current()
    L.current(pid)
    try:
        wid = w.on("ping", lambda who, kind, key=None: got.append((who, kind, key)))
    finally:
        L.current(was)
    out = [0] * 4
    assert w.list == [] and w.queued == 0
    assert w.due(0, out) == 0
    assert s.tick(0) == 0                   # nothing to run: returns at once
    assert w.post(pid, "ping", ("run", None)) and w.queued == 1
    assert not w.post(pid, "pong", ("konami",))     # it does not listen on it
    assert not w.post(pid + 99, "ping", ("run", None))
    assert upcalls == []                    # neither the wake nor the post asks for one
    assert w.due(0, out) == 0               # a message is no due wake: the frame's start serves it
    s.mail()
    assert got == [("system", "run", None)] and w.queued == 0
    for i in range(moy_evq.DEPTH + 3):
        w.post(pid, "ping", ("open", i))
    assert s.mail_of(pid) == moy_evq.DEPTH  # the oldest dropped
    w.end(pid)
    assert w.mail == [] and w.by_id == {} and not moy_evq.has(pid)
    assert wid not in w.by_id
    with pytest.raises(ValueError):
        w.on("", lambda: None)


LUA_GAME = '''
function _init()
  moybyte.award("gold")
  moybyte.award("konami")
  moybyte.progress("steps", 2)
end

function _draw() end
'''


def test_a_lua_game_awards_its_own_badges(tmp_path):
    """A Lua run holds `moybyte.award` and `moybyte.progress` (and
    `moybyte.send`) as a service does: the Python face's verbs, run as the
    run's process, so the OS stamps the game's app id and the game reaches
    its own manifest's badges and no other's."""
    if S._svc() is None:
        pytest.skip("no Lua runtime on this host")
    notifications.NOTES.reset()
    ws = build_ws(tmp_path)
    try:
        from runtime import moy_carts, app_perms
        cart = moy_carts.create("Lua Badges", ws.carts_root, src=LUA_GAME, type="game",
                                runtime="lua", main="main.lua")
        man_path = os.path.join(cart["path"], "manifest.json")
        with open(man_path) as f:
            man = json.load(f)
        man["moybyte"]["achievements"] = [
            {"id": "gold", "title": "Gold", "icon": "star", "hint": "Find gold"},
            {"id": "steps", "title": "Steps", "icon": "run", "hint": "Walk", "goal": 3}]
        with open(man_path, "w") as f:
            json.dump(man, f)
        ws.carts.rescan()
        app = app_perms.app_id(next(c for c in ws.carts.all if c["title"] == "Lua Badges"))
        _SINCE[0] = L.next()
        open_cart(ws, "Lua Badges")
        make_drv(ws).frame(1 / 30)
        settle_badges(ws)
        rec = ws.ach.doc()[app]
        assert rec["name"] == "Lua Badges" and sorted(rec["found"]) == ["gold"]
        assert rec["value"] == {"steps": 2}
        assert ("Lua Badges: badge found!", "Gold") in [
            (n.summary, n.body) for n in notifications.NOTES.list]
        assert not ws.ach.has(A.SYSTEM, "konami")
        assert any("konami refused: not declared" in t for t in _log(ws))
    finally:
        S.SCHED.stop(ws.ach.pid)
        notifications.NOTES.reset()


def _live_procs():
    return [p.pid for p in S.SCHED.procs.values()
            if p.name == A.PROC and p.state != S.EXITED]


def test_one_process_a_vm_owned_by_the_newest_console(tmp_path):
    """A VM runs one achievements process, the newest console's: each
    console that loads ends the last one's, and a console that never loads
    (a bare Workstation) still ends it, so a badge the old console sent is
    never served into the new console's frame as a banner over its page."""
    consoles = []
    for i in range(4):
        ws = build_ws(tmp_path / ("c%d" % i))
        consoles.append(ws)
        assert _live_procs() == [ws.ach.pid]
    consoles[-1].ach.award("first_open")         # queued, not yet served
    from runtime import console, host_app
    from runtime.host_canvas import make_system_canvas
    from runtime.input import InputState
    bare = console.Workstation(host_app._NullComp(),
                               make_system_canvas(host_app.WIDTH, host_app.HEIGHT),
                               InputState(), [])
    assert _live_procs() == [] and bare.ach.pid is None
    bare.ach.award("first_open")                 # no process: goes nowhere
    assert not notifications.NOTES.list
