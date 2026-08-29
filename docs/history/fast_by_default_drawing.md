# Fast-by-default drawing: folding the perf primitives into the canonical API

**Status: ARCHIVED (2026-07-08) — every fold now has a verdict; this doc is
design history.** Fold 1 (auto-batch `spr()`) SHIPPED as the native `spr_gate`
(#63). Fold 2 (auto map cache) was built, A/B'd on hardware, and **REVERTED**
(Brick Siege map 4.3–5.7 → 13.4ms: the keyed full-region composite reads every
pixel where the direct raster skips transparent tiles; machinery stays behind
`MAP_AUTO_CACHE=False`). Fold 3 landed as the **`background()`** declared-backdrop
verb (docs/moy_cart_api.md). Fold 4's spirit landed as pal-state-id variant
caching (#72). Frame pacing shipped as the governor machinery
(`console.FPS_GOVERNOR`, policy currently OFF — owner measurement mode).
Current numbers + verdicts live ONLY in the **#66 performance ledger**.

**Audience:** engine/runtime. Read `docs/history/perf_60fps_architecture.md` (the perf
grounding), `docs/porting_pico8.md` (the API-alignment surface), and
`docs/moy_cart_api.md` (the current cart/device API) alongside this.

> *[Editorial note, 2026-08-29 — the sentence above is left as written.
> `docs/porting_pico8.md` was the hand-port-to-Python guide; it became
> `docs/two_languages.md` when the browser's PICO-8 drop started emitting a
> Lua cart that RUNS, so there is no port to guide. What this doc wanted from
> it — the PICO-8/TIC-80 API-alignment surface these folds were designed
> against — is `docs/moy_cart_api.md`, already named beside it.]*

Related issues: #43 (draw-call ceiling / native batch / dirty-rect), #54 (scroll
engine — `make_layer`/`draw_layer`), #32 (native `map()`), #11 (TIC-80-shaped API +
the global-vs-namespaced rule), #62 (native text), #58 (ESP32-P4 2D accel), #6
(runtime decision).

---

## 0. TL;DR

A performance lever a kid has to *know about and reach for* is a failed
abstraction. Moybyte currently exposes three such levers as first-class verbs —
`spr_batch`, `make_layer`, `draw_layer` — and a fourth gap (no way to use a
paint-app picture as a background without re-encoding it as thousands of draw
calls). This doc folds all four into the **canonical, TIC-80-shaped surface**, so a
kid's naive code is fast because they wrote the obvious thing:

| Fold | Kid writes (unchanged, canonical) | Engine does (automatic) | Replaces |
|---|---|---|---|
| **1. Auto-batch `spr()`** | `spr(n, x, y)` in a loop | coalesces contiguous sprite runs into one native `blit_batch` | explicit `spr_batch` |
| **2. Auto-cache `map()`** | `camera(...)` + `map()` | caches the rasterized tilemap, window-copies when only the camera moved | explicit `make_layer`/`draw_layer` (common case) |
| **3. Paint-image assets** | `background(img)` / `spr(img, x, y)` | bakes the 64-color image → 565 layer once (native `blit_indices`), blits per frame | the 32k-`rect` background-paint anti-pattern |

