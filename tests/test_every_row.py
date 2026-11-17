"""Every row of the call table on every tier that projects it, compared with
Python's answer (docs/refactor_2026-11.md, phase 4, T3).

The probes are GENERATED from native/os_calls.json, never kept by hand: a
row with an `example` is called with it on each tier its projections name,

  * Python: `ctx.<role>.<verb>` on an AppContext holding every role, or the
    `moybyte` module's name, on CPython and on the boards' object model (the
    T-Deck's desktop booted on the desktop MicroPython, as the roles trace
    is: tests/test_semantic_traces.py);
  * Lua: the row's Lua name in a Lua app holding every permission the rows
    answer under;
  * wasm: the row's `moybyte.app` import in a module tools/wat.py assembles,
    run as a compiled app;
  * WASI: the row's `moybyte.os` import in a command assembled the same way,
    run in the terminal,

and each tier's answer is reduced to one outcome: an error code (or the
codes a face's text names), nothing, or a value in one form (text and bytes
alike, a JSON text read as its value, a NUL-joined list as its items). A row
whose tiers disagree is a finding, fixed where it differs.

The UI toolkit's rows are not here: their answer is a picture, and
tests/test_app_ui.py compares the tiers' pictures of the features it draws.
The rows with no tier-neutral parameters (an AppContext's own objects) are
the roles trace's (tests/test_semantic_traces.py).
"""

import binascii
import json
import os
import struct
import sys

import pytest

from tools import gen_os_table as G
from tools import wasm_cart

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

SRC = G.load()
ERRORS = {e["code"]: e for e in SRC["errors"]}
CODE_OF = {e["n"]: e["code"] for e in SRC["errors"]}
# The codes a face's shared refusal text stands for.
REFUSED = frozenset(e["code"] for e in SRC["errors"]
                    if e.get("msg") == ERRORS["BAD"]["msg"])
# WASI's errno (moy_wasi.c's role_errno) back to the codes it stands for.
WASI_ERRNO = {2: {"DENIED", "STALE"}, 68: {"FULL"}, 48: {"NOMEM"}, 44: {"ABSENT"},
              43: {"NOSTORE"}, 29: {"IO"}, 6: {"NEEDS_VM"}, 9: {"FORGED"}, 10: {"BUSY"},
              28: {"BAD"}}
# Rows whose value differs by design from call to call: compared by length.
# Ids a run hands out (a notification's, a timer's, a key's) are among them.
VARIES = {"moybyte.random", "moybyte.aes_gcm_seal", "moybyte.clock", "clipboard.seq", "theme.gen",
          "moybyte.notify", "moybyte.on", "moybyte.every", "moybyte.after", "moybyte.at",
          "moybyte.key_make", "moybyte.key_public", "moybyte.status_show"}
ROWS = [r for r in G.door_rows(SRC) if "example" in r and G.role_of(r) != "ui"]

def py_rows():
    """A role row an AppContext serves as ctx.<role>.<verb>, and the
    `moybyte` module's names."""
    from runtime.app_context import ROLES
    return [r for r in ROWS if G.role_of(r) in ROLES or r.get("py")]


def lua_rows():
    return [r for r in ROWS if r.get("lua")]


def own_form(r):
    """A row whose Python and Lua names are their own form (a callback where
    the C row posts a message: lua_own): compared on the import tiers only."""
    return bool(r.get("lua_own"))


def lua_args(r):
    """A row's example as its Lua name takes it: a files row's kind is the
    grant's folder in Lua (moy_app_lua.c's field_kind), not an argument."""
    return [a for (n, t), a in zip([p for p in G.params(r) if p[1] != "out"], r["example"])
            if n != "kind"]


def wasm_rows():
    return [r for r in ROWS if r.get("wasm") is True]


def wasi_rows():
    return [r for r in ROWS if r.get("wasi")]


def _arg_types(r):
    return [t for _n, t in G.params(r) if t != "out"]


def _has_out(r):
    return any(t == "out" for _n, t in G.params(r))


