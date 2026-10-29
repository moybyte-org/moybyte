# Map (grep -n a name to jump there):
#   is_light                          the theme's tool-surface presentation class
#   wrap_words                        the text path's wrap (C); wrap_px, print_fit, text_width beside it
#   font                              the font each role's text draws in (C); use_font, print_font, cart_text beside it
#   -- interaction state              widget_state
#   -- the style                      metrics, state_colors (C)
#   -- the theme's shape              use_shape, paint (C)
#   -- window frames                  frame, frame_buttons, frame_band (C)
#   -- depth                          use_depth, depth, frame_geom, frame_under, frame_under_onto, cast (C)
#   -- glyphs                         glyph, glyph_id (C); s, the string table (C)
#   -- widgets                        button, chip
#   -- button kinds                   use_buttons, button_unreadable, begin_frame, primary_warnings
#   -- list rows and grid cells       row, cell
#   -- the classic game-canvas button family  game_btn, mini_btn, toolbar
#   -- scrolling                      drag_steps, pan_drag, row_drag, ScrollRegion, DragTap
#   PaintRing                         what the last painted frames left of a region, per buffer
#   ScrollRegion                      the one scroll model
#   DragTap                           press/drag/release over a ScrollRegion
#   ui_frame                          a host begins an app's frame of runtime/ui.py's verbs
#   AppHost                           a shipped app's host side of the verbs (ctx.ui)
#   Hold                              the page under a modal, held while it is up; its rows frames
#   TextView                          a text area's rows in C: its record, shift and floor
"""The toolkit's engine layer: the rect-taking widgets under the public UI API.

`runtime/ui.py` is the public UI API (docs/ui_api_2026-10.md), the verbs every
app and cart draws with; this module is what lies under them and what the
chrome keeps (§7 there): the rect-taking widgets -- buttons, rows, cells, tab
rows, status rows, panels -- the marks (`focus`, `current`), the one
`ScrollRegion` model, the theme's shape, depth and
fonts, and `ui_frame`, which a host calls to begin an app's frame of verbs.
The shell's apps not yet moved onto the verbs draw here too, and
tests/test_ui_public.py pins which. The rect algebra is `runtime/ui.py`'s.
Design rules (deliberate, argued in the redesign thread):

  * IMMEDIATE MODE, and the bodies are C: each widget is one call into
    native/moy_ui (docs/kernel_toolkit_2026-10.md), which draws into the
    canvas's CANVAS row through the kernels the canvas verbs end in -- so it
    renders identically on the host, every board and the browser. No retained tree, no per-frame
    allocation beyond small tuples. tests/ui_twin.py keeps the Python bodies
    as the oracle.
  * Widgets take RECTS; the per-surface Layout classes keep owning reflow,
    with `runtime/ui.py`'s rect algebra.
  * Colors come from the semantic theme tokens (chrome.theme_colors -- surface/ink/
    focus/value/danger/...), so restyling an app means routing it
    through this module, not hunting literals. A button names its KIND
    (normal, primary, tonal, danger, toggle) and the theme styles the kind
    (`use_buttons`).
  * STYLE IS DATA, not code. Every per-state colour and every number a draw
    used to hardcode -- pads, edge weights, strip heights, icon boxes, label
    alignment -- is native/moy_ui/style.json, resolved by native/moy_ui's C
    against the theme (the style section below is its face, and caches what
    it answers so a widget pays one identity compare and two dict gets and
    allocates nothing). What a theme changes beyond colours -- surfaces, a
    frame style, metrics, text effects -- is its shape (`use_shape`). Four
    frozen quirks stayed code; style.json's "quirks" records them.
  * Geometry functions are PURE and separate from the draw functions, so a
    surface's tap handler hit-tests the exact rects the draw used (the
    `action_rects` pattern, generalized) -- a cached bar strip can draw once
    while taps keep resolving.
  * SIX STATES resolve in ONE place with the precedence
    `disabled > pressed > hot > on > hover > rest`: `on`/`hot`/`disabled` are
    caller ARGUMENTS (they are meaning -- only the surface knows them), while
    `hover`/`pressed` arrive as a widget's `state` argument (they are
    interaction). `rest` is byte-identical to what the goldens already know:
    every state cue is a delta painted ON the rest look, resolved through the
    theme's own semantic roles, never a new literal.

Min-size convention: a window-content layout may expose `MIN_W` / `MIN_H`
constants; app registration adopts them and the windowed WM clamps resizes to
the registered floor.

MicroPython-safe: tuples/lists/dicts, no f-strings, and no imports from the
shell/surface graph. Glyph drawing is injected by callers that want it, keeping
this toolkit a leaf module that chrome and every surface can safely import:
its one import is moy_ui, the C this module is the face of.
"""

from array import array

try:
    from struct import unpack_from as _unpack
except ImportError:  # pragma: no cover - a port with only ustruct
    from ustruct import unpack_from as _unpack

import moy_ui as _C

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


