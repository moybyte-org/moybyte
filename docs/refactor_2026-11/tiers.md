# Refactorability audit: the UI toolkit, the app tiers and their bindings, the generators, the build and the tools

Tree: origin/dev at 5465b571c. Read-only. Paths are repo-relative; line numbers are from that commit.

## 1. Health verdict

The *engines* are in good shape: one toolkit in C (`native/moy_ui`), one app ABI in C (`native/moy_app`), one call table (`native/os_calls.json`) whose generator already writes the ELF, WASI, wasm, Lua-name, permission, SDK and doc projections. Drift guards exist for every generated file. The cost is in the **faces**. Every native module is reached through a hand-written MicroPython face (`native/*/modmoy_*.c`: 41 files, 1,425 `mp_obj_t` functions, 893 of them 8 lines or fewer), a hand-written CPython ctypes face (`runtime/host/*`: 23 files repeat the same `_SIGS` and argtypes loader, plus about 40 hand-mirrored `ctypes.Structure`s), and, for app-visible rows, a hand-written Lua face (`moy_app_lua.c`, `moycore_ui.h`) and a hand-written run/wasm face (`moy_ui_wasm.c`). Constants, error texts, the UI override-table schema and the Lua JSON encoder are each restated in 3 to 5 places, with nothing guarding them, and in two places they already disagree. The UI context (`moy_ui_ctx_t`) is a 177-field god struct. Its per-widget state is a 16-slot table whose fields mean different things for different kinds, and it drops state silently when the table is full. The build and `tools/` are mostly healthy: the board build is "solved" data plus a patch ladder, and PNG and board helpers are already shared. Two exceptions: one dead tool, and one load-bearing "spike" tool. **The highest-payoff move:** a face generator driven by the call table plus a per-module face table, with unix MicroPython as its parity oracle. Do not move the host onto unix MicroPython (§2.4).

---

## 2. Main deliverable: generating the binding faces

### 2.1 What exists, per verb

Take `ui.button` as the example. It is hand-written in seven places. The engine is the only part that is logic.

| layer | file | lines/verb | generated today? |
|---|---|---|---|
| engine | `native/moy_ui/moy_ui_api.c` `moy_ui_api_button` | logic | n/a |
| MicroPython face | `native/moy_ui/modmoy_ui.c:2829-2838` + table row `:4276` | 11 | no |
| ctypes argtypes | `runtime/host/moy_ui_binding.py:331` | 1 | no |
| ctypes wrapper | `runtime/host/moy_ui_api_binding.py:84-89` + install list `:818` | 7 | no |
| run face | `native/moy_ui/moy_ui_wasm.c:304-309` `moy_ui_run_button` | 6 | no |
| wasm import | `native/moy_ui/moy_ui_wasm.c:655-662` `moy_ui_wasm_button` | 8 | prototype only (`moy_ui_wasm_rows.h`) |
| Lua face | `native/moycore/moycore_ui.h:306-324` + `luaL_Reg` row `:1056` | 19 | no |
| Python public verb | `runtime/toolkit/ui.py:345-350` | 6 | no |
| SDK header | `sdk/include/moybyte/ui.h` | | yes |

The override table (`o`, about 30 keys: `moy_ui_api.h:180-300`) is encoded or decoded by hand **five** times:
- `runtime/toolkit/ui.py:187-280` `_block`, used on the host;
- `modmoy_ui.c:2380-2475` `over_qstr`/`over_slot`/`over_dict`, used on the boards;
- `moycore_ui.h:137-230` `moycore_ui_over`, used by Lua;
- `moy_ui_over_block` in C, used by wasm and the host;
- the bit list in `moy_ui_api.h` itself.

The host never runs the boards' dict decoder (`ui.py:178` `_OVER_DICT = getattr(_C, "over_dict", False)`), and the boards never run `_block`. One unix-MicroPython page covers the crossing (`tests/test_ui_opts_seq.py`). That covers only the keys that page uses.

App-ABI rows (crypto, clipboard, theme, files) have the same shape: `modmoy_app.c:2170` `mod_sha256`, `moy_app_binding.py:312` `sha256`, `moy_app_lua.c:832` `l_moybyte_sha256`. Only the wasm and WASI projections go through the generated, generic door.

### 2.2 What the table needs that it lacks

`os_calls.json` rows already carry `in` (`int`, `str`, `str?`, `bytes`, `cart`, `out`), `ret` (`int`, `len`), `how` and the tier names. That covers about 70% of the app rows. The kernel-face modules (`moy_log`, `moy_lease`, `moy_evq`, `moy_gfx`…) are **not** in `os_calls.json`: they are the console's private ABI, not the app ABI. They need a second table.

**Proposed: two sources, one generator.**

