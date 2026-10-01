# The native kernel — the OS below the app verb table, Python as an app runtime (2026-09)

**Status: DRAFT rev 2, 2026-09-27.** The direction doc #224 asks for as its
first deliverable. Rev 1 went through the adversarial architecture and
performance/hardware passes the same day (verdicts: **REWORK** / **PERF CASE
STANDS WITH FIXES**); every finding is folded into this revision, and **§12 is
the finding-by-finding ledger**. Nothing here is scheduled until the owner
accepts it. It reverses a standing sentence — "MicroPython is the shell"
(`docs/moycore_direction.md` §1) — which is why it is settled on paper first.
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
table (§3). The kernel's language is decided by a spike: Rust if it passes, C if
it does not (§5).

**The goal that forces it is memory for carts.** A compiled cart's memory is the
cart-runtime reserve, not free PSRAM (`docs/wasm_tier_plan_2026-09.md`), and on
the S3 boards that reserve is what the Python heap leaves. The heap after boot
holds several times its live set at the launcher (MEASURED, Guition S3, #158:
about 4 MB grown for about 0.7 MB live) — free space the collector cannot hand
back while any object pins an area. When the OS no longer lives in that heap, the
VM runs only while a Python app or cart does, and stopping it hands every grown
area back (SOURCE, §1.3).

**MicroPython stays, in its right place.** It is why a kid can write an app in
200 lines with no build (`system_carts/notes.moy`), why the editors iterate fast,
and the language most of the console's carts are written in. What this doc ends
is its role as the base a constrained OS stands on.

## 1. The problem, in evidence

### 1.1 The acceptance test: Doom, fresh and after a session

**MEASURED, #158 (2026-09-26).** Doom's load footprint
(`native/moy_wasm/moy_wasm_footprint.h`) against each board's free PSRAM when
the Player starts it: it runs on the Waveshare P4; it runs on a freshly booted
T-Deck and is refused after a session, which leaves about 200 KB less PSRAM free
with the largest block unchanged; it is refused on a freshly booted Guition S3,
the floor board. (The Guition P4's refusal is its store's size, and #158's
question.)

**ESTIMATED: still refused on the floor board after the compiler fix `14241a8`.**
That fix shrank Doom's S3 module to 789 KB. The footprint is the file block (26
pages plus 1/32 plus 1 KB: 1,758,208 B), the pool (256 KB plus a quarter of the
module), the module and the 16 KB stack: 1,758,208 + 464,128 + 807,936 + 16,384
= **3,046,656 B** with 789 KiB (3,022,986 B with 789,000 B). The Guition S3's
last measured fresh-boot free is 3,014,696 B, so Doom is **8–32 KB over** — and
run-to-run spread in free PSRAM is larger than that margin (#158 recorded about
110 KB of spread at the launcher), and the images have changed since. No free
figure is current until sprint 0 re-measures.

Doom is the **minimum**. A bigger zone, a bigger WAD or the next cart needs the
held space. The metric this doc is held to is **cart-available PSRAM at launch**
— free total AND largest block when the Player starts a cart, over at least five
boots, fresh and after a scripted session, on each S3 board — with its threshold
set by the sprint 0 census (§6).

### 1.2 One heap is the system's memory

**SOURCE.** Everything on the Python side shares one VM: the boot spine
(`device/desktop_spine.py`) builds the store, the `Workstation`, the WM and the
frame loop in it; apps are Layer instances in it; Python carts run in it. Lua and
compiled carts run in C, but the loop around them, the drivers they read, the
audio they feed and the storage gate a compiled cart's `read` calls
(`read_on_vm`, `native/moycore/modmoycore.c`) are Python. The C side — linear
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

