"""The browser draws what the boards draw: each of its two arrangements lays
out on the metric class of the board whose arrangement it is, and its image
carries the faces and the icon sets those boards freeze, so a theme's fonts
and its icon set resolve in the wasm as they do on the glass.

The handheld 320x240 is the T-Deck's (no glass declared, the handheld's
class); the 1024x600 desk is the Waveshare P4's (its 7-inch glass, the desk's
class: `web_boot.DESK_DIAGONAL_IN`). `tools/make_font.py module` writes each
image's `fonts_data` from its board.toml (`font_tier`, else `tier`); the
module the browser freezes is held here against each board's, and
`iconsets.carried` -- what the bar, the Editor's ladder and the C parts draw
from -- is evaluated under each image's module for every seed theme in both
variants. tests/test_editor_ladder_icons.py and tests/test_web_fonts.py run
the real wasm; so does the last test here, which holds the console page's
achievements to a board's (the one-cart player keeps none).
"""

import glob
import json
import os
import re
import subprocess
import sys
import tempfile
import types
from pathlib import Path

import pytest

from runtime import fontset, iconsets, layout_base
from ws_helpers import build_ws

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from tools import board_config, make_font  # noqa: E402

FW = ROOT / "firmware"
WEB = FW / "web_runner"
TDECK = FW / "lilygo_t_deck_plus_mainline"
P4 = FW / "esp32_p4_wifi6_touch_lcd_7b"
# Every board that freezes a font set: the console boards, the browser aside.
CONSOLES = sorted(Path(p).parent for p in glob.glob(str(FW / "*" / "build.sh"))
                  if "make_font.py\" module" in Path(p).read_text()
                  and Path(p).parent != WEB)
SEEDS = sorted(Path(p).stem for p in glob.glob(str(ROOT / "native" / "moy_ui" / "themes" / "*.json")))
VARIANTS = ("dark", "light")
# The browser's arrangements: (the board whose arrangement it is, the glass
# web_boot declares for it, the font tier of its class).
ARRANGEMENTS = {"handheld": (TDECK, None, "handheld"),
                "desk": (P4, "DESK_DIAGONAL_IN", "desktop")}


def _image(board):
    """The `fonts_data` board `board`'s image freezes, as a module."""
    ns = types.ModuleType("fonts_data")
    src = make_font.render_module(make_font.board_fonts(str(board)))
    exec(compile(src, "fonts_data", "exec"), ns.__dict__)
    return ns


def _tiers(board):
    t = make_font.board_fonts(str(board))
    return (t,) if isinstance(t, str) else t


def _web_glass(name):
    """The number web_boot declares as the desk's glass."""
    m = re.search(r"^%s = ([0-9.]+)$" % name, (WEB / "web_boot.py").read_text(), re.M)
    assert m, "web_boot no longer declares %s" % name
    return float(m.group(1))


def test_the_console_boards_are_found():
    names = sorted(b.name for b in CONSOLES)
    assert names == ["esp32_p4_wifi6_touch_lcd_7b", "guition_jc3248w535",
                     "guition_jc8012p4a1c", "lilygo_t_deck_plus_mainline"], names


@pytest.mark.parametrize("arrangement", sorted(ARRANGEMENTS))
def test_each_arrangement_lays_out_on_its_boards_class(arrangement):
    board, const, tier = ARRANGEMENTS[arrangement]
    declared = board_config.load(board).get("panel", {}).get("diagonal_in")
    glass = _web_glass(const) if const else None
    assert glass == declared, (arrangement, glass, declared)
    cls = layout_base.class_of(glass)
    assert cls == layout_base.class_of(declared)
    assert (cls == layout_base.DESK) == (tier == "desktop")
    assert _tiers(board) == (tier,)
    assert tier in _tiers(WEB), "the browser's image lacks the %s faces" % tier


