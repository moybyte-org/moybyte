#!/usr/bin/env python3
# Map (grep -n a name to jump there):
#   -- the source's views      the rows read as roles, verbs, permissions and tiers
#   TableError                 a source the rules refuse, named by its row
#   perm_tiers                 the tiers a permission is served on, derived from its rows
#   -- the prototype           a row's C prototype, parsed
#   -- the source's rules      check: what the source may say
#   -- the ELF tier            the OS function table, the kernel's rows, the SDK
#   -- the app ABI             the C rows, the wasm and ui imports, Lua's names
#   -- the Python console and the docs  the GENERATED splices and the doc tables
#   outputs                    every file and its text, the one list main writes or checks
"""Write every tier's table of Moybyte's app and OS calls from their one
source, native/os_calls.json (docs/os_2026-11/tiers.md section 1):

  the ELF tier (the OS function table, by ordinal)
    native/moy_elf/moy_os_table.h   the rows' ordinals, the version, the table type
    native/moy_elf/moy_os_rows.h    the kernel's prototype of each row (moy_os_<name>)
    native/moy_elf/moy_os_table.c   the table, a retired row's typed stub, and the
                                    body of each row the role door serves
    sdk/include/moybyte/os.h        a program's header: the moybyte rows, the event
                                    descriptor and its record (from
                                    native/moy_kernel/moy_evq.h), a WASI program's
                                    moybyte.os imports
    sdk/src/moyos_stubs.c           a module's side of every row: the function by
                                    its C library name, calling the table by ordinal
    native/moy_elf/moy_os_check.c   each row's argument rule (u), checked before a
                                    U-mode module's call runs (the P4s, moy_umode.h)
  the WASI tier
    native/moy_wasm/moy_wasi_os_rows.h  a WASI program's moybyte.os imports
  the app ABI (native/moy_app)
    native/moy_app/moy_app_rows.h   the roles, the C rows, the door's table, the
                                    permission map
    native/moy_app/moy_app_wasm_rows.h  the moybyte.app imports: each native
    native/moy_ui/moy_ui_wasm_rows.h    the ui imports, served by native/moy_ui
    native/moy_app/moy_app_lua_rows.h   the names a Lua run and a Lua service hold
  the Python console, between GENERATED markers
    runtime/shell/app_context.py    ROLES
    runtime/shell/app_perms.py      TABLE, each permission's tiers derived
    runtime/shell/shell_servers.py  DOOR_ARGS
    runtime/system/scheduler.py     _CALLS
    runtime/run/lua_ext.py          the Lua run's role names
  docs/app_api_v1.md                the role table and the call table

The output is committed: no build runs this, and tests/test_os_table.py
regenerates it and fails on any difference.

    python3 tools/gen_os_table.py [--check]
"""

import argparse
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from board_config import runtime_file  # noqa: E402
SOURCE = os.path.join(ROOT, "native", "os_calls.json")
EVQ_H = os.path.join(ROOT, "native", "moy_kernel", "moy_evq.h")

KERNEL_H = os.path.join(ROOT, "native", "moy_elf", "moy_os_table.h")
ROWS_H = os.path.join(ROOT, "native", "moy_elf", "moy_os_rows.h")
KERNEL_C = os.path.join(ROOT, "native", "moy_elf", "moy_os_table.c")
SDK_H = os.path.join(ROOT, "sdk", "include", "moybyte", "os.h")
STUBS_C = os.path.join(ROOT, "sdk", "src", "moyos_stubs.c")
CHECK_C = os.path.join(ROOT, "native", "moy_elf", "moy_os_check.c")
APP_H = os.path.join(ROOT, "native", "moy_app", "moy_app_rows.h")
WASM_H = os.path.join(ROOT, "native", "moy_app", "moy_app_wasm_rows.h")
UI_H = os.path.join(ROOT, "native", "moy_ui", "moy_ui_wasm_rows.h")
LUA_H = os.path.join(ROOT, "native", "moy_app", "moy_app_lua_rows.h")
WASI_OS_H = os.path.join(ROOT, "native", "moy_wasm", "moy_wasi_os_rows.h")
# The module a WASI program imports a "wasi" row from.
WASI_OS = "moybyte.os"
APP_CONTEXT = str(runtime_file("app_context.py"))
APP_PERMS = str(runtime_file("app_perms.py"))
SHELL_SERVERS = str(runtime_file("shell_servers.py"))
SCHEDULER = str(runtime_file("scheduler.py"))
LUA_EXT = str(runtime_file("lua_ext.py"))
APP_DOC = os.path.join(ROOT, "docs", "app_api_v1.md")

# The rows that run the SDK's C library's exit path (__moy_exit, sdk/libc/)
# before the kernel's: C's exit, never _exit.
EXIT_HOOK = ("exit",)

TIERS = ("python", "lua", "wasm", "elf")
# moy-spec's tiers: the game verbs a "spec" permission's floor names are
# served on these (the cart verbs, which this source does not hold).
SPEC_TIERS = ("python", "lua", "wasm")

ROW_FIELDS = {"name", "kind", "server", "perm", "in", "ret", "how", "door", "wasm", "elf",
              "lua", "py", "hdr", "c", "va", "glibc", "fail", "noreturn", "retired", "u"}
ELF_FIELDS = {"hdr", "c", "va", "glibc", "fail", "noreturn", "retired", "u"}
PERM_FIELDS = {"perm", "grants", "prompt", "needs", "py", "tiers", "spec"}
# A row's argument rule (u): one token a kernel parameter. A row the door
# serves takes its rule from its in (_door_u).
#   -        a value: nothing to check
#   s        a NUL-terminated string the process can read
#   r w rw   a pointer to one of what it points at, readable / writable / both
#   r:N      ... N of them (a parameter's name), or *N (what that parameter
#            points at, itself checked first); void counts bytes
#   r#N      ... N bytes, whatever it points at (a sockaddr and its length)
#   f        a function the process's text holds
#   k        a value the row's body matches against its own records
#   ?        after any pointer token: NULL passes
U_TOKEN = re.compile(r"^(?:-|k|f|s\??|(?P<perm>rw|r|w)(?P<null>\?)?(?:(?P<sep>[:#])(?P<deref>\*)?(?P<n>[A-Za-z_]\w*))?)$")
KINDS = ("posix", "moybyte")
# Where a row is served: a C row of native/moy_app ("c"), the Python
# console's server for the role ("shell"), a process's ("proc:<name>"), a
# console call answered as the calling process ("call"), the ELF kernel's
# own ("kernel"), or a name the Python console hands its programs ("python").
DOOR_SERVERS = ("c", "shell", "call")
TYPES = ("int", "str", "str?", "bytes", "cart", "out")
RETS = ("int", "len")
HOWS = ("hop", "seq", "direct")
FLOOR_ROLE = "floor"


class TableError(ValueError):
    pass


def load(path=SOURCE):
    with open(path) as f:
        return json.load(f)


def dump(src):
    """The source's text as it is kept: one row a line."""
    o = ["{", '  "about": %s,' % json.dumps(src["about"]),
         '  "version": %d,' % src["version"]]
    for key in ("roles", "rows", "permissions"):
        items = src[key]
        o.append('  "%s": [' % key)
        o += ["    %s%s" % (json.dumps(x), "," if i < len(items) - 1 else "")
              for i, x in enumerate(items)]
        o.append("  ]" + ("," if key != "permissions" else ""))
    o += ["}", ""]
    return "\n".join(o)


# -- the source's views ---------------------------------------------------------

def is_door(r):
    """A row of the role table, which the ROLE door names by its index."""
    s = r.get("server")
    return s in DOOR_SERVERS or (s or "").startswith("proc:")


