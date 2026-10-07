# The survival set — sprint 3's native design (2026-10)

**What this is.** What sprint 3 of the kernel plan
(`docs/native_kernel_2026-09.md`, #224) will build, designed ahead of the first
line of it: the six subsystems a console needs while
no Python app runs — input, audio, the glass, the storage gate for the internal
flash volumes, the frame tail and the links — each with its carve, its native
interface, the Python it deletes, the open issues it designs in, the board
facts that bite it and its gate. The kernel's language is C (owner,
2026-10-06). Where the sprint stands is #224's; every measurement it produces
goes there, the per-cart frame numbers to #66 and the P4's to #58.

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

## 0. The passes, in order

The owner works in whole-subsystem passes, one agent per subsystem, no slices
smaller than a subsystem on a board. The order is by risk and by what each
pass needs from the one before it.

| pass | subsystem | why here | agents |
|---|---|---|---|
| **1** | **the glass** (§3): canvas and layer ownership, the layer pool, the surface table, present on the three compositor kinds | the riskiest crossing and the one every later proof reads through; it fixes the kernel's PSRAM share (the pool, §6.1 of the plan) and executes the `surface_model_v1.md` amendment the plan orders first | one, alone on the boards |
| **2** | **input** (§4), **audio** (§5) and **the links with the internal volumes** (§6) | three subsystems that share no file once the carve has split the board glue; their host halves are long and their board halves short | three in parallel; the board passes serialise on the ports (`tools/board.py` refuses a held port and names the holder); the links agent takes the Zero and the browser first, which neither of the other two touches |
| **3** | **the frame tail** (§7): the loop, the pump, idle, OTA health, PERF, serial and diag, the boot order, each board's `moy_runtime.py` | last, because every service it would have to call up into has crossed; its gate is the upcall count, and that count is only meaningful once input, present, audio and the tail polls are C calls | one, alone on the boards |

Pass 2's three agents do not share files because the carve (§2) splits each
board's glue by subsystem first, and `device/desktop_spine.py` is the frame
tail's alone: a pass that needs a different provider for one of
`build_desktop`'s arguments changes the provider, not the spine. They do not
share boards at the same moment because the ports arbitrate; what they share is
the queue. The honest parallelism is that a pass is mostly host work — C, the
host binding, the fuzzers, the traces — with one board pass at the end of each
landing, and the three host phases overlap.

Each pass lands on dev on its own: the carve commit first, then the crossing,
each with the goldens untouched and the on-glass suites unchanged
(`tools/board.py pass tdeck guition_s3 p4 guition_p4`, the Zero's suite where
the pass reaches it, `tools/preflight.sh` before the report).

## 1. The crossing, component by component

| what | Python today (the twin after the carve) | C | pass |
|---|---|---|---|
| canvas and layer tables, the pool, lending and bakes | `device/device_canvas.py`, `_LayerComp`, `_LAYER_POOL`, `_LENT_BAKES` (`+runtime/moy_glass.py`) | `+native/moy_glass/moy_canvas.h`, `+native/moy_glass/moy_layer.h` | 1 |
| the surface table and the mint | `runtime/surface.py` (`+runtime/moy_glass.py`) | `+native/moy_glass/moy_surface.h` | 1 |
| the banded compositor, the fold | `device/banded_panel.py`, `modules/tdeck_panel.py`, `modules/guition_panel.py` | `+native/moy_glass/moy_present_banded.c` over `native/moy_flush/` and each board's panel module | 1 |
| the DSI compositor, rotated included | `device/dsi_panel.py`, `device/p4_canvas.py`, `modules/p4_display.py`, `modules/guition_p4_display.py` | `+native/moy_glass/moy_present_dsi.c` over `native/p4/moy_dsi/` and `native/p4/moy_ppa/` | 1 |
| the palette | `runtime/palette.py` | the canvas's table in `moy_canvas.h` | 1 |
| the host and browser rasters | `runtime/host_canvas.py`, `runtime/gfx_binding.py`, `firmware/web_runner/web_canvas.py` | the same `moy_glass` built for ctypes and for the web build | 1 |
| the merged input state, sources, the pointer | `device/moybyte/input.py`'s `InputState`, `runtime/input.py`, `runtime/widgets.py`'s `Pointer` and `pointer_state` (`+runtime/moy_input.py`) | `+native/moy_input/moy_input.h` | 2 |
| the T-Deck keyboard, trackball and poller | `device/moybyte/input.py`'s `TDeckKeyboard`, `decode_raw`, `InputPoller`; `device/device_input.py` | `+native/moy_input/moy_tdeck_kbd.c`, a kernel task | 2 |
| the touch drivers | `device/gt911.py`, `device/gsl3680.py`, `device/axs_touch.py`, `modules/p4_input.py`, `modules/guition_p4_input.py`, `modules/gsl_fw_jc8012.py` | `+native/moy_input/moy_touch_gt911.c`, `+native/moy_input/moy_touch_gsl3680.c`, `+native/moy_input/moy_touch_axs.c`; the GSL firmware as a C array | 2 |
| the BLE HID central | `device/ble_keyboard.py` over `bluetooth`; `native/p4/moy_ble_hid/` | `+native/moy_input/moy_ble_hid.c` over the NimBLE host, on every console | 2 |
| the browser's event decode | `runtime/web_input.py` | the web build's `moy_input` import | 2 |
| the six audio verbs, the bank push, the master level | `device/device_audio.py`, `runtime/audio.py`'s `AudioEngine`, `runtime/widgets.py`'s `_SilentAudio`, `device/moycore_glue.py`'s drain | `native/moy_audio/` grows its verb face; the codec in `+native/moy_audio/moy_codec_es8311.c` | 2 |
| WiFi, the radio under the spine's lease | `device/device_wifi.py` over `network` (`+runtime/moy_net.py`) | `+native/moy_net/moy_wifi.c` | 2 |
| ESP-NOW's owner | `device/moy_espnow.py` over `espnow`; `native/p4/moy_c6/` | `+native/moy_net/moy_link.c` | 2 |
| the HTTP core and the webhost | `device/moy_webserver.py`, `device/moy_webhost.py`; `native/moy_web/` | `+native/moy_net/moy_http.c`, `+native/moy_net/moy_webhost.c` | 2 |
| the sync RPC, both halves | `runtime/moy_sync.py`, `firmware/web_runner/carts_link.py`, `firmware/web_runner/update_link.py`, `firmware/web_runner/gpio_link.py` | `+native/moy_net/moy_sync.c` | 2 |
| the updater, its HTTP(S) client, the C6 updater, Get Carts' transport | `device/moy_ota.py`'s updater half (`+device/moy_http.py`), `device/moy_c6_update.py`, `device/cart_net.py` | `+native/moy_net/moy_ota.c`, `+native/moy_net/moy_c6_update.c` | 2 |
| the web-console switch and its screen | `runtime/web_console.py`, `runtime/web_console_ui.py`, `runtime/moy_qr.py` | `+native/moy_net/moy_webconsole.c`, drawn by the recovery floor's text path | 2 |
| the Zero's host | `modules/zero_host.py`, `modules/zero_gpio.py`, `modules/zero_setup.py` | the same `moy_net`, with the Zero's GPIO allowlist as a board table | 2 |
| the internal flash volumes | `moy_vol`'s borrowed littlefs backend (sprint 1b) | `native/moy_store/moy_vol.c` owns the instance | 2 |
| the loop, the pump, idle, OTA health, PERF, stage meters, the tail polls | `runtime/device_boot.py`'s frame half (`+runtime/frame_loop.py`), `runtime/console_perf.py`, `runtime/perf_hud.py`, `runtime/perf_line.py`'s formatter, `+device/moy_ota_health.py` | `+native/moy_kernel/moy_loop.c`, `+native/moy_kernel/moy_idle.c`, `+native/moy_kernel/moy_perf.c` | 3 |
| the dev channel's reader and kernel words, the diag ring | `runtime/dev_channel.py`, `device/device_diag.py`, `device/moybyte_diag.py`, `device/device_util.py`; `native/moy_serial/` | `+native/moy_kernel/moy_devch.c`, `+native/moy_kernel/moy_diag.c` | 3 |
| the boot order and the splash | `device/desktop_spine.py`'s boot half, `runtime/device_boot.py`'s `DeviceBoot.say` and `note` | `+native/moy_kernel/moy_boot.c`, drawing with `native/moy_kernel/moy_recovery.c`'s text path | 3 |
| the board glue | each console's `moy_runtime.py`; `device/p4_desktop.py`'s canvas, present and radio wiring; `device/boot_shell.py` | per-board defines in `mpconfigboard.h` and `board.toml` | 3 |
| the browser's and the host's drivers of the loop | `firmware/web_runner/web_boot.py`'s `step_frame_json`, `runtime/host_app.py` | JS and CPython call `moy_loop_step` | 3 |

