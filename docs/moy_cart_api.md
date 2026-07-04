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
below is pre-injected as a global.

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

## The canvas

- **320×240**, indexed. `W` = 320, `H` = 240 are globals (read them; don't assume).
- **A running cart owns the FULL canvas** — the console's top bar auto-hides during
  play and appears only in the pause menu (HOME / `q` on the T-Deck, the ☰ button on
  the web page) and on the crash panel. Don't reserve rows for system chrome.
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
| `print(s, x, y, c, scale=1)` | text (8×8 petme128 font; `scale=2` = 16px tall) |
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
| `make_layer(w, h)` | create an off-screen layer (wider than the screen). Draw into it once with the **same verbs** (`cls`/`map`/`spr`/`rect`/…) via the layer's methods |
| `draw_layer(layer, cam_x=0, cam_y=0)` | blit the visible `W×H` window of `layer` at the camera offset (clamped to the layer bounds). Draw actors on top afterwards |

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
deliberate: the same surface maps onto the host window (indices → RGB888) and the
device's native `moy_compositor` RGB565 framebuffer (indices → RGB565 via the
palette), and eventually a Lua VM. **A cart authored once runs on every tier** (Zero /
Player / One) — see `docs/hardware_lineup.md` and issue #59. When you add a drawing
feature, add it to **both** `runtime/canvas.py` and the device path and keep the name
identical.

**Fuller example:** `system_carts/star_catcher.moy/main.py` (a complete game — sprites,
particles, `cfg` tuning, `pmem`, hearts/score UI).
