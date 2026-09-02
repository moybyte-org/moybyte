# Moybyte `.moy` cart API (current)

The drawing/input/audio API a **`.moy` cartridge** calls. This is the *current*
console API — it supersedes the legacy `.moyproj` SDK API in
[`moybyte_api.md`](history/moybyte_api.md) and the 128×128 `run/sprite/text` contract in
[`firmware_runtime_contract.md`](history/firmware_runtime_contract.md), both of which describe
the old parallel SDK.

**Source of truth (keep this doc in sync with them):** the API namespace is built by
`make_api()` in `runtime/host_app.py` (host reference) and the identical
`make_api()` in `firmware/lilygo_t_deck_plus_mainline/modules/moy_runtime.py`
(device). The drawing ops live in `device_canvas.DeviceCanvas` (the host builds
it through `runtime/host_canvas.py`); the palette in
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
  fast tier). **The simulator is the same**: since 2026-08-14 the host builds the
  boards' own vendored Lua 5.4 with the same `LUA_32BITS`, so float semantics and
  integer wrap are identical on every tier and a float-heavy cart does not drift
  between sim and device. (That used to be false — the host ran a second Lua with
  64-bit doubles, and closing it is what made golden-frame parity meaningful for
  float-heavy carts.)

*(Host note: a Lua cart on the PC simulator needs a **C compiler**, not a Python
dependency — the host compiles that same vendored VM on demand and caches it.
Without one, a Lua cart opens the "needs the Lua runtime" panel.)*

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
| `tri(x1, y1, x2, y2, x3, y3, c)` | **filled** triangle — *provisional*, see below |
| `trib(x1, y1, x2, y2, x3, y3, c)` | triangle **outline** — *provisional*, see below |
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
| `map(mx=0, my=0, w=None, h=None, sx=0, sy=0, colorkey=-1, scale=1, layers=0)` | blit a `w×h` region of the cart's tilemap (top-left cell `mx,my`) to screen `sx,sy`. With `layers` non-zero, only the cells whose **tile's** flags share a bit with it |
| `mget(x, y)` / `mset(x, y, tile)` | read / write a tilemap cell (tile id; `mget` = `-1` if none) |
| `fget(n)` / `fget(n, b)` | tile `n`'s flag byte / whether bit `b` (0–7) of it is set. `0` / `False` off the sheet |
| `fset(n, v)` / `fset(n, b, on)` | write tile `n`'s flag byte / set or clear one bit of it |
| `image(name)` | load a paint-image asset (`images/<name>.moyimg`) as a big `Image`; place with `spr(img, x, y)`. Memoised. `None` if absent |
| `image(rows, mapping, transparent=".")` | build a small `Image` from ASCII art, e.g. `image(["..##..","..##.."], {"#": 8})` |
| `Image(w, h, indices, transparent)` | a sprite bitmap object (also `Image.from_ascii(...)`) |
| `sspr(sx, sy, sw, sh, dx, dy, dw=None, dh=None, colorkey=-1, flip=0)` | **stretch** a `sw×sh` region of the sheet into a `dw×dh` rect. Source coords are sheet **pixels**, not tile ids. Unlike `spr`'s integer `scale` this is an arbitrary stretch — the textured wall-slice verb, and how you scale a sprite by a non-integer amount. `dw`/`dh` default to `sw`/`sh`. *Provisional*, see below |
| `tline(x0, y0, x1, y1, u, v, du, dv, colorkey=-1)` | a **textured** line: exactly `line()`'s pixels, but sampling the **tilemap** as a virtual texture. `u,v` is the start texture coord and `du,dv` the per-pixel step, all in **16.16 fixed point** — an integer, so a cart passes `int(f * 65536)`. Wraps modulo the map's pixel size; empty cells draw nothing. One call per scanline is a Mode 7 floor, one per column textures a raycaster. *Provisional*, see below |

### Tile flags, and drawing a level in strata

Every tile has a **flag byte** — eight bits you tag it with once, in the cart's
`flags.moyflags` file (SPEC.md 3.5), and every cell that uses that tile is tagged
with it. It is the tile-tagging idiom: *solid*, *spike*, *coin*, *layer 2*.

