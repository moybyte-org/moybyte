"""The toolkit's style as data, and the outline seed theme.

1. **The style IS the shipped pixels.** The widgets hardcode no colour or pad;
   native/moy_ui resolves them from native/moy_ui/style.json, and every kind
   in every state resolves on every theme set.

2. **The resolution path allocates nothing per draw.** The P4's editor tabs
   are dispatch-bound (#163), so a per-draw dict get is the one place the style
   could lose board performance. The budgets assert a FLOOR before a cap, so
   deleting the probe FAILS rather than passes -- the shape
   `tests/test_top_bar.py::test_bar_strip_rebuild_budget` established.

3. **The `colors=` escape hatch carries frozen pixels and nothing else** (#207).
   Every surviving site is pinned by module, by count and by reason, in both
   directions -- and so are the kinds a surface asks the catalog for, so a
   typo'd kind cannot silently fall back to the row palette.

4. **A restyle beyond colours is a theme's shape.** `outline` is a seed theme
   (docs/theming_2026-09.md §9, phase 3 step 4): its surfaces restyle the
   shipped widgets on every variant, with no code of its own.
"""

import ast
import json
from pathlib import Path

import pytest

from runtime import ui_draw
from runtime.chrome import THEME_NAMES, THEME_VARIANTS
from ws_helpers import fixture_colors, install_theme
from theme_live import live_tokens  # noqa: E402
from runtime.host_canvas import make_system_canvas as SystemCanvas
from runtime.host_canvas import indices_of

from tests.test_ui_states import _paint_shipped

ROOT = Path(__file__).resolve().parent.parent
STYLE = json.loads(ROOT.joinpath("native", "moy_ui", "style.json").read_text())


# =============================================================================
# 1. the style IS the current pixels
# =============================================================================


def test_the_default_tables_cover_every_kind_the_toolkit_asks_for():
    """A kind a widget looks up but the style does not name would silently
    style as a row. Enumerated from the draw functions themselves."""
    asked = ("row", "cell", "cell_band", "chip", "button", "button_primary",
             "button_tonal", "button_danger", "tab", "status", "panel",
             "panel_title", "toolbar", "scrollbar", "focus",
             # the shell's own row kinds, asked for through `row(kind=...)`
             "row_menu", "row_list", "row_chrome", "row_cta")
    for kind in asked:
        assert kind in ui_draw.kinds(), kind
        assert ui_draw.REST in STYLE["kinds"][kind], kind
    for kind in ui_draw.kinds():
        assert ui_draw.REST in STYLE["kinds"][kind], (
            "%r names no REST entry, so a state it does not name resolves "
            "through the row fallback instead of its own look" % kind)
    metric_kinds = ("button", "chip", "row", "cell", "tab", "status", "panel",
                    "focus", "scrollbar", "text_field", "game_btn",
                    "game_icon_btn", "mini_btn", "dialog")
    for kind in metric_kinds:
        assert isinstance(ui_draw.metrics(kind), tuple), kind


def test_every_state_of_every_kind_resolves_on_every_theme_set():
    """The restyle promise, over the whole table rather than row/cell alone: a
    theme that names NO state token still resolves to a real palette index, the
    block's 64.. included (or to None, which means 'paint nothing here'), on
    every family x variant."""
    for name in THEME_NAMES:
        for variant in THEME_VARIANTS:
            th = live_tokens(name, variant)
            for kind in ui_draw.kinds():
                for state in ui_draw.STATES:
                    trio = ui_draw.state_colors(th, kind, state)
                    assert len(trio) == 3, (kind, state)
                    for c in trio:
                        assert c is None or (isinstance(c, int)
                                             and 0 <= c < 128), (kind, state, trio)


def test_the_scrollbar_width_metric_and_the_class_attribute_agree():
    """`ScrollRegion.BAR_W` is public (a caller hit-tests the bar with it) AND
    a style metric. They must never disagree."""
    bw = ui_draw.metrics("scrollbar")[ui_draw.SB_W]
    assert ui_draw.ScrollRegion.BAR_W == bw
    r = ui_draw.ScrollRegion()
    r.set((0, 0, 40, 100), 400)
    assert r.bar_rect(1)[2] == bw


