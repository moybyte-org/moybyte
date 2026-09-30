# The WebAssembly cart tier — implementation plan (2026-09)

**What this is.** The steps that turn the #158 spike into a third cart
runtime, in the order their dependencies force, with the repository each step
lands in and the executable guard that says it is done. Measurements live in
#158. The ABI is moy-spec's SPEC.md §16, the WebAssembly binding, and its
open items are proposals/wasm-runtime.md's. This document is the sequence and the decisions that fix it; it
is dated, and it moves to `docs/history/` when the tier ships. Revised
2026-09-25 after three adversarial reviews (architecture, hardware, product);
the decisions below record what they changed.

## Decisions this plan rests on (2026-09-25)

- **WebAssembly, not native ELF.** The lineup is three instruction sets plus
  a browser; one `.wasm` runs on all of them and the per-architecture module
  is a derived cache. Linear memory is bounded and imports are the only
  capability surface. There is no firmware symbol table to version. The
  cart's memory is one fixed block sized by its manifest. The author's
  toolchain is clang.
- **A per-architecture module is native code, and is trusted only by
  provenance.** The sandbox lives in the compiler's output, not in the loader,
  so a board loads a module only if its custom section carries the full key
  (wasm hash, compiled-code format version, compiler flags, target) and the
  file is signed the way OTA images are. A module for another chip or format
  is simply not this console's -- it is never opened, the interpreter plays
  the cart instead (2026-09-30, below); a TAMPERED one -- present signature
  bytes that do not verify -- is refused, and a test proves it.
- **The owner can run unknown sources (owner, 2026-09-29).** Signing is
  the default, not a lock: a Settings switch, off by default and turned on
  past a plain warning, lets a board run unsigned modules, so anyone who
  rebuilds a cart from its source (Doom from moybyte-org/gpl-carts, their own
  game) can run it on their own console. The provenance key is still
  checked, because it is what keeps a module built for another runtime from
  crashing the board. Carts published through the store never need the
  switch: the store compiles and signs what it lists.
- **A moy-spec binding, not a moybyte-only runtime.** SPEC.md §3.1 already
  makes an unknown `runtime` a clean refusal, so a host that lacks the tier
  pays nothing. The import table *is* the verb table, which is the spec's own
  subject; libmoy's README names a wasm import table as the second binding
  it was shaped for. Phase 4 made it SPEC.md §16, an optional binding beside
  Lua (2026-09-30); until then SPEC.md §15 named `"wasm"` the reference
  console's vendor runtime.
- **One engine, one import table, every tier.** The host runs the same C
  binding over WAMR through ctypes, exactly as `runtime/lua_host.py` runs
  Lua, because a host and a device that disagree about what a verb does is
  the disease that deleted lupa. There is no wasmtime tier; the spike's host
  runner stays an oracle inside `experiments/wasm_aot/doom/`. The browser
  adapts the same C thunks in JavaScript.
- **The module shape is moycore's.** A native module (new: native/moy_wasm)
  is the ENGINE only: the vendored runtime and the thread a cart runs on. The
  import table is C in libmoy beside the Lua binding, and moycore hosts it
  with the snapshot-in, queue-out contract it already has. The Player's one
  Lua hook becomes a map of runtime factories, so a missing runtime is an
  absent key.
- **Friction lives in code, not prose.** The board-specific knowledge lives
  in a pinned fork of WAMR, vendored as sources like everything else the
  boards compile (`tools/vendor_libmoy.py` says why: no network fetch inside a
  build). The compiler ships prebuilt. A port adds a component and a call.
- **WAMR is carried as a fork, pinned by hash.** moybyte-org/wasm-micro-runtime,
  branch `moybyte-2.4.5` over the upstream 2.4.5 tag, holding the ESP32-S3 and
  ESP32-P4 platform work. Fork policy: the pin moves only for a security fix,
  an ESP-IDF bump, or a feature the tier needs; every move rebuilds the
  compilers and every module, and re-runs the hello and Doom checks on both
  chip families and on Linux. Upstreaming is optional and off the critical
  path. The module's provenance key no longer names the fork commit (below,
  2026-09-30): re-vendoring alone no longer stales an installed module.
