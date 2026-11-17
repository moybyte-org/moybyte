# Map (grep -n a name to jump there):
#   load            every face.json, by module
#   Headers         a face's headers: prototypes, structures, constants
#   Row             one row: its C parameters, the kinds the faces convert
#   check           the rules a face.json is refused by
#   mirrors         a face's ctypes structure mirrors (tools/gen_os/structs.py)
#   gen_mp          the MicroPython face, modmoy_<m>_face.h
#   gen_host        the ctypes face, runtime/host/<m>_face.py
#   gen_faces_py    the host faces' registration table
#   outputs         every file this writes
#   parity_script   the script both runtimes run for test_face_parity
"""The binding faces: every native/<dir>/face.json, its rows' C prototypes
read from the module's headers, and what the faces are made of (the
MicroPython face, the ctypes face, the host faces' registration table).

A face.json maps a module's name to its face:

  about     one line: what the module is
  c         the hand MicroPython file whose globals table expands the rows
            (its dir's modmoy_<m>.c by default)
  host      the module's host face as runtime/host names it (null: the module
            exists only on a board); `binding: true` when that file's
            binding() builds the module, `lazy: true` when the face is built
            at the module's first import rather than at `import runtime`,
            `registered: false` when the host's console must not find it by
            the module's name (the face is imported by its file's)
  headers   the headers the rows' prototypes, structures and constants are
            read from, beside the face.json or under native/
  consts    "C_NAME" or "C_NAME as PY_NAME": a constant of the headers, an
            integer or a string literal, on both faces under its Python name
  when      {NAME: "C condition"}: the board builds a row whose `when` is NAME
            holds only where the condition holds (MOY_FACE_<M>_ROWS carries
            the group inside #if); the host face holds it unless it is absent
  structs   C structures the host's hand rows read: their ctypes mirrors,
            read from the native headers (tools/gen_os/structs.py)
  errors    {name: {"codes": {rc: "Exxx"}, "other": "Exxx"}}: a C answer code
            raised as OSError(errno), 0 answering; a code may raise
            "ValueError: <text>" or "MemoryError", and "other": "rc" raises
            OSError(rc), the code an errno value
  buf       {"c": structure, "free": function}: the buffer a `take` row's C
            allocates (a pointer and a length, `p` and `n`) and its free
  api       the toolkit's engine verbs (native/moy_ui's alone): tools/gen_os/ui.py's
  roles     {role: C prefix}: the role objects whose C rows (native/os_calls.json)
            both faces generate as methods, tools/gen_os/roles.py's
  rows      one per Python name, in the globals' order. `py` is its Python
            signature ("read(seq=0, n=32)"); `hand` a row both faces write by
            hand (its name and arity are still checked); otherwise `c` is the
            C function and the row adds only what its prototype cannot say:
              span     [param]: a (pointer, size_t) pair, one str or bytes
              wbuf     [param]: a (pointer, size_t) pair the C writes, a bytearray
            An array parameter (`const uint8_t addr[6]`) takes a buffer of
            exactly that many bytes.
              trunc    [param]: an integer taken modulo its width (handles)
              null     [param]: a const char * None passes as NULL
              out      {param: [field, ...] | null}: a structure answered as
                       the tuple of its fields ("field?" a truth value, a char
                       array its text), or a scalar answered as itself
              neg_out  a negative int answer is answered as itself, else the
                       one out parameter; "tuple": else (answer, out, ...)
              err      an `errors` name: the C's int answer raises
              ret      the answer's type where the prototype's is not it
                       ("bool"), or "ok": an int answer, True where it is 0
              fix      {param: C}: a C parameter the row passes as a constant (a
                       header's integer, or a string literal); the Python
                       parameters then meet the C ones by name, and a str
                       default is the C's literal
              take     {param: "text" | "bytes"}: an out buffer (the face's
                       `buf`) answered as a str or bytes and freed
              flag     [param]: an int taken as a truth value
              guard    NAME: the board's call runs between the hand file's
                       NAME_BEGIN and NAME_END (its answer kept)
              when     a `when` name: the row exists on the boards where its
                       condition holds
              noalloc  the MicroPython face allocates nothing (checked)
              doc      the row's docstring
              example  the arguments the parity script calls the row with
              absent   ["host"] or ["mp"]: the faces the row is not on

Rows without a face.json `rows` list are registered only: their faces are
still written by hand (docs/refactor_2026-11.md, phase 6). Every module bound
by hand is a key of tests/test_hand_bindings.py's HAND_WRITTEN, with its
reason: a new module has a generated face or an entry there."""

import ast
import json
import os
import re

from . import TableError, header
from . import roles as _roles
from . import structs as _structs
from .cproto import c_decl, param_name, parse, wrap
from .table import ROOT

NATIVE = os.path.join(ROOT, "native")
HOST = os.path.join(ROOT, "runtime", "host")
FACES_PY = os.path.join(HOST, "faces.py")
# Registered before any other: everything else's imports may reach it.
FIRST = ("moy_spine",)

MOD_FIELDS = {"about", "c", "host", "binding", "lazy", "registered", "headers", "consts", "structs", "errors",
              "rows", "api", "when", "roles", "buf"}
ROW_FIELDS = {"py", "hand", "c", "span", "wbuf", "ret", "neg_out", "trunc", "null", "out", "err", "noalloc", "example",
              "absent", "doc", "when", "fix", "take", "guard", "flag"}
FACES = ("host", "mp")

_UNSIGNED = {"uint8_t": 8, "uint16_t": 16, "uint32_t": 32, "unsigned": 32, "unsigned int": 32,
             "size_t": 64, "uint64_t": 64}
_SIGNED = {"int8_t": 8, "int16_t": 16, "int32_t": 32, "int": 32, "long": 64, "int64_t": 64}
_CTYPES = {"bool": "_c.c_bool", "int": "_c.c_int", "unsigned": "_c.c_uint",
           "unsigned int": "_c.c_uint", "long": "_c.c_long", "size_t": "_c.c_size_t",
           "float": "_c.c_float", "double": "_c.c_double", "char": "_c.c_char",
           "void": "None"}
