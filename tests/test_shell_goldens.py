"""The SHELL pixel goldens (#UI refactor 2026-08, Phase 0).

What this is, and what it is NOT
--------------------------------
This hashes the rendered SYSTEM canvas of every shell surface against bytes
STORED in the repo (`tests/shell_goldens/hashes.json`). That is the whole
point, and it is what makes it different in kind from the checks it must not
be confused with:

  * `tests/spec_conformance/hashes.json` pins the CART raster (320x240,
    ten recorded verb traces). It says nothing about a single shell pixel.
  * `tests/test_responsive_editors.py` builds TWO workstations *now* and
    compares them -- a live-vs-live A/B. A refactor that moves both arms
    passes green. It also asserts layout attributes against the constants in
    the module it is guarding (`lay.pg_span == P._PG_SPAN`), which is an
    oracle derived from the thing under test.
  * `tests/test_wm_windowed.py` compares two code paths in the same commit.

Only a hash of committed bytes can catch "the whole shell moved one pixel",
which is exactly the risk of transcribing hand-rolled widgets onto a toolkit.

The matrix, and why it is this one
----------------------------------
Five configurations, each ONE axis away from the frozen baseline, so a red
golden names the axis by itself. Every configuration renders the SAME surface
list (plus the two desk surfaces that only exist on the windowed tier), which
keeps the matrix a clean product instead of a pile of special cases.

  tdeck_320x240_fs1_dark   the frozen baseline: shared canvas (sys IS game),
                           font scale 1, dark. Every other tier degrades to
                           these pixels, so this is the reference row.
  tdeck_320x240_fs1_light  the SAME geometry, `variant="light"`. The variant
                           axis in isolation: any difference from the row
                           above is purely the token set. Before this file,
                           `variant="light"` appeared five times in the suite
                           and was NEVER followed by a draw -- a light panel
                           could render black-on-black and the suite stayed
                           green.
  guition_480x320_fs1_dark the Guition tier (#202): a fullscreen board whose
                           system canvas is NOT the game canvas. Zero host
                           coverage before this file; asserted only in
                           `tests/test_guition_on_glass.py`, which skips
                           without the board.
  big_800x480_fs3_dark     font_scale 3. fs=2 renders elsewhere but is never a
                           pinned reference and fs=3 never drew a shell frame
                           at all (`ui_widgets_2026-08.md` A-m6). 800x480 is
                           the smallest canvas that gives fs=3 room.
  p4_1024x600_fs2_windowed the desktop tier: WindowedWM, font_scale 2 (so the
                           fs=2 rung is pinned here rather than in a config of
                           its own), plus the desk and a desk-with-one-window.

That is 5 configurations x 17 surfaces (+2 windowed-only) = 87 goldens in
about 1 second. The combination NOT covered is light-on-windowed; it is the
one intersection, not an axis, and adding it would start the product
explosion this phase was told to avoid.

Determinism
-----------
Everything time-dependent is pinned rather than tolerated (the repo rule:
dt is INJECTED, never read from a clock -- `ScrollRegion` is the precedent):

  * dt is the fixed `_DT`; no surface reads a clock for its own animation.
  * `bar_layer._clock_text` is bound to a constant. The OS bar prints a live
    HH:MM, and a minute boundary between two renders is a real flake that has
    taken CI down before (see `test_responsive_editors._quiesce`).
  * every transient overlay is cleared each frame (`_quiesce`): the toast, the
    confetti/egg timers, the achievements list, the system menu, About, the
    notice banner, the FPS/perf HUDs, the cursor.
  * `console._COVER_SLICE_MS` is raised so every launcher/picker cover builds
    within its frame. This is load-bearing, not belt-and-braces: the cover
    build is a per-frame TIME budget, and the launcher home hashes differently
    at a 1ms slice than at 5ms. Left alone, these goldens would be a function
    of how fast the machine is.
  * each surface is rendered until two consecutive frames are byte-identical
    (cap `_MAX_FRAMES`); a surface that never settles FAILS by name rather
    than silently baking in whichever frame it was caught on.

Deliberately NOT covered
------------------------
  * A RUNNING cart. That is the game domain -- pinned by
    `tests/test_spec_conformance.py` against the spec's own goldens -- and
    seed carts are free to use `rnd()`, so it is not deterministic here.
  * The EDIT ICONS ("theme") surface and the OTA update screen: neither is in
    the Phase 0 list, and the update screen's content is a live network state
    machine.
  * light x windowed (see the matrix note above).

What moves these goldens (so a red one is diagnosable)
-----------------------------------------------------
Three things, in descending order of likelihood: a shell DRAW change; a
`system_carts/` edit (the surfaces show real seeded content -- the shelf, the
picker grid and every Editor tab render the seeded store, and the Editor tabs
render `Star Catcher` specifically); and a font/palette change.

One structural fact learned while proving this harness can fail (2026-08-19):
at 320x240 / font scale 1 the shell takes its FROZEN hand-rolled branches --
`editor_app._draw_zone` and its siblings are guarded `if not ws.layout._base`
-- so the `runtime/ui.py` toolkit is only live on the other three rows.
Perturbing `ui.button`'s pad by one unit turned guition / big_fs3 / p4 red and
left BOTH tdeck rows green. So the Phase 3 widget transcription is gated by
the non-`_base` configs; a matrix of just the baseline tier would have watched
that phase go by without seeing it.

Re-baselining is a DELIBERATE act
---------------------------------
    MOYBYTE_UPDATE_GOLDENS=1 .venv/bin/python -m pytest tests/test_shell_goldens.py -p no:xdist
or  .venv/bin/python -m pytest tests/test_shell_goldens.py --update-goldens -p no:xdist

Nothing re-baselines automatically. If a golden moves and you cannot say in
one sentence which pixel moved and why, the change is a revert, not an update
(`docs/ui_refactor_2026-08.md` Section 8).
"""

