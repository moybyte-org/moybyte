"""An app and its service in ONE cart (docs/app_api_v1.md section 7;
runtime/system/scheduler.py, AN APP'S OWN SERVICE): the manifest's
`"moybyte": {"entries": {"service": file}}` names the service's program
beside `main`, and the service runs under the app's one id, consent and
storage, as a `type: service` cart's does.

The app starts, stops and asks after its own service with
`moybyte.service_start()`, `service_stop()` and `service_running()`, every
tier's same three calls (native/os_calls.json): a start or stop is asked and
run by the shell at the next tick, never inside the caller's call, and a
process of any other app, the shell, or a `type: service` cart is answered
ABSENT, since the call names no app at all. Restart policies and a VM stop
treat it as any service. The Python and Lua entries run here; the WASI one
in the last tests, over tests/fixtures/wasm/wasi_svc.moy's module."""

import json
import os
import shutil
import time

import pytest

from runtime import app_perms, moy_evq, moybyte, os_log, scheduler as S
from runtime import moy_log as L
from runtime.shell import shell_servers

from ws_helpers import build_ws, make_drv, select_on_home

APP = "local.both"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# The app asks for its service at its start, then pings it once it runs.
APP_SRC = '''
import moybyte
got = []
asked = []


def _init():
    moybyte.on("pong", lambda who, *a: got.append((who,) + a))
    asked.append(moybyte.service_start())


def _update(dt):
    if not got and moybyte.service_running() > 0:
        moybyte.send("local.both", "ping", 7)


def _draw():
    pass
'''

PY_SVC = '''
def heard(who, *args):
    prefs.set("heard", list(args))
    prefs.set("self", moybyte.service_running())
    moybyte.send(who, "pong", args[0])


def moy_start():
    moybyte.on("ping", heard)
'''

LUA_SVC = '''
local function heard(who, ...)
    local args = {...}
    prefs.set("heard", args)
    prefs.set("self", moybyte.service_running())
    moybyte.send(who, "pong", args[1])
end

function moy_start()
    moybyte.on("ping", heard)
end
'''

# A service that crashes at its first tick: what the restart policy follows.
PY_CRASH = '''
def boom():
    raise ValueError("bad")


def moy_start():
    moybyte.after(0, boom)
'''


@pytest.fixture(params=["python", "lua"])
def tier(request):
    if request.param == "lua" and S._svc() is None:
        pytest.skip("no Lua runtime on this host")
    return request.param


def _cart(tmp_path, entry, text, perms=("files", "prefs"), entries=None):
    root = tmp_path / "carts" / "both.moy"
    root.mkdir(parents=True)
    (root / "manifest.json").write_text(json.dumps({
        "title": "Both", "id": APP, "canvas": "320x240",
        "moybyte": {"type": "app", "permissions": list(perms),
                    "entries": entries if entries is not None else {"service": entry}}}))
    (root / "main.py").write_text(APP_SRC)
    (root / "config.json").write_text("{}")
    if text is not None:
        (root / entry).write_bytes(text if isinstance(text, bytes) else text.encode())
    return root


def _ws(tmp_path, tier=None, src=None, perms=("files", "prefs"), entry=None):
    if entry is None:
        entry = "service.lua" if tier == "lua" else "service.py"
    if src is None:
        src = LUA_SVC if tier == "lua" else PY_SVC
    _cart(tmp_path, entry, src, perms)
    ws = build_ws(tmp_path)
    S.attach(ws)
    return ws


def _shelf(ws):
    return next(c for c in ws.carts.all if c.get("id") == APP)


def _as(pid, fn):
    was = L.current()
    L.current(pid)
    try:
        return fn()
    finally:
        L.current(was)


def _claim(ws, carts):
    """The console names process pid's cart `carts[pid]` (procs.who), as it
    names a run's, a service's or a command's."""
    who = ws.procs.who
    ws.procs.who = lambda q: (None, carts[q], False) if q in carts else who(q)
    S.attach(ws)


def _ticks(n=3):
    for _ in range(n):
        S.SCHED.tick(10000)
        S.SCHED.poll()


@pytest.fixture(autouse=True)
def _ended():
    yield
    for p in list(S.SCHED.services()):
        if p.name == APP and p.state != S.EXITED:
            S.SCHED.stop(p.pid)
    S.SCHED.asked.clear()