# -- the Python probe --------------------------------------------------------------
#
# A roles-trace driver (test_semantic_traces' ROLES_BOOT runs it on the VM):
# `roles_trace(ws)` prints one "T <row> <answer>" line a row, the answer JSON
# with every str and bytes as {"x": hex}.

PY_DRIVER = r'''CALLS = @CALLS@


def _hex(b):
    return "".join("%02x" % c for c in b)


def _enc(v):
    if v is None or isinstance(v, bool) or isinstance(v, int):
        return v
    if isinstance(v, float):
        return v
    if isinstance(v, str):
        return {"x": _hex(v.encode())}
    if isinstance(v, (bytes, bytearray, memoryview)):
        return {"x": _hex(bytes(v))}
    if isinstance(v, (list, tuple)):
        return [_enc(x) for x in v]
    if isinstance(v, dict):
        return {"d": {str(k): _enc(v[k]) for k in v}}
    if v is NO_STORE:
        return {"code": "NOSTORE"}
    return {"o": type(v).__name__}


def roles_trace(ws):
    """The trace as a Python app: the probe cart's run (`write_py_app`), its
    run grant and its process, as the Lua app's are."""
    import app_perms
    import moy_log
    cart = [c for c in ws.carts.all if c.get("title") == "Every Row Py"][0]
    app_perms.made_here(ws, cart)
    ws.home.select_cart(cart)
    ws.open()
    ws.input.begin_frame()
    ws.frame(1.0 / 30)
    pl = ws.run.player
    was = moy_log.current()
    moy_log.current(pl.pid)
    try:
        _trace(pl.app_ctx)
    finally:
        moy_log.current(was)


def _trace(ctx):
    import json
    import moybyte
    for name, py, types, args in CALLS:
        head, _, verb = py.rpartition(".")
        holder = moybyte if head == "moybyte" else getattr(ctx, head, None)
        if head in ROLES:
            holder = getattr(ctx, head, None)
        fn = getattr(holder, verb, None)
        if fn is None:
            print("T", name, json.dumps({"noname": py}))
            continue
        a = [x.encode() if t == "bytes" and x is not None else x
             for t, x in zip(types, args)]
        try:
            v = fn(*a)
        except OSError as e:
            out = {"raise": "OSError", "errno": e.args[0] if e.args else None}
        except MemoryError:
            out = {"raise": "MemoryError"}
        except Exception as e:
            out = {"raise": type(e).__name__, "msg": str(e)}
        else:
            out = {"ok": _enc(v)}
        print("T", name, json.dumps(out))
'''


def py_driver():
    calls = [(r["name"], r["name"] if G.role_of(r) != "moybyte" else r["py"][0], _arg_types(r),
              r["example"]) for r in py_rows()]
    return PY_DRIVER.replace("@CALLS@", repr(calls))


def write_py_app(carts):
    """The Python probe's cart, under the permissions the Lua app holds."""
    d = carts / "EveryRowPy.moy"
    d.mkdir(parents=True)
    (d / "manifest.json").write_text(json.dumps({
        "format": "moy-1", "title": "Every Row Py", "id": "test.everyrow.py",
        "runtime": "python", "main": "main.py", "canvas": "320x240",
        "moybyte": {"type": "app", "permissions": _perms()}}))
    (d / "main.py").write_text("def update():\n    pass\n\n\ndef draw():\n    pass\n")


def py_trace_cpython(tmp_path):
    from runtime import host_app
    from runtime.app_context import NO_STORE, ROLES
    import contextlib
    import io
    ns = {"ROLES": ROLES, "NO_STORE": NO_STORE}
    exec(py_driver(), ns)
    write_py_app(tmp_path / "carts")
    ws = host_app.build_workstation(str(tmp_path / "carts"))
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        ns["roles_trace"](ws)
    return _lines(out.getvalue().splitlines())