for _t in ("int8_t", "int16_t", "int32_t", "int64_t", "uint8_t", "uint16_t", "uint32_t",
           "uint64_t"):
    _CTYPES[_t] = "_c.c_" + _t[:-2]


def sources():
    """[(dir, path)] of every face.json, by dir: native/<dir>, and the P4
    boards' native/p4/<dir>."""
    out = []
    for d in sorted(os.listdir(NATIVE)):
        p = os.path.join(NATIVE, d, "face.json")
        if os.path.isfile(p):
            out.append((d, p))
    for d in sorted(os.listdir(os.path.join(NATIVE, "p4"))):
        p = os.path.join(NATIVE, "p4", d, "face.json")
        if os.path.isfile(p):
            out.append(("p4/" + d, p))
    return out


def load():
    """{module: face} over every face.json, each face carrying its `dir`."""
    faces = {}
    for d, p in sources():
        with open(p) as f:
            src = json.load(f)
        for m, face in src.items():
            if m in faces:
                raise TableError("%s: %s has a face in native/%s too" % (p, m, faces[m]["dir"]))
            face = dict(face)
            face["dir"] = d
            faces[m] = face
    return faces


# ---- the headers ------------------------------------------------------------

def _strip(text):
    text = re.sub(r"/\*.*?\*/", " ", text, flags=re.S)
    return re.sub(r"//[^\n]*", "", text)


def _header(face, name):
    for base in (os.path.join(NATIVE, face["dir"]), NATIVE):
        p = os.path.join(base, name)
        if os.path.isfile(p):
            return p
    for d in sorted(os.listdir(NATIVE)):
        p = os.path.join(NATIVE, d, name)
        if os.path.isfile(p):
            return p
    raise TableError("%s: no header %s" % (face["dir"], name))


class Headers:
    """A module's headers: their prototypes, structures and constants."""

    def __init__(self, face):
        self.text = ""
        for h in face.get("headers", []):
            with open(_header(face, h)) as f:
                self.text += _strip(f.read()).replace("\\\n", " ") + "\n"
        self.flat = re.sub(r"\s+", " ", re.sub(r"^\s*#[^\n]*$", " ", self.text, flags=re.M))
        self._values = None

    def proto(self, fn):
        m = re.search(r"(?:^|[;}])\s*((?:extern\s+)?[A-Za-z_][\w\s\*]*?[\s\*])%s\s*\(([^;{)]*(?:\([^)]*\)[^;{)]*)*)\)\s*;"
                      % re.escape(fn), self.flat)
        if not m:
            raise TableError("no prototype of %s in the face's headers" % fn)
        ret = m.group(1).replace("extern", "").strip()
        return parse("%s %s(%s)" % (ret, fn, m.group(2).strip()))

    def struct(self, name):
        m = re.search(r"typedef\s+struct\s*\w*\s*\{([^{}]*)\}\s*%s\s*;" % re.escape(name), self.flat)
        if not m:
            raise TableError("no structure %s in the face's headers" % name)
        fields = []
        for decl in m.group(1).split(";"):
            decl = decl.strip()
            if not decl:
                continue
            dm = re.match(r"((?:const\s+)?(?:unsigned\s+)?[A-Za-z_]\w*\s*\**)\s*(.*)$", decl)
            ctype = dm.group(1).replace(" ", "") if "*" in dm.group(1) else dm.group(1).strip()
            for nm in dm.group(2).split(","):
                nm = nm.strip()
                am = re.match(r"(\w+)\s*(?:\[([^\]]+)\])?$", nm)
                fields.append((am.group(1), ctype, am.group(2)))
        return fields

    def values(self):
        """{name: int} of the headers' integer #defines and enum members."""
        if self._values is None:
            raw = {}
            self._strings = {}
            self._fn = {m.group(1): ([p.strip() for p in m.group(2).split(",")], m.group(3))
                        for m in re.finditer(r"^[ \t]*#[ \t]*define[ \t]+([A-Z_][A-Z0-9_]*)"
                                             r"\(([^)]*)\)[ \t]+([^\n]+)$", self.text, re.M)}
            for m in re.finditer(r"^[ \t]*#[ \t]*define[ \t]+([A-Z_][A-Z0-9_]*)[ \t]+([^\n]+)$", self.text, re.M):
                raw[m.group(1)] = m.group(2).strip()
                lit = re.match(r'^"((?:[^"\\]|\\.)*)"$', raw[m.group(1)])
                if lit:
                    self._strings[m.group(1)] = lit.group(1)
            for m in re.finditer(r"\benum\s*\w*\s*\{([^}]*)\}", self.flat):
                prev = None
                for item in m.group(1).split(","):
                    item = item.strip()
                    if not item:
                        continue
                    nm, _, val = item.partition("=")
                    nm = nm.strip()
                    raw[nm] = val.strip() if val else ("0" if prev is None else "(%s) + 1" % prev)
                    prev = nm
            self._values = {}
            self._raw = raw
            for k in raw:
                try:
                    self._values[k] = self.value(k)
                except TableError:
                    pass
        return self._values

    def strings(self):
        """{name: text} of the headers' string-literal #defines."""
        self.values()
        return self._strings

    def value(self, name, depth=0):
        if depth > 32:
            raise TableError("%s does not resolve" % name)
        return self.expr(self._raw[name], name, depth)

    def expr(self, expr, name="", depth=0):
        """The integer C expression `expr` over the headers' constants."""
        self.values()
        for _ in range(8):           # a function-like macro, its arguments substituted
            call = re.search(r"\b(%s)\s*\(([^()]*)\)" % "|".join(self._fn), expr) if self._fn else None
            if not call:
                break
            params, body = self._fn[call.group(1)]
            args = dict(zip(params, [x.strip() for x in call.group(2).split(",")]))
            body = re.sub(r"\b\w+\b", lambda w: "(%s)" % args[w.group(0)] if w.group(0) in args
                          else w.group(0), body)
            expr = expr[:call.start()] + "(%s)" % body + expr[call.end():]
        expr = re.sub(r"\((?:u?int\d+_t|unsigned|int|size_t)\)", "", expr)
        expr = re.sub(r"\b(0x[0-9a-fA-F]+|\d+)[uUlL]+\b", r"\1", expr)

        def sub(m):
            w = m.group(0)
            if w in self._raw:
                return "(%d)" % self.value(w, depth + 1)
            if re.match(r"0x[0-9a-fA-F]+$|\d+$", w):
                return w
            raise TableError("%s names %s" % (name, w))
        expr = re.sub(r"\b[A-Za-z_]\w*\b", sub, expr)
        if not re.match(r"^[\s\d()+\-*/|&<>x0-9a-fA-F]*$", expr):
            raise TableError("%s is not an integer: %s" % (name, expr))
        return int(eval(expr.replace("/", "//"), {"__builtins__": {}}))


