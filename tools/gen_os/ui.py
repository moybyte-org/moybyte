# Map (grep -n a name to jump there):
#   Arg  one C parameter of a context-form verb and its kind
#   Verb  one face.json api row with its C prototype
#   verbs  every api row as a Verb
#   -- the context form, MicroPython  gen_mp: modmoy_ui_face.h
#   -- the context form, ctypes  gen_host: runtime/host/moy_ui_face.py
#   -- the RUN form  gen_run_h, gen_run_c: the run prototypes and bodies
#   Public  one os_calls.json ui row
#   publics  every ui row as a Public
#   gen_wasm  moy_ui_wasm_face.h
#   gen_lua  moycore_ui_rows.h and MOYCORE_UI_VERBS
#   -- ui.py's verb bodies  gen_ui_py: the GENERATED regions
#   -- the parity probe  probe_calls, probe_script
#   -- the check  check: every hand face exists, no raw param on a generated one
#   outputs  every file this module writes, for tools/gen_os_table.py
"""The toolkit's verb faces (docs/refactor_2026-11/tiers.md section 2.2), from
two sources:

  native/moy_ui/face.json's `api` rows, one per engine verb
  (moy_ui_api.h's moy_ui_api_<verb>, its C prototype read from the header),
  each emitting both entry forms (T16): the context form a Python app's
  frame passes its own context to -- the board module's `api_<verb>`
  (modmoy_ui_face.h) and the host's over ctypes (runtime/host/moy_ui_face.py,
  with their argtypes) -- and, where it has `run`, the RUN form over the
  run's one context (moy_ui_run_rows.h, its bodies moy_ui_run_face.h);

  native/os_calls.json's `ui.*` rows, the public verbs, each drawn through
  an engine verb: a compiled app's import (moy_ui_wasm_face.h), a Lua cart's
  `ui.<verb>` (native/moycore/moycore_ui_rows.h) and the Python verb's body in
  runtime/toolkit/ui.py (a GENERATED region inside each verb, under its own
  docstring).

An `api` row:
  api       the verb: the Python name api_<verb>, the C moy_ui_api_<verb>
  c         the C function where it is another (moy_ui_run_back)
  hand      true: both context faces are written by hand (the row only
            names them, and the check finds them); ["mp"] or ["host"]: that
            face alone
  ret       the Python answer where the C type does not say it: "bool",
            "nested" (MOY_UI_API_DEEP raises), "nested_none" (likewise,
            answering None), "if" (the out words' tuple where the C answered
            true, else None)
  out       {param: n}: int32 words the face makes and answers as a tuple
  buf       {param: n}: int32 words the caller passes (an array('i'))
  opts      [param]: a (bytes, length) pair a sequence may stand for (the
            boards' module joins it, modmoy_ui.c's opts_of)
  wspan     [param]: a (const int32_t *, int n) pair from an array('i')
  flag      [param]: an int the face takes as a truth value
  trunc     [param]: an int taken modulo its width
  dflt      {param: default}: the Python default
  run       the RUN form: "draw" (opens the run's sink; an answer goes to
            *out), "nest" (MOY_UI_API_DEEP answers -MOY_UI_DEEP), "deep" (the
            same, else *out), "query:<v>" (no drawing; <v> with no run)
  run_ret   the RUN form's answer parameter where `out` is the verb's own
  run_fix   {param: C}: what the RUN form passes for a parameter it lacks
  doc       what the RUN form's prototype says of it

A `ui.*` row (native/os_calls.json) adds:
  py_sig    the Python verb's parameters; `in`'s (the out ones aside) in order
  enum      {param: {"py": table, "lua": table, "n": count, "default": i}}: a
            name on Python and Lua, an index on wasm (the button kinds)
  base      {param: {"lua": 1}}: the index base a tier answers and takes in
  list      [param]: a sequence on Python and Lua, NUL-separated bytes on wasm
  lua_ret   what the Lua verb answers: the door rows' "nil" (the default),
            "bool" or "value", or the toolkit's own "index" (a value from 1)
            and "words" (an out's words, a value each)
  end_form  true: the call with no arguments ends a nest
  ui        the faces' particulars: `api` (the engine verb, the row's own by
            default), `fix` ({param: C name}), `hand` (["wasm", "lua",
            "py"]: faces written by hand), `lua_in` ({param: "opt0",
            "check", "bool"}), `lua_opt` ([list param]: nil reads ""),
            `init` (*out's value before the call), `wret` ("in:<param>": a
            failed call answers that argument), `wput` ("always" or "pos":
            when the out words are written; on success by default),
            `wnull` ([param]: an empty text crosses as NULL)."""

import json
import os
import re
import textwrap

from . import TableError, header
from .cproto import c_decl
from . import face as _face
from .face import Headers, NATIVE
from .table import ROOT, runtime_file, verb_of
from .py import splice

UI = os.path.join(NATIVE, "moy_ui")
FACE = os.path.join(UI, "face.json")
MP_C = os.path.join(UI, "modmoy_ui_face.h")
HOST_PY = os.path.join(ROOT, "runtime", "host", "moy_ui_face.py")
RUN_H = os.path.join(UI, "moy_ui_run_rows.h")
RUN_C = os.path.join(UI, "moy_ui_run_face.h")
WASM_C = os.path.join(UI, "moy_ui_wasm_face.h")
LUA_H = os.path.join(NATIVE, "moycore", "moycore_ui_rows.h")
UI_PY = str(runtime_file("ui.py"))
HAND_MP = os.path.join(UI, "modmoy_ui.c")
HAND_HOST = os.path.join(ROOT, "runtime", "host", "moy_ui_api_binding.py")
HAND_RUN = os.path.join(UI, "moy_ui_run.c")
HAND_WASM = os.path.join(UI, "moy_ui_wasm.c")
HAND_LUA = os.path.join(NATIVE, "moycore", "moycore_ui.h")

API_FIELDS = {"api", "c", "hand", "ret", "out", "buf", "opts", "wspan", "flag", "trunc", "dflt",
              "run", "run_ret", "run_fix", "doc"}
ROW_UI = {"api", "fix", "hand", "lua_in", "lua_opt", "init", "wret", "wput", "wnull"}
UI_LUA_RETS = ("nil", "bool", "value", "index", "words")
# Python names a C parameter cannot keep on the host: the face calls these.
_PY_TAKEN = {"len", "int", "bytes", "bool", "tuple", "in", "c", "lib", "m"}
_UNSIGNED = {"uint32_t", "uint16_t", "uint8_t", "unsigned", "size_t"}
_CT = {"int": "c.c_int", "int32_t": "c.c_int32", "uint32_t": "c.c_uint32", "size_t": "c.c_size_t",
       "int16_t": "c.c_int16", "uint8_t": "c.c_uint8", "void": "None", "bool": "c.c_bool"}


