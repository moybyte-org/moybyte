# Map (grep -n a name to jump there):
#   is_light                          the theme's tool-surface presentation class
#   wrap_words                        the text path's wrap (C); text_width beside it
#   -- rect algebra                   inset, cut_top, cut_bottom, cut_left, split
#   -- interaction state              widget_state
#   -- the style                      use_skin, metrics, state_colors (C)
#   -- the theme's shape              use_shape, paint (C)
#   -- draw == tap                    Hits
#   Hits                              per-draw hit registry
#   -- glyphs                         glyph, glyph_id (C); s, the string table (C)
#   -- widgets                        button, chip
#   -- list rows and grid cells       row, cell
#   -- the classic game-canvas button family  game_btn, mini_btn, toolbar
#   -- scrolling                      row_drag, ScrollRegion, DragTap
#   ScrollRegion                      the one scroll model
#   DragTap                           press/drag/release over a ScrollRegion
"""Shared immediate-mode widget toolkit (visual identity v1, Phase 3).

The ONE place the Open Machine chrome vocabulary is drawn -- buttons, tab rows,
status rows, panels, focus rings -- plus the small layout algebra resizable
windows keep re-deriving (rect cuts/splits/insets), the draw==tap `Hits`
registry, and the one `ScrollRegion` model. Design rules (deliberate, argued in
the redesign thread):

  * IMMEDIATE MODE, and the bodies are C: each widget is one call into
    native/moy_ui (docs/kernel_toolkit_2026-10.md), which draws into the
    canvas's CANVAS row through the kernels the canvas verbs end in -- so it
    renders identically on the host, every board and the browser, and a
    compiled app draws the same widgets. No retained tree, no per-frame
    allocation beyond small tuples. tests/ui_twin.py keeps the Python bodies
    as the oracle.
  * Widgets take RECTS; the per-surface Layout classes keep owning reflow
    (their frozen 320x240 `_base` branches are the parity contract). The
    combinators here are helpers FOR those classes, not a layout engine.
  * Colors come from the semantic theme tokens (chrome.theme_colors -- surface/ink/
    focus/play/author/danger/...), so restyling an app means routing it
    through this module, not hunting literals.
  * STYLE IS DATA, not code. Every per-state colour and every number a draw
    used to hardcode -- pads, edge weights, strip heights, icon boxes, label
    alignment -- is native/moy_ui/style.json, resolved by native/moy_ui's C
    against the theme (the style section below is its face, and caches what
    it answers so a widget pays one identity compare and two dict gets and
    allocates nothing). A skin is a delta in that file, installed by name
    (`use_skin`). Four frozen quirks stayed code; style.json's "quirks"
    records them.
  * Geometry functions are PURE and separate from the draw functions, so a
    surface's tap handler hit-tests the exact rects the draw used (the
    `action_rects` pattern, generalized) -- a cached bar strip can draw once
    while taps keep resolving.
  * INTERACTION STATE belongs to the registry; SEMANTICS belong to the caller.
    Six states resolve in ONE place with the precedence
    `disabled > pressed > hot > on > hover > rest`: `on`/`hot`/`disabled` stay
    caller ARGUMENTS (they are meaning -- only the surface knows them), while
    `hover`/`pressed` come from `Hits`'s pointer pump (they are interaction --
    only the registry may write them). `rest` is byte-identical to what the
    goldens already know: every state cue is a delta painted ON the rest look,
    resolved through the theme's own semantic roles, never a new literal.
  * `Hits` IS FOR BOUNDED WIDGET SETS -- toolbars, button rows, list rows --
    and deliberately NOT for grids. That is MEASURED, not stylistic: making
    the registry "the ONE state holder per surface" would have the P4's map
    tab register ~395 rects per full draw (~9.3ms, 13% of that tab's frame)
    where today it registers zero. A grid keeps its arithmetic hit-test
    (`_cell_rect(i)` + `rect_in`) and feeds `cell(state=cell_state(i, hov,
    prs))`; that is why `cell` takes no `hits` argument at all and therefore
    CANNOT register one rect per cell. Do not "unify" this away.

Min-size convention: a window-content layout may expose `MIN_W` / `MIN_H`
constants; app registration adopts them and the windowed WM clamps resizes to
the registered floor.

MicroPython-safe: tuples/lists/dicts, no f-strings, and no imports from the
shell/surface graph. Glyph drawing is injected by callers that want it, keeping
this toolkit a leaf module that chrome and every surface can safely import.
(The imports below are moy_ui, the C this module is the face of, and widgets --
a peer leaf, so no cycle: the rect hit-test has exactly one definition,
widgets._in, re-exported here as `rect_in`.)
"""

from array import array

try:
    from struct import unpack_from as _unpack
except ImportError:  # pragma: no cover - a port with only ustruct
    from ustruct import unpack_from as _unpack

import moy_ui as _C

try:
    from widgets import _in as rect_in
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime.widgets import _in as rect_in

_BLACK = 0
_WHITE = 7          # MOY64 cream


def _fs(cv):
    fs = getattr(cv, "font_scale", 1)
    return fs if fs >= 1 else 1


def is_light(th):
    """The theme's explicit tool-surface presentation class. Color indices are
    not luminance: a future light theme may use navy/brown ink rather than index
    0, so surfaces must not infer this branch from one palette value."""
    return bool(th.get("surface_light", False))


def scroll_cues(cv, up_xy, dn_xy, can_up, can_dn, c, scale=1):
    """The list-overflow chevrons ('^' above, 'v' below) -- one implementation
    for every scrolling surface; positions/color stay the caller's (they are
    part of each surface's frozen geometry)."""
    _C.scroll_cues(cv, up_xy, dn_xy, can_up, can_dn, c)


def wrap_words(text, maxc):
    """Greedy word-wrap of `text` into lines of at most `maxc` BYTES, words
    joined by single spaces (an over-long single word gets its own line,
    truncated at a codepoint). The one wrap: native/moy_gfx/moy_text.h."""
    if type(text) is not str:
        text = str(text)
    return _C.wrap(text, maxc)


def text_width(s, scale=1):
    """The pixels `s` draws at `scale`: UTF-8 bytes, a cell each."""
    if type(s) is not str:
        s = str(s)
    return _C.text_width(s, scale)


# --- rect algebra (pure tuple math; every rect is (x, y, w, h)) --------------

def inset(rect, dx, dy=None):
    """Shrink `rect` by dx horizontally and dy (default dx) vertically, clamped
    so the result never has negative size."""
    return _C.inset(rect, dx, dx if dy is None else dy)


def cut_top(rect, h):
    """Split off an h-tall band from the top: returns (band, rest)."""
    return _C.cut(rect, 0, h)


def cut_bottom(rect, h):
    """Split off an h-tall band from the bottom: returns (band, rest)."""
    return _C.cut(rect, 1, h)


def cut_left(rect, w):
    """Split off a w-wide band from the left: returns (band, rest)."""
    return _C.cut(rect, 2, w)


def cut_right(rect, w):
    """Split off a w-wide band from the right: returns (band, rest)."""
    return _C.cut(rect, 3, w)


def hsplit(rect, n, gap=0):
    """`n` equal-width columns across `rect` (integer slack goes to the last)."""
    if n <= 1:
        return [rect]
    return _C.split(rect, n, gap, True)


def vsplit(rect, n, gap=0):
    """`n` equal-height rows down `rect` (integer slack goes to the last)."""
    if n <= 1:
        return [rect]
    return _C.split(rect, n, gap, False)


# (rect_in is widgets._in, imported at the top -- one hit-test definition.)


# --- interaction state: the six-state model -----------------------------------
#
# One vocabulary, one precedence, one resolution point. The names are module
# CONSTANTS so a comparison costs no allocation and a typo raises AttributeError
# instead of silently comparing false forever.

REST = "rest"
HOVER = "hover"
ON = "on"
HOT = "hot"
PRESSED = "pressed"
DISABLED = "disabled"

