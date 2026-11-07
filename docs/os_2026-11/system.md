# The OS (2026-11): the terminal, remote access and the system files

Phases 3 and 4 of the order of work. Read `docs/os_2026-11.md` first.

---

## 1. The terminal

The overview's real-OS principle (owner, 2026-11-03), for the terminal: **a
bash-like shell in C, plus SSH** (§3).

**The shell is C, at kernel level** (`native/moy_sh/`), like the update
screen's kernel chrome, and it fits "the kernel is C": it keeps working
while an S3's Python VM is stopped (`ps`, `kill`, `log`, `reboot` stay
available over the serial and web faces and on screen through the
kernel's text renderer, the recovery console's, §2) and on the headless Zero, which has no console Python. Its
syntax is **bash-compatible**: pipes, redirection (`>`, `>>`, `<`) into the
files tree, variables and `$(...)`, `if`/`for`/`while`/`case`, functions
and scripts, `&`/`jobs`/`fg`/`kill`, history and completion. **Where bash
would fork and exec, the shell asks the OS to spawn a process** of any tier
(`processes.md` §1). Real bash cannot be ported: it needs fork, exec, signals, process
groups and termios, none of which the kernel has. **It derives from dash**
(BSD-3-Clause; owner, 2026-11-03), vendored like WAMR (`native/moy_sh/UPSTREAM.md`:
a pinned commit and one patch), with its fork/exec, signal and job-control
layer rewritten over the process table: a fork saves the session's arena and
runs the child to its end, so a subshell's state goes as a forked one's and a
pipeline's stages run one after another over pipe buffers; an exec is a spawn,
and a program spawned with `&` is a job of its own (`native/moy_sh/moy_sh.h`); BusyBox ash
is GPLv2 and cannot enter the FSL-1.1-MIT firmware (`docs/licensing_v1.md`).

**Full access, per-command consent.** The shell runs with full access, a
root shell for the device's owner; a command installed as an app runs under
**its own app's consent** (the overview's §1), never the shell's.

**Commands as apps.** A manifest `type: "command"` is a process kind: no
window, no home icon; installing it puts its name on the PATH with the same
install, consent and store as any app; a cart may carry an app and a
command (`processes.md` §2). An installed command's name is its manifest's; a
collision with a built-in or an installed name is refused at install naming
the holder, and `<id>/<name>` always runs a specific one (owner,
2026-11-03). Two kinds: **line programs** (`argv`, `stdin`, `stdout`,
`stderr` as byte streams over kernel pipe buffers, an exit code; a Python
cart's `main(argv)` in the shell's VM, a wasm command's WASI `_start`
(`tiers.md` §3), an ELF's `main(argc, argv)` over the POSIX rows (`tiers.md`
§1)) and **full-screen text apps** (`top`, an editor, a mesh chat) over the shell's
**terminal emulation**: an ANSI/VT100 subset (below), so full-screen apps, a
future SSH client and a laptop's `ssh` session all speak one standard. The
`python` REPL into the shell's VM is one command among many.

