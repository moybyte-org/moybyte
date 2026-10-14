"""The shell layout's resolver (docs/shell_layout_2026-10.md §9-§11, §13 steps 4
and 5).

The loader's half (caps, clamps, the required set on the data, inheritance)
is `tests/test_theme_loader.py`'s. Here: the composer's drops per tier and
mode, the 75% floor, the required set again after the drops, §6's worked
examples resolved per tier, the user's `layout_theme` pick (its precedence,
its strict reader, the Settings row) and the host's bars following the
resolved layout through `Workstation.place_bars`. What the bars draw and
where their taps go is `tests/test_bars.py`'s.
"""

import json
import random

import pytest

import runtime  # noqa: F401  (registers the host's moy_ui)
import moy_ui
from runtime import layout_base, shell_layout
from runtime.shell_layout import FALLBACK, resolve
from runtime.wm import FullscreenStackWM
from runtime.wm_windowed import WindowedWM
from test_shell_goldens import CONFIGS
from ws_helpers import build_desktop_ws, build_ws

TODAY = FALLBACK[0]
# Today's bar in console mode: the taskbar's chips have no value there.
TODAY_C = TODAY[:3] + ((),) + TODAY[4:]
ALL = shell_layout.WIDGETS
DESKTOP = WindowedWM.LAYOUT_CATALOGUE["desktop"]
CONSOLE = FullscreenStackWM.LAYOUT_CATALOGUE["console"]
EDGES = shell_layout.EDGES
TOP_BOTTOM = ("top", "bottom")


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
    "p4_desktop": (1024, 600, 2, DESKTOP),
    "p4_console": (1024, 600, 2, CONSOLE),
    "guition_p4_desktop": (1280, 800, 2, DESKTOP),
}


def _today(tier):
    return TODAY if TIERS[tier][3] is DESKTOP else TODAY_C


def _res(bars, tier, edges=EDGES, cat=None):
    w, h, cs, tcat = TIERS[tier]
    return resolve(bars, (TODAY,), w, h, cs, cat or tcat, edges)


@pytest.mark.parametrize("tier", sorted(TIERS))
def test_the_worked_examples_resolve_per_tier(tier):
    desktop = TIERS[tier][3] is DESKTOP
    if desktop:
        assert _res(EXAMPLES[1], tier) == ((TODAY,), ())
    else:
        # `chips` has no value in console mode: dropped, the bar stays
        assert _res(EXAMPLES[1], tier) == ((TODAY_C,), ("chips: not in this mode",))
    bars, notes = _res(EXAMPLES[2], tier)
    if desktop:
        assert (bars, notes) == (EXAMPLES[2], ())
    else:
        assert bars == (_bar("bottom", 22, ["menu", "separator", "zone"], [],
                             ["close", "wifi", "battery", "clock"]),)
        assert notes == ("chips: not in this mode",)
    assert _res(EXAMPLES[3], tier) == (EXAMPLES[3], ())
    assert _res(EXAMPLES[4], tier) == (EXAMPLES[4], ())
    assert _res(EXAMPLES[6], tier) == (EXAMPLES[6], ())


def test_a_docked_calendar_resolves_where_the_floor_holds():
    # `calendar` is in both modes' catalogues: on the P4s example 5's right
    # bar clears the floor (76% of the width) and docks the month grid; on a
    # 320- or 480-wide glass it fails the floor and the default stands in --
    # one rule, no board exception.
    assert "calendar" in DESKTOP and "calendar" in CONSOLE
    for tier in ("p4_desktop", "p4_console", "guition_p4_desktop"):
        bars, notes = _res(EXAMPLES[5], tier)
        assert bars == (_today(tier), EXAMPLES[5][1]), tier
    for tier in ("tdeck", "guition_s3"):
        bars, notes = _res(EXAMPLES[5], tier)
        assert bars == (_today(tier),) and "replaced by the default" in notes, tier
        assert "the bars leave the content under 75% of the glass" in notes


