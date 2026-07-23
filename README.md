# Moybyte

[![CI](https://github.com/nikola-j/moybyte/actions/workflows/ci.yml/badge.svg)](https://github.com/nikola-j/moybyte/actions/workflows/ci.yml)

Moybyte is a PC-first simulator and SDK for a future ESP32 kids' creative coding console.

> **Two systems live in this repo** (see `CLAUDE.md`): the original **`.moyproj` SDK**
> (documented below), and the newer **`.moy` console** — a TIC-80-style
> "fantasy workstation" where *everything is a cartridge*, now running the shipped
> **v0.5 shell** (everything-is-a-process). The `.moy` console is where current
> feature work happens.

## v0.5 console (current direction)

One shared console runs on both the PC and the LilyGO T-Deck Plus (it renders the
same pixels on each). Run it on the PC:

```bash
.venv/bin/python tools/simulate_desktop.py    # launcher -> cartridge -> code/paint editors
```

Build + flash the device firmware (MicroPython):

```bash
MOYBYTE_SKIP_VFS_BOOT=1 make firmware-build-lilygo-micropython
make firmware-flash-lilygo-micropython PORT=/dev/ttyACM0
```

Design + current status: [`moybyte_Console_Plan_v0_5.md`](moybyte_Console_Plan_v0_5.md)
and the shell UX reference [`docs/shell_ux_v1.md`](docs/shell_ux_v1.md). Working
orientation: `CLAUDE.md`. Issue status at a glance: run `make sync-issues` and open
`docs/issues/STATUS.md` (see [`docs/issue_taxonomy.md`](docs/issue_taxonomy.md)).

---

## `.moyproj` SDK (original)

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
.venv/bin/moybyte new my_game
.venv/bin/moybyte run my_game.moyproj --headless --frames 60
```

Pack a project bundle:

```bash
.venv/bin/moybyte pack examples/tiny_runner.moyproj --out /tmp/tiny_runner.kc8
```

Run a desktop simulator window when pygame is available:

```bash
.venv/bin/moybyte run examples/tiny_runner.moyproj --fps 30 --scale 4
```

Press `Esc` or close the window to exit.

Bundled examples:

```text
examples/tiny_runner.moyproj
examples/blocks_demo.moyproj
examples/music_player_stub.moyproj
examples/radio_pong_stub.moyproj
```

The firmware-facing API contract is in
`docs/history/firmware_runtime_contract.md`. The first concrete hardware target is the
LilyGO T-Deck Plus, with board-specific details kept out of the portable API.

Kid projects import the small public API from `moybyte`:

```python
from moybyte import *

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

## License

Everything you'd do as a person is free: run the simulator, flash the firmware
on your own board, modify it, teach with it, make and sell your own carts.
Selling hardware (or a commercial product) built on the console requires a
commercial license, and that restriction expires per release two years after
publication. Details and the exact split: [`LICENSE.md`](LICENSE.md) — SDK and
examples are [MIT](LICENSES/MIT.md); the console and firmware are
[FSL-1.1-MIT](LICENSES/FSL-1.1-MIT.md) (source-available, becomes MIT after
2 years). The `.moy` cart format and API are an open specification; carts you
author are yours. The rationale lives in
[`docs/pricing_release_model_v1.md`](docs/pricing_release_model_v1.md).
