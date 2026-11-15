"""The WASI tier: a WASI program's moybyte.os imports."""


from .table import (WASI_OS, cap_packed, params, upper, verb_of, wasi_c, wasi_call, wasi_name,
    wasi_params, wasi_role, wasi_rows, wasi_sig)
from .cproto import HEAD, c_decl, param_name, parse


def wasi_import(r):
    """A WASI program's side of a "wasi" row: its moybyte.os import, and the
    function by the row's C name over it (each text a pointer and a length)."""
    ret, name, ps, _v = parse(wasi_c(r))
    verb = wasi_name(r)
    args, call = [], []
    for n, k, ln in wasi_params(r):
        if k == "s":
            args += ["const char *%s" % n, "size_t %s_len" % n]
            call += [n, "%s ? __builtin_strlen(%s) : 0" % (n, n)]
        elif k == "o":
            args += ["void *%s" % n, "size_t %s" % (ln or n + "_cap")]
            call += [n, ln or "%s_cap" % n]
        elif k == "b":
            args += ["const void *%s" % n, "size_t %s" % (ln or n + "_len")]
            call += [n, ln or "%s_len" % n]
        elif k == "p":
            args.append("int *%s" % n)
            call.append(n)
        else:
            args.append(next(p for p in ps if param_name(p) == n))
            call.append(n)
    imp = "__moy_os_%s" % verb
    return ["int32_t %s(%s)" % (imp, ", ".join(args) or "void"),
            '    __attribute__((import_module("%s"), import_name("%s")));' % (WASI_OS, verb),
            "static inline %s {" % wasi_c(r),
            "    int32_t r = %s(%s);" % (imp, ", ".join(call)),
            "    if (r < 0) {",
            "        errno = (int)-r;",
            "        return %s;" % r.get("fail", "-1"),
            "    }",
            "    return (%s)r;" % ret,
            "}"]


def gen_wasi_h(src):
    o = [HEAD,
         "// A WASI program's moybyte.os imports (native/moy_wasm/moy_wasi.h): each\n"
         "// X(verb, its WAMR signature), a text a pointer and a length, the rows of\n"
         "// native/os_calls.json marked \"wasi\", whose C function the kernel serves an\n"
         "// ELF module too, in their import names' order: WAMR's lookup order, so the\n"
         "// table registers const, unsorted at run time.\n",
         "#ifndef MOY_WASI_OS_ROWS_H", "#define MOY_WASI_OS_ROWS_H", "",
         "#define MOY_WASI_OS_ROWS(X) \\"]
    for r in sorted(wasi_rows(src), key=lambda r: wasi_name(r).encode()):
        o.append('    X(%s, "%s") \\' % (wasi_name(r), wasi_sig(r)))
    o += ["",
          "// The rows served where they are called (how \"here\" or \"seq\"): C on the",
          "// caller's task, no VM's queue, which a host whose module runs on the VM's",
          "// own task can serve in place (native/moy_wasm_web/moy_wasi_web.h).",
          "#define MOY_WASI_OS_HERE(X) \\"]
    for r in sorted(wasi_rows(src), key=lambda r: wasi_name(r).encode()):
        if r.get("how") in ("here", "seq"):
            o.append("    X(%s) \\" % wasi_name(r))
    o += ["", "#endif // MOY_WASI_OS_ROWS_H", "",
          "// The natives of the rows the kernel does not serve, where moy_wasi.c",
          "// defines MOY_WASI_OS_BODIES after its os_call and os_role: a console",
          "// call packs its arguments (os_arg_t: a text, a text that may be empty, a",
          "// number) as the JSON array the console's verb takes; an app row packs its",
          "// fields (os_arg_t: a text or bytes, a number) for its role table row.",
          "#if defined(MOY_WASI_OS_BODIES) && !defined(MOY_WASI_OS_BODIES_DONE)",
          "#define MOY_WASI_OS_BODIES_DONE", ""]
    for r in wasi_rows(src):
        if wasi_role(r):
            ps, packs, out = ["wasm_exec_env_t e"], [], ("NULL", "0")
            for name, typ in params(r):
                if typ == "int":
                    ps.append("int32_t %s" % name)
                    packs.append("OS_I(%s)" % name)
                elif typ == "out":
                    ps += ["uint8_t *%s" % name, "uint32_t %s_cap" % name]
                    out = (name, name + "_cap")
                    if cap_packed(r):               # a door served in Python: _call_body's
                        packs.append("OS_CAP(%s_cap)" % name)
                else:
                    ps += ["const %s *%s" % ("uint8_t" if typ == "bytes" else "char", name),
                           "uint32_t %s_n" % name]
                    packs.append("OS_O((const char *)%s, %s_n)" % (name, name)
                                 if typ == "bytes" else "OS_O(%s, %s_n)" % (name, name))
            o += c_decl("static int32_t w_os_%s" % wasi_name(r), ps, " {")
            if packs:
                o.append("    const os_arg_t a[%d] = { %s };" % (len(packs), ", ".join(packs)))
            o.append("    return os_role(e, MOY_T_%s, %s, %s, %d, %s, %s);" % (
                upper(r["name"]), "OS_" + r["how"].upper(),
                "a" if packs else "NULL", len(packs), out[0], out[1]))
            o += ["}", ""]
            continue
        if not wasi_call(r):
            continue
        ps, packs = ["wasm_exec_env_t e"], []
        for name, typ in params(r):
            if typ == "int":
                ps.append("int32_t %s" % name)
                packs.append("OS_I(%s)" % name)
            else:
                ps += ["const char *%s" % name, "uint32_t %s_n" % name]
                packs.append("OS_%s(%s, %s_n)" % ("O" if typ == "str?" else "S", name, name))
        o += c_decl("static int32_t w_os_%s" % verb_of(r), ps, " {")
        if packs:
            o.append("    const os_arg_t a[%d] = { %s };" % (len(packs), ", ".join(packs)))
        o.append('    return os_call(e, "%s", %s, %d);' % (r["name"], "a" if packs else "NULL",
                                                       len(packs)))
        o += ["}", ""]
    o += ["#endif // MOY_WASI_OS_BODIES", ""]
    return "\n".join(o)