def _lines(lines):
    got = {}
    for line in lines:
        if line.startswith("T "):
            _t, name, raw = line.split(" ", 2)
            got[name] = json.loads(raw)
    return got


# -- the Lua probe -----------------------------------------------------------------

LUA_HEAD = r'''
local function hex(s)
  return (s:gsub(".", function(c) return string.format("%02x", c:byte()) end))
end
local enc
local function list(t)
  local n = 0
  for _ in pairs(t) do n = n + 1 end
  if n ~= #t then return nil end
  local out = {}
  for i = 1, #t do out[i] = enc(t[i]) end
  return "[" .. table.concat(out, ",") .. "]"
end
enc = function(v)
  local k = type(v)
  if v == nil then return "null" end
  if k == "boolean" then return tostring(v) end
  if k == "number" then
    if math.type(v) == "integer" then return string.format("%d", v) end
    return string.format("%.6g", v)
  end
  if k == "string" then return '{"x":"' .. hex(v) .. '"}' end
  if k == "table" then
    local l = list(v)
    if l then return l end
    local keys, out = {}, {}
    for key in pairs(v) do keys[#keys + 1] = tostring(key) end
    table.sort(keys)
    for i, key in ipairs(keys) do
      local val = v[key]
      if val == nil then val = v[tonumber(key)] end
      out[i] = '"' .. key .. '":' .. enc(val)
    end
    return '{"d":{' .. table.concat(out, ",") .. '}}'
  end
  return '{"o":"' .. k .. '"}'
end
local function quote(s) return '"' .. s:gsub('\\', '\\\\'):gsub('"', '\\"') .. '"' end
TRACE = {}
local function probe(name, path, ...)
  local f = _G
  for part in path:gmatch("[^.]+") do
    if type(f) ~= "table" then f = nil break end
    f = f[part]
  end
  if type(f) ~= "function" then
    TRACE[#TRACE + 1] = name .. ' {"noname":' .. quote(path) .. '}'
    return
  end
  local r = table.pack(pcall(f, ...))
  if not r[1] then
    local msg = tostring(r[2]):gsub("^[^:]*:%d+: ", "")
    TRACE[#TRACE + 1] = name .. ' {"raise":"LuaError","msg":' .. quote(msg) .. '}'
    return
  end
  local vals = {}
  for i = 2, r.n do vals[#vals + 1] = enc(r[i]) end
  TRACE[#TRACE + 1] = name .. ' {"ret":[' .. table.concat(vals, ",") .. ']}'
end
'''


def _lua_lit(v):
    if v is None:
        return "nil"
    if isinstance(v, int):
        return str(v)
    return '"%s"' % "".join("\\%d" % b for b in v.encode())


def lua_source():
    calls = ["  probe(%s, %s%s)" % (_lua_lit(r["name"]), _lua_lit(r["lua"][0]),
                                    "".join(", " + _lua_lit(a) for a in lua_args(r)))
             for r in lua_rows()]
    return (LUA_HEAD + "function _init()\n" + "\n".join(calls)
            + '\n  TRACE = table.concat(TRACE, "\\n")\nend\nfunction _draw() cls(0) end\n')


def _perms():
    out = set()
    for r in ROWS:
        out.update(G.perms_of(r, SRC))
    return sorted(p for p in out if p not in ("full_access",))


def lua_trace(tmp_path):
    from runtime.host_app import build_workstation
    from ws_helpers import select_on_home
    d = tmp_path / "carts" / "EveryRow.moy"
    d.mkdir(parents=True)
    (d / "manifest.json").write_text(json.dumps({
        "format": "moy-1", "title": "Every Row", "id": "test.everyrow", "runtime": "lua",
        "main": "main.lua", "canvas": "320x240",
        "moybyte": {"type": "app", "permissions": _perms()}}))
    (d / "main.lua").write_text(lua_source())
    (d / "config.json").write_text("{}")
    ws = build_workstation(str(tmp_path / "carts"))
    assert select_on_home(ws, "Every Row") is not None
    ws.open()
    ws.input.begin_frame()
    ws.frame(1.0 / 30)
    assert ws.run.player.cart_error is None, ws.run.player.cart_error
    text = ws.run.player.lua.get_global("TRACE")
    got = {}
    for line in (text or "").splitlines():
        name, _, raw = line.partition(" ")
        got[name] = json.loads(raw)
    return got


