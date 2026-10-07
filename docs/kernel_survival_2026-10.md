# The survival set — sprint 3's native design (2026-10)

**What this is.** What sprint 3 of the kernel plan
(`docs/native_kernel_2026-09.md`, #224) will build, designed ahead of the first
line of it: the six subsystems a console needs while no Python app runs —
input, audio, the glass, the storage gate for the internal flash volumes, the
frame tail and the links — each with its carve, its native interface, the
Python it deletes, the open issues it designs in, the board facts that bite it
and its gate. The kernel's language is C (owner, 2026-10-06). Where the sprint
stands is #224's; every measurement it produces goes there, the per-cart frame
numbers to #66 and the P4's to #58. Revised 2026-10-07 after an architecture
and a performance review; the decisions those forced carry that date.

**What it stands on.** Sprint 2 made the kernel the entry on the four consoles
and gave it handle tables, the settings rows, the crash record, the task
watchdog and the recovery floor (`docs/kernel_spine_2026-10.md`;
`native/moy_kernel/`, `native/moy_spine/`). Sprint 1b put the store in C down
to the card volume the kernel owns on every console (`docs/kernel_store_2026-10.md`;
`native/moy_store/`). Sprint 3 is the first sprint whose code runs every frame
and touches every driver, which is why its passes are whole subsystems and why
each one ends on five boards.

**The name.** After sprint 4 a Lua or wasm cart runs with the VM stopped on
the S3 boards. What must keep running through that stop — the panel's feed, the
touch poll, the keyboard, the speaker, the radio, the loop that paces them — is
this set. Sprint 3 moves it below the VM; sprint 4 stops the VM above it.

**What it does not buy.** No speed. The three upcalls a frame cost microseconds
against a frame measured in milliseconds, and #66 records the console's floor
unchanged through the last loop extraction (#202); a full collect while the VM
is up still stalls pacing, the tail polls and present, and the native loop
removes none of it. The sprint's gates therefore hold the numbers flat; they do
not expect them to move.

## 0. The passes, in order

The owner works in whole-subsystem passes, one agent per subsystem, no slices
smaller than a subsystem on a board. The order is by risk and by what each
pass needs from the one before it.

| pass | subsystem | why here | agents |
|---|---|---|---|
| **1** | **the glass** (§3): canvas and buffer ownership, the layer pool, the surface table, present on the three compositor kinds | the riskiest crossing and the one every later proof reads through; it fixes the kernel's PSRAM share (the pool, §6.1 of the plan) and executes the `surface_model_v1.md` amendment the plan orders first | one, alone on the boards |
| **2** | **input** (§4), **audio** (§5) and **the links with the internal volumes** (§6) | three subsystems that share no file once the carve (§2) has split the board glue, the widgets, the dev channel's words and the build registrations; their host halves are long and their board halves short | three in parallel on disjoint files; the boards are a queue (below) |
| **3** | **the frame tail** (§7): the loop, the pump, idle, OTA health, PERF, serial and diag, the boot order, each board's `moy_runtime.py` | last, because every service it would have to call up into has crossed; its gate is the upcall count, and that count is only meaningful once input, present, audio and the tail polls are C calls | one, alone on the boards |

**Files.** Pass 2's three agents share no file because the carve lands first:
each subsystem's service construction is its own provider module that
`build_desktop` and `device/p4_desktop.py` call (§2 item 1), `Pointer` and
`_SilentAudio` leave `runtime/widgets.py`, the dev channel's words are
registered per subsystem, and every new native directory is in every build
list as an empty module before any pass fills it. `device/moycore_glue.py` is
the audio agent's alone in this sprint (its input refresh is sprint 4's, with
the frame around `tick()`).

**Boards.** No two of the three are board-disjoint: input needs all four
consoles, audio the T-Deck and both P4s, the links all five. What each agent
does alone is its host half — the C, the host binding, the fuzzers, the traces
— and the three host halves overlap; the board passes queue on the ports
(`tools/board.py` refuses a held port and names the holder). The Zero is the
links agent's alone. The browser is every pass-2 agent's: `runtime/web_input.py`
is input's, `firmware/web_runner/web_boot.py`'s `_RunnerAudio` is audio's, the
three `*_link.py` files are the links', and each landing rebuilds the web
bundle (§6.8). The one shared bus is the P4s' I2C, which the touch controller
and the codec share on both boards: the kernel's bus object lands with the
carve's native stubs (§2 item 6), input owns the bus, audio adds its device.

Each pass lands on dev on its own: the carve commit first, then the crossing,
each with the goldens untouched and the on-glass suites unchanged
(`tools/board.py pass tdeck guition_s3 p4 guition_p4`, the Zero built and its
suite run every pass, `tools/preflight.sh` before the report).

## 1. The crossing, component by component

| what | Python today (the twin after the carve) | C | pass |
|---|---|---|---|
| canvas rows, the off-heap buffer table, the pool, lending and bakes | `device/device_canvas.py`, `_LayerComp`, `_LAYER_POOL`, `_LENT_BAKES` (`runtime/moy_glass.py`) | `native/moy_glass/moy_canvas.h`, `native/moy_glass/moy_buf.h` | 1 |
| the surface table and the mint | `runtime/surface.py` (`runtime/moy_glass.py`) | `native/moy_glass/moy_surface.h` | 1 |
| the banded compositor, the fold | `device/banded_panel.py`, `modules/tdeck_panel.py`, `modules/guition_panel.py` | `native/moy_glass/moy_present_banded.c` over `native/moy_flush/` and each board's panel module | 1 |
| the DSI compositor, rotated included | `device/dsi_panel.py`, `device/p4_canvas.py`, `modules/p4_display.py`, `modules/guition_p4_display.py` | `native/moy_glass/moy_present_dsi.c` over `native/p4/moy_dsi/` and `native/p4/moy_ppa/`; the backlight is the panel entry sprint 2 gave the floor | 1 |
| the palette | `runtime/palette.py` | the canvas's table in `moy_canvas.h` | 1 |
| the host and browser rasters | `runtime/host_canvas.py`, `runtime/gfx_binding.py`, `firmware/web_runner/web_canvas.py` | the same `moy_glass` built for ctypes and for the web build | 1 |
| the merged input state, sources, the pointer | `device/moybyte/input.py`'s `InputState`, `runtime/input.py`, `Pointer` and `pointer_state` (`runtime/moy_input.py`) | `+native/moy_input/moy_input.h` | 2 |
| the T-Deck keyboard, trackball and poller | `device/moybyte/input.py`'s `TDeckKeyboard`, `decode_raw`, `InputPoller`; `device/device_input.py` | `+native/moy_input/moy_tdeck_kbd.c`, a kernel task | 2 |
| the touch drivers | `device/gt911.py`, `device/gsl3680.py`, `device/axs_touch.py`, `modules/p4_input.py`, `modules/guition_p4_input.py`, `modules/gsl_fw_jc8012.py` | `+native/moy_input/moy_touch_gt911.c`, `+native/moy_input/moy_touch_gsl3680.c`, `+native/moy_input/moy_touch_axs.c`; the GSL firmware as a C array | 2 |
| the BLE HID central | `device/ble_keyboard.py` over `bluetooth`; `native/p4/moy_ble_hid/` | `+native/moy_input/moy_ble_hid.c` over the NimBLE host, on every console | 2 |
| the browser's event decode | `runtime/web_input.py` | the web build's `moy_input` import | 2 |
| the audio session, the six verbs, the bank push, the master level | `device/device_audio.py`, `runtime/audio.py`'s `AudioEngine`, `_SilentAudio`, `runtime/host_api.py`'s `FakeAudio`, `web_boot.py`'s `_RunnerAudio`, `device/moycore_glue.py`'s drain (`runtime/audio_session.py`) | `native/moy_audio/` grows its session and verb face; the codec in `+native/moy_audio/moy_codec_es8311.c` | 2 |
| WiFi, the radio under the spine's lease | `device/device_wifi.py` (the service over `kernel_wlan`; the Zero's station stays the port's) | `native/moy_net/moy_wifi.c` | 2 |
| ESP-NOW's owner | `device/moy_espnow.py` over `espnow`; `native/p4/moy_c6/` | `native/moy_net/moy_link.c` | 2 |
| the HTTP core and the webhost | `device/moy_webserver.py`, `device/moy_webhost.py`; `native/moy_web/` | `native/moy_net/moy_http.c`, `+native/moy_net/moy_webhost.c` | 2 |
| the sync RPC, both halves | `runtime/moy_sync.py`, `firmware/web_runner/carts_link.py`, `firmware/web_runner/update_link.py`, `firmware/web_runner/gpio_link.py` | `native/moy_net/moy_sync.c` | 2 |
| the updater, its HTTP(S) client, the C6 updater, Get Carts' transport | `device/moy_ota.py`'s updater half (`device/moy_http.py`), `device/moy_c6_update.py`, `device/cart_net.py` | `+native/moy_net/moy_ota.c`, `+native/moy_net/moy_c6_update.c` | 2 |
| the web-console switch | `runtime/web_console.py` (its screen: §13, question 8) | `+native/moy_net/moy_webconsole.c` | 2 |
| the Zero's host | `modules/zero_host.py`, `modules/zero_gpio.py`, `modules/zero_setup.py` | the same `moy_net`, with the Zero's GPIO allowlist as a board table | 2 |
| the internal flash volumes | `moy_vol`'s borrowed littlefs backend (sprint 1b) | `native/moy_store/moy_vol.c` owns the instance, with a VFS type of the kernel's for Python | 2 |
| the loop, the pump, idle, OTA health, PERF, the HUD, stage meters, the tail polls | `runtime/device_boot.py`'s frame half (`runtime/frame_loop.py`), `runtime/console_perf.py`, `runtime/perf_hud.py`, `runtime/perf_line.py`'s formatter, `device/moy_ota_health.py` | `+native/moy_kernel/moy_loop.c`, `+native/moy_kernel/moy_idle.c`, `+native/moy_kernel/moy_perf.c` | 3 |
| the dev channel's reader and kernel words, the diag ring | `runtime/dev_channel.py`, `device/device_diag.py`, `device/moybyte_diag.py`, `device/device_util.py`; `native/moy_serial/` | `+native/moy_kernel/moy_devch.c`, `+native/moy_kernel/moy_diag.c` | 3 |
| the boot order | `device/desktop_spine.py`'s boot half, `runtime/device_boot.py`'s `DeviceBoot` (its splash: §13, question 9) | `+native/moy_kernel/moy_boot.c` | 3 |
| the board glue | each console's `moy_runtime.py` and the provider modules the carve makes; `device/p4_desktop.py`'s present wiring; `device/boot_shell.py` | per-board defines in `mpconfigboard.h` and `board.toml` | 3 |
| the browser's and the host's drivers of the loop | `firmware/web_runner/web_boot.py`'s `step_frame_json`, `runtime/host_app.py` | JS and CPython call `moy_loop_step` | 3 |

Each native directory builds three ways, as `native/moy_spine/` does on the
boards and the host: a MicroPython usermod the boards take through board.toml,
a host library for ctypes, and a fuzz driver. **The browser is the fourth
(2026-10-07).** Sprint 2 left the browser on the Python spine: the page's
wasm is built from moy-spec's `runner/`, and rebuilding it crosses a repo.
This sprint's modules cannot stay out of that build — the
browser's canvas is `DeviceCanvas`, its input is the same merge, its loop is
the same step — so the kernel's modules enter the web build from pass 1, the
spine's C joins them in pass 3, and each pass that touches them rebuilds the
bundle in the pinned emscripten container and re-bakes it into every image
(`.claude/rules/web.md`; `tools/preflight.sh` compares the blob to its source).
The twin names the interface, the tests pin it, the crossing swaps the body and
deletes the twin in the same change.

**What stays Python after sprint 3, by decision.** The Workstation, the window
managers and the draw stack (`runtime/console.py`, `runtime/wm.py`,
`runtime/wm_windowed.py`, `runtime/launcher_layer.py`) with their retained
caches and streaks: sprint 7's, reached from the loop by three upcalls a frame.
The Player, the tick model, the runtime map and moycore's frame glue
(`runtime/player.py`, `runtime/tick_model.py`, `device/moycore_glue.py`'s input
refresh and the frame around `tick()`): sprint 4's. The bank model the Music
editor edits (`runtime/audio.py`'s `SFX`, `MusicTrack`, `AudioBank`). The
Settings screens over the services, including the firmware-update screen
(`runtime/update_ui.py`, §10) and the toggles of `runtime/console_settings.py`,
each of which writes its row and then calls the kernel's setter for the
subject that crossed (crisp pixels to the glass; PERF DIAG, the SD diag and
the FPS chip to the perf and diag modules; the idle timeout to the ladder).
`runtime/console_notices.py`'s banner, which reads the update verdict from
the kernel's OTA health. `DeviceBoot`'s runtime probe and the cart step
(`runtime/boot_carts.py`). The apps.

## 2. The carve

Python only, host-tested, one commit per subsystem, no behaviour change: the
goldens, the traces and the on-glass suites stay byte-for-byte. It runs one
pass ahead of the crossing it serves and no further. Pass 2's disjointness is
made here, not promised.

1. **The service providers.** `device/desktop_spine.py` builds the WiFi
   service, the ESP-NOW link, the updater, Get Carts' transport and the webhost
   itself, and `device/p4_desktop.py` constructs the BLE keyboard, the input
   state and the C6 updater. Each subsystem's construction moves to a provider
   module — `device/wire_input.py`, `device/wire_audio.py`,
   `device/wire_links.py` — that `build_desktop` and the P4 desk call by one
   name each, so the spine composes and no pass edits it. The T-Deck's
   `moy_runtime.py` loses the poller thread, the keyboard and trackball
   construction and the text-mode hook to
   `firmware/lilygo_t_deck_plus_mainline/modules/tdeck_input.py`, its audio
   factory and the webhost diag hook to the providers; the Guition S3 gets
   `firmware/guition_jc3248w535/modules/guition_input.py`; the P4s' input
   modules exist. What remains in each `moy_runtime.py` is the frame tail's:
   the tail hooks, the diag, the PERF sink.
2. **The widgets split.** `Pointer` and `pointer_state` move to
   `runtime/moy_input.py`; `_SilentAudio` to `runtime/audio_session.py`;
   `runtime/console.py`'s one import line changes once, here.
3. **The dev channel's words are registered.** `DevChannel` keeps the reader
   and a word table; `tap`, `swipe` and `drag` register from
   `runtime/devch_input.py`, `vol` from `runtime/devch_audio.py`, `link`,
   `web`, `recv` and the tier-1 sideload words (`moy?`, `moy-put`, `moy-del`,
   `moy-rescan`, `moy-run`) from `runtime/devch_links.py`. The frame tail's
   reader later takes the same tables from C.
4. **The frame half of `runtime/device_boot.py`** — `FrameLoop`, `FramePump`,
   `IdleBlank`, `OtaHealth`, `PerfSampler`, `StageMeters`, `poll_webhost`,
   `poll_link`, `apply_touch` — moves to `runtime/frame_loop.py`.
   `DeviceBoot` keeps the splash and the runtime probe.
5. **`device/moy_ota.py` splits** into `device/moy_ota_health.py` (the boot
   verdict and the confirm after painted frames: the loop's) and the updater
   over `device/moy_http.py` (the streaming client with redirects, which
   `device/cart_net.py` already reads through). Two passes, two files.
6. **The build registrations and the native stubs.** `native/moy_glass/`,
   `native/moy_input/`, `native/moy_net/` and the kernel's new files are added
   to every list that enumerates native modules — each console's and the
   Zero's board.toml (taken or denied with a reason), the Makefile's
   `UNIX_MP_MODULES`, `runtime/native_build.py`, the web runner's build — as
   modules that compile to nothing, so a pass adds bodies to directories every
   build already knows. Two small pieces of real C land here because two
   passes need them on day one: the kernel's I2C bus object
   (`native/moy_kernel/moy_bus.c`: one master bus per board define, devices
   added by address) and the handle kinds of §3.1 in `native/moy_spine/moy_htab.h`.
7. **The twins take the native call shapes.** `runtime/moy_glass.py`: tables
   of kind CANVAS, BUF, SURF and OWNER over `moy_spine.Table`, the pool keyed
   by byte size with an owner per loan, and `present`, `fence` and
   `present_pending` as the three verbs the compositors answer;
   `device/device_canvas.py` is rebased so every buffer it holds is a row.
   `runtime/moy_input.py`: one `InputTable` replacing the two `InputState`
   classes (the boards' fifteen-name one and the host's eight), bit order
   fixed to libmoy's `moy_button` enum with the seven console-only names
   after it, sources by handle of kind SRC, `masks(order, player)` kept with
   both arguments, the pointer's place/down/fresh/click as fields of the
   table. `runtime/audio_session.py`: a session per owner carrying its bank,
   the six verbs on a session, `focus` naming the audible one.
   The links' twin: the WiFi credential rules, the link's peer table of kind
   PEER, the HTTP request parser and response writers as pure functions, the
   sync batch codec; the first and the last three are `native/moy_net`'s since
   pass 2 (`runtime/net_binding.py` on CPython), the peer table
   `device/moy_espnow.py`'s.
8. **The survival traces** join `tests/test_semantic_traces.py` before anything
   crosses: an input trace (a scripted event stream → the merged state, both
   masks for players 0 and 1, and the pointer, per frame), a glass trace (a
   draw script → the frame's crc, the surface gens, the buffer rows by owner
   and origin), a loop trace (a scripted boot and twenty frames → the stage
   order, the idle ladder's state and the upcall count per frame, the last ten
   frames driven from a second thread), a links trace (a sync batch → the
   store's rows; the OTA state machine's transitions on a canned manifest).
   The audio trace is `tests/test_audio_parity.py`, already across bindings,
   plus a session trace (two sessions, one focused). Each trace holds on
   CPython and the desktop MicroPython, and on glass through the dev channel's
   crc words.

## 3. The glass (pass 1)

### 3.1 Canvas and buffer ownership

A canvas is a kernel row of kind CANVAS: `{buf, w, h, stride, caps, palette,
clip, camera, font_scale, counters, owner}`. The state array `device_canvas.py`
shares with `moy_gfx` today (`_ST_*`: camera, clip, size, font scale, the
profiling counters) is the row. Every off-heap buffer is a row of kind BUF —
sprint 2 reserved the kind for `runtime/moybuf.py`'s registry and this pass is
where the table is built — with a role (layer, bake, scratch, cache, paint,
pool) and an origin (pool, alloc, heap). Owners are rows of kind OWNER, minted
by whatever starts a lifetime (a cart run, the wallpaper, a window, the map
cache, the Paint document) and released when it ends; `reclaim` takes the
owner's handle, so a recycled lifetime cannot free another's loans — the slot's
generation refuses it. `moy_htab.h`'s kinds become APP, BUF (the buffer
table, no longer only reserved), CANVAS, SURF, OWNER, SRC, PEER and AUDIO,
added in the carve (§2 item 6).

    int moy_canvas_new(uint32_t *h, uint16_t w, uint16_t h_px, uint32_t caps, uint32_t owner);
    int moy_canvas_sync_back(uint32_t h);                // re-point at the compositor's back buffer
    int moy_buf_new(uint32_t *h, size_t nbytes, uint8_t role, uint32_t owner);   // pool first, then PSRAM
    int moy_buf_release(uint32_t h);                     // back to the pool or freed, by origin
    int moy_owner_new(uint32_t *h, const char *tag);
    int moy_owner_end(uint32_t h);                       // every loan returned, then the row
    int moy_glass_capture(uint32_t h, uint8_t *idx, size_t n, int *exact);  // §3.6

Errors are the spine's: STALE, FULL and NOMEM, mapped by the binding to the
same exceptions. A kinded table holds 256 rows; the glass's peak is counted
by the gate (the pool's free rows, the live layers, the P4 desk's windows and
their drag backdrops, bakes capped at four per owner, scratches) and FULL on
BUF is a loud `MemoryError`, never a silent fallback.

**The draw verbs are not wrapped.** They are C already (`native/moy_gfx/`,
libmoy under it), and on every board the hot four — `rect`, `rectb`, `print`,
`pix` — are C gate objects that shadow the Python methods (`_install_draw_gates`),
which is what #66 measured as the difference between tens of microseconds an
op and a Python frame's hundreds (`.claude/rules/rendering.md` has the
tooling; the figures are #66's). The gates survive the crossing: the draw
context holds the canvas row's pointer and generation, validation is an index
and a generation compare inside the context and never a table lookup per op,
and no Python frame is added to any verb. The ungated Python verbs are
deleted, not wrapped, and the remaining Python methods are the gates' C
objects as today. Lua and wasm carts reach the same functions without the
binding in sprint 4. The `set_pump` upcall path in the draw context
(`GATE_PUMP_EVERY`), unarmed since the core-0 feeder, is deleted.

The game canvas, the system canvas, a cart-declared small canvas, the view
scratch, the fold's snapshot scratch, the bar's strip cache and every window
buffer the P4 desk mints are rows. The DSI scan buffers are `moy_dsi`'s own
and stay its; the rotated compositor's two paint buffers, which
`moy_alloc.malloc_dma` hands out with no free today, become rows the kernel
holds for the console's lifetime; the `malloc_dma` lane `device_canvas.py` keeps
for a firmware without `alloc` is deleted, since every image has had `alloc`
since #186.

### 3.2 The pool and the share

`_LAYER_POOL` is most of the T-Deck's retained memory (sprint 0's census,
#224), because it recycles but never frees and lives in `heap_caps` memory no
collector reaches. The owner's decision (2026-10-05) makes it the kernel's.
The design, with the accounting the share needs:

- **Rows, not dicts.** A pooled buffer is a BUF row whose origin is POOL;
  the pool is the set of free rows keyed by byte size, in PSRAM by rule.
- **Owned by whom is what the share counts.** A row a cart run makes — its
  layers, its view and snapshot scratches, its bakes — is cart memory,
  counted against cart-available PSRAM and reclaimed at the run's end, after
  moycore's `close()` has dropped its pointers into them (the plan's §4.4).
  The kernel's share counts kernel-owned rows only: the pool's free rows, the
  system canvas and the paint buffers, the strip cache, the capture scratch
  while a shot is taken. So the share's arithmetic on the Guition S3 is the
  plan's: the C side the census measured, plus a pool of about one 320×240
  layer; a scaled cart's two 153,600-byte scratches do not sit in it between
  runs because they go with the run.
- **Bounded by the share.** The pool holds at most `MOY_GLASS_POOL_BYTES`,
  a per-board define derived from the share minus the fixed buffers the build
  knows and printed beside the headroom. Above the bound a released row is
  freed, not kept: on the Guition S3 a scroll cart's 384,000-byte world is
  freed at exit and allocated again at the next run, one `heap_caps` call.
- **NOMEM.** When PSRAM cannot give a row, the kernel frees the pool's free
  rows and retries once. If that fails the binding keeps today's last resort
  — a gc-heap `bytearray`, after the compact the host comment describes — so
  no cart that ran before stops running, and the row is counted with origin
  HEAP. The gate expects that count to read zero across the cycle set below;
  a non-zero count is the regression the perf review named, a heap area grown
  by a layer that then refuses Doom.
- **Loans have owners.** `moy_buf_new` records the owner, `moy_owner_end`
  returns every loan — the #63 leak fix, kept, with the bakes of #186 as
  loans of the same kind. The Paint app's `release_bakes` is the same call
  with its own owner.
- **Counted.** `heapcaps` gains the table's rows and bytes by owner class and
  origin, and PSRAM's largest free block beside its free total; the census
  snapshot gains a `glass` field with the same split.

The window buffers of the P4 desk, which `_LayerComp.release` frees by origin
today, keep that behaviour: a window's buffer is an ALLOC row, freed on
release, never pooled, because the desk re-mints at every size.

### 3.3 The surface table

The amendment `docs/surface_model_v1.md` §15 records (2026-10-07) is executed
here, at the scope that is true of the code. The registry
(`runtime/surface.py`: `Surface`, `SurfaceSet`, the one mint, the set-level
epoch, the prefix-scoped sync) is inert on every shipping tier — no canvas
defines `begin_surface`, so `wm_windowed`'s `_recording` is never set — and
nothing reads a gen: the P4 desk's retained caches and streaks
(`wm_windowed.py`'s stamp and desk streaks, `runtime/launcher_layer.py`'s
`_advance_streak`) are the Python window managers' and stay theirs until
sprint 7. Pass 1 ports the registry as a kernel table of kind SURF with the
same fields and the same three producer verbs, deletes the Python leaf, and
adds the one thing the kernel needs now: a signal in the other direction.

    int  moy_surface_get(uint32_t *h, const char *sid, uint8_t domain);   // created with fresh gens
    void moy_surface_touch(uint32_t h);      // Class A, attributed
    void moy_surface_move(uint32_t h);       // placement only (L1)
    void moy_surface_animating(uint32_t h, bool on);   // Class B
    void moy_surface_epoch(void);            // Class A, un-attributed: everything changed
    uint32_t moy_surface_content_gen(uint32_t h);      // folded with the epoch, compared !=
    uint32_t moy_surface_kernel_epoch(void); // the kernel's own draws bump it

**The kernel-to-WM signal.** The kernel draws under a Python window manager
from this sprint on — the idle wake, the screensaver, the parked web-console
screen, the floor's text, the HUD — and a Python gate that does not know
would leave the panel holding the kernel's last frame (the `IdleBlank`
behaviour its docstring numbers second). So `runtime/console.py`'s
`_needs_redraw` gains a leg: a kernel epoch newer than the one this console
last painted under reads as dirty, and stays dirty for as many frames as the
backend retains, so no buffer keeps the kernel's picture. The WMs' caches
need no change: a repaint under the gate is what they already do on a dirty
frame.