- **How a host executes the module is host policy** (AOT, XIP, caches, an
  interpreter) and never enters the spec. **Every console board also carries
  WAMR's interpreter (owner, 2026-09-30)**, so nothing is ever refused for
  want of a module: a cart with none for this chip, or whose module has gone
  stale, plays on it instead, slower, with a short notice. A
  browser-authored or Zero-synced cart therefore plays on a board with no
  compiler involved. #158's spike measured an interpreter not paying for
  itself on speed alone; that number priced fps, never what a moved runtime
  pin does to every *installed* cart (ESP 88), which is what forced the
  reversal -- see "A cart survives its firmware" below. The interpreter
  chosen and its per-board cost are #158's.
- **Cart storage is the board's.** The SD card on the T-Deck, the flash VFS on
  the P4 boards; an SD card can become a P4 requirement if cart sizes demand
  it. Assets are read through the cart's own folder and nothing else.
- **Every console board, not two.** Each board declares or denies the module
  in its `board.toml` with a reason, and the gate runs on every board that
  declares it. The Guition S3 is the floor board for memory.
- **The floor defines a portable cart; a bigger cart is allowed and says
  so.** The tier ships on every console board, and the compiled tier's floor
  is the FLOOR board's share of the cart-runtime reserve: a cart within it
  runs on every board. A cart above it is allowed (decided 2026-09-26, for
  Doom first): the check command warns rather than refuses, and a board that
  cannot fit it refuses at launch with a plain notice to the player, never an
  error panel or a crash. Raising what a board can fit is the kernel work in
  the C re-architecture issue, not a per-cart exception.
