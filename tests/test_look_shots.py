"""`tools/look_shots.py`, the 2026-10 look's review kit (docs/look_2026-10.md
step 1): every scene `docs/media/look_2026-10/index.txt` names has a shell
setup, and a scene renders from the real shell and pairs with its mockup."""

import os
import sys

from PIL import Image

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))

import look_shots as L                                  # noqa: E402


def test_every_scene_has_a_setup_and_known_steps():
    scenes = L.read_index()
    assert scenes, "index.txt names no scene"
    table = L._setup_table()
    steps = L.plan_steps()
    assert steps.get("1"), "the plan's step 1 lost its Landed mark"
    for name, _what, theme, _mode, _size in scenes:
        assert os.path.exists(os.path.join(L.MOCKUPS, name + ".png")), name
        assert L.screen_of(name) in table, \
            "%s has no setup in tools/look_shots.py" % name
        for s in L.scene_steps(name, theme):
            assert s in steps, "%s names step %s, not in the plan" % (name, s)


def test_a_step_keeps_the_scenes_it_targets():
    kept = [s[0] for s in L.select(L.read_index(), (), "22")]
    assert "calc_all_themes" in kept and "moy-light_desktop" in kept
    assert "moy-light_handheld_home" not in kept


def test_scenes_render_and_pair(tmp_path):
    rows = L.run(str(tmp_path), ["moy-light_handheld_home",
                                 "moy-dark_handheld_home"], quiet=True)
    assert sorted(r[0] for r in rows) == ["moy-dark_handheld_home",
                                          "moy-light_handheld_home"]
    index = (tmp_path / "index.txt").read_text()
    for name, built, _missing in rows:
        mock = Image.open(tmp_path / (name + ".mockup.png"))
        shot = Image.open(tmp_path / (name + ".render.png"))
        assert shot.size == mock.size == (320, 240)
        assert len(shot.getcolors(1 << 16)) > 4, "%s rendered blank" % name
        pair = Image.open(tmp_path / (name + ".pair.png"))
        assert pair.width == 2 * 320 + 8 and pair.height > 240
        big = Image.open(tmp_path / (name + ".pair@3x.png"))
        assert big.width == 2 * 960 + 8 and big.height > 720
        head = [ln for ln in index.splitlines() if ln.startswith(name + " ")]
        assert head and ("NOT BUILT YET" in head[0]) == (not built), name


def test_a_run_puts_the_pins_back(tmp_path):
    from runtime import cover_cache
    g = L._goldens()
    before = cover_cache._COVER_SLICE_MS, g._CLOCK
    L.run(str(tmp_path), ["moy-light_handheld_home"], quiet=True)
    assert (cover_cache._COVER_SLICE_MS, g._CLOCK) == before


def test_parts_draws_the_settings_scenes_with_the_parts_sheet(tmp_path):
    L.run(str(tmp_path / "shell"), ["moy-light_handheld_settings"], quiet=True)
    L.run(str(tmp_path / "parts"), ["moy-light_handheld_settings"], quiet=True, parts=True)
    index = (tmp_path / "parts" / "index.txt").read_text()
    assert "the parts sheet's settings page" in index
    a = Image.open(tmp_path / "shell" / "moy-light_handheld_settings.render.png")
    b = Image.open(tmp_path / "parts" / "moy-light_handheld_settings.render.png")
    assert a.tobytes() != b.tobytes(), "--parts draws the sheet, not the shell's screen"


# -- the kit's own scenes, checks and audit (round 2) -------------------------------

def test_every_kit_scene_has_a_config_and_a_kind():
    table = L._kit_table()
    assert table
    for screen, (config, setup, what, kind) in table.items():
        assert config in (L.HANDHELD, L.DESK, L.CONSOLE), screen
        assert callable(setup) and what, screen
        assert kind in ("theme", "profile"), screen
    themed = L.kit_scenes(kit=True)
    assert {(t, m) for _n, _s, t, m, _p in themed} == set(L.KIT_MODES)
    profiled = L.kit_scenes(kit=False, profiles=True)
    assert {p for _n, _s, _t, _m, p in profiled} == set(L.PROFILES)
    names = [s[0] for s in profiled]
    assert "moy-dark_handheld_wifi-password_input-tdeck" in names
    assert "moy-dark_handheld_settings_name-typing_input-pad" in names


