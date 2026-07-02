# Firmware Runtime Contract

> **Legacy — `.moyproj` device contract.** This describes the old `.moyproj` SDK's
> device runtime (`from moybyte import *`, `run/sprite/text/clear/button`, a **128×128**
> canvas). The shipping console is the v0.4 `.moy` runtime — a **320×240** indexed
> canvas with a TIC-80-style API — documented in **[`moy_cart_api.md`](../moy_cart_api.md)**.
> Still useful for the `.moyproj` SDK's permission/service model.

This is the contract a future device runtime must satisfy so existing Moybyte
projects continue to run when moved from the PC simulator to hardware.

The first concrete hardware target is the user's LilyGO T-Deck Plus. Board
details such as display driver, keyboard scan behavior, speaker path, SD card
mount, and pin assignments must live in a board profile after they are verified
against the exact device revision. Do not hardcode those details into the
portable Moybyte API.

## Runtime Layers

Portable project layer:

```text
user main.py
generated block code
from moybyte import *
```

Device runtime layer:

```text
moybyte API shim
runtime loop
screen service
input service
audio service
file service
radio service
permission checks
friendly error screen/log
```

PC-only layer:

```text
CLI
pytest tests
block compiler
manifest validator
pygame backend
project packer
```

The firmware does not need to run the whole repository. It needs to provide the
same child-facing API and lifecycle semantics for packaged projects.

## Public API Required On Device

The device runtime must expose these names from `moybyte`:

```text
game
run(update=None, draw=None)
sprite(name, x=0, y=0, w=8, h=8)
draw_sprite(sprite)
clear(color=0)
text(value, x, y, color=1)
rect(x, y, w, h, color=1, fill=True)
circle(x, y, r, color=1, fill=False)
line(x1, y1, x2, y2, color=1)
button(name)
button_pressed(name)
button_released(name)
beep()
audio
files
radio
random_int(low, high)
random_x()
random_y()
colors
```

Minimum object behavior:

```text
Sprite:
  name
  x
  y
  w
  h
  visible
  frame
  flip_x
  flip_y
  touching(other)
  move_to(x, y)

Game:
  @game.update
  @game.draw
  @game.on_button(name)
```

## Lifecycle

The device runtime should follow this sequence:

```text
load project bundle or folder
parse trusted manifest metadata
validate permissions
mount project files
initialize screen/input/audio/radio services
execute entry script
register callbacks
run frame loop
on each frame:
  update input edge state
  call button handlers
  call update(dt)
  call draw()
  present 128x128 logical canvas
on Stop/Home/crash:
  stop project cleanly
  show launcher or error screen
```

The logical canvas is 128x128 pixels. A board profile decides how that canvas is
scaled, centered, or letterboxed on the real display.

## Input Semantics

Canonical buttons:

```text
up down left right
a b x y
run stop home save share
select start
```

`button(name)` returns whether the button is currently held.

`button_pressed(name)` returns true only on the first frame after a transition
from released to held.

`button_released(name)` returns true only on the first frame after a transition
from held to released.

The LilyGO T-Deck Plus keyboard and buttons should map to this abstraction in a
board profile. The project code must not depend on raw key codes.

## Permissions

Every device service must enforce manifest permissions:

```text
audio.play/beep requires audio=true
radio.send requires radio=true
files.* requires files != none
SD card access requires sd_card or advanced file mode
wifi/ai/gpio/system are denied unless explicitly enabled
```

Permission failures should produce friendly project errors, not crashes or raw
tracebacks on the child-facing screen.

## Service Boundaries

Audio:

```text
beep()
audio.play_sfx(name)
audio.play(path)
audio.pause()
audio.stop()
audio.volume(value)
```

The firmware service owns decoding, buffering, I2S/DAC output, and volume. User
code must not decode MP3 or talk to hardware directly.

Files:

```text
files.read_text(path)
files.write_text(path, value)
files.list(path)
```

Normal project access is confined to the project folder or packaged bundle.

Radio:

```text
radio.send(message)
@radio.on_message
def receive(message): ...
```

The firmware service owns pairing, ESP-NOW or other transport details,
retries, rate limits, and message framing. User code only sees small messages.

## Portable Subset

Project code and generated block code should import only:

```text
from moybyte import *
math
random
simple built-ins
```

Avoid:

```text
pygame
typer
rich
pydantic
os
subprocess
socket/network clients
threads
arbitrary pip packages
CPython introspection APIs
```

The PC checker is:

```bash
moybyte check-portable <project.moyproj>
```

## LilyGO T-Deck Plus Bring-Up Target

The first useful device demo should be smaller than the full simulator:

```text
1. boot into a Moybyte runner
2. load one bundled project, initially tiny_runner
3. map keyboard/D-pad input to Moybyte buttons
4. draw the 128x128 logical canvas on the display
5. run update/draw at a stable frame rate
6. show friendly errors over serial or on screen
7. return to a minimal launcher or reboot cleanly on stop
```

Acceptable stretch shortcuts for tonight:

```text
single bundled project
no visual editor
no block compiler on device
fake radio
beep-only audio
serial logs for friendly errors
manual flash/deploy flow
```

The board profile should be named `lilygo_t_deck_plus` once the exact firmware
stack and pin/display/keyboard details are verified.

The current local board profile lives in `docs/boards/lilygo_t_deck_plus.md` and
can be inspected with:

```bash
moybyte board-info lilygo_t_deck_plus
```
