# KidCode

[![CI](https://github.com/nikola-j/kidcode/actions/workflows/ci.yml/badge.svg)](https://github.com/nikola-j/kidcode/actions/workflows/ci.yml)

KidCode is a PC-first simulator and SDK for a future ESP32 kids' creative coding console.

> **Two systems live in this repo** (see `CLAUDE.md`): the original **`.kcproj` SDK**
> (documented below), and the newer **v0.4 `.kcart` console** — a TIC-80-style
> "fantasy workstation" where *everything is a cartridge*. The v0.4 console is where
> current feature work happens.

## v0.4 console (current direction)

One shared console runs on both the PC and the LilyGO T-Deck Plus (it renders the
same pixels on each). Run it on the PC:

```bash
.venv/bin/python tools/simulate_desktop.py    # launcher -> cartridge -> code/paint editors
```

Build + flash the device firmware (MicroPython):

```bash
KIDCODE_SKIP_VFS_BOOT=1 make firmware-build-lilygo-micropython
make firmware-flash-lilygo-micropython PORT=/dev/ttyACM0
```

Design + current status: [`KidCode_Console_Plan_v0_4.md`](KidCode_Console_Plan_v0_4.md)
(see its "Implementation status" section). Working orientation: `CLAUDE.md`.

---

## `.kcproj` SDK (original)

The first goal is the edit-run-test loop on a normal computer:

```bash
make setup
make test
make run-headless
```

Check that project code stays inside the portable subset intended for a future device runtime:

```bash
make check-portable
```

Create a new project:

```bash
.venv/bin/kidcode new my_game
.venv/bin/kidcode run my_game.kcproj --headless --frames 60
```

Pack a project bundle:

```bash
.venv/bin/kidcode pack examples/tiny_runner.kcproj --out /tmp/tiny_runner.kc8
```

Run a desktop simulator window when pygame is available:

```bash
.venv/bin/kidcode run examples/tiny_runner.kcproj --fps 30 --scale 4
```

Press `Esc` or close the window to exit.

Bundled examples:

```text
examples/tiny_runner.kcproj
examples/blocks_demo.kcproj
examples/music_player_stub.kcproj
examples/radio_pong_stub.kcproj
```

The firmware-facing API contract is in
`docs/firmware_runtime_contract.md`. The first concrete hardware target is the
LilyGO T-Deck Plus, with board-specific details kept out of the portable API.

Kid projects import the small public API from `kidcode`:

```python
from kidcode import *

player = sprite("robot", x=60, y=60)

@game.update
def update(dt):
    if button("right"):
        player.x += 2

@game.draw
def draw():
    clear()
    draw_sprite(player)

run()
```

The child-facing API and generated project code are kept portable so they can later be reimplemented on a MicroPython/ESP32 runtime. PC-only libraries belong in the simulator, CLI, compiler, and tests.
