"""Phase 4: skins as DATA -- the transcription, the second skin, the budget.

Four claims, in the order they matter:

1. **The default tables ARE the shipped pixels.** `runtime/ui.py`'s widgets no
   longer hardcode a colour or a pad; they read `_SPECS` / `_METRICS`. The
   whole correctness argument is that the 87 shell goldens and the 48 widget
   hashes did not move, which `test_shell_goldens.py` and `test_ui_states.py`
   assert on every run. What is added HERE is the round trip: installing the
   catalog's `"default"` entry renders byte-identically to installing no skin
   at all, on every one of the five golden configurations.

2. **A second skin is pure data.** `skin.use("outline")` restyles the entire
   shell -- every surface, every app, every Editor tab -- and this file is the
   only thing that mentions it. No surface module was edited, and none may be:
   `test_no_surface_module_knows_about_skins` is the ratchet.

3. **A restyle is not a resize.** `"outline"` changes no metric that any
   layout, pure-geometry helper or hit-test reads, so nothing reflows and no
   golden that is not a colour could move. That is asserted slot by slot, not
   claimed in prose.

4. **The resolution path allocates nothing per draw and adds no draw calls.**
   The P4's editor tabs are dispatch-bound (#163), so a per-draw dict get is
   the one place this phase could lose board performance. Both budgets assert a
   FLOOR before a cap, so deleting the probe FAILS rather than passes -- the
   shape `tests/test_top_bar.py::test_bar_strip_rebuild_budget` established.
"""

import ast
import hashlib
from pathlib import Path

import pytest

from runtime import skin, ui
from runtime.chrome import THEMES, THEME_VARIANTS, theme_colors
from runtime.host_canvas import make_system_canvas as SystemCanvas
from runtime.host_canvas import indices_of

from tests import test_shell_goldens as goldens
from tests.test_ui_states import _paint_shipped

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def _restore_default_skin():
    """Every test leaves the default skin installed. `ui`'s skin is module
    state, and a leaked one would restyle whatever ran next in this process."""
    yield
    skin.use(skin.DEFAULT)
    ui.set_skin(None)


@pytest.fixture(scope="module", autouse=True)
def _pin_cover_budget():
    """The golden harness's own fixture, module-scoped so every capture in this
    file is taken under it. `_cover_for` spends a per-frame TIME slice, and the
    budget is load-bearing on more than the shelf: measured, the Settings
    surface at fs=3 hashes differently at a 1ms slice than at an unbounded
    one, so an arm taken without it is not comparable to one taken with it."""
    from runtime import console
    was = console._COVER_SLICE_MS
    console._COVER_SLICE_MS = 10 ** 9
    yield
    console._COVER_SLICE_MS = was


def _ab_captures(config, tmp_path, arms):
    """Render one config once per (name, skin) arm, ADJACENTLY, after a
    throwaway warm-up capture -- and that sequencing is not superstition.

    Measured 2026-08-19: the golden harness carries a first-capture effect.
    Four consecutive captures of `big_800x480_fs3_dark` give
    d1b5.., f4c5.., f4c5.., f4c5.. for the `settings` surface, and rendering
    only up to `settings` gives ZERO differing pixels -- so what moves it is
    process state left behind by the surfaces rendered AFTER it (an app, an
    Editor tab), on the NEXT workstation. `hashes.json` stores the cold value
    for the alphabetically-first config and warm values for the other four,
    which is self-consistent for `test_shell_goldens.py` (one capture per
    config per process) and not something an A/B in one process can rely on.
    Warming up per config, then taking every arm back to back, makes the arms
    differ by the skin and by nothing else. This is a property of the harness,
    not of skins -- see the report."""
    ui.set_skin(None)
    goldens.capture(config, tmp_path / (config + "_warm"))    # discarded
    out = {}
    for name, use in arms:
        if use is None:
            ui.set_skin(None)
        else:
            skin.use(use)
        out[name] = goldens.capture(config, tmp_path / (config + "_" + name))
    ui.set_skin(None)
    return out


# =============================================================================
# 1. the default skin IS the current pixels
# =============================================================================

