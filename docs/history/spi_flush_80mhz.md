# The real-80 MHz SPI flush recipe (T-Deck Plus), distilled from LovyanGFX + esp_lcd

**Status:** research / reference-extraction spike. Output is this doc only — a
recipe to hand to the flush agent. Nothing here edits the live flush path
(`moy_compositor.py` / `tdeck_display.py` are owned by another agent). LovyanGFX is
studied as a **reference** for how the S3 hits a clean 80 MHz DMA flush; we are not
adding it as a dependency. **2026-07-03:** current flush numbers + the lever ledger
live in the **#66 performance ledger** (GitHub issue); this doc remains the wiring
deep-dive.

**TL;DR / verdict (read this first):**

The ~28 ms full-frame flush is **clock-bound at ~40 MHz**, and on the T-Deck's exact
wiring **a real 80 MHz is physically unreachable** — by any config, ours or
LovyanGFX's. The cause is **not** our esp_lcd setup: it is that the display SPI pins
(SCK=40, MOSI=41, MISO=38) are **not** the ESP32-S3 SPI2 **IOMUX-native** pins, so
every transfer is routed through the **GPIO matrix**, which the ESP-IDF SPI master
silently caps at ~40 MHz (80 MHz needs IOMUX-direct pins). We already *request*
80 MHz (`tdeck_display.py` line 36); the silicon refuses it on these pins.

So the recipe splits in two:

- **§1 — what to change on our path to be certain we're at the 40 MHz tier's
  *ceiling*** (verify clock, single-window streaming, max transfer size, byte-swap
  in hardware). These are real, applicable wins that close the gap to the ~30.7 ms
  theoretical floor, and they're the LovyanGFX techniques that *do* transfer.
