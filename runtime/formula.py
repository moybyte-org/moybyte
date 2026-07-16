"""Sheets' hand-rolled formula engine (#78).

The portable subset BANS eval/exec, so a spreadsheet formula is compiled the way
the blocks compiler compiles blocks: a tiny tokenizer + a recursive-descent
parser + an evaluator, all hand-rolled -- no `re`, no `ast`, MicroPython-safe
(host == device). A kid's `=A1+B1*2` becomes a little tuple tree, walked once per
recalc.

The vocabulary is deliberately the block-operator vocabulary (#48) -- mod, round,
abs, min, max -- so Sheets and Blocks teach the SAME words, plus sum/avg over a
range (A1:C1). Cell refs are A1-style (column letters + 1-based row). Arithmetic
is + - * / with parentheses and unary minus.

Nothing here ever raises out to the app: `Sheet.recalc()` turns a malformed
formula into the error value `#ERR` shown in the cell, and a reference cycle into
`#LOOP`, never a crash or a hang. The parser raises `FormulaError` internally; the
Sheet catches it. `eval_formula()` is the guarded one-shot entry the unit tests
and the app call.
"""

ERR = "#ERR"       # malformed formula / bad reference / math error (e.g. /0)
LOOP = "#LOOP"     # a reference cycle (A1=B1, B1=A1)
_ERRORS = (ERR, LOOP)

# Function names -- the block-operator vocabulary (#48) plus sum/avg over ranges.
_FUNCS = ("sum", "avg", "mod", "round", "abs", "min", "max")


class FormulaError(Exception):
    """Any malformed formula: bad token, bad ref, wrong arity, math error. Caught
    at the Sheet boundary and shown as `#ERR` -- never escapes to the app."""


# --------------------------------------------------------------------------- #
# Cell reference algebra: A1 <-> (col_index, row_index), both 0-based.
# --------------------------------------------------------------------------- #

def col_to_index(letters):
    """"A" -> 0, "Z" -> 25, "AA" -> 26. Bijective base-26 (spreadsheet columns)."""
    letters = letters.upper()
    n = 0
    for ch in letters:
        if not ("A" <= ch <= "Z"):
            raise FormulaError("bad column: " + letters)
        n = n * 26 + (ord(ch) - 64)     # 'A' == 65 -> 1
    return n - 1


def index_to_col(idx):
    """0 -> "A", 25 -> "Z", 26 -> "AA" (the inverse of col_to_index)."""
    if idx < 0:
        raise FormulaError("bad column index")
    out = ""
    idx += 1
    while idx > 0:
        idx, rem = divmod(idx - 1, 26)
        out = chr(65 + rem) + out
    return out


def parse_ref(ref):
    """"B3" -> (1, 2): (col_index, row_index), both 0-based. Rows are 1-based in
    the A1 name (A1 is the top-left cell -> row_index 0)."""
    ref = ref.upper()
    i = 0
    while i < len(ref) and "A" <= ref[i] <= "Z":
        i += 1
    if i == 0 or i >= len(ref):
        raise FormulaError("bad cell ref: " + ref)
    letters = ref[:i]
    digits = ref[i:]
    for ch in digits:
        if not ("0" <= ch <= "9"):
            raise FormulaError("bad cell ref: " + ref)
    row = int(digits)
    if row < 1:
        raise FormulaError("bad row: " + ref)
    return (col_to_index(letters), row - 1)


def make_ref(col, row):
    """(1, 2) -> "B3" -- the inverse of parse_ref, for building/normalising keys."""
    return index_to_col(col) + str(row + 1)


# --------------------------------------------------------------------------- #
# Tokenizer -- hand-rolled char scan (no `re`).
# --------------------------------------------------------------------------- #
#   ("num", value)   ("cell", "A1")   ("name", "sum")   ("op", "+")

