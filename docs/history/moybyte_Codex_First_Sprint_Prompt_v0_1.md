# Moybyte — First Codex Sprint Prompt v0.1

Paste this into Codex from the root of a new repo.

```text
You are implementing Moybyte v0.1.

Moybyte is a PC-first simulator and SDK for a future ESP32 kids' creative coding console. Do not start with firmware. Build the desktop development loop first.

Primary goal:
Create a Python package and CLI that can run simple `.moyproj` projects in a headless simulator and, if pygame is installed, in a desktop window.

Architecture decisions:
- User projects import from `moybyte`.
- Project folders use `.moyproj` format.
- Blocks compile to readable Python; do not implement a Scratch VM.
- Runtime canvas is 128x128 logical pixels.
- Simulator must have both headless and pygame backends.
- All features must be testable with `pytest`.
- The public `moybyte` API and generated/user project code must stay portable to a future MicroPython/ESP32 runtime.
- Do not put PC-only dependencies such as pygame, typer, rich, or pydantic inside the portable `moybyte` runtime layer. Keep them in simulator, CLI, compiler, or test code only.
- User projects and generated block code should import only from `moybyte` plus a small approved Python subset such as `math`, `random`, and simple built-ins.

Create this repo structure:

moybyte/
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
  permissions.py
  manifest.py
  errors.py

moybyte_sim/
  __init__.py
  main.py
  headless_backend.py
  pygame_backend.py
  fake_audio.py
  fake_radio.py

moybyte_blocks/
  __init__.py
  schema.py
  compiler.py

moybyte_cli/
  __init__.py
  main.py

examples/
  tiny_runner.moyproj/
    manifest.json
    main.py
  blocks_demo.moyproj/
    manifest.json
    blocks.json
  music_player_stub.moyproj/
    manifest.json
    main.py

tests/
  test_manifest.py
  test_permissions.py
  test_runtime.py
  test_sprite.py
  test_blocks_compile.py
  test_cli.py

Also create:
- pyproject.toml
- Makefile
- README.md
- docs/project_format.md
- docs/moybyte_api.md

CLI commands:
- moybyte doctor
- moybyte validate <project>
- moybyte run <project> [--headless] [--frames N]
- moybyte compile <project>

Minimum Moybyte API:
- run(update=None, draw=None)
- game.update decorator
- game.draw decorator
- sprite(name, x=0, y=0, w=8, h=8)
- draw_sprite(sprite)
- clear(color=0)
- text(value, x, y)
- rect(x, y, w, h, color=1, fill=True)
- button(name)
- button_pressed(name)
- beep()

Input names:
up, down, left, right, a, b, x, y, run, stop, home, save, share.

Project manifest example:
{
  "schema": "moybyte.project.v1",
  "id": "tiny_runner",
  "title": "Tiny Runner",
  "kind": "game",
  "age_mode": "text",
  "entry": "main.py",
  "canvas": {"width": 128, "height": 128, "scale": 4},
  "permissions": {
    "files": "project",
    "sd_card": false,
    "audio": true,
    "radio": false,
    "wifi": false,
    "ai": false,
    "gpio": false,
    "system": false
  }
}

First acceptance tests:
1. `make setup` installs editable package with dev deps.
2. `make test` passes.
3. `moybyte validate examples/tiny_runner.moyproj` succeeds.
4. `moybyte run examples/tiny_runner.moyproj --headless --frames 60` succeeds.
5. A headless test can press `right` for several frames and assert the player sprite moved.
6. `moybyte compile examples/blocks_demo.moyproj` generates `generated/main.generated.py`.
7. `moybyte run examples/blocks_demo.moyproj --entry generated/main.generated.py --headless --frames 60` succeeds.
8. `moybyte run examples/music_player_stub.moyproj --headless --frames 30` succeeds and logs fake audio calls.

Do not implement:
- ESP32 firmware
- full visual block editor
- real MP3 decoding
- real ESP-NOW
- real AI gateway
- cloud accounts
- full Scratch compatibility

Keep code simple, typed where useful, and heavily tested.
```
