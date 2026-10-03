#!/usr/bin/env python3
"""A WebAssembly text-format assembler for the subset this repository writes.

    python3 tools/wat.py in.wat out.wasm
    python3 tools/wat.py --cart src.moy out.moy   # a fixture cart, assembled

The wasm fixtures under libmoy/test/wasm/ are committed as .wat SOURCE, never as
binaries, and this turns them into modules with nothing but Python: no wabt, no
wasm-ld, no network. It is a test tool, not an author's toolchain -- a cart is
built by clang (SPEC.md 16.2) -- and it covers exactly what the
fixtures need: the wasm32 MVP profile's function, memory, global, export,
start and data fields, every MVP numeric instruction the fixtures reach, and
both the flat and the folded instruction forms. Anything outside that is an
error naming the construct, never a silently wrong module.

The output is ordinary wasm: `wat2wasm` from wabt accepts the same sources, so a
fixture can be cross-checked against it by anybody who has it.
"""

import struct
import sys

VALTYPES = {"i32": 0x7F, "i64": 0x7E, "f32": 0x7D, "f64": 0x7C}

# opcode, then the immediate kind: None, "local", "global", "func", "label",
# "mem" (a memarg, with the natural alignment as log2), "i32", "i64", "f32",
# "f64", "zero" (memory.size/grow's reserved byte).
OPS = {
    "unreachable": (0x00, None), "nop": (0x01, None),
    "br": (0x0C, "label"), "br_if": (0x0D, "label"), "return": (0x0F, None),
    "call": (0x10, "func"), "drop": (0x1A, None), "select": (0x1B, None),
    "local.get": (0x20, "local"), "local.set": (0x21, "local"),
    "local.tee": (0x22, "local"),
    "global.get": (0x23, "global"), "global.set": (0x24, "global"),
    "i32.load": (0x28, ("mem", 2)), "i64.load": (0x29, ("mem", 3)),
    "f32.load": (0x2A, ("mem", 2)), "f64.load": (0x2B, ("mem", 3)),
    "i32.load8_s": (0x2C, ("mem", 0)), "i32.load8_u": (0x2D, ("mem", 0)),
    "i32.load16_s": (0x2E, ("mem", 1)), "i32.load16_u": (0x2F, ("mem", 1)),
    "i32.store": (0x36, ("mem", 2)), "i64.store": (0x37, ("mem", 3)),
    "f32.store": (0x38, ("mem", 2)), "f64.store": (0x39, ("mem", 3)),
    "i32.store8": (0x3A, ("mem", 0)), "i32.store16": (0x3B, ("mem", 1)),
    "memory.size": (0x3F, "zero"), "memory.grow": (0x40, "zero"),
    "i32.const": (0x41, "i32"), "i64.const": (0x42, "i64"),
    "f32.const": (0x43, "f32"), "f64.const": (0x44, "f64"),
    "i32.eqz": (0x45, None), "i32.eq": (0x46, None), "i32.ne": (0x47, None),
    "i32.lt_s": (0x48, None), "i32.lt_u": (0x49, None),
    "i32.gt_s": (0x4A, None), "i32.gt_u": (0x4B, None),
    "i32.le_s": (0x4C, None), "i32.le_u": (0x4D, None),
    "i32.ge_s": (0x4E, None), "i32.ge_u": (0x4F, None),
    "f32.eq": (0x5B, None), "f32.ne": (0x5C, None), "f32.lt": (0x5D, None),
    "f32.gt": (0x5E, None), "f32.le": (0x5F, None), "f32.ge": (0x60, None),
    "i32.clz": (0x67, None), "i32.ctz": (0x68, None), "i32.popcnt": (0x69, None),
    "i32.add": (0x6A, None), "i32.sub": (0x6B, None), "i32.mul": (0x6C, None),
    "i32.div_s": (0x6D, None), "i32.div_u": (0x6E, None),
    "i32.rem_s": (0x6F, None), "i32.rem_u": (0x70, None),
    "i32.and": (0x71, None), "i32.or": (0x72, None), "i32.xor": (0x73, None),
    "i32.shl": (0x74, None), "i32.shr_s": (0x75, None),
    "i32.shr_u": (0x76, None), "i32.rotl": (0x77, None),
    "i32.rotr": (0x78, None),
    "f32.abs": (0x8B, None), "f32.neg": (0x8C, None), "f32.ceil": (0x8D, None),
    "f32.floor": (0x8E, None), "f32.trunc": (0x8F, None),
    "f32.nearest": (0x90, None), "f32.sqrt": (0x91, None),
    "f32.add": (0x92, None), "f32.sub": (0x93, None), "f32.mul": (0x94, None),
    "f32.div": (0x95, None), "f32.min": (0x96, None), "f32.max": (0x97, None),
    "i32.trunc_f32_s": (0xA8, None), "i32.trunc_f32_u": (0xA9, None),
    "f32.convert_i32_s": (0xB2, None), "f32.convert_i32_u": (0xB3, None),
    "i32.reinterpret_f32": (0xBC, None), "f32.reinterpret_i32": (0xBE, None),
}
BLOCKS = {"block": 0x02, "loop": 0x03, "if": 0x04}


