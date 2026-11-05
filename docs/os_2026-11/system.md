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
reads its code. `python` reads its stdin as a REPL (an expression's value
printed) or runs `-c CODE`; the serial face's stdin is empty, so there it
takes a pipe or a here-document.

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
`date`, `sleep`, `clear`, `help`, `settings` `list`, `get`, `set` and
`unset` (§5), and `pkg list` and `pkg remove` (§6), the last two in
`native/moy_sh/moy_sh_store.c`. Everything else needs the console:
`settings reload` (it applies the file), the other `pkg` verbs, `perm`, `notify`,
`service`, `lease`, `python`, the device faces, the installed commands, `df`
and the console's /proc, /dev and /bin rows.

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
touch the shell's symbols sit under it. A run holds its caller's thread (in the console the VM's) until its commands end, so **Ctrl-C
reaches it in C**: the face's poll kicks the input drivers and reads the
kernel input table's latches between merges for a key that types 0x03, the
T-Deck's Mic (`start`) or a gamepad's B. A role command that waits (`http
get`, `mic read`, `lora recv`, `ble scan`, `snd play`) asks the same poll
between its polls through `moy_sh.interrupted()`, which on the serial face is
the channel's 0x03, gives back what it took and exits 130. The simulator and
the host tests run the same session through the host's ctypes binding
(`runtime/host/moy_sh.py`); `tests/test_moy_sh.py` replays every serial and
suite transcript through the screen face and holds its grid's lines to the
serial face's output.

**The terminal emulation**, a full-screen text app's surface: the app
writes an ANSI/VT100 subset to its stdout and reads keys from stdin, as on
any Unix; the C shell's emulator keeps the cell grid (`moy_term.h` lists the
subset it reads: cursor motion, erase, SGR inks, scrolling and wrap), and the
screen face draws that grid through `ui.text_view`, whose SGR tones are the
theme's. The input half below is still a sketch:

```
ESC[row;colH  ESC[K  ESC[2J  ESC[3xm/4xm  ESC[?25l/h   cursor, erase, 16 colours as theme tokens, cursor show/hide
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
rolls back an OTA update and `reboot`s, with no computer and no reflash.

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
session the kernel keeps: `SH` a line of output, then `SH end <status>`), over a web
terminal through the webhost (`device/moy_webhost.py`, a route beside
`/run` and `/update`), and over **SSH**: `ssh moybyte.local` from any
laptop, `scp`/`sftp` into the files tree, mDNS for the hostname (the IDF's
`mdns` component), crypto from the mbedTLS already in the image
(`native/moy_net/moy_net_port.c`), the server Dropbear (MIT-style, small,
built for embedded; owner, 2026-11-03; libssh is LGPL and awkward in a
statically linked FSL image, wolfSSH is GPL). **Sign-in (owner,
2026-11-03)**: password login by default, the password set by the user at
setup (on the Zero, on its webhost page), never a shared default; keys in
`system/authorized_keys`, so `ssh-copy-id` works; passwords and keys are
both valid, and an advanced user turns passwords off
with one line in the sshd_config-like `system/ssh` (`password_auth = no`).
The web terminal asks for the same password, since anyone on the LAN would
otherwise get a root shell; the serial terminal needs no sign-in, being the
owner's cable. **A spike first**: image size against `headroom_floor_kib`,
RAM per session, and internal SRAM on the S3 with WiFi up. Terminal-only
apps are **the app model for the headless Zero**
(`firmware/seeed_xiao_esp32s3_zero/README.md`): `ssh` in from a laptop and
run a command. The Zero runs the C built-ins until WASI commands arrive
(the WASI spike, or P5.5); installable commands reach it with WASI.

## 4. SFTP and WebDAV

**SFTP** over SSH (§3) and **WebDAV** beside the sync RPC
(owner, 2026-11-03) let a computer mount the files tree
(`between_apps.md` §2) natively, the RPC staying
for the browser. WebDAV runs over HTTPS with the device's own certificate
(mbedTLS server mode, made at setup, its fingerprint shown on screen) and
SSH's sign-in (§3) as the credential, since HTTP basic over plain HTTP
sends the secret in the clear and `native/moy_net` has only a TLS client;
if the SSH spike says the S3s cannot carry a second TLS server, they mount
over SFTP only (owner, 2026-11-03).

## 5. Settings as files

**`system/`** holds the console's documents **as plain-text files like
`/etc`** (owner, 2026-11-03), beside the carts folder (`/sd/moybyte/system/`
on a card): `settings` and `sources` (§6) today, and `wifi`,
`authorized_keys`, `ssh`, `crontab` and `hostname` as the phases that need
them arrive; the shell reaches them, and `settings` works over them.

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
`load_record`, `find_installed` and `remove` call it on every tier, and with
the VM stopped the C shell's `pkg list` and `pkg remove` serve it, the
removal forgetting the cart's consent through the settings rows.
