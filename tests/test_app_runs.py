"""Each run holds its own grant (docs/os_2026-11.md section 7, phase 5 step 2;
docs/os_2026-11/tiers.md section 3).

An app's run on a tier binding -- a compiled app's instance, a Lua app's
state -- holds its grant as a value (native/moy_app/moy_app_run.h), and every
call it makes is served from that value and no other:

  * two compiled apps loaded at once, their imports called on each one's
    own exec env (the engine's handle on the call, as WAMR hands it to the
    natives), each read and write only their own prefs;
  * a Lua app played on the console beside a compiled app's instance, their
    calls interleaved, likewise;
  * a permission turned off reaches the run's very next call: the grant row
    is the authority, so a run's copy made before cannot keep the role, and a
    Lua state opened after has no name for it.

The natives are called through the import adapter's own table
(moy_app_wasm_natives) on the host's WAMR (runtime/host/wasm_binding).
"""

import ctypes
import json
import os

import pytest

from runtime import app_perms, host_app
from tools import wasm_cart
from ws_helpers import build_ws, select_on_home

import moy_app

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ROLES = os.path.join(ROOT, "tests", "fixtures", "wasm", "app_roles.moy")
DT = 1.0 / 30.0
DENIED, ABSENT = 4, 8


def _binding_or_skip():
    from runtime import wasm_binding, wasm_host
    if wasm_host.available():
        return
    why = wasm_binding.why_unavailable() or "unknown"
    if os.environ.get("CI") or os.environ.get("MOYBYTE_REQUIRE_HOST_WASM"):
        pytest.fail("the host wasm binding did not build: %s" % why)
    pytest.skip("no host wasm binding: %s" % why)


class _Native(ctypes.Structure):
    _fields_ = [("symbol", ctypes.c_char_p), ("fn", ctypes.c_void_p),
                ("signature", ctypes.c_char_p), ("attachment", ctypes.c_void_p)]


_SPAN = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_uint32]


def _natives():
    """prefs_get and prefs_set as the engine calls them: its handle on the
    call first, then each '*~' as a pointer and its length."""
    from runtime.host import host_native
    lib = ctypes.CDLL(host_native.build())
    lib.moy_app_wasm_natives.restype = ctypes.c_void_p
    n = ctypes.c_uint32()
    p = lib.moy_app_wasm_natives(ctypes.byref(n))
    rows = {r.symbol.decode(): r.fn for r in (_Native * n.value).from_address(p)}
    get = ctypes.CFUNCTYPE(ctypes.c_int32, *(_SPAN + [ctypes.c_void_p, ctypes.c_uint32]))(
        rows["prefs_get"])
    put = ctypes.CFUNCTYPE(ctypes.c_int32, *(_SPAN + [ctypes.c_char_p, ctypes.c_uint32]))(
        rows["prefs_set"])
    return get, put


class _Calls:
    """One run's prefs, read and written through its imports."""

    def __init__(self, run):
        self.env = run.env()
        self.get_fn, self.put_fn = _natives()

    def get(self, key):
        k = key.encode()
        buf = ctypes.create_string_buffer(64)
        r = self.get_fn(self.env, k, len(k), ctypes.cast(buf, ctypes.c_void_p), 64)
        return json.loads(buf.raw[:r]) if r >= 0 else -r

    def put(self, key, value):
        k, v = key.encode(), json.dumps(value).encode()
        return self.put_fn(self.env, k, len(k), v, len(v))


def _module(tmp_path):
    """App Roles Wasm built where a run can load it."""
    dst = str(tmp_path / "module" / "app_roles.moy")
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    wasm_cart.build(ROLES, dst)
    return dst


def _compiled(app, cart, app_id, perms=("prefs",)):
    """A compiled app's run of the fixture on the host's WAMR, holding a run
    grant of `app_id` for `perms` as the Player makes one (the ungated theme
    and surface beside the roles they name): its canvas its own. Loaded (the grant check passed) and never ticked."""
    from runtime.wasm_binding import HostWasmRun
    roles, kind = moy_app.policy(perms)
    needs = roles + tuple(r for r in ("surface", "theme") if r not in roles)
    g = app.grant(app_id, needs, kind=kind, ns=app_id, run=True)
    buf = bytearray(320 * 240 * 2)
    run = HostWasmRun(buf, 320, 240, cart, "main.wasm", 1, app=app.run(g, roles))
    run._buf_ref = buf
    return run, g


def test_two_compiled_apps_each_reach_only_their_own_grant(tmp_path):
    _binding_or_skip()
    cart = _module(tmp_path)
    ws = host_app.build_workstation(str(tmp_path / "carts"))
    app = ws.app_abi
    a, ga = _compiled(app, cart, "test.alpha")
    b, gb = _compiled(app, cart, "test.beta")
    assert ga != gb
    try:
        ca, cb = _Calls(a), _Calls(b)
        assert ca.env and cb.env and ca.env != cb.env
        assert ca.put("k", "alpha") == 0
        assert cb.get("k") == ABSENT, "the second app read the first one's prefs"
        assert cb.put("k", "beta") == 0
        for _ in range(3):
            assert ca.get("k") == "alpha"
            assert cb.get("k") == "beta"
        # ...and the console's own reading of each grant agrees.
        assert moy_app.Prefs(app, ga).get("k") == "alpha"
        assert moy_app.Prefs(app, gb).get("k") == "beta"
    finally:
        b.close()
        a.close()