def role_of(r):
    return r["name"].split(".", 1)[0] if is_door(r) else None


def verb_of(r):
    return r["name"].split(".", 1)[1]


def door_rows(src):
    """The role table, in the door's order."""
    return [r for r in src["rows"] if is_door(r)]


def role_table(src=None):
    """The role table as the app ABI's tests read it: each row's role, verb,
    server, grant (its role's) and wasm (its import's WAMR signature, or
    None), in the door's order."""
    if src is None:
        src = load()
    return [{"role": role_of(r), "verb": verb_of(r), "server": r["server"],
             "grant": grant_of(src, role_of(r)),
             "wasm": wasm_sig(r) if r.get("wasm") is True else None} for r in door_rows(src)]


def roles(src):
    return [x["role"] for x in src["roles"]]


def ctx_roles(src):
    """The roles an AppContext builds an object for (app_context.ROLES)."""
    return [x["role"] for x in src["roles"] if x.get("ctx")]


def grant_of(src, role):
    for x in src["roles"]:
        if x["role"] == role:
            return x["grant"]
    raise TableError("no role %r" % role)


def params(r):
    """[(name, type)] of a row's neutral parameters."""
    out = []
    for p in r.get("in", ()):
        name, _, typ = p.partition(":")
        out.append((name, typ))
    return out


def wasm_sig(r):
    """The WAMR signature string of a row's moybyte.app import."""
    return "(%s)i" % "".join("i" if t == "int" else "*~" for _n, t in params(r))


def wasi_rows(src):
    """The rows a WASI program imports from moybyte.os."""
    return [r for r in src["rows"] if r.get("wasm") == "wasi"]


def wasi_params(r):
    """[(name, kind)] of a "wasi" row's C prototype: "s" a text (a pointer and
    a length in the import), "i" a number."""
    out = []
    for p in parse(r["c"])[2]:
        name = _param_name(p)
        out.append((name, "s" if p.startswith("const char *") else "i"))
    return out


def wasi_sig(r):
    """The WAMR signature string of a row's moybyte.os import."""
    return "(%s)i" % "".join("*~" if k == "s" else "i" for _n, k in wasi_params(r))


def door_kinds(r):
    """The row's fields as the door decodes them in Python: `s` text, `o`
    text where empty is None, `i` a number, `c` a cart, `b` bytes."""
    m = {"str": "s", "str?": "o", "int": "i", "cart": "c", "bytes": "b"}
    return "".join(m[t] for _n, t in params(r) if t != "out")


def perms_of(r, src):
    """The permissions a row answers under: its role's grant, and the row's
    own `perm` (a gate its server passes, as the nav verbs pass the front's)."""
    out = []
    role = role_of(r)
    if role is not None and grant_of(src, role) not in ("never", FLOOR_ROLE):
        out = [p["perm"] for p in src["permissions"] if p["grants"] == "role:" + role]
    p = r.get("perm")
    if p is not None:
        out += list(p) if isinstance(p, list) else [p]
    return out


def row_tiers(r, src):
    """The tiers that serve a row, from its projections."""
    out = set()
    if isinstance(r.get("elf"), int):
        out.add("elf")
    if r.get("wasm"):
        out.add("wasm")
    if r.get("lua"):
        out.add("lua")
    if r.get("py") or (role_of(r) in ctx_roles(src)):
        out.add("python")
    return out


def perm_tiers(p, src):
    """The tiers a permission is served on: every tier for the floor (moy-spec's
    verbs and nothing to consent to), Python for a marker (a shipped Python
    app's cart), the stated tiers for what no row serves (a native module's
    run, a service's start), and else the tiers of the rows it hands an app,
    moy-spec's tiers too where its verbs are the spec's."""
    kind = p["grants"].split(":")[0]
    if kind == "floor":
        return TIERS
    if kind == "marker":
        return ("python",)
    if "tiers" in p:
        return tuple(t for t in TIERS if t in p["tiers"])
    have = set(SPEC_TIERS) if p.get("spec") else set()
    for r in src["rows"]:
        if p["perm"] in perms_of(r, src):
            have |= row_tiers(r, src)
    return tuple(t for t in TIERS if t in have)


def c_name(r):
    """The C name of a moybyte row a module calls: moy_<role>_<verb>, and
    moy_<verb> for a console call."""
    role = role_of(r)
    if role == "moybyte":
        return "moy_" + verb_of(r)
    return "moy_%s_%s" % (role, verb_of(r))


def c_params(r):
    out = []
    for name, typ in params(r):
        if typ == "int":
            out.append("int32_t %s" % name)
        elif typ in ("str", "str?"):
            out.append("const char *%s" % name)
        elif typ == "bytes":
            out += ["const void *%s" % name, "size_t %s_len" % name]
        elif typ == "out":
            out += ["void *%s" % name, "size_t %s_cap" % name]
        else:
            raise TableError("%s: a %s has no C form" % (r["name"], typ))
    return out


def _door_u(r):
    """A door row's argument rule, from its in: the strings it reads, the
    bytes it reads and the answer it writes, each by its length."""
    u = []
    for name, typ in params(r):
        if typ == "int":
            u.append("-")
        elif typ in ("str", "str?"):
            u.append("s" if typ == "str" else "s?")
        elif typ == "bytes":
            u += ["r:%s_len" % name, "-"]
        elif typ == "out":
            u += ["w:%s_cap" % name, "-"]
    return u


def elf(src):
    """The OS function table: {version, rows} by ordinal, every row with its
    C prototype (a moybyte row a door row serves gets its own, from its
    neutral types) and `door`, its index in the role table, or None."""
    index = {r["name"]: i for i, r in enumerate(door_rows(src))}
    rows = {}
    for r in src["rows"]:
        o = r.get("elf")
        if not isinstance(o, int):
            continue
        if o in rows:
            raise TableError("ordinal %d twice: %s and %s" % (o, rows[o]["name"], r["name"]))
        if "c" in r:
            e = {k: r[k] for k in ("name", "kind", "c", "fail") if k in r}
            e.update({k: r[k] for k in ELF_FIELDS if k in r})
            e["name"] = parse(r["c"])[1]
            e["kind"] = r.get("kind", "moybyte")
            e["door"] = None
        else:
            e = {"name": c_name(r), "kind": "moybyte",
                 "c": "int32_t %s(%s)" % (c_name(r), ", ".join(c_params(r)) or "void"),
                 "fail": "-1", "door": index[r["name"]], "how": r.get("how", "hop"),
                 "u": _door_u(r), "src": r}
        rows[o] = e
    if sorted(rows) != list(range(len(rows))):
        raise TableError("the ordinals are not 0 .. %d" % (len(rows) - 1))
    return {"version": src["version"], "rows": [rows[i] for i in range(len(rows))]}


# -- the prototype ----------------------------------------------------------

_PROTO = re.compile(r"^(?P<ret>.*?[\s*])(?P<name>[A-Za-z_]\w*)\((?P<args>.*)\)$")


def _split(args):
    """The parameters of a parameter list, split at its top-level commas."""
    out, depth, cur = [], 0, ""
    for ch in args:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if ch == "," and depth == 0:
            out.append(cur.strip())
            cur = ""
        else:
            cur += ch
    if cur.strip():
        out.append(cur.strip())
    return out


def _param_name(p):
    m = re.search(r"\(\s*\*\s*([A-Za-z_]\w*)\s*\)", p)
    if m:
        return m.group(1)
    m = re.search(r"([A-Za-z_]\w*)\s*(\[[^\]]*\])?$", p)
    if not m:
        raise TableError("a parameter with no name: %r" % p)
    return m.group(1)


