# moy_app — the app ABI's kernel half

What `docs/kernel_appabi_2026-10.md` builds in C: the grants every app's
context holds, the grant policy, the roles served in C, and the binding of the
rows the console serves in Python. `roles.json` is the
role table (one row per verb, its `server` saying where it is served), and
`tests/test_roles.py` holds the C rows to it.

| file | what it is |
|---|---|
| `roles.json` | the role table: role, verb, server (`c`, or `shell`: the Python console's), the permission that grants it, its wasm import type |
| `moy_app.h`, `moy_app.c` | the state (`moy_appabi_t`): the grant table (kind GRANT, rows keyed by the app's id, SHIPPED idempotent by id, RUN ended with its run, each with the surface row the shell writes), the damage flags, the live token table under its generation (the theme's vocabulary, `moy_app_token_name`), the pointer the surface rows read (a `moy_input_ptr_t` the console binds, let go when the VM ends), Paint's open picture (its `paint_doc` and `paint_doc_kind` settings rows, JSON strings, read and followed by the artwork rows), the clipboard's 4 KiB of text, the per-row counters, prefs written into a settings store; the policy (permission to role, the files kinds, the two-kinds refusal, the key a cart's grant is made under); the C rows |
| `modmoy_app.c` | the MicroPython binding, module `moy_app`: `App(settings)`, `kernel(fresh)`, the shell's writes (`surface_write`, `bind_pointer`, `theme_write`, `serve`), the role types over a grant -- `Damage`, `Surface`, `Theme`, `Prefs`, `Artwork`, `Clipboard` with C rows, `Files`, `Carts`, `Nav`, `Notify`, `Wallpaper`, `Install` served in Python -- `grant_id`, and the policy. A shell row checks the grant holds its role (`moy_app_holds`), counts the call in `served()` and calls the server the console registered for the role (`runtime/shell_servers.py`) with the grant first, allocation-free once the row has been called |
| `micropython.cmake`, `micropython.mk` | the boards', and the desktop's and browser's, builds; it reads `native/moy_spine`'s headers and links beside it, and `native/moy_input`'s pointer type |

The state is the kernel's on a board (`moy_app_kernel`, from the spine's
allocator, so PSRAM): made once, never freed, its prefs the kernel's settings
rows, so a VM stop leaves the grants, the clipboard and the rows for the next
VM; a fresh start ends the RUN grants and clears the clipboard. The browser,
the desktop MicroPython and the CPython host give each console its own
(`App(settings)`). The KSTOP down line reads the kernel's grant count
(`grants=N`), which the T-Deck's suite holds flat across stops.

The host's binding is `tools/moy_app_binding.py`, over ctypes, from the spine's
host library (`tools/moy_index_spike.py`'s SPINE carries these sources), which
the `runtime` package registers as `moy_app`. The Zero denies the module: it
runs no apps.

The nets: `tests/test_moy_app.py` (one script over the Python oracle
`tests/app_twin.py`, the ctypes binding and the native module on the desktop
MicroPython in the boards' object model, compared line for line),
`tests/test_roles.py` (the table, the C rows' order, the per-frame budgets)
and the roles trace in `tests/test_semantic_traces.py`.
