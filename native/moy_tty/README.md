# moy_tty — the shell's face over a stream

The face SSH (`native/moy_ssh`) and the web terminal open: a `moy_sh`
session whose terminal is a socket, its line discipline, its text apps and
its hops to the console's task (`docs/os_2026-11/system.md` §3). One face,
two transports, and nothing of Dropbear's in it, so a board without the SSH
server could still serve the web terminal. How a face is reached is
`moy_tty.h`'s header.

| file | what it is |
|---|---|
| `moy_tty.h` | the face's surface: a transport's open, reap, signal, resize and hold; the shell table; the tasks, the pair and the hop |
| `moy_tty.c` | the face: the socket pair's end, the terminal's line discipline (its caret keys, history and Tab), `exec` with the stream as its stdin, a text app's keys, the exit status a transport reaps |
| `moy_tty_sh.c` | the face's shell: a `moy_sh` session over the board's host table (`native/moy_sh/moy_sh_board.h`), the entries that cross the door hopped, Tab's names (`moy_sh_cands`), a full-screen text app drawn as VT100 |
| `moy_tty_sys.c` | the face task, the hop to the console's task and its tail (`moy_tty_tail`, a weak hook in `native/moy_kernel/moy_loop_board.c`), the socket pair, PSRAM |
| `moy_tty_sftp.c` | the SFTP server a face runs in place of a command (`MOY_TTY_SFTP`, SSH's `sftp` subsystem): SFTP version 3 over the stream, on the session's files tree through the shell's paths, each request one hop to the console's task, the shelf told of changed cart folders through the door's `rescan` (its header; `docs/os_2026-11/system.md` §4) |
| `moy_tty_ws.c` | the web terminal's bridge: the socket the webhost upgraded (`GET /term`), its WebSocket messages to and from a face |
| `modmoy_tty.c` | the module `moy_tty`: `stats()` |
| `host/tty_main.c` | the host harness `tests/test_moy_tty.py` builds: the kernel's webhost with its sign-in (a system folder, or `-` for an open host; `token` and `drop` on stdin mint a QR token and forget every browser) and the bridge in front of the face and shell SSH's harness runs (`native/moy_ssh/host/ssh_main.c`) |

## What runs where

- **the face task** (`moy_tty`, priority 1, core 0, a 24 KB PSRAM stack)
  runs a session's shell: every C built-in, the parse and the line
  discipline, so a long command holds no frame and a slow client blocks only
  this task. Each transport holds it while it serves (`moy_tty_hold`); it is
  made at the first hold and parks while nobody holds it and no face runs,
  never deleted (a deleted PSRAM-stacked task links FreeRTOS's task-state
  query into IRAM, internal SRAM the idle desk would pay).
- **the bridge** (`moy_tty_ws`, the same, a 6 KB PSRAM stack) moves one
  page's WebSocket to and from its face, made at the first `GET /term`.
- **the console's task** serves a HOP at its frame's tail: what must run
  there — the store's files (the volume's borrow resolves through the VM's
  mount table, an internal-flash write disables the cache a PSRAM stack runs
  on, and the T-Deck's card shares the panel's bus) and the door's crossings
  into Python (role commands, the console's processes, `/proc`, `/dev`,
  installed commands) — is handed there and waited for. What a hopped
  command writes is held and sent from the face's task after it, so the
  console never waits on a client. The webhost answers the upgrade on this
  task and lets go of the socket at once.
- **the face's output is gathered** (`moy_tty.c`, OUT_BYTES): each send on
  the pair is a segment on the board's loopback, which queues at most
  `LWIP_LOOPBACK_MAX_PBUFS` (8) and drops the rest, so the face sends in
  pieces of up to 1 KB: when the piece fills, before it waits for input or a
  hop, at the end, and at the shell's poll once 10 ms old. The bridge frames
  what the pair holds when a piece arrives and never waits for more: a wait
  of any length is a scheduler tick, 10 ms on a board, on every key.

**One face at a time** across the transports (owner choice open: two faces
would take two arenas): a page that finds an SSH session's face is closed
1013 `busy`, and an SSH client that finds the page's is refused its channel.
An SFTP server is a face too, a session of the shell's for its paths: while a
shell runs (on the page, or a channel of the same SSH connection) SFTP's
channel is refused, and while SFTP runs a shell is.

`moy_sh`'s running session is per task (`native/moy_sh/moy_sh.h`, TASKS), so
a stream face and the Terminal's run side by side.

## The web terminal

`GET /term?cols=&rows=` with `Upgrade: websocket`, sealed like every gated
route (the browser sign-in, `docs/os_2026-11/system.md` §3; the protocol
`native/moy_net/moy_seal.h`): a bodiless request's tag rides in `t=`, so a
replayed upgrade is refused. No
session is 401 `{"error":"session"}`, a seal that does not open 401
`{"error":"auth"}` and a source the backoff holds 429 `{"error":"wait","ms":N}`,
all before any upgrade. A browser's WebSocket reports none of them, so the page
asks the same route without the upgrade why a socket failed: a sealed 200
`{"term":1}` says the session is good and a shell is here, and an image without
the bridge answers 404. The messages are `moy_tty_ws.c`'s header: binary keys
in, binary bytes out, a text `{"size":[C,R]}` for a resize, and a close that
says why it ended; under a session each message crosses as one sealed binary
frame, its first byte `k` or `s` saying which, and one that fails its seal
ends the socket (1008 "seal"). The end of its session cuts the terminal
(1008 "revoked"): the browser forgotten, the password changed, the session
unused for 30 minutes (the page's own frames count as use) or taken by newer
sign-ins. The subprotocol is `moy.sh`; the board
link (`docs/os_2026-11/web_terminal_spike.md` §7) is a route of its own whose
shell channel carries this stream.

The page's sign-in is the terminal's (owner, 2026-11-06, the spike's §4): on a
console a paired browser can already run any code (`POST /sync` writes a cart,
`POST /run` plays it), so a shell behind the same sign-in adds no exposure.

## Memory

An SFTP face adds its request and answer buffers (34 KB and 32 KB) and
its handles, PSRAM, for its length. A face's record (its line, history and output piece, about 7 KB), the
session's 32 KB arena (`moy_tty_sh.c`, the most one used is `stats()`'s
`arena_peak`, measured in #246) and the face task's stack are PSRAM; the
bridge's two 2 KB buffers live while a page is connected.

## Boards

Every console takes the module (a `[native.shared]` default, with `moy_sh`,
which the face runs; `tests/test_board_toml.py` holds that a board with
`moy_ssh` has both). The Zero takes it without the web terminal's bridge
(its `native/micropython.cmake` sets `MOY_TTY_WEB_TERM` OFF, so its GET /term
answers 404): the page a Zero serves may carry no pin, and SSH is its one
face. With no frame loop it serves a face's hops from its poll loop
(`moy_tty.tail()`, `zero_host.serve`).
