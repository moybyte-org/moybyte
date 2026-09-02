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
# p8-Lua -> Lua 5.4: ONE LEXER, then transforms over its tokens
# --------------------------------------------------------------------------
# Every bug this porter has had was a boundary question -- is this character
# inside a string, a comment, a long string; where does this number end; where
# does this statement start. Five separate hand-written scanners each answered
# those independently, and they drifted: `+=` was expanded inside a cart's
# binary data blob, a `--[[ ]]` comment body was parsed as code, `0or` was read
# as one number, `n[o]phase` as one lvalue. Each fix taught one scanner
# something the others still did not know.
#
# So the line is LEXED once, and the transforms below work on tokens. They
# cannot re-ask the question differently, because they cannot see characters.
#
# MicroPython runs this file (the browser imports carts): no f-strings, no
# regex on the hot path, no str.isalnum, and one line's tokens at a time.

_LIFECYCLE = ("_init", "_update", "_update60", "_draw")
# Words that CLOSE a block, so a `?`'s arguments stop before them.
_BLOCK_ENDS = ("end", "else", "elseif", "until")

_GLYPH_BTN = {
    "\x8b": "0", "\x91": "1", "\x94": "2",
    "\x83": "3", "\x8e": "4", "\x97": "5",
    "\u2b05": "0", "\u27a1": "1", "\u2b06": "2",
    "\u2b07": "3", "\U0001f17e": "4", "\u274e": "5",
}
_VARIATION = "\ufe0f"

# The P8SCII code each button spelling stands for, so a `.p8.png` byte and a
# text `.p8`'s emoji land on the same generated name.
_GLYPH_CODE = {
    "\x8b": 0x8b, "\x91": 0x91, "\x94": 0x94,
    "\x83": 0x83, "\x8e": 0x8e, "\x97": 0x97,
    "\u2b05": 0x8b, "\u27a1": 0x91, "\u2b06": 0x94,
    "\u2b07": 0x83, "\U0001f17e": 0x8e, "\u274e": 0x97,
}

# The same glyphs INSIDE a string, where a cart is not asking for a button
# number but printing an icon: `"press <x> to start"`, `"<left><up><right>
# <down>"`. The console's font is petme128, ASCII 0x20..0x7f and nothing else,
# so every one of these drew as blank -- the cart's own control legend, gone.
#
# The two BUTTON glyphs deliberately become "A" and "B" rather than any
# attempt at PICO-8's own badges: this console's buttons ARE named A and B
# (the shim maps p8 button 4 to `a` and 5 to `b`), so an O/X icon here would
# be a faithful copy of the wrong instruction. The arrows are the d-pad and
# read as one on any font.
_GLYPH_TEXT = {
    "\x8b": "<", "\x91": ">", "\x94": "^", "\x83": "v",
    "\x8e": "A", "\x97": "B",
    "\u2b05": "<", "\u27a1": ">", "\u2b06": "^", "\u2b07": "v",
    "\U0001f17e": "A", "\u274e": "B",
}

# The PICTURE glyphs, 128..153, drawn rather than transliterated -- P8SCII's
# double-wide set, 7x5 on an 8px advance, beside the shim's 3x5 ASCII on 4px.
# These are OUR bitmaps, not PICO-8's font: the shapes are generic (a heart, a
# star, a block) and the font itself is Lexaloffle's, so it is not ours to
# ship. The six BUTTONS are deliberately absent -- they become "A"/"B" and the
# arrows above, because this console's buttons ARE named A and B and a
# pixel-perfect (x) badge would be a faithful copy of the wrong instruction.
_P8_WIDE_ART = {
    0x80: ("#######", "#######", "#######", "#######", "#######"),  # block
    0x81: ("#.#.#.#", ".#.#.#.", "#.#.#.#", ".#.#.#.", "#.#.#.#"),  # checker
    0x82: ("#.....#", "###.###", "#######", "#.#.#.#", ".#####."),  # cat
    0x84: ("#...#..", "..#...#", "#...#..", "..#...#", "#...#.."),  # shade
    0x85: ("...#...", "#..#..#", ".#####.", "#..#..#", "...#..."),  # burst
    0x86: ("..###..", ".#####.", "#######", ".#####.", "..###.."),  # dot
    0x87: (".##.##.", "#######", "#######", ".#####.", "...#..."),  # heart
    0x88: ("..###..", ".#...#.", "#..#..#", ".#...#.", "..###.."),  # sun
    0x89: ("..###..", "..###..", "#######", "...#...", "..#.#.."),  # person
    0x8A: ("...#...", ".#####.", "#######", "#.....#", "#.###.#"),  # house
    0x8C: (".#####.", "#.....#", "#.#.#.#", "#.###.#", ".#####."),  # face
    0x8D: ("...####", "...#..#", "...#...", ".###...", ".###..."),  # note
    0x8F: ("...#...", "..###..", ".#####.", "..###..", "...#..."),  # diamond
    0x90: (".......", ".......", ".......", ".......", "#..#..#"),  # ellipsis
    0x92: ("...#...", "..###..", "#######", ".#####.", ".##.##."),  # star
    0x93: ("#######", ".#####.", "...#...", ".#####.", "#######"),  # hourglass
    0x95: (".......", ".#####.", "#.....#", ".......", "......."),  # arc
    0x96: ("...#...", "..#.#..", ".#...#.", "#.....#", "......."),  # chevron
    0x98: ("#######", ".......", "#######", ".......", "#######"),  # h-lines
    0x99: ("#.#.#.#", "#.#.#.#", "#.#.#.#", "#.#.#.#", "#.#.#.#"),  # v-lines
}


def _wide_glyph_hex():
    """The art above as 5 bytes per code, bit c = column c (bit 0 leftmost) --
    the same bit order the 3x5 set uses. Codes with no art pack as zero so the
    table indexes straight off the byte."""
    out = []
    for code in range(0x80, 0x9A):
        rows = _P8_WIDE_ART.get(code)
        for r in range(5):
            v = 0
            if rows:
                for c in range(7):
                    if rows[r][c] == "#":
                        v |= 1 << c
            out.append("%02x" % v)
    return "".join(out)

T_WS = 0
T_NAME = 1
T_NUM = 2
T_STR = 3
T_LONG = 4
T_COMMENT = 5
T_OP = 6

_KIND_NAME = {0: "ws", 1: "name", 2: "num", 3: "str", 4: "long",
              5: "comment", 6: "op"}

_LUA_ESCAPES = "abfnrtvxzu\\'\"\n"



def _isword(ch):
    return ch.isalpha() or ch.isdigit()


def _ident(ch):
    return ch == "_" or _isword(ch)


# The name the rest of this file has always used for it.
_ident_char = _ident


def _long_open(s, i):
    """Level of a long bracket opening at `i` (`[[` is 0, `[=[` is 1), else -1."""
    if i >= len(s) or s[i] != "[":
        return -1
    j = i + 1
    while j < len(s) and s[j] == "=":
        j += 1
    if j < len(s) and s[j] == "[":
        return j - i - 1
    return -1


def _read_number(s, i):
    """One p8 numeric literal at `i` -> (end, Lua spelling).

    p8 ends a number at the first char that cannot continue one, so `0or 1` is
    three tokens; Lua reads on and calls `0o` malformed. And `0b1010.1` is a
    binary literal with a binary FRACTION, which Lua cannot spell at all.
    """
    n = len(s)
    j = i
    if s[j] == "0" and j + 1 < n and s[j + 1] in "bB":
        j += 2
        st = j
        while j < n and s[j] in "01":
            j += 1
        whole = s[st:j]
        frac = ""
        if j < n and s[j] == "." and j + 1 < n and s[j + 1] in "01":
            j += 1
            st = j
            while j < n and s[j] in "01":
                j += 1
            frac = s[st:j]
        if not whole and not frac:
            return i + 1, s[i]
        val = int(whole or "0", 2)
        if frac:
            return j, str(val + int(frac, 2) / float(1 << len(frac)))
        return j, str(val)
    if s[j] == "0" and j + 1 < n and s[j + 1] in "xX":
        j += 2
        while j < n and s[j] in "0123456789abcdefABCDEF.":
            j += 1
        if j < n and s[j] in "pP":
            k = j + 1
            if k < n and s[k] in "+-":
                k += 1
            if k < n and s[k].isdigit():
                while k < n and s[k].isdigit():
                    k += 1
                j = k
        return j, s[i:j]
    while j < n and s[j].isdigit():
        j += 1
    if j < n and s[j] == ".":
        j += 1
        while j < n and s[j].isdigit():
            j += 1
    # NO EXPONENT FORM. Lua reads `12e4` as a number and p8 does not -- its
    # numbers are 16.16 fixed point, where an exponent has nowhere to go. A
    # minified cart leans on that: `... or 12e4({n,e,o,t},d)` is the number 12
    # and then a CALL to a function named e4, and reading it as 1.2e5 swallowed
    # the call and left the line unterminated.
    return j, s[i:j]


def _long_to_quoted(text):
    """A single-line `[[...]]` holding P8SCII bytes, respelled as a quoted
    string with escapes -- or None to leave it alone.

    A long string takes no escapes, so its bytes go to the file as themselves;
    main.lua is written UTF-8, so byte 0x9d becomes two bytes and every index
    into the string shifts. `picooffroad` keeps its car mesh in one of these
    and unpacks it with ord(), which then read the wrong bytes and did
    arithmetic on nil. Quoted-with-escapes carries the exact bytes.
    """
    body = text
    lvl = 0
    while body[1 + lvl:2 + lvl] == "=":
        lvl += 1
    open_len = 2 + lvl
    close = "]" + "=" * lvl + "]"
    if not body.endswith(close):
        return None                      # unterminated: spans lines, leave it
    inner = body[open_len:-len(close)]
    if "\n" in inner or not any(c > "\x7f" for c in inner):
        return None
    out = ['"']
    for ch in inner:
        cp = ord(ch)
        if cp > 255:
            return None                  # not a P8SCII byte; do not guess
        if cp > 126 or cp < 32:
            out.append("\\%d" % cp)
        elif ch == '"':
            out.append('\\"')
        elif ch == "\\":
            out.append("\\\\")
        else:
            out.append(ch)
    out.append('"')
    return "".join(out)


def _fix_string(text):
    """A p8 string literal, spelled so Lua both PARSES and RECEIVES it.

    Two different failures. `"\\^i"` is P8SCII and Lua calls it an invalid
    escape, refusing to load the cart. And a raw P8SCII byte survives parsing
    but not the FILE: main.lua is written UTF-8, so 0x87 becomes two bytes and
    the shim's print(), which reads bytes, looks up 0xC2.
    """
    out = []
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if ch == "\\" and i + 1 < n:
            nxt = text[i + 1]
            if nxt not in _LUA_ESCAPES and not nxt.isdigit():
                out.append("\\")
            out.append(ch)
            out.append(nxt)
            i += 2
            continue
        if ch in _GLYPH_TEXT:
            out.append(_GLYPH_TEXT[ch])
            i += 1
            if i < n and text[i] == _VARIATION:
                i += 1
            continue
        if ch > "\x7f":
            cp = ord(ch)
            out.append("\\%d" % cp if cp < 256 else "?")
            i += 1
            if i < n and text[i] == _VARIATION:
                i += 1
            continue
        out.append(ch)
        i += 1
    return "".join(out)


# Multi-char operators, longest first: the scan takes the first that matches, so
# `>>>` must be tried before `>>`, and `//` before `/`.
_OPS = (
    # 4 chars, then 3, then 2 -- the scan takes the FIRST match, so a longer
    # operator that starts with a shorter one has to come first or it is never
    # seen. `>>>` before `>>`, `<<>` before `<<`, `..=` before `..`.
    ">>>=", "<<>=", ">><=",
    "...", ">>>", "<<>", ">><", "..=", "//=", ">>=", "<<=", "^^=",
    "^^", "==", "~=", "!=", "<=", ">=", "..", "::", "<<", ">>", "//",
    "+=", "-=", "*=", "/=", "\\=", "%=", "^=", "&=", "|=",
)
# p8 spellings of operators Lua spells differently. Only these: Lua 5.4 has NO
# compound assignment at all, so every `op=` is expanded by a transform rather
# than renamed here, and inventing a Lua spelling for one would be a lie the
# expander then has to undo. `\\=` and `>>>=` normalise only their operator half.
_OP_MAP = {"!=": "~=", "^^": "~", ">>>": ">>", "\\": "//",
           "\\=": "//=", ">>>=": ">>="}