def test_the_rule_runs_again_after_the_drops():
    # The loader passes `zone` in a vertical bar; the composer drops it there
    # and the required set fails, so the default stands in.
    lay = (_bar("top", 18, ["menu", "close"]), _bar("left", 40, ["zone"]))
    bars, notes = _res(lay, "p4_desktop")
    assert bars == (TODAY,)
    assert notes == ("zone: not on a left bar", "no zone after the drops",
                     "replaced by the default")
    # a tier that places no side bar drops the bar first
    bars, notes = _res(lay, "p4_desktop", TOP_BOTTOM)
    assert bars == (TODAY,) and notes[0] == "left bar: not on this tier"
    # a required widget that does not fit is a drop like any other
    lay = (_bar("top", 18, ["text"], [], ["clock", "wifi", "battery", "zone", "menu", "close"],
                text=["x" * 24]),)
    bars, notes = _res(lay, "tdeck")
    assert bars == (TODAY_C,)
    assert notes[:2] == ("zone: does not fit the top bar", "no zone after the drops")


def test_a_widget_that_does_not_fit_its_bar_is_dropped():
    lay = (_bar("top", 18, ["zone", "calendar", "title"], [], ["menu", "close", "clock"]),)
    bars, notes = _res(lay, "tdeck", cat=ALL)
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
    assert bars == (TODAY_C,) and notes[0] == "the bars leave the content under 75% of the glass"
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
        edges = rng.choice((TOP_BOTTOM, EDGES))
        got, notes = resolve(tuple(bars), (TODAY,), w, h, cs,
                             rng.choice((ALL, DESKTOP, CONSOLE)), edges)
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
    ws = build_ws(tmp_path / "a")
    assert ws.look.bars == (TODAY_C,)
    assert ws.look.layout_notes == ("chips: not in this mode",)
    ws2 = build_desktop_ws(tmp_path / "b")
    assert ws2.look.bars == (TODAY,) and ws2.look.layout_notes == ()
    for ws in (ws, ws2):
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
    assert layout_base.bar_edge() == "top" and ws.look.bars == (TODAY_C,)


@pytest.mark.parametrize("value", ["nowhere", 7, "", None, ["dock"], "NIGHT"])
def test_the_reader_is_strict(tmp_path, value):
    ws = build_ws(tmp_path)
    ws.system.set("layout_theme", value)
    assert ws.look.layout_theme() is None
    ws.look.apply_layout()
    assert ws.look.bars == (TODAY_C,) and layout_base.bar_edge() == "top"


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
# the console catalogue (`chips`), one whose cell does not fit an 18-row bar
# (`calendar`), one with no cell on a side bar (`title`) and one that does
# not fit its bar (a `text` wider than the 16-row side bar).
FIXTURE = {"format": 1, "name": "drops", "layout": {"bars": [
    {"edge": "top", "left": ["zone", "chips", "calendar"],
     "right": ["clock", "close", "menu", "wifi", "battery"]},
    {"edge": "left", "size": 16, "left": ["title", "text"], "text": ["abcdefgh"]}]}}
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
    assert bars == (TODAY[:2] + (want_left, ()) + TODAY[4:],
                    _bar("left", 16)), config_name
    want = [] if desktop else ["chips: not in this mode"]
    want += ["calendar: does not fit the top bar", "title: not on a left bar",
             "text: does not fit the left bar"]
    assert list(notes) == want
    _install(VERTICAL_ZONE)
    ws.look.set_theme(ws.look.theme_name, persist=False)
    ws.system.set("layout_theme", "vzone")
    ws.look.apply_layout()
    assert ws.look.bars == ((TODAY if desktop else TODAY_C),)
    want = ("zone: not on a left bar", "no zone after the drops", "replaced by the default")
    assert ws.look.layout_notes == want + (() if desktop else ("chips: not in this mode",))
    assert layout_base.bar_edge() == "top"
