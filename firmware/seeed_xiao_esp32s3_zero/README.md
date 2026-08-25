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

2. **Push everything else** (modules + web bundle + seed carts, and optionally
   creds):

   ```bash
   ./provision.sh /dev/ttyACM0 [path/to/wifi.json]
   ```

   `wifi.json` is the console's own store shape
   (`{"networks": [{"ssid", "password"}]}`) and is a secret — never in the
   repo. Reading it off a console board over its dev channel works. **Omitting
   it is now a supported answer**: a board with no credentials hosts its own
   setup AP (below), and re-running the script without the argument leaves an
   existing `/moy/wifi.json` alone.

3. Watch it come up: `mpremote connect /dev/ttyACM0 repl` prints
   `ZERO serving http://<ip>:8080/` (mDNS `moybyte-zero.local`). Open that in
   a browser; authoring is on, and every commit syncs back to the board.

## First-run setup, with no cable (`zero_setup.py`, #41)

A board that cannot join anything does **not** print and give up — it becomes
its own network for as long as it takes to be told about a real one:

```
boot → connect() finds nothing joinable
     → SoftAP `moybyte-zero-XXXX`  (open; the suffix is the AP MAC's tail)
     → join it from a phone, open http://192.168.4.1/
     → name + 4-digit pin + a network from the scan + its password
     → saved → reboot → STA, as normal
```

The form is one small self-contained page on **port 80** (a phone types
`192.168.4.1`, not `192.168.4.1:8080`), and it is a plain `<form>` — the page's
only script fills the network list, so a phone with a broken one can still be
typed into. The AP is **open on purpose**: there is nothing behind it but the
form, it exists only while the board is unconfigured, and the one secret that
crosses it belongs to the person standing next to the board. What it costs is
that a neighbour in range during that minute could configure the board first;
what it buys is a setup that needs no printed key and no instructions.

Setup writes two files: the network into `/moy/wifi.json` (**merged** — the new
one goes first, since `connect()` walks the list in order, and older ones are
kept so a board set up elsewhere still comes up at home), and the name + pin
into `/moy/zero.json`. From then on the name is the mDNS label and **the pin
gates every write** — `POST /sync` and `POST /gpio` both. A page reaches the
write half by carrying `?pin=…`, which is why the serial line and the "saved"
page both print the whole pinned url. A board provisioned over USB has no
`zero.json` at all, and its writes stay open, exactly as before.

## Pins (`zero_gpio.py`, #9)

The other half of "the browser is the console, this board is what a browser is
not". A cart running in the browser calls `pin_write` / `pin_read`; the page
batches them and POSTs to whoever served it.

```
GET  /gpio   → {"v": 1, "pins": [...]}                     the allowlist
POST /gpio   → {"v": 1, "ops": [...], "pin": "1234"}
             → {"ok": n, "reads": {"<pin>": 0|1}, "err": [...]}
```

Digital in and out only. **The allowlist is the security model** — `1, 2, 4, 5,
6, 7, 8, 9` (the pads `D0 D1 D3 D4 D5 D8 D9 D10`) plus `21`, the on-board user
LED. A pin outside it is refused and never touched, because the pins left out
are the ones the board is running on: `26–37` (flash + octal PSRAM), `19/20`
(the USB device this board is reached through), `43/44` (`D6`/`D7`, UART0,
where MicroPython keeps the REPL that is the recovery path), `0/45/46`
(boot-mode and VDD_SPI strapping) and `3` (`D2`, JTAG-source strapping — the
one *exposed* pad held back, on the strict reading; `zero_gpio.PINS` carries
the argument for re-admitting it).

Two behaviours worth knowing, both in `pin_factory`'s docstring: **a read never
reconfigures a pin** (so `pin_write(21, 0)` then `pin_read(21)` answers 0 and
leaves the LED lit — the other way round, reading a light turns it off), which
means a written pin stays an output until reboot; and **an input is pulled up**,
so an unwired pin reads 1 and a button to ground reads 0.

## Verified where

Hardware, on this board (2026-08-25), and host tests for everything else —
`tests/test_zero_setup.py` and `tests/test_zero_gpio.py`, 80-odd cases over the
parsing, the refusals, the persisted shapes and the browser-side queue.

**On glass:**

- The whole seed roster: `provision.sh` then `carts.json` → **36 cart folders /
  132 files** (all 35 in `system_carts/`, plus one made in the browser on an
  earlier session and synced back — which is the pull and push halves both
  still working).
- `GET /gpio` and the empty-batch probe answer the allowlist; a write→read
  round trip on GPIO 2 reports `1` then `0`; an untouched pin reads `1` through
  its pull-up; GPIO 21 (the LED) takes both levels; `44 / 30 / 19 / 3` are each
  refused **by name** in one batch whose fifth op still applied; junk is 400
  and `PUT` is 405.
- First-run setup, with `/moy/wifi.json` moved aside: the board came up on
  `ZERO SETUP  join 'moybyte-zero-973d' (open) then open http://192.168.4.1/`,
  and an on-device threaded loopback against that AP IP got `GET /` → 200 (the
  real form, naming the AP), `GET /scan` → 200 listing the real networks around
  the desk (an STA scan running beside a live AP, which is the part that could
  only be tested here), `POST /setup` → 200 with the pinned url and the parsed
  fields, and a bad pin → 400 saying "4 digits" with nothing saved. Credentials
  restored afterwards; the board is back on STA serving the full store.

**NOT verified on hardware**, deliberately:

- **A phone has never joined this AP.** The HTTP side was driven from the board
  itself, so what is proven is the server, the form, the scan and the parsing —
  not the phone's association, its captive-portal probe, or how the page looks
  on a small screen.
- **The reboot-into-STA leg of a real setup.** The loopback saves into a list,
  not onto flash, and never calls `machine.reset()` — running the genuine
  `run()` would have overwritten the board's real credentials with a made-up
  network. `save_setup` is covered by a host test that round-trips real files.
- **The LED's polarity.** Pin 21 takes both levels and reads them back; nobody
  looked at the board. It is documented active-low from the Seeed schematic.
- **The browser end against this board.** `gpio_link` + the worker's probe and
  pump are host- and syntax-checked only: `firmware/web_runner/dist` needs
  emsdk and was not built here, so no page has yet driven a pin. The wire shape
  between the two ends is pinned by a test that runs a real batch out of the
  browser queue and into `zero_gpio.handle`.

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
