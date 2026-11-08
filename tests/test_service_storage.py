"""A background service's storage is its app's (owner, 2026-11-08;
docs/os_2026-11/processes.md sections 2 and 5, tiers.md section 2).

A service gets the same files and prefs roles as a foreground run of its app,
under its manifest's grants, on a run grant of its own whose owner is its pid
(runtime/shell/system_api.py's `service_api`): a Python service holds the
names a Python app holds, a Lua service's state opens the names a Lua app's
does (native/moy_app/moy_app_lua.h). What it writes, the app reads; what it
is not granted has no name. The app and its service talk through messages:
`moybyte.send(its own app id, name, ...)`, which the rule always lets through
within one app, and which another app's process reaches only by a name the
receiver's manifest lists under `receives`.
Concurrent edits are the app's own business; the OS syncs nothing.

Every test runs once per tier, the same body over the Python service and
its Lua twin. The host steps a Lua service at the scheduler's poll, so its
role rows run in place; a P4 runs it on a task of its own and hops each row
to the VM's task (moycore_svc.h), which only glass reaches."""

import json

import pytest

from runtime import app_perms, moybyte, notifications, os_log, scheduler as S, system_api
from runtime import moy_log as L

from ws_helpers import build_ws, make_drv, select_on_home

APP = "local.pair"
_SINCE = 0

PY = '''
def heard(who, *args):
    prefs.set("heard", list(args))
    moybyte.send(who, "pong", args[0])


def moy_start():
    files.save_text("from_service", "hello from the service")
    prefs.set("seen", 3)
    moybyte.on("ping", heard)
'''

LUA = '''
local function heard(who, ...)
    local args = {...}
    prefs.set("heard", args)
    moybyte.send(who, "pong", args[1])
end

function moy_start()
    files.save_text("from_service", "hello from the service")
    prefs.set("seen", 3)
    moybyte.on("ping", heard)
end
'''

# A service that only waits: what it holds of the role names is read off
# its namespace (Python) or logged at its start (Lua).
PY_BARE = '''
def moy_start():
    moybyte.every(1000, lambda: None)
'''

LUA_BARE = '''
function moy_start()
    print(tostring(files ~= nil) .. " " .. tostring(prefs ~= nil) .. " " .. tostring(io ~= nil))
    moybyte.every(1000, function() end)
end
'''

# The app: it waits on "pong" and sends its service "ping" at its start.
APP_SRC = '''
import moybyte
got = []


def _init():
    moybyte.on("pong", lambda who, *a: got.append((who,) + a))
    moybyte.send("local.pair", "ping", 7)


def _update(dt):
    pass


def _draw():
    pass
'''


@pytest.fixture(params=["python", "lua"])
def tier(request):
    if request.param == "lua" and S._svc() is None:
        pytest.skip("no Lua runtime on this host")
    return request.param


@pytest.fixture
def ws(tmp_path):
    notifications.NOTES.reset()
    w = build_ws(tmp_path)
    yield w
    for p in list(S.SCHED.services()):
        if p.cart is not None and p.cart.get("id") == APP:
            S.SCHED.stop(p.pid)
    notifications.NOTES.reset()


def _service(tier, src=None, perms=("files", "prefs")):
    lua = tier == "lua"
    if src is None:
        src = LUA if lua else PY
    return {"id": APP, "title": "Pair", "type": "service",
            "runtime": "lua" if lua else "python", "src": src,
            "permissions": list(perms), "required": [], "path": "/carts/pair.moy"}


def _start(ws, cart, rec=None):
    global _SINCE
    _SINCE = L.next()
    S.attach(ws)
    pid = S.SCHED.start(cart, rec, app_perms.services(ws), None, ws, cart)
    S.SCHED.poll()
    return pid


def _app(ws, perms=("files", "prefs")):
    """The app's names, as a foreground run of the same app is handed them."""
    cart = {"id": APP, "title": "Pair", "type": "app", "permissions": list(perms)}
    return system_api.make_system_api(ws.app_context, cart, ws.canvas)


def _log(pid):
    """The OS log's lines of `pid` since the last `_start`."""
    return [t for _s, _t, _l, q, t in os_log.records(_SINCE, 128) if q == pid]


def test_a_service_writes_a_file_and_a_pref_that_its_app_reads(ws, tier):
    pid = _start(ws, _service(tier))
    assert S.SCHED.procs[pid].state == S.BACKGROUND, _log(pid)
    app = _app(ws)
    assert app["files"].load_text("from_service") == ("hello from the service", None)
    assert app["prefs"].get("seen") == 3
    # the app writes, the service reads: one store, one namespace
    app["prefs"].set("from_app", "hi")
    assert ws.system.get(APP + "_from_app") == "hi"


def test_the_service_holds_a_grant_of_its_own(ws, tier):
    """Its grant is owned by its pid, so a foreground run of the same app
    ending its own grant leaves the service's standing."""
    pid = _start(ws, _service(tier))
    p = S.SCHED.procs[pid]
    assert p.ctx is not None
    fg = ws.app_context(APP, ("prefs",), APP, run=True)
    assert fg.grant != p.ctx.grant
    fg.end()
    S.SCHED.send(pid, "ping", ("after", 1))
    S.SCHED.mail()
    S.SCHED.poll()
    assert _app(ws)["prefs"].get("heard") == ["after", 1]
    S.SCHED.stop(pid)
    assert p.ctx is None                    # its end ends its grant


