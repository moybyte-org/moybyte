# Refactorability audit: native C core (2026-11-11, tree 5465b571c)

Scope: our C under `native/` (moy_kernel, moy_store, moy_index, moy_input,
moy_tty, moy_glass, moy_gfx, moy_flush, moy_png, p4/, moy_play, moy_spine,
moy_media, moy_audio, moy_net, moy_ssh, moy_usbdisk, moy_elf, moy_wasm,
moy_wasm_web, moycore, moy_app, moy_sh). Vendored code, `native/moy_ui` and
the `modmoy_*.c` / ctypes faces are out of scope; where a finding crosses into
them it says so.

## 1. Health verdict

The core is in better shape than its size suggests. Each module states its
contract in its header. The call table is generated end to end (ELF rows, WASI
rows, app rows, Lua names), and one JSON scanner, one handle table and one
event queue already exist and are used. Duplication sits one level below the
modules, in the **platform layer every file rebuilds for itself**:

- 67 `__has_include("esp_heap_caps.h")` probes under about 40 private
  macro names;
- 21 private `now_ms`/`now_us` clocks;
- about 30 PSRAM-first allocators with *different* fallback policies;
- about 10 lazily-made mutexes, each in a three-platform copy;
- 238 hand-written `__attribute__((weak))` prototypes that the compiler never
  checks against their definitions.

Beside that platform layer:

- **Policy duplicates.** Two Lua-service timer mechanisms, the errno mapping
  in two tiers, two UTF-8 decoders with different validity rules, a
  byte-identical string builder in two store files.
- **Board data in two channels.** Generated `MOY_HW_*` defines, and
  hand-written `mpconfigboard.h` pins held equal to `board.toml` by tests.
- **A state-isolation gap.** The toolkit-instance guard stops at `native/moy_ui`,
  so the file-scope caches and wire flags in `moy_gfx` that every instance
  draws through are unguarded and unlocked.

None of this needs a redesign. Nearly all of it is mechanical consolidation
behind small headers, each with a scan-test guard, in batches that land
independently.

## 2. Findings, ranked by payoff

