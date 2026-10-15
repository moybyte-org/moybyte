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
    assert "calc_all_themes" in kept and "daylight-light_desktop" in kept
    assert "daylight-light_handheld_home" not in kept


def test_scenes_render_and_pair(tmp_path):
    rows = L.run(str(tmp_path), ["daylight-light_handheld_home",
                                 "moy-dark_handheld_home"], quiet=True)
    assert sorted(r[0] for r in rows) == ["daylight-light_handheld_home",
                                          "moy-dark_handheld_home"]
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
    L.run(str(tmp_path), ["daylight-light_handheld_home"], quiet=True)
    assert (cover_cache._COVER_SLICE_MS, g._CLOCK) == before