def test_the_shelf_reads_the_entry_and_its_service_is_the_apps(tmp_path, tier):
    ws = _ws(tmp_path, tier)
    cart = _shelf(ws)
    want = "service.lua" if tier == "lua" else "service.py"
    assert cart["type"] == "app" and cart["entries"] == {"service": want}
    assert S.entry_of(cart) == (want, tier)
    run = S.as_service(ws.carts.rehydrate(cart))
    assert run["type"] == S.SERVICE and run["runtime"] == tier and run["main"] == want
    assert run["id"] == APP and run["title"] == "Both" and run["path"] == cart["path"]
    assert run["permissions"] == ["files", "prefs"]
    assert "moy_start" in run["src"]


@pytest.mark.parametrize("entries", [
    None, {}, {"service": 3}, {"service": "/abs.py"}, {"service": "../up.py"},
    {"service": "service.txt"}, {"service": ""}, {"other": "service.py"}, ["service.py"],
])
def test_an_entry_that_does_not_read_is_absent(entries):
    assert S.entry_of({"entries": entries}) is None
    assert S.as_service({"entries": entries, "path": "/nowhere"}) is None


def test_the_app_starts_asks_after_and_stops_its_own_service(tmp_path, tier):
    """`service_start` from the app's `_init` asks; the shell starts it at
    the next tick; the app hears it by its one id, and it answers the app;
    the service's own `service_running` is its pid; the stop is the app's."""
    ws = _ws(tmp_path, tier)
    assert select_on_home(ws, "Both") is not None
    ws.open()
    pl = ws.run.player
    assert pl.cart_error is None, pl.cart_error
    assert pl.ns["asked"] == [0]                    # asked, not yet run
    assert S.SCHED.find(APP) is None
    drv = make_drv(ws)
    for _ in range(6):
        drv.frame(1 / 30)
        S.SCHED.poll()
    p = S.SCHED.find(APP)
    assert p is not None and p.kind == S.SERVICE and p.cart["runtime"] == tier
    assert _as(pl.pid, moybyte.service_running) == p.pid
    assert _as(pl.pid, moybyte.service_start) == p.pid     # running: nothing more to ask
    assert not S.SCHED.asked
    assert pl.ns["got"] == [(APP, 7)]
    assert pl.ns["prefs"].get("heard") == [7]       # its storage is the app's
    assert pl.ns["prefs"].get("self") == p.pid
    rows = [r for r in ws.procs.rows() if r[2] == "service" and r[6] == p.pid]
    assert [r[1] for r in rows] == ["Both"]         # the task manager: the cart's one name
    assert _as(pl.pid, moybyte.service_stop) == 1
    assert p.state != S.EXITED                      # asked, run at the next tick
    _ticks()
    assert p.state == S.EXITED and p.code == 0
    assert _as(pl.pid, moybyte.service_running) == 0
    assert _as(pl.pid, moybyte.service_stop) == 0   # nothing left to stop


LUA_APP = '''
heard = 0
function _init()
    moybyte.on("pong", function(who, v) heard = v end)
    moybyte.service_start()
end

function _update()
    if heard == 0 and moybyte.service_running() > 0 then
        moybyte.send("local.both", "ping", 9)
    end
end

function _draw()
end
'''


def test_a_lua_app_starts_its_own_service(tmp_path, tier):
    if S._svc() is None:
        pytest.skip("no Lua runtime on this host")
    root = _cart(tmp_path, "service.lua" if tier == "lua" else "service.py",
                 LUA_SVC if tier == "lua" else PY_SVC)
    man = json.loads((root / "manifest.json").read_text())
    man.update(runtime="lua", main="main.lua")
    (root / "manifest.json").write_text(json.dumps(man))
    (root / "main.py").unlink()
    (root / "main.lua").write_text(LUA_APP)
    ws = build_ws(tmp_path)
    S.attach(ws)
    assert select_on_home(ws, "Both") is not None
    ws.open()
    assert ws.run.player.cart_error is None, ws.run.player.cart_error
    drv = make_drv(ws)
    for _ in range(6):
        drv.frame(1 / 30)
        S.SCHED.poll()
    p = S.SCHED.find(APP)
    assert p is not None and p.cart["runtime"] == tier
    assert ws.system.get(APP + "_heard") == [9]


def test_the_last_ask_wins(tmp_path):
    ws = _ws(tmp_path, "python")
    cart = _shelf(ws)
    pid = 4100
    _claim(ws, {pid: cart})
    assert _as(pid, moybyte.service_start) == 0
    assert _as(pid, moybyte.service_stop) == 1      # a start asked: it is stopped
    _ticks()
    assert S.SCHED.find(APP) is None
    assert _as(pid, moybyte.service_start) == 0
    assert _as(pid, moybyte.service_start) == 0
    assert len(S.SCHED.asked) == 1                  # one ask a service
    _ticks()
    assert S.SCHED.find(APP) is not None