class WatError(Exception):
    pass


# -- reading ------------------------------------------------------------------

class Str(object):
    """A string literal: its BYTES, which is what every wasm string is."""

    def __init__(self, data):
        self.data = data


def _unescape(body):
    out = bytearray()
    i = 0
    while i < len(body):
        c = body[i]
        if c != "\\":
            out += c.encode("utf-8")
            i += 1
            continue
        nxt = body[i + 1]
        simple = {"n": 10, "t": 9, "r": 13, '"': 34, "'": 39, "\\": 92}
        if nxt in simple:
            out.append(simple[nxt])
            i += 2
        elif nxt == "u":
            end = body.index("}", i)
            out += chr(int(body[i + 3:end], 16)).encode("utf-8")
            i = end + 1
        else:
            out.append(int(body[i + 1:i + 3], 16))
            i += 3
    return bytes(out)


def tokenize(text):
    toks = []
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if c in " \t\r\n":
            i += 1
        elif text.startswith(";;", i):
            j = text.find("\n", i)
            i = n if j < 0 else j
        elif text.startswith("(;", i):
            j = text.find(";)", i)
            if j < 0:
                raise WatError("unterminated block comment")
            i = j + 2
        elif c in "()":
            toks.append(c)
            i += 1
        elif c == '"':
            j = i + 1
            while text[j] != '"':
                j += 2 if text[j] == "\\" else 1
            toks.append(Str(_unescape(text[i + 1:j])))
            i = j + 1
        else:
            j = i
            while j < n and text[j] not in " \t\r\n()":
                j += 1
            toks.append(text[i:j])
            i = j
    return toks


def parse(text):
    stack = [[]]
    for t in tokenize(text):
        if t == "(":
            stack.append([])
        elif t == ")":
            if len(stack) < 2:
                raise WatError("unbalanced ')'")
            done = stack.pop()
            stack[-1].append(done)
        else:
            stack[-1].append(t)
    if len(stack) != 1 or len(stack[0]) != 1 or stack[0][0][:1] != ["module"]:
        raise WatError("expected exactly one (module ...)")
    return stack[0][0]


# -- encoding -----------------------------------------------------------------

def uleb(v):
    out = bytearray()
    while True:
        b = v & 0x7F
        v >>= 7
        out.append(b | (0x80 if v else 0))
        if not v:
            return bytes(out)


def sleb(v):
    out = bytearray()
    while True:
        b = v & 0x7F
        v >>= 7
        done = (v == 0 and not b & 0x40) or (v == -1 and b & 0x40)
        out.append(b | (0 if done else 0x80))
        if done:
            return bytes(out)