Why the table crosses now and not with the window managers: so that sprint 4's
VM-free cart has a producer it can reach — L9 holds for a cart with no VM,
content-dirty on exactly the frames it renders, and the signal must be a
kernel call — and so that the kernel's own draws have the epoch above to move.
The per-buffer N-deep last-seen of §4 and the predicate of the frame gate are
re-expressed in C in sprint 7, which is Phase C of that document's §9 and the
column its §7 ledger names. Until then, `runtime/console.py`'s frame gate
stays Python and folds `ws._dirty` into one `moy_surface_epoch()` per dirty
frame; the roughly 135 write sites across 31 files are untouched, which keeps
L6 true on the S3 (one C call per painted frame, none per write) and matches
§7's "writes never".

### 3.4 Present: three compositors, one contract

The backend interface `device/banded_panel.py` documents — size, framebuffer,
back buffer, gfx, flush, sync — is §4's contract plus the buffer handoff, and
the kernel's compositors implement it in C with the same split
`native/moy_flush/moy_flush.h` draws: the engine owns the frame state machine,
the board owns the transport.

    int moy_glass_present(uint32_t canvas_h);        // flush: swap, kick, return
    int moy_glass_fence(uint32_t canvas_h, int kind); // SYNC, SNAP, FRAME: no DMA reads what the caller is about to write
    int moy_glass_present_pending(void);             // the loop's pre-frame hook; a no-op on a banded board
    int moy_glass_end_frame(void);                   // §4's end_frame: the last write of the frame