# -- the wasm and WASI probes ------------------------------------------------------
#
# One module a tier: each row's import called with its example, its answer
# stored at RES + 4*k and an out's bytes in a 4 KiB region of their own from
# OUT; the whole span written out at the end (the cart's `write`, the
# command's stdout) for the test to read.

RES = 61440
OUT = 65536
CAP = 4096


def _wat_bytes(b):
    return "".join("\\%02x" % c for c in b)


def probe_wat(rows, module, names, command):
    """The probe module's text: `names(r)` the import name of row `r`."""
    imports, data, body = [], [], []
    at = 1024
    outs = 0
    for k, r in enumerate(rows):
        ps, args = [], []
        ex = iter(r["example"])
        for _n, t in G.params(r):
            if t == "int":
                ps.append("i32")
                args.append("(i32.const %d)" % next(ex))
            elif t == "out":
                ps += ["i32", "i32"]
                args.append("(i32.const %d) (i32.const %d)" % (OUT + CAP * outs, CAP))
            else:
                v = next(ex)
                ps += ["i32", "i32"]
                if v is None:
                    args.append("(i32.const 0) (i32.const 0)")
                    continue
                b = v.encode()
                data.append('  (data (i32.const %d) "%s")' % (at, _wat_bytes(b)))
                args.append("(i32.const %d) (i32.const %d)" % (at, len(b)))
                at += len(b) + 1
        if _has_out(r):
            outs += 1
        imports.append('  (import "%s" "%s" (func $r%d (param %s) (result i32)))'
                       % (module, names(r), k, " ".join(ps)))
        body.append("    (i32.store (i32.const %d) (call $r%d %s))" % (RES + 4 * k, k, " ".join(args)))
    assert at < RES and len(rows) * 4 <= OUT - RES
    span = (OUT - RES) + CAP * outs
    pages = (OUT + CAP * outs) // 65536 + 1
    if command:
        imports.insert(0, '  (import "wasi_snapshot_preview1" "fd_write" '
                          '(func $fd_write (param i32 i32 i32 i32) (result i32)))')
        tail = ["    (i32.store (i32.const 16) (i32.const %d))" % RES,
                "    (i32.store (i32.const 20) (i32.const %d))" % span,
                "    (drop (call $fd_write (i32.const 1) (i32.const 16) (i32.const 1) (i32.const 24)))"]
        entry = ['  (func (export "_start")'] + body + tail + ["  )"]
    else:
        imports.insert(0, '  (import "moy" "write" (func $write (param i32 i32 i32 i32) (result i32)))')
        imports.insert(0, '  (import "moy" "cls" (func $cls (param i32)))')
        data.append('  (data (i32.const 32) "trace")')
        tail = ["    (drop (call $write (i32.const 32) (i32.const 5) (i32.const %d) (i32.const %d)))"
                % (RES, span)]
        entry = (['  (func (export "_init")'] + body + tail + ["  )"]
                 + ['  (func (export "_update") (param $dt f32))',
                    '  (func (export "_draw") (call $cls (i32.const 0)))'])
    return "\n".join(["(module"] + imports + ['  (memory (export "memory") %d %d)' % (pages, pages)]
                     + data + entry + [")"]) + "\n", pages


def _read_span(blob, rows):
    got, outs = {}, 0
    for k, r in enumerate(rows):
        v = struct.unpack_from("<i", blob, 4 * k)[0]
        out = None
        if _has_out(r):
            if v >= 0 and r.get("ret") == "len":
                at = (OUT - RES) + CAP * outs
                out = blob[at:at + min(v, CAP)]
            outs += 1
        got[r["name"]] = {"rc": v, "out": None if out is None else out.hex()}
    return got