`fget` reads them (collision: `fget(mget(cx, cy), 0)` asks "is the tile at this
cell solid?"), `fset` writes them mid-run (a door that opens), and `map(...,
layers)` filters on them — which is how a level is drawn in strata from ONE map:
the ground with mask `1`, then the actors, then the foreground with mask `2` on
top. `layers` of `0` (or absent) is no filter at all, and a cart with no
`flags.moyflags` has all-zero flags, so a non-zero mask draws nothing there.

### The 3D verbs are provisional

`tri`, `trib`, `sspr` and `tline` are **moy core's**, not moybyte's — their pixels are
defined by [SPEC.md §6.1 and §7.1](https://github.com/moybyte-org/moy-spec), and moybyte
compiles the spec's own `libmoy` for them, so a cart draws the same triangle on every
conforming console. That is also why the signatures above are terse: **the spec is the
authority on what they draw**, and a fuller restatement here would be a second source of
truth that drifts.

"Provisional" is the spec's own word (§6.1): membership is settled but the semantics may
still move, and §11's conformance suite does not yet count them. A cart using them is fine
— the seed carts do — but they are the one corner of this API that could change under you.

### The batch verbs are gone (`spr_batch`, `rect_batch`, `spans`)

Moybyte used to have three verbs for handing the console a pre-packed list of sprites or
rects. **They were deleted on 2026-08-14** and there is nothing to replace them with:
write the plain loop.

```python
for e in enemies:            # this IS the fast path
    spr(e.tile, e.x, e.y, 0, 2)
```

Two reasons, and the second is the one that decided it. First, they bought almost nothing:
the console coalesces a contiguous run of `spr()` calls into one native batch by itself,
and an ordinary `rect` call is a few microseconds of dispatch, so a 160-wall raycaster
frame spends under a millisecond on call overhead. Second, **Lua carts could never call
them at all** — the bridge marshals numbers, not lists — so the same game written in the
two languages needed two different draw loops. A verb that only half the carts can use is
a split vocabulary, and moy core had already declined all three for the same reason:
*batching is the host's duty, and a cart is never asked to pre-pack its geometry*
([SPEC.md §6.1](https://github.com/moybyte-org/moy-spec)).

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
cart with no scenes just gets an empty list. Author them WYSIWYG in the Editor's
**Scene** tab: pick a sprite, tap the world to place an actor, tap one to select it,
drag to move, and set its tag in the props row — PLAY spawns what you placed.

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

### The actor world (`#109`)

`scene()` is the placement you authored (read-only, never changes). Its **live,
playable projection** is the *actor world* — the same actors, but you can move and
remove them and the changes stick from frame to frame. It's built for you from the
active scene the first time you touch it, and starts fresh from the scene on every
run. These are the cart-API mirrors of the **Actors** blocks (a coin collector is
~8 blocks) — a kid who graduates to code calls exactly what they used to click.

| call | does |
|---|---|
| `actors()` | a snapshot list of every live actor (mutable `.tag`/`.tile`/`.x`/`.y`/`.flip`) |
| `actors(tag)` | just the live actors whose `.tag == tag`. It's a **snapshot**, so you can `remove_actor` while looping over it without skipping anyone |
| `touching(a, b)` | `True` if actor `a` overlaps `b` (an 8×8-box AABB). `b` is another actor OR a tag string — a tag tests `a` against any *other* live actor of that tag |
| `move_actor(a, dx, dy)` | nudge actor `a` by `(dx, dy)` world pixels |
| `move_actor_to(a, x, y)` | place actor `a` at world `(x, y)` |
| `remove_actor(a)` | take actor `a` out of the world (a collected coin, a dead enemy) |
| `draw_scene()` | draw every remaining actor at its position with `spr` (list order = z-order, `.flip` respected) |

A full coin collector, in code (the **Actors** blocks compile to exactly this):

```python
score = 0

def _update(dt):
    global score
    for player in actors("player"):
        if btn("left"):  move_actor(player, -2, 0)
        if btn("right"): move_actor(player, 2, 0)
        if btn("up"):    move_actor(player, 0, -2)
        if btn("down"):  move_actor(player, 0, 2)
    for coin in actors("coin"):
        if touching(coin, "player"):
            remove_actor(coin)
            score = score + 1

def _draw():
    cls(col("dark_blue"))
    draw_scene()
    print("SCORE " + str(score), 4, 4, col("white"))
```