# `op=` -> the operator to build `x = x <op> (rhs)` with. The keys are what the
# lexer emits, so they are already normalised.
_COMPOUND = {"+=": "+", "-=": "-", "*=": "*", "/=": "/", "%=": "%",
             "^=": "^", "..=": "..", "//=": "//", "&=": "&", "|=": "|",
             "<<=": "<<", ">>=": ">>", "^^=": "~"}


def lex_line(line, state=None):
    """One source line -> ([(kind, text)], carry state).

    `state` carries an unterminated long string or `--[[ ]]` comment across
    lines, which the old per-line comment split could not do: it ended the
    comment at the newline and handed the prose inside it to the parser.
    """
    toks = []
    i = 0
    n = len(line)

    if state is not None:
        kind, level = state
        close = "]" + "=" * level + "]"
        k = line.find(close)
        if k < 0:
            toks.append((T_LONG if kind == "string" else T_COMMENT, line))
            return toks, state
        toks.append((T_LONG if kind == "string" else T_COMMENT,
                     line[:k + len(close)]))
        i = k + len(close)
        state = None

    while i < n:
        ch = line[i]

        if ch in " \t":
            j = i
            while j < n and line[j] in " \t":
                j += 1
            toks.append((T_WS, line[i:j]))
            i = j
            continue

        if ch == "-" and line[i + 1:i + 2] == "-":
            lv = _long_open(line, i + 2)
            if lv >= 0:
                close = "]" + "=" * lv + "]"
                k = line.find(close, i + 2 + lv + 2)
                if k < 0:
                    toks.append((T_COMMENT, line[i:]))
                    return toks, ("comment", lv)
                toks.append((T_COMMENT, line[i:k + len(close)]))
                i = k + len(close)
                continue
            toks.append((T_COMMENT, line[i:]))
            return toks, None

        lv = _long_open(line, i)
        if lv >= 0:
            close = "]" + "=" * lv + "]"
            k = line.find(close, i + lv + 2)
            if k < 0:
                toks.append((T_LONG, line[i:]))
                return toks, ("string", lv)
            raw = line[i:k + len(close)]
            quoted = _long_to_quoted(raw)
            toks.append((T_STR, quoted) if quoted else (T_LONG, raw))
            i = k + len(close)
            continue

        if ch in "'\"":
            q = ch
            j = i + 1
            closed = False
            while j < n:
                c = line[j]
                if c == "\\" and j + 1 < n:
                    j += 2
                    continue
                if c == q:
                    closed = True
                    j += 1
                    break
                j += 1
            body = line[i + 1:(j - 1) if closed else j]
            toks.append((T_STR, q + _fix_string(body) + (q if closed else "")))
            i = j
            continue

        if ch in _GLYPH_BTN:
            # A button glyph in an expression means the button NUMBER -- but
            # `squiddy` assigns to two of them, using single glyphs as variable
            # names to save bytes, and `1 = 0` is not Lua. So it becomes a NAME
            # the shim predefines to that number: `btn(<right>)` still reads 1,
            # and a cart that would rather use the glyph as a variable can.
            toks.append((T_NAME, "_p8g%d" % _GLYPH_CODE[ch]))
            i += 1
            if i < n and line[i] == _VARIATION:
                i += 1
            continue

        if ch > "\x7f":
            # Any OTHER P8SCII character becomes a NAME that the shim predefines
            # to the character's own code.
            #
            # Emitting the number directly was the first attempt and it was
            # half right: `fillp(#)` with a shading glyph wants the value, but
            # carts ALSO use single glyphs as variable names to save bytes --
            # `squiddy`, a 1k-jam cart, assigns to three of them, and `1 = 0`
            # is not Lua. A predefined name reads correctly in BOTH positions,
            # which is what makes it strictly better than choosing one.
            # Only the P8SCII range gets a name -- those are the ones the
            # shim predefines. A stray character from somewhere else keeps its
            # code, which at least parses.
            cp = ord(ch)
            toks.append((T_NAME, "_p8g%d" % cp) if cp <= 0xff
                        else (T_NUM, str(cp)))
            i += 1
            if i < n and line[i] == _VARIATION:
                i += 1
            continue

        if _ident(ch) and not ch.isdigit():
            j = i
            while j < n and _ident(line[j]):
                j += 1
            toks.append((T_NAME, line[i:j]))
            i = j
            continue

        if ch.isdigit() or (ch == "." and line[i + 1:i + 2].isdigit()):
            j, text = _read_number(line, i)
            toks.append((T_NUM, text))
            i = j
            continue

        for op in _OPS:
            if line.startswith(op, i):
                toks.append((T_OP, _OP_MAP.get(op, op)))
                i += len(op)
                break
        else:
            toks.append((T_OP, _OP_MAP.get(ch, ch)))
            i += 1

    return toks, state


def _needs_space(a, b):
    """Would writing these two tokens adjacent re-lex as something else?

    This is the other half of the lexer's job and it is easy to forget: the
    scan SPLIT `0or` into a number and a word correctly, and joining them back
    with no gap produced `0or` again -- the exact input Lua calls a malformed
    number. A token stream is only worth anything if it can be written back
    out as the same tokens.
    """
    ka, ta = a
    kb, tb = b
    if ka in (T_NAME, T_NUM) and kb in (T_NAME, T_NUM):
        return True
    if ka == T_NUM and kb == T_OP and tb[0] in "._":
        return True
    if ka == T_OP and kb == T_OP:
        joined = ta[-1] + tb[0]
        if joined == "--" or joined == "[[" or joined == "]]":
            return True     # a comment, or a long bracket, out of thin air
        for op in _OPS:
            if op.startswith(joined):
                return True
    return False


def render(toks):
    out = []
    prev = None
    for t in toks:
        if prev is not None and prev[0] != T_WS and t[0] != T_WS \
                and _needs_space(prev, t):
            out.append(" ")
        out.append(t[1])
        prev = t
    return "".join(out)


_LIFECYCLE = ("_init", "_update", "_update60", "_draw")

# A term is complete after one of these; what follows can only continue the
# expression through an operator.
_TERM_KINDS = (T_NAME, T_NUM, T_STR, T_LONG)
_CONTINUES = ("and", "or", "not")
# Words that can only START a statement -- so an expression before one is over.
_STOPS = ("return", "end", "else", "elseif", "then", "do", "until", "local",
          "if", "while", "for", "function", "repeat", "break", "goto", "in")


def _skip_ws_back(toks, i):
    while i > 0 and toks[i - 1][0] in (T_WS, T_COMMENT):
        i -= 1
    return i


def _skip_ws(toks, i):
    while i < len(toks) and toks[i][0] in (T_WS, T_COMMENT):
        i += 1
    return i


def _is(tok, text):
    return tok[1] == text


def lvalue_start(toks, opi):
    """Index where the lvalue ending just before `opi` begins, or -1.

    An lvalue is a NAME plus any run of `.name`, `:name` and `[expr]`, and the
    walk has to respect that SHAPE. p8 packs statements onto one line with no
    separator, so `local _ENV=n[o] phase+=speed` arrives as `n[o]phase+=speed`
    -- a character walk ate `n[o]phase` as one lvalue and assigned to a name
    that does not exist.
    """
    i = _skip_ws_back(toks, opi)
    end = i
    while i > 0:
        prev = toks[i - 1]
        if prev[0] == T_OP and prev[1] == "]":
            depth = 0
            k = i
            while k > 0:
                t = toks[k - 1]
                if t[0] == T_OP and t[1] == "]":
                    depth += 1
                elif t[0] == T_OP and t[1] == "[":
                    depth -= 1
                    if depth == 0:
                        break
                k -= 1
            if depth != 0:
                return -1
            i = k - 1
            continue
        if prev[0] == T_NAME:
            k = i - 1
            j = _skip_ws_back(toks, k)
            if j > 0 and toks[j - 1][0] == T_OP and toks[j - 1][1] in (".", ":"):
                i = j - 1
                continue
            return k
        break
    # a postfix with no name in front of it is not an lvalue
    if i < end and toks[i][0] == T_OP and toks[i][1] == "[":
        return -1
    return i if i < end else -1


def rhs_end(toks, start):
    """Index one past the end of the expression beginning at `start`.

    p8 lets statements share a line with no separator, so `dx/=l dy/=l` is two
    of them. After a complete TERM an expression can only go on via an
    OPERATOR (symbolic, or and/or/not); a name, number, string or `{` there is
    the next statement.
    """
    depth = 0
    term = False
    callable_term = False       # can a string or table literal follow as ARGS?
    i = start
    n = len(toks)
    while i < n:
        kind, text = toks[i]
        if kind in (T_WS, T_COMMENT):
            i += 1
            continue
        if kind == T_OP:
            if text in "([{":
                # `f"s"` and `f{...}` are CALLS in Lua, so a literal after a
                # callable term continues the expression. After a NUMBER it
                # cannot, and that is the case this rule exists for
                # (`x+=1 y+=2` is two statements).
                if depth == 0 and term and text == "{" and not callable_term:
                    return i
                depth += 1
            elif text in ")]}":
                if depth == 0:
                    return i          # closes something we are inside
                depth -= 1
                if depth == 0:
                    term = True
                    callable_term = True
            else:
                term = False          # an operator: the expression goes on
                callable_term = False
            i += 1
            continue
        if depth > 0:
            i += 1
            continue
        if kind == T_NAME:
            if text in _STOPS:
                return i
            if term and text not in _CONTINUES:
                return i
            term = text not in _CONTINUES
            callable_term = term
            i += 1
            continue
        # number / string / long string
        if term and not (callable_term and kind in (T_STR, T_LONG)):
            return i
        term = True
        callable_term = kind in (T_STR, T_LONG)
        i += 1
    return n


def expand_compound(toks):
    """`X op= RHS` -> `X = X op (RHS)`, as many times as a line holds.

    NOT a fixed budget: a minified cart puts its whole draw loop on one line,
    and an eight-per-line cap left the ninth `-=` unexpanded as a syntax error
    700 columns from its cause.
    """
    guard = 0
    while guard < len(toks) + 4:
        guard += 1
        at = -1
        for i in range(len(toks)):
            if toks[i][0] == T_OP and toks[i][1] in _COMPOUND:
                at = i
                break
        if at < 0:
            return toks
        lo = lvalue_start(toks, at)
        if lo < 0:
            # not an assignment we can rewrite; leave the line alone rather
            # than emit something that parses as nonsense
            return toks
        op = _COMPOUND[toks[at][1]]
        end = rhs_end(toks, at + 1)
        lhs = toks[lo:_skip_ws_back(toks, at)]
        rhs = toks[at + 1:end]
        while rhs and rhs[0][0] == T_WS:
            rhs = rhs[1:]
        # A trailing COMMENT belongs after the closing paren, not inside it:
        # `x += d[i] -- why` wrapped the comment in the parens, so the `)`
        # itself was commented out and the paren never closed.
        trail = []
        while rhs and rhs[-1][0] in (T_WS, T_COMMENT):
            trail.insert(0, rhs.pop())
        trail = [t for t in trail if t[0] == T_COMMENT]
        new = (toks[:lo] + lhs + [(T_WS, " "), (T_OP, "="), (T_WS, " ")] + lhs
               + [(T_WS, " "), (T_OP, op), (T_WS, " "), (T_OP, "(")]
               + rhs + [(T_OP, ")")])
        if end < len(toks):
            new = new + [(T_WS, " ")] + toks[end:]
        new = new + trail
        toks = new
    return toks


# p8's memory sigils: `@a` is peek(a), `%a` is peek2(a), `$a` is peek4(a).
_SIGIL = {"@": "peek", "%": "peek2", "$": "peek4"}
# Words that cannot END an expression, so what follows them is in prefix
# position. Everything else that is a name -- including nil/true/false -- is.
_NOT_TERM = _STOPS + _CONTINUES
_TERM_ENDERS = (")", "]", "}")


