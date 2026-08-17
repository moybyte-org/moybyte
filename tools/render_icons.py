#!/usr/bin/env python3
"""Render the default top-bar IconSheet to a labeled PNG so the baked icons can be
eyeballed (Stage 1). Loads the SAME default theme the console bakes
(console._default_icon_sheet()) and draws each 16x16 icon at 4x on a dark background
with its kind + slot label beside it, mirroring the dark bar they render on.

Pure stdlib (zlib for the PNG) + the repo's runtime modules -- no PIL needed. If the
output path can't be written it falls back to an ASCII grid per icon on stdout.

Usage:
    python tools/render_icons.py [out.png]

Default out is the session scratchpad path the task specifies."""

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

# console.py uses bare `editors`/`audio`/`blocks` names (its frozen device names);
# host_app registers those aliases, so import it first.
from tools import pngwrite  # noqa: E402
from runtime import host_app  # noqa: F401,E402
from runtime import console as C  # noqa: E402
from runtime.palette import _BASE16  # noqa: E402

DEFAULT_OUT = ("/tmp/claude-1000/-home-nikola-Documents-Work-moybyte/"
               "950f1685-037e-4148-b85c-4b08b06bd9eb/scratchpad/icons_preview.png")

SCALE = 4               # px per icon pixel
PAD = 6                 # padding around each cell
LABEL_H = 10            # rows reserved under each icon for the kind label
COLS = 4                # icons per row in the contact sheet
BG = (24, 24, 28)       # dark background (the bar is black; this reads close)
FG = (255, 241, 232)    # label text (MOY64 white)

# A tiny 5x7 bitmap font for the labels (just the chars the kind names use).
_FONT = {
    "A": ["01110", "10001", "10001", "11111", "10001", "10001", "10001"],
    "B": ["11110", "10001", "11110", "10001", "10001", "10001", "11110"],
    "C": ["01110", "10001", "10000", "10000", "10000", "10001", "01110"],
    "D": ["11110", "10001", "10001", "10001", "10001", "10001", "11110"],
    "E": ["11111", "10000", "11110", "10000", "10000", "10000", "11111"],
    "F": ["11111", "10000", "11110", "10000", "10000", "10000", "10000"],
    "G": ["01110", "10001", "10000", "10111", "10001", "10001", "01110"],
    "H": ["10001", "10001", "10001", "11111", "10001", "10001", "10001"],
    "I": ["11111", "00100", "00100", "00100", "00100", "00100", "11111"],
    "K": ["10001", "10010", "10100", "11000", "10100", "10010", "10001"],
    "L": ["10000", "10000", "10000", "10000", "10000", "10000", "11111"],
    "M": ["10001", "11011", "10101", "10101", "10001", "10001", "10001"],
    "N": ["10001", "11001", "10101", "10011", "10001", "10001", "10001"],
    "O": ["01110", "10001", "10001", "10001", "10001", "10001", "01110"],
    "P": ["11110", "10001", "10001", "11110", "10000", "10000", "10000"],
    "R": ["11110", "10001", "10001", "11110", "10100", "10010", "10001"],
    "S": ["01111", "10000", "10000", "01110", "00001", "00001", "11110"],
    "T": ["11111", "00100", "00100", "00100", "00100", "00100", "00100"],
    "U": ["10001", "10001", "10001", "10001", "10001", "10001", "01110"],
    "V": ["10001", "10001", "10001", "10001", "10001", "01010", "00100"],
    "W": ["10001", "10001", "10001", "10101", "10101", "11011", "10001"],
    "X": ["10001", "10001", "01010", "00100", "01010", "10001", "10001"],
    "Y": ["10001", "10001", "01010", "00100", "00100", "00100", "00100"],
    " ": ["00000"] * 7,
    "0": ["01110", "10001", "10011", "10101", "11001", "10001", "01110"],
    "1": ["00100", "01100", "00100", "00100", "00100", "00100", "01110"],
    "2": ["01110", "10001", "00001", "00110", "01000", "10000", "11111"],
    "3": ["11110", "00001", "00001", "01110", "00001", "00001", "11110"],
    "4": ["00010", "00110", "01010", "10010", "11111", "00010", "00010"],
    "5": ["11111", "10000", "11110", "00001", "00001", "10001", "01110"],
    "6": ["01110", "10000", "11110", "10001", "10001", "10001", "01110"],
    "7": ["11111", "00001", "00010", "00100", "01000", "01000", "01000"],
    "8": ["01110", "10001", "01110", "10001", "10001", "10001", "01110"],
    "9": ["01110", "10001", "10001", "01111", "00001", "00001", "01110"],
}


def _draw_text(px, w, x, y, text, color):
    for ch in str(text).upper():
        glyph = _FONT.get(ch, _FONT[" "])
        for ry in range(7):
            row = glyph[ry]
            for rx in range(len(row)):
                if row[rx] == "1":
                    xi, yi = x + rx, y + ry
                    if 0 <= xi < w:
                        px[yi][xi] = color
        x += 6


def _write_png(path, px, w, h):
    rows = [b"".join(bytes(p) for p in row) for row in px]
    pngwrite.write_png(path, rows, w, h)


def _ascii_dump(sheet):
    for kind, slot in C._ICON.items():
        print("== %s (slot %d) ==" % (kind, slot))
        ox, oy = sheet.tile_origin(slot)
        for ly in range(sheet.TILE):
            line = ""
            for lx in range(sheet.TILE):
                c = sheet.pget(ox + lx, oy + ly)
                line += " " if c == 0 else ("%x" % (c & 15))
            print(line)
        print()


def main():
    out = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_OUT
    sheet = C._default_icon_sheet()
    kinds = list(C._ICON.items())
    cell_w = 16 * SCALE + 2 * PAD
    cell_h = 16 * SCALE + 2 * PAD + LABEL_H
    rows = (len(kinds) + COLS - 1) // COLS
    w = COLS * cell_w
    h = rows * cell_h
    px = [[BG for _ in range(w)] for _ in range(h)]

    for i, (kind, slot) in enumerate(kinds):
        cx = (i % COLS) * cell_w + PAD
        cy = (i // COLS) * cell_h + PAD
        ox, oy = sheet.tile_origin(slot)
        for ly in range(16):
            for lx in range(16):
                c = sheet.pget(ox + lx, oy + ly)
                if c == 0:
                    continue                   # leave the dark cell background
                r, g, b = _BASE16[c & 15]
                for sy in range(SCALE):
                    for sx in range(SCALE):
                        px[cy + ly * SCALE + sy][cx + lx * SCALE + sx] = (r, g, b)
        _draw_text(px, w, cx, cy + 16 * SCALE + 1, "%s %d" % (kind, slot), FG)

    try:
        os.makedirs(os.path.dirname(out), exist_ok=True)
        _write_png(out, px, w, h)
        print("Wrote icon preview:", out)
        print("  %d icons, %dx%d px (%dx scale, %d cols)" % (len(kinds), w, h, SCALE, COLS))
    except OSError as exc:
        print("PNG write failed (%s); ASCII dump:\n" % exc)
        _ascii_dump(sheet)


if __name__ == "__main__":
    main()
