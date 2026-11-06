# moy_sh — the terminal's shell

The C shell of `docs/os_2026-11/system.md` §1: bash-compatible syntax over
the kernel's processes and the store, derived from dash (`UPSTREAM.md`). How a
session runs — the push-model input, the fork as an arena snapshot, the exec as
a host spawn, the built-ins — is `moy_sh.h`'s header.

| file | what it is |
|---|---|
| `moy_sh.h` | the public interface: a session, its host table, feeding it text |
| `moy_sh.c` | the session: dash's state set up, a run, the fork, the wait, the exec, the role commands, `sh` |
| `moy_sh_sys.c` | the OS dash sees: descriptors over pipe buffers and whole-file objects, paths, the store through `moy_vol`, `/dev` `/proc` `/bin`, signals |
| `moy_sh_cmds.c` | the C core built-ins (file commands, `ps`, `free`, `df`, `log`/`dmesg`, `reboot`, `date`, `sleep`, `clear`, `help`), sorted |
| `moy_sh_arena.c` | the session's region: first-fit with boundary tags, saved and restored whole for a fork |
| `moy_sh_fmt.c` | the shell's `vsnprintf`: every integer conversion at every length, which a board's nano formatter lacks |
| `moy_sh_g.h` | every dash global as a field of one record, which lives in the arena |
| `moy_sh_posix.h`, `dash_config.h` | the shim dash compiles against (`-include dash_config.h`) |
| `moy_sh_int.h` | what the three `.c` files share |
| `dash/`, `patches/`, `dash_vendor.json`, `dash_pin.h` | the vendored dash (`UPSTREAM.md`) |
| `moy_sh_board.c`, `moy_sh_board.h` | a board's host table (the kernel's figures in C, the console's across the ROLE door: `runtime/shell/sh_door.py` serves the processes, /proc, /dev, /bin and the installed commands, `runtime/shell/sh_roles.py` the role commands) and the serial face, the dev channel's `sh` word |
| `moy_sh_store.c` | the console's commands the shell serves from the store while no VM runs (`settings`; `pkg list` and `pkg remove` over `native/moy_store/moy_shelf.h`), over the C the console calls (`moy_sh_board.h`) |
| `moy_sh_screen.c`, `moy_sh_screen.h` | the screen face: a session over the board's table whose terminal is a grid, its Ctrl-C read from the kernel input table's latches while a run holds the frame; the Terminal's face draws and feeds it |
| `moy_sh_face.c`, `moy_sh_face.h` | the Terminal's face: a screen session with its prompt line, history, completion and key rows, and their input, drawn through the toolkit's engine (`native/moy_ui/moy_ui_api.h`) on a frame the caller begins; the Terminal app (`runtime/apps/terminal_app.py`) hands it the console's frames and the kernel's recovery console its own. Built where the image carries the toolkit |
| `moy_sh_floor.c`, `moy_sh_floor.h` | the recovery console's screen: the Terminal's face under the reason, the crash record's lines and the choices (Try again, Safe start, Reboot) as buttons, its words at the prompt the floor's; the default seed theme's dark look in the kernel's look, petme128 through a RUN sink, drawn whole into a logical buffer the kernel turns onto its panel (`native/moy_kernel/moy_kernel.c`'s `moy_kernel_recovery`); `tests/test_recovery_floor.py` renders the same C on the host. Built with the face |
| `moy_sh_app.c`, `moy_sh_app.h` | the text apps every face hosts (`edit`, `top`): a full-screen app's own screen and keys, a line app over the prompt, the serial face's caret notation |
| `moy_sh_edit.c` | `edit FILE`, the terminal's editor (#117): a gap buffer, saved through the store's crash-safe write, the floor's way to fix `system/settings` |
| `moy_sh_top.c` | `top`: the rows `ps` reads with the task manager's CPU and memory, a second at a time; Kill sends SIGTERM |
| `moy_term.c`, `moy_term.h` | the terminal's cell grid: the VT100 subset a line program and a full-screen app write, a scrollback ring, its lines as SGR text for `ui.text_view` |
| `modmoy_sh.c`, `micropython.cmake` | the MicroPython module (a door command's streams, `line`, `interrupted`, the screen face's `Screen`) and the board build: the shell is its own static library, so dash's shim reaches no other file |
| `host/sh_main.c`, `host/board_main.c` | the host harnesses the suite runs: a session over a folder, and the serial face (or with `-screen` the screen face) over the board's table |

On CPython the module is `runtime/host/moy_sh.py`: the same names by ctypes,
the shell built as its own archive and linked whole into the host's one
library (`runtime/host/host_native.py`), so the simulator and the host tests
run a session over the console's own door, log and store.

## Memory

A session takes its record and its arena from the host's `alloc` (PSRAM on a
board, `heap_caps` with the default heap only where there is no PSRAM); a pipe's buffer, a file read whole and a fork's snapshot are taken
while a run needs them and returned after. The screen face takes its record,
its session and its grid the same way at its first open (`moy_sh_screen_new`)
and gives them back at `close`. The objects hold one writable word,
`moy_sh_g` (the running session's state, and between runs the serial face's,
parked there so the board keeps no root of its own): `tests/test_moy_sh.py`
compiles them as a board does and fails on any other `.data` or `.bss`. The Terminal's face takes its record (the line, the history, the candidates and the log's row buffers) the same way at `moy_sh_face_new` and gives it back at `moy_sh_face_free`; a frame allocates nothing but the log's row buffers when the grid grows. The recovery floor's screen takes its record, its UI context, its shape and its pixels the same way at `moy_sh_floor_new`. A text app takes its front, its record and its screen (`moy_sh_app_open`), and the editor its text, from the same `alloc` while it is open.

## The suite

`tests/test_moy_sh.py` builds `host/sh_main.c` under AddressSanitizer and
UBSan and runs every script in `tests/moy_sh/` — one per feature and one per C
built-in — against the transcript beside it (`MOYBYTE_UPDATE_GOLDENS=1`
rewrites them). The harness's stand-in OS is listed in its header.
`tests/moy_sh/serial/` does the same for the serial face: each line fed as
the `sh` word feeds it, with the VM stopped or (a `vm_` script) a stand-in
console behind the door. Every serial and suite script replays through the
screen face too, and its grid's lines must be the serial face's output.
An `app_` script drives a text app over the serial face and replays
through no other face, since the screen face draws the app on a screen of
its own. `tests/test_moy_term.py` holds the grid's escapes,
`tests/test_terminal.py` the Terminal app and its text apps over the host
binding, `tests/test_recovery_floor.py` the editor on the floor.