def name(s):
    data = s.encode("utf-8") if isinstance(s, str) else s
    return uleb(len(data)) + data


def vec(items):
    return uleb(len(items)) + b"".join(items)


def _int(tok, bits):
    t = tok.replace("_", "")
    neg = t.startswith("-")
    t = t.lstrip("+-")
    v = int(t, 16) if t.lower().startswith("0x") else int(t)
    v = -v if neg else v
    if not -(1 << (bits - 1)) <= v < (1 << bits):
        raise WatError("%s does not fit i%d" % (tok, bits))
    return v - (1 << bits) if v >= (1 << (bits - 1)) else v


def _float(tok):
    t = tok.replace("_", "")
    if "0x" in t.lower():
        return float.fromhex(t)
    return float(t)


# -- the module ---------------------------------------------------------------

def _is_id(x):
    return isinstance(x, str) and x.startswith("$")


def _kw(x):
    return x[0] if isinstance(x, list) and x and isinstance(x[0], str) else None


class Assembler(object):
    def __init__(self, module):
        self.fields = module[1:]
        if self.fields and _is_id(self.fields[0]):
            self.fields = self.fields[1:]
        self.types = []
        self.imports = []
        self.funcs = []           # (type index, local types, body)
        self.func_ids = {}
        self.n_funcs = 0
        self.mems = []
        self.mem_ids = {}
        self.n_mems = 0
        self.globals = []
        self.global_ids = {}
        self.n_globals = 0
        self.exports = []
        self.start = None
        self.datas = []

    def typeidx(self, params, results):
        sig = (tuple(params), tuple(results))
        if sig not in self.types:
            self.types.append(sig)
        return self.types.index(sig)

    @staticmethod
    def signature(items):
        """(param ...)/(result ...) at the head of `items`: the names, the
        param and result types, and how many items were consumed."""
        params, names, results = [], {}, []
        k = 0
        while k < len(items) and _kw(items[k]) in ("param", "result"):
            it = items[k]
            if it[0] == "param":
                rest = it[1:]
                if rest and _is_id(rest[0]):
                    names[rest[0]] = len(params)
                    rest = rest[1:]
                params.extend(rest)
            else:
                results.extend(it[1:])
            k += 1
        for t in params + results:
            if t not in VALTYPES:
                raise WatError("unknown value type %r" % t)
        return params, names, results, k

    def limits(self, items):
        nums = [int(x) for x in items if isinstance(x, str) and not _is_id(x)]
        if len(nums) == 1:
            return b"\x00" + uleb(nums[0])
        if len(nums) == 2:
            return b"\x01" + uleb(nums[0]) + uleb(nums[1])
        raise WatError("memory limits want a minimum and an optional maximum")

    def ref(self, tok, ids, what):
        if _is_id(tok):
            if tok not in ids:
                raise WatError("unknown %s %s" % (what, tok))
            return ids[tok]
        return int(tok)

    def declare(self):
        """Index spaces first, imports before definitions, as wasm numbers
        them -- so a call can name a function defined further down."""
        for f in self.fields:
            if _kw(f) == "import":
                desc = f[3]
                kind = desc[0]
                ident = desc[1] if len(desc) > 1 and _is_id(desc[1]) else None
                if kind == "func":
                    if ident:
                        self.func_ids[ident] = self.n_funcs
                    self.n_funcs += 1
                elif kind == "memory":
                    if ident:
                        self.mem_ids[ident] = self.n_mems
                    self.n_mems += 1
                elif kind == "global":
                    if ident:
                        self.global_ids[ident] = self.n_globals
                    self.n_globals += 1
                else:
                    raise WatError("cannot import a %s" % kind)
        self.imported = (self.n_funcs, self.n_mems, self.n_globals)
        for f in self.fields:
            kw = _kw(f)
            ident = f[1] if len(f) > 1 and _is_id(f[1]) else None
            if kw == "func":
                if ident:
                    self.func_ids[ident] = self.n_funcs
                self.n_funcs += 1
            elif kw == "memory":
                if ident:
                    self.mem_ids[ident] = self.n_mems
                self.n_mems += 1
            elif kw == "global":
                if ident:
                    self.global_ids[ident] = self.n_globals
                self.n_globals += 1

    def assemble(self):
        self.declare()
        for f in self.fields:
            kw = _kw(f)
            if kw == "import":
                self.field_import(f)
        for f in self.fields:
            kw = _kw(f)
            if kw == "import":
                continue
            handler = getattr(self, "field_" + str(kw), None)
            if handler is None:
                raise WatError("unsupported module field (%s ...)" % kw)
            handler(f)
        return self.emit()

    def field_import(self, f):
        mod, nm, desc = f[1], f[2], f[3]
        kind = desc[0]
        rest = desc[1:]
        if rest and _is_id(rest[0]):
            rest = rest[1:]
        if kind == "func":
            params, _, results, _ = self.signature(rest)
            body = b"\x00" + uleb(self.typeidx(params, results))
        elif kind == "memory":
            body = b"\x02" + self.limits(rest)
        else:
            mut = _kw(rest[0]) == "mut"
            t = rest[0][1] if mut else rest[0]
            body = b"\x03" + bytes((VALTYPES[t], 1 if mut else 0))
        self.imports.append(name(mod.data) + name(nm.data) + body)

    def field_func(self, f):
        items = f[1:]
        idx = self.imported[0] + len(self.funcs)
        if items and _is_id(items[0]):
            items = items[1:]
        while items and _kw(items[0]) == "export":
            self.exports.append((items[0][1].data, 0x00, idx))
            items = items[1:]
        params, names, results, k = self.signature(items)
        items = items[k:]
        local_types = list(params)
        while items and _kw(items[0]) == "local":
            rest = items[0][1:]
            if rest and _is_id(rest[0]):
                names[rest[0]] = len(local_types)
                rest = rest[1:]
            local_types.extend(rest)
            items = items[1:]
        code = Code(self, names)
        code.seq(items)
        code.out.append(0x0B)
        declared = local_types[len(params):]
        runs = []
        for t in declared:
            if runs and runs[-1][1] == t:
                runs[-1][0] += 1
            else:
                runs.append([1, t])
        locals_enc = vec([uleb(n) + bytes((VALTYPES[t],)) for n, t in runs])
        self.funcs.append((self.typeidx(params, results), locals_enc,
                           bytes(code.out)))

    def field_memory(self, f):
        items = f[1:]
        idx = self.imported[1] + len(self.mems)
        if items and _is_id(items[0]):
            items = items[1:]
        while items and _kw(items[0]) == "export":
            self.exports.append((items[0][1].data, 0x02, idx))
            items = items[1:]
        if items and _kw(items[0]) == "import":
            raise WatError("write an imported memory as (import ... (memory ...))")
        self.mems.append(self.limits(items))

    def field_global(self, f):
        items = f[1:]
        idx = self.imported[2] + len(self.globals)
        if items and _is_id(items[0]):
            items = items[1:]
        while items and _kw(items[0]) == "export":
            self.exports.append((items[0][1].data, 0x03, idx))
            items = items[1:]
        mut = _kw(items[0]) == "mut"
        t = items[0][1] if mut else items[0]
        init = Code(self, {})
        init.seq(items[1:])
        self.globals.append(bytes((VALTYPES[t], 1 if mut else 0))
                            + bytes(init.out) + b"\x0b")

    def field_export(self, f):
        nm, desc = f[1].data, f[2]
        kinds = {"func": (0x00, self.func_ids), "memory": (0x02, self.mem_ids),
                 "global": (0x03, self.global_ids)}
        if desc[0] not in kinds:
            raise WatError("cannot export a %s" % desc[0])
        kind, ids = kinds[desc[0]]
        self.exports.append((nm, kind, self.ref(desc[1], ids, desc[0])))

    def field_start(self, f):
        self.start = self.ref(f[1], self.func_ids, "func")

    def field_data(self, f):
        items = f[1:]
        if items and _is_id(items[0]):
            items = items[1:]
        if items and _kw(items[0]) == "memory":
            items = items[1:]
        off = items[0]
        if _kw(off) == "offset":
            off = off[1]
        expr = Code(self, {})
        expr.seq([off])
        payload = b"".join(s.data for s in items[1:])
        self.datas.append(b"\x00" + bytes(expr.out) + b"\x0b" + name(payload))

    def emit(self):
        def section(sid, body):
            return bytes((sid,)) + uleb(len(body)) + body

        out = bytearray(b"\x00asm\x01\x00\x00\x00")
        out += section(1, vec([b"\x60" + vec([bytes((VALTYPES[t],)) for t in p])
                               + vec([bytes((VALTYPES[t],)) for t in r])
                               for p, r in self.types]))
        if self.imports:
            out += section(2, vec(self.imports))
        if self.funcs:
            out += section(3, vec([uleb(t) for t, _, _ in self.funcs]))
        if self.mems:
            out += section(5, vec(self.mems))
        if self.globals:
            out += section(6, vec(self.globals))
        if self.exports:
            out += section(7, vec([name(n) + bytes((k,)) + uleb(i)
                                   for n, k, i in self.exports]))
        if self.start is not None:
            out += section(8, uleb(self.start))
        if self.funcs:
            bodies = []
            for _, locs, code in self.funcs:
                body = locs + code
                bodies.append(uleb(len(body)) + body)
            out += section(10, vec(bodies))
        if self.datas:
            out += section(11, vec(self.datas))
        return bytes(out)