def tokenize(src):
    toks = []
    i = 0
    n = len(src)
    while i < n:
        ch = src[i]
        if ch in " \t\r\n":
            i += 1
            continue
        if ch in "+-*/(),:":
            toks.append(("op", ch))
            i += 1
            continue
        if ch.isdigit() or ch == ".":
            j = i
            dot = False
            while j < n and (src[j].isdigit() or src[j] == "."):
                if src[j] == ".":
                    if dot:
                        raise FormulaError("bad number")
                    dot = True
                j += 1
            text = src[i:j]
            toks.append(("num", float(text) if dot else int(text)))
            i = j
            continue
        if ch.isalpha():
            j = i
            while j < n and src[j].isalpha():
                j += 1
            letters = src[i:j]
            # Letters immediately followed by digits -> a cell ref (A1); letters
            # alone -> a function name (sum, mod, ...). "A1B" is malformed.
            k = j
            while k < n and src[k].isdigit():
                k += 1
            if k > j:
                if k < n and src[k].isalpha():
                    raise FormulaError("bad name: " + src[i:k + 1])
                toks.append(("cell", src[i:k].upper()))
                i = k
            else:
                toks.append(("name", letters.lower()))
                i = j
            continue
        raise FormulaError("bad character: " + ch)
    return toks


# --------------------------------------------------------------------------- #
# Parser -- recursive descent. AST nodes are plain tuples (MicroPython-cheap):
#   ("num", v) ("cell", "A1") ("range", "A1", "B3")
#   ("neg", node) ("bin", op, left, right) ("call", name, [args])
# --------------------------------------------------------------------------- #

class _Parser:
    def __init__(self, toks):
        self.toks = toks
        self.i = 0

    def _peek(self):
        return self.toks[self.i] if self.i < len(self.toks) else None

    def _next(self):
        t = self._peek()
        self.i += 1
        return t

    def _eat_op(self, ch):
        t = self._peek()
        if t is None or t[0] != "op" or t[1] != ch:
            raise FormulaError("expected '" + ch + "'")
        self.i += 1

    def parse(self):
        node = self._expr()
        if self.i != len(self.toks):
            raise FormulaError("unexpected trailing input")
        return node

    def _expr(self):                       # + and - (lowest precedence)
        node = self._term()
        while True:
            t = self._peek()
            if t is not None and t[0] == "op" and t[1] in "+-":
                self._next()
                node = ("bin", t[1], node, self._term())
            else:
                return node

    def _term(self):                       # * and /
        node = self._factor()
        while True:
            t = self._peek()
            if t is not None and t[0] == "op" and t[1] in "*/":
                self._next()
                node = ("bin", t[1], node, self._factor())
            else:
                return node

    def _factor(self):                     # unary +/- then a primary
        t = self._peek()
        if t is not None and t[0] == "op" and t[1] in "+-":
            self._next()
            operand = self._factor()
            return ("neg", operand) if t[1] == "-" else operand
        return self._primary()

    def _primary(self):
        t = self._next()
        if t is None:
            raise FormulaError("unexpected end of formula")
        kind, val = t
        if kind == "num":
            return ("num", val)
        if kind == "cell":
            # A range only appears here (A1:B3), always as a function argument.
            nxt = self._peek()
            if nxt is not None and nxt[0] == "op" and nxt[1] == ":":
                self._next()
                end = self._next()
                if end is None or end[0] != "cell":
                    raise FormulaError("bad range")
                return ("range", val, end[1])
            return ("cell", val)
        if kind == "name":
            if val not in _FUNCS:
                raise FormulaError("unknown function: " + val)
            self._eat_op("(")
            args = []
            if not self._is_op(")"):
                args.append(self._expr())
                while self._is_op(","):
                    self._next()
                    args.append(self._expr())
            self._eat_op(")")
            return ("call", val, args)
        if kind == "op" and val == "(":
            node = self._expr()
            self._eat_op(")")
            return node
        raise FormulaError("unexpected token")

    def _is_op(self, ch):
        t = self._peek()
        return t is not None and t[0] == "op" and t[1] == ch


def parse(src):
    """Compile a formula string (no leading '=') to an AST. Raises FormulaError."""
    return _Parser(tokenize(src)).parse()


# --------------------------------------------------------------------------- #
# Evaluator -- walks the AST, resolving cell refs through a callback.
# --------------------------------------------------------------------------- #
#   resolve(col, row) -> a numeric value for that cell (0 for blank). It may
#   raise FormulaError(LOOP)/FormulaError(ERR) to propagate a cycle/error.

