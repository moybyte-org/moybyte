# Verification of the audit findings (2026-11-11)

Every finding in `native.md`, `shell.md`, `tiers.md` and `extra.md`, checked
against origin/dev 5465b571c by opening the cited lines and re-running the
counts. `docs/refactor_2026-11.md` plans on these results, not on the audits'
own text.

**Tags.** *verified*: the claim, its counts and the fix's feasibility hold.
*corrected*: the finding stands, with the evidence or the fix changed as
stated. *dropped*: the claim does not hold, or the work is already done.
Counts under "checked" are from this pass; where an audit's number differs by
a few and nothing rests on it, the finding stays *verified* and the number is
given.

Vendored trees (Lua, libmoy, WAMR, dash, mbedTLS, monocypher, Dropbear, the
DSI vendor drivers) are outside every count.

## Native C (N)

| id | tag | checked |
|---|---|---|
| N1 | corrected | The probe appears 62 times under `native/` (55 outside `native/moy_ui`), not 67, under 39 macro names. Private clocks: at least 30 `static` helpers (three `now_us` copies in `moy_vol.c` alone), more than the 21 listed. Lazily-made mutexes sit in 13 files. `tests/test_moy_spine_twins.py:572` pins the probe's literal text, so the test changes with the header. The fix is feasible: `moy_sync.h:22` already decides the platform once for the kernel. |
| N2 | verified | The seven sampled helpers have the policies the table gives (`moy_evq.c:86` PSRAM with no fallback, `moy_proc.c:18` PSRAM then `calloc`, `moy_log.c:56` PSRAM then internal, `moy_aud.c:105` kpool first, `moy_os.c:85`, `moy_seal.c:89`). 47 files call `heap_caps_*alloc`; seven redeclare `moy_kpool_alloc` weak. `moy_kernel.h:19` credits the placement test with more than it checks. |
| N3 | corrected | The state is there: `moy_shape.c:27-28,171-173,667`, `moy_path.c:31,48-53`, `moy_text.c:57-60`, three setters called in different subsets (`moy_scr.c:44`, `moy_sh_floor.c:76-77`, `modmoy_gfx.c:1063-1064`, `modmoy_ui.c:830`), and the instance scan reads `native/moy_ui` only. The race was not found. A program linked as an ELF carries its own copies of `moy_text.c` and `moy_shape.c` (`tools/elf_cart.py:66-78`), so its statics are its own. A compiled cart's session draws on its pthread while the VM's task is blocked in `sess_wait` (`modmoy_wasm.c:836`). A WASI command or service has no screen, and an import made from a cart's `par` lane is refused (`native/moy_wasm/README.md`, "A cart's `par` items" and its test list). What stands is the guard gap and the three wire-order globals. |
| N4 | corrected | 292 weak declarations in our `.c` files (252 outside `native/moy_ui` and the MicroPython faces), not 238. `moy_loop_job` is redeclared seven times, four of them spelling the callback type out instead of `moy_loop_job_fn` (`moy_loop.h:259`). No `#pragma weak` exists today, so the first conversion proves the form on every toolchain. |
| N5 | corrected | `timer_add` is at `moycore_svc.c:352`; the timer row type is declared in the `.c` (`:96`), not in the header. `l_at` (`:573`) and the `ALARMS` prelude (`:688`) are as described; `moy_evq_timer` and `moy_evq_timers_run` exist (`moy_evq.h:229,245`). |
| N6 | verified | `role_errno` at `moy_os.c:944` and `moy_wasi.c:921`; `evq_errno` `moy_os.c:2115` and `os_errno` `moy_wasi.c:757`; the two send bodies at `moy_os.c:2125` and `moy_wasi.c:762`. |
| N7 | verified | `hw_defines` at `tools/board_config.py:414`, `MOY_LEASE_ROWS` at `:945`. The five boards' `mpconfigboard.h` hold 155 `MOY_*` defines (P4 37, Guition S3 27, Guition P4 46, T-Deck 40, Zero 5). The Zero's `MOY_NET_GPIO_PINS` is at its `mpconfigboard.h:145`. |
| N8 | corrected | The two `sb_t` builders are near-identical, not byte-identical: the journal's adds `sb_quote` and names a local differently (`moy_seed.c:150-205`, `moy_journal.c:278-345`). Three base64 decoders and two encoders, `hex_to` beside `moy_seal_unhex`, eleven UTF-8 helpers and `moy_http_resp_parse` in `moy_ota.c:84` are as cited; `moy_ufiles.c:1821` holds a twelfth UTF-8 reader. |
| N9 | verified | `SNAP_*` at `moycore_run.h:25`, copied at `wasm_binding.py:485` and in `lua_binding.py`. `runtime/host` declares 104 ctypes structures. |
| N10 | verified | 21 `xTaskCreate*` calls in 19 files. |
| N11 | verified | `moy_kernel.c` 2,129 lines with section rules at 208, 465, 518, 576, 1365; `moy_os.c` 4,108 lines with the sections at the lines given. |
| N12 | verified | 27 `MOY_*_OK` enumerators; `MOY_HTTP_OK = 1` (`moy_net.h:26`) is the only one not 0 or an alias. `moy_evq.c` returns a code positive at 12 sites and negated at one. |
| N13 | verified | `moy_htab.h:3-6` makes the claim; `moy_dev.c` keeps its own gen and slot rows; 14 of the 15 kinds a 4-bit field allows are named (`moy_htab.h:39-53`). The same header still calls the deleted Python spine "the interface and the reference". |
| N14 | verified | `open_names` at `moycore_svc.c:638` opens five libraries and bans three names by hand; the cart run takes `moy_lua_open` (`moycore_lua.c:1264`). |
| N15 | corrected | Four twins live in `runtime/carts` (pack 299 lines, files 315, img 393, fs 537: 1,544 in all). The Settings pair named is gone: its Python half is the test oracle `tests/spine_twin.py`, which stays. The host already builds the C for two of the four (`FILES_PART`, `IMG_PART` in `runtime/host/moy_play.py:78-82`), which is what makes the fix feasible. |