- **§2 — the 80 MHz lever (IOMUX) and why it's closed on this board.** Documented so
  no one re-spends effort chasing a config that can't exist here. Escapes are a
  hardware-rev concern (P4 / #12).

This also **closes the open question in `docs/history/perf_60fps_architecture.md` §1.3**
("confirm the wiring uses IOMUX pins"): it does not.

---

## 1. Recipe for OUR path (lcd_bus / esp_lcd + the native moy_compositor)

These are the techniques LovyanGFX and the esp_lcd "best practice" examples use for
a clean, full-rate DMA flush, mapped onto what we already have. They will **not**
exceed ~40 MHz on this wiring (see §2) but they make sure we're at that tier's
ceiling with no avoidable overhead.

### 1.1 Confirm the effective clock first (one-time measurement)

Before tuning anything, prove what clock we're actually getting. esp_lcd computes
the real divider via `spi_get_actual_clock(APB, requested, 0)`; on matrix pins a
requested 80 MHz becomes ~40 MHz. Two ways to confirm from our stack:

- Cheap: time a known full-frame flush (we already see ~28 ms). 320×240×16 bits is
  1,228,800 bits; `bits / 28 ms ≈ 43.9 Mbit/s` ⇒ effective clock ≈ 40 MHz +
  overhead. That alone confirms the matrix cap.
- Authoritative: build lcd_bus once with `LCD_DEBUG_PRINT` on (it logs `pclk_hz`),
  or read the SPI clock-divider registers; the divider will be ~2 (80/40), not 1.

If the measured rate were already ~80 Mbit/s we'd be on IOMUX and done — it isn't.

### 1.2 Stream the whole frame inside ONE CASET/RASET window (already done — keep it)

LovyanGFX's fast path arms the address window once and streams all pixels as a
single logical write, re-issuing only `RAMWR`/`RAMWR-continue` between DMA chunks —
never re-sending CASET/RASET per band. Re-arming the window per band injects
command→data turnarounds that stall the pipe and (as our own notes record) glitch
rows at the boundary.

**Our compositor already does this** (`moy_compositor.Compositor.flush`): `_set_window`
once, then `tx_color(RAMWR, …)` for the first band and `RAMWRC` (0x3C) for the rest.
**Keep this exactly.** This is the single most important "LovyanGFX technique" and we
already have it. Do not regress to per-band windowing.

### 1.3 Maximize transfer size / minimize transfer count

LovyanGFX pushes the largest DMA burst the hardware allows (one ~`pushColors` per
flush where possible). Each separate `tx_color` has fixed per-transaction overhead
(queueing, CS, the command phase). Fewer/bigger bands = closer to the bit-clock
floor.

- The compositor's `_flush_rows = 48` (5 bands for 240 rows) is already near
  optimal; the limiter is the per-band DMA bounce fitting **internal** SRAM, not the
  matrix clock. CLAUDE.md's "band count barely matters — same total bytes" is
  correct: at 40 MHz the bytes dominate, so going from 5 bands to 1 saves only the
  ~4 inter-band command gaps (microseconds), not milliseconds.
- The IDF bus is already configured for big transfers:
  `machine_hw_spi.c` sets `max_transfer_sz = SPI_LL_DMA_MAX_BIT_LEN/8` and
  `spi_bus_initialize(..., SPI_DMA_CH_AUTO)`. **No change needed**; do not shrink it.
- **Actionable:** if internal-SRAM headroom allows (watch the "Moybyte mem:" boot
  readout), raising `_flush_rows` toward a single full-frame transfer trims the last
  few command gaps. Marginal at 40 MHz; safe to leave at 48.

### 1.4 Do the RGB565 byte-swap in hardware, not the CPU (already done — keep it)

A CPU byte-swap of 153,600 bytes per frame would add real milliseconds. esp_lcd
(and LovyanGFX) do the swap in the panel-IO layer. Our path already passes
`rgb565_byte_swap=True` to the ST7789 driver (`tdeck_display.py`), so the swap is in
the lcd_bus/esp_lcd path, not Python. **Keep it; never byte-swap the framebuffer in
MicroPython.**

### 1.5 DMA from a stable, DMA-reachable buffer (already done — keep it)

LovyanGFX DMAs from a buffer that won't be mutated mid-transfer. Our compositor
copies the framebuffer once into a dedicated PSRAM DMA frame buffer
(`moy_alloc.malloc_dma(..., MEMORY_SPIRAM | MEMORY_DMA)`) and slices stable,
non-overlapping bands from it, so the async esp_lcd DMA (trans_queue depth 10) never
reads a buffer the next band has clobbered. The S3 can DMA from PSRAM. **Keep this**;
it's why the flush is clean. (This was the fix for the one-band vertical-offset bug.)

### 1.6 Net of §1

Everything LovyanGFX does for a *clean* DMA flush, our path already does (single
window + RAMWRC streaming, hardware byte-swap, stable PSRAM DMA source, max transfer
size, AUTO DMA). The only knob with any remaining headroom is band size (§1.3), and
it's worth single-digit microseconds at 40 MHz. **There is no esp_lcd/lcd_bus config
change that buys a meaningful flush speedup — because the flush is already at the
40 MHz tier's ceiling, and that tier is set by the pins (§2), not the config.**

---

## 2. The 80 MHz lever (IOMUX) — and why it's closed on the T-Deck

### 2.1 How 80 MHz is actually programmed

The SPI clock is `APB(80 MHz) / (pre · n)`. The IDF divider routine
(`hal/esp32s3/include/hal/spi_ll.h`, `spi_ll_master_cal_clock`) fast-paths anything
above 60 MHz to divider 1:1 → 80 MHz:

```c
if (hz > ((fapb / 4) * 3)) {   // > 60 MHz with an 80 MHz APB
    reg.clkdiv_pre = 0;        // pre = 1
    reg.clkcnt_n   = 0;        // n   = 1
    eff_clk = fapb;            // 80 MHz
}
```

We already request this: `tdeck_display.py freq = 80000000` → lcd_bus copies it to
`panel_io_config.pclk_hz` → `esp_lcd_new_panel_io_spi` → `spi_bus_add_device` at
80 MHz. **Programming 80 MHz is not the problem; getting the *pin* to toggle at
80 MHz is.**

### 2.2 IOMUX vs GPIO matrix (the whole ballgame)

- **IOMUX (direct):** dedicated pad, no synchronizer → full **80 MHz**.
- **GPIO matrix (routed):** the crossbar adds a register-sync stage
  (`GPIO_LL_MATRIX_DELAY_NS`); the IDF SPI master then caps usable clock at
  **~40 MHz** (~26 MHz for full-duplex reads).

Espressif's ESP32-S3 SPI Master docs, verbatim:
> "speeds up to 80 MHz on the dedicated SPI pins and 40 MHz on GPIO-matrix-routed
> pins are supported"
> "full-duplex transfers routed over the GPIO matrix only support speeds up to
> 26 MHz"
> "when an SPI Host is set to 40 MHz or lower … routing SPI pins via the GPIO matrix
> will behave the same compared to routing them via IOMUX"

The IDF picks IOMUX-vs-matrix **purely by comparing your pin numbers to the host's
IOMUX-native pins** (`esp_driver_spi/src/gpspi/spi_common.c`,
`check_iomux_pins_quad`): if `sclk_io_num != spiclk_iomux_pin` for *any* line it
clears `SPICOMMON_BUSFLAG_IOMUX_PINS`, sets `use_gpio = 1`, and routes through the
matrix — no warning, silently halving the clock. **This is our ~28 ms.** LovyanGFX
is bound by the identical check on the identical silicon; it has no escape we lack.

### 2.3 The S3 SPI2 IOMUX-native pins (the only gold set)

From `components/soc/esp32s3/include/soc/spi_pins.h`:

| SPI2 line | IOMUX-native GPIO |
|-----------|-------------------|
| CLK       | **12** |
| MOSI (D)  | **11** |
| MISO (Q)  | **13** |
| CS        | **10** |
| HD / WP   | 9 / 14 |

(SPI3 on the S3 has **no** IOMUX pins — `//SPI3 have no iomux pins` — so SPI3 is
always matrix-routed and can never hit 80 MHz. SPI2 is the only candidate host, and
we already use it.)

### 2.4 Cross-check vs the T-Deck wiring → unreachable

T-Deck display pins (`tdeck_display.py` / `tdeck_board.py`):

| Role | T-Deck GPIO | IOMUX-native for SPI2? |
|------|-------------|------------------------|
| host | 1 (SPI2_HOST) | correct host |
| SCK  | **40** | ✗ (IOMUX CLK = 12) → matrix |
| MOSI | **41** | ✗ (IOMUX MOSI = 11) → matrix |
| MISO | **38** | ✗ (IOMUX MISO = 13) → matrix |
| DC   | 11 | plain GPIO (not an SPI data line) |
| CS   | 12 | via esp_lcd, not clock-critical |

**Not one clock/data line is IOMUX-native.** Cruel irony: GPIO 11 and 12 — the
IOMUX MOSI/CLK pins — are wired on the T-Deck to **DC and panel CS**, not to the
data/clock lines. The SCK/MOSI/MISO routing to 40/41/38 is fixed copper. **A real
80 MHz is physically unreachable on this wiring, by our config or LovyanGFX's.**

(320×240×16 bits / 40 MHz ≈ 30.7 ms theoretical floor; plus command/window overhead
≈ the ~28–31 ms measured. Consistent.)

### 2.5 Levers that do NOT help (so nobody re-tries them)

- `max_transfer_sz` / DMA channel: already maxed (`SPI_LL_DMA_MAX_BIT_LEN/8`,
  `SPI_DMA_CH_AUTO`); irrelevant to per-bit clock.
- Bigger bands / one transfer: trims command gaps (µs), not the bit-clock floor.
- `input_delay_ns` / dummy bits: a *read*-side (MISO) timing fix; a full-frame flush
  is write-only, so it can't raise our ceiling.
- Quad/octal SPI: the T-Deck ST7789 is single-data-line; no D1–D3 copper. N/A.
- APB > 80 MHz: the SPI source clock is the 80 MHz APB on this part; can't overclock
  past it for SPI.

---

## 3. What to do instead (the only lever left)

The flush is a fixed ~28–31 ms on this wiring. Don't chase a faster clock — there
isn't one. The path forward is the one `docs/history/perf_60fps_architecture.md` already
lays out, which this spike confirms is the only remaining lever:

1. **Hide the flush behind render** with async double-buffered DMA → frame period
   becomes `max(render, flush)`, not `render + flush`.
2. **Shrink internal resolution** (160×120 → 2× point-double on flush): 4× less
   render *and* 4× less flush data (~7–8 ms at the 40 MHz tier), comfortably under
   the 16.6 ms / 60 fps budget.

Neither needs a faster SPI clock.

A genuine 80 MHz / ~15 ms flush would require a **hardware rev** that wires the panel
SPI to GPIO 11/12/13 (the SPI2 IOMUX pins) — a P4 / #12 conversation, not a software
fix.

---

## Sources

- ESP-IDF SPI Master driver (ESP32-S3), IOMUX vs GPIO-matrix max frequencies:
  <https://docs.espressif.com/projects/esp-idf/en/stable/esp32s3/api-reference/peripherals/spi_master.html>
- In-tree IDF (verified): `components/soc/esp32s3/include/soc/spi_pins.h`
  (SPI2 IOMUX pins); `esp_driver_spi/src/gpspi/spi_common.c`
  (`check_iomux_pins_quad`, the IOMUX-vs-matrix decision);
  `hal/esp32s3/include/hal/spi_ll.h` (`spi_ll_master_cal_clock`, the divider);
  `hal/spi_hal_iram.c` (`spi_hal_get_freq_limit`, the matrix delay cap);
  `micropy_updates/esp32/machine_hw_spi.c` (`max_transfer_sz`, `SPI_DMA_CH_AUTO`);
  `ext_mod/lcd_bus/esp32_src/spi_bus.c` (`pclk_hz` → `esp_lcd_new_panel_io_spi`).
- LovyanGFX as the reference for the clean-DMA-flush techniques (single-window
  streaming, hardware byte-swap, max DMA burst, 80 MHz on IOMUX pins):
  <https://github.com/lovyan03/LovyanGFX> (ST7789/ESP32-S3 config example).
- Repo context: `docs/history/perf_60fps_architecture.md` §1.2–§1.3 (flush budget + the
  80 MHz caveat this spike resolves); `firmware/.../modules/moy_compositor.py`
  (the banded single-window `tx_color` flush this recipe validates).
