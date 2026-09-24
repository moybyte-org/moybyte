# wasm_aot — is a WASM cart runtime fast enough for an emulator / 3D game? (#158)

Measured 2026-07-27 on the host + **ESP32-P4 on glass**, and 2026-09-24 on the
**ESP32-S3 (LilyGO T-Deck) on glass**, where the same modules run and **Doom runs**
(`doom/`). Sibling of `experiments/lua_bridge/` (the #67 Lua spike) and deliberately
the same shape: one workload, run under every candidate runtime, compared on the
same board at the same clock.

**Verdict: interpreted WASM is not worth a third runtime (1.09× Lua). AOT is (18× the
interpreter on the P4, 9× on the S3), it loads from a FILE into PSRAM on both boards,
and it carries Doom: 21–26 fps on the S3's glass with a third of every frame spent
on the SPI panel, ~19 ms of work per frame on the P4.**

## ESP32-S3 (T-Deck), 2026-09-24 — the Player tier is in scope

Same 6502 core, same three runtimes, on the T-Deck at the console's own settings
(240 MHz, 32 KB icache / 64 KB dcache / 32 B lines, octal PSRAM; the spike runs PSRAM
at 80 MHz where the console runs 120). The wasm is unchanged; the AOT is
`wamrc --target=xtensa --cpu=esp32s3 --size-level=0` (why that flag: below).

| runtime | instr/s | NES fps (CPU only) | vs interp | `spin` |
|---|---|---|---|---|
| WASM fast-interp (WAMR 2.4.5) | 0.172 M | 17.3 | 1.0× | 12.3 M/s |
| **WASM AOT, relocated into PSRAM** | **1.467 M** | **148** | **8.5×** | 273.6 M/s |
| **WASM AOT, XIP from a flash partition** | **1.541 M** | **155** | **9.0×** | 273.2 M/s |

(P4, for scale: interp 0.188 M, AOT-XIP 2.828 M at 360 MHz. The S3 interpreter is
within 1% of the P4's on this dispatch-heavy core; the S3's Lua was not measured on
this core.) Identical cycle counts (5,997,400) in all three modes.

**And Doom** — doomgeneric compiled to wasm32 (wasi-sdk 24), 320×200, the shareware
IWAD in a 5 MB flash partition, a 5 MB linear memory with a 3 MB zone, sound off —
runs on the T-Deck's glass, demo and menus, through **six imports**: ticks, sleep,
key, draw-frame, WAD size, WAD read. That draw-frame import is the "framebuffer
verb" the P4 write-up said any real C cart is gated on, in its smallest form.

| module (all `--size-level=0`) | .aot | Doom's own ms/frame | fps in the E1M1 demo |
|---|---|---|---|
| XIP from flash (`--xip`: indirect calls, no LLVM intrinsics, bounds checks) | 912 KB | ~33 | ~21 |
| plain AOT relocated into PSRAM (direct calls, intrinsics, bounds checks) | 992 KB | ~26 | ~25 |
| XIP from flash, `--bounds-checks=0` (the sandbox priced, not proposed) | 753 KB | ~24 | ~26 |

Every frame also pays **13.0 ms for the panel**: 128 KB over the ST7789's 80 MHz SPI,
blitted synchronously in the game thread through two internal-SRAM bounce buffers.
That is the T-Deck's physical wire, and the obvious next lever: hand the flush to
core 0 the way `native/moy_flush` does and the frame is Doom's own time, i.e. the
35 Hz tic cap. The title screen already sits at 34 fps (Doom's 35 Hz). Load: 170 ms
(XIP) / 290 ms (PSRAM), Doom's own init 0.5 s; PSRAM free while running 2.9 MB (XIP)
/ 2.2 MB (PSRAM-resident); internal SRAM free ~100 KB with WAMR, the LCD driver and
a 160 KB game thread stack up.

`doom/README.md` has the pieces and the reproduce steps; `doom/doom_host.py` runs
the same module under wasmtime on the host with the same six imports (the reference
that separated a module fault from a port fault twice).

### What the S3 taught, in the order it bit

1. **The prebuilt `wamrc` has no Xtensa backend.** `--target=xtensa` fails with
   "llvm get target from triple (xtensa-pc-linux-gnu) failed", and Espressif's
   esp-clang tarballs ship no LLVM dev libraries to link one against.
   `toolchain/build_wamrc_xtensa.sh` builds what WAMR's own `build_llvm.py
   --platform xtensa` would — Espressif's LLVM fork at `xtensa_release_18.1.2`, X86 +
   experimental Xtensa, static, no tools — then wamrc against it. ~45 min, ~3 GB.
   The P4 write-up's "same prebuilt wamrc, `--target=xtensa`" was wrong.
2. **The S3's instruction-bus alias is fetch-only.** PSRAM at data address D is
   fetched at D + 0x0600_0000 (one MMU table serves both buses,
   `SOC_MMU_LINEAR_ADDR_MASK`), but a load or store through that alias is
   `LoadProhibited` / `StoreProhibited`. So AOT code may not READ its own text — and
   Xtensa code reads its literal pool with PC-relative `l32r`. Hence
   **`--size-level=0`** (LLVM's large code model): constants materialise in
   registers, `literal_size=0`, and an XIP module carries **zero relocations**.
   Without it: `LoadProhibited` on the first literal, and past 256 KB of text the
   `l32r` cannot reach the pool at all ("target address out of range").
3. **WAMR's esp-idf "dual bus mirror" (`WASM_MEM_DUAL_BUS_MIRROR`, on by default
   for the S3) is broken on IDF 5.5**: its delta is `SOC_IROM_LOW - SOC_IROM_HIGH`
   (lands outside every mapped range; should be `- SOC_DROM_LOW`) and it clears fresh
   exec memory through the fetch-only alias. `toolchain/patch_wamr_s3.py` fixes both,
   marker-guarded. With that, a plain `.aot` relocates into PSRAM and runs — which is
   the S3's "a cart is a file": no partition-install step, unlike the P4.
4. **XIP from flash splits the same way**: the loader parses the file through the
   DATA mapping, the CPU fetches through the INST one. Asking `esp_partition_mmap`
   for both returns the SAME address (shared table), so the alias is the constant
   delta; the patch's `os_get_ibus_mirror()` / `os_register_xip_window()` carry it
   into the loader's `module->code`.
5. **The esp-idf loader refuses an XIP file with text relocations** ("cannot apply
   relocation to text section ... --enable-indirect-mode"). `aot_prelink.py`
   resolves the `l32r` ones on the host and strips the group — only needed when a
   module is built with a size level other than 0; `aot_dump.py` shows what a
   file carries.
6. **wamrc compiles the WASI pointer conversions INTO the module.** It knows
   libc-wasi's signatures (`fd_write` is `(i*i*)i`), so a host stub declared
   `(iiii)i` receives native pointers and rejects them as "out of bounds memory
   access" — before Doom has printed a byte. Host WASI stubs must use WAMR's own
   signature strings and take native pointers. An hour, and the reason
   `doom_host.py` exists.
7. **The fast interpreter's loader patches bytecode in place**: a `.wasm` handed
   to it straight off the flash mapping dies with "Dbus write to cache rejected".
   Copy it to RAM first; `.aot` files load fine from the mapping.
8. `MALLOC_CAP_EXEC` on the S3 is 263 KB of internal SRAM with
   `CONFIG_ESP_SYSTEM_MEMPROT_FEATURE` off (the 6502 spike's config) and **0** with it
   on (the Doom app's) — moot once code lives in PSRAM or flash, but it is why the
   two apps print different `exec=` lines.

### What this does not prove

A bare ESP-IDF app, not the console: no WM, no Python VM, no WiFi beside it, PSRAM
at 80 MHz where the console tunes 120. No sound. Input is demo-grade (the C3
keyboard reports presses only, so keys are timed holds; the trackball turns). The
WAD is a partition and the module is a partition — the store / install half of
#158 is untouched, though on the S3 it can now be "copy the `.aot` into PSRAM"
rather than "write a flash partition".

## ESP32-P4, revisited 2026-09-24 — "a cart is a file" holds here too

The July constraint below ("plain AOT can never load on this board; XIP is the
only route") was a HEAP fact, not a hardware one. IDF's `cpu_region_protect.c`
gives the P4's external RAM no PMP entry at all ("default all permissions" — its
own comment), and the one region that is locked read-only is the flash rodata
mapping, which is exactly where the July run had embedded its `.aot`. What made
`MALLOC_CAP_EXEC` come back empty was `CONFIG_ESP_SYSTEM_PMP_IDRAM_SPLIT`
(default on; the console's P4 build runs with it OFF), and WAMR only ever asks
for that capability. So `toolchain/patch_wamr_s3.py` step 6 has the P4 take an
executable mapping from PSRAM, and does the cache sync a unified bus needs after
the loader has written the text (`esp_cache_msync` write-back, instruction-cache
invalidate, `fence.i`). Same 6502 core, same board, console clock and PSRAM (hex,
200 MHz):

| runtime | instr/s | vs interp | `spin` |
|---|---|---|---|
| WASM fast-interp | 0.189 M | 1.0× | — |
| AOT XIP from a flash partition (July's only route) | 2.786 M | 14.7× | 475 M/s |
| **AOT loaded into PSRAM** (`--target=riscv32 --target-abi=ilp32f`) | **3.505 M** | **18.5×** | **570 M/s** |

Direct calls and LLVM intrinsics buy the PSRAM-resident module 25% over XIP, as
the July write-up predicted. **Doom too**: the same `doom.wasm` compiled with
`--cpu-features=+m,+a,+f,+c` (without `+m` LLVM emits `__umodsi3` libcalls WAMR's
symbol table lacks; the double-float helpers it still wants are all registered),
loaded from the partition into PSRAM in 470 ms, ran the E1M1 demo headless at
**~19 ms per frame** — 50 fps with no panel in the loop — against the S3's ~26 ms
of Doom work per frame. Only 1.4× the S3 at 1.5× the clock: Doom's renderer
walks a 5 MB linear memory in PSRAM, and the P4's PSRAM path is the wall #77 and
`p4-ppa-reads-psram-slowly` already describe. `CHIP=p4 doom/flash_doom.sh` and
`doom/read_doom.py PORT --pulse` are the P4 halves; the app is headless there
(the P4's glass is the DSI tier, not a spike's).

So the install story is now the SAME on both boards: read the `.aot` — from SD,
from a download, from anywhere — into PSRAM and call it. The flash-partition
XIP path stays available on both as the PSRAM-cheaper option (0.7 MB of PSRAM for
Doom's text), and it is the slower one on both.

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

**1. The P4 registers ZERO exec-capable heap under the default
`CONFIG_ESP_SYSTEM_PMP_IDRAM_SPLIT`** — measured, `heap_caps_get_free_size(
MALLOC_CAP_EXEC) == 0`, and WAMR's esp-idf `os_mmap()` allocates AOT text with
`MALLOC_CAP_EXEC` alone, so plain AOT fails with "allocate memory failed" on a
stock WAMR. That is the heap's answer, not the chip's: PSRAM carries no PMP entry
and executes fine (the P4 section above), and with the split off — how the console
builds — the internal heap is exec-capable too (378 KB free in the bare app).

**2. XIP code cannot live in flash `.rodata`.** Embedding the `.aot` in a `const`
array puts it in the DROM mapping, which the PMP locks read-only; it loads fine and
then faults on the first call (`Instruction access fault`, `MTVAL` inside the DROM
range). An XIP module needs its own flash partition, mapped with
`esp_partition_mmap(..., ESP_PARTITION_MMAP_INST, ...)` — see `partitions_spike.csv`.
That is the XIP route's constraint only: a plain `.aot` read out of a `.moy` folder
on SD/VFS into PSRAM and called works on this board (the P4 section above), which is
the install story #158 wants. The partition write is the option that saves PSRAM,
not the only door.

**3. XIP is the SLOWER AOT mode** (indirect calls through a symbol table, no LLVM
intrinsics), so 2.828 M is the **pessimistic** AOT figure — the PSRAM-resident
module measured 3.505 M on the same board (above).

**4. Footprint: ~180 KB internal RAM** for runtime + module, against the Lua core's
31 KB heap. Fine on the P4; on the S3's 512 KB this is the harder problem (cf. #66).

**5. AOT is per-architecture.** The portable artifact is the `.wasm`; the `.aot` is a
disposable build product. A store would fan out riscv32-ilp32f (P4) and xtensa (S3,
measured above) alongside the `.wasm` for host and browser — and the browser, needing
no AOT at all, is the fastest tier because it JITs.

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
build needed **for riscv32**; the Xtensa backend is not in it (S3 section, item 1).
Runtime and `wamrc` versions must match (AOT files carry a format version); this
spike pinned both to **WAMR-2.4.5**.

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
linker (no `wasm-ld` on this box), and the prebuilt `wamrc`. `TARGET=s3 ./build.sh`
builds the Xtensa modules instead (needs `toolchain/build_wamrc_xtensa.sh` first),
the device half is `idf.py -B build_s3 -D SDKCONFIG=sdkconfig.s3 set-target esp32s3
build`, flashed with `--before usb_reset` at 0x0/0x8000/0x10000 plus the XIP module
at 0x210000, and read with `read_spike.py PORT --attach` (the T-Deck is attach-only).
Restore the console with `make firmware-flash-tdeck-mainline PORT=...`.

**The device half replaces the console firmware.** Restore with
`make firmware-flash-p4 PORT=/dev/ttyACM0` and verify with `tools/p4_autotest.py`.