# Precedence, strongest first. `disabled` wins over everything (a widget that
# cannot be used must never look armed, however the cursor is sitting on it);
# `pressed` beats the semantics because on a TOUCH tier it is the only feedback
# a finger ever gets; `hover` is the weakest and loses to every semantic state,
# so a selected row does not flicker under a passing cursor.
STATES = (DISABLED, PRESSED, HOT, ON, HOVER, REST)


def widget_state(on=False, hot=False, disabled=False, interact=None):
    """Resolve the six states to one, per STATES's precedence.

    `on`/`hot`/`disabled` are the CALLER's semantics (a toggle, an armed
    destructive action, an unusable verb); `interact` is whatever
    `Hits.state_of` returned for this widget's id -- HOVER, PRESSED or None --
    and the registry's pump is the only thing allowed to produce it.
    """
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


# --- the style: data in native/moy_ui, resolved in C ---------------------------
#
# The look and the numbers are DATA: native/moy_ui/style.json, generated into
# the C tables native/moy_ui resolves (docs/kernel_toolkit_2026-10.md §2). A
# kind's (field, ink, edge) in each state is a chain of token specs resolved
# against the theme's token row, and a skin is a delta over the default entry,
# installed by name (`use_skin`). Nothing in this module holds a colour or a
# pad.
#
# What stays here is the FACE: `state_colors` hands back a shared tuple from a
# cache keyed on the theme dict's identity, the kind and the state, so a widget
# draw allocates nothing and crosses into C only on the first draw of a kind
# under a theme (and again after a skin install). A theme dict is packed into
# one of the C side's four looks the first time it is seen; the ring keeps the
# four most recent, because several surfaces pass `self.theme or {}` and an
# EMPTY DICT IS A FRESH OBJECT every call, which must not evict the live theme.

# Label alignment (a paint choice, never a geometry one).
ALIGN_LEFT = 0
ALIGN_CENTER = 1
ALIGN_RIGHT = 2

# --- metric slots -------------------------------------------------------------
# One tuple per kind (`metrics(kind)`); the constants are its indices, and
# style.json names the slots in this order. Numbers are in UNSCALED units and
# the `* fs` stays in the draw, except where style.json's quirks say not.
BTN_PAD, BTN_ICON, BTN_GLYPH, BTN_EDGE, BTN_ALIGN = 0, 1, 2, 3, 4
CHIP_CLIP, CHIP_MINX, CHIP_MINY, CHIP_EDGE, CHIP_ALIGN = 0, 1, 2, 3, 4
ROW_PAD, ROW_ICON, ROW_GLYPH, ROW_GAP, ROW_EDGE = 0, 1, 2, 3, 4
CELL_PAD, CELL_CAP, CELL_INDENT, CELL_ICON, CELL_GLYPH, CELL_EDGE = (
    0, 1, 2, 3, 4, 5)
TAB_GAP, TAB_PAD, TAB_ICON = 0, 1, 2
ST_RULE, ST_PAD, ST_GAP, ST_DY = 0, 1, 2, 3
PANEL_EDGE, PANEL_INSET, PANEL_STRIP, PANEL_PAD = 0, 1, 2, 3
FOCUS_RINGS, FOCUS_GAP = 0, 1
SB_W, SB_MIN = 0, 1
TF_PADX, TF_PADY, TF_CARET_W, TF_CARET_H = 0, 1, 2, 3
TF_FIELD, TF_RING, TF_INK, TF_HINT, TF_CARET = 4, 5, 6, 7, 8
GB_PAD, GB_RING, GB_INK = 0, 1, 2
GI_PAD, GI_GLYPH, GI_LABEL, GI_RING, GI_INK = 0, 1, 2, 3, 4
MB_PADX, MB_PADY, MB_INK = 0, 1, 2
DLG_EDGE, DLG_FILL, DLG_RING = 0, 1, 2

_TOKENS = _C.tokens()                 # the token row's order (moy_app's vocabulary)
_ABSENT = -2147483648
_STATE_ID = {}
for _i, _s in enumerate(_C.states()):
    _STATE_ID[_s] = _i
_REST_ID = _STATE_ID[REST]
_KIND_ID = {}                         # kind name -> id, interned at first use
_METRICS = {}                         # kind -> the active skin's tuple
_RING = []                            # [theme dict, look slot, cache], newest first
_RING_MAX = 4
_TH = None                            # the theme dict _FLAT caches for
_LOOK = 0
_FLAT = {}                            # kind -> {state: (field, ink, edge)}


def _load_metrics():
    m = {}
    for name in _C.metric_kinds():
        m[name] = _C.metrics(name)
    _METRICS.clear()
    _METRICS.update(m)
    ScrollRegion.BAR_W = m["scrollbar"][SB_W]


def skin_names():
    """The skins, in presentation order -- what a picker lists."""
    return _C.skins()


def active_skin():
    """The installed skin's name."""
    return _C.skin()


def use_skin(name):
    """Install skin `name` (an unknown name installs the default, as
    `chrome.theme_colors` does for an unknown theme). Returns the name that
    was installed, so a caller persists the resolved value. Every cached
    colour and metric is dropped: the next draw of each kind resolves again,
    and `ScrollRegion.BAR_W` follows the scrollbar's width."""
    global _TH, _FLAT
    got = _C.skin(name)
    # The ring goes whole, as the theme dicts are re-packed on their next draw:
    # a dict a caller refilled in place since it was packed must not keep
    # resolving against the row it had then.
    del _RING[:]
    _TH = None
    _FLAT = {}
    _load_metrics()
    return got


def kinds():
    """The kinds a style entry colours."""
    return _C.kinds()


def metrics(kind):
    """The active skin's metric tuple for `kind`; index it with the slot
    constants above."""
    return _METRICS[kind]


_BUTTON_KIND = {}                     # button(kind=...) -> the style kind
_BUTTON_KID = {}                      # button(kind=...) -> its kind id


def _button_kind(kind):
    k = _BUTTON_KIND.get(kind)
    if k is None:
        k = _BUTTON_KIND[kind] = _C.kinds()[_C.button_kind(kind)]
    return k


def _kind_id(kind):
    k = _KIND_ID.get(kind)
    if k is None:
        k = _KIND_ID[kind] = _C.kind(kind)
    return k


def _pack(th):
    row = array("i", [_ABSENT] * len(_TOKENS))
    for i in range(len(_TOKENS)):
        v = th.get(_TOKENS[i])
        if v is not None:
            row[i] = int(v)
    return row


def _bind_theme(th):
    """Make `th` the theme the cache answers for, reusing its look when the
    ring still has it."""
    global _TH, _FLAT, _LOOK
    ring = _RING
    for i in range(len(ring)):
        ent = ring[i]
        if ent[0] is th:
            if i:
                del ring[i]
                ring.insert(0, ent)
            _TH, _LOOK, _FLAT = th, ent[1], ent[2]
            return
    if len(ring) < _RING_MAX:
        slot = len(ring)
    else:
        slot = ring.pop()[1]
    _C.look(slot, _pack(th))
    ent = [th, slot, {}]
    ring.insert(0, ent)
    _TH, _LOOK, _FLAT = th, slot, ent[2]


def _colors_slow(kind, state):
    t = _C.colors(_LOOK, _kind_id(kind), _STATE_ID.get(state, _REST_ID))
    ent = _FLAT.get(kind)
    if ent is None:
        ent = _FLAT[kind] = {}
    ent[state] = t
    return t


def state_colors(th, kind, state):
    """(field, ink, edge) for widget `kind` in `state`, as palette indices
    (None: paint nothing), resolved by native/moy_ui against `th`.

    The hot path is an identity compare plus two dict gets, and it returns
    the SHARED tuple -- it allocates nothing. Treat the result as READ-ONLY."""
    if th is not _TH:
        _bind_theme(th)
    try:
        return _FLAT[kind][state]
    except KeyError:
        return _colors_slow(kind, state)


