"""The ELF tier: the OS function table, the kernel's rows, the argument
checks, the SDK's header and stubs."""

import re

from . import TableError
from .table import (EVQ_H, EXIT_HOOK, U_TOKEN, cap_packed, ident, kernel_params, kernel_proto,
    load, params, wasi_rows)
from .cproto import HEAD, param_name, parse
from .wasi import wasi_import


TABLE_T = """typedef struct {
    uint32_t version;     // bumped on every append; never on anything else
    uint32_t rows;
    void *fn[];           // row i is the same prototype forever; retired -> a typed stub
} moy_os_table_t;"""


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
         "// place of the ...). native/moy_elf/moy_os*.c define them, but a row the role\n"
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
          "// How it runs: on the task that serves the process's session; where it",
          "// is called, under the app ABI's seqlock (native/moy_app/moy_app_wasm.h);",
          "// or where it is called, its row's own lock its guard, hopped where it",
          "// answers that it needs the VM.",
          "enum { MOY_OS_HOP = 0, MOY_OS_SEQ = 1, MOY_OS_HERE = 2 };",
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
    """A door row's body: its fields packed for the role door. A door
    served in Python is handed an answer's capacity as a number field, as
    the wasm adapter hands it (gen_wasm_h); the C side still writes at
    most `cap` bytes into the buffer, which the row's `w:<name>_cap` rule
    has proven lies in the module's memory, so the number only tells the
    door what fits (an INT32_MAX ceiling keeps it a positive int32)."""
    src = r["src"]
    cap_field = cap_packed(src)
    ins = [(n, t) for n, t in params(src) if t != "out" or cap_field]
    out = [n for n, t in params(src) if t == "out"]
    o = ["%s {" % kernel_proto(r)]
    if ins:
        o.append("    const moy_os_arg_t a[%d] = {" % len(ins))
        for n, t in ins:
            if t == "out":
                o.append("        { NULL, 0u, (int32_t)(%s_cap < 0x7fffffffu ? %s_cap : 0x7fffffffu),"
                         " MOY_OS_ARG_INT }," % (n, n))
            elif t == "int":
                o.append("        { NULL, 0u, %s, %s }," % (n, _ARG[t]))
            elif t == "bytes":
                o.append("        { %s, %s_len, 0, %s }," % (n, n, _ARG[t]))
            else:
                o.append("        { %s, 0u, 0, %s }," % (n, _ARG[t]))
        o.append("    };")
    how = "MOY_OS_" + r["how"].upper()
    o.append("    return moy_os_call(%du, %s, %s, %d, %s, %s);   // %s" % (
        r["door"], how, "a" if ins else "NULL", len(ins),
        out[0] if out else "NULL", (out[0] + "_cap") if out else "0u", src["name"]))
    o.append("}")
    o.append("")
    return o


def gen_kernel_c(table):
    rows = table["rows"]
    o = [HEAD, '#include "moy_platform.h"', "", "#include <errno.h>", "#include <stddef.h>", "",
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
            o.append("    (void)%s;" % param_name(p))
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


def evq_block(path=EVQ_H):
    """The event descriptor, its record's version, the backpressure's
    bounds and the record, as moy_evq.h states them."""
    with open(path) as f:
        text = f.read()
    out = []
    for pat in (r"#define MOY_EVQ_FD \d+", r"#define MOY_EVQ_VERSION \d+",
                r"#define MOY_EVQ_NAME \d+u", r"#define MOY_EVQ_APP \d+u",
                r"#define MOY_EVQ_SENDER \d+u", r"#define MOY_EVQ_BATCH \d+u",
                r"typedef struct \{[^}]*\} moy_evq_ev_t;"):
        m = re.search(pat, text, re.S)
        if not m:
            raise TableError("moy_evq.h no longer has %r" % pat)
        out.append(m.group(0))
    return "\n".join(out[:-1]) + "\n\n" + out[-1]


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
         "// how many processes took one, or -1 with EAGAIN where none did because",
         "// a receiver holds MOY_EVQ_SENDER of this process's messages unread (the",
         "// kernel's backpressure: send again later). A poll's wake reads",
         "// MOY_EVQ_BATCH records at most (EAGAIN past that, the rest queued for the",
         "// next poll, which returns at once): each sender's messages in its order,",
         "// the senders in turn.",
         evq_block(), "",
         "// moy_log's levels: the OS log's (native/moy_kernel/moy_log.h).",
         "#define MOY_LOG_ERR 3", "#define MOY_LOG_WARNING 4", "#define MOY_LOG_INFO 6",
         "#define MOY_LOG_DEBUG 7", "",
         "// A status item's states (moy_status_show, moy_status_state): its icon in",
         "// the accent, the paused ink or the danger ink.",
         "#define MOY_STATUS_ON 0", "#define MOY_STATUS_PAUSED 1", "#define MOY_STATUS_ERROR 2",
         "",
         "#ifdef __wasm__", "",
         "#include <errno.h>", ""]
    for r in wasi_rows(src):
        o += wasi_import(r)
        o.append("")
    o += ["#else", "",
          "// The table the OS hands a module at its load (sdk/src/moyos_crt.c).",
          TABLE_T, ""]
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
        args = ", ".join(param_name(p) for p in kparams)
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
            vname = param_name(va["arg"])
            vtype = va["arg"][:va["arg"].rfind(vname)].strip()
            o.append("    %s = 0;" % va["arg"])
            o.append("    if (%s) {" % va["when"])
            o.append("        va_list ap;")
            o.append("        va_start(ap, %s);" % param_name(ps[-1]))
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


