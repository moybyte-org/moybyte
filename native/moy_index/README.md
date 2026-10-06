# moy_index — the store's index, native

The handle table `runtime/moy_index.py` defines, as a C ABI (`moy_index.h`) and
its C twin (`moy_index.c`): sprint 1a's component, and since sprint 1b's slice 1
(`docs/kernel_store_2026-10.md` §4, §10) the index every image runs -- the
boards and the browser freeze no `moy_index.py`. Its rows and its root table
(`moy_index_root`: up to eight store roots, a key being the root's id byte and
the cart's folder) are PSRAM on a board. `runtime/moy_index.py` stays the host's:
CPython's simulator, tools and tests import it, and it is the reference every
binding is pinned against. `MOY_INDEX_IMPL=py` builds an image with the Python
index instead; `tools/moy_index_spike.py`'s header is the authority on that hook
and the harness's commands. The numbers are #224's.

| file | what it is |
|---|---|
| `moy_index.h` | the ABI: the twin implements it, everything above calls only it |
| `moy_index.c` | the C twin |
| (`../moy_spine/moy_htab.h`) | the handle table `moy_index.c` takes its slots from: generations, lowest-first reuse and the check on every use are the kernel's one implementation, here unkinded with 12 bits of slot; the index adds the path intern on top |
| `modmoy_index.c` | the MicroPython binding: the module `moy_index`, registered extensible so a `moy_index.py` on the path wins; its allocator is PSRAM on a board, and `mem()` is what the census reads |
| `bench_moy_index.c` | `moy_index_bench`, the hot path driven from C; only under `MOY_INDEX_BENCH=1` |
| `moy_index_host.c` | the two host imports over malloc, for the ctypes binding |
| `fuzz_index.c` | the API-sequence fuzz against a model: libFuzzer or a seeded driver, under ASan and UBSan |
| `micropython.cmake`, `micropython.mk` | the boards', and the desktop's and browser's, builds of the hook |

The nets: `tests/test_moy_index.py` (every binding: the Python index and the C
twin through ctypes), `tests/test_store_roots.py` (the root table, over every
binding and on the VM), `tests/test_moy_index_twins.py` (that suite on the desktop
MicroPython's both object models, a random walk against the Python twin, the
fuzz under the sanitizers) and the store trace in
`tests/test_semantic_traces.py`, pinned over the native index too.
