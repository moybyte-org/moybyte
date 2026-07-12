"""Shared immediate-mode widget toolkit (visual identity v1, Phase 3).

The ONE place the Open Machine chrome vocabulary is drawn -- buttons, tab rows,
status rows, panels, focus rings -- plus the small layout algebra resizable
windows keep re-deriving (rect cuts/splits/insets), the draw==tap `Hits`
registry, and the one `ScrollRegion` model. Design rules (deliberate, argued in
the redesign thread):

  * IMMEDIATE MODE over the indexed canvas primitives only (rect/rectb/print/
    spr) -- renders identically on the host SystemCanvas, the device canvas,
    and the web RecordingLayer; no retained tree, no per-frame allocation
    beyond small tuples.
  * Widgets take RECTS; the per-surface Layout classes keep owning reflow
    (their frozen 320x240 `_base` branches are the parity contract). The
    combinators here are helpers FOR those classes, not a layout engine.
  * Colors come from the semantic theme tokens (chrome.THEMES -- surface/ink/
    focus/play/author/danger/...), so restyling an app means routing it
    through this module, not hunting literals.
  * Geometry functions are PURE and separate from the draw functions, so a
    surface's tap handler hit-tests the exact rects the draw used (the
    `action_rects` pattern, generalized) -- a cached bar strip can draw once
    while taps keep resolving.

Min-size convention: a window-content layout may expose `min_w` / `min_h`
attributes; the windowed WM clamps resizes to them (wired per-app as surfaces
adopt the toolkit).

MicroPython-safe: tuples/lists/dicts, no f-strings, no stdlib beyond what the
other staged modules already use.
"""

try:
    from chrome import _blit_glyph, _print_scaled, _text_w
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime.chrome import _blit_glyph, _print_scaled, _text_w

_BLACK = 0
_WHITE = 7          # MOY64 cream


def _fs(cv):
    fs = getattr(cv, "font_scale", 1)
    return fs if fs >= 1 else 1


def is_light(th):
    """True when the theme's tool surface is LIGHT (its ink token is dark) --
    THE Phase 3 gate. Surfaces read it through ws.light_chrome() (the layers
    inside the chrome import cycle can't import this module at load time);
    everything else may call it directly."""
    return th.get("ink", 7) == 0


def scroll_cues(cv, up_xy, dn_xy, can_up, can_dn, c, scale=1):
    """The list-overflow chevrons ('^' above, 'v' below) -- one implementation
    for every scrolling surface; positions/color stay the caller's (they are
    part of each surface's frozen geometry)."""
    if can_up:
        cv.print("^", up_xy[0], up_xy[1], c, scale)
    if can_dn:
        cv.print("v", dn_xy[0], dn_xy[1], c, scale)


# --- rect algebra (pure tuple math; every rect is (x, y, w, h)) --------------

def inset(rect, dx, dy=None):
    """Shrink `rect` by dx horizontally and dy (default dx) vertically, clamped
    so the result never has negative size."""
    if dy is None:
        dy = dx
    x, y, w, h = rect
    return (x + dx, y + dy, max(0, w - 2 * dx), max(0, h - 2 * dy))


def cut_top(rect, h):
    """Split off an h-tall band from the top: returns (band, rest)."""
    x, y, w, rh = rect
    h = max(0, min(h, rh))
    return (x, y, w, h), (x, y + h, w, rh - h)


def cut_bottom(rect, h):
    """Split off an h-tall band from the bottom: returns (band, rest)."""
    x, y, w, rh = rect
    h = max(0, min(h, rh))
    return (x, y + rh - h, w, h), (x, y, w, rh - h)


def cut_left(rect, w):
    """Split off a w-wide band from the left: returns (band, rest)."""
    x, y, rw, h = rect
    w = max(0, min(w, rw))
    return (x, y, w, h), (x + w, y, rw - w, h)


def cut_right(rect, w):
    """Split off a w-wide band from the right: returns (band, rest)."""
    x, y, rw, h = rect
    w = max(0, min(w, rw))
    return (x + rw - w, y, w, h), (x, y, rw - w, h)


def hsplit(rect, n, gap=0):
    """`n` equal-width columns across `rect` (integer slack goes to the last)."""
    x, y, w, h = rect
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
    """`n` equal-height rows down `rect` (integer slack goes to the last)."""
    x, y, w, h = rect
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


def rect_in(px, py, rect):
    x, y, w, h = rect
    return x <= px < x + w and y <= py < y + h


