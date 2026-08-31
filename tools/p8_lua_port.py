#!/usr/bin/env python3
"""Port a PICO-8 `.p8` cart to a `"runtime": "lua"` `.moy` cartridge.

`p8_import.py` beside this file converts the ASSETS. Because a moy cart is Lua
(SPEC.md 4), a PICO-8 cart's own code can very nearly RUN too, so this tool
emits a complete Lua cart --

  main.lua =  [PICO-8 compat shim]  the p8 API written over the moy cart API
            + [__gff__ flag table]  fget/map-layer masks (import_p8 defers gff)
            + [full 128x64 map]     __map__ rows 0-31 PLUS the rows 32-63 that
                                    PICO-8 stores in the BOTTOM half of __gfx__
                                    (low-nibble-first bytes -- the shared-RAM
                                    quirk), normalized to big-endian hex here
            + [the cart's own Lua]  mechanically converted p8-Lua -> Lua 5.4
                                    (`!=` -> `~=`, `x += e` -> `x = x + (e)`,
                                    one-line `if (c) s` -> `if c then s end`,
                                    `_init/_update/_draw` renamed to `p8_*` so
                                    the shim can pace them at PICO-8's fixed
                                    30fps from moy's dt-driven loop)

plus sprites.moygfx / sounds.json via import_p8's converters and a lua-runtime
manifest that declares `"canvas": "128x128"` (SPEC.md 1/3.1) -- the cart draws
REAL p8 pixels 1:1 on its own raster and the HOST owns all scaling (the old
draw-2x-yourself shim is gone; a native-res cart fills a quarter of the pixels).
Text is the PICO-8 system font itself (3x5, 4px advance -- Lexaloffle, CC0),
drawn by the shim through pix(), because SPEC.md 6's 8px `print` glyphs are
twice the size p8 meant on a 128px raster. p8 `sin`/`cos` turn-and-flip
semantics, table verbs (`add/del/foreach/all/count`), and flag-masked `map()`
are implemented in the shim over the ordinary cart verbs, so the port exercises
the whole Lua bridge.

Usage:
    python3 p8_lua_port.py cart.p8 out_dir [--title "Name"]

The emitted cart is only as faithful as doubles-instead-of-16.16-fixed-point
allows (fine for most carts; Celeste Classic community ports do the same).
LICENSING NOTE: PICO-8 BBS carts default to CC BY-NC-SA 4.0 -- ported carts are
dev/test material unless the license says otherwise; keep them out of
system_carts/ and ship an attribution note next to the cart. The manifest says
so too (`safe_to_share: false`), so a host's share paths never have to infer it.

THIS FILE RUNS ON MICROPYTHON, and that is a CONSTRAINT rather than an
observation: a console can port a dropped cart in the browser (or on a board) by
running THIS code inside its own VM, which is the only arrangement in which the
desktop and the console cannot disagree about what a `.p8` becomes. So: no
`os.path`, no `os.makedirs`, no f-strings, no `json` `indent=`, no `str.isalnum`,
and NOTHING in `re` beyond MicroPython's subset -- no lookarounds, no `(?:...)`,
no inline `(?m)` flags, which its engine rejects at COMPILE time with "regex too
complex". `port_sections()` exists for the same reason: a caller that was handed
the bytes should not have to find them on a filesystem again.
"""

import json
import re
import os
import sys

from p8_import import (  # the asset converters, vendored beside this file
    read_p8, _title_from, gfx_to_kgfx, icon_tile, sfx_music_to_sounds,
    music_start_map)


# --------------------------------------------------------------------------
# p8-Lua -> Lua 5.4 (the mechanical dialect transforms)
# --------------------------------------------------------------------------

# `//` FIRST is not cosmetic: `_expand_compound` takes the earliest match, and
# in `i //= w` the one-char `/` also matches -- one character later, against an
# lvalue that is a slash. Longest-first is what makes the earliest-wins rule
# pick the real operator.
_ASSIGN_OPS = ("//", "..", "+", "-", "*", "/", "%", "^")
_LIFECYCLE = ("_init", "_update", "_update60", "_draw")


def _isword(ch):
    """`ch.isalnum()`, spelled for a stdlib that does not have it.

    MicroPython's `str` carries isalpha/isdigit and no isalnum, and this file
    runs there (see the module header). Identifier chars are the only thing
    isalnum was ever asked here, so the two agree on every input this sees."""
    return ch.isalpha() or ch.isdigit()


def _ident_char(ch):
    return ch == "_" or _isword(ch)


_LUA_ESCAPES = 'abfnrtvxzu\\\'"\n'
_LONG_MASK = "__p8lstr%d__"


def _long_open(line, i):
    """The level of a long bracket opening at `i` (`[[` is 0, `[=[` is 1), or -1."""
    if i >= len(line) or line[i] != "[":
        return -1                            # a line that ends in `--`
    j = i + 1
    while j < len(line) and line[j] == "=":
        j += 1
    if j < len(line) and line[j] == "[":
        return j - i - 1
    return -1


def _scan_line(line, state, spans):
    """One pass over a source line -> (code, comment, state).

    Every dialect transform below scans for operators "outside strings", and
    every one of them means QUOTED strings. A `[[...]]` long string is neither
    quoted nor one line, so `+=` inside a cart's DATA blob got expanded as if it
    were code -- `bqc+>qo` became `bqc = bqc + (>qo`, in the middle of a track
    table, and the cart died a thousand lines away with `unexpected symbol near
    ')'`. Nothing about that error names the damage or where it happened.

    So long strings are masked behind an opaque identifier (a complete term, the
    way a string is, so `_rhs_end` reads the line the same) and put back after.
    The same walk carries a `--[[ ]]` block comment across lines, which the old
    per-line comment split could not do either: it ended the comment at the
    newline and handed the prose inside it to the parser as code.

    `code` comes back None for a line that must pass through untouched.
    """
    if state is not None:
        kind, level = state
        close = "]" + "=" * level + "]"
        k = line.find(close)
        if k < 0:
            if kind == "string":
                spans.append(line)
                return _LONG_MASK % (len(spans) - 1), "", state
            return None, line, state
        if kind == "comment":
            return None, line, None
        spans.append(line[:k + len(close)])
        head = _LONG_MASK % (len(spans) - 1)
        rest, comment, state = _scan_line(line[k + len(close):], None, spans)
        if rest is None:
            return None, line, state
        return head + rest, comment, state
    out = []
    i = 0
    n = len(line)
    q = None
    while i < n:
        ch = line[i]
        if q:
            out.append(ch)
            if ch == "\\" and i + 1 < n:
                i += 1
                out.append(line[i])
            elif ch == q:
                q = None
            i += 1
            continue
        if ch in "'\"":
            q = ch
            out.append(ch)
            i += 1
            continue
        if ch == "-" and line[i + 1:i + 2] == "-":
            lv = _long_open(line, i + 2)
            if lv >= 0:
                close = "]" + "=" * lv + "]"
                if line.find(close, i + 2 + lv + 2) < 0:
                    return "".join(out), line[i:], ("comment", lv)
            return "".join(out), line[i:], None
        lv = _long_open(line, i)
        if lv >= 0:
            close = "]" + "=" * lv + "]"
            k = line.find(close, i + lv + 2)
            if k < 0:
                spans.append(line[i:])
                out.append(_LONG_MASK % (len(spans) - 1))
                return "".join(out), "", ("string", lv)
            spans.append(line[i:k + len(close)])
            out.append(_LONG_MASK % (len(spans) - 1))
            i = k + len(close)
            continue
        out.append(ch)
        i += 1
    return "".join(out), "", None


def _unmask_long_strings(code, spans):
    for k in range(len(spans) - 1, -1, -1):
        code = code.replace(_LONG_MASK % k, spans[k])
    return code


