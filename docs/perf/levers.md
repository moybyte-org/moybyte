# Performance levers

What has been decided about making a cart's frame cheaper: the model a frame is
reasoned in, the levers that shipped, the ones that were measured and reverted,
and the ones still open. Decisions only. The numbers are elsewhere:

- `docs/perf/ledger.md` — each board's per-cart fps and frame ms and its desk,
  generated from the perf records of one protocol, which it names
  (`tools/perf_ledger.py`).
- Issue comments — the story behind a number: a bisect, an A/B, a deep dive.
  #66 for the carts (its moved body sections are indexed in its body), #58 for
  the P4 desk, #158 for the compiled tier, #67 for the Lua tier.
- `docs/perf_native_gap_v1.md` (#77) — why the boards trail native emulators,
  the per-board frame split, the PPA verdict and the cross-board lever roadmap.

How to measure, and which meter answers which question, is the `perf` skill.

## The frame budget model

A drawn frame is the cart's `_update` (logic), its draw calls (render), the
shell around it (chrome: the bar, the composite, the window stack; and the
loop's own stages: inputs, the dev channel, present) and the flush. The `LOOP`
and `PERF` lines split it by stage, `HITCH` names the stage of a frame over
80 ms, and `ws.stage_meters` splits the loop on a board's dev channel.

- **The panel transfer is hidden.** The S3 boards' flush is double-buffered
  through two internal-SRAM bounce slots and kicked asynchronously, so the SPI
  transfer overlaps the next frame's render and the flush costs its kick,
  unless the render is shorter than the transfer. The P4s scan PSRAM
  continuously and pay a composite instead (`docs/perf_native_gap_v1.md` §2).
- **The pump-gap chain is CLOSED (2026-07-08).** A third bounce slot fed the
  panel perfectly and moved no frame rate: the residual SPI idle always
  overlapped VM work. There is no headroom left in the feed path.
- **Two regimes.** A logic-bound cart (Sakura, moss moss) pays the
  interpreter; its levers are fewer opcodes, the Lua tier and the compiled
  tier, never the shell. A render-bound cart (Brick Siege) pays pixel fill in
  the C kernels; its lever is drawing less (`background()`, layers).
- **Every slice of a frame is accounted for before a lever is chosen.** On
  2026-09-11 a stale `wmw` field and an unsettled `chrome` EMA (`c95bf89`) made
  moss moss on the Guition P4 look chrome-bound when it is logic-bound.
- **Price a shell body in its stage meter, not in a bench (2026-09-11).** An
  isolated timing understates in-frame cost several times over, because the
  composite DMA runs through the input poll by design and a tight-loop probe
  measures an uncontended bus.
- **A loop that spins pays the shell per spin**, not per drawn frame: CPU, not
  fps, on a cart with headroom.
- **The collector's cost is per COLLECTION** (a whole-heap mark and sweep that
  stops the frame and a compiled cart's sound with it), so the lever is how
  often one comes: the console's own garbage rate. A play frame allocates
  nothing on the Python heap (2026-09-30): the loop, inputs, dev channel,
  Player, glue and compositor hand-off allocate zero bytes a frame, and the
  periodic diag text exists only under PERF DIAG. `tests/test_frame_alloc.py`
  pins it on the desktop MicroPython built in the boards' model (32-bit,
  REPR_C, threads under one GIL). What allocates on a board and reads as free:
  set arithmetic, a `getattr` that finds a method (a bound method), a tuple
  returned to be unpacked, `str()` of a str (a copy), a function holding a
  generator or closure (its cells are made on EVERY call, early return or
  not), `select.poll()`'s list, I2C `readfrom`, `"%d" %`, and `id()` of a
  pointer above the 30-bit small int (a P4's PSRAM). On glass `gc.mem_alloc()`
  walks the whole heap, so take it over a window of seconds, never per frame.
- **Per-board verdicts do not transfer.** The `-O3` `moy_gfx` pragma is
  A/B-confirmed on the compute-bound S3 and NULL on the dispatch-bound P4;
  the cart-VM collector's schedule fixed a P4 defect and only trimmed the
  T-Deck's tail.

## Shipped levers

| lever | what it does | landed | where it lives |
|---|---|---|---|
| Native sprite batch | N `spr()` calls in a frame collapse into one C call | `e70779de4` 2026-06-27 (#43) | `spr_batch` in `device/device_canvas.py` |
| Native `spr` gate | a kid's `spr()` lands in C with no Python frame | `93b010640` 2026-07-03 (#63) | `native/moy_gfx/modmoy_gfx.c` |
| Scroll layers | a pre-rendered background and a window copy (`make_layer`/`draw_layer`) | `e11d4bb87` 2026-06-28 (#54) | `device/device_canvas.py` |
| S3 cache geometry | 32 KB icache, 64 KB dcache, 32-byte dcache lines | `163de4946` 2026-07-03; 32-byte lines `1e06abb93` (#63) | each S3 board's `sdkconfig.board` |
| SRAM-bounce flush | the panel DMA reads only internal-SRAM bounce slots, never PSRAM: banding gone, and the GDMA layer copy safe again | `2b07f0ea3` 2026-07-03 (#66) | the T-Deck's `native/moy_lcd/modmoy_lcd.c`, the Guition S3's `native/moy_axs/modmoy_axs.c` |
| Async flush overlap | the flush refills a bounce slot while the next frame renders | 2026-07 (#40/#43); on the mainline T-Deck `e3bbd710a` 2026-08-16 | `native/moy_flush/moy_flush.c`; `ASYNC_FLUSH` in the T-Deck's `modules/moy_runtime.py` is the one-flag revert |
| Pump pokes | `pump_if_pending()` after a big native draw, so a heavy frame does not starve the SPI feed | `134008df7` 2026-07-04 (#66) | the S3 panel drivers |
| Kid mode diag gate | Settings → PERF DIAG off by default: no periodic line, sampler or SD write during play; DIAG SD LOG is its own gate | `134008df7` 2026-07-04, `19d65e4e9` 2026-07-08 (#68) | `runtime/shell/console_settings.py` |
| REPR_C unboxed floats | a float is not a heap object, which ended the float-boxing micro-stutter | `3cdfc3056` 2026-07-04 (#66) | every console board's `mpconfigboard.h` / build patch |
| `background()` | a declared backdrop the engine repaints before `_draw`: the no-`cls` idiom as a verb | `d0219623c` 2026-07-07 (#63) | `runtime/run/cart_api.py`; the habits in `docs/moy_cart_api.md` "Make it fast" |
| Pal-state variant cache | `pal()` states are content-keyed and sprite variants are cached per image, so a tint does not rebake every frame | `dbe6e053c` 2026-07-07 (#63/#72) | `device/device_canvas.py` |
| Layer pool | layer buffers are pooled per owner and reclaimed when the cart exits | `e56323dac` 2026-07-07 (#63) | `device/device_canvas.py` |
| Auto-native carts | the loader compiles every cart def with `@micropython.native`, with a probe, a fallback and the crash-line remap | `f9dac8379` 2026-07-07 (#67) | `runtime/run/player.py` |
| Live-set diet | carts slim to metadata and icon, rehydrate on open, re-slim on switch | `2fa69f801` 2026-07-08 (#66) | `runtime/shell/cart_manager.py` |
| External memory at 120 MHz | octal flash and PSRAM at 120 MHz on the S3 boards, with the temperature retune | `21158ebd6` 2026-07-09 (#66, #169) | each S3 board's `sdkconfig.board` |
| `-O3` raster kernels | `moy_gfx`'s kernels built at `-O3` (S3: a win; P4: null, kept) | `0580fad56` 2026-07-10 (#77) | `native/moy_gfx/libmoy_kernels.c` |
| P4 L2 cache 256 KB | the P4's L2 cache doubled; this is what closed render-overlap on the P4 | `166542578` 2026-07-27 (#159) | the P4 boards' `sdkconfig.board` |
| Flush-bounce scale fold | a small-canvas game is upscaled inside the bounce bands instead of composited | `1a5f1133f` 2026-08-11 (#190) | `native/moy_flush/moy_fold.c` |
| Map-lookup cache | the index re-aimed for REPR_C, 512 slots on every console board; neither half moves a frame alone, and 1024 buys nothing over 512 | `9462e52d3` 2026-09-21 (#77) | each board's `mpconfigboard.h`; `docs/perf_native_gap_v1.md` §6 |
| `gc_alloc` run hints | a free-run hint per size class (2–32 blocks), so a multi-block allocation stops re-walking every hole; allocations over 32 blocks still walk, by design | `f4180be09` 2026-09-23 (#66, #77) | `tools/patch_gc_run_hints.py` |
| Map growth at three quarters full | a hash map above twelve slots grows while a quarter of it is empty, so a lookup that misses (a method on an instance, a builtin in a module) stops within a few probes; every console board and the browser console, the Zeros decline it for its heap | 2026-11-18 (#77) | `tools/patch_map_load.py`; `docs/perf_native_gap_v1.md` §6 |
| Cart-VM collector on the generational schedule | Lua's incremental collector paced itself into one frame; the generational one does not | `4be6cd4de` 2026-09-20 (#107) | `native/moycore/moycore_lua.c` |
| A stream's decoder in internal SRAM | while a stream plays, the media task's stack (minimp3's scratch) and decoder state are internal SRAM where the heap keeps `MOY_MEDIA_SRAM_FLOOR` free, else PSRAM; nothing is held while no stream plays (the owner's lever over QIO flash and an IRAM decoder copy, 2026-11-10) | `28c6bea20` 2026-11-10 (#66) | `native/moy_media/moy_media.h` |

What is left in `mp_map_lookup` after the map-lookup levers is the kid
idiom's miss-before-hit, a VM question: do not re-price it as a cache size
(`docs/perf_native_gap_v1.md` §6, 2026-09-23). The map growth shortens each
miss; a method cache keyed by (type, name) is what would remove it.

## Tried and reverted (do not retry without reading this)

- **64-byte dcache lines** (2026-07-03, back to 32 in `1e06abb93`): horizontal
  garbage bands (CPU and GDMA coherency granularity against the PSRAM panel
  flush) and slower interpreter heap access.
- **The GDMA async layer copy against a flush that reads PSRAM** (disabled
  `1e06abb93`, re-enabled under the SRAM-bounce flush `2b07f0ea3`, both
  2026-07-03): it starves the panel's own PSRAM read. It is safe only while the
  panel reads internal SRAM; `LAYER_COPY_ASYNC` in `device/device_canvas.py`.
- **Fold-2, the automatic `map()` cache** (`2fdf2a32b`, turned off on the first
  flash `45d5ce65c`, 2026-07-07): caching `map()` into a keyed layer LOST on the
  device, because the keyed full-region composite reads every pixel where the
  direct raster skips transparent tiles; x86 hid it, PSRAM exposed it. The
  machinery was deleted with the glass tables' move to C (`702135c03`,
  2026-10-07). The explicit layer idiom (#54) is the cache that works, and
  Brick Siege's field stays one native `map()` call for the same reason.
- **A third bounce slot** (`8d3ed3bf2`, reverted `83debd6f4`, 2026-07-08): it
  closed the SPI idle gap completely and moved no fps, and the extra in-flight
  band intermittently blocked the VM on a full SPI queue. `BOUNCE_SLOTS` is 2.
  The same verdict retired a core-1 present task unbuilt: a perfect feeder buys
  the same zero.
- **The double game canvas on the P4** (`26e1f9f`, 2026-07-27): copy-on-swap
  to make the deferred composite fence-free; the fence was nearly free and the
  swap's retention copy was not (`docs/perf_native_gap_v1.md` §6).
- **The batch verbs for Lua carts** (deleted 2026-08-14): a net loss once Lua's
  draws went direct to C (#66, 2026-08-14).
- **Batching draws on the Python side** (#163, 2026-08-04): an interpreted
  append under `rect()` cost more per op than the native crossing it saved,
  even in pure runs. A batch pays only when the append is C (`make_spr_gate`)
  or the pack is built once and handed whole to one call (`map()`); the C draw
  gates on the root canvas (`7c5c8cfe0`) took the dispatch slice instead.
- **`-O3` for the Lua VM** (2026-08-11): null on both boards; the VM stays at
  `-O2`.
- **The GIL round-robin divisor and the frame-spill threshold** (declined
  2026-09-21 and 2026-09-23): `docs/perf_native_gap_v1.md` §6 has the reasons.
- **Dirty rectangles** (dropped 2026-07-04, owner call): static screens skip
  redraw and flush (#44), the transfer hides behind render (#40/#43) and a static
  background is the layer idiom (#54). Revisit only if a real cart profile shows
  static-background redraw dominating after the layer idiom is applied.

## Retired

- **FRAMESKIP and the frame governor** were deleted by the tick model
  (`26dc2717a`, 2026-09-05, #217): `native/moy_play/moy_tick.c` is the one
  scheduler. Its own pin found a free lever: a tick-only frame must beat a
  drawing frame, not the period (2026-09-10).
- **The web view's per-surface deltas** (#76) went with the web view
  (`moy_webserver.py`, deleted `bcd8c1f6d`, 2026-10-08); the browser console is
  `firmware/web_runner/`.
- **Rate-limiting the touch read** (proposed 2026-09-11, when the Guition S3's
  touch read was the loop's dearest stage): the touch controllers are read by
  the kernel's input task now, off the loop (each board.toml's `[[hardware.resource]]` rows).
- **Sakura's repeat-run logic regression** was the native-code exec arena, fixed
  2026-07-11 (#66, "CLOSED 2026-07-11").

## Open

- **The launcher's live wallpaper** defeats the redraw-on-change gate, so the
  tile grid re-renders at the wallpaper's rate. #73's per-surface compositing
  is the architectural fix.
- **A logic-bound cart's interpreter time.** moss moss on the Guition P4 misses
  its declared tick by a few milliseconds of `_update`; nothing in the shell is
  big enough to hand it those. The lever is fewer opcodes
  (`docs/perf_native_gap_v1.md` §9) or a faster tier (#67 Lua, #158 compiled).
- **The cross-board roadmap's open rows** are `docs/perf_native_gap_v1.md` §6
  ("Open — API-preserving", "Open — model / strategic").
