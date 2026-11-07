# moy_app — the app ABI's kernel half

The app ABI's kernel half (#224 sprint 5; the design record is
`docs/history/kernel_appabi_2026-10.md`): the grants every app's context holds,
the grant policy, the roles served in C, and the binding of the rows the
console serves in Python. `roles.json` is the
role table (one row per verb, its `server` saying where it is served), and
`tests/test_roles.py` holds the C rows to it.

| file | what it is |
|---|---|
| `roles.json` | the role table: role, verb, server (`c`, `shell`: the Python console's, or `proc:<name>`: a process's), the permission that grants it, its wasm import type; and the permission table (`"permissions"`: name, what it grants, tiers, install prompt line, the service it needs, its Python names), whose `role:` rows are this module's allowlist (`runtime/shell/app_perms.py`, `docs/os_2026-11/processes.md` §2) |
| `moy_app.h`, `moy_app.c` | the state (`moy_appabi_t`): the grant table (kind GRANT, rows keyed by the app's id, SHIPPED idempotent by id, RUN ended with its run, each with the surface row the shell writes), the damage flags, the live token table under its generation (the theme's vocabulary, `moy_app_token_name`), the pointer the surface rows read (a `moy_input_ptr_t` the console binds, let go when the VM ends), Paint's open picture (its `paint_doc` and `paint_doc_kind` settings rows, JSON strings, read and followed by the artwork rows), the clipboard's 4 KiB of text, the user-files store the files and wallpaper-copy rows reach (the layer's verb table `moy_uf_ops`, the carts root, readable and writable, the session count, the copy's generation), the per-row counters, prefs written into a settings store; the ROLE door (`moy_app_role`: any table row by its index, its arguments packed as length-prefixed fields, a C row run here with a blob the layer allocated copied out and freed, a shell row handed to the door `moy_app_door_bind` binds, the kernel's counted `moy_loop_role`); the policy (permission to role, the files kinds, the two-kinds refusal, the key a cart's grant is made under); the C rows, each store row taking the bus gate around its own op (`moy_vol_gate_enter`) and a grant made with a files kind reaching that kind alone |
| `moy_app_wasm.h`, `moy_app_wasm.c` | the wasm import adapter: one native per row with a wasm type, imported from module `moybyte.app` as `<role>_<verb>`, each packing its arguments for the ROLE door; writes, prefs, the files rows and shell rows hop to the VM's task (`moy_wasm_on_vm`, weak: a call where there is none), the look's and the clipboard's reads run in place under the seqlock (`moy_app_seq_begin`/`end`); each native's run found by the engine's handle on its call (`moy_app_wasm_run_of`, which the engine's glue defines: `native/moycore/moycore_wasm.c`, `runtime/host/moyhost_wasm.c`), the console's state the theme is read from (`moy_app_wasm_live`), the load's grant check against the run (`moy_app_wasm_admit`), a module's import walk (`moy_app_wasm_imports`, which the VM-free verdict reads too) and the hops' count and microseconds |
| `moy_app_run.h` | a run's grant, `moy_app_run_t`: the state, its serial, the grant and the role mask the permissions name, a value the run's owner holds (a Lua state's registry, a wasm run's per-run state in its engine's glue, a process row with the table); `moy_app.h` makes one (`moy_app_run_make`), checks its state is still made (`moy_app_run_live`) and holds the one a VM stop carries (`moy_app_stop_hold`, `moy_app_stopped`, `moy_app_stop_end`). It includes nothing else, so an engine's glue holds one without this module's headers |
| `moy_app_lua.h`, `moy_app_lua.c` | the Lua face: the state's own run, copied into its registry at its open (`moy_app_lua_open(L, run)`), opened into its state as the names its permissions grant (`files`, `prefs`, `set_theme`, `open_app`), each a C function packing its arguments for the ROLE door and answering as the Python face does (`(value, err)` as two results, a value of prefs as JSON), called in place on the VM's task; and with `files`, Lua's `io` and `os.remove`/`os.rename` over the grant's one kind, a path that climbs, is absolute or names a folder refused (EACCES); `os.time`/`clock`/`difftime` for every app run. `moycore_lua.c` opens it, weak, with the state; it compiles to nothing without Lua's headers |
| `modmoy_app.c` | the MicroPython binding, module `moy_app`: `App(settings)`, `kernel(fresh)`, the shell's writes (`surface_write`, `bind_pointer`, `theme_write`, `serve`, `store_bind`), the role types over a grant -- `Damage`, `Surface`, `Theme`, `Files`, `Prefs`, `Artwork`, `Clipboard` with C rows, `Carts`, `Nav`, `Notify`, `Wallpaper` (its copy's two rows C), `Install` served in Python -- `grant_id`, `app.role` (the ROLE door), `table()` and `door_bind(on)`, `app.run(g, roles)` (a run's bytes, which a Lua or wasm run's open copies), `app.stop_hold(run)` and `app.stop_end()`, `wasm_rows()` and `wasm_hops()`, and the policy. A shell row checks the grant holds its role (`moy_app_holds`), counts the call in `served()` and calls the server the console registered for the role (`runtime/shell/shell_servers.py`) with the grant first, allocation-free once the row has been called |
| `micropython.cmake`, `micropython.mk` | the boards', and the desktop's and browser's, builds; it reads `native/moy_spine`'s headers and links beside it, `native/moy_input`'s pointer type, and `native/moy_lua`'s headers for the Lua face. They also build `native/moy_store`'s user-files layer (`moy_ufiles.c`) and its module `moy_ufiles` (`modmoy_ufiles.c`), the files role's server: an image that denies this module carries neither |

