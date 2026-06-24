# KidCode Console - MicroPython-First Design Doc v0.1

**Project:** KidCode Console / KidCode OS  
**Document version:** 0.1  
**Date:** 2026-06-18  
**Status:** Parallel architecture branch to `KidCode_Console_Design_Doc_v0_3.md`  
**Purpose:** Explore the product and firmware direction where KidCode is built around MicroPython from day one, instead of using Lua as the primary device runtime.

---

## 0. Executive conclusion

This document describes a more Python-forward version of KidCode:

> KidCode Console is a MicroPython-first creative coding console where the shell, apps, editor, and child projects are all built around Python-style code, with native modules used only where performance, hardware access, or safety require them.

This direction exists because the parent-facing value of "real Python" is high. It may also simplify the story for schools, workshops, and makers: projects are Python files, lessons teach Python syntax, and the advanced mode can expose a real MicroPython REPL.

The practical architecture is:

```text
ESP-IDF + FreeRTOS base
  |
  MicroPython firmware with LVGL bindings
  |
  KidCode OS shell written mostly in MicroPython
  |
  KidCode framework modules
  |
  KidCode Studio apps
  |
  Child projects loaded as controlled MicroPython modules
```

This is not "MicroPython running MicroPython inside MicroPython." There is one MicroPython VM. The shell loads project files as modules or executes them in a controlled namespace, then calls their lifecycle hooks.

The central bet:

> MicroPython may be good enough if KidCode keeps graphics, input polling, audio, storage safety, and any future 3D helpers behind native-backed APIs, while child code stays at the game/app logic level.

The central risk:

> The product may feel slower or less controlled than a native shell plus Lua runtime, especially if child projects can block the UI loop, allocate too much memory, or access raw hardware APIs.

This direction deserves a spike before committing the project to Lua-first.

---

## 1. Relationship to the v0.3 design

The v0.3 design recommends:

```text
ESP-IDF + FreeRTOS
KidCode Kernel / services layer
LVGL shell
KidCode Studio
Lua-style or MicroPython-style runtime
```

This MicroPython-first branch changes the ownership model:

```text
ESP-IDF + FreeRTOS
MicroPython runtime
LVGL bindings
MicroPython KidCode shell
MicroPython KidCode apps
MicroPython child projects
native C modules where needed
```

The product pillars stay mostly the same:

- make, do not consume
- keyboard-first creative computer
- instant Run loop
- local-first and private
- safe sharing and modules
- optional AI helper later

The technical priority changes:

- Python story becomes first-class.
- Lua is removed from the first architecture.
- Native C/C++ becomes an acceleration and safety layer, not the primary app framework.
- Reset/restart becomes an accepted recovery mechanism for bad child code.

---

## 2. Why consider MicroPython-first

### 2.1 Parent and school value

Parents understand Python. Schools understand Python. "Your child can grow into real Python" is easier to explain than "your child can grow into Lua."

The product can say, honestly:

- projects are Python files
- advanced mode has a Python REPL
- lessons teach Python syntax gradually
- APIs are designed for kids, but the language is real MicroPython

### 2.2 Simpler mental model

With MicroPython-first, the stack can be described as:

```text
KidCode OS is written in Python.
Kid projects are written in Python.
The fast hardware pieces are native modules.
```

That is easier for contributors, parents, and older children to understand.

### 2.3 Existing ecosystem proof

Projects like MicroPythonOS show that an app-centric UI system, LVGL bindings, activities, app manifests, and installable MicroPython apps are plausible on ESP32-class hardware.

KidCode does not need to copy MicroPythonOS or port it directly to the LilyGO T-Deck Plus, but it can learn from the pattern:

- Python apps
- LVGL UI from Python
- app manifests
- activity lifecycle
- app launcher
- services and settings
- desktop simulator support

---

## 3. Non-goals

MicroPython-first does not mean:

- building a general Android clone
- running CPython
- supporting arbitrary Python packages
- letting child projects access raw hardware by default
- letting every app use network, files, pins, camera, or radio freely
- putting performance-critical rendering in Python loops
- guaranteeing that every Python mistake can be recovered without reset

KidCode still needs to be a controlled creative console, not a generic MicroPython handheld.

---

## 4. Proposed stack

### 4.1 Firmware base

```text
ESP-IDF / FreeRTOS
  - display driver
  - touch / keyboard / trackball drivers
  - SD and flash storage
  - Wi-Fi / ESP-NOW
  - audio output
  - watchdogs
  - partitioning and update path
```

MicroPython runs on top of this base.

