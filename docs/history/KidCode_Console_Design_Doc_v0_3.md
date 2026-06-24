# KidCode Console — Comprehensive Design Doc v0.3

**Project:** Kids Edu Slate / Laptop → KidCode Console / KidCode OS  
**Document version:** 0.3  
**Date:** 2026-06-17  
**Status:** Consolidated conclusions from multiple hardware, software, UI, runtime, AI, and product discussions  
**Purpose:** Provide one coherent product/specification document that can be given to Codex or another implementer as the current project direction.

---

## 0. Executive conclusion

The project should not try to become a cheap Android tablet, Chromebook, or tiny Linux laptop in v1. That path is attractive emotionally because it feels like “my first real computer,” but it quickly drifts into expensive tablet/laptop economics and distracts from the real moat.

The strongest current direction is:

> **KidCode Console is a child’s first programmable creative computer: a chunky keyboard-first ESP32-class game-making console where kids can draw sprites, write real code, press Run, play instantly, share locally, play multiplayer, and optionally receive safe AI help.**

The device should feel like a friendly old home computer / fantasy console / handheld maker machine, not a generic tablet.

The practical architecture is:

```text
ESP-IDF + FreeRTOS base
  ↓
KidCode Kernel / services layer
  ↓
LVGL system shell + themeable UI
  ↓
KidCode Studio apps
  ↓
Sandboxed project runtimes:
    - primary small scripting runtime, likely Lua-style or MicroPython-style
    - optional MicroPython/Python Lab if memory and stability allow
    - KidCode game/app APIs shared across runtimes
```

The product should support “windows” only as a **theme and UI metaphor**, not as a real desktop operating system with arbitrary processes. Kids can have a Classic Desktop theme with icons, wallpaper, taskbar, tiny app windows, a file browser, and even a funny **Refresh Desktop** action. Underneath, it remains one controlled shell.

---

## 1. Major conclusions extracted from prior discussions

### 1.1 Form factor conclusion

Earlier direction: Linux/Android educational slate or mini-laptop.  
Current conclusion: ESP32-class creative console.

The current best form factor is:

- single-piece slate body
- integrated physical keyboard
- no hinge in v1
- rear kickstand optional but not central
- 7-inch-ish screen preferred for comfort
- D-pad and ABXY controls
- dedicated physical **Run**, **Save**, **Home/Stop**, and **Share** buttons
- chunky, friendly, rugged shell
- old home-computer / Macintosh / fantasy-console vibe

The device should be keyboard-first. A child should feel they own a “real little computer,” but the system should be focused and safe.

### 1.2 Screen conclusion

A 7-inch screen is still the preferred target because the keyboard width naturally pushes the body toward that size anyway.

Target:

- 7 inch display
- 800×480 minimum
- 1024×600 preferred if using ESP32-P4-class hardware
- touch input useful but not the only input method
- UI should assume low resources and avoid desktop/laptop complexity

Drawing should work through multiple input paths:

- touch
- optional stylus
- D-pad pixel cursor
- keyboard shortcuts

Capacitive touch is fine for prototypes because ready-made boards support it. Resistive touch remains worth testing for sprite/pixel drawing because it allows cheap precise stylus input.

### 1.3 Keyboard conclusion

Keyboard is core product identity, not a secondary accessory.

The keyboard must feel better than a toy calculator. It does not need to be laptop-premium, but it must be good enough for naming projects, typing small code snippets, and feeling like a real computer.

Conclusion:

- real QWERTY layout
- kid-readable legends
- 14–16 mm key pitch minimum target
- physical Run/Save/Home/Share keys
- arrow keys or D-pad
- product direction: custom matrix keyboard
- prototype direction: LilyGO T-Deck keyboard first, then USB/matrix keyboard on a 7-inch ESP32 board

### 1.4 Mouse/pointer conclusion

Mouse support is possible but should not define v1.

Viable input options:

- touch
- keyboard
- D-pad
- trackball/mini pointer on dev boards like T-Deck
- USB mouse where USB host is available
- Bluetooth mouse only if stack/hardware support is stable

Product conclusion:

> Support pointer input in the UI model, but design every core workflow so it works with keyboard/D-pad/touch. Mouse is a bonus, not a requirement.

### 1.5 Desktop/windows conclusion

A full Windows-like desktop is not realistic or desirable on ESP32.

But a **desktop feeling** is realistic and valuable:

- wallpaper
- icons
- simple app launcher
- taskbar/status bar
- fake/managed windows
- file browser
- simple Paint app
- code editor
- project windows/panels
- pointer cursor
- right-click/long-press context menus
- “Refresh Desktop” nostalgia button

Conclusion:

> Implement desktop as a theme/mode on top of the same KidCode shell, not as a separate OS.

Supported themes/modes:

1. **Simple Launcher** — default for younger children
2. **Classic Desktop** — old computer nostalgia, icons/windows/taskbar/refresh
3. **Fantasy Console** — PICO-8-like make/play cartridge interface
4. **Advanced Hacker Mode** — files, console, hardware, network, Python/Lua lab

### 1.6 Runtime/language conclusion

The project needs real programming, not only canned blocks.

However, on-device C/C++ compiling is not realistic for v1. A full desktop IDE is also not realistic.

The realistic path is:

- build a system shell in ESP-IDF/FreeRTOS
- use LVGL for system UI
- store user projects as files on SD/flash
- Run executes a sandboxed script/project inside a controlled runtime panel/window
- expose safe KidCode APIs for graphics, input, sound, storage, radio, and hardware
- support beginner Cards/Blocks first, then text scripting

