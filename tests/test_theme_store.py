"""Theme packages in the store (docs/theming_2026-09.md §9, phase 6 step 2).

A theme is a cart folder, `<name>.moy`, whose manifest says `"type":
"theme"` and `"runtime": "theme"`: data the loader validates, never a cart
that runs. The console lists it with the seeds (`ws.look.theme_names()`),
resolves it from its bytes without installing it (`moy_ui.theme_resolve`),
installs it when it is picked, reads its fonts from its own folder, and
boots the default seed with the loader's note, once, when it will not load.
The home, the Make tab and the projects list never show one; Get Carts
installs it as a cart, and its verbs are USE and REMOVE.

The fixture package is tests/fixtures/themes/dusk.moy: standalone, two
fonts from its folder, a frame, metrics and surfaces of its own.
"""

import json
import shutil
from pathlib import Path

import pytest

import runtime  # noqa: F401  (registers the host's moy_ui)
from ws_helpers import build_ws, build_desktop_ws
import moy_ui
from runtime import appearance, chrome

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "themes" / "dusk.moy"
NAME = "dusk"


@pytest.fixture(autouse=True)
def _seeds_only():
    yield
    moy_ui.theme_uninstall()
    chrome.adopt_packages((), None)


def _put(ws, src=None, name=NAME):
    """Copy the fixture into ws's store as `<name>.moy` (its theme.json
    replaced by `src` when given) and rescan."""
    dst = Path(ws.carts_root) / (name + ".moy")
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(FIXTURE, dst)
    if src is not None:
        (dst / "theme.json").write_bytes(src)
    ws.carts.rescan()
    return dst


def _src(**kw):
    pkg = {"format": 1, "name": NAME, "colors": {"dark": {"panel": 2}}}
    pkg.update(kw)
    return json.dumps(pkg).encode()


def test_a_package_is_listed_with_the_seeds_and_nowhere_a_cart_is(tmp_path):
    ws = build_ws(tmp_path)
    _put(ws)
    assert ws.look.theme_names() == chrome.THEME_NAMES + (NAME,)
    assert NAME not in chrome.THEME_NAMES
    for tab in ("games", "mine", "apps", "make"):
        assert not [c for c in ws._launcher_items(ws.carts.all, tab)
                    if c.get("type") == "theme"], tab
    from runtime.shell_servers import NavServer
    assert not [c for c in NavServer(ws).projects(None) if c.get("type") == "theme"]


def test_picking_it_installs_it_with_its_folders_fonts(tmp_path):
    ws = build_ws(tmp_path)
    _put(ws)
    ws.look.set_theme(NAME)
    assert ws.look.theme_name == NAME and moy_ui.theme_installed() == NAME
    assert ws.system.get("theme") == NAME
    src = (FIXTURE / "theme.json").read_bytes()
    got = moy_ui.theme_resolve(src)
    assert got[0] == 0
    for light, variant in ((False, "dark"), (True, "light")):
        want = got[4 if light else 3][0]
        assert moy_ui.theme(NAME, light)[0] == want
        row = chrome.theme_colors(NAME, variant)
        assert [row[k] for k in moy_ui.tokens()] == [
            bool(v) if k in ("surface_light", "bar_light", "band_light") else v
            for k, v in zip(moy_ui.tokens(), want)]
    assert (NAME, "tiny5.fnt") in appearance._FONTS
    assert moy_ui.font("ui")[0] == appearance._FONTS[(NAME, "tiny5.fnt")]
    ws.frame(1 / 30)


def test_a_row_naming_the_package_boots_it(tmp_path):
    ws = build_ws(tmp_path)
    _put(ws)
    ws.look.set_theme(NAME)
    moy_ui.theme_uninstall()
    again = build_ws(tmp_path)
    assert again.look.theme_name == NAME
    assert moy_ui.theme_installed() == NAME


def test_the_cards_resolve_a_package_without_installing_it(tmp_path):
    ws = build_ws(tmp_path)
    _put(ws)
    ws.look.set_theme("berry")
    assert moy_ui.theme_installed() is None
    row = chrome.theme_colors(NAME, "dark")
    assert row["panel"] == 62
    assert ws.look._theme_layout(NAME)[0] is False
    assert moy_ui.theme_installed() is None


@pytest.mark.parametrize("src,why", [
    (b'{"format": 1, "name": "other"}', "name"),
    (_src(inherits="berry"), "chain"),
    (_src(format=9), "newer"),
    (b'{"format": 1, "name": "dusk", ', "syntax"),
    (_src(colors={"dark": {"panel": 2}}, pad="x" * 5000), "big"),
])
def test_a_package_that_will_not_load_boots_the_default_with_one_note(
        tmp_path, src, why):
    ws = build_ws(tmp_path)
    _put(ws, src)
    assert NAME not in ws.look.theme_names()
    ws.system.set("theme", NAME)
    ws.look.set_theme(NAME)
    assert ws.look.theme_name == chrome.DEFAULT_THEME
    assert moy_ui.theme_installed() is None, "a refused package never reaches the slot"
    assert ws._notice is not None and ws._notice[2] == "warn"
    assert ws._notice[1] == moy_ui.s("theme.why_" + why)
    ws._notice = None
    ws.look.set_theme(NAME)
    assert ws._notice is None, "the note is shown once"
    again = build_ws(tmp_path)
    assert again.look.theme_name == chrome.DEFAULT_THEME


