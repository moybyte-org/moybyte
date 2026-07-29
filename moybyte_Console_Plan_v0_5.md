# Moybyte Console — Plan v0.5

**Project:** Moybyte — a kid-safe creative computer where everything is an editable cartridge
**Document version:** 0.5
**Date:** 2026-07-01
**Supersedes:** `docs/history/moybyte_Console_Plan_v0_4.md` (v0.4, 2026-06-21)
**Status:** Consolidation + course-correction. v0.4 was written before the console shipped end-to-end and before the hardware direction resolved into a tier family. This document folds the as-built reality, the decisions taken since, and the forward vision into one source of truth.

> **How to read the status tags in this doc**
> **DECIDED** — a committed call; build against it.
> **BUILT** — shipping today on host and/or device.
> **EXPLORATION / OPEN** — a direction we like but have *not* committed; do not build against it as if settled.

---

## 0. Executive conclusion

v0.4 reframed the product from "ESP32 game console with an IDE" to a **kid-safe fantasy workstation where the visible computer is made of editable cartridges.** That soul is correct and we keep it. But v0.4 made five load-bearing calls that reality has since overtaken — the primary runtime, the canvas, the hardware shape, the internal architecture, and the first-MVP framing. v0.5 corrects them and states where the product is going.

The v0.5 shape in one paragraph:

> **Moybyte is a kid's first *creative* computer: a device where the desktop, tools, games, and art are all cartridges a child can open, edit, remix, and share. It ships as one identical console running on a three-tier hardware family — Zero, Player, and One — that differ only in their I/O backend, so a cartridge authored once runs on every tier. The runtime is MicroPython-first. The product's purpose is to teach a child to *make* a computer, not merely to *use* one.**

The single most important positioning decision (see §2):

> **DECIDED — Moybyte is "learn to make," not "learn to use."** The **One** is the make-workstation and the identity anchor. **Zero** and **Player** are reach and play *around* that core — the funnel that pulls a kid from playing carts to making them. "Learning computer use" is the Trojan horse, not the reason this computer exists.

Picotron and TIC-80 remain the north-star references for *grammar* (everything-is-a-cartridge; a fantasy console with built-in editors). We copy the product grammar; we do not depend on their runtimes.

---

## 1. What changed since v0.4

### 1.1 The corrections (the reason this document exists)