def parse(c):
    """(return type, name, [parameter], variadic) of prototype `c`."""
    m = _PROTO.match(c.strip())
    if not m:
        raise TableError("not a prototype: %r" % c)
    ret = m.group("ret").strip()
    ps = _split(m.group("args"))
    variadic = bool(ps) and ps[-1] == "..."
    if variadic:
        ps = ps[:-1]
    if ps == ["void"]:
        ps = []
    for p in ps:
        _param_name(p)
    return ret, m.group("name"), ps, variadic


# -- the source's rules ---------------------------------------------------------

def _check_elf_table(table):
    rows = table["rows"]
    seen = set()
    for i, r in enumerate(rows):
        if r["kind"] not in KINDS:
            raise TableError("row %d's kind: %r" % (i, r["kind"]))
        if r["kind"] == "posix" and not r.get("hdr"):
            raise TableError("posix row %d names no header" % i)
        if r["name"] in seen:
            raise TableError("row twice: %s" % r["name"])
        seen.add(r["name"])
        ret, name, _params, variadic = parse(r["c"])
        if name != r["name"]:
            raise TableError("row %d's prototype names %s" % (i, name))
        if variadic != ("va" in r):
            raise TableError("row %d: a ... and a va go together" % i)
        if (ret == "void") != (r["fail"] is None):
            raise TableError("row %d: fail is null exactly when it returns nothing" % i)
        if r.get("noreturn") and ret != "void":
            raise TableError("row %d: a noreturn row returns nothing" % i)
        _check_u(i, r)
    if table["version"] != len(rows):
        raise TableError("version %d with %d rows: an append bumps it, nothing else does"
                         % (table["version"], len(rows)))


def check(src):
    """The source's own rules: fields, servers, types, the projections each
    tier takes, Lua's names Python's, the ordinals, the permissions."""
    role_names = roles(src)
    if len(set(role_names)) != len(role_names):
        raise TableError("a role twice")
    perm_names = [p["perm"] for p in src["permissions"]]
    if len(set(perm_names)) != len(perm_names):
        raise TableError("a permission twice")
    for x in src["roles"]:
        g = x["grant"]
        if g not in ("never", FLOOR_ROLE) and g not in perm_names:
            raise TableError("role %s's grant %r is no permission" % (x["role"], g))
    seen = set()
    for i, r in enumerate(src["rows"]):
        name = r.get("name")
        if not name or set(r) - ROW_FIELDS:
            raise TableError("row %d's fields: %s" % (i, sorted(r)))
        if name in seen:
            raise TableError("row twice: %s" % name)
        seen.add(name)
        kind = r.get("kind", "moybyte")
        if kind not in KINDS:
            raise TableError("%s's kind: %r" % (name, kind))
        if kind == "posix":
            if "server" in r or not isinstance(r.get("elf"), int) or "c" not in r:
                raise TableError("%s: a posix row is an ELF row with its C prototype" % name)
        elif "server" not in r:
            raise TableError("%s names no server" % name)
        if set(r) & ELF_FIELDS and "c" not in r:
            raise TableError("%s: ELF details with no prototype" % name)
        if is_door(r):
            role = role_of(r)
            if role not in role_names or "." not in name:
                raise TableError("%s: a door row is <role>.<verb> of a role" % name)
        elif r.get("server") not in ("kernel", "python", None):
            raise TableError("%s's server: %r" % (name, r.get("server")))
        for pname, typ in params(r):
            if typ not in TYPES or not re.match(r"^[a-z_][a-z0-9_]*$", pname):
                raise TableError("%s's parameter %s:%s" % (name, pname, typ))
        if "ret" in r and r["ret"] not in RETS:
            raise TableError("%s's ret: %r" % (name, r["ret"]))
        if "how" in r and r["how"] not in HOWS:
            raise TableError("%s's how: %r" % (name, r["how"]))
        outs = [n for n, t in params(r) if t == "out"]
        if (r.get("ret") == "len") != bool(outs) and r.get("how") != "direct":
            raise TableError("%s: a len answer is what an out parameter holds" % name)
        if r.get("wasm") not in (None, True, "wasi"):
            raise TableError("%s's wasm: %r" % (name, r["wasm"]))
        if r.get("wasm") == "wasi":
            if "c" not in r or not isinstance(r.get("elf"), int) or kind != "moybyte":
                raise TableError("%s: a WASI import is a moybyte row the kernel serves" % name)
            ps = parse(r["c"])[2]
            if parse(r["c"])[0] != "int" or not all(
                    p.startswith("const char *") or p.startswith("int ") for p in ps):
                raise TableError("%s: a WASI import takes texts and numbers, and answers a "
                                 "number" % name)
        elif r.get("wasm"):
            if not ("in" in r and "ret" in r and "how" in r):
                raise TableError("%s: an import needs in, ret and how" % name)
            if (r["how"] == "direct") != (role_of(r) == "ui"):
                raise TableError("%s: a direct import is the toolkit's" % name)
            if r["how"] != "direct":
                ints = [k for k, (_n, t) in enumerate(params(r)) if t == "int"]
                if ints not in ([], [0]) or len(outs) > 1 or (outs and params(r)[-1][1] != "out"):
                    raise TableError("%s: the adapter packs one number first and one "
                                     "answer last" % name)
        e = r.get("elf")
        if e is not None and not (isinstance(e, int) or e == "deferred"):
            raise TableError("%s's elf: %r" % (name, e))
        if isinstance(e, int) and kind == "moybyte" and "c" not in r:
            if not is_door(r) or "in" not in r or "ret" not in r:
                raise TableError("%s: an ELF row the door serves needs in and ret" % name)
            if len(outs) > 1 or (outs and params(r)[-1][1] != "out"):
                raise TableError("%s: one answer, last" % name)
            if [n for n, t in params(r) if t == "cart"]:
                raise TableError("%s: a cart has no C form" % name)
        if r.get("door"):
            if r.get("server") not in ("shell", "call") and not r["server"].startswith("proc:"):
                raise TableError("%s: the door's Python half serves shell rows" % name)
            if "in" not in r or outs:
                raise TableError("%s: a door row's fields are its in, no out" % name)
        lua, py = r.get("lua") or [], r.get("py") or []
        if not set(lua) <= set(py):
            raise TableError("%s: Lua's names are Python's, verbatim: %s" % (
                name, sorted(set(lua) - set(py))))
        for p in perms_of(r, src):
            if p not in perm_names:
                raise TableError("%s's perm %r is no permission" % (name, p))
    for p in src["permissions"]:
        if set(p) - PERM_FIELDS:
            raise TableError("%s's fields: %s" % (p["perm"], sorted(p)))
        kind = p["grants"].split(":")[0]
        if "tiers" in p and kind not in ("full_access", "boot"):
            raise TableError("%s: only what no row serves states its tiers" % p["perm"])
        if kind in ("full_access", "boot") and "tiers" not in p:
            raise TableError("%s: no row serves it, so it states its tiers" % p["perm"])
        if not perm_tiers(p, src):
            raise TableError("%s: no tier serves it" % p["perm"])
    for r in door_rows(src):
        if role_of(r) == "ui" and r.get("elf") != "deferred":
            raise TableError("%s: the toolkit's rows are deferred for ELF" % r["name"])
    _check_elf_table(elf(src))


def _is_pointer(p):
    return "*" in p or "[" in p


def _check_u(i, r):
    """Every pointer a row takes has a rule, and every rule a parameter."""
    params = kernel_params(r)
    u = r.get("u", ["-"] * len(params))
    if len(u) != len(params):
        raise TableError("row %d's u has %d tokens for %d parameters" % (i, len(u), len(params)))
    names = [_param_name(p) for p in params]
    for p, tok in zip(params, u):
        m = U_TOKEN.match(tok)
        if not m:
            raise TableError("row %d: %r is not an argument rule" % (i, tok))
        if _is_pointer(p) != (tok != "-"):
            raise TableError("row %d: %s %s a rule" % (
                i, _param_name(p), "needs" if _is_pointer(p) else "is a value and takes no"))
        if m.group("n") and m.group("n") not in names:
            raise TableError("row %d: %r names no parameter" % (i, tok))


