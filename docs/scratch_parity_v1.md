# Scratch parity — what we have, what's missing

Living checklist of Scratch's block vocabulary vs. Moybyte's blocks (#85/#93). The
goal is to feel like Scratch for a kid, adapted to the console (touch-first, indexed
pixel canvas, scene actors = sprites). `✓` = supported, `~` = partial/adapted, `✗` =
missing. Our block names differ where the console idiom differs (noted in parens).

**Honest headline:** the core is here — sprites with per-object scripts, events,
motion (incl. real rotation), looks, control, operators, variables, lists, custom
blocks, sensing, sound. The gaps are ~13 clusters, mostly "nice to have"; the ones
that matter most for real games are **clones, glide, timer + real wait, ask/answer,
point-towards, graphic effects, and trig operators**.

## Top priorities (the ones worth doing next)

1. **Clones** — `create clone of ()`, `when I start as a clone`, `delete this clone`.
   Our per-tag actors are static (placed in the scene); true runtime spawning is the
   biggest game-enabler still missing. (`remove me` already covers delete.)
2. **Real timing** — `wait () seconds` is currently a **no-op** (frame-based); add a
   real per-actor/global timer so waits and `timer` work. Also `glide () secs to …`.
3. **`point towards` (mouse / sprite)** + **`go to` (mouse / random / sprite)** —
   the clean way to do directional/aim controls (Scratch does the trig for you).
4. **`ask () and wait` / `answer`** — text input, huge for quizzes/adventures.
5. **Graphic effects** — `change/set [ghost/color/…] effect`, `clear effects`
   (at least **ghost** = transparency and a **color** shift).
6. **Trig operators** — `sin/cos/tan` (+ `floor/ceiling`), needed for smooth aiming
   and circular motion.
7. **`say for () secs` / `think ()`** — timed speech + think bubbles.

---

## By category

### Motion  (our "Sprite" category, blue)
- ✓ move (n) steps · turn (d) degrees · point in direction (d)
- ✓ go to x () y () · change/set my x/y · my x · my y · my direction
- ✓ if on edge, bounce · **set rotation style** (all around / left-right / don't rotate)
- ✓ all-around **rotation** (arbitrary angle, cached)
- ✗ **glide () secs to (x,y)** (needs real timing)
- ✗ **point towards (mouse / sprite)**
- ✗ **go to (mouse / random / sprite)**

### Looks
- ✓ show · hide · set/change size · my size
- ✓ switch costume to (n) · next costume · costume # · **say (text)**
- ~ costumes are **tile ids** (no named costumes / per-sprite costume list yet)
- ✗ **say () for () secs** · **think ()** / think for secs
- ✗ **graphic effects** (ghost/color/fisheye/…) + clear effects
- ✗ **backdrops** (the stage's own costume) + switch/next backdrop, backdrop #/name
- ✗ layer order blocks: go to front/back layer, go forward/backward () layers
  (the scene editor has front/back, but there are no *blocks* for it)

### Sound
- ✓ play sound (n) · beep (freq)
- ~ volume / stop-all exist as engine verbs but not as blocks
- ✗ **play sound () until done** · start-sound-and-continue distinction
- ✗ change/set volume by () (as blocks) · volume reporter
- ✗ sound effects (pitch / pan) · tempo (music extension)

### Events
- ✓ when program starts (green flag) · every frame (forever) · **when I'm tapped**
  (when this sprite clicked) · **when (key) pressed** · **when I hear ()** (receive)
  · **broadcast ()**
- ✗ **broadcast () and wait**
- ✗ when backdrop switches to () · when (loudness/timer) > ()

### Control
- ✓ if · if/else · repeat (n) · forever · wait until · repeat until · stop
- ~ **wait () seconds** exists but is a **no-op** (frame-based; no real timer)
- ✓ break out of loop (our addition)
- ✗ **clones**: create clone of () · when I start as a clone (`delete this clone`
  ≈ our `remove me`)

### Sensing
- ✓ touching (tag)? · **touching edge?** · touch/mouse x·y · buttons (key pressed?)
- ✗ **timer** / reset timer
- ✗ **ask () and wait** / answer
- ✗ **distance to (sprite / mouse)**
- ✗ touching (mouse-pointer)? · touching color ()? / color-is-touching
- ✗ **( ) of ( )** — read another sprite's x/y/size/etc.
- ✗ loudness (mic) · set drag mode · current date/time · days-since-2000 · username

### Operators
- ✓ + − × ÷ · pick random () to () · `< > =` (+ our `<= >= !=`) · and/or/not
- ✓ join · letter () of · length of · mod · round · abs · min · max · sqrt
- ✗ **( ) contains ( )?** (string contains)
- ✗ **[function] of ()**: sin · cos · tan · asin/acos/atan · ln · log · e^ · 10^
- ✗ floor / ceiling (we round only)

### Variables & Lists
- ✓ set/change a variable · variables & lists
- ✓ list: add · delete (remove at) · replace (set at) · item (get) · length · for-each · clear
- ✗ list: **insert at** · **item # of** (index-of) · **contains ()?**
- ✗ **monitors** — show/hide a variable or list *on the stage* (Scratch's on-screen
  readouts + slider). We have no variable-watcher overlay.

### My Blocks (custom blocks)
- ✓ define with parameters · call · recursion guard  (full)

### Pen / Drawing
- ~ we have direct draw verbs (cls/spr/print/rect/rectb/circ/line/pix — our "Draw"
  category) but **no Pen** that trails behind a moving sprite (pen down/up, stamp,
  clear, set pen color/size).

### Not planned (Scratch extensions, out of scope for now)
- Video sensing · Text-to-speech · Translate · micro:bit/LEGO hardware · Music
  (note/drum/tempo) beyond simple sfx.

---

## Big structural gaps (beyond individual blocks)
- **Clones / runtime spawning** — actors are placed in the scene at author time; a
  cart can't spawn new ones. (See #109 for the actor model.)
- **A real clock** — `wait`/`timer`/`glide` all need frame-time accumulation per
  actor/globally; today `wait` no-ops.
- **Costumes & backdrops as first-class** — costumes are raw tile ids; there's no
  per-sprite costume list with names, and the Stage has no backdrop of its own.
- **On-stage monitors** — no variable/list watchers drawn on the game screen.

## Where the code lives
- Vocabulary + compiler: `runtime/blocks.py` (catalog, helpers, `compile_blocks`).
- Runtime actor verbs + `draw_scene` (looks/rotation): `runtime/host_app.py` +
  `firmware/.../device_api.py`; the actor world is `runtime/widgets.py` (`SceneWorld`).
- Editor surfaces: `runtime/block_editor_ui.py` (+ `editors_block.py`),
  `runtime/scene_editor_ui.py` (the side-by-side workspace).
