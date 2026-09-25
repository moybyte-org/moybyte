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
- **One cart, every board: no fragmentation.** A compiled cart that runs on
  a P4 board and not on an S3 board is not a moybyte cart, exactly as the
  Lua tier's floor exists so a script cart runs on modest hardware. The
  compiled tier's floor is therefore the FLOOR board's share of the
  cart-runtime reserve, a cart's manifest may declare memory up to that
  floor and no more, the check command and the store refuse above it, and
  the tier ships on every console board or on none. A cart that needs more
  than the floor is a demo, not a cart.
- **Doom is a locally built demo, never a cart of ours.** doomgeneric is GPL
  and the shareware WAD forbids consideration and derivative works, so the
  port follows the Celeste rule in `THIRD_PARTY.md`: a recipe fetches both,
  prints both licences, builds the cart on the developer's machine, and it
  is never hosted, seeded or shipped. It runs from the launcher only if it
  fits the tier's floor, on every board; that is a measurement, not a
  verdict, and the next decision names the levers.
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
  partition-table change and a full-erase reflash per device. If none fits
  the floor, Doom stays the spike demo on every board.

## The phases

Each step names its repository, what it produces, and the guard that says it
is done. A guard is a test or a check script, never prose.

### Landed

Phases 1 and 2 and the Player half of phase 3 are in (2026-09-25): the
engine is vendored and taken by every console board, moy-spec carries the
binding candidate and libmoy's import table, and a compiled cart runs from
the launcher on the host and on all four boards, under guards in every
on-glass suite. The numbers are on #158; how each piece works is its README
(`native/moy_wasm/README.md`, `native/moycore/README.md`, and the proposal in
moy-spec). A full-frame blit cart presents well above the tick model's 30 on
every board, so the per-board fps floor the guards pin is the ceiling that
matters, not a predicted one.

### Phase 3, the rest — Doom, signing, the table's storage (moybyte)

- **The table's storage.** Re-vendor libmoy for its read-only import table
  and host-owned registration storage, and allocate that storage per session
  in PSRAM, which returns its bytes of internal SRAM on every board; drop the
  workaround moycore carries for closing an unopened binding.
- **Signing.** Per-architecture modules are signed with the OTA key and a
  board loads only a signed module whose key matches; a tampered module is a
  fixture the on-glass suites refuse.
- **Doom, built by the recipe.** A recipe fetches doomgeneric and the
  shareware WAD, prints both licences, and builds a `.moy` locally on the
  `"moy"` imports: `blit` with its 256-entry palette, the WAD through the
  cart's own `read`, `time()` as the clock, no blocking import, pacing from
  the tick model. It runs from the launcher on every board or on none: the
  three levers (a smaller zone, a larger reserve, text in flash) are measured
  once on the floor board, and if none fits, Doom stays the spike demo.
- **Guards:** the tampered module refused on glass; Doom's frame CRC against
  the host run at named tics, with the level transition the spike saw
  excluded by name; if Doom fits, its launcher run in every suite with an
  fps floor.

### Phase 4 — second host and promotion (moy-spec; when a second author or host exists)

- The desktop player runs wasm carts through libmoy's table under WAMR.
- The browser runner instantiates the cart as a sibling module whose imports
  are JavaScript adapters over the same C thunks. Never an engine inside the
  engine.
- One conformance scene per new import, with an RGB golden type decided in
  phase 2 because the palette-index goldens cannot represent a 256-entry
  blit; refusal fixtures run on every host.
- Then, and not before: the proposal becomes a binding section of SPEC.md,
  the C header freezes, the distribution notes move to PORTING.md, and
  SPEC.md §15's vendor-runtime line goes.

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
- Whether the Doom glue under `experiments/wasm_aot/doom/` is marked
  GPL-2.0-or-later, which is what linking into doomgeneric implies.
- Human testing on every touched board before any of this reaches master.
- After phase 4: promote the proposal to a binding, or keep the vendor
  runtime. Nothing before phase 4 is a public promise.
- Whether an SD card becomes a P4 requirement.

## Deliberately not in this plan

- A wasmtime host tier.
- `blit565`. The proposal measured it as the slow route on the floor board;
  it returns when a cart that is inherently direct-colour asks for it.
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
