# Moybyte `.moy` cart API (current)

The drawing/input/audio API a **v0.4 `.moy` cartridge** calls. This is the *current*
console API — it supersedes the legacy `.moyproj` SDK API in
[`moybyte_api.md`](history/moybyte_api.md) and the 128×128 `run/sprite/text` contract in
[`firmware_runtime_contract.md`](history/firmware_runtime_contract.md), both of which describe
the old parallel SDK.

**Source of truth (keep this doc in sync with them):** the API namespace is built by
`make_api()` in `runtime/host_app.py` (host reference) and the identical
`make_api()` in `firmware/lilygo_t_deck_plus_micropython/modules/moy_runtime.py`
(device). The drawing ops live in `runtime/canvas.py`; the palette in
`runtime/palette.py`; buttons in `runtime/input.py`. A cart runs **identically** on the
PC simulator and on the device — same names, same pixels.

---

## The shape of a cart

A cart is a single `main.py` inside a `.moy` folder (`manifest.json` + `main.py` +
`config.json`, optional sprite sheet / tilemap / sounds / paint images). It defines up
to three lifecycle functions and calls the API by name — **no imports**; every name
below is pre-injected as a global. (A cart can also be written in **Lua** —
`main.lua` + `"runtime": "lua"` in the manifest, same API — see
[Writing a cart in Lua](#writing-a-cart-in-lua-67).)

```python
# a tiny cart: move a ball with the D-pad
x = y = 0

def _init():                 # once, when the cart starts (optional)
    global x, y
    x, y = W // 2, H // 2

def _update(dt):             # every frame; dt = seconds since last frame (optional)
    global x, y
    speed = 120 * dt         # ~120 px/sec, framerate-independent
    if btn("left"):  x -= speed
    if btn("right"): x += speed
    if btn("up"):    y -= speed
    if btn("down"):  y += speed

def _draw():                 # every frame; render here
    cls(col("dark_blue"))
    circ(int(x), int(y), 6, col("yellow"))
    print("MOVE ME", 8, 8, col("white"), 1)
```

### Lifecycle
| hook | when | notes |
|---|---|---|
| `_init()` | once at cart start (and on restart) | optional; reset your state here |
| `_update(dt)` | once per frame, before draw | optional; `dt` is **seconds** (float). Put game logic here |
| `_draw()` | once per frame | render the frame; called every frame even if `_update` isn't defined |

## Writing a cart in Lua (#67)

A cart can be **Lua instead of Python**: set `"runtime": "lua"` and
`"main": "main.lua"` in `manifest.json`. **Every call in this document is valid
verbatim in both languages** — `spr(1, x, y)`, `btn("left")`, `cfg("speed", 2)`,
`pmem`, `quit()`, `textmode()` — because the Lua globals *are* the same console
API (the device bridges them natively; hot sprites append to the same batch the
Python path uses). Same lifecycle too: define `_init` / `_update(dt)` / `_draw`
as global functions.

```lua
-- the same tiny cart, in Lua
local x, y = 0, 0

function _init()
  x, y = W // 2, H // 2
end

function _update(dt)
  local speed = 120 * dt
  if btn("left")  then x = x - speed end
  if btn("right") then x = x + speed end
  if btn("up")    then y = y - speed end
  if btn("down")  then y = y + speed end
end

function _draw()
  cls(col("dark_blue"))
  circ(flr(x), flr(y), 6, col("yellow"))
  print("MOVE ME", 8, 8, col("white"))
end
```

**Why pick Lua:** speed. Logic-heavy carts run flat frame times with no
garbage-collector pauses (the measured verdicts live in issue #67 — e.g. Sakura's
logic at a flat 3–4ms where the Python twin spikes to 19–24ms).
`system_carts/sakura_lua.moy` is the living example — a line-by-line twin of
`sakura.moy`, pixel-identical by test.

The few Lua-specific notes:

- `touch()` returns **multiple values**, not a tuple:
  `local tx, ty, tapped, held = touch()` (all `nil` when no pointer).
- `print(...)` is the **draw-text verb** (as in this doc), not Lua's console print.
- Layer methods are **colon calls**: `lay = make_layer(w, h)`, then `lay:cls(0)`,
  `lay:map(...)`, `lay:spr(...)`; stamp with `draw_layer(lay, cam_x, cam_y)`.
- `spr(n, x, y, colorkey, scale, flip)` takes **sheet-tile numbers** (the fast
  path). Paint images (`image("name")`) are placed via a layer —
  `lay:spr(image("bg"), x, y)` — not passed to `spr()` directly; multi-tile
  sprites (`w,h` spans) are drawn as their individual tiles.
- No imports, same as Python. The **safe Lua stdlib** is available: `math.*`,
  `string.*`, `table.*` (no `io`/`os`/`load`/`require`).
- A crash opens the same error panel, and EDIT drops on the offending
  `main.lua` line. The code editor's tap-palette offers `~` (for `~=`) in a
  Lua project.
- The blocks editor stays Python-only for now (compiles to Python by design).
- **Numbers on the device are 32-bit** (`LUA_32BITS`, #67): floats carry ~7
  digits and integers wrap at ±2.1 billion — plenty for scores, timers and
  positions, and float math runs on the hardware FPU (part of why Lua is the
  fast tier). The PC simulator uses 64-bit doubles, so a float-heavy cart can
  drift slightly between sim and device.

*(Host note: the PC simulator runs Lua carts through `lupa` — an optional dev
dependency; without it a Lua cart opens the "needs the Lua runtime" panel.)*

## Frame pacing

The console has a frame governor: a **game** cart locks to a steady **30fps** —
a steady 30 feels smoother than a jittery 40, and the headroom absorbs hiccups.
If your cart genuinely holds 60 (measure it!), declare `"fps": 60` in
`manifest.json` (Hop Quest and Sky Run do). Tools/apps and all console screens
run at 60. *(The governor currently ships **disabled** — `console.FPS_GOVERNOR
= False`, an owner measurement mode so every cart shows its real uncapped fps;
the manifest field and the policy are live the moment the flag flips back.)*
Your `_update(dt)` gets the real `dt` either way — movement written as
`speed * dt` is framerate-independent.

## The canvas

- **320×240**, indexed. `W` = 320, `H` = 240 are globals (read them; don't assume).
- **A running GAME owns the FULL canvas** — the console's top bar hides during play
  (it reappears only on the crash panel). There is no pause screen and no reserved
  letter: **every key belongs to your cart**. To exit, the player **holds BACKSPACE
  (~700ms)** — a quick tap reaches your cart as a plain key; a sustained hold shows a
  small progress toast and returns to whoever launched the cart (the launcher, or
  the editor's PLAY). A `"tool"`/`"app"` cart runs WITH a minimal console bar
  (title + status + ✕) instead, and exits via that ✕.
- **Typing games: call `textmode(True)`** (e.g. in `_init`). In game mode the device
  keyboard only produces 9 letters (the button-mapped `a d w s z x r` plus plain
  `q e`); text mode delivers EVERY letter to `key()`/`keyp()`.
- **A `textmode(True)` game MUST provide its own exit** — the console can't reach it.
  In text mode BACKSPACE is a plain typed key your cart reads (a delete), so the
  console's standard BACKSPACE exit never reaches the cart, and the T-Deck keyboard
  has no autorepeat so a *held* BACKSPACE doesn't register either. So bind **`quit()`**
  (see [State & utility](#state--utility)) to a spare key or a small on-screen
  affordance you draw. Letter Blitz models this with a tap-anytime ✕ in the top-right
  corner (above its HUD, clear of the play area) — a touch exit never depends on the
  keyboard. (A `"tool"`/`"app"` cart already runs with the console's own context-✕
  bar, so it's exitable without this; but `quit()` works for any cart.)
- Every color is a **MOY64 palette index 0–63**, or a name via `col("red")` (see
  [Palette](#palette)). The canvas stores indices; the host resolves them to RGB for
  the window, the device maps them into the RGB565 framebuffer.
- Origin is top-left, `+x` right, `+y` down.

---

## Drawing

| call | does |
|---|---|
| `cls(c=0)` | clear the screen to color `c` |
| `pix(x, y, c)` | set one pixel |
| `line(x0, y0, x1, y1, c)` | line |
| `rect(x, y, w, h, c)` | **filled** rectangle |
| `rectb(x, y, w, h, c)` | rectangle **outline** |
| `circ(cx, cy, r, c)` | **filled** circle |
| `circb(cx, cy, r, c)` | circle **outline** |
| `print(s, x, y, c, scale=1)` | text (8×8 petme128 font, pixel-identical host↔device). `scale` is accepted but **ignored** — game text is always 8px (the Settings text-size option scales the SYSTEM UI only, #39). Honours `camera`/`clip`/`pal` like every primitive (native on device, #62) |
| `camera(x=0, y=0)` | offset all subsequent draws by `-x,-y` (world → screen). No args resets |
| `clip(x=None, y=None, w=None, h=None)` | clip drawing to a rect. No args resets to full screen |
| `pal(c0=None, c1=None)` | draw color `c0` as `c1` until reset. No args resets |
| `palt(c=None, on=None)` | make palette index `c` transparent (`on=True`) or not. No args resets |

## Sprites, sheets & tilemaps

Sprites are **8×8 tiles** from the cart's sprite sheet (editable in the on-device paint
editor). Tiles are referenced by integer id.

| call | does |
|---|---|
| `spr(n, x, y, colorkey=-1, scale=1, flip=0, w=1, h=1)` | draw sheet tile `n` at `x,y`. `colorkey` = transparent index (`-1` = opaque). `scale` enlarges. `flip`: `0` none, `1` horizontal, `2` vertical, `3` both. `w,h>1` draws a multi-tile sprite (e.g. `w=2,h=2` = 16×16). `n` may also be an `Image` |
| `spr_batch(items, colorkey=-1, scale=1)` | draw MANY 1×1 sheet tiles in one call. `items` = sequence of `(tile, x, y)` or `(tile, x, y, flip)`. The sprite analogue of `map()` — on device it's **one** native call for N sprites (draw-call count is the FPS bottleneck, so batch hot sprites) |
| `map(mx=0, my=0, w=None, h=None, sx=0, sy=0, colorkey=-1, scale=1)` | blit a `w×h` region of the cart's tilemap (top-left cell `mx,my`) to screen `sx,sy` |
| `mget(x, y)` / `mset(x, y, tile)` | read / write a tilemap cell (tile id; `mget` = `-1` if none) |
| `image(name)` | load a paint-image asset (`images/<name>.moyimg`) as a big `Image`; place with `spr(img, x, y)`. Memoised. `None` if absent |
| `image(rows, mapping, transparent=".")` | build a small `Image` from ASCII art, e.g. `image(["..##..","..##.."], {"#": 8})` |
| `Image(w, h, indices, transparent)` | a sprite bitmap object (also `Image.from_ascii(...)`) |

## Scroll layers (`#54`)

For scrollers, pre-render a wide level once and window-copy it each frame instead of
re-drawing the background every frame.

| call | does |
|---|---|
| `background(x)` | **declare the backdrop once** — a color (`background(col("dark_blue"))`) or a painted Image (`background(image("bg"))`) — and the engine repaints it at the start of every frame automatically. Your `_draw` then only draws the moving things: no `cls`, no backdrop blit, nothing to overdraw. `background()` with no args clears it |
| `make_layer(w, h)` | create an off-screen layer (wider than the screen). Draw into it once with the **same verbs** (`cls`/`map`/`spr`/`rect`/…) via the layer's methods |
| `draw_layer(layer, cam_x=0, cam_y=0)` | blit the visible `W×H` window of `layer` at the camera offset (clamped to the layer bounds). Draw actors on top afterwards |

## Scenes (placed actors, `#85`)

A **scene** is a saved table of placed actors — a sprite + a world position + a tag —
that your cart reads once in `_init` and spawns however it likes. Scenes live in the
cart's `scenes/<name>.moyscene` files (tiny JSON, one row per actor, list order =
spawn order = draw order); the manifest's `assets.scenes` lists them, and the first
entry is the default active scene. Pure data: `scene()` never draws anything, and a
cart with no scenes just gets an empty list.

| call | does |
|---|---|
| `scene()` | the ACTIVE scene's actors — a list of read-only rows with `.tag` (the kind your code branches on), `.tile` (sheet index), `.x`/`.y` (world-space), `.flip`, `.flags` (a dict of extras). Missing/empty scene → `[]` |
| `scene(name)` | a named scene's actors, WITHOUT switching the active one |
| `load_scene(name)` | switch the active scene (e.g. `level2`) and return its actors. Resets to the default on the next run. Unknown name → `[]`, active unchanged |

```python
def _init():
    global coins, px, py
    coins = []
    for a in scene():                 # spawn whatever was placed
        if a.tag == "coin":
            coins.append([a.x, a.y])
        elif a.tag == "player":
            px, py = a.x, a.y
```

Treat the rows as read-only — a game's *changing* state belongs in your own
variables (and `pmem` for what should survive), not written back into the scene.

## Reading documents (`table` / `text`, `#78`)

A game can read a **Sheets** sheet or a **Writer** doc that lives in its own cart
folder — the document IS the game data. Make the document in the Sheets/Writer app,
attach it to your cart (`tables/<name>.moysheet`, `docs/<name>.moytext`), then read
it back. Both are tiny kid-greppable JSON; a missing name reads as an empty list, so
these never crash your cart.

| call | does |
|---|---|
| `table(name)` | read the sheet `tables/<name>.moysheet` as **rows** — a list of lists of the sheet's computed values (numbers stay numbers, text stays strings, a blank cell is `""`). Missing name → `[]` |
| `text(name)` | read the Writer doc `docs/<name>.moytext` as **lines** — a list of strings, one per line. Missing name → `[]` |

```python
# A wave table authored in Sheets drives how many enemies each level spawns:
WAVES = table("waves")        # e.g. [[3], [5], [8], [12]]

def spawn(level):
    count = WAVES[level][0] if level < len(WAVES) else 20
    ...

# Dialog written in Writer, shown a line at a time:
LINES = text("intro")         # ["You wake in a cave.", "A torch flickers.", ...]

def _draw():
    cls(col("black"))
    for i, ln in enumerate(LINES):
        print(ln, 8, 8 + i * 10, col("white"))
```

---

## Make it fast (five habits)

Every draw call is native on the device, so the usual cost is not *how* you draw —
it's painting **more pixels than the frame needs**. Five habits keep any cart smooth
(measured on hardware, #66):

1. **Better yet: declare the background, don't draw it.** `background(col("dark_blue"))`
   (or `background(image("bg"))` for a painted backdrop) once in `_init` and the
   engine repaints it every frame for you — on the device the restore rides the
   async copy engine, so the backdrop costs (almost) nothing. The habits below are
   for when you draw the backdrop yourself:
2. **Your background IS the clear color.** `cls(col("dark_blue"))` already paints
   every pixel — don't follow it with a full-screen backdrop `rect()`. That paints
   the whole screen twice and costs ~7ms of the device's ~30ms frame budget for
   nothing. (Battle City does it right: one `cls` in the field color, then only the
   HUD strip repaints its own black.)
3. **Static scenery goes in a layer, once.** If your level or backdrop doesn't change
   every frame, draw it ONCE into `lay = make_layer(W, H)` — a layer speaks the whole
   drawing API (`lay.cls` / `lay.map` / `lay.rect` …) — and stamp it back each frame
   with `draw_layer(lay, 0, 0)`. One flat copy replaces `cls` + a full `map()`
   re-render, and it erases last frame's sprites for free. For scrolling worlds make
   the layer wider than the screen and pan with `draw_layer(lay, cam_x, 0)`.
   (Hop Quest and Sky Run both do exactly this — read their `_build_layer` /
   `_build_world`.)
4. **Lots of sprites? Just call `spr()` in a loop.** The engine coalesces consecutive
   `spr()` calls into one native batch automatically; `spr_batch()` is the manual form
   when you already build a list. Likewise one `map()` call always beats drawing tiles
   one by one.
5. **Never wrap `spr()` in `pal()` every frame — bake tinted copies once.** The
   engine caches each image pre-baked at one scale under the current palette; a
   `pal()` call invalidates that cache, so a `pal(...)`/`spr(...)`/`pal()` sandwich
   re-bakes the sprite pixel by pixel on EVERY draw (this alone once cost Letter
   Blitz most of its frame, #72). If you want the same art in several colors or
   sizes, build each variant once with `image(rows, {"#": the_color})` and keep it
   in a dict keyed by `(color, scale)` — then the play path is all cheap cached
   blits. `pal()` is still fine for one-off moments (a flash of damage on a
   full-screen repaint) — just not inside your per-frame sprite loop. (Letter
   Blitz's `_glyph`/`_tank_sprite` caches model the pattern.)

---

## Input

Buttons are named. The canonical set is `left, right, up, down, a, b, run, home`.

| call | returns |
|---|---|
| `btn(name)` | `True` while the button is **held** |
| `btnp(name)` | `True` on the frame it was **pressed** (the released→held edge) |
| `key(code=None)` | with a code (`key(ord("a"))`): is that ASCII key down this frame. No arg: the last key code (`0` if none). *One key at a time* (T-Deck reports 1 byte/frame) |
| `keyp(code=None)` | same, but only the press edge this frame |
| `touch()` | `(x, y, tapped, held)` in canvas space, or `None` if no pointer. `tapped` = press edge (one hit per tap); `held` = the finger/button is still down this frame, position following the drag (drawing, sliders) |
| `mouse()` | TIC-80 7-tuple `(x, y, left, middle, right, scrollx, scrolly)`; a tap = left. middle/right/scroll are always 0 on hardware |
| `textmode(on=True)` | opt a running cart into clean text-keyboard input (for typing a name/password) so `key()/keyp()` return typeable ASCII; `textmode(False)` restores game mode (held WASD/arrows drive `btn()`). Auto-resets to game mode on exit |

## Audio

| call | does |
|---|---|
| `sfx(n, chan=None)` | play sound effect `n` (optionally on channel `chan`) |
| `beep(freq, dur=0.15)` | a quick tone at `freq` Hz |
| `music(track, loop=True)` | start a music track |
| `music_stop()` | stop music |
| `sound_stop(chan=None)` | stop a channel (or all) |
| `volume(level)` | set output volume |

## State & utility

| call | does |
|---|---|
| `time()` | milliseconds since the cart started |
| `quit()` | END this cart and return to the launcher (or the editor it was run from). Bind it to a key or an on-screen ✕/back button. **Required** for a `textmode(True)` game — the console's BACKSPACE exit can't reach text mode |
| `pmem(index, value=None)` | persistent memory: `pmem(i)` reads an int, `pmem(i, v)` writes+persists (high scores, saves) |
| `cfg(key, default=None)` | read a value from the cart's `config.json` — the **"Make it mine"** tuning a kid edits (speed, counts, colors…) |
| `col(name_or_index)` | resolve a color **name** (0–15) or int to a `0–63` palette index |
| `rnd(n=1.0)` | random float in `[0, n)` |
| `flr(x)` | floor to int |
| `W`, `H` | canvas size (320, 240) |
| `wifi` | **only present** if the cart's manifest permissions include `"network"` (capability-gated, `#38`). A normal cart has no `wifi` name at all |

---

## Palette

The **MOY64** palette (`runtime/palette.py`) is 64 colors. Indices **0–15** are the
PICO-8 base (named, use `col("name")`); **16–63** are a curated desktop gamut (pastels,
earth tones, vivid accents, neutrals, deep shades) — pass those as integers.

| idx | name | idx | name | idx | name | idx | name |
|---|---|---|---|---|---|---|---|
| 0 | `black` | 4 | `brown` | 8 | `red` | 12 | `blue` |
| 1 | `dark_blue` | 5 | `dark_grey` | 9 | `orange` | 13 | `indigo` |
| 2 | `dark_purple` | 6 | `light_grey` | 10 | `yellow` | 14 | `pink` |
| 3 | `dark_green` | 7 | `white` | 11 | `green` | 15 | `peach` |

`col("green")` → 11, `col(40)` → 40, `col("nope")` → 7 (falls back to white).

---

## Why this API (portability)

The canvas works in **palette indices** and the API is **plain functions over a
buffer** — no dependency on `framebuf`, LVGL, or even Python in the contract. That's
deliberate: the same surface maps onto the host window (indices → RGB888), the
device's native `moy_compositor` RGB565 framebuffer (indices → RGB565 via the
palette), and the Lua cart VM (#67) — the "not even Python" clause is now shipping
code. **A cart authored once runs on every tier** (Zero /
Player / One) — see `docs/hardware_lineup.md` and issue #59. When you add a drawing
feature, add it to **both** `runtime/canvas.py` and the device path and keep the name
identical.

**Fuller example:** `system_carts/star_catcher.moy/main.py` (a complete game — sprites,
particles, `cfg` tuning, `pmem`, hearts/score UI).
