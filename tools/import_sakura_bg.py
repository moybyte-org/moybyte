#!/usr/bin/env python3
"""Import the Sakura carts' backdrop from the owner's source image.

`system_carts/sakura.moy` and `system_carts/sakura_lua.moy` share one static
scene: `images/bg.moyimg`, a 320x240 MOY64 index bitmap the cart bakes into an
off-screen layer with a single spr() (see the carts' main.py/main.lua header).
This script is the conversion that produced it -- run it again with the same
source image to reproduce the shipped bytes exactly.

The source is an AI-generated cherry-tree scene supplied by the project owner
-- the project's own image, with no outside rights holder.

THE HIGH-RESOLUTION ORIGINAL IS LOST (it was handed to a session and never
committed). What is committed is `assets/sakura_bg.png`, the shipped bitmap
rendered back out at 320x240 -- and because the crop is already 4:3, the
downscale is then identity and the quantise is idempotent, re-importing it
reproduces `bg.moyimg` BYTE-FOR-BYTE. So the pipeline is reproducible from a
clean clone; what is gone is the ability to re-derive at a different size or
crop. Run with no arguments to use it:

    .venv/bin/python tools/import_sakura_bg.py ~/sakura_source.jpeg
    .venv/bin/python tools/import_sakura_bg.py SRC --png /tmp/sakura.png
    .venv/bin/python tools/import_sakura_bg.py SRC --emit      # the EMIT tables
    .venv/bin/python tools/import_sakura_bg.py SRC --dry-run   # write nothing

The pipeline: centre-crop to 4:3 (the source is a little wider), LANCZOS down to
320x240, then quantise to MOY64 by nearest colour in a luma-weighted RGB metric.
Dithering is available (`--dither`, Floyd-Steinberg at the given strength) but
ships OFF: the source is pixel art whose palette already sits close to MOY64's
lavender/rose family, and both error-diffused and ordered dithering laid a
visible crosshatch over the flat sky and water. Straight nearest-colour keeps
those areas clean.

`--emit` prints the carts' `EMIT` literals -- the petal-shedding points, which
have to sit on the canopy of THIS image. They are derived from the quantised
result (dense runs of blossom colour, thinned onto a grid), printed in both
Python and Lua syntax; the two carts' tables must stay identical or
tests/test_lua_sakura_parity.py fails.

After importing, bump `"version"` in BOTH carts' manifest.json (#47) or an
already-seeded device keeps the old art, and refresh sakura_lua's cover with
`tools/gen_covers.py sakura_lua`.

Needs Pillow (a declared dev dependency); everything else is stdlib.
"""

import argparse
import json
import os
import random
import struct
import sys
import zlib

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from runtime.palette import MOY64  # noqa: E402

W, H = 320, 240
CARTS = ("sakura", "sakura_lua")

# Nearest-colour metric: plain RGB distance weighted toward how much each
# channel carries luma, which keeps the sky's lavender ramp from snapping to
# a same-brightness blue.
_WEIGHT = (2.0, 4.0, 3.0)

# The canopy's colours in the quantised image (rose/orchid family). Used only
# to find petal-shedding points; a stray falling petal is excluded by the
# density test in emit_points, not by this list.
BLOSSOM = (14, 16, 24, 25, 26)
EMIT_MAX_Y = 168        # below this the pinks are fallen petals on the island
EMIT_CELL = (17, 15)    # grid the shedding points are thinned onto
EMIT_MAX = 145
EMIT_SEED = 20260729


def crop_4x3(im, left):
    """Crop the widest 4:3 window `left` pixels in from the source's left edge."""
    w, h = im.size
    ch = h - (h % 3)
    cw = ch * 4 // 3
    if cw > w:
        cw = w - (w % 4)
        ch = cw * 3 // 4
    left = max(0, min(int(left), w - cw))
    return im.crop((left, 0, left + cw, ch))


def quantise(pixels, dither=0.0):
    """RGB triples (row-major, W*H of them) -> MOY64 index bytes."""
    cache = {}

    def nearest(rgb):
        got = cache.get(rgb)
        if got is None:
            best, bestd = 0, None
            for i, (pr, pg, pb) in enumerate(MOY64):
                dr, dg, db = rgb[0] - pr, rgb[1] - pg, rgb[2] - pb
                d = (_WEIGHT[0] * dr * dr + _WEIGHT[1] * dg * dg
                     + _WEIGHT[2] * db * db)
                if bestd is None or d < bestd:
                    best, bestd = i, d
            cache[rgb] = got = best
        return got

    if dither <= 0.0:
        return bytes(nearest(p) for p in pixels)

    work = [list(p) for p in pixels]      # float-ish accumulation for the error
    out = bytearray(W * H)
    for y in range(H):
        for x in range(W):
            i = y * W + x
            cur = work[i]
            key = (min(255, max(0, int(round(cur[0])))),
                   min(255, max(0, int(round(cur[1])))),
                   min(255, max(0, int(round(cur[2])))))
            idx = nearest(key)
            out[i] = idx
            pal = MOY64[idx]
            err = [(cur[c] - pal[c]) * dither for c in range(3)]
            for dx, dy, f in ((1, 0, 7 / 16.0), (-1, 1, 3 / 16.0),
                              (0, 1, 5 / 16.0), (1, 1, 1 / 16.0)):
                nx, ny = x + dx, y + dy
                if 0 <= nx < W and ny < H:
                    tgt = work[ny * W + nx]
                    for c in range(3):
                        tgt[c] += err[c] * f
    return bytes(out)


