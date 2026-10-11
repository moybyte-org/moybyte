# moy_ui — the widget toolkit in C

The toolkit's bodies in C, with style and strings as data
(`docs/kernel_toolkit_2026-10.md`, #224). `runtime/ui.py` is its face: every
shipped app and every app cart still imports `ui`, and the face hands its
calls here.

| file | holds |
|---|---|
| `style.json` | the style: each kind's `(field, ink, edge)` token spec per state, each kind's metric slots, the state aliases, the quirks that stay code, the skins as deltas, the token row's order (moy_app's vocabulary) and the hit buffer's capacity |
| `strings/en.json` | the strings C draws, English, one entry per string id |
| `moy_ui_tables.h`, `moy_ui_tables.c` | GENERATED from the two by `tools/gen_ui_tables.py` and committed; `tests/test_ui_tables.py` fails a stale pair |
| `moy_ui.h`, `moy_ui.c` | the style resolution: kinds and states by name, the skin by name, the looks (the style resolved against a token row) |
| `modmoy_ui.c` | the module `moy_ui` the boards and the browser import |

A data edit is the JSON plus the regenerated tables; it touches no C body
and no Python logic, and it still needs a firmware build to reach a board.
The tables are `const`; the looks are allocated once, in PSRAM where a board
has it.

On the host the C is compiled into the glass's library
(`runtime/glass_binding.py`), because its primitives draw into the canvas
rows that library holds; `tools/moy_ui_binding.py` is the module over ctypes.
A console takes the module in its `board.toml`; the Zero, which runs no apps,
denies it.

The oracle is `tests/ui_twin.py`, the toolkit's Python as it was before the
crossing; `tests/test_ui_twin.py` fuzzes every widget against it.
