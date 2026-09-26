# moy_wasm — the WebAssembly cart tier's engine

`docs/wasm_tier_plan_2026-09.md` is the plan; this is the module its phase 1
built and its phase 3 opened to the Player. It is the ENGINE only: the vendored
WAMR runtime and the thread a module runs on. There are no verbs here and no
import table — that is C in libmoy beside the Lua binding
(`native/moycore/libmoy/moy_wasm.c`, vendored), hosted by moycore — so a module
run through `start()` below can import nothing, and a cart runs as a SESSION
moycore drives (see "A cart's session").

| piece | where |
|---|---|
| the runtime | `wamr/`: the AOT-only subset of Moybyte's WAMR fork (the plan's "carried as a fork"), copied by `tools/vendor_wamr.py` at the commit `wamr_vendor.json` records (`make vendor-wamr`; `tests/test_wamr_vendor.py` holds the copy to the fork) |
| the build | `micropython.cmake`: the runtime as its own static library, AOT only, no interpreter, no WASI, no builtin libc |
| the binding | `modmoy_wasm.c`: read a module file, run it on a thread, report; a cart's session |
| the session | `moy_wasm_session.h`: the C surface moycore drives a compiled cart through |
| the key | `moy_wasm_key.h`: the provenance key a module must carry, per chip, and the layout of its signature |
| the footprint | `moy_wasm_footprint.h`: what a load takes -- the pool, the block a module file is read into, the run stack -- stated once for the engine, the Player's fit check and the host twin |
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

`start(path, export, args=(), loops=1, stack=None, psram_stack=None, pool=None)` reads the
module file through the VFS (the T-Deck's SD and the P4s' flash store look the
same) into a PSRAM buffer, checks its signature (see Provenance; a refused
module ends the run at once, with no thread) and starts a thread that does,
`loops` times over: load, check the key, instantiate, call `export(*args)`
with i32 arguments, and unload. `stack`/`psram_stack` override the board's
setting and `pool` the pool's sizing rule, for a measurement: a module's
`pool_peak` under a pool bigger than the rule's is what the rule has to hold.
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
mark), `pool`/`pool_peak` (the pool's size for this module and its high-water
mark), and the internal-SRAM account: `sram_before` (free
when the run was started) and `sram_min` (the heap's local-minimum monitor,
started before the thread exists, so the thread's own stack and control block
are in it), plus `psram_min`.

`mem()` → `(internal_free, internal_largest, internal_min_since_boot,
psram_free, psram_largest)`. `footprint(memory, module_bytes)` → `(total,
block)`: what loading a module file of `module_bytes` for a cart declaring
`memory` bytes of linear memory holds in PSRAM at its peak, and the largest
single free block that peak asks for (see "A cart too big for the board").
`KEY` is the key this build wants after the wasm
line, `FORK` the fork commit, `STACK` the board's default `(bytes, in_psram)`,
`POOL` the runtime pool's base size (a run's `pool` adds a share of its
module's).

## A cart's session

