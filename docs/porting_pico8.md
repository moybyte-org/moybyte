# Porting a PICO-8 cart to KidCode

So you imported a PICO-8 `.p8` with `tools/import_p8.py` and now you have a
`.kcart`. The **art and sound came across automatically**, but the **code did
not run** — and that's on purpose. KidCode is **Python**, PICO-8 is **Lua**, so
you don't *run* a PICO-8 cart here, you **port** it. Porting it is how you learn
how two little game consoles say the same thing in different words.

The importer kept the original Lua inside your `main.py` as a big comment, with
`# PORT NOTE:` lines next to it for the tricky bits *your* cart uses. This page is
the **full map** — every verb, side by side.

## The 3 gotchas (read these first!)

These are the ones that bite everyone:

### 1. The fill names are SWAPPED

In PICO-8, `rect`/`circ` draw **outlines** and `rectfill`/`circfill` draw
**filled** shapes. In KidCode it's the other way around — `rect`/`circ` are
**filled**, and `rectb`/`circb` are the **outlines** (the `b` is for "border").

| You want… | PICO-8 | KidCode |
|---|---|---|
| filled rectangle | `rectfill` | `rect` |
| rectangle outline | `rect` | `rectb` |
| filled circle | `circfill` | `circ` |
| circle outline | `circ` | `circb` |

### 2. Rectangles measure differently

PICO-8 rectangles take the **opposite corner** `(x1, y1)`. KidCode rectangles
take a **width and height** `(w, h)`.

```
PICO-8:  rectfill(10, 10, 30, 20, 8)   -- corner at (30,20)
KidCode: rect(10, 10, 21, 11, 8)       #  w = 30-10+1 = 21, h = 20-10+1 = 11
```

So the rule is: `w = x1 - x + 1` and `h = y1 - y + 1`.

### 3. Buttons are NAMES, not numbers

PICO-8 asks for buttons by number 0–5. KidCode asks by name.

| PICO-8 | KidCode | What it is |
|---|---|---|
| `btn(0)` | `btn("left")` | left |
| `btn(1)` | `btn("right")` | right |
| `btn(2)` | `btn("up")` | up |
| `btn(3)` | `btn("down")` | down |
| `btn(4)` | `btn("a")` | the O button |
| `btn(5)` | `btn("b")` | the X button |

`btnp` (pressed-this-frame) works the same way — just use names.

## Verb-by-verb cheatsheet

### Drawing

| PICO-8 | KidCode | Notes |
|---|---|---|
| `cls(c)` | `cls(c)` | same; both default to color 0 |
| `pset(x,y,c)` | `pix(x,y,c)` | 3 args sets a pixel |
| `pget(x,y)` | `pix(x,y)` | 2 args reads a pixel |
| `line(x0,y0,x1,y1,c)` | `line(x0,y0,x1,y1,c)` | same |
| `rectfill(x,y,x1,y1,c)` | `rect(x,y,w,h,c)` | **filled**; corners → w,h (gotcha 1 + 2) |
| `rect(x,y,x1,y1,c)` | `rectb(x,y,w,h,c)` | **outline**; corners → w,h (gotcha 1 + 2) |
| `circfill(x,y,r,c)` | `circ(x,y,r,c)` | **filled**; same args (gotcha 1) |
| `circ(x,y,r,c)` | `circb(x,y,r,c)` | **outline**; same args (gotcha 1) |
| `spr(n,x,y)` | `spr(n,x,y)` | mostly same; see "Sprites" below |
| `sspr(...)` | — | no stretch-blit; use `spr(n,x,y, scale=N)` or skip |
| `print(s,x,y,c)` | `print(s,x,y,c)` | color is an index or `col("white")` |

### Colors

The palettes match! KidCode's first 16 colors **are** PICO-8's 16 colors, in the
same order — so a bare number like `8` is red in both. You can also write
`col("red")`, `col("white")`, etc. Names: `black, dark_blue, dark_purple,
dark_green, brown, dark_grey, light_grey, white, red, orange, yellow, green,
blue, indigo, pink, peach` (indices 0–15).

### Sprites

`spr(n, x, y)` draws tile `n` at `(x, y)` in both. KidCode's version is
`spr(n, x, y, colorkey, scale)`:

- **Transparency:** PICO-8 uses `palt(c, true)`; KidCode passes a `colorkey`
  straight to `spr` (e.g. `spr(3, x, y, 0)` makes color 0 see-through).
- **No multi-tile span** (`spr(n,x,y,w,h)`) and **no flip** (`spr(n,x,y,...,fx,fy)`)
  yet — draw big or flipped sprites tile-by-tile for now.
- **Scaling:** `spr(n, x, y, scale=2)` doubles the size.

### Buttons & input

| PICO-8 | KidCode |
|---|---|
| `btn(0..5)` | `btn("left"/"right"/"up"/"down"/"a"/"b")` |
| `btnp(0..5)` | `btnp("left"/"right"/"up"/"down"/"a"/"b")` |

### Sound

| PICO-8 | KidCode | Notes |
|---|---|---|
| `sfx(n)` | `sfx(n)` | the importer brought sounds across (lossy: 8 PICO-8 instruments folded to 4 waves, effects dropped) |
| `music(n)` | `music(n)` | the importer flattens PICO-8's 4 music channels to 1 |

### The cart loop

PICO-8 runs `_init()` / `_update()` / `_draw()`. KidCode is the same idea —
define `_init()`, `_update(dt)`, and `_draw()`. (KidCode's `_update` gets the
time since the last frame, `dt`, instead of you calling `t()`.)

## Not here yet → adapt or skip

These PICO-8 features have no KidCode equivalent today. The importer flags them in
your `main.py` when your cart uses them:

| PICO-8 | What to do instead |
|---|---|
| `map()` / `mget` / `mset` | No tilemap yet (coming in issue #32). Draw tiles by hand with `spr()` in a loop, or keep your level in a Python list. |
| `pal()` / `palt()` | No palette remap. Use the `spr` `colorkey` arg for transparency; skip remaps. |
| `camera(x,y)` | No camera. Subtract the camera offset yourself in your x,y math. |
| `fget` / `fset` | No sprite flags. Keep that info in your own Python dict or list. |
| `peek` / `poke` / `memcpy` | **No raw memory access — on purpose.** Rewrite that part with normal Python variables and lists. |

## A tiny worked example

PICO-8:

```lua
function _draw()
  cls(1)
  if btn(0) then x -= 1 end       -- left
  rectfill(x, 60, x+8, 68, 8)     -- filled red box, corner args
  circ(64, 64, 10, 7)             -- white outline circle
  spr(1, x, 60)
end
```

KidCode (Python):

```python
def _draw():
    cls(1)
    if btn("left"):                 # numbers -> names
        x -= 1
    rect(x, 60, 9, 9, 8)            # rectfill -> rect, corners -> w,h (8+1, 68-60+1)
    circb(64, 64, 10, 7)           # circ outline -> circb
    spr(1, x, 60)                  # spr is the same
```

Happy porting! Every verb you convert teaches you a bit more about how the two
consoles think.