def kernel_params(r):
    _ret, _name, ps, variadic = parse(r["c"])
    if variadic:
        ps = ps + [r["va"]["arg"]]
    return ps


def kernel_proto(r, prefix="moy_os_"):
    ret, name, _p, _v = parse(r["c"])
    ps = kernel_params(r)
    sep = "" if ret.endswith("*") else " "
    return "%s%s%s%s(%s)" % (ret, sep, prefix, name, ", ".join(ps) or "void")


def ident(name):
    return re.sub(r"[^A-Za-z0-9_]", "_", name)


def upper(name):
    return ident(name).upper()


# -- the ELF tier -----------------------------------------------------------

TABLE_T = """typedef struct {
    uint32_t version;     // bumped on every append; never on anything else
    uint32_t rows;
    void *fn[];           // row i is the same prototype forever; retired -> a typed stub
} moy_os_table_t;"""

HEAD = "// GENERATED by tools/gen_os_table.py from native/os_calls.json. Do not edit.\n"


def _headers(rows):
    out = []
    for r in rows:
        h = r.get("hdr")
        if h and h not in out:
            out.append(h)
    return out


def gen_kernel_h(table):
    rows = table["rows"]
    o = [HEAD,
         "// The OS function table's ordinals (docs/os_2026-11/tiers.md section 1): row i\n"
         "// is the same prototype forever, appended never changed, a retired one\n"
         "// answering through a typed stub. A module records the rows it calls\n"
         "// (sdk/src/moyos_stubs.c); one that calls a row past MOY_OS_ROWS is refused.\n",
         "#ifndef MOY_OS_TABLE_H", "#define MOY_OS_TABLE_H", "",
         "#include <stdint.h>", "",
         "#define MOY_OS_VERSION %du" % table["version"],
         "#define MOY_OS_ROWS %du" % len(rows), "",
         TABLE_T, "",
         "enum {"]
    for i, r in enumerate(rows):
        o.append("    MOY_OS_ROW_%s = %d," % (ident(r["name"]), i))
    o += ["};", "",
          "extern const moy_os_table_t moy_os_table;",
          "// Each row's name, in ordinal order.",
          "extern const char *const moy_os_row_names[MOY_OS_ROWS];", "",
          "#endif // MOY_OS_TABLE_H", ""]
    return "\n".join(o)


def gen_rows_h(table):
    rows = table["rows"]
    o = [HEAD,
         "// The kernel's side of each row: moy_os_<name>, at the prototype the module's\n"
         "// C library declares (a varargs row takes its one optional argument in the\n"
         "// place of the ...). native/moy_elf/moy_os.c defines them, but a row the role\n"
         "// door serves, whose body moy_os_table.c has: moy_os_call over its fields.\n",
         "#ifndef MOY_OS_ROWS_H", "#define MOY_OS_ROWS_H", "",
         "#include <stddef.h>", "#include <stdint.h>", "#include <sys/types.h>", "",
         "// A header a board's C library lacks (sys/mman.h) leaves its rows' types",
         "// to sys/types.h."]
    for h in _headers(rows):
        o += ["#if __has_include(<%s>)" % h, "#include <%s>" % h, "#endif"]
    o += ["", "// The calling thread's errno, set by a row that fails.",
          "void moy_os_errno_set(int e);", "",
          "// A field of a row the role door serves: a number, a NUL-terminated text,",
          "// a text or NULL for none, or bytes and their length.",
          "enum { MOY_OS_ARG_INT = 0, MOY_OS_ARG_STR, MOY_OS_ARG_OPT, MOY_OS_ARG_BYTES };",
          "typedef struct {",
          "    const void *p;",
          "    size_t n;",
          "    int32_t v;",
          "    uint8_t kind;",
          "} moy_os_arg_t;",
          "// How it runs: on the task that serves the process's session, or where it",
          "// is called, under the app ABI's seqlock (native/moy_app/moy_app_wasm.h).",
          "enum { MOY_OS_HOP = 0, MOY_OS_SEQ = 1 };",
          "// Role table row `row` for the calling process's run, its fields `a` and",
          "// its answer written into `out` (cap bytes): the answer, or -1 and errno.",
          "int32_t moy_os_call(uint32_t row, int how, const moy_os_arg_t *a, int n, void *out,",
          "                    size_t cap);", ""]
    for r in rows:
        if r.get("retired"):
            continue
        attr = " __attribute__((noreturn))" if r.get("noreturn") else ""
        o.append("%s%s;" % (kernel_proto(r), attr))
    o += ["", "#endif // MOY_OS_ROWS_H", ""]
    return "\n".join(o)


_ARG = {"int": "MOY_OS_ARG_INT", "str": "MOY_OS_ARG_STR", "str?": "MOY_OS_ARG_OPT",
        "bytes": "MOY_OS_ARG_BYTES"}


def _call_body(r):
    src = r["src"]
    ins = [(n, t) for n, t in params(src) if t != "out"]
    out = [n for n, t in params(src) if t == "out"]
    o = ["%s {" % kernel_proto(r)]
    if ins:
        o.append("    const moy_os_arg_t a[%d] = {" % len(ins))
        for n, t in ins:
            if t == "int":
                o.append("        { NULL, 0u, %s, %s }," % (n, _ARG[t]))
            elif t == "bytes":
                o.append("        { %s, %s_len, 0, %s }," % (n, n, _ARG[t]))
            else:
                o.append("        { %s, 0u, 0, %s }," % (n, _ARG[t]))
        o.append("    };")
    how = "MOY_OS_SEQ" if r["how"] == "seq" else "MOY_OS_HOP"
    o.append("    return moy_os_call(%du, %s, %s, %d, %s, %s);   // %s" % (
        r["door"], how, "a" if ins else "NULL", len(ins),
        out[0] if out else "NULL", (out[0] + "_cap") if out else "0u", src["name"]))
    o.append("}")
    o.append("")
    return o


def gen_kernel_c(table):
    rows = table["rows"]
    o = [HEAD, '#if !__has_include("esp_heap_caps.h")    // a host (moy_os.c)', "#define _POSIX_C_SOURCE 200809L", "#define _DEFAULT_SOURCE",
         "#endif", "", "#include <errno.h>", "#include <stddef.h>", "",
         '#include "moy_os_table.h"', '#include "moy_os_rows.h"', ""]
    for r in rows:
        if r["door"] is not None and not r.get("retired"):
            o += _call_body(r)
    for r in rows:
        if not r.get("retired"):
            continue
        params_ = kernel_params(r)
        o.append("// Retired %s." % r["retired"])
        o.append("static %s {" % kernel_proto(r, "moy_os_retired_"))
        for p in params_:
            o.append("    (void)%s;" % _param_name(p))
        if r.get("noreturn"):
            o.append("    moy_os__exit(127);")
        else:
            o.append("    moy_os_errno_set(ENOSYS);")
            if r["fail"] is not None:
                o.append("    return %s;" % r["fail"])
        o.append("}")
        o.append("")
    o.append("const moy_os_table_t moy_os_table = {")
    o.append("    MOY_OS_VERSION, MOY_OS_ROWS, {")
    for r in rows:
        fn = ("moy_os_retired_" if r.get("retired") else "moy_os_") + r["name"]
        o.append("        (void *)%s," % fn)
    o += ["    },", "};", "",
          "const char *const moy_os_row_names[MOY_OS_ROWS] = {"]
    for r in rows:
        o.append('    "%s",' % r["name"])
    o += ["};", ""]
    return "\n".join(o)