def _split_comment(code):
    """Split one line into (code, comment) honoring '--' only outside strings."""
    q = None
    i = 0
    n = len(code)
    while i < n:
        ch = code[i]
        if q:
            if ch == "\\":
                i += 1
            elif ch == q:
                q = None
        elif ch in "'\"":
            q = ch
        elif ch == "-" and i + 1 < n and code[i + 1] == "-":
            return code[:i], code[i:]
        i += 1
    return code, ""


def _find_outside_strings(code, sub, start=0):
    q = None
    i = start
    n = len(code)
    while i < n:
        ch = code[i]
        if q:
            if ch == "\\":
                i += 1
            elif ch == q:
                q = None
        elif ch in "'\"":
            q = ch
        elif code.startswith(sub, i):
            return i
        i += 1
    return -1


# p8's six button glyphs, as the button numbers `btn()` actually takes. They
# are ORDINARY CHARACTERS in cart source -- `if btn(<right>)` -- in two
# spellings: a P8SCII byte from a .p8.png ROM, and the UTF-8 emoji a text .p8
# stores. Lua 5.4 will not take either one in an expression, so a cart that
# reads its d-pad the way the manual shows it would not load at all.
_GLYPH_BTN = {
    "\x8b": "0", "\x91": "1", "\x94": "2",
    "\x83": "3", "\x8e": "4", "\x97": "5",
    "\u2b05": "0", "\u27a1": "1", "\u2b06": "2",
    "\u2b07": "3", "\U0001f17e": "4", "\u274e": "5",
}
_VARIATION = "\ufe0f"


def _read_number(code, i):
    """Read ONE p8 numeric literal starting at `i`; return (end, Lua spelling).

    Two things Lua 5.4's lexer does differently, both of which stop a real cart
    at LOAD time, before a single frame runs:

    `0b1010` -- and `0b0000100000000010.1`, a binary literal with a binary
    FRACTION -- is p8's spelling for a bit pattern. Lua has no `0b` at all, so
    it becomes a decimal here. (Hex passes through: Lua 5.4 reads `0xff.8`.)

    And p8's lexer ENDS a number at the first character that cannot continue
    one, which is why carts write `e and 0or 1` and `flip_x and-1or 1`. Lua
    keeps reading and reports `malformed number near '0o'`. The caller puts the
    space back; this returns where the number really stopped.
    """
    n = len(code)
    j = i
    if code[j] == "0" and j + 1 < n and code[j + 1] in "bB":
        j += 2
        st = j
        while j < n and code[j] in "01":
            j += 1
        whole = code[st:j]
        frac = ""
        if j < n and code[j] == "." and j + 1 < n and code[j + 1] in "01":
            j += 1
            st = j
            while j < n and code[j] in "01":
                j += 1
            frac = code[st:j]
        if not whole and not frac:
            return i + 1, code[i]            # a bare `0b`: not ours to touch
        val = int(whole or "0", 2)
        if frac:
            return j, str(val + int(frac, 2) / float(1 << len(frac)))
        return j, str(val)
    if code[j] == "0" and j + 1 < n and code[j + 1] in "xX":
        j += 2
        while j < n and (code[j] in "0123456789abcdefABCDEF." ):
            j += 1
        if j < n and code[j] in "pP":
            k = j + 1
            if k < n and code[k] in "+-":
                k += 1
            if k < n and code[k].isdigit():
                while k < n and code[k].isdigit():
                    k += 1
                j = k
        return j, code[i:j]
    while j < n and code[j].isdigit():
        j += 1
    if j < n and code[j] == ".":
        j += 1
        while j < n and code[j].isdigit():
            j += 1
    if j < n and code[j] in "eE":
        k = j + 1
        if k < n and code[k] in "+-":
            k += 1
        if k < n and code[k].isdigit():
            while k < n and code[k].isdigit():
                k += 1
            j = k
    return j, code[i:j]


def _fix_p8_tokens(code):
    """The p8 lexer's extensions, spelled the way Lua 5.4 reads them.

    Three of them, all outside strings: `\\` is p8's integer division and Lua
    spells that `//`; `0b...` literals become decimals; and a number butted
    straight against a word gets its space back (see `_read_number`).

    These are LOAD-time failures, not wrong pixels -- four of five carts pulled
    off the BBS died here, each on a different one, with the cart's own line
    number pointing at code that is perfectly good p8.
    """
    out = []
    i = 0
    n = len(code)
    q = None
    prev = " "
    while i < n:
        ch = code[i]
        if q:
            if ch == "\\" and i + 1 < n:
                nxt = code[i + 1]
                if nxt not in _LUA_ESCAPES and not nxt.isdigit():
                    # P8SCII: `"\^i"` is p8 telling its own print to invert.
                    # Lua reads `\^` as an invalid escape and refuses to LOAD
                    # the cart, so the backslash becomes a literal one -- the
                    # code shows up as text instead of stopping the cart.
                    out.append("\\")
                out.append(ch)
                out.append(nxt)
                i += 2
                continue
            out.append(ch)
            if ch == q:
                q = None
            i += 1
            continue
        if ch in "'\"":
            q = ch
            out.append(ch)
            prev = ch
            i += 1
            continue
        starts = ch.isdigit() and not (_ident_char(prev) or prev == ".")
        if not starts and ch == "." and i + 1 < n and code[i + 1].isdigit():
            # Only a preceding `.` blocks this, and it blocks the one case that
            # matters: `a..5` is a concatenation. `and.5or 2` is not a field
            # access -- no Lua field name starts with a digit -- so an
            # identifier char in front is p8's lexer again, not an index.
            starts = prev != "."
        if starts:
            j, text = _read_number(code, i)
            out.append(text)
            if j < n and _ident_char(code[j]):
                out.append(" ")              # `0or 1` -> `0 or 1`
            prev = code[j - 1]
            i = j
            continue
        if ch == "^" and code[i + 1:i + 2] == "^":
            out.append("~")                  # p8 spells bitwise xor `^^`
            prev = "~"
            i += 2
            continue
        if code.startswith(">>>", i):
            # p8's LOGICAL right shift. Lua 5.4's `>>` already is one (it is
            # defined on the integer, not the sign), so this is the same op.
            out.append(">>")
            prev = ">"
            i += 3
            continue
        if ch == "\\":
            out.append("//")                 # p8's integer division
            prev = ch
            i += 1
            continue
        if ch in _GLYPH_BTN:
            out.append(_GLYPH_BTN[ch])
            prev = "0"
            i += 1
            if i < n and code[i] == _VARIATION:
                i += 1                       # the emoji's variation selector
            continue
        out.append(ch)
        prev = ch
        i += 1
    return "".join(out)


def _expand_print_shorthand(code):
    """p8's `?x` -> `print(x)`, at a STATEMENT START only.

    `?` prints the rest of the LINE, so the arguments run to the end -- which is
    why this cannot be a plain textual swap for `print(`: there would be no
    closing paren to write, and the paren has to go somewhere a statement ends.

    "Statement start" is the whole rule, and it is narrow ON PURPOSE. Firing
    after a `then` closes the paren past the line's own `end` -- which is what
    it did, turning `if(x) ?"hi"` into `if x then print("hi" end)`. The one
    place a `?` legitimately follows something is a p8 one-line `if`, and
    `_expand_oneline_if` expands its inner statement through here itself, where
    that statement is a line of its own and the boundary is real.
    """
    i = _find_outside_strings(code, "?")
    if i < 0:
        return code
    before = code[:i].rstrip()
    if before and before[-1] != ";":
        return code                          # a `?` mid-expression is not ours
    rest = code[i + 1:].strip()
    if not rest:
        return code
    return code[:i] + "print(" + rest + ")"