def _primary_end(toks, i):
    """End of the PRIMARY expression at `i`, or -1.

    A sigil binds tighter than arithmetic -- `@a+1` is `peek(a)+1`, not
    `peek(a+1)` -- so it takes a primary: a parenthesised expression, or a name
    or number plus any run of `.name`, `[expr]` and `(args)`.
    """
    i = _skip_ws(toks, i)
    if i >= len(toks):
        return -1
    kind, text = toks[i]
    if kind == T_OP and text == "(":
        depth = 0
        for k in range(i, len(toks)):
            if toks[k][0] != T_OP:
                continue
            if toks[k][1] in "([{":
                depth += 1
            elif toks[k][1] in ")]}":
                depth -= 1
                if depth == 0:
                    return k + 1
        return -1
    if kind not in (T_NAME, T_NUM):
        return -1
    i += 1
    while True:
        j = _skip_ws(toks, i)
        if j >= len(toks) or toks[j][0] != T_OP:
            return i
        if toks[j][1] in (".", ":"):
            k = _skip_ws(toks, j + 1)
            if k >= len(toks) or toks[k][0] != T_NAME:
                return i
            i = k + 1
            continue
        if toks[j][1] in ("[", "("):
            depth = 0
            for k in range(j, len(toks)):
                if toks[k][0] != T_OP:
                    continue
                if toks[k][1] in "([{":
                    depth += 1
                elif toks[k][1] in ")]}":
                    depth -= 1
                    if depth == 0:
                        break
            else:
                return i
            i = k + 1
            continue
        return i


def expand_memory_sigils(toks):
    """`@a` -> `peek(a)`, `%a` -> `peek2(a)`, `$a` -> `peek4(a)`.

    `%` is also modulo, and telling the two apart is the whole difficulty: it
    is peek2 in PREFIX position and modulo after a term. On characters that is
    guesswork; on tokens it is just "what was the last significant token".

    Runs to a FIXED POINT, because a sigil's operand can hold another one:
    `@(bs|@ls)` copies its operand through verbatim, so a single pass expanded
    the outer and left the inner as a bare `@` for Lua to choke on.
    """
    for _ in range(8):
        new_toks = _expand_sigils_once(toks)
        if new_toks == toks:
            return toks
        toks = new_toks
    return toks


def _expand_sigils_once(toks):
    out = []
    i = 0
    prev_term = False
    while i < len(toks):
        kind, text = toks[i]
        if kind == T_OP and text in _SIGIL and not prev_term:
            end = _primary_end(toks, i + 1)
            if end > 0:
                out.append((T_NAME, _SIGIL[text]))
                out.append((T_OP, "("))
                out.extend(toks[_skip_ws(toks, i + 1):end])
                out.append((T_OP, ")"))
                i = end
                prev_term = True
                continue
        if kind in (T_NAME, T_NUM, T_STR, T_LONG):
            # A KEYWORD is not a term, and that is the whole disambiguation
            # here: `return@a` is a peek, and reading `return` as a term made
            # the `@` look like it followed a value -- so it was left alone and
            # Lua could not lex it. `nil`/`true`/`false` ARE terms.
            prev_term = (text not in _NOT_TERM) if kind == T_NAME else True
        elif kind == T_OP:
            prev_term = text in _TERM_ENDERS
        out.append(toks[i])
        i += 1
    return out


# The bitwise operators. Lua 5.4 refuses them on a non-integral number and p8's
# 16.16 fixed point allows them, so the OPERANDS are floored -- the operator
# itself is left exactly where it is, which is what keeps precedence correct
# without this having to know any.
_BITOPS = ("<<", ">>", "&", "~", "|")

def _already_floored(toks, lo, hi):
    """Is toks[lo:hi] a literal, or already `flr(...)`?"""
    core = [t for t in toks[lo:hi] if t[0] != T_WS]
    if not core:
        return True
    if len(core) == 1 and core[0][0] == T_NUM:
        # A WHOLE number needs no flooring; a fractional one still does, and
        # Lua refuses `x & 0xffff.fffe` exactly as it refuses a fractional
        # variable. That literal is where p8's fixed-point masks live.
        return "." not in core[0][1]
    return core[0] == (T_NAME, "flr") and core[1:2] == [(T_OP, "(")]


def expand_bitops(toks):
    """`a & b` -> `flr(a) & flr(b)`.

    Lua 5.4 raises "number has no integer representation" for a bitwise
    operator on a non-integral float; p8's numbers are 16.16 fixed point and it
    allows them. `picooffroad` computes `(((i>>4)+time()*2)*4) & 7` for a
    dither index and stopped on its title screen.

    Wrapping the OPERANDS rather than rewriting `a & b` into `band(a, b)` is
    both simpler and much faster. Simpler because the operator stays put, so
    precedence needs no thought at all. Faster because `band` is a Lua call
    whose body makes two more, and measured on Lua 5.4 that is 20.7x the cost
    of the bare VM instruction against 4.8x for this -- and a cart doing bit
    work in an inner loop (a software blitter, a bytecode VM) runs this
    hundreds of thousands of times a frame.

    A literal operand is left alone: it is already an integer, and skipping it
    is most of the difference between 9.4x and 4.8x.
    """
    guard = 0
    while guard < 200:
        guard += 1
        done = True
        for i in range(len(toks)):
            if toks[i][0] != T_OP or toks[i][1] not in _BITOPS:
                continue
            # The two sides are decided INDEPENDENTLY: `a & ~b` has no primary
            # on the right (a unary operator is not one), and giving up on the
            # whole operator there left `a` unfloored -- the exact float that
            # raises.
            lo = _primary_start(toks, i)
            hi = _primary_end(toks, i + 1)
            rs = _skip_ws(toks, i + 1)
            wrap_r = hi > 0 and not _already_floored(toks, rs, hi)
            wrap_l = lo >= 0 and not _already_floored(toks, lo,
                                                      _skip_ws_back(toks, i))
            if not wrap_r and not wrap_l:
                continue
            out = list(toks[:hi]) if hi > 0 else list(toks)
            if wrap_r:
                out = (toks[:rs] + [(T_NAME, "flr"), (T_OP, "(")]
                       + toks[rs:hi] + [(T_OP, ")")])
            if wrap_l:
                le = _skip_ws_back(toks, i)
                out = (out[:lo] + [(T_NAME, "flr"), (T_OP, "(")]
                       + out[lo:le] + [(T_OP, ")")] + out[le:])
            toks = (out + toks[hi:]) if hi > 0 else out
            done = False
            break
        if done:
            return toks
    return toks


def _primary_start(toks, opi):
    """Start of the PRIMARY ending just before `opi`, or -1.

    The mirror of _primary_end: a `)`/`]` closes back to its opener, a name or
    number takes its `.name` / `[...]` / `(...)` chain, and a leading unary
    minus comes along.
    """
    i = _skip_ws_back(toks, opi)
    if i <= 0:
        return -1
    end = i
    prev = toks[i - 1]
    if prev[0] == T_OP and prev[1] in (")", "]"):
        want = "(" if prev[1] == ")" else "["
        depth = 0
        k = i
        while k > 0:
            t = toks[k - 1]
            if t[0] == T_OP and t[1] in (")", "]"):
                depth += 1
            elif t[0] == T_OP and t[1] in ("(", "["):
                depth -= 1
                if depth == 0:
                    break
            k -= 1
        if depth != 0:
            return -1
        i = k - 1
        # A call or index has a name in front of it -- but a KEYWORD is not a
        # callee. `return (a) & 1` looks exactly like a call to something
        # named `return`, and taking it produced `band(return (a), 1)`.
        j = _skip_ws_back(toks, i)
        if j > 0 and toks[j - 1][0] == T_NAME \
                and toks[j - 1][1] not in _NOT_TERM:
            i = j - 1
        else:
            return i
    elif prev[0] in (T_NAME, T_NUM):
        if prev[0] == T_NAME and prev[1] in _NOT_TERM:
            return -1
        i -= 1
    else:
        return -1
    # walk back over a `.name` / `:name` chain
    while True:
        j = _skip_ws_back(toks, i)
        if j > 1 and toks[j - 1][0] == T_OP and toks[j - 1][1] in (".", ":") \
                and toks[j - 2][0] in (T_NAME, T_NUM):
            i = j - 2
            continue
        break
    # a unary minus belongs to the primary
    j = _skip_ws_back(toks, i)
    if j > 0 and toks[j - 1][0] == T_OP and toks[j - 1][1] == "-":
        k = _skip_ws_back(toks, j - 1)
        if k == 0 or (toks[k - 1][0] == T_OP and toks[k - 1][1] not in (")", "]")):
            i = j - 1
    return i if i < end else -1


def if_do_to_then(toks):
    """p8 accepts `do` wherever Lua wants `then`.

    Not one cart's typo: `moss moss` writes `if cond do` twenty-two times and
    the word `then` zero times.
    """
    depth = 0
    pending = False
    out = list(toks)
    for i in range(len(out)):
        kind, text = out[i]
        if kind == T_OP:
            if text in "([{":
                depth += 1
            elif text in ")]}":
                depth -= 1
            continue
        if kind != T_NAME or depth != 0:
            continue
        if text == "if" or text == "elseif":
            pending = True
        elif pending and text == "do":
            out[i] = (T_NAME, "then")
            pending = False
        elif pending and (text == "then" or text in _STOPS):
            pending = False
    return out


def expand_print_shorthand(toks):
    """p8's `?x` -> `print(x)`, wherever it appears.

    `?` has no other meaning in p8, so the only real question is where the
    closing paren goes: `?` prints the rest of the LINE, but a line can end
    with block keywords that must stay OUTSIDE the call. Firing blindly turned
    `if a then ?x end` into `if a then print(x end)`.

    So the arguments run to the end of the line minus any trailing `end` /
    `else` / `elseif` / `until` and any comment. That is what lets this fire
    mid-line, which the earlier statement-start-only rule could not: `squiddy`
    minifies to `y=-y?"text",108,60` and its print was left as a bare `?`.
    """
    for i in range(len(toks)):
        if toks[i][0] != T_OP or toks[i][1] != "?":
            continue
        rest = toks[i + 1:]
        if not [t for t in rest if t[0] not in (T_WS, T_COMMENT)]:
            return toks                  # a `?` with nothing after it
        tail = []
        while rest:
            last = None
            for k in range(len(rest) - 1, -1, -1):
                if rest[k][0] not in (T_WS, T_COMMENT):
                    last = k
                    break
            if last is None:
                break
            if rest[last][0] == T_NAME and rest[last][1] in _BLOCK_ENDS:
                tail = rest[last:] + tail
                rest = rest[:last]
                continue
            break
        while rest and rest[-1][0] == T_COMMENT:
            tail.insert(0, rest.pop())
        while rest and rest[-1][0] == T_WS:
            rest.pop()
        while rest and rest[0][0] == T_WS:
            rest = rest[1:]
        head = [] if (i and toks[i - 1][0] == T_WS) else [(T_WS, " ")]
        if tail and tail[0][0] != T_WS:
            tail = [(T_WS, " ")] + tail
        return (toks[:i] + head + [(T_NAME, "print"), (T_OP, "(")]
                + rest + [(T_OP, ")")] + tail)
    return toks


# p8's one-line block forms and the word that opens their body in Lua. `while`
# is not a bonus: `nimudazus` writes `while(n<=#e and e[n].o>o.o)n+=1`, and
# treating only `if` this way left that as a syntax error.
_ONELINE = {"if": "then", "while": "do"}


def _oneline_if_at(toks, start):
    """Index of a p8 short-`if`/short-`while` at or after `start`, or -1.

    NOT just the start of the line: p8 lets statements share a line, and
    `key=mget(e,n) if(key==9)mset(...)` is one line of a real cart.
    """
    for i in range(start, len(toks)):
        if toks[i][0] != T_NAME or toks[i][1] not in _ONELINE:
            continue
        j = _skip_ws(toks, i + 1)
        if j < len(toks) and toks[j][0] == T_OP and toks[j][1] == "(":
            return i
    return -1


