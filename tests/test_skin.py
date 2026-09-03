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

5. **The `colors=` escape hatch carries frozen pixels and nothing else** (#207).
   Section 6 pins every surviving site by module, by count and by reason, in
   both directions -- and pins the other half too, the kinds a surface asks the
   catalog for, so a typo'd kind cannot silently fall back to the row palette.
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
    from runtime import cover_cache
    was = cover_cache._COVER_SLICE_MS
    cover_cache._COVER_SLICE_MS = 10 ** 9
    yield
    cover_cache._COVER_SLICE_MS = was


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
             "panel_title", "toolbar", "scrollbar", "focus",
             # the shell's own row kinds, asked for through `row(kind=...)`
             "row_menu", "row_list", "row_chrome", "row_cta")
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
    # It was console.py until #209 landing D moved the whole look cluster --
    # theme, variant, skin, font scale, wallpaper, icon sheet -- onto ws.look.
    "appearance.py": "installs + persists the skin (Appearance.set_skin)",
    # The PICKER: imports the catalog for its NAME LIST (as it imports
    # chrome.THEMES), and asks the theme role to install one. Never calls
    # `skin.use` itself.
    "appearance_app.py": "lists the catalog; installs via ctx.theme.set_skin",
}

# May NAME the verb (they call it) but must not import the catalog: the
# AppContext role an app reaches the setting through, and the kernel's boot
# cascade, which re-applies the stored name through the owner.
_SKIN_FORWARDERS = {
    "app_context.py": "Theme.set_skin -> ws.look.set_skin",
    "console.py": "load_system's apply cascade -> ws.look.set_skin",
}


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
    assert users == {"appearance.py"}, sorted(users)


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
    """`ws.look.set_skin` is the ONE install (`skin.use`) + the persistence, and
    `load_system` re-applies it -- the same two lines `set_theme_variant` has.
    Asserted through `ui` as well as through the store: storing the name and
    forgetting to install it would look identical from the settings dict."""
    from runtime import host_app, moy_carts
    th = theme_colors("night")
    carts = str(tmp_path / "carts")
    ws = host_app.build_workstation(carts)
    assert ws.look.skin_name == skin.DEFAULT
    plain = ui.state_colors(th, "row", ui.REST)

    ws.look.set_skin("outline")
    assert ws.look.skin_name == "outline" and skin.active() == "outline"
    assert ui.state_colors(th, "row", ui.REST) != plain, "ui was not restyled"
    assert moy_carts.load_system(carts)["skin"] == "outline"

    # A fresh boot over the same store re-installs it.
    skin.use(skin.DEFAULT)
    assert ui.state_colors(th, "row", ui.REST) == plain
    ws2 = host_app.build_workstation(carts)
    assert ws2.look.skin_name == "outline"
    assert ui.state_colors(th, "row", ui.REST) != plain, "boot did not apply"

    # An unknown name resolves to the default and STORES the resolved value,
    # so a store naming a skin this build dropped heals on the next pick.
    ws2.look.set_skin("no-such-skin")
    assert ws2.look.skin_name == skin.DEFAULT
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
    assert ws.look.skin_name == "outline"


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
    assert ws.look.skin_name == "outline"
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


# =============================================================================
# 6. the `colors=` escape hatch, and the kinds a surface asks for (#207)
# =============================================================================
#
# `ui.row` / `ui.cell` / `ui.chip` / `ui.text_field` all take an explicit
# (field, ink, edge) triple that bypasses this catalog entirely. It exists for a
# real case -- `ui.row`'s own docstring: "a site whose pixels are frozen
# off-token" -- and #207 is the triage that separated those from the sites that
# were merely hand-resolving a role the catalog could own. Phase 4 shipped with
# 27 uses of it and no way to tell the two apart; the ones that were a widget
# STATE PALETTE now resolve through `ui.state_colors` against a kind, and what
# is left is pinned below.
#
# The classifier reads what reaches the `colors=` argument of a call. A
# hand-built triple is a bypass and is counted; an explicit `None` is not (it is
# the default, and it is how a branched site spells "use the kind"). A site that
# computes in branches has each BRANCH classified, which is why two modules
# appear both here and in the kind-naming floor below.