def _evq_block(path=EVQ_H):
    """The event descriptor, its record's version and the record, as
    moy_evq.h states them."""
    with open(path) as f:
        text = f.read()
    out = []
    for pat in (r"#define MOY_EVQ_FD \d+", r"#define MOY_EVQ_VERSION \d+",
                r"#define MOY_EVQ_NAME \d+u", r"#define MOY_EVQ_APP \d+u",
                r"typedef struct \{[^}]*\} moy_evq_ev_t;"):
        m = re.search(pat, text, re.S)
        if not m:
            raise TableError("moy_evq.h no longer has %r" % pat)
        out.append(m.group(0))
    return "\n".join(out[:4]) + "\n\n" + out[4]


def _wasi_import(r):
    """A WASI program's side of a "wasi" row: its moybyte.os import, and the
    function by the row's C name over it (each text a pointer and a length)."""
    ret, name, ps, _v = parse(r["c"])
    verb = verb_of(r)
    args, call = [], []
    for p, (n, k) in zip(ps, wasi_params(r)):
        if k == "s":
            args += ["const char *%s" % n, "size_t %s_len" % n]
            call += [n, "%s ? __builtin_strlen(%s) : 0" % (n, n)]
        else:
            args.append(p)
            call.append(n)
    imp = "__moy_os_%s" % verb
    return ["int32_t %s(%s)" % (imp, ", ".join(args)),
            '    __attribute__((import_module("%s"), import_name("%s")));' % (WASI_OS, verb),
            "static inline %s {" % r["c"],
            "    int32_t r = %s(%s);" % (imp, ", ".join(call)),
            "    if (r < 0) {",
            "        errno = (int)-r;",
            "        return -1;",
            "    }",
            "    return (int)r;",
            "}"]


def gen_sdk_h(table):
    rows = table["rows"]
    src = load()
    o = [HEAD,
         "// A Moybyte program's header (sdk/README.md): the OS's own functions that\n"
         "// have no POSIX name, and the event descriptor a program reads its messages\n"
         "// from. A native module's POSIX functions are the C library's own headers'\n"
         "// (unistd.h, fcntl.h, poll.h, ...), served by the OS table; a WASI\n"
         "// program's are WASI's, and the calls it shares with a native module are\n"
         "// its moybyte.os imports, under the same names.\n",
         "#ifndef MOYBYTE_OS_H", "#define MOYBYTE_OS_H", "",
         "#include <stddef.h>", "#include <stdint.h>", "",
         "#ifdef __cplusplus", 'extern "C" {', "#endif", "",
         "// The OS table this header was made from: a module built against it runs",
         "// on a console whose table has every row the module calls.",
         "#define MOY_OS_VERSION %d" % table["version"], "",
         "// THE EVENT DESCRIPTOR (docs/os_2026-11/processes.md section 1, the service",
         "// model): the process's event queue, readable while a message is queued.",
         "// A program blocks in poll (poll_oneoff) on it, its nearest deadline the",
         "// timeout, and each read takes one record: a moy_evq_ev_t, then its",
         "// arguments (a JSON array, `n` bytes), then a NUL. A read never blocks: -1",
         "// with EAGAIN while nothing is queued, EMSGSIZE where the buffer is short",
         "// (the record's head written, its `n` what the arguments need, the",
         "// message kept). A native service that is stopped reads end of file (0),",
         "// its poll answering POLLHUP, and returns from main. A message reaches",
         "// the process only on a name it listens on (moy_on); moy_send answers",
         "// how many processes took one.",
         _evq_block(), "",
         "#ifdef __wasm__", "",
         "#include <errno.h>", ""]
    for r in wasi_rows(src):
        o += _wasi_import(r)
        o.append("")
    o += ["#else", "",
          "// The table the OS hands a module at its load (sdk/src/moyos_crt.c).",
          TABLE_T, "",
          "// moy_log's levels: the OS log's (native/moy_kernel/moy_log.h).",
          "#define MOY_LOG_ERR 3", "#define MOY_LOG_WARNING 4", "#define MOY_LOG_INFO 6",
          "#define MOY_LOG_DEBUG 7", ""]
    for r in rows:
        if r["kind"] == "moybyte" and r["door"] is None and not r.get("retired"):
            o.append("%s;" % r["c"])
    o += ["",
          "// The app's calls (docs/app_api_v1.md, the call table): each the console's",
          "// row of the same name, under the process's permissions. A text is",
          "// NUL-terminated; an answer is written into the buffer given with its",
          "// capacity. Each answers its number (a length, for an answer written),",
          "// or -1 with errno: EACCES a permission it lacks, ERANGE a buffer too",
          "// small, EINVAL an argument refused, ENOENT nothing of that name, EAGAIN",
          "// no console to serve it now (its queue full, or the process ending)."]
    for r in rows:
        if r["door"] is not None:
            o.append("%s;   // %s" % (r["c"], r["src"]["name"]))
    o += ["", "#endif // __wasm__", "",
          "#ifdef __cplusplus", "}", "#endif", "", "#endif // MOYBYTE_OS_H", ""]
    return "\n".join(o)


def gen_wasi_h(src):
    o = [HEAD,
         "// A WASI program's moybyte.os imports (native/moy_wasm/moy_wasi.h): each\n"
         "// X(verb, its WAMR signature), a text a pointer and a length, the rows of\n"
         "// native/os_calls.json marked \"wasi\", whose C function the kernel serves an\n"
         "// ELF module too.\n",
         "#ifndef MOY_WASI_OS_ROWS_H", "#define MOY_WASI_OS_ROWS_H", "",
         "#define MOY_WASI_OS_ROWS(X) \\"]
    for r in wasi_rows(src):
        o.append('    X(%s, "%s") \\' % (verb_of(r), wasi_sig(r)))
    o += ["", "#endif // MOY_WASI_OS_ROWS_H", ""]
    return "\n".join(o)