def test_the_kit_renders_its_scenes_with_checks_and_an_audit(tmp_path):
    failed = []
    rows = L.run(str(tmp_path), ["contrast-dark_handheld_settings_focus-volume",
                                 "moy-dark_handheld_home_input-touch",
                                 "contrast-dark_handheld_sysmenu"],
                 quiet=True, kit=True, profiles=True, only_kit=True, audit_csv=True,
                 failed=failed)
    names = sorted(r[0] for r in rows)
    assert names == ["contrast-dark_handheld_settings_focus-volume",
                     "contrast-dark_handheld_sysmenu",
                     "moy-dark_handheld_home_input-touch"]
    assert failed == []
    kit = tmp_path / "kit"
    index = (kit / "index.txt").read_text()
    assert "check: one focus mark ok" in index
    assert "check: hero rim ok" in index and "check: clean cover ok" in index
    for name in names:
        assert Image.open(kit / (name + ".png")).size == (320, 240)
    csv = (kit / "contrast-dark_handheld_settings_focus-volume.audit.csv").read_text()
    assert csv.startswith("scene,pair,colour,against,ratio,floor,flag")
    assert "focused row part vs plate" in csv


def test_an_input_profile_moves_the_hints(tmp_path):
    L.run(str(tmp_path / "pad"), ["moy-dark_handheld_home"], quiet=True, inputs="pad")
    L.run(str(tmp_path / "tdeck"), ["moy-dark_handheld_home"], quiet=True)
    a = Image.open(tmp_path / "pad" / "moy-dark_handheld_home.render.png")
    b = Image.open(tmp_path / "tdeck" / "moy-dark_handheld_home.render.png")
    assert a.tobytes() != b.tobytes(), "--input draws that input's keys"
    assert a.crop((0, 0, 320, 200)).tobytes() == b.crop((0, 0, 320, 200)).tobytes(), \
        "only the hint strip moves with the input"


def test_the_checks_fail_what_round_2_found():
    assert L._clean(128) and L._clean(256) and L._clean(64) and L._clean(32)
    assert not L._clean(138) and not L._clean(96)
    assert round(L.contrast((255, 255, 255), (0, 0, 0)), 2) == 21.0
    assert round(L.contrast((255, 255, 255), (255, 230, 0)), 2) == 1.27
    assert L.bar_parity([("moy", "c", "dark", (35, 1)), ("moy", "c", "light", (32, 1))]) \
        == [("bar parity", False, "moy on c, modes dark/light")]
    assert L.bar_parity([("moy", "c", "dark", (35, 1)), ("moy", "c", "light", (35, 1))])[0][1]


def test_pads_measures_a_pills_ink():
    img = Image.new("RGB", (40, 12), (200, 200, 240))
    for x in range(5, 35):                  # ink from 5 to 34 in a pill 0..39
        img.putpixel((x, 6), (20, 20, 20))
    assert L.pads(img, (0, 0, 40, 12)) == (5, 5)
    for x in range(35, 39):                 # the ink runs on to the pill's end
        img.putpixel((x, 6), (20, 20, 20))
    assert L.pads(img, (0, 0, 40, 12)) == (5, 1)


def test_a_failed_check_fails_the_run(tmp_path, monkeypatch):
    monkeypatch.setattr(L, "checks", lambda ws, probe, img: [("clean cover", False, "138")])
    assert L.main([str(tmp_path), "moy-dark_handheld_home"]) == 1


# -- the audit picks real pairs (round 3's triage) ----------------------------------

def _glyph_row():
    """A 40x12 row: plate, a solid 3:1-missing pill, a black glyph-ish
    stroke with grey anti-aliasing beside it, all on white."""
    img = Image.new("RGB", (40, 12), (255, 255, 255))
    for x in range(2, 8):
        for y in range(2, 9):
            img.putpixel((x, y), (230, 226, 246))          # a faint pill
    for y in range(2, 10):
        img.putpixel((20, y), (0, 0, 0))                   # a stem
        img.putpixel((21, y), (128, 128, 128))             # its anti-aliasing
    return img


def test_the_audit_drops_anti_aliasing_and_holds_text_to_4_5():
    img = _glyph_row()
    inks = L._inks(img, (0, 0, 40, 12), (255, 255, 255))
    assert inks["solid"] == [(230, 226, 246)]
    assert inks["text"] == [(0, 0, 0)], "the grey between ink and ground is a blend"
    assert L._between((128, 128, 128), (0, 0, 0), (255, 255, 255))
    assert not L._between((230, 0, 0), (0, 0, 0), (255, 255, 255))
    assert L.TEXT_FLOOR == 4.5 and L.FLOOR == 3.0


