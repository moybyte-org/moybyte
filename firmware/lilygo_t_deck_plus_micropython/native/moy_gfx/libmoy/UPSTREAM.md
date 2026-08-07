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

The second row is the interesting one, and it is a measurement rather than a
preference. All four rasters hash identical on every verb; only the cost differs:

| verb | ESP32-S3 | ESP32-P4 | kept |
|---|---|---|---|
| `print` | libmoy **1.21× slower** | identical (18.8 µs/op either way) | moy_gfx |
| `blit_map` | libmoy **1.54× slower** | libmoy 0.92× | moy_gfx |
| `spr` | moy_gfx **6% faster** | libmoy 0.83× | moy_gfx |

The causes are named, not mysterious. `moy_gfx_text_raw` hoists the clip and
early-outs per glyph where `moy_print` walks bits through `moy_put`.
`moy_map_draw` routes each cell through `moy_spr` and resolves colour per pixel,
where `blit_map` copies 16-bit words out of a pre-baked RGB565 atlas. And the
sprite path pairs that atlas with the #43 batch protocol — which, note, beats
libmoy by only six percent, so that architecture is worth much less than its
complexity suggests.

**The S3 is what decides these, and the P4 disagrees with it on two of the
three.** That is the whole reason both boards get measured: the constrained
board is the one a cart is too slow on, and it is also the one that cannot be
driven remotely (its USB-CDC RX is dead under the desktop), so its numbers come
from the standalone A/B bench rather than from the console.

`print` is the sharpest case, because SPEC.md §6 has no scale ("text is always
8px") so the cart's `print` *is* scale 1 and could cross. It was routed through
`moy_print`, measured identical on the P4, and **reverted on the S3 number** —
the console draws chrome text by the dozen per frame there. Scale > 1 was never
a cart anyway; that is this console's chrome at the #39 system font size, which
is host business (SPEC.md §0).

### Two of those three numbers are SUSPECT — re-measure before trusting them

The S3 column came from a standalone A/B bench run at 21:04 on 2026-08-06. The
libmoy in that build got `moy_print`'s whole-column off-clip early-out and
`moy_spr`'s scale-1 fast path **afterwards** (the source in the bench tree is
stamped 21:45), and the run's own write-up describes the pre-optimization code
in both cases: *"moy_print walks bits through moy_put"* and *"moy_map_draw
routes each cell through moy_spr and therefore moy_put, resolving per pixel"*.
That second one is no longer true at scale 1 — which is the only scale a
tilemap uses by default.

So `print` and `blit_map` may have been declined against a libmoy that no
longer exists. `spr` lost by only six percent, which is inside the size of that
kind of change too. **Nothing here is settled until the S3 bench is re-run
against current libmoy**, and it needs a hand: that board has no BOOT button and
its USB-CDC RX is dead under the desktop, so it enters the ROM loader only by
holding the trackball (GPIO0) in while powering on.

The P4 half was re-measured and is current. It prefers libmoy on `blit_map`
(0.92×) and calls `print` a tie — so if the S3 numbers move, all three verbs are
back in play.

Whatever the answer, fixes belong **upstream**: they would benefit every libmoy
host, not just this one. `moy_circ` already took that route — it emitted one
`moy_rect` per row until this console measured its own hand-written direct-span
form faster, and the fix went to moy-spec (`ef01426`) rather than staying here.

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
