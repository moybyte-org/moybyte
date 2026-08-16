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
stage existed to answer. Each stage is one commit, and the commit message says
what to look for -- flash the last good one to bisect.

**ALL SIX STAGES ARE ON GLASS (owner, 2026-08-16).** Panel, touch, keyboard, SD,
audio and the shared console all came up; carts run, with sound. The port works.
What it was NOT was fast: it ran at roughly **half the fork's frame rate**, and
the whole of the difference was one number --

    fork      HITCH ... pump=3.8 ... raw(... flush=2.1)
              PUMP pump=3.79 idle=0.00 gaps=0 feed=10.67 bands=5 fold=0
    mainline  HITCH ... pump=0.0 ... raw(... flush=16.8 to 20.2)
              (no PUMP line at all)

    Brick Siege 26-27 fps (fork 51-54) | Brick Siege Lua 27 | Celeste 20-24
    Sky Run 30 | Letter Blitz 21-33

-- because the flush was blocking and paid the whole 153,600 B transfer inside
the frame. Worth being precise about which line said what, because the two
`pump=` fields look alike and are not the same instrument: the `0.0` is
**HITCH's** `pump=%.1f`, and this build emitted **no `PUMP` line whatever** --
`_diag_pump` returns early unless `comp.bounce_flush`, and the loop here did not
even import it. Both are fixed below, so **the `PUMP` line appearing at all is
the first proof the overlap is in an image.**

**The flush is fixed as of this commit and is the one thing here that has NOT
been on glass**; see "the compositor" below for what to watch.

Stage 2 also settles where the module list lives: `board.toml` + `tools/board_config.py`,
the same declaration the other two boards use, and the whole shared console
stages from here on. That is deliberate and it is explained in the file: the
stage commits are bisect points, so consecutive images should differ by the
subsystem under test and not also by a megabyte of frozen bytecode.

### What it costs, against the build it replaces

| | shipping fork build | this build |
|---|---|---|
| app image | 5,052,032 B | **3,566,416 B** |
| headroom in the 5 MB `ota_0` slot | 186 KB | **1,639 KB** |

