"""The shell layout's resolver (docs/shell_layout_2026-10.md §9-§11, §13 step 4).

The loader's half (caps, clamps, the required set on the data, inheritance)
is `tests/test_theme_loader.py`'s. Here: the composer's drops per tier and
mode, the 75% floor, the required set again after the drops, §6's worked
examples resolved per tier, the user's `layout_theme` pick (its precedence,
its strict reader, the Settings row) and the host bar following the
resolved layout through `Workstation.place_bar`.
"""

import json
import random

import pytest

import runtime  # noqa: F401  (registers the host's moy_ui)
import moy_ui
from runtime import layout_base, shell_layout
from runtime.shell_layout import FALLBACK, resolve
from test_shell_goldens import CONFIGS
from ws_helpers import build_desktop_ws, build_ws

TODAY = FALLBACK[0]
ALL = shell_layout.WIDGETS
CONSOLE = tuple(w for w in ALL if w != "chips")
STEP4 = ("top", "bottom")


@pytest.fixture(autouse=True)
def _top_again():
    yield
    layout_base.place_bar("top")


def _bar(edge, size=18, left=(), center=(), right=(), text=()):
    return (edge, size, tuple(left), tuple(center), tuple(right), tuple(text))


# §6's worked examples, as the loader hands them over.
EXAMPLES = {
    1: (TODAY,),
    2: (_bar("bottom", 22, ["menu", "separator", "chips", "zone"], [],
             ["close", "wifi", "battery", "clock"]),),
    3: (_bar("top", 18, ["zone"], ["clock"], ["wifi", "battery", "close", "menu"]),),
    4: (_bar("top", 18, ["title"], [], ["clock", "close", "menu", "wifi", "battery"]),
        _bar("bottom", 18, ["zone"])),
    5: (TODAY, _bar("right", 120, ["calendar"], [], ["spacer"])),
    6: (_bar("bottom", 18, ["menu", "zone"], [], ["clock", "close", "battery"]),),
}

# The glasses of §10's tier table: (w, h, chrome scale, catalogue).
TIERS = {
    "tdeck": (320, 240, 1, CONSOLE),
    "guition_s3": (480, 320, 2, CONSOLE),
    "p4_desktop": (1024, 600, 2, ALL),
    "p4_console": (1024, 600, 2, CONSOLE),
    "guition_p4_desktop": (1280, 800, 2, ALL),
}


def _res(bars, tier, edges=STEP4):
    w, h, cs, cat = TIERS[tier]
    return resolve(bars, (TODAY,), w, h, cs, cat, edges)


@pytest.mark.parametrize("tier", sorted(TIERS))
def test_the_worked_examples_resolve_per_tier(tier):
    desktop = TIERS[tier][3] is ALL
    assert _res(EXAMPLES[1], tier) == ((TODAY,), ())
    bars, notes = _res(EXAMPLES[2], tier)
    if desktop:
        assert (bars, notes) == (EXAMPLES[2], ())
    else:
        # `chips` has no value in console mode: dropped, the bar stays
        assert bars == (_bar("bottom", 22, ["menu", "separator", "zone"], [],
                             ["close", "wifi", "battery", "clock"]),)
        assert notes == ("chips: not in this mode",)
    assert _res(EXAMPLES[3], tier) == (EXAMPLES[3], ())
    assert _res(EXAMPLES[4], tier) == (EXAMPLES[4], ())
    # Side bars wait for step 5: the right bar is dropped, the top bar stands.
    assert _res(EXAMPLES[5], tier) == ((TODAY,), ("right bar: not on this tier",))
    assert _res(EXAMPLES[6], tier) == (EXAMPLES[6], ())


def test_a_docked_calendar_clears_the_floor_on_the_big_glass_only():
    # With the side edges open (step 5), example 5 keeps 76% of the P4's
    # width and resolves; on a 320- or 480-wide glass it fails the floor and
    # the default stands in -- one rule, no board exception.
    edges = shell_layout.EDGES
    for tier in ("p4_desktop", "p4_console", "guition_p4_desktop"):
        assert _res(EXAMPLES[5], tier, edges) == (EXAMPLES[5], ()), tier
    for tier in ("tdeck", "guition_s3"):
        bars, notes = _res(EXAMPLES[5], tier, edges)
        assert bars == (TODAY,) and notes[-1] == "replaced by the default", tier
        assert "the bars leave the content under 75% of the glass" in notes


def test_the_rule_runs_again_after_the_drops():
    # The loader passes `zone` in a vertical bar; the composer drops it there
    # and the required set fails, so the default stands in.
    lay = (_bar("top", 18, ["menu", "close"]), _bar("left", 40, ["zone"]))
    bars, notes = _res(lay, "p4_desktop", shell_layout.EDGES)
    assert bars == (TODAY,)
    assert notes == ("zone: not on a left bar", "no zone after the drops",
                     "replaced by the default")
    bars, notes = _res(lay, "p4_desktop")
    assert bars == (TODAY,) and notes[0] == "left bar: not on this tier"
    # a required widget that does not fit is a drop like any other
    lay = (_bar("top", 18, ["text"], [], ["clock", "wifi", "battery", "zone", "menu", "close"],
                text=["x" * 24]),)
    bars, notes = _res(lay, "tdeck")
    assert bars == (TODAY,)
    assert notes[:2] == ("zone: does not fit the top bar", "no zone after the drops")