def _fold(line, width=99, held=None):
    """A C line longer than `width` broken at the shallowest ", " that fits
    (the last of those), the rest under the parenthesis or brace open there;
    `held` the column of one a line it continues left open."""
    if len(line) <= width or line.lstrip().startswith("//"):
        return [line]
    stack, best = [] if held is None else [held], None
    for i, ch in enumerate(line):
        if ch in "({":
            stack.append(i)
        elif ch in ")}" and stack:
            stack.pop()
        elif ch == "," and i + 1 < width and stack and line[i + 1:i + 2] == " ":
            key = (-len(stack), i)
            if best is None or key > best[0]:
                best = (key, i, stack[-1])
    if best is None:
        return [line]
    _k, i, at = best
    return [line[:i + 1]] + _fold(" " * (at + 1) + line[i + 2:], width, at)


def fold(text):
    return "\n".join(x for line in text.split("\n") for x in _fold(line))


def load():
    with open(FACE) as f:
        rows = json.load(f)["moy_ui"].get("api", [])
    return rows


def _base(t):
    return re.sub(r"\s+", " ", re.sub(r"\bconst\b", "", t)).strip()


def _split_param(p):
    m = re.match(r"^(.*?)\b(\w+)\s*(?:\[(\w*)\])?$", p.strip())
    if not m:
        raise TableError("no parameter in %r" % p)
    return m.group(1).strip(), m.group(2), m.group(3)


class Arg:
    """One C parameter of an engine verb, as the faces take it."""

    def __init__(self, kind, cn, ct, arr=None, n=None, py=(), pair=None):
        self.kind, self.cn, self.ct, self.arr, self.n = kind, cn, ct, arr, n
        self.py = list(py)              # the Python parameters it consumes
        self.pair = pair                # a (pointer, length) pair's length decl

    def decl(self, name=None):
        name = name or self.cn
        return "%s%s%s%s" % (self.ct, "" if self.ct.endswith("*") else " ", name,
                             "[%s]" % self.arr if self.arr is not None else "")


class Verb:
    """An `api` row and its engine verb's prototype."""

    def __init__(self, row, hdr):
        bad = set(row) - API_FIELDS
        if bad:
            raise TableError("moy_ui api %s: %s" % (row.get("api"), ", ".join(sorted(bad))))
        self.row = row
        self.verb = row["api"]
        self.name = "api_" + self.verb
        self.fn = row.get("c", "moy_ui_api_" + self.verb)
        hand = row.get("hand", False)
        self.hand = {"mp", "host"} if hand is True else set(hand or ())
        self.args = None
        self.why = None
        self.ret, self.params = "void", []
        try:
            ret, _fn, ps, _ = hdr.proto(self.fn)
            self.ret = ret.strip()
            self.params = [_split_param(p) for p in ps if p.strip() not in ("", "void")]
            self.args = self._classify()
        except TableError as e:
            if self.hand != {"mp", "host"}:
                raise
            self.why = str(e)
        dflt = row.get("dflt", {})
        self.py = [] if self.args is None else [p for a in self.args for p in a.py]
        self.dflt = [dflt.get(p) for p in self.py]

    def _classify(self):
        row = self.row
        out, buf = row.get("out", {}), row.get("buf", {})
        opts, wspan = set(row.get("opts", ())), set(row.get("wspan", ()))
        flag, trunc = set(row.get("flag", ())), set(row.get("trunc", ()))
        args, i, ps = [], 0, self.params
        while i < len(ps):
            ct, cn, arr = ps[i]
            b = _base(ct)
            nxt = _base(ps[i + 1][0]) if i + 1 < len(ps) else None
            py = cn + "_" if cn in _PY_TAKEN else cn
            if b == "moy_ui_ctx_t *":
                args.append(Arg("ctx", cn, ct, py=["ctx"]))
            elif b == "moy_ui_inst_t *":
                args.append(Arg("inst", cn, ct))
            elif b == "moy_ui_sink_t *":
                args.append(Arg("sink", cn, ct, py=["cv", "look"]))
            elif b == "moy_ui_span_t":
                args.append(Arg("span", cn, ct, py=[py]))
            elif b == "moy_ui_over_t *":
                args.append(Arg("over", cn, ct, py=[py]))
            elif b == "moy_ui_box_t":
                args.append(Arg("box", cn, ct, py=[py]))
            elif b == "moy_ui_box_t *":
                args.append(Arg("boxout", cn, ct))
            elif b == "uint8_t *" and nxt == "size_t":
                kind = "opts" if cn in opts else "bytes" if ct.startswith("const") else "rw"
                args.append(Arg(kind, cn, ct, py=[py], pair=ps[i + 1]))
                i += 1
            elif b in ("int32_t *", "int32_t") and (arr is not None or b.endswith("*")) \
                    and cn in wspan:
                args.append(Arg("wspan", cn, ct, arr, py=[py], pair=ps[i + 1]))
                i += 1
            elif (b == "int32_t *" or (b == "int32_t" and arr is not None)) and cn in out:
                args.append(Arg("out", cn, ct, arr, n=out[cn]))
            elif (b == "int32_t *" or (b == "int32_t" and arr is not None)) and cn in buf:
                args.append(Arg("buf", cn, ct, arr, n=buf[cn], py=[py]))
            elif arr is None and b in ("int", "int32_t", "uint32_t", "size_t", "bool"):
                kind = "flag" if cn in flag else "trunc" if (cn in trunc or b in ("uint32_t",)) \
                    else "int"
                args.append(Arg(kind, cn, ct, py=[py]))
            else:
                args.append(Arg("raw", cn, ct, arr))     # the RUN form passes it on
            i += 1
        rb = _base(self.ret)
        if rb not in ("void", "int", "int32_t", "uint32_t", "size_t", "moy_ui_box_t", "char *",
                      "bool"):
            raise TableError("moy_ui %s: no answer for %s" % (self.verb, self.ret))
        if [a for a in args if a.kind == "out"] and len([a for a in args if a.kind == "out"]) > 1:
            raise TableError("moy_ui %s: one out" % self.verb)
        return args

    def has(self, kind):
        return any(a.kind == kind for a in self.args or ())

    @property
    def answers(self):
        """Whether the Python verb answers something."""
        rb = _base(self.ret)
        r = self.row.get("ret")
        if r == "nested_none":
            return False
        return rb != "void" or self.has("out") or self.has("boxout")


def verbs(hdr=None):
    hdr = hdr or Headers({"dir": "moy_ui", "headers": ["moy_ui_api.h", "moy_ui_run.h"]})
    return [Verb(r, hdr) for r in load()]


# ---- the context form, MicroPython ----------------------------------------------

def _mp_conv(a, x):
    b = _base(a.ct)
    if a.kind == "flag":
        return "mp_obj_is_true(%s)" % x
    if a.kind == "trunc":
        return "(%s)mp_obj_get_int_truncated(%s)" % (b, x)
    if b == "size_t":
        return "(size_t)num(%s)" % x
    return "num(%s)" % x


