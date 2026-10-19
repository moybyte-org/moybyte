"""runtime/ui.py Phase 3a: the six-state interaction model, the `Hits` pointer
pump, and the two new widget kinds (`row`, `cell`).

Three jobs, in the order they matter:

1. **Nothing existing moved.** `test_shipped_widgets_are_byte_identical` renders
   every SHIPPED widget across all 12 theme family x variant sets at four
   size/font-scale combinations and hashes the canvas. The digests below were
   taken from the tree BEFORE this phase's edit and are pinned here, so a later
   phase that "just tidies" a draw function fails on the spot instead of
   quietly re-baselining. Re-taking them is a DELIBERATE act: change the
   constants in the same commit that changes the pixels, and say which pixel
   moved.
2. **The state model resolves right** -- the full precedence lattice, not a
   sample of it.
3. **The pump obeys its rules**, each of which is a #177 hover-shelf bug made
   structural, and `Hits` stays BOUNDED (a grid registers zero rects).
"""

import hashlib

from runtime import ui
from runtime import ui_draw
from runtime.chrome import THEME_VARIANTS
from ws_helpers import fixture_colors, install_theme

# The palette themes whose widgets these pin byte for byte: the seeds of
# MOY64 colours docs/look_2026-10.md step 27 deleted, as fixture packages
# (tests/fixtures/themes/), in their old seed order; `moy`'s, `blocks`' and
# `contrast`'s RGB roles are tests/test_theme_rgb.py's and its goldens'.
THEME_NAMES = ("night", "indigo", "berry", "forest", "slate", "machine",
               "platinum", "win95", "outline")
from runtime.host_canvas import make_system_canvas as SystemCanvas
from runtime.host_canvas import indices_of


TH = fixture_colors("machine")


# --- test doubles --------------------------------------------------------------

class _Ptr:
    """The three fields the pump duck-types off moy_input.Pointer. `hovering` is
    the browser/mouse flag today's Pointer does NOT have -- it is set only by
    the test that proves the pump reads it when a tier grows one."""

    def __init__(self, visible=True, down=False, hovering=None):
        self.visible = visible
        self.down = down
        if hovering is not None:
            self.hovering = hovering


TOUCH = _Ptr(visible=False)              # touch places the pointer HIDDEN
CURSOR = _Ptr(visible=True)              # trackball / mouse


def _glyph_draw(kind, rect, color, cv):
    """A leaf-safe stand-in for ws._glyph: deterministic, rect + colour only."""
    x, y, w, h = rect
    cv.rect(x + 1, y + 1, max(1, w - 2), max(1, h - 2), color)
    cv.rectb(x, y, w, h, color)


class _Img:
    """A duck-typed 16x16 indexed tile the system canvas accepts."""

    def __init__(self, seed=3):
        self.w = 16
        self.h = 16
        self.pix = bytearray(((i * 7 + seed) & 63) for i in range(256))
        self.transparent = -1
        self._paint = True


IMG = _Img()


class _CountingHits(ui_draw.Hits):
    """Counts registrations. The budget test below asserts a FLOOR before a
    cap, so deleting the counter (or the registration) fails rather than
    passes -- the shape tests/test_top_bar.py established."""

    def __init__(self):
        ui_draw.Hits.__init__(self)
        self.adds = 0

    def add(self, rect, verb, arg=None):
        self.adds += 1
        ui_draw.Hits.add(self, rect, verb, arg)


# =============================================================================
# 1. nothing existing moved
# =============================================================================