# The shell's own row kinds -- `ui.DEFAULT_SPECS`'s entries beyond the toolkit's
# own vocabulary, added by #207. Named here rather than derived, so that adding
# one to `ui.py` without a consumer, a transcription row and an "outline" delta
# is a red test in three places.
_SHELL_KINDS = ("row_menu", "row_list", "row_chrome", "row_cta")

_FROZEN_HATCH = {
    "settings_layer.py": (3,
        "the CONNECTED line's `play` and the password prompt's `ink` are status "
        "and body roles the theme owns, not widget states; the 'soon' second "
        "line's grey is a literal that matches no token on any theme"),
    "cards_layer.py": (3,
        "the bad-card warning frame, and the two choice-cell palettes: a chosen "
        "swatch is literal black + literal yellow (which is NOT `accent` on "
        "slate), and the bg-thumb cell must paint NO field because its picture "
        "is drawn BEFORE the frame"),
    "code_layer.py": (2,
        "the popup shell's ink is the FIND colour -- no widget state; the popup "
        "ENTRIES must paint no field when unselected, because their rects tile "
        "the panel edge to edge and any kind's rest field would erase the "
        "shell's own border a row at a time"),
    "achievements_ui.py": (1,
        "the Easter-egg banner: literal white on a literal black dialog, a "
        "surprise no theme and no skin colours"),
    "update_ui.py": (1,
        "one line per update PHASE, inked by what is happening (downloading / "
        "done / failed) -- a status colour, not a state of the widget"),
    "system_menu_ui.py": (1,
        "the popup's section HEADER: its dark-chrome grey is a literal, and "
        "only the light branch reads a role"),
    "storybook_app.py": (1,
        "a deck row is cream paper with black ink -- frozen off-token, the "
        "case the hatch is documented for"),
    "sheets_app.py": (1,
        "the attach list's rows, same cream paper; only the edge is themed"),
    "music_editor_ui.py": (1,
        "the title-strip nudge ticks: a frozen blue/black/white trio with no "
        "token behind any of the three"),
}


def _is_catalog_call(node):
    return (isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "state_colors")


def _bindings(target, value):
    """(name, expression) pairs one assignment binds -- tuple unpacking
    included, because `kind, colors = "row", (7, 0, edge)` is how a branched
    site is written and missing it under-counts the hatch by one."""
    if isinstance(target, ast.Name):
        return [(target.id, value)]
    if isinstance(target, ast.Tuple) and isinstance(value, ast.Tuple) and \
            len(target.elts) == len(value.elts):
        out = []
        for t, v in zip(target.elts, value.elts):
            out += _bindings(t, v)
        return out
    return []


def _reaching(tree, argname):
    """Every expression that can reach the keyword `argname` of some call in
    this module, resolving a Name through the assignments that bind it. Deduped
    by source position, so a helper walked twice is one site."""
    via_name = set()
    found = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            for kw in node.keywords:
                if kw.arg != argname:
                    continue
                if isinstance(kw.value, ast.Name):
                    via_name.add(kw.value.id)      # branched, resolved below
                else:
                    found[(kw.value.lineno, kw.value.col_offset)] = kw.value
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for t in node.targets:
                for name, val in _bindings(t, node.value):
                    if name in via_name:
                        found[(val.lineno, val.col_offset)] = val
    return [found[k] for k in sorted(found)]


def _is_none(node):
    return isinstance(node, ast.Constant) and node.value is None


def _hatch_sites(path):
    """The hand-built triples reaching a `colors=` argument, as line numbers.

    An explicit `None` is not a hatch use -- it IS the default, and a branched
    site that names a kind on one arm spells "use the kind" that way."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return sorted(n.lineno for n in _reaching(tree, "colors")
                  if not _is_none(n) and not _is_catalog_call(n))


def _named_kinds(path):
    """The SHELL kinds this module asks a widget for by name. `ui.button`'s own
    `kind=` vocabulary ("play", "normal", ...) is not in the set, so it does not
    count here."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return sorted(n.value for n in _reaching(tree, "kind")
                  if isinstance(n, ast.Constant) and n.value in _SHELL_KINDS)