def _lvalue_start(code, opi):
    """Walk back from the operator over an lvalue.

    An lvalue is a NAME plus any run of `.name` / `:name` / `[expr]` postfixes,
    and the walk has to respect that SHAPE rather than eating "identifier chars,
    dots and brackets" as one blob. p8 packs statements onto a line with no
    separator, so `local _ENV=n[o] phase+=speed` arrives as `n[o]phase+=speed`
    -- and the blob walk took `n[o]phase` as the lvalue, assigning to a name
    that does not exist and losing the real one. A bare name after `]` belongs
    to the previous statement; only `.` and `:` link segments together.
    """
    j = opi
    while j > 0 and code[j - 1] in " \t":
        j -= 1
    end = j
    while j > 0:
        ch = code[j - 1]
        if ch == "]":
            depth = 0
            k = j
            while k > 0:
                c = code[k - 1]
                if c == "]":
                    depth += 1
                elif c == "[":
                    depth -= 1
                    if depth == 0:
                        break
                k -= 1
            if depth != 0:
                return end               # unbalanced: not an lvalue
            j = k - 1
            continue
        if _ident_char(ch):
            k = j
            while k > 0 and _ident_char(code[k - 1]):
                k -= 1
            if k > 0 and code[k - 1] in ".:":
                j = k - 1
                continue
            return k
        break
    # A postfix with no name in front of it (`)[1] += x`) is not an lvalue.
    return end if j < end and code[j] == "[" else j


# The operator keywords -- the only words that can follow a complete term and
# CONTINUE the same expression. Everything else that starts a term after one has
# finished is the next statement.
_RHS_CONTINUES = ("and", "or", "not")
_RHS_STOPS = ("return", "end", "else", "elseif", "then", "do", "until")


def _rhs_end(code, start):
    """The statement boundary after a compound-assign RHS.

    Three ways an RHS ends: the code does, a keyword starts the next statement
    (`freeze-=1 return end`), or -- the one this missed -- A NEW TERM BEGINS
    where an expression cannot have one. PICO-8 lets statements share a line
    with no separator, so `dx/=l dy/=l` is two of them, and reading to the next
    KEYWORD swallowed the second into the first's right-hand side:

        dx/=l dy/=l        became   dx = dx / (l dy = dy / (l))
        x+=1 y+=2          became   x = x + (1 y = y + (2))

    which is a syntax error, from an idiom every other cart uses. Found by
    importing a real BBS cart once the pxa compression stopped refusing them.

    The rule that fixes it is a fact about Lua rather than a heuristic about
    carts: after a complete term (an identifier, a number, a string, a closing
    bracket) an expression can only go on via an OPERATOR -- symbolic, or one of
    `and`/`or`/`not`. An identifier, number, string or `{` there cannot be part
    of the same expression, so it is the next statement.
    """
    q = None
    depth = 0
    i = start
    n = len(code)
    term = False          # did we just finish a term?
    while i < n:
        ch = code[i]
        if q:
            if ch == "\\":
                i += 1
            elif ch == q:
                q = None
                term = True
        elif ch in "'\"":
            if depth == 0 and term:
                return i
            q = ch
        elif ch in "([{":
            if depth == 0 and term and ch == "{":
                return i          # a table constructor cannot follow a term
            depth += 1
        elif ch in ")]}":
            depth -= 1
            if depth == 0:
                term = True
        elif depth == 0 and (ch.isalpha() or ch == "_"):
            j = i
            while j < n and _ident_char(code[j]):
                j += 1
            word = code[i:j]
            prev = code[i - 1] if i > 0 else " "
            atomic = not (_isword(prev) or prev in "._:")
            if word in _RHS_STOPS and atomic:
                return i
            if term and atomic and word not in _RHS_CONTINUES:
                return i          # a new term where an expression cannot have one
            term = word not in _RHS_CONTINUES
            i = j
            continue
        elif depth == 0 and ch.isdigit():
            if term and not (i and code[i - 1] in "._" ) and not _isword(
                    code[i - 1] if i else " "):
                return i
            j = i
            while j < n and (_isword(code[j]) or code[j] in "._"):
                j += 1
            term = True
            i = j
            continue
        elif depth == 0 and not ch.isspace():
            term = False          # an operator: the expression goes on
        i += 1
    return n


def _expand_compound(code):
    """`X op= RHS` -> `X = X op (RHS)`, statement-bounded, outside strings."""
    # NOT a fixed budget. "A line holds few of these" was the old comment and
    # the old cap was 8; a minified cart puts its whole draw loop on one line,
    # and the ninth `-=` came out unexpanded -- a syntax error reported 700
    # columns away from the operator that caused it. The bound below is only a
    # runaway guard: each pass consumes one operator, so it cannot be reached.
    for _ in range(len(code) + 1):
        found = None
        for op in _ASSIGN_OPS:
            end = len(op) + 1                # past the trailing '='
            i = _find_outside_strings(code, op + "=")
            # skip ==, <=, >=, ~= lookalikes: the char after must not be '='
            while i >= 0 and code[i + end:i + end + 1] == "=":
                i = _find_outside_strings(code, op + "=", i + end)
            if i >= 0 and (found is None or i < found[0]):
                found = (i, op)
        if found is None:
            return code
        i, op = found
        end = len(op) + 1
        lo = _lvalue_start(code, i)
        lhs = code[lo:i].strip()
        if not lhs:                          # not actually an assignment
            return code
        re_ = _rhs_end(code, i + end)
        rhs = code[i + end:re_].strip()
        code = (code[:lo] + lhs + " = " + lhs + " " + op + " (" + rhs + ")"
                + (" " if re_ < len(code) else "") + code[re_:])
    return code


# Words that can only START a statement -- so an `if` still waiting for its
# `then` was a short-if and has already ended. Without this, `if(k==9)mset(x)
# for i=1,3 do` would hand the FOR's `do` to the if.
_IF_CLOSERS = ("for", "while", "function", "repeat", "end", "return", "local",
               "else", "elseif")


def _if_do_to_then(code):
    """p8 accepts `do` wherever Lua wants `then`.

    Not one cart's typo: `moss moss` writes `if cond do` twenty-two times and
    the word `then` zero times, so this is the dialect, not a slip. Lua 5.4
    stops at the first one with `'then' expected near 'do'`.
    """
    out = []
    i = 0
    n = len(code)
    q = None
    depth = 0
    pending = 0
    while i < n:
        ch = code[i]
        if q:
            out.append(ch)
            if ch == "\\" and i + 1 < n:
                i += 1
                out.append(code[i])
            elif ch == q:
                q = None
            i += 1
            continue
        if ch in "'\"":
            q = ch
            out.append(ch)
            i += 1
            continue
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
        elif ch.isalpha() or ch == "_":
            j = i
            while j < n and _ident_char(code[j]):
                j += 1
            word = code[i:j]
            prev = code[i - 1] if i else " "
            if depth == 0 and not (_ident_char(prev) or prev in "._:"):
                if word == "if" or word == "elseif":
                    pending = 1
                elif pending and word == "do":
                    out.append("then")
                    i = j
                    pending = 0
                    continue
                elif pending and (word == "then" or word in _IF_CLOSERS):
                    pending = 0
            out.append(word)
            i = j
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def _oneline_if_at(code, start=0):
    """Where a p8 short-`if` starts on this line at or after `start`, or -1.

    NOT just the start of the line. p8 lets statements share a line with no
    separator and carts lean on it hard -- `key=mget(e,n) if(key==9)mset(...)`
    is one line of a real cart, and anchoring on the first token left the `if(`
    in the middle of it as a syntax error (`'then' expected near 'mset'`).
    """
    i = _find_outside_strings(code, "if", start)
    while i >= 0:
        prev = code[i - 1] if i else " "
        after = code[i + 2:]
        if not _ident_char(prev) and prev != "." \
                and after.lstrip().startswith("("):
            return i
        i = _find_outside_strings(code, "if", i + 2)
    return -1


