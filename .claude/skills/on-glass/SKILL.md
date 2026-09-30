---
name: on-glass
description: Drive an attached Moybyte board -- find its port, check who holds it, flash it, push and run a cart, take a screenshot, read state/pmem/heap, run its on-glass suite, and get the desk back when it is wedged. Use before opening any serial port or writing a script that talks to a board.
---

# On glass

One command per step: `tools/board.py BOARD VERB`. BOARD is the board.toml
`[board] ota` id: `tdeck`, `guition_s3` (both ESP32-S3), `p4` (Waveshare, the
only CH343), `guition_p4`, and the headless `xiao_zero` — read from the tree,
so a new board is drivable the day its board.toml lands. Do not write a pyserial
script: each board opens with its own, opposite line state, and `P4Board`
(`tools/p4_autotest.py`), which every verb goes through, is what gets it right.

## Which port

```bash
tools/board.py ports                 # every port: board, usb id, serial, and who holds it
tools/board.py tdeck port            # its /dev/serial/by-id path; exit 1 if held
```

Nothing is opened. A port is settled by its USB serial (the chip's MAC, read
from sysfs) against this machine's learned identities
(`~/.config/moybyte/boards.json`), the Zero's board.toml `serial_number`, or a
usb id only one board declares. A board that answers its identity on a port is
learned, so the first time name the port once — `tools/board.py tdeck --port
/dev/ttyACM2 state` — or record what you already know without opening anything:
`tools/board.py ports --remember E0:72:A1:CC:E7:08=tdeck`. `ports --probe` asks
each unclaimed, unheld port once.

**A port another process holds is somebody's session** — an on-glass suite,
another agent's measurement. Every verb refuses it and names the pid; a second
reader splits the replies and both sides fail ("multiple access on port" on one,
"no STATE reply" on the other). `--force` is for your own stale process only.

## The loop

```bash
tools/board.py tdeck state                    # stack, cart, error, diag, wifi (--json: all)
tools/board.py tdeck push path/to/cart.moy    # copy to the store, rescan the shelf
tools/board.py tdeck run "Brick Siege"        # by title; checks it came up without an error
tools/board.py tdeck shot /tmp/tdeck.png      # then Read the PNG to see it
tools/board.py tdeck leave                    # end the cart, reopen the desk it had
tools/board.py tdeck py "ws.screen" "len(ws.carts.all)"   # eval on the live console
tools/board.py tdeck py --exec "ws._x = 1"    # statements, in the shared ws._g namespace
tools/board.py tdeck pmem [--text]            # the running cart's 256 cells (--text: a string)
tools/board.py tdeck mem                      # python heap, internal SRAM, PSRAM
tools/board.py tdeck tap 160 120              # swipe X0 Y0 X1 Y1 [FRAMES] / open settings
tools/board.py tdeck tail 10 --send "diag 1" --grep PERF
tools/board.py tdeck perf "Brick Siege"       # see the perf skill
```

`perf` ends its cart and puts diag and uncap back, `push` changes only the
store, `shot`/`pmem`/`state`/`mem` change nothing, and `desk` returns a board
to where it boots (no cart, launcher on top, the desk
open on a windowed tier). **Leave a board the way you found it** — a leftover
menu or cart is what the next suite trips on.

**`shot`**: `--source screen` (default) is the buffer the panel last took; on
the Guition P4 that is the portrait scan buffer, turned back to the landscape
desk. `--source game` is the cart's canvas, cropped to its view. On the two S3
boards a small-canvas game and a compiled cart present straight from their own
buffer (the fold), so the screen buffer shows the desk under them: use
`--source game` for a Python or Lua cart there. The frame is taken in one
command between two frames, then sent deflated in bands.

**Carts.** A folder cart pushes as it is. A compiled cart needs its per-chip
module, built and signed on the host — the chip is `[board] chip` (`esp32s3`
for tdeck/guition_s3, `esp32p4` for p4/guition_p4):

```bash
python3 tools/wasm_cart.py tests/fixtures/wasm/hello.moy /tmp/hello.moy --chip esp32s3
python3 tools/jet_cart.py /tmp/carts --cart teapot --chip esp32s3
python3 experiments/wasm_aot/doom/build_cart.py --out /tmp/carts   # never committed or pushed to a store
```

## Flash, reboot, wait

```bash
tools/board.py p4 flash --build     # build.sh, board_flash.py with its [flash] facts, wait for the desk
tools/board.py guition_s3 reboot    # attach-only: esptool --after hard_reset; P4: the RTS pulse
tools/board.py tdeck reboot --soft  # Ctrl-C, then Ctrl-D: for a board sitting at >>>
tools/board.py guition_s3 wait      # until `state` answers (default 120 s)
```

Build the two P4s one at a time (they race on the component manager's git
cache). A board takes most of a minute to reach the desk after a reset — the
Guition S3 from its card the longest — and `wait` is what knows when it has;
a suite started earlier errors every test with "did not answer `state`".

## When it will not answer

1. `tools/board.py X state` names what it sees. `desk` when the stack is wrong.
2. It sits at `>>>` (somebody sent `quit`, which leaves the DESKTOP for the
   REPL, not the cart): `reboot --soft`. Only once `>>>` is up — a Ctrl-D sent
   before it is swallowed and the board then answers nothing, Ctrl-C included.
3. Silent: `reboot`. On the S3/Guition P4 that is esptool driving the SoC's
   USB-JTAG (the port node survives it); never an RTS pulse under an open
   handle there. A T-Deck wedged for esptool connects with `--before
   usb_reset`, which its `[flash]` block declares and `reboot` uses.
4. Still silent after a reboot: the image. Hold the T-Deck's trackball (GPIO0)
   while powering on to reach the ROM loader, then `flash`.

When a suite fails on a board you have just been driving, suspect your own
leftovers first: `desk`, check `state`'s stack, reboot if unsure.

## The suites

Each board has one, gated on its own variable, over `tests/on_glass.py`:

```bash
MOYBYTE_TDECK_PORT=$(tools/board.py tdeck port) .venv/bin/python -m pytest tests/test_tdeck_on_glass.py
MOYBYTE_GUITION_PORT=$(tools/board.py guition_s3 port) .venv/bin/python -m pytest tests/test_guition_on_glass.py
MOYBYTE_P4_PORT=$(tools/board.py p4 port) .venv/bin/python -m pytest tests/test_p4_on_glass.py
MOYBYTE_GUITION_P4_PORT=$(tools/board.py guition_p4 port) .venv/bin/python -m pytest tests/test_guition_p4_on_glass.py
```

The Waveshare's suite resets its board at the start; the attach-only boards
are attached to as they are, so their desk must be clean. `.claude/rules/testing.md`
has the rules that bite when you write one.
