"""Generate the seed games' COVERS (SPEC.md 3.6, visual identity v1 Section
11.4): run each game cart headless through the real console for a few seconds,
grab its game canvas, and write it to the cart source's `cover.png` -- a
128 x 128 indexed PNG in the console palette, the profile every host reads.

The frame becomes a cover the way moy-spec's `moy play` F7 capture makes one:
as it is when it is already 128 x 128; otherwise its centre square, reduced by
taking every k-th pixel when the side is k x 128, else area-averaged. An
averaged pixel is a blend, so it is mapped back to the nearest MOY64 colour --
these are pixel carts, and the cover stays one.

Run from the repo root:

    .venv/bin/python tools/gen_covers.py                # every seed cart's cover
    .venv/bin/python tools/gen_covers.py brick_siege    # just one (or a new one)

Covers are committed artifacts (authored once, replaceable by hand or in Paint);
re-running the tool refreshes them. It bumps each cart's manifest "version"
when it writes one, so seeded boards re-seed the cart and pick the cover up
(#47). FRAMES says how long a cart plays before its frame is taken, and a cart
whose usual moment is a bad picture names a better one in PICKS.
"""

import json
import os
import random
import re
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from runtime import cover_png, host_app, host_canvas, palette  # noqa: E402

MOY64_RGB = bytes(c for rgb in palette.MOY64 for c in rgb)

FRAMES = 80           # ~2.5s of play: the scene develops, nobody has died yet
SEED = 1
COVER_TYPES = ("game", "story")
SIDE = cover_png.SIDE

# slug -> (frames to play, the square (x, y, side) of the frame to take, or
# None for its centre), where the usual moment is a poor picture. Bullet Storm
# has no storm yet at FRAMES; Tap The Coin's whole frame is an empty field
# with one coin in it, so its cover is that coin, at twice its size.
PICKS = {
    "moybyte.bullet_storm": (200, None),
    "moybyte.tap_game": (FRAMES, (34, 160, 64)),
}


def _spans(side):
    """For each of the SIDE output pixels along an axis, [(source offset,
    weight), ...] over a `side`-pixel span; the weights sum to `side`."""
    out = []
    for o in range(SIDE):
        start, end = o * side, (o + 1) * side
        cells = []
        i = start // SIDE
        while i * SIDE < end:
            cells.append((i, min(end, (i + 1) * SIDE) - max(start, i * SIDE)))
            i += 1
        out.append(cells)
    return out