def _mp_row(v):
    fn = "ui_api_" + v.verb
    n = len(v.py)
    need = sum(1 for d in v.dflt if d is None)
    var = n > 3 or need != n
    if var:
        o = ["static mp_obj_t %s(size_t n_args, const mp_obj_t *a) {" % fn]
        if need == n:
            o.append("    (void)n_args;")
        arg = ["a[%d]" % i for i in range(n)]
    else:
        o = ["static mp_obj_t %s(%s) {" % (fn, ", ".join("mp_obj_t a%d" % i for i in range(n))
                                           or "void")]
        arg = ["a%d" % i for i in range(n)]
    k = 0
    call = []
    post = None
    for a in v.args:
        x = arg[k:k + len(a.py)]
        if a.kind == "ctx":
            o.append("    moy_ui_ctx_t *%s = ctx_of(%s);" % (a.cn, x[0]))
            call.append(a.cn)
        elif a.kind == "inst":
            call.append("NULL")
        elif a.kind == "sink":
            o.append("    moy_ui_sink_t %s;" % a.cn)
            o.append("    sink_of(&%s, %s, %s);" % (a.cn, x[0], x[1]))
            call.append("&" + a.cn)
        elif a.kind == "span":
            call.append("span0_of(%s)" % x[0])
        elif a.kind == "over":
            o.append("    moy_ui_over_t %s_v;" % a.cn)
            call.append("over_of(%s, &%s_v)" % (x[0], a.cn))
        elif a.kind == "box":
            call.append("box_of(%s)" % x[0])
        elif a.kind == "boxout":
            o.append("    moy_ui_box_t %s;" % a.cn)
            call.append("&" + a.cn)
            post = "    return box_tuple(%s);" % a.cn
        elif a.kind in ("opts", "bytes", "rw"):
            o.append("    mp_buffer_info_t %s_b;" % a.cn)
            if a.kind == "opts":
                o.append("    uint8_t %s_ob[OPTS_BUF];" % a.cn)
                o.append("    opts_of(%s, %s_ob, &%s_b);" % (x[0], a.cn, a.cn))
            else:
                o.append("    mp_get_buffer_raise(%s, &%s_b, MP_BUFFER_%s);"
                         % (x[0], a.cn, "READ" if a.kind == "bytes" else "RW"))
            call += ["(%s)%s_b.buf" % (a.ct, a.cn), "%s_b.len" % a.cn]
        elif a.kind == "wspan":
            o.append("    mp_buffer_info_t %s_b;" % a.cn)
            o.append("    mp_get_buffer_raise(%s, &%s_b, MP_BUFFER_READ);" % (x[0], a.cn))
            call += ["(const int32_t *)%s_b.buf" % a.cn, "(int)(%s_b.len / 4)" % a.cn]
        elif a.kind == "out":
            o.append("    int32_t %s[%d];" % (a.cn, a.n))
            call.append(a.cn)
            items = ", ".join("mp_obj_new_int(%s[%d])" % (a.cn, j) for j in range(a.n))
            post = ["    mp_obj_t t_[%d] = {%s};" % (a.n, items),
                    "    return ret_tuple(%d, t_);" % a.n]
        elif a.kind == "buf":
            w = 0 if a.ct.startswith("const") else 1
            o.append("    %sint32_t *%s = words_of(%s, %d, %d, MP_ERROR_TEXT(\"moy_ui: %d words\"));"
                     % ("const " if not w else "", a.cn, x[0], a.n, w, a.n))
            call.append(a.cn)
        else:
            dflt = v.dflt[k]
            e = _mp_conv(a, x[0])
            if var and dflt is not None:
                e = "n_args > %d ? %s : %s" % (k, e, dflt)
            call.append(e)
        k += len(a.py)
    cx = "%s(%s)" % (v.fn, ", ".join(call))
    ret = v.row.get("ret")
    rb = _base(v.ret)
    if ret == "if":
        o.append("    if (!%s) {" % cx)
        o.append("        return mp_const_none;")
        o.append("    }")
        o += post
    elif post is not None:
        o.append("    %s;" % cx)
        o += post if isinstance(post, list) else [post]
    elif ret == "nested":
        o.append("    return mp_obj_new_int(nested(%s));" % cx)
    elif ret == "nested_none":
        o.append("    nested(%s);" % cx)
        o.append("    return mp_const_none;")
    elif rb == "void":
        o.append("    %s;" % cx)
        o.append("    return mp_const_none;")
    elif ret == "bool" or rb == "bool":
        o.append("    return mp_obj_new_bool(%s);" % cx)
    elif rb == "moy_ui_box_t":
        o.append("    return box_tuple(%s);" % cx)
    elif rb == "char *":
        o.append("    const char *r_ = %s;" % cx)
        o.append("    return r_ == NULL ? mp_const_none : mp_obj_new_str(r_, strlen(r_));")
    elif rb in ("uint32_t", "size_t"):
        o.append("    return mp_obj_new_int_from_uint(%s);" % cx)
    else:
        o.append("    return mp_obj_new_int(%s);" % cx)
    o.append("}")
    if var:
        o.append("static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(%s_obj, %d, %d, %s);"
                 % (fn, need, n, fn))
    else:
        o.append("static MP_DEFINE_CONST_FUN_OBJ_%d(%s_obj, %s);" % (n, fn, fn))
    return o


def gen_mp(vs):
    o = [header("native/moy_ui/face.json", "//"),
         "// The board module's context form of the toolkit's verbs (`api_<verb>`), included",
         "// by modmoy_ui.c after its converters: MOY_FACE_MOY_UI_ROWS is their part of its",
         "// globals table. Each takes the app's context, the canvas and look it draws in",
         "// where it draws, its arguments, and `o` the override dict or block.", ""]
    for v in vs:
        if "mp" in v.hand:
            continue
        o += _mp_row(v)
        o.append("")
    o.append("#define MOY_FACE_MOY_UI_ROWS \\")
    ent = ["    { MP_ROM_QSTR(MP_QSTR_%s), MP_ROM_PTR(&ui_%s_obj) }" % (v.name, v.name)
           for v in vs if "mp" not in v.hand]
    o += [e + ", \\" for e in ent[:-1]] + [ent[-1] + ",", ""]
    return "\n".join(o)


# ---- the context form, ctypes ----------------------------------------------------

def _ctype(a):
    b = _base(a.ct)
    if a.kind in ("ctx", "inst", "over", "rw", "out", "buf", "wspan"):
        return "c.c_void_p"
    if a.kind == "sink":
        return "c.POINTER(moy_ui_sink_t)"
    if a.kind == "span":
        return "moy_ui_span_t"
    if a.kind == "box":
        return "moy_ui_box_t"
    if a.kind == "boxout":
        return "c.POINTER(moy_ui_box_t)"
    if a.kind in ("opts", "bytes"):
        return "c.c_char_p"
    return _CT[b]


