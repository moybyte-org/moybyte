# T-Deck on mainline MicroPython (no LVGL, no fork)

This is the LilyGO T-Deck Plus built **the way the P4 is built**: mainline
MicroPython + an out-of-tree board definition + `USER_C_MODULES`. It exists so
the project has ONE build strategy instead of two.

It does **not** replace `firmware/lilygo_t_deck_plus_micropython/` yet. That
target is what ships; nothing here touches it. This tree reads two things from
it and writes nothing: the shared native modules under `native/` (single source
of truth stays there) and two board-agnostic `.patch` files.

**Stage 1 — panel bring-up — was VERIFIED ON GLASS 2026-08-16.** Flashed to the
owner's T-Deck and confirmed by eye: the eight colour bars and the checker came
up on the first flash, no MADCTL adjustment needed. Read back over the REPL on
the running board:

    MicroPython v1.28.0 (MAINLINE, not the fork)   lvgl: absent
    PANEL 320 x 240   byteswap True   MADCTL 0x68
    full-screen bars() redraw + flush: 4811 us
    image 1,704,976 B of the 5,242,880 B ota_0 slot

So the ST7789 runs on mainline with no LVGL anywhere, which is the question that
stage existed to answer. **Stages 2-6 are written and compile; none of them has
been on glass.** Each stage is one commit, and the commit message says what to
look for -- flash the last good one to bisect.

Stage 2 also settles where the module list lives: `board.toml` + `tools/board_config.py`,
the same declaration the other two boards use, and the whole shared console
stages from here on. That is deliberate and it is explained in the file: the
stage commits are bisect points, so consecutive images should differ by the
subsystem under test and not also by a megabyte of frozen bytecode.

---

## Build and flash

```bash
# build (first run clones micropython v1.28.0 into .build/, ~4 min; warm ~40s)
./firmware/lilygo_t_deck_plus_mainline/build.sh

# put the board in the ROM loader BY HAND -- see below -- then:
make firmware-flash-tdeck-mainline PORT=/dev/ttyACM0
make firmware-monitor-tdeck-mainline PORT=/dev/ttyACM0
```

**There is no BOOT button on a T-Deck.** The trackball CLICK is GPIO0: hold the
trackball pressed in while powering the board on, then release. esptool's
auto-reset does not sync over this board's native USB, which is why the flash
target uses `--before no_reset`.

Outputs land in `dist/tdeck_mainline/`:

| file | what it is | where it goes |
|---|---|---|
| `moybyte_tdeck.bin` | bootloader + partition table + app, merged | cable flash at **0x0** (the S3's bootloader offset; the P4's is 0x2000) |
| `moybyte_tdeck_app.bin` | the app partition alone | what an OTA writes into the inactive slot |

The merged image leaves `otadata` (0x1D000) erased, so the bootloader falls back
to `ota_0` — the same effect the fork target gets by erasing that region
explicitly. The flash target erases it anyway, because a board that has taken an
OTA is running `ota_1` and would otherwise boot the stale slot and look like the
flash did nothing.

### Which mode an image boots

`modules/moybyte_shell.py` carries one `MODE` string. Every mode except
`"desktop"` is **self-terminating** — it paints, prints and returns to the REPL
rather than taking the loop over, so a bring-up program never spends a REPL the
owner might still have had. From a live REPL any of them can be re-run without
a reflash:

```python
import tdeck_smoke
tdeck_smoke.touch()                       # or panel() / keyboard() / sd() / audio()
```

#### `MODE = "panel"` (stage 1)

Expected on the glass: eight vertical colour bars — white, yellow, cyan, green,
magenta, red, blue, black, left to right — over the top three quarters, and a
16px white/black checker across the bottom quarter.

```
Moybyte T-Deck (mainline) boot
Moybyte T-Deck (mainline) shell starting -- mode=panel
Moybyte panel: init
Moybyte panel: 320x240 nfbs=2 madctl=0x68 gfx=True
Moybyte panel: backlight ON -- expect 8 colour bars over a checker
Moybyte panel: flushes=8 last=NNNNNus (NN.N fps ceiling)
Moybyte panel smoke done -> REPL
```