Each native directory builds three ways, as `native/moy_spine/` does: a
MicroPython usermod the boards and the browser take through board.toml, a host
library for ctypes, and a fuzz driver. The twin names the interface, the tests
pin it, the crossing swaps the body and deletes the twin in the same change.

**What stays Python after sprint 3, by decision.** The Workstation, the window
managers and the draw stack (`runtime/console.py`, `runtime/wm.py`,
`runtime/wm_windowed.py`): sprint 7's, reached from the loop by three upcalls a
frame. The Player, the tick model, the runtime map and moycore's frame glue
(`runtime/player.py`, `runtime/tick_model.py`, `device/moycore_glue.py`'s input
refresh and the frame around `tick()`): sprint 4's. The bank model the Music
editor edits (`runtime/audio.py`'s `SFX`, `MusicTrack`, `AudioBank`). The
Settings screens over the services, including the firmware-update screen
(`runtime/update_ui.py`, §10). `DeviceBoot`'s runtime probe and the cart step
(`runtime/boot_carts.py`). The apps.

## 2. The carve

Python only, host-tested, one commit per subsystem, no behaviour change: the
goldens, the traces and the on-glass suites stay byte-for-byte. It runs one
pass ahead of the crossing it serves and no further.

1. **The board glue splits by subsystem**, so pass 2's agents never edit one
   file. The T-Deck's `moy_runtime.py` loses its input half (the poller thread,
   the keyboard and trackball construction, the text-mode hook) to
   `+firmware/lilygo_t_deck_plus_mainline/modules/tdeck_input.py`; the P4s
   already have `p4_input.py` and `guition_p4_input.py`, the Guition S3 gets
   `+firmware/guition_jc3248w535/modules/guition_input.py`. What remains in each
   `moy_runtime.py` is the frame tail's: the tail hooks, the diag, the PERF
   sink, the webhost and radio injections — which pass 2's links agent reaches
   through `build_desktop`'s named arguments, never by editing the file.
2. **The frame half of `runtime/device_boot.py`** — `FrameLoop`, `FramePump`,
   `IdleBlank`, `OtaHealth`, `PerfSampler`, `StageMeters`, `poll_webhost`,
   `poll_link`, `apply_touch` — moves to `+runtime/frame_loop.py`. `DeviceBoot`
   keeps the splash and the runtime probe.
