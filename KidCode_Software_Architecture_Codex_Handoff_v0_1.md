# KidCode Software Architecture — Codex Implementation Handoff v0.1

**Project:** Kids Edu Slate / Laptop → KidCode Console pivot  
**Document:** Software architecture and implementation plan  
**Version:** 0.1  
**Date:** 2026-06-16  
**Intended reader:** Codex / engineering agent / future developer  
**Primary goal:** Build a PC-first KidCode prototype with a real edit-run-test loop, then prepare the path to ESP32 firmware.

---

## 0. Copy/paste prompt for Codex

Use this section when starting a fresh Codex session.

```text
You are implementing KidCode, a PC-first simulator and SDK for a future ESP32-based kids' creative coding console.

Read this architecture document completely before coding.

Build the repo in small, testable steps. Prioritize:
1. A working Python package named `kidcode` that exposes the child-facing API.
2. A CLI named `kidcode` that can create, validate, compile, and run projects.
3. A desktop simulator that runs `.kcproj` projects with a scaled 128x128 fantasy-console canvas.
4. Example projects and pytest coverage.
5. A simple block compiler that converts `blocks.json` into `main.py`.

Do not start with ESP32 firmware. Do not build a full Scratch clone. Do not build a full IDE. First make the PC loop work:

    make setup
    make test
    make run-example

The first milestone is complete when:
- `kidcode run examples/tiny_runner.kcproj` opens a simulator window or runs headless in CI.
- The player sprite moves from keyboard input.
- `kidcode compile examples/blocks_demo.kcproj` generates valid Python from blocks.
- `pytest` passes.
- The project format, manifest validation, permissions model, and basic runtime API are documented.
```

---

## 1. Product software thesis

KidCode is not only a fantasy console and not only a MicroPython terminal.

It is a ladder:

```text
Cards mode → Blocks mode → Text mode → Advanced mode → Developer/firmware mode
```

The same device should let a 5-year-old press cards together, an 8-year-old write simple game code, and a maker parent write a small real app such as a music player, sensor dashboard, or local radio chat.

The fantasy console is the friendly creative shell. The real programming model is a small, stable KidCode API that can run in a PC simulator first and later on ESP32/MicroPython firmware.

### Core product loop

```text
Draw → Code → Run → Play → Fix → Share → Improve
```

### Core engineering loop

```text
Codex edits code → tests run → simulator launches → examples run → failures are visible → Codex fixes code
```

The PC loop is not optional. It is the foundation that makes the rest of the project feasible.

---

## 2. Highest-level architecture

There are three related but separate systems:

```text
1. KidCode SDK / Runtime API
   The Python-facing API used by kid projects.

2. KidCode Simulator / Tooling
   PC implementation of the runtime, CLI, block compiler, tests, examples.

3. KidCode Firmware
   Future ESP32 implementation using ESP-IDF/LVGL/MicroPython/native services.
```

The early implementation should focus on 1 and 2.

```text
Phase 0: PC-first SDK + simulator + project format + examples + tests
Phase 1: Block compiler + basic IDE data model + headless tests
Phase 2: MicroPython compatibility subset + Unix MicroPython testing
Phase 3: ESP32 proof with MicroPython or ESP-IDF shell
Phase 4: LVGL UI shell + native services + runtime supervisor
```

---

## 3. Locked technical decisions

### 3.1 PC-first before firmware

Start with a PC simulator in normal Python. This is the fastest way to let Codex build and test.

**Do not begin by flashing ESP32 hardware.** Hardware loops are too slow for early architecture work.

### 3.2 Kid projects use a stable high-level API

User code should import from `kidcode`, not directly from hardware libraries:

```python
from kidcode import *

player = sprite("robot", x=60, y=60)

@game.update
def update(dt):
    if button("right"):
        player.x += 2
```

Later, on-device MicroPython exposes the same or very similar API.

### 3.3 Blocks compile to code, not to a full Scratch VM

Blocks are a UI/view over a constrained KidCode program model. They should generate Python/KidCode code.

```text
blocks.json → KidCode AST → generated main.py → runtime
```

Do not implement a full Scratch-compatible VM.

### 3.4 Fantasy-console constraints are intentional

The runtime should start tiny:

```text
Canvas:       128x128 internal pixels
Palette:      16 colors initially
Sprites:      8x8 / 16x16 logical sprites
Input:        keyboard, D-pad, ABXY abstraction
Audio:        beep/SFX stubs first, native streaming later
Networking:   fake radio in simulator, ESP-NOW later
```

### 3.5 Advanced Mode is allowed, but gated

Advanced mode unlocks more powerful APIs and app-building capability, but not by default for young kids.

Normal KidCode app:

```python
from kidcode import *
audio.play("/music/song.mp3")
```

Advanced app:

```python
import os
from kidcode.system import audio_service
```

Future firmware should enforce permissions for files, radio, internet, GPIO, AI, and system services.

### 3.6 Keep the public runtime portable

KidCode has two different execution worlds:

```text
Portable runtime world
  user main.py
  generated block code
  public kidcode API
  future MicroPython/ESP32 implementation

PC tooling world
  CLI
  simulator backends
  block compiler
  tests
  manifest/package tooling
```

The portable runtime world must not depend on PC-only libraries. Code that may later run on the device should avoid pygame, typer, rich, pydantic, pathlib-heavy logic, dataclasses, complex typing behavior, threads, subprocesses, network clients, and other CPython-only conveniences.

The public `kidcode` package should be deliberately simple: plain classes, functions, lists, dictionaries, small constants, and backend injection points. The PC simulator can inject desktop services behind that API. Later, the firmware or MicroPython port can provide the same API names with native services.

User projects and generated block code should target this portable subset:

```text
Allowed by default:
  from kidcode import *
  simple built-ins
  math
  random

Avoid by default:
  arbitrary pip packages
  direct pygame imports
  direct OS/filesystem access
  direct networking
  CPython introspection or subprocess APIs
```

This does not mean the whole repo must run on the device. It means the child-facing API and project code stay device-shaped while PC-only tooling remains free to use normal desktop dependencies.

---

## 4. Recommended repository layout

Create a monorepo.

```text
kidcode/
  README.md
  pyproject.toml
  Makefile
  LICENSE

  docs/
    architecture.md
    project_format.md
    kidcode_api.md
    block_compiler.md
    firmware_plan.md
    safety_permissions.md
    codex_tasks.md

  kidcode/
    __init__.py
    api.py
    app.py
    runtime.py
    sprites.py
    screen.py
    input.py
    audio.py
    files.py
    radio.py
    ai.py
    permissions.py
    manifest.py
    errors.py
    assets.py
    colors.py

  kidcode_sim/
    __init__.py
    main.py
    pygame_backend.py
    headless_backend.py
    fake_audio.py
    fake_radio.py
    fake_files.py
    screenshot.py

  kidcode_blocks/
    __init__.py
    schema.py
    ast.py
    compiler.py
    validators.py
    templates.py

  kidcode_cli/
    __init__.py
    main.py
    commands/
      new.py
      run.py
      validate.py
      compile.py
      pack.py
      doctor.py

  examples/
    tiny_runner.kcproj/
      manifest.json
      main.py
      assets/
    blocks_demo.kcproj/
      manifest.json
      blocks.json
      assets/
    music_player_stub.kcproj/
      manifest.json
      main.py
      assets/
    radio_pong_stub.kcproj/
      manifest.json
      main.py
      assets/

  tests/
    test_manifest.py
    test_permissions.py
    test_runtime.py
    test_sprite.py
    test_blocks_compile.py
    test_examples.py
    test_cli.py

  tools/
    render_project_screenshot.py
    check_micropython_subset.py
    export_kcproj.py

  firmware/
    README.md
    esp32/
      CMakeLists.txt
      sdkconfig.defaults
      main/
        app_main.cpp
      components/
        kid_hal/
        kid_ui/
        kid_runtime/
        kid_services/
        kid_storage/
        kid_radio/
        kid_audio/
        kid_ai_client/
    lvgl_sim/
      README.md
```

For the first Codex implementation, it is acceptable to create only:

```text
kidcode/
kidcode_sim/
kidcode_blocks/
kidcode_cli/
examples/
tests/
docs/
Makefile
pyproject.toml
```

The `firmware/` directory can contain planning stubs until the PC loop works.