def gen_stubs_c(table):
    rows = table["rows"]
    o = [HEAD,
         "// The module's side of the OS table: each row a function under its C library\n"
         "// name that calls the table's row by its ordinal. Built once per row\n"
         "// (-DMOY_OS_ONLY=<row>) into libmoyos.a, so a module links only the rows it\n"
         "// calls, and each records its ordinal in the non-loaded section .moy.rows,\n"
         "// which the loader reads to refuse a module that needs a newer console. A\n"
         "// retired row has none: a module built now cannot call it.\n",
         "#define _DEFAULT_SOURCE 1", "#define _GNU_SOURCE 1",
         "#undef _FORTIFY_SOURCE", "",
         "#include <stdarg.h>", "#include <stdint.h>", "#include <stddef.h>",
         '#include "moyos_rt.h"', "",
         "#if defined(MOY_OS_ONLY)",
         "#define MOY_OS_ROW(n) ((n) == MOY_OS_ONLY)",
         "#else",
         "#define MOY_OS_ROW(n) 1",
         "#endif", "",
         "#define MOY_OS_STR_(x) #x",
         "#define MOY_OS_STR(x) MOY_OS_STR_(x)",
         "#define MOY_OS_USED(n) __asm__(\".pushsection .moy.rows,\\\"\\\",@progbits\\n"
         ".4byte \" MOY_OS_STR(n) \"\\n.popsection\")", ""]
    for i, r in enumerate(rows):
        if r.get("retired"):
            o.append("// %d %s: retired %s" % (i, r["name"], r["retired"].split(":")[0]))
            o.append("")
            continue
        ret, name, ps, variadic = parse(r["c"])
        kparams = kernel_params(r)
        args = ", ".join(_param_name(p) for p in kparams)
        o.append("#if MOY_OS_ROW(%d)" % i)
        if r["kind"] == "posix":
            o.append("#include <%s>" % r["hdr"])
        else:
            o.append('#include "moybyte/os.h"')
        o.append("typedef %s;" % kernel_proto(r, "moy_os_fn_").replace(
            "moy_os_fn_" + name + "(", "moy_os_fn_" + ident(name) + "(", 1))
        o.append("MOY_OS_USED(%d);" % i)
        if name in EXIT_HOOK:
            o.append('extern void __moy_exit(void) __attribute__((weak, visibility("hidden")));')
        sig = r["c"]
        if r.get("glibc"):
            o.append("#ifdef __GLIBC__")
            o.append("%s" % sig.replace(name + "(", r["glibc"] + "(", 1))
            o.append("#else")
            o.append("%s" % sig)
            o.append("#endif")
        else:
            o.append(sig)
        o.append("{")
        if variadic:
            va = r["va"]
            vname = _param_name(va["arg"])
            vtype = va["arg"][:va["arg"].rfind(vname)].strip()
            o.append("    %s = 0;" % va["arg"])
            o.append("    if (%s) {" % va["when"])
            o.append("        va_list ap;")
            o.append("        va_start(ap, %s);" % _param_name(ps[-1]))
            o.append("        %s = va_arg(ap, %s);" % (vname, vtype))
            o.append("        va_end(ap);")
            o.append("    }")
        call = "((moy_os_fn_%s *)__moy_os->fn[%d])(%s)" % (ident(name), i, args)
        if name in EXIT_HOOK:
            # The SDK's C library runs its atexit list and flushes its
            # streams first, when the module links it (sdk/libc/).
            o.append("    if (__moy_exit) {")
            o.append("        __moy_exit();")
            o.append("    }")
        if r.get("noreturn"):
            o.append("    %s;" % call)
            o.append("    __builtin_unreachable();")
        elif ret == "void":
            o.append("    %s;" % call)
        else:
            o.append("    return %s;" % call)
        o.append("}")
        o.append("#endif")
        o.append("")
    return "\n".join(o)


# -- the app ABI --------------------------------------------------------------

def _wrap(items, indent="    ", width=96):
    """Comma-separated `items` filled to `width` columns."""
    out, cur = [], indent
    for it in items:
        piece = it + ","
        if len(cur) + len(piece) + 1 > width and cur.strip():
            out.append(cur.rstrip())
            cur = indent
        cur += piece + " "
    if cur.strip():
        out.append(cur.rstrip())
    return out


def _c_decl(head, ps, tail, width=99):
    """`head(p, p, ...)tail`, its parameters wrapped under the first."""
    line = head + "("
    pad = " " * len(line)
    out = []
    for i, p in enumerate(ps):
        piece = p + ("," if i < len(ps) - 1 else ")" + tail)
        if len(line) + len(piece) + 1 > width and line.strip() and not line.endswith("("):
            out.append(line.rstrip())
            line = pad
        line += piece + ("" if i == len(ps) - 1 else " ")
    if not ps:
        line += ")" + tail
    out.append(line)
    return out


def c_rows(src):
    return [r for r in door_rows(src) if r["server"] == "c"]


def gen_app_h(src):
    rs = roles(src)
    floor = [x["role"] for x in src["roles"] if x["grant"] == FLOOR_ROLE]
    o = [HEAD,
         "// The app ABI's tables (native/moy_app/moy_app.h): the roles, the rows served\n"
         "// in C, the role table the ROLE door names by index, and the permission each\n"
         "// role is granted by.\n",
         "#ifndef MOY_APP_ROWS_H", "#define MOY_APP_ROWS_H", "",
         "// The roles: a grant's mask is 1 << role.",
         "enum {"]
    o += _wrap(["MOY_ROLE_%s" % upper(r) + (" = 0" if i == 0 else "") for i, r in enumerate(rs)]
               + ["MOY_ROLE_N"])
    o += ["};",
          "// The roles every grant holds: the console calls, which the console answers",
          "// under the permission each names.",
          "#define MOY_ROLE_FLOOR (%s)" % (" | ".join("(1u << MOY_ROLE_%s)" % upper(r)
                                                      for r in floor) or "0u"),
          "#define MOY_ROLE_NAMES { \\"]
    o += [ln + " \\" for ln in _wrap(['"%s"' % r for r in rs], "    ", 92)]
    o += ["}", "",
          "// The rows served in C: what moy_app_count counts.",
          "enum {"]
    cr = c_rows(src)
    o += _wrap(["MOY_ROW_%s" % upper(r["name"]) + (" = 0" if i == 0 else "")
                for i, r in enumerate(cr)] + ["MOY_ROW_N"])
    o += ["};",
          "#define MOY_ROW_NAMES { \\"]
    o += [ln + " \\" for ln in _wrap(['"%s"' % r["name"] for r in cr], "    ", 92)]
    o += ["}", "",
          "// The role table, row for row: what the ROLE door names by index.",
          "enum {"]
    dr = door_rows(src)
    o += _wrap(["MOY_T_%s" % upper(r["name"]) + (" = 0" if i == 0 else "")
                for i, r in enumerate(dr)])
    o += ["};",
          "#define MOY_APP_TABLE_N %du" % len(dr),
          "// Each row's \"role.verb\", its role and its C row (-1: the Python console",
          "// serves it).",
          "#define MOY_APP_TABLE_ROWS { \\"]
    for r in dr:
        crow = "MOY_ROW_%s" % upper(r["name"]) if r["server"] == "c" else "-1"
        o.append('    { "%s", MOY_ROLE_%s, %s }, \\' % (r["name"], upper(role_of(r)), crow))
    o += ["}", "",
          "// The permission each role is granted by: a role absent here is never",
          "// granted.",
          "#define MOY_APP_PERMS { \\"]
    for x in src["roles"]:
        if x["grant"] not in ("never", FLOOR_ROLE):
            o.append('    { "%s", MOY_ROLE_%s }, \\' % (x["grant"], upper(x["role"])))
    o += ["}", "", "#endif // MOY_APP_ROWS_H", ""]
    return "\n".join(o)


def _ui_c_params(r):
    out = []
    for _n, t in params(r):
        if t == "int":
            out.append("int32_t")
        elif t == "out":
            out += ["uint8_t *", "uint32_t"]
        else:
            out += ["const uint8_t *", "uint32_t"]
    return ", ".join(["void *"] + out)


def wasm_rows(src):
    return [r for r in door_rows(src) if r.get("wasm") is True]