def test_the_quirks_that_stayed_code_are_documented():
    """Transcription honesty (Section 3.3): not every quirk is data, and the
    ones that are not must say so where the next reader will look."""
    quirks = STYLE["quirks"]
    assert set(quirks) >= {"game_btn", "text_field", "mini_btn"}
    for kind, why in quirks.items():
        assert len(why) > 40, kind


# =============================================================================
# 2. the budget: zero per-draw allocation
# =============================================================================

class _CountingCanvas:
    """A real system canvas whose draw ops are counted. Counts DISPATCH, which
    is what the P4's editor tabs are bound by -- not pixels: the row's fill and
    text counters (what a C primitive bumps per op, as the gates do) plus the
    sprites the faces blit."""

    def __init__(self, w=320, h=240, fs=1):
        self._cv = SystemCanvas(w, h, font_scale=fs)
        self._cv.gate_counts_reset()
        self.font_scale = fs
        self._spr = 0

    @property
    def ops(self):
        fills, texts, _fu, _tu = self._cv.gate_counts()
        return fills + texts + self._spr

    @property
    def _ui(self):
        return self._cv._ui

    def flush_batch(self):
        self._cv.flush_batch()

    def rect(self, *a):
        self._cv.rect(*a)

    def rectb(self, *a):
        self._cv.rectb(*a)

    def print(self, *a):
        self._cv.print(*a)

    def spr(self, *a):
        self._spr += 1
        self._cv.spr(*a)


def _draw_many(cv, th, n=100):
    """A widget storm across every kind that resolves through the style."""
    for i in range(n):
        state = ui_draw.STATES[i % len(ui_draw.STATES)]
        ui_draw.row(cv, th, (0, 0, 200, 16), "Row", state=state, value="v")
        ui_draw.cell(cv, th, (0, 20, 60, 60), "C", state=state)
        ui_draw.chip(cv, th, (0, 90, 60, 18), "Chip", on=(i % 2 == 0))
        ui_draw.button(cv, th, (0, 110, 90, 20), "Go", kind="primary")
        ui_draw.panel(cv, th, (0, 140, 80, 60), title="T")
        ui_draw.status_row(cv, th, (0, 210, 200, 14), ("a", "b"))


@pytest.fixture
def empty_ring(monkeypatch):
    """A theme ring empty as at boot: a theme an earlier test drew is otherwise
    already held and packs nothing. The C look slots the test overwrites are
    re-resolved at the next bind afterwards (`look_moved`)."""
    monkeypatch.setattr(ui_draw, "_RING", [])
    ui_draw.look_moved()
    yield
    monkeypatch.undo()
    ui_draw.look_moved()


def test_the_style_path_resolves_in_c_once_per_theme(monkeypatch, empty_ring):
    """Every widget's colours resolve inside native/moy_ui against the look
    its theme was packed into: 600 widget draws on one theme pack it ONCE and
    never ask the face's cache (`state_colors`, which stays for the callers
    that want a triple), and the faces' own tuples stay shared.

    Floor first: without the pack assertion this test passes when the probe
    is gone, which is how a regression net quietly stops being one."""
    th = fixture_colors("night")
    packs = []
    real_look = ui_draw._C.look
    monkeypatch.setattr(ui_draw._C, "look",
                        lambda slot, row: (packs.append(slot), real_look(slot, row))[1])
    asked = []
    real_colors = ui_draw.state_colors
    monkeypatch.setattr(ui_draw, "state_colors",
                        lambda t, k, st: (asked.append(k), real_colors(t, k, st))[1])
    cv = _CountingCanvas()
    calls = ui_draw._C.calls()
    _draw_many(cv, th, 100)
    assert ui_draw._C.calls() - calls >= 600, "the widgets stopped calling the C"
    assert len(packs) == 1, packs
    assert asked == [], "a widget resolved its colours in Python: %r" % asked[:5]
    # The metrics tuples are shared: `metrics()` is a dict get.
    assert ui_draw.metrics("row") is ui_draw.metrics("row")
    a = ui_draw.state_colors(th, "chip", ui_draw.ON)
    assert ui_draw.state_colors(th, "chip", ui_draw.ON) is a


