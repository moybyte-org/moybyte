#!/usr/bin/env python3
"""Draws every picture `index.txt` names, and writes `index.txt`.

`mockups.py` is the renderer and the base scenes (each theme's home and
desk, the Calc sheet, the type specimen); `rounds/round7.py` to
`rounds/round12.py` are layered over it in order, each patching the one
before, and draw the icon sets, the editors, the system and pause menus,
Settings, Appearance, the handheld Calc, the console home, the wallpaper
scenes, the sheets, and the Editor's desk layouts and handheld tabs
(round 12, `EDITOR_LAYOUT.md`); `rounds/round14.py` adds the three
readings of maximise and the resizable panes (`MAXIMISE_VARIANTS.md`). The icon sets' sources live in native/moy_ui/icons/
(tools/make_icons.py builds the shipped masks from them).

  pip install resvg-py==0.5.0   # the SVG rasteriser, into any path
  .venv/bin/python docs/media/look_2026-10/draw.py

LOOK_OUT=DIR draws into DIR instead. The fonts download once (mockups.py).
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "rounds"))
import round12 as R12  # noqa: E402
import round14 as R14  # noqa: E402

M = R12.M


def main():
    del M.INDEX[:]
    for T in M.VARIANTS + (M.CONTRAST, M.CONTRAST_LIGHT) + M.RICED + M.STYLES:
        M.hh_home(T).save(f"{T['key']}-{T['mode']}_handheld_home",
                          f"Handheld home, Games tab, carousel on Sky Run; {T['name']}", x3=True)
        M.desktop(T).save(f"{T['key']}-{T['mode']}_desktop",
                          f"Desk: Calc focused, Files behind, bar; {T['name']}")
    M.calc_sheet()
    M.specimen()
    src = R12.scenes()
    R14.scenes()
    with open(os.path.join(R12.R7.OUT, "index.txt"), "w") as fh:
        fh.write("\n".join(M.INDEX) + "\n")
    print("SVG source bytes per set:", src)
    M.contrast_report()
    M.flash_report()


if __name__ == "__main__":
    main()
