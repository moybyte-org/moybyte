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

`provision.sh` survives and **changed jobs**: it provisions the credentials and
the pairing pin, and keeps the module push as an opt-in dev loop. The CARTS
stopped being its job on 2026-08-30 — the image carries a compressed seed
roster and inflates it on first boot — so what is left there is a safety net and
a re-push flag. See "Provisioning" below.

## Build, flash, look

```bash
make firmware-build-zero                             # -> dist/zero/
make firmware-flash-zero PORT=/dev/ttyACM0           # merged image at 0x0
make firmware-monitor-zero PORT=/dev/ttyACM0         # the only way to watch it
```

`tools/board_flash.py` reads the offsets from this board's `board.toml`
`[flash]` block and erases otadata first, so a board that has taken an OTA boots
the slot the cable flash just wrote.

**The website flashes this same image**, since 2026-08-29 — `site/build.py`'s
`BOARDS` carries the card, and `tools/publish_firmware_release.py` reads that
same table to publish the asset the page serves. The one field pair that is not
the Guition's, on the same chip, is the reset: the page asks for `no_reset` and
offers no reset afterwards, because esptool-js picks its sequence off the USB
PID and would otherwise put the classic DTR/RTS dance on this board's running
CDC — the wedge in the hardware facts below. So the browser's gesture is the
BOOT button held while plugging in, and a replug afterwards.

**The migration flash WIPES THE STORE.** The new partition table puts `vfs` at
`0x5A0000`; the stock MicroPython table it replaces put it far lower, so the old
filesystem is not where the new image looks and comes up freshly formatted. Run
`./provision.sh` afterwards to put the credentials and the pin back; the seed
roster the image carries seeds itself on the next boot. A kid's browser-made
carts are not lost by this — the browser keeps its own copy in OPFS and syncs
them back on the next visit — but anything that existed *only* here is.

### The image

- **8MB flash, two OTA slots** (`boards/MOYBYTE_ZERO/partitions-moybyte-zero.csv`).
  The console S3 layout does not fit 8MB, and the failure is not a build error:
  the bootloader rejects the table and the board boot loops with no console at
  all. That was learned here — see the hardware facts below — so this table is
  authored, not inherited.
