# Board ports: the N-board architecture (2026-08)

The standing direction doc for adding boards, written the week the fork died
and the two-board build system collapsed into one strategy (#161 closed
2026-08-17). It exists because the next two boards are named and the cost
curve #161 predicted ("the cost curve is in the number of boards") is about to
be walked: **a port today costs ~400 lines of copied frame loop plus
hand-written Makefile/CI plumbing**, and that is the remaining bill after
everything data-driven already landed.

Status lives in the tracker issue (see below), not here. What IS here: the
lineup, the measured per-board bill, the phases, and the declines.

## The lineup, and why

| board | class | why |
|---|---|---|
| **Guition JC1060P470-class (P4 + C6)** | desktop tier | the COST path: ~$33–37 vs the Waveshare 7B's ~$80 (bom_pricing_2026-07: the $100 retail floor "needs the Guition board as the electronics core"). Same architecture class as the shipped P4 port — P4 + C6-over-SDIO + 1024×600 DSI touch — so this is a **variant port**, and its job is to prove variants are cheap. The 2026-06 evaluation (#12) was closed by buying the Waveshare instead; the reason to want the Guition (price) never went away. |
| **Guition JC3248W535 (S3)** | handheld tier | a ~$15-class 3.5" 320×480 S3 smart display. Same chip as the T-Deck, but a **new port class** on every other axis: QSPI panel (AXS15231B — not `moy_lcd`'s ST7789-over-SPI), touch on the same chip (no keyboard, no trackball), and — owner call at bring-up, 2026-08-18 — LANDSCAPE 480×320 off a portrait-native panel whose MADCTL MV is dead on this glass, so `moy_axs` rotates in its band copy. Its job is to prove the contracts, not the copies. |

