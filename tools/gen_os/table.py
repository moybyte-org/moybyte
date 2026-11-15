"""The source, native/os_calls.json: its paths, its views (roles, verbs,
permissions, tiers) and its rules (check)."""

import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from board_config import runtime_file  # noqa: E402

from . import TableError  # noqa: E402
from .cproto import param_name, parse  # noqa: E402


ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SOURCE = os.path.join(ROOT, "native", "os_calls.json")
EVQ_H = os.path.join(ROOT, "native", "moy_kernel", "moy_evq.h")

KERNEL_H = os.path.join(ROOT, "native", "moy_elf", "moy_os_table.h")
ROWS_H = os.path.join(ROOT, "native", "moy_elf", "moy_os_rows.h")
KERNEL_C = os.path.join(ROOT, "native", "moy_elf", "moy_os_table.c")
SDK_H = os.path.join(ROOT, "sdk", "include", "moybyte", "os.h")
STUBS_C = os.path.join(ROOT, "sdk", "src", "moyos_stubs.c")
CHECK_C = os.path.join(ROOT, "native", "moy_elf", "moy_os_check.c")
WILD_H = os.path.join(ROOT, "tests", "fixtures", "elf", "elf_wild.moy", "src", "rows.h")
APP_H = os.path.join(ROOT, "native", "moy_app", "moy_app_rows.h")
WASM_H = os.path.join(ROOT, "native", "moy_app", "moy_app_wasm_rows.h")
UI_H = os.path.join(ROOT, "native", "moy_ui", "moy_ui_wasm_rows.h")
LUA_H = os.path.join(ROOT, "native", "moy_app", "moy_app_lua_rows.h")
ERR_H = os.path.join(ROOT, "native", "moy_app", "moy_app_err.h")
FACE_ERRORS = os.path.join(ROOT, "runtime", "system", "face_errors.py")
EVQ_CODES_H = os.path.join(ROOT, "native", "moy_kernel", "moy_evq_codes.h")
WASI_OS_H = os.path.join(ROOT, "native", "moy_wasm", "moy_wasi_os_rows.h")
# The module a WASI program imports a "wasi" row from.
WASI_OS = "moybyte.os"
APP_CONTEXT = str(runtime_file("app_context.py"))
APP_PERMS = str(runtime_file("app_perms.py"))
SHELL_SERVERS = str(runtime_file("shell_servers.py"))
SCHEDULER = str(runtime_file("scheduler.py"))
LUA_EXT = str(runtime_file("lua_ext.py"))
APP_DOC = os.path.join(ROOT, "docs", "app_api_v1.md")
PLUGIN_TABLE = os.path.join(ROOT, "sdk", "moybyte_sdk", "os_table.py")
APP_WASM_SDK_H = os.path.join(ROOT, "sdk", "include", "moybyte", "app.h")
UI_SDK_H = os.path.join(ROOT, "sdk", "include", "moybyte", "ui.h")
STUBS = os.path.join(ROOT, "sdk", "moybyte_sdk", "stubs")
LUA_STUB = os.path.join(STUBS, "moybyte_os.lua")
PY_STUBS = (os.path.join(STUBS, "__builtins__.pyi"), os.path.join(STUBS, "moybyte.pyi"))

# The rows that run the SDK's C library's exit path (__moy_exit, sdk/libc/)
# before the kernel's: C's exit, never _exit.
EXIT_HOOK = ("exit",)

TIERS = ("python", "lua", "wasm", "elf")
# moy-spec's tiers: the game verbs a "spec" permission's floor names are
# served on these (the cart verbs, which this source does not hold).
SPEC_TIERS = ("python", "lua", "wasm")

ROW_FIELDS = {"name", "kind", "server", "perm", "grant", "in", "ret", "how", "door", "wasm", "wasi", "elf",
              "lua", "lua_own", "py", "py_face", "hdr", "c", "va", "glibc", "fail", "noreturn", "retired",
              "u", "example", "lua_ret", "lua_in", "lua_enum", "out_shape", "object"}