def _expand_oneline_if(code, start=0):
    """p8's `if (cond) stmt` one-liner -> `if cond then stmt end`.

    `start` is what makes this try EVERY candidate on the line instead of
    giving up at the first. `if(a or b)and c then` opens with an `if(` that is
    not a short-if at all -- the parens are a sub-expression and the condition
    goes on -- and bailing there left the real short-if further down the same
    line unexpanded, which is where the parser stopped.
    """
    at = _oneline_if_at(code, start)
    if at < 0:
        return code
    indent = code[:at]
    code = code[at:]
    p = code.index("(")
    depth = 0
    q = None
    i = p
    while i < len(code):
        ch = code[i]
        if q:
            if ch == "\\":
                i += 1
            elif ch == q:
                q = None
        elif ch in "'\"":
            q = ch
        elif ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                break
        i += 1
    if depth != 0:
        return _expand_oneline_if(indent + code, len(indent) + 2)
    cond = code[p + 1:i]
    rest = code[i + 1:].strip()
    if not rest or rest.split(None, 1)[0] in ("then", "do", "and", "or", "not") \
            or rest[0] in "+-*/%<>=~.([{":
        # a normal if, or a condition that goes on: try the next candidate
        return _expand_oneline_if(indent + code, len(indent) + 2)
    # `rest` may itself be another short-if (`if(a) if(b) x=1`), and it is a
    # statement of its own now, so both expansions run on it here.
    rest = _expand_oneline_if(_expand_print_shorthand(rest))
    return indent + "if " + cond + " then " + rest + " end"


def _rename_lifecycle(code):
    for name in _LIFECYCLE:
        code = code.replace("function " + name + "(", "function p8" + name + "(")
    return code


_EMPTY_MUSIC_STUB = re.compile(r"^\s*function\s+music\s*\([^)]*\)\s*end\s*$")


def p8_lua_to_lua54(lines):
    out = []
    state = None
    for line in lines:
        line = line.replace("\t", "  ").rstrip()
        spans = []
        code, comment, state = _scan_line(line, state, spans)
        if code is None:                     # inside a `--[[ ]]` block comment
            out.append(line)
            continue
        if _EMPTY_MUSIC_STUB.match(code):
            # The cart silenced ITSELF with an empty music() override (the
            # celeste-maker mirror ships one). The port imports __music__ as
            # real tracks, so drop the stub and let the shim's music() play
            # them. A NON-empty override is real cart behaviour and is kept.
            out.append("-- [port] dropped the cart's empty music() stub "
                       "(imported __music__ plays instead)")
            continue
        i = _find_outside_strings(code, "!=")
        while i >= 0:
            code = code[:i] + "~=" + code[i + 2:]
            i = _find_outside_strings(code, "!=")
        # The LEXER first: everything below reads the line as tokens, and
        # `0or`, `i\\w` and `0b1010` are not the tokens they look like.
        code = _fix_p8_tokens(code)
        code = _if_do_to_then(code)
        code = _expand_print_shorthand(code)
        code = _expand_oneline_if(code)
        code = _expand_compound(code)
        code = _rename_lifecycle(code)
        out.append(_unmask_long_strings(code, spans) + comment)
    return "\n".join(out) + "\n"


# --------------------------------------------------------------------------
# __map__ + the __gfx__-shared bottom half -> 64 rows of big-endian hex
# --------------------------------------------------------------------------

def full_map_rows(sections):
    """The full 128x64 cell map as 64 strings of 256 hex chars (big-endian
    cell bytes). Rows 0-31 come from __map__ verbatim; rows 32-63 live in the
    BOTTOM half of __gfx__ (PICO-8 shared RAM), where the byte order is
    low-nibble-first -- normalized here so the Lua reader has ONE format."""
    map_lines = [ln.strip().lower() for ln in sections.get("map", [])]
    gfx_lines = [ln.strip().lower() for ln in sections.get("gfx", [])]
    rows = []
    for y in range(32):
        row = map_lines[y] if y < len(map_lines) else ""
        row = "".join(ch if ch in "0123456789abcdef" else "0" for ch in row)
        rows.append((row + "0" * 256)[:256])
    for k in range(32):
        cells = []
        for half in range(2):                # two 128-nibble gfx lines per map row
            gi = 64 + 2 * k + half
            g = gfx_lines[gi] if gi < len(gfx_lines) else ""
            g = "".join(ch if ch in "0123456789abcdef" else "0" for ch in g)
            g = (g + "0" * 128)[:128]
            for i in range(64):
                lo, hi = g[2 * i], g[2 * i + 1]   # low nibble is the LEFT pixel
                cells.append(hi + lo)             # -> big-endian text byte
        rows.append("".join(cells))
    return rows


def gff_hex(sections):
    """The 256 per-sprite flag bytes as one 512-char hex string."""
    text = "".join(ln.strip().lower() for ln in sections.get("gff", []))
    text = "".join(ch if ch in "0123456789abcdef" else "0" for ch in text)
    return (text + "0" * 512)[:512]


# --------------------------------------------------------------------------
# the PICO-8 compat shim (pure Lua over the ordinary moy cart verbs)
# --------------------------------------------------------------------------

