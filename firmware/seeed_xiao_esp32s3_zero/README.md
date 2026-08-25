# Zero — Seeed XIAO ESP32-S3, the headless cart-store companion (#41)

The re-based Zero (moycore plan §3.2, owner call 2026-08-12; tree re-provisioned
2026-08-25 on the sync RPC's landing): the **browser runs the whole console**
(the wasm head), and this board is the piece a browser cannot be — the kid's
cart store on real flash. It serves the wasm bundle + `carts.json` and applies
`POST /sync` write-backs through the same `moy_webhost`/`moy_sync` every
console board runs, so a cart made in the browser lands on this flash within a
second of its commit and comes back on the next visit. Console in your pocket;
the screen is whatever browser is nearby.

The predecessor tree (the "browser is the GPU" streaming port) died with the
2026-08 streaming sunset and was deleted 2026-08-17 (`931ede6`); git history
has it. Nothing here descends from it except the hardware facts below.

## Provisioning (no ESP-IDF build — deliberate)

1. **Flash stock MicroPython once** (v1.28.0, `ESP32_GENERIC_S3-SPIRAM_OCT`
   from micropython.org — this board has 8MB flash + 8MB octal PSRAM):

   ```bash
   # from a running MicroPython, the SAFE way into the ROM loader:
   mpremote connect /dev/ttyACM0 exec "import machine; machine.bootloader()"
   python -m esptool --chip esp32s3 --port /dev/ttyACM0 --before no_reset \
       --after no_reset erase_flash
   python -m esptool --chip esp32s3 --port /dev/ttyACM0 --before no_reset \
       --after watchdog_reset write_flash 0x0 ESP32_GENERIC_S3-SPIRAM_OCT-*.bin
   ```

2. **Push everything else** (modules + web bundle + seed carts + creds):

   ```bash
   ./provision.sh /dev/ttyACM0 path/to/wifi.json
   ```

   `wifi.json` is the console's own store shape
   (`{"networks": [{"ssid", "password"}]}`) and is a secret — never in the
   repo. Reading it off a console board over its dev channel works.

3. Watch it come up: `mpremote connect /dev/ttyACM0 repl` prints
   `ZERO serving http://<ip>:8080/` (mDNS `moybyte-zero.local`). Open that in
   a browser; authoring is on, and every commit syncs back to the board.

## Hardware facts (learned the painful way — respect these)

- **The REPL is silent unless the host asserts DTR.** A raw pyserial client
  must set `dtr=True`; mpremote does. Looks exactly like a dead board.
- **Getting OUT of the ROM loader needs no replug after all** (2026-08-25):
  `esptool --after watchdog_reset` exits download mode cleanly on this board.
  The old note ("only a physical replug exits ROM mode") predates it — that
  was `hard_reset`, which indeed does nothing here. Prefer
  `machine.bootloader()` to get IN (an esptool DTR-dance against the running
  TinyUSB CDC has wedged the USB device before — memory: `zero-port-xiao`).
- **The dual-OTA console partition table does not fit its 8MB flash** — but
  stock MicroPython ships its own single-app 8MB table, so on this
  arrangement that whole class of problem is gone.
- **STA, not SoftAP.** The old streaming port measured SoftAP throughput as
  the cause of its multi-second stalls. Serving a ~570KB gzipped bundle wants
  the router; a board with no saved network prints and drops to the REPL.
  (SoftAP provisioning — the self-contained pocket story — is a recorded
  follow-up in #41, not built here.)

## What is deliberately absent

- **No consent gate on /sync yet** — same standing as the console boards
  (`moy_webhost`'s docstring carries the doctrine). A headless board cannot
  show a PIN; its pairing story (QR on a sticker, a button press) is #41's.
- **No GPIO verbs** — the #9 pull rides its own track; this host is where
  they will plug in.
- **No frozen image / no build.sh** — provision.sh's cp list IS the module
  set. Graduate to the port kit only when something needs freezing.