def wrap_px(text, maxw, scale=1):
    """`wrap_words` by pixels: lines that draw in at most `maxw` pixels at
    `scale` through a canvas's `print` (the print font, `print_font`)."""
    if type(text) is not str:
        text = str(text)
    return _C.wrap(text, max(1, maxw // max(1, scale)), _C.print_font())


def print_fit(s, maxw, scale=1):
    """The longest prefix of `s` that draws in at most `maxw` pixels at
    `scale` through a canvas's `print`, cut at a codepoint."""
    if type(s) is not str:
        s = str(s)
    if _C.text_width(s, scale, _C.print_font()) <= maxw:
        return s
    n = len(s)
    while n > 0 and _C.text_width(s[:n], scale, _C.print_font()) > maxw:
        n -= 1
    return s[:n]


def print_ellipsis(s, maxw, scale=1):
    """`s` as a canvas's `print` shows it in a box `maxw` pixels wide at
    `scale`: whole where it fits, else cut at a codepoint with the font's
    ellipsis after it where the font has one (the toolkit's cut, the one
    a label and a table cell take), three full stops where it has none."""
    if type(s) is not str:
        s = str(s)
    got = _C.text_cut(s, maxw, scale, _C.print_font())
    if got is s or got == s or got.endswith("\u2026"):
        return got
    head = print_fit(s, maxw - print_width("...", scale), scale).rstrip()
    return head + "..." if head else got


def text_width(s, scale=1, role=None):
    """The pixels `s` draws at `scale`, the sum of its UTF-8 bytes' advances:
    in `role`'s font (`font`), or petme128's 8px cell with no role."""
    if type(s) is not str:
        s = str(s)
    if role is None:
        return _C.text_width(s, scale)
    return _C.text_width(s, scale, _C.font(role)[0])


_HERO = [0]


def font(role="ui"):
    """(font, w, line, ascent): the font the toolkit's `role` text draws in
    (docs/theming_2026-09.md §4.7; roles `ui`, `title`, `mono`, `caption`),
    its glyph box's width, its line advance and its baseline, unscaled.
    Petme128's, (0, 8, 8, 7), until a font is installed for the role.
    `hero` is the handheld home's title face where one is installed
    (`use_hero`), else the `title` role's."""
    if role == "hero":
        info = _C.font_info(_HERO[0]) if _HERO[0] else None
        if info is None:
            return _C.font("title")
        return (_HERO[0], info[0], info[2], info[3])
    return _C.font(role)


def font_v2(fid):
    """Whether loaded font `fid` is a version 2 face (native/moy_gfx/moy_text.h:
    anti-aliased, its line holding its leading); petme128 (0) and a version
    1 font are not."""
    info = _C.font_info(fid) if fid else None
    return info is not None and info[4] == 0


def use_hero(fid):
    """Draw the handheld home's title in font `fid` (a `font_load` id; 0:
    the `title` role's), a face of the look's type (`fontset.ROLES`)."""
    _HERO[0] = fid


def use_font(role, fid):
    """Draw the toolkit's `role` text in font `fid` (a `font_load` id, 0 for
    petme128). False, installing nothing, for a font that is not loaded. A
    layout reads the metrics when it is built, so one built before keeps
    the old font's."""
    return _C.use_font(role, fid)


def print_font(fid=None):
    """Draw a canvas's `print` in font `fid` (0 for petme128): the font the
    shell's own text draws in. Answers the font it replaced, or None, and
    installs nothing, for a font that is not loaded. While a cart's code
    runs (`cart_text`) prints are petme128's and this names the font they
    come back to. With no `fid`, the font prints draw in now."""
    if fid is None:
        return _C.print_font()
    return _C.print_font(fid)


def line_dy(dy, fs=1, role="ui"):
    """A text line's top offset `dy` at font scale `fs`, written for
    petme128's 7-row ascent, kept where it centres the line for `role`'s
    font: moved up by half the ascent that font adds. A shorter ascent keeps
    the offset as written, so it is the identity under petme128 and Tiny5."""
    return dy - max(0, (_C.font(role)[3] - 7) * fs) // 2


def print_cut(s, maxw, scale=1):
    """`print_fit`, never empty for a non-empty `s`: a box too narrow for one
    character still shows the first."""
    if type(s) is not str:
        s = str(s)
    got = print_fit(s, maxw, scale)
    return got if got or not s else s[:1]


def print_width(s, scale=1):
    """The pixels `s` draws at `scale` through a canvas's `print` now: in the
    print font (`print_font`)."""
    if type(s) is not str:
        s = str(s)
    return _C.text_width(s, scale, _C.print_font())


def cart_text(on):
    """The app-cart boundary (docs/theming_2026-09.md §4.7): on while a
    cart's code runs, so its `print` and its `ui` draw in petme128 whatever
    the theme names; off puts the shell's fonts back. Answers whether it
    was on."""
    return _C.cart_text(on)


def font_file(name):
    """A seed theme package's file by name -- a font, or font X.fnt's licence
    text X.txt -- as bytes, or None."""
    return _C.font_file(name)


def theme_fonts(name):
    """((role, file), ...): theme `name`'s font files, role by role along
    its `inherits`."""
    return _C.theme_fonts(name)


def theme_sheets(name):
    """(sprites, icons): theme `name`'s sheet files (docs/theming_2026-09.md
    §4.6) along its `inherits`, None where none names one."""
    return _C.theme_sheets(name)


def sheet_check(src, rows):
    """(rc, rows): sheet bytes `src` read as the loader reads a sheet of at
    most `rows` rows (the cart sheet's hex format, strictly)."""
    return _C.sheet_check(src, rows)


def sheet_load(src):
    """rc: load the role sheet the sprite parts draw from (0, or the
    loader's refusal, which leaves none)."""
    return _C.sheet_load(src)


def sheet_clear():
    """No role sheet: every part draws its surface."""
    _C.sheet_clear()


def sheet_caps():
    """(width, sprite rows, icon rows, sprite bytes, icon bytes): a sheet's
    caps."""
    return _C.sheet_caps()


def theme_layout(name):
    """(refused, bars): theme `name`'s layout along its `inherits`, as the
    loader checked it (docs/shell_layout_2026-10.md §5); refused when the
    default's stands in. A bar is (edge, size, left, center, right, texts)."""
    return _C.theme_layout(name)


def theme_home(name):
    """The home's arrangement in theme `name`'s layout (docs/shell_layout_2026-10.md
    §12): "carousel" (the default), "grid" or "icons"."""
    return _C.theme_home(name)


def theme_install(src):
    """(rc, notes): install theme.json bytes `src` as the one package beside
    the seeds, replacing the last; a refusal leaves it as it was."""
    return _C.theme_install(src)


def theme_installed():
    """The installed theme package's name (the one beside the seeds), or None."""
    return _C.theme_installed()


def theme_uninstall():
    """Drop the installed theme package: only the seeds remain."""
    _C.theme_uninstall()


def theme_resolve(src):
    """The loader's answer for theme.json bytes `src`, installing nothing:
    (rc, notes, name, dark, light, fonts, layout, sheets)."""
    return _C.theme_resolve(src)


def theme_custom(source, accent, tint):
    """(rc, bytes): `custom`'s theme.json derived from `source` (a seed's
    name or a stored package's bytes), `accent` (an accent's A or a
    preset's dark value, None for the source's own) and `tint` (a tint's name, None for none);
    docs/theming_2026-09.md §4.10."""
    return _C.theme_custom(source, accent, tint)


def theme_accents():
    """((name, A, deep), ...): the sixteen accents, style.json's."""
    return _C.theme_accents()


def theme_presets():
    """((name, dark, light), ...): the accent presets, style.json's, each
    picked by its dark value (docs/look_2026-10.md step 27)."""
    return _C.theme_presets()


def theme_tints():
    """((name, ramp), ...): the eight tints, style.json's."""
    return _C.theme_tints()


def font_load(src):
    """(rc, font): load a font file's bytes into a row of its own
    (native/moy_gfx/moy_text.h); font is None when refused."""
    return _C.font_load(src)


def font_info(fid):
    """(w, h, line, ascent, first, count) of a loaded font, or None."""
    return _C.font_info(fid)


def font_reset():
    """Free every loaded font: the loader's slots outlive a VM."""
    _C.font_reset()


def font_free(fid):
    """Free a loaded font's row; False for an id that names none."""
    return _C.font_free(fid)


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
    destructive action, an unusable verb); `interact` is the widget's
    `state` argument -- HOVER, PRESSED or None.
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
# against the theme's token row. Nothing in this module holds a colour or a
# pad.
#
# What stays here is the FACE: `state_colors` hands back a shared tuple from a
# cache keyed on the theme dict's identity, the kind and the state, so a widget
# draw allocates nothing and crosses into C only on the first draw of a kind
# under a theme. A theme dict is packed into
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
BTN_PAD, BTN_ICON, BTN_GLYPH, BTN_EDGE, BTN_ALIGN, BTN_ICON_GAP = 0, 1, 2, 3, 4, 5
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
_METRICS = {}                         # kind -> its metric tuple
_RING = []                            # [theme dict, look slot, cache, look gen], newest first
_RING_MAX = 4
_TH = None                            # the theme dict _FLAT caches for
_LOOK = 0
_GEN = 0                              # bumped by look_moved: the ring's epoch
_FLAT = {}                            # kind -> {state: (field, ink, edge)}


def _load_metrics():
    m = {}
    for name in _C.metric_kinds():
        m[name] = _C.metrics(name)
    _METRICS.clear()
    _METRICS.update(m)
    ScrollRegion.BAR_W = m["scrollbar"][SB_W]


def kinds():
    """The kinds a style entry colours."""
    return _C.kinds()


def metrics(kind):
    """The metric tuple for `kind`; index it with the slot constants above."""
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


def look_moved():
    """The look moved (`Appearance.publish`, with its `look_gen`): every ring
    entry resolved before re-resolves at its next bind. The ring's epoch is
    the process's own, since every console in it shares this module."""
    global _GEN, _TH
    _GEN += 1
    _TH = None


def _bind_theme(th):
    """Make `th` the theme the cache answers for, reusing its look when the
    ring holds it under the current generation; an entry from an older one is
    re-resolved in its own slot."""
    global _TH, _FLAT, _LOOK
    ring = _RING
    for i in range(len(ring)):
        ent = ring[i]
        if ent[0] is th:
            if i:
                del ring[i]
                ring.insert(0, ent)
            if ent[3] != _GEN:
                _C.look(ent[1], _pack(th))
                ent[2] = {}
                ent[3] = _GEN
            _TH, _LOOK, _FLAT = th, ent[1], ent[2]
            return
    if len(ring) < _RING_MAX:
        slot = len(ring)
    else:
        slot = ring.pop()[1]
    _C.look(slot, _pack(th))
    ent = [th, slot, {}, _GEN]
    ring.insert(0, ent)
    _TH, _LOOK, _FLAT = th, slot, ent[2]


_LOOK_KERNEL = 5                      # moy_ui.h's MOY_UI_LOOK_KERNEL
_KERNEL = [None, -1]                  # the theme dict the kernel's look holds, its look gen


def kernel_look(th):
    """Put `th` in the kernel's own look, the one the chrome it draws over a
    run (moy_chrome_overlay_draw) resolves in: packed again only when the
    theme or the look's generation moved."""
    k = _KERNEL
    if k[0] is not th or k[1] != _GEN:
        _C.look(_LOOK_KERNEL, _pack(th))
        k[0], k[1] = th, _GEN


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
_F_FOCUS = 512
_FRAME, _CONTENT, _ALL = 1, 2, 3


def _look(th):
    """The C look `th` resolves in (its ring slot), binding it on a miss. A
    caller with no theme passes its own colours; any look serves it."""
    if th is None:
        return 0
    if th is not _TH:
        _bind_theme(th)
    return _LOOK


def label(cv, rect, text, ink, align=ALIGN_LEFT, bg=None, shadow=None):
    """Text in a box at the canvas's font scale: truncated to what fits (at a
    codepoint), placed by `align`, centred vertically. Returns the drawn
    width. `bg` is the colour index the caller has filled the whole box with
    (the glyph lane then need not read it); `shadow` is (ci, alpha 1..255,
    dx, dy), drawn under the text at the font scale."""
    return _C.label(cv, rect, text, ink, align, bg, shadow)


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
           glyph=None, glyph_draw=None, disabled=False, state=None, app=None,
           focus=False):
    """One themed button: filled field, thin edge, centered label
    (truncated to fit), optional 16x16 icon image or 12x12 glyph at the left.
    A glyph is drawn by `glyph` unless the caller passes its own
    `glyph_draw(kind, rect, color, cv)`;
    `on` swaps to the accent (the pressed/active look).

    `kind` is what the button MEANS (docs/media/look_2026-10/DESIGN.md,
    "Button kinds"): "normal", "primary" (the one primary action on a
    surface), "tonal" (a secondary emphasis), "danger" (clears or destroys)
    or "toggle" (a two-state part, drawn as a cell); an unknown one is
    normal. `app` is the drawing app's id, which picks the theme's
    `app_buttons[app]` overrides.

    `disabled` + `state` are the six-state model (see `widget_state`): they
    default to the untouched REST/`on` rendering the goldens pin, and only a
    caller that opts in pays anything. `state` is HOVER, PRESSED or None.

    The field/ink/edge come from the style under the key `kind` maps to
    (style.json's "button_kinds") and the live theme's button leaves
    (`use_buttons`), the pads/boxes/alignment from `_METRICS["button"]`."""
    flags = (_F_ON if on else 0) | (_F_DISABLED if disabled else 0) | (
        _F_FOCUS if focus else 0)
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
    if kid == _PRIMARY_KID:
        _primary_seen(cv, rect, label)
    row = -1 if app is None else _app_row(app)
    act = -1 if state is None else _STATE_ID.get(state, -1)
    if icon_img is None and (glyph is None or glyph_draw is None):
        _C.button(cv, look, rect, kid, flags, act, label, gid, _ALL, row)
        return
    _C.button(cv, look, rect, kid, flags, act, label, gid, _FRAME, row)
    fs = _fs(cv)
    tx = _C.button_x(rect, flags, label, fs)
    y, h = int(rect[1]), int(rect[3])
    m = _METRICS["button"]
    if icon_img is not None:
        box = m[BTN_ICON] * fs
        cv.spr(icon_img, tx, y + (h - box) // 2, fs)
    else:
        st = widget_state(on, False, disabled, state)
        ink = _C.button_style(look, kid, _STATE_ID.get(st, _REST_ID), row)[1]
        glyph_draw(glyph, (tx, y, m[BTN_GLYPH] * fs, h), ink, cv)
    _C.button(cv, look, rect, kid, flags, act, label, gid, _CONTENT, row)


# -- button kinds (docs/look_2026-10.md step 10) ----------------------------------

BUTTON_KINDS = []                     # the theme model's kind names, in its order
for _n in _C.theme_look_fields()[0]:
    if _n.startswith("buttons.") and _n.split(".")[1] not in BUTTON_KINDS:
        BUTTON_KINDS.append(_n.split(".")[1])
BUTTON_KINDS = tuple(BUTTON_KINDS)
_PRIMARY_KID = _C.kind(_C.kinds()[_C.button_kind("primary")])
_APP_ROW = {}                         # app id -> its row of the live theme's overrides
_PRIMARY = {}                         # id(canvas) -> (rect, label) of this frame's primary
_PRIMARY_WARNED = set()
primary_warnings = []                 # (first label, second label), once each


def _app_row(app):
    r = _APP_ROW.get(app)
    if r is None:
        r = _APP_ROW[app] = _C.buttons_app(app)
    return r


def use_buttons(live):
    """Install the live theme's `buttons[kind]` and `app_buttons[app][kind]`
    (`chrome.live_colors`' `look`, every colour placed in its block) for every
    button that draws, its look's `disabled` as every disabled part's ink
    and its `danger_ink` as the ink of a menu's danger rows; None drops
    them. A gradient fill draws its first stop."""
    _C.buttons_clear()
    _APP_ROW.clear()
    _DISABLED[0] = None
    if live is None:
        _C.disabled(None)
        _C.danger_ink(None)
        return
    for app, kind, vals in live.buttons:
        _C.buttons_set(app, BUTTON_KINDS.index(kind), vals)
    v = live.look[0].get("disabled")
    _DISABLED[0] = v if v is not None and v >= 0 else None
    _C.disabled(_DISABLED[0])
    v = live.look[0].get("danger_ink")
    _C.danger_ink(v if v is not None and v >= 0 else None)


_DISABLED = [None]        # the live theme's disabled ink, a colour index, or None


def disabled_ink(th):
    """The colour index a disabled icon or label draws in under theme `th`:
    the live theme's `disabled` (about 3:1 on the surface, under the ink and
    its dim), else `th`'s ink_dim."""
    v = _DISABLED[0]
    return v if v is not None else th["ink_dim"]


def button_ink(th, kind="normal"):
    """The colour index a `kind` button's label draws in at rest under theme
    `th` and the installed button leaves, for an icon that stands for that
    kind's action off a button (a trash icon is the danger button's ink).
    Where that ink does not read on `th`'s surface (a filled button's light
    ink on a light page), the kind's colour role (`danger`) or the ink."""
    look = _LOOK if th is _TH else _look(th)
    ink = _C.button_style(look, _kind_id(_button_kind(kind)), _REST_ID, -1)[1]
    if ink is not None and _C.theme_reads(th["surface"], ink):
        return ink
    return th.get(kind, th["ink"])


def button_unreadable(th, apps=()):
    """The button kinds whose ink misses the contrast floor on their field at
    rest under theme `th` and the installed button leaves, as (app, kind)
    pairs (app None: the theme's own). A miss never refuses a theme."""
    look = _LOOK if th is _TH else _look(th)
    out = []
    for app in (None,) + tuple(apps):
        row = -1 if app is None else _app_row(app)
        for kind in BUTTON_KINDS:
            kid = _kind_id(_button_kind(kind))
            f, i = _C.button_style(look, kid, _REST_ID, row)[:2]
            if f is not None and i is not None and not _C.theme_reads(f, i):
                out.append((app, kind))
    return out


def begin_frame():
    """A new frame: the one-primary check starts over."""
    if _PRIMARY:
        _PRIMARY.clear()


def _primary_seen(cv, rect, label):
    """One `primary` per surface (DESIGN.md, "Button kinds"): a second one on
    the same canvas in one frame is warned of, once per pair of labels."""
    k = id(cv)
    got = _PRIMARY.get(k)
    if got is None:
        _PRIMARY[k] = (tuple(rect), label)
    elif got[0] != tuple(rect):
        pair = (got[1], label)
        if pair not in _PRIMARY_WARNED:
            _PRIMARY_WARNED.add(pair)
            primary_warnings.append(pair)
            print("ui: a second primary button on one surface: %r and %r" % pair)


def chip(cv, th, rect, label, on=False, hot=False, fs=None,
         glyph=None, glyph_draw=None, disabled=False, state=None,
         colors=None, kind="chip", focus=False):
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
    (field, ink, edge) triple bypassing the style, for a site whose pixels are
    frozen off-token. `field` None paints no field, `edge` None no border."""
    flags = (_F_ON if on else 0) | (_F_HOT if hot else 0) | (_F_FOCUS if focus else 0) | (
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
# a row with no field when unselected, a fixed label pad, a right-hand value
# and a trailing icon, `files_app._draw_rows` (panel/
# title field, dim/accent edge, a truncated name), `storybook_app._draw_rows`
# (an off-token field, a vertically centred label), the system menu's rows
# (hilite on the selected row only, drawn by `moy_chrome`'s C) and
# `achievements_ui._draw_achievements` (a leading glyph, a locked/dim look
# that IS `disabled`). `cell` answers a thumbnail with a centred caption and
# `cards_layer._draw_choice_icons` / `_draw_bg_thumbs` (frame-only cells whose
# picture is bespoke) -- which is why `cell` RETURNS the art rect instead of
# trying to own every picture.


def row(cv, th, rect, label, kind="row", on=False, hot=False, disabled=False,
        state=None, colors=None, icon_img=None, glyph=None, glyph_draw=None,
        value=None, value_ink=None, edge=True, pad=None, text_dy=None,
        fs=None, pad_right=None, scale=1,
        glyph_ink=None, glyph_size=None, focus=False):
    """One list row: [field] [edge] [icon|glyph] [label] ... [value].

    Semantics are the caller's: `on` is the selection, `hot` the armed
    destructive look, `disabled` the unusable one (dim ink). `state` is the
    interaction cue: HOVER, PRESSED or None.

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
                 (Settings, the system menu popup). Its WEIGHT is style metrics.
      `kind`     which style entry to resolve against, as `button`'s `kind` is
                 (there is no name map here: a row's kind IS the catalog key).
                 The default is the list-row palette every caller had before
                 this argument existed. The shell's own row kinds are in
                 style.json beside it -- "row_menu" and friends, the
                 panel-CHROME rows that paint NO field until they are selected,
                 which this kind cannot express because its rest field spec is
                 always a colour.
      `colors`   an explicit (field, ink, edge) triple, bypassing the style --
                 the escape hatch for sites whose pixels are frozen off-token
                 (Storybook's literal 7/0 rows, `cards_layer`'s own palette).
                 `field` None paints no field at all. Reach for `kind` first:
                 a colour that is a widget STATE belongs in the catalog, and
                 #207 is the triage that moved ten such sites out of here.
      `glyph_ink`/`glyph_size`  the leading glyph's colour and box, when it is
                 not the label's ink at the style's box (a bright badge beside
                 quiet text; a 16x16 egg).
      `value`    a right-aligned secondary string (the Settings/WiFi value
                 column); the label truncates to whatever is left, and the
                 value itself truncates before it can escape the rect (#174).

    Draws only; returns nothing -- deliberately. `row` is the most-drawn widget
    in the shell and a return value is a tuple allocated per row per frame;
    callers that need the content band call the PURE `row_content_rect`
    instead (the `cell_art_rect` precedent).
    """
    flags = (_F_ON if on else 0) | (_F_HOT if hot else 0) | (_F_FOCUS if focus else 0) | (
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
         edge_last=False, focus=False):
    """One grid cell: [field] [caption] [edge] + a centred icon/glyph, and
    RETURNS the art rect for the caller's own picture.

    Returning the art rect is the load-bearing decision: a thumbnail, a
    wallpaper preview, a sprite tile and a hand-painted background swatch are
    four different pictures, and a `cell` that tried to draw them all would fit
    none of them. It draws the FRAME -- which is the part that is copied 16
    times -- and hands back where the picture goes.

    A grid hit-tests arithmetically (`FileGridView.tap`,
    `cards_layer._choice_cells`); `state` is its cell's cue.

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
                   scale 1.
      `edge_last`  paint the border AFTER the icon/glyph rather than before,
                   so content that escapes the cell is clipped by its own
                   frame. (It cannot come after the CALLER's art -- that is
                   drawn once `cell` has returned; a caller that needs the
                   frame on top of its picture passes `colors=(f, i, None)`
                   and stamps the border itself.)
      `kind`       which style entry to resolve against, as `row`.
      `colors`     an explicit (field, ink, edge) triple, as `row`.

    Default draw order is field -> caption -> edge -> icon/glyph, so the
    caller's art (drawn after, into the returned rect) never sits under the
    border and the border never sits under the art -- which is what
    `cards_layer._draw_bg_thumbs` already does by hand.
    """
    flags = (_F_ON if on else 0) | (_F_HOT if hot else 0) | (_F_FOCUS if focus else 0) | (
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
    while i < n:
        cv.rectb(x + i, y + i, w - 2 * i, h - 2 * i, c)
        i += 1


def tab_row_rects(rect, tabs, fs, gap=None):
    """PURE geometry for a labeled tab row: [(id, rect, labels_on), ...] laid
    left-to-right in `rect`. Overflow policy (resizable windows): if the fully
    labeled row doesn't fit, every tab collapses to its icon-only chip; chips
    that still don't fit are dropped from the END (draw-what-fits, same as the
    frozen icon ladder). `tabs` is a sequence of (id, label) -- icons are the
    draw pass's concern.

    The gap/pad/icon-box are style metrics, but they are the one group that
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


def tab_row(cv, th, rect, tabs, active, icon_for=None, ink=None,
            icon_draw=None):
    """The labeled tab ladder (mockup: Config | Blocks | Code | ...). `tabs` is
    a sequence of (id, label, icon_kind); the ACTIVE tab wears the selection
    color (grape in the shipped themes) with its title ink, inactive tabs stay
    quiet on the field. `icon_draw(kind, rect)` draws a tab's icon where it
    can (a theme's icon set), else `icon_for(kind)`'s 16x16 image. Returns the
    geometry list so a cached-strip caller can hit-test without redrawing."""
    fs = _fs(cv)
    icons = {}
    slim = []
    for tid, label, icon_kind in tabs:
        slim.append((tid, label))
        icons[tid] = icon_kind
    rects = tab_row_rects(rect, slim, fs)
    for tid, r, labels_on in rects:
        kind = icons[tid]
        img = icon_for(kind) if icon_for is not None else None
        tab(cv, th, r, tid == active, str(_tab_label(tabs, tid)), labels_on, ink, img,
            icon_draw=icon_draw, kind=kind)
    return rects


def tab(cv, th, r, active, label="", labels_on=False, ink=None, img=None,
        icon_draw=None, kind=None, pad=0):
    """One tab chip of `tab_row` in rect `r`: its field (the ON look when
    `active`), its icon in the icon slot -- `icon_draw(kind, rect)` where it
    draws one (a theme's icon set), else the 16x16 `img` -- and `label` when
    `labels_on`. `pad` grows the field that much past its content on either
    side (the content lies in `r` less `pad` each side)."""
    look = _LOOK if th is _TH else _look(th)
    x, y, w, h = r
    inner = (x + pad, y, w - 2 * pad, h) if pad else r
    if img is None and icon_draw is None:
        if not pad:
            _C.tab(cv, look, r, active, labels_on, label, ink, _ALL)
            return
        _C.tab(cv, look, r, active, labels_on, label, ink, _FRAME)
        _C.tab(cv, look, inner, active, labels_on, label, ink, _CONTENT)
        return
    fs = _fs(cv)
    x, y, w, h = inner
    box = _METRICS["tab"][TAB_ICON] * fs
    _C.tab(cv, look, r, active, labels_on, label, ink, _FRAME)
    ix = x + (_METRICS["tab"][TAB_PAD] * fs if labels_on else (w - box) // 2)
    iy = y + (h - box) // 2
    if icon_draw is None or not icon_draw(kind, (ix, iy, box, box)):
        if img is not None:
            cv.spr(img, ix, iy, fs)
    _C.tab(cv, look, inner, active, labels_on, label, ink, _CONTENT)


def _tab_label(tabs, tid):
    for t, label, _icon in tabs:
        if t == tid:
            return label
    return ""


# The inputs the hints draw the keys of (moy_ui.h, MOY_UI_INPUT_*): a desk
# keyboard, the T-Deck's keyboard, a gamepad, and touch alone, which shows no
# keycaps.
INPUTS = ("keys", "tdeck", "pad", "touch")


def input_kind(name=None):
    """The input in use, one of INPUTS; `name` sets it first, and the keys
    the hint strips draw with it (`hint_kind`). The console sets it from the
    last input used."""
    if name is not None:
        _C.input(INPUTS.index(name))
    return INPUTS[_C.input()]


def hint_kind(name=None):
    """The input whose keys every hint strip draws, one of INPUTS: the input
    in use, but where a touch follows keys the strips keep those keys, so
    they stay up under a finger on a board with keys; "touch" (no keys used
    yet: touch alone) shows none. `name` sets it first."""
    if name is not None:
        _C.hint_input(INPUTS.index(name))
    return INPUTS[_C.hint_input()]


def hints(cv, th, rect, items, desk=False, draw=True):
    """Keycap hints in band `rect`, from its left (the public `ui.hints`'s
    part, for chrome that lays itself out): `items` alternate an action --
    `confirm`, `back`, `change`, `tabs`, `menu` or `cancel`, drawn as the keys of the
    hint input (`hint_kind`) -- or keys spelled out -- space-separated;
    `enter`, `left`, `right`, `up` and `down` draw as signs -- and what they
    do. Answers
    each pair's (x0, x1) across the band, what a tap hit-tests (x0 == x1 for
    a pair the input has no key for; none under touch); `draw` False only
    measures."""
    b = b"\0".join([str(t).encode("utf-8") for t in items])
    return _C.hints(cv, _LOOK if th is _TH else _look(th), rect, b, desk, draw)


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
# their pads. That is the honest home for them: they are numbers the style
# sets, not roles a theme resolves.

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
# primitive, one for the process.

_PART_ID = {}
_THEME_STATE_ID = {}
for _i, _s in enumerate(_C.theme_states()):
    _THEME_STATE_ID[_s] = _i


def use_shape(name):
    """Install theme `name`'s shape for every primitive (None: the empty one,
    which draws as no theme had one, with no button leaves)."""
    _C.use_shape(name)
    if name is None:
        use_buttons(None)


def pattern_unreadable(th):
    """The installed shape's dither and pinstripe surfaces whose label ink
    misses the contrast floor against either colour in theme `th`, as
    (part, state) pairs (docs/theming_2026-09.md §7). A miss never refuses a
    theme."""
    return _C.pattern_unreadable(_look(th))


def paint(cv, th, rect, part, state=REST):
    """Paint theme part `part`'s sprite in `state` into `rect` (the role
    sheet's, `sheet_load`), else its surface, as a primitive paints its
    frame. False, drawing nothing, when the theme gives the part neither
    there: the caller draws its own."""
    p = _PART_ID.get(part)
    if p is None:
        p = _PART_ID[part] = _C.parts().index(part)
    return _C.paint(cv, _LOOK if th is _TH else _look(th), rect, p,
                    _THEME_STATE_ID[state])


# --- window frames ---------------------------------------------------------------
# The window decoration and the bar's band in the frame style the theme
# chooses (docs/theming_2026-09.md §4.5): the styles are C, a theme only picks
# one, its title alignment and its button order.

FRAME_BUTTONS = ("close", "max", "min", "back")     # MOY_UI_FB_*
FRAME_MIN, FRAME_BACK, FRAME_MATTE, FRAME_FULL = 1, 2, 8, 16   # MOY_UI_FRAME_*


def frame(cv, th, rect, title_h, title, active, btns, fs):
    """Window `rect`'s frame: its ring, its `title_h` strip with `title` and
    the strip's buttons, its drop shadow. `btns`: FRAME_MIN for a minimise
    button, FRAME_BACK for the back chevron at the head of the strip (True:
    a minimise), FRAME_MATTE for a content rect standing in from the
    sides by the foot, whose columns the depth's body then paints too, and
    FRAME_FULL for a full-screen window's row: the title and the buttons
    alone, on what is under them, maximise drawn as restore."""
    _C.frame(cv, _LOOK if th is _TH else _look(th), rect, title_h, title, active, int(btns), fs)


def frame_buttons(rect, title_h, title, btns, fs):
    """The strip's buttons as `frame` draws them, left to right:
    [(name, rect, right)], `right` True for the group right of the title."""
    return [(FRAME_BUTTONS[b[0]], (b[1], b[2], b[3], b[4]), b[5] == 1)
            for b in _C.frame_buttons(rect, title_h, title, int(btns), fs)]


def frame_icon(rect, title_h, btns=0):
    """The slot (x, y, w, h) the frame leaves for the window's line icon left
    of its title, past a back chevron `btns` asks for, or None where it draws
    none (a frame style's frame, `win.icon` off)."""
    return _C.frame_icon(rect, title_h, int(btns))


# --- depth: the theme's radii, shadow and edges (docs/look_2026-10.md step 6) ---
#
# Installed with the theme and its mode (`use_depth`); a theme with no `look`
# takes the default's, flat, and a theme whose chain names a frame style nearer than its look
# keeps that frame (moy_theme_look_frames). A window's shadow and its
# rounded outline's fringe blend over whatever lies under it: the desk layer
# casts them over what it repainted (`frame_under`, runtime/wm_desk.py), and
# a window that repaints takes the casts of the windows above it over what
# it painted (`frame_under_onto`, runtime/wm_windowed.py), so a blend never
# lands twice on the same pixel.

UNDER_FULL, UNDER_SMALL, UNDER_NONE = 0, 1, 2
CAST_WINDOW, CAST_CARD, CAST_KEY = 0, 1, 2
_DEPTH = [(0,) * 15]


def use_depth(name, light):
    """Install theme `name`'s depth in a mode (None: the default's), and its
    marks."""
    _DEPTH[0] = tuple(_C.use_depth(name, light))


def depth():
    """The installed depth: (win_r, card_r, key_r, corners, stepped, shadow,
    edges, alpha, blur, dx, dy, back, frames, bar_h, hhbar_h); shadow 0 none,
    1 soft, 2 hard; `frames` when the window frame is the depth's; the desk
    bar's and the handheld bar's heights."""
    return _DEPTH[0]


def frame_geom(w, h, title_h, active=True, mode=UNDER_FULL):
    """A w x h window's (radius, foot, pad left, up, right, down) under the
    depth: the foot is the rows under its content its bottom corners take,
    the pads how far its shadow at `mode`'s size reaches."""
    return _C.frame_geom(w, h, title_h, active, mode)


def frame_under(cv, th, rect, title_h, active, mode=UNDER_FULL):
    """What window `rect` leaves to the pixels under it: its shadow at `mode`'s
    size and its outline's partly covered pixels, blended over them."""
    _C.frame_under(cv, _LOOK if th is _TH else _look(th), rect, title_h, active, mode)


def frame_under_onto(cv, th, rect, title_h, active, onto, onto_th, onto_active,
                     mode=UNDER_FULL):
    """`frame_under` of window `rect`, onto only the pixels window `onto`
    (title strip `onto_th`, focused `onto_active`) paints opaque."""
    _C.frame_under_onto(cv, _LOOK if th is _TH else _look(th), rect, title_h, active, mode,
                        onto, onto_th, onto_active)


def cast(cv, th, rect, part=CAST_CARD, mode=UNDER_FULL):
    """The shadow `rect` casts as a part (CAST_*); False where the theme
    casts none."""
    return _C.cast(cv, _LOOK if th is _TH else _look(th), rect, part, mode)


# frame_band's `side`: where the band's edge line goes, facing the content.
BAND_BOTTOM, BAND_TOP, BAND_RIGHT, BAND_LEFT = 0, 1, 2, 3


def frame_band(cv, th, rect, light=False, side=BAND_BOTTOM):
    """The bar's band in the frame's style; `light`: a window's light toolbar;
    `side` the edge line's (a top bar's is its bottom row)."""
    _C.frame_band(cv, _LOOK if th is _TH else _look(th), rect, light, side)


# bar_part's parts (moy_ui.h's MOY_UI_BARP_*): the 2026-10 look's bar.
BAR_BAND, BAR_HH_BAND, BAR_SYS, BAR_HH_SYS, BAR_CHIP, BAR_TILE = 0, 1, 2, 3, 4, 5


def tile(cv, th, rect, icon, locked=False, r=8, desk=False):
    """A badge's tile over `rect` on a card's surface: `icon` on a tile
    tinted from `value`, or with `locked` veiled with a lock badge, its
    corners cut to `r` (at the font scale) toward `surface`."""
    _C.tile(cv, _LOOK if th is _TH else _look(th), rect, icon, 4 if locked else 0, r, _fs(cv),
            _DESK if desk else 0)


def unlock(cv, th, cx, y, head, name, icon, burst=0, desk=False):
    """The unlock banner (GAPS 47), centred on `cx` from `y`: the badge's
    tile, `head` in `value` and its `name`; a secret's with confetti
    `burst` (1..255) of the way out from its edges. Its card's rect."""
    return _C.unlock(cv, _LOOK if th is _TH else _look(th), cx, y, head, name, icon, burst,
                     _fs(cv), _DESK if desk else 0)


def modal(cv, th, rect, desk=False):
    """A system card over `rect`, flat (the popover's and the pause card's:
    a hard drop in a shade of the ground, the surface and its edge), for a
    surface that lays out its own card."""
    _C.modal(cv, _LOOK if th is _TH else _look(th), rect, _fs(cv), _DESK if desk else 0)


# perf's argument slots in its array('h') of sixteen
PERF_RIGHT, PERF_BOTTOM, PERF_FPS, PERF_FRAME, PERF_DETAIL, PERF_FS, PERF_DESK, PERF_DRAW = range(8)
PERF_PARTS = 8


def perf(cv, th, args, words, out):
    """The perf HUD's chip, and with its `PERF_DETAIL` its breakdown card
    (moy_ui_perf), from `args` -- an array('h') of sixteen: the bottom right
    it hangs from, the fps, the frame in tenths of a ms, the detail, the
    scale (the caller's: a game canvas has no font scale to ask), the desk
    class and whether to draw, then from `PERF_PARTS` draw, flush, logic and
    idle in tenths of a ms -- and `words` its NUL-separated labels; the box
    it covers into `out`, an array('i', 4). Allocates nothing."""
    _C.perf(cv, _LOOK if th is _TH else _look(th), args, words, out)


def scrim(cv, th, rect, src=None, also=None):
    """What a modal dims under it (D-01): `rect` washed in the look's
    `scrim`, drawn once a full frame, before the modal's card. `src`: a
    buffer laid out as the canvas's whose pixels it washes instead of the
    canvas's (the frame a buffer of the ring holds); `also`: one each result
    is stored to as well (the layer a hold keeps)."""
    look = _LOOK if th is _TH else _look(th)
    if src is None and also is None:
        _C.scrim(cv, look, rect)
    else:
        _C.scrim(cv, look, rect, src, also)


def bar_part(cv, th, part, rect, on=0, fill=None):
    """A bar part of the live theme's look (docs/look_2026-10.md step 14):
    a desk or handheld bar's band (`on` the band's BAND_* side), the system
    button's tile (`on`: its menu is open), a desk chip's field (`on`: the
    focused window's) or a desk icon's tile (step 25, BAR_TILE; `fill`, a
    look colour, is its app's of the look's `tiles`, step 26b)."""
    look = _LOOK if th is _TH else _look(th)
    if fill is not None:
        return _C.bar_part(cv, look, part, rect, int(on), fill)
    return _C.bar_part(cv, look, part, rect, int(on))


def toolbar(cv, th, rect):
    """The app toolbar band (Files, Storybook, Get Carts): the theme's title
    surface. Returns the ink its text draws in: title_ink, or the ink of
    the fill a theme's toolbar surface puts there instead."""
    return _C.toolbar(cv, _LOOK if th is _TH else _look(th), rect)


def dialog(cv, rect, ring=None, fill=None):
    """The dark MODAL panel shell (the blocks prompts/insert menu, the system
    menu, the graduation banner): dark-purple base + a colored frame. Geometry
    stays the caller's (their hit rects are test-pinned constants); only the
    shell drawing is shared. `ring`/`fill` default to the style's."""
    _C.dialog(cv, rect, ring, fill)


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


def autoscroll_mark(cv, th, x, y, s=1):
    """A middle-button autoscroll's anchor at (x, y): a ring on the
    surface with a wedge up and a wedge down (`editors_code.TextMouse`)."""
    r = 6 * s
    cv.circ(x, y, r, th["surface"])
    cv.circb(x, y, r, th["ink"])
    cv.tri(x - 2 * s, y - s, x + 2 * s, y - s, x, y - 4 * s, th["ink"])
    cv.tri(x - 2 * s, y + s, x + 2 * s, y + s, x, y + 4 * s, th["ink"])


# The focus mark's size classes and the marks' flags (native/moy_ui/moy_ui.h).
_MARK_CLASS = {"part": 0, "card": 1, "small": 2, None: 3}
_MARK_AFTER, _MARK_BACK, _MARK_SIDE, _MARK_INK, _MARK_RING, _MARK_COUNT = 1, 2, 4, 8, 32, 64
_MARK_KEEP, _MARK_SELECTED, _MARK_PILL = 512, 1024, 2048


def focus(cv, th, rect, size=None, r=0, after=False, fs=None, ring=False, selected=False,
          pill=False, flat=False):
    """The focus mark on `rect`, the theme's by its size class
    (docs/media/look_2026-10/DESIGN.md, the marks): `size` "card", "small",
    "part", or None to class it by its size. Drawn after the part's own fill
    and before its content; a lifted card's rim is a second call with
    `after`, once its content is drawn. Returns (ink, box): the colour the
    content takes (None: its own) and the box it draws in (a lifted card's,
    grown and raised). A part drawn by `row`, `cell`, `chip` or `button`
    takes `focus=True` instead. In a window behind it draws nothing. `ring`
    draws the theme's ring whatever its focus style, and is no focus mark (a
    cursor's: the sprite editor's). `selected` marks the card a screen
    describes while focus is elsewhere or does not draw (touch): the card's
    mark as a current mark, drawn whatever the shell says of focus, with no
    rim and no focus mark counted (the desk shelf's selected card). `pill`
    draws the bar's mark whatever the theme's style (an OS widget's, a tab's
    of the home): a plate of the mark's colour under the content, and with
    `after` a ring two units (`fs`) wide on `rect`'s edge, radius `r`; the
    content keeps its own ink. `flat` draws the theme's ring as the focus
    mark, counted, whatever its style: a mark that moves with every focus
    step and must not lift or shadow its neighbours (the home's grid and
    icons, docs/look_2026-10.md step 33)."""
    if flat:
        ring = True
    return _C.focus(cv, _LOOK if th is _TH else _look(th), rect, _MARK_CLASS[size], r,
                    (_MARK_AFTER if after else 0) | (_MARK_RING if ring else 0)
                    | (_MARK_COUNT if flat else 0)
                    | (_MARK_SELECTED if selected else 0) | (_MARK_PILL if pill else 0), fs)


def cursor(cv, rect, keyline=0):
    """A canvas's cursor around `rect` (a pixel, a brush, a swatch): a
    `keyline` line round it, a line of the frame's `surface`, the theme's
    `accent` ring two pixels wide with corners of three, and a last line of
    `surface` (DESIGN.md's cursor), so it reads on a pixel of any colour.
    Under the frame's theme (`ui_frame`)."""
    th = _TH
    x, y, w, h = rect
    cv.rectb(x - 1, y - 1, w + 2, h + 2, keyline)
    if th is None or th.get("surface") is None:
        return
    s = th["surface"]
    cv.rectb(x - 2, y - 2, w + 4, h + 4, s)
    cv.rrect(x - 3, y - 3, w + 6, h + 6, 2, None, s)
    cv.rrect(x - 4, y - 4, w + 8, h + 8, 3, None, th.get("accent", th["ink"]), 2)
    cv.rrect(x - 5, y - 5, w + 10, h + 10, 4, None, s)


def current(cv, th, rect, r=0, back=False, side=False, fs=None, keep=False):
    """The current mark under `rect`'s content: the theme's plate, fill or
    bar (`side`: a bar beside a row, not under it; `back`: in a window
    behind; `keep`: the content keeps its own colours, a code line's, so an
    opaque plate is the row's outline instead). Returns the ink the content
    takes, or None for its own. A part drawn by `row`, `cell`, `chip` or
    `tab` marks its `on` itself."""
    return _C.current(cv, _LOOK if th is _TH else _look(th), rect, r,
                      (_MARK_BACK if back else 0) | (_MARK_SIDE if side else 0)
                      | (_MARK_KEEP if keep else 0), fs)


def current_ink(cv, th, back=False):
    """The ink the current content takes under the theme's current mark, as
    `current` answers it, without drawing the mark (a caller that draws its
    own, the desk bar's chips): the look's `current.ink`, a fill's selection
    ink, or None for its own."""
    return _C.current(cv, _LOOK if th is _TH else _look(th), (0, 0, 0, 0), 0,
                      _MARK_INK | (_MARK_BACK if back else 0), None)


_MARKS_ON = [True]   # what `marks` last set, read with no tuple made (`marks_on`)


def marks(active=None, reset=False):
    """(active, focus marks drawn, reach): whether focus draws (`active` sets it: the shell clears it
    while a window behind draws; under touch, `input_kind`, no focus draws
    whatever it says, as on a phone), the count of focus marks drawn (`reset`
    zeroes it after reading), and how far a card's focus mark reaches past
    the card's top edge."""
    if active is not None:
        _MARKS_ON[0] = bool(active)
    return _C.marks(active, reset)


def marks_on():
    """`marks()[0]`, allocating nothing: the one setter is `marks`."""
    return _MARKS_ON[0]


def mark_reach(size="part"):
    """How far a focus mark of size class `size` ("part", "card", "small")
    reaches past its box: the ring's gap, width and outline as the live look
    rounds and defaults them (0 for a mark inside its box). What a part that
    keeps room for its ring lays out by."""
    return _C.mark_reach(_MARK_CLASS[size])


# -- the public UI API's host side (docs/ui_api_2026-10.md) ----------------------
#
# runtime/ui.py's verbs draw into UI_TARGET: the running app's context (its
# focus, the stops its last frame drew, each id's kept state: bytes it keeps
# between frames), the canvas and the look. A host begins each frame of an
# app's verbs with ui_frame, before the app's update, so `ui.keys_taken()`
# answers there.

UI_TARGET = [None, None, 0, 0]  # the frame's context, canvas, look and desk flags
_DESK = 1                               # moy_ui.h's MOY_UI_DESK
_WINDOW = 2                             # ... and MOY_UI_WINDOW
_ELSEWHERE = 4                          # ... and MOY_UI_ELSEWHERE
_KEY_EDGE = 16                          # ... and MOY_UI_KEY_EDGE
_APP_SHIFT = 8                          # moy_ui_api.h's MOY_UI_APP_SHIFT
UI_SIZE = _C.api_size()
HOST_ROWS = 6                           # moy_ui_api.h's MOY_UI_API_HOST: the rects of `AppHost.rows`


_WHOLE = [None]                         # ui_frame's last whole-canvas rect
ui_owes = _C.api_owes      # (ctx) whether the frame it drew last owes the next
ui_moving = _C.api_moving  # (ctx) whether a page of it flings or glides
_DT_MS = [0]                            # the loop's tick, the frames' clock (frame_dt)
_MOVING = [False]                       # a frame begun since moving_take left a page moving
_AGAIN = [False]                        # a pass since again_take owes the next frame
# The painted frame the console draws (paint_frame): its number, and whether
# its content is alone on the glass -- the content's ring vouches for its
# buffers there (a full-screen tier's content, no overlay, cursor or pill
# over it); the console; the last painted frame before it that drew (not a
# repeat of the one before it, `repeat_frame`); and the last repeat.
_PAINT = [-1, False, None, -1, -1]
# A window's content drawn by the windowed WM: 0 none, 1 a window frame, 2 one
# whose target the WM's shown ring vouches holds the page as the frame before
# left it (`window_frame`): the one voucher of a window's pages.
_WIN = [0]
_CHANGE = [None]
_SHIFT = "shift"
_ROWS = "rows"
_TV_HIST = 4                    # the frames a TextView remembers (RETAINED_FRAMES - 1 at most)


def paint_frame(n, alone, console=None):
    """`console` paints frame `n` (its count of painted frames), its
    content alone on the glass where `alone`: an app's `AppHost` may then
    make it a shift frame out of the frame before (`PaintRing`)."""
    if _PAINT[4] != _PAINT[0]:
        _PAINT[3] = _PAINT[0]
    _PAINT[0] = n
    _PAINT[1] = bool(alone)
    _PAINT[2] = console
    _CHANGE[0] = None


def painted():
    """The number of the frame the console paints (`paint_frame`)."""
    return _PAINT[0]


def painted_alone():
    """Whether the frame the console paints shows its content alone on a
    canvas whose content vouches for its own buffers (`paint_frame`): no
    overlay, cursor or toast over it, on the fullscreen tier."""
    return _PAINT[1]


def rows_drawn():
    """The frame painted drew its content's changed rows alone, as a
    surface that keeps its own frames says (the sprite pickers over the
    page they hold): what `change_take` answers next."""
    _CHANGE[0] = _ROWS


def popover_band(card, cv, desk):
    """The rows of popover card `card` (x, y, w, h) its body lies in, as
    (x, y, w, h): the card's width, inside its inset top and foot, clear of
    its rounded corners and its drop (moy_ui_modal_band: the value row's
    inset, MOY_UI_M_VALUE_ROW_INSET, at the scale)."""
    pad = _METRICS["value_row"][_VALUE_ROW_INSET + (1 if desk else 0)] * _fs(cv)
    return (card[0], card[1] + pad, card[2], card[3] - 2 * pad)


_VALUE_ROW_INSET = 28


def repeat_frame():
    """The painted frame repeats the one before it (the windowed WM's
    copy, surface model §14.6): a page that frame drew is as it left it."""
    _PAINT[4] = _PAINT[0]


def _drew_last():
    """The last painted frame that drew, a run of repeats after it seen
    through."""
    return _PAINT[3] if _PAINT[4] == _PAINT[0] else _PAINT[0]


def window_frame(v):
    """The windowed WM draws a window's content now (`v` 1), its target
    holding the page as the last frame left it with nothing over it (`v`
    2: the shift frame's licence on the WM's side, docs/surface_model_v1.md
    §14.7), or has done (0)."""
    _WIN[0] = v


def change_take():
    """What the frame painted since the last call changed, as its shift
    frames said: "shift" (a region moved and its floor drawn), "rows" (the
    floor alone), None (drawn whole, or nothing spoke). Reading clears it."""
    got = _CHANGE[0]
    _CHANGE[0] = None
    return got


def frame_dt(ms):
    """The loop's tick in ms, which every frame begun until the next call
    hands the engine as its clock (`MOY_UI_IN_DT`): the console sets it once
    a loop pass. 0, no clock: a glide lands at once and nothing flings."""
    _DT_MS[0] = int(ms)


def moving_take():
    """Whether a frame begun since the last call left a page of its
    context moving (a fling, a glide): the console's frame gate reads it as
    animation, so the motion's next frame comes with nothing dirty. Reading
    clears it."""
    got = _MOVING[0]
    _MOVING[0] = False
    return got


def again_take():
    """Whether a pass drawn since the last call changed what it had drawn
    before the change and owes the next frame (`AppHost.again`): the console
    paints it, input or none. Reading clears it."""
    got = _AGAIN[0]
    _AGAIN[0] = False
    return got


def ui_context():
    """A fresh context for one app's verbs."""
    return bytearray(UI_SIZE)


def ui_frame(ctx, cv, th, rect=None, inp=None, desk=False, window=False, app=None,
             elsewhere=False, edge=False):
    """Begin context `ctx`'s frame on canvas `cv` under theme `th`: a page
    fills `rect` (the canvas when None), `inp` is the frame's input in the
    run snapshot's shape (an array('i') of buttons held, buttons pressed --
    bit i of each is left, right, up, down, a, b, run -- the pointer's x, y
    and flags, and the key typed; None for none), `desk` picks the desk's
    rows and `window` says the page lies in a window's body, whose colour
    its ground then is; `app` is the shell app's id, whose
    `app_buttons[app]` its buttons take; `elsewhere`, the keys are another
    surface's (a cart's editor handle holds them), so no stop holds focus;
    `edge`, the key in `inp` is a press edge already (the host sends each
    typed key once and begins no frame for its release). The frame's clock is `frame_dt`'s. Answers whether the frame is settled:
    it owes no frame after it -- what its input moved (a focus, a scroll)
    this pass draws -- so a page drawn again draws the same pixels; a page
    still moving on its own is `ui_moving`'s. A duplicate id the last frame
    drew is logged, once."""
    t = UI_TARGET
    t[0] = ctx
    t[1] = cv
    t[2] = _look(th)
    dup = _C.api_dup(ctx)
    if dup is not None:
        print("ui: two stops share the id %r; the later is no stop" % dup)
    if rect is None:
        rect = _WHOLE[0]
        if rect is None or rect[2] != cv.w or rect[3] != cv.h:
            rect = _WHOLE[0] = (0, 0, cv.w, cv.h)
    flags = ((_DESK if desk else 0) | (_WINDOW if window else 0)
             | (_ELSEWHERE if elsewhere else 0) | (_KEY_EDGE if edge else 0))
    t[3] = flags & (_DESK | _WINDOW)
    if app is not None:
        flags |= (_app_row(app) + 1) << _APP_SHIFT
    if inp is not None:
        inp[_IN_DT] = _DT_MS[0]
    settled = _C.api_begin(ctx, inp, rect, flags, _fs(cv))
    if _C.api_moving(ctx):
        _MOVING[0] = True
    return settled


def bar_lent(ctx):
    """What the app whose context is `ctx` lent its bar in the frame it drew
    last (`ui.bar`): (title, icon slots, half-strength bits, back, chip), or
    None; `chip` the words of the tool that names no icon (its slot -1),
    "" for none."""
    got = _C.api_bar_lent(ctx)
    if got is None:
        return None
    last = _BAR_LENT
    if last[0] is got:                  # the module's answer is the last one's
        return last[1]
    title, slots, off, chip = got
    last[0] = got
    last[1] = (title, slots, off & 0x3F, bool(off & 0x80000000), chip)
    return last[1]


_BAR_LENT = [None, None]                # the module's last answer, and ours to it


def bar_tap(ctx, i):
    """The host's bar activated tool `i` (-2: its way back) of the bar `ctx`
    lent; the app's next `ui.bar` answers it."""
    _C.api_bar_tap(ctx, i)


_HOST_BUTTONS = ("left", "right", "up", "down", "a", "b", "run")
_UP = 1 << 2                            # "up"'s bit in a frame's buttons
_DIRS = 15                              # the four directions' bits
# The host whose frame began last with the keys (`steps_dead`).
_KEYS_HOST = [None]


def _rep_dirs(rw):
    """The directions a `rep_word` steps, as a frame's direction bits."""
    m = 0
    if rw & 0xFF:
        m |= 1
    if rw & 0xFF00:
        m |= 2
    if rw & 0xFF0000:
        m |= 4
    if (rw >> 24) & 0xFF:               # no constant past a small int's range
        m |= 8
    return m


def _pays(cv, p):
    """Whether moving region R (plan `p`: its height p[3], the floor's rows
    p[5], the rows of R's pictures the floor leaves p[6]) out of the last
    frame costs canvas `cv` less than drawing what of R the floor does not:
    its `SHIFT_COST` per mille of a plain row's raster each row it copies
    costs, None where unknown (then it moves). A picture's rows cost more to
    draw than any copy, so a move that leaves one undrawn pays."""
    c = getattr(cv, "SHIFT_COST", None)
    return c is None or p[6] > 0 or p[5] * 1000 + c * p[3] < 1000 * p[3]


def steps_dead(rw, console=None):
    """Whether a held key's (or the ball's) steps `rw`, with no press
    beside them, would move nothing on the page whose frame `console` drew
    last took the keys in: the console then marks no frame for them."""
    h = _KEYS_HOST[0]
    return h is not None and h._console is console and h.dead(rw)
_EDGE = [False]                         # an up press went past a page's top


def edge_take():
    """Whether an up press went past the top of a page drawn since the
    last call (`api_edge`, outside a dialog): the shell walks focus onto
    its bar. Reading clears it."""
    got = _EDGE[0]
    _EDGE[0] = False
    return got


# What every hint strip names while the keys are on the shell's bar
# (`BarLayer.focus_enter`): left and right walk its items, confirm opens one,
# back comes off it.
BAR_KEYS = ("change", "Move", "confirm", "Open", "back", "Back")
_ON_BAR = [False]


def on_bar(on=None):
    """Whether the keys are on the shell's bar, so a hint strip names
    `BAR_KEYS` in its own keys' place; `on` sets it first."""
    if on is not None:
        _ON_BAR[0] = bool(on)
    return _ON_BAR[0]


def bar_or(items):
    """`items`, or `BAR_KEYS` while the keys are on the bar (`on_bar`)."""
    return BAR_KEYS if _ON_BAR[0] else items

# The words of a frame's input (moy_ui_api.h's MOY_UI_IN_*): the buttons held
# and pressed, the pointer's x, y and flags, the key typed, its modifiers,
# the wheel's steps across and down, the frame's clock (ui_frame's), the
# directions' steps beyond their presses (the input table's `rep_word`) and
# a held key's typing repeats (its byte | count << 16).
UI_IN_LEN = 12
_IN_DT = 9
_IN_REP = 10
_IN_KEYREP = 11
_P_MIDDLE = 8                           # MOY_UI_P_MIDDLE: the middle button held
_PB_MIDDLE = 2                          # moy_input's PB_MIDDLE


class AppHost:
    """The host side of one shipped app's verbs (`ctx.ui`, for an app class
    with `UI = True`; Settings, the update screen and the wifi popover hold
    their own): its context, and the frame's input gathered from the app
    protocol's handlers in the run snapshot's shape. `keys` and `point`
    answer whether the app owes a frame, a page of it moving on its own
    included (`moving`; its `damage.all()` is the caller's);
    `begin` begins the frame. A tap is a press frame then a release frame
    where it went down; a host that passes `point` the pointer's `down` on
    every sample also drags (the press held while it moves, released where
    it comes up), which a page scrolls by. A frame that was not settled
    owes one more, so the rest state a press or a move leaves is painted,
    then nothing. `app` is the app's id (its buttons take the theme's
    `app_buttons[app]`)."""

    def __init__(self, app=None):
        self.app = app
        self.ctx = ui_context()
        self.inp = array("i", [0] * UI_IN_LEN)
        self.tap = 0                    # 1 a press to begin, 3 held, 2 its release
        self.hold = False               # the pointer is down since the press
        self.owed = False
        self.key = 0                    # the key typed last frame (a press is its change)
        self.px = self.py = -1          # where the pointer was last seen, in the app's space
        self.mid = False                # the middle button is held
        self.ring = PaintRing(1)        # what the frame before left of the page in its buffer
        self._plan = array("i", [0] * 8)
        self._again = False           # `again` since the last begin: no shift frame
        # A dialog's rows frame is the host's to allow: `held(plan)` answers
        # whether every buffer holds the page under the dialog the plan names
        # (its card and drop, plan[0:4], standing plan[7] frames) as the
        # frames before left it (`Hold.rows`); None, never.
        self.held = None
        self._t3 = 0
        self._keys = False              # the frame begun last took the keys
        self.shifting = False           # the frame begun last draws its pages' floor alone
        self._dead = 0                  # the directions whose steps moved nothing last frame
        self._began = -1                # the painted frame it began last
        self._console = None            # ...and the console that painted it
        # the rows the app asks the frame begun next to draw alone (`rows`):
        # their count, the rects, and a region it moves first by dy
        self._want = -1
        self.own_rows = False           # the app's rows alone: never a plan of the engine's own
        self._wr = array("i", [0] * (4 * HOST_ROWS))
        self._wm = array("i", [0] * 5)

    def reset(self):
        """A fresh context: focus, kept state and input forgotten."""
        self.ctx = ui_context()
        self.ring.clear()
        self.clear()

    def clear(self):
        """Forget the input gathered for the next frame."""
        a = self.inp
        for n in range(UI_IN_LEN):
            a[n] = 0
        self.tap = 0
        self.hold = False
        self.owed = False
        self.key = 0

    def keys(self, i):
        """Take this frame's buttons and typed key from input state `i`."""
        a = self.inp
        b = 0
        for n in range(len(_HOST_BUTTONS)):
            if i.pressed(_HOST_BUTTONS[n]):
                b |= 1 << n
        k = getattr(i, "last_key", 0) or 0
        if not isinstance(k, int):
            k = ord(k[0]) if k else 0
        a[0] = a[1] = b
        typed = k != 0 and k != self.key
        self.key = k
        a[5] = k if typed else 0        # the engine sees each press once
        a[6] = getattr(i, "mods", 0) or 0
        a[7] = getattr(i, "wheel_x", 0)
        a[8] = getattr(i, "wheel_y", 0)
        rw = getattr(i, "rep_word", 0)
        kn = getattr(i, "key_repeats", 0)
        mid = bool(getattr(i, "pointer_buttons", 0) & _PB_MIDDLE)
        if (rw and not b and not typed and not kn and not mid and mid == self.mid
                and not a[7] and not a[8] and self.dead(rw)):
            rw = 0                      # a held key's steps where they move nothing
        a[_IN_REP] = rw - 0x100000000 if rw >> 31 else rw    # a small-int test: no bigint
        a[_IN_KEYREP] = (i.repeat_key & 0xFFFF) | kn << 16 if kn else 0
        moved = mid or mid != self.mid or a[7] or a[8] or rw or kn
        self.mid = mid
        return (bool(b) or typed or self.owed or self.tap != 0 or bool(moved)
                or _C.api_owes(self.ctx) or _C.api_moving(self.ctx))

    def point(self, x, y, click, down=None):
        """A pointer sample in the app's coordinates; a click starts a tap.
        With `down` (the pointer held), the press stays held while it moves
        and is released where it comes up."""
        self.px = int(x)
        self.py = int(y)
        if click:
            self.inp[2] = int(x)
            self.inp[3] = int(y)
            self.tap = 1
            self.hold = bool(down)
            return True
        if down is None or not self.hold:
            return False
        self.inp[2] = int(x)
        self.inp[3] = int(y)
        if not down:
            self.hold = False
            self.tap = 2
        return True

    def focus(self, id):
        """Move focus to `id` ahead of the next frame, so that frame's keys
        act on it and its begin shows it (`ui.focus` inside a frame moves it
        after them, and owes the next frame)."""
        _C.api_focus_ahead(self.ctx, id)

    def box(self, id):
        """(x, y, w, h) where stop `id` drew in the last frame, or None."""
        return _C.api_box(self.ctx, id)

    def resume(self, cv, th):
        """Point the verbs at this context again, on `cv` under theme `th`,
        beginning no frame: what its last frame left -- focus, stops, the
        bar it lent -- stands, and a verb that only draws (`ui.cursor`) draws
        as it did. A frame that paints a part of its page again (Paint's
        stroke frame) draws through it."""
        t = UI_TARGET
        t[0] = self.ctx
        t[1] = cv
        t[2] = _look(th)
        t[3] = self._t3
        if self._keys:
            _KEYS_HOST[0] = self

    def begin(self, cv, th, rect, desk=False, window=False, elsewhere=False):
        """Begin the frame on `cv` under theme `th`, its page `rect` (in a
        window's body when `window`): whether it is settled. `elsewhere`:
        the keys are a part's the app drives itself (a story's slide), so no
        stop holds focus this frame, as while the keys are on the shell's
        bar (`on_bar`): one focus mark a screen."""
        a = self.inp
        t = self.tap
        a[4] = 7 if t == 1 else 3 if t == 3 else 1 if t == 2 else 0
        if (self.mid or a[7] or a[8]) and self.px >= 0:
            if not a[4]:                # the wheel and the middle button act where it rests
                a[2] = self.px
                a[3] = self.py
            a[4] |= 1 | (_P_MIDDLE if self.mid else 0)
        self.ring.close(_C.api_shift_drawn(self.ctx) & 4)
        away = elsewhere or _ON_BAR[0]
        settled = ui_frame(self.ctx, cv, th, rect, a, desk, window, self.app,
                           elsewhere=away, edge=True)
        self._t3 = UI_TARGET[3]
        self._shift(cv, th)
        if a[1] & _UP and _C.api_edge(self.ctx):
            _EDGE[0] = True
        # The directions this frame's steps took, where they moved nothing
        # and the page owes nothing: a held key's further steps that way are
        # no frame (`dead`), until a frame begins otherwise.
        d = (a[1] & _DIRS) | _rep_dirs(a[_IN_REP])
        self._dead = (d if d and not (a[1] & ~_DIRS) and not a[5] and not t and not a[7]
                      and not a[8] and settled and not _C.api_moved(self.ctx)
                      and not _C.api_moving(self.ctx) else 0)
        self._began = _PAINT[0]
        self._console = _PAINT[2]
        self._keys = not away
        if not away:
            _KEYS_HOST[0] = self
        self.tap = (3 if self.hold else 2) if t == 1 else 3 if t == 3 else 0
        a[0] = a[1] = a[5] = a[7] = a[8] = 0
        self.owed = not settled
        return settled

    def rows(self, rects, n, move=None):
        """Ask that the frame begun next draw only the `n` rects of
        `rects` (x, y, w, h each, HOST_ROWS at most), the page's rows its
        app changed, after moving `move` (x, y, w, h, dy: a region of the
        page whose pixels the app scrolled by whole rows) out of the last
        frame. The frame is such a rows frame where the engine licenses it
        (`shifting` then says so): the page's gen held and nothing the
        engine applies moved; the app then joins those rects to the floor
        (`floor_take`) and draws its own pixels only inside them. A frame begun without a request draws as the engine
        plans it, or whole where the app draws rows the engine does not
        see (`own_rows`)."""
        w = self._wr
        for i in range(4 * n):
            w[i] = rects[i]
        m = self._wm
        if move is None:
            m[4] = 0
            m[2] = 0
        else:
            for i in range(5):
                m[i] = move[i]
        self._want = n

    def _shift(self, cv, th):
        """The frame begun as a shift frame where it may be one
        (docs/scroll_and_text_2026-10.md §3.3): the engine licenses it from
        the input it applied and the gens the last pass drew, the ring
        vouches that the frame before drew the page whole into the buffer
        `shift_last` reads, and the canvas moves that frame's pixels of the
        page's region R by the scroll's move; the pass then draws R's floor
        alone."""
        f = _PAINT[0]
        ring = self.ring
        key = _GEN + _GEN + (1 if _MARKS_ON[0] else 0)
        p = self._plan
        self.shifting = False
        again = self._again
        self._again = False
        w = _WIN[0]
        if w:
            ok = w == 2 and ring.vouch_key(key, th)     # the WM's shown ring vouches
        else:
            ok = _PAINT[1] and ring.vouch(cv, f, key, th)
        # A canvas whose copy costs about what the raster it saves does
        # (`SHIFT_COST`, per mille of a row's raster a row it copies costs;
        # None where unknown) draws the frame whole instead. A dialog's rows
        # frame is the host's to allow (`held`), and always pays: the frame
        # it saves puts the page under the dialog back too.
        # A pass that changed what it drew before the change (`again`) left
        # the last frame's page unlike the app's: this frame draws whole.
        want = self._want
        self._want = -1
        planned = ok and not again and _C.api_shift_plan(self.ctx, p)
        if want >= 0 and ok and not again and not planned:
            self._host_rows(cv, want, p)    # the app's rows
        elif planned and (not self.own_rows or want >= 0 and not p[4] and not self._wm[4]) and (
                _pays(cv, p) if not p[7] else self.held is not None and self.held(p)):
            # the engine's plan; an app with rows of its own takes a rows
            # plan where it asked for one, its rows joining the floor
            # (`floor_take`)
            if cv.shift_last(p[0] + getattr(cv, "_ox", 0), p[1] + getattr(cv, "_oy", 0),
                             p[2], p[3], 0, p[4]):
                cv.shift_fence()        # the floor draws over rows the move wrote
                _C.api_shift_arm(self.ctx)
                self.shifting = True
                _CHANGE[0] = _SHIFT if p[4] else _ROWS
        ring.note(f, key, 0, None, cv, th, _PAINT[1] or w != 0)

    def floor_rects(self, out):
        """This rows frame's floor as the pass has it, into `out` (x, y, w,
        h each): its count, 0 where the frame draws whole. The app's rows
        each rect reaches draw again."""
        if not self.shifting:
            return 0
        n = _C.api_floor_into(self.ctx, out)
        return n if n > 0 else 0

    def floor_take(self, cv, x, y, w, h):
        """Rect x, y, w, h joins the rows frame's floor over the page's
        ground, which the app draws its rows on: whether it did."""
        return self.shifting and _C.api_floor_take(self.ctx, cv, UI_TARGET[2], x, y, w, h)

    def break_rows(self):
        """This rows frame draws whole from here: the app's rows could not
        join its floor."""
        if self.shifting:
            _C.api_shift_break(self.ctx)

    def _host_rows(self, cv, n, p):
        """The frame as the rows the app asked for (`rows`): the page moved
        out of the last frame, the app's region by its rows, the floor
        those rects."""
        if not _C.api_shift_rows(self.ctx, self._wr, n, p) or not _pays(cv, p):
            return
        ox = getattr(cv, "_ox", 0)
        oy = getattr(cv, "_oy", 0)
        m = self._wm
        if not cv.shift_last(p[0] + ox, p[1] + oy, p[2], p[3], 0, 0):
            return
        if m[4] and m[2] > 0 and not cv.shift_last(m[0] + ox, m[1] + oy, m[2], m[3], 0, m[4]):
            return
        cv.shift_fence()
        _C.api_shift_arm(self.ctx)
        self.shifting = True
        _CHANGE[0] = _SHIFT if m[4] else _ROWS

    def dead(self, rw):
        """Whether a held key's (or the ball's) steps `rw` (the input
        table's `rep_word`) would move nothing: each is a direction the
        steps of the last painted frame that drew (the repeats of it after
        it seen through) took to no effect, and the page owes nothing and
        does not move."""
        d = self._dead
        return (d != 0 and (_rep_dirs(rw) & ~d) == 0 and self._began == _drew_last()
                and not self.owed and self.tap == 0 and not _C.api_owes(self.ctx)
                and not _C.api_moving(self.ctx))

    def again(self):
        """The pass changed what it had drawn before the change (a page it
        opened, a place it picked): the next frame is owed, as one the
        engine owes (`keys` answers it, and the console paints it with no
        input: `again_take`), never the page marked whole."""
        self.owed = True
        self._again = True
        _AGAIN[0] = True

    def tap_armed(self):
        """Whether the pointer is down on a stop its release may activate
        (no page's drag, no fling it caught): the release frame's pass may
        act on it."""
        return bool(_C.api_tap_armed(self.ctx))

    def owes(self):
        """Whether the pass begun last owes the frame after it (a popover
        laid out for another body, a focus it opened): that frame's layout
        is not the last one's."""
        return bool(_C.api_owes(self.ctx))

    def quiet(self):
        """Whether no input waits for the next frame: no button, typed key,
        repeat, wheel or press, and no frame owed."""
        a = self.inp
        return not (a[1] or a[5] or a[7] or a[8] or a[_IN_REP] or a[_IN_KEYREP]
                    or self.tap or self.owed)

    def still(self):
        """Whether the page this context drew last is what it draws next:
        its input moved nothing, no tap waits, it owes no frame and no page
        of it moves on its own."""
        return (not self.owed and self.tap == 0 and not _C.api_owes(self.ctx)
                and not _C.api_moving(self.ctx))

    @property
    def moving(self):
        """Whether a page of this context flings or glides: its next frame
        is due though no input comes."""
        return _C.api_moving(self.ctx)

    @property
    def moved(self):
        """Whether the frame begun last moved the focus or a scroll, which
        its pass drew: a frame that owes nothing but is not the one before
        it (a popover's card the WM owes every buffer)."""
        return _C.api_moved(self.ctx)


def wash_page(src=None, also=None):
    """Wash, on the frame's canvas, what a dialog begun now would: the
    surface, less the page's hint band at its foot; the rect it washed.
    The dialog then takes `{"wash": False}`. `src` and `also` as `scrim`'s."""
    t = UI_TARGET
    box = _C.api_wash_box(t[0])
    if src is None and also is None:
        _C.api_wash(t[0], t[1], t[2])
    else:
        _C.scrim(t[1], t[2], box, src, also)
    return box


# What the held page's dialog takes: the page under it is washed already.
NO_WASH = {"wash": False}


# What the page under a dialog draws this frame (`Hold.under`): put back
# washed, the dialog drawn over it held; the last frame's, taken from its
# buffer; or drawn.
HOLD_PUT, HOLD_LAST, HOLD_DRAW = 0, 1, 2


class Hold:
    """The page under a modal, held while the modal is up
    (docs/surface_model_v1.md §14.5): a dialog's page, the system menu's
    home, the run's frame under the pause card. A modal takes the keys and
    the pointer, so the page under it does not change while it stays: the
    first frame draws the page and washes it (`wash`, or `keep` where the
    scrim stored to the held layer as it landed), the rest of the page's
    rect copied there; each frame after puts that copy back (`put`)
    instead of drawing the page and its wash, and draws the modal over it.
    `key` is what the page's pixels are a function of while the modal is
    up (the modal itself, the page's rect, the look); a key that differs,
    a page that was not still, anything animating, a canvas that retains
    nothing or one drawing into a viewport of its buffer draws it whole.

    A page nothing draws again -- the run's frozen frame under the pause
    card -- is kept as it was too (`snap`), put back for the frame the card
    washes and for the resumed game (`unsnap`).

    A dialog's first frame takes the page from the last frame instead of
    drawing it where that frame drew it alone and still, with no dialog up
    (`shown`), and the input that opened the dialog changed nothing else
    in it with nothing reported before it (`opened`): `last` says so, and
    the wash reads the page from the buffer that frame drew into.

    A frame whose input only typed into the dialog's field or moved its
    focus puts nothing back (a dialog's rows frame:
    docs/scroll_and_text_2026-10.md §5): the dialog's frame begins ahead
    of the page (`rows_frame`), and where its host allows it (`ok`, the
    host's `held`) the dialog draws its changed rows alone. That is where
    every buffer holds the page as the frames before left it (`rows`: each
    a frame the dialog drew over the hold alone on the glass, `note`), or
    on the desk where the Editor's window buffer holds it (`desk_frame`),
    and only on a frame whose dialog gave the gen it gave the last."""

    def __init__(self):
        self.key = None
        self.snapped = False            # the page kept as it was (`snap`)
        self._shown = -1                # the frame that drew the page alone and still
        self._over = -1                 # the frame a dialog opened over it first draws
        self._src = None                # this frame's page: the last frame's buffer
        self._at = PaintRing()          # the frames that drew the dialog over the hold
        self._ask = [None, None, None, 0]   # this frame's console, canvas, key, dt (`ask`)
        self.desk = -1                  # the frame the desk vouched its window holds the page
        self.on_desk = -1               # the frame the desk drew the dialog into its window
        self.begun = -1                 # the frame the dialog's host began ahead of the page
        self._gen = None
        self._same = False

    # -- the page held washed

    def put(self, ws, cv, rect, key, dt=0):
        """Put the held page back into `rect`: whether it was held under
        `key` (else the frame draws the page)."""
        held = ws.held_pages
        if (self.key is None or held[1] is not self or self.key != key
                or ws._animating(dt)):
            return False
        x, y, w, h = rect
        cv.blit_strip_rect(held[0], 0, 0, x, y, w, h)
        return True

    def layer(self, ws, cv, still, dt=0):
        """The held layer, where this frame's page may be held: only where
        it is `still`, nothing animates and the canvas keeps its pixels and
        draws into its whole buffer (a pane's viewport is copied from as
        the buffer it lies in, so it is not). None otherwise, or with no
        room for it."""
        if not still or ws._animating(dt) or not _whole_retained(cv):
            return None
        return held_layer(ws, cv)

    def wash(self, ws, cv, rect, key, still, dt=0):
        """Wash the page in `rect` under the dialog begun on the frame's
        verbs (`wash_page`) and hold it under `key` where it may be held
        (`layer`): the wash stores to the held layer as it lands, and what
        it leaves of `rect` -- the page's hint band, which the dialog's
        keys drew this frame -- is copied there after it. Where `last`
        took the page from the last frame, the wash reads it from that
        frame's buffer."""
        src = self._src
        self._src = None
        lay = self.layer(ws, cv, still, dt)
        also = None if lay is None else getattr(lay, "_buf", None)
        box = wash_page(src, also)
        self._store(ws, cv, lay, key, (rect,) if also is None else _outside(rect, box))

    def keep(self, ws, cv, rect, key, still, dt=0, washed=()):
        """Hold `rect` of the frame drawn so far under `key`, where it may
        be held (`layer`); `washed`: rects a scrim already stored to the
        held layer (`scrim`'s `also`), which are not copied again."""
        parts = [rect]
        for w in washed:
            parts = [p for r in parts for p in _outside(r, w)]
        self._store(ws, cv, self.layer(ws, cv, still, dt), key, parts)

    def _store(self, ws, cv, lay, key, parts):
        self.key = None
        if lay is None:
            return
        for r in parts:
            lay.blit_strip_rect(cv, 0, 0, r[0], r[1], r[2], r[3])
        ws.held_pages[1] = self
        self.key = key

    def drop(self):
        """The modal closed: the page is no longer held, nor kept."""
        self.key = None
        self.snapped = False
        self._at.clear()

    # -- the page kept as it was

    def snap(self, ws, cv):
        """Keep the whole frame of `cv` as it is, before a modal draws over
        it, where there is room for it (`held_layer`)."""
        lay = held_layer(ws, cv, 2)
        self.snapped = lay is not None
        if lay is not None:
            lay.blit_strip(cv, 0, 0)
            ws.held_pages[3] = self

    def unsnap(self, ws, cv):
        """Put the frame `snap` kept back into `cv`."""
        if self.snapped:
            lay = ws.held_pages[2]
            cv.blit_strip_rect(lay, 0, 0, 0, 0, lay.w, lay.h)

    # -- a dialog's page: its first frame, its rows frames

    def under(self, ws, key, rect, dt=0):
        """What the page in `rect` draws this frame under the dialog up,
        `key` what it is a function of (None with no dialog up): HOLD_PUT,
        HOLD_LAST or HOLD_DRAW. The dialog's rows frame is asked first
        (`rows_frame`)."""
        if key is None:
            if self.key is not None:
                self.drop()
            return HOLD_DRAW
        cv = ws.sys_canvas
        if self.put(ws, cv, rect, key, dt):
            return HOLD_PUT
        return HOLD_LAST if self.last(ws, cv, rect, dt) else HOLD_DRAW

    def note(self):
        """The frame drew the dialog over the page held (put back, washed
        into the hold, or left as it was by a rows frame): counted where it
        was alone on the glass."""
        if self.key is None or not _PAINT[1]:
            self._at.clear()
        else:
            self._at.note(_PAINT[0])

    def ask(self, ws, cv, key, dt=0):
        """This frame's terms for `rows`, before the dialog's frame begins."""
        a = self._ask
        a[0] = ws
        a[1] = cv
        a[2] = key
        a[3] = dt

    def rows(self, plan):
        """Whether the dialog's rows frame `plan` (`AppHost.held`) may leave
        the page as it is: it is held under the key `ask` gave, nothing
        animates, and each of the RETAINED_FRAMES painted frames before this
        one drew the dialog the plan names (standing plan[7] frames) over
        the hold, so every buffer holds the page outside the dialog's card."""
        ws, cv, key, dt = self._ask
        self._ask[0] = self._ask[1] = None
        n = getattr(cv, "RETAINED_FRAMES", 0)
        if (ws is None or self.key is None or ws.held_pages[1] is not self or self.key != key
                or n < 1 or n > self._at.size or plan[7] < n or ws._animating(dt)):
            return False
        f = _PAINT[0]
        for k in range(n):
            if self._at.frame(k) != f - 1 - k:
                return False
        return True

    def shown(self, ws, cv, still, dt=0):
        """This frame drew the page with no dialog up: noted where it was
        `still` and is all the frame draws on a canvas that keeps it (no
        banner, overlay, cursor or pill over it, nothing animating)."""
        alone = getattr(ws.wm, "content_alone", None)
        ok = (still and not getattr(cv, "covered", False) and alone is not None
              and alone() and not ws._animating(dt) and _whole_retained(cv))
        self._shown = ws._frames_drawn if ok else -1

    def opened(self, ws):
        """A dialog opened over the page from an input that changed nothing
        else in it: its first frame may take the page from the last frame
        (`last`), where that frame drew it (`shown`) and no change was
        reported since (the console's `_dirty_in`)."""
        f = ws._frames_drawn
        self._over = f if self._shown == f - 1 and not ws._dirty_in else -1

    def last(self, ws, cv, rect, dt=0):
        """Whether this frame, the first of the dialog `opened` over the
        page, takes the page in `rect` from the last frame instead of
        drawing it (else the frame draws the page). One retained buffer
        holds it already; more give the buffer that frame drew into, which
        the wash reads (`last_buffer`), or copy it out (`copy_last`)."""
        over = self._over
        self._over = -1
        self._src = None
        if over != ws._frames_drawn or ws._animating(dt) or not _whole_retained(cv):
            return False
        if cv.RETAINED_FRAMES == 1:
            return True
        lb = getattr(cv, "last_buffer", None)
        src = None if lb is None else lb()
        if src is not None:
            self._src = src
            return True
        cl = getattr(cv, "copy_last", None)
        return cl is not None and bool(cl(rect[0], rect[1], rect[2], rect[3]))

    def desk_frame(self, holds):
        """The desk Editor draws the dialog this frame, into its window's
        buffer: a window frame (`window_frame`), whose buffer holds the page
        as the frame before left it where `holds`."""
        f = _PAINT[0]
        self.on_desk = f
        self.desk = f if holds else -1

    def rows_frame(self, ws, key, gen, host, rect, desk, dt=0):
        """Begin the dialog's frame on its `host` ahead of the page, once a
        painted frame, `key` what the page is a function of and `gen` the
        dialog's: on the desk a window frame (`window_frame`), one the desk
        vouches for where it said so. Whether it is the dialog's rows frame,
        which draws the dialog's changed rows alone and leaves the page as
        it is; else the frame draws the page and the dialog as ever, the
        dialog's draw resuming the frame begun here (`dialog`)."""
        f = _PAINT[0]
        if self.begun == f:
            return False
        cv = ws.sys_canvas
        self.ask(ws, cv, key, dt)
        self._same = gen == self._gen
        self._gen = gen
        w = 2 if self.desk == f else 1 if self.on_desk == f else 0
        if w:
            _WIN[0] = w
        try:
            host.begin(cv, ws.theme_colors, rect, desk)
        finally:
            if w:
                _WIN[0] = 0
        self.begun = f
        return host.shifting

    def dialog(self, host, cv, th, rect, desk):
        """Begin the dialog's draw on its `host`, or resume the frame
        `rows_frame` began ahead of the page this frame."""
        if self.begun == _PAINT[0]:
            self.begun = -1
            host.resume(cv, th)
        else:
            host.begin(cv, th, rect, desk)

    def floor_box(self, host, out):
        """The floor's bounding rect the dialog's last pass drew into
        `out`, an array('i') of four: whether it drew one."""
        return _C.api_floor_box(host.ctx, out)

    def ok(self, plan):
        """The dialog's host's `held`: whether its rows frame `plan` may
        leave the page as it is."""
        if not self._same:
            return False
        if self.desk == _PAINT[0]:
            return plan[7] >= 1
        return self.rows(plan)



# -- TextView: a text area in visual rows (native/moy_ui/moy_ui_text.h) ---------------
#
# The view's words, in moy_ui_text.h's MOY_UI_TV_* order (tests/test_text_view.py
# holds the two together).
(TV_X, TV_Y, TV_W, TV_H, TV_LH, TV_CELL, TV_TEXT_H, TV_FS, TV_FONT, TV_TX, TV_MARK,
 TV_GUTTER, TV_COLS, TV_TOP, TV_OFF, TV_LEFT, TV_CARET_ROW, TV_CARET_COL, TV_SEL_R0,
 TV_SEL_C0, TV_SEL_R1, TV_SEL_C1, TV_ERR_ROW, TV_FIND_CI, TV_SYNTAX, TV_FLAGS, TV_BG,
 TV_CARET, TV_SEL, TV_GUT, TV_GUT_CUR, TV_VALUE, TV_ERR, TV_TONE0) = range(34)
TT_N = 9                        # the tones (MOY_UI_TT_*): text, keyword, string, number,
                                # comment, builtin, value, danger, dim
(TV_KEY, TV_OVER_Y0, TV_OVER_Y1, TV_LINES, TV_NUM0, TV_DY, TV_FLOOR, TV_WHOLE, TV_DRAWN,
 TV_FLOOR_Y0, TV_FLOOR_Y1, TV_LEN) = range(TV_TONE0 + TT_N, TV_TONE0 + TT_N + 12)
TVF_FADE, TVF_GROUND, TVF_CLIP = 1, 2, 4
SYN_NONE, SYN_PYTHON, SYN_LUA, SYN_JSON, SYN_SGR = range(5)
SYNTAX = {"python": SYN_PYTHON, "lua": SYN_LUA, "json": SYN_JSON, "sgr": SYN_SGR}


def text_words(syntax, words, tone):
    """Install the words `syntax` ("python" or "lua") colours in `tone`
    (an MOY_UI_TT_* index); a word installed again takes its new tone."""
    _C.text_words(SYNTAX[syntax], "\0".join(words).encode(), tone)


_CODE_ROLES = ("code_text", "code_keyword", "code_string", "code_number", "code_comment",
               "code_builtin")


def text_tones(content):
    """The live theme's code roles (`content`, a role -> colour index dict:
    `Appearance.content_colors`) as the colours a text view's syntax and SGR
    tones take (`ui.text_view`, moy_ui_api.h's moy_ui_text_tones)."""
    _C.text_tones([content.get(r) for r in _CODE_ROLES])


class TextView:
    """One text area's rows in C (docs/scroll_and_text_2026-10.md §5): R, a
    rect of rows, drawn through `moy_ui.text_draw` from a list of lines, a
    line a row. The caller fills `v` (the TV_* words) and draws; the view
    plans the frame against what it drew last (`moy_ui.text_plan`) and,
    where the buffer the canvas's `shift_last` reads held R whole as this
    view drew it the frame before (its `PaintRing`, or under a window the
    WM's word: `window_frame`), moves those pixels by the scroll and draws
    the floor alone -- the rows whose text or marks moved, the band the
    scroll uncovered, and what an overlay covered last frame (`over`).
    `kind` says what the last draw was: "whole", "rows" or "shift"."""

    def __init__(self):
        self.state = bytearray(_C.text_size())
        self.v = array("i", [0] * TV_LEN)
        self.ring = PaintRing(1)
        self.kind = None
        self.over = array("i", [0, 0])  # what overlays covered of R this frame (y0, y1)
        self._w = 0                     # the window's word the frame was planned under
        # the last frames' plans, a slot a frame (n % _TV_HIST): the frame,
        # its kind (0 whole, 1 rows, 2 shift) and the band its floor spans
        self._hist = array("i", [-1] * (4 * _TV_HIST))
        self._band = array("i", [0, 0])  # `_lag`'s answer: the band R lags

    def reset(self):
        """The next frame draws whole."""
        _C.text_reset(self.state)
        self.ring.clear()

    def cover(self, y0, y1):
        """Rows y0..y1 of R are drawn over this frame (an overlay): the next
        frame draws them again."""
        o = self.over
        if o[1] <= o[0]:
            o[0] = y0
            o[1] = y1
            return
        if y0 < o[0]:
            o[0] = y0
        if y1 > o[1]:
            o[1] = y1

    def plan(self, cv, lines, err=None, find=None, win=None):
        """Before anything draws in R this frame: whether it is a floor
        frame (R's pixels moved out of the last frame's buffer, its floor
        left to draw), else a whole one. The caller fills R's ground on a
        whole frame (`ground` False) or leaves R alone. `win`, where given,
        is the window's word in place of the WM's (`window_frame`): a
        content that keeps its own frames hands it down (the desk Editor)."""
        v = self.v
        o = self.over
        v[TV_OVER_Y0] = o[0]
        v[TV_OVER_Y1] = o[1]
        o[0] = o[1] = 0
        f = _PAINT[0]
        w = _WIN[0] if not win else win
        self._w = w
        ring = self.ring
        if w:
            ok = w >= 2 and ring.vouch_key(0)
        else:
            ok = _PAINT[1] and ring.vouch(cv, f, 0)
        self.kind = "whole"
        may = _C.text_plan(self.state, v, lines, err, find)
        if ok and may and _text_pays(cv, v):
            dy = v[TV_DY]
            ox = getattr(cv, "_ox", 0)
            oy = getattr(cv, "_oy", 0)
            lag = self._lag(f, getattr(cv, "RETAINED_FRAMES", 1)) if w == 3 and not dy else -1
            if lag < 0:
                moved = cv.shift_last(v[TV_X] + ox, v[TV_Y] + oy, v[TV_W], v[TV_H], 0, dy)
            elif lag:
                b = self._band
                moved = cv.shift_last(v[TV_X] + ox, b[0] + oy, v[TV_W], b[1] - b[0], 0, 0)
            else:
                moved = True
            if moved:
                if lag:
                    cv.shift_fence()
                self.kind = _SHIFT if dy else _ROWS
                _CHANGE[0] = self.kind
        self._note(f, v)
        return self.kind != "whole"

    def _lag(self, f, n):
        """Where the window's word says the buffer this frame paints holds
        R as frame f - n left it (`win` 3): whether R's pixels there lag
        the last frame's only in the bands the rows frames since drew --
        1, their span in `_band`, 0 none -- else -1."""
        h = self._hist
        y0 = 1 << 30
        y1 = -y0
        for b in range(1, n):
            i = ((f - b) % _TV_HIST) * 4
            if h[i] != f - b:
                continue                # the view drew nothing that frame
            if h[i + 1] != 1:
                return -1
            if h[i + 3] > h[i + 2]:
                y0 = h[i + 2] if h[i + 2] < y0 else y0
                y1 = h[i + 3] if h[i + 3] > y1 else y1
        if n > _TV_HIST:
            return -1
        if y1 <= y0:
            return 0
        self._band[0] = y0
        self._band[1] = y1
        return 1

    def _note(self, f, v):
        h = self._hist
        i = (f % _TV_HIST) * 4
        h[i] = f
        h[i + 1] = 0 if self.kind == "whole" else 2 if self.kind == _SHIFT else 1
        h[i + 2] = v[TV_FLOOR_Y0]
        h[i + 3] = v[TV_FLOOR_Y1]

    def draw(self, cv, look, lines, err=None, find=None, ground=True):
        """Draw the rows the plan left (every row on a whole frame), each
        on its ground where `ground`, and note the frame. The rows drawn."""
        v = self.v
        if ground:
            v[TV_FLAGS] |= TVF_GROUND
        else:
            v[TV_FLAGS] &= ~TVF_GROUND
        n = _C.text_draw(self.state, cv, look, v, lines, err, find, self.kind == "whole")
        self.ring.note(_PAINT[0], 0, 0, None, cv, None, _PAINT[1] or self._w != 0)
        return n


def _text_pays(cv, v):
    """Whether moving R out of the last frame costs `cv` less than drawing
    the rows the floor leaves (`SHIFT_COST`, as `_pays`)."""
    c = getattr(cv, "SHIFT_COST", None)
    h = v[TV_H]
    return c is None or v[TV_FLOOR] * 1000 + c * h < 1000 * h

def _outside(r, hole):
    """Rect `r` less rect `hole`: up to four rects, the bands above, below,
    left and right of what they share."""
    x0, y0, x1, y1 = r[0], r[1], r[0] + r[2], r[1] + r[3]
    hx0, hy0 = max(x0, hole[0]), max(y0, hole[1])
    hx1, hy1 = min(x1, hole[0] + hole[2]), min(y1, hole[1] + hole[3])
    if hx1 <= hx0 or hy1 <= hy0:
        return [r]
    out = []
    if hy0 > y0:
        out.append((x0, y0, r[2], hy0 - y0))
    if hy1 < y1:
        out.append((x0, hy1, r[2], y1 - hy1))
    if hx0 > x0:
        out.append((x0, hy0, hx0 - x0, hy1 - hy0))
    if hx1 < x1:
        out.append((hx1, hy0, x1 - hx1, hy1 - hy0))
    return out


def _whole_retained(cv):
    """Whether canvas `cv` keeps its pixels from frame to frame and draws
    into its whole buffer (a pane's viewport is not its buffer)."""
    if getattr(cv, "RETAINED_FRAMES", 0) < 1:
        return False
    vp = getattr(cv, "viewport", None)
    return vp is None or vp() == (0, 0, getattr(cv, "_stride", cv.w),
                                  getattr(cv, "_bh", cv.h))


# A console's holds share two layers of its canvas's size, one modal up at
# a time: `ws.held_pages` = [the layer the page is held in washed, the Hold
# whose page it holds, the layer a frame is kept in as it was (`Hold.snap`),
# the Hold that kept it]. Kept across modals (minting one is a third of a
# dialog's first frame on the T-Deck: the pause card mints both on an idle
# frame); the fit check's release frees them.

def held_layer(ws, cv, i=0):
    """Console `ws`'s hold layer `i` -- 0 the page held washed, 2 the frame
    kept as it was (`Hold.snap`) -- of canvas `cv`'s size: minted where
    there is none of it (an idle frame's work where a modal will want it,
    so the modal's first frame does not mint it); None with no room, or
    while the frame kept in another size is still up."""
    held = ws.held_pages
    lay = held[i]
    if lay is not None and lay.w == cv.w and lay.h == cv.h:
        return lay
    if lay is not None and i and held[3] is not None and held[3].snapped:
        return None
    _release(lay)
    held[i] = held[i + 1] = None
    try:
        lay = held[i] = cv.new_layer(cv.w, cv.h)
    except Exception:  # noqa: BLE001
        return None
    return lay


def release_held(ws):
    """Free one of console `ws`'s hold layers (the fit check's release):
    the washed page's, then the kept frame's while no modal is up over it.
    True when there was one; the next modal mints it again."""
    held = ws.held_pages
    for i in (0, 2):
        if held[i] is not None and not (i and held[3] is not None and held[3].snapped):
            _release(held[i])
            held[i] = held[i + 1] = None
            return True
    return False


def _release(lay):
    rel = getattr(lay, "release", None)
    if rel is not None:
        rel()


# A coverless cart's cover (`initial_cover`): (block, ink) pairs of MOY64
# indices, the colours a cart's own cover draws in, each ink at 3:1 or more
# on its block: yellow, red, blue, green, pink, orange, dark purple, dark
# green, indigo, dark blue.
INITIAL_BLOCKS = ((10, 1), (8, 7), (12, 1), (11, 1), (14, 1), (9, 1), (2, 7), (3, 7),
                  (13, 7), (1, 15))


def initial_block(title):
    """The (block, ink) pair of `title`'s generated cover: the same for the
    same title on every tier and every run (no `hash`, which is salted)."""
    n = 0
    for ch in str(title or ""):
        n = (n * 31 + ord(ch)) & 0xffff
    return INITIAL_BLOCKS[n % len(INITIAL_BLOCKS)]


# The generated cover is a picture of a cover's side (SPEC.md 3.6's 128),
# drawn into any card as a cover is (`cv.cover`): its letter's pixels are
# INITIAL_UNIT of that side each -- a sixteenth, so the letter is whole
# pixels at every clean card side (`launcher_layer._clean_side`) and no
# taller than half the block.
INITIAL_SIDE = 128
INITIAL_UNIT = 8
_initial_runs = {}       # letter -> its ink as rects on the INITIAL_SIDE picture


def _initial_rects(cv, letter):
    """`letter`'s ink as (x0, y0, x1, y1) rects on the INITIAL_SIDE picture,
    the columns' vertical runs (a run the column before ended the same way
    widened by one): centred on the letter's own ink, not on its cell. Kept
    per letter; () where the font has no ink for it."""
    got = _initial_runs.get(letter)
    if got is not None:
        return got
    cols = cv.glyph_cols(letter)
    lit = [c for c in range(8) if cols and cols[c]]
    out = []
    if lit:
        rows = 0
        for b in cols:
            rows |= b
        r0 = 0
        while not rows >> r0 & 1:
            r0 += 1
        r1 = 7
        while not rows >> r1 & 1:
            r1 -= 1
        u = INITIAL_UNIT
        ox = (INITIAL_SIDE - (lit[-1] - lit[0] + 1) * u) // 2 - lit[0] * u
        oy = (INITIAL_SIDE - (r1 - r0 + 1) * u) // 2 - r0 * u
        open_ = {}                        # (y0, y1) -> the rect ending at this column
        for c in lit:
            x0 = ox + c * u
            j = 0
            while j < 8:
                if not cols[c] >> j & 1:
                    j += 1
                    continue
                k = j
                while k < 8 and cols[c] >> k & 1:
                    k += 1
                y0, y1 = oy + j * u, oy + k * u
                prev = open_.get((y0, y1))
                if prev is not None and out[prev][2] == x0:
                    out[prev] = (out[prev][0], y0, x0 + u, y1)
                else:
                    open_[(y0, y1)] = len(out)
                    out.append((x0, y0, x0 + u, y1))
                j = k
    got = _initial_runs[letter] = tuple(out)
    return got


def _cover_edge(a, f):
    """The first of a card's columns (or rows) that a cover drawn into it
    samples at or past the picture's column `a`, at the 16.16 step `f`:
    `cv.cover` reads column (i * f + f / 2) >> 16 for card column i."""
    e = ((a << 16) - (f >> 1) + f - 1) // f
    return e if e > 0 else 0


_initial_of = {}          # title -> (block, ink, its letter or None)
_initial_quads = {}       # (letter, w, h) -> the letter's rects in a w x h card


def initial_cover(cv, rect, r, title, stepped=False):
    """A cart with no cover's picture over `rect` (corners `r`): its title's
    first letter on a colour block that stays the cart's (`initial_block`),
    a pixel letter (petme128) drawn as a cover of INITIAL_SIDE scaled to
    `rect` would be -- each letter pixel the card columns and rows
    `cv.cover` samples it at -- so it keeps its proportion to its card at
    every size a carousel's motion passes through, as a cover's art does.
    The letter's rects at a card's size are kept, and fill in one call."""
    x, y, w, h = rect
    block, ink, q = _initial_parts(cv, title, w, h)
    cv.rrect(x, y, w, h, r, block, stepped=stepped)
    if q:
        cv.fill_rects(q, -1, x, y, ink)


def _initial_parts(cv, title, w, h):
    """(block, ink, the letter's rects in a w x h card or None) of the
    title tile `initial_cover` draws, each kept."""
    got = _initial_of.get(title)
    if got is None:
        s = str(title or "").strip()
        got = initial_block(title) + ((s[0].upper() if s else None),)
        if len(_initial_of) >= 64:
            _initial_of.clear()
        _initial_of[title] = got
    block, ink, letter = got
    if letter is None or w <= 0 or h <= 0:
        return block, ink, None
    key = (letter, w, h)
    q = _initial_quads.get(key)
    if q is None:
        fx = (INITIAL_SIDE << 16) // w
        fy = (INITIAL_SIDE << 16) // h
        q = array("h")
        for x0, y0, x1, y1 in _initial_rects(cv, letter):
            a, b = _cover_edge(x0, fx), _cover_edge(y0, fy)
            cw, ch = _cover_edge(x1, fx) - a, _cover_edge(y1, fy) - b
            if cw > 0 and ch > 0:
                q.extend((a, b, cw, ch, ink))
        if len(_initial_quads) >= 64:
            _initial_quads.clear()
        _initial_quads[key] = q
    return block, ink, q


def initial_parts(cv, title, w, h):
    """(block, the letter's rects or None) of the title tile `initial_cover`
    draws over a w x h card: its colour block's rounded rect, and the rects
    (x, y, w, h, ink) its letter fills over it, kept."""
    block, _ink, q = _initial_parts(cv, title, w, h)
    return block, q or None


FADE_WELL = -3     # `fade`'s colour: a part's well (moy_ui_well's fill)


def fade(cv, rect, col, up=False, look=None):
    """A fade over `rect` toward colour `col` (or FADE_WELL), nothing at one
    edge to nearly the colour at the other -- the bottom, or the top with
    `up`: what says a list goes on past that edge."""
    _C.fade(cv, rect, col, up, look)


def fill_uncovered(cv, inner, outer, col):
    """Fill only the parts of `inner` that `outer` does not already cover.

    For the "clear the body, then clear the panel on top of it" idiom, where both
    fills use the SAME colour and the panel sits almost entirely inside the body.
    On the P4's 1024x600 editor those two rects are ~450K and ~426K pixels, so the
    second fill was rewriting ~94% of the first in the same colour -- ~848KB of
    redundant writes per frame, and `mg_fill_run` is a cached store loop, so
    PSRAM's write-allocate doubles a fill's traffic. Settings hit the identical bug
    and deleting its duplicate fill was worth ~9ms (see settings_layer.draw).

    The panel is NOT contained in the body -- under a top bar its top edge sits
    `2 * fs` px above (`panel = (8fs, cy - 2fs, ...)` vs `body_fill = (cx, cy,
    ...)`, the content rect) -- so the
    fill cannot simply be dropped; that would leave a stale strip under the bar.
    This paints the overhang and nothing else, which is pixel-identical to the two
    full fills whenever the colours match. Emits nothing at all when `inner` is
    fully covered (the Scene pane's layout), and degrades to one full fill when the
    two rects do not overlap, so it is safe on every tier.
    """
    _C.fill_uncovered(cv, inner, outer, col)


# --- scrolling ------------------------------------------------------------------

def drag_steps(d, step):
    """The whole `step`s in `d` pixels of drag travel, rounded toward zero
    so a finger moved a pixel either way scrolls nothing. A drag that
    carries its remainder advances its anchor by `drag_steps(d, step) * step`."""
    return d // step if d >= 0 else -(-d // step)


def pan_drag(drag, ed, px, py, cell, lh):
    """One frame of a text pan, content following the finger: `ed` (its
    `top`/`left` and `scroll(rows, cols)`) sits `drag_steps` of the finger's
    travel from the anchor, so a drag moves it by the lines the finger moved,
    either way, at any speed. `drag` is (x, y, cols, rows): the anchor and
    what has scrolled since it. An edge that stops the pan re-anchors that
    axis at the finger, so the way back answers at once. `cell` 0 pans rows
    only. Returns the new drag, the same object when nothing was asked."""
    ax, ay, nc, nr = drag
    dr = drag_steps(py - ay, lh) - nr
    dc = drag_steps(px - ax, cell) - nc if cell else 0
    if not (dr or dc):
        return drag
    top, left = ed.top, ed.left
    ed.scroll(-dr, -dc)
    if top - ed.top == dr:
        nr += dr
    else:
        ay, nr = py, 0
    if left - ed.left == dc:
        nc += dc
    else:
        ax, nc = px, 0
    return (ax, ay, nc, nr)


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
# ping-pong 2, the P4's triple framebuffer 3 -- shipped, efcf5d1). A paint
# ring keeps this many entries so it can vouch for RETAINED_FRAMES
# consecutive paints on ANY tier; raise it if a canvas ever rotates more
# buffers.
_MAX_RETAINED = 3


class PaintRing:
    """What the last painted frames left of one region in the buffers a
    canvas presents in turn (docs/scroll_and_text_2026-10.md §3.1): a slot
    per painted frame -- its number, the canvas it drew on, its key (what
    the pixels are a function of besides the region's own state, compared
    `==`, and an object compared `is`: a theme), the region's offset, a
    stamp (a rect the caller drew over it, which it repaints), what the
    frame changed (its owner's record; None: unknown) and whether the paint
    left the region whole with nothing over it. The slots are kept, never
    made again: noting a frame allocates nothing.

    `vouch` is the shift frame's licence on the host's side: frame n-1
    painted the region whole under this key into the buffer `shift_last`
    reads, so a shift brings n-1 to n. `held` names the paint the buffer a
    frame draws into holds, RETAINED_FRAMES back. Under the windowed WM the
    shown ring vouches for the buffer instead (one voucher per region) and
    the content's ring answers only `vouch_key`. Every painted region -- a
    full-screen app's page, a ScrollRegion, the home's band and the desk
    home's still frames -- keeps one of these."""

    def __init__(self, n=_MAX_RETAINED):
        self.n = n
        self._f = array("i", [-1] * n)
        self._off = array("i", [0] * n)
        self._key = [None] * n
        self._obj = [None] * n
        self._cv = [None] * n
        self._stamp = [None] * n
        self._change = [None] * n
        self._whole = bytearray(n)
        self._i = 0                     # the newest slot
        self.size = 0                   # the slots holding a frame

    def clear(self):
        """Forget every frame: the region repaints whole until it re-arms."""
        self.size = 0
        for i in range(self.n):
            self._cv[i] = self._obj[i] = self._key[i] = self._stamp[i] = self._change[i] = None

    def note(self, frame_no, key=None, offset=0, stamp=None, cv=None, obj=None, whole=True,
             change=None):
        """Frame `frame_no` painted the region at `offset`."""
        i = self._i + 1
        if i >= self.n:
            i = 0
        self._i = i
        self._f[i] = frame_no
        self._key[i] = key
        self._obj[i] = obj
        self._off[i] = offset
        self._stamp[i] = stamp
        self._cv[i] = cv
        self._change[i] = change
        self._whole[i] = 1 if whole else 0
        if self.size < self.n:
            self.size += 1

    def close(self, whole):
        """The newest frame's paint is done: whether it left the region
        whole, with nothing drawn over it."""
        if self.size and not whole:
            self._whole[self._i] = 0

    def _slot(self, back):
        i = self._i - back
        return i + self.n if i < 0 else i

    def frame(self, back=0):
        """The number of the frame `back` paints before the newest."""
        return self._f[self._slot(back)] if back < self.size else -1

    def offset(self, back=0):
        return self._off[self._slot(back)]

    def key(self, back=0):
        return self._key[self._slot(back)]

    def stamp(self, back=0):
        return self._stamp[self._slot(back)]

    def vouch_key(self, key=None, obj=None):
        """True when the newest frame painted the region whole under `key`
        (and `obj`), whatever buffer it drew into and whenever: where
        another ring vouches that the buffer `shift_last` reads holds that
        frame's region (the windowed WM's shown ring), else None."""
        if not self.size:
            return None
        i = self._i
        if not self._whole[i] or self._obj[i] is not obj or self._key[i] != key:
            return None
        return True

    def change(self, back=0):
        return self._change[self._slot(back)]

    def whole(self, back=0):
        return back < self.size and self._whole[self._slot(back)] == 1

    def held(self, cv, frame_no):
        """How many paints back the paint is whose pixels the buffer frame
        `frame_no` draws into holds: RETAINED_FRAMES - 1, where the frames
        between were this region's paints, one after another (a gap means
        another surface may have painted the buffers) and that paint left
        the region whole. None otherwise."""
        k = getattr(cv, "RETAINED_FRAMES", 0)
        if k < 1 or k > self.size:
            return None
        for b in range(k):
            if self._f[self._slot(b)] != frame_no - 1 - b:
                return None
        return k - 1 if self._whole[self._slot(k - 1)] else None

    def vouch(self, cv, frame_no, key=None, obj=None):
        """True when frame `frame_no` - 1 painted the region whole under
        `key` (and `obj`) on `cv`, whose `shift_last` reads that frame's
        buffer; else None."""
        if not self.size or getattr(cv, "RETAINED_FRAMES", 0) < 1:
            return None
        i = self._i
        if (self._f[i] != frame_no - 1 or self._cv[i] is not cv or not self._whole[i]
                or self._obj[i] is not obj or self._key[i] != key):
            return None
        return True


class ScrollRegion:
    """The one scroll model: a view rect over larger content, on either AXIS
    (vertical by default -- the Settings rows; `horizontal=True` for the
    Library shelf). The caller draws items at (item_pos - self.offset) along
    the scroll axis and clips to the view; this owns the offset bookkeeping,
    clamping, drag deltas, and the slim scrollbar (drawn + hit through the
    same geometry -- along the right edge vertically, the bottom edge
    horizontally)."""

    # Scaled by fs at draw. This is style METRICS (`_METRICS["scrollbar"]`),
    # the one metric that is also a public class attribute -- a caller reading
    # BAR_W and the pixels drawn must never disagree. The module's last line
    # sets it from C.
    BAR_W = 4
    # The motion -- a drag, the fling a release leaves, the glide a step or a
    # reveal aims -- is native/moy_ui's (moy_ui_scroll_t), its constants
    # moy_ui.h's; every dt is INJECTED (the loop's tick), never read from a
    # clock, so the physics is deterministic under test.

    def __init__(self, horizontal=False):
        self.horizontal = bool(horizontal)
        self.view = (0, 0, 0, 0)
        self.content = 0            # content extent along the scroll axis
        self.offset = 0
        # The drag, the fling and the glide (the last drag sample, the EMA
        # finger velocity in px/ms, the float offset while coasting, the
        # glide's target, their flags) live in this buffer; the rules are
        # native/moy_ui's (moy_ui_scroll_t).
        self._st = bytearray(28)
        # Scroll-as-blit paint ring (#113): what the canvas's buffers hold
        # of this view (`PaintRing`), so a scrolled view can shift its
        # already-correct pixels and repaint only the exposed band.
        self.ring = PaintRing()

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
        self.ring.clear()

    def note_painted(self, frame_no, key=None, stamp=None, alone=True):
        """Record a paint of this view at the current offset. `key` pins the
        non-scroll state the pixels depend on; `stamp` is an opaque damage rect
        (the cursor sprite baked into this paint) returned by a later
        blit_shift so the caller can repaint it. `alone` False: something
        was drawn over the view this frame, so the paint vouches for nothing.
        The ring keeps _MAX_RETAINED paints: blit_shift needs
        RETAINED_FRAMES consecutive ones, and this has no canvas to read
        the real N from."""
        self.ring.note(frame_no, key, self.offset, stamp, whole=alone)

    def blit_shift(self, cv, frame_no, key=None):
        """(delta, stamp) when the canvas's target framebuffer holds this
        view's pixels at a known offset, else None (caller paints full). delta
        is current offset minus the offset baked in the target buffer; stamp
        is that paint's recorded damage rect (or None)."""
        ring = self.ring
        if getattr(cv, "scroll_rect", None) is None:
            return None
        b = ring.held(cv, frame_no)
        if b is None or ring.key(b) != key:
            return None
        delta = self.offset - ring.offset(b)
        if abs(delta) >= self._extent():
            return None                # nothing survives the shift
        return (delta, ring.stamp(b))

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
        """Kill any motion, a fling or a glide (a catching touch / a
        programmatic scroll)."""
        _C.scroll_stop(self._st)

    @property
    def animating(self):
        """True while the view moves on its own -- a released fling coasts
        or a glide eases -- so the owner ticks it each frame and keeps the
        redraw gate open until it rests."""
        return bool(self._st[12] & 6)

    @property
    def flinging(self):
        """True while a released fling coasts."""
        return bool(self._st[12] & 2)

    @property
    def gliding(self):
        """True while a glide eases the view to its target."""
        return bool(self._st[12] & 4)

    @property
    def target(self):
        """The offset the glide is carrying the view to, None with none."""
        if not self._st[12] & 4:
            return None
        return _unpack("<i", self._st, 16)[0]

    def glide_to(self, p, slide=False):
        """Carry the view to offset `p` (clamped), eased in time from where
        it is; `slide`, over the fixed slide time instead (the handheld home's
        carousel). A target where the view is ends any glide."""
        _C.scroll_glide_to(self._st, self.offset, p, slide, self.content, self._extent())

    def fling(self, vel):
        """Add `vel` px/ms to the fling, begun from the offset where none
        coasts: a momentum that is not a drag's release (the trackball's
        coast). Stops a glide."""
        _C.scroll_fling(self._st, self.offset, vel)

    def step(self, n, size):
        """Move the glide's target `n` steps of `size` -- from the target
        while a glide is live, else from the offset -- and answer it."""
        return _C.scroll_step(self._st, self.offset, n, size, self.content, self._extent())

    def land(self):
        """End the motion where it was going (a glide at its target)."""
        off = _C.scroll_land(self._st, self.offset)
        if off >= 0:
            self.offset = off

    def tick(self, dt_ms):
        """Advance the fling or the glide by one frame (dt_ms injected): a
        fling integrates the offset and decays, a glide eases toward its
        target, each hard-stopping at the clamp edges (no overshoot).
        Returns True when a motion was live this frame."""
        off = _C.scroll_tick(self._st, dt_ms, self.offset, self.content, self._extent())
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