## The Python console (S)

| id | tag | checked |
|---|---|---|
| S1 | corrected | The setter is called at 45 sites in 17 files: 31 as `ws._set_text_mode` from outside the console's own files, 14 from inside them (`console.py` 5, `console_saves.py`, `shell_servers.py`, and the host's `moy_input.py`). `nav.text_mode` is called at 8 sites, not 6 (Files 1, Music 3, Appearance 2, the story editor 2). The flag is written around the setter at four places, not three: `cart_api.py:450`, `moycore_glue.py:597`, `console_spine.py:479` and `console.py:2708`. The cancel ladder is at `console_text.py:80-125` as described, and 17 surfaces define `back()`. `host_api.py` is under `runtime/run`; `takes_escape` and `escape` are at `:457` and `:471`. The `Layer` hooks exist (`layers.py:60-66`) with three stray callers of other objects' `on_enter`/`on_leave` and none through the router. |
| S1 live bug | verified | Only Settings, the Terminal, Music and the Player define `cancel_field`; `app_cancel` is called from the Player alone (`player.py:1579`). A registered shell app that draws a field with no `cancel_field` (Files, Appearance, Storybook) falls to the end of the ladder and Mic becomes the console's key (`console_input.py:98-99`). Read from the code; not reproduced on a T-Deck in this pass. |
| S2 | corrected | 2,079 lines, 183 `ws` references, the six view flags in `_page_of` (`:1874`). The literal `"settings"` appears 62 times in 23 files outside the layer, 26 of them comparisons; the audit's 33 in 12 is an undercount. `"tasks"`: 25 literal sites. `wifi_pw` is a `TextEntry` used for its text (`:363`), `_wifi_seed` at `:362,865,938`. |
| S3 | verified | Nine `KeyEdge()` instances in eight files, plus `_ekey_prev`, the two `seed_key`s, `seed_cart_key`, `_wifi_seed`, `arm_prompt` and the driver's adjacent-frame rule (`runtime/run/host_api.py:526-532`). |
| S4 | verified | The Code tab calls `ws.history` (`code_layer.py:668-671`); Map, Music, Scene, Blocks (`block_editor_ui.py:1123`) and Paint (`paint_layer.py:491-494`) walk their own stacks. |
| S5 | verified | The window X's per-kind ladder is at `wm_windowed.py:2108-2125`; `open_app` commits two surfaces by name (`console_spine.py:473-474`). |
| S6 | verified | 30 calls in 15 files. |
| S7 | verified | 98 distinct `ws._name` reads in 61 files outside `console*.py`; `ws._dirty = True` at 214 sites against 63 `mark_dirty()` calls; `player._is_tool` read from 6 sites outside the Player, `wm._wins` from 12 outside `runtime/wm`. |
| S8 | verified | 806 `except ImportError` in 222 files; 888 `from runtime` import lines; 26 alias lines in `runtime/host/host_app.py`. |
| S9 | verified | The pinned set holds 15 names in four groups (`tests/test_console_facade.py:301-307`). |
| S10 | verified | `console_notices.py` 2,098 lines with the five classes at the lines given; `console.py` 4,132. `Workstation._icon_btn` (`console.py:3907`) has no caller in `runtime`, `device`, `tests`, `tools` or the web runner. |
| S11 | verified | The per-kind ladder calls six `nav` verbs (`files_app.py:504-580`); "Open with" goes through the intents (`:577`); My Art calls `nav.open_image` twice. |
| S12 | verified | `about_ui.py` keeps `_top`, `_rows`, `_key` and its own `wrap_lines` (`:86,133-135,180-182`). |
| S13 | verified | The no-keyboard check is in Settings (6), the home search (3), the launcher (2) and the console (3); no app field has it. |
| S14 | corrected | `story_doc.py:315-345` and `theme_editor.py:114-129` keep their own depth-32 lists. The shared class is `History` with `OpCodec` (`op_history.py:43,179`); there is no class named `OpHistory`. |
| S15 | verified | The trackball spend is in `runtime/run/host_api.py:519-525` and `device/desktop_spine.py:111-117`. `moybyte.py:1172` repeats `devices.NAMES`; `PERM` maps each name to itself. `ENDS` is ordered by `insert(0)` in two modules and `append` in the rest. `take_pcm`, `perf_breakdown_raw` and `grid_content_w` have no caller in this repo beyond one test; moy-spec and the carts repo were not searched in this pass. `Scheduler` runs from `scheduler.py:410` in a 1,938-line file. |
| S16 | verified | `Player` is at `player.py:273`: 82 methods, a 2,813-line file. |

