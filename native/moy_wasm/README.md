# moy_wasm — the WebAssembly cart tier's engine (phase 1)

`docs/wasm_tier_plan_2026-09.md` is the plan; this is the module its phase 1
built. It is the ENGINE only: the vendored WAMR runtime and the thread a
module runs on. There are no verbs here and no import table — that is C in
libmoy beside the Lua binding, hosted by moycore (phases 2 and 3) — so a
module that imports anything fails to instantiate today.

| piece | where |
|---|---|
| the runtime | `wamr/`: the AOT-only subset of Moybyte's WAMR fork (the plan's "carried as a fork"), copied by `tools/vendor_wamr.py` at the commit `wamr_vendor.json` records (`make vendor-wamr`; `tests/test_wamr_vendor.py` holds the copy to the fork) |
| the build | `micropython.cmake`: the runtime as its own static library, AOT only, no interpreter, no WASI, no builtin libc |
| the binding | `modmoy_wasm.c`: read a module file, run it on a thread, report |
| the key | `moy_wasm_key.h`: the provenance key a module must carry, per chip |
| the thread | `moy_wasm_thread.c`: the run's pthread, stack placed per board |

Which boards take it is `board.toml` data: each console board's
`[[native.shared.take]]` and the headless Zero's denial, each with its reason;
`tests/test_board_toml.py` holds every board file to deciding.

## A run

```python
import moy_wasm
moy_wasm.start("/sd/carts/wasm_hello/hello.aot", "step", (20000,))
moy_wasm.done()          # False while the thread runs
moy_wasm.result()        # waits (the VM lock released), then a dict
```

`start(path, export, args=(), loops=1, stack=None, psram_stack=None)` reads the
module file through the VFS (the T-Deck's SD and the P4s' flash store look the
same) into a PSRAM buffer and starts a thread that does, `loops` times over:
load, check the key, instantiate, call `export(*args)` with i32 arguments, and
unload. `stack`/`psram_stack` override the board's setting for a measurement.
One run at a time.

**Everything WAMR does happens on that thread**, the runtime's init and
teardown included: WAMR's platform layer calls `pthread_self()`, and IDF
answers that only for a pthread -- which the MicroPython VM's task never is.
The thread runs on the VM's core at the VM's priority, so a run takes the VM's
time and never core 0's radios and flush feeder.

`result()` carries `ok`/`error`, the export's `value` (and `mismatches`: passes
whose value differed from the first), `loops`, the module's `wasm` hash from
its key, `load_us`/`load_us_max`, `inst_us`, `call_us` (first, `_min`,
`_max`), `run_us`, `stack`/`stack_psram`/`stack_used` (the thread's high-water
mark), `pool`/`pool_peak`, and the internal-SRAM account: `sram_before` (free
when the run was started) and `sram_min` (the heap's local-minimum monitor,
started before the thread exists, so the thread's own stack and control block
are in it), plus `psram_min`.

`mem()` → `(internal_free, internal_largest, internal_min_since_boot,
psram_free, psram_largest)`. `KEY` is the key this build wants after the wasm
line, `FORK` the fork commit, `STACK` the board's default `(bytes, in_psram)`,
`POOL` the runtime pool's size.

## Where the memory goes

Nothing is held between runs: the module allocates no memory at boot, so what
it costs the idle desk is its static data (about 0.6 KB of internal RAM) and
nothing else. During a run:

| what | where |
|---|---|
| the module file | PSRAM, read once, freed at the end of the run |
| the runtime's pool (module and instance structures, the exec env) | PSRAM, `MOY_WASM_POOL_BYTES` (256 KB), allocated at the run's start and freed at its end |
| the AOT text | PSRAM: the S3 fetches it through the instruction-bus alias, the P4's external RAM carries no PMP entry |
| linear memory, AOT data sections, any runtime allocation of 1 KB or more | PSRAM only (`WASM_ESPIDF_PSRAM_THRESHOLD` in the fork's esp-idf platform) |
| the run's stack | per board, `MOY_WASM_STACK_BYTES` / `MOY_WASM_STACK_PSRAM` in `mpconfigboard.h`: 16 KB in PSRAM on every board |
| the thread's control block | internal SRAM, under 1 KB |

**A load PSRAM cannot serve is refused**, never served from internal SRAM:
the fork's allocator returns NULL for the text and for any data allocation over
the threshold rather than falling back, because internal SRAM on the S3 is what
WiFi, BLE and the display's DMA share. The stack defaults to PSRAM for the same
reason: measured on the Guition S3 on 2026-09-25, an internal 16 KB stack cost a run 17 KB of
internal SRAM against about 1 KB for a PSRAM one, at the same speed
(`step(400000)` 276 vs 277 ms), and with WiFi and BLE up that board has no
17 KB to give. The number that pins this is `WASM_RUN_SRAM_MAX` in
`tests/on_glass.py`.

The AOT native-stack check is live: the run hands the runtime its stack's low
end plus a 2 KB guard (`wasm_runtime_set_native_stack_boundary`), and the fork
reports the calling task's real stack start where upstream reported none. An
overflow traps as "native stack overflow".

## The cache sync

On the S3 the loader writes the text through the data bus and fetches it
through the instruction bus, so the two must be made to agree. The fork does
it by RANGE, twice (right after the copy, because the loader reads the text
back through the instruction alias before relocating it, and at the end of the
load): `esp_cache_msync` write-back on the data range, then invalidate on the
instruction range, under IDF's cross-core cache lock. Upstream wrote back the
whole data cache and toggled the instruction cache off and on, which faults the
other core if it is executing from flash or PSRAM at that moment — and on a
console it always is. The P4 does the same range sync plus `fence.i`. The
on-glass load/unload loop under a live cart and WiFi is the guard.

## Provenance

A per-architecture module is native code; the sandbox is whatever the compiler
emitted. So a module loads only if its custom section `moybyte.key` says:

```
moybyte-aot 1
wasm <sha256 of the canonical .wasm>
fork <the fork commit this image runs>
target xtensa          | riscv32
cpu esp32s3            | generic-rv32
abi -                  | ilp32f
features -             | +m,+a,+f,+c
opt 3
size 0                 | 3
bounds 1
stack-bounds 1
xip 0
```

Everything after the wasm line must equal `"fork " MOY_WASM_FORK_COMMIT "\n"`
plus the chip's block in `moy_wasm_key.h`, byte for byte; an absent, malformed
or different key is refused with the first field that differs, after the
parse and before anything in the module runs. `tools/wasm_module.py` reads the
same header to write keys and to choose wamrc's flags, so a module it builds
carries exactly the key this check wants. Signing arrives with phase 3.

## Stopping a run

**A runaway export cannot be stopped mid-loop.** `terminate()` calls
`wasm_runtime_terminate`, which raises "terminated by user" in the instance —
and AOT code reads the instance's exception only when an import call returns
(`aot_emit_function.c`, with bounds checks on, which the key pins). Measured on
the Guition S3 on 2026-09-25: `spin(400000000)` calls no import, ran its whole 1.7 s after
`terminate()`, and then returned the termination. A cart that calls any verb
would stop at its next verb; a loop that calls none runs until it returns.
WAMR's loop-edge checks (`check_suspend_flags`) are emitted only for
shared-memory modules under `--enable-multi-thread`, so stopping such a loop
takes a compiler change in the fork — a flag the key would then carry — or the
board restarting. `tests/on_glass.py`'s `wasm_runaway_runs_to_its_end` pins the
current answer.

## Testing

- `tests/test_wamr_vendor.py`: the copy is the fork at the pinned commit (read
  from the clone's git objects when `experiments/wasm_aot/wamr` has it), the
  interpreter and compiler stayed behind, and one pin names it everywhere.
- `tests/test_wasm_module.py`: the key and the flags the builder derives.
- On glass, every declaring board's suite: the idle cost against a module-free
  image of the same tree, the hello module and four foreign modules, the two
  stack placements, the termination answer, a Lua cart after a wasm run, a
  load/unload loop under a live cart and WiFi, and a run with WiFi and BLE up.
  The suites build the modules with the pinned compilers
  (`python3 tools/wasm_module.py compilers`) and push them into the board's
  store under `wasm_hello/`, which is not a `.moy` folder and never lists as a
  cart.
