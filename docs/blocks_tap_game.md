# Build a Tap Game in Blocks

This is a step-by-step guide for building **Tap Game** in the Moybyte block editor.
A gold coin pops up somewhere on the screen. Tap it to score a point and it jumps
to a new spot. A timer counts down -- when it hits zero, it's GAME OVER. Tap again
to play once more.

Everything here uses only blocks and buttons that exist in the editor. The finished
cart ships as `system_carts/tap_game.moy` -- you can open it to peek, or build your
own from scratch.

## Controls (host simulator)

- **Arrow keys** -- move the cursor up/down the script (and step a block's slot with
  left/right).
- **Z** = the **A** button (insert a block / edit the highlighted slot).
- **X** = the **B** button (back out of a menu).
- **Enter** = save / run.
- **Mouse** = touch (tap a row, tap a menu choice, tap a number key).

On the device it's the same: the trackball moves the cursor, the keyboard types,
and the touchscreen taps.

## How to open the block editor

1. On the launcher, open a cart (or make a new one).
2. Tap the **BLOCKS** tool button (or press its shortcut) to open the block editor.
   You'll see three event hats already there:
   - `when program starts`
   - `every frame (update)`
   - `every frame (draw)`

Each hat has a `+` **add a block** row inside it. Put the cursor on a `+` row and
press **A** (or tap it twice) to open the insert menu:

`PICK A KIND` (a category) -> `PICK A BLOCK` (a block in that category).

The categories are: **When...**, **Control**, **Draw**, **Buttons**, **Variables**,
**Math**, **Sound**.

## Step 1 -- Make the variables

We need five variables. In the insert menu, choose the **Variables** category. The
first row is **`+ new variable`**. Pick it, type a name, then press **OK**.

Make these five (repeat `+ new variable` for each):

- `score` -- how many coins you tapped
- `timer` -- frames left on the clock
- `over` -- 0 while playing, 1 when the game is over
- `tx` -- the coin's x position
- `ty` -- the coin's y position

> **Tip:** typing a name uses the on-screen/T-Deck keyboard. Press **OK** when done,
> or **X** to cancel.

## Step 2 -- Set up the game (`when program starts`)

Put the cursor on the `+` row inside **`when program starts`** and add these, one at
a time, from the **Variables** category (`set {var} to {value}`):

```
set score to 0
set timer to 600
set over to 0
set tx to (random to 270) + 15
set ty to (random to 150) + 56
```

### How to type a number (the important part)

When you add `set {var} to {value}`, the `{value}` is an **expression slot** -- a
Scratch-style white oval. Move the cursor to the block, step to the `{value}` slot
with **right**, and press **A**. The **number pad** opens.

- **Tap the digits** (or type them on the keyboard) to enter a number like `600`.
- Press **OK** to keep it, **DEL** to backspace, **X** to cancel.
- A leading `-` and one `.` are allowed (for negatives / decimals).

That's how you get `set score to 0`, `set timer to 600`, and `set over to 0`.

### How to use random + math for `tx` and `ty`

For `set tx to (random to 270) + 15` you drop blocks into the oval instead of typing
a number:

1. Edit the `{value}` slot. On the number pad, press **BLOCK** (the green button) --
   this opens the value chooser. (Or: the chooser's first row is **`123 type a
   number`** if you'd rather type.)
2. The chooser is `PICK A VALUE`. Choose **`{a} + {b}`** from the **Math** category.
   Now the slot shows `0 + 0`.
3. Edit the `{a}` part: press **BLOCK** again and choose **`random to {n}`** (also in
   **Math**). Set its `{n}` to `270` with the number pad.
4. Edit the `{b}` part: type `15` on the number pad.

`random to 270` gives a whole number from 0 to 269, so the coin lands somewhere on
the screen. Do the same for `ty`: `(random to 150) + 56`.

> **Why the `+ 15` / `+ 56`?** It keeps the coin away from the score text at the top
> and the screen edges. You can change those numbers to move the play area.

## Step 3 -- The game loop (`every frame (update)`)

Inside **`every frame (update)`**, build this. The outer `if` is from the **Control**
category (`if {cond}`); its condition is from **Math** (`{a} = {b}`).

```
if over = 0:
    change timer by -1
    if screen tapped:
        if tap x > tx:
            if tap x < (tx + 28):
                if tap y > ty:
                    if tap y < (ty + 28):
                        change score by 1
                        set tx to (random to 270) + 15
                        set ty to (random to 150) + 56
                        beep at 880 Hz
    if timer < 1:
        set over to 1
```

Here's where each piece comes from:

- **`if over = 0`** -- add `if {cond}` (Control). Edit `{cond}`, press **BLOCK**, pick
  **`{a} = {b}`** (Math). Set `{a}` to the **`{var}`** block (Variables) pointing at
  `over`, and `{b}` to the typed number `0`.
- **`change timer by -1`** -- `change {var} by {value}` (Variables). Point `{var}` at
  `timer`; type `-1` into `{value}` (the number pad accepts the leading `-`).
- **`if screen tapped`** -- `if {cond}` (Control); for `{cond}` press **BLOCK** and
  pick **`screen tapped`** (the **Buttons** category).
- **`tap x` / `tap y`** -- in the **Buttons** category. These tell you *where* the tap
  landed, so you can check it's on the coin.
- **`tx + 28`** -- `{a} + {b}` (Math), with `{a}` = the `tx` variable and `{b}` = `28`
  (28 is the coin's size). The four nested `if`s together mean "the tap is inside the
  coin's 28x28 box".
- **`beep at 880 Hz`** -- `beep at {freq} Hz` (Sound). Type `880` on the number pad.

To nest a block *inside* an `if`, put the cursor on the `+` row that appears indented
under it, then add the block there.

> The block under the cursor shows a one-line hint at the top of the screen. `forever`
> and `wait` have hints explaining how they behave on the console.

## Step 4 -- Draw the game (`every frame (draw)`)

Inside **`every frame (draw)`**:

```
clear screen to dark_blue
if over = 0:
    draw sprite 0 at x tx y ty
    outline box at tx,ty size 28x28 in yellow
    write "SCORE" at x 8 y 28 in white
    write score at x 56 y 28 in yellow
    write "TIME" at x 8 y 40 in white
    write timer at x 56 y 40 in green
    write "TAP THE COIN" at x 8 y 224 in light_grey
if over > 0:
    write "GAME OVER" at x 110 y 100 in red
    write "SCORE" at x 120 y 120 in white
    write score at x 168 y 120 in yellow
    write "TAP TO REPLAY" at x 104 y 140 in light_grey
if (over > 0) and screen tapped:
    set score to 0
    set timer to 600
    set over to 0
```

Blocks used here:

- **`clear screen to {color}`** (Draw) -- the `{color}` is a dropdown; press **A** on it
  and pick `dark_blue` (or step it with left/right).
- **`draw sprite {id} at x {x} y {y}`** (Draw) -- `{id}` is `0` (the coin you painted in
  the PAINT editor); `{x}`/`{y}` are the `tx`/`ty` variables. Edit each oval, press
  **BLOCK**, pick the **`{var}`** block, and point it at `tx` / `ty`.
- **`outline box at {x},{y} size {w}x{h} in {color}`** (Draw) -- this is the tappable
  yellow square. `{x}`/`{y}` are `tx`/`ty`; `{w}`/`{h}` are `28`; `{color}` is `yellow`.
- **`write {text} at x {x} y {y} in {color}`** (Draw) -- the `{text}` oval can hold a
  word like `"SCORE"` *or* a variable. For a word, edit the oval, press **BLOCK**, and
  pick the text option; for a number readout like `score`, drop the **`{var}`** block.
- **`{a} and {b}`** (Math) -- combines `over > 0` with `screen tapped` so a tap only
  restarts after the game is over.

> **Why `y 28` and not `y 8`?** While a cart runs, the console keeps a thin tool bar
> (CODE / PAINT / MAP / HOME) across the top ~22 pixels. Draw your score below that so
> it isn't hidden.

## Step 5 -- Save and play

- Press **SAVE** (or **Enter**). The editor compiles your blocks into a real program.
  If anything is wrong it tells you instead of saving over your work.
- Tap **CLOSE** to go back, then **RUN** the cart. Tap the coin and watch the score
  climb before the clock runs out!

To run the shipped version on the host:

```bash
python tools/simulate_desktop.py --cart system_carts/tap_game.moy
```

## Want to see the code?

Press **CODE** in the block editor to *graduate to code*: it turns your blocks into
Python you can read and keep editing. That's the icons -> blocks -> code ladder.
