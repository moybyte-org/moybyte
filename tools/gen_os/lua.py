"""The names a Lua run and a Lua service hold."""


from .table import door_rows, ident, py_twin, role_of, upper, verb_of
from .cproto import HEAD


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


def gen_lua_h(src):
    o = [HEAD,
         "// The names a Lua program holds (native/moy_app/moy_app_lua.h): a run's role\n"
         "// binding, each X(role, its table or NULL for a global, its name, the C\n"
         "// function moy_app_lua.c serves it with, the table row it calls, 1 for a\n"
         "// read), opened for the roles the run's permissions name, a read for the\n"
         "// roles its grant holds; and the console calls a Lua service's\n"
         "// `moybyte` table holds (native/moycore/moycore_svc.c), each answered by\n"
         "// the console's Python as the service's process.\n",
         "#ifndef MOY_APP_LUA_ROWS_H", "#define MOY_APP_LUA_ROWS_H", "",
         "#define MOY_APP_LUA_NAMES(X) \\"]
    for role, tbl, fn, cfn, row, read in lua_names(src):
        o.append('    X(MOY_ROLE_%s, %s, "%s", %s, MOY_T_%s, %d) \\' % (
            upper(role), '"%s"' % tbl if tbl else "NULL", fn, cfn, upper(row), read))
    o += ["", "#define MOY_APP_LUA_CALLS(X) \\"]
    for v in lua_calls(src):
        o.append('    X("%s") \\' % v)
    o += ["", "// The rows a Lua name calls that run where they are called (how \"here\"),",
          "// on a Lua service's own task too, hopped only where they answer NEEDS_VM.",
          "#define MOY_APP_LUA_HERE(X) \\"]
    here = []
    for _role, _tbl, _fn, _c, row, _read in lua_names(src):
        if row not in here and next(r for r in door_rows(src) if r["name"] == row).get("how") == "here":
            here.append(row)
            o.append("    X(MOY_T_%s) \\" % upper(row))
    o += ["", "#endif // MOY_APP_LUA_ROWS_H", ""]
    return "\n".join(o)