def test_no_other_app_reaches_it(tmp_path):
    """The calls name no app: another app's process, the shell and a
    `type: service` cart's are answered ABSENT, and nothing starts."""
    ws = _ws(tmp_path, "python")
    other = {"id": "local.other", "title": "Other", "type": "app", "path": "/x/other.moy"}
    svc = {"id": "local.svc", "title": "Svc", "type": "service",
           "entries": {"service": "service.py"}, "path": "/x/svc.moy"}
    _claim(ws, {4201: other, 4202: svc})
    for pid in (4201, 4202, S.SHELL):
        for fn in (moybyte.service_start, moybyte.service_stop, moybyte.service_running):
            assert _as(pid, fn) == moybyte.ABSENT
    _ticks()
    assert S.SCHED.find(APP) is None and not S.SCHED.asked


def test_the_door_answers_a_compiled_programs_calls(tmp_path):
    """A compiled app's and an ELF program's rows (ConsoleCalls): its
    grant's process, STALE for a grant none holds."""
    ws = _ws(tmp_path, "python")
    cart = _shelf(ws)
    calls = shell_servers.ConsoleCalls(ws)
    pid = 4300
    _claim(ws, {pid: cart})
    ws.procs.pid_of = lambda g: pid if g == 77 else None
    assert calls.service_start(77) == 0
    _ticks()
    p = S.SCHED.find(APP)
    assert calls.service_running(77) == p.pid
    assert calls.service_running(78) == shell_servers._DOOR_STALE
    assert calls.service_stop(77) == 1
    _ticks()
    assert calls.service_running(77) == 0


def test_the_terminal_lists_it_by_the_carts_name(tmp_path):
    from runtime.shell import sh_roles
    ws = _ws(tmp_path, "python")
    rows = sh_roles._svc_rows(ws)
    assert [r[0] for r in rows if r[1].get("path") == _shelf(ws)["path"]] == [APP]


def test_autostart_and_a_vm_stop_start_it_from_the_app_cart(tmp_path):
    ws = _ws(tmp_path, "python", perms=("files", "prefs", "autostart"))
    cart = _shelf(ws)
    assert S.wants_autostart(ws, cart)
    assert S.wants_autostart(ws, dict(cart, permissions=["prefs"])) is False
    started = S.boot(ws)
    p = S.SCHED.find(APP)
    assert p is not None and p.pid in started
    paths = S.SCHED.pause()                         # a VM stop: it is TERMed
    assert cart["path"] in paths and p.state == S.EXITED
    S.SCHED.resumed = [cart["path"]]
    S.SCHED.procs.pop(p.pid, None)
    ws.consent.is_on = lambda app, perm: False      # autostart turned off: the resume starts it
    assert S.boot(ws) and S.SCHED.find(APP) is not None


def test_its_restart_policy_is_the_cart_names(tmp_path, monkeypatch):
    monkeypatch.setattr(S, "MOY_RESTART_BACKOFF_MS", 4)
    ws = _ws(tmp_path, "python", src=PY_CRASH)
    S.SCHED.set_policy(APP, S.ON_CRASH)
    try:
        cart = _shelf(ws)
        pid = S.start_cart(ws, cart)
        assert pid is not None
        _ticks(1)
        assert S.SCHED.procs[pid].state == S.EXITED
        assert S.SCHED.procs[pid].code == S.EXIT_CRASHED
        assert APP in S.SCHED.pending               # on-crash: it comes back
        time.sleep(0.01)
        _ticks(1)
        again = S.SCHED.find(APP)
        assert again is not None and again.pid != pid and again.origin is cart
        assert again.cart["main"] == "service.py"
    finally:
        S.SCHED.set_policy(APP, S.NEVER)
        S.SCHED.forgive(APP)


