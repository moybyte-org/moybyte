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