def _restype(v):
    rb = _base(v.ret)
    if rb == "moy_ui_box_t":
        return "moy_ui_box_t"
    if rb == "char *":
        return "c.c_char_p"
    return _CT[rb]


def _sig(v):
    ts = []
    for a in v.args:
        ts.append(_ctype(a))
        if a.pair is not None:
            ts.append(_CT[_base(a.pair[0])])
    return '    ("%s", [%s], %s),' % (v.fn, ", ".join(ts), _restype(v))


def _py_row(v):
    ps = [p if d is None else "%s=%s" % (p, d) for p, d in zip(v.py, v.dflt)]
    o = ["    def %s(%s):" % (v.name, ", ".join(ps))]
    call, post = [], None
    for a in v.args:
        x = a.py
        if a.kind == "ctx":
            o.append("        p_, k_ = ctx_(ctx)")
            call.append("p_")
        elif a.kind == "inst":
            call.append("None")
        elif a.kind == "sink":
            call.append("c.byref(sink(cv, look))")
        elif a.kind == "span":
            o.append("        %s_t, %s_k = span0(%s)" % (a.cn, a.cn, x[0]))
            call.append("%s_t" % a.cn)
        elif a.kind == "over":
            o.append("        %s_v, %s_k = over(%s)" % (a.cn, a.cn, x[0]))
            call.append("%s_v" % a.cn)
        elif a.kind == "box":
            call.append("box_of(%s)" % x[0])
        elif a.kind == "boxout":
            o.append("        %s = moy_ui_box_t()" % a.cn)
            call.append("c.byref(%s)" % a.cn)
            post = "        return (%s.x, %s.y, %s.w, %s.h)" % ((a.cn,) * 4)
        elif a.kind in ("opts", "bytes"):
            o.append("        %s_b = bytes(%s)" % (a.cn, x[0]))
            call += ["%s_b" % a.cn, "len(%s_b)" % a.cn]
        elif a.kind == "rw":
            o.append("        %s_b = (c.c_uint8 * max(1, len(%s))).from_buffer(%s) if len(%s) "
                     "else (c.c_uint8 * 1)()" % (a.cn, x[0], x[0], x[0]))
            call += ["%s_b" % a.cn, "len(%s)" % x[0]]
        elif a.kind == "wspan":
            o.append("        %s_w = (c.c_int32 * len(%s))(*%s)" % (a.cn, x[0], x[0]))
            call += ["%s_w" % a.cn, "len(%s)" % x[0]]
        elif a.kind == "out":
            o.append("        %s_w = (c.c_int32 * %d)()" % (a.cn, a.n))
            call.append("%s_w" % a.cn)
            post = "        return tuple(%s_w)" % a.cn
        elif a.kind == "buf":
            call.append("(c.c_int32 * %d).from_buffer(%s)" % (a.n, x[0]))
        elif a.kind == "flag":
            call.append("1 if %s else 0" % x[0])
        else:
            call.append("int(%s)" % x[0])
    cx = "lib.%s(%s)" % (v.fn, ", ".join(call))
    ret = v.row.get("ret")
    rb = _base(v.ret)
    if ret == "if":
        o.append("        if not %s:" % cx)
        o.append("            return None")
        o.append(post)
    elif post is not None:
        o.append("        " + cx)
        o.append(post)
    elif ret == "nested":
        o.append("        return _nested(int(%s))" % cx)
    elif ret == "nested_none":
        o.append("        _nested(int(%s))" % cx)
    elif rb == "void":
        o.append("        " + cx)
    elif ret == "bool" or rb == "bool":
        o.append("        return bool(%s)" % cx)
    elif rb == "moy_ui_box_t":
        o.append("        b_ = %s" % cx)
        o.append("        return (b_.x, b_.y, b_.w, b_.h)")
    elif rb == "char *":
        o.append("        r_ = %s" % cx)
        o.append("        return None if r_ is None else r_.decode(\"utf-8\", \"replace\")")
    else:
        o.append("        return int(%s)" % cx)
    o.append("    m.%s = %s" % (v.name, v.name))
    return o


def gen_host(vs):
    o = [header("native/moy_ui/face.json", "#"),
         '"""The host\'s context form of the toolkit\'s verbs over ctypes: the argtypes',
         "of each engine verb they call (SIGS, which runtime/host/moy_ui_binding.py",
         "sets), and the verbs, set on the module by runtime/host/moy_ui_api_binding.py's",
         'install with its converters; and the ctypes mirrors of the structures the',
         'toolkit\'s host binding reads (tools/gen_os/structs.py)."""', "",
         "import ctypes as c", "import ctypes as _c", "", ""] + _face.mirrors(_face.load()["moy_ui"]).emit() + [
         "SIGS = ("]
    o += [_sig(v) for v in vs if v.args is not None and v.hand != {"mp", "host"}]
    o += [")", "", "",
          "def install(m, lib, ctx_, sink, span0, over, box_of):",
          '    """The verbs on module `m` over `lib`: `ctx_` a context\'s pointer and what',
          "    keeps it, `sink` a canvas's sink, `span0` a text's span (None as \"\"), `over`",
          '    the override block\'s struct and what keeps it, `box_of` a rect."""',
          "    from runtime.moy_ui_binding import _nested"]
    for v in vs:
        if "host" not in v.hand:
            o += _py_row(v)
    o.append("")
    return "\n".join(o)


# ---- the RUN form ----------------------------------------------------------------

def _run_args(v):
    """The RUN form's C parameters after `target`, and its answer's."""
    fix = v.row.get("run_fix", {})
    ps = []
    for a in v.args:
        if a.kind in ("ctx", "sink", "inst") or a.cn in fix:
            continue
        if a.kind == "boxout":
            ps.append("int32_t %s[4]" % a.cn)
            continue
        ps.append(a.decl())
        if a.pair is not None:
            ps.append(" ".join(a.pair[:2]) if a.pair[2] is None else "%s %s[%s]" % a.pair)
    kind = v.row["run"].split(":")[0]
    rb = _base(v.ret)
    if kind in ("draw", "deep") and rb != "void":
        ps.append("int32_t *%s" % v.row.get("run_ret", "out"))
    return ps


def _run_ret(v):
    kind = v.row["run"].split(":")[0]
    if kind != "query":
        return "int"
    rb = _base(v.ret)
    return "int" if rb in ("int", "bool") else rb


def _run_head(v):
    return c_decl("%s moy_ui_run_%s" % (_run_ret(v), v.verb), ["void *target"] + _run_args(v), "")


