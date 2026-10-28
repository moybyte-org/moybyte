"""The Code tab's rows as runtime/code_layer.py drew them in Python before
they crossed into C (native/moy_ui/moy_ui_text.c): the oracle the TextView
is held to byte for byte (tests/test_text_view.py), as tests/ui_twin.py is
the toolkit's.

`OldRows` carries the old methods; `install(layer)` binds them onto one
CodeLayer instance, so a console draws its Code tab (and theme Config, which
is the same class) as it did."""

import types

from runtime import ui_draw as _ui_draw
from runtime.code_layer import (_HL_TEXT, _HL_KEYWORD, _HL_STRING, _HL_NUMBER, _HL_COMMENT,
                                _HL_BUILTIN, _HL_KEYWORDS, _HL_LUA_KEYWORDS, _HL_BUILTINS,
                                _highlight)

_FADE = 12
_FADE_ALPHA = (89, 153, 217)
_MATCH_ALPHA = 96
_HL_MEMO = 200

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



class OldRows:
    def draw_lines(self, lines, first, rect):
        """Lines of code read only in `rect` from line `first`, numbered, in
        the Code tab's mono font and colours: the desk's made code
        (`editor_parts.made`). The rows it shows."""
        ws = self.ws
        cv = ws.sys_canvas
        th = ws.theme_colors
        hl = self._hl_map()
        lay = ws.code_layout
        cell, lh = lay.cell, lay.lh
        x, y, w, h = rect
        gut = (len(str(len(lines))) + 1) * cell
        rows = max(1, h // lh)
        cols = max(1, (w - gut) // cell)
        was = _ui_draw.print_font(_ui_draw.font("mono")[0])
        cv.clip(x, y, w, h)
        try:
            for k in range(rows):
                n = first + k
                if n >= len(lines):
                    break
                ly = y + k * lh
                num = str(n + 1)
                cv.print(num, x + gut - (len(num) + 1) * cell, ly, th["ink_dim"], 1)
                self._draw_code_runs(lines[n][:cols], 0, cols, ly, x + gut, hl)
        finally:
            cv.clip()
            _ui_draw.print_font(was)
        return rows

    def _draw_code(self):
        # Responsive (#39 step 2): the code editor draws on the SYSTEM canvas at
        # native size, so a bigger panel shows more lines + wider columns and a
        # bigger font scales the text. All positions come from CodeLayout (verbatim
        # baseline at 320x240/1x). The editor's COLS/ROWS already track the layout.
        NAMES = self._NAMES
        ws = self.ws
        cv = ws.sys_canvas
        if ws.code_layout.symbols != self.wants_symbols():
            # The last input moved between touch and keys: the symbol keys
            # come or go, and the code takes their rows (D-17).
            ws._relayout_code()
        lay = ws.code_layout
        fs = lay.fs
        cell = lay.cell                          # on-screen char-cell width
        lh = lay.lh
        th = lay.text_h                          # the glyph box: caret, selection
        ed = ws.editor
        t = self._t = self._tones()
        cv.cls(t["bg"])                         # full-screen editor
        # The old title + RUN/SAVE/CLOSE top band is gone (Stage 4 rollout): the unified
        # bar (drawn after this in draw()) owns the top 18px -- PLAY runs (and persists,
        # #111), X exits, and the tab ladder switches views (persisting the outgoing tab
        # too). The text area already starts at y0 == 18 (below the bar), so the body is
        # fullscreen with no chrome of its own.
        # code area (horizontal scroll: columns [left, left+COLS))
        if ed is not None:
            self._apply_gutter(lay, ed)          # #89: narrow ed.COLS while the gutter is on
            gc = self._gutter_cols(ed)           # gutter width in cells (0 == off)
            tx0 = lay.x0 + gc * cell             # text origin (shifted right by the gutter)
            cols = ed.COLS
            vis = ed.visible_lines()
            errrow = ws.code_err_row
            # The current line on the theme's current mark with a `value`
            # bar at its left, and a line longer than the view fading out
            # over FADE pixels at its right.
            fx = tx0 + cols * cell - _FADE * fs
            for idx in range(len(vis)):
                y = self._y0(lay) + idx * lh
                absrow = ed.top + idx
                full = vis[idx]
                on_err = errrow is not None and absrow == errrow
                cur = absrow == ed.row
                fade = len(full) - ed.left > cols
                if cur:
                    self._current_line(cv, lay, y, lay.mark_x, fx if fade else None)
                if gc:                           # #89: line-number gutter (crash mark integrated)
                    num = str(absrow + 1)[-(gc - 1):]
                    cv.print(num, lay.x0 + (gc - 1 - len(num)) * cell, y,
                             NAMES["red"] if on_err else
                             (t["gutter_cur"] if cur else t["gutter"]), 1)
                elif on_err:                     # no gutter: the crash tick, on the mark's place
                    cv.rect(lay.mark_x, y, 2 * fs, th, NAMES["red"])
                if on_err:                       # inline error underline (#24)
                    cv.rect(tx0, y + th, cols * cell, fs, NAMES["red"])
                self._draw_selection(ed, absrow, y, tx0, cell, cols, th, t["sel"])
                self._draw_find_matches(ed, absrow, full, y, tx0, cell, cols, th)
                self._draw_code_runs(full, ed.left, cols, y, tx0, t.get("hl"))
                if on_err and ws.code_err:      # short reason after the code, if it fits
                    mcol = max(0, min(cols, len(full) - ed.left)) + 1
                    if mcol < cols - 2:
                        cv.print(ws.code_err[:cols - mcol],
                                 tx0 + mcol * cell, y, NAMES["red"], 1)
                if fade:
                    self._fade(cv, fx, y, lh, t["bg"], fs)
                    if cur:
                        self._current_line(cv, lay, y, fx, None)
                if absrow == ed.row:            # caret on the cursor's line
                    vcol = ed.col - ed.left
                    if 0 <= vcol <= cols:
                        # One scale wide, in the column before the cell: a
                        # mono face leaves its cells two blank columns between
                        # glyphs, and a caret filling both read as the next
                        # stem's bold (design review R3-24).
                        cv.rect(tx0 + vcol * cell - fs, y + fs, fs, th - 2 * fs, t["caret"])
        if lay.status_band is not None and ed is not None:
            self._draw_status(cv, lay, ed)
        self._draw_tools(lay, ed, t)             # #89: tools toggle + palette + find bar
        self._draw_symbols()

    def _current_line(self, cv, lay, y, x0, x1):
        """The current line's mark, the columns [x0, x1) of it (x1 None: to
        the area's right): the theme's current mark (a `bar` beside the line,
        as beside a row) laid over the WHOLE line from `mark_x` (inside the
        content inset, clear of the frame) and clipped to those columns, so
        a line drawn in two parts around its fade is one mark -- one outline,
        one plate -- and the `value` bar where the part starts at `mark_x`."""
        right = lay.cx + lay.cw
        left = lay.mark_x
        x1 = right if x1 is None else x1
        if x1 <= x0:
            return
        th = self.ws.theme_colors
        whole = x0 == left and x1 == right
        if not whole:
            cv.clip(x0, y, x1 - x0, lay.lh)
        _ui_draw.current(cv, th, (left, y, right - left, lay.lh), side=True, keep=True)
        if not whole:
            cv.clip()
        if x0 == left:
            cv.rect(x0, y, 2 * lay.fs, lay.lh, th["value"])

    def _fade(self, cv, x, y, h, bg, fs):
        """A line longer than the view fades into `bg` over the FADE pixels
        from x: three steps, each a band of `bg` at a rising alpha."""
        step = _FADE * fs // len(_FADE_ALPHA)
        for i in range(len(_FADE_ALPHA)):
            cv.shadow(x + i * step, y, step, h, 0, bg, _FADE_ALPHA[i], 0)

    def _runs(self, line):
        """Memoized per-line syntax highlight (#24) as its runs: a flat list
        of (start, end, colour, text) for each stretch of one colour, the
        stretch's own text kept with it so a frame prints the memo's strings
        and slices none (a slice is a new string on the heap, per stretch per
        line per frame). Lines recur every frame, so cache by text; a memo
        holds one language's colours, and a switch of language (a python
        project for a lua one) empties it.

        The bound is TWO generations, not a clear. Dragging through a long file
        walks past the bound, and emptying the memo there re-highlights the whole
        visible window inside ONE frame -- a felt hitch, mid-drag, on exactly the
        gesture the memo exists to make cheap. Retiring the older generation
        leaves the lines still on screen one lookup away and caps the memory at
        what the single larger bound it replaces held."""
        lang = self._lang()
        if lang != self._hl_lua:
            self._hl_lua = lang
            self._hl_cache = {}
            self._hl_old = {}
        runs = self._hl_cache.get(line)
        if runs is None:
            runs = self._hl_old.get(line)
            if runs is None:
                runs = (_runs_of(line, _highlight_json(line)) if lang == "json"
                        else _highlight_runs(line, lang == "lua"))
            if len(self._hl_cache) >= _HL_MEMO:
                self._hl_old = self._hl_cache
                self._hl_cache = {}
            self._hl_cache[line] = runs
        return runs

    def _draw_code_runs(self, line, left, cols, y, x0, hl):
        """Draw the columns [left, left+cols) of one code line as runs of
        same-colored text (#24), each through `hl` (the theme's tones) when
        it has one. On the SYSTEM canvas at the layout's char-cell width
        (8*fs), so it scales with the font. `x0` is the text origin (shifted
        right by the #89 line-number gutter). A run the window cuts is sliced;
        a whole one prints its memoized text."""
        cv = self.ws.sys_canvas
        cell = self.ws.code_layout.cell
        runs = self._runs(line)
        right = left + cols
        for k in range(0, len(runs), 4):
            a = runs[k]
            b = runs[k + 1]
            if b <= left:
                continue
            if a >= right:
                break
            text = runs[k + 3]
            if a < left or b > right:
                if a < left:
                    a = left
                if b > right:
                    b = right
                text = line[a:b]
            cl = runs[k + 2]
            cv.print(text, x0 + (a - left) * cell, y,
                     hl.get(cl, cl) if hl else cl, 1)

    def _visible_span(self, ed, s, e, cols):
        """Clip a column span [s, e) to the horizontally-scrolled visible window,
        returning the visible (start_col, end_col) or None when it's off-screen."""
        vs = s if s > ed.left else ed.left
        we = ed.left + cols
        ve = e if e < we else we
        if ve <= vs:
            return None
        return vs, ve

    def _draw_selection(self, ed, absrow, y, tx0, cell, cols, h, color):
        """Highlight the selected columns on one visible row (#89): a filled tint
        behind the text (the runs redraw on top). Multi-row selections fill full
        lines between the endpoints."""
        b = ed.selection_bounds()
        if b is None:
            return
        r0, c0, r1, c1 = b
        if absrow < r0 or absrow > r1:
            return
        s = c0 if absrow == r0 else 0
        e = c1 if absrow == r1 else len(ed.lines[absrow])
        span = self._visible_span(ed, s, e, cols)
        if span is None:
            return
        vs, ve = span
        cv = self.ws.sys_canvas
        cv.rect(tx0 + (vs - ed.left) * cell, y, (ve - vs) * cell, h, color)

    def _draw_find_matches(self, ed, absrow, line, y, tx0, cell, cols, h):
        """Tint every occurrence of the find query on one visible row (#89),
        under the code: the current one, at the caret, gets a bar beneath."""
        q = self._find_q
        if not (self._find_open and q):
            return
        hay = line.lower() if self._find_ci else line
        qq = q.lower() if self._find_ci else q
        L = len(qq)
        cv = self.ws.sys_canvas
        value = self.ws.theme_colors["value"]
        bar = max(1, h // 7)
        start = 0
        while True:
            i = hay.find(qq, start)
            if i < 0:
                break
            span = self._visible_span(ed, i, i + L, cols)
            if span is not None:
                vs, ve = span
                mx, mw = tx0 + (vs - ed.left) * cell, (ve - vs) * cell
                cv.shadow(mx, y, mw, h, 0, value, _MATCH_ALPHA, 0)
                if absrow == ed.row and i == ed.col:
                    cv.rect(mx, y + h - bar, mw, bar, value)
            start = i + (L if L > 0 else 1)

    # The tool/find buttons ARE `ui.chip`s (Phase 3d): the private `_panel_btn`
    # copy is gone. What stays here is only the chip's FACE -- the part ui.py is
    # deliberately blind to -- handed to `chip` as its `glyph_draw` callback, the
    # same seam `game_icon_btn` already uses to keep the glyph vocabulary out of
    # the leaf.


_NAMES = ("draw_lines", "_draw_code", "_current_line", "_fade", "_runs", "_draw_code_runs",
          "_visible_span", "_draw_selection", "_draw_find_matches")


def install(layer):
    """Draw `layer`'s rows as the Python did."""
    layer._hl_cache = {}
    layer._hl_old = {}
    layer._hl_lua = None
    for name in _NAMES:
        setattr(layer, name, types.MethodType(getattr(OldRows, name), layer))
