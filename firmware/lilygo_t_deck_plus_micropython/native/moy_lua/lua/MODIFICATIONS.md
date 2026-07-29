# Moybyte modifications to Lua 5.4.7

Upstream: **Lua 5.4.7**, https://www.lua.org/ftp/lua-5.4.7.tar.gz
(the `src/` directory of that tarball). Licence: MIT — see `COPYRIGHT`.

The `.c`/`.h` files here are that tarball's `src/` **minus** the standalone
driver programs and build glue (`lua.c`, `luac.c`, `lua.hpp`, `Makefile`),
which the embedded VM does not use, **plus** the changes below. Everything
else is byte-for-byte upstream.

## 1. `#pragma GCC optimize("O2")` — 32 `.c` files

Every compiled translation unit gained this block at the top (after
`#define ..._c` / `#define LUA_CORE`, before the first include):

```c
/* Moybyte #67: the MicroPython esp32 port compiles usermod sources at -Os;
   the VM's dispatch loops want speed (S3 A/B: sakura _update ~10ms here vs the
   #6 spike's 2.69ms at -O2 on the same silicon). In-source pragma per the #77
   lesson: cmake source-file properties never reach the linked objects. */
#if defined(__GNUC__) && !defined(__clang__)
#pragma GCC optimize("O2")
#endif
```

Files: `lapi.c lauxlib.c lbaselib.c lcode.c lcorolib.c lctype.c ldblib.c
ldebug.c ldo.c ldump.c lfunc.c lgc.c linit.c liolib.c llex.c lmathlib.c
lmem.c loadlib.c lobject.c lopcodes.c loslib.c lparser.c lstate.c lstring.c
lstrlib.c ltable.c ltablib.c ltm.c lundump.c lutf8lib.c lvm.c lzio.c`

Compiler-flag change only — no change to Lua semantics.

## 2. `luaconf.h` — `LUA_32BITS` 0 → 1

```diff
-#define LUA_32BITS	0
+#define LUA_32BITS	1
```

with an explanatory comment above it. This selects Lua's supported 32-bit
number configuration (`int`/`float` instead of `long long`/`double`) because
both target FPUs are single-precision. It is an upstream-provided
configuration switch, not a code change, but it does change observable number
behaviour (device integers wrap at 2^31), so it is recorded here.

## Verifying

```bash
curl -O https://www.lua.org/ftp/lua-5.4.7.tar.gz && tar xzf lua-5.4.7.tar.gz
diff -u lua-5.4.7/src/lvm.c ./lvm.c        # only the pragma block
diff -u lua-5.4.7/src/luaconf.h ./luaconf.h # only LUA_32BITS
```
