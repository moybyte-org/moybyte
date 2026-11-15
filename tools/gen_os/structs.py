"""The C structures a host face mirrors over ctypes, read from the native
headers' declarations: each face.json's `structs` names them, and the
module's runtime/host/<m>_face.py holds one ctypes class each, named as C
names it. tests/test_struct_mirrors.py compiles a probe of every mirror's
size and field offsets against the host's C.

A `structs` entry is a C type name, or {"c": name, "addr": [field, ...],
"header": path}: `addr` the `char *` fields the C reads as bytes at an
address rather than as NUL-terminated text (c_void_p, not c_char_p),
`header` the one header under native/ to read it from where several declare
it (else the first that does). A structure a mirror holds by
value is mirrored with it; a pointer to a structure is POINTER of its mirror
when the face names that structure too, else c_void_p. A field that is a
function pointer is a CFUNCTYPE of its prototype: a pointer parameter is
POINTER of its scalar or of a structure the face names, a pointer to a
pointer POINTER(c_void_p), a `char *` c_char_p, any other c_void_p, as a
pointer it answers is.

The headers searched are every .h under native/ but the vendored runtimes'
trees, the face's own headers first; their integer constants size the
arrays, with the host build's own -D values (runtime/host/host_native.py's
CFLAGS) over a header's default."""

import keyword
import os
import re
import warnings

from . import TableError
from .table import ROOT

NATIVE = os.path.join(ROOT, "native")
SKIP = ("wamr", "micropython", "lua", "dash", "mbedtls", "tlsf")

WAMR = os.path.join(NATIVE, "moy_wasm", "wamr")
WAMR_INC = os.path.join(WAMR, "core", "iwasm", "include")

SCALARS = {
    "bool": "c_bool", "_Bool": "c_bool", "char": "c_char", "signed char": "c_byte",
    "unsigned char": "c_ubyte", "short": "c_short", "short int": "c_short",
    "unsigned short": "c_ushort", "unsigned short int": "c_ushort",
    "int": "c_int", "signed": "c_int", "signed int": "c_int",
    "unsigned": "c_uint", "unsigned int": "c_uint", "long": "c_long", "long int": "c_long",
    "unsigned long": "c_ulong", "unsigned long int": "c_ulong",
    "long long": "c_longlong", "unsigned long long": "c_ulonglong",
    "float": "c_float", "double": "c_double", "size_t": "c_size_t", "ssize_t": "c_ssize_t",
    "uintptr_t": "c_size_t", "intptr_t": "c_ssize_t", "void": None,
}
for _n in (8, 16, 32, 64):
    SCALARS["int%d_t" % _n] = "c_int%d" % _n
    SCALARS["uint%d_t" % _n] = "c_uint%d" % _n


_HEADERS = {}


def _strip(text):
    text = re.sub(r"/\*.*?\*/", " ", text, flags=re.S)
    return re.sub(r"//[^\n]*", "", text)


def _headers(first):
    """Every header under native/ but the vendored trees', and WAMR's public
    include directory."""
    out = list(first)
    for d, subs, files in os.walk(NATIVE):
        subs[:] = sorted(s for s in subs if s not in SKIP and not s.startswith("."))
        for f in sorted(files):
            p = os.path.join(d, f)
            if f.endswith(".h") and p not in out:
                out.append(p)
    for f in sorted(os.listdir(WAMR_INC)) if os.path.isdir(WAMR_INC) else ():
        if f.endswith(".h"):
            out.append(os.path.join(WAMR_INC, f))
    return out