The **Actors** block category gives you the no-loop version of the same thing:
*for each `player` actor → move actor with the buttons*, *for each `coin` actor → if
actor touching `player`? → remove actor, change score by 1*, then *clear*, *draw
scene*, *write score*. Inside a *for each … actor* block, "actor" always means the
one it's currently looping over. (`system_carts/coin_quest.moy` is the built-in demo.)

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
   nothing. (Brick Siege does it right: one `cls` in the field color, then only the
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
   `spr()` calls into one native batch automatically — there is no manual form and you
   do not need one. Likewise one `map()` call always beats drawing tiles one by one.
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

**Which key is which.** The buttons are the same everywhere; the *keys* differ, because
a thumb keyboard and a desktop keyboard are not the same instrument:

| button | T-Deck | a keyboard with arrows (sim, browser) |
|---|---|---|
| `up` `left` `down` `right` | **W A S D** | **arrows** (W A S D also work) |
| `a` | **L** or **SPACE** | **Z** or **SPACE** |
| `b` | **K** | **X** |
| `run` | **ENTER** | **ENTER** |
| `home` | **BACKSPACE** | **BACKSPACE** |
| `stop` | — | **ESC** |

On the T-Deck the left thumb steers and the right thumb fires, both on the home row.
That board has **no arrow keys at all**, and its `Z`/`X` are bottom-row keys the left
thumb has to leave `WASD` to reach — so it gets `L`/`K`. A desktop keyboard has arrows
and comfortable `Z`/`X`, which is PICO-8's layout and every emulator's, so it gets
those. `SPACE` is jump and `ENTER` is `run` on both.

`run` is also the launcher's **open this cart**, so Enter confirms in menus.
`home` is the **exit**: hold it ~700ms while a game is running.

**Every other key is a plain character** a cart reads with `key()` / `keyp()`, firing
no button — including `R`, `H`, `J`, `Q`, `E` and (on the T-Deck) `Z`/`X`, all of which
used to be buttons. A typing game can use the whole keyboard without a letter also
moving the player; a stolen letter is a bug you only find by playing.

In **text mode** (`textmode(True)`, the code editor, a wifi password) no key fires a
button at all — even Backspace, Enter and space arrive as plain characters to type.

| call | returns |
|---|---|
| `btn(name, player=None)` | `True` while the button is **held**. With **no** `player` it means *any* controller — whatever is plugged in or paired drives your cart, which is what every single-player cart wants. `player=0` is this console's own controls, `player=1, 2, …` are **extra controllers** (see Multiplayer below) |
| `btnp(name, player=None)` | `True` on the frame it was **pressed** (the released→held edge). Takes the same optional `player` |
| `players()` | how many players are connected right now (**1** = just this console). Offer a 2-player mode when it's `>= 2` |
| `key(code=None)` | with a code (`key(ord("a"))`): is that ASCII key down this frame. No arg: the last key code (`0` if none). *One key at a time* (T-Deck reports 1 byte/frame) |
| `keyp(code=None)` | same, but only the press edge this frame |
| `touch()` | `(x, y, tapped, held)` in canvas space, or `None` if no pointer. `tapped` = press edge (one hit per tap); `held` = the finger/button is still down this frame, position following the drag (drawing, sliders) |
| `mouse()` | TIC-80 7-tuple `(x, y, left, middle, right, scrollx, scrolly)`; a tap = left. middle/right/scroll are always 0 on hardware |
| `textmode(on=True)` | opt a running cart into clean text-keyboard input (for typing a name/password) so `key()/keyp()` return typeable ASCII; `textmode(False)` restores game mode (held WASD/arrows drive `btn()`). Auto-resets to game mode on exit |
| `view(w, h)` | declare the cart's LOGICAL viewport: the console composites the centered `w`x`h` region of the 320x240 canvas at the biggest integer scale that fits the screen (a 128x128 PICO-8 port fills the P4 glass at 4x instead of the full canvas's 2x); touch coords stay in full canvas space. `view()` restores the full canvas; auto-resets each run |

**Declaring a smaller canvas (SPEC.md 1/3.1):** `manifest.json` may carry
`"canvas": "160x120"` or `"canvas": "128x128"` (default `"320x240"`) — the cart
then plays on a genuinely smaller raster: `W`/`H` report it, every verb clips to
it, and the console integer-scales it up centered on the screen. The set is
**closed** (those three sizes only); anything else is refused at start, never run
at the wrong dimensions. A 128x128 PICO-8 port that can spare 8 rows pairs this
with `view(128, 120)` so the 4:3 glass fills its height (2x on the handheld, 5x
on the P4) instead of letterboxing the square. Drawing a quarter of the pixels is
also the single biggest speed lever a port has.

