"""One timer scheduler and one Lua `moybyte` table (docs/refactor_2026-11.md,
decision 1): every tier's timer is a row of the kernel's table
(native/moy_kernel/moy_evq.h, TIMERS), a Lua app's run and a Lua service hold
one C table (native/moycore/moycore_moybyte.h) over one sandbox
(moycore_lua_sandbox), and neither the Lua service nor the Python registry
keeps timers of its own."""

import inspect
import json
import re
from pathlib import Path

import pytest

from runtime import moy_evq, moybyte, scheduler as S
from runtime import moy_log as L
from runtime.run import lua_ext
from ws_helpers import build_ws, make_drv, select_on_home

ROOT = Path(__file__).resolve().parents[1]
MOYCORE = ROOT / "native" / "moycore"

PROBE = '''
local names = {}
for k in pairs(moybyte) do names[#names + 1] = k end
table.sort(names)
local banned = {}
for _, k in ipairs({"load", "loadstring", "dofile", "loadfile", "require",
                    "collectgarbage", "io", "os", "debug", "package"}) do
  if _G[k] ~= nil then banned[#banned + 1] = k end
end
moybyte.log(moybyte.INFO, "names " .. table.concat(names, " "))
moybyte.log(moybyte.INFO, "globals " .. table.concat(banned, " "))
TIMER = moybyte.every(60000, function() end)
'''


def _lines(since, pid):
    return [r[4] for r in L.read(since, 1 << 30) if r[3] == pid]


def _said(since, pid, what):
    for t in _lines(since, pid):
        if t.startswith(what + " ") or t == what:
            return t[len(what):].split()
    return None


@pytest.fixture
def lua():
    if S._svc() is None:
        pytest.skip("no Lua runtime on this host")


def _cart(tmp_path, runtime, main, src, perms=()):
    root = tmp_path / "carts" / "probe.moy"
    root.mkdir(parents=True)
    (root / "manifest.json").write_text(json.dumps({
        "title": "Probe", "id": "local.probe", "canvas": "320x240", "runtime": runtime,
        "main": main, "moybyte": {"type": "app", "permissions": list(perms)}}))
    (root / main).write_text(src)
    (root / "config.json").write_text("{}")
    return root


def test_a_python_process_s_timers_are_rows_of_the_kernel_s_table():
    pid = L.current()
    before = moy_evq.timers(pid) if moy_evq.has(pid) else 0
    w = moybyte.WAKES
    ids = [w.every(60000, lambda: None), w.after(60000, lambda: None),
           w.alarm(2 ** 31 - 1, lambda: None, False)]
    try:
        assert moy_evq.timers(pid) == before + 3
        for wid in ids:
            assert w.by_id[wid][moybyte._HOW] == moybyte._TIMER
    finally:
        for wid in ids:
            w.cancel(wid)
    assert moy_evq.timers(pid) == before


def test_the_registry_keeps_no_due_list():
    """Wakes runs no clock of its own: no `due`, no deadline arithmetic."""
    assert not hasattr(moybyte.Wakes, "due")
    src = inspect.getsource(moybyte.Wakes)
    for word in ("_ticks_ms", "_ticks_add", "_ticks_diff", "deadline"):
        assert word not in src, word


def test_the_lua_service_keeps_no_timer_table():
    src = (MOYCORE / "moycore_svc.c").read_text()
    for word in ("svc_timer_t", "MOYCORE_SVC_TIMERS", "->due", ".due", "every;"):
        assert word not in src, word
    assert "moycore_mb_open" in src and "moycore_lua_sandbox" in src
    assert "MOYCORE_SVC_TIMERS" not in (MOYCORE / "moycore_svc.h").read_text()


def test_one_c_table_is_every_lua_program_s_moybyte():
    """The table is built in one place, and each holder opens it."""
    for f in sorted(MOYCORE.glob("*.c")):
        if f.name != "moycore_moybyte.c":
            assert 'lua_setglobal(L, "moybyte")' not in f.read_text(), f.name
    assert "moycore_mb_open" in (MOYCORE / "moycore_lua.c").read_text()
    assert "__moybyte_fire" not in lua_ext.PRELUDE_HANDLES
    assert not hasattr(lua_ext, "lua_lit")


def test_libmoy_s_sandbox_bans_what_the_tier_s_does():
    def banned(text, name):
        m = re.search(r"%s\[\]\s*=\s*\{(.*?)\};" % name, text, re.S)
        return set(re.findall(r'"(\w+)"', m.group(1)))
    ours = banned((MOYCORE / "moycore_lua.c").read_text(), "SANDBOX_BANNED")
    libmoy = banned((MOYCORE / "libmoy" / "moy_lua.c").read_text(), "BANNED")
    assert ours == libmoy and {"load", "dofile", "loadfile", "io", "require", "debug"} <= ours


def test_a_lua_app_and_a_lua_service_hold_one_table_one_sandbox_and_kernel_timers(
        tmp_path, lua):
    _cart(tmp_path, "lua", "main.lua", PROBE + "\nfunction _draw() cls(0) end\n")
    ws = build_ws(tmp_path)
    S.attach(ws)
    since = L.next()
    assert select_on_home(ws, "Probe") is not None
    ws.open()
    pl = ws.run.player
    assert pl.cart_error is None, pl.cart_error
    app = pl.pid
    make_drv(ws).frame(1 / 30)
    app_names = _said(since, app, "names")
    app_banned = _said(since, app, "globals")
    assert moy_evq.timers(app) == 1             # its `every`, the kernel's
    svc = S.SCHED.start({"id": "local.probe.svc", "title": "Probe", "runtime": "lua",
                         "src": PROBE, "permissions": []})
    try:
        for _ in range(3):
            S.SCHED.tick(0)
            S.SCHED.poll()
        svc_names = _said(since, svc, "names")
        assert moy_evq.timers(svc) == 1
        # `os` is each holder's own over the sandbox: a service's os.time and
        # os.clock, a run's file rows (native/moy_app/moy_app_lua.h)
        svc_banned = _said(since, svc, "globals")
        assert [g for g in svc_banned if g != "os"] == [g for g in app_banned if g != "os"] == []
        own = set(lua_ext.LUA_OWN) | set(lua_ext.LUA_RUN_CALLS) | {"ERR", "WARNING", "INFO"}
        assert own <= set(app_names) and own <= set(svc_names)
        assert set(svc_names) - own <= {"exit"} | set(app_names)
    finally:
        S.SCHED.stop(svc)
        ws.spine.exit()
