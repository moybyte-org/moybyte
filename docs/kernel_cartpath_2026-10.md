# The cart path — sprint 4's native design (2026-10)

**What this is.** What sprint 4 of `docs/native_kernel_2026-09.md` (#224)
builds, written before any of it: where every piece of the cart path lands,
the C interfaces of the Player, the tick model, the runtime map and the in-cart
chrome, the VM's stop and start around a cart step by step, which carts run
with no VM, the order one agent lands it in, and the gate. The kernel's
language is C (owner, 2026-10-06). Status lives in #224, which also holds
the sprint's measurements; per-cart fps belongs to #66, P4 figures to #58. Revised 2026-10-08 after an adversarial review; §5 carries what it
changed, and §9 the owner's decisions on its questions.

**What it stands on.** Sprint 2 gave the kernel the entry, the handle tables,
the route tables, the crash record and the recovery floor
(`docs/kernel_spine_2026-10.md`). Sprint 1b put the store in C, cart loading
included (`native/moy_store/moy_load.h`; `docs/kernel_store_2026-10.md`).
Sprint 3 moved below the VM what must keep running while none runs — input,
audio sessions, the glass and its buffer rows, the frame loop, the radio —
and left the loop's three console upcalls as the door into Python
(`docs/kernel_survival_2026-10.md`, its §7 above all). Sprint 4 moves the
cart's run below that door.

**The point (owner, 2026-10-08): the kernel controls memory and the run, not
the Python VM.** The VM becomes something the kernel takes down on demand. A
VM-free cart makes zero Python upcalls whether or not the VM is up, and the
kernel stops the VM only when a cart needs the memory — the fit check with
the VM up fails — which is mostly the S3 tier. Stopping for every VM-free
cart is not a goal; it stays a later option, gated on a measured return start
(§5).

## 0. Scope (owner, 2026-10-08)

**The core, and only the core.** The Player with its loop and its tick
model; the map of runtimes; the glue around moycore; the chrome drawn over a cart (the strip, the system menu,
the error and fit panels, the toasts), netplay's lockstep, the Lua superset
rulings (the plan's §2.3) and stopping the VM for a cart. Absorbed because they
are the core: **#192** (the C play path), **#143**'s error and fit panels, and
**#212**'s runaway watchdog, which a run with no VM needs (§8.2). The rest of
#212 — breakpoints, inspection, the draw-call pick — stays on its issue.

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
2026-10-07): the pass pins traces first and splits a file only where it must.

## 1. The crossing

"Deleted" is Python gone in the commit that lands its C; "stays" names the
part that remains Python and why.

| file | lands in | deleted | stays, and why |
|---|---|---|---|
| `runtime/player.py` | `native/moy_play/moy_play.c` | the run's lifecycle, the frame (key edges, ticks, draw, audio pull, the periodic pmem flush, `quit()`), the crash capture, the hold-to-exit gesture, pacing, the run's diag lines; the error, fit and newer-console panels once the chrome lands (§6 step 2) | the **Python runtime**: compile, auto-native, the code cache, the namespace `make_api` builds, a traceback's cart line. It is the runtime map's Python row (§3.3). The file keeps its name |
| `runtime/tick_model.py` | `native/moy_play/moy_tick.c` | all of it | — |
| `device/moycore_glue.py` | `native/moycore/moycore_run.c` and the map's Lua and wasm rows | all of it: `MoycoreRun`'s refresh, drain and persist; `WasmRun`, `CartFrame`, `aot_path`, `wasm_head`, `missing_imports`, the fit check, the module's signature check, `reserve_p8_memory`, `make_runtimes` | — |
| `native/moycore/modmoycore.c` | **split**: `native/moycore/moycore_run.c` (no `py/` include: the console over kernel buffers, the snapshot, the audio queue into the run's session, pmem, the layer and image seams, the wasm session's requests through the kernel's volume) and `modmoycore.c` (the binding) | `read_on_vm`, `files_on_vm` and the root pointers they read | the binding, for a Lua run that keeps the VM (§2): `register()`'s trampolines live only there |
| `runtime/lua_ext.py` | the C name table and the handles prelude as Lua source (`+native/moycore/prelude.lua`, compiled into the image) | the snapshot, view and audio seams, `install_handles`, `PRELUDE_HANDLES`, the deny lists | the registrations a VM-kept Lua run needs: `open_editor` and `PRELUDE_EDITOR`, `wifi`, the console verbs, the pins, the app roles |
| `runtime/lua_host.py`, `runtime/wasm_host.py` | the host's ctypes binding of `moycore_lua.c` and `moy_play` (`runtime/lua_binding.py`, with `runtime/moyhost_wasm.c` in the same library), driven for a Lua cart by `device/moycore_glue.py` over `runtime/moycore.py` | `lua_host.py` | `wasm_host.py`: the host's compiled-cart runtime (its engine is host policy), its frames the Player's |
| `runtime/device_boot.py`'s runtime probe and map | the runtime map | that half | its boot screen is sprint 3's |
| `runtime/cart_files.py` | `native/moy_store/moy_files.c` (the cart's written files over `moy_fs`'s crash-safe write) | the run path | the reference `tests/test_moy_files.py` holds the C to, and the store's `remove` that Get Carts' REMOVE calls |
| `runtime/moyimg.py`'s decoder and `runtime/moy_image.py`'s `Image` | `native/moy_store/moy_img.c`, a C port of the moyimg codec (none exists in C: `moy_cat.c` only gathers a cart's `.moyimg` files), and an IMAGE handle (§3.1) | the Python decode on the run path | the codec's Python stays the reference the port's parity test reads, and the encoder Paint uses; `Image` stays a Python wrapper over the handle for Python carts; the wallpaper sidecar is sprint 5's |
| the scene world (`runtime/widgets.py`'s scenes, the `.moyscene` parse) | `native/moycore/moycore_scene.h`: the parse and `draw_scene` in C, the live world as Lua tables in the run's state | the run path's parse and placement | the Scene tab's editing, an app; a Python cart's world |
| `netplay.py` (deleted) | `native/moy_play/moy_match.c` | all of it (step 2) | — |
| `runtime/players.py` | `PlayerRouter`: `moy_input`'s player slots; `NetService`: `moy_match.c` | all of it | `LoopbackNet` becomes the C binding's loopback, for the host tests |
| `moy_espnow.py` (deleted) | `moy_match.c`: beacons, the peer table, offer, join, start, the match's end and its config | all of it (the radio is `native/moy_net/moy_link.c` since sprint 3) | the launcher's peer line reads the kernel's table |
| `system_menu_ui.py` (deleted) | `native/moy_play/moy_chrome.c` | the drawing (step 2; the rows and actions are `runtime/console_notices.py`'s) | the launcher hands the menu its rows (SEARCH), so there is one menu over a cart and over the shell |
| `runtime/console_notices.py` | `moy_chrome.c`: the notice banner, the toast and their deadlines | the queue and the two draws | the `Achievements` wiring: badges are awarded at launch, before any stop, and post their toast to the kernel |
| `runtime/achievements_ui.py` | — | — | **stays**: the eggs fire on the launcher and in Settings, which are apps (this corrects the plan's §2.2.1 row) |
| `runtime/console.py`'s cart bar, tool bar and their taps, `settle_cart_frame`, `_sync_cart_text_mode` | `moy_chrome.c`'s strip; `moy_input`'s keyboard mode | those methods | the shell's own top bar calls the same C strip, so the strip has one body until the window managers cross |
| `runtime/console.py`'s `_crash_to_code`, `runtime/console_spine.py`'s `_exit_to_caller` | the route is `moy_route`'s; the kernel records a crash's file, line and text beside it | the decision | executing a route that lands in a Python app (the Editor, Settings) |
| `native/moy_spine/modmoy_spine.c`'s `AppRegistry`, `BackStack`, `Returns`, `Leases` | kernel singletons, allocated once at first light | the objects' ownership: each is a `mp_obj_malloc_with_finaliser` whose `__del__` frees its table, so a sweep frees the routes | the Python objects, as non-owning views with no-op finalisers (§5.4) |
| `runtime/dev_channel.py`'s `state` and `run` | `native/moy_kernel/moy_devch.c` | those two words' Python | the words that act on a Python app (`py`, `open`, `tap` on a named button) |
| `runtime/cart_api.py`, `device/device_api.py`, `runtime/cart_verbs.py` | — | — | **stay**: the Python cart's namespace, one body on every tier, thinning as its services are C (the plan's open placement, answered) |

The shipped apps stay Python, per the plan's §3: the launcher (decided by the
`need` policy, §9 decision 1), Settings, Files, Paint, the editors. Python
carts stay Python and keep the VM.

## 2. Which carts run with no VM

**The rule, decided at launch from the catalogue entry alone**
(`moy_play_vm_free`, one C function, the census's and the Player's):

1. the runtime is `lua` or `wasm`, and the image takes it;
2. the type is `game`, read with the store's default: a manifest that names
   no type is a `game` when it is a spec cart and an `app` otherwise
   (`runtime/moy_carts.py`'s rule, which `moy_cat.c` stores raw today and C
   applies the same way);
3. every permission is in the **native set**: `graphics`, `input`, `audio`,
   `multiplayer`.

**Everything else keeps the VM**, by name: `network` (the `wifi` object),
`console` (the text console), `pins` (on a console no backend is granted, and
in the browser the verbs are the page's HTTP queue, `firmware/web_runner/gpio_link.py`),
`files` and every `files:<kind>`, `prefs`, and every role permission
`runtime/system_api.py` grants. A permission C does not know keeps the VM.
The census (§6 step 1) pins the verdict against the real manifests of every
seed cart and every PICO-8 import.

A cart that fails the rule runs under the same C Player with the VM up: its
non-native names are trampolines, counted APP upcalls. The names a cart sees
are the same whether or not the VM is down.

**The Lua superset, name by name** (the plan's §2.3; every name of
`runtime/cart_verbs.py` outside libmoy's table, and every name the Player
grants beside it):

| name | ruling | how |
|---|---|---|
| `make_layer`, `draw_layer` | native | libmoy's core verbs, with the Display seam supplied by `moycore_run.c`; a layer is a BUF row the run owns |
| `image`, `Image` | native | an IMAGE handle over the cart's image, decoded by the C port of moyimg |
| `scene`, `load_scene`, `actors`, `touching`, `move_actor`, `move_actor_to`, `remove_actor`, `draw_scene` | native | the scene texts handed to the run before the load and parsed in C; the live world is the prelude's Lua tables in the run's state, at most 256 actors (§9 decision 5); `draw_scene` is C over them |
| `mouse`, `col`, `W`, `H` | native | the pointer snapshot; numbers set as globals at open |
| `net`, `on_net` | native | `moy_match.c`'s message queue, behind `multiplayer`; `on_net` is a Lua function the run calls between ticks |
| `wifi`, `pin_write`, `pin_read` | keeps the VM | behind `network` and `pins` |
| `open_editor`, the app roles, the console verbs | keeps the VM | granted only to app, tool and script carts (rule 2) |
| `trace` | absent in a no-VM run | the host harness's; no board grants it |

**A Lua cart with no VM** runs on the VM service task (§4): moycore opens over
`moy_load`'s arena, the Lua state's chunks come from moycore's pool, every
verb is libmoy's C or the run's native rows, and the prelude is Lua compiled
from the image.

**A wasm cart with no VM** runs on its session thread as it does today. The
session's file requests are C over the kernel's volume
(`native/moy_store/moy_files.h`), run on the VM service task through
`moy_wasm_on_vm` while it waits on the call, each inside the board's bus gate
(`moy_vol_gate_enter`): the session's stack is PSRAM
(`native/moy_wasm/moy_wasm_footprint.h`) and an internal-flash write disables
the cache, and the service task needs no fence against itself.

**A Python cart keeps the VM**, under the same C Player: the map's Python row
turns open, tick and draw into APP upcalls into the namespace the Python
runtime builds, one crossing per tick and one per draw.

## 3. The C interfaces

`+native/moy_play/` is one module a board takes in board.toml, as it takes
`moy_kernel`; the Zero denies it (it runs no carts). It builds into the four
console images, the browser's web build and the host's ctypes library.

### 3.1 Handles and errors

A run is an **OWNER** row (kind 5, sprint 3's lifetimes) with the role RUN;
the Player's handle is that row's. Every layer, image, scratch and audio
session it takes is a row it owns and goes when it ends. Two new kinds in
`native/moy_spine/moy_htab.h`: **IMAGE** (10) and **ACTOR** (11). A kinded
table holds at most 256 rows, so a run has at most 256 live images and 256
live actors where a Python cart's scene world has no cap (§9 decision 5).

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
    int  moy_play_end(uint32_t run, int why);      // HOLD, QUIT, MENU, CRASH, LINK, SERIAL
    int  moy_play_info(uint32_t run, moy_play_info_t *out);
    bool moy_play_vm_free(const moy_cat_entry_t *e, uint8_t *why);    // §2's rule

`launch` decides from the catalogue entry (§2's rule, and for a compiled cart
its footprint against free PSRAM with the VM up), records the route with
`moy_route` and arms the crash record with the cart's id (§8.3); the load
follows, after the stop when there is one (§5.2). `info` answers the runtime,
whether the run is VM-free and why not, whether the VM stopped and why, the
error text, the crash's file and line, the tick model's state and the run's
upcall totals by class. Every call validates its handle; a handle that
outlived its run is STALE.

### 3.3 The runtime map

    typedef struct {
        const char *name;                     // the manifest's "runtime"
        int  (*open)(moy_run_row_t *r, const moy_cart_t *c, char *err, size_t n);
        int  (*frame)(moy_run_row_t *r, float dt, bool draw);   // _update, then _draw
        void (*close)(moy_run_row_t *r);     // idempotent, safe from any state
        int  (*fit)(const moy_cat_entry_t *e, moy_fit_t *need, moy_fit_t *have);   // NULL: none
        bool vm;                              // its ops call into the VM
    } moy_rt_ops_t;
    const moy_rt_ops_t *moy_rt_get(const char *name);    // NULL: not in this image

A tick and its draw are one op because both C runtimes run `_update` and
`_draw` back to back (moycore's frame); a logic-only tick passes `draw` false.
The Lua and wasm rows are C. The Python row is registered by the MicroPython
binding when a VM starts and withdrawn at its stop, its ops counted APP
upcalls. A runtime an image lacks is an absent row and the runtime-missing
panel.

### 3.4 The tick model

    void moy_tick_start(moy_tick_t *t, int rate_hz, bool steady);
    void moy_tick_mode(moy_tick_t *t, bool steady, bool uncap);
    bool moy_tick_plan(moy_tick_t *t, float dt, uint8_t *n);   // n ticks now; draw?
    void moy_tick_note(moy_tick_t *t, float tick_s);
    void moy_tick_stats(const moy_tick_t *t, moy_tick_stats_t *out);   // div, misses, probes

`TickScheduler` call for call, allocation-free, on an injected `dt`, in
`float`: the boards' MicroPython is single-precision, so the board's
trajectory is the one the trace pins, and the host runs it too. The run row
holds the model; `moy_loop_set_tick` paces the loop from it.

### 3.5 The chrome

    int  moy_chrome_notice(const char *title, const char *sub, int kind, uint32_t ms);
    int  moy_chrome_toast(const char *title, int glyph, uint32_t ms);
    int  moy_chrome_menu(const moy_menu_row_t *rows, int n);    // open over whatever is in front
    int  moy_chrome_input(const moy_play_in_t *in, int *chosen); // the row id taken, or none
    void moy_chrome_strip(uint32_t canvas, int kind);           // CRASH or TOOL, also the shell's
    void moy_chrome_draw(uint32_t canvas, uint32_t now);        // panel, pill, menu, banner, toast

One body for each piece of in-cart chrome, drawn through the system canvas
after the run's draw and before `end_frame`, the HUD's place (sprint 3's §7.3).
The error, fit and newer-console panels take their words from one string
table in C (§6 step 5 is #143's pass over it). Over a run the menu's rows are
today's — RESTART CART, DELETE CART (the store's delete), SETTINGS, ABOUT,
REBOOT — and SETTINGS ends the run with a route into Settings; no EDIT row
joins them (§9 decision 1). The shell hands its own rows (SEARCH) when it
opens the menu.

### 3.6 Netplay

    int  moy_match_offer(const char *cart_title);                  // the host side, at launch
    int  moy_match_state(moy_match_info_t *out);                   // peers, index, seed, dead
    int  moy_lockstep_advance(uint32_t run, uint32_t held, uint32_t now_ms);   // OK or STALLED
    void moy_lockstep_resend(uint32_t run);
    int  moy_lockstep_packet(const uint8_t *data, size_t n, uint32_t now_ms);

`moy_match.c` is the link's protocol and the lockstep session over
`moy_link`'s ring: inputs, never state; a missing input stalls,
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

**`moy_loop_step` gates the upcalls, not the step.** Today it answers STOPPED
when no VM runs (`native/moy_kernel/moy_loop.c`); it becomes a step whose
console upcalls are skipped and counted REFUSED when no VM runs, and whose
other stages run. The board's stages that touch the VM go: `b_say` writes
through the kernel's serial path, not `mp_hal_stdout_tx_str` under the GIL
macros.

**Who drives it while the VM is down: the VM service task itself.** After the
stop's `mp_deinit` the task does not end; it runs the kernel run's loop until
the run ends, and then starts the next VM where its `soft_reset:` label is
(§5). Its stack is the one a Lua cart has always compiled and run on
(moycore's `run_chunk` loads the source there), its watchdog subscription
(`moy_kernel_feed`) is unchanged, and no second task's stack is alive at a
handover. `moy_loop_task` stays what sprint 3 made it: the driver of the
window between a soft reset's teardown and the next VM. This keeps the VM
task's stack in internal SRAM while the VM is down, where the plan's §4.4
returned it; the S3s' gain is PSRAM, and the T-Deck's largest internal block
with radios up is too small to hold two task stacks at once (#224).

**Every crossing into Python is counted, and so is every refused one.** The
loop's classes are CONSOLE, APP, DRIVER, SERVICE and a new **REFUSED**: an
upcall attempted with no VM or nothing registered, which `up()` answers ABSENT
today without counting. The `mp_call_*` sites reachable from `moy_loop` and
`moy_play` are listed by file and function in `native/moy_play/upcall_sites.txt`,
and a test holds the tree's sites to that list as `tests/test_mp_task_calls.py`
holds the `mp_task` copy to its record: a new site fails it until it is
counted and listed.

## 5. Stopping and starting the VM

The VM stops on the S3 boards, behind a board lever (`MOY_VM_STOP` in the
board's `mpconfigboard.h`, beside the panel's kernel entry points the stopped
run presents through) the P4s lack (ABSENCE, never 0; the plan's §10
question 5 is unchanged). The stop and its run are
`native/moy_play/moy_play_stop.c`; `vmstop force` (a dev word) makes the fit
check read as failing, so a board where a cart fits can be made to stop. **It stops
only when the run needs the memory (owner, 2026-10-08)**: a VM-free cart
whose fit check fails with the VM up, which today means a compiled cart the
size of Doom on the S3s. Every other run keeps the VM, so an ordinary exit
returns to a launcher that is still running, as it does today. Stopping for
every VM-free cart, a policy value `always` beside `need`, stays off (§9
decision 1): a return start rebuilds the shell. The zero-upcall
gate does not depend on the policy, because a VM-free run makes no upcall
whether or not the VM is down.

`kstop` grows a stop mode, `kstop N stop`: the real stop and start with no
cart, which is the guard that the teardown leaves the kernel alive and the
kernel's state intact.

### 5.1 When the stop is refused

The run keeps the VM when any of these holds, and `info` names which:

- the cart fails §2's rule;
- the policy is `need` and the fit check passes with the VM up;
- the cart would not fit with the VM stopped either (its footprint is over
  free PSRAM, or its block over the largest one, with every byte of the VM's
  heap added back): the fit notice, with the VM up;
- the run's route is not HOME (the Editor's PLAY, a run an app started, a
  desk window): returning to a Python app's state is sprint 5's
  `open()`-after-stop contract;
- a WiFi lease is held by a Python owner (the kernel's own holders, the link
  and the run, do not refuse: the radio has been the kernel's since sprint 3);
- an OTA write is running;
- the board lacks the lever.

### 5.2 Launch, the VM stopping

1. The launcher (Python) calls `moy_play_launch`. The kernel decides the stop
   from the catalogue entry and the fit with the VM up, records the route,
   arms the crash record, and answers. Nothing of the cart is loaded yet:
   buffers made before the stop would sit inside the free run the stop is
   about to widen, and the census saw a few tens of KB of new buffers cost the
   T-Deck's largest block hundreds of KB (#224). The Python Player launches
   the run before it builds anything of its own (the audio session, the
   namespace), and what the kernel keeps for runs -- the Player's row, the
   front's chrome list, the compiled cart's stream ring, the resume record --
   is made before the VM's first area (`moy_kernel_reserve`), below every
   area a VM will hold. The `heapwalk` dev word lists PSRAM's blocks by
   address, at the stop's down point and its run's end with `heapwalk stop
   all`.
2. Before it returns, the launcher writes its place — shelf, selection,
   scroll, search — to its resume record (§5.4), and the badges the launch
   earned post their toast.
3. The console's frame upcall returns; the loop ends with a STOP reason at
   the frame boundary.
4. The VM service task runs the stop in the order the plan's §4.4 gives,
   which is today's `soft_reset_exit:` list in `moy_kernel.c` plus three
   steps after `mp_deinit`: the roots zeroed, the first heap area given back,
   and the stdin ring and the interrupt character taken by the kernel. Unlike
   the plan's order, the task is not deleted.
5. The task loads the cart (`moy_cat_load` into the run's arena) and opens
   the runtime, and the fit check runs again against the LARGEST block, which
   is what Doom's load needs. A refusal or a raise is the chrome's panel, still
   with no VM.
6. Frames, on the same task.

### 5.3 While the VM is down

The kernel owns the frame, the cart's runtime, input, audio, present, the
card volume and internal flash, the radio and the match, the dev channel, the
webhost (its Python routes answer that no VM runs), idle, OTA health and the
watchdog. On serial, `state` and `run` are the kernel's (§6 step 2); `py`,
`open` and `tap` on a named button answer that no VM runs; there is no REPL.
A Ctrl-C (0x03) ends the run (why SERIAL) and starts the VM, so
`board.py reboot --soft` and an interrupting host reach a VM as they do today.

### 5.4 Exit, the VM starting, the shell rebuilt

1. The run ends: hold-to-exit, `quit()`, a menu row, a crash, a lost match, a
   Ctrl-C. The kernel persists pmem (the store's crash-safe write), closes the
   runtime, ends the audio session, releases the run's rows and the arena,
   disarms the crash record on a clean end, and records the route with any
   crash's text, file and line.
2. A crash with no VM paints the error panel and waits there. A compiled cart,
   the one that stops the VM under the `need` policy, has no source and no
   EDIT, which is today's panel.
3. The task jumps to `soft_reset:` and starts the VM: the first area,
   `gc_init`, `mp_init`, the kernel's flash volume mounted, `main.py`.
4. `main.py` asks the kernel how it started: a new call beside
   `moy_kernel_mode` (which answers only the boot decision) answers BOOT or
   RETURN. A return start skips the splash, the seed and the OTA verdict,
   builds the Workstation and the WM, executes the route the kernel recorded,
   registers the three upcalls and returns into the loop.
5. The launcher reads its resume record and lands where it was left.

**The kernel state a return start reads**, each of which must survive a sweep:
the settings rows (theme, volume, idle); the store's index and catalogue; the
crash record; the **back-stack, returns and leases**, which today are tables
owned by Python objects whose finalisers free them, and become kernel
singletons the Python objects view without owning (the app registry is
re-registered by the Workstation at every start, so it is cleared there
rather than kept); the **resume record**, a fixed-size kernel struct in PSRAM
that lives until the next reboot and is never written to flash (a settings
commit can cost the Guition S3 a quarter-second, #224); and the **card
volume**: on the Guition S3 the VM start mounts the kernel's VFS type over the
volume the kernel holds open, as it mounts internal flash, and touches no bus,
because a failed remount there stays failed until a reboot.

**What dies at a return start.** Everything else in the GC heap: the
Workstation and its WM, the launcher and its items, the Project, the
catalogue's Python views, the theme tables, the font and glyph caches,
`moybuf`'s caches, the Python cart code cache, the editors, the dev channel's
Python words, the webhost's Python routes, the `Achievements` object with its
`_played` and `_seen_views` counters (the play-five and toolbox badges), the
`Clipboard` (#132), and a toast whose deadline had not run out. Today these
die only at a reboot; under `need` they die when a big compiled cart returns,
and none of them is carried over (§9 decision 2).

### 5.5 The return, measured

The kernel stamps each VM start (`moy_kernel.h`'s `MOY_STAMP_*`, ms after
power-on): the last VM's end -- the stopped run's end at a return start, the
teardown's first moment at a soft reset, power-on at a boot -- the next VM's
`mp_init`, the console's build beginning, the Workstation built, the wiring
done (`device/desktop_spine.py` stamps those three) and the first frame
(`boot_ok`). `state` reads them as `start`: the kind and the parts VM start,
imports, the Workstation (the store's scan included), the wiring, the first
frame, and their total. The gate takes it on both S3s, five runs fresh and
five after the census's session; #224 holds the figures. The `on-glass`
suites hold a return start to every part being read.

Two Python levers are taken before it is measured (§6 step 4,
2026-10-10). The shell's import chain stops pulling the editors: the
block, map, scene and music editors' UIs and the paint surface are
`Workstation` properties built at their first use (`runtime/console.py`'s
`_LAZY_MODULES`), and the shell's modules import the editor cores they use
from their own modules rather than the `editors` umbrella. And the apps'
cost at a start is Paint's document, which `PaintAppLayer` builds at its
first use: the six apps are still constructed and registered at the start,
because the others each construct in a few milliseconds (#224) and the
registry's identity, relayout and window wiring read the live objects.

### 5.6 The stop inventory rows that must hold

From the plan's §4.4, each with its check in `kstop N stop` and in the
launch/exit cycles:

- the RX ISR keeps notifying the VM service task, which no longer dies; the
  kernel drains the stdin ring and holds the interrupt character off while the
  VM is down, and turns 0x03 into the run's SERIAL end;
- the wasm session ended before any sweep (a stopped run's session opens after
  the stop and closes before the start);
- the root section zeroed, so no stale root marks the reused first area;
- moycore's binding closed and its pointers into Python buffers cleared; the
  Lua state's chunks back to the pool;
- the fold disarmed and the async copies fenced;
- the glass rows and the registry freed; the native-code arena freed;
- the first area freed, and allocated again at start;
- the kernel's GPIO ISRs and native tasks alive throughout;
- the routes and leases readable after the stop (`kstop N stop` sets a route
  and a lease before it and reads them back after);
- **mounts and SD**: internal flash is the kernel's littlefs, mounted at each
  start; the Guition S3's card is the kernel's open volume, mounted at each
  start with no bus traffic; the T-Deck's `moy_sd` stays attached;
- the leases row as §5.1 has it.

## 6. The pass order

Each step lands in commits that build, pass `tools/preflight.sh`, and pass
`tools/board.py pass` on the four consoles; a checkpoint is a step's last
commit. The traces are extended before the code they pin
(`tests/test_semantic_traces.py`, both object models).

1. **The Player, the tick model, the runtime map and the glue in C, the VM
   still up.**
   - First the meters: the run's trace vocabulary (lifecycle, the tick
     trajectory, error capture, pmem flush points, exit routes), the upcall
     totals with the REFUSED class in `state`, `upcall_sites.txt` and its test,
     and the census tool with its test pinned against the real manifests.
   - Then `moy_tick.c`; the `modmoycore.c` split; the C moyimg decoder with a
     parity test against `runtime/moyimg.py` over every seed image; the scene
     and actor rows with the scene trace.
   - Then `moy_play.c` with the map's three rows and the native superset
     names; the wasm session's requests to the kernel's volume with
     `cart_files` crossing and internal-flash writes routed to the service
     task. The stack high-water around moycore's open and frame is measured on
     both S3s.
   - The console's frame upcall still drives the Player, and the panels are
     still the shell's, drawn from `moy_play_info`.

   *Checkpoint 1:* every suite as before; a Lua and a wasm seed on each
   console read zero APP, SERVICE and REFUSED upcalls per frame; the census
   agrees with every run's `info`; the roster's fps within noise.
2. **The chrome, netplay, and the kernel's own serial words.** `moy_chrome.c`
   (strip, pill, panels, menu, banner, toast) and the shell's bar onto the C
   strip, the Python panels deleted with it; `moy_match.c`; `state` and `run`
   in C; the crash record armed for games; then the kernel run as the loop's
   foreground. *Checkpoint 2:* zero upcalls of every class but DRIVER from
   launch to exit for every VM-free census cart on the host, the browser and
   the four consoles, with the VM up; a two-board lockstep match (T-Deck and a
   P4) holds its stall rate against #65's.
3. **Stopping the VM, last and on its own.**
   - The spine singletons, with the route and lease read-back in `kstop`.
   - The step gated per upcall, `b_say` off the GIL path, the kernel run on
     the VM service task after `mp_deinit`, and `kstop N stop`: 100 cycles on
     both S3s, the T-Deck's input task and shared bus included (the run the
     plan's §11 asked for before sprint 4 relies on stops), card reads across
     each cycle on the Guition S3.
   - The return start: the start kind, the resume record, the card's mount
     over the open volume.
   - The launch-time stop under `need`, the load after it.

   *Checkpoint 3:* `state` reads the VM down through a Doom run on both S3s;
   20 Doom launch/exit cycles on each with the largest block flat from the
   second; a Ctrl-C during a stopped run reaches a VM; every suite green with
   its VM-free checks reading kernel words.
4. **The return, then the gate's numbers.** The launcher's import chain and
   lazy app construction (§5.5); the return start measured; the gate (§7),
   recorded in #224 and #66; a return start measured, which is what the
   `always` option would be judged on; the option stays off.
5. **The absorbed issues.** #212's runaway watchdog (§8.2); #143's words and
   visuals on the error and fit panels; #192's tracker closed against this
   doc, its outstanding measurements taken on the C path.

## 7. The gate

Each item fails with its bug present.

| item | how | the bug it catches |
|---|---|---|
| zero Python upcalls, launch to exit, every tier | the run's totals by class at launch and exit: CONSOLE, APP, SERVICE and REFUSED unchanged (DRIVER is the host's and browser's harness, zero on a board), for each VM-free census cart, in the host goldens, the browser suites and each console's suite; `upcall_sites.txt`'s test | any trampoline, a chrome draw left in Python, a drain through Python, an upcall refused while stopped, a new uncounted call site |
| the kernel stops the VM when, and only when, the run needs it | `state` reads the VM down through Doom on both S3s and up through every seed Lua cart; `kstop N stop` flat with the routes and leases read back | a refusal that should not fire, a stop nobody needed, a stop that soft-resets instead, a sweep that frees kernel state |
| cart-available PSRAM | free and largest block right after the stop and before the load, worst of five boots fresh and after the census's session (`tools/mem_census.py`), both S3s, within 64 KiB (configuration: the run's table and chrome rows) of the `kstop N stop` no-cart baseline on the same boot, and at or above the plan's §6.1 values | memory the stop does not return, a buffer made before the stop splitting the free run |
| Doom | loads and plays on a T-Deck after the scripted session and on a fresh Guition S3, five boots each, and 20 launch/exit cycles on each with its fit passing on the largest block every time | the same, measured by the cart the plan's §1.1 is written for |
| the VM-free census | every seed cart and every `make -C libmoy p8-carts` cart with its runtime and `moy_play_vm_free`'s verdict and reason, pinned; on each S3 the run's `info` agrees (`tools/vm_free_census.py --board`, which also holds every VM-free run's books to the zero-upcall row) | a rule that drifts, a permission spelled wrong, a cart that silently keeps the VM |
| exit to the launcher | §5.5's stamp: under `need`, a non-stopping exit within noise of dev's; a stopping exit against the owner's budget | a slow return, a lost resume record |
| the standing meters | the four-board pass; both S3s' uncapped roster; Bench's µs per op, each console; `KERNEL_SRAM` at the launcher with the VM up, and internal free and low-water with a VM-free Lua run in front reported beside it (moycore's allocator takes internal SRAM above its floor, so that figure is the run's, not the kernel's); every image above its floor, the Zero's above 256 KiB; reboot to first light and to `state` per console | the regressions every sprint guards |

## 8. Three mechanisms worth their own words

### 8.1 Python carts and the error panel

A Python cart's crash throws the kid into the Editor on the line, as today,
and so does a Lua cart's, whose VM stays up under `need`. A compiled cart's
crash paints the panel, as today, whether or not its run stopped the VM.

### 8.2 The runaway watchdog (#212's rider)

The service task's watchdog feed is per frame; a run that does not come back
to the loop starves it. Before that reset, a tick-overrun budget (a multiple
of the pacing slot, configuration: `moy_play.h`'s RUNAWAY WATCH) fires from a
watcher on another task: a Lua run's count hook (`LUA_MASKCOUNT`, installed
from C, outside the cart's reach) raises on the line it is on and the run
ends as a crash under the stuck title; a wasm run is terminated and stops at
its next import call. A wasm loop that calls no import cannot be stopped
(`native/moy_wasm/README.md`): the task watchdog resets the board, and the
crash record names the cart (§8.3). The fork's loop-edge check is a #158 item.
A Python cart's tick is not watched: its scheduled interrupt stays on #212.

### 8.3 Games in the crash record

Today only a user app or a wallpaper arms the record (`moy_kstate_arm` keeps
those two roles), so a game that faults or hangs the board leaves a record
that names no cart. `moy_play_launch` arms a new GAME role with the cart's id
before the runtime opens, and a clean end disarms it, so every fault and hang
in a run names its cart. A game spends no strikes (§9 decision 3).

## 9. The owner's decisions (2026-10-08)

1. **The VM stops only when a cart needs the memory** (policy `need`). The
   `always` policy stays off, so its return budget, its crash screen for a Lua
   cart with the VM stopped, and an EDIT row in the in-game menu are not built.
   A return start is a boot without the store: #224's census holds its parts
   against today's exit to the launcher. The plan's §3 trigger for the native
   launcher is unchanged.
2. **Session state lost at a stopped return is accepted**: the Clipboard
   (#132), the play-five and toolbox badge counters, a toast mid-deadline. It
   happens only at a `need` stop and is handled when that code is C; no
   settings row or kernel record carries any of it over.
3. **Games earn no crash strikes, and are named in the crash record**:
   `moy_play_launch` arms the cart's id before the runtime opens and a clean end
   disarms it (§8.3). The kid's own game always reopens to be fixed.
4. **Ctrl-C during a stopped run ends the run and starts the VM** (§5.3). There
   is no REPL while it is down; serial reads `state` and `run`, and the tools
   reach a VM through the interrupt.
5. **A VM-free run holds at most 256 live actors and 256 live images**; a
   Python cart has no cap. A cart past it fails loudly (FULL) rather than
   drawing wrong, and the limit is documented in the cart API's limits through
   moy-spec.

## 10. Claims this sprint falsifies

Rewritten where they stand by the step that falsifies them:

| claim | lives in | step |
|---|---|---|
| MicroPython as the shell and not the engine; the C-layers decision | `docs/moycore_direction.md` §1, §3 | 2 |
| moycore's glue and the host twins as the cart runners | the board.toml descriptions of `moycore_glue.py`, `lua_host.py`, `wasm_host.py` | 1 |
| `achievements_ui.py` crosses; the cart API rows are open; `moy_espnow.py` crosses with the radios | `docs/native_kernel_2026-09.md` §2.2.1 | 1, 2 |
| the stop deletes the VM's task and returns its stack; the stop inventory's sprint-4 rows; a held lease refuses a stop | `docs/native_kernel_2026-09.md` §4.4, §6.1 | 3 |
| no VM stop on dev; the loop task drives the frames through sprint 4's stops | `docs/kernel_survival_2026-10.md` §7.1, §7.5 | 3 |
| the spine's tables are freed with their Python objects | `native/moy_spine/modmoy_spine.c`'s header | 3 |
| the loaded cart's arena lives until the callback returns | `native/moy_store/moy_load.h` | 1 |
| netplay's files and the Python drain | `docs/netplay_v1.md`, `.claude/rules/netplay.md` | 2 |