Language direction:

- **Lua-style scripting** is technically attractive: small, embeddable, game-friendly, easier to sandbox.
- **MicroPython** is educationally attractive: real Python-ish learning path, familiar to parents/schools, can write `.py` files.
- Best architecture: make the KidCode API language-neutral so Lua and MicroPython can share the same graphics/input/hardware services.

Recommended product decision:

> Use one primary scripting runtime for v0 stability. Architect for both Lua and MicroPython, but do not let dual-runtime support block the first working product.

Practical staging:

1. v0: KidCode action/cards model + one embedded scripting runtime.
2. v0.5: text editor writes project files to SD/flash and runs them in a sandbox.
3. v1: optional **Python Lab** if MicroPython fits well.
4. Later: more advanced language/runtime experiments.

### 1.7 Graphics conclusion

LVGL should be used for the system GUI, but kid programs should not directly manipulate LVGL widgets by default.

Conclusion:

- LVGL = shell, menus, editor UI, file browser, settings, parent screens
- KidCode canvas = user app/game output surface
- Kid project APIs draw onto a controlled canvas, not raw LVGL
- advanced mode may expose some UI widgets later, but beginner apps should use safe high-level APIs

This keeps projects portable, sandboxable, and understandable.

### 1.8 “Kernel” conclusion

The “kernel” idea is good as a product/software architecture metaphor, even though technically this is not a Unix kernel.

Use:

- ESP-IDF/FreeRTOS as the real base
- **KidCode Kernel** as the internal services layer

KidCode Kernel owns:

- app lifecycle
- project storage
- input events
- graphics canvas
- sound
- permissions
- radio/network services
- expansion modules
- AI gateway client
- crash recovery
- parent settings

This gives the project a clean mental model and makes it easier for advanced parents or contributors to add modules.

### 1.9 Game engine conclusion

Yes, the project is effectively building a small 2D game engine and creative runtime.

That is acceptable and actually central to the product moat.

2D is core. 3D should remain experimental only.

Realistic:

- sprites
- tile maps
- collisions
- simple physics
- animation
- sound effects
- local multiplayer
- small project cartridges

Possible but not core:

- pseudo-3D raycaster
- voxel/block-world demo at very low resolution
- Mode 7 style effects
- wireframe experiments

Not v1:

- full Minecraft clone
- hardware-accelerated modern 3D
- large 3D worlds
- video streaming or media-heavy gaming

### 1.10 Web/dev conclusion

A simple web lab is possible and valuable, but it should be scoped carefully.

Possible:

- device hosts a tiny local HTTP server
- parent phone/tablet/computer opens the child’s page on the same Wi-Fi
- child edits simple HTML/CSS/JS-like project or template
- page can display sprite/game/project info
- parent can view project gallery

Not realistic on ESP32 as a full browser development environment:

- full Chrome-like browser
- modern web app dev stack
- Node/npm
- heavy JavaScript tooling

Conclusion:

> Include Web Lab as a constrained local-server creative mode, not as “real web dev laptop replacement.”

### 1.11 Network/hardware conclusion

Network and hardware should not be isolated labs only. They should fold into Game Lab, App Lab, and Web Lab.

Examples:

- Game Lab can use ESP-NOW multiplayer.
- App Lab can read sensors or control LEDs.
- Web Lab can expose sensor data on a local web page.
- Hardware Lab can teach circuits/modules more directly.

Conclusion:

> Network and hardware are platform capabilities, surfaced inside all labs through safe APIs.

### 1.12 AI conclusion

AI is valuable, but it must not be “ChatGPT for kids.”

Child-facing AI should be a set of constrained tools:

- explain this error
- give me a hint
- make this code simpler
- suggest the next challenge
- clean up my sprite
- generate a tiny 16-color sprite idea
- write instructions for my game
- explain what my game does

The ESP32 should not call LLM APIs directly. Use a gateway.

Architecture:

```text
KidCode device
  → KidCode Gateway
  → cloud LLM or parent local LLM
  → safety / schema validation
  → KidCode device
```

Parent modes:

1. AI off
2. KidCode cloud helper
3. parent-local AI server
4. custom OpenAI-compatible endpoint

AI output should be schema-constrained and converted into device-native assets or hints. It should support privacy-conscious parents who run local models.

### 1.13 Sharing/multiplayer conclusion

Local-first sharing is one of the strongest differentiators.

Conclusion:

- ESP-NOW for same-room multiplayer and project exchange
- no accounts required
- no router required for local sharing
- host/client model for games
- small project bundles
- kid-friendly pairing using icon codes

Start with:

- share sprite
- share game/project
- share high score
- two-player Pong or maze demo

### 1.14 Hardware platform conclusion

Immediate development should use the existing LilyGO T-Deck.

Hardware track:

| Stage | Hardware | Purpose |
|---|---|---|
| Phase 0 | LilyGO T-Deck | software mule: shell, editor, runtime, save/load, radio |
| Phase 1 | cheap 7-inch ESP32-S3 board | validate big screen UI and touch |
| Phase 1.5 | ESP32-P4 + ESP32-C6 board | validate smoother LVGL, 1024×600, stronger UI |
| Phase 2 | semi-custom prototype | keyboard, controls, shell, battery, expansion |
| Phase 3 | custom board/shell | only after product validation |

ESP32-S3 may be enough for a constrained v1. ESP32-P4+C6 is a better polished direction but adds complexity because P4 has no built-in wireless.

### 1.15 Product positioning conclusion

The strongest positioning is not “educational tablet.”

Better positioning:

- first programmable console
- first game-making computer
- creative coding handheld
- local-first kid maker computer

The emotional product promise:

> “A real little computer where your child can make their own games, art, gadgets, and multiplayer worlds — safely, locally, and without becoming another tablet.”

---

## 2. Product definition

### 2.1 One-line concept

**KidCode Console is a keyboard-first creative coding console for kids: draw sprites, write simple real code, press Run, play instantly, share locally, and learn computing through making.**

### 2.2 Product pillars

1. **Make, don’t consume**  
   The device exists for creation, not passive media.

2. **Keyboard-first real-computer feeling**  
   It should feel like a child’s first own computer.

3. **Instant Run loop**  
   Edit → Run → Play → Fix must be fast and satisfying.

4. **Local-first and private**  
   Core features work offline and without accounts.

5. **Safe expandability**  
   Sensors, LEDs, radio, and modules are available through safe APIs.

6. **Optional AI helper, not AI companion**  
   AI helps with errors, sprites, explanations, and parent summaries.

7. **Multiplayer and sharing as magic**  
   Same-room sharing creates delight and social learning.

### 2.3 What it is not

KidCode Console is not:

- a cheap laptop
- a Chromebook competitor
- an Android tablet with a coding app
- a fake toy laptop with canned games
- a general-purpose Linux desktop
- a full Scratch clone
- a full PICO-8 clone
- an open internet chat device
- “ChatGPT for kids”

---

## 3. Target users and modes

### 3.1 Age bands

| Age | Mode | Experience |
|---|---|---|
| 4–6 | Cards Mode | choose actions, draw, press Run, cause/effect |
| 6–8 | Blocks Mode | Scratch-like logic, sprites, loops, conditions |
| 8–10+ | Text Mode | Lua/Python-like scripts, variables, functions, sensors |
| Parent/maker | Advanced Mode | files, runtime settings, gateway, modules, diagnostics |

### 3.2 User roles

- **Child:** makes games, drawings, tiny apps, projects
- **Parent:** configures safety, AI, Wi-Fi, sharing, backups
- **Advanced child:** explores code, sensors, web lab, radio
- **Maker parent:** adds modules and hacks the platform
- **School/workshop:** uses guided lesson/project packs

---

## 4. Core product loop

The main loop is:

```text
Draw → Code → Run → Play → Fix → Share → Multiplayer → Show parent
```

The ideal first-session flow:

1. Child turns on device.
2. Device opens simple launcher.
3. Child chooses **Make Game**.
4. Child picks or draws a sprite.
5. Child chooses behavior card or edits tiny code.
6. Child presses physical **Run**.
7. Game starts instantly.
8. Child presses **Home/Stop** to return.
9. Child changes one thing.
10. Child presses **Run** again.
11. Child saves and names the project.
12. Child shares with sibling/friend locally.

The physical Run button is important because it makes programming feel like a real action.

---

## 5. Hardware design

### 5.1 Industrial design direction

A chunky, friendly, repairable single-body console:

```text
┌─────────────────────────────────────────────┐
│                 7" SCREEN                   │
│                                             │
│   speaker                          speaker │
├─────────────────────────────────────────────┤
│ D-pad   Q W E R T Y U I O P      A B X Y   │
│         A S D F G H J K L                  │
│         Z X C V B N M                      │
│       Space        Run Save Home Share      │
└─────────────────────────────────────────────┘
```

Style references:

- old Macintosh/home computer warmth
- fantasy console
- Game Boy / DS creativity
- M5 Cardputer hacker toy
- kid-rugged educational device

Avoid:

- generic tablet look
- cheap toy laptop look
- fragile hinge
- cluttered gaming handheld aesthetic

### 5.2 Hardware targets

| Area | Target |
|---|---|
| Display | 7 inch, 800×480 minimum, 1024×600 stretch |
| CPU | ESP32-S3 minimum, ESP32-P4+C6 preferred for polished UI |
| RAM | Use PSRAM-equipped modules only |
| Storage | internal flash + microSD preferred |
| Wireless | Wi-Fi, Bluetooth if available, ESP-NOW required for sharing/multiplayer |
| Input | keyboard, D-pad, ABXY, Run/Save/Home/Share, touch |
| Audio | speaker/buzzer, simple SFX, optional mic only if privacy-safe |
| Battery | rechargeable Li-ion/LiPo pack, safe charge path |
| Expansion | kid-safe module port + hidden maker/debug port |
| Shell | screwable, rugged, rubber bumpers, repairable where feasible |

### 5.3 Hardware stages

#### Phase 0: LilyGO T-Deck

Use this immediately because it already has:

- ESP32-S3
- keyboard
- small screen
- trackball/pointer
- Wi-Fi/Bluetooth
- microSD on many versions
- LoRa variant support
- battery-capable form factor

Prove:

- launcher
- LVGL shell
- project files
- Run loop
- text/action editor
- canvas runtime
- save/load
- ESP-NOW demo
- local share demo
- AI gateway request/response

Do not optimize UI around the T-Deck screen; it is only the software mule.

#### Phase 1: 7-inch ESP32 dev board

Use a cheap 7-inch ESP32-S3 or ESP32-P4 display board to validate:

- real screen layout
- touch sprite editor
- scaled canvas
- keyboard over USB or matrix
- battery behavior
- performance
- UI density

#### Phase 2: Integrated prototype

Build a semi-custom device with:

- 7-inch screen
- custom keyboard
- D-pad/ABXY
- physical Run/Save/Home/Share
- speaker
- battery
- microSD
- protected expansion connector
- 3D-printed or CNC shell

