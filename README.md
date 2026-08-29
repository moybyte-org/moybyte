# moybyte

[![CI](https://github.com/moybyte-org/moybyte/actions/workflows/ci.yml/badge.svg)](https://github.com/moybyte-org/moybyte/actions/workflows/ci.yml)

**An operating system that turns an ESP32 board into a small general-purpose
computer — one you can also write software on, on the board itself. The software
is cartridges: games, wallpapers, tools, whatever you make. Open any of them,
change it, run it, with no host computer in the loop. It boots on three
off-the-shelf boards today; the same source tree is also a PC simulator and a
browser build.**

Its closest relatives are TIC-80 and Picotron: a fantasy console whose editors
are part of the machine. The difference is that here it goes all the way down to
an operating system — the launcher, the editor (config / blocks / code / sprites
/ tilemap / scene / music) and your running cart are all processes on one window
manager, not a separate mode you leave the machine to enter. A cart
is a folder: a manifest, a Python or Lua script, an indexed sprite sheet, a
tilemap, a sound bank. No build step, no per-device binary, no host toolchain.

It is approachable enough for a ten-year-old (that is what the block editor and
the seed carts are for) without being *only* that: underneath is a MicroPython
firmware with native C kernels, a Lua VM, OTA updates and a windowing shell, and
you are meant to be able to read and change all of it.

Carts draw in 64 indexed colours on a 320×240 surface, the same on every target.
The shell around them is not fixed — it reflows from that handheld screen to a 7″
1024×600 desktop, all from one implementation.

This repo is the **reference implementation of [moy core 0.2](https://github.com/moybyte-org/moy-spec)**,
the public spec for that cart format and its verb table.

<p align="center">
  <img src="docs/media/desktop/paint.gif"
       alt="On the windowed desktop: drawing a smile on the pet sprite in the editor, and the running game window beside it wearing the change">
  <br><em>Paint a smile on the sprite — the game window beside it is already wearing it.</em>
</p>

| ![The code editor as a window on the desktop](docs/media/desktop/code.gif) | ![Building a block program next to the scene it drives](docs/media/desktop/blocks.gif) |
|:--:|:--:|
| The code editor is a tab in the same system | Blocks compile to the same Python, and *graduate* to it |

## What it runs on

| target | what it is |
|---|---|
| **PC simulator** | `tools/simulate_desktop.py` over the same `runtime/` the boards run. The host reference and the fast dev loop. Needs a C compiler — the raster is compiled libmoy, the same one the boards use. |
| **LilyGO T-Deck Plus** (ESP32-S3) | MicroPython firmware, native 320×240, keyboard + trackball + touch, carts on SD, OTA updates. |
| **Waveshare ESP32-P4 7B** | 1024×600 MIPI-DSI. Same system, second presentation tier: a windowed desktop with draggable app windows. |
| **Guition JC3248W535** (ESP32-S3) | the ~$15 3.5″ smart display: a QSPI AXS15231B panel, touch-only, landscape 480×320, carts on the TF card when one is in the slot. |
| **Browser** | MicroPython compiled to WebAssembly (`firmware/web_runner/`) — the OS *is* the page, no server. |

Host and device are **one codebase**, not a port. `runtime/` is canonical; each
firmware build stages copies of those modules and freezes them, so the simulator
is not a second implementation that can drift from the firmware.

The GIFs above are the desktop tier at 1024×600. Below is the same system, from
the same modules, on the handheld tier at its native 320×240 — the layout
reflows to the smaller screen:

| ![The paint editor at native 320x240](docs/media/paint.gif) | ![The code editor at native 320x240](docs/media/code.gif) |
|:--:|:--:|
| The same paint session, fullscreen at 320×240 | …and the same code editor |

## What's in it

Everything below exists and runs today. Where something is unverified or rough,
it says so.

**The shell** — a launcher, a Player (`run(cart)` plays until exit and returns to
whoever called it), and an Editor: all ordinary processes over a window manager.
Two presentation tiers from one implementation — a fullscreen back-stack on the
handheld, and a windowed desktop on the 7″ board with draggable, resizable
windows and a taskbar, where a playtest keeps running beside the editor you are
typing in. Panel themes in dark and light, live or static wallpapers, and a
per-window responsive layout: every surface reflows from a phone panel to a 7″
desk, and only a *running cart* is fixed at 320×240.

**The editors, on the device itself** — seven tabs over one project: Config,
Blocks, Code, Sprites, Map, Scene, Music. Blocks compile to the same Python and
*graduate* to it when you edit the code directly. There is no save button and no
dirty star: commits ride a typing-idle autosave and every exit path, backed by a
per-project journal, so undo/redo walks fine-grained edits and then whole
commits, scoped to the tab you are in. A crash drops you into the code on the
offending line.

**Apps** — Paint, Files, Writer, Sheets, Storybook, Calc, Settings, Appearance,
WiFi setup. Drawings, documents and tables live in a shared file layer that carts
can read back. They sit on the launcher as carts and behave like the rest of the
system. An app can now BE a cartridge — declared by manifest permissions, with
no shell module, no registration and no reflash
([`docs/app_api_v1.md`](docs/app_api_v1.md); `system_carts/notes.moy` is one in
200 lines). These particular apps still live in the shell, deliberately: they
are big, and the capability is there for what *you* write.

**Two cart languages** — Python and Lua, one verb table, valid verbatim in both.
On device, Lua carts run on a vendored Lua 5.4 VM whose heap lives *outside*
MicroPython's GC and is freed wholesale at exit. Drawing, input, sprites,
tilemaps, layers, audio, scenes, spreadsheets and documents, persistent memory —
[the full table](docs/moy_cart_api.md) is about 60 verbs and no imports.

**Graphics** — an indexed 64-colour palette end to end, every draw verb landing
in a native C kernel on device (`moy_gfx`). The 7″ board composites the game
through the SoC's hardware PPA, with the DMA overlapping the next frame's input
poll; scrolling shifts retained pixels instead of repainting them; sprite
batching collapses N calls into one. An optional frameskip runs logic at the full
rate and motion at 30 Hz.

**Sound** — a C mixer (`moy_audio`) on the boards and in the browser, fed by a
tracker-style sound bank. PICO-8 imports carry eight waveforms, the effect
column, four-channel patterns and SFX loop ranges.

**Cartridges are folders** — a manifest, a script, an indexed sheet, a tilemap, a
sound bank. No build step and no per-device binary: copy a folder onto the SD
card and it is on the launcher. Built-in carts re-seed by version and keep the
your saves and tuning across an update.

**Wireless** — WiFi setup lives in Settings, so it works while a game runs.
Firmware updates go over the air on two signed channels, stable and beta, into
an inactive OTA slot with bootloader rollback — the whole chain (real WiFi,
signature check on device, streamed install, boot the new slot, rollback
self-heal) has run on the glass of the T-Deck and the P4. Each board also serves
the browser console below over its own WiFi: the wasm bundle is baked into the
firmware image, so a phone on the same network gets the full console from the
device itself — reading that board's cartridges and writing every change back to
it, behind the pairing pin the board puts on screen.

**Five rendering backends, one contract** — host, three boards, and a browser
build that rasterizes in WebAssembly. That contract is written down
([`docs/surface_model_v1.md`](docs/surface_model_v1.md)), including its graveyard
of approaches that were built, measured and reverted.

**Tests** — several thousand, all headless (the CI badge above is the live
count). Golden-frame tests pin the host renderer and a canvas-parity suite holds
the device backend to it; the firmware tests read the frozen module tree rather
than executing it. Each of the three boards is driven over its live serial
console by a pytest suite that taps and swipes the real UI, and the browser build
has a screenshot harness that boots the real wasm console and decodes the same
framebuffer the page blits.

## Try it in 60 seconds

```bash
make setup                                       # venv + editable install (dev, sim)
make test                                        # pytest
.venv/bin/python tools/simulate_desktop.py       # boots the launcher
```

Arrows move, `Enter` runs, `M` menu, `H` home, `Esc` quits; the mouse is the
touchscreen. Tap **Make ✏️** to open the editor on any cart, including the ones
you just played.

**On Windows**, the Makefile doesn't apply (it is POSIX), so `make setup` is
spelled out — same three steps, same extras:

```bat
py -3 -m venv .venv
.venv\Scripts\python -m pip install --upgrade pip setuptools
.venv\Scripts\python -m pip install -e ".[dev,sim]"

.venv\Scripts\python tools\simulate_desktop.py
```

Every command below works as written with `.venv\Scripts\python` in place of
`.venv/bin/python`. Python 3.10+, **and a C compiler — it is a requirement, not
an extra.** The host draws through the boards' own vendored libmoy, compiled on
demand and cached; there has been no Python fallback raster since 2026-08-15, so
without a compiler the simulator dies at its first draw and `make test` cannot
render. (You get a sentence saying so, not a ctypes error.) The same applies to
audio, which is silence without one, and to Lua carts, which open the "needs the
Lua runtime" panel: `cc` or `gcc` on PATH, or `$CC`. Debian/Ubuntu
`sudo apt install build-essential`, Fedora `sudo dnf install gcc`, macOS
`xcode-select --install`.

```bash
# the P4's windowed desktop tier, on your PC
.venv/bin/python tools/simulate_desktop.py --size 1024x600 --windowed

# skip the launcher, run one cart
.venv/bin/python tools/simulate_desktop.py --cart system_carts/star_catcher.moy

# ...and run YOUR cart in place, editing it between runs (see below)
.venv/bin/python tools/simulate_desktop.py --cart ~/.moybyte/projects/mine.moy

# the whole console in a browser: the wasm build (firmware/web_runner)
cd firmware/web_runner && ./build.sh && python serve.py

# headless tour -> animated GIF (this is how the GIFs above are made)
.venv/bin/python tools/simulate_desktop.py --demo --gif demo.gif
```

No display? Every test runs headless, and `--gif`/`--script` drive the real
system without one.

**`--cart` copies before it runs.** A cart from outside the cart store is copied
into it (`--save-dir`, default `~/.moybyte/projects/`) the first time it is seen,
and every later run opens *that* copy — so edits to the folder you pointed at
stop showing up. Fine for trying a cart, wrong while writing one. Keep the cart
you are working on **inside the store** and it runs in place, edits and all.
(`--save-dir` moves the store, but the system carts seed into wherever it
points, so it is a second store rather than a way to run one loose folder.)

**In the browser, for real.** `firmware/web_runner/build.sh` compiles the same
system to WebAssembly (it fetches emsdk itself; first build is slow) and emits a
static `dist/` — serve it with `firmware/web_runner/serve.py` and the whole
system, cart roster included, runs in a tab with no server behind it. Carts and
drawings made there are kept in that browser and are still on the shelf after a
reload; a `.moy` file carries one in or out. A page served by a *board* instead
edits that board's store, over the wire — where the page came from decides which,
once, and the two never mix. That build is also what the spec repo vendors as its
player, so **you can try a cart without cloning anything**: `moy run` over there
is one command and no dependencies.

## Write a cart

A cart is a folder (`manifest.json` + `main.py` + `config.json`, plus optional
sprites / tilemap / sounds). Three optional lifecycle hooks, and **no imports** —
the verbs are pre-injected globals:

```python
# a tiny cart: move a ball with the D-pad
x = y = 0

def _init():
    global x, y
    x, y = W // 2, H // 2

def _update(dt):                 # dt in seconds
    global x, y
    speed = 120 * dt
    if btn("left"):  x -= speed
    if btn("right"): x += speed

def _draw():
    cls(col("dark_blue"))
    circ(int(x), int(y), 6, col("yellow"))
    print("MOVE ME", 8, 8, col("white"))
```

The same cart in **Lua** is one manifest line away (`"runtime": "lua"` +
`main.lua`) — every verb in the API is valid verbatim in both languages, and on
device Lua carts run on a vendored Lua 5.4 VM with the cart heap outside
MicroPython's GC. `system_carts/sakura_lua.moy` is a line-by-line, pixel-identical
twin of `system_carts/sakura.moy`, pinned by a test.

- **[`docs/moy_cart_api.md`](docs/moy_cart_api.md)** — the full verb table:
  drawing, input, sprites, tilemaps, layers, audio, scenes, persistent memory.
- **`system_carts/*/`** — 30-odd real carts, from a 70-line tap game to Battle
  City. They are the worked examples, and they model the "draw less" idioms the
  docs teach.

## The spec

The cart format and verb table are a **public spec** so carts aren't hostage to
this one implementation: [**moybyte-org/moy-spec**](https://github.com/moybyte-org/moy-spec)
(MIT). It ships `SPEC.md`, a browser player built from this repo's web runner,
a `moy` CLI (`new` / `run` / `export` / `port`), and a PICO-8 converter — a p8
cart converts art, map, sound and code under a compat shim.

The spec is deliberately narrow: it describes what a *game* touches. That layer
is where the word *console* belongs — moy core specifies a virtual console, and
Moybyte is a system that contains one. Everything else in this repo — the shell,
the editors, the window manager, the app API — is above core, and consoles are
expected to differ there.

## The hardware, honestly

All three boards are real and all three boot to Moybyte — but all three are
off-the-shelf dev boards; bespoke hardware is roadmap, not shipped. The T-Deck
Plus is a keyboard handheld; the P4 board is a 7″ desktop; the Guition is a
~$15 touch-only 3.5″ display. What's honest about the state:

- **It plays.** The seed carts run at playable frame rates on the boards, with
  the whole editor suite usable on the device itself.
- **Performance is tracked in the open, not claimed.** Per-cart fps, the frame
  budget model, and every lever *including the ones that were built, measured
  and reverted* live in [issue #66](https://github.com/moybyte-org/moybyte/issues/66)
  (T-Deck) and [#58](https://github.com/moybyte-org/moybyte/issues/58) (P4);
  [`docs/perf_native_gap_v1.md`](docs/perf_native_gap_v1.md) is the strategic
  analysis of why we trail native emulators and what's left.
- **Open holes are filed, not hidden** — USB-HID keyboard and audio on the P4,
  the touch-controller stalls, the editor-tab draw cost. See the issue tracker.

Build and flash:

```bash
# each build.sh clones the MicroPython and ESP-IDF it needs into .build/ --
# you do not install a toolchain, but the first build of a board is slow.
make firmware-build-tdeck-mainline && make firmware-flash-tdeck-mainline PORT=/dev/ttyACM0
make firmware-build-p4             && make firmware-flash-p4             PORT=/dev/ttyACM0
make firmware-build-guition-s3     && make firmware-flash-guition-s3     PORT=/dev/ttyACM0
```

Without the toolchain: every build off `master` publishes to the rolling
[`firmware-latest`](https://github.com/moybyte-org/moybyte/releases/tag/firmware-latest)
release, and the project site flashes any of the three boards straight from the browser
over Web Serial (Chrome or Edge) — the same image at the same offset as the
commands above. The site serves its own copy of each image because that is the
only origin a browser may fetch firmware from; `tools/fetch_ci_firmware.py` is
how they get there.

`master` is the tested branch — it is what the site flashes and what a board
offers itself over the air. Work happens on `dev`, whose builds publish
separately to
[`firmware-beta`](https://github.com/moybyte-org/moybyte/releases/tag/firmware-beta)
for the device's opt-in BETA channel (Settings → CHANNEL). Beta images are
untested by definition; the bootloader keeps the previous one and rolls back if
a new image doesn't come up.

Each firmware directory has its own README recording the hardware-learned
constraints (shared SPI bus rules, DSI/PSRAM timing, the keyboard's two modes) —
**read them before touching that board.** They exist because each line in them
cost a debugging session.

## Where things live

| path | |
|---|---|
| `runtime/` | the system: kernel, WMs, player, editor app, every surface. **[Its README](runtime/README.md) is a per-file map.** |
| `system_carts/` | the seed cartridges — games, wallpapers, and the system apps (Paint, Files, Sheets, Writer, Storybook, Calc) |
| `firmware/lilygo_t_deck_plus_mainline/` | the ESP32-S3 (T-Deck) port; the shared native C modules live in repo-root `native/` |
| `firmware/esp32_p4_wifi6_touch_lcd_7b/` | the ESP32-P4 port (mainline MicroPython + a vendored DSI driver) |
| `firmware/guition_jc3248w535/` | the Guition 3.5″ S3 port (its own QSPI panel driver, `native/moy_axs`) |
| `firmware/seeed_xiao_esp32s3_zero/` | the Zero: a headless companion, not a console — it stores and serves a kid's carts to the browser build |
| `firmware/web_runner/` | the MicroPython-WASM build; `build.sh` fetches emsdk itself |
| `tools/` | simulator, GIF recorder, p8 importers, on-glass test drivers |
| `docs/` | cart API, shell UX, visual identity, architecture and design docs |
| **`CLAUDE.md`** | **the best single map of this repo.** Written for AI tools, but it is the orientation doc humans should read first. |

Design doc: [`moybyte_console_plan_2026-07.md`](moybyte_console_plan_2026-07.md).
Shell reference: [`docs/shell_ux_v1.md`](docs/shell_ux_v1.md).

## Contributing

Read [`CONTRIBUTING.md`](CONTRIBUTING.md) — features want an issue first, and
every commit needs a DCO sign-off (`git commit -s`). Then read `CLAUDE.md`.

The one rule that trips people up: **host == device.** A change to the drawing
API lands in the ONE canvas class every tier runs (`device_canvas.DeviceCanvas`,
built for the host by `runtime/host_canvas.py`) with
an identical API, or the "one cart, every tier" contract breaks.

## License

Everything you'd do as a person is free: run the simulator, flash the firmware on
your own board, modify it, teach with it, make and sell your own carts. Selling
hardware (or a commercial product) built on Moybyte requires a commercial
license, and that restriction expires per release two years after publication.

Details and the exact split: [`LICENSE.md`](LICENSE.md) — the system and
firmware are
[FSL-1.1-MIT](LICENSES/FSL-1.1-MIT.md) (source-available, becomes MIT after two
years). The `.moy` cart format and API are an open specification, and carts you
author are yours. What each licence lets you do, in a table:
[`docs/licensing_v1.md`](docs/licensing_v1.md).

---

*The kid- and parent-facing side of this project lives at
[moybyte.com](https://moybyte.com). This README is for people reading the source.*

*Most of the code here was written with Claude Code, directed and tested on
hardware by a human.*
