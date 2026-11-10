# moy_app — the app ABI's kernel half

The app ABI's kernel half (#224 sprint 5; the design record is
`docs/history/kernel_appabi_2026-10.md`): the grants every app's context holds,
the grant policy, the roles served in C, and the binding of the rows the
console serves in Python. The role table is the role rows of
`native/os_calls.json`, the one source of Moybyte's app and OS calls (one row
per verb, its `server` saying where it is served, each tier's name for it),
from which `tools/gen_os_table.py` writes this module's tables;
`tests/test_roles.py` holds the C rows to it.

| file | what it is |
|---|---|
| `moy_app_rows.h` | generated: the roles (`MOY_ROLE_*`, and `MOY_ROLE_FLOOR`, the roles every grant holds), the C rows (`MOY_ROW_*`), the role table (`MOY_T_*`, `MOY_APP_TABLE_ROWS`) and the permission each role is granted by (`MOY_APP_PERMS`, the allowlist) |
| `moy_app_wasm_rows.h` | generated: the `moybyte.app` natives, one per row a compiled app imports, each packing its fields for the ROLE door, and the table row each serves |
| `moy_app_lua_rows.h` | generated: the names a Lua run's role binding opens (`MOY_APP_LUA_NAMES`) and the console calls a Lua service's `moybyte` table holds (`MOY_APP_LUA_CALLS`) |
| `moy_app.h`, `moy_app.c` | the state (`moy_appabi_t`): the grant table (kind GRANT, rows keyed by the app's id, SHIPPED idempotent by id, RUN ended with its run, each with the surface row the shell writes), the damage flags, the live token table under its generation (the theme's vocabulary, `moy_app_token_name`), the pointer the surface rows read (a `moy_input_ptr_t` the console binds, let go when the VM ends), Paint's open picture (its `paint_doc` and `paint_doc_kind` settings rows, JSON strings, read and followed by the artwork rows), the clipboard's 4 KiB of text, the user-files store the files and wallpaper-copy rows reach (the layer's verb table `moy_uf_ops`, the carts root, readable and writable, the session count, the copy's generation), the per-row counters, prefs written into a settings store; the ROLE door (`moy_app_role`: any table row by its index, its arguments packed as length-prefixed fields, a C row run here with a blob the layer allocated copied out and freed, a shell row handed to the door `moy_app_door_bind` binds, the kernel's counted `moy_loop_role`); the policy (permission to role, the files kinds, the two-kinds refusal, the key a cart's grant is made under); the C rows, each store row taking the store's lock and the bus gate around its own op (`moy_vol_gate_enter`, NEEDS_VM where its task cannot reach the store), a prefs write made off the VM's task owed to the VM's next frame (`moy_app_files_end_all` saves it), and a grant made with a files kind reaching that kind alone |
| `moy_app_wasm.h`, `moy_app_wasm.c` | the wasm import adapter: one native per row with a wasm type, imported from module `moybyte.app` as `<role>_<verb>`, each packing its arguments for the ROLE door; a row of how "here" (the store's rows and the clipboard's write, a process's messages, timers and log, the device handles, `service_running`) runs on the caller's task and hops only where it answers NEEDS_VM, the shell rows hop to the VM's task (`moy_wasm_on_vm`, weak: a call where there is none), the look's and the clipboard's reads run in place under the seqlock (`moy_app_seq_begin`/`end`); each native's run found by the engine's handle on its call (`moy_app_wasm_run_of`, which the engine's glue defines: `native/moycore/moycore_wasm.c`, `runtime/host/moyhost_wasm.c`), the console's state the theme is read from (`moy_app_wasm_live`), the load's grant check against the run (`moy_app_wasm_admit`), a module's import walk (`moy_app_wasm_imports`, which the VM-free verdict reads too), the hops' count and microseconds and the count of rows answered here |
| `moy_app_run.h` | a run's grant, `moy_app_run_t`: the state, its serial, the grant and the role mask the permissions name, a value the run's owner holds (a Lua state's registry, a wasm run's per-run state in its engine's glue, a process row with the table); `moy_app.h` makes one (`moy_app_run_make`), checks its state is still made (`moy_app_run_live`) and holds the one a VM stop carries (`moy_app_stop_hold`, `moy_app_stopped`, `moy_app_stop_end`, which ends it with every other run grant the stopped VM's processes held). It includes nothing else, so an engine's glue holds one without this module's headers |
| `moy_app_lua.h`, `moy_app_lua.c` | the Lua face: the state's own run, copied into its registry at its open (`moy_app_lua_open(L, run)`), opened into its state as the names its permissions grant (`files`, `prefs`, `set_theme`, `open_app`), each a C function packing its arguments for the ROLE door and answering as the Python face does (`(value, err)` as two results, a value of prefs as JSON), called in place on the VM's task, or, for a state on a task of its own (a Lua service on a P4, `moy_app_lua_open_hop`), a row of how "here" on that task (`MOY_APP_LUA_HERE`) and any other run through the opener's hop; and with `files`, Lua's `io` and `os.remove`/`os.rename` over the grant's one kind, a path that climbs, is absolute or names a folder refused (EACCES); `os.time`/`clock`/`difftime` for every app run. `moycore_lua.c` opens it, weak, with the state; it compiles to nothing without Lua's headers |
| `modmoy_app.c` | the MicroPython binding, module `moy_app`: `App(settings)`, `kernel(fresh)`, the shell's writes (`surface_write`, `bind_pointer`, `theme_write`, `serve`, `store_bind`), the role types over a grant -- `Damage`, `Surface`, `Theme`, `Files`, `Prefs`, `Artwork`, `Clipboard` with C rows, `Carts`, `Nav`, `Notify`, `Wallpaper` (its copy's two rows C), `Install` served in Python -- `grant_id`, `app.role` (the ROLE door), `table()` and `door_bind(on)`, `app.run(g, roles)` (a run's bytes, which a Lua or wasm run's open copies), `app.stop_hold(run)` and `app.stop_end()`, `wasm_rows()` and `wasm_hops()`, and the policy. A shell row checks the grant holds its role (`moy_app_holds`), counts the call in `served()` and calls the server the console registered for the role (`runtime/shell/shell_servers.py`) with the grant first, allocation-free once the row has been called |
| `micropython.cmake`, `micropython.mk` | the boards', and the desktop's and browser's, builds; it reads `native/moy_spine`'s headers and links beside it, `native/moy_input`'s pointer type, and `native/moy_lua`'s headers for the Lua face. They also build `native/moy_store`'s user-files layer (`moy_ufiles.c`) and its module `moy_ufiles` (`modmoy_ufiles.c`), the files role's server: an image that denies this module carries neither |