def _num(v):
    """Coerce a resolved cell value into arithmetic. Blank -> 0; a number stays;
    text (or an error value) is not a number -> #ERR."""
    if isinstance(v, bool):
        raise FormulaError("bad value")
    if isinstance(v, (int, float)):
        return v
    if v == "" or v is None:
        return 0
    raise FormulaError("not a number: " + str(v))


def _flatten(node, resolve):
    """Expand one function argument to a flat list of numbers -- a range becomes
    all its numeric cells; a scalar becomes a single number."""
    if node[0] == "range":
        c0, r0 = parse_ref(node[1])
        c1, r1 = parse_ref(node[2])
        if c0 > c1:
            c0, c1 = c1, c0
        if r0 > r1:
            r0, r1 = r1, r0
        out = []
        for r in range(r0, r1 + 1):
            for c in range(c0, c1 + 1):
                out.append(_num(resolve(c, r)))
        return out
    return [evaluate(node, resolve)]


def evaluate(node, resolve):
    kind = node[0]
    if kind == "num":
        return node[1]
    if kind == "cell":
        c, r = parse_ref(node[1])
        return _num(resolve(c, r))
    if kind == "range":
        raise FormulaError("range only allowed inside sum/avg/min/max")
    if kind == "neg":
        return -evaluate(node[1], resolve)
    if kind == "bin":
        a = evaluate(node[2], resolve)
        b = evaluate(node[3], resolve)
        op = node[1]
        if op == "+":
            return a + b
        if op == "-":
            return a - b
        if op == "*":
            return a * b
        if b == 0:
            raise FormulaError("divide by zero")
        return a / b
    if kind == "call":
        return _call(node[1], node[2], resolve)
    raise FormulaError("bad node")