def expand_oneline_if(toks, start=0):
    """p8's `if (cond) stmt` -> `if cond then stmt end`, and the same for
    `while (cond) stmt` -> `while cond do stmt end`.

    Tries EVERY candidate on the line: `if(a or b)and c then` opens with an
    `if(` that is not a short-if -- the parens are a sub-expression and the
    condition goes on -- and stopping there left the real one unexpanded.
    """
    at = _oneline_if_at(toks, start)
    if at < 0:
        return toks
    word = toks[at][1]
    opener = _ONELINE[word]
    p = _skip_ws(toks, at + 1)
    depth = 0
    close = -1
    for k in range(p, len(toks)):
        if toks[k][0] != T_OP:
            continue
        if toks[k][1] in "([{":
            depth += 1
        elif toks[k][1] in ")]}":
            depth -= 1
            if depth == 0:
                close = k
                break
    if close < 0:
        return expand_oneline_if(toks, at + 1)
    cond = toks[p + 1:close]
    rest = toks[close + 1:]
    head = _skip_ws(rest, 0)
    if head >= len(rest):
        return expand_oneline_if(toks, at + 1)
    kind, text = rest[head]
    if kind == T_COMMENT:
        return expand_oneline_if(toks, at + 1)
    if kind == T_NAME and text in ("then", "do", "and", "or", "not"):
        return expand_oneline_if(toks, at + 1)   # a normal block, or a
                                                 # condition that goes on
    if kind == T_OP and text in ("+", "-", "*", "/", "%", "<", ">", "=", "~",
                                 ".", "(", "[", "{", "^", "..", "==", "<=",
                                 ">=", "~=", "and", "or"):
        return expand_oneline_if(toks, at + 1)
    body = expand_oneline_if(expand_print_shorthand(rest[head:]))
    tail = []
    while body and body[-1][0] == T_COMMENT:
        tail.insert(0, body.pop())
    return (toks[:at] + [(T_NAME, word), (T_WS, " ")] + cond
            + [(T_WS, " "), (T_NAME, opener), (T_WS, " ")] + body
            + [(T_WS, " "), (T_NAME, "end")] + tail)


def rename_lifecycle(toks):
    """`_update` -> `p8_update`, wherever the cart says it as a GLOBAL.

    Not just `function _update(`. A cart may write `_update = function() ...`
    instead, and `poom` does exactly that for both of its lifecycle hooks --
    the rename missed them, so the console's driver found nothing to call and
    the cart drew its first frame and stopped. Renaming the NAME rather than
    the definition form catches every spelling, and renames uses along with
    definitions so they still agree.

    A field access is left alone: `t._draw` is the cart's own table, not the
    lifecycle hook.
    """
    out = list(toks)
    for i in range(len(out)):
        if out[i][0] != T_NAME or out[i][1] not in _LIFECYCLE:
            continue
        j = _skip_ws_back(out, i)
        if j > 0 and out[j - 1][0] == T_OP and out[j - 1][1] in (".", ":"):
            continue
        out[i] = (T_NAME, "p8" + out[i][1])
    return out


def _is_empty_music_stub(toks):
    """`function music(...) end` and nothing else -- the cart silencing itself.

    The celeste-maker mirror ships one. The port imports __music__ as real
    tracks, so the stub is dropped and the shim's music() plays them; a
    NON-empty override is real cart behaviour and is kept.
    """
    core = [t for t in toks if t[0] not in (T_WS, T_COMMENT)]
    if len(core) < 5:
        return False
    if core[0] != (T_NAME, "function") or core[1] != (T_NAME, "music"):
        return False
    if core[2] != (T_OP, "("):
        return False
    k = 3
    while k < len(core) and core[k] != (T_OP, ")"):
        if core[k][0] not in (T_NAME, T_OP):
            return False
        k += 1
    return k + 1 < len(core) and core[k + 1] == (T_NAME, "end") \
        and len(core) == k + 2


def _compound_wants_more(toks):
    """Does this line end on an `op=` with nothing after it?

    p8 lets an expression continue onto the next line, and a compound assign is
    where that bites: `peephole +=` on one line with its value on the next
    expanded to `peephole = peephole + ()` -- an empty pair of parens.
    `PICO-BALL` writes it that way, and a line-at-a-time pipeline cannot see
    the value from here.
    """
    for i in range(len(toks)):
        if toks[i][0] == T_OP and toks[i][1] in _COMPOUND:
            if not [t for t in toks[i + 1:] if t[0] not in (T_WS, T_COMMENT)]:
                return True
    return False


