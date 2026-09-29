# The WebAssembly cart tier — implementation plan (2026-09)

**What this is.** The steps that turn the #158 spike into a third cart
runtime, in the order their dependencies force, with the repository each step
lands in and the executable guard that says it is done. Measurements live in
#158. The ABI lives in moy-spec (proposals/wasm-runtime.md, which SPEC.md §15
points at). This document is the sequence and the decisions that fix it; it
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
  (wasm hash, fork commit, compiler flags, target) and the file is signed the
  way OTA images are. A tampered or foreign module is refused, and a test
  proves it.
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
  it was shaped for. Until phase 4 promotes it, one line under SPEC.md §15
  names `"wasm"` the reference console's vendor runtime, and every public
  piece before that (the libmoy file, the CLI check) is labelled as tracking
  the proposal.
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
  chip families and on Linux; the cache key carries the fork commit, so a
  stale module reads as absent, never migrated. Upstreaming is optional and
  off the critical path.
- **How a host executes the module is host policy** (AOT, XIP, caches, an
  interpreter for small carts) and never enters the spec. No interpreter is
  built into the boards: a cart without a matching signed module is refused,
  which means a browser-authored or Zero-synced cart cannot play on a board
  until a compiler service exists. That is stated, not hidden.
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
  palette and the asset read.
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

- The teapot, ESP 88 (the neon city film) and further JetExamples ports
  (the tropical island, the mail-plane sprite demo, the neon car, one
  effects demo) become moybyte-org's MIT carts repo, each after its own
  asset-licence check;
  template-cube becomes a `moy new --jet` starter in moy-spec with the import
  header; the installer and index tools move from gpl-carts into moy-spec's
  CLI so both carts repos share them. moybyte keeps the seeding and the
  on-glass guards. Seeding a compiled cart is designed in
  `ports/jet/README.md` and waits on #124's gate.
- The compiled tier's render cost against native (#158 has the numbers):
  the per-chip compilers used to split every memory access they could not
  prove aligned into single bytes, the cart kept its per-pixel shading calls
  out of line, and the compiler paid five avoidable costs #158 lists; all
  are fixed. What remains is the software bounds checks, which stay on; the
  frame, depth and transform buffers in PSRAM, where native keeps its hot
  buffers in internal SRAM; Jet's second raster core, which a cart has no
  way to use; and on the P4s the console's frame copy (the S3s show the
  cart's frame without one).

### Phase 4 — promotion (moy-spec; the owner's decision)

The second and third hosts are in (2026-09-26): moy-spec's desktop player
runs compiled carts on Linux, Windows and macOS, and its web player runs a
cart as a sibling module whose imports are JavaScript adapters over the
binding's own C. Conformance scenes for every wasm-only import, the ordinary
verbs, a trap and the refusal fixtures hold every host to identical RGB565
frames (#158). What remains is promotion:

- The proposal becomes a binding section of SPEC.md, the C header freezes,
  the distribution notes move to PORTING.md, and SPEC.md §15's vendor-runtime
  wording goes.

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
- After phase 4: promote the proposal to a binding, or keep the vendor
  runtime. Nothing before phase 4 is a public promise.
- Whether an SD card becomes a P4 requirement.

## Deliberately not in this plan

- A wasmtime host tier.
- PCM audio. Doom runs silent; `snd`'s rate and channels are pinned by the
  first cart that needs them, and `moy_audio` is vendored from moy-spec.
- moybyte's superset verbs for wasm carts, and a libc story beyond "no WASI
  imports".
- A Lua-to-wasm path. The proposal's answer is "nothing, deliberately".
- The cloud compiler.
- An interpreter as a spec feature. It is host policy, and #158's
  measurements say it does not pay for itself.
- A framebuffer for Lua carts. SPEC.md §12.6 stands; §15 carves the one
  exception, for a cart that owns its own memory.
- An ESP-IDF engine component in libmoy, so another ESP32 OS can take the
  chip-side engine under MIT instead of re-deriving it from `native/moy_wasm`.
  Deferred until another OS wants it (owner, 2026-09-29); moy-spec's desktop
  and web players already show the job off the chip.