def _wasm_or_skip():
    from runtime import wasm_binding, wasm_host
    if wasm_host.available():
        return
    why = wasm_binding.why_unavailable() or "unknown"
    if os.environ.get("CI") or os.environ.get("MOYBYTE_REQUIRE_HOST_WASM"):
        pytest.fail("the host wasm binding did not build: %s" % why)
    pytest.skip("no host wasm binding: %s" % why)


def _cart(tmp_path, name, man, probe):
    wat, man["memory"] = probe
    src = tmp_path / "src" / name
    (src / "src").mkdir(parents=True)
    (src / "manifest.json").write_text(json.dumps(man))
    (src / "src" / "main.wat").write_text(wat)
    dst = tmp_path / "carts" / name
    wasm_cart.build(str(src), str(dst))
    return dst


def wasm_trace(tmp_path):
    _wasm_or_skip()
    from runtime import host_app
    from ws_helpers import open_cart
    rows = wasm_rows()
    root = str(tmp_path / "carts")
    host_app.moy_carts.ensure_dirs(root)
    _cart(tmp_path, "every_row.moy", {
        "format": "moy-1", "title": "Every Row Wasm", "id": "test.everyrow", "runtime": "wasm",
        "main": "main.wasm", "extensions": ["moybyte.app"], "writable": ["trace"],
        "moybyte": {"type": "app", "permissions": _perms()}},
        probe_wat(rows, "moybyte.app", lambda r: G.ident(r["name"]), False))
    ws = host_app.build_workstation(root)
    open_cart(ws, "Every Row Wasm")
    for _ in range(2):
        ws.mark_dirty()
        ws.frame(1.0 / 30)
    path = os.path.join(os.path.dirname(root), "apps", "every_row", "trace")
    assert os.path.exists(path), ws.run.player.cart_error
    with open(path, "rb") as f:
        return _read_span(f.read(), rows)


def wasi_trace(tmp_path):
    _wasm_or_skip()
    from runtime.host_app import build_workstation
    from test_wasi_commands import run
    rows = wasi_rows()
    (tmp_path / "files" / "Documents").mkdir(parents=True)
    (tmp_path / "carts").mkdir(exist_ok=True)
    _cart(tmp_path, "everyrow.moy", {
        "format": "moy-1", "title": "Every Row Wasi", "id": "wasi.everyrow", "runtime": "wasm",
        "main": "main.wasm",
        "moybyte": {"type": "command", "permissions": _perms()}},
        probe_wat(rows, "moybyte.os", G.wasi_name, True))
    ws = build_workstation(str(tmp_path / "carts"))
    app = ws.app("terminal")
    app.open()
    app.draw(0)
    app.draw(0)
    try:
        run(app, "cd /files/Documents")
        run(app, "wasi.everyrow/everyrow > trace.bin")
    finally:
        app.close_session()
    path = tmp_path / "files" / "Documents" / "trace.bin"
    assert path.exists(), app.face.lines()
    return _read_span(path.read_bytes(), rows)


# -- one outcome a row ----------------------------------------------------------------

# What a face answers as nothing (None, nil) where C names a code: a row
# that found nothing, sealed bytes that do not open.
NONE = frozenset({"none", "ABSENT", "FORGED"})


def _none_of(codes):
    return codes | {"none"} if codes & NONE else codes


def _codes_of_text(text):
    for e in SRC["errors"]:
        if e["code"] == "OK":
            continue
        if text == e.get("msg") or text == e.get("lua"):
            return REFUSED if e["code"] in REFUSED else {e["code"]}
        if e.get("err") and (text == e["err"] or (e["code"] == "IO" and text.startswith("[Errno"))):
            return {e["code"]}
    return None