def test_a_theme_switch_packs_once_and_then_stays_cached(monkeypatch, empty_ring):
    """The fast path is an identity compare, so alternating themes must not
    thrash: each theme is packed into a C look once, while the ring holds it."""
    packs = []
    real_look = ui_draw._C.look
    monkeypatch.setattr(ui_draw._C, "look",
                        lambda slot, row: (packs.append(slot), real_look(slot, row))[1])
    a, b = fixture_colors("night"), fixture_colors("berry")
    cv = _CountingCanvas()
    for _ in range(20):
        ui_draw.row(cv, a, (0, 0, 100, 16), "x")
    assert len(packs) == 1, "no pack counted -- is the probe wired?"
    for _ in range(20):
        ui_draw.row(cv, b, (0, 0, 100, 16), "x")
    assert len(packs) == 2, packs
    # And back: a theme the ring still holds costs NO pack. This is what keeps
    # the `self.theme or {}` sites (an empty dict is a fresh object every
    # call) from evicting the live theme once per frame.
    for _ in range(20):
        ui_draw.row(cv, a, (0, 0, 100, 16), "x")
        ui_draw.row(cv, {}, (0, 0, 100, 16), "x")       # a throwaway theme
    assert len(packs) == 2 + 20, packs


# =============================================================================
# 3. the `colors=` escape hatch, and the kinds a surface asks for (#207)
# =============================================================================
#
# `ui_draw.row` / `ui_draw.cell` / `ui_draw.chip` / `ui_draw.text_field` all take an explicit
# (field, ink, edge) triple that bypasses this catalog entirely. It exists for a
# real case -- `ui_draw.row`'s own docstring: "a site whose pixels are frozen
# off-token" -- and #207 is the triage that separated those from the sites that
# were merely hand-resolving a role the catalog could own. Phase 4 shipped with
# 27 uses of it and no way to tell the two apart; the ones that were a widget
# STATE PALETTE now resolve through `ui_draw.state_colors` against a kind, and what
# is left is pinned below.
#
# The classifier reads what reaches the `colors=` argument of a call. A
# hand-built triple is a bypass and is counted; an explicit `None` is not (it is
# the default, and it is how a branched site spells "use the kind"). A site that
# computes in branches has each BRANCH classified, which is why two modules
# appear both here and in the kind-naming floor below.

# The shell's own row kinds -- style.json's kinds beyond the toolkit's
# own vocabulary, added by #207. Named here rather than derived, so that adding
# one to `ui.py` without a consumer, a transcription row and an "outline" delta
# is a red test in three places.
_SHELL_KINDS = ("row_menu", "row_list", "row_chrome", "row_cta")
# Kinds no surface asks for that stay in the table: the part list theme
# packages name is frozen (docs/theming_2026-09.md §4.3), so a part outlives
# its last surface. `row_cta` was the old Storybook's NEW row, `row_list` the
# power popover's level line (its rows are Settings' sliders since look step 33).
_UNASKED_PARTS = ("row_cta", "row_list")

