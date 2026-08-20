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