import hashlib
import json
import os
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
GOLDEN_DIR = Path(__file__).resolve().parent / "shell_goldens"
GOLDEN_FILE = GOLDEN_DIR / "hashes.json"

UPDATE_ENV = "MOYBYTE_UPDATE_GOLDENS"
REBASELINE_CMD = (
    "MOYBYTE_UPDATE_GOLDENS=1 .venv/bin/python -m pytest "
    "tests/test_shell_goldens.py -p no:xdist")

_DT = 1.0 / 30.0          # the injected frame delta; never a clock
_MAX_FRAMES = 6           # settle cap -- every surface settles at 2 today
_CLOCK = "00:00"          # the pinned OS-bar clock text
_COVER_SLICE_UNBOUNDED = 10 ** 9   # ms: build every cover in its own frame

# The cart whose Editor tabs are rendered. A seeded GAME with a sprite sheet,
# a tilemap, a scene and a sound bank, so all seven tabs have real content.
_EDITOR_CART = "Star Catcher"

# Seed carts the golden matrix deliberately does NOT show.
#
# The launcher shelf, the picker grid and the desk icon column render REAL
# seeded content (see "What moves these goldens" above), so without this list
# these 87 goldens are a function of `system_carts/` and merely ADDING a cart
# turns five configurations red for a reason that is not a pixel. That is the
# worst kind of red: it trains the reader to re-baseline, which is precisely the
# laundering this file exists to prevent.
#
# So a content addition is either EXCLUDED here -- one line, with a `why`, the
# `board.toml` deny convention -- or deliberately re-baselined. Both are
# decisions; neither is a shrug. Excluding a cart costs nothing this file
# measures: no golden here renders a cart's own pixels (a running cart is the
# game domain, pinned by tests/test_spec_conformance.py), only the tile the
# shelf draws for it, and 34 tiles already exercise every shelf branch there is.
GOLDEN_EXCLUDE = {
    "Notes": "the #181 user-app demo cart, added after the Phase 0 baseline. "
             "It is content; the shell paths it exercises (the app bar over a "
             "cart, the permission-gated namespace) are pinned by "
             "tests/test_user_apps.py.",
}

# The Editor tab ladder and the system-app roster. Both are pinned against the
# live registries below (test_tab_ladder_is_fully_covered /
# test_every_registered_app_is_covered), so adding a tab or an app without
# adding its golden is a red test rather than a silent coverage hole.
TABS = ("cards", "blocks", "code", "paint", "map", "scene", "music")
APPS = ("artwork", "appearance", "writer", "storybook", "sheets", "files", "calc")

