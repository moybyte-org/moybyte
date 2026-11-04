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
| `moy_sh_g.h` | every dash global as a field of one record, which lives in the arena |
| `moy_sh_posix.h`, `dash_config.h` | the shim dash compiles against (`-include dash_config.h`) |
| `moy_sh_int.h` | what the three `.c` files share |
| `dash/`, `patches/`, `dash_vendor.json`, `dash_pin.h` | the vendored dash (`UPSTREAM.md`) |
| `host/sh_main.c` | the host harness the suite runs |

## Memory

A session takes its record and its arena from the host's `alloc` (PSRAM on a
board); a pipe's buffer, a file read whole and a fork's snapshot are taken
while a run needs them and returned after. The objects hold one writable word,
`moy_sh_g` (the running session's state): `tests/test_moy_sh.py` compiles them
as a board does and fails on any other `.data` or `.bss`.

## The suite

`tests/test_moy_sh.py` builds `host/sh_main.c` under AddressSanitizer and
UBSan and runs every script in `tests/moy_sh/` — one per feature and one per C
built-in — against the transcript beside it (`MOYBYTE_UPDATE_GOLDENS=1`
rewrites them). The harness's stand-in OS is listed in its header.