## The toolkit, the tiers, the bindings, the generators (T)

| id | tag | checked |
|---|---|---|
| T1 | verified | 42 `modmoy_*.c` under `native/*/` (47 with `native/p4`), 31,329 lines, about 1,500 `mp_obj_t` functions. `runtime/host` holds 19,032 lines of Python and 1,910 of C; 24 files carry a `_SIGS` loader. |
| T2 | verified | 46 `ui.*` rows; 113 `api_*` wrappers on the host; 46 `moy_ui_wasm_*` and 48 `moycore_ui_*` functions; the override codecs at `ui.py:197`, `modmoy_ui.c:2378-2632`, `moycore_ui.h:137` and `moy_ui_api.h:286`. |
| T3 | verified | No test drives the Lua, wasm or WASI projection of every row. `tests/test_semantic_traces.py:2362` and `tests/test_roles.py:51` both cite a `test_ui_wasm` file that is not in the tree. Nothing under `tests/` names `LUA_RUN_CALLS`. |
| T4 | verified | `PRELUDE_MOYBYTE` at `lua_ext.py:724`, `lua_lit` `:909`, `_RunWakes` `:946` building `__moybyte_fire(...)` source at `:960`. |
| T5 | verified | FULL is "the grant table is full" on both Python faces (`modmoy_app.c:86`, `moy_app_binding.py:229`) and "the answer is too long" in Lua (`moy_app_lua.c:243`); only Lua knows `NEEDS_VM`. |
| T6 | corrected | The stepping protocol is already one body: `moy_wasi_step` and `moy_wasi_stop` in `moy_wasi.c` take a `moy_wasi_runner_t` of three callbacks (`moy_wasi.h:162-181`). What exists twice is the hop underneath (FreeRTOS semaphores in `modmoy_wasm.c:1571-1603`, a pthread condition in `moyhost_wasm.c:758-797`) and the job's start, end and service lifecycle. The saving is smaller than the audit's figure and the board half is the session every compiled cart shares. |
| T7 | verified | 176 field declarations in `moy_ui_ctx_t` (`moy_ui_api.h:425-751`); `mua_keep` returns NULL silently at 16 kept ids (`moy_ui_api.c:534-554`). |
| T8 | corrected | The reader is `moy_theme.c:21-208` with depth 8, and `moy_theme.h:2` calls its input untrusted. It is not unfuzzed: `tests/test_theme_loader.py:650` mutates the seed themes into a seeded corpus that `tests/test_theme_store.py:138` also runs. It is outside the native fuzz targets (`fuzz_spine.c`, `fuzz_fs.c`), which is the gap. |
| T9 | corrected | 104 ctypes structures in `runtime/host`, not about 40. One has a size check. |
| T10 | verified | Six hand registrations in `runtime/__init__.py:36-130`, one `setdefault` in `net_binding.py:246`, the rest found by file name. |
| T11 | corrected | 2,006 lines and 96 top-level functions, not 120. |
| T12 | verified | Three runtime modules import `tools/moy_index_spike.py` (1,178 lines); one test does too. |
| T13 | verified | `runtime/host/moy_log.py:16-18` against `moy_log.h:37`. |
| T14 | verified | 55 `l_*` row functions in the 1,775-line `moy_app_lua.c`; the WASI tier's generic rows start at `moy_wasi.c:1044`. |
| T15 | corrected | `tools/bench_unix_mp.py` is named once outside itself, by a comment at `modmoy_gfx.c:67`, which goes with it. The other three items are as described. |
| T16 | verified | Nothing to change; recorded so the two entry forms are not merged. |
| tiers §2.4 | verified | 540 test files, 427 importing `runtime`, 172 using `monkeypatch`, 67 driving unix MicroPython. |