**Declaring which input you use (`#42`):** `manifest.json` may carry an optional
`"input"` list naming the input groups a cart actually reads — any of `"buttons"`
(`btn`/`btnp`), `"touch"` (`touch()`), `"keyboard"` (`key`/`keyp`/`textmode`), e.g.
`"input": ["touch"]` for a touch-only game. It's purely a hint for surfaces that draw
optional controls (today: the web view's virtual gamepad + soft-keyboard summon —
`"buttons"` shows the d-pad + A/B, `"keyboard"` shows the ⌨ toggle; the HOME/exit
button always shows, so even a `["touch"]` cart stays exitable); it changes nothing about
which calls actually work. **Omit it and every control shows** (today's behavior,
zero regression) — only declare it once a cart's input is settled.

## Multiplayer (`#65`)

> **Needs a second controller or console.** With only this console's own controls,
> `players()` is `1` and `btn(name, p)` for `p > 0` is always `False`, so a cart that
> reads `player=0` is a normal single-player cart — nothing changes.

> **Where the players come from.** Every input producer — the built-in keyboard, a
> paired Bluetooth keyboard, the touch screen, a radio peer — owns its own *source*,
> and a source carries a player. Give one `player = 1` and it IS player 2: no
> transport to register, no netcode. Until somebody does, every source is player 0
> and `btn(name)`, `btn(name, 0)` and `players()` all answer exactly as before.

There is **one** multiplayer API and the way the extra players arrive (a second USB
gamepad, a phone over the web view, another Moybyte over the radio) is just a backend —
your cart never knows the difference.

**Two ways to get a second player today**, and your cart is written the same for both:

- **One console, two kids.** Pair a Bluetooth keyboard to a console that already
  has its own (a T-Deck), then Settings → **2 PLAYERS**: the built-in keyboard is
  player one and the Bluetooth one is player two, on one screen. Nothing else
  changes — `players()` just becomes `2`.
- **Two consoles, one game.** Both kids open the *same* game with the
  `"multiplayer"` permission, and the consoles find each other over the radio and
  play together, each on their own screen. There is nothing to set up: being in
  the same room is the whole handshake. The game restarts when your friend joins,
  because both consoles have to start from the same first frame.

> **Writing a game that works two-console:** always read `btn(name, 0)` and
> `btn(name, 1)` with an explicit player number, never bare `btn(name)`, for
> anything that moves the game. Bare `btn(name)` means "whoever is holding *this*
> console", which is a different answer on each screen. Keep it for things where
> that is what you want, like "press anything to restart".
>
> Two linked consoles run the *same game twice*, one on each screen, and they stay
> in step by trading only which buttons are held. That works as long as both sides
> compute the same thing from the same buttons: use `rnd()` for randomness (the
> console seeds it identically on both), and never make the game depend on the
> clock. `Brick Siege` and `Harpoon Pop` are both written this way if you want to
> read one.

**Shared screen (many controllers).** Read each player with the `player` argument.
Player 0 is always this console:

```python
def _update(dt):
    # player 0 (this console) moves the blue paddle, player 1 the red one
    if btn("up", 0):   blue.y -= 2
    if btn("down", 0): blue.y += 2
    if players() >= 2:
        if btn("up", 1):   red.y -= 2
        if btn("down", 1): red.y += 2
```

**Two consoles (shared game state).** A cart that declares the `"multiplayer"`
permission in its manifest also gets a tiny message API — send a bit of data to the
other console, and handle what arrives:

| call | does |
|---|---|
| `net.send(data)` | send `data` (a small value — a number, a short list/dict) to the other console |
| `on_net(fn)` | register `fn(msg)` — it's called once for **each** message that arrives, right before your `_update` runs |

```python
# manifest.json: "permissions": ["multiplayer"]
def _catch(msg):
    other.x, other.y = msg          # the friend's position arrived
on_net(_catch)

def _update(dt):
    net.send((me.x, me.y))          # tell the friend where I am
```