| what you see | what it means |
|---|---|
| nothing on serial at all | not the panel — bootloader/partition/flash-mode |
| serial runs, screen stays dark | backlight (GPIO42) or the board power rail (GPIO10) |
| screen lights, shows noise | init sequence went out but `show()` did not land |
| bars appear, but rotated / mirrored | MADCTL. `moy_lcd.set_madctl(0x28)` / `0xA8` / `0xE8` at the REPL — no rebuild needed |
| bars appear with wrong colours | red/blue swapped = the BGR bit; washed out = a gamma command was rejected |
| rows sheared diagonally | stride — a `WIDTH`/`row_bytes` mistake |
| a seam every 48 rows | the flush banding — a continuation band sent a command |
| flicker or tearing | the ping-pong; try `TDeckCompositor(nfbs=1)` |
| `flush timed out` | `on_color_trans_done` never fired — the completion fence, not the panel |

#### `MODE = "touch"` (stage 2)

Five yellow boxes — four corners and the centre — on a dark field, with
`TOUCH THE BOXES` across the top. Touch one: a crosshair follows the finger
(red on the press frame, green while held) and the bottom line reads
`map=<canvas coords> raw=<straight off the GT911>`. Runs 60s, then returns.

```
Moybyte touch: available=1 addr=0x5d int_pin=16 gate=on
Moybyte touch: map knobs swap=False flip_x=False flip_y=True raw=320x240
TAP 1 map=(26,26) raw=(26,213)
Moybyte touch: reads=NN max=N.Nms over5=N over20=N int_edges=NN skipped=NN
```

This is also the first pass through `DeviceCanvas` on this build, so a screen
that draws at all proves the RGB565 raster, the `moy_gfx` kernel and the
petme128 text kernel as a side effect.

| what you see | what it means |
|---|---|
| `available=0` / `GT911 not found on I2C0` | the controller did not answer at 0x5D or 0x14 on I2C0 (SCL 8 / SDA 18) — wiring or the I2C bus, not the mapping |
| taps land in the WRONG box | the mapping. `raw=` climbing while `map=` falls names the flipped axis: set `device_input.TOUCH_FLIP_X` / `TOUCH_FLIP_Y` / `TOUCH_SWAP` from the REPL and re-run `tdeck_smoke.touch()` — module globals, no rebuild |
| crosshair lags or sticks | `over20` on the I2CSTAT line. The GT911 clock-stretches 20-45ms on most finger-down reads (#74); this smoke is single-threaded, which is exactly the cost stage 3's poller thread removes |
| `gate=OFF (blind polling)` | the INT pin (GPIO16) could not be claimed. Touch still works; it just spends bus time on every pass |

---

## What is here

```
boards/MOYBYTE_TDECK/   out-of-tree board definition + the OTA partition table
board.toml              WHICH modules cross, and why (tools/board_config.py)
native/moy_lcd/         the ST7789 SPI panel backend (this board's moy_dsi)
native/.staged/         shared native modules, copied from the fork tree (gitignored)
modules/                board-authored: boot/main/moybyte_shell/tdeck_panel/tdeck_smoke
                        + everything board.toml stages (gitignored)
build.sh                clone -> patch -> stage -> freeze -> build -> collect
```

### `board.toml` — where the module list lives

`build.sh` contains no `cp runtime/*.py` line and must never grow one. What
crosses is declared in `board.toml` and staged by `tools/board_config.py`, the
same mechanism the other two boards use, from two sources with two deliberately
different strategies:

| source | strategy | why |
|---|---|---|
| `runtime/` | **denylist** | a shared tree's default answer is "yes, this crosses", so what needs writing down is the exclusions, each with a reason. Identical to the fork build's list — same board, same 320×240 tier. |
| the fork build's `modules/` | **allowlist** | a board tree's default answer is "no". Three of its modules drive `lcd_bus`/`lvgl` and two are that build's own boot spine, which would *shadow* this one's. |

The stager also prunes: the frozen manifest freezes the whole `modules/`
directory, and that directory is gitignored, so an unstaged module would
otherwise stay in the image forever. A **tracked** file there is board-authored
and never pruned — which is why a new board module has to be whitelisted in
`.gitignore` as well.

### `native/moy_lcd` — why a panel module at all

The P4 scans a PSRAM framebuffer continuously (MIPI-DSI, DPI mode, no per-frame
transfer). The T-Deck has to PUSH 320×240 RGB565 down SPI every frame, so the
work LVGL and `lcd_bus` used to do has to live somewhere. It lives in one 500-line
C file, and three things in it are load-bearing:

**IDF's own ST7789 driver is not enough.** `esp_lcd_panel_init()` sends exactly
`SLPOUT` / `MADCTL` / `COLMOD`, and `esp_lcd_new_panel_st7789()` has no
`init_cmds` / `vendor_config` hook to extend it. Porch, gate/VCOM/power control,
the two 14-byte gamma curves and `INVON` therefore go out as our own
`esp_lcd_panel_io_tx_param()` calls afterwards, with `DISPON` via
`esp_lcd_panel_disp_on_off()`. The register values are the ones already proven on
this glass, transcribed from the fork's `_st7789_init.py` (MIT — see
THIRD_PARTY.md); its `import lvgl` was only for orientation constants, resolved
here into one MADCTL byte.