### 4.2 MicroPython runtime

The firmware includes:

- MicroPython VM
- filesystem support
- selected standard modules
- selected hardware modules
- LVGL bindings
- frozen KidCode framework modules
- optional native C modules for hot paths

The build should remove or gate unsafe modules in child mode where possible.

### 4.3 LVGL from MicroPython

The shell uses LVGL through MicroPython bindings:

```python
import lvgl as lv
```

LVGL owns:

- launcher
- project gallery
- editor screens
- settings
- dialogs
- parent mode
- theme panels

Child projects should not manipulate arbitrary LVGL widgets in beginner mode. They draw to a KidCode canvas API.

### 4.4 KidCode framework

KidCode provides frozen/default modules:

```text
kidcode
kidcode.canvas
kidcode.input
kidcode.audio
kidcode.storage
kidcode.radio
kidcode.project
kidcode.permissions
kidcode.errors
```

The child-facing API remains small:

```python
from kidcode import *

player = sprite("robot", x=60, y=60)

def update(dt):
    if button("right"):
        player.x += 2

def draw():
    clear()
    draw_sprite(player)
```

### 4.5 Native acceleration modules

Native C/C++ modules should provide:

- fast framebuffer/canvas operations
- palette conversion
- sprite blits
- display flush
- keyboard/trackball polling
- audio beep/SFX backend
- safe path and manifest validation if needed
- ESP-NOW framing if Python overhead becomes too high
- future raycaster or tiny 3D helpers

Python should orchestrate. Native code should push pixels and handle tight loops.

---

## 5. How project execution works

There is one MicroPython VM. The KidCode shell is running inside it. Project execution is a controlled load-and-call operation.

### 5.1 Project package

Suggested project folder:

```text
/projects/tiny_runner.kcart/
  manifest.json
  main.py
  sprites.ksp
  sounds/
  data/
  preview.raw
```

Manifest example:

```json
{
  "format": "kidcode-project-v1",
  "title": "Tiny Runner",
  "runtime": "kidcode-micropython",
  "entry": "main.py",
  "canvas": {"width": 128, "height": 128},
  "permissions": ["graphics", "input", "sound"]
}
```

### 5.2 Loader model

The first implementation can use a simple loader:

```python
source = open(project_main_path).read()
env = kidcode_project_environment(project_manifest)
exec(source, env)
```

Then the runner looks for known hooks:

```python
setup = env.get("setup")
update = env.get("update")
draw = env.get("draw")
```

The frame loop calls:

```text
poll input
call update(dt)
call draw()
present canvas
handle stop/home/crash
```

This is easy to prototype and easy to explain.

### 5.3 Later loader options

If the simple `exec` loader is too loose or too slow, options include:

- load project as a named module from a project path
- compile projects to `.mpy` bytecode on a PC or gateway
- freeze lesson projects into firmware
- use an app-style Activity class for advanced projects

### 5.4 Reset and cleanup

Because there is no real process isolation, the runner must assume project code can leave state behind.

After Stop or crash:

- clear the KidCode canvas
- clear input state
- stop audio
- close project files
- remove project modules from `sys.modules` if using imports
- drop project globals if using `exec`
- run garbage collection
- return to launcher

If a project hard-locks the VM:

- Stop/Home should trigger a watchdog path where possible
- a soft reset is acceptable
- a hard reset is acceptable if it returns to the shell quickly

Fast reset is part of the safety model in this branch.

---

## 6. Safety model

MicroPython-first cannot provide true process isolation on ESP32. The safety model is cooperative plus reset-based.

### 6.1 Beginner project sandbox

Beginner projects should see only:

```python
from kidcode import *
```

They should not see by default:

```python
import machine
import network
import os
import socket
import _thread
```

Possible enforcement levels:

1. Soft enforcement: editor templates and lessons only expose `kidcode`.
2. Import filtering: override or wrap `__import__` in the project environment.
3. Firmware build gating: omit or hide dangerous modules in child mode.
4. Parent/advanced mode: expose raw MicroPython deliberately.

The first spike can use soft enforcement. Product firmware needs stronger controls.

### 6.2 Permissions

Project manifest permissions gate services:

```text
graphics
input
sound
project_storage
radio
wifi
ai
hardware_modules
raw_hardware
```

The `kidcode` API checks permissions before performing service actions.

### 6.3 Watchdog and recovery

Problem cases:

- `while True: pass`
- huge list allocations
- recursive functions until memory exhaustion
- blocking file reads
- blocking network calls
- accidental import of heavy modules
- direct LVGL misuse in advanced mode

