"""The app ABI: the C rows, the door's table, the wasm and ui imports."""


from . import TableError
from .table import FLOOR_ROLE, cap_packed, direct_native, door_rows, ident, params, role_of, roles, upper, verb_of, wasm_sig
from .cproto import HEAD, c_decl, wrap


def c_rows(src):
    return [r for r in door_rows(src) if r["server"] == "c"]


def door_c_rows(src):
    """The C rows the ROLE door serves: every C row but an object's and the
    toolkit's."""
    return [r for r in c_rows(src) if not r.get("object") and r.get("how") != "direct"]


def door_fields(r):
    """A C row's fields as the door checks them: i a number, * a span (an out
    whose capacity crosses is a number)."""
    out = ""
    for _n, t in params(r):
        if t == "out":
            out += "i" if cap_packed(r) else ""
        elif t == "cart":
            raise TableError("%s: a cart is no C row's field" % r["name"])
        else:
            out += "i" if t == "int" else "*"
    return out


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
    o += wrap(["MOY_ROLE_%s" % upper(r) + (" = 0" if i == 0 else "") for i, r in enumerate(rs)]
               + ["MOY_ROLE_N"])
    o += ["};",
          "// The roles every grant holds: the console calls, which the console answers",
          "// under the permission each names.",
          "#define MOY_ROLE_FLOOR (%s)" % (" | ".join("(1u << MOY_ROLE_%s)" % upper(r)
                                                      for r in floor) or "0u"),
          "#define MOY_ROLE_NAMES { \\"]
    o += [ln + " \\" for ln in wrap(['"%s"' % r for r in rs], "    ", 92)]
    o += ["}", "",
          "// The rows served in C: what moy_app_count counts.",
          "enum {"]
    cr = c_rows(src)
    o += wrap(["MOY_ROW_%s" % upper(r["name"]) + (" = 0" if i == 0 else "")
                for i, r in enumerate(cr)] + ["MOY_ROW_N"])
    o += ["};",
          "#define MOY_ROW_NAMES { \\"]
    o += [ln + " \\" for ln in wrap(['"%s"' % r["name"] for r in cr], "    ", 92)]
    o += ["}", "",
          "// The role table, row for row: what the ROLE door names by index.",
          "enum {"]
    dr = door_rows(src)
    o += wrap(["MOY_T_%s" % upper(r["name"]) + (" = 0" if i == 0 else "")
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
          "// The C rows the ROLE door serves (moy_app.c's door_c), each X(row, its",
          "// body door_<role>_<verb>, its fields): one character a field, i a number,",
          "// * a text or bytes, checked before the body runs. A C row not here (an",
          "// object, the toolkit's) the door refuses BAD.",
          "#define MOY_APP_DOOR_ROWS(X) \\"]
    o += ['    X(MOY_ROW_%s, %s, "%s") \\' % (upper(r["name"]), ident(r["name"]), door_fields(r))
          for r in door_c_rows(src)]
    o += ["", "// The most fields a C row takes.",
          "#define MOY_APP_DOOR_FIELDS %d" % max(len(door_fields(r)) for r in door_c_rows(src)),
          "",
          "// The permission each role is granted by: a role absent here is never",
          "// granted.",
          "#define MOY_APP_PERMS { \\"]
    for x in src["roles"]:
        if x["grant"] not in ("never", FLOOR_ROLE):
            o.append('    { "%s", MOY_ROLE_%s }, \\' % (x["grant"], upper(x["role"])))
    o += ["}", "", "#endif // MOY_APP_ROWS_H", ""]
    return "\n".join(o)


def ui_c_params(r):
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


def wasm_natives(src):
    """The rows with a moybyte.app import in their import names' order: the
    order WAMR looks a name up in, so the natives' table registers const,
    never sorted at run time."""
    return sorted(wasm_rows(src), key=lambda r: ident(r["name"]).encode())


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
        o += c_decl("int32_t %s" % direct_native(r), ui_c_params(r).split(", "), " UI_WEAK;")
    o.append("")
    for r in rows:
        if r["how"] == "direct":
            continue
        cps = ["void *e"]
        nums, fields = [], []
        for n, t in params(r):
            if t == "int":
                cps.append("int32_t %s" % n)
                nums.append("q_" + n)
                fields.append("NUM(q_%s, %s)" % (n, n))
            elif t == "out":
                cps += ["uint8_t *%s" % n, "uint32_t %s_cap" % n]
                if cap_packed(r):
                    # the door's Python half is handed the answer's capacity
                    nums.append("q_" + n)
                    fields.append("NUM(q_%s, %s_cap)" % (n, n))
            else:
                cps += ["const uint8_t *%s" % n, "uint32_t %s_n" % n]
                fields.append("{ %s, %s_n }" % (n, n))
        outs = [n for n, t in params(r) if t == "out"]
        fn = "w_" + ident(r["name"])
        o += c_decl("static int32_t %s" % fn, cps, " {")
        if nums:
            o.append("    uint8_t %s;" % ", ".join("%s[4]" % q for q in nums))
        if fields:
            o.append("    span_t f[%d] = { %s };" % (len(fields), ", ".join(fields)))
        o.append("    return run(e, %s, MOY_T_%s, %s, %d, %s, %s);" % (
            r["how"].upper(), upper(r["name"]), "f" if fields else "NULL", len(fields),
            outs[0] if outs else "NULL", (outs[0] + "_cap") if outs else "0"))
        o.append("}")
    o += ["", "// The natives, in their import names' order, and the table row each serves.",
          "static const moy_app_native_t NATIVES[MOY_APP_WASM_N] = {"]
    for r in wasm_natives(src):
        fn = direct_native(r) if r["how"] == "direct" else "w_" + ident(r["name"])
        o.append('    { "%s", FN(%s), "%s", NULL },' % (ident(r["name"]), fn, wasm_sig(r)))
    o += ["};", "static const int16_t NATIVE_ROWS[MOY_APP_WASM_N] = {"]
    o += wrap(["MOY_T_%s" % upper(r["name"]) for r in wasm_natives(src)])
    o += ["};", ""]
    return "\n".join(o)


def gen_ui_h(src):
    rows = [r for r in wasm_rows(src) if role_of(r) == "ui"]
    o = [HEAD,
         "// The toolkit's moybyte.app imports (native/moy_ui/moy_ui_wasm.h states each\n"
         "// verb's contract): their count, each native's prototype, which must be\n"
         "// moy_ui_wasm.h's, and the natives by import name, in the role table's order.\n",
         "#ifndef MOY_UI_WASM_ROWS_H", "#define MOY_UI_WASM_ROWS_H", "",
         "#include <stdint.h>", "",
         "#define MOY_UI_WASM_ROWS %d" % len(rows), ""]
    for r in rows:
        o += c_decl("int32_t moy_ui_wasm_%s" % verb_of(r), ui_c_params(r).split(", "), ";")
    o += ["", "#define MOY_UI_WASM_NATIVES { \\"]
    for r in rows:
        o.append('    { "%s", FN(moy_ui_wasm_%s) }, \\' % (ident(r["name"]), verb_of(r)))
    o += ["}", "", "#endif // MOY_UI_WASM_ROWS_H", ""]
    return "\n".join(o)


def gen_uitree_h(src):
    rows = [r for r in wasm_rows(src) if role_of(r) == "uitree"]
    o = [HEAD,
         "// The UI engine's moybyte.app imports (native/moy_uitree/moy_uitree_wasm.h\n"
         "// states each row's contract): each native's prototype, which the engine's\n"
         "// definitions are compiled against, in the role table's order.\n",
         "#ifndef MOY_UITREE_WASM_ROWS_H", "#define MOY_UITREE_WASM_ROWS_H", "",
         "#include <stdint.h>", "",
         "#define MOY_UITREE_WASM_ROWS %d" % len(rows), ""]
    for r in rows:
        o += c_decl("int32_t %s" % direct_native(r), ui_c_params(r).split(", "), ";")
    o += ["", "#endif // MOY_UITREE_WASM_ROWS_H", ""]
    return "\n".join(o)