def _run_body(v):
    fix = v.row.get("run_fix", {})
    kind, _, fb = v.row["run"].partition(":")
    call, box = [], None
    for a in v.args:
        if a.kind == "ctx":
            call.append("c")
        elif a.kind == "sink":
            call.append("&r.s")
        elif a.kind == "inst":
            call.append("NULL")
        elif a.cn in fix:
            call.append(fix[a.cn])
        elif a.kind == "boxout":
            box = a.cn
            call.append("&b_")
        else:
            call.append(a.cn)
            if a.pair is not None:
                call.append(a.pair[1])
    cx = "moy_ui_api_%s(%s)" % (v.verb, ", ".join(call))
    rb = _base(v.ret)
    o = _run_head(v)
    o[-1] += " {"
    if kind == "query":
        o.append("    moy_ui_ctx_t *c = run_ctx(target);")
        if rb == "void":
            o += ["    if (c != NULL) {", "        %s;" % cx, "    }"]
        else:
            o.append("    return c != NULL ? %s : %s;" % (cx, fb))
        o.append("}")
        return o
    o.append("    OPEN(target);")
    if kind == "nest":
        o.append("    return close_run(&r, nest_rc(%s));" % cx)
        o.append("}")
        return o
    if box:
        o.append("    moy_ui_box_t b_;")
    res = v.row.get("run_ret", "out")
    if kind == "deep":
        o += ["    int32_t v = %s;" % cx, "    if (v == MOY_UI_API_DEEP) {",
              "        return close_run(&r, MOY_UI_DEEP);", "    }", "    *%s = v;" % res]
    elif rb != "void":
        o.append("    *%s = %s;" % (res, cx))
    else:
        o.append("    %s;" % cx)
    if box:
        o += ["    %s[%d] = b_.%s;" % (box, i, f) for i, f in enumerate("xywh")]
    o.append("    return close_run(&r, MOY_UI_OK);")
    o.append("}")
    return o


def gen_run_h(vs):
    o = [header("native/moy_ui/face.json", "//"),
         "// The RUN form of the toolkit's verbs (moy_ui_run.h): each over `target`, a libmoy",
         "// moy_canvas (NULL: -MOY_UI_STALE). A drawing verb answers 0 or a moy_ui.h code",
         "// negated (-MOY_UI_DEEP: a scroll, text view, dialog or popover opened with",
         "// MOY_UI_API_NEST open already), its answer written at its last parameter; a",
         "// query answers the engine's, or its value with no run.",
         "#ifndef MOY_UI_RUN_ROWS_H", "#define MOY_UI_RUN_ROWS_H", ""]
    for v in vs:
        if v.row.get("run"):
            if v.row.get("doc"):
                o += ["// " + x for x in textwrap.wrap(v.row["doc"], 96)]
            h = _run_head(v)
            h[-1] += ";"
            o += h
    o += ["", "#endif // MOY_UI_RUN_ROWS_H", ""]
    return "\n".join(o)


def gen_run_c(vs):
    o = [header("native/moy_ui/face.json", "//"),
         "// The RUN form's bodies (their prototypes moy_ui_run_rows.h), included once, by",
         "// moy_ui_run.c after its OPEN, run_ctx and nest_rc.", ""]
    for v in vs:
        if v.row.get("run"):
            o += _run_body(v)
            o.append("")
    return "\n".join(o)


# ---- the public verbs (native/os_calls.json's ui rows) ---------------------------

def _in(r):
    out = []
    for p in r["in"]:
        n, _, t = p.partition(":")
        out.append((n, t))
    return out


class Public:
    """A `ui.*` row and the engine verb it draws through."""

    def __init__(self, r, by_verb, vals):
        bad = set(r.get("ui", {})) - ROW_UI
        if bad:
            raise TableError("%s: ui %s" % (r["name"], ", ".join(sorted(bad))))
        self.r = r
        self.verb = verb_of(r)
        u = r.get("ui", {})
        self.u = u
        self.hand = set(u.get("hand", ()))
        self.api = by_verb.get(u.get("api", self.verb))
        if self.api is None:
            raise TableError("%s: no engine verb %s" % (r["name"], u.get("api", self.verb)))
        self.ins = _in(r)
        self.fix = u.get("fix", {})
        self.vals = vals
        self.py = r.get("py_sig")
        lua = [n for n, t in self.ins if t != "out"]
        self.lua_ix = {n: i + 1 for i, n in enumerate(lua)}
        if self.py is not None:
            names = [p.split("=")[0].strip() for p in self.py]
            self.py_name = dict(zip(lua, names))
        else:
            self.py_name = {n: n for n in lua}

    def run_params(self):
        """The RUN form's parameters: (Arg or None, name) per C one after target."""
        v = self.api
        fix = v.row.get("run_fix", {})
        return [a for a in v.args if a.kind not in ("ctx", "sink", "inst") and a.cn not in fix]

    def kind(self):
        return self.api.row.get("run", "").split(":")[0]

    def answers(self):
        """Whether the RUN form writes an answer at its added *out."""
        return self.kind() in ("draw", "deep") and _base(self.api.ret) != "void"


def publics(src, vs):
    by = {v.verb: v for v in vs}
    hdr = Headers({"dir": "moy_ui", "headers": ["moy_ui.h", "moy_ui_api.h"]})
    vals = hdr.values()
    out = []
    for r in src["rows"]:
        if not r["name"].startswith("ui."):
            continue
        out.append(Public(r, by, vals))
    return out


_WTAKEN = {"e", "o", "ob", "on", "rc", "got"}


def _wn(n):
    """A wasm row's C name for parameter `n`."""
    return n + "_" if n in _WTAKEN else n


def _wrap(head, args, tail, indent="    "):
    """`head(args)tail` on lines of at most 99, the arguments under the first."""
    return c_decl(indent + head, args, tail)


def _wasm_params(p):
    ps = []
    for n, t in p.ins:
        if n == "o" and t == "bytes":
            ps += ["const uint8_t *ob", "uint32_t on"]
        elif t == "int":
            ps.append("int32_t " + _wn(n))
        elif t == "out":
            ps += ["uint8_t *" + n, "uint32_t %s_cap" % n]
        else:
            ps += ["const uint8_t *" + n, "uint32_t %s_n" % n]
    return ps


