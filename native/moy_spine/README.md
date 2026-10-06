# moy_spine — the kernel's spine, native

The data half of the spine `docs/kernel_spine_2026-10.md` designs, in C:
`runtime/moy_spine.py` defines the interface and is the reference, call for call,
and `tests/test_moy_spine.py` pins it for every binding. The Python twin stays
what every image freezes; a build takes the C twin only when `MOY_SPINE_IMPL=c`
says so, and `tools/moy_index_spike.py`'s header (`--component spine`) is the
authority on that hook and the harness's commands.

| file | what it is |
|---|---|
| `moy_htab.h`, `moy_htab.c` | the handle table: `gen << 12 \| kind << 8 \| slot`, never 0, generation-checked, lowest-first reuse. Unkinded (kind 0, 12 bits of slot) it is `native/moy_index`'s slot bookkeeping too |
| `moy_route.h`, `moy_route.c` | the app registry (a kinded `moy_htab`), the back-stack, the return records and `route()`, the WiFi lease mask |
| `moy_settings.h`, `moy_settings.c` | system.json as rows of JSON text, and the scanner that reads it |
| `modmoy_spine.c` | the MicroPython binding: the module `moy_spine`, registered extensible so a `moy_spine.py` on the path wins |
| `moy_spine_host.c` | the allocator over calloc, and the table calls the header has only inline, for the ctypes binding (`tools/moy_spine_binding.py`) |
| `fuzz_spine.c` | the API-sequence fuzz against models, and the settings scanner on raw and corrupted text: libFuzzer or a seeded driver, under ASan and UBSan |
| `micropython.cmake`, `micropython.mk` | the boards', and the desktop's and browser's, builds of the hook |

Every byte a component holds comes from the `moy_htab_mem_t` it is made with:
PSRAM under `modmoy_spine.c` on a board (`docs/native_kernel_2026-09.md` §4.6),
calloc on the host, a failure-injecting counter under the fuzzer, and
`tests/test_moy_spine_twins.py` asserts the placement in the sources. The one
thing the binding keeps on the VM's heap is a `Table`'s row objects, which the
collector must see. An object frees its C state in `__del__`.

A kind (an app id, a back-stack kind) is 1..15 bytes of any value, held as a
length and 15 bytes; the binding hands it back as an interned str, so `top()`,
`has()` and `index()` allocate nothing. Settings keys and values carry explicit
lengths, so a key may hold a NUL.

The nets: `tests/test_moy_spine.py` (every binding: the Python twin and the C
twin through ctypes), `tests/test_moy_spine_twins.py` (that suite on the desktop
MicroPython's both object models, the frame path's allocations and the
finalisers on that VM, a random walk against the Python twin, the scanner
against CPython's json, the fuzz under the sanitizers, the PSRAM rule) and the
spine trace in `tests/test_semantic_traces.py`, pinned over the native module on
both object models.