#### Phase 3: Product hardware

Only after testing with real children/families:

- custom carrier board if justified
- manufacturable shell
- improved keyboard
- safety/compliance review
- accessory/module ecosystem

### 5.4 ESP32-S3 vs ESP32-P4+C6

| Option | Pros | Cons | Use |
|---|---|---|---|
| ESP32-S3 | cheap, integrated Wi-Fi/BLE, ESP-NOW easy, mature | weaker GUI, memory tight | v0/v1 cost-focused |
| ESP32-P4 + C6 | stronger HMI/UI, better display potential | needs companion wireless chip, more integration risk | polished prototype / premium v1 |

Recommendation:

- develop core software so it can run on S3
- keep P4+C6 as the better-screen/better-UI path
- do not depend on P4-only features in the first software architecture

### 5.5 Input system

Required inputs:

- QWERTY keyboard
- D-pad
- A/B/X/Y buttons
- Run
- Save
- Home/Stop
- Share
- touch

Optional inputs:

- trackball
- USB mouse
- Bluetooth mouse
- capacitive stylus
- resistive stylus

Input abstraction:

```text
physical inputs → KidInput events → shell/runtime/tools
```

Every app should receive normalized events:

- key down/up
- button down/up
- pointer move/click
- touch down/move/up
- gamepad direction/action

This lets the same project run on T-Deck, 7-inch prototype, or final hardware.

### 5.6 Battery target

Realistic target:

- v0 off-the-shelf board: 3–5 hours
- product target: 4–6 hours
- stretch target: 6–8 hours

Design for:

- dimming
- sleep
- quick resume
- battery indicator
- safe charging
- no exposed raw cells
- parent-replaceable battery only if feasible and safe

### 5.7 Expansion

Two expansion paths:

#### Kid-safe cartridge/module port

For child-facing modules:

- keyed connector
- protected power
- I2C first
- module identification
- chunky plastic modules
- no exposed fragile pins

Example modules:

- RGB LED
- distance sensor
- temperature/humidity
- knobs/buttons
- servo/motor
- sound sensor
- LoRa radio
- simple robotics

#### Hidden maker/debug port

Under screw flap for parents/makers:

- GPIO
- UART
- I2C
- SPI
- 3.3V
- GND
- boot/debug access

Principle:

> Child modules should be safe and rugged. Raw GPIO belongs behind a parent/maker door.

### 5.8 LoRa / Meshtastic-style module

LoRa is interesting but should not be open kid chat by default.

Good uses:

- treasure hunts
- long-range beacons
- sensor projects
- parent/maker Meshtastic experiments
- school/camp activities

Cautions:

- regional frequency rules
- duty-cycle limits
- child safety
- stranger contact risk

Recommendation:

- support LoRa as optional module or advanced variant
- do not make public/open chat a child-facing default
- for Serbia/Europe, 868 MHz module path is likely relevant

---

## 6. Software architecture

### 6.1 Stack overview

```text
Hardware
  - ESP32-S3 or ESP32-P4+C6
  - display, touch, keyboard, D-pad, buttons, speaker, SD, radio

ESP-IDF / FreeRTOS
  - drivers
  - tasks
  - memory
  - filesystems
  - Wi-Fi/Bluetooth/ESP-NOW

KidCode Kernel
  - app lifecycle
  - project manager
  - input service
  - graphics canvas service
  - sound service
  - storage service
  - radio/share service
  - permissions/sandbox
  - expansion module service
  - AI gateway client
  - settings/parent controls
  - crash recovery

LVGL Shell
  - launcher
  - themes
  - windows/panels
  - file browser
  - editors
  - settings
  - parent UI

KidCode Studio
  - Game Lab
  - Draw Lab
  - App Lab
  - Web Lab
  - Hardware Lab
  - Network Lab
  - Help/Docs

Project runtimes
  - Cards/Blocks interpreter
  - Lua-style or MicroPython-style text runtime
  - KidCode-8 game runtime
```

### 6.2 KidCode Kernel responsibilities

The KidCode Kernel is an internal services layer, not a real Unix kernel.

Responsibilities:

- boot sequence
- mode/theme selection
- app lifecycle
- project file loading/saving
- runtime start/stop/reset
- watchdog/crash recovery
- input dispatch
- graphics canvas ownership
- audio playback
- safe filesystem access
- network permissions
- ESP-NOW sessions
- module detection
- AI requests
- parent lock/settings

### 6.3 Task model

Use FreeRTOS tasks, but keep the user mental model simple.

Possible internal tasks:

- UI task
- input task
- runtime task
- storage task
- radio task
- audio task
- AI/network task
- module polling task

Important rule:

> A child project must never be able to freeze the whole device permanently.

Use:

- watchdogs
- runtime step budgets
- memory limits
- Stop/Home escape
- crash screen with friendly error
- safe reboot path

### 6.4 UI layer: LVGL shell

LVGL should own:

- launcher
- menus
- dialogs
- settings
- editor widgets
- file browser
- parent UI
- theme system
- fake windows/panels

Kid projects should render to a controlled canvas object, not arbitrary LVGL widgets.

### 6.5 Project runtime model

A project runs inside a controlled runtime container:

```text
Project file/package
  ↓
Runtime loader
  ↓
Sandbox permissions
  ↓
KidCode API bindings
  ↓
Canvas/audio/input loop
  ↓
Stop/Home returns to shell
```

Runtime states:

- unloaded
- loaded
- running
- paused
- crashed
- stopped

Required shell actions:

- Run
- Stop
- Restart
- Save
- Duplicate
- Share
- Export/log for parent