def test_the_whole_shell_round_trips_the_default_skin_and_restyles_under_a_second(
        tmp_path):
    """The two headline claims, measured on one baseline so they cannot
    disagree about what "unskinned" means.

    ROUND TRIP: `ui.set_skin(None)` and `skin.use("default")` are independent
    expressions of the same look -- the built-in table, and the catalog entry
    that points at it -- and 87 hashes across five configurations say they
    agree, surface for surface.

    RESTYLE: `skin.use("outline")` then repaints essentially every one of those
    surfaces, and this file is the only thing in the repo that mentions it. No
    surface module was edited to make that happen, and
    `test_no_surface_module_knows_about_skins` is the ratchet that keeps it so.

    Verified on the NON-`_base` configurations specifically: at 320x240/1x the
    shell takes frozen hand-rolled branches (`if not ws.layout._base`), so a
    green T-Deck row proves nothing about a widget change."""
    arms = (("base", None), ("default", skin.DEFAULT), ("outline", "outline"))
    checked = 0
    restyled_configs = 0
    for config in sorted(goldens.CONFIGS):
        caps = _ab_captures(config, tmp_path, arms)
        base, dflt, styled = caps["base"], caps["default"], caps["outline"]
        assert dflt == base, (
            "config %r renders differently under skin.use('default') than "
            "under no skin at all -- the catalog's default is not the built-in "
            "tables: %s"
            % (config, sorted(s for s in base if base[s] != dflt.get(s))))
        assert sorted(styled) == sorted(base)
        untouched = sorted(s for s in base if base[s] == styled[s])
        assert len(untouched) <= 1, (
            "config %r: %d surfaces are byte-identical under a different skin "
            "-- a skin that reaches nothing is a skin that is not wired: %s"
            % (config, len(untouched), untouched))
        checked += len(base)
        restyled_configs += 1
    assert checked >= 80 and restyled_configs == len(goldens.CONFIGS), checked


def test_the_shipped_widgets_are_identical_under_the_default_skin():
    """The 48-hash widget sheet, re-rendered with the catalog's default
    installed. Cheaper than the shell goldens and it walks every widget's
    branches, so it is the one to run while transcribing."""
    for name, _tokens in THEMES:
        for variant in THEME_VARIANTS:
            th = theme_colors(name, variant)
            for (w, h, fs) in ((320, 240, 1), (1024, 600, 2)):
                ui.set_skin(None)
                a = SystemCanvas(w, h, font_scale=fs)
                _paint_shipped(a, th)
                skin.use(skin.DEFAULT)
                b = SystemCanvas(w, h, font_scale=fs)
                _paint_shipped(b, th)
                assert bytes(a._buf) == bytes(b._buf), (name, variant, w, h, fs)


def test_the_default_tables_cover_every_kind_the_toolkit_asks_for():
    """A kind a widget looks up but the table does not name would silently
    style as a row. Enumerated from the draw functions themselves."""
    asked = ("row", "cell", "cell_band", "chip", "button", "button_play",
             "button_author", "button_danger", "tab", "status", "panel",
             "panel_title", "toolbar", "scrollbar", "focus")
    for kind in asked:
        assert kind in ui.DEFAULT_SPECS, kind
        assert ui.REST in ui.DEFAULT_SPECS[kind], kind
    for kind in ui.DEFAULT_SPECS:
        assert ui.REST in ui.DEFAULT_SPECS[kind], (
            "%r names no REST entry, so a state it does not name resolves "
            "through the row fallback instead of its own look" % kind)
    metric_kinds = ("button", "chip", "row", "cell", "tab", "status", "panel",
                    "focus", "scrollbar", "text_field", "game_btn",
                    "game_icon_btn", "mini_btn", "dialog")
    for kind in metric_kinds:
        assert kind in ui.DEFAULT_METRICS, kind


