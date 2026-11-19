# Build Tap Only Red in Blocks

This is a step-by-step guide for building **Tap Only Red** in the Moybyte block
editor. Bubbles float up the screen. Tap a red one and you score; tap any other
colour and it is a miss. A red that floats away is a miss too, and five misses
start the score over.

The whole game is two sprites with two short scripts each, and a Stage that
keeps the score. It teaches the idea every bigger game is made of: **each kind
of thing carries its own scripts**, and every copy of it on the screen runs them.

The finished cart ships as `system_carts/moybyte.tap_only_red.moy`: open it to
peek, or build your own from scratch.

## Controls (host simulator)

- **Arrow keys** move the cursor up and down a script, and step along a block's
  slots with left and right.
- **Z** is the **A** button (add a block, edit the highlighted slot).
- **X** is the **B** button (back out of a menu).
- **Mouse** is touch: tap a row, a menu choice, a key.

On a console the trackball or the arrows move the cursor, the keyboard types,
and the touchscreen taps.

## The pieces

| piece | what it is |
|---|---|
| **Sprites** tab | the pictures: an 8 x 8 red bubble with a white shine, and a blue, a green, a yellow and a pink bubble |
| **Scene** tab | where the bubbles start: three tagged `red`, four tagged `lure` |
| **Blocks** tab | the scripts: the **Stage**'s, the `red` sprite's and the `lure` sprite's |

A **tag** is the name a placed sprite answers to. Every bubble tagged `red`
runs the `red` scripts, whichever picture it wears, so four lure colours need
only one set of lure scripts.

## Step 1 -- Paint the bubbles (Sprites)

Paint sprite 1 as a red circle with a white rim and one white pixel for a
shine. Paint sprites 2 to 5 the same circle in other colours, without the
shine: the shine is how a player who cannot tell red from pink still finds the
reds. Leave the corners black, the colour the screen is cleared to.

## Step 2 -- Place them (Scene)

In the **Scene** tab, pick the **Stamp** tool and sprite 1 from the stamps.
Set **Tag** to `red`, then tap three places on the stage, spread from top to
bottom so they do not rise in a row. Set **Tag** to `lure` and stamp one bubble
of each other colour.

## Step 3 -- The score (the Stage)

Open the **Blocks** tab. The tabs above the script are **Stage** and one per
tag: `red` and `lure`. Start on **Stage**.

Make two variables: in the insert menu (**Pick a kind**), choose
**Variables**, then **+ new variable**, and type the name.

- `score` -- the reds you tapped
- `misses` -- the taps that were not red, and the reds that got away

Then build the Stage's three scripts:

```
when program starts
    set score to 0
    set misses to 0

every frame (update)
    if misses > 4
        set score to 0
        set misses to 0

every frame (draw)
    clear screen to black
    draw scene
    write "SCORE" at x 8 y 8 in white
    write score at x 56 y 8 in yellow
    write "MISSES" at x 224 y 8 in white
    write misses at x 280 y 8 in red
    write "TAP ONLY THE RED" at x 96 y 224 in light_grey
```

- **`set {var} to {value}`** is in **Variables**. Point `{var}` at `score`;
  `{value}` is a value slot: edit it, choose **123 type a number** and type `0`.
- **`if {cond}`** is in **Control**. For its condition choose **`{a} > {b}`**
  from **Math**, put the `misses` variable in `{a}` and type `4` in `{b}`.
- **`clear screen to {color}`** and **`write {text} at x {x} y {y} in {color}`**
  are in **Draw**. A `{text}` slot takes a word or a variable.
- **`draw scene`** is in **Sprite**. It paints every bubble where it is now,
  so it comes after the clear and before the words that sit on top.

## Step 4 -- The red bubble (`red`)

Choose the **`red`** tab. A sprite starts with four hats: when program starts,
every frame (update), every frame (draw), and **when I'm tapped**.

```
when program starts
    set size to 400 %

every frame (update)
    change my y by -2
    if my y < -32
        change misses by 1
        go to x (random to 288) y 240

when I'm tapped
    change score by 1
    beep at 880 Hz
    go to x (random to 288) y 240
```

- **`set size to {n} %`** is in **Looks**: 400 % draws the 8 x 8 bubble 32
  pixels wide, a size a finger can hit. **when I'm tapped** answers a tap
  anywhere in the box the bubble is drawn in.
- **`change my y by {dy}`** is in **Sprite**. A smaller y is higher on the
  screen, so `-2` floats the bubble up two pixels a frame.
- **`my y`** is in **Sprite** too. Below `-32` the whole bubble is off the
  top: the red got away, which is a miss.
- **`go to x {x} y {y}`** (Sprite) sends the bubble back under the bottom of
  the screen. For `{x}` choose **`random to {n}`** from **Math** and type `288`:
  a whole number from 0 to 287, so the bubble comes back somewhere new and
  stays on the 320-pixel-wide screen.
- **`beep at {freq} Hz`** is in **Sound**.

## Step 5 -- The lures (`lure`)

Choose the **`lure`** tab. Its scripts are the red's with two changes: a lure
that floats away costs nothing, and tapping one is the miss.

```
when program starts
    set size to 400 %

every frame (update)
    change my y by -2
    if my y < -32
        go to x (random to 288) y 240

when I'm tapped
    change misses by 1
    beep at 220 Hz
    go to x (random to 288) y 240
```

## Step 6 -- Play

Press **PLAY**. The editor compiles your blocks into a program first; if
something is wrong it says so and keeps your last good one.

To run the shipped version on the host:

```bash
python tools/simulate_desktop.py --cart system_carts/moybyte.tap_only_red.moy
```

## Make it yours

- Faster bubbles: `change my y by -3`. Or make a `speed` variable, change it by
  1 in the red's **when I'm tapped**, and rise by `0 - speed`.
- More bubbles: stamp more of them in the Scene. No script changes.
- A new lure colour: paint sprite 6 and stamp it with the tag `lure`.

## Want to see the code?

Open the **Code** tab to *graduate to code*: it turns your blocks into Python
you can read and keep editing. That's the icons -> blocks -> code ladder.