def test_a_widget_that_does_not_fit_its_bar_is_dropped():
    lay = (_bar("top", 18, ["zone", "calendar", "title"], [], ["menu", "close", "clock"]),)
    bars, notes = _res(lay, "tdeck")
    assert bars == (_bar("top", 18, ["zone", "title"], [], ["menu", "close", "clock"]),)
    assert notes == ("calendar: does not fit the top bar",)
    # a bar's length is the glass at the chrome scale: the Guition S3's 480
    # columns hold 240 logical, the zone and the right cluster with room
    lay = (_bar("top", 18, ["zone", "text"], [], ["menu", "close"], ["y" * 12]),)
    assert _res(lay, "guition_s3")[0] == lay
    lay = (_bar("top", 18, ["zone", "text", "text"], [], ["menu", "close"],
                ["y" * 12, "z" * 12]),)
    bars, notes = _res(lay, "guition_s3")
    assert bars == (_bar("top", 18, ["zone", "text"], [], ["menu", "close"], ["y" * 12]),)
    assert notes == ("text: does not fit the top bar",)


def test_the_content_keeps_three_quarters_of_each_axis():
    tall = (_bar("top", 160, ["zone"], [], ["menu", "close"]),)
    bars, notes = _res(tall, "tdeck")
    assert bars == (TODAY,) and notes[0] == "the bars leave the content under 75% of the glass"
    ok = (_bar("top", 30, ["zone"], [], ["menu", "close"]),)   # 210 of 240 rows stay
    assert _res(ok, "tdeck") == (ok, ())


def test_the_composer_never_raises():
    rng = random.Random(203)
    names = list(ALL) + ["text"] * 3
    for _i in range(2000):
        bars = []
        for edge in rng.sample(shell_layout.EDGES, rng.randint(0, 4)):
            slots = [[rng.choice(names) for _k in range(rng.randint(0, 6))] for _s in range(3)]
            n_text = sum(s.count("text") for s in slots)
            bars.append((edge, rng.randint(16, 160)) + tuple(map(tuple, slots))
                        + (tuple("t" * rng.randint(1, 24) for _t in range(n_text)),))
        w, h = rng.choice(((320, 240), (480, 320), (1024, 600), (64, 64), (1280, 800)))
        cs = rng.randint(1, 3)
        edges = rng.choice((STEP4, shell_layout.EDGES))
        got, notes = resolve(tuple(bars), (TODAY,), w, h, cs, rng.choice((ALL, CONSOLE)), edges)
        assert got and all(b[0] in edges or got is FALLBACK for b in got)
        assert all(type(n) is str for n in notes)


# -- the workstation: the pick, the reader, the row, the mode ----------------------

DOCK = {"format": 1, "name": "dock", "inherits": "berry",
        "layout": {"bars": [{"edge": "bottom", "size": 22,
                             "left": ["menu", "separator", "chips", "zone"],
                             "right": ["close", "wifi", "battery", "clock"]}]}}


def _install(pkg):
    rc, _notes = moy_ui.theme_install(json.dumps(pkg).encode())
    assert rc == 0


def _rows(ws):
    return [r[0] for r in ws.settings_layer._settings_rows()]


def test_the_default_layout_is_todays_bar_on_every_tier(tmp_path):
    for ws in (build_ws(tmp_path / "a"), build_desktop_ws(tmp_path / "b")):
        assert ws.look.bars == (TODAY,) and ws.look.layout_notes == ()
        assert layout_base.bar_edge() == "top"
        assert ws.look.layout_theme() is None
        assert "layout_theme" not in _rows(ws)     # every seed's layout is the default's


def test_a_picked_layout_survives_two_theme_switches(tmp_path):
    _install(DOCK)
    ws = build_ws(tmp_path)
    ws.look.set_theme("night", persist=False)    # the cache folds the install
    assert ws.look.layout_choices() == ("dock",)
    rows = _rows(ws)
    assert rows[rows.index("font_scale") + 1] == "layout_theme"
    ws.look.cycle_layout_theme(1)
    assert ws.system.get("layout_theme") == "dock"
    assert layout_base.bar_edge() == "bottom"
    assert ws.look.bars == (_bar("bottom", 22, ["menu", "separator", "zone"], [],
                                 ["close", "wifi", "battery", "clock"]),)
    assert ws.look.layout_notes == ("chips: not in this mode",)
    for name in ("forest", "platinum"):
        ws.look.set_theme(name)
        assert ws.look.layout_theme() == "dock" and layout_base.bar_edge() == "bottom"
    assert ws.system.get("theme") == "platinum"
    # THEME deletes the row and the active theme's bars come back
    ws.look.cycle_layout_theme(-1)
    assert ws.system.get("layout_theme") is None
    assert layout_base.bar_edge() == "top" and ws.look.bars == (TODAY,)