def _wasm_row(p, fix=None):
    """The body of p's wasm import, its fixed arguments from `fix` (p's own by default)."""
    fix = p.fix if fix is None else fix
    v = p.api
    o = []
    types = dict(p.ins)
    if types.get("o") == "bytes":
        o.append("    OVER(ob, on);")
    call, outs = ["wasm_target()"], []
    wnull = set(p.u.get("wnull", ()))
    took_o = False
    for a in p.run_params():
        if a.cn in fix:
            call.append(fix[a.cn])
            continue
        if a.kind == "over":
            call.append("&o")
            took_o = True
        elif a.kind == "span":
            call.append("moy_ui_span_of(%s%s, %s_n)" % (
                ("%s_n ? " % a.cn) if a.cn in wnull else "", a.cn + (" : NULL" if a.cn in wnull
                                                                     else ""), a.cn))
        elif a.kind in ("opts", "bytes"):
            call += [a.cn, "%s_n" % a.cn]
        elif a.kind in ("out", "boxout", "buf"):
            n = 4 if a.kind == "boxout" else a.n
            o.append("    int32_t %s_w[%d] = {%s};" % (a.cn, n, ", ".join(["0"] * n)))
            call.append("%s_w" % a.cn)
            outs.append((a.cn, n))
        elif a.kind == "flag" and types.get(a.cn) == "int":
            call.append("%s != 0" % _wn(a.cn))
        else:
            if a.cn not in types:
                raise TableError("%s: no wasm argument for %s" % (p.r["name"], a.cn))
            call.append(_wn(a.cn))
    if types.get("o") == "bytes" and not took_o:
        o.append("    (void)o;")
    wret = p.u.get("wret", "")
    put = p.u.get("wput", "ok")
    oname = [n for n, t in p.ins if t == "out"]
    if outs and not oname:
        raise TableError("%s: out words with no out" % p.r["name"])

    def puts(cond):
        lines = []
        for cn, n in outs:
            lines.append("put_words(%s, %s_cap, %s_w, %d);" % (oname[0], oname[0], cn, n))
        if not lines:
            return []
        if cond is None:
            return ["    " + x for x in lines]
        return ["    if (%s) {" % cond] + ["        " + x for x in lines] + ["    }"]

    if p.kind() == "query":
        rb = _base(v.ret)
        cx = "moy_ui_run_%s(%s)" % (v.verb, ", ".join(call))
        if rb == "void":
            o += ["    %s;" % cx, "    return 0;"]
        elif outs:
            o.append("    int32_t rc = %s;" % cx)
            o += puts("rc > 0" if put == "pos" else "rc == 0")
            o.append("    return rc;")
        else:
            o.append("    return %s;" % cx)
    elif p.answers():
        init = p.u.get("init", "0")
        if wret.startswith("in:"):
            init = _wn(wret[3:])
        o.append("    int32_t got = %s;" % (_wn(init) if init in types else init))
        call.append("&got")
        o += _wrap("int rc = moy_ui_run_%s" % v.verb, call, ";")
        o += puts(None if put == "always" else "rc == 0")
        o.append("    return rc != 0 ? %s : got;" % (_wn(wret[3:]) if wret.startswith("in:")
                                                       else "rc"))
    else:
        if outs:
            o += _wrap("int rc = moy_ui_run_%s" % v.verb, call, ";")
            o += puts(None if put == "always" else "rc == 0")
            o.append("    return rc;")
        else:
            o += _wrap("return moy_ui_run_%s" % v.verb, call, ";")
    return o


def _wasm_head(p):
    return c_decl("int32_t moy_ui_wasm_%s" % p.verb, ["void *e"] + _wasm_params(p), " {")


def _wasm_shared(g, names):
    """The helper and imports of a group of wasm rows sharing one body."""
    v = g[0].api.verb
    ps = _wasm_params(g[0])
    if any(_wasm_params(p) != ps for p in g):
        return None
    o = c_decl("static int32_t %s_row" % v, ["int32_t " + n for n in names] + ps, " {")
    o += _wasm_row(g[0], {n: n for n in names}) + ["}", ""]
    pn = [x.split()[-1].lstrip("*") for x in ps]
    for p in g:
        o += _wasm_head(p) + ["    (void)e;"]
        o += _wrap("return %s_row" % v, [p.fix[n] for n in names] + pn, ";") + ["}", ""]
    return o


def gen_wasm(ps):
    o = [header("native/os_calls.json", "//"),
         "// A compiled app's ui imports (their prototypes moy_ui_wasm_rows.h), each decoded",
         "// onto the RUN form: included once, by moy_ui_wasm.c after wasm_target, OVER and",
         "// put_words.", ""]
    mine = [p for p in ps if "wasm" not in p.hand]
    groups = shared(mine, _wasm_row)
    done = set()
    for p in mine:
        if p.verb in done:
            continue
        sh = _wasm_shared(*groups[p.verb]) if p.verb in groups else None
        if sh is not None:
            o += sh
            done |= {q.verb for q in groups[p.verb][0]}
            continue
        o += _wasm_head(p) + ["    (void)e;"] + _wasm_row(p) + ["}", ""]
    return "\n".join(o)


def _enum_py(p, cn, n):
    e = p.r["enum"][cn]
    return "%s.index(%s) if %s in %s else %d" % (e["py"], n, n, e["py"], e["default"])