Same console, same baked browser bundle (572,693 B), same partition table —
**1.42 MB less image, 29% smaller.** LVGL, `lcd_bus`, `st7789`, `task_handler`,
`rgb_bus`, `spi3wire` and the fork's `i2c` are simply not in it; the panel is
one 837-line C file. Both builds stamp OTA board id `tdeck` against a
byte-identical partition table, so a payload from either installs into the
other's inactive slot — which is what makes the migration an OTA rather than a
cable flash.

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
Moybyte panel: pump (NNNN, N, N, NNNN, 5)
Moybyte panel smoke done -> REPL
```

`last=` is the WALL transfer span, kick to fully out, and the overlap does not
shrink it — the bus moves 153,600 B at whatever rate it moves them. What the
overlap changes is how much of that the CPU waits for, which is the console's
`flush=` and `bounce_stats()[…]`. The smoke fences with `comp.sync()` before
reading, because `flush()` now returns with the frame still going out.

| what you see | what it means |
|---|---|
| nothing on serial at all | not the panel — bootloader/partition/flash-mode |
| serial runs, screen stays dark | backlight (GPIO42) or the board power rail (GPIO10) |
| screen lights, shows noise | init sequence went out but `show()` did not land |
| bars appear, but rotated / mirrored | MADCTL. `moy_lcd.set_madctl(0x28)` / `0xA8` / `0xE8` at the REPL — no rebuild needed |
| bars appear with wrong colours | red/blue swapped = the BGR bit; washed out = a gamma command was rejected |
| rows sheared diagonally | stride — a `WIDTH`/`row_bytes` mistake |
| a seam every 48 rows | the flush banding — a continuation band sent a command |
| flicker or tearing | the ping-pong, or the #66 flush overlap refilling a bounce slot under a live DMA. `ASYNC_FLUSH = False` in `tdeck_panel.py` is the one-flag revert that tells the two apart; `TDeckCompositor(nfbs=1)` disables both |
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

#### `MODE = "keyboard"` (stage 3)

Three 15-second phases, then a verdict. A **moving green bar** runs across the
top the whole time — a frozen bar is a frozen loop, which is the failure this
smoke exists to make visible without a stopwatch. Under it: the last key byte,
the raw five matrix bytes, the held buttons lit green, and everything typed so
far.

| phase | what it proves |
|---|---|
| 1 ASCII sync | the C3 answers at 0x55 and returns clean 1-byte ASCII — the mode the code editor runs in. **A held key does not repeat**; that is correct, not a fault |
| 2 raw sync | `0x03` took, and reads return five bytes. **Hold W and the `up` chip stays lit** — that is the whole reason raw mode exists |
| 3 raw poller | the same reads on `InputPoller`'s thread. Compare its `loop_max` with phase 2's |

```
Moybyte kbd: 2 raw sync         frames=NNN loop_max=NNms over20=N | i2c reads=NNN max=NN.Nms ...
Moybyte kbd: poller thread up (12ms cadence)
Moybyte kbd: 3 raw poller       frames=NNN loop_max=NNms over20=N | i2c reads=NNN max=NN.Nms ...
Moybyte kbd: GIL VERDICT loop_max sync=NNms poller=NNms
Moybyte kbd: reverted to ASCII -- raw_mode=False
```

**Read the verdict like this.** The C3 clock-stretches; measured stalls on this
board run 21–60ms. In phase 2 that stall lands inside the loop and *is* the
frame. In phase 3 it lands on the poller thread — but only if `machine_i2c.c`
released the GIL, because MicroPython threads share one, so without the patch
the stall freezes the VM from whichever thread took it.

* phase 3 `loop_max` collapses toward the flush cost while its `i2c max=` stays
  bad → **the patch works**, which is the whole reason this port carries it.
* both `loop_max` values bad → the patch is not in this image. `grep "Moybyte #69 GIL"
  firmware/lilygo_t_deck_plus_mainline/.build/micropython/ports/esp32/machine_i2c.c`.
* both `loop_max` values good *and* `i2c max=` small → the C3 simply was not
  stalling this run. Hold several keys at once and re-run `tdeck_smoke.keyboard()`.

| what you see | what it means |
|---|---|
| `RAW MODE UNSUPPORTED` | C3 firmware older than 2025-06-12 ignored `0x03`. The driver stuck the session back on ASCII + the hold latch, which is correct — but hold-to-move will stall. Flash `T-Keyboard_..._250620.bin` |
| phase 2 lights no buttons | the matrix decode. `bytes=` on screen is the raw five; `moybyte/input.py`'s `RAW_KEYS` table maps (byte, bit) → key |
| `poller thread FAILED to start` | no `_thread` or no RAM. The console degrades to synchronous polling, which *is* phase 2 — a real fallback, not a break |
| the bar freezes and never resumes | not a stall — a hang. The last serial line names the phase |

The last thing the smoke does is send `0x04`. Skipping that revert is what once
left the keyboard streaming matrix bytes at the code editor, irreversibly,
because nothing re-sends it.

#### `MODE = "sd"` (stage 4)

**The stage that hangs boards.** The card and the panel share one SPI host, and
every way that goes wrong is silent: the board stops, USB stays enumerated but
dead, no panic to read. So this smoke is built as a *bracket* — every phase
prints before and after, and if the board wedges, the last serial line names
the op that did it.

| the last line you see | what wedged |
|---|---|
| `SD > sync` | the pre-op DMA drain |
| `SD > op` | the SD transaction itself |
| `SD < op` | **the next panel flush** — the shared-bus corruption this whole design exists to avoid. `SD = panel ok` is the line that says it did not happen |

On the glass: a scrolling `SD SMOKE` status list. It checks that `moy_lcd` and
`moybyte_sd` agree about the host id and CS pin (a drifted constant would be a
hang with no message), mounts, prints the card size and `/sd` listing, and then
runs **ten write / flush / read / flush rounds of 4KB**, verifying the bytes
every round. One clean write proves nothing — bus and DMA corruption is
cumulative, and the documented failure mode is "the write lands, then a *later*
flush freezes". It finishes with a burst of 60 back-to-back flushes for the
same reason.

```
Moybyte sd: sectors=NNNNNNN (NNNNMB) root=[...]
SD > sync   (write 1/10)
SD > op     (write 1/10)
SD < op     (write 1/10) NNms
SD = panel ok (write 1/10)
...
Moybyte sd: 60 post-session flushes in NNNms (N.Nms each)
Moybyte sd: rounds=10 bad=0 write_max=NNms read_max=NNms
```

**With no card in the slot** the mount fails, and the smoke deliberately keeps
going: it flushes the panel and reports `SD = panel ok after a failed mount`.
That is a real check, not politeness — `moy_sd.init()` calls `sdspi_host_deinit()`
on a failed attach, which is the one teardown in this design, and it must not
poison the bus.

The card is left **mounted** at `/sd` when the smoke returns. That is
deliberate: `with_sd_live` attaches once and keeps the device resident for the
session, because tearing it down between ops is what corrupts the bus.

#### `MODE = "audio"` (stage 5)

A rising four-note phrase, the three starter SFX (coin / jump / thud), five
seconds of music 0, then the same SFX at master volume 0 and 7.

**But the verdict is a number, not the sound.** "I hear nothing" has at least
four causes — no native module, no I2S channel, a synth producing silence, or
an amp that is not wired — and an ear cannot tell them apart. So every step
reports `moy_audio.frames_out()`, the frames the I2S peripheral has actually
**accepted**, which is the last thing measurable on this side of the wire:

```
Moybyte audio: feed=core-1 task rate=22050 bank sfx=3 music=1
Moybyte audio: beep 262    350ms frames=7717 measured=22048Hz (nominal 22050)
Moybyte audio: VERDICT the peripheral consumes at the nominal rate.
```

| the number | what it means |
|---|---|
| ~22050 Hz | the synth renders and the peripheral consumes. Silence past this point is the **amp or its wiring**, not the firmware |
| flat / `frames=0` | nothing is feeding I2S. The `feed=` line says which path was taken — `core-1 task`, `legacy I2S`, or `NONE` |
| a *wrong* rate | the clock. By ear this is just "sounds a bit off"; by number it is unambiguous |
| `moy_audio ABSENT` | the usermod is not in this image. That is silence **by design** (#97) — the Python fallback synth died with moycore stage 0 |

The `vol 0` step is there for the same reason: master 0 must silence the amp
while the peripheral **keeps** taking frames, which is exactly the pair of facts
a counter can show and an ear cannot.

The bank is `AudioBank.default()`, so this needs no card and no cart. The
core-1 feeder task keeps running after the smoke returns — `moy_audio.audio_stop()`
from the REPL stops it.

#### `MODE = "desktop"` (stage 6) — the console

The real thing: `moy_runtime.run_desktop()` over the shared boot spine
(`runtime/device_boot.py`), the shared `console.Workstation`, carts on SD, Lua
carts through `moycore`, OTA, and the browser console baked into the image.

Boot is the same sequence the other two boards run, because it is the same
code. What you should see:

1. serial says `Moybyte boot: starting`, the panel lights on the **boot splash**
   with a progress bar (a first boot writes every built-in cartridge to SD
   before anything else composes — that wait is what the bar is for);
2. `Moybyte boot: loading cartridges N/M` climbing;
3. `Moybyte loaded N carts from SD`, then `lua runtime ON (moycore)`;
4. the **launcher**, and `Moybyte first frame in NNNms`.

Then it is the console: tap a cart to play it, hold BACKSPACE ~700ms to leave a
game, the ≡ menu and Settings work, the Editor's seven tabs work.

| what you see | what it means |
|---|---|
| splash, then `using built-in carts` | SD did not mount. Run `MODE = "sd"` — it will say why, in a bracket |
| splash forever, no launcher | read the last `boot:` line; it names the step |
| launcher, but no sound | stage 5 answers this with a number, not an ear |
| `lua runtime ABSENT` | `moycore` is not in the image; Lua carts open the runtime-missing panel, which is the designed floor |

---

## The serial dev channel, and the RX question

The T-Deck's shipping firmware has **no on-glass test harness** — no `state`, no
`tap`, no `py` — where the P4 has all three (`tools/p4_autotest.py`,
`tests/test_p4_on_glass.py`). The stated reason is that this board's USB-CDC RX
is dead under the desktop. **That reason is wrong**, and the correction is the
most valuable thing in this port.

### What was recorded, and what is actually true

`CLAUDE.md` says "this fork's USB-CDC stack has no at-arrival interrupt-char
scan, so Ctrl-C/REPL/commands never arrive". The revert that established the
lore (`4faf07a`) says "select.poll reports stdin ALWAYS-READY even when empty,
so poll-then-readline becomes a blocking read that stalled the loop ~30s".

The first claim is false and checkable. The shipping fork is MicroPython
**v1.27.0**; this build is **v1.28.0**; and every file on the CDC receive path
is byte-identical between them — MicroPython's `mp_usbd_cdc.c`, `mp_usbd.c`,
`mp_usbd_runtime.c`, `interrupt_char.c`, `sys_stdio_mphal.c`, and the esp32
port's `usb.c`, `uart.c` and `main.c`. Its `mphalport.c`, `vm.c` and
`scheduler.c` differ only cosmetically, and both builds resolve to the same
`MICROPY_HW_USB_CDC=1` / `USB_SERIAL_JTAG=0` / `UART_REPL=1`. The at-arrival
scan **exists**:

```c
/* shared/tinyusb/mp_usbd_cdc.c, tud_cdc_rx_cb -- identical in both trees */
if (data_char == mp_interrupt_char) {
    stdin_ringbuf.iget = stdin_ringbuf.iput = 0;
    mp_sched_keyboard_interrupt();
}
```

and `tud_cdc_rx_cb` is linked into the shipping image. The same revert commit
says so itself, three lines below the wrong diagnosis: *"without a reader in
flight, **Ctrl-C drops to a live REPL**"*.

So **rebuilding on mainline changes nothing about RX**, in either direction.
What was really happening has two parts, and neither is a broken `poll`:

1. **`sys.stdin.readline()` blocks per character.** `sys_stdio_mphal.c`'s
   `stdio_read` loops on `mp_hal_stdin_rx_chr`, which never returns empty. So
   ONE byte in the ring buffer makes `poll` *correctly* report ready, and then
   `readline()` waits for a newline that may never come. That is the ~30s stall,
   exactly.
2. **Something was putting that byte there.** `MICROPY_HW_ENABLE_UART_REPL` is
   on in both builds, and UART0's ISR feeds the *same* `stdin_ringbuf`. Noise on
   a floating U0RXD (GPIO44, on this board's expansion header) is
   indistinguishable from a typed character. This is a hypothesis, not a
   measurement — see below for the one line that settles it.

### What this build does instead

`moy_runtime._SerialChannel` is armed by default (`SERIAL_CMDS = True`) and is
built so that both mechanisms are survivable:

* it registers **`select.POLLIN` only**. A bare `register(sys.stdin)` defaults
  to `RD|WR`, and `mphalport.c` grants `POLL_WR` unconditionally — so a bare
  registration is truthy on every call forever, which looks exactly like "poll
  reports stdin always-ready";
* it **never calls `readline()`**. It reads **one byte** with
  `sys.stdin.read(1)`, only after poll reported `RD` (which the port sets only
  when `ringbuf_peek() != -1`), accumulating until a newline. A byte read is a
  byte consumed, so noise costs a bounded slice of a frame and can never park
  the loop; a partial line past 96 chars is dropped;
* it **counts what it swallowed**, and the diag tick prints
  `SERIAL rx=N lines=N dropped=N partial=N`.

That last line is the experiment. **`rx` climbing on an idle board with
`lines=0` means something is injecting bytes into stdin** — mechanism 2, and
the fix is `MICROPY_HW_ENABLE_UART_REPL (0)` in the board header, which takes
UART0's ISR off the shared ring buffer. `rx=0` while the channel refuses
commands means the CDC path itself, and the escalation is the S3's
**USB-Serial/JTAG** peripheral, which fills the ring from a *true hardware ISR*
(`usb_serial_jtag.c`) rather than a scheduled TinyUSB task — that is what the
P4's UART behaves like, and it is why the P4's stdin commands work. On the S3
it is mutually exclusive with CDC (`SOC_USB_OTG_PERIPH_NUM=1`).

One caveat worth knowing: TinyUSB is pumped by the MicroPython scheduler, which
the VM services at every bytecode branch. Ordinary Python loops are fine, but
`@micropython.native` code and long native C calls do **not** check — so RX
latency is bounded by the longest gap between VM branches, not by the poll
cadence.

### The commands

Piped whole lines, one per newline: `echo state > /dev/ttyACM0`.

| command | what it does |
|---|---|
| `state` | one-line JSON: screen / frames / cart / stack / settings scroll / wifi / app claims |
| `tap <x> <y>` | a synthetic tap at canvas coords, through the real pointer feed |
| `tap <name>` | tap a named bar button (any `ws.layout.<name>_btn` rect) |
| `run [name]` | select the first cart whose title matches, and run it |
| `diag 0\|1` | the diagnostic frame-eaters (`perf_capture` + the FPS chip) |
| `skip 0\|1` | the #77 frameskip gate |
| `gov 0\|1` | the #63 frame governor |
| `mem` | a forced collect, then the live/free split |
| `py <code>` | eval/exec one line against the LIVE console (`ws`, `wm`, `pointer` in scope) |
| `quit` | leave the desktop for the REPL, cleanly |

If this works on glass, `tools/p4_autotest.py`'s approach points straight at
this board and the T-Deck gains the on-glass suite it has never had. If it does
not, the `SERIAL rx=` counter says which of the two mechanisms is responsible,
which is more than the previous attempt could say.

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
work LVGL and `lcd_bus` used to do has to live somewhere. It lives in one 837-line
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

**The flush OVERLAPS the next frame's render** (ported 2026-08-16, not yet on
glass — see below). 320×240×2 = 153,600 B is ~17 ms on this bus, and paid
synchronously it caps the loop near 58 fps before a pixel is drawn. That is
exactly what the first console build measured against the fork on the same
glass:

| | fork | mainline, before | mainline, after |
|---|---|---|---|
| `flush=` | 2.1 ms | 16.8–20.2 ms | **expected ~2–5 ms** |
| `PUMP` | `pump=3.79 idle=0.00 gaps=0 feed=10.67 bands=5` | `pump=0.0` (not running) | expected fork-shaped |
| Brick Siege | 51–54 fps | 26–27 fps | expected ~45 fps (see below) |

So `moy_lcd`'s one blocking `show()` is now a three-verb split, and it is the
fork's strategy, not a new one:

* **`kick(n)`** arms the window, resets the band bookkeeping, copies + queues the
  first `BOUNCE_SLOTS` bands (~6.8 ms of transfer buffered) and **returns**;
* **`pump()`** copies + queues every band whose bounce slot has since freed —
  ~0.8 ms each, and non-blocking *only because* of the no-acquire patch below;
* **`drain()`** finishes feeding and waits out the tail. It runs at the top of
  the next `flush()`, where the render that just happened has already hidden
  most of it, and before every SD op.

`show(n)` survives as `kick` + `drain` in one call: the bring-up smokes want one
number and no ping-pong reasoning.

**Two feeders call `pump()`, and both are needed.** A 2 ms `machine.Timer`
(esp32 timers schedule through `mp_sched`, so the callback lands between
bytecodes — the only feeder during a cart's long Python `_update`), and the
`pump_if_pending` poke `DeviceCanvas` makes after each big native draw op (the
soft timer **cannot** fire while the interpreter sits inside one 15 ms C fill;
that measured as `PUMP idle=2-6ms` of starved SPI on the fork). On a gated
canvas `moy_gfx`'s own draw context upcalls it every `GATE_PUMP_EVERY` ops.

Both are optimisations of *when* bands are fed. If the timer never starts and
every poke is missed, `drain()` feeds them all and the flush is simply
serialised again — the pre-overlap cost, **never a glitch**: the front buffer is
immutable while it ships, so the bands are tear-free by construction.

`sync()` is consequently **real now**, not a formality. The card shares this SPI
host, and the continuation bands deliberately do not hold the bus lock, so an SD
op overlapping an in-flight flush is the documented way to hang this board.
`run_desktop._with_sd_synced` already called it; it now drains.

*Why ~45 fps and not the fork's 51–54:* the two builds' `draw=` differ by ~3 ms
independently of the flush (26–27 fps at `flush=17` implies ~20 ms of draw,
against the fork's ~17). The overlap makes the frame `max(render, transfer)`, so
what is left is a render-side gap — the `LAYER_COPY_ASYNC` row in the table
below is the first suspect.

### `modules/tdeck_panel.py` — the compositor

`TDeckCompositor` implements the same small interface `DeviceCanvas.__init__`
and `moy_runtime.run_desktop` already call — `size` / `framebuffer` /
`back_buffer` / `gfx` / `flush` / `sync` — the one `p4_display.P4Compositor` and
`moy_compositor.Compositor` both implement, plus the two the diag layer probes
for with `getattr` (`pump_if_pending`, `bounce_stats`). No new seam is invented
(`docs/backend_contract_v1.md` L8: strategy stays the backend's).

It is thin on purpose: the ping-pong, the timer and the stats forwarding, over
`moy_lcd`'s kick/pump/drain. Where the fork's compositor owns the bounce
buffers, the completion counter and the pacing arithmetic in Python, all of that
is C here, so a band never crosses the boundary.

**`ASYNC_FLUSH = False` is the one-flag fallback.** It restores the blocking
`moy_lcd.show()` path byte-for-byte, and it is how a tear, a glitch or a hang
gets attributed to the overlap in a single reflash.

**What is NOT ported, deliberately:** the #190 flush-bounce scale fold, which
*synthesises* each band for a small-canvas game rather than copying the root
framebuffer. It needs `moy_gfx` kernels writing into the bounce slots, i.e. the
slots handed back to Python, and it is a separate lever with its own A/B.
`fold_supported` is absent, `DeviceCanvas.blit_game` takes its ordinary root
composite path, and the PUMP line prints `fold=0`. Nothing degrades.

---

## The three fork patches, re-solved

`build.sh` applies them marker-guarded, so a warm `.build` re-patches nothing.

| patch | how it is solved here | which stage needs it |
|---|---|---|
| **REPR_C unboxed floats** (#66) | guarded `sed` on MicroPython's own `mpconfigport.h` (in the cloned upstream under `.build/`, not a file of ours) — its `MICROPY_OBJ_REPR` line, and the build **fails loudly** if the line has changed shape. A sed rather than the fork's context diff, so it survives the line moving between MicroPython releases. Verified in the built tree. | None strictly — but it is free, it changes object layout so it must be settled early, and every image here was compiled and linked with it |
| **I2C GIL release** (#69) | a small in-place Python edit that brackets `i2c_master_cmd_begin` with `MP_THREAD_GIL_EXIT/ENTER`. Result is byte-identical to the fork's patched file. Exits non-zero if the call site is not found. | **Stage 3.** It is what makes the poller THREAD worth having: without it a C3 clock-stretch stall holds the GIL and freezes the whole VM no matter which thread issued the read. Stage 2 reads I2C on the main thread and cannot tell the difference |
| **esp_lcd `tx_color` no-acquire** (#66) | the fork's `.patch` file, applied to the ESP-IDF tree (idempotent; the shared checkout usually has it already from the fork build) | **The overlap.** Without it every continuation band calls `spi_device_acquire_bus`, which waits for the device's in-flight DMA — so `pump()` would block on the previous band and the flush would be serialized again, just spelled differently. Confirmed compiled in: `panel_io_spi_tx_color` branches on the sign of `lcd_cmd` around both the acquire and the release |

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
| async flush + pump (#40/#43/#66) | on | **on** (2026-08-16) | ported — see the flush section above. Was the biggest single lever here; `ASYNC_FLUSH = False` in `tdeck_panel.py` reverts it |
| GDMA async layer copy (#54 St.2 / #63) | on | **off** | `device_canvas.LAYER_COPY_ASYNC` is tied to `moy_compositor.SRAM_BOUNCE_FLUSH`, and that module is not staged here, so it resolves False. **The stated reason for that used to be "there is no SRAM bounce here", and since the flush split that is simply untrue** — the bounce is in `moy_lcd`, and the PSRAM contention the lever guards against was never present anyway (the panel DMA only ever reads internal SRAM). So this is now the RANKED next lever, not a deferred one: it measured layer 7ms → 0.04ms on the fork, and the ~3 ms of render the mainline carries over the fork is the right size for it. It needs a decision about where the flag comes from, because `device_canvas.py` belongs to the shipping tree and must not be edited from here — setting `device_canvas.LAYER_COPY_ASYNC = True` before the first canvas is constructed is the no-edit route |
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
  session — which is why `sync()` exists, and why it stopped being a formality
  the moment the flush started outliving its call. The continuation bands
  deliberately do **not** hold the SPI bus lock (that is the no-acquire patch),
  so nothing but `sync()` keeps an SD transaction off a live panel DMA.
  **One exception, and it is not ours:** `DeviceBoot.load_carts` opens ONE
  `with_sd_live` session around the whole seed+scan and repaints the progress
  bar *inside* it (`DeviceBoot.note` → `comp.flush()`, once per cart), so a
  first boot genuinely interleaves panel bands with SD writes. `CLAUDE.md`
  states the no-flush-in-session rule absolutely, and **the shipping fork build
  has been violating it in this exact place for as long as it has had the async
  flush** — `moy_runtime` passes a bare `with_sd_live`, not the synced wrapper,
  and it works on glass. So this build matching it is not a new risk; before
  this commit the mainline was merely *safer* than what ships, because its
  blocking flush could not overlap anything. If a first boot wedges with
  `SD > op` as the last serial line, this is the first place to look, and
  `ASYNC_FLUSH = False` is the test.
  A second, narrower one: `Workstation._cover_prefetch_tick` reads a cover blob
  off `/sd` on idle frames through a bare `store.load_image`, not through
  `ws._with_sd`, so it takes no `sync()` either. It is gated on **three**
  consecutive quiet frames, which at the frame cap is ~50 ms after the last
  kick — long after the 2 ms pump has finished the frame — so it is very hard to
  hit, and again it is what the fork already does.
  Worth being honest about the size of this risk rather than repeating the
  folklore: what `CLAUDE.md` records as having actually hung boards is
  `sdspi_host_deinit` between ops and re-creating a `Pin` on `TFT_CS` —
  *teardown*, not concurrency. Two devices transacting on one host is what
  `spi_master`'s bus lock is for, and it is what the fork has relied on for
  months. And interleaving is *architecturally* fine on SPI: every band is its
  own transaction with CS released at the end, so a card transfer slotted
  between two bands is latched by the card alone, and the ST7789 — mid-RAMWR
  with its CS high — ignores it. What the no-acquire patch really changes is
  that the panel no longer *excludes* the card for the duration of a frame:
  ordering becomes per-transaction arbitration by the bus lock instead of a held
  lock. `sync()` is how exclusion is restored where this project's rule wants
  it, and it is cheap, so the places that already call it should keep doing so.
- **Do not re-create a `Pin` on `TFT_CS` (12) or `SD_CS` (39) once a driver owns
  them.** `moy_lcd.init()` parks them high *before* the bus exists, which is what
  the fork's `tdeck_board.init_board_pins` does; it is reconfiguring them
  afterwards that corrupts the shared bus and silent-hangs the next flush.
- **80 MHz SPI is requested, not delivered.** None of MOSI(41)/SCK(40)/MISO(38)
  are the S3's IOMUX-native FSPI pins, so everything routes through the GPIO
  matrix, which caps a write-only LCD at ~40 MHz. The board's wiring is the wall.
- **The keyboard has two modes** (ASCII vs raw matrix, `0x03`/`0x04` over I2C
  0x55) and the console flips between them per screen. `MODE = "keyboard"` is
  the on-glass check, and the last thing it does is send the `0x04` revert.
- **Serial TX works during play.** RX is the one constraint this port
  *contests* rather than inherits — see "The serial dev channel, and the RX
  question" above. The reason previously recorded for it is wrong on the facts,
  and the channel here is built to survive what was actually happening.

---

## Stages

Each is one commit. The commit message says what it adds and what to look for
on glass, so a misbehaviour can be bisected by flashing the last good one.

| # | what | mode | image | state |
|---|---|---|---|---|
| 1 | **Panel** — mainline boots, ST7789 comes up, a test pattern lands | `panel` | 1,704,976 B | **on glass 2026-08-16** |
| 2 | **Touch** (GT911, I2C0 @ 0x5D/0x14) + `board.toml` + the whole shared module tree | `touch` | 2,207,232 B | **on glass 2026-08-16** |
| 3 | **Keyboard** (ESP32-C3 @ I2C0 0x55, both modes) + the #69 poller A/B | `keyboard` | 2,210,016 B | **on glass 2026-08-16** |
| 4 | **SD** — `moy_sd` attach on the live host, the dangerous one | `sd` | 2,238,144 B | **on glass 2026-08-16** |
| 5 | **Audio** — I2S into the MAX98357 via `moy_audio` | `audio` | 2,251,856 B | **on glass 2026-08-16** |
| 6 | **The console** — `run_desktop` over `device_boot`, Lua carts, OTA, the baked web console, the serial dev channel | `desktop` | 3,564,672 B | **on glass 2026-08-16** — worked, at ~half the fork's fps |
| 7 | **The flush overlap** — `moy_lcd` kick/pump/drain, the 2 ms pump timer, a real `sync()` | `desktop` | 3,566,416 B | **compiles; NOT on glass** |

### Reading the next flash (stage 7)

Nothing below has been on hardware. Turn `diag 1` on and read three lines:

* **`raw(... flush=N)`** in `DRAWBRK`/`HITCH` — the whole point. **16.8–20.2 → 2–5 ms**
  is success. Still ~17 means `flush()` is not taking the async path at all
  (`ASYNC_FLUSH`, or `moy_lcd.kick` missing, or `nfbs == 1`), and the PUMP line
  will be absent to match.
* **`PUMP pump=… idle=… gaps=… feed=… bands=5 fold=0`** — a line that did not
  exist before, printed every 3 s diag tick. Its mere presence says
  `comp.bounce_flush` is on, i.e. the split path is running; its absence says it
  is not, and there is no point reading anything else.
  Expect `pump≈3–4 ms` (five 30 KB PSRAM→SRAM memcpys), `bands=5`,
  `fold=0` (the #190 fold is not ported). **`idle`/`gaps` are the diagnosis if
  fps disappoints**: `idle≈0 gaps=0` like the fork means the feeders keep up and
  what remains is real transfer time; `idle=2–6 ms` means bands are being fed
  late and the lever is the pump period or a third bounce slot (the fork's
  verdict on the third slot was "closed the gap, bought no fps" — read
  `moy_compositor.BOUNCE_SLOTS` before repeating it). A `pump=` near the whole
  transfer would mean the no-acquire patch is not in the image after all.
* **fps** — Brick Siege **26–27 → ~45** is the expected shape; 51–54 would mean
  the render side matched the fork too, which it did not before. Sky Run 30,
  Celeste 20–24, Letter Blitz 21–33, Brick Siege Lua 27 should all move with it.

If the serial dev channel is alive on this board (see the RX section — that is
its own open question), the cheapest check needs no diag tick and no hitch:

```
echo 'py ws.comp.bounce_stats()' > /dev/ttyACM0        # (pump, idle, gaps, feed, bands)
echo "py __import__('moy_lcd').pump_stats()" > /dev/ttyACM0
```

The second is the raw C tuple, `(pump, idle, gaps, feed, bands, blocked,
timeouts)`. **`blocked` is the one that answers the whole question**: it is the
µs the CPU actually spent inside `kick`+`drain` for the last frame, i.e. the
same quantity as `flush=`, straight from the driver. `timeouts` must be 0.

And two things that would say the overlap is *wrong* rather than slow: **tearing
or a band-shaped seam** (the ping-pong or a slot being refilled under a live DMA
— set `ASYNC_FLUSH = False` and reflash to confirm the attribution), and **a
hang inside an SD session** (`SD > op` as the last serial line), which would mean
a flush outlived a `sync()`. `moy_lcd.pump_stats()[6]` counts flush timeouts;
it should stay 0.

The stage-6 figure includes the 572,693 B baked browser bundle. Build on a tree
with no `firmware/web_runner/dist` and the image is about 573 KB smaller, with
a warning — such an image serves a console only from a copy on its storage. The
warning becomes a hard failure under `CI` (or `MOYBYTE_REQUIRE_WEB_BUNDLE=1`),
because a published image with no console is the whole bug the baking fixes.