1. **`native/os_calls.json`** (app rows, including `ui.*`) gains per-row fields:
   - `py_sig`: the Python face's parameter list with defaults, for example `["label", "kind='normal'", "o=None"]`;
   - `enum`: an argument that crosses as a name on Python and Lua and as an index on wasm, for example `{"kind": "moy_ui_button_kinds"}`. Today `ui.py:349` and `moycore_ui.h:308-316` each do this lookup by hand;
   - `base`: the index base a tier answers in, for example `{"cur": {"py": 0, "lua": 1}}`. The 0- versus 1-based list cursor is deliberate and currently lives in prose (`moycore_ui.h:13`);
   - `list`: an `items` argument that is a sequence on Python and Lua and NUL-joined bytes on wasm. Today that is `ui.py` `_opts`, `modmoy_ui.c` `opts_of` and `moycore_ui_items`;
   - `out_shape`: how an `out` answer comes back:
     - `int4` gives a tuple on Python and 4 returns on Lua (`ui.page`);
     - `text` gives str;
     - `bytes`;
     - `names` gives a list split on NUL;
     - `json` gives a decoded value;
   - `lua_ret`: Lua's convention:
     - `value`;
     - `value_err` (`moy_app_lua.c:276` `ret_text`);
     - `value_err_absent_nil`;
     - `bool`;
   - `end_form`: true where a call with no arguments ends a nest (`ui.group()`, `ui.scroll()`, `ui.dialog()`, `ui.popover()`, `ui.grid()`);
   - `cap`: an `out`'s first capacity, and `grow: true` where FULL retries eightfold (`moy_app_lua.c:197` `row_call_cap`);
   - `example`: arguments a probe calls the row with, for §3's test.