def _ptype(p):
    """A parameter's type: its declaration without its name."""
    name = param_name(p)
    return re.sub(r"\b%s\b" % name, "", p, count=1).strip()


def _u_expr(tok, p, by_name):
    m = U_TOKEN.match(tok)
    name = param_name(p)
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
        by_name = {param_name(p): p for p in params}
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


# Each argument's kind for the wild fixture's sweep: what its rule checks, or
# the handle the row looks it up by.
WILD_KINDS = ("A_VAL", "A_STR", "A_STR0", "A_BUF", "A_BUF0", "A_CODE", "A_LEN", "A_FD",
              "A_THREAD", "A_DIR", "A_MAP", "A_ADDR", "A_OPAQUE")


def _wild_kind(p, tok, row, counts):
    """A_LEN is a pointer to another argument's count (a `*n` rule's n)."""
    name = param_name(p)
    ptype = _ptype(p)
    if name in counts:
        return "A_LEN"
    if tok == "-":
        if name == "fd":
            return "A_FD"
        if ptype == "pthread_t":
            return "A_THREAD"
        return "A_VAL"
    if tok == "k":
        if row == "munmap":
            return "A_MAP"
        if ptype.startswith("DIR"):
            return "A_DIR"
        if "addrinfo" in ptype:
            return "A_ADDR"
        return "A_OPAQUE"
    if tok == "f":
        return "A_CODE"
    if tok.startswith("s"):
        return "A_STR0" if tok.endswith("?") else "A_STR"
    return "A_BUF0" if U_TOKEN.match(tok).group("null") else "A_BUF"


def gen_wild_h(table):
    """The wild fixture's row list (tests/fixtures/elf/elf_wild.moy): every
    row a module reaches with a checked pointer or a handle, each argument's
    kind, and which argument is a buffer's count."""
    o = [HEAD,
         "// Every row whose arguments the kernel checks or looks up, for the wild",
         "// fixture's sweep (tests/on_glass.py, elf_umode_isolates): its ordinal,",
         "// its name, each argument's kind and, for a buffer, the index of the",
         "// argument that counts it (-1: its type's size).", "",
         "#ifndef WILD_ROWS_H", "#define WILD_ROWS_H", "",
         "enum { %s };" % ", ".join(WILD_KINDS), "",
         "typedef struct {",
         "    unsigned short row;",
         "    const char *name;",
         "    unsigned char n;",
         "    unsigned char kind[8];",
         "    signed char len[8];",
         "} wild_row_t;", "",
         "static const wild_row_t WILD_ROWS[] = {"]
    for i, r in enumerate(table["rows"]):
        if r.get("retired"):
            continue
        params = kernel_params(r)
        u = r.get("u", ["-"] * len(params))
        names = [param_name(p) for p in params]
        counts = {U_TOKEN.match(t).group("n") for t in u
                  if U_TOKEN.match(t) and U_TOKEN.match(t).group("deref")}
        kinds = [_wild_kind(p, t, r["name"], counts) for p, t in zip(params, u)]
        if all(k in ("A_VAL", "A_OPAQUE") for k in kinds):
            continue
        lens = []
        for t in u:
            m = U_TOKEN.match(t)
            n = m.group("n") if m and t not in ("-", "k", "f") and not t.startswith("s") else None
            lens.append(str(names.index(n)) if n and not m.group("deref") else "-1")
        o.append('    { %d, "%s", %d, { %s }, { %s } },' % (
            i, r["name"], len(params), ", ".join(kinds), ", ".join(lens)))
    o += ["};", "", "#define WILD_NROWS (sizeof WILD_ROWS / sizeof WILD_ROWS[0])", "",
          "#endif", ""]
    return "\n".join(o)
