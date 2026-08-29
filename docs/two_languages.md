# Two languages, one console

Moybyte carts are written in **Python** or in **Lua**, and both speak the *same*
console. `spr(1, x, y)` is `spr(1, x, y)` in either. There is one verb table
(`docs/moy_cart_api.md`), one palette, one cart loop — what changes between the
two is the language *around* the calls, and that is what this page is for.

**You are probably here because you opened a cart in a language you don't
write.** A cart imported from PICO-8 arrives as Lua (see the last section), and
two of the seed carts ship as line-for-line twins on purpose:
`system_carts/sakura.moy/main.py` and `system_carts/sakura_lua.moy/main.lua` are
the same game in the two languages, so you can read one beside the other.

**Which one is a cart?** Its `manifest.json` says: `"runtime": "python"` with
`"main": "main.py"`, or `"runtime": "lua"` with `"main": "main.lua"`. The code
editor shows you whichever file the manifest names.

---

## The cart loop is the same

Both languages define the same three functions, and the console calls them.

```python
# Python
def _init():
    ...

def _update(dt):    # dt = seconds since the last frame
    ...

def _draw():
    cls(0)
    spr(1, x, y)
```

```lua
-- Lua
function _init()
    ...
end

function _update(dt)    -- dt = seconds since the last frame
    ...
end

function _draw()
    cls(0)
    spr(1, x, y)
end
```

`_init` is optional in both. `_update(dt)` gets the time since the last frame in
seconds, so movement is `x = x + speed * dt` in either language.

---

## The differences that will actually bite you

### 1. Lists start at 1 in Lua, at 0 in Python

This is the one that catches everybody.

| | Python | Lua |
|---|---|---|
| first item | `stuff[0]` | `stuff[1]` |
| last item | `stuff[-1]` | `stuff[#stuff]` |
| how many | `len(stuff)` | `#stuff` |
| walk it | `for item in stuff:` | `for _, item in ipairs(stuff) do` |
| count 0…9 | `for i in range(10):` | `for i = 0, 9 do` |
| count 1…10 | `for i in range(1, 11):` | `for i = 1, 10 do` |

Note the loop bounds: Python's `range` **stops before** its end, Lua's numeric
`for` **includes** it.

### 2. Lua closes blocks with `end`; Python uses indentation

```python
if hp <= 0:
    dead = True
elif hp < 3:
    flash = True
else:
    flash = False
```

```lua
if hp <= 0 then
    dead = true
elseif hp < 3 then
    flash = true
else
    flash = false
end
```

Every `if` / `for` / `while` / `function` in Lua ends with `end`. Indentation is
just for you to read — but indent anyway.

### 3. Small words, different spellings

| | Python | Lua |
|---|---|---|
| not equal | `!=` | `~=` |
| nothing | `None` | `nil` |
| true / false | `True` / `False` | `true` / `false` |
| and / or / not | `and` `or` `not` | `and` `or` `not` (same!) |
| comment | `# like this` | `-- like this` |
| join two strings | `"a" + "b"` | `"a" .. "b"` |
| string to screen | `str(n)` | `tostring(n)` |
| whole-number divide | `a // b` | `a // b` (same!) |
| add one | `n += 1` | `n = n + 1` — **Lua has no `+=`** |

### 4. Only `nil` and `false` are false in Lua

In Python, `0` and `""` and an empty list are all falsy. In Lua they are **true**.

```python
if count:            # false when count == 0
```

```lua
if count > 0 then    -- say what you mean; `if count then` is true at zero
```

### 5. Lua variables are global unless you say `local`

```python
def tick():
    speed = 2        # local to this function, automatically
```

```lua
function tick()
    local speed = 2  -- without `local` this is a GLOBAL
end
```

Globals work, and the cart loop needs a few (your player position has to survive
between `_update` calls). But a stray global inside a helper is a bug that shows
up somewhere else, so reach for `local` by default.

### 6. Tables do both jobs

Lua has one container. A Python list and a Python dict are both a Lua table.

```python
petals = []
petals.append({"x": 10, "y": 0})
p = petals[0]
p["y"] += 1
for p in petals:
    ...
```

```lua
petals = {}
petals[#petals + 1] = {x = 10, y = 0}
local p = petals[1]
p.y = p.y + 1
for _, p in ipairs(petals) do
    ...
end
```

`p.y` and `p["y"]` mean the same thing in Lua.

### 7. Rounding down is not the same on negative numbers

Python's `int()` **truncates toward zero**; Lua's `math.floor` always goes
**down**. They agree on `3.7` and disagree on `-3.7` (`-3` vs `-4`) — which is
exactly the case a wrapped sprite hits. `sakura_lua.moy` carries the fix as a
three-line helper:

```lua
local function trunc(v)
    if v >= 0 then return math.floor(v) end
    return -math.floor(-v)
end
```

The console's own `flr()` is Lua's floor in both languages, so use `flr` when
you want *down* and `trunc` when you want *toward zero*.

---

## The console verbs are identical

Same names, same arguments, same order. A few examples — the full table is
`docs/moy_cart_api.md`.

| what | both languages |
|---|---|
| clear the screen | `cls(0)` |
| a pixel | `pix(x, y, c)` — with two arguments it *reads* one |
| filled / outline rect | `rect(x, y, w, h, c)` / `rectb(...)` |
| filled / outline circle | `circ(x, y, r, c)` / `circb(...)` |
| a sprite | `spr(n, x, y, colorkey, scale, flip, w, h)` |
| text | `print(s, x, y, c)` |
| a button | `btn("left")`, `btnp("a")` |
| a sound | `sfx(n)`, `music(n)` |
| the tilemap | `map(...)`, `mget(x, y)`, `mset(x, y, id)` |

Colours are palette indices `0..15` for the classic set (`8` is red in both), or
`col("red")` by name — `col` works in Lua too.

Two shapes are worth calling out because they differ in *style*, not in name:

* **Layers use a colon in Lua.** `lay = make_layer(w, h)` then `lay:spr(img, x,
  y)` where Python writes `lay.spr(img, x, y)`. The colon is Lua's way of passing
  the object itself as the first argument.
* **`touch()` returns several values in Lua**, not a tuple: `local x, y, tapped,
  held = touch()`, or `nil` when nothing is touching. Nothing is allocated per
  call, which is the point of it.

---

## If your cart came from PICO-8

Dropping a `.p8` or `.p8.png` on the console converts it and it **runs** — the
cart's own Lua, mechanically converted to Lua 5.4, underneath a generated
compatibility shim that spells PICO-8's verbs in terms of the console's.

Two things to know before you edit it:

1. **The generated part is the top of the file** — the flag table and the block
   between `PICO-8 compatibility shim` and `end shim`. The cart's own code is
   everything below that, and that is the part to change.
2. **`_init` / `_update` / `_draw` were renamed `p8_init` / `p8_update` /
   `p8_draw`.** The shim owns the real ones so it can pace the cart at PICO-8's
   fixed 30 frames a second. Rename yours the same way if you add one.

The import prints a short compatibility report naming the PICO-8 verbs *your*
cart calls that the shim has no answer for, or answers differently. That list is
the honest edge of the conversion; everything else came across.