### 6.6 Filesystem and project layout

Use flash for system files and either microSD or internal flash for user projects.

Suggested project package:

```text
/projects/
  my_robot_game.kcart/
    manifest.json
    main.lua           or main.py / main.kid
    sprites.ksp
    tiles.ktile
    map.kmap
    sounds.ksnd
    README.txt
    preview.bmp/png/raw
```

Manifest example:

```json
{
  "format": "kidcode-project-v1",
  "title": "Robot Maze",
  "author_alias": "Blue Cat",
  "age_mode": "blocks",
  "runtime": "kidcode8-lua",
  "canvas": { "width": 128, "height": 128 },
  "permissions": ["graphics", "input", "sound"],
  "created_at": "device-local-time",
  "updated_at": "device-local-time"
}
```

Principles:

- projects are folders or zipped bundles
- easy backup/export
- share bundles are small
- no child real names required
- parent can inspect files in Advanced Mode

---

## 7. Runtime and programming model

### 7.1 KidCode-8 fantasy console

KidCode-8 is the creative runtime.

Initial constraints:

| Feature | Target |
|---|---|
| Canvas | 128×128 initially, maybe 160×120 later |
| Palette | 16 colors initially |
| Sprites | 8×8, 16×16, optional 32×32 |
| Tile map | small tile maps |
| Sound | beeps/SFX first, music later |
| Input | keyboard, D-pad, ABXY, touch optional |
| Storage | small project bundles |
| Sharing | local bundle transfer |
| Multiplayer | ESP-NOW host/client |

### 7.2 App loop

Recommended high-level model:

```text
setup()
update(dt)
draw()
```

Or beginner-friendly equivalent:

```text
When Run starts
Every frame
When button pressed
When sprite touches sprite
```

This supports both cards/blocks and text.

### 7.3 KidCode API

The runtime should expose safe APIs.

Graphics:

```text
clear(color)
sprite(name, x, y)
draw_sprite(id)
rect(x, y, w, h, color)
text("hello", x, y)
set_camera(x, y)
```

Input:

```text
button("left")
button("a")
key("space")
touch_x(), touch_y()
```

Sound:

```text
beep()
play_sfx("jump")
set_volume(5)
```

Game helpers:

```text
collides(a, b)
move(sprite, dx, dy)
score += 1
```

Storage:

```text
save_value("highscore", 10)
load_value("highscore")
```

Radio:

```text
radio_send({"x": player.x})
radio_on_message(handler)
```

Hardware:

```text
module_read("temperature")
led_set(0, "red")
servo_set("arm", 90)
```

Network/web, gated by permissions:

```text
web_page("status", html)
web_value("score", score)
```

AI helper, never raw chat by default:

```text
ask_helper("explain_error", error_id, context)
```

### 7.4 Language strategy

There are three layers:

1. **Cards Mode** — youngest children, no syntax
2. **Blocks Mode** — structured programming
3. **Text Mode** — real code

Text runtime options:

#### Lua-style runtime

Pros:

- small
- embeddable
- game-friendly
- easy C bindings
- easier sandboxing
- historically common in games

Cons:

- less familiar to schools/parents than Python
- syntax not as directly educationally mainstream

#### MicroPython runtime

Pros:

- Python familiarity
- good STEM story
- real `.py` files
- strong educational association

Cons:

- heavier
- memory/performance risk
- sandboxing and integration can be trickier
- full MicroPython environment may conflict with controlled app runtime

Conclusion:

> Design the KidCode API so either language can bind to it. Pick one runtime for the first shippable loop.

Recommended first implementation path:

- build KidCode-8 runtime in C/C++ over ESP-IDF
- implement Cards/Blocks as data that calls KidCode APIs
- add Lua-style scripting first if speed/simplicity matters
- add MicroPython/Python Lab if/when memory and runtime stability are proven

Alternative acceptable path:

- use MicroPython first if the goal is educational legitimacy over engine performance
- still hide raw MicroPython complexity behind KidCode APIs

### 7.5 Code editor scope

Realistic editor:

- line-based text editing
- syntax coloring
- autocomplete snippets
- friendly errors
- simple docs panel
- run/stop key support
- save/load files
- cursor movement via keyboard/D-pad/touch

Not realistic:

- VS Code
- full IntelliSense
- package manager
- on-device C/C++ compiler
- complex refactoring
- multiple arbitrary processes

### 7.6 Friendly error system

Errors should be teaching moments.

Example:

```text
I got stuck on line 8.

You wrote:
  player.x = player.x +

I think something is missing after the + sign.
Try adding a number, like:
  player.x = player.x + 1

[Show me] [Ask Helper] [Go to line]
```

Error UI should support:

- child explanation
- line highlight
- suggested fix
- “Ask Helper” button if AI enabled
- no scary stack dumps in child mode
- advanced details hidden behind parent/hacker toggle

---

## 8. User interface and themes

### 8.1 Shell home

Default launcher:

```text
MAKE        PLAY        DRAW
SHARE       MULTIPLAYER PLUG-INS
HELP        PARENT      SETTINGS
```

Younger mode should use icons and very little text.

### 8.2 Theme system

Themes are skins over the same shell and app model.

#### Simple Launcher

Best default for ages 4–6.

- big icons
- no overlapping windows
- guided flows
- fewer settings
- safe by default

#### Classic Desktop

For nostalgia and “real computer” feeling.

Features:

- wallpaper
- desktop icons
- taskbar/status bar
- pointer cursor
- small managed windows
- file/project browser
- Paint
- Code
- Console
- Refresh Desktop

