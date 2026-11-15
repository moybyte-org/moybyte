"""The names a Lua run and a Lua service hold."""


from . import TableError
from .table import cap_packed, door_rows, ident, params, py_twin, role_of, upper, verb_of
from .cproto import HEAD

# What a Lua name answers (native/os_calls.json's lua_ret), each an LR_* the
# one dispatcher in native/moy_app/moy_app_lua.c serves.
LUA_RETS = ("value", "bool", "nil", "value_nil", "nil_absent", "value_err",
            "value_err_absent_nil", "text_err", "json_or", "taken", "enum", "value_name",
            "raw", "hand")
OUT_SHAPES = ("names",)


def lua_ret(r, name):
    """What Lua name `name` of row `r` answers."""
    ret = r.get("lua_ret", "value")
    if isinstance(ret, dict):
        ret = ret.get(name, "value")
    return ret


def lua_fields(r):
    """(the fields a Lua name packs, one character each, and their defaults):
    k the grant's files kind, s a text, o an optional text (nil is empty), b
    bytes, i a number, d an optional number, j any value JSON-encoded, c an
    out's capacity, an optional number packed as its field."""
    sig, defs = "", []
    over = r.get("lua_in", {})
    for n, t in params(r):
        how = over.get(n)
        if t == "out":
            if cap_packed(r):
                if not isinstance(how, int):
                    raise TableError("%s: a packed out takes its capacity's default" % r["name"])
                sig += "c"
                defs.append(how)
            continue
        if role_of(r) == "files" and n == "kind":
            sig += "k"
        elif how == "json" and t == "str":
            sig += "j"
        elif isinstance(how, int) and t == "int":
            sig += "d"
            defs.append(how)
        elif how is not None:
            raise TableError("%s: lua_in %s is json or a default" % (r["name"], n))
        else:
            sig += {"int": "i", "str": "s", "str?": "o", "bytes": "b"}.get(t, "?")
    if "?" in sig or len(defs) > 2:
        raise TableError("%s: a Lua name takes texts, bytes, numbers, two defaults" % r["name"])
    return sig, defs + [0] * (2 - len(defs))


def check_lua(src):
    """The door rows' Lua fields; a ui row's are the toolkit's Lua face's (gen_os
    ui.py checks them)."""
    for r in src["rows"]:
        if r["name"].startswith("ui."):
            continue
        rets = r.get("lua_ret", {})
        rets = list(rets.values()) if isinstance(rets, dict) else [rets]
        if any(x not in LUA_RETS for x in rets) or r.get("out_shape", "names") not in OUT_SHAPES:
            raise TableError("%s: lua_ret is one of %s" % (r["name"], LUA_RETS))
        if ("lua_enum" in r) != ("enum" in rets):
            raise TableError("%s: an enum answer names its values" % r["name"])
        if set(r) & {"lua_ret", "lua_in", "lua_enum", "out_shape"} and not r.get("lua"):
            raise TableError("%s: Lua's fields on a row Lua does not hold" % r["name"])


def lua_names(src):
    """[(role, table or None, name, C function, row, read)] of the names a Lua
    run's role binding holds, in the source's order; `read` where the row is
    a read under the seqlock, held where the run's grant holds its role. A
    moybyte row served in C is a function of the run's `moybyte` table."""
    out = []
    for r in door_rows(src):
        role = role_of(r)
        if role == "moybyte" and (r.get("server") != "c" or r.get("lua_own") or py_twin(r)):
            continue
        for n in r.get("lua") or ():
            tbl, _, fn = n.rpartition(".")
            out.append((role, tbl or None, fn, "l_" + ident(n), r["name"],
                        r.get("how") == "seq"))
    return out


def lua_calls(src):
    """The console calls a Lua service holds as moybyte.<verb>."""
    return [verb_of(r) for r in door_rows(src)
            if role_of(r) == "moybyte" and (r.get("server") == "call" or py_twin(r))
            and r.get("lua")]


