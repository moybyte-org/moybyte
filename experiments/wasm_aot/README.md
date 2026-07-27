# wasm_aot — is a WASM cart runtime fast enough for an emulator / 3D game? (#158)

Measured 2026-07-27, host + **ESP32-P4 on glass**. Sibling of `experiments/lua_bridge/`
(the #67 Lua spike) and deliberately the same shape: one workload, run under every
candidate runtime, compared on the same board at the same clock.

**Verdict: interpreted WASM is not worth a third runtime (1.09× Lua). AOT is (16×).**

## The workload

A representative **6502 interpreter inner loop** — fetch → decode → dispatch →
execute → flags → memory — written twice, line-for-line equivalent:

- `spike6502.lua` — Lua 5.4, dispatch via a table of closures (the idiomatic fast shape)
- `core6502.c` — C, dispatch via a table of function pointers (`call_indirect`; the
  structural twin of the Lua version, deliberately, so the comparison is best-form
  vs best-form)

Memory model is the real NES one (2 KB RAM mirrored to `$0000-$1FFF`, PRG at `$8000`).
The test program is a store/increment/compare/branch loop averaging **3.0 cycles per
instruction** (the NES average is ~3.5, so the fps figures below are the pessimistic
direction).

All runtimes returned **identical cycle counts** (5,997,400 for 2M instructions) with
`bad_opcodes=0`. Same work, three engines.

## Results

**ESP32-P4, on glass** — 360 MHz, L2 cache 256 KB (pinned in `sdkconfig.defaults` to
match the console build, so these compare directly to the `moy_lua` run):

| runtime | instr/s | NES fps (CPU only) | vs Lua |
|---|---|---|---|
| Lua (`moy_lua`) | 0.173 M | 17.5 | 1.0× |
| WASM fast-interp (WAMR 2.4.5) | 0.188 M | 19.0 | 1.09× |
| **WASM AOT (XIP)** | **2.828 M** | **284.8** | **16.3×** |

Pure-arithmetic reference (`spin`, 1M iterations): Lua 5.2 M/s · interp 12.35 M/s ·
**AOT 476 M/s (91× Lua)**.

**Host (x86-64), same cores:**

| runtime | instr/s |
|---|---|
| Lua 5.4 (lupa) | 7.92 M |
| WAMR fast-interp | 18.45 M |
| WAMR AOT | 257.3 M |

The AOT-over-interp speedup is **15.0× on device, 14× on host** — it holds across
architectures. **NES needs 60 fps for the CPU; AOT delivers 285**, i.e. 4.7× headroom
for the PPU/APU and the console's own frame cost.

## Hardware-learned constraints (the valuable part)

**1. The P4 registers ZERO exec-capable heap** — measured, `heap_caps_get_free_size(
MALLOC_CAP_EXEC) == 0`. WAMR's esp-idf `os_mmap()` allocates AOT text with
`MALLOC_CAP_EXEC`, so **plain AOT can never load on this board**; it fails with
"allocate memory failed". XIP is not an optimisation here, it is the only route.

**2. XIP code must be mapped on the INSTRUCTION bus, which breaks "a cart is a file."**
Embedding the `.aot` in a `const` array puts it in flash `.rodata` = the DROM (data)
mapping; it loads fine and then faults on the first call (`Instruction access fault`,
`MTVAL` inside the DROM range). It has to live in its own flash partition, mapped with
`esp_partition_mmap(..., ESP_PARTITION_MMAP_INST, ...)` — see `partitions_spike.csv`.
**Consequence for #158: you cannot read `main.aot` out of a `.moy` folder on SD/VFS and
call it.** Installing a WASM cart means writing its AOT into a scratch partition and
re-mapping (`esp_partition_write` works at runtime) — an install step with flash wear,
not open-and-run. In a browser this constraint does not exist.

**3. XIP is the SLOWER AOT mode** (indirect calls through a symbol table, no LLVM
intrinsics), so 2.828 M is the **pessimistic** AOT figure, not the optimistic one.

**4. Footprint: ~180 KB internal RAM** for runtime + module, against the Lua core's
31 KB heap. Fine on the P4; on the S3's 512 KB this is the harder problem (cf. #66).

**5. AOT is per-architecture.** The portable artifact is the `.wasm`; the `.aot` is a
disposable build product. A store would fan out riscv32-ilp32f (P4) and xtensa (S3,
**untested**) alongside the `.wasm` for host and browser — and the browser, needing no
AOT at all, is the fastest tier because it JITs.

## Integration gotchas — six, none hard, all invisible until you hit them

Recorded because they are the honest cost of "a third runtime" (cf. #67's tail):

1. `CONFIG_WAMR_ENABLE_LIBC_WASI` **defaults to `y`** in WAMR's Kconfig and does not
   compile for riscv32 (`os_timespec` vs `struct timespec` in `locking.h`).
2. `WAMR_BUILD_REF_TYPES` defaults **differently** between the linux and esp-idf build
   paths (1 vs unset).
3. A `switch` (→ `br_table`) core loads on the linux build and is **rejected by the
   esp-idf build** ("br_table targets must all use same result type") with identical
   `-D` flags. Hence the function-pointer dispatch. Unresolved; worth a bug report.
4. WAMR must run on a **real pthread** — IDF's `pthread_self()` asserts under a plain
   FreeRTOS task like `app_main` ("Failed to find current thread ID!").
5. `exec=0` (above).
6. DROM vs IROM for XIP (above).

Also: `wamrc` ships as a **prebuilt x86-64 binary** in WAMR's GitHub releases — no LLVM
build needed. Runtime and `wamrc` versions must match (AOT files carry a format
version); this spike pinned both to **WAMR-2.4.5**.

## What this does and does not prove

It is a **CPU core, not an emulator**. 285 fps CPU-only means the CPU has stopped being
the constraint; it says nothing about a PPU, memory-mapped I/O dispatch, or the
console's frame budget. And nothing here is integrated with the console — this is a bare
ESP-IDF app. The real work is #158's: imports trampolining to the same `make_api`
closures the Lua VM already uses, manifest plumbing, crash-line mapping, and on the P4
the install-to-partition step.

**Two API gaps block any of this from being useful**, independent of runtime (both
already noted in #158's worked example):

- **No framebuffer verb.** A software 3D rasterizer that reaches pixels through `pset`
  pays a WASM→host trampoline per pixel — 76,800 per frame at 320×240, dead on arrival.
  Give a cart a linear-memory buffer we blit once and the rasterizer runs at native
  speed; at 320×240 that is ~4.6 M pixel-writes/s against ~476 M ops/s, comfortably in
  budget. **"Fast 3D game" is gated on this verb, not on the runtime.**
- **No binary/user file access.** No cart verb reaches a ROM the user supplies — which
  is also the legally load-bearing part of any emulator story.

Caveat on the 91× arithmetic figure: this module's linear memory was tiny and internal.
A cart wanting megabytes lands in PSRAM, where #66's bandwidth wall — not compute —
sets the pace.

## Reproducing

```bash
./build.sh                      # wasm + both .aot + the generated headers
.venv/bin/python spike_host.py  # host Lua reference
# device: flash wasm_spike/ (see build.sh notes), then
.venv/bin/python read_spike.py /dev/ttyACM0
```

`build.sh` documents the toolchain: clang's wasm32 backend + `rust-lld` as the wasm
linker (no `wasm-ld` on this box), and the prebuilt `wamrc`.

**The device half replaces the console firmware.** Restore with
`make firmware-flash-p4 PORT=/dev/ttyACM0` and verify with `tools/p4_autotest.py`.
