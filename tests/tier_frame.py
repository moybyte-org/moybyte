"""The tier cart's frame as every tier hashes it (tests/fixtures/wasm/tier.moy).

The cart draws the same frame forever -- par's bands, blit565, its own file
through `read`, a config value through `cfg` -- so a host, a board and a page
that run it must hold the same pixels, and `tests/shell_goldens/wasm_tier.json`
is that frame's one digest. Each tier hands over its screen in its own shape
(the host's canvas in its byte order, a page's canvas as RGBA), so this is
where they become the one shape the golden is over: 320 x 240 canonical
little-endian RGB565 words, with the console's FPS corner -- perf_hud's tap
box, which a page shows and the host test turns off -- zeroed."""

import hashlib
import json
from pathlib import Path

GOLDEN = Path(__file__).resolve().parent / "shell_goldens" / "wasm_tier.json"
ROW = "rgb565_320x240_fps_corner_zeroed"
W, H = 320, 240
CORNER = (W - 40, H - 14)      # perf_hud.PerfHud._fps_tap_rect on 320 x 240


def digest(words):
    """sha256 of `words` (W*H canonical RGB565 values) with the corner zeroed."""
    out = bytearray(W * H * 2)
    for i, v in enumerate(words):
        x, y = i % W, i // W
        if x >= CORNER[0] and y >= CORNER[1]:
            v = 0
        out[2 * i] = v & 255
        out[2 * i + 1] = v >> 8
    return hashlib.sha256(bytes(out)).hexdigest()


def from_canvas(buf, swapped):
    """The words of a canvas's framebuffer, in its byte order."""
    b = bytes(buf)
    if swapped:
        return [(b[2 * i] << 8) | b[2 * i + 1] for i in range(W * H)]
    return [b[2 * i] | (b[2 * i + 1] << 8) for i in range(W * H)]


def from_rgba(rgba):
    """The words a page's canvas shows: its RGBA is RGB565 with each channel's
    high bits repeated (page_core.html's LUT), so the top bits are the word."""
    out = []
    for i in range(W * H):
        r, g, b = rgba[4 * i], rgba[4 * i + 1], rgba[4 * i + 2]
        out.append(((r >> 3) << 11) | ((g >> 2) << 5) | (b >> 3))
    return out


def golden():
    return json.loads(GOLDEN.read_text())[ROW]