def _lua_row(p, fix=None):
    """(the body's lines up to its run call, the lines that answer) of p's Lua verb, its
    fixed arguments from `fix` (p's own by default)."""
    fix = p.fix if fix is None else fix
    v = p.api
    o = []
    lua_in = p.u.get("lua_in", {})
    lua_opt = set(p.u.get("lua_opt", ()))
    base = p.r.get("base", {})
    enum = p.r.get("enum", {})
    lists = set(p.r.get("list", ()))
    call, outs = ["moycore_ui_canvas()"], []
    for a in p.run_params():
        if a.cn in fix:
            call.append(fix[a.cn])
            continue
        i = p.lua_ix.get(a.cn)
        how = lua_in.get(a.cn)
        if a.kind in ("out", "boxout", "buf"):
            n = 4 if a.kind == "boxout" else a.n
            o.append("    int32_t %s_w[%d] = {%s};" % (a.cn, n, ", ".join(["0"] * n)))
            call.append("%s_w" % a.cn)
            outs.append((a.cn, n))
            continue
        if i is None:
            raise TableError("%s: no Lua argument for %s" % (p.r["name"], a.cn))
        if a.kind == "over":
            o.append("    moy_ui_over_t %s;" % a.cn)
            o.append("    moycore_ui_over(L, %d, &%s);" % (i, a.cn))
            call.append("&" + a.cn)
        elif a.kind == "span" and how == "tostring":
            o += ["    size_t %s_n = 0;" % a.cn,
                  "    const char *%s_s = lua_isnoneornil(L, %d) ? \"\" : luaL_tolstring(L, %d, "
                  "&%s_n);" % (a.cn, i, i, a.cn),
                  "    moy_ui_span_t %s = moy_ui_span_of((const uint8_t *)%s_s, %s_n);"
                  % (a.cn, a.cn, a.cn)]
            call.append(a.cn)
        elif a.kind == "span":
            o.append("    moy_ui_span_t %s = moycore_ui_span(L, %d);" % (a.cn, i))
            call.append(a.cn)
        elif a.kind in ("opts", "bytes") and a.cn in lists:
            o.append("    size_t %s_n = 0;" % a.cn)
            if a.cn in lua_opt:
                o.append("    const char *%s = lua_isnoneornil(L, %d) ? \"\" : "
                         "moycore_ui_items(L, %d, &%s_n);" % (a.cn, i, i, a.cn))
            else:
                o.append("    const char *%s = moycore_ui_items(L, %d, &%s_n);" % (a.cn, i, a.cn))
            call += ["(const uint8_t *)%s" % a.cn, "%s_n" % a.cn]
        elif a.cn in enum:
            e = enum[a.cn]
            o += ["    int %s = %d;" % (a.cn, e["default"]),
                  "    if (lua_type(L, %d) == LUA_TSTRING) {" % i,
                  "        const char *k = lua_tostring(L, %d);" % i,
                  "        for (int i = 0; i < %d; i++) {" % e["n"],
                  "            if (strcmp(k, %s[i][0]) == 0) {" % e["lua"],
                  "                %s = i;" % a.cn, "                break;", "            }",
                  "        }", "    }"]
            call.append(a.cn)
        elif a.kind in ("int", "trunc", "flag"):
            b1 = base.get(a.cn, {}).get("lua", 0)
            if b1:
                e = "lua_isnoneornil(L, %d) ? -1 : moycore_ui_int(L, %d) - %d" % (i, i, b1)
            elif how == "opt0":
                e = "lua_isnoneornil(L, %d) ? 0 : moycore_ui_int(L, %d)" % (i, i)
            elif how == "check":
                e = "(int32_t)luaL_checkinteger(L, %d)" % i
            elif how == "bool" or a.kind == "flag":
                e = "lua_toboolean(L, %d)" % i
            else:
                e = "moycore_ui_int(L, %d)" % i
            o.append("    %s %s = %s;" % (_base(a.ct), a.cn, e))
            call.append(a.cn)
        else:
            raise TableError("%s: no Lua face for %s" % (p.r["name"], a.cn))
    lret = p.r.get("lua_ret", "nil")
    rb = _base(v.ret)
    if p.kind() == "query":
        cx = "moy_ui_run_%s(%s)" % (v.verb, ", ".join(call))
        if rb == "void" or lret == "nil":
            return o, ["    %s;" % cx, "    return 0;"]
        if lret == "bool":
            return o, ["    lua_pushboolean(L, %s != 0);" % cx, "    return 1;"]
        return o, ["    lua_pushinteger(L, (lua_Integer)%s);" % cx, "    return 1;"]
    if p.answers():
        o.append("    int32_t got = %s;" % p.u.get("init", "0"))
        call.append("&got")
    if p.kind() in ("nest", "deep"):
        o += _wrap("moycore_ui_nested(L, moy_ui_run_%s" % v.verb, call, ");")
    else:
        o += _wrap("moy_ui_run_%s" % v.verb, call, ";")
    if lret == "nil":
        return o, ["    return 0;"]
    if lret == "bool":
        return o, ["    lua_pushboolean(L, got != 0);", "    return 1;"]
    if lret == "value":
        return o, ["    lua_pushinteger(L, got);", "    return 1;"]
    if lret == "index":
        return o, ["    lua_pushinteger(L, (lua_Integer)got + 1);", "    return 1;"]
    if lret == "words":
        cn, n = outs[0]
        return o, ["    for (int i = 0; i < %d; i++) {" % n,
                   "        lua_pushinteger(L, %s_w[i]);" % cn, "    }", "    return %d;" % n]
    raise TableError("%s: lua_ret %s" % (p.r["name"], lret))


def shared(ps, body):
    """The rows of `ps` whose bodies are one body once their fixed arguments are
    parameters, as {first row's verb: (the group's rows, its fixed C names)}: such
    rows draw through one static helper, as hand faces share value_row and
    choice_row. `body(p, fix)` is a row's body; a group shares only that."""
    groups = {}
    for p in ps:
        if not p.fix:
            continue
        names = tuple(sorted(p.fix))
        key = (p.api.verb, names, tuple(body(p, {n: n for n in names})))
        groups.setdefault(key, []).append(p)
    return {g[0].verb: (g, k[1]) for k, g in groups.items() if len(g) > 1}


def _lua_shared(g, names):
    """The helper and wrappers of a group of Lua rows sharing one body."""
    v = g[0].api.verb
    rets = {tuple(_lua_row(p)[1]) for p in g} - {("    return 0;",)}
    if len(rets) > 1:
        return None
    pre = _lua_row(g[0], {n: n for n in names})[0]
    ret = list(rets.pop()) if rets else ["    return 0;"]
    head = "static int moycore_ui_%s_of(lua_State *L, %s)" % (
        v, ", ".join("int32_t " + n for n in names))
    o = [head, "{"] + pre + ret + ["}", ""]
    for p in g:
        args = ", ".join(["L"] + [p.fix[n] for n in names])
        o += ["static int moycore_ui_%s(lua_State *L)" % p.verb, "{"]
        if ret != ["    return 0;"] and _lua_row(p)[1] == ["    return 0;"]:
            o += ["    moycore_ui_%s_of(%s);" % (v, args), "    return 0;"]
        else:
            o.append("    return moycore_ui_%s_of(%s);" % (v, args))
        o += ["}", ""]
    return o


def gen_lua(ps):
    o = [header("native/os_calls.json", "//"),
         "// A Lua cart's ui verbs over the RUN form, included by moycore_ui.h after its",
         "// converters; MOYCORE_UI_VERBS is the `ui` table's luaL_Reg rows, every verb's.",
         "#ifndef MOYCORE_UI_ROWS_H", "#define MOYCORE_UI_ROWS_H", ""]
    mine = [p for p in ps if "lua" not in p.hand]
    groups = shared(mine, lambda p, fix: _lua_row(p, fix)[0])
    done = set()
    for p in mine:
        if p.verb in done:
            continue
        sh = _lua_shared(*groups[p.verb]) if p.verb in groups else None
        if sh is not None:
            o += sh
            done |= {q.verb for q in groups[p.verb][0]}
            continue
        pre, ret = _lua_row(p)
        o += ["static int moycore_ui_%s(lua_State *L)" % p.verb, "{"] + pre + ret + ["}", ""]
    o.append("#define MOYCORE_UI_VERBS \\")
    ent = ['    {"%s", moycore_ui_%s}' % (p.verb, p.verb) for p in ps]
    o += [e + ", \\" for e in ent[:-1]] + [ent[-1] + ",", "", "#endif // MOYCORE_UI_ROWS_H", ""]
    return "\n".join(o)


# ---- ui.py's verb bodies ---------------------------------------------------------

PY_BEGIN = "    # GENERATED from native/os_calls.json's ui.%s. Do not edit.\n"
PY_END = "    # END GENERATED\n"


