# moy_ui — the widget toolkit in C

The toolkit's bodies in C, with style and strings as data
(`docs/kernel_toolkit_2026-10.md`, #224). `runtime/ui.py` is its face: every
shipped app and every app cart still imports `ui`, and the face hands its
calls here.

| file | holds |
|---|---|
| `style.json` | the style (its own "about" says what it holds), the token row's order (moy_app's vocabulary) and the hit buffer's capacity |
| `moy_ui_tables.h`, `moy_ui_tables.c` | GENERATED from `style.json` by `tools/gen_ui_tables.py` and committed; `tests/test_ui_tables.py` fails a stale pair |
| `moy_ui.h`, `moy_ui.c` | the style resolution: kinds and states by name, the skin by name, the looks (the style resolved against a token row) |
| `moy_theme.h`, `moy_theme.c` | the theme loader (`docs/theming_2026-09.md`): format 1's strict reader, `inherits`, the derivation rules and the readability check, flattening a theme into a role row (the tokens, then the content roles) and its shape (metrics, text effects, and surfaces compiled from their grammar into records); the seeds and one installed package |
| `themes/*.json` | the seed theme packages, embedded in `moy_ui_tables.c` in style.json's `theme.seeds` order |
| `themes/tiny5.txt`, `themes/tiny5.fnt` | the default theme's UI font: its glyphs as pixel rows, and the font file `tools/make_tiny5_font.py` builds from them, embedded in `moy_ui_tables.c` with every font file a seed names (`THIRD_PARTY.md` §3.1a) |
| `moy_ui_draw.c`, `moy_ui_glyph.c`, `moy_ui_interact.c` | the primitives (each paints its part's surface when the installed shape gives one, and draws its label in the part's text effect and pressed offset), the window frame styles and the bar's band (`moy_ui_frame`, `moy_ui_frame_band`), the glyph raster, and Hits/ScrollRegion/DragTap over caller-owned buffers |
| `moy_ui_paint.c` | the surface painter: a compiled surface into a box, under the corner mask of the theme's radius |
| `moy_ui_sink.c` | the ROW sink: a glass canvas row by its CANVAS handle |
| `moy_ui_wasm.h`, `moy_ui_wasm.c` | the RUN sink and the `ui` rows a compiled app imports (roles.json's `ui` role) |
| `modmoy_ui.c` | the module `moy_ui` the boards and the browser import (its `themes`, `theme_roles`, `theme`, `theme_shape`, `surface`, `parts`, `theme_states`, `theme_install`, `theme_installed` and `theme_fonts` face the loader, `font_load`, `font_free`, `font_reset`, `font_info` and `font_file` the font rows behind `moy_text` (`native/moy_gfx/moy_text.h`), `use_font` and `font` the font each role's text draws in, `print_font` the one a canvas's print draws in and `cart_text` the app-cart boundary (`docs/theming_2026-09.md` §4.7), `use_shape` installs a theme's shape, `paint` is the painter's sink op and `frame`, `frame_buttons` and `frame_band` draw and lay out a window frame); its `s`, `str_set` and `str_reset` read and write the spine's string table |

The strings C draws are the spine's (`native/moy_spine/moy_str.h`), not this
module's: the kernel's floor draws them on an image with no toolkit.

Which edits are data and which need C is the design doc's §2.2.

On the host the C is compiled into the glass's library
(`runtime/glass_binding.py`), because its primitives draw into the canvas
rows that library holds; `tools/moy_ui_binding.py` is the module over ctypes.
A console takes the module in its `board.toml`; the Zero, which runs no apps,
denies it.

The oracle is `tests/ui_twin.py`, the toolkit's Python as it was before the
crossing; `tests/test_ui_twin.py` fuzzes every widget against it.