class Code(object):
    """One function body (or a constant expression) as bytes."""

    def __init__(self, asm, local_ids):
        self.asm = asm
        self.locals = local_ids
        self.labels = []
        self.out = bytearray()

    def blocktype(self, items):
        if items and _kw(items[0]) == "result":
            t = items[0][1:]
            if len(t) != 1:
                raise WatError("a block has at most one result in the MVP")
            return bytes((VALTYPES[t[0]],)), 1
        return b"\x40", 0

    def label(self, tok):
        if _is_id(tok):
            for depth, lab in enumerate(reversed(self.labels)):
                if lab == tok:
                    return depth
            raise WatError("unknown label %s" % tok)
        return int(tok)

    def immediate(self, kind, toks, k):
        """Encode op immediates from the flat token list at k; returns the
        bytes and the new position."""
        a = self.asm
        if kind is None:
            return b"", k
        if kind == "zero":
            return b"\x00", k
        tok = toks[k]
        if kind == "local":
            return uleb(a.ref(tok, self.locals, "local")), k + 1
        if kind == "global":
            return uleb(a.ref(tok, a.global_ids, "global")), k + 1
        if kind == "func":
            return uleb(a.ref(tok, a.func_ids, "func")), k + 1
        if kind == "label":
            return uleb(self.label(tok)), k + 1
        if kind == "i32":
            return sleb(_int(tok, 32)), k + 1
        if kind == "i64":
            return sleb(_int(tok, 64)), k + 1
        if kind == "f32":
            return struct.pack("<f", _float(tok)), k + 1
        if kind == "f64":
            return struct.pack("<d", _float(tok)), k + 1
        natural = kind[1]
        offset, align = 0, natural
        while k < len(toks) and isinstance(toks[k], str) and "=" in toks[k]:
            key, val = toks[k].split("=", 1)
            if key == "offset":
                offset = int(val, 0)
            elif key == "align":
                align = {1: 0, 2: 1, 4: 2, 8: 3}[int(val, 0)]
            else:
                raise WatError("unknown memarg %s" % toks[k])
            k += 1
        return uleb(align) + uleb(offset), k

    def seq(self, items):
        """A sequence of instructions, flat or folded."""
        k = 0
        while k < len(items):
            it = items[k]
            if isinstance(it, list):
                self.folded(it)
                k += 1
                continue
            if it in BLOCKS:
                k += 1
                lab = None
                if k < len(items) and _is_id(items[k]):
                    lab = items[k]
                    k += 1
                bt, used = self.blocktype(items[k:])
                k += used
                self.out.append(BLOCKS[it])
                self.out += bt
                self.labels.append(lab)
                continue
            if it == "else":
                self.out.append(0x05)
                k += 1
                if k < len(items) and _is_id(items[k]):
                    k += 1
                continue
            if it == "end":
                self.out.append(0x0B)
                self.labels.pop()
                k += 1
                if k < len(items) and _is_id(items[k]):
                    k += 1
                continue
            if it not in OPS:
                raise WatError("unsupported instruction %r" % it)
            op, kind = OPS[it]
            imm, k = self.immediate(kind, items, k + 1)
            self.out.append(op)
            self.out += imm

    def folded(self, it):
        head = it[0]
        if head in ("block", "loop"):
            rest = it[1:]
            lab = None
            if rest and _is_id(rest[0]):
                lab, rest = rest[0], rest[1:]
            bt, used = self.blocktype(rest)
            self.out.append(BLOCKS[head])
            self.out += bt
            self.labels.append(lab)
            self.seq(rest[used:])
            self.out.append(0x0B)
            self.labels.pop()
            return
        if head == "if":
            rest = it[1:]
            lab = None
            if rest and _is_id(rest[0]):
                lab, rest = rest[0], rest[1:]
            bt, used = self.blocktype(rest)
            rest = rest[used:]
            conds = [r for r in rest if _kw(r) not in ("then", "else")]
            then = [r for r in rest if _kw(r) == "then"]
            other = [r for r in rest if _kw(r) == "else"]
            for c in conds:
                self.folded(c)
            self.out.append(BLOCKS["if"])
            self.out += bt
            self.labels.append(lab)
            if then:
                self.seq(then[0][1:])
            if other:
                self.out.append(0x05)
                self.seq(other[0][1:])
            self.out.append(0x0B)
            self.labels.pop()
            return
        if head not in OPS:
            raise WatError("unsupported instruction %r" % head)
        op, kind = OPS[head]
        imm, k = self.immediate(kind, it, 1)
        for operand in it[k:]:
            if not isinstance(operand, list):
                raise WatError("stray token %r in (%s ...)" % (operand, head))
            self.folded(operand)
        self.out.append(op)
        self.out += imm


