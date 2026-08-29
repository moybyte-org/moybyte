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

## A real board since 2026-08-29 — and what that replaced

This board spent 2026-07..2026-08 running **stock MicroPython** with the shared
modules PUSHED here as plain files by `provision.sh`. There was no ESP-IDF
build, no frozen image and no OTA, and `board.toml` said so in capitals:
*"DELIBERATELY NOT A BUILD TARGET … If this board ever earns a frozen image, it
graduates to the ordinary port-kit shape and this file grows the sections."*

That was the right shape for a spike. A push is a sub-minute dev loop, an
ESP-IDF build is not, and nothing needed freezing that a `cp` could not deliver.
**The owner reversed it on 2026-08-29**, and the graduation is the one that
paragraph predicted: `build.sh` over `tools/esp32_build_lib.sh`, a
`boards/MOYBYTE_ZERO/` board def, and `board.toml` grown its `[modules]` /
`[native]` / `[flash]` sections.

What ended the no-build arrangement is that the board stopped being a spike and
became the thing a kid's carts actually live on. Three properties a pushed tree
cannot have started to matter:

- **The browser console rides the image.** `native/moy_web` bakes the gzipped
  bundle into the firmware so the page a board serves cannot drift behind the
  board serving it. That has been true of every console board since 2026-08-15;
  this board had no image, so it was the last place the drift survived — its
  bundle was only ever a copy somebody pushed.
- **A second app slot**, so the board can be updated by anyone who is not
  holding a cable. It is headless and lives on a shelf; a cable is a real cost.
- **One declaration of what it runs.** The module set was a `cp` list in a shell
  script — the exact thing that falls behind the code it is a list of, which had
  already happened here once (`moy_store.mjs` became an asset the worker
  statically imports and the hand-list missed it the same day, which is a
  console that cannot boot). `board.toml` is the declaration now, and
  `tests/test_staging_closure.py` derives this board's frozen set from it and
  asserts every import of every staged module resolves on this target — the same
  net all three console boards ride.

`provision.sh` survives and **changed jobs**: it provisions the STORE (carts,
credentials, the pairing pin) and keeps the module push as an opt-in dev loop.
See "Provisioning" below.

## Build, flash, look

```bash
make firmware-build-zero                             # -> dist/zero/
make firmware-flash-zero PORT=/dev/ttyACM0           # merged image at 0x0
make firmware-monitor-zero PORT=/dev/ttyACM0         # the only way to watch it
```

`tools/board_flash.py` reads the offsets from this board's `board.toml`
`[flash]` block and erases otadata first, so a board that has taken an OTA boots
the slot the cable flash just wrote.

**The migration flash WIPES THE STORE.** The new partition table puts `vfs` at
`0x5A0000`; the stock MicroPython table it replaces put it far lower, so the old
filesystem is not where the new image looks and comes up freshly formatted. Run
`./provision.sh` afterwards to put the seed roster, the credentials and the pin
back. A kid's browser-made carts are not lost by this — the browser keeps its
own copy in OPFS and syncs them back on the next visit — but anything that
existed *only* here is.

### The image

- **8MB flash, two OTA slots** (`boards/MOYBYTE_ZERO/partitions-moybyte-zero.csv`).
  The console S3 layout does not fit 8MB, and the failure is not a build error:
  the bootloader rejects the table and the board boot loops with no console at
  all. That was learned here — see the hardware facts below — so this table is
  authored, not inherited.
