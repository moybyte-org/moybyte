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
from runtime.chrome import THEME_NAMES, THEME_VARIANTS, theme_colors

# The seeds of MOY64 colours, whose widgets these pin byte for byte; `moy`'s
# RGB roles are tests/test_theme_rgb.py's and its goldens'.
THEME_NAMES = tuple(n for n in THEME_NAMES if n != "moy")
from runtime.host_canvas import make_system_canvas as SystemCanvas
from runtime.host_canvas import indices_of


TH = theme_colors("machine")


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


class _CountingHits(ui.Hits):
    """Counts registrations. The budget test below asserts a FLOOR before a
    cap, so deleting the counter (or the registration) fails rather than
    passes -- the shape tests/test_top_bar.py established."""

    def __init__(self):
        ui.Hits.__init__(self)
        self.adds = 0

    def add(self, rect, verb, arg=None):
        self.adds += 1
        ui.Hits.add(self, rect, verb, arg)


# =============================================================================
# 1. nothing existing moved
# =============================================================================

# theme/variant WxH@font_scale sha256[:16] of the index readout. Taken on the
# pre-Phase-3a tree; a raw RGB565 A/B of the two module objects in one process
# agreed on all 48 (0 differing bytes).
SHIPPED_WIDGET_HASHES = (
    "night/dark 320x240@1 9b3213a12ef6aa66",
    "night/dark 480x320@1 addf42889ff3f81b",
    "night/dark 1024x600@2 f782ffe78a4e7632",
    "night/dark 320x240@3 7cddefd066c52b96",
    "night/light 320x240@1 5635396e37980851",
    "night/light 480x320@1 66b148010a402929",
    "night/light 1024x600@2 49ed8f11eea607a1",
    "night/light 320x240@3 f8b0be4bf8b6e201",
    "indigo/dark 320x240@1 f7f30745d353139e",
    "indigo/dark 480x320@1 67e285c27cb855cc",
    "indigo/dark 1024x600@2 132f7ab64feffedd",
    "indigo/dark 320x240@3 444fdee986a3b0c8",
    "indigo/light 320x240@1 f8d92eaa38302b1b",
    "indigo/light 480x320@1 7a9da76c75747824",
    "indigo/light 1024x600@2 a776df2d582d7525",
    "indigo/light 320x240@3 2c1df2aa3a2935ec",
    "berry/dark 320x240@1 2598af81f5028512",
    "berry/dark 480x320@1 5dd15d7f1fb630f9",
    "berry/dark 1024x600@2 83a6b7e663abcb33",
    "berry/dark 320x240@3 39e125b6cde32ca1",
    "berry/light 320x240@1 9b52b87610ee9087",
    "berry/light 480x320@1 c3f7146803f30a7f",
    "berry/light 1024x600@2 67140521889d6dae",
    "berry/light 320x240@3 e2d3395d2306e7d2",
    "forest/dark 320x240@1 0f17bffe48c50373",
    "forest/dark 480x320@1 8cc0f95b13b6c4c3",
    "forest/dark 1024x600@2 fc0e252ea998e51b",
    "forest/dark 320x240@3 7b442d4b9ab9fc9e",
    "forest/light 320x240@1 2a18a81f55f951f4",
    "forest/light 480x320@1 71479964c0535242",
    "forest/light 1024x600@2 13367de583abfc13",
    "forest/light 320x240@3 37450731fc28bbf9",
    "slate/dark 320x240@1 1becd4c23700133b",
    "slate/dark 480x320@1 d184ea801a2214e0",
    "slate/dark 1024x600@2 83da7976e7421380",
    "slate/dark 320x240@3 223c35ebc1e5f58d",
    "slate/light 320x240@1 329deda2b54715fe",
    "slate/light 480x320@1 62605e1e1580a2a2",
    "slate/light 1024x600@2 0b73f630328b7845",
    "slate/light 320x240@3 77ba0f7ac06f3485",
    "machine/dark 320x240@1 ec636e8b2d7190f0",
    "machine/dark 480x320@1 105a8f20fdaa4264",
    "machine/dark 1024x600@2 578a11a020234908",
    "machine/dark 320x240@3 ef771cc323804ea5",
    "machine/light 320x240@1 b91fd481fa34bf26",
    "machine/light 480x320@1 e2aacc49e09e86e5",
    "machine/light 1024x600@2 8c2e8faa5158bcb4",
    "machine/light 320x240@3 81e9acfe363c0c5f",
    "platinum/dark 320x240@1 720d378fe76856ea",
    "platinum/dark 480x320@1 d3881c99b479b558",
    "platinum/dark 1024x600@2 f54ffd18e95aba4b",
    "platinum/dark 320x240@3 6c2d9c1bbb67ac68",
    "platinum/light 320x240@1 d76254937abfbaaf",
    "platinum/light 480x320@1 453994ad5c07444f",
    "platinum/light 1024x600@2 17caca48ecbcb934",
    "platinum/light 320x240@3 5e98c3e329ff2340",
    "win95/dark 320x240@1 669526c9a144d6a9",
    "win95/dark 480x320@1 312e567c4ff10021",
    "win95/dark 1024x600@2 ca63600e21f6a4ed",
    "win95/dark 320x240@3 69cf2b704ed0a105",
    "win95/light 320x240@1 47dfdb1ffea748f0",
    "win95/light 480x320@1 e76431b27e20bf92",
    "win95/light 1024x600@2 6a9a775240af1495",
    "win95/light 320x240@3 51045a8ca03e6902",
    "outline/dark 320x240@1 9b3213a12ef6aa66",
    "outline/dark 480x320@1 addf42889ff3f81b",
    "outline/dark 1024x600@2 f782ffe78a4e7632",
    "outline/dark 320x240@3 7cddefd066c52b96",
    "outline/light 320x240@1 5635396e37980851",
    "outline/light 480x320@1 66b148010a402929",
    "outline/light 1024x600@2 49ed8f11eea607a1",
    "outline/light 320x240@3 f8b0be4bf8b6e201",
    "contrast/dark 320x240@1 19c96d673e0dfe5e",
    "contrast/dark 480x320@1 144f273d58e73bb2",
    "contrast/dark 1024x600@2 de8ba0be06de2403",
    "contrast/dark 320x240@3 c002fd3158a8fbb5",
    "contrast/light 320x240@1 202b2edd459f43f3",
    "contrast/light 480x320@1 d8b5083fdbd5d3c3",
    "contrast/light 1024x600@2 b993207733865b98",
    "contrast/light 320x240@3 34e4243eedca6eb3",
)

