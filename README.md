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
