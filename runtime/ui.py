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
(The one import below is widgets -- a peer leaf, so no cycle: the rect hit-test
has exactly one definition, widgets._in, re-exported here as `rect_in`.)
"""

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


# State tokens resolve through the SAME alias discipline chrome.THEMES uses for
# its semantic roles (chrome._SEMANTIC_ALIAS). ui.py cannot import chrome -- that
# would close the cycle ui -> chrome -> settings_layer -> ui -- so the chains a
# state cue needs live here, over roles chrome already flattens into every theme
# dict. A skin (or a theme) that names no state token still paints a correct cue,
# which is what lets all 12 family x variant sets keep working unmodified with
# zero per-state hand-tuning: the shelf's "janky" verdict was per-surface
# improvisation, and the fix is one derivation, here.
_STATE_ALIAS = {
    "hover": ("dim",),                    # the shelf's hover field token
    "hover_cue": ("focus", "accent"),     # the additive edge ring
    "pressed": ("selection", "hilite"),   # the press wash
    "pressed_ink": ("selection_ink", "ink"),
    "disabled_ink": ("ink_dim",),
    "disabled_edge": ("dim",),
}


def state_token(th, role, fallback=None):
    """One state token: `role`, else its alias chain, else `fallback`. Never a
    new colour literal -- every value comes out of the theme's own roles."""
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


# --- the skin seam: Phase 4 plugs in HERE, and nowhere else -------------------
#
# `state_colors` is the ONE place a widget kind's per-state look is decided, so
# it is the one place a skin has to intercept. Phase 4's `runtime/skin.py` -- a
# new LEAF, never chrome.py, which would close the cycle above -- calls
# `ui.set_skin(fn)` at import with a closure over its pre-flattened, NESTED
# SKIN[kind][state] tables of concrete ints (nested because a flattened
# `kind + ":" + state` key allocates a string per draw). `fn(th, kind, state)`
# returns an (field, ink, edge) triple, or None to fall through to the
# token-derived default below. ui.py never imports the skin; the skin installs
# itself, so this module stays a leaf.
_SKIN = None


def set_skin(fn):
    """Install the skin resolver (or None to restore the built-in default)."""
    global _SKIN
    _SKIN = fn