def test_the_audit_cuts_a_box_to_what_shows():
    assert L._clip((0, 200, 320, 40), (0, 28, 320, 192)) == (0, 200, 320, 20)
    assert L._clip((0, 230, 320, 10), (0, 28, 320, 192)) is None


def test_a_pair_an_open_question_owns_is_no_flag():
    rows = [("p", "#292c52", "#181839", 1.28, 3.0, "D-05"),
            ("p", "#ffee62", "#ffe600", 1.07, 3.0, ""),
            ("p", "#000000", "#ffe600", 16.6, 3.0, "")]
    assert L.flagged(rows) == [rows[1]]


def test_the_kit_renders_the_card_menus_and_the_shelfs_buttons():
    table = L._kit_table()
    for screen, want in (("handheld_home_long-press", "touch"),
                         ("handheld_make_long-press", "touch"),
                         ("handheld_home_pad-x", "pad"), ("desktop_home_pad-up", "pad")):
        assert screen in table and L.KIT_INPUT[screen] == want
    assert "handheld_make_input" in table, "the Make tab under each input"
    assert "desktop_calc" in table


def test_a_long_press_opens_the_card_menu_and_the_check_says_so(tmp_path):
    failed = []
    L.run(str(tmp_path), ["moy-dark_handheld_home_long-press", "moy-dark_handheld_home_pad-x",
                          "moy-dark_desktop_home_pad-up"],
          quiet=True, kit=True, theme="moy", variant="dark", only_kit=True, failed=failed)
    assert failed == []
    index = (tmp_path / "kit" / "index.txt").read_text()
    assert "check: card menu ok (a long press on the card opens its verbs" in index
    assert "check: card menu ok (a pad's X opens its verbs" in index
    assert "check: shelf up ok" in index


def test_a_long_press_that_opens_nothing_fails_the_run(tmp_path, monkeypatch):
    from runtime import launcher_layer
    assert L.PRESS_MS > launcher_layer.LONG_PRESS_MS
    monkeypatch.setattr(L, "PRESS_MS", 200)              # a tap, not a long press
    failed = []
    L.run(str(tmp_path), ["moy-dark_handheld_home_long-press"], quiet=True, kit=True,
          theme="moy", variant="dark", only_kit=True, failed=failed)
    assert [f[1] for f in failed] == ["card menu"]


def test_the_audit_measures_round_3s_pairs(tmp_path):
    """The pairs round 3 found by eye are measured in the scenes that show
    them: the Mode segment on a focus plate (R3-04), the focused desk chip's
    label as text (R3-06), the Name row's mark against the row (R3-03) and
    the desk Calc's backspace against a digit (R3-09); a row under its floor
    is a FLAG unless an open question owns it."""
    L.run(str(tmp_path), ["contrast-dark_handheld_parts-appearance_focus-mode",
                          "moy-dark_desktop_calc", "moy-dark_handheld_settings_focus-name"],
          quiet=True, kit=True, only_kit=True, audit_csv=True)
    kit = tmp_path / "kit"

    def rows(name):
        return [ln.split(",") for ln in (kit / (name + ".audit.csv")).read_text().splitlines()[1:]]
    for name, pair in (("contrast-dark_handheld_parts-appearance_focus-mode",
                        "focused row part vs plate"),
                       ("moy-dark_desktop_calc", "bar chip label vs plate"),
                       ("moy-dark_desktop_calc", "desk backspace ink height vs digit cap"),
                       ("moy-dark_handheld_settings_focus-name", "focus mark height vs row")):
        got = [r for r in rows(name) if r[1] == pair]
        assert got, (name, pair)
        for r in got:
            under = float(r[4]) < float(r[5])
            assert r[6] == ("FLAG" if under and not r[7] else ""), r
    chip = [r for r in rows("moy-dark_desktop_calc") if r[1] == "bar chip label vs plate"]
    assert chip[0][5] == "4.5", "a label is text"


def test_a_mark_shorter_than_its_row_and_a_rim_off_its_cover_are_flags():
    assert L._measure("focus mark height vs row", 7, 22, 7 >= 22)[3] < 1.0
    assert L.flagged([L._measure("hero rim-to-cover gap", 5, 0, False)])
    assert not L.flagged([L._measure("hero rim-to-cover gap", 0, 0, True)])
