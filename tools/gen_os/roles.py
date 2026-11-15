"""The role objects' rows of a face with `roles` (native/moy_app's): each C row
of a named role in native/os_calls.json (an object's aside) as a method of
the role's Python object, on both faces, from the row's `in`, `ret` and
`lua_ret` and its C prototype (the face's headers):

  native/<dir>/modmoy_<m>_face.h  the MicroPython methods, and
                                  MOY_FACE_<M>_<ROLE>: the role's part of its
                                  type's locals table
  runtime/host/<m>_face.py        <Role>Rows, the same methods over ctypes, the
                                  hand role class's base, and ROLE_SIGS

A method's C call is `<prefix><verb>(a, g, ...)`: `prefix` the face's
`roles` names for the role, a and g the role object's app and grant (the
hand file's role_app; the host's self._app._p, self._g, its lib self._lib).
Its parameters are the row's `in`: an int, a str (the pointer and length of
the text, str() of anything else), and an out (with `ret` "len": the text
the C copies, sized by a first call with no buffer). Its answer is the
row's `lua_ret`, as the Lua door shapes it:

  value       the C's answer, a negative one the code it negates raised;
              with an out, the out's text
  value_nil   the int the C writes at its last parameter; None for ABSENT
  bool        value, as a truth value
  taken       whether the C answered OK; BAD is False, another code raises
  enum        lua_enum's name of the C's answer (None for a null one)

Any other code raises what the error table names (the hand file's check
and raise_rc; face_errors.exception)."""

from . import TableError, header
from .cproto import c_decl, param_name
from .table import load as load_src, params, role_of, verb_of

SHAPES = ("value", "value_nil", "bool", "taken", "enum")
_CT = {"int": "_c.c_int", "int32_t": "_c.c_int32", "uint32_t": "_c.c_uint32",
       "size_t": "_c.c_size_t", "const char *": "_c.c_char_p", "size_t *": "_c.POINTER(_c.c_size_t)",
       "int32_t *": "_c.POINTER(_c.c_int32)"}


def _base(t):
    return " ".join(t.replace("*", " *").split())


class Method:
    """One generated row: its role, verb, C function and parameters."""

    def __init__(self, face, hdr, r):
        self.r = r
        self.role, self.verb = role_of(r), verb_of(r)
        self.fn = face["roles"][self.role] + self.verb
        self.ret, _, cps, _ = hdr.proto(self.fn)
        self.cps = [(p[:p.rfind(param_name(p))].strip(), param_name(p)) for p in cps]
        self.ins = params(r)
        self.shape = r.get("lua_ret", "value")
        if self.shape not in SHAPES:
            raise TableError("%s: no role face for lua_ret %s" % (r["name"], self.shape))
        want = 2 + sum({"int": 1, "str": 2, "out": 3}[t] for _, t in self.ins)
        want += self.shape == "value_nil"
        if len(self.cps) != want:
            raise TableError("%s: %s takes %d parameters, the row %d"
                             % (r["name"], self.fn, len(self.cps), want))
        if any(t == "out" for _, t in self.ins) and r.get("ret") != "len":
            raise TableError("%s: an out with no len answer" % r["name"])

    @property
    def names(self):
        return [n for n, t in self.ins if t != "out"]

    def enum(self):
        return [(i, n) for i, n in enumerate(self.r["lua_enum"]) if n is not None]


def methods(face, hdr, src=None):
    src = src or load_src()
    out = []
    for r in src["rows"]:
        if role_of(r) in face.get("roles", {}) and r.get("server") == "c" and not r.get("object"):
            out.append(Method(face, hdr, r))
    return out


# ---- MicroPython ------------------------------------------------------------------

