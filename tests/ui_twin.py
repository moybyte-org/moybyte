"""The oracle for native/moy_ui (docs/kernel_toolkit_2026-10.md §7): the
toolkit's Python bodies as they were before the crossing -- runtime/ui.py's
style resolution, widgets, Hits, ScrollRegion and DragTap -- kept here as
tests/spine_twin.py keeps the spine's. The fuzz in
tests/test_ui_twin.py draws every case through this and through the shipped
toolkit and compares them pixel by pixel; tests/test_ui_style_data.py holds
native/moy_ui/style.json to these tables.

Generated from the two modules with their docstrings dropped; it is frozen
here, and edited only where the shipped toolkit's contract deliberately
moves (each such edit says so where it is).
"""

import re

from runtime.widgets import _in as rect_in

_BLACK = 0
_WHITE = 7

def _fs(cv):
    fs = getattr(cv, 'font_scale', 1)
    return fs if fs >= 1 else 1

def is_light(th):
    return bool(th.get('surface_light', False))

def scroll_cues(cv, up_xy, dn_xy, can_up, can_dn, c, scale=1):
    if can_up:
        cv.print('^', up_xy[0], up_xy[1], c, scale)
    if can_dn:
        cv.print('v', dn_xy[0], dn_xy[1], c, scale)

# Edited with the text path (sprint 6 step 3, docs/kernel_toolkit_2026-10.md
# §4): text is measured in UTF-8 BYTES, whitespace is str.split()'s ASCII set,
# and a truncation backs up to the last whole codepoint, ending its line. For
# ASCII this is the old body exactly.
_SPACES = re.compile(b"[ \t\n\r\x0b\x0c\x1c-\x1f]+")


def fit_bytes(b, cells):
    if len(b) <= cells:
        return b
    k = cells
    while k > 0 and (b[k] & 0xC0) == 0x80:
        k -= 1
    return b[:k]


def wrap_words(text, maxc):
    lines = []
    cur = b''
    if maxc < 1:
        return lines
    for word in _SPACES.split(str(text).encode('utf-8')):
        if not word:
            continue
        cand = word if not cur else cur + b' ' + word
        if len(cand) <= maxc:
            cur = cand
            continue
        if cur:
            lines.append(cur)
        cur = b''
        if len(word) <= maxc:
            cur = word
        else:
            f = fit_bytes(word, maxc)
            if f:
                lines.append(f)
    if cur:
        lines.append(cur)
    return [ln.decode('utf-8') for ln in lines]

def inset(rect, dx, dy=None):
    if dy is None:
        dy = dx
    (x, y, w, h) = rect
    return (x + dx, y + dy, max(0, w - 2 * dx), max(0, h - 2 * dy))

def cut_top(rect, h):
    (x, y, w, rh) = rect
    h = max(0, min(h, rh))
    return ((x, y, w, h), (x, y + h, w, rh - h))

def cut_bottom(rect, h):
    (x, y, w, rh) = rect
    h = max(0, min(h, rh))
    return ((x, y + rh - h, w, h), (x, y, w, rh - h))

def cut_left(rect, w):
    (x, y, rw, h) = rect
    w = max(0, min(w, rw))
    return ((x, y, w, h), (x + w, y, rw - w, h))

def cut_right(rect, w):
    (x, y, rw, h) = rect
    w = max(0, min(w, rw))
    return ((x + rw - w, y, w, h), (x, y, rw - w, h))

def hsplit(rect, n, gap=0):
    (x, y, w, h) = rect
    if n <= 1:
        return [rect]
    cw = (w - (n - 1) * gap) // n
    cols = []
    cx = x
    for i in range(n - 1):
        cols.append((cx, y, cw, h))
        cx += cw + gap
    cols.append((cx, y, x + w - cx, h))
    return cols

def vsplit(rect, n, gap=0):
    (x, y, w, h) = rect
    if n <= 1:
        return [rect]
    rh = (h - (n - 1) * gap) // n
    rows = []
    ry = y
    for i in range(n - 1):
        rows.append((x, ry, w, rh))
        ry += rh + gap
    rows.append((x, ry, w, y + h - ry))
    return rows
REST = 'rest'
HOVER = 'hover'
ON = 'on'
HOT = 'hot'
PRESSED = 'pressed'
DISABLED = 'disabled'
STATES = (DISABLED, PRESSED, HOT, ON, HOVER, REST)

def widget_state(on=False, hot=False, disabled=False, interact=None):
    if disabled:
        return DISABLED
    if interact == PRESSED:
        return PRESSED
    if hot:
        return HOT
    if on:
        return ON
    if interact == HOVER:
        return HOVER
    return REST
_STATE_ALIAS = {'hover': ('dim',), 'hover_cue': ('focus', 'accent'), 'pressed': ('selection', 'hilite'), 'pressed_ink': ('selection_ink', 'ink'), 'disabled_ink': ('ink_dim',), 'disabled_edge': ('dim',)}

def state_token(th, role, fallback=None):
    v = th.get(role)
    if v is not None:
        return v
    chain = _STATE_ALIAS.get(role)
    if chain is not None:
        for base in chain:
            v = th.get(base)
            if v is not None:
                return v
    return fallback
