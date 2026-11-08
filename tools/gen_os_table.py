#!/usr/bin/env python3
"""Write the OS function table's consumers from its one source,
native/moy_elf/os_table.json (docs/os_2026-11/tiers.md section 1):

  native/moy_elf/moy_os_table.h   the rows' ordinals, the version, the table type
  native/moy_elf/moy_os_rows.h    the kernel's prototype of each row (moy_os_<name>)
  native/moy_elf/moy_os_table.c   the table itself, a retired row's typed stub
  sdk/include/moybyte/os.h        a module's header: the hooks, the moybyte rows,
                                  the event record (from native/moy_input/moy_event.h)
  sdk/src/moyos_stubs.c           a module's side of every row: the function by
                                  its C library name, calling the table by ordinal

The output is committed: no build runs this, and tests/test_os_table.py
regenerates it and fails on any difference, as tests/test_ui_tables.py does
for tools/gen_ui_tables.py.

    python3 tools/gen_os_table.py [--check]
"""

import argparse
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SOURCE = os.path.join(ROOT, "native", "moy_elf", "os_table.json")
EVENT_H = os.path.join(ROOT, "native", "moy_input", "moy_event.h")

KERNEL_H = os.path.join(ROOT, "native", "moy_elf", "moy_os_table.h")
ROWS_H = os.path.join(ROOT, "native", "moy_elf", "moy_os_rows.h")
KERNEL_C = os.path.join(ROOT, "native", "moy_elf", "moy_os_table.c")
SDK_H = os.path.join(ROOT, "sdk", "include", "moybyte", "os.h")
STUBS_C = os.path.join(ROOT, "sdk", "src", "moyos_stubs.c")

FIELDS = {"name", "kind", "hdr", "c", "va", "glibc", "fail", "noreturn", "retired"}
KINDS = ("posix", "moybyte")


class TableError(ValueError):
    pass


def load(path=SOURCE):
    with open(path) as f:
        return json.load(f)


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
    params = _split(m.group("args"))
    variadic = bool(params) and params[-1] == "..."
    if variadic:
        params = params[:-1]
    if params == ["void"]:
        params = []
    for p in params:
        _param_name(p)
    return ret, m.group("name"), params, variadic


def check(table):
    """The table's own rules: fields, kinds, names once, a prototype that
    parses and names its row, a varargs row's stand-in, the version."""
    rows = table["rows"]
    seen = set()
    for i, r in enumerate(rows):
        if set(r) - FIELDS or not {"name", "kind", "c", "fail"} <= set(r):
            raise TableError("row %d's fields: %s" % (i, sorted(r)))
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
    if table["version"] != len(rows):
        raise TableError("version %d with %d rows: an append bumps it, nothing else does"
                         % (table["version"], len(rows)))


def kernel_params(r):
    _ret, _name, params, variadic = parse(r["c"])
    if variadic:
        params = params + [r["va"]["arg"]]
    return params


def kernel_proto(r, prefix="moy_os_"):
    ret, name, _p, _v = parse(r["c"])
    params = kernel_params(r)
    sep = "" if ret.endswith("*") else " "
    return "%s%s%s%s(%s)" % (ret, sep, prefix, name, ", ".join(params) or "void")


def ident(name):
    return re.sub(r"[^A-Za-z0-9_]", "_", name)


# -- the outputs ------------------------------------------------------------

TABLE_T = """typedef struct {
    uint32_t version;     // bumped on every append; never on anything else
    uint32_t rows;
    void *fn[];           // row i is the same prototype forever; retired -> a typed stub
} moy_os_table_t;"""

HEAD = "// GENERATED by tools/gen_os_table.py from native/moy_elf/os_table.json. Do not edit.\n"


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
         "// place of the ...). native/moy_elf/moy_os.c defines them.\n",
         "#ifndef MOY_OS_ROWS_H", "#define MOY_OS_ROWS_H", "",
         "#include <stdint.h>"]
    for h in _headers(rows):
        o.append("#include <%s>" % h)
    o += ["", "// The calling thread's errno, set by a row that fails.",
          "void moy_os_errno_set(int e);", ""]
    for r in rows:
        attr = " __attribute__((noreturn))" if r.get("noreturn") else ""
        o.append("%s%s;" % (kernel_proto(r), attr))
    o += ["", "#endif // MOY_OS_ROWS_H", ""]
    return "\n".join(o)