_FROZEN_HATCH = {
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
    """The SHELL kinds this module asks a widget for by name. `ui_draw.button`'s own
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
            "the style: drop the count.\nrecorded reason: %s"
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
        "cards_layer.py": 2,            # the cards and the ADVANCED row
    }, named
    assert sum(named.values()) == 2


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
        if kind in STYLE["button_kinds"]:
            continue
        assert kind in ui_draw.kinds(), (kind, asked[kind])
    # And every shell kind has a consumer: a table entry nothing asks for is a
    # decision pretending to be one.
    for kind in _SHELL_KINDS:
        assert kind in ui_draw.kinds(), kind
        if kind in _UNASKED_PARTS:
            assert kind not in asked, "%r is asked for again: drop it from the list" % kind
            continue
        assert kind in asked, "%r is in the table but nothing asks for it" % kind


# The expression each moved site built by hand at HEAD (`3b22d5a`), as a lambda
# over one theme dict. This is the transcription pin: `_quiet_row(...)` and its
# siblings must resolve to EXACTLY these triples on all 12 theme x variant sets,
# which is what makes the move a no-op rather than a re-baseline.
#
# It is not redundant with the goldens, and the gap is measured rather than
# assumed: `row_cta` is drawn by no surface (it stays as a part of the frozen
# vocabulary, below), so only these triples hold its pixels. Every other state
# below IS netted by a golden (verified by mutation: `row_menu` REST turns all
# ten golden rows red).
_PRE_207_TRIPLES = {
    ("row_menu", ui_draw.REST):  # the system menu (moy_chrome)
        lambda th: (None, th["chrome_ink_dim"], None),
    ("row_menu", ui_draw.ON):
        lambda th: (th["hilite"], th["selection_ink"], None),
    ("row_list", ui_draw.REST):  # the wifi list + the wifi/bluetooth note lines
        lambda th: (None, th["ink_dim"], None),
    ("row_list", ui_draw.ON):
        lambda th: (th["hilite"], th["selection_ink"], None),
    ("row_chrome", ui_draw.REST):  # cards_layer (a card)
        lambda th: (None, th["ink"] if th.get("bar_light", False)
                    else th["chrome_ink"], None),
    ("row_chrome", ui_draw.ON):
        lambda th: (th["hilite"], th["selection_ink"], None),
    ("row_chrome", ui_draw.DISABLED):  # no surface draws a disabled card row
        lambda th: (None, th["chrome_ink_dim"], None),
    ("row_cta", ui_draw.REST):   # a part of the frozen vocabulary no surface draws
        lambda th: (th["hilite"], 0, th["dim"]),
    ("row_cta", ui_draw.ON):
        lambda th: (th["accent"], 0, th["accent"]),
}


def test_the_shell_kinds_resolve_to_the_pixels_they_replaced():
    """The #207 transcription, theme set by theme set.

    Two roles that agree on eleven of the twelve sets are not the same role:
    `ink_dim` and `chrome_ink_dim` differ only on machine/dark, which is exactly
    why "row_list" and "row_menu" are two entries and not one. Collapsing them
    would pass every golden and move a pixel on one theme."""
    checked = 0
    for name in THEME_NAMES:
        for variant in THEME_VARIANTS:
            th = live_tokens(name, variant)
            for (kind, state), before in _PRE_207_TRIPLES.items():
                assert tuple(ui_draw.state_colors(th, kind, state)) == \
                    tuple(before(th)), (name, variant, kind, state)
                checked += 1
    assert checked == 2 * len(THEME_NAMES) * len(_PRE_207_TRIPLES)
    # Every kind this catalog registered is transcribed above -- a kind added
    # without its "what it replaced" row is a colour nobody checked.
    assert {k for k, _s in _PRE_207_TRIPLES} == set(_SHELL_KINDS)


# =============================================================================
# 4. the outline seed: a restyle in the theme's shape
# =============================================================================

def test_the_outline_package_restyles_the_shipped_widgets_on_every_variant():
    """The skin axis folded into the theme (docs/theming_2026-09.md §9, phase 3
    step 4): `outline`, once a seed and now a fixture package
    (tests/fixtures/themes/), has surfaces that repaint the shipped widgets,
    never to nothing, and the shape it installs is its own."""
    for variant in THEME_VARIANTS:
        th = fixture_colors("outline", variant)
        install_theme("outline")
        try:
            ui_draw.use_shape("moy")
            a = SystemCanvas(320, 240, font_scale=1)
            _paint_shipped(a, th)
            ui_draw.use_shape("outline")
            b = SystemCanvas(320, 240, font_scale=1)
            _paint_shipped(b, th)
        finally:
            ui_draw.use_shape(None)
        assert bytes(a._buf) != bytes(b._buf), variant
        assert len(set(indices_of(b))) > 3, variant