**MEASURED.** The S3 boards' `mpconfigboard.h` records the launcher's boot heap
growing with the number of carts on the card (#66); #158 measured the held-to-
live ratio above, and that a 4 MB reserve held the heap lower with all 51 carts
still loading. On 2026-09-02 one fragmented Python allocation on an 8 MB S3 took
every remaining byte of PSRAM until a hard reset (#66). The reserve
(`MOYBYTE_GC_SPLIT_RESERVE`) and the flash partition stay as they are
(`docs/wasm_tier_plan_2026-09.md`, 2026-09-26).

### 1.4 Retained memory, with a named candidate

**MEASURED, #158:** the T-Deck's ~200 KB after a session is retained, not
fragmented. **SOURCE: `device/device_canvas.py`'s `_LAYER_POOL`** is a candidate
that fits: a dead cart's PSRAM layer buffer (150–384 KB) goes into a pool keyed
by size, and "nothing is ever dropped from the pool". It lives outside the GC
heap, so a VM stop does NOT return it — and a stop that drops the Python dict
holding it would leak it for good. Separately, once WiFi has been up, the S3's
internal SRAM does not come back (#158, phase 1). Sprint 0's census attributes
both before anything is sized.

### 1.5 Long-lived data costs every collect

**MEASURED (#186; `runtime/moybuf.py`'s header).** The mark phase scans every
word of every live block, so collect time grows with live bytes.

### 1.6 The only full reclaim today is a reboot

**SOURCE: `ports/esp32/main.c` at v1.28.0.** The port's soft reset is the only
place that tears down timers, threads (`mp_thread_deinit`), the native-code arena
(`esp_native_code_free_all`), BLE, ESP-NOW, UARTs and pins, then re-runs the
boot. The console's boot repeats what the user watches: the splash, the touch
bring-up, the store scan, the radios.

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

Sprint 0 records a speed baseline and every sprint re-takes it (§6).

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

Everything the console needs while no Python app runs is OS. Sprint 0 delivers
the complete per-module placement of `runtime/` and `device/` as a table in this
doc; the grouping the sprints follow:

| group | modules (not exhaustive) | sprint |
|---|---|---|
| the kernel's spine: routing, back-stack, app registry, settings store, WiFi leases, crash record, strike ledger, recovery screen | `runtime/console.py` (in part), `runtime/crash_guard.py`, `runtime/system_store.py` | 2 |
| input: touch, keyboards, the BLE HID keyboard below `bluetooth` | `device/gt911.py`, `device/gsl3680.py`, `device/axs_touch.py`, `device/ble_keyboard.py` | 3 |
| audio: the I2S feed and the sfx/music semantics the glue drains through `make_api` | `device/device_audio.py`, `device/moycore_glue.py`'s audio half | 3 |
| the glass: canvas ownership, present, compositors | `device/device_canvas.py`, `device/dsi_panel.py`, `device/p4_canvas.py` | 3 |
| storage: the SD gate, the store of record and its journal | the boards' `with_sd`, `runtime/moy_journal.py` | 1b and 3 |
| the frame tail: loop, pump, idle blank, OTA health, PERF, serial | `runtime/device_boot.py`, `runtime/perf_line.py`, `runtime/dev_channel.py`, `device/moy_ota.py` | 3 |
| radios and links: WiFi, ESP-NOW, the C6 updater, the webhost and sync RPC | `device/device_wifi.py`, `device/moy_espnow.py`, `device/moy_c6_update.py`, `device/moy_webhost.py`, `device/moy_webserver.py`, `runtime/moy_sync.py` | 3 |
| the store: index, catalogue, covers, seed, project loading | `runtime/moy_carts.py`, `runtime/cover_cache.py`, `runtime/moy_seed.py`, `runtime/project.py` | 1b |
| the cart path: loop, tick model, runtime map, moycore glue, in-cart chrome, netplay lockstep, notices and toasts over a cart | `runtime/player.py`, `runtime/tick_model.py`, `device/moycore_glue.py`, `runtime/system_menu_ui.py`, `runtime/netplay.py`, the achievements/notify path | 4 |
| the roles and their services | `runtime/app_context.py`, `runtime/system_api.py`, `runtime/artwork.py`, the wallpaper role | 5 |
| the toolkit's core | `runtime/ui.py` | 6 |
| the window managers | `runtime/wm_windowed.py`, `runtime/wm_desk.py`, `runtime/wm_chrome.py`, the rest of `runtime/console.py` | 7 |
| apps (stay Python) | the launcher, Settings, Files, Paint, Calc, Notes, Storybook, Appearance, the Editor and its tabs (the Studio, `docs/studio_2026-09.md`, panes and docking included), `runtime/blocks.py` (the block compiler is an editor's) | — |

The Zero companion board (`firmware/seeed_xiao_esp32s3_zero/`) is a fifth
target: headless, the cart store, running the webhost and the sync RPC. Every
link gate counts it.

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
  `device/` ~14,500, unplaced services ~11,900; sprint 0's placement table
  settles it). The apps are roughly 23,000 more. Rewriting the apps frees no
  memory, because their memory goes when the VM stops.
- **Change rate.** The editors and Paint change most; a Python edit needs no
  firmware build.
- **They keep the ABI honest.** Our apps on the table a kid's app uses is the
  test that table gets.
- **The system-as-carts direction stays open** (#55,
  `docs/shell_architecture_v1.md` §2).

**The launcher is decided by measurement.** Every cart returns to it. A Python
launcher means every return starts the VM, re-imports the launcher and rebuilds
its live set from a 64 KB first area, doubling through collects as it grows.
Nobody has timed that; sprint 0 does, on both S3 boards, before sprint 1
commits. If it misses the return budget (§10), the launcher becomes the one
shipped app that goes native.

**The recovery floor is native.** `docs/shell_architecture_v1.md` §2.3 requires
"a hardcoded minimal recovery UI … a floor that cannot itself fail". A kernel
that reboots into a Python launcher has no floor when the VM cannot start, so a
native recovery screen is sprint 2's.

## 4. The shape

### 4.1 Three layers, two boundaries

    hardware drivers (C, ESP-IDF)  ←→  the kernel (Rust or C)  ←→  libmoy, moycore, WAMR (C)
                                              ↑
                              app runtimes: MicroPython, Lua, wasm

C drivers and vendored code stay C whatever the kernel's language. The repo's
Python drivers (touch, the BLE keyboard, audio) become kernel code (§2.2).

**What the boundaries cost.** A Rust↔C call is a plain call under the C ABI: no
marshalling for `repr(C)` data, the cost of a C call across translation units.
Lost: inlining across the boundary, since ESP-IDF builds C with GCC and Rust uses
LLVM. The rule that makes that harmless is libmoy's — **a hot loop lives on one
side**: the kernel calls a C kernel per rect, sprite or band. Risks the spike
measures rather than assumes (§5):

- **Misaligned access on Xtensa (MEASURED, #158):** Espressif's LLVM Xtensa
  backend — Rust's backend on the S3 — split accesses of unknown alignment into
  byte loads, roughly doubling Jet's raster time until fixed for the wasm
  compilers. A store parser reading `u32`s out of byte buffers is that pattern.
- **Placement:** Rust code lands in flash, where Lua's interpreter hot path is
  placed in IRAM by `MOY_HOT` (`native/moy_lua/lua/luaconf.h`); kernel code
  competes with frozen bytecode and the Lua VM for the S3's instruction cache.
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

A stop is harder than a sweep, and sprint 0 inventories it before anything is
built (SOURCE: `ports/esp32/main.c`, `native/moycore/modmoycore.c`):

- **Everything with a Python callback must be torn down**, or an ISR calls into a
  freed heap: `machine.Timer`s, `bluetooth` IRQ handlers, threads. Keeping BLE up
  across a stop therefore means the HID keyboard lives below `modbluetooth`.
- **C statics survive `mp_deinit`:** moycore's `g_p8mem`/`g_p8rom` (Python
  bytearrays), `g_wread`, the Lua state, five `MP_REGISTER_ROOT_POINTER`s, and
  `moy_alloc` registry entries owned by `moybuf` views. Each is either cleared at
  stop or moved to kernel ownership.
- **The native-code arena** (`esp_native_code_free_all`) is freed only in the
  port's soft reset.
- **The first 64 KB area** is allocated outside the `soft_reset:` label and never
  freed.
- **MicroPython is the port's `main.c` task, not a library.** Stopping and
  restarting it in-process is either an embedding of MicroPython under our own
  task or a fork of the port's `main.c`. Sprint 0 decides which.

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
is tiny and shared with WiFi, BLE and the display's DMA (#158: the T-Deck's
low-water with WiFi and BLE up is a few KB; WiFi does not give its share back).
moycore's Lua allocator uses internal SRAM above a floor and falls back to PSRAM
below it at a measured cost (`native/moycore/modmoycore.c`). So:

- **Rule:** kernel data is allocated with `heap_caps(MALLOC_CAP_SPIRAM)` unless
  it is latency-bound, and a test asserts it.
- **Gate:** every sprint records internal free and low-water on both S3 boards
  with WiFi and BLE up, against sprint 0's baseline.

## 5. The kernel's language

**#224 chose C on 2026-09-26. This doc reopens it for a spike**, because the
kernel's scope changed: #224's reason that "most kernel code is glue to C" held
for a thin layer, and §2.2 is tens of thousands of lines of logic and
concurrency.

**For Rust** (research, 2026-09-27): safe Rust rules out use-after-free, buffer
overflows, null dereferences and data races by construction on every path,
where sanitizers and fuzzing find them on the paths tests run. Google's Android
report (2025) puts Rust's memory-safety bug density about 1000× below its C and
C++, with 4× fewer rollbacks and 25% less review time. The kernel is concurrent
across cores (`native/moy_flush/moy_flush.c`: "every clause was a race once"),
the import-shaped ABI concentrates `unsafe` at a thin boundary, and most of this
repo's code is written by agents.

**Against, or not bought:** a panic still takes the board down; logic bugs,
deadlocks and stack overflows remain; `unsafe` can still corrupt memory (the
Linux kernel's first Rust CVE, 2025, was a race in an `unsafe` list operation).
The S3's Xtensa needs Espressif's forked rustc and LLVM (`espup`); upstream LLVM's
Xtensa backend is experimental and Rust does not target it. Rust in an ESP-IDF
CMake build as a static library is documented; in a MicroPython usermod there is
no prior art. The browser is the known risk: `wasm32-unknown-emscripten` links
through emcc, and matching rustc to emscripten is fragile where the web runner
and moy-spec pin emscripten for reproducibility. Two languages in the tree.

**Sprint 1a is the spike, on the smallest store component, and its gate is the
decision.** Written in Rust and in C, it must:

1. link into all five firmware targets (four consoles and the Zero), the S3s'
   through the forked toolchain;
2. link into the web runner under the pinned emscripten;
3. load on the host through ctypes and in the unix MicroPython build;
4. report each image's delta against the C twin — counting the web bundle, which
   rides every board's image (`.claude/rules/web.md`), so a kernel's wasm build
   is charged to every board a second time;
5. bench unaligned loads and the instruction-cache miss rate (`perfcnt`) on its
   hottest path on the S3, and show no regression on sprint 0's baseline;
6. run under CI in the time preflight allows.

We iterate until the owner is happy or calls it. **Unhappy means C, and nothing
else in this doc changes**: the sprints, gates and ABI are language-neutral. The
loser is deleted in 1a. Either way #224's containment is required: the host
builds under AddressSanitizer and UndefinedBehaviorSanitizer in CI (Rust's
`unsafe` included, Miri where it runs); fuzzing for every parser of untrusted
bytes; a small kernel; a crash-only kernel that records its reason, reboots to
the native recovery screen or the launcher, and reports over the dev channel.

## 6. The sprints

Each sprint moves one set of components, deletes the Python it replaces, passes
its gate on the host, the browser and every target that takes it, and re-takes
sprint 0's baseline. **Every sprint's gate also includes:** internal SRAM free
and low-water on both S3 boards with WiFi and BLE up (§4.6); each image's
headroom above a per-board floor set in sprint 0; the semantic traces extended
before anything crosses.

| sprint | moves | gate |
|---|---|---|
| **0 — evidence** | nothing. Meters: a `heapcaps` dev-channel word (PSRAM and internal, free and largest) and a GC-pause field in PERF. The census by owner (live GC bytes, held areas, `heap_caps` bytes) with `_LAYER_POOL` checked first. Doom's fit over ≥5 boots per S3 board. Launcher import + construction time on both S3 boards. The complete placement table (§2.2). The stop inventory (§4.4) and the embed-vs-port-fork decision. A stop/start spike on an S3 that keeps one real peripheral alive across the stop — the flush task and a C-owned touch poll. The P4 repartition costed. | the census names the owners of the boot peak and of the T-Deck's ~200 KB; 100 stop/start cycles on the S3 with the peripheral alive and `heap_caps` free flat; a cart-available PSRAM threshold and the kernel's fixed share defined from the census; per-board headroom floors set |
| **1a — the language** | the smallest store component, in Rust and in C (§5) | §5's six items; the owner's decision |
| **1b — the store** | the store's index, catalogue build, cover bookkeeping, seed and project loading; the journal | the parity tests across bindings; the heap after boot within the census's threshold of live; IF the census names the store as the boot peak's owner, Doom loads on a fresh Guition S3 over five boots |
| **2 — the spine** | handle tables; the crash record and strike ledger (`crash_guard`); the native recovery screen; the settings store; WiFi leases | a native crash is recorded and shown after reboot; a VM that fails to start lands on the recovery screen; a stale handle is refused loudly |
| **3 — the survival set** | input (touch, keyboards, BLE HID below `bluetooth`); audio (I2S feed and the sfx/music semantics); the glass (canvas ownership, present, compositors); the SD gate; the frame tail (loop, pump, idle blank, OTA health, PERF, serial); radios, the webhost and the sync RPC | the native loop drives the frame on every tier with Python as an upcall (§4.2); each board's on-glass suite unchanged; `surface_model_v1.md`'s amendment (§8) landed first |
| **4 — the cart path** | the Player's loop and tick model, the runtime map, moycore's glue, the in-cart chrome (strip, system menu, error and fit panels, toasts), netplay lockstep, the Lua superset rulings (§2.3) | a Lua and a wasm cart run launch to exit with **zero Python upcalls** on every tier and **with the VM stopped** on the S3s; cart-available PSRAM above the sprint 0 threshold; Doom loads on a T-Deck after a scripted session and on a fresh Guition S3; the cart census of VM-free carts; exit-to-launcher time against the return budget decides the launcher (§3) |
| **5 — the ABI** | the roles redesigned import-shaped (§2.1); `ctx.shell` closed; the Python binding thin; the wasm import adapter; the `open()`-after-stop contract | `docs/app_api_v1.md` rewritten in place; `tests/test_app_context.py` and the traces cover every role; a wasm module reaches a role through an import; every shipped app restores after a stop |
| **6 — the toolkit** | `runtime/ui.py`'s core and one text path for every runtime | pixel goldens identical on every row that exercises the toolkit; an A/B of widget-heavy frames on an S3 and a P4; a wasm app draws a button and a scroll list |
| **7 — the window managers** | the WMs' state, policy and chrome; the rest of `runtime/console.py` | the P4 desk's on-glass suites unchanged; an A/B of chrome frames on an S3 with the dev channel's timing; `runtime/console.py` is gone |

Sprint 4 is where the memory half's acceptance test is met, and where Python
stops being the OS. Sprints 5 to 7 finish the line.

**The Studio's Run pane is sprint 7's concern.** `docs/studio_2026-09.md` docks
the playtest window into a pane the Editor lends, which is new policy in
`runtime/wm_windowed.py`. It lands before sprint 7 starts, so the port carries
it, or it is designed into sprint 7; it never lands during sprint 6 or 7, whose
gates hold pixels (`docs/theming_2026-09.md` §8 sets the same rule for
theming). The Player in the pane is the same Player, so sprint 4's gate is
unaffected.

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
  images have the least headroom (#158), so a repartition is likely early. A
  partition change is a cable migration flash that can wipe the store
  (`firmware/seeed_xiao_esp32s3_zero/README.md`): cheap while the field is the
  owner's desk, expensive after. Sprint 0 costs it; every sprint gates on the
  headroom floor.
- **Internal SRAM** (§4.6), gated every sprint.
- **A second public contract.** The roles become an ABI for Python and wasm
  apps, under a trace pin.

## 8. Relation to decisions already made, and the sentences this falsifies

- **#224.** This doc is its deliverable. It keeps #224's direction and
  containment, reopens its language for a spike (§5), moves the toolkit from
  "last, or never" into sprint 6, and replaces its crossing order (§6).
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
| the heap "never shrinks" / "never gives an area back" (true in practice, false as mechanism: §1.3) | `.claude/rules/boards.md`, `tools/esp32_build_lib.sh` | sprint 0, in the first change that touches each file — a comment edit alone to the build lib would run the firmware build |

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
   VM and rebuilding a Python launcher. How long may it take? The owner sets it
   from sprint 0's first figure (owner, 2026-09-27); sprint 4 measures against it
   and decides the launcher (§3).
2. **Embed or fork the port's `main.c`** for an in-process VM stop (§4.4).
   Sprint 0.
3. **The Lua superset rulings**, name by name (§2.3). Sprint 4.
4. **`_LAYER_POOL`.** Answered only if sprint 0's census names it as the
   T-Deck's retained memory (owner, 2026-09-27): then the owner decides whether
   its retention is fixed on its own or waits for sprint 3, where the pool
   becomes kernel-owned.
5. **The desk.** Whether the P4 ever stops its VM with apps open.
6. **Text.** One text path for every app runtime; a fixed 8×8 font will not be
   enough for wasm apps (#158's e-reader case). Sprint 6.

## 11. What can kill it

- **Sprint 0's stop spike failing** with a real peripheral alive. The invisible
  stop dies; the fallback is a soft reset into a Player-only VM before a big cart,
  built only then.
- **The census naming something cheaper.** If the boot peak or the retained
  memory is one fixable owner, fix it; the kernel then has to earn itself on its
  other grounds — wasm apps, one toolkit, speed — and this doc says so.
- **Internal SRAM.** If moving state native costs the S3 internal SRAM it cannot
  spare, and PSRAM placement cannot absorb it.
- **Flash headroom** that forces a repartition after there are users.
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
2026-09-27); sprint 1a's gate is the language pass in executable form.