@pytest.mark.parametrize("runtime,entry,want", [
    ("lua", "svc.elf", ["prefs", "autostart", "full_access"]),   # the ELF service's
    ("lua", None, ["prefs", "autostart"]),
    ("wasm", "svc.wasm", ["prefs", "autostart", "full_access"]),  # a WASI service autostarts
])
def test_the_prompt_asks_for_what_the_app_and_its_service_are_served(runtime, entry, want):
    cart = {"id": APP, "type": "app", "runtime": runtime,
            "permissions": ["prefs", "autostart", "full_access"],
            "entries": {"service": entry} if entry else None}
    assert app_perms.asked(cart["permissions"], app_perms.tiers_of(cart)) == want
    assert [p for p in want if app_perms.line(p) in app_perms.prompt(cart)] == want
    man = {"id": APP, "runtime": runtime, "moybyte": {
        "type": "app", "permissions": cart["permissions"], "entries": cart["entries"]}}
    assert app_perms.tiers_of(app_perms.manifest_cart(man, "both.moy")) \
        == app_perms.tiers_of(cart)


PY_NOTE_SVC = '''
heard = []


def moy_action(nid, key):
    heard.append((nid, key))


def moy_start():
    moybyte.every(60000, lambda: None)
'''


def test_a_notification_from_its_service_opens_the_app(tmp_path):
    """Tapped, an app's own service's notification opens the app (its main,
    never the entry) and still reaches the service's `moy_action`
    (tests/test_notifications.py has a `type: service` cart's, which
    reaches the service alone)."""
    from runtime import notifications as N
    ws = _ws(tmp_path, "python", src=PY_NOTE_SVC, perms=("notifications",))
    make_drv(ws).frame(1 / 30)
    pid = S.start_cart(ws, _shelf(ws))
    assert pid is not None and S.SCHED.procs[pid].cart["entry"] == "service"
    nid = _as(pid, lambda: moybyte.notify("Both", "a ping"))
    assert nid
    opened = []
    ws.open = lambda c=None: opened.append(c)
    assert N.NOTES.act(nid)
    assert [c["path"] for c in opened] == [_shelf(ws)["path"]]
    assert opened[0]["main"] == "main.py" and opened[0]["type"] == "app"
    assert S.SCHED.procs[pid].ns["heard"] == [(nid, "default")]


def test_the_install_prompt_says_the_app_works_in_the_background():
    cart = {"id": APP, "type": "app", "runtime": "lua", "permissions": ["files", "prefs"],
            "entries": {"service": "service.lua"}}
    assert app_perms.prompt(cart)[-1] == app_perms.BACKGROUND
    assert app_perms.prompt(dict(cart, entries=None))[-1] != app_perms.BACKGROUND


def test_a_copy_keeps_its_service_and_what_its_program_asks_for(tmp_path):
    """The Make tab's COPY regenerates the manifest: it keeps the entry (and
    the file beside it), the permissions, `receives` and the rest of
    COPY_KEEPS, and drops what makes a seed a system cart."""
    from runtime import moy_carts
    root = _cart(tmp_path, "service.lua", LUA_SVC, perms=("prefs", "autostart"))
    man = json.loads((root / "manifest.json").read_text())
    man["moybyte"].update(receives=["ping"], handles=["text/plain"], system=True, order=3)
    (root / "manifest.json").write_text(json.dumps(man))
    cart = moy_carts.load(str(root))
    dup = moy_carts.duplicate(cart, str(tmp_path / "carts"))
    assert dup["entries"] == {"service": "service.lua"}
    assert dup["permissions"] == ["prefs", "autostart"]
    assert dup["receives"] == ["ping"] and dup["handles"] == ["text/plain"]
    ven = json.loads(open(dup["path"] + "/manifest.json").read())["moybyte"]
    assert "system" not in ven and "order" not in ven
    run = S.as_service(dup)
    assert run["id"] == dup["id"] != APP and run["src"] == LUA_SVC


# -- a WASI service entry --------------------------------------------------------

@pytest.fixture(scope="module")
def wasi_module(tmp_path_factory):
    from runtime import native_build
    if native_build.cc() is None:
        pytest.skip("no C compiler")
    from runtime import wasm_binding, wasm_host
    from tools import jet_cart, wasm_cart
    required = bool(os.environ.get("CI") or os.environ.get("MOYBYTE_REQUIRE_HOST_WASM"))
    if not wasm_host.available():
        why = "no host wasm binding: %s" % (wasm_binding.why_unavailable() or "?")
        if required:
            pytest.fail(why)
        pytest.skip(why)
    try:
        jet_cart.wasi_sdk(fetch=True)
    except Exception as exc:  # noqa: BLE001
        if required:
            pytest.fail("no wasi-sdk: %s" % exc)
        pytest.skip("no wasi-sdk: %s" % exc)
    dst = str(tmp_path_factory.mktemp("entrysvc") / "wasi_svc.moy")
    wasm_cart.build(os.path.join(ROOT, "tests", "fixtures", "wasm", "wasi_svc.moy"), dst)
    return os.path.join(dst, "main.wasm")