# ---- a row ------------------------------------------------------------------

def py_sig(row):
    """(name, [(param, default text or None)], star) of a row's `py`."""
    if row.get("hand") and re.match(r"^\w+$", row["py"]):
        return row["py"], [], None
    m = re.match(r"^(\w+)\((.*)\)$", row["py"])
    if not m:
        raise TableError("not a Python signature: %r" % row["py"])
    ps, star = [], None
    for p in [x.strip() for x in m.group(2).split(",") if x.strip()]:
        if p.startswith("*"):
            star = p[1:]
            continue
        nm, eq, dflt = p.partition("=")
        ps.append((nm.strip(), dflt.strip() if eq else None))
    return m.group(1), ps, star


def _base(t):
    t = re.sub(r"\bconst\b", "", t).strip()
    return re.sub(r"\s+", " ", t)


class Row:
    """One generated row: its Python parameters joined to its C ones."""

    def __init__(self, m, face, hdr, row):
        self.m, self.row = m, row
        self.name, self.params, self.star = py_sig(row)
        if row.get("hand"):
            return
        self.ret, self.fn, cps, _ = hdr.proto(row["c"])
        span = set(row.get("span", ())) | set(row.get("wbuf", ()))
        out = row.get("out", {})
        fix, take = row.get("fix", {}), row.get("take", {})
        by_name = {nm: j for j, (nm, _) in enumerate(self.params)} if fix else None
        self.c = []        # (kind, cname, ctype, py index or None)
        self.fixed = {}    # cname: the length of an array parameter (`addr[6]`)
        i, k = 0, 0

        def at(cn):
            if by_name is None:
                return k
            if cn not in by_name:
                raise TableError("%s.%s: no Python parameter %s" % (m, self.name, cn))
            return by_name[cn]
        while i < len(cps):
            p = cps[i]
            cn = param_name(p)
            ct = p[:p.rfind(cn)].strip()
            if cn in fix or cn in take:
                self.c.append(("fix" if cn in fix else "take", cn, ct, None))
                i += 1
                continue
            if cn in span:
                ln = cps[i + 1]
                j = at(cn)
                self.c.append(("wbuf" if cn in row.get("wbuf", ()) else "span", cn, ct, j))
                self.c.append(("len", param_name(ln), ln[:ln.rfind(param_name(ln))].strip(), j))
                k += 1
                i += 2
                continue
            fixed = re.search(r"\[(\d+)\]\s*$", p)
            if fixed:
                self.fixed[cn] = int(fixed.group(1))
                self.c.append(("fixed", cn, ct + " *", at(cn)))
                k += 1
                i += 1
                continue
            if cn in out:
                self.c.append(("out", cn, ct, None))
            else:
                self.c.append(("in", cn, ct, at(cn)))
                k += 1
            i += 1
        if k != len(self.params) or self.star:
            raise TableError("%s.%s: %d Python parameters for %d C ones"
                             % (m, self.name, len(self.params), k))
        self.outs = [(cn, ct) for kind, cn, ct, _ in self.c if kind == "out"]
        if self.outs and _base(self.ret) != "void" and not (row.get("err") or row.get("neg_out")):
            raise TableError("%s.%s: an out parameter and an answer" % (m, self.name))
        if take and (len(take) != 1 or self.outs or _base(self.ret) != "int"
                     or not row.get("err")):
            raise TableError("%s.%s: a take row answers one buffer, its int raising"
                             % (m, self.name))
        if row.get("guard") and _base(self.ret) == "void":
            raise TableError("%s.%s: a guarded row answers" % (m, self.name))
        for cn in (list(row.get("trunc", ())) + list(row.get("null", ())) + list(span) + list(fix)
                   + list(take) + list(row.get("flag", ()))):
            if cn not in [x[1] for x in self.c]:
                raise TableError("%s.%s: no parameter %s" % (m, self.name, cn))

    @property
    def arity(self):
        need = sum(1 for _, d in self.params if d is None)
        return need, len(self.params), bool(self.star)


def rows(m, face, hdr=None):
    hdr = hdr or Headers(face)
    out = []
    for r in face.get("rows", []):
        bad = set(r) - ROW_FIELDS
        if bad:
            raise TableError("%s: a row with %s" % (m, ", ".join(sorted(bad))))
        out.append(Row(m, face, hdr, r))
    return out


def consts(face):
    """[(C name, Python name)] of a face's constants."""
    out = []
    for c in face.get("consts", []):
        cn, _, pn = c.partition(" as ")
        out.append((cn.strip(), (pn or cn).strip()))
    return out


def names(m, face, where):
    """The Python names `where` ("host" or "mp") holds."""
    got = [r_name(r) for r in face.get("rows", []) if where not in r.get("absent", ())]
    return got + [p for _, p in consts(face)]


def generated(face):
    """Whether any of a face's rows or constants is generated."""
    return bool(face.get("consts")) or any(not r.get("hand") for r in face.get("rows", []))


def struct_names(face):
    return [s["c"] if isinstance(s, dict) else s for s in face.get("structs", [])]


_INDEX = {}


def index():
    """Every declaration of the native headers (tools/gen_os/structs.py), read once."""
    if "ix" not in _INDEX:
        _INDEX["ix"] = _structs.Index(defines=_structs.host_defines())
    return _INDEX["ix"]