def p8_lua_to_lua54(lines):
    out = []
    state = None
    pending = ""
    for line in lines:
        line = line.replace("\t", "  ").rstrip()
        if pending:
            line = pending + " " + line.lstrip()
            pending = ""
        toks, state = lex_line(line, state)
        # Hold a line that ends on an `op=` and glue the next one to it. The
        # blank keeps the line COUNT, so a later error still points where the
        # cart's author would look.
        if state is None and _compound_wants_more(toks):
            pending = line
            out.append("")
            continue
        if _is_empty_music_stub(toks):
            out.append("-- [port] dropped the cart's empty music() stub "
                       "(imported __music__ plays instead)")
            continue
        toks = expand_print_shorthand(toks)
        toks = expand_memory_sigils(toks)
        toks = expand_bitops(toks)
        toks = if_do_to_then(toks)
        toks = expand_oneline_if(toks)
        toks = expand_compound(toks)
        toks = rename_lifecycle(toks)
        out.append(render(toks))
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
local P8_DT = 1 / 30               -- _update's rate; _update60 relocks it to 60
if P8_VH < 128 then view(128, P8_VH) end
do
  local m_spr, m_btn, m_btnp = spr, btn, btnp
  local m_camera = camera
  local m_rect, m_rectb = rect, rectb
  local m_circ, m_circb = circ, circb
  -- The 2026-09 core verbs: nil on a host that predates them, and every use
  -- below is guarded so the cart still runs there.
  local m_oval, m_ovalb, m_fillp = oval, ovalb, fillp
  local m_sget, m_sset, m_palt = sget, sset, palt
  local m_fget, m_fset, m_map = fget, fset, map
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
  -- btnp reads the LATCH and nothing else, and both halves of that matter.
  --
  -- Latched, because a 30fps cart ticks every OTHER console frame while an
  -- engine press edge lasts ONE, so reading the engine directly ate half of
  -- all presses.
  --
  -- And nothing else, because the fallback that used to sit here double-
  -- counted the other way round: a 60fps cart on a 30fps host runs TWO ticks
  -- inside one console frame, the first tick cleared the latch, and the second
  -- still saw the engine's edge -- which is live for that whole frame. One tap
  -- of left moved two slots in an upgrade menu. The latch is set once per
  -- console frame and cleared by the tick that consumes it, so one press is
  -- one edge at any pair of rates.
  --
  -- Held, btnp REPEATS: p8 fires again after a 15-tick delay, then every 4.
  -- That is what makes a menu scroll while a cart holds left, and without it
  -- the only way through a long upgrade list is to tap it item by item. The
  -- counters advance per TICK (the cart's own frames), not per console frame,
  -- so the cadence is the cart's at either rate.
  --
  -- ONE DELIBERATE DIVERGENCE from fake-08, which is otherwise the reference
  -- here. Its predicate is `held == 15 or (held >= 15 and held % 4 == 0)`,
  -- which fires on tick 15 AND tick 16 -- two edges one tick apart, which is
  -- precisely the doubled menu move this repeat was added alongside fixing.
  -- We keep its steady-state cadence (16, 20, 24 ...) and drop the adjacent
  -- 15. The delay is one tick longer; nothing can feel that.
  local pending, hold = {}, {}
  local RPT_DELAY, RPT_EVERY = 15, 4
  -- btn() with NO argument is a different verb: p8 returns a BITFIELD of every
  -- button, bit i for button i. `squiddy` reads `b=btn()` and then does
  -- arithmetic on it, so returning a boolean stopped the cart on flr(true).
  function btn(i)
    if i == nil then
      local m = 0
      for k = 0, 5 do
        if m_btn(BTN[k]) then m = m | (1 << k) end
      end
      return m
    end
    return m_btn(BTN[i] or "a")
  end
  function btnp(i)
    if i == nil then
      local m = 0
      for k = 0, 5 do
        if btnp(k) then m = m | (1 << k) end
      end
      return m
    end
    if pending[i] then return true end
    local h = hold[i]
    if h and h > RPT_DELAY and (h - RPT_DELAY - 1) % RPT_EVERY == 0 then
      return true
    end
    return false
  end

  -- PICO-8 numbers are 16.16 fixed point and every API arg is implicitly
  -- FLOORED (p8 carts pass float colors/coords everywhere -- celeste's "1000"
  -- popup draws with color 7+flash%2). The moy engine takes integer indices,
  -- so this shim floors at the boundary.
  local mfloor = math.floor
  -- p8 coerces every API number argument: nil is 0, a numeric string is its
  -- number, and anything else (`pset(x, y, color)` -- the API function, a
  -- typo for a `colour` parameter, live in `picooffroad`) is 0 rather than
  -- an error.
  local function fl(v)
    if type(v) ~= "number" then v = tonumber(v) or 0 end
    return mfloor(v)
  end

  -- Declared HERE, above the fill verbs that read it. It was declared beside
  -- fillp() further down, which is after rectfill -- so rectfill closed over
  -- nothing and read a nil GLOBAL instead, and the transparency skip was dead
  -- code that tested green by doing nothing. A test that filled the screen
  -- and looked at it is what caught that.
  local fill_pattern, fill_transparent = 0, false
  -- p8's pen: the colour a draw verb uses when the cart passes none, and
  -- its print cursor.
  local p8_pen = 6
  local p8_cx, p8_cy = 0, 0

  -- A p8 COLOUR argument is a byte: the low nibble draws; bit 7 picks the
  -- secret palette, which this port ships at indices 16-31 (SPEC.md 2.2);
  -- and, when a fill pattern is set, the high nibble is the colour the
  -- pattern's holes take -- unless the pattern's 0x0.8 bit says they are
  -- transparent. On a host with the console's fillp that is one call per
  -- shape; the older host keeps its transparent-pattern-draws-nothing.
  local function pcol(c)
    c = fl(c == nil and p8_pen or c)
    if c >= 128 then return 16 + (c & 15) end
    return c & 15
  end
  local function shape_col(c)
    c = fl(c == nil and p8_pen or c)
    if fill_pattern ~= 0 and m_fillp ~= nil then
      m_fillp(fill_pattern, fill_transparent and -1 or ((c >> 4) & 15))
    end
    if c >= 128 then return 16 + (c & 15) end
    return c & 15
  end
  local function fill_skip()
    return fill_transparent and (m_fillp == nil or fill_pattern == 0xffff)
  end

  -- Every P8SCII picture character, as a global holding its own code. The
  -- porter renames a glyph in the cart's code to one of these, so a cart that
  -- passes a shading glyph to fillp() gets the number and one that uses a
  -- glyph as a VARIABLE gets a variable.
  for _c = 0x80, 0xff do
    _ENV["_p8g" .. _c] = _c
  end
  -- ...except the six BUTTONS, which stand for the button they name.
  _p8g139, _p8g145, _p8g148 = 0, 1, 2      -- left, right, up
  _p8g131, _p8g142, _p8g151 = 3, 4, 5      -- down, O, X

  function camera(cx, cy) m_camera(fl(cx), fl(cy)) end
  -- p8 math over the sandboxed Lua math lib (the moy api only registers
  -- rnd/flr; a python cart gets abs/min/max from python builtins, a lua cart
  -- gets them here). p8 angles are TURNS (0..1) and sin is flipped (+y down).
  function sin(t) return -msin((t or 0) * 6.283185307179586) end
  function cos(t) return mcos((t or 0) * 6.283185307179586) end
  local mabs = math.abs
  function flr(v) return mfloor(v or 0) end
  function abs(v) return mabs(v or 0) end
  -- p8 coerces nil to 0 in arithmetic, so `min(nil, 5)` is 0 there and an
  -- error in Lua. `dank_tomb` stops on its first frame otherwise, and the
  -- message has no line number in it -- the error is raised inside math.min,
  -- so there is nothing in the cart to point at.
  local mmin, mmax = math.min, math.max
  function min(a, b) return mmin(a or 0, b or 0) end
  function max(a, b) return mmax(a or 0, b or 0) end
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
      m_spr(n, x, y, -1, 1, flip)              -- transparency is palt state
    else
      for ty = 0, h - 1 do
        for tx = 0, w - 1 do
          local cx = fx and (w - 1 - tx) or tx
          local cy = fy and (h - 1 - ty) or ty
          m_spr(n + cx + cy * 16, x + tx * 8, y + ty * 8, -1, 1, flip)
        end
      end
    end
  end

  -- p8 rect/circ are OUTLINES and rectangles take the far corner
  function rectfill(x0, y0, x1, y1, c)
    if fill_skip() then return end
    x0 = fl(x0) y0 = fl(y0) x1 = fl(x1) y1 = fl(y1)
    if x1 < x0 then x0, x1 = x1, x0 end
    if y1 < y0 then y0, y1 = y1, y0 end
    m_rect(x0, y0, x1 - x0 + 1, y1 - y0 + 1, shape_col(c))
  end
  function rect(x0, y0, x1, y1, c)
    if fill_skip() then return end
    x0 = fl(x0) y0 = fl(y0) x1 = fl(x1) y1 = fl(y1)
    if x1 < x0 then x0, x1 = x1, x0 end
    if y1 < y0 then y0, y1 = y1, y0 end
    m_rectb(x0, y0, x1 - x0 + 1, y1 - y0 + 1, shape_col(c))
  end
  function circfill(x, y, r, c)
    if fill_skip() then return end
    m_circ(fl(x), fl(y), fl(r), shape_col(c))
  end
  function circ(x, y, r, c)
    if fill_skip() then return end
    m_circb(fl(x), fl(y), fl(r), shape_col(c))
  end
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
  local P8_WIDE = {}
  do
    local hex = "7f7f7f7f7f552a552a5541777f553e0000000000114411441108493e49081c3e"
             .. "7f3e1c367f7f3e081c2249221c1c1c7f0814083e7f415d00000000003e41555d"
             .. "3e7848080e0e0000000000081c3e1c0800000000490000000000081c7f3e367f"
             .. "3e083e7f0000000000003e410000081422410000000000007f007f007f555555"
             .. "5555"
    for i = 0, 25 do
      local rows, any = {}, false
      for r = 0, 4 do
        local v = tonumber(string.sub(hex, i * 10 + r * 2 + 1, i * 10 + r * 2 + 2), 16)
        rows[r] = v
        if v ~= 0 then any = true end
      end
      if any then P8_WIDE[128 + i] = rows end
    end
  end
  -- The button codes resolved to LETTERS at draw time, so every route agrees:
  -- a literal glyph the import rewrote, a `\151` escape it did not, chr(151),
  -- string.char, a concatenation built at runtime. The import-time rewrite is
  -- what makes the ported source readable; this is what makes it correct.
  local BTN_GLYPH = {[139] = 60, [145] = 62, [148] = 94, [131] = 118,
                     [142] = 65, [151] = 66}
  local sbyte = string.byte
  local m_p8print = __moy_p8print
  function print(s, x, y, c)
    s = tostring(s)
    -- print(s) and print(s, c) take the cursor and advance it a line, as
    -- PICO-8 does (without its scrolling); the four-argument form is placed.
    if y == nil then
      c = x
      x, y = p8_cx, p8_cy
      p8_cy = p8_cy + 6
    end
    c = pcol(c)
    local lx = fl(x)
    if m_p8print ~= nil then
      -- The console draws the PICO-8 font itself (moy_p8.c): one call for the
      -- string instead of fifteen pix() calls a glyph.
      return m_p8print(s, lx, fl(y), c)
    end
    local cx, cy = lx, fl(y)
    for i = 1, #s do
      local b = sbyte(s, i)
      if b == 10 then
        cx, cy = lx, cy + 6
      else
        b = BTN_GLYPH[b] or b
        local g = P8_GLYPHS[b]
        if g then
          if g ~= 0 then
            for p = 0, 14 do
              if (g >> p) & 1 == 1 then m_pix(cx + p % 3, cy + p // 3, c) end
            end
          end
          cx = cx + 4
        else
          -- P8SCII's picture glyphs are DOUBLE WIDE: 7x5 on an 8px advance,
          -- which is why the advance cannot just be 4 for everything.
          local w = P8_WIDE[b]
          if w then
            for r = 0, 4 do
              local v = w[r]
              for k = 0, 6 do
                if (v >> k) & 1 == 1 then m_pix(cx + k, cy + r, c) end
              end
            end
            cx = cx + 8
          else
            cx = cx + 4
          end
        end
      end
    end
  end

  local m_pal = pal
  -- p8's pal() with no arguments resets BOTH palettes and the transparency
  -- (colour 0 transparent, the rest opaque); the console's resets one.
  local function p8_palt_default()
    m_palt()
    m_palt(0, true)
  end
  -- The SCREEN palette, pal(c0, c1, 1): p8 keeps it across frames (a cart
  -- sets its fade once and draws), the console resets draw state every
  -- frame -- so it is remembered here and re-applied at the top of _draw.
  local spal, spal_live = {}, false
  local function spal_set(c0, c1)
    spal[c0] = c1
    spal_live = true
    m_pal(c0, c1, 1)
  end
  local function spal_apply()
    if not spal_live then return end
    for i = 0, 15 do
      local v = spal[i]
      if v ~= nil and v ~= i then m_pal(i, v, 1) end
    end
  end
  function pal(a, b, p)
    if a == nil then m_pal() spal, spal_live = {}, false p8_palt_default() return end
    if type(a) == "table" then
      -- p8 0.2.0's TABLE form: a whole palette in one call. A table with a
      -- [0] entry keys by colour directly; a plain array maps its i-th entry
      -- onto colour i-1. Carts use it for per-scene recolours, and floor()ing
      -- a table is what stopped two of them on their first frame.
      local shift = (a[0] ~= nil) and 0 or 1
      local screen = (b == 1)
      for k, v in pairs(a) do
        if type(k) == "number" and type(v) == "number" then
          if screen then spal_set(fl(k) - shift, pcol(v))
          else m_pal(fl(k) - shift, pcol(v)) end
        end
      end
      return
    end
    if p == 1 then spal_set(fl(a) & 15, pcol(b)) return end
    m_pal(fl(a) & 15, pcol(b))
  end
  -- palt(): p8's default is colour 0 transparent; palt(c, t) sets one;
  -- palt(bits) (0.2.0) sets all sixteen from a bitfield, bit 15 = colour 0.
  -- Transparency is STATE here rather than a colorkey on every spr, so
  -- `palt(0, false)` really does draw a sprite's black pixels.
  function palt(c, t)
    if c == nil then p8_palt_default() return end
    if t == nil then
      local bits = fl(c)
      m_palt()
      for i = 0, 15 do m_palt(i, (bits >> (15 - i)) & 1 == 1) end
      return
    end
    m_palt(fl(c) & 15, t and true or false)
  end
  function pset(x, y, c) m_pix(fl(x), fl(y), pcol(c)) end
  function pget(x, y) return m_pix(fl(x), fl(y)) end
  local m_line = line
  function line(x0, y0, x1, y1, c)
    if fill_skip() then return end
    m_line(fl(x0), fl(y0), fl(x1), fl(y1), shape_col(c))
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
    -- p8's all(nil) is an empty loop, not an error. Carts lean on it for
    -- "iterate this list if there is one", and `dank_tomb` stops on its first
    -- frame without it.
    if t == nil then return function() return nil end end
    local i = 0
    local v
    return function()
      if t[i] == v then i = i + 1 end
      v = t[i]
      return v
    end
  end
  function foreach(t, f) for v in all(t) do f(v) end end
  function count(t, v)
    if v == nil then return #t end
    local n = 0
    for i = 1, #t do if t[i] == v then n = n + 1 end end
    return n
  end

  -- p8 INDEXES STRINGS: `s[i]` is the i-th character, and `#s` its length.
  -- Lua gives strings a metatable whose __index is the string library, so
  -- `s[1]` is nil there and a cart reading its own packed data byte by byte
  -- gets nil and stops. Two of twelve measured carts do exactly that -- one
  -- unpacking sprites from a string, one measuring text width.
  do
    local smeta = getmetatable("")
    if smeta then
      local slib = smeta.__index
      smeta.__index = function(str, k)
        if type(k) == "number" then
          if k < 0 then k = #str + k + 1 end
          return string.sub(str, k, k)
        end
        return slib[k]
      end
    end
  end
  sub = string.sub
  tostr = tostring
  function sgn(x) if (x or 0) < 0 then return -1 end return 1 end
  function mid(a, b, c) return max(min(a, b), min(max(a, b), c)) end
  -- rnd(t) on a TABLE returns a random ELEMENT of it (p8 0.2.0), which is not
  -- a variant of the numeric form -- it is a different verb wearing the same
  -- name, and carts use it for exactly the kind of pick-one that appears in
  -- every update: `rnd(spawn_points)`. Without it the cart dies on arithmetic
  -- against a table the first time that line runs.
  function rnd(n)
    if type(n) == "table" then
      local c = #n
      if c == 0 then return nil end
      return n[mrandom(c)]
    end
    return mrandom() * (n or 1)
  end

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
  -- The six button codes agree with what the IMPORT does to the same glyph in
  -- a literal: a cart that builds its legend with chr(151) must not get a
  -- different answer from one that typed the character.
  local BTN_CHR = {[139] = "<", [145] = ">", [148] = "^", [131] = "v",
                   [142] = "A", [151] = "B"}
  function chr(...)
    local n = select("#", ...)
    if n == 1 then
      local c = ...
      local b = BTN_CHR[c]
      if b then return b end
    end
    return string.char(...)
  end
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
  -- The console's own kernel where it has one (SPEC.md 6, 2026-09); the
  -- row-by-row Lua above on a host that predates it.
  local function _oval_box(x0, y0, x1, y1)
    x0, y0, x1, y1 = fl(x0), fl(y0), fl(x1), fl(y1)
    if x1 < x0 then x0, x1 = x1, x0 end
    if y1 < y0 then y0, y1 = y1, y0 end
    return x0, y0, x1 - x0 + 1, y1 - y0 + 1
  end
  function oval(x0, y0, x1, y1, col)
    if m_ovalb == nil then _oval(x0, y0, x1, y1, col, false) return end
    if fill_skip() then return end
    local x, y, w, h = _oval_box(x0, y0, x1, y1)
    m_ovalb(x, y, w, h, shape_col(col))
  end
  function ovalfill(x0, y0, x1, y1, col)
    if m_oval == nil then _oval(x0, y0, x1, y1, col, true) return end
    if fill_skip() then return end
    local x, y, w, h = _oval_box(x0, y0, x1, y1)
    m_oval(x, y, w, h, shape_col(col))
  end

  -- The rest of PICO-8's surface that is plain Lua or plain arithmetic. None
  -- of these needed a console verb; they were simply never written down, so a
  -- cart calling one crashed with nothing said at import time.
  ceil = math.ceil
  function srand(x) return mrandomseed(flr(x or 0)) end
  -- COROUTINES: p8's names for Lua's own library, which SPEC.md 4.1 admits
  -- (2026-09-02). Guarded, because a host on an older binding still nils it,
  -- and a nil-guarded alias is a nil call at the site instead of at load.
  if coroutine ~= nil then
    cocreate, coresume, costatus, yield = coroutine.create, coroutine.resume,
                                          coroutine.status, coroutine.yield
    coclose = coroutine.close
  end
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
  -- p8's bitwise operators work on its 16.16 FIXED POINT representation, so a
  -- mask WITH A FRACTION is meaningful there: `x & 0xffff.fffe` is idiomatic
  -- p8 for "drop the lowest fractional bit". We floor instead, which discards
  -- the fraction entirely.
  --
  -- THAT IS NOT A SHORTCUT, IT IS THE PLATFORM. libmoy builds Lua with
  -- LUA_32BITS, which makes lua_Number a SINGLE-PRECISION float -- 24 bits of
  -- mantissa against the 32 a 16.16 value needs. Measured on the real console
  -- (libmoy/build/run_cart, 2026-09-01): `0xffff.fffe` is already 65536.0 by
  -- the time the cart sees it, so the mask a cart wrote does not survive being
  -- READ, let alone applied. Do not re-attempt it without changing
  -- LUA_FLOAT_TYPE, which is a spec decision and not a porter fix.
  --
  -- A faithful implementation WAS written, and what let it look correct is the
  -- part worth remembering: it was checked in a scratch `lupa` script. lupa is
  -- 64-bit, every tier this project ships is LUA_32BITS, and lupa was DELETED
  -- from the host in 2026-08-14 for precisely that reason. The host's own Lua
  -- would have said no. Do not check numeric semantics on a Lua nobody ships.
  --
  -- The cost is real and bounded: a cart that masks fractional bits (dank_tomb
  -- parses its config that way) reads zeros. Integer masks -- every other cart
  -- measured -- are exact, because a whole number's low 16 fixed-point bits
  -- are zero and flooring discards nothing.
  function band(a, b) return flr(a) & flr(b) end
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
    m_sspr(sx, sy, sw, sh, dx, dy, dw or sw, dh or sh, -1, f)
  end

  -- map + flags: the map DATA now ships as map.moymap (the console's own
  -- format -- editable, native-map()-able); build the fast Lua-side lookup
  -- from it ONCE at start via the console mget (captured before the p8 mget
  -- shadows it). __gff__ stays baked below the shim (flags have no moy home).
  -- ===================== the compatibility layer =====================
  -- p8 verbs that reach for a MACHINE this console does not have: raw memory,
  -- a dither pattern register, machine counters, the cart ROM. Each one was a
  -- nil-call before -- the cart stopped dead the first frame it ran the line.
  --
  -- The rule here is: never crash, be honest where we can be, and be a
  -- harmless no-op where we cannot. The import report says which is which, so
  -- a cart that comes out looking wrong says so on the way in.

  -- MEMORY. A host carrying PICO-8's memory map in C (`__moy_poke` and its
  -- siblings: sheet, map, flags, draw palette, camera/clip and the SCREEN
  -- behind their PICO-8 addresses) gets every memory verb routed to it, one
  -- binding call per byte -- ~0.9us on the P4, ~1.5us on the S3 boards,
  -- against 8-13us for the sparse table below. Without it: 64K of SCRATCH,
  -- sparse -- a cart's own bookkeeping works exactly, a poke at hardware is
  -- remembered and affects nothing. (Routing only 0x2000 to the real map
  -- through THIS table was tried and reverted: `picooffroad` uses the region
  -- as free memory, and two stores in two encodings is the seam that broke.)
  local _mrd, _mwr
  if __moy_poke ~= nil then
    local cpeek, cpoke = __moy_peek, __moy_poke
    local cmemcpy, cmemset = __moy_memcpy, __moy_memset
    _mrd, _mwr = cpeek, cpoke
    function peek(a, n)
      if n == nil or n <= 1 then return cpeek(a) end
      local out = {}
      for i = 0, fl(n) - 1 do out[i + 1] = cpeek(a + i) end
      return table.unpack(out)
    end
    function poke(a, v, ...)
      cpoke(a, v or 0)
      local n = select("#", ...)
      for i = 1, n do cpoke(a + i, select(i, ...) or 0) end
    end
    function memcpy(dst, src, len) cmemcpy(dst, src, len or 0) end
    function memset(dst, val, len) cmemset(dst, val or 0, len or 0) end
  else
    local p8mem = {}
    _mrd = function(a) return p8mem[a] or 0 end
    _mwr = function(a, v)
      if a >= 0 and a < 0x8000 then p8mem[a] = v & 0xff end
    end
    function peek(a, n)
      a = fl(a)
      if n == nil or n <= 1 then return _mrd(a) end
      local out = {}
      for i = 0, fl(n) - 1 do out[i + 1] = _mrd(a + i) end
      return table.unpack(out)
    end
    function poke(a, ...)
      a = fl(a)
      local n = select("#", ...)
      for i = 1, n do _mwr(a + i - 1, fl(select(i, ...) or 0)) end
    end
    function memcpy(dst, src, len)
      dst, src, len = fl(dst), fl(src), fl(len or 0)
      if dst == src or len <= 0 then return end
      if dst < src then
        for i = 0, len - 1 do _mwr(dst + i, _mrd(src + i)) end
      else
        for i = len - 1, 0, -1 do _mwr(dst + i, _mrd(src + i)) end
      end
    end
    function memset(dst, val, len)
      dst, val, len = fl(dst), fl(val or 0), fl(len or 0)
      for i = 0, len - 1 do _mwr(dst + i, val) end
    end
  end
  function peek2(a) a = fl(a) local v = _mrd(a) | (_mrd(a + 1) << 8)
    if v >= 0x8000 then v = v - 0x10000 end return v end
  function poke2(a, v) a, v = fl(a), fl(v or 0) & 0xffff
    _mwr(a, v & 0xff) _mwr(a + 1, (v >> 8) & 0xff) end
  function peek4(a) a = fl(a)
    local v = _mrd(a) | (_mrd(a+1) << 8) | (_mrd(a+2) << 16) | (_mrd(a+3) << 24)
    return v / 65536.0 end
  function poke4(a, v) a = fl(a)
    local raw = fl((v or 0) * 65536) & 0xffffffff
    _mwr(a, raw & 0xff) _mwr(a+1, (raw>>8) & 0xff)
    _mwr(a+2, (raw>>16) & 0xff) _mwr(a+3, (raw>>24) & 0xff) end

  -- SAVE DATA is the one that can be honest all the way down: p8's 64 cartdata
  -- slots and the console's pmem are the same shape, so a cart's progress
  -- really does survive being closed.
  local m_pmem = pmem
  function cartdata(id) return true end
  function dget(i) i = fl(i) if i < 0 or i > 63 then return 0 end
    return m_pmem(i) or 0 end
  function dset(i, v) i = fl(i) if i < 0 or i > 63 then return end
    m_pmem(i, fl(v or 0)) end

  -- MACHINE COUNTERS. The ones with a real answer here get one; the rest read
  -- 0, which is what a cart's "cpu at 12%" HUD will show.
  -- stat() reads MACHINE COUNTERS, and the TYPE of the answer matters as much
  -- as the value: p8's string stats return strings and its boolean stats
  -- return booleans. Returning 0 for everything sent `terra` into
  -- `if stat(6) ~= "" then loadplayer(stat(6))` -- 0 is not "", so it tried to
  -- load a save that was the number zero, and the cart never came back.
  function stat(n, a)
    n = fl(n)
    if n == 4 or n == 6 or n == 31 then return "" end     -- clipboard, param,
                                                          -- keypress char
    if n == 30 or n == 120 or n == 121 then return false end
    if n == 28 then return false end                      -- key held (b, key)
    if n >= 16 and n <= 26 then return -1 end             -- sfx/music channels
    return 0                                              -- cpu, memory, mouse,
                                                          -- date parts, the rest
  end

  -- fillp() is a DITHER PATTERN for the fill verbs. The console fills solid,
  -- so the pattern is remembered and not used: the cart runs and its gradients
  -- come out flat. Six of twelve measured carts call it, and every one of them
  -- used to stop dead here.
  -- fillp() is a DITHER PATTERN for the fill verbs, and the console fills
  -- solid -- but the pattern's fractional 0.5 bit means "colour 1 is
  -- TRANSPARENT", and that half we can honour exactly by not drawing.
  --
  -- It matters more than the dithering does. Carts fade between scenes by
  -- setting a transparent pattern and filling the whole screen:
  -- `picooffroad` does exactly that every transition, and ignoring the
  -- transparency turned each fade into a solid black screen -- strictly worse
  -- than the flat fill everything else gets.
  function fillp(p)
    p = p or 0
    if type(p) ~= "number" then p = tonumber(p) or 0 end
    fill_pattern = mfloor(p) & 0xffff
    fill_transparent = (p % 1) >= 0.5
    -- The console holds the pattern as draw state; a shape re-applies a live
    -- one before it draws (shape_col), so only the RESET needs saying now.
    if fill_pattern == 0 and m_fillp ~= nil then m_fillp() end
  end

  -- The SHEET is a file here, not memory. sget reads back 0 rather than
  -- refusing: a cart doing collision off sheet pixels will be wrong, and one
  -- doing an effect will just not see it.
  -- The sheet is a FILE here, not memory -- but a cart that reads it back
  -- needs to read what it wrote. `__p8_sheet` is baked in (only for carts
  -- that call these), so sget sees the real art and sset is visible to sget.
  -- What sset is NOT visible to is spr(), which keeps drawing the imported
  -- sheet; that is the part the report calls an approximation.
  local sheet = __p8_sheet
  local HEXD = "0123456789abcdef"
  function sget(x, y)
    if sheet == nil then return 0 end
    x, y = fl(x), fl(y)
    if x < 0 or x > 127 or y < 0 or y > 127 then return 0 end
    return tonumber(string.sub(sheet[y + 1], x + 1, x + 1), 16) or 0
  end
  function sset(x, y, c)
    if sheet == nil then return end
    x, y = fl(x), fl(y)
    if x < 0 or x > 127 or y < 0 or y > 127 then return end
    c = fl(c or 6) % 16
    local row = sheet[y + 1]
    sheet[y + 1] = string.sub(row, 1, x) .. string.sub(HEXD, c + 1, c + 1)
                   .. string.sub(row, x + 2)
  end
  if m_sget ~= nil then
    -- The console's own sheet verbs (SPEC.md 7.1, 2026-09): what sset writes
    -- is what spr draws, and the baked copy above is only for older hosts.
    function sget(x, y) return m_sget(fl(x), fl(y)) end
    function sset(x, y, c) m_sset(fl(x), fl(y), fl(c == nil and p8_pen or c)) end
  end
  if __moy_poke ~= nil then
    -- The sheet IS memory here (0x0000..0x1fff, two pixels a byte), so an
    -- sset is what spr() draws next frame -- the approximation above is gone.
    local cpeek, cpoke = __moy_peek, __moy_poke
    function sget(x, y)
      x, y = fl(x), fl(y)
      if x < 0 or x > 127 or y < 0 or y > 127 then return 0 end
      local b = cpeek(y * 64 + (x >> 1))
      if x & 1 == 1 then return b >> 4 end
      return b & 15
    end
    function sset(x, y, c)
      x, y = fl(x), fl(y)
      if x < 0 or x > 127 or y < 0 or y > 127 then return end
      c = fl(c or 6) % 16
      local a = y * 64 + (x >> 1)
      local b = cpeek(a)
      if x & 1 == 1 then cpoke(a, (b & 0x0f) | (c << 4))
      else cpoke(a, (b & 0xf0) | c) end
    end
  end
  -- There is no ROM to re-read, and no terminal behind a cart.
  if __moy_reload ~= nil then
    -- The cart ROM is the seeded memory image on a host with the C map, so
    -- reload() really does bring the art, the map and the flags back (and
    -- a partial reload(dst, src, len) fetches a slice, which is how a cart
    -- streams tracks or levels out of its own map data). cstore() writes the
    -- snapshot only; nothing persists to the cart file.
    local creload, ccstore = __moy_reload, __moy_cstore
    function reload(dst, src, len) creload(dst or 0, src or 0, len) end
    function cstore(dst, src, len) ccstore(dst or 0, src or 0, len) end
  else
    function reload(...) end
    function cstore(...) end
  end
  function printh(...) end
  function extcmd(...) end
  -- The console calls _draw() for you, so there is nothing to wait for.
  function flip() end
  function holdframe() end
  -- p8's persistent draw colour, and its print cursor.
  function color(c) p8_pen = fl(c or 6) & 0x8f end
  function cursor(x, y, c) p8_cx, p8_cy = fl(x or 0), fl(y or 0)
    if c ~= nil then p8_pen = fl(c) & 0x8f end end

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
  if __moy_poke ~= nil then
    -- Memory is the truth on a host that has it: a cell poked at 0x2000 (or
    -- 0x1000, the shared rows) is the cell mget reads and map() draws.
    local cpeek, cpoke = __moy_peek, __moy_poke
    local function maddr(x, y)
      if y < 32 then return 0x2000 + y * 128 + x end
      return 0x1000 + (y - 32) * 128 + x
    end
    function mget(x, y)
      x, y = mfloor(x or 0), mfloor(y or 0)
      if x < 0 or x > 127 or y < 0 or y > 63 then return 0 end
      return cpeek(maddr(x, y))
    end
    function mset(x, y, v)
      x, y = mfloor(x or 0), mfloor(y or 0)
      if x < 0 or x > 127 or y < 0 or y > 63 then return end
      cpoke(maddr(x, y), v or 0)
    end
  end
  function fget(n, f)
    local v = gff[mfloor(n or 0)] or 0
    if f == nil then return v end
    return (v >> f) & 1 == 1
  end
  function fset(n, f, v)
    n = mfloor(n or 0)
    if v == nil then gff[n] = mfloor(f or 0) & 0xff return end
    local bit = 1 << (mfloor(f) & 7)
    gff[n] = v and ((gff[n] or 0) | bit) or ((gff[n] or 0) & ~bit)
  end
  if m_fget ~= nil then
    -- The console carries the flags (flags.moyflags, SPEC.md 3.5): fget and
    -- fset are its own, and what fset writes is what map(..., layers) sees.
    function fget(n, f)
      n = fl(n)
      if f == nil then return m_fget(n) end
      return m_fget(n, fl(f))
    end
    function fset(n, f, v)
      n = fl(n)
      if v == nil then m_fset(n, fl(f)) else m_fset(n, fl(f), v and true or false) end
    end
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
    if m_fget ~= nil then
      -- The console's own masked walk (SPEC.md 7.2 layers, 2026-09), in C
      -- on every libmoy host; the Lua loop below is for one that predates it.
      m_map(celx, cely, cw, ch, sx, sy, -1, 1, mask)
      return
    end
    for cy = 0, ch - 1 do
      local rowb = (cely + cy) * 128
      for cx = 0, cw - 1 do
        local tile = p8map[rowb + celx + cx + 1] or 0
        if tile > 0 and (mask == 0
                         or ((gff[tile] or 0) & mask) ~= 0) then
          m_spr(tile, sx + cx * 8, sy + cy * 8, -1, 1, 0)
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
  -- A cart picks its own rate by which one it DEFINES: `_update60` runs the
  -- game at 60, `_update` at 30, and a cart that defines both means the 60 (so
  -- does PICO-8). The choice cannot be made when this shim loads -- the cart's
  -- own functions are defined below it -- so it is locked on the first frame,
  -- which is also the first moment it can be known.
  --
  -- Reading only `p8_update` was not a slow cart, it was a DEAD one: a
  -- 60fps cart's update never ran at all, so it drew its first frame forever
  -- and answered no input. `bunnysurvivor` is one, and "couldn't send any
  -- input" is exactly what that looks like from the outside.
  -- The RATE is locked once; the FUNCTION is looked up every tick. p8 reads
  -- `_update` fresh each frame, and a cart may reassign it -- a scene machine
  -- swapping its update for the next screen is ordinary p8. Caching the
  -- function on frame one froze any cart that had not defined it yet, or that
  -- replaced it later, with no error to show for it.
  local locked = false
  -- An edge stays visible for the WHOLE cart frame, _draw included: PICO-8's
  -- btnp() answers the same in _draw as it did in _update, and petal quest's
  -- title starts from a btnp() inside its draw. So a consumed edge is cleared
  -- at the top of the NEXT console frame, not the moment the tick returns --
  -- and an edge latched on a frame with no tick (a host faster than 30Hz)
  -- still waits for one.
  local consumed = false
  function _update(dt)
    if not locked and (p8_update60 or p8_update) then
      locked = true
      if p8_update60 then
        P8_DT = 1 / 60
        EPS = P8_DT * 0.02
      end
    end
    if consumed then
      for i = 0, 5 do pending[i] = false end
      consumed = false
    end
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
      for i = 0, 5 do                            -- hold length, in CART ticks
        hold[i] = m_btn(BTN[i]) and (hold[i] or 0) + 1 or 0
      end
      if n > 1 then                              -- a catch-up tick: the first
        for i = 0, 5 do pending[i] = false end   -- in this frame took the edge
      end
      local tick = p8_update60 or p8_update
      if tick then tick() end
      consumed = true
      ticked = true
    end
    if n >= MAX_CATCHUP then acc = 0 end         -- write off what cannot be paid
  end
  function _draw()
    if ticked and p8_draw then
      -- the console resets camera/clip/pal/palt after every cart frame;
      -- re-park the p8 camera and restore p8's default transparency (colour
      -- 0) so a cart that trusts persistent draw state gets PICO-8's.
      camera()
      p8_palt_default()
      spal_apply()
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


