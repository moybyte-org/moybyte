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
| 1–256 bytes | the **small-object pool**: eight size classes (16/32/48/64/96/128/192/256), each carved out of 8KB PSRAM chunks that serve one class apiece |
| anything larger | `heap_caps_realloc`, **internal SRAM first** down to the `set_sram_floor` headroom, PSRAM when the floor says no |

The pool exists because on the S3 the IDF allocator costs about **9 µs per
malloc and 2 µs per free** with its control structures in PSRAM — a Lua `{}` is
9.7 µs, a two-field constructor 21 µs — and a PICO-8 port builds a table per
vector operation, every tick, against a 33 ms frame.

Four things about it are load-bearing:

- **No per-block header.** Lua's realloc contract hands back the true old size
  whenever `ptr` is non-NULL, so a block's class is a function of `osize`
  alone. Which makes one invariant absolute: **a request of 1–256 bytes is
  always a pool block and never anything else**, because the free path has
  nothing but the size to decide with. Pool exhaustion therefore returns NULL
  and lets Lua run its emergency GC and retry, rather than falling back to the
  heap — one heap pointer on a free list would be handed out later as a pool
  block and leaked past close.
- **One class per chunk, which is what lets a chunk go back.** A chunk is
  aligned to its own size, so `p & ~(chunk - 1)` is the chunk a block came from
  in a single AND; the header there holds that class's free list, its bump
  cursor and a **live count**, so the free that empties a chunk is the free
  that returns it to the heap — **O(1) worst case**, with no free list to walk,
  because every entry that could point into the chunk *is* the chunk's own
  list. Shared chunks cannot do it at any price: one survivor pins the whole
  chunk, and a cart's parse burst can leave most of a megabyte held for a third
  of it live, on a board with kilobytes of free PSRAM. The cost is a floor — a
  class touched at all holds a chunk, so eight classes hold eight — which is
  why a chunk is **8KB**: 64KB of floor, and 512 blocks of the commonest class
  have to die together to give one back.
- **One chunk size per run, and one empty chunk kept back.** The halving retry
  (to 4KB) still runs, but only for the *first* chunk of a run: the mask is
  what makes the lookup a single AND, and a second size would need a second
  mask. A later chunk that cannot be had returns NULL, and Lua's emergency GC
  now genuinely frees chunks before the retry. The one empty chunk the pool
  keeps — retyped to whatever class asks next — is what stops a loop that
  allocates and frees one block of an otherwise-full class from paying a malloc
  *and* a free every iteration.
- **Blocks live in PSRAM; the per-class current chunk is a static, i.e.
  internal SRAM.** The free-list heads moved into the chunk header when chunks
  became per-class, so alloc and free each write one PSRAM word more than a
  static free list would; the header's hot fields are its first 32 bytes on
  purpose, one cache line per active chunk. The blocks stay out of SRAM because
  giving the VM more internal SRAM measured *slower* — the rest of the board
  starves for it. The large path's SRAM-first
  policy is untouched, and that is where the structures the policy was written
  for actually live — the Lua stack is one array well over a kilobyte, and so
  is a big table's node array.

**Every chunk goes back at `close()`** as well, after `lua_close` and never
before: nothing survives a run, and a cart that churned its way to a hundred
chunks must not hold them while the launcher is up. `alloc_stats()`'s seven
fields read all-zero afterwards except the peak.

`alloc_stats()` → `(sram_live, psram_live, peak, sram_denied, pool_live,
pool_cap, pool_chunks)`. The first four count the bytes **Lua asked for**, pool
blocks included and charged to PSRAM. The last three are the pool itself:
`pool_live` is the *class* bytes of the live blocks, `pool_cap` the usable
bytes across every chunk (the kept-back spare included, because it is memory
the pool holds), and the difference is slack — free lists, un-carved tails, and
the remainder each chunk's class size leaves. So the PSRAM the VM actually
holds is `(psram_live - pool_live) + pool_cap`. **`pool_cap` falls when chunks
go back**, and that is the number to watch — `tests/test_moycore_pool.py`'s
burst scenario is where that fall is asserted.

