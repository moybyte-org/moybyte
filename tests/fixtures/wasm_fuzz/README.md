# wasm_fuzz — what fuzzing the wasm tier's engine kept

Inputs `tests/test_wasm_hostile.py` replays through `tools/wasm_fuzz.py`'s
harnesses, built from the fork's runtime at the pin (`native/moy_wasm/README.md`).
None may crash, under AddressSanitizer and UndefinedBehaviorSanitizer where the
compiler has them.

| folder | what | replayed by |
|---|---|---|
| `seeds/` | the hostile seeds (`tools/wasm_fuzz/hostile.py`): huge section counts, overlapping data segments, deep blocks, giant memory and table limits, bad LEB128 | both interpreter targets |
| `interp_fast64/`, `interp_classic32/` | each interpreter target's corpus, merged to the inputs that add coverage | its target |
| `aot_rv32/`, `aot_xtensa/` | each chip's AOT module corpus, its seeds the fixtures' modules built unsigned | its target |
| `crashes/<target>/` | every input a fuzzer crashed on, minimized, kept after its fix | its target |

`tools/wasm_fuzz.py keep TARGET CORPUS` writes a corpus folder: inputs cut to
8 KiB, the smallest first up to 384 KiB a target. A crash goes into `crashes/`
by hand, with its fix in the fork (`make vendor-wamr`) or in this tree.
