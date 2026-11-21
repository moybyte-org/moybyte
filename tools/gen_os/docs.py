"""docs/app_api_v1.md's permission, call and role tables."""


from .table import (DIRECT_ROLES, APP_DOC, WASI_OS, ctx_roles, door_rows, elf, ident, perm_tiers,
    perms_of, role_of, verb_of, wasi_name)
from .cproto import parse
from .py import MD_BEGIN, MD_END, MD_PERMS_BEGIN, MD_ROLES_BEGIN, splice


def _cell(names):
    return ", ".join("`%s`" % n for n in names) if names else ""


def _perm_name(p):
    """A permission as a manifest spells it: `files` also as `files:<folder>`,
    a lease only as `lease:<resource>`."""
    if p["grants"] == "lease":
        return "`%s:<resource>`" % p["perm"]
    if p["grants"] == "role:files":
        return "`%s`, `%s:<folder>`" % (p["perm"], p["perm"])
    return "`%s`" % p["perm"]


def doc_perms(src):
    """The guide's permission table: each permission, what the app holds and
    the tiers that may hold it, consecutive rows that say the same sharing a
    line."""
    lines = []
    for p in src["permissions"]:
        tiers = ", ".join(perm_tiers(p, src))
        if lines and lines[-1][1:] == [p["doc"], tiers]:
            lines[-1][0] += ", " + _perm_name(p)
        else:
            lines.append([_perm_name(p), p["doc"], tiers])
    return (["| permission | what the app holds | tiers |", "|---|---|---|"]
            + ["| %s | %s | %s |" % tuple(x) for x in lines])


def gen_doc(src):
    """The guide's three regions: the permission table, the call table, and
    the shipped apps' role table."""
    roles = ["| role | what it is | granted by | verbs |", "|---|---|---|---|"]
    for x in src["roles"]:
        if not x.get("ctx"):
            continue
        verbs = ", ".join("`%s()`" % verb_of(r) for r in door_rows(src)
                          if role_of(r) == x["role"])
        perm = next((p["perm"] for p in src["permissions"]
                     if p["grants"] == "role:" + x["role"]), None)
        roles.append("| `ctx.%s` | %s | %s | %s |" % (x["role"], x.get("doc", ""),
                                                     "`%s`" % perm if perm else "the shell",
                                                     verbs))
    o = ["| call | permission | Python | Lua | wasm (`moybyte.app`; a WASI program's `moybyte.os`) | C (ELF) |",
         "|---|---|---|---|---|---|"]
    table = elf(src)
    cnames = {}
    for e in table["rows"]:
        if e["door"] is not None:
            cnames[e["src"]["name"]] = e["name"]
    for r in src["rows"]:
        if r.get("kind") == "posix" or role_of(r) in DIRECT_ROLES:
            continue
        if r.get("retired"):
            continue
        cname = cnames.get(r["name"]) or (parse(r["c"])[1] if "c" in r else None)
        imported = ([ident(r["name"])] if r.get("wasm") else []) + (
            ["%s.%s" % (WASI_OS, wasi_name(r))] if r.get("wasi") else [])
        if not (cname or imported or r.get("lua")):
            continue
        perms = perms_of(r, src)
        py = list(r.get("py") or ())
        if role_of(r) in ctx_roles(src):
            py = ["ctx.%s" % r["name"]] + py
        o.append("| `%s` | %s | %s | %s | %s | %s |" % (
            r["name"], _cell(perms) or "none", _cell(py), _cell(r.get("lua")),
            _cell(imported), _cell([cname] if cname else [])))
    text = splice(APP_DOC, "\n".join(doc_perms(src)) + "\n", MD_PERMS_BEGIN, MD_END)
    text = splice(APP_DOC, "\n".join(o) + "\n", MD_BEGIN, MD_END, text)
    return splice(APP_DOC, "\n".join(roles) + "\n", MD_ROLES_BEGIN, MD_END, text)