def _mp_body(m, fn):
    """The lines of m's MicroPython body, its C function called as `fn`."""
    o = ["    uint32_t g;", "    moy_appabi_t *a = role_app(self, &g);"]
    call, out = ["a", "g"], None
    for n, t in m.ins:
        if t == "int":
            call.append("(%s)mp_obj_get_int(%s)" % (m.cps[len(call)][0], n))
        elif t == "str":
            o.append("    size_t %s_n;" % n)
            o.append("    const char *%s_s = text_in(%s, &%s_n);" % (n, n, n))
            call += ["%s_s" % n, "%s_n" % n]
        else:
            out = n
    if out:
        f = "%s(%s)" % (fn, ", ".join(call + ["%s", "%s", "&n"]))
        o += ["    size_t n = 0;", "    check(%s);" % (f % ("NULL", "0")),
              "    vstr_t v;", "    vstr_init_len(&v, n);",
              "    check(%s);" % (f % ("v.buf", "n")),
              "    return mp_obj_new_str_from_vstr(&v);"]
    elif m.shape == "value_nil":
        o += ["    int32_t v = 0;",
              "    int rc = %s(%s, &v);" % (fn, ", ".join(call)),
              "    if (rc == MOY_APP_ABSENT) {", "        return mp_const_none;", "    }",
              "    check(rc);", "    return mp_obj_new_int(v);"]
    elif m.shape == "taken":
        o += ["    int rc = %s(%s);" % (fn, ", ".join(call)),
              "    if (rc != MOY_APP_BAD) {", "        check(rc);", "    }",
              "    return mp_obj_new_bool(rc == MOY_APP_OK);"]
    else:
        o += ["    int32_t v = %s(%s);" % (fn, ", ".join(call)),
              "    if (v < 0) {", "        raise_rc(-v);", "    }"]
        if m.shape == "bool":
            o.append("    return mp_obj_new_bool(v);")
        elif m.shape == "enum":
            for i, n in m.enum():
                o += ["    if (v == %d) {" % i, "        return MP_OBJ_NEW_QSTR(MP_QSTR_%s);" % n,
                      "    }"]
            o.append("    return mp_const_none;")
        else:
            o.append("    return mp_obj_new_int(v);")
    return o


def _fn_type(m, name):
    """A pointer to a function of m's C prototype, named `name`."""
    return "%s (*%s)(%s)" % (m.ret, name, ", ".join(t for t, _ in m.cps))


def _mp_shared(ms):
    """{method: helper name} and the helpers' lines: the methods whose bodies are one
    body once their C function is a parameter call one helper, as the hand file's
    name_read did."""
    groups = {}
    for m in ms:
        key = (tuple(_mp_body(m, "fn")), _fn_type(m, "fn"), tuple(m.names))
        groups.setdefault(key, []).append(m)
    helper, o = {}, []
    for (body, ftype, names), g in groups.items():
        if len(g) < 2:
            continue
        name = "role_%s_%s_of" % (g[0].role, g[0].verb)
        args = ["mp_obj_t self"] + ["mp_obj_t %s" % n for n in names] + [ftype]
        o += c_decl("static mp_obj_t " + name, args, " {") + list(body) + ["}", ""]
        for m in g:
            helper[m] = name
    return helper, o


def _mp_method(m, helper=None):
    cname = "%s_%s" % (m.role, m.verb)
    args = ["mp_obj_t self"] + ["mp_obj_t %s" % n for n in m.names]
    o = ["static mp_obj_t %s(%s) {" % (cname, ", ".join(args))]
    if helper:
        o.append("    return %s(%s);" % (helper, ", ".join(["self"] + m.names + [m.fn])))
    else:
        o += _mp_body(m, m.fn)
    o.append("}")
    o.append("static MP_DEFINE_CONST_FUN_OBJ_%d(%s_obj, %s);" % (1 + len(m.names), cname, cname))
    return o


