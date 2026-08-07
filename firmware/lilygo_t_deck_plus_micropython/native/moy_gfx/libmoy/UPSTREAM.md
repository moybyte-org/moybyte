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

## Which verbs cross, and which do not

`modmoy_gfx.c` is not a libmoy port. It is moybyte's compositor kernel, and only
the verbs whose GEOMETRY the spec defines route through libmoy:

| | |
|---|---|
| **libmoy's** | `tri`, `sspr`, `tline`, `circ`, `circb`, `line` |
| **measured and DECLINED** | `print`, `blit_map`, the sprite path (`blit565`/`blit_batch`/`spr_gate`) |
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
| `print` | 1.21× slower | **1.04×** | tie | a tie; crossing costs ~0.8 µs/op |
| `blit_map` | 1.54× slower | **0.78×** | 0.92× | **libmoy wins on both** |
| `spr` | 1.06× slower | **0.79×** | 0.83× | **libmoy wins on both** |

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