def test_the_colors_hatch_is_pinned_site_by_site():
    """The ratchet, in BOTH directions: a module may not grow a hand-built
    triple, and a module that stops needing one must be taken off the list.

    Pinned by count rather than by line, because line numbers churn on every
    edit above them and a ratchet nobody can keep green stops being run. The
    REASON is the load-bearing half: a new entry here is a claim that some
    pixels are frozen off-token, and it has to be written down to be argued
    with."""
    total = 0
    for path in sorted(ROOT.joinpath("runtime").glob("*.py")):
        if path.name == "ui.py":            # where the parameter is DEFINED
            continue
        frozen = _hatch_sites(path)
        want, why = _FROZEN_HATCH.get(path.name, (0, ""))
        assert len(frozen) == want, (
            "%s: %d hand-built `colors=` triple(s) at lines %s, expected %d.\n"
            "Adding one means claiming those pixels are frozen off-token -- say "
            "so in _FROZEN_HATCH. Removing one means it now resolves through "
            "the skin: drop the count.\nrecorded reason: %s"
            % (path.name, len(frozen), frozen, want, why or "(none)"))
        total += len(frozen)
    assert total == sum(n for n, _why in _FROZEN_HATCH.values())
    for name, (_n, why) in _FROZEN_HATCH.items():
        assert len(why) > 40, name


def test_the_hatch_that_moved_now_names_a_kind_instead():
    """The other half of the count, and the reason it cannot pass vacuously: the
    ten sites #207 moved did not merely lose a literal, they NAME a catalog
    kind. Deleting the moves shrinks the hatch count above and fails here.

    This floor counts the shape the sites have TODAY (`row(kind="row_menu")`).
    They spent one landing passing `state_colors(...)` through `colors=` instead,
    because `row`/`cell` had no `kind=`; giving them one is what turned each of
    these call sites into one word."""
    named = {}
    for path in sorted(ROOT.joinpath("runtime").glob("*.py")):
        if path.name == "ui.py":
            continue
        k = _named_kinds(path)
        if k:
            named[path.name] = len(k)
    assert named == {
        "achievements_ui.py": 1,        # the achievements list
        "cards_layer.py": 1,            # the Config tab's cards
        "settings_layer.py": 6,         # the rows, the wifi list, the notes
        "storybook_app.py": 1,          # the + NEW row
        "system_menu_ui.py": 1,         # the popup's rows
    }, named
    assert sum(named.values()) == 10


def test_every_kind_a_surface_asks_the_catalog_for_exists():
    """A kind the table does not name resolves through `_state_colors_slow`'s
    ROW fallback -- silently, and with the wrong pixels. So the kind names in
    the tree are checked against the table, which is the whole reason a surface
    may name a kind but never import this module.

    A widget's `kind=` argument and a direct `state_colors` call are the same
    question asked two ways, so both shapes are collected. `ui.py` is skipped
    because it is the toolkit ASKING, not a surface: its own draw functions map
    a caller's `kind=` through `_BUTTON_KIND_KEY`, computed by construction."""
    asked = {}
    for path in sorted(ROOT.joinpath("runtime").glob("*.py")):
        if path.name == "ui.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in _reaching(tree, "kind"):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                asked.setdefault(node.value, []).append(path.name)
        for node in ast.walk(tree):
            if _is_catalog_call(node) and len(node.args) >= 2:
                kind = node.args[1]
                assert isinstance(kind, ast.Constant) and \
                    isinstance(kind.value, str), (
                        "%s:%d asks for a computed kind -- a typo in it is a "
                        "silent fallback to the row palette"
                        % (path.name, node.lineno))
                asked.setdefault(kind.value, []).append(path.name)
    assert asked, "no surface names a kind -- is the probe still wired?"
    for kind in asked:
        # `button`'s own vocabulary is a NAME MAP, not a catalog key; every
        # other kind a surface names must be in the table itself.
        if kind in ui._BUTTON_KIND_KEY:
            continue
        assert kind in ui.DEFAULT_SPECS, (kind, asked[kind])
    # And every shell kind has a consumer: a table entry nothing asks for is a
    # decision pretending to be one.
    for kind in _SHELL_KINDS:
        assert kind in ui.DEFAULT_SPECS, kind
        assert kind in asked, "%r is in the table but nothing asks for it" % kind


