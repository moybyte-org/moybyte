"""The moy-app plugin: its table, a wasm app's headers, the Lua and Python stubs."""


from . import header
from .table import c_name, ident, params, perms_of, role_of, verb_of, wasi_name, wasi_rows
from .cproto import HEAD, c_decl, parse
from .app import ui_c_params, wasm_rows


# A name a row hands a tier that is a moy-spec verb already (print draws, the
# floor's): the plugin neither stubs it nor infers a permission from it.
SPEC_NAMES = ("print",)
PLUGIN_TIERS = ("python", "lua", "wasm", "elf")


def _plugin_perms(r, src):
    return tuple(perms_of(r, src))


def _named(src, field):
    """{name: the permissions it answers under} of the names `field` (py or
    lua) lists, rows and permissions alike; a name more than one row hands
    out answers under any of theirs."""
    out = {}
    for r in src["rows"]:
        for n in r.get(field) or ():
            if n in SPEC_NAMES:
                continue
            have = out.setdefault(n, [])
            have += [p for p in _plugin_perms(r, src) if p not in have]
    if field == "py":
        for p in src["permissions"]:
            for n in p["py"]:
                have = out.setdefault(n, [])
                if p["perm"] not in have:
                    have.append(p["perm"])
    return {n: tuple(ps) for n, ps in out.items()}


def plugin_names(src):
    """What `moy app check` infers a cart's permissions from, per tier: each
    name a module imports or a script reaches, with the permissions it
    answers under (any one of them; none for an ungated name)."""
    elf_rows = {}
    for r in src["rows"]:
        o = r.get("elf")
        if isinstance(o, int) and not r.get("retired"):
            elf_rows[o] = (parse(r["c"])[1] if "c" in r else c_name(r), _plugin_perms(r, src))
    return {"wasm_app": {ident(r["name"]): _plugin_perms(r, src) for r in wasm_rows(src)},
            "wasm_os": {wasi_name(r): _plugin_perms(r, src) for r in wasi_rows(src)},
            "elf": elf_rows,
            "retired": tuple(sorted(r["elf"] for r in src["rows"]
                                    if isinstance(r.get("elf"), int) and r.get("retired"))),
            "py": _named(src, "py"), "lua": _named(src, "lua")}


def _py_dict(name, d, doc):
    o = ["# " + doc, "%s = {" % name]
    for k in sorted(d, key=lambda k: (isinstance(k, str), k)):
        o.append("    %r: %r," % (k, d[k]))
    return o + ["}", ""]


def gen_plugin_table(src):
    t = plugin_names(src)
    o = [header("native/os_calls.json", "#"),
         '"""The table `moy app` reads (sdk/moybyte_sdk/cli.py): what a module or a script',
         "reaches on each tier, with the permissions each answers under (any one of",
         'them), from native/os_calls.json."""', "",
         "VERSION = %d" % src["version"],
         "SPEC_NAMES = %r" % (SPEC_NAMES,), ""]
    o += _py_dict("WASM_APP", t["wasm_app"], 'a wasm app\'s "moybyte.app" imports')
    o += _py_dict("WASM_OS", t["wasm_os"], 'a WASI program\'s "moybyte.os" imports')
    o += _py_dict("ELF", t["elf"], "a native module's rows by ordinal (its .moy.rows): "
                  "(its C name, permissions)")
    o += ["# the ordinals of the retired rows, which no module built now calls",
          "RETIRED = %r" % (t["retired"],), ""]
    o += _py_dict("PY", t["py"], "the names a Python app reaches")
    o += _py_dict("LUA", t["lua"], "the names a Lua app reaches")
    return "\n".join(o)


def _app_c_params(r):
    out = []
    for n, t in params(r):
        if t == "int":
            out.append("int32_t %s" % n)
        elif t == "out":
            out += ["void *%s" % n, "uint32_t %s_cap" % n]
        elif t == "bytes":
            out += ["const void *%s" % n, "uint32_t %s_n" % n]
        else:
            out += ["const char *%s" % n, "uint32_t %s_n" % n]
    return out or ["void"]