def square(w, h, rgb, crop=None):
    """128 x 128 R, G, B bytes from a w x h picture: its centre square (or the
    `crop` square, (x, y, side)), then every k-th pixel when the side is
    k x 128, each pixel k times over when 128 is k x the side, else the
    overlap-weighted area average rounded half up."""
    if crop is None:
        side = min(w, h)
        x0, y0 = (w - side) // 2, (h - side) // 2
    else:
        x0, y0, side = crop
    out = bytearray(SIDE * SIDE * 3)
    if SIDE % side == 0:
        k = SIDE // side
        for oy in range(SIDE):
            row = ((y0 + oy // k) * w + x0) * 3
            for ox in range(SIDE):
                s = row + (ox // k) * 3
                d = (oy * SIDE + ox) * 3
                out[d:d + 3] = rgb[s:s + 3]
        return bytes(out)
    if side % SIDE == 0:
        k = side // SIDE
        for oy in range(SIDE):
            row = ((y0 + oy * k) * w + x0) * 3
            for ox in range(SIDE):
                s = row + ox * k * 3
                d = (oy * SIDE + ox) * 3
                out[d:d + 3] = rgb[s:s + 3]
        return bytes(out)
    spans = _spans(side)
    cols = [0] * (side * SIDE * 3)
    for y in range(side):
        src = ((y0 + y) * w + x0) * 3
        for ox in range(SIDE):
            r = g = b = 0
            for i, wt in spans[ox]:
                s = src + i * 3
                r += rgb[s] * wt
                g += rgb[s + 1] * wt
                b += rgb[s + 2] * wt
            d = (y * SIDE + ox) * 3
            cols[d], cols[d + 1], cols[d + 2] = r, g, b
    total = side * side
    half = total // 2
    for oy in range(SIDE):
        for ox in range(SIDE):
            r = g = b = 0
            for i, wt in spans[oy]:
                s = (i * SIDE + ox) * 3
                r += cols[s] * wt
                g += cols[s + 1] * wt
                b += cols[s + 2] * wt
            d = (oy * SIDE + ox) * 3
            out[d] = (r + half) // total
            out[d + 1] = (g + half) // total
            out[d + 2] = (b + half) // total
    return bytes(out)


def to_palette(rgb):
    """R, G, B bytes -> MOY64 indices, the nearest colour by squared distance
    (the lowest index on a tie) -- cover_png's INDEX rule."""
    memo = {}
    out = bytearray(len(rgb) // 3)
    for i in range(len(out)):
        key = bytes(rgb[3 * i:3 * i + 3])
        v = memo.get(key)
        if v is None:
            r, g, b = key
            best, bd = 0, 1 << 30
            for k in range(64):
                dd = ((r - MOY64_RGB[3 * k]) ** 2 + (g - MOY64_RGB[3 * k + 1]) ** 2
                      + (b - MOY64_RGB[3 * k + 2]) ** 2)
                if dd < bd:
                    best, bd = k, dd
            v = memo[key] = best
        out[i] = v
    return bytes(out)


def cover_of(w, h, indices, crop=None):
    """A w x h MOY64 frame -> the bytes of its cover.png."""
    if (w, h) == (SIDE, SIDE) and crop is None:
        return cover_png.encode_indexed(indices, MOY64_RGB)
    rgb = b"".join(MOY64_RGB[3 * v:3 * v + 3] for v in indices)
    return cover_png.encode_indexed(to_palette(square(w, h, rgb, crop)),
                                    MOY64_RGB)


def gen_cover(slug):
    """Run system_carts/<slug>.moy headless and write its cover.png.
    Returns True when a cover was written."""
    src_dir = os.path.join(ROOT, "system_carts", slug + ".moy")
    manifest_path = os.path.join(src_dir, "manifest.json")
    with open(manifest_path) as f:
        manifest = json.load(f)
    if manifest.get("type") not in COVER_TYPES:
        print("skip (not a game):", slug)
        return False
    # A throwaway store seeded from the real system carts, so the run uses the
    # exact seed content (and never mutates the sources).
    tmp = tempfile.mkdtemp(prefix="covers-")
    # A Python cart's rnd() is the random module's: seeded, a cart draws the
    # same frame every run, so a regenerated cover moves only when the cart did.
    random.seed(SEED)
    try:
        ws = host_app.build_workstation(os.path.join(tmp, "carts"))
        target = None
        for i, it in enumerate(ws.launcher.items):
            p = it.get("path") or ""
            if p.endswith("/" + slug + ".moy") or p.endswith(os.sep + slug + ".moy"):
                target = i
                break
        if target is None:
            print("skip (not seeded on the launcher):", slug)
            return False
        ws.launcher.sel = target
        ws.launch_selected()
        if ws.screen != "desktop" or ws.cart_error:
            print("skip (did not start):", slug, ws.cart_error)
            return False
        # Console overlays must not bake into the artwork (#86: several shipped
        # covers carried the host mouse cursor; the fps readout does the same).
        ws.show_fps = False
        if ws.pointer is not None:
            ws.pointer.visible = False
        frames, crop = PICKS.get(slug, (FRAMES, None))
        for _ in range(frames):
            ws._toast_until = 0          # keep system toasts out of the artwork
            if ws.pointer is not None:
                ws.pointer.visible = False
            ws.frame(1 / 30)
        ws._toast_until = 0
        ws._dirty = True
        ws.frame(1 / 30)                 # one clean frame after the last toast clear
        if ws.cart_error:
            print("skip (crashed during capture):", slug, ws.cart_error)
            return False
        cv = ws.canvas                   # the cart's GAME canvas
        # indices_of is the exact reduction of the RGB565 canvas back to MOY64
        # indices -- see runtime/host_canvas.py.
        data = cover_of(cv.w, cv.h, host_canvas.indices_of(cv), crop)
        assert cover_png.decode(data) is not None, "not a cover in the profile"
        out = os.path.join(src_dir, "cover.png")
        with open(out, "wb") as f:
            f.write(data)
        # Bump the manifest version so seeded devices re-seed the cover (#47),
        # in the text, so the manifest keeps the layout its author gave it.
        manifest["version"] = int(manifest.get("version", 1)) + 1
        with open(manifest_path) as f:
            text = f.read()
        bumped = re.sub(r'("version"\s*:\s*)\d+', r"\g<1>%d" % manifest["version"],
                        text, count=1)
        if bumped == text:
            raise SystemExit("%s: no \"version\" to bump" % manifest_path)
        with open(manifest_path, "w") as f:
            f.write(bumped)
        print("wrote", out, len(data), "bytes (manifest version ->",
              manifest["version"], ")")
        return True
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main():
    picks = sys.argv[1:]
    carts = os.path.join(ROOT, "system_carts")
    slugs = picks or sorted(
        n[:-4] for n in os.listdir(carts)
        if n.endswith(".moy") and os.path.exists(os.path.join(carts, n, "cover.png")))
    done = 0
    for slug in slugs:
        try:
            done += 1 if gen_cover(slug) else 0
        except Exception as exc:  # noqa: BLE001 - keep going per cart
            print("FAILED:", slug, exc)
    print(done, "covers written")


if __name__ == "__main__":
    main()
