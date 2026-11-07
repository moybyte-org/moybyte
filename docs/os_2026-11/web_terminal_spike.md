# Spike result (2026-11-06): the web terminal, and one link to the board

**A spike result, dated 2026-11-06**, on the `webterm` branch (over
`moyfetch`). It answers how P4.2 (the web terminal, `system.md` §3) gets
built, and the owner's addition of the same day: one general way for a
browser console served by a board to use that board's hardware, with the
terminal as one client of it. The measurements are a dated record of the
prototype (Guition S3, the bench's WiFi, this machine as the client); the
numbers that track the system stay in #246.

**Superseded where the build differs** (P4.2, built 2026-11-07; what IS
lives in `system.md` §3, `native/moy_tty/README.md` and
`native/moy_sh/moy_sh_link.h`):
- the face moved to a module of its own, `native/moy_tty`, not a file of
  `native/moy_sh`; each transport holds the face task while it serves;
- the bridge is `native/moy_tty/moy_tty_ws.c`; a resize is a text message
  `{"size":[C,R]}`, not xterm's report; the board closes 1000 `exit N` and
  1013 `busy`; the subprotocol is `moy.sh`; the route answers `{"term":1}`
  without the upgrade, which the page asks why a socket failed; the face
  gathers its own output (a bridge that waited 2 ms for more cost a 10 ms
  tick a key on a board) and the bridge frames what the pair holds;
- the second `neofetch` stop of §2 was the face's loopback dropping
  segments, fixed by the face's output gathering before this build
  (`tests/test_moy_tty.py` runs it twice);
- the bundle every board bakes is not free: it carries the Terminal's face,
  grid and the link in place of the shell (the numbers are #246's);
- the site's build is `build.sh --site` into `dist_site/`, and a run there
  has a budget of 3 s.

**The constraint** (owner): no second renderer and no second Terminal. The
Terminal's C face (`native/moy_sh/moy_sh_face.h`) and the toolkit draw it
everywhere; only where the shell runs differs, chosen by the page's origin
as carts are (`.claude/rules/web.md`, two web modes).

## The recommendation

1. **The transport is a WebSocket the webhost upgrades** (`GET /term`, pin
   gated), handed off the console's task at once to a bridge task, and **the
   board end is the SSH face unchanged** (the SSH face's header (now `native/moy_tty/moy_tty.h`)):
   the bridge opens a face exactly as Dropbear does. Prototyped and measured
   below; it is the only option whose frame cost is nil by construction.
2. **The browser end is a full-screen text app**, `remote`, over the face's
   existing app surface (`native/moy_sh/moy_sh_app.h`): the face already
   hands a full-screen app the grid and turns every key, the key rows' too,
   into xterm bytes. A board-served page's Terminal opens straight into it;
   the same app with an SSH transport is the "future SSH client" of
   `system.md` §1. One face, one grid, one look.
3. **Site mode runs `moy_sh` in the wasm**, which works today (below) but
   costs every board image, the Zero past its slot. Build it into the site's
   bundle only, or decide the Zero's flash first (§1).
4. **The pin suffices on a console; the Zero is an owner call** (§4). Before
   any shell sits behind the pin, the webhost needs a backoff on wrong pins:
   it is four digits and nothing limits guesses.
5. **The same socket becomes the board link**: channels for the shell, the
   pins, a leased UART, a device API's events, each opened as the board's
   own process under the board's lease table and consent (§7).

**Estimate:** the web terminal on the consoles, board-served and site mode,
**3 worker-sessions** (the face split out of `moy_ssh` with the run
generalised, 1; the `remote` app, the worker's socket and the Terminal's
mode, 1; site mode's hooks, Ctrl-C and the image question, plus the on-glass
and e2e tests, 1). The Zero's terminal **+1**, after the owner's flash call.
The board link with GPIO, the shell and one device API on it **+2**, after
the terminal.

## 1. The shell in the browser build

**It builds and runs.** `native/moy_sh/micropython.mk` is the cmake file's
twin for the webassembly port: dash's sources take the shim by target
(`$(BUILD)/moy_sh/dash/%.o`), the shell's own files `-std=gnu99`, and the
host table is `moy_sh_board.c` without `MOY_SH_ON_BOARD`, as the CPython
host builds it (`runtime/host/moy_sh.py`). `MOY_WEB_SHELL=1
firmware/web_runner/build.sh` stages it; without the variable the bundle is
as on dev. the probe `shell_probe.mjs` (the spike's branch, commit 3cd02d40f) boots the console under
node and runs a session: `pwd`, `ls /`, a redirect, `cat`, `rm`, a pipeline
into `wc -l`, `ps` (which crossed the ROLE door into the console's Python
and listed its processes), `free` and `neofetch` all answered, eight
commands in 23 ms, the arena at 5.9 KB.

**What the web's host table needs** beyond the board's as built:
- **home and root**: the session starts at `/` and `ls /` shows the
  worker's whole MEMFS (`tmp/`, `home/`, `dev/`, `proc/`, `moy/`); the
  table's root should be the store's (`/moy`), home `/moy/files`;
- **`reboot`** (a page reload, or refused), **`free`** (the wasm memory and
  the gc heap, not the host's malloc it reads now) and **`neofetch`'s chip**
  ("host");
- **Ctrl-C mid-run.** A run holds the worker's thread and the page delivers
  input only between steps (no ASYNCIFY), so a `sleep 30` freezes the tab
  for its duration and nothing can interrupt it. The text apps are fine
  (they tick per frame). Either a SharedArrayBuffer flag the face's poll
  reads (needs COOP/COEP headers, which GitHub Pages cannot send) or a run
  budget that ends a run after N ms: the second is the realistic one.

**The cost, measured:** `micropython.wasm` 3,336,469 → 3,511,543 B
(+175 KB), gzipped 1,658,515 → 1,736,312 B (**+77.8 KB**). The gzipped
bundle rides every board image, in both OTA slots. The Guition S3 kept
748 KB of headroom (floor 512). **The Zero's image overflowed its 3.25 MiB
slot by 1 KB** (`Error: All app partitions are too small ... overflow
0x400`), so it had about 77 KB to spare (the build prints 73 KB on this branch without the shell). The Guition P4
fell under its 1024 KB floor too (1001 KB). dash is about two
thirds of the shell's objects in the wasm (100 KB of 221 KB).

**So:** a board-served page needs no local shell (its shell is the board's,
§2), and only site mode does. Two ways to pay:
- **(i) one bundle, the shell in it**: every board pays +78 KB a slot, and
  the Zero needs room first (its slots grown out of its 1.375 MiB store, or
  something else taken out of its image);
- **(ii) the site's bundle with the shell, the baked one without dash**:
  the same build with the flag the site's publish sets, the baked bundle
  keeping only the face, the grid and the app surface that §2's `remote`
  needs. A second build variant to test; the boards pay nothing.

The recommendation is (ii): what a board serves never runs the local shell.

## 2. The transport to the board

**What exists.** The webhost (`native/moy_net/moy_webhost.c`) is
request/response: polled once a frame on the console's task (on the Zero
every 10 ms from `zero_host.serve`), one request per connection, closed
after its answer, one parked slot for the VM's routes that blocks every
other accept while it is held. No WebSocket, no chunked upload, no streams
anywhere in the C (`grep` for them finds only the worker's comment). The
Python streaming view had one and lost it in 2026-09 when the sync RPC
shipped as plain HTTP and nothing else used it (`.claude/rules/web.md`); a
terminal is the consumer it lacked. Its lesson stands: a persistent socket
dies through a flash write, so the page reconnects a dropped terminal, and
`/update` stays plain HTTP.

| | (a) a request per key, a held request for output | (b) a WebSocket upgrade | (c) the sync RPC |
|---|---|---|---|
| C change | a second kind of parked slot (the one there is blocks all other accepts while held), an output queue, a session table keyed by a token | the upgrade (SHA-1, the 101), the socket handed to a task: **~150 lines, done** | a "run" op: no interactivity, no text apps |
| key → echo | a new TCP connection and a request each, accepted at the frame's poll: **GET /sync median 17 ms, p90 62 ms** (n=30) | **median 6.3–7.0 ms, p90 7–12 ms** (n=30–40, three runs) | as (a), and no echo |
| full-screen redraw | the held request's answer per batch, then a new request | the face's bytes as they come: `top`'s first screen 3.9 KB in 57 ms, then about 56 B/s; `edit` opened in 71 ms, a key's redraw 7–30 ms | none |
| frame loop | every key and every output batch is a request served on the frame | nil: the upgrade is the request the poll already reads; after it the socket is the bridge task's | as (a) |

**The prototype** (`moy_ssh_ws.c`, now `native/moy_tty/moy_tty_ws.c`, the route in
`moy_webhost.c`): the webhost reads `GET /term?pin=&cols=&rows=`, checks the
pin, answers 101 and calls `moy_web_term(fd, cols, rows)` (weak: an image
without the bridge answers 404). The bridge task (6 KB PSRAM stack) opens a
face with `moy_ssh_command(NULL, pty=1, ...)`, the call Dropbear makes, and
moves bytes between the WebSocket's frames and the face's socket pair with
`select`: a frame in is keys, a text frame `ESC[8;R;Ct` is a resize
(`moy_ssh_winch`), a close or the face's end closes both. The face task is
the SSH run's when one exists, else one the bridge makes. `ls`, `ps`,
`neofetch`, `top`, `edit` and `exit` all ran over it from a plain Python
client (`experiments/webterm/ws_probe.py`, on the build's protocol since).

**Measured on the Guition S3** (web console on, so the glass parked):
- connect to prompt 65–102 ms;
- a key's echo, above; the face echoes on its own task, nothing hops;
- a command that touches the store hops to the console's task and waits for
  its frame's tail: `ls` median 98 ms (82–216, n=12), `cat
  /sd/moybyte/system/settings | wc -l` median 158 ms (p90 463), `echo hi`
  (no hop) median 55 ms. The hop is the cost, and it is the SSH face's
  already;
- **RAM per session**: internal SRAM free 44,751 → 42,435 B with a session
  open (−2.3 KB; low-water 39,763), PSRAM −79 KB (the face task's 24 KB
  stack, the bridge's 6 KB, the 32 KB arena, and the server's globals that
  `moy_ssh_globals` takes, Dropbear's among them);
- **image**: the bridge 1.5 KB of text; the upgrade and its SHA-1 about
  1 KB in the webhost.

**What the prototype shows that wants fixing before it lands:**
- the face sends the cursor's position every 250 ms tick whether it moved or
  not (12 B, about 48 B/s idle), and every write is its own frame: 38,910 B
  went out in 1,983 frames, 20 B a frame. A coalescing read in the bridge
  (a few ms) and a cursor sent only when it moved fix both;
- **`neofetch` run a second time in one face session stops after 379 bytes**
  (three times of three; the serial face's `sh neofetch` twice is fine, and a
  new session works). It is the face's path, so `ssh` meets it too; the
  hold-and-send of a hop's output is where to look;
- the SSH run's face task parks unless the server runs, so the prototype
  makes its own; landing it means one face task woken by either transport
  (§3).

## 3. The board end: the SSH face, with the transport swapped

**Yes, with nothing in the face changed.** The face's Dropbear side
(`native/moy_ssh/port/moy_ssh_port.h`) is already transport-neutral:
`moy_ssh_command` gives a byte stream with a terminal's size,
`moy_ssh_signal`, `moy_ssh_winch` and `moy_ssh_reap` the rest. The bridge
uses exactly those, so the line discipline, the history, the text apps, the
hops and the held output are the SSH session's.

The seam to land is an ownership move, not new behaviour:
- **the face out of `moy_ssh`**: `moy_ssh_face.c`, `moy_ssh_sh.c`, the
  hop and tail, and the face task's half of the run into a module of their
  own (a `+native/moy_sh/moy_sh_remote.c` beside the other faces, or its own
  folder) that both transports take, so a board without Dropbear serves the
  web terminal; its state its own, not inside the server's globals;
- **the run** wakes the face task when either transport wants it, and
  parks it when neither does (`moy_ssh_run.c`'s word grows a second holder);
- **one face at a time** across both, as today, or two faces with two
  arenas (32 KB PSRAM each): an owner choice, the first is free;
- **Tab completion** in the face's line discipline: the browser's Terminal
  completes, the SSH face does not yet, and `ssh` users want it too.

**The browser end.** The Terminal face hosts a full-screen app through
`moy_sh_app_ops` (keys, term, tick, resize); a `remote` app whose term is
fed from the link and whose keys go to it is the whole browser side in C. The
worker opens the socket and hands bytes into the VM between steps, as
`carts_link.py` does its events (nothing waits inside a step), and the
Terminal app on a board-served page opens `remote` instead of a local
session. The key rows, the theme and the grid are the face's.

## 4. Sign-in

**On a console, a pin holder can already run any code.** A pinned `POST
/sync` writes any file of the carts root, a cart's `main.py` and manifest
included (`tests/test_guition_on_glass.py` does it); a pinned `POST /run`
plays it (`device/moy_webhost.py`'s `_run`). "A cart with no consent record
-- one ... brought in by hand -- never prompts and runs on its manifest as
written" (`runtime/shell/app_perms.py`), and Python is sealed by nothing
(`docs/os_2026-11.md` §4). So the pin is already root on a console, and a
shell behind it adds no exposure.

**On the Zero it is not.** The Zero runs no carts (no `/run`), its batches
reach only the carts and files roots, and a pushed `.py` needs the cable.
Behind the pin today: the store (all of it, deletion included), the pins in
its allowlist and a signed update. A shell would add the board's own files,
`/moy/zero.json` (the name and the pin) and the WiFi credentials the setup
kept. That step up is the owner's call.

**The pin is weak against guessing.** It is four digits and the webhost
limits nothing (`pin_ok_query` compares and answers 403 at once); at the
measured 17 ms a request, every pin is tried in under three minutes from
the LAN. A console's web console is on only while Settings or `web` turns
it on; the Zero serves always. A backoff after wrong pins (a few lines in
the webhost, its counter outside the VM) belongs before any shell sits
behind the pin, and helps every gated route today.

**The SSH password is not needed for the web terminal on a console**: the
pin already grants more, and a password typed into a page served over plain
HTTP crosses the LAN in the clear as the pin does. It buys a stronger secret
than four digits, which the backoff buys too. Requiring it would also mean
no web terminal on a console with no password set, which is most of them.
On the Zero, where the shell is a step up, the password (set on its setup
page) is the stronger gate if the owner wants one.

**What the Zero uses for a pin with no glass:** its first-run setup page
mints one and shows the paired url, or `provision.sh` mints one and prints
it; it lives in `/moy/zero.json` and never rotates once scanned
(`firmware/seeed_xiao_esp32s3_zero/README.md`, "The pin gates everything").

## 5. The Zero

**Nothing runs a shell on it today.** It denies `moy_sh` and `moy_ssh` in
its `board.toml` ("this board takes the shell with that face"), its serial
is a bare REPL with no dev channel, and it has no console Python: its
MicroPython runs `zero_host.serve()`, which polls the webhost every 10 ms.

**What would serve the board end there:** `moy_sh` (its C built-ins, the
store's commands in C, `log`, `ps` of the kernel; the ROLE door answers
"needs the console" since nothing serves it) and the face module of §3
without Dropbear, over the WebSocket. Two things the Zero lacks: a frame
tail to serve the hops (a call from `zero_host`'s 10 ms loop is enough), and
**flash**: 73 KB of headroom against the shell's ~125 KB of text in
the S3 build's archive before the link drops what is unused. Its slots
must grow out of its 1.375 MiB store, or the shell stays off it. That is
the same decision as §1's (i), and the owner's.

## 6. SSH from the browser instead

A Dropbear client compiled to wasm, and a WebSocket-to-TCP bridge on the
board onto its own port 22. **It costs more and buys little:**
- the client's crypto in the bundle that rides every board image (curve25519,
  ed25519, chacha20-poly1305: Dropbear's server added 53–61 KB to an image,
  #246, and a client is of that order again);
- an SSH handshake per session on the S3 (`ssh host ls /` took about a
  second on the T-Deck, #246) and the server's ~56 KB PSRAM a session;
- it needs the SSH password set: no password, no web terminal;
- **what it buys is secrecy against a passive listener**, but the page and
  its JavaScript come from the same board over plain HTTP, so anyone who can
  change the page owns the session anyway. Transport security for the web
  console is TLS on the webhost, a different decision (the S3's internal
  SRAM with WiFi up is its constraint, #246).

## 7. One link to the board's hardware

**What a page reaches on its board today: four mechanisms, one per
capability**, each a route and a link module of the same shape (a queue in
Python, the worker doing the I/O, one request in flight, answered from the
last answer):

| capability | the route | the browser's half | where |
|---|---|---|---|
| the store | `POST /sync`, `GET /carts.json`, `GET /files.json` | the worker's sync pump, `runtime/carts/moy_sync.py` | every board |
| pins | `GET`/`POST /gpio` | `firmware/web_runner/gpio_link.py` (`ws.gpio`) | the Zero only (`MOY_NET_GPIO_PINS`) |
| the firmware | `GET`/`POST /update` | `firmware/web_runner/update_link.py` (`ws.updater`) | every board |
| the board's Settings | `GET /console.json`, system batches | the worker | consoles |

Nothing else: no UART, SPI or I2C, no LoRa, BLE, mic or audio; the WIFI
panel is `None` on a page. And `/gpio` drives the pins directly
(`moy_gpio_drive`), outside the lease table, so a pin a cart on the board
leased can be driven from a page too.

**One channel.** The `/term` socket generalises to `/link`: one WebSocket a
page, pin gated, multiplexing channels in small binary frames:

```
open   ch KIND ARGS     KIND: sh (cols rows) | dev (/dev/uart1, /dev/gpio5) | lease (row, cfg) | events (source)
opened ch | refused ch ERRNO       the board's answer: EBUSY, EPERM, EINVAL as the lease table gives them
data   ch BYTES          both ways: the shell's stream, a UART's bytes, a pin's level, an event record
close  ch                either side; the board's sweep gives back what the channel took
```

**It maps onto the board's model with nothing new to trust.** A link is a
process on the board (kind `link`, named after the cart the page runs), so
`ps`, `pins` and `top` show it and the sweep ends its leases when the socket
closes. A channel's `open` is the take the board already does for a local
process: a `dev` channel is the shell's `/dev` file (`runtime/system/devfs.py`),
a `lease` the lease table's take (`native/moy_kernel/moy_lease.h`), refused
EPERM without `lease:<row>` for that cart's id. The cart is in the board's
own store (a board-served page edits the board's store), so its consent
record and manifest are the board's, and the board's Settings turn its
permissions off as for a local run. `/gpio` folds into it as the `pins`
subset under a lease.

**The cart API seam.** The console's device services are injected objects
(`ws.gpio`, `ws.updater`, the device APIs `runtime/system/devices.py`
names); a board fills them locally, a board-served page with a link-backed
twin per service in `firmware/web_runner/`, each a channel on the one socket
instead of a route of its own. The verb table does not move: `pin_read`
already answers the last level the board reported (`docs/moy_cart_api.md`),
and the device API sketch (`hardware.md` §4) is polled (`recv(into)` ->
`None`), which is what a browser step can do. Nothing web-only enters
moy-spec: the link is a host's way of answering the same verbs, as the
simulator's stand-ins are. What a verb cannot be over a link is blocking; a
verb that waits on a board cannot wait in a browser step, so the spec keeps
device verbs polled.

**Costs:**
- **a GPIO round trip**: one frame each way, the echo above (median
  ~7 ms) when the channel's work is C on the bridge task (the lease table and
  the pin drivers are C); a channel served by the console's Python
  (`devfs.py`) adds a hop to the frame's tail (the `ls` figure). Today's
  `/gpio` is a request each, median 17 ms, p90 62;
- **RAM on the S3**: the bridge and its buffers about 9 KB of PSRAM and the
  socket about 2 KB internal for the whole link; a shell channel adds the
  face (the 24 KB stack and 32 KB arena in PSRAM);
- **the frame loop**: nothing for the bridge; a Python-served channel costs
  its own work at the tail, as a hop does now;
- **the Zero with no console Python**: C channels only, which is most of
  what it has: the shell (§5), pins under a lease, LoRa (`native/moy_net/moy_lora.h`
  is C) and the store. A UART lease is Python's `machine.UART` today and
  would need a C reader to be a Zero channel.

**Recommendation:** build the terminal (§3) as the first channel on a link
shaped like this, so the socket, the pin gate, the process row and the sweep
are built once; move pins onto it second (with the lease fix), then a
device API's events.
