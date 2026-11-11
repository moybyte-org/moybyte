"""The app guide's worked examples (docs/app_api_v1.md), written from the guide
alone and run on the real console: a Python app whose list scrolls and whose
choice survives a reopen through prefs, a Lua service that answers a message
from another app, and a Lua app that pings that service and hears the answer
through `moybyte.on`, posts a notification (and gets none without the
permission), sets an alarm and writes the OS log. A failure here is the guide
promising something the console does not do."""

import json
import shutil
from pathlib import Path

import pytest

from runtime import app_perms, host_app, moy_evq, notifications, os_log, scheduler as S
from runtime import moy_log as L

from ws_helpers import build_ws, select_on_home

APPS = Path(__file__).resolve().parent / "fixtures" / "apps"
DT = 1.0 / 30


def _install(ws, name):
    """Copy a fixture cart onto the console's shelf, as a push does."""
    shutil.copytree(APPS / name, Path(ws.carts_root) / name)
    ws.carts.rescan()
    title = json.loads((APPS / name / "manifest.json").read_text())["title"]
    return next(c for c in ws.carts.all if c["title"] == title)


def _frames(drv, n=3):
    for _ in range(n):
        drv.ws.mark_dirty()
        drv.frame(DT)


def _open(ws, title):
    assert select_on_home(ws, title) is not None
    ws.open()
    assert ws.run.player.cart_error is None, ws.run.player.cart_error
    drv = host_app.ConsoleDriver(ws)
    _frames(drv)
    return ws.run.player.ns, drv


def test_the_python_app_scrolls_its_list_and_keeps_its_choice(tmp_path):
    ws = build_ws(tmp_path)
    _install(ws, "shopping.moy")
    ns, drv = _open(ws, "Shopping")
    assert ns["cur"] == 0
    for _ in range(30):                     # well past the rows a screen shows
        drv.press("down")
        _frames(drv)
    drv.press("a")
    _frames(drv)
    assert ns["cur"] == 30
    assert ns["prefs"].get("cur") == 30
    ws.exit()
    _frames(drv)
    ns, drv = _open(ws, "Shopping")         # a new run reads its prefs back
    assert ns["cur"] == 30


@pytest.fixture
def echo(tmp_path):
    if S._svc() is None:
        pytest.skip("no Lua runtime on this host")
    ws = build_ws(tmp_path)
    cart = _install(ws, "echo_lua.moy")
    pid = S.start_cart(ws, cart)
    assert pid is not None
    S.SCHED.poll()
    yield pid
    S.SCHED.stop(pid)


def _peer(app, receives="pong"):
    """A process of app `app` listening on "pong", as another app's would:
    the answer comes from another app, so its manifest lists the name under
    `receives` (docs/app_api_v1.md section 8). Its queue is not the VM's,
    so the scheduler's mail leaves it for the test to take."""
    pid = L.spawn("peer")
    assert moy_evq.open(pid, app, receives, False) == moy_evq.OK
    assert moy_evq.listen(pid, "pong", True) == moy_evq.OK
    return pid


def _serve():
    for _ in range(3):
        S.SCHED.mail()
        S.SCHED.poll()


def test_the_lua_service_answers_a_message_from_another_app(echo):
    assert S.SCHED.procs[echo].state == S.BACKGROUND
    peer = _peer("local.pinger")
    try:
        assert moy_evq.send(peer, "local.echo", "ping", "[41]")[0] == 1
        _serve()
        assert moy_evq.take(peer) == (echo, "local.echo", "pong", "[42]")
    finally:
        moy_evq.close(peer)


def test_another_app_reaches_only_the_names_the_service_receives(echo):
    peer = _peer("local.pinger")
    try:
        assert moy_evq.send(peer, "local.echo", "hello", "[1]")[0] == 0
    finally:
        moy_evq.close(peer)


def test_an_answer_reaches_an_app_only_on_a_name_it_receives(echo):
    peer = _peer("local.pinger", receives="")
    try:
        assert moy_evq.send(peer, "local.echo", "ping", "[1]")[0] == 1
        _serve()
        assert moy_evq.take(peer) is None
    finally:
        moy_evq.close(peer)


def _pinger(ws, off=()):
    """The Lua app opened beside the running Echo service, its permissions
    `off` turned off in Settings first, its frames and the scheduler's
    serves run until the answer could have come back."""
    cart = _install(ws, "pinger_lua.moy")
    for perm in off:
        ws.consent.turn(app_perms.app_id(cart), perm, False)
    _ns, drv = _open(ws, "Pinger")
    for _ in range(4):
        _serve()
        S.SCHED.tick(0)
        _frames(drv, 1)
    return ws.run.player._lua


def test_the_lua_app_hears_the_services_answer_through_on(tmp_path):
    if S._svc() is None:
        pytest.skip("no Lua runtime on this host")
    ws = build_ws(tmp_path)
    echo = S.start_cart(ws, _install(ws, "echo_lua.moy"))
    assert echo is not None
    since = L.next()
    try:
        lua = _pinger(ws)
        assert lua.get_global("ANSWER") == "local.echo says 42"
        assert lua.get_global("RANG") == 1
        pid = ws.run.player.pid
        assert "pinged the echo" in [t for _s, _t, _l, q, t in os_log.records(since, 128)
                                     if q == pid]
        nid = lua.get_global("NOTE")
        assert isinstance(nid, int) and notifications.NOTES.find(nid) is not None
    finally:
        S.SCHED.stop(echo)


def test_the_lua_app_posts_no_notification_without_the_permission(tmp_path):
    if S._svc() is None:
        pytest.skip("no Lua runtime on this host")
    ws = build_ws(tmp_path)
    lua = _pinger(ws, off=("notifications",))
    assert lua.get_global("NOTE") is None
    assert lua.get_global("RANG") == 1, "the alarm is the alarms permission's"