| v0.4 said | v0.5 reality | Why / source |
|---|---|---|
| **Lua-first** runtime; MicroPython as "Advanced/Lab" | **MicroPython/Python-first**; Lua/TIC-80 kept as a *future second cart type* behind the manifest `runtime` seam | Decided 2026-06-26. The T-Deck spike proved MicroPython boots/draws/runs carts on hardware and **exonerated it on performance** (the frame wall was native LVGL rotation, not Python). The only technical argument for Lua-first evaporated. #11 |
| Canvas target **480×270**, 64-color | **Two rendering domains:** the *game* is a fixed **320×240** indexed viewport (the console spec); the *system UI* renders at the panel's **native** resolution and reflows | #39 (owner-decided, in progress) |
| One "fantasy workstation" device | A **three-tier hardware family — Zero / Player / One** — over one shared console; tiers differ only in backend | §3 |
| Native core + **Lua-VM-per-cart + KidKernel services + LVGL shell** | Shipped simpler: a **host==device MicroPython shared console** + native C kernels (`moy_gfx`, `moy_compositor`) for the hot paths + a swappable per-tier backend | as-built (see CLAUDE.md) |
| First MVP = **Living Desktop** | Shipped first as a **TIC-80-style cart console**: on-device code/sprite/map editors, SD cartridges, an editable launcher, scroll engine, OTA, web view. "Living Desktop" survives as *a cart type*, not the MVP | tic80-goal; #54, #53, #41 |
| KidCode / Spryte naming | **Moybyte** (brand), `moybyte` / `moy_` (code/paths). Rename merged (PR #60) | — |

*(Note: many open issues predate the rename and still read `kc_` / `KidCode` / `.kcart`. This document uses `moy_` / Moybyte / `.moy`.)*

### 1.2 What v0.4 got right and we keep unchanged

- **Everything is a cartridge** — one object type for games, wallpapers, widgets, tools, lessons.
- **Host == device** — the PC sim is a faithful emulator of the device (same pixels, same font, mouse=touch, arrows=trackball).
- **Local-first, parent-safe** — offline by default, no accounts, no open child chat, no child real-name.
- **Progressive complexity** — cards → blocks → code → hardware lab.
- **Instant edit/run loop** — change one thing, press Run, see it change.
- **ESP32 appliance direction** — not a Linux/Radxa tablet path for v1.
- **Physical real-computer identity** — keyboard, D-pad, Run/Home, touch/pointer.

### 1.3 The new organizing idea v0.4 lacked

v0.4 described *one* device. v0.5's central architectural claim is:

> **One console, N backends. A tier is a backend port, not a fork.** The shared `console` / `editors` / `moy_carts` modules, the cart format, and the **320×240 indexed MOY64 canvas contract** are identical across every tier. Only the display / input / storage / transport backend differs. **The cart contract is the portability guarantee — the technical promise everything else rests on.**

---

## 2. Product definition & positioning

### 2.1 One-line concept

**Moybyte is a physical creative computer for kids: a device where the desktop, tools, games, art, and lessons are editable cartridges — sold as a three-tier family that all run the same console, so what a child makes on one runs on all.**

### 2.2 Emotional promise

> **Your first real little computer — not for watching videos, but for making the computer itself come alive.**

### 2.3 The positioning thesis (DECIDED)

The tempting question is "is this a first computer to learn *computer use*, or a computer to learn *programming*?" It's a false binary, and the answer dissolves it:

- **"Learn computer use"** is a *substitution* play — replace the tablet with something better-for-them. It competes with iPads and Chromebooks, lives or dies on being nearly as fun as YouTube (a fight you lose), and teaches a *fading* skill (files/folders/windows — the desktop metaphor kids no longer grow up on). **We reject this framing.**
- **"Learn programming"** is a *creation / agency* play — give the kid a workshop. It competes with micro:bit, PICO-8, Scratch, Kano; it doesn't fight consumption on fun-per-minute, it competes on **pride-of-making** and identity ("I'm someone who makes things"). Smaller audience, but stickier, and a parent who *actively chooses* it. **This is us.**

> **DECIDED: Moybyte is "learn to make." The "make the computer yours" magic is a programming pitch wearing a computer-use costume — and that's the genius, not a contradiction. Internally we know the engine is *learn to make*, not *learn to operate a desktop*.**

Consequences that fall out of this and bind design decisions:
- The first-run must optimize for a **made-something** moment (edit → Run → *it changed*), never a "navigated-the-desktop" moment.
- We do **not** chase tablet-parity (browser, media, app breadth). That's the trap that makes us a worse iPad.
- The desktop/cartridge skeuomorphism is justified by **remixability** (everything opens), not by "teaching kids what a real OS looks like."
- **"Computer use" is the *body*** (a whole little laptop, keyboard, files, many tools — the thing that separates us from a single-gadget coding toy); **"learn to make" is the *purpose*.**

### 2.4 Product pillars

1. **The computer is programmable.** The desktop, wallpaper, widgets, tools, and games are all code-backed cartridges.
2. **Make, do not consume.** No passive media center, no open internet in child mode. Creation first.
3. **Everything is a cartridge.** Game, app, wallpaper, widget, lesson, tool — variations of one bundle format. (Endgame: the *system itself* is carts — §5.5, #55.)
4. **One cart runs on every tier.** The 320×240 indexed MOY64 canvas is the universal contract. *(New pillar — the tier family's whole coherence rests on it.)*
5. **MicroPython-first visible world.** The visible computer is MicroPython userland; the hidden machine is native C. *(Was "Lua-first" in v0.4.)*
6. **Progressive complexity.** Icons → cards/blocks → Python → hardware/Python lab → advanced system mode.
7. **Instant edit/run loop.** Change one thing, press Run, see the computer change.
8. **Local-first and parent-safe.** Offline by default; local sharing first; AI optional and parent-gated.
9. **Physical real-computer identity.** Integrated keyboard/D-pad, Run/Home, touch/pointer.

### 2.5 What Moybyte is not

Not a cheap tablet · not a Chromebook replacement · not a normal Linux desktop · not a pure game console · not a PICO-8/Picotron/TIC-80 clone · not an app-store device · not an open child chat device · not "ChatGPT for kids" · not a toy laptop with canned games.

---

## 3. The three-tier hardware family

### 3.1 The lineup

All three run the **same carts + the same console** via the host==device architecture. Only the **backend** (display / input / storage / transport) differs. A cart authored once runs on every tier.

| | **Moybyte Zero** | **Moybyte Player** | **Moybyte One** |
|---|---|---|---|
| **Chip** | ESP32-S3 (e.g. Seeed XIAO S3) | ESP32-S3 | ESP32-P4 (+ C6 Wi-Fi) |
| **Display** | none → **phone browser** | small SPI LCD, native 320×240 | 7″ MIPI-DSI |
| **Input** | phone (web view) | D-pad / buttons + SD | USB → integrated keyboard |
| **Cart storage** | flash / Wi-Fi | SD | SDIO |
| **Role** | cheapest, phone-played companion | pocket game console | large-screen make-workstation |
| **Status** | design (enabled by #41/#22) | **shipping** (LilyGO T-Deck class) | porting (#58) |

**Why a tier is cheap: it's a backend port.** The console UI, editors, cart store, and canvas are one codebase. Zero and Player ride **off-the-shelf** boards (they prove portability and seed a community); **the One is the bespoke tier — the one whose hardware we design ourselves.**

### 3.2 The One — the make-workstation (#58)

ESP32-P4 + 7″ DSI + keyboard + SDIO. This is the identity anchor. Its advantages over the SPI tiers are structural:
- **No flush ceiling.** MIPI-DSI does continuous scanout from a PSRAM framebuffer — the Player's hard ~20ms / 153KB SPI push (§12.4) simply doesn't exist.
- **No SD↔display bus war.** SD on SDIO 3.0, display on DSI — separate buses. The #56 shared-SPI hazard is gone.
- **RAM headroom.** 32MB PSRAM ends the Wi-Fi-vs-framebuffer squeeze (#38/#40).
- **A real keyboard + big screen** — the ergonomics that make *authoring* (not just playing) pleasant. This is where a kid graduates from playing carts to making them.

What the One is for, stated crisply: **"make without a tether — standalone, big screen, real keys."**

### 3.3 The Player — the pocket console (shipping)

ESP32-S3 + SPI ST7789 (320×240) + physical input + SD. Fully working today: carts, on-device editors, audio, OTA (#53), web view (#41). Perf: current numbers live in the **#66 performance ledger** (2026-07-04: Sakura 36–38fps smooth, Sky Run 40–46 at the flush ceiling, render-bound carts 24–29; zero artifacts, micro-stutter fixed at the root).

- **A D-pad fits better than a keyboard.** Carts read `btn(left/right/up/down/a/b)` — a 1:1 map. The keyboard belongs on the One (for coding); the Player is for *play*. The T-Deck's trackball is a stopgap.
- **Native 320×240 is the requirement.** A 2.4″ IPS 240×320 (landscape 320×240) panel is the exact canvas — no scaling. This is what disqualifies smaller-screen S3 handhelds (e.g. a 240×135 Cardputer).
- Long-term: a light custom S3 board (S3 + 320×240 IPS + cheap D-pad/ABXY) that reuses the shipping S3 backend, or a confirmed-PSRAM off-the-shelf S3 gamepad handheld.

### 3.4 The Zero — the headless companion ("the browser is the GPU", #41/#22)

The XIAO ESP32-S3 is the *same chip* as the Player, so the firmware core runs unchanged; its only limit is I/O (~11 pins, no screen/keyboard/SD). So the Zero is **headless, played through the web view on a phone**:
- With the draw-command web protocol (#41/#22), a headless Zero **never rasterizes**. The "canvas" is the `DrawRecorder`: the device runs cart *logic* and streams draw commands; the **browser** does all the pixel work. No `moy_gfx`, no framebuffer, no flush — minimal RAM/CPU, and the cart runs fast (logic only).
- **Carts live in flash** (16MB XIAO S3 Plus recommended → hundreds of KB-scale carts) or pull over Wi-Fi. `moy_carts` already takes a `root` and is pure filesystem ops, so SD→flash is a mount-point swap, not a rewrite.
- **Caveat:** not standalone — it's a Wi-Fi + phone companion, not a Game Boy. It also **assumes the kid has a phone** and Wi-Fi. Framed honestly: the Zero is a **demo/companion/seed** unit, never sold as "the cheap way to actually own one."

### 3.5 The create/consume funnel (DECIDED direction)

- **The create/consume axis and the tier ladder run opposite — on purpose.** Authoring (keyboard) lives in the One; the cheap tiers *play*. That's a **funnel** (play cheap → graduate to making), not a bug. And it's softened by web-view authoring (§8.4): a cheap Zero + the kid's own phone = a real authoring station, so *making is available on every tier* (tethered), while the One is where making is **best and untethered**.
- **Tiers are roles, not just entry points.** In a local multiplayer session (§9), the **One is the table/host** (big screen, most RAM) and **Players/Zeros are the controllers**. A living-room party where the One is the board and the handhelds are the pads.

---

## 4. The canvas contract & two rendering domains (#39, DECIDED)

### 4.1 Two domains

> **DECIDED (owner):** the *game* is a **fixed 320×240 indexed canvas** — the console spec, like a fantasy console — scaled fixed-aspect onto whatever panel. The *system UI* (desktop + editors) renders at the **panel's native resolution** and reflows to use the space.

| | **Game** | **System UI** (desktop, code/sprite/map/bg editors) |
|---|---|---|
| Resolution | fixed **320×240**, fixed aspect | the panel's **native** size, responsive |
| On screen | scaled, letterboxed **viewport** | fills the panel, reflows |
| Portability | **the spec** — carts + the cart API never change | chrome only; never touches `.moy` |
| Player (320×240) | fills screen | == game size → identical to today |
| One (7″) | crisp ×N viewport, centered/docked | roomy native-res editors |

- **Font/UI scale is user-adjustable in Settings** (persisted in `system.json`), defaulting by panel — accessibility *and* density. The game keeps its own 8×8 pixels regardless.
- **Near-term dev win, not just future hardware:** the host sim and web console already render at arbitrary sizes, so the moment the system UI is resolution-parametric, you author carts in a *spacious* PC/browser editor with the game in a fixed 320×240 preview.

### 4.2 Why the game canvas is indexed (the portability contract)

The game canvas works in **palette indices** (the **MOY64** palette) with a plain-function drawing API — no dependency on `framebuf`, LVGL, or even Python. This is deliberate: the *same* `.moy` runs on the host, on every device tier (indices → RGB565 via `moy_compositor`), and eventually a Lua VM. **This is the contract to protect. Hold the line at 320×240 for carts.**

> **DECIDED: the game canvas stays 320×240 indexed, forever, across all tiers.** Low-res is the *aesthetic*, not a limitation (TIC-80 240×136, PICO-8 128×128 prove it). Giving the One a native hi-res *game* canvas would fork the contract and dissolve the tier family's coherence — so we don't. The One's big screen is spent on *system UI* and a crisp ×N game viewport, not on breaking the spec.

---

## 5. Architecture as-built

### 5.1 Layer model (the real one)

```text
Hardware  (per tier: ESP32-S3 / ESP32-P4; display, input, SD/flash, wireless)
   ↓
Native C kernels
   - moy_gfx        indexed blitter (fill / fill_rect / blit565 / spr_batch / blit_window)
   - moy_compositor RGB565 framebuffer + DMA flush
   - moy_sd         SD attach on the shared host (native, no bus re-init)
   ↓
MicroPython runtime  (device backend: DeviceCanvas + make_api + TrackBall/Touch/Keyboard)
   ↓
Shared console  (host == device, staged into firmware at build)
   - console.py     Launcher + Pointer + Workstation + top bar + mode switching
   - editors.py     CodeEditor / SpriteSheet / PaintEditor / IconSheet
   - moy_carts.py   the .moy store (scan/load/save/create/dup/del/seed)
   ↓
Cartridge userland  (.moy: games, wallpapers, widgets, tools, lessons)
   ↓
Optional future: Lua second cart type (§6.4) · MicroPython Hardware Lab (§11.4)
```

The elaborate v0.4 "KidKernel services + one-Lua-VM-per-cart + LVGL-shell" stack did **not** happen and is not the plan. The shipping architecture is simpler: native kernels for the hot paths, one MicroPython runtime, one shared console, backend-swapped per tier.

### 5.2 Host == device (the crown jewel)

The console UI is **one codebase** that both the host simulator and every device tier run — the *same* 320×240 pixels, the *same* petme128 font. Canonical sources live in `runtime/`; the firmware build stages copies into its frozen `modules/` tree so the device freezes identical code. The host sim is a faithful emulator (mouse=touch, arrows=trackball). This is what makes tiers cheap and testing honest.

### 5.3 The cart format (`.moy`)

Folder form:
```text
my_cart.moy/
  manifest.json     format, title, type, runtime, canvas, permissions, version
  main.py           the cart (MicroPython; _init/_update/_draw or exec model)
  config.json       kid-tunable values (edited by cards; re-seed-preserving)
  sprites.moygfx    PICO-8 __gfx__-style indexed sprite sheet
  map.moymap        (optional) tilemap
  pmem.json         (optional) persistent kid data / saves
```

- **Cart types:** app / wallpaper / widget / game / story / animation / lesson / tool / theme / (hardware_project / web_project later).
- **Versioning (#47):** every built-in cart's `manifest.json` carries an integer `version`. Re-seed replaces an on-device built-in only when the baked version is newer, preserving `pmem.json` + `config.json`. **Bump the version whenever a built-in's content changes.**

### 5.4 The drawing & runtime API (indexed, plain functions)

```text
graphics:  cls  pset  line  rect  rectfill  circ  circfill  spr  print
           map  camera  clip  pal            (all operate in MOY64 indices)
input:     btn  btnp  touch  pointer  key
audio:     beep  sfx  music  volume
storage:   store  fetch  (pmem)  files.list
sharing:   radio.send  radio.on_message  share.send_cart      (gated)
hardware:  module.*  led.set  servo.set                        (gated, §11)
```

Cart lifecycle: `_init()` / `_update(dt)` / `_draw()` (with a simpler `exec`-style path also supported today). Cards/blocks drive or generate the same model.

### 5.5 Endgame: the system itself as carts (#55, EXPLORATION / OPEN)

Take "everything is a cartridge" to its Picotron conclusion: the **system UI itself** — top bar, launcher, Settings, dropdown, editors — runs as *carts* the console loads, not as hardcoded chrome. The themeable `IconSheet` (#46, shipped) is the low-risk first slice (repaint the bar's icons).

The hard parts (why this is OPEN, not committed):
- **Privilege boundary.** System carts need powers the kid sandbox forbids (switch screens, open editors, manage carts, read wifi/battery, reboot) → we need a privileged/system API surface and a system-vs-kid trust distinction we don't have yet.
- **Crash isolation + lifecycle.** The bar/launcher must *never* be taken down by a buggy cart. Needs a loader + watchdog story first.

---

## 6. Runtime & languages

### 6.1 MicroPython-first (DECIDED)

The cart runtime is **MicroPython/Python**. Rationale: proven to boot/draw/run carts on real hardware; familiar to parents/schools; already shipping.

> **2026-07-03/04 update to the perf rationale:** the original "exonerated on performance (cart logic ~1–2ms)" claim held for sprite-light carts but NOT for float-physics carts — #63/#66 found THREE interpreter taxes on kid-idiomatic code: the per-draw-call dispatch wall, the call-frame heap-spill pathology (120-entity logic collapsed to 10–12fps), and float boxing (16B heap alloc per float result → a ~150ms gc collect every second = the micro-stutter). All three were fixed **engine-side, API unchanged** (native `spr_gate`, doubled S3 caches, REPR_C unboxed floats), which is the doctrine: kid code stays Python-simple, the engine keeps it fast. Sakura now runs 36–38fps smooth. The decision stands — strengthened. Lua remains the future *second* cart type (~4x faster on the same loop post-REPR_C, #6/#67), with PICO-8/TIC-80 source-compat the likelier trigger than raw speed. Current numbers: the **#66 performance ledger**.

We adopted **TIC-80-style drawing conventions in place** (filled `rect`/`circ`, outline `rectb`/`circb`, `spr` by sheet index, `print`) without a Lua VM.

### 6.2 The blocks → code ladder (#29 shipped, #48 next, #15)

Progressive authoring, all editing the *same* cart:
- **Icons → blocks → Python.** #29 shipped the block-language overhaul + a working single-object tap game.
- **Blocks depth (#48)** is the path to real games in blocks, in leverage order: **lists** (the biggest gap — no collections ⇒ no multiple enemies/bullets/inventories), **custom blocks** (define/call with params), **control** (`repeat until`, `wait until`, `break`; fix frame-yielding `forever`), **operators** (`mod`, `round`, `abs`, `min/max`, comparisons).
- **Blockly-in-browser (#22)** is the richer web-based block editor path.
- The `.moyproj` SDK + block compiler is deliberately **kept** — it seeds the icons→blocks→code ladder even though `.moy` is the active console format.

### 6.3 Quests & pedagogy (#20, see §7).

### 6.4 Lua / TIC-80 as a future second cart type (#11, OPEN)

The Lua door stays open **cheaply** — API-as-contract, VM-neutral native kernels, a stable `_init/_update/_draw` lifecycle, a manifest `runtime` field — but we do **not** pay for it now. When we do add it (#11), the model is:
- **TIC-80-compatible Lua** as the second cart API: global `TIC()` loop + global `cls/spr/btn/print/…`; Moybyte extensions **namespaced** (`moy.radio.send`, `moy.badge.unlock`, `moy.desktop.set_wallpaper`).
- **OPEN reconciliation:** TIC-80 is a **240×136** framebuffer; our canvas is **320×240**. Whether a TIC-80 cart type renders into a 240×136 sub-viewport, or we run a "compatible subset" at 320×240, is undecided and load-bearing. Gate this on a real `.tic` source-compat goal, not on aesthetics.

---

## 7. The creative ladder & guided quests (#20)

### 7.1 The ladder

```text
use → customize → edit a cartridge → make a sprite → change one line of code → build a game/widget → share/remix
```

This ladder is the *product thesis* made operational: it's how one device serves creative *play* for a 5-year-old (who can't really "program") and real *programming* for a 10-year-old. The tiers map onto it — cheap tiers enter at "use/play," the One reaches "build/make."

### 7.2 Quests as part of the computer (#20, OPEN)

Not separate tutorials — small, achievable missions that feel native to the machine, data-driven (`/system/quests/*.quest.json`), with badges/unlocks:

```text
Wake Up the Desktop → change a color, press Run        → Desktop Painter
Make a Pet          → add an animated sprite           → Pet Maker
First Code Spell    → change one number in main.py      → Code Tinkerer
Turn It Into a Game → make the pet move with arrows     → Game Starter
```

A quest carries: title, short goal, hint, completion condition, reward, optional "show me how." This is the concrete engine of "learn to make" onboarding.

### 7.3 First-session flow (illustrative)

```text
1. Boot into a living cart (e.g. a space desktop / a playable seed cart).
2. Prompt: "Make it yours."
3. Change 3 visible things (color, a number, a sprite) via cards.
4. Press Run — it changes immediately.
5. "See the code" → change one line → Run again.
6. Save as the kid's own cart.
7. (Later) Share it to a friend's device.
```

The teachable moment is always: **the computer is made of editable things, and *I* changed it.**

---

## 8. Editors & tools

### 8.1 Shipping editors (BUILT)

- **Code editor (#3):** full-screen; caret nav, vertical+horizontal scroll, drag-to-scroll, tap-to-place, RUN/SAVE/CLOSE, and an on-screen symbol palette for `= ( ) [ ] { } < > %` (the T-Deck keyboard lacks `=`).
- **Sprite / paint editor (#4):** `sprites.moygfx` storage (PICO-8 `__gfx__`-style indexed).
- **IconSheet (#46):** 16×16 themeable system-bar icons; Settings → EDIT ICONS repaints them. The low-risk first slice of §5.5.

### 8.2 Editors in progress / planned

- **Map editor (#57):** place big multi-tile sprites — a SIZE brush.
- **Music editor (#50):** compose tunes on-device, mirroring the sprite/map editor pattern. *(Music was deferred in the TIC-80 goal; this is its home.)*
- **Top-bar dropdown menu (#52):** Picotron-style overflow / system actions.
- **Resizable UI font (#39):** the settings-driven system-UI scale.

### 8.3 The unified top bar (#46, BUILT)

One 18px bar: icon-only mode switchers (home/edit/paint/map/blocks) on the left, status (clock/wifi/batt/gear) on the right, cart actions (new/dup/del) on home. Icons are 16×16 sprites from an editable `IconSheet`.

### 8.4 Web-view authoring — un-gating "make" to every tier (DECIDED direction)

The web view (#22/#41) is not just for play. Because keyboard-less tiers (Zero, and a D-pad Player) can't comfortably author on-device, **authoring for those tiers happens through the web view**, where the phone/PC supplies a real keyboard and a big screen.

> **DECIDED direction — remote the *same* editor, don't fork it.** The browser is a thin terminal: the device runs the real `editors.py`, streams it over the #22 draw-command protocol, and the browser sends keystrokes back. **One editor codebase** — preserving host==device. (The rejected alternative — a separate JS editor in the browser — means maintaining two editors and syncing their features + file formats forever.)
>
> The one detail to nail under this: what resolution the editor renders at when remoted to a phone viewport vs. running native on the One's panel.

This is what makes the create/consume funnel humane: the cheapest Zero + a phone the kid already owns = a genuine authoring station. The One stays differentiated as *untethered, standalone, best-ergonomics* making.

---

## 9. Multiplayer & local sharing (#7)

### 9.1 ESP-NOW: the numbers (verified 2026-07-01)

| | ESP-NOW |
|---|---|
| Paired peers | ≤ 20 total (7 encrypted default, up to 17 configurable) |
| Broadcast | **unlimited** listeners — no pairing, no 20-cap |
| Payload | 250 B/packet (v1); 1470 B on ESP-NOW v2 (newer IDF) |
| Latency | **1–10 ms** (≈5 ms typical single packet) |
| Throughput | ~214 Kbps open air, ~555 Kbps shielded, ~1 Mbps PHY ceiling |

**Player count is not the constraint — shared airtime is.** ESP-NOW is half-duplex, so throughput is split across every transmitter on the channel. Practical: **4–8 players for a tight real-time game, up to ~16 with pairing, "the whole class" for lighter broadcast sync.** Latency (~5ms local) *beats* most console party games over WiFi — that's the superpower.

### 9.2 Topology (DECIDED direction): host-authoritative star + broadcast

- Clients **unicast** their inputs to the host (tiny packets).
- The host runs the game and **broadcasts** one state packet everyone receives.
- Traffic scales ~linearly with players (not N²), and broadcast sidesteps the peer cap.
- Maps onto the tiers: **the One is the host/table**, **Player/Zero are controllers**.

Discipline: broadcast isn't ACKed → send absolute state snapshots (self-healing), use unicast+ACK only for critical one-shot events (join, score, game-over). **Caveat:** ESP-NOW shares the 2.4GHz radio with WiFi — a Zero streaming to a phone *and* doing ESP-NOW at once is the contended case to test; peers must sit on the connected AP's channel.

### 9.3 Cart sharing

Cartridge-first sharing: share a sprite / sticker / wallpaper / widget / game / high score / multiplayer invite. Start smaller than full projects (a sprite, a wallpaper) before whole carts. Parent-gated receive; **received carts are quarantined until accepted.** The API seam (`radio.*`, `share.send_cart`, `radio_send`/`radio_receive` permissions) and the MicroPython `espnow` module already exist — a first party-game cart needs no new C.

---

## 10. Rich rendering & 3D

### 10.1 The fixed viewport keeps games portable (DECIDED)

All *games* render into the 320×240 indexed viewport (§4). This is what makes a cart run everywhere. 3D does **not** get to break this.

### 10.2 Voxels — the on-brand, portable path to 3D (#44)

The engine decision (#44) was to **extend our own indexed engine**, not adopt a turnkey one (TGX/PixelRoot32 evaluated and declined — wrong color model / framework lock-in). 3D fits *inside* the indexed contract via a **Voxatron-model** renderer:

- **Indexed voxel volume, fixed-ish camera, column-DDA/splatting** → rasterizes into the *same 320×240 indexed frame*. So it runs on every native tier and stays inside the cart contract. Aesthetically on-brand (Voxatron is literally the PICO-8 author's voxel console).
- **Tiered rollout:**
  1. **Bake-to-2D-sprite first (DECIDED as the first step).** A kid builds a voxel model; the system renders it from an angle into sprites usable in *normal 2D carts*. Dimensional-looking art (Crossy Road / Q\*bert / Populous charm) at **zero live-render cost**, fully portable (output is sprites), fully **kid-authorable** via a voxel editor. ~80% of the delight for ~20% of the risk.
  2. **Live voxel mode later (stretch).** Volume re-rendered each frame; camera moves. Perf-heavy — gated behind the perf work (dirty-rect, sprite-batch, flush). It *breathes on the One* (P4, no flush ceiling, real compute); keep the camera **fixed-ish** (free-fly 3D is where indexed voxel gets expensive — Voxatron holds the camera for exactly this reason).

> **Ship bake-to-sprite regardless of whether live voxel ever lands** — it's the portable, authorable, on-brand win and doesn't depend on the perf stretch.

### 10.3 Rich web-view rendering — including 3D (EXPLORATION / OPEN)

When the browser renders (the Zero's "browser is the GPU" model), the visual ceiling **decouples from the ESP32** — a phone browser runs WebGL/WebGPU 3D easily. This is a genuine differentiator but an **unfenced version dissolves the product**, so it is explicitly *exploration, not plan*:

- **The elegant form (if pursued):** the device stays the **game server** (authoritative logic/state); assets load into the browser once and cache; per frame only entity/camera *deltas* stream. The device still "runs the game"; the browser is a rich client. Composes with §9 multiplayer (device = host, phones = fancy viewports).
- **The form to avoid:** the cart becomes a WebGL *web app* and the ESP32 degrades to a dongle — that's a web game platform wearing an ESP32 hat, smuggling the tablet back into a "real computer" product.
- **The reconciliation that would make it safe:** make the **voxel scene the shared, renderer-agnostic content model** — native tiers render it chunky (indexed splat); the web view renders the *same* voxel volume richer via WebGL. One cart, two fidelities, portability intact. The web view becomes a *nicer renderer of the same content*, not a separate cart class.
- **Open blockers before this could be committed:** it's a de-facto third cart profile (won't run natively); it's not kid-authorable (shaders); and it risks splitting the low-res identity. **Decide deliberately; do not slide into it.**

---

## 11. Hardware & modules

### 11.1 Boards per tier

- **Player:** LilyGO T-Deck (today); candidate off-the-shelf S3 gamepad handheld with a native 320×240 IPS + D-pad (confirm 8MB PSRAM before committing); a light custom S3 board later.
- **One:** ESP32-P4-WIFI6-Touch-LCD-7B (7″ 1024×600 IPS, GT911 touch, ESP32-C6 Wi-Fi6, 32MB PSRAM, SDIO 3.0, battery) — #58. Also evaluating the Guition P4 7″ board (#12). A Waveshare ESP32-S3-Touch-LCD-7 (RGB-parallel, continuous scanout) is a cheap **no-flush test bench** to de-risk the One's big-screen architecture on a chip we already ship.
- **Zero:** Seeed XIAO ESP32-S3 Plus (16MB flash / 8MB PSRAM, same footprint) recommended; N16R8 DevKitC / Waveshare S3 as non-XIAO options; ultra-cheap no-PSRAM S3 boards viable since the Zero doesn't rasterize.

### 11.2 Hard device constraints (respect these — full detail in CLAUDE.md)

- **SD shares the SPI host with the display** on the S3 tiers. Nothing touches SD before the panel is up; live SD goes through the native `moy_sd` attach (no bus re-init); never tear the SD device down mid-session or flush the panel inside an SD op. (The #56 saga. Gone on the One — separate buses.)
- **The `run_desktop` native-takeover loop starves USB** — no serial/REPL/esptool once "desktop running" prints. Capture boot logs passively while pressing reset.
- **Full-screen flush must be a single `tx_color`** from a PSRAM DMA buffer.
- **The keyboard has two modes** (clean 1-byte ASCII for the editor; raw-matrix for hold-to-move in carts); the console flips per screen.

### 11.3 Input across form factors (#42)

The cart input model (`btn`/`btnp`) assumes physical controls, which breaks on touch-only surfaces (phone web view). The unified plan:
- **On-screen virtual gamepad** — d-pad + A/B, **device-drawn into the frame** (so it works identically on the T-Deck touchscreen and every web client, streamed for free), feeding `btn`/`btnp` with **zero cart changes**.
- **Web/phone keyboard** for text; **external BLE keyboard/mouse/joypad (#26)** for the One and power users.
- Show the pad on touch-only displays; a toggle on the Player (it has real buttons); ideally driven by what the cart reads.

### 11.4 Modules & the Hardware Lab (#9, OPEN)

The "hardware learning" pillar: gated `module.*` / `led.set` / `servo.set` APIs, and physical extensions like **Lego motor control (#9)** over wireless or device pins. This is where MicroPython's Advanced/Hardware-Lab role lives (§6.4) — sensor/robotics examples, parent/maker scripts — always behind explicit permissions.

---

## 12. Platform services

### 12.1 OTA firmware update (#53, BUILT, hardware-confirmed)

Dual-OTA partitioning: the device flashes a new `.bin` into the **inactive** slot and ping-pongs, with rollback on (a bad image self-heals; `run_desktop` calls `mark_valid()` at a healthy boot). **Two channels — STABLE and UNSTABLE/BETA** — toggled in Settings; the build stamps its channel/version into a gitignored `_ota_build.py`. Phase-3 WiFi download streams a manifest-described `.bin` to SD (sha256-verified). **Bump `moy_ota.FIRMWARE_VERSION` on every release.** Download ~72KB/s ≈ the MicroPython TCP ceiling (don't chase it).

### 12.2 Web view (#41/#22, host-tested; hardware-UNVERIFIED)

Serves the **running console** to a browser on the same WiFi via the **same draw-command protocol** (`defspr`/`spr`-by-index/`map`/primitives) — the page renders device frames, never the raw framebuffer (WiFi ~72KB/s, 153KB/frame is unplayable). Live channel is a persistent **WebSocket** (frames push down, input pushes up — no per-frame handshake). Off by default (zero per-draw cost); Settings → WEB VIEW swaps in a `TeeCanvas`. This is the Zero's entire rendering path and the universal authoring/remote surface (§8.4). **WiFi↔LCD-DMA coexistence + the socket layer are unverified on hardware.**

### 12.3 Storage

`.moy` carts on SD (Player) / flash (Zero) / SDIO (One), via `moy_carts` (pure filesystem ops, root-parameterized). On the S3 tiers, live SD access uses the native `moy_sd` attach and `with_sd_live` (mount once, keep resident, run ops between frames).

### 12.4 The perf ceiling (#43 → the #66 ledger)

**Current numbers, the frame-budget model, and the lever ledger live in issue #66** (the living performance ledger — edit its body when hardware numbers land; don't fork the numbers into this doc). The shape of the ceiling (2026-07-04): the ~15–16ms SPI flush is hidden behind render by the double-buffer overlap and now streams from internal-SRAM bounce buffers (PSRAM contention can no longer corrupt it), so light carts sit at the ~45–50fps flush ceiling; float-physics carts run logic-bound but healthy (Sakura ~11ms logic → 36–38fps after the #63/#66 interpreter-tax fixes: native `spr_gate`, doubled caches, REPR_C unboxed floats); Python-prim-render-bound carts sit at 24–29 until native text #62 / map #32. Remaining SPI-tier levers, in value order, are in #66. **On the One (P4/DSI), the flush ceiling is gone** — which is a core reason the One is the reference tier.

---

## 13. Safety, privacy & AI

### 13.1 Local-first & parent-safe

Offline by default; local sharing first; no accounts for local use; no child real-name; no open internet in child mode.

### 13.2 Cart permission model

```text
low  (default): graphics, input, sound, local_storage
high (parent-gated): radio_send, radio_receive, local_web_server, wifi_internet,
                     ai_helper, sensor_read, actuator_write, raw_files, advanced_system
```
Wallpapers/widgets cannot silently use the network. Received carts are quarantined until accepted. Parent can restore defaults / system carts.

### 13.3 AI helper (#8, OPEN, parent-gated)

AI understands *cartridges and customization*, not only game code. Allowed: explain this error · suggest one small change · make this wallpaper more fun · turn this pet into a game idea · summarize my project for a parent. **Not** allowed by default: open-ended child chat · AI companion behavior · raw internet · unrestricted image gen · unapproved sharing. Architecture: device → parent-approved gateway → LLM → **schema-checked** structured response → device. AI returns structured assets/hints, never freeform control of the system.

---

## 14. Roadmap & status

### 14.1 Built (shipping on host and/or device)

Shared host==device console · `.moy` cart format + store + versioning (#47) · on-device code (#3) / sprite (#4) editors · unified themeable top bar + IconSheet (#46) · native `moy_gfx` + `moy_compositor` pipeline · `spr_batch` (#43) · scroll engine window-copy + a hardware-confirmed ~45fps demo cart (#54) · two-channel OTA, hardware-confirmed (#53) · web view / WebSocket, host-tested (#41/#22) · block language + single-object game (#29) · SD live read/write (#56 fix).

### 14.2 Next (dependency-ordered)

1. **The One / P4 port (#58)** — the primary backend; de-risk toolchain → native DSI driver → wire the shared console.
2. **Responsive native-res system UI + resizable font (#39)** — unlocks spacious authoring now, hardware-ready later.
3. **Input across form factors (#42)** — virtual gamepad + web keyboard; prerequisite for phone/web play (Zero/Player-on-phone).
4. **ESP-NOW local multiplayer & sharing (#7)** — party games; cart transfer.
5. **Blocks depth (#48)** — lists first; the path to real games in blocks.
6. **Quests & badges (#20)** — the "learn to make" onboarding engine.
7. **Music editor (#50)**, **map big-sprite brush (#57)**, **top-bar dropdown (#52)**.
8. **Voxel: bake-to-sprite (#44)** — portable, authorable 3D-look.
9. **TIC-80 second cart type (#11)** — gated on a real source-compat goal.

### 14.3 Milestones

```text
M1  One (P4) boots the shared console on the 7" panel                        (#58)
M2  Native-res system UI + fixed 320×240 game viewport across tiers          (#39)
M3  Two kids play a party game over ESP-NOW (One = table, Players = pads)     (#7)
M4  A kid authors a cart on a Player/Zero via the phone web view             (#42/#22)
M5  Blocks can build a multi-object game (lists)                             (#48)
M6  Quest+badge onboarding path end-to-end                                    (#20)
M7  Voxel bake-to-sprite in the sprite pipeline                               (#44)
```

---

## 15. Open decisions

**Must decide reasonably soon**
1. **Tagline & naming.** Brand is **Moybyte** (rename merged); the device line is **Zero / Player / One** (DECIDED 2026-07-01 — familiar-but-clear, `Pi`-style ladder). Remaining: the tagline (the "MOY = *moj* / *my* computer" narrative).
2. **TIC-80 canvas reconciliation (#11).** 240×136 sub-viewport vs a 320×240 compatible subset.
3. **The Player's final board** — off-the-shelf S3 handheld (confirm PSRAM) vs a light custom S3 PCB.

**Can decide later**
4. **Commit to system-as-carts (#55)** — needs the privilege boundary + crash isolation first.
5. **Rich web-view 3D scope (EXPLORATION, §10.3)** — if/when, and only in the fenced "device-as-server" form.
6. **Lua second cart type timing (#11)** — gated on a real `.tic`/Lua source-compat goal.
7. **Lego/modules scope (#9)** and **BLE input (#26)** — the Hardware Lab surface.
8. **AI helper (#8)** — gateway and provider.

---

## 16. Final v0.5 recommendation

Lock this as the plan:

```text
Moybyte is a kid's first CREATIVE computer — learn to MAKE, not learn to use.
It ships as one console over three backends: Zero (companion) / Player (pocket console) / One (make-workstation).
A cart authored once runs on every tier — the 320×240 indexed MOY64 canvas is the contract, held forever.
The system UI renders native-res and responsive; the game canvas stays fixed 320×240.
The runtime is MicroPython-first; Lua/TIC-80 is a future second cart type, kept open cheaply.
Native C owns the hot paths; the shared MicroPython console owns the visible computer.
The One is the identity anchor; the cheap tiers are reach and play — the funnel into making.
3D lives inside the contract via indexed voxels (bake-to-sprite first); richer web-view 3D is a fenced exploration.
Local-first, parent-safe, ESP-NOW party play, on-device + web-view authoring.
```

The clearest proof of the whole thesis remains a single moment:

> **A child changes one thing, presses Run, and the computer becomes theirs — and it doesn't matter which of the three they're holding.**