## One table, a server per verb

The role table is data, and every verb has one of three servers:

- **`c`**: the state is the kernel's and resident (the settings rows, the
  glass, the lease table, the engine's sizing, the user-files layer). Each verb
  is its own C function taking the grant; there is no by-name dispatcher.
- **`shell`**: the state is the Python console's (the routes' surfaces, the
  look and its caches, the wallpaper cart's namespace, the shelf's live list,
  Get Carts' transport). There is no resident C catalogue,
  so every cart and wallpaper read is shell-served, and none is per-frame. A
  Python app's binding calls the registered Python server straight, counted,
  with no trip through C. A compiled app reaches one through the ROLE door
  (the row's index, an argument buffer, an answer buffer), counted as its own
  upcall class in `native/moy_play/upcall_sites.txt`, entering MicroPython only
  on the VM's task and answering `MOY_APP_NEEDS_VM` with no VM.
- **`proc:<name>`**: the state is a background process's, the cart
  `moybyte.<name>` (`notify`'s rows, the achievements process,
  `docs/os_2026-11/processes.md` §8). The console registers its link to the
  process for the role (`runtime/system/achievements.py`), so this module serves
  the row as a shell row; the link sends the process its events as messages and
  reads its answers off its functions.
- **Python-only**: a row with no wasm type, whose only consumer is Python on
  paths and objects (`install.net`, `install.keep`, `install.op`).

A role read on the frame path is a C call with no Python shim, or a cached
object checked by one integer (`theme.colors()` against the token generation,
`surface.canvas()` the grant's registered canvas).

**The grant.** Every caller of a role holds one: a row of this module's grant
table (`moy_htab.h`'s kind GRANT) carrying the app's key, its class, its role
mask, its files kind, its prefs namespace, an OWNER handle it names and its
surface row. It is not itself an OWNER row, because a stop ends every OWNER
row and a shipped app's grant outlives one (2026-10-10). A grant is keyed by
the cart's `id` (#162, SPEC.md §3.1): the manifest's `<author>.<name>`, else
the folder less `.moy`, else a path-less built-in's title slug (ASCII letters
and digits, the C rule), so a renamed cart keeps its prefs and its strikes.
Every role call takes the grant first; no verb takes the calling app as an
argument, and in every runtime an ungranted verb has no name (`DENIED` guards a
binding that exposed one anyway).

**A run's grant.** A Lua or wasm run holds its grant as a value,
`moy_app_run_t` (`moy_app_run.h`): the state, the grant and the roles its
permissions name, which the Player makes (`app.run`) and the run's open
copies into what owns the run (a Lua state's registry, a compiled run's
per-run state in its engine's glue). Every call the run makes is answered
from that copy and no other, so two runs at once are two grants. The grant
row stays the authority: one app id has one RUN row, so a launch after
Settings turned a permission off re-makes the row the earlier run's copy
names, and that copy's next call to the role answers `DENIED`. A copy of a
grant that ended answers `STALE`, and one whose state was freed reads as no
run (`moy_app_run_live`, by the state's serial). A compiled app the kernel
runs with no VM gets the run the Player held in the kernel's state across
the stop (`moy_app_stop_hold`); the start after the stop ends it.

**Call shapes.** Errors extend `MOY_HTAB_*` (`moy_app.h`). Text crosses as a
byte span; a text or blob answer is written into the caller's buffer and
returns its length, with a `*_size` verb for blobs; a list is a count and a
packed buffer of ids; numbers are `int32`, as moy-spec's binding marshals them.
A cart is its folder (a built-in's title where it has none), a document
`(kind, name)`, an app its registered id.

**Sessions and the bus.** A files session holds no bus gate: `begin` checks
readiness, every verb takes and leaves the gate around its own op
(`moy_vol_gate_enter`), and one left open ends at the next frame's start and at
the run's end.

## Only launcher launches stop the VM

A launch from the launcher may stop the VM, by the Player's rules
(`native/moy_play/moy_play_stop.c`'s verdict): a VM-free cart, or a compiled app whose every
import is C-served. A launch from any other surface keeps the VM, and the run's
`info` names the reason "place": the Editor's PLAY, a run an app started
(Storybook's and Get Carts' PLAY, Files' RUN), a desk window. After a stopped
run the start follows the kernel's route and lands the launcher on its resume
record (`native/moy_kernel/moy_kernel.h`): **no app gains a way to come back
after a stop** (owner, 2026-10-10). There is no place table; the resume record
is the kernel's and the launcher's alone. A stopped return keeps the kernel's
state, this module's included: the clipboard and the shipped apps' grants,
which the return start re-registers onto the rows they had.

## Where the state lives

The state is the kernel's on a board (`moy_app_kernel`, from the spine's
allocator, so PSRAM): made once, never freed, its prefs the kernel's settings
rows, so a VM stop leaves the grants, the clipboard and the rows for the next
VM; a fresh start ends the RUN grants and clears the clipboard. The browser,
the desktop MicroPython and the CPython host give each console its own
(`App(settings)`). The KSTOP down line reads the kernel's grant count
(`grants=N`), which the T-Deck's suite holds flat across stops.

The host's binding is `runtime/host/moy_app_binding.py`, over ctypes, from the spine's
host library (`tools/moy_index_spike.py`'s SPINE carries these sources), which
the `runtime` package registers as `moy_app`; the user-files layer is a library
of its own there (`runtime/host/moy_ufiles_binding.py`), whose verb table `store_bind`
hands the state. The Zero denies the module: it runs no apps, and its sync
moves user files by path.

The nets: `tests/test_userfiles_parity.py` (the user-files layer's bytes, on
both bindings, against the Python it replaced), `tests/test_moy_app.py` (one
script over the Python oracle
`tests/app_twin.py`, the ctypes binding and the native module on the desktop
MicroPython in the boards' object model, compared line for line),
`tests/test_roles.py` (the table, the C rows' order, the per-frame budgets),
the roles trace in `tests/test_semantic_traces.py` and its trace for a Lua
app (the Python app's line for line, on the host and on the desktop
MicroPython), `tests/test_lua_roles.py` (a Lua app's names held, absent
and turned off, and its `io`/`os` refused outside its kind) and
`tests/test_app_runs.py` (two compiled apps, and a Lua app beside a compiled
one, each reaching only its own grant; a permission turned off absent at a
run's next call).
