#!/usr/bin/env python3
"""The console's icon sets (docs/look_2026-10.md step 16): the pixel set and
the smooth sets' masks, built from their sources in native/moy_ui/icons/.

Every set fills the slots of `pixel/slots.txt`.

* The PIXEL set is `pixel/system_icons.moygfx`, a 16x16 IconSheet of 8x7
  tiles that `pixel/pixel_icons.py` draws. Its first line is `clear 16`: its
  clear pixel is past the palette (a `.` in the file), so the canonical
  Moy's index-0 outline draws.
* A SMOOTH set is `smooth/<set>/<slot>.svg`, 24-unit line art, and a
  `<slot>.fill.svg` layer where the set has two. Each layer of each set is
  rasterised by resvg at every size of SIZES into a font file, format
  version 2 (native/moy_gfx/moy_text.h): slot n is the glyph at byte
  FIRST + n, its box placed so that the glyph drawn at (x, y) puts the
  icon's square at (x, y); 2 bpp at 16 px and under, 4 bpp above. A
  slot's coverage is the alpha of its render quantised as the targets'
  (docs/media/look_2026-10/mockups.py `levels`). Moy is not a mask: it is
  pixel art in every set. Redo is undo's mirror, in both kinds: its
  source is never read.

  build    every set -> native/moy_ui/icons/<set>[.fill]-<size>.fnt and
           set.json (the files, the sets each tier takes, the slots, the
           rasteriser's version), and the pixel set from pixel_icons.py.
  check    rebuild in memory and compare with the committed files; refused
           under another resvg, since a different rasteriser moves pixels.
  report   the bytes each tier's icons cost.

A tier takes the sets a seed theme names (native/moy_ui/themes/), at the
tier's sizes; tools/make_font.py's `module` freezes them beside the fonts.

Run: python tools/make_icons.py [build | check | report]
"""

import io
import json
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
ICONS = os.path.join(ROOT, "native", "moy_ui", "icons")
THEMES = os.path.join(ROOT, "native", "moy_ui", "themes")
PIXEL = os.path.join(ICONS, "pixel", "system_icons.moygfx")

SIZES = {"handheld": (16,), "desktop": (16, 20, 24)}
FIRST = 0x21                # slot 0's byte; a version 2 font draws ASCII bytes as given
MIRRORED = {"redo": "undo"}
PIXEL_ONLY = ("moy",)
DEFAULT_SET = "default"
FILL_DEFAULT = "#7da7ea"
# A two-layer set's fill colours, slot by slot (the targets' LUNA_FILL,
# docs/media/look_2026-10/rounds/round7.py): the fill is the set's art, so it
# travels with the set, not with a theme.
FILLS = {
    "luna": {"files": "#f5c542", "folder": "#f5c542", "notes": "#ffffff", "doc": "#ffffff",
             "art": "#ffffff", "wallpaper": "#9fd0ff", "play": "#46b843", "run": "#46b843",
             "game": "#c9d3e6", "settings": "#b8c0cc", "trash": "#c9d3e6", "del": "#c9d3e6",
             "moy": "#c9b8ff", "home": "#f2a65a", "projects": "#f5c542", "star_on": "#f5c542",
             "paint": "#e2582e", "storybook": "#e2582e", "music": "#46b843",
             "battery": "#46b843", "calc": "#c9d3e6", "getcarts": "#46b843",
             "save": "#7da7ea", "sprite": "#9be36b", "map": "#9fd0ff", "mute": "#c9d3e6",
             "copy": "#ffffff", "palette": "#f2c38a",
             "export": "#f5c542"},
}


def slots():
    with open(os.path.join(ICONS, "pixel", "slots.txt")) as f:
        return [ln.split()[1] for ln in f if ln.strip()]


def sets():
    """{set: [layer, ...]}: every smooth set and its layers ("ink", "fill")."""
    out = {}
    d = os.path.join(ICONS, "smooth")
    for name in sorted(os.listdir(d)):
        files = os.listdir(os.path.join(d, name))
        out[name] = ["ink"] + (["fill"] if any(f.endswith(".fill.svg") for f in files) else [])
    return out