def _proc_lib():
    from runtime.host import host_native
    lib = ctypes.CDLL(host_native.build())
    U32 = ctypes.c_uint32
    lib.moy_proc_add.argtypes = [U32, ctypes.c_uint8, ctypes.c_uint8, ctypes.c_char_p, U32, U32,
                                 ctypes.POINTER(U32)]
    lib.moy_proc_next.argtypes = [U32]
    lib.moy_proc_end.argtypes = [U32, ctypes.c_int32]
    lib.moy_proc_bind.argtypes = [U32, ctypes.c_void_p, ctypes.c_char_p]
    lib.moy_proc_get.argtypes = [U32, ctypes.POINTER(ctypes.c_void_p)]
    return lib


def test_a_compiled_apps_calls_find_its_grant_through_its_process_row(tmp_path):
    """moy_app_wasm_run_of maps the call's engine to the process row it is
    bound to (native/moy_kernel/moy_proc.h): the row the Player names next is
    the one the run's row takes, its open binds the engine and copies the
    grant, and a call reads the grant the ROW holds."""
    from runtime import moy_play
    _binding_or_skip()
    cart = _module(tmp_path)
    ws = host_app.build_workstation(str(tmp_path / "carts"))
    app = ws.app_abi
    lib = _proc_lib()
    h = ctypes.c_uint32()
    assert lib.moy_proc_add(77, 3, 1, b"test.alpha", 0, 0, ctypes.byref(h)) == 0
    lib.moy_proc_next(h.value)
    a, ga = _compiled(app, cart, "test.alpha")
    b, gb = _compiled(app, cart, "test.beta")      # no row: found among the runs
    try:
        row = ctypes.c_void_p()
        assert lib.moy_proc_get(h.value, ctypes.byref(row)) == 0
        r = ctypes.cast(row, ctypes.POINTER(moy_play._ProcRow)).contents
        assert r.engine and r.app.g == ga and r.pid == 77
        ca, cb = _Calls(a), _Calls(b)
        assert ca.put("k", "alpha") == 0 and cb.put("k", "beta") == 0
        assert ca.get("k") == "alpha" and cb.get("k") == "beta"
        roles, _ = moy_app.policy(("prefs",))
        assert lib.moy_proc_bind(h.value, r.engine, bytes(app.run(gb, roles))) == 0
        assert ca.get("k") == "beta", "the call did not read its row's grant"
        assert lib.moy_proc_end(h.value, 0) == 0
        assert ca.get("k") == "alpha", "with its row ended, the run's own"
    finally:
        lib.moy_proc_end(h.value, 0)
        b.close()
        a.close()


LUA_PREFS = """
SEEN = {}
function _init() prefs.set("k", "lua") end
function _update()
  SEEN[#SEEN + 1] = prefs.get("k", "none")
end
function _draw() cls(0) end
"""


def _write_lua_app(carts, name, src, perms):
    d = carts / (name + ".moy")
    d.mkdir(parents=True, exist_ok=True)
    (d / "manifest.json").write_text(json.dumps(
        {"title": name, "canvas": "320x240", "runtime": "lua", "main": "main.lua",
         "moybyte": {"type": "app", "permissions": list(perms)}}))
    (d / "main.lua").write_text(src)
    (d / "config.json").write_text("{}")


def _frame(ws):
    ws.input.begin_frame()
    ws.frame(DT)
    assert ws.player.cart_error is None, ws.player.cart_error


def test_a_lua_app_and_a_compiled_app_at_once_each_reach_only_their_own(tmp_path):
    _binding_or_skip()
    cart = _module(tmp_path)
    _write_lua_app(tmp_path / "carts", "Lua Prefs", LUA_PREFS, ["graphics", "prefs"])
    ws = build_ws(tmp_path)
    app = ws.app_abi
    # The compiled app's instance first; the Lua run opens a row of its own
    # beside it (native/moycore/moycore_lua.h).
    c, gc = _compiled(app, cart, "test.compiled")
    try:
        calls = _Calls(c)
        assert calls.put("k", "compiled") == 0
        assert select_on_home(ws, "Lua Prefs") is not None
        ws.open()
        for _ in range(3):
            _frame(ws)
            assert calls.get("k") == "compiled"
            assert calls.put("k", "compiled") == 0
        lua = ws.player._lua
        lua.exec("OUT = table.concat(SEEN, ',')")
        assert lua.get_global("OUT") == "lua,lua,lua"
        assert ws.player._app_ctx.grant != gc
        assert moy_app.Prefs(app, gc).get("k") == "compiled"
    finally:
        c.close()


def test_a_permission_turned_off_is_absent_at_the_runs_next_call(tmp_path):
    """The run's copy of its grant names the grant row, which is the
    authority at every call: Settings turning prefs off makes the app's next
    grant (the same row: one per app id) hold no prefs, so a run made before
    gets no value at its very next call, and a Lua state opened after has no
    `prefs` at all (nil, as Python's None)."""
    _binding_or_skip()
    cart = _module(tmp_path)
    src = """
NAME = ""
function _init() NAME = type(prefs) end
function _draw() cls(0) end
"""
    _write_lua_app(tmp_path / "carts", "Revoked", src, ["graphics", "prefs"])
    ws = build_ws(tmp_path)
    app = ws.app_abi
    entry = select_on_home(ws, "Revoked")
    assert entry is not None
    aid = app_perms.app_id(entry)
    c, g = _compiled(app, cart, aid)
    try:
        calls = _Calls(c)
        assert calls.put("k", 1) == 0 and calls.get("k") == 1
        ws.consent.turn(aid, "prefs", False)
        ws.open()
        _frame(ws)
        assert ws.player._lua.get_global("NAME") == "nil"
        assert ws.player._app_ctx.grant == g, "the app's grant is one row per app id"
        assert calls.get("k") == DENIED, "a run kept a role Settings turned off"
    finally:
        c.close()