def _py_body(p):
    v = p.api
    args = []
    base = p.r.get("base", {})
    end = p.r.get("end_form")
    for a in v.args:
        if a.kind in ("ctx", "sink", "inst", "out", "boxout"):
            continue
        if a.cn in p.fix:
            val = p.vals.get(p.fix[a.cn])
            if val is None:
                raise TableError("%s: no value of %s" % (p.r["name"], p.fix[a.cn]))
            args.append(str(val))
            continue
        n = p.py_name.get(a.cn)
        if n is None:
            raise TableError("%s: no Python argument for %s" % (p.r["name"], a.cn))
        if a.kind == "over":
            args.append("_over(%s)" % n)
        elif a.kind in ("opts", "bytes"):
            args.append("_opts(%s)" % n)
        elif a.cn in p.r.get("enum", {}):
            args.append(_enum_py(p, a.cn, n))
        elif base.get(a.cn) is not None:
            args.append("-1 if %s is None else %s" % (n, n))
        elif a.kind == "span" and end:
            args.append('"" if %s is None else %s' % (n, n))
        else:
            args.append(n)
    ret = "return " if v.answers else ""
    if v.has("sink"):
        head = ["    t = _T"]
        cx = "_C.%s(t[0], t[1], t[2]%s)" % (v.name, "".join(", " + x for x in args))
    else:
        head = []
        cx = "_C.%s(_T[0]%s)" % (v.name, "".join(", " + x for x in args))
    line = "    " + ret + cx
    if len(line) > 99:
        k = line.index("(") + 1
        parts = (cx[cx.index("(") + 1:-1]).split(", ")
        out, cur = [], line[:k]
        for i, x in enumerate(parts):
            piece = x + (", " if i < len(parts) - 1 else ")")
            if len(cur) + len(piece.rstrip()) > 99:
                out.append(cur.rstrip())
                cur = " " * k
            cur += piece
        out.append(cur)
        return head + out
    return head + [line]


def gen_ui_py(ps, text):
    for p in ps:
        if "py" in p.hand or p.py is None:
            continue
        begin = PY_BEGIN % p.verb
        text = splice(UI_PY, "\n".join(_py_body(p)) + "\n", begin, PY_END, text=text)
    return text


# ---- the parity probe --------------------------------------------------------------

_NESTS = {"scroll": 5, "grid": 7, "group": 5, "popover": 6}
_PROBE = {"ctx": ["CTX"], "sink": ["CV", "None"], "span": ['"probe"'], "over": ["OV"],
          "opts": ['b"one\\x00two\\x00three"'], "bytes": ['b"pasted"'],
          "rw": ["bytearray(b'abc' + bytes(29))"], "wspan": ["array('i', [0, -1, 0, 75, 4, 0])"],
          "box": ["(0, 0, 100, 40)"], "flag": ["True"], "trunc": ["7"], "int": ["3"]}


def probe_calls(vs=None):
    """[(name, [argument text])]: each generated context-form row called on
    arguments made from its C types, a nest it opens closed after it."""
    out = []
    for v in vs or verbs():
        if v.args is None or v.hand or v.verb == "size" or (v.verb.startswith("run_")
                                                            and v.verb != "run_typing"):
            continue
        args = []
        for a in v.args:
            if a.kind == "buf":
                args.append("array('i', [0] * %d)" % a.n)
            else:
                args += _PROBE.get(a.kind, [])
        out.append((v.name, args))
        if v.verb == "dialog":
            out.append((v.name, ["CTX", "CV", "None", '""', 'b""', "0", "None"]))
        elif v.verb in _NESTS:
            out.append((v.name, ["CTX", "CV", "None", '""'] + ["0"] * (_NESTS[v.verb] - 5)
                        + ["None"]))
    return out


def probe_script(vs=None):
    """The context form's probe, run where `M` is the module, `CTX` a context
    begun by `begin()`, `CV` a canvas, `OV` an override block: three frames of
    every generated row, each answer printed."""
    o = ["def out(name, f):", "    try:", "        r = f()", "    except Exception as e:",
         "        r = 'EXC ' + type(e).__name__", "    print(name, repr(r))", "",
         "for f in range(3):", "    begin()"]
    o += ["    out(%r, lambda: M.%s(%s))" % (n, n, ", ".join(a)) for n, a in probe_calls(vs)]
    o += ["print('DONE')", ""]
    return "\n".join(o)


# ---- the check ---------------------------------------------------------------------

def check(vs, ps):
    seen = set()
    for v in vs:
        if v.verb in seen:
            raise TableError("moy_ui api %s twice" % v.verb)
        seen.add(v.verb)
        if v.row.get("run") and v.args is None:
            raise TableError("moy_ui api %s: a RUN form with no face (%s)" % (v.verb, v.why))
        for a in v.args or ():
            if a.kind == "raw" and v.hand != {"mp", "host"}:
                raise TableError("moy_ui api %s: no face for %s %s" % (v.verb, a.ct, a.cn))
    for p in ps:
        if p.r.get("lua_ret", "nil") not in UI_LUA_RETS:
            raise TableError("%s: lua_ret is one of %s" % (p.r["name"], UI_LUA_RETS))
    with open(HAND_MP) as f:
        mp = f.read()
    with open(HAND_HOST) as f:
        host = f.read()
    for v in vs:
        if "mp" in v.hand and not re.search(r"\bui_%s_obj\b" % v.name, mp):
            raise TableError("moy_ui %s: no hand MicroPython face" % v.name)
        if "host" in v.hand and not re.search(r"\bdef %s\(" % v.name, host):
            raise TableError("moy_ui %s: no hand ctypes face" % v.name)
    with open(HAND_WASM) as f:
        wasm = f.read()
    with open(HAND_LUA) as f:
        lua = f.read()
    for p in ps:
        if "wasm" in p.hand and not re.search(r"\bmoy_ui_wasm_%s\(" % p.verb, wasm):
            raise TableError("%s: no hand wasm row" % p.r["name"])
        if "lua" in p.hand and not re.search(r"\bmoycore_ui_%s\(" % p.verb, lua):
            raise TableError("%s: no hand Lua verb" % p.r["name"])
        if p.py is None and "py" not in p.hand:
            raise TableError("%s: no py_sig" % p.r["name"])


def outputs(src, outs):
    """The faces' files, and ui.py's text (`outs` holds over.py's) with each
    generated verb body spliced in."""
    vs = verbs()
    ps = publics(src, vs)
    check(vs, ps)
    got = [(MP_C, fold(gen_mp(vs))), (HOST_PY, fold(gen_host(vs))), (RUN_H, gen_run_h(vs)),
           (RUN_C, fold(gen_run_c(vs))), (WASM_C, fold(gen_wasm(ps))), (LUA_H, fold(gen_lua(ps)))]
    for i, (path, text) in enumerate(outs):
        if path == UI_PY:
            outs[i] = (path, gen_ui_py(ps, text))
    return got
