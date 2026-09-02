# Moybyte on the Guition JC3248W535 (ESP32-S3, 3.5" 320x480)

The third board and the first provisioned through the #202 port kit
(`docs/board_ports_2026-08.md` -- read its checklist before touching this
port; #202 is the living status). A ~$15-class smart display: same chip as
the T-Deck, a **new port class** on every other axis -- QSPI panel
(AXS15231B, not an ST7789 over plain SPI), touch on the same controller
(no keyboard, no trackball), and a 480x320 LANDSCAPE console on the
fullscreen tier, rotated out of the portrait-native glass in the panel
driver's band copy.

## Hardware facts (pin provenance: the owner's working ESPHome definition,
`~/Documents/Work/esphome/JC3248W535.yaml` -- pins verified on the physical
board; tuning deliberately not copied, see `boards/.../sdkconfig.board`)

| subsystem | facts |
|---|---|
| panel | AXS15231B, 320x480 portrait-native, QSPI @ 40MHz: CLK 47, D0 21, D1 48, D2 40, D3 39, CS 45. No reset GPIO. MADCTL MV is DEAD on this glass (tested 0x60 + 0x20 live, both scramble; Arduino_GFX writes the bit, the LVGL-forum reports match ours) -- the console runs LANDSCAPE 480x320 via the rotate in moy_axs's band copy (owner call 2026-08-18). |
| touch | AXS15231 (same bridge), I2C0 SDA 4 / SCL 8, addr 0x3B. Raw coords are portrait panel coords (driver: `device/axs_touch.py`). |
| backlight | GPIO1, active high, PWM-capable (binary on/off for now -- owner call). |
| battery | ADC GPIO5, divider ~1.72x (unwired here yet). |
| flash/PSRAM | 16MB DIO; octal PSRAM. BOTH at 120MHz since 2026-08-19 (the T-Deck's experimental MSPI profile, A/B'd on this glass: carts +25-29%, pump -23%, SPI starvation -77%; needs the #169 retune patch, applied by build.sh). |
| SD | TF slot VERIFIED on glass (2026-08-20): SPI3, CS 10 / MOSI 11 / SCLK 12 / MISO 13 (f1atb's field guide, confirmed by mount). **A card, when present, IS the cart store** (`/sd/carts`, T-Deck model, seeds on first boot); no card degrades to the internal VFS (`/moy/carts`). THE TRAP: `machine.SDCard`'s SPI slot numbers map INVERTED to host numbers -- **slot=2 is SPI3, slot=3 is SPI2 (the panel's bus)**; slot=3 dies with ESP_ERR_INVALID_STATE before touching the card, and the failed constructor leaks the sdspi singleton so every later probe fails the same way until a reboot. `moy_runtime.SD_STATUS` carries the mount verdict for the dev channel (boot output is DROPPED until a serial host attaches, #201). OTA staging deliberately stays on the internal VFS (a pulled card must not kill an update). |
| audio | speaker header exists; amp/pins UNVERIFIED -- stage 5 is open. |
| USB | the S3's native USB-Serial/JTAG (303a:1001). Console primary per #201, so serial RX works under the desktop. |

## The panel path

**The game fold + the game window** (2026-08-19, #190's cousin, then the
owner's bezel insight the same evening): on a play frame the game composite
never touches the root framebuffer -- `DeviceCanvas.blit_game`'s existing
#190 plumbing arms `moy_axs`, whose flush synthesizes every band from the
scratch snapshot directly. **Any integer scale since 2026-09** (the latch and
both gathers moved into the shared `native/moy_flush/moy_fold` when the T-Deck
took the lever): the C used to fold scale 1 only, so a cart-declared small
canvas -- a 128px PICO-8 port at 2x on this 480x320 glass -- fell back to a
full-root composite on the CPU every frame, which is the case the port exists
for. And because the bezels never change while the
panel's GRAM persists, only the FIRST folded flush ships full-screen (laying
the bezels); every steady play frame after it arms CASET/RASET to the game's
physical rectangle (240x320, 8-aligned) and ships the game alone -- the
T-Deck's exact payload at a quarter of this bus's full-frame time. Proven
byte-identical to the composite path on the device itself
(`moy_axs.fold_test`, both passes, 0 mismatched bytes); `fold_stats`'
windowed counter tracks folded flushes 1:1 minus the bezel-layers. Overlays
disarm through the shared frame walk and pay the old cost. Measured ladder
on this glass (Star Catcher / Sakura Lua): 80MHz bring-up 24/21 -> 120MHz
MSPI 30/27 -> fold 35/30 -> game window 42/34 -> **core-0 feeder 53/43fps**.

**The feed left the VM core** (2026-08-19, the ledger's recorded strategic
lever, landed the same evening as the window): MicroPython's task is pinned
to core 1 on this port, so the band feed -- the rotate/fold synthesis plus
the queueing, ~7-8ms/frame -- used to bill every frame AND starve whenever
the VM sat inside a long native call (a moycore tick pumps nothing; the
flush wall measured 12.4ms against a 7.7ms game-window transfer). `moy_axs`
now runs the WHOLE flush on a FreeRTOS task pinned to core 0 (the CORE-0
FEEDER block in the C carries the handoff protocol), woken per band by the
SPI done-ISR, itself pinned to core 0. The 2ms soft pump timer and the
DeviceCanvas draw-op pump pokes are retired on this board (`moy_axs.pump`
survives as a no-op). The T-Deck took the same design on 2026-08-21
(`d9aa73e`) and the two feeders were merged into `native/moy_flush` the same
day, so "this board feeds off the VM core and the T-Deck does not" is history.
Measured on this glass: MP-side flush block 0ms, SPI starvation
0us (was ~4ms), flush wall 9.4ms, 0 timeouts / 0 tx errors over a
5,000-flush soak, `fold_test` re-proven at 0 mismatched bytes, on-glass
suite 10/10. The remaining gap to the T-Deck's 60/50 is now the VM core's
own frame (cart logic+render + the composite snapshot); recorded next
levers: per-cart render diets and the launcher first-paint diet (the
gesture-transition spike, which the feeder does not touch).


`native/moy_axs` -- raw `spi_master`, NOT esp_lcd, because the AXS15231B's
QSPI protocol wants the whole frame under ONE CS assertion behind a 4-byte
1-line opcode header (`0x32 00 2C 00`), which is the opposite of esp_lcd's
per-call CS cycling. That is the TRANSPORT, and since
2026-08-21 it is all this module still owns. Everything under it -- the flush
concurrency this file used to carry as its own copy of moy_lcd's design -- is
**`native/moy_flush`**, one shared body with the T-Deck, promoted the day that
board moved onto THIS board's core-0 feeder and the two halves became literal
copies of one another. Read `moy_flush.h`; it is the authority, and nothing
about the feeder or the handoff is restated here. What moy_axs supplies is
three hooks: `frame_begin` (acquire the bus, arm the window, ship the pixel
header), `queue_band` (the ROTATE-gather or the fold synthesis into the slot
the engine hands it, queued with `SPI_TRANS_CS_KEEP_ACTIVE` on all but the
last) and `frame_end` (retrieve the results, release the bus). Bands are 32
physical rows; completion is counted by this module's `post_cb` ISR, whose
body is the engine's `moy_flush_band_done_from_isr` -- static inline, so the
callback keeps its IRAM placement. `modules/guition_panel.py` is the
compositor over it -- since 2026-08-21 a thin SUBCLASS of the shared
`device/banded_panel.py` (`FoldingCompositor` over `BandedCompositor`, #206
item 1), the Python twin of the `moy_flush` split above. What is left in this
file is the `moy_axs` import, WIDTH/HEIGHT, the `ASYNC_FLUSH` revert flag and
the module-level `set_backlight()` -- the `*_fold` verbs moved onto the shared
folding rung in 2026-09 when the T-Deck grew the same lever, and what is still
this board's alone is the game WINDOW; there is no `sd_bracket` here, because nothing else is known
to share this QSPI host. Init sequence provenance: ESPHome's AXS15231 model plus
its generated DCS tail -- the exact sequence the owner's ESPHome build runs on
this exact glass.

## Build / flash / monitor

```bash
make firmware-build-guition-s3
make firmware-flash-guition-s3 PORT=/dev/ttyACM1
make firmware-monitor-guition-s3 PORT=/dev/ttyACM1
```

Bring-up smokes: `modules/moybyte_shell.py`'s `MODE` ("panel" / "touch" /
"desktop"), all self-terminating, all re-runnable from the live REPL:

```python
import moybyte_shell as s; s.MODE = "touch"; s.main()
```

## The serial dev channel

One vocabulary, every board (`runtime/dev_channel.py`; the T-Deck's README
carries the full RX story, which is this board's too -- same S3
USB-Serial/JTAG, same ISR). What is board-specific here is the cart push:
`python tools/push_cart.py <cart.moy> --board guition_s3` copies a folder onto
whichever store the console reports (`ws.carts_root` -- the TF card when one is
in the slot, the internal VFS when not), and on an image that has the `recv`
command it goes 8 bits wide instead of base64 inside `py` lines. This board's
`[serial] window` is **16384**: its USB-Serial/JTAG ISR only drains what the
stdin ring has room for, so the endpoint stalls and the host blocks -- real
flow control, unlike the P4, where the ack is the only backpressure and the
window is 4096 for that reason. The payload lands in a `.new` the host renames
only once the board's read-back sha256 agrees, and a host that goes quiet
mid-window is abandoned after 5s with the tmp removed. It is the only push
transport there is (the base64 chunk path went with it, 2026-09-02), so an
image from before `recv` answers `REMOTE ? recv` and the tool stops with one
line naming the firmware as too old.

## Bring-up log

* 2026-08-20 -- **stage 4 lands: the TF card is the cart store** (owner call
  "already has an SD inside, so you can do that now"; the exit-gesture DECLINE
  and the #202 close are the same session -- see the hardware table's SD row
  for the slot-numbering trap that cost the evening's plumbing). 34 carts
  seed to `/sd/carts` on first boot with a card, on-glass suite 10/10 running
  carts from the card, no card = the internal store this board shipped with.
  Also decided: **no touch-only game-exit gesture -- DECLINED** (owner: "we
  don't support that"; a paired BLE keyboard's hold-BACKSPACE is the exit
  path on this board), and **audio stays untracked** (the board HAS an audio
  out -- a 2-pin JST1.25 speaker connector, I2S per the field guides -- amp
  pins unverified; revisit if a speaker ever gets plugged in).
* 2026-08-19 (evening) -- **the owner calls the board PORTED.** Also the
  wedge arc's closing field data: **the cable-flash replug rule is RETIRED**
  (owner observation, after many flashes since bring-up: touch answered
  immediately after every one -- the single dead-touch episode never
  recurred, consistent with the boot-race + idle-filler story in
  device/axs_touch.py's docstring). Flash normally; a dead touch after boot
  would be a boot-race recurrence worth a serial trace, not routine. (The
  rule lives on only in this issue thread's OLDER comments -- an agent
  following #202 chronologically re-instructed replugs twice on 2026-08-19;
  the driver docstring is the authority.) Two closers the same session:
  * **font_scale 2 was BUILT, SHIPPED AND REVERTED the same day** (the full
    A/B ran on glass with the owner's eyes on both ends). At 1x the glass was
    untappable ("impossible to tap anything"), so 2x shipped -- the #39
    layouts held at 480x320@2 (240x160 font units, narrower than the 320x240
    base, a combination no tier had run; host demo tour + on-glass 10/10) --
    and the owner's verdict on the result was "2x looks bad": text at 1x
    reads FINE here, the real problem is TAP TARGETS. The direction that
    replaces it (recorded in #202, deferred until after the UI refactor):
    interactive chrome -- bar icons especially, and menu/settings rows --
    wants a MINIMUM PHYSICAL SIZE (a PPI floor) independent of the font
    scale, i.e. a chrome_scale beside font_scale in chrome.Layout. On this
    glass 16px is 2.46mm (~165 PPI); the Library shelf already models the
    answer (resolution-driven with fs floors, the 2026-07-12 owner call).
    FONT_SCALE stays 1; do not re-flip it to solve tap size.
  * **the Bench twins ran on this board for the first time** (over the dev
    channel, feeder image; JSON via tools/p4_cart_bench.py --attach). The
    floors MATCH THE T-DECK REFEREE: idle 62.5fps p50=16ms, silent/sound
    scenes 55.5fps p50=18ms, sound ≡ silent -- despite the rotate and the
    2x-class glass, which is the fold + game window + core-0 feeder chain
    doing its job. Lua logic 2.2x Python (45.4 vs 32.2fps), draw paths equal
    within the clock -- the #66 twin-audit result reproduced. Numbers in
    #202; cross-session diffs need a fresh-boot same-shape session (the
    ledger's rule).
* 2026-08-18 -- port authored (stage 0 skeleton through stage 6 code):
  moy_axs + guition_panel + axs_touch + run_desktop; `make test` green with
  the board in the staging-closure/board-toml suites.
* 2026-08-19 (the morning after, owner's eyes on the glass) -- three hardware
  verdicts and the LANDSCAPE flip:
  * **the panel discards writes until CASET/RASET are armed** -- first light's
    "coloured static" was power-on GRAM noise under a fully-successful flush;
    arming the window live fixed it, and moy_axs arms it every kick now.
  * **MADCTL MV is dead**: 0x60 and 0x20 both scramble the write path while
    0x00 stays clean. Landscape therefore rotates in the band copy
    (rotate-gather: sequential PSRAM reads, scattered writes into the
    uncached SRAM bounce -- same read traffic as the memcpy it replaced), with
    `moy_axs.set_rot(0|1)` as the direction knob; rot 0 confirmed upright.
  * **touch has two failure modes that present identically** (both in
    `device/axs_touch.py`'s docstring): a SECOND machine.I2C(0) instance
    reads constant bytes while the driver's first instance works -- so never
    diagnose touch with a side probe, go through the live console -- and a
    BOOT RACE where the constructor's single probe read loses and
    `available` latches False for the session (the episode that looked like
    a hardware wedge until two power cycles cleared it). The ctor retries
    now, poll() re-probes every ~2s, and a constant-byte streak is named on
    serial after ~5s instead of reading as "nobody is touching the screen".
  * touch mapping CALIBRATED on glass (SWAP_XY + FLIP_X, rot 0): taps land
    under the finger, corners included.
  * `tests/test_guition_on_glass.py` re-passed **10/10** on the landscape
    console (viewport seam now `(80, 40, 1)`).
  * **kinetic-scroll hiccup, measured and named** (open perf item, #202/#66
    style): a fling feels like start-stop-continue because the first
    repaint at a gesture transition bills ~80-84ms (frame-timer trace: idle
    frames cost 0 under the redraw gate, steady drag frames 27ms, fling
    shift frames ~0ms at 60fps -- and NO gc, the heap never jumps). It is
    the #113 retained-ring's full-paints-before-shift-eligibility cost at
    480x320. The lever is the launcher first-paint diet, not the driver.
  * lived pain: entering a game with no way out (the touch-only exit
    gesture, already on #202) -- the owner had to power cycle to leave a
    cart. Rising priority (a paired BLE keyboard's hold-BACKSPACE now
    also serves).
  * **the scroll feel, closed the same evening** (owner verdict: "perfect,
    looks better than tdeck"): the drag-hang-then-phantom-fling was the
    driver waiting the GT911's 400ms no-news bound to believe a lift on a
    controller whose only silence IS the lift. Fixed in device/axs_touch.py
    with measured constants: a 90ms per-controller bound (2x the worst
    touched gap) plus hold-window EXTRAPOLATION (the pointer glides on its
    measured velocity through the <=90ms release window instead of
    freezing -- pixels, not physics: extrapolated frames stay stale for
    the velocity EMA). A shared-console "still finger" decay rule was
    built on the wrong model the same day and REVERTED on data -- the
    trace that killed it (a resting finger streams 88% fresh) is in the
    console.py docstring. GPIO3 is CONFIRMED the touch INT (pulses while
    touched, silent after lift) -- the recorded next lever if release
    latency ever needs to drop below ~90ms.
* 2026-08-18 (first night, on glass, first build) -- **the console runs**:
  * stage 1: `moy_axs` first light on the first attempt -- init accepted,
    banded bounce flush at **19.4ms/frame** (51.6fps ceiling; pump 5.4ms CPU,
    idle 1.1ms, blocked only 1.1ms -- the kick/pump/drain overlap works,
    0 timeouts / 0 queue errors over the session).
  * stage 2 (half): the AXS15231 touch controller answers at 0x3B and
    reports no-touch correctly. The MAPPING knobs are still the
    ESPHome-derived guess -- run `guition_smoke.touch()` with a finger and
    bake the winners into `device/axs_touch.py`.
  * stage 6: boots to the desktop (first frame 270ms after a seeded boot;
    34 carts seeded to `/moy/carts` on the first boot), OTA confirm fired
    (`marked app valid (slot ota_0)`), and
    **`tests/test_guition_on_glass.py` passed 10/10** -- state shape, the
    320x480-system/320x240-game viewport seam `(0, 120, 1)`, swipes through
    the real pointer feed, a Python cart (Star Catcher, ~28fps) and a Lua
    cart (Sakura Lua via moycore, ~24fps) both run and exit, idle blank +
    wake, mem. `MODE = "desktop"` is the shipped default.
  * NOT yet verified (needs eyes/fingers): the pattern's orientation, colors
    and checker squareness on the physical glass, and the touch calibration
    pass. One anomaly on file: a single
    `frame error: 'NoneType' object isn't iterable` fired once, on the
    first-ever cart exit of the first seeded session, and never reproduced
    (not on later exits, not on a fresh boot); `_frame_error` now prints the
    full traceback so a recurrence names its line.
  * open stages/decisions: SD (stage 4 -- pins unverified), audio (stage 5 --
    amp/pins unverified), backlight PWM dimming (owner call), and a
    **touch-only exit gesture for fullscreen games** -- this board has no
    BACKSPACE to hold, so a running game currently has no on-glass exit path
    (tools/apps keep the bar's context-X; games hide the bar). #202 carries
    it.