## God objects, maintenance, live bugs (G, X, L) and the brief's measured items (B)

| id | tag | checked |
|---|---|---|
| G1 | verified | 338 methods: `Workstation` 163 plus nine mixins (Notices 41, HomeVerbs 29, SpineVerbs 26, TextVerbs 20, InputVerbs 17, SaveVerbs 14, PerfMeters 12, SettingsToggles 11, CanvasBinding 5); 9,490 lines in ten files. |
| G2 | verified | See T7 and T8; `moy_ui_api.c` 4,480 lines, `moy_ui_parts.c` 3,514, `moy_theme.c` 4,694. |
| G3 | verified | See S16. |
| G4 | verified | `moy_app.c` is 2,680 lines; its door is a hand `switch (row)` from `:2304` with 71 cases, beside a hand `no_args` list (`:1783`). The rows' ids, roles and permission map are generated (`moy_app_rows.h`); the bodies are not. |
| G5 | verified | See N11. |
| G6 | verified | `WindowedWM` 106 methods, `BarLayer` 92, `SettingsLayer` 88, `Appearance` 92, `GetCartsAppLayer` 103, `EditorDesk` 100, each within a few lines of the size given. |
| G7 | verified | `DeviceCanvas`: 96 methods in a 3,037-line file. |
| X1 | verified | The store's one door for a format change is `prune_retired` (`runtime/carts/moy_seed.py:590`), gated on a generation sidecar. No store carries a format version a reader checks. |
| X2 | verified | 22 `moybyte_patch_*` functions over MicroPython and ESP-IDF v5.5.1 (`tools/esp32_build_lib.sh`). |
| X3 | verified | Seven `*_vendor.json` pins and a `make vendor-*` target each; nothing compares a pin with upstream. |
| X4 | corrected | 44 configurations, 1,013 hashes. The "after" picture already exists on demand: `tools/golden_png.py` renders a configuration's surfaces as the hash saw them. Missing are the "before" picture, the diff, and a failure message that produces them. |
| X5 | verified | No document under `docs/` or `native/moy_kernel` states which task may touch what. |
| X6 | corrected | Tracked Markdown outside this appendix is about 61k lines; `docs/` outside `docs/history` holds 18.6k of them. |
| X7 | verified | No manifest field names a minimum firmware (`runtime`, `native`, `docs/moy_cart_api.md`). |
| X8 | not checked | Headroom is what a build prints; this pass built nothing. |
| L1 | verified | S1's live bug above. |
| L2 | corrected | T8 above: a real gap, smaller than "unfuzzed". |
| L3 | dropped | As a live bug. N3 above: every path that could draw through `moy_gfx` beside the VM's frame was read, and none runs at the same time. N3 stays as a guard gap. |
| B1 | verified | Three timer schedulers: `Wakes` (`runtime/system/moybyte.py:341`, its `every`/`after`/`alarm`/`due`), `moy_evq_timer` (`moy_evq.c:812`), the Lua service's (`moycore_svc.c:352`). Python's messages already ride the kernel's queue (`moybyte.py:418,458,469`); only its timers are its own. |
| B2 | verified | A Python process posts a notification straight into the list (`moybyte.py:848`); every other tier posts through the kernel's queue (`moy_app.c:2008`), which the list drains (`runtime/system/notifications.py:40-43,163`). One list and one id space, two ways in. |
| B3 | verified | A Python process holds a device as an object (`runtime/system/devices.py`), every other tier as a handle (`runtime/system/dev_handles.py`). `docs/os_2026-11/hardware.md` §4 describes the pair as the design. |