def assemble(text):
    """WAT source -> module bytes."""
    return Assembler(parse(text)).assemble()


def build_cart(src, dst):
    """Copy a fixture cart folder, subfolders included, assembling its
    main.wat into main.wasm -- the form a host loads. The .wat itself is not
    copied."""
    import os
    import shutil
    if not os.path.isdir(dst):
        os.makedirs(dst)
    for entry in sorted(os.listdir(src)):
        full = os.path.join(src, entry)
        if entry == "main.wat":
            with open(full, encoding="utf-8") as f:
                blob = assemble(f.read())
            with open(os.path.join(dst, "main.wasm"), "wb") as f:
                f.write(blob)
        elif os.path.isdir(full):
            shutil.copytree(full, os.path.join(dst, entry))
        elif os.path.isfile(full):
            shutil.copyfile(full, os.path.join(dst, entry))


def main(argv):
    if len(argv) == 4 and argv[1] == "--cart":
        build_cart(argv[2], argv[3])
        return 0
    if len(argv) != 3:
        sys.stderr.write("usage: wat.py in.wat out.wasm | --cart src.moy out.moy\n")
        return 2
    with open(argv[1], encoding="utf-8") as f:
        blob = assemble(f.read())
    with open(argv[2], "wb") as f:
        f.write(blob)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
