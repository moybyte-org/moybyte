# The cart path — sprint 4's native design (2026-10)

**What this is.** What sprint 4 of `docs/native_kernel_2026-09.md` (#224)
builds, written before any of it: where every piece of the cart path lands,
the C interfaces of the Player, the tick model, the runtime map and the in-cart
chrome, the VM's stop and start around a cart step by step, which carts run
with no VM, the order one agent lands it in, and the gate. The kernel's
language is C (owner, 2026-10-06). Where the sprint stands is #224's; every
measurement it produces goes there, the per-cart frame numbers to #66 and the
P4's to #58.

**What it stands on.** Sprint 2 gave the kernel the entry, the handle tables,
the routes and return records, the crash record and the recovery floor
(`docs/kernel_spine_2026-10.md`). Sprint 1b put the store in C, cart loading
included (`native/moy_store/moy_load.h`; `docs/kernel_store_2026-10.md`).
Sprint 3 moved below the VM everything that must keep running while none
runs — input, audio sessions, the glass and its buffer rows, the frame loop,
the radio — and left the loop's three console upcalls as the only door into
Python (`docs/kernel_survival_2026-10.md`, its §7 above all). Sprint 4 moves
the cart's own run below that door, and then closes the VM behind it.

## 0. Scope (owner, 2026-10-08)

**The core, and only the core.** The Player's loop and tick model, the
runtime map, moycore's glue, the in-cart chrome (the strip, the system menu,
the error and fit panels, the toasts), netplay's lockstep, the Lua superset
rulings (the plan's §2.3) and stopping the VM for a cart. Absorbed because they
are the core: **#192** (the C play path), **#143**'s error and fit panels, and
**#212**'s runaway watchdog, which a run with no VM needs (§8.2). The rest of
#212 — breakpoints, inspection, the draw-call pick — is not the core and stays
on its issue.

**Deferred until the scope decision after sprint 4** (the plan's §10 question
8), 2026-10-08:

| issue | why it waits |
|---|---|
| #228 3D for carts | a verb family moy-spec defines first (`docs/engine3d_2026-10.md`); it runs inside the runtime this sprint moves, and lands against the C Player once that exists |
| #133, #134 the cart network verb, leaderboards | a public verb with a WiFi lease and a security surface to design; leaderboards ride it |
| #7 local sharing over ESP-NOW | a store-to-store transfer, not a run; it takes the C peer table this sprint builds (§3.6) |
| #227 native carts per chip | a build, signing and trust question of the compiled tier (#158); the runtime map takes a new row when it is answered |
| #231 the file picker for compiled carts | console UI granted to a cart; a VM-free run has no toolkit to draw it until the toolkit's sprint |
| #226's time verb | a verb through moy-spec, and true time on the boards is its own prerequisite |

**One implementing agent, one pass, in the order of §6**, every commit
building and every board passing; when an agent's context fills, a successor
continues from the last green checkpoint. **No carve phase** (owner,
2026-10-07): the pass pins traces first and splits a file only where it must
(§1's two splits are the ones it must).

## 1. The crossing

"Deleted" is Python gone in the commit that lands its C; "stays" names the
part that remains Python and why.

| file | lands in | deleted | stays, and why |
|---|---|---|---|
| `runtime/player.py` | `+native/moy_play/moy_play.c` | the run's lifecycle, the frame (key edges, ticks, draw, audio pull, the periodic pmem flush, `quit()`), the crash capture, the hold-to-exit gesture, the error panel, the fit and newer-console notices, pacing, the run's diag lines | the **Python runtime**: compile, auto-native, the code cache, the namespace `make_api` builds, a traceback's cart line. It is a row of the runtime map whose ops are upcalls (§3.3). The file keeps its name |
| `runtime/tick_model.py` | `+native/moy_play/moy_tick.c` | all of it | — |
| `device/moycore_glue.py` | `+native/moycore/moycore_run.c` and the map's Lua and wasm rows | all of it: `MoycoreRun`'s refresh, drain and persist; `WasmRun`, `CartFrame`, `aot_path`, `wasm_head`, `missing_imports`, the fit check, the module's signature check, `reserve_p8_memory`, `make_runtimes` | — |
| `native/moycore/modmoycore.c` | **split**: `+native/moycore/moycore_run.c` (no `py/` include: the console over kernel buffers, the snapshot, the audio queue into the run's session, pmem, the layer and image seams, the wasm session's reads through the kernel's volume) and `modmoycore.c` (the binding) | `read_on_vm`, `files_on_vm` and the gate and files root pointers they read | the binding, for a Lua run that keeps the VM (§2): `register()`'s trampolines live only there |
| `runtime/lua_ext.py` | the C name table and the handles prelude as Lua source (`+native/moycore/prelude.lua`, compiled into the image) | the snapshot, view and audio seams, `install_handles`, `PRELUDE_HANDLES`, the deny lists | the registrations a VM-kept Lua run needs: `open_editor` and `PRELUDE_EDITOR`, `wifi`, the console verbs, the app roles |
| `runtime/lua_host.py`, `runtime/wasm_host.py` | the host's ctypes binding of `moy_play` | both | — |
| `runtime/device_boot.py`'s runtime probe and map | the runtime map | that half | its boot screen is sprint 3's |
| `runtime/cart_files.py` | `native/moy_store/` (the cart's written files over `moy_fs`'s crash-safe write) | all of it | — |
| `runtime/moy_image.py`'s `Image` | an IMAGE handle (§3.1) the Lua prelude, the wasm imports and the Python canvas share | the pixel holder | a Python wrapper over the handle, for Python carts; the wallpaper sidecar is sprint 5's |
| `runtime/netplay.py` | `+native/moy_play/moy_match.c` | all of it | — |
| `runtime/players.py` | `PlayerRouter`: `moy_input`'s player slots; `NetService`: `moy_match.c` | all of it | `LoopbackNet` becomes the C binding's loopback, for the host tests |
| `device/moy_espnow.py` | `moy_match.c`: beacons, the peer table, offer, join, start, the match's end and its config | all of it (the radio is `native/moy_net/moy_link.c` since sprint 3) | the launcher's peer line reads the kernel's table |
| `runtime/system_menu_ui.py` | `+native/moy_play/moy_chrome.c` | all of it | the launcher hands the menu its rows (SEARCH), so there is one menu over a cart and over the shell |
| `runtime/console_notices.py` | `moy_chrome.c`: the notice banner, the toast and their deadlines | the queue and the two draws | the `Achievements` wiring: badges are awarded at launch, before any stop, and post their toast to the kernel |
| `runtime/achievements_ui.py` | — | — | **stays**: the eggs fire on the launcher and in Settings, which are apps (this corrects the plan's §2.2.1 row) |
| `runtime/console.py`'s cart bar, tool bar and their taps, `settle_cart_frame`, `_sync_cart_text_mode` | `moy_chrome.c`'s strip; `moy_input`'s keyboard mode | those methods | the shell's own top bar calls the same C strip, so the strip has one body until the window managers cross |
| `runtime/cart_api.py`, `device/device_api.py`, `runtime/cart_verbs.py` | — | — | **stay**: the Python cart's namespace, one body on every tier, thinning as its services are C (the plan's open placement, answered) |
| `runtime/console_spine.py`'s `_exit_to_caller`, `_crash_to_code` | the route is C already (`moy_route`); the kernel records the crash's file, line and text with it | the decision | executing a route that lands in a Python app (the Editor, Settings) |

The shipped apps stay Python, per the plan's §3: the launcher (decided by the
return budget, §5.5), Settings, Files, Paint, the editors. Python carts stay
Python and keep the VM.

## 2. Which carts run with no VM

**The rule, decided at launch from the manifest alone**
(`moy_play_vm_free`, one C function, the census's and the Player's):

1. the runtime is `lua` or `wasm`, and the image takes it;
2. the type is `game` (an app, tool or script is granted roles and
   `open_editor`, which are objects: sprint 5's ABI);
3. no permission outside the native set: `net` and `gpio` are native, `wifi`
   (a role-shaped object) is not.

A cart that fails the rule runs under the same C Player with the VM up: its
non-native names are trampolines, counted as APP upcalls. Nothing about a cart
changes with the board it runs on except whether the VM is down; the names a
cart sees are the same either way.

**The Lua superset, name by name** (the plan's §2.3; every name of
`runtime/cart_verbs.py` outside libmoy's table, and every name the Player
grants beside it):

| name | ruling | how |
|---|---|---|
| `make_layer`, `draw_layer` | native | libmoy's core verbs, with the Display seam supplied by `moycore_run.c`; a layer is a BUF row the run owns |
| `image`, `Image` | native | an IMAGE handle over the cart's loaded image bytes, decoded by the store's moyimg decoder (`native/moy_store/moy_cat.c`); `lay:spr(img, …)` is a C call |
| `scene`, `load_scene`, `actors`, `touching`, `move_actor`, `move_actor_to`, `remove_actor`, `draw_scene` | native | the scene world as rows in the run's arena, actors by ACTOR handle; the prelude's wrappers stay Lua |
| `mouse`, `col`, `W`, `H` | native | the pointer snapshot; numbers set as globals at open |
| `pin_write`, `pin_read` | native | `native/moy_net/moy_gpio.c`, behind `gpio` |
| `net`, `on_net` | native | `moy_match.c`'s message queue; `on_net` is a Lua function the run calls between ticks |
| `wifi` | keeps the VM | a role-shaped object |
| `open_editor`, the app roles, the console verbs | keeps the VM | granted only to app, tool and script carts (rule 2) |
| `trace` | absent in a no-VM run | the host harness's; no board grants it |

**A Lua cart with no VM** runs on the task that drives the loop (§4): moycore
opens over `moy_load`'s arena, the Lua state's chunks come from moycore's
pool, every verb is libmoy's C or the run's native rows, and the prelude is
Lua compiled from the image.

**A wasm cart with no VM** runs on its session thread as it does today; the
session's requests that went to the VM's task (`read`, the written files) are
served by the kernel's card volume on the thread that asks, inside the
volume's fence. The fit check reads free PSRAM after the stop, which is the
point of the stop.

**A Python cart keeps the VM**, under the same C Player: the map's Python row
turns open, tick and draw into APP upcalls into the namespace the Python
runtime builds. Its frame pays one crossing per tick and one per draw, as
moycore's paid one per frame before it went C.

## 3. The C interfaces

`+native/moy_play/` is one module a board takes in board.toml, as it takes
`moy_kernel`; the Zero denies it (it runs no carts). It builds into the four
console images, the browser's web build and the host's ctypes library.

### 3.1 Handles and errors

New kinds in `native/moy_spine/moy_htab.h`: **RUN** (10, the run table: one
live row on every tier today), **IMAGE** (11) and **ACTOR** (12). A run is an
OWNER row (sprint 3's lifetimes), so every layer, image, scratch and audio
session it takes is a row it owns and goes when it ends.

Errors extend `MOY_HTAB_*` as `moy_route.h`'s do:

    enum {
        MOY_PLAY_OK = 0, MOY_PLAY_STALE = 1, MOY_PLAY_FULL = 2, MOY_PLAY_NOMEM = 3,
        MOY_PLAY_NOCART = 4,    // no such cart, or it will not load
        MOY_PLAY_NORT = 5,      // its runtime is not in this image
        MOY_PLAY_NEWER = 6,     // a compiled cart imports what this console lacks
        MOY_PLAY_FIT = 7,       // a compiled cart's footprint is over what is free
        MOY_PLAY_RAISED = 8,    // the load, _init or a frame raised: the error panel
        MOY_PLAY_ENDED = 9,     // the run is over; its route is decided
        MOY_PLAY_NEEDS_VM = 10, // a Python-row call while no VM runs
    };

### 3.2 The Player

    int  moy_play_launch(const char *cart, const char *caller, uint32_t flags, uint32_t *run);
    int  moy_play_frame(uint32_t run, uint32_t dt_us, bool render);   // OK or ENDED
    int  moy_play_input(uint32_t run, const moy_play_in_t *in);       // keys, pointer, the hold
    int  moy_play_end(uint32_t run, int why);      // HOLD, QUIT, MENU, CRASH, LINK
    int  moy_play_info(uint32_t run, moy_play_info_t *out);
    bool moy_play_vm_free(const moy_cat_entry_t *e, uint8_t *why);    // §2's rule

`launch` loads the cart through `moy_cat_load` into an arena the run keeps,
records the route with `moy_route` (the caller's kind) and opens the runtime;
a refusal is a code, and its panel is the chrome's (§3.5). `info` answers the
runtime, whether the run is VM-free and why not, the error text, the crash's
file and line, the tick model's state and the run's upcall totals by class.
Every call validates its handle; a handle that outlived its run is STALE.

### 3.3 The runtime map

    typedef struct {
        const char *name;                     // the manifest's "runtime"
        int  (*open)(moy_run_row_t *r, const moy_cart_t *c, char *err, size_t n);
        int  (*init)(moy_run_row_t *r);
        int  (*tick)(moy_run_row_t *r, float dt);
        int  (*draw)(moy_run_row_t *r);
        void (*close)(moy_run_row_t *r);     // idempotent, safe from any state
        int  (*fit)(const moy_cart_t *c, moy_fit_t *need, moy_fit_t *have);   // NULL: none
        bool vm;                              // its ops call into the VM
    } moy_rt_ops_t;
    const moy_rt_ops_t *moy_rt_get(const char *name);    // NULL: not in this image

The Lua and wasm rows are C. The Python row is registered by the MicroPython
binding when a VM starts and withdrawn at its stop, its ops counted APP
upcalls; `moy_rt_get("python")` with no VM answers NULL, and a Python cart's
launch then starts one (§5.2). A runtime an image lacks is an absent row and
the runtime-missing panel.

### 3.4 The tick model

    void moy_tick_start(moy_tick_t *t, int rate_hz, bool steady);
    void moy_tick_mode(moy_tick_t *t, bool steady, bool uncap);
    bool moy_tick_plan(moy_tick_t *t, float dt, uint8_t *n);   // n ticks now; draw?
    void moy_tick_note(moy_tick_t *t, float tick_s);
    void moy_tick_stats(const moy_tick_t *t, moy_tick_stats_t *out);   // div, misses, probes

`TickScheduler` call for call, allocation-free, on an injected `dt`, in
`float`: the boards' MicroPython is single-precision, so the board's
trajectory is the one the trace pins, and the host runs it too. The run row
holds the model; `moy_loop_set_tick` paces the loop from it as the Player's
`_arm_pacing` does.

### 3.5 The chrome

    int  moy_chrome_notice(const char *title, const char *sub, int kind, uint32_t ms);
    int  moy_chrome_toast(const char *title, int glyph, uint32_t ms);
    int  moy_chrome_menu(const moy_menu_row_t *rows, int n);    // open over whatever is in front
    int  moy_chrome_input(const moy_play_in_t *in, int *chosen); // the row id taken, or none
    void moy_chrome_strip(uint32_t canvas, int kind);           // CRASH or TOOL, also the shell's
    void moy_chrome_draw(uint32_t canvas, uint32_t now);        // panel, pill, menu, banner, toast

One body for each piece of in-cart chrome, drawn through the system canvas
after the run's draw and before `end_frame`, the HUD's place (sprint 3's §7.3).
The error panel and the fit and newer-console panels take their words from one
string table in C (§6 step 5 is #143's pass over it). The menu's rows over a
VM-free run are RESTART CART, DELETE CART (the store's delete), SETTINGS and
EDIT (each ends the run with a route into a Python app), ABOUT and REBOOT; the
shell hands its own rows (SEARCH) when it opens the menu.

### 3.6 Netplay

    int  moy_match_offer(const char *cart_title);                  // the host side, at launch
    int  moy_match_state(moy_match_info_t *out);                   // peers, index, seed, dead
    int  moy_lockstep_advance(uint32_t run, uint32_t held, uint32_t now_ms);   // OK or STALLED
    void moy_lockstep_resend(uint32_t run);
    int  moy_lockstep_packet(const uint8_t *data, size_t n, uint32_t now_ms);

`moy_match.c` is `device/moy_espnow.py`'s protocol and `runtime/netplay.py`'s
session over `moy_link`'s ring: inputs, never state; a missing input stalls,
never extrapolates (`docs/netplay_v1.md`). The pre-tick drain is a read of the
ring in the frame, not a SERVICE upcall. A match's config overrides the run's
config in the run's row, never on the card.

## 4. The frame while a run is in front

The loop's frame stage has two foregrounds. **The console** is today's: the
three upcalls, the WM compositing. **A kernel run** is new: the frame stage
calls `moy_play_frame` and `moy_chrome_draw` and makes no upcall, and the
board's compositor presents the run's canvas as it presents the fullscreen
game canvas today. The fullscreen tiers (the S3s, and a play-world cart on a
P4) put a run in front for its whole life. A playtest window on the P4 desk
stays the windowed WM's, which is Python until the window managers cross: its
run is the same C Player, driven from the WM's frame, with no APP or SERVICE
upcall but the console's three.

While the VM is down `moy_loop_task` (sprint 3's) runs the same
`moy_loop_step` with the board's stages; only the console upcalls are gated on
a VM, not the step. Its stack is internal, so a pmem commit to internal flash
needs no deferral, and it is sized at the gate from the Lua roster's measured
high-water.

**Every crossing into Python is counted.** Each `mp_call_*` in `native/`
outside the vendored trees sits behind `moy_loop_count` with its class, and
a test holds that by grep: a new call without a count fails it. The run's
`info` reads the totals at launch and at exit.

## 5. Stopping and starting the VM

The VM stops on the S3 boards, behind a board.toml lever (`vm_stop`) the P4s
lack (ABSENCE, never 0; the plan's §10 question 5 is unchanged). The board's
`kstop` grows a stop mode (`kstop N stop`) that runs the real stop and start
with no cart, which is the guard that the teardown leaves the kernel alive.

### 5.1 When the stop is refused

The run keeps the VM when any of these holds, and `info` names which:

- the cart fails §2's rule;
- the run's route is not HOME (the Editor's PLAY, a run an app started, a desk
  window): coming back to a Python app's state is sprint 5's `open()`-after-stop
  contract (§9 question 2);
- a WiFi lease is held by a Python owner (the kernel's own holders, the link
  and the run, no longer refuse: the radio has been the kernel's since sprint
  3);
- an OTA write is running;
- the board lacks the lever.

### 5.2 Launch, the VM stopping

1. The launcher (Python) calls `moy_play_launch`. The kernel loads the cart,
   records the route, decides the stop, and answers.
2. Before it returns, the launcher writes its place — shelf, selection,
   scroll, search — to its resume row (§5.4), and the badges the launch
   earned post their toast.
3. The console's frame upcall returns; the loop ends with a STOP reason at
   the frame boundary.
4. The VM task runs the stop in the plan's §4.4 order: the loop's upcalls
   cleared, moycore's binding closed, the feeder drained and the fold disarmed,
   async copies waited, the port's deinit list with the Python-handler pin
   sweep, `gc_sweep_all`, the glass rows the VM named released, the
   `moy_alloc` registry freed, the native-code arena freed, `mp_deinit`, the
   root section zeroed, the first area freed, `mp_main_task_handle` handed to
   the loop task, the stdin ring's reader and the interrupt character moved to
   the kernel, the VM task deleted.
5. The loop task opens the run: the runtime's `open`, the fit check against
   the PSRAM the stop returned, `_init`. A refusal or a raise is the chrome's
   panel, still with no VM.
6. Frames.

### 5.3 While the VM is down

The kernel owns the frame, the cart's runtime, input, audio, present, the
card volume and internal flash, the radio and the match, the dev channel, the
webhost (its Python routes answer that no VM runs), idle, OTA health and the
watchdog. The dev channel's Python words (`py`, `tap` on a named button,
`open`) answer that no VM runs; `run` and `state` are the kernel's, so a suite
drives and reads a VM-free run without one.

### 5.4 Exit, the VM starting, the shell rebuilt

1. The run ends: hold-to-exit, `quit()`, a menu row, a crash, a lost match.
   The kernel persists pmem (the store's crash-safe write), closes the runtime,
   ends the audio session, releases the run's rows and the arena, and records
   the route with any crash's text, file and line.
2. A crash with no VM paints the error panel and waits there; EDIT (absent
   for a compiled cart, which has no source) ends the run with the Editor's
   route and the line, and any other way out is HOME.
3. The loop task creates the VM task: the first area, `mp_thread_init`,
   `gc_init`, `mp_init`, the kernel's flash volume mounted, `main.py`.
4. `main.py` sees a return start (`moy_kernel_mode`): no splash, no seed, no
   OTA verdict. It builds the Workstation and the WM, runs the route the kernel
   recorded, registers the three upcalls and returns into the loop.
5. The launcher reads its resume row and lands where it was left.

**What dies, and what rebuilds it.** Everything in the GC heap: the
Workstation and its WM, the launcher and its items, the Project, the
catalogue's Python views, the theme tables, the font and glyph caches,
`moybuf`'s caches, the Python cart code cache, the editors, the dev channel's
Python words, the webhost's Python routes, the `Achievements` object. The
rebuild reads only kernel state: the settings rows (theme, volume, idle), the
store's index and catalogue, the routes and return records, the crash record,
the resume row. **Anything a session relied on that lives only in the heap is
lost at every return**, which today it is only at a reboot: the
`Achievements` tracker's distinct-carts count for `play_five` is the one the
pass found, and it moves to a settings row. The resume row is the launcher's
alone in this sprint, a fixed-size row in the settings store; sprint 5
generalises it into every app's contract.

### 5.5 The return budget, measured

The kernel stamps the exit's first moment and the first console frame the new
VM draws, and `state` reads the difference with its parts: VM start, imports,
the Workstation, the first frame. The gate takes it on both S3s, five runs
fresh and five after the census's session, against the budget the owner sets
from it (the plan's §3; §9 question 1). Sprint 0 found the imports the largest
part of a Python launcher's start (#224). Before a native launcher is
weighed, two Python levers are taken: the launcher's import chain stops
pulling the editors, and the apps are constructed at their first open rather
than at boot.

### 5.6 The stop inventory rows that must hold

From the plan's §4.4, each with its check in the gate's `kstop N stop` and in
the launch/exit cycles: the RX ISR's task handle moved before the VM task is
deleted; the stdin ring drained by the kernel and the interrupt character
off while down; the wasm session ended before a sweep (a VM-free wasm run
never meets a sweep: its session opens after the stop and closes before the
start); the root section zeroed, so no stale root marks the reused first area;
moycore's binding closed and its pointers into Python buffers cleared; the
Lua state's chunks back to the pool; the fold disarmed and the async copies
fenced; the glass rows and the registry freed; the native-code arena freed;
the first area freed and allocated again at start; the VM task's stack back
to internal SRAM; the kernel's GPIO ISRs and native tasks alive through it; the
leases row as §5.1 has it.

## 6. The pass order

Each step lands in commits that build, pass `tools/preflight.sh`, and pass
`tools/board.py pass` on the four consoles; a checkpoint is a step's last
commit. The traces are extended before the code they pin
(`tests/test_semantic_traces.py`, both object models).

1. **The Player, the tick model, the runtime map and the glue in C, the VM
   still up.** First the run's trace vocabulary (lifecycle, the tick
   trajectory, error capture, pmem flush points, exit routes) and the upcall
   totals in `state`, so the gate's meter exists and reads non-zero. Then
   `moy_tick.c`; then the `modmoycore.c` split; then `moy_play.c` with the map's
   three rows and the native superset names; the wasm session's requests to
   the kernel's volume with `cart_files` crossing. The console's frame upcall
   still drives the Player. *Checkpoint 1:* every board's suite as before; a
   Lua and a wasm seed on each console read zero APP and SERVICE upcalls per
   frame; the roster's fps within noise.
2. **The chrome and netplay.** `moy_chrome.c` (strip, pill, panels, menu,
   banner, toast) and the shell's bar onto the C strip; `moy_match.c`; then the
   kernel run as the loop's foreground. *Checkpoint 2:* zero upcalls of every
   class from launch to exit for every VM-free census cart on the host, the
   browser and the four consoles, with the VM up; a two-board lockstep match
   (T-Deck and a P4) holds its stall rate against #65's.
3. **Stopping the VM, last and on its own.** First `kstop N stop` with no cart,
   100 cycles on both S3s, the T-Deck's input task and shared bus
   included (the run the plan's §11 asked for before sprint 4 relies on
   stops). Then the launch-time stop on the S3s and the return start of §5.4.
   *Checkpoint 3:* `state` reads the VM down through a Doom run on both S3s;
   100 launch/exit cycles of a Lua cart with `heapcaps` flat from the second;
   every suite green with its VM-free checks reading kernel words.
4. **The gate's numbers** (§7), recorded in #224 and #66; the return budget
   measured for the owner's call.
5. **The absorbed issues.** #212's runaway watchdog (§8.2); #143's words and
   visuals on the error and fit panels; #192's tracker closed against this
   doc, its outstanding measurements taken on the VM-free path.

## 7. The gate

Each item fails with its bug present.

| item | how | the bug it catches |
|---|---|---|
| zero Python upcalls, launch to exit, every tier | the run's totals by class at launch and exit: CONSOLE, APP, SERVICE unchanged (DRIVER is the host's and browser's harness, zero on a board), for each VM-free census cart, in the host goldens, the browser suites and each console's suite | any trampoline, a chrome draw left in Python, a drain through Python, an uncounted call (the grep test) |
| the VM stopped on the S3s | `state` reads the VM down and no `mp_task` during the run; `kstop N stop` and the launch/exit cycles flat | a refusal that should not fire, a stop that soft-resets instead |
| cart-available PSRAM | free and largest block at the fit check, worst of five boots fresh and after the census's session (`tools/mem_census.py`), both S3s, at or above the plan's §6.1 values | memory the stop does not return, a run row the pool keeps |
| Doom | loads and plays on a T-Deck after the scripted session and on a fresh Guition S3, five boots each | the same, measured by the cart the plan's §1.1 is written for |
| the VM-free census | a tool lists every seed cart and every `make -C libmoy p8-carts` cart with its runtime and `moy_play_vm_free`'s verdict and reason; a test pins the list, and on each S3 the run's `info` agrees with it | a rule that drifts, a cart that silently keeps the VM |
| exit to the launcher | §5.5's stamp, against the owner's budget | a slow return, a lost resume row |
| the standing meters | the four-board pass; the `--uncap` roster on both S3s; Bench µs/op on every console; `KERNEL_SRAM`, and the loop task's stack as the only internal cost while stopped (the plan's §6.1); every image above its floor, the Zero's above 256 KiB; reboot to first light and to `state` per console | the regressions every sprint guards |

## 8. Two mechanisms worth their own words

### 8.1 Python carts and the error panel

A Python cart's crash still throws the kid into the Editor on the line, as
today; a Lua cart's crash with the VM up does too. A VM-free crash paints the
C panel and waits for a choice, because a VM start is not instant and an
Editor opening by itself after a pause reads as a second fault.

### 8.2 The runaway watchdog (#212's rider)

The loop's watchdog feed is per frame; a run that does not come back to the
loop starves it. Before that reset, a frame-overrun budget (a multiple of the
pacing slot, configuration) fires: a Lua run's count hook (`LUA_MASKCOUNT`,
installed from C, outside the cart's reach) raises at the next instruction
count and the run ends as a crash "stuck on line N"; a wasm run is terminated
and stops at its next import call; a Python cart's VM gets a scheduled
interrupt, caught narrowly. A wasm loop that calls no import cannot be stopped
(`native/moy_wasm/README.md`): the task watchdog resets the board, and the
crash record charges the cart (§9 question 3).

## 9. Open questions for the owner

1. **The return budget.** Recommendation: the launcher's first frame within
   1 s of the exit, worst of five after the scripted session, on both S3s. A
   Python launcher that misses it after §5.5's two levers is the plan's §3
   trigger for the native launcher, designed on its own.
2. **The VM stop for runs that return into a Python app** (the Editor's PLAY,
   an app's run). Recommendation: not in sprint 4. The Editor's PLAY is the
   iterate loop, where the return must be instant and the Editor's state is
   sprint 5's contract; those runs keep the VM and still make zero upcalls.
3. **A wasm loop that calls no import.** Recommendation: the board reset and
   the crash record for now; the fork's loop-edge check, a compiler flag the
   key would carry and a cost to measure, is a #158 item.
4. **If the budget is missed.** Recommendation: the stop ships for runs that
   need it — a compiled cart that does not fit with the VM up — and every
   other VM-free run keeps the VM until the launcher meets the budget; the
   zero-upcall gate holds either way.

## 10. Claims this sprint falsifies

Rewritten where they stand by the step that falsifies them:

| claim | lives in | step |
|---|---|---|
| "MicroPython is the shell and is not the engine"; the C-layers decision | `docs/moycore_direction.md` §1, §3 | 2 |
| moycore's glue and the host twins as the cart runners | the board.toml descriptions of `moycore_glue.py`, `lua_host.py`, `wasm_host.py` | 1 |
| `achievements_ui.py` crosses; the cart API rows are open; `moy_espnow.py` crosses with the radios | `docs/native_kernel_2026-09.md` §2.2.1 | 1, 2 |
| the stop inventory's sprint-4 rows; a held lease refuses a stop | `docs/native_kernel_2026-09.md` §4.4 | 3 |
| no VM stop on dev; the loop task draws only the web-console screen | `docs/kernel_survival_2026-10.md` §7.1, §7.5 | 3 |
| the loaded cart's arena lives until the callback returns | `native/moy_store/moy_load.h` | 1 |
| netplay's files and the Python drain | `docs/netplay_v1.md`, `.claude/rules/netplay.md` | 2 |