def gen_app_wasm_h(src):
    o = [HEAD,
         "// A compiled app's moybyte.app imports (docs/app_api_v1.md section 10), for\n"
         "// a C app built for wasm (tools/wasm_cart.py puts this folder on its include\n"
         "// path): each row of the role table a compiled app imports, under its\n"
         "// import name. A text or bytes is a pointer and its length, never\n"
         "// NUL-terminated; an answer is written into the buffer given with its\n"
         "// capacity. Each answers its number, or a negative code. A row is held\n"
         "// where the app's permissions grant it (the comment names them).\n",
         "#ifndef MOYBYTE_APP_H", "#define MOYBYTE_APP_H", "",
         "#include <stdint.h>", "",
         "#ifdef __cplusplus", 'extern "C" {', "#endif", "",
         "#define MOYBYTE_APP_VERSION %d" % src["version"], "",
         '#define MOYBYTE_APP_IMPORT(name) __attribute__((import_module("moybyte.app"), '
         "import_name(#name)))", ""]
    for r in wasm_rows(src):
        perms = perms_of(r, src)
        o.append("// %s%s" % (r["name"], (": " + ", ".join(perms)) if perms else ""))
        o += c_decl("int32_t %s" % ident(r["name"]), _app_c_params(r),
                     " MOYBYTE_APP_IMPORT(%s);" % ident(r["name"]))
    o += ["", "#ifdef __cplusplus", "}", "#endif", "", "#endif // MOYBYTE_APP_H", ""]
    return "\n".join(o)


def gen_sdk_ui_h(src):
    rows = [r for r in wasm_rows(src) if role_of(r) == "ui"]
    o = [HEAD,
         "// The public UI API for a native app (docs/ui_api_2026-10.md): the toolkit\n"
         "// linked into the module (sdk/src/moyui_link.c, native/moy_ui), drawing on\n"
         "// the app's own RGB565 screen. Each ui row is app.h's, the same call a\n"
         "// compiled app imports, answered inline by the copy's RUN face\n"
         "// (native/moy_ui/moy_ui_run.h). A frame is moy_ui_link_frame, the rows,\n"
         "// then moy_ui_link_show:\n"
         "//\n"
         "//     moy_frame_t f;\n"
         "//     int32_t on = 1;\n"
         "//     while (moy_ui_link_frame(&f)) {\n"
         "//         ui_page(\"Settings\", 8, NULL, 0, NULL, 0);\n"
         "//         on = ui_toggle(\"Sound\", 5, on, NULL, 0);\n"
         "//         moy_ui_link_show();\n"
         "//     }\n",
         "#ifndef MOYBYTE_UI_H", "#define MOYBYTE_UI_H", "",
         "#include <stdint.h>", "",
         '#include "moybyte/frame.h"', "",
         "#ifdef __cplusplus", 'extern "C" {', "#endif", "",
         "// The copy opened on the program's screen (moy_screen) with the console's",
         "// theme: 0, or -1 for no screen or no memory. The first frame opens it.",
         "int moy_ui_link_open(void);",
         "// The console's next frame into *f, its input the rows' and the theme",
         "// pulled where it moved, after the app's `_layout` where the surface it",
         "// lays out for moved: 1, or 0 once the app is stopped.",
         "int moy_ui_link_frame(moy_frame_t *f);",
         "// A responsive app's layout (docs/app_api_v1.md section 3): defined, the",
         "// app draws on its window's or the whole screen's surface instead of the",
         "// fixed raster, and moy_ui_link_frame calls it with that surface's size",
         "// and the font scale before the first frame it reads and again whenever",
         "// they change. Optional, so weak; exported, so the console finds it.",
         "#if defined(__wasm__)",
         '#define MOY_UI_LAYOUT __attribute__((weak, export_name("_layout")))',
         "#else",
         '#define MOY_UI_LAYOUT __attribute__((weak, visibility("default")))',
         "#endif",
         "void _layout(int32_t w, int32_t h, int32_t fs) MOY_UI_LAYOUT;",
         "// The frame's screen handed to the console (moy_present): 0, or -1.",
         "int moy_ui_link_show(void);",
         "// The canvas the rows draw on (a libmoy moy_canvas), NULL before the open.",
         "void *moy_ui_link_target(void);", ""]
    for r in rows:
        o += c_decl("int32_t moy_ui_wasm_%s" % verb_of(r), ui_c_params(r).split(", "), ";")
    o.append("")
    for r in rows:
        args = ["moy_ui_link_target()"]
        for n, t in params(r):
            if t == "int":
                args.append(n)
            elif t == "out":
                args += ["(uint8_t *)%s" % n, "%s_cap" % n]
            else:
                args += ["(const uint8_t *)%s" % n, "%s_n" % n]
        o.append("// %s" % r["name"])
        o += c_decl("static inline int32_t %s" % ident(r["name"]), _app_c_params(r), " {")
        o += c_decl("    return moy_ui_wasm_%s" % verb_of(r), args, ";")
        o.append("}")
    o += ["", "#ifdef __cplusplus", "}", "#endif", "", "#endif // MOYBYTE_UI_H", ""]
    return "\n".join(o)