def _dec(v):
    """A probe's encoded value as Python: text and bytes as bytes."""
    if isinstance(v, dict):
        if "x" in v:
            return binascii.unhexlify(v["x"])
        if "d" in v:
            return {k: _dec(x) for k, x in v["d"].items()}
        if "code" in v:
            return v
        return ("object", v.get("o"))
    if isinstance(v, list):
        return [_dec(x) for x in v]
    if isinstance(v, bool):
        return int(v)
    return v


def _err_codes(err, row=None):
    if isinstance(err, dict) and err.get("code"):
        return {err["code"]}
    if isinstance(err, bytes):
        text = err.decode("utf-8", "replace")
        if row is not None and row.get("lua_ret") == "value_name" and text in ERRORS:
            return {text}           # the code's own name (moy_app_lua.c, LR_VALUE_NAME)
        return _codes_of_text(text)
    return None


def outcome(row, tier, ans):
    """(codes, value): the codes a refusal stands for, or None and the
    value ("none" for nothing)."""
    if tier in ("wasm", "wasi"):
        rc = ans["rc"]
        if rc < 0:
            if tier == "wasm":
                code = CODE_OF.get(-rc)
                return (_none_of({code}) if code else {"rc%d" % rc}), None
            return _none_of(WASI_ERRNO.get(-rc, {"errno%d" % -rc})), None
        if ans["out"] is not None:
            return None, binascii.unhexlify(ans["out"])
        if row.get("lua_enum") and rc < len(row["lua_enum"]):
            name = row["lua_enum"][rc]          # the index of the table's enum
            return (NONE, None) if name is None else (None, name.encode())
        if row.get("lua_ret") == "taken":
            return None, 1                      # 0 (OK): taken, as Python's and Lua's true
        return None, rc
    if "noname" in ans:
        return {"noname"}, None
    if "raise" in ans:
        if ans["raise"] == "OSError" and ans.get("errno") == 28:
            return {"FULL"}, None
        if ans["raise"] == "MemoryError":
            return {"NOMEM"}, None
        codes = _codes_of_text(ans.get("msg", ""))
        if codes:
            return codes, None
        # A refusal in the face's own words: an argument the call refuses.
        return REFUSED | {"raise"}, None
    if tier == "lua":
        vals = [_dec(v) for v in ans["ret"]]
        while vals and vals[-1] is None and len(vals) > 1:
            vals.pop()
        value = vals[0] if vals else None
        if len(ans["ret"]) == 2 and ans["ret"][1] is not None:
            codes = _err_codes(_dec(ans["ret"][1]), row)
            if codes:
                return codes, None
    else:
        value = _dec(ans["ok"])
        if isinstance(value, list) and len(value) == 2 and (
                value[1] is None or isinstance(value[1], bytes) or isinstance(value[1], dict)) \
                and row["name"].startswith("files."):
            codes = _err_codes(value[1])
            if codes:
                return codes, None
            value = value[0]
    if value is None:
        return NONE, None
    if isinstance(value, int) and value < 0 and CODE_OF.get(-value):
        return {CODE_OF[-value]}, None
    return None, value


def _same_value(row, a, b):
    if row["name"] in VARIES:
        return type(a) is type(b) or (isinstance(a, bytes) and isinstance(b, bytes))
    if a == b:
        return True
    for x, y in ((a, b), (b, a)):
        if isinstance(y, bytes) and not isinstance(x, bytes):
            try:
                if json.loads(y.decode()) == _plain(x):
                    return True
            except ValueError:
                pass
            if isinstance(x, list) and y.rstrip(b"\0").split(b"\0") == x:
                return True
            if isinstance(x, list) and not x and y == b"":
                return True
    return False


def _plain(v):
    if isinstance(v, bytes):
        return v.decode("utf-8", "replace")
    if isinstance(v, list):
        return [_plain(x) for x in v]
    if isinstance(v, dict):
        return {k: _plain(x) for k, x in v.items()}
    return v


def agree(row, a, b):
    (ca, va), (cb, vb) = a, b
    void = row.get("ret") == "int" and not _has_out(row)
    for c, v in ((ca, vb), (cb, va)):
        if void and c == NONE and v == 0:
            return True             # a void call: nothing in Python and Lua, 0 in C
    if ca is not None or cb is not None:
        return ca is not None and cb is not None and bool(ca & cb)
    return _same_value(row, va, vb)