A compiled cart the Player runs is a session: the same signature check,
thread, pool, load and provenance check as a run, held open for the cart's
life, with moycore — the host half, which owns the console — supplying the
callbacks (`moy_wasm_session.h`). The thread calls them once the runtime is up (moycore
registers libmoy's import table), once the module is loaded and its key checked
(the module's shape against the manifest's `memory`, `moy_wasm_check`), once it
is instantiated (bind it to the console) and for each hook, and before teardown.
`moy_wasm_session_open` also refuses a module whose key names another
`main.wasm` than the cart's (its sha256, which the board computes).

The MicroPython task blocks in each call while the thread runs the hook — the
thread is at the VM's priority on the VM's core, so it IS the VM's time — and
while it waits it serves `moy_wasm_on_vm` requests: the two imports that need
the VM, the cart's own file read (through the VFS, the last file held open) and
the config lookup, run on the task, so the thread never touches MicroPython.
One session at a time, never beside a `start()` run.

Where a board finds a cart's compiled module is host policy: `<main>.<chip>.aot`
beside `main.wasm` in the cart's folder, `CHIP` naming the chip
(`tools/wasm_cart.py` builds it; `moycore_glue.aot_path` finds it). A cart with
no module for this chip is refused on the Player's panel. The sync RPC declines
binary files, so a module never crosses between a browser and a board
(`runtime/moy_sync.py`): a compiled cart plays where its module was put.

**A full-frame blit presents above the tick model's 30 on every board.** The
plan expected the S3 to sit at or under it; measured on 2026-09-25 (the figures
are #158's) the Blit Wasm fixture — the cart's own raster rewriting all
320 x 240 indices plus the host's 256-entry palette resolve, every frame — ran
unpaced at over 45 fps on every console board, the Guition P4 lowest, the
Waveshare P4 at its 60 cap. A frame on this tier costs what the CART's raster
costs; the resolve is a few milliseconds. Each suite pins a floor for that
fixture and for the hello cart (`tests/test_*_on_glass.py`), measured with WiFi
off, the state a cart plays in.

## Where the memory goes

Nothing is held between runs: the module allocates no memory at boot, so what
it costs the idle desk is its static data (about 0.6 KB of internal RAM) and
nothing else. During a run:

| what | where |
|---|---|
| the module file | PSRAM, read once; a run keeps it for its passes, a cart's session frees it once the module is loaded (below) |
| the runtime's pool (module and instance structures, the module's data segments, the loader's relocation tables while it relocates, the exec env) | PSRAM, `MOY_WASM_POOL_BYTES` (256 KB) plus a quarter of the module (`MOY_WASM_POOL_SHARE`), allocated at the run's start and freed at its end |
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

**A cart's module file is gone before its memory is allocated.** The
session reads the file into a block the size of the linear memory the
manifest declares, loads it, and frees it before instantiating, so the
linear memory takes that block back: read into a block of its own size, the
file left a hole below the text that the linear memory could not use, and a
cart that fit the free PSRAM in total was refused for want of one block. The
load is freeable: the fork's AOT loader copies everything the module keeps,
its data segments included, into the pool, so nothing points into the file
once the load returns. A data segment left pointing into the file would be
copied out of freed memory at instantiation, which the reused block has
already zeroed -- a Doom build traps on its first call through a function
table, and the hello cart's file name reads as nothing. The hello cart's
greeting check in every suite is the guard.

The AOT native-stack check is live: the run hands the runtime its stack's low
end plus a 2 KB guard (`wasm_runtime_set_native_stack_boundary`), and the fork
reports the calling task's real stack start where upstream reported none. An
overflow traps as "native stack overflow".

## A cart too big for the board

A cart above the tier's floor is allowed, and a board that cannot fit it says
so before anything loads (the plan's 2026-09-26 decision). The Player asks the
runtime what the cart's load takes -- `moycore_glue.WasmRuntime.footprint`:
the manifest's `memory`, this chip's signed module as it sits in the store,
through `footprint()` above -- and compares it with `mem()`'s PSRAM free total
and largest block. A cart that does not fit opens the fit NOTICE: the Player's
panel under the title "Too big for this console." naming the cart, what it
needs and what the board has free, with the same way out as every panel and
no EDIT (`runtime/player.py`'s `fit_notice`). The footprint is the file's
block (which the linear memory takes back), the pool, the module's own size
for the text and data the loader maps, and the run stack; it over-counts the
text by the module's relocations and symbols, so a cart it passes has the
room.

A load that runs out of memory anyway -- the heap in more pieces than the
free total suggests -- gets the same notice: every allocation failure on the
start path, the engine's and moycore's and WAMR's "allocate ... failed", reads
`out of memory: ...`, and a MemoryError reads the same. The host twin refuses
by the same header against `wasm_host.MEMORY_LIMIT`, the biggest board's
PSRAM, so a cart the host refuses is one no board could run.

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
features -             | +m,+a,+f,+c,+unaligned-scalar-mem
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
carries exactly the key this check wants.

### Misaligned access

WebAssembly lets any load or store be misaligned, so an access whose address
the compiler cannot see is emitted at alignment 1, and a backend that thinks
its target cannot take a misaligned word splits it: four byte loads, shifts
and ors for every `i32.load`, four byte stores for every `i32.store`. Both
chips take a misaligned load or store of any width in hardware, so both
compilers are told so. On the P4 it is the `+unaligned-scalar-mem` feature in
the key. The Xtensa backend has no such feature, so the fork's compiler knows
it from the cpu: for `--cpu=esp32s3` an access is emitted at its own width.
What the split cost the Jet showcase, on both chips, is in #158.

The guard is a module that sweeps misaligned loads and stores of every
width, floating point included, across 72 KB of linear memory, so it crosses
every cache line in it and at least one MMU page, and counts every access
that read or wrote other bytes than byte-wise composition says
(`tests/fixtures/wasm/misaligned.c`). Every declaring board's suite runs it
and wants 0; a core that trapped instead would take the board down with it.

**And the file is signed the way OTA images are.** A module file is the
module, then an RSA signature (PKCS#1 v1.5, SHA-256), its length and the magic
`moybyte-sig1`; the signature covers a text naming the chip, the module's
length and its sha256, so every byte of the module -- the key section included
-- is under it (`moy_wasm_key.h` states the layout and the text, and
`tools/wasm_module.py` reads them from there). The check runs on the
MicroPython task the moment the file is read, before the thread exists: the
engine hashes the module and hands the text to `moy_ota.verify_sig`, the one
body every OTA manifest is checked with, against the keys the image trusts
(`moy_ota.OTA_PUBLIC_KEYS`). An unsigned module, a malformed trailer, a
signature for another chip, a byte changed anywhere or a key the image does not
trust is refused as `refused: unsigned module` / `malformed signature` /
`bad signature`, and the runtime never sees it. `tools/wasm_module.py` signs
every module it builds with the OTA signing key (`$MOYBYTE_OTA_SIGNING_KEY`,
else the file `make ota-keygen` writes) and `verify` answers as a board
would; `--unsigned` builds a module a board refuses.

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
- `tests/test_wasm_signing.py`: the signature, through the device's own
  `moy_ota.verify_sig` with a throwaway key -- a signed module verifies and
  comes back as it was built; a byte changed anywhere, the key section
  included, another chip's signature, another key's, an unsigned module and a
  malformed trailer are refused.
- `tests/test_moycore_glue.py`: the device glue's `WasmRun` over a fake moycore
  (the arguments `wasm_open` gets, the refusals before it) and the runtime's
  fit report (what it asks `footprint()`, through the store's gate).
- `tests/test_wasm_cart.py`: the same import table on the host, over WAMR built
  for Linux at this pin (`runtime/wasm_binding.py`) — the hello cart's pixel
  golden, the hooks under the tick model, a trap, quit, the runtime-missing
  panel, the fit notice (the footprint against the header's rule, the huge
  fixture past any board, the host's limit, a load that still runs out), a
  folder in the cart reading as a missing file, and the store's handling of a
  compiled cart.
- On glass, every declaring board's suite: the idle cost against a module-free
  image of the same tree, the hello module and six a board refuses (no key,
  another fork, other flags, another chip, no signature, one byte of a signed
  module's key changed), the two stack placements, the termination answer, a
  Lua cart after a wasm run, a load/unload loop under a live cart and WiFi,
  and a run with WiFi and BLE up; then the Player path — the hello and blit
  carts run from the launcher at their fps floors, a cart with no module for
  this chip refused, a cart whose module was tampered with after signing
  refused, the huge fixture -- 40 MB of declared memory, past every
  board's PSRAM -- refused with the fit notice, the hello cart running after
  it, and the Read Dir fixture's `read` of its own `src/` folder reading
  nothing, as a missing file does. The suites build the modules with the pinned compilers
  (`python3 tools/wasm_module.py compilers`), sign them with the OTA signing
  key (a suite without it skips the wasm checks, saying why), and push them
  into the board's store: the phase-1 modules under `wasm_hello/`, which is
  not a `.moy` folder and never lists as a cart, and the fixture carts as
  `wasm_hello.moy`, `wasm_blit.moy`, `wasm_huge.moy` and `wasm_readdir.moy`.