Important:

- windows are managed panels, not arbitrary OS windows
- only one or a few apps active at once
- child can always press Home/Stop

#### Fantasy Console

For PICO-8-like identity.

- cartridges/projects
- Draw / Code / Map / Sound / Run tabs
- tiny canvas preview
- palette
- project gallery

#### Advanced Hacker Mode

For older kids/parents.

- file browser
- console/logs
- runtime selection
- hardware modules
- network settings
- local web server
- AI endpoint settings
- diagnostics

### 8.3 Window model

A realistic ESP32 window model:

- fixed max number of windows/panels
- no arbitrary app processes
- no heavy compositing
- rectangular LVGL panels
- simple focus model
- close/minimize optional
- Run window owns canvas

Example:

```text
[Code Editor] [Sprite Editor] [Run Preview]
```

or in Classic Desktop:

```text
Desktop
  ├── Paint window
  ├── Code window
  └── Game window
```

### 8.4 Refresh Desktop feature

Include it as delightful nostalgia.

Behavior:

- right-click/long-press desktop → Refresh
- icons wiggle/reload
- maybe a tiny sound
- no real technical need

This is cheap, fun, and emotionally on-brand.

---

## 9. Product features

### 9.1 Game Lab

Main product feature.

Features:

- make new game
- choose template
- draw sprite
- add behavior
- edit code/blocks
- Run instantly
- simple collisions
- score/lives
- levels/maps
- local multiplayer templates

Templates:

- collect the stars
- maze runner
- Pong
- platform jumper
- dodge game
- pet simulator
- drawing toy
- two-player race

### 9.2 Draw Lab

Features:

- sprite editor
- tile editor
- background editor
- animation frames
- palette picker
- fill/erase/mirror
- D-pad pixel cursor
- touch drawing
- preview animation
- share sprite

### 9.3 App Lab

For simple non-game apps:

- calculator
- notes
- music beeper
- timer
- weather station with module
- pet app
- quiz app
- tiny database/list app

App Lab uses the same canvas/UI APIs but presents templates differently.

### 9.4 Web Lab

Constrained local web projects.

Features:

- start local web page
- view from parent phone/tablet/computer
- show score/sensor/project info
- edit simple page template
- no internet publishing by default

Example projects:

- “My game page”
- “Room temperature dashboard”
- “Family quiz”
- “Pet status page”

### 9.5 Hardware Lab

Features:

- module detection
- sensor readings
- LED control
- servo/motor examples
- simple circuit explanations
- safety-first API

Example projects:

- distance-controlled sprite
- LED rainbow
- temperature graph
- clap-to-jump game
- servo robot face

### 9.6 Network Lab

Network is a platform capability, but Network Lab teaches it explicitly.

Features:

- send message to nearby device
- host/join game
- share high score
- pair devices with icon code
- local web server demo
- advanced Wi-Fi setup

### 9.7 Music/Sound Lab

Start small:

- beep editor
- simple sound effects
- tiny sequencer later
- attach sounds to sprites/actions

### 9.8 Lessons / Quest system

Offline lessons are important.

Structure:

- short quests
- make something in 5–15 minutes
- child earns project badges, not manipulative rewards
- no ads, no dark patterns

Example quests:

- make a cat move
- add a coin
- make a maze
- make two-player Pong
- use a distance sensor
- make a web page for your game

### 9.9 Parent Mode

Parent Mode should be protected by simple parent gate.

Features:

- age mode
- Wi-Fi setup
- AI on/off
- cloud/local AI endpoint
- sharing permissions
- export/backup projects
- storage management
- screen time/session settings optional
- view project summaries
- update firmware

### 9.10 Project gallery

The gallery should feel like a cartridge shelf.

Each project card:

- title
- preview image
- type: game/app/art/web/hardware
- last edited
- age/mode
- share status
- duplicate/delete/export

---

## 10. Sharing and multiplayer

### 10.1 Local sharing

Use ESP-NOW where available.

Share types:

- sprite
- project
- high score
- game invite
- multiplayer session

Pairing UX:

```text
Device A: Share Project
Device B: Receive
Both show: ⭐ 🐱 🚀 🍓
Press OK on both.
```

### 10.2 Protocol sketch

Project transfer:

```text
announce
pair confirm
metadata
chunk 1..N
ACK/retry
checksum
install as received project
```

Design goals:

- robust to dropped packets
- progress bar
- cancel button
- no account
- no internet
- parent setting can restrict receiving

### 10.3 Multiplayer model

Start with host-authoritative games.

```text
Host device:
  runs game state
  receives player inputs
  broadcasts snapshots

Client device:
  sends input
  displays local/interpolated state
```

First demos:

- two-player Pong
- maze race
- turn-based battle
- co-op drawing

Avoid:

- large real-time worlds
- high player counts
- internet multiplayer

---

## 11. AI helper

### 11.1 Principle

> AI helps the child make. It should not become the toy, the friend, or the teacher-parent replacement.

### 11.2 Allowed child-facing AI tools

- Explain this error
- Give me one small hint
- Explain what my code does
- Make this easier
- Suggest a challenge
- Clean up my sprite
- Turn this drawing into 16-color pixel art
- Make a background idea
- Write instructions for my game
- Summarize my project for parent

Avoid:

- open-ended child chat
- emotional companion behavior
- unrestricted image generation
- internet search/chat for kids by default
- collecting personal information

### 11.3 Gateway architecture

```text
KidCode Console
  ↓ local Wi-Fi request
KidCode Gateway
  ↓ provider adapter
Cloud LLM / local LLM / custom endpoint
  ↓
Safety filters + JSON schema validation
  ↓
KidCode-native response
```