---

## 5. Dependency recommendations for Phase 0

Use boring, easy Python dependencies for PC tooling and simulator code. Do not leak these dependencies into the portable `kidcode` runtime layer or generated user project code.

```toml
[project]
name = "kidcode"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = [
  "pygame>=2.5",
  "pydantic>=2.0",
  "typer>=0.12",
  "rich>=13.0"
]

[project.optional-dependencies]
dev = [
  "pytest>=8.0",
  "ruff>=0.5",
  "mypy>=1.10"
]
```

Notes:

- `pygame` gives the fastest desktop simulator path, but belongs in `kidcode_sim`.
- `pydantic` is useful for PC-side manifest and block schema validation, but the device runtime should use a tiny validator or trusted packaged metadata.
- `typer` and `rich` make the CLI pleasant, but belong in `kidcode_cli`.
- Keep dependencies minimal; this should run easily on Linux/macOS/Windows.
- Keep `kidcode/` portable enough that it can later be reimplemented or shimmed on MicroPython without rewriting kid projects.

If pygame causes install problems, keep the headless backend working so CI/tests still run.

---

## 6. Project format: `.kcproj`

Use a folder-based format first. Later it can be zipped into `.kc8`, `.kcapp`, or `.kidcart`.

```text
my_project.kcproj/
  manifest.json
  main.py
  blocks.json          optional
  assets/
    sprites/
    sounds/
    images/
    music/
  generated/
    main.generated.py  optional
```

### 6.1 `manifest.json`

Example:

```json
{
  "schema": "kidcode.project.v1",
  "id": "tiny_runner",
  "title": "Tiny Runner",
  "kind": "game",
  "age_mode": "text",
  "entry": "main.py",
  "canvas": {
    "width": 128,
    "height": 128,
    "scale": 4
  },
  "permissions": {
    "files": "project",
    "sd_card": false,
    "audio": true,
    "radio": false,
    "wifi": false,
    "ai": false,
    "gpio": false,
    "system": false
  },
  "assets": {
    "sprites": "assets/sprites",
    "sounds": "assets/sounds",
    "images": "assets/images"
  },
  "author": {
    "display_name": "KidCode"
  }
}
```

### 6.2 Required manifest fields

| Field | Required | Meaning |
|---|---:|---|
| `schema` | yes | Must be `kidcode.project.v1` for v0 |
| `id` | yes | Stable slug, lowercase/underscore |
| `title` | yes | Human-readable title |
| `kind` | yes | `game`, `app`, `demo`, `tool` |
| `age_mode` | yes | `cards`, `blocks`, `text`, `advanced` |
| `entry` | yes | Usually `main.py` |
| `canvas` | yes | Internal fantasy-console canvas |
| `permissions` | yes | Capability request |

### 6.3 Project kinds

```text
game      Uses update/draw loop, sprites, input, audio.
app       Can build UI-style tools like music player, calculator, file viewer.
demo      Example project bundled with SDK.
tool      Advanced/parent/maker utility.
```

For v0, `game` and `app` can run on the same runtime.

---

## 7. Runtime programming model

Support two equivalent styles.

### 7.1 Simple callback style

This is the preferred kid-facing style.

```python
from kidcode import *

player = sprite("robot", x=60, y=60)
coin = sprite("coin", x=30, y=30)
score = 0

@game.update
def update(dt):
    global score

    if button("left"):
        player.x -= 2
    if button("right"):
        player.x += 2

    if player.touching(coin):
        score += 1
        coin.move_to(random_x(), random_y())
        beep()

@game.draw
def draw():
    clear()
    draw_sprite(player)
    draw_sprite(coin)
    text(f"Score: {score}", 4, 4)

run()
```

### 7.2 Function naming style

This is useful for generated blocks.

```python
from kidcode import *

player = sprite("robot", x=60, y=60)

def update(dt):
    if button("right"):
        player.x += 2

def draw():
    clear()
    draw_sprite(player)

run(update=update, draw=draw)
```

The runtime should support both.

---

## 8. Runtime lifecycle