## One table, a server per verb

The role table is data, and every verb has one of four servers:

- **`c`**: the state is the kernel's and resident (the settings rows, the
  glass, the lease table, the engine's sizing, the user-files layer). Each verb
  is its own C function taking the grant; there is no by-name dispatcher.
  The `moybyte` role's C rows, the crypto (`random`, `sha256`, `hmac_sha256`,
  `aes_gcm_seal`, `aes_gcm_open`, `aes_ctr`, `aes_ccm_seal`, `aes_ccm_open`,
  `x25519`, `ed25519_public`, `ed25519_sign`, `ed25519_verify`), keep no state
  at all: the door runs `native/moy_net/moy_crypto.h`'s body in place (`how` "seq"); every
  Lua state holds them in its `moybyte` table, calling the body directly, a
  service with no grant too (`moy_app_lua_open_crypto`); and the module's
  functions of the same names are Python's (`runtime/system/moybyte.py`).
  Its rows over the kernel's own locked state -- a message's `send` and
  `recv`, a listen's `on`, `log`, the timers `every`, `after` and `cancel`,
  and the alarm `at` (under the `alarms` the process's event queue was
  opened with, `moy_evq_at`) -- are the
  process's whose grant it is (the grant's owner, its pid), run where they
  are called (`how` "here"): on a compiled program's own task, never a hop,
  so they answer with no VM. A "here" row that answers `MOY_APP_NEEDS_VM`
  is hopped as a "hop" row is. The device handles (`dev_open` to
  `dev_get`) are such rows too: the kernel's handle table
  (`native/moy_kernel/moy_dev.h`) serves the kinds it owns, as the grant's
  owner under the grant's device permissions (`moy_app_grant_devs`, set from
  the run's granted permissions), and any other kind or handle answers
  `MOY_APP_NEEDS_VM` there and is handed to the door's Python half on the
  VM's task (`door: true`, so its fields are the door's). So is
  `service_running`: the pid of the app's service where a kernel tier runs it
  live under the grant's app id (`moycore_svc_of`, `moy_os_service_of`,
  `moy_wasi_service_of`, each tier's table under its lock), else the door's
  Python half's (`Scheduler.own`), and `service_stop`: that service hung up
  there (its tier's TERM) and named to the scheduler (`moy_evq.h`, THE STOPS
  ASKED), which ends it as its own stop does once a VM runs; a Lua program, whose calls are the VM's
  already, keeps the console call (gen_os_table's `py_twin`). Lua's `send`, `log` and wakes are its own
  forms of them (`lua_own`: a Lua service's C, a Lua app's prelude over the
  `moybyte` module).
  The store's rows (`files.*`, `prefs.*`) and the clipboard's write are C
  too, and run where they are called as well (`how` "here", owner
  2026-11-09, #246): a compiled or Lua program's store op runs on its own
  task. The store keeps them apart from the VM's with one lock
  (`native/moy_store/moy_vol.h`, THE STORE'S LOCK): a row holds it for its
  whole op, from the gate it takes (`moy_vol_gate_enter`, the card's bus
  gate inside it) to the gate it leaves, and VfsFat's and KVfs's calls take
  it around each FatFS or littlefs call, so the VM waits for one op of a
  program's at most and never inside a library call; the VM's task goes
  first when both wait. A path resolves off the VM's task through the volume
  table (THE VOLUME TABLE: the kernel's card over its native driver, the
  kernel's flash, POSIX at "/"), never through MicroPython's mount table, and
  nothing raises there; a store the table does not hold answers
  `MOY_APP_NEEDS_VM` and the row is hopped. A row's gate and its errno are
  its task's (`moy_app_why`), and so is the scratch a raised call's unwind
  frees. The settings rows are read and written under the settings lock
  (`native/moy_spine/moy_settings.h`, THE SETTINGS LOCK), which the Python
  binding takes too; a `prefs.set` or `prefs.clear` made off the VM's task
  leaves its row in the rows and is saved by the VM's next frame
  (`moy_app_files_end_all`, through the rows' saver, which is Python's), or
  with no VM by the next start's load. The clipboard's write is the
  seqlock's writer, as the shell's writes of the look and the clipboard
  are, and every writer holds the kernel's writer lock
  (`native/moy_kernel/moy_lock.h`), so the seqlock has one writer at a time
  whatever task writes.
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
- **`call`**: a console call (the `moybyte` role, which every grant holds):
  the `moybyte` module's function (`open_with`, `share`, `pick`) run as
  the process whose grant it is
  (`runtime/shell/shell_servers.py`'s `ConsoleCalls`), so the permission it
  names is that process's. A native command reaches them through the door; a
  Lua service and a WASI program through the VM's queue; a compiled app
  through its imports. The rows the kernel answers first reach the door
  where it has no answer: an item a compiled program was handed
  (`item_read`, `item_write`) that is not a user file in the kernel's table
  (`native/moy_kernel/moy_items.h`), or a store its task cannot reach;
  `service_stop` and `service_running` for a service no kernel tier runs.
  The door hands the Python half an answer buffer's capacity as a
  number field, so a read that does not fit keeps what it read. An import of the `moybyte` role is admitted
  for every grant (`MOY_ROLE_FLOOR`); the permission a call names is checked
  at the call.
- **Python-only**: a row no other tier imports, whose only consumer is Python
  on paths and objects (`install.net`, `install.keep`, `install.op`).

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
row stays the authority: each run holds a RUN row of its own, its pid the
row's owner (a foreground run's and a service's alike), and when Settings
turns a permission off or on every live RUN row of the app is recomputed
from what its kind of run is now entitled to (`moy_app_regrant`, reached
through `app_perms.regrant`), so a run's next call to a role turned off
answers `DENIED`. A copy of a
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