SHIM = r'''-- ============================================================
-- PICO-8 compatibility shim (generated by tools/p8_lua_port.py)
-- The p8 API written over the moy cart API; the ported cart below
-- calls PICO-8 verbs and never knows it moved.
-- ============================================================
-- NATIVE RES: the manifest declares "canvas": "128x128" (SPEC.md 1/3.1), so
-- this cart draws REAL p8 pixels 1:1 and the HOST owns all scaling. The old
-- shim drew 2x itself, because asking the host to composite made the zoom
-- appear only on the one tier that implemented `view` -- with the canvas in
-- the manifest every conforming host sizes the raster instead, and the cart
-- fills a QUARTER of the pixels it used to (the biggest speed lever a port
-- has on an interpreter-bound board).
-- ZOOM (--zoom): nothing is cropped from the raster any more -- all 128 rows
-- draw, and pset/pget/camera stay p8-true. The flag became the `view` hint
-- below: a host with room composites the CENTERED 128x120 at the biggest
-- integer scale that fits (2x fills a 4:3 screen's 240px height exactly; 5x
-- fills 600px), and one that presents pixel-for-pixel draws it unscaled. view
-- is core (SPEC.md 6) and cannot mislead a cart, so no guard -- lossy only at
-- PRESENTATION.
local P8_VH = __P8_VH__
local P8_DT = 1 / 30               -- PICO-8 _update runs at a fixed 30fps
if P8_VH < 128 then view(128, P8_VH) end
do
  local m_spr, m_btn, m_btnp = spr, btn, btnp
  local m_camera = camera
  local m_rect, m_rectb = rect, rectb
  local m_circ, m_circb = circ, circb
  local m_sfx = sfx
  local m_music, m_music_stop = music, music_stop
  -- The data tables (emitted ABOVE the shim) and the stdlib verbs, captured
  -- once as upvalues: fget hits __p8_gff on every collision probe and map()
  -- on every tile, where a global read is a hash lookup in _ENV per call --
  -- on a 240MHz interpreter those lookups were measurable frame time.
  local gff, mm = __p8_gff, __music_map
  local msin, mcos, matan, mrandom = math.sin, math.cos, math.atan, math.random
  local mrandomseed = math.randomseed
  local tremove = table.remove

  local BTN = {[0] = "left", [1] = "right", [2] = "up", [3] = "down",
               [4] = "a", [5] = "b"}
  -- btnp edges are latched across console frames (see _update below): a game
  -- tick runs only ~every other console frame and an engine press edge lasts
  -- ONE console frame, so an unlatched port would eat ~half of all presses.
  local pending = {}
  function btn(i) return m_btn(BTN[i] or "a") end
  function btnp(i) return pending[i] or m_btnp(BTN[i] or "a") end

  -- PICO-8 numbers are 16.16 fixed point and every API arg is implicitly
  -- FLOORED (p8 carts pass float colors/coords everywhere -- celeste's "1000"
  -- popup draws with color 7+flash%2). The moy engine takes integer indices,
  -- so this shim floors at the boundary.
  local mfloor = math.floor
  local function fl(v) return mfloor(v or 0) end

  function camera(cx, cy) m_camera(fl(cx), fl(cy)) end
  -- p8 math over the sandboxed Lua math lib (the moy api only registers
  -- rnd/flr; a python cart gets abs/min/max from python builtins, a lua cart
  -- gets them here). p8 angles are TURNS (0..1) and sin is flipped (+y down).
  function sin(t) return -msin((t or 0) * 6.283185307179586) end
  function cos(t) return mcos((t or 0) * 6.283185307179586) end
  flr = math.floor
  abs = math.abs
  min = math.min
  max = math.max
  sqrt = math.sqrt
  function atan2(dx, dy) return matan(-(dy or 0), dx or 0) / 6.283185307179586 % 1 end

  function spr(n, x, y, w, h, fx, fy)
    local flip = (fx and 1 or 0) + (fy and 2 or 0)
    n = fl(n)
    x = fl(x)
    y = fl(y)
    w = w or 1
    h = h or 1
    if w == 1 and h == 1 then
      m_spr(n, x, y, 0, 1, flip)               -- p8 color 0 is transparent
    else
      for ty = 0, h - 1 do
        for tx = 0, w - 1 do
          local cx = fx and (w - 1 - tx) or tx
          local cy = fy and (h - 1 - ty) or ty
          m_spr(n + cx + cy * 16, x + tx * 8, y + ty * 8, 0, 1, flip)
        end
      end
    end
  end

  -- p8 rect/circ are OUTLINES and rectangles take the far corner
  function rectfill(x0, y0, x1, y1, c)
    x0 = fl(x0) y0 = fl(y0) x1 = fl(x1) y1 = fl(y1)
    if x1 < x0 then x0, x1 = x1, x0 end
    if y1 < y0 then y0, y1 = y1, y0 end
    m_rect(x0, y0, x1 - x0 + 1, y1 - y0 + 1, fl(c))
  end
  function rect(x0, y0, x1, y1, c)
    x0 = fl(x0) y0 = fl(y0) x1 = fl(x1) y1 = fl(y1)
    if x1 < x0 then x0, x1 = x1, x0 end
    if y1 < y0 then y0, y1 = y1, y0 end
    m_rectb(x0, y0, x1 - x0 + 1, y1 - y0 + 1, fl(c))
  end
  function circfill(x, y, r, c) m_circ(fl(x), fl(y), fl(r), fl(c)) end
  function circ(x, y, r, c) m_circb(fl(x), fl(y), fl(r), fl(c)) end
  -- SPEC.md 6 gives `print` fixed 8px glyphs -- TWICE the size p8 meant on a
  -- native 128px raster, and celeste's memorial letters its text at the p8
  -- 4px advance (8px glyphs smear into each other). So the port carries the
  -- PICO-8 system font ITSELF (Lexaloffle, released CC0; bitmaps read from
  -- fake-08's 0x5600 memory dump): chars 32..127 as 15-bit 3x5 glyphs at the
  -- true 4px advance / 6px line height, drawn through pix() -- pure spec
  -- verbs, portable to any conforming host.
  local m_pix = pix
  local P8_GLYPHS = {}
  do
    local hex = "00002092002d5f7d2f9f52a57adb000a224a292255d505d0140001c020001494"
             .. "7b6f749373e779a749ed79cf7bc949277bef49ef0410141044540e38151121a7"
             .. "636a5f787ad872783b5872f812f87a785f6874b834b85ae872485bf85b583b70"
             .. "1f7867505778387024b86b682f687f685aa879e87338324b44916926002a7000"
             .. "00225bef7aef624e7b6b72cf12cf7a4e5bed749734975aed72495b7f5b6b3b6e"
             .. "13ef676a5aef39ce24976b6d2f6d7f6d5aad79ed72a764d62492359303e07b50"
    for i = 0, 95 do
      P8_GLYPHS[i + 32] = tonumber(string.sub(hex, i * 4 + 1, i * 4 + 4), 16)
    end
  end
  local sbyte = string.byte
  function print(s, x, y, c)
    s = tostring(s)
    c = c == nil and 7 or fl(c)
    local lx = fl(x)
    local cx, cy = lx, fl(y)
    for i = 1, #s do
      local b = sbyte(s, i)
      if b == 10 then
        cx, cy = lx, cy + 6
      else
        local g = P8_GLYPHS[b]
        if g and g ~= 0 then
          for p = 0, 14 do
            if (g >> p) & 1 == 1 then m_pix(cx + p % 3, cy + p // 3, c) end
          end
        end
        cx = cx + 4
      end
    end
  end

  local m_pal = pal
  function pal(a, b)
    if a == nil then m_pal() else m_pal(fl(a), fl(b)) end
  end
  function pset(x, y, c) m_pix(fl(x), fl(y), fl(c)) end
  function pget(x, y) return m_pix(fl(x), fl(y)) end
  local m_line = line
  function line(x0, y0, x1, y1, c)
    m_line(fl(x0), fl(y0), fl(x1), fl(y1), fl(c))
  end

  function sfx(n) if n and n >= 0 then m_sfx(fl(n)) end end
  function music(n)
    -- p8 music(n) takes a PATTERN index; __music_map (baked per cart) maps the
    -- song starts to Moybyte track ids, nearest-lower for a mid-song index.
    if n == -1 then m_music_stop()
    elseif n then
      local t = mm and mm[n]
      if t == nil and mm then
        for k = n, 0, -1 do
          if mm[k] ~= nil then t = mm[k] break end
        end
      end
      m_music(t or 0)
    end
  end
  function menuitem() end                      -- p8 pause menu: nothing to add to

  -- p8 table verbs. all() tolerates deleting the CURRENT item mid-loop
  -- (celeste's foreach(objects, ...) destroys objects while iterating).
  function add(t, v) t[#t + 1] = v return v end
  function del(t, v)
    for i = 1, #t do
      if t[i] == v then tremove(t, i) return end
    end
  end
  function all(t)
    local i = 0
    local v
    return function()
      if t[i] == v then i = i + 1 end
      v = t[i]
      return v
    end
  end
  function foreach(t, f) for v in all(t) do f(v) end end
  function count(t) return #t end
  sub = string.sub
  tostr = tostring
  function sgn(x) if (x or 0) < 0 then return -1 end return 1 end
  function mid(a, b, c) return max(min(a, b), min(max(a, b), c)) end
  function rnd(n) return mrandom() * (n or 1) end

  -- THE CLOCK. PICO-8 counts SECONDS since the cart started; the console's
  -- time() counts MILLISECONDS. Both p8 names, because carts use either, and
  -- `time` shadows the console's deliberately -- inside a ported cart, p8
  -- semantics are the correct ones. Getting this wrong does not crash, it runs
  -- everything time-based a thousand times too fast, which is why it was
  -- reported as "differs" rather than "missing".
  local m_time = time
  function t() return m_time() / 1000 end
  time = t

  -- STRING HELPERS. Real Lua 5.4 has every one of these under another name, so
  -- these are renames rather than implementations -- except split(), which is
  -- PICO-8's own and has no stdlib twin.
  chr = string.char
  function ord(s, i, n)
    if s == nil or s == "" then return nil end
    if n then return string.byte(s, i or 1, (i or 1) + n - 1) end
    return string.byte(s, i or 1)
  end
  function tonum(v)
    if type(v) == "number" then return v end
    return tonumber(v)
  end
  -- split(s, [sep], [convert]) -- sep defaults to ",", a NUMBER sep cuts fixed
  -- width chunks, and numeric-looking parts become numbers unless told not to.
  function split(s, sep, num)
    local out = {}
    if s == nil then return out end
    s = tostring(s)
    if num == nil then num = true end
    local function keep(part)
      out[#out + 1] = num and (tonumber(part) or part) or part
    end
    if type(sep) == "number" then
      local step = sep < 1 and 1 or sep
      for i = 1, #s, step do keep(string.sub(s, i, i + step - 1)) end
      return out
    end
    sep = sep or ","
    if sep == "" then sep = "," end
    local i = 1
    while true do
      local j = string.find(s, sep, i, true)
      if j then keep(string.sub(s, i, j - 1)) else keep(string.sub(s, i)) break end
      i = j + #sep
    end
    return out
  end

  -- OVAL / OVALFILL. PICO-8 draws an ellipse in a BOUNDING BOX (x0,y0 to
  -- x1,y1); the console has circles and no ellipse. Midpoint ellipse, four-way
  -- symmetric, so it is the same pixels PICO-8 draws rather than a scaled
  -- circle. Found by a cart dying on it -- and it was not even in the gap
  -- table, so the import said nothing and the crash was the first news.
  local function _oval(x0, y0, x1, y1, col, fill)
    x0, y0, x1, y1 = flr(x0), flr(y0), flr(x1), flr(y1)
    if x1 < x0 then x0, x1 = x1, x0 end
    if y1 < y0 then y0, y1 = y1, y0 end
    local a, b = (x1 - x0) / 2, (y1 - y0) / 2
    local cx, cy = x0 + a, y0 + b
    local prev
    for dy = 0, flr(b) do
      local dx = a
      if b > 0 then dx = a * sqrt(1 - (dy * dy) / (b * b)) end
      dx = flr(dx + 0.5)
      if fill then
        -- ONE SPAN PER ROW. The console's rect is (x, y, W, H) and FILLED --
        -- not PICO-8's two corners, which is what the shim's own rect() above
        -- converts for. Passing corners here drew a rectangle with a rounded
        -- top instead of an ellipse, on a cart, visibly.
        local w = dx * 2 + 1
        m_rect(flr(cx - dx), flr(cy - dy), w, 1, col)
        if dy > 0 then m_rect(flr(cx - dx), flr(cy + dy), w, 1, col) end
      else
        pset(cx - dx, cy - dy, col) pset(cx + dx, cy - dy, col)
        pset(cx - dx, cy + dy, col) pset(cx + dx, cy + dy, col)
        -- join the runs so a wide ellipse's flanks have no gaps
        if prev and prev - dx > 1 then
          for d = dx, prev do
            pset(cx - d, cy - dy, col) pset(cx + d, cy - dy, col)
            pset(cx - d, cy + dy, col) pset(cx + d, cy + dy, col)
          end
        end
      end
      prev = dx
    end
  end
  function oval(x0, y0, x1, y1, col) _oval(x0, y0, x1, y1, col, false) end
  function ovalfill(x0, y0, x1, y1, col) _oval(x0, y0, x1, y1, col, true) end

  -- The rest of PICO-8's surface that is plain Lua or plain arithmetic. None
  -- of these needed a console verb; they were simply never written down, so a
  -- cart calling one crashed with nothing said at import time.
  ceil = math.ceil
  function srand(x) return mrandomseed(flr(x or 0)) end
  function deli(t, i)
    if t == nil then return nil end
    if i == nil then i = #t end
    local v = t[i]
    table.remove(t, i)
    return v
  end
  unpack = table.unpack
  function pack(...) return {n = select("#", ...), ...} end
  -- p8's run() RESTARTS the cart. There is no reload verb here, so the
  -- closest honest thing is the cart's own _init again -- which is what a
  -- game-over `run()` is reaching for. It does NOT reset globals the way a
  -- real restart does, and unlike p8's it RETURNS, so the rest of the caller
  -- still runs. Reported as approximated, not silently substituted.
  function run()
    if p8_init then p8_init() end
  end
  function band(a, b) return flr(a) & flr(b) end
  function bor(a, b) return flr(a) | flr(b) end
  function bxor(a, b) return flr(a) ~ flr(b) end
  function bnot(a) return ~flr(a) end
  function shl(a, n) return flr(a) << flr(n) end
  function shr(a, n) return flr(a) >> flr(n) end
  function rotl(a, n) n = flr(n) % 32 return ((flr(a) << n) | (flr(a) >> (32 - n))) & 0xffffffff end
  function rotr(a, n) n = flr(n) % 32 return ((flr(a) >> n) | (flr(a) << (32 - n))) & 0xffffffff end

  -- NO COROUTINES, and the reason is worth stating where somebody will next
  -- reach for them: this IS real Lua 5.4, but the console opens only base,
  -- math, string and table (libmoy/moy_lua.c), so `coroutine` is not a global
  -- here. A shim of `cocreate = coroutine.create` therefore does not fall back
  -- to anything -- it fails while the shim itself is loading, taking the whole
  -- cart with it, which is how this was found. Opening the library is a
  -- one-line spec decision, not a porter one.

  -- sspr: the first eight arguments agree, and then they do not. PICO-8 takes
  -- two flip BOOLEANS where the console takes a colorkey and a flip BITMASK
  -- (1=h, 2=v), so a cart asking for a mirrored blit was passing `true` as a
  -- colour. dw/dh default to the source size, as PICO-8 lets them; colour 0 is
  -- transparent, the same convention spr() uses above.
  local m_sspr = sspr
  function sspr(sx, sy, sw, sh, dx, dy, dw, dh, fx, fy)
    local f = 0
    if fx then f = f + 1 end
    if fy then f = f + 2 end
    m_sspr(sx, sy, sw, sh, dx, dy, dw or sw, dh or sh, 0, f)
  end

  -- map + flags: the map DATA now ships as map.moymap (the console's own
  -- format -- editable, native-map()-able); build the fast Lua-side lookup
  -- from it ONCE at start via the console mget (captured before the p8 mget
  -- shadows it). __gff__ stays baked below the shim (flags have no moy home).
  local m_mget, m_mset = mget, mset
  local p8map = {}
  __p8_map = p8map                     -- the global name stays for tooling
  for y = 0, 63 do
    local base = y * 128
    for x = 0, 127 do
      local v = m_mget(x, y)
      p8map[base + x + 1] = (v and v >= 0) and v or 0
    end
  end

  -- mset writes BOTH stores. The console has mset, but map() above draws from
  -- the p8map copy built just now -- so a cart that wrote a cell and expected
  -- to see it drew the old one. Silent and wrong, which is the worse kind.
  function mset(x, y, v)
    x, y, v = flr(x or 0), flr(y or 0), v or 0
    if x >= 0 and x < 128 and y >= 0 and y < 64 then
      p8map[y * 128 + x + 1] = v
    end
    m_mset(x, y, v)
  end
  function mget(x, y)
    x = mfloor(x or 0)
    y = mfloor(y or 0)
    if x < 0 or x > 127 or y < 0 or y > 63 then return 0 end
    return p8map[y * 128 + x + 1]
  end
  function fget(n, f)
    local v = gff[mfloor(n or 0)] or 0
    if f == nil then return v end
    return (v >> f) & 1 == 1
  end
  -- Flag-masked map: ONE native call when the host offers the C walk
  -- (moybyte's __moy_map_masked, #66 M0 -- the flags crossed once in the
  -- __gff__ block above; the quads ride the same batch the spr fast path
  -- stamps), else the Lua cell loop. The loop was 4.5ms of celeste's render
  -- on the interpreter-bound boards, measured by difference. Args are
  -- floored up front (p8 floors every API arg), so both lanes agree.
  local native_map = __moy_map_masked
  function map(celx, cely, sx, sy, cw, ch, mask)
    celx = mfloor(celx or 0)
    cely = mfloor(cely or 0)
    sx = mfloor(sx or 0)
    sy = mfloor(sy or 0)
    cw = mfloor(cw or 16)
    ch = mfloor(ch or 16)
    mask = mask or 0
    if native_map ~= nil
        and native_map(celx, cely, sx, sy, cw, ch, mask) then
      return
    end
    for cy = 0, ch - 1 do
      local rowb = (cely + cy) * 128
      for cx = 0, cw - 1 do
        local tile = p8map[rowb + celx + cx + 1] or 0
        if tile > 0 and (mask == 0
                         or ((gff[tile] or 0) & mask) ~= 0) then
          m_spr(tile, sx + cx * 8, sy + cy * 8, 0, 1, 0)
        end
      end
    end
  end

  -- moybyte lifecycle -> the p8 one, paced at PICO-8's fixed 30fps
  local ticked = true
  function _init()
    if p8_init then p8_init() end
  end
  -- WALL-CLOCK cadence: p8_update runs 30x per real second whatever rate the
  -- host calls _update at, and a host too slow to draw that often loses DRAWS,
  -- not game speed. That is SPEC.md 5's one sanctioned degradation ("skip
  -- _draw while continuing to call _update at the full rate"), applied from
  -- inside the cart because the shim cannot verify the host is doing it.
  --
  -- On a host that does pace to 30 (both reference players do), dt is 1/30 and
  -- this ticks exactly once per call -- the same frame-for-frame behaviour a
  -- quantized cadence gave, reached without assuming the pacing.
  --
  -- The cost, and it is real: where the host rate is not a multiple of 30, the
  -- ticks land on ITS frame grid, so their spacing alternates (at 45fps, gaps
  -- of 22 and 44ms). That is arithmetic, not a scheme to tune away -- a 45fps
  -- host cannot place 30 evenly spaced ticks per second. Even spacing at the
  -- wrong rate is what this replaced: it ran a cart at 75% speed there, and
  -- 53% at 32fps.
  local EPS = P8_DT * 0.02      -- absorbs an integer-ms host period (33 vs 33.33)
  local MAX_CATCHUP = 4         -- past this the board genuinely cannot keep up
  local acc = 0
  function _update(dt)
    for i = 0, 5 do                              -- latch edges EVERY frame
      if m_btnp(BTN[i]) then pending[i] = true end
    end
    dt = dt or P8_DT
    if dt > 0.25 then dt = 0.25 end              -- a stall is a pause, not debt
    acc = acc + dt
    local n = 0
    while acc >= P8_DT - EPS and n < MAX_CATCHUP do
      acc = acc - P8_DT
      n = n + 1
      if p8_update then p8_update() end
      for i = 0, 5 do pending[i] = false end     -- the tick consumed the edges
      ticked = true
    end
    if n >= MAX_CATCHUP then acc = 0 end         -- write off what cannot be paid
  end
  function _draw()
    if ticked and p8_draw then
      -- the console resets camera/clip/pal after every cart frame; re-park the
      -- p8 camera so a cart that trusts persistent camera state draws at origin
      camera()
      p8_draw()
      ticked = false
    end
  end
end
-- ============================== end shim =============================
'''