def gen_kernel_c(table):
    rows = table["rows"]
    o = [HEAD, '#if !__has_include("esp_heap_caps.h")    // a host (moy_os.c)', "#define _POSIX_C_SOURCE 200809L", "#define _DEFAULT_SOURCE",
         "#endif", "", "#include <errno.h>", "#include <stddef.h>", "",
         '#include "moy_os_table.h"', '#include "moy_os_rows.h"', ""]
    for r in rows:
        if not r.get("retired"):
            continue
        ret, name, _p, _v = parse(r["c"])
        params = kernel_params(r)
        o.append("// Retired %s." % r["retired"])
        o.append("static %s {" % kernel_proto(r, "moy_os_retired_"))
        for p in params:
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


def _event_block(path=EVENT_H):
    """MOY_EVENT_VERSION, the record and its kinds, as moy_event.h states them."""
    with open(path) as f:
        text = f.read()
    out = []
    for pat in (r"#define MOY_EVENT_VERSION \d+",
                r"typedef struct \{[^}]*\} moy_event_t;",
                r"enum \{\s*MOY_EV_NONE = 0,[^}]*\};"):
        m = re.search(pat, text, re.S)
        if not m:
            raise TableError("moy_event.h no longer has %r" % pat)
        out.append(m.group(0))
    return "\n\n".join(out)


def gen_sdk_h(table):
    rows = table["rows"]
    o = [HEAD,
         "// A Moybyte native module's header (sdk/README.md): what a module defines for\n"
         "// the OS to call, and the OS's own functions that have no POSIX name. The\n"
         "// POSIX functions are the C library's own headers' (unistd.h, fcntl.h, ...),\n"
         "// served by the same table.\n",
         "#ifndef MOYBYTE_OS_H", "#define MOYBYTE_OS_H", "",
         "#include <stdint.h>", "",
         "#ifdef __cplusplus", 'extern "C" {', "#endif", "",
         "// The OS table this header was made from: a module built against it runs",
         "// on a console whose table has every row the module calls.",
         "#define MOY_OS_VERSION %d" % table["version"], "",
         "// The table the OS hands a module at its load (sdk/src/moyos_crt.c).",
         TABLE_T, "",
         "// The event record (version 1, 16 bytes).",
         _event_block(), "",
         "// What the OS calls, each optional. A command is main(argc, argv); an app or",
         "// a service is moy_start, then moy_timer for each tick of a timer it",
         "// started (moy_timer_every) and moy_on for each event of the kinds it asked",
         "// for (moy_events), then moy_stop. Every call is on the module's own task.",
         "int moy_start(void);",
         "void moy_stop(void);",
         "void moy_timer(uint32_t t_us);",
         "void moy_on(const moy_event_t *e);", "",
         "// moy_log's levels: the OS log's (native/moy_kernel/moy_log.h).",
         "#define MOY_LOG_ERR 3", "#define MOY_LOG_WARNING 4", "#define MOY_LOG_INFO 6",
         "#define MOY_LOG_DEBUG 7", ""]
    for r in rows:
        if r["kind"] == "moybyte":
            o.append("%s;" % r["c"])
    o += ["", "#ifdef __cplusplus", "}", "#endif", "", "#endif // MOYBYTE_OS_H", ""]
    return "\n".join(o)


def gen_stubs_c(table):
    rows = table["rows"]
    o = [HEAD,
         "// The module's side of the OS table: each row a function under its C library\n"
         "// name that calls the table's row by its ordinal. Built once per row\n"
         "// (-DMOY_OS_ONLY=<row>) into libmoyos.a, so a module links only the rows it\n"
         "// calls, and each records its ordinal in the non-loaded section .moy.rows,\n"
         "// which the loader reads to refuse a module that needs a newer console.\n",
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
        ret, name, params, variadic = parse(r["c"])
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
            o.append("        va_start(ap, %s);" % _param_name(params[-1]))
            o.append("        %s = va_arg(ap, %s);" % (vname, vtype))
            o.append("        va_end(ap);")
            o.append("    }")
        call = "((moy_os_fn_%s *)__moy_os->fn[%d])(%s)" % (ident(name), i, args)
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


def outputs(table=None):
    """[(path, text)] for every generated file."""
    if table is None:
        table = load()
    check(table)
    return [(KERNEL_H, gen_kernel_h(table)), (ROWS_H, gen_rows_h(table)),
            (KERNEL_C, gen_kernel_c(table)), (SDK_H, gen_sdk_h(table)),
            (STUBS_C, gen_stubs_c(table))]


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
