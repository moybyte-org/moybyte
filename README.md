# KidCode

[![CI](https://github.com/nikola-j/kidcode/actions/workflows/ci.yml/badge.svg)](https://github.com/nikola-j/kidcode/actions/workflows/ci.yml)

KidCode is a PC-first simulator and SDK for a future ESP32 kids' creative coding console.

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

Prepare the current LilyGO T-Deck Plus target artifact:

```bash
make device-doctor
./.venv/bin/kidcode lilygo-next
make export-lilygo-example
```

Build the first serial-only firmware smoke test:

```bash
make firmware-build-lilygo
```

Flash and monitor it once the board appears as a serial device:

```bash
make device-port
make firmware-upload-lilygo PORT=/dev/ttyACM0
make firmware-monitor-lilygo PORT=/dev/ttyACM0
```

Or run the upload, short monitor capture, and serial check in one step:

```bash
make firmware-smoke-lilygo PORT=/dev/ttyACM0
```

Expected serial smoke output includes:

```text
KidCode firmware smoke test
Board id: lilygo_t_deck_plus
Bundled project: tiny_runner
Bundle bytes: <non-zero>
Display: KidCode native tiny_runner canvas
KidCode heartbeat 0
```

Save a monitor log and verify it with:

```bash
make firmware-smoke-check-lilygo LOG=/tmp/kidcode_lilygo_serial.log
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
