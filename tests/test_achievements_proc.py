"""Achievements as a background process (docs/os_2026-11/processes.md section 8,
runtime/system/achievements.py, system_carts/moybyte.achievements.moy).

The console starts the process when it loads its store; an `achieve()` from a
cart's notify role is a message the process wakes on in the scheduler's slice;
it records into its document, which the console writes at its next frame, and
posts a notification, which the console shows as the unlock banner. A message
wake with nothing queued costs a frame nothing, and with the process stopped
an event goes nowhere while the reads answer the last document.

THE TIER-CONFORMANCE TEST (docs/os_2026-11.md, phase 5): every test over the
`ws` fixture runs once per tier, the same body over the same process ported
to it -- the same events in, the same store, the same notifications out. The
Python process is the seed cart; the Lua one is a service of its own state
(native/moycore/moycore_svc.h), tests/fixtures/achievements_lua.moy, put in
the seed's place in the store.
"""

import os
import shutil

import pytest

from runtime import moybyte, moy_carts, notifications, scheduler as S
from runtime import achievements as A

from ws_helpers import build_ws, make_drv

LUA_TWIN = os.path.join(os.path.dirname(__file__), "fixtures", "achievements_lua.moy")
WASM_WHY = ("a compiled service needs an ABI the owner has not chosen: its timer "
            "callbacks as exports the OS calls, or WASI's poll_oneoff")


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
    yield w
    S.SCHED.stop(w.ach.pid) if w.ach.pid is not None else None
    notifications.NOTES.reset()


def _store(ws):
    return moy_carts.load_achievements(ws.carts_root)


def test_the_console_starts_the_process_over_its_store(ws):
    p = S.SCHED.procs[ws.ach.pid]
    assert p.kind == S.SERVICE and p.state == S.BACKGROUND
    assert p.name == A.PROC and p.cart["type"] == "service"
    assert "notifications" in p.granted
    # it holds a wake on each of its messages, and nothing a frame serves
    assert S.SCHED.wakes_of(ws.ach.pid) == 2
    assert not [w for w in moybyte.WAKES.list if w[1] == ws.ach.pid]
    # the task manager lists it as a service
    rows = [r for r in ws.procs.rows() if r[6] == ws.ach.pid]
    assert rows and rows[0][1] == "Achievements" and rows[0][2] == "service"


def test_an_achieve_from_a_cart_reaches_the_store_and_posts(ws):
    """A cart's `ctx.notify.achieve` (Paint's save) is a message: nothing
    happens until the console's next frame serves it, before it draws; the
    process records it and posts, the console shows the post as the unlock
    banner, keeping it in the list, and writes the document the process
    handed back."""
    notify = ws.app_context("probe", ("notify",)).notify
    notify.achieve("paint_save")
    assert not notify.has("first_paint") and S.SCHED.mail_of(ws.ach.pid) == 1
    assert ws._toast_until == 0
    drv = make_drv(ws)
    drv.frame(1 / 30)
    assert notify.has("first_paint") and S.SCHED.mail_of(ws.ach.pid) == 0
    assert ws.ach.toast[0] == "first_paint" and ws._toast_until
    notes = notifications.NOTES
    assert [(n.app, n.summary, n.pid) for n in notes.list] == [
        ("Achievements", "Little Artist", ws.ach.pid)]
    assert notes.list[0].body == "Save a drawing"
    assert notes.banner is None             # the unlock banner is its banner
    assert ws.ach.pending is None           # written
    assert "first_paint" in _store(ws)["found"]


def test_the_tick_serves_a_message_the_frame_did_not(ws):
    """A message sent after the frame's start (a callback's, a board's
    service upcall) waits for the scheduler's tick; the console writes what
    it hands back at the start of the next frame."""
    ws.ach.note("run")
    S.SCHED.tick(0)
    assert ws.ach.has("first_run") and ws.ach.pending is not None
    make_drv(ws).frame(1 / 30)
    assert ws.ach.pending is None and "first_run" in _store(ws)["found"]


def test_a_secret_award_raises_the_secrets_banner(ws):
    assert ws.ach.award("konami") is True
    assert ws.ach.award("nope") is False
    S.SCHED.tick(0)
    assert ws.ach.toast == ("konami", "Secret Coder", "spark", True)
    assert ws.ach.award("konami") is False  # found: no message


def test_a_quiet_award_is_recorded_and_not_announced(ws):
    ws.ach.award("first_run", quiet=True, day=20260101)
    S.SCHED.tick(0)
    assert ws.ach.found_on("first_run") == 20260101
    assert ws.ach.toast is None and not notifications.NOTES.list


def test_with_the_process_stopped_events_go_nowhere_and_reads_hold(ws):
    ws.ach.note("run")
    S.SCHED.tick(0)
    assert ws.ach.has("first_run")
    S.SCHED.stop(ws.ach.pid)                # the task manager's Stop
    assert not ws.ach.running() and S.SCHED.wakes_of(ws.ach.pid) == 0
    ws.ach.note("open", "x")
    assert S.SCHED.mail_of(ws.ach.pid) == 0
    assert ws.ach.award("konami") is False
    assert ws.ach.has("first_run") and ws.ach.count() == 1   # the last document
    assert ws.ach.today() == ws._today_ymd()
    ws.load_achievements()                  # a store load starts it again
    assert ws.ach.running() and ws.ach.has("first_run")


def test_a_load_restarts_the_process_and_keeps_what_it_found(ws):
    first = ws.ach.pid
    ws.ach.note("run")
    S.SCHED.tick(0)
    ws.load_achievements()
    assert ws.ach.pid != first and S.SCHED.procs[first].state == S.EXITED
    assert ws.ach.has("first_run") and "first_run" in _store(ws)["found"]


def test_an_idle_message_wake_costs_a_frame_nothing():
    """A message wake is no wake a tick or a board's upcall serves until a
    message is queued, and neither it nor a send asks a board for the
    service upcall: the console serves what is queued at its frame's start
    (a compiled run the kernel drives makes no crossing for it)."""
    w = moybyte.Wakes()
    got = []
    upcalls = []
    w.added = lambda: upcalls.append(1)     # a board's: the service upcall wanted
    wid = w.on("achieve", lambda kind, key=None: got.append((kind, key)))
    out = [0] * 4
    assert w.list == [] and w.queued == 0
    assert w.due(0, out) == 0
    s = S.Scheduler(w, None)
    assert s.tick(0) == 0                   # nothing to run: returns at once
    pid = w.by_id[wid][1]
    assert w.send(pid, "achieve", ("run", None)) and w.queued == 1
    assert not w.send(pid, "award", ("konami",))    # no wake on it
    assert not w.send(pid + 99, "achieve", ("run", None))
    assert upcalls == []                    # neither the wake nor the send asks for one
    assert w.due(0, out) == 1 and out[0] == wid
    w.call(wid)
    assert got == [("run", None)] and w.queued == 0
    for i in range(moybyte.MAIL_MAX + 3):
        w.send(pid, "achieve", ("open", i))
    assert w.queued == moybyte.MAIL_MAX     # the oldest dropped
    w.end(pid)
    assert w.queued == 0 and w.mail == [] and w.by_id == {}
    with pytest.raises(ValueError):
        w.on("lora", lambda: None)
