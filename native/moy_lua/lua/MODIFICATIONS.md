# Moybyte modifications to Lua 5.4.7

Upstream: **Lua 5.4.7**, https://www.lua.org/ftp/lua-5.4.7.tar.gz
(the `src/` directory of that tarball). Licence: MIT — see `COPYRIGHT`.

The `.c`/`.h` files here are that tarball's `src/` **minus** the standalone
driver programs and build glue (`lua.c`, `luac.c`, `lua.hpp`, `Makefile`),
which the embedded VM does not use, **plus** the changes below. Everything
else is byte-for-byte upstream.

## 0. `lobject.c`: an integral float prints without ".0"

`tostringbuff` no longer appends `.0` to a float that reads as an integer, so
`tostring(3.0)`, `3.0 .. ""` and `print(6/2)` all give `3` (moy-spec SPEC.md
4.2). The same edit is in moy-spec's `libmoy/vendor/lua`.

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

## 3. `lobject.c`: integers convert to decimal without `snprintf`

`tostringbuff` no longer calls `lua_integer2str` for an integer, and no longer
calls `lua_number2str` for an integral float that item 0 already prints as bare
digits. Both go through a hand-rolled `moy_int2str` instead (#66).

Why: on the S3 one `snprintf` costs 3–4 µs, and a PICO-8 port formats a number
per tile per frame — `tostr()`, and every table keyed by `x..","..y` — against
a 33 ms frame.

The output is byte-for-byte what `"%d"` produced. The digits come off the
**unsigned** negation, so `LUA_MININTEGER` converts without overflowing, and
the float side takes the fast path only for a non-zero integral value with
|x| < 1e7 — which is exactly where `%.7g` stops printing plain digits (style
`f` holds while the decimal exponent is below the precision), and below the
float32 exact-integer limit. Zero is left to `snprintf` on purpose: `-0.0`
prints `-0`, and a cast to an integer would lose the sign.

`string.format("%d", …)` is untouched and still goes through `snprintf`, which
is what lets a cart hold one against the other
(`tests/test_moycore_pool.py::test_integers_format_exactly_as_snprintf_did`).
Because the output is identical, a Lua that does not carry this patch — moy-spec's
runner copy does not — still agrees with a board that does.

## 4. `luaconf.h`, `lvm.c`, `ltable.c`, `ldo.c`: the VM's hot loop lives in IRAM on the Xtensa boards

`MOY_HOT` (luaconf.h) places `luaV_execute`, `luaV_finishOp`, the four
`luaH_get*` lookups and `luaD_precall`/`luaD_poscall` in `.iram1.moylua` when
`__XTENSA__` is defined, and is empty elsewhere. On the ESP32-S3 flash and
PSRAM share one MSPI bus, so an instruction-cache miss in the interpreter loop
waits behind the cart's own data; it buys about a tenth of a cart tick for
~11 KB of IRAM (#66). `ESP_PLATFORM` is not the guard because MicroPython
compiles usermods without it; `__XTENSA__` is the compiler's own. The P4
(RISC-V) is untouched.

## Verifying

```bash
curl -O https://www.lua.org/ftp/lua-5.4.7.tar.gz && tar xzf lua-5.4.7.tar.gz
diff -u lua-5.4.7/src/lvm.c ./lvm.c        # the pragma block, plus item 4
diff -u lua-5.4.7/src/luaconf.h ./luaconf.h # LUA_32BITS, plus item 4
diff -u lua-5.4.7/src/lobject.c ./lobject.c # the pragma, plus items 0 and 3
```
