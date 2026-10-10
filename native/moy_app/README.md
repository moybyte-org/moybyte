# moy_app — the app ABI's kernel half

What `docs/kernel_appabi_2026-10.md` builds in C: the grants every app's
context holds, the grant policy, the roles served in C, and the binding of the
rows the console serves in Python. `roles.json` is the
role table (one row per verb, its `server` saying where it is served), and
`tests/test_roles.py` holds the C rows to it.

| file | what it is |
|---|---|
| `roles.json` | the role table: role, verb, server (`c`, or `shell`: the Python console's), the permission that grants it, its wasm import type |
| `moy_app.h`, `moy_app.c` | the state (`moy_appabi_t`): the grant table (kind GRANT, rows keyed by the app's id, SHIPPED idempotent by id, RUN ended with its run, each with the surface row the shell writes), the damage flags, the live token table under its generation (the theme's vocabulary, `moy_app_token_name`), the pointer the surface rows read (a `moy_input_ptr_t` the console binds, let go when the VM ends), Paint's open picture (its `paint_doc` and `paint_doc_kind` settings rows, JSON strings, read and followed by the artwork rows), the clipboard's 4 KiB of text, the user-files store the files and wallpaper-copy rows reach (the layer's verb table `moy_uf_ops`, the carts root, readable and writable, the session count, the copy's generation), the per-row counters, prefs written into a settings store; the ROLE door (`moy_app_role`: any table row by its index, its arguments packed as length-prefixed fields, a C row run here with a blob the layer allocated copied out and freed, a shell row handed to the door `moy_app_door_bind` binds, the kernel's counted `moy_loop_role`); the policy (permission to role, the files kinds, the two-kinds refusal, the key a cart's grant is made under); the C rows, each store row taking the bus gate around its own op (`moy_vol_gate_enter`) and a grant made with a files kind reaching that kind alone |
| `moy_app_wasm.h`, `moy_app_wasm.c` | the wasm import adapter: one native per row with a wasm type, imported from module `moybyte.app` as `<role>_<verb>`, each packing its arguments for the ROLE door; writes, prefs, the files rows and shell rows hop to the VM's task (`moy_wasm_on_vm`, weak: a call where there is none), the look's and the clipboard's reads run in place under the seqlock (`moy_app_seq_begin`/`end`); the run's grant and role mask (`moy_app_wasm_bind`), the load's grant check (`moy_app_wasm_admit`), a module's import walk (`moy_app_wasm_imports`, which the VM-free verdict reads too) and the hops' count and microseconds |
| `modmoy_app.c` | the MicroPython binding, module `moy_app`: `App(settings)`, `kernel(fresh)`, the shell's writes (`surface_write`, `bind_pointer`, `theme_write`, `serve`, `store_bind`), the role types over a grant -- `Damage`, `Surface`, `Theme`, `Files`, `Prefs`, `Artwork`, `Clipboard` with C rows, `Carts`, `Nav`, `Notify`, `Wallpaper` (its copy's two rows C), `Install` served in Python -- `grant_id`, `app.role` (the ROLE door), `table()` and `door_bind(on)`, `app.wasm_bind(g, roles)`, `app.wasm_end()`, `wasm_rows()` and `wasm_hops()`, and the policy. A shell row checks the grant holds its role (`moy_app_holds`), counts the call in `served()` and calls the server the console registered for the role (`runtime/shell_servers.py`) with the grant first, allocation-free once the row has been called |
| `micropython.cmake`, `micropython.mk` | the boards', and the desktop's and browser's, builds; it reads `native/moy_spine`'s headers and links beside it, and `native/moy_input`'s pointer type. They also build `native/moy_store`'s user-files layer (`moy_ufiles.c`) and its module `moy_ufiles` (`modmoy_ufiles.c`), the files role's server: an image that denies this module carries neither |

The state is the kernel's on a board (`moy_app_kernel`, from the spine's
allocator, so PSRAM): made once, never freed, its prefs the kernel's settings
rows, so a VM stop leaves the grants, the clipboard and the rows for the next
VM; a fresh start ends the RUN grants and clears the clipboard. The browser,
the desktop MicroPython and the CPython host give each console its own
(`App(settings)`). The KSTOP down line reads the kernel's grant count
(`grants=N`), which the T-Deck's suite holds flat across stops.

The host's binding is `tools/moy_app_binding.py`, over ctypes, from the spine's
host library (`tools/moy_index_spike.py`'s SPINE carries these sources), which
the `runtime` package registers as `moy_app`; the user-files layer is a library
of its own there (`tools/moy_ufiles_binding.py`), whose verb table `store_bind`
hands the state. The Zero denies the module: it runs no apps, and its sync
moves user files by path.

The nets: `tests/test_userfiles_parity.py` (the user-files layer's bytes, on
both bindings, against the Python it replaced), `tests/test_moy_app.py` (one
script over the Python oracle
`tests/app_twin.py`, the ctypes binding and the native module on the desktop
MicroPython in the boards' object model, compared line for line),
`tests/test_roles.py` (the table, the C rows' order, the per-frame budgets)
and the roles trace in `tests/test_semantic_traces.py`.