def _polled(cond, secs=5.0):
    t0 = time.monotonic()
    while not cond():
        if time.monotonic() - t0 >= secs:
            return False
        S.SCHED.poll()
        time.sleep(0.01)
    return True


def test_a_python_app_runs_its_wasi_service_entry(tmp_path, wasi_module):
    """The entry `service.wasm` is a WASI command: started from the app's
    ask on the wasm runtime, its queue its app's id, its own
    `moy_service_running` its pid, its stop the app's."""
    _cart(tmp_path, "service.wasm", open(wasi_module, "rb").read())
    ws = build_ws(tmp_path)
    S.attach(ws)
    assert "wasm" in ws.runtimes
    cart = _shelf(ws)
    assert S.entry_of(cart) == ("service.wasm", "wasm")
    app = L.spawn("both-app")
    _claim(ws, {app: cart})
    since = L.next()
    try:
        assert _as(app, moybyte.service_start) == 0
        _ticks(1)
        p = S.SCHED.find(APP)
        assert p is not None and p.rt is ws.runtimes["wasm"], [
            t for _s, _t, _l, _q, t in os_log.records(since, 64)]
        assert moy_evq.app(p.pid) == APP
        assert moy_evq.open(app, APP, "", True) == moy_evq.OK
        assert moy_evq.listen(app, "pong", True) == moy_evq.OK
        assert _polled(lambda: moy_evq.send(app, APP, "ping", "[3]")[0] == 1)
        assert _polled(lambda: moy_evq.take(app) == (p.pid, APP, "pong", "[3]"))
        assert _polled(lambda: moy_evq.send(app, APP, "note", '["n"]')[0] == 1)
        want = "service_running %d errno 0" % p.pid
        assert _polled(lambda: want in [t for _s, _t, _l, q, t in os_log.records(since, 128)
                                        if q == p.pid])
        assert _as(app, moybyte.service_stop) == 1
        _ticks(1)
        assert p.state == S.EXITED and p.code == 0
    finally:
        moy_evq.close(app)


def test_an_elf_entry_is_a_module_a_chip(tmp_path):
    root = tmp_path / "both.moy"
    root.mkdir()
    for n in ("svc.esp32s3.elf", "svc.esp32p4.elf", "svc.x.y.elf", "main.py"):
        (root / n).write_text("")
    run = S.as_service({"id": APP, "path": str(root), "entries": {"service": "svc.elf"}})
    assert run["native"] == ["esp32p4", "esp32s3"] and app_perms.tier_of(run) == "elf"
    assert S.as_service({"id": APP, "path": str(root),
                         "entries": {"service": "none.elf"}}) is None

    class Engine:
        CHIP = "esp32p4"
    from runtime.run import elf_run
    rt = elf_run.ElfRuntime.__new__(elf_run.ElfRuntime)
    rt.engine = Engine()
    assert rt.module(run) == "%s/svc.esp32p4.elf" % root
    native = {"path": str(root), "native": ["esp32p4"], "main": "main.esp32p4.elf"}
    (root / "main.esp32p4.elf").write_text("")
    assert rt.module(native) == "%s/main.esp32p4.elf" % root


# -- the tools build a service entry beside main -----------------------------------

WAT_SVC = '(module (func (export "_start")))\n'


def test_wasm_cart_builds_and_signs_a_service_entry_beside_a_python_main(tmp_path,
                                                                        monkeypatch):
    from tools import wasm_cart, wasm_module
    src = _cart(tmp_path, "svc.wasm", None)
    (src / "src").mkdir()
    (src / "src" / "svc.wat").write_text(WAT_SVC)
    made = []
    monkeypatch.setattr(wasm_module, "build",
                        lambda blob, chip, out, signed=True: made.append(
                            (blob[:4], chip, os.path.basename(out), signed)))
    dst = str(tmp_path / "out" / "both.moy")
    out = wasm_cart.build(str(src), dst, ["esp32s3", "esp32p4"])
    assert sorted(out) == ["service", "service.esp32p4", "service.esp32s3"]
    with open(out["service"], "rb") as f:
        assert f.read(4) == b"\0asm"
    assert open(os.path.join(dst, "main.py")).read() == APP_SRC     # main as it was
    assert made == [(b"\0asm", c, wasm_cart.aot_name("svc.wasm", c), True)
                    for c in ("esp32s3", "esp32p4")]