def test_every_state_of_every_kind_resolves_on_all_twelve_theme_sets():
    """The restyle promise, over the whole table rather than row/cell alone: a
    skin or theme that names NO state token still resolves to a real palette
    index (or to None, which means 'paint nothing here'), so all 12 family x
    variant sets keep working unmodified under any skin."""
    for name, _tokens in THEMES:
        for variant in THEME_VARIANTS:
            th = theme_colors(name, variant)
            for use in (skin.DEFAULT, "outline"):
                skin.use(use)
                for kind in ui.DEFAULT_SPECS:
                    for state in ui.STATES:
                        trio = ui.state_colors(th, kind, state)
                        assert len(trio) == 3, (use, kind, state)
                        for c in trio:
                            assert c is None or (isinstance(c, int)
                                                 and 0 <= c < 64), (
                                use, kind, state, trio)


def test_the_scrollbar_width_metric_and_the_class_attribute_agree():
    """`ScrollRegion.BAR_W` is public (a caller hit-tests the bar with it) AND
    skin metrics. They must never disagree, in either direction."""
    ui.set_skin(None)
    assert ui.ScrollRegion.BAR_W == ui.DEFAULT_METRICS["scrollbar"][ui.SB_W]
    ui.set_skin(None, None, {"scrollbar": (9, 8)})
    assert ui.ScrollRegion.BAR_W == 9
    r = ui.ScrollRegion()
    r.set((0, 0, 40, 100), 400)
    assert r.bar_rect(1)[2] == 9
    ui.set_skin(None)
    assert ui.ScrollRegion.BAR_W == 4


def test_the_quirks_that_stayed_code_are_documented():
    """Transcription honesty (Section 3.3): not every quirk is data, and the
    ones that are not must say so where the next reader will look."""
    kinds = [k for k, _why in ui.NON_DATA_QUIRKS]
    assert set(kinds) >= {"game_btn", "text_field", "mini_btn"}
    for kind, why in ui.NON_DATA_QUIRKS:
        assert len(why) > 40, kind


# =============================================================================
# 2. the second skin is pure data
# =============================================================================

# The modules allowed to know the catalog exists, and what each is allowed to
# do with it. Everything else in `runtime/` is a SURFACE: it draws through `ui`
# and cannot tell which skin is installed.
#
# This is an exact set, not a floor -- `test_the_skin_wiring_is_exactly_two_modules`
# asserts the tree matches it in both directions, so adding a fourth consumer is
# a deliberate edit here rather than a quiet import somewhere.
_SKIN_OWNERS = {
    # The SETTING's owner: imports the catalog, installs a skin, persists the
    # name, re-applies it at boot. The one place `skin.use` is called.
    "console.py": "installs + persists the skin (Workstation.set_skin)",
    # The PICKER: imports the catalog for its NAME LIST (as it imports
    # chrome.THEMES), and asks the theme role to install one. Never calls
    # `skin.use` itself.
    "appearance_app.py": "lists the catalog; installs via ctx.theme.set_skin",
}

# May NAME the verb (it forwards it) but must not import the catalog: the
# AppContext role an app reaches the setting through.
_SKIN_FORWARDERS = {"app_context.py": "Theme.set_skin -> ws.set_skin"}


def test_no_surface_module_knows_about_skins():
    """The ratchet. `runtime/skin.py` installs itself into `ui`; nothing in the
    surface graph may import it, name a skin or reach into the tables -- the
    moment one does, a skin stops being data and becomes a fork.

    "Nothing" was once literally every module, which made WIRING the catalog a
    red test -- and for a while the wiring simply did not exist: 217 lines with
    zero importers, no way to pick a skin and nothing persisted. The exemption
    is the two modules named in `_SKIN_OWNERS` (plus the role that forwards the
    verb), because a setting needs an owner and a picker; the ratchet itself is
    unchanged for the ~60 surface modules it is actually about."""
    for path in sorted(ROOT.joinpath("runtime").glob("*.py")):
        if path.name in ("skin.py", "ui.py"):
            continue
        src = path.read_text(encoding="utf-8")
        owner = path.name in _SKIN_OWNERS
        tree = ast.parse(src)
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
                names += [a.name for a in node.names]
            for n in names:
                assert owner or n.split(".")[-1] != "skin", (
                    "%s imports the skin catalog. Skins are installed once, "
                    "by whoever owns the setting; a surface only ever draws "
                    "through ui." % path.name)
        if not (owner or path.name in _SKIN_FORWARDERS):
            assert "set_skin" not in src, path.name
        # No module -- owner, forwarder or surface -- may name a SKIN, and none
        # may reach into the tables. A picker offers `skin.names()`; a literal
        # skin name in shell code is the fork this whole phase exists to
        # prevent.
        for name in skin.names():
            if name == skin.DEFAULT:
                continue          # "default" is an ordinary English word
            assert repr(name) not in src and ('"%s"' % name) not in src, (
                "%s names the %r skin" % (path.name, name))
        assert "DEFAULT_SPECS" not in src and "_METRICS[" not in src, path.name


