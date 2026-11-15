# Moybyte Zero on an ESP32-C3 (`c3_zero`)

The Zero — the screenless cart store a browser console runs against — on the
smallest chip that holds it: an ESP32-C3 with 4 MB of flash in the package
(the ESP32-C3FH4 of the "Super Mini" boards and their kin), USB-Serial/JTAG on
the chip, WiFi, and no PSRAM.

It is a **variant** of the XIAO Zero, not a second design. Both boards take
the same store host (`device/zero_host.py`, `device/zero_setup.py`,
`device/zero_shell.py`) and the same module sets but the SSH server, so how
the Zero is set up, paired, synced and signed into is
`firmware/seeed_xiao_esp32s3_zero/README.md`, and is not repeated here. This
file is what the chip changes.

## What the chip changes

- **No PSRAM.** Every block the kernel places in PSRAM is the one internal
  heap's on a build without it (`native/moy_kernel/moy_platform.h`,
  `MOY_HAS_PSRAM`), a task stack the table puts in PSRAM is internal, and the
  kernel keeps no small-block pool (`native/moy_kernel/moy_kernel.h`) and a
  half-size log ring (`native/moy_kernel/moy_log.h`). WiFi, the VM's heap and
  the native tier share the chip's internal SRAM, so the Python heap starts
  at 32 KB and grows only while `MOYBYTE_GC_SPLIT_RESERVE` stays outside it
  (`boards/MOYBYTE_C3_ZERO/mpconfigboard.h`, `moybyte_patch_gc_split_reserve`):
  the Zero's Python drives the C that does the work. The webhost's request
  buffer (`MOY_HTTP_REQ_MAX`, `native/moy_net/moy_net.h`) is the largest
  single tenant, held for the sync batches' size. The WiFi/lwIP window set
  the XIAO takes is left out for the same reason
  (`boards/MOYBYTE_C3_ZERO/sdkconfig.board`).
- **One app slot, no OTA.** The image carries its own browser console, which
  is more than half of it, so two slots do not fit in 4 MB: the table is a
  factory slot and the store (`boards/MOYBYTE_C3_ZERO/partitions-moybyte-c3-zero.csv`).
  The updater reports itself unavailable (`moy_ota.OtaUpdater.available()`),
  so the console's update screen shows no update to offer, and a new image
  goes on by cable. The store is the space after the slot.
- **No SSH server.** Running, it held its stack, Dropbear's state and the host
  key in the same heap, and the web host then had none left for a browser's
  parallel requests (`board.toml`'s `moy_ssh` denial). The web terminal is the
  shell's face here; `passwd` still sets the password a browser pairs with.
- **Single core, RISC-V.** Every task the native tier starts runs on core 0
  (`native/moy_kernel/moy_tasks.h`).
- **One I2C controller.** The kernel's device bus takes port 0, so this board
  declares no kernel bus beside it (`native/moy_kernel/moy_devbus.c`).

## Hardware facts

- USB id `303a:1001` (USB-Serial/JTAG). It opens with both lines HIGH, like
  the XIAO's (`[serial]` in `board.toml`): both LOW is a chip reset under the
  open handle. esptool's default reset reaches the ROM loader and its default
  `hard_reset` (an RTS pulse) leaves it into the app; nothing has to be held.
  Unlike the XIAO it declares no `watchdog_reset`: that resets the chip's USB
  too, under esptool's open handle, and the board comes back under a new
  ttyACM number (`[flash]` in `board.toml`).
- **Recovery:** hold BOOT (GPIO9) while plugging in, then flash. Ctrl-C on
  the serial port stops the store host at the REPL and Ctrl-D starts it again.
  A main.py written to the store's root does not replace the frozen one:
  MicroPython runs a frozen `main.py` before it looks at the filesystem.
- The UART0 REPL (TX GPIO21, RX GPIO20) stays on as the recovery console for
  an image whose USB has wedged.
- On a Super Mini the LED is GPIO8 (active LOW), a strapping pin.

## Free pins

The `/gpio` allowlist, `MOY_NET_GPIO_PINS` in `boards/MOYBYTE_C3_ZERO/mpconfigboard.h`,
and the `gpio` lease row in `board.toml` (a test holds the two equal):
GPIO0, GPIO1, GPIO3, GPIO4, GPIO5, GPIO6, GPIO7 and GPIO10. `machine.I2C(0)`
defaults to SDA GPIO5 and SCL GPIO6. Held back: the strapping pins 2, 8 and 9,
UART0's 20 and 21, the flash's 11–17 and USB's 18 and 19.

## Build, flash, look

```bash
make firmware-build-c3-zero
make firmware-flash-c3-zero PORT=/dev/ttyACM0
make firmware-monitor-c3-zero PORT=/dev/ttyACM0
```

`build.sh` runs from a worktree like every board's (`tools/worktree.py`), and
the browser console it bakes is that tree's own (`firmware/web_runner/`). The
build fails when the image outgrows the factory slot (the #168 guard reads the
slot from this board's CSV). A flash that changes the table formats the store.
