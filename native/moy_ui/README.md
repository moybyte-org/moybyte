# moy_ui — the widget toolkit in C

The toolkit's bodies in C, with style and strings as data (#224 sprint 6;
the design record is `docs/history/kernel_toolkit_2026-10.md`), and the engine of the public UI
API (`docs/ui_api_2026-10.md`). `runtime/toolkit/ui.py` (the verbs) and
`runtime/toolkit/ui_draw.py` (the rect-taking widgets the chrome keeps) are its
Python faces, `native/moycore/moycore_ui.h` its Lua face and
`moy_ui_wasm.c` its rows for a compiled app.

| file | holds |
|---|---|
| `style.json` | the style (its own "about" says what it holds), the token row's order (moy_app's vocabulary) and the hit buffer's capacity |
| `moy_ui_tables.h`, `moy_ui_tables.c` | GENERATED from `style.json` by `tools/gen_ui_tables.py` and committed; `tests/test_ui_tables.py` fails a stale pair |
| `moy_ui.h`, `moy_ui.c` | the style resolution: kinds and states by name, the skin by name, the looks (the style resolved against a token row, a kind's floor forms where its pair misses the floor), and the button kinds' style with the live theme's `buttons` and `app_buttons` leaves (`moy_ui_button_style`, docs/history/look_2026-10.md step 10) |
| `moy_theme.h`, `moy_theme.c` | the theme loader (`docs/theme_and_shell_2026-11.md`): format 2's strict reader (a colour a MOY64 index or `#rrggbb`), `inherits`, the derivation rules and the readability check, flattening a theme into a role row (the tokens, then the content roles) and its shape (metrics, text effects, and surfaces compiled from their grammar into records), and its `layout` (the bars, capped, clamped and held to the required widgets: `docs/theme_and_shell_2026-11.md` §8.3); the seeds and one installed package, and a stored package resolved from its bytes without installing it (`moy_theme_probe`, `moy_ui.theme_resolve`); the accent-and-tint derivation (`moy_theme_derive`, §4.10 there) and `custom`'s theme.json written from it (`moy_theme_custom`, `moy_ui.theme_custom`); a theme's sheet files, and the role sheet read, decoded into a buffer it owns and its set cells marked (`moy_theme_sheet_load`, docs/theme_and_shell_2026-11.md §3.6); the theme model's `look` read, inherited and resolved with its defaults (`moy_theme_look`); RGB's luminance and nearest MOY64 index, and the live theme's RGB placed in the shell's block of indices 64–127 (`moy_theme_place`, `docs/history/look_2026-10.md` step 5) |
| `themes/*.json` | the seed theme packages, embedded in `moy_ui_tables.c` in style.json's `theme.seeds` order |
| `themes/tiny5.glyphs`, `themes/tiny5.fnt`, `themes/tiny5.txt` | the default theme's UI font: its glyphs as pixel rows, the font file `tools/make_tiny5_font.py` builds from them, and its licence (OFL-1.1) in full; the font and the licence are embedded in `moy_ui_tables.c`, as every font a seed names is with its `X.txt` (`THIRD_PARTY.md` §3.1a) |
| `icons/` | the icon sets (`docs/history/look_2026-10.md` step 16): `pixel/` the pixel set (`system_icons.moygfx`, drawn by `pixel_icons.py`; `slots.txt` the slots every set fills), `smooth/<set>/` the SVG sources, and the masks `tools/make_icons.py` builds from them, `SET[.fill]-SIZE.fnt` at format version 2, with `set.json`; a board freezes its tier's beside its fonts and `runtime/toolkit/fontset.py` reads them |
| `fonts/` | the font set (`docs/history/look_2026-10.md` step 3): the faces `tools/make_font.py` builds, `FAMILY-SIZE-WEIGHT-BPP.fnt` at format version 2, each family's licence `FAMILY.txt`, and `set.json` (the faces, each tier's set, the pinned sources); a board freezes its tier's as `fonts_data` and `runtime/toolkit/fontset.py` reads them (`THIRD_PARTY.md` §3.1b) |
| `moy_ui_draw.c`, `moy_ui_glyph.c`, `moy_ui_interact.c` | the primitives (each paints its part's sprite when the role sheet sets its cell, else its surface when the installed shape gives one, and draws its label in the part's text effect and pressed offset), the window frame styles and the bar's band (`moy_ui_frame`, `moy_ui_frame_band`), the glyph raster, and ScrollRegion/DragTap over caller-owned buffers |
| `moy_ui_paint.c` | the surface painter: a compiled surface into a box, under the corner mask of the theme's radius |
| `moy_ui_depth.c` | the theme's depth (`docs/history/look_2026-10.md` step 6): its look's radii, shadow and edges installed per theme and mode, the window frame it draws with its parts (the header styles, the rule; step 13), the bars' parts (the band, the system button's tile, a desk chip; step 14), what a window leaves to the desk under it (its shadow and its outline's fringe) and a card's shadow, through `native/moy_gfx/moy_shape.h` |
| `moy_ui_parts.c` | the parts the public UI API's verbs draw (`docs/history/look_2026-10.md` step 12): the value rows (info, link, cycle, seg, swatches, toggle, slider, progress) and the icon before a row's label, a group's caption and card, the keycap hints and the page's ground, from `style.json`'s `group` and `value_row` metrics; and step 21's: a table's column geometry, header and rows, a text field's row and a dialog's modal card; and step 20's: a tab row, and a card of a grid (its marks and captions before the app draws its picture, the picture's cut corners and ring after) |
| `moy_ui_sink.c` | the ROW sink: a glass canvas row by its CANVAS handle |
| `moy_ui_text.h`, `moy_ui_text.c` | A text view: a text area in rows -- a line's colour runs (Python, Lua and JSON highlighting over a word table the shell installs, or SGR escapes), each row's ground, current mark, gutter number, error, selection, find matches, text, fade and caret -- and the record of what each row drew (its gen), kept per view id (`MOY_UI_TEXT_VIEWS` across the contexts), against which a frame plans the rows it draws and the move of the rest: `ui.text_view`'s, which the Code tab, theme Config, the Editor desk's made code and the text console draw through |
| `moy_ui_api.h`, `moy_ui_api.c` | the public UI API's engine: a context per running app (focus, the last frame's stops, each id's kept state, the layout cursor), the frame's input resolved once into focus moves, activation, the pointer's press and drag and the page's scroll (a scroll region's, a placed table's), a focused field's typed key, a dialog's hold on the input and a card grid's columns, and the verbs, each drawing through the primitives with the theme's marks; its other files share `moy_ui_api_int.h` (the stop flags, the floor's rects and clips, and the `mua_*` helpers one file defines and another calls) |
| `moy_ui_api_text.c` | `ui.text_view`: a view's lines, wrapped rows and marks drawn, and the queries an app asks of a view (`moy_ui_api_text_at`, `_aim`, `_reveal`, `_cover`) |
| `moy_ui_api_kept.c` | the kept ring (a slot a paint its context begins: its kept table's record and the parts `held` names; outside the context, in one block taken when a context first keeps, for the four that kept last), from which a kept table's frame draws only what the buffer it paints lacks (`moy_ui_api_table_floor`), moving the view out of the last frame's buffer by the scrolls' difference (`moy_ui_api_table_shift`) or copying what that buffer holds as this frame draws it (`moy_ui_api_table_copy`); the one record of a table's kept faces (`moy_ui_api_slots`: which part's face each slot of the app's layer holds, which was stamped least recently, which to draw next) |
| `moy_ui_api_table.c` | a table's rows and parts (`moy_ui_api_table`, `moy_ui_api_table_part`), the blocks of fields an app sets through the engine as they change (`moy_ui_api_block_set`: the fields set since a generation, what they show hashed), a text field and a dialog over its wash |
| `moy_ui_api_pop.c` | popovers, menus, card grids, an empty page, tools and the app's bar |
| `moy_ui_api_host.c` | a shift frame's plan (`moy_ui_api_shift_plan`), the lag log, and an app's host (`moy_ui_api_host_begin`) |
| `moy_ui_wasm.h`, `moy_ui_wasm.c` | the RUN sink and the RUN face: the run's one context over its snapshot, the verbs a Lua cart's `ui` calls and the `ui` rows a compiled app imports (roles.json's `ui` role) |
| `modmoy_ui.c` | the module `moy_ui` the boards and the browser import (its `themes`, `theme_roles`, `theme`, `theme_look`, `theme_look_fields`, `theme_place`, `theme_block`, `theme_rgb`, `theme_shape`, `surface`, `parts`, `theme_states`, `theme_install`, `theme_installed`, `theme_fonts`, `theme_sheets` and `theme_layout` face the loader, `sheet_check`, `sheet_load`, `sheet_clear`, `sheet_cells`, `sheet_set` and `sheet_caps` the role sheet, `font_load`, `font_free`, `font_reset`, `font_info` and `font_file` the font rows behind `moy_text` (`native/moy_gfx/moy_text.h`), `use_font` and `font` the font each role's text draws in, `print_font` the one a canvas's print draws in and `cart_text` the app-cart boundary (`docs/theme_and_shell_2026-11.md` §3.7), `use_shape` installs a theme's shape, `buttons_set`, `buttons_clear` and `buttons_app` the live theme's button leaves and `button_style` resolves a button kind against them, `theme_reads` is the contrast floor, `paint` is the painter's sink op and `frame`, `frame_buttons` and `frame_band` draw and lay out a window frame; `use_depth`, `frame_geom`, `frame_icon`, `frame_under`, `cast` and `bar_part` are the depth's; `api_size`, `api_begin` and the `api_` verbs are the public UI API's engine, `api_text_view`, `api_text_at` and `api_text_cover` the text view's verb, hit test and overlays, `text_words`, `text_runs` and `text_tones` its word table, colour runs and tones); its `s`, `str_set` and `str_reset` read and write the spine's string table |

## Which edits are data, which need C

| a change | the edit |
|---|---|
| which token a widget state paints; a skin; a new kind that reuses a primitive's shape | data (`style.json`) |
| a theme's colours | data (a theme package) |
| a pad, an edge weight, a strip height, a label alignment | data (metrics); a size change moves goldens, deliberately |
| a glyph's pixels; a new glyph | data (style's glyph vocabulary) |
| a string C draws | data (strings, below) |
| any layout; which widget a screen draws where; a string Python passes in | Python (the apps, their Layouts and the WMs) |
| a new primitive shape, a new state, a new metric slot, a quirk, the hover and press rules, scroll physics, the text lane, a font format, theming's painter | C |

## Strings as data (#135)

The strings C draws are the spine's (`native/moy_spine/moy_str.h`), not this
module's: the kernel's floor draws them on the Zero, which denies the toolkit.
A string id is a dotted name (`fit.needs`, `recovery.title`); C reads
`moy_str(id)` and formats with `moy_str_fmt`, and the Python face reads
`ui.s(id)`. Each entry keeps its conversions in order (`%s`, `%u`, `%d`,
`%08x`), and a language pack whose entry carries different ones is refused for
that id, which then reads English: a pack is untrusted data, as a theme is.

Every `moy_text` call carries its SOURCE (`native/moy_gfx/moy_text.h`):
`TABLE` (a string id), `PY` (text a runtime passed in), `DATA` (a number or a
user's text formatted into a table entry) or `LIT` (a literal in C). `LIT`
never reaches the raster. The `PY` call sites are a ratchet that only comes
down, each removed as its strings move into the table
(`tests/test_ui_strings.py`, 2026-10-11): the menu rows, panel and strip
titles, notices and toasts are Python-fed through moy_chrome's builders, and
the sysmenu's rows and the runtime's notices already read the table.
Glyph coverage is the font's (owner, 2026-10-11): the text call takes UTF-8
and a font, so a font that covers Cyrillic is data.

On the host the C is compiled once, into the host's one library
(`runtime/host/host_native.py`) with the glass whose canvas rows its primitives
draw into, the Lua run and the compiled-cart run, as on an image: the theme,
depth, fonts and cart-text boundary the shell sets are the ones a Lua or
compiled page's `ui` reads (`tests/test_host_one_library.py`).
`runtime/host/moy_ui_binding.py` is the module over ctypes; its draw verbs,
its interaction and its public UI API are set on it by
`moy_ui_draw_binding.py`, `moy_ui_scroll_binding.py` and
`moy_ui_api_binding.py` beside it.
A console takes the module in its `board.toml`; the Zero, which runs no apps,
denies it.

The oracle is `tests/ui_twin.py`, the toolkit's Python as it was before the
crossing; `tests/test_ui_twin.py` fuzzes every widget against it.