# -- the tests ----------------------------------------------------------------------

@pytest.fixture(scope="module")
def traces(tmp_path_factory):
    out = {"python": py_trace_cpython(tmp_path_factory.mktemp("py")),
           "lua": lua_trace(tmp_path_factory.mktemp("lua"))}
    for tier, fn in (("wasm", wasm_trace), ("wasi", wasi_trace)):
        try:
            out[tier] = fn(tmp_path_factory.mktemp(tier))
        except pytest.skip.Exception:
            pass
    return out


TIER_ROWS = {"python": py_rows, "lua": lua_rows, "wasm": wasm_rows, "wasi": wasi_rows}


def test_every_row_a_tier_projects_has_an_example_and_a_probe():
    """A row with a tier's name and no example is a row no probe calls."""
    missing = [r["name"] for r in G.door_rows(SRC) if G.role_of(r) != "ui" and "example" not in r
               and "in" in r and (r.get("lua") or r.get("wasm") is True or r.get("wasi"))]
    assert not missing, "rows no probe calls (give them an example): %s" % missing


def test_each_probe_answers_every_row_its_tier_projects(traces):
    for tier, got in traces.items():
        want = [r["name"] for r in TIER_ROWS[tier]()]
        assert sorted(got) == sorted(want), tier
        nameless = sorted(n for n, a in got.items() if isinstance(a, dict) and "noname" in a)
        assert not nameless, "%s projects names its face does not hold: %s" % (tier, nameless)


def test_every_tier_answers_every_row_as_python_does(traces):
    py = traces["python"]
    differ = {}
    for tier in ("lua", "wasm", "wasi"):
        got = traces.get(tier)
        if got is None:
            continue
        for r in TIER_ROWS[tier]():
            name = r["name"]
            if name not in py or own_form(r):
                continue
            a = outcome(r, "python", py[name])
            b = outcome(r, tier, got[name])
            if not agree(r, a, b):
                differ["%s %s" % (tier, name)] = "python %r, %s %r" % (a, tier, b)
    assert not differ, "tiers disagree:\n" + "\n".join("%s: %s" % kv for kv in sorted(differ.items()))


def test_the_tiers_without_python_agree_with_each_other(traces):
    """A row Python does not project (the device and status handles) is
    compared across the tiers that do."""
    differ = {}
    for r in ROWS:
        if r in py_rows() and not own_form(r):
            continue
        tiers = [t for t in ("lua", "wasm", "wasi") if t in traces and r["name"] in traces[t]
                 and not (own_form(r) and t == "lua")]
        for t in tiers[1:]:
            a = outcome(r, tiers[0], traces[tiers[0]][r["name"]])
            b = outcome(r, t, traces[t][r["name"]])
            if not agree(r, a, b):
                differ["%s/%s %s" % (tiers[0], t, r["name"])] = "%r, %r" % (a, b)
    assert not differ, "tiers disagree:\n" + "\n".join("%s: %s" % kv for kv in sorted(differ.items()))


def test_the_python_probe_answers_alike_on_the_boards_object_model(tmp_path):
    from unix_mp import require_unix_mp
    import test_semantic_traces as st
    exe = require_unix_mp("moy_spine")
    board = _lines("T " + line for line in st._roles_trace_board(exe, tmp_path, py_driver(),
                                                                write_py_app))
    host = py_trace_cpython(tmp_path / "host")
    differ = {}
    for r in py_rows():
        a = outcome(r, "python", host[r["name"]])
        b = outcome(r, "python", board[r["name"]])
        if not agree(r, a, b):
            differ["board " + r["name"]] = "cpython %r, board %r" % (a, b)
    assert not differ, "the board's Python differs:\n" + "\n".join(
        "%s: %s" % kv for kv in sorted(differ.items()))


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