def _stub_rows(src, field):
    """[(name, row, permissions)] of the names `field` lists, in the source's
    order, each once."""
    seen, out = set(), []
    for r in src["rows"]:
        for n in r.get(field) or ():
            if n in SPEC_NAMES or n in seen:
                continue
            seen.add(n)
            out.append((n, r, tuple(perms_of(r, src))))
    return out


def _stub_doc(n, r, perms):
    ps = ", ".join("%s: %s" % (a, t) for a, t in params(r) if t != "out")
    return "%s(%s): the row %s%s" % (n, ps, r["name"],
                                     ("; needs " + " or ".join(perms)) if perms else "")


STUB_HEAD = header("native/os_calls.json", "")


def gen_lua_stub(src):
    o = ["---@meta", "-- " + STUB_HEAD,
         "-- The names a Lua app reaches beyond moy-spec's verbs (moy-api.lua), for",
         "-- the Lua language server: docs/app_api_v1.md section 10 has each call.", ""]
    tables = []
    for n, r, perms in _stub_rows(src, "lua"):
        tbl = n.rpartition(".")[0]
        if tbl and tbl not in tables:
            tables.append(tbl)
            o.append("%s = {}" % tbl)
        if "in" not in r and n == r["name"]:     # a service's object (net), not a call
            o += ["--- " + _stub_doc(n, r, perms), "%s = {}" % n]
        else:
            o += ["--- " + _stub_doc(n, r, perms), "function %s(...) end" % n]
    return "\n".join(o) + "\n"


def gen_py_stubs(src):
    """(__builtins__.pyi, moybyte.pyi): a Python app's globals and its
    moybyte module's names beyond moy-spec's verbs, for an editor."""
    head = ["# " + STUB_HEAD,
            "# The names a Python app reaches beyond moy-spec's verbs, for an editor:",
            "# docs/app_api_v1.md section 10 has each call, runtime/system/moybyte.py",
            "# its signature.", "from typing import Any", ""]
    glob, mod, objs = [], [], {}
    for n, r, perms in _stub_rows(src, "py"):
        doc = '    """%s"""' % _stub_doc(n, r, perms)
        obj, _, verb = n.rpartition(".")
        if obj == "moybyte":
            mod += ["def %s(*args: Any, **kwargs: Any) -> Any:" % verb, doc, ""]
        elif obj:
            objs.setdefault(obj, []).extend(
                ["    def %s(self, *args: Any, **kwargs: Any) -> Any:" % verb,
                 "    " + doc, ""])
        elif "in" in r:
            glob += ["def %s(*args: Any, **kwargs: Any) -> Any:" % n, doc, ""]
        else:                       # a service's object (wifi, net), not a call
            glob += ["%s: Any   # %s" % (n, " or ".join(perms) or r["name"]), ""]
    for obj, body in objs.items():
        glob += ["class _%s:" % obj] + body + ["%s: _%s" % (obj, obj), ""]
    for p in src["permissions"]:
        for n in p["py"]:
            if n not in objs and not any(line.startswith(("def %s(" % n, "%s: " % n))
                                         for line in glob):
                glob += ["%s: Any   # %s" % (n, p["perm"]), ""]
    return "\n".join(head + glob).rstrip("\n") + "\n", "\n".join(head + mod).rstrip("\n") + "\n"