def mirrors(face, extra=()):
    """The face's structures' ctypes mirrors, dependencies first; `extra` the
    structures its generated rows answer."""
    names = struct_names(face)
    return _structs.Mirrors(face.get("structs", []) + [s for s in extra if s not in names],
                            index())


def r_name(r):
    return py_sig(r)[0]


def check(faces=None):
    faces = load() if faces is None else faces
    for m, face in faces.items():
        bad = set(face) - MOD_FIELDS - {"dir"}
        if bad:
            raise TableError("%s: %s" % (m, ", ".join(sorted(bad))))
        if "rows" not in face:
            continue
        hdr = Headers(face)
        rs = rows(m, face, hdr)
        seen = set()
        for r in rs:
            if r.name in seen:
                raise TableError("%s.%s twice" % (m, r.name))
            seen.add(r.name)
            for a in r.row.get("absent", ()):
                if a not in FACES:
                    raise TableError("%s.%s: absent on %s" % (m, r.name, a))
            if r.row.get("err") and r.row["err"] not in face.get("errors", {}):
                raise TableError("%s.%s: no errors %s" % (m, r.name, r.row["err"]))
        vals = hdr.values()
        for r in rs:
            if r.row.get("when") and r.row["when"] not in face.get("when", {}):
                raise TableError("%s.%s: no when %s" % (m, r.name, r.row["when"]))
        for cn, pn in consts(face):
            if cn in hdr.strings():
                if pn in seen:
                    raise TableError("%s: %s is a row and a constant" % (m, pn))
                continue
            if cn not in vals:
                raise TableError("%s: no integer constant %s in its headers" % (m, cn))
            if not -(1 << 30) <= vals[cn] < (1 << 30):
                raise TableError("%s: %s is no small int on a board (MP_ROM_INT): %#x"
                                 % (m, cn, vals[cn]))
            if pn in seen:
                raise TableError("%s: %s is a row and a constant" % (m, pn))
    return faces


# ---- the MicroPython face ---------------------------------------------------

def _mp_in(ct, cn, row, o):
    b = _base(ct)
    if cn in row.get("flag", ()):
        return "mp_obj_is_true(%s)" % o
    if b in ("char *",) or b == "char*":
        if cn in row.get("null", ()):
            return "%s == mp_const_none ? NULL : mp_obj_str_get_str(%s)" % (o, o)
        return "mp_obj_str_get_str(%s)" % o
    if b == "bool":
        return "mp_obj_is_true(%s)" % o
    if b in ("float", "double"):
        return "(%s)mp_obj_get_float(%s)" % (b, o)
    if b in _UNSIGNED or b in _SIGNED:
        get = "mp_obj_get_int_truncated" if cn in row.get("trunc", ()) else "mp_obj_get_int"
        return "(%s)%s(%s)" % (b, get, o)
    raise TableError("no MicroPython argument for %s %s" % (ct, cn))


def _mp_val(ct, v):
    b = _base(ct)
    if b in ("char *", "char*"):
        return "%s ? mp_obj_new_str_from_cstr(%s) : mp_const_none" % (v, v)
    if b == "bool":
        return "mp_obj_new_bool(%s)" % v
    if b in ("float", "double"):
        return "mp_obj_new_float(%s)" % v
    if b in ("uint8_t", "uint16_t", "int8_t", "int16_t"):
        return "MP_OBJ_NEW_SMALL_INT(%s)" % v
    if b == "uint64_t":
        return "mp_obj_new_int_from_ull(%s)" % v
    if b in ("int64_t", "long"):
        return "mp_obj_new_int_from_ll(%s)" % v
    if b in _UNSIGNED:
        return "mp_obj_new_int_from_uint(%s)" % v
    if b in _SIGNED:
        return "mp_obj_new_int(%s)" % v
    raise TableError("no MicroPython answer for %s" % ct)


def _c_default(d, ct):
    if d[:1] in ("'", '"'):
        return json.dumps(ast.literal_eval(d))
    if d == "None":
        return "NULL" if "*" in ct else "0"
    if d in ("True", "False"):
        return "true" if d == "True" else "false"
    return d


def _take_key(r):
    """What a take row's call is, past its arguments: rows sharing it share a helper."""
    if r.row.get("hand") or not r.row.get("take") or r.row.get("when"):
        return None
    return (r.fn, r.row.get("guard"), r.row["err"], tuple(r.row["take"].items()))


def _mp_take(m, face, r, name):
    """The helper the take rows sharing r's call call: the call, its guard, its
    raise and the buffer taken."""
    ps = []
    for kind, cn, ct, _ in r.c:
        if kind != "take":
            ps.append(ct + ("" if ct.endswith("*") else " ") + cn)
    (cn, how), = r.row["take"].items()
    call = [("&" + c) if kind == "take" else c for kind, c, _, _ in r.c]
    o = c_decl("static mp_obj_t " + name, ps, " {")
    o.append("    %s %s = { NULL, 0 };" % (face["buf"]["c"], cn))
    callx = "%s(%s)" % (r.fn, ", ".join(call))
    if r.row.get("guard"):
        g = r.row["guard"]
        o += ["    %s r_ = 0;" % _base(r.ret), "    %s_BEGIN" % g, "    r_ = %s;" % callx,
              "    %s_END" % g]
        callx = "r_"
    o += ["    face_raise_%s(%s);" % (r.row["err"], callx),
          "    return face_%s_take(&%s, %d);" % (m, cn, how == "text"), "}", ""]
    return o