Gateway responsibilities:

- API keys
- parent auth
- provider switching
- rate limits
- safety filters
- schema validation
- local/private endpoint support
- logging visible to parent
- data minimization

### 11.4 AI modes

| Mode | Description |
|---|---|
| Off | no AI, fully local core device |
| Cloud helper | easiest parent setup |
| Parent local AI | local server on home PC/NAS |
| Custom endpoint | OpenAI-compatible advanced setup |

Local endpoint example:

```text
Base URL: http://192.168.1.50:11434/v1
Model: parent-selected
API key: optional
```

### 11.5 AI asset format

AI art should return constrained assets, not arbitrary images.

Example:

```json
{
  "type": "sprite",
  "name": "happy robot",
  "w": 16,
  "h": 16,
  "palette": "kidcode16",
  "pixels": "encoded-pixel-data"
}
```

Then the child edits it locally.

---

## 12. Privacy and safety

### 12.1 Local-first defaults

Works offline:

- drawing
- coding
- playing
- saving/loading
- local project gallery
- ESP-NOW sharing
- local multiplayer
- offline docs
- lessons

Cloud optional:

- AI helper
- firmware updates
- optional parent backup/sync later

### 12.2 Data principles

- no child real name required
- no child account required for local use
- no ads
- no behavioral advertising
- no raw open internet in child mode
- no voice/photo upload by default
- minimal telemetry
- parent controls Wi-Fi and cloud
- local/private AI option
- export/delete projects easily

### 12.3 Permission model

Project permissions:

- graphics
- input
- sound
- local storage
- radio local share
- sensor module
- actuator module
- local web server
- AI helper
- Wi-Fi internet, parent-only/advanced

A project should not silently gain network or AI access.

---

## 13. Implementation architecture for Codex

### 13.1 Repository structure

Suggested repo:

```text
kidcode-console/
  firmware/
    CMakeLists.txt
    main/
      app_main.cpp
      kc_kernel/
      kc_ui/
      kc_runtime/
      kc_canvas/
      kc_input/
      kc_storage/
      kc_audio/
      kc_radio/
      kc_modules/
      kc_ai_client/
    components/
      lvgl/
      display_driver/
      keyboard_driver/
      scripting_runtime/
  tools/
    asset_packer/
    project_packer/
    simulator/
  gateway/
    server/
    providers/
    safety/
    schemas/
  docs/
  examples/
    tiny_runner/
    pong_multiplayer/
    sprite_painter/
```

### 13.2 Core firmware modules

#### kc_kernel

- boot
- app lifecycle
- mode/theme registry
- runtime start/stop
- panic/crash handling
- permission checks

#### kc_input

- keyboard scanning/input mapping
- D-pad/buttons
- touch/pointer
- normalized events

#### kc_canvas

- low-resolution frame buffer
- palette
- scaling to LVGL/display
- drawing primitives
- sprite blit
- text rendering

#### kc_storage

- mount flash/SD
- project CRUD
- manifest parsing
- safe paths
- import/export bundles

#### kc_runtime

- Cards/Blocks interpreter
- scripting runtime integration
- API bindings
- step budget/watchdog
- error translation

#### kc_ui

- LVGL shell
- launcher
- project gallery
- editor screens
- themes
- dialogs
- window/panel manager

#### kc_radio

- ESP-NOW pairing
- share protocol
- multiplayer protocol
- retries/checksums

#### kc_audio

- beeps
- SFX
- small music engine later

#### kc_modules

- module detection
- I2C registry
- safe APIs

#### kc_ai_client

- gateway discovery/config
- request builder
- schema parser
- parent permission checks

### 13.3 First firmware milestone

**KidCode Nano v0.1 on T-Deck**

Must do:

1. Boot to LVGL launcher.
2. Show project gallery.
3. Open Tiny Runner demo.
4. Render 128×128 canvas scaled to screen.
5. Move sprite using keyboard/D-pad/trackball where available.
6. Save/load one project.
7. Press Run to restart.
8. Press Home/Stop to return to launcher.
9. Show friendly error screen for a forced script/runtime error.

Nice next:

10. Basic sprite editor.
11. ESP-NOW two-player Pong.
12. Project share skeleton.
13. AI gateway “explain error” proof.

### 13.4 Simulator

Build a PC simulator early if possible.

Why:

- faster Codex development loop
- test UI without flashing ESP constantly
- run unit tests for runtime/project format
- easier screenshots/videos

Simulator can use:

- SDL or LVGL PC simulator
- same KidCode runtime/core where possible
- fake input/events
- local filesystem projects

Important:

> Keep firmware modules portable enough that core runtime and project logic can run on PC.

---

## 14. MVP specification

### 14.1 MVP hardware

For a compelling demo:

- T-Deck or 7-inch ESP32 board
- keyboard input
- screen
- SD/flash storage
- D-pad/buttons if available
- Wi-Fi/ESP-NOW

### 14.2 MVP software

Must include:

- launcher
- project gallery
- one editable demo game
- tiny code/action editor
- sprite/canvas rendering
- Run/Stop loop
- save/load
- friendly errors
- local share or multiplayer proof

### 14.3 MVP demo project: Tiny Runner

Features:

- player sprite
- move left/right/up/down
- collectible
- score
- obstacle
- edit speed/color/name
- press Run to restart
- save project

Expansion:

- add second player via ESP-NOW
- share high score
- AI explain error
- draw custom player sprite

### 14.4 MVP success test