```text
load manifest
validate permissions
create RuntimeContext
mount project filesystem
load assets
execute main.py in sandbox namespace
register callbacks
start simulator/game loop
on frame:
  update input
  call update(dt)
  call draw()
  present frame
on crash:
  capture friendly traceback
  return to launcher or CLI error
```

### 8.1 Runtime states

```text
CREATED
LOADING
READY
RUNNING
PAUSED
ERROR
STOPPED
```

### 8.2 Error handling

All user code exceptions should be caught and converted into a friendly error object:

```json
{
  "type": "KidCodeRuntimeError",
  "title": "Your game crashed",
  "message": "name 'plaer' is not defined",
  "file": "main.py",
  "line": 8,
  "hint": "Did you mean 'player'?",
  "raw_traceback": "..."
}
```

In the simulator CLI, print both:

1. friendly summary
2. raw traceback in verbose mode

Later, the AI helper can use the friendly error object.

---

## 9. Core API v0

The public API should be small and stable.

### 9.1 Top-level imports

`from kidcode import *` should expose only safe kid-facing names.

Suggested exports:

```python
game
run
sprite
draw_sprite
clear
text
rect
circle
line
button
button_pressed
touch
beep
random_int
random_x
random_y
colors
```

Avoid exporting advanced/system APIs from `*`.

### 9.2 `game`

```python
@game.update
def update(dt): ...

@game.draw
def draw(): ...

@game.on_button("a")
def jump(): ...
```

Minimum implementation:

```python
class Game:
    def update(self, fn): ...
    def draw(self, fn): ...
    def on_button(self, name): ...
```

### 9.3 Sprites

```python
player = sprite("robot", x=60, y=60)
player.x += 1
player.y -= 1
player.visible = True
player.touching(other)
player.move_to(x, y)
```

Fields:

```text
name
x
y
w
h
visible
frame
flip_x
flip_y
```

For v0, sprites can be colored rectangles if no sprite asset exists. This keeps the runtime working before art tools exist.

### 9.4 Screen/drawing

```python
clear(color=0)
text("Hello", x=4, y=4)
rect(x, y, w, h, color=1, fill=True)
circle(x, y, r, color=2, fill=False)
line(x1, y1, x2, y2, color=3)
draw_sprite(player)
```

Internally these draw to a 128x128 logical framebuffer or immediate canvas abstraction.

### 9.5 Input

Use abstract buttons, not raw keyboard keys.

```python
button("left")        # held
button_pressed("a")   # edge-triggered this frame
button_released("a")
```

Canonical button names:

```text
up down left right
a b x y
run stop home save share
select start
```

PC mapping:

```text
Arrow keys → up/down/left/right
Z or J     → A
X or K     → B
A or U     → X
S or I     → Y
Enter      → Run/Start
Esc        → Stop/Home
Ctrl+S     → Save
```

### 9.6 Audio

V0 can stub audio but keep the API.

```python
beep()
audio.play_sfx("coin")
audio.play("/music/song.mp3")
audio.pause()
audio.stop()
audio.volume(50)
```

Implementation guidance:

- `beep()` can print/log or play a tiny generated tone in pygame.
- MP3 playback can be a no-op/stub in v0.
- The API is important because future firmware will implement decoding/playback natively.

### 9.7 Files

Kid-facing API should be limited to the project folder by default.

```python
files.read_text("notes.txt")
files.write_text("save.txt", "hello")
files.list("assets/sounds")
```

Permission modes:

```text
none       no file access
project    project folder only
sd_read    read SD card public folders
sd_write   write SD card public folders
advanced   broader access, parent unlocked
```

### 9.8 Radio

V0 simulator can fake radio with an in-process message bus or localhost sockets later.

```python
radio.send("jump")

@radio.on_message
def receive(message):
    print(message)
```

For local multiplayer, later define structured messages:

```python
radio.send({"type": "input", "player": 2, "buttons": ["left", "a"]})
```

### 9.9 AI helper

V0 should define schemas, not call a real LLM by default.

```python
ai.explain_error(error)
ai.suggest_fix(code, error)
ai.make_sprite(prompt, size=16)
```

In simulator, return deterministic fake responses unless a gateway URL is explicitly configured.

---

## 10. Permissions model

Permissions are central because the same system spans kid mode and advanced mode.

### 10.1 Permission object