ALIGN_LEFT = 0
ALIGN_CENTER = 1
ALIGN_RIGHT = 2
(BTN_PAD, BTN_ICON, BTN_GLYPH, BTN_EDGE, BTN_ALIGN) = (0, 1, 2, 3, 4)
(CHIP_CLIP, CHIP_MINX, CHIP_MINY, CHIP_EDGE, CHIP_ALIGN) = (0, 1, 2, 3, 4)
(ROW_PAD, ROW_ICON, ROW_GLYPH, ROW_GAP, ROW_EDGE) = (0, 1, 2, 3, 4)
(CELL_PAD, CELL_CAP, CELL_INDENT, CELL_ICON, CELL_GLYPH, CELL_EDGE) = (0, 1, 2, 3, 4, 5)
(TAB_GAP, TAB_PAD, TAB_ICON) = (0, 1, 2)
(ST_RULE, ST_PAD, ST_GAP, ST_DY) = (0, 1, 2, 3)
(PANEL_EDGE, PANEL_INSET, PANEL_STRIP, PANEL_PAD) = (0, 1, 2, 3)
(FOCUS_RINGS, FOCUS_GAP) = (0, 1)
(SB_W, SB_MIN) = (0, 1)
(TF_PADX, TF_PADY, TF_CARET_W, TF_CARET_H) = (0, 1, 2, 3)
(TF_FIELD, TF_RING, TF_INK, TF_HINT, TF_CARET) = (4, 5, 6, 7, 8)
(GB_PAD, GB_RING, GB_INK) = (0, 1, 2)
(GI_PAD, GI_GLYPH, GI_LABEL, GI_RING, GI_INK) = (0, 1, 2, 3, 4)
(MB_PADX, MB_PADY, MB_INK) = (0, 1, 2)
(DLG_EDGE, DLG_FILL, DLG_RING) = (0, 1, 2)
DEFAULT_METRICS = {'button': (2, 16, 12, 1, ALIGN_CENTER), 'chip': (4, 2, 1, 1, ALIGN_CENTER), 'row': (4, 16, 14, 1, 1), 'cell': (2, 14, 2, 16, 14, 1), 'tab': (2, 3, 16), 'status': (1, 3, 2, 1), 'panel': (1, 1, 12, 3), 'focus': (2, 1), 'scrollbar': (4, 8), 'text_field': (4, 3, 6, 8, _BLACK, 6, _WHITE, 5, 10), 'game_btn': (6, _WHITE, _BLACK), 'game_icon_btn': (2, 16, 19, _WHITE, _BLACK), 'mini_btn': (2, 2, _BLACK), 'dialog': (1, 2, _WHITE)}
_PANEL = ('panel', 60)
_TITLE = ('title', 13)
_TITLE_INK = ('title_ink', _BLACK)
_DIM = ('dim', 1)
_EDGE = ('edge', 13)
_ACCENT = ('accent', 10)
_DANGER = ('danger', 8)
_INK_DIM = ('ink_dim', 6)
_BORDER = ('border', _BLACK)
_SURFACE = ('surface', 'panel', 60)
_SELECTION = ('selection', 'hilite', 13)
_PRESS = ('pressed', 'hilite', 13)
_PRESS_INK = ('pressed_ink', _WHITE)
_DIS_INK = ('disabled_ink', 6)
_DIS_EDGE = ('disabled_edge', 1)
_HOVER_CUE = ('hover_cue', 'accent', 10)
_ROW_STATES = {REST: (_PANEL, _TITLE_INK, _DIM), HOVER: (_PANEL, _TITLE_INK, _HOVER_CUE), ON: (_TITLE, _TITLE_INK, _ACCENT), HOT: (_DANGER, _WHITE, _DIM), PRESSED: (_PRESS, _PRESS_INK, _ACCENT), DISABLED: (_PANEL, _DIS_INK, _DIS_EDGE)}
_CHIP_STATES = {REST: (_PANEL, _TITLE_INK, _DIM), HOVER: (_PANEL, _TITLE_INK, _HOVER_CUE), ON: (_ACCENT, _BLACK, _EDGE), HOT: (_DANGER, _WHITE, _DIM), PRESSED: (_PRESS, _PRESS_INK, _EDGE), DISABLED: (_PANEL, _DIS_INK, _DIS_EDGE)}
_CELL_BAND_STATES = {REST: (_TITLE, _TITLE_INK, None), HOVER: (_TITLE, _TITLE_INK, None), ON: (_ACCENT, _BLACK, None), HOT: (_DANGER, _TITLE_INK, None), PRESSED: (_TITLE, _TITLE_INK, None), DISABLED: (_TITLE, _TITLE_INK, None)}
_HILITE = ('hilite', 13)
_CHROME_INK = ('chrome_ink', _WHITE)
_CHROME_DIM = ('chrome_ink_dim', 6)
_SEL_INK = ('selection_ink', _WHITE)

def _chrome_row_states(rest_ink):
    return {REST: (None, rest_ink, None), HOVER: (None, rest_ink, _HOVER_CUE), ON: (_HILITE, _SEL_INK, None), HOT: (_DANGER, _WHITE, _DIM), PRESSED: (_PRESS, _PRESS_INK, None), DISABLED: (None, _DIS_INK, None)}
_ROW_CHROME_STATES = _chrome_row_states(_CHROME_INK)
_ROW_CHROME_STATES[DISABLED] = (None, _CHROME_DIM, None)
_ROW_CTA_STATES = {REST: (_HILITE, _BLACK, _DIM), HOVER: (_HILITE, _BLACK, _HOVER_CUE), ON: (_ACCENT, _BLACK, _ACCENT), HOT: (_DANGER, _WHITE, _DIM), PRESSED: (_PRESS, _PRESS_INK, _ACCENT), DISABLED: (_HILITE, _DIS_INK, _DIM)}

def _button_states(bg, ink):
    return {REST: (bg, ink, _BLACK), HOVER: (bg, ink, _HOVER_CUE), ON: (_ACCENT, _BLACK, _BLACK), HOT: (bg, ink, _BLACK), PRESSED: (_PRESS, _PRESS_INK, _BLACK), DISABLED: (bg, _DIS_INK, _BLACK)}
_BUTTON_KIND_KEY = {'normal': 'button', 'play': 'button_play', 'author': 'button_author', 'danger': 'button_danger'}
DEFAULT_SPECS = {'row': _ROW_STATES, 'cell': _ROW_STATES, 'cell_band': _CELL_BAND_STATES, 'chip': _CHIP_STATES, 'row_menu': _chrome_row_states(_CHROME_DIM), 'row_list': _chrome_row_states(_INK_DIM), 'row_chrome': _ROW_CHROME_STATES, 'row_cta': _ROW_CTA_STATES, 'button': _button_states(('surface', _WHITE), ('ink', _BLACK)), 'button_play': _button_states(('play', _WHITE), _WHITE), 'button_author': _button_states(('author', _WHITE), _BLACK), 'button_danger': _button_states(('danger', _WHITE), _WHITE), 'tab': {ON: (_SELECTION, _TITLE_INK, None), REST: (None, _WHITE, None)}, 'status': {REST: (('surface_alt', 'panel', 60), _INK_DIM, _BORDER)}, 'panel': {REST: (_SURFACE, None, _BORDER)}, 'panel_title': {REST: (_TITLE, _TITLE_INK, None)}, 'toolbar': {REST: (_TITLE, None, None)}, 'scrollbar': {REST: (_DIM, _INK_DIM, None)}, 'focus': {REST: (None, None, ('focus', 10))}}
NON_DATA_QUIRKS = (('game_btn', 'at font scale 1 the label prints with the legacy scale-2 argument but centres with height 8 -- an off-by-one-scale mismatch the shipped pixels bake in. It is a BRANCH on fs, not a number.'), ('text_field', 'fixed 1x metrics: both block prompts draw on the game canvas, so the pads, the caret box and the 8px caret advance never scale. The numbers are data (TF_*), the absence of `* fs` is the quirk.'), ('mini_btn', "unscaled label pads (2, 2): the Settings steppers' vocabulary predates font scaling and its pads never grew. Data (MB_*), unscaled by branch."), ('chip', "unscaled label clip/min pads (4, 2, 1): a chip's label geometry is in raw pixels while its glyph box is not. Data (CHIP_*), unscaled."))

def _token(th, spec):
    if spec is None:
        return None
    if isinstance(spec, int):
        return spec
    if isinstance(spec, str):
        return state_token(th, spec)
    for s in spec:
        v = _token(th, s)
        if v is not None:
            return v
    return None
_SKIN = None
_SPECS = DEFAULT_SPECS
_METRICS = DEFAULT_METRICS
_FLAT = {}
_FLAT_TH = None
_FLAT_RING = []
_FLAT_RING_MAX = 4

def set_skin(fn=None, specs=None, metrics=None):
    global _SKIN, _SPECS, _METRICS, _FLAT, _FLAT_TH
    _SKIN = fn
    if specs is None:
        _SPECS = DEFAULT_SPECS
    else:
        merged = dict(DEFAULT_SPECS)
        merged.update(specs)
        _SPECS = merged
    if metrics is None:
        _METRICS = DEFAULT_METRICS
    else:
        merged = dict(DEFAULT_METRICS)
        merged.update(metrics)
        _METRICS = merged
    _FLAT = {}
    _FLAT_TH = None
    del _FLAT_RING[:]
    ScrollRegion.BAR_W = _METRICS['scrollbar'][SB_W]

def metrics(kind):
    return _METRICS[kind]

def _flatten_kind(th, kind):
    src = _SPECS.get(kind)
    if src is None:
        return None
    out = {}
    for state in src:
        spec = src[state]
        out[state] = (_token(th, spec[0]), _token(th, spec[1]), _token(th, spec[2]))
    _FLAT[kind] = out
    return out

