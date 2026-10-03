# Theming mockups (2026-09-27)

The pictures behind `docs/theming_2026-09.md` §1: the look the owner chose
(D1) and the home (D7), next to the shell as it rendered before. They are
kept so a later implementation can be compared against what was decided.

**The `before_*` files are engine renders.** They were captured through the
shell golden harness (`tests/test_shell_goldens.py`) on 2026-09-27: the
windowed desk at 1024×600 and the Library at 480×320, both in the `night`
theme.

**Every other file is a hand-drawn mockup, not an engine render.** Each was
drawn at native size in MOY64 colours only, with bitmap fonts (Tiny5), the
real 16×16 system icons (`chrome._ICON_ART`), and frames captured from the
seed carts as covers. They show the look, not finished layouts.

| file | what it shows |
|---|---|
| `before_desk_1024x600.png` | the windowed desk with Calc, as rendered before this design |
| `before_library_480x320.png` | the Library shelf at 480×320, as rendered before |
| `look_light_desk_480x320.png`, `look_dark_desk_480x320.png` | D1 on the desk: outlined windows with hard shadows, a left title with the app's stripe and a square close, icon tiles with the label under, Moy's night |
| `look_light_library_480x320.png`, `look_dark_library_480x320.png` | D1 on a library window: the same parts on a card grid |
| `home_light_480x320.png`, `home_dark_480x320.png` | D7: the tabbed cover-carousel home |
| `home_light_320x240.png`, `home_dark_320x240.png` | D7 on the T-Deck: the same home fits at 320×240 |
| `home_wallpaper_ok_320x240.png` | a home wallpaper (Sakura) that stays readable: every piece of chrome is solid |
| `home_wallpaper_clash_320x240.png` | a home wallpaper (Space Desktop) that clashes: its own text and character sit under the carousel (§4.9, §12.1) |

Light and dark are the same drawing with a different colour table (D1).
