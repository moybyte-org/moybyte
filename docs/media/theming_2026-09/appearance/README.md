# Appearance mockups (2026-10-14)

The pictures behind theming phase 6's Appearance page
(`docs/theming_2026-09.md` §4.10 and §9, phase 6). **Every file is a
hand-drawn mockup, not an engine render**, drawn by `mockups.py` here
(`.venv/bin/python docs/media/theming_2026-09/appearance/mockups.py`): MOY64
only, Tiny5 and petme128, the real 16×16 system icons, the D1 parts. The
seeds' colours come from the host's resolver, and every derived look is
computed by the script's `derive()`, the §4.10 rule written out in Python;
it reports the readability pairs of every seed × accent × tint × variant on
each run.

320×240 is the T-Deck in console mode (the page and its pickers are
fullscreen pages); 1024×600 is the Waveshare P4 as shipped, chrome 2×, font
1× (Appearance is a window, the pickers are popovers).

| file | what it shows |
|---|---|
| `appearance_320x240_dark.png`, `appearance_320x240_light.png` | the page on the T-Deck, `night` dark and light: THEME, MODE, ACCENT, TINT, WALLPAPER, WINDOW STYLE, FONT, EDIT FILE… (no CHROME SIZE: one chrome scale) |
| `appearance_1024x600_dark.png` | the page as a window on the P4 desk, `night` dark: the THEME grid inline, then the rows; also the "before" of the accent change |
| `appearance_1024x600_light.png` | the same page in `platinum` light, WINDOW STYLE on PLATINUM; its accent is not one of the swatches, so the row reads THEME'S OWN |
| `theme_grid_320x240.png`, `theme_grid_1024x600.png` | the THEME grid: the seeds, an installed store theme (`sunset`) and, once a change is made, `custom` first |
| `accent_popover_1024x600.png`, `accent_320x240.png` | the ACCENT picker: sixteen swatches, the look live behind it |
| `tint_popover_1024x600.png`, `tint_320x240.png` | the TINT picker: each tint as its ramp, NONE keeping the theme's own |
| `wallpaper_320x240.png` | the WALLPAPER page: the wallpaper is the theme's, and a pick is a change into `custom` |
| `accent_after_1024x600.png` | after ACCENT MINT and TINT TEAL on `night`: the desk, the bar, Calc, the Editor's code and blocks and the page itself, all derived; the theme is `custom` and NAME… has appeared |
| `derived_roles.png` | that change as a table: every role of both variants, base → derived, and which rule set it |
| `taskbar_top_1024x600.png` | EDIT TASKBAR on the live top bar: MENU selected, its removal refused |
| `taskbar_left_1024x600.png` | the whole bar moved to the left edge, ZONE kept on a slim top bar, the add tray open |
| `taskbar_320x240.png` | EDIT TASKBAR on the T-Deck: the clock held and moving, the tray and the edges as the page under the bar |
| `flow_320x240.png` | the flow: `night` → ACCENT PINK → `custom` appears → NAME… `bubblegum` → it is in the grid and `custom` is free |

The six-page theme editor drawn before them (branch `editordesign`,
`937863c7`) was rejected by the owner on 2026-10-14 and never merged.