- **What makes two slots fit** is that the image is headless: no console, no
  canvas, no seed carts, and only one shared native module. Measured on the first
  build, 2026-08-29: **2,158,384 B of a 2,883,584 B slot, 708 KB headroom**,
  against the Guition's 3,668,096 B the day before. The CSV carries the full
  arithmetic and what would falsify it; the build prints the headroom on every
  run and fails on an overflow (#168).
- **The frozen set is `board.toml`.** This is the one board whose
  `[modules.shared]` is an ALLOWLIST rather than a denylist, and its board file
  argues the case at length: a denylist is right when the source tree's default
  answer is yes, `runtime/` IS the console, and this board is not one.
- **One shared C module, `moy_web`.** The other seven are denied with the
  hardware or the workload that is missing.

## Provisioning (the store, not the modules)

```bash
./provision.sh [--modules] [--web] [--clean] [/dev/ttyACM0] [path/to/wifi.json]
```

Default: make the directories, copy the whole seed roster into `/moy/carts`,
optionally write the credentials, mint or keep the pairing pin, reboot, and
print the paired url.

`wifi.json` is the console's own store shape
(`{"networks": [{"ssid", "password"}]}`) and is a secret — never in the repo.
Reading one off a console board over its dev channel works. **Omitting it is a
supported answer**: a board with no credentials hosts its own setup AP (below),
and re-running the script without the argument leaves an existing
`/moy/wifi.json` alone.

The script **mints a pairing pin** when the board has none, and prints the
paired url at the end. That is the whole gesture: since 2026-08-25 the pin gates
everything but the console's boot files, so a page opened without it can read
nothing. Minted on the board — the "is there one?" check and the write are one
read of one filesystem — and **kept** when there is one, so re-provisioning
never rotates a pin somebody has already scanned.

### The flags, and the one hazard they exist for

`--modules` pushes the image's own module set as plain files, and `--web` pushes
the wasm bundle. Both are **dev loops**, and both are the same trade the bundle
already makes on every board: **storage WINS, so the image is the guarantee and
not the ceiling.** A push is a second, a reflash is minutes.

The hazard is that MicroPython searches `/` before `.frozen`, so a pushed `.py`
outranks the image's own copy **on every boot after it**, silently and forever,
while every diagnostic still points at the firmware. Three things answer that:
the push is a flag rather than the default, `--clean` removes exactly what
`--modules` put there, and `zero_host.serve()` prints a loud
`ZERO NOTE: pushed copies are SHADOWING the image for: …` line at boot.

Neither list is written in the script. The module list comes from `board.toml`
(the same call the build stages from) and the asset list from
`moy_webhost.ASSETS`; a hand-list is what broke this board once already.

## Updates, on a board with no screen

Every other board triggers and reports an update on its own glass — Settings →
UPDATE ONLINE, a progress bar, and a banner naming what the last install did.
This one has no glass, so the same `moy_ota.OtaUpdater` is driven by
`zero_host.ZeroUpdate` and reported as JSON.

**The trigger is a request, not a timer.**

```
GET  /update?pin=NNNN   → the running version/label/channel/slot, the PREVIOUS
                          install's verdict, and live progress
POST /update            → {"action": "check" | "install" | "cancel", ...}
                          answers immediately; the work runs in the poll loop
```

Both methods are gated. There is deliberately **no read-half exemption**: what a
GET reveals is which firmware a specific board on somebody's home network is
running, which is a shopping list for whoever wants to hand it an image — the
same call `/gpio`'s GET was brought under on 2026-08-25. The doctrine and its
reasoning live in `device/moy_webhost.py`'s SECURITY docstring; this endpoint is
an instance of it.

**A boot-time check runs, and installs nothing.** Once the running image has
certified itself (below), the board checks the manifest once, prints the result
on serial, and caches it for `GET /update`. Installing takes a request carrying
the pin — the same act of consent that gates every other write here. The one
exception is opt-in: `"ota_auto": true` in `/moy/zero.json` makes the boot check
install what it finds. **It is OFF by default**, because an unattended firmware
replacement on the board holding a kid's only local copy of their carts should
be something somebody chose. ON is a defensible setting for a board that lives
on a shelf and is never reached for; it is a one-word edit.

**How a human learns it happened**, three ways, none of them a UI:

- **serial** — every transition prints a `ZERO ota:` line, and the boot line
  names the running label. The cable is how a pin reaches a human on this board
  anyway.
- **`GET /update?pin=`** — including `last`, the previous install's verdict:
  `ok` (the slot we pointed the bootloader at is the one running) or
  `rolled_back` (the bootloader gave up on it). That is the headless replacement
  for the console's notice banner, and it is why the pending marker is cleared
  at the CONFIRM rather than at the read.
- **the page itself** — the console is baked into the image, so a board that
  updated is serving a different console the next time it is opened.

**The rollback confirm is the one piece of OTA that could not be reused.**
`confirm_when_healthy` waits for frames on the glass, and this board paints
none: a constant there would certify every image unconditionally, and a zero
would roll every image back. So `moy_ota.confirm_when_serving` takes the
evidence this hardware can give — the store host is up and listening on a joined
network — and counts poll iterations survived after it. An image whose host
never comes up never confirms, which is exactly the image the bootloader should
take back.

The board id inside a signed manifest is **`xiao_zero`**, which is also its CI
matrix row and the `latest-xiao_zero.json` a device asks for. Channels are the
repo's two: `dev` → beta/`unstable`, `master` → stable.

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
into `/moy/zero.json`. From then on the name is the mDNS label and the pin is
the whole access control.

## The pin gates everything (2026-08-25)

The doctrine and its reasoning live in `device/moy_webhost.py`'s SECURITY
docstring, which this board's host is an instance of. What is true *here*:

- **Every Zero has a pin.** Setup's form mints one; `provision.sh` mints one
  for a board that never saw that form. The old "no `zero.json`, so writes stay
  open" state is gone — once reads are gated too, a pinless board is one
  handing its entire store to whoever is on the WiFi.
- **`/gpio` and `/update` are gated on both methods**, which is this board's own
  addition to the table — see the Pins and Updates sections for why neither
  `GET` is free.
- **Nothing here can show the pin to a human.** No screen: it reaches you over
  the cable (`provision.sh`'s paired url, the boot line) or off the setup form
  you just filled in. Keep the url; opening the bare address is not a dead end,
  but it costs you a prompt and a typed four digits.
- **What stays open is the console's own boot files plus `GET /sync`.** A
  browser has to load the page before it can ask for anything, and the page has
  to know a board is here before it knows to ask.

## Pins (`zero_gpio.py`, #9)

The other half of "the browser is the console, this board is what a browser is
not". A cart running in the browser calls `pin_write` / `pin_read`; the page
batches them and POSTs to whoever served it.

```
GET  /gpio?pin=1234  → {"v": 1, "pins": [...]}             the allowlist
POST /gpio           → {"v": 1, "ops": [...], "pin": "1234"}
             → {"ok": n, "reads": {"<pin>": 0|1}, "err": [...]}
```

Both are gated. The `GET` was open until 2026-08-25 on the reasoning that it
changes nothing; what it hands over is this board's wiring, which is a fact
about somebody's house — and the page never needed it, because it probes with
an empty `POST`, which was always gated.

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

**The frozen image has never run on hardware.** It was built on 2026-08-29 —
that is a real build, with real numbers (the sizes above come out of it), and it
is the *only* thing about the image that has been checked. No Zero has been
flashed with it. Everything in the two lists below that is dated 2026-08-25 was
measured on the **pushed** arrangement this build replaces; the modules are the
same files, so what those runs prove about `moy_sync`, the pin gate and the
files layer still holds, and what they say nothing about is the image: the
partition table, the console arrangement, WiFi with this sdkconfig, the OTA
endpoints on real flash, and the rollback confirm.

Host tests cover the rest: `tests/test_zero_setup.py`, `tests/test_zero_gpio.py`
and `tests/test_zero_update.py` (110-odd cases over the parsing, the refusals,
the persisted shapes, the browser-side queue and the update state machine), plus
`tests/test_zero_provision.py` and this board's row in
`tests/test_staging_closure.py`.

**On glass (2026-08-25, on the pushed arrangement):**

- The whole seed roster: `provision.sh` then `carts.json` → **36 cart folders /
  132 files** (all 35 in `system_carts/`, plus one made in the browser on an
  earlier session and synced back — which is the pull and push halves both
  still working), 763KB in 11.6s over the LAN.
- **The pin gate**: `GET /carts.json`, `GET /files.json`, `GET /gpio` and
  `POST /sync` each 403 `{"error":"pin"}` with no pin and answer with one;
  `GET /sync` and `GET /` stay open. **And the prompt**, in real headless
  Chrome against this board: a page opened at `http://<ip>:8080/?handheld=1`
  refuses to boot, shows the in-page prompt, and after four digits reloads with
  `?pin=` and comes up on the launcher (`mode: board`, 30 tiles).
- **The files layer and the journal**: a `{"v":2,"root":"files"}` batch put a
  drawing in `/moy/files/drawings/` and `files.json` served it back; two carts
  pushes left a real `journal/` on the board (`journal.jsonl` + `cursor.json` +
  per-commit snapshots under `s/`, the identical second manifest correctly
  deduped to no entry), and `journal_undo`/`journal_redo` walked the live
  `main.py` back and forward between the two browser-era commits. Both test
  artifacts removed afterwards through the same endpoint.
- All nine pushed modules imported cleanly on-device.
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
  fields, and a bad pin → 400 saying "4 digits" with nothing saved.
  Credentials restored afterwards.

**NOT verified on hardware**, and each for its own reason:

- **The image, end to end.** Nothing below has been on this glass: the new
  partition table (a bootloader that rejects a table boot loops silently — this
  board's own worst failure mode), WiFi under this board's sdkconfig, the
  TinyUSB CDC console in a Moybyte build, the baked bundle being served, the
  OTA endpoints, and the rollback confirm. **The first flash is the check**, and
  it is worth doing with `make firmware-monitor-zero` already attached.
- **A phone has never joined the setup AP.** The HTTP side was driven from the
  board itself, so what is proven is the server, the form, the scan and the
  parsing — not the phone's association, its captive-portal probe, or how the
  page looks on a small screen.
- **The reboot-into-STA leg of a real setup.** The loopback saves into a list,
  not onto flash, and never calls `machine.reset()` — running the genuine
  `run()` would have overwritten the board's real credentials with a made-up
  network. `save_setup` is covered by a host test that round-trips real files.
- **The LED's polarity.** Pin 21 takes both levels and reads them back; nobody
  looked at the board. It is documented active-low from the Seeed schematic.
- **A cart in a browser driving this board's PINS.** The console half is
  proven against this board (a real Chrome session pulled its store and came up
  on the launcher), but nothing has yet called `pin_write` from a running cart,
  so `gpio_link` + the worker's pump remain host-checked. The wire shape between
  the two ends is pinned by a test that runs a real batch out of the browser
  queue and into `zero_gpio.handle`.

## Hardware facts (learned the painful way — respect these)

- **The REPL is silent unless the host asserts DTR.** A raw pyserial client
  must set `dtr=True`; mpremote does. Looks exactly like a dead board. This
  survives the graduation on purpose: the image keeps MicroPython's TinyUSB CDC
  console rather than adopting the console boards' USB-Serial/JTAG promotion,
  because that promotion exists for a board that never returns to the REPL and
  this one is interrupted into the REPL every time it is provisioned. The
  reasoning is in `boards/MOYBYTE_ZERO/mpconfigboard.h`; flipping it would
  change both the USB id and the DTR rule, and is an A/B for somebody holding
  the board.
- **Getting OUT of the ROM loader needs no replug after all** (2026-08-25):
  `esptool --after watchdog_reset` exits download mode cleanly on this board.
  The old note ("only a physical replug exits ROM mode") predates it — that
  was `hard_reset`, which indeed does nothing here. Prefer
  `machine.bootloader()` to get IN (an esptool DTR-dance against the running
  TinyUSB CDC has wedged the USB device before). The BOOT button is the last
  resort: hold it while powering on.
- **The console dual-OTA partition table does not fit this 8MB flash**, and the
  failure is silent: the bootloader REJECTS the table and the board boot loops
  with no console. That is why this board has its own CSV rather than the
  siblings', and it is why the first flash of a new table deserves a monitor.
  (The stock MicroPython single-app 8MB table this board used to run had no such
  problem, and no second slot either.)
- **STA, not SoftAP.** The old streaming port measured SoftAP throughput as
  the cause of its multi-second stalls. Serving a ~630KB gzipped bundle wants
  the router; a board with no saved network hosts the setup AP instead.
- **No BLE in this image, and it is not a preference.** Dropping
  `boards/sdkconfig.ble` removes the IDF component but not MicroPython's nimble
  sources, so `MICROPY_PY_BLUETOOTH` has to be cleared in `mpconfigboard.h` as
  well — measured on this board's first build, which failed at
  `nimble/nimble_port.h: No such file or directory`. The two are halves of one
  decision, and the payoff is the internal RAM the Guition's fragment records
  BLE eating (that board had four bytes free with the stack active, and WiFi
  could not initialise at all).

## What is deliberately absent

- **No card for this board in the website's flasher.** `site/build.py`'s
  `BOARDS` table is what the page's Web Serial flasher writes from *and* what
  `tools/publish_firmware_release.py` publishes cable-flash images from, and the
  Zero is not in it. So the OTA half works today and the "flash it from the
  website" half does not exist — a gap, not a decision, and the one follow-up
  this board's promotion left open on purpose.
- **No way for the BOARD to show its own pin.** It has no screen, so the pin
  reaches a human over the cable (`provision.sh`'s paired url, the serial line
  at boot) or off the setup form they just filled in. A QR on a sticker, or a
  button that re-prints it, is still #41's. Until then: keep the url.
- **No `files/` UI of its own** — this board *stores* the #108 layer and
  serves it; the browsing, editing and undo of those files happen in the
  console that pairs with it. Note the asymmetry the sync stack carries: the
  carts root has a real journal here, the files root's `.history/` sidecars
  are a different mechanism and are neither synced nor written by the
  receiver.
- **No on-glass suite.** The other three boards have one
  (`tests/test_*_on_glass.py` over the shared dev channel); this board has no
  console, no `DevChannel` and no frames to assert about. Its equivalent is
  `GET /update` and `GET /carts.json` over the network, which is what a person
  would check by hand anyway.
