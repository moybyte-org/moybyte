"""The skin CATALOG -- widget style as data (UI refactor 2026-08, Phase 4).

What a skin is
--------------
`runtime/ui.py` holds the widget vocabulary AND its default look, as two
pre-flattened tables: `ui.DEFAULT_SPECS` (kind x state -> a (field, ink, edge)
triple of token specs) and `ui.DEFAULT_METRICS` (kind -> a tuple of pads, edge
weights, strip heights, icon boxes and label alignment). A **skin** is a DELTA
over those two tables, installed with `ui.set_skin(specs=..., metrics=...)`.
Nothing here draws; nothing here knows a surface exists.

That is the whole mechanism, and it is falsifiable: a skin that needs an edit
to any surface module is not a skin, it is a fork. The two shipped here are
pure data -- see `tests/test_skin.py`, which renders the entire shell under
each and proves the default one is byte-identical to installing no skin at all.

Why this module is a leaf, and why it is NOT in chrome.py
---------------------------------------------------------
`chrome.py` imports `settings_layer` and `code_layer`, both of which import
`ui`. A skin table living beside `THEMES` would therefore close the cycle
`ui -> chrome -> settings_layer -> ui`. So the catalog lives here, importing
exactly one module -- `ui`, a peer leaf whose only import is `widgets`. `ui`
never imports this, which is what lets the console boot and look right with
this module absent: the default skin is simply `ui`'s own tables.

Colours vs SIZES -- the scope of a restyle
------------------------------------------
A skin restyles within the indexed canvas's primitive vocabulary: fills, edges,
glyph text, sprite icons. It may change every colour, every edge weight and
every label alignment freely -- those are paint. It may ALSO change the
sizing metrics (`TAB_GAP`, `CELL_PAD`, `PANEL_STRIP`, `SB_W`, ...), but doing
so reflows the layouts around the widget and re-baselines the goldens: that is
a deliberate versioned act, not a data tweak (`docs/ui_widgets_2026-08.md`
Section 3.3). **`"outline"` below deliberately changes NO sizing metric**, so
"restyle without resize" is demonstrated rather than asserted.

Using one
---------
    from runtime import skin
    skin.use("outline")          # or skin.use(skin.DEFAULT) to go back
    skin.names()                 # ("default", "outline") -- for a picker

The Appearance app's row calls `skin.use(name)` and persists the name beside
`theme`/`theme_variant`; see the note at the bottom of this file.
"""

try:
    import ui as _ui
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime import ui as _ui


DEFAULT = "default"


# --- "outline" -- the falsifiable proof, as pure data -------------------------
#
# One coherent restyle, chosen so that every change is PAINT and none is
# geometry: heavier two-pixel frames drawn INWARD (so a widget occupies the
# same rect it always did), flat fields that let the border carry the shape,
# and left-aligned labels. The state palette re-reads the same semantic roles
# the themes already define -- `hilite`/`selection`/`accent`/`focus` -- so all
# 12 theme family x variant sets keep working unmodified, which is the point of
# resolving through roles instead of literals.
#
# Read it as the delta it is: every key here is a kind whose entry in
# `ui.DEFAULT_SPECS` / `ui.DEFAULT_METRICS` it replaces wholesale; kinds not
# named keep the default.

_HILITE = ("hilite", "selection", 13)
_TITLE_INK = ("title_ink", 0)
_INK_DIM = ("ink_dim", 6)
_ACCENT = ("accent", 10)
_FOCUS = ("focus", "accent", 10)
_DIM = ("dim", 1)
_PANEL = ("panel", 60)

# Rows and cells lose their field entirely at rest -- the frame IS the widget.
# Selection fills with the theme's hilite rather than its title tone, and the
# pressed wash reuses the accent, which is the loudest a quiet state may get
# (Section 7: hot stays the only shouting state).
_OUTLINE_ROW = {
    _ui.REST:     (None, _TITLE_INK, _DIM),
    _ui.HOVER:    (None, _TITLE_INK, _FOCUS),
    _ui.ON:       (_HILITE, _TITLE_INK, _ACCENT),
    _ui.HOT:      (("danger", 8), 7, ("danger", 8)),
    _ui.PRESSED:  (_ACCENT, 0, _ACCENT),
    _ui.DISABLED: (None, _INK_DIM, _INK_DIM),
}

_OUTLINE_CHIP = {
    _ui.REST:     (None, _TITLE_INK, _DIM),
    _ui.HOVER:    (None, _TITLE_INK, _FOCUS),
    _ui.ON:       (_HILITE, _TITLE_INK, _ACCENT),
    _ui.HOT:      (("danger", 8), 7, ("danger", 8)),
    _ui.PRESSED:  (_ACCENT, 0, _ACCENT),
    _ui.DISABLED: (None, _INK_DIM, _INK_DIM),
}


