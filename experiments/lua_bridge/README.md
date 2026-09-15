# lua_bridge — is a Lua tier worth building? (#6 / #67)

The spike that decided the Lua cart runtime, and the two parity harnesses that
outlived it.

## What is live

- `host_parity.py` — Sakura, Python original vs its line-faithful Lua port, N
  deterministic frames each, draw stream and petal state compared. Wired into
  pytest as `tests/test_lua_sakura_parity.py`.
- `brick_parity.py` — the same harness over Brick Siege
  (`tests/test_lua_brick_siege_parity.py`).

Both fake the cart API on purpose: they prove the PORT, not the engine.

## The device half

`main/main.c` is the on-silicon benchmark: sakura's exact `_update` loop (120
petals of float physics) under stock Lua 5.4 and in plain C, on the XIAO
ESP32-S3 that had measured MicroPython at ~13.5 ms/frame. It is kept as the
record of what was measured.

It does not build as it stands. The stock Lua 5.4.7 tree it compiled against
was removed from this repository (nothing built it — no make target, no CI job,
no preflight step — and the SHIPPING Lua is `native/moy_lua/lua/`, which is a
modified copy and cannot stand in: the whole point here was a stock VM). To run
it again, unpack the `lua-5.4.7` tarball's `src/` into `components/lua/` beside
an ESP-IDF component `CMakeLists.txt`; git history has the one that was here,
and `THIRD_PARTY.md` recorded it as unmodified upstream MIT.