```python
class Permissions(BaseModel):
    files: Literal["none", "project", "sd_read", "sd_write", "advanced"] = "project"
    sd_card: bool = False
    audio: bool = True
    radio: bool = False
    wifi: bool = False
    ai: bool = False
    gpio: bool | Literal["safe_port_only"] = False
    system: bool = False
```

### 10.2 Runtime checks

Every service must check permissions.

Examples:

```text
audio.play() requires audio=true
radio.send() requires radio=true
ai.* requires ai=true
files outside project require sd_card or advanced files permission
system APIs require system=true and advanced/developer mode
```

### 10.3 Friendly denied error

```text
This project tried to use radio, but radio is not enabled for this project.
Ask a parent to enable Radio in Project Settings.
```

---

## 11. Blocks architecture

Blocks should be simple JSON first. A visual editor can come later.

### 11.1 `blocks.json` example

```json
{
  "schema": "kidcode.blocks.v1",
  "variables": [
    {"name": "score", "type": "number", "initial": 0}
  ],
  "sprites": [
    {"name": "player", "asset": "robot", "x": 60, "y": 60},
    {"name": "coin", "asset": "coin", "x": 30, "y": 30}
  ],
  "scripts": [
    {
      "event": {"type": "update"},
      "body": [
        {
          "type": "if_button",
          "button": "right",
          "body": [
            {"type": "move_sprite", "sprite": "player", "dx": 2, "dy": 0}
          ]
        },
        {
          "type": "if_touching",
          "a": "player",
          "b": "coin",
          "body": [
            {"type": "change_var", "name": "score", "delta": 1},
            {"type": "beep"}
          ]
        }
      ]
    },
    {
      "event": {"type": "draw"},
      "body": [
        {"type": "clear"},
        {"type": "draw_sprite", "sprite": "player"},
        {"type": "draw_sprite", "sprite": "coin"},
        {"type": "text", "value": "Score: {score}", "x": 4, "y": 4}
      ]
    }
  ]
}
```

### 11.2 Compiler output

The compiler should generate readable Python:

```python
# Generated from KidCode Blocks. Edits may be overwritten.
from kidcode import *

score = 0
player = sprite("robot", x=60, y=60)
coin = sprite("coin", x=30, y=30)

def update(dt):
    global score
    if button("right"):
        player.x += 2
    if player.touching(coin):
        score += 1
        beep()

def draw():
    clear()
    draw_sprite(player)
    draw_sprite(coin)
    text(f"Score: {score}", 4, 4)

run(update=update, draw=draw)
```

### 11.3 Compiler implementation

Use a deliberately small AST.

```text
Block JSON
  ↓ validate schema
KidCode AST nodes
  ↓ codegen
main.generated.py
  ↓ optionally copy to main.py
runtime
```

Do not generate arbitrary Python from untrusted strings. Use whitelisted node types.

### 11.4 First block types

```text
clear
text
draw_sprite
if_button
move_sprite
set_sprite_pos
if_touching
change_var
set_var
beep
wait
send_radio
```

---

## 12. Simulator architecture

### 12.1 Simulator backends

Implement two backends:

```text
PygameBackend
  real window, keyboard input, drawing, optional sound

HeadlessBackend
  no window, deterministic tests, fake input frames, screenshot/framebuffer capture
```

Codex and CI should be able to use the headless backend without GUI.

### 12.2 Runtime loop

```python
while running:
    dt = clock.tick(fps) / 1000
    input.update()
    runtime.update(dt)
    runtime.draw()
    backend.present(framebuffer)
```

Default FPS: 30.

### 12.3 Headless test example

```python
def test_player_moves_right():
    sim = HeadlessSimulator("examples/tiny_runner.kcproj")
    x0 = sim.get_sprite("player").x
    sim.press("right")
    sim.step(frames=5)
    assert sim.get_sprite("player").x > x0
```

### 12.4 Screenshot tests

Optional but useful:

```bash
kidcode screenshot examples/tiny_runner.kcproj --frames 10 --out /tmp/tiny_runner.png
```

Use screenshots to help Codex verify UI/rendering regressions later.

---

## 13. CLI design

CLI command name: `kidcode`.

### 13.1 Commands