def cell_state(index, hover_index=-1, pressed_index=-1):
    """A GRID's arithmetic answer to `Hits.state_of` -- O(1), no registration.

    See the module docstring: a grid must not put one hit rect per cell into
    `Hits`. It already hit-tests arithmetically to find the cell under a tap
    (`FileGridView.tap`, `cards_layer._choice_cells`); it feeds the two indices
    it derived the same way here and pays nothing per cell.
    """
    # Grids spell "nothing selected" as -1 (FileGridView.sel), and so do the
    # sentinels here -- so a negative index must never match itself into a cue.
    if index < 0:
        return None
    if index == pressed_index:
        return PRESSED
    if index == hover_index:
        return HOVER
    return None


# --- draw == tap: the Hits registry ------------------------------------------

# Flag duplicate ids at registration. OFF in release (last registered wins, as
# it always did); a dev/test build flips it and gets the ambiguity as an
# exception at the draw that caused it, rather than as a cue attaching to the
# wrong widget three surfaces later.
HITS_DEBUG = False


# The hit buffer's capacity: twice the most rects any surface registers in the
# goldens' frames (native/moy_ui/style.json's hits_capacity).
HITS_CAP = _C.hits_cap()


class Hits:
    """Per-draw hit registry AND the surface's interaction-state holder.

    The draw pass `add`s each interactive rect with a verb (+ optional arg);
    the pointer handler resolves a tap with `at` and pumps hover/pressed with
    `pointer_frame`; the next draw reads the states back with `state_of`. One
    list, reused via `clear()` -- no per-frame churn.

    SCOPE: bounded widget sets only (toolbars, button rows, list rows). A grid
    must NOT register one rect per cell -- see the module docstring for the
    measurement, and `cell_state` for what a grid does instead.

    The rules below are each a #177 hover-shelf bug made structural, so that
    the same four bugs cannot be rediscovered once per surface again:

      * **Ids are the draw's `(verb, arg)` pairs.** Duplicates are harmless for
        taps (topmost-at-point wins) but AMBIGUOUS for `state_of`, so
        `HITS_DEBUG` raises on registering one; last registered wins in release.
      * **Rects live from one full draw to the next.** `clear()` wipes them;
        hover/pressed ids PERSIST across clears and re-resolve against the fresh
        rects on the next pointer sample. So a parked cursor re-seeds on the
        next SAMPLE (a repeat sample at the same point is enough) rather than on
        first sighting -- which is the stale-after-relayout bug closed.
      * **Coordinates are surface-local** -- exactly what the draw registered.
        (Window-local vs screen coords was its own shelf bug.)
      * **Hover requires a POINTING cursor**: `visible or hovering, and not
        down`. Touch never hovers -- it places the pointer hidden -- but touch
        DOES press. Pressed clears on release, and while the finger is outside
        the rect it went down on.
      * **Leave is the router's job.** Whichever component stops feeding a
        surface pointer samples (`wm_windowed`, the fullscreen stack, an app
        hosting sub-layouts) MUST call `pointer_leave()`, or the cue it left
        behind is stale forever.
      * **The pump is the ONLY writer** of `hover`/`pressed`.

    What the pump deliberately does NOT own is hover-as-SELECTION. A surface
    whose hover moves the selection (Settings rows, the file grids) is doing
    app state: it repaints through the ordinary dirty path and keeps its own
    small glue. The pump only says where the pointer is.
    """

    def __init__(self, cap=None):
        cap = HITS_CAP if cap is None else cap
        # The rects live in a buffer this object owns, the rules in C
        # (native/moy_ui); ids are interned per clear().
        self._buf = array("i", bytes(4 * (2 + 5 * cap)))
        self._keys = []         # id -> (verb, arg), this draw's
        self._ids = {}          # (verb, arg) -> id
        self._pump = array("i", [-1, -1, -1, 0])
        self._said_full = False
        self.hover = None       # (verb, arg) under a resting pointing cursor
        self.pressed = None     # (verb, arg) held down, pointer still inside
        self._armed = None      # what the press EDGE landed on
        self._down = False      # last sample's held state (for the edge)

    def clear(self):
        _C.hits_clear(self._buf)
        del self._keys[:]
        self._ids.clear()

    def add(self, rect, verb, arg=None):
        key = (verb, arg)
        i = self._ids.get(key)
        if i is None:
            i = len(self._keys)
            self._keys.append(key)
            self._ids[key] = i
        elif HITS_DEBUG:
            raise ValueError(
                "duplicate Hits id (%r, %r): state_of() would be "
                "ambiguous" % (verb, arg))
        if not _C.hits_add(self._buf, rect, i):
            self._full(key)

    def _full(self, key):
        """An add past the buffer's capacity: refused, said once per surface,
        and an error under HITS_DEBUG -- a tap must never be lost silently."""
        if HITS_DEBUG:
            raise OverflowError("Hits full at %d rects: %r not registered"
                                % (len(self._keys), key))
        if not self._said_full:
            self._said_full = True
            print("HITS FULL: %d rects, %r and later ones not registered"
                  % (len(self._keys), key))

    @property
    def _items(self):
        """The registered (rect, verb, arg)s, in order -- for tests."""
        b = self._buf
        out = []
        for i in range(b[0]):
            r = b[2 + 5 * i:7 + 5 * i]
            out.append(((r[0], r[1], r[2], r[3]), self._keys[r[4]][0],
                        self._keys[r[4]][1]))
        return out

    def at(self, px, py):
        """(verb, arg) of the topmost hit at (px, py), or None. Later adds win
        (drawn later = on top), matching paint order."""
        i = _C.hits_at(self._buf, px, py)
        return self._keys[i] if i >= 0 else None

    # -- the interaction pump --------------------------------------------------

    def pointer_frame(self, px, py, pointer):
        """ONE pump for hover AND pressed; True when either changed.

        Call it once per pointer sample from the surface's pointer handler
        (before or after the tap routing -- it reads, it never consumes), and
        mark the surface dirty on True. `pointer` is duck-typed: `.down` and
        `.visible`, plus the browser/mouse `.hovering` flag when a tier has one
        (today's `moy_input.Pointer` does not, so a tier hovers exactly when its
        cursor is visible -- trackball and mouse yes, touch never).
        """
        down = bool(getattr(pointer, "down", False))
        pointing = bool(getattr(pointer, "visible", False)
                        or getattr(pointer, "hovering", False))
        p = self._pump
        armed = self._armed
        p[2] = -1 if armed is None else self._ids.get(armed, -1)
        p[3] = 1 if self._down else 0
        _C.hits_pump(self._buf, px, py, down, pointing, p)
        keys = self._keys
        new_hover = keys[p[0]] if p[0] >= 0 else None
        new_pressed = keys[p[1]] if p[1] >= 0 else None
        if not down:
            self._armed = None
        elif not self._down:
            self._armed = keys[p[2]] if p[2] >= 0 else None   # the press EDGE
        self._down = down
        changed = (new_hover != self.hover) or (new_pressed != self.pressed)
        self.hover = new_hover
        self.pressed = new_pressed
        return changed

    def pointer_leave(self):
        """The router stopped feeding this surface: drop both cues. True when
        that changed anything, so a stale cue costs exactly one repaint and an
        already-quiet surface costs none.

        It forgets the press-edge HISTORY too: after a leave this surface has
        no pointer past, so the next sample it receives is a fresh edge. That
        is deliberate -- on a touch tier the first sample a newly-focused
        surface sees IS the finger going down, and refusing to arm there would
        cost exactly the feedback `pressed` exists to give.
        """
        changed = self.hover is not None or self.pressed is not None
        self.hover = None
        self.pressed = None
        self._armed = None
        self._down = False
        return changed

    def state_of(self, verb, arg=None):
        """PRESSED / HOVER / None for one widget id, read back at draw. Takes
        the id UNPACKED so a query allocates nothing (the ids themselves are
        the two tuples the pump already built)."""
        p = self.pressed
        if p is not None and p[0] == verb and p[1] == arg:
            return PRESSED
        h = self.hover
        if h is not None and h[0] == verb and h[1] == arg:
            return HOVER
        return None