def _mp_row(m, face, hdr, r, helper=None):
    fn = "face_%s_%s" % (m, r.name)
    need, total, _ = r.arity
    var = total > 3 or need != total
    o = []
    if var:
        o.append("static mp_obj_t %s(size_t n_args, const mp_obj_t *argv) {" % fn)
        arg = ["argv[%d]" % i for i in range(total)]
    else:
        o.append("static mp_obj_t %s(%s) {" % (fn, ", ".join("mp_obj_t a%d" % i for i in range(total))
                                               or "void"))
        arg = ["a%d" % i for i in range(total)]
    call = []
    for kind, cn, ct, k in r.c:
        b = _base(ct)
        if kind == "span" and b == "char *":
            o.append("    size_t %s_n;" % cn)
            o.append("    const char *%s = mp_obj_str_get_data(%s, &%s_n);" % (cn, arg[k], cn))
            call.append(cn)
        elif kind == "fixed":
            o.append("    mp_buffer_info_t %s_b;" % cn)
            o.append("    mp_get_buffer_raise(%s, &%s_b, MP_BUFFER_READ);" % (arg[k], cn))
            o.append("    if (%s_b.len != %d) {" % (cn, r.fixed[cn]))
            o.append('        mp_raise_ValueError(MP_ERROR_TEXT("%s is %d bytes"));' % (cn, r.fixed[cn]))
            o.append("    }")
            call.append("(%s)%s_b.buf" % (ct, cn))
        elif kind in ("span", "wbuf"):
            o.append("    mp_buffer_info_t %s_b;" % cn)
            o.append("    mp_get_buffer_raise(%s, &%s_b, MP_BUFFER_%s);"
                     % (arg[k], cn, "READ" if kind == "span" else "WRITE"))
            n = _elem(b)
            o.append("    size_t %s_n = %s_b.len%s;" % (cn, cn, "" if n == 1 else " / %d" % n))
            call.append("(%s)%s_b.buf" % (ct, cn))
        elif kind == "len":
            call.append("(%s)%s_n" % (_base(ct), r.c[r.c.index((kind, cn, ct, k)) - 1][1]))
        elif kind == "out":
            st = b.rstrip("*").strip()
            o.append("    %s %s%s;" % (st, cn, " = 0" if st in _UNSIGNED or st in _SIGNED else ""))
            call.append("&" + cn)
        elif kind == "fix":
            call.append(r.row["fix"][cn])
        elif kind == "take":
            if not helper:
                o.append("    %s %s = { NULL, 0 };" % (face["buf"]["c"], cn))
                call.append("&" + cn)
        else:
            dflt = r.params[k][1]
            expr = _mp_in(ct, cn, r.row, arg[k])
            if var and k >= need:
                expr = "n_args > %d ? %s : %s" % (k, expr, _c_default(dflt, b))
            if "*" in b:
                o.append("    const char *%s = %s;" % (cn, expr))
            else:
                o.append("    %s %s = %s;" % (b, cn, expr))
            call.append(cn)
    callx = "%s(%s)" % (r.fn, ", ".join(call))
    ret = r.row.get("ret") or _base(r.ret)
    err = r.row.get("err")
    guard = r.row.get("guard")
    if guard and not helper:
        o += ["    %s r_ = 0;" % _base(r.ret), "    %s_BEGIN" % guard, "    r_ = %s;" % callx,
              "    %s_END" % guard]
        callx = "r_"
    if helper:
        o += c_decl("    return " + helper, call, ";")
    elif r.row.get("take"):
        (cn, how), = r.row["take"].items()
        o.append("    face_raise_%s(%s);" % (err, callx))
        o.append("    return face_%s_take(&%s, %d);" % (m, cn, how == "text"))
    elif err:
        o.append("    face_raise_%s(%s);" % (err, callx))
    elif ret == "ok":
        o.append("    return mp_obj_new_bool(%s == 0);" % callx)
    elif r.row.get("neg_out") == "tuple":
        o.append("    int rc_ = %s;" % callx)
        o.append("    if (rc_ < 0) {")
        o.append("        return MP_OBJ_NEW_SMALL_INT(rc_);")
        o.append("    }")
        o.append("    mp_obj_t t_[%d] = { MP_OBJ_NEW_SMALL_INT(rc_), %s };" % (
            len(r.outs) + 1, ", ".join(_mp_val(_base(ct).rstrip("*").strip(), cn)
                                       for cn, ct in r.outs)))
        o.append("    return mp_obj_new_tuple(%d, t_);" % (len(r.outs) + 1))
    elif r.row.get("neg_out"):
        o.append("    int rc_ = %s;" % callx)
        o.append("    return mp_obj_new_int(rc_ < 0 ? rc_ : (mp_int_t)%s);" % r.outs[0][0])
    elif ret == "void" or r.outs:
        o.append("    %s;" % callx)
    elif ret in ("char *", "char*"):
        o.append("    const char *r_ = %s;" % callx)
        o.append("    return %s;" % _mp_val(ret, "r_"))
    else:
        o.append("    return %s;" % _mp_val(ret, callx))
    if r.outs and not r.row.get("neg_out"):
        if len(r.outs) != 1:
            raise TableError("%s.%s: one out parameter" % (m, r.name))
        cn, ct = r.outs[0]
        fields = r.row["out"][cn]
        st = _base(ct).rstrip("*").strip()
        if fields is None:
            o.append("    return %s;" % _mp_val(st, cn))
        else:
            types = {f: (t, n) for f, t, n in hdr.struct(st)}
            o.append("    mp_obj_t t_[%d] = {" % len(fields))
            for f in fields:
                v = "%s.%s" % (cn, f.rstrip("?"))
                t, n = types[f.rstrip("?")]
                if f.endswith("?"):
                    o.append("        mp_obj_new_bool(%s)," % v)
                elif n and t == "char":
                    o.append("        mp_obj_new_str(%s, strlen(%s))," % (v, v))
                else:
                    o.append("        %s," % _mp_val(t, v))
            o.append("    };")
            o.append("    return mp_obj_new_tuple(%d, t_);" % len(fields))
    elif (err or ret == "void") and not r.row.get("take"):
        o.append("    return mp_const_none;")
    o.append("}")
    if var:
        o.append("static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(%s_obj, %d, %d, %s);"
                 % (fn, need, total, fn))
    else:
        o.append("static MP_DEFINE_CONST_FUN_OBJ_%d(%s_obj, %s);" % (total, fn, fn))
    return o


def mp_path(m, face):
    return os.path.join(NATIVE, face["dir"], "modmoy_%s_face.h" % m[4:] if m.startswith("moy_")
                        else "mod%s_face.h" % m)


def hand_c(m, face):
    return os.path.join(NATIVE, face["dir"], face.get("c") or "modmoy_%s.c" % m[4:])