def convert(path, left, dither):
    from PIL import Image
    im = Image.open(path).convert("RGB")
    small = crop_4x3(im, left).resize((W, H), Image.LANCZOS)
    return quantise(list(small.getdata()), dither)


def emit_points(buf):
    """Petal-shedding anchors for the carts' EMIT table: pixels inside a DENSE
    patch of blossom colour (so a lone drifting petal or a rose glint on the
    water never qualifies), thinned to one per grid cell so the flurry comes
    down evenly across the canopy."""
    fam = set(BLOSSOM)
    mask = [1 if b in fam else 0 for b in buf]
    cells = {}
    cw, ch = EMIT_CELL
    for y in range(2, min(EMIT_MAX_Y, H - 2)):
        for x in range(2, W - 2):
            if not mask[y * W + x]:
                continue
            near = 0
            for dy in range(-2, 3):
                row = (y + dy) * W + x
                for dx in range(-2, 3):
                    near += mask[row + dx]
            if near < 14:                  # under ~56% of a 5x5 neighbourhood
                continue
            cells.setdefault((x // cw, y // ch), []).append((x, y))
    rng = random.Random(EMIT_SEED)
    pts = [rng.choice(v) for _, v in sorted(cells.items())]
    if len(pts) > EMIT_MAX:
        rng.shuffle(pts)
        pts = pts[:EMIT_MAX]
    pts.sort()
    return pts


def _wrap(text, width=86, indent=""):
    out, line = [], indent
    for word in text.split(" "):
        if line != indent and len(line) + len(word) + 1 > width:
            out.append(line)
            line = indent
        line += ("" if line == indent else " ") + word
    out.append(line)
    return "\n".join(out)


def emit_tables(pts):
    py = "EMIT = [" + ", ".join("(%d, %d)" % p for p in pts) + "]"
    lua = "EMIT = { " + ", ".join("{%d, %d}" % p for p in pts) + " }"
    return py, _wrap(lua)


def write_png(path, buf, scale=1):
    rows = bytearray()
    for y in range(H * scale):
        rows.append(0)
        base = (y // scale) * W
        for x in range(W * scale):
            r, g, b = MOY64[buf[base + x // scale]]
            rows += bytes((r, g, b))

    def chunk(tag, data):
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    png = (b"\x89PNG\r\n\x1a\n"
           + chunk(b"IHDR", struct.pack(">IIBBBBB", W * scale, H * scale,
                                        8, 2, 0, 0, 0))
           + chunk(b"IDAT", zlib.compress(bytes(rows), 9))
           + chunk(b"IEND", b""))
    with open(path, "wb") as f:
        f.write(png)


def encode_bg(buf):
    """The legacy zlib .moyimg envelope -- what both carts' bg has always used
    (a dense 320x240 scene compresses far better than the RLE codec, and every
    decoder dispatches on the absent `codec` field)."""
    import base64
    return json.dumps({
        "format": "moyimg-v1", "w": W, "h": H,
        "data": base64.b64encode(zlib.compress(buf, 9)).decode("ascii"),
    })


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("source", nargs="?", default=os.path.join(ROOT, "assets", "sakura_bg.png"),
                    help="source image (default: the committed assets/sakura_bg.png)")
    ap.add_argument("--left", type=int, default=14,
                    help="left edge of the 4:3 crop (default 14: centred)")
    ap.add_argument("--dither", type=float, default=0.0,
                    help="Floyd-Steinberg strength; 0 (default) = none")
    ap.add_argument("--png", help="also write a preview PNG here")
    ap.add_argument("--scale", type=int, default=1, help="preview PNG scale")
    ap.add_argument("--emit", action="store_true",
                    help="print the EMIT literals for main.py / main.lua")
    ap.add_argument("--dry-run", action="store_true",
                    help="convert (and preview) but do not touch the carts")
    args = ap.parse_args(argv)

    buf = convert(args.source, args.left, args.dither)
    if args.png:
        write_png(args.png, buf, max(1, args.scale))
        print("preview ->", args.png)
    if not args.dry_run:
        blob = encode_bg(buf)
        for slug in CARTS:
            d = os.path.join(ROOT, "system_carts", slug + ".moy", "images")
            os.makedirs(d, exist_ok=True)
            with open(os.path.join(d, "bg.moyimg"), "w") as f:
                f.write(blob)
            print("wrote", os.path.join(d, "bg.moyimg"), len(blob), "bytes")
    if args.emit:
        py, lua = emit_tables(emit_points(buf))
        print("\n--- main.py ---\n" + py)
        print("\n--- main.lua ---\n" + lua)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