def test_the_fuzz_corpus_never_reaches_the_slot(tmp_path):
    from test_theme_loader import _corpus
    ws = build_ws(tmp_path)
    dst = _put(ws)
    for src in _corpus(120):
        src = src.replace(b'"fz"', b'"dusk"')
        (dst / "theme.json").write_bytes(src)
        ws.carts.rescan()
        ws.look.set_theme(NAME, persist=False)
        got = moy_ui.theme_resolve(src)
        ok = got[0] == 0 and got[2] == NAME
        assert (ws.look.theme_name == NAME) == ok
        assert ok or moy_ui.theme_installed() in (None, NAME)
        if not ok:
            assert ws.look.theme_name == chrome.DEFAULT_THEME


def test_a_changed_package_is_re_taken_and_a_removed_one_falls_back(tmp_path):
    ws = build_ws(tmp_path)
    dst = _put(ws)
    ws.look.set_theme(NAME)
    gen = ws.look.look_gen
    (dst / "theme.json").write_bytes(_src(colors={"dark": {"panel": 3}}))
    ws.carts.rescan()
    assert ws.theme_colors["panel"] == 3 and ws.look.look_gen > gen
    gen = ws.look.look_gen
    ws.carts.rescan()
    assert ws.look.look_gen == gen, "an unchanged package is not re-installed"
    shutil.rmtree(dst)
    ws.carts.rescan()
    assert ws.look.theme_name == chrome.DEFAULT_THEME
    assert ws.system.get("theme") == chrome.DEFAULT_THEME
    assert NAME not in ws.look.theme_names()


def test_the_desk_draws_a_package(tmp_path):
    ws = build_desktop_ws(tmp_path)
    _put(ws)
    ws.look.set_theme(NAME)
    ws.open_desk()
    for _ in range(3):
        ws._dirty = True
        ws.frame(1 / 30)
    assert ws.look.theme_name == NAME


def test_contrast_is_a_seed_that_reads_in_both_variants():
    from runtime import ui
    assert "contrast" in chrome.THEME_NAMES
    for light in (False, True):
        row, bad, rc = moy_ui.theme("contrast", light)
        assert rc == 0 and bad == 0
    try:
        ui.use_shape("contrast")
        for variant in chrome.THEME_VARIANTS:
            th = chrome.theme_colors("contrast", variant)
            assert ui.pattern_unreadable(th) == []
            assert (th["panel"], th["ink"], th["accent"]) == (0, 7, 10)
    finally:
        ui.use_shape(None)


def test_files_lists_a_package_as_a_theme_whose_door_is_appearance(tmp_path):
    ws = build_ws(tmp_path)
    files = ws._apps_by_id["files"]
    assert ws.open_app(files)
    files.open()
    assert "themes" not in [k for k, _l in files._shown_kinds()]
    _put(ws)
    files.open()
    assert ("themes", "THEMES") in files._shown_kinds()
    files._enter_rows("themes")
    assert files._rows == ("dusk.moy",)
    assert files.door("dusk.moy", "themes") == ("appearance", "dusk.moy")
    files._enter_rows("projects")
    assert "dusk.moy" not in files._rows
    assert files.route("dusk.moy", "themes") == "appearance"


def test_an_installed_package_as_the_carts_repository_ships_it(tmp_path):
    """The fixture is shaped as moybyte-org/carts publishes a theme: the
    manifest, theme.json, its font, the font's licence beside it (`X.txt`
    beside `X.fnt`), the theme's licence naming the font, and a cover. The
    roster lists it once, as a theme; Files lists it under THEMES; the
    store's sweep leaves it whole; picking it loads its font and nothing
    else of the folder."""
    names = sorted(p.name for p in FIXTURE.iterdir())
    assert names == ["LICENSES.txt", "cover.png", "manifest.json", "theme.json",
                     "tiny5.fnt", "tiny5.txt"]
    for fnt in FIXTURE.glob("*.fnt"):
        assert "SIL OPEN FONT LICENSE Version 1.1" in fnt.with_suffix(".txt").read_text()
        assert fnt.name in (FIXTURE / "LICENSES.txt").read_text()
    ws = build_ws(tmp_path)
    dst = _put(ws)
    rows = [c for c in ws.carts.all
            if str(c.get("path", "")).replace("\\", "/").endswith("/dusk.moy")]
    assert len(rows) == 1 and rows[0].get("type") == "theme"
    assert ws.look.theme_names().count(NAME) == 1
    files = ws._apps_by_id["files"]
    assert ws.open_app(files)
    files.open()
    files._enter_rows("themes")
    assert files._rows == ("dusk.moy",)
    from runtime import moy_seed
    before = {p.name: p.read_bytes() for p in dst.iterdir()}
    moy_seed.sweep_store(ws.carts_root)
    assert {p.name: p.read_bytes() for p in dst.iterdir()} == before
    ws.carts.rescan()
    ws.look.set_theme(NAME)
    assert ws.look.theme_name == NAME and moy_ui.theme_installed() == NAME
    assert [k for k in appearance._FONTS if k[0] == NAME] == [(NAME, "tiny5.fnt")]