def gen_mp(m, face):
    hdr = Headers(face)
    rs = rows(m, face, hdr)
    o = [header("native/%s/face.json" % face["dir"], "//"),
         "// %s's generated rows, included by %s: MOY_FACE_%s_ROWS is their"
         % (m, os.path.basename(hand_c(m, face)), m.upper()),
         "// part of its globals table.", "", '#include "py/runtime.h"']
    if face.get("errors"):
        o.append('#include "py/mperrno.h"')
    o.append("")
    for name, e in sorted(face.get("errors", {}).items()):
        o.append("static void face_raise_%s(int rc) {" % name)
        o.append("    switch (rc) {")
        o.append("    case 0: return;")
        for rc, eno in e["codes"].items():
            if eno.startswith("ValueError: "):
                o.append('    case %s: mp_raise_ValueError(MP_ERROR_TEXT("%s"));' % (rc, eno[12:]))
            elif eno == "MemoryError":
                o.append("    case %s: mp_raise_type(&mp_type_MemoryError);" % rc)
            else:
                o.append("    case %s: mp_raise_OSError(MP_%s);" % (rc, eno))
        o.append("    default: mp_raise_OSError(%s);" % ("rc" if e["other"] == "rc"
                                                        else "MP_" + e["other"]))
        o.append("    }")
        o.append("}")
        o.append("")
    if face.get("buf"):
        b = face["buf"]
        o += ["// A buffer the C allocated as a str (`text`) or bytes, freed either way.",
              "static mp_obj_t face_%s_take(%s *b, int text) {" % (m, b["c"]),
              "    mp_obj_t o;", "    nlr_buf_t nlr;", "    if (nlr_push(&nlr) == 0) {",
              "        o = text ? mp_obj_new_str(b->p, b->n) "
              ": mp_obj_new_bytes((const byte *)b->p, b->n);",
              "        nlr_pop();", "    } else {", "        %s(b);" % b["free"],
              "        nlr_jump(nlr.ret_val);", "    }", "    %s(b);" % b["free"], "    return o;",
              "}", ""]
    mp = [r for r in rs if not r.row.get("hand") and "mp" not in r.row.get("absent", ())]
    strs = hdr.strings()
    for cn, pn in consts(face):
        if cn in strs:
            o.append("static MP_DEFINE_STR_OBJ(face_%s_%s_obj, %s);" % (m, pn, cn))
    if any(cn in strs for cn, _ in consts(face)):
        o.append("")
    keys = [_take_key(r) for r in mp]
    helper = {}
    for r, key in zip(mp, keys):
        if key is not None and keys.count(key) > 1 and key not in helper:
            helper[key] = "face_%s_%s_take" % (m, r.fn)
            o += _mp_take(m, face, r, helper[key])
    for r, key in zip(mp, keys):
        if not r.row.get("when"):
            o += _mp_row(m, face, hdr, r, helper.get(key))
            o.append("")
    ent = []
    for r in mp:
        if not r.row.get("when"):
            ent.append("    { MP_ROM_QSTR(MP_QSTR_%s), MP_ROM_PTR(&face_%s_%s_obj) }"
                       % (r.name, m, r.name))
    for cn, pn in consts(face):
        if cn in strs:
            ent.append("    { MP_ROM_QSTR(MP_QSTR_%s), MP_ROM_PTR(&face_%s_%s_obj) }" % (pn, m, pn))
        else:
            ent.append("    { MP_ROM_QSTR(MP_QSTR_%s), MP_ROM_INT(%s) }" % (pn, cn))
    for name, cond in face.get("when", {}).items():
        group = [r for r in mp if r.row.get("when") == name]
        macro = "MOY_FACE_%s_ROWS_%s" % (m.upper(), name.upper())
        o.append("#if %s" % cond)
        o.append("")
        for r in group:
            o += _mp_row(m, face, hdr, r)
            o.append("")
        o.append("#define %s \\" % macro)
        rows_ = ["    { MP_ROM_QSTR(MP_QSTR_%s), MP_ROM_PTR(&face_%s_%s_obj) }"
                 % (r.name, m, r.name) for r in group]
        o += [e + ", \\" for e in rows_[:-1]] + [rows_[-1] + ","]
        o += ["#else", "#define %s" % macro, "#endif", ""]
        ent.append("    " + macro)
    o.append("#define MOY_FACE_%s_ROWS \\" % m.upper())
    # A group's macro brings its own commas.
    tail = ["" if e.strip().startswith("MOY_FACE_") else "," for e in ent]
    o += ["%s%s \\" % (e, t) for e, t in zip(ent[:-1], tail[:-1])]
    o += [ent[-1] + tail[-1], ""]
    return "\n".join(o)


# ---- the ctypes face --------------------------------------------------------

def _elem(ptr):
    """The bytes of what pointer type `ptr` points at."""
    inner = _base(ptr).rstrip("*").strip()
    if inner in ("char", "void", "uint8_t", "int8_t"):
        return 1
    return {"int16_t": 2, "uint16_t": 2, "int32_t": 4, "uint32_t": 4, "float": 4}[inner]


def _ctype(ct, structs=(), buf=False):
    b = _base(ct)
    if buf:
        return "_c.c_char_p" if b == "char *" else "_c.c_void_p"
    if b.endswith("*"):
        inner = b.rstrip("*").strip()
        if inner == "char":
            return "_c.c_char_p"
        if inner in structs:
            return "_c.POINTER(%s)" % inner
        if inner in ("void", "uint8_t"):
            return "_c.c_void_p" if inner == "void" else "_c.c_char_p"
        return "_c.POINTER(%s)" % _CTYPES[inner]
    if b not in _CTYPES:
        raise TableError("no ctypes type for %s" % ct)
    return _CTYPES[b]


def host_path(m, face):
    return os.path.join(HOST, "%s_face.py" % m)


def _py_in(ct, cn, row, v):
    b = _base(ct)
    if cn in row.get("flag", ()):
        return "1 if %s else 0" % v
    if b in ("char *", "char*"):
        if cn in row.get("null", ()):
            return "None if %s is None else _f.cstr(%s)" % (v, v)
        return "_f.cstr(%s)" % v
    if b == "bool":
        return "bool(%s)" % v
    if b in ("float", "double"):
        return "float(%s)" % v
    return "int(%s)" % v