_SIZES = ((320, 240, 1), (480, 320, 1), (1024, 600, 2), (320, 240, 3))


def _paint_shipped(cv, th):
    """Every SHIPPED widget, with arguments chosen to walk each branch. Nothing
    added by Phase 3a appears here -- that is the point."""
    ui.scroll_cues(cv, (4, 4), (4, 200), True, True, th["accent"], 2)
    ui.button(cv, th, (8, 14, 90, 22), "PLAY", kind="play")
    ui.button(cv, th, (8, 40, 90, 22), "CHANGE", kind="normal")
    ui.button(cv, th, (8, 66, 90, 22), "AUTHOR", kind="author")
    ui.button(cv, th, (8, 92, 90, 22), "DELETE FOREVER", kind="danger")
    ui.button(cv, th, (8, 118, 90, 22), "ON", on=True)
    ui.button(cv, th, (8, 144, 90, 22), "ICON", icon_img=IMG)
    ui.button(cv, th, (8, 170, 90, 22), "GLYPH", glyph="run",
              glyph_draw=_glyph_draw)
    ui.chip(cv, th, (104, 14, 60, 18), "QUIET")
    ui.chip(cv, th, (104, 34, 60, 18), "ON", on=True)
    ui.chip(cv, th, (104, 54, 60, 18), "HOT", hot=True)
    ui.chip(cv, th, (104, 74, 60, 18), "OVERFLOWING LABEL")
    ui.chip(cv, th, (104, 94, 60, 18), "G", glyph="edit",
            glyph_draw=_glyph_draw)
    ui.chip(cv, th, (104, 114, 60, 18), "FS3", fs=3)
    hits = ui.Hits()
    ui.tab_row(cv, th, (104, 136, 200, 20),
               (("cfg", "Config", "gear"), ("code", "Code", "edit"),
                ("map", "Map", "map")), "code",
               icon_for=lambda k: IMG, hits=hits)
    ui.tab_row(cv, th, (104, 158, 60, 20),
               (("cfg", "Config", "gear"), ("code", "Code", "edit"),
                ("map", "Map", "map")), "cfg", icon_for=None, ink=5)
    ui.status_row(cv, th, (104, 180, 200, 14), ("Ln 13, Col 1", "No issues"))
    ui.game_btn(cv, (170, 14, 70, 20), "GO", 11)
    ui.game_icon_btn(cv, (170, 36, 70, 20), "run", "GO", 11,
                     glyph_draw=_glyph_draw)
    ui.mini_btn(cv, (170, 58, 30, 12), "-", 9)
    ui.toolbar(cv, th, (170, 72, 140, 16))
    ui.dialog(cv, (170, 90, 130, 40))
    ui.text_field(cv, (174, 94, 120, 14), "hi", "")
    ui.text_field(cv, (174, 112, 120, 14), "", "type here")
    content = ui.panel(cv, th, (250, 14, 60, 60), title="TOOLS")
    ui.focus_ring(cv, th, ui.inset(content, 4, 4))
    ui.fill_uncovered(cv, (0, 200, 320, 40), (10, 205, 100, 20), th["dim"])
    ui.fill_uncovered(cv, (240, 200, 60, 20), (0, 0, 4, 4), th["edge"])
    sr = ui.ScrollRegion()
    sr.set((250, 80, 60, 100), 400)
    sr.scroll_by(120)
    sr.draw_bar(cv, th)
    sh = ui.ScrollRegion(horizontal=True)
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
                th = theme_colors(name, variant)
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
    for th in (theme_colors("night"), theme_colors("machine", "light")):
        for kwargs in ({}, {"on": True}, {"hot": True}):
            a = SystemCanvas(80, 30)
            b = SystemCanvas(80, 30)
            ui.chip(a, th, (2, 2, 70, 20), "HI", **kwargs)
            ui.chip(b, th, (2, 2, 70, 20), "HI",
                    disabled=False, state=None, **kwargs)
            assert bytes(a._buf) == bytes(b._buf)
        for kind in ("normal", "play", "author", "danger"):
            a = SystemCanvas(80, 30)
            b = SystemCanvas(80, 30)
            ui.button(a, th, (2, 2, 70, 20), "HI", kind=kind)
            ui.button(b, th, (2, 2, 70, 20), "HI", kind=kind,
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
                for interact in (None, ui.HOVER, ui.PRESSED):
                    got = ui.widget_state(on=on, hot=hot, disabled=disabled,
                                          interact=interact)
                    if disabled:
                        want = ui.DISABLED
                    elif interact == ui.PRESSED:
                        want = ui.PRESSED
                    elif hot:
                        want = ui.HOT
                    elif on:
                        want = ui.ON
                    elif interact == ui.HOVER:
                        want = ui.HOVER
                    else:
                        want = ui.REST
                    assert got == want, (disabled, hot, on, interact, got)


def test_precedence_order_is_the_documented_one():
    assert ui.STATES == (ui.DISABLED, ui.PRESSED, ui.HOT, ui.ON, ui.HOVER,
                         ui.REST)
    # A no-argument call is rest: every existing widget's default.
    assert ui.widget_state() == ui.REST


def test_state_names_are_distinct_constants():
    assert len(set(ui.STATES)) == 6


# -- tokens: derived, never new literals ---------------------------------------

def test_every_state_resolves_on_all_twelve_theme_sets():
    """The restyle promise: a theme that names NO state token still paints
    a correct cue, so all 12 family x variant sets keep working unmodified."""
    for name in THEME_NAMES:
        for variant in THEME_VARIANTS:
            th = theme_colors(name, variant)
            palette = set(th.values())
            for state in ui.STATES:
                for kind in ("row", "cell"):
                    field, ink, edge = ui.state_colors(th, kind, state)
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
    assert ui.state_colors(th, "row", ui.HOVER)[2] == 9
    field, ink, _edge = ui.state_colors(th, "row", ui.PRESSED)
    assert (field, ink) == (5, 3)
    # A theme that DOES name the token wins over the chain; index 0 is a
    # value, not a miss.
    th2 = dict(th, focus=0, selection=11)
    assert ui.state_colors(th2, "row", ui.HOVER)[2] == 0
    assert ui.state_colors(th2, "row", ui.PRESSED)[0] == 11
    # A theme silent on every role paints the chain's literal fallback, and a
    # spec with no fallback paints nothing.
    assert ui.state_colors({}, "row", ui.REST) == (60, 7, 1)
    assert ui.state_colors({}, "row_menu", ui.REST) == (None, 6, None)


def test_hover_is_additive_and_disabled_only_dims():
    """SS7's principles as assertions: hover changes the EDGE only (field and
    ink stay at rest), and disabled keeps the field but dims the ink."""
    th = theme_colors("night")
    rest = ui.state_colors(th, "row", ui.REST)
    hover = ui.state_colors(th, "row", ui.HOVER)
    assert hover[0] == rest[0] and hover[1] == rest[1]
    assert hover[2] != rest[2] and hover[2] == th["focus"]
    dis = ui.state_colors(th, "row", ui.DISABLED)
    assert dis[0] == rest[0]
    assert dis[1] != rest[1] and dis[1] == th["ink_dim"]


def test_ui_stays_a_leaf():
    """The cycle ui -> chrome -> settings_layer -> ui must stay impossible.
    Checked on IMPORT STATEMENTS, not on substrings -- the module documents
    the cycle in prose."""
    import ast
    tree = ast.parse(open(ui.__file__).read())
    names = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.extend(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            names.append(node.module or "")
    assert names, "no imports found -- is the scan still reading ui.py?"
    for name in names:
        leaf = name.split(".")[-1]
        assert leaf not in ("chrome", "console", "settings_layer",
                            "wm_windowed"), name
    # The imports are the C this module is the face of, the array type its
    # token rows and hit buffers travel in, struct to read a scroll state's
    # velocity, and the peer leaf that owns the single hit-test.
    assert set(n.split(".")[-1] for n in names) == {
        "widgets", "moy_ui", "array", "struct", "ustruct"}, names


# =============================================================================
# 3. the pump
# =============================================================================

def _armed(rect=(10, 10, 40, 20), verb="btn", arg=1):
    h = ui.Hits()
    h.add(rect, verb, arg)
    return h


def test_hover_needs_a_pointing_cursor():
    h = _armed()
    assert h.pointer_frame(20, 20, CURSOR) is True
    assert h.hover == ("btn", 1)
    assert h.state_of("btn", 1) == ui.HOVER
    assert h.pressed is None


def test_touch_never_hovers_but_touch_does_press():
    h = _armed()
    assert h.pointer_frame(20, 20, TOUCH) is False     # hidden cursor: no hover
    assert h.hover is None and h.pressed is None
    down = _Ptr(visible=False, down=True)
    assert h.pointer_frame(20, 20, down) is True
    assert h.pressed == ("btn", 1)
    assert h.state_of("btn", 1) == ui.PRESSED
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
    assert h.state_of("btn", 1) == ui.HOVER            # still stale, until...
    assert h.pointer_frame(20, 20, CURSOR) is True     # ...the next SAMPLE
    assert h.hover == ("other", 2)
    assert h.state_of("btn", 1) is None


def test_a_parked_cursor_reseeds_on_the_next_sample_not_on_first_sighting():
    """The cursor has not moved a pixel; a NEW widget was drawn under it. It
    must light up on the next pointer sample -- never require the cursor to
    move, and never light up merely because the draw happened."""
    h = ui.Hits()
    h.add((100, 100, 40, 20), "far", None)
    assert h.pointer_frame(20, 20, CURSOR) is False
    assert h.hover is None
    h.clear()
    h.add((10, 10, 40, 20), "near", None)              # drawn under the cursor
    assert h.hover is None                             # the DRAW changed nothing
    assert h.pointer_frame(20, 20, CURSOR) is True     # the SAMPLE did
    assert h.hover == ("near", None)


def test_state_of_matches_on_both_halves_of_the_id():
    h = ui.Hits()
    h.add((0, 0, 10, 10), "row", 3)
    h.pointer_frame(5, 5, CURSOR)
    assert h.state_of("row", 3) == ui.HOVER
    assert h.state_of("row", 4) is None                # same verb, other arg
    assert h.state_of("cell", 3) is None               # same arg, other verb
    assert h.state_of("row") is None                   # arg defaults to None


def test_at_still_resolves_topmost_and_the_pump_agrees():
    h = ui.Hits()
    h.add((0, 0, 100, 100), "under", 1)
    h.add((10, 10, 20, 20), "over", 2)
    assert h.at(15, 15) == ("over", 2)
    h.pointer_frame(15, 15, CURSOR)
    assert h.hover == ("over", 2)


def test_hits_debug_flags_duplicate_ids():
    """Duplicates are harmless for taps and AMBIGUOUS for state_of. Release
    keeps the old behaviour (last registered wins); debug says so out loud."""
    h = ui.Hits()
    h.add((0, 0, 10, 10), "row", 1)
    h.add((0, 20, 10, 10), "row", 1)                   # release: allowed
    assert h.at(5, 25) == ("row", 1)
    assert ui.HITS_DEBUG is False                      # OFF in the shipped build
    ui.HITS_DEBUG = True
    try:
        g = ui.Hits()
        g.add((0, 0, 10, 10), "row", 1)
        g.add((0, 0, 10, 10), "row", 2)                # different arg: fine
        try:
            g.add((0, 20, 10, 10), "row", 1)
        except ValueError as exc:
            assert "duplicate" in str(exc)
        else:
            raise AssertionError("duplicate id not flagged in debug mode")
    finally:
        ui.HITS_DEBUG = False


# =============================================================================
# 4. Hits stays BOUNDED -- the measured constraint, as a budget
# =============================================================================

def _draw_a_representative_surface(cv, th, hits):
    """One bounded widget set (a tab ladder + a list) beside a 24-cell GRID --
    the arrangement the P4 map tab has, where per-cell registration would cost
    ~9.3ms."""
    ui.tab_row(cv, th, (0, 0, 300, 18),
               (("a", "A", "gear"), ("b", "B", "edit"), ("c", "C", "map")),
               "b", hits=hits)
    for i in range(8):
        ui.row(cv, th, (0, 20 + i * 20, 200, 18), "ROW %d" % i,
               on=(i == 2), hits=hits, verb="row", arg=i)
    for i in range(24):
        ui.cell(cv, th, (200 + (i % 6) * 18, 20 + (i // 6) * 18, 16, 16),
                on=(i == 5), state=ui.cell_state(i, 7, -1))


def test_hits_add_budget_per_full_draw():
    """The Phase 3a perf ratchet, in tests/test_top_bar.py's shape: a FLOOR
    first, so deleting the registrations (or the counter) fails rather than
    sails through, then a cap."""
    cv = SystemCanvas(320, 240)
    th = theme_colors("night")
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
    th = theme_colors("night")
    hits = _CountingHits()
    for i in range(24):
        ui.cell(cv, th, ((i % 6) * 20, (i // 6) * 20, 18, 18), "N",
                state=ui.cell_state(i, 3, 4))
    assert hits.adds == 0
    import inspect
    assert "hits" not in inspect.signature(ui.cell).parameters
    assert "hits" in inspect.signature(ui.row).parameters


def test_cell_state_is_the_grids_arithmetic_state_of():
    assert ui.cell_state(3, 3, -1) == ui.HOVER
    assert ui.cell_state(3, 3, 3) == ui.PRESSED        # pressed wins
    assert ui.cell_state(3, -1, 3) == ui.PRESSED
    assert ui.cell_state(3) is None
    # Grids spell "nothing selected" as -1 (FileGridView.sel), and so do the
    # sentinels -- so -1 must never match itself into a cue.
    assert ui.cell_state(-1) is None
    assert ui.cell_state(-1, -1, -1) is None


def test_a_row_reads_its_own_state_from_the_registry():
    """The ergonomic half of the boundedness rule: a bounded surface wires the
    pump once and every row registers AND reads itself."""
    cv = SystemCanvas(200, 60)
    th = theme_colors("night")
    hits = ui.Hits()
    for i in range(3):
        ui.row(cv, th, (0, i * 20, 200, 18), "R%d" % i,
               hits=hits, verb="row", arg=i)
    assert hits.pointer_frame(10, 25, CURSOR) is True
    assert hits.hover == ("row", 1)
    cv2 = SystemCanvas(200, 60)
    hits.clear()
    for i in range(3):
        ui.row(cv2, th, (0, i * 20, 200, 18), "R%d" % i,
               hits=hits, verb="row", arg=i)
    # Row 1 now wears the hover edge; rows 0 and 2 do not.
    assert cv2.pix(0, 20) == th["focus"]
    assert cv2.pix(0, 0) == th["dim"]


# =============================================================================
# 5. the row kind
# =============================================================================

def test_row_rest_and_selected_paint_the_row_tokens():
    th = theme_colors("night")
    cv = SystemCanvas(200, 60)
    ui.row(cv, th, (0, 0, 200, 20), "REST")
    assert cv.pix(2, 2) == th["panel"]
    assert cv.pix(0, 0) == th["dim"]
    ui.row(cv, th, (0, 20, 200, 20), "SEL", on=True)
    assert cv.pix(2, 22) == th["title"]
    assert cv.pix(0, 20) == th["accent"]
    ui.row(cv, th, (0, 40, 200, 20), "ARMED", hot=True)
    assert cv.pix(2, 42) == th["danger"]


def test_row_field_none_paints_nothing_and_edge_is_optional():
    """Settings and the system menu paint no field on an unselected row and no
    border at all; a `row` that could not express that would not fit them."""
    th = theme_colors("night")
    cv = SystemCanvas(100, 20)
    cv.cls(31)
    ui.row(cv, th, (0, 0, 100, 20), "", colors=(None, 7, 3), edge=False)
    assert cv.pix(0, 0) == 31 and cv.pix(50, 10) == 31
    ui.row(cv, th, (0, 0, 100, 20), "", colors=(None, 7, 3), edge=True)
    assert cv.pix(0, 0) == 3


def test_row_disabled_dims_and_does_not_register():
    """"dim ink, NON-registering": an unusable row must not be tappable, which
    is exactly what the four hand-rolled disabled sites get wrong today."""
    th = theme_colors("night")
    cv = SystemCanvas(200, 20)
    hits = ui.Hits()
    ui.row(cv, th, (0, 0, 200, 20), "LOCKED", disabled=True,
           hits=hits, verb="row", arg=0)
    assert hits.at(10, 10) is None
    hits.clear()
    ui.row(cv, th, (0, 0, 200, 20), "OPEN", hits=hits, verb="row", arg=0)
    assert hits.at(10, 10) == ("row", 0)


def test_row_truncates_the_label_and_the_value_inside_the_rect():
    """#174's lesson generalized: overflow must CLIP, never escape the rect and
    land ink on whatever is beside it."""
    th = theme_colors("night")
    cv = SystemCanvas(200, 40)
    cv.cls(31)
    ui.row(cv, th, (20, 10, 60, 20), "A VERY LONG ROW LABEL",
           value="ALSO LONG", colors=(None, 7, 3), edge=False)
    for x in range(20):                                # nothing left of the row
        assert cv.pix(x, 20) == 31
    for x in range(80, 200):                           # nothing right of it
        assert cv.pix(x, 20) == 31


def test_row_value_is_right_aligned_and_label_keeps_the_rest():
    th = theme_colors("night")
    cv = SystemCanvas(200, 20)
    cv.cls(31)
    ui.row(cv, th, (0, 0, 200, 20), "WIFI", value="OFF",
           colors=(None, 7, 3), edge=False, pad=4)
    # "OFF" ends one pad short of the right edge: 200 - 4 - 24 = 172.
    assert any(cv.pix(x, 6) == 7 for x in range(172, 196))
    assert cv.pix(198, 10) == 31


def test_row_icon_and_glyph_shift_the_label(monkeypatch):
    """Achievements' leading glyph, Settings' trailing status icon."""
    th = theme_colors("night")
    plain = SystemCanvas(200, 20)
    ui.row(plain, th, (0, 0, 200, 20), "NAME", colors=(None, 7, 3), edge=False)
    withg = SystemCanvas(200, 20)
    ui.row(withg, th, (0, 0, 200, 20), "NAME", glyph="lock",
           glyph_draw=_glyph_draw, colors=(None, 7, 3), edge=False)
    assert bytes(plain._buf) != bytes(withg._buf)
    withi = SystemCanvas(200, 20)
    ui.row(withi, th, (0, 0, 200, 20), "NAME", icon_img=IMG,
           colors=(None, 7, 3), edge=False)
    assert bytes(withi._buf) != bytes(plain._buf)


def test_row_pad_and_text_dy_reproduce_the_frozen_geometries():
    """Settings' literal (4, 5), Files' (4*fs, 6*fs), Storybook's centred
    label -- the three the converting phases must be able to hit exactly."""
    th = theme_colors("night")
    a = SystemCanvas(200, 26)
    ui.row(a, th, (0, 0, 200, 26), "X", colors=(None, 7, 3), edge=False,
           pad=4, text_dy=5)
    b = SystemCanvas(200, 26)
    b.print("X", 4, 5, 7, 1)
    assert bytes(a._buf) == bytes(b._buf)
    c = SystemCanvas(200, 26)
    ui.row(c, th, (0, 0, 200, 26), "X", colors=(None, 7, 3), edge=False, pad=6)
    d = SystemCanvas(200, 26)
    d.print("X", 6, (26 - 8) // 2, 7, 1)
    assert bytes(c._buf) == bytes(d._buf)


# =============================================================================
# 6. the cell kind
# =============================================================================

def test_cell_returns_the_art_rect_and_matches_the_pure_geometry():
    th = theme_colors("night")
    cv = SystemCanvas(200, 120)
    art = ui.cell(cv, th, (0, 0, 76, 66), "name")
    assert art == ui.cell_art_rect((0, 0, 76, 66), 1, 2, 14)
    assert art == (2, 2, 72, 50)                       # FileGridView's numbers
    assert ui.cell_art_rect((0, 0, 76, 66), 1, 3, 17) == (3, 3, 70, 46)
    # A frame-only cell (cards' choice cells): pad 0, no caption.
    assert ui.cell_art_rect((5, 5, 20, 20), 1, 0, 0) == (5, 5, 20, 20)


def test_cell_selected_lifts_the_field_and_the_edge():
    th = theme_colors("night")
    cv = SystemCanvas(200, 120)
    ui.cell(cv, th, (0, 0, 76, 66), "a")
    assert cv.pix(1, 1) == th["panel"] and cv.pix(0, 0) == th["dim"]
    ui.cell(cv, th, (80, 0, 76, 66), "b", on=True)
    assert cv.pix(81, 1) == th["title"] and cv.pix(80, 0) == th["accent"]


def test_cell_band_fills_the_caption_zone():
    """The wallpaper cards' look: a filled strip with a left-aligned title,
    accented when selected. Without a band the label prints on the field."""
    th = theme_colors("night")
    cv = SystemCanvas(200, 120)
    # y = 78 is inside the 17px caption zone (63..79) but below the label row
    # (67..74), so it reads the ZONE, not the glyphs.
    ui.cell(cv, th, (0, 0, 100, 80), "TITLE", band=True, pad=3, caption_h=17)
    assert cv.pix(50, 78) == th["title"]
    ui.cell(cv, th, (100, 0, 100, 80), "TITLE", band=True, on=True, pad=3,
            caption_h=17)
    assert cv.pix(150, 78) == th["accent"]
    plain = SystemCanvas(120, 90)
    ui.cell(plain, th, (0, 0, 100, 80), "TITLE", pad=3, caption_h=17)
    assert plain.pix(50, 78) == th["panel"]             # no band: the field


def test_cell_art_never_covers_the_border_or_the_caption():
    """Draw order is field -> caption -> edge, and the art rect excludes both,
    so a caller's picture cannot land on the frame."""
    th = theme_colors("night")
    cv = SystemCanvas(120, 90)
    ax, ay, aw, ah = ui.cell(cv, th, (0, 0, 100, 80), "T", band=True, pad=3,
                             caption_h=17)
    assert ax >= 3 and ay >= 3
    assert ax + aw <= 100 - 3
    assert ay + ah <= 80 - 17


def test_cell_states_route_through_the_same_model_as_row():
    th = theme_colors("night")
    for state in (ui.HOVER, ui.PRESSED):
        cv = SystemCanvas(60, 60)
        ui.cell(cv, th, (0, 0, 40, 40), None, state=state)
        want = ui.state_colors(th, "cell", state)
        assert cv.pix(0, 0) == want[2]
        assert cv.pix(1, 1) == want[0]


def test_cell_draws_a_centred_glyph_or_icon_in_the_art_rect():
    th = theme_colors("night")
    a = SystemCanvas(60, 60)
    art = ui.cell(a, th, (0, 0, 40, 40), None, glyph="dot",
                  glyph_draw=_glyph_draw, pad=0)
    assert art == (0, 0, 40, 40)
    cx = art[0] + (art[2] - 14) // 2
    assert a.pix(cx + 1, art[1] + (art[3] - 14) // 2 + 1) == \
        ui.state_colors(th, "cell", ui.REST)[1]
    b = SystemCanvas(60, 60)
    ui.cell(b, th, (0, 0, 40, 40), None, icon_img=IMG, pad=0)
    assert bytes(a._buf) != bytes(b._buf)


# =============================================================================
# 5. the widget matrix: every kind in every state, each theme in its shape
# =============================================================================

def _paint_matrix(cv, th):
    """Every style kind in all six states through the widget that draws it
    (row, cell, chip, button), plus a kind the table does not name."""
    own = {
        "tab": lambda r: ui.tab_row(cv, th, (0, r[1], 318, 14),
                                    (("a", "Aa", "gear"), ("b", "Bb", "map")),
                                    "b"),
        "status": lambda r: ui.status_row(cv, th, (0, r[1], 318, 14),
                                          ("Ln 1", "ok")),
        "panel": lambda r: ui.panel(cv, th, (2, r[1] + 1, 150, 12)),
        "panel_title": lambda r: ui.panel(cv, th, (160, r[1] + 1, 150, 12),
                                          title="T"),
        "toolbar": lambda r: ui.toolbar(cv, th, (0, r[1], 318, 14)),
        "scrollbar": lambda r: _matrix_bar(cv, th, r[1]),
        "focus": lambda r: ui.focus_ring(cv, th, (20, r[1] + 4, 100, 6)),
    }
    # The kinds as they were pinned (step 1); a kind added since is pinned by
    # its own test, so it never moves these hashes.
    kinds = sorted(k for k in ui.kinds() if k not in ("toggle",)) + ["nosuchkind"]
    y = 0
    for kind in kinds:
        if kind in own:
            own[kind]((0, y))
            y += 15
            continue
        x = 0
        for st in ui.STATES:
            r = (x, y, 52, 14)
            on, hot, dis = st == ui.ON, st == ui.HOT, st == ui.DISABLED
            interact = st if st in (ui.HOVER, ui.PRESSED) else None
            if kind.startswith("button"):
                bk = {"button": "normal"}.get(kind, kind[7:])
                ui.button(cv, th, r, "B" + kind[:3], kind=bk, on=on,
                          disabled=dis, state=interact)
            elif kind == "chip":
                ui.chip(cv, th, r, "CHIP", on=on, hot=hot, disabled=dis,
                        state=interact)
            elif kind.startswith("cell"):
                ui.cell(cv, th, r, "C", kind=kind, on=on, hot=hot,
                        disabled=dis, state=interact, band=kind == "cell_band")
            else:
                ui.row(cv, th, r, kind, kind=kind, on=on, hot=hot,
                       disabled=dis, state=interact, value="v")
            x += 53
        y += 15


def _matrix_bar(cv, th, y):
    sr = ui.ScrollRegion(horizontal=True)
    sr.set((0, y, 318, 14), 900)
    sr.scroll_by(200)
    sr.draw_bar(cv, th)


# theme/variant sha256[:16] of the index readout at 320x320@1, the theme's
# shape installed.
WIDGET_MATRIX_HASHES = (
    "night/dark 440e0d4ba8d56f75",
    "night/light 240ea7b6696bce9f",
    "indigo/dark 22f5d28af4170731",
    "indigo/light e2e417dbd7288e15",
    "berry/dark b3833b64011aef87",
    "berry/light 1c31c502ae0ce1e8",
    "forest/dark 02c43d5b5648a9b8",
    "forest/light d97d86ae58ebdfc2",
    "slate/dark 7045de95d649a958",
    "slate/light bbdb6e828013e377",
    "machine/dark 442fc9846855fd19",
    "machine/light 1182f776168850c6",
    "platinum/dark e6f6bbf6903e7608",
    "platinum/light 6ed90f275b55f5eb",
    "win95/dark f8e11776201207a6",
    "win95/light d2a1afc5b48a7fd4",
    "outline/dark fdfe3e068520d653",
    "outline/light 0865b7c6f177a4fe",
    "contrast/dark e708f1ba493c81b1",
    "contrast/light e0091b59ccc5358b",
)


def _matrix_hashes():
    out = []
    try:
        for name in THEME_NAMES:
            ui.use_shape(name)
            for variant in THEME_VARIANTS:
                cv = SystemCanvas(320, 320)
                _paint_matrix(cv, theme_colors(name, variant))
                out.append("%s/%s %s" % (
                    name, variant, hashlib.sha256(indices_of(cv)).hexdigest()[:16]))
    finally:
        ui.use_shape(None)
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
    th = theme_colors("machine")
    for st in ui.STATES:
        t, c = ui.state_colors(th, "toggle", st), ui.state_colors(th, "chip", st)
        if st == ui.ON:
            assert t == (th["play"], 7, c[2])
        else:
            assert t == c, st
    a, b = SystemCanvas(80, 24), SystemCanvas(80, 24)
    ui.chip(a, th, (2, 2, 70, 18), "ON", on=True, kind="toggle")
    ui.chip(b, th, (2, 2, 70, 18), "ON", on=True)
    assert bytes(a._buf) != bytes(b._buf)


def test_no_tap_is_lost_past_the_capacity(capsys):
    """An add past the hit buffer's capacity is refused, said ONCE per
    surface, and an error under HITS_DEBUG (docs/kernel_toolkit_2026-10.md
    §3.4): the widget drawn last must never lose its tap silently."""
    hits = ui.Hits(cap=4)
    for i in range(6):
        hits.add((i * 10, 0, 10, 10), "k", i)
    assert len(hits._items) == 4
    assert hits.at(5, 5) == ("k", 0) and hits.at(45, 5) is None
    out = capsys.readouterr().out
    assert out.count("HITS FULL") == 1, out
    assert ui.HITS_CAP == 32
    ui.HITS_DEBUG = True
    try:
        h = ui.Hits(cap=1)
        h.add((0, 0, 1, 1), "a")
        try:
            h.add((0, 0, 1, 1), "b")
        except OverflowError:
            pass
        else:
            raise AssertionError("a full buffer did not raise under HITS_DEBUG")
    finally:
        ui.HITS_DEBUG = False