# --- draw == tap: the Hits registry ------------------------------------------

class Hits:
    """Per-draw hit registry: the draw pass `add`s each interactive rect with a
    verb (+ optional arg); the pointer handler resolves a tap with `at`. One
    list, reused via clear() -- no per-frame churn."""

    def __init__(self):
        self._items = []

    def clear(self):
        del self._items[:]

    def add(self, rect, verb, arg=None):
        self._items.append((rect, verb, arg))

    def at(self, px, py):
        """(verb, arg) of the topmost hit at (px, py), or None. Later adds win
        (drawn later = on top), matching paint order."""
        for i in range(len(self._items) - 1, -1, -1):
            rect, verb, arg = self._items[i]
            if rect_in(px, py, rect):
                return (verb, arg)
        return None


# --- widgets ------------------------------------------------------------------

# button `kind` -> (bg token, ink literal-or-token). Tokens resolve through the
# theme dict; literals are ints. The mockup's vocabulary: PLAY is signal green
# with cream ink, CHANGE/normal is the warm-light chip with dark ink, authoring
# wears orange, danger red.
_BUTTON_KINDS = {
    "normal": ("surface", "ink"),
    "play": ("play", _WHITE),
    "author": ("author", _BLACK),
    "danger": ("danger", _WHITE),
}


def _resolve(th, token_or_literal, fallback):
    if isinstance(token_or_literal, str):
        return th.get(token_or_literal, fallback)
    return token_or_literal