def _state_colors_slow(th, kind, state):
    ent = _FLAT.get(kind)
    if ent is None:
        ent = _flatten_kind(th, kind)
    if ent is not None:
        got = ent.get(state)
        if got is not None:
            return got
        got = ent.get(REST)
        if got is not None:
            return got
    if kind != 'row':
        return state_colors(th, 'row', state)
    return (None, _BLACK, None)

def state_colors(th, kind, state):
    if _SKIN is not None:
        out = _SKIN(th, kind, state)
        if out is not None:
            return out
    if th is not _FLAT_TH:
        _bind_theme(th)
    try:
        return _FLAT[kind][state]
    except KeyError:
        return _state_colors_slow(th, kind, state)

def _bind_theme(th):
    global _FLAT, _FLAT_TH
    ring = _FLAT_RING
    for i in range(len(ring)):
        ent = ring[i]
        if ent[0] is th:
            _FLAT = ent[1]
            _FLAT_TH = th
            if i:
                del ring[i]
                ring.insert(0, ent)
            return
    _FLAT = {}
    _FLAT_TH = th
    ring.insert(0, (th, _FLAT))
    del ring[_FLAT_RING_MAX:]

def cell_state(index, hover_index=-1, pressed_index=-1):
    if index < 0:
        return None
    if index == pressed_index:
        return PRESSED
    if index == hover_index:
        return HOVER
    return None
HITS_DEBUG = False

class Hits:

    def __init__(self):
        self._items = []
        self.hover = None
        self.pressed = None
        self._armed = None
        self._down = False

    def clear(self):
        del self._items[:]

    def add(self, rect, verb, arg=None):
        if HITS_DEBUG:
            items = self._items
            for i in range(len(items)):
                it = items[i]
                if it[1] == verb and it[2] == arg:
                    raise ValueError('duplicate Hits id (%r, %r): state_of() would be ambiguous' % (verb, arg))
        self._items.append((rect, verb, arg))

    def at(self, px, py):
        for i in range(len(self._items) - 1, -1, -1):
            (rect, verb, arg) = self._items[i]
            if rect_in(px, py, rect):
                return (verb, arg)
        return None

    def pointer_frame(self, px, py, pointer):
        down = bool(getattr(pointer, 'down', False))
        pointing = bool(getattr(pointer, 'visible', False) or getattr(pointer, 'hovering', False))
        hit = self.at(px, py)
        if down:
            if not self._down:
                self._armed = hit
            armed = self._armed
            new_pressed = armed if armed is not None and hit == armed else None
            new_hover = None
        else:
            self._armed = None
            new_pressed = None
            new_hover = hit if pointing else None
        self._down = down
        changed = new_hover != self.hover or new_pressed != self.pressed
        self.hover = new_hover
        self.pressed = new_pressed
        return changed

    def pointer_leave(self):
        changed = self.hover is not None or self.pressed is not None
        self.hover = None
        self.pressed = None
        self._armed = None
        self._down = False
        return changed

    def state_of(self, verb, arg=None):
        p = self.pressed
        if p is not None and p[0] == verb and (p[1] == arg):
            return PRESSED
        h = self.hover
        if h is not None and h[0] == verb and (h[1] == arg):
            return HOVER
        return None