**The panel DMA only ever reads internal SRAM.** The frame ships as 48-row bands
memcpy'd PSRAM → one of two internal DMA bounce buffers. That is the #66 design
and it is here from the start: heavy PSRAM traffic during a PSRAM-sourced
transfer starves the SPI FIFO and clocks out garbage rows, and a PSRAM-direct
transfer would additionally need the `spi_master` patch the fork build carries.

**Only the first band carries a command.** Bands 2..N go out with `lcd_cmd = -1`
— no command phase at all — into a window armed once with CASET/RASET. This is
what "a full-screen flush must be a single `tx_color`" is really about: it is
re-issuing a command mid-stream that glitches rows at the command→data boundary,
and esp_lcd blocks on a drained queue before any command.

### `modules/tdeck_panel.py` — the compositor

`TDeckCompositor` implements the same small interface `DeviceCanvas.__init__`
and `moy_runtime.run_desktop` already call — `size` / `framebuffer` /
`back_buffer` / `gfx` / `flush` / `sync` — the one `p4_display.P4Compositor` and
`moy_compositor.Compositor` both implement. No new seam is invented
(`docs/backend_contract_v1.md` L8: strategy stays the backend's).

**The flush is blocking**, so `sync()` is a no-op and there is no
`pump_if_pending` (DeviceCanvas looks that one up with `getattr` and degrades
cleanly; the P4 has none either). The fork's overlap — async completion callback,
soft-timer pump, draw-verb poke — is a real ~2× on this board and is the first
thing to port once the console boots. It is a performance lever layered on a
working panel, not part of proving the panel works.

---

## The three fork patches, re-solved

`build.sh` applies them marker-guarded, so a warm `.build` re-patches nothing.

| patch | how it is solved here | which stage needs it |
|---|---|---|
| **REPR_C unboxed floats** (#66) | guarded `sed` on MicroPython's own `mpconfigport.h` (in the cloned upstream under `.build/`, not a file of ours) — its `MICROPY_OBJ_REPR` line, and the build **fails loudly** if the line has changed shape. A sed rather than the fork's context diff, so it survives the line moving between MicroPython releases. Verified in the built tree. | None strictly — but it is free, it changes object layout so it must be settled early, and every image here was compiled and linked with it |
| **I2C GIL release** (#69) | a small in-place Python edit that brackets `i2c_master_cmd_begin` with `MP_THREAD_GIL_EXIT/ENTER`. Result is byte-identical to the fork's patched file. Exits non-zero if the call site is not found. | **Stage 3.** It is what makes the poller THREAD worth having: without it a C3 clock-stretch stall holds the GIL and freezes the whole VM no matter which thread issued the read. Stage 2 reads I2C on the main thread and cannot tell the difference |
| **esp_lcd `tx_color` no-acquire** (#66) | the fork's `.patch` file, applied to the ESP-IDF tree (idempotent; the shared checkout usually has it already from the fork build) | **None yet.** `moy_lcd.show()` fences on its own completion counter rather than relying on `spi_device_acquire_bus` happening to serialize the bands, so without the patch the flush is merely serialized. The patch is what lets it *overlap* later |

A fourth rides along, the same one the P4 build reuses: **native-code-free**
(#66), which lets a cart-compile miss reclaim the `@micropython.native` exec
arena instead of growing it until soft reset.

---

## What the fork tunes that this build does not (yet)

Deliberate. Each is a lever with a recorded verdict, and each should be turned on
with an A/B rather than inherited.

| lever | fork | here | why |
|---|---|---|---|
| cache geometry (#63) | 32KB icache / 64KB dcache / 32B line | **same** | pure win, already proven on this board; costs 48KB internal SRAM |
| flash + PSRAM at 120MHz (#66/#169) | on, plus a vendor-gate patch | **off** (80/80) | an EXPERIMENTAL IDF feature whose failure mode is random faults ~20 °C from boot temperature. It needs the retune patch to be safe, and neither belongs in a bring-up |
| `-O3` on moy_gfx (#77) | on (Brick Siege 33→51 fps) | inherited | it is a pragma inside the shared `moy_gfx` source, so it comes with the staged module |
| async flush + pump (#40/#43/#66) | on | **off** | see the compositor note above |
| PSRAM-direct DMA (`spi_master` patch, #43) | on | **off** | the SRAM-bounce path makes it unnecessary and it is the riskier of the two |

---

## Hard constraints this port inherits

Every one of these was learned by hanging or bricking a board. `CLAUDE.md` is
the authority; what follows is how they land in *this* tree.

- **SD shares SPI2 with the panel.** `moy_lcd.init()` runs `spi_bus_initialize()`
  once and never tears it down. Nothing may touch SD before it; the SD card
  attaches to the already-initialised host through `moy_sd`
  (`sdspi_host_init_device`, no bus re-init), never `machine.SDCard`; no SD
  device may be torn down between ops; and no panel flush may overlap an SD
  session — which is why `sync()` must exist even though it is a no-op today.
- **Do not re-create a `Pin` on `TFT_CS` (12) or `SD_CS` (39) once a driver owns
  them.** `moy_lcd.init()` parks them high *before* the bus exists, which is what
  the fork's `tdeck_board.init_board_pins` does; it is reconfiguring them
  afterwards that corrupts the shared bus and silent-hangs the next flush.
- **80 MHz SPI is requested, not delivered.** None of MOSI(41)/SCK(40)/MISO(38)
  are the S3's IOMUX-native FSPI pins, so everything routes through the GPIO
  matrix, which caps a write-only LCD at ~40 MHz. The board's wiring is the wall.
- **The keyboard has two modes** (ASCII vs raw matrix, `0x03`/`0x04` over I2C
  0x55) and the console flips between them per screen. That arrives with stage 3.
- **Serial TX works during play; RX did not, under the fork.** Do not build an
  in-loop serial command channel on this board without first proving RX arrives.

---

## Stages

Each is one commit. The commit message says what it adds and what to look for
on glass, so a misbehaviour can be bisected by flashing the last good one.

1. **Panel.** Mainline boots, ST7789 comes up, a test pattern lands.
   **On glass 2026-08-16.**
2. **Touch** (GT911, I2C0 @ 0x5D/0x14), plus `board.toml` and the whole shared
   module tree. `MODE = "touch"`.
3. **Keyboard** (ESP32-C3 @ I2C0 0x55, both modes) and the #69 poller thread.
   `MODE = "keyboard"`.
4. **SD** (`moy_sd` attach on the live host — the dangerous one).
   `MODE = "sd"`.
5. **Audio** (I2S / MAX98357 via `moy_audio`). `MODE = "audio"`.
6. **The shared console**: `moy_runtime.run_desktop` over `runtime/device_boot.py`,
   Lua carts, OTA, the web console blob. `MODE = "desktop"`.
