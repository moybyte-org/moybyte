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
from runtime.chrome import THEMES, THEME_VARIANTS, theme_colors
from runtime.host_canvas import make_system_canvas as SystemCanvas
from runtime.host_canvas import indices_of


TH = theme_colors("machine")


# --- test doubles --------------------------------------------------------------

class _Ptr:
    """The three fields the pump duck-types off widgets.Pointer. `hovering` is
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
    "night/dark 320x240@1 f8108d19044793e1",
    "night/dark 480x320@1 a1ac33fe1201673a",
    "night/dark 1024x600@2 f35bb9aa13bc678a",
    "night/dark 320x240@3 1ad7d2949cf72343",
    "night/light 320x240@1 aaba6f3b32d40464",
    "night/light 480x320@1 ee8eafacd28ebd44",
    "night/light 1024x600@2 0782647e7ae9de1e",
    "night/light 320x240@3 b6a942f24118f1b7",
    "indigo/dark 320x240@1 72962530ccb605ba",
    "indigo/dark 480x320@1 e3b77268f8608596",
    "indigo/dark 1024x600@2 ac228d08e7b080e1",
    "indigo/dark 320x240@3 8db6e103afb6d0de",
    "indigo/light 320x240@1 be3f3a308dd510f2",
    "indigo/light 480x320@1 037c229b72141e14",
    "indigo/light 1024x600@2 b0d918d77e41b586",
    "indigo/light 320x240@3 8033ce97f42f7f61",
    "berry/dark 320x240@1 190e7efeb5e3c0fc",
    "berry/dark 480x320@1 da71cb285c9c318c",
    "berry/dark 1024x600@2 0708319d240dc470",
    "berry/dark 320x240@3 802cbcd8bbcd57e9",
    "berry/light 320x240@1 2103cf71543ec6e3",
    "berry/light 480x320@1 073838a618fc7f2c",
    "berry/light 1024x600@2 451376eb0b4c2153",
    "berry/light 320x240@3 7bce28740b8eb807",
    "forest/dark 320x240@1 85302653d81b6d95",
    "forest/dark 480x320@1 0c8c29726383420e",
    "forest/dark 1024x600@2 651ad5dcbf62b697",
    "forest/dark 320x240@3 64e593bb9f584958",
    "forest/light 320x240@1 2a18a81f55f951f4",
    "forest/light 480x320@1 71479964c0535242",
    "forest/light 1024x600@2 13367de583abfc13",
    "forest/light 320x240@3 37450731fc28bbf9",
    "slate/dark 320x240@1 83abe25993cbf820",
    "slate/dark 480x320@1 bf551ff15d7e2232",
    "slate/dark 1024x600@2 3aaf24928583b0b9",
    "slate/dark 320x240@3 526a785bf8c315a3",
    "slate/light 320x240@1 ed8efaefb14a47bd",
    "slate/light 480x320@1 eec11a96c80a9c14",
    "slate/light 1024x600@2 65698a96e51c3da8",
    "slate/light 320x240@3 629cb63fc97e94c3",
    "machine/dark 320x240@1 4d1852879278cced",
    "machine/dark 480x320@1 91f89748658bc962",
    "machine/dark 1024x600@2 387c2a580e7fca1c",
    "machine/dark 320x240@3 86e72a17d54d8c44",
    "machine/light 320x240@1 fc0a1b294a31c829",
    "machine/light 480x320@1 1837c59cdab0f52e",
    "machine/light 1024x600@2 f9f24b590dd2c874",
    "machine/light 320x240@3 3a0d204690518100",
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
    for name, _tokens in THEMES:
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
    """The restyle promise: a skin/theme that names NO state token still paints
    a correct cue, so all 12 family x variant sets keep working unmodified."""
    for name, _tokens in THEMES:
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


def test_state_token_walks_the_alias_chain():
    th = {"dim": 42, "accent": 9, "hilite": 5, "ink": 7}
    assert ui.state_token(th, "hover") == 42            # hover -> dim
    assert ui.state_token(th, "hover_cue") == 9         # -> focus -> accent
    assert ui.state_token(th, "pressed") == 5           # -> selection -> hilite
    assert ui.state_token(th, "pressed_ink") == 7       # -> selection_ink -> ink
    assert ui.state_token(th, "nope", 3) == 3           # unknown -> fallback
    # A theme that DOES name the token wins over the chain.
    assert ui.state_token({"hover": 1, "dim": 42}, "hover") == 1
    # Index 0 (black) is a legitimate value, not a miss.
    assert ui.state_token({"hover": 0}, "hover", 9) == 0


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


# -- the skin seam --------------------------------------------------------------

def test_set_skin_intercepts_every_kind_and_state_and_can_fall_through():
    """Where Phase 4 plugs in. `runtime/skin.py` will install a closure over its
    pre-flattened nested SKIN[kind][state] tables; ui.py must never import it."""
    seen = []

    def skin(th, kind, state):
        seen.append((kind, state))
        if state == ui.REST:
            return None                       # fall through to the default
        return (1, 2, 3)

    th = theme_colors("night")
    default_rest = ui.state_colors(th, "row", ui.REST)
    ui.set_skin(skin)
    try:
        assert ui.state_colors(th, "row", ui.PRESSED) == (1, 2, 3)
        assert ui.state_colors(th, "cell", ui.HOVER) == (1, 2, 3)
        assert ui.state_colors(th, "row", ui.REST) == default_rest
        # The widgets route through the same seam, so a skin restyles them.
        cv = SystemCanvas(60, 20)
        ui.row(cv, th, (0, 0, 60, 20), "X", on=True)
        assert cv.pix(1, 1) == 1
    finally:
        ui.set_skin(None)
    assert ("row", ui.PRESSED) in seen and ("cell", ui.HOVER) in seen
    assert ui.state_colors(th, "row", ui.PRESSED) != (1, 2, 3)


def test_ui_stays_a_leaf():
    """The cycle ui -> chrome -> settings_layer -> ui must stay impossible: the
    skin installs itself into ui, ui never reaches for it. Checked on IMPORT
    STATEMENTS, not on substrings -- the module documents the cycle in prose."""
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
        assert leaf not in ("chrome", "console", "skin", "settings_layer",
                            "wm_windowed"), name
    # The ONE import is the peer leaf that owns the single hit-test.
    assert set(n.split(".")[-1] for n in names) == {"widgets"}, names


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
    """widgets.Pointer has no `hovering` today; the browser/mouse tier will."""
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
