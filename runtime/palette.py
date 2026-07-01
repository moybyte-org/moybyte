"""MOY64 palette for the v0.4 fantasy-workstation canvas.

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


def _hsv(h, s, v):
    r, g, b = colorsys.hsv_to_rgb(h, s, v)
    return (round(r * 255), round(g * 255), round(b * 255))


# Curated extension (indices 16-63): a DESKTOP-GRADE gamut -- soft pastels, earth
# tones, vivid accents, neutrals and deep shades -- replacing the old mechanical
# "16 hues x 3 brightness tiers, all sat 0.75" ramp. The v0.4 workstation is a
# desktop OS, and naturalistic wallpaper/UI art needs desaturated pastels (skies)
# and warm earth tones (wood/ground) the saturated ramp simply didn't contain.
# The base 16 (PICO-8) is UNCHANGED, so every named colour and every shipped cart
# keeps its exact pixels; only the previously-mechanical filler slots gain meaning.
_PASTEL = [_hsv(h, 0.30, 0.90) for h in          # 16-27: soft skies/atmosphere
           (0.00, 0.06, 0.12, 0.30, 0.45, 0.55, 0.62, 0.72, 0.80, 0.90, 0.96, 0.50)]
_EARTH = [_hsv(0.08, 0.45, 0.55), _hsv(0.07, 0.28, 0.66), _hsv(0.10, 0.55, 0.40),  # 28-35: wood/tan
          _hsv(0.12, 0.22, 0.80), _hsv(0.30, 0.38, 0.45), _hsv(0.05, 0.50, 0.34),
          _hsv(0.09, 0.18, 0.52), _hsv(0.13, 0.33, 0.62)]
_VIVID = [_hsv(h, 0.80, 0.88) for h in           # 36-47: saturated accents (not in base 16)
          (0.04, 0.10, 0.18, 0.33, 0.42, 0.50, 0.58, 0.70, 0.78, 0.86, 0.93, 0.27)]
_NEUTRAL = [_hsv(0.62, 0.10, 0.95), _hsv(0.62, 0.12, 0.78), _hsv(0.62, 0.15, 0.60),  # 48-55: cool/warm greys
            _hsv(0.62, 0.18, 0.42), _hsv(0.08, 0.08, 0.90), _hsv(0.08, 0.10, 0.55),
            _hsv(0.62, 0.20, 0.28), _hsv(0.00, 0.00, 0.16)]
_SHADOW = [_hsv(h, 0.55, 0.40) for h in          # 56-63: deep shades
           (0.00, 0.08, 0.45, 0.55, 0.62, 0.72, 0.85, 0.93)]
_EXTEND48 = _PASTEL + _EARTH + _VIVID + _NEUTRAL + _SHADOW


def _build_palette():
    return list(_BASE16) + _EXTEND48


MOY64 = _build_palette()

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


def rgb888_table(palette=MOY64):
    """Flat bytes table [r,g,b]*len(palette) for fast index->RGB resolution."""
    out = bytearray(len(palette) * 3)
    for i, (r, g, b) in enumerate(palette):
        out[i * 3] = r
        out[i * 3 + 1] = g
        out[i * 3 + 2] = b
    return bytes(out)