def test_the_second_skin_reaches_the_kinds_that_moved():
    """The point of the move, stated as the property it restores: every shell
    kind repaints under a different skin, on all 12 theme x variant sets. A kind
    the outline skin forgot would resolve identically and this fails."""
    for kind in sorted(_SHELL_KINDS):
        moved = 0
        for name, _tokens in THEMES:
            for variant in THEME_VARIANTS:
                th = theme_colors(name, variant)
                for state in (ui.REST, ui.ON, ui.DISABLED):
                    skin.use(skin.DEFAULT)
                    a = ui.state_colors(th, kind, state)
                    skin.use("outline")
                    b = ui.state_colors(th, kind, state)
                    moved += (tuple(a) != tuple(b))
        assert moved >= 12, (kind, moved)


# The expression each moved site built by hand at HEAD (`3b22d5a`), as a lambda
# over one theme dict. This is the transcription pin: `_quiet_row(...)` and its
# siblings must resolve to EXACTLY these triples on all 12 theme x variant sets,
# which is what makes the move a no-op rather than a re-baseline.
#
# It is not redundant with the goldens, and the gap is measured rather than
# assumed: mutating `row_cta`'s REST triple leaves BOTH golden suites green,
# because Storybook's shelf always opens with the "+ NEW" row selected, so that
# row is never rendered at rest. Every other state below IS netted by a golden
# (verified by mutation: `row_menu` REST turns all ten golden rows red).
_PRE_207_TRIPLES = {
    ("row_menu", ui.REST):  # settings_layer._draw_settings_row, system_menu_ui
        lambda th: (None, th["chrome_ink_dim"], None),
    ("row_menu", ui.ON):
        lambda th: (th["hilite"], th["selection_ink"], None),
    ("row_list", ui.REST):  # the wifi list + the wifi/bluetooth note lines
        lambda th: (None, th["ink_dim"], None),
    ("row_list", ui.ON):
        lambda th: (th["hilite"], th["selection_ink"], None),
    ("row_chrome", ui.REST):  # achievements_ui (earned), cards_layer (a card)
        lambda th: (None, th["ink"] if th.get("bar_light", False)
                    else th["chrome_ink"], None),
    ("row_chrome", ui.ON):
        lambda th: (th["hilite"], th["selection_ink"], None),
    ("row_chrome", ui.DISABLED):  # achievements_ui (locked)
        lambda th: (None, th["chrome_ink_dim"], None),
    ("row_cta", ui.REST):   # storybook_app._draw_rows, the "+ NEW" row
        lambda th: (th["hilite"], 0, th["dim"]),
    ("row_cta", ui.ON):
        lambda th: (th["accent"], 0, th["accent"]),
}


def test_the_shell_kinds_resolve_to_the_pixels_they_replaced():
    """The #207 transcription, theme set by theme set.

    Two roles that agree on eleven of the twelve sets are not the same role:
    `ink_dim` and `chrome_ink_dim` differ only on machine/dark, which is exactly
    why "row_list" and "row_menu" are two entries and not one. Collapsing them
    would pass every golden and move a pixel on one theme."""
    skin.use(skin.DEFAULT)
    checked = 0
    for name, _tokens in THEMES:
        for variant in THEME_VARIANTS:
            th = theme_colors(name, variant)
            for (kind, state), before in _PRE_207_TRIPLES.items():
                assert tuple(ui.state_colors(th, kind, state)) == \
                    tuple(before(th)), (name, variant, kind, state)
                checked += 1
    assert checked == 12 * len(_PRE_207_TRIPLES)
    # Every kind this catalog registered is transcribed above -- a kind added
    # without its "what it replaced" row is a colour nobody checked.
    assert {k for k, _s in _PRE_207_TRIPLES} == set(_SHELL_KINDS)
