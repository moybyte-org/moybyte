# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What belongs in this file (read before adding to it)

**Decisions, not status.** A decision keeps its value forever — "the indexed
canvas was A/B'd on P4 glass and RGB565 won (2026-08-05)", "the double game
canvas was built, measured and REVERTED (`26e1f9f`)", "do NOT re-attempt in-loop
serial on the T-Deck". Those are what stop the next session re-doing dead work,
and they are historical BY NATURE; keep them, and keep their dates and commit
hashes. **A status claim rots.** "Still needs on-hardware verification",
"pending next flash", "not yet verified" are true for a week and then quietly
lie — and a doc that lies is worse than one that is silent, because the reader
acts on it. Status belongs in a GitHub issue, which has state; this file should
say what IS, with a date, or point at the issue.

That is not hypothetical. On 2026-08-15 this file said OTA "Still NEEDS
ON-HARDWARE VERIFICATION" in the paragraph directly ABOVE the one recording the
whole chain passing on glass on both boards, and an agent duly told the owner
their T-Deck might need a migration flash it had taken two weeks earlier.

Same rule for numbers: per-cart fps and frame budgets live in issue **#66**, P4
numbers in **#58**. A snapshot pasted here goes stale exactly as this paragraph
warns — one did, inside the sentence forbidding it.

## What this repo is

Moybyte is an operating system for ESP32 boards: a console where the software is
cartridges, running as firmware on two boards plus a host simulator and a browser build.
Everything is ONE system now — the `.moyproj` SDK that used to live beside it
(`moybyte/`, `moybyte_cli/`, `moybyte_sim/`, `moybyte_blocks/`, `examples/`, the
portable-subset gate) was **deleted 2026-07-31**; nothing depended on it but its own
tests, and the console's block compiler (`runtime/blocks.py`, #29) was always separate.
Git history has it if you need it. `.moy` is the only cart format.

