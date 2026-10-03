# moy_wasm_web — the browser's compiled-cart engine

The browser console's twin of `native/moy_wasm`: the session surface
moycore drives a compiled cart through (`native/moy_wasm/moy_wasm_session.h`),
implemented over the browser's own WebAssembly engine instead of WAMR. The
cart's `main.wasm` is compiled and instantiated by the page's worker as a
SIBLING of the MicroPython module, never an engine inside it, and everything
above the session is the boards': moycore's compiled-cart half, libmoy's
import table (`native/moycore/libmoy/moy_wasm.c`, built for a JavaScript
embedder under `MOY_WASM_JS`), `device/moycore_glue.WasmRun`, the Player, its
fit check and the canvas. It is moy-spec's web player's design (its
`libmoy/port/wasm` README, "Compiled carts") on this console.

| piece | where |
|---|---|
| the session, and the `moy_wasm` module | `modmoy_wasm_web.c` |
| what the binding asks of a page in C | `libmoy/embed.c`: VENDORED from moy-spec (`make vendor-libmoy`, stamped in `native/libmoy_vendor.json`) — the reaches into the cart's memory, the run of a `par` item, and the table's exports for the adapters |
| the instance and its adapters | `firmware/web_runner/worker.js`'s cart engine (`installCartEngine`): one import per row of the table, generated from the row's signature string |
| the build | `micropython.mk`, staged by `firmware/web_runner/build.sh` with the session header beside it; it defines `MOY_WASM_JS` for the whole build |

The boards never see this directory (their native scan takes directories
with a `micropython.cmake`), and the unix build names its modules.

## A cart's session here

`moy_wasm_session_open` reads `main.wasm` through the VFS as a board reads its
module, has moycore check its shape against the manifest
(`moy_wasm_check_bytes`), hands the bytes to the worker, which compiles and
instantiates them with its adapters, and has moycore bind the instance: the
binding the adapters call the table with is what `bound` names. A hook is
moycore's `ops->call` as on a board, which brackets the call with the
binding's `moy_wasm_begin`/`moy_wasm_end` and has the session run the export
(`moy_wasm_session_export`). Everything runs on the page's one thread, the
VM's: `moy_wasm_on_vm` runs its request at once, which is how the cart's
`read` reaches the store through the same `open` a board's does, and its
`write`, `erase` and `list` reach `runtime/cart_files.py` -- which keeps the
written files in the VFS and has the worker make each durable in OPFS, with
an install's crash-safety (`moy_store.mjs`'s `commitWritten`) -- and there are
no lanes, so a cart's `par` items run in order through the binding, each with
its own stack pointer (SPEC.md §16.10's one-core host).

The adapters copy a `*~` span in and out of the VM's memory around the call,
bounds-checked against the cart's; a pointer the table carries as a plain
`i32` is the binding's to reach through `Module.moyCart`. A trap the binding
raises is thrown back through the cart as an exception, and whatever the cart
throws (an `unreachable`, an access outside its memory) is the trap's
message on the console's error panel. Only an `Error` is the cart's: anything
else passing through a hook is the VM's own unwinding, and is rethrown.

A frame reaches the canvas as every non-board frame does: `blit` and
`blit565` write moycore's canvas, which the worker ships to the page. A
cart's `snd` goes to `moy_audio`'s stream (`moy_audio_snd.h`), which the web
runner's per-frame `render()` mixes after the synth, so the samples ride the
same finished PCM the page plays through its AudioWorklet.

## What does not apply here

A board's compiled module is NATIVE code, trusted by provenance: a key naming
its chip and format, a signature, and the owner's Unknown sources switch for
an unsigned one. The browser never loads one. `main.wasm` is validated by the
browser and runs bounds-checked in its own memory, the sandbox SPEC.md §16
assumes, so it needs no key, no signature and no switch: `CHIP` and `FORMAT`
are None, Get Carts installs no module (`cart_index.plan` with no chip), and
WasmRun opens `main.wasm` itself and is never "slow" for it — the slow-play
notice is about a board missing ITS module. The Unknown sources row is on
every tier's Settings and has nothing to govern here.

The fit check is the browser's own: a cart's load takes its declared memory,
one block of its own (the module's `WebAssembly.Memory`), and its module
file, as moy-spec's web player sizes it; what the browser can give is the
largest memory it allocates for one module, found once by allocating it
(`mem()`). A load whose memory the browser still will not give reads
"out of memory", which the Player turns into the same notice.