def data_tables_lua(sections, want_sheet=False):
    """The cart's baked-in data, emitted BEFORE the shim so it can close over
    it as upvalues.

    `want_sheet` bakes the 128x128 sprite sheet in as text, which costs ~16KB
    of main.lua -- so it is only done for a cart that actually calls sget or
    sset. It is not a luxury for those: `terra` UNPACKS its graphics into the
    sheet with sset at load and then reads them back to generate its world, so
    with sset dropped its "scan down for solid ground" loop never terminated.
    Sixty million mget calls in, it was still looking.
    """
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
             "  -- A host that carries the flags on the console (SPEC.md 3.5)",
             "  -- but did not read flags.moyflags for this cart gets them",
             "  -- from here, once; a host that did reads the same bytes.",
             "  if fset ~= nil then",
             "    for i = 0, 255 do",
             "      if __p8_gff[i] ~= 0 then fset(i, __p8_gff[i]) end",
             "    end",
             "  end",
             "end",
              ""]
    if want_sheet:
        rows = (gfx_to_kgfx(sections.get("gfx", [])) or "").split("\n")
        rows = [(r + "0" * 128)[:128] for r in rows][:128]
        while len(rows) < 128:
            rows.append("0" * 128)
        lines.append("-- __gfx__ as READABLE pixels, for sget/sset. spr() still")
        lines.append("-- draws sprites.moygfx, so an sset is visible to sget and")
        lines.append("-- not to the screen.")
        lines.append("__p8_sheet = {")
        for r in rows:
            lines.append('  "%s",' % r)
        lines.append("}")
        lines.append("")
    return "\n".join(lines)