def lua_run_calls(src):
    """The console calls a Lua app's run holds as moybyte.<verb> (runtime/run/
    lua_ext.py): a service's, each gated where its server is, as a Python
    app's."""
    return lua_calls(src)


def lua_own(src):
    """The moybyte.<verb> names every Lua program holds that are no console
    call: its wakes and the log, each a service's own C
    (native/moycore/moycore_svc.c) and an app run's prelude over the moybyte
    module (runtime/run/lua_ext.py). A row served in C is the run's role
    binding's (lua_names), but a lua_own one's: Lua's name for it is its own
    form (a function where the C row posts a message)."""
    calls = set(lua_calls(src))
    out = []
    for r in src["rows"]:
        if r.get("server") == "c" and not r.get("lua_own"):
            continue
        for n in r.get("lua") or ():
            if n.startswith("moybyte.") and n[8:] not in calls and n[8:] not in out:
                out.append(n[8:])
    return out


def _enum_text(names):
    """A C literal of an enum answer's names, split on |, a null's empty."""
    if any(not (x or "w").isidentifier() for x in names):
        raise TableError("an enum answer's names are words: %s" % names)
    return '"%s"' % "|".join(x or "" for x in names)


def gen_lua_h(src):
    check_lua(src)
    rows = {r["name"]: r for r in door_rows(src)}
    o = [HEAD,
         "// The names a Lua program holds (native/moy_app/moy_app_lua.h): a run's role\n"
         "// binding, opened for the roles the run's permissions name, a read (how\n"
         "// \"seq\") for the roles its grant holds; and the console calls a Lua\n"
         "// service's `moybyte` table holds (native/moycore/moycore_svc.c), each\n"
         "// answered by the console's Python as the service's process.\n"
         "//\n"
         "// MOY_APP_LUA_NAMES(X) expands X(role, its table or NULL for a global, its\n"
         "// name, 1 for a read, its own C function (LR_HAND's) or NULL for the one\n"
         "// dispatcher's, the table row it calls, 1 where the row runs where it\n"
         "// is called (how \"here\", on a Lua service's own task too, hopped only where\n"
         "// it answers NEEDS_VM), what it answers (LR_*), the answer's shape (i a\n"
         "// number, t an out's bytes, n its NUL-separated names), its fields (gen_os\n"
         "// lua.py's lua_fields), two defaults, and an enum answer's names, split\n"
         "// on |, a null's empty).\n",
         "#ifndef MOY_APP_LUA_ROWS_H", "#define MOY_APP_LUA_ROWS_H", "",
         "enum {"]
    o += ["    LR_%s%s," % (x.upper(), " = 0" if i == 0 else "") for i, x in enumerate(LUA_RETS)]
    o += ["};", "", "#define MOY_APP_LUA_NAMES(X) \\"]
    for role, tbl, fn, cfn, row, read in lua_names(src):
        r = rows[row]
        name = "%s.%s" % (tbl, fn) if tbl else fn
        ret = lua_ret(r, name)
        sig, defs = lua_fields(r)
        shape = "i" if r.get("ret") != "len" else "n" if r.get("out_shape") == "names" else "t"
        enum = r.get("lua_enum")
        enum = "NULL" if enum is None else _enum_text(enum)
        o.append('    X(MOY_ROLE_%s, %s, "%s", %d, %s, MOY_T_%s, %d, LR_%s, \'%s\', "%s", %d, %d, %s) \\' % (
            upper(role), '"%s"' % tbl if tbl else "NULL", fn, read,
            cfn if ret == "hand" else "NULL", upper(row),
            r.get("how") == "here", ret.upper(), shape, sig, defs[0], defs[1], enum))
    o += ["", "#define MOY_APP_LUA_CALLS(X) \\"]
    for v in lua_calls(src):
        o.append('    X("%s") \\' % v)
    o += ["", "#endif // MOY_APP_LUA_ROWS_H", ""]
    return "\n".join(o)