- **Banded (T-Deck, Guition S3).** `native/moy_glass/moy_present_banded.c`
  is `BandedCompositor` and `FoldingCompositor` over `moy_flush` and
  `moy_fold`: the ping-pong, the drain-swap-kick flush, the game fold's arming
  from `blit_game`, the Guition's game-window sub-rect, the T-Deck's async
  layer copy. The two board panel modules (`moy_lcd`, `moy_axs`) keep the
  transport hooks they have.
- **DSI (Waveshare P4).** `native/moy_glass/moy_present_dsi.c` is
  `P4Compositor`: the three scan buffers `moy_dsi` owns, the deferred show,
  the fences the overlap counters meter, the PPA composite hooks `blit_game`
  and `blit_cover`.
- **Rotated DSI (Guition P4).** The same file with `angle` and the damage
  path: the persistent landscape paint buffers (rows, §3.1), the quiet-game-
  frame single PPA op, the damage rects the WM hands down, the bounce worker's
  bands. The WM's `note_damage` becomes `moy_glass_damage(x, y, w, h)` on the
  root canvas, as absent on the other backends as it is now.
- **The backlight** on every board is the panel entry sprint 2 gave the
  recovery floor (`MOY_KERNEL_PANEL(backlight)`); the two P4 display modules
  that own `set_backlight` today are deleted with it.

