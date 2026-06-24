"""KID64 palette for the v0.4 fantasy-workstation canvas.

64 indexed colors. Indices 0-15 are the well-known PICO-8 base palette (familiar,
pleasant, and the culture v0.4 targets); 16-63 are a generated HSV ramp so a
cartridge has a full 64-color workspace. Colors are (r, g, b) 0-255.

The canvas works in palette indices; `Canvas.to_rgb888()` resolves them through
this table. On device these same indices map to the RGB565 framebuffer the
native compositor flushes.
"""

import colorsys

# PICO-8 base 16 (indices 0-15).
_BASE16 = [
    (0x00, 0x00, 0x00),  # 0  black
    (0x1D, 0x2B, 0x53),  # 1  dark-blue
    (0x7E, 0x25, 0x53),  # 2  dark-purple
    (0x00, 0x87, 0x51),  # 3  dark-green
    (0xAB, 0x52, 0x36),  # 4  brown
    (0x5F, 0x57, 0x4F),  # 5  dark-grey
    (0xC2, 0xC3, 0xC7),  # 6  light-grey
    (0xFF, 0xF1, 0xE8),  # 7  white
    (0xFF, 0x00, 0x4D),  # 8  red
    (0xFF, 0xA3, 0x00),  # 9  orange
    (0xFF, 0xEC, 0x27),  # 10 yellow
    (0x00, 0xE4, 0x36),  # 11 green
    (0x29, 0xAD, 0xFF),  # 12 blue
    (0x83, 0x76, 0x9C),  # 13 indigo
    (0xFF, 0x77, 0xA8),  # 14 pink
    (0xFF, 0xCC, 0xAA),  # 15 peach
]


def _build_palette():
    pal = list(_BASE16)
    # Fill 16-63 with a hue/value ramp: 16 hues x 3 brightness tiers.
    for i in range(64 - len(_BASE16)):
        hue = (i % 16) / 16.0
        val = 0.45 + 0.25 * (i // 16)  # 0.45, 0.70, 0.95
        r, g, b = colorsys.hsv_to_rgb(hue, 0.75, val)
        pal.append((int(r * 255), int(g * 255), int(b * 255)))
    return pal


KID64 = _build_palette()

# Named indices used across the system / carts.
NAMES = {
    "black": 0,
    "dark_blue": 1,
    "dark_purple": 2,
    "dark_green": 3,
    "brown": 4,
    "dark_grey": 5,
    "light_grey": 6,
    "white": 7,
    "red": 8,
    "orange": 9,
    "yellow": 10,
    "green": 11,
    "blue": 12,
    "indigo": 13,
    "pink": 14,
    "peach": 15,
}


def color(name_or_index):
    """Resolve a color name or index to a 0-63 palette index."""
    if isinstance(name_or_index, str):
        return NAMES.get(name_or_index, 7)
    return int(name_or_index) & 63


def rgb888_table(palette=KID64):
    """Flat bytes table [r,g,b]*len(palette) for fast index->RGB resolution."""
    out = bytearray(len(palette) * 3)
    for i, (r, g, b) in enumerate(palette):
        out[i * 3] = r
        out[i * 3 + 1] = g
        out[i * 3 + 2] = b
    return bytes(out)
