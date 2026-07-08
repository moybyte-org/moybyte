# Moy Night -- the moybyte desktop wallpaper (brand colorway).
#
# A STATIC night scene in the site palette: a midnight-navy sky with dithered
# gradient bands, a faint dot grid, a deterministic starfield, a glowing crescent
# moon, and Moy asleep on the horizon under the two-tone wordmark. No _update, so
# the desktop backdrop never forces a repaint (idle stays free, incl. the web
# view). "Make it mine": star count, moon glow colour, and whether Moy is home.
#
# Composition note: on the big desktop the 320x240 frame is COVER-cropped (~4x,
# the middle ~256x150 shows), so everything important sits in the centre band.

# MOY64 indices used (the moybyte site palette, mapped):
#   0 black    1 dark_blue(#1D2B53)   60 midnight navy   7 cream(#FFF1E8)
#   6 light_grey   13 lavender(#83769C)   10 yellow(#FFEC27)
NAVY = 60

# Moy, asleep (the boot-logo mascot art; '.'=sky, hex nibble = palette index).
MOY = (
    "................", "...0000000......", "..0ddddddd0.....", ".0d66ddddd0.....",
    ".0dddddddd0.....", ".0dddddddd0000..", ".0dd00d00ddddd0.", ".0dddddddddddd0.",
    ".0dddddddddddd0.", ".0dd0ddd0ddddd0.", ".0ddd000dddddd0.", ".0dddddddddddd0.",
    ".0dddddddddddd0.", "..022222222220..", "..02220002220...", "...000...000....",
)

stars = []
accent = 10
show_moy = 1


def _rng(seed):
    """A tiny deterministic LCG so the sky is a DESIGNED constellation -- the same
    every boot (rnd() would reshuffle it per session)."""
    state = [seed]

    def nxt(n):
        state[0] = (state[0] * 1103515245 + 12345) & 0x7FFFFFFF
        return state[0] % n
    return nxt


def _init():
    global stars, accent, show_moy
    n = int(cfg("stars", 70))
    r = _rng(20260708)
    stars = []
    for _i in range(n):
        # (x, y, kind): kind 0 = dim lavender speck, 1 = cream dot, 2 = sparkle
        stars.append((r(W), r(200), 2 if r(10) == 0 else (1 if r(3) == 0 else 0)))
    accent = col(cfg("accent", "yellow"))
    try:
        show_moy = int(cfg("moy", 1))
    except (TypeError, ValueError):
        show_moy = 1


def _draw():
    # -- sky: three clean stepped bands, darkest at the top -----------------------
    cls(0)
    rect(0, 64, W, 56, 1)
    rect(0, 120, W, 64, NAVY)
    # -- faint dot grid (the site's pattern texture) ----------------------------
    for gy in range(8, 184, 24):
        for gx in range(8, W, 24):
            pix(gx, gy, 1 if gy < 64 else 13)
    # -- stars (kept above the ground line) --------------------------------------
    for s in stars:
        x, y, kind = s[0], s[1] * 176 // 200, s[2]
        if kind == 0:
            pix(x, y, 13)
        elif kind == 1:
            pix(x, y, 7)
        else:                                  # a plus-shaped sparkle
            pix(x, y, 7)
            pix(x - 1, y, 6)
            pix(x + 1, y, 6)
            pix(x, y - 1, 6)
            pix(x, y + 1, 6)
    # -- crescent moon (accent colour); the bite re-fills with the band behind ----
    mx, my = 240, 84
    circ(mx, my, 14, accent)
    circ(mx - 7, my - 5, 11, 1)                # bite: the sky eats the crescent
    pix(mx - 9, my - 7, 13)                    # one lavender speck inside the dark
    # -- the ground: a lavender horizon over deep blue ----------------------------
    rect(0, 184, W, 2, 13)
    rect(0, 186, W, 54, 1)
    for x in range(4, W, 8):
        pix(x, 189, NAVY)
    # -- Moy, asleep on the ground, wordmark beside (all inside the centre band
    # -- the 4x cover crop shows ~y 45..195) --------------------------------------
    if show_moy:
        _moy(118, 152, 2)
        print("moy", 158, 168, 7)
        print("byte", 182, 168, accent)
        print("z", 148, 144, 6)
        print("z", 154, 136, 13)


def _moy(ox, oy, s):
    """Blit the mascot art at integer scale `s` with rect blocks (pure indexed
    draws -- no sprite sheet, so the wallpaper is a single self-contained file)."""
    hexd = "0123456789abcdef"
    for row in range(16):
        line = MOY[row]
        for cx in range(16):
            ch = line[cx]
            if ch == ".":
                continue
            rect(ox + cx * s, oy + row * s, s, s, hexd.index(ch))