@pytest.mark.parametrize("value", ["nowhere", 7, "", None, ["dock"], "NIGHT"])
def test_the_reader_is_strict(tmp_path, value):
    ws = build_ws(tmp_path)
    ws.system.set("layout_theme", value)
    assert ws.look.layout_theme() is None
    ws.look.apply_layout()
    assert ws.look.bars == (TODAY,) and layout_base.bar_edge() == "top"


def test_an_uninstalled_pick_reads_as_absent_at_boot(tmp_path):
    _install(DOCK)
    ws = build_ws(tmp_path)
    ws.system.set("layout_theme", "dock")
    ws.look.apply_layout()
    assert layout_base.bar_edge() == "bottom"
    _install({"format": 1, "name": "other"})      # the one slot now holds another
    ws.load_system()
    assert ws.look.layout_theme() is None
    assert ws.system.get("layout_theme") == "dock"  # the row stays; it reads absent
    assert layout_base.bar_edge() == "top"


def test_the_settings_row_steps_through_the_choices(tmp_path):
    _install(DOCK)
    ws = build_ws(tmp_path)
    ws.look.set_theme("night", persist=False)
    sl = ws.settings_layer
    sl.set_msel = _rows(ws).index("layout_theme")
    sl.settings_adjust(1)
    assert ws.look.layout_theme() == "dock" and layout_base.bar_edge() == "bottom"
    assert _rows(ws).index("layout_theme") == sl.set_msel   # the row stays put
    sl.settings_adjust(1)
    assert ws.look.layout_theme() is None and layout_base.bar_edge() == "top"
    sl.settings_adjust(-1)
    assert ws.look.layout_theme() == "dock"


def test_a_mode_switch_resolves_the_catalogue_again(tmp_path):
    _install(DOCK)
    ws = build_desktop_ws(tmp_path)
    ws.look.set_theme("night", persist=False)
    ws.system.set("layout_theme", "dock")
    ws.look.apply_layout()
    assert ws.wm.mode == "desktop" and "chips" in ws.look.bars[0][2]
    ws.wm.enter_console()
    assert ws.wm.mode == "console" and "chips" not in ws.look.bars[0][2]
    assert ws.look.layout_notes == ("chips: not in this mode",)
    ws.wm.leave_console()
    assert "chips" in ws.look.bars[0][2] and layout_base.bar_edge() == "bottom"


# One fixture layout that triggers each drop of §10 at once: a widget outside
# the console catalogue (`chips`), one that does not fit its bar (`calendar`)
# and a bar on an edge this step does not place (`left`).
FIXTURE = {"format": 1, "name": "drops", "layout": {"bars": [
    {"edge": "top", "left": ["zone", "chips", "calendar"],
     "right": ["clock", "close", "menu", "wifi", "battery"]},
    {"edge": "left", "size": 40, "left": ["title"]}]}}
VERTICAL_ZONE = {"format": 1, "name": "vzone", "layout": {"bars": [
    {"edge": "top", "right": ["clock", "close", "menu"]},
    {"edge": "left", "size": 40, "left": ["zone"]}]}}


def _golden_ws(cfg, carts):
    from runtime import host_app
    ws = host_app.build_workstation(str(carts), sys_size=cfg["sys_size"],
                                    font_scale=cfg["font_scale"], windowed=cfg["windowed"],
                                    panel_diagonal_in=cfg["diagonal_in"])
    if cfg["windowed"]:
        ws.open_desk()
        if cfg.get("console"):
            ws.wm.enter_console()
    return ws


@pytest.mark.parametrize("config_name", sorted(CONFIGS))
def test_the_fixture_layouts_drop_as_specified_on_every_config(tmp_path, config_name):
    cfg = CONFIGS[config_name]
    ws = _golden_ws(cfg, tmp_path / "carts")
    desktop = ws.wm.mode == "desktop"
    _install(FIXTURE)
    ws.look.set_theme(ws.look.theme_name, persist=False)
    ws.system.set("layout_theme", "drops")
    ws.look.apply_layout()
    bars, notes = ws.look.bars, ws.look.layout_notes
    want_left = ("zone", "chips") if desktop else ("zone",)
    assert bars == (TODAY[:2] + (want_left,) + TODAY[3:],), config_name
    want = ["left bar: not on this tier"]
    if not desktop:
        want.append("chips: not in this mode")
    want.append("calendar: does not fit the top bar")
    assert list(notes) == want
    _install(VERTICAL_ZONE)
    ws.look.set_theme(ws.look.theme_name, persist=False)
    ws.system.set("layout_theme", "vzone")
    ws.look.apply_layout()
    assert ws.look.bars == (TODAY,)
    assert ws.look.layout_notes == ("left bar: not on this tier", "no zone after the drops",
                                    "replaced by the default")
    assert layout_base.bar_edge() == "top"