def versions():
    from importlib import metadata
    return {"resvg-py": metadata.version("resvg-py")}


def bpp_of(size):
    return 2 if size <= 16 else 4


def file_name(set_name, layer, size):
    return "%s%s-%d.fnt" % (set_name, ".fill" if layer == "fill" else "", size)


def _levels(alpha, bpp):
    """Coverage 0..255 -> levels 0..2**bpp-1, as the targets quantise."""
    n = (1 << bpp) - 1
    return [round(a * n / 255) for a in alpha]


def raster(set_name, layer, slot, size):
    """The slot's coverage at `size` px, rows of levels, or None when the set
    has no source for it."""
    import resvg_py
    from PIL import Image
    name = slot + (".fill" if layer == "fill" else "") + ".svg"
    path = os.path.join(ICONS, "smooth", set_name, name)
    if not os.path.exists(path):
        return None
    with open(path) as f:
        src = f.read()
    png = bytes(resvg_py.svg_to_bytes(svg_string=src, width=size, height=size))
    a = list(Image.open(io.BytesIO(png)).convert("RGBA").getchannel("A").tobytes())
    lv = _levels(a, bpp_of(size))
    return [lv[y * size:(y + 1) * size] for y in range(size)]


def _pack(levels, bpp):
    out = bytearray((len(levels) * bpp + 7) // 8)
    for i, v in enumerate(levels):
        bit = i * bpp
        out[bit >> 3] |= v << (8 - bpp - (bit & 7))
    return bytes(out)


def build_font(set_name, layer, size, names=None):
    """A version 2 font file: the slots of `set_name`'s `layer` at `size`."""
    names = slots() if names is None else names
    bpp = bpp_of(size)
    index = [0] * 256
    recs = [struct.pack("<I", 0)[:3] + struct.pack("<BBbbB", 0, 0, 0, 0, size)]
    cov = bytearray()
    rows_of = {}
    for n, slot in enumerate(names):
        if slot in PIXEL_ONLY:
            continue
        src = MIRRORED.get(slot)
        if src is not None:
            got = rows_of.get(src) or raster(set_name, layer, src, size)
            rows = [r[::-1] for r in got] if got is not None else None
        else:
            rows = raster(set_name, layer, slot, size)
        if rows is None:
            continue
        rows_of[slot] = rows
        ys = [y for y in range(size) if any(rows[y])]
        xs = [x for x in range(size) if any(r[x] for r in rows)]
        if not ys:
            continue
        x0, x1, y0, y1 = xs[0], xs[-1] + 1, ys[0], ys[-1] + 1
        lv = [rows[y][x] for y in range(y0, y1) for x in range(x0, x1)]
        index[FIRST + n] = len(recs)
        recs.append(struct.pack("<I", len(cov))[:3]
                    + struct.pack("<BBbbB", x1 - x0, y1 - y0, x0, size - y0, size))
        cov += _pack(lv, bpp)
    head = b"MFNT" + bytes((2, bpp, size, size, size, len(recs))) + struct.pack("<HI", 0, 0)
    data = head + bytes(index) + b"".join(recs) + bytes(cov)
    return data[:12] + struct.pack("<I", len(data)) + data[16:]


def seed_sets():
    """The smooth sets the seed themes name: what an image freezes."""
    names = set()
    for f in sorted(os.listdir(THEMES)):
        if not f.endswith(".json"):
            continue
        with open(os.path.join(THEMES, f)) as fh:
            look = json.load(fh).get("look", {})
        for part in [look] + [look.get(v, {}) for v in ("dark", "light")]:
            ic = part.get("icons") if isinstance(part, dict) else None
            if isinstance(ic, dict) and ic.get("kind") == "smooth":
                names.add(ic.get("set") or DEFAULT_SET)
    return sorted(names | {DEFAULT_SET})


def pixel_text():
    """The pixel set's .moygfx, drawn by pixel_icons.py."""
    sys.path.insert(0, os.path.join(ICONS, "pixel"))
    try:
        import pixel_icons
    finally:
        sys.path.pop(0)
    return pixel_icons.moygfx(slots())[0]


def build_all():
    files = {}
    table = {}
    for name, layers in sets().items():
        for layer in layers:
            for size in sorted({s for t in SIZES.values() for s in t}):
                fn = file_name(name, layer, size)
                files[fn] = build_font(name, layer, size)
                table[fn[:-4]] = {"set": name, "layer": layer, "size": size,
                                  "bpp": bpp_of(size), "bytes": len(files[fn])}
    frozen = seed_sets()
    tiers = {}
    for tier, sizes in SIZES.items():
        tiers[tier] = sorted(k for k, v in table.items()
                             if v["set"] in frozen and v["size"] in sizes)
    meta = {"about": "The icon sets tools/make_icons.py builds (docs/look_2026-10.md "
                     "step 16): a smooth set's masks, a font file per layer and size, "
                     "slot n at byte `first` + n; a board takes its tier's files and "
                     "the pixel set.",
            "built_with": versions(), "first": FIRST, "slots": slots(),
            "mirrored": MIRRORED, "pixel_only": list(PIXEL_ONLY),
            "fills": {k: v for k, v in FILLS.items() if k in sets()},
            "fill_default": FILL_DEFAULT, "sets": sets(), "tiers": tiers, "masks": table}
    files["set.json"] = (json.dumps(meta, indent=1, sort_keys=True) + "\n").encode()
    files[os.path.join("pixel", "system_icons.moygfx")] = pixel_text().encode()
    return files


def module_lines(tier):
    """The `ICONS` dict make_font.py's fonts_data carries: tier `tier`'s mask
    files and the pixel set, as bytes."""
    with open(os.path.join(ICONS, "set.json"), "rb") as f:
        meta_b = f.read()
    names = json.loads(meta_b)["tiers"].get(tier, [])
    out = ["ICONS = {"]
    for n in names:
        with open(os.path.join(ICONS, n + ".fnt"), "rb") as f:
            out.append("    %r: %r," % (n + ".fnt", f.read()))
    with open(PIXEL, "rb") as f:
        out.append("    %r: %r," % ("system_icons.moygfx", f.read()))
    out.append("}")
    return out


def main(argv):
    cmd = argv[1] if len(argv) > 1 else "build"
    if cmd in ("-h", "--help"):
        print(__doc__)
        return 0
    if cmd == "report":
        meta = json.load(open(os.path.join(ICONS, "set.json")))
        px = os.path.getsize(PIXEL)
        for tier, names in meta["tiers"].items():
            masks = sum(meta["masks"][n]["bytes"] for n in names)
            print("%s: %d mask files, %d bytes; the pixel set %d bytes"
                  % (tier, len(names), masks, px))
        for name in meta["sets"]:
            got = {k: v["bytes"] for k, v in meta["masks"].items() if v["set"] == name}
            print("  %s: %s" % (name, ", ".join("%s %d" % kv for kv in sorted(got.items()))))
        return 0
    if cmd == "check":
        meta = json.load(open(os.path.join(ICONS, "set.json")))
        if meta["built_with"] != versions():
            raise SystemExit("make_icons check: built with %s, this is %s"
                             % (meta["built_with"], versions()))
    files = build_all()
    if cmd == "check":
        bad = []
        for n, d in files.items():
            p = os.path.join(ICONS, n)
            if not os.path.exists(p) or open(p, "rb").read() != d:
                bad.append(n)
        have = {f for f in os.listdir(ICONS) if f.endswith(".fnt")}
        bad += sorted(have - set(files))
        if bad:
            print("make_icons check: these differ from their sources: " + ", ".join(bad))
            return 1
        print("make_icons check: %d files match their sources" % len(files))
        return 0
    if cmd != "build":
        print(__doc__)
        return 2
    for f in os.listdir(ICONS):
        if f.endswith(".fnt"):
            os.remove(os.path.join(ICONS, f))
    for n, d in files.items():
        with open(os.path.join(ICONS, n), "wb") as f:
            f.write(d)
    print("make_icons: %d files" % len(files))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