### N1. One platform header: board detection, clocks, mutexes
- **Evidence.**
  - **Board probes.** 67 `__has_include("esp_heap_caps.h")` probes define
    about 40 distinct names for "on a board":
    - `MOY_SYNC_RTOS` (`moy_kernel/moy_sync.h:22`);
    - `SVC_BOARD` (`moycore/moycore_svc.c:35`);
    - `PROC_BOARD`, `LEASE_BOARD`, `DEVBUS_BOARD`, `LOCK_RTOS`
      (`moy_store/moy_vol.c`), `MOY_OS_BOARD`;
    - `MEDIA_BOARD`, `MOY_AUD_BOARD`, `MOY_GLASS_BOARD`, `MOY_INPUT_BOARD`,
      `MOY_SPINE_BOARD`, `MOY_SH_ON_BOARD`, `ON_BOARD`, `USB_ON_BOARD`;
    - `WA_BOARD`, `MOY_WASI_BOARD`, `FETCH_ON_BOARD`, `LORA_ON_BOARD`,
      `MOY_LOG_ESP`;
    - plus 12 `*_ALLOC` and 4 `*_PSRAM` variants.

    `moy_kernel/moy_dev.c:1-40` and `moy_crash.c:7` give the reason, once
    each: `ESP_PLATFORM` is not defined for usermod sources.
  - **Clocks.** 21 private clock helpers, in three types (u32 ms, u64 ms,
    i64 us):
    - `moy_store/moy_vol.c:98,124,151` (three in one file);
    - `moy_kernel/moy_evq.c:755`, `moy_loop.c:228,232`, `moy_vmq.c:55`,
      `moy_screen_slot.c:50`, `moy_notes.c:50`, `moy_log.c:73`;
    - `moycore/moycore_svc.c:179`;
    - `moy_sh/moy_sh_face.c:13`, `moy_sh_screen.c:12`, `moy_sh_job.c:11`;
    - `moy_tty/moy_tty_ws.c:70`, `moy_tty_sh.c:314`;
    - `moy_net/moy_lora.c:320`;
    - `moy_input/moy_ble_task.c:150`, `modmoy_input.c:76`;
    - `moy_app/moy_app_wasm.c:22,31`.
  - **Mutexes.** About 10 lazily-made mutexes share one shape: a
    double-checked `portMUX` around `xSemaphoreCreate*Static`, then a
    `pthread_mutex_t` branch, then a no-thread branch. Copies:
    - `moy_spine/moy_settings.c:36-81` (recursive);
    - `moy_store/moy_vol.c:75-145`;
    - `moy_kernel/moy_lease.c`, `moy_lock.c`;
    - `moy_play/moy_play.c:77-107`;
    - `moy_media/moy_media.c`, `moy_net/moy_seal.c`, `moy_elf/moy_os.c:83-228`
      (its own "platform" section);
    - `moy_audio/moy_aud_out.c`.

    `moy_net/moy_net_port.c` is a third platform layer ("the clock, sleep and
    memory under them").
- **What to do.**
  - Add `+native/moy_kernel/moy_platform.h`, header-only and linked by every
    image already through the kernel:
    - `MOY_ON_BOARD`, `MOY_THREADS` and `MOY_BROWSER`, decided once;
    - `moy_now_us()` (int64) and `moy_now_ms()` (uint64);
    - `moy_mutex_t` with `MOY_MUTEX_INIT`, lock, unlock and trylock, and a
      recursive variant.
  - Fold `moy_sync.h`'s platform half into it.
  - Replace the per-module macros, clocks and mutex copies.
  - Keep `moy_sync.h`'s critical-section lock: a mutex is not a replacement
    for it.
- **Size.** About 600 lines removed. Each mutex copy is 30 to 45 lines over 3
  platforms; each clock is about 10 lines; each probe block is 3 to 8.
- **Risk: medium.** It touches every module's preprocessor path. A wrong probe
  compiles calloc into a board, which spends internal SRAM. The browser has no
  threads, so its branch must stay "no lock".
- **Guard.**
  - A scan test: `__has_include("esp_heap_caps.h")`, `esp_timer_get_time` /
    `clock_gettime(CLOCK_MONOTONIC` and `xSemaphoreCreate*` may appear only in
    `moy_platform.h` and an allowlist (DMA drivers, `p4/`), each entry with
    its reason.
  - `tests/test_moy_spine_twins.py:571` pins the literal
    `'#if __has_include("esp_heap_caps.h")\n#define MOY_SPINE_BOARD'`, so that
    test pins the duplication itself. Rewrite it to assert the platform
    header.
- **Depends on:** nothing. N2 builds on it.
- **Board:** yes. Build all five targets and the web runner, then run one
  board pass on the T-Deck and the P4 (heap report and PERF unchanged).

### N2. One kernel allocator, with the placement rule as code
- **Evidence.**
  - `moy_kernel/moy_kernel.h:15-20` states "KERNEL DATA IS PSRAM", and
    `:172-182` adds that small blocks of at most 1 KiB come from `moy_kpool`
    so a VM stop is not fragmented (#224).
  - About 30 private helpers carry that rule out, with different policies:

    | helper | PSRAM attempt | then |
    |---|---|---|
    | `moy_evq.c:86 mem()` | PSRAM | no fallback |
    | `moy_proc.c:18`, `moy_lease.c:38` | PSRAM | `calloc` |
    | `moy_log.c:56` | PSRAM | internal 8BIT |
    | `moy_aud.c:105` | kpool first, then PSRAM | internal |
    | `moy_elf/moy_os.c:85` | PSRAM\|8BIT | no fallback |
    | `moy_net/moy_seal.c:89` | PSRAM\|8BIT | `calloc` |

    The rest are `moy_kernel.c:1567 kernel_psram`, `moy_loop_board.c b_alloc`,
    `moycore_svc.c:197`, `moy_glass` `tab_alloc`/`px_alloc` (3 copies,
    including the host and fuzz ones), `moy_input.c tab_alloc` (2),
    `moy_spine` `spine_alloc` (2), `moy_media.c block_alloc`,
    `moy_wasm/moy_wasi.c w_alloc`, `moy_ssh.c raw_alloc` and `moy_sh_store.c`.
  - `moy_sync.h:113 moy_sync_table` is yet another PSRAM-then-calloc helper.
  - 7 files redeclare `moy_kpool_alloc` weak.
  - **Latent issue.** The kernel's own small tables are allocated while a VM
    is up, in PSRAM directly, not from kpool: evq queue rows, proc rows, lease
    rows, the log, notes and status (through `moy_sync_table`). The
    fragmentation motive in `moy_kernel.h` covers them too.
  - **Doc contradiction.** `moy_kernel.h:19` says
    `tests/test_moy_spine_twins.py` "asserts the placement". It asserts it
    only for the five spine files (`:553-578`).
- **What to do.** Add `moy_kmem.h` with
  `moy_kalloc(n, MOY_KMEM_SMALL|MOY_KMEM_PSRAM|MOY_KMEM_INTERNAL_OK|MOY_KMEM_DMA)`
  and `moy_kfree(p)`, implementing kpool, then PSRAM, then the fallback the
  flags allow. Every helper above becomes one call. Each call site's current
  policy is kept explicitly in its flags; the choices are made visible, not
  changed silently.
- **Size.** About 350 lines removed, with 30 helpers gone.
- **Risk: medium.** Placement drives the S3's internal-SRAM budget. Moving the
  kernel tables into kpool changes kpool's high water
  (`moy_kpool_stats`, 64 KiB). That move is a separate, measured step.
- **Guard.** Generalise `test_kernel_data_is_allocated_from_psram` to all of
  `native/` except vendored code: any `heap_caps_*alloc` / `calloc` /
  `malloc` outside `moy_kmem.c` must sit in an allowlist with its reason
  (ISR rings, DMA, the Lua allocator's SRAM-first pool, which is a settled
  decision).
- **Depends on:** N1 (the platform macro).
- **Board:** yes. Read the T-Deck's and P4's heap lines (`HEAPS`, kpool
  stats) before and after, and run a VM-stop cart on an S3.

### N3. `moy_gfx` state is outside the instance guard: shared caches and three wire-order flags
- **Evidence.**
  - `tests/test_ui_instance.py:1-20,150` promises that a toolkit instance on
    another task "never writes the bytes the shell's frames write". It scans
    only `native/moy_ui`.
  - Every instance draws through `moy_gfx` (`moy_ui` calls
    `moy_shape_rrect`/`shadow`/`wire` and `moy_text_*`, about 80 call sites),
    and `moy_gfx` keeps unlocked file-scope state:
    - **`moy_shape.c`:** `s_cov`/`s_cov_r`/`s_cov_use`/`s_tick` (an LRU
      corner cache, `:171-215`), `s_mask[3]` (shadow masks, `:667`), `s_st`
      and `s_swapped`;
    - **`moy_path.c`:** `s_edges`/`s_acc`/`s_lent` (scratch, `:48-57`) and
      `s_swapped`;
    - **`moy_text.c`:** `s_print` (the print font), `s_wire_swapped`, `s_lanes`
      and `s_fonts` (`:57-60`).
  - Wire byte order is three globals with three setters:
    - `moy_shape_wire_swapped` (`moy_shape.c:30`);
    - `moy_path_wire_swapped` (`moy_path.c:33`);
    - `moy_text_wire` (`moy_text.c:304`).

    Callers set different subsets: `moy_scr.c:44` sets path only;
    `moy_sh/moy_sh_floor.c:76-77` sets text and shape but not path;
    `modmoy_gfx.c:1063-1064` sets shape and path. Meanwhile the kernels
    already take `swap` per call (`mg_ink(..., swap)`,
    `moy_gfx_kernels.h:476`).
  - `can()` is copied in `moy_path.c` and `moy_shape.c`; `isqrt64` in
    `moy_backdrop.c` and `moy_scr.c`.
- **What to do.**
  1. Pass the wire order with the target, as the kernels do: a field of the
     raster target or the `moy_scr_t`. Delete the three globals and setters.
  2. Move the shape and path scratch and caches into a per-instance struct,
     reached from the instance as the toolkit's rings already are. Where that
     is too invasive, lock them and say so in the header.
  3. Extend the SHARED scan in `test_ui_instance.py` (its `_state()` already
     parses any C file) to `moy_gfx/{moy_shape,moy_path,moy_text,moy_backdrop,moy_scr}.c`.
- **Size.** About 60 lines removed. The real gain is closing a race the
  existing guard claims is closed.
- **Risk: medium.** Pixel goldens cover the output. A wrong wire order shows
  as swapped colours on the S3s only.
- **Guard.**
  - The extended SHARED scan.
  - A host test that draws an rrect and a shadow from two threads with two
    instances, compared against one-thread output (it runs under
    `tools/asan_pytest.sh`, which will also show a TSan-class corruption as a
    mismatch).
  - A golden that draws path, shape and text in one frame on a swapped
    target.
- **Depends on:** coordinate with the `moy_ui` auditor (the instance struct
  is theirs).
- **Board:** yes, one S3, which has the swapped wire: draw goldens on glass,
  plus a compiled app with a dialog over the shell.
- **Unconfirmed.** I did not prove a compiled program draws rrects on its own
  task concurrently with the VM's frame. The worker's first step is to read
  `moy_ui_wasm.c`'s instance path and confirm.

### N4. Weak prototypes are re-typed in each consumer, unchecked
- **Evidence.**
  - 238 `... __attribute__((weak));` declarations in `.c` files in scope.
  - Repeated names include `moy_loop_job` (7×: `moy_evq.c:25`,
    `moy_vmq.c:53`, `moy_notes.c:24`, `moy_media.c:20`,
    `moy_sh_board.c:42`, `modmoy_elf.c:481`, `modmoy_wasm.c:1907`),
    `moy_kpool_alloc`/`free` (7× each), `moy_play_front_live` (5×),
    `moy_share_take`/`give`, `moy_log_write`, `moy_lock_take`/`give`,
    `moy_lease_lock`/`unlock` and `moy_input_kernel` (3× each).
  - Some copies use a different spelling of the type: `moy_evq.c:25` writes
    `bool (*fn)(void *)` where `moy_loop.h:259-260` has `moy_loop_job_fn`.
  - A header signature change leaves every weak copy stale. That is undefined
    behaviour at the call, with no diagnostic.
- **What to do.**
  - In each consumer: `#include` the owning header, then
    `#pragma weak moy_loop_job`. The compiler then checks the call against
    the real prototype.
  - Where the header is not on an image's include path (an image denying the
    module), keep the declaration but generate it.
  - Do NOT put `weak` on the declaration inside the owning header. In the
    defining TU it would make the definition itself weak.
- **Size.** About 200 lines removed.
- **Risk: low.** It is mechanical. The build is the test.
- **Guard.** A scan test: no `__attribute__((weak));` declaration in a `.c`
  except an allowlist (vendored code, `moy_ui`). Every `#pragma weak X` must
  follow an include of the header that declares X.
- **Depends on:** nothing.
- **Board:** a build of all five targets plus web; no flash needed.

### N5. Lua services keep a second timer scheduler beside `moy_evq`'s
- **Evidence.**
  - `moycore/moycore_svc.c:352-381` is `every`/`after`: a private 16-slot
    `svc_timer_t` table (`moycore_svc.h:143`), u32-ms due times, polled in
    `run()` (`:1127-1145`) and folded into the wait (`:1166-1175`).
  - The same file's `moybyte.at` already uses the kernel's alarms
    (`l_at`/`l_unalarm`, `:716`). It reaches them through a 25-line Lua
    prelude, `ALARMS` (`:688-712`), that maps negative ids onto a message
    wake.
  - `moy_evq.c:765-947` has per-process periodic timers with the same
    "a missed tick is not owed" rule, posted as `MOY_EVQ_TIMER` events.
  - So one service runs two timer mechanisms with two id spaces, and the
    tiers' third scheduler (Python `Wakes`, which the brief already measured)
    sits beside them.
- **What to do.**
  - Back `every`/`after` with `moy_evq_timer(s->pid, ms, every, &id)`.
  - Dispatch `MOY_EVQ_TIMER`/`MOY_EVQ_AT` events by id to the Lua ref in
    `take_mail`.
  - Delete `svc_timer_t`, the poll, the wait fold and the `ALARMS` prelude.
- **Size.** About 150 lines removed.
- **Risk: medium.** `moy_evq` timers fire from the kernel frame's job
  (`timers_job` via `moy_loop_job`), so service timers become
  frame-quantised (16 to 33 ms) instead of exact to the ms on the service's
  own task. The fix may need `moy_evq_timers_run` called from the waiting
  task, or the wait to compute the next deadline (`moy_evq_timers_run`
  already returns it).
- **Guard.** `tests/test_lua_services.py`: a service's `every` and `at` share
  one id space, and a service holds no timer table. Add a scan: no
  `due`/`every` fields in `moycore_svc.c`.
- **Depends on:** the brief's three-scheduler finding. Land it after the
  owner picks which scheduler is "the one", so the Python `Wakes` move and
  this one agree.
- **Board:** yes. An S3 with a Lua service through a VM stop.

### N6. The errno mapping and the send argument rule are written twice (ELF and WASI)
- **Evidence.**
  - `role_errno` maps `MOY_APP_*` to POSIX errno at `moy_elf/moy_os.c:944`
    and to WASI errno at `moy_wasm/moy_wasi.c:921`: the same ten cases.
  - `evq_errno` (`moy_os.c:2115`) and `os_errno` (`moy_wasi.c:757`) are the
    same mapping again.
  - `moy_os_moy_send` (`moy_os.c:2125-2165`) and `w_os_send`
    (`moy_wasi.c:762-783`) each re-implement the rule for a send's arguments
    (trim whitespace, require `[`, default `[]`, copy and bound the names),
    and the FULL→EAGAIN special case.
- **What to do.**
  - Add an `errno` column per `MOY_APP_*`/`MOY_EVQ_*` code to
    `native/os_calls.json`. Have `tools/gen_os_table.py` emit both
    numberings (it already writes both tiers' row headers).
  - Move the argument rule into the kernel: `moy_evq_send_text(from, app, an,
    name, nn, args, n)` returning a `MOY_EVQ_*` code. The tiers then only
    translate.
- **Size.** About 90 lines removed.
- **Risk: low.** `tests/test_os_table.py` regenerates and diffs.
- **Guard.** The generator. Add a scan: no `case MOY_APP_` outside generated
  files.
- **Depends on:** nothing.
- **Board:** no for the host. The P4 ELF suite rides the next board pass.

### N7. Board pins reach C through two channels
- **Evidence.**
  - `tools/board_config.py:414 hw_defines` generates `MOY_HW_<ROW>_<PIN>`
    from `board.toml [hardware]`. C reads it for LoRa, mic, SDMMC and the
    SPI2 radio CS.
  - The rest of the pins are hand-written in `mpconfigboard.h`: 30 distinct
    `MOY_BUS_I2C_*`, `MOY_AUDIO_I2S_*`, `MOY_INPUT_TOUCH_*` and
    `MOY_KERNEL_PANEL_*` names, about 155 `#define MOY_*` across the five
    boards. Example: the T-Deck has `MOY_BUS_I2C_SDA`
    (`mpconfigboard.h:164`, through `MICROPY_HW_I2C0_SDA`) while its
    `board.toml:346-348` `i2c0` os row says `sda = 18`.
  - The Zero's `/gpio` allowlist `MOY_NET_GPIO_PINS`
    (`MOYBYTE_ZERO/mpconfigboard.h:145`) is a hand copy of its lease row,
    which `tests/test_board_toml.py:1099` holds equal. Meanwhile
    `board_config` already emits that same row as `MOY_LEASE_ROWS`
    (`:945`).
- **What to do.**
  - Have the drivers read `MOY_HW_I2C0_SDA`, `MOY_HW_I2S0_BCK` and the rest,
    adding the missing `os` rows to each `board.toml`.
  - Derive `/gpio`'s allowlist from `MOY_LEASE_ROWS` (or emit
    `MOY_NET_GPIO_PINS` from `board_config`).
  - Delete the hand defines and the equality test.
- **Size.** About 100 defines removed, and one equality test becomes a
  generation.
- **Risk: medium.** A wrong pin means a dead peripheral on one board.
- **Guard.** `test_board_toml`: no `mpconfigboard.h` defines a `MOY_*` that
  names a GPIO number. The pin-shaped name list comes from `hw_defines`.
- **Depends on:** the board-glue auditor (`board_config.py` is theirs).
- **Board:** yes. All five build, boot and touch/key/audio smoke.

### N8. Small codecs copied across modules
- **Evidence.**
  - **String builder.** `sb_t` with `sb_put`/`sb_str`/`sb_canon`/`sb_free`
    is byte-identical in `moy_store/moy_seed.c:150-205` and
    `moy_journal.c:278-345`. About 10 more growable sinks exist: `sink_t` in
    `moy_ufiles.c:1486`, `moy_sh_fmt.c:21`, `moy_sync.c:30`, `moy_json.c:664`
    and `moy_settings.c:350`; `buf_t` in `moy_gpio.c:113` and
    `moy_app_lua.c:70`; `out_t` in `moy_webhost.c:191` and `moy_perf.c:126`.
  - **Base64.** Three decoders: `moy_seed.c:284-305`, `moy_img.c:32-296` and
    `moy_net/moy_sync_apply.c:369`. Two encoders: `moy_webhost.c:869` and
    `moy_webconsole.c:162`.
  - **Hex.** Two decoders: `moy_ota.c:275 hex_to`, beside the shared
    `moy_seal_unhex`.
  - **UTF-8.** About 11 validators, decoders and encoders:
    - `moy_net/moy_http.c:189 moy_utf8_valid` (a public helper living in the
      HTTP file);
    - `moy_store/moy_cat.c:196`, `moy_media.c:310,340`,
      `moy_kernel/moy_devch.c:470`, `moy_sh_face.c:96`, `moy_webhost.c:723`;
    - `moy_gfx/moy_text.c:374` and `moy_path.c:343`. These two disagree:
      `moy_text` refuses C0/C1 and F5+, while `moy_path` accepts overlong
      leads and maps controls to `?`;
    - `moy_term.c:583` and `moy_json.c:48`.
  - **HTTP heads.** The request parser is `moy_net/moy_http.c:72`, but the
    response parser `moy_http_resp_parse` lives in `moy_ota.c:84`, declared
    in `moy_ota.h:52`. `moy_webhost.c:1062-1072` builds a head by hand beside
    `moy_http_head`, and `:1677` re-scans for CRLFCRLF itself.
- **What to do.**
  - Add `moy_spine/moy_sb.{c,h}`: a growable builder with a failed flag and
    an allocator parameter, following `moy_htab_mem_t`.
  - Add `moy_spine/moy_codec.{c,h}` with b64, b64url, hex and UTF-8
    (`valid`, `next`, `put`, `whole_prefix`), each with one validity rule.
  - Move `moy_http_resp_parse` to `moy_http.c`.
  - Pick the text rule for `moy_path`, the strict one, unless a golden shows
    why not.
- **Size.** About 350 lines removed.
- **Risk: low.** `fuzz_spine.c`, `fuzz_fs.c` and `fuzz_net.c` already feed
  these parsers, so extend them to the codec.
- **Guard.** A scan test: no `static ... (b64|base64|utf8|unhex)` and no
  `} sb_t;` outside `moy_spine`. Add a fuzz target for `moy_codec`.
- **Depends on:** nothing.
- **Board:** no.

### N9. C layouts and enums hand-mirrored on the host
- **Evidence.**
  - `moycore/moycore_run.h:25-47` defines the `SNAP_*` enum.
  - It is copied by hand twice: `runtime/host/lua_binding.py:268-273` and
    `runtime/host/wasm_binding.py:485-491`. On a board, `modmoycore.c:1333`
    exports it.
  - Across `runtime/host/*.py` there are about 141 constant lines mirroring
    C (`moy_input.py` 31, `moy_play.py` 20, `wasm_binding.py` 11 ...) and
    103 hand-written `ctypes.Structure` classes. 5 tests check a size.
- **What to do (C side).** Add a generator (or a mode of
  `gen_os_table.py`) that parses the enums and `#define`s a header marks
  (`// HOST-EXPORT`) into one committed `+runtime/host/native_consts.py`. For
  structs, have each module export `moy_<x>_layout()` (size plus offsets),
  which the ctypes face asserts at load.
- **Size.** About 250 lines removed. More importantly, drift becomes
  impossible.
- **Risk: low.**
- **Guard.** The generated-file diff test (as `test_os_table.py` does). The
  ctypes faces assert the layout at load.
- **Depends on:** the bindings auditor owns the Python half; agree the
  mechanism with them.
- **Board:** no.

### N10. Task placement is scattered literals
- **Evidence.** 21 `xTaskCreate*` calls with literal stack, priority and
  core:
  - `moy_mic.c:217` (3072/6/0);
  - `moy_usbdisk.c:431`, `moy_fetch.c:279` (6144/5/0);
  - `moy_lora.c:423` (4096/10/0), `moy_midi.c:509` (2560/12/0);
  - `moy_tty_sys.c:49`, `moy_webauth.c:846`, `p4/moy_usb/moy_usb_host.c:352,361`,
    `p4/moy_ppa/modmoy_ppa.c:678`, `moy_media.c:2302`, ...

  Per-chip core choices are buried in `#if CONFIG_IDF_TARGET_ESP32P4`
  (`moycore_svc.c:48-57`, `modmoy_wasm.c:1773-1777`).
- **What to do.**
  - Add one table `moy_kernel/moy_tasks.h`: name, stack, priority, core per
    chip, PSRAM stack yes/no, with a `moy_task_spawn(MOY_TASK_MIC, fn, arg,
    &h)`.
  - Optionally have the board override it from `board.toml`.
  - The table becomes the one place a reviewer sees the system's task layout,
    for example "input above VM", "radios on core 0".
- **Size.** About 40 lines removed. The payoff is legibility and one guard.
- **Risk: low to medium.** Keep every value identical in the first commit.
- **Guard.** A scan test: no `xTaskCreate` outside `moy_tasks.c`, `p4/`
  vendor code and `moy_kernel.c`'s pinned `mp_task` copy. A table test can
  assert ordering invariants.
- **Depends on:** N1.
- **Board:** yes. All boards boot, then PERF.

### N11. Two oversized files doing several jobs
- **Evidence.**
  - **`moy_kernel/moy_kernel.c`** (2129 lines, 34 file-scope statics) has
    five jobs: the crash intake (`:208`), the binding reach (`:465`), the
    watchdog (`:518`), the recovery floor (`:576-1365`, about 790 lines),
    and the pinned `mp_task` copy (`:1365`).
  - **`moy_elf/moy_os.c`** (4108 lines) holds its own platform layer
    (`:83`, see N1), files and streams (`:1045-1616`), threads
    (`:1734-1991`), sockets (`:2319-3157`, about 840 lines) and processes
    (`:3157-3895`).
- **What to do.**
  - Move the floor to `moy_recovery_floor.c` (beside `moy_recovery.c`'s
    raster) and the watchdog to `moy_wd.c`. Leave the `mp_task` copy where
    `mp_task_calls.txt` pins it.
  - Split `moy_os.c` into `moy_os_files.c`, `moy_os_net.c` and
    `moy_os_proc.c` over a private `moy_os_int.h`.
- **Size.** 0 lines removed. Both files become reviewable in one sitting.
- **Risk: low.** It is a pure move. Check `tools/mp_task_calls.py` still
  finds its range.
- **Guard.** The existing ones (`test_mp_task_calls.py`, the ELF suites).
- **Depends on:** N1 first, so the split does not carry the platform block
  along.
- **Board:** yes. Recovery floor on glass (`board.py X reboot` into the
  floor) and the P4 ELF suite.

### N12. Result codes: 26 module enums, with mixed signs
- **Evidence.**
  - There are 26 `MOY_*_OK` enums, for example `moy_evq.h:100`,
    `moy_vmq.h:44`, `moy_app.h:46`, `moy_lease.h:97`, `moy_net.h:26`.
  - `MOY_HTTP_OK = 1` is the odd one out (`moy_net.h:26`).
  - Within `moy_evq` the sign varies: `moy_evq_listen` returns `MOY_EVQ_OK` or
    a positive code, while `moy_evq_send` returns `-MOY_EVQ_FULL`. Every
    caller must know which (`moy_wasi.c:782`, `moy_os.c:2161`).
- **What to do.**
  - Not a big-bang rename. State one convention in `moy_kernel.h`: 0 is OK,
    a negative value is a code, and the code space is the module's.
  - Fix `MOY_HTTP_OK` and the evq sign split.
  - Have the tier translators (N6) consume the convention.
- **Size.** Small.
- **Risk: medium.** Sign changes are easy to miss.
- **Guard.** A scan: every `enum { MOY_X_OK = 0` (not 1); every public
  function that returns a module code is listed in its header with its sign.
- **Depends on:** N6.
- **Board:** no.

### N13. Doc claims the code contradicts
- **Handle table.** `moy_spine/moy_htab.h:3-6` says it is "the one
  implementation of the discipline every handle in the kernel follows".
  `moy_kernel/moy_dev.c:51-66,210-245` keeps its own gen/slot handle table.
  Also, the htab KIND field is 4 bits and 14 of 15 kinds are taken
  (`moy_htab.h:39-53`), so moy_dev could not get one without widening it.
  Fix the doc. Widening is a separate decision.
- **Placement.** `moy_kernel.h:19` says "tests/test_moy_spine_twins.py
  asserts the placement". It asserts it for spine only (see N2).
- **GPIO allowlist.** `moy_net/moy_gpio.c:9-11` says the allowlist is
  `MOY_NET_GPIO_PINS` in `mpconfigboard.h`, while `board.toml` says the
  lease row is. That is two sources for one fact (N7).
- **Size.** Text only.
- **Risk: low.**
- **Guard.** None executable. Run `tools/check_docs.py`.

### N14. A second Lua sandbox for services
- **Evidence.**
  - `moycore/moycore_svc.c:638-670` (`open_names`) builds a service's
    globals by hand: base, coroutine, table, string and math, nil-ing
    `load`/`dofile`/`loadfile`, plus its own `print`, `os` and `moybyte`.
  - A cart run takes libmoy's sandbox (`moy_lua_open`,
    `moycore_lua.c:1264`), and an app run adds `moy_app_lua_open`
    (`moy_app_lua.c:1671`).
  - So two sandbox policies decide what Lua may reach.