Both `net` and `on_net` are **only present** when the manifest grants
`"multiplayer"` — a normal cart has no `net` name at all (just like `wifi`).

> **Keeping two consoles in step:** if both sides run the same game logic, seed your
> randomness the same way on both (share a start seed and use it for `rnd()`), and keep
> game logic off the wall clock — otherwise the two screens drift apart.

## Pins — wires, lights and buttons (`#9`)

Some consoles have a **spare pin header**: the Moybyte Zero is one, a little
board with no screen that serves this console to your browser over WiFi. When
your cart is running on a console like that, it gets two extra verbs and can
turn things on and off in the real world.

| call | does |
|---|---|
| `pin_write(n, v)` | pin `n` goes high (`v` = 1) or low (`v` = 0). `True`/`False` work too |
| `pin_read(n)` | the last level pin `n` reported — `0`, `1`, or `None` before the first answer |

```python
LED = 21          # the Zero's own little light
BUTTON = 2        # a switch wired between pin 2 and ground

def _update(dt):
    if pin_read(BUTTON) == 0:      # pressed (the switch pulls the pin low)
        pin_write(LED, 0)          # this LED is ON when the pin is LOW
    else:
        pin_write(LED, 1)
```

**Your cart must ASK, in its manifest.** `"permissions": ["pins"]` -- the same
declaration `network` and `multiplayer` need, and for the same reason: a cart
that can move the wiring in somebody's room should have said so where a person
can read it before running it. Without it the names are absent even on a console
that has pins.

**And the names are only there when the console has pins.** On moybyte.com, on a
T-Deck or a P4, or in a browser tab you opened from a file, `pin_write` does
not exist at all and using it is a `NameError` — the same rule as `wifi` and
`net`. That is on purpose: a verb that pretends to work while nothing happens
is the hardest possible bug for a kid to see.

**They are not instant, and you should know how much.** Your cart runs in the
browser and the pins are on the board across the room, so a `pin_write` does
not go down a wire — it is **queued**, and the page sends everything that piled
up about **30 times a second**. So:

* a write lands roughly **one to two frames** after you make it, plus however
  long your WiFi takes;
* `pin_read(n)` answers with the **last thing the board said**, which is up to
  one of those round trips old — the first call, before any answer has come
  back, returns `None`;
* writing the same pin twice in one frame is fine — only the **last** value was
  ever real, and only it is sent.

None of that is enough to notice for a light, a buzzer or a button. It is not
fast enough to bit-bang a protocol, and it is not meant to be.

**Which pins?** Each board publishes its own list, and a pin that is not on it
is **refused, never driven** — the board needs some of its pins to stay alive
(its flash, its USB, its serial console), and a typo must not be able to reach
them. Ask for a pin outside the list and the console says so once, and nothing
happens. On the Zero the list is `1, 2, 4, 5, 6, 7, 8, 9` (the pads marked
`D0`, `D1`, `D3`, `D4`, `D5`, `D8`, `D9`, `D10`) plus `21`, the board's own
built-in LED.

> **On or off, and that is all — for now.** There is no `pin_pwm`, no servo and
> no motor verb yet: driving a motor takes a driver board between the pin and
> the motor, and that is `#9`'s next step rather than this one.

## Turning a cart into an APP (`#181`)

A cart whose manifest says `"type": "app"` is a tool rather than a game. The
console runs it **with its own top bar** — a title and an X — so it can never trap
you, and it can ask for a few of the console's own powers by naming them in
`permissions`, exactly like `"multiplayer"` above:

```json
"type": "app",
"permissions": ["graphics", "input", "files:docs", "prefs"]
```

| permission | what the cart gets |
|---|---|
| `files` / `files:<kind>` | `files.save_text(name, text)` / `load_text` / `list` / `new_name` / `rename` / `delete` — one kind only (`docs` = your documents, the ones Writer and Files show) |
| `prefs` | `prefs.get(key)` / `prefs.set(key, value)` — settings that survive a reboot, in this app's own corner |
| `appearance` | `set_theme(name)` / `themes()` |
| `launch` | `open_app(id)` |

Every app cart also gets four names with no permission needed, because they are
how an app draws rather than what it may touch: **`screen()`** (the canvas),
**`theme()`** (the console's live colors), **`bar_h()`** (how many rows the top
bar owns — draw below them) and **`ui`**, the console's own widget toolkit
(buttons, rows, panels, rect maths, `ui.Hits`). Storage answers `(value, error)`
and never crashes, so there is no `try` to write.