def test_the_skin_wiring_is_exactly_two_modules():
    """The other direction of the ratchet: the exemption list is not a place
    to park modules, so the tree must MATCH it. A named owner that stopped
    importing the catalog is dead wiring (which is what shipped), and an
    unnamed one is the fork."""
    importers = set()
    for path in sorted(ROOT.joinpath("runtime").glob("*.py")):
        if path.name in ("skin.py", "ui.py"):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
                names += [a.name for a in node.names]
            if any(n.split(".")[-1] == "skin" for n in names):
                importers.add(path.name)
    assert importers == set(_SKIN_OWNERS), sorted(importers)
    # And `use` is called from the owner alone.
    users = set()
    for path in sorted(ROOT.joinpath("runtime").glob("*.py")):
        if path.name == "skin.py":
            continue
        src = path.read_text(encoding="utf-8")
        if "_skin.use(" in src or "skin.use(" in src:
            users.add(path.name)
    assert users == {"console.py"}, sorted(users)


def test_skin_is_a_leaf_and_ui_never_imports_it():
    """The cycle `ui -> chrome -> settings_layer -> ui` is why the table is not
    in chrome.py. Checked on import STATEMENTS, both ways."""
    tree = ast.parse(ROOT.joinpath("runtime", "skin.py").read_text("utf-8"))
    imported = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            imported.append(node.module or "")
            imported += [a.name for a in node.names]
    # Both arms of the device/host import ladder: `import ui` on a board,
    # `from runtime import ui` on the host.
    leaves = set()
    for n in imported:
        for part in n.split("."):
            if part:
                leaves.add(part)
    assert leaves <= {"ui", "runtime"}, (
        "skin.py must import nothing but the ui leaf; got %s" % sorted(leaves))
    assert "ui" in leaves
    ui_tree = ast.parse(ROOT.joinpath("runtime", "ui.py").read_text("utf-8"))
    for node in ast.walk(ui_tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            mod = getattr(node, "module", None) or ""
            names = [a.name for a in node.names] + [mod]
            for n in names:
                assert n.split(".")[-1] != "skin", "ui.py imports skin"


def test_use_resolves_unknown_names_and_round_trips():
    assert skin.names() == ("default", "outline")
    assert skin.use("outline") == "outline"
    assert skin.active() == "outline"
    assert skin.use("no-such-skin") == skin.DEFAULT
    assert skin.active() == skin.DEFAULT


# =============================================================================
# 3. a restyle is not a resize
# =============================================================================

# Per kind, the metric slots that SIZE a widget -- the ones a Layout class, a
# pure-geometry helper (`cell_art_rect`, `row_content_rect`, `tab_row_rects`,
# `bar_rect`, `panel`'s content rect) or a hit-test reads. Changing one of
# these is a legitimate skin act and a DELIBERATE re-baseline; changing only
# the others is a restyle, which is what a shipped skin must be.
SIZING_SLOTS = {
    "button":        (ui.BTN_PAD, ui.BTN_ICON, ui.BTN_GLYPH),
    "chip":          (ui.CHIP_CLIP, ui.CHIP_MINX, ui.CHIP_MINY),
    "row":           (ui.ROW_PAD, ui.ROW_ICON, ui.ROW_GLYPH, ui.ROW_GAP),
    "cell":          (ui.CELL_PAD, ui.CELL_CAP, ui.CELL_INDENT, ui.CELL_ICON,
                      ui.CELL_GLYPH),
    "tab":           (ui.TAB_GAP, ui.TAB_PAD, ui.TAB_ICON),
    "status":        (ui.ST_RULE, ui.ST_PAD, ui.ST_GAP, ui.ST_DY),
    "panel":         (ui.PANEL_INSET, ui.PANEL_STRIP, ui.PANEL_PAD),
    "focus":         (ui.FOCUS_GAP,),
    "scrollbar":     (ui.SB_W, ui.SB_MIN),
    "text_field":    (ui.TF_PADX, ui.TF_PADY, ui.TF_CARET_W, ui.TF_CARET_H),
    "game_btn":      (ui.GB_PAD,),
    "game_icon_btn": (ui.GI_PAD, ui.GI_GLYPH, ui.GI_LABEL),
    "mini_btn":      (ui.MB_PADX, ui.MB_PADY),
    "dialog":        (),
}


def test_the_shipped_second_skin_changes_no_sizing_metric():
    """"Outline" restyles within the primitive vocabulary: fills, edge weights,
    alignment, colour. Every number that would reflow a layout is untouched, so
    no geometry test and no non-colour golden could move."""
    spec, metrics = skin.SKINS["outline"]
    assert metrics, "the outline skin has no metrics delta left to check"
    for kind, tup in metrics.items():
        base = ui.DEFAULT_METRICS[kind]
        assert len(tup) == len(base), (
            "%s: a metric tuple must keep its slot count" % kind)
        for slot in SIZING_SLOTS[kind]:
            assert tup[slot] == base[slot], (
                "skin 'outline' changed SIZING slot %d of %r (%r -> %r). That "
                "reflows layouts and re-baselines goldens -- a deliberate "
                "versioned act, not a restyle." % (slot, kind, base[slot],
                                                   tup[slot]))
    assert spec, "the outline skin has no state deltas"


def test_every_skin_in_the_catalog_keeps_the_tuple_shapes():
    """A short metric tuple is an IndexError inside a draw, on device, at the
    worst possible moment. Length is checked here for every catalog entry."""
    for name in skin.names():
        specs, metrics = skin.SKINS[name]
        for kind, tup in (metrics or {}).items():
            assert kind in ui.DEFAULT_METRICS, (name, kind)
            assert len(tup) == len(ui.DEFAULT_METRICS[kind]), (name, kind)
            for v in tup:
                assert isinstance(v, int), (name, kind, v)
        for kind, states in (specs or {}).items():
            assert kind in ui.DEFAULT_SPECS, (name, kind)
            assert ui.REST in states, (name, kind)
            for state, trio in states.items():
                assert state in ui.STATES, (name, kind, state)
                assert len(trio) == 3, (name, kind, state)


def test_a_partial_skin_is_a_delta_over_the_defaults():
    """A skin names only what it changes; every other kind and metric keeps the
    default, so a two-line skin cannot leave a widget unresolvable."""
    th = theme_colors("night")
    ui.set_skin(None, {"chip": {ui.REST: (5, 6, 7)}}, {"row": (9, 16, 14, 1, 1)})
    assert ui.state_colors(th, "chip", ui.REST) == (5, 6, 7)
    assert ui.state_colors(th, "row", ui.REST) == (
        th["panel"], th["title_ink"], th["dim"])
    assert ui.metrics("row")[ui.ROW_PAD] == 9
    assert ui.metrics("cell") == ui.DEFAULT_METRICS["cell"]


# =============================================================================
# 4. the budget: zero per-draw allocation, no extra draw calls
# =============================================================================

class _CountingCanvas:
    """A real system canvas with counted draw verbs. Counts DISPATCH, which is
    what the P4's editor tabs are bound by -- not pixels."""

    def __init__(self, w=320, h=240, fs=1):
        self._cv = SystemCanvas(w, h, font_scale=fs)
        self.font_scale = fs
        self.ops = 0

    def rect(self, *a):
        self.ops += 1
        self._cv.rect(*a)

    def rectb(self, *a):
        self.ops += 1
        self._cv.rectb(*a)

    def print(self, *a):
        self.ops += 1
        self._cv.print(*a)

    def spr(self, *a):
        self.ops += 1
        self._cv.spr(*a)


def _draw_many(cv, th, n=100):
    """A widget storm across every kind that resolves through the skin."""
    for i in range(n):
        state = ui.STATES[i % len(ui.STATES)]
        ui.row(cv, th, (0, 0, 200, 16), "Row", state=state, value="v")
        ui.cell(cv, th, (0, 20, 60, 60), "C", state=state)
        ui.chip(cv, th, (0, 90, 60, 18), "Chip", on=(i % 2 == 0))
        ui.button(cv, th, (0, 110, 90, 20), "Go", kind="play")
        ui.panel(cv, th, (0, 140, 80, 60), title="T")
        ui.status_row(cv, th, (0, 210, 200, 14), ("a", "b"))


def test_the_skin_path_allocates_nothing_per_draw(monkeypatch):
    """Zero per-draw allocation, expressed as OBJECT IDENTITY.

    A transient tuple is not observable in CPython (tracemalloc reports live
    blocks; gc counts are net of frees), so the measurable form of "the draw
    did not build a tuple" is that the tuple it handed back is the SHARED one
    the flatten built. 600 widget draws must yield a handful of distinct
    objects; a resolver that rebuilds per call yields 600.

    Floor first: without the length assertion this test passes when the probe
    is gone, which is how a regression net quietly stops being one."""
    th = theme_colors("night")
    flattens = []
    real_flatten = ui._flatten_kind

    def counted_flatten(t, kind):
        flattens.append(kind)
        return real_flatten(t, kind)

    handed = []
    real_colors = ui.state_colors

    def counted_colors(t, kind, state):
        out = real_colors(t, kind, state)
        handed.append(out)
        return out

    monkeypatch.setattr(ui, "_flatten_kind", counted_flatten)
    monkeypatch.setattr(ui, "state_colors", counted_colors)

    ui.set_skin(None)
    cv = _CountingCanvas()
    _draw_many(cv, th, 100)

    assert len(handed) >= 600, (
        "only %d resolutions counted -- is the probe still wired to "
        "ui.state_colors?" % len(handed))
    assert len(flattens) >= 1, "no flatten counted -- is _flatten_kind wired?"
    # One flatten per KIND for this theme, and never again: the identity fast
    # path (`th is _FLAT_TH`) is what keeps a per-draw resolve off the chain.
    assert len(flattens) == len(set(flattens)) <= 10, flattens
    # ZERO ALLOCATION: every triple is one of the flattened, shared tuples.
    distinct = len(set(id(t) for t in handed))
    assert distinct <= 24, (
        "%d distinct colour tuples for %d resolutions -- the resolution path "
        "is building one per draw" % (distinct, len(handed)))
    # The metrics tuples are shared too: `metrics()` is a dict get, never a
    # tuple built from the theme.
    assert ui.metrics("row") is ui.metrics("row")
    assert ui.metrics("row") is ui.DEFAULT_METRICS["row"]


def test_a_theme_switch_reflattens_once_and_then_stays_flat(monkeypatch):
    """The fast path is an identity compare, so alternating themes must not
    thrash: each theme pays one flatten per kind it draws, once."""
    flattens = []
    real_flatten = ui._flatten_kind
    monkeypatch.setattr(
        ui, "_flatten_kind",
        lambda t, k: (flattens.append(k), real_flatten(t, k))[1])
    ui.set_skin(None)
    a, b = theme_colors("night"), theme_colors("berry")
    cv = _CountingCanvas()
    for _ in range(20):
        ui.row(cv, a, (0, 0, 100, 16), "x")
    n_after_a = len(flattens)
    assert n_after_a >= 1, "no flatten counted -- is the probe wired?"
    assert n_after_a == 1, flattens
    for _ in range(20):
        ui.row(cv, b, (0, 0, 100, 16), "x")
    assert len(flattens) == 2, flattens
    # And back: a theme the ring still holds costs NO re-flatten. This is what
    # keeps the `self.theme or {}` sites (an empty dict is a fresh object every
    # call) from wiping the live theme's flatten once per frame.
    for _ in range(20):
        ui.row(cv, a, (0, 0, 100, 16), "x")
        ui.row(cv, {}, (0, 0, 100, 16), "x")       # a throwaway theme
    assert len(flattens) == 2 + 20, flattens


def test_the_default_skin_adds_no_draw_calls():
    """Dispatch budget. Installing the catalog's default must cost exactly the
    same number of canvas verbs as installing no skin at all -- the P4's editor
    tabs are dispatch-bound, so an extra rectb per widget is a real regression
    even when every pixel matches."""
    th = theme_colors("night")
    ui.set_skin(None)
    plain = _CountingCanvas()
    _draw_many(plain, th, 40)
    skin.use(skin.DEFAULT)
    dflt = _CountingCanvas()
    _draw_many(dflt, th, 40)
    assert plain.ops >= 400, (
        "only %d canvas ops counted -- is _CountingCanvas still forwarding?"
        % plain.ops)
    assert dflt.ops == plain.ops, (dflt.ops, plain.ops)
    assert bytes(dflt._cv._buf) == bytes(plain._cv._buf)


def test_a_restyle_stays_within_a_dispatch_budget():
    """A skin may add edge rings; it may not multiply a surface's draw cost.
    Bounded at 1.5x the default -- enough for the two-pixel frames "outline"
    draws, nowhere near enough to hide a per-widget loop."""
    th = theme_colors("night")
    ui.set_skin(None)
    plain = _CountingCanvas()
    _draw_many(plain, th, 40)
    skin.use("outline")
    styled = _CountingCanvas()
    _draw_many(styled, th, 40)
    assert plain.ops >= 400 and styled.ops >= 400
    assert styled.ops <= plain.ops * 3 // 2, (styled.ops, plain.ops)


def test_the_second_skin_draws_a_real_frame_on_every_theme():
    """A restyle must not resolve to nothing. Rendered on all 12 theme sets:
    the outline skin's canvases differ from the default's, and never come out
    blank."""
    for name, _tokens in THEMES:
        for variant in THEME_VARIANTS:
            th = theme_colors(name, variant)
            ui.set_skin(None)
            a = SystemCanvas(320, 240, font_scale=1)
            _paint_shipped(a, th)
            skin.use("outline")
            b = SystemCanvas(320, 240, font_scale=1)
            _paint_shipped(b, th)
            assert bytes(a._buf) != bytes(b._buf), (name, variant)
            assert len(set(indices_of(b))) > 3, (name, variant)
            assert hashlib.sha256(indices_of(b)).digest() != \
                hashlib.sha256(indices_of(a)).digest()


# =============================================================================
# 5. the wiring: an owner, a persisted setting, a picker
# =============================================================================
#
# Phase 4 shipped the catalog with NO importers -- 217 lines, no `set_skin`
# call anywhere, no UI, nothing stored -- so "a skin is data" was true and
# unreachable. These pin the three pieces that make it a setting.

def test_the_workstation_owns_the_skin_and_re_installs_it_at_boot(tmp_path):
    """`ws.set_skin` is the ONE install (`skin.use`) + the persistence, and
    `load_system` re-applies it -- the same two lines `set_theme_variant` has.
    Asserted through `ui` as well as through the store: storing the name and
    forgetting to install it would look identical from the settings dict."""
    from runtime import host_app, moy_carts
    th = theme_colors("night")
    carts = str(tmp_path / "carts")
    ws = host_app.build_workstation(carts)
    assert ws.skin_name == skin.DEFAULT
    plain = ui.state_colors(th, "row", ui.REST)

    ws.set_skin("outline")
    assert ws.skin_name == "outline" and skin.active() == "outline"
    assert ui.state_colors(th, "row", ui.REST) != plain, "ui was not restyled"
    assert moy_carts.load_system(carts)["skin"] == "outline"

    # A fresh boot over the same store re-installs it.
    skin.use(skin.DEFAULT)
    assert ui.state_colors(th, "row", ui.REST) == plain
    ws2 = host_app.build_workstation(carts)
    assert ws2.skin_name == "outline"
    assert ui.state_colors(th, "row", ui.REST) != plain, "boot did not apply"

    # An unknown name resolves to the default and STORES the resolved value,
    # so a store naming a skin this build dropped heals on the next pick.
    ws2.set_skin("no-such-skin")
    assert ws2.skin_name == skin.DEFAULT
    assert ui.state_colors(th, "row", ui.REST) == plain
    assert moy_carts.load_system(carts)["skin"] == skin.DEFAULT


def test_a_store_that_names_no_skin_installs_nothing(tmp_path):
    """`ui`'s own tables ARE the default, so an absent key means "nothing to
    install" -- not "assert the default over whatever this process has". On a
    board the two readings coincide; on a host that builds several
    workstations in one process (this file, the sim's A/B) only the first is
    true."""
    from runtime import host_app
    skin.use("outline")
    ws = host_app.build_workstation(str(tmp_path / "carts"))
    assert skin.active() == "outline"
    assert ws.skin_name == "outline"


def test_the_appearance_app_is_the_picker(tmp_path):
    """The THEMES tab carries the chips, beside DARK/LIGHT: same tab, same
    band shape, and a tap installs + persists through `ctx.theme.set_skin`
    (the app never calls `skin.use`)."""
    from runtime import host_app, moy_carts
    from tests.test_appearance_app import _open_appearance
    carts = str(tmp_path / "carts")
    ws = host_app.build_workstation(carts)
    app = _open_appearance(ws)
    app._set_mode("themes")

    chips = app._skin_chip_rects()
    assert [n for n, _r in chips] == list(skin.names())
    rect = dict(chips)["outline"]
    x, y, w, h = rect
    app.handle_pointer(x + w // 2, y + h // 2, True)
    assert ws.skin_name == "outline"
    assert skin.active() == "outline"
    assert moy_carts.load_system(carts)["skin"] == "outline"
    assert "OUTLINE" in app.status

    # ...and the tab draws under it, chips included (the pick must not be a
    # state change nothing renders).
    ws.input.begin_frame()
    ws.frame(1 / 30)
    before = bytes(ws.sys_canvas._buf)
    app.handle_pointer(*_center(dict(chips)[skin.DEFAULT]), click=True)
    ws._dirty = True
    ws.input.begin_frame()
    ws.frame(1 / 30)
    assert bytes(ws.sys_canvas._buf) != before, "the skin swap painted nothing"


def _center(rect):
    x, y, w, h = rect
    return (x + w // 2, y + h // 2)


def test_the_skin_chips_fit_every_tier(tmp_path):
    """Geometry, on the golden matrix's five configurations: the chips sit
    inside the preview field, never overlap the DARK/LIGHT band above them,
    and are wide enough for the catalog's longest name at font scale 1-2.

    The band exists because they did NOT fit beside the variant chips: 70px of
    room at 320x240 clips "DEFAULT" to four characters."""
    from runtime import host_app
    from tests.test_appearance_app import _open_appearance
    longest = max(len(n) for n in skin.names())
    for config in sorted(goldens.CONFIGS):
        cfg = goldens.CONFIGS[config]
        ws = host_app.build_workstation(
            str(tmp_path / ("carts_" + config)), sys_size=cfg["sys_size"],
            font_scale=cfg["font_scale"], windowed=cfg["windowed"])
        app = _open_appearance(ws)
        app._set_mode("themes")
        fx, fy, fw, fh = app.layout.field
        fs = app.layout.fs
        below = fy + 17 * fs                     # the variant band's bottom
        for name, (x, y, w, h) in app._skin_chip_rects():
            assert x >= fx and x + w <= fx + fw, (config, name)
            assert y >= below and y + h <= fy + fh, (config, name)
            if fs <= 2:
                assert w >= (longest + 1) * 8 * fs, (config, name, w)