Recovery tools:

- visible Stop/Home key
- periodic yield points in KidCode APIs
- MicroPython scheduler or async loop where practical
- watchdog reset
- low-memory friendly error screen after restart
- last-crash marker stored in flash

The product promise should be:

> Bad code should not brick the device. At worst, the console resets and returns to the project with a friendly error or recovery prompt.

Not:

> Every bad Python program can be interrupted perfectly.

---

## 7. Performance model

MicroPython can be acceptable if Python does game logic, not pixel pushing.

### 7.1 Good Python work

Good:

- moving sprites
- changing scores
- collision checks for small sprite counts
- simple app logic
- menus and forms
- text editing logic
- lesson scripting
- sensor reads
- simple radio messages

### 7.2 Native-backed work

Keep these native-backed:

- clearing framebuffer
- drawing sprites
- scaling 128x128 canvas to display
- text rasterization if Python is too slow
- audio output
- display flush
- 3D/raycasting helpers
- compression and bundle transfer if needed

### 7.3 Canvas strategy

Kid projects draw to a logical canvas:

```text
128x128, 16 colors initially
```

The implementation can store this as:

- indexed 4-bit or 8-bit buffer for memory efficiency
- RGB565 buffer for simpler display flushing
- native object exposed through Python methods

The shell presents the canvas through LVGL or a direct display flush path. The exact presentation path should be chosen by measurement.

### 7.4 Display strategy

Avoid full-screen redraws for game frames.

Targets:

- update only the canvas area when a project is running
- avoid redrawing the whole LVGL shell every frame
- use dirty rectangles if useful
- keep serial logging low in normal builds

---

## 8. App and shell model

### 8.1 Shell apps

Shell apps are MicroPython classes using LVGL:

- Launcher
- Project Gallery
- Game Lab
- Draw Lab
- Code Editor
- Settings
- Parent Mode
- Diagnostics

This can follow an Activity-like model:

```python
class Activity:
    def on_create(self):
        pass

    def on_resume(self):
        pass

    def on_pause(self):
        pass

    def on_destroy(self):
        pass
```

### 8.2 Project runner app

The Project Runner is a shell app that:

- loads manifest
- creates project environment
- starts frame loop
- maps input into KidCode buttons
- owns canvas presentation
- catches project exceptions
- returns to launcher on Stop/Home

### 8.3 Editor app

The first editor should be modest:

- project settings
- sprite color/name/speed fields
- line-based code editor later
- Run/Stop loop
- friendly syntax/runtime errors

Full IDE behavior is not a v0 target.

---

## 9. Hardware direction

### 9.1 LilyGO T-Deck Plus role

The T-Deck Plus remains a software mule.

It should prove:

- MicroPython firmware boots from SD/launcher or flash
- display works
- keyboard works
- trackball or navigation input works
- SD/flash project storage works
- LVGL shell is usable
- Tiny Runner feels responsive
- Stop/Home/reset recovery is acceptable

Do not optimize product UI around the T-Deck screen. Use it to validate the architecture.

### 9.2 Future 7-inch hardware

The final product direction still wants:

- larger display
- real keyboard
- D-pad/ABXY
- Run/Save/Home/Share
- speaker
- battery
- safe module connector

MicroPython-first does not remove the need for careful hardware design.

---

## 10. AI, sharing, and modules

These stay later-stage features.

### 10.1 AI helper

AI should remain gateway-based:

```text
KidCode device
  -> parent/local/cloud gateway
  -> LLM
  -> schema-checked response
  -> KidCode device
```

MicroPython should not hold API keys directly in child mode.

### 10.2 Sharing

ESP-NOW sharing is still attractive:

- share sprite
- share project
- share high score
- two-player templates

The first implementation can use Python if message rates are low. Move framing/retry/chunking native if needed.

### 10.3 Hardware modules

Beginner code should use safe APIs:

```python
temperature = module_read("temperature")
led_set(0, "red")
```

Raw `machine.Pin` belongs in parent/advanced mode.

---

## 11. Comparison with Lua-first

### 11.1 MicroPython-first advantages

- strongest parent-facing story
- projects are Python from day one
- easier school/workshop positioning
- simpler contributor mental model
- existing MicroPython and LVGL binding ecosystem
- one language across shell, apps, lessons, and projects

### 11.2 MicroPython-first risks

- less host control than Lua
- higher memory pressure
- garbage collection pauses
- harder sandboxing
- child code can block the system
- raw hardware APIs are tempting
- full game engine and 3D features may need more native code

### 11.3 Lua-first advantages