- **What to do.**
  - Have `moycore` own one `moycore_lua_sandbox(L, flags)` used by both the
    run and the service. libmoy's `moy_lua_open` stays the cart layer above
    it.
  - Or document why a service's base library must differ, and pin the
    difference.
- **Size.** About 30 lines removed.
- **Risk: medium.** It changes the security surface. Review the banned list
  against libmoy's.
- **Guard.** `test_lua_services.py`: a service and a cart see the same
  dangerous-global set (`load`, `dofile`, `loadfile`, `io`, `require`,
  `debug`).
- **Depends on:** the brief's "Lua `moybyte` table built twice" finding;
  do them together.
- **Board:** no.

### N15. The C/Python twins, from the C side (pointer for the runtime auditor)
- **Evidence.** Rule-for-rule twins held equal by tests:
  - `moy_store/moy_pack.c` ↔ `runtime/carts/moy_pack.py` (`moy_pack.h:14-15`);
  - `moy_files.c` ↔ `cart_files.py` (`moy_files.h:3-4`);
  - `moy_img.c` ↔ `moyimg.py` (`moy_img.h:3-4`);
  - `moy_settings.c` ↔ `runtime/moy_spine.py` Settings;
  - the crash ledger ↔ `test_ledger_twin`;
  - `moy_store` ↔ `moy_fs.py` (`runtime/carts/moy_fs.py:506-516`: "CPython
    ... has no such module and keeps the twin").

  The host already loads kernel C through ctypes for `evq`, `loop`,
  `lease`, `log` and `ufiles` (`runtime/host/moy_evq.py:20-24`,
  `native_build.Part`).
- **What to do (C side).** Make `moy_store` (pack, files, img, fs) a
  `native_build.Part` like `evq`, so CPython runs the C. The Python twins
  then become thin faces and the twin tests become plain tests.
- **Size.** About 1300 Python lines removed on the runtime side. No C
  changes beyond host exports.
- **Risk: medium.** The simulator, tools and tests start depending on a
  built `.so` for the store. CI already builds the host library.
- **Guard.** A test: no `runtime/carts` module defines a body for a function
  the C store exports.
- **Depends on:** the runtime auditor owns the decision.
- **Board:** no.

## 3. Suggested order (each batch lands alone)

| batch | findings | needs | board |
|---|---|---|---|
| B1 hygiene | N4 (weak pragmas + scan), N6 (errno generated + send rule), N8 (moy_sb, moy_codec, http resp move), N13 (doc fixes) | host only | build all targets, no flash |
| B2 platform | N1 (moy_platform.h: probes, clocks, mutexes; rewrite the spine placement test) | B1 not required | T-Deck + P4 pass |
| B3 memory | N2 (moy_kmem + generalised placement guard). The kernel tables into kpool is a second, measured commit | B2 | T-Deck + P4 heap lines, one VM-stop cart on an S3 |
| B4 gfx state | N3 (wire order per target, per-instance scratch, SHARED scan extended to moy_gfx) | coordinate with moy_ui auditor | one S3 draw pass |
| B5 file splits | N11 | B2 (so the platform block is gone first) | floor on glass, P4 ELF suite |
| B6 board data | N7, N10 | board-glue auditor; N1 for N10 | all five boot |
| B7 timers | N5, N14 | the owner's call on "the one scheduler" | S3 Lua service through a VM stop |
| B8 host mirrors | N9, N15 | bindings and runtime auditors | none |
| B9 convention | N12 | N6 | none |

B1, B4 and B8 can run in parallel with B2. B3 must follow B2, B5 must
follow B2, and B7 waits on an owner decision.

## 4. Do NOT touch (deliberate, or settled)

**Vendored code**
- `moy_lua/lua`, `moycore/libmoy`, `moy_audio/libmoy`, `moy_wasm/wamr`,
  `moy_sh/dash`, `moy_net/mbedtls`, `monocypher`, `moy_ssh` dropbear,
  `moy_dsi/vendor*`.
- Fix these upstream or in the fork and re-vendor. N1 and N4 scans must
  exclude them.

**Two of something by design**
- **`moy_png`'s own inflater** (`moy_png.c:362`, its own adler) looks like a
  duplicate of uzlib, but it is deliberate. `moy_png.h` streams a row at a
  time in one caller-owned work buffer that holds offsets, so a decode
  resumes on a later call, and the host test lane builds it without
  MicroPython's uzlib.
- **The two `kern()` functions** in `moy_text.c:411` and `moy_path.c:367`
  read different table formats (bitmap font pairs, outline face index).
  Only their UTF-8 decoders should merge (N8).
- **`moy_sync.h`'s lock and `moy_lock.h`'s mutex are two locks on purpose**:
  a critical section for a few loads, and a mutex for 4 KiB copies
  (`moy_lock.h:11-13`). N1 adds a portable mutex type; it does not merge
  them.
- **`moy_wasm_web` vs `moy_wasm`**: the browser engine is a sibling session
  over the page's WebAssembly, not WAMR (`moy_wasm_web/README.md`). The
  session surface is shared; the two implementations are not duplicates.
- **The WASI preview1 table in `moy_wasi.c:2183`** is hand-written because
  it is a standard ABI, not an `os_calls.json` row. The `moybyte.os` rows
  beside it are already generated.
- **`moy_prof`, `moycore_prof` and `moy_perf`** are three distinct meters
  (the `perf` skill): a PC sampler, LUAPROF, and the PERF line. They are not
  three profilers to merge.

**Pinned by other guards or by owner decisions**
- **`moy_kernel.c`'s `mp_task` copy** stays verbatim and in place
  (`mp_task_calls.txt`, "embed, not fork", owner 2026-10-05). N11 moves the
  sections around it, never the copy.
- **`moy_flush`** ("every clause was a race once") and the
  `moy_present_*` compositors: no consolidation without
  `tests/moy_flush_harness/` and on-glass runs.
- **The Lua allocator's SRAM-first pool** in `moycore_lua.c` is settled
  (measured ~2× faster than all-PSRAM on the S3). N2's guard must allowlist
  it, not fold it.
- **The C-kernel/Python-apps line, and §8 of `docs/surface_model_v1.md`**:
  nothing here moves an app into C or adds an invalidation mechanism.
- **`moy_dev`'s own handle table**: do not force it into `moy_htab` without
  first deciding to widen the 4-bit KIND (N13).