```bash
kidcode new my_game --kind game
kidcode validate examples/tiny_runner.kcproj
kidcode run examples/tiny_runner.kcproj
kidcode run examples/tiny_runner.kcproj --headless --frames 60
kidcode compile examples/blocks_demo.kcproj
kidcode pack examples/tiny_runner.kcproj --out tiny_runner.kc8
kidcode doctor
```

### 13.2 Makefile targets

```makefile
setup:
	python -m pip install -e .[dev]

test:
	pytest

lint:
	ruff check .

format:
	ruff format .

run-example:
	kidcode run examples/tiny_runner.kcproj

run-headless:
	kidcode run examples/tiny_runner.kcproj --headless --frames 60

compile-blocks:
	kidcode compile examples/blocks_demo.kcproj
```

---

## 14. Example projects to implement first

### 14.1 `tiny_runner.kcproj`

Purpose: prove sprites, input, text, score, collision.

Features:

```text
player rectangle/sprite
coin rectangle/sprite
arrow key movement
score counter
beep on collect
```

### 14.2 `blocks_demo.kcproj`

Purpose: prove block compiler.

Contains `blocks.json`, compiles to `generated/main.generated.py`, and runs.

### 14.3 `music_player_stub.kcproj`

Purpose: prove app mode and advanced app story.

No real MP3 decoding required. It should show:

```text
list fake songs
A = play/pause
Left/Right = previous/next
screen shows now playing
```

Code should use the future-facing API:

```python
audio.play("assets/music/song1.mp3")
audio.pause()
```

### 14.4 `radio_pong_stub.kcproj`

Purpose: prove API shape for future ESP-NOW.

For v0, it can run in fake radio mode only.

---

## 15. Testing plan

### 15.1 Unit tests

Required:

```text
manifest parsing
permission validation
sprite movement/collision
input edge detection
drawing commands do not crash
runtime lifecycle
friendly error conversion
block schema validation
block compiler output
CLI validate command
examples load successfully
```

### 15.2 Integration tests

```text
run tiny_runner headless for 60 frames
simulate right button and verify movement
compile blocks_demo and run generated code
run music_player_stub headless and verify audio calls logged
attempt radio without permission and verify friendly error
```

### 15.3 Future MicroPython compatibility tests

Later add a tool:

```bash
tools/check_micropython_subset.py examples/tiny_runner.kcproj/main.py
```

This should flag CPython-only features if we want the code to run on MicroPython.

Rules can start simple:

```text
no external imports except kidcode/math/random/time/os subset
no pathlib
no dataclasses in user code
no typing-heavy runtime use in user code
avoid f-string debug syntax
avoid advanced introspection
```

---

## 16. Firmware architecture plan

Do not implement this first, but keep the shape in mind.

### 16.1 Target firmware stack

```text
ESP-IDF
FreeRTOS
LVGL UI shell
MicroPython user runtime
KidCode native modules/services
LittleFS internal storage
FATFS SD storage
ESP-NOW radio service
I2S audio service
Wi-Fi AI gateway client
```

The ESP-IDF build system is component-oriented, so firmware should be organized as ESP-IDF components rather than one large `main` folder.

### 16.2 Firmware component layout

```text
firmware/esp32/
  main/
    app_main.cpp

  components/
    kid_hal/
      display
      buttons
      keyboard
      touch
      battery
      speaker
      sdcard

    kid_ui/
      lvgl setup
      launcher screens
      editor screens
      error screens
      parent settings

    kid_runtime/
      runtime supervisor
      MicroPython integration
      project loading
      crash handling

    kid_services/
      event bus
      permissions
      logging
      time

    kid_storage/
      LittleFS/FATFS wrappers
      project bundles
      manifest validation

    kid_radio/
      ESP-NOW pairing
      chunked transfer
      multiplayer messages

    kid_audio/
      beep/sfx
      I2S output
      future MP3/WAV service

    kid_ai_client/
      HTTP/WebSocket gateway client
      JSON schema request/response
```

### 16.3 Firmware task model

```text
UI task
  LVGL tick/render/input

Runtime task
  runs one project at a time
  owns MicroPython VM or script execution

Input task
  keyboard/button/touch scanning
  posts events

Audio task
  buffered playback/beeps

Radio task
  ESP-NOW send/receive, pairing, chunking

Storage task or service
  project load/save/export/import

AI client task
  network requests to gateway, never direct child open chat
```