A working hardware definition for the JC3248W535 already exists at
`~/Documents/Work/esphome/JC3248W535.yaml` (ESPHome, on the physical board):
QSPI clk GPIO47 / data [21, 48, 40, 39] / cs 45 @ 40MHz, AXS15231 touch on
I2C sda 4 / scl 8 (calibrated: swap_xy + mirror_y in landscape), backlight
PWM GPIO1, battery ADC GPIO5, 16MB flash DIO, octal PSRAM @ 80MHz. Treat its
pins as verified and its *tuning* as untested here — it runs a 64B cache line,
and this repo's T-Deck learned on glass that 64B lines break the CPU↔GDMA
handoff in OUR flush path (sdkconfig.board's cache note). Per-board verdicts
don't transfer; A/B it.

## What a port costs today (measured 2026-08-17)

| piece | cost | state |
|---|---|---|
| `boards/<B>/` (sdkconfig, cmake, partitions) | per-chip facts + learned prose | irreducible, and good — this is where constraints live |
| `board.toml` (modules + native, denials with whys) | copy + edit | solved (#161) |
| `build.sh` | ~40 lib calls + the board's patch ladder | solved (`tools/esp32_build_lib.sh`) |
| panel backend (native C) | 800+ lines | irreducible unless the panel repeats |
| input drivers | GT911 already exists twice | Phase C |
| **`modules/moy_runtime.py`** | **~300–460 lines: run_desktop + frame loop** | **Phase B — the big one** |
| `boot.py` / `main.py` / `moybyte_shell.py` | near-twins (boot.py differs by one string) | Phase B rides along |
| Makefile targets | hand-written per board | Phase A |
| CI legs + cache keys | hand-written per board | Phase A |
| test tables (NATIVE/HOST_ONLY/WIRING…) | one row per board | deliberate tripwires — keep |
| on-glass suite | cheap since the shared DevChannel | every board gets one at stage 6 |

## The phases

**Phase A — plumbing as data.** `[flash]`/`[monitor]` sections in `board.toml`
(chip, image offset, baud, otadata offset/size, flash mode), Makefile pattern
rules over the board list, CI matrix legs + cache keys derived from the same
list. Both Guitions flash like their siblings (S3 at 0x0 DIO, P4 at 0x2000),
so this pays twice on arrival. Safe, mechanical, testable
(`tests/test_board_toml.py` grows the same both-halves checks the staging got).

**Phase B — the frame-loop spine.** Extract the loop's INVARIANT ORDER into
shared code (a `FrameLoop` beside `device_boot`'s `FramePump`): begin → input
sources → dev channel → idle tick (after EVERY input source — the wake-swallow
rule) → pointer → present hooks → `ws.frame` → tail → pace. Boards supply
`poll_input()` / `present()` / `tail()` hooks and keep their hardware.

This REVISITS a #161 decline, deliberately. "The frame loop's middle, where
the hardware genuinely differs" was true when written; the 2026-08-17
dev-channel + IdleBlank unification then deleted a third of both middles, and
what remains per-board is an enumerable hook set. And the risk that made the
decline right — no way to verify a migration on the T-Deck — is gone: both
boards carry on-glass suites now, and they are the migration gate (**both
suites green on flashed hardware, or the extraction reverts**). The order
itself is the payload: #56 (SD-before-display), the idle-after-inputs rule and
present-before-sync_back are all order bugs, and order that lives in one
shared file is order a new board cannot re-discover on glass. PURR's F13 —
quoted in #161 — is the same lesson from a 12-board OS.

Without Phase B the Guition P4's `moy_runtime.py` is a ~95% copy of the
Waveshare's 460 lines — a twin factory opened the same month the last twins
were closed.

**Phase C — drivers promoted on demand, never ahead of a second consumer.**
The rule: a driver moves from a board tree to the shared `device/` (Python) or
`native/` (C) the day a SECOND board carries the hardware, parameterized by
`board.toml` data — and not one day earlier.
  * **GT911**: the second consumer likely arrives with the Guition P4 (to
    confirm at bring-up). `device/device_input.py`'s core + `p4_input.py`'s
    calibration become one driver with per-board (addr, addrsize, flips, size).
  * **AXS15231 touch**: lands directly as a shared `device/` driver — it is
    new code, so it starts in the right place.
  * **The QSPI panel**: `moy_lcd`'s VALUE is not the ST7789 init table — it is
    the band/bounce/kick-pump-drain machinery and the hard-won DMA rules
    compiled into it. Whether the AXS15231B backend shares that C core
    (parameterized io layer + init table) or stands alone was a bring-up
    decision to make ON HARDWARE, not in this doc. **ANSWERED 2026-08-18: it
    STANDS ALONE** — `native/moy_axs`, raw `spi_master`, because the whole
    frame ships under ONE CS assertion and no io-layer parameter over
    `moy_lcd`'s body expresses that. The DESIGN crossed verbatim (bands, the
    two internal-SRAM bounce slots, kick/pump/drain, DMA only from internal
    SRAM) and the code did not — which is the shape Phase C's rule wants, and
    the reason the copy is cheap to keep honest.

**Phase D — the port checklist** (below). A checklist, not a generator —
three data points before any codegen. Drafted from the T-Deck mainline port
(the most recent board to walk all six stages, 2026-08-16) after Phases A–C
landed; the first Guition port validates it and corrects it in place.

## The port checklist (Phase D)

Every stage is a **flashable bisect point**: consecutive images differ by the
thing under test. Every bring-up program before stage 6 is **self-terminating**
(paints, prints a number, returns to the REPL — never spends a REPL the owner
might still have had).

**Stage 0 — the skeleton.** `firmware/<board>/` with: `board.toml` (start from
the sibling of your tier; write `[board]` chip/ota/tier + tier_why, `[flash]`
`[monitor]`, the `[modules]`/`[native]` denials WITH reasons — the whole
console stages from here on, deliberately: images must differ by the subsystem
under test, not by a megabyte of frozen bytecode), `boards/<BOARD>/`
(mpconfigboard.cmake/.h, `sdkconfig.board` with the prose beside every value,
the OTA-shaped partitions CSV), `build.sh` (source
`tools/esp32_build_lib.sh`; write only the board's patch ladder + sdkconfig
guard list), a `native/micropython.cmake` including the board modules +
`.staged/micropython.cmake`, and `modules/` with `boot.py`/`main.py`/
`moybyte_shell.py` (MODE string, self-terminating smokes) + `moy_runtime.py`
(run_desktop). Add the Makefile build target and the CI matrix row. Run
`make test`: the staging-closure/board-toml suites must pass before any
hardware exists.

**Stage 1 — panel.** The board's compositor (implementing
`docs/surface_model_v1.md` §4 — size/framebuffer/gfx/flush/sync) over its
native panel module. Smoke: colour bars + checker, printed flush µs. The
MADCTL / byte-order / rotation table in the T-Deck README's stage-1 section is
the debug map. Decide the panel driver's share-or-stand-alone question ON THIS
GLASS (moy_lcd's band/bounce machinery vs. a new io layer).

**Stage 2 — touch.** Start from `device/gt911.py` if the part matches (the
byte-order caveat: read the dump, never assume); calibrate with a
corner-target smoke; bake the swap/flip knobs with the calibration date. A new
part's driver starts IN `device/` (shared) with board params.

**Stage 3 — input beyond touch** (keyboard/trackball/BLE), if any. This is
where the poller-thread question lives on a bus-contended board.

**Stage 4 — storage.** SD or internal VFS; the store root must not shadow a
frozen module name (`/moy/carts`, never `/moybyte/...`).

**Stage 5 — audio**, if wired (the moy_audio usermod + a device_audio
constructor carrying the board's pins).

**Stage 6 — the console.** run_desktop = construct compositor/canvas/inputs,
`DeviceBoot` → `wire_workstation_core` → services (`ws.updater`/`ws.webhost`)
→ `IdleBlank` + `DevChannel` (+ board extras via its `extra`/`env` hooks) →
the board's `poll_inputs`/`present`/`tail`/`account` hooks + the shared
`device_boot.FrameLoop`. Exit criteria, all three: the desktop on glass;
`make test` green; **the board's on-glass suite exists and passes** (copy
`tests/test_tdeck_on_glass.py`'s shape — attach or reset per the board's USB
anatomy, assert against `state`, leave the console where you found it). OTA
needs no extra step: the board id is in board.toml and the manifest publisher
follows the CI matrix row.

## What the Guition S3 specifically stresses (and the P4 doesn't)

* **Input without a keyboard.** `moybyte/input.py` is the T-Deck keyboard
  matrix + InputState fused; a touch-only board needs the InputState core
  separable from the keyboard driver. Do this split AS the port needs it.
* **A third system resolution on the fullscreen tier.** 480×320 landscape
  (owner call 2026-08-18, off the portrait-native 320×480 glass): system UI
  responsive at native res (#39 — closed, the machinery exists), the game a
  fixed 320×240 composited 1:1. First fullscreen-tier board where system
  canvas ≠ game canvas — the seam the P4 runs windowed, run fullscreen.
  **It carried**: the existing `SystemCanvas` + `wm.composite_game`, zero new
  shared code, pinned by `tests/test_guition_on_glass.py`. What did NOT carry
  is chrome legibility at this PPI — a font_scale 2 build was made and
  REVERTED same-day on owner verdict (2026-08-19); the real fix is a PPI floor
  on bar icons and menu rows, independent of font scale, deferred in #202.
* **Backlight as PWM** (GPIO1) — `set_backlight` grows a duty, or stays
  binary; owner call at bring-up. **ANSWERED 2026-08-18: binary.** Every caller
  in the console — the boot light-up fence, `IdleBlank`, the dev channel's
  `bl` — asks for on/off, so the duty stays unbuilt until something wants it.

## Declined (recorded so it is not re-litigated)

* **A driver registry / ABI / swappable UI backends** — PURR's own F14 is the
  bill (17 backend-macro call sites, icons silently gone on three apps).
  #161's verdict stands: take the board file, leave the ABI.
* **Full sdkconfig codegen** — the option lists are data fed to the shared
  guard; `sdkconfig.board` stays the store, with the learned prose beside each
  value, exactly as the PURR board-file lesson wants.
* **A board scaffold generator** — before three ports have walked the
  checklist, a generator is a guess about what varies.
* Everything here assumes ESP-IDF. `esp32_build_lib.sh` is IDF-specific by
  name and on purpose; a non-Espressif board would be a second lib, and none
  is planned.