- **Doom lives in moybyte-org/gpl-carts, never in the firmware.** doomgeneric
  is GPL and the shareware WAD forbids consideration and derivative works, so
  the glue and recipe are GPL-2.0-or-later in that repository (decided
  2026-09-26), which publishes built carts with their source; its installer
  fetches Debian's WAD rather than anyone hosting it, and Doom is never
  seeded, preloaded or shipped with a console (`THIRD_PARTY.md`). It runs from the launcher
  on a board that can fit it and shows the notice on one that cannot: the
  Waveshare P4 always, the T-Deck from a fresh boot but not after a session
  has left about 200 KB less PSRAM free (its largest block unchanged, so
  retained rather than fragmented), the Guition S3 never, and the Guition P4's store
  cannot hold the cart (#158). The next decision names the levers that would
  widen that set.
  Doom is still the cart that found the ABI's gaps: the 256-entry blit
  palette, the asset read and the sample stream.
- **A compiled cart's memory is the cart-runtime reserve, not free PSRAM.**
  The T-Deck keeps a fixed slice of PSRAM out of the Python heap for the
  Lua VM, the compositor's framebuffer and the layer pool
  (`firmware/lilygo_t_deck_plus_mainline/boards/MOYBYTE_TDECK/mpconfigboard.h`,
  enforced by the split-heap patch in `tools/esp32_build_lib.sh`). A wasm cart
  runs with no Lua VM, so its linear memory and relocated text take the VM's
  share of that same slice, and the tier's floor on a board is the slice minus
  the framebuffer and the pool. Doom on the T-Deck therefore means one of
  three levers, measured in phase 3 in this order: a smaller zone (its
  `-mb` knob) and which levels survive it; a larger reserve at build time and
  what the shell loses; the text in flash through a partition, which costs a
  partition-table change and a full-erase reflash per device. Measured
  2026-09-26 (#158): at the current reserve none fits the floor board, so
  the reserve and the flash partition stay as they are and the lever is the
  kernel work in #224 instead.
- **A cart survives its firmware (owner, 2026-09-30).** ESP 88: a T-Deck that
  took an OTA carrying a re-vendored WAMR fork refused every compiled cart
  already on its card, because the provenance key named the fork commit and
  re-vendoring moves it for reasons that do not touch what a module needs
  from the runtime it loads into. A board depends only on moy-spec now:
  - **A `.moy` carries `main.wasm` plus any number of compiled modules**,
    each named for what it runs on -- chip and compiled-code format version,
    e.g. `main.esp32s3.f1.aot` (`tools/wasm_cart.py`'s `aot_name`,
    `device/moycore_glue.py`'s `aot_path`). SPEC.md 16 already permits
    exactly this (quoted in `native/moy_wasm/README.md`'s "A cart's
    session"), so the spec needs no change. Off a console, the cart is
    fully portable.
  - **Push and install copy `main.wasm` plus only the module matching the
    target console**; a cart with none gets one compiled on the spot,
    unsigned (`tools/push_cart.py`'s `compiled_module` already did this for
    push; moy-spec's `moy install` does the same).
  - **On the console the cart folder is self-contained.** A console of the
    same chip and format takes its module as it is; any other console runs
    the cart on the interpreter until it gets its own. Nothing is evicted --
    a module counts toward the cart's size, checked at install, and leaves
    only with the cart.
  - **The key names a compiled-code FORMAT VERSION, not the fork commit**
    (`native/moy_wasm/moy_wasm_key.h`'s `MOY_WASM_FORMAT_VERSION`), hand-bumped
    only for a change that reaches the vendored AOT loader/runtime ABI or the
    pinned compiler -- `native/moy_wasm/wasm_format_version.json` names the
    exact scope and `tests/test_wasm_format_version.py` is the guard. A
    module goes stale only when the format moves, which is rare by design;
    routine re-vendoring no longer stales an installed cart. Mechanism and
    trust table: `native/moy_wasm/README.md`'s "Provenance" and "Unknown
    sources".
  - **The interpreter is WAMR's, vendored like the AOT runtime**
    (`make vendor-wamr`; `native/moy_wasm/wamr/`, never hand-edited) and
    built into every console board's image -- classic, per #158's
    classic-vs-fast measurement (the two P4s' headroom was the tightest it
    was picked against); mechanism and cost:
    `native/moy_wasm/README.md`'s "The interpreter tier".
    The Player runs `main.wasm` on it whenever no valid AOT module matches,
    with a short toast (`runtime/console_notices.py`'s toast, not the
    blocking notice panel -- the cart plays, just slower), which retires
    the "Needs an update." panel `43581ed4` added for exactly the case this
    decision now plays through instead.

## The phases

Each step names its repository, what it produces, and the guard that says it
is done. A guard is a test or a check script, never prose.

### Landed

Phases 1 and 2 and all of phase 3 are in (2026-09-25/26): the engine is
vendored and taken by every console board; moy-spec carries the binding
candidate and libmoy's import table; a compiled cart runs from the launcher
on the host and on all four boards under guards in every on-glass suite;
modules are signed the way OTA images are; a cart too big for a board opens
a plain notice instead of failing, and a full store says so; the loader
bug is fixed in the fork; and Doom builds from a local recipe and runs
where it fits. The numbers and the fit measurement are on
#158; how each piece works is its README (`native/moy_wasm/README.md`,
`native/moycore/README.md`, `experiments/wasm_aot/doom/README.md`, and the
proposal in moy-spec).

### The showcase — what remains

Jet Teapot, a 3D cart on Jet (https://github.com/CubeCoders/Jet, MIT), runs on
all four boards under pinned fps floors, its model read through `read` and its
frame, HUD included, handed to `blit565`; its source and build tools are MIT (2026-09-26; numbers on #158). What remains follows
CLAUDE.md's placement rule:

- The teapot and ESP 88 are moybyte-org/mit-carts' (2026-09-30), which
  builds and publishes them by recipe with `tools/jet_cart.py` at a pinned
  moybyte commit; `ports/jet/` keeps a stamped copy for the guards (its
  README says why). Further JetExamples ports (the tropical island, the
  mail-plane sprite demo, the neon car, one effects demo) join mit-carts,
  each after its own asset-licence check. template-cube is moy-spec's `moy
  new --jet` starter, and `moy install` and `moy index` are moy-spec's, which
  both carts repositories use. moybyte keeps the seeding and the on-glass
  guards; seeding a compiled cart is designed in `ports/jet/README.md` and
  waits on #124's gate.
- The compiled tier's render cost against native (#158 has the numbers):
  the per-chip compilers used to split every memory access they could not
  prove aligned into single bytes, the cart kept its per-pixel shading calls
  out of line, and the compiler paid five avoidable costs #158 lists; all
  are fixed. Jet's second raster core is the cart's through `par`
  (2026-09-29), fork-join over the cart's own memory rather than threads;
  what it buys against native's two cores is on #158. What remains is the
  software bounds checks, which stay on; the frame, depth and transform
  buffers in PSRAM, where native keeps its hot buffers in internal SRAM; the
  frame's setup, which stays on one core and costs the sandbox more than the
  raster does; and on the P4s the console's frame copy (the S3s show the
  cart's frame without one).

### Phase 4 — promotion (moy-spec; landed 2026-09-30)

moy-spec's desktop player runs compiled carts on Linux, Windows and macOS, and
its web player runs a cart as a sibling module whose imports are JavaScript
adapters over the binding's own C; conformance scenes for every wasm-only
import, the ordinary verbs, a trap and the refusal fixtures hold every host to
identical RGB565 frames (#158). The binding is SPEC.md §16, optional beside
Lua (§15); its measurements are RATIONALE.md's and how a host executes a
module is PORTING.md's. `libmoy/include/moy_cart.h` is the import table as C,
held row for row to `wasm-imports.json`, and `moy check` passes a well-formed
compiled cart with nothing to warn about. A cart author's loop is moy-spec's:
`moy new --wasm` and `--jet`, `moy build` with a pinned wasi-sdk, `moy play`
rebuilding on save (COMPILED.md), and `tools/push_cart.py` compiles the
module a board needs when a cart has none.

### Phase 5 — distribution (later)

- The store serves signed per-architecture modules beside the canonical
  `.wasm`, keyed by the full key, with an on-device cache directory.
- Compiling in the cloud so a cart can be written on the device alone is a
  separate proposal on top of this.

## Decision points, all the owner's

- ~~After phase 1: ship the tier on every console board, or wait for the
  S3 diet.~~ Decided 2026-09-25: ship on every board. The S3 boards' floor
  breach is the console's with WiFi up, not the tier's; WiFi is not meant to
  be on while a cart plays, and the S3 diet is a later item of its own.
- ~~Whether the Doom glue is marked GPL-2.0-or-later.~~ Decided 2026-09-26:
  it is, in moybyte-org/gpl-carts, which is Doom's home.
- Human testing on every touched board before any of this reaches master.
- ~~After phase 4: promote the proposal to a binding, or keep the vendor
  runtime.~~ Decided: promoted, SPEC.md §16 (2026-09-30).
- Whether an SD card becomes a P4 requirement.

## Deliberately not in this plan

- A wasmtime host tier.
- Doom's music. A compiled cart's sound is `snd` (22,050 Hz mono, a
  2,048-frame queue, pinned by Doom in SPEC.md §16.9), which the T-Deck mixes
  into `moy_audio`'s output and a board with no speaker drains by the clock.
  Doom plays its sound effects through it; its music, MUS through an OPL
  synth, is not built, because the synth costs more of a T-Deck frame than
  the rest of Doom's sound (#158).
- moybyte's superset verbs for wasm carts, and a libc story beyond "no WASI
  imports".
- A Lua-to-wasm path. The proposal's answer is "nothing, deliberately".
- The cloud compiler.
- An interpreter as a SPEC feature -- it stays host policy, SPEC.md §16's own
  words; nothing requires a host to carry one. The interpreter itself
  shipped 2026-09-30 anyway, on every console board -- "A cart survives its
  firmware" above is why the earlier "does not pay for itself" read of #158
  stopped being the deciding question.
- A framebuffer for Lua carts. SPEC.md §12.6 stands; §15 carves the one
  exception, for a cart that owns its own memory.
- An ESP-IDF engine component in libmoy, so another ESP32 OS can take the
  chip-side engine under MIT instead of re-deriving it from `native/moy_wasm`.
  Deferred until another OS wants it (owner, 2026-09-29); moy-spec's desktop
  and web players already show the job off the chip.