def state_colors(th, kind, state):
    """(field, ink, edge) for widget `kind` in `state` -- the default skin.

    The palette is the shipped list-row / grid-cell one, i.e. exactly what
    `files_app._draw_rows` and `file_widgets.FileGridView.draw` paint today: a
    quiet panel field with title ink and a dim edge, lifting to the title field
    with the accent edge when selected. `kind` is not branched on today (rows
    and cells share one palette on purpose) -- it is the skin's lookup key, and
    passing it now is what keeps Phase 4 a pure substitution.

    `field` may legitimately be None from a skin: a row that paints NO field
    (Settings, the system menu) is the common case, not an exception.
    """
    if _SKIN is not None:
        out = _SKIN(th, kind, state)
        if out is not None:
            return out
    ink = th.get("title_ink", _BLACK)
    if state == HOT:
        return (th.get("danger", 8), _WHITE, th.get("dim", 1))
    if state == ON:
        return (th.get("title", 13), ink, th.get("accent", 10))
    if state == PRESSED:
        # Quiet by default (SS7): pressed reuses the SELECTION wash rather than
        # inventing a loud look; `hot` stays the only shouting state.
        return (state_token(th, "pressed", th.get("hilite", 13)),
                state_token(th, "pressed_ink", _WHITE),
                th.get("accent", 10))
    if state == DISABLED:
        return (th.get("panel", 60),
                state_token(th, "disabled_ink", 6),
                state_token(th, "disabled_edge", 1))
    if state == HOVER:
        # ADDITIVE by construction: field and ink stay exactly at rest and only
        # the edge takes the cue, so hover changes paint -- never geometry, and
        # never legibility.
        return (th.get("panel", 60), ink,
                state_token(th, "hover_cue", th.get("accent", 10)))
    return (th.get("panel", 60), ink, th.get("dim", 1))


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

    def __init__(self):
        self._items = []
        self.hover = None       # (verb, arg) under a resting pointing cursor
        self.pressed = None     # (verb, arg) held down, pointer still inside
        self._armed = None      # what the press EDGE landed on
        self._down = False      # last sample's held state (for the edge)

    def clear(self):
        del self._items[:]

    def add(self, rect, verb, arg=None):
        if HITS_DEBUG:
            items = self._items
            for i in range(len(items)):
                it = items[i]
                if it[1] == verb and it[2] == arg:
                    raise ValueError(
                        "duplicate Hits id (%r, %r): state_of() would be "
                        "ambiguous" % (verb, arg))
        self._items.append((rect, verb, arg))

    def at(self, px, py):
        """(verb, arg) of the topmost hit at (px, py), or None. Later adds win
        (drawn later = on top), matching paint order."""
        for i in range(len(self._items) - 1, -1, -1):
            rect, verb, arg = self._items[i]
            if rect_in(px, py, rect):
                return (verb, arg)
        return None

    # -- the interaction pump --------------------------------------------------

    def pointer_frame(self, px, py, pointer):
        """ONE pump for hover AND pressed; True when either changed.

        Call it once per pointer sample from the surface's pointer handler
        (before or after the tap routing -- it reads, it never consumes), and
        mark the surface dirty on True. `pointer` is duck-typed: `.down` and
        `.visible`, plus the browser/mouse `.hovering` flag when a tier has one
        (today's `widgets.Pointer` does not, so a tier hovers exactly when its
        cursor is visible -- trackball and mouse yes, touch never).
        """
        down = bool(getattr(pointer, "down", False))
        pointing = bool(getattr(pointer, "visible", False)
                        or getattr(pointer, "hovering", False))
        hit = self.at(px, py)
        if down:
            if not self._down:
                self._armed = hit          # the press EDGE picks the target
            armed = self._armed
            new_pressed = armed if (armed is not None and hit == armed) else None
            new_hover = None               # a pointer that is down never hovers
        else:
            self._armed = None
            new_pressed = None
            new_hover = hit if pointing else None
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
           glyph=None, glyph_draw=None, disabled=False, state=None):
    """One themed chip button: filled field, thin dark edge, centered label
    (truncated to fit), optional 16x16 icon image or 12x12 glyph at the left.
    Glyphs require the caller's leaf-safe `glyph_draw(kind, rect, color, cv)`;
    `on` swaps to the accent (the pressed/active look).

    `disabled` + `state` are the six-state model (see `widget_state`): they
    default to the untouched REST/`on` rendering the goldens pin, and only a
    caller that opts in pays anything. `state` is what `Hits.state_of` returned
    for this button's id."""
    fs = _fs(cv)
    x, y, w, h = rect
    bg_tok, ink_tok = _BUTTON_KINDS.get(kind, _BUTTON_KINDS["normal"])
    bg = _resolve(th, bg_tok, _WHITE)
    ink = _resolve(th, ink_tok, _BLACK)
    edge = _BLACK
    st = widget_state(on, False, disabled, state)
    if st == DISABLED:
        ink = state_token(th, "disabled_ink", 6)
    elif st == PRESSED:
        bg = state_token(th, "pressed", th.get("hilite", 13))
        ink = state_token(th, "pressed_ink", _WHITE)
    elif st == ON:
        bg = th.get("accent", 10)
        ink = _BLACK
    elif st == HOVER:
        edge = state_token(th, "hover_cue", th.get("accent", 10))
    cv.rect(x, y, w, h, bg)
    cv.rectb(x, y, w, h, edge)
    fw = 8 * fs
    pad = 2 * fs
    iw = 0
    has_glyph = glyph is not None and glyph_draw is not None
    if icon_img is not None or has_glyph:
        iw = (16 if icon_img is not None else 12) * fs + pad
    label = str(label)
    maxc = max(0, (w - 2 * pad - iw) // fw)
    if len(label) > maxc:
        label = label[:maxc]
    tw = iw + len(label) * fw
    tx = x + max(pad, (w - tw) // 2)
    if icon_img is not None:
        cv.spr(icon_img, tx, y + (h - 16 * fs) // 2, fs)
    elif has_glyph:
        glyph_draw(glyph, (tx, y, 12 * fs, h), ink, cv)
    if label:
        cv.print(label, tx + iw, y + (h - 8 * fs) // 2, ink, 1)


def chip(cv, th, rect, label, on=False, hot=False, fs=None,
         glyph=None, glyph_draw=None, disabled=False, state=None):
    """The app-toolbar CHIP -- the one implementation of the `_button` the
    Appearance/Writer/Storybook/Artwork apps each used to carry a local copy
    of (pixel-identical to those). A quiet field on the panel color with the
    theme's title ink; `on` swaps to the accent toggle look (edge border);
    `hot` to danger red with light ink (an armed destructive action).

    `glyph` + `glyph_draw` (the game_icon_btn pattern -- the caller passes
    ws._glyph so this module never imports the vocabulary): draw the icon
    centered in the chip INSTEAD of the text label. The label still names the
    chip at the call site (and is the fallback when no glyph is given).

    `chip` vs `button`: chip is the PANEL-chrome vocabulary (toolbars inside
    the dark app chrome, theme-quiet); button is the Open Machine STUDIO
    vocabulary (dark-edged verb chips -- PLAY/CHANGE/SAVE).

    `disabled` + `state` are the six-state model (see `widget_state`), and both
    default to the exact pixels the goldens pin. `disabled` is what the three
    live private copies -- `writer_app._hist_btn`, `sheets_app._icon_btn`,
    `code_layer._panel_btn` -- each dim by hand today."""
    if fs is None:
        fs = _fs(cv)
    x, y, w, h = rect
    st = widget_state(on, hot, disabled, state)
    if st == HOT:
        bg = th.get("danger", 8)
        ink = _WHITE
        edge = th.get("dim", 1)
    elif st == ON:
        bg = th.get("accent", 10)
        ink = _BLACK
        edge = th.get("edge", 13)
    elif st == DISABLED:
        # What `writer_app._hist_btn` and `sheets_app._icon_btn` hand-roll: the
        # quiet chip shell with the ink (and the edge) carrying the affordance,
        # because there is no dimmed sprite -- only a dimmed colour.
        bg = th.get("panel", 60)
        ink = state_token(th, "disabled_ink", 6)
        edge = state_token(th, "disabled_edge", 1)
    elif st == PRESSED:
        bg = state_token(th, "pressed", th.get("hilite", 13))
        ink = state_token(th, "pressed_ink", _WHITE)
        edge = th.get("edge", 13)
    elif st == HOVER:
        bg = th.get("panel", 60)
        ink = th.get("title_ink", _BLACK)
        edge = state_token(th, "hover_cue", th.get("accent", 10))
    else:
        bg = th.get("panel", 60)
        ink = th.get("title_ink", _BLACK)
        edge = th.get("dim", 1)
    cv.rect(x, y, w, h, bg)
    cv.rectb(x, y, w, h, edge)
    if glyph is not None and glyph_draw is not None:
        glyph_draw(glyph, rect, ink, cv)
        return
    fw = 8 * fs
    label = str(label)
    # A label wider than the chip CLIPS (draw-what-fits, like label_row) --
    # overflow escaped the rect and landed ink-on-anything (#174).
    maxc = max(0, (w - 4) // fw)
    if len(label) > maxc:
        label = label[:maxc]
    cv.print(label, x + max(2, (w - len(label) * fw) // 2),
             y + max(1, (h - 8 * fs) // 2), ink, 1)


# --- list rows and grid cells: the two shapes the tree hand-rolls ~29 times ----
#
# Designed from the real call sites, not from first principles. `row` answers
# `settings_layer._draw_settings_row` (no field when unselected, a fixed label
# pad, a right-hand value + a trailing icon), `files_app._draw_rows` (panel/
# title field, dim/accent edge, a truncated name), `storybook_app._draw_rows`
# (an off-token field, a vertically centred label), `system_menu_ui` (hilite on
# the selected row only) and `achievements_ui._draw_achievements` (a leading
# glyph, a locked/dim look that IS `disabled`). `cell` answers
# `file_widgets.FileGridView.draw` (thumbnail + centred caption),
# `appearance_app._draw_wall_card` (art + a filled caption BAND) and
# `cards_layer._draw_choice_icons` / `_draw_bg_thumbs` (frame-only cells whose
# picture is bespoke) -- which is why `cell` RETURNS the art rect instead of
# trying to own every picture.


def row(cv, th, rect, label, on=False, hot=False, disabled=False, state=None,
        colors=None, icon_img=None, glyph=None, glyph_draw=None,
        value=None, value_ink=None, edge=True, pad=None, text_dy=None,
        hits=None, verb=None, arg=None, fs=None):
    """One list row: [field] [edge] [icon|glyph] [label] ... [value].

    Semantics are the caller's: `on` is the selection, `hot` the armed
    destructive look, `disabled` the unusable one (dim ink AND no hit
    registration -- an unusable row must not be tappable). Interaction comes
    from the registry: pass `hits` + `verb` (+ `arg`) and the row registers its
    own tap rect AND reads its own hover/pressed state back, so wiring a
    surface up is the pump call plus nothing per row. `state` overrides that
    read (what a grid-like surface with arithmetic hit-testing passes).

    Knobs, each one a real site's frozen geometry:
      `pad`      left/right inset (default `4 * fs`; Settings' frozen literal 4
                 and Storybook's `6 * fs` both pass their own).
      `text_dy`  label top offset inside the row; default is vertically centred
                 (Storybook), Settings passes 5 and Files `6 * fs`.
      `edge`     draw the resolved edge as a 1px border (Files/Storybook) or
                 not (Settings, the system menu popup).
      `colors`   an explicit (field, ink, edge) triple, bypassing the skin --
                 the escape hatch for sites whose pixels are frozen off-token
                 (Storybook's literal 7/0 rows, `cards_layer`'s own palette).
                 `field` None paints no field at all.
      `value`    a right-aligned secondary string (the Settings/WiFi value
                 column); the label truncates to whatever is left, and the
                 value itself truncates before it can escape the rect (#174).

    Draws only; returns nothing. The rect is the caller's -- rows are laid out
    by the per-surface Layout classes, as every widget here is.
    """
    if fs is None:
        fs = _fs(cv)
    x, y, w, h = rect
    if state is None and hits is not None and verb is not None:
        state = hits.state_of(verb, arg)
    st = widget_state(on, hot, disabled, state)
    if colors is None:
        colors = state_colors(th, "row", st)
    field, ink, edge_c = colors
    if field is not None:
        cv.rect(x, y, w, h, field)
    if edge and edge_c is not None:
        cv.rectb(x, y, w, h, edge_c)
    if pad is None:
        pad = 4 * fs
    fw = 8 * fs
    tx = x + pad
    right = x + w - pad
    if icon_img is not None:
        cv.spr(icon_img, tx, y + (h - 16 * fs) // 2, fs)
        tx += 16 * fs + pad
    elif glyph is not None and glyph_draw is not None:
        glyph_draw(glyph, (tx, y + (h - 14 * fs) // 2, 14 * fs, 14 * fs),
                   ink, cv)
        tx += 14 * fs + pad
    ty = y + ((h - 8 * fs) // 2 if text_dy is None else text_dy)
    if value is not None:
        value = str(value)
        maxv = (right - tx) // fw
        if maxv < 0:
            maxv = 0
        if len(value) > maxv:
            value = value[:maxv]
        if value:
            cv.print(value, right - len(value) * fw, ty,
                     ink if value_ink is None else value_ink, 1)
            right -= (len(value) + 1) * fw     # one blank column between them
    if label:
        label = str(label)
        maxc = (right - tx) // fw
        if maxc < 0:
            maxc = 0
        if len(label) > maxc:
            label = label[:maxc]
        if label:
            cv.print(label, tx, ty, ink, 1)
    # A DISABLED row registers nothing: "dim ink, non-registering" is the whole
    # point of the state -- the three sites that hand-roll disabled ink today
    # all still accept taps, which is the bug the state absorbs.
    if hits is not None and verb is not None and st != DISABLED:
        hits.add(rect, verb, arg)


def cell_art_rect(rect, fs=1, pad=None, caption_h=0):
    """PURE geometry: the picture sub-rect of a grid cell -- inside `pad` on
    three sides, above the caption zone at the bottom. Separate from `cell` so
    a grid can size its thumbnail CACHE once, outside the loop, exactly as
    `FileGridView.draw` derives `art_w`/`art_h` before iterating."""
    if pad is None:
        pad = 2 * fs
    x, y, w, h = rect
    return (x + pad, y + pad, max(0, w - 2 * pad),
            max(0, h - pad - caption_h))


def cell(cv, th, rect, label=None, on=False, hot=False, disabled=False,
         state=None, colors=None, band=False, band_fill=None, band_ink=None,
         pad=None, caption_h=None, icon_img=None, glyph=None, glyph_draw=None,
         fs=None):
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

      `pad`        inset of the art (default `2 * fs`; `FileGridView` 2, the
                   wallpaper cards `3 * fs`, a frame-only cell 0).
      `caption_h`  height of the caption zone at the bottom; default `14 * fs`
                   when there is a label, else 0. The wallpaper cards pass
                   `17 * fs`.
      `band`       fill the caption zone (wallpaper cards) instead of printing
                   the label straight onto the field (`FileGridView`); the
                   label is left-aligned on a band and centred without one.
      `colors`     an explicit (field, ink, edge) triple, as `row`.

    Draw order is field -> caption -> edge, so the caller's art (drawn after,
    into the returned rect) never sits under the border and the border never
    sits under the art -- which is what `appearance_app._draw_wall_card` and
    `cards_layer._draw_bg_thumbs` both already do by hand.
    """
    if fs is None:
        fs = _fs(cv)
    x, y, w, h = rect
    st = widget_state(on, hot, disabled, state)
    if colors is None:
        colors = state_colors(th, "cell", st)
    field, ink, edge_c = colors
    if pad is None:
        pad = 2 * fs
    if caption_h is None:
        caption_h = 14 * fs if label is not None else 0
    if field is not None:
        cv.rect(x, y, w, h, field)
    if label is not None and caption_h > 0:
        cap_y = y + h - caption_h
        cap_ink = ink
        if band:
            if band_fill is None:
                if st == ON:
                    band_fill = th.get("accent", 10)
                elif st == HOT:
                    band_fill = th.get("danger", 8)
                else:
                    band_fill = th.get("title", 13)
            if band_ink is None:
                band_ink = _BLACK if st == ON else th.get("title_ink", _BLACK)
            cv.rect(x, cap_y, w, caption_h, band_fill)
            cap_ink = band_ink
        label = str(label)
        maxc = max(0, (w - 2 * pad) // (8 * fs))
        if len(label) > maxc:
            label = label[:maxc]
        if label:
            lw = len(label) * 8 * fs
            lx = (x + pad + 2 * fs) if band else (x + (w - lw) // 2)
            cv.print(label, lx, cap_y + (caption_h - 8 * fs) // 2, cap_ink, 1)
    if edge_c is not None:
        cv.rectb(x, y, w, h, edge_c)
    art = cell_art_rect(rect, fs, pad, caption_h)
    ax, ay, aw, ah = art
    if icon_img is not None:
        cv.spr(icon_img, ax + (aw - 16 * fs) // 2, ay + (ah - 16 * fs) // 2, fs)
    elif glyph is not None and glyph_draw is not None:
        glyph_draw(glyph, (ax + (aw - 14 * fs) // 2, ay + (ah - 14 * fs) // 2,
                           14 * fs, 14 * fs), ink, cv)
    return art


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


# --- the classic game-canvas button family -----------------------------------
# The pre-toolkit Workstation draw trio (_btn/_icon_btn/_mini_btn), moved here
# verbatim (the 2026-07 kernel-shrink direction: console stays compositor/router);
# Workstation keeps thin delegates so every call site and test is untouched.
# These are the EDITOR-body vocabulary (paint/map/music/blocks action bars and
# the block prompts' pads): caller-colored fill, cream ring, black label.

def game_btn(cv, rect, label, fill):
    """A labeled action button. Preserves the frozen baseline quirk VERBATIM:
    at font scale 1 the label prints with the legacy scale-2 arg but centers
    with height 8 (byte-identical to the shipped pixels)."""
    x, y, w, h = rect
    fs = _fs(cv)
    cv.rect(x, y, w, h, fill)
    cv.rectb(x, y, w, h, _WHITE)
    if fs <= 1:
        cv.print(label, x + 6, y + (h - 8) // 2, _BLACK, 2)
    else:
        cv.print(label, x + 6 * fs, y + (h - 8 * fs) // 2, _BLACK, 2)


def game_icon_btn(cv, rect, kind, label, fill, glyph_draw=None):
    """A button that leads with an icon glyph (pre-literate) and keeps the word
    as a small secondary cue beside it."""
    x, y, w, h = rect
    fs = _fs(cv)
    cv.rect(x, y, w, h, fill)
    cv.rectb(x, y, w, h, _WHITE)
    if glyph_draw is not None:
        glyph_draw(kind, (x + 2 * fs, y, 16 * fs, h), _BLACK, cv)
    if label:
        cv.print(label, x + 19 * fs, y + (h - 8 * fs) // 2, _BLACK, 1)


def mini_btn(cv, rect, label, fill):
    """A tiny labeled chip (no ring) -- the Settings steppers' vocabulary."""
    x, y, w, h = rect
    cv.rect(x, y, w, h, fill)
    cv.print(label, x + 2, y + 2, _BLACK, 1)


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
    ix, iy, iw, ih = int(inner[0]), int(inner[1]), int(inner[2]), int(inner[3])
    ox, oy, ow, oh = int(outer[0]), int(outer[1]), int(outer[2]), int(outer[3])
    if iw <= 0 or ih <= 0:
        return
    ix1, iy1 = ix + iw, iy + ih
    ox1, oy1 = ox + ow, oy + oh
    if ox >= ix1 or ox1 <= ix or oy >= iy1 or oy1 <= iy:
        cv.rect(ix, iy, iw, ih, col)          # no overlap: all of it is uncovered
        return
    if iy < oy:                                # strip above
        cv.rect(ix, iy, iw, oy - iy, col)
    if iy1 > oy1:                              # strip below
        cv.rect(ix, oy1, iw, iy1 - oy1, col)
    my0 = iy if iy > oy else oy                # the vertically-overlapping band
    my1 = iy1 if iy1 < oy1 else oy1
    if my1 > my0:
        if ix < ox:                            # strip left
            cv.rect(ix, my0, ox - ix, my1 - my0, col)
        if ix1 > ox1:                          # strip right
            cv.rect(ox1, my0, ix1 - ox1, my1 - my0, col)


# --- scrolling ------------------------------------------------------------------

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

    BAR_W = 4           # scaled by fs at draw
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
        self._drag = None           # last drag sample's axis coordinate
        self._vel = 0.0             # kinetic: EMA finger velocity, px/ms
        self._fling = False         # a released fling is coasting
        self._foff = 0.0            # float offset shadow while coasting
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

    def drag_start(self, p):
        self._drag = p
        self.stop()                 # a new touch CATCHES a live fling

    def drag_move(self, p, dt_ms=None):
        if self._drag is None:
            return False
        d = self._drag - p
        self.scroll_by(d)
        self._drag = p
        # Release-velocity EMA, fed the loop's dt by the caller. The pointer
        # routes every frame (the redraw gate only skips DRAWS), so a held-
        # still finger decays the velocity to ~0 -- hold-then-release is a
        # stop, not a fling. No dt (a legacy caller): velocity stays 0.
        if dt_ms:
            self._vel += (d / dt_ms - self._vel) * self.VEL_EMA
        return True

    def drag_end(self):
        self._drag = None
        if abs(self._vel) >= self.MIN_FLING and self._max_offset() > 0:
            self._fling = True
            self._foff = float(self.offset)

    def stop(self):
        """Kill any kinetic motion (a catching touch / a programmatic scroll)."""
        self._fling = False
        self._vel = 0.0

    @property
    def animating(self):
        """True while a released fling is coasting -- the owner ticks it each
        frame and keeps the redraw gate open until it rests."""
        return self._fling

    def tick(self, dt_ms):
        """Advance a coasting fling by one frame (dt_ms injected): integrate
        the offset, decay the velocity, hard-stop at the clamp edges (#113
        v1: no overshoot). Returns True when this frame moved the view."""
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

    # -- scrollbar -----------------------------------------------------------

    def bar_rect(self, fs=1):
        """The scrollbar thumb rect, or None when everything fits."""
        x, y, w, h = self.view
        m = self._max_offset()
        vis = self._extent()
        if m <= 0 or vis <= 0:
            return None
        bw = self.BAR_W * fs
        th_ = max(8 * fs, vis * vis // self.content)
        tp = (vis - th_) * self.offset // m
        if self.horizontal:
            return (x + tp, y + h - bw, th_, bw)
        return (x + w - bw, y + tp, bw, th_)

    def draw_bar(self, cv, th):
        fs = _fs(cv)
        r = self.bar_rect(fs)
        if r is None:
            return
        x, y, w, h = self.view
        bw = self.BAR_W * fs
        if self.horizontal:
            cv.rect(x, y + h - bw, w, bw, th.get("dim", 1))
        else:
            cv.rect(x + w - bw, y, bw, h, th.get("dim", 1))
        cv.rect(r[0], r[1], r[2], r[3], th.get("ink_dim", 6))


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
        self._press = None          # (x, y) at the press edge, while pending
        self._dragging = False
        self._caught = False        # this press stopped a live fling (#113):
                                    # its release is a CATCH, never a tap

    @property
    def dragging(self):
        return self._dragging

    def frame(self, px, py, click, down, slop=6, dt_ms=None):
        """One pointer sample: `click` is the press edge, `down` the held
        state, `dt_ms` the loop's tick (feeds the kinetic release velocity).
        Returns the press-edge (x, y) on a clean tap release, else None.
        While a drag is live the region's offset follows the finger -- the
        caller syncs its scroll state from region.offset."""
        region = self.region
        axis = px if region.horizontal else py
        if click:
            if rect_in(px, py, region.view):
                self._press = (px, py)
                self._dragging = False
                self._caught = region.animating   # read BEFORE drag_start stops it
                region.drag_start(axis)
            return None
        if down:
            if self._press is not None and not self._dragging:
                sx, sy = self._press
                if abs(px - sx) > slop or abs(py - sy) > slop:
                    self._dragging = True
            if self._dragging:
                region.drag_move(axis, dt_ms)  # the region owns the offset mid-drag
            return None
        region.drag_end()
        press, self._press = self._press, None
        was_drag, self._dragging = self._dragging, False
        caught, self._caught = self._caught, False
        if press is None or was_drag or caught:
            return None
        return press