Whatever the kernel draws after the draw-stack upcall — the HUD, the floor's
text, the parked screen, the saver — is drawn before `end_frame`, because on
the P4s nothing may write a buffer after the frame's async PPA op has been
submitted, and the cache writeback precedes the submit (#58). The loop's order
(§7.1) holds that.

The stop inventory's three glass rows land here: the fold latch is disarmed
and its snapshot fenced by the kernel before any sweep; `moy_gfx`'s async copy
and the PPA's bounce worker are waited out by `moy_glass_fence(FRAME)`; and the
next present after a soft reset is the kernel's, because the compositor no
longer holds a Python buffer. The feeder task, the bounce slots and the scan
buffers are C-owned already and stay.

### 3.5 What Python is deleted

`device/device_canvas.py` shrinks to the binding (the verb names bound to the
gates, the `SystemCanvas` font-scale and layer riders, the host's
`RETAINED_FRAMES` probe); `_LayerComp`, `_LAYER_POOL`, `_LENT_BAKES`,
`_MaskedRegion`, the `malloc_dma` lane, `to_indices`, the `MAP_AUTO_CACHE`
machinery and every no-kernel Python draw lane go, and with the lanes
`tests/test_device_canvas_parity.py`'s `gfx=False` arm, which existed to run
them. `device/banded_panel.py`, `device/dsi_panel.py`, `device/p4_canvas.py`,
the four board panel modules, `runtime/surface.py` (its Phase A gates in
`tests/test_surface_model.py` re-pinned on the binding), `runtime/palette.py`
and `firmware/web_runner/web_canvas.py` are deleted. `runtime/host_canvas.py`
and `runtime/gfx_binding.py` rebind to `moy_glass` built for ctypes;
`runtime/moyhost_gfx.c` goes with the compositor it duplicated.

### 3.6 Absorbed: #126's screenshot

`moy_glass_capture` hands back a frame as MOY64 indices through the reverse
lookup `to_indices` runs today, and reports whether the lookup was exact. A
frame drawn through palette verbs is exact and is written through `moy_fs` as
an indexed `.moyimg` under the user's drawings — the store's own picture
format, which is indexed only, so Files and Paint open it and it can be a
wallpaper with no new reader. A frame that is not — a compiled cart's
direct-colour frame, a raw-565 cover composite, a cart-palette frame — is
quantised to the nearest MOY64 entry in C, outside the frame, and the file's
name says so; a PNG encoder would keep the colours and is a new module, which
is the trade §13's question 6 puts beside the GIF. The trigger is the dev
channel's `shot` word, a new word; the ≡ menu's action comes with the in-cart
chrome in sprint 4.

### 3.7 Per-board facts that bite

- **T-Deck.** The card shares SPI2 with the panel: `moy_lcd` owns the host,
  `sync()` is what restores exclusion between a flush and a card session, and
  the pins `TFT_CS` and `SD_CS` are never re-created once a driver has them.
  The feeder's bands do not hold the bus lock (the no-acquire patch), so a card
  transfer slots between bands by design. The kernel's present keeps every one
  of these as the panel module has them.
- **Guition S3.** The panel's MADCTL rotate is dead on this glass, so landscape
  is the band copy's and the fold's; the game window ships a sub-rect after the
  first full frame lays the bezels; the card is on SPI3 and shares nothing.
- **Waveshare P4.** PSRAM at 200 MHz and the 256 KB L2 cache are the scan-out's
  floor; the DSI underrun hook patch keeps the restart interrupt at priority 3
  under ESP-Hosted bursts; a 1:1 full-screen copy is bandwidth-bound and the
  PPA wins only on upscales (§8 of the surface model).
- **Guition P4.** Every full frame is a whole-buffer rotate; a damage frame
  rotates rects; a block rotated through the bounce worker is copied by the
  GDMA, never by the CPU inside the call, in full-width bands, with the last
  band overlapping rather than ending in a sliver — the three lessons its
  README records, kept as asserts in the C.
- **Both P4s.** The PSRAM-by-rule test exempts what the engine must read
  fast: the crisp-composite SRAM bands, the rotate bounce slots and the scan
  buffers (the engine reads SRAM about four times faster than PSRAM,
  `device/dsi_panel.py`'s notes and #58).
- **Every board.** `git add` a new module before the first build, or the
  stager prunes it; a bug in present reboots five boards, so the pass runs on
  the host and the desktop MicroPython until the crc traces hold, and only then
  on glass.

### 3.8 The gate

| gate | host | on glass |
|---|---|---|
| the raster is unchanged | `tests/test_spec_conformance.py` and `tests/test_gfx_binding.py` over `moy_glass`; every pixel golden on every row unchanged; the glass trace on both object models | `tools/p4_conformance.py` on both P4s, and its banded equivalent on both S3s through `moy_glass_capture`: zero differing pixels on every scene |
| the gates survive | a grep-test that no verb of the binding is a Python `def` over a C call | `tools/p4_cart_bench.py` µs/op, best-of-8, on both S3s and both P4s: every verb within noise of #66's recorded row for that board; the `--uncap` roster (`tools/board.py perf --uncap`) on both S3s within noise of its last recorded run |
| the pool is the kernel's, bounded, and the share holds | the pool's rows asserted through the twin's shapes by owner class and origin; a fuzz of new/release/reclaim under ASan | the cycle set on each S3 — Sky Run, Hop Quest, a scaled 128×128 Lua cart alternating with a 320×240 cart, twenty opens and closes each — with `heapcaps` PSRAM free AND largest block flat, HEAP-origin rows zero, BUF rows at peak under 256, the pool within the board's bound; the census (`tools/mem_census.py`) fresh and after its scripted session names no retained owner above the share; Doom's fit on the Guition S3 after the scripted session, five times |
| present is the kernel's on three compositor kinds | the compositor state machines as pure functions over a fake transport | each console's on-glass suite unchanged; `display_underruns_are_zero` on both P4s; `tools/p4_surface_sweep.py` drag medians and `tools/p4_clicks.py` transitions on both P4s within noise of #58's last sweep, read as frame milliseconds, not fence counts; per-stage meters (#210) from `state` with no stage's overrun count above its last recorded run |
| the kernel survives a VM teardown | — | `kstop 100` (§7.5) on the Guition S3 and the T-Deck with the feeder armed and the fold live: PSRAM and internal free and largest flat, and the first frame after each restart presented by the kernel's compositor with no re-initialisation line |
| a screenshot is a picture | the capture's indices equal the host raster's for every golden scene; the inexact path's nearest-entry quantisation pinned on three fixtures | `shot` on each console writes a `.moyimg` Files opens |
| the standing items | §9 | §9 |

## 4. Input (pass 2)

### 4.1 The table

One `moy_input` table for every tier: the fifteen buttons in libmoy's
`moy_button` bit order (the host's eight were already in it; the boards'
table is re-ordered to it, with the seven console-only names after), held and
pressed masks per player, `last_key`, text mode, the pointer's place, down,
fresh and click, and a source table of kind SRC — the keyboard, the BLE
keyboard, the touch, the trackball, the browser, the net slots — each owning a
held set and a key. The state every surface reads is the merge, taken once
per frame at `moy_input_begin_frame`, which is where edges are computed.

    int  moy_input_source(uint32_t *h, const char *name, uint8_t player);
    void moy_input_set_held(uint32_t src, uint8_t button, bool held);   // from a task or an ISR
    void moy_input_key(uint32_t src, int key);               // one-shot: delivered for exactly one frame
    void moy_input_release_all(uint32_t src);                // this source holds nothing
    void moy_input_pointer(uint32_t src, int x, int y, bool down);
    void moy_input_begin_frame(void);                        // the latches merged, the edges computed
    void moy_input_masks(uint8_t player, uint32_t *held, uint32_t *pressed);   // player 0 is the union
    int  moy_input_text_mode(bool on);                       // flips the T-Deck keyboard's mode
    void moy_input_snapshot(int32_t *arr);                   // the array a Lua cart's btn() reads

`masks` keeps both of `button_masks`' arguments because
`device/moybyte/input.py` records why each exists: a mask packed in the wrong
order gave every Lua cart a d-pad rotated a quarter turn, and a mask packed for
the wrong player is the same silent failure. The order is now the header's
constant rather than a caller's tuple; the player argument stays, and
`runtime/lua_ext.py`'s snapshot and `runtime/players.py`'s slots call it as
they call `button_masks` now. The one host behaviour that changes — the host
gains the seven console-only names — is pinned in the input trace. The dev
channel's `tap`, `swipe` and `drag` inject into a source of their own, through
the same table, so a scripted gesture is indistinguishable from a finger.

**Writers are not frame code.** The sources are written by ISRs, by the
poller task and by NimBLE's host task, and the table is PSRAM by rule, which
an ISR cannot touch while the cache is off for an internal-flash write. So
each source has a latch in internal RAM — its held bits, its key, its pointer
sample — written under a `portMUX` spinlock, and `begin_frame` merges the
latches into the PSRAM table once per frame. The latches and the ISR text in
IRAM are in §8's ledger.

### 4.2 The drivers

Each driver is a kernel task or an ISR writing into its source's latch; none
touches a Python object.

- **The T-Deck keyboard.** The C3 at I2C address 0x55 in its two modes:
  ASCII (one byte per press edge, shift and sym resolved on the keyboard) and
  raw matrix (five bytes, level state). `decode_raw` and the `KEY_BUTTON` table
  are the C decoder, pinned by `tests/test_tdeck_keymap.py` over ctypes. The
  mode flip is one C function: `0x03` on entering a game, `0x04` and the drain
  on leaving, the typed byte seeded into `last_key` — the hazard the README
  records closes because there is no GIL window between the revert and the
  drain. The hold latch (`KEY_HOLD_MS`) stays for the ASCII mode. Raw needs
  keyboard firmware of 2025-06-12 or later; an older unit is detected by its
  stray ASCII byte and kept on the latch path, as today.
- **The input poller task.** The I2C0 transactions — keyboard, GT911, the
  deferred mode writes — move from a Python thread to a kernel task on core 0,
  paced one pass per frame by a notify from the loop's input stage. A
  clock-stretch stall (tens of milliseconds, measured in #69) blocks that task
  and nothing else; the `I2CSTAT` counters keep counting from it. The build's
  I2C GIL-release patch has no caller after this and leaves the board's patch
  list. The stop inventory's row lands here: the task is stopped
  cooperatively and joined before any VM teardown.
- **The trackball and the touch INT gate.** Four direction pulses and a click
  on GPIO IRQs, and the GT911's INT edge counter that gates its reads, become
  kernel ISRs in IRAM.
- **GT911 (T-Deck, Waveshare P4)**, **GSL3680 (Guition P4)**, **AXS15231
  (Guition S3)**: the shared core `device/gt911.py` factors (the held point,
  the mapping) is one C body; the GSL3680's firmware bytes are a C array and
  its upload runs after the splash's first frame, behind a lit screen, as the
  spine's `inputs()` ordering has it; the AXS touch reads portrait panel
  coordinates on the panel's own I2C0. On both P4s the touch controller's bus
  is the codec's too: the bus is the kernel's object (§2 item 6), input brings
  it up, audio adds its device.
- **BLE HID below `bluetooth`.** `device/ble_keyboard.py`'s whole protocol
  path — scan for 0x1812, connect and bond, discover the Report and Boot
  Keyboard Input characteristics, enable their CCCDs, consume reports — moves
  onto the NimBLE host's C callbacks, with `native/p4/moy_ble_hid/`'s
  notification queue as its seed, on every console (the S3s' on-die radio,
  the P4s' C6 over ESP-Hosted). The kernel owns the NimBLE host's lifecycle.
  Bond secrets go through the NimBLE store callbacks to `moy_fs` as it is in
  1b, beside the chosen address and name, so nothing here waits on the links
  pass. The Settings panel's scan, pick and forget are calls on the kernel's
  device list.
- **The browser and the host.** `runtime/web_input.py`'s event batch decodes
  in the web build's C into the same table; the host's SDL driver writes the
  table through ctypes.

### 4.3 The soft-reset list

The VM service's `soft_reset_exit` is `mp_task`'s, copied and pinned
(`native/moy_kernel/mp_task_calls.txt`), and today it tears down what this
pass makes the kernel's: `mp_bluetooth_deinit` stops the NimBLE host,
`espnow_deinit` the radio's receive path, and `machine_pins_deinit` strips
every pin in the port's table of its ISR, the kernel's included. A Ctrl-D
would drop the keyboard, the trackball and the link. So this pass changes the
copy in three MOY-marked places, and the record is regenerated under that
review: `MICROPY_PY_BLUETOOTH` and `MICROPY_PY_ESPNOW` are off on the four
consoles, because their only users are the two drivers that cross (the Zero
never had them); the P4's `patches/p4_modbluetooth_ble_hid_fastpath.patch`
retires with the module it patched; and the pin sweep becomes the one the
plan's §4.4 asked for, a sweep of pins with a Python handler, so a kernel ISR
survives. The links pass makes the same guard hold for its receive ring.

### 4.4 Absorbed issues

- **#26 beyond keyboards.** A boot-protocol mouse is a second HID report
  shape over the same central: buttons and deltas into a pointer source, the
  windowed desk's cursor already consuming one. A gamepad is a third, and
  gamepads do not share one report layout: a generic report-descriptor parser
  is a thousand lines of C that the owner should ask for by naming the
  controllers (§13, question 4). Multiple devices at once is the central's
  connection table, four on either stack.
- **#196.** The decoder owns both keyboard modes in C whatever the answer;
  the always-raw question is §13's question 5.
- **#83.** The P4's USB HID host would be a transport under the same report
  decode as BLE; whether it is built is §13's question 3.

### 4.5 What Python is deleted

`device/moybyte/input.py` and `device/moybyte/__init__.py`, `runtime/input.py`,
`runtime/web_input.py`, `device/device_input.py`, `device/gt911.py`,
`device/gsl3680.py`, `device/axs_touch.py`, `device/ble_keyboard.py`, the
boards' input modules, `gsl_fw_jc8012.py` and `runtime/moy_input.py`'s twin
body. `tests/test_tdeck_keymap.py`, `tests/test_tdeck_input.py` and
`tests/test_ble_keyboard.py` keep their cases over the binding.

### 4.6 Per-board facts that bite

The T-Deck keyboard and its touch share I2C0, and the keyboard clock-stretches
for tens of milliseconds; the `I2C_TIMEOUT_US` cap of 5000 stays. The
Waveshare's BLE shares the C6's SDIO with WiFi, needs the 64-packet ACL pool
and the 12 KB NimBLE host stack its README records, and only HOGP keyboards
exist to it. The Guition P4's GSL3680 reports in a 1664×896 firmware space
that is scaled, and its upload is the slow part of the boot. The Guition S3's
touch shares the AXS15231B bridge with its panel. The T-Deck's on-die BLE
shares the radio with WiFi and ESP-NOW, and sprint 0's internal-SRAM baseline
was taken with both radios up, so the host stack's cost on the S3s is in the
baseline already; the C central's own statics are not, and §8 counts them.

### 4.7 The gate

| gate | host | on glass |
|---|---|---|
| one table, every tier | the input trace on both object models, both masks for players 0 and 1; `test_tdeck_keymap` (both modes agree, every key), `test_ble_keyboard` (report decode) over ctypes; a fuzz of the HID report decoder | `cart_runs_and_exits`, `home_shelf_fling`, `idle_blank_and_wake` on every console, driven through the kernel's injected source |
| the drivers call no Python | `tests/test_no_vm_calls_in_drivers.py`: the task and ISR sources of `native/moy_input/` contain no call into the VM (`mp_call_function*`, `mp_sched_schedule`, no `mp_obj_t`) | the image stages no Python driver: `py __import__("moybyte.input")` fails on every console |
| the T-Deck's drivers are the kernel's | — | the keyboard smoke's A/B on glass; `I2CSTAT` maxima unchanged with the poller task; raw-mode hold-to-move in a game and clean typing in the editor; the trackball's four directions and click |
| the drivers outlive the VM | — | `tools/board.py BOARD reboot --soft` with a bonded BLE keyboard and, on the T-Deck, the trackball: after the soft reset the keyboard types and the ball rolls with no re-pair and no re-init line; then `kstop 100` (§7.5) on the T-Deck with the poller task and the keyboard alive — the spike the plan's §11 owes before sprint 4 relies on stops |
| the share holds | — | §9's internal-SRAM items, with the poller task's stack and the latches in the link-map delta |

## 5. Audio (pass 2)

### 5.1 Sessions and the verb face

The synth is libmoy's and already C (`native/moy_audio/`): the bank, both
sequencers, the mixer, the core-1 feeder task that blocks on the DMA drain,
the stream a compiled cart mixes in. What is Python is thin and scattered —
`device/device_audio.py` pushes the bank once per cart and forwards six verbs,
`runtime/audio.py`'s `AudioEngine` holds the host's playback, and the
engine is constructed per cart by `runtime/project_store.py`, wrapped by
`runtime/host_api.py`'s `FakeAudio` and `web_boot.py`'s `_RunnerAudio` (a
Python class over it), previewed by `runtime/music_editor_ui.py`, silenced by
`runtime/audio_session.py`'s `_SilentAudio`, driven by `runtime/wallpaper.py`'s
run and drained by `device/moycore_glue.py`. The one global verb face those
share has no owner, which is wrong on the P4 desk, where two cart windows and
the Music editor's preview can hold banks at once. The crossing gives the
module sessions:

    int  moy_audio_open(uint32_t *s, uint32_t owner, const char *bank_json, size_t n);
    int  moy_audio_bank(uint32_t s, const char *bank_json, size_t n);   // on change of the bank's rev
    void moy_audio_focus(uint32_t s);                       // the audible session; the others are muted, not stopped
    void moy_audio_sfx(uint32_t s, int n, int chan);
    void moy_audio_beep(uint32_t s, int freq_hz, int dur_ms);
    void moy_audio_music(uint32_t s, int track, bool loop);
    void moy_audio_music_stop(uint32_t s);
    void moy_audio_stop(uint32_t s);
    void moy_audio_close(uint32_t s);                       // voices silenced, bank freed
    void moy_audio_volume(int level);                       // 0..7, the settings row, read at boot
    void moy_audio_hush(void);                              // every session silent within a block
    int  moy_audio_sample_load(uint32_t *h, const int16_t *pcm, size_t frames, int rate);   // #70's voice
    void moy_audio_sample_play(uint32_t s, uint32_t h, int chan);

A session is a row (kind AUDIO, few) owned by an OWNER handle (§3.1) — a cart
run, the wallpaper, the Music editor — and `focus` is what the Player and
the desk's focus change call. The legacy feed — `machine.I2S` driven from
`tick()` when the core-1 task failed to start — is deleted, as the stop
inventory has it; a board whose task cannot start has no audio, reported as
absence. The I2S channel and the feeder task keep starting at the first
session, not at boot: the perf review's point that their internal memory is
paid with the first cart today and should stay paid then. `device/moycore_glue.py`'s
drain stops going through `make_api` closures and calls the session's verbs,
one C call per queued op, in the queue's order. The master level is a settings
row the kernel reads; the Settings screen writes the row. The AUDIORATE probe
and the trigger lines are the kernel's and gated by PERF DIAG as every periodic
line is. The host's `runtime/audio_binding.py` rebinds to the same module built
for ctypes, its `render` pulled by SDL and by the web runner's `take_pcm` as
now.

### 5.2 Absorbed issues

- **#82, the ES8311 on both P4s.** The codec is an I2C register sequence at
  address 0x18 (the factory firmware and Waveshare's demo carry a known-good
  one), the PA enable a GPIO (53 on the Waveshare, 20 on the Guition P4), the
  I2S a standard channel on the pins both READMEs list. It lands as a board
  define on the one module, `MOY_AUDIO_CODEC_ES8311`, the way the DSI panel is
  a define, on the kernel's I2C bus beside the touch controller; both P4s flip
  `moy_audio` from denied to taken in board.toml. The feeder task's design is
  the T-Deck's and is not re-measured against a per-frame feed, because the
  per-frame feed is the one being deleted. The Guition S3 stays denied: its
  amp and pins are unverified, and verifying them is a bring-up, not a
  crossing. The plan's §6.2 puts #82 in this sprint; the gate is the one every
  board's audio takes (§5.5).
- **#70, sound packs.** The mechanism is a sample voice in the mixer at the
  point where a compiled cart's stream is already added (`moy_audio_snd.h`):
  clips in PSRAM, resampled to the output rate at load, played by slot on a
  session's channel under the master level. The issue's shape — a family-wide
  `sound_packs/` folder in the store with templates, labels per slot and
  recordings that never leave the device — makes a pack store content under
  the user's files, loaded by a cart into cart memory on demand, never the
  kernel's share. The cart-facing verb (`load_pack`, `play`) is a change to the
  public verb table, which is moy-spec's; how much of the issue this sprint
  takes is §13's question 2.

### 5.3 What Python is deleted

`device/device_audio.py`; `runtime/audio.py`'s `AudioEngine` (the bank model
stays); `runtime/audio_session.py`'s `_SilentAudio`, `runtime/host_api.py`'s
`FakeAudio` and `web_boot.py`'s `_RunnerAudio` (a console with no backend
holds no session and the binding's verbs are no-ops; the web runner's PCM
sink pulls the kernel's `render`); `runtime/moyhost_audio.c` where the host
library replaces it; the drain's closures in `device/moycore_glue.py`; the
per-cart engine construction in `runtime/project_store.py` and
`runtime/wallpaper.py`, which open sessions instead.

### 5.4 Per-board facts that bite

The T-Deck's amp is on its own pins (GPIO 7, 5, 6) behind the board power
gate, so audio never meets the panel-and-card bus; the output rate is 22050
because the synth's character is defined there (SPEC.md 8.3), and the feeder
and a compiled cart's thread share core 1, which is why the feeder's work per
block stays small. On both P4s the codec and the quad microphone share the
touch controller's I2C bus, and the PA enable pin differs between the two
boards. A sound that is not heard is not proven by a test that passes: the
instruments are the digital self-dump and the rate probe with its `seam=`
counter, which read the rendered-versus-written seam with no ear in the loop
and which caught a 37% loss once that every per-side clock had certified.

### 5.5 The gate

| gate | host | on glass |
|---|---|---|
| the synth is unchanged | `tests/test_audio_parity.py` bit-identical across the binding and the desktop MicroPython; the session trace (two sessions, one focused, the muted one's verbs leave the render unchanged) | the T-Deck's self-dump of a seed cart's music equals the host's render; the AUDIORATE line over thirty seconds of music with a cart running reads a cumulative ratio of 1.000 and `seam=` at 1.0000, on the T-Deck and both P4s |
| the P4s make sound | — | the ES8311's registers read back as the sequence wrote them after init; then the two instruments above; the seed carts' sounds and the Music tab's preview, owner-heard once |
| the feed calls no Python | `tests/test_no_vm_calls_in_drivers.py` over the feeder's sources | a Lua cart's sfx reaches the mixer through the drain with one binding call per queued op, counted by the session trace on the desktop MicroPython |
| a stop silences | — | `hush` from the dev channel while music plays: silence within one block, `seam=` unchanged after |
| the lazy start holds | — | internal free and largest at the idle desk before the first session unchanged from the pass before (§9) |

## 6. The links and the internal volumes (pass 2)

### 6.1 The radios

- **WiFi.** `native/moy_net/moy_wifi.c` is the driver's life: init once,
  scan, connect with the saved credentials, autoconnect at boot, the
  `wifi.json` store through `moy_fs`, power down and up as the spine's lease
  mask asks. The lease is sprint 2's and stays where it is; the driver only
  answers it. The internal-SRAM facts `device/cart_net.py` records — the
  receive buffers the driver takes on first start and keeps, the TLS working
  set a download needs — are the kernel's to report, not to hide.
- **ESP-NOW.** `native/moy_net/moy_link.c` is `device/moy_espnow.py`'s
  discovery, pairing and the two-console link over the esp_now API — on-die on
  the S3s, through `native/p4/moy_c6/`'s shim on the P4s, which already
  implements that API. The receive ring is the kernel's, in PSRAM, fed from the
  WiFi task's callback through an internal latch like an input source's, so a
  soft reset cannot orphan it and `espnow_deinit` has nothing left to call
  (§4.3). The lockstep over it, the `net0` input slots and the Player's arming
  are sprint 4's.

### 6.2 The HTTP core, the webhost and the sync RPC

`native/moy_net/moy_http.c` is `device/moy_webserver.py` over BSD sockets:
the request parser, the sized, chunked, file and blob responses, the
non-blocking listener polled once per frame. It builds on lwip and on POSIX
alike, so the host runs it under the fuzzers. `+native/moy_net/moy_webhost.c`
is the routes: the baked bundle from `native/moy_web/` zero-copy, the live
store packed from the C catalogue (1b's), the user files, the PIN gate on
every write, `/sync`, `/run`, `/update`, the goodbye window. The sync codec
(`runtime/moy_sync.py`'s batches, base64 payloads, roots, the journal commit
per published cart file) is one C body for both ends: the webhost applies
batches through `moy_fs` and `moy_journal`; the browser's half
(`carts_link.py`, `update_link.py`, `gpio_link.py`) is the same code in the
web build, with fetch, OPFS and the file picker as JS imports. The
`StoreWatcher` that detects changes for the push half reads the store's index
rows.

### 6.3 The updater and its clients

`+native/moy_net/moy_ota.c` is the updater: the streaming HTTP(S) client with
redirects (also Get Carts' transport), the manifest's verification under the
scheme `tools/ota_sign.py` defines, the write into the inactive slot, the card
path from `/moy/update`. The health half — the boot verdict and the confirm
after painted frames — is the loop's (§7). `+native/moy_net/moy_c6_update.c`
is the C6's updater over ESP-Hosted's RPC with the block's own signature.
The recovery floor gains an `update` word that drives this updater with no
VM, from a card image or a URL, so a console that cannot start its VM can
still take a release (§10). The TLS stack is the platform's on a board; the
host exercises parsing, verification and the state machine over plain sockets
and the signature vectors, as now.

### 6.4 The web-console switch

`runtime/web_console.py` — the pairing pin, the paired URL, parking the glass
while a browser edits the store — is `+native/moy_net/moy_webconsole.c`. The
screen the glass parks on (`runtime/web_console_ui.py`, with its QR from
`runtime/moy_qr.py` and its two buttons) is a themed, interactive screen
under the pixel goldens; whether the kernel draws a plain one in its place,
so that a console whose VM is down can still show it, or the Python screen
stays above a native switch, is §13's question 8. The switch itself crosses
either way, and the park is a kernel epoch (§3.3).

### 6.5 The Zero

The Zero's modules cross (the plan's open placement, §10): one C webhost
serves five boards, the Zero's GPIO verbs become a board allowlist table, its
first-run access point is `moy_wifi`'s provisioning mode, and `zero_host.py`
dissolves as each console's `moy_runtime.py` does. The Zero takes `moy_kernel`
in this pass too — the spine doc deferred it to the sprint that makes the
kernel the entry on every target, and this is it; its floor is serial-only,
since it has no panel. It keeps the port's REPL as its console. What this
costs its image is §9's business every pass, not only this one.

### 6.6 The internal flash volumes

Sprint 1b borrowed the littlefs instance from the mount table under nlr, and
its design records why `VfsLfs2` cannot wrap a kernel-owned one: its callbacks
call the block device's methods and its working directory is a gc buffer. So
this pass has `native/moy_store/moy_vol.c` own the instance — the kernel's
`lfs2_t` with C callbacks over `esp_partition`, mounted at start — and gives
Python a VFS type of the kernel's over it, modelled on the port's own
littlefs VFS: mount and umount, open with the file object's read, write, seek
and close, `ilistdir`, `mkdir` and `rmdir`, `remove` and `rename`, `stat` and
`statvfs`, `chdir` and `getcwd`. What lives there is the kernel's to keep
across a VM teardown: the system documents on a no-card board, the staged
update, the BLE bonds, the Zero's whole store, the embedded floor's read-only
built-ins when sprint 4 needs them; a compiled cart's file calls on these
volumes go through the owned instance with no VM gate (the wasm session's row
of the plan's §4.4). An internal-flash write stalls flash-resident code on both
cores while the cache is off, so the feeder's band pump is paused for the
write's duration; the gate holds the feeder's errors at zero through it.

### 6.7 What Python is deleted

`device/device_wifi.py`, `device/moy_espnow.py`, `device/moy_webserver.py`,
`device/moy_webhost.py`, `runtime/moy_sync.py`, `device/cart_net.py`,
`device/moy_c6_update.py`, the updater half of `device/moy_ota.py`,
`runtime/web_console.py`, the three `*_link.py` files of the web runner, and
the Zero's `zero_host.py`, `zero_gpio.py` and `zero_setup.py`.
`runtime/host_api.py`'s service fakes shrink to what the host's harness still
stands in for.

### 6.8 Per-board facts that bite

The Waveshare's SDMMC slot 1 belongs to the C6 and constructing it panics the
board; the Guition S3's card is on SPI3 but its slot numbers invert against
host numbers; a card that will not mount leaves its bus up. The Zero has no
dev channel and a Ctrl-C takes its webhost offline until a Ctrl-D, its USB
port re-enumerates under a new name, and it holds every module twice across
8 MB of flash, so each C module here is weighed against its 256 KiB floor —
which it approaches already, the web bundle being most of what it carries
(#224). The browser's half is a derived artifact: the kernel's modules enter
the web build through the pinned emscripten container, and `tools/preflight.sh`
compares the baked blob to its source. New IDF components do not enter
through a usermod's cmake on the P4 build; a dependency the net code needs is
added to the board's component list.

### 6.9 The gate

| gate | host | on glass |
|---|---|---|
| the wire is unchanged | the HTTP parser and the sync codec fuzzed under ASan and UBSan; a batch round-trips against the browser's; the manifest verifier against `tools/ota_sign.py`'s vectors and the tamper cases; the links trace | `web_console_is_baked_into_the_image`, `wifi_status_is_readable`, `wifi_is_off_at_rest` on every console; the Zero re-provisioned and paired from Chrome (`tools/web.py shot`) and its suite green |
| an update still updates | the OTA state machine on a canned manifest | a beta pushed over WiFi to each console through the unstable channel (the `release` skill) and confirmed; an image armed to never confirm rolls back on one S3 and one P4; the C6 updated on both P4s; the floor's `update` word takes a card image |
| the link links | the peer table and the pairing state machine as pure functions | two consoles paired through `link`, a cart beamed, and a 200-message burst delivered whole at the link's set rate — the acceptance `device/moy_espnow.py` records for its ring size — with the figures to #7 |
| the drivers call no Python | `tests/test_no_vm_calls_in_drivers.py` over the WiFi callback, the link's receive path and the HTTP poll | the image stages none of §6.7's modules |
| the volumes are the kernel's | `tests/test_store_on_vfs.py` over the owned littlefs through the kernel's VFS type; the power-cut matrix re-run | commit, reboot, intact on every console and the Zero; ten store commits under a running cart on both S3s with the feeder's `tx_errs` at zero and `display_underruns_are_zero` on both P4s, the longest frame recorded to #224 |
| the Zero fits | — | its headroom above the floor after this pass, with the per-pass budget of §9 |

## 7. The frame tail (pass 3)

### 7.1 The loop

`+native/moy_kernel/moy_loop.c` is `FrameLoop.step` in C, in the order that
class's docstring guards: the pump's head, every input source, the dev channel,
the idle ladder (after every input, so the waking touch is swallowed), the
pointer, `present_pending`, the three upcalls (`handle_input`,
`handle_pointer`, `frame`), the kernel's own draws, `end_frame`, the
first-frame backlight gate, OTA health, the tail polls (webhost, link, diag
cadence), pace, account, the watchdog feed. The stage meters (#210) are the
kernel's, with the same stage names and budgets. A frame error in an upcall is
caught under nlr and printed as the board prints it today; whether it counts
against an app is the ledger's rule (sprint 2), which this sprint does not
change.

    int  moy_loop_step(void);                         // one frame; returns QUIT when the channel asked for the REPL
    int  moy_loop_run(void);                          // until QUIT or a VM teardown
    int  moy_loop_upcall(int which, mp_obj_t fn);     // register one of the three; refused while no VM runs
    uint32_t moy_loop_upcalls(uint32_t *console, uint32_t *app, uint32_t *driver);   // per frame, by class

**Who runs it, and how it is safe (2026-10-07).** The plan's §4.4 rules out a
kernel that runs inside the VM's task, and a VM cannot tear itself down with
its own frames under a loop. So:

- `moy_loop_run` is the outermost frame of the VM service task. The console's
  Python boot registers the three upcalls and RETURNS; the service then calls
  `moy_loop_run()` where `mp_task` would enter its REPL loop, and the loop
  calls Python back on the same task through the port's callback path. No
  Python frame is below the loop, so the service's teardown list runs with
  nothing of the loop's on the stack.
- The three upcalls are registered root pointers (`MP_REGISTER_ROOT_POINTER`),
  never a `mp_obj_t` held in kernel state (the plan's §4.3); `soft_reset_exit`
  clears them, a MOY-marked line in the copy, and a registration or a call
  while no VM runs is refused, not attempted.
- `moy_loop_step` keeps no state on the stack between frames: the loop's
  state is one static struct, so the task that calls `step` can change between
  any two frames.
- A small kernel task, `moy_loop_task`, internal stack sized at the gate and
  pinned to the VM's core, drives the loop while no VM runs: across a soft
  reset's window today (the glass keeps presenting while the VM restarts) and
  through sprint 4's stops. Both tasks that may drive the loop have internal
  stacks, so an internal-flash write from the loop needs no deferral.
- The loop trace drives its last ten frames from a second thread (§2 item
  8), which is the host's form of the same property.

**The consequence for the plan's §6.1 (2026-10-07).** While the VM runs the
loop adds no task, so the kernel's internal cost is unchanged by it. While the
VM is stopped (sprint 4) the VM task's stack returns, as the plan's §4.4 has
it, and `moy_loop_task`'s stack is the cost in its place — smaller than the
stack it replaces, so §6.1's "nothing net while stopped" holds with that task
counted and is read as "no more than the loop task's stack net while
stopped", measured at the gate. In the browser the worker calls
`moy_loop_step` where it calls `step_frame_json` today; on the host
`runtime/host_app.py` calls it through ctypes. Three tiers, one function, the
plan's §4.2.

**Upcalls are counted by class.** Console upcalls are the three draw-stack
entries; app upcalls are an app's hooks; driver upcalls are a tier's harness
(the host's SDL glue, the browser's JS). The gate after this pass is that a
frame's console count is three and its service count is zero on every tier;
sprint 4's gate is zero console and app upcalls on a VM-free cart.

### 7.2 The pump, idle and OTA health

The pump is `FramePump` in C: the clamped dt, the cadence debt, the
sleep-overshoot slack against the 10 ms FreeRTOS tick, the published slot the
stage budgets are cut from. OTA health is `device/moy_ota_health.py`'s two
halves: the verdict read on the boot path before anything can overwrite it,
and the confirm fired from the loop after real painted frames; the verdict is
read by `runtime/console_notices.py`'s banner as a kernel flag.

`+native/moy_kernel/moy_idle.c` is `IdleBlank` with the three behaviours its
docstring lists kept — the wake tap swallowed, the kernel epoch moved on wake
so the Python gate repaints (§3.3), an explicit blank outranking activity —
generalised into a ladder whose rungs are settings rows, runtime-settable as
`power N` sets the timeout today, with OFF a value:

1. **dim** after `idle_dim_s`: the backlight ramped down by LEDC where the pin
   drives one, absent where it is binary;
2. **screensaver** after `idle_saver_s`: what it shows is §13's question 7;
3. **blank** after `idle_blank_s`: today's behaviour, the board rendering
   while dark;
4. **sleep** after `idle_sleep_s`: light sleep with wake on the input ISRs,
   refused while a WiFi lease is held or the webhost serves.

Which rungs this sprint builds, on which boards, and #130's low-battery rung
are §13's question 1; the ladder carries the hooks for all of them.

### 7.3 PERF, the HUD, serial and diag

`+native/moy_kernel/moy_perf.c` is `PerfSampler` and `PerfMeters`: one field
set from one accounting path, the compositor's overlap counters read raw, a
lever the board lacks printed as `-`, nothing formatted while PERF DIAG is off.
`runtime/perf_line.py` keeps its parser for the host tools and loses its
formatter; `perf_line_is_the_one_format` holds the C formatter to the parser.
`runtime/perf_hud.py` crosses as the plan has it: the FPS chip and the
frame-time breakdown are the kernel's draw after the upcall and before
`end_frame`, through the system canvas.

`+native/moy_kernel/moy_devch.c` is the dev channel's reader (over
`native/moy_serial/`) and word table. The words that act on kernel state —
`state`'s kernel fields, `heapcaps`, `mem`, `bl`, `vol`, `power`, `diag`,
`uncap`, `crisp`, `web`, `link`, `shot`, `hush`, `kstop`, the `k*` test words,
`recv` and the tier-1 sideload words — are C. The words that act on the
console (`run`, `open`, `tap` on a named button, `py`) are registered by Python
as upcalls, the tables the carve made (§2 item 3); `py` is an upcall by
definition and stays. One vocabulary on every board, a declined word naming
what the board lacks.

`+native/moy_kernel/moy_diag.c` is the ring `device/moybyte_diag.py` keeps and
its flush to the card on the T-Deck's cadence through the owned card volume,
plus the PUMP, HITCH and LOOP lines `device/device_diag.py` formats.

### 7.4 The boot order

`+native/moy_kernel/moy_boot.c` is `build_desktop`'s order: the splash (whose
drawer is §13's question 9), the store (C), the runtime probe (Python until
sprint 4), the Workstation and the window manager (Python until sprint 7), the
services wired as kernel handles the Python side reads, the OTA verdict, the
loop. `mem_census.mark` stays on the path with its names. The boot is timed at
every pass (§9).

The board glue dissolves: each console's `moy_runtime.py` and the carve's
provider modules become the per-board configuration in `mpconfigboard.h` and
board.toml — pins, the panel and codec defines, the idle rungs' defaults, which
services the board has — and `tests/test_board_service_parity.py`'s wiring
table reads board.toml. The bring-up smokes and `device/boot_shell.py`'s mode
ladder are answered in §10.

### 7.5 `kstop`, the test-only teardown

There is no VM stop on dev until sprint 4 (the spine doc's §8), and sprint 0's
spike is not in the tree. What exists is the VM service's soft reset, which
runs the whole teardown list of §4.3 and starts a VM again. `kstop N` is that,
N times, counted: a dev word the kernel answers by running the service's
soft reset cycle with the kernel's drivers alive and printing `heapcaps`
before and after. It lands in pass 1 (the glass is what the first cycles must
survive), when the panel holds its last frame across each window as it does
through a Ctrl-D today; from pass 3 `moy_loop_task` drives the loop across the
window and the glass keeps presenting. It is test-only, not in the vocabulary
a kid can reach. It is not sprint 4's stop — no VM task is deleted — and
sprint 4's gates stay sprint 4's; it is the executable guard that a teardown
leaves the kernel's drivers alive, which this sprint can run and the plan's
§11 asked for.

### 7.6 What Python is deleted

`runtime/frame_loop.py` (the carve's file, in full), `device/desktop_spine.py`'s
boot half, `device/device_diag.py`, `device/device_util.py`,
`device/moybyte_diag.py`, `runtime/console_perf.py`, `runtime/perf_hud.py`,
`runtime/dev_channel.py` down to the registrations, `device/moy_ota_health.py`,
each console's `moy_runtime.py` and the carve's provider modules,
`device/p4_desktop.py`'s present wiring, `device/boot_shell.py` and the smokes,
`firmware/web_runner/web_boot.py`'s `step_frame_json`.

### 7.7 Per-board facts that bite

The T-Deck's card bracket around the diag flush is the owned volume's fence
now, and its serial is the stdin ring the port's RX ISR feeds, which the stop
inventory already assigns to the kernel. The Waveshare's 2 Mbaud link once
let a raw upload run into the 15 s watchdog, so every long word feeds it per
window. The Guition S3 drops its boot output until a serial host attaches, so
the boot's verdicts are read from `state`, not from the log. The Guition P4 is
attach-only, so no step of the loop's gate may reset it under an open handle.
The browser's worker is the loop's clock, and its PCM sink and pointer sink
are JS imports of the kernel's web build once the audio and input passes have
replaced the Python classes that are those sinks today.

### 7.8 The gate

| gate | host | on glass |
|---|---|---|
| the frame is the kernel's on every tier, Python called up into | the loop trace on CPython and the desktop MicroPython: the stage order, three console upcalls and zero service upcalls per frame, the driver thread switched mid-run; the host goldens unchanged under `host_app` driving `moy_loop_step`; the browser suites under the worker's call | each console's suite unchanged; `perf_line_is_the_one_format`, `wm_meters_answer_for_the_frame_they_measured`, `diag_toggle_roundtrips`, `idle_blank_and_wake`, `idle_timeout_restored` green; `state` reads the kernel's stage meters and a per-frame upcall count of three; `kstop 100` on every console with the glass presenting through each window |
| the kernel still catches a hang and a crash | `tests/test_moy_kernel.py` over the loop's feed and the cleared root pointers | `tools/kernel_gate.py` crash, hang, floor and safe on every console, with the loop in C feeding the watchdog |
| the idle ladder | the ladder as a pure state machine over a fake clock | dim, saver and blank observed on each console through `power`; the wake tap swallowed; a serial `power off` not woken by its own bytes; after saver, park and wake on the T-Deck and a P4 the frame crc equals a live repaint's and `_frames_drawn` advanced by `RETAINED_FRAMES` |
| the cadence is unchanged | — | the `--uncap` roster on both S3s and Bench µs/op on every console within noise of the pass before; per-stage meters with no stage's overrun count above its last recorded run; on the P4s the desk and drag medians of §3.8 |
| the Zero boots through the kernel's entry | — | the Zero's suite, with `moy_kernel` taken and its serial floor answering |
| the boot is no slower | — | §9's boot times |

## 8. Memory: the two shares

**PSRAM.** The kernel's share at the Player's fit check is at most 1 MiB on
each S3 (the plan's §6.1). After this sprint it is: the ping-pong or paint
buffers, the audio bank and ring once a session has opened, the system canvas,
the strip cache, the pool's free rows within the per-board bound, the surface,
input, owner and net tables (small, PSRAM by rule), the HTTP core's buffers,
the link's ring, the capture scratch while a shot is taken. Cart-owned rows
are not in it (§3.2). The build prints the fixed part beside the headroom; the
pool's bound is derived from it; `heapcaps` reports the live total by owner
class with the largest free block. The gate at every pass: the figure within
the share on both S3s, fresh and after the census's scripted session, with
cart-available PSRAM at or above the plan's threshold in free bytes AND
largest block.

**Internal SRAM.** The share is 4 KiB net while the VM runs, of which the
kernel has already spent its static part on each console (the `KERNEL_SRAM`
constants in the on-glass suites, measured 2026-10-06; about a quarter of the
share on the T-Deck). ESTIMATED, from where each piece is placed and which
way it moves:

| cost | placed in | internal SRAM |
|---|---|---|
| the tables (canvas, buffer, surface, owner, input, peer), the pool's rows, the HTTP buffers, the link's ring, the diag ring | PSRAM by rule | 0 |
| the input poller task's stack | internal; replaces the Python thread's, which is internal already (`mp_thread_create_ex` creates the thread's task with the port's default stack, 4 KiB plus the check margin) | a gain if the C task's stack is at most the thread's; sized at the gate |
| the per-source latches, their spinlock, the task notifies, the HID notification queue | internal, by their nature | hundreds of bytes; counted in the link map |
| the ISR text: trackball, touch INT, the band-done helper already there | IRAM, which shares the S3's internal pool with DRAM | hundreds of bytes; counted in the link map |
| the C central's statics beside NimBLE's own | `.bss` | hundreds of bytes; NimBLE's host and its pools are in the baseline, which was taken with BLE up |
| LEDC's fade service for the dim rung | internal, once installed | small; installed only on boards whose rung is on |
| the TLS working set during an OTA, the WiFi driver's receive buffers | internal, transient and at first start respectively | today's costs, moved from Python's call to the kernel's; `device/cart_net.py` records their size class |
| the loop, the pump, the ladder, the meters | the VM service task's stack while the VM runs | 0 |
| `moy_loop_task` | internal; exists from this sprint, runs only while no VM does | its stack while a VM runs is a cost unless it is created at the first teardown — it is, so 0 while the VM runs |
| the audio task and the I2S DMA ring | internal, started at the first session | today's first-cart cost, unchanged |
| the modules' other statics | `.bss` | under 1 KiB in all |

Two things the figure alone would hide, so the gate reads both. The largest
DMA-capable free block matters more than the free total: `moy_sd` takes a
16 KB bounce per transfer and halves it until internal DMA memory can give it,
so one new allocation that splits that block halves every card read's run.
And the baseline moves a little between boots, so a pass is judged on a
link-map delta — `.dram0`, `.iram0` and `.bss` against the pass before,
deterministic — and on `heapcaps` free, largest and low-water, internal and
DMA-capable, with WiFi and BLE up, against sprint 0's baseline; the
`KERNEL_SRAM` constants are updated per board at every pass so the wasm idle
guards stay the executable check. If the poller task cannot fit under the
thread's stack it replaces, the fallback is the loop's input stage on the
service task with the I2C timeout as the only guard — the pre-#69 shape, whose
cost #69 measured — and the owner hears about it before that trade is made.

## 9. The gate, summarised

Every pass carries the plan's standing items (its §6) and the meters the
reviews asked for, each a number that moves when the bug is present:

- **internal SRAM**: the link-map delta and `heapcaps` free, largest and
  low-water, internal and DMA-capable, both S3s, both radios up, against
  sprint 0's baseline and within the share (§8); `KERNEL_SRAM` updated;
- **PSRAM**: the share by owner class, free and largest, fresh and after the
  census's session, both S3s;
- **flash**: every image above its floor (1 MiB on the P4s, 512 KiB on the
  S3s, 256 KiB on the Zero), the Zero BUILT at every pass with a per-pass byte
  budget set from its remaining headroom, and a module the Zero cannot afford
  denied there with its reason in board.toml and the Python it replaces kept
  on that one board;
- **cadence**: the `--uncap` roster on both S3s, Bench µs/op on every console,
  the per-stage meters against their budgets, the P4s' desk and drag medians
  in frame milliseconds, with underruns at zero;
- **boot**: reboot to first light and reboot to `state` per console, against
  sprint 2's figures in #224, no slower;
- **the traces** extended before the crossing, green on both object models;
- `tools/board.py pass` on the four consoles, the Zero's suite, the browser's
  suites, `tools/preflight.sh`.

The sprint's gate is met when pass 3 lands: the kernel owns the frame on
every tier and Python is what it calls up into; each board's on-glass suite
is unchanged; the amendment landed first. Two re-takes close it: sprint 0's
census (`tools/mem_census.py`) on both S3s, naming no retained owner the
kernel does not account for, and the kernel gate tool on every console.

## 10. The open placements this doc answers

The plan's §2.2.1 marks these `open` and says the owner decides them before
their group's sprint. The recommendations, each one sentence of mechanism:

| placement | recommendation |
|---|---|
| `device/boot_shell.py` and the bring-up smokes | the mode ladder is `moy_boot_decide`, already the kernel's; each smoke becomes a floor word (`kpanel`, `ktouch`, `kkbd`, `kaudio`) that probes the C driver with no VM, from the serial the floor already answers |
| `runtime/update_ui.py` | the install pump is the kernel's (`moy_ota.c`'s step runs in the loop's tail) and the floor gets an `update` word, so a console that cannot start its VM updates itself; the screen stays a Settings screen in Python above both |
| `runtime/web_console_ui.py`, `runtime/moy_qr.py` | §13, question 8 |
| the Zero's modules and entry stubs | cross with the webhost they drive (§6.5); the Zero takes `moy_kernel` |
| `device/device_api.py`, `runtime/cart_api.py`, `runtime/cart_verbs.py` | not this sprint's: the canvas verbs stay the gates under an unchanged `make_api`, which leaves sprint 4 free to answer the question either way |

## 11. Decisions (2026-10-07)

- **The loop is the outermost frame of the VM service task, its upcalls are
  root pointers, and a kernel task drives it while no VM runs** (§7.1), with
  the §6.1 reading written there.
- **The pool is bounded, not just owned, and the share counts kernel-owned
  rows only** (§3.2); a cart's rows are cart memory and go with the run; the
  NOMEM path evicts the pool, then keeps the gc-heap last resort, counted.
- **The surface table crosses now as an inert registry plus the epoch fold**,
  with a kernel-to-WM epoch (§3.3); no compositor reads a gen until sprint 7.
- **Every off-heap buffer is a BUF row with a role, owners are OWNER rows**,
  and the new kinds are in `moy_htab.h` from the carve (§3.1).
- **The draw gates survive and no verb grows a Python frame** (§3.1).
- **The kernel's modules enter the web build from pass 1**, reversing
  sprint 2's deferral; the pinned-container rebuild is part of every pass that
  touches them (§1).
- **The P4s' shared I2C bus is the kernel's object**, input brings it up,
  audio adds the codec (§2, §4.2).
- **`MICROPY_PY_BLUETOOTH` and `MICROPY_PY_ESPNOW` leave the console images**
  with the drivers that were their only users, and the pin sweep becomes the
  Python-handler one (§4.3).
- **Audio has sessions with owners and a focus** (§5.1); the I2S start stays
  lazy.
- **The kernel's littlefs is reached through a VFS type of the kernel's**,
  as the store doc said it would have to be (§6.6).
- **`kstop` is the sprint's teardown guard**, test-only, pass 1's (§7.5).
- **#82 is in** (the plan's §6.2 assigns it; a register table behind a
  define) and the Guition S3's audio stays a bring-up.
- **The Zero crosses** (§6.5), with a per-board deny if a module outgrows its
  floor, judged every pass.
- **One sync codec in C for both ends** (§6.2).
- **`docs/moycore_direction.md` §3's "presentation stays per-board and outside
  moycore" stands** after this sprint, re-read as the plan asked: the glass is
  the kernel's, moycore is still the game surface's producer and owns no
  buffer, and "per-board" is the transport under one compositor engine.

## 12. What can kill it

- **A present bug on a board no suite can reach quickly.** The Guition P4 is
  attach-only and the Guition S3 drops its boot log; a compositor that paints
  black on one of them is debugged through `state` and the crc words, which is
  why the glass pass holds on the desktop MicroPython's crc traces before any
  flash. The scope is the risk: `device/device_canvas.py` alone is the largest
  Python file that crosses in the whole plan.
- **The internal-SRAM share on the T-Deck** (§8): most of it is spent, the
  poller task is a gain only if its stack fits under the thread's, and the
  largest DMA block is what the card reads through. The fallback is named.
- **The BLE central in C on the S3s' on-die radio with WiFi and ESP-NOW
  sharing it.** The P4's path is the model, but its radio is on another chip;
  the S3's coexistence is measured, not assumed, with
  `wasm_low_water_with_radios_up`'s shape of test run under the kernel's
  central.
- **The Zero's headroom.** Its 3 MiB slots (2026-10-07) leave it about its
  floor again above the floor, which is this pass's `moy_kernel` and `moy_net`
  plus every pass that touches the web build and grows the bundle it carries;
  the per-pass budget and the deny path are what keep a pass from discovering
  this at its end.
- **A crossing without its trace.** The carve lands the traces first; a pass
  whose trace is not green on both object models does not flash.

## 13. The owner's answers (2026-10-07)

The owner took the recommendation on every question but one, preferring to
drop work over adding it:

1. **#130:** dim, screensaver and blank as Settings rows with OFF; battery and
   sleep wait for a gauge and a reason.
2. **#70:** the sample voice and its C API only; the cart verb goes through
   moy-spec later; no recording.
3. **#83:** out of sprint 3.
4. **#26:** keyboard and boot mouse now; a gamepad when controllers are named.
5. **#196:** two keyboard modes, decoder in C; the issue closes on this.
6. **#126: dropped from sprint 3.** Screenshots, when they come, are the P4s'
   only, and the P4's H.264 encoder (a video stream rather than a GIF) is the
   direction to keep in mind; §3.6 stays a design, unbuilt.
7. **#226:** the screensaver is a cover cycle on the S3s and a wallpaper cart on
   the P4s.
8. **The web-console screen:** the themed Python screen while the VM runs, the
   kernel's plain one only when it does not.
9. **The splash:** the kernel lights the glass with the logo; the themed boot
   screen follows once Python is up.
10. **§10's placements** stand as written.

## 14. Claims this sprint falsifies

Each is rewritten where it stands, by the pass that falsifies it, never
footnoted:

| claim | lives in | rewritten by |
|---|---|---|
| `runtime/surface.py` stays, unreachable | `.claude/rules/web.md`; `runtime/app_context.py`'s docstring | pass 1, which deletes the file |
| the S3 build denies `wm_windowed.py` and executes no new code for the surface model | `docs/surface_model_v1.md` §2, §5.1 | the amendment (§15 there), then pass 1 |
| `test_device_canvas_parity.py`'s `gfx=False` arm stays because it runs the canvas's no-kernel lanes | `.claude/rules/rendering.md` | pass 1, which deletes the lanes and the arm |
| the browser keeps the Python spine because its wasm is moy-spec's derived artifact | `docs/kernel_spine_2026-10.md` §1 | pass 1 states the reversal; pass 3 takes the C spine into the browser |
| the Zero's floor exists because it meets the kernel only through the web bundle | `docs/native_kernel_2026-09.md` §6.1 | pass 2, links |
| the I2C GIL-release patch is what keeps a keyboard stall off the frame | the T-Deck's README and `build.sh` | pass 2, input |
| `moy_audio` is denied on the P4s pending #82 | both P4 board.toml files | pass 2, audio |
| the Zero keeps the port's entry | `firmware/seeed_xiao_esp32s3_zero/board.toml`, `docs/kernel_spine_2026-10.md` §8 | pass 2, links |
| `FrameLoop` is the loop's one copy for every board | `runtime/frame_loop.py`'s docstring, `device/desktop_spine.py`'s | pass 3 |
| "nothing net while stopped, when the VM task's stack is back" | `docs/native_kernel_2026-09.md` §6.1 | pass 3 adds the loop task to the sentence (§7.1) |
| the stop inventory's sprint-3 rows read "3" | `docs/native_kernel_2026-09.md` §4.4 | each pass, as its row lands |