3. **`device/moy_ota.py` splits** into `+device/moy_ota_health.py` (the boot
   verdict and the confirm after painted frames: the loop's) and the updater
   over `+device/moy_http.py` (the streaming client with redirects, which
   `device/cart_net.py` already reads through). Two passes, two files.
4. **The twins take the native call shapes.** `+runtime/moy_glass.py`: tables
   of kind CANVAS, LAYER and SURF over `moy_spine.Table`, the pool keyed by
   byte size with an owner per loan, and `present`, `fence` and
   `present_pending` as the three verbs the compositors answer;
   `device/device_canvas.py` is rebased so every buffer it holds is a row.
   `+runtime/moy_input.py`: one `InputTable` replacing the two `InputState`
   classes (the boards' fifteen-name one and the host's), sources by handle of
   kind SRC, the pointer's place/down/fresh/click as fields of the table.
   `+runtime/moy_net.py`: the WiFi state machine, the link's peer table of kind
   PEER, the HTTP request parser and response writers as pure functions, the
   sync batch codec. The dev channel's words that act on kernel state read
   their answers from these twins.
5. **The survival traces** join `tests/test_semantic_traces.py` before anything
   crosses: an input trace (a scripted event stream → the merged state and the
   pointer, per frame), a glass trace (a draw script → the frame's crc, the
   surface gens and the pool's rows), a loop trace (a scripted boot and twenty
   frames → the stage order, the idle ladder's state and the upcall count per
   frame), a links trace (a sync batch → the store's rows; the OTA state
   machine's transitions on a canned manifest). The audio trace is
   `tests/test_audio_parity.py`, already across bindings. Each trace holds on
   CPython and the desktop MicroPython, and on glass through the dev channel's
   crc words.

## 3. The glass (pass 1)

### 3.1 Canvas and layer ownership

A canvas is a kernel row of kind CANVAS: `{buf, w, h, stride, caps, palette,
clip, camera, font_scale, counters, owner}`. A layer is a row of kind LAYER:
`{buf, w, h, origin, owner, nbytes}`. The state array `device_canvas.py` shares
with `moy_gfx` today (`_ST_*`: camera, clip, size, font scale, the profiling
counters) is the row. The draw verbs are already C (`native/moy_gfx/`, libmoy
under it); what crosses is the dispatch above them and everything they draw
into. Python's `DeviceCanvas` keeps its method names and becomes the binding:
each verb is one call carrying the handle, and a stale handle is refused as the
spine refuses one. Lua and wasm carts reach the same functions without the
binding in sprint 4 (libmoy's table installs them), which is the point of
moving the dispatch down now.

    int moy_canvas_new(uint32_t *h, uint16_t w, uint16_t h_px, uint32_t caps, uint32_t owner);
    int moy_canvas_fill(uint32_t h, int x, int y, int w, int hh, uint16_t c);   // and the rest of the verb table
    int moy_canvas_sync_back(uint32_t h);                // re-point at the compositor's back buffer
    int moy_layer_new(uint32_t *h, uint16_t w, uint16_t hh, uint32_t owner);  // pool first, then PSRAM
    int moy_layer_release(uint32_t h);                   // back to the pool or freed, by origin
    int moy_glass_reclaim(uint32_t owner);               // every loan of a dead owner, layers and bakes
    int moy_glass_capture(uint32_t h, uint8_t *idx, size_t n);  // the frame as MOY64 indices (#126)

Errors are the spine's: STALE, FULL and NOMEM, mapped by the binding to the
same exceptions. A verb that draws returns nothing and never raises for a
coordinate — the kernels clip, as they do today.

The game canvas, the system canvas, a cart-declared small canvas, the view
scratch, the fold's snapshot scratch and every window buffer the P4 desk
mints are rows. The P4's scan buffers, which `moy_alloc.malloc_dma` hands out
with no free today, are the kernel's at start (the stop inventory's row); the
`malloc_dma` lane `device_canvas.py` keeps for a firmware without `alloc` is
deleted, since every image has had `alloc` since #186.

### 3.2 The pool

`_LAYER_POOL` is most of the T-Deck's retained memory (sprint 0's census,
#224), because it recycles but never frees and lives in `heap_caps` memory no
collector reaches. The owner's decision (2026-10-05) makes it the kernel's. The
design:

- **Rows, not dicts.** A pooled buffer is a LAYER row whose origin is POOL;
  the pool is the set of free rows keyed by byte size, in PSRAM by rule.
- **Bounded by the share.** The pool holds at most `MOY_GLASS_POOL_BYTES`,
  a per-board define derived from the kernel's 1 MiB PSRAM share minus the
  fixed buffers the build knows (the scan buffers, the flush slots, the audio
  bank, the game canvas). Above the bound a released layer is freed, not kept.
  On the Guition S3 the arithmetic leaves room for about one 320×240 layer
  (the plan's §6.1); on the T-Deck for more; the values are the build's and
  are printed with the headroom.
- **Loans have owners.** `moy_layer_new` records the owner handle (a cart run,
  the wallpaper, the map cache, a window), `moy_glass_reclaim(owner)` returns
  every loan when the owner dies — the #63 leak fix, kept, with the bakes of
  #186 as loans of the same kind. The Paint app's `release_bakes` is the same
  call with its own owner.
- **Counted.** `heapcaps` gains the pool's rows and bytes; the census snapshot
  gains a `glass` field. The gate reads both.

The window buffers of the P4 desk, which `_LayerComp.release` frees by origin
today, keep that behaviour: a window's buffer is an ALLOC row, freed on
release, never pooled, because the desk re-mints at every size.

### 3.3 The surface table

The amendment `docs/surface_model_v1.md` §15 records (2026-10-07) is executed
here. A surface is a row of kind SURF: `{domain, w, h, x, y, scale, z,
content_gen, place_gen, animating}`; the registry, the one monotonic mint and
the set-level epoch are the kernel's; `runtime/surface.py` is deleted.

    int  moy_surface_get(uint32_t *h, const char *sid, uint8_t domain);   // created with fresh gens
    void moy_surface_touch(uint32_t h);      // Class A, attributed
    void moy_surface_move(uint32_t h);       // placement only (L1)
    void moy_surface_animating(uint32_t h, bool on);   // Class B
    void moy_surface_epoch(void);            // Class A, un-attributed: everything changed
    uint32_t moy_surface_content_gen(uint32_t h);      // folded with the epoch, compared !=

Two things the table's generation counters must not be confused with. A
handle's generation is the slot's, validated per use (the kernel doc's §4.3);
a surface's `content_gen` is minted from the kernel's one counter and compared
`!=` by each consumer, which is §2's rule. The §8 graveyard's per-object
counter failure cannot arise: no counter restarts, and the only cache is the
compositor's own last-seen, which the kernel holds.

Why it moves now and not with the window managers: the compositors cross in
this pass and §4's per-buffer N-deep last-seen is theirs; and in sprint 4 a
cart running with no VM still has to mark its surface changed on each frame
it draws (L9), with no Python left to do it — the producer signal has to be
the kernel's. Until
sprint 7, `runtime/console.py`'s frame gate stays Python and folds `ws._dirty`
into one `moy_surface_epoch()` per dirty frame; the ~179 write sites are
untouched, which keeps L6 true on the S3 (one C call per painted frame, none
per write) and matches §7's "writes never".

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

- **Banded (T-Deck, Guition S3).** `+native/moy_glass/moy_present_banded.c`
  is `BandedCompositor` and `FoldingCompositor` over `moy_flush` and
  `moy_fold`: the ping-pong, the drain-swap-kick flush, the game fold's arming
  from `blit_game`, the Guition's game-window sub-rect, the T-Deck's async
  layer copy. The two board panel modules (`moy_lcd`, `moy_axs`) keep the
  transport hooks they have.
- **DSI (Waveshare P4).** `+native/moy_glass/moy_present_dsi.c` is
  `P4Compositor`: three scan buffers, the deferred show, the fences the
  overlap counters meter, the PPA composite hooks `blit_game` and `blit_cover`.
- **Rotated DSI (Guition P4).** The same file with `angle` and the damage
  path: the persistent landscape paint buffer, the quiet-game-frame single PPA
  op, the damage rects the WM hands down, the bounce worker's bands. The WM's
  `note_damage` becomes `moy_glass_damage(x, y, w, h)` on the root canvas, as
  absent on the other backends as it is now.

The stop inventory's three glass rows land here: the fold latch is disarmed
and its snapshot fenced by the kernel before any sweep; `moy_gfx`'s async copy
and the PPA's bounce worker are waited out by `moy_glass_fence(FRAME)`; and the
next present after a stop is the kernel's, because the compositor no longer
holds a Python buffer. The feeder task, the bounce slots and the scan buffers
are C-owned already and stay.

`end_frame` exists for the P4's ordering rule (an async PPA op is the frame's
last write, with the cache writeback before submit, #58): the loop calls it
after the draw-stack upcall returns and before `present`.

### 3.5 What Python is deleted

`device/device_canvas.py` shrinks to the binding (the verb names, the
`SystemCanvas` font-scale and layer riders, the host's `RETAINED_FRAMES`
probe); `_LayerComp`, `_LAYER_POOL`, `_LENT_BAKES`, `_MaskedRegion`, the
`malloc_dma` lane, `to_indices` and the `MAP_AUTO_CACHE` machinery go.
`device/banded_panel.py`, `device/dsi_panel.py`, `device/p4_canvas.py`, the
four board panel modules, `runtime/surface.py`, `runtime/palette.py` and
`firmware/web_runner/web_canvas.py` are deleted. `runtime/host_canvas.py` and
`runtime/gfx_binding.py` rebind to `moy_glass` built for ctypes;
`runtime/moyhost_gfx.c` goes with the compositor it duplicated.

### 3.6 Absorbed: #126's screenshot

`moy_glass_capture` hands back the game canvas's frame as MOY64 indices: the
canvas is RGB565 with the palette resolved at draw, and the reverse lookup
`to_indices` runs today is the kernel's. The picture is written through
`moy_fs` as a `.moyimg` under the user's drawings — the store's own picture
format, so Files and Paint open it and it can be a wallpaper with no new
reader. The trigger is the dev channel's `shot` word now and the ≡ menu's
action when sprint 4 crosses the in-cart chrome. The GIF rung is not in this
sprint: a ring of index frames costs the kernel's whole PSRAM share for a few
seconds of capture (§13, question 6).

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
- **Every board.** `git add` a new module before the first build, or the
  stager prunes it; a bug in present reboots five boards, so the pass runs on
  the host and the desktop MicroPython until the crc traces hold, and only then
  on glass.

### 3.8 The gate

| gate | host | on glass |
|---|---|---|
| the raster is unchanged | `tests/test_spec_conformance.py` and `tests/test_gfx_binding.py` over `moy_glass`; every pixel golden on every row unchanged; the glass trace on both object models | `tools/p4_conformance.py` on both P4s, and its banded equivalent on both S3s through `moy_glass_capture`: zero differing pixels on every scene |
| the pool is the kernel's and bounded | the pool's rows asserted through the twin's shapes; a fuzz of new/release/reclaim under ASan | twenty cart open-and-close cycles on each S3: `heapcaps` PSRAM free flat, the pool's bytes within the board's bound; the census (`tools/mem_census.py`) names no retained owner above the plan's share; Doom fits on a freshly booted Guition S3 five times running |
| present is the kernel's on three compositor kinds | the compositor state machines as pure functions over a fake transport, the per-buffer last-seen tested N-deep | each console's on-glass suite unchanged; `display_underruns_are_zero` on both P4s; the perf roster within noise on every board (`tools/board.py perf`, numbers to #66 and #58); the Guition P4's drag and picker frames through the damage path at their recorded medians |
| the stop survives the glass | — | sprint 0's stop spike on the Guition S3 re-run with the kernel-owned pool and the fold armed: a hundred stops, PSRAM and internal free flat |
| a screenshot is a picture | the capture's indices equal the host raster's for every golden scene | `shot` on each console writes a `.moyimg` Files opens |

## 4. Input (pass 2)

### 4.1 The table

One `moy_input` table for every tier: the fifteen buttons as held and pressed
masks, `last_key`, text mode, the pointer's place, down, fresh and click, and
a source table of kind SRC — the keyboard, the BLE keyboard, the touch, the
trackball, the browser, the net slots — each owning a held set and a key. The
state every surface reads is the merge, taken once per frame at
`moy_input_begin_frame`, which is where edges are computed.

    int  moy_input_source(uint32_t *h, const char *name);
    void moy_input_set_held(uint32_t src, uint8_t button, bool held);
    void moy_input_key(uint32_t src, int key);               // one-shot: delivered for exactly one frame
    void moy_input_release_all(uint32_t src);                // this source holds nothing
    void moy_input_pointer(uint32_t src, int x, int y, bool down);
    void moy_input_begin_frame(void);                        // the merge and the edges
    uint32_t moy_input_masks(uint32_t *held, uint32_t *pressed);
    int  moy_input_text_mode(bool on);                       // flips the T-Deck keyboard's mode
    void moy_input_snapshot(int32_t *arr);                   // the array a Lua cart's btn() reads

The dev channel's `tap`, `swipe` and `drag` inject into a source of their own,
through the same table, so a scripted gesture is indistinguishable from a
finger — which is what makes the on-glass suites' gestures a test of the
kernel's path and not of a Python shortcut.

### 4.2 The drivers

Each driver is a kernel task or an ISR writing into its source; none touches a
Python object.

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
  I2C GIL-release patch has no caller after this and is dropped from the
  board's patch list. The stop inventory's row lands here: the task is stopped
  cooperatively and joined before any VM teardown.
- **The trackball.** Four direction pulses and a click on GPIO IRQs, kernel
  ISRs that survive a stop (the pin sweep removes Python handlers only).
- **GT911 (T-Deck, Waveshare P4)**, **GSL3680 (Guition P4)**, **AXS15231
  (Guition S3)**: the shared core `device/gt911.py` factors (the held point,
  the mapping) is one C body; the GSL3680's firmware bytes are a C array and
  its upload runs after the splash's first frame, behind a lit screen, as the
  spine's `inputs()` ordering has it; the AXS touch reads portrait panel
  coordinates on the panel's own I2C0.
- **BLE HID below `bluetooth`.** `device/ble_keyboard.py`'s whole protocol
  path — scan for 0x1812, connect and bond, discover the Report and Boot
  Keyboard Input characteristics, enable their CCCDs, consume reports — moves
  onto the NimBLE host's C callbacks, with `native/p4/moy_ble_hid/`'s
  notification queue as its seed, on every console (the S3s' on-die radio,
  the P4s' C6 over ESP-Hosted). Bond secrets go through the NimBLE store
  callbacks to `moy_fs` on the internal volume, beside the chosen address
  and name. The result is what the stop inventory asked for: a keyboard whose
  IRQ outlives a VM stop. The Settings panel's scan, pick and forget are calls
  on the kernel's device list.
- **The browser and the host.** `runtime/web_input.py`'s event batch decodes
  in the web build's C into the same table; the host's SDL driver writes the
  table through ctypes.

### 4.3 Absorbed issues

- **#26 beyond keyboards.** A boot-protocol mouse is a second HID report
  shape over the same central: buttons and deltas into a pointer source, the
  windowed desk's cursor already consuming one. A gamepad is a third, and
  gamepads do not share one report layout: a generic report-descriptor parser
  is a thousand lines of C that the owner should ask for by naming the
  controllers (§13, question 4). Multiple devices at once is the central's
  connection table, four on either stack.
- **#196.** The decoder owns both keyboard modes in C whatever the answer;
  always-raw would add three layout tables and repeat timing on the path that
  must never mistype. Recommendation: keep the two modes; the issue's acute
  hazard is closed by the one table and its test (§13, question 5).
- **#83.** The P4's USB HID host is a transport under the same report decode
  as BLE. It is not in this sprint: the components it needs (`usb_host` and
  the HID class driver) cannot enter through a usermod on this build, the
  VBUS and connector question is unanswered hardware, and on the Guition P4
  the USB port is the console's serial (§13, question 3).

### 4.4 What Python is deleted

`device/moybyte/input.py` and `device/moybyte/__init__.py`, `runtime/input.py`,
`runtime/web_input.py`, `device/device_input.py`, `device/gt911.py`,
`device/gsl3680.py`, `device/axs_touch.py`, `device/ble_keyboard.py`, the
boards' input modules and `gsl_fw_jc8012.py`; `runtime/widgets.py` loses
`Pointer` and `pointer_state`. `tests/test_tdeck_keymap.py`,
`tests/test_tdeck_input.py` and `tests/test_ble_keyboard.py` keep their cases
over the binding.

### 4.5 Per-board facts that bite

The T-Deck keyboard and its touch share I2C0, and the keyboard clock-stretches
for tens of milliseconds; the `I2C_TIMEOUT_US` cap of 5000 stays. The
Waveshare's BLE shares the C6's SDIO with WiFi, needs the 64-packet ACL pool
and the 12 KB NimBLE host stack its README records, and only HOGP keyboards
exist to it. The Guition P4's GSL3680 reports in a 1664×896 firmware space
that is scaled, and its upload is the slow part of the boot. The Guition S3's
touch shares the AXS15231B bridge with its panel. The T-Deck's on-die BLE
shares the radio with WiFi and ESP-NOW, and sprint 0's internal-SRAM baseline
was taken with both radios up, so the central's cost on the S3s is in the
baseline already.

### 4.6 The gate

| gate | host | on glass |
|---|---|---|
| one table, every tier | the input trace on both object models; `test_tdeck_keymap` (both modes agree, every key), `test_ble_keyboard` (report decode) over ctypes; a fuzz of the HID report decoder | `cart_runs_and_exits`, `home_shelf_fling`, `idle_blank_and_wake` on every console, driven through the kernel's injected source |
| the T-Deck's drivers are the kernel's | — | the keyboard smoke's A/B on glass; `I2CSTAT` maxima unchanged with the poller task; raw-mode hold-to-move in a game and clean typing in the editor; the trackball's four directions and click |
| BLE HID survives below the VM | — | a keyboard typed on both P4s and both S3s through `bt`; and the §11 spike the plan owes: a hundred stop-and-start cycles on the T-Deck with the poller task and the keyboard alive, before sprint 4 relies on stops |
| nothing a driver does reaches Python | the loop trace counts zero input upcalls | `state` reads `input=native` and the upcall count per frame carries no input entry |

## 5. Audio (pass 2)

### 5.1 The verb face

The synth is libmoy's and already C (`native/moy_audio/`): the bank, both
sequencers, the mixer, the core-1 feeder task that blocks on the DMA drain,
the stream a compiled cart mixes in. What is Python is thin — the bank pushed
once per cart as text, six verbs forwarded, the master level, the diag lines,
the I2S construction and the legacy VM-side feed. The crossing gives the
module a verb face the kernel and libmoy's cart bindings call directly:

    int  moy_audio_bank(const char *json, size_t n);       // once per cart, on change of the bank's rev
    void moy_audio_sfx(int n, int chan);
    void moy_audio_beep(int freq_hz, int dur_ms);
    void moy_audio_music(int track, bool loop);
    void moy_audio_music_stop(void);
    void moy_audio_stop(void);
    void moy_audio_volume(int level);                      // 0..7, read from the settings rows at boot
    void moy_audio_hush(void);                             // a stop's silence, sprint 4's caller
    int  moy_audio_sample_load(uint32_t *h, const int16_t *pcm, size_t frames, int rate);   // #70's voice
    void moy_audio_sample_play(uint32_t h, int chan);

The legacy feed — `machine.I2S` driven from `tick()` when the core-1 task
failed to start — is deleted, as the stop inventory has it; a board whose
task cannot start has no audio, reported as absence. `device/moycore_glue.py`'s
drain stops going through `make_api` closures and calls the verbs; the queue's
order is the queue's. The master level is a settings row the kernel reads; the
Settings screen writes the row. The AUDIORATE probe and the trigger lines are
the kernel's and gated by PERF DIAG as every periodic line is. The host's
`runtime/audio_binding.py` rebinds to the same module built for ctypes, its
`render` pulled by SDL and by the web runner's `take_pcm` as now.

### 5.2 Absorbed issues

- **#82, the ES8311 on both P4s.** The codec is an I2C register sequence at
  address 0x18 (the factory firmware and Waveshare's demo carry a known-good
  one), the PA enable a GPIO (53 on the Waveshare, 20 on the Guition P4), the
  I2S a standard channel on the pins both READMEs list. It lands as a board
  define on the one module, `MOY_AUDIO_CODEC_ES8311`, the way the DSI panel is
  a define; both P4s flip `moy_audio` from denied to taken in board.toml. The
  feeder task's design is the T-Deck's and is not re-measured against a
  per-frame feed, because the per-frame feed is the one being deleted. The
  Guition S3 stays denied: its amp and pins are unverified, and verifying them
  is a bring-up, not a crossing.
- **#70, sound packs.** The mechanism is a sample voice in the mixer at the
  point where a compiled cart's stream is already added (`moy_audio_snd.h`):
  clips in PSRAM, resampled to the output rate at load, played by slot on a
  channel under the master level. A pack is a cart's content and loads into
  cart-available PSRAM, not the kernel's share. The cart-facing verb
  (`load_pack`, `play`) is a change to the public verb table, which is
  moy-spec's, so this sprint lands the C API and no verb; recording needs a
  microphone, a permission and an app, none of which is the kernel's (§13,
  question 2).

### 5.3 What Python is deleted

`device/device_audio.py`; `runtime/audio.py`'s `AudioEngine` (the bank model
stays); `runtime/widgets.py`'s `_SilentAudio` (a console with no backend holds
no audio handle and the binding's verbs are no-ops); `runtime/moyhost_audio.c`
where the host library replaces it; the drain's closures in
`device/moycore_glue.py`.

### 5.4 Per-board facts that bite

The T-Deck's amp is on its own pins (GPIO 7, 5, 6) behind the board power
gate, so audio never meets the panel-and-card bus; the output rate is 22050
because the synth's character is defined there (SPEC.md 8.3), and the feeder
and a compiled cart's thread share core 1, which is why the feeder's work per
block stays small. On the P4s the codec and the quad microphone share one I2C
bus, and the PA enable pin differs between the two boards. A sound that is not heard is
not proven by a test that passes: the instruments are the digital self-dump
and the rate probe, both of which run with no ear in the loop.

### 5.5 The gate

| gate | host | on glass |
|---|---|---|
| the synth is unchanged | `tests/test_audio_parity.py` bit-identical across the binding and the desktop MicroPython; the verbs over ctypes | the T-Deck's self-dump of a seed cart's music equals the host's render; the rate probe reads a cumulative ratio of 1.000 over thirty seconds of music with a cart running |
| the P4s make sound | the codec's register sequence as a table test | the same two instruments on both P4s with the ES8311 up; the seed carts' sounds and the Music tab's preview, owner-heard once |
| the verbs are C calls | the loop trace counts zero audio upcalls | a Lua cart's sfx reaches the mixer with no Python frame in `moy_prof`'s samples |
| a stop silences | — | `moy_audio_hush` from the dev channel while music plays: silence within one block |

## 6. The links and the internal volumes (pass 2)

### 6.1 The radios

- **WiFi.** `+native/moy_net/moy_wifi.c` is the driver's life: init once,
  scan, connect with the saved credentials, autoconnect at boot, the
  `wifi.json` store through `moy_fs`, power down and up as the spine's lease
  mask asks. The lease is sprint 2's and stays where it is; the driver only
  answers it. The internal-SRAM facts `device/cart_net.py` records — the
  receive buffers the driver takes on first start and keeps, the TLS working
  set a download needs — are the kernel's to report, not to hide.
- **ESP-NOW.** `+native/moy_net/moy_link.c` is `device/moy_espnow.py`'s
  discovery, pairing and the two-console link over the esp_now API — on-die on
  the S3s, through `native/p4/moy_c6/`'s shim on the P4s, which already
  implements that API. The receive ring is the kernel's, not the port's
  `rxbuf`, so a VM stop cannot orphan it. The lockstep over it, the `net0`
  input slots and the Player's arming are sprint 4's.

### 6.2 The HTTP core, the webhost and the sync RPC

`+native/moy_net/moy_http.c` is `device/moy_webserver.py` over BSD sockets:
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
parked screen is drawn by the recovery floor's text path with the QR encoder
in C beside it, because a console whose VM is down to free memory for a
browser's edit session has no toolkit to draw with; the toolkit version, if
ever wanted, is sprint 6's. That answers two open placements
(`runtime/web_console_ui.py`, `runtime/moy_qr.py`) with a mechanism sprint 2
built.

### 6.5 The Zero

The Zero's modules cross (the plan's open placement, §10): one C webhost
serves five boards, the Zero's GPIO verbs become a board allowlist table, its
first-run access point is `moy_wifi`'s provisioning mode, and `zero_host.py`
dissolves as each console's `moy_runtime.py` does. The Zero takes `moy_kernel`
in this pass too — the spine doc deferred it to the sprint that makes the
kernel the entry on every target, and this is it; its floor is serial-only,
since it has no panel. It keeps the port's REPL as its console.

### 6.6 The internal flash volumes

Sprint 1b borrowed the littlefs instance from the mount table under nlr. This
pass has `native/moy_store/moy_vol.c` own it: the kernel mounts the internal
partition's littlefs at start, and Python's `VfsLfs2` is mounted over the same
instance the way `VfsFat` was over the owned card in slice 8. What lives there
is the kernel's to keep across a VM stop: the system documents on a no-card
board, the staged update, the BLE bonds, the Zero's whole store, the embedded
floor's read-only built-ins when sprint 4 needs them. An internal-flash write
stalls flash-resident code on both cores while the cache is off, so the
feeder's band pump is paused for the write's duration; the gate measures a
flush under a store commit.

### 6.7 What Python is deleted

`device/device_wifi.py`, `device/moy_espnow.py`, `device/moy_webserver.py`,
`device/moy_webhost.py`, `runtime/moy_sync.py`, `device/cart_net.py`,
`device/moy_c6_update.py`, the updater half of `device/moy_ota.py`,
`runtime/web_console.py`, `runtime/web_console_ui.py`, `runtime/moy_qr.py`,
the three `*_link.py` files of the web runner, and the Zero's
`zero_host.py`, `zero_gpio.py` and `zero_setup.py`. `runtime/host_api.py`'s
service fakes shrink to what the host's harness still stands in for.

### 6.8 Per-board facts that bite

The Waveshare's SDMMC slot 1 belongs to the C6 and constructing it panics the
board; the Guition S3's card is on SPI3 but its slot numbers invert against
host numbers; a card that will not mount leaves its bus up. The Zero has no
dev channel and a Ctrl-C takes its webhost offline until a Ctrl-D, its USB
port re-enumerates under a new name, and it holds every module twice across
8 MB of flash, so each C module here is weighed against its 256 KiB floor. The
browser's half is a derived artifact: the kernel's modules enter the web build
through the pinned emscripten container, and `tools/preflight.sh` compares the
baked blob to its source. New IDF components do not enter through a usermod's
cmake on the P4 build; a dependency the net code needs is added to the board's
component list.

### 6.9 The gate

| gate | host | on glass |
|---|---|---|
| the wire is unchanged | the HTTP parser and the sync codec fuzzed under ASan and UBSan; a batch round-trips against the browser's; the manifest verifier against `tools/ota_sign.py`'s vectors and the tamper cases; the links trace | `web_console_is_baked_into_the_image`, `wifi_status_is_readable`, `wifi_is_off_at_rest` on every console; the Zero re-provisioned and paired from Chrome (`tools/web.py shot`) and its suite green |
| an update still updates | the OTA state machine on a canned manifest | a beta pushed over WiFi to each console through the unstable channel (the `release` skill) and confirmed; an image armed to never confirm rolls back on one S3 and one P4; the C6 updated on both P4s; the floor's `update` word takes a card image |
| the link links | the peer table and the pairing state machine as pure functions | two consoles paired through `link`, a cart beamed, delivery counted at the set rate (numbers to the issue) |
| the volumes are the kernel's | `tests/test_store_on_vfs.py` over the owned littlefs; the power-cut matrix re-run | commit, reboot, intact on every console and the Zero; a flush measured under a store commit on both S3s |
| nothing here reaches Python | the loop trace counts zero link upcalls | the upcall count per frame carries no webhost, radio or OTA entry |

## 7. The frame tail (pass 3)

### 7.1 The loop

`+native/moy_kernel/moy_loop.c` is `FrameLoop.step` in C, in the order that
class's docstring guards: the pump's head, every input source, the dev channel,
the idle ladder (after every input, so the waking touch is swallowed), the
pointer, `present_pending`, the three upcalls (`handle_input`,
`handle_pointer`, `frame`), `end_frame`, the first-frame backlight gate, OTA
health, the tail polls (webhost, link, diag cadence), pace, account, the
watchdog feed. The stage meters (#210) are the kernel's, with the same stage
names and budgets. A frame error in an upcall is caught under nlr and printed
as the board prints it today; whether it counts against an app is the
ledger's rule (sprint 2), which this sprint does not change.

    int  moy_loop_step(void);                         // one frame; returns QUIT when the channel asked for the REPL
    int  moy_loop_run(void);                          // until QUIT
    void moy_loop_upcall(int which, mp_obj_t fn);     // register the three draw-stack entries
    uint32_t moy_loop_upcalls(uint32_t *console, uint32_t *app, uint32_t *driver);   // per frame, by class

**Who runs it.** On a device the loop runs on the VM service task sprint 2
created: the console's Python boot ends in `moy_kernel.run()`, the loop calls
Python back on the same task through ordinary callbacks, and no second task
and no hop exist. That costs nothing in internal SRAM, which is why it is the
choice; what it forgoes is the stop inventory's row that deletes the VM's task
to return its stack, which sprint 4 takes up when it designs the stop — the
measured need of a VM-free cart decides whether the VM gets a task of its own
then, with the task's stack placement bounded by the rule that a task writing
flash needs an internal stack. In the browser the worker calls
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
stage budgets are cut from. OTA health is `+device/moy_ota_health.py`'s two
halves: the verdict read on the boot path before anything can overwrite it,
and the confirm fired from the loop after real painted frames.

`+native/moy_kernel/moy_idle.c` is `IdleBlank` with the three behaviours its
docstring lists kept (the wake tap swallowed, the epoch bumped on wake, an
explicit blank outranking activity), generalised into a ladder whose rungs are
per-board configuration:

1. **dim** after `idle_dim_s`: the backlight ramped down by PWM where the pin
   drives one, absent where it is binary;
2. **screensaver** after `idle_saver_s`: the kernel cycles the shelf's covers
   from the store's cover facts through its own text path and `moy_png`, with
   no VM — the S3's screensaver; a wallpaper cart is the P4 desk's, whose VM
   is up anyway (#226's item, routed through the spine's run verb);
3. **blank** after `idle_blank_s`: today's behaviour, the board rendering
   while dark;
4. **sleep**: light sleep with wake on the input ISRs, refused while a WiFi
   lease is held or the webhost serves.

Rung 4 and the low-battery rung of #130 are not built in this sprint; the
ladder has their hooks, and the reasons are §13's question 1.

### 7.3 PERF, serial and diag

`+native/moy_kernel/moy_perf.c` is `PerfSampler` and `PerfMeters`: one field
set from one accounting path, the compositor's overlap counters read raw, a
lever the board lacks printed as `-`, nothing formatted while PERF DIAG is off.
`runtime/perf_line.py` keeps its parser for the host tools and loses its
formatter; `perf_line_is_the_one_format` holds the C formatter to the parser.
`runtime/perf_hud.py` draws through the canvas and stays a draw.

`+native/moy_kernel/moy_devch.c` is the dev channel's reader (over
`native/moy_serial/`) and word table. The words that act on kernel state —
`state`'s kernel fields, `heapcaps`, `mem`, `bl`, `vol`, `power`, `diag`,
`uncap`, `crisp`, `web`, `link`, `shot`, the `k*` test words, `recv` and
`push` — are C. The words that act on the console (`run`, `open`, `tap` on a
named button, `py`) are registered by Python as upcalls, the pattern the
spine's registry set; `py` is an upcall by definition and stays. One
vocabulary on every board, a declined word naming what the board lacks.

`+native/moy_kernel/moy_diag.c` is the ring `device/moybyte_diag.py` keeps and
its flush to the card on the T-Deck's cadence through the owned card volume,
plus the PUMP, HITCH and LOOP lines `device/device_diag.py` formats.

### 7.4 The boot order

`+native/moy_kernel/moy_boot.c` is `build_desktop`'s order with the kernel
drawing the splash before any VM runs, through the floor's text path: first
light comes earlier, and a boot that fails before the VM is legible on the
glass. The order is the spine's — splash, the store (C), the runtime probe
(Python until sprint 4), the Workstation and the window manager (Python until
sprint 7), the services wired as kernel handles the Python side reads, the OTA
verdict, the loop. `mem_census.mark` stays on the path with its names.

The board glue dissolves: each console's `moy_runtime.py` becomes the
per-board configuration in `mpconfigboard.h` and board.toml — pins, the panel
and codec defines, the idle rungs, which services the board has — and
`tests/test_board_service_parity.py`'s wiring table reads board.toml. The
bring-up smokes and `device/boot_shell.py`'s mode ladder are answered in §10.

### 7.5 What Python is deleted

`+runtime/frame_loop.py` (the carve's file, in full), `device/desktop_spine.py`'s
boot half, `device/device_diag.py`, `device/device_util.py`,
`device/moybyte_diag.py`, `runtime/console_perf.py`, `runtime/dev_channel.py`
down to the registrations, `+device/moy_ota_health.py`, each console's
`moy_runtime.py`, `device/p4_desktop.py`'s present and radio wiring,
`device/boot_shell.py` and the smokes, `firmware/web_runner/web_boot.py`'s
`step_frame_json`.

### 7.6 Per-board facts that bite

The T-Deck's card bracket around the diag flush is the owned volume's fence
now, and its serial is the stdin ring the port's RX ISR feeds, which the stop
inventory already assigns to the kernel. The Waveshare's 2 Mbaud link once
let a raw upload run into the 15 s watchdog, so every long word feeds it per
window. The Guition S3 drops its boot output until a serial host attaches, so
the boot's verdicts are read from `state`, not from the log. The Guition P4 is
attach-only, so no step of the loop's gate may reset it under an open handle.
The browser's worker is the loop's clock and the wasm's audio sink
(`_RunnerAudio`) and pointer sink are JS imports of the kernel's web build.

### 7.7 The gate

| gate | host | on glass |
|---|---|---|
| the frame is the kernel's on every tier, Python called up into | the loop trace on CPython and the desktop MicroPython: the stage order, three console upcalls and zero service upcalls per frame; the host goldens unchanged under `host_app` driving `moy_loop_step`; the browser suites under the worker's call | each console's suite unchanged; `perf_line_is_the_one_format`, `wm_meters_answer_for_the_frame_they_measured`, `diag_toggle_roundtrips`, `idle_blank_and_wake`, `idle_timeout_restored` green; `state` reads the kernel's stage meters and a per-frame upcall count of three |
| the kernel still catches a hang and a crash | `tests/test_moy_kernel.py` over the loop's feed | `tools/kernel_gate.py` crash, hang, floor and safe on every console, with the loop in C feeding the watchdog |
| the idle ladder | the ladder as a pure state machine over a fake clock | dim, saver and blank observed on each console through `power`; the wake tap swallowed; a serial `power off` not woken by its own bytes |
| the cadence is unchanged | — | the perf roster within noise on every board; the stage budgets' misses no higher than before the pass |
| the Zero boots through the kernel's entry | — | the Zero's suite, with `moy_kernel` taken and its serial floor answering |

## 8. Memory: the two shares

**PSRAM.** The kernel's share at the Player's fit check is at most 1 MiB on
each S3 (the plan's §6.1). After this sprint it is: the scan or ping-pong
buffers, the flush's bounce slots (internal, not here), the audio bank and ring,
the game canvas, the pool within its per-board bound, the surface, input and
net tables (small, PSRAM by rule), the HTTP core's buffers, the capture scratch
while a shot is taken. The build prints the fixed part beside the headroom; the
pool's bound is derived from it; `heapcaps` reports the live total. The gate at
every pass: the figure within the share on both S3s, fresh and after the
census's scripted session, with cart-available PSRAM at or above the threshold.

**Internal SRAM.** The share is 4 KiB net while the VM runs. ESTIMATED, from
where each piece is placed:

| cost | placed in | internal SRAM |
|---|---|---|
| the tables (canvas, layer, surface, input, peer), the pool's rows, the HTTP buffers, the ring | PSRAM by rule | 0 |
| the input poller task's stack | internal; replaces a Python thread whose stack was in the gc heap | the one real cost: sized at the gate, a few KiB |
| the BLE central's state | NimBLE's own, already up in the baseline | 0 net |
| the loop, the pump, the ladder, the meters | the VM service task's stack, which is the baseline's | 0 |
| the modules' statics | `.bss` | under 1 KiB in all |
| the ES8311 path, the P4s | the P4 has no internal-SRAM share to defend | — |

So the poller task is the number to watch on the T-Deck, whose DMA-capable
low-water with WiFi and BLE both up is the narrowest margin the two S3s have;
the gate measures it with `heapcaps` and the figures go to #224. If it does not
fit, the poller's pass is the loop's input stage on the service task with the
I2C timeout as the only guard — the pre-#69 shape, which the measurement in
#69 says costs felt stalls — and the owner hears about it before that trade is
made.

## 9. The gate, summarised

Every pass carries the plan's standing items (its §6): the two S3s' internal
SRAM, free and low-water, read with both radios up and held to sprint 0's
baseline and the kernel's share; every image above its headroom floor (1 MiB
on the P4s, 512 KiB on the S3s, 256 KiB on the Zero); the traces extended
before the crossing. Every pass ends with `tools/board.py pass` on the four
consoles, the Zero's suite when the pass reaches it, the browser's suites
when it reaches the web build, and `tools/preflight.sh`.

The sprint's gate is met when pass 3 lands: the kernel owns the frame on
every tier and Python is what it calls up into; each board's on-glass suite
is unchanged; the amendment landed first. Two re-takes close
it: sprint 0's census (`tools/mem_census.py`) on both S3s, naming no retained
owner the kernel does not account for, and the kernel gate tool on every
console.

## 10. The open placements this doc answers

The plan's §2.2.1 marks these `open` and says the owner decides them before
their group's sprint. The recommendations, each one sentence of mechanism:

| placement | recommendation |
|---|---|
| `device/boot_shell.py` and the bring-up smokes | the mode ladder is `moy_boot_decide`, already the kernel's; each smoke becomes a floor word (`kpanel`, `ktouch`, `kkbd`, `kaudio`) that probes the C driver with no VM, from the serial the floor already answers |
| `runtime/update_ui.py` | the install pump is the kernel's (`moy_ota.c`'s step runs in the loop's tail) and the floor gets an `update` word, so a console that cannot start its VM updates itself; the screen stays a Settings screen in Python above both |
| `runtime/web_console_ui.py`, `runtime/moy_qr.py` | drawn by the floor's text path in C (§6.4), toolkit never needed |
| the Zero's modules and entry stubs | cross with the webhost they drive (§6.5); the Zero takes `moy_kernel` |
| `device/device_api.py`, `runtime/cart_api.py`, `runtime/cart_verbs.py` | not this sprint's: the canvas verbs become C calls under an unchanged `make_api`, which leaves sprint 4 free to answer the question either way |

## 11. Decisions

- **The pool is bounded, not just owned** (§3.2): a layer above the bound is
  freed, because an unbounded pool is the retention sprint 0 measured with a
  different owner.
- **The loop runs on the VM service task** (§7.1), one task and no hop; the
  VM's own task is sprint 4's question, with the flash-write constraint on
  stack placement named here so it is not rediscovered.
- **The surface table crosses in this pass**, with the compositors and ahead
  of the window managers, because the kernel's compositors consume it and a
  VM-free cart must produce into it.
- **The T-Deck keyboard keeps two modes**; the decoder owns both in C (§4.3).
- **#82 is in; #83 and recording are out** (§4.3, §5.2): a codec is a register
  table behind a define; a USB host stack and a microphone pipeline are
  bring-ups with open hardware questions.
- **The screenshot is in and the GIF is not** (§3.6), on the share's
  arithmetic.
- **The Zero crosses** (§6.5): one webhost in C for five boards beats one in C
  for four and one in Python for the board whose only job is the webhost.
- **The browser's half of the sync RPC is the kernel's wasm build**, with JS
  imports for what the page owns; the alternative — a Python page half over a
  C board half — is two codecs for one wire.
- **`docs/moycore_direction.md` §3's "presentation stays per-board and outside
  moycore" stands** after this sprint, re-read as the plan asked: the glass is
  the kernel's, moycore is still the game surface's producer and owns no
  buffer, and "per-board" is the transport under one compositor engine.

## 12. What can kill it

- **A present bug on a board no suite can reach quickly.** The Guition P4 is
  attach-only and the Guition S3 drops its boot log; a compositor that paints
  black on one of them is debugged through `state` and the crc words, which is
  why the glass pass holds on the desktop MicroPython's crc traces before any
  flash.
- **The poller task's stack on the T-Deck** (§8). The fallback is named.
- **The BLE central in C on the S3s' on-die radio with WiFi and ESP-NOW
  sharing it.** The P4's path is the model, but its radio is on another chip;
  the S3's coexistence is measured, not assumed, with `wasm_low_water_with_radios_up`'s
  shape of test run under the kernel's central.
- **The links pass growing past its floor on the Zero.** Each C module is
  weighed on the Zero's image twice; a module the Zero cannot afford is denied
  there and the Python it replaces stays on that one board, named in its
  board.toml.
- **A crossing without its trace.** The carve lands the traces first; a pass
  whose trace is not green on both object models does not flash.

## 13. Open questions for the owner (bounded)

1. **#130's rungs.** Facts: idle dim needs a PWM-capable backlight pin — the
   Guition S3's is, the T-Deck's and the P4s' drive a GPIO that LEDC can
   modulate; no battery reader exists in the tree, and the T-Deck's README
   names no gauge, so the low-battery rung starts with a hardware check;
   light sleep must refuse while a WiFi lease is held or the webhost serves,
   and its wake sources are the input ISRs this sprint builds. Options: (a)
   dim, screensaver and blank in this sprint, battery and sleep later; (b) (a)
   plus the battery rung after the gauge is identified; (c) all five.
   Recommendation: (a).
2. **#70's scope.** Facts: the mixer's sample voice is a few hundred lines at
   a mix point that exists; the cart verb is a public-table change owned by
   moy-spec; recording needs the Waveshare's ES7210, a `microphone`
   permission and a recorder app; a 26-clip pack at 8 kHz is under half a
   megabyte of cart memory. Options: (a) the mechanism only, and a proposal to
   moy-spec for the verb; (b) (a) with the pack format and the verb landed
   through moy-spec in parallel; (c) recording too. Recommendation: (a).
3. **#83, USB HID host on the P4.** Facts: the IDF components exist but do
   not enter through a usermod on this build; VBUS and the connector are
   unconfirmed on the Waveshare; the Guition P4's USB is its serial. Options:
   (a) out of this sprint, the report decode shared with BLE so USB adds only
   a transport later; (b) keyboard only, Waveshare only, after a hardware
   check. Recommendation: (a).
4. **#26's gamepad.** Facts: a boot mouse is cheap and lands with the
   keyboard; a gamepad needs either a named allowlist of report layouts or a
   generic descriptor parser. Options: (a) keyboard and mouse now, gamepad
   when the owner names the controllers #65's couch co-op should take; (b)
   the parser now. Recommendation: (a).
5. **#196.** The decoder is C either way; always-raw means owning base, shift
   and sym layout tables and repeat timing. Recommendation: no; the issue is
   marked pending-decision, so this closes it one way or the other.
6. **#126's GIF.** Facts: a ring of index frames for five seconds at ten frames
   a second is of the order of the kernel's whole PSRAM share on an S3, and the
   encode must stay out of the frame. Options: (a) screenshot only, GIF never
   on the S3s; (b) GIF on the P4s only, where the share is not the constraint;
   (c) a shorter, lower-rate ring on every board. Recommendation: (a) now,
   (b) if the P4 desk wants it.
7. **The open placements** of §10: confirm or redirect each.

## 14. Claims this sprint falsifies

Each is rewritten where it stands, by the pass that falsifies it, never
footnoted:

| claim | lives in | rewritten by |
|---|---|---|
| `runtime/surface.py` stays, unreachable | `.claude/rules/web.md`; `runtime/app_context.py`'s docstring | pass 1, which deletes the file |
| the S3 build denies `wm_windowed.py` and executes no new code for the surface model | `docs/surface_model_v1.md` §2, §5.1 | the amendment (§15 there), then pass 1 |
| the I2C GIL-release patch is what keeps a keyboard stall off the frame | the T-Deck's README and `build.sh` | pass 2, input |
| `moy_audio` is denied on the P4s pending #82 | both P4 board.toml files | pass 2, audio |
| the Zero keeps the port's entry | `firmware/seeed_xiao_esp32s3_zero/board.toml`, `docs/kernel_spine_2026-10.md` §8 | pass 2, links |
| `FrameLoop` is the loop's one copy for every board | `runtime/device_boot.py`'s docstring, `device/desktop_spine.py`'s | pass 3 |
| the loop runs in the VM's task and the VM's task is deleted at a stop | `docs/native_kernel_2026-09.md` §4.4's task row | pass 3 records the one-task shape; sprint 4 amends the row with its measurement |
| the stop inventory's sprint-3 rows read "3" | `docs/native_kernel_2026-09.md` §4.4 | each pass, as its row lands |
