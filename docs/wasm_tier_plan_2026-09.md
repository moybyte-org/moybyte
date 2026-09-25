# The WebAssembly cart tier — implementation plan (2026-09)

**What this is.** The steps that turn the #158 spike into a third cart
runtime, in the order their dependencies force, with the repository each step
lands in and the guard that says it is done. Measurements live in #158. The
ABI lives in moy-spec (proposals/wasm-runtime.md, which SPEC.md §15 points
at). This document is the sequence and the decisions that fix it; it is dated,
and it moves to `docs/history/` when the tier ships.

## Decisions this plan rests on (2026-09-25)

- **WebAssembly, not native ELF.** The lineup is three instruction sets plus
  a browser; one `.wasm` runs on all of them and the per-architecture module
  is a derived cache. Linear memory is bounded and imports are the only
  capability surface, so a cart cannot reach the console heap or the radio.
  There is no firmware symbol table to version. The cart's memory is one
  fixed block sized by its manifest. The author's toolchain is clang.
- **A moy-spec binding, not a moybyte-only runtime.** SPEC.md §3.1 already
  makes an unknown `runtime` a clean refusal, so a host that lacks the tier
  pays nothing. The import table *is* the verb table, which is the spec's
  own subject. The hosts that would run it beyond the boards, the desktop
  player and the browser runner, are moy-spec's. The binding stays a
  *proposal* until phase 5's two-host golden; nothing before that is a public
  promise.
- **Friction lives in code, not prose.** The board-specific knowledge lives
  in a pinned fork of WAMR, the binding ships inside libmoy, the compiler
  ships prebuilt. A port adds a component and a call, exactly as it does
  for Lua.
- **WAMR is carried as a fork, pinned by hash.** moybyte-org/wasm-micro-runtime,
  branch `moybyte-2.4.5`: one commit over the upstream 2.4.5 tag with the
  ESP32-S3 and ESP32-P4 platform fixes, and the whole upgrade path is
  rebasing that commit onto the next tag. Upstreaming is optional and off
  the critical path (decided 2026-09-25, to stay clear of upstream's
  contribution process).
- **How a host executes the module is host policy** (AOT, XIP, per-arch
  caches, an interpreter for small carts) and never enters the spec. The
  `.wasm` is the only artifact in a cart.
- **Doom is the first cart.** It is the demo that proves the tier, and the
  port that found the draft ABI's two gaps: the frame blit needs a 256-entry
  palette, and a ported engine needs a clock and a file-read import.

## The phases

Each step names its repository, what it produces, and what says it is done.

### Phase 1 — the gate: WAMR inside the real console image (moybyte)

The spike is closed: the T-Deck is back on the console firmware, the fork is
pinned, the moy-spec proposal is corrected, and #158 carries the verdict.

- A native module beside `native/moy_lua/` (new: native/moy_wasm) wrapping
  WAMR's ESP-IDF component with the AOT loader only: no interpreter, no
  WASI, no builtin libc. The runtime's pool lives in PSRAM; one executable
  mapping per loaded cart. Python surface: load a module file with a memory
  size, call an export, unload.
- Both console boards build with it. The build's headroom line and the
  boot's internal-SRAM figure are compared against the previous build and
  recorded in #158.
- A hello module (the spike's 6502 core will do) loads from the SD card as a
  per-architecture file into PSRAM and runs from the REPL on both boards.
- **Guard:** a case in `tests/test_tdeck_on_glass.py` and
  `tests/test_p4_on_glass.py` that loads the hello module and checks its
  return value.
- **Kill criterion:** internal SRAM on the T-Deck after runtime init must
  leave the flush's bounce buffers (`native/moy_flush/moy_flush.c`) and the
  Lua tier's allocator floor intact. If it does not, the tier ships on the
  P4 and in the browser first and the S3 waits for a diet. The plan does not
  change shape either way.

### Phase 2 — the ABI revision (moy-spec)

Revise proposals/wasm-runtime.md from a draft into a binding candidate:

- **Manifest.** `"runtime": "wasm"`, `"main"`, and a fixed linear-memory
  size the host checks against the module's own declaration before it
  allocates anything. The compiled tier's memory floor (the draft's open
  item 8), sized from Doom and the hello cart.
- **Imports.** Module `"moy"`: the verb table; `blit` with a 256-entry
  palette; `blit565`; a millisecond clock; asset or file read, decided
  together with #108; `snd` with its rate and channel count pinned by phase
  4's implementation.
- **Profile and exports** unchanged: wasm32 MVP, `_init`, `_update`, `_draw`,
  `memory`, no WASI imports. A libc story for ports: wasi-libc linked without
  WASI imports, standard output through a log import.