def music_map_lua(sections):
    """The baked {p8_pattern_start: moy_track} table the shim's music() reads."""
    starts = music_start_map(sections.get("music", []))
    if not starts:
        return "__music_map = nil"
    pairs = ", ".join("[%d]=%d" % (k, v) for k, v in sorted(starts.items()))
    return "__music_map = {%s}" % pairs


def data_tables_lua(sections):
    lines = [music_map_lua(sections),
             "-- __gff__ (the map itself ships as map.moymap)",
             "__p8_gff = {}",
             "do",
             '  local gff = "' + gff_hex(sections) + '"',
             "  for i = 0, 255 do",
             "    __p8_gff[i] = tonumber(string.sub(gff, i * 2 + 1, i * 2 + 2), 16)",
             "  end",
             "  -- hosts with the native masked-map walk take the flags ONCE",
             "  -- (moybyte's __moy_map_flags, #66 M0); the Lua table above",
             "  -- stays -- fget reads it either way.",
             "  if __moy_map_flags ~= nil then __moy_map_flags(gff) end",
             "end",
              ""]
    return "\n".join(lines)


# Every global the shim defines for the game code. Order is cosmetic; the
# emitted block groups them a line at a time.
P8_API = ("btn btnp camera sin cos flr abs min max sqrt atan2 spr rectfill "
          "rect circfill circ print pal pset pget line sfx music menuitem "
          "add del all foreach count sub tostr sgn mid rnd mget fget map "
          # 2026-08-30: the gaps that were only ever a naming difference.
          "t time chr ord tonum split mset sspr "
          "oval ovalfill ceil srand deli unpack pack run "
          "band bor bxor bnot shl shr rotl rotr").split()