def gen_wasm_h(src):
    rows = wasm_rows(src)
    o = [HEAD,
         "// The moybyte.app imports (native/moy_app/moy_app_wasm.h): one native per\n"
         "// row with an import, named <role>_<verb> at its WAMR signature. A row the\n"
         "// adapter serves packs its fields for the ROLE door and runs as its how\n"
         "// says (run(), moy_app_wasm.c); a toolkit row is native/moy_ui's own,\n"
         "// weak here, so an image without the toolkit answers NULL for it.\n",
         "#define MOY_APP_WASM_N %d" % len(rows), ""]
    for r in rows:
        if r["how"] != "direct":
            continue
        o += _c_decl("int32_t moy_ui_wasm_%s" % verb_of(r), _ui_c_params(r).split(", "),
                     " UI_WEAK;")
    o.append("")
    for r in rows:
        if r["how"] == "direct":
            continue
        cps = ["void *e"]
        num = "NULL"
        spans = []
        for n, t in params(r):
            if t == "int":
                cps.append("int32_t %s" % n)
                num = "&" + n
            elif t == "out":
                cps += ["uint8_t *%s" % n, "uint32_t %s_cap" % n]
            else:
                cps += ["const uint8_t *%s" % n, "uint32_t %s_n" % n]
                spans.append(n)
        outs = [n for n, t in params(r) if t == "out"]
        fn = "w_" + ident(r["name"])
        o += _c_decl("static int32_t %s" % fn, cps, " {")
        if spans:
            o.append("    span_t s[%d] = { %s };" % (
                len(spans), ", ".join("{ %s, %s_n }" % (n, n) for n in spans)))
        o.append("    return run(e, %s, MOY_T_%s, %s, %s, %d, %s, %s);" % (
            r["how"].upper(), upper(r["name"]), num, "s" if spans else "NULL", len(spans),
            outs[0] if outs else "NULL", (outs[0] + "_cap") if outs else "0"))
        o.append("}")
    o += ["", "// The natives, in the role table's order, and the table row each serves.",
          "static const moy_app_native_t NATIVES[MOY_APP_WASM_N] = {"]
    for r in rows:
        fn = ("moy_ui_wasm_" + verb_of(r)) if r["how"] == "direct" else "w_" + ident(r["name"])
        o.append('    { "%s", FN(%s), "%s", NULL },' % (ident(r["name"]), fn, wasm_sig(r)))
    o += ["};", "static const int16_t NATIVE_ROWS[MOY_APP_WASM_N] = {"]
    o += _wrap(["MOY_T_%s" % upper(r["name"]) for r in rows])
    o += ["};", ""]
    return "\n".join(o)


def gen_ui_h(src):
    rows = [r for r in wasm_rows(src) if r["how"] == "direct"]
    o = [HEAD,
         "// The toolkit's moybyte.app imports (native/moy_ui/moy_ui_wasm.h states each\n"
         "// verb's contract): their count, each native's prototype, which must be\n"
         "// moy_ui_wasm.h's, and the natives by import name, in the role table's order.\n",
         "#ifndef MOY_UI_WASM_ROWS_H", "#define MOY_UI_WASM_ROWS_H", "",
         "#include <stdint.h>", "",
         "#define MOY_UI_WASM_ROWS %d" % len(rows), ""]
    for r in rows:
        o += _c_decl("int32_t moy_ui_wasm_%s" % verb_of(r), _ui_c_params(r).split(", "), ";")
    o += ["", "#define MOY_UI_WASM_NATIVES { \\"]
    for r in rows:
        o.append('    { "%s", FN(moy_ui_wasm_%s) }, \\' % (ident(r["name"]), verb_of(r)))
    o += ["}", "", "#endif // MOY_UI_WASM_ROWS_H", ""]
    return "\n".join(o)


def lua_names(src):
    """[(role, table or None, name, C function, row)] of the names a Lua run's
    role binding holds, in the source's order."""
    out = []
    for r in door_rows(src):
        role = role_of(r)
        if role == "moybyte":
            continue
        for n in r.get("lua") or ():
            tbl, _, fn = n.rpartition(".")
            out.append((role, tbl or None, fn, "l_" + ident(n), r["name"]))
    return out


def lua_calls(src):
    """The console calls a Lua service holds as moybyte.<verb>."""
    return [verb_of(r) for r in door_rows(src)
            if role_of(r) == "moybyte" and r.get("lua")]


def gen_lua_h(src):
    o = [HEAD,
         "// The names a Lua program holds (native/moy_app/moy_app_lua.h): a run's role\n"
         "// binding, each X(role, its table or NULL for a global, its name, the C\n"
         "// function moy_app_lua.c serves it with, the table row it calls), opened\n"
         "// for the roles the run holds; and the console calls a Lua service's\n"
         "// `moybyte` table holds (native/moycore/moycore_svc.c), each answered by\n"
         "// the console's Python as the service's process.\n",
         "#ifndef MOY_APP_LUA_ROWS_H", "#define MOY_APP_LUA_ROWS_H", "",
         "#define MOY_APP_LUA_NAMES(X) \\"]
    for role, tbl, fn, cfn, row in lua_names(src):
        o.append('    X(MOY_ROLE_%s, %s, "%s", %s, MOY_T_%s) \\' % (
            upper(role), '"%s"' % tbl if tbl else "NULL", fn, cfn, upper(row)))
    o += ["", "#define MOY_APP_LUA_CALLS(X) \\"]
    for v in lua_calls(src):
        o.append('    X("%s") \\' % v)
    o += ["", "#endif // MOY_APP_LUA_ROWS_H", ""]
    return "\n".join(o)


# -- the Python console and the docs -----------------------------------------

PY_BEGIN = "# GENERATED by tools/gen_os_table.py from native/os_calls.json. Do not edit.\n"
PY_END = "# END GENERATED\n"
MD_BEGIN = "<!-- GENERATED by tools/gen_os_table.py from native/os_calls.json. Do not edit. -->\n"
MD_END = "<!-- END GENERATED -->\n"


def _splice(path, body, begin=PY_BEGIN, end=PY_END):
    """`path`'s text with its one generated region's body replaced."""
    with open(path) as f:
        text = f.read()
    a = text.find(begin)
    b = text.find(end, a + 1)
    if a < 0 or b < 0 or text.find(begin, a + 1) >= 0:
        raise TableError("%s has no one generated region" % os.path.relpath(path, ROOT))
    return text[:a + len(begin)] + body + text[b:]


def gen_app_context(src):
    names = ctx_roles(src)
    lines = _wrap(['"%s"' % r for r in names], "         ", 80)
    lines[0] = "ROLES = (" + lines[0].lstrip()
    lines[-1] = lines[-1].rstrip(",") + ")"
    return _splice(APP_CONTEXT, "\n".join(lines) + "\n")


def _py(v):
    return "None" if v is None else json.dumps(v)


def _tup(v):
    if not v:
        return "()"
    if len(v) == 1:
        return "(%s,)" % json.dumps(v[0])
    return "(%s)" % ", ".join(json.dumps(x) for x in v)


def gen_app_perms(src):
    o = ["TABLE = ("]
    for p in src["permissions"]:
        head = "    (%s, %s, %s," % (json.dumps(p["perm"]), json.dumps(p["grants"]),
                                     _tup(perm_tiers(p, src)))
        tail = "%s, %s, %s)," % (_py(p["prompt"]), _py(p["needs"]), _tup(p["py"]))
        if len(head) + 1 + len(tail) <= 99:
            o.append(head + " " + tail)
        else:
            o += [head, "     " + tail]
    o.append(")")
    return _splice(APP_PERMS, "\n".join(o) + "\n")


def gen_door_args(src):
    o = ["DOOR_ARGS = {"]
    for r in door_rows(src):
        if r.get("door"):
            o.append('    "%s": "%s",' % (r["name"], door_kinds(r)))
    o.append("}")
    return _splice(SHELL_SERVERS, "\n".join(o) + "\n")


def gen_calls(src):
    o = ["_CALLS = {"]
    for v in lua_calls(src):
        o.append('    "moybyte.%s": _moybyte.%s,' % (v, v))
    o.append("}")
    return _splice(SCHEDULER, "\n".join(o) + "\n")


def gen_lua_ext(src):
    heads = []
    for _role, tbl, fn, _c, _row in lua_names(src):
        h = tbl or fn
        if h not in heads:
            heads.append(h)
    return _splice(LUA_EXT, "LUA_ROLE_NAMES = %s\n" % _tup(heads))


def _cell(names):
    return ", ".join("`%s`" % n for n in names) if names else ""


