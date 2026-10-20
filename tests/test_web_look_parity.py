"""The browser draws what the boards draw: its image carries the faces and
the icon sets the console boards of its metric class freeze, so a theme's
fonts and its icon set resolve in the wasm as they do on the glass.

`tools/make_font.py module` writes each image's `fonts_data` from its
board.toml (`font_tier`, else `tier`); the module the browser freezes is held
here against the boards' (the handheld arrangement's against the T-Deck's and
the Guition S3's), and `iconsets.carried` -- what the bar, the Editor's ladder
and the C parts draw from -- is evaluated under each image's module for every
seed theme in both variants. tests/test_editor_ladder_icons.py and
tests/test_web_fonts.py run the real wasm.
"""

import glob
import sys
import types
from pathlib import Path

import pytest

from runtime import fontset, iconsets
from ws_helpers import build_ws

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from tools import make_font  # noqa: E402

FW = ROOT / "firmware"
WEB = FW / "web_runner"
TDECK = FW / "lilygo_t_deck_plus_mainline"
# Every board that freezes a font set: the console boards, the browser aside.
CONSOLES = sorted(Path(p).parent for p in glob.glob(str(FW / "*" / "build.sh"))
                  if "make_font.py\" module" in Path(p).read_text()
                  and Path(p).parent != WEB)
SEEDS = sorted(Path(p).stem for p in glob.glob(str(ROOT / "native" / "moy_ui" / "themes" / "*.json")))
VARIANTS = ("dark", "light")


def _image(board):
    """The `fonts_data` board `board`'s image freezes, as a module."""
    ns = types.ModuleType("fonts_data")
    src = make_font.render_module(make_font.board_fonts(str(board)))
    exec(compile(src, "fonts_data", "exec"), ns.__dict__)
    return ns


def _tier(board):
    return make_font.board_fonts(str(board))


def test_the_console_boards_are_found():
    names = sorted(b.name for b in CONSOLES)
    assert names == ["esp32_p4_wifi6_touch_lcd_7b", "guition_jc3248w535",
                     "guition_jc8012p4a1c", "lilygo_t_deck_plus_mainline"], names


def test_the_browser_carries_what_its_class_of_board_freezes():
    web = _image(WEB)
    peers = [b for b in CONSOLES if _tier(b) == _tier(WEB)]
    assert TDECK in peers, "the browser's handheld arrangement is the T-Deck's"
    for board in peers:
        img = _image(board)
        assert sorted(web.FACES) == sorted(img.FACES), board.name
        assert sorted(web.ICONS) == sorted(img.ICONS), board.name
        for name, data in img.ICONS.items():
            assert web.ICONS[name] == data, (board.name, name)


def test_the_browser_carries_icon_sets():
    web = _image(WEB)
    assert "system_icons.moygfx" in web.ICONS
    assert any(n.endswith(".fnt") for n in web.ICONS), sorted(web.ICONS)


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


def test_every_seed_theme_draws_the_tdecks_icon_set_in_the_browser(monkeypatch, theme_specs):
    assert SEEDS == ["blocks", "contrast", "moy"], SEEDS    # a new seed joins this net
    assert theme_specs[("moy", "dark")] == ("smooth", "default", None)
    tdeck = _carried(monkeypatch, TDECK, theme_specs)
    web = _carried(monkeypatch, WEB, theme_specs)
    assert web == tdeck
    assert web[("moy", "dark")] == ("smooth", "default", None)