2. **`native/<module>/face.json`**, a new per-module file, for the private faces. Each row names its C function. The generator **reads the prototype from the module's header** with the C-prototype parser `gen_os_table.py:494` `parse()` already has, so nothing restates the C types. The row adds only what the C types cannot say:
   - **types**:
     - `u32`/`i32`/`u8`/`bool`/`f32` follow from the prototype;
     - a `(const char *p, size_t n)` pair is marked `span` (str or bytes in, one Python argument);
     - `str?` is None→NULL;
     - `truncated` is `mp_obj_get_int_truncated` for handles and pids, versus `mp_obj_get_int`;
   - **ownership**:
     - `borrow` (a returned `const char *` copied into a Python str, `moy_log_name`);
     - `own:<free_fn>` (a buffer the C allocated: `moy_app_binding.py:71` `_Buf`);
     - `keep` (an argument the C holds after the call, so the face must pin it: the `ui` context bytearray, a canvas buffer);
   - **buffers**:
     - `out(cap=CONST|param)` gives bytes, str or a struct;
     - `inout` (`moy_ui_api_field`'s text buffer);
     - `canvas` (a `DeviceCanvas` handed down as `moy_canvas *`);
     - `ctx` (the toolkit's bytearray context). The last two are named converters implemented once per tier (§2.3);
   - **struct outputs**: `out:moy_log_stats_t -> (first, next, used, cap)`, the fields by name. The generator emits the ctypes `Structure` from the header, which also retires the hand mirrors (finding T9);
   - **errors**: `err: errno|app|bool|none|<table>`, a name in one error table (finding T5) mapping each code to its exception on Python and MicroPython and its message on Lua;
   - **constants**: `consts: ["MOY_LOG_TEXT_MAX as TEXT_MAX", "MOY_LOG_EMERG..DEBUG"]`. Python then stops restating `TEXT_MAX = 2048` (`runtime/host/moy_log.py:18`) and `range(8)`;
   - **optional arguments and defaults**: `"read(seq=0, n=32)"`;
   - **`noalloc: true`**: a frame-path row whose MicroPython face may allocate nothing. The generator uses stack buffers, and the parity test asserts `gc.mem_alloc()` stays flat, as `test_ui_opts_seq.py` does;
   - **`targets`**: where the row exists, for example `moy_wasm.service_*` is absent on `moy_wasm_web`. The absence is declared, not discovered (`modmoy_wasm_web.c` registers a subset under the same module name).

**Callbacks** (`moy_evq` handlers, `moycore.register`, `moy_input` sinks) are rows marked `hand: true`. The generator only checks that the hand function exists in both faces.

**Override schema.** `+native/moy_ui/over.json` lists each key: name, kind (flag, colour, int, int2, int4, text, text list, enum), its flag bit and word order. It generates:
- the `MOY_UI_O_*` defines;
- `moy_ui_over_block` (the decoder);
- `ui.py`'s `_block` (the encoder);
- `modmoy_ui.c`'s `over_slot`/`over_dict`, still a single walk of the dict map, so it still allocates nothing;
- `moycore_ui_over`;
- the docs' key table.

### 2.3 What stays hand-written

- Every engine (`moy_ui_api*.c`, `moy_app.c`, the kernel).
- Per-tier **converters**, one small file each:
  - `+native/moy_face/face_mp.h`: canvas, ctx, span, error raise;
  - `+runtime/host/face_ctypes.py`: the same converters plus the one library loader that replaces the 23 copies of `_lib()`;
  - `+native/moy_face/face_lua.h`.
- MicroPython **object types and methods**: `moy_glass` compositors, `moy_app.App`, the `moy_play` session. The generator can emit a type's method rows, but the type struct and its `make_new` stay by hand.
- Callbacks, iterators, anything that holds a Python object across calls (GC roots).
- Hot-path specials whose hand code is the point:
  - `label_text` (`modmoy_ui.c:2768`);
  - the frame verbs pinned by `tests/test_frame_alloc.py`.

  Their *signature row* is still in the table, so the name and arity parity is checked.
- `runtime/toolkit/ui.py`'s public verbs: the docstrings are the API's documentation. Generate only the bodies, between GENERATED markers, as `gen_os_table` already does for `app_context.py` and `scheduler.py` (`gen_os_table.py:1418` `_splice`).
- The Lua preludes in `runtime/run/lua_ext.py` that wrap handles in tables (layers, editor, grants). These are object-valued verbs, and the DENY-list rule in `.claude/rules/shell.md` says they stay in that one module.

### 2.4 The alternative: run the host on unix MicroPython with the real modules

What exists already:
- `make unix-micropython` builds 16 real modules, in a 64-bit build and the boards' 32-bit REPR_C build;
- 69 test files drive it through `tests/unix_mp.py`;
- `test_semantic_traces` boots the T-Deck desktop under it;
- the web runner runs the whole console on MicroPython-wasm with the real modules.

So the console demonstrably runs off-board on MicroPython.

**What it would delete:**
- about 11k lines of ctypes faces;
- `runtime/host/moyhost_lua.c` (567), the WASI half of `moyhost_wasm.c` and `moyhost_elf.c` loaders;
- `native_build.py`/`host_native.py` (515);
- `runtime/__init__.py`'s registration shims;
- and the whole class of "host face disagrees with board face" bugs.

**What it would cost:**
- **The test suite.** 433 of 567 test files import `runtime` in-process on CPython, and 174 use `monkeypatch`. pytest does not run on MicroPython, so every such test becomes a driver script plus a subprocess, as the 69 existing ones are. That is a rewrite of most of the 245k test lines, or a new in-VM test framework.
- **The simulator.** `tools/simulate_desktop.py`/`host_app.ConsoleDriver` use pygame. Unix MicroPython needs an SDL bridge or a pipe to a CPython window (the web runner's worker protocol, reused).
- **CPython-only host modules.** `lora_sim`, `gatt_sim`, `lease_sim`, `mic_sim`, `midi_sim`, `usb_sim`, `bus_sim` and `camera_sim` stand in for hardware the C modules expect. They become C sims or MicroPython-side Python behind a different seam.
- **Tools that render.** `tools/look_shots.py` (4,108 lines), `golden_png.py` and `focus_shots.py` build a Workstation in-process.
- **Debuggability.** No pdb, coverage, `inspect.getsource`-based web bake (the `dead-code-pruning` memory) or tracebacks with CPython's detail.
- **Speed.** Pure-Python shell code runs slower on MicroPython than on CPython. The suite already runs under xdist to stay tolerable.

**Recommendation: the generator.** It removes the duplicated *source* without moving 433 test files. Use unix MicroPython as the **oracle** in the migration's parity test, which is what it is already good at. The two converge: once every face is generated from one table, the remaining host/board difference is the table's converters, and those are small enough to audit. Revisit "host on MicroPython" only if the parity test keeps finding converter bugs. Even then, the right step is to move selected *suites* (the frame-alloc and board-model ones already are), not the developer host.

### 2.5 Migrating one module at a time behind a parity test

1. **Generator first, no module switched.**
   - Split `tools/gen_os_table.py` (2,006 lines, ~20 outputs) into a package `tools/gen_os/`: the loader and rules, the C prototype parser, one emitter per tier (finding T11).
   - Add `face.json` reading and the emitters for:
     - `native/<m>/modmoy_<m>_face.c`: an `#include`-able file defining the functions and a `MOY_FACE_<M>_ROWS` macro the hand globals table expands;
     - `runtime/host/<m>_face.py`: the ctypes rows plus wrappers.
   - Committed output plus a drift test, as now.
2. **The parity test** (`+tests/test_face_parity.py`), parametrized over every `face.json`:
   - **names**: the MicroPython globals table (parsed as `tests/test_moycore_glue.py:105` does) equals the ctypes module's public names equals the table's rows, minus declared absences;
   - **behaviour**: one generated driver script per module calls each row with its `example` arguments and prints normalized answers (value, exception type, allocation delta). The script runs under CPython (ctypes face) and under `require_unix_mp(<m>, board_model=True)`, and the two outputs must be equal. This is `tests/test_gfx_binding.py`'s op-script pattern, generalized;
   - **hand versus generated**, during migration only: the same script against the hand face and the generated face, on both runtimes, so four columns agree before the switch.
3. **Per module, one commit each:**
   - generate beside the hand face (as `<m>_gen`);
   - the four columns agree;
   - switch the globals table and the host import;
   - delete the hand rows;
   - the parity test keeps names and behaviour.
4. **Order**, smallest and most mechanical first:
   1. `moy_log` (constants, struct out);
   2. `moy_lease`;
   3. `moy_alloc`;
   4. `moy_prof`;
   5. `moy_mic`/`moy_midi`/`moy_http`;
   6. `moy_store`/`moy_ufiles` (out buffers, ownership);
   7. the app ABI's crypto, clipboard and theme rows (all four tiers from `os_calls.json`);
   8. `moy_evq` minus callbacks;
   9. `moy_ui` (finding T2, the biggest and best covered by goldens).

   `moy_glass`, `moy_play`, `moy_wasm` and `moycore` last or never: mostly object types and callbacks.
5. **Board check:** one board pass per batch, because the MicroPython faces compile into every image. Size deltas print in the build.

---

## 3. Findings, ranked by payoff

### T1. Hand-written MicroPython and ctypes faces for ~1,400 functions (the generator of §2)

- **Evidence:**
  - `native/*/modmoy_*.c`: 41 files, 31,329 lines, 1,425 functions, 893 of them 8 lines or fewer (4.2k lines), plus ~2.8k lines of `MP_DEFINE_*`/table rows.
  - `runtime/host/*`: ~19k lines. 23 files carry the `for name, args, res in _SIGS: fn.argtypes = …` loader (for example `moy_log.py:53-61`, `moy_lease.py:83-95`).
  - Name drift is unguarded for every module but `moycore` (`tests/test_moycore_glue.py:105`). For example, `moy_ui`'s host has `api_floor` (`moy_ui_api_binding.py:443`, used only by `tests/test_shift_frames.py`) and the board has no such name, and no test notices either way.
- **Do:** §2.2–2.5.
- **Lines:** about −6k to −8k net. About 9–11k hand lines become ~1.5k generator, ~3k of table JSON and ~0.6k of converters. The bigger gain is that a new verb is one table row plus the engine.
- **Risk:** medium.
  - The MicroPython faces are on every image. Integer truncation, None handling and allocation behaviour are what the generated code can get subtly wrong. The parity test's four columns and `noalloc` catch these.
- **Guard:** `+tests/test_face_parity.py` (names plus behaviour on both runtimes), and the generator's drift test.
- **Depends on:** T11 (split the generator first) and T5 (one error table).
- **Board:** yes, one pass per migrated batch (images rebuild).

### T2. The UI verbs: six hand faces per verb, the override table five times

- **Evidence:** §2.1.
  - 46 `ui.*` rows (`os_calls.json`).
  - 113 `api_*` names in `modmoy_ui.c` against 113+2 in `moy_ui_api_binding.py`.
  - 45 `moy_ui_run_*` plus 46 `moy_ui_wasm_*` in `moy_ui_wasm.c`.
  - 46 `moycore_ui_*` in `moycore_ui.h`.
  - Override codecs: `ui.py:187`, `modmoy_ui.c:2380-2632`, `moycore_ui.h:137`, `moy_ui_over_block`, and the defines in `moy_ui_api.h:262-300`.
  - Enum lookup by hand on two tiers: `ui.py:349` and `moycore_ui.h:308`.
- **Do:**
  - Extend the `ui.*` rows with §2.2's fields and add `+native/moy_ui/over.json`.
  - Generate the `ui_api_*` MicroPython functions, the ctypes `api_*` and argtypes, `moy_ui_run_*`, `moy_ui_wasm_*`, `moycore_ui_*` plus its `luaL_Reg`, and the `ui.py` bodies between markers.
  - Keep `label_text` and the verbs marked `hand`.
- **Lines:** about −2.5k (46 verbs × ~55 glue lines) and −600 (the codecs).
- **Risk:** medium. Pixel goldens (`test_shell_goldens`, `test_app_ui`) and `test_ui_opts_seq` already hold the observable behaviour. Zero-allocation on the boards' dict path must survive generation.
- **Guard:**
  - T3's every-row test;
  - `test_ui_opts_seq` extended to drive every `over.json` key (generated from the schema), not one page's keys;
  - `tests/test_frame_alloc.py`.
- **Depends on:** T1's generator and T7 (split the run face out of `moy_ui_wasm.c`, so the generated file has one job).
- **Board:** yes, one pass on the T-Deck plus a P4: goldens and frame alloc.

### T3. No test walks every call-table row on every tier

- **Evidence:**
  - `tests/test_semantic_traces.py:2065-2370` (the roles trace) drives every role row through a Python `AppContext`, on CPython and on the board model. Nothing does the same for a Lua run's names, the wasm imports or the WASI imports. Its docstring points the toolkit rows to tests/test_ui_wasm.py (`:2357`), a file that does not exist.
  - `tests/test_roles.py:183` checks only that the `py` names are callable on `moybyte`.
  - Nothing references `LUA_RUN_CALLS`/`RUN_VERBS`/`LUA_OWN` in `tests/`.
  - `tests/test_app_ui.py` compares tiers' pixels for three features (clip, tabs, back), not per row.
- **Do:** `+tests/test_every_row.py`, table-driven:
  - for each row with a tier projection, a probe built from its `example` (§2.2);
  - the probe apps are generated, never hand-kept:
    - a Python app calling `ctx.<role>.<verb>`/`moybyte.<verb>`;
    - a Lua app calling the same names;
    - a wasm module importing every `wasm: true` row and calling each by a dispatch index, assembled by `tools/wat.py` (no compiler);
    - a WASI command for the `wasi: true` rows, assembled the same way;
  - each prints one normalized trace line per row: answer class, value and a canvas hash for `ui.*`;
  - compare each tier's trace to the Python one, with a coverage ratchet as `roles_trace`'s "uncalled";
  - run the Python column on CPython and on the board model (`require_unix_mp`).
- **Lines:** about +600 test code, generator included.
- **Risk:** low (test only).
- **Guard:** it is the guard. It also gives T1, T2 and T4 their safety net.
- **Depends on:** the `example` field (§2.2). It can land before T1 and T2 with examples for the existing rows.
- **Board:** no.

### T4. A Lua app's `moybyte` table is a second implementation of the Lua service's, with its own JSON encoder and a source-text callback path

- **Evidence:**
  - `runtime/run/lua_ext.py:726-829` `PRELUDE_MOYBYTE`:
    - a JSON encoder written in Lua (`enc`, `args`, `:744-779`);
    - `every`/`after`/`at`/`on`/`cancel` over Python `__moybyte_wake`.
  - `lua_ext.py:946-1019` `_RunWakes` fires a wake by building Lua *source text* `"__moybyte_fire(%d, …)"` with `lua_lit` (`:909`) and `exec`ing it. That is a third serializer, and it allocates per fire.
  - The Lua service's table is C: `native/moycore/moycore_svc.c:632-672`. Its `l_console` (`:504`) encodes with `moy_app_lua_json_dump` (`native/moy_app/moy_app_lua.c:585`), and its timers and `on` are C over `moy_evq` (`:352-380`).
  - The two encoders can disagree: Lua's sorts keys, encodes NaN as `NaN`, and limits depth to 16. The C one is the store's scanner's twin.
- **Do:**
  - Give a Lua *app* run the C table `moycore_svc.c` builds. Factor `open_names`' `MOYBYTE` half into a function the run's role binding calls.
  - Its wakes become `moy_evq` wakes under the run's pid, fired at the frame's start by the Player instead of through `exec`.
  - Delete `PRELUDE_MOYBYTE`'s encoder and wake half, `_RunWakes` and the `lua_lit` fire path.
  - Python's `Wakes` then serves Python apps only. Folding it onto `moy_evq` is the scheduler auditor's half.
- **Lines:** about −250 (Python plus Lua source in strings).
- **Risk:** medium.
  - The firing moment moves from Python's scheduler to the Player's frame start, so ordering against Python wakes in the same frame can change.
  - `test_semantic_traces`, `test_lua_services` and `test_app_entries` hold the order.
- **Guard:** T3's Lua column, plus a test that a Lua app's and a Lua service's `moybyte` names are the same set (from `gen_os_table.lua_names`).
- **Depends on:** the other auditors' timer finding (one scheduler). It can land before it, because it removes one of the three.
- **Board:** yes, one pass, because the Lua tier is on every console.

### T5. Error codes are mapped to exceptions and messages by hand per tier, and the tiers already disagree

- **Evidence:**
  - `modmoy_app.c:82-97` `raise_rc` and `moy_app_binding.py:226-236` `_raise` map `MOY_APP_FULL` to `OSError(ENOSPC)` ("the grant table is full").
  - `moy_app_lua.c:233-250` `raise_rc` maps the same code to "the answer is too long", and also knows `NEEDS_VM`, which the Python faces do not.
  - `push_err` (`moy_app_lua.c:252`) restates the Python face's `err` texts, including "NO STORAGE", which is also in `runtime/shell/app_context.py:95`.
  - Per-module `_check`/`check` copies: 4 in `runtime/host`, 5 in `native/*/modmoy_*.c`, each with its own errno table (for example `moy_lease.py:98`).
- **Do:**
  - One `errors` section in the table: code → (Python exception, errno, message, Lua message).
  - Generate `moy_app_err.h` (C plus Lua) and `+runtime/host/face_errors.py`.
  - Decide FULL's meaning once: it means two different things today.
- **Lines:** about −150.
- **Risk:** low. Make the FULL decision explicit in the commit.
- **Guard:** a test that every `MOY_APP_*` code in `moy_app.h` has a row and that both faces raise the row's exception (T1's parity script).
- **Depends on:** nothing.
- **Board:** no.

### T6. The WASI command and service step runner exists twice

- **Evidence:**
  - Board: `native/moy_wasm/modmoy_wasm.c:1522-2077`:
    - `cmd_up`/`cmd_call`/`cmd_done`/`cmd_serve`/`cmd_terminate`;
    - `svc_*`;
    - `sess_wait` (`:836`).

    These are the stepping and hop protocol of `moy_wasi.h`'s "IN STEPS", written inside the MicroPython face.
  - Host: `runtime/host/moyhost_wasm.c:700-960`:
    - `job_hop`/`job_main`/`job_done`/`job_serve`/`job_terminate`;
    - `hw_wasi_start`/`step`/`stop`/`service`.

    The same protocol, again, on pthreads.
  - The boards also run pthreads (`moy_wasm_thread.h`).
  - How a module *executes* (AOT versus interpreter) is host policy and stays split (`native/moy_wasm/README.md`, "Decisions"). The runner around it is not.
- **Do:**
  1. Diff the two runners first.
  2. Move the shared runner into `+native/moy_wasm/moy_wasi_job.c`: a pthread job with hop, serve, done and terminate, parametrized by a start callback.
  3. Make `modmoy_wasm.c` the MicroPython face over it, and `moyhost_wasm.c` the ctypes face over it.
- **Lines:** about −300.
- **Risk:** medium. The stop and terminate paths are where boards hang. The README's "A command" section and `test_elf_commands`/`test_wasm_app` hold the host half.
- **Guard:** the host suites then run the boards' runner. Add a test that `moyhost_wasm.c` defines no `job_*`.
- **Depends on:** nothing.
- **Board:** yes, one P4 pass (WASI command and service on glass).

### T7. `moy_ui_ctx_t` is a god struct; per-widget state is overloaded and drops silently

- **Evidence:**
  - `native/moy_ui/moy_ui_api.h:425-751`: 177 field declarations under prefixes per verb family:
    - `tbl_`, `tc_`, `tp_` (tables);
    - `dlg_`, `dw_` (dialogs);
    - `pop_` (popovers);
    - `sh_`, `fl_`, `kf_` (shift frames, floor, kept floor);
    - `snap_`, `bar_`, `fe_`, `spread_`, `txt_`.

    The engine's files (`moy_ui_api_table.c`, `_pop.c`, `_kept.c`, `_host.c`) each reach into all of it.
  - `moy_ui_keep_t` (`:322-337`) overloads `a`/`b` ("a page: its scroll, its rows' height; a list: its row").
  - `mua_keep` (`moy_ui_api.c:534-554`) returns NULL when the 16 slots (`MOY_UI_API_KEEP`) are taken. The widget then simply has no kept state, and nothing tells it the way `dup`/`lost` are told for stops (`:149-150`).
  - The run face (`moy_ui_run_*`, used by Lua *and* wasm) lives in `moy_ui_wasm.c` beside the wasm import decoders.
- **Do (no behaviour change):**
  - Group each family into a named sub-struct (`moy_ui_tbl_t tbl; moy_ui_dlg_t dlg; moy_ui_floor_t fl; …`) declared in the header of the file that owns it.
  - Give `moy_ui_keep_t` named fields per kind (a union tagged by `kd`).
  - Count keep overflow as `keep_lost` and tell it as `lost` is told.
  - Move `moy_ui_run_*` to `moy_ui_run.c`.

  The context is opaque bytes to every face (`api_size()`/bytearray; no ctypes mirror of it), so the regroup is ABI-free.
- **Measure-then-draw:** keep the single-pass, previous-frame-stops model. A measure pass would roughly double verb cost on the S3. The shift-frame, floor and kept-table machinery (`surface_model_v1.md` §14) is built on "this frame draws from last frame's record", and §8 is closed. The one-frame lag is already explicit (`owe`, `moy_ui_api_owes`). Make it observable rather than remove it: count frames owed per page in the PERF line's UI field.
- **Lines:** about 0 net, but the 177 flat fields become ~12 owned groups.
- **Risk:** low. It is a mechanical rename, held by the goldens and `test_ui_instance`.
- **Guard:**
  - a test that each engine file touches only its own sub-struct, a grep ratchet like `test_console_facade`'s;
  - a test that keep overflow increments `keep_lost` (16+1 pages in one context).
- **Depends on:** nothing. Do it before T2 so the generated run face lands in its own file.
- **Board:** no (host goldens). It joins T2's board pass.

### T8. Two JSON readers in the native tree; the theme's reads untrusted packages

- **Evidence:**
  - `native/moy_ui/moy_theme.c:21-208`: a hand JSON reader (`rd_t`, `str`, `number` in hundredths, `skip_nested`, depth 8). It parses user-installed `theme.json` (`moy_theme.h:2` says untrusted).
  - `native/moy_spine/moy_json.c` (993 lines) is "the kernel's JSON scanner: one body". It is fuzzed (`native/moy_spine/fuzz_spine.c`, `native/moy_store/fuzz_fs.c`) and held to CPython's `json`.
  - `moy_ui` already includes `moy_spine` (`native/moy_ui/micropython.mk:14`).
- **Do:** read theme packages with `moy_json`'s scanner, plus a `moy_json_hundredths()` number helper. Delete `rd_t`.
- **Lines:** about −180.
- **Risk:** medium. The two readers differ:
  - the theme reader's depth is 8 against `moy_json`'s 32;
  - number saturation;
  - NaN and Infinity, which `moy_json` takes and a theme must refuse.

  `tests/test_theme_store.py`'s fuzz corpus and `test_theme_sprites` hold the refusals.
- **Guard:** a ratchet test that `moy_theme.c` defines no JSON tokenizer (grep `rd_t`), plus the existing theme fuzz corpus.
- **Depends on:** nothing.
- **Board:** no.

### T9. About 40 hand-mirrored C structs in the ctypes faces

- **Evidence:**
  - `runtime/host/moy_ui_binding.py:115-208,465,485` (`_Box`, `_Surface`, `_Sink`, `_Over`, …);
  - `moy_ui_api_binding.py:975-1099`;
  - `glass_binding.py:73-803`;
  - `moy_log.py:23-35`;
  - `moy_app_binding.py:71-100`.

  Only `_Over` has a size guard (`tests/test_ui_public.py:1018` against `moy_ui_over_size`). A field added in C without the mirror reads garbage on the host only.
- **Do:** generate the mirrors from the headers in T1's emitter, with the struct named in `face.json`. Until then, export `offsetof`/`sizeof` probes from C for every mirrored struct, and add one test comparing all of them.
- **Lines:** about −300 (generated).
- **Risk:** low.
- **Guard:** the offset test.
- **Depends on:** T1 for generation. The offset test stands alone.
- **Board:** no.

### T10. Three ways a host face becomes the module `moy_X`

- **Evidence:**
  - `runtime/__init__.py:36-130` registers `moy_spine`, `moy_app`, `moy_ufiles`, `moy_ui`, `moy_log` and `moy_evq` by hand, each with its own docstring and error message;
  - `runtime/host/net_binding.py:246` self-registers with `sys.modules.setdefault`;
  - other faces (`moy_lease.py`, `moy_input.py`, `moy_play.py`, …) are found by file name on the flattened `runtime` path.
- **Do:** one table `{module: face}` (generated with T1) and one loop in `runtime/__init__.py`.
- **Lines:** about −100.
- **Risk:** low. Import order matters for `moy_spine` first; keep it first in the table.
- **Guard:** a test that every module a board stages (`board_config list-native`) has exactly one host face and is registered by the loop.
- **Depends on:** T1, or it can stand alone.
- **Board:** no.

### T11. `tools/gen_os_table.py` is 2,006 lines writing ~20 files; split it before it grows

- **Evidence:** the docstring (`tools/gen_os_table.py:13-47`) lists 20+ outputs across 5 tiers, all in one module (`grep -n "^def "` shows 120 functions).
- **Do:**
  - `tools/gen_os/`:
    - `+table.py` (load, rules, views);
    - `+cproto.py` (parse);
    - `+elf.py`, `+wasi.py`, `+app.py`, `+lua.py`, `py.py`, `+docs.py`, `+sdk.py`;
    - later `+face.py` (T1) and `ui.py` (T2).
  - Keep `tools/gen_os_table.py` as the entry point and `outputs()` as the one list.
- **Lines:** about 0 net.
- **Risk:** low (drift test).
- **Guard:** `tests/test_os_table.py::test_the_generated_files_are_the_source_s`, unchanged.
- **Depends on:** nothing. Do it first.
- **Board:** no.

### T12. A "spike" tool is load-bearing for the host runtime

- **Evidence:**
  - `tools/moy_index_spike.py` (1,178 lines; "Sprint 1a's harness") holds:
    - the host library `Component`s for index, spine and fs (`:150-200`);
    - `host_library()`;
    - `moy_index`'s ctypes `_SIGS` (`:245`).
  - The runtime imports it by `sys.path` surgery:
    - `runtime/host/moy_app_binding.py:31-34`;
    - `runtime/host/moy_spine_binding.py:25`;
    - `runtime/host/host_native.py:84`.
  - `tools/board_config.py:677` and `tools/esp32_build_lib.sh:883` cite it for `MOY_INDEX_IMPL`.
  - Its bench, sizes and ci-time commands are one-off measurement history (#224).
- **Do:**
  - Move the `Component`/Part definitions and the index `_SIGS` into `+runtime/host/native_parts.py` (beside `native_build.py`). The host faces import that, with no `tools/` on `sys.path`.
  - Keep the fuzz, sanitize and host commands as `+tools/native_check.py`.
  - Delete or archive `bench`, `sizes` and `ci-time` if #224's numbers live in the issue (they should).
- **Lines:** about −400 from `tools/`, and the runtime stops depending on `tools/`.
- **Risk:** low.
- **Guard:** a test that nothing under `runtime/` imports from `tools/`; today `runtime/host/*binding*.py` do.
- **Depends on:** nothing.
- **Board:** no.

### T13. Constants restated in the host faces

- **Evidence:**
  - `runtime/host/moy_log.py:18-20`: `EMERG…DEBUG = range(8)`, `KERNEL, SHELL = 0, 1`, `TEXT_MAX = 2048`, against `native/moy_kernel/moy_log.h:37` `MOY_LOG_TEXT_MAX 2048u` and `modmoy_log.c:176`;
  - the same pattern in `moy_lease.py` (`PIN_MAX`, `_ERR`) and `moy_evq.py`.
- **Do:** T1's `consts`. Until then, add one test that reads each face's constants against the C header's values.
- **Lines:** about −100.
- **Risk:** low.
- **Guard:** T1's names-and-values parity.
- **Depends on:** T1, or the interim test.
- **Board:** no.

### T14. The Lua app ABI is per-row hand C while wasm and WASI ride one generic door

- **Evidence:**
  - `native/moy_app/moy_app_lua.c` (1,775 lines) has a function per row: `l_files_*` (`:291-368`), `l_prefs_*`, `l_theme_*` (`:707-745`), `l_clipboard_*` (`:747-776`), crypto (`:823-941`), `dev_*` (`:1021-1100`), `status_*`.
  - Each does the same four steps: pack the arguments as the door's fields, `row_call`, then shape the answer with `ret_text`/bool/int.
  - The WASI tier serves the same rows generically from their `in` types (`native/moy_wasm/moy_wasi.c:1044` "THE APP ROWS").
- **Do:** with §2.2's `lua_ret` and `out_shape`, one generic Lua dispatcher packs arguments from `in` (as `DOOR_ARGS` does for Python) and shapes the answer from `lua_ret`. Keep `l_io_*` (a file object type) and `moy_app_lua_json_*` by hand.
- **Lines:** about −700.
- **Risk:** medium. The Lua answer shapes are the app API. T3's Lua column must be green first.
- **Guard:** T3.
- **Depends on:** T3, T5, and §2.2's fields.
- **Board:** yes, one pass.

### T15. Dead and stale

- `tools/bench_unix_mp.py` (161 lines):
  - nothing references it;
  - its header tells you to build inside `firmware/lilygo_t_deck_plus_mainline/.build/micropython`, which `.claude/rules/rendering.md` forbids;
  - it benchmarks `spr_gate`, which `.claude/rules/shell.md` records as deleted.

  Delete it. `make unix-micropython` plus `tests/test_frame_alloc.py` is its replacement.
- `tests/test_semantic_traces.py:2357` docstring cites tests/test_ui_wasm.py, which does not exist. Point it at T3's test when T3 lands, or at what drives the ui imports today.
- `api_floor` (`runtime/host/moy_ui_api_binding.py:443`) is a host-only name that only `tests/test_shift_frames.py` uses. Either give the board face the name (it is a read of engine state) or move it to a test helper. T1's parity test will flag it.
- `tools/music_mockups.py` (1,688 lines) is referenced only by `docs/media/music_2026-11/README.md`. A one-off mockup generator for a shipped design: move it to `experiments/` with its README, or delete it once its PNGs are committed.
- **Risk:** low.
- **Guard:** a `tools/` reference ratchet. `tests/test_tools_smoke.py` already holds the board tools; extend it to fail a `tools/*.py` that nothing (Makefile, tests, skills, docs, other tools) names.
- **Board:** no.

### T16. Lua `ui` and the Python face pass the canvas differently, and the engine has two entry ABIs

- **Evidence:**
  - Python calls `moy_ui_api_<verb>(ctx, sink, …)` with the context, canvas and look on every call (`ui.py:350` `_T`).
  - Lua and wasm call `moy_ui_run_<verb>(target, …)` (`moy_ui_wasm.c:283-300` `OPEN`), which finds the run's one context from the canvas.

  This is deliberate (one context per Python app; one per run). It doubles the face surface, and the generator would emit both.
- **Do:** nothing structural. Make the generator's row emit both from one row, so they cannot diverge. Listed so a worker does not "unify" them.
- **Risk:** n/a.
- **Board:** no.

---

## 4. Suggested order: batches that land independently

1. **Batch A: groundwork, host only, no board.**
   - T11 (split `gen_os_table`);
   - T12 (spike tool out of the runtime path);
   - T15 (dead and stale);
   - T5 (one error table; decide FULL);
   - T9's offset test;
   - T13's interim constants test.
2. **Batch B: the net, host only.** T3, the every-row test, with `example` added to the existing rows. It goes red where the tiers already disagree (T5's FULL). Fix those findings in the same batch.
3. **Batch C: the generator, first modules.**
   - T1's emitters and `+tests/test_face_parity.py`;
   - migrate `moy_log`, `moy_lease`, `moy_alloc`, `moy_prof`, then the crypto, clipboard and theme app rows on all four tiers;
   - T10 (one registration loop) and T13 folded in;
   - T9 generation;
   - one board pass (T-Deck plus one P4).
4. **Batch D: the toolkit.**
   - T7 (ctx sub-structs, keep overflow told, run face to `moy_ui_run.c`);
   - then T2 (UI verb rows plus `over.json`);
   - one board pass: goldens, frame alloc, `p4_conformance` not needed.
5. **Batch E: the Lua tier.**
   - T4 (the Lua app run on the C `moybyte` table);
   - T14 (generic Lua dispatcher);
   - one board pass.
6. **Batch F: wasm.** T6 (one WASI step runner), with one P4 pass.
7. **Batch G: remaining modules** through T1, one per commit: `moy_store`/`moy_ufiles`, then `moy_evq` (non-callback rows), `moy_input`, `moy_net`, `moy_gfx`. Stop where a module is mostly object types.

T8 (theme JSON reader) can ride any host-only batch.

## 5. What NOT to touch, and why

- **The host on unix MicroPython** (§2.4): it would rewrite 433 test files and lose the in-process simulator. Use unix MicroPython as the oracle, not the host.
- **`firmware/*/build.sh`:** the remaining per-board lines are the patch ladder and identity. `docs/board_ports_2026-08.md` records the build as "solved" and declines full sdkconfig codegen. The two S3 sdkconfig fragments must not merge (`.claude/rules/shell.md`, the 2026-08-22 pass).
- **AOT on boards versus the interpreter on the host:** "how a host EXECUTES a module never enters the spec" (`native/moy_wasm/README.md`). T6 shares only the runner around execution.
- **`moy_wasm_web`'s subset `moy_wasm`:** the absences (services, AOT, key) are deliberate (`modmoy_wasm_web.c:21-25`). Declare them in the face table; do not fill them.
- **Python twins and oracles are not duplicates:** `tests/spine_twin.py`, `runtime/carts/moy_index.py` (the `MOY_INDEX_IMPL=py` oracle), `tests/app_twin.py`, and `test_moycore_glue`'s `FakeMoycore` built from the C's own names.
- **The Lua tier's 1-based answers versus Python's 0-based:** a per-tier convention. Encode it as `base` in the table; do not unify it.
- **The DENY-list Lua preludes for object-valued verbs** (`lua_ext.py` handles, layers, editor, grants): `.claude/rules/shell.md` makes that one module the place for them. T4 removes only the `moybyte` call and wake half.
- **The single-pass, previous-frame-stops UI model:** a measure pass costs the S3 a second verb walk, and §14's shift and floor machinery rests on it. `surface_model_v1.md` §8 is closed.
- **`modmoy_ui.c`'s zero-allocation hand paths** (`label_text`, the single dict-map walk): generate their schema, keep their shape. `test_frame_alloc` and `test_ui_opts_seq` pin them.
- **The committed-output-plus-drift-test pattern** (`gen_ui_tables`, `gen_os_table`): the generator in T1 and T2 follows it, so no build step generates and a reviewer sees every generated line.
- **Vendored trees** (WAMR, libmoy, Lua, dash, `p8_lua_port.py`, `sdk/libc`): fix upstream and re-vendor.
- **`tools/look_shots.py`, `focus_shots.py`, `shell_bench.py`, `input_gate.py`:** large but live, with tests and skills naming them.