A child should be able to:

1. start a project
2. change something visible
3. press Run
4. see the change immediately
5. save it
6. show it to someone

A parent should be able to understand in 30 seconds:

> “This is not another tablet. It is a little computer for making games and learning code.”

---

## 15. Product roadmap

### Phase 0 — Software proof on T-Deck

- LVGL launcher
- input abstraction
- canvas runtime
- Tiny Runner
- save/load
- basic editor
- friendly error screen
- ESP-NOW proof

### Phase 1 — 7-inch UI prototype

- port shell to 7-inch board
- scale canvas
- layout editor/gallery/drawing tools
- USB or matrix keyboard
- test touch drawing
- test battery
- test performance

### Phase 2 — Integrated prototype

- custom keyboard
- D-pad/ABXY
- Run/Save/Home/Share keys
- 3D shell
- speaker
- battery
- microSD
- expansion connector
- local sharing polished
- first AI gateway

### Phase 3 — Family validation

Test with:

- user’s children
- 3–5 other families
- maybe coding club/workshop

Measure:

- do children return to projects?
- do they understand Run/Edit/Fix?
- is the keyboard usable?
- is drawing fun?
- is local sharing magical?
- do parents understand the value?
- does AI feel useful or scary?

### Phase 4 — Productization

Only after validation:

- manufacturing DFM
- compliance review
- battery safety
- keyboard sourcing
- shell tooling
- accessory/module ecosystem
- firmware updater
- parent gateway/cloud decision

---

## 16. Cost and positioning

### 16.1 Cost direction

Prototype rough ranges:

| Component | Rough range |
|---|---:|
| 7-inch ESP32 display board | $35–100 |
| keyboard/buttons | $10–35 |
| battery/power | $6–18 |
| audio/cables/small parts | $5–15 |
| shell | $15–40 |
| total prototype | ~$75–200 |

Commercial target:

| Tier | Retail target |
|---|---:|
| tiny Cardputer-like | $49–99 |
| 7-inch ESP32 creative console | $99–149 ideal |
| nicer P4 7-inch version | $149–199 |
| Linux/Android kid computer | $199–399 |

Conclusion:

> Keep v1 scoped tightly enough to stay in the $99–149 dream range. If it drifts above $199, it must justify why it is not competing with cheap tablets/Chromebooks.

### 16.2 Moat

The hardware alone is not the moat.

Moat is:

- kid-first IDE
- instant Run loop
- fantasy-console runtime
- local sharing
- ESP-NOW multiplayer
- sprite/tile tools
- safe modules
- local-first privacy
- optional guardrailed AI
- emotional “my first real computer” shell/themes

---

## 17. Open decisions

### 17.1 Must decide soon

1. Primary v0 scripting runtime: Lua-style or MicroPython-first?
2. Exact first dev board after T-Deck: ESP32-S3 7-inch or ESP32-P4 board?
3. Project file format: folder vs packed bundle?
4. First UI theme: Simple Launcher or Fantasy Console?
5. Editor first: action/cards editor or text editor?
6. Is resistive touch worth sourcing for prototype?

### 17.2 Can decide later

1. final product name
2. LoRa built-in vs module-only
3. cloud AI business model
4. subscription vs no subscription
5. exact keyboard mechanism
6. production SoC
7. parent phone app vs local web gateway only
8. official lesson marketplace/library

---

## 18. Recommended immediate decisions

To avoid stalling, lock this for the next implementation pass:

1. **Name internally:** KidCode Console hardware, KidCode OS shell, KidCode-8 runtime, KidCode Studio IDE.
2. **Prototype hardware:** LilyGO T-Deck first.
3. **Firmware base:** ESP-IDF + FreeRTOS.
4. **GUI:** LVGL.
5. **Runtime output:** controlled KidCode canvas, not raw LVGL.
6. **First project:** Tiny Runner.
7. **First language path:** implement runtime API and action/cards first; bind one scripting runtime next.
8. **Storage:** project folders on SD/flash with manifest.
9. **Sharing:** ESP-NOW proof after save/load works.
10. **AI:** gateway proof only after core Run loop works.

---

## 19. Codex task breakdown

### Task group A — Project skeleton

- create ESP-IDF project
- integrate LVGL for target board
- create module folders
- boot to blank launcher

### Task group B — Input/canvas

- implement input abstraction
- implement 128×128 canvas
- scale canvas to display
- draw test sprite

### Task group C — Tiny Runner

- create sprite struct
- handle movement
- score/collectible
- Run/Stop lifecycle
- demo project manifest

### Task group D — Storage

- mount SD/flash
- save project manifest
- load project
- gallery list

### Task group E — Editor

- simple property editor first
- change sprite color/speed/name
- Run shows changes

### Task group F — Runtime

- define KidCode API
- implement cards/action interpreter
- add scripting runtime binding later

### Task group G — Sharing

- ESP-NOW init
- peer pairing
- send/receive small payload
- high score share
- project chunk protocol later

### Task group H — AI gateway

- define request schemas
- local gateway server prototype
- device sends explain-error request
- gateway returns strict JSON response

---

## 20. Final product vision

The final product should feel like this:

A child opens a chunky little computer with a real keyboard. They draw a robot, press Run, and it moves. They change one number and the robot moves faster. They press Share and their sibling receives the game. They plug in a distance sensor and control the robot with their hand. When something breaks, the Helper explains the error in simple words. A parent can keep everything offline or connect it to a local AI server.

That is the unique product.

Not another tablet.  
Not a toy laptop.  
Not a generic dev board.  
A first programmable creative computer.