def gen_doc(src):
    o = ["| role | what it is | granted by | verbs |", "|---|---|---|---|"]
    for x in src["roles"]:
        if not x.get("ctx"):
            continue
        verbs = ", ".join("`%s()`" % verb_of(r) for r in door_rows(src)
                          if role_of(r) == x["role"])
        perm = next((p["perm"] for p in src["permissions"]
                     if p["grants"] == "role:" + x["role"]), None)
        o.append("| `ctx.%s` | %s | %s | %s |" % (x["role"], x.get("doc", ""),
                                                 "`%s`" % perm if perm else "the shell",
                                                 verbs))
    o += ["", "The calls a program reaches on more than one tier, each by its name",
          "there (`docs/os_2026-11/tiers.md` §1; the toolkit's `ui` rows are",
          "`docs/ui_api_2026-10.md`'s):", "",
          "| call | permission | Python | Lua | wasm (`moybyte.app`; a WASI program's `moybyte.os`) | C (ELF) |",
          "|---|---|---|---|---|---|"]
    table = elf(src)
    cnames = {}
    for e in table["rows"]:
        if e["door"] is not None:
            cnames[e["src"]["name"]] = e["name"]
    for r in src["rows"]:
        if r.get("kind") == "posix" or role_of(r) == "ui":
            continue
        if r.get("retired"):
            continue
        cname = cnames.get(r["name"]) or (parse(r["c"])[1] if "c" in r else None)
        imported = (ident(r["name"]) if r.get("wasm") is True
                    else "%s.%s" % (WASI_OS, verb_of(r)) if r.get("wasm") else None)
        if not (cname or imported or r.get("lua")):
            continue
        perms = perms_of(r, src)
        py = list(r.get("py") or ())
        if role_of(r) in ctx_roles(src):
            py = ["ctx.%s" % r["name"]] + py
        o.append("| `%s` | %s | %s | %s | %s | %s |" % (
            r["name"], _cell(perms) or "none", _cell(py), _cell(r.get("lua")),
            _cell([imported] if imported else []), _cell([cname] if cname else [])))
    return _splice(APP_DOC, "\n".join(o) + "\n", MD_BEGIN, MD_END)


def _ptype(p):
    """A parameter's type: its declaration without its name."""
    name = _param_name(p)
    return re.sub(r"\b%s\b" % name, "", p, count=1).strip()


def _u_expr(tok, p, by_name):
    m = U_TOKEN.match(tok)
    name = _param_name(p)
    if tok in ("-", "k"):
        return None
    if tok == "f":
        return "U_CODE(%s)" % name
    if tok.startswith("s"):
        return ("U_STR0(%s)" if tok.endswith("?") else "U_STR(%s)") % name
    perm = {"r": "MOY_U_R", "w": "MOY_U_W", "rw": "MOY_U_R | MOY_U_W"}[m.group("perm")]
    pointee = _ptype(p).rstrip()[:-1].strip()
    void = re.sub(r"\bconst\b", "", pointee).strip() == "void"
    size = "1" if void or m.group("sep") == "#" else "sizeof *%s" % name
    if m.group("n"):
        n = m.group("n")
        count = "(%s != NULL ? (size_t)*%s : 0)" % (n, n) if m.group("deref") else "(size_t)%s" % n
        size = "u_n(%s, %s)" % (count, size)
    return "%s(%s, %s, %s)" % ("U_BUF0" if m.group("null") else "U_BUF", name, size, perm)


def gen_check_c(table):
    rows = table["rows"]
    o = [HEAD,
         "// Each row's argument rule (os_calls.json's u), over a U-mode module's\n"
         "// registers, before its call runs: every pointer it passes inside its own\n"
         "// process (moy_umode.h). A row whose arguments are all values has none.\n",
         "#include <stddef.h>", "#include <stdint.h>", "",
         '#include "moy_os_rows.h"', '#include "moy_umode.h"', "",
         "#define U_STR(p) moy_u_str(u, (const char *)(p))",
         "#define U_STR0(p) ((p) == NULL || U_STR(p))",
         "#define U_BUF(p, n, perm) moy_u_ok(u, (const void *)(p), (n), (perm))",
         "#define U_BUF0(p, n, perm) ((p) == NULL || U_BUF(p, n, perm))",
         "#define U_CODE(p) moy_u_ok(u, (const void *)(p), 4, MOY_U_X)",
         "// Registers a parameter takes: a check sees at most 7 after its space.",
         "#define U_REGS(t) ((sizeof(t) + 3u) / 4u)", "",
         "static size_t u_n(size_t n, size_t each) {",
         "    return each != 0 && n > SIZE_MAX / each ? SIZE_MAX : n * each;",
         "}", "",
         "typedef int u_check_fn(const moy_u_space_t *, uint32_t, uint32_t, uint32_t, uint32_t,",
         "                       uint32_t, uint32_t, uint32_t);", ""]
    checked = []
    for i, r in enumerate(rows):
        params = kernel_params(r)
        u = r.get("u", ["-"] * len(params))
        by_name = {_param_name(p): p for p in params}
        exprs = []
        # a deref'd count's own pointer first
        order = sorted(range(len(params)), key=lambda k: bool(re.search(r"[:#]\*", u[k])))
        for k in order:
            e = _u_expr(u[k], params[k], by_name)
            if e:
                exprs.append(e)
        if not exprs:
            continue
        checked.append(i)
        o.append("// %d %s" % (i, r["name"]))
        regs = " + ".join("U_REGS(%s)" % _ptype(p) for p in params)
        o.append("_Static_assert(%s <= 7, \"%s's registers\");" % (regs, r["name"]))
        o.append("static int u_check_%d(const moy_u_space_t *u, %s) {" % (i, ", ".join(params)))
        o.append("    return %s;" % "\n        && ".join(exprs))
        o.append("}")
        o.append("")
    o.append("int moy_os_check(const moy_u_space_t *u, uint32_t row, const uint32_t *a) {")
    o.append("    u_check_fn *fn;")
    o.append("    switch (row) {")
    for i in checked:
        o.append("    case %d: fn = (u_check_fn *)(void *)u_check_%d; break;" % (i, i))
    o += ["    default: return 1;", "    }",
          "    return fn(u, a[0], a[1], a[2], a[3], a[4], a[5], a[6]);", "}", ""]
    return "\n".join(o)


def outputs(src=None):
    """[(path, text)] for every generated file."""
    if src is None:
        src = load()
    check(src)
    table = elf(src)
    return [(KERNEL_H, gen_kernel_h(table)), (ROWS_H, gen_rows_h(table)),
            (KERNEL_C, gen_kernel_c(table)), (SDK_H, gen_sdk_h(table)),
            (STUBS_C, gen_stubs_c(table)), (CHECK_C, gen_check_c(table)),
            (APP_H, gen_app_h(src)), (WASM_H, gen_wasm_h(src)), (UI_H, gen_ui_h(src)),
            (LUA_H, gen_lua_h(src)), (WASI_OS_H, gen_wasi_h(src)),
            (APP_CONTEXT, gen_app_context(src)), (APP_PERMS, gen_app_perms(src)),
            (SHELL_SERVERS, gen_door_args(src)), (SCHEDULER, gen_calls(src)),
            (LUA_EXT, gen_lua_ext(src)), (APP_DOC, gen_doc(src))]


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--check", action="store_true",
                    help="write nothing; exit 1 when a generated file is stale")
    args = ap.parse_args(argv)
    stale = 0
    for path, text in outputs():
        try:
            with open(path) as f:
                have = f.read()
        except OSError:
            have = None
        if have == text:
            continue
        if args.check:
            print("%s is stale: run tools/gen_os_table.py" % os.path.relpath(path, ROOT))
            stale = 1
            continue
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write(text)
        print("wrote %s" % os.path.relpath(path, ROOT))
    return stale


if __name__ == "__main__":
    sys.exit(main())
