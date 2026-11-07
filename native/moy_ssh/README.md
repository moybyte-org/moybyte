# moy_ssh — the SSH server

`ssh <name>.local` into a console (`moybyte-XXXX.local` until Settings'
Name is set) (`docs/os_2026-11/system.md` §3):
Dropbear's server inside the kernel's process, its session's shell the C
`moy_sh` over the stream face the web terminal opens too
(`native/moy_tty/README.md`). How the server runs a session is `moy_ssh.h`'s
header, how the shell is reached `native/moy_tty/moy_tty.h`'s. The
console's side — when it serves, the password, the radio and the mDNS name —
is `runtime/system/ssh_service.py`.

| file | what it is |
|---|---|
| `moy_ssh.h`, `moy_ssh.c` | the server: setup (the host key read or made), the accept loop, a connection's sign-in read from the store, a session run to its end and its epoch freed, the counted PSRAM allocator, the POSIX names Dropbear asks for, its channel's command opened as a face (`moy_ssh_command` over `moy_tty_open`), the `sftp` subsystem as the face's SFTP server, `/etc` the server's system folder |
| `moy_ssh_run.c` | the run: the server's task, made at the first start and parked between a stop and the next, and its hold on the face task; the console's start and stop (`moy_ssh.h`'s THE RUN) |
| `modmoy_ssh.c` | the module `moy_ssh`: `start`, `stop`, `stats`, `hash`, over the run |
| `port/` | Dropbear's options (`localoptions.h`), its `config.h`, the names the port renames (`moy_ssh_port.h`, `moy_ssh_names.h`), and in `port/esp/` the headers ESP-IDF lacks |
| `dropbear/`, `patches/`, `dropbear_vendor.json` | the vendored Dropbear (`UPSTREAM.md`) |
| `host/ssh_main.c` | the host harness `tests/test_moy_ssh.py` builds: the run, the face (`native/moy_tty`) and the board's shell over a folder, the console's task a thread, the face's socket pair a board's loopback (its bytes cross a thread, a little late, its sends counted), and a VM that answers `passwd` and the console's rows |

## SFTP

`sftp` and `scp` (`docs/os_2026-11/system.md` §4): Dropbear's `sftp`
subsystem is on (`port/localoptions.h`), its command the name
`SFTPSERVER_PATH` gives (`internal-sftp`), which `moy_ssh_command` opens as
the face's SFTP server (`native/moy_tty/moy_tty_sftp.c`) instead of a shell
command; an `exec` of that name does the same, as on OpenSSH. It signs in as
a shell does, counts as the one face (`native/moy_tty/README.md`), and
`tests/test_moy_sftp.py` drives it with OpenSSH's `sftp -b` and `scp -s`.

## Crypto

Dropbear brings its own: curve25519 key exchange, an ed25519 host key and
client keys, chacha20-poly1305, SHA-2, from its bundled libtomcrypt. The
image's mbedTLS has none of curve25519, chacha20 or ed25519, so the server
uses none of it.

## What runs where

- **the server** (`moy_ssh`, priority 1, core 0, a 16 KB PSRAM stack) owns
  the network and the crypto, one connection at a time; it reads a
  connection's sign-in files through the hop to the console's task;
- **the face** is `native/moy_tty`'s task, which the server holds while it
  serves: the session's shell, the hops and the gathered output are that
  module's (its README, What runs where).

The server's task is made at the first start and parks between a stop and
the next start, never deleted, for the face task's reason
(`native/moy_tty/README.md`, What runs where).
A stop asked from a face's own command (`passwd -d` over SSH or the web
terminal) stands until that session ends, and a start in the same session
(`passwd x`) withdraws it (`moy_ssh.h`, THE RUN). The console keeps the
radio and the mDNS name until `moy_ssh.parked()` says the server has parked
and its face has ended, so that session ends cleanly even where `ssh` is the
radio's only holder.

## Memory

Every Dropbear allocation is PSRAM and counted (`moy_ssh_stats`). Dropbear's
large globals (`ses`, `svr_ses`, `opts`, `svr_opts`), libtomcrypt's
descriptor tables and the algorithm lists (which a session writes) are PSRAM
too, taken at the first `start` (`moy_ssh_globals`): each global's name is
the object a pointer points at (`port/moy_ssh_port.h`). The run keeps one
pointer of internal SRAM, the server's state one more. A session's shell and
its arena are the face's (`native/moy_tty/README.md`, Memory); `stats()`'s
`arena_peak` reports it.

## Sign-in

At every connection's start the server reads the store's system folder
(`moy_ssh.h`'s SIGN-IN): `password`, the hash (`$moy1$SALT$HEX`, SHA-256
iterated, `moy_ssh_hash`; no password, no SSH), `ssh`'s `password_auth`, and
`authorized_keys` (ed25519 keys). Any user name is the console's one
account. In the shell `/etc` is that folder, and `/etc/dropbear`, `/.ssh`
and the home's `.ssh` (a session's home is the user's files folder,
`docs/os_2026-11/system.md` §4) name it too: an older ssh-copy-id installs
a key on a Dropbear server in the first, a current one in the home's.

## Boards

Every board takes the module (each board.toml's `[native.shared]`) with
`native/moy_tty`; the image cost and the internal SRAM are in #246. On the
headless Zero SSH is the shell's one face, its sessions' hops served by its
poll loop and its door `firmware/seeed_xiao_esp32s3_zero/modules/zero_shell.py`;
it holds no `ssh` lease, its station and mDNS name being up while it serves
its webhost (`runtime/system/ssh_service.py`).