- smaller embedded runtime
- easier native host control
- strong game scripting fit
- better for tight runtime loops
- cleaner separation between shell and project runtime

### 11.4 Lua-first risks

- weaker parent-facing story
- needs more custom OS/framework work
- Python path becomes secondary or experimental
- harder to market as real Python learning

---

## 12. Decision spike

Before choosing MicroPython-first, build a narrow proof.

### 12.1 Spike goal

Answer:

> Can a MicroPython/LVGL KidCode shell on T-Deck Plus run Tiny Runner with acceptable input feel, frame rate, restart behavior, and recovery from bad code?

### 12.2 Required demo

Must show:

1. boots to simple KidCode launcher
2. keyboard/trackball diagnostic screen
3. Tiny Runner project loaded from file
4. `from kidcode import *` project API
5. 128x128 canvas rendering
6. Run/Stop loop
7. friendly Python syntax error
8. recovery from `while True: pass` by reset or watchdog
9. memory report before and after repeated Run/Stop

### 12.3 Metrics

Record:

- boot time to launcher
- time from Run press to first frame
- approximate FPS
- free heap after boot
- free heap after Tiny Runner load
- free heap after 20 Run/Stop cycles
- input latency by feel and logs
- reset recovery time
- binary size
- filesystem layout size

### 12.4 Pass criteria

Good enough for v0 if:

- launcher feels responsive
- Tiny Runner feels playable
- input mapping is obvious
- Run/Stop cycle does not leak badly
- syntax/runtime errors return to shell
- infinite loop recovers by reset without manual reflashing
- the implementation looks maintainable

Fail if:

- T-Deck port consumes most of the effort
- LVGL shell is sluggish before projects run
- project execution routinely corrupts shell state
- memory is too tight for editor plus project runner
- Stop/Home cannot recover reliably enough

---

## 13. Implementation plan for this branch

### Task group A - MicroPython firmware orientation

- identify MicroPython/LVGL firmware base
- identify T-Deck Plus display/input/SD support gaps
- document partition and boot strategy
- build or obtain a minimal firmware image

### Task group B - KidCode module

- create `kidcode` MicroPython API skeleton
- implement button state
- implement canvas commands
- implement `sprite`
- implement `clear`, `rect`, `text`, `draw_sprite`
- add fake/simulator version for PC tests if practical

### Task group C - Launcher and runner

- boot to launcher
- list one project
- open project
- load `main.py`
- call `setup/update/draw`
- show friendly errors
- return to launcher

### Task group D - Device input

- create raw input diagnostic
- map keyboard/trackball/buttons into canonical names
- separate text input from game buttons
- define Stop/Home recovery behavior

### Task group E - Performance path

- measure pure Python canvas path
- add native-backed framebuffer if needed
- remove full-screen redraws from game loop
- tune logging and frame pacing

### Task group F - Safety path

- restricted import experiment
- permissions checks
- memory cleanup after Stop
- watchdog reset experiment
- last-crash marker and friendly recovery screen

---

## 14. Product positioning for this branch

Possible claim:

> KidCode starts with kid-friendly projects and grows into real MicroPython.

Avoid claiming:

> Full desktop Python.

Better wording:

- Python-powered creative console
- real MicroPython projects
- kid-safe Python APIs
- built-in Python Lab and game maker
- native-speed graphics behind simple Python commands

---

## 15. Open questions

1. Is MicroPythonOS worth borrowing from, or should KidCode use only its architectural ideas?
2. Can the T-Deck Plus run a MicroPython/LVGL firmware with usable display and keyboard support quickly?
3. Should beginner projects use `exec` in a controlled namespace or import project modules?
4. Can dangerous modules be hidden strongly enough for child mode?
5. Is reset-based recovery acceptable for the child experience?
6. Should the core canvas be a MicroPython object backed by a native framebuffer?
7. How much of the shell can be written in Python before memory pressure becomes painful?
8. Does a Python-first path leave enough room for future 3D/raycasting through native helpers?

---

## 16. Current recommendation

Do not discard the Lua/native architecture yet.

Do run a MicroPython-first spike now, because it may be product-strategically better if it is technically good enough.

The decision should be evidence-based:

```text
If MicroPython-first feels responsive and recoverable on T-Deck:
  make it the primary architecture.

If MicroPython-first is sluggish or hard to control:
  return to native shell + Lua runtime,
  and keep MicroPython as Python Lab.
```

The next concrete step is not a full OS. It is a Tiny Runner spike with a frozen/default `kidcode` module and measured recovery behavior.
