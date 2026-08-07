# libmoy (raster) — vendored, verbatim

`moy.h` / `moy_pixel.h` / `moy_canvas.c` / `moy_sprite.c` / `moy_data.c` are
**copied unmodified** from [moy-spec](https://github.com/moybyte-org/moy-spec)'s
`libmoy/` — the moy console as a C library. MIT, same as this directory's
`LICENSE`.

**Which commit, and whether this copy still matches it, is recorded in
[`../../libmoy_vendor.json`](../../libmoy_vendor.json)** — written by
`make vendor-libmoy` and checked by `tests/test_libmoy_vendor.py`. That file is
the authority; this page is the explanation.

Compiled with **`MOY_PIXEL_RGB565`** (set in `../micropython.cmake`, once, for
the whole module). SPEC.md §1.1 leaves the canvas format to the host, and this
console's is 16-bit words rather than palette indices — measured and settled,
see CLAUDE.md's graphics section.

**And at `-O3`, via `../libmoy_kernels.c`.** That shim exists because the ports
build usermods at `-O2` and `modmoy_gfx.c` carries an in-source
`#pragma GCC optimize("O3")` that only covers its own file — so when six verbs
became calls into libmoy they silently dropped an optimisation level. Measured
on a P4 (2026-08-07): **`map` 562.5 → 453.1 µs/op, `sprb` 335.9 → 296.9**. The
pragma cannot go in the vendored copies (that is a red test), so the shim
`#include`s them instead and they stay byte-identical.

## Which verbs cross, and which do not

`modmoy_gfx.c` is not a libmoy port. It is moybyte's compositor kernel, and only
the verbs whose GEOMETRY the spec defines route through libmoy:

| | |
|---|---|
| **libmoy's** | `tri`, `sspr`, `tline`, `circ`, `circb`, `line`, `print`, `blit_map`, `blit_batch` |
| **no counterpart** | `fill`, `fill_rect`, `blit565_scale`, `copy_async`, `copy_wait`, `scroll_rect`, `blit_window`, `blit_indices`, `fill_spans`, `draw_ctx` |

The third row is moybyte's compositor, not the spec's raster: async DMA, the
scroll blit, the window composite, the draw-context gate. `fill`/`fill_rect`
belong there too despite `moy_cls`/`moy_rect` existing, because they are
**viewport-aware** — a windowed layer clearing itself must not wipe the desktop
it draws on, and libmoy's canvas has no such notion.

The second row was declined on measurements that turned out to be STALE, and
re-running them on 2026-08-07 overturned two of the three. Kept here in full,
because a decision reversed quietly teaches nobody anything.

The S3 column originally came from a bench run whose libmoy predated that same
session's optimizations — `moy_print`'s whole-column off-clip early-out and
`moy_spr`'s scale-1 fast path, which between them are most of what those verbs
do. Re-measured against current libmoy on both boards (`LIB565/gfx`, below 1.00
means libmoy is faster):

| verb | S3 before | **S3 now** | P4 | verdict |
|---|---|---|---|---|
| `print` | 1.21× slower | **1.04×** | tie | crossed; measured 1.03× in the console |
| `blit_map` | 1.54× slower | **0.78×** | 0.92× | crossed; **640.6 → 453.1 µs/op** in the console |
| `spr` | 1.06× slower | **0.79×** | 0.83× | crossed; **406.2 → 296.9 µs/op** (0.73×) |

The two console figures are master → this build **with the `-O3` shim**, both
re-measured on a P4 on 2026-08-07 against a master build flashed the same hour.
The intermediate 562.5 / 335.9 quoted in the commits that landed these verbs
were correct at the time and predate the shim.

**Do not quote this bench's cheap verbs.** `line`, `tri` and `circb` are timed
in batches whose size is chosen per run by a doubling ladder, and they disagree
with a direct probe by 2–4× and vary between runs on ONE build — `line` read
31.2 µs/op and 62.5 on the same master image. Measured directly, libmoy's `line`
and the transcription it replaced are **identical** (14.41 µs/op median, five
samples each, both builds), so nothing about those three verbs changed. Only
verbs expensive enough to fill a batch at a small, stable `k` (`map`, `sprb`,
`cls`, `sspr`, `tline`) carry a number worth comparing. The harness now prints
`k` and the min/med/max spread so this is visible rather than inferred.

`blit_map` and `print` crossed on 2026-08-07 and are **verified on glass**: all
ten conformance scenes pass on an ESP32-P4, `tilemap` among them, and the Bench
cart puts `map` at 0.88× — the largest single verb gain on either board, since
it was also the most expensive verb by four times.

The sprite path crossed too, and it turned out to be **one C function** rather
than the three the name suggests: `blit565` blits arbitrary RGB565 sources
(Images, layers) and has no libmoy counterpart, `spr_gate` only fills the batch
array, and both batch call sites funnel into `blit_batch`. The #43 dispatch win
is untouched — one canvas is borrowed for the whole run and the loop still
collapses N sprites into one MicroPython→C call; only the inner blit moved.

**The RGB565 tile atlas is gone.** `_sheet_atlas` baked all 512 tiles with pal
and colorkey folded in, and nothing reads it any more. Three things followed:

| | |
|---|---|
| `sprb` | 406.2 → **296.9 µs/op** (0.73×, with the `-O3` shim) |
| `map` | 640.6 → **453.1 µs/op** (0.71×, with the `-O3` shim) |
| worst frame | 102 ms → **25 ms** |

That last one was not predicted and is the best of the three. The bake was
32,768 MicroPython loop iterations plus a 64 KB allocation, paid on first
map/spr use — a ~100 ms hitch that had been sitting in the silent phase of every
Bench run all along, attributed to nothing. Removing the cache removed the
stall, and the 64 KB it held goes back to the S3, where internal SRAM is the
scarcest thing there is.

A paint edit now needs no cache invalidation at all, because there is no cache:
the sheet IS the source.

`moy_spr`'s scale-1 fast path is what moved the last two: `moy_map_draw` routes
every cell through it, so fixing one fixed both. The old write-up's diagnosis
("resolves per pixel through `moy_put`") described code that no longer existed
by the time it was quoted.

Two consequences worth stating plainly. **`map` is the console's most expensive
verb** — 640 µs/op, four times the next — so a 22% win there is the largest
single raster gain available on either board. And **adopting `spr` hands back
the 64 KB RGB565 tile atlas**, on the board where 64 KB of internal SRAM is the
scarcest thing there is.

The lesson is not about libmoy. It is that a measurement written up confidently
outlives the code it measured, and nobody re-runs a number that reads like a
verdict. The dates are in the table for that reason.

## Why the 3D verbs went first

`provisional_tline` **failed on the board and passed on the host** — 2773
pixels, 3.61%. The host and the device ran two hand-written copies of the same
kernel, `test_device_canvas_parity.py` compared the host against a *Python
transcription* of the C, and none of that could catch the C itself being wrong.
Only on-glass conformance could, and it had never been run on that verb.

Routing the verb through the spec's own raster fixed it: **0 differing pixels**,
and all ten scenes pass on an ESP32-P4. That is the argument for this directory,
made once rather than asserted.

## Do not edit these files

Fix it upstream in moy-spec, then:

```sh
make vendor-libmoy                          # ../moy-spec beside this repo
make vendor-libmoy SPEC=/path/to/moy-spec
```

An edit made here instead is a red test (`tests/test_libmoy_vendor.py`).

## The sheet is the spec's shape

libmoy addresses a sprite sheet with SPEC.md §3.2's fixed geometry — 128×256,
16 tiles per row, 512 tiles — rather than a width passed per call. A cart sheet
is exactly that (`Project._build_sheet` makes a 16×32 `SpriteSheet`, and
`from_hex` pads a short pre-512 blob into the top half with tile ids unchanged),
so the two agree. `moy_gfx_is_moy_sheet` guards it anyway: a sheet of another
shape would be read at the wrong stride, and drawing it wrong is worse than not
drawing it.

## What this costs

`moy_data.c` brings libmoy's own copy of the §2.2 palette and the §6 font —
about 1 KB of tables that moybyte already has in other forms. Whole translation
units come over rather than the three functions actually called, because taking
half a `.c` file is how a vendored copy stops being a copy; the linker's
`--gc-sections` drops what nothing calls.