# --- glyphs: the icon vocabulary, drawn by native/moy_ui ------------------------
#
# The 12x12 glyphs are style.json's "glyphs"; one C raster derives each one's
# spans at draw time, at any scale, and draws them into the canvas's row. A
# widget given `glyph=` draws it through here unless the caller passes its own
# `glyph_draw(kind, rect, color, cv)`, which then REPLACES the label (the code
# editor's chip faces).

_GLYPH_ID = {}                        # kind -> id, interned at first use


def s(sid):
    """The text of string id `sid` (native/moy_spine/strings/en.json): the
    language pack's entry, else English."""
    return _C.s(sid)



def glyph_id(kind):
    """The glyph's id, or -1 when the vocabulary has no such glyph -- the
    question "is it a glyph" every caller with a label fallback asks."""
    g = _GLYPH_ID.get(kind)
    if g is None:
        g = _C.glyph_id(kind) if type(kind) is str else -1
        _GLYPH_ID[kind] = g
    return g


def glyph(cv, kind, rect, c, scale=None):
    """Draw glyph `kind` centred in `rect` in colour `c` (no background), each
    glyph pixel a `scale` square -- the canvas's font scale by default. An
    unknown kind draws NOTHING, so a caller keeps its text label as the
    fallback."""
    g = _GLYPH_ID.get(kind)
    if g is None:
        g = glyph_id(kind)
    if g >= 0:
        _C.glyph(cv, g, rect, c, scale or None)


def _glyph_draw(kind, rect, c, cv):
    glyph(cv, kind, rect, c)


# The default glyph drawer, as a `glyph_draw=` a caller passes to mean "the
# toolkit's own raster" (game_icon_btn's, which draws no glyph without one).
glyph_draw = _glyph_draw


# --- the C primitives' face ------------------------------------------------------
#
# A widget below is one call into native/moy_ui with the canvas itself, the
# look its theme resolves in, the kind's id, the state flags and its rect,
# text and colours as the caller has them: the binding converts coordinates
# toward zero (as the print gate does), masks colours as the canvas does and
# lands a pending sprite batch first. On a board a Python call costs more than
# most widgets draw, so a face makes one.

_F_ON, _F_HOT, _F_DISABLED, _F_EDGE = 1, 2, 4, 8
_F_BAND, _F_EDGE_LAST, _F_LABEL, _F_ICON, _F_GLYPH = 16, 32, 64, 128, 256
_FRAME, _CONTENT, _ALL = 1, 2, 3


def _look(th):
    """The C look `th` resolves in (its ring slot), binding it on a miss. A
    caller with no theme passes its own colours; any look serves it."""
    if th is None:
        return 0
    if th is not _TH:
        _bind_theme(th)
    return _LOOK


def label(cv, rect, text, ink, align=ALIGN_LEFT):
    """Text in a box at the canvas's font scale: truncated to what fits (at a
    codepoint), placed by `align`, centred vertically. Returns the drawn
    width."""
    return _C.label(cv, rect, text, ink, align)


def list(cv, th, rect, items, region, row_h, sel=-1, kind="row_list"):
    """A scrolling list: the `items` (strs) as `kind` rows `row_h` apart in
    `rect`, scrolled by `region` (a ScrollRegion this sets to the list's
    extent), `sel` the selected row, and the region's bar."""
    region.set(rect, len(items) * row_h)
    _C.list(cv, _LOOK if th is _TH else _look(th), rect, _kind_id(kind),
            "\0".join(items).encode("utf-8"), len(items), row_h, region.offset, sel,
            region.BAR_W)


# --- widgets ------------------------------------------------------------------
#
# Every draw below reads its look from `state_colors` and its numbers from
# `_METRICS`. The only literals left in a draw body are the FONT CELL (8, the
# petme128 advance -- geometry, not style) and the frozen quirks recorded in
# style.json's "quirks".


