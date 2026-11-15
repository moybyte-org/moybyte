"""The app ABI's answer codes: the C enum and what each face makes of a code,
for the C faces (MicroPython's and Lua's) and the CPython host's."""

from . import TableError, header
from .cproto import HEAD

ERROR_FIELDS = {"code", "n", "doc", "py", "errno", "msg", "lua", "err"}
EXCEPTIONS = ("ValueError", "OSError", "MemoryError")
# What an unknown code answers as.
FALLBACK = "BAD"


def errors(src):
    return src.get("errors", [])


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
        if e.get("py") not in EXCEPTIONS or not e.get("lua"):
            raise TableError("error %s: py is one of %s, and lua its text" % (e["code"], EXCEPTIONS))
        if (e["py"] == "OSError") != ("errno" in e):
            raise TableError("error %s: an errno is OSError's, and OSError has one" % e["code"])
        if e["py"] != "MemoryError" and not e.get("msg"):
            raise TableError("error %s: no msg" % e["code"])
        for k in ("msg", "lua", "err"):
            if '"' in e.get(k, "") or "\\" in e.get(k, ""):
                raise TableError("error %s: %s is plain text" % (e["code"], k))


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
    for e in rows:
        if "err" in e:
            o.append("#define MOY_APP_ERR_%s %s" % (e["code"], _c(e["err"])))
    o += ["", "#endif", ""]
    return "\n".join(o)


def gen_face_errors(src):
    rows = errors(src)
    o = [header("native/os_calls.json", "#"),
         '"""The app ABI\'s answer codes (native/moy_app/moy_app_err.h) and what the',
         "host's faces make of one: `exception(code)` is what a refused call raises,",
         "ERR[code] the text a storage row answers as its err instead of raising (IO's",
         'formats the store\'s errno and its strerror). A code not here answers as',
         '%s."""' % FALLBACK, "", "import errno as _errno", ""]
    o += ["%s = %d" % (e["code"], e["n"]) for e in rows]
    o += ["", "# code: (exception, errno, message)", "RAISES = {"]
    for e in rows[1:]:
        eno = "_errno." + e["errno"] if "errno" in e else "None"
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