def gen_mp(mod, face, hdr, src=None):
    ms = methods(face, hdr, src)
    o = [header("native/%s/face.json and native/os_calls.json" % face["dir"], "//"),
         "// %s's generated role rows, included by modmoy_%s.c after role_app, text_in, check"
         % (mod, mod[4:]),
         "// and raise_rc: MOY_FACE_%s_<ROLE> is a role's part of its type's locals table."
         % mod[4:].upper(), ""]
    helper, shared = _mp_shared(ms)
    o += shared
    for m in ms:
        o += _mp_method(m, helper.get(m)) + [""]
    for role in face["roles"]:
        ent = ["    { MP_ROM_QSTR(MP_QSTR_%s), MP_ROM_PTR(&%s_%s_obj) }," % (m.verb, role, m.verb)
               for m in ms if m.role == role]
        o.append("#define MOY_FACE_%s_%s \\" % (mod[4:].upper(), role.upper()))
        o += [e + " \\" for e in ent[:-1]] + [ent[-1], ""]
    return "\n".join(o)


# ---- ctypes ---------------------------------------------------------------------------

def _ctype(t):
    b = _base(t)
    if b in _CT:
        return _CT[b]
    if b.endswith("*"):
        return "_c.c_void_p"
    raise TableError("no ctypes type for %s" % t)


def _host_method(m):
    o = ["    def %s(self%s):" % (m.verb, "".join(", " + n for n in m.names)),
         "        f, a, g = self._lib.%s, self._app._p, self._g" % m.fn]
    call, out = ["a", "g"], None
    for n, t in m.ins:
        if t == "int":
            call.append("int(%s)" % n)
        elif t == "str":
            o.append('        %s_b = str(%s).encode("utf-8", "surrogateescape")' % (n, n))
            call += ["%s_b" % n, "len(%s_b)" % n]
        else:
            out = n
    fn = "f"
    if out:
        o += ["        n = _c.c_size_t()",
              "        _check(%s(%s, None, 0, _c.byref(n)))" % (fn, ", ".join(call)),
              "        buf = _c.create_string_buffer(n.value or 1)",
              "        _check(%s(%s, buf, n.value, _c.byref(n)))" % (fn, ", ".join(call)),
              '        return buf.raw[:n.value].decode("utf-8", "surrogateescape")']
    elif m.shape == "value_nil":
        o += ["        v = _c.c_int32()",
              "        rc = %s(%s, _c.byref(v))" % (fn, ", ".join(call)),
              "        if rc == _E.ABSENT:", "            return None",
              "        _check(rc)", "        return v.value"]
    elif m.shape == "taken":
        o += ["        rc = %s(%s)" % (fn, ", ".join(call)),
              "        if rc != _E.BAD:", "            _check(rc)", "        return rc == _E.OK"]
    else:
        o += ["        v = %s(%s)" % (fn, ", ".join(call)),
              "        if v < 0:", "            raise _E.exception(-v)"]
        if m.shape == "bool":
            o.append("        return bool(v)")
        elif m.shape == "enum":
            o.append("        return %s.get(v)" % ("{%s}" % ", ".join(
                '%d: "%s"' % (i, n) for i, n in m.enum())))
        else:
            o.append("        return v")
    return o


def _fold(head, items, tail, width=99):
    """`head` the items, comma-separated, `tail`: wrapped under the first item."""
    out, line = [], head
    for i, x in enumerate(items):
        piece = x + ("," if i < len(items) - 1 else tail)
        if len(line) + len(piece) > width and line != head:
            out.append(line.rstrip())
            line = " " * len(head)
        line += piece + " "
    out.append(line.rstrip())
    return out


def host_rows(face, hdr, src=None):
    """The ctypes face's lines: _check, ROLE_SIGS and each role's <Role>Rows."""
    ms = methods(face, hdr, src)
    o = ["", "", "def _check(rc):", "    if rc != _E.OK:", "        raise _E.exception(rc)",
         "", "", "ROLE_SIGS = ("]
    for m in ms:
        o += _fold('    ("%s", [' % m.fn, [_ctype(t) for t, _ in m.cps], "], %s)," % _ctype(m.ret))
    o.append(")")
    for role in face["roles"]:
        o += ["", "", "class %sRows:" % role.capitalize(),
              '    """The %s role\'s generated rows; the role class sets _app, _g and _lib."""'
              % role]
        for m in ms:
            if m.role == role:
                o += [""] + _host_method(m)
    return o