ELF_FIELDS = {"hdr", "c", "va", "glibc", "fail", "noreturn", "retired", "u"}
# The Python faces a row's Lua names stand beside where Python holds its
# service as objects, not the row's verbs (a row's py_face): the device faces
# (moybyte.lease, moybyte.lora, ...) and the status item (moybyte.status).
PY_FACES = ("devices", "status")
PERM_FIELDS = {"perm", "grants", "prompt", "needs", "py", "doc", "tiers", "spec"}
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
HOWS = ("hop", "seq", "here", "direct")
FLOOR_ROLE = "floor"


def load(path=SOURCE):
    with open(path) as f:
        return json.load(f)


def dump(src):
    """The source's text as it is kept: one row a line."""
    o = ["{", '  "about": %s,' % json.dumps(src["about"]),
         '  "version": %d,' % src["version"]]
    keys = [k for k in ("roles", "rows", "permissions", "errors", "evq_errors") if k in src]
    for key in keys:
        items = src[key]
        o.append('  "%s": [' % key)
        o += ["    %s%s" % (json.dumps(x), "," if i < len(items) - 1 else "")
              for i, x in enumerate(items)]
        o.append("  ]" + ("," if key != keys[-1] else ""))
    o += ["}", ""]
    return "\n".join(o)


def is_door(r):
    """A row of the role table, which the ROLE door names by its index: a row
    a role's server serves, or any row whose fields the door's Python half
    decodes (a kernel or Python row a compiled app imports)."""
    s = r.get("server")
    return s in DOOR_SERVERS or (s or "").startswith("proc:") or bool(r.get("door"))


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


def cap_packed(r):
    """Whether a row's answer capacity crosses as a number field: a row the
    door's Python half may serve (not a C row's alone)."""
    return r.get("server") != "c" or bool(r.get("door"))


def py_twin(r):
    """Whether a C row run where it is called has the moybyte module's verb
    as its Python half: the kernel's answer on a compiled program's own
    task, and, for a door row, the console call where the kernel has none
    (the door's). A Lua program, whose calls are the VM's already, holds the
    console call itself. A Lua tier's own form of the row (lua_own) is no
    twin."""
    return (r.get("server") == "c" and r.get("how") == "here" and not r.get("lua_own")
            and bool(r.get("py")) and role_of(r) == "moybyte")


def wasi_rows(src):
    """The rows a WASI program imports from moybyte.os."""
    return [r for r in src["rows"] if r.get("wasi")]


def wasi_call(r):
    """Whether a "wasi" row is a console call: posted on the VM's queue as a
    JSON array of texts and numbers and answered by the scheduler's
    `_CALLS`, not served by the kernel or in C: the moybyte module's own
    verb, so a row Python holds by name, over texts and numbers. A row it
    does not hold (the device handles'), or one that carries bytes or an
    answer's buffer (share, item_read), is the door's Python half's."""
    return ("c" not in r and role_of(r) == "moybyte" and r.get("server") != "c"
            and bool(r.get("py")) and all(t in ("int", "str", "str?") for _n, t in params(r)))


def wasi_role(r):
    """Whether a "wasi" row is an app row: its role table row run for the
    program's app run through the ROLE door, as a C program's is: any row
    the kernel does not serve that is no console call (a row served in C,
    and a door row, which the door's Python half serves as the program's
    process: the shell's servers' and the console calls no console call
    carries)."""
    return "c" not in r and not wasi_call(r)


def wasi_name(r):
    """A "wasi" row's moybyte.os import name: a moybyte row's verb, a kernel
    row named for its C function that name less its moy_, any other
    <role>_<verb>."""
    if "." not in r["name"]:
        return r["name"][4:] if r["name"].startswith("moy_") else r["name"]
    return verb_of(r) if role_of(r) in (None, "moybyte") else "%s_%s" % (role_of(r), verb_of(r))


def console_calls(src):
    """The console calls the VM's queue answers (the scheduler's `_CALLS`):
    {"moybyte.<verb>": the moybyte module's function}. A call row is the verb
    itself; a Python row a compiled program imports is the verb's message
    half (`<verb>_message`), whose wake posts the program a message."""
    out = {}
    for r in door_rows(src):
        if role_of(r) != "moybyte":
            continue
        if (r.get("server") == "call" or py_twin(r)) and (
                r.get("lua") or (r.get("wasi") and wasi_call(r))):
            out[r["name"]] = verb_of(r)
        elif r.get("server") == "python" and r.get("wasi") and wasi_call(r):
            out[r["name"]] = verb_of(r) + "_message"
    return out