- **What makes two slots fit** is that the image is headless: no console, no
  canvas, and only one shared native module. Measured on the first build,
  2026-08-29: **2,158,384 B of a 2,883,584 B slot, 708 KB headroom**, against
  the Guition's 3,668,096 B the day before. The CSV carries the full arithmetic
  and what would falsify it; the build prints the headroom on every run and
  fails on an overflow (#168).
- **The seed roster is in the image, COMPRESSED** (2026-08-30). This board
  carried no carts at all until then, and both forms were **built** to find out
  why. With the plain `carts_data.py` the console boards freeze, this image is
  **2,830,672 B of the 2,883,584 B slot — 51 KB left**: it fits, under the #168
  warning floor, one cart from a build failure, in a slot paid for twice. With
  the same 35 carts as one raw-deflate stream each it is **2,399,232 B — 473 KB
  left**, against **2,194,112 B / 673 KB** for the image that carried no roster
  at all. `tools/gen_device_carts.py --packed` emits it (201,716 B of blobs
  against 731,592 B of plain source); `zero_host.seed_carts()` inflates it into
  an **empty** store on first boot, one cart at a time, through MicroPython's
  built-in `deflate`.
- **The frozen set is `board.toml`.** This is the one board whose
  `[modules.shared]` is an ALLOWLIST rather than a denylist, and its board file
  argues the case at length: a denylist is right when the source tree's default
  answer is yes, `runtime/` IS the console, and this board is not one.
- **One shared C module, `moy_web`.** The other seven are denied with the
  hardware or the workload that is missing.

## Provisioning (the store, not the modules)

```bash
./provision.sh [--modules] [--web] [--carts] [--clean] [/dev/ttyACM0] [wifi.json]
```

Default: make the directories, optionally write the credentials, mint or keep
the pairing pin, reboot, and print the paired url. It copies the seed roster
**only onto a board that has no carts at all** — the image now writes out
whatever its store is missing on every boot, so this is the safety net for an
image built before the roster was baked in, and the emptiness question is asked
on the board with the same read `store_is_empty()` does. `--carts` forces the push, for a roster that moved since the image was
built; forcing it writes the repo's copy of a cart over whatever is on the
board, which on the one board that is the store *of record* is a real thing to
mean.

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

`--modules` pushes the image's own module set as plain files, `--web` pushes the
wasm bundle, and `--carts` re-pushes the seed roster. All three are **dev
loops**, and all three are the same trade the bundle already makes on every
board: **storage WINS, so the image is the guarantee and not the ceiling.** A
push is a second, a reflash is minutes.

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

**The two routes are `device/moy_webhost.py`'s, on every board** (2026-08-29).
They lived here alone until then, which made "the browser can update the board
that serves it" true on exactly one board of four. What stays this board's own
is the BACKEND behind them: `ZeroUpdate` drives the install slice by slice in
the poll loop, where a board with a screen instead hands its glass back
(`moy_webhost.ConsoleUpdate`) and lets its own update screen do the work. The
two are not one class behind a flag, for the reason `confirm_when_serving` is
not one with `confirm_when_healthy`: the backend is where the claim about this
hardware lives.

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
the pin — the same act of consent that gates every other write here.

**There is no auto-install, and there was one for a day.** `"ota_auto": true`
in `/moy/zero.json` made the boot check install what it found, off by default;
the owner deleted it on 2026-08-29. It was a one-board divergence — no other
board in this tree has any auto-install concept, and a console board takes two
deliberate human acts, opening the update screen in Settings and confirming —
and nothing ever wrote the flag, so reaching it meant hand-editing a JSON file
over the cable. Unattended firmware replacement, on the board holding the only
local copy of a kid's carts, down a path that had never run on hardware, is not
a thing to carry even switched off. **Where the request comes from instead: the
browser console this board serves** (landed 2026-08-29). The browser is this
board's screen, so the page carries a firmware strip that shows the running
version and the previous install's verdict, asks the board to look, offers what
it found, and then displays the download and the flash as they happen. Two
taps, which are this board's spelling of the two the other boards take on
glass.

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
     → join it from a phone; the form opens by itself (or open http://192.168.4.1/)
     → name + 4-digit pin + a network from the scan + its password
     → saved → reboot → STA, as normal
```

The form is one small self-contained page on **port 80** (a phone types
`192.168.4.1`, not `192.168.4.1:8080`) — the same argument that moved the
serving half onto 80 on 2026-08-29, so both are there now and never collide,
because a board either serves or hosts this AP and never both. It is a plain
`<form>` — the page's only script fills the network list, offers the reveal on
the password field and tells you what the scan found, so a phone with a broken
script still has a page with a correct static state to type into. The AP is
**open on purpose**: there is nothing behind it but the form, it exists only
while the board is unconfigured, and the one secret that crosses it belongs to
the person standing next to the board. What it costs is that a neighbour in
range during that minute could configure the board first; what it buys is a
setup that needs no printed key and no instructions.

**It is a captive portal since 2026-08-29, reversing this port's own recorded
decline** (the argument, its price and what would reverse it back live in
`zero_setup.py`'s docstring, which quotes the decline verbatim). `DnsRedirect`
answers every name on :53 with the AP's address and every unserved `GET` is a
302 to `http://192.168.4.1/`, so a phone's connectivity probe reaches this board
and gets an answer it did not expect — which is what makes both platforms open
the form without anybody typing an address. It works because ESP-IDF's SoftAP
DHCP already hands out the AP's own address as the DNS server
(`CONFIG_LWIP_DHCPS_ADD_DNS`, `y` in this board's generated sdkconfig), so
nothing has to be configured for the queries to arrive — which is fortunate,
since MicroPython exposes no `esp_netif_dhcps_option` binding at all, RFC 8910's
option 114 included. **The portal is optional at every step**: a responder that
cannot bind :53 prints one line and setup serves exactly as it did before, and
answers carry TTL 0 so nothing this board says about a name outlives the phone's
stay on the AP.

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
and `tests/test_zero_update.py` (140-odd cases over the parsing, the refusals,
the persisted shapes, the captive portal's packets, the browser-side queue, the
update state machine and one whole first run end to end), plus
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
  Chrome against this board: a page opened at `http://<ip>/?handheld=1`
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

**Verified on this board on 2026-08-29/30**, which is most of what the list
below used to hold:

- **The image, end to end.** The authored dual-OTA table boots — no silent
  bootloader loop — and the board comes up on WiFi, serves the baked bundle, and
  answers `GET /update` with a real verdict (`no build published on this channel
  yet`, which is the correct answer to a channel with nothing on it, and proves
  the endpoint rather than the install).
- **A phone joins the setup AP and the portal opens itself.** The sign-in sheet
  appeared on association with no address typed, the form was usable one-handed,
  and submitting it rebooted the board onto the home network — the whole
  first-run leg, on a real phone, which is the one thing no host could cover.
- **The LED is ACTIVE-LOW**, confirmed by looking at the board rather than by
  reading the schematic: `{"p":21,"v":0}` lights it. So a cart writing 1 to
  "turn it on" turns it off, which is why `Pin Light`'s `_apply()` inverts.
- **A freshly flashed board is not an empty board.** The compressed roster
  (`carts_data.CARTS_Z`) inflated on first boot with no cable step: 36 carts
  served from `GET /carts.json` on a board that had just been erased. This was
  the last thing standing between "flashed" and "usable" and it is gone.
- **The seed adds what is missing and rewrites nothing.** A later image carrying
  one new cart (`Pin Light`) was flashed onto a board holding 43: all 36 roster
  carts ended up present, the new one among them, and the eight browser-made
  carts beside them — an imported PICO-8 Celeste included — were untouched.
- **The rollback confirm fires on real flash** (checklist step 7): `ZERO ota:
  this image confirmed itself -- rollback cancelled`, once, right after the
  store host comes up.
- **The online OTA check reaches the internet**, not merely the endpoint: DNS,
  TLS to github.com, the request, and a real `http status=404` read back for a
  channel with nothing on it — which is the correct answer, and every layer
  under it had to work to produce it.

**NOT verified on hardware**, and each for its own reason:

- **An OTA install on this board.** The endpoints answer, the whole online path
  to the manifest works and the rollback confirm fires — but the channel is
  empty, so nothing has been downloaded into the inactive slot here yet. That
  needs a published build, not a bench: step 6 below runs at the first release.
- **A cart in a browser driving this board's PINS.** The console half is
  proven against this board (a real Chrome session pulled its store and came up
  on the launcher) and the LED takes both levels over `POST /gpio`, but nothing
  has yet called `pin_write` from a running cart, so `gpio_link` + the worker's
  pump remain host-checked. The wire shape between the two ends is pinned by a
  test that runs a real batch out of the browser queue and into
  `zero_gpio.handle`, and `Pin Light` (`system_carts/pin_light.moy`) is the cart
  that closes it — step 5 below.

**The reboot-into-STA leg LEFT this list on 2026-08-29**, and it is worth
saying how, because the same move is available to the rest of it. The on-glass
loopback saved into a list and never called `machine.reset()` — running the
genuine `run()` on the desk would have overwritten that board's real
credentials with a made-up network — so four pieces were each proven and the
SEQUENCE was not, which is the half that reboots a board. It is now covered on
the host by `test_a_whole_first_run_lands_on_flash_and_reboots_in_that_order`:
the real `run()`, the real transport over a real socket, real files in a tmp
dir, with only the radio and the reset injected. It pins that a refusal writes
nothing and does not arm the reboot, that the phone has the whole answer
**before** the reset (the files are read inside the fake `machine.reset()`, so
"before" is a fact and not an inference), that the new network goes first and
the old one is kept, and the order the AP and both sockets come down in. What
is left on this board is the radio: that the association happens and that the
reboot comes back up on STA, which is item 1 of the checklist below.

## The bench checklist (one board, one phone, one afternoon)

What the two lists above leave is genuinely hardware-only. This is the order to
do it in — each step's failure would otherwise be mistaken for the next step's
— with what a PASS looks like and what a FAILURE looks like, because on a board
with no screen those are easy to confuse. Attach the monitor first and leave it
attached for all of it:

```bash
make firmware-build-zero
make firmware-flash-zero PORT=/dev/ttyACM0
make firmware-monitor-zero PORT=/dev/ttyACM0
```

**0. The image boots at all.** *Pass:* the monitor prints a MicroPython banner
and then a `ZERO ...` line within a few seconds. *Fail:* a repeating
`rst:0x3 (RTC_SW_SYS_RST)` / bootloader banner loop with nothing after it —
that is the partition table being rejected, this board's worst failure mode,
and it is a build-side fix (the CSV), not anything below. Silence with no
banner at all is usually the DTR rule, not a dead board: a raw pyserial client
must set `dtr=True`.

**1. A phone joins the setup AP and the form opens itself.** A board straight
off the migration flash has no credentials and is already here — the flash
wipes the store. On a board that has since been provisioned, move them aside
over the cable and reset:

```bash
mpremote connect /dev/ttyACM0 fs cp :/moy/wifi.json wifi.json.bak
mpremote connect /dev/ttyACM0 fs rm :/moy/wifi.json
mpremote connect /dev/ttyACM0 reset
```

*Pass:* the monitor prints `ZERO no wifi -- hosting the setup AP` and then
`ZERO SETUP  join 'moybyte-zero-XXXX' (open) and the form opens itself -- or
open http://192.168.4.1/`; the phone's WiFi list shows that SSID; joining it
pops the sign-in sheet within ~10s showing the form, with the AP name in the
subtitle. *Fail, and which is which:*
- the line says `-- or open http://192.168.4.1/` **without** "and the form
  opens itself" → the :53 bind failed and a `no captive portal (dns: …)` line
  says why. Everything else still works; type the address.
- the SSID never appears → the AP itself, not the portal.
- the phone joins, says "no internet", and no sheet appears → the probe is not
  reaching us. Test the responder directly from a laptop on the same AP:
  `dig @192.168.4.1 connectivitycheck.gstatic.com +short` should answer
  `192.168.4.1`. If it does, the phone is not asking us (a private-DNS
  setting); if it times out, the responder is not answering.
- the sheet appears but is blank → the page, not the portal. `curl -v
  http://192.168.4.1/` from the laptop.

**2. The page is usable one-handed.** *Pass:* nothing needs pinch-zoom to read,
tapping a field does not zoom the page, the pin field brings up a number
keypad, the network list shows real SSIDs as tappable chips, tapping one fills
the field, and "show password" reveals what was typed. *Fail:* the page zooms
on focus (an input smaller than 16px slipped in); the chips are absent and the
hint still reads "Looking for networks..." (the `/scan` fetch never resolved —
check `ZERO SETUP scan failed:` on the monitor); the hint reads "No networks in
range" while the phone can see plenty (the STA scan beside a live AP is what
that means, and it is the one thing only this hardware can test).

**3. The reboot-into-STA leg.** Fill the form in with a real network and submit.
*Pass:* the "saved" page names the network and shows `http://<name>.local/
?pin=NNNN`; the monitor prints `ZERO SETUP saved: name=… ssid=… -- rebooting`
and then, after the reset, `ZERO wifi: <ssid> <ip>` and `ZERO serving
http://<ip>/?pin=NNNN`. Then check what landed:
`mpremote connect /dev/ttyACM0 fs cat :/moy/wifi.json` — the network just typed
must be **first** and any older one still there — and `… fs cat :/moy/zero.json`
→ `{"name": …, "pin": …}`. *Fail:* the board reboots straight back into the
setup AP → it saved but cannot join (a wrong password: the whole reason for the
reveal in step 2); the page hangs on submit with nothing on the monitor → the
POST never arrived; `ZERO SETUP save failed:` → the filesystem, and the board
stays on the AP so it can be retried. Restore the real credentials afterwards
(`mpremote connect /dev/ttyACM0 fs cp wifi.json.bak :/moy/wifi.json`) or run
`./provision.sh`.

**4. The LED's polarity.** ANSWERED 2026-08-30: it is **active-low**, confirmed
by watching the board rather than by reading the schematic. Kept here because it
is the check to repeat on a board that behaves oddly. With the board serving and
its pin known:

```bash
curl -s -X POST http://<ip>/gpio \
  -d '{"v":1,"pin":"NNNN","ops":[{"p":21,"mode":"out","v":0}]}'
# -> {"ok": 1, "reads": {}, "err": []}
```

*Pass:* the on-board user LED **lights** on `v:0` and goes out on `v:1` —
that is active-low, and it means a cart writing 1 to "turn it on" turns it off.
*Fail:* the opposite, in which case this board differs from the one this was
measured on and `zero_gpio.PINS`' note about pin 21 needs correcting. Note
`pin_write(21, 0)` then `pin_read(21)` answers 0 and leaves the LED as it is —
a read never reconfigures a pin.

**5. A cart in a browser drives a pin.** `Pin Light` ships in the roster for
exactly this — open the paired url, open it from the launcher, tap the button.
(Or write your own calling `pin_write(21, 0)` / `pin_write(21, 1)` on a timer.) *Pass:* the LED blinks in step with the cart, and the network tab shows
`POST /gpio` batches carrying the pin. *Fail:* 403 `{"error":"pin"}` → the page
was opened without `?pin=`; batches leaving with no LED → step 4's polarity;
no batches at all → `gpio_link` or the worker's pump, which is the half that
has only ever been host-checked.

**6. The OTA endpoints on real flash.** Both methods are gated and both read
the pin off the QUERY, the POST included:

```bash
curl -s "http://<ip>/update?pin=NNNN"
curl -s -X POST "http://<ip>/update?pin=NNNN" -d '{"action":"check"}'
```

*Pass:* the GET returns JSON naming the running version/label/channel/slot and
`"state": "idle"`, plus `"last"` once an install has happened; the POST answers
`{"ok": true, "message": "queued", …}` **immediately** and the monitor prints a
`ZERO ota:` line a moment later. *Fail:* `403 {"error":"pin"}` → the pin, and
note it is not in the body here; `503 {"error":"no updater"}` → `make_updater`
failed at boot and printed why; a POST that hangs for tens of seconds → it is
doing the download inline, which is exactly the shape it is written not to have.

**7. The rollback confirm.** After an install, watch for `ZERO ota: this image
confirmed itself -- rollback cancelled`. *Pass:* it appears once, shortly after
the store host comes up. *Fail:* it never appears and the next reset comes back
on the old label — which is the bootloader doing exactly its job, and means the
new image's host did not come up.

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
- **Software entry into the loader is NOT reliable, and the failure wedges USB**
  (2026-08-30). `mpremote exec "import machine; machine.bootloader()"` worked
  once and, on a second attempt in the same session, left the board answering
  neither HTTP nor the REPL, with esptool getting a pySerial **write timeout**
  on every `--before` mode. Nothing software-side recovers it: a `USBDEVFS_RESET`
  ioctl needs root and the CDC endpoint is already gone. **Hold BOOT while
  power-cycling** — that is the recovery, and on an unattended board it is the
  reason to prefer it as the way IN too.
- **`mpremote` of any kind STOPS the console.** `exec`, `fs cat`, `fs cp` all
  interrupt `main.py`, which kills `zero_host.serve()` — the board then answers
  nothing on the network and looks dead while being perfectly healthy. Follow
  any `mpremote` with `mpremote connect <port> reset`. This is how a
  "moybyte-zero.local refused to connect" was self-inflicted twice.
- **Getting OUT of the ROM loader needs no replug** (2026-08-25):
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
