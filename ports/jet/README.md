# The Jet carts — the compiled tier's showcases

Two `"runtime": "wasm"` carts (docs/wasm_tier_plan_2026-09.md, "The showcase
cart") rendered by [Jet](https://github.com/CubeCoders/Jet) — CubeCoders' MIT
software rasteriser, which writes RGB565 into buffers the caller owns — each
porting one of [JetExamples](https://github.com/CubeCoders/JetExamples)' own
examples and handing its frame to the console whole through `blit565`, the
import SPEC.md §16.5 keeps for pixels that are direct-colour by nature:

- **Jet Teapot** (`teapot.moy/`): the Utah teapot, lit and depth-buffered,
  from `esp32-lighting-teapot` — its light, glaze, sky gradient, rocking
  motion and Flat / Gouraud / Phong cycle, with the camera the player's.
- **ESP 88** (`esp88.moy/`): `esp32-neon-film`, the two-minute neon city film
  in twelve cuts, played in a loop.

**Their home is [moybyte-org/mit-carts](https://github.com/moybyte-org/mit-carts)**,
which builds and publishes them: a change to a cart lands there. The two
folders here are its carts/<id>/, less that repository's own cart.json,
recipe and README, copied by `tools/vendor_jet_carts.py` (`make
vendor-jet-carts`) and stamped in `jet_carts_vendor.json`, because this
repository's tests and on-glass guards build them and a build here never
fetches; `tests/test_jet_vendor.py` makes an edit to the copy loud. What stays
this repository's own is Jet and the film's code (`make vendor-jet`), and the
build, `tools/jet_cart.py`, which mit-carts' recipe also runs at a pinned
moybyte commit, so the flags have one home.

## Credits

Jet and ESP 88 are by [CubeCoders](https://github.com/CubeCoders/Jet) (PhonicUK).

## Jet Teapot

A Phong-lit surface is a gradient, which a 256-entry palette would have to
quantize first; this is the cart that made `blit565` earn its place.

Its buttons and its `config.json` keys (`width`, `interlaced`, `shading`,
`hud`, `cores`) are mit-carts' `carts/teapot/README.md`. The HUD's strip reads the width, the shading, the whole-frame rate, the mean
time Jet's `render()` took, and the triangles it rasterized, each averaged over
the last second. The cart draws it into its own frame, before `blit565`, in
the console's font (`src/hud_font.h`) and two of its palette's colours: the
pixels a `rect` and a `print` over the blit would draw, which a test holds it
to byte for byte. A frame the cart owns whole is one a banded board can show
straight from the cart's memory (`native/moy_flush/moy_fold.h`); verbs drawn
over it would have the console write the frame into its canvas first.

Half width is a compile-time switch in Jet, so the teapot's module carries Jet twice —
once as is, once with the switch on and its namespaces renamed on the
compiler's command line (`tools/jet_cart.py`'s `HALF_RENAMES`) — and the cart
picks one at `_init`. Jet's field buffers (`FIELD_BUFFERS`) are off: they exist
so a panel's DMA can scan one field while the CPU draws the other, and here the
console takes the whole frame, so interlacing runs over the one full-height
buffer instead.

## ESP 88

The film's own code builds and animates every cut — the city, the cars, the
cockpit, the rain, the lens pulls — through `Film::seek`, and the cart plays
it from its own clock, looping where the example restarts its board after a
second of black. Its buttons and `config.json` keys are mit-carts'
`carts/esp88/README.md`.

It renders the way the example's ESP32 runtime (`components/esp32_jet`) does:
Jet's half-width field buffers, one field a frame, the river reflecting the
previous field, and the sprites — glows, fades, credits — composited at full
width onto the rows each field sends out. Here those rows go into the cart's
frame rather than to a panel, so the other field's rows stay as they were, as
a panel's do. The picture is two thirds of the film's: the 480 × 296 between
its letterbox bars becomes 320 × 198, centred in the console's 320 × 240,
whose black above and below stands in for the bars. That takes one change to
the film's code, `World.hpp`'s frame size made a default the build overrides
(`tools/vendor_jet.py`'s `PATCHES`); the three things the film draws in
480-wide pixels, the cart adapts instead: the glow's falloff and the closing
credits come scaled in `assets.bin`, and `main.cpp` places the credits and
turns the film's bars off.

The film's artwork — its textures, palettes, glow and credits — is read from
`assets.bin` at `_init` into zeroed arrays (`src/Assets.hpp`,
`src/CreditMask.hpp`) rather than compiled in as initialised data, which a
module carries a second time and a board holds a third while it loads.

## Two cores

Both carts draw with the console's second core the way Jet's own ESP32-S3
runtime draws with it: the frame's setup -- the transform, the culling, the
sort -- runs on one core, and the frame's rows are cut into bands that
rasterize at once, each into its own rows and its own triangle flags, merged
after. The cart hands the bands to the console through `par`
(SPEC.md §16.10 in moy-spec), which runs them on as many cores as
the console gives it, or one after another on a console with one; the frame
is the same either way, which `tests/test_jet_cart.py` holds byte for byte
against Jet's single pass. The teapot's bands also clear and widen their own
rows, two bands for an interlaced frame and four for a whole one, so a core
the display keeps busy takes fewer; ESP 88's two bands read the other field
for their reflections, never each other's rows, and its scan-out is split the
same way. An item has 2 KB of stack of its own (`runtime.cpp`) and uses under
half a kilobyte, which the test holds too. What the second core buys, board
by board, is #158's.

## Where they live

| what | where |
|---|---|
| a cart's folder: manifest, config, data, licences, `src/` -- mit-carts' copy | `teapot.moy/`, `esp88.moy/`, stamped in `jet_carts_vendor.json` (`make vendor-jet-carts`) |
| the hooks, buttons, config, buffers and HUD | `<cart>/src/main.cpp` |
| the imports, moy-spec's header; the heap, `par`'s items and their stacks, and the C library's edges; the HUD's glyphs, the console's font | `<cart>/src/moy_cart.h`, `runtime.cpp`, `hud_font.h` — one body in both, which a test holds equal |
| the teapot's scene, compiled once per Jet build, and its Jet configuration | `teapot.moy/src/scene.cpp`, `JetConfig.hpp` |
| Jet, vendored at the commit JetExamples pins | `jet/`, stamped in `jet_vendor.json` (`make vendor-jet`) |
| the teapot's model, derived from the example's generated mesh | `teapot.moy/teapot.obj` (derived by the same script, `--carts` writing it into mit-carts) |
| the film's code and Jet configuration, from the same JetExamples commit | `examples/esp32-neon-film/main/` (the same script) |
| the film's artwork, derived from its generated headers | `esp88.moy/assets.bin` (the same, `--carts`) |

A built cart is its source folder plus `main.wasm`, and a board's signed
`main.<chip>.aot`; none of those is ever committed.

```bash
python3 tools/jet_cart.py /tmp/carts                      # /tmp/carts/teapot.moy, esp88.moy
python3 tools/jet_cart.py /tmp/carts --cart esp88 --chip esp32s3 --chip esp32p4   # + signed modules
python3 tools/push_cart.py /tmp/carts/esp88.moy --board guition_s3   # onto a board
```

Each compiles with wasi-sdk 24's clang (C++17, `-fno-exceptions -fno-rtti`,
the rasterizer's per-pixel shading calls and its per-row span step inlined,
float-to-int casts as wasm's saturating conversions -- `tools/jet_cart.py`
says why), links wasi-libc and libc++ statically, and defines the four WASI calls
their stdio makes, writing nowhere, so the module imports nothing but
`"moy"`; the C libraries' formatted messages before an abort go nowhere too,
which keeps printf out of the module. The heap is `runtime.cpp`'s own
allocator over the memory above the static data, whose end it takes from the
linker rather than asking the memory its size (the file says what asking
costs, and why it is best fit from both ends).
`tests/test_jet_cart.py` holds each build to the console's import
table and to a sibling moy-spec's `moy check`, which has nothing to warn
about.

## Memory

Each cart's memory is fixed by its manifest and laid out by the link: the
stack first (an overflow leaves linear memory and traps), then the static
data, then the heap to the end. `tools/jet_cart.py` refuses a memory that
leaves the heap less than the cart's `heap_min`. The load footprint the fit
notice checks (`native/moy_wasm/moy_wasm_footprint.h`) is that memory, the
chip's module and the engine's pool; #158 has each cart's against each board.

The teapot: `"memory": 13` pages, 832 KB: a 64 KB stack first
(an overflow leaves linear memory and traps), then the static data — mostly a
300 KB arena holding the frame `blit565` takes and Jet's 16-bit depth buffer,
or in half width the frame and Jet's half-width colour and depth buffers, the
same bytes either way — then the heap to the end. The heap holds the OBJ text while Jet's
loader parses it, the mesh, and Jet's per-frame queues, and a test holds its
measured peak to three quarters of what it has. Its footprint is within the
floor board's room at the launcher, so it runs on every console board.

ESP 88: `"memory": 27` pages, 1,728 KB: a 16 KB stack (the film uses under
6 KB, which the cart reports), then the frame, the two half-width fields and
the artwork's arrays, then about 1.25 MB of heap. The heap holds each cut's
city, cars and cockpit, which the film builds when the cut begins and frees at
the next, beside Jet's per-frame queues, which keep the capacity of the
busiest frame drawn so far: the boulevard's city loaded after the pursuit's
queue is the most it ever holds, and a test plays the film that way and holds
the peak inside `heap_min`. That is as small as the film allows without
changing it. The footprint is about 2.7 MB on either chip: a Guition S3 has
that free from a fresh boot, but not after a long session has left less, and
then the cart opens the fit notice.

## Byte order

Jet stores RGB565 as native 16-bit words and has no switch for its output
order: the one swap it offers is `Sprite2D`'s compositor for a byte-swapped
panel's scanout. WebAssembly's memory is little-endian, which is the order
`blit565` fixes, so the frame goes to the console as Jet wrote it.
`main.cpp` asserts the target's order at compile time, and a test reads the sky
gradient's bottom row back off the host's canvas against the colour it
computes itself — a value whose two byte orders differ.

## Measuring

Measure with WiFi off and `uncap 1`, so every loop frame draws; the tick
model otherwise holds the drawn rate to a divisor of what the manifest
declares. PERF gives the console's figure and the HUD gives the cart's own; the
numbers, per board and mode, are #158's. Every console board's on-glass suite
runs the teapot from the launcher that way, in Phong at half and full width,
against a floor of its own (`tests/on_glass.py`'s `jet_holds_its_floor`), and
leaves it installed as it ships. The native figure it is set against is the
example's own `VALIDATION.md` in JetExamples at the pin: the same teapot on an
ESP32-S3 at 480 × 320, half width, field-interlaced, two raster cores, no
sandbox.

ESP 88 measures itself per cut: over each cut's most recent play of a second
or more it writes the frames a second and the mean ms Jet took (render and the
film's effects) into pmem, in tenths, at slots 32 + cut and 16 + cut (cut 0
to 11), beside the heap's peak and size (slots 0 and 1, KB), the stack it
used (slot 6, KB) and the most a `par` item's stack held (slot 7, bytes). One uncapped loop of the film fills all twelve. An interlaced
frame is one field, so its rate is the one `VALIDATION.md` gives for the S3
in fields a second, at 480 × 320 against the cart's 320 × 198.

## Seeding (design; nothing seeds them yet)

A compiled cart's modules are native code a board trusts only by provenance,
so a seeded copy needs what `tools/jet_cart.py --chip` produces — `main.wasm`
and each chip's module, signed with the OTA key — made by the build, never by
hand and never committed.

1. **Where it comes from.** The build of the copy here, whose `main.wasm` is
   the one mit-carts' release carries, gets a roster entry with `"system":
   true` and an `"order"`, and `tools/gen_device_carts.py`, which packs the
   roster into each image, learns one thing: a system cart whose runtime is
   `wasm` is not read as text but taken from a directory of built carts
   (`--compiled DIR`), one per chip, and a board's image packs only its
   own chip's module beside `main.wasm`. Its roster entry is deflated like
   every other seed.
2. **Who signs.** Today the OTA key is a repository secret that only the
   `publish` job's small script sees; the board builds, which run ESP-IDF's
   and MicroPython's own build code, never do. That stays true. A new job
   ahead of the board matrix compiles `main.wasm` and both chips' modules
   UNSIGNED (the toolchain needs no secret), then a step with
   `MOYBYTE_OTA_SIGNING_KEY` in its environment signs the finished bytes with
   `tools/wasm_module.py`'s `sign` (a `sign` subcommand over an existing
   module is the one addition that tool needs; it already signs what it
   compiles), and uploads them as an artifact the board jobs download and bake.
3. **A build without the key** — a pull request from a fork, or a developer
   without the release key — packs no compiled seed and says so, rather than
   seeding a cart its own board refuses as unsigned unless its owner has
   turned on Unknown sources. A developer who signs with a key of their own
   gets a module the image refuses unless it trusts that key, whatever that
   setting says: that is `OTA_PUBLIC_KEYS`' policy, not a new one
   (`native/moy_wasm/README.md`, "Unknown sources").
4. **Licences.** The seeded cart's `LICENSES.txt` gains the notices of the
   libraries compiled into `main.wasm` — wasi-libc (musl's MIT among them) and
   LLVM's libc++, whose LLVM exception waives attribution for compiled code —
   before the cart ships in a product image (THIRD_PARTY.md §2.7).

**What seeding the teapot costs, as of 2026-09-26.** CI: the job fetches wasi-sdk 24 (a 119 MB
tarball) and the two pinned `wamrc` builds (58 MB and 79 MB), all cacheable
under their sha256 pins, and compiles for well under a minute on a runner; the
host CI job already keeps wasi-sdk in its cache under that pin for this cart's
goldens (`.github/workflows/ci.yml`, and the same step in
`tools/preflight.sh`), so the seeding job shares it. Each console image: about
340 KB more in its packed roster — a chip's module is most of it — against the
headroom the build prints, which the S3 boards' slots decide. First boot: the
seed writes about 770 KB into the store. The alternative that costs the image
nothing is to publish the built cart per chip beside the firmware images and
let the store install it (phase 5's distribution); it needs a network the
first time, which a seed does not.