def wasi_c(r):
    """A "wasi" row's C prototype: the kernel's own, or a console call's as
    the OS table names it (moy_<verb>)."""
    if "c" in r:
        return r["c"]
    return "int32_t %s(%s)" % (c_name(r), ", ".join(c_params(r)) or "void")


def wasi_params(r):
    """[(name, kind, length's name)] of a "wasi" row's import: "s" a text,
    "o" a buffer the call writes, "b" bytes (each a pointer and a length in
    the import), "p" a word the call writes (a pointer), "i" a number; a
    kernel row's from its C prototype, where a void pointer and the size_t
    after it are one buffer ("b" or "o", its length's name third), any
    other's from its in (None third)."""
    if "c" not in r:
        m = {"str": "s", "str?": "s", "int": "i", "out": "o", "bytes": "b"}
        return [(n, m[t], None) for n, t in params(r)]
    out = []
    ps = parse(wasi_c(r))[2]
    i = 0
    while i < len(ps):
        p, name = ps[i], param_name(ps[i])
        if p.startswith(("const void *", "void *")):
            out.append((name, "b" if p.startswith("const ") else "o", param_name(ps[i + 1])))
            i += 2
            continue
        kind = "s" if p.startswith("const char *") else "p" if p.startswith("int *") else "i"
        out.append((name, kind, None))
        i += 1
    return out


def wasi_sig(r):
    """The WAMR signature string of a row's moybyte.os import."""
    m = {"i": "i", "p": "*"}
    return "(%s)i" % "".join(m.get(k, "*~") for _n, k, _l in wasi_params(r))


def door_kinds(r):
    """The row's fields as the door decodes them in Python: `s` text, `o`
    text where empty is None, `i` a number, `c` a cart, `b` bytes; an answer's
    buffer (out) is handed over as its capacity, a number."""
    m = {"str": "s", "str?": "o", "int": "i", "cart": "c", "bytes": "b", "out": "i"}
    return "".join(m[t] for _n, t in params(r))