def button(cv, th, rect, label, kind="normal", on=False, icon_img=None,
           glyph=None, glyph_draw=None, disabled=False, state=None):
    """One themed chip button: filled field, thin dark edge, centered label
    (truncated to fit), optional 16x16 icon image or 12x12 glyph at the left.
    A glyph is drawn by `glyph` unless the caller passes its own
    `glyph_draw(kind, rect, color, cv)`;
    `on` swaps to the accent (the pressed/active look).

    `disabled` + `state` are the six-state model (see `widget_state`): they
    default to the untouched REST/`on` rendering the goldens pin, and only a
    caller that opts in pays anything. `state` is what `Hits.state_of` returned
    for this button's id.

    The field/ink/edge come from the skin under the key `kind` maps to
    (style.json's "button_kinds"), the pads/boxes/alignment from
    `_METRICS["button"]`."""
    flags = (_F_ON if on else 0) | (_F_DISABLED if disabled else 0)
    gid = -1
    if icon_img is not None:
        flags |= _F_ICON
    elif glyph is not None:
        flags |= _F_GLYPH
        if glyph_draw is None:
            gid = glyph_id(glyph)
    look = _LOOK if th is _TH else _look(th)
    kid = _BUTTON_KID.get(kind)
    if kid is None:
        kid = _BUTTON_KID[kind] = _kind_id(_button_kind(kind))
    act = -1 if state is None else _STATE_ID.get(state, -1)
    if icon_img is None and (glyph is None or glyph_draw is None):
        _C.button(cv, look, rect, kid, flags, act, label, gid, _ALL)
        return
    _C.button(cv, look, rect, kid, flags, act, label, gid, _FRAME)
    fs = _fs(cv)
    tx = _C.button_x(rect, flags, label, fs)
    y, h = int(rect[1]), int(rect[3])
    m = _METRICS["button"]
    if icon_img is not None:
        box = m[BTN_ICON] * fs
        cv.spr(icon_img, tx, y + (h - box) // 2, fs)
    else:
        ink = state_colors(th, _button_kind(kind),
                           widget_state(on, False, disabled, state))[1]
        glyph_draw(glyph, (tx, y, m[BTN_GLYPH] * fs, h), ink, cv)
    _C.button(cv, look, rect, kid, flags, act, label, gid, _CONTENT)


def chip(cv, th, rect, label, on=False, hot=False, fs=None,
         glyph=None, glyph_draw=None, disabled=False, state=None,
         colors=None, kind="chip"):
    """The app-toolbar CHIP -- the one implementation of the `_button` the
    Appearance/Storybook/Artwork apps each used to carry a local copy
    of (pixel-identical to those). A quiet field on the panel color with the
    theme's title ink; `on` swaps to the accent toggle look (edge border);
    `hot` to danger red with light ink (an armed destructive action).

    `glyph` (a glyph kind, drawn by `glyph`, or the caller's own
    `glyph_draw(kind, rect, color, cv)`): draw the icon centered in the chip
    INSTEAD of the text label. The label still names the
    chip at the call site (and is the fallback when no glyph is given).

    `chip` vs `button`: chip is the PANEL-chrome vocabulary (toolbars inside
    the dark app chrome, theme-quiet); button is the Open Machine STUDIO
    vocabulary (dark-edged verb chips -- PLAY/CHANGE/SAVE).

    `disabled` + `state` are the six-state model (see `widget_state`), and both
    default to the exact pixels the goldens pin. `disabled` is what the one
    live private copy -- `code_layer._panel_btn` -- dims by hand today.

    `kind` is the style entry it resolves against: "chip", or another kind
    of its shape ("toggle", whose ON reads as switched on).

    `colors` is the same escape hatch `row` and `cell` carry: an explicit
    (field, ink, edge) triple bypassing the skin, for a site whose pixels are
    frozen off-token. `field` None paints no field, `edge` None no border."""
    flags = (_F_ON if on else 0) | (_F_HOT if hot else 0) | (
        _F_DISABLED if disabled else 0)
    gid = -1
    if glyph is not None:
        flags |= _F_GLYPH
        if glyph_draw is None:
            gid = glyph_id(glyph)
    look = _LOOK if th is _TH else _look(th)
    kid = _KIND_ID.get(kind)
    if kid is None:
        kid = _kind_id(kind)
    act = -1 if state is None else _STATE_ID.get(state, -1)
    if glyph is None or glyph_draw is None:
        _C.chip(cv, look, rect, kid, flags, act, label, gid, fs, colors, _ALL)
        return
    # The caller's glyph REPLACES the label.
    _C.chip(cv, look, rect, kid, flags, act, label, gid, fs, colors, _FRAME)
    ink = (state_colors(th, kind, widget_state(on, hot, disabled, state))
           if colors is None else colors)[1]
    glyph_draw(glyph, rect, ink, cv)


# --- list rows and grid cells: the two shapes the tree hand-rolls ~29 times ----
#
# Designed from the real call sites, not from first principles. `row` answers
# `settings_layer._draw_settings_row` (no field when unselected, a fixed label
# pad, a right-hand value + a trailing icon), `files_app._draw_rows` (panel/
# title field, dim/accent edge, a truncated name), `storybook_app._draw_rows`
# (an off-token field, a vertically centred label), the system menu's rows
# (hilite on the selected row only, drawn by `moy_chrome`'s C) and
# `achievements_ui._draw_achievements` (a leading glyph, a locked/dim look
# that IS `disabled`). `cell` answers
# `file_widgets.FileGridView.draw` (thumbnail + centred caption),
# `appearance_app._draw_wall_card` (art + a filled caption BAND) and
# `cards_layer._draw_choice_icons` / `_draw_bg_thumbs` (frame-only cells whose
# picture is bespoke) -- which is why `cell` RETURNS the art rect instead of
# trying to own every picture.


def row(cv, th, rect, label, kind="row", on=False, hot=False, disabled=False,
        state=None, colors=None, icon_img=None, glyph=None, glyph_draw=None,
        value=None, value_ink=None, edge=True, pad=None, text_dy=None,
        hits=None, verb=None, arg=None, fs=None, pad_right=None, scale=1,
        glyph_ink=None, glyph_size=None):
    """One list row: [field] [edge] [icon|glyph] [label] ... [value].

    Semantics are the caller's: `on` is the selection, `hot` the armed
    destructive look, `disabled` the unusable one (dim ink AND no hit
    registration -- an unusable row must not be tappable). Interaction comes
    from the registry: pass `hits` + `verb` (+ `arg`) and the row registers its
    own tap rect AND reads its own hover/pressed state back, so wiring a
    surface up is the pump call plus nothing per row. `state` overrides that
    read (what a grid-like surface with arithmetic hit-testing passes).

    Knobs, each one a real site's frozen geometry:
      `pad`      left inset (default `ROW_PAD * fs`; Settings' frozen literal 4
                 and Storybook's `6 * fs` both pass their own).
      `pad_right` right inset; defaults to `pad`. A site with a deep LEFT
                 inset inside a narrow rect used to truncate its own text to
                 zero because one number did both jobs (the achievements
                 badge).
      `text_dy`  label top offset inside the row; default is vertically centred
                 (Storybook), Settings passes 5 and Files `6 * fs`.
      `scale`    text scale for BOTH label and value (default 1). The width
                 arithmetic follows it, so a scale-2 headline truncates at the
                 right column instead of escaping the rect.
      `edge`     draw the resolved edge as a border (Files/Storybook) or not
                 (Settings, the system menu popup). Its WEIGHT is skin metrics.
      `kind`     which skin entry to resolve against, as `button`'s `kind` is
                 (there is no name map here: a row's kind IS the catalog key).
                 The default is the list-row palette every caller had before
                 this argument existed. The shell's own row kinds are in
                 style.json beside it -- "row_menu" and friends, the
                 panel-CHROME rows that paint NO field until they are selected,
                 which this kind cannot express because its rest field spec is
                 always a colour.
      `colors`   an explicit (field, ink, edge) triple, bypassing the skin --
                 the escape hatch for sites whose pixels are frozen off-token
                 (Storybook's literal 7/0 rows, `cards_layer`'s own palette).
                 `field` None paints no field at all. Reach for `kind` first:
                 a colour that is a widget STATE belongs in the catalog, and
                 #207 is the triage that moved ten such sites out of here.
      `glyph_ink`/`glyph_size`  the leading glyph's colour and box, when it is
                 not the label's ink at the skin's box (a bright badge beside
                 quiet text; a 16x16 egg).
      `value`    a right-aligned secondary string (the Settings/WiFi value
                 column); the label truncates to whatever is left, and the
                 value itself truncates before it can escape the rect (#174).

    Draws only; returns nothing -- deliberately. `row` is the most-drawn widget
    in the shell and a return value is a tuple allocated per row per frame;
    callers that need the content band call the PURE `row_content_rect`
    instead (the `cell_art_rect` precedent).
    """
    if state is None and hits is not None and verb is not None:
        state = hits.state_of(verb, arg)
    flags = (_F_ON if on else 0) | (_F_HOT if hot else 0) | (
        _F_DISABLED if disabled else 0) | (_F_EDGE if edge else 0)
    gid = -1
    if icon_img is not None:
        flags |= _F_ICON
    elif glyph is not None:
        flags |= _F_GLYPH
        if glyph_draw is None:
            gid = glyph_id(glyph)
    look = _LOOK if th is _TH else _look(th)
    kid = _KIND_ID.get(kind)
    if kid is None:
        kid = _kind_id(kind)
    act = -1 if state is None else _STATE_ID.get(state, -1)
    if icon_img is None and (glyph is None or glyph_draw is None):
        _C.row(cv, look, rect, kid, flags, act, label, value, gid, glyph_size,
               glyph_ink, value_ink, pad, pad_right, text_dy, scale, fs, colors, _ALL)
    else:
        # The caller's picture lands between the frame and the text, as the
        # body it replaced drew it.
        _C.row(cv, look, rect, kid, flags, act, label, value, gid, glyph_size,
               glyph_ink, value_ink, pad, pad_right, text_dy, scale, fs, colors, _FRAME)
        if fs is None:
            fs = _fs(cv)
        x, y, w, h = int(rect[0]), int(rect[1]), int(rect[2]), int(rect[3])
        m = _METRICS["row"]
        p = m[ROW_PAD] * fs if pad is None else int(pad)
        if icon_img is not None:
            box = m[ROW_ICON] * fs
            cv.spr(icon_img, x + p, y + (h - box) // 2, fs)
        else:
            box = (m[ROW_GLYPH] if glyph_size is None else glyph_size) * fs
            ink = (state_colors(th, kind, widget_state(on, hot, disabled, state))
                   if colors is None else colors)[1]
            glyph_draw(glyph, (x + p, y + (h - box) // 2, box, box),
                       ink if glyph_ink is None else glyph_ink, cv)
        _C.row(cv, look, rect, kid, flags, act, label, value, gid, glyph_size,
               glyph_ink, value_ink, pad, pad_right, text_dy, scale, fs, colors,
               _CONTENT)
    # A DISABLED row registers nothing: "dim ink, non-registering" is the whole
    # point of the state.
    if hits is not None and verb is not None and not disabled:
        hits.add(rect, verb, arg)


def row_content_rect(rect, fs=1, pad=None, pad_right=None, icon=False,
                     glyph=False, glyph_size=None):
    """PURE geometry: the band a `row`'s label and value occupy, i.e. inside
    the pads and after any leading icon/glyph.

    Separate from `row` for the same reason `cell_art_rect` is separate from
    `cell`: a caller that draws its own trailing content (a stepper pair, a
    progress bar, a badge) needs the arithmetic WITHOUT the draw paying a tuple
    allocation on every row of every frame."""
    return _C.row_content(rect, fs, pad, pad_right, icon, glyph, glyph_size)


def cell_art_rect(rect, fs=1, pad=None, caption_h=0):
    """PURE geometry: the picture sub-rect of a grid cell -- inside `pad` on
    three sides, above the caption zone at the bottom. Separate from `cell` so
    a grid can size its thumbnail CACHE once, outside the loop, exactly as
    `FileGridView.draw` derives `art_w`/`art_h` before iterating."""
    return _C.cell_art(rect, fs, pad, caption_h)


def cell(cv, th, rect, label=None, kind="cell", on=False, hot=False,
         disabled=False, state=None, colors=None, band=False, band_fill=None,
         band_ink=None, pad=None, caption_h=None, icon_img=None, glyph=None,
         glyph_draw=None, fs=None, text_dx=None, text_dy=None,
         edge_last=False):
    """One grid cell: [field] [caption] [edge] + a centred icon/glyph, and
    RETURNS the art rect for the caller's own picture.

    Returning the art rect is the load-bearing decision: a thumbnail, a
    wallpaper preview, a sprite tile and a hand-painted background swatch are
    four different pictures, and a `cell` that tried to draw them all would fit
    none of them. It draws the FRAME -- which is the part that is copied 16
    times -- and hands back where the picture goes.

    There is deliberately NO `hits` argument. A grid registers no per-cell hit
    rects (module docstring); it hit-tests arithmetically and passes
    `state=cell_state(i, hover_i, pressed_i)`.

      `pad`        inset of the art (default `CELL_PAD * fs`; `FileGridView` 2,
                   the wallpaper cards `3 * fs`, a frame-only cell 0).
      `caption_h`  height of the caption zone at the bottom; default
                   `CELL_CAP * fs` when there is a label, else 0. The wallpaper
                   cards pass `17 * fs`.
      `band`       fill the caption zone (wallpaper cards) instead of printing
                   the label straight onto the field (`FileGridView`); the
                   label is left-aligned on a band and centred without one.
      `text_dx`/`text_dy`  explicit label offsets inside the cell, overriding
                   the band/centred choice -- `row`'s knobs, same meaning and
                   same default-None semantics. A caption whose frozen baseline
                   is `y + h - 13*fs` agrees with the band centre only at font
                   scale 1, which is what kept `appearance_app._draw_wall_card`
                   hand-rolled.
      `edge_last`  paint the border AFTER the icon/glyph rather than before,
                   so content that escapes the cell is clipped by its own
                   frame. (It cannot come after the CALLER's art -- that is
                   drawn once `cell` has returned; a caller that needs the
                   frame on top of its picture passes `colors=(f, i, None)`
                   and stamps the border itself.)
      `kind`       which skin entry to resolve against, as `row`.
      `colors`     an explicit (field, ink, edge) triple, as `row`.

    Default draw order is field -> caption -> edge -> icon/glyph, so the
    caller's art (drawn after, into the returned rect) never sits under the
    border and the border never sits under the art -- which is what
    `appearance_app._draw_wall_card` and `cards_layer._draw_bg_thumbs` both
    already do by hand.
    """
    flags = (_F_ON if on else 0) | (_F_HOT if hot else 0) | (
        _F_DISABLED if disabled else 0) | (_F_BAND if band else 0) | (
        _F_EDGE_LAST if edge_last else 0) | (_F_LABEL if label is not None else 0)
    gid = -1
    if icon_img is not None:
        flags |= _F_ICON
    elif glyph is not None:
        flags |= _F_GLYPH
        if glyph_draw is None:
            gid = glyph_id(glyph)
    look = _LOOK if th is _TH else _look(th)
    kid = _KIND_ID.get(kind)
    if kid is None:
        kid = _kind_id(kind)
    act = -1 if state is None else _STATE_ID.get(state, -1)
    if icon_img is None and (glyph is None or glyph_draw is None):
        return _C.cell(cv, look, rect, kid, flags, act, label, gid, pad, caption_h,
                       band_fill, band_ink, text_dx, text_dy, fs, colors, _ALL)
    # The caller's picture lands between the frame and a late edge.
    art = _C.cell(cv, look, rect, kid, flags, act, label, gid, pad, caption_h,
                  band_fill, band_ink, text_dx, text_dy, fs, colors, _FRAME)
    if fs is None:
        fs = _fs(cv)
    ax, ay, aw, ah = art
    m = _METRICS["cell"]
    if icon_img is not None:
        box = m[CELL_ICON] * fs
        cv.spr(icon_img, ax + (aw - box) // 2, ay + (ah - box) // 2, fs)
    else:
        box = m[CELL_GLYPH] * fs
        ink = (state_colors(th, kind, widget_state(on, hot, disabled, state))
               if colors is None else colors)[1]
        glyph_draw(glyph, (ax + (aw - box) // 2, ay + (ah - box) // 2, box, box),
                   ink, cv)
    _C.cell(cv, look, rect, kid, flags, act, label, gid, pad, caption_h,
            band_fill, band_ink, text_dx, text_dy, fs, colors, _CONTENT)
    return art


def _cell_edge(cv, x, y, w, h, c, n):
    cv.rectb(x, y, w, h, c)
    i = 1
    while i < n:                           # skins only; the default is 1 ring
        cv.rectb(x + i, y + i, w - 2 * i, h - 2 * i, c)
        i += 1


def tab_row_rects(rect, tabs, fs, gap=None):
    """PURE geometry for a labeled tab row: [(id, rect, labels_on), ...] laid
    left-to-right in `rect`. Overflow policy (resizable windows): if the fully
    labeled row doesn't fit, every tab collapses to its icon-only chip; chips
    that still don't fit are dropped from the END (draw-what-fits, same as the
    frozen icon ladder). `tabs` is a sequence of (id, label) -- icons are the
    draw pass's concern.

    The gap/pad/icon-box are skin metrics, but they are the one group that
    SIZES a widget: changing them reflows the tab ladder and re-baselines the
    goldens. That is a deliberate versioned act, not a data tweak (Section 3.3)."""
    labels = []
    for _id, label in tabs:
        labels.append(str(label) if label else "")
    on, rects = _C.tab_rects(rect, "\0".join(labels).encode("utf-8"), len(labels),
                             fs, gap)
    out = []
    for i in range(len(rects)):
        out.append((tabs[i][0], rects[i], on))
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
    look = _LOOK if th is _TH else _look(th)
    m = _METRICS["tab"]
    pad = m[TAB_PAD] * fs
    box = m[TAB_ICON] * fs
    for tid, r, labels_on in rects:
        text = str(_tab_label(tabs, tid))
        img = icon_for(icons[tid]) if icon_for is not None else None
        if img is None:
            _C.tab(cv, look, r, tid == active, labels_on, text, ink, _ALL)
        else:
            x, y, w, h = r
            _C.tab(cv, look, r, tid == active, labels_on, text, ink, _FRAME)
            cv.spr(img, x + (pad if labels_on else (w - box) // 2), y + (h - box) // 2, fs)
            _C.tab(cv, look, r, tid == active, labels_on, text, ink, _CONTENT)
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
    parts = []
    for item in items:
        parts.append(item if type(item) is str else str(item))
    _C.status(cv, _LOOK if th is _TH else _look(th), rect,
              "\0".join(parts).encode("utf-8"), len(parts))


# --- the classic game-canvas button family -----------------------------------
# The pre-toolkit Workstation draw trio (_btn/_icon_btn/_mini_btn), moved here
# verbatim (the 2026-07 kernel-shrink direction: console stays compositor/router);
# Workstation keeps thin delegates so every call site and test is untouched.
# These are the EDITOR-body vocabulary (paint/map/music/blocks action bars and
# the block prompts' pads): caller-colored fill, cream ring, black label.
#
# They take NO theme dict -- they draw over a cart's own colours -- so their
# ring/ink are theme-independent literals living in the metrics tuple beside
# their pads. That is the honest home for them: they are numbers a skin sets,
# not roles a theme resolves.

def game_btn(cv, rect, label, fill):
    """A labeled action button. Preserves the frozen baseline quirk VERBATIM:
    at font scale 1 the label prints with the legacy scale-2 arg but centers
    with height 8 (byte-identical to the shipped pixels) -- a BRANCH, not a
    number, which is why it stays code (style.json's "quirks")."""
    _C.game_btn(cv, rect, label, fill)


def game_icon_btn(cv, rect, kind, label, fill, glyph_draw=None):
    """A button that leads with an icon glyph (pre-literate) and keeps the word
    as a small secondary cue beside it."""
    if glyph_draw is None:
        # no glyph at all, as the body it replaced drew it
        _C.game_icon_btn(cv, rect, -1, label, fill, _ALL)
    elif glyph_draw is _glyph_draw:
        _C.game_icon_btn(cv, rect, glyph_id(kind), label, fill, _ALL)
    else:
        _C.game_icon_btn(cv, rect, -1, label, fill, _FRAME)
        fs = _fs(cv)
        m = _METRICS["game_icon_btn"]
        glyph_draw(kind, (int(rect[0]) + m[GI_PAD] * fs, int(rect[1]),
                          m[GI_GLYPH] * fs, int(rect[3])), m[GI_INK], cv)
        _C.game_icon_btn(cv, rect, -1, label, fill, _CONTENT)


def mini_btn(cv, rect, label, fill, label_rect=None):
    """A tiny labeled chip (no ring) -- the Settings steppers' vocabulary. Its
    pads are UNSCALED (style.json's "quirks"): the vocabulary predates font scaling
    and its frozen pixels never grew with it.

    `label_rect` is where those pads are measured from when the CHIP is bigger
    than the label's own box -- a chrome-scaled tap target (#203), whose fill
    still paints the whole finger-sized `rect`. Defaults to `rect`, which is the
    identity everywhere the two are the same size."""
    _C.mini_btn(cv, rect, label, fill, label_rect)


# --- the theme's shape ---------------------------------------------------------
# A theme's surfaces, corner radius, pressed-label offset and text effects
# (docs/theming_2026-09.md §4.2-§4.4), resolved once in C and drawn by every
# primitive. Like the skin, one for the process.

_PART_ID = {}
_THEME_STATE_ID = {}
for _i, _s in enumerate(_C.theme_states()):
    _THEME_STATE_ID[_s] = _i


def use_shape(name):
    """Install theme `name`'s shape for every primitive (None: the empty one,
    which draws as no theme had one)."""
    _C.use_shape(name)


def paint(cv, th, rect, part, state=REST):
    """Paint theme part `part`'s surface in `state` into `rect`, as a
    primitive paints its frame. False, drawing nothing, when the theme gives
    the part none there: the caller draws its own."""
    p = _PART_ID.get(part)
    if p is None:
        p = _PART_ID[part] = _C.parts().index(part)
    return _C.paint(cv, _LOOK if th is _TH else _look(th), rect, p,
                    _THEME_STATE_ID[state])


def toolbar(cv, th, rect):
    """The app toolbar band (Storybook): the theme's title surface --
    chips and status text draw over it in title_ink."""
    _C.toolbar(cv, _LOOK if th is _TH else _look(th), rect)
    return rect


def dialog(cv, rect, ring=None, fill=None):
    """The dark MODAL panel shell (the blocks prompts/insert menu, the system
    menu, the graduation banner): dark-purple base + a colored frame. Geometry
    stays the caller's (their hit rects are test-pinned constants); only the
    shell drawing is shared. `ring`/`fill` default to the skin's."""
    _C.dialog(cv, rect, ring, fill)


def dialog_rings():
    """How many rings the active skin draws around a dialog (`dialog`'s edge
    count): what the kernel's chrome draws its menu and ABOUT box with."""
    return _METRICS["dialog"][DLG_EDGE]


def text_field(cv, rect, text, placeholder="", colors=None, fs=None):
    """The modal prompts' text-entry field: black field, light-grey ring, cream
    text or a dim placeholder, and the yellow caret bar after the text.

    Its metrics are FIXED 1x by default (style.json's "quirks"): both block prompts
    draw on the game canvas, so the pads, the caret box and the 8px caret
    advance never scale. Pass `fs` to scale them (a system-surface field on a
    font-scaled tier) and `colors` -- (field, ring, ink, placeholder, caret) --
    to theme it; both default to the frozen rendering the goldens pin."""
    _C.text_field(cv, rect, text, placeholder, colors, fs)


def panel(cv, th, rect, title=None, fs=None):
    """A tool panel: the warm surface + thin border, optionally a compact title
    strip (title bg / title ink tokens). Returns the CONTENT rect (inside the
    border, below any title strip)."""
    return _C.panel(cv, _LOOK if th is _TH else _look(th), rect, title, fs)


def focus_ring(cv, th, rect, fs=None):
    """The Section 5.2 focus treatment: signal-yellow ring with a 1-step gap --
    one implementation so keyboard/pointer focus looks the same everywhere."""
    _C.focus_ring(cv, _LOOK if th is _TH else _look(th), rect, fs)


def fill_uncovered(cv, inner, outer, col):
    """Fill only the parts of `inner` that `outer` does not already cover.

    For the "clear the body, then clear the panel on top of it" idiom, where both
    fills use the SAME colour and the panel sits almost entirely inside the body.
    On the P4's 1024x600 editor those two rects are ~450K and ~426K pixels, so the
    second fill was rewriting ~94% of the first in the same colour -- ~848KB of
    redundant writes per frame, and `mg_fill_run` is a cached store loop, so
    PSRAM's write-allocate doubles a fill's traffic. Settings hit the identical bug
    and deleting its duplicate fill was worth ~9ms (see settings_layer.draw).

    The panel is NOT contained in the body -- its top edge sits `2 * fs` px above
    (`panel = (8fs, bar_h - 2fs, ...)` vs `body_fill = (0, bar_h, ...)`) -- so the
    fill cannot simply be dropped; that would leave a stale strip under the bar.
    This paints the overhang and nothing else, which is pixel-identical to the two
    full fills whenever the colours match. Emits nothing at all when `inner` is
    fully covered (the Scene pane's layout), and degrades to one full fill when the
    two rects do not overlap, so it is safe on every tier.
    """
    _C.fill_uncovered(cv, inner, outer, col)


# --- scrolling ------------------------------------------------------------------

def row_drag(anchor, py, step, top, top_max):
    """PURE row-snapped drag scrolling, the Settings-rows contract: a held
    vertical drag moves the window one row per `step` pixels of travel, finger
    up scrolls the content down, and the sub-step remainder stays anchored so
    a slow drag still crosses a row. `anchor` is the finger y the pending
    travel is measured from, `top` the first visible row, `top_max` the
    largest `top` the list allows. Returns (anchor, top); the caller marks
    dirty when `top` moved."""
    delta = anchor - py
    while delta >= step and top < top_max:
        top += 1
        delta -= step
    while delta <= -step and top > 0:
        top -= 1
        delta += step
    return py + delta, top


# The largest physical-buffer rotation any canvas has (host 1, device
# ping-pong 2, the P4's triple framebuffer 3 -- shipped, efcf5d1). The paint ring
# keeps this many entries so blit_shift can verify RETAINED_FRAMES consecutive
# paints on ANY tier; raise it if a canvas ever rotates more buffers.
_MAX_RETAINED = 3


class ScrollRegion:
    """The one scroll model: a view rect over larger content, on either AXIS
    (vertical by default -- the Settings rows; `horizontal=True` for the
    Library shelf). The caller draws items at (item_pos - self.offset) along
    the scroll axis and clips to the view; this owns the offset bookkeeping,
    clamping, drag deltas, and the slim scrollbar (drawn + hit through the
    same geometry -- along the right edge vertically, the bottom edge
    horizontally)."""

    # Scaled by fs at draw. This is skin METRICS (`_METRICS["scrollbar"]`) and
    # `use_skin` rebinds it, because the bar width is the one metric that is
    # also a public class attribute -- a caller reading BAR_W and the pixels
    # drawn must never disagree. The module's last line sets it from C.
    BAR_W = 4
    # Kinetic scrolling (#113 Phase 4). Velocities are px/ms so the tuning is
    # frame-rate independent; every dt is INJECTED (the loop's tick), never
    # read from a clock, so the physics is deterministic under test.
    FRICTION = 0.995    # per-ms decay: a fling coasts ~1s (0.995^1000 ~ 0.007)
    MIN_FLING = 0.15    # px/ms (150 px/s): a release slower than this is a stop
    STOP_VEL = 0.02     # px/ms: coasting below this comes to rest
    VEL_EMA = 0.5       # release velocity reflects the last few pointer samples

    def __init__(self, horizontal=False):
        self.horizontal = bool(horizontal)
        self.view = (0, 0, 0, 0)
        self.content = 0            # content extent along the scroll axis
        self.offset = 0
        # The drag and the fling (the last drag sample, the EMA finger velocity
        # in px/ms, the float offset while coasting, their flags) live in this
        # buffer; the rules are native/moy_ui's (moy_ui_scroll_t).
        self._st = bytearray(16)
        # Scroll-as-blit paint ring (#113): the most recent painted frames'
        # (frame_no, offset, key, stamp), newest first. blit_shift compares the
        # current offset against the paint whose pixels sit in the target
        # framebuffer (RETAINED_FRAMES back -- 1 on the host's persistent
        # buffer, 2 on the device ping-pong), so a scrolled view can shift its
        # already-correct pixels and repaint only the exposed band.
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

    # -- scroll-as-blit (#113) ----------------------------------------------
    # The band contract: an eligible frame shifts the view's pixels by the
    # offset delta (Canvas.scroll_rect) and repaints ONLY the exposed band --
    # pixel-identical to a full repaint, at a fraction of the draw calls. The
    # ring pins everything that must match for the shift to be sound: the
    # paints must be the immediately preceding console frames (a gap means
    # another surface may have painted these framebuffers), and the target
    # buffer's paint must share the caller's `key` (selection/statics -- any
    # difference means the retained pixels aren't a pure translation of the
    # current state).

    def invalidate(self):
        """Content changed under the pixels (items/layout/theme): force full
        paints until the ring re-arms."""
        self._painted = []

    def note_painted(self, frame_no, key=None, stamp=None):
        """Record a paint of this view at the current offset. `key` pins the
        non-scroll state the pixels depend on; `stamp` is an opaque damage rect
        (the cursor sprite baked into this paint) returned by a later
        blit_shift so the caller can repaint it."""
        self._painted.insert(0, (frame_no, self.offset, key, stamp))
        # Keep _MAX_RETAINED entries: blit_shift needs RETAINED_FRAMES
        # consecutive paints, and note_painted has no canvas to read the real
        # N from. Trimming below the largest N anywhere (the P4's triple
        # framebuffer) would silently disable scroll-as-blit there:
        # len(_painted) < k forever.
        del self._painted[_MAX_RETAINED:]

    def blit_shift(self, cv, frame_no, key=None):
        """(delta, stamp) when the canvas's target framebuffer holds this
        view's pixels at a known offset, else None (caller paints full). delta
        is current offset minus the offset baked in the target buffer; stamp
        is that paint's recorded damage rect (or None)."""
        k = getattr(cv, "RETAINED_FRAMES", 0)
        if (k < 1 or getattr(cv, "scroll_rect", None) is None
                or len(self._painted) < k):
            return None
        for i in range(k):
            if self._painted[i][0] != frame_no - 1 - i:
                return None            # not consecutive paints of THIS view
        _fno, off, pkey, stamp = self._painted[k - 1]
        if pkey != key:
            return None
        delta = self.offset - off
        if abs(delta) >= self._extent():
            return None                # nothing survives the shift
        return (delta, stamp)

    def scroll_to_show(self, p, size):
        """Nudge the offset so the content span [p, p+size) is inside the view."""
        vis = self._extent()
        if p < self.offset:
            self.offset = p
        elif p + size > self.offset + vis:
            self.offset = p + size - vis
        self._clamp()

    # -- drag (touch) ------------------------------------------------------
    # drag_* take the AXIS coordinate (py vertically, px horizontally);
    # DragTap below picks it for callers that feed whole pointer samples.

    @property
    def _vel(self):
        """The release velocity, px/ms (single precision) -- for tests."""
        return _unpack("<f", self._st, 0)[0]

    @property
    def _drag(self):
        """The last drag sample's axis coordinate, None with no drag live."""
        if not self._st[12] & 1:
            return None
        return _unpack("<i", self._st, 8)[0]

    def drag_start(self, p):
        _C.scroll_drag_start(self._st, p)

    def drag_move(self, p, dt_ms=None):
        off = _C.scroll_drag_move(self._st, p, dt_ms, self.offset, self.content,
                                  self.view[2] if self.horizontal else self.view[3])
        if off < 0:
            return False
        self.offset = off
        return True

    def drag_end(self):
        _C.scroll_drag_end(self._st, self.offset, self.content,
                           self.view[2] if self.horizontal else self.view[3])

    def stop(self):
        """Kill any kinetic motion (a catching touch / a programmatic scroll)."""
        _C.scroll_stop(self._st)

    @property
    def animating(self):
        """True while a released fling is coasting -- the owner ticks it each
        frame and keeps the redraw gate open until it rests."""
        return bool(self._st[12] & 2)

    def tick(self, dt_ms):
        """Advance a coasting fling by one frame (dt_ms injected): integrate
        the offset, decay the velocity, hard-stop at the clamp edges (#113
        v1: no overshoot). Returns True when this frame moved the view."""
        off = _C.scroll_tick(self._st, dt_ms, self.offset, self.content,
                             self.view[2] if self.horizontal else self.view[3])
        if off < 0:
            return False
        self.offset = off
        return True

    @property
    def drag_active(self):
        return bool(self._st[12] & 1)

    # -- scrollbar -----------------------------------------------------------

    def bar_rect(self, fs=1):
        """The scrollbar thumb rect, or None when everything fits."""
        return _C.bar_rect(self.view, self.content, self.offset, self.horizontal, fs,
                           self.BAR_W)

    def draw_bar(self, cv, th):
        _C.scrollbar(cv, _LOOK if th is _TH else _look(th), self.view, self.content,
                     self.offset, self.horizontal, self.BAR_W)


class DragTap:
    """Press/drag/release disambiguation over a ScrollRegion -- the ONE
    touch-list gesture machine (the Library shelf and the Settings rows both
    ride it). A press inside the region's view arms a pending tap and starts
    a drag; finger travel past the slop turns the gesture into a SCROLL (the
    pending tap dies); a clean release -- press and release with no drag --
    FIRES the tap. Callers feed every pointer sample to frame() and activate
    ONLY on its result, so scrolling can never 'click' whatever happens to be
    under the finger (the press-edge-activation bug this class retires)."""

    def __init__(self, region):
        self.region = region
        # The gesture's state (the press point and its flags) lives in a buffer this
        # object owns; the rules are native/moy_ui's.
        self._t = bytearray(12)
        self._out = array("i", [0, 0, 0])
                                    # its release is a CATCH, never a tap

    @property
    def dragging(self):
        return bool(self._t[8] & 2)

    def frame(self, px, py, click, down, slop=6, dt_ms=None):
        """One pointer sample: `click` is the press edge, `down` the held
        state, `dt_ms` the loop's tick (feeds the kinetic release velocity).
        Returns the press-edge (x, y) on a clean tap release, else None.
        While a drag is live the region's offset follows the finger -- the
        caller syncs its scroll state from region.offset."""
        r = self.region
        out = self._out
        tapped = _C.dragtap(self._t, r._st, r.view, r.horizontal, px, py, click, down,
                            slop, dt_ms, r.offset, r.content, out)
        r.offset = out[0]
        return (out[1], out[2]) if tapped else None


_load_metrics()
