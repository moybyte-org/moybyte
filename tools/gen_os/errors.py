"""The app ABI's answer codes: the C enum and what each face makes of a code,
for the C faces (MicroPython's and Lua's) and the CPython host's."""

import errno as _errno

from . import TableError, header
from .cproto import HEAD

ERROR_FIELDS = {"code", "n", "doc", "py", "errno", "msg", "lua", "err", "os"}
EVQ_FIELDS = {"code", "os", "app"}
EXCEPTIONS = ("ValueError", "OSError", "MemoryError")
# What an unknown code answers as.
FALLBACK = "BAD"


def errors(src):
    return src.get("errors", [])


def evq_errors(src):
    return src.get("evq_errors", [])


def check_errors(src):
    rows = errors(src)
    if [e.get("n") for e in rows] != list(range(len(rows))):
        raise TableError("the errors are numbered 0, 1, ... in order")
    if not rows or rows[0]["code"] != "OK":
        raise TableError("the errors begin with OK")
    codes = [e["code"] for e in rows]
    if len(set(codes)) != len(codes):
        raise TableError("an error code twice")
    if FALLBACK not in codes:
        raise TableError("no %s, which an unknown code answers as" % FALLBACK)
    for e in rows:
        if set(e) - ERROR_FIELDS or not e.get("doc"):
            raise TableError("error %s's fields: %s" % (e.get("code"), sorted(e)))
        if e["code"] == "OK":
            if set(e) != {"code", "n", "doc"}:
                raise TableError("OK raises nothing")
            continue
        if not e.get("os", "").isupper():
            raise TableError("error %s: no os, the errno a compiled program reads" % e["code"])
        if e.get("py") not in EXCEPTIONS or not e.get("lua"):
            raise TableError("error %s: py is one of %s, and lua its text" % (e["code"], EXCEPTIONS))
        if (e["py"] == "OSError") != ("errno" in e):
            raise TableError("error %s: an errno is OSError's, and OSError has one" % e["code"])
        if e["py"] != "MemoryError" and not e.get("msg"):
            raise TableError("error %s: no msg" % e["code"])
        for k in ("msg", "lua", "err"):
            if '"' in e.get(k, "") or "\\" in e.get(k, ""):
                raise TableError("error %s: %s is plain text" % (e["code"], k))
    seen = set()
    for e in evq_errors(src):
        if set(e) != EVQ_FIELDS or not e["os"].isupper() or e["code"] in seen:
            raise TableError("evq error %s's fields: %s" % (e.get("code"), sorted(e)))
        if e["app"] not in codes or e["app"] == "OK":
            raise TableError("evq error %s: app %s is no app code" % (e["code"], e["app"]))
        seen.add(e["code"])


def _c(s):
    return "NULL" if s is None else '"%s"' % s


def gen_err_h(src):
    rows = errors(src)
    o = [HEAD, "// The app ABI's answer codes (a row answers -code) and what each face makes",
         "// of one. MOY_APP_ERRORS(X) expands X(code, exception, errno, message, Lua",
         "// message) for every code but OK: the Python faces raise the exception",
         "// (with the errno, for OSError) and the message, a Lua app's call raises the",
         "// Lua message. MOY_APP_ERR_<code> is the text a storage row answers as its",
         "// err instead of raising (IO's formats the store's errno and its strerror).",
         "// A code not here answers as MOY_APP_%s." % FALLBACK, "",
         "#ifndef MOY_APP_ERR_H", "#define MOY_APP_ERR_H", "", "enum {"]
    width = max(len("    MOY_APP_%s = %d," % (e["code"], e["n"])) for e in rows)
    for e in rows:
        line = "    MOY_APP_%s = %d," % (e["code"], e["n"])
        o.append((line.ljust(width) + "  // " + e["doc"]) if e["code"] != "OK" else line)
    o += ["};", "", "#define MOY_APP_FALLBACK MOY_APP_%s" % FALLBACK, "",
          "#define MOY_APP_ERRORS(X) \\"]
    body = ["    X(%s, %s, %s, %s, %s)" % (e["code"], e["py"], e.get("errno", "NONE"),
                                          _c(e.get("msg")), _c(e["lua"]))
            for e in rows[1:]]
    o += [b + " \\" for b in body[:-1]] + [body[-1], ""]
    o += ["", "// The errno a compiled program reads for each code but OK, without its E:",
          "// X(code, ACCES) is an ELF module's EACCES and a WASI program's ACCES.",
          "#define MOY_APP_ERRNO(X) \\"]
    body = ["    X(%s, %s)" % (e["code"], e["os"]) for e in rows[1:]]
    o += [b + " \\" for b in body[:-1]] + [body[-1], ""]
    for e in rows:
        if "err" in e:
            o.append("#define MOY_APP_ERR_%s %s" % (e["code"], _c(e["err"])))
    plain = [e for e in rows if "err" in e and "%" not in e["err"]]
    o += ["", "// The codes whose err is plain text: X(code), its text MOY_APP_ERR_<code>.",
          "#define MOY_APP_ERR_TEXTS(X) %s" % " ".join("X(%s)" % e["code"] for e in plain)]
    o += ["", "#endif", ""]
    return "\n".join(o)


def gen_evq_codes_h(src):
    rows = evq_errors(src)
    o = [HEAD, "// What each tier makes of the event queue's codes (moy_evq.h numbers them):",
         "// MOY_EVQ_CODES(X) expands X(code, errno, app) for every code but OK, the",
         "// errno a compiled program reads for it without its E (an ELF module's",
         "// EBADF, a WASI program's BADF) and the app ABI's code a row answers for it",
         "// (MOY_APP_<app>, negated).", "",
         "#ifndef MOY_EVQ_CODES_H", "#define MOY_EVQ_CODES_H", "",
         "#define MOY_EVQ_CODES(X) \\"]
    body = ["    X(%s, %s, %s)" % (e["code"], e["os"], e["app"]) for e in rows]
    o += [b + " \\" for b in body[:-1]] + [body[-1], "", "#endif", ""]
    return "\n".join(o)


def gen_face_errors(src):
    rows = errors(src)
    o = [header("native/os_calls.json", "#"),
         '"""The app ABI\'s answer codes (native/moy_app/moy_app_err.h) and what the',
         "Python faces make of one: a row answers -code, as the console's own",
         "servers do; `exception(code)` is what a refused call raises,",
         "ERR[code] the text a storage row answers as its err instead of raising (IO's",
         'formats the store\'s errno and its strerror). A code not here answers as',
         '%s. An errno is its number (MicroPython\'s errno module names a few)."""' % FALLBACK, ""]
    o += ["%s = %d" % (e["code"], e["n"]) for e in rows]
    o += ["", "# code: (exception, errno, message)", "RAISES = {"]
    for e in rows[1:]:
        eno = "%d" % getattr(_errno, e["errno"]) if "errno" in e else "None"
        o.append("    %s: (%s, %s, %r)," % (e["code"], e["py"], eno, e.get("msg")))
    o += ["}", "", "ERR = {"]
    o += ["    %s: %r," % (e["code"], e["err"]) for e in rows if "err" in e]
    o += ["}", "", "",
          "def exception(code):",
          '    """The exception a refused call with `code` raises."""',
          "    exc, eno, msg = RAISES.get(code, RAISES[%s])" % FALLBACK,
          "    if eno is not None:",
          "        return exc(eno, msg)",
          "    return exc() if msg is None else exc(msg)", ""]
    return "\n".join(o)
