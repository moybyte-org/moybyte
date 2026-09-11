# ports/ — third-party carts ported to the Lua runtime (dev/test material)

Carts in this folder are **conformance and stress tests for the #67 Lua cart
runtime**, ported from other fantasy consoles with the moy-spec CLI's
`moy port` (the one converter, spec-side — `p8_lua_port.py` there).
They are deliberately **NOT seed carts**: seeding is declared per cart now (a
`system_carts/*/manifest.json` carries `"system": true` and an `"order"`, and
`tools/gen_device_carts.py` reads those), and nothing here carries either
declaration or lives in `system_carts/` — so none of it is baked into a
firmware image or seeded onto a device. To play one, copy the `.moy` folder into a cart store
(the tests do exactly that into a tmp store).

## p8/ — the PICO-8 conformance corpus, ONE cart each (2026-09-11)

`tools/gen_p8_ports.py` builds `ports/p8/<lid>.moy` for each cart in moy-spec's
`conformance/p8_corpus.json`. The carts are **not in this repo and must not be**
— they are their authors' work, several under licences that forbid
redistribution. The bytes come from that file's links, cached outside the tree by
moy-spec's `conformance/fetch_p8_corpus.py` (`~/.cache/moy/p8`), which is what
moy-spec CI already does. What IS committed is `ports/p8/perf/<lid>.lua`: our own
~10 lines per cart naming the scene to measure in and why.

```bash
python3 ../moy-spec/conformance/fetch_p8_corpus.py   # once, ~570KB
python3 tools/gen_p8_ports.py                        # -> ports/p8/*.moy
```

**ONE cart, not two.** The corpus is two things at once — the COMPATIBILITY set
(does the importer handle this cart: does it boot, take input, draw right) and
the PERFORMANCE set (what a frame costs with a real scene on screen). Those want
different starts, so the boards grew a plain import AND a "… Perf" twin of each,
and the two drifted: by 2026-09-11 every cart in the old `ports/p8perf/` carried
`P8_VH = 128` where a fresh import carries 120, so every perf number in #66 was
measured on a viewport eight rows taller than the one a real cart draws — and no
two boards held the same set. A cart cannot drift from itself.

**The generated half is its own file**, and that is the format now, not a local
trick: SPEC.md §4's `sources` lists every script a cart is made of and the host
runs them in that order, each its own chunk. The importer writes `p8.lua` (data
tables + the PICO-8 compat shim) and `main.lua` (the cart's own code, nothing
else), and lists both. The shim was 61% of what it used to write into main.lua
— 60,654 bytes identical in every port, ahead of the part a person opens — so
moss moss's main.lua went 100,400 → 37,687 bytes.

Separate chunks are what makes it work: the shim publishes 96 GLOBALS and
globals cross a chunk boundary, while its four per-cart upvalue captures stay
beside the data tables inside p8.lua, which is why the cut is there and not
between them. `main.lua`'s `local` aliases (`localization_lua`) travel with the
game for the same reason, in reverse.

Two things fall out. A crash in cart code reports its own line — `main.lua:19`
where the unsplit file said a line 1,400 down the generated prefix — and the
shim stops being 62KB of permanently-live string per cart on the launcher shelf
(`_HEAVY_CART_KEYS`).

**A cart's PICO-8 TABS are files too.** PICO-8 cuts a cart's code into numbered
tabs with a line that reads `-->8`, and the port used to flatten them into one
`main.lua` where four stray comments were all that was left of the author's own
structure. Each tab is a source now, in tab order, tab 0 being `main.lua`;
a tab titled by its first comment line takes that name (`--board` →
`board.lua`), anything else takes the number PICO-8 shows. Of the twelve corpus
carts two have tabs — dungeons & diagrams drops from 28 KB of everything to
5.4 KB of game plus `menu`/`tutorial`/`board`/`puzzles_list`, terra_1cart gets
three numbered ones.

Globals are again the reason it works, and their absence the reason it is not
always done: where a top-level `local` in one tab is read by a later one, where
a `goto` and its label are in different tabs, or where a long string holds a
line reading `-->8`, the tabs stay in one file and the import report says which.
PICO-8 joins its tabs into one chunk before parsing, so all three are legal
there. See `PICO8.md` (moy-spec) for the whole argument.

**The perf wrapper is a third script**, `perf.lua`, listed after `main.lua`. It
wraps what the cart defined (`p8_init`), so it cannot run before it; and it is
ours rather than the cart's, so it has no business inside the one file a person
opens. It reaches the cart's state through globals, which is all that crosses a
chunk — fine here, because a PICO-8 cart's top-level names are globals by
construction.

**The toggle is `config.json`'s `perf`**, the cart API's existing "Make it mine"
surface (`cfg(key, default)`, `docs/moy_cart_api.md`): `0` plays the cart
normally, `1` starts it in the measured scene. A kid never sees it; a measurement
session flips it in the Editor's Config tab with no re-push, and
`host_app._SEED_PRESERVE` keeps the value across a re-seed. `0`/`1` rather than
`true`/`false` because that is what the shipped carts already use
(`brick_siege_lua.moy/config.json`). Measured on Guition P4 glass the day it
landed — moss moss: `perf 0` logic 21ms / 30 fps drawn, `perf 1` logic 28ms /
13 fps, which reproduces the retired twin cart's numbers from one cart.

## celeste.moy — Celeste Classic

- **Original:** *Celeste* (PICO-8, 2016) by **Maddy Thorson & Noel Berry** —
  <https://www.lexaloffle.com/bbs/?tid=2145>, mirrored by the community at
  <https://celesteclassic.github.io/>.
- **License:** PICO-8 BBS carts default to **CC BY-NC-SA 4.0**. This port is
  kept strictly as in-repo development/test material with attribution — it must
  never ship in a product image, a seed set, or anything commercial.
- **Regenerate:** download the cart source (e.g. the `celeste.p8` mirrored in
  `CelesteClassic/celeste-maker`) and run:

  ```bash
  moy port celeste.p8 ports/celeste.moy --title "Celeste Classic"   # moy-spec CLI
  ```

- **What the port proves:** ~1550 lines of real-world PICO-8 Lua running under
  `moy_lua`/`lua_host` through the generated compat shim — flag-masked `map()`,
  `fget` off `__gff__`, the gfx-shared map rows 32-63, turn-based `sin`/`cos`,
  p8 table verbs (`add/del/foreach` with mid-iteration deletion), fixed-30fps
  pacing from the dt loop, and the 128x128 screen centered in the 320x240
  canvas. (The driving test moved out with the port tool; the Lua runtime's
  own coverage is the moy conformance suite.)
- **Known limitations:** moy's font is 8px wide vs PICO-8's 4px, so long text
  lines overflow the 128px window (the title credits clip); audio is the lossy
  `import_p8` fold (music is stubbed in this cart's source); numbers are IEEE
  doubles, not PICO-8's 16.16 fixed point (community ports do the same — the
  game plays correctly, TAS-exact replay would not).
