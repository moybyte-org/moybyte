"""The handheld home shows a theme's hero at one side in every mode
(docs/look_2026-10.md step 17): a mode whose faces are taller (Moy Day's
against Night's) takes the room from the gaps around the caption, never a
clean step off the card (`CarouselLauncher._geom_hh`)."""

import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tests"))

import test_shell_goldens as g                          # noqa: E402


def _hero(tmp_path, config, theme, mode):
    import moy_ui
    moy_ui.theme_uninstall()
    try:
        cfg = dict(g.CONFIGS[config], variant=mode, theme=theme)
        ws = g._build(cfg, str(tmp_path / ("%s_%s_%s" % (config, theme, mode))))
        ws.go_home()
        geom = ws.launcher._geom()
        assert ws.launcher.handheld(), config
        return geom[2], geom[1], geom[8]
    finally:
        moy_ui.theme_uninstall()


@pytest.mark.parametrize("config", ["tdeck_320x240_fs1_dark", "guition_480x320_fs1_dark"])
@pytest.mark.parametrize("theme", ["moy", "blocks", "contrast"])
def test_every_mode_of_a_theme_keeps_one_hero_side(tmp_path, config, theme):
    dark, band, foot = _hero(tmp_path, config, theme, "dark")
    light, _band, lfoot = _hero(tmp_path, config, theme, "light")
    assert dark == light, (theme, config, dark, light)
    for side, f in ((dark, foot), (light, lfoot)):
        # the caption and its metadata end over the hint strip
        meta_y, meta_h, hints = f[3], f[4], f[5]
        assert meta_y + meta_h <= hints[1], (theme, config)