# theme/variant WxH@font_scale sha256[:16] of the index readout. Taken on the
# pre-Phase-3a tree (a raw RGB565 A/B of the two module objects in one process
# agreed on all 48, 0 differing bytes); re-pinned by the 2026-10 look's step 10,
# which moved the primary, tonal and danger buttons to their kinds' colours,
# step 11, which gave Contrast a `value` of its own (white), the buttons'
# icon gap (style.json's `icon_gap`, 2026-10-18), which moved ICON and GLYPH,
# and a labelled tab chip's pad after its label (2026-10-19).
SHIPPED_WIDGET_HASHES = (
    "night/dark 320x240@1 7538a196513f2768",
    "night/dark 480x320@1 aff1c8a382a8e7f9",
    "night/dark 1024x600@2 e916daad1ae81130",
    "night/dark 320x240@3 b6fa26909d8a4a5c",
    "night/light 320x240@1 5eb77effc3da0e4b",
    "night/light 480x320@1 84ff9743c11f0411",
    "night/light 1024x600@2 0ef400ab05b7cb1a",
    "night/light 320x240@3 650ff3def0c3b6f4",
    "indigo/dark 320x240@1 f33b4212756e4d80",
    "indigo/dark 480x320@1 ae47558c3035e041",
    "indigo/dark 1024x600@2 ed1890f4d13edc72",
    "indigo/dark 320x240@3 b3e506fba1d0487a",
    "indigo/light 320x240@1 f80e5f23c3f5ecdc",
    "indigo/light 480x320@1 fb620066763c259a",
    "indigo/light 1024x600@2 3ca2f48aef4a15f8",
    "indigo/light 320x240@3 3194491a3795820d",
    "berry/dark 320x240@1 78dac4b68c37b38c",
    "berry/dark 480x320@1 370ddb399a45670e",
    "berry/dark 1024x600@2 84b85e6249ad019f",
    "berry/dark 320x240@3 f18afebf5a336075",
    "berry/light 320x240@1 58b13c3bcef53f5e",
    "berry/light 480x320@1 a846ef9c1d6e2cb5",
    "berry/light 1024x600@2 71104d073b9e534e",
    "berry/light 320x240@3 0aab96dc9084127e",
    "forest/dark 320x240@1 f7ccbf84fc8eb339",
    "forest/dark 480x320@1 8ce60d65b9981fae",
    "forest/dark 1024x600@2 08417cf5c2cda60c",
    "forest/dark 320x240@3 f0db92f550bbbcb8",
    "forest/light 320x240@1 2883d35f61b3e856",
    "forest/light 480x320@1 0e79541089bb61c3",
    "forest/light 1024x600@2 561d8c40dd39a08d",
    "forest/light 320x240@3 bab247d7fabe12be",
    "slate/dark 320x240@1 76e984cff7c81e78",
    "slate/dark 480x320@1 70ec27da183a5fbd",
    "slate/dark 1024x600@2 e804879278f50de2",
    "slate/dark 320x240@3 62e239bf7b260a5c",
    "slate/light 320x240@1 79bfea67ea265f8d",
    "slate/light 480x320@1 d4b901d219535ee5",
    "slate/light 1024x600@2 f870f553fcc8394c",
    "slate/light 320x240@3 4ae81674ec6565cb",
    "machine/dark 320x240@1 8318c5db4093e055",
    "machine/dark 480x320@1 a4548de9d3237bc6",
    "machine/dark 1024x600@2 9de5e913aa8bd3b8",
    "machine/dark 320x240@3 609a88fcb3e8e45b",
    "machine/light 320x240@1 afc62f7ec4b5801a",
    "machine/light 480x320@1 26c816c67bebb54d",
    "machine/light 1024x600@2 3b3f4721eb5d8e3e",
    "machine/light 320x240@3 1d227b8bb236ad2a",
    "platinum/dark 320x240@1 2a3fadcbe50a885e",
    "platinum/dark 480x320@1 9c0436fd62eda503",
    "platinum/dark 1024x600@2 2bc0285d57012514",
    "platinum/dark 320x240@3 8e09b53611b0b655",
    "platinum/light 320x240@1 80c1e1834b0bb2ae",
    "platinum/light 480x320@1 1a7720522ac010de",
    "platinum/light 1024x600@2 2b2fbd06d2262b3c",
    "platinum/light 320x240@3 53c3e4ef14ef1dfc",
    "win95/dark 320x240@1 d3776d16603f8f4c",
    "win95/dark 480x320@1 12a05ca2f5a066b8",
    "win95/dark 1024x600@2 0bea28712f3f6cd2",
    "win95/dark 320x240@3 04f324ced6fb6d08",
    "win95/light 320x240@1 2bf904646525a725",
    "win95/light 480x320@1 a2c6006688773d0c",
    "win95/light 1024x600@2 91e2cf8dc911ff5c",
    "win95/light 320x240@3 fc4a299c42200768",
    "outline/dark 320x240@1 7538a196513f2768",
    "outline/dark 480x320@1 aff1c8a382a8e7f9",
    "outline/dark 1024x600@2 e916daad1ae81130",
    "outline/dark 320x240@3 b6fa26909d8a4a5c",
    "outline/light 320x240@1 5eb77effc3da0e4b",
    "outline/light 480x320@1 84ff9743c11f0411",
    "outline/light 1024x600@2 0ef400ab05b7cb1a",
    "outline/light 320x240@3 650ff3def0c3b6f4",
)

_SIZES = ((320, 240, 1), (480, 320, 1), (1024, 600, 2), (320, 240, 3))


def _paint_shipped(cv, th):
    """Every SHIPPED widget, with arguments chosen to walk each branch. Nothing
    added by Phase 3a appears here -- that is the point."""
    ui_draw.scroll_cues(cv, (4, 4), (4, 200), True, True, th["accent"], 2)
    ui_draw.button(cv, th, (8, 14, 90, 22), "PLAY", kind="primary")
    ui_draw.button(cv, th, (8, 40, 90, 22), "CHANGE", kind="normal")
    ui_draw.button(cv, th, (8, 66, 90, 22), "TONAL", kind="tonal")
    ui_draw.button(cv, th, (8, 92, 90, 22), "DELETE FOREVER", kind="danger")
    ui_draw.button(cv, th, (8, 118, 90, 22), "ON", on=True)
    ui_draw.button(cv, th, (8, 144, 90, 22), "ICON", icon_img=IMG)
    ui_draw.button(cv, th, (8, 170, 90, 22), "GLYPH", glyph="run",
              glyph_draw=_glyph_draw)
    ui_draw.chip(cv, th, (104, 14, 60, 18), "QUIET")
    ui_draw.chip(cv, th, (104, 34, 60, 18), "ON", on=True)
    ui_draw.chip(cv, th, (104, 54, 60, 18), "HOT", hot=True)
    ui_draw.chip(cv, th, (104, 74, 60, 18), "OVERFLOWING LABEL")
    ui_draw.chip(cv, th, (104, 94, 60, 18), "G", glyph="edit",
            glyph_draw=_glyph_draw)
    ui_draw.chip(cv, th, (104, 114, 60, 18), "FS3", fs=3)
    hits = ui_draw.Hits()
    ui_draw.tab_row(cv, th, (104, 136, 200, 20),
               (("cfg", "Config", "gear"), ("code", "Code", "edit"),
                ("map", "Map", "map")), "code",
               icon_for=lambda k: IMG, hits=hits)
    ui_draw.tab_row(cv, th, (104, 158, 60, 20),
               (("cfg", "Config", "gear"), ("code", "Code", "edit"),
                ("map", "Map", "map")), "cfg", icon_for=None, ink=5)
    ui_draw.status_row(cv, th, (104, 180, 200, 14), ("Ln 13, Col 1", "No issues"))
    ui_draw.game_btn(cv, (170, 14, 70, 20), "GO", 11)
    ui_draw.game_icon_btn(cv, (170, 36, 70, 20), "run", "GO", 11,
                     glyph_draw=_glyph_draw)
    ui_draw.mini_btn(cv, (170, 58, 30, 12), "-", 9)
    ui_draw.toolbar(cv, th, (170, 72, 140, 16))
    ui_draw.dialog(cv, (170, 90, 130, 40))
    ui_draw.text_field(cv, (174, 94, 120, 14), "hi", "")
    ui_draw.text_field(cv, (174, 112, 120, 14), "", "type here")
    content = ui_draw.panel(cv, th, (250, 14, 60, 60), title="TOOLS")
    ui_draw.focus_ring(cv, th, ui.inset(content, 4, 4))
    ui_draw.fill_uncovered(cv, (0, 200, 320, 40), (10, 205, 100, 20), th["dim"])
    ui_draw.fill_uncovered(cv, (240, 200, 60, 20), (0, 0, 4, 4), th["edge"])
    sr = ui_draw.ScrollRegion()
    sr.set((250, 80, 60, 100), 400)
    sr.scroll_by(120)
    sr.draw_bar(cv, th)
    sh = ui_draw.ScrollRegion(horizontal=True)
    sh.set((4, 210, 200, 24), 900)
    sh.scroll_by(300)
    sh.draw_bar(cv, th)


