# Studio mockups (2026-09-29)

The pictures behind `docs/studio_2026-09.md`: the middle-ground Studio the
owner settled on, drawn at the Waveshare P4's 1024×600 and the T-Deck's
320×240, as the record an implementation is checked against.

**Every file is a hand-drawn mockup, not an engine render.** Each was drawn in
the D1 look (`docs/theming_2026-09.md` §1) with MOY64 colours, the Tiny5 UI
font, and sprites, maps and frames taken from the Sky Run and Coin Quest seed
carts. The P4 and T-Deck screens are drawn at **2× of device pixels**, so one
device pixel is a 2×2 block; `panel_minimums.png` is the exception and draws
the 7" screen **1:1**. They show layout and behaviour, not finished pixels.

| file | what it shows |
|---|---|
| `layout_a_code_run_1024x600.png` | Layout A on the 7": Files, Code and Run across, Make it mine under Run and scrolling. The WM's title strip on top; under it the Studio's one bar, which is the panes' tab bars with the project menu at the left end and undo, redo and Play at the right. Code shows a signature hint for `sfx(` that lists the cart's sounds, each with a play button |
| `layout_b_sprites_map_run_1024x600.png` | Layout B: Sprites (PICO-8's set: tools, palette with the rest of MOY64 behind a button, flags, sheet with its pages) beside Map over Run. Map takes its brush from the Sprites sheet instead of drawing a second one |
| `layout_c_scene_blocks_code_1024x600.png` | Layout C with Coin Quest: Scene, Blocks and Run across, Code under Run. Blocks follows the Scene selection (a coin is selected, so the coin's script is open), and Code shows the lines those blocks generate |
| `moving_a_tab_1024x600.png` | Dragging the `map` tab over the Code pane: split up or down, add as a tab, and the sideways splits refused because two panels need 640px. On the right, the same choices from holding a tab |
| `panel_minimums.png` | The panel set and its minimum sizes, with the 7" screen drawn 1:1: two rows of 320×222 panels fit, and a scrolling panel fits under Run |
| `tdeck_320x240.png` | The T-Deck: one bar with every file of the project as a tile (the open one shows its name), undo, redo, Play and the OS's exit; no splits. Code, Sprites and Map, each at the full screen |

The P4 screens are drawn in the light variant and the T-Deck in the dark one;
D1 makes those one routine with two token tables.