def button(cv, th, rect, label, kind="normal", on=False, icon_img=None,
           glyph=None):
    """One themed chip button: filled field, thin dark edge, centered label
    (truncated to fit), optional 16x16 icon image or 12x12 glyph at the left.
    `on` swaps to the accent (the pressed/active look)."""
    fs = _fs(cv)
    x, y, w, h = rect
    bg_tok, ink_tok = _BUTTON_KINDS.get(kind, _BUTTON_KINDS["normal"])
    bg = _resolve(th, bg_tok, _WHITE)
    ink = _resolve(th, ink_tok, _BLACK)
    if on:
        bg = th.get("accent", 10)
        ink = _BLACK
    cv.rect(x, y, w, h, bg)
    cv.rectb(x, y, w, h, _BLACK)
    fw = 8 * fs
    pad = 2 * fs
    iw = 0
    if icon_img is not None or glyph is not None:
        iw = (16 if icon_img is not None else 12) * fs + pad
    label = str(label)
    maxc = max(0, (w - 2 * pad - iw) // fw)
    if len(label) > maxc:
        label = label[:maxc]
    tw = iw + len(label) * fw
    tx = x + max(pad, (w - tw) // 2)
    if icon_img is not None:
        cv.spr(icon_img, tx, y + (h - 16 * fs) // 2, fs)
    elif glyph is not None:
        _blit_glyph(cv, glyph, (tx, y, 12 * fs, h), ink, fs)
    if label:
        cv.print(label, tx + iw, y + (h - 8 * fs) // 2, ink, 1)


def chip(cv, th, rect, label, on=False, hot=False, fs=None):
    """The app-toolbar CHIP -- the one implementation of the `_button` the
    Appearance/Writer/Storybook/Artwork apps each used to carry a local copy
    of (pixel-identical to those). A quiet field on the panel color with the
    theme's title ink; `on` swaps to the accent toggle look (edge border);
    `hot` to danger red with light ink (an armed destructive action).

    `chip` vs `button`: chip is the PANEL-chrome vocabulary (toolbars inside
    the dark app chrome, theme-quiet); button is the Open Machine STUDIO
    vocabulary (dark-edged verb chips -- PLAY/CHANGE/SAVE)."""
    if fs is None:
        fs = _fs(cv)
    x, y, w, h = rect
    if hot:
        bg = th.get("danger", 8)
        ink = _WHITE
        edge = th.get("dim", 1)
    elif on:
        bg = th.get("accent", 10)
        ink = _BLACK
        edge = th.get("edge", 13)
    else:
        bg = th.get("panel", 60)
        ink = th.get("title_ink", _BLACK)
        edge = th.get("dim", 1)
    cv.rect(x, y, w, h, bg)
    cv.rectb(x, y, w, h, edge)
    fw = 8 * fs
    label = str(label)
    cv.print(label, x + max(2, (w - len(label) * fw) // 2),
             y + max(1, (h - 8 * fs) // 2), ink, 1)


def tab_row_rects(rect, tabs, fs, gap=None):
    """PURE geometry for a labeled tab row: [(id, rect, labels_on), ...] laid
    left-to-right in `rect`. Overflow policy (resizable windows): if the fully
    labeled row doesn't fit, every tab collapses to its icon-only chip; chips
    that still don't fit are dropped from the END (draw-what-fits, same as the
    frozen icon ladder). `tabs` is a sequence of (id, label) -- icons are the
    draw pass's concern."""
    if gap is None:
        gap = 2 * fs
    x0, y, w, h = rect
    fw = 8 * fs
    pad = 3 * fs
    icon_w = 16 * fs

    def chip_w(label, labels_on):
        if labels_on and label:
            return icon_w + pad + len(str(label)) * fw + pad
        return icon_w + 2 * pad

    labels_on = True
    total = 0
    for _id, label in tabs:
        total += chip_w(label, True) + gap
    if total - gap > w:
        labels_on = False
    out = []
    x = x0
    for tid, label in tabs:
        cw = chip_w(label, labels_on)
        if x + cw > x0 + w:
            break
        out.append((tid, (x, y, cw, h), labels_on))
        x += cw + gap
    return out


def tab_row(cv, th, rect, tabs, active, icon_for=None, hits=None, ink=None):
    """The labeled tab ladder (mockup: Config | Blocks | Code | ...). `tabs` is
    a sequence of (id, label, icon_kind); the ACTIVE tab wears the selection
    color (grape in the shipped themes) with its title ink, inactive tabs stay
    quiet on the field. Registers ("tab", id) hits; returns the geometry list
    so a cached-strip caller can hit-test without redrawing."""
    fs = _fs(cv)
    icons = {}
    slim = []
    for tid, label, icon_kind in tabs:
        slim.append((tid, label))
        icons[tid] = icon_kind
    rects = tab_row_rects(rect, slim, fs)
    for tid, r, labels_on in rects:
        x, y, w, h = r
        on = (tid == active)
        if on:
            cv.rect(x, y, w, h, th.get("selection", th.get("hilite", 13)))
        ink_i = th.get("title_ink", _BLACK) if on else (
            ink if ink is not None else _WHITE)
        img = icon_for(icons[tid]) if icon_for is not None else None
        pad = 3 * fs
        if img is not None:
            cv.spr(img, x + (pad if labels_on else (w - 16 * fs) // 2),
                   y + (h - 16 * fs) // 2, fs)
        if labels_on:
            cv.print(str(_tab_label(tabs, tid)),
                     x + pad + 16 * fs + pad, y + (h - 8 * fs) // 2, ink_i, 1)
        if hits is not None:
            hits.add(r, "tab", tid)
    return rects


def _tab_label(tabs, tid):
    for t, label, _icon in tabs:
        if t == tid:
            return label
    return ""


def status_row(cv, th, rect, items):
    """The window status band ("Ln 13, Col 1" / "No issues"): a quiet strip on
    the alt surface with dim ink, items spaced left-to-right."""
    fs = _fs(cv)
    x, y, w, h = rect
    cv.rect(x, y, w, h, th.get("surface_alt", th.get("panel", 60)))
    cv.rect(x, y, w, 1 * fs, th.get("border", _BLACK))
    ink = th.get("ink_dim", 6)
    fw = 8 * fs
    tx = x + 3 * fs
    ty = y + (h - 8 * fs + 1 * fs) // 2
    for item in items:
        s = str(item)
        if tx + len(s) * fw > x + w:
            break
        cv.print(s, tx, ty, ink, 1)
        tx += (len(s) + 2) * fw


def toolbar(cv, th, rect):
    """The app toolbar band (Writer/Storybook): the theme's title surface --
    chips and status text draw over it in title_ink."""
    x, y, w, h = rect
    cv.rect(x, y, w, h, th.get("title", 13))
    return rect


def dialog(cv, rect, ring=_WHITE, fill=2):
    """The dark MODAL panel shell (the blocks prompts/insert menu, the system
    menu, the graduation banner): dark-purple base + a colored frame. Geometry
    stays the caller's (their hit rects are test-pinned constants); only the
    shell drawing is shared."""
    x, y, w, h = rect
    cv.rect(x, y, w, h, fill)
    cv.rectb(x, y, w, h, ring)


def text_field(cv, rect, text, placeholder=""):
    """The modal prompts' text-entry field at the game canvas's fixed 1x metrics
    (both block prompts draw there): black field, light-grey ring, cream text or
    a dim placeholder, and the yellow caret bar after the text."""
    x, y, w, h = rect
    cv.rect(x, y, w, h, _BLACK)
    cv.rectb(x, y, w, h, 6)
    if text:
        cv.print(text, x + 4, y + 3, _WHITE, 1)
    elif placeholder:
        cv.print(placeholder, x + 4, y + 3, 5, 1)
    cv.rect(x + 4 + len(text) * 8, y + 3, 6, 8, 10)


def panel(cv, th, rect, title=None, fs=None):
    """A tool panel: the warm surface + thin border, optionally a compact title
    strip (title bg / title ink tokens). Returns the CONTENT rect (inside the
    border, below any title strip)."""
    if fs is None:
        fs = _fs(cv)
    x, y, w, h = rect
    cv.rect(x, y, w, h, th.get("surface", th.get("panel", 60)))
    for i in range(fs):
        cv.rectb(x - i, y - i, w + 2 * i, h + 2 * i, th.get("border", _BLACK))
    content = inset(rect, 1, 1)
    if title:
        strip_h = 12 * fs
        strip, content = cut_top(content, strip_h)
        sx, sy, sw, sh = strip
        cv.rect(sx, sy, sw, sh, th.get("title", 13))
        cv.print(str(title), sx + 3 * fs, sy + (sh - 8 * fs) // 2,
                 th.get("title_ink", _BLACK), 1)
    return content


def focus_ring(cv, th, rect, fs=None):
    """The Section 5.2 focus treatment: signal-yellow ring with a 1-step gap --
    one implementation so keyboard/pointer focus looks the same everywhere."""
    if fs is None:
        fs = _fs(cv)
    x, y, w, h = rect
    color = th.get("focus", 10)
    for i in range(max(2, fs)):
        d = fs + 1 + i
        cv.rectb(x - d, y - d, w + 2 * d, h + 2 * d, color)


# --- scrolling ------------------------------------------------------------------

class ScrollRegion:
    """The one scroll model: a view rect over taller content. The caller draws
    rows at (row_y - self.offset) and clips to the view; this owns the offset
    bookkeeping, clamping, wheel/drag deltas, and the slim scrollbar (drawn +
    hit through the same geometry)."""

    BAR_W = 4           # scaled by fs at draw

    def __init__(self):
        self.view = (0, 0, 0, 0)
        self.content_h = 0
        self.offset = 0
        self._drag_y = None

    def set(self, view_rect, content_h):
        self.view = view_rect
        self.content_h = max(0, int(content_h))
        self._clamp()

    def _max_offset(self):
        return max(0, self.content_h - self.view[3])

    def _clamp(self):
        m = self._max_offset()
        if self.offset < 0:
            self.offset = 0
        elif self.offset > m:
            self.offset = m

    def scroll_by(self, dy):
        self.offset += int(dy)
        self._clamp()

    def scroll_to_show(self, y, h):
        """Nudge the offset so the content span [y, y+h) is inside the view."""
        vh = self.view[3]
        if y < self.offset:
            self.offset = y
        elif y + h > self.offset + vh:
            self.offset = y + h - vh
        self._clamp()

    # -- drag (touch) ------------------------------------------------------

    def drag_start(self, py):
        self._drag_y = py

    def drag_move(self, py):
        if self._drag_y is None:
            return False
        self.scroll_by(self._drag_y - py)
        self._drag_y = py
        return True

    def drag_end(self):
        self._drag_y = None

    # -- scrollbar -----------------------------------------------------------

    def bar_rect(self, fs=1):
        """The scrollbar thumb rect, or None when everything fits."""
        x, y, w, h = self.view
        m = self._max_offset()
        if m <= 0 or h <= 0:
            return None
        bw = self.BAR_W * fs
        th_ = max(8 * fs, h * h // self.content_h)
        ty = y + (h - th_) * self.offset // m
        return (x + w - bw, ty, bw, th_)

    def draw_bar(self, cv, th):
        fs = _fs(cv)
        r = self.bar_rect(fs)
        if r is None:
            return
        x, y, w, h = self.view
        bw = self.BAR_W * fs
        cv.rect(x + w - bw, y, bw, h, th.get("dim", 1))
        cv.rect(r[0], r[1], r[2], r[3], th.get("ink_dim", 6))