def _defines_function(body, name):
    """`function NAME(` anywhere in `body`, at a keyword/identifier boundary."""
    i = 0
    n = len(body)
    while True:
        j = body.find("function", i)
        if j < 0:
            return False
        i = j + 8
        if j > 0 and (_ident_char(body[j - 1]) or body[j - 1] in ".:"):
            continue                      # the tail of a longer word
        k = j + 8
        while k < n and body[k] in " \t\r\n":
            k += 1
        if k == j + 8:                    # `function` and the name must part
            continue
        if not body.startswith(name, k):
            continue
        k += len(name)
        if k < n and _ident_char(body[k]):
            continue                      # a LONGER name that starts with ours
        while k < n and body[k] in " \t\r\n":
            k += 1
        if body[k:k + 1] == "(":
            return True


def _assigns_global(body, name):
    """`NAME =` (not `==`) or `NAME,` where NAME opens a line."""
    n = len(body)
    ln = len(name)
    i = 0
    while True:
        j = body.find(name, i)
        if j < 0:
            return False
        i = j + ln
        k = j
        while k > 0 and body[k - 1] in " \t":
            k -= 1
        if k != 0 and body[k - 1] != "\n":
            continue                      # something else opened this line
        e = j + ln
        if e < n and _ident_char(body[e]):
            continue                      # a LONGER name that starts with ours
        while e < n and body[e] in " \t\r\n":
            e += 1
        if body[e:e + 1] == ",":
            return True
        if body[e:e + 1] == "=" and body[e + 1:e + 2] != "=":
            return True


def localization_lua(body):
    """`local NAME = NAME` aliases at file scope, between shim and game code.

    Game functions compiled after this block bind the p8 API as UPVALUES (a
    slot read) instead of _ENV lookups (a table hash on EVERY call) -- on the
    interpreter-bound boards the hashes were measurable frame time (#67).
    A name the cart itself reassigns at global scope stays global: an alias
    would freeze the pre-assignment value for every later caller. The scan is
    conservative -- a false positive only loses that one name's speedup.

    The two predicates are hand-walked rather than regexes because both of the
    regexes they replaced needed constructs MicroPython's `re` refuses to
    COMPILE -- a lookbehind, a non-capturing group, an inline `(?m)` and a
    negative lookahead -- and this file has to run there (module header)."""
    keep = []
    for name in P8_API:
        if _defines_function(body, name):
            continue                      # cart defines its own
        if _assigns_global(body, name):
            continue                      # cart assigns the global
        keep.append(name)
    if not keep:
        return ""
    lines = ["-- Localized p8 API (generated -- see localization_lua): the game",
             "-- code below binds these as upvalues, not per-call _ENV lookups."]
    for i in range(0, len(keep), 8):
        chunk = ", ".join(keep[i:i + 8])
        lines.append("local %s = %s" % (chunk, chunk))
    return "\n".join(lines) + "\n"


def build_manifest(title, icon=None):
    # The spec manifest (SPEC.md 3.1). fps 30 because that IS PICO-8's rate --
    # the shim paces the p8 lifecycle at a fixed 1/30 dt. "ported_from" is an
    # unrecognised field; the spec requires hosts to ignore it (3.1).
    man = {
        "format": "moy-1",
        "title": title,
        "version": 1,
        "main": "main.lua",
        "fps": 30,
        # SPEC.md 1/3.1: the p8 screen IS the raster. The cart draws native
        # 128x128 pixels and the host scales/letterboxes -- a quarter of the
        # fill the old draw-2x-yourself shim paid.
        "canvas": "128x128",
        "input": ["buttons"],
        # A ported cart is SOMEBODY ELSE'S cart. PICO-8 BBS carts default to
        # CC BY-NC-SA 4.0 (module header), so playing and studying one is fine
        # and republishing it is not -- stated in the manifest so a host's share
        # paths read the answer instead of inferring it from `ported_from`.
        "safe_to_share": False,
        "ported_from": "pico-8",
    }
    if icon is not None:
        # SPEC.md 3.4: the tiles a launcher shows the cart by. p8 has no icon
        # field, so this is the sheet's first non-blank tile (see icon_tile) --
        # without it a ported cart falls back to tile 0, which the p8 convention
        # leaves EMPTY, i.e. every ported cart would show a blank square.
        man["icon"] = [icon, 1, 1]
    return man


