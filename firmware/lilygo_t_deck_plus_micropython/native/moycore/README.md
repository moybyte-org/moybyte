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

## Superset verbs are not bound here

`make_layer`/`draw_layer`/`image`, scenes, tables, texts and `view()` are
moybyte's, not the spec's. The cart census that decided to leave them
Python-side is in the plan: one Lua cart in the tree uses layers, at one blit
per frame rather than one per sprite, so a second console in C would trade the
duplication this module deletes for a smaller one.

## Testing

`tests/test_moycore_loop.py` and `tests/test_semantic_traces.py` drive this
module through a real MicroPython VM on the desktop. That binary is a `make`
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