CONFIGS = {
    "tdeck_320x240_fs1_dark": dict(
        sys_size=None, font_scale=1, windowed=False, variant="dark"),
    "tdeck_320x240_fs1_light": dict(
        sys_size=None, font_scale=1, windowed=False, variant="light"),
    "guition_480x320_fs1_dark": dict(
        sys_size=(480, 320), font_scale=1, windowed=False, variant="dark"),
    "big_800x480_fs3_dark": dict(
        sys_size=(800, 480), font_scale=3, windowed=False, variant="dark"),
    "p4_1024x600_fs2_windowed": dict(
        sys_size=(1024, 600), font_scale=2, windowed=True, variant="dark"),
}


def _axes(cfg):
    """The one-line axis description quoted in a failure message."""
    w, h = cfg["sys_size"] or (320, 240)
    return "size=%dx%d font_scale=%d variant=%s tier=%s" % (
        w, h, cfg["font_scale"], cfg["variant"],
        "windowed" if cfg["windowed"] else "fullscreen")


# ---------------------------------------------------------------------------
# capture
# ---------------------------------------------------------------------------

def _quiesce(ws):
    """Strip every transient + clock-driven thing off the frame.

    Called before EVERY frame, not once per surface: opening a cart or an app
    notes achievements, which can re-arm the toast/confetti mid-sequence."""
    if ws.pointer is not None:
        ws.pointer.visible = False
    ws.ach.toast = None
    ws.ach.toast_until = 0
    au = ws.ach_ui
    au.egg_msg = None
    au.egg_until = 0
    au._confetti_until = 0
    ws.show_achievements = False
    ws.show_fps = False
    ws.perf_hud = False
    ws.perf_capture = False
    ws.sysmenu.open = False
    ws._about = False
    ws._notice = None
    ws._notice_until = 0
    # The bar prints a live HH:MM. Bind the accessor -- pinning the per-second
    # cache does NOT work (bar_layer compares `now_s == self._clock_at`, so a
    # sentinel forces a recompute every frame).
    ws.bar_layer._clock_text = lambda: _CLOCK


def _render(ws, surface, config_name):
    """Draw until two consecutive frames are byte-identical; hash that frame."""
    prev = None
    for _ in range(_MAX_FRAMES):
        _quiesce(ws)
        ws._dirty = True                 # defeat the #44 redraw gate
        ws.frame(_DT)
        cur = bytes(ws.sys_canvas._buf)
        if cur == prev:
            return hashlib.sha256(cur).hexdigest()
        prev = cur
    raise AssertionError(
        "shell golden surface %r (config %r) never settled: %d frames drawn "
        "and the last two still differ. Something on it is animating or "
        "time-dependent; pin it (inject dt) or exclude it with a comment "
        "naming what and why." % (surface, config_name, _MAX_FRAMES))


def _cart_by_title(ws, title):
    for cart in ws._all_carts:
        if cart.get("title") == title:
            return cart
    raise AssertionError("seed cart not found: " + title)


def _build(cfg, carts_dir):
    from runtime import host_app
    ws = host_app.build_workstation(
        str(carts_dir), sys_size=cfg["sys_size"],
        font_scale=cfg["font_scale"], windowed=cfg["windowed"])
    # persist=False: the two tdeck rows must differ by the token set ALONE, so
    # neither may leave a theme_variant behind in its store.
    ws.set_theme_variant(cfg["variant"], persist=False)
    keep = [c for c in ws._all_carts if c.get("title") not in GOLDEN_EXCLUDE]
    if len(keep) != len(ws._all_carts):
        ws._apply_items(keep)            # pin the roster -- see GOLDEN_EXCLUDE
    return ws


def _surface_plan(ws, cfg):
    """(name, enter) pairs in the fixed order they are rendered.

    ONE workstation renders them all, in this order, so the captured state is
    a pure function of the list -- a fresh workstation per surface would be
    ~35 seed copies each and buy nothing a fixed order does not."""
    plan = []
    if cfg["windowed"]:
        # The desk (the make world's floor) and the desk with ONE app window.
        plan.append(("desk", lambda: ws.open_desk()))
        plan.append(("desk_window_calc",
                     lambda: ws.open_app(ws._apps_by_id["calc"])))
    plan.append(("launcher", lambda: ws.go_home()))
    plan.append(("picker", lambda: ws.open_picker()))
    plan.append(("settings", lambda: ws.open_settings()))
    cart = _cart_by_title(ws, _EDITOR_CART)
    for tab in TABS:
        def enter(tab=tab):
            ws.open_in_editor(cart)      # re-open per tab: no cross-tab state
            ws.set_menu_view(tab)
        plan.append(("editor_" + tab, enter))
    for app in APPS:
        plan.append(("app_" + app,
                     lambda app=app: ws.open_app(ws._apps_by_id[app])))
    return plan