def _py_val(ct, v):
    b = _base(ct)
    if b in ("char *", "char*"):
        return "_f.text(%s)" % v
    if b == "bool":
        return "bool(%s)" % v
    return v


def _py_row(m, face, hdr, r, structs):
    ps = []
    for nm, d in r.params:
        ps.append(nm if d is None else "%s=%s" % (nm, d))
    o = ["def %s(%s):" % (r.name, ", ".join(ps))]
    if r.row.get("doc"):
        o.append('    """%s"""' % r.row["doc"])
    call = []
    for kind, cn, ct, k in r.c:
        v = r.params[k][0] if k is not None else None
        if kind == "span":
            o.append("    %s_b = _f.span(%s)" % (v, v))
            call.append("%s_b" % v)
            size = "len(%s_b)" % v
        elif kind == "fixed":
            o.append("    %s_b = _f.span(%s)" % (v, v))
            o.append("    if len(%s_b) != %d:" % (v, r.fixed[cn]))
            o.append('        raise ValueError("%s is %d bytes")' % (cn, r.fixed[cn]))
            call.append("%s_b" % v)
        elif kind == "wbuf":
            o.append("    %s_b = _f.wbuf(%s)" % (v, v))
            call.append("%s_b" % v)
            size = "len(%s_b)" % v
        elif kind == "len":
            n = _elem(_base(r.c[r.c.index((kind, cn, ct, k)) - 1][2]))
            call.append(size if n == 1 else "%s // %d" % (size, n))
        elif kind == "out":
            st = _base(ct).rstrip("*").strip()
            o.append("    %s = %s()" % (cn, st if st in structs else _CTYPES[st]))
            call.append("_c.byref(%s)" % cn)
        elif kind == "fix":
            fx = r.row["fix"][cn]
            call.append(repr(ast.literal_eval(fx).encode()) if fx[:1] == '"'
                        else str(hdr.values()[fx]))
        elif kind == "take":
            o.append("    %s = %s()" % (cn, face["buf"]["c"]))
            call.append("_c.byref(%s)" % cn)
        else:
            call.append(_py_in(ct, cn, r.row, v))
    callx = "_lib().%s(%s)" % (r.fn, ", ".join(call))
    ret = r.row.get("ret") or _base(r.ret)
    if r.row.get("take"):
        (cn, how), = r.row["take"].items()
        o.append("    _raise_%s(%s)" % (r.row["err"], callx))
        o.append("    return _f.take(_lib().%s, %s)%s" % (face["buf"]["free"], cn,
                                                       ".decode()" if how == "text" else ""))
        return o
    if r.row.get("err"):
        o.append("    _raise_%s(%s)" % (r.row["err"], callx))
    elif ret == "ok":
        o.append("    return %s == 0" % callx)
    elif r.row.get("neg_out") == "tuple":
        o.append("    rc_ = %s" % callx)
        o.append("    return rc_ if rc_ < 0 else (rc_, %s)" % ", ".join("%s.value" % cn for cn, _ in r.outs))
        return o
    elif r.row.get("neg_out"):
        o.append("    rc_ = %s" % callx)
        o.append("    return rc_ if rc_ < 0 else %s.value" % r.outs[0][0])
        return o
    elif ret == "void" or r.outs:
        o.append("    %s" % callx)
    else:
        o.append("    return %s" % _py_val(ret, callx))
    if r.outs:
        cn, ct = r.outs[0]
        fields = r.row["out"][cn]
        if fields is None:
            o.append("    return %s.value" % cn)
        else:
            types = {f: (t, n) for f, t, n in hdr.struct(_base(ct).rstrip("*").strip())}

            def val(f):
                v = "%s.%s" % (cn, f.rstrip("?"))
                t, n = types[f.rstrip("?")]
                if f.endswith("?"):
                    return "bool(%s)" % v
                return '%s.decode("utf-8")' % v if n and t == "char" else v
            items = [val(f) for f in fields]
            line = "    return (%s%s)" % (", ".join(items), "," if len(fields) == 1 else "")
            o += _fold_py(line) if len(line) > 99 else [line]
    return o


def _fold_py(line):
    """A `return (a, b, ...)` line past 99 wrapped under its first item."""
    k = line.index("(") + 1
    out, cur = [], line[:k]
    for x in line[k:].split(", "):
        if len(cur) + len(x) + 2 > 99 and cur.strip() != line[:k].strip():
            out.append(cur.rstrip())
            cur = " " * k
        cur += x + ", "
    out.append(cur[:-2])
    return out


def _errnos(e):
    """Whether an `errors` entry names an errno (a table the host face keeps)."""
    return e["other"] != "rc" or any(x.startswith("E") for x in e["codes"].values())


def _structs_needed(rs, face=None):
    want = []
    for r in rs:
        for cn, ct in getattr(r, "outs", []):
            st = _base(ct).rstrip("*").strip()
            if r.row["out"][cn] is not None and st not in want:
                want.append(st)
        if r.row.get("take") and not r.row.get("hand") and face["buf"]["c"] not in want:
            want.append(face["buf"]["c"])
    return want