As built, a Python line program is a command cart's `main(argv)` in the
shell's VM (`runtime/shell/sh_door.py` says how it runs): its name on the
PATH is its id's name (`local.hello` is `hello`; an install through `pkg`
or Get Carts refuses a name already taken (§6), and two carts that share one
by another way in, a cart pushed or made on the console, leave it to the
first id) and
`<id>/<name>` runs that one; it is a process of its own (kind `command`)
holding the permissions its consent grants, its `print` and `input` the
session's streams over the door, its exit code `main`'s answer, under the
scheduler's guard for 30 s at most. A command run with `&` runs to its end
before the prompt returns, its output into the log under its pid, and `wait`
reads its code. `python` with the terminal for its stdin is the
interactive REPL in front of the face (below); with a pipe or a
here-document it reads that as a REPL (an expression's value printed), and
it runs `-c CODE`.

**Built-ins are views of OS services: one implementation, a GUI face and a
command face, never a second copy.** The core commands are **C built-ins**
over the kernel's own tables and the store's C (`native/moy_store/moy_fs.h`,
`moy_ufiles.h`): the file commands, `ps`, `kill`, `free`, `df`,
`log`/`dmesg` (`processes.md` §9), `reboot`, `date`. Commands for **Python-side
services** call them through the C-owned role table (`native/moy_app`, the
ROLE door): `pkg` (§6), `perm`, `settings`, `notify`, `service`
(`processes.md` §5), `cron`/`at` (`between_apps.md` §3), `open`/`pick`
(`between_apps.md` §1–§2), and the network and hardware
commands as their services land (`wifi`, `ifconfig`, `ping`, `http`, `ble`,
`ntp`, `pins`, `i2c`, `uart`, `lora`, `battery`, `brightness`). **While the
VM is stopped** a command that needs a shell-served row is refused with
"needs the console", the answer the ROLE door already gives
(`MOY_APP_NEEDS_VM`), never queued (owner, 2026-11-03): a queued `install`
that runs when the VM returns is a surprise. On a board the session's host
table is `native/moy_sh/moy_sh_board.h`: the log, memory, `reboot`, the
kernel's row and the run in front (which `kill` ends) are served in C, and
everything the console knows (its processes, /proc and /dev through
`runtime/system/devfs.py`, the installed and role commands, `df`) crosses the
ROLE door on the shell's own row to `runtime/shell/sh_door.py`, so with the VM
stopped `ps` shows the console stopped and the run in front. The role commands
served today are `perm`, `notify`, `service`, `lease`, `python` and the device
faces `http`, `mic`, `lora`, `ble`, `usb`, `midi`, `media` and `snd`, each a
face of its service's one implementation run as the shell (pid 1), giving back
what it took before it ends, a media stream excepted;
`runtime/shell/sh_roles.py` lists their forms.

**The line between C and the console** (owner, 2026-11-05: the terminal is
mostly C, one implementation moved down, Python calling it). With the VM
stopped these answer, each over the C the console itself calls: the file
commands, `ps`, `kill` (the run in front), `free`, `log`/`dmesg`, `reboot`,
`date`, `sleep`, `clear`, `help`, `neofetch` (the rows only the console
knows, its name, board, firmware, display, theme and WiFi, read from the
door's `/proc/console`, left out), `edit` and `top` (below), `settings`
`list`, `get`, `set` and `unset` (§5), and `pkg list` and `pkg remove` (§6),
the last two in `native/moy_sh/moy_sh_store.c`. Everything else needs the
console: `settings reload` (it applies the file), the other `pkg` verbs,
`perm`, `notify`, `service`, `lease`, `python`, the device faces, the
installed commands, `df` and the console's /proc, /dev and /bin rows.

**The text apps** (`native/moy_sh/moy_sh_app.h`): `edit FILE` (#117), `top`
and the interactive `python`, each one C implementation that every face
hosts the same way: the Terminal's face, the recovery console's (the same
face) and the serial face. The command asks its face to put the app in
front, and the app takes the face when the line's run ends. A full-screen
app (`edit`, `top`) has a screen of its own of the face's size (the
alternate screen: the shell's grid is untouched and back as it was when the
app ends), which it writes as VT100, and reads keys as a terminal sends
them; the face turns its input into those (a typed key its byte, the
arrows, the trackball's and a d-pad's steps xterm's escapes, a key row's
button its bytes), and a resize is the app's screen resized and drawn again.
The key row holds the app's own keys (the editor's Save and Exit, top's Kill
and Quit), on the T-Deck then Tab and the symbols its keyboard lacks where
the app types text, on a gamepad and under touch the arrows; the T-Deck's
Mic enters the row and leaves it, a keyboard's ^S, ^X and Esc are the
editor's. On the serial face each `sh` line is the app's keys in caret
notation and the answer is its screen (`moy_sh_board.h`). The split (owner,
2026-11-05: the terminal is mostly C): the editor is C, so the recovery
console edits `system/settings` with no VM; `top` is C over the rows `ps`
reads, the task manager's CPU and memory crossing the door with them
(`runtime/shell/sh_door.py`), so with the VM stopped it shows what `ps`
shows, the kernel, the console stopped and the run in front; `python` is a
line app over the prompt row, its prompt `>>> `, each line run as `python
--repl LINE` in the console's VM (`runtime/shell/sh_roles.py`), so with the
VM stopped it says "needs the console". An `input()` typed at it meets the
end of the face's stdin.

**The screen face** is the Terminal's face, which is C
(`native/moy_sh/moy_sh_face.h`, owner 2026-11-05: the terminal is mostly C,
so it runs with no VM): a session of the C shell whose output fills a VT100
cell grid in C (`native/moy_sh/moy_term.h`), drawn as a `ui.text_view` log
through the toolkit's engine (`native/moy_ui/moy_ui_api.h`), with a one-row
text view under it for the prompt line, the history, completion and the key
rows, all of it in the face's record in PSRAM, taken at the first open. The
Terminal system app (`runtime/apps/terminal_app.py` on its identity cart
`system_carts/moybyte.terminal.moy`, the code icon, after the other built-in
apps) launches it in the console, handing it the host's frame and the
frame's keys; the recovery console (§2) opens the same face from the kernel.
There is no new drawing path, and the shell holds no pixels. A typed line is
echoed into the grid and run; Tab completes a command's name
(`moy_sh_complete`) or a path (`moy_sh_list`, the shell's own view of the
files), left and right move the caret and up and down walk the history. A
desk has a keyboard and draws no key row. On a handheld a key row under the
prompt holds what the input cannot type, and a completion with several
candidates offers them there to pick: the T-Deck's holds Tab and the shell's
symbols its keyboard has no key for (`moy_sh_face_tdeck_row`, read from the
keyboard's matrix in `native/moy_input/moy_kbd.c`), its trackball being the
arrows, Enter the run and Mic the stop, and down past the newest line enters
it; a gamepad's and touch's hold Tab, Ctrl-C, the arrows and Run, and under
touch the shell's symbols sit under it. **A shell command never holds the
console's frame while it waits** (2026-11-06): the Terminal's and the
serial face's line runs on a task of its session's own, in strict turn with
the console's (`native/moy_sh/moy_sh_board.h`, THE RUNNER), and where it
waits -- `sleep`, a role command between its steps -- it parks: the frame
goes on, the face shows no prompt, and the loop's tail hands the run its
turn again a frame later. A role command that waits (`http get`, `pkg`'s
fetches and installs, `mic read`, `lora recv`, `ble scan`, `snd play`) is a
generator the door steps a few milliseconds a crossing
(`runtime/shell/sh_door.py`), its output reaching the face as it comes, and
one on the network asks for the link without waiting for it. An SSH
session's own task waits between the steps the same way. While a run
computes it holds the console's task, so **Ctrl-C reaches it in C**: the
face's poll kicks the input drivers and reads the kernel input table's
latches between merges for a key that types 0x03, the T-Deck's Mic
(`start`) or a gamepad's B; a parked run takes the Terminal's Ctrl-C and
the board's cancel key from the face, the console's back (B, Esc, the bar's
chevron) from the Terminal's `back()`, which keeps the Terminal open while a
run is parked, and the serial face's from the channel's 0x03. A role command asks the same poll between its polls through
`moy_sh.interrupted()`, or is stopped by its session, gives back what it
took and exits 130. The simulator and
the host tests run the same session through the host's ctypes binding
(`runtime/host/moy_sh.py`); `tests/test_moy_sh.py` replays every serial and
suite transcript through the screen face and holds its grid's lines to the
serial face's output.

**The terminal emulation**, a full-screen text app's surface: the app
writes an ANSI/VT100 subset to its stdout and reads keys from stdin, as on
any Unix; the C shell's emulator keeps the cell grid (`moy_term.h` lists the
subset it reads: cursor motion, erase, SGR inks and grounds, scrolling and
wrap), and the screen face draws that grid through `ui.text_view`, whose
ANSI inks are the theme's tones and whose RGB inks and grounds are the
nearest of the sixteen base colours, exactly; the half and whole blocks
(▀ ▄ █) are fills, so a program draws pixel art two pixels a cell, as
`neofetch` draws Moy. The keys below are what the text apps read (`moy_sh_app.h`); a
line program's stdin, the resize event and the size call are still a sketch:

```
ESC[row;colH  ESC[K  ESC[2J  ESC[3xm/4xm  ESC[?25l/h   cursor, erase, 16 colours as theme tokens, cursor show/hide
ESC[38;2;r;g;bm  ESC[48;2;r;g;bm  ESC[49m             an ink and a ground: the nearest base colour
ESC[A..D, PgUp/PgDn, Home/End, F-keys as xterm sends them; a resize as the OS event "winch"
stdio: Python print/input; Lua io.read/io.write; C read/write on 0, 1, 2 (tiers.md §1); wasm fd_read/fd_write (WASI, tiers.md §3)
moybyte.term.size() -> (cols, rows)   the one thing the standards lack; args and the exit code are WASI's and POSIX's
```

## 2. The recovery console

**The recovery console** (owner, 2026-11-03), Linux's TTYs and safe mode in
one: when the GUI breaks, the C shell over a small **kernel text renderer**
(the kernel's font into the framebuffer, keys from the C input task), the
same renderer that shows the shell on screen while the VM is stopped. It is
entered automatically after N consecutive shell crashes (the kernel's crash
count in RTC memory, `native/moy_kernel/moy_crash.h`: a crash with no app,
wallpaper or game open counts one, a clean boot starts it over, an app's,
a wallpaper's or a game's crash is the strike ledger's; N is 3 unless the
console sets another through `moy_crash.crash_limit`, and a choice made on the
floor starts the count over), by a key held at boot (the T-Deck's Mic
key, which the kernel reads on the raw matrix; the trackball click is
GPIO0 and belongs to the ROM loader), or by a chord while running (Mic +
Backspace held on the T-Deck, which has no Ctrl, Alt, Esc or Tab;
Ctrl+Alt+F1 on a USB or BLE keyboard); a touch-only board enters it by the
crash count or over the dev channel. From it the owner reads the `log`,
`kill`s a process, `pkg remove`s an app, fixes a `system/` file, takes or
rolls back an OTA update and `reboot`s, with no computer and no reflash;
`edit` and `top` (§1) take its whole screen while they are open, the head
back when they end.

**The screen** is the Terminal's C face (`native/moy_sh/moy_sh_face.h`), no
second copy, under a head that says why the floor is up ("The console kept
crashing.", the boot loop's, a start that failed) with the crash record's
lines, and the choices as buttons: Try again, Safe start, Reboot (a restart
with the counts started over, where the shell's own `reboot` keeps them and
comes back here). The same words typed at the prompt pick them, and `repl`
too. It draws through the toolkit's engine on a RUN sink (`moy_sh_floor.h`):
the default seed theme's dark look, every role in petme128, because the
theme's faces are a frozen Python module no C reaches with the VM down.
`tests/test_recovery_floor.py` renders it on the host, at 320×240 and at
desk size, and `tools/kernel_gate.py BOARD crashes` holds a board's screen to
that render, types a command into it and taps Try again. Each board's own
input feeds it through the kernel's input table: the T-Deck's keyboard
(its raw matrix, typing), trackball (steps, its click Enter) and GT911 touch;
the touch of the Waveshare P4 (GT911), the Guition S3 (AXS15231) and the
Guition P4 (GSL3680, its firmware uploaded by the floor's bring-up). A
board on which no input comes up and which names an idle time
(`MOY_KERNEL_IDLE_SAFE_MS`: the Guition P4) starts SAFE after it.

**The card from C.** A floor boot has run no VM, so nothing Python mounted
the card; the store's FATFS is mounted from C instead (`moy_store_card_up`,
`native/moy_store/moy_card.c`, over the board's card bring-up in
`native/moy_sd/moy_sd.h`, whose pins are the board.toml `[hardware]` card
row's). The console's Python calls the same C (`moy_sd.up()`, then
`moy_store.card`). The floor brings the card up as it opens, on the T-Deck
once the panel is (the card rides the panel's SPI2), so `settings` and
`pkg list`/`pkg remove` reach `system/` and the carts with no VM, on the
screen and in the serial's `sh LINE` alike.

## 3. Not tied to the screen: serial, the web terminal and SSH

The same shell runs over the USB serial dev
channel (the kernel word `sh LINE`, `native/moy_kernel/moy_devch.c`, over one
session the kernel keeps: `SH` a line of output, then `SH end <status>`), over
the **web terminal**, and over **SSH**: `ssh moybyte-XXXX.local` from any
laptop, `scp`/`sftp` into the files tree, mDNS for the hostname (the IDF's
`mdns` component, the name Settings' Name; a console with none set is
`moybyte-` and the last four hex digits of its MAC, owner, 2026-11-06,
`runtime/system/ssh_service.py`), the server Dropbear (MIT-style,
small, built for embedded; owner, 2026-11-03; libssh is LGPL and awkward in a
statically linked FSL image, wolfSSH is GPL) with its own crypto: the image's
mbedTLS has no curve25519, chacha20 or ed25519 (the spike, e5b4dcd9e), so
Dropbear's bundled libtomcrypt carries them (`native/moy_ssh/README.md`).
**Sign-in (owner,
2026-11-03)**: password login by default, the password set by the user at
setup (on the Zero, on its webhost page), never a shared default; keys in
`system/authorized_keys`, so `ssh-copy-id` works; passwords and keys are
both valid, and an advanced user turns passwords off
with one line in the sshd_config-like `system/ssh` (`password_auth = no`).
With no password set there is no SSH. The password's hash is
`system/password`, set with the shell's `passwd`; the shell's `/etc` is
`system/`, and `/etc/dropbear` names it too, where ssh-copy-id puts a key
for a Dropbear server. The server holds the radio (the `ssh` lease) while a
password is set (`runtime/system/ssh_service.py`). Every console serves
it; the Guition P4's slots grew for it (its board.toml's `[image]`).
The serial terminal needs no sign-in, being the owner's cable.

**The web terminal** is the Terminal of the browser console, in both web
modes, one face and one look (`docs/os_2026-11/web_terminal_spike.md`, the
spike the owner approved on 2026-11-06):
- **On a page a board serves** the Terminal opens straight into `remote`, a
  face of that board's shell (`native/moy_sh/moy_sh_link.h`): the board's
  bytes draw the page's grid and its keys go back as they are typed. The
  shell is the board's, on the same stream face SSH opens
  (`native/moy_tty/README.md`): one face at a time across the two, so a page
  that finds an SSH session's is told so. The transport is a WebSocket the
  webhost upgrades, `GET /term?cols=&rows=` (subprotocol `moy.sh`), sealed
  like every route below, handed off the console's task at once, so the
  frame never carries it.
- **Its sign-in is the page's**, the browser sign-in below: on a console a
  paired browser can already write and run any cart, so a shell adds no
  exposure. The page says in the Terminal when the board does not answer, no
  longer takes this browser, or makes it wait ("wait N s", the 429's `ms`).
- **On the project site** the console runs the shell itself in the page
  (`firmware/web_runner/build.sh --site`), its root the browser's store
  (`/moy` in the page) and its home that store's `files`. A run holds the
  page's worker, so one past its budget is stopped as Ctrl-C stops it
  (`native/moy_sh/moy_sh_screen.c`). The bundle every board bakes carries the
  face and no shell. **A spike first**: image size against `headroom_floor_kib`,
RAM per session, and internal SRAM on the S3 with WiFi up. Terminal-only
apps are **the app model for the headless Zero**
(`firmware/seeed_xiao_esp32s3_zero/README.md`): `ssh` in from a laptop and
run a command. The Zero runs the C built-ins until WASI commands arrive
(the WASI spike, or P5.5); installable commands reach it with WASI.

**The browser sign-in** (owner, 2026-11-07: a device key and sealed
sessions, the crypto in the wasm; it replaced the 4-digit pin of 2026-08-25).
A browser pairs once and signs in with a device key from then on; every
route but the public bundle and the `GET /sync` marker is sealed to its
session. The protocol is `native/moy_net/moy_seal.h`'s, the board's end
`native/moy_net/moy_webauth.c`, the page's `native/moy_net/moy_seal_web.c`
compiled into the wasm (a page served over plain HTTP has no WebCrypto), both
over mbedTLS: a board's ESP-IDF copy with its hardware AES and SHA, the
host's and the wasm's the same release vendored (`native/moy_net/mbedtls/README.md`).
- **Pairing**: the console's QR carries a single-use 128-bit token (ten
  minutes, a fresh one each time the screen draws it) and the first 8 bytes
  of the board's id after the address's `#`, which never reaches a server or
  a log. A browser that cannot scan types the console's password, the one
  `passwd` sets for SSH: SPAKE2 over P-256 (RFC 9382's M and N and key
  schedule) from the stored hash, so the password never crosses the network
  and a recorded exchange gives nothing to guess against offline. With no
  password set a browser pairs by the QR alone; the Zero, with no screen,
  sets its password in its setup form or `provision.sh --password`. A pairing
  mints a random 128-bit device key with an id and the browser's name, kept in
  `system/webkeys` (`/etc/webkeys`); the page keeps it per board id in its
  origin's storage.
- **A session**: a key login answers a fresh challenge with an HMAC over both
  sides' nonces, the board proves the key back, and HKDF gives one AES-128
  key each way. A request carries its session and sequence number on the
  query and its body (or, bodiless, its tag) under AES-GCM bound to its method
  and target; the board takes each sequence number once, inside a window of
  64. An answer is sealed records bound to its status, its last record
  marked, so a cut, reordered or replayed answer does not open. The
  Terminal's WebSocket frames are sealed each way, counted from 0, and a frame
  that does not open closes the socket 1008 "seal".
- **Sealed routes**: the store's pulls (`GET /carts.json`, `GET
  /files.json`, `GET /console.json`) and `POST /sync`, `/gpio`, `/term`, and
  `/run` and `/update`, which the VM answers and the C seals. All of them read or change a person's work or the firmware, so none
  is left open; the bundle is the same bytes for everybody and the marker
  says only that a board lives here.
- **The backoff**: every credential the sign-in checks (a key, a token, a
  password's PAKE) counts against its source, ten free, then a window from
  1 s doubling to 5 min, and thirty from anywhere shut out every source that
  has not signed in since; a waiting source is answered 429 with its
  credential unread. A sealed request is not a credential: its tag is 128
  bits.
- **Forgetting a browser**: Settings -> Paired browsers lists each (its name,
  the day it was last seen) and forgets one or all; `passwd -l` lists them and
  `passwd -r ID|all` forgets. A forgotten key's sessions end at once and a web
  terminal it holds is cut (1008 "revoked"). Changing or removing the password
  (`passwd`, `passwd -d`) forgets no browser: a paired browser holds its own
  key, and a household that changes the password should not have to re-pair
  every tablet; forgetting is its own act, in the same two places. Both show
  the SSH host key's fingerprint (`SHA256:`, OpenSSH's form) to check a first
  `ssh` against.
- **What it does not stop**: the page itself is served over plain HTTP, so an
  attacker ACTIVE on the LAN can swap the page for one that captures a
  password typed into it or a key it reads; only HTTPS with a certificate
  the browser trusts closes that, and a console has none. A passive listener
  learns nothing, a stolen QR token is spent or stale, and SSH, whose host
  key a person can check, stays the full-security path. A device key lives in
  the browser's storage for that origin: anyone with that browser profile is
  that browser, until it is forgotten. A session's keys come from the device
  key, so a key stolen later opens sessions recorded earlier; forgetting it
  is the remedy.

## 4. SFTP

**SFTP** over SSH (§3) lets a computer mount the files tree
(`between_apps.md` §2), the sync RPC staying for the browser. **WebDAV is
dropped (owner, 2026-11-07)**: over plain HTTP its sign-in crosses the network
readable, an HTTPS server needs a certificate no authority issues for a
`.local` name, Finder and Explorer cannot use the browser console's device
keys, and the Guition P4's image has no room for it; Linux file managers open
`sftp://` natively, and Cyberduck and WinSCP do on macOS and Windows. A
WebDAV server stays possible as a user's C (ELF) service (`tiers.md` §1),
which has every table row.

**SFTP (P4.4, 2026-11-07)** is the SSH server's `sftp` subsystem, so
`sftp moybyte-XXXX.local` and `scp file moybyte-XXXX.local:` (scp's protocol
since OpenSSH 9.0; `scp -s` before it) reach the files tree below with SSH's
sign-in (§3): wherever SSH serves, SFTP does. The server is our own SFTP
version 3 in C (`native/moy_tty/moy_tty_sftp.c`), not a vendored one:
Dropbear has none, OpenSSH's `sftp-server` is built on a process's POSIX
files, and the free C servers we know of are GPL or LGPL. It runs as the
session's face in place of a shell (`native/moy_tty/README.md`), so it
counts as the one face: a shell open on the same console, over SSH or the
web terminal, refuses it until the shell ends, and the server takes one
connection at a time. Its paths are the shell's own mapping (a session's
home its start, `/etc` the system folder, the root, `/dev`, `/proc` and `/bin`
the shell's listings, a virtual file never opened). Each request is one hop
to the console's task under the board's gate, opening, reading or writing
at most 32 KB and closing, so a big file streams a request a frame and
nothing stays open between them; a write appends at the file's end, as an
upload's always does. Modes, owners and times are accepted and ignored
(`setstat`), symlinks are unsupported, and a listing carries sizes, not
times. A change under the carts folder (a cart folder written, made,
removed or renamed) tells the shelf once the client has been quiet for
300 ms or the session ends: the door's `rescan`, which re-reads those
folders alone (`ws.rescan_carts`, as the browser sync does). System files
stay writable: the owner is root on their own console.

**What the files tree shows (2026-11-06).** Its root `/` is the console's
real root: `/bin`, `/dev`, `/etc` and `/proc`, the names of the volume
mounted at `/` (the internal flash, its `moy/` store among them) and the
folders mounted beside it (`/sd` with a card), each once
(`native/moy_sh/moy_sh_sys.c`). A session, on every face (the Terminal, SSH,
the serial `sh`, the recovery console), starts in the **user's files
folder** beside the store's carts, `/sd/moybyte/files` on a card and
`/moy/files` without one, which is its `$HOME` and where `cd` returns; a
console with no files folder yet starts in `/`
(`moy_sh_board_go_home`). The store is the one the console booted on, found
the way its boot finds it (the card's where its carts are there, else the
internal flash's), and `/etc` is that store's `system/`, the folder the SSH
server signs in from (`ssh_service.system_dir`, beside `ws.carts_root`). It is the folder the Files app shows, so the
shell, SFTP and the app see one home; the home's `.ssh` is `/etc`, as
`/.ssh` is, so ssh-copy-id's key lands where the server reads it.

## 5. Settings as files

**`system/`** holds the console's documents **as plain-text files like
`/etc`** (owner, 2026-11-03), beside the carts folder (`/sd/moybyte/system/`
on a card): `settings` and `sources` (§6), `password`, `ssh`,
`authorized_keys` and the SSH host key (§3), and `wifi`, `crontab` and
`hostname` as the phases that need them arrive; the shell reaches them as
`/etc`, and `settings` works over them.

**`system/settings`** (P3.2, `runtime/carts/moy_docs.py`) is every row
`ws.system` holds, one `key = value` a line, sorted by key, each value the
row's JSON on one line (`theme = "blocks"`, `font_scale = 2`,
`favorites = ["/sd/moybyte/carts/moybyte.paint.moy"]`). The reader is
strict: a line that is not `key = <JSON>` (blank lines and `#` comments
aside) is passed over and logged. The file's one reader and one writer are
C, the settings rows' (`native/moy_spine/moy_settings.h`): `moy_docs`'s
`parse_settings` and `settings_text` call them on every tier. It is written
atomically and read through the store's crash-safe reader, like every store
write. The shell's `settings` (`runtime/shell/sh_roles.py`) lists the file, `get`s a value,
`set`s one (a JSON value, or the words as a string) and `unset`s one,
re-applying the key live where the console applies it (Settings' own verbs),
and `reload` reads a file edited by hand back into the console, which
otherwise writes its rows over the edit at its next change. With the VM
stopped the shell serves `settings` itself, in C over the same reader and
writer (`native/moy_sh/moy_sh_store.c`): `list`, `get`, `set` and `unset`
change the file, which the console reads at its next start; `reload` needs
the console. The web sync's
system root carries the file as `system/settings` (`moy_sync.SYSTEM_FILES`,
`.claude/rules/web.md`).

**Everything is a file, where it is cheap (owner, 2026-11-03).** The
settings moved from `system.json` to the plain-text file with no migration:
a legacy `system.json` reads as absent until wiped by hand (no seed content
carries settings, so no seed version moved), and `cat`, `settings` and an
editor all reach them. `/proc/<pid>/status` and `/proc/<pid>/log` are virtual
files over the process table and the log ring, and a leased device is
`/dev/uart1`, `/dev/gpio4`: `runtime/system/devfs.py` is their file face
(`open`, `listdir`, `cat`), which the shell mounts (P3); opening a device
takes its lease for the opener and closing gives it back, or reads through a
lease the opener already holds. A C line program reaches them with
`open`/`read` once they are registered through ESP-IDF's VFS, with the ELF
tier. Nothing
else gets a file face until a command needs one.

## 6. The package index

`pkg` (owner, 2026-11-03) is apt-shaped: a repository is a URL serving an
index and the carts it lists; moybyte-org/carts is the first. A `.moy` is the
package. As built (P3.2), one installer has two faces: `runtime/carts/pkg.py`
holds the verbs and the checks over `runtime/carts/cart_index.py` (with the
`install` role's store, network and radio lease), and Get Carts
(`runtime/apps/getcarts_app.py`) is its GUI face, making the same checks
(`pkg.blocker`, `pkg.install_check`) and the same removal (`pkg.removed`).

**The index** is moy-spec's (`cartindex.py`, version 1), read as it is, with
two additions read here: an entry's `depends`, a list of cart ids, and the
signature. **An index is signed as OTA manifests are** (the same keys,
RSA-2048 PKCS#1 v1.5 over SHA-256): `tools/ota_sign.py sign-index` appends
`, "sig": "<hex>"}` in place of the document's closing brace, covering
`moybyte-index-v1\n` and the document as it was, so neither side
re-serializes JSON. A signed index whose signature does not verify is
refused, and so is a `sig` that is not the last member; an unsigned index is
read. A board verifies in the kernel (`moy_net.ota_verify`), the host through
its twin; the browser has no verifier and needs none, its fetch checking the
server's certificate.

**`system/sources`** names the repositories, one index URL a line (`#`
comments); absent or empty, the console reads moybyte-org/carts. A legacy
`indexes.json` reads as absent. A page served from a static host writes the
host's `indexes.json` there at boot.

**The verbs** (the shell's `pkg`, `runtime/shell/sh_roles.py`):

| verb | does |
|---|---|
| `pkg update` | fetches every source's index, checks it and caches it in `pkg/` beside the carts folder |
| `pkg list` | every cart the cached indexes list: id, version, where it stands here (`-`, `installed`, `update`, `taken`), name |
| `pkg search WORD...` | those whose id, name, description or tags hold every word |
| `pkg install [-y] ID...` | each with its `depends` first (`cart_index.needs`: one no index lists, or a cycle, refuses), the checks, an external file's licence, the download and the permission prompt (`processes.md` §2); `-y` answers yes, else each is asked on stdin |
| `pkg remove ID...` | the store's delete: the folder, its record, the files it wrote, its consent |
| `pkg list` with no VM | the carts the record holds, `installed` (the indexes need the console) |
| `pkg upgrade [-y]` | every installed cart the indexes list newer |
| `pkg sources` | the sources in use |

A verb that reads the indexes fetches a source the cache does not hold yet.
**The checks**, the same in Get Carts: a store to keep it in; a runtime this
console carries; the chip, a compiled cart with neither a module for this
chip nor a `main.wasm` being refused; the engine's own fit and THE SHARES'
(`processes.md` §6: a cart whose load needs more than this board's share for
the running set could never be admitted); room in the store; and at the
install prompt a permission this console lacks, and **a command-name
collision**: a `"type": "command"` cart (§1) whose name, its id's part after
the first dot, the shell already answers to (a built-in or a role command,
`pkg.SHELL_NAMES`) or another installed command holds is not installed. Get
Carts installs one cart at a time, so a cart whose dependency is not yet on
the console says to get that one first.

**The record and the removal are C** (`native/moy_store/moy_shelf.h`):
`installed.json`'s one reader (the file, else its backup healed into place),
the record without a cart (written as `json.dumps` writes it, and handed to
a keeper before anything is deleted), the folder's rename out of the carts
folder and delete, and the cart's written files. `cart_index`'s
`load_record`, `find_installed` and `remove_steps` call it on every tier, and
with the VM stopped the C shell's `pkg list` and `pkg remove` serve it, the
removal forgetting the cart's consent through the settings rows.

**An install's landing and a removal are stepped, a store op a frame**
(2026-11-07), where one frame used to hold them whole (diag's HITCH lines
named it). `pkg` and Get Carts step the same generators: `Install`'s
staging folder made and its landing (the old copy aside, the build in by one
rename, the record, the old copy deleted an entry at a time),
`pkg.landed` (the consent, the shelf's re-read) and `pkg.removed`
(`cart_index.remove_steps`, the consent forgotten, the re-read). A record or a
consent is saved in two store ops (`moy_fs._write_atomic_steps`: the backup,
then the marker and the file together, or the whole save when the backup is
no longer its own), the saves are carried in the op that sets the old copy
aside, and a verb stopped partway (Ctrl-C, the app closed) finishes what it
started at once (`pkg.through`, `pkg.land_now`: the landing and its consent
and re-read).