## The five issues folded in (owner, 2026-11-11)

Checked the same way, against the same commit. The issue texts are the
mirror's (`make sync-issues`), read on 2026-11-11.

| id | issue | tag | checked |
|---|---|---|---|
| K1 | #245, the web canvas | corrected | **Still worth doing.** `WebCompositor` (`firmware/web_runner/web_canvas.py`) and `HostCompositor` (`runtime/host/host_canvas.py:141`) hold the same four methods, which alone would be twenty lines. The reason is drift: the host's has since gained `copy_last`, `shift_last` and `copy_last_into` (`:178-209`), which the WM probes for with `hasattr` (`runtime/wm/wm_windowed.py:1946,1979,1995,2067`, `wm_overlays.py:442`, `wm_parts.py:248`), and the web's has not. So the browser's desk skips the kept-frame paths the host's goldens run. The browser build has the two raster verbs the methods need (`modmoy_gfx.c:2438-2439`). `host_canvas.py` is CPython-only, so the shared class needs a module both sides can load. |
| K2 | #245, the P4 PPA composite | corrected | **Still worth doing, at a narrower scope.** The issue lists a C API into the PPA as something the port needs. It exists: `moy_ppa_k_scale`, `_scale_nb`, `_snap`, `_snap_wait`, `_sync` (`native/p4/moy_ppa/modmoy_ppa.c:374,1681-1703`). The kernel's front already composites a game frame on both P4 compositors in C over them (`native/moy_glass/modmoy_glass.c:1060-1151`). So the choice is written twice and differently: `_composite_paint` (`device/p4_canvas.py:364-432`) picks the PPA's banded nearest for crisp, its bilinear scale, or the CPU kernel, and demotes on an error; the front picks bilinear or the CPU kernel, with no PPA crisp path and no demotion. What is worth doing is one C body for that choice. The rest of the issue's list (`present_frame`, `_patch`, `_blit_patches`, `_paint_frame`, `blit_strip_async`) has no second copy and no host test, and is a perf port. |
| I1 | #242, the conformance matrix | verified | The host tests set the input table's sources (`tests/test_app_field_typing.py:261`) and never go through the keyboard driver's modes. The drivers build into the host's input part and have entry points (`runtime/host/moy_input.py:258-260,1026-1032`: `moy_kbd.c`, `moy_hid.c`, `moy_touch.c`), so the matrix is feasible on the host. |
| I2 | #242, one owner per policy | verified | S1 and S15's trackball item, above. The toolkit's half exists for all three policies (`api_typing`, `api_cancel`, `api_back`). |
| I3 | #242, explicit widget state | verified | A context holds one field edit, not one per field: `fe_id`, `fe_live`, `fe_cancel` (`moy_ui_api.h:744-750`) and one start text per instance (`:399-404`). The keyed table beside it (`moy_ui_keep_t`, 16 ids, a `seen` flag) is T7's. |
| I4 | #242, measure first, then draw | corrected | A measure pass is what `docs/surface_model_v1.md` §8 and §14 rule out. The engine already has the other answer: a layout from a stale measure owes the next frame (`moy_ui_api.h:498-499`), and a part with nothing to place from draws nothing and owes (`:654`, `:1410`). The item is planned as that rule applied to popovers and anchored menus. |
| E1 | #127, the backup archive | verified | No archive export or restore exists, and no backup tool under `tools/`. The store's packer writes and reads zips (`runtime/carts/moy_pack.py:95,144,183` and its C body), and the USB drive and SFTP transports the issue names have landed. |
| C1 | #160, the broken-cart badge | verified | `Workstation.cart_broken` (`runtime/shell/console_spine.py:85`) and `CrashGuard.broken_ids` (`runtime/shell/crash_guard.py:364`) are called from tests only. The launcher has a corner mark to copy, the favourite's star (`runtime/home/launcher_layer.py:1279`). Verdict: wire it. |
| F1 | #249, the stuck-run test | corrected | `test_a_stuck_run_with_the_vm_down_is_ended` (`tests/test_guition_on_glass.py:779`, body at `tests/on_glass.py:2866`). The wedge the issue reports is fixed: the test's `finally` brings a VM back after a failure (fea23f699, 2026-10-31, the day after). The one failure's cause is not known. |
| F2 | #249, the soft-reset suite | not checked | `test_twenty_soft_resets_leave_psram_flat` (`tests/on_glass.py:2667`); its bounds were last adjusted on 2026-11-08. Whether it still flakes can only be told by repeated runs on a Guition S3, and this pass drove no board. |

