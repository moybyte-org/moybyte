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

`tests/test_moycore_loop.py`, against the unix dual-usermod build:

    cd firmware/lilygo_t_deck_plus_micropython/.build/usermods_luadraw
    ln -sfn ../../native/moycore moycore        # beside moy_gfx and moy_lua
    cd ../lvgl_micropython/lib/micropython/ports/unix
    make VARIANT=standard BUILD=build-moycore USER_C_MODULES=<abs usermods_luadraw>
