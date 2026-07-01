# Moybyte Console / Spryte OS — Plan v0.4

**Project:** Kids Edu Slate / Laptop → Moybyte Console / Spryte OS / Moybyte Fantasy Workstation  
**Document version:** 0.4  
**Date:** 2026-06-21  
**Status:** Post-v0.3 consolidation after Picotron, MicroPythonOS, Lua RTOS, PocketDeck, PicoPico/Pico Pal, and “creative computer, not game console” discussions.  
**Purpose:** Replace the v0.3 “ESP32 creative coding console” plan with a clearer v0.4 direction: a kid-safe fantasy workstation where the visible computer is made of editable cartridges.

---

## 0. Executive conclusion

v0.3 was good, but it was still too close to:

> “An ESP32 game-making console with an IDE.”

v0.4 should reframe the product as:

> **A kid-safe fantasy workstation: a physical creative computer where the desktop, wallpapers, widgets, tools, lessons, apps, games, and projects are all cartridges that can eventually be opened, edited, remixed, and shared.**

The key product shift is:

```text
v0.3:
  Draw → Code → Run → Play → Share
  First demo: Tiny Runner

v0.4:
  Make the computer yours → Edit the living desktop → Add a pet/widget → Turn it into a game/app → Share
  First demo: Living Desktop
```

Games remain important, but they are no longer the center of the product identity. The center is:

> **“The computer itself is programmable.”**

Picotron is the clearest north-star reference. It proves the emotional and architectural shape: fantasy workstation, desktop, cartridges, Lua userland, built-in tools, wallpapers, widgets, and apps/games as the same kind of object. We should not depend on running Picotron itself; we should copy the product grammar.

---

## 0.5 Implementation status — June 2026 (what's actually built)

This document is the **direction**; this section is where the build actually is.
The console now runs end-to-end on both the **host simulator** and the **LilyGO
T-Deck Plus** (MicroPython) — and, importantly, from the **same code**.

**Built**
- **One shared console** — `runtime/console.py` + `runtime/editors.py` +
  `runtime/moy_carts.py`, staged into the firmware at build so the device freezes
  the identical modules. The host sim is now a **faithful emulator** of the device:
  same 320×240 surface, same petme128 8×8 font, **mouse = touchscreen**, **arrows =
  trackball**.
- **Everything-is-a-cartridge `.moy` model** (manifest + `main.py` + `config.json`
  + `sprites.moygfx`); seeded system carts (Space / Ocean / Star Catcher), with
  create / duplicate / delete and on-device SD live read+write.
