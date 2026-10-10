# moy_spine — the kernel's spine, native

The data half of the spine `docs/kernel_spine_2026-10.md` designs, in C:
`tests/spine_twin.py` is its Python oracle, call for call, and
`tests/test_moy_spine.py` pins both. It is built the three ways
`native/moy_index/` is (see its README), with no hook: every image, the
desktop MicroPython and the CPython host (`tools/moy_spine_binding.py`, which
the `runtime` package registers as `moy_spine`) run it;
`tools/moy_index_spike.py --component spine` runs its host suite and
sanitizers.

| file | what it is |
|---|---|
| `moy_htab.h`, `moy_htab.c` | the handle table: `gen << 12 \| kind << 8 \| slot`, never 0, generation-checked, lowest-first reuse. Unkinded (kind 0, 12 bits of slot) it is `native/moy_index`'s slot bookkeeping too |
| `moy_route.h`, `moy_route.c` | the app registry (a kinded `moy_htab`), the back-stack, the return records and `route()`, the WiFi lease mask |
| `moy_settings.h`, `moy_settings.c` | system.json as rows of JSON text, and the count of changes since it was last clean |
| `moy_json.h`, `moy_json.c` | the kernel's JSON scanner, one body for the settings store and the cart store (manifests, journal lines, the seed roster): spans walked in place, CPython's json.loads as the reference, and json.dumps's text for a span. `native/moy_store`'s fragment compiles it, since every image links the store |
| `moy_ledger.h`, `moy_ledger.c` | the strike ledger's slot (`runtime/crash_guard.py`) as text in, text out: the edits `CrashGuard` makes, written as `json.dumps` would write them |
| `modmoy_spine.c` | the MicroPython binding: the module `moy_spine`, registered extensible so a Python module of that name on the path wins (how a suite runs the oracle on a VM). `Settings.set` encodes a value, marks the store dirty and calls the save hook |
| `moy_spine_host.c` | the allocator over calloc, and the table calls the header has only inline, for the ctypes binding (`tools/moy_spine_binding.py`) |
| `fuzz_spine.c` | every component against a model with allocation failure injected, and the scanner on raw and corrupted text: libFuzzer or a seeded driver |

Every byte a component holds comes from the `moy_htab_mem_t` it is made with:
PSRAM under `modmoy_spine.c` on a board (`docs/native_kernel_2026-09.md` §4.6),
calloc on the host, a counting allocator under the fuzzer; the placement is
asserted in the sources. The one thing the binding keeps on the VM's heap is a
`Table`'s row objects, which the collector must see. An object frees its C
state in `__del__`.

A kind (an app id, a back-stack kind) is 1..15 bytes of any value, held as a
length and 15 bytes; the binding hands it back as an interned str, so `top()`,
`has()` and `index()` allocate nothing. Settings keys and values carry explicit
lengths, so a key may hold a NUL. `Settings.get` decodes a row afresh; null, the
booleans, small integers and strings without escapes are made in C, so a read
on the frame path does not go through the json module.

The nets are `tests/test_moy_spine.py`, `tests/test_moy_spine_twins.py` (the
suite on both desktop MicroPython builds, the VM-only properties, a random walk
against the Python twin, the scanner against CPython's json, the fuzz under the
sanitizers) and the spine trace in `tests/test_semantic_traces.py`, pinned over
the native module on both object models.
