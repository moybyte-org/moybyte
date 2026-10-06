# moy_index — the store's index, native

The handle table `runtime/moy_index.py` defines, as a C ABI (`moy_index.h`) and
its C twin (`moy_index.c`): sprint 1a's language spike
(`docs/native_kernel_2026-09.md` §5, #224). The Python index stays what every
image freezes; a build takes a twin only when `MOY_INDEX_IMPL` names one, and
`tools/moy_index_spike.py`'s header is the authority on that hook, the Rust
twin's build script and the harness's commands. The spike's numbers are #224's.

| file | what it is |
|---|---|
| `moy_index.h` | the ABI: every twin implements it, everything above calls only it |
| `moy_index.c` | the C twin |
| `modmoy_index.c` | the MicroPython binding both twins share: the module `moy_index`, registered extensible so a `moy_index.py` on the path wins |
| `bench_moy_index.c` | `moy_index_bench`, the hot path driven from C; only under `MOY_INDEX_BENCH=1` |
| `moy_index_host.c` | the two host imports over malloc, for the ctypes binding |
| `fuzz_index.c` | the API-sequence fuzz against a model: libFuzzer or a seeded driver, under ASan and UBSan |
| `rust/` | the Rust twin: a `no_std` crate exporting `moy_index.h` (`src/lib.rs`), its tests for `cargo test` and Miri, a cargo-fuzz target (`fuzz/`), and `build.sh`, which builds the static library for each target and names the toolchain pins (`tools/rust_tree.sh` installs them) |
| `micropython.cmake`, `micropython.mk` | the boards', and the desktop's and browser's, builds of the hook |

The nets: `tests/test_moy_index.py` (every binding: the C twin through ctypes,
and the Rust twin under `MOY_INDEX_IMPL=rust`), `tests/test_moy_index_twins.py`
(that suite on the desktop MicroPython's both object models, a random walk
against the Python twin, the fuzz under the sanitizers), the store trace in
`tests/test_semantic_traces.py`, pinned over the native index too, and
`tools/link_providers.py`'s guard on every image's link map: no Rust object
provides a C library name.