- `runtime/` — the **host reference** of the console (launcher → Player → tabbed Editor). Pure host, fast dev loop. See `runtime/README.md` for the per-file map; don't duplicate it.
- `firmware/lilygo_t_deck_plus_micropython/` — the **device port** of that same console (MicroPython).
- `firmware/esp32_p4_wifi6_touch_lcd_7b/` — the **second device target** (#58): the 7″ 1024×600 MIPI-DSI "desktop workstation" board. Panel/touch/SD/WiFi hardware-confirmed; the **console runs on glass** (the two-worlds desktop under `WindowedWM` — boots to the desk, #105; carts on internal flash; the two-worlds split is on-glass verified — `tests/test_p4_on_glass.py` boots to the desk, opens app windows and scrolls/flings them against the real pointer feed) — colors/flicker/touch/popup/wallpaper all fixed on-glass, the game composite runs on the hardware **PPA** (`moy_ppa`) with an **async-overlap** frame path — post-#159 (L2 cache 256KB) the **whole cart roster sits at the 60 cap** with headroom; app-window drags ~43fps (triple framebuffer + retained backdrop cache).
- `system_carts/*.moy` — seed cartridges (folder = `manifest.json` + `main.py` + `config.json`).

The shipped shell is the **2026-07 shell** (everything-is-a-process: launcher / Player / Editor apps
over a fullscreen-stack WM; spec `docs/shell_ux_v1.md`).

**Versions: there is exactly one ladder, and docs are not on it.** The only
version a user ever sees is the firmware's — `moy_ota.FIRMWARE_VERSION`, a
monotonic integer cut by `make release` and tagged `vN`, carrying a human
`label` (`0.6`) that the update screen and the OTA manifest show. Betas stamp
the build epoch instead, labelled `beta <date>`. Everything else is **dated,
not numbered**: design docs, plans, and shell generations are named by the
month they describe (`moybyte_console_plan_2026-07.md`, "the 2026-07 shell"),
because a `v0.5` beside a firmware `0.6` reads as an older release and is not
one. Archived plans keep their historical `v0_N` filenames under
`docs/history/` — that is what git history and the issues call them. Two names
that are FORMAT generations and deliberately stay: the `.moy` cart format and
the indexed-canvas contract (still `v04` in `tests/test_v04_userland.py`,
`docs/audio_design_v04.md`, and code comments) — renaming those would churn the
public cart vocabulary for no gain.

### The indexed-canvas portability contract (why the canvas is "indexed")

The `.moy` canvas works in **palette indices** (the `MOY64` palette) with a plain-function drawing API (`cls/pset/line/rect/rectfill/circ/circfill/spr/print`) — no dependency on `framebuf`, LVGL, or even Python. This is deliberate: the *same* `.moy` runs on the host, on both boards and in the browser. **There is now ONE canvas class on every tier** — `device_canvas.DeviceCanvas`, RGB565 with the palette resolved at draw time; the host builds it on CPython through `runtime/host_canvas.py` (the host's own indexed raster, runtime/canvas.py, was deleted 2026-08-15 — git history has it). So a drawing feature is added ONCE, in that class + the `moy_gfx`/libmoy kernel under it.

### Graphics is conformance-checked, and the indexed canvas was MEASURED AND DECLINED

Graphics has not followed audio into vendoring, but the reason is *not* that
libmoy's raster cannot fit here — an earlier version of this note claimed that
and was wrong, so the argument is recorded properly.

**The difference.** libmoy draws into a framebuffer of palette INDICES and
resolves colour once, later. Moybyte's device canvas resolves at DRAW time and
stores RGB565 straight into the compositor buffer.

**"It doesn't fit" is wrong.** libmoy renders the CART canvas, which SPEC.md 1
fixes at 320×240 — not the desktop, so the P4's 1024×600 scan-out buffer is not
the thing being proposed. An indexed cart canvas is 76,800 B instead of
153,600 B, every draw writes one byte per pixel instead of two, and the kernel
for the conversion already exists (`moy_gfx.blit_indices`, #63).

**It was A/B'd on P4 glass and RGB565-at-draw won** (2026-08-05, moy-spec
session; a standalone ESP-IDF bench at 360MHz / 200MHz PSRAM / `-O2`, four
deterministic scenes × 20 frames, canvas in SRAM and again in PSRAM). **A** =
8-bit index canvas drawn by libmoy's kernels unmodified + one resolve at the
stamp; **B** = 565-at-draw, geometry copied line-for-line from libmoy so only
the write differs. A/B ratios, A over B (below 1 = indexed wins):

| ui (100 rects) | ray (320 sspr cols) | mode7 (120 tline rows) | tri (60 tris) | stamp |
|---|---|---|---|---|
| **0.73×** | **1.13×** | **1.20×** | **0.85×** | A 2.0ms resolve vs B 0.42ms memcpy (SRAM) / 1.76ms (PSRAM) |

So indexed wins where the kernel is **write-bandwidth-bound** (fills) and loses
where it is **per-pixel-sample-bound** (sspr, tline) — those loops are dominated
by sheet addressing and Bresenham, so a narrower store buys nothing while the
resolve is added on top. Two things not to misquote: the "tline is a wash"
reading is from the run *before* the reduce-once tline fix (25ms → 8ms), when
the soft-modulos hid the format entirely; and the stamp comparison flatters A,
because on the P4 today B's stamp is not a memcpy at all but `moy_ppa.blit_async`
— hardware, ~free to the CPU, and the PPA does not consume indices. The T-Deck
is the untested opposite shape: it already pays an SRAM bounce copy the resolve
could ride.

**Every scene hashed A==B in both placements** — an indexed canvas loses no
colour, proven on silicon. That question is closed; the format choice is settled
on performance, and B is what ships.

**Nine verbs are libmoy's now (2026-08-07), and the hand-porting argument lost.**
`tri`/`sspr`/`tline`/`circ`/`circb`/`line` went first, then `print`/`blit_map`/
`blit_batch` later the same day; in `moy_gfx` all nine are CALLS into vendored
libmoy (`native/moy_gfx/libmoy/`, built `MOY_PIXEL_RGB565` at `-O3` via
`libmoy_kernels.c`), not transcriptions of it. The reason is one measurement: on 2026-08-06 the board failed
`provisional_tline` against the golden (2773 px, 3.61%) while the host passed it,
because the only lane that exercises the REAL C kernel is on-glass conformance
and it had never been run on that verb — `test_device_canvas_parity.py` compares
the host to a *Python transcription* of the kernel, which cannot catch the
transcription being right and the C being wrong. Routing the verb through the
spec's own raster took it to **0 differing pixels**, all ten scenes pass on the
P4, and the cart-level cost is nil (per-verb A/B within the bench's ±5% noise;
per-cart fps unchanged across the roster). **`print`, `blit_map` and the sprite
path were declined first and then CROSSED** — the numbers that kept them out were
stale (they predated `moy_print`'s off-clip early-out and `moy_spr`'s scale-1 fast
path), and re-measuring on 2026-08-07 reversed two of the three; adopting `spr`
also deleted the RGB565 tile atlas, which handed back 64 KB of S3 internal SRAM
and removed a ~100ms first-use bake nobody had attributed. What `moy_gfx` still
owns is its COMPOSITOR — viewport-aware `fill`/`fill_rect`, `blit565_scale`,
`copy_async`, `scroll_rect`, `blit_window`, `blit_indices`, `fill_spans`,
`draw_ctx` — which the spec's raster has no counterpart for.
**`native/moy_gfx/libmoy/UPSTREAM.md` is the authority on which verbs cross and
why** (it carries the before/after table, the dates, and the warning about which
of the bench's verbs are too cheap to quote); fixes belong upstream (`moy_circ`
already went that way — moy-spec `ef01426`). Tooling: `tools/p4_perf.py` (per-cart fps),
`tools/p4_bench.py` (the Bench cart's per-verb µs, and the Lua twin's on-glass
report), `tools/p4_conformance.py --serve` (holds the board — opening the port
REBOOTS it, which cost a full boot per scene; the suite went 12min → 4m45).

**`tests/test_spec_conformance.py` is that gate** (suite vendored under
`tests/spec_conformance/`, see its UPSTREAM.md). It replays the spec's recorded
verb traces through the host's canvas (`runtime/host_canvas.py` → `DeviceCanvas`) and hashes each frame against the
golden — all ten scenes including the provisional 3D ones, in ~0.1s, on every
`make test`. It exists because the suite previously only checked this repo from
*outside* it (moy-spec's `conformance/parity.py --ref`, and `tools/p4_conformance.py`
on a board), so `make test` could go green on a raster that no longer drew what
the spec said. The device inherits it through `test_device_canvas_parity.py`
(host↔device), and `tools/p4_conformance.py` is still the only check that
reaches the real C kernel on real glass — run it when the raster changes.

**The compiled-vs-compiled check is a `make` target now (2026-08-15).**
`tests/test_gfx_binding.py::test_matches_the_native_moy_gfx` drives **131 ops
across 16 verbs** through the host's ctypes binding AND through the real native
`moy_gfx` under a desktop MicroPython — the only lane in `make test` where two
independently COMPILED rasters meet. It used to point at a hand-built binary
nothing produced, so it passed on one machine and silently SKIPPED in CI.
**`make unix-micropython`** builds it (~15s cold, <1s warm, **no cache** — a
cache miss that skips the check is the bug), CI runs it every push, and a
missing binary WARNS locally and FAILS under `CI`/`MOYBYTE_REQUIRE_UNIX_MP`.
The same binary carries `moycore` and `moy_audio`. On the strength of it,
`tests/test_device_canvas_parity.py` shed its ~400-line Python transcription of
libmoy's nine verbs — `_FakeGfx` forwards them to the binding and transcribes
only `moy_gfx`'s OWN compositor; its `gfx=False` arm STAYS, because it is the
only thing anywhere that runs `device_canvas`'s no-kernel Python lanes. Two
things not to undo in that op script: its framebuffer is a `memoryview` into a
larger **patterned** arena (without it a capacity guard that fails to clamp
writes past the end on BOTH sides and reads as agreement), and its clamp ops aim
ONE pixel past each edge, because a mutant that clamps at `max_rows + 1`
survives any large overhang. Five other suites still point at their own
hand-built binaries and skip the same way (`test_flush_fold`,
`test_gate_pal_sync`, `test_semantic_traces`, `test_moycore_loop`,
`test_audio_parity`'s native case) — all five pass against this one binary and
want the same candidate-path line; do NOT instead symlink into
`firmware/.../.build/lvgl_micropython/`, which `build.sh` git-clones into.

### Audio is VENDORED from moy-spec, not implemented here (#97)

The one subsystem where that rule is inverted. SPEC.md §8.3 pins synthesis to
PICO-8's measured output (as reverse-engineered by zepto8/fake-08) — the
unequal instrument loudness, the pitched noise walk, the Hz-linear slide, the
109/110 phaser detune — and moy-spec ships its own C implementation of it,
**libmoy**. That source is vendored verbatim into
`firmware/lilygo_t_deck_plus_micropython/native/moy_audio/libmoy/` and
**compiled into** the T-Deck and the web runner; `modmoy_audio.c` is a thin
binding that forwards the six §8.2 verbs and owns I2S. libmoy owns the bank,
both sequencers and the mixer, so the boards are conformant by construction and
nothing marshals across the boundary per frame — the bank crosses ONCE per cart
as `sounds.json` text.

**Heard on a T-Deck (owner-verified, 2026-08-09, firmware 0.9 over OTA).** Until
then the swap had only host evidence — `tests/test_audio_parity.py` diffs every
sample against the vendored C, which proves the twin faithful and says nothing
about whether I2S comes up on glass. It does. **The game sounds CHANGED audibly,
and that is the expected result, not a regression**: §8.3 pins the synth to
PICO-8's measured output, whose instrument loudness is deliberately unequal — the
triangle family peaks at about twice the square family. The engine it replaced
had them EQUAL: `_sample_wave` before `c5d594e` returned ±1.0 for both square and
triangle (organ reached 1.5, phaser was halved back to 1.0), so the audible change
is that triangle-family parts are now roughly twice as loud against a square lead
as they used to be. The seed carts were authored against the old balance, so a
cart whose lead now sits under its accompaniment is a **cart mix** to fix in its
`sounds.json` vol column, never a synth to "correct" here — and nothing will catch
it for you, because §8.3 exempts audio from pixel conformance on purpose.

So: **do not "improve" the synthesis locally, and do not add a waveform or an
effect here.** Fix it in moy-spec, re-vendor with **`make vendor-libmoy`**
(`tools/vendor_libmoy.py`, pointed at a sibling moy-spec checkout or `SPEC=`;
it copies the pinned file set and re-stamps `native/libmoy_vendor.json`), bring
the Python twin along. **Editing a vendored file in place is a red test** —
`tests/test_libmoy_vendor.py` hashes every one against the manifest, and also
diffs against a sibling checkout when it sits at the pinned commit, so both
"someone patched the copy" and "someone patched upstream without re-vendoring"
fail on the same day rather than at the next re-vendor. The #167 3D verbs took the other route
— `moy_gfx` re-implements `moy_canvas.c`'s geometry line-for-line — and that is
only safe because the conformance goldens pin every pixel; §8.3 deliberately
exempts audio from pixel conformance, so there is no golden to catch a drifting
twin.

**The Python twin synth is DEAD (moycore stage 0, 2026-08-11).** The host sim
now binds the vendored C itself: `runtime/audio_binding.py` compiles the
DOUBLE-WIDENED source (the parity harness's own recipe — the strict suite had
proven the twin bit-identical to exactly that program, so the swap moved no
sample) plus a small shim (`runtime/moyhost_audio.c`) into a hash-cached `.so`
under `.build/host_audio/`; `make setup` pre-builds it, first use builds
lazily. `AudioEngine` keeps its name/shape everywhere as the bank/MODEL
holder (the device constructs it too); **no compiler / no native module means
SILENCE, not a fallback synth** (owner call, KISS — `DeviceAudio`'s
Python-engine lane is deleted). `tests/test_audio_parity.py` still gates: the
strict pass now pins the BINDING bit-exactly against an independently-driven
reference render (any difference is marshalling, never the synth), the
device-precision pass still measures the double-vs-float gap, and it still
drives the NATIVE module under a desktop MicroPython build when one exists.
Run `.venv/bin/python experiments/audio_parity/audio_parity.py -v` for the
report. The data model (`SFX`/`MusicTrack`/`AudioBank`, `sounds.json`, the
Music editor) is still ordinary shared Python and is not affected by any of
this.

## Common commands

```bash
make setup          # python -m venv + pip install -e '.[dev,sim]' (hermetic: NOT --system-site-packages)
make test           # pytest (all). The venv python is .venv/bin/python

# run a single test
.venv/bin/python -m pytest tests/test_v04_userland.py -k cards
.venv/bin/python -m pytest tests/test_micropython_spike.py::test_name
```

`.moy` console (host):
```bash
python tools/simulate_desktop.py                                  # boots the launcher (needs a display)
python tools/simulate_desktop.py --cart system_carts/star_catcher.moy
python tools/simulate_desktop.py --demo --gif demo.gif            # headless tour
```

## Firmware (LilyGO T-Deck Plus, MicroPython)

This is the active hardware target. Build → flash → monitor:

```bash
MOYBYTE_SKIP_VFS_BOOT=1 make firmware-build-lilygo-micropython     # outputs to dist/current/
make firmware-flash-lilygo-micropython PORT=/dev/ttyACM0           # esptool, default_reset
make firmware-monitor-lilygo-micropython PORT=/dev/ttyACM0         # miniterm @115200
```

- The build (`firmware/lilygo_t_deck_plus_micropython/build.sh`) **clones `lvgl_micropython` into `.build/`**, stages the native C modules (`native/moy_gfx`, `moy_alloc`, `moy_sd`, `moy_audio`, `moy_lua` — the #67 Lua cart VM — and `moy_web`, the browser console baked into the image, below) into its `ext_mod` tree (re-staged every build because `ext_mod` is wiped on re-clone) and the shared `runtime/` modules into `modules/`, freezes the `modules/` Python, and emits `app` + full-flash images to `dist/` (both gitignored). **Which shared modules cross is DATA, not a list in the script** (#161 Phase 3): each board carries a `board.toml` whose `[modules.shared]` is a **denylist** over `runtime/*.py` — every `[[deny]]` names the file, a kind, and a prose `why` — read by `tools/board_config.py`. So a new shared module reaches every board by default and staying off one is a written decision; the old per-board allowlists (54 `cp` lines on the T-Deck, 65 names on the P4) are what let the T-Deck silently miss the web console. The P4's `[modules.device]` staging from the T-Deck tree deliberately stays an ALLOWLIST — `runtime/` is a shared tree whose default answer is "yes", a board's `modules/` is not. The stager also **prunes untracked strays**: `modules/` is gitignored and never cleaned while the freeze takes the whole DIRECTORY, so deleted modules (`canvas.py`, `palette.py`, `moy_lua_glue.py`) kept shipping in every image built on a warm tree. It needs the ESP-IDF 5.5 toolchain (`IDF_PYTHON ?= ~/.espressif/.../idf5.5_py3.10_env/bin/python`).
- The MicroPython console is the only firmware. (The older Arduino/PlatformIO serial-smoke firmware and the legacy LVGL `.moyproj` game-loop boot path were removed; git history has them.)

### Second device target: ESP32-P4 (Waveshare 7B) — bring-up (#58)

- `firmware/esp32_p4_wifi6_touch_lcd_7b/` — the desktop-tier board (7″ 1024×600 MIPI-DSI, GT911 touch, C6 WiFi over SDIO, 32MB PSRAM/flash). **NOT lvgl_micropython** (no P4/DSI support there): mainline MicroPython v1.28 with an out-of-tree board def (`boards/MOYBYTE_P4`) + `native/moy_dsi` (vendored EK79007 driver; DPI mode — the DSI peripheral scans a PSRAM framebuffer continuously, so there is **no per-frame flush** and the T-Deck's tx_color ceiling doesn't exist). Build/flash/monitor via `make firmware-build-p4` / `make firmware-flash-p4 PORT=/dev/ttyACM0` / `make firmware-monitor-p4 PORT=...` (→ `dist/p4/moybyte_p4.bin`, flashed at offset **0x2000**); serial = CH343 on `/dev/ttyACM0`, REPL stays alive (no native-takeover USB starvation). **Read that dir's README before touching the P4** — it records the hardware-learned constraints (SD power comes from the P4's internal LDO4 which stock MicroPython never enables; SDMMC slot 1 belongs to the C6 and claiming it panics the board; PSRAM must run at 200MHz or the DSI scan-out underruns; WiFi needs no C6 flash; a root-level VFS dir named like a frozen module SHADOWS it — hence the store root is `/moy/carts`, never `/moybyte/...`). The **console is staged** (2026-07-08): `build.sh` freezes the shared `runtime/` console **plus `wm_windowed.py`** (P4-only tier) and stages `device_canvas`/`device_api`/`device_wifi`/`moybyte.input` from the T-Deck modules tree; `moy_gfx`+`moy_alloc`+`moy_lua` (#67) ride `USER_C_MODULES` via `native/.staged/` (moy_gfx grew `blit565_scale`, the windowed composite kernel); the partition table is OTA-shaped 2×4MB + auto-vfs tail. The launcher boots under `WindowedWM` with carts on the internal-flash VFS. On-glass (2026-07-09): colors (canonical vs T-Deck byte-swapped RGB565 → `PAL565_WIRE`), flicker (DPI `num_fbs=2` ping-pong), touch (180° mount → `p4_input.FLIP_X/Y`) all fixed. Play perf comes from three levers: the quiet-frame partial repaint (`WindowedWM.draw_stack`), the hardware **PPA** game composite, and the **async-composite overlap** — a quiet game frame runs the composite via `moy_ppa.blit_async` and DEFERS the scan-out switch to the next loop's `P4Compositor.present_pending()` (fenced by `moy_ppa.sync`, a done-ISR counter), so the PPA DMA overlaps the loop tail + input poll (`blit_game(defer=not full)`; full paints stay blocking so chrome never races the DMA). Brick Siege 35→51 (PPA)→56 (overlap); most carts ~60. App-window **drags/resizes use the dirty-union restore** (2026-07-10, "smooth like a real OS"): the `_BackdropLayer` retained cache is re-stamped only over the moving window's recent footprint (`blit_strip_rect`, dest-clipped `blit565` — the full-screen 1.2MB restore was the drag path's dominant cost and no accelerator helps a 1:1 copy), and **resize is live-body** (frame+title+grip track the grip, retained content crops/reveals, real reflow on release; grip TOUCH target 2× the visual; the game window keeps the outline preview; web RecordingLayer falls back to full restore + outline — probe on `blit_strip_rect`). On-glass, owner-verified: drags 14→**30fps locked** (union alone bought ~nothing — default windows are ~60% of the desktop, so the wins were **body-subtract** restore-only-the-trail + the **stamp-defer**: the ~24ms content stamp runs async-PPA, registered by the WM and kicked by `P4Compositor.flush` AFTER bar/chips/cursor — an async PPA op must be the frame's LAST write, and `moy_ppa` must C2M-writeback dst before submit because **the IDF PPA driver invalidates the whole out buffer at submit**, discarding unflushed CPU writes — see the P4 README constraints). The render-overlap lever is RESOLVED: the **triple framebuffer** shipped (drags 30→42.8fps, `efcf5d1`) but its second half — the double game canvas — was built, measured and **REVERTED** the same day (`26e1f9f`, which leaves the verdict as a NOTE at the defer site: windowed Battle City 56→41fps — the game fence was already ~free; the ~150KB retention memcpy + drain stalls cost more than they saved), and then **#159's L2 cache 128→256KB** closed the game chapter outright (Brick Siege busy 15.5→8.0ms, the whole roster at the 60 cap; 512KB does not boot — internal/DMA pool 0x101). **The PPA only helps UPSCALE composites** — the game→window scale is 2.6× (tiny source read, hardware scale), but a full-screen 1:1 copy (the backdrop restore) is ~identical CPU vs PPA (~26ms, PSRAM-bandwidth-bound vs the DSI scan-out), and **sprite BATCHING is a dead end** (64× 16×16 queued = 4.57ms vs 0.70ms CPU, ~10× vs `spr_batch` — per-op submit dwarfs a tiny blit), so both stay on the CPU. `run_desktop` has serial dev commands (REPL-alive board): `run <name>`/`open settings|picker`/`drag [frames]`/`cache 0|1`/`diag 0|1`/`skip 0|1`; `moy_runtime.run_ppa_smoke()` A/Bs the composite. #58 is the living port status; open perf follow-ups: the **editor-tab/transition draw cost** (dispatch-bound — the native span-batch verb #163 is the ranked lever, the scene renderer second) and the #113 Phase 5 Settings partial repaint. Also open: USB-HID keyboard, audio (ES8311), OTA/web-view wiring.

### Third target: the web runner + the moy-spec repo (#151/#170)

- `firmware/web_runner/` — the **MicroPython-WASM** build of the same console.
  **The browser is no longer the GPU (moycore stage 4, 2026-08-12): the wasm
  RASTERIZES** — the narrative is `docs/moycore_plan_2026-08.md` §6; what a coder
  needs here is the shape. `build.sh` compiles `moy_gfx` + vendored libmoy in as a
  usermod; the shell draws on `DeviceCanvas` itself (staged from the T-Deck tree)
  wrapped by `web_canvas.py` — `WebCompositor` supplies a buffer and no flush,
  `WebSystemCanvas` adds font-scale text, font-scale layers and `blit_cover`. The
  page's only pixel work is a 64K-entry 565→RGBA table plus `putImageData`. No
  board lever is reimplemented: bounce pump, DPI ping-pong, GDMA, PPA and PSRAM
  pooling are all probe-guarded and simply absent here.
  The handheld tier is the T-Deck's arrangement (one canvas, system IS game), the
  desktop tier is the P4's (big system canvas + a real 320×240 game canvas +
  `blit_game`). **`blit_cover` is not optional on a 565 system canvas** —
  `wallpaper._backdrop_blit` probes for it and otherwise expands a palette-INDEX
  buffer that does not exist there, drawing nothing: a black desk with correct
  chrome on top, which is exactly what the first build did.
  Numbers live in the plan; the operational summary is that the console half of a
  frame is now a fraction of a millisecond and the PRESENT (heap copy + RGBA
  expansion) is the expensive half, so optimize there first. Real headless Chrome
  holds 60fps locked.
  **This bundle RIDES BOTH BOARD IMAGES (2026-08-15).** A board serves the wasm
  console over its own WiFi (`moy_webhost`), and it used to serve only a copy a
  human had put on its storage — which drifts with nothing to detect it (a board
  served a bundle old enough to carry a desktop-blackout bug fixed hours earlier),
  and the T-Deck cannot be pushed to at all because the push hands the board a url
  over serial and that board's USB-CDC RX is dead under the desktop. So
  `tools/gen_web_blob.py` `.incbin`s the four PRE-GZIPPED assets (572,693 B; raw
  is 1,155,953 B and does not fit the S3's slot) into the `moy_web` usermod, which
  hands them out as READ-ONLY memoryviews at flash — never a `bytes` per request,
  on a board with ~23KB of internal SRAM free in play. **Storage still WINS**, so
  `make p4-web-push` stays the sub-minute dev loop and the image is the guarantee,
  not the ceiling; `start()` prints which of the two it is serving, because a
  stale pushed copy shadowing a good baked one is the same bug one level down. The
  cost is ~573KB of every app image and of every OTA payload, which left the
  T-Deck **186KB of its 5MB slot** — so an oversized image is now a BUILD FAILURE
  on both boards (it was a warning that still emitted artifacts esptool would
  refuse), and the next growth spurt is a partition-table decision, not a
  surprise. Building the bundle needs emsdk, so a missing one only WARNS locally
  and FAILS under `CI`/`MOYBYTE_REQUIRE_WEB_BUNDLE` — the firmware workflow builds
  the web runner before each board, so a published image always carries one. **The browser runs moycore too since 2026-08-13** — the
  usermod is staged beside `moy_gfx`/`moy_lua`/`moy_audio` and `web_boot` wires
  the boards' chooser verbatim, so a Lua cart's whole frame runs inside libmoy
  on all three tiers rather than two. It needs no wasm variant of the module:
  its `micropython.mk` compiles only `modmoycore.c` + libmoy's Lua binding and
  reaches the raster and the VM through its staged SIBLINGS, and its board
  allocator is `__has_include`-guarded down to `realloc`. `build.sh` also clones + patches the webassembly port (custom
  `moybyte` variant: GC_SPLIT_HEAP_AUTO, no asyncify) and compiles `moy_lua` +
  `moy_audio`; two of its Makefile patches are load-bearing and non-obvious.
  (1) `-Wno-unknown-pragmas` has to be appended to the PORT's CFLAGS, not to
  `CFLAGS_USERMOD`: py.mk folds the usermod flags in at its include and the port
  adds `-Wall` afterwards, which re-enables the warning that `-Werror` then makes
  fatal. (2) `HEAPU8` is patched INTO the port's own
  `EXPORTED_RUNTIME_METHODS_EXTRA` list; that variable is set with `+=`, so a
  command-line assignment REPLACES it — which drops `getValue`/`setValue` and the
  VM's JS wrapper dies at boot with "Module.getValue is not a function". Dev loop: `moy.py run` (sub-second hot reload via `?dev=1` + `/stamp`);
  `node harness.mjs` drives it headless. **To see what the BROWSER shows, use
  `node pageshot.mjs <scenario.json> [outdir]`**: it boots the real wasm console
  from `dist/` and decodes the same framebuffer the browser blits, into PNGs.
  (It used to have to slice the page's replayer out of the page source and replay
  commands into a matching index buffer; there is no replayer now, so the harness
  is a decoder.) Scenario steps are
  `frames`/`shot`/`tap`/`hover`/`drag`/`key`/`py`/`note`/`stain`/`unpainted`.
  Reach for it FIRST on any "it looks wrong / it doesn't show up" report — a
  screenshot is the right evidence for a placement or retention bug, and the
  misplaced FPS chip (#178 tail) was invisible in a frame dump and obvious in a
  PNG. When a bug survives that (browser-only plumbing: the worker pump, the
  transferable framebuffer ping-pong, rAF), **`node browsershot.mjs
  <scenario.json>` drives the shipped page in real headless Chrome over CDP** (no
  puppeteer — it serves `dist/` and screenshots the canvas itself); its `js` step
  evaluates in the page. Note the page waits behind a play-button splash unless
  the scenario passes `"query": "?dev=1"`, so a scenario that forgets it
  screenshots a blank canvas and looks like a raster bug. **This build
  is Moybyte's own browser console and nothing else's** — the `--spec` slim
  player (24 shell modules AST-stubbed to absorbing `_Stub`s, de-branded,
  vendored into the spec's `runner/` and published by a `web-player` workflow)
  was **deleted 2026-08-06**, along with that workflow, the `MANIFEST.json`
  stamp and the MIT carve-out in `LICENSE.md` that existed to let it be
  redistributed. The public spec repo (`~/Documents/Work/moy-spec`,
  github.com/moybyte-org/moy-spec, MIT) now **builds its own player from
  libmoy** (`libmoy/port/wasm`, emscripten): 297KB against the MicroPython
  build's 1,001KB, because SPEC.md pins Lua as the cart language so the Python
  VM was only ever there for the shell `--spec` stubbed out — and because a C
  raster in wasm can fill 76,800 px/frame, so the page's whole JS
  draw-command replayer stopped being necessary. That repo is still SPEC.md
  ("moy core 0.1") + runner + the `moy` CLI (new/run/export/port/demo) + the
  **p8 converter, which is UPSTREAM of us** (2026-08-15). This line used to say
  "re-vendor `p8_import.py` whenever `tools/import_p8.py` changes", i.e.
  moybyte → spec, and it had the arrow backwards: SPEC.md is what says what a
  converted cart MEANS (§8.1 fixes `57 = A4 = 440 Hz`, hence PICO-8's pitch
  offset of 24; §8.1's keyed rest is what makes a ported slide glide from the
  right note), so corrections are worked out there and travel HERE. They did
  not: upstream fixed the offset `0 → 24`, our hand-copy never heard, and
  **every cart imported through this repo came out two octaves flat while
  `make test` stayed green** — the tests had pinned the wrong model too, so
  re-syncing meant deliberately breaking a passing test and nobody did.
  So it is vendored now, like libmoy: `tools/p8_import.py` is moy-spec's file
  byte-for-byte, refreshed with **`make vendor-p8-import`**
  (`tools/vendor_p8_import.py`, stamping `tools/p8_import_vendor.json`), and
  **editing it here is a red test** — `tests/test_p8_import_vendor.py` hashes
  it against the manifest, diffs it against a sibling checkout at the pinned
  commit, refuses to let `tools/import_p8.py` re-grow a converter verb, and
  checks the thing that actually broke: that the converter and the vendored
  synth (`moy_audio.c`'s `pitch - 24.0f`) still put A4 in the same place. What
  stays ours in `tools/import_p8.py` is only the CLI, the `.moy` folder
  writer, and the guided PICO-8 → Python port notes (#36); a Lua port is `moy
  port`, over there. One thing the swap COST, recorded in the spec's
  `conformance/README.md`: that JS replayer was the project's only *independent*
  raster, and moycore/libmoy/moy_gfx all share one lineage — the ESP32-P4 run is
  now the only cross-check that cannot have inherited a bug. AUDIO on the web
  (#170): the console ships per-frame FINISHED PCM
  (base64) and the page plays it through ONE AudioWorklet ring (continuous
  resample, seam-free; starvation decays instead of hard-cutting); the runner
  tops a ~120ms cushion via the page-reported queue depth
  (`step_frame_json(dt, audio_ahead)`) — the browser twin of the device ring
  top-up. p8 IMPORTS are full-fidelity since #170: 8 waves 1:1, effect column
  verbatim, 4-channel music rows, SFX loop ranges, per-row pattern lengths
  (`row_secs`) by the **zepto8-verified** length rule (first non-looping
  channel; all-looping → slowest channel — the p8 wiki's "all-looping loops
  forever" is WRONG, trust zepto8 for p8 semantics). `ports/celeste.moy` is
  gitignored on BOTH repos (CC BY-NC-SA — never commit or ship it); regenerate
  with the moy-spec CLI: `moy port <cart.p8.png> ports/celeste.moy --title
  "Celeste Classic" --zoom` (the port tool lives ONLY there now — one
  converter, no drift). **Do not drop `--zoom`**: it is what bakes the
  `view(128, 120)` hint, and without it the T-Deck letterboxes the 128px
  square at 1× instead of compositing the centered 128×120 at 2× — a
  regeneration on 2026-08-11 lost it and shipped a tiny celeste to the glass.

### Host == device: the shared console (important)

The console UI is **one codebase** that both the host simulator and the
device run — they render the *same* 320×240 pixels with the *same* petme128 font.
The canonical sources live in `runtime/`; `build.sh` **stages copies into the
firmware `modules/` tree** so the device freezes the identical code (same pattern,
re-staged every build, gitignored). The shipped **2026-07 shell** (spec
`docs/shell_ux_v1.md`) is everything-is-a-process: two concerns — authoring and
playing — joined by one primitive, `run(cart)` plays until exit and returns control
to whoever called it.

- `runtime/console.py` — the shell **kernel**: `Workstation`, shrunk by the 2026-07
  refactor to a compositor/router — the Layer-stack frame/input/pointer loop, the
  shared draw toolkit (`_glyph`/`_icon`/`_btn`), store/service attach points
  (`carts_store`/`wifi`/`updater`) and the spawn/exit verbs; everything
  the user sees is an app it runs. Backend-agnostic: injected `make_api` + cart
  store. (frozen as `console`)
- `runtime/project.py` / `player.py` / `editor_app.py` / `wm.py` — the 2026-07 shell
  split (build-staged like the rest): **`Project`** = the open cart's live workspace
  (cart/config/sheet/tilemap/images/pmem + the `commit_*` persistence verbs; a commit
  also appends the undo journal and runs graduation detection). **`Player`** = the
  `run(cart) → plays → returns` black box: starts the cart under the frozen
  `make_api`, ticks it, turns any crash into the **crash-to-code throw**
  (2026-07-23: the run exits straight into the Editor's Code tab with the caret
  on the crashing line + a tap/type-dismissible error popup — `ws._crash_to_code`,
  armed from both the failed-start path in `run()` and the mid-frame capture; the
  old parked OOPS panel survives only as the no-open-cart fallback), owns the transient
  hold-to-exit toast — zero knowledge of who launched it; exit pops to the run
  CALLER (launcher→launcher; Editor-PLAY→the same tab). **`EditorApp`** = ONE
  authoring app opened on a `Project`: the tab ladder Config→Blocks→Code→Sprites→
  Map→Scene→Music (+ PROJECTS/UNDO/REDO/PLAY in its lent bar zone — there is NO
  SAVE, see #111 below), whose tabs ARE the
  extracted `*_layer.py` surfaces — all seven tabs are **system-domain responsive**
  (#39 step 3: `PaintLayout`/`MapLayout`/`SceneLayout`/`MusicLayout`/`CardsLayout`
  join `CodeLayout`/`BlockLayout`, each `_base`-verbatim byte-identical at 320×240/1×),
  so the whole Editor reflows to any panel/window size and only a RUNNING CART still
  draws on the fixed 320×240 game canvas. **`FullscreenStackWM`** = the small-screen
  tier's WM: the process back-stack (`screen` is a read-only projection of its top),
  the **memoized** visible/draw stack (zero per-frame list churn, #66), and the
  game↔system viewport composite. **`runtime/wm_windowed.py`** (`WindowedWM` —
  host/P4 only, deliberately NOT staged to the S3 build) is the second presentation
  tier (#73/#58 "Desktop look"): the Picotron-style windowed desktop, now split
  into **TWO WORLDS (#105, 2026-07-20)** — boot lands on the **DESK** (stack kind
  `"desk"`, the make world's floor: wallpaper + a system-app icon column
  (PLAY/PROJECTS/Files/Paint/Writer/Sheets/Storybook/Calc, tile-0 cart art) +
  the one OS bar with taskbar chips and NO context-X), where every process above
  the desk is a window with a WM title strip (minimize/maximize/close),
  draggable by the strip and resizable by the bottom-right grip
  (apply-on-release rubber band). The **PLAY icon drops to the fullscreen
  Library** (the play world): system-app carts leave the shelf on this tier
  ("apps are windows, games are fullscreen"), a tap runs the game FULLSCREEN
  exactly like the small tiers (windows only exist above `"desk"`, so every
  `not _order` deferral presents fullscreen; `composite_game` routes the play
  world through the polymorphic `_blit_game` — native P4 blit / web b64-spr),
  and the Make tile / a card's CHANGE drop back to the desk (CHANGE with that
  project's Editor open). `ws.windowed_chrome` is a world-aware PROPERTY
  (`wm.desk_open()`), and a world flip triggers a relayout so app-layout chrome
  never leaks across worlds. The **picker + Editor share ONE
  "Make" window** (`_GROUP`): picking a project swaps its content to the Editor,
  PROJECTS/its X swap back (the back-stack keeps both kinds — presentation-only
  merge). **Input focus is decoupled from the back-stack**: clicking a window or
  its chip moves keyboard+highlight WITHOUT popping — a playtest keeps ticking
  (it stays the stack top) while the Editor beside it is typed in, its pointer
  feed click-stripped so the background cart never eats editor taps; only an
  explicit exit ends a run (strip X / hold-BACKSPACE while focused / app verb).
  True multi-cart (N games ticking) stays out of scope per #73.
  In the desk world the zoned bar suppresses its OS right zone + the dock
  inside windows, so a window's bar row is purely the app's toolbar — the desktop
  bar is the ONE taskbar. A running desk-world cart composites integer-scaled + centered in
  its window (no minimize — hiding a game would silently pause it); per-window
  **layout contexts** re-run the #39 responsive layouts at each window's size,
  and `Wallpaper.draw` composites/fills the SYSTEM canvas (cover-crop backdrop)
  so the big desktop backdrop is real. **Panel THEMES** (`chrome.THEMES`,
  picked in the Appearance app, persisted): named token sets (`panel`/`edge`/`title`/
  `title_ink`/`accent`/`hilite`/`dim` + the §4.3 semantic roles) that every panel
  surface reads per draw — Settings panel, picker backdrop, window strips/chips,
  launcher selection accents, the OS bar/dock, the ≡ menu, achievements, the OTA
  screens, and (on non-`_base` tiers) the editor tab surfaces; the default "night"
  is the moybyte site colorway and keeps the frozen colors byte-identical. Every
  family ships a **dark AND a light variant** (2026-07-23: Appearance → THEMES →
  DARK/LIGHT chips, `theme_colors(name, variant)`, persisted `theme_variant`;
  light = paper/pastel fields + dark ink via the `bar`/`chrome_ink`/
  `selection_ink`/`bar_light` roles, whose statics ARE the frozen dark literals —
  see docs/visual_identity_v1.md §4.3 status). **WiFi setup lives in Settings** (#38, spec §10): a
  WIFI row + panel (scan/password/connect/forget over the injected `ws.wifi`),
  so it works while a game runs — the bar's wifi icon deep-links there in
  windowed mode (fullscreen tiers keep launching the wifi.moy tool). The default
  wallpaper is **`moy_night.moy`** (static brand-colorway scene — a static
  wallpaper keeps the idle desktop free under the redraw gate). The wasm head
  runs this WM as an ordinary raster tier since moycore stage 4 — window buffers
  are real layers, the game and wallpaper composite through `blit_game`/
  `blit_cover`, and the recording transport that used to need special cases here
  (RecordingLayer window buffers, composites shipped as one self-contained spr,
  scaled text recorded as rect blocks, the bar drawn uncached) is deleted. Try it:
  `python tools/simulate_desktop.py --size 1024x600 --windowed`.
- **Editor-as-an-app UX (this replaced the maker/player tap-mode):** a launcher tap
  **always RUNS the cart** — no mode, no type dispatch. The pinned **"Make ✏️"
  tile** opens the Editor **project-picker** (the same grid over every editable cart
  + a ＋New tile), which owns project management (＋New / Copy / Delete — delete is
  a two-tap confirm); the launcher home has no new/dup/del. **Wallpapers are
  backdrop-only**: excluded from the run-grid, chosen in the **Appearance app**
  (wallpaper + panel theme — the ONE appearance surface; Settings' APPEARANCE
  action row deep-links to it, the old WALLPAPER/THEME stepper rows are gone),
  still editable via the picker.
- **The zoned top bar (#46, macOS-menu-bar model):** one OS-owned 18px bar. RIGHT
  zone = OS status (clock/wifi/batt/≡ + a **context-X** that exits the active app;
  the launcher root draws no X). LEFT zone = LENT to the active app (`draw_zone`):
  the launcher shows the selected cart's name, the Editor its PROJECTS/tab-ladder/
  UNDO/REDO/PLAY icons. Icons stay 16×16 sprites from the editable `IconSheet`
  (Settings → EDIT ICONS). **The bar hides entirely while a GAME plays** (the cart
  owns all 320×240); tools/apps run WITH a minimal bar (title + status + X) so
  they're always exitable.
- **Exit model (#71's pause machinery is retired):** a fullscreen GAME exits on a
  sustained **hold-BACKSPACE (~700ms)** — raw-matrix mode streams the held key, a
  transient progress toast fills, the pop returns to the caller; a quick tap is a
  plain cart key. Taskbar tools/apps exit via the context-X, so BACKSPACE stays an
  ordinary key there (the wifi password field's delete works with zero
  special-casing). A `textmode(True)` game provides its OWN exit via the additive
  cart verb **`quit()`** (in text mode BACKSPACE arrives as a typed delete and the
  keyboard has no autorepeat, so the console's gesture can't reach it) — Letter
  Blitz models it with a tap-✕. (The plan's triple-tap alias was dropped after
  on-device testing.)
- `runtime/editors.py` — `CodeEditor` / `SpriteSheet` / `PaintEditor` cores, plus
  `IconSheet` (16×16 themeable system-bar icon tiles; Settings → EDIT ICONS repaints it). (frozen as `editors`)
- `runtime/moy_carts.py` — the `.moy` store (scan/load/save_*/create/duplicate/delete;
  versioned `seed_builtins` re-seed; `system_icons.moygfx` bar theme; only `json`+`os`)
  plus the **per-project undo/redo journal** (`journal.jsonl` append-only + full-file
  snapshots + an atomic **per-file cursor map** (#111: tolerant migration from the old
  single seq), torn-snapshot-safe; commits fire on a typing-idle autosave debounce +
  hard commits on EVERY exit path — tab-leave/PLAY/workspace-swap/HOME/window-X; there
  is NO SAVE button or concept, and no dirty-star UI. **Undo is unified (#111,
  `runtime/op_history.py`)**: every Editor tab + Writer/Sheets keep fine-grained in-RAM
  ops (`History`/`OpCodec`, keyframe+ops; commits embed the op batch in their journal
  line, Desk-Lab apps persist theirs in `files/.history/` sidecars), and the ONE bar
  UNDO/REDO pair walks local ops first, then whole commits **scoped to the active
  tab's file(s)** — never another tab's) and **blocks↔code graduation** (MakeCode model: a diverging code commit —
  conservative recompile-and-normalize compare — stores `"graduated": true` in the
  manifest with a journal rider; the Blocks tab goes read-only + celebrates; undoing
  past the graduating commit un-graduates). Also owns the **wallpaper-preview sidecars**
  (`<cart>/thumbs/wp<w>x<h>.mct`, stamped via `cover_sig`; a regenerable cache
  whose readers validate magic+size+stamp — a computed preview FRAME is far
  dearer to rebuild than to read). Cover thumbs used the same machinery (#66/#86)
  and were **removed** in #155: with `moy_gfx.decode_runs` + `crop_index` the
  RLE decode fell from 0.5–1.7s to ~1.7ms, so reading a per-size crop sidecar
  (~66ms on the P4's ~470KB/s flash) cost the same as rebuilding from the blob
  while charging a ~30ms write per cover per size. Covers now cache their PARSED
  RUNS in RAM instead (`Workstation._cover_runs`, ~15KB each), which is what
  makes a window resize cheap. Also passes the **#67 dual-runtime fields** (`runtime`/`main`) through
  load/save_code/create/duplicate/seed_builtins, so a lua cart's source stays in
  `main.lua` end-to-end (save_code only Python-syntax-gates python carts). Newer
  asset kinds ride the same load/save/create/duplicate/seed flow: **scenes**
  (#85: `scenes/*.moyscene` placed-actor tables, manifest `assets.scenes`
  ordered with element 0 the default; consumed via the `scene()`/`load_scene()`
  cart verbs over `widgets.Scenes`; authored WYSIWYG in the Editor's **Scene
  tab** — `scene_editor_ui.py` over the `editors_scene.SceneEditor` core, which
  live-syncs each gesture into `ws.scenes` so PLAY re-starts on the freshest
  placement) and the **Desk Lab interop docs** (#78:
  `tables/*.moysheet` from the Sheets app + `docs/*.moytext` from Writer, read
  back via the `table()`/`text()` cart verbs — all in `docs/moy_cart_api.md`).
  Also owns the **#108 user-files layer**: `files/<kind>/` BESIDE the carts dir
  (drawings/docs/tables/sprites/music + folder-valued recordings — the
  `FILE_KINDS` registry), with list/load/save/rename/duplicate verbs, a
  restorable `files/trash/` (count-pruned, no confirm dialogs), auto-naming
  (`new_file_name`), and the one-shot `artwork.moyimg → files/drawings/`
  migration. Browsed by the **Files system app** (`files_app.py` over the
  shared `file_widgets.FileGridView` thumbnail grid — the same widget Paint's
  OPEN mode embeds); Paint autosaves NAMED drawings on an idle debounce and
  WALL/GAME/wallpaper are **copy-on-use** (the design discussion + decisions
  live in #108's comments). (frozen as `moy_carts`)
- **Dual cart runtimes (#67, on-glass both boards 2026-07-14):** a manifest
  `"runtime": "lua"` (+ `"main": "main.lua"`) routes `Player.start` through the
  injected `ws.lua_runtime` factory instead of the Python compile/exec path — a
  build without the runtime opens the normal cart-error panel. Host runner:
  `runtime/lua_host.py` (lupa, Lua 5.4 pinned, stdlib sandboxed to
  base/math/string/table, lupa's `python` bridge removed; injected by
  `build_workstation` when lupa imports — an optional dev dep). Device +
  browser runtime: **`moycore`** (`native/moycore/`, staged to both boards and
  the wasm head), which binds the vendored Lua 5.4 through **libmoy's own
  binding** (`libmoy/moy_lua.c`, vendored from moy-spec): all 38 SPEC verbs are
  C functions and `moycore.tick(dt)` runs `_update` and `_draw` back to back
  without re-entering Python — ONE upcall per frame instead of hundreds. One VM
  per run, the cart's whole Lua heap OUTSIDE the MP gc heap (freed wholesale at
  close), input arriving as an `array("i")` snapshot refreshed before the tick,
  audio leaving as a queue drained through the same `make_api` closures a
  Python cart uses, pmem C-side with a dirty flag persisted at the #66
  boundaries.
  **There is exactly one Lua runtime and no chooser** (2026-08-13). The old one
  — `LuaCartRun`, ~40 registered Python trampolines, the `bind_draw` direct-draw
  family, the `spr_gate` batch protocol (token `0x7A11`), `moy_lua_glue.py` and
  `modmoy_lua.c` entire — is DELETED; `native/moy_lua/` is now the vendored VM
  and nothing else, and `import moy_lua` is meant to fail. Read the deletion
  commit before proposing to bring any of it back: it was kept as a fallback
  long after it should have been, and while it was there it silently ran every
  layer cart.
  What moycore registers ON TOP of libmoy's table is a **deny list, not an allow
  list** (`moycore_glue.LIBMOY_VERBS` + `NOT_REGISTRABLE`, twinned in
  `runtime/lua_host.py`): what is stable and enumerable is what libmoy OWNS, and
  an allow list silently drops any moybyte verb nobody remembered to add — which
  it did. Object-valued verbs (`make_layer`/`draw_layer`/`image`) are never
  registry entries at all: a trampoline marshals scalars and a Layer comes back
  nil, so they ride int handles plus a Lua prelude, and **that glue is
  `runtime/lua_ext.py`** — ONE definition every runtime imports, including the
  host's ctypes binding. It is one file because it was two: the copy the host
  did not have is why layer carts crashed there and merely fell back on device.
  **If you add a runtime, import that module; if you add an object-valued verb,
  it goes there, not in a verb list.**
  Three things moycore was missing when it shipped, each of which would have read
  as "moycore made the cart slower" with nothing pointing at a cause, all fixed
  2026-08-13 and pinned in `tests/test_moycore_loop.py`: the **p8 shim's masked
  map walk** (`__moy_map_masked`/`__moy_map_flags`, #66 M0 — 4.5ms of celeste's
  S3 render, and the shim nil-guards the names so losing them is silent), the
  **SRAM-floor knob** (`run_desktop` drops the Lua allocator's internal headroom
  48→24KB at boot and moycore had no such name, leaving a cart at ~97% PSRAM —
  the measured-2× regime), and a **seeded `rnd`** (libmoy's xorshift32 treats
  `con->rng == 0` as a fixed constant, so every run of every cart drew the same
  sequence; the old prelude had shadowed `rnd` with Lua's per-state
  `math.random`, which is why nobody saw it).
  **The semantic PIN is `tests/test_semantic_traces.py`** — twin Python/Lua
  carts fed scripted input, replayed through the real glue on the unix
  dual-usermod build, hash- and log- and audio-order- and pmem-compared. It
  drives moycore now, and the first time it did it caught a real divergence:
  libmoy's `camera` returned nothing where moybyte's (and TIC-80's, and
  PICO-8's) returns the PREVIOUS offset, so `local px, py = camera(x, y)` read
  nil. Fixed upstream in both the binding and SPEC.md §6's table. Run it before
  crossing anything further, and extend its trace vocabulary FIRST —
  `docs/moycore_plan_2026-08.md` (tracker #192) is the direction doc.
  Every non-spec verb still trampolines to the SAME Python `make_api` closures
  (tuple returns fan out to Lua multivalues, so `touch()` needs no wrapper).
  Related trap in the same family, fixed upstream: `moy_console` holds its sheet
  and map by POINTER and a brand-new project has neither, so `spr(0,0,0)` in an
  empty cart used to segfault libmoy's binding — a board reset with no message. Related S3 lever, same date: the **#190 flush-bounce scale fold** —
  a small-canvas game frame's composite is synthesized inside the bounce bands
  (root fb untouched on quiet frames; overlays disarm and pay the old cost;
  PUMP diag `fold=N` is the on-glass proof). Three hardware-learned constraints
  live in the module: the vendored
  lua sources carry **in-source `-O2` pragmas** (historically a guard against
  `-Os` halving the VM; today's builds resolve usermods to `-O2` globally, so
  the pragmas PIN that — and `-O3` on the VM is a measured ~2–5% REGRESSION on
  the P4 (#159) and a measured NULL on the S3 (#190 A/B, 2026-08-11), so O2 is
  the affirmed setting on BOTH boards, not a leftover),
  the `lua_Alloc` is
  **internal-SRAM-first with a 48KB headroom floor + PSRAM fallback** (the
  all-PSRAM version measured ~2× slower on the S3's 120MHz-OCT bus), and
  `lua_to_mp` **small-ints integer args + returns interned names as qstrs**
  (#107: `mp_obj_new_int_from_ll` per coordinate was ~64B × every upcall arg —
  celeste's 11KB/frame of marshalling garbage, i.e. a 160–200ms auto-collect
  every ~6s of play; from_ll survives only as the >31-bit fallback).
  `system_carts/sakura_lua.moy` is the A/B twin of sakura (line-faithful,
  bit-identical over 600 host frames — `tests/test_lua_sakura_parity.py` +
  `experiments/lua_bridge/host_parity.py`); the measured cross-board verdict
  lives in #67 (S3: parity with auto-native Python; P4: logic 3–4ms flat vs
  Python's 6–7ms with 19–24ms GC spikes). A canvas without a `_batch_arr`
  (the wasm head's recording CommandCanvas) declines the C fast path — the
  Tee-specific decline guards died with the 2026-08 streaming sunset. The
  Phase 4 protocol tests + Phase 5 UX tail
  (crash-line mapping via `player._lua_cart_line`, the per-language symbol
  palette in `code_layer`, the docs Lua section) shipped 2026-07-15 (`21c1278`).
  **`LUA_32BITS` is DECIDED AND ON** (`6ddaf7c`, `luaconf.h`): both boards' FPUs
  are single-precision, so doubles would be soft-float — 32-bit floats use the
  hardware FPU and halve TValue. The host runner (lupa) stays on 64-bit doubles,
  so golden-frame parity is host-only for float-heavy carts and device integers
  wrap at 2^31. Recorded because this line said "remaining: only the LUA_32BITS
  decision" for weeks after it was made, and a 2026-08-09 perf hunt spent its
  last lead re-proposing a lever that was already shipped.
- **pmem persistence is DEFERRED (#66, on-glass 2026-07-14):** `pmem(i, v)` is
  RAM + a dirty mark; `Pmem.flush()` persists at cart exit (`release_world`),
  the crash capture, the workspace swap, and a periodic frame-boundary save
  (`player.PMEM_FLUSH_MS`, 60s). The old per-write SD save was Letter Blitz's
  81–130ms per-pop "word-event logic spike" (probe-attributed on glass). The
  perf_capture-gated `PMEM save=<ms>` diag line shows the deferred cadence.
- `runtime/font.py` — petme128 8×8 font, the ONE glyph source both backends rasterize (#62): the host draws it per-pixel, the device passes its blob to the native `moy_gfx.text` kernel (staged as `moy_font` at build; framebuf.text — same glyphs, no clip rect — is the no-gfx fallback).
- **UI scrolling is kinetic + scroll-as-blit (#113, 2026-07-22 — the living plan/status issue):** `ui.ScrollRegion` owns the fling physics (all dt INJECTED from the loop — never a clock — so tests are exact-trajectory deterministic) plus a painted-frame ring; an eligible drag/fling frame SHIFTS the retained pixels via the `scroll_rect` system verb (ONE implementation on every tier since the canvas flip — `DeviceCanvas.scroll_rect` over `moy_gfx.scroll_rect`, which the host reaches through `runtime/gfx_binding.py`; the old host `canvas.py` lane is deleted) and repaints only the exposed band (`Launcher.draw_shift` — the home shelf + Editor picker pilots; Settings still row-snaps, its pixel-smooth conversion is #113 Phase 5). The learned rule: **everything inside a scrolled band must be a pure function of the offset** (the picker's dots now ride the scroll in-band). The ring pins sel/statics/`ws._cover_gen` and measures against `RETAINED_FRAMES` paints back (host/layers 1, device root ping-pong 2). Web transport: the `scr` op shifts the browser's retained buffer (never deduped to `{"same":1}` — replaying a shift double-applies), covers + the static wallpaper composite ship ONCE via `/assets` (`ws.cover_assets`, serial names), and the windowed WM's gesture-vs-window checks resolve by IDENTITY (`_wins.get(key) is win` — the shared "make" group's `win.kind` is the CONTENT kind, so `key == win.kind` never matched and silently disabled the drag content-freeze/stamp-defer everywhere).
- `runtime/host_app.py` — host glue: host `make_api`, `build_workstation()` (injects `ws.lua_runtime` when lupa imports, #67), `ConsoleDriver` (mouse=touch, arrows=trackball). Not on device.

(The pre-unification host UI — `shell.py`/`workstation.py`/`engine.py`/`api.py`/
`cartridge.py` — was removed once the shared console replaced it; issue #17.)

### Device module map (`firmware/lilygo_t_deck_plus_micropython/modules/`)

- `moybyte_shell.py` — boot/`main()`; mode flags `RUN_DESKTOP` / `RUN_TOUCH_CALIBRATE` / `RUN_KEYBOARD_PROBE` (the STAGE3/NATIVE_CORE bring-up benches and the pre-display SD-prefetch A/B toggle were removed; the #63 `MOYBYTE_BENCH=1` build is the benchmark harness).
- `moy_runtime.py` — the **device backend**: `DeviceCanvas` (hot ops `cls`/`rect`/`circ`/`spr` go through the native `moy_gfx` kernel — `fill`/`fill_rect`/`blit565` straight into the compositor's RGB565 buffer — with framebuf for text/lines and as the no-`moy_gfx` fallback; `spr` blits a per-sprite pre-scaled RGB565 cache, and `make_api` reuses one tile `Image` per `(id, colorkey)` so the cache survives across frames), `make_api`, embedded fallback `CARTS`, `TrackBall`, `Touch`, `run_desktop()`, `run_keyboard_probe()`. Imports the shared `console`/`editors`/`moy_carts` and injects the device `make_api` + store into `console.Workstation`. **Input runs on a poller thread (#69, `MOY_INPUT_POLLER`)**: `moybyte.input.InputPoller` owns every I2C0 transaction (kbd + GT911 + mode switches) off the frame loop, so the C3's 40-60ms clock-stretch stalls block only that thread — requires the build's `esp32_i2c_gil_release.patch` (machine.I2C frees the GIL across its blocking wait); falls back to synchronous polling if `_thread`/the thread dies.
- `console.py` / `project.py` / `player.py` / `editor_app.py` / `wm.py` / `editors.py` / `moy_carts.py` (+ the `*_layer.py`/`*_ui.py` surfaces, `blocks.py`, `web_view.py`) — **staged from `runtime/` at build** (see above).
- `moybyte_sd.py` — SD mount on the shared SPI bus; `with_sd(fn)` = mount → run → unmount + deselect.
- `moy_compositor.py` — native RGB565 framebuffer + DMA flush.
- `tdeck_display.py` — display/LVGL + SPI bus bootstrap.
- `moy_ota.py` — OTA firmware updater (#53): `OtaUpdater` flashes a new app image from `/sd/update/*.bin` into the **inactive** OTA slot via `esp32.Partition` (block-erase `writeblocks`), then `set_boot` + `machine.reset`. Phase 3 adds WiFi download — `check_online`/`begin_download`/`download_step` stream a manifest-described `.bin` over a raw socket straight to SD (sha256-verified, never buffering the whole 3MB), reusing the injected `wifi` service. Device-only; `run_desktop` injects it into the shared `Workstation` (which owns all the update-screen pixels), wires the wifi service, and calls `mark_valid()` at a healthy boot to cancel rollback.
- `moy_webserver.py` — the device **socket/HTTP/WebSocket transport core**. Until 2026-08-12 this was the device WEB VIEW (#41/#22, owner-verified once on-glass 2026-08-01, #182) — the streaming browser mirror. **The whole streaming stack was DELETED in the 2026-08 sunset** (`docs/moycore_plan_2026-08.md` §3.2, owner decision; `tests/test_streaming_sunset.py` pins the absences): the frame push, `device_webview.py`, the recording `TeeCanvas`, stream mode, the Settings WEB VIEW row, `ws.web_hook`, the host `tools/web_console.py` + its VM deploy recipe, and the decline-the-Tee guards in `moy_lua_glue`. The browser's job belongs to the **wasm head** (`firmware/web_runner`), to be synced per §3.4; mirror-of-glass is an accepted loss (a screenshot verb on the sync RPC is the recorded open question). What survives here — deliberately, for the §3.4 sync RPC to ride — is the bare transport: non-blocking listener, `parse_request`/`http_response`, the RFC 6455 upgrade + framing (shared `web_view_ws`, the only file of that lineage the boards still freeze), one persistent non-blocking `_WSConn` (cross-iteration read buffer, blocking-budget sends, idle reaper), and a `WebServer` with `handle_http`/`on_text`/`send_text` seams, no consumer wired. **The recording stack is GONE as of stage 4** (2026-08-12): the wasm head rasterizes, so `runtime/web_view.py` and `runtime/web_view_page.py` were deleted outright with the recorder, CommandCanvas, RecordingLayer, ServedState, SurfaceDelta, WsClientState, the wire protocol and the page's JS replayer. Two pieces of that module were never about rasterizing and survive on their own: `runtime/web_input.py` (browser events → InputState/Pointer, which the §3.4 RPC also speaks) and `web_view_ws.py`. `runtime/surface.py` and `wm_windowed`'s `if not self._recording` guards deliberately STAY, unreachable — `docs/surface_model_v1.md` §13 records why, and is the place to argue with it. The XIAO Zero port stood entirely on the deleted stream; the owner re-based it the next day (plan §3.2): the browser runs the wasm head, and the Zero becomes the pocketable cart-store + GPIO peripheral it pairs with (#41 direction, #9 pins) — its rebuild rides the §3.4 track.

### Hard device constraints (learned the painful way — respect these)

- **SD shares the SPI host with the display.** **SD is no longer mounted before `init_display()` (#56).** The old boot prefetch read carts via `machine.SDCard` *before* the panel came up; that re-runs `spi_bus_initialize()`, and on a **populated** card the mount succeeds but leaves the shared host claimed, so the next `init_display()` intermittently failed with `can't convert '' to int` (the "no-SD / empty-SD boots, SD-with-files doesn't" bug, confirmed + fixed on hardware). So `moybyte_shell.main()` now defaults `PREFETCH_SD_BEFORE_DISPLAY=False`: **nothing touches SD before the panel is up**, and `run_desktop` loads carts *after* init via `with_sd_live` (`prefetched=None → _load_carts(with_sd_live)`), degrading to built-in carts on any SD failure (so this can only make display init MORE reliable). Mounting `machine.SDCard` **after** the panel is live still hard-hangs the board (gray screen, dead USB): `esp_lcd` and `machine.SDCard` are two driver stacks fighting over one host and CS-deselect alone is not enough — which is exactly why the post-display path uses the native `moy_sd` attach (below), not `machine.SDCard`. **Live reads/writes (post-display) go through the native `moy_sd` module** (`native/moy_sd/modmoy_sd.c`), which *attaches* the card to the host `esp_lcd` already initialized (`sdspi_host_init_device`, no bus re-init — the ESP-IDF "Sharing the SPI Bus" pattern) and leaves the panel device intact. `moybyte_sd.with_sd_live(fn)` mounts via `moy_sd` **once and keeps the card resident** for the session, then just runs `fn`. **Do not tear the SD device down between ops** (learned the painful way): a per-op `sdspi_host_deinit` — or reconfiguring the panel's `TFT_CS` via `Pin(...)` — corrupts the shared bus/DMA state and the *next panel flush silent-hangs the board* (the write itself lands on SD, then resume freezes; no panic, USB stays enumerated but dead). So leave `TFT_CS`/`SD_CS` alone (driver-owned; only park the unused LoRa `RADIO_CS` high) and never flush the panel inside the session — the desktop loop is single-threaded, so SD ops run between frames. On-device writes are enabled (`Workstation.can_manage`, wired to `with_sd_live` in `run_desktop`).
- **T-Deck USB under the desktop — the 2026-07-10 measured verdict (replaces the old "starves USB" lore).** Serial **TX streams fine during play** (PERF/MEMX/HITCH lines flow for hours — passively read `/dev/ttyACM*` with reconnect) and **esptool flashing works** — but its auto-reset never syncs on this board's native USB, so the board has to be put in the ROM loader BY HAND: there is no BOOT button — **the trackball click is GPIO0, so hold the trackball in while powering the board on**, then let go (the P4's CH343 auto-resets fine). **RX is dead on the shipped loop**: this fork's USB-CDC stack has no at-arrival interrupt-char scan, so Ctrl-C/REPL/commands never arrive — and there is no software workaround: a cross-thread `sys.stdin` read never completes (and eats Ctrl-C as data while blocked), `select.poll` reports stdin always-ready-when-empty, and a main-thread read stalls the desktop whenever a serial client is attached but quiet (a 30s kbd-attributed HITCH when it shipped briefly). Both experiment shapes were built, glass-tested and **reverted** — don't re-attempt in-loop serial commands on the T-Deck without fixing the fork's CDC RX first; the P4 keeps its `run`/`skip`/`diag` stdin commands (mainline + CH343 stdin semantics are sane).
- **Full-screen flush must be a single `tx_color`** from a PSRAM DMA buffer; multiple `tx_color` calls glitch rows at the command→data boundary.
- **The keyboard has two modes; the console flips between them per screen.** The T-Deck keyboard is a separate ESP32-C3 (I2C 0x55; firmware in `firmware/lilygo_t_deck_plus_reference/examples/Keyboard_ESP32C3` — an UNTRACKED vendor reference tree, so a fresh checkout will not have it; THIRD_PARTY.md's scope note explains why). In its default mode it returns clean 1-byte ASCII (shift→uppercase, sym→symbols/digits, all resolved on-keyboard) but reports each key **once on the press edge with no autorepeat** — so a *held* key can't be detected, only faked for `KEY_HOLD_MS` by `TDeckKeyboard`'s latch (movement stalls while you hold). For true hold-to-move, a running cart switches the keyboard to **raw-matrix mode** (`0x03`, `LILYGO_KB_MODE_RAW_CMD`): it then streams the full key matrix each read, so a held direction keeps firing. `Workstation._set_text_mode` → `TDeckKeyboard.set_game_mode(on)` drives this: ASCII for the code editor (so typing is clean — `last_key`), raw everywhere else. The revert is `0x04` (`..._MODE_KEY_CMD`) — the step an earlier attempt missed, which is why raw mode used to garble the editor *irreversibly*. **`__init__` boots in ASCII and never enables raw**; raw needs keyboard fw **≥ 2025-06-12** (`T-Keyboard_..._250620.bin`), and on older fw the `0x03` is ignored — `_read_raw_buttons` detects the stray ASCII byte and sticks the session back on the 1-byte + latch path (`_raw_unsupported`; class flag `RAW_GAME_MODE` force-disables raw). The keyboard has **no `=` `[ ] { } < > %`** keys at all → the code editor shows an on-screen symbol palette for those. (`0x01 <duty>` over I2C sets the keyboard backlight.) Use `RUN_KEYBOARD_PROBE` to dump keys over serial (USB-friendly, no takeover).

## Conventions

- The current design doc is **`moybyte_console_plan_2026-07.md`** (repo root); superseded v0.1/v0.3/v0.4 docs are archived under `docs/history/`. The **current `.moy` cart API** is documented in **`docs/moy_cart_api.md`**; the legacy `.moyproj` SDK specs (api / project-format / runtime-contract) are archived under `docs/history/` too. The shipped v0.5 shell's UX reference is **`docs/shell_ux_v1.md`** (corrected to the as-built reality); `docs/shell_architecture_v1.md` (privileged system carts + layered compositor) is the standing direction doc; the three implemented shell plan docs (`shell_ux_technical_plan_v1` / `shell_os_architecture_v1` / `shell_layers_refactor_v1`) are archived under `docs/history/`. **`docs/ui_damage_model_v1.md`** is the standing proposal for UI frame cost (nothing built): the desktop tier redraws whole surfaces every frame, the Editor tabs cost 76-92ms of *content-independent* chrome, and the shell already carries six hand-rolled partial re-derivations of region invalidation — two of which produced the same silent cache bug. It also records the **why-not-LVGL** decision with numbers (we ship LVGL on the T-Deck for panel/bus bring-up only, the drawing path left it at 47→90fps, and the P4 has no LVGL port), so that is not re-litigated. **`docs/surface_model_v1.md`** is its successor and the standing presentation CONTRACT for every backend (immediate widget logic + retained surfaces + explicit generation-counter dirty + per-backend compositing strategy): read it BEFORE touching any rendering/compositing/invalidation code on any tier, and treat its §8 graveyard as settled — a new backend implements its §4 compositor contract, it does not invent a new invalidation mechanism.
- **Issue mirror (`docs/issues/`, gitignored):** a **local, un-committed** snapshot of every GitHub issue, split into `open/` and `closed/` (files named `NNNN-slug.md`) plus `INDEX.md`, so an issue number referenced in a commit, doc, or chat resolves without network access. GitHub is the source of truth — this is a generated read-only mirror (not in git, to avoid churn), so a fresh checkout won't have it: **build it with `make sync-issues`** (wrapper over `tools/sync_issues.py`; needs the `gh` CLI, authed). The script wipes and rewrites both folders from `gh`, so state changes and edits never leave a stale copy. **Run `make sync-issues` at the start of any session that reads or reasons about issues, and again after EVERY issue you open/close/comment/edit** — the mirror is only trustworthy if syncing is a reflex, and living-body issues (like the #66 performance ledger) go stale locally the moment the body is edited on GitHub. Don't hand-edit the files.
- Tests run against the host packages only; firmware tests (`tests/test_micropython_spike.py`) grep the frozen device modules' source rather than executing them.
- **On-glass P4 testing (#156):** the P4's REPL-alive serial is a real test channel — `tools/p4_autotest.py` (`P4Board`: RTS-pulse reset, boot wait, command plumbing) drives the console over it, and `tests/test_p4_on_glass.py` is a pytest suite gated on `MOYBYTE_P4_PORT` (`MOYBYTE_P4_PORT=/dev/ttyACM0 .venv/bin/python -m pytest tests/test_p4_on_glass.py`, ~44s, one board reset per module, tests share the session in file order and leave the board on the desk). The device half lives in `moy_runtime.run_desktop`'s serial commands: **`swipe x0 y0 x1 y1 [frames]`** (a gesture through the real pointer feed — press edge, held interpolation, real release), **`state`** (one-line JSON: world/windows/focus/Settings scroll model/wifi/app claims — assertions read console STATE, not pixels), **`py <code>`** (eval/exec against the live console between frames: the profiling hook — wrap a draw verb, time a commit, count `rect` calls), plus `open appearance|wifi` plus the older `tap`/`run`/`drag`/`diag`/`skip`/`gov`/`union`/`cache`. Gotchas: **wait for `REMOTE drag done`/`swipe done`** before the next command (a mid-gesture command measures with the script still feeding the pointer); PERF's `wmr/wmw/wms` are last-sample values that go STALE when their pass stops running (a repeated constant means "not running"); allow ~10s after a first `open picker` at a new size (cover pop-in, #155); and **never put a call that blocks on FLASH inside a `pyexec` snippet** — `pyexec` uploads multi-line code in chunks while `cmd` sends one line, so a real `os.mkdir`/file write stalls the frame loop long enough for a streaming `diag 1` PERF line to interleave into the chunk exchange, after which the reader parses the fragment as a serial COMMAND and int()s its argument (surfacing as `PY ERR SyntaxError: invalid syntax for integer with base 10`, which names nothing that is wrong); issue those as their own short `cmd`. **Look system-app carts up by TITLE, never folder name** — the device seeds from the title slug (`appearance.moy`), the host copies the source folder (`theme_picker.moy`), and that mismatch is exactly what broke `AppearanceAppLayer.is_app` on device (pinned now by `tests/test_device_seed_parity.py`'s app-identity parity test). No T-Deck equivalent: that board's USB-CDC RX is dead under the desktop (see the hard-constraints section).
- **Cart versioning (#47):** every `system_carts/*/manifest.json` carries an integer `"version"`. `seed_builtins` re-seeds an on-SD built-in only when the baked version is **newer**, and preserves the kid's data (`pmem.json` saves + `config.json` tuning) across the re-seed. **Bump a built-in's manifest `version` whenever you change its content**, or an already-seeded device keeps the stale copy.
- **Device performance — the single source of truth is issue #66** (the living "performance ledger": current per-cart fps, the frame-budget model, shipped/reverted/open levers, how to measure). **Edit #66's body when new hardware numbers land** (comments = changelog), then `make sync-issues`; do NOT scatter numbers into this file, the plan, or new docs — they go stale. The **cross-board strategic analysis** (why we trail native emulators, the frame-budget taxes, the PPA scale-only verdict, and the ranked lever roadmap — frameskip / `-Ofast` / SRAM working set / `moy_gfx` IRAM / dual-core / Lua / render-overlap) lives in **`docs/perf_native_gap_v1.md`**, tracked by **#77**; P4 numbers are in **#58**. **No per-cart fps snapshot lives here** — one did, and it went stale exactly as this paragraph warns: it still quoted "Brick Siege 25-33" long after the board reached the 60 cap, inside the sentence forbidding scattered numbers. Read #66 for where the roster stands and #58 for the P4; the shipped-lever facts below are dated and stay. **The engine-side lever chain is exhausted** — every feed/dispatch/GC-cost lever tried and either shipped (auto-native carts #67, live-set diet, pal-state variant cache #72, layer pool, `background()`) or reverted with a recorded verdict (Fold-2 auto map cache, third bounce slot — the latter also retired the core-1 feeder unbuilt); remaining fps levers are per-cart render diets (Brick Siege field layer), the #67 Lua tier (strategic), and the P4 (#58). **Frameskip (#77) SHIPPED 2026-07-10, both boards** (Settings → FRAMESKIP, default OFF, persisted; P4 serial `skip 0|1`): a GAME's logic+input+audio tick every loop frame, render+composite+flush every second — logic at the full rate, motion at 30Hz; the trade is ~2× alloc churn (GC collects come ~2× as often). The same build's `-O3` moy_gfx pragma is **A/B-confirmed on the S3** (one pragma line: Brick Siege 33-36 → 51-54fps, render −40%; compute-bound there, unlike the dispatch-bound P4 where the same pragma measured null — per-board verdicts don't transfer), see #66/#77. Every draw verb is native (#43/#32/#62/#63) and the "draw LESS" idioms are both modeled by the seed carts and taught in docs/moy_cart_api.md → "Make it fast" — kids copy the carts, the carts model the doc. Remaining play defects: the #74 touch stalls (fingerprinted: boot-wake one-shot + rare steady-state; INT-pin gating is next) and the launcher live-wallpaper cost (~10fps launcher; the Make picker went static-black). Kid mode (#68): Settings → PERF DIAG (default OFF) gates the diag frame-eaters — **measurement sessions need it ON** — and DIAG SD LOG separately gates the periodic diag→SD write (keep OFF for stutter-free serial measurement). Three interpreter-vs-kid-idiom taxes were found and fixed ENGINE-SIDE, kid API untouched: per-draw-call dispatch (#43 batch + #63 native `spr_gate`), call-frame heap-spill (#63), and **float boxing** (#66: REPR_A allocated 16B per float result — 73KB/frame in sakura — whose heap-wrap gc collect was the long-standing ~150ms micro-stutter; fixed by the REPR_C build patch, unboxed 30-bit floats). Banding is structurally gone (#66 SRAM-bounce flush: the panel DMA only reads internal SRAM; the esp_lcd no-acquire patch makes banded tx_color queue-only). Remaining known frame spikes are tracked: #68 (diag-caused, kid-mode gate) and #69 (keyboard+touch I2C stalls, sized via I2CSTAT). Diagnostics: `PERF`/`DRAWBRK`/`DRAW2`(now with per-verb map/text/fill)/`BATCH`/`FLUSHBRK`/`CHROMEBRK`/`PUMP`/`I2CSTAT`/`CALIB`/`HITCH` serial lines, gated behind `perf_capture`.
- **OTA / on-device firmware update (#53):** the build is now **dual-OTA** (`build.sh --ota` → `otadata + ota_0 + ota_1 + vfs`; the slot sizes are NOT the same on the two boards and are not repeated here — read `firmware/lilygo_t_deck_plus_micropython/build.sh`'s generated table and `firmware/esp32_p4_wifi6_touch_lcd_7b/boards/MOYBYTE_P4/partitions-moybyte-p4.csv`, which are the only things that decide whether an image fits), so the device can flash a new `.bin` from `/sd/update` into the **inactive** slot and ping-pong (`moy_ota.OtaUpdater` + Settings → UPDATE FW). The running slot is never touched and rollback is on (`CONFIG_BOOTLOADER_APP_ROLLBACK_ENABLE`), so a bad image self-heals — `moy_runtime.run_desktop` calls `updater.mark_valid()` once the desktop is up to confirm the new image. **The app (ota_0) moved off 0x10000 → 0x20000**, so `build.sh` merges the full image at the derived offset and the Makefile uses `MPY_APP_OFFSET`. **BOTH BOARDS ARE ALREADY ON THE DUAL-OTA LAYOUT** (owner-confirmed 2026-08-15) and have taken OTA updates on glass — see the channel entry below. So the one-time migration is HISTORY, not a step anybody has to plan: `make firmware-flash-lilygo-micropython-full-erase` (rewrite the partition table + clear `otadata` so the bootloader boots ota_0) is what a board that has NEVER carried the OTA table needs — a fresh unit, or one recovered to a legacy single-app image. The ordinary targets assume the table is there, which on these two it is. **Phase 3** adds Settings → UPDATE ONLINE: it reads `/sd/update/ota.json` (`{"manifest_url": ...}`), fetches a manifest (`version`/`url`/`sha256`/`size`), and if newer than `moy_ota.FIRMWARE_VERSION` streams the `.bin` to SD then installs it. **Bump `moy_ota.FIRMWARE_VERSION` on every release** (like cart versioning) or the online check won't offer the update. **This is VERIFIED on hardware, on both boards** — flash, reboot-into-new-slot, rollback and the WiFi download all ran on glass on 2026-08-02 (the numbers are in the channel entry below), which also settled the WLAN-vs-LCD-DMA coexistence that #38 had flagged as the open risk. This paragraph carried "still NEEDS ON-HARDWARE VERIFICATION" for two weeks after the entry directly beneath it recorded the whole chain passing; if you are about to re-verify OTA because a doc told you to, read that entry first.
- **Branches and releases (2026-08-02):** **`dev` is where work lands; `master` is what users get.** Commit to `dev` by default — a change is not on master until a human has tested it on the boards it touches. The two branches ARE the two OTA channels and the two rolling releases, and CI keeps them apart: a push to `dev` builds a **beta** (channel `unstable` → the `firmware-beta` release), a push to `master` builds a **stable** (channel `stable` → `firmware-latest`, which is what the site's flasher writes and what the stable OTA offers). Host CI (tests) runs on both; the site (Pages) only ever republishes off master. Firmware builds are path-filtered pushes (`firmware/`, `runtime/`, `system_carts/`) and the workflow's per-ref `cancel-in-progress` collapses a burst of dev pushes into one build of the last commit — so "every push builds" costs one build, not ten. **The merge into master IS the release**, and `make release NAME=0.7` (`tools/release.py`) is how it happens: clean-tree + upstream checks → `make test` → `merge --no-ff dev` → bump `moy_ota.FIRMWARE_VERSION` **and** set `FIRMWARE_NAME` → commit + tag `v<NAME>` → **stop**, printing the push command (pushing master is the moment a device somewhere is offered the build, so it is a separate deliberate keystroke; `PUSH=1` skips the pause, `NOTES="…"` records what changed beside the constant). **`NAME` is the release**: `MAJOR.MINOR` (a third component only for a pure fix release), checked for shape, and it becomes the git tag, the `label` in the OTA manifest and the string on the kid's update screen — `FIRMWARE_VERSION` stays an opaque counter nobody reads, because it is signed as an int and betas stamp a build epoch into it. Re-cutting under a name already tagged is refused, which is the prompt to pick `0.6.1`. Both boards' `build.sh` read `FIRMWARE_NAME` for the stable `OTA_LABEL`. GitHub's default branch stays `master` so the public face of the repo is the tested tree. Don't hand-bump `FIRMWARE_VERSION`, and don't push straight to master for anything a board can run.
- **Two OTA channels (#53):** STABLE (master) and UNSTABLE/BETA (dev) — the branch mapping above, now literal. Settings → CHANNEL toggles which the device checks; `ota.json` carries `{"channels": {"stable": url, "unstable": url}}`, and when the card says nothing `moy_ota.default_manifest_url()` points each channel at the **per-board** `latest-<board>.json` CI publishes on that channel's release (per board because an OTA payload is an app-partition image — Xtensa on the T-Deck, RISC-V on the P4 — so the wrong one is a valid image that cannot boot; the board is therefore INSIDE the signature (scheme `moybyte-ota-v2`) and a manifest naming another board is refused by name before the signature is even checked) — so a board straight off the flasher can update over the internet with **no ota.json and no host of the owner's** (the card still WINS, which is how a LAN/offline host overrides it; `_http_open` follows redirects because a release download 302s to the CDN). The build STAMPS its identity into a gitignored `modules/_ota_build.py` (CHANNEL/VERSION/LABEL) from `MOYBYTE_OTA_CHANNEL` (default `stable`; CI derives it from the ref), so the channel is a **build choice, not a per-branch source edit** (clean across merges) — `moy_ota` imports it and offers an install when the manifest's channel **differs** from the running one (a switch — incl. beta→stable rollback) **or** is a higher version **within** the channel. A beta's version is the build epoch (auto-newer each publish), shown via a human `label`; the CI publisher reads that stamp back out of the build artifact rather than re-deriving it, because a manifest advertising a version the image doesn't carry would offer the same install forever. **Publish the current working tree (uncommitted OK) as a beta the device pulls over WiFi:** `make ota-publish-unstable` (builds with the unstable stamp → `OTA_ROOT/unstable/{firmware.bin,latest.json}`), served by a persistent host (`make ota-serve-install` → systemd `--user` unit `tools/moybyte-ota.service`) — the LAN path, for when you don't want to push. Both boards are past the one USB flash the first two-channel firmware needed, so betas reach them over WiFi. **The GitHub-served channels are VERIFIED on glass** (P4, 2026-08-02, real WiFi): saved-credential autoconnect → TLS to github.com → the **302 to the release CDN** (whose header block is **5167 B** — the reader's 16K cap exists for exactly this; the 4096 this code shipped with would have failed precisely here) → the 846 B manifest → **signature verified on-device in 46 ms** against the baked key (tampered + unsigned both refused) → 3,085,168 B streamed to the internal VFS at **~55 KB/s** (55 s), sha256-checked against the SIGNED manifest → installed in 11 steps (55 s) → booted into the other slot, correctly stamped `beta <epoch> / unstable`. **The T-Deck ran the same chain on 2026-08-02** (owner-driven, baked urls): stable → 404 → “no STABLE build for this console yet”, beta → `hdr=5177B` → verify in **53 ms** (the S3 is barely slower than the P4's 46 ms) → 4,296,752 B at **~137 KB/s** (~31 s) → install ~380 ms per 32 KB block (~50 s) → “MOYBYTE UPDATED”. That download rate **supersedes the ≈72 KB/s “TCP ceiling”** quoted elsewhere for OTA; the install is the slow half now. A leftover `/sd/update/ota.json` silently reroutes every check (the card WINS, and a card url needs no signature), so delete it before testing the real path. Two things that path taught: **`ensure_online()` must WAIT for the link** after the autoconnect (`ONLINE_WAIT_MS`) — `DeviceWifi.connect()` polls 4 s and gives up, and a saved network came up **1.5 s after it did**, so a perfectly good network read as “wifi offline” (the wait belongs there, not in `connect()`, which would then freeze the desktop on every wrong password); and a **cable flash over a pending marker** makes the next boot report `rolled_back` — accurate (the OTA'd image is not what is running) but a developer-only artifact. **CI collected only the T-Deck's `ota_build.json`** (the P4's lives at `dist/p4/`, outside `firmware/`), so every P4 beta shipped a correctly-stamped IMAGE under a manifest whose version fell back to the committed `FIRMWARE_VERSION` — i.e. a P4 already on beta compares its epoch against a manifest claiming `2` and is **never offered a newer beta at all**. Fixed + pinned by a test; the step now warns if any board's stamp goes missing.
- **OTA works on the P4 too (#53/#58, on-glass 2026-08-02):** the partition table was OTA-shaped from bring-up and `update_ui` was always frozen in; what was missing was `moy_ota` itself (now staged from the T-Deck tree — it is board-agnostic, `update_dir` is a constructor arg), the **staging directory** (`/moy/update` on the internal VFS — this console has no SD, and `with_sd` is a plain call-through here), the `_ota_build` identity stamp (now including `BOARD`), `CONFIG_BOOTLOADER_APP_ROLLBACK_ENABLE` (was never set — add it to build.sh's sdkconfig regeneration guard or an existing config silently keeps it off), the app-partition image (`dist/p4/moybyte_p4_app.bin` — `moybyte_p4.bin` is bootloader+table+app merged for a cable flash at 0x2000, and handing THAT to `esp32.Partition` would write a bootloader into an app slot), and the `mark_valid()` call at a healthy boot. **Verified end to end on hardware** by installing the board's own running image into the inactive slot (3,085,216 B in 11 steps, ~45s; reboot came up on `ota_1` and marked valid). Two things that misfire and are worth knowing: `step()` returns **True while more remains** (`runtime/update_ui.py` drives it as `more = u.step()`) — inverting it writes a truncated image, whose `set_boot` is then correctly refused with `ESP_ERR_OTA_VALIDATE_FAILED` and leaves the board on its old slot; and a **cable flash must erase otadata first** (`make firmware-flash-p4` now does, `P4_OTADATA_OFFSET`) or a board that has taken an OTA writes ota_0 and boots the stale ota_1, looking exactly like a flash that did nothing.
- **"Did the update work?" is now two mechanisms, both on-glass (#53, 2026-08-02):** the rollback confirm fires from the **frame loop**, not the boot path — `moy_ota.confirm_when_healthy(ws._frames_drawn)`, called every iteration, needs `HEALTHY_PAINTS` (=1) frames to have reached the glass AND `HEALTHY_LOOPS` (=120) iterations to have survived. Confirming where the desktop is merely CONSTRUCTED certifies an image that has never drawn a pixel, which is exactly #56. **The paint threshold cannot be raised**: the console repaints only when something changes, so a quiet desktop sits at ONE painted frame indefinitely (measured — a 120-*paint* gate would roll back every update nobody was touching); the loop counter is what carries the wait. And `finish()` writes `<update_dir>/pending.json` naming the slot it pointed the bootloader at, which `boot_check()` compares against the running slot on the next boot → `("ok"|"rolled_back", text)`. The marker is cleared at the **confirm**, not at the read, so an image that boots, reports, and then dies still carries its evidence into the boot after the rollback. The verdict surfaces as a **system notice banner** (`ws.notice()` / `announce_update()`, a new overlay layer that must be registered in BOTH `wm.py` and `wm_windowed.py` — the windowed WM builds its own overlay list) and again on Settings → UPDATE. Rollback self-heal is **verified on the P4** by resetting inside the confirm window (the state a firmware that dies in its first seconds leaves behind — no deliberately broken binary needed): the bootloader reverted, the board came back on the old slot and said so. While the banner is up the desktop repaints every frame, so the loop runs ~12/s instead of ~40/s and the confirm takes ~10s, not ~3.
- **OTA manifests are SIGNED (2026-08-02):** the device's `ssl.wrap_socket` does no certificate verification, so TLS alone left any board on a hostile WiFi installable-by-anyone — and the manifest's own `sha256` is no defence against whoever wrote the manifest. So CI signs the manifest (`tools/ota_sign.py`, RSA-2048/SHA-256 PKCS#1 v1.5 over `channel|version|size|sha256`) and `moy_ota.verify_manifest` checks it against `OTA_PUBLIC_KEYS` baked into the running image; the image itself rides the signed `sha256`, which the download already verifies. **RSA and not Ed25519 purely for the verifier**: `pow(sig, 65537, n)` is ~17 modular squarings that MicroPython does in C with no native module of ours, where pure-Python curve arithmetic would be seconds (3-arg `pow` needs `MICROPY_PY_BUILTINS_POW3`, which the esp32 port's `ROM_LEVEL_EXTRA_FEATURES` enables). **Measured on the P4** (2026-08-02, the shipped verifier ast-extracted and run on real MicroPython over the serial harness — `tests/test_p4_on_glass.py`): **35ms modexp, 41ms a whole `verify_manifest`**, with tamper/junk/unsigned all refused and `int(hex,16)`@512 chars, `to_bytes(256,'big')` and `hashlib.sha256` all confirmed present. Budget ~100ms on the slower T-Deck; it is once per check, behind a CHECKING screen already waiting on the network. (The original "single-digit ms" estimate was wrong by ~5× — mpz reduces by division.) The **url and label are deliberately unsigned** so a classroom can mirror the official manifest to a LAN host and rewrite the url — the bytes stay pinned by the signed hash. **Policy:** a manifest from a BAKED channel url must be signed; one reached because the owner put an `ota.json` on the card need not be (writing to the SD card is a physical act of consent, and it keeps the key-free LAN dev loop working) — but a signature that IS present is always checked, so a tampered official manifest can't be laundered through a local host. A build with no baked key can't require one (it would just brick updates). The private key is a repo secret (`MOYBYTE_OTA_SIGNING_KEY`, `make ota-keygen` generates it and prints the `gh secret set` line + the constant to paste); `OTA_PUBLIC_KEYS` is a TUPLE so a key can be rotated by publishing an image trusted by the old key and signed by the new. Signing needs the `release` extra; **verifying needs nothing**, which is what lets the security-critical half be tested in ordinary CI (`tests/test_ota_signing.py` carries a throwaway key and signs with `pow(m, d, n)`). Residual, accepted: a compromised GitHub account can sign a real update, and boards running an image with no baked key stay unprotected until one update later.
