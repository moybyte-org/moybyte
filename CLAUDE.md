# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## START HERE — what are you about to touch?

This file is a MAP. Find your row, read the authority it names, then come back
for the rules. Reading it top to bottom is the slow path.

| you are about to… | read FIRST | the thing that bites |
|---|---|---|
| change a draw verb / the raster | `docs/surface_model_v1.md` §4, then `device/device_canvas.py` | ONE canvas class runs on every tier. `tools/p4_conformance.py` is the only check that reaches the real C on real glass. |
| add or port a board | `docs/board_ports_2026-08.md` — its stage-6 "TAKE THESE" list | four shared bodies are taken, not copied; a lever a board lacks is expressed by ABSENCE, never by 0 |
| touch a panel flush | `native/moy_flush/moy_flush.c`'s header | "every clause was a race once". `tests/moy_flush_harness/` compiles it with no board attached |
| touch SD or the panel bus | that board dir's README | the two drivers share one SPI host; a per-op teardown hangs the board with no panic |
| change the shell / a WM / an app | `runtime/README.md` (per-file map), `docs/app_api_v1.md` | pixel goldens are the net, and the 320×240/1× row does NOT exercise the toolkit |
| add a cart verb | `docs/moy_cart_api.md`, and SPEC.md in the moy-spec repo | the verb table is a PUBLIC spec; Python and Lua must agree verbatim |
| touch audio | `native/moy_audio/libmoy/UPSTREAM.md` | it is VENDORED — fix it upstream in moy-spec and re-vendor, never here |
| touch multiplayer | `docs/netplay_v1.md` | the payload is INPUTS, never state; a missing input STALLS, it never extrapolates |
| touch the browser build | `firmware/web_runner/`, `docs/moycore_direction.md` | two web modes, no crossover; where a page is SERVED from decides where its carts live |
| chase a performance number | **#66** (per-cart fps), **#58** (P4), `docs/perf_native_gap_v1.md` (#77) | numbers live in issues, never in this file — see the rule below |
| drive a board over serial | `tools/p4_autotest.py`, the three `tests/test_*_on_glass.py` | the boards' line-state rules are OPPOSITE; `[serial]` in board.toml is the authority |
| edit any document | — | run `tools/check_docs.py`; it resolves every path AND pins duplication downward |

**Four rules that outrank anything below.** Host and device are ONE codebase, not
a port — a drawing change lands in the one canvas class or the "one cart, every
tier" contract breaks. A number that MEASURES the system belongs in its issue,
not here. A board that lacks a lever reports `None`, never `0`. And a mechanism
promoted into one body needs an executable guard, or it rots silently — that is
the failure most of the entries below were written to prevent.

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

**Same rule for numbers, and this file is a MAP to them, never a copy of them.**
A measurement pasted here goes stale exactly as this paragraph warns — one did,
inside the sentence forbidding it, and on 2026-08-28 a swept pass found the
T-Deck's image headroom recorded as `186KB` when the build reported eight times
that. State the DECISION and point at the number's home:

| what | where it lives |
|---|---|
| per-cart fps, frame budgets, the lever ledger | **#66** |
| P4 port state, its transitions and image cost | **#58** |
| cross-board strategy, the A/B benches, the ranked levers | `docs/perf_native_gap_v1.md` (**#77**) |
| Guition port + its A/Bs | **#202** · Lua/moycore tier **#67** · crisp composite **#204** |
| a board's image headroom | the build prints it; nothing else is current |
| how a subsystem works | that module's or board dir's README/header |

The numbers that MAY stay are the ones that are configuration rather than
status — a chunk size, a timeout, a headroom floor, a gesture's hold time —
because those are the design, not a measurement of it.

## What this repo is

Moybyte is an operating system for ESP32 boards: a console where the software is
cartridges, running as firmware on three boards plus a host simulator and a browser build.
Everything is ONE system: **`.moy` is the only cart format.** (A separate
`.moyproj` SDK was deleted 2026-07-31 because nothing depended on it but its own
tests; the block compiler was always separate and lives in `runtime/blocks.py`.
Git history has the rest — do not reintroduce the format.)

- `runtime/` — the **host reference** of the console (launcher → Player → tabbed Editor). Pure host, fast dev loop. See `runtime/README.md` for the per-file map; don't duplicate it.
- `firmware/lilygo_t_deck_plus_mainline/` — the **device port** of that same console (MicroPython).
- `firmware/esp32_p4_wifi6_touch_lcd_7b/` — the **second device target** (#58): the 7″ 1024×600 MIPI-DSI "desktop workstation" board. Panel/touch/SD/WiFi hardware-confirmed; the **console runs on glass** (the two-worlds desktop under `WindowedWM` — boots to the desk, #105; carts on internal flash; the two-worlds split is on-glass verified — `tests/test_p4_on_glass.py` boots to the desk, opens app windows and scrolls/flings them against the real pointer feed) — colors/flicker/touch/popup/wallpaper all fixed on-glass, the game composite runs on the hardware **PPA** (`moy_ppa`) with an **async-overlap** frame path, and #159's L2 cache 128→256KB closed the game chapter; app-window drags ride a triple framebuffer + a retained backdrop cache. **Where this board stands is #58; per-cart fps is #66** — do not restate either here.
- `firmware/guition_jc3248w535/` — the **third board** (#202): the ~$15 Guition
  3.5″ S3 smart display, touch-only, LANDSCAPE 480×320. The port kit's first walk
  of the six-stage checklist — provisioned and on glass in one session
  (2026-08-18). **#202 CLOSED 2026-08-20**; that dir's README holds its hardware
  rules.
  - **New panel class: QSPI AXS15231B via `native/moy_axs`** (raw `spi_master` —
    the whole frame ships under ONE CS assertion, which is why its TRANSPORT
    cannot share `moy_lcd`'s C body). Its band feed runs on a **core-0 FreeRTOS
    feeder task**: the first board whose flush never costs the VM core.
  - **The concurrency half IS shared** — `native/moy_flush`, one body linked by
    both panel modules, each keeping only its transport as three hooks. Its header
    is the authority on all of it. Per-board and meant to stay: each transport,
    each panel's constants, this board's rotate/fold band synthesis, the T-Deck's
    `sd_guard`.
  - **The Python compositors are one body too** (`device/banded_panel.py`'s
    `BandedCompositor`); `TDeckCompositor`/`GuitionCompositor` are thin subclasses
    adding only what is theirs. **The T-Deck must keep NO `fold_supported`
    attribute at all** — absence is how a board says it lacks a lever.
  - **The panel is portrait-native and its MADCTL MV is dead on this glass**, so
    `moy_axs` rotates in its band copy (sequential PSRAM reads + scattered writes
    into the uncached SRAM bounce, ~the same cost as the memcpy it replaced).
  - **First fullscreen-tier board where system canvas ≠ game canvas** (320×240
    game on a 480×320 system surface), carried entirely by the existing
    `SystemCanvas` + `wm.composite_game` with zero new shared code.
    **font_scale 2 was built and REVERTED same-day on owner verdict** — 1× text
    reads fine; the real problem is tap-target size, and the fix is a PPI floor on
    chrome geometry (#203), not magnification.
  - **Three hardware rules from its README**: the panel DISCARDS writes until
    CASET/RASET are armed; MV scrambles; and the touch controller STREAMS while
    touched but returns a constant-byte IDLE FILLER once lifted — **the filler IS
    the lift signal** (a day of "wedge" theories built on idle-time probes is
    recorded in #202 as a cautionary tale).
  - **A TF card, when present, IS the cart store** (`/sd/carts`, seeded on first
    boot; no card = internal VFS). The port's `machine.SDCard` SPI slot numbers map
    INVERTED to host numbers — slot=3 is the panel's bus, which fails
    `ESP_ERR_INVALID_STATE` and leaks the sdspi singleton until reboot.
  - Declined: a touch-only game-exit gesture (a paired BLE keyboard's
    hold-BACKSPACE is the exit path). Untracked: audio.
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

The `.moy` canvas works in **palette indices** (the `MOY64` palette) with a plain-function drawing API (`cls/pset/line/rect/rectfill/circ/circfill/spr/print`) — no dependency on `framebuf`, LVGL, or even Python. This is deliberate: the *same* `.moy` runs on the host, on all three boards and in the browser. **There is now ONE canvas class on every tier** — `device_canvas.DeviceCanvas`, RGB565 with the palette resolved at draw time; the host builds it on CPython through `runtime/host_canvas.py` (the host's own indexed raster, runtime/canvas.py, was deleted 2026-08-15 — git history has it). So a drawing feature is added ONCE, in that class + the `moy_gfx`/libmoy kernel under it. The SYSTEM-surface contract (#39: font_scale text, font-scale layers, `blit_cover`) is one body too since 2026-08-18 — `device_canvas.SystemCanvas`, which the host/web/P4 classes subclass for only their per-tier pieces; two of the three hand-copies it replaced had silently drifted (the P4's print stride, its lost cart-palette layer rider).

### Graphics is conformance-checked, and the indexed canvas was MEASURED AND DECLINED

Graphics has not followed audio into vendoring, and the reason is measured
rather than assumed: **the indexed canvas was A/B'd on P4 glass (2026-08-05) and
565-at-draw won**, losing no colour — every scene hashed identical on silicon.
That question is CLOSED on performance. Why, by how much, and the two readings
not to misquote: **`docs/perf_native_gap_v1.md` §8** (#77). Do not re-derive it
here.

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
`tools/p4_cart_bench.py` (the Bench cart's per-verb µs — since 2026-08-17 the
Lua twin reports over serial too: both carts write a fixed PMEM layout the tool
reads live via `moycore.pmem_image`, so `--json`/`--diff` speak one format for
both twins; `tests/test_bench_pmem_report.py` locks the three layout copies
together), `tools/p4_bench.py` (the console's own UI-panel bench),
`tools/p4_conformance.py --serve` (holds the board — opening the port
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
survives any large overhang. Every suite that drives the real native modules
resolves the binary through ONE shared lookup, `tests/unix_mp.py`
(`require_unix_mp`, probing the binary for the modules the suite needs;
`MOYBYTE_MICROPYTHON` overrides) — `test_gate_pal_sync`, `test_semantic_traces`,
`test_moycore_loop`, `test_audio_parity`'s native case and `test_gfx_binding`
all ride it, none carries its own path, and absence WARNS locally / FAILS under
`CI`/`MOYBYTE_REQUIRE_UNIX_MP` instead of silently skipping. (This line used to
say five suites "still point at their own hand-built binaries" for days after
the unification landed — the 2026-08-17 sweep verified all five run, 19 passed
0 skipped.) Do NOT symlink into
`firmware/lilygo_t_deck_plus_mainline/.build/micropython/`, which `build.sh` git-clones into.

### Audio is VENDORED from moy-spec, not implemented here (#97)

The one subsystem where that rule is inverted. SPEC.md §8.3 pins synthesis to
PICO-8's measured output (as reverse-engineered by zepto8/fake-08) — the
unequal instrument loudness, the pitched noise walk, the Hz-linear slide, the
109/110 phaser detune — and moy-spec ships its own C implementation of it,
**libmoy**. That source is vendored verbatim into
`native/moy_audio/libmoy/` and
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
make firmware-build-tdeck-mainline                                 # -> dist/tdeck_mainline/
make firmware-flash-tdeck-mainline PORT=/dev/ttyACM0               # merged image at 0x0
#   ... then a SEPARATE esptool --before default_reset --after hard_reset to START it:
#   write_flash's own trailing reset does not, and the board otherwise sits in the loader
make firmware-monitor-tdeck-mainline PORT=/dev/ttyACM0             # miniterm @115200
#   (the fork-era firmware-*-lilygo-micropython names survive as aliases)
```

- The build (`firmware/lilygo_t_deck_plus_mainline/build.sh`) **clones mainline MicroPython v1.28 and
  esp-idf v5.5.1 into `.build/`** (no LVGL, no fork — see below), applies the port's patches under
  marker guards, stages the shared native C modules plus its own `native/moy_lcd`
  through `USER_C_MODULES`, and freezes the Python. **Which shared modules cross is DATA, not a list
  in the script** (#161 Phase 3, completed 2026-08-17): each board carries a `board.toml` with a
  **denylist** over `runtime/*.py` (every `[[deny]]` names the file, a kind and a prose `why`), an
  **allowlist** over `device/*.py`, and a **`[native.shared]` denylist** over the C modules in
  `native/` (the P4 denies `moy_sd`/`moy_audio` with reasons; the T-Deck denies nothing —
  `tools/board_config.py stage-native` stages the copies and generates the `.staged/micropython.cmake`
  include list, so neither build.sh nor the tracked cmake names a shared module). The **web runner
  carries a `board.toml` too** (the last hand-rolled DENY list, converted the same day), and
  `tests/test_staging_closure.py` derives every target's frozen set from the declarations. A new
  shared module reaches every board by default and staying off one is a written decision; the old
  per-board allowlists are what let the T-Deck silently miss the web console. The `device/` allowlist
  stays an allowlist because `runtime/` is a shared tree whose default answer is "yes" and the device
  tier's is not. The stager also **prunes untracked strays** — `modules/` is gitignored and never
  cleaned while the freeze takes the whole DIRECTORY, so deleted modules kept shipping in images
  built on a warm tree. **The shared half of every board build is `tools/esp32_build_lib.sh`**
  (sourced by all three build.sh scripts; landed 2026-08-17 with two, and the
  Guition was provisioned on it the next day): toolchain setup with the export.sh probe +
  install self-heal, IDF_COMPONENTS append, the native-code-free patch, native staging + the web
  blob (generated into the STAGED copy — a build must never write into the shared `native/` tree),
  the OTA identity stamp (every board reads `device/moy_ota.py`, one path), the frozen manifest +
  md5 fingerprint, the stale-sdkconfig guard and the #168 size guard; what stays per-board is the
  patch ladder and the sdkconfig facts. **A board's sdkconfig facts are DATA the build READS**
  (2026-08-21): `boards/<BOARD>/sdkconfig.board` is the one store, prose and all, and no script
  restates any of it. Each `build.sh` used to hand the guard a hand-typed subset of its own
  fragment, where anything left out silently did nothing on a warm build tree — `082fb9e` hit
  exactly that, twice in one commit. `moybyte_sdkconfig_guard <board_def_dir> <generated_sdkconfig>`
  replaces it: a fingerprint over the fragment + `mpconfigboard.cmake` + `MPY_TAG` decides whether
  the tree is stale (so a DELETED line counts too, and so does a change to which upstream fragments
  the board pulls — `CONFIG_SPIRAM_MODE_OCT` is MicroPython's value, not ours), and only once that
  matches does it check the generated config, where a missing option can now mean one thing:
  Kconfig REFUSED it. That case prints the fragment's own comment block and fails under
  `CI`/`MOYBYTE_REQUIRE_SDKCONFIG`. It caught a live one immediately —
  `CONFIG_BT_CTRL_BLE_ADV_REPORT_FLOW_CTRL_NUM=20` is below IDF's `range 50 1000`, so both S3
  boards silently kept 100 and the saving that commit describes never happened (now 50). A
  disable -- `CONFIG_X=` or the idiomatic `CONFIG_X=n` -- is fingerprinted but never grepped: a
  disabled bool renders "is not set" and a hidden choice member is absent from a generated config
  entirely, so a grep for either false-alarms (the `=n` spelling joined 2026-08-25 after
  `CONFIG_BT_HCI_LOG_DEBUG_EN=n` failed every CI p4 build while local builds only warned). The partition CSV is likewise named once,
  in `CONFIG_PARTITION_TABLE_CUSTOM_FILENAME`, and exported as `BOARD_PARTITION_CSV`. Full codegen
  of the fragment stays DECLINED — `docs/board_ports_2026-08.md` carries that entry and the
  reasoning. **The cable-flash facts are board.toml data too** (`[flash]`/
  `[monitor]`, read by `tools/board_flash.py`, #202 Phase A): image path, offset, baud, the otadata
  region (T-Deck 0x1d000, P4 0xd000 — erased FIRST so a board that has OTA'd boots the slot the
  flash just wrote) and the reset strategy (the T-Deck declares `usb_reset` — measured, its
  USB-Serial/JTAG write-times-out under `default_reset` when wedged); the Makefile flash/monitor
  targets are two lines and the CI matrix is one include-row per board. **The serial-console facts
  are board.toml data as well** (`[serial]`, read by `tools/push_cart.py`): the line state at open
  (asserted on the two SoC-USB S3 boards, low on the P4's CH343), whether the board may be reset at
  all (`attach_only`), and the upload chunk (256 on the P4's UART — its stdin ring has no flow
  control and 768 corrupts silently, measured 2026-08-19), which is what lets **ONE cart-push tool
  serve every board**: `python tools/push_cart.py <cart.moy> --board tdeck|p4|guition_s3` (the names
  are the board files' own `[board] ota` ids, required on purpose — a default would be a silent
  wrong transport) copies a cart folder onto the live console's store, whose path is DISCOVERED from
  `ws.carts_root` rather than declared (the Guition's is a TF card when one is in the slot and the
  internal VFS when not). **The frame loop is shared
  too** (#202 Phase B, `device_boot.FrameLoop`): the invariant order — inputs → dev channel → idle
  tick → pointer → present → frame → backlight gate → pump.tail → tail → pace — lives ONCE, pinned
  by order tests in `tests/test_device_boot.py`; each board's `run_desktop` supplies
  `poll_inputs`/`present`/`tail`/`account` hooks and its hardware. The GT911's no-news contract
  (hold / stale-mark / bound) is one copy in `device/gt911.py` (#202 Phase C). Images land in
  `dist/tdeck_mainline/` (gitignored), and an oversized one is a BUILD FAILURE on every board.

- **THE lvgl_micropython FORK IS DELETED (2026-08-17).** The T-Deck ships the mainline port, which
  measured FASTER on the Bench referee — the gap is in the console FLOOR, not the raster: per-op verb
  costs are identical and it shows on `idle` as much as `draw` (numbers in #66) — and
  is the only build whose serial dev channel works. What used to live in that board directory and was
  never board-specific now sits at the repo root, so no board reads a sibling board's tree:
  **`native/`** (the shared C modules), **`patches/`** (the IDF/MicroPython patches), **`device/`**
  (the device tier every board stages — `device_canvas`, `device_api`, `device_diag`, `moy_ota`,
  `moy_webhost`, `moybyte_sd`, `moycore_glue`, the `moybyte` package …). LVGL is gone with it; the
  panel comes up through `native/moy_lcd` + `modules/tdeck_panel.py`, and that is now the ONLY panel
  driver in the tree. `patches/` was pruned to its three consumers on 2026-08-17: five orphans were
  DELETED (git history has them) — `esp32_i2c_new_driver` (reachable only through the fork's knob,
  the #69 decision), `esp32_repr_c_floats` + `esp32_i2c_gil_release` (both live on as build.sh's
  guarded sed/heredoc, steps 3b/3c — the patch files were unapplied second copies),
  `esp32_tdeck_early_board_init` and `spi_master_psram_tx_dma` (fork-only mechanisms; the mainline
  flush never DMAs from PSRAM).
- The MicroPython console is the only firmware. (The older Arduino/PlatformIO serial-smoke firmware and the legacy LVGL `.moyproj` game-loop boot path were removed; git history has them.)

### Second device target: ESP32-P4 (Waveshare 7B) — bring-up (#58)

- `firmware/esp32_p4_wifi6_touch_lcd_7b/` — the desktop-tier board (7″ 1024×600
  MIPI-DSI, GT911 touch, C6 WiFi over SDIO, 32MB PSRAM/flash), mainline
  MicroPython v1.28 with an out-of-tree board def (`boards/MOYBYTE_P4`).
  **Read that dir's README before touching this board** — it holds the
  hardware-learned constraints. Build/flash/monitor: `make firmware-build-p4` /
  `make firmware-flash-p4 PORT=…` / `make firmware-monitor-p4 PORT=…`; serial is
  a CH343 and the REPL stays alive.
  - **`native/moy_dsi` scans, it does not push.** DPI mode: the DSI peripheral
    scans a PSRAM framebuffer continuously, so there is **no per-frame flush** and
    the T-Deck's tx_color ceiling does not exist here. This board denies
    `moy_flush` in board.toml for that reason.
  - **The hardware rules that cost sessions** (the README is the authority): SD
    power comes from the internal LDO4 that stock MicroPython never enables;
    SDMMC slot 1 belongs to the C6 and claiming it panics the board; PSRAM must
    run at 200MHz or the DSI scan-out underruns; WiFi needs no C6 flash; and a
    root-level VFS dir named like a frozen module SHADOWS it — which is why the
    store root is `/moy/carts`, never `/moybyte/...`.
  - **The game composite runs on the hardware PPA with an async overlap**: a quiet
    frame composites via `moy_ppa.blit_async` and DEFERS the scan-out switch to the
    next loop's `present_pending()`, so the DMA overlaps the loop tail and the
    input poll. Full paints stay blocking so chrome never races the DMA. **An async
    PPA op must be the frame's LAST write**, and `moy_ppa` must C2M-writeback dst
    before submit, because the IDF PPA driver invalidates the whole out buffer at
    submit and would discard unflushed CPU writes.
  - **The PPA only helps UPSCALE composites.** A full-screen 1:1 copy (the backdrop
    restore) is ~identical CPU vs PPA, PSRAM-bandwidth-bound against the scan-out;
    and **sprite BATCHING is a dead end** (~10× worse than `spr_batch` — per-op
    submit dwarfs a tiny blit). Both stay on the CPU.
  - **The PPA scaler is fixed BILINEAR in silicon**, so pixel art composites
    smeared. Settings → CRISP PIXELS (default OFF, capability-gated, serial
    `crisp 0|1`) reroutes through `moy_ppa.blit_crisp` — a banded internal-SRAM
    bounce, byte-exact against the CPU kernel. Ledger: #204.
  - App-window drags use the **dirty-union restore** with a body-subtract trail and
    a deferred content stamp; resize is live-body. The **triple framebuffer**
    shipped; the **double game canvas was built, measured and REVERTED** (`26e1f9f`
    — the game fence was already ~free and the retention memcpy cost more than it
    saved), and #159's L2 cache 128→256KB closed the game chapter (512KB does not
    boot — internal/DMA pool 0x101).
  - Status and numbers: **#58**. Open: USB-HID keyboard, audio (ES8311).

### Third target: the web runner + the moy-spec repo (#151/#170)

- `firmware/web_runner/` — the **MicroPython-WASM** build of the same console.
  **The browser is no longer the GPU (moycore stage 4, 2026-08-12): the wasm
  RASTERIZES** — the narrative is `docs/history/moycore_plan_2026-08.md` §6; what a coder
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
  frame is now a small fraction of the budget and the PRESENT (heap copy + RGBA
  expansion) is the expensive half, so optimize there first. Numbers, including
  what real headless Chrome holds, are in the plan and #66.
  **This bundle RIDES BOTH BOARD IMAGES (2026-08-15).** A board serves the wasm
  console over its own WiFi (`moy_webhost`), and it used to serve only a copy a
  human had put on its storage — which drifts with nothing to detect it (a board
  served a bundle old enough to carry a desktop-blackout bug fixed hours earlier),
  and the T-Deck cannot be pushed to at all because the push hands the board a url
  over serial and, when that was written, that board's RX was dead under the desktop. So
  `tools/gen_web_blob.py` `.incbin`s the four PRE-GZIPPED assets (572,693 B; raw
  is 1,155,953 B and does not fit the S3's slot) into the `moy_web` usermod, which
  hands them out as READ-ONLY memoryviews at flash — never a `bytes` per request,
  on a board with very little internal SRAM free in play. **Storage still WINS**, so
  `make p4-web-push` stays the sub-minute dev loop and the image is the guarantee,
  not the ceiling; `start()` prints which of the two it is serving, because a
  stale pushed copy shadowing a good baked one is the same bug one level down. The
  cost is a fixed slice of every app image and of every OTA payload — so an
  oversized image is now a BUILD FAILURE on both boards (each build prints its own
  slot headroom, which is the only number worth trusting; a figure pasted here was
  stale by 8x within two weeks) (it was a warning that still emitted artifacts esptool would
  refuse), and the next growth spurt is a partition-table decision, not a
  surprise. Building the bundle needs emsdk, so a missing one only WARNS locally
  and FAILS under `CI`/`MOYBYTE_REQUIRE_WEB_BUNDLE` — the firmware workflow builds
  the web runner before each board, so a published image always carries one.
  **TWO WEB MODES, TOTAL, NO CROSSOVER (2026-08-25, #193/#197).** Where a page is
  SERVED from decides where its carts live. A board-served page edits the BOARD's
  store (the pull + `POST /sync` half); a page on a static host — moybyte.com, an
  export, `file://` — keeps them in the BROWSER. The mode is decided ONCE at boot,
  BEFORE the VFS is seeded, because it decides what the VFS is seeded FROM
  (`moy_store.probeMode`): `GET /sync` is the marker a board serves, and a GET miss
  falls through to an EMPTY-batch POST, because a board running firmware older than
  that marker still ACCEPTS the batch and reading it as "static host" would quietly
  strand a kid's edits in a browser. Mode 1 is not a second persistence design — it
  is a second DELIVERY TARGET for the batch `moy_sync`'s `StoreWatcher` already
  builds, so writes, deletes and cart-deletes arrive in the one op vocabulary and
  the two sides cannot disagree about what a batch means. **The substrate is OPFS,
  not IndexedDB** (the moycore plan §9 open question, closed here): the ops ARE file
  writes at paths, so OPFS applies them 1:1 and a cart folder stays a cart folder —
  the shape `moy_carts` already speaks on every tier — where IndexedDB would mean
  inventing a path keyspace and a blob schema to keep a filesystem inside a
  database, then keeping that schema honest as the store grows file kinds. Measured
  in Chrome and recorded in the moycore plan: seeding, reload and commit all land
  nowhere near the budget, which is what makes the file-shaped substrate free to
  prefer. **The
  journal comes along in mode 1 (2026-08-25)** — see the doctrine below; the
  mechanism is one argument, `web_boot` handing its site-mode carts watcher
  `moy_sync.skip_keep_journal` instead of the wire's `_skip`, so the #111 history
  rides the ordinary sweep into the ordinary store. This line used to record the
  absence as a gap against #193's "with its undo history"; the gap is closed, and
  the JS half (`moy_store.skipLocal` vs `skipName`) is pinned against the Python
  one by `tests/test_web_store.py`. **No OPFS means the
  page says so**: a private window, blocked site data or a `file://` origin runs in
  memory exactly as before, with the row reading "carts will NOT survive a reload";
  a quota failure mid-apply requeues, and after three gives up ONCE and says that
  too. `.moy` export/import is the no-account escape hatch (a dependency-free
  STORED zip out, stored-or-deflated in) and is offered only in mode 1 — on a board
  the console owns the store. worker.js STATICALLY imports `moy_store.mjs`, so it is
  in `moy_webhost.ASSETS` too: a board that does not serve it serves a console that
  cannot boot. Proof is `tests/test_web_persist_e2e.py` (env-gated `MOYBYTE_WEB_E2E`),
  which needs **two Chrome runs sharing one profile AND one port** — OPFS is scoped
  to the origin and lives in the profile, hence browsershot's `MOY_PROFILE`.
  **The 3.4 sync RPC SHIPPED (2026-08-25): a page served from a board writes
  BACK.** `runtime/moy_sync.py` is the one body — the browser's `StoreWatcher`
  (a ~1/s stat-sweep of the wasm VFS; no store hooks, because the store writes
  through several funnels and watching the filesystem catches every writer by
  construction), the wire shape (`POST /sync`, commit-shaped batches under the
  transport's 64KB request cap; big files chunk through a `.tmp` and publish
  atomically), and `apply_ops`, which the board (`moy_webhost`), the dev twin
  (`serve.py --carts DIR`) and the CI convergence harness
  (`tests/test_sync_convergence.py` — the pin the moycore plan §3.4 demands:
  two real stores, drop-and-reattach, a two-sided collision, both journals
  still replayable) all share. Per-file last-writer-wins, and
  a batch that changes the SHELF fires `ws.rescan_carts()` so the launcher
  follows with no reboot (on-glass: `test_guition_on_glass.py`'s sync test).
  **THE JOURNAL LIVES WITH THE STORE OF RECORD (owner call 2026-08-25),** which
  replaces "both sides keep their own journal" — true, and useless: the
  browser's VFS is a scratch copy that dies with the tab, so the kid's undo
  history lived in the one place it could not survive, and a board that took a
  browser's edits kept no record of them at all. There is ONE durable journal
  per cart and it sits where the cart durably lives. Board-served page: the
  RECEIVER journals (`apply_ops(..., journal=True)` appends a #111 commit for
  every carts-root file a batch publishes, in the shape `Project.commit_*`
  writes, so on-glass UNDO walks back through work done in a browser; the
  browser's VFS journal is still written, still never crosses, and is now
  correctly READ as session-local undo). Static host: the browser's OPFS is of
  record, so the journal persists there. **The wire predicate itself never
  moves** — `_skip` refuses journal paths, a board-mode batch is byte-identical
  to what it always was, and the site relaxation is a watcher argument rather
  than a change of what the wire means. The cost is stated where it is paid
  (`journal_append` writes a full snapshot + a log line + an atomic cursor
  rewrite per file — fine at cart sizes, which is what the console's own
  commits have always paid), and the #108 files root is deliberately NOT part
  of it: its undo is `files/.history/` op sidecars, a different mechanism, and
  a pushed drawing lands as a file and nothing else.
  **THE PIN GATES EVERYTHING (owner call 2026-08-25),** reversing the standing
  "the read half stays open". A pinned host serves a stranger exactly the boot
  assets (`index.html`/`worker.js`/`moy_store.mjs`/`micropython.mjs`/`.wasm` —
  open BY NECESSITY, because the page is what shows the pin prompt) plus
  `GET /sync` (the capability marker, which reveals that a board lives here and
  nothing else, and the page has to make its mode decision before it has a pin
  to offer). `GET /carts.json`, `GET /files.json`, `POST /sync`, `POST /run`
  and the Zero's `/gpio` all take the pin. The old split handed any device on
  the WiFi the whole cart store for the asking, on the reasoning that a read
  changes nothing — what it reads is a child's work off their console. A GET
  carries its pin the only place a GET can, so **`moy_webserver.parse_request`
  stopped stripping query strings** (it was spending the credential before any
  handler saw it) and hands `handle_http` the request TARGET; handlers route on
  the bare path and read `query_param(target, "pin")`. A refusal is
  `403 {"error":"pin"}`, distinguishable from the transport's plain-text 404
  because the page branches on it: worker.js stops the boot and the page
  prompts IN-PAGE (never `window.prompt` — a native dialog cannot be styled,
  screenshotted or driven by the browser harness), remembering the pin in
  localStorage per ORIGIN so a kid types four digits once per browser and a QR
  arrival never sees it at all. Dev twins stay pinless and are meant to
  (`serve.py --pin NNNN` exists only so the gate and the prompt can be driven
  in real Chrome with no board on the desk — `tests/test_web_sync_e2e.py`).
  **The #108 user files ride the same protocol as a SECOND root (2026-08-25,
  owner call "they should get synced")** — `GET /files.json` pulls them and a
  batch stamped `{"v": 2, "root": "files"}` pushes them back, the bump being
  what makes a board flashed before this REFUSE the batch instead of writing
  `drawings/…` into its carts store; a files path must start with a
  `moy_carts.FILE_KINDS` kind, which is the one rule that keeps `.history/`
  (each side's own undo) and `trash/` (a local recovery bin — syncing a
  still-undoable delete is how LWW becomes data loss) home in both directions,
  and the browser builds its files watcher only when the pull answered, so an
  older board's 404 cannot disable the carts push with it.
  **WASM MODE IS A SWITCH, NOT A SESSION (owner call, #197, 2026-08-25), and
  the boards carry a PIN now.** No heartbeat, no presence detection, no
  timeout, no session object: while Settings → WEB CONSOLE is ON the glass
  PARKS on a connection screen (`runtime/web_console_ui.py`, back-stack kind
  `webconsole`) and turning it off returns the console — which is how the
  two-writer collision is designed out rather than detected. The screen is a QR
  of the paired url plus tap-to-reveal text plus TURN OFF; the encoder is ours
  (`runtime/moy_qr.py` — byte mode, EC L, versions 1–4, one fixed mask whose
  format bits say so; there is no library on a board and the pin is not a
  constant anything could be baked with). The pin is minted ONCE, lazily, into
  `system.json` (`ws.web_pin()`), and `make_webhost` reads it at **`start()`,
  never at construction** — boards build the webhost before system.json is
  loaded, so a pin captured then is one minted against an empty store. **PLAY ON
  DEVICE** is `POST /run` (`{"cart": …, "pin": …}`, pin-gated like `/sync`,
  launched inside the storage gate because it runs at the frame tail) over
  `ws.launch_named`, the ONE lookup the serial `run` also uses — title *and*
  folder, because on device those differ by construction. Its exit returns to
  the connection screen, and it does so from `go_home`'s tail rather than at
  each exit site: "return to the launcher" has many doors and they all funnel
  there. `GET /sync` → `{"sync":1}` is the capability marker the page probes
  once at boot (a static host 404s it) before offering the button.
  The **Zero is re-provisioned as the sync RPC's first host**
  (`firmware/seeed_xiao_esp32s3_zero/` — stock MicroPython + `provision.sh`
  pushed files, no build; the browser console served FROM the XIAO's flash
  round-trips authored carts onto it, verified with real headless Chrome
  2026-08-25). **It carries the whole stack, not the minimum that boots**
  (owner call, same day): the sync stack's full import closure — `moy_carts` +
  `moy_image` for the #108 files half, `moy_journal` for the history it now
  keeps — and `provision.sh` mints a pairing pin for a board that never went
  through the setup form. That directory's README is the authority on both, and
  `tests/test_zero_provision.py` ratchets its cp list, which is the whole build
  system on that board.
  **The browser runs moycore too since 2026-08-13** — the
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
  VM's JS wrapper dies at boot with "Module.getValue is not a function". Dev loop:
  `serve.py` over `dist/` (`--carts DIR` makes it the board twin: live carts.json
  + POST /sync against a plain directory); `node harness.mjs` drives it headless.
  **`moy.py` was DELETED 2026-08-25** (owner call): it was moy-spec's CLI shape
  left behind — its `run`/`export` served a `runner/` dir this repo never had
  (worker.js missing from its file list), `new` duplicates the console's own
  +New, and the real `moy` CLI (new/run/export/port/demo) lives in the spec repo.
  Hot reload went with it, unmissed. **To see what the BROWSER shows, use
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
  ("moy core 0.2") + runner + the `moy` CLI (new/run/export/port/demo) + the
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

### The UI refactor landed (2026-08-19) — one widget vocabulary, apps as data, user apps

Record: `docs/history/ui_refactor_2026-08.md` (it folds `ui_widgets_2026-08.md`
and `shell_decoupling_2026-08.md`, and CUT about half the combined program on
evidence — read its Section 1 before proposing any cut part again). The rules it
left:

- **The 320×240/1× baseline does NOT exercise the toolkit.** `editor_app._draw_zone`
  and siblings are guarded `if not ws.layout._base`, so perturbing a widget turns
  the Guition/fs3/windowed configs red and leaves BOTH T-Deck rows green.
  **Verify a widget change on the non-`_base` configs; a green T-Deck row proves
  nothing.** The net is `tests/test_shell_goldens.py` + `test_settings_layer_pixels.py`.
- **Adding a system app is TWO files** (`docs/app_api_v1.md` has the checklist):
  an `"app"` block in the identity cart's manifest, and `runtime/app_decls.py`, its
  generated frozen copy. The five hand-lists it replaced failed silently and on
  device only. Ratchet: `tests/test_app_registry.py`.
- **The bar contract is a HOST guarantee**, scoped to `"tool"` — there are SEVEN
  strip kinds and collapsing them breaks the others.
- **Apps declare what they need** (`runtime/app_context.py`, a pure leaf) and there
  is **zero `ws.` in the app tier**. `ctx.files` and `ctx.carts` are split
  deliberately: carts authors executable content, so granting it to a cart is
  self-escalation. **No `property` forwards** (a plain hop is +0.5µs, a property
  +5.1µs) — live state reads through a method. `prefs_ns` exists because
  `paint_doc` is the real key in kids' `system.json` since #108.
- **Style is data** — `runtime/skin.py`, NOT `chrome.py`, which would close
  `ui → chrome → settings_layer → ui`. `tests/test_skin.py` pins skin knowledge to
  its two owner modules in both directions.
- **A user can add an app.** `runtime/system_api.py` maps manifest permissions to
  roles as an ALLOWLIST; never grantable: `shell`, `carts`, `wallpaper`, `artwork`,
  `damage`, `surface`, `clipboard`, `notify`. **An ungranted verb is ABSENT, not
  stubbed** (`system_carts/notes.moy` is the proof). Storybook/Sheets/Files/Paint
  STAY shell code; Calc is portable today.
- **The windowed DESK world must NOT bind the system canvas** — a cart there lives
  in a window whose blit source IS `ws.canvas`, so binding makes the desktop blit
  itself. Found only by rendering it.
- **`crash_guard` disables a type:"app" cart after three crashed opens**, and
  committing fixed CODE forgives the strikes (`Project.commit_code` →
  `forgive_app` is the only thing that clears the count).

### Two consoles, one game: ESP-NOW multiplayer SHIPPED (2026-08-22, #65/#7)

**`docs/netplay_v1.md` is the authority** -- the standing contract: the lockstep
model, the give-up doctrine, the radio facts and the hardware terms, dated and
sourced to whatever measurement decided them. Read it before touching any of
this. The two campaigns behind it are the RECORD, archived 2026-08-27:
`docs/history/espnow_multiplayer_2026-08.md` (the S3 transport ledger plus the
four on-glass bugs no host test caught) and
`docs/history/espnow_p4_2026-08.md` (the C6-shim track, phases A-G). What
belongs here is only what a coder must not undo:

- **`runtime/netplay.py`** is the deterministic core and an import-free leaf like
  `players.py` (the button order arrives as a constructor argument, so `cart_api`
  stays its one author). **`device/moy_espnow.py`** owns ESP-NOW's single global
  recv slot for the whole firmware and dispatches by frame type; anything else
  that ever wants the radio registers there rather than opening its own.
- **The payload is INPUTS, never state**, and that is a measurement rather than a
  taste. **A missing input STALLS the sim; it never extrapolates** -- a guessed
  frame desyncs the two sims for good, and silently. Do not "improve" either one.
- **Input delay is ADAPTIVE and raise-only since 2026-08-25**: matches start at
  DELAY=1 (33ms) and a session under real stall pressure raises itself to 2 --
  never down, because a lower delay mid-match can overwrite an input frame the
  peer already played. The 2026-08-22 "DELAY=1 measured and REJECTED (14%)"
  verdict was the PACING's fault, not the radio's -- burst catch-up
  self-hastened both consoles to the margin cliff; with debt-dropping,
  loop-rate stall retries and the guest phase slew, the S3 pair measures
  1.8-2.3% at DELAY=1 while the P4 pair (whose C6-shim transport genuinely
  consumes the one-tick budget) escalates to 2 by ~6s and plays clean. The
  archived campaign's "Input latency" section carries the whole measurement.
- **The BLE keyboard's background scan owned most of a radio board's packet
  loss** -- interval==window was 100% radio duty, 5s on/5s off, costing the P4
  ~40% of inbound espnow at an idle desk while hiding from every blocking
  bench (a stalled frame loop stops re-arming the scan). Background rescans
  are 10% duty + passive now; only the user-facing picker scans continuously.
  If a radio symptom appears only while the loop RUNS, suspect the scan first
  (`device/ble_keyboard.py` has the numbers).
- **modespnow's ring race is patched in every board build**
  (`patches/esp32_espnow_ring_race.patch`): the upstream reader raises
  `buffer error` on a healthy ring when it catches a record mid-write, and
  the ring then really is desynced. `_recover()` re-applies the PHY rate (an
  active() cycle silently resets it to 1M) and counts itself in stats().
- **The tuning recipe is ORDER-SENSITIVE and the ack LIES.** `rxbuf` before
  `active(True)`, the rate after. Both facts cost a session to learn; the module
  header carries each one beside the number that taught it.
- **The handshake is BROADCAST, addressed in the payload.** Unicast here needs a
  peer registration that an `active()` cycle wipes and the first beacon races,
  which cost a night to find. Do not tidy it back.
- **Every input packet carries the frame its sender is WAITING FOR.** A fixed
  redundancy window deadlocks two stalled consoles permanently.
- **Nothing waits forever.** An unanswered invite and a match that falls too far
  behind both give up and re-run the cart solo; a frozen screen with no
  explanation is the one outcome designed out.
- **A restart must not stop the radio** (`Player.release_world` stops the link
  only when `ws.netplay` is None) -- a forming match re-runs the cart, and the
  dying run used to kill the session that caused the restart.
- **Board scope: ALL THREE console boards since 2026-08-24** (a browser still
  has no radio). The morning of that day settled "the P4 cannot join" (the
  flag flip failed at link; ESP-Hosted's RPC carries no ESP-NOW; upstream
  esp-hosted-mcu #19 open and unshipped) -- and the rest of it un-settled the
  verdict by BUILDING the path the verdict named: hosted 2.12.12 + the moy_c6
  shim (seventeen esp_now_* wrappers over custom RPC) + a shimmed C6 slave
  flashed over its own SDIO link. **`docs/history/espnow_p4_2026-08.md` is that
  whole campaign** -- the phases, every on-glass verdict, the BLE regression
  and its fix, and the P4<->T-Deck Brick Siege match at 28.6 ticks/s. Its Phase F
  (2026-08-25) is the stall-rate hunt: the shim's blocking send RPC moved off
  the VM core onto a TX queue, which is what made the pair playable; the stall
  rate and the per-send cost are in that campaign record. Phase G (same day): the C6
  image SHIPS -- CI builds and publishes it, `latest-p4.json` carries a `c6`
  block under its own signature, and Settings -> UPGRADE C6 RADIO
  (`device/moy_c6_update.py`, P4-gated) downloads and flashes the slave over
  SDIO with the slave self-reporting its version (MOYC6_V_VERSION). On-glass
  end to end, including the second-run UP TO DATE.
  **FLOAT WIDTH IS PART OF THE LOCKSTEP CONTRACT** (found by the owner's
  hands, first cross-arch match): two consoles in a match run the same sim,
  and REPR_C's 30-bit floats against boxed 32-bit singles diverge the worlds
  by construction -- 0/1105 world checksums agreeing before the P4 took
  `moybyte_patch_repr_c`, 1106/1106 after. Every board that can hold a link
  runs REPR_C; a board that cannot take it cannot join a match. Found the same day, unrelated to the radio: the P4's WiFi
  buffer set had silently never applied -- `esp_wifi_remote` renames every
  `CONFIG_ESP_WIFI_*` to `CONFIG_WIFI_RMT_*`, so upstream's own fragment asked
  with a dead name and the build carried 10/32/32/6/6 against a 65534 TCP
  window (the S3 commit's "half the set is worse than none" state, 0890249).
  The P4's sdkconfig.board now states the set in the RMT namespace, with the
  prose; numbers in #58.
- **LOCAL 2P is the same cart API with no radio at all** (#65 Phase 1): Settings
  -> **2 PLAYERS** gives a paired Bluetooth keyboard the second player slot, so
  two kids share one screen using two real keyboards. Capability-gated on
  `ws.second_keyboard()`, which is non-None only where a board ALREADY has a
  keyboard of its own (the T-Deck); elsewhere the Bluetooth one is `ws.keyboard`
  and reassigning it would strand player one. The mechanism is entirely #26's: a
  source carries a player, two disagreeing IS multiplayer. Two honesty rules fell
  out and both are pinned -- the setting REFUSES where it cannot work, and a
  DISCONNECTED keyboard releases its slot rather than leaving a cart with a
  character nobody drives. **Dividing the T-Deck's built-in keyboard between two
  kids was built and REVERTED the same day (owner call, 2026-08-22): that thumb
  keyboard is far too small for two people, and the second keyboard is the
  answer.**
- **`system_carts/brick_siege.moy` and `harpoon_pop.moy` are two-player**, and
  read `btn(name, i)` without learning where the second pad came from -- the
  point of one API. The Lua twin is ported in step (its parity test compares
  every draw call for 3000 frames). Brick Siege's roster global had to be renamed
  `players` -> `tanks`: `players` is the API verb's name, and a list shadowing it
  made the cart call a list the moment it asked how many players there were.
- **A Lua cart could never see a second player** until 2026-08-22: the moycore
  snapshot has slots for player two and nothing filled them, so libmoy's
  `players()` answered 1 forever and the line-faithful Lua twin of a 2P cart
  fielded one tank where the Python original fielded two. Both tiers feed it now.
- **The master audio level persists across a cart start** (`ws.system["volume"]`,
  applied in `project._build_audio`). The backend is rebuilt per run, so `vol 0`
  at the launcher used to print "no audio backend" and change nothing -- a mute
  that looked like it worked until the next game played at full volume.

### The 2026-08-22 hardening pass — the rules it left

Record: `docs/history/sweeps_2026-08.md`. The recurring shape, and the first
thing to check when adding anything here: **a mechanism was promoted into one
body and nothing executable guarded it.**

- **A board with no lever reports `None`, never `0`.** A frozen 0 is also what a
  BROKEN lever looks like, and that ambiguity is what hid `PUMP fold=` printing 0
  on every board for weeks. Apply it to every new meter.
- **A counter that reaches no consumer is not instrumentation.** The Guition's
  route is `state`, not PUMP — it stages no `device_diag` (its board.toml says
  why; the P4's absence is an unwritten omission, not a decision).
- **`sdkconfig.board` is the only copy of a board's sdkconfig facts.** The guard
  derives its list and splits "is the tree stale" (a fingerprint) from "did Kconfig
  honour it" (a grep that only means anything once the fingerprint matches).
  **The two S3 fragments are 35-of-36 identical and must NOT be merged** — each
  value carries its own per-board argument.
- **A wired service must be STARTED and POLLED, not merely built**, and `poll` must
  be per-frame: on the boot path it runs once and reads statically identical.
- **Derived values get one author.** The two InputStates stay separate at 94%
  identical — `BUTTONS` is 8 names vs 15, in different orders, and a test says
  asserting them equal would be wrong.
- **Storage READS take the SD gate too, not just writes.** `poll_webhost` runs at
  the frame tail after `kick()`, so a frame that painted leaves the feeder shipping
  bands, and an sdspi transaction there is the documented panic.
- **The browser gets carts, not their history** — the undo journal crosses the wire
  in neither direction; the receiving side writes its own (`pmem` does cross).

### The shell carve LANDED (2026-08-27) — six collaborators behind the Workstation façade

Record: `docs/history/console_architecture_2026-08.md` (rev 3, #209). Six
collaborators — `ws.web`/`prefs`/`covers`/`carts`/`look`/`history` — came out
from behind the façade in five gated landings. `tests/test_console_facade.py`
pins every surviving forward with its caller, the 17 legacy property forwards,
and every `getattr(ws, "…")` name in runtime/device/tools. Facts not to undo:

- **`ws.system` is an ALIAS of the SystemStore dict and is never rebound** —
  `SystemStore.load()` mutates it in place. That identity is why settings_layer's
  and dev_channel's raw writes never had to migrate.
- **The achievement overlays are EVENT-PUSH**: an unlock writes the flat kernel
  deadlines from the arm site; `_animating` and both WMs read plain ints and never
  call into `ach`/`ach_ui`.
- **`theme_colors` stays a flat kernel REBIND on purpose** — the launcher's cache
  keys fold `id(ws.theme_colors)`, so the rebind IS the shelf invalidation. An
  in-place alias there would be a live bug.
- **The frame loop reaches collaborators directly, never through a forward**;
  per-card grid paths use injected BOUND methods.
- **`_icon_cache` is CoverCache's and invalidates on a rescan**, BEFORE
  `slim_carts` re-bakes — slimming is the last moment a cart's sprite art exists
  in RAM.
- **Serial vocabulary moved with the code**: `p4_conformance` speaks `ws.carts.all`,
  `p4_hitch` wraps `ws.history.idle_tick`, `p4_chrome_freeze`/`p4_scroll_ab` speak
  `ws.look`.

### The 2026-08-28 quality sweep — the rules it left

Record: `docs/history/sweeps_2026-08.md`; the gates are in #206, #207, #208.

- **ONE `PERF` line, one producer, three boards.** `runtime/perf_line.py` holds
  the field table, the formatter AND the parser, measured by
  `device_boot.PerfSampler` on `FrameLoop.account`. **A field a board cannot
  measure prints `-`, never `0`.** Cart titles are slugged and compounds join with
  `/`, because both readers split on whitespace and an inner `=` reads as a field.
  `tools/p4_perf.py` requires `--board`: it used to default to the P4's dtr/rts
  LOW, which on an S3 is a chip reset that strands the handle.
- **`moy_flush`'s tail wait runs even after a queue error** — the T-Deck's SD fence
  and the Guition's `s_retrieved` reconciliation both depend on it, and skipping it
  leaves band DMA live on a bus an sdspi session is about to take.
  `tests/moy_flush_harness/` compiles the REAL C on a host with no board.
- **Adding a settings toggle is one entry in `SETTINGS_TOGGLES`**, not six sites.
  The capability gates stay expressed, and the flat mirrors stay flat attributes —
  `frame_cap_fps` reads `ws.frameskip` every loop iteration on all three boards.
- **The `colors=` hatch is 14 sites and each has a written reason** (`ui.row`/
  `ui.cell` take `kind=`, like `ui.button`). `row_menu` and `row_list` are
  deliberately two skin entries: `ink_dim` and `chrome_ink_dim` resolve differently
  on machine/dark, so collapsing them passes every golden and moves a pixel.
- **The on-glass suites share one fixture** (`tests/on_glass.py`), and
  reset-vs-attach is read from `board.toml`, never chosen in a suite.
- **A doc names a file only if it exists** — `tools/check_docs.py` resolves
  backticked paths AND bare `.py` names, and pins cross-document duplication so it
  can only shrink. Run it after any docs edit.

### Host == device: the shared console (important)

The console UI is **one codebase** that both the host simulator and the
device run — they render the *same* 320×240 pixels with the *same* petme128 font.
The canonical sources live in `runtime/`; `build.sh` **stages copies into the
firmware `modules/` tree**, re-staged every build and gitignored, so the device
freezes the identical code. The shipped **2026-07 shell** (spec
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
  split (build-staged like the rest): **`Project`** = the open cart's live workspace and its
  `commit_*` verbs (`runtime/README.md` lists what it holds; the rule here is that
  a commit also appends the undo journal and runs graduation detection). **`Player`** = the
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
  Library** (the play world) — `docs/shell_ux_v1.md` is the UX authority for both
  worlds; the rule a coder needs is that windows exist ONLY above `"desk"`, so
  every `not _order` deferral presents fullscreen ( `composite_game` routes the play
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
  RLE decode fell by ~three orders of magnitude, so reading a per-size crop
  sidecar cost the SAME as rebuilding from the blob while also charging a write
  per cover per size. Covers now cache their PARSED RUNS in RAM instead
  (`Workstation._cover_runs`), which is what makes a window resize cheap.
  Numbers in #155/#66. Also passes the **#67 dual-runtime fields** (`runtime`/`main`) through
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
  `runtime/lua_host.py` over `runtime/lua_binding.py` — ctypes over the SAME
  vendored Lua 5.4 + libmoy binding the boards build, `LUA_32BITS` included,
  compiled on demand into a hash-cached `.so` (lupa and its `lua` extra were
  DELETED 2026-08-14: no compiler means the runtime-missing panel, not a
  second engine — same rule as host audio). Device +
  browser runtime: **`moycore`** (`native/moycore/`, staged to all three boards
  and the wasm head), which binds the vendored Lua 5.4 through **libmoy's own
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
  list** (`LIBMOY_VERBS` + `NOT_REGISTRABLE` — ONE definition in
  `runtime/lua_ext.py`, imported by both `moycore_glue` and `lua_host`; they
  were twinned copies once, and are not now): what is stable and enumerable is what libmoy OWNS, and
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
  map walk** (`__moy_map_masked`/`__moy_map_flags`, #66 M0 — a large slice of celeste's
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
  `docs/moycore_direction.md` (tracker #192) is the direction doc, and its
  gap list is the authority on what the pin does NOT yet cover; the walked
  plan behind it is `docs/history/moycore_plan_2026-08.md`.
  Every non-spec verb still trampolines to the SAME Python `make_api` closures
  (tuple returns fan out to Lua multivalues, so `touch()` needs no wrapper).
  Related trap in the same family, fixed upstream: `moy_console` holds its sheet
  and map by POINTER and a brand-new project has neither, so `spr(0,0,0)` in an
  empty cart used to segfault libmoy's binding — a board reset with no message. Related S3 lever, same date: the **#190 flush-bounce scale fold** —
  a small-canvas game frame's composite is synthesized inside the bounce bands
  (root fb untouched on quiet frames; overlays disarm and pay the old cost;
  the dev channel's `state` → `fold` is the on-glass proof, backed by
  `moy_axs.fold_stats()`. **NOT the `PUMP` diag line** — that line's `fold=`
  read a `fold_count` attribute NO compositor defined, so it printed 0 on every
  board from the day it was written until 2026-08-21, and this file called it
  "the on-glass proof" the whole time. It could not have been one even wired:
  the T-Deck has no fold, and the Guition — the only board that has one —
  denies `device_diag` and so has no PUMP line at all. `state["fold"]` is
  `None` on a board with no fold lever, never 0, because a frozen 0 is exactly
  what a BROKEN fold looks like and that ambiguity is what hid this). Three hardware-learned constraints
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
  (#107: `mp_obj_new_int_from_ll` per coordinate allocated per upcall arg — enough
  marshalling garbage to force a visible periodic auto-collect during play (#66);
  from_ll survives only as the >31-bit fallback).
  `system_carts/sakura_lua.moy` is the A/B twin of sakura (line-faithful,
  bit-identical over 600 host frames — `tests/test_lua_sakura_parity.py` +
  `experiments/lua_bridge/host_parity.py`); the measured cross-board verdict
  lives in #67 (S3: parity with auto-native Python; P4: flat logic against
  Python's GC spikes). A canvas without a `_batch_arr`
  (the wasm head's recording CommandCanvas) declines the C fast path — the
  Tee-specific decline guards died with the 2026-08 streaming sunset. The
  Phase 4 protocol tests + Phase 5 UX tail
  (crash-line mapping via `player._lua_cart_line`, the per-language symbol
  palette in `code_layer`, the docs Lua section) shipped 2026-07-15 (`21c1278`).
  **`LUA_32BITS` is DECIDED AND ON** (`6ddaf7c`, `luaconf.h`): both boards' FPUs
  are single-precision, so doubles would be soft-float — 32-bit floats use the
  hardware FPU and halve TValue. Since the lupa deletion the host binding builds
  the same `LUA_32BITS`, so all tiers share float semantics and integers wrap at
  2^31 everywhere. Recorded because this line said "remaining: only the LUA_32BITS
  decision" for weeks after it was made, and a 2026-08-09 perf hunt spent its
  last lead re-proposing a lever that was already shipped.
- **pmem persistence is DEFERRED (#66, on-glass 2026-07-14):** `pmem(i, v)` is
  RAM + a dirty mark; `Pmem.flush()` persists at cart exit (`release_world`),
  the crash capture, the workspace swap, and a periodic frame-boundary save
  (`player.PMEM_FLUSH_MS`, 60s). The old per-write SD save was Letter Blitz's per-pop "word-event logic spike"
  (probe-attributed on glass; #66). The
  perf_capture-gated `PMEM save=<ms>` diag line shows the deferred cadence.
- `runtime/font.py` — petme128 8×8 font, the ONE glyph source both backends rasterize (#62): the host draws it per-pixel, the device passes its blob to the native `moy_gfx.text` kernel (staged as `moy_font` at build; framebuf.text — same glyphs, no clip rect — is the no-gfx fallback).
- **UI scrolling is kinetic + scroll-as-blit (#113, 2026-07-22 — the living plan/status issue):** `ui.ScrollRegion` owns the fling physics (all dt INJECTED from the loop — never a clock — so tests are exact-trajectory deterministic) plus a painted-frame ring; an eligible drag/fling frame SHIFTS the retained pixels via the `scroll_rect` system verb (ONE implementation on every tier since the canvas flip — `DeviceCanvas.scroll_rect` over `moy_gfx.scroll_rect`, which the host reaches through `runtime/gfx_binding.py`; the old host `canvas.py` lane is deleted) and repaints only the exposed band (`Launcher.draw_shift` — the home shelf + Editor picker pilots; Settings still row-snaps, its pixel-smooth conversion is #113 Phase 5). The learned rule: **everything inside a scrolled band must be a pure function of the offset** (the picker's dots now ride the scroll in-band). The ring pins sel/statics/`ws._cover_gen` and measures against `RETAINED_FRAMES` paints back (host/layers 1, device root ping-pong 2). Web transport: the `scr` op shifts the browser's retained buffer (never deduped to `{"same":1}` — replaying a shift double-applies), covers + the static wallpaper composite ship ONCE via `/assets` (`ws.cover_assets`, serial names), and the windowed WM's gesture-vs-window checks resolve by IDENTITY (`_wins.get(key) is win` — the shared "make" group's `win.kind` is the CONTENT kind, so `key == win.kind` never matched and silently disabled the drag content-freeze/stamp-defer everywhere).
- `runtime/host_app.py` — host glue: host `make_api`, `build_workstation()` (injects `ws.lua_runtime` when the native binding builds, #67), `ConsoleDriver` (mouse=touch, arrows=trackball). Not on device.

(The pre-unification host UI — `shell.py`/`workstation.py`/`engine.py`/`api.py`/
`cartridge.py` — was removed once the shared console replaced it; issue #17.)

### Device module map

The T-Deck's own board code is `firmware/lilygo_t_deck_plus_mainline/modules/`
(six tracked files — the rest of that directory is STAGED at build and
gitignored). Everything both boards share moved to the repo root when the fork
went: the device tier is **`device/`**, the C modules **`native/`**.

- `moybyte_shell.py` — boot/`main()`; mode flags `RUN_DESKTOP` / `RUN_TOUCH_CALIBRATE` / `RUN_KEYBOARD_PROBE` (the STAGE3/NATIVE_CORE bring-up benches and the pre-display SD-prefetch A/B toggle were removed; the #63 `MOYBYTE_BENCH=1` build is the benchmark harness).
- `moy_runtime.py` — the **device backend**: `DeviceCanvas` (hot ops `cls`/`rect`/`circ`/`spr` go through the native `moy_gfx` kernel — `fill`/`fill_rect`/`blit565` straight into the compositor's RGB565 buffer — with framebuf for text/lines and as the no-`moy_gfx` fallback; `spr` blits a per-sprite pre-scaled RGB565 cache, and `make_api` reuses one tile `Image` per `(id, colorkey)` so the cache survives across frames), `make_api`, embedded fallback `CARTS`, `TrackBall`, `Touch`, `run_desktop()`, `run_keyboard_probe()`. Imports the shared `console`/`editors`/`moy_carts` and injects the device `make_api` + store into `console.Workstation`. **Input runs on a poller thread (#69, `MOY_INPUT_POLLER`)**: `moybyte.input.InputPoller` owns every I2C0 transaction (kbd + GT911 + mode switches) off the frame loop, so the C3's 40-60ms clock-stretch stalls block only that thread — requires the build's `esp32_i2c_gil_release.patch` (machine.I2C frees the GIL across its blocking wait); falls back to synchronous polling if `_thread`/the thread dies.
- `console.py` / `project.py` / `player.py` / `editor_app.py` / `wm.py` / `editors.py` / `moy_carts.py` (+ the `*_layer.py`/`*_ui.py` surfaces and `blocks.py`) — **staged from `runtime/` at build** (see above).
- `device/moybyte_sd.py` — SD mount on the shared SPI bus; `with_sd(fn)` = mount → run → unmount + deselect.
- `tdeck_panel.py` + `native/moy_lcd/` — the panel backend, replacing the fork's
  `tdeck_display.py` (LVGL bootstrap) and `moy_compositor.py` (Python banding).
  `TDeckCompositor` is the ping-pong + `ASYNC_FLUSH`/`LAYER_COPY_ASYNC` levers and
  the `bounce_stats`/`pump_last_us` meters; `moy_lcd` owns the ST7789, the banded
  flush and the `kick`/`pump`/`drain` protocol. The hard-won rules moved INTO the C
  with it — DMA only from internal SRAM, only the first band carries a command
  (what "a full-screen flush must be a single `tx_color`" really meant), and a band
  must fit one SPI DMA transaction, that last one as a compile-time assert. The
  #190 flush-bounce scale fold is deliberately NOT ported (`fold_supported` is
  absent, `PUMP` prints `fold=0`, nothing degrades) — see `tdeck_panel.py`'s header.
- `device/moy_ota.py` — OTA firmware updater (#53): `OtaUpdater` flashes a new app image from `/sd/update/*.bin` into the **inactive** OTA slot via `esp32.Partition` (block-erase `writeblocks`), then `set_boot` + `machine.reset`. Phase 3 adds WiFi download — `check_online`/`begin_download`/`download_step` stream a manifest-described `.bin` over a raw socket straight to SD (sha256-verified, never buffering the whole 3MB), reusing the injected `wifi` service. Device-only; `run_desktop` injects it into the shared `Workstation` (which owns all the update-screen pixels), wires the wifi service, and calls `mark_valid()` at a healthy boot to cancel rollback.
- `device/moy_webserver.py` — the device **socket/HTTP/WebSocket transport core**. Until 2026-08-12 this was the device WEB VIEW (#41/#22, owner-verified once on-glass 2026-08-01, #182) — the streaming browser mirror. **The whole streaming stack was DELETED in the 2026-08 sunset** (`docs/history/moycore_plan_2026-08.md` §3.2, owner decision; `tests/test_streaming_sunset.py` pins the absences): the frame push, `device_webview.py`, the recording `TeeCanvas`, stream mode, the Settings WEB VIEW row, `ws.web_hook`, the host `tools/web_console.py` + its VM deploy recipe, and the decline-the-Tee guards in `moy_lua_glue`. The browser's job belongs to the **wasm head** (`firmware/web_runner`), to be synced per §3.4; mirror-of-glass is an accepted loss (a screenshot verb on the sync RPC was the recorded successor and was DROPPED, owner 2026-08-25 — the browser IS the console, so show-and-tell happens there). What survives here — deliberately, for the §3.4 sync RPC to ride — is the bare transport: non-blocking listener, `parse_request`/`http_response`, the RFC 6455 upgrade + framing (shared `web_view_ws`, the only file of that lineage the boards still freeze), one persistent non-blocking `_WSConn` (cross-iteration read buffer, blocking-budget sends, idle reaper), and a `WebServer` with `handle_http`/`on_text`/`send_text` seams, no consumer wired. **The recording stack is GONE as of stage 4** (2026-08-12): the wasm head rasterizes, so `runtime/web_view.py` and `runtime/web_view_page.py` were deleted outright with the recorder, CommandCanvas, RecordingLayer, ServedState, SurfaceDelta, WsClientState, the wire protocol and the page's JS replayer. Two pieces of that module were never about rasterizing and survive on their own: `runtime/web_input.py` (browser events → InputState/Pointer, which the §3.4 RPC also speaks) and `web_view_ws.py`. `runtime/surface.py` and `wm_windowed`'s `if not self._recording` guards deliberately STAY, unreachable — `docs/surface_model_v1.md` §13 records why, and is the place to argue with it. The XIAO Zero port stood entirely on the deleted stream; the owner re-based it the next day (plan §3.2): the browser runs the wasm head, and the Zero becomes the pocketable cart-store + GPIO peripheral it pairs with (#41 direction, #9 pins) — its rebuild rides the §3.4 track.

### Hard device constraints (learned the painful way — respect these)

- **SD shares the SPI host with the display, and getting it wrong HANGS the
  board** — gray screen, dead USB, no panic. Three rules, each learned on
  hardware:
  - **Nothing touches SD before the panel is up** (#56). A pre-display mount
    re-runs `spi_bus_initialize()`, and on a POPULATED card it succeeds while
    leaving the shared host claimed, so the next `init_display()` intermittently
    failed — the "no-SD boots, SD-with-files doesn't" bug.
    `PREFETCH_SD_BEFORE_DISPLAY=False`; carts load after init and degrade to the
    built-ins on any SD failure.
  - **After the panel is live, never `machine.SDCard`** — `esp_lcd` and that
    driver fight over one host and a CS-deselect is not enough. Live reads and
    writes go through the native `moy_sd` ATTACH (`sdspi_host_init_device`, no bus
    re-init — the ESP-IDF "Sharing the SPI Bus" pattern), which leaves the panel
    device intact. `moybyte_sd.with_sd_live(fn)` mounts once and keeps the card
    RESIDENT for the session.
  - **Do not tear the SD device down between ops, and do not touch the CS pins.**
    A per-op `sdspi_host_deinit`, or reconfiguring `TFT_CS` via `Pin(...)`,
    corrupts the shared bus/DMA state and the NEXT PANEL FLUSH silently hangs the
    board — the write itself lands, then resume freezes. Leave `TFT_CS`/`SD_CS`
    alone (driver-owned); park only the unused LoRa `RADIO_CS`. Never flush the
    panel inside a session: the loop is single-threaded, so SD ops run between
    frames. (`tests/test_moybyte_sd.py` pins which lifecycle touches which pin.)
- **T-Deck serial RX WORKS, and the fix was three things at once (#201, 2026-08-16).** TX always
  streamed (PERF/HITCH lines flow for hours). RX did not, and the explanations in this file were
  wrong twice: first "this fork's USB-CDC stack has no at-arrival interrupt-char scan" (false —
  `tud_cdc_rx_cb` is linked and does scan), then micropython#18581's "CDC only initialises at the
  REPL" (true of CDC, but not the reason — the image had NO stdin path at all). `nm` settled it:
  `tud_cdc_rx_cb` present, `tusb_init` ABSENT, `usb_serial_jtag_isr_handler` ABSENT, and stdin bound
  to `uart_stdout_init` on U0RXD — a header pin with nothing attached. Bytes written to the
  enumerated interface were accepted by the host stack and dropped.

  The mainline port fixes it with three changes that are only sufficient TOGETHER, which is why each
  was measured as a failure on its own:

    1. `MICROPY_HW_ENABLE_USBDEV (0)` — `MICROPY_HW_USB_CDC = USBDEV` forces
       `MICROPY_HW_ESP_USB_SERIAL_JTAG` to 0 on the S3 (`SOC_USB_OTG_PERIPH_NUM == 1`), compiling out
       the ISR that fills `stdin_ringbuf`. It also gives MicroPython its own TX.
    2. USB-Serial/JTAG as the **PRIMARY** ESP-IDF console. A SECONDARY console is output-only by
       design, so input was never possible while it was secondary.
    3. `MICROPY_HW_ENABLE_UART_REPL (0)` — UART0 shared the ringbuf, and its floating pin is where
       every `SERIAL rx=1` stray byte came from.

  (2) alone HANGS the board: with USBDEV still on, `mp_hal_stdout_tx_strn` falls through to IDF's
  blocking primary console. So it needs (1)'s non-blocking `usb_serial_jtag_tx_strn`, which gives an
  absent host one 200ms timeout then latches `terminal_connected = false`.

  **The fork could not be fixed this way and was never made to work.** Its `MOYBYTE_REPL=jtag` mode
  had three independent bugs (documented in the deletion commit); with all three fixed it boots and
  PRINTS but still takes no input, on an identical console config and identical linked symbols. The
  remaining difference is the MicroPython base itself. The fork is gone, so this is history, not a
  TODO.

  **Do NOT use the USB product id as the RX tell** — the old note said `303a:1001` = RX dead,
  `303a:4001` = RX works. On this port a WORKING board enumerates `1001`, because that is the
  USB-Serial/JTAG peripheral doing its job. `4001` means TinyUSB CDC, which is now the arrangement
  that does NOT take input here.

  Flashing: esptool works, but `write_flash`'s own trailing reset does not start the app — a SEPARATE
  `esptool --before default_reset --after hard_reset` does, so no human reset is needed between flash
  and boot. The ROM-loader entry by hand (**hold the trackball in — it is GPIO0 — while powering on**)
  is still the recovery path when an image wedges the USB device.

  Serial reads are unreliable ACROSS a reset: the device node is torn down under an open handle, so a
  reader that opens too early sees zero bytes and looks exactly like a dead board. Three separate
  "the board is silent" conclusions in one session were this. Read with miniterm, or open after the
  boot settles.

- **Full-screen flush must be a single `tx_color`** from a PSRAM DMA buffer; multiple `tx_color` calls glitch rows at the command→data boundary.
- **The keyboard has two modes; the console flips between them per screen.** The T-Deck keyboard is a separate ESP32-C3 (I2C 0x55; firmware in `firmware/lilygo_t_deck_plus_reference/examples/Keyboard_ESP32C3` — an UNTRACKED vendor reference tree, so a fresh checkout will not have it; THIRD_PARTY.md's scope note explains why). In its default mode it returns clean 1-byte ASCII (shift→uppercase, sym→symbols/digits, all resolved on-keyboard) but reports each key **once on the press edge with no autorepeat** — so a *held* key can't be detected, only faked for `KEY_HOLD_MS` by `TDeckKeyboard`'s latch (movement stalls while you hold). For true hold-to-move, a running cart switches the keyboard to **raw-matrix mode** (`0x03`, `LILYGO_KB_MODE_RAW_CMD`): it then streams the full key matrix each read, so a held direction keeps firing. `Workstation._set_text_mode` → `TDeckKeyboard.set_game_mode(on)` drives this: ASCII for the code editor (so typing is clean — `last_key`), raw everywhere else. The revert is `0x04` (`..._MODE_KEY_CMD`) — the step an earlier attempt missed, which is why raw mode used to garble the editor *irreversibly*. **`__init__` boots in ASCII and never enables raw**; raw needs keyboard fw **≥ 2025-06-12** (`T-Keyboard_..._250620.bin`), and on older fw the `0x03` is ignored — `_read_raw_buttons` detects the stray ASCII byte and sticks the session back on the 1-byte + latch path (`_raw_unsupported`; class flag `RAW_GAME_MODE` force-disables raw). The keyboard has **no `=` `[ ] { } < > %`** keys at all → the code editor shows an on-screen symbol palette for those. (`0x01 <duty>` over I2C sets the keyboard backlight.) Use `RUN_KEYBOARD_PROBE` to dump keys over serial (USB-friendly, no takeover).

## Conventions

- The current design doc is **`moybyte_console_plan_2026-07.md`** (repo root); superseded v0.1/v0.3/v0.4 docs are archived under `docs/history/`. The **current `.moy` cart API** is documented in **`docs/moy_cart_api.md`**; the legacy `.moyproj` SDK specs (api / project-format / runtime-contract) are archived under `docs/history/` too. The shipped v0.5 shell's UX reference is **`docs/shell_ux_v1.md`** (corrected to the as-built reality); `docs/shell_architecture_v1.md` (privileged system carts + layered compositor) is the standing direction doc; the three implemented shell plan docs (`shell_ux_technical_plan_v1` / `shell_os_architecture_v1` / `shell_layers_refactor_v1`) are archived under `docs/history/`. **`docs/surface_model_v1.md`** is the standing presentation CONTRACT for every backend (immediate widget logic + retained surfaces + explicit generation-counter dirty + per-backend compositing strategy): read it BEFORE touching any rendering/compositing/invalidation code on any tier, and treat its §8 graveyard as settled — a new backend implements its §4 compositor contract, it does not invent a new invalidation mechanism. It absorbed its predecessor on **2026-08-27** and is the whole living record on UI frame cost now: `ui_damage_model_v1.md`'s own §0 review killed the fine-grained damage architecture (its "content-independent chrome" finding had no power — every one of those draw loops iterates the VIEWPORT, not the content, so identical numbers were structurally guaranteed), and what survived is **§14** — the shipped focused-window content freeze, which `wm_windowed._content_static` and its test now cite as their design source, and the evidence for one invalidation mechanism instead of six (2026-07-26: two of the six carried the same wrong key, cost 72ms of an 86ms frame twice per gesture, and produced no signal whatsoever). §8 carries the **why-not-LVGL** decision with its numbers (used on the T-Deck for panel/bus bring-up only, never to draw; our own blitter took that path 47→90fps; deleted entirely with the fork on 2026-08-17), so that is not re-litigated. The full record, including the phases the review walked, is `docs/history/ui_damage_model_v1.md`. **`docs/board_ports_2026-08.md`** is the standing direction doc for ADDING A BOARD (tracker **#202**: the Guition S3 JC3248W535 and a Guition P4 are the named next two): the per-board bill, **Phases A–D all landed 2026-08-17** (flash/CI plumbing as board.toml data; the shared `FrameLoop`; the GT911 core promoted; the six-stage port checklist), the declines (no driver registry/ABI, no codegen) — read its checklist before starting any port.
- **Issue mirror (`docs/issues/`, gitignored):** a **local, un-committed** snapshot of every GitHub issue, split into `open/` and `closed/` (files named `NNNN-slug.md`) plus `INDEX.md`, so an issue number referenced in a commit, doc, or chat resolves without network access. GitHub is the source of truth — this is a generated read-only mirror (not in git, to avoid churn), so a fresh checkout won't have it: **build it with `make sync-issues`** (wrapper over `tools/sync_issues.py`; needs the `gh` CLI, authed). The script wipes and rewrites both folders from `gh`, so state changes and edits never leave a stale copy. **Run `make sync-issues` at the start of any session that reads or reasons about issues, and again after EVERY issue you open/close/comment/edit** — the mirror is only trustworthy if syncing is a reflex, and living-body issues (like the #66 performance ledger) go stale locally the moment the body is edited on GitHub. Don't hand-edit the files.
- Tests run against the host packages only; firmware tests (`tests/test_micropython_spike.py`) grep the frozen device modules' source rather than executing them.
- **On-glass testing — all three boards have a suite** (#156). Each is gated on
  its own env var and shares one session in file order, leaving the board where
  it found it: `tests/test_p4_on_glass.py` (`MOYBYTE_P4_PORT`),
  `tests/test_tdeck_on_glass.py` (`MOYBYTE_TDECK_PORT`),
  `tests/test_guition_on_glass.py` (`MOYBYTE_GUITION_PORT`), over
  `tools/p4_autotest.py`'s `P4Board` and the shared `tests/on_glass.py` fixture.
  - **The line state at open is per-board and OPPOSITE, and it is DATA.**
    `P4Board(board_dir=…)` reads `dtr`/`rts`/`attach_only`/`chunk` from that
    board's `[serial]` block. The P4's CH343 opens with both LOW; the two S3
    boards' USB-Serial/JTAG is ON the SoC, so opening them low is a CHIP RESET
    (`rst:0x15`) after which the device re-enumerates under the open handle and
    every read returns nothing, forever — indistinguishable from a dead board.
    `attach_only` REFUSES a reset rather than recording one.
  - **Merely OPENING the P4's CH343 reboots it** (`rst:0x1`; the Linux CH34x
    driver glitches the reset circuit). A bare probe right after open is
    measuring a board mid-boot, ~17s to the desk.
  - **The dev channel is ONE class** (`runtime/dev_channel.py`) with one
    vocabulary: `state`/`tap`/`run`/`open`/`swipe`/`drag`/`diag`/`skip`/`gov`/
    `mem`/`bl`/`vol`/`power`/`web`/`py`/`quit`. A command a board cannot serve
    DECLINES. Board extras arrive as a handler dict, `py` scope extras via `env`.
    `state` is one-line JSON and assertions read console STATE, not pixels;
    `swipe` goes through the real pointer feed; `py` evals against the live
    console between frames.
  - **`quit` exits the DESKTOP to the REPL, not the running cart**
    (`REMOTE quit -> REPL`). Using it to end a cart leaves the board at `>>>`,
    after which every suite errors with "did not answer `state`" and reads like a
    dead board. Recover with a **Ctrl-D soft reset** — it re-runs `main.py` and
    does NOT re-enumerate USB, which is what makes it safe on an attach-only board.
  - **Never put a call that blocks on FLASH inside a `pyexec` snippet.** `pyexec`
    uploads in chunks while `cmd` sends one line, so a real file write stalls the
    loop long enough for a streaming PERF line to interleave into the exchange;
    the reader then parses the fragment as a COMMAND and `int()`s its argument,
    surfacing as a `PY ERR` that names nothing that is wrong. Issue those as their
    own short `cmd`.
  - **A UART board's stdin ring is ~256 bytes with NO flow control**, so
    `_write_line` paces bursts and any other writer of long lines must too (USB
    boards backpressure and never need it). `SERIAL_LINE_MAX` must fit the
    harness's `pyexec` chunk lines — at the T-Deck's original 96 every P4 upload
    was silently dropped as noise.
  - Waits and staleness: **wait for `REMOTE drag done`/`swipe done`** before the
    next command; PERF's `wmr/wmw/wms` are last-sample values that go STALE when
    their pass stops running (a repeated constant means "not running"); allow ~10s
    after a first `open picker` at a new size (cover pop-in, #155).
  - **Look system-app carts up by TITLE, never folder name** — the device seeds
    from the title slug, the host copies the source folder, and that mismatch is
    what broke `AppearanceAppLayer.is_app` on device (pinned by
    `tests/test_device_seed_parity.py`).
  - When a T-Deck sits wedged for esptool, `--before usb_reset` connects where
    `default_reset` write-times-out.
- **Cart versioning (#47):** every `system_carts/*/manifest.json` carries an integer `"version"`. `seed_builtins` re-seeds an on-SD built-in only when the baked version is **newer**, and preserves the kid's data (`pmem.json` saves + `config.json` tuning) across the re-seed. **Bump a built-in's manifest `version` whenever you change its content**, or an already-seeded device keeps the stale copy.
- **Device performance — #66 is the single source of truth** (the living ledger:
  current per-cart fps, the frame-budget model, shipped/reverted/open levers, how
  to measure). **Edit #66's BODY when new hardware numbers land; comments are the
  changelog** — then `make sync-issues`. P4 numbers are **#58**; the cross-board
  strategic analysis (why we trail native emulators, the frame-budget taxes, the
  PPA scale-only verdict, the ranked lever roadmap) is
  `docs/perf_native_gap_v1.md`, tracked by **#77**.
  - **Do NOT scatter numbers into this file.** One snapshot here quoted a cart's
    fps long after the board had passed it, inside the sentence forbidding
    scattered numbers.
  - **The engine-side lever chain is EXHAUSTED.** Every feed/dispatch/GC-cost
    lever was tried and either shipped (auto-native carts #67, live-set diet,
    pal-state variant cache #72, layer pool, `background()`) or reverted with a
    recorded verdict (Fold-2 auto map cache; the third bounce slot, which also
    retired the core-1 feeder unbuilt). What is left: per-cart render diets, the
    #67 Lua tier, and the P4 (#58).
  - **Per-board verdicts do NOT transfer.** The `-O3` `moy_gfx` pragma is
    A/B-confirmed on the S3 (compute-bound there) and measured NULL on the
    dispatch-bound P4 — one pragma line, opposite answers.
  - **Frameskip (#77) ships OFF** (Settings → FRAMESKIP, persisted, serial
    `skip 0|1`): a GAME's logic+input+audio tick every loop frame, render and
    flush every second — full-rate logic, 30Hz motion, at the cost of roughly
    double the alloc churn.
  - **Kid mode (#68): PERF DIAG is OFF by default and gates the diag frame-eaters
    — a measurement session needs it ON**, and DIAG SD LOG separately gates the
    periodic diag→SD write (keep it off for stutter-free serial measurement).
  - Three interpreter-vs-kid-idiom taxes were fixed ENGINE-SIDE with the kid API
    untouched: per-draw-call dispatch (#43/#63), call-frame heap-spill (#63), and
    **float boxing** — REPR_A allocated per float result, and the resulting
    heap-wrap collect was the long-standing micro-stutter; the REPR_C build patch
    (unboxed 30-bit floats) fixed it. Banding is structurally gone (the SRAM-bounce
    flush: panel DMA reads only internal SRAM).
  - Diagnostics, all gated behind `perf_capture`: `PERF`/`DRAWBRK`/`DRAW2`/
    `BATCH`/`FLUSHBRK`/`CHROMEBRK`/`PUMP`/`I2CSTAT`/`CALIB`/`HITCH`.
  - Open defects: #74 touch stalls, the launcher live-wallpaper cost, and #69's
    keyboard+touch I2C stalls (sized via I2CSTAT).
- **OTA and firmware updates (#53)** — verified end to end on the T-Deck and the
  P4 (2026-08-02: real WiFi, on-device signature check, streamed install, boot
  into the new slot, rollback self-heal). **Timings, sizes and rates live in
  #53**, not here.
  - **Both boards are dual-OTA already** (`otadata + ota_0 + ota_1 + vfs`), so the
    one-time migration is HISTORY. The slot sizes differ per board and are NOT
    repeated here: each `build.sh`'s generated table and the P4's partition CSV
    are the only things that decide whether an image fits.
  - **The OTA payload is the APP-PARTITION image, never the merged one.**
    `…_app.bin` is the payload; `…​.bin` is bootloader+table+app for a cable flash.
    Handing the merged one to `esp32.Partition` writes a bootloader into an app
    slot.
  - **`step()` returns True WHILE MORE REMAINS** (`update_ui` drives it as
    `more = u.step()`). Inverting it writes a truncated image, whose `set_boot` is
    then correctly refused with `ESP_ERR_OTA_VALIDATE_FAILED`.
  - **A cable flash must erase otadata FIRST**, or a board that has taken an OTA
    writes ota_0 and boots the stale ota_1 — indistinguishable from a flash that
    did nothing. `tools/board_flash.py` does it, from `[flash]` data.
  - **The rollback confirm fires from the FRAME LOOP**, not the boot path:
    `confirm_when_healthy(ws._frames_drawn)` needs `HEALTHY_PAINTS` frames on the
    glass AND `HEALTHY_LOOPS` iterations survived. Confirming where the desktop is
    merely CONSTRUCTED certifies an image that never drew a pixel (#56). **The
    paint threshold cannot be raised** — the console repaints only on change, so
    a quiet desktop sits at ONE painted frame indefinitely and a paint-based gate
    would roll back every update nobody was touching. The loop counter carries the
    wait.
  - **`finish()` writes `pending.json` naming the slot it pointed the bootloader
    at**; `boot_check()` compares it against the running slot next boot. **The
    marker is cleared at the CONFIRM, not at the read**, so an image that boots,
    reports and then dies still carries its evidence into the boot after the
    rollback. The verdict surfaces as a notice banner and again on Settings →
    UPDATE.
  - **Two channels, and the channel is a BUILD choice**: STABLE from master,
    UNSTABLE/BETA from dev, stamped into a gitignored `modules/_ota_build.py` from
    `MOYBYTE_OTA_CHANNEL` — clean across merges, never a per-branch source edit.
    An install is offered when the manifest's channel DIFFERS from the running one
    (a switch, including beta→stable rollback) **or** is higher WITHIN the channel.
    A card's `ota.json` always WINS over the baked url, which is how a LAN or
    offline host overrides it — so **delete a leftover one before testing the
    real path**, or it silently reroutes every check.
  - **The manifest is SIGNED, and the BOARD is inside the signature** (scheme
    `moybyte-ota-v2`): an OTA payload is an app-partition image, so another
    board's is a valid image that cannot boot, and a manifest naming one is
    refused BY NAME before the signature is even checked. `ssl.wrap_socket` does
    no certificate verification on device, which is *why* the manifest is signed
    rather than trusted for arriving over TLS.
  - **RSA, not Ed25519, purely for the verifier**: `pow(sig, 65537, n)` is a
    handful of modular squarings MicroPython does in C, where pure-Python curve
    arithmetic would take seconds. **Signing needs the `release` extra; verifying
    needs nothing**, which is what lets the security-critical half be tested in
    ordinary CI (`tests/test_ota_signing.py`, `tests/test_moy_ota.py`).
  - **The url and label are deliberately UNSIGNED** so a classroom can mirror the
    official manifest to a LAN host and rewrite the url — the bytes stay pinned
    by the signed hash. **Policy:** a manifest from a BAKED channel url must be
    signed; one reached because the owner put an `ota.json` on the card need not
    be (writing to the card is a physical act of consent, and it keeps the
    key-free LAN dev loop working) — but a signature that IS present is always
    checked, so a tampered official manifest cannot be laundered through a local
    host. A build with no baked key cannot require one.
  - **`OTA_PUBLIC_KEYS` is a TUPLE so a key can be ROTATED** — publish an image
    trusted by the old key and signed by the new.
  - **`ensure_online()` must WAIT for the link** after autoconnect
    (`ONLINE_WAIT_MS`): `DeviceWifi.connect()` polls briefly and gives up, and a
    saved network that comes up just after reads as "wifi offline". The wait
    belongs there, not in `connect()`, which would freeze the desktop on every
    wrong password.
  - **Bump `moy_ota.FIRMWARE_VERSION` only via `make release`** — a hand bump
    desynchronises the stamp CI reads back out of the artifact, and a manifest
    advertising a version the image does not carry offers the same install
    forever.
- **Branches and releases: `dev` is where work lands; `master` is what users get.**
  Commit to `dev` by default — a change is not on master until a human has tested
  it on the boards it touches, and **never push straight to master anything a
  board can run**.
  - **The two branches ARE the two OTA channels.** A push to `dev` builds a beta
    (channel `unstable` → the `firmware-beta` release); a push to `master` builds
    a stable (→ `firmware-latest`, which the site's flasher writes and the stable
    OTA offers). Host CI runs on both; the site republishes only off master.
  - **The merge into master IS the release**, and `make release NAME=0.7` is how:
    clean-tree checks → `make test` → `merge --no-ff dev` → bump
    `FIRMWARE_VERSION` AND set `FIRMWARE_NAME` → commit + tag → **stop**, printing
    the push command. Pushing master is the moment a device somewhere is offered
    the build, so it stays a separate deliberate keystroke.
  - **`NAME` is the release** (`MAJOR.MINOR`; a third component only for a pure fix
    release). It becomes the tag, the manifest `label` and the string on the kid's
    update screen. `FIRMWARE_VERSION` stays an opaque counter nobody reads — it is
    signed as an int and betas stamp a build epoch into it. **Do not hand-bump it.**
    Re-cutting a name already tagged is refused, which is the prompt to pick a fix
    release.
  - Firmware builds are path-filtered pushes and the workflow's per-ref
    `cancel-in-progress` collapses a burst of dev pushes into one build of the last
    commit, so "every push builds" costs one build, not ten.