`sram_report()` → `(sram_free_min, psram_fallback, floor)`, or **`None`** on a
tier whose allocator has one region to choose from (the host, the wasm head).
This is the SAME switch above, reported per RUN rather than per session:
`run_begin` resets both meters and `close()` does not, so the console reads them
at the exit boundary, after the VM is gone (`Player.release_world`, #211).
`psram_fallback` is the field that matters — a **boolean about a regime
change**, because a cart that outgrows the floor does not fail and does not
warn, it starts allocating from PSRAM and runs about twice as slow. Both are
read where the large path already knows the free figure, so the accounting adds
one compare and no syscall; the consequence is that the low-water mark is
sampled at the VM's large allocations, which is where the floor decision is
actually made, and not between them. `sram_free_min` is `None` until a run
reaches that test at all.

## The per-verb profiler (`profile` / `verb_stats` / `verb_reset`)

A Lua/p8 cart draws through libmoy's C verbs straight into the framebuffer — for
a p8 cart literally so, since `moy_p8.c` makes the canvas the screen region — so
**every meter `DeviceCanvas` owns reads zero on this tier**. `DRAW2` says
`layer=0 batch=0 map=0 text=0 fill=0` and `BATCH` says 0 sprites while the frame
spends 40 ms somewhere. That is why every pass that asked where a slow port's
render went ended at "the cart's own code": there was no instrument that could
say otherwise.

These three open it. `profile(1)` replaces every C-function global with a
closure that times the original; `verb_stats()` returns `(hz, frames, ((name,
calls, self, inclusive), ...))` and `verb_reset()` zeroes it. The serial face is
`verbs on|off|reset` and a bare `verbs`, which prints one `VERBS` line of
**per-frame** calls and milliseconds (`runtime/dev_channel.verbs_line`).

Four things about it are load-bearing:

- **It gates at INSTALL, not per call.** Disarmed, a cart's globals ARE the
  vendored C functions — there is no wrapper in the hot path, so there is no
  gate to test in it and nothing to measure. An `if (prof)` inside a verb
  reached three thousand times a frame is a tax the shipping frame would pay
  forever to answer a question asked twice a year. It is also its own switch
  rather than a rider on `diag`, for the same reason: a measurement session
  arms it, an ordinary diag session does not.
- **The clock is the CPU cycle counter** (`esp_cpu_get_cycle_count`, one
  instruction), not `mp_hal_ticks_us`. That clock is `esp_timer_get_time` on
  both S3 boards and costs about what the verbs being measured cost — at three
  thousand calls a frame it would not perturb the measurement so much as become
  it. The rate is MEASURED at install against the millisecond clock and handed
  back as `hz`, because the boards do not share one frequency and the host has
  no cycle counter at all.
- **Self and inclusive are both kept.** `foreach` is why: five calls a frame and
  ten milliseconds on moss moss, every bit of it the Lua function foreach was
  handed. Charged inclusively it reads as the slowest thing in the cart and
  aims a fix at the wrong file. A verb is charged what it spent minus what its
  callees spent, and non-verb Lua lands on the nearest verb enclosing it — so
  `t` is C cost for a leaf verb, and for one carrying an `in` it is C cost plus
  the Lua underneath.
- **The wrapper carries the original's upvalue and calls it DIRECTLY.** libmoy's
  p8 verbs keep the machine pointer in upvalue 1 and `register()`'s trampolines
  keep their index there, so the wrapper's upvalue 1 is the original's and
  `fn(L)` — never `lua_call` — leaves `lua_upvalueindex(1)` resolving correctly
  with no extra Lua frame. A closure with two upvalues would be mis-wrapped, so
  one is skipped rather than guessed at.

Install happens at `load()` when armed, before the chunk runs, because a cart
captures its globals as it loads — the p8 shim RESOLVES its verbs there
(`spr = __moy_p8_spr or spr`), so the name a cart calls is not the name libmoy
registered. Arming and then launching is the reading that misses nothing;
arming into a running cart still catches every verb it calls by global name.

## The per-FUNCTION Lua profiler (`lua_profile` / `lua_stats` / `lua_reset`)

The per-verb profiler above closes half the box: it says how much of a p8
frame is C. The rest is "the interpreter", and on a ported cart that is not one
body of Lua but **two** — the cart's own code, and the 1,348 lines defining 128
functions that `tools/p8_lua_port.py` emits into every cart it converts. Which
half the time is in decides whether there is anything to fix: the shim is
GENERATED, so a fix there lands on every ported cart at once.

`lua_profile(on, interval, shim_lo, shim_hi)` sets a Lua count+call hook;
`lua_stats(top)` returns `(hz, frames, interval, totals, rows, srcs)` and
`lua_reset()` zeroes it. The serial face is `luaprof on|off|reset [interval]`
and a bare `luaprof`, which prints one `LUAPROF` line
(`runtime/dev_channel.luaprof_line`).

Five things about it are load-bearing:

- **It SAMPLES, and that is the whole design.** A call/return profiler pays its
  overhead per CALL, so it inflates exactly the functions that are small and
  called often — which is what the shim is made of, and what the question is
  about. It would find the shim expensive whether or not it is. A count hook
  fires every N VM instructions whoever is running, so what it weighs is
  instructions executed. `tests/test_moycore_loop.py` pins this with a fixture
  built to a KNOWN 3:1 split across a fake shim boundary, where a
  call-weighted profiler would answer 50%.
- **It perturbs, and the honest claim is narrower than "it doesn't".** Lua 5.4
  gates hooks per CallInfo through `trap`, so an unarmed VM pays nothing — but
  with `LUA_MASKCOUNT` set, EVERY instruction detours through `luaG_traceexec`.
  That tax is per-instruction and does not fall as the interval rises; only the
  per-sample work does, which is why the frame rate is the same at interval 256
  and 4096. What can be claimed is that the tax is the same for every Lua
  function and therefore CANCELS OUT OF A SHARE — and that is testable, not
  asserted: run one cart at two rates and the shares agree if it holds. Read
  the sample share, not the wall-clock one.
- **It cannot see the COLLECTOR**, and that is the one gap worth knowing.
  Collection runs inside the allocator at a `checkGC` point, not as counted VM
  instructions, so it generates no samples however long it takes. It lands in
  the wall-clock column, charged to whoever tripped it. A row whose `t` share
  badly exceeds its sample share is ALLOCATING, not computing. `lua_gc_mode`
  below is the lever for that, and the two shipped together.
- **The shim's line range is read from the cart that is loaded**, never baked
  in. The emitted block is a fixed 1,348 lines but it starts wherever that
  cart's data tables ended — line 26 in one port, line 163 in one that needs
  the raw sheet. `dev_channel.shim_line_range` finds the generator's two marker
  comments by streaming the file in blocks with a carry, because the board
  being asked has that same cart resident and a reader that pulled it into
  `splitlines()` would OOM the cart it was about to measure.
  **From `p8.lua`** on a cart the current importer wrote: a port is two scripts
  now (SPEC.md 4) and the shim is that one, so those are the line numbers the
  VM reports. `dev_channel.cart_shim_range` picks the file; a single-file port
  still answers from `main.lua`.
- **The range is CHECKED against the cart, not believed.** The shim owns
  `_draw` (the porter renames a p8 cart's own to `p8_draw`), so its
  `linedefined` must land inside the range the host passed. If it does not, the
  two are looking at different files and the pin is REFUSED: nothing is charged
  as shim and `lua_stats` says `pinned` is false, rather than reporting a
  confident split of the wrong cart. Pinning also keeps the prelude chunk
  (`moycore.exec`, whose lines also start at 1) out of the shim's bucket.

Disarmed there is no hook, no table and no allocation. Rows are identified by
`source` + `linedefined` rather than by name, because most of these functions
are local or anonymous and a name would be a guess; C functions are counted in
`c_calls` but given no row, since every one of them reports `"=[C]"` and would
collide into a single meaningless line.

## The cart VM's collector (`lua_gc_mode`)

`lua_gc_mode(mode, a, b, c)` reads and sets the Lua heap's collector — stop,
restart, incremental with its pause/stepmul/stepsize, or **generational** — and
returns `(heap_kb, running, generational)`. Serial: `luagc [stop|run|inc [pause
step size]|gen [minor major]]`.

It is a knob here rather than upstream because moycore OPENS the VM: libmoy and
the p8 shim are both vendored, but the collector's schedule is this file's. It
ARMS as well as sets, so it survives the relaunch every A/B tool performs, and
an armed mode is applied AFTER `load()`'s settling collect so a `stop` never
applies to the parse burst — the run's high-water mark, and the one thing that
must still be collected.

**Stopping it is the direct measurement of what it costs**, which is why the
verb exists at all: the difference between a window with the collector running
and one with it stopped is collection, on the live cart with nothing else
changed. That measurement is worth taking before tuning, because a big heap is
not the same thing as a busy collector — an incremental collector's cost tracks
the ALLOCATION RATE, and a cart can hold a megabyte of long-lived data and give
its collector almost nothing to do.

Two verbs serve the pool and nothing else:

- **`gc()`** → the VM's heap in KB after a full, stop-the-world collect. A
  diagnostic and a test verb, not a frame-loop one — but `load()` runs the same
  collect itself once `_init` returns, because the parse burst is the run's
  high-water mark and Lua's incremental collector would otherwise walk it out
  one step at a time while the frame loop needed the memory. It exists as a
  verb because SPEC.md 4.1 takes `collectgarbage` away from a cart, so nothing
  else can ask the VM to settle.
- **`pool_check()`** → `0`, or a negative code. The invariant the design rests
  on — a block masks back to the chunk it came from — is invisible from Python
  and does not fault when it breaks; it decrements a *neighbour's* live count
  and frees a chunk that is still in use. So the checker walks every chunk,
  every free list and both sets of links: `-1` no chunk size, `-2` a chunk off
  its own alignment, `-3` the global list's back-link, `-4` a class out of
  range, `-5` a live or linked spare, `-6` a free block that masks to another
  chunk, `-7` one off its class's stride, `-8` a free list longer than the
  chunk was carved (a cycle, or a double free), `-9` live + free ≠ carved,
  `-10` room-list membership disagreeing with whether the chunk has room,
  `-11` a spare that kept its class, `-12` the chunk count, `-13` `pool_cap`,
  `-14` `pool_live`, `-15` a room list holding another class's chunk.

`-DMOYCORE_POOL=0` compiles the pool out, which is the A/B: the P4 has abundant
internal SRAM and a different allocator profile, and per-board verdicts do not
transfer.

## Superset verbs are not bound here

`make_layer`/`draw_layer`/`image`, scenes and `view()` are
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

One verb exists only there: **`sram_sim(bytes)`** arms a simulated internal-SRAM
region on a build with no `esp_heap_caps.h`, supplying the free figure a board
reads from its heap. It is how the floor arithmetic and `sram_report`'s fallback
flag are driven where the tests run, and it is compiled out of every firmware
build — like `pool_check`, a test verb rather than a knob.