- **On-device code editor** — full-screen: caret nav, vertical+horizontal scroll
  (1-cell scrolloff), drag-to-scroll, tap-to-place, RUN/SAVE/CLOSE icons, and a
  tappable symbol palette for `= ( ) [ ] { } < > %` (the keyboard has no `=`). *(issue #3 ✅)*
- **On-device sprite/paint editor** + `sprites.moygfx` storage (PICO-8 `__gfx__`-style). *(issue #4 ✅)*
- **Native RGB565 DMA compositor** (`moy_gfx` / `moy_compositor`; see firmware
  `NATIVE_CORE_PLAN.md` / `STAGE3_PLAN.md`); trackball + GT911 touch + keyboard
  (clean 1-byte ASCII — see `CLAUDE.md`).

**Divergences from this plan worth noting**
- **Runtime (§1.4, §6–7, issue #6):** the plan called for **Lua-first** with
  MicroPython as the lab option. In practice the console is **MicroPython**, and we
  adopted **TIC-80-style drawing-API conventions in-place** (`rect`/`circ` filled,
  `rectb`/`circb` outlines, `pix`, `print`, sheet-indexed `spr`) rather than a Lua
  VM (issue #11). A Lua VM / full TIC-80 runtime (`TIC()` loop, 240×136 framebuffer,
  `sfx`/`music`, `.tic` import) remains open (#11).
- **Surface (§1.6):** the live target is the T-Deck's **320×240**, not 480×270.
- **Host:** the host reference is now the shared console (a device emulator),
  superseding the older `runtime/shell.py` / `workstation.py` UI (retirement: #17).

**Open / next (GitHub issues):** #11 (full TIC-80 runtime), #14 (on-device error
reporting + safe save — a bad cart can currently hang the launcher), #17 (retire
legacy host UI), #18 (cross-cart sprite reuse); plus the earlier #1/#2/#5/#7/#8/#13.

---

## 1. What changed from v0.3

### 1.1 From “creative coding console” to “fantasy workstation”

v0.3 positioned the product as a keyboard-first ESP32 game-making console. That was useful because it avoided becoming a cheap Android tablet or Chromebook.

v0.4 keeps the ESP32 appliance direction, but changes the metaphor:

```text
Not:
  Game console with coding tools

Instead:
  Tiny programmable workstation where games are one cartridge type
```

This better matches the user constraint:

> **We are making a creative computer, not a game console.**

### 1.2 From “Tiny Runner first” to “Living Desktop first”

v0.3 MVP centered on Tiny Runner.

v0.4 MVP should center on:

> **Make your desktop alive.**

Tiny Runner becomes project #4 or #5, after the child has already customized the computer itself.

Reason:

```text
Tiny Runner teaches: “I made a game.”
Living Desktop teaches: “This computer is mine, and code changes it.”
```

For a 5+ child, changing the whole computer is more magical and simpler than understanding platformer physics/collision.

### 1.3 From “themes as UI skin” to “desktop as editable cartridge”

v0.3 had themes: Simple Launcher, Classic Desktop, Fantasy Console, Advanced Hacker Mode.

v0.4 keeps those, but adds a stronger principle:

> The desktop itself should eventually be a protected system cartridge.

The child can duplicate it, remix it, and make their own version without breaking the real system.

### 1.4 From “one scripting runtime TBD” to “Lua-first fantasy runtime, MicroPython as lab/advanced option”

v0.3 left Lua vs MicroPython open.

v0.4 decision:

```text
Primary fantasy workstation runtime:
  Lua

Advanced/hardware/education runtime:
  MicroPython optional

Native implementation:
  ESP-IDF / FreeRTOS / C/C++
```

Why Lua first:

- smaller embedded scripting VM
- cleaner cartridge lifecycle
- easier sandboxing
- better PICO-8/Picotron culture fit
- better for “the visible OS is editable”

Why keep MicroPython:

- great for hardware learning
- familiar to parents/schools
- useful prototype path
- good Advanced Mode / Python Lab
- existing projects such as MicroPythonOS and PocketDeck validate this approach

### 1.5 From “LVGL shell + runtime” to “native core + cartridge userland”

v0.3 architecture:

```text
ESP-IDF + FreeRTOS
  ↓
Moybyte Kernel
  ↓
LVGL shell
  ↓
Studio apps
  ↓
Sandboxed runtime
```

v0.4 architecture:

```text
ESP-IDF / FreeRTOS native core
  ↓
KidKernel services
  ↓
Fantasy workstation runtime
  ↓
Lua cartridge userland
  ↓
Desktop / tools / widgets / games / apps / lessons
```

LVGL can still be used for native shell/tools, especially early, but the long-term product identity should be cartridge-first rather than LVGL-app-first.

### 1.6 From 128×128 fantasy console to 480×270-ish fantasy workstation

v0.3 used a Moybyte-8 style 128×128 canvas.

v0.4 should support multiple logical modes:

```text
Workstation mode:
  480×270, 64-color target

Low-power / small game modes:
  240×135
  160×90
  128×128 compatibility mode
```

The physical LCD can still be 800×480 or 1024×600. The fantasy display is scaled.

### 1.7 PICO-8 compatibility becomes later, not central

PICO-8 compatibility-ish is still attractive, but it should not define the product. The device is not a PICO-8 machine and not a PicoPico/Pico Pal competitor.

v0.4 stance:

```text
PICO-8/Picotron inspiration:
  yes

PICO-8 compatibility mode:
  later experiment

Actual PICO-8 cartridge execution:
  not v1, likely not legal/practical as core product
```

### 1.8 Existing projects are references, not bases

References and what to steal:

| Reference | What it validates | What to avoid |
|---|---|---|
| Picotron | Fantasy workstation, Lua cartridges, userland tools, live desktop | Do not depend on proprietary runtime/ports |
| MicroPythonOS | App lifecycle, MicroPython UI/app shell, MPK packages, “everything is an app” | Android-like app model is not the final product feeling |
| Lua RTOS | Lua as ESP32-visible userland over FreeRTOS/hardware APIs | Old/IoT-oriented base; not graphical workstation-ready |
| PocketDeck | MicroPython + fast C modules + emulator-first dev | Productivity/cyberdeck framing, not kid creative workstation |
| PicoPico/Pico Pal | ESP32-class retro/fantasy console direction is plausible | Do not become a game console |
| Esposito | SD-loaded apps, Lua REPL, emulator, dynamic loading | Native ELF apps are parent/dev mode only |

---

## 2. Updated product definition

### 2.1 One-line concept

**Moybyte Console is a physical fantasy workstation for kids: a chunky keyboard-first creative computer where the desktop, tools, games, apps, art, music, lessons, and widgets are editable cartridges.**

### 2.2 Emotional promise

> **Your first real little computer — not for watching videos, but for making the computer itself come alive.**

### 2.3 Product pillars

1. **The computer is programmable**  
   The desktop, wallpaper, pets, widgets, apps, games, and tools are all code-backed.

2. **Make, do not consume**  
   No passive media center. No open internet in child mode. Creation first.

3. **Everything is a cartridge**  
   A project, app, game, wallpaper, widget, lesson, tool, and demo are variations of the same bundle format.

4. **Lua-first visible world**  
   The visible computer is Lua/userland where possible. The hidden hardware core is native C/C++.

5. **Progressive complexity**  
   Cards → blocks → Lua → MicroPython/hardware lab → advanced file/system mode.

6. **Instant edit/run loop**  
   Change one thing, press Run, see the computer change.

7. **Local-first and parent-safe**  
   Offline by default. Local sharing first. AI optional and parent-gated.

8. **Physical real-computer identity**  
   Integrated keyboard, Run/Save/Home/Share buttons, D-pad/buttons, touch/pointer support.

### 2.4 What it is not

Moybyte/Spryte is not:

- a cheap tablet
- a Chromebook replacement
- a normal Linux desktop
- a pure game console
- a PICO-8 clone
- a Picotron clone
- an Android-like app store device
- an open child chat device
- “ChatGPT for kids”
- a toy laptop with canned games

---

## 3. The v0.4 core experience

### 3.1 First boot

The device boots into a playful living desktop.

Example:

```text
[Space wallpaper with slow stars]
[Big friendly icons: Make, Paint, Code, Play, Share]
[Small pet/clock widget]
[Run / Save / Home / Share physical keys]
```

The first call-to-action is not “Make Game.”

It is:

> **Make it mine**

### 3.2 First session flow

```text
1. Child turns on device.
2. Desktop appears with a simple animated wallpaper.
3. Child taps “Make it mine.”
4. Chooses a theme: Space / Ocean / Castle / Frog Pond / Robot Lab.
5. Adds stickers/stars/pet.
6. Presses Run.
7. Desktop changes immediately.
8. Opens “See the code” or “Change with cards.”
9. Changes one number/color/sprite.
10. Presses Run again.
11. Saves as “My Space Computer.”
```

This teaches the key idea:

> The computer is made of editable things.

### 3.3 First five projects

The initial learning ladder should be:

```text
1. My Living Desktop
   Change wallpaper, stars, colors, stickers.

2. My Desktop Pet
   Add a frog/cat/robot that moves.

3. My Pet Reacts
   Pet follows touch, keyboard, D-pad, or button press.

4. My Pet Collects Stars
   Add scoring and collision: game mechanics emerge naturally.

5. Tiny Runner / Tiny Maze
   First explicit game project.
```

This progression is better than jumping directly into a platformer.

---

## 4. Cartridge-first object model

### 4.1 Everything is a cartridge

A cartridge is the one object type in the system.

Cartridge modes:

```text
app
wallpaper
widget
game
story
animation
music
lesson
tool
theme
screensaver
hardware_project
web_project
```

The same package structure works for all of them.

### 4.2 Cartridge package structure

Folder form:

```text
my_space_desktop.moy/
  manifest.json
  main.lua
  sprites.moygfx
  sounds.ksfx
  map.moymap
  data.pod.json
  preview.kimg
  README.md
```

Packed form later:

```text
my_space_desktop.moy.zip
```

Maybe a cute final extension later:

```text
my_space_desktop.spryte
```

But keep `.moy` internally for now.

### 4.3 Manifest example

```json
{
  "format": "moybyte-cart-v1",
  "title": "My Space Desktop",
  "type": "wallpaper",
  "runtime": "lua",
  "main": "main.lua",
  "canvas": { "width": 480, "height": 270, "palette": "moy64" },
  "permissions": ["graphics", "input", "sound"],
  "age_mode": "cards",
  "safe_to_share": true
}
```

### 4.4 System cartridges

The visible system should eventually be built from protected cartridges:

```text
/system/carts/
  desktop.moy
  launcher.moy
  file_browser.moy
  sprite_editor.moy
  code_editor.moy
  map_editor.moy
  sound_editor.moy
  settings.moy
  clock_widget.moy
  lessons_intro.moy
```

Rules:

- system cartridges are protected
- child can open/read them in advanced modes
- child can duplicate them
- child edits the duplicate, not the protected original
- parent can restore system cartridges

This gives the “everything is Lua” magic without letting the child brick the device.

### 4.5 User cartridges

```text
/home/projects/
  living_desktops/
  games/
  apps/
  art/
  stories/
  hardware/
  web/

/home/widgets/
/home/wallpapers/
/shared/received/
/shared/exported/
```

The gallery should not expose filesystem complexity to young children. Internally, keep folders simple and inspectable.

---

## 5. Software architecture v0.4

### 5.1 Layer model

```text
Hardware
  - ESP32-S3 or ESP32-P4 + ESP32-C6/S3 companion
  - display, touch, keyboard, buttons, D-pad, audio, SD, wireless

Native core: ESP-IDF / FreeRTOS / C/C++
  - drivers
  - memory/tasks
  - display pipeline
  - input scanning
  - audio output
  - filesystem
  - radio/wireless
  - watchdogs

KidKernel services
  - cartridge lifecycle
  - permissions
  - graphics canvas
  - input routing
  - audio mixer
  - storage API
  - share/radio API
  - module API
  - AI gateway client
  - parent settings
  - crash recovery

Fantasy workstation runtime
  - Lua VM host
  - safe APIs
  - frame/update loop
  - scheduler/budgets
  - cartridge loader
  - error mapper

Lua userland
  - desktop
  - wallpaper
  - widgets
  - launcher
  - tools eventually
  - apps/games/lessons

Optional advanced runtimes
  - MicroPython Lab
  - native dev apps in parent/maker mode
```

### 5.2 Native core responsibilities

Native C/C++ owns anything that must be reliable or fast:

```text
boot
crash recovery
watchdog
battery/power
LCD/display driver
touch/keyboard/buttons
framebuffer/compositor
sprite blitter
tilemap renderer
audio mixer
filesystem/mounting
Wi-Fi/ESP-NOW/BLE
permissions
parent lock
firmware update
AI/network client
```

This prevents the “everything in Lua” dream from slowing down the whole device.

### 5.3 Lua userland responsibilities

Lua should own the visible, remixable computer:

```text
desktop behavior
wallpapers
widgets
small apps
games
lessons
project templates
tool scripts eventually
settings panels eventually
```

The target feeling:

> Open any visible thing, duplicate it, inspect it, remix it.

### 5.4 LVGL role

LVGL is still useful, but not the product concept.

v0.4 LVGL rule:

```text
Use LVGL for native shell/tools where practical.
Do not expose raw LVGL as the child programming API.
Keep child projects on a controlled fantasy canvas.
```

Possible staging:

```text
v0.4a:
  Native LVGL shell, Lua cartridges run inside canvas.

v0.4b:
  Desktop wallpaper/widget cartridges run behind native launcher.

v0.5:
  Launcher/desktop become Lua-driven with native fallback.

v1+:
  More tools become Lua cartridges.
```

### 5.5 Runtime lifecycle

Cartridge states:

```text
installed
loaded
running
paused
crashed
stopped
uninstalled
```

Runtime actions:

```text
Run
Stop
Restart
Pause
Save
Duplicate
Share
Restore
Inspect
```

Safety rules:

- Home/Stop must always work
- runaway Lua script must be interruptible
- crash returns to friendly error screen, not reboot loop
- system cartridges protected
- network/hardware/AI permissions explicit

---

## 6. Lua strategy

### 6.1 Lua is the primary fantasy runtime

Use Lua for the main workstation experience.

Target model:

```lua
function _init()
  -- setup
end

function _update(dt)
  -- logic
end

function _draw()
  -- drawing
end
```

For younger children, Cards/Blocks compile to or drive the same model.

### 6.2 “Everything is Lua” but not literally

Correct interpretation:

```text
Visible computer:
  Lua cartridges

Invisible machine:
  native firmware
```

Bad literal interpretation:

```text
Lua does display drivers, audio mixing, filesystem, compositor, all UI layout, and pixel loops.
```

Good interpretation:

```text
Lua calls spr(), map(), sfx(), desktop.add_widget().
C/C++ implements the heavy operations.
```

### 6.3 Lua VM isolation

Ideal long-term:

```text
one Lua state per running cartridge
separate budgets
clean unload/restart
crash one cartridge, not the desktop
```

Pragmatic v0.4:

```text
one foreground Lua app/cart
optional one wallpaper cart
strict Stop/Home watchdog
full runtime reset between runs
```

Do not attempt full process-like multitasking in v0.4.

### 6.4 Lua APIs

Core APIs:

```lua
-- lifecycle
_init()
_update(dt)
_draw()

-- graphics
cls(color)
pset(x, y, color)
line(x1, y1, x2, y2, color)
rect(x, y, w, h, color)
rectfill(x, y, w, h, color)
circ(x, y, r, color)
spr(id, x, y, opts)
sspr(...)
map(...)
print(text, x, y, color)
pal(a, b)
camera(x, y)
clip(x, y, w, h)

-- input
btn("left")
btnp("a")
key("space")
pointer()
touch()

-- sound
beep(freq, duration)
sfx(id)
music(id)
volume(n)

-- storage
store(key, value)
fetch(key)
files.list(path)
project.save()

-- desktop/workstation
desktop.set_wallpaper(cart)
desktop.add_icon(title, cart, x, y)
desktop.add_widget(cart, x, y)
desktop.notify(text)
window.open(opts)
dialog.alert(text)

-- sharing/radio
radio.send(data)
radio.on_message(fn)
share.send_project(path)

-- hardware, gated
module.list()
module.read(name)
led.set(index, color)
servo.set(name, angle)

-- AI, gated
helper.explain_error(error_id)
helper.suggest_next_step(project_context)
```

### 6.5 Example: living wallpaper

```lua
stars = {}

function _init()
  for i = 1, 80 do
    stars[i] = { x = rnd(480), y = rnd(270), speed = 1 + rnd(2) }
  end
end

function _update(dt)
  for s in all(stars) do
    s.y = s.y + s.speed
    if s.y > 270 then s.y = 0 end
  end
end

function _draw()
  cls(1)
  for s in all(stars) do
    pset(s.x, s.y, 7)
  end
  spr("frog", 220, 190)
end
```

### 6.6 Cards/Blocks bridge

The first editor does not need full Scratch.

Use simple cards that generate or edit Lua-backed project data:

```text
When computer starts:
  set background to Space
  add 80 stars
  add Frog pet

Every frame:
  move stars down
  if Frog touches pointer, jump
```

Advanced view reveals Lua.

---

## 7. MicroPython strategy

### 7.1 MicroPython is realistic as a scripting layer

MicroPython can be embedded or used as a firmware-level scripting runtime, and existing projects validate it on ESP32-class devices.

But for this product it should not be the main visible OS layer in v0.4.

### 7.2 Why not MicroPython as the primary cartridge runtime?

MicroPython is excellent, but less clean for Picotron-like cartridge semantics:

```text
Needs:
  load cartridge
  run
  pause/stop
  reset/unload
  isolate
  restart cleanly

MicroPython can do much of this, but it is more naturally a board-level scripting environment.
Lua is more naturally a small host-controlled app VM.
```

MicroPython also makes it tempting to expose raw hardware too early, which conflicts with kid-safe cartridge design.

### 7.3 Where MicroPython belongs

Use MicroPython for:

```text
Python Lab
Hardware Lab
sensor/robotics examples
advanced text coding
parent/maker scripts
maybe Web Lab prototypes
maybe v0 proof-of-concept shell if Lua path stalls
```

Example:

```python
from kid import screen, module

temp = module.read("temperature")
screen.text(f"Room: {temp} C", 10, 10)
```

### 7.4 MicroPython integration rule

MicroPython should call the same KidKernel APIs as Lua where possible.

```text
Lua spr()       → native moy_canvas_sprite()
Python kid.spr → native moy_canvas_sprite()
```

This keeps the platform language-neutral internally while keeping Lua as the default user experience.

---

## 8. UI and interaction model

### 8.1 Modes/themes

v0.4 keeps the v0.3 theme idea, but reframes themes as cartridge-driven environments.

#### Simple Mode

For younger kids.

- big icons
- no overlapping windows
- guided “Make it mine” flow
- cards first
- minimal file concepts

#### Classic Desktop

Main emotional target.

- wallpaper
- icons
- taskbar/status bar
- pointer
- managed windows
- widgets
- file/project shelf
- “Refresh Desktop” joke
- desktop can be duplicated/remixed later

#### Fantasy Workstation

Picotron/PICO-8-like mode.

- cartridge shelf
- Code / Draw / Map / Sound / Run
- canvas preview
- direct Lua editing

#### Maker Mode

For parent/advanced kid.

- files
- console/logs
- MicroPython Lab
- hardware modules
- network settings
- AI endpoint
- diagnostics

### 8.2 Window model

No real arbitrary desktop process model.

Use managed panels:

```text
Desktop layer
Wallpaper cartridge layer
Widget layer
System panel layer
Foreground app/tool layer
Dialog/error layer
```

Max active runtime in v0.4:

```text
1 foreground cartridge
+ 1 lightweight wallpaper cartridge
+ maybe static widgets
```

### 8.3 Physical buttons

Keep v0.3 hardware buttons:

```text
Run
Save
Home/Stop
Share
D-pad
A/B/X/Y
Keyboard
Touch
Optional pointer/mouse
```

Run should feel physical and magical.

Home/Stop must be the panic escape.

### 8.4 First-session UI labels

Prefer:

```text
Make it mine
Paint
Code
Play
Share
Help
Parent
```

Avoid starting with:

```text
IDE
Runtime
Console
File Manager
Compiler
```

---

## 9. Hardware direction v0.4

### 9.1 Keep ESP32 appliance direction

v0.4 does not return to a Linux/Radxa laptop path for v1.

Reason:

- cost and complexity get worse
- product becomes tablet/Chromebook-adjacent
- open Linux desktop distracts from the cartridge workstation moat
- ESP32 keeps it appliance-like, local-first, charming, and focused

Linux/Radxa can remain useful for:

```text
PC simulator
AI/local gateway testing
high-end future branch
parent dev environment
```

But not the core v1 product target.

### 9.2 Development hardware ladder

```text
Phase 0A: PC simulator
  - fastest Codex/dev loop
  - run cartridge runtime and UI mockups locally

Phase 0B: LilyGO T-Deck
  - immediate ESP32-S3 software mule
  - keyboard, screen, trackball, radio
  - prove boot/run/save/input/runtime

Phase 1: 7-inch ESP32-S3 board
  - validate 7-inch UI and touch
  - lower-cost path

Phase 1.5: ESP32-P4 + ESP32-C6/S3 companion
  - preferred polished direction
  - better display/UI headroom
  - wireless via companion

Phase 2: semi-custom integrated prototype
  - keyboard, D-pad, Run/Save/Home/Share
  - 7-inch display
  - battery
  - speaker
  - microSD
  - shell

Phase 3: product hardware
  - custom board/shell only after family validation
```

### 9.3 ESP32-S3 vs ESP32-P4

| Choice | v0.4 role |
|---|---|
| ESP32-S3 | software mule / low-cost feasibility target |
| ESP32-P4 + wireless companion | preferred polished 7-inch workstation target |
| Linux/Radxa | simulator/gateway/future high-end, not core v1 |

### 9.4 Screen/canvas

Physical screen:

```text
800×480 minimum
1024×600 preferred on P4-class board
7-inch target
```

Logical canvas modes:

```text
480×270 workstation
240×135 low-power/game
160×90 tiny demo
128×128 compatibility/game mode
```

### 9.5 Keyboard/body

Carry forward v0.3:

- single-piece slate
- integrated keyboard
- no hinge in v1
- chunky/rugged
- kid-readable QWERTY
- D-pad/ABXY
- Run/Save/Home/Share
- optional rear kickstand
- parent-repairable where practical

---

## 10. Tools roadmap

### 10.1 Tool principle

Eventually:

> Built-in tools are cartridges too.

Pragmatically:

> Build tools natively first where needed, then expose/replace with Lua cartridges over time.

### 10.2 Tools list

Minimum v0.4 tools:

```text
Project Gallery
Make It Mine editor
Simple Cards editor
Basic Code view
Run preview
Friendly error screen
Save/Duplicate/Restore
```

Next tools:

```text
Sprite Paint
Simple Map/Background editor
Sound/beep editor
File Browser
Share/Receive
Tiny Help/Docs
```

Later tools:

```text
Music tracker
Theme editor
Widget editor
Web Lab
Hardware Lab
MicroPython Lab
AI helper UI
Advanced console
```

### 10.3 Tool implementation staging

```text
Stage 1:
  Native LVGL tool screens editing cartridge data.

Stage 2:
  Lua cartridges can run as wallpapers/widgets/apps.

Stage 3:
  Some system panels become Lua-driven.

Stage 4:
  Sprite editor / file browser / lessons become inspectable Lua cartridges.

Stage 5:
  User can duplicate and remix system tools safely.
```

---

## 11. AI helper v0.4

v0.3 AI direction remains good.

v0.4 adjustment:

AI should understand cartridges and desktop customization, not only game code.

Allowed AI tasks:

```text
explain this error
suggest one small change
make this wallpaper more fun
turn this pet into a game idea
summarize my project for parent
convert drawing to tiny sprite
write instructions for my cartridge
suggest next quest
```

Not allowed by default:

```text
open-ended child chat
AI companion behavior
raw internet search
unrestricted image generation
unapproved sharing
```

Architecture remains:

```text
Device → Parent-approved gateway → cloud/local/custom LLM → schema-checked response → device
```

AI should return structured assets/hints, not arbitrary freeform control of the system.

---

## 12. Sharing and multiplayer v0.4

v0.3 sharing remains strong.

v0.4 adds that sharing is cartridge-first:

```text
share wallpaper
share pet
share widget
share sprite
share game
share lesson
share high score
share local multiplayer invite
```

First sharing demo should probably be smaller than full project transfer:

```text
1. Share a sprite/sticker.
2. Share a wallpaper cartridge.
3. Share Tiny Runner later.
4. Add multiplayer demos after cartridge transfer works.
```

Use ESP-NOW where available, with parent setting for receive/send.

---

## 13. Privacy and safety v0.4

Carry forward v0.3 local-first principles.

Add cartridge permission model more explicitly:

```json
{
  "permissions": [
    "graphics",
    "input",
    "sound",
    "local_storage"
  ]
}
```

Higher-risk permissions:

```text
radio_send
radio_receive
local_web_server
wifi_internet
ai_helper
sensor_read
actuator_write
raw_files
advanced_system
```

Rules:

- wallpapers/widgets cannot silently use network
- AI permission must be parent-gated
- received cartridges are quarantined until accepted
- system cartridges protected
- parent can restore defaults
- no child real-name requirement
- no accounts for local use

---

## 14. MVP v0.4: Living Desktop

### 14.1 MVP goal

Build the smallest demo that proves:

> A child can edit the computer itself, press Run, and see the desktop change.

### 14.2 MVP hardware

Acceptable MVP targets:

```text
PC simulator first
LilyGO T-Deck second
7-inch ESP32 board third
```

### 14.3 MVP software must-haves

```text
1. Boot to shell/desktop.
2. Show a live wallpaper cartridge.
3. Open “Make it mine.”
4. Change at least 3 visible parameters:
   - theme/background
   - number/speed of stars
   - pet/sprite choice or color
5. Save as user cartridge.
6. Press Run to restart/apply.
7. Press Home/Stop to recover.
8. Show friendly error if script breaks.
9. Duplicate protected system wallpaper into user project.
10. Load saved user wallpaper on boot.
```

### 14.4 MVP nice-to-haves

```text
Tiny Runner as second/third demo
basic sprite editor
local share a wallpaper/sticker
Lua code view
cards-to-Lua preview
ESP-NOW ping/share proof
```

### 14.5 MVP demo script

Demo to show a parent/investor/friend:

```text
1. Device boots into Space Desktop.
2. Press Make it mine.
3. Change stars from 20 to 80.
4. Add Frog pet.
5. Press Run.
6. Frog appears on desktop and stars animate.
7. Open Code view; change frog x position or speed.
8. Press Run again.
9. Save as “Nikola Space.”
10. Press Share to send wallpaper/sticker to another device or simulator.
```

That sells the concept better than a generic mini-game.

---

## 15. Codex implementation plan v0.4

### 15.1 Repository structure

```text
moybyte-console/
  firmware/
    main/
      app_main.cpp
      moy_kernel/
      moy_display/
      moy_canvas/
      moy_input/
      moy_audio/
      moy_storage/
      moy_runtime_lua/
      moy_cartridge/
      moy_permissions/
      moy_radio/
      moy_ai_client/
      moy_ui_native/
    components/
      lua/
      lvgl/
      display_driver/
      keyboard_driver/
  runtime/
    lua_api_docs/
    test_carts/
  system_carts/
    desktop.moy/
    wallpaper_space.moy/
    clock_widget.moy/
    frog_pet.moy/
  examples/
    living_desktop/
    tiny_runner/
    pong_multiplayer/
  tools/
    simulator/
    asset_packer/
    cart_packer/
  gateway/
    server/
    schemas/
    providers/
  docs/
```

### 15.2 Task group A — PC simulator first

Goal: fast development loop.

Tasks:

```text
A1. Create desktop simulator app.
A2. Implement virtual 480×270 canvas.
A3. Implement keyboard/pointer input.
A4. Load cartridge folder from local filesystem.
A5. Run a hardcoded C demo first.
A6. Add Lua runtime.
A7. Run wallpaper_space.moy.
```

Acceptance:

```text
Desktop window opens on PC.
Space wallpaper animates.
Changing main.lua or manifest changes the output after restart.
```

### 15.3 Task group B — Cartridge format

Tasks:

```text
B1. Define manifest schema.
B2. Implement cartridge loader.
B3. Implement safe path resolution.
B4. Implement preview metadata.
B5. Implement duplicate/save project.
B6. Implement system vs user cartridge flag.
```

Acceptance:

```text
System wallpaper can be duplicated into /home/projects.
User copy can be edited without touching system copy.
```

### 15.4 Task group C — Lua runtime

Tasks:

```text
C1. Embed Lua.
C2. Register lifecycle: _init/_update/_draw.
C3. Register drawing APIs: cls, pset, rect, spr, print.
C4. Register input APIs: btn, key, pointer.
C5. Register storage APIs: store/fetch minimal.
C6. Add runtime reset.
C7. Add instruction/time budget or watchdog.
C8. Add friendly error mapper.
```

Acceptance:

```text
A Lua wallpaper runs.
A Lua error returns to friendly error screen.
Stop/Home recovers from infinite loop.
```

### 15.5 Task group D — Native shell / Living Desktop

Tasks:

```text
D1. Boot to native desktop shell.
D2. Render wallpaper cartridge as background.
D3. Render static icons.
D4. Add Make It Mine panel.
D5. Edit manifest/config values.
D6. Apply changes with Run.
D7. Save user cartridge.
```

Acceptance:

```text
User can change wallpaper parameters and save the result.
Boot can load the saved user wallpaper.
```

### 15.6 Task group E — Cards editor

Tasks:

```text
E1. Define simple card model.
E2. Cards edit config or generate small Lua snippets.
E3. Implement cards for background, stars, pet, speed, follow pointer.
E4. Add “See code” read-only view.
E5. Later add editable code view.
```

Acceptance:

```text
Child can create a living desktop without typing syntax.
Advanced view shows the Lua behind it.
```

### 15.7 Task group F — Hardware port

Tasks:

```text
F1. Port simulator core to T-Deck.
F2. Display scaled canvas.
F3. Map keyboard/trackball/buttons.
F4. Mount SD or flash project directory.
F5. Test runtime memory and frame rate.
F6. Port to 7-inch board.
```

Acceptance:

```text
Same wallpaper cartridge runs on simulator and ESP hardware.
```

### 15.8 Task group G — Tiny Runner as secondary demo

Tasks:

```text
G1. Build Tiny Runner cartridge using same APIs.
G2. Use sprite movement and collision.
G3. Add score.
G4. Make speed/color editable.
G5. Add save/load.
```

Acceptance:

```text
Tiny Runner proves games are just another cartridge type.
```

### 15.9 Task group H — Sharing proof

Tasks:

```text
H1. Implement local export bundle.
H2. Implement receive/import in simulator first.
H3. Implement ESP-NOW metadata send.
H4. Send small sticker/wallpaper.
H5. Add checksum/retry later.
```

Acceptance:

```text
One device/simulator can send a tiny cartridge/sticker to another.
```

---

## 16. Development milestones

### Milestone 0 — Decision lock

Lock:

```text
Product metaphor: fantasy workstation
First MVP: Living Desktop
Primary runtime: Lua
MicroPython: advanced/lab/prototype option
Hardware: ESP32 appliance path
```

### Milestone 1 — Simulator living desktop

Deliver:

```text
PC simulator
480×270 canvas
Lua wallpaper
config editing
Run/Stop
save/duplicate
friendly error
```

### Milestone 2 — T-Deck living desktop

Deliver:

```text
Boot shell on T-Deck
run same Lua wallpaper
keyboard/trackball input
save/load on device
Home/Stop recovery
```

### Milestone 3 — 7-inch UI prototype

Deliver:

```text
same runtime on 7-inch board
Make It Mine UI feels good
touch drawing/editing test
basic sprite/pet selection
```

### Milestone 4 — Tiny Runner and project gallery

Deliver:

```text
project gallery
Living Desktop cart
Pet cart
Tiny Runner cart
duplicate/edit/run all of them
```

### Milestone 5 — Local share

Deliver:

```text
share sticker/wallpaper
receive/import
icon-code pairing
parent setting for receive
```

### Milestone 6 — AI helper proof

Deliver:

```text
forced Lua error
Ask Helper
gateway returns child-friendly explanation
no open chat
schema-validated response
```

---

## 17. Open decisions after v0.4

### Must decide soon

1. Final internal name: Moybyte OS vs Spryte OS vs other.
2. Lua version/runtime choice: Lua 5.4, Lua 5.3, Luau, or custom constrained dialect.
3. Whether to use LVGL PC simulator or SDL/custom simulator first.
4. Exact ESP32-P4 dev board for 7-inch testing.
5. Whether first cards editor edits JSON/config only or generates Lua.
6. Whether `.moy` remains the public extension.

### Can decide later

1. PICO-8 compatibility-ish mode.
2. MicroPython Lab timeline.
3. Blocks editor implementation.
4. LoRa/Meshtastic-style module.
5. Parent phone app vs local web page.
6. Cloud AI integration.
7. Final industrial design.
8. Lesson library strategy.

---

## 18. Final v0.4 recommendation

Lock this as the new plan:

```text
Moybyte/Spryte is a physical kid-safe fantasy workstation.
The visible computer is made of cartridges.
Lua is the primary fantasy runtime.
Native ESP-IDF/FreeRTOS owns hardware and safety.
MicroPython is optional Advanced/Python/Hardware Lab.
The first MVP is Living Desktop, not Tiny Runner.
Games remain a core project type, but not the whole identity.
```

The clearest first proof is:

> **A child edits a wallpaper/pet cartridge, presses Run, and the desktop itself changes.**

That is the magic.