def test_the_desk_arrangement_declares_its_glass_to_the_workstation():
    src = (WEB / "web_boot.py").read_text()
    assert "panel_diagonal_in=DESK_DIAGONAL_IN" in src
    assert src.count("panel_diagonal_in=") == 1, "only the desk declares a glass"


def test_the_browser_carries_what_its_boards_freeze():
    web = _image(WEB)
    faces, icons = set(), set()
    for board in CONSOLES:
        if not set(_tiers(board)) <= set(_tiers(WEB)):
            continue
        img = _image(board)
        assert set(img.FACES) <= set(web.FACES), board.name
        for name, data in img.ICONS.items():
            assert web.ICONS.get(name) == data, (board.name, name)
        faces |= set(img.FACES)
        icons |= set(img.ICONS)
    assert sorted(web.FACES) == sorted(faces), "the browser carries a face no board does"
    assert sorted(web.ICONS) == sorted(icons), "the browser carries an icon file no board does"
    assert "system_icons.moygfx" in web.ICONS


@pytest.fixture(scope="module")
def theme_specs(tmp_path_factory):
    """{(theme, variant): the icon set its look names}, uncarried."""
    ws = build_ws(tmp_path_factory.mktemp("parity"))
    out = {}
    for name in SEEDS:
        for variant in VARIANTS:
            ws.look.set_theme(name, persist=False, variant=variant)
            out[(name, variant)] = iconsets.spec(ws.look.live.look)
    return out


def _carried(monkeypatch, board, specs):
    monkeypatch.setattr(fontset, "_data", _image(board))
    monkeypatch.setattr(iconsets, "_PIXEL", [])
    return {k: iconsets.carried(s) for k, s in specs.items()}


@pytest.mark.parametrize("arrangement", sorted(ARRANGEMENTS))
def test_every_seed_theme_draws_its_boards_icon_set_in_the_browser(
        monkeypatch, theme_specs, arrangement):
    assert SEEDS == ["blocks", "contrast", "moy"], SEEDS    # a new seed joins this net
    assert theme_specs[("moy", "dark")] == ("smooth", "default", None)
    board = ARRANGEMENTS[arrangement][0]
    on_glass = _carried(monkeypatch, board, theme_specs)
    web = _carried(monkeypatch, WEB, theme_specs)
    assert web == on_glass
    assert web[("moy", "dark")] == ("smooth", "default", None)



@pytest.mark.skipif(not os.environ.get("MOYBYTE_WEB_E2E"),
                    reason="MOYBYTE_WEB_E2E not set (boots the built wasm console)")
def test_the_browser_console_awards_achievements_as_a_board_does():
    from web_e2e import require
    require()
    if not (WEB / "dist" / "micropython.mjs").exists():
        pytest.skip("no built firmware/web_runner/dist")
    py = ("import json, web_boot\n"
          "_n0 = _ws.ach.count()\n"
          "web_boot.open_cart('Star Catcher')\n")
    report = ("print('ACH ' + json.dumps({'before': _n0, 'after': _ws.ach.count(),"
              " 'toast': _ws.ach.toast is not None}))\n")
    steps = [{"frames": 30}, {"py": py}, {"frames": 10}, {"py": report}]
    with tempfile.TemporaryDirectory() as tmp:
        scenario = Path(tmp) / "ach.json"
        scenario.write_text(json.dumps({"size": [320, 240], "windowed": False, "steps": steps}))
        r = subprocess.run(["node", "pageshot.mjs", str(scenario), str(Path(tmp) / "shots")],
                           cwd=WEB, capture_output=True, text=True, timeout=300)
    assert r.returncode == 0 and "Traceback" not in r.stdout, r.stdout[-3000:] + r.stderr[-500:]
    line = [ln for ln in r.stdout.splitlines() if "ACH " in ln]
    assert line, r.stdout[-3000:]
    got = json.loads(line[0].split("ACH ", 1)[1])
    assert got["after"] > got["before"], got     # First Steps, as on a board
