"""The Code tab's colour runs as runtime/code_layer.py found them in Python
before they crossed into C (native/moy_ui/moy_ui_text.c): the oracle the
C's runs are held to (tests/test_text_view.py), as tests/ui_twin.py is the
toolkit's."""

from runtime.code_layer import (_HL_TEXT, _HL_KEYWORD, _HL_STRING, _HL_NUMBER, _HL_COMMENT,
                                _HL_BUILTIN, _HL_KEYWORDS, _HL_LUA_KEYWORDS, _HL_BUILTINS)

_HL_CLASS = bytes((1 if (c == 95 or 65 <= c <= 90 or 97 <= c <= 122) else
                   2 if 48 <= c <= 57 else 3 if c in (34, 39) else
                   4 if c == 35 else 5 if c == 45 else 0) for c in range(128))
# Each language's word -> its colour, one lookup a name.
_HL_WORDS = ({}, {})
for _w in _HL_BUILTINS:
    _HL_WORDS[0][_w] = _HL_BUILTIN
    _HL_WORDS[1][_w] = _HL_BUILTIN
for _w in _HL_KEYWORDS:
    _HL_WORDS[0][_w] = _HL_KEYWORD
for _w in _HL_LUA_KEYWORDS:
    _HL_WORDS[1][_w] = _HL_KEYWORD


def _highlight_runs(line, lua=False):
    """`_highlight` as the memo keeps it: a flat list of (start, end, colour,
    text) for each stretch of one colour, scanned a token at a time over the
    line's bytes (`_highlight` walks it a character at a time and a second
    pass groups the colours). A line with a non-ASCII character takes that
    walk."""
    b = line.encode()
    n = len(b)
    if n != len(line):
        return _runs_of(line, _highlight(line, lua))
    cls = _HL_CLASS
    words = _HL_WORDS[1 if lua else 0]
    out = []
    pc = -1
    ps = 0
    i = 0
    while i < n:
        k = cls[b[i]] if b[i] < 128 else 0
        if k == 1:
            j = i + 1
            while j < n and b[j] < 128 and 0 < cls[b[j]] < 3:
                j += 1
            c = words.get(line[i:j], _HL_TEXT)
        elif k == 2:
            j = i + 1
            while j < n and (48 <= b[j] <= 57 or b[j] == 46 or b[j] == 120):
                j += 1
            c = _HL_NUMBER
        elif k == 3:
            q = b[i]
            j = i + 1
            while j < n:
                x = b[j]
                if x == 92 and j + 1 < n:
                    j += 2
                    continue
                j += 1
                if x == q:
                    break
            c = _HL_STRING
        elif (k == 4 and not lua) or (k == 5 and lua and i + 1 < n and b[i + 1] == 45):
            j = n
            c = _HL_COMMENT
        else:
            j = i + 1
            while j < n and (b[j] >= 128 or cls[b[j]] == 0):
                j += 1
            c = _HL_TEXT
        if c != pc:
            if pc >= 0:
                out += (ps, i, pc, line[ps:i])
            pc = c
            ps = i
        i = j
    if pc >= 0:
        out += (ps, n, pc, line[ps:n])
    return out


def _runs_of(line, cols):
    """Per-character colours as `_highlight_runs`' flat runs."""
    runs = []
    n = len(line)
    i = 0
    while i < n:
        c = cols[i]
        j = i + 1
        while j < n and cols[j] == c:
            j += 1
        runs += (i, j, c, line[i:j])
        i = j
    return runs



def _is_alpha(ch):
    return ch == "_" or ("a" <= ch <= "z") or ("A" <= ch <= "Z")


def _highlight(line, lua=False):
    """The Code tab's highlight as Python had it before the C took it: a
    list of palette indices, one per character of `line` (#24).
    Hand-rolled scanner -- no regex/tokenize, so it runs under MicroPython.
    `lua` (#67 Phase 5) switches the comment marker (`--`; `#` is Lua's length
    operator, NOT a comment) and the keyword set; strings/numbers/cart-verb
    builtins scan identically in both languages."""
    n = len(line)
    out = [_HL_TEXT] * n
    i = 0
    keywords = _HL_LUA_KEYWORDS if lua else _HL_KEYWORDS
    while i < n:
        ch = line[i]
        if (lua and ch == "-" and i + 1 < n and line[i + 1] == "-") or \
                (not lua and ch == "#"):       # comment to end of line
            while i < n:
                out[i] = _HL_COMMENT
                i += 1
            break
        if ch == '"' or ch == "'":             # string literal (single line)
            q = ch
            out[i] = _HL_STRING
            i += 1
            while i < n:
                out[i] = _HL_STRING
                if line[i] == "\\" and i + 1 < n:   # escape: consume next char too
                    i += 1
                    out[i] = _HL_STRING
                    i += 1
                    continue
                if line[i] == q:
                    i += 1
                    break
                i += 1
            continue
        if "0" <= ch <= "9":                   # number literal
            while i < n and (("0" <= line[i] <= "9") or line[i] == "." or line[i] == "x"):
                out[i] = _HL_NUMBER
                i += 1
            continue
        if _is_alpha(ch):                      # identifier / keyword / builtin
            j = i
            while j < n and (_is_alpha(line[j]) or "0" <= line[j] <= "9"):
                j += 1
            word = line[i:j]
            if word in keywords:
                cl = _HL_KEYWORD
            elif word in _HL_BUILTINS:
                cl = _HL_BUILTIN
            else:
                cl = _HL_TEXT
            while i < j:
                out[i] = cl
                i += 1
            continue
        i += 1                                 # operator / punctuation / space
    return out


def _highlight_json(line):
    """`_highlight`'s answer for a line of JSON (the theme mode's Config): a
    key (a string a colon follows) as a builtin, a value's string as a
    string, a number as a number, true, false and null as keywords."""
    n = len(line)
    out = [_HL_TEXT] * n
    i = 0
    while i < n:
        ch = line[i]
        if ch == '"':
            j = i + 1
            while j < n and line[j] != '"':
                j += 2 if line[j] == "\\" else 1
            j = min(n, j + 1)
            k = j
            while k < n and line[k] == " ":
                k += 1
            cl = _HL_BUILTIN if k < n and line[k] == ":" else _HL_STRING
            while i < j:
                out[i] = cl
                i += 1
            continue
        if "0" <= ch <= "9" or (ch == "-" and i + 1 < n and "0" <= line[i + 1] <= "9"):
            while i < n and (("0" <= line[i] <= "9") or line[i] in "-+.eE"):
                out[i] = _HL_NUMBER
                i += 1
            continue
        if _is_alpha(ch):
            j = i
            while j < n and _is_alpha(line[j]):
                j += 1
            if line[i:j] in ("true", "false", "null"):
                while i < j:
                    out[i] = _HL_KEYWORD
                    i += 1
            i = j
            continue
        i += 1
    return out
