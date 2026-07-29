# Moybyte modifications to Lua 5.4.7

Upstream: **Lua 5.4.7**, https://www.lua.org/ftp/lua-5.4.7.tar.gz
(the `src/` directory of that tarball). Licence: MIT — see `COPYRIGHT`.

**The Lua sources in this directory are UNMODIFIED** — every `.c` and `.h`
file is byte-for-byte identical to `lua-5.4.7/src/`.

Two packaging-only differences:

- The standalone driver programs and build glue (`lua.c`, `luac.c`,
  `lua.hpp`, `Makefile`) are omitted; the embedded VM does not use them.
- `CMakeLists.txt` is **added** by Moybyte — an ESP-IDF component wrapper
  (`idf_component_register` + `-O2`, `LUA_32BITS=0`). It is Moybyte's own
  file, not upstream Lua's, and is covered by this repository's licence.

This is the `experiments/lua_bridge` measurement spike (issue #6/#67), kept
deliberately on stock Lua so it measures a *stock* VM. The shipped, modified
copy lives in
`firmware/lilygo_t_deck_plus_micropython/native/moy_lua/lua/`.

## Verifying

```bash
curl -O https://www.lua.org/ftp/lua-5.4.7.tar.gz && tar xzf lua-5.4.7.tar.gz
for f in *.c *.h; do diff -q "lua-5.4.7/src/$f" "$f"; done   # silent = identical
```