### 16.4 Runtime isolation on device

Each project should run cleanly.

Target behavior:

```text
select project
save selected project path
stop current runtime
reset/reinitialize MicroPython VM or reboot into project runner if needed
run project
on Stop/Home/crash, return to launcher
```

This avoids cross-project state leaks.

### 16.5 Native service boundary

User code should not decode MP3 or manage ESP-NOW directly.

User code:

```python
audio.play("/sd/music/song.mp3")
radio.send({"type": "jump"})
```

Native service:

```text
audio task streams/decodes/buffers
radio task handles peer list, retries, ACKs, chunking
permissions are checked before service calls
```

---

## 17. AI gateway architecture

The ESP32 device should not call LLM providers directly.

```text
KidCode device / simulator
  → KidCode Gateway
  → cloud LLM or parent local OpenAI-compatible endpoint
  → schema validation
  → KidCode device / simulator
```

### 17.1 V0 simulator AI

Implement fake deterministic responses first:

```python
ai.explain_error(error) → "It looks like a variable name may be misspelled."
ai.suggest_fix(code, error) → structured suggestion object
ai.make_sprite(prompt) → placeholder pixel art JSON
```

### 17.2 Gateway request shape

```json
{
  "schema": "kidcode.ai.request.v1",
  "tool": "explain_error",
  "age_mode": "text",
  "project_id": "tiny_runner",
  "payload": {
    "error": {
      "message": "name 'plaer' is not defined",
      "line": 8
    },
    "code_excerpt": "..."
  }
}
```

### 17.3 Gateway response shape

```json
{
  "schema": "kidcode.ai.response.v1",
  "tool": "explain_error",
  "safe": true,
  "message": "The name 'plaer' looks like a typo. Did you mean 'player'?",
  "actions": [
    {
      "type": "replace_text",
      "file": "main.py",
      "line": 8,
      "from": "plaer",
      "to": "player"
    }
  ]
}
```

Do not expose open-ended child chat in the device UI.

---

## 18. KidCode modes

### 18.1 Cards mode

For 5+.

```text
WHEN button A
DO jump
```

Implementation in v0 can be data-only. No visual editor required yet.

### 18.2 Blocks mode

Scratch-like, but constrained.

Implementation v0: JSON blocks + compiler. Visual editor later.

### 18.3 Text mode

Kid-facing Python/KidCode API.

### 18.4 Advanced mode

Unlocks selected modules:

```python
import os
from kidcode.system import audio_service
from kidcode.hardware import i2c
```

Not in v0 unless stubs are easy.

### 18.5 Developer mode

Firmware flashing, serial logs, low-level diagnostics. Do not expose in PC v0 except as docs/stubs.

---

## 19. Security and safety boundaries

Even in the simulator, design as if this will become a child device.

### 19.1 User code sandbox in PC v0

Python sandboxing is hard. For local development v0, do not promise real security. Instead:

```text
Use validation and permissions for API calls.
Warn that PC v0 executes local Python code.
Future device runtime will restrict imports/capabilities.
```

### 19.2 Device security principles

```text
No child account required for local use.
No open browser.
No unrestricted internet from child projects.
No raw access to Wi-Fi credentials.
No arbitrary firmware flashing from kid mode.
No unlimited radio spam.
No direct cloud AI keys on device.
```

---

## 20. Concrete Codex milestone plan

### Milestone 1 — Repo bootstrap

Deliverables:

```text
pyproject.toml
Makefile
README.md
kidcode package
kidcode_cli package
tests run
```

Acceptance:

```bash
make setup
make test
kidcode doctor
```

### Milestone 2 — Manifest + project loader

Deliverables:

```text
manifest model
permissions model
project loader
validate CLI
example tiny_runner manifest
```

Acceptance:

```bash
kidcode validate examples/tiny_runner.kcproj
pytest tests/test_manifest.py
```

### Milestone 3 — Runtime + headless backend

Deliverables:

```text
RuntimeContext
Game callbacks
Sprite class
Input state
HeadlessBackend
```