def button(cv, th, rect, label, kind='normal', on=False, icon_img=None, glyph=None, glyph_draw=None, disabled=False, state=None):
    fs = _fs(cv)
    (x, y, w, h) = rect
    st = widget_state(on, False, disabled, state)
    (bg, ink, edge) = state_colors(th, _BUTTON_KIND_KEY.get(kind, 'button'), st)
    m = _METRICS['button']
    cv.rect(x, y, w, h, bg)
    n = m[BTN_EDGE]
    if n > 0:
        cv.rectb(x, y, w, h, edge)
        i = 1
        while i < n:
            cv.rectb(x + i, y + i, w - 2 * i, h - 2 * i, edge)
            i += 1
    fw = 8 * fs
    pad = m[BTN_PAD] * fs
    icon_box = m[BTN_ICON] * fs
    iw = 0
    has_glyph = glyph is not None and glyph_draw is not None
    if icon_img is not None or has_glyph:
        iw = (icon_box if icon_img is not None else m[BTN_GLYPH] * fs) + pad
    label = str(label)
    maxc = max(0, (w - 2 * pad - iw) // fw)
    label = fit_bytes(label.encode('utf-8'), maxc).decode('utf-8')  # bytes (step 6)
    tw = iw + len(label.encode('utf-8')) * fw
    align = m[BTN_ALIGN]
    if align == ALIGN_CENTER:
        off = (w - tw) // 2
    elif align == ALIGN_RIGHT:
        off = w - pad - tw
    else:
        off = pad
    if off < pad:
        off = pad
    tx = x + off
    if icon_img is not None:
        cv.spr(icon_img, tx, y + (h - icon_box) // 2, fs)
    elif has_glyph:
        glyph_draw(glyph, (tx, y, m[BTN_GLYPH] * fs, h), ink, cv)
    if label:
        cv.print(label, tx + iw, y + (h - 8 * fs) // 2, ink, 1)

def chip(cv, th, rect, label, on=False, hot=False, fs=None, glyph=None, glyph_draw=None, disabled=False, state=None, colors=None):
    if fs is None:
        fs = _fs(cv)
    (x, y, w, h) = rect
    st = widget_state(on, hot, disabled, state)
    if colors is None:
        colors = state_colors(th, 'chip', st)
    (bg, ink, edge) = colors
    m = _METRICS['chip']
    if bg is not None:
        cv.rect(x, y, w, h, bg)
    n = m[CHIP_EDGE]
    if n > 0 and edge is not None:
        cv.rectb(x, y, w, h, edge)
        i = 1
        while i < n:
            cv.rectb(x + i, y + i, w - 2 * i, h - 2 * i, edge)
            i += 1
    if glyph is not None and glyph_draw is not None:
        glyph_draw(glyph, rect, ink, cv)
        return
    fw = 8 * fs
    label = str(label)
    maxc = max(0, (w - m[CHIP_CLIP]) // fw)
    label = fit_bytes(label.encode('utf-8'), maxc).decode('utf-8')  # bytes (step 6)
    lw = len(label.encode('utf-8')) * fw
    minx = m[CHIP_MINX]
    align = m[CHIP_ALIGN]
    if align == ALIGN_CENTER:
        ox = (w - lw) // 2
    elif align == ALIGN_RIGHT:
        ox = w - minx - lw
    else:
        ox = minx
    if ox < minx:
        ox = minx
    oy = (h - 8 * fs) // 2
    if oy < m[CHIP_MINY]:
        oy = m[CHIP_MINY]
    cv.print(label, x + ox, y + oy, ink, 1)

def row(cv, th, rect, label, kind='row', on=False, hot=False, disabled=False, state=None, colors=None, icon_img=None, glyph=None, glyph_draw=None, value=None, value_ink=None, edge=True, pad=None, text_dy=None, hits=None, verb=None, arg=None, fs=None, pad_right=None, scale=1, glyph_ink=None, glyph_size=None):
    if fs is None:
        fs = _fs(cv)
    (x, y, w, h) = rect
    if state is None and hits is not None and (verb is not None):
        state = hits.state_of(verb, arg)
    st = widget_state(on, hot, disabled, state)
    if colors is None:
        colors = state_colors(th, kind, st)
    (field, ink, edge_c) = colors
    m = _METRICS['row']
    if field is not None:
        cv.rect(x, y, w, h, field)
    if edge and edge_c is not None:
        n = m[ROW_EDGE]
        if n > 0:
            cv.rectb(x, y, w, h, edge_c)
            i = 1
            while i < n:
                cv.rectb(x + i, y + i, w - 2 * i, h - 2 * i, edge_c)
                i += 1
    if pad is None:
        pad = m[ROW_PAD] * fs
    fw = 8 * fs * scale
    th_ = 8 * fs * scale
    tx = x + pad
    right = x + w - (pad if pad_right is None else pad_right)
    if icon_img is not None:
        box = m[ROW_ICON] * fs
        cv.spr(icon_img, tx, y + (h - box) // 2, fs)
        tx += box + pad
    elif glyph is not None and glyph_draw is not None:
        box = (m[ROW_GLYPH] if glyph_size is None else glyph_size) * fs
        glyph_draw(glyph, (tx, y + (h - box) // 2, box, box), ink if glyph_ink is None else glyph_ink, cv)
        tx += box + pad
    ty = y + ((h - th_) // 2 if text_dy is None else text_dy)
    if value is not None:
        value = str(value)
        maxv = (right - tx) // fw
        if maxv < 0:
            maxv = 0
        value = fit_bytes(value.encode('utf-8'), maxv)    # bytes (step 5)
        if value:
            cv.print(value.decode('utf-8'), right - len(value) * fw, ty, ink if value_ink is None else value_ink, scale)
            right -= (len(value) + m[ROW_GAP]) * fw
    if label:
        label = str(label)
        maxc = (right - tx) // fw
        if maxc < 0:
            maxc = 0
        label = fit_bytes(label.encode('utf-8'), maxc)    # bytes (step 5)
        if label:
            cv.print(label.decode('utf-8'), tx, ty, ink, scale)
    if hits is not None and verb is not None and (st != DISABLED):
        hits.add(rect, verb, arg)

def row_content_rect(rect, fs=1, pad=None, pad_right=None, icon=False, glyph=False, glyph_size=None):
    m = _METRICS['row']
    if pad is None:
        pad = m[ROW_PAD] * fs
    if pad_right is None:
        pad_right = pad
    (x, y, w, h) = rect
    tx = x + pad
    if icon:
        tx += m[ROW_ICON] * fs + pad
    elif glyph:
        tx += (m[ROW_GLYPH] if glyph_size is None else glyph_size) * fs + pad
    return (tx, y, max(0, x + w - pad_right - tx), h)

def cell_art_rect(rect, fs=1, pad=None, caption_h=0):
    if pad is None:
        pad = _METRICS['cell'][CELL_PAD] * fs
    (x, y, w, h) = rect
    return (x + pad, y + pad, max(0, w - 2 * pad), max(0, h - pad - caption_h))

def cell(cv, th, rect, label=None, kind='cell', on=False, hot=False, disabled=False, state=None, colors=None, band=False, band_fill=None, band_ink=None, pad=None, caption_h=None, icon_img=None, glyph=None, glyph_draw=None, fs=None, text_dx=None, text_dy=None, edge_last=False):
    if fs is None:
        fs = _fs(cv)
    (x, y, w, h) = rect
    st = widget_state(on, hot, disabled, state)
    if colors is None:
        colors = state_colors(th, kind, st)
    (field, ink, edge_c) = colors
    m = _METRICS['cell']
    if pad is None:
        pad = m[CELL_PAD] * fs
    if caption_h is None:
        caption_h = m[CELL_CAP] * fs if label is not None else 0
    if field is not None:
        cv.rect(x, y, w, h, field)
    if label is not None and caption_h > 0:
        cap_y = y + h - caption_h
        cap_ink = ink
        if band:
            if band_fill is None or band_ink is None:
                (bf, bi, _be) = state_colors(th, 'cell_band', st)
                if band_fill is None:
                    band_fill = bf
                if band_ink is None:
                    band_ink = bi
            cv.rect(x, cap_y, w, caption_h, band_fill)
            cap_ink = band_ink
        label = str(label)
        maxc = max(0, (w - 2 * pad) // (8 * fs))
        label = fit_bytes(label.encode('utf-8'), maxc)    # bytes (step 5)
        if label:
            lw = len(label) * 8 * fs
            label = label.decode('utf-8')
            if text_dx is None:
                lx = x + pad + m[CELL_INDENT] * fs if band else x + (w - lw) // 2
            else:
                lx = x + text_dx
            ly = cap_y + (caption_h - 8 * fs) // 2 if text_dy is None else y + text_dy
            cv.print(label, lx, ly, cap_ink, 1)
    n = m[CELL_EDGE]
    if edge_c is not None and n > 0 and (not edge_last):
        _cell_edge(cv, x, y, w, h, edge_c, n)
    art = cell_art_rect(rect, fs, pad, caption_h)
    (ax, ay, aw, ah) = art
    if icon_img is not None:
        box = m[CELL_ICON] * fs
        cv.spr(icon_img, ax + (aw - box) // 2, ay + (ah - box) // 2, fs)
    elif glyph is not None and glyph_draw is not None:
        box = m[CELL_GLYPH] * fs
        glyph_draw(glyph, (ax + (aw - box) // 2, ay + (ah - box) // 2, box, box), ink, cv)
    if edge_c is not None and n > 0 and edge_last:
        _cell_edge(cv, x, y, w, h, edge_c, n)
    return art

def _cell_edge(cv, x, y, w, h, c, n):
    cv.rectb(x, y, w, h, c)
    i = 1
    while i < n:
        cv.rectb(x + i, y + i, w - 2 * i, h - 2 * i, c)
        i += 1

def tab_row_rects(rect, tabs, fs, gap=None):
    m = _METRICS['tab']
    if gap is None:
        gap = m[TAB_GAP] * fs
    (x0, y, w, h) = rect
    fw = 8 * fs
    pad = m[TAB_PAD] * fs
    icon_w = m[TAB_ICON] * fs

    def chip_w(label, labels_on):
        if labels_on and label:
            return icon_w + pad + len(str(label).encode('utf-8')) * fw + pad
        return icon_w + 2 * pad
    labels_on = True
    total = 0
    for (_id, label) in tabs:
        total += chip_w(label, True) + gap
    if total - gap > w:
        labels_on = False
    out = []
    x = x0
    for (tid, label) in tabs:
        cw = chip_w(label, labels_on)
        if x + cw > x0 + w:
            break
        out.append((tid, (x, y, cw, h), labels_on))
        x += cw + gap
    return out

def tab_row(cv, th, rect, tabs, active, icon_for=None, hits=None, ink=None):
    fs = _fs(cv)
    m = _METRICS['tab']
    (on_field, on_ink, _oe) = state_colors(th, 'tab', ON)
    (off_field, off_ink, _re) = state_colors(th, 'tab', REST)
    if ink is not None:
        off_ink = ink
    icons = {}
    slim = []
    for (tid, label, icon_kind) in tabs:
        slim.append((tid, label))
        icons[tid] = icon_kind
    rects = tab_row_rects(rect, slim, fs)
    pad = m[TAB_PAD] * fs
    box = m[TAB_ICON] * fs
    for (tid, r, labels_on) in rects:
        (x, y, w, h) = r
        if tid == active:
            if on_field is not None:
                cv.rect(x, y, w, h, on_field)
            ink_i = on_ink
        else:
            if off_field is not None:
                cv.rect(x, y, w, h, off_field)
            ink_i = off_ink
        img = icon_for(icons[tid]) if icon_for is not None else None
        if img is not None:
            cv.spr(img, x + (pad if labels_on else (w - box) // 2), y + (h - box) // 2, fs)
        if labels_on:
            cv.print(str(_tab_label(tabs, tid)), x + pad + box + pad, y + (h - 8 * fs) // 2, ink_i, 1)
        if hits is not None:
            hits.add(r, 'tab', tid)
    return rects

def _tab_label(tabs, tid):
    for (t, label, _icon) in tabs:
        if t == tid:
            return label
    return ''

def status_row(cv, th, rect, items):
    fs = _fs(cv)
    (x, y, w, h) = rect
    (field, ink, rule) = state_colors(th, 'status', REST)
    m = _METRICS['status']
    cv.rect(x, y, w, h, field)
    if rule is not None and m[ST_RULE] > 0:
        cv.rect(x, y, w, m[ST_RULE] * fs, rule)
    fw = 8 * fs
    tx = x + m[ST_PAD] * fs
    ty = y + (h - 8 * fs + m[ST_DY] * fs) // 2
    gap = m[ST_GAP]
    for item in items:
        s = str(item)
        if tx + len(s.encode('utf-8')) * fw > x + w:
            break
        cv.print(s, tx, ty, ink, 1)
        tx += (len(s.encode('utf-8')) + gap) * fw

def game_btn(cv, rect, label, fill):
    (x, y, w, h) = rect
    fs = _fs(cv)
    m = _METRICS['game_btn']
    cv.rect(x, y, w, h, fill)
    cv.rectb(x, y, w, h, m[GB_RING])
    if fs <= 1:
        cv.print(label, x + m[GB_PAD], y + (h - 8) // 2, m[GB_INK], 2)
    else:
        cv.print(label, x + m[GB_PAD] * fs, y + (h - 8 * fs) // 2, m[GB_INK], 2)

def game_icon_btn(cv, rect, kind, label, fill, glyph_draw=None):
    (x, y, w, h) = rect
    fs = _fs(cv)
    m = _METRICS['game_icon_btn']
    cv.rect(x, y, w, h, fill)
    cv.rectb(x, y, w, h, m[GI_RING])
    if glyph_draw is not None:
        glyph_draw(kind, (x + m[GI_PAD] * fs, y, m[GI_GLYPH] * fs, h), m[GI_INK], cv)
    if label:
        cv.print(label, x + m[GI_LABEL] * fs, y + (h - 8 * fs) // 2, m[GI_INK], 1)

def mini_btn(cv, rect, label, fill, label_rect=None):
    (x, y, w, h) = rect
    m = _METRICS['mini_btn']
    cv.rect(x, y, w, h, fill)
    (lx, ly) = (rect if label_rect is None else label_rect)[:2]
    cv.print(label, lx + m[MB_PADX], ly + m[MB_PADY], m[MB_INK], 1)

def toolbar(cv, th, rect):
    (x, y, w, h) = rect
    (field, _ink, _edge) = state_colors(th, 'toolbar', REST)
    cv.rect(x, y, w, h, field)
    return rect

def dialog(cv, rect, ring=None, fill=None):
    (x, y, w, h) = rect
    m = _METRICS['dialog']
    cv.rect(x, y, w, h, m[DLG_FILL] if fill is None else fill)
    c = m[DLG_RING] if ring is None else ring
    n = m[DLG_EDGE]
    if n > 0:
        cv.rectb(x, y, w, h, c)
        i = 1
        while i < n:
            cv.rectb(x + i, y + i, w - 2 * i, h - 2 * i, c)
            i += 1

def dialog_rings():
    return _METRICS['dialog'][DLG_EDGE]

def text_field(cv, rect, text, placeholder='', colors=None, fs=None):
    (x, y, w, h) = rect
    m = _METRICS['text_field']
    if colors is None:
        (field, ring, ink) = (m[TF_FIELD], m[TF_RING], m[TF_INK])
        (hint, caret) = (m[TF_HINT], m[TF_CARET])
    else:
        (field, ring, ink, hint, caret) = colors
    if fs is None:
        fs = 1
    px = m[TF_PADX] * fs
    py = m[TF_PADY] * fs
    if field is not None:
        cv.rect(x, y, w, h, field)
    if ring is not None:
        cv.rectb(x, y, w, h, ring)
    if text:
        cv.print(text, x + px, y + py, ink, 1)
    elif placeholder:
        cv.print(placeholder, x + px, y + py, hint, 1)
    if caret is not None:
        cv.rect(x + px + len(text.encode('utf-8')) * 8 * fs, y + py, m[TF_CARET_W] * fs, m[TF_CARET_H] * fs, caret)

def panel(cv, th, rect, title=None, fs=None):
    if fs is None:
        fs = _fs(cv)
    (x, y, w, h) = rect
    (field, _ink, border) = state_colors(th, 'panel', REST)
    m = _METRICS['panel']
    cv.rect(x, y, w, h, field)
    n = m[PANEL_EDGE] * fs
    i = 0
    while i < n:
        cv.rectb(x - i, y - i, w + 2 * i, h + 2 * i, border)
        i += 1
    content = inset(rect, m[PANEL_INSET], m[PANEL_INSET])
    if title:
        (strip, content) = cut_top(content, m[PANEL_STRIP] * fs)
        (sx, sy, sw, sh) = strip
        (tf, ti, _te) = state_colors(th, 'panel_title', REST)
        cv.rect(sx, sy, sw, sh, tf)
        cv.print(str(title), sx + m[PANEL_PAD] * fs, sy + (sh - 8 * fs) // 2, ti, 1)
    return content

def focus_ring(cv, th, rect, fs=None):
    if fs is None:
        fs = _fs(cv)
    (x, y, w, h) = rect
    (_f, _i, color) = state_colors(th, 'focus', REST)
    m = _METRICS['focus']
    n = m[FOCUS_RINGS]
    if n < fs:
        n = fs
    gap = m[FOCUS_GAP]
    i = 0
    while i < n:
        d = fs + gap + i
        cv.rectb(x - d, y - d, w + 2 * d, h + 2 * d, color)
        i += 1

def fill_uncovered(cv, inner, outer, col):
    (ix, iy, iw, ih) = (int(inner[0]), int(inner[1]), int(inner[2]), int(inner[3]))
    (ox, oy, ow, oh) = (int(outer[0]), int(outer[1]), int(outer[2]), int(outer[3]))
    if iw <= 0 or ih <= 0:
        return
    (ix1, iy1) = (ix + iw, iy + ih)
    (ox1, oy1) = (ox + ow, oy + oh)
    if ox >= ix1 or ox1 <= ix or oy >= iy1 or (oy1 <= iy):
        cv.rect(ix, iy, iw, ih, col)
        return
    if iy < oy:
        cv.rect(ix, iy, iw, oy - iy, col)
    if iy1 > oy1:
        cv.rect(ix, oy1, iw, iy1 - oy1, col)
    my0 = iy if iy > oy else oy
    my1 = iy1 if iy1 < oy1 else oy1
    if my1 > my0:
        if ix < ox:
            cv.rect(ix, my0, ox - ix, my1 - my0, col)
        if ix1 > ox1:
            cv.rect(ox1, my0, ix1 - ox1, my1 - my0, col)

def row_drag(anchor, py, step, top, top_max):
    delta = anchor - py
    while delta >= step and top < top_max:
        top += 1
        delta -= step
    while delta <= -step and top > 0:
        top -= 1
        delta += step
    return (py + delta, top)
_MAX_RETAINED = 3

class ScrollRegion:
    BAR_W = 4
    FRICTION = 0.995
    MIN_FLING = 0.15
    STOP_VEL = 0.02
    VEL_EMA = 0.5

    def __init__(self, horizontal=False):
        self.horizontal = bool(horizontal)
        self.view = (0, 0, 0, 0)
        self.content = 0
        self.offset = 0
        self._drag = None
        self._vel = 0.0
        self._fling = False
        self._foff = 0.0
        self._painted = []

    def set(self, view_rect, content):
        self.view = view_rect
        self.content = max(0, int(content))
        self._clamp()

    def _extent(self):
        return self.view[2] if self.horizontal else self.view[3]

    def _max_offset(self):
        return max(0, self.content - self._extent())

    def _clamp(self):
        m = self._max_offset()
        if self.offset < 0:
            self.offset = 0
        elif self.offset > m:
            self.offset = m

    def scroll_by(self, d):
        self.offset += int(d)
        self._clamp()

    def invalidate(self):
        self._painted = []

    def note_painted(self, frame_no, key=None, stamp=None):
        self._painted.insert(0, (frame_no, self.offset, key, stamp))
        del self._painted[_MAX_RETAINED:]

    def blit_shift(self, cv, frame_no, key=None):
        k = getattr(cv, 'RETAINED_FRAMES', 0)
        if k < 1 or getattr(cv, 'scroll_rect', None) is None or len(self._painted) < k:
            return None
        for i in range(k):
            if self._painted[i][0] != frame_no - 1 - i:
                return None
        (_fno, off, pkey, stamp) = self._painted[k - 1]
        if pkey != key:
            return None
        delta = self.offset - off
        if abs(delta) >= self._extent():
            return None
        return (delta, stamp)

    def scroll_to_show(self, p, size):
        vis = self._extent()
        if p < self.offset:
            self.offset = p
        elif p + size > self.offset + vis:
            self.offset = p + size - vis
        self._clamp()

    def drag_start(self, p):
        self._drag = p
        self.stop()

    def drag_move(self, p, dt_ms=None):
        if self._drag is None:
            return False
        d = self._drag - p
        self.scroll_by(d)
        self._drag = p
        if dt_ms:
            self._vel += (d / dt_ms - self._vel) * self.VEL_EMA
        return True

    def drag_end(self):
        self._drag = None
        if abs(self._vel) >= self.MIN_FLING and self._max_offset() > 0:
            self._fling = True
            self._foff = float(self.offset)

    def stop(self):
        self._fling = False
        self._vel = 0.0

    @property
    def animating(self):
        return self._fling

    def tick(self, dt_ms):
        if not self._fling or dt_ms <= 0:
            return False
        self._foff += self._vel * dt_ms
        self._vel *= self.FRICTION ** dt_ms
        m = self._max_offset()
        if self._foff <= 0:
            self._foff = 0.0
            self.stop()
        elif self._foff >= m:
            self._foff = float(m)
            self.stop()
        elif abs(self._vel) < self.STOP_VEL:
            self.stop()
        self.offset = int(self._foff + 0.5)
        return True

    @property
    def drag_active(self):
        return self._drag is not None

    def bar_rect(self, fs=1):
        (x, y, w, h) = self.view
        m = self._max_offset()
        vis = self._extent()
        if m <= 0 or vis <= 0:
            return None
        bw = self.BAR_W * fs
        th_ = max(_METRICS['scrollbar'][SB_MIN] * fs, vis * vis // self.content)
        tp = (vis - th_) * self.offset // m
        if self.horizontal:
            return (x + tp, y + h - bw, th_, bw)
        return (x + w - bw, y + tp, bw, th_)

    def draw_bar(self, cv, th):
        fs = _fs(cv)
        r = self.bar_rect(fs)
        if r is None:
            return
        (x, y, w, h) = self.view
        bw = self.BAR_W * fs
        (trough, thumb, _e) = state_colors(th, 'scrollbar', REST)
        if trough is not None:
            if self.horizontal:
                cv.rect(x, y + h - bw, w, bw, trough)
            else:
                cv.rect(x + w - bw, y, bw, h, trough)
        if thumb is not None:
            cv.rect(r[0], r[1], r[2], r[3], thumb)

class DragTap:

    def __init__(self, region):
        self.region = region
        self._press = None
        self._dragging = False
        self._caught = False

    @property
    def dragging(self):
        return self._dragging

    def frame(self, px, py, click, down, slop=6, dt_ms=None):
        region = self.region
        axis = px if region.horizontal else py
        if click:
            if rect_in(px, py, region.view):
                self._press = (px, py)
                self._dragging = False
                self._caught = region.animating
                region.drag_start(axis)
            return None
        if down:
            if self._press is not None and (not self._dragging):
                (sx, sy) = self._press
                if abs(px - sx) > slop or abs(py - sy) > slop:
                    self._dragging = True
            if self._dragging:
                region.drag_move(axis, dt_ms)
            return None
        region.drag_end()
        (press, self._press) = (self._press, None)
        (was_drag, self._dragging) = (self._dragging, False)
        (caught, self._caught) = (self._caught, False)
        if press is None or was_drag or caught:
            return None
        return press

# --- the glyph raster as it was in runtime/chrome.py ---------------------------
# The oracle for native/moy_ui's glyph raster (tests/test_chrome_glyphs.py):
# the 12x12 bitmaps and the run-coalescing walk, drawn as rect spans.

_GLYPH_SIZE = 12
_GLYPHS = {
    "run":    (0x000, 0x180, 0x1C0, 0x1E0, 0x1F0, 0x1F8, 0x1F8, 0x1F0, 0x1E0, 0x1C0, 0x180, 0x000),
    "save":   (0x000, 0x7FE, 0x402, 0x5FA, 0x402, 0x402, 0x4F2, 0x492, 0x492, 0x492, 0x7FE, 0x000),
    "close":  (0x000, 0x204, 0x30C, 0x198, 0x0F0, 0x060, 0x060, 0x0F0, 0x198, 0x30C, 0x204, 0x000),
    "edit":   (0x01E, 0x03E, 0x07C, 0x0F8, 0x0F0, 0x1E0, 0x3C0, 0x780, 0x700, 0x600, 0x400, 0x000),
    "paint":  (0x006, 0x00C, 0x018, 0x030, 0x060, 0x0E0, 0x1F0, 0x1F0, 0x1F0, 0x0E0, 0x000, 0x000),
    "home":   (0x000, 0x060, 0x0F0, 0x1F8, 0x3FC, 0x7FE, 0x204, 0x264, 0x264, 0x264, 0x3FC, 0x000),
    "minus":  (0x000, 0x000, 0x000, 0x000, 0x000, 0x7FE, 0x7FE, 0x000, 0x000, 0x000, 0x000, 0x000),
    "plus":   (0x000, 0x000, 0x060, 0x060, 0x060, 0x7FE, 0x7FE, 0x060, 0x060, 0x060, 0x000, 0x000),
    "turtle": (0x000, 0x0F8, 0x1FC, 0x3FE, 0x3FE, 0x3FE, 0x3FE, 0x2ED, 0x653, 0x000, 0x000, 0x000),
    "rabbit": (0x220, 0x220, 0x220, 0x360, 0x1C0, 0x3E0, 0x7F4, 0x7F0, 0x3E0, 0x000, 0x000, 0x000),
    "star":   (0x000, 0x060, 0x060, 0x0F0, 0xFFC, 0x7F8, 0x3F0, 0x3F0, 0x618, 0x618, 0x000, 0x000),
    "dot":    (0x000, 0x000, 0x000, 0x0F0, 0x1F8, 0x1F8, 0x1F8, 0x0F0, 0x000, 0x000, 0x000, 0x000),
    "get":    (0x000, 0x060, 0x060, 0x060, 0x264, 0x1F0, 0x0E0, 0x040, 0x7FE, 0x402, 0x7FE, 0x000),
    "put":    (0x000, 0x040, 0x0E0, 0x1F0, 0x264, 0x060, 0x060, 0x060, 0x7FE, 0x402, 0x7FE, 0x000),
    "heart":  (0x000, 0x30C, 0x79E, 0x7FE, 0x7FE, 0x7FE, 0x3FC, 0x1F8, 0x0F0, 0x060, 0x000, 0x000),
    # "map": a 3x3 tile grid (the tilemap editor's nav/open icon, #32) -- full
    # h-lines at rows 1/5/9, v-lines at cols 1/5/9, so it reads as a placed grid.
    "map":    (0x000, 0x7FE, 0x444, 0x444, 0x444, 0x7FE, 0x444, 0x444, 0x444, 0x7FE, 0x000, 0x000),
    # "blocks": two stacked Scratch-style notched bricks (the #29 block-editor icon).
    "blocks": (0x000, 0x3F8, 0x7FC, 0x7FC, 0x3F8, 0x000, 0x1FC, 0x3FE, 0x3FE, 0x1FC, 0x000, 0x000),
    # "scene": the placement editor (#85 Stage 2) -- a viewport frame with two
    # placed actors inside (one low-left, one high-right): things ON a stage.
    "scene":  (0x000, 0x7FE, 0x402, 0x40E, 0x40E, 0x402, 0x582, 0x582, 0x402, 0x7FE, 0x000, 0x000),
    # Desktop-shell icons (#28). "code" = angle brackets </>; "gear" = a
    # settings cog; "note" = a music note (the #16 music slot, greyed until it
    # lands); "app" = a generic window (default cart icon).
    "code":   (0x000, 0x048, 0x08C, 0x118, 0x230, 0x230, 0x118, 0x08C, 0x048, 0x000, 0x000, 0x000),
    "gear":   (0x000, 0x060, 0x276, 0x3FC, 0x1F8, 0x18C, 0x18C, 0x1F8, 0x3FC, 0x276, 0x060, 0x000),
    "note":   (0x000, 0x07E, 0x042, 0x042, 0x042, 0x042, 0x0C6, 0x1CE, 0x1CE, 0x0C4, 0x000, 0x000),
    # "music": a beamed pair of eighth notes -- the #50 sound-editor switcher's
    # fallback glyph (the bar normally blits the 16x16 IconSheet "music" sprite).
    "music":  (0x000, 0x07E, 0x042, 0x042, 0x042, 0x0C2, 0x1C2, 0x1CE, 0x00E, 0x00C, 0x000, 0x000),
    "app":    (0x000, 0x7FE, 0x402, 0x7FE, 0x402, 0x402, 0x402, 0x402, 0x402, 0x402, 0x7FE, 0x000),
    "wifi":   (0x000, 0x000, 0x1F8, 0x204, 0x0F0, 0x108, 0x060, 0x000, 0x060, 0x000, 0x000, 0x000),
    "batt":   (0x000, 0x000, 0x180, 0x7FE, 0x7FE, 0x7FE, 0x7FE, 0x7FE, 0x7FE, 0x000, 0x000, 0x000),
    # Achievements (#21): "trophy" (the unlocked-badge cue), "lock" (a locked/secret
    # entry), "smile" (the "Oh! You found me!" Easter-egg character), "key" (the
    # explorer reward), and "spark" (the celebratory confetti pip).
    "trophy": (0x000, 0x7FE, 0x7FE, 0x3FC, 0x3FC, 0x1F8, 0x0F0, 0x060, 0x060, 0x1F8, 0x1F8, 0x000),
    "lock":   (0x000, 0x0F0, 0x108, 0x108, 0x108, 0x7FE, 0x7FE, 0x792, 0x792, 0x7FE, 0x7FE, 0x000),
    "smile":  (0x0F0, 0x308, 0x404, 0x492, 0x492, 0x404, 0x444, 0x438, 0x404, 0x308, 0x0F0, 0x000),
    "key":    (0x000, 0x1C0, 0x220, 0x220, 0x1C0, 0x080, 0x080, 0x0E0, 0x080, 0x0E0, 0x000, 0x000),
    "spark":  (0x000, 0x060, 0x060, 0x060, 0x366, 0x1FC, 0x060, 0x1FC, 0x366, 0x060, 0x060, 0x000),
    # "menu": the hamburger (≡) -- three full-width bars. The system-menu (#52) toggle;
    # always a _glyph bitmap (NOT a themeable IconSheet slot) so it can't go blank on a
    # device whose saved theme predates this icon.
    "menu":   (0x000, 0x000, 0x7FE, 0x7FE, 0x000, 0x7FE, 0x7FE, 0x000, 0x7FE, 0x7FE, 0x000, 0x000),
    # "projects": a 2x2 grid of tiles -- the Editor's "back to the project-picker"
    # affordance (spec shell_ux_v1.md). Not an IconSheet slot, so it renders via the
    # _glyph fallback (ws._icon falls back to this 12x12 bitmap for kinds not in _ICON).
    "projects": (0x000, 0x7BC, 0x7BC, 0x7BC, 0x7BC, 0x000, 0x000, 0x7BC, 0x7BC, 0x7BC, 0x7BC, 0x000),
    # --- editor tool-row glyphs (#89-#93) -----------------------------------
    # The pre-literate icons for the six editors' new tool buttons -- the paint
    # transforms (#90), the map tools (#91), the code range ops (#89), the music
    # copy/reorder pads (#92) and the block undo/redo (#93) -- so those rows read
    # as pictures, not one-letter labels, on the small tiers a kid can't yet read.
    # Same authoring provenance as the set above (traced/hand-cleaned in the
    # Pixelarticons style; MIT (c) Gerrit Halfmann -- see the note over _GLYPHS).
    # "undo"/"redo": a bent arrow curling back (undo left, redo its mirror).
    "undo":      (0x000, 0x000, 0x100, 0x300, 0x7C0, 0xFC0, 0x7C8, 0x318, 0x038, 0x0F0, 0x000, 0x000),
    "redo":      (0x000, 0x000, 0x008, 0x00C, 0x03E, 0x03F, 0x13E, 0x18C, 0x1C0, 0x0F0, 0x000, 0x000),
    # "copy": two overlapping pages; "duplicate": one page with a + (a distinct
    # "make another" -- both appear on the music pad, so they must not collide).
    "copy":      (0x000, 0x0FC, 0x084, 0x3F4, 0x294, 0x294, 0x2FC, 0x210, 0x210, 0x3F0, 0x000, 0x000),
    "duplicate": (0x000, 0x3F8, 0x208, 0x248, 0x2E8, 0x248, 0x208, 0x208, 0x208, 0x3F8, 0x000, 0x000),
    # "cut": open scissors; "paste": a clipboard with lines.
    "cut":       (0x000, 0x606, 0x606, 0x30C, 0x198, 0x0F0, 0x060, 0x0F0, 0x198, 0x39C, 0x39C, 0x000),
    "paste":     (0x0E0, 0x110, 0x7FC, 0x404, 0x5F4, 0x404, 0x5F4, 0x404, 0x5F4, 0x404, 0x7FC, 0x000),
    # "select": a dashed selection marquee; "find": a magnifying glass.
    "select":    (0x000, 0x6DA, 0x402, 0x402, 0x000, 0x402, 0x402, 0x000, 0x402, 0x402, 0x6DA, 0x000),
    "find":      (0x000, 0x1E0, 0x210, 0x408, 0x408, 0x408, 0x210, 0x1E0, 0x038, 0x01C, 0x008, 0x000),
    # "indent"/"outdent": stacked text lines nudged right / left by a margin bar;
    # "linenums": two lines with leading digits (the code gutter toggle).
    "indent":    (0x000, 0x7F8, 0x000, 0x1FC, 0x000, 0xC00, 0xDE0, 0xDF8, 0xDE0, 0xC00, 0x000, 0x000),
    "outdent":   (0x000, 0x1FE, 0x000, 0x3F8, 0x000, 0x003, 0x07B, 0x1FB, 0x07B, 0x003, 0x000, 0x000),
    "linenums":  (0x000, 0x4FC, 0xCFC, 0x400, 0x4FC, 0x000, 0x4FC, 0xCFC, 0x400, 0x4FC, 0x000, 0x000),
    # "tools": a wrench (the code editor's TLS tool-palette toggle).
    "tools":     (0x000, 0x01C, 0x02E, 0x024, 0x018, 0x030, 0x060, 0x0C0, 0x180, 0x300, 0x200, 0x000),
    # "fill": a handled paint pail over a paint splash (the bucket/flood tool; the
    # map's FLOOD tool reuses it -- one bucket idiom). The handle arc is what keeps
    # it reading as a pail, not a wine glass, at 12px.
    "fill":      (0x000, 0x0E0, 0x110, 0x3F0, 0x210, 0x210, 0x210, 0x210, 0x1E0, 0x0C0, 0x1E0, 0x0C0),
    # "flip_h"/"flip_v": two solid triangles mirrored about the vertical / horizontal
    # axis; "rotate": a broken circle with an arrowhead (turn 90).
    "flip_h":    (0x000, 0x404, 0x60C, 0x71C, 0x7BC, 0x7BC, 0x71C, 0x60C, 0x404, 0x000, 0x000, 0x000),
    "flip_v":    (0x000, 0x3F8, 0x3F8, 0x1F0, 0x0E0, 0x000, 0x0E0, 0x1F0, 0x3F8, 0x3F8, 0x000, 0x000),
    "rotate":    (0x000, 0x0E4, 0x316, 0x40E, 0x41E, 0x800, 0x800, 0x400, 0x410, 0x318, 0x0E0, 0x000),
    # "mirror": two arrowheads pointing INTO a solid vertical axis line -- the
    # paint editor's mirror-draw toggle (#90), a persistent symmetry axis rather
    # than the one-shot flip_h/flip_v transforms above.
    "mirror":    (0x000, 0x060, 0x060, 0x462, 0x666, 0x76E, 0x76E, 0x666, 0x462, 0x060, 0x060, 0x000),
    # "arr_l/r/u/d": solid directional arrows (the paint pixel-shift + music reorder).
    "arr_l":     (0x000, 0x000, 0x040, 0x0C0, 0x1C0, 0x3F8, 0x1C0, 0x0C0, 0x040, 0x000, 0x000, 0x000),
    "arr_r":     (0x000, 0x000, 0x020, 0x030, 0x038, 0x1FC, 0x038, 0x030, 0x020, 0x000, 0x000, 0x000),
    "arr_u":     (0x000, 0x020, 0x070, 0x0F8, 0x1FC, 0x070, 0x070, 0x070, 0x070, 0x000, 0x000, 0x000),
    "arr_d":     (0x000, 0x000, 0x070, 0x070, 0x070, 0x070, 0x1FC, 0x0F8, 0x070, 0x020, 0x000, 0x000),
    # "clear": a trash can (empties the sprite); "rect_tool": a hollow rectangle
    # (the map's box tool); "resize": a diagonal double-arrow (the map WH resize).
    "clear":     (0x000, 0x1F8, 0x7FE, 0x204, 0x2A4, 0x2A4, 0x2A4, 0x2A4, 0x2A4, 0x3FC, 0x1F8, 0x000),
    "rect_tool": (0x000, 0x000, 0x7FC, 0x404, 0x404, 0x404, 0x404, 0x404, 0x7FC, 0x000, 0x000, 0x000),
    "resize":    (0x000, 0x3C0, 0x300, 0x280, 0x040, 0x020, 0x010, 0x014, 0x00C, 0x03C, 0x000, 0x000),
    # The standalone Paint app's tool vocabulary (artwork.py): "eraser" a tilted
    # eraser block over its smear; "picker" the eyedropper; "line" a bare diagonal
    # stroke; "circle" the hollow shape tool; "spray" a can puffing dots; "move"
    # the four-way pan cross. Same Pixelarticons-style provenance as the set above.
    "eraser":    (0x000, 0x078, 0x0FC, 0x1FC, 0x3F8, 0x3F0, 0x7E0, 0x7C0, 0x7C0, 0x380, 0x7E0, 0x000),
    "picker":    (0x01C, 0x03E, 0x03E, 0x07C, 0x0F0, 0x1E0, 0x3C0, 0x380, 0x700, 0x600, 0x400, 0x000),
    "line":      (0x000, 0x006, 0x00E, 0x01C, 0x038, 0x070, 0x0E0, 0x1C0, 0x380, 0x700, 0x600, 0x000),
    "circle":    (0x0F0, 0x30C, 0x204, 0x402, 0x402, 0x402, 0x402, 0x402, 0x402, 0x204, 0x30C, 0x0F0),
    "spray":     (0x314, 0x32A, 0x794, 0x7AA, 0x794, 0x780, 0x780, 0x780, 0x780, 0x780, 0x780, 0x000),
    "move":      (0x060, 0x0F0, 0x1F8, 0x060, 0x462, 0xE67, 0xE67, 0x462, 0x060, 0x1F8, 0x0F0, 0x060),
}


def glyph_runs(kind, fs):
    bits = _GLYPHS.get(kind)
    n = _GLYPH_SIZE
    runs = []
    if bits is not None:
        for r in range(n):
            row = bits[r]
            if not row:
                continue
            yy = r * fs
            run = 0
            for col in range(n):
                if row & (1 << (n - 1 - col)):
                    run += 1
                elif run:
                    runs.append(((col - run) * fs, yy, run * fs))
                    run = 0
            if run:
                runs.append(((n - run) * fs, yy, run * fs))
    return runs


def blit_glyph(cv, kind, rect, c, scale=None):
    x, y, w, h = rect
    fs = int(scale) if scale else getattr(cv, "font_scale", 1)
    if fs < 1:
        fs = 1
    span = _GLYPH_SIZE * fs
    ox = x + (w - span) // 2
    oy = y + (h - span) // 2
    for dx, dy, rw in glyph_runs(kind, fs):
        cv.rect(ox + dx, oy + dy, rw, fs, c)


# --- the primitives step 5 added, as references ---------------------------------


def label(cv, rect, text, ink, align=0):
    x, y, w, h = rect
    adv = 8 * _fs(cv)
    b = fit_bytes(str(text).encode('utf-8'), max(0, w // adv)) if text else b''
    tw = len(b) * adv
    if align == ALIGN_CENTER:
        x += (w - tw) // 2
    elif align == ALIGN_RIGHT:
        x += w - tw
    if b:
        cv.print(b.decode('utf-8'), x, y + (h - adv) // 2, ink)
    return tw


def list(cv, th, rect, items, region, row_h, sel=-1, kind='row_list'):
    region.set(rect, len(items) * row_h)
    x, y, w, h = rect
    fs = _fs(cv)
    cv.clip(x, y, w, h)
    for i in range(len(items)):
        ry = y + i * row_h - region.offset
        if ry + row_h > y and ry < y + h:
            row(cv, th, (x, ry, w - region.BAR_W * fs, row_h), items[i], kind=kind,
                on=i == sel)
    cv.clip()
    region.draw_bar(cv, th)