# Every global the shim defines for the game code. Order is cosmetic; the
# emitted block groups them a line at a time.
P8_API = ("btn btnp camera sin cos flr abs min max sqrt atan2 spr rectfill "
          "rect circfill circ print pal pset pget line sfx music menuitem "
          "add del all foreach count sub tostr sgn mid rnd mget fget map "
          # 2026-08-30: the gaps that were only ever a naming difference.
          "t time chr ord tonum split mset sspr "
          "oval ovalfill ceil srand deli unpack pack run "
          "cocreate coresume costatus yield coclose "
          # 2026-09-01: the compatibility layer -- these answer rather than
          # stopping the cart, and the report calls them approximations.
          "peek peek2 peek4 poke poke2 poke4 memcpy memset "
          "cartdata dget dset stat fillp sget sset fset "
          "reload cstore printh extcmd flip holdframe color cursor "
          "band bor bxor bnot shl shr rotl rotr").split()


# --------------------------------------------------------------------------
# The import verdict: what a cart will do here, decided from its source
# --------------------------------------------------------------------------
#
# Three answers, in order of how much a person needs to hear them:
#
#   "refused"  the cart cannot run on this console -- it loads other carts,
#              packs its data in 16.16 fixed-point bit tricks, spins on
#              flip(), or draws in a screen mode there is no screen for.
#              A host refuses the import and says why, so nothing lands on
#              a shelf that cannot start.
#   "gaps"     it runs, and something will look or sound different: a
#              pause-menu entry, a machine counter reading zero, a sound
#              synthesised by poking sfx RAM. Import it, badge it, list them.
#   "runs"     nothing the scan knows about is in the way.
#
# Everything here is a pattern over the CONVERTED code, not an execution: a
# dry run can tell you a cart errored on frame one, and this cannot -- but
# this runs in a millisecond on every tier, with the cart's own words as the
# reason. Each rule exists because a corpus cart hit it (PICO8.md's table
# says which). A rule that matches nothing in the corpus was not added.

def _strip_lua(body):
    """The code with comments and string CONTENTS gone, so a pattern never
    fires on prose. Strings keep their quotes (a call shape survives), long
    brackets and long comments are removed whole.

    Appends SLICES, never characters: this runs on MicroPython in the browser
    importer, where a list with one entry per byte of a 100 KB cart is the
    allocation that fails."""
    out = []
    i = 0
    n = len(body)
    keep = 0                              # start of the slice not yet emitted
    while i < n:
        ch = body[i]
        if ch == "-" and body.startswith("--", i):
            out.append(body[keep:i])
            j = i + 2
            k = j
            while k < n and body[k] == "=":
                k += 1
            if body[j:j + 1] == "[" and body[k:k + 1] == "[":
                close = "]" + "=" * (k - j - 1) + "]"
                e = body.find(close, k)
                i = n if e < 0 else e + len(close)
            else:
                e = body.find("\n", j)
                i = n if e < 0 else e
            keep = i
            continue
        if ch == '"' or ch == "'":
            out.append(body[keep:i])
            j = i + 1
            while j < n and body[j] != ch and body[j] != "\n":
                if body[j] == "\\":
                    j += 1
                j += 1
            out.append(ch + ch)
            i = j + 1
            keep = i
            continue
        if ch == "[" and body[i + 1:i + 2] in ("[", "="):
            k = i + 1
            while k < n and body[k] == "=":
                k += 1
            if body[k:k + 1] == "[":
                out.append(body[keep:i])
                close = "]" + "=" * (k - i - 1) + "]"
                e = body.find(close, k)
                out.append('""')
                i = n if e < 0 else e + len(close)
                keep = i
                continue
        i += 1
    out.append(body[keep:n])
    return "".join(out)


def _call_sites(code, name):
    """The index just past the `(` of every `name(` call in `code`, at a word
    boundary and not a method or field (`x:name(`, `t.name(`). Hand-walked
    like _calls_verb, and for the same reason: this file runs on MicroPython,
    whose `re` has no lookbehind and no word boundary."""
    out = []
    i = 0
    n = len(name)
    while True:
        j = code.find(name, i)
        if j < 0:
            return out
        i = j + n
        prev = code[j - 1] if j > 0 else " "
        k = j + n
        while k < len(code) and code[k] in " \t":
            k += 1
        if not (_ident_char(prev) or prev in "._:") and code[k:k + 1] == "(":
            out.append(k + 1)


def _call_args(code, at):
    """The argument list of the call whose `(` precedes `at`, split at the
    commas of its own level; the text of each, stripped."""
    depth = 0
    args = []
    start = at
    i = at
    while i < len(code):
        ch = code[i]
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            if depth == 0:
                args.append(code[start:i].strip())
                return args
            depth -= 1
        elif ch == "," and depth == 0:
            args.append(code[start:i].strip())
            start = i + 1
        i += 1
    return args


def _num(text):
    """A numeric literal's value, or None: `0x5f2c`, `24364`."""
    t = text.strip().lower()
    try:
        if t.startswith("0x"):
            return int(t[2:].split(".")[0], 16)
        if t and t.replace(".", "", 1).isdigit():
            return int(float(t))
    except ValueError:
        pass
    return None


def _hex_addr_calls(code, verb):
    """Every first argument of `verb(` that is a number, as an int."""
    out = []
    for at in _call_sites(code, verb):
        args = _call_args(code, at)
        if args:
            v = _num(args[0])
            if v is not None:
                out.append(v)
    return out