Acceptance:

```bash
kidcode run examples/tiny_runner.kcproj --headless --frames 60
pytest tests/test_runtime.py
```

### Milestone 4 — Pygame simulator

Deliverables:

```text
Pygame window
scaled 128x128 canvas
keyboard mapping
basic drawing
```

Acceptance:

```bash
kidcode run examples/tiny_runner.kcproj
```

Manual test: arrow keys move the player.

### Milestone 5 — Block compiler

Deliverables:

```text
blocks schema
compiler
blocks_demo project
generated/main.generated.py
```

Acceptance:

```bash
kidcode compile examples/blocks_demo.kcproj
kidcode run examples/blocks_demo.kcproj --entry generated/main.generated.py --headless --frames 60
pytest tests/test_blocks_compile.py
```

### Milestone 6 — App examples

Deliverables:

```text
music_player_stub.kcproj
radio_pong_stub.kcproj
fake audio service
fake radio service
permission errors
```

Acceptance:

```bash
kidcode run examples/music_player_stub.kcproj --headless --frames 30
pytest tests/test_permissions.py
```

### Milestone 7 — Documentation and first release tag

Deliverables:

```text
docs/project_format.md
docs/kidcode_api.md
docs/block_compiler.md
docs/codex_tasks.md
```

Acceptance:

```bash
make test
make run-headless
```

---

## 21. Definition of Done for v0.1

v0.1 is done when:

```text
A developer can clone the repo and run `make setup && make test`.
A developer can run `kidcode run examples/tiny_runner.kcproj`.
A headless run works without a display.
A simple blocks project compiles into readable Python.
The project manifest and permissions are validated.
At least three examples exist: tiny_runner, blocks_demo, music_player_stub.
The README explains the concept and quickstart.
The code is organized so firmware can later reuse concepts and API names.
```

---

## 22. Things not to implement yet

Do not implement these in v0.1:

```text
full visual block editor
full on-device IDE
ESP32 flashing pipeline
real MicroPython embedding
real MP3 decoder
real ESP-NOW multiplayer
real AI gateway
online accounts
cloud project sharing
asset marketplace
parent mobile app
firmware OTA
PICO-8 compatibility
full Scratch VM
```

Stubs/interfaces are okay. Full implementation is not.

---

## 23. Future firmware notes and source references

These are relevant to later implementation:

- ESP-IDF uses a CMake/component build system; firmware should be organized into components, not a monolith.
- LVGL supports PC simulation using SDL, which is useful for testing the future on-device UI shell on a computer.
- MicroPython has a Unix port that is useful for testing MicroPython code without deploying to a device.
- ESP-IDF has Unity-based unit testing and pytest-based target testing, useful once hardware enters the loop.

For v0.1, use the PC Python simulator first. The firmware layer should not block application/runtime progress.

---

## 24. Suggested first issue list

Create these as GitHub issues or Codex tasks.

1. Bootstrap Python package, CLI, Makefile, tests.
2. Implement `manifest.py` and `permissions.py`.
3. Implement `Project.load(path)`.
4. Implement `RuntimeContext` and callback registration.
5. Implement `Sprite` and collision.
6. Implement `HeadlessBackend`.
7. Implement `kidcode run --headless`.
8. Implement `PygameBackend`.
9. Create `tiny_runner.kcproj`.
10. Implement block schema and compiler.
11. Create `blocks_demo.kcproj`.
12. Implement fake audio service and `music_player_stub.kcproj`.
13. Implement fake radio service and `radio_pong_stub.kcproj`.
14. Add friendly error conversion.
15. Write docs for project format and KidCode API.

---

## 25. Final architecture summary

The correct implementation path is:

```text
Build KidCode as a PC-testable creative coding runtime first.
Make the child-facing API stable.
Make blocks compile to that API.
Make examples prove games and small apps.
Only then port the runtime concepts to ESP32 firmware.
```

This protects the project from the classic embedded trap: spending weeks on drivers and flashing before the actual product loop is fun.

The first real demo is not firmware.

The first real demo is:

```bash
kidcode run examples/tiny_runner.kcproj
```

And the screen shows a tiny game the child can edit, run, break, fix, and eventually build into something bigger.