Design rule (from #11): **the canonical fantasy-console core stays global and
simple; the cleverness lives in the engine; Moybyte-specific escape hatches are
namespaced under `moy.*`.** The explicit primitives don't disappear — they demote
to rarely-needed `moy.*` escape hatches for the cases automation can't catch.

**Performance frame of reference (current device, post-#43/#54):** the flush is
~15 ms and is now *hidden* — `DOUBLE_BUFFER`/`ASYNC_FLUSH`/`PSRAM_DIRECT_FLUSH` make
frame time ≈ `max(render, flush)`. So **render (draw) is the wall, and it is
draw-*call*-bound**, not pixel-fill-bound. Every fold below is aimed at render, not
flush.

---

## 1. Guiding principle: fast by default

The three reference consoles teach the API vocabulary but not the constraint we
actually have. Kids will write the naive loop:

```python
def _draw():
    cls(12)
    map()                 # scrolling level
    spr(hero, hx, hy)     # actors
    for e in enemies:
        spr(e.tile, e.x, e.y)
    print(score, 4, 4)
```

On today's engine that hits three cliffs — a per-frame `map()` re-raster
(~12–14 ms), N per-sprite MP→C calls (the #43 wall), and (if the background is a
painting) a load-time avalanche of `rect()` calls. The kid can't be expected to
know `make_layer`/`spr_batch` exist. So the engine must make the naive loop fast:

1. **Auto-batch `spr()`** — the sprite loop collapses to one native call.
2. **Auto-cache `map()`** — the scroll becomes a window-copy.
3. **Paint-image assets** — a painted background is data + one cheap blit.
4. (**Dirty-rect**, #43 — the general umbrella for mostly-static screens; out of
   scope here but the fold that catches everything else.)

This mirrors how real 2D engines work: **immediate-mode surface, retained
cleverness underneath.**

---

## 2. Fold 1 — auto-batch `spr()`

### 2.1 The problem
`spr_batch` (#43) collapses N per-sprite MP→C `blit565` calls into one
`blit_batch`. It's the single biggest draw-side lever (draw is call-bound). But it's
an explicit verb: a kid must know to build an items list and call it. Measured on
`sakura.moy`: 120 petals as per-petal `rect`/`pix` = **160 device draw calls/frame**
→ ~10 fps; rewritten to one `spr_batch` = **2 calls/frame** (`draw_layer` +
`spr_batch`), pixel-identical.

### 2.2 The mechanism: a state-break sprite batcher
The canvas holds a **pending sprite batch**. `spr()` (sheet-tile form) appends to it
instead of blitting immediately. The batch **flushes** whenever draw order or batch
state would otherwise be violated — this is the whole correctness story:

A flush is triggered by:
- any **non-`spr` primitive** (`cls`/`rect`/`line`/`circ`/`print`/`map`/`draw_layer`/…),
- a change in **`colorkey` or `scale`** (`blit_batch` takes these once per call),
- a change in **`camera`/`clip`/`pal`/`palt`** (baked into the call/atlas via `_palgen`),
- an **`Image` sprite** or a **multi-tile** `spr(…, w>1/h>1)` (not a 1×1 sheet-tile
  blit — draw immediately after flushing the pending batch),
- **end of the cart's `_draw()`** (the frame loop flushes before compositing chrome / presenting).

`flip` is per-item (`blit_batch` supports it), so a flip change does *not* break the
batch. A single-item flush should fall back to `blit565` directly (no regression for
a cart that draws one sprite between rects).

### 2.3 Why it's safe
A contiguous run of `spr()` is already **pixel-identical** to `spr_batch` — proven
by the sakura parity test (0 pixel diffs across all four blossom colorways). So
coalescing changes nothing on screen. The device path is exactly today's
`blit_batch`; the host path is the existing per-item reference loop.

### 2.4 Costs / risks
- **Cross-cutting:** every primitive must flush the pending batch first, and so must
  the console's own chrome draw (top-bar icons are sprites). Not a localized change.
- **Deferred semantics:** `pix(x, y)` used as a *read* right after a `spr()` (before
  flush) would read stale. Rare in kid carts; document it.
- **Z-order bugs** if a flush point is missed. Mitigated by the golden test below.

### 2.5 Acceptance
- [ ] `spr()` (1×1 sheet-tile) batches; all listed state-breaks flush.
- [ ] Golden-frame parity test: representative scenes rendered immediate vs. batched
      are byte-identical (extend the device-canvas parity suite).
- [ ] `spr_batch` stays as `moy.spr_batch` — a thin alias feeding the same batcher,
      for generated/blocks code.
- [ ] `sakura.moy` reverts its hand-rolled batch to a plain `spr()` loop with no FPS
      change.

---

## 3. Fold 2 — auto-cache `map()`

### 3.1 The problem
The scroll engine (#54) proved the win: **Sky Run** pre-renders its world into a
`make_layer` once and window-copies each frame → **~42 fps** (render ≈ 12.7 ms);
**Hop Quest** re-renders its tilemap every frame → **~24–29 fps** (render ≈
22–36 ms). But the win requires the cart author to know `make_layer`/`draw_layer`.
A kid writing plain `camera()` + `map()` pays the full re-raster.

### 3.2 The mechanism
`map()` gains an internal render-cache:
- First call rasterizes the tilemap region into a hidden 565 layer, keyed by
  `(tilemap.gen, sheet.gen, scale)`.
- Subsequent calls where only the **camera** changed **window-copy from the cache**
  (`blit_window`, the #54 primitive) instead of re-rastering.
- `mset`/`pal`/`palt`/sprite-sheet edits bump a gen → cache drops → next `map()`
  re-rasters.

The kid writes the plain TIC-80 thing and gets the Sky Run number for free — and
existing naive `map()` carts (Hop Quest) speed up **without being rewritten**.
Redrawing the background region each frame still erases last frame's actors for
free, exactly like today.

### 3.3 Why `map()` is foldable and a raw background isn't
`map()` has **declared intent**: the tilemap asset already states the world's size
and content, so the engine knows *what* to cache and *when* it's stale. A background
built from an arbitrary pile of `rect`/`circ`/`spr` calls declares nothing — caching
it means *inferring* "these calls repeat every frame," which is the general,
expensive problem (**dirty-rect**), not a `map()` cache.

### 3.4 Cache policy & memory
- **Small worlds** (≤ a few screens): cache the whole rasterized map (the `make_layer`
  approach). A 320×240 slice is 153 KB in 565.
- **Big worlds:** a whole-world cache doesn't fit PSRAM (a 10-screen level ≈ 1.5 MB).
  Use a **scrolling window cache** (viewport + margin, re-raster the leading edge as
  the camera advances). This is *more* scalable than `make_layer` (which required the
  whole world resident, hence Sky Run's 2.5-screen cap), at the cost of edge-fill
  logic.

### 3.5 The escape hatch survives
`make_layer`/`draw_layer` demote to `moy.layer`/`moy.draw_layer` for what auto-caching
can't cover: **multi-layer parallax**, **off-screen effect buffers**, and
**non-tilemap render targets**. 95% of games (tilemap scrollers) never touch them.

### 3.6 Costs / risks
- Cache **invalidation** is the classic footgun (stale-background bugs); gen-counters
  make it tractable.
- Adds hidden state to `map()`, like the batcher adds to `spr()`.
- The big-world window cache is real engineering (incremental edge raster), but it
  reuses `_Layer`/`blit_window` from #54.

### 3.7 Acceptance
- [ ] `map()` window-copies on camera-only change; re-rasters on gen bump.
- [ ] Hop Quest-style cart measured on hardware: approaches Sky Run's render time
      with no cart change.
- [ ] Parity test: auto-cached `map()` output == direct-raster `map()` output.
- [ ] `moy.layer`/`moy.draw_layer` retained for the escape-hatch cases.

---

## 4. Fold 3 — paint-image assets (the sakura / paint-app case)

### 4.1 The problem
A kid draws a detailed **64-color** background in the **paint app** (distinct from
the 16-color sprite editor and the map editor) and wants it in their game. It can't
be a sprite (sheet is 4-bit / 16-color) or a tilemap (254-tile cap, 16-color tiles).
The sakura anti-pattern encoded such an image as a compressed blob **replayed as
`rect()` calls** — measured at **32,289 `rect()` calls** to paint one 320×240
background (seconds of load on-device).

### 4.2 The reframe: data, not draw calls
The paint app must hand a game **data** — a flat MOY64 index bitmap — never draw
calls. This is precisely Picotron's model (images are `userdata` index bitmaps you
blit) and exactly what sakura's blob *decodes to*. Treated as data + a real blit
primitive, the 32k-call load never happens. This use case has **no PICO-8/TIC-80
equivalent** — it is a Picotron-shaped feature, appropriate because Moybyte is a
64-color workstation.

### 4.3 The third asset type
| Asset | Colors | Unit | Editor | Lineage | Use |
|---|---|---|---|---|---|
| Sprite | 16 | 8×8 tile | sprite editor | TIC-80/PICO-8 | animated actors, tiles |
| Tilemap | (sprite tiles) | cell grid | map editor | TIC-80/PICO-8 | levels |
| **Paint image** | **64 (MOY64)** | **arbitrary bitmap** | **paint app** | **Picotron** | **detailed backgrounds / art** |

Stored per-cart as a flat, compressed MOY64 index bitmap (proposed `images/*.moyimg`;
sakura's `_BG` is this format in disguise). Loaded like `sprites.moygfx` /
`map.moymap` by the cart loader + `gen_device_carts`.

### 4.4 Pipeline
1. **Author:** paint app → `.moyimg` (index bitmap, compressed).
2. **Bake once (load / first use):** native `blit_indices` converts indices → a
   cached 565 layer in **one C call** (~1–3 ms), *not* 32k rects. Host parity: the
   layer buffer is already indices, so the "bake" is a slice copy.
3. **Per frame:** window-copy / blit the cached layer (`draw_layer` machinery), or
   under dirty-rect only the changed regions. Redraw erases last frame's sprites for
   free.

### 4.5 The `blit_indices` native op
The one new kernel (`moy_gfx`), same family as `blit565`/`blit_window`:

```
blit_indices(dst565, dst_w, dst_h, x, y, indices, iw, ih, pal565[, clip...])
    # per pixel: dst565[...] = pal565[indices[...]]   (clamped to dst)
```

- Device: converts index→565 into the target 565 layer, one call.
- Host: `Canvas` method that writes indices straight into the (index) buffer — a
  slice copy; no conversion (host is already index-space).
- Runs at **cart load**, off the per-frame hot path — zero interaction with the
  async flush / frame budget.

### 4.6 API surface
- **Composable (canonical):** `spr(img, x, y)` — `spr()` already accepts an `Image`;
  a paint image is just a big sprite you can place anywhere (background *or* a
  mid-screen decoration). Its 565 bake is cached per image (like the per-sprite RGB
  cache, but baked via `blit_indices`).
- **Kid-obvious sugar:** `background(img)` — sets the auto-managed backdrop
  (redraw + sprite-erase handled). Lead with this for a first game.

### 4.7 The cost that doesn't vanish: memory
- A full-screen 64-color image baked to 565 is **~153 KB PSRAM each.** Sprites and
  tilemaps are tiny; painted images are not. **Bake lazily** (only the active
  background) and budget the count.
- A **large scrolling painted world** is RAM-bound: unlike a tilemap it can't be
  incrementally re-rastered, so it must be fully resident (per-frame SD streaming is
  far too slow — SD shares the display SPI). Single-screen / few-screen painted
  backgrounds are the comfortable zone.
- Upside: a **static** painted background is the ideal dirty-rect case — draw once,
  re-composite only where sprites moved.

### 4.8 Acceptance
- [ ] `.moyimg` format + paint-app save + loader/`gen_device_carts` wiring.
- [ ] `moy_gfx.blit_indices` native op + host-parity `Canvas` method + parity test.
- [ ] `spr(img, x, y)` and `background(img)` surface; per-image 565 cache.
- [ ] `sakura.moy` re-expressed: `_BG` → a `.moyimg`, `_paint_bg` → one bake at load,
      `draw_layer` unchanged. Load drops from 32k calls to ~ms.

---

## 5. What we deliberately do NOT do (recorded so it isn't re-litigated)

- **Index framebuffer pivot** (device draws in indices, convert at flush). Under the
  current async model it's a **net loss**: `PSRAM_DIRECT_FLUSH` removed the staging
  copy the convert could have fused into; flush is already hidden; draw is call-bound
  so cheaper fills barely help; and the full-frame index→565 convert lands as
  net-new CPU on the render critical path (~2–5 ms), with no hardware CLUT on the S3
  to offload it. **Only** attractive on **ESP32-P4** (#58), which has a 2D
  pixel-processing accelerator that could do the convert in hardware. Rejected for
  the S3.
- **Tilemap-as-image-codec** (encode a 64-color background as ~926 unique tiles + a
  40×30 map to reach `blit_map`). A workaround for the 16-color sheet cap; superseded
  by paint-image assets (§4), which are the honest representation.
- **Binary `.tic` / `.p8` cart compatibility.** Low value for a kids' console (kids
  won't import third-party carts), huge cost (Lua VM + exact memory/sound model).
  Defer — #11's Level 3.

---

## 6. Where this leaves the API (ties to #11)

Post-fold, the drawing surface is cleanly split. Note the base-16 MOY64 palette
`[0..15]` **is the PICO-8 palette exactly**, and sprites are 16-color — so
sprite/palette semantics are already reference-compatible.

**Global (canonical fantasy-console core — identical whether the cart is Python or
later Lua):**
`cls · pix · line · rect · rectb · circ · circb · spr · map · mget · mset · print ·
btn · btnp · key · keyp · mouse · sfx · music · time · pmem · clip · camera · pal ·
palt · rnd · flr · background`

**Namespaced `moy.*` (Moybyte-specific):**
- system/hardware: `moy.touch` · `moy.textmode` · `moy.cfg` · `moy.wifi`/`moy.radio` ·
  `moy.badge` · `moy.desktop`
- kid conveniences: `moy.col` · `moy.image`/`moy.Image` · `moy.beep` · `moy.volume`
- perf/advanced escape hatches: `moy.layer` · `moy.draw_layer` · `moy.spr_batch`

**Folded away from the kid surface entirely:** the *need* to call `spr_batch` /
`make_layer` / `draw_layer` — the automatic paths cover the common cases.

---

## 7. Build order

Each fold reuses machinery that already exists; sequence by cost/value:

1. **Auto-batch `spr()`** — cheapest, highest immediate value; sakura already proves
   the pixels. Uses today's `blit_batch`.
2. **`blit_indices` + paint-image assets** — fixes load *and* unblocks paint→game.
   One new kernel + an asset type; off the hot path.
3. **Auto-cache `map()`** — the biggest engine change (cache + invalidation +
   big-world window policy). Reuses `_Layer`/`blit_window` (#54).
4. **Dirty-rect** (#43) — the general umbrella that also catches non-tilemap static
   backgrounds; the largest architectural move, tracked separately.

---

## 8. Open decisions

- **Loop model:** keep `_init`/`_update(dt)`/`_draw` (variable-timestep, honest for a
  18–42 fps device) vs. add TIC-80's `TIC()` for compat carts (#11). Leaning: keep
  `dt` native, add `TIC()` as a thin shim only if/when Level-2 source-compat lands.
- **`background(img)` vs `spr(img)`-only** as the background surface (clarity vs.
  composability).
- **Cache-memory budget policy** for auto-`map()` and baked paint images (how many
  resident, eviction).
- **`.moyimg` compression** (raw / RLE / deflate) and whether the paint app writes it
  directly or via an export step.
