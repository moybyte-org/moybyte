# Jet Teapot — the compiled tier's showcase cart

A `"runtime": "wasm"` cart (docs/wasm_tier_plan_2026-09.md, "The showcase
cart"): the Utah teapot, lit and depth-buffered, rendered by
[Jet](https://github.com/CubeCoders/Jet) — CubeCoders' MIT software
rasteriser, which writes RGB565 into buffers the caller owns — with a HUD
drawn into the same frame, handed to the console whole through `blit565`. It
ports one of Jet's own example scenes,
[JetExamples](https://github.com/CubeCoders/JetExamples)' `esp32-lighting-teapot`:
its light, glaze, sky gradient, rocking motion and Flat / Gouraud / Phong
cycle. The camera is the player's. It is the first real user of `blit565`,
the import the proposal keeps for pixels that are direct-colour by nature: a
Phong-lit surface is a gradient, which a 256-entry palette would have to
quantize first.

## Credits

Jet is by [CubeCoders](https://github.com/CubeCoders/Jet) (PhonicUK).

## Playing it

| button | does |
|---|---|
| left / right | turn |
| up / down | fly forward / back, level |
| A / B | climb / sink |

The HUD's strip reads the width, the shading, the whole-frame rate, the mean
time Jet's `render()` took, and the triangles it rasterized, each averaged over
the last second. The cart draws it into its own frame, before `blit565`, in
the console's font (`src/hud_font.h`) and two of its palette's colours: the
pixels a `rect` and a `print` over the blit would draw, which a test holds it
to byte for byte. A frame the cart owns whole is one a banded board can show
straight from the cart's memory (`native/moy_flush/moy_fold.h`); verbs drawn
over it would have the console write the frame into its canvas first.

`config.json` picks how the frame is made, at launch:

| key | values | what it is |
|---|---|---|
| `width` | `"full"`, `"half"` | Jet's `HALF_WIDTH_BUFFERS`: one stored pixel per two columns, doubled into the frame before `blit565` |
| `interlaced` | `false`, `true` | Jet's `interlacedMode`: each frame renders every other row, alternating |
| `shading` | `"cycle"`, `"flat"`, `"gouraud"`, `"phong"` | the example's three-second cycle, or one mode held |
| `hud` | `true`, `false` | the HUD in the frame's top strip |

Half width is a compile-time switch in Jet, so the module carries Jet twice —
once as is, once with the switch on and its namespaces renamed on the
compiler's command line (`tools/jet_cart.py`'s `HALF_RENAMES`) — and the cart
picks one at `_init`. Jet's field buffers (`FIELD_BUFFERS`) are off: they exist
so a panel's DMA can scan one field while the CPU draws the other, and here the
console takes the whole frame, so interlacing runs over the one full-height
buffer instead.

## Where it lives

| what | where |
|---|---|
| the cart's source folder: manifest, config, model, licences, `src/` | `teapot.moy/` |
| the hooks, buttons, config, buffers and HUD | `teapot.moy/src/main.cpp` |
| the HUD's glyphs, the console's font | `teapot.moy/src/hud_font.h` |
| the scene, compiled once per Jet build | `teapot.moy/src/scene.cpp` |
| Jet's configuration | `teapot.moy/src/JetConfig.hpp` |
| the heap and the WASI calls the C library makes | `teapot.moy/src/runtime.cpp` |
| the imports it uses, from module `"moy"` only | `teapot.moy/src/moy.h` |
| Jet, vendored at the commit JetExamples pins | `jet/`, stamped in `jet_vendor.json` (`make vendor-jet`) |
| the model, derived from the example's generated mesh | `teapot.moy/teapot.obj` (the same script) |

The built cart is the source folder plus `main.wasm`, and a board's signed
`main.<chip>.aot`; none of those is ever committed.

```bash
python3 tools/jet_cart.py /tmp/carts                      # /tmp/carts/teapot.moy
python3 tools/jet_cart.py /tmp/carts --chip esp32s3 --chip esp32p4   # + signed modules
python3 tools/push_cart.py /tmp/carts/teapot.moy --board guition_s3  # onto a board
```

It compiles with wasi-sdk 24's clang (C++17, `-fno-exceptions -fno-rtti`,
the rasterizer's per-pixel shading calls inlined -- `tools/jet_cart.py` says
why), links wasi-libc and libc++ statically, and defines the four WASI calls
their stdio makes, writing nowhere, so the module imports nothing but
`"moy"`. The heap is `runtime.cpp`'s own first-fit allocator over the memory
above the static data, whose end it takes from the linker rather than asking
the memory its size (the file says what asking costs).
`tests/test_jet_cart.py` holds the build to the console's import
table and a sibling moy-spec's `moy check`, whose one finding is the warning
every compiled cart draws while the binding tracks the proposal.

## Memory

`"memory": 13` pages, 832 KB, fixed, laid out by the link: a 64 KB stack first
(an overflow leaves linear memory and traps), then the static data — mostly a
300 KB arena holding the frame `blit565` takes and Jet's 16-bit depth buffer,
or in half width the frame and Jet's half-width colour and depth buffers, the
same bytes either way — then the heap to the end. The heap holds the OBJ text while Jet's
loader parses it, the mesh, and Jet's per-frame queues; `tools/jet_cart.py`
refuses a memory that leaves it less than `HEAP_MIN`, and a test holds its
measured peak to three quarters of what it has. The load footprint the fit
notice checks (`native/moy_wasm/moy_wasm_footprint.h`) is that memory, the
chip's module and the engine's pool — within the floor board's room at the
launcher, so the cart runs on every console board.

## Byte order

Jet stores RGB565 as native 16-bit words and has no switch for its output
order: the one swap it offers is `Sprite2D`'s compositor for a byte-swapped
panel's scanout. WebAssembly's memory is little-endian, which is the order
`blit565` fixes, so the frame goes to the console as Jet wrote it.
`main.cpp` asserts the target's order at compile time, and a test reads the sky
gradient's bottom row back off the host's canvas against the colour it
computes itself — a value whose two byte orders differ.

## Measuring

Measure with WiFi off and `uncap 1`, so every loop frame draws; the manifest
declares 60 and the tick model otherwise holds the drawn rate to a divisor of
it. PERF gives the console's figure and the HUD gives the cart's own; the
numbers, per board and mode, are #158's. Every console board's on-glass suite
runs the cart from the launcher that way, in Phong at half and full width,
against a floor of its own (`tests/on_glass.py`'s `jet_holds_its_floor`), and
leaves it installed as it ships. The native figure they are set against is the
example's own `VALIDATION.md` in JetExamples at the pin: the same teapot on an
ESP32-S3 at 480 × 320, half width, field-interlaced, two raster cores, no
sandbox.

## Seeding (design; nothing seeds it yet)

A compiled cart's modules are native code a board trusts only by provenance,
so a seeded copy needs what `tools/jet_cart.py --chip` produces — `main.wasm`
and each chip's module, signed with the OTA key — made by the build, never by
hand and never committed.

1. **Where it lives.** The source folder moves to `system_carts/` with
   `"system": true` and an `"order"`, and `tools/gen_device_carts.py`, which
   packs the roster into each image, learns one thing: a system cart whose
   runtime is `wasm` is not read as text but taken from a directory of built
   carts (`--compiled DIR`), one per chip, and a board's image packs only its
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
   seeding a cart its own board would refuse as unsigned. A developer who
   signs with a key of their own gets a module the image refuses unless it
   trusts that key: that is `OTA_PUBLIC_KEYS`' policy, not a new one.
4. **Licences.** The seeded cart's `LICENSES.txt` gains the notices of the
   libraries compiled into `main.wasm` — wasi-libc (musl's MIT among them) and
   LLVM's libc++, whose LLVM exception waives attribution for compiled code —
   before the cart ships in a product image (THIRD_PARTY.md §2.7).

**What it costs, as of 2026-09-26.** CI: the job fetches wasi-sdk 24 (a 119 MB
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