def gen_host(m, face):
    hdr = Headers(face)
    rs = rows(m, face, hdr)
    mir = mirrors(face, _structs_needed(rs, face))
    structs = mir.done
    vals = hdr.values()
    gen = [r for r in rs if not r.row.get("hand") and "host" not in r.row.get("absent", ())]
    public = [r.name for r in gen] + [p for _, p in consts(face)]
    what = "rows" if gen or consts(face) else "structures"
    o = [header("native/%s/face.json" % face["dir"], "#"),
         '"""%s\'s generated %s over ctypes: %s.' % (m, what, face["about"]),
         'The module `%s` is runtime/host/%s.py."""' % (m, face["host"]),
         ""] + (["import ctypes as _c"] if gen or structs or face.get("roles") else []) + (
         ["import errno as _errno"] if any(map(_errnos, face.get("errors", {}).values()))
         else []) + (
         ["", "import face_ctypes as _f"] if gen or face.get("errors") else []) + (
         ["", "import face_errors as _E"] if face.get("roles") else []) + [
         "", "__all__ = ("] + wrap(['"%s"' % n for n in public]) + [")", ""]
    for cn, pn in consts(face):
        if cn in hdr.strings():
            o.append("%s = \"%s\"" % (pn, hdr.strings()[cn]))
        else:
            o.append("%s = %d" % (pn, vals[cn]))
    if consts(face):
        o.append("")
    o.append("")
    o += mir.emit()
    for name, e in sorted(face.get("errors", {}).items()):
        if _errnos(e):
            o.append("_%s = {" % name.upper())
            o += wrap(["%d: _errno.%s" % (vals[rc] if rc in vals else int(rc), eno)
                       for rc, eno in e["codes"].items() if eno.startswith("E")])
            o.append("}")
        o += ["", "", "def _raise_%s(rc):" % name]
        if e["other"] != "rc":
            o += ["    if rc:",
                  "        _f.raise_errno(_%s.get(rc, _errno.%s))" % (name.upper(), e["other"]),
                  "", ""]
            continue
        o += ["    if not rc:", "        return"]
        for rc, eno in e["codes"].items():
            v = vals[rc] if rc in vals else int(rc)
            if eno.startswith("ValueError: "):
                o += ["    if rc == %d:" % v, '        raise ValueError("%s")' % eno[12:]]
            elif eno == "MemoryError":
                o += ["    if rc == %d:" % v, "        raise MemoryError()"]
        eno = "_%s.get(rc, rc)" % name.upper() if _errnos(e) else "rc"
        o += ["    _f.raise_errno(%s)" % eno,
              "", ""]
    if face.get("roles"):
        o += _roles.host_rows(face, hdr) + [""]
    if not gen:
        while o[-1] == "":
            o.pop()
        return "\n".join(o) + "\n"
    o.append("_SIGS = (")
    seen = set()
    for r in gen:
        if r.fn in seen:
            continue
        seen.add(r.fn)
        args = []
        for kind, cn, ct, k in r.c:
            args.append(_ctype(ct, structs, kind in ("span", "wbuf", "fixed")))
        ret = "_c.c_int" if r.row.get("err") else _ctype(r.ret, structs)
        o.append('    ("%s", [%s], %s),' % (r.fn, ", ".join(args), ret))
    if face.get("buf") and any(r.row.get("take") for r in gen):
        o.append('    ("%s", [_c.c_void_p], None),' % face["buf"]["free"])
    o += [")", "", "", "def _lib():", "    return _f.lib(_SIGS)", "", ""]
    for r in gen:
        o += _py_row(m, face, hdr, r, structs)
        o += ["", ""]
    while o[-1] == "":
        o.pop()
    o.append("")
    return "\n".join(o)


# ---- the host faces' registration table -------------------------------------

def gen_faces_py(faces):
    reg = [m for m in faces if faces[m].get("host") and faces[m].get("registered", True)]
    order = [m for m in FIRST if m in reg] + sorted(m for m in reg if m not in FIRST)
    o = [header("native/*/face.json", "#"),
         '"""The host\'s face of each native module: (module, runtime/host file,',
         "binding, lazy), in registration order. runtime/__init__.py registers each as",
         "`module` and `runtime.module`, a lazy one at its first import; a binding",
         'face\'s binding() builds the module, any other face is the module."""', "",
         "FACES = ("]
    for m in order:
        f = faces[m]
        o.append('    ("%s", "%s", %s, %s),' % (m, f["host"], bool(f.get("binding")),
                                              bool(f.get("lazy"))))
    o += [")", ""]
    return "\n".join(o)


def outputs(faces=None):
    faces = check(faces)
    out = [(FACES_PY, gen_faces_py(faces))]
    for m, face in faces.items():
        if not generated(face):
            continue
        out.append((mp_path(m, face), gen_mp(m, face)))
    for m, face in faces.items():
        if face.get("roles"):
            out.append((mp_path(m, face), _roles.gen_mp(m, face, Headers(face))))
    for m, face in faces.items():
        if face.get("host") and (generated(face) or face.get("structs")) and "api" not in face:
            out.append((host_path(m, face), gen_host(m, face)))
    return out


# ---- the parity script ------------------------------------------------------

_SCRIPT = '''
import json
def _norm(v):
    if isinstance(v, (tuple, list)):
        return [_norm(x) for x in v]
    if isinstance(v, (bytes, bytearray)):
        return {"bytes": list(v)}
    if isinstance(v, float):
        return round(v, 3)
    return v
def _call(name, args, noalloc):
    f = getattr(M, name)
    try:
        r = ["ok", _norm(f(*args))]
    except Exception as e:
        r = ["raise", type(e).__name__, e.args[0] if isinstance(e, OSError) and e.args else None]
    grew = 0
    bufs = [_norm(a) for a in args if isinstance(a, bytearray)]
    if noalloc and MP:
        import gc
        f(*args)
        gc.collect()
        a = gc.mem_alloc()
        f(*args)
        grew = gc.mem_alloc() - a
    print(json.dumps([name, r, grew, bufs]))
'''


def _lit(v):
    """An example argument as Python source: {"bytes": [..]} and
    {"bytearray": n} are those objects."""
    if isinstance(v, dict):
        if "bytes" in v:
            return "bytes(%r)" % (v["bytes"],)
        return "bytearray(%d)" % v["bytearray"]
    return repr(v)


def parity_script(m, face):
    """The script both runtimes run, `M` the module and `MP` true on
    MicroPython: each constant's value, then each generated row on its
    example, one JSON line each."""
    o = [_SCRIPT]
    for _, pn in consts(face):
        o.append("print(json.dumps([%r, getattr(M, %r, 'ABSENT')]))" % (pn, pn))
    for r in face.get("rows", []):
        if r.get("hand") or "example" not in r or r.get("absent") or r.get("when"):
            continue
        o.append("_call(%r, [%s], %s)" % (r_name(r), ", ".join(_lit(a) for a in r["example"]),
                                          bool(r.get("noalloc"))))
    return "\n".join(o) + "\n"