def test_a_service_without_the_grant_has_no_names(ws, tier):
    pid = _start(ws, _service(tier, LUA_BARE if tier == "lua" else PY_BARE, ()))
    p = S.SCHED.procs[pid]
    if tier == "lua":
        assert "false false false" in _log(pid)
    else:
        assert "files" not in p.ns and "prefs" not in p.ns
    assert p.ctx is None


def test_a_service_whose_grant_settings_turned_off_reads_none(ws, tier):
    """A Python service's names read None, as an app's do; a Lua one's
    state opens none."""
    rec = {"perms": ["files", "prefs"], "off": ["files", "prefs"]}
    pid = _start(ws, _service(tier, LUA_BARE if tier == "lua" else PY_BARE), rec)
    p = S.SCHED.procs[pid]
    if tier == "lua":
        assert "false false false" in _log(pid)
    else:
        assert p.ns["files"] is None and p.ns["prefs"] is None


def test_an_app_and_its_service_round_trip_a_message(tier, tmp_path):
    """The app tells its service at its start; the service records what it
    heard in the shared prefs and tells the app back."""
    root = tmp_path / "carts" / "pair.moy"
    root.mkdir(parents=True)
    (root / "manifest.json").write_text(json.dumps({
        "title": "Pair", "id": APP, "canvas": "320x240",
        "moybyte": {"type": "app", "permissions": ["files", "prefs"]}}))
    (root / "main.py").write_text(APP_SRC)
    (root / "config.json").write_text("{}")
    ws = build_ws(tmp_path)
    pid = _start(ws, _service(tier))
    assert select_on_home(ws, "Pair") is not None
    ws.open()
    assert ws.player.cart_error is None, ws.player.cart_error
    drv = make_drv(ws)
    for _ in range(4):
        drv.frame(1 / 30)
        S.SCHED.poll()
    assert ws.player.ns["prefs"].get("heard") == [7]
    assert ws.player.ns["got"] == [(APP, 7)]
    assert S.SCHED.procs[pid].state == S.BACKGROUND
    S.SCHED.stop(pid)


@pytest.mark.parametrize("receives", [None, ["ping"]])
def test_another_apps_send_needs_the_name_in_receives(ws, tier, monkeypatch, receives):
    cart = _service(tier)
    if receives is not None:
        cart["receives"] = receives
    pid = _start(ws, cart)
    monkeypatch.setattr(S.SCHED, "app_of",
                        lambda q: "local.other" if q == 4242 else (APP if q == pid else None))
    was = L.current()
    L.current(4242)
    try:
        took = moybyte.send(APP, "ping", "x")
        assert took == (1 if receives else 0)
    finally:
        L.current(was)
        moybyte.WAKES.end(4242)
    S.SCHED.mail()
    S.SCHED.poll()
    assert _app(ws)["prefs"].get("heard") == (["x"] if receives else None)


def test_the_install_prompt_says_a_service_works_in_the_background():
    svc = {"id": APP, "type": "service", "runtime": "lua",
           "permissions": ["files", "prefs"]}
    lines = app_perms.prompt(svc)
    assert lines[-1] == app_perms.BACKGROUND
    assert app_perms.prompt(dict(svc, type="app"))[-1] != app_perms.BACKGROUND
    assert app_perms.prompt(dict(svc, permissions=[])) is None
    man = {"title": "Pair", "id": APP, "moybyte": {"type": "service"}}
    assert app_perms.manifest_cart(man, "pair.moy")["type"] == "service"


def test_a_start_whose_grant_cannot_be_made_is_refused(ws, tier, monkeypatch):
    def full(*a, **k):
        raise OSError(28, "the grant table is full")
    monkeypatch.setattr(system_api, "service_api", full)
    assert _start(ws, _service(tier)) is None
    p = [q for q in S.SCHED.services() if q.name == APP][-1]
    assert p.state == S.EXITED and p.code == S.EXIT_CRASHED and p.ctx is None
    assert any("its grant" in t for t in _log(p.pid))


def test_a_manifests_receives_reaches_the_cart(tmp_path):
    """`receives` is a Moybyte manifest field: its list, None without one."""
    from runtime import moy_carts
    root = tmp_path / "pair.moy"
    root.mkdir()
    man = {"title": "Pair", "id": APP, "canvas": "320x240",
           "moybyte": {"type": "service", "receives": ["ping"]}}
    (root / "manifest.json").write_text(json.dumps(man))
    (root / "main.py").write_text("")
    (root / "config.json").write_text("{}")
    assert moy_carts.load(str(root))["receives"] == ["ping"]
    man["moybyte"].pop("receives")
    (root / "manifest.json").write_text(json.dumps(man))
    assert moy_carts.load(str(root))["receives"] is None
