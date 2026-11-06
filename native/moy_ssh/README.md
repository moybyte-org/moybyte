# moy_ssh — the SSH server

`ssh` into a console (`docs/os_2026-11/system.md` §3): Dropbear's server
inside the kernel's process, its session's shell the C `moy_sh` over a face
of its own. How the server runs a session and how the shell is reached is
`moy_ssh.h`'s and `moy_ssh_face.h`'s headers. Built only into an image whose
build sets `MOYBYTE_SSH=1` (`micropython.cmake`).

| file | what it is |
|---|---|
| `moy_ssh.h`, `moy_ssh.c` | the server: setup (the host key's bytes, the stored hash), the accept loop, a session run to its end and its epoch freed, the counted PSRAM allocator, the POSIX names Dropbear asks for |
| `moy_ssh_face.h`, `moy_ssh_face.c` | the shell's face over the channel: the socket pair, the terminal's line discipline, `exec`, the exit status Dropbear reaps |
| `moy_ssh_sh.c` | the face's shell on a board: a `moy_sh` session over the board's host table (`native/moy_sh/moy_sh_board.h`) |
| `modmoy_ssh.c` | the module `moy_ssh`: `start`, `stop`, `stats`, `hash`; the server's task, its stack PSRAM |
| `port/` | Dropbear's options (`localoptions.h`), its `config.h`, the names the port renames (`moy_ssh_port.h`), and in `port/esp/` the headers ESP-IDF lacks |
| `dropbear/`, `patches/`, `dropbear_vendor.json` | the vendored Dropbear (`UPSTREAM.md`) |
| `host/ssh_main.c` | the host harness `tests/test_moy_ssh.py` builds: the server and face over a TCP port with a stand-in shell |

## What runs where

The server task (`moy_ssh`, priority 1, core 0, a 16 KB PSRAM stack) owns the
network and the crypto. A session's command line runs on the VM's task, at
the frame loop's tail (`moy_ssh_face_tail`, a weak hook in
`native/moy_kernel/moy_loop_board.c`), because the shell's role commands
cross into Python there; a line runs to its end inside one tail.

## Memory

Every Dropbear allocation is PSRAM and counted (`moy_ssh_stats`). Dropbear's
large globals (`ses`, `svr_ses`, `opts`, `svr_opts`) and libtomcrypt's
descriptor tables are PSRAM too, taken at the first `start`
(`moy_ssh_globals`): each global's name is the object a pointer points at
(`port/moy_ssh_port.h`). What stays in internal SRAM is Dropbear's algorithm
lists (`.data`, written at run time) and a few words. The store's files are
the VM's, so the host key and the hash come in from Python and a new key goes
back out to it.

## Sign-in

A password, checked against `$moy1$SALT$HEX` (SHA-256 iterated, `moy_ssh_hash`):
any user name is the console's one account.
