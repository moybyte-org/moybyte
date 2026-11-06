# moy_ssh — the SSH server

`ssh <name>.local` into a console (`moybyte-XXXX.local` until Settings'
Name is set) (`docs/os_2026-11/system.md` §3):
Dropbear's server inside the kernel's process, its session's shell the C
`moy_sh` over a face of its own. How the server runs a session is
`moy_ssh.h`'s header, how the shell is reached `moy_ssh_face.h`'s. The
console's side — when it serves, the password, the radio and the mDNS name —
is `runtime/system/ssh_service.py`.

| file | what it is |
|---|---|
| `moy_ssh.h`, `moy_ssh.c` | the server: setup (the host key read or made), the accept loop, a connection's sign-in read from the store, a session run to its end and its epoch freed, the hop to the console's task, the counted PSRAM allocator, the POSIX names Dropbear asks for |
| `moy_ssh_run.c` | the run: the server's task and the face's, made at the first start and parked between a stop and the next, the console's start and stop (`moy_ssh.h`'s THE RUN) |
| `moy_ssh_face.h`, `moy_ssh_face.c` | the shell's face over the channel: the socket pair, its own task, the terminal's line discipline, `exec` with the channel as its stdin, a text app's keys, the exit status Dropbear reaps |
| `moy_ssh_sh.c` | the face's shell: a `moy_sh` session over the board's host table (`native/moy_sh/moy_sh_board.h`), the entries that cross the door hopped, `/etc` the server's system folder, a full-screen text app drawn as VT100 |
| `modmoy_ssh.c` | the module `moy_ssh`: `start`, `stop`, `stats`, `hash`, over the run |
| `port/` | Dropbear's options (`localoptions.h`), its `config.h`, the names the port renames (`moy_ssh_port.h`, `moy_ssh_names.h`), and in `port/esp/` the headers ESP-IDF lacks |
| `dropbear/`, `patches/`, `dropbear_vendor.json` | the vendored Dropbear (`UPSTREAM.md`) |
| `host/ssh_main.c` | the host harness `tests/test_moy_ssh.py` builds: the run, the face and the board's shell over a folder, the console's task a thread, and a VM that answers `passwd` |

## Crypto

Dropbear brings its own: curve25519 key exchange, an ed25519 host key and
client keys, chacha20-poly1305, SHA-2, from its bundled libtomcrypt. The
image's mbedTLS has none of curve25519, chacha20 or ed25519, so the server
uses none of it.

## What runs where

Three tasks, none of them the VM's:

- **the server** (`moy_ssh`, priority 1, core 0, a 16 KB PSRAM stack) owns
  the network and the crypto, one connection at a time;
- **the face** (`moy_ssh_face`, the same, a 24 KB PSRAM stack) runs the
  session's shell: every C built-in, the parse and the line discipline, so
  a long command holds no frame and a slow client blocks only this task;
- **the console's task** serves a HOP at its frame's tail (`moy_ssh_tail`, a
  weak hook in `native/moy_kernel/moy_loop_board.c`): what must run there —
  the store's files (the volume's borrow resolves through the VM's mount
  table, an internal-flash write disables the cache a PSRAM stack runs on,
  and the T-Deck's card shares the panel's bus) and the door's crossings into
  Python (role commands, the console's processes, `/proc`, `/dev`, installed
  commands) — is handed there and waited for. What a hopped command writes
  is held and sent from the face's task after it, so the console never waits
  on a client. The connection's sign-in files are read the same way.

The server's two tasks are made at the first start and park between a stop
and the next start, never deleted: deleting a PSRAM-stacked task links
FreeRTOS's task-state query into IRAM, internal SRAM the idle desk would pay.
A stop asked from a session's own command (`passwd -d` over SSH) stands
until that session ends, and a start in the same session (`passwd x`)
withdraws it (`moy_ssh.h`, THE RUN).

`moy_sh`'s running session is per task (`native/moy_sh/moy_sh.h`, TASKS), so
an SSH session and the Terminal's run side by side.

## Memory

Every Dropbear allocation is PSRAM and counted (`moy_ssh_stats`). Dropbear's
large globals (`ses`, `svr_ses`, `opts`, `svr_opts`), libtomcrypt's
descriptor tables and the algorithm lists (which a session writes) are PSRAM
too, taken at the first `start` (`moy_ssh_globals`): each global's name is
the object a pointer points at (`port/moy_ssh_port.h`). The run keeps one
pointer of internal SRAM, the server's state one more. A session's shell
arena is 32 KB of PSRAM (`moy_ssh_sh.c`), not the Terminal's 128 KB: the most
a session used is `stats()`'s `arena_peak`, measured in #246 over
ssh-copy-id's install command, pipelines, `top` and `edit`.

## Sign-in

At every connection's start the server reads the store's system folder
(`moy_ssh.h`'s SIGN-IN): `password`, the hash (`$moy1$SALT$HEX`, SHA-256
iterated, `moy_ssh_hash`; no password, no SSH), `ssh`'s `password_auth`, and
`authorized_keys` (ed25519 keys). Any user name is the console's one
account. In the shell `/etc` is that folder, and `/etc/dropbear` names it
too, which is where ssh-copy-id installs a key on a Dropbear server.

## Boards

Every console takes the module (each board.toml's `[native.shared]`); the
image cost and the internal SRAM are in #246. The Zero denies it with
`moy_sh` until its phase (`docs/os_2026-11.md` §7, phase 4).