def _close(text, at):
    """The index past the brace that closes the one at `at`."""
    depth = 0
    for i in range(at, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return i + 1
    raise TableError("an unclosed brace")


def _top(text, sep):
    """`text` split at `sep` outside braces and parentheses."""
    out, depth, cur = [], 0, ""
    for ch in text:
        if ch in "{(":
            depth += 1
        elif ch in "})":
            depth -= 1
        if ch == sep and depth == 0:
            out.append(cur)
            cur = ""
        else:
            cur += ch
    if cur.strip():
        out.append(cur)
    return out


class Index:
    """Every structure, union, enum and type alias the headers declare."""

    def __init__(self, first=(), defines=None):
        self.bodies = {}        # name -> (kind, body text, header), the first declared
        self.every = {}         # name -> every declaration of it
        self.enums = set()
        self.alias = {}         # name -> the type text it names
        self.fnptr = {}         # name -> (ret, params) of a function-pointer typedef
        self.raw = {}           # constant -> its text
        self.defines = dict(defines or {})
        self._seen = set()
        hs = _headers(first)
        _HEADERS.update((os.path.basename(p), p) for p in hs)
        for p in hs:
            with open(p, errors="replace") as f:
                self._read(f.read(), os.path.relpath(p, ROOT))

    def _read(self, text, where):
        text = self._host(_strip(text).replace("\\\n", " "))
        for m in re.finditer(r"^[ \t]*#[ \t]*define[ \t]+([A-Za-z_]\w*)[ \t]+([^\n]+)$", text, re.M):
            self.raw.setdefault(m.group(1), m.group(2).strip())
        flat = re.sub(r"^[ \t]*#[^\n]*", " ", text, flags=re.M)
        self._decls(flat, where)

    def _host(self, text):
        """`text` with the lines the host's build leaves out by its
        conditionals blanked: #if, #ifdef, #ifndef, #elif and #else over the
        host's -D values and the macros the headers define."""
        out, stack = [], []          # stack: (taking, taken, outer)
        for line in text.split("\n"):
            m = re.match(r"^\s*#\s*(ifdef|ifndef|if|elif|else|endif|define)\b(.*)$", line)
            live = all(t for t, _k, _o in stack)
            if m is None:
                out.append(line if live else "")
                continue
            d, rest = m.group(1), m.group(2).strip()
            if d in ("ifdef", "ifndef", "if"):
                if d == "if":
                    v = self._cond(rest)
                else:
                    v = (rest.split()[0] in self.defines or rest.split()[0] in self._seen)
                    v = v if d == "ifdef" else not v
                stack.append((v, v, live))
            elif d == "elif" and stack:
                _t, k, o = stack[-1]
                v = not k and self._cond(rest)
                stack[-1] = (v, k or v, o)
            elif d == "else" and stack:
                _t, k, o = stack[-1]
                stack[-1] = (not k, True, o)
            elif d == "endif" and stack:
                stack.pop()
            elif d == "define" and live:
                self._seen.add(rest.split("(")[0].split()[0] if rest else "")
            out.append(line if live and d == "define" else "")
        return "\n".join(out)

    def _cond(self, expr):
        known = self.defines

        def defined(m):
            n = m.group(1)
            return "1" if n in known or n in self._seen else "0"
        e = re.sub(r"__has_include\s*\(\s*[\"<]([^\">]+)[\">]\s*\)",
                   lambda m: "1" if _HEADERS.get(os.path.basename(m.group(1))) else "0", expr)
        e = re.sub(r"\bdefined\s*\(?\s*__has_include\s*\)?", "1", e)
        e = re.sub(r"\bdefined\s*\(?\s*(\w+)\s*\)?", defined, e)
        e = e.replace("&&", " and ").replace("||", " or ")
        e = re.sub(r"!(?!=)", " not ", e)

        def word(m):
            w = m.group(0)
            if w in ("and", "or", "not") or re.match(r"^(0x[0-9a-fA-F]+|\d+)$", w):
                return w
            try:
                return str(self.value(w))
            except (TableError, KeyError, SyntaxError, TypeError, ZeroDivisionError):
                return "0"
        e = re.sub(r"\b[A-Za-z_]\w*\b", word, e)
        e = re.sub(r"\b(0x[0-9a-fA-F]+|\d+)[uUlL]+\b", r"\1", e)
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                return bool(eval(e, {"__builtins__": {}}))
        except Exception:
            return True

    def _decls(self, flat, where):
        for m in re.finditer(r"\b(typedef\s+)?(struct|union|enum)\s*(\w*)\s*\{", flat):
            end = _close(flat, m.end() - 1)
            body = flat[m.end():end - 1]
            tail = re.match(r"\s*([^;{}]*);", flat[end:])
            kind, tag = m.group(2), m.group(3)
            names = [tag] if tag else []
            if m.group(1) and tail:
                names += [n.strip() for n in tail.group(1).split(",")
                          if re.match(r"^\w+$", n.strip())]
            for n in names:
                if kind == "enum":
                    self.enums.add(n)
                    self._enum(body)
                else:
                    self.bodies.setdefault(n, (kind, body, where))
                    self.every.setdefault(n, []).append((kind, body, where))
            if kind == "enum":
                self._enum(body)
        for m in re.finditer(r"\btypedef\s+([^;{}()]+?)\s+(\w+)\s*;", flat):
            t = re.sub(r"\s+", " ", m.group(1)).strip()
            if not t.startswith(("struct", "union", "enum")) or "{" not in t:
                self.alias.setdefault(m.group(2), t)
        for m in re.finditer(r"\btypedef\s+([^;{}()]+?)\(\s*\*\s*(\w+)\s*\)\s*\(([^;{}]*)\)\s*;",
                             flat):
            self.fnptr.setdefault(m.group(2), (m.group(1).strip(), m.group(3).strip()))

    def _enum(self, body):
        prev = None
        for item in body.split(","):
            nm, _, val = item.strip().partition("=")
            nm = nm.strip()
            if not re.match(r"^[A-Za-z_]\w*$", nm):
                continue
            self.raw.setdefault(nm, val.strip() if val else ("0" if prev is None else "(%s) + 1" % prev))
            prev = nm

    def value(self, expr, depth=0):
        """The integer C expression `expr` over the constants."""
        if depth > 32:
            raise TableError("%s does not resolve" % expr)
        expr = re.sub(r"\((?:u?int\d+_t|unsigned|int|size_t|long)\)", "", expr)
        expr = re.sub(r"\bsizeof\s*\(\s*(\w+)\s*\)", lambda m: str(self.sizeof(m.group(1))), expr)
        expr = re.sub(r"\b(0x[0-9a-fA-F]+|\d+)[uUlL]+\b", r"\1", expr)

        def sub(m):
            w = m.group(0)
            if re.match(r"0x[0-9a-fA-F]+$|\d+$", w):
                return w
            if w in self.defines:
                return "(%d)" % self.value(self.defines[w], depth + 1)
            if w in self.raw:
                return "(%d)" % self.value(self.raw[w], depth + 1)
            raise TableError("%s names %s" % (expr, w))
        out = re.sub(r"\b[A-Za-z_]\w*\b", sub, expr)
        if not re.match(r"^[\s\d()+\-*/%|&<>x0-9a-fA-F]*$", out):
            raise TableError("not an integer: %s" % expr)
        return int(eval(out.replace("/", "//"), {"__builtins__": {}}))

    def sizeof(self, t):
        sizes = {"uint8_t": 1, "int8_t": 1, "uint16_t": 2, "int16_t": 2, "uint32_t": 4,
                 "int32_t": 4, "uint64_t": 8, "int64_t": 8, "int": 4, "char": 1}
        if t in sizes:
            return sizes[t]
        raise TableError("sizeof(%s) in a mirrored array" % t)


def _base(t):
    t = re.sub(r"\b(const|volatile|_Atomic|restrict|__restrict)\b", "", t)
    return re.sub(r"\s+", " ", t).strip()


class Mirrors:
    """The ctypes classes of a face's `structs`, in declaration order."""

    def __init__(self, entries, index):
        self.ix = index
        self.addr = {}
        self.named = []
        self.where = {}         # name -> the header its mirror is read from
        for e in entries:
            if isinstance(e, dict):
                self.named.append(e["c"])
                self.addr[e["c"]] = set(e.get("addr", ()))
                if "header" in e:
                    self.where[e["c"]] = e["header"]
            else:
                self.named.append(e)
        self.done = []          # class names, dependencies first
        self.lines = {}
        self.fields = {}        # name -> [(field, C member)] the probe checks
        for n in self.named:
            self._mirror(n)

    def _mirror(self, name, inline=None, owner=None):
        if name in self.lines:
            return
        if inline is None:
            if name not in self.ix.bodies:
                raise TableError("no structure %s in the native headers" % name)
            want = self.where.get(name)
            got = [d for d in self.ix.every[name] if want is None or d[2] == "native/" + want]
            if not got:
                raise TableError("no structure %s in native/%s" % (name, want))
            kind, body, where = got[0]
            self.where[name] = where[len("native/"):]
        else:
            kind, body = inline
        self.lines[name] = None             # in progress: a pointer back is c_void_p
        fields, anon, checks, extra = [], [], [], []
        for decl in _top(body, ";"):
            decl = decl.strip()
            if not decl:
                continue
            sm = re.match(r"^(struct|union)\s*(\w*)\s*\{", decl)
            if sm:
                end = _close(decl, sm.end() - 1)
                inner = decl[sm.end():end - 1]
                rest = decl[end:].strip()
                sub = "%s_%s" % (name, rest or "u%d" % len(fields))
                sub = re.sub(r"\W", "_", sub)
                self._mirror(sub, (sm.group(1), inner), owner or name)
                if not rest:
                    anon.append(sub.rsplit("_", 1)[1])
                    fields.append((anon[-1], sub))
                    for f, member in self.fields[sub]:
                        checks.append((f, member))
                    continue
                for d in rest.split(","):
                    pn, ty = self._declarator(d.strip(), sub, name)
                    fields.append((pn, ty))
                    checks.append((pn, d.strip().split("[")[0].strip()))
                continue
            fm = re.match(r"^(.*?)\(\s*\*\s*(\w+)\s*\)\s*\((.*)\)$", decl, re.S)
            if fm:
                ty = self._fn(fm.group(1), fm.group(3))
                pn = _py(fm.group(2))
                fields.append((pn, ty))
                checks.append((pn, fm.group(2)))
                continue
            tm = re.match(r"^((?:(?:const|volatile|unsigned|signed|struct|union|enum|long|short)\s+)*"
                          r"[A-Za-z_]\w*)(.*)$", decl.strip(), re.S)
            if not tm:
                raise TableError("%s: a member %r" % (name, decl))
            for d in _top(tm.group(2), ","):
                d = d.strip()
                bits = None
                if ":" in d:
                    d, _, b = d.partition(":")
                    d, bits = d.strip(), self.ix.value(b)
                lead = re.match(r"^((?:\*|\s|\bconst\b|\bvolatile\b|\brestrict\b)*)(.*)$", d, re.S)
                stars = lead.group(1).count("*")
                d = lead.group(2).strip()
                ty = self._type(tm.group(1), stars, name, re.sub(r"[\s*]|\[.*", "", d))
                pn, ty = self._declarator(d.lstrip("* "), ty, name)
                fields.append((pn, ty) if bits is None else (pn, ty, bits))
                checks.append((pn, re.sub(r"\[.*", "", d.lstrip("* "))))
        cls = "Union" if kind == "union" else "Structure"
        o = ["class %s(_c.%s):" % (name, cls)]
        if anon:
            o.append("    _anonymous_ = (%s)" % "".join('"%s", ' % a for a in anon).rstrip(" "))
        o.append("    _fields_ = [")
        for f in fields:
            if len(f) == 3:
                o.append('        ("%s", %s, %d),' % f)
            else:
                o.append('        ("%s", %s),' % f)
        o += ["    ]", "", ""]
        self.lines[name] = o + extra
        self.fields[name] = [(pn, member) for pn, member in checks]
        self.done.append(name)

    def _declarator(self, d, ty, owner):
        m = re.match(r"^(\w+)\s*((?:\[[^\]]*\]\s*)*)$", d)
        if not m:
            raise TableError("%s: a declarator %r" % (owner, d))
        for dim in reversed(re.findall(r"\[([^\]]*)\]", m.group(2))):
            ty = "%s * %d" % (ty if " * " not in ty else "(%s)" % ty, self.ix.value(dim))
        return _py(m.group(1)), ty

    def _type(self, t, stars, owner, field=None):
        t = _base(t)
        t = re.sub(r"^(struct|union) ", "", t)
        if t.startswith("enum "):
            return "_c.c_int" if not stars else "_c.c_void_p"
        while t in self.ix.alias and t not in self.ix.bodies and t not in SCALARS:
            nxt = _base(self.ix.alias[t])
            if nxt.endswith("*"):
                stars += nxt.count("*")
                nxt = nxt.rstrip("* ").strip()
            t = re.sub(r"^(struct|union) ", "", nxt)
            if t.startswith("enum "):
                return "_c.c_int" if not stars else "_c.c_void_p"
        while t not in SCALARS and t not in self.ix.bodies and (t in self.ix.defines or t in self.ix.raw):
            t = _base(self.ix.defines.get(t, self.ix.raw.get(t)))
        if t in self.ix.fnptr:
            ret, ps = self.ix.fnptr[t]
            return self._fn(ret, ps) if stars == 0 else "_c.c_void_p"
        if stars:
            if stars == 1 and t == "char":
                return "_c.c_void_p" if field in self.addr.get(owner, ()) else "_c.c_char_p"
            if stars == 1 and t in self.ix.bodies and t in self.named:
                self._mirror(t)
                return "_c.POINTER(%s)" % t if self.lines.get(t) is not None else "_c.c_void_p"
            return "_c.c_void_p"
        if t in self.ix.enums:
            return "_c.c_int"
        if t in SCALARS and SCALARS[t]:
            return "_c." + SCALARS[t]
        if t in self.ix.bodies:
            self._mirror(t)
            return t
        raise TableError("%s: no ctypes type for %r" % (owner, t))

    def _fn(self, ret, params):
        ret = _base(ret)
        rstars = ret.count("*")
        r = "None" if ret == "void" else self._arg(ret.rstrip("* "), rstars, True)
        args = []
        ps = [p.strip() for p in _top(params, ",")]
        if ps in ([], ["void"]):
            ps = []
        for p in ps:
            p = _base(p)
            m = re.match(r"^(.*?)(\**)\s*(\w+)?\s*(\[[^\]]*\])?$", p)
            if m is None:
                raise TableError("a parameter %r" % p)
            t, st = m.group(1).strip(), len(m.group(2)) + (1 if m.group(4) else 0)
            if not t:
                t, st = m.group(3), len(m.group(2))
            elif m.group(3) and not re.match(r"^\w[\w ]*$", t):
                raise TableError("a parameter %r" % p)
            elif m.group(3) and (t + " " + m.group(3)) in SCALARS and not st:
                t = t + " " + m.group(3)
            args.append(self._arg(t, st))
        return "_c.CFUNCTYPE(%s)" % ", ".join([r] + args)

    def _arg(self, t, stars, ret=False):
        t = re.sub(r"^(struct|union) ", "", _base(t))
        while t in self.ix.alias and t not in self.ix.bodies and t not in SCALARS:
            nxt = _base(self.ix.alias[t])
            stars += nxt.count("*")
            t = nxt.rstrip("* ").strip()
        if stars:
            if stars == 1 and t == "char":
                return "_c.c_char_p"
            if stars == 1 and SCALARS.get(t) and not ret:
                return "_c.POINTER(_c.%s)" % SCALARS[t]
            if stars == 2 and not ret:
                return "_c.POINTER(_c.c_void_p)"
            if stars == 1 and t in self.named and t in self.ix.bodies and not ret:
                self._mirror(t)
                if self.lines.get(t) is not None:
                    return "_c.POINTER(%s)" % t
            return "_c.c_void_p"
        if t in self.ix.enums or t.startswith("enum "):
            return "_c.c_int"
        if t in SCALARS and SCALARS[t]:
            return "_c." + SCALARS[t]
        raise TableError("no ctypes argument for %r" % t)

    def emit(self):
        out = []
        for n in self.done:
            out += self.lines[n]
        return out


def _py(name):
    return name + "_" if keyword.iskeyword(name) else name


def host_defines():
    """The -D values the host's one library is compiled with."""
    import sys
    host = os.path.join(ROOT, "runtime", "host")
    sys.path.insert(0, host)
    try:
        import host_native
        flags = list(host_native.CFLAGS)
    finally:
        sys.path.remove(host)
    out = {}
    for f in flags:
        m = re.match(r"^-D(\w+)(?:=(.*))?$", f)
        if m:
            out[m.group(1)] = m.group(2) if m.group(2) is not None else "1"
    return out
