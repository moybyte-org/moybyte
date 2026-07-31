# moybyte

[![CI](https://github.com/moybyte-org/moybyte/actions/workflows/ci.yml/badge.svg)](https://github.com/moybyte-org/moybyte/actions/workflows/ci.yml)

**An operating system that turns an ESP32 board into a small general-purpose
computer — one you can also write software on, on the board itself. The software
is cartridges: games, wallpapers, tools, whatever you make. Open any of them,
change it, run it, with no host computer in the loop. It boots on two
off-the-shelf boards today; the same source tree is also a PC simulator and a
browser build.**

Its closest relatives are TIC-80 and Picotron: a fantasy console whose editors
are part of the machine. The difference is that here the console *is* the
system — the launcher, the window manager and the editor (config / blocks / code
/ sprites / tilemap / scene / music) are processes on the same window manager
that runs your cart, not a separate mode you leave the machine to enter. A cart
is a folder: a manifest, a Python or Lua script, an indexed sprite sheet, a
tilemap, a sound bank. No build step, no per-device binary, no host toolchain.

It is approachable enough for a ten-year-old (that is what the block editor and
the seed carts are for) without being *only* that: underneath is a MicroPython
firmware with native C kernels, a Lua VM, OTA updates and a windowing shell, and
you are meant to be able to read and change all of it.

Carts draw in 64 indexed colours on a 320×240 surface, the same on every target.
The shell around them is not fixed — it reflows from that handheld screen to a 7″
1024×600 desktop, from one implementation.

This repo is the **reference implementation of [moy core 0.1](https://github.com/moybyte-org/moy-spec)**,
the public spec for that cart format and its verb table.

<p align="center">
  <img src="docs/media/desktop/paint.gif"
       alt="On the windowed desktop: drawing a smile on the pet sprite in the editor, and the running game window beside it wearing the change">
  <br><em>Paint a smile on the sprite — the game window beside it is already wearing it.</em>
</p>

| ![The code editor as a window on the desktop](docs/media/desktop/code.gif) | ![Building a block program next to the scene it drives](docs/media/desktop/blocks.gif) |
|:--:|:--:|
| The code editor is a tab in the same console | Blocks compile to the same Python, and *graduate* to it |

## What it runs on

| target | what it is |
|---|---|
| **PC simulator** | `runtime/` — pure Python, no device. The host reference and the fast dev loop. |
| **LilyGO T-Deck Plus** (ESP32-S3) | MicroPython firmware, native 320×240, keyboard + trackball + touch, carts on SD, OTA updates. |
| **Waveshare ESP32-P4 7B** | 1024×600 MIPI-DSI. Same console, second presentation tier: a windowed desktop with draggable app windows. |
| **Browser** | MicroPython compiled to WebAssembly (`firmware/web_runner/`) — the console *is* the page, no server. |

Host and device are **one codebase**, not a port. `runtime/` is canonical; each
firmware build stages copies of those modules and freezes them, so the simulator
is not a second implementation that can drift from the firmware.

The GIFs above are the desktop tier at 1024×600. Below is the same console, from
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
system; their code still lives in the shell rather than in an editable cart,
which is what [#181](https://github.com/moybyte-org/moybyte/issues/181) is for.

**Two cart languages** — Python and Lua, one verb table, valid verbatim in both.
On device, Lua carts run on a vendored Lua 5.4 VM whose heap lives *outside*
MicroPython's GC and is freed wholesale at exit. Drawing, input, sprites,
tilemaps, layers, audio, scenes, spreadsheets and documents, persistent memory —
[the full table](docs/moy_cart_api.md) is about 55 verbs and no imports.

**Graphics** — an indexed 64-colour palette end to end, every draw verb landing
in a native C kernel on device (`moy_gfx`). The 7″ board composites the game
through the SoC's hardware PPA, with the DMA overlapping the next frame's input
poll; scrolling shifts retained pixels instead of repainting them; sprite
batching collapses N calls into one. An optional frameskip runs logic at the full
rate and motion at 30 Hz.

**Sound** — a C mixer (`moy_audio`) on both boards and in the browser, fed by a
tracker-style sound bank. PICO-8 imports carry eight waveforms, the effect
column, four-channel patterns and SFX loop ranges.

**Cartridges are folders** — a manifest, a script, an indexed sheet, a tilemap, a
sound bank. No build step and no per-device binary: copy a folder onto the SD
card and it is on the launcher. Built-in carts re-seed by version and keep the
your saves and tuning across an update.

**Wireless** — WiFi setup lives in Settings, so it works while a game runs.
Firmware updates go over the air on two channels, stable and beta, into an
inactive OTA slot with bootloader rollback; that whole path was confirmed on a
T-Deck — download, install, boot the new slot, roll back. It has not been
exercised in a while, so treat it as "worked when last tested". The device can
also serve the running console to a browser on the same network as draw commands
rather than pixels, which ran on that board too — but **that transport is broken
on the T-Deck right now**
([#182](https://github.com/moybyte-org/moybyte/issues/182)). The radio is fine:
it associates, takes a DHCP lease and serves the page; a missing re-export makes
the asset request fail, and the page never opens its live channel.

**Four rendering backends, one contract** — host, two boards, and a browser page
that draws the console's commands itself. That contract is written down
([`docs/surface_model_v1.md`](docs/surface_model_v1.md)), including its graveyard
of approaches that were built, measured and reverted.

**Tests** — ~1900, all headless. Golden-frame tests pin the host renderer and a
canvas-parity suite holds the device backend to it; the firmware tests read the
frozen module tree rather than executing it. The P4 is driven over its live serial
console by a pytest suite that taps and swipes the real UI, and the browser build
has a screenshot harness that replays real frames through the real page code.

## Try it in 60 seconds

```bash
make setup                                       # venv + editable install (dev + sim)
make test                                        # pytest
.venv/bin/python tools/simulate_desktop.py       # boots the launcher
```

Arrows move, `Enter` runs, `M` menu, `H` home, `Esc` quits; the mouse is the
touchscreen. Tap **Make ✏️** to open the editor on any cart, including the ones
you just played.

```bash
# the P4's windowed desktop tier, on your PC
.venv/bin/python tools/simulate_desktop.py --size 1024x600 --windowed

# skip the launcher, run one cart
.venv/bin/python tools/simulate_desktop.py --cart system_carts/star_catcher.moy

# the whole console streamed to a browser as draw commands (no wasm build)
.venv/bin/python tools/web_console.py --size 1024x600 --windowed

# headless tour -> animated GIF (this is how the GIFs above are made)
.venv/bin/python tools/simulate_desktop.py --demo --gif demo.gif
```

No display? Every test runs headless, and `--gif`/`--script` drive the real
console without one.

**In the browser, for real.** `firmware/web_runner/build.sh` compiles the same
console to WebAssembly (it fetches emsdk itself; first build is slow) and emits a
static `dist/` — serve it with `firmware/web_runner/serve.py` and the whole
console, cart roster included, runs in a tab with no server behind it. That build
is also what the spec repo vendors as its player, so **you can try a cart without
cloning anything**: `moy.py run` over there is one command and no dependencies.

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
this console: [**moybyte-org/moy-spec**](https://github.com/moybyte-org/moy-spec)
(MIT). It ships `SPEC.md`, a browser player built from this repo's web runner,
a `moy` CLI (`new` / `run` / `export` / `port`), and a PICO-8 converter — a p8
cart converts art, map, sound and code under a compat shim.

The spec is deliberately narrow: it describes what a *game* touches. Everything
else in this repo — the shell, the editors, the window manager, the app API — is
above core, and consoles are expected to differ there.

## The hardware, honestly

Both boards are real and both boot to the console — but both are off-the-shelf
dev boards; bespoke hardware is roadmap, not shipped. The T-Deck Plus is a
keyboard handheld; the P4 board is a 7″ desktop. What's honest about the state:

- **It plays.** The seed carts run at playable frame rates on both boards, with
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
MOYBYTE_SKIP_VFS_BOOT=1 make firmware-build-lilygo-micropython   # needs ESP-IDF 5.5
make firmware-flash-lilygo-micropython PORT=/dev/ttyACM0
make firmware-build-p4 && make firmware-flash-p4 PORT=/dev/ttyACM0
```

Each firmware directory has its own README recording the hardware-learned
constraints (shared SPI bus rules, DSI/PSRAM timing, the keyboard's two modes) —
**read them before touching that board.** They exist because each line in them
cost a debugging session.

## Where things live

| path | |
|---|---|
| `runtime/` | the console: kernel, WMs, player, editor app, every surface. **[Its README](runtime/README.md) is a per-file map.** |
| `system_carts/` | the seed cartridges — games, wallpapers, and the system apps (Paint, Files, Sheets, Writer, Storybook, Calc) |
| `firmware/lilygo_t_deck_plus_micropython/` | the ESP32-S3 port + the native C modules (`moy_gfx`, `moy_lua`, `moy_audio`, `moy_sd`) |
| `firmware/esp32_p4_wifi6_touch_lcd_7b/` | the ESP32-P4 port (mainline MicroPython + a vendored DSI driver) |
| `firmware/web_runner/` | the MicroPython-WASM build; `build.sh` fetches emsdk itself |
| `tools/` | simulator, web console, GIF recorder, p8 importers, on-glass test drivers |
| `docs/` | cart API, shell UX, visual identity, architecture and design docs |
| **`CLAUDE.md`** | **the best single map of this repo.** Written for AI tools, but it is the orientation doc humans should read first. |

Design doc: [`moybyte_Console_Plan_v0_5.md`](moybyte_Console_Plan_v0_5.md).
Shell reference: [`docs/shell_ux_v1.md`](docs/shell_ux_v1.md).

## Contributing

Read [`CONTRIBUTING.md`](CONTRIBUTING.md) — features want an issue first, and
every commit needs a DCO sign-off (`git commit -s`). Then read `CLAUDE.md`.

The one rule that trips people up: **host == device.** A change to the drawing
API must land in both backends (`runtime/canvas.py` and the device modules) with
an identical API, or the "one cart, every tier" contract breaks.

## License

Everything you'd do as a person is free: run the simulator, flash the firmware on
your own board, modify it, teach with it, make and sell your own carts. Selling
hardware (or a commercial product) built on the console requires a commercial
license, and that restriction expires per release two years after publication.

Details and the exact split: [`LICENSE.md`](LICENSE.md) — the console and
firmware are
[FSL-1.1-MIT](LICENSES/FSL-1.1-MIT.md) (source-available, becomes MIT after two
years). The `.moy` cart format and API are an open specification, and carts you
author are yours. What each licence lets you do, in a table:
[`docs/licensing_v1.md`](docs/licensing_v1.md).

---

*The kid- and parent-facing side of this project lives at
[moybyte.com](https://moybyte.com). This README is for people reading the source.*
