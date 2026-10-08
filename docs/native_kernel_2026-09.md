# The native kernel — the OS below the app verb table, Python as an app runtime (2026-09)

**Status: rev 2, 2026-09-27.** The direction doc #224 asks for as its
first deliverable. Rev 1 went through the adversarial architecture and
performance/hardware passes the same day (verdicts: **REWORK** / **PERF CASE
STANDS WITH FIXES**); every finding is folded into this revision, and **§12 is
the finding-by-finding ledger**. **Accepted by the owner 2026-10-05**; the
sprints of §6 run in the order §6 gives (owner, 2026-10-06), and where each one
stands is #224's. The kernel's language is C (owner, 2026-10-06, §5). Sprint 0's
findings are folded in (2026-10-05): §1 and §11 say what its census found, and
§6.1 holds the values its gate set. It reverses a
standing sentence — "MicroPython is the shell" (`docs/moycore_direction.md` §1)
— which is why it was settled on paper first.
**Tracks:** #224 (the direction) · #158 (the compiled tier; Doom on the floor
board is the memory half's acceptance test) · #66 (heap and frame numbers) ·
#186 (`moybuf`) · #58 (P4).
**Claims are labelled** the way `docs/surface_model_v1.md` labels them:
**MEASURED** (with its source), **SOURCE** (read out of code, with the file),
**ESTIMATED** (arithmetic shown) or **PREDICTED** (with the gate that settles
it). Measurements are cited to the issue that holds them; the few quoted here
are the ones an argument turns on.

---

## 0. The thesis

**The OS is native code, and Python is an app runtime.** Below the app verb
table sit the kernel, the loop, the Player, the store, the drivers, the window
managers, the toolkit and everything else the console needs while no Python app
runs. Above it sit apps and carts, in Python, Lua or wasm. Our own shipped apps
— the launcher, Settings, Files, Paint, the editors — stay Python apps on that
table (§3). The kernel is written in C (§5).

**What it buys.** A VM that runs only while a Python app or cart does, so every
app starts on a clean heap and stopping it hands every grown area back (SOURCE,
§1.3); a kernel that outlives an app's crash and records it (§5's containment,
sprint 2's recovery floor); one app API for Python and wasm apps (§2.1); one
toolkit for every app runtime (§4.5); and speed on the S3: native chrome, with
latency-bound state in internal SRAM where the Python heap can only be PSRAM
(§1.8, §4.6). A compiled cart's memory is the cart-runtime reserve, not free
PSRAM (`docs/wasm_tier_plan_2026-09.md`), and on the S3 boards that reserve is
what the Python heap and the C side leave; a cart that runs with the VM stopped
gets everything the VM held.