Anything you did not ask for **is not there** — no `carts`, no shell. Writing
that name is an ordinary "name is not defined" error, like a typo.

`system_carts/notes.moy` is a small worked example: it types, saves, and lists
what it saved. The full rules (what is never grantable, and how to make an app
reflow to a big screen with `_layout(w, h, fs)` instead of drawing at a fixed
320×240) are in `docs/app_api_v1.md`.

## Audio

| call | does |
|---|---|
| `sfx(n, chan=None)` | play sound effect `n` (optionally on channel `chan`) |
| `beep(freq, dur=0.15)` | a quick tone at `freq` Hz |
| `music(track, loop=True)` | start a music track |
| `music_stop()` | stop music |
| `sound_stop(chan=None)` | stop a channel (or all) |
| `volume(level)` | master output level, **0–7** (the same scale as a note's `vol`) |

`beep()` plays at the exact frequency you ask for, on the engine's own
oscillator — it never takes a channel away from music or an effect.

The sound bank lives in the cart's `sounds.json` (authored in the Music tab).
Since #170 the model is PICO-8-parity:

- A note is `[pitch, wave, vol]` or `[pitch, wave, vol, effect]`. `pitch` is a
  semitone index 0–95 (57 = A4 = 440 Hz, −1 = rest), `vol` 0–7.
- **8 waveforms**: `0` square, `1` triangle, `2` saw, `3` noise, `4` pulse,
  `5` organ, `6` tilted saw, `7` phaser.
- **Effects** (optional 4th field, PICO-8 numbering): `1` slide (glide from the
  channel's previous note), `2` vibrato, `3` drop, `4` fade in, `5` fade out,
  `6`/`7` arpeggio fast/slow over the note's group of four.
- A music track's pattern **row** is one SFX id — or a **list of up to 4 ids**
  (one per channel, `-1` = silent), so a track can play real multi-part music.
  Music claims voices from the top (a 1-channel track uses only channel 3);
  `sfx()` round-robins whatever channels music leaves free.
- A looping SFX may set `loop_start` (play everything once, then repeat from
  there — p8's loop range), and a track may carry `row_secs`, per-row
  durations in seconds (`0` = hold the row forever) — how a p8 song's
  changing pattern lengths survive the import.

- A note with `vol` `0` but a real pitch is a **keyed rest**: silent, but still
  the note a following slide glides *from*. Only pitch `−1` leaves that origin
  untouched. (PICO-8 works this way, so ported slides land right.)

Imported PICO-8 carts (`tools/import_p8.py` / `moy port`) carry all of this
over verbatim — waves, effects, keyed rests and all four music channels. The
two tools land on the *same* `sounds.json` because they are the same converter:
moy-spec's `p8_import.py`, vendored here as `tools/p8_import.py`. They differ
only in what they do with the cart's *code* — `moy port` writes Lua plus a p8
compat shim, `tools/import_p8.py` writes a Python stub for you to port into.

**One number worth knowing if you read a `.p8` by hand:** PICO-8's tracker
labels its pitch `0` as "C0", but its synth tunes pitch `33` to 440 Hz, so its
labels sit two octaves below concert naming. The import adds **24**, which is
why a p8 `21` (33) arrives here as pitch `57` — A4, the same note you heard in
PICO-8.

**Instruments are not equally loud, on purpose.** The triangle family is about
twice the square family, which is PICO-8's own mix; music is balanced against
it, and evening them out would make every square lead shout down its own
accompaniment. The synthesis is [SPEC.md §8.3](https://github.com/moybyte-org/moy-spec)
exactly — moybyte compiles moy-spec's `libmoy` for it — so a cart sounds the
same here as on any other console that implements the spec.

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
| `net` / `on_net` | **only present** if the cart's manifest permissions include `"multiplayer"` (capability-gated, `#65`). See **Multiplayer** above |

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
Player / One). When you add a drawing
feature, add it to the ONE canvas class (`device_canvas.DeviceCanvas`) and keep the name
identical.

**Fuller example:** `system_carts/star_catcher.moy/main.py` (a complete game — sprites,
particles, `cfg` tuning, `pmem`, hearts/score UI).
