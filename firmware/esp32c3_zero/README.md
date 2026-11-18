# Moybyte Zero on an ESP32-C3 (`c3_zero`)

The Zero — the screenless cart store a browser console runs against — on the
smallest chip that holds it: an ESP32-C3 with 4 MB of flash in the package
(the ESP32-C3FH4 of the "Super Mini" boards and their kin), USB-Serial/JTAG on
the chip, WiFi, and no PSRAM.

It is a **variant** of the XIAO Zero, not a second design. Both boards take
the same store host (`device/zero_host.py`, `device/zero_setup.py`,
`device/zero_shell.py`) and the same module sets, so how
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
  the Zero's Python drives the C that does the work, and the reserve is the
  room that C takes while it works. A sync batch is the largest of it: the
  webhost reads an upload longer than its kept head into a buffer of its
  own, up to `MOY_HTTP_REQ_MAX`, and gives it back when the request ends
  (`native/moy_net/moy_webhost.c`, `HEAD_CAP`), so the room is there between
  requests rather than held. The WiFi/lwIP window set the XIAO takes is left
  out for the same reason (`boards/MOYBYTE_C3_ZERO/sdkconfig.board`).
- **One app slot, no OTA** (owner, 2026-11-17). The table is a factory slot
  and the store (`boards/MOYBYTE_C3_ZERO/partitions-moybyte-c3-zero.csv`).
  The updater reports itself unavailable (`moy_ota.OtaUpdater.available()`),
  so the console's update screen shows no update to offer, and a new image
  goes on by cable. The store is the space after the slot, and holds the
  browser console: the add-on cart whose digest the image names
  (`native/moy_net/moy_page.h`), which the cable flash installs into it over
  the REPL (`[flash] addon`, `tools/board_flash.py`) and the website's
  install writes into the store it lays down (`tools/store_image.py`). The
  website's card offers that install and, as the C3's only update, the
  merged image as it is, which keeps the store. Like the XIAO, a C3 whose
  store holds no add-on fetches one from its release unless its owner
  removed it (`zero_host.make_addon`, `way_back`), with buffers made for the
  fetch and freed after.
- **The image carries the core carts alone** (owner, 2026-11-17;
  `moy_seed.CORE`, board.toml's `[roster] image`), under the XIAO's seed
  rule (`zero_host.seed_carts`). The release publishes the other system carts, all
  but the test fixtures, as official carts in `carts-c3_zero.json`. A new store gets nothing beside the
  core: next to the add-on its little room is the kid's. With no OTA, a
  cable flash brings every official cart a store holds to the image's
  release (`tools/board_flash.py`'s `install_carts`; the core carts are the
  image's seed's). The page this board
  serves adds and removes the rest as the XIAO's does (its README), the
  install's modules frozen here for it; each request's buffers (its release
  index read whole, about 45 KB, and the cart's download) are made for it
  and given back when it ends.
- **SSH, without SFTP.** The server and the shell's face run in the same
  internal heap, so a build without PSRAM sizes them to what a session was
  measured to use there: the server's and the face's stacks and the session's
  arena (`native/moy_ssh/moy_ssh_run.c`, `native/moy_tty/moy_tty_sys.c`,
  `native/moy_tty/moy_tty_sh.c`). The face task is made when the server
  starts, beside the other long-lived blocks, because made at a session's
  start it stayed in the middle of the free heap and cut the block an upload
  needs. The `sftp` subsystem is refused (`native/moy_ssh/moy_ssh.c`): its
  buffers do not fit beside a session, and a client reports a refusal where
  running out would cut a transfer short. While a session runs, an upload
  that finds no room is answered 503 and the page sends it again.
- **Single core, RISC-V.** Every task the native tier starts runs on core 0
  (`native/moy_kernel/moy_tasks.h`).
- **One I2C controller.** The kernel's device bus takes port 0, so this board
  declares no kernel bus beside it (`native/moy_kernel/moy_devbus.c`).

## Hardware facts

- USB id `303a:1001` (USB-Serial/JTAG). It opens with both lines HIGH, like
  the XIAO's (`[serial]` in `board.toml`): both LOW is a chip reset under the
  open handle. esptool's default reset reaches the ROM loader and its default
  `hard_reset` (an RTS pulse) leaves it into the app; nothing has to be held.
- **Recovery:** hold BOOT (GPIO9) while plugging in, then flash. Ctrl-C on
  the serial port stops the store host at the REPL and Ctrl-D starts it again.
  A main.py written to the store's root does not replace the frozen one:
  MicroPython runs a frozen `main.py` before it looks at the filesystem.
- The UART0 REPL (TX GPIO21, RX GPIO20) stays on as the recovery console for
  an image whose USB has wedged.
- On a Super Mini the LED is GPIO8 (active LOW), a strapping pin.

## Free pins

The `/gpio` allowlist is the `gpio` lease row in `board.toml`, which the build
emits as `MOY_LEASE_GPIO_PINS`: GPIO0, GPIO1, GPIO3, GPIO4, GPIO5, GPIO6, GPIO7
and GPIO10. `machine.I2C(0)`
defaults to SDA GPIO5 and SCL GPIO6. Held back: the strapping pins 2, 8 and 9,
UART0's 20 and 21, the flash's 11–17 and USB's 18 and 19.

## Build, flash, look

```bash
make firmware-build-c3-zero
make firmware-flash-c3-zero PORT=/dev/ttyACM0
make firmware-monitor-c3-zero PORT=/dev/ttyACM0
```

`build.sh` runs from a worktree like every board's (`tools/worktree.py`), and
the browser console its image names is that tree's own (`firmware/web_runner/`),
and the flash installs it. The
build fails when the image outgrows the factory slot (the #168 guard reads the
slot from this board's CSV). A flash that changes the table formats the store.