def test_shipped_widgets_are_byte_identical():
    """The Phase 3a contract: purely additive. Every shipped widget's REST
    (and `on`/`hot`) rendering is exactly what it was, on all 12 theme sets and
    at 320x240/1x, 480x320/1x (Guition), 1024x600/2x (P4 windowed) and
    320x240/3x -- the axes the 320x240-dark-only checks structurally cannot
    see."""
    seen = []
    for name in THEME_NAMES:
        for variant in THEME_VARIANTS:
            for (w, h, fs) in _SIZES:
                th = fixture_colors(name, variant)
                cv = SystemCanvas(w, h, font_scale=fs)
                _paint_shipped(cv, th)
                seen.append("%s/%s %dx%d@%d %s" % (
                    name, variant, w, h, fs,
                    hashlib.sha256(indices_of(cv)).hexdigest()[:16]))
    assert len(seen) == len(SHIPPED_WIDGET_HASHES) > 40
    for got, want in zip(seen, SHIPPED_WIDGET_HASHES):
        assert got == want, (
            "a shipped widget's pixels moved: %s (pinned %s)" % (got, want))


def test_the_new_arguments_default_to_the_frozen_rendering():
    """`disabled`/`state` were added to chip and button. Passing them at their
    defaults, or not at all, must be the same call -- checked per branch rather
    than only through the aggregate hash above."""
    for th in (fixture_colors("night"), fixture_colors("machine", "light")):
        for kwargs in ({}, {"on": True}, {"hot": True}):
            a = SystemCanvas(80, 30)
            b = SystemCanvas(80, 30)
            ui_draw.chip(a, th, (2, 2, 70, 20), "HI", **kwargs)
            ui_draw.chip(b, th, (2, 2, 70, 20), "HI",
                    disabled=False, state=None, **kwargs)
            assert bytes(a._buf) == bytes(b._buf)
        for kind in ("normal", "play", "author", "danger"):
            a = SystemCanvas(80, 30)
            b = SystemCanvas(80, 30)
            ui_draw.button(a, th, (2, 2, 70, 20), "HI", kind=kind)
            ui_draw.button(b, th, (2, 2, 70, 20), "HI", kind=kind,
                      disabled=False, state=None)
            assert bytes(a._buf) == bytes(b._buf)


# =============================================================================
# 2. the state model
# =============================================================================

def test_precedence_over_every_combination():
    """disabled > pressed > hot > on > hover > rest, exhaustively: 2^3 semantic
    flags x the three things the registry can report."""
    for disabled in (False, True):
        for hot in (False, True):
            for on in (False, True):
                for interact in (None, ui_draw.HOVER, ui_draw.PRESSED):
                    got = ui_draw.widget_state(on=on, hot=hot, disabled=disabled,
                                          interact=interact)
                    if disabled:
                        want = ui_draw.DISABLED
                    elif interact == ui_draw.PRESSED:
                        want = ui_draw.PRESSED
                    elif hot:
                        want = ui_draw.HOT
                    elif on:
                        want = ui_draw.ON
                    elif interact == ui_draw.HOVER:
                        want = ui_draw.HOVER
                    else:
                        want = ui_draw.REST
                    assert got == want, (disabled, hot, on, interact, got)


def test_precedence_order_is_the_documented_one():
    assert ui_draw.STATES == (ui_draw.DISABLED, ui_draw.PRESSED, ui_draw.HOT, ui_draw.ON, ui_draw.HOVER,
                         ui_draw.REST)
    # A no-argument call is rest: every existing widget's default.
    assert ui_draw.widget_state() == ui_draw.REST


def test_state_names_are_distinct_constants():
    assert len(set(ui_draw.STATES)) == 6


# -- tokens: derived, never new literals ---------------------------------------

def test_every_state_resolves_on_all_twelve_theme_sets():
    """The restyle promise: a theme that names NO state token still paints
    a correct cue, so all 12 family x variant sets keep working unmodified."""
    for name in THEME_NAMES:
        for variant in THEME_VARIANTS:
            th = fixture_colors(name, variant)
            palette = set(th.values())
            for state in ui_draw.STATES:
                for kind in ("row", "cell"):
                    field, ink, edge = ui_draw.state_colors(th, kind, state)
                    for c in (field, ink, edge):
                        assert isinstance(c, int) and 0 <= c < 64
                    # Token-DERIVED: every colour a state paints is one the
                    # theme already names (or the shared cream/black the whole
                    # toolkit uses), never a fresh literal invented here.
                    assert field in palette
                    assert ink in palette or ink in (0, 7)
                    assert edge in palette


def test_state_tokens_walk_the_alias_chain_in_c():
    """A state token the theme does not hold resolves through its alias chain
    (native/moy_ui), read here through the row kind's specs: hover's edge is
    hover_cue (-> focus -> accent), pressed's field pressed (-> selection ->
    hilite) and its ink pressed_ink (-> selection_ink -> ink)."""
    th = {"panel": 60, "dim": 42, "accent": 9, "hilite": 5, "ink": 3,
          "title_ink": 0}
    assert ui_draw.state_colors(th, "row", ui_draw.HOVER)[2] == 9
    field, ink, _edge = ui_draw.state_colors(th, "row", ui_draw.PRESSED)
    assert (field, ink) == (5, 3)
    # A theme that DOES name the token wins over the chain; index 0 is a
    # value, not a miss.
    th2 = dict(th, focus=0, selection=11)
    assert ui_draw.state_colors(th2, "row", ui_draw.HOVER)[2] == 0
    assert ui_draw.state_colors(th2, "row", ui_draw.PRESSED)[0] == 11
    # A theme silent on every role paints the chain's literal fallback, and a
    # spec with no fallback paints nothing.
    assert ui_draw.state_colors({}, "row", ui_draw.REST) == (60, 7, 1)
    assert ui_draw.state_colors({}, "row_menu", ui_draw.REST) == (None, 6, None)