def test_elf_cart_signs_a_service_entry_and_writes_no_native_for_a_script_app(
        tmp_path, monkeypatch):
    from tools import elf_cart
    src = _cart(tmp_path, "svc.elf", None)
    (src / "src" / "svc").mkdir(parents=True)
    (src / "src" / "svc" / "main.c").write_text("int main(void) { return 0; }\n")
    made = []
    monkeypatch.setattr(elf_cart, "build_module", lambda sources, chip, out, signed=True,
                        sign_with=None: made.append(
                            ([os.path.basename(s) for s in sources], chip,
                             os.path.basename(out), signed)) or out)
    dst = str(tmp_path / "out" / "both.moy")
    out = elf_cart.build(str(src), dst, ["esp32s3", "esp32p4"])
    assert sorted(out) == ["service.esp32p4", "service.esp32s3"]
    assert made == [(["main.c"], c, "svc.%s.elf" % c, True) for c in ("esp32s3", "esp32p4")]
    man = json.load(open(os.path.join(dst, "manifest.json")))
    assert "native" not in man["moybyte"]           # its main is the Python one


def test_a_python_app_runs_its_elf_service_entry(tmp_path):
    """The ELF service fixture built as an app's entry for the host's chip:
    started from the app's ask, it answers the app's ping under the app's id
    and its own `moy_service_running` through the ELF door is its pid."""
    from runtime import native_build
    from tools import elf_cart
    if native_build.cc() is None:
        pytest.skip("no C compiler")
    src = _cart(tmp_path / "src", "svc.elf", None)
    (src / "src" / "svc").mkdir(parents=True)
    shutil.copy(os.path.join(ROOT, "tests", "fixtures", "elf", "elf_svc.moy", "src", "main.c"),
                str(src / "src" / "svc" / "main.c"))
    man = json.loads((src / "manifest.json").read_text())
    man["moybyte"]["permissions"] = ["notifications", "full_access"]     # a native module asks
    (src / "manifest.json").write_text(json.dumps(man))
    carts = tmp_path / "carts"
    carts.mkdir()
    elf_cart.build(str(src), str(carts / "both.moy"), ["host"], signed=False)
    ws = build_ws(tmp_path)
    assert "elf" in ws.runtimes
    cart = _shelf(ws)
    app = L.spawn("both-app")
    _claim(ws, {app: cart})
    try:
        assert _as(app, moybyte.service_start) == 0
        _ticks(1)
        p = S.SCHED.find(APP)
        assert p is not None and p.rt is ws.runtimes["elf"], [
            t for _s, _t, _l, _q, t in os_log.records(0, 400)][-8:]
        assert moy_evq.open(app, APP, "", True) == moy_evq.OK
        for name in ("pong", "noted", "owned"):
            assert moy_evq.listen(app, name, True) == moy_evq.OK
        assert _polled(lambda: moy_evq.send(app, APP, "ping", "[5]")[0] == 1)
        assert _polled(lambda: moy_evq.queued(app) == 1)
        assert moy_evq.take(app) == (p.pid, APP, "pong", "[5]")
        assert _polled(lambda: moy_evq.send(app, APP, "note", "[]")[0] == 1)
        assert _polled(lambda: moy_evq.queued(app) == 2)
        assert moy_evq.take(app)[2] == "noted"
        assert moy_evq.take(app) == (p.pid, APP, "owned", '["running"]')
        assert _as(app, moybyte.service_stop) == 1
        _ticks(1)
        assert p.state == S.EXITED
    finally:
        moy_evq.close(app)


def test_a_copy_keeps_its_scripts_load_order(tmp_path):
    """`sources` is the order a multi-file cart loads its scripts in (a PICO-8
    port's); a copy keeps it."""
    from runtime import moy_carts
    root = tmp_path / "carts"
    root.mkdir()
    cart = moy_carts.create("Port", str(root))
    moy_carts.add_source(cart, "b.py", "B = 2\n")
    moy_carts.add_source(cart, "a.py", "A = 1\n")
    cart = moy_carts.load(cart["path"])
    want = moy_carts.cart_sources(cart)
    assert want[-2:] == ["b.py", "a.py"]
    dup = moy_carts.duplicate(cart, str(root))
    assert moy_carts.cart_sources(dup) == want
    man = json.loads(open(dup["path"] + "/manifest.json").read())
    assert man["sources"] == want