def _shifts_by_16(code):
    """A `>> 16`, `<< 16`, `shr(x, 16)`, `shl(x, 16)` or `lshr(x, 16)`."""
    for op in (">>", "<<"):
        i = 0
        while True:
            j = code.find(op, i)
            if j < 0:
                break
            i = j + 2
            k = i
            while k < len(code) and code[k] in " \t":
                k += 1
            if code[k:k + 2] == "16" and not (code[k + 2:k + 3].isdigit()
                                             or code[k + 2:k + 3] == "."):
                return True
    for fn in ("shr", "shl", "lshr"):
        for at in _call_sites(code, fn):
            args = _call_args(code, at)
            if len(args) >= 2 and args[1] == "16":
                return True
    return False


_BIT_FNS = ("band", "bor", "bxor", "bnot", "shl", "shr", "lshr", "rotl", "rotr")


def _frac_hex_bits(line):
    """A fractional hex constant on a line that also does bit arithmetic."""
    if not re.search(r"0x[0-9a-fA-F]*\.[0-9a-fA-F]+", line):
        return False
    if "&" in line or "|" in line or "~" in line or "<<" in line or ">>" in line:
        return True
    for fn in _BIT_FNS:
        if _call_sites(line, fn):
            return True
    return False


def classify_body(body):
    """-> {"verdict": "runs"|"gaps"|"refused", "reasons": [...]}, from the
    CONVERTED cart code (what p8_lua_to_lua54 emits). Reasons are the
    sentences a host shows; the refusing ones come first."""
    code = _strip_lua(body)
    lines = code.split("\n")
    refused = []
    gaps = []

    # -- will not run --------------------------------------------------------
    if any(ln.lstrip().startswith("#include") for ln in lines):
        refused.append("it #includes another file, which does not travel with the cart")
    # PICO-8's load() swaps carts; a cart's own `board:load()` is not it.
    if not _defines_function(body, "load") and _call_sites(code, "load"):
        refused.append("it loads other carts (load) -- a multi-cart game; this console "
                       "runs one cart at a time")
    # The 16.16 class. A shift by sixteen is how a cart reads the integer
    # half of a packed 32-bit value, and it is FATAL when the cart is
    # unpacking its data that way (celeste 2's px9, nimudazus's bytecode) --
    # a decoder is a peek2/peek4/ord next to the shifts. Without one it is a
    # hash or a mask that comes out wrong, which is a gap, not a death.
    shifts = _shifts_by_16(code)
    decoder = bool(_call_sites(code, "peek2") or _call_sites(code, "peek4")
                   or _call_sites(code, "ord"))
    if shifts and decoder:
        refused.append("it unpacks its data with 16.16 fixed-point shifts that this "
                       "console's float numbers cannot reproduce")
    elif shifts:
        gaps.append("it shifts numbers by 16 bits, which loses the fixed-point precision "
                    "PICO-8 has; a hash or a mask may come out wrong")
    if any(_frac_hex_bits(ln) for ln in lines):
        gaps.append("it does bit arithmetic on fractional hex constants (0x0.0001 and "
                    "the like); those bits are lost on floats, so a packed flag may read wrong")
    has_loop = (_defines_function(body, "p8_update") or _defines_function(body, "p8_update60")
                or _defines_function(body, "p8_draw"))
    if _call_sites(code, "flip") and not _defines_function(body, "flip"):
        if not has_loop:
            refused.append("it runs its own loop on flip() instead of _update/_draw, "
                           "and the console owns the frame")
        else:
            gaps.append("flip() does nothing here; the console draws each frame itself")
    for at in _call_sites(code, "poke"):
        args = _call_args(code, at)
        if len(args) >= 2 and _num(args[0]) == 0x5f2c:
            v = _num(args[1])
            if v is not None and v & 7:
                refused.append("it switches to a 64x64 or rotated screen mode (poke 0x5f2c) "
                               "this console has no screen for")
                break

    # -- runs, with gaps -----------------------------------------------------
    if _call_sites(code, "menuitem") and not _defines_function(body, "menuitem"):
        gaps.append("its pause-menu entries (menuitem) are not shown; the console owns the menu")
    stat_sites = _call_sites(code, "stat")
    stat_ids = _hex_addr_calls(code, "stat")
    if stat_sites and not _defines_function(body, "stat") and (
            len(stat_ids) < len(stat_sites) or any(not 32 <= i <= 36 for i in stat_ids)):
        gaps.append("stat() reads zero: clock, CPU and audio counters are not measured")
    regs = []
    for v in ("poke", "poke2", "poke4", "memcpy", "memset"):
        regs.extend(_hex_addr_calls(code, v))
    if any(0x3100 <= a < 0x4300 for a in regs):
        gaps.append("it writes sound data into sfx/music memory at runtime; the imported "
                    "sounds play instead")
    if any(a == 0x5f2d for a in regs) or any(32 <= i <= 36 for i in stat_ids):
        gaps.append("it reads the mouse; there is no pointer in a PICO-8 port's input")
    if any(a in (0x5f54, 0x5f55) for a in regs):
        gaps.append("it remaps the sheet or screen (0x5f54/0x5f55); the remap is remembered, "
                    "not applied")
    if any(a in (0x5f5e, 0x5f5f) for a in regs):
        gaps.append("it uses bitplane masks (0x5f5e); the mask is remembered, not applied")
    if any(0x5600 <= a < 0x5e00 for a in regs):
        gaps.append("it installs a custom font (0x5600); text draws in the system font")
    if any(len(_call_args(code, at)) >= 3 for at in _call_sites(code, "sfx")):
        gaps.append("sfx() with an offset or length plays the whole sound")
    if _call_sites(code, "cstore"):
        gaps.append("cstore() writes a copy in memory; nothing is saved back to the cart file")
    if _call_sites(code, "serial"):
        gaps.append("serial() has nothing on the other end")
    if any(len(a) >= 3 and a[2] == "2"
           for a in (_call_args(code, at) for at in _call_sites(code, "pal"))):
        gaps.append("the secondary palette (pal(..., 2)) is treated as the draw palette")

    if refused:
        return {"verdict": "refused", "reasons": refused + gaps}
    if gaps:
        return {"verdict": "gaps", "reasons": gaps}
    return {"verdict": "runs", "reasons": []}


def classify(sections):
    """The verdict for parsed cart `sections` (p8_import.read_p8), computed
    BEFORE anything is written, so a host can refuse without a trace."""
    return classify_body(p8_lua_to_lua54(sections.get("lua", [])))


def _calls_verb(body, name):
    """`name(` in the body at a word boundary."""
    i = 0
    n = len(name)
    while True:
        j = body.find(name, i)
        if j < 0:
            return False
        i = j + n
        prev = body[j - 1] if j > 0 else " "
        k = j + n
        while k < len(body) and body[k] in " \t":
            k += 1
        if not (_ident_char(prev) or prev in "._:") and body[k:k + 1] == "(":
            return True


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


def build_manifest(title, icon=None, fps=30):
    # The spec manifest (SPEC.md 3.1). `fps` is the cart's LOGIC rate, and a p8
    # cart picks it by which lifecycle it defines: _update60 means 60, _update
    # means 30. Declaring 30 for every cart was wrong in a way that reached the
    # host -- `Workstation.frame_cap_fps` reads this field, so a 60fps cart was
    # capped to a 30Hz loop while the shim still wanted 60Hz logic, which is
    # two cart ticks inside one console frame. That is exactly the shape of the
    # doubled btnp edge fixed on 2026-09-01.
    # "ported_from" is an unrecognised field; the spec requires hosts to ignore
    # it (3.1).
    man = {
        "format": "moy-1",
        "title": title,
        "version": 1,
        "main": "main.lua",
        "fps": fps,
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
        # SPEC.md 2.2: the cart's own 64-entry table -- PICO-8's sixteen, then
        # its sixteen SECRET colours at 16-31 (pal(c, 128 + i) in the shim
        # lands there), then the base sixteen again to fill the table. A
        # ported cart never reaches past 31.
        "palette": P8_PALETTE,
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
                 "input", "safe_to_share", "ported_from", "palette", "icon")

# PICO-8's palette (its base sixteen are SPEC.md 2's 0-15 byte for byte) and
# its secret sixteen, as the manifest ships them.
P8_PALETTE = (
    "000000 1D2B53 7E2553 008751 AB5236 5F574F C2C3C7 FFF1E8 "
    "FF004D FFA300 FFEC27 00E436 29ADFF 83769C FF77A8 FFCCAA "
    "291814 111D35 422136 125359 742F29 49333B A28879 F3EF7D "
    "BE1250 FF6C24 A8E72E 00B543 065AB5 754665 FF6E59 FF9D81 "
    "000000 1D2B53 7E2553 008751 AB5236 5F574F C2C3C7 FFF1E8 "
    "FF004D FFA300 FFEC27 00E436 29ADFF 83769C FF77A8 FFCCAA "
    "000000 1D2B53 7E2553 008751 AB5236 5F574F C2C3C7 FFF1E8 "
    "FF004D FFA300 FFEC27 00E436 29ADFF 83769C FF77A8 FFCCAA").split()


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
    "music": m, "verdict": classify_body(...)}`. Takes SECTIONS rather than a path because a console was handed
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
    want_sheet = _calls_verb(body, "sget") or _calls_verb(body, "sset")
    # Data tables BEFORE the shim, so the shim captures them as upvalues.
    # Written as PIECES: joined into one string first, main.lua is ~100 KB
    # held twice, which is the allocation the browser's MicroPython refused.
    _write(out_dir, "main.lua",
           [header, data_tables_lua(sections, want_sheet), "\n", shim, "\n",
            localization_lua(body), body])
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

    # flags.moyflags (SPEC.md 3.5): __gff__ is its first 256 tiles, byte for
    # byte, so the console's own fget/fset/map(..., layers) read the real
    # thing; the Lua table below the shim stays for a host without them.
    gff = gff_hex(sections)
    if any(ch != "0" for ch in gff):
        gff = (gff + "0" * 1024)[:1024]
        _write(out_dir, "flags.moyflags",
               "\n".join(gff[i:i + 64] for i in range(0, 1024, 64)) + "\n")
        written.append("flags.moyflags")

    sounds, n_sfx, n_music = sfx_music_to_sounds(
        sections.get("sfx", []), sections.get("music", []))
    if sounds:
        _write(out_dir, "sounds.json", json.dumps(sounds))
        written.append("sounds.json")
    else:
        n_sfx = n_music = 0          # nothing was written, so nothing counted

    _write(out_dir, "manifest.json",
           manifest_text(build_manifest(
               title, icon_tile(kgfx),
               60 if _defines_function(body, "p8_update60") else 30)))
    written.append("manifest.json")
    return {"files": sorted(written), "sfx": n_sfx, "music": n_music,
            "verdict": classify_body(body)}


def port(p8_path, out_dir, title=None, crop=(0, 0), force=False):
    """Port a cart file to `out_dir`; returns the writer's summary.

    A REFUSED verdict (classify) writes nothing and raises SystemExit with
    the reasons, unless `force`: a host that lands such a cart on its shelf
    lands one that cannot start."""
    sections = read_p8(p8_path)      # text .p8 OR the BBS .p8.png
    title = title or _title_from(sections, p8_path)
    verdict = classify(sections)
    if verdict["verdict"] == "refused" and not force:
        raise SystemExit("refused: %s will not run on this console:\n  - %s\n"
                         "(--force ports it anyway)"
                         % (title, "\n  - ".join(verdict["reasons"])))
    summary = port_sections(sections, out_dir, title, crop)
    summary["out_dir"] = out_dir
    return summary


def verdict_lines(verdict):
    """The verdict as the lines a host prints or shows beside the cart."""
    v = verdict["verdict"]
    if v == "runs":
        return ["runs: nothing the importer knows about is in the way"]
    head = ("will not run:" if v == "refused" else "runs with gaps:")
    return [head] + ["  - " + r for r in verdict["reasons"]]


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
        print("usage: p8_lua_port.py cart.p8 out_dir [--title NAME] [--zoom [T,B]]"
              " [--force]")
        return 2
    summary = port(args[0], args[1], title, crop, force="--force" in argv)
    out = summary["out_dir"]
    vh = 128 - crop[0] - crop[1]
    for line in verdict_lines(summary["verdict"]):
        print("  " + line)
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