**Memory for carts proposed it; the census moved it onto those grounds.** The
boot's peak had one owner, the shelf scan, and it is fixed in Python (§11): the
S3 heap no longer boots to several times its live set (#224). The retained
memory's main owner, `_LAYER_POOL`, becomes the kernel's in sprint 3 (§1.4).

**MicroPython stays, in its right place.** It is why a kid can write an app in
200 lines with no build (`system_carts/moybyte.notes.moy`), why the editors iterate fast,
and the language most of the console's carts are written in. What this doc ends
is its role as the base a constrained OS stands on.

## 1. The problem, in evidence

### 1.1 The acceptance test: Doom, fresh and after a session

**MEASURED, #224 (sprint 0's census and its boot fixes, 2026-10-05).** Doom's
load footprint (`native/moy_wasm/moy_wasm_footprint.h`, for the S3 module the
compiler fix `14241a8` shrank) against free PSRAM at the Player's fit check: it
loads on fresh boots of both S3 boards, the Guition S3 — the floor board — by
the narrowest margin. A scripted session retains PSRAM outside the GC heap,
most of it `_LAYER_POOL` (§1.4), so the fit after a session is the harder one
on both boards. Since the shelf scan builds the slim catalogue directly (dev
`39b7186`), the T-Deck's GC heap boots to about 1 MB held, not 4.19 MB, and
the PSRAM it no longer holds is cart-available. #224 holds each
fit, per board and state. (On the Waveshare P4 Doom runs; the Guition P4's
refusal is its store's size, and #158's question.)

Doom is the **minimum**. A bigger zone, a bigger WAD or the next cart needs the
held space. The metric this doc is held to is **cart-available PSRAM at launch**
— free total AND largest block when the Player starts a cart, over at least five
boots, fresh and after a scripted session, on each S3 board — with the threshold
sprint 0 set (§6.1).

### 1.2 One heap is the system's memory

**SOURCE.** Everything on the Python side shares one VM: the boot spine
(`device/desktop_spine.py`) builds the store, the `Workstation`, the WM and the
frame loop in it; apps are Layer instances in it; Python carts run in it. Lua and
compiled carts run in C, but the loop around them, the drivers they read, the
audio they feed and the storage gate around a store op are Python. The C side — linear
memory, the Lua VM, the panel buffers, the layer pool, `moybuf` payloads — draws
on the same PSRAM through `heap_caps`.

### 1.3 The heap grows to its peak and keeps it

**SOURCE: MicroPython v1.28.0 (the pinned `MPY_TAG`), `py/gc.c`.** The esp32
port builds `MICROPY_GC_SPLIT_HEAP_AUTO`. When an allocation fails, `gc_alloc`
collects first and grows only if the collect did not make room; a growth adds an
area as large as the current total (so the total doubles), capped by what is
available (`gc_try_add_heap`). After a sweep, `gc_sweep_free_blocks` frees any
area other than the first that holds no live block — and `gc_sweep_all` at a VM
stop sweeps everything, so every grown area goes back. The heap is sized by its
PEAK demand and shrinks only when a whole area empties, which one survivor
prevents.

**MEASURED, #224's census.** The boot's peak is what sizes the heap, and its
owner was the shelf scan: it decoded every cart's sprites and map, then
`slim_carts` dropped most of what it decoded, and the areas grown for that
transient stayed because survivors sat in each. The scan builds the slim
catalogue directly (dev `39b7186`), so the boot leaves five areas, about 1 MB
held, on every console board (#224 has each board's figures).
An allocation a collect cannot place still doubles it: on some T-Deck boots a
sixth area grew between the launcher and a cart's start (#224). On 2026-09-02 one
fragmented Python allocation on an 8 MB S3 took
every remaining byte of PSRAM until a hard reset (#66). The reserve
(`MOYBYTE_GC_SPLIT_RESERVE`) and the flash partition stay as they are
(`docs/wasm_tier_plan_2026-09.md`, 2026-09-26).

### 1.4 Retained memory: `_LAYER_POOL` first

**MEASURED, #224's census.** What a scripted session keeps is PSRAM outside
the GC heap, whose held bytes do not move, and every byte of it has an owner:
it is retained, not fragmented.
Its largest owner on both S3 boards, and most of it on the T-Deck, is
**`device/device_canvas.py`'s `_LAYER_POOL`** (SOURCE): a dead cart's PSRAM
layer buffer goes into a pool keyed by size, and nothing is ever dropped from
the pool. The rest is buffers each made once and kept — the fold's scratch, the
bars' strips, the run-canvas cache, the audio backend. A fold scratch that a new
geometry replaces is released (`8199005`; the census found it leaking). The pool
lives outside the GC heap, so a VM stop returns it only through the `moy_alloc`
registry's free-all (§4.4); its retention waits for sprint 3, where the pool
becomes the kernel's (owner, 2026-10-05; §10 question 4). Separately, once WiFi
has been up, the S3's internal SRAM does not come back (#158, phase 1; the
census confirms it).

### 1.5 Long-lived data costs every collect

**MEASURED (#186; `runtime/moybuf.py`'s header).** The mark phase scans every
word of every live block, so collect time grows with live bytes.

### 1.6 The only full reclaim today is a reboot

**SOURCE: `ports/esp32/main.c` at v1.28.0.** The port's soft reset is the only
place that tears down timers, threads (`mp_thread_deinit`), BLE, ESP-NOW, UARTs
and pins, then re-runs the boot; the native-code arena
(`esp_native_code_free_all`) is freed there and at a cart-compile miss
(`patches/esp32_native_code_free.patch`). The console's boot repeats what the
user watches: the splash, the touch bring-up, the store scan, the radios.

### 1.7 What is not the problem: code, and the P4 desk

- **Code.** **SOURCE: `tools/esp32_build_lib.sh`, `freeze("${MODULES_DIR}",
  opt=3)`.** The shell's bytecode executes from flash; an imported module costs
  the heap its objects, not its code.
- **The P4 desk.** The P4 boards have the PSRAM (#158). The memory problem is the
  8 MB S3 boards', and they run the fullscreen tier: one app on the glass.

### 1.8 Speed: a by-product, predicted

Speed is not the case this doc rests on. What is known and predicted:

- **MEASURED, 2026-07-27, P4 glass (`docs/history/ui_damage_model_v1.md`
  §0.065).** A gated `cv.rect` call costs about 1.2 µs, about an empty
  MicroPython loop iteration. On the P4's map tab, if all of Python vanished the
  frame would go from 70.7 ms to about 32 ms — Python was roughly 55% of that
  chrome frame, and the rest is a PSRAM-bandwidth floor native code cannot
  remove. (The same doc later corrects §0.065's retained-blit comparison; the
  split quoted here is unaffected.) So native chrome is **PREDICTED** to be
  materially faster on the P4 too, and still bandwidth-bound.
- **PREDICTED:** shorter and rarer collects; a lower console floor under a cart
  (#158 splits the Guition S3's floor into touch polling, the Player's Python per
  tick, the composite and the diag meters — a few percent of Doom's fps, not a
  multiple); cheaper hit registration (`runtime/ui.py`'s header: ~395 rects ≈
  9.3 ms on the P4, which is why `Hits` is barred from grids).
- **Not a gain:** a Python app calling the kernel still pays the MicroPython→C
  call (~1.2 µs) plus any heap allocation for returned tuples. Native-to-native
  is cheap; Python-to-native is what it is today.

Sprint 0's speed baseline is #224's census — collections and their pauses,
frame rates, imports, exit to the launcher — and every sprint re-takes it (§6).

## 2. The line

### 2.1 The app verb table, and what it becomes

| table | who binds to it | authority |
|---|---|---|
| the cart verbs (for a compiled cart, its import table) | every cart: Python, Lua, wasm | `docs/moy_cart_api.md`, SPEC.md in moy-spec |
| the AppContext roles | shipped apps | `runtime/app_context.py` (`ROLES`) |
| the user-app permissions | `type: "app"` carts | `runtime/system_api.py`, a filter over the roles |

The roles become an **ABI**, implemented natively and bound into the runtimes
that host apps: MicroPython and wasm. Lua stays a CART runtime; there are no Lua
apps until a need for one is on the table.

**This is a redesign of the app API, not a new binding for it.** Today's roles
are object-shaped (SOURCE, `runtime/app_context.py`): `Surface.canvas()` returns
a canvas object, `batch(fn)` takes a Python callback, `Nav.play(cart, caller)`
takes a Layer and `open_app` an app class, `Carts.all()` returns cart objects,
`Theme.skin` returns style tables, `WallpaperRole.preview(cv, rect, dt)` draws
into a canvas object. An import-shaped ABI carries numbers, handles and buffers
only. The sketch the sprint 5 design starts from:

| today | import-shaped |
|---|---|
| a canvas object | a canvas handle; draws go through the cart verbs against it |
| `batch(fn)` | `begin` / `commit` on a storage session handle |
| a Layer or app class as an argument | an app id; the caller is the calling app's id |
| cart and document objects | ids, with fields read by verb or as a packed row buffer |
| style tables | a theme handle and token reads |
| a callback | an event on the app's queue, read in its tick |

`ctx.shell` (a pinned, shrinking list, `docs/app_api_v1.md`) closes before the
ABI freezes. The wasm app is the ABI's second consumer: an ungranted role is
simply not imported, the "no name" rule `runtime/system_api.py` already applies.
Getting a wasm app onto a board is the tier plan's phase 5, not this doc.

### 2.2 Placement

Everything the console needs while no Python app runs is OS. §2.2.1 places
every module of `runtime/` and `device/`; the grouping it applies, and the
sprints that follow it:

| group | headline modules | sprint |
|---|---|---|
| the kernel's spine: routing, back-stack, app registry, settings store, WiFi leases, crash record, strike ledger, recovery screen | `runtime/console.py` (in part), `runtime/crash_guard.py`, `runtime/system_store.py` | 2 |
| input: touch, keyboards, the BLE HID keyboard below `bluetooth` | `native/moy_input` (the table, the touch controllers, the T-Deck keyboard and trackball, the BLE HID central over NimBLE) | 3 |
| audio: the I2S feed and the sfx/music semantics the glue drains | `device_audio.py` (deleted by sprint 3's audio pass), `device/moycore_glue.py`'s audio half | 3 |
| the glass: canvas ownership, present, compositors | `device/device_canvas.py`, `device/dsi_panel.py`, `device/p4_canvas.py` | 3 |
| storage: the SD gate, the store of record and its journal | the boards' `with_sd`, `runtime/moy_journal.py` | 1b and 3 |
| the frame tail: loop, pump, idle blank, OTA health, PERF, serial | `runtime/frame_loop.py` (deleted by sprint 3's frame-tail pass: `native/moy_kernel/moy_loop.c`), `runtime/perf_line.py`, `runtime/dev_channel.py`, `device/moy_ota.py` | 3 |
| radios and links: WiFi, ESP-NOW, the updaters, the webhost and sync RPC | `device/device_wifi.py`, `device/moy_espnow.py`, `moy_c6_update.py` (deleted by sprint 3's links pass), `device/moy_webhost.py`, `moy_webserver.py` (deleted by sprint 3's links pass), `runtime/moy_sync.py` | 3 |
| the store: index, catalogue, covers, seed, project loading | `runtime/moy_carts.py`, `runtime/cover_cache.py`, `runtime/moy_seed.py`, `runtime/project.py` (in part) | 1b |
| the cart path: loop, tick model, runtime map, moycore glue, in-cart chrome, netplay lockstep, notices and toasts over a cart | `runtime/player.py`, `runtime/tick_model.py`, `device/moycore_glue.py`, `runtime/system_menu_ui.py`, `runtime/netplay.py`, the achievements/notify path | 4 |
| the roles and their services | `runtime/app_context.py`, `runtime/system_api.py`, `runtime/artwork.py`'s `ArtworkService` (the rest of the file is Paint, an app), `runtime/wallpaper.py` | 5 |
| the toolkit's core | `runtime/ui.py` | 6 |
| the window managers | `runtime/wm_windowed.py`, `runtime/wm_desk.py`, `runtime/wm_chrome.py`, the rest of `runtime/console.py` | 7 |
| apps (stay Python) | the launcher, Settings, Files, Paint, Calc, Notes, Storybook, Appearance, the Editor and its tabs (the Studio, `docs/studio_2026-09.md`, panes and docking included), `runtime/blocks.py` (the block compiler is an editor's) | — |

The Zero companion board (`firmware/seeed_xiao_esp32s3_zero/`) is a fifth
target: headless, the cart store, running the webhost and the sync RPC. Every
link gate counts it.

### 2.2.1 Every module

Every tracked Python module of `runtime/` and `device/`, the C shims beside
them, the Python each board authors under `firmware/<board>/modules/` and the
browser tier's own Python is placed below; the copies a build stages into a
board's tree are not listed. The rule applied throughout is §0's: everything the
console needs while no Python app runs is OS.

- **Group** is a row of the table above by its first words (spine, input, audio,
  glass, storage, frame tail, radios and links, store, cart path, roles,
  toolkit, window managers), or **app** (stays Python, §3), **host-only** (the
  simulator, the browser page and build tooling: not on a board), **bring-up**
  (the boot ladder and the REPL smokes) or **split** (the note names the parts).
- **Sprint** is the sprint of §6 in which the module crosses, is rebased onto
  the kernel or is deleted. "—" is a module that never crosses: an app, or a
  file that is not on a board. "rest stays" on a split names the part that
  remains Python.
- **open** is a placement this doc does not settle. The question is in the
  note, the group is the one the module belongs to if the answer is "OS", and
  the owner decides it.
- The Zero takes the store modules (`runtime/moy_carts.py` and its siblings),
  `runtime/moy_fs.py`, `runtime/moy_journal.py`, `runtime/moy_sync.py`,
  `runtime/ticks.py`, `device/moy_webhost.py` and
  `device/moy_ota.py` (`firmware/seeed_xiao_esp32s3_zero/board.toml` is the
  authority), so a crossing of any of them is a Zero link gate.

**`device/`**

| module | group | sprint | note |
|---|---|---|---|
| `device/banded_panel.py` | glass | 3 | the compositor both banded-panel S3 boards run; their per-board subclasses are in the board table |
| `device/boot_shell.py` | bring-up | open | the boot-mode ladder every board's `moybyte_shell.py` calls; its `desktop` mode is the production boot, every other mode a REPL smoke. Question: with a native loop as the boot entry, do the smoke modes become native bring-up modes, dev-channel words, or stay Python probes? One answer covers this row, the boards' entry stubs and `moybyte_shell.py` files, and the three smoke modules |
| `cart_net.py` | radios and links | 3 | deleted 2026-10-07: Get Carts' transport is `device/wire_links.py`'s `CartNet` over `native/moy_net/moy_ota.c`'s client; the app above it stays Python |
| `device/desktop_spine.py` | split | 3 + 7 | the boot order and `Desktop.run`, the frame loop, cross with the frame tail; the `Workstation` and WM construction goes with `runtime/console.py` |
| `device/device_api.py` | cart path | open | a re-export of `make_api`; follows `runtime/cart_api.py` |
| `device_audio.py` | audio | 3 | deleted 2026-10-07: the I2S feed and the six verbs are `native/moy_audio`'s |
| `device/device_canvas.py` | glass | 3 | the one canvas class every tier runs; `runtime/host_canvas.py` rebinds with it and `tools/p4_conformance.py` is its check on glass |
| `device/device_diag.py` | frame tail | 3 | the serial diagnostics the frame loop emits between frames |
| `device/device_util.py` | frame tail | 3 | the leaf under the device modules (tick helpers, diag shims); deleted with its last device importer |
| `device/device_wifi.py` | radios and links | 3 | the radio driver; the lease that gates it (`wifi_hold` / `wifi_release`) is the spine's, in `runtime/console_spine.py` over `runtime/moy_spine.py` |
| `device/dsi_panel.py` | glass | 3 | the shared P4 DSI compositor, rotated variant included |
| `moy_c6_update.py` | radios and links | 3 | deleted 2026-10-07: the C6's image streams into the radio through `native/moy_net/moy_ota.c`'s C6 sink; `device/moy_ota.py`'s `C6Updater` is Settings → UPGRADE C6 RADIO's face |
| `device/moy_espnow.py` | radios and links | 3 | the board's one ESP-NOW owner; netplay's lockstep over it is the cart path's |
| `device/moy_ota.py` | frame tail | 3 | the firmware's identity and the updaters' Settings face over `native/moy_net/moy_ota.c` (crossed 2026-10-07, with its HTTP(S) client, `moy_http.py`); OTA health is `device/moy_ota_health.py`. The Zero takes it |
| `device/moy_webhost.py` | radios and links | 3 | the webhost; the Zero takes it |
| `moy_webserver.py` | radios and links | 3 | deleted 2026-10-08: the socket and HTTP core are `native/moy_net`'s, the Zero's setup form included |
| `device/moybyte_diag.py` | frame tail | 3 | offline log capture to SD for the T-Deck, where the loop starves USB serial |
| `device/moybyte_sd.py` | storage | 1b | the T-Deck's SD gate on the SPI host the panel owns; a per-op teardown hangs the board with no panic |
| `device/moycore_glue.py` | split | 3 + 4 | the audio drain half crosses with audio; the input refresh and the frame around `tick()` cross with the cart path |
| `device/p4_canvas.py` | glass | 3 | the P4 system canvas over the DSI framebuffer, with the PPA composite hooks |
| `device/p4_desktop.py` | split | 3 + 7 | the P4 canvas, deferred present, BLE keyboard and C6 updater wiring cross with the glass and radios; installing the windowed WM goes with the WMs |

**`runtime/`**

| module | group | sprint | note |
|---|---|---|---|
| `runtime/__init__.py` | host-only | — | the host package marker |
| `runtime/achievements_ui.py` | cart path | 4 | the achievement and Easter-egg drawing: the "achievements/notify path" of §2.2 |
| `runtime/app_context.py` | roles | 5 | `ROLES` is the ABI's source |
| `runtime/app_decls.py` | spine | 2 | the app registry's frozen declaration, generated from the manifests by `tools/gen_device_carts.py`; the manifests stay the source |
| `runtime/app_shell.py` | app | — | the list shell Files, Storybook and Get Carts share |
| `runtime/appearance.py` | roles | 5 | `Workstation.look`: theme variant, skin, font scale, wallpaper and bar icons, the state behind the Theme and Wallpaper roles |
| `runtime/appearance_app.py` | app | — | Appearance |
| `runtime/artwork.py` | split | 5, rest stays | `ArtworkService` is a role service (5); `PaintDocument`, `PaintAppLayout` and `PaintAppLayer`, the Paint app, stay Python |
| `runtime/audio.py` | split | 3, rest stays | `AudioEngine` crossed with audio (deleted 2026-10-07); the bank model (`SFX`, `MusicTrack`, `AudioBank`) is the music editor's data and stays |
| `runtime/audio_binding.py` | host-only | — | the host's ctypes binding of libmoy audio; sprint 3's audio crossing rebinds it |
| `runtime/bar_layer.py` | window managers | 7 | the top bar and dock every WM draws; its geometry constants are read outside it |
| `runtime/block_editor_ui.py` | app | — | the Editor's Blocks tab |
| `runtime/blocks.py` | app | — | the block compiler is an editor's (§2.2) |
| `runtime/boot_carts.py` | store | 1b | the boot's cart step, split from `runtime/device_boot.py`: seed the store, read its catalogue, fall back to the built-in carts; `DeviceBoot` takes it as a mixin |
| `runtime/calc_app.py` | app | — | Calc |
| `runtime/cards_layer.py` | app | — | the Config tab's "Make it mine" cards |
| `runtime/cart_api.py` | cart path | open | `make_api` builds the Python cart's namespace, one body on every tier; a Python cart runs in the VM (§1.2). Question: does it stay as the Python runtime's binding, thinning as the services under it go native, or does the kernel install the verb table into a Python cart's globals as libmoy's binding does for Lua and wasm? `device/device_api.py`, `runtime/cart_verbs.py` and `runtime/host_api.py`'s re-export follow the answer |
| `runtime/cart_files.py` | store | 1b | a compiled cart's written files; native before sprint 4's VM-free gate asks for it |
| `runtime/cart_index.py` | app | — | Get Carts' engine: the index, the per-console plan and `Install`; it writes through the store |
| `runtime/cart_manager.py` | store | 1b | the shelf's roster: scan, new, duplicate, delete, favorites and recents |
| `runtime/cart_verbs.py` | cart path | open | the cart API's names as one tuple, read by the code editor's highlighter and the block compiler; follows `runtime/cart_api.py` |
| `runtime/chrome.py` | split | 5 + 6 + 7, rest stays | token tables, `theme_colors` and the default bar icons → 5; colour names, the glyph vocabulary and scaled-text helpers → 6; `Layout` → 7 with the bar it positions; `CodeLayout` stays with the Editor |
| `runtime/code_layer.py` | app | — | the Editor's Code tab |
| `runtime/console.py` | split | 2 + 7 | the spine's half is `runtime/console_spine.py` (2); the layer stack, frame, pointer, composite and the rest → 7, where the file is deleted; `wire_workstation_core` loses a line as each service crosses |
| `runtime/console_spine.py` | spine | 2 | the run and exit verbs, app registration and resolution, the WiFi lease and the settings wiring, over `runtime/moy_spine.py`; its Python side (the app objects, the surfaces a route lands on, the radio glue) stays until each subject's sprint (`docs/kernel_spine_2026-10.md`) |
| `runtime/console_notices.py` | cart path | 4 | achievements wiring, the notice banner and its toast deadline; the firmware-update verdict it carries is raised by sprint 3's OTA health |
| `runtime/console_perf.py` | frame tail | 3 | the PERF meters and the capture frame tail |
| `runtime/console_saves.py` | app | — | the Editor's save and PLAY verbs on the Workstation; PLAY starts the Player through the spine |
| `runtime/console_settings.py` | spine | 2 | the toggle setters over the settings store; each toggle's subject (frame tail, input, glass, store) crosses in its own sprint |
| `runtime/cover_cache.py` | store | 1b | the shelf's cover and icon pipeline |
| `runtime/cover_png.py` | store | 1b | the native `moy_png` already decodes on boards and in the browser; the Python reader is the host's |
| `runtime/crash_guard.py` | spine | 2 | the strike ledger for apps and the wallpaper; its C twin is `moy_spine.CrashGuard` (`native/moy_spine/moy_ledger.c`), which the native spine's consoles run |
| `runtime/dev_channel.py` | frame tail | 3 | the serial dev channel, one vocabulary on every board |
| `runtime/device_boot.py` | split | 3 + 4 | `DeviceBoot`'s runtime probe and map → 4; its boot screen, the frame pump, OTA health, idle blank, PERF sampler and `FrameLoop` → 3; its cart step is `runtime/boot_carts.py` |
| `runtime/editor_app.py` | app | — | the Editor and its tab ladder |
| `runtime/editor_handle.py` | app | — | the Editor's engine behind `open_editor`; a cart that calls it keeps the VM (§2.3) |
| `runtime/editors.py` | app | — | the umbrella that re-exports the editor cores |
| `runtime/editors_base.py` | app | — | the shared typed-key and text-entry leaf |
| `runtime/editors_block.py` | app | — | the block program model |
| `runtime/editors_code.py` | app | — | the code buffer |
| `runtime/editors_music.py` | app | — | the tracker-style music editor core |
| `runtime/editors_paint_map.py` | app | — | the paint and map editor cores |
| `runtime/editors_scene.py` | app | — | the scene placement core |
| `runtime/editors_sheet.py` | app | — | the sprite, tile and map models; `IconSheet` is also the bar's icon data, which the bar reads in 7 |
| `runtime/file_widgets.py` | app | — | the thumbnail-grid picker Files and Paint share |
| `runtime/files_app.py` | app | — | Files |
| `runtime/font.py` | host-only | — | the host's petme128 glyph table, standing in for the `framebuf` font a board has; the native canvas carries its font from sprint 3, and one text path for every runtime is sprint 6's (§10) |
| `runtime/getcarts_app.py` | app | — | Get Carts, a registered system app |
| `runtime/gfx_binding.py` | host-only | — | the host's `moy_gfx`; sprint 3's glass rebinds it |
| `runtime/history_router.py` | app | — | the Editor's UNDO and REDO router |
| `runtime/host_api.py` | host-only | — | the host's service fakes and the driver; it re-exports `make_api` |
| `runtime/host_app.py` | host-only | — | the simulator's harness; it becomes the host driver of the native loop (§4.2) as sprints 3 to 7 land |
| `runtime/host_canvas.py` | host-only | — | the boards' canvas class on CPython; rebinds with `device/device_canvas.py` in 3 |
| `runtime/input.py` | input | 3 | the host's eight names over the native input table (`HostInputTable`) |
| `runtime/launcher_layer.py` | app | — | the launcher stays Python unless the return-budget measurement fails (§3); `EditorPickerLayer` rides in it |
| `runtime/layers.py` | split | 7, rest stays | the `Layer` protocol stays as the apps' base; the draw-only overlays and the object-surface adapters go with the WMs |
| `runtime/layout_base.py` | toolkit | 6 | the baseline predicate every `*Layout` derives from, the apps' layouts included |
| `runtime/lua_binding.py` | host-only | — | the host's ctypes binding of libmoy's Lua |
| `runtime/lua_ext.py` | cart path | 4 | the Lua superset's shared glue: the per-name rulings of §2.3 are made here |
| `runtime/lua_host.py` | host-only | — | the host twin of `device/moycore_glue.py`; follows it in 4 |
| `runtime/map_editor_ui.py` | app | — | the Editor's Map tab |
| `runtime/moy_carts.py` | store | 1b | the `.moy` store, read and written by path; its callers reach a cart through `runtime/moy_catalogue.py`; the Zero takes it |
| `runtime/moy_catalogue.py` | store | 1b | the store's interface: carts by handle (§4.3), the native store's call shapes and errors written over the Python store; every caller of the catalogue, a whole-cart load, create, duplicate and delete goes through it |
| `runtime/moy_file_ops.py` | store | 1b | a user file's life: history sidecars, rename, trash, restore; the Zero takes it |
| `runtime/moy_files.py` | store | 1b | the user-files layer under the Files app and role; the Zero takes it |
| `runtime/moy_fs.py` | store | 1b | the crash-safe write primitive every store module stands on, so it goes first; the Zero takes it |
| `runtime/moy_image.py` | split | 4 + 5 | `Image`, the `image` verb's object and a kernel handle (§2.3) → 4; the wallpaper-preview sidecar → 5; the codec is `runtime/moyimg.py` |
| `runtime/moy_index.py` | store | 1a | the store's index: a row per cart folder named by a handle, slot and generation, checked on every use; no I/O. Sprint 1a's language spike (§5, §6) wrote it as a C twin, `native/moy_index/moy_index.c` |
| `runtime/moy_journal.py` | store | 1b | the undo journal, named under storage in §2.2; the Zero takes it |
| `runtime/moy_qr.py` | radios and links | open | the pairing QR encoder for the web-console screen; follows `runtime/web_console_ui.py` |
| `runtime/moy_seed.py` | store | 1b | seeding and the sweep of retired seeds; the Zero takes it |
| `runtime/moy_spine.py` | spine | 2 | the spine's interface and its Python twin: handle tables, the app registry, the back-stack and return records, the WiFi lease mask and the settings rows; the native `moy_spine` replaces it on every console (`docs/kernel_spine_2026-10.md`); the host, the browser build and the Zero keep this file |
| `runtime/moy_store_base.py` | store | 1b | the store's on-card layout and shared rules; the Zero takes it |
| `runtime/moy_sync.py` | radios and links | 3 | the sync RPC's push half; the Zero takes it |
| `runtime/moybuf.py` | spine | 2 | the Python view over `moy_alloc` entries; the registry becomes a handle table of kind BUF (`docs/kernel_spine_2026-10.md` §1), and the stop inventory (§4.4) clears its rows or moves them to kernel ownership |
| `moyhost_audio.c` | host-only | — | deleted 2026-10-07: `runtime/audio_binding.py` compiles `native/moy_audio` itself |
| `runtime/moyhost_console.h` | host-only | — | the console the Lua and wasm host shims share |
| `runtime/moyhost_gfx.c` | host-only | — | the C shim `runtime/gfx_binding.py` compiles |
| `runtime/moyhost_lua.c` | host-only | — | the C shim `runtime/lua_binding.py` compiles |
| `runtime/moyhost_wasm.c` | host-only | — | the C shim `runtime/wasm_binding.py` compiles |
| `runtime/moyimg.py` | store | 1b | the moyimg codec and the content stamp the store's sidecars are keyed on, split from `runtime/moy_image.py`; the Zero takes it |
| `runtime/music_editor_ui.py` | app | — | the Editor's Music tab |
| `runtime/native_build.py` | host-only | — | builds the host's ctypes bindings; the kernel's host binding (§4.2) builds through it from the first crossing |
| `runtime/netplay.py` | cart path | 4 | lockstep: inputs, never state |
| `runtime/op_history.py` | app | — | the in-RAM undo core every editor and Desk Lab app shares |
| `runtime/paint_layer.py` | app | — | the sprite and icon paint editor, and EDIT ICONS |
| `runtime/palette.py` | glass | 3 | the MOY64 table the canvas converts through; the native canvas owns it |
| `runtime/perf_hud.py` | frame tail | 3 | the FPS and frame-time overlay, drawn over any content |
| `runtime/perf_line.py` | frame tail | 3 | the PERF line's one field table, formatter and parser |
| `runtime/player.py` | cart path | 4 | the cart loop and the runtime map |
| `runtime/players.py` | cart path | 4 | input routing to player slots and the net seam netplay uses |
| `runtime/project.py` | app | — | `Project`: `runtime/project_store.py`'s class plus the Editor's half, the per-tab op histories (`history_for`) and the CONFIG tab's undo codec |
| `runtime/project_store.py` | store | 1b | the open cart's data, its builders and its `commit_*` verbs, split from `runtime/project.py`; the code commit's graduation check still asks the block compiler and Storybook's deck compiler, which are apps' |
| `runtime/scene_editor_ui.py` | app | — | the Editor's Scene tab |
| `runtime/settings_layer.py` | app | — | Settings; its WIFI panel drives the spine's leases |
| `runtime/skin.py` | toolkit | 6 | the skin catalogue stays unchanged data the native toolkit installs (§4.5) |
| `runtime/storybook_app.py` | app | — | Storybook |
| `runtime/surface.py` | glass | 3 | the Surface and its dirty protocol, kernel-owned after the `surface_model_v1.md` amendment (§8) |
| `runtime/system_api.py` | roles | 5 | the user-app permission filter over the roles |
| `runtime/system_menu_ui.py` | cart path | 4 | the ≡ system menu over a cart |
| `runtime/system_store.py` | spine | 2 | `system.json`'s owner, the settings store |
| `runtime/text_console.py` | app | — | a script's screen; a Python script is itself the app |
| `runtime/text_modes.py` | app | — | the editor mode table |
| `runtime/tick_model.py` | cart path | 4 | the scheduler that paces a cart's logic and draw |
| `runtime/ticks.py` | app | — | the Python side's clock shim, a support leaf; the native modules have their own clock; the Zero takes it |
| `runtime/ui.py` | toolkit | 6 | the immediate-mode toolkit core; `docs/theming_2026-09.md` §8 lists what is built ahead of it |
| `runtime/update_ui.py` | frame tail | open | the firmware-update flow (SD, online, C6) with its per-frame install pump; Settings opens it. Question: is it an OS screen that crosses with `device/moy_ota.py` in 3 (or with the toolkit in 6), because a console that cannot start its VM must still update itself, or a Settings screen that stays Python above a native updater? |
| `runtime/wallpaper.py` | roles | 5 | the backdrop renderer behind the wallpaper role; a wallpaper cart runs under the spine's crash guard |
| `runtime/wasm_binding.py` | host-only | — | the host's ctypes binding of libmoy's wasm import table |
| `runtime/wasm_host.py` | host-only | — | the host twin of `device/moycore_glue.py` for wasm; follows it in 4 |
| `runtime/web_console.py` | radios and links | 3 | the web-console switch: pairing pin, paired url, and parking the glass while a browser edits the store |
| `runtime/web_console_ui.py` | radios and links | open | the screen the glass parks on while no app runs, so it is OS by §2.2's rule; it draws with `runtime/ui.py` and `runtime/moy_qr.py`. Question: does it cross in 3 with the webhost, drawn without the toolkit, or wait for the toolkit in 6? |
| `runtime/web_input.py` | input | 3 | the browser's event decode; the boards deny it |
| `runtime/widgets.py` | split | 3 + 4 + 5 + 6 | `Pointer` and `pointer_state`, `_SilentAudio` (deleted) → 3; `Achievements`, `Pmem`, `Actor`, `Scenes`, `SceneWorld`, `Popup` → 4; `Clipboard` → 5; `ConfirmTap`, `_Blit` and the small draw helpers → 6 |
| `runtime/wm.py` | window managers | 7 | the memoized draw stack, the game-to-system composite and navigation over the spine's back-stack (`runtime/moy_spine.py`'s `BackStack`, 2) |
| `runtime/wm_chrome.py` | window managers | 7 | the windowed WM's title strip, borders and taskbar chips |
| `runtime/wm_desk.py` | window managers | 7 | the windowed desk's root layer and backdrop cache |
| `runtime/wm_windowed.py` | window managers | 7 | the P4 desk; the Studio's Run pane lands before it starts (§6) |

**Board-specific Python (`firmware/<board>/modules/`)**

| module | group | sprint | note |
|---|---|---|---|
| `firmware/lilygo_t_deck_plus_mainline/modules/tdeck_panel.py` | glass | 3 | the T-Deck's thin subclass over `device/banded_panel.py` |
| `firmware/esp32_p4_wifi6_touch_lcd_7b/modules/p4_display.py` | glass | 3 | this board's backlight and the shared DSI compositor |
| `firmware/guition_jc3248w535/modules/guition_panel.py` | glass | 3 | the Guition S3's thin subclass over `device/banded_panel.py` |
| `firmware/guition_jc8012p4a1c/modules/guition_p4_display.py` | glass | 3 | this board's backlight and the rotated DSI compositor |
| `firmware/seeed_xiao_esp32s3_zero/modules/zero_host.py` | radios and links | open | the Zero runs no app and no cart, so the memory goal does not reach it, yet everything it does is the OS's by §2.2's rule and every link gate counts it. Answered (`docs/kernel_survival_2026-10.md` §6.5): the links crossed in 3; the boot order, the seed, the OTA driver and the setup form stay with the board glue |
| each console board's `boot.py`, `main.py` and `moybyte_shell.py` | bring-up | open | entry stubs and boot-mode declarations on the T-Deck, Waveshare P4, Guition S3 and Guition P4; see `device/boot_shell.py` |
| the bring-up smokes `tdeck_smoke.py`, `guition_smoke.py` and `guition_p4_smoke.py` | bring-up | open | per-subsystem REPL smokes; see `device/boot_shell.py` |
| each console board's `moy_runtime.py` | frame tail | 3 | board glue: builds the board's drivers and hands them to the spine, and dissolves into the kernel's per-board configuration as they cross; the T-Deck's also holds the SD gate wrapper, storage's |
| the Zero's `zero_gpio.py` (deleted 2026-10-08) and `zero_setup.py` | radios and links | 3 | the allowlisted GPIO endpoint is `native/moy_net/moy_gpio.c`; the first-run access point, its portal and transport are `moy_net`'s, the form stays |
| the Zero's `boot.py` and `main.py` | radios and links | open | the entry stubs; `main.py` auto-boots the store host, guarded so a failure falls to the REPL; follow `zero_host.py` |

**The browser tier (`firmware/web_runner/`)**

| module | group | sprint | note |
|---|---|---|---|
| `firmware/web_runner/carts_link.py` | radios and links | 3 | the page's half of Get Carts' transport: fetch, the OPFS keeper and the file picker |
| `firmware/web_runner/gpio_link.py` | radios and links | 3 | the page's half of the Zero's GPIO verbs, a queue |
| `firmware/web_runner/serve.py` | host-only | — | the local static server for the web build |
| `firmware/web_runner/shims/zlib.py` | host-only | — | a browser-VM stdlib shim over `deflate` |
| `firmware/web_runner/update_link.py` | radios and links | 3 | the page's half of updating the board that served it |
| `firmware/web_runner/variant/manifest.py` | host-only | — | the web build's freeze manifest |
| `firmware/web_runner/web_boot.py` | frame tail | 3 | the browser's boot; JS drives the native loop per frame (§4.2) |
| `firmware/web_runner/web_canvas.py` | glass | 3 | the wasm head's raster, the browser's canvas |
| `firmware/web_runner/web_p8.py` | host-only | — | browser-only glue around moy-spec's PICO-8 converter |

### 2.3 The Lua superset: registration is a deny list

A Lua cart runs with no VM only if every name it can call is native. Moybyte
registers EVERY non-libmoy name in the cart's namespace as a trampoline into
Python (SOURCE: `runtime/lua_ext.py`, `docs/moycore_direction.md` §3 — a deny
list, not an allow list): `wifi`, `net`, `on_net` (a callback into the cart),
`pin_write`, `pin_read`, `draw_scene`, the editor trampolines, and the
object-valued set that rides a prelude over handles (`make_layer`,
`draw_layer`, `image`/`Image`, the scene and actor verbs). Netplay's lockstep
runs in Python every frame a netplay cart runs.

The layer verbs have a native home already: libmoy installs `make_layer` and
`draw_layer` as CORE C verbs (moy-spec `b9dbba1`, 2026-08-19), reusing the ONE
verb table by swapping the console's canvas around each call
(`native/moycore/libmoy/moy_lua.c`). They return nil only because moybyte
supplies no Display seam, and moybyte's prelude replaces them because ours take
an `Image` and composite. Supplying the seam natively and making `image` a
kernel handle removes the "second console in C" cost
`docs/moycore_direction.md` §3 was decided against.

Every other registered name gets one of three rulings in sprint 4, recorded per
name: **native**, **absent in a no-VM run**, or **this cart keeps the VM**.
`open_editor` is the clearest "keeps the VM": it returns an in-cart editor the
cart draws and taps every frame (`PRELUDE_EDITOR`, #112). A cart census gate
says which carts in the tree run VM-free, by name.

## 3. Why our own apps stay Python

- **Size.** The OS side is about 45,000 lines of Python (ESTIMATED from the
  architecture review's count, 2026-09-27: the §2.2 `runtime/` modules ~18,500,
  `device/` ~14,500, unplaced services ~11,900; #224 carries the count by
  placement, taken from §2.2.1). The apps are roughly 23,000 more. Rewriting the
  apps frees no memory, because their memory goes when the VM stops.
- **Change rate.** The editors and Paint change most; a Python edit needs no
  firmware build.
- **They keep the ABI honest.** Our apps on the table a kid's app uses is the
  test that table gets.
- **The system-as-carts direction stays open** (#55,
  `docs/shell_architecture_v1.md` §2).

**The launcher is decided by measurement.** Every cart returns to it. A Python
launcher means every return starts the VM, re-imports the launcher and rebuilds
its live set from a 64 KB first area, doubling through collects as it grows.
Sprint 0 timed the parts on both S3 boards (#224): the imports, the launcher's
construction, and an exit whose cost is mostly one full collect. The return
budget is set when sprint 4 can measure a Python launcher's whole return (owner,
2026-10-05; §10 question 1); if the launcher misses it, it becomes the one
shipped app that goes native.

**The recovery floor is native.** `docs/shell_architecture_v1.md` §2.3 requires
"a hardcoded minimal recovery UI … a floor that cannot itself fail". A kernel
that reboots into a Python launcher has no floor when the VM cannot start, so a
native recovery screen is sprint 2's.

## 4. The shape

### 4.1 Three layers, two boundaries

    hardware drivers (C, ESP-IDF)  ←→  the kernel (C)  ←→  libmoy, moycore, WAMR (C)
                                              ↑
                              app runtimes: MicroPython, Lua, wasm

The drivers, the vendored code and the kernel are one language, so a boundary
between layers is a plain C call, with nothing to marshal. The repo's Python drivers (touch,
the BLE keyboard, audio) become kernel code (§2.2).

**What the boundaries cost.** A call between layers costs a C call across
translation units. The rule that keeps it cheap is libmoy's — **a hot loop lives
on one side**: the kernel calls a C kernel per rect, sprite or band. Two costs
to design for:

- **Placement:** kernel code lands in flash by default, where Lua's interpreter
  hot path is placed in IRAM by `MOY_HOT` (`native/moy_lua/lua/luaconf.h`);
  kernel code competes with frozen bytecode and the Lua VM for the S3's
  instruction cache.
- **Allocation:** through `heap_caps`, so kernel data lands in the RAM chosen
  for it (§4.6).

### 4.2 One loop, the same inversion on every tier

The native loop owns the frame on every tier, and Python is an upcall on every
tier: the board's main task drives the loop on a device, JS drives it per frame
in the browser (as it drives `step_frame_json` into Python today,
`firmware/web_runner/web_boot.py`), and the host harness drives it under
CPython. Python apps are called from the loop, never the other way round. That
keeps host == device (CLAUDE.md's first rule; "no device-only shell logic",
`docs/shell_architecture_v1.md` §4).

**VM stop is device-only; its contract is not.** Stopping the VM is a memory
mechanism for the S3 boards. What every tier tests is the property that makes it
safe: **a VM-free run makes zero Python upcalls**, counted by the kernel and
asserted in the host goldens, the browser suites and the on-glass suites alike.
The device additionally stops the VM.

The kernel is one library with three bindings, the pattern `moy_gfx` runs: a
MicroPython usermod on the boards and in the browser; ctypes on the host
(`runtime/native_build.py`); the unix MicroPython build (`tests/unix_mp.py`) for
the traces. **Each crossing deletes its Python implementation in the same
change** ("zero duplication", `moycore_direction.md` §3), and carries a parity
test across the bindings, as `tests/test_gfx_binding.py` does.

### 4.3 Handles, not objects

Kernel state holds no runtime's object beyond a call. A runtime holds integer
handles — index plus generation — validated against a live table on every use,
so a handle that outlived its VM fails loudly (`moybuf`'s rule, "loud beats
corrupt"; moycore's int layer handles). That is what makes a VM stop safe.
`surface_model_v1.md` §8 buried per-object generation counters because a web
client's cache aliased a recreated surface into a wrong replay; here the table's
owner checks every use and there is no remote cache, so that failure cannot
arise.

### 4.4 Stopping the VM

The VM starts when a Python app or cart opens and stops when the last one
closes. On the S3 fullscreen tier that is one app at a time; on the P4 desk the
VM stays up while any Python app is open.

**The inventory (sprint 0, 2026-10-05).** A stop is harder than a sweep.
SOURCE: MicroPython v1.28.0's `ports/esp32/main.c` (`mp_task` and its
`soft_reset_exit:` list), `mpthreadport.c`, `machine_pin.c`,
`usb_serial_jtag.c`, `py/runtime.c` and `py/gc.c`; ours as each row names.
"Soft reset" is what the port does today, "stop" what the VM stop must do. The
P4 rows matter only if a P4 ever stops its VM (§10 question 5).

| what | where | soft reset today | stop | sprint |
|---|---|---|---|---|
| ***Python callbacks run from an ISR or another task*** | | | | |
| GPIO IRQs (T-Deck trackball, the GT911 INT gate) | the kernel's IRAM ISRs (`native/moy_input/moy_input_task.c`); a Python `Pin.irq` handler is held in the `machine_pin_irq_handler` root | the kernel's sweep (`moy_kernel_pins_deinit`, in place of `machine_pins_deinit`) removes only the pins with a Python handler; a kernel GPIO ISR survives | the same | 0 (the rule); 3 (landed, input pass) |
| the input task (T-Deck) | `native/moy_input/moy_input_task.c`'s core-0 task, one pass per frame's kick | no VM call in it; it survives | kept | 3 (landed, input pass) |
| the BLE keyboard | the kernel's central (`native/moy_input/moy_ble_task.c`): NimBLE's host task runs only C, and the console images carry no `bluetooth` module | the kernel owns the host; the soft reset does not touch it | kept | 3 (landed, input pass) |
| ESP-NOW | `native/moy_net/moy_link.c` holds esp_now's one receive callback, which latches each frame into the kernel's ring in PSRAM; `device/moy_espnow.py` drains it through `moy_net.Link` per frame | nothing: the port's `espnow` module is out of the console images, so the link and its ring stay up | the same | 3 (landed, pass 2), 4 (lockstep) |
| the legacy I2S feed | `device_audio.py`'s `i2s.irq`, taken only when the core-1 task failed to start | the object's finaliser, at the sweep | the same | 3 (deleted, 2026-10-07: no Python feeds a speaker) |
| `machine.Timer`, `micropython.schedule`, UART, socket callbacks, dupterm | no user in `runtime/`, `device/` or a board's modules | `machine_timer_deinit_all`, `machine_uart_deinit_all`, `socket_events_deinit` | kept | — |
| the console's RX ISR | `usb_serial_jtag.c` wakes `mp_main_task_handle` on every packet | the VM's task never dies | the handle moves to the kernel's task before the VM's task is deleted, or a byte from the host notifies a freed task | 0 |
| the console's input ring | the RX ISR moves bytes into the port's stdin ring only while it has room, and only the VM reads the ring; the ISR also schedules a `KeyboardInterrupt` on `mp_interrupt_char` | the VM drains it | while the VM is down the kernel drains the ring and re-polls the USB FIFO every window, and sets `mp_interrupt_char` to -1 — a host writing through a stop otherwise stalls the console's input for good (found by the spike, 2026-10-05) | 0 |
| the wasm session's task | `native/moy_wasm/modmoy_wasm.c`'s `g_sess` asks the VM's task to run `vm_fn` for a cart's `read` and file calls, through `moycore_wasm_gate` | nothing ends the session | moycore's `close()` (`wasm_end`) first | 1b (the card's volume), 3 (the internal volumes), 4 |
| ***C state that survives `mp_deinit`*** | | | | |
| root pointers | seven in `native/moycore/modmoycore.c` (`moycore_calls`, `_wasm_file`, `_wasm_gate`, `_wasm_files`, `_p8mem`, `_p8rom`, `_view`), and the port's | kept: `mp_init` resets only its own fields, and the collector scans the whole root section, so a stale root marks whatever now sits at its address in the reused first area | the root section zeroed after `mp_deinit` | 0 |
| moycore's statics | `g_p8mem`/`g_p8rom` (into the `p8_memory` bytearrays); `RUN`'s canvas, sheet, map, layer, `snap` and `aq` pointers into Python buffers, and `RUN.cfg`; `WR`, `g_wread` | kept (`open` re-zeroes `RUN`) | moycore's stop hook: `close()`, then the p8 pointers cleared | 0; 4 makes cart state the kernel's |
| the Lua state and its pool | `RUN.L` over moycore's chunk pool, in `heap_caps` memory | leaked if a cart is running: nothing calls `close()` | `close()` before the sweep frees every chunk (`pool_release`) | 4 |
| the fold latch | `native/moy_flush/moy_fold.c`'s `moy_fold`: pointers into the fold scratch (a glass row, `device/moycore_glue.py`), the palette and the game canvas, which the core-0 feeder reads band by band; a snapshot copy may be in flight | the feeder drained, the snapshot fenced and the latches cleared by the kernel before the sweep (`moy_glass_vm_stop`, sprint 3 pass 1) | the same; the kernel owns the next present | 0; 3 (landed, pass 1) |
| async copies | `native/moy_gfx/modmoy_gfx.c`'s `moy_gfx_mcp`; P4: `native/p4/moy_ppa/modmoy_ppa.c`'s transactions, bounce worker and snapshot | waited out by the kernel before the sweep (`moy_glass_vm_stop`) | the same | 3 (landed, pass 1) |
| ***`heap_caps` memory owned by Python objects*** | | | | |
| the glass's buffer rows (once the `moy_alloc` registry) | `native/moy_glass`: every off-heap buffer a canvas holds -- layers, the pool, bakes, scratches, the run canvas, window and paint buffers -- is a BUF row; `runtime/moybuf.py`'s caches stay `moy_alloc`'s | a row Python holds goes back when its Buf is finalised in the sweep, and the kernel ends every lifetime the VM named after it (`moy_glass_vm_swept`); the pool is the kernel's, bounded per board | the same; `moy_alloc`'s remaining entries freed after the sweep | 0; 3 (the glass, pass 1) |
| `moy_alloc.malloc_dma` | the P4 paint and scratch buffers before sprint 3 | deleted from every caller: the rotated compositor's buffers are glass rows the kernel holds | -- | 3 (landed, pass 1) |
| wasm linear memory and the runtime pool | `modmoy_wasm.c`, per session | freed by `wasm_end` | `close()` first | 4 |
| driver buffers | the panel framebuffers (`s_fbs` in `moy_lcd`/`moy_axs`), the flush bounce slots, the audio bank and PCM ring, the SD bounce, `moy_prof`'s ring | C-owned, allocated once | kept: the kernel's | — |
| ***Port state only the soft reset handles*** | | | | |
| the native-code arena | `esp_native_code_free_all`, in `MALLOC_CAP_EXEC` (internal) memory; extern by `patches/esp32_native_code_free.patch` for `moy_gfx.native_code_free_all` | freed after the sweep | the same | 0 |
| the first heap area | `mp_task` allocates it before `soft_reset:` | reused, never freed | freed with the VM, allocated at start | 0 |
| the VM's task | `mp_task` never returns; `mp_thread_init` binds thread 0 to it | lives forever | created at start, deleted at stop (its stack goes back to internal SRAM, §4.6); `mp_thread_init` at every start | 0 |
| mounts and SD | `mp_init` empties the mount table; the internal flash is the kernel's littlefs instance (`native/moy_store/moy_kvfs.c`, 2026-10-08), a `KVfs` each VM's start mounts at "/" (`docs/kernel_survival_2026-10.md` §6.6). Guition S3: the store's card volume over `moy_sd.open` (`moy_runtime.tf_card`, mounted by `device/card_store.py`; the bus is never torn down). T-Deck: `moy_sd` stays attached (`init` is idempotent) | remounted | the same until the gate is native; a failed Guition remount stays failed until a reboot (its README) | 1b (the card), 3 (internal flash) |
| WiFi and its leases | the port never deinitialises the WLAN driver; the lease table (`runtime/moy_spine.py`'s `Leases`) is Python | the radio left as it was | refused while a lease is held | 2 |
| an OTA write | `device/moy_ota.py` streams into the inactive slot | — | refused while it runs | 2 |
| ***Native tasks*** | | | | |
| the flush feeder, the audio core-1 task, `moy_prof`'s timer; P4: `moy_c6`'s TX task, `moy_ble_hid`'s queue | `native/moy_flush/`, `native/moy_audio/`, `native/moy_prof/`, `native/p4/` | no VM calls; they survive | kept; the audio task silences the cart's sound | 3 |

**The order of a stop** follows from the table: the app's `close()`; the
cooperative stops (threads joined, moycore's `close()`, the feeder drained and
the fold disarmed, async copies waited); the port's deinit list, with the
pin-wide sweep replaced by the Python-handler one; `gc_sweep_all`, whose
finalisers close files, sockets, I2S and SD; the `moy_alloc` registry freed; the
native-code arena freed; `mp_deinit`; the root section zeroed; the first area
freed; `mp_main_task_handle` handed to the kernel's task; the VM's task deleted.

**Embed, not fork (owner, 2026-10-05).** MicroPython
becomes a service the kernel starts and stops on a task of its own, inside the
esp32 port's build: the port's `MICROPY_ESP_IDF_ENTRY` override
(`mpconfigport.h`) renames its `app_main`, the kernel's entry runs instead, and
the kernel's VM service calls `gc_init` and `mp_init` and runs the order above
itself, over the port's extern deinit functions. The literal `ports/embed` is
not it: it builds the core without the port's modules (`machine`, `network`,
`bluetooth`, `espnow`), which the console's Python uses until sprints 2 and 3
move what it needs. The case:

- **A tag bump.** A fork is a patch over `mp_task`'s body, and the soft-reset
  list is where a port release adds a peripheral's teardown: a multi-hunk
  rebase on every bump. The embed adds no patch (`esp_native_code_free_all` is
  extern already) and links against the port's functions, so a rename fails the
  build. What it could miss is a new teardown step, so it carries a guard: the
  build compares `mp_task`'s call list at the pinned `MPY_TAG` with the one the
  VM service records, and fails on a difference. The bump's review is that diff.
  v1.29.0 is the case (#224's rehearsal): its port drops
  `machine_timer_deinit_all`, and a timer is released only by its finaliser,
  inside the sweep, so the stop stops timers itself before the sweep.
- **Teardown.** The fork inherits a list that is wrong for a stop with the
  kernel up: `machine_pins_deinit` removes the kernel's GPIO ISRs with Python's,
  and nothing drains the feeder, frees the registry, zeroes the roots or frees
  the first area. The patch would rewrite the list anyway, inside a task that
  never dies, so the VM's stack never returns to internal SRAM.
- **The inversion.** §4.2 has the native loop own the frame with Python as an
  upcall. The embed is that shape on the device: the kernel's task owns the
  board and starts the VM. A fork keeps MicroPython's task as the board's main
  task, with the kernel running inside it.
- **The other tiers need nothing from either.** The browser's VM lives as long
  as its worker (`firmware/web_runner/worker.js` loads it), and unix MicroPython
  and the CPython host never stop one. The zero-upcall count (§4.2) is the
  contract every tier shares.
- **The five targets** are all esp32-port builds. Once the kernel owns the loop
  (sprint 3) it is the entry on every one, whether or not that board ever stops
  its VM, and a board takes the VM service in board.toml as it takes `moy_wasm`.

Sprint 0's spike is built in this shape, so it tests the decision as well as
the stop.

Unix MicroPython has none of `heap_caps`, the native-code arena or IRQs, so it
tests VM re-initialisation and the zero-upcall contract, not the stop's memory.
The stop spike runs on an S3 first.

An app that is stopped comes back from persisted state: `close()` is the leaving
hook, `commit()` the forced save, `open()` re-enters (`docs/app_api_v1.md`).
Sprint 5 adds the contract that `open()` after a stop restores what the user
left, and a test holding every shipped app to it.

No interim memory mechanism is built ahead of this. A release protocol, a
soft-reset backstop and heap arenas were drafted and dropped (owner,
2026-09-27): each is work the VM stop undoes.

### 4.5 The toolkit

The toolkit goes native on a third trigger (owner, 2026-09-27): **one
implementation for every app runtime**, so a Python app and a wasm app draw the
same button through the same code. It is shaped for it: immediate mode with no
retained tree (L7 stands), style as data (`DEFAULT_SPECS` / `DEFAULT_METRICS`,
skins as deltas, `runtime/skin.py` unchanged), geometry pure and separate from
drawing, drawing through canvas verbs that are already C. The cost is iteration
speed on widgets; style changes stay free. The theme data the toolkit consumes,
and which theming pieces are built ahead of this sprint, are
`docs/theming_2026-09.md` §8. The net is the pixel goldens on the
rows that exercise the toolkit — not the 320×240/1× row
(`.claude/rules/rendering.md`).

### 4.6 Internal SRAM

Moving objects out of the PSRAM GC heap into native code can be a **net internal
SRAM loss**: native `.bss`/`.data`, task stacks and small `malloc`s land in
internal DRAM, and IDF sends small allocations internal-first. The S3's budget
is tiny and shared with WiFi, BLE and the display's DMA (sprint 0's baseline,
#224: with WiFi and BLE up the T-Deck's DMA-capable low-water is the tightest
figure on either board; WiFi does not give its share back, #158).
moycore's Lua allocator uses internal SRAM above a floor and falls back to PSRAM
below it at a measured cost (`native/moycore/modmoycore.c`). So:

- **Rule:** kernel data is allocated with `heap_caps(MALLOC_CAP_SPIRAM)` unless
  it is latency-bound, and a test asserts it.
- **Gate:** every sprint records internal free and low-water on both S3 boards
  with WiFi and BLE up, against sprint 0's baseline and within the kernel's
  internal share (§6.1).

## 5. The kernel's language

**The kernel is written in C (owner, 2026-10-06, #224).** #224 chose C on
2026-09-26; rev 1 of this doc reopened it for a spike, because the kernel's
scope had grown from a thin layer to tens of thousands of lines of logic and
concurrency (§2.2). Sprint 1a ran the spike on the store's index, written in both
languages and measured on every target. The evidence is #224's: the C twin
(comment 6005099497), the Rust probe (6003372477) and the side-by-side
(6007533482). The reasons:

- **The S3 needs a forked compiler.** On Xtensa, Rust builds only with
  Espressif's forked nightly. That compiler cannot use the S3's zero-overhead
  loop, so the hot path (`intern`) ran slower than C's, and forcing the loop
  crashes the fork.
- **Rust's bundled runtime shadows the board's C library without a word.** The
  `compiler_builtins` and compiler-rt in a Rust static library define the names
  of libc and libm, and the link takes them silently. On the P4 they fail to link
  at all, on the float ABI.
- **Rust doubled the code size of a small component.**
- **The kernel is mostly glue to C** (ESP-IDF, libmoy, MicroPython, WAMR), so
  the logic Rust would make safe is the smaller part of it.

The P4's RISC-V side worked with upstream Rust; the S3 is where it did not.

**What C gives up.** Safe Rust rules out use-after-free, buffer overflows, null
dereferences and data races by construction on every path, where sanitizers and
fuzzing find them on the paths tests run; Google's Android report (2025) puts
Rust's memory-safety bug density about 1000× below C and C++'s. The kernel is
concurrent across cores (`native/moy_flush/moy_flush.c`: "every clause was a race
once") and most of this repo's code is written by agents. A panic still takes
the board down, and `unsafe` can still corrupt memory, so Rust is not free
either. The decision makes containment carry the weight.

**Containment is mandatory, in any language** (#224): the host builds under
AddressSanitizer and UndefinedBehaviorSanitizer in CI; fuzzing for every parser
of untrusted bytes; a small kernel; a crash-only kernel that records its reason,
reboots to the native recovery screen or the launcher, and reports over the dev
channel; and handles instead of pointers (§4.3). The store's index is the first
component under it, and the spine the second: `tools/moy_index_spike.py` runs
each one's suite and API fuzz under both sanitizers.

**Revisited, never assumed.** Rust is considered again per subsystem when
upstream LLVM's Xtensa support matures, so that the S3 builds with a stock
compiler, or for a self-contained core behind a narrow C interface. The bar is
the six items the spike held both languages to:

1. link into all five firmware targets (four consoles and the Zero);
2. link into the web runner under the pinned emscripten;
3. load on the host through ctypes and in the unix MicroPython build;
4. report each image's delta against the C twin, counting the web bundle, which
   rides every board's image (`.claude/rules/web.md`), so a kernel's wasm build
   is charged to every board a second time;
5. bench unaligned loads and the instruction-cache miss rate (`perfcnt`) on the
   hottest path on the S3, with no regression on sprint 0's baseline;
6. run under CI in the time preflight allows.

The Rust twin of the store's index, its toolchain pins and its link guard were
deleted when the decision was taken. `experiments/rust_probe/` stays as the
record of what a Rust build of each target needs; #224 holds the numbers.

## 6. The sprints

Each sprint moves one set of components, deletes the Python it replaces, passes
its gate on the host, the browser and every target that takes it, and re-takes
sprint 0's baseline. **Every sprint's gate also includes:** internal SRAM free
and low-water on both S3 boards with WiFi and BLE up (§4.6); each image's
headroom above its floor (§6.1); the semantic traces extended
before anything crosses.

**The order (owner, 2026-10-06):** sprint 0, 1a, then **sprint 2, the spine,
before 1b**, then 1b (the native store), 3 and 4, then 5–7 or the narrowed scope
(§10 question 8, decided before sprint 5). The table below follows it, and each
sprint still opens with its carve. Each sprint also designs in the open issues
whose features land in its subsystems (§6.2), so that nothing is built in Python
and then ported.

**Every sprint opens with a carve, in Python, before any native code is
written** (owner, 2026-10-05):

1. Each module §2.2.1 marks split along this sprint's line is split into files,
   so the crossing replaces whole files rather than operating inside one.
2. The components that cross take the interface the native code will expose —
   handles, not objects (§4.3), the native API's call shapes and errors — and
   their callers move to it.
3. The semantic traces and host tests pin that interface.

The crossing then swaps the implementation under unchanged tests and traces. A
carve changes no behaviour, so it lands on dev on its own with the goldens and
the on-glass suites untouched, and its iterations are host test runs, not
firmware builds. It runs one sprint ahead, never further: sprint 5 redesigns the
roles, and sprints 6 and 7 cross what sprint 5 reshapes. No general clean-up
precedes the sprints, because each sprint deletes the Python it replaces. The
store's index took its native interface from the 1b carve before sprint 1a
wrote it, so the language spike ran against pinned tests.

**From sprint 4 the carve is no longer a phase of its own** (owner,
2026-10-07). The pass that crosses a subsystem pins its traces first and
splits a file only where two agents running in parallel would otherwise edit
it; it writes no Python twin in the native call shapes beyond the reference
the host already needs. Sprint 3's carve had landed whole by then
(`docs/kernel_survival_2026-10.md` §2).

| sprint | moves | gate |
|---|---|---|
| **0 — evidence** | nothing. Meters: a `heapcaps` dev-channel word (PSRAM and internal, free and largest) and a GC-pause field in PERF. The census by owner (live GC bytes, held areas, `heap_caps` bytes) with `_LAYER_POOL` checked first. Doom's fit over ≥5 boots per S3 board. Launcher import + construction time on both S3 boards. The complete placement table (§2.2). The stop inventory (§4.4) and the embed-vs-port-fork decision. A stop/start spike on an S3 that keeps one real peripheral alive across the stop — the flush task and a C-owned touch poll. The P4 repartition costed. | the census names the owners of the boot peak and of the T-Deck's retained memory; 100 stop/start cycles on the S3 with the peripheral alive and `heap_caps` free flat; a cart-available PSRAM threshold and the kernel's fixed share defined from the census; per-board headroom floors set (§6.1) |
| **1a — the language** | the smallest store component, the store's index (`runtime/moy_index.py`: the handle table every call that names a cart validates against, no I/O), written in C and in Rust as the language spike (§5) | §5's six items; the owner's decision (C, 2026-10-06) |
| **2 — the spine** | handle tables; the crash record and strike ledger (`crash_guard`); the native recovery screen; the settings store; WiFi leases | a native crash is recorded and shown after reboot; a VM that fails to start lands on the recovery screen; a stale handle is refused loudly |
| **1b — the store** | the store's index, catalogue build, cover bookkeeping, seed and project loading; the journal; last, the card volume the kernel owns, with the read cache on every console board's card (the SD gate for the card, owner, 2026-10-06; `docs/kernel_store_2026-10.md` slice 8) | the parity tests across bindings; the heap after boot within §6.1's bound; Doom loads on a fresh Guition S3 over five boots |
| **3 — the survival set** | input (touch, keyboards, BLE HID below `bluetooth`); audio (I2S feed and the sfx/music semantics); the glass (canvas ownership, present, compositors); the storage gate for the internal flash volumes (the card's is 1b's); the frame tail (loop, pump, idle blank, OTA health, PERF, serial); radios, the webhost and the sync RPC | the native loop drives the frame on every tier with Python as an upcall (§4.2); each board's on-glass suite unchanged; `surface_model_v1.md`'s amendment (§8) landed first |
| **4 — the cart path** | the Player's loop and tick model, the runtime map, moycore's glue, the in-cart chrome (strip, system menu, error and fit panels, toasts), netplay lockstep, the Lua superset rulings (§2.3) | a Lua and a wasm cart run launch to exit with **zero Python upcalls** on every tier and **with the VM stopped** on the S3s; cart-available PSRAM at or above §6.1's threshold; Doom loads on a T-Deck after a scripted session and on a fresh Guition S3; the cart census of VM-free carts; exit-to-launcher time against the return budget decides the launcher (§3) |
| **5 — the ABI** (scope: §10 question 8) | the roles redesigned import-shaped (§2.1); `ctx.shell` closed; the Python binding thin; the wasm import adapter; the `open()`-after-stop contract | `docs/app_api_v1.md` rewritten in place; `tests/test_app_context.py` and the traces cover every role; a wasm module reaches a role through an import; every shipped app restores after a stop |
| **6 — the toolkit** (scope: §10 question 8) | `runtime/ui.py`'s core and one text path for every runtime | pixel goldens identical on every row that exercises the toolkit; an A/B of widget-heavy frames on an S3 and a P4; a wasm app draws a button and a scroll list |
| **7 — the window managers** (scope: §10 question 8) | the WMs' state, policy and chrome; the rest of `runtime/console.py` | the P4 desk's on-glass suites unchanged; an A/B of chrome frames on an S3 with the dev channel's timing; `runtime/console.py` is gone |

Sprint 4 is where the memory half's acceptance test is met and the cart path
runs VM-free. Sprints 5 to 7 finish the line as far as the scope the owner
chooses before sprint 5 takes them (§10 question 8).

**The Studio's Run pane is sprint 7's concern.** `docs/studio_2026-09.md` docks
the playtest window into a pane the Editor lends, which is new policy in
`runtime/wm_windowed.py`. It lands before sprint 7 starts, so the port carries
it, or it is designed into sprint 7; it never lands during sprint 6 or 7, whose
gates hold pixels (`docs/theming_2026-09.md` §8 sets the same rule for
theming). The Player in the pane is the same Player, so sprint 4's gate is
unaffected.

### 6.1 The values sprint 0 set (2026-10-05)

Configuration, derived from #224's measurements; the derivation, and where each
board stands against each value, are #224's.

- **Cart-available PSRAM** (§1.1): at the Player's fit check, the worst of at
  least five boots, fresh and after the census's scripted session
  (`tools/mem_census.py`), on each S3 board: **at least 4 MiB free and a 3 MiB
  largest block**. Doom's footprint is the floor. The margin above it is one
  area of the Python heap at its boot size (about 1 MiB), the step by which
  free PSRAM at the fit check moved between boots once the catalogue fix
  landed, so one growth of the heap before a cart starts cannot refuse Doom;
  rounded up to whole MiB.
- **The kernel's fixed share.** PSRAM: native code holds **at most 1 MiB** at
  the Player's fit check on each S3 board — panel buffers, flush slots, audio,
  driver buffers, the game canvas, the layer pool once it is the kernel's, the
  kernel's own tables — so that with the cart threshold it leaves 3 MiB for the
  Python heap and what Python holds while the VM runs. The C side the census
  measured fits it on both boards; on the Guition S3 what is left is about one pooled 320×240
  layer, which bounds the pool sprint 3 makes the kernel's. Internal SRAM: the
  kernel costs **at most 4 KiB net** against sprint 0's baseline (§4.6: free,
  largest block and low-water, all-internal and DMA-capable, WiFi and BLE up)
  while the VM runs, and **no more than the loop task's stack net** while it is
  stopped, when the VM task's stack is back (`moy_loop_task`, which drives the
  kernel's frame while no VM runs, exists only then: it is created when the
  VM's teardown opens the window and deletes itself when the next VM is up,
  docs/kernel_survival_2026-10.md §7.1). Kernel data is PSRAM by rule, and the T-Deck's DMA-capable
  low-water with both radios up is the tightest figure on either board.
- **Image headroom floors**, the OTA-slot headroom `build.sh` prints with the
  browser console baked in, at every sprint's gate:

  | image | floor | why |
  |---|---|---|
  | Waveshare P4, Guition P4 | 1 MiB | the 6 MiB slots (owner, 2026-10-05; `f738797`) were cut for the kernel; the floor leaves it about half of their headroom, so the store never pays for a second table change |
  | T-Deck, Guition S3 | 512 KiB | no table change is planned; the floor leaves the kernel about two-thirds of their headroom and keeps the build's own warning (`MOYBYTE_APP_HEADROOM_WARN_BYTES`, 200 KB, #168) over twice |
  | Zero | 256 KiB | the baked web bundle took it under the floor, and the 3 MiB slots (owner, 2026-10-07) re-tabled it rather than take the bundle off its image; they leave the kernel's native modules (`docs/kernel_survival_2026-10.md` §6.5) about the floor again, out of an 8 MB flash whose store pays for every slot byte twice |

- **The heap after boot** (sprint 1b's gate): the GC heap the boot leaves at the
  launcher holds at most the five areas the catalogue's boot leaves (dev
  `39b7186`), on each console board with the census's stores.

The Guition S3 takes the same values. Its figures on its card after the
catalogue fix, fresh and after the scripted session, are #224's, and the
values hold against them.

### 6.2 The open issues each sprint absorbs (owner, 2026-10-06)

An open issue whose feature lands in a subsystem a sprint moves is designed into
that sprint, natively, against the kernel's interface; nothing is built in
Python first and ported after. The sprint's carve names it.

| sprint | subsystem | open issues |
|---|---|---|
| **2 — the spine** | the crash record and strike ledger; the recovery screen; the settings store | #160 the crash-loop guard (a bad wallpaper cart at boot); #143 friendly failure (the recovery screen's words); #131's parental controls (one `gate(action)` check the store, Settings and Get Carts call, the PIN stored hashed in the settings rows) |
| **1b — the store** | cart identity, profiles, the journal, export, sharing, the on-card layout, the card volume | #162 namespaced `<author>.<title>` ids; #131 multi-kid profiles (the kid folders and the family shelf as roots, the owner field, saves keyed by cart id); #136 the version time machine (reads the journal); #127 project backup, export and import; #122 online sharing, with #125 the publish flow, #123 the gallery index and #195 the browser's play-and-remix leg; #235 store aging and a sharded `/moy/carts` |
| **3 — the survival set** | input | #26 BLE keyboard, mouse and joypad; #83 the P4's USB HID host; #196 the T-Deck's always-raw keyboard |
| | audio | #82 the P4's ES8311 codec; #70 sound packs (a PCM sample voice, numbered slots) |
| | the frame tail and the glass | #130 power management (idle dim and sleep, low battery); #126 on-device capture (screenshot, GIF); #226's idle screensaver |
| **4 — the cart path** | the Player, the runtime map, the cart's verbs and chrome | #133 the cart-facing network verb, with #134 online leaderboards on it; #212 debug mode and the runaway watchdog; #228 3D for carts (`docs/engine3d_2026-10.md`); #7 local sharing over ESP-NOW; #227 native carts per chip; #231 the file picker for compiled carts; #192 the C play path (moycore's glue); #226's time verb; #143's error and fit panels |
| **6–7 — the toolkit and the window managers** | the toolkit, the WMs and their chrome | #135 localization glyphs and strings; theming (`docs/theming_2026-09.md`); the Studio's docking (`docs/studio_2026-09.md`); the charm issues #140 (tracker), #141, #142, #144–#146 and #148; #177 hover feedback; #132 the system clipboard; #129 first-boot setup |

Trackers (#66, #95, #97–#99, #105) and issues about apps alone are not listed.

## 7. What it costs

- **Size.** About 45,000 lines of Python become native code (§3), crossed
  sprint by sprint; each sprint's gate sets the pace, and this doc makes no
  calendar estimate. The apps (~23,000) do not.
- **Crash behaviour.** A kernel bug is a reset with no message today
  (`docs/history/moycore_plan_2026-08.md`). §5's containment and sprint 2's
  recovery floor are part of this direction, not optional.
- **Development speed.** A kernel change needs a firmware build. What changes
  often — the apps — stays Python.
- **Flash.** **ESTIMATED** (the performance review): ~18,500 lines of Python
  become roughly 0.5–1 MB of native text against roughly 0.35–0.5 MB of frozen
  bytecode deleted, net +0.15–0.5 MB for the `runtime/` share alone — and the
  kernel's wasm build rides every image again inside the web bundle. The P4
  boards were repartitioned to 6 MiB app slots for it (owner, 2026-10-05;
  `f738797`, `f596ed5`), paid from the store. A partition change is a cable
  flash — OTA writes only the next app slot — and `tools/board_flash.py`
  formats a store the new table moved, so the store's contents go
  (`firmware/seeed_xiao_esp32s3_zero/README.md`): cheap while the field is the
  owner's desk, expensive after. Every sprint gates on §6.1's floors.
- **Internal SRAM** (§4.6), gated every sprint.
- **A second public contract.** The roles become an ABI for Python and wasm
  apps, under a trace pin.

## 8. Relation to decisions already made, and the sentences this falsifies

- **#224.** This doc is its deliverable. It keeps #224's direction, containment
  and language (C, confirmed by the spike, §5), moves the toolkit from "last, or
  never" into sprint 6, and replaces its crossing order (§6).
- **`docs/wasm_tier_plan_2026-09.md`.** The reserve and the flash partition stay;
  raising what a board can fit is this work. A cart above the floor is still
  refused with a notice where it does not fit.
- **`docs/surface_model_v1.md`** is LOCKED and a change to it is a change to that
  file first. The contract is language-neutral, but the doc names
  `runtime/surface.py` as the Surface's home and its dirty signals are
  producer-owned Python counters. **Amendment before sprint 3:** the Surface and
  its dirty protocol are kernel-owned, producers signal through the kernel's
  handles, and the laws and the §4 compositor contract are unchanged. Its §8
  graveyard stands, why-not-LVGL included: the kernel is our own code built for
  the host, with no widget tree (L7).
- **`ui_damage_model_v1.md` §0.065.** Cited for its split (§1.8), not for a
  "no".
- **`shell_architecture_v1.md`** §2.3's recovery floor becomes sprint 2's; §3.4's
  one VM, one active cart is kept.
- **`shell_os_architecture_v1.md` §6.** "No per-surface heaps" stands; arenas
  were dropped (§4.4).

Every sentence this doc makes false is corrected IN PLACE by the sprint that
makes it false, not annotated:

| sentence | where | corrected by |
|---|---|---|
| "MicroPython is the shell and is not the engine" | `docs/moycore_direction.md` §1 | sprint 4 |
| the C-layers decision ("a second console in C") | `docs/moycore_direction.md` §3 | sprint 4 |
| "Presentation stays per-board and outside moycore" | `docs/moycore_direction.md` §3 | stands — the kernel is not moycore; re-read at sprint 3 |
| `console.py` as the shell kernel | `.claude/rules/shell.md` | sprint 7 |
| the Surface's home and producer-owned dirty signals | `docs/surface_model_v1.md` §2–§3 | the amendment, before sprint 3 |
| the roles as object-returning methods | `docs/app_api_v1.md` | sprint 5 |
| the heap "never gives an area back" (false as mechanism: §1.3) | `tools/esp32_build_lib.sh`, the split reserve's comment | the next change that touches the file — a comment edit alone would run the firmware build |

## 9. Non-goals

- Moving the shipped apps, the editors or their Layout classes out of Python.
- A second Python VM, concurrent VMs, or a scheduler.
- Any change to the cart API or the compiled tier's import table.
- Raising `MOYBYTE_GC_SPLIT_RESERVE`, or executing module text from a flash
  partition (both declined 2026-09-26, `docs/wasm_tier_plan_2026-09.md`).
- Store capacity on the Guition P4 (#158's question).
- A retained widget tree, in any language.
- Getting wasm apps onto a board (the tier plan's phase 5).
- Rewriting C drivers or vendored C in the kernel's language.

## 10. Open questions (bounded)

1. **The return budget.** Nothing reboots, but exit-to-launcher means starting a
   VM and rebuilding a Python launcher. How long may it take? The owner leans to
   a native launcher and sets the budget when sprint 4 can measure a Python
   launcher's return against it (owner, 2026-10-05); sprint 0's figures are
   #224's.
2. **Embed or fork the port's `main.c`** for an in-process VM stop. Embed (owner,
   2026-10-05): §4.4 has the case, #224 the spike's runs of both and their
   MicroPython v1.29.0 rehearsal.
3. **The Lua superset rulings**, name by name (§2.3). Sprint 4.
4. **`_LAYER_POOL`.** Sprint 0's census names it as most of the T-Deck's
   retained memory; its retention waits for sprint 3, where the pool becomes
   kernel-owned (owner, 2026-10-05).
5. **The desk.** Whether the P4 ever stops its VM with apps open.
6. **Text.** One text path for every app runtime; a fixed 8×8 font will not be
   enough for wasm apps (#158's e-reader case). Sprint 6.
7. **The open placements** of §2.2.1: the rows whose sprint reads `open`, each
   with its question in its note. Each is answered before its group's sprint
   starts.
8. **The scope after sprint 4.** Sprints 2, 1b, 3 and 4 run as §6 has them
   (owner, 2026-10-05; the order, 2026-10-06). **Before sprint 5 starts**, the owner chooses between:
   - **the full plan**: sprints 5–7 as §6 has them;
   - **a narrowed plan**: sprint 5 limited to the `open()`-after-stop contract
     every Python app needs; the wasm app ABI and sprints 6–7 deferred until
     wasm apps need them. The app world stays Python, and only the cart path
     runs VM-free.

## 11. What can kill it

- **A stop failing with a real peripheral alive.** Sprint 0's spike passed on
  the Guition S3 (2026-10-05, #224): 100 stops in one boot with the flush task
  and a C-owned touch poll alive, memory flat, in both lifecycles. The T-Deck's
  input task and shared bus run the spike before sprint 4 relies on stops
  (#224). If a stop fails there, the invisible stop dies; the fallback is a soft
  reset into a Player-only VM before a big cart, built only then.
- **The census naming something cheaper — it did, for the boot peak.** The rule
  was: if the boot peak or the retained memory is one fixable owner, fix it, and
  the kernel then has to earn itself on its other grounds. The boot peak had one
  owner, the shelf scan, and it is fixed in Python: the scan builds the slim
  catalogue directly (dev `39b7186`) and frozen modules come first on
  `sys.path` (`4d85b00`). The retained memory's main owner, `_LAYER_POOL`,
  waits for sprint 3, where the pool becomes the kernel's (owner, 2026-10-05).
  So the kernel proceeds on its other grounds (§0): a restartable VM with a
  clean heap per app; crash containment; one app API for Python and wasm apps;
  one toolkit; and the S3's chrome speed, with latency-bound state in internal
  SRAM. On those grounds the owner runs sprints 2, 1b, 3 and 4 as planned and
  chooses the scope after them before sprint 5 (2026-10-05, the order
  2026-10-06; §10 question 8).
- **Internal SRAM.** If moving state native costs the S3 internal SRAM it cannot
  spare, and PSRAM placement cannot absorb it (the share, §6.1).
- **Flash headroom** that forces a repartition after there are users (the
  floors, §6.1).
- **A crossing without its pin.**

## 12. Review ledger

Rev 1 → rev 2, 2026-09-27. A = architecture pass (REWORK), P = performance and
hardware pass (STANDS WITH FIXES).

| # | finding | verdict | changed |
|---|---|---|---|
| A1, P1 | sprint 3 ("cart path") could not run VM-free: touch, BLE keyboard, audio, present, the frame tail, the SD gate a wasm `read` calls, the dev channel and PERF were all sprint 4 | accepted | survival set is sprint 3, cart path sprint 4 (§6) |
| A2 | two inverted control flows (device loop native, host and web Python-driven) break host == device | accepted | §4.2: one inversion on every tier; VM stop device-only; the zero-upcall contract tested everywhere |
| A3 | the superset is a DENY-list registration, far wider than three verbs; `open_editor` keeps the VM live; netplay runs in Python | accepted | §2.3: every name ruled; cart census gate |
| A4 | many modules unplaced; the Zero is a fifth target | accepted | §2.2 grouping, full table a sprint 0 deliverable; Zero in every link gate |
| A5 | size understated ~2× (no `device/`, no services); the driver non-goal contradicted A1 | accepted | §3 and §7 recounted; non-goal says C drivers |
| A6 | the roles are object-shaped; this is an API redesign | accepted | §2.1 sketch; sprint 5 rewrites `app_api_v1.md` |
| A7 | sprint 1 too big; its Doom gate assumed the census's answer | accepted | 1a spike / 1b store; Doom gate conditional |
| A8, P6 | the stop spike could not test its own kill criterion; embedding, IRQs, C statics, the native-code arena and the first area unaddressed; unix cannot test heap_caps | accepted | §4.4 inventory; spike on an S3 with a real peripheral alive |
| A9 | the web bundle charges the kernel's wasm build to every board | accepted | §5 item 4, §7 |
| A10 | no recovery floor if the VM cannot start; strike ledger unscheduled | accepted | sprint 2 |
| A11 | `surface_model_v1.md` needs an amendment, not "unchanged" | accepted | §8 amendment before sprint 3 |
| A12 | the 1.2 µs mis-sourced; measurements embedded | accepted | §1.8 sources it; figures cited to issues |
| A13 | falsified sentences unlisted | accepted | §8 table |
| P2 | "within ~30 KB" hid the sign: Doom is still 8–32 KB over, inside run-to-run noise | accepted | §1.1 |
| P3 | internal SRAM unaddressed; moving objects native is a net internal loss | accepted | §4.6 rule and per-sprint gate |
| P4 | `_LAYER_POOL` is a named candidate for the ~200 KB, and a stop would leak it | accepted | §1.4, sprint 0, §10.4 |
| P5 | "the kernel's fixed share" undefined; largest block lower than total | accepted | threshold and share defined by the census; metric counts largest block |
| P7 | return-to-launcher time unmeasured | accepted | sprint 0 times it |
| P8 | §1.8 misread §0.065: Python was ~55% of that chrome frame | accepted | §1.8 corrected |
| P9 | Xtensa misaligned access is measured, not hypothetical; flash vs IRAM; icache; Python→kernel calls still cost | accepted | §4.1, §5 item 5, §1.8 |
| P10 | P4 flash headroom at risk early | accepted | §7 estimate; per-board floors; repartition costed in sprint 0 |
| P11 | several gates had no meter or threshold | accepted | `heapcaps` word and GC-pause field in sprint 0; thresholds from the census |
| P12 | growth wording wrong | accepted | §1.3 |

The evidence and language-and-build passes were stopped early (owner,
2026-09-27); sprint 1a ran the language pass in executable form, and §5 records
its decision.