def surface_names(config_name):
    """The surfaces a config covers -- the key set its goldens must carry."""
    cfg = CONFIGS[config_name]
    names = []
    if cfg["windowed"]:
        names += ["desk", "desk_window_calc"]
    names += ["launcher", "picker", "settings"]
    names += ["editor_" + t for t in TABS]
    names += ["app_" + a for a in APPS]
    return names


def capture(config_name, carts_dir):
    """{surface: sha256} for one configuration."""
    cfg = CONFIGS[config_name]
    ws = _build(cfg, carts_dir)
    expect_bytes = ws.sys_canvas.w * ws.sys_canvas.h * 2      # RGB565
    out = {}
    for name, enter in _surface_plan(ws, cfg):
        enter()
        assert len(bytes(ws.sys_canvas._buf)) == expect_bytes, (
            "%s/%s: system canvas is %d bytes, expected %d (w*h*2)"
            % (config_name, name, len(bytes(ws.sys_canvas._buf)), expect_bytes))
        out[name] = _render(ws, name, config_name)
    return out


# ---------------------------------------------------------------------------
# the goldens file + the explicit re-baseline
# ---------------------------------------------------------------------------

def _load_goldens():
    if not GOLDEN_FILE.exists():
        raise AssertionError(
            "%s is missing. Baseline it deliberately:\n    %s"
            % (GOLDEN_FILE, REBASELINE_CMD))
    return json.loads(GOLDEN_FILE.read_text(encoding="utf-8"))


def _updating(request):
    if os.environ.get(UPDATE_ENV):
        return True
    # The pytest flag is registered in tests/conftest.py; `getoption` with a
    # default keeps this module importable without it.
    return bool(request.config.getoption("--update-goldens", default=False))


_CAPTURED = {}          # config -> {surface: hash}, filled only while updating


@pytest.fixture(scope="module", autouse=True)
def _write_goldens_on_teardown(request):
    """Write the re-baselined file ONCE, after the last test in this module.

    Per-test writes would race under xdist, so update mode refuses to run
    distributed (below) and accumulates into `_CAPTURED` instead."""
    yield
    if not _CAPTURED:
        return
    GOLDEN_DIR.mkdir(parents=True, exist_ok=True)
    stored = {}
    if GOLDEN_FILE.exists():
        stored = json.loads(GOLDEN_FILE.read_text(encoding="utf-8"))
    stored.update(_CAPTURED)
    if set(_CAPTURED) == set(CONFIGS):
        # A FULL re-baseline may prune configs that no longer exist; a partial
        # one (`-k tdeck`) must never delete the rows it did not render.
        stored = {k: v for k, v in stored.items() if k in CONFIGS}
    GOLDEN_FILE.write_text(
        json.dumps(stored, indent=2, sort_keys=True) + "\n", encoding="utf-8")


@pytest.fixture(autouse=True)
def _pin_cover_budget(monkeypatch):
    """Build every cover inside its own frame.

    `_cover_for` spends a per-frame TIME slice (`_COVER_SLICE_MS`), so on a
    slower machine a shelf cover pops in a frame later and the launcher hashes
    differently -- measured: slice 1ms and slice 5ms give different launcher
    pixels. Raising the budget makes the captured frame the fully-built one on
    every machine."""
    from runtime import console
    monkeypatch.setattr(console, "_COVER_SLICE_MS", _COVER_SLICE_UNBOUNDED)


