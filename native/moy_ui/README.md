# moy_ui — the widget toolkit in C

The toolkit's bodies in C, with style and strings as data
(`docs/kernel_toolkit_2026-10.md`, #224). `runtime/ui.py` is its face: every
shipped app and every app cart still imports `ui`, and the face hands its
calls here.

| file | holds |
|---|---|
| `style.json` | the style (its own "about" says what it holds), the token row's order (moy_app's vocabulary) and the hit buffer's capacity |
| `strings/en.json` | English for every string C draws, by id |
| `moy_ui_tables.h`, `moy_ui_tables.c` | GENERATED from the two by `tools/gen_ui_tables.py` and committed; `tests/test_ui_tables.py` fails a stale pair |
| `moy_ui.h`, `moy_ui.c` | the style resolution: kinds and states by name, the skin by name, the looks (the style resolved against a token row) |
| `modmoy_ui.c` | the module `moy_ui` the boards and the browser import |

Which edits are data and which need C is the design doc's §2.2.

On the host the C is compiled into the glass's library
(`runtime/glass_binding.py`), because its primitives draw into the canvas
rows that library holds; `tools/moy_ui_binding.py` is the module over ctypes.
A console takes the module in its `board.toml`; the Zero, which runs no apps,
denies it.

The oracle is `tests/ui_twin.py`, the toolkit's Python as it was before the
crossing; `tests/test_ui_twin.py` fuzzes every widget against it.