# The order a manifest's fields are WRITTEN in. Declared rather than taken from
# the dict because MicroPython's dicts are not insertion-ordered: without this
# the same cart ported on two tiers differs by field order alone, which is both
# an unreadable diff and the end of any byte-for-byte check between them.
MANIFEST_KEYS = ("format", "title", "version", "main", "fps", "canvas",
                 "input", "safe_to_share", "ported_from", "icon")


def manifest_text(man):
    """`json.dump(man, indent=2)` for a `json` with no `indent=` (the header).

    A LAYOUT, not an encoder: every value still goes through `json.dumps`. The
    manifest is one flat object of scalars and scalar lists, which is the only
    shape this lays out -- anything nested would need a real pretty-printer, and
    the answer to that is to not put it in a manifest."""
    out = []
    for key in MANIFEST_KEYS:
        if key not in man:
            continue
        v = man[key]
        if isinstance(v, list):
            body = ("[\n" + ",\n".join("    " + json.dumps(x) for x in v)
                    + "\n  ]")
        else:
            body = json.dumps(v)
        out.append("  " + json.dumps(key) + ": " + body)
    return "{\n" + ",\n".join(out) + "\n}\n"


def _mkdirs(path):
    """`os.makedirs(path, exist_ok=True)` for an `os` that stops at mkdir."""
    at = "/" if path.startswith("/") else ""
    for seg in path.replace("\\", "/").split("/"):
        if not seg:
            continue
        at = at + seg if at in ("", "/") else at + "/" + seg
        try:
            os.mkdir(at)
        except OSError:
            pass                 # exists, or a parent we cannot make -- the
                                 # write below is what actually reports


def _write(out_dir, name, text):
    # `encoding=` is load-bearing on CPython (a non-UTF-8 locale would mangle an
    # accented title) and simply ignored by MicroPython's `open`.
    #
    # `text` may be a LIST of pieces, and on the small tiers that is the point:
    # the map is 16KB, and joining it into one string before writing needs that
    # 16KB twice at once. MicroPython ran out of heap on exactly that join.
    f = open(out_dir + "/" + name, "w", encoding="utf-8")
    try:
        if isinstance(text, list):
            for piece in text:
                f.write(piece)
        else:
            f.write(text)
    finally:
        f.close()


def port_sections(sections, out_dir, title, crop=(0, 0)):
    """Already-parsed `sections` -> a `.moy` folder at `out_dir`.

    Returns the facts only the writer knows: `{"files": [...], "sfx": n,
    "music": m}`. Takes SECTIONS rather than a path because a console was handed
    the dropped bytes and has already had to parse them to decide the file was a
    cart at all -- reading it a second time here is 40ms of a `.p8.png` inflate
    spent to learn nothing."""
    _mkdirs(out_dir)
    written = []

    body = p8_lua_to_lua54(sections.get("lua", []))
    header = ("-- %s -- ported from PICO-8 by tools/p8_lua_port.py (#11/#67).\n"
              "-- The data tables + shim are generated; the game code below them\n"
              "-- is the original cart's Lua, mechanically converted to Lua 5.4.\n"
              % title)
    vh = 128 - int(crop[0]) - int(crop[1])
    if vh not in (120, 128):
        # The host's view crop shows the CENTERED 128x120 (SPEC.md 6) -- the
        # only crop a native-res port can ask for is 8 rows or none.
        raise SystemExit("--zoom: the view crop is 8 rows (128x120) or nothing"
                         " -- T+B must be 8 or 0, got %d,%d" % tuple(crop))
    shim = SHIM.replace("__P8_VH__", str(vh))
    # Data tables BEFORE the shim, so the shim captures them as upvalues.
    main_lua = (header + data_tables_lua(sections) + "\n" + shim + "\n"
                + localization_lua(body) + body)
    _write(out_dir, "main.lua", main_lua)
    written.append("main.lua")

    # map.moymap -- the console's own tilemap format (cells store tile+1,
    # 0 = empty), so the map is REAL data other tools/editors/native map()
    # consume; the shim rebuilds its fast Lua table from it at start. The p8
    # convention maps exactly: cell 0 ("sprite 0", empty by convention) -> 0,
    # id N -> N+1. Id 255 can't be stored (+1 overflows the byte) -> empty.
    rows = full_map_rows(sections)
    if any(c != "0" for r in rows for c in r):
        out_rows = []
        for r in rows:
            cells = []
            for i in range(0, len(r), 2):
                v = int(r[i:i + 2], 16)
                cells.append("%02x" % (0 if v == 255 else (v + 1) & 0xFF if v else 0))
            out_rows.append("".join(cells))
        out_rows.append("")
        _write(out_dir, "map.moymap", ["128 64\n", "\n".join(out_rows)])
        written.append("map.moymap")

    kgfx = gfx_to_kgfx(sections.get("gfx", []))
    if kgfx:
        _write(out_dir, "sprites.moygfx", kgfx)
        written.append("sprites.moygfx")

    sounds, n_sfx, n_music = sfx_music_to_sounds(
        sections.get("sfx", []), sections.get("music", []))
    if sounds:
        _write(out_dir, "sounds.json", json.dumps(sounds))
        written.append("sounds.json")
    else:
        n_sfx = n_music = 0          # nothing was written, so nothing counted

    _write(out_dir, "manifest.json",
           manifest_text(build_manifest(title, icon_tile(kgfx))))
    written.append("manifest.json")
    return {"files": sorted(written), "sfx": n_sfx, "music": n_music}


def port(p8_path, out_dir, title=None, crop=(0, 0)):
    sections = read_p8(p8_path)      # text .p8 OR the BBS .p8.png
    title = title or _title_from(sections, p8_path)
    port_sections(sections, out_dir, title, crop)
    return out_dir


def parse_zoom(argv):
    """--zoom [T,B] -> (top, bottom) p8 rows the port can spare, or (0, 0).

    Bare --zoom means 4,4: the 8-row concession that lets a 4:3 host fill its
    height (view(128, 120) composites at 2x = 256x240 there). Nothing is
    cropped from the RASTER any more -- the rows only leave the picture on
    hosts that exploit the SPEC.md 6 view hint, and that crop is CENTERED,
    so T,B survives as CLI shape only; 8,0-style edge protection no longer
    maps and port() refuses any split that isn't 8 rows total (or none)."""
    if "--zoom" not in argv:
        return (0, 0)
    i = argv.index("--zoom")
    nxt = argv[i + 1] if i + 1 < len(argv) else ""
    # Only a literal T,B counts as the spec. Anything else after --zoom is
    # somebody else's argument -- `port --zoom cart.p8` puts the cart there --
    # and consuming it because it happened to follow the flag is how a stray
    # token ends up interpreted as something it is not.
    if not re.match(r"^\d+,\d+$", nxt):
        return (4, 4)
    t, b = nxt.split(",", 1)
    return (max(0, int(t)), max(0, int(b)))


def main(argv):
    args = [a for a in argv if not a.startswith("--")]
    title = None
    if "--title" in argv:
        title = argv[argv.index("--title") + 1]
        args = [a for a in args if a != title]
    crop = parse_zoom(argv)
    if crop != (0, 0):
        args = [a for a in args if "," not in a or not a.replace(",", "").isdigit()]
    if len(args) != 2:
        print("usage: p8_lua_port.py cart.p8 out_dir [--title NAME] [--zoom [T,B]]")
        return 2
    out = port(args[0], args[1], title, crop)
    vh = 128 - crop[0] - crop[1]
    print("ported ->", out)
    print("  canvas: 128x128 (native p8 pixels -- the host scales)")
    if crop != (0, 0):
        print("  zoom: view(128, %d) -- a host with room shows the centered"
              " rows at its best integer scale (2x fills 240px, 5x fills"
              " 600px); one that presents pixel-for-pixel draws them"
              " unscaled" % vh)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