def test_hover_is_additive_and_disabled_only_dims():
    """SS7's principles as assertions: hover changes the EDGE only (field and
    ink stay at rest), and disabled keeps the field but dims the ink."""
    th = fixture_colors("night")
    rest = ui_draw.state_colors(th, "row", ui_draw.REST)
    hover = ui_draw.state_colors(th, "row", ui_draw.HOVER)
    assert hover[0] == rest[0] and hover[1] == rest[1]
    assert hover[2] != rest[2] and hover[2] == th["focus"]
    dis = ui_draw.state_colors(th, "row", ui_draw.DISABLED)
    assert dis[0] == rest[0]
    assert dis[1] != rest[1] and dis[1] == th["ink_dim"]


def test_ui_stays_a_leaf():
    """The cycle ui -> chrome -> settings_layer -> ui must stay impossible.
    Checked on IMPORT STATEMENTS, not on substrings -- the module documents
    the cycle in prose."""
    import ast
    tree = ast.parse(open(ui_draw.__file__).read())
    names = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.extend(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            names.append(node.module or "")
    assert names, "no imports found -- is the scan still reading ui_draw.py?"
    for name in names:
        leaf = name.split(".")[-1]
        assert leaf not in ("chrome", "console", "settings_layer",
                            "wm_windowed"), name
    # The imports are the C this module is the face of, the array type its
    # token rows and hit buffers travel in, and struct to read a scroll
    # state's velocity.
    assert set(n.split(".")[-1] for n in names) == {
        "moy_ui", "array", "struct", "ustruct"}, names


# =============================================================================
# 3. the pump
# =============================================================================

def _armed(rect=(10, 10, 40, 20), verb="btn", arg=1):
    h = ui_draw.Hits()
    h.add(rect, verb, arg)
    return h


def test_hover_needs_a_pointing_cursor():
    h = _armed()
    assert h.pointer_frame(20, 20, CURSOR) is True
    assert h.hover == ("btn", 1)
    assert h.state_of("btn", 1) == ui_draw.HOVER
    assert h.pressed is None


def test_touch_never_hovers_but_touch_does_press():
    h = _armed()
    assert h.pointer_frame(20, 20, TOUCH) is False     # hidden cursor: no hover
    assert h.hover is None and h.pressed is None
    down = _Ptr(visible=False, down=True)
    assert h.pointer_frame(20, 20, down) is True
    assert h.pressed == ("btn", 1)
    assert h.state_of("btn", 1) == ui_draw.PRESSED
    assert h.hover is None


def test_a_down_cursor_does_not_hover():
    """`visible or hovering, AND NOT down` -- a mouse dragging over a widget is
    pressing something else, not hovering this."""
    h = _armed()
    h.pointer_frame(20, 20, CURSOR)
    assert h.hover is not None
    h.pointer_frame(20, 20, _Ptr(visible=True, down=True))
    assert h.hover is None and h.pressed == ("btn", 1)


def test_the_hovering_flag_is_read_when_a_tier_has_one():
    """moy_input.Pointer has no `hovering` today; the browser/mouse tier will."""
    h = _armed()
    assert h.pointer_frame(20, 20, _Ptr(visible=False)) is False
    assert h.pointer_frame(20, 20, _Ptr(visible=False, hovering=True)) is True
    assert h.hover == ("btn", 1)


def test_pressed_clears_on_release_and_on_leaving_the_rect():
    h = _armed()
    down = _Ptr(visible=False, down=True)
    h.pointer_frame(20, 20, down)
    assert h.pressed == ("btn", 1)
    assert h.pointer_frame(200, 200, down) is True     # slid off: cue drops
    assert h.pressed is None
    assert h.pointer_frame(20, 20, down) is True       # slid back: re-arms
    assert h.pressed == ("btn", 1)
    assert h.pointer_frame(20, 20, TOUCH) is True      # release
    assert h.pressed is None


def test_a_press_that_starts_outside_never_arms():
    """The press EDGE picks the target: dragging INTO a widget with the button
    already down must not light it up."""
    h = _armed()
    down = _Ptr(visible=False, down=True)
    h.pointer_frame(200, 200, down)                    # press edge on nothing
    assert h.pressed is None
    assert h.pointer_frame(20, 20, down) is False
    assert h.pressed is None


def test_pointer_frame_reports_change_only_when_something_changed():
    h = _armed()
    assert h.pointer_frame(20, 20, CURSOR) is True
    assert h.pointer_frame(21, 21, CURSOR) is False    # same widget, no repaint
    assert h.pointer_frame(200, 200, CURSOR) is True
    assert h.pointer_frame(201, 201, CURSOR) is False


def test_pointer_leave_drops_both_and_reports_correctly():
    h = _armed()
    h.pointer_frame(20, 20, CURSOR)
    assert h.pointer_leave() is True
    assert h.hover is None and h.pressed is None
    assert h.pointer_leave() is False                  # already quiet: no repaint
    h.pointer_frame(20, 20, _Ptr(visible=False, down=True))
    assert h.pressed is not None
    assert h.pointer_leave() is True
    assert h.pressed is None
    # Leaving forgets the press-EDGE history: the surface has no pointer past,
    # so the next sample IS an edge. That is what makes a touch tier's first
    # sample after a focus change -- which is the finger going down -- still
    # produce the feedback `pressed` exists for.
    assert h.pointer_frame(20, 20, _Ptr(visible=False, down=True)) is True
    assert h.pressed == ("btn", 1)


def test_ids_persist_across_clear_and_re_resolve_against_fresh_rects():
    """Rects live from one full draw to the next; hover/pressed ids do not die
    with them. This is the stale-after-relayout shelf bug, closed."""
    h = _armed((10, 10, 40, 20), "btn", 1)
    h.pointer_frame(20, 20, CURSOR)
    assert h.hover == ("btn", 1)
    h.clear()
    assert h.at(20, 20) is None                        # the rect is gone
    assert h.hover == ("btn", 1)                       # the id is not
    h.add((10, 10, 40, 20), "other", 2)                # the surface relaid out
    assert h.state_of("btn", 1) == ui_draw.HOVER            # still stale, until...
    assert h.pointer_frame(20, 20, CURSOR) is True     # ...the next SAMPLE
    assert h.hover == ("other", 2)
    assert h.state_of("btn", 1) is None


def test_a_parked_cursor_reseeds_on_the_next_sample_not_on_first_sighting():
    """The cursor has not moved a pixel; a NEW widget was drawn under it. It
    must light up on the next pointer sample -- never require the cursor to
    move, and never light up merely because the draw happened."""
    h = ui_draw.Hits()
    h.add((100, 100, 40, 20), "far", None)
    assert h.pointer_frame(20, 20, CURSOR) is False
    assert h.hover is None
    h.clear()
    h.add((10, 10, 40, 20), "near", None)              # drawn under the cursor
    assert h.hover is None                             # the DRAW changed nothing
    assert h.pointer_frame(20, 20, CURSOR) is True     # the SAMPLE did
    assert h.hover == ("near", None)


def test_state_of_matches_on_both_halves_of_the_id():
    h = ui_draw.Hits()
    h.add((0, 0, 10, 10), "row", 3)
    h.pointer_frame(5, 5, CURSOR)
    assert h.state_of("row", 3) == ui_draw.HOVER
    assert h.state_of("row", 4) is None                # same verb, other arg
    assert h.state_of("cell", 3) is None               # same arg, other verb
    assert h.state_of("row") is None                   # arg defaults to None


def test_at_still_resolves_topmost_and_the_pump_agrees():
    h = ui_draw.Hits()
    h.add((0, 0, 100, 100), "under", 1)
    h.add((10, 10, 20, 20), "over", 2)
    assert h.at(15, 15) == ("over", 2)
    h.pointer_frame(15, 15, CURSOR)
    assert h.hover == ("over", 2)


def test_hits_debug_flags_duplicate_ids():
    """Duplicates are harmless for taps and AMBIGUOUS for state_of. Release
    keeps the old behaviour (last registered wins); debug says so out loud."""
    h = ui_draw.Hits()
    h.add((0, 0, 10, 10), "row", 1)
    h.add((0, 20, 10, 10), "row", 1)                   # release: allowed
    assert h.at(5, 25) == ("row", 1)
    assert ui_draw.HITS_DEBUG is False                      # OFF in the shipped build
    ui_draw.HITS_DEBUG = True
    try:
        g = ui_draw.Hits()
        g.add((0, 0, 10, 10), "row", 1)
        g.add((0, 0, 10, 10), "row", 2)                # different arg: fine
        try:
            g.add((0, 20, 10, 10), "row", 1)
        except ValueError as exc:
            assert "duplicate" in str(exc)
        else:
            raise AssertionError("duplicate id not flagged in debug mode")
    finally:
        ui_draw.HITS_DEBUG = False


# =============================================================================
# 4. Hits stays BOUNDED -- the measured constraint, as a budget
# =============================================================================

def _draw_a_representative_surface(cv, th, hits):
    """One bounded widget set (a tab ladder + a list) beside a 24-cell GRID --
    the arrangement the P4 map tab has, where per-cell registration would cost
    ~9.3ms."""
    ui_draw.tab_row(cv, th, (0, 0, 300, 18),
               (("a", "A", "gear"), ("b", "B", "edit"), ("c", "C", "map")),
               "b", hits=hits)
    for i in range(8):
        ui_draw.row(cv, th, (0, 20 + i * 20, 200, 18), "ROW %d" % i,
               on=(i == 2), hits=hits, verb="row", arg=i)
    for i in range(24):
        ui_draw.cell(cv, th, (200 + (i % 6) * 18, 20 + (i // 6) * 18, 16, 16),
                on=(i == 5), state=ui_draw.cell_state(i, 7, -1))


def test_hits_add_budget_per_full_draw():
    """The Phase 3a perf ratchet, in tests/test_top_bar.py's shape: a FLOOR
    first, so deleting the registrations (or the counter) fails rather than
    sails through, then a cap."""
    cv = SystemCanvas(320, 240)
    th = fixture_colors("night")
    hits = _CountingHits()
    for _frame in range(3):                            # three full draws
        hits.clear()
        before = hits.adds
        _draw_a_representative_surface(cv, th, hits)
        adds = hits.adds - before
        assert adds >= 11, (
            "no hit rects registered (%d) -- did the widgets stop calling "
            "hits.add?" % adds)
        assert adds <= 12, (
            "%d hit rects for 3 tabs + 8 rows + a 24-cell grid; the grid must "
            "register ZERO (see the ui.py module docstring)" % adds)
        assert len(hits._items) == adds                # nothing leaks per frame


def test_a_grid_registers_nothing():
    """The constraint stated directly: 24 cells, 0 rects. `cell` has no `hits`
    parameter, so this cannot regress by someone 'unifying' a call site."""
    cv = SystemCanvas(320, 240)
    th = fixture_colors("night")
    hits = _CountingHits()
    for i in range(24):
        ui_draw.cell(cv, th, ((i % 6) * 20, (i // 6) * 20, 18, 18), "N",
                state=ui_draw.cell_state(i, 3, 4))
    assert hits.adds == 0
    import inspect
    assert "hits" not in inspect.signature(ui_draw.cell).parameters
    assert "hits" in inspect.signature(ui_draw.row).parameters


def test_cell_state_is_the_grids_arithmetic_state_of():
    assert ui_draw.cell_state(3, 3, -1) == ui_draw.HOVER
    assert ui_draw.cell_state(3, 3, 3) == ui_draw.PRESSED        # pressed wins
    assert ui_draw.cell_state(3, -1, 3) == ui_draw.PRESSED
    assert ui_draw.cell_state(3) is None
    # Grids spell "nothing selected" as -1 (FileGridView.sel), and so do the
    # sentinels -- so -1 must never match itself into a cue.
    assert ui_draw.cell_state(-1) is None
    assert ui_draw.cell_state(-1, -1, -1) is None


def test_a_row_reads_its_own_state_from_the_registry():
    """The ergonomic half of the boundedness rule: a bounded surface wires the
    pump once and every row registers AND reads itself."""
    cv = SystemCanvas(200, 60)
    th = fixture_colors("night")
    hits = ui_draw.Hits()
    for i in range(3):
        ui_draw.row(cv, th, (0, i * 20, 200, 18), "R%d" % i,
               hits=hits, verb="row", arg=i)
    assert hits.pointer_frame(10, 25, CURSOR) is True
    assert hits.hover == ("row", 1)
    cv2 = SystemCanvas(200, 60)
    hits.clear()
    for i in range(3):
        ui_draw.row(cv2, th, (0, i * 20, 200, 18), "R%d" % i,
               hits=hits, verb="row", arg=i)
    # Row 1 now wears the hover edge; rows 0 and 2 do not.
    assert cv2.pix(0, 20) == th["focus"]
    assert cv2.pix(0, 0) == th["dim"]


# =============================================================================
# 5. the row kind
# =============================================================================

def test_row_rest_and_selected_paint_the_row_tokens():
    th = fixture_colors("night")
    cv = SystemCanvas(200, 60)
    ui_draw.row(cv, th, (0, 0, 200, 20), "REST")
    assert cv.pix(2, 2) == th["panel"]
    assert cv.pix(0, 0) == th["dim"]
    ui_draw.row(cv, th, (0, 20, 200, 20), "SEL", on=True)
    assert cv.pix(2, 22) == th["title"]
    assert cv.pix(0, 20) == th["accent"]
    ui_draw.row(cv, th, (0, 40, 200, 20), "ARMED", hot=True)
    assert cv.pix(2, 42) == th["danger"]


def test_row_field_none_paints_nothing_and_edge_is_optional():
    """Settings and the system menu paint no field on an unselected row and no
    border at all; a `row` that could not express that would not fit them."""
    th = fixture_colors("night")
    cv = SystemCanvas(100, 20)
    cv.cls(31)
    ui_draw.row(cv, th, (0, 0, 100, 20), "", colors=(None, 7, 3), edge=False)
    assert cv.pix(0, 0) == 31 and cv.pix(50, 10) == 31
    ui_draw.row(cv, th, (0, 0, 100, 20), "", colors=(None, 7, 3), edge=True)
    assert cv.pix(0, 0) == 3


def test_row_disabled_dims_and_does_not_register():
    """"dim ink, NON-registering": an unusable row must not be tappable, which
    is exactly what the four hand-rolled disabled sites get wrong today."""
    th = fixture_colors("night")
    cv = SystemCanvas(200, 20)
    hits = ui_draw.Hits()
    ui_draw.row(cv, th, (0, 0, 200, 20), "LOCKED", disabled=True,
           hits=hits, verb="row", arg=0)
    assert hits.at(10, 10) is None
    hits.clear()
    ui_draw.row(cv, th, (0, 0, 200, 20), "OPEN", hits=hits, verb="row", arg=0)
    assert hits.at(10, 10) == ("row", 0)


def test_row_truncates_the_label_and_the_value_inside_the_rect():
    """#174's lesson generalized: overflow must CLIP, never escape the rect and
    land ink on whatever is beside it."""
    th = fixture_colors("night")
    cv = SystemCanvas(200, 40)
    cv.cls(31)
    ui_draw.row(cv, th, (20, 10, 60, 20), "A VERY LONG ROW LABEL",
           value="ALSO LONG", colors=(None, 7, 3), edge=False)
    for x in range(20):                                # nothing left of the row
        assert cv.pix(x, 20) == 31
    for x in range(80, 200):                           # nothing right of it
        assert cv.pix(x, 20) == 31


def test_row_value_is_right_aligned_and_label_keeps_the_rest():
    th = fixture_colors("night")
    cv = SystemCanvas(200, 20)
    cv.cls(31)
    ui_draw.row(cv, th, (0, 0, 200, 20), "WIFI", value="OFF",
           colors=(None, 7, 3), edge=False, pad=4)
    # "OFF" ends one pad short of the right edge: 200 - 4 - 24 = 172.
    assert any(cv.pix(x, 6) == 7 for x in range(172, 196))
    assert cv.pix(198, 10) == 31


def test_row_icon_and_glyph_shift_the_label(monkeypatch):
    """Achievements' leading glyph, Settings' trailing status icon."""
    th = fixture_colors("night")
    plain = SystemCanvas(200, 20)
    ui_draw.row(plain, th, (0, 0, 200, 20), "NAME", colors=(None, 7, 3), edge=False)
    withg = SystemCanvas(200, 20)
    ui_draw.row(withg, th, (0, 0, 200, 20), "NAME", glyph="lock",
           glyph_draw=_glyph_draw, colors=(None, 7, 3), edge=False)
    assert bytes(plain._buf) != bytes(withg._buf)
    withi = SystemCanvas(200, 20)
    ui_draw.row(withi, th, (0, 0, 200, 20), "NAME", icon_img=IMG,
           colors=(None, 7, 3), edge=False)
    assert bytes(withi._buf) != bytes(plain._buf)


def test_row_pad_and_text_dy_reproduce_the_frozen_geometries():
    """Settings' literal (4, 5), Files' (4*fs, 6*fs), Storybook's centred
    label -- the three the converting phases must be able to hit exactly."""
    th = fixture_colors("night")
    a = SystemCanvas(200, 26)
    ui_draw.row(a, th, (0, 0, 200, 26), "X", colors=(None, 7, 3), edge=False,
           pad=4, text_dy=5)
    b = SystemCanvas(200, 26)
    b.print("X", 4, 5, 7, 1)
    assert bytes(a._buf) == bytes(b._buf)
    c = SystemCanvas(200, 26)
    ui_draw.row(c, th, (0, 0, 200, 26), "X", colors=(None, 7, 3), edge=False, pad=6)
    d = SystemCanvas(200, 26)
    d.print("X", 6, (26 - 8) // 2, 7, 1)
    assert bytes(c._buf) == bytes(d._buf)


# =============================================================================
# 6. the cell kind
# =============================================================================

def test_cell_returns_the_art_rect_and_matches_the_pure_geometry():
    th = fixture_colors("night")
    cv = SystemCanvas(200, 120)
    art = ui_draw.cell(cv, th, (0, 0, 76, 66), "name")
    assert art == ui_draw.cell_art_rect((0, 0, 76, 66), 1, 2, 14)
    assert art == (2, 2, 72, 50)                       # FileGridView's numbers
    assert ui_draw.cell_art_rect((0, 0, 76, 66), 1, 3, 17) == (3, 3, 70, 46)
    # A frame-only cell (cards' choice cells): pad 0, no caption.
    assert ui_draw.cell_art_rect((5, 5, 20, 20), 1, 0, 0) == (5, 5, 20, 20)


def test_cell_selected_lifts_the_field_and_the_edge():
    th = fixture_colors("night")
    cv = SystemCanvas(200, 120)
    ui_draw.cell(cv, th, (0, 0, 76, 66), "a")
    assert cv.pix(1, 1) == th["panel"] and cv.pix(0, 0) == th["dim"]
    ui_draw.cell(cv, th, (80, 0, 76, 66), "b", on=True)
    assert cv.pix(81, 1) == th["title"] and cv.pix(80, 0) == th["accent"]


def test_cell_band_fills_the_caption_zone():
    """The wallpaper cards' look: a filled strip with a left-aligned title,
    accented when selected. Without a band the label prints on the field."""
    th = fixture_colors("night")
    cv = SystemCanvas(200, 120)
    # y = 78 is inside the 17px caption zone (63..79) but below the label row
    # (67..74), so it reads the ZONE, not the glyphs.
    ui_draw.cell(cv, th, (0, 0, 100, 80), "TITLE", band=True, pad=3, caption_h=17)
    assert cv.pix(50, 78) == th["title"]
    ui_draw.cell(cv, th, (100, 0, 100, 80), "TITLE", band=True, on=True, pad=3,
            caption_h=17)
    assert cv.pix(150, 78) == th["accent"]
    plain = SystemCanvas(120, 90)
    ui_draw.cell(plain, th, (0, 0, 100, 80), "TITLE", pad=3, caption_h=17)
    assert plain.pix(50, 78) == th["panel"]             # no band: the field


def test_cell_art_never_covers_the_border_or_the_caption():
    """Draw order is field -> caption -> edge, and the art rect excludes both,
    so a caller's picture cannot land on the frame."""
    th = fixture_colors("night")
    cv = SystemCanvas(120, 90)
    ax, ay, aw, ah = ui_draw.cell(cv, th, (0, 0, 100, 80), "T", band=True, pad=3,
                             caption_h=17)
    assert ax >= 3 and ay >= 3
    assert ax + aw <= 100 - 3
    assert ay + ah <= 80 - 17


def test_cell_states_route_through_the_same_model_as_row():
    th = fixture_colors("night")
    for state in (ui_draw.HOVER, ui_draw.PRESSED):
        cv = SystemCanvas(60, 60)
        ui_draw.cell(cv, th, (0, 0, 40, 40), None, state=state)
        want = ui_draw.state_colors(th, "cell", state)
        assert cv.pix(0, 0) == want[2]
        assert cv.pix(1, 1) == want[0]


def test_cell_draws_a_centred_glyph_or_icon_in_the_art_rect():
    th = fixture_colors("night")
    a = SystemCanvas(60, 60)
    art = ui_draw.cell(a, th, (0, 0, 40, 40), None, glyph="dot",
                  glyph_draw=_glyph_draw, pad=0)
    assert art == (0, 0, 40, 40)
    cx = art[0] + (art[2] - 14) // 2
    assert a.pix(cx + 1, art[1] + (art[3] - 14) // 2 + 1) == \
        ui_draw.state_colors(th, "cell", ui_draw.REST)[1]
    b = SystemCanvas(60, 60)
    ui_draw.cell(b, th, (0, 0, 40, 40), None, icon_img=IMG, pad=0)
    assert bytes(a._buf) != bytes(b._buf)


# =============================================================================
# 5. the widget matrix: every kind in every state, each theme in its shape
# =============================================================================

def _paint_matrix(cv, th):
    """Every style kind in all six states through the widget that draws it
    (row, cell, chip, button), plus a kind the table does not name."""
    own = {
        "tab": lambda r: ui_draw.tab_row(cv, th, (0, r[1], 318, 14),
                                    (("a", "Aa", "gear"), ("b", "Bb", "map")),
                                    "b"),
        "status": lambda r: ui_draw.status_row(cv, th, (0, r[1], 318, 14),
                                          ("Ln 1", "ok")),
        "panel": lambda r: ui_draw.panel(cv, th, (2, r[1] + 1, 150, 12)),
        "panel_title": lambda r: ui_draw.panel(cv, th, (160, r[1] + 1, 150, 12),
                                          title="T"),
        "toolbar": lambda r: ui_draw.toolbar(cv, th, (0, r[1], 318, 14)),
        "scrollbar": lambda r: _matrix_bar(cv, th, r[1]),
        "focus": lambda r: ui_draw.focus_ring(cv, th, (20, r[1] + 4, 100, 6)),
    }
    # The kinds as they were pinned (step 1); a kind added since is pinned by
    # its own test, so it never moves these hashes.
    kinds = sorted(k for k in ui_draw.kinds() if k not in ("toggle",)) + ["nosuchkind"]
    y = 0
    for kind in kinds:
        if kind in own:
            own[kind]((0, y))
            y += 15
            continue
        x = 0
        for st in ui_draw.STATES:
            r = (x, y, 52, 14)
            on, hot, dis = st == ui_draw.ON, st == ui_draw.HOT, st == ui_draw.DISABLED
            interact = st if st in (ui_draw.HOVER, ui_draw.PRESSED) else None
            if kind.startswith("button"):
                bk = {"button": "normal"}.get(kind, kind[7:])
                ui_draw.button(cv, th, r, "B" + kind[:3], kind=bk, on=on,
                          disabled=dis, state=interact)
            elif kind == "chip":
                ui_draw.chip(cv, th, r, "CHIP", on=on, hot=hot, disabled=dis,
                        state=interact)
            elif kind.startswith("cell"):
                ui_draw.cell(cv, th, r, "C", kind=kind, on=on, hot=hot,
                        disabled=dis, state=interact, band=kind == "cell_band")
            else:
                ui_draw.row(cv, th, r, kind, kind=kind, on=on, hot=hot,
                       disabled=dis, state=interact, value="v")
            x += 53
        y += 15


def _matrix_bar(cv, th, y):
    sr = ui_draw.ScrollRegion(horizontal=True)
    sr.set((0, y, 318, 14), 900)
    sr.scroll_by(200)
    sr.draw_bar(cv, th)


# theme/variant sha256[:16] of the index readout at 320x320@1, the theme's
# shape installed (Contrast's re-pinned by the look's step 11: its `value`;
# every theme's by a labelled tab chip's pad after its label, 2026-10-19).
WIDGET_MATRIX_HASHES = (
    "night/dark 962f46bb8830d0b3",
    "night/light 70c63fd09357ba7d",
    "indigo/dark 7fcda7382ce42fe0",
    "indigo/light 17a93acbf4d22ec0",
    "berry/dark 6f1bcb6502e23d2b",
    "berry/light e247e2549ad7a5e5",
    "forest/dark 07377d5e5a2264e9",
    "forest/light faf765626d7f8707",
    "slate/dark d76f0d134006e4b3",
    "slate/light 8b44361ff5b3a71e",
    "machine/dark 936680b6f00e0139",
    "machine/light 2e4214141e347918",
    "platinum/dark 3257f5546afba3ee",
    "platinum/light 13e7d1e70cd8a1fb",
    "win95/dark 8ca26d4ca565dc03",
    "win95/light 81fbc46dc9cb148f",
    "outline/dark 73303068057fbda9",
    "outline/light d9dcc7e6c55ee618",
)


def _matrix_hashes():
    out = []
    try:
        for name in THEME_NAMES:
            th = {v: fixture_colors(name, v) for v in THEME_VARIANTS}
            install_theme(name)                # its shape: the slot's
            ui_draw.use_shape(name)
            for variant in THEME_VARIANTS:
                cv = SystemCanvas(320, 320)
                _paint_matrix(cv, th[variant])
                out.append("%s/%s %s" % (
                    name, variant, hashlib.sha256(indices_of(cv)).hexdigest()[:16]))
    finally:
        ui_draw.use_shape(None)
    return out


def test_every_kind_in_every_state_is_byte_identical():
    """The widget matrix (docs/kernel_toolkit_2026-10.md §7): each style kind
    in each state, every seed theme in each variant under its own shape --
    pinned so a style or a painter change moves none of it unnamed."""
    got = _matrix_hashes()
    assert len(got) == len(WIDGET_MATRIX_HASHES) == 2 * len(THEME_NAMES)
    for g, w in zip(got, WIDGET_MATRIX_HASHES):
        assert g == w, "a kind's pixels moved: %s (pinned %s)" % (g, w)


def test_the_toggle_is_a_chip_that_reads_switched_on():
    """A kind added as data (sprint 6, docs/kernel_toolkit_2026-10.md §2.2):
    chip's shape and chip's look, except ON, which paints the theme's play
    green with light ink."""
    th = fixture_colors("machine")
    for st in ui_draw.STATES:
        t, c = ui_draw.state_colors(th, "toggle", st), ui_draw.state_colors(th, "chip", st)
        if st == ui_draw.ON:
            assert t == (th["play"], 7, c[2])
        else:
            assert t == c, st
    a, b = SystemCanvas(80, 24), SystemCanvas(80, 24)
    ui_draw.chip(a, th, (2, 2, 70, 18), "ON", on=True, kind="toggle")
    ui_draw.chip(b, th, (2, 2, 70, 18), "ON", on=True)
    assert bytes(a._buf) != bytes(b._buf)


def test_no_tap_is_lost_past_the_capacity(capsys):
    """An add past the hit buffer's capacity is refused, said ONCE per
    surface, and an error under HITS_DEBUG (docs/kernel_toolkit_2026-10.md
    §3.4): the widget drawn last must never lose its tap silently."""
    hits = ui_draw.Hits(cap=4)
    for i in range(6):
        hits.add((i * 10, 0, 10, 10), "k", i)
    assert len(hits._items) == 4
    assert hits.at(5, 5) == ("k", 0) and hits.at(45, 5) is None
    out = capsys.readouterr().out
    assert out.count("HITS FULL") == 1, out
    assert ui_draw.HITS_CAP == 32
    ui_draw.HITS_DEBUG = True
    try:
        h = ui_draw.Hits(cap=1)
        h.add((0, 0, 1, 1), "a")
        try:
            h.add((0, 0, 1, 1), "b")
        except OverflowError:
            pass
        else:
            raise AssertionError("a full buffer did not raise under HITS_DEBUG")
    finally:
        ui_draw.HITS_DEBUG = False