# ---------------------------------------------------------------------------
# the tests
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("config_name", sorted(CONFIGS))
def test_shell_surfaces_match_goldens(config_name, tmp_path, request):
    got = capture(config_name, tmp_path / "carts")

    if _updating(request):
        assert not hasattr(request.config, "workerinput"), (
            "re-baselining under xdist would have every worker rewrite the "
            "goldens file. Run it serially:\n    " + REBASELINE_CMD)
        _CAPTURED[config_name] = got
        pytest.skip("re-baselining %s (%d surfaces)" % (config_name, len(got)))

    stored = _load_goldens()
    golden = stored.get(config_name)
    assert golden is not None, (
        "no goldens stored for config %r (axes: %s). Baseline it:\n    %s"
        % (config_name, _axes(CONFIGS[config_name]), REBASELINE_CMD))

    moved = []
    for surface in sorted(set(got) | set(golden)):
        want = golden.get(surface)
        have = got.get(surface)
        if want != have:
            moved.append((surface, want, have))
    if moved:
        lines = [
            "SHELL PIXELS MOVED -- config %r" % config_name,
            "  axes: %s" % _axes(CONFIGS[config_name]),
            "  %d surface(s) changed:" % len(moved),
        ]
        for surface, want, have in moved:
            lines.append("    %-20s golden=%s  rendered=%s"
                         % (surface,
                            (want[:16] + "..") if want else "<absent>",
                            (have[:16] + "..") if have else "<not rendered>"))
        lines.append("")
        lines.append("  Each line is one shell surface whose rendered system "
                     "canvas no longer hashes to the bytes stored in")
        lines.append("  %s." % GOLDEN_FILE.relative_to(ROOT))
        lines.append("  If the change is INTENDED and you can say which pixel "
                     "moved and why, re-baseline deliberately:")
        lines.append("    " + REBASELINE_CMD)
        raise AssertionError("\n".join(lines))


def test_goldens_file_covers_exactly_the_matrix(request):
    """No stale rows, no missing rows -- a deleted surface or a renamed config
    has to be re-baselined rather than quietly left behind in the file."""
    if _updating(request):
        pytest.skip("re-baselining: the file is rewritten at module teardown")
    stored = _load_goldens()
    assert sorted(stored) == sorted(CONFIGS), (
        "stored configs %s != matrix %s -- re-baseline:\n    %s"
        % (sorted(stored), sorted(CONFIGS), REBASELINE_CMD))
    for config_name in CONFIGS:
        assert sorted(stored[config_name]) == sorted(surface_names(config_name)), (
            "config %r: stored surfaces %s != expected %s -- re-baseline:\n    %s"
            % (config_name, sorted(stored[config_name]),
               sorted(surface_names(config_name)), REBASELINE_CMD))
        for surface, digest in stored[config_name].items():
            assert isinstance(digest, str) and len(digest) == 64, (
                "%s/%s: %r is not a sha256 hex digest"
                % (config_name, surface, digest))


def test_every_axis_is_actually_exercised():
    """The matrix must MOVE each axis, not merely name it. A config list that
    silently collapsed onto one geometry would keep every golden green while
    covering nothing."""
    sizes = {(c["sys_size"] or (320, 240)) for c in CONFIGS.values()}
    assert (320, 240) in sizes and (480, 320) in sizes
    assert {c["font_scale"] for c in CONFIGS.values()} >= {1, 2, 3}
    assert {c["variant"] for c in CONFIGS.values()} == {"dark", "light"}
    assert {c["windowed"] for c in CONFIGS.values()} == {True, False}


def test_the_light_variant_really_draws_different_pixels(request):
    """The blind spot this harness was built for: `variant="light"` appeared
    five times in the suite and was never followed by a draw, so a light panel
    could have rendered black-on-black forever. Two configs differ ONLY by the
    variant, so every surface that reads a theme token must differ."""
    if _updating(request):
        pytest.skip("re-baselining: the file is rewritten at module teardown")
    stored = _load_goldens()
    dark = stored["tdeck_320x240_fs1_dark"]
    light = stored["tdeck_320x240_fs1_light"]
    same = [s for s in dark if dark[s] == light.get(s)]
    assert not same, (
        "these surfaces hash IDENTICALLY in dark and light, i.e. they ignore "
        "the theme variant: %s" % sorted(same))


def test_tab_ladder_is_fully_covered():
    """Every real Editor tab has a golden (the sentinel PROJECTS/UNDO/REDO/PLAY
    zone entries are actions, not tabs)."""
    from runtime import editor_app
    live = tuple(t for t, _glyph in editor_app._ZONE_TABS
                 if t and not t.startswith("\x00"))
    assert live == TABS, (
        "the Editor tab ladder changed (%s) -- add the new tab here and "
        "re-baseline:\n    %s" % (list(live), REBASELINE_CMD))


def test_every_registered_app_is_covered(tmp_path):
    """Every registered system app has a golden. A new app that skips this
    list is a coverage hole the refactor would not see."""
    ws = _build(CONFIGS["tdeck_320x240_fs1_dark"], tmp_path / "carts")
    assert sorted(ws._apps_by_id) == sorted(APPS), (
        "the system-app registry changed (%s) -- add the new app here and "
        "re-baseline:\n    %s" % (sorted(ws._apps_by_id), REBASELINE_CMD))