def perms_of(r, src):
    """The permissions a row answers under: its role's grant, unless the row's
    own `grant` is the floor (a read every app is given, as the theme's are),
    and the row's own `perm` (a gate its server passes, as the nav verbs pass
    the front's)."""
    out = []
    role = role_of(r)
    if (role is not None and r.get("grant") != FLOOR_ROLE
            and grant_of(src, role) not in ("never", FLOOR_ROLE)):
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
    if r.get("wasm") or r.get("wasi"):
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
        if "grant" in r and (r["grant"] != FLOOR_ROLE or r.get("perm") is not None
                             or role_of(r) is None
                             or grant_of(src, role_of(r)) in ("never", FLOOR_ROLE)):
            raise TableError("%s: a row's own grant is the floor, under a role a "
                             "permission grants, and gates nothing" % name)
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
        if r.get("how") == "here" and r.get("server") != "c":
            raise TableError("%s: a row run where it is called is served in C" % name)
        outs = [n for n, t in params(r) if t == "out"]
        if (r.get("ret") == "len") != bool(outs) and r.get("how") != "direct":
            raise TableError("%s: a len answer is what an out parameter holds" % name)
        if r.get("wasm") not in (None, True) or r.get("wasi") not in (None, True):
            raise TableError("%s's wasm or wasi: %r, %r" % (name, r.get("wasm"), r.get("wasi")))
        if r.get("wasi"):
            kernel = "c" in r and isinstance(r.get("elf"), int) and kind == "moybyte"
            app = "c" not in r and bool(r.get("wasm")) and r.get("how") != "direct" and (
                r.get("server") == "c" or bool(r.get("door")))
            if not (kernel or app):
                raise TableError("%s: a WASI import is a moybyte row the kernel serves, or a "
                                 "row a compiled app imports served in C or by the door" % name)
            ps = parse(wasi_c(r))[2]
            if kernel and (parse(wasi_c(r))[0] not in ("int", "int32_t", "uint32_t") or not all(
                    p.startswith(("const char *", "int ", "int32_t ", "uint32_t ", "int *"))
                    or (p.startswith("size_t ") and i > 0
                        and ps[i - 1].startswith(("const void *", "void *")))
                    or (p.startswith(("const void *", "void *")) and i + 1 < len(ps)
                        and ps[i + 1].startswith("size_t "))
                    for i, p in enumerate(ps))):
                raise TableError("%s: a WASI import takes texts, buffers with their lengths, "
                                 "words it writes and numbers, and answers a number" % name)
        if r.get("wasm"):
            if not ("in" in r and "ret" in r and "how" in r):
                raise TableError("%s: an import needs in, ret and how" % name)
            if (r["how"] == "direct") != (role_of(r) == "ui"):
                raise TableError("%s: a direct import is the toolkit's" % name)
            if r["how"] != "direct" and (len(outs) > 1 or (outs and params(r)[-1][1] != "out")):
                raise TableError("%s: the adapter writes one answer, last" % name)
        e = r.get("elf")
        if e is not None and not (isinstance(e, int) or e == "linked"):
            raise TableError("%s's elf: %r" % (name, e))
        if isinstance(e, int) and kind == "moybyte" and "c" not in r:
            if not is_door(r) or "in" not in r or "ret" not in r:
                raise TableError("%s: an ELF row the door serves needs in and ret" % name)
            if len(outs) > 1 or (outs and params(r)[-1][1] != "out"):
                raise TableError("%s: one answer, last" % name)
            if [n for n, t in params(r) if t == "cart"]:
                raise TableError("%s: a cart has no C form" % name)
        if r.get("door"):
            if r.get("server") == "c" and r.get("how") != "here":
                raise TableError("%s: the door's Python half serves a C row only where it "
                                 "runs where it is called and passes what C does not own"
                                 % name)
            if "in" not in r or len(outs) > 1 or (outs and params(r)[-1][1] != "out"):
                raise TableError("%s: a door row's fields are its in, one answer last" % name)
        lua, py = r.get("lua") or [], list(r.get("py") or [])
        if role_of(r) in ctx_roles(src):
            py.append(r["name"])
        if r.get("py_face", "devices") not in PY_FACES:
            raise TableError("%s: py_face names one of %s or is absent" % (name, PY_FACES))
        if not set(lua) <= set(py) and not r.get("py_face"):
            raise TableError("%s: Lua's names are Python's, verbatim: %s" % (
                name, sorted(set(lua) - set(py))))
        for p in perms_of(r, src):
            if p not in perm_names:
                raise TableError("%s's perm %r is no permission" % (name, p))
        if "example" in r and len(r["example"]) != sum(1 for _n, t in params(r) if t != "out"):
            raise TableError("%s: an example is one argument a parameter that is no out" % name)
    for p in src["permissions"]:
        if set(p) - PERM_FIELDS:
            raise TableError("%s's fields: %s" % (p["perm"], sorted(p)))
        if not isinstance(p.get("doc"), str) or not p["doc"]:
            raise TableError("%s: no doc" % p["perm"])
        kind = p["grants"].split(":")[0]
        if "tiers" in p and kind not in ("full_access", "boot"):
            raise TableError("%s: only what no row serves states its tiers" % p["perm"])
        if kind in ("full_access", "boot") and "tiers" not in p:
            raise TableError("%s: no row serves it, so it states its tiers" % p["perm"])
        if not perm_tiers(p, src):
            raise TableError("%s: no tier serves it" % p["perm"])
    for r in door_rows(src):
        if role_of(r) == "ui" and r.get("elf") != "linked":
            raise TableError("%s: the toolkit's rows are linked into an ELF module" % r["name"])
    _check_elf_table(elf(src))


def _is_pointer(p):
    return "*" in p or "[" in p


def _check_u(i, r):
    """Every pointer a row takes has a rule, and every rule a parameter."""
    params = kernel_params(r)
    u = r.get("u", ["-"] * len(params))
    if len(u) != len(params):
        raise TableError("row %d's u has %d tokens for %d parameters" % (i, len(u), len(params)))
    names = [param_name(p) for p in params]
    for p, tok in zip(params, u):
        m = U_TOKEN.match(tok)
        if not m:
            raise TableError("row %d: %r is not an argument rule" % (i, tok))
        if _is_pointer(p) != (tok != "-"):
            raise TableError("row %d: %s %s a rule" % (
                i, param_name(p), "needs" if _is_pointer(p) else "is a value and takes no"))
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
