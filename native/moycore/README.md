# moycore — the cart's whole frame in C (moycore plan stage 2)

`run_begin()` builds a libmoy console over buffers the console already owns;
`tick(dt)` runs the cart's `_update` and `_draw` end to end. One upcall per
frame instead of hundreds.

**The engine is not written here.** `libmoy/moy_lua.c` is moy-spec's own Lua
binding — all 38 SPEC.md verbs as C functions against a `moy_console` — and
`moy.h` exports `moy_lua_open`/`init`/`update`/`draw`. This module is the HOST
half: what a *moybyte* console is made of.

| piece | how |
|---|---|
| canvas | the DeviceCanvas framebuffer itself (libmoy takes a caller-owned `pix` and a caller-supplied wire table, so the panel's byte order stays out of the cart contract) |
| input, time, pointer | a SNAPSHOT array the frame loop refreshes before the tick — `btn()` sixty times a frame costs zero crossings |
| audio | a command QUEUE the host drains after the tick, order preserved |
| pmem | a C array with a dirty flag, the shape the device already defers it to (#66) |
| tile flags | 512 bytes COPIED in at `run_begin` (SPEC.md 3.5) -- the one buffer here that is not the caller's, because C writes it (`fset`, a poke to `0x3000`, the p8 shim's `__moy_map_flags`) and the caller may hand over a plain `bytes` |

## What it does NOT compile

Neither the raster nor a Lua VM: the binary already has one of each
(`moy_gfx`'s vendored libmoy, `moy_lua`'s vendored Lua 5.4). A second copy of
either would be a duplicate-symbol link error, so the include path points at
the siblings — the same shape `moy_lua` already uses to reach `moy_gfx`'s C
API. **moycore therefore requires both siblings in the build.**

`libmoy_binding.c` exists because the pragma silencing the two diagnostics a
32-bit Lua necessarily trips cannot live in the vendored file (editing it is a
red test) and cannot live in the build fragment either (the ports append their
own `-Wall` after `CFLAGS_USERMOD`).

## The VM's allocator

`l_alloc` is the cart's Lua heap, and it is the **system** heap rather than
MicroPython's gc heap, so cart churn can never lengthen a shell collect. Two
halves, one switch each:

| request | where it comes from |
|---|---|
| 1–256 bytes | the **small-object pool**: eight size classes (16/32/48/64/96/128/192/256), a singly linked free list per class, blocks carved from 32KB PSRAM chunks |
| anything larger | `heap_caps_realloc`, **internal SRAM first** down to the `set_sram_floor` headroom, PSRAM when the floor says no |

The pool exists because on the S3 the IDF allocator costs about **9 µs per
malloc and 2 µs per free** with its control structures in PSRAM — a Lua `{}` is
9.7 µs, a two-field constructor 21 µs — and a PICO-8 port builds a table per
vector operation, every tick, against a 33 ms frame.

Three things about it are load-bearing:

- **No per-block header.** Lua's realloc contract hands back the true old size
  whenever `ptr` is non-NULL, so a block's class is a function of `osize`
  alone. Which makes one invariant absolute: **a request of 1–256 bytes is
  always a pool block and never anything else**, because the free path has
  nothing but the size to decide with. Pool exhaustion therefore returns NULL
  and lets Lua run its emergency GC and retry, rather than falling back to the
  heap — one heap pointer on a free list would be handed out later as a pool
  block and leaked past close.
- **Blocks live in PSRAM; the free-list heads are a static, i.e. internal
  SRAM.** The heads are touched on every alloc and free; the blocks are the
  VM's, and the 2026-09-02 measurement is that giving the VM more internal SRAM
  made the tick *slower*, because the rest of the board starves for it. The
  large path's SRAM-first policy is untouched, and that is where the structures
  the policy was written for actually live — the Lua stack is one array well
  over a kilobyte, and so is a big table's node array.
- **Every chunk goes back at `close()`.** Nothing survives a run; a cart that
  churned its way to a dozen chunks must not hold them while the launcher is
  up. `alloc_stats()`'s seven fields read all-zero afterwards except the peak,
  and `tests/test_moycore_pool.py` is the guard.

`alloc_stats()` → `(sram_live, psram_live, peak, sram_denied, pool_live,
pool_cap, pool_chunks)`. The first four count the bytes **Lua asked for**, pool
blocks included and charged to PSRAM. The last three are the pool itself:
`pool_live` is the *class* bytes of the live blocks, `pool_cap` the usable
bytes across every chunk, and the difference is slack — free lists, the
un-carved tail, and the ≤255 bytes each earlier chunk abandoned. So the PSRAM
the VM actually holds is `(psram_live - pool_live) + pool_cap`.

`-DMOYCORE_POOL=0` compiles the pool out, which is the A/B: the P4 has abundant
internal SRAM and a different allocator profile, and per-board verdicts do not
transfer.

## Superset verbs are not bound here

`make_layer`/`draw_layer`/`image`, scenes, tables, texts and `view()` are
moybyte's, not the spec's. The cart census that decided to leave them
Python-side is in the plan: one Lua cart in the tree uses layers, at one blit
per frame rather than one per sprite, so a second console in C would trade the
duplication this module deletes for a smaller one.

## Testing

`tests/test_moycore_loop.py`, `tests/test_moycore_pool.py` and
`tests/test_semantic_traces.py` drive this module through a real MicroPython VM
on the desktop. That binary is a `make`
target now:

    make unix-micropython

It clones a pinned micropython, symlinks every native module that ships a
Makefile fragment (`moy_gfx`, `moy_lua`, `moycore`, `moy_audio`) into one
usermods tree, and builds `ports/unix` — about fifteen seconds cold, under a
second warm, into `.build/unix_micropython/…/build-moybyte/micropython`. CI
runs it on every push.

The target exists because the recipe used to live HERE, as prose, and prose is
not a build: the compiled-vs-compiled parity check
(`tests/test_gfx_binding.py::test_matches_the_native_moy_gfx`, the only place
two independently compiled rasters are compared) pointed at a hand-built
artifact nothing produced, so it passed on one machine and silently skipped
everywhere else. Tests that need the binary say so loudly when it is absent
rather than vanishing from the run.

`MOYBYTE_MICROPYTHON=/path/to/micropython` points them at a different build.