- **The C header** waits until the import list survives phase 4, as the draft
  already says.
- **A check command in the moy CLI** (new) that validates a module against
  the profile: imports only from `"moy"`, the required exports present,
  memory minimum equal to maximum equal to the manifest. Standard library
  only, a small section parser.
- **Guard:** moy-spec's docs check; the check command has tests against one
  conforming and one non-conforming module.

### Phase 3 — the binding inside libmoy (moy-spec, vendored into moybyte)

- A wasm binding beside libmoy's Lua binding: the native-symbol table that
  maps `"moy"` imports onto libmoy's verbs, `blit` and `blit565` resolved
  into libmoy's canvas, the fixed memory, and the load policy: the canonical
  `.wasm`, a host-supplied per-architecture module when the host has one,
  otherwise a clean refusal. WAMR is the engine on boards and on the desktop;
  the browser binds in JavaScript (phase 5).
- **Guard:** libmoy's test target runs the hello cart under WAMR on Linux
  and checks a frame CRC.
- moybyte re-vendors (`make vendor-libmoy`) and the phase-1 module's spike
  imports are replaced by the libmoy table.

### Phase 4 — the Player path (moybyte)

- **Host.** A sibling of `runtime/lua_host.py` runs a wasm cart through
  wasmtime on the PC, its imports trampolining to the canvas, so the dev
  loop never leaves the host.
- **Boards.** `"runtime": "wasm"` dispatches to the phase-3 binding;
  `runtime/device_boot.py`'s runtime-missing screen covers a board built
  without it.
- **Tick.** `_update` and `_draw` run under `runtime/tick_model.py` exactly
  as Lua's do. A blit lands in the cart's canvas under
  `docs/surface_model_v1.md` §4: one canvas class, no new invalidation path.
- **Diagnostics.** The PERF line covers wasm carts. The PC sampler sees AOT
  text as one unnamed bucket, since the module carries no symbols; recorded,
  not fixed.
- **Doom as a `.moy`.** The port under `experiments/wasm_aot/doom/`
  repackaged on the `"moy"` imports, the WAD as a cart asset read through the
  file import, a 256-entry blit. A store cart, not a seed cart, because of
  its size. Licences into `THIRD_PARTY.md`: doomgeneric is GPL-2.0, the
  shareware WAD ships as shareware.
- **Guard:** the hello cart in both on-glass suites; Doom's frame CRC at
  fixed game tics matches the host run, which the spike's harness already
  checks.

### Phase 5 — second host and conformance (moy-spec)

- The desktop player (the SDL2 port) runs wasm carts through the phase-3
  binding under WAMR.
- The browser runner instantiates the cart as a sibling module with imports
  bound to the runner's exported verbs. Never an engine inside the engine.
- A wasm twin of one conformance scene, the flat-shaded raycaster whose Lua
  sibling is already measured, passes the existing goldens on a board, in the
  desktop player and in the browser.
- Then, and not before: the proposal becomes a binding section of SPEC.md,
  the C header freezes, and PORTING.md gets its section: what a host needs,
  how it refuses, the floor.

### Phase 6 — toolchain and distribution (when a second author or host exists)

- **moy-spec releases** carry a prebuilt wamrc for the ESP32-S3 and for
  RISC-V. CI builds the recipe in
  `experiments/wasm_aot/toolchain/build_wamrc_xtensa.sh` once and caches it.
  The CLI's build command drives clang and, given an architecture, wamrc.
- **moybyte's store** serves per-architecture modules beside the canonical
  `.wasm`, keyed by the wasm hash, the architecture and the WAMR version,
  with an on-device cache directory. Compiling in the cloud so a cart can be
  written on the device alone is a separate proposal on top of this.

## Decision points

- **After phase 1:** the S3 is in the first release, or the P4 and the
  browser go first.
- **After phase 5:** the proposal is promoted to a binding, or it stays a
  vendor runtime. Nothing before phase 5 is a public promise.

## Deliberately not in this plan

- A Lua-to-wasm path. The proposal's answer is "nothing, deliberately", and
  it stands.
- The cloud compiler.
- An interpreter fallback as a spec feature. It is host policy, and #158's
  measurements say it does not pay for itself.
- A framebuffer for Lua carts. SPEC.md §12.6 stands; §15 carves the one
  exception, for a cart that owns its own memory.

## Housekeeping the spike left

- The T-Deck's panel lost its mode registers minutes into play under the
  spike's own driver (`experiments/wasm_aot/doom/README.md`, "the static").
  The console's driver may share the exposure: soak the console on the
  T-Deck, and if it reproduces, re-assert the registers in the flush. Not
  part of the tier.