def _call(name, args, resolve):
    if name in ("sum", "avg"):
        nums = []
        for a in args:
            nums.extend(_flatten(a, resolve))
        total = 0
        for x in nums:
            total += x
        if name == "sum":
            return total
        if not nums:
            return 0
        return total / len(nums)
    # min/max also accept ranges (a whole column of scores).
    if name in ("min", "max"):
        nums = []
        for a in args:
            nums.extend(_flatten(a, resolve))
        if not nums:
            raise FormulaError(name + " needs a value")
        return min(nums) if name == "min" else max(nums)
    vals = [evaluate(a, resolve) for a in args]
    if name == "mod":
        if len(vals) != 2:
            raise FormulaError("mod needs two values")
        if vals[1] == 0:
            raise FormulaError("mod by zero")
        return vals[0] - vals[1] * (vals[0] // vals[1])
    if name == "round":
        if len(vals) != 1:
            raise FormulaError("round needs one value")
        return _clean(round(vals[0]))
    if name == "abs":
        if len(vals) != 1:
            raise FormulaError("abs needs one value")
        return abs(vals[0])
    raise FormulaError("unknown function: " + name)


def _clean(v):
    """Drop a trailing .0 so integer-valued results present as ints."""
    if isinstance(v, float) and v == int(v):
        return int(v)
    return v


def eval_formula(text, resolve):
    """Guarded one-shot: compile + evaluate `text` (with or without a leading '='),
    returning a value, or the error string ERR. NEVER raises -- malformed input is
    `#ERR`. A resolver that raises FormulaError(LOOP) surfaces as LOOP. This is the
    entry the app and the unit tests call."""
    src = text[1:] if text.startswith("=") else text
    try:
        return _clean(evaluate(parse(src), resolve))
    except FormulaError as exc:
        return LOOP if str(exc) == LOOP else ERR
    except Exception:  # noqa: BLE001 -- a bad formula can never crash the shell
        return ERR


# --------------------------------------------------------------------------- #
# The sheet model: cells (raw text) -> computed values, with cycle detection.
# --------------------------------------------------------------------------- #

def parse_literal(raw):
    """A non-formula cell: a number if it reads as one, else the raw string.
    Blank stays "" (kept out of numeric coercion by _num)."""
    s = raw.strip()
    if s == "":
        return ""
    neg = s[0] in "+-"
    body = s[1:] if neg else s
    if body and _looks_numeric(body):
        return float(s) if "." in s else int(s)
    return raw


def _looks_numeric(body):
    dot = False
    seen = False
    for ch in body:
        if ch == ".":
            if dot:
                return False
            dot = True
        elif "0" <= ch <= "9":
            seen = True
        else:
            return False
    return seen


class Sheet:
    """A tiny grid of cells. `cells` maps an "A1" key to the RAW text the kid typed
    (a formula like "=A1+1" or a literal like "3" / "hello"). `recalc()` produces
    `values`, the computed value per cell, resolving formulas on demand in
    dependency order with cycle detection (grids are tiny -- naive is fine, and
    readable).

    Nothing here throws: a malformed formula is `#ERR`, a reference loop is `#LOOP`.
    """

    def __init__(self, name="Sheet", rows=20, cols=8):
        self.name = str(name)
        self.rows = int(rows)
        self.cols = int(cols)
        self.cells = {}        # "A1" -> raw text
        self.values = {}       # "A1" -> computed value (set by recalc)

    # -- editing ---------------------------------------------------------------

    def set_cell(self, col, row, raw):
        key = make_ref(col, row)
        if raw is None or raw == "":
            self.cells.pop(key, None)
        else:
            self.cells[key] = raw
        self.recalc()

    def raw_at(self, col, row):
        return self.cells.get(make_ref(col, row), "")

    def value_at(self, col, row):
        return self.values.get(make_ref(col, row), "")

    # -- recompute -------------------------------------------------------------

    def recalc(self):
        self.values = {}
        cache = self.values
        asts = {}              # key -> parsed AST (or ERR sentinel) memo

        def compute(col, row, stack):
            key = make_ref(col, row)
            if key in cache:
                return cache[key]
            raw = self.cells.get(key, "")
            if raw == "":
                cache[key] = ""
                return ""
            if not raw.startswith("="):
                cache[key] = parse_literal(raw)
                return cache[key]
            if key in stack:                        # a reference cycle
                cache[key] = LOOP
                return LOOP
            if key not in asts:
                try:
                    asts[key] = parse(raw[1:])
                except Exception:  # noqa: BLE001
                    asts[key] = ERR
            ast = asts[key]
            if ast == ERR:
                cache[key] = ERR
                return ERR
            stack.add(key)

            def resolve(c, r):
                v = compute(c, r, stack)
                if v in _ERRORS:
                    raise FormulaError(v)
                return v

            try:
                cache[key] = _clean(evaluate(ast, resolve))
            except FormulaError as exc:
                cache[key] = LOOP if str(exc) == LOOP else ERR
            except Exception:  # noqa: BLE001
                cache[key] = ERR
            stack.discard(key)
            return cache[key]

        for key in list(self.cells.keys()):
            col, row = parse_ref(key)
            compute(col, row, set())
        return self.values

    # -- serialisation (the kid-greppable .moysheet blob) ----------------------

    def to_dict(self):
        """The moysheet-v1 structure: formula (`f`) + computed value (`v`) per
        populated cell. Tiny, engine-free JSON (the v0.4 portability contract)."""
        self.recalc()
        cells = {}
        for key, raw in self.cells.items():
            cells[key] = {"f": raw, "v": self.values.get(key, "")}
        return {"format": "moysheet-v1", "name": self.name,
                "rows": self.rows, "cols": self.cols, "cells": cells}

    @classmethod
    def from_dict(cls, data):
        if not isinstance(data, dict):
            return cls()
        sheet = cls(data.get("name", "Sheet"),
                    int(data.get("rows", 20)), int(data.get("cols", 8)))
        cells = data.get("cells")
        if isinstance(cells, dict):
            for key, entry in cells.items():
                if isinstance(entry, dict):
                    raw = entry.get("f", "")
                elif isinstance(entry, str):
                    raw = entry
                else:
                    raw = ""
                if raw != "":
                    sheet.cells[str(key).upper()] = raw
        sheet.recalc()
        return sheet