def _outline_button(bg, ink):
    """A `button` variant under "outline": the verb keeps its identity colour
    (a PLAY chip must still read as green) but wears the theme's own border
    instead of the hard black one, and its states move the FRAME."""
    return {
        _ui.REST:     (bg, ink, _DIM),
        _ui.HOVER:    (bg, ink, _FOCUS),
        _ui.ON:       (_ACCENT, 0, _ACCENT),
        _ui.HOT:      (bg, ink, _DIM),
        _ui.PRESSED:  (_ACCENT, 0, _ACCENT),
        _ui.DISABLED: (_PANEL, _INK_DIM, _INK_DIM),
    }


_OUTLINE_SPECS = {
    "row":           _OUTLINE_ROW,
    "cell":          _OUTLINE_ROW,
    "cell_band":     {
        _ui.REST:     (_HILITE, _TITLE_INK, None),
        _ui.HOVER:    (_HILITE, _TITLE_INK, None),
        _ui.ON:       (_ACCENT, 0, None),
        _ui.HOT:      (("danger", 8), 7, None),
        _ui.PRESSED:  (_ACCENT, 0, None),
        _ui.DISABLED: (_HILITE, _INK_DIM, None),
    },
    "chip":          _OUTLINE_CHIP,
    "button":        _outline_button(("surface", 7), ("ink", 0)),
    "button_play":   _outline_button(("play", 7), 7),
    "button_author": _outline_button(("author", 7), 0),
    "button_danger": _outline_button(("danger", 7), 7),
    # The active tab is a filled block with the accent under it; inactive tabs
    # keep the chrome ink they had.
    "tab":           {_ui.ON: (_HILITE, _TITLE_INK, None),
                      _ui.REST: (None, 7, None)},
    "status":        {_ui.REST: (_PANEL, _INK_DIM, _ACCENT)},
    "panel":         {_ui.REST: (("surface", "panel", 60), None, _ACCENT)},
    "panel_title":   {_ui.REST: (_HILITE, _TITLE_INK, None)},
    "toolbar":       {_ui.REST: (_PANEL, None, None)},
    "scrollbar":     {_ui.REST: (None, _ACCENT, None)},
    "focus":         {_ui.REST: (None, None, _FOCUS)},
}

# Metrics: PAINT ONLY. Every slot touched below is an edge weight, an
# alignment or a colour literal; not one of them changes a widget's size, its
# pads or a strip height, so no layout reflows and every stored golden that is
# not a colour stays where it was. (The kinds not named -- "cell", "tab",
# "panel", "focus", "scrollbar" -- keep the default tuple exactly.)
_OUTLINE_METRICS = {
    #                pad icon glyph EDGE  ALIGN
    "button":        (2,  16,  12,   2,   _ui.ALIGN_LEFT),
    #                clip minx miny EDGE  ALIGN
    "chip":          (4,   2,   1,   2,   _ui.ALIGN_LEFT),
    #                pad icon glyph gap  EDGE
    "row":           (4,  16,  14,   1,   2),
    #                the game-canvas family: same pads, brighter frames
    "game_btn":      (6, 10, 0),          # yellow ring instead of cream
    "game_icon_btn": (2, 16, 19, 10, 0),
    "mini_btn":      (2, 2, 0),
    "dialog":        (2, 2, 10),          # a two-pixel yellow modal frame
    "text_field":    (4, 3, 6, 8, 0, 10, 7, 5, 10),   # accent ring
}


SKINS = {
    DEFAULT: (None, None),                # ui's own tables, by reference
    "outline": (_OUTLINE_SPECS, _OUTLINE_METRICS),
}

# Presentation order for a picker (dicts are unordered on MicroPython).
ORDER = (DEFAULT, "outline")

_active = DEFAULT


def names():
    """The catalog, in presentation order -- what a picker lists."""
    return ORDER


def active():
    """The installed skin's name."""
    return _active


def use(name):
    """Install skin `name` (unknown names fall back to the default, as
    `chrome.theme_colors` does for an unknown theme). Returns the name that
    was actually installed, so a caller can persist the resolved value."""
    global _active
    entry = SKINS.get(name)
    if entry is None:
        name = DEFAULT
        entry = SKINS[DEFAULT]
    specs, metrics = entry
    _ui.set_skin(None, specs, metrics)
    _active = name
    return name


# --- where the Appearance row plugs in ---------------------------------------
#
# The picker UI is deliberately NOT here and NOT in this phase's file set
# (`runtime/appearance_app.py` is owned elsewhere while this lands). When it is
# added, it is one row beside THEMES / DARK-LIGHT:
#
#     from runtime import skin                    # (device: `import skin`)
#     ...
#     for name in skin.names():                   # a chip row, like THEMES
#         _ui.chip(cv, th, r, name.upper(), on=(name == skin.active()))
#     ...
#     def _pick_skin(self, name):
#         self.ws.set_skin(skin.use(name))        # persist beside theme_variant
#
# `Workstation` needs the same two lines `set_theme_variant` has: store the
# name in its config and call `skin.use(stored)` at boot, before the first
# draw. Nothing else in the shell changes -- that is the claim this phase
# makes, and `tests/test_skin.py` is where it is checked.