## What the owner's rule rests on (plan §6)

Checked for the verdicts the plan records under the owner's rule of
2026-11-11.

- **S4.** Five of the nine `KeyEdge` instances are declared as Ctrl+Z/Y
  trackers and used for nothing else: `map_editor_ui.py:173`,
  `music_editor_ui.py:60`, `scene_editor_ui.py:111`,
  `block_editor_ui.py:243`, `paint_layer.py:363`.
- **S9.** Read through a `ws` name, the six big forwards have 214 sites in
  source and 1,159 in tests (`screen` 45 and 303, `cart` 85 and 226,
  `cart_error` 32 and 164, `menu_view` 31 and 90, `sheet` 19 and 108, `ns` 2
  and 268). The nine cheap ones have 40 and 155.
- **S12.** `settings_licence`, the surface the licence text draws, is
  stored in 22 golden configurations. The page's key handling is its own
  change-of-`last_key` test (`about_ui.py:164-168`).
- **T5.** `moy_app.c` answers `MOY_APP_FULL` both for a full table
  (`:366`, `:1836`, `:1888`, `:1926`, `:1941`) and for an answer longer than
  its buffer (`:2467`, `:2497`, `:2524`), so each tier's text names half of
  one meaning.
- **The matrix's ELF column.** `tests/test_elf_commands.py:36,572` builds
  modules for the host through `tools/elf_cart.py`, and
  `tests/fixtures/elf/elf_page.moy` is an ELF app that draws a toolkit page.

## Tally

80 rows: N1–N15, S1–S16 and S1's live bug, T1–T16 and the audit's §2.4
comparison, G1–G7, X1–X8, the three live bugs as `extra.md` lists them
(L1–L3, which restate S1's bug, T8 and N3), the three measured items of the
brief that no finding carries (B1–B3), and the ten rows of the five issues.

- verified: 56
- corrected: 21
- dropped: 1 (L3)
- not checked: 2 (X8, F2)
