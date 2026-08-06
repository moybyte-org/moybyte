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
| **libmoy's** | `tri`, `sspr`, `tline` |
| **still moybyte's** | `fill`, `fill_rect`, `blit565`, `blit565_scale`, `blit_map`, `blit_batch`, `spr_gate`, `circ`, `circb`, `line`, `text`, `copy_async`, `copy_wait`, `scroll_rect`, `blit_window`, `blit_indices`, `fill_spans`, `draw_ctx` |

The second list is two different things. Most of it has **no libmoy
counterpart** — async DMA, sprite batching, the scroll blit, the window
composite, the draw-context gate — and never will; that is moybyte's
compositor, not the spec's raster.

But `fill_rect`, `circ`, `circb`, `line` and `text` **do** have counterparts and
are still transcriptions. They stay for now because each is a small, closed
loop that the conformance goldens already pin, and because moving them means
moving the sprite path too (`blit565` is a 565→565 blit of a pre-scaled cache,
which is a different strategy from `moy_spr` reading the raw sheet, and the
cache exists because per-pixel palette lookup was too slow on the S3). That
call wants an ESP32-S3 measurement, which does not exist yet.

## Why these three went first

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
