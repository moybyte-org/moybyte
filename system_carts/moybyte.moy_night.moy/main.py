# Moy Night -- the moybyte desktop wallpaper (brand colorway), drawn per glass.
#
# A STATIC night scene: a black sky with a deterministic starfield, a crescent
# moon under the bar at the top right, a dithered ground along the foot, and
# Moy asleep on it. No _update, so the backdrop never forces a repaint (idle
# stays free). "Make it mine": star count, moon glow colour, whether Moy is home.
#
# Per glass (manifest "glass": true, docs/theming_2026-09.md 4.9): W and H are
# the glass's, QUIET is the shell's quiet rectangles (its bars, the home's text,
# the desk's icon column), and SUBJECT is where Moy sleeps, which a new window
# keeps clear of. Moy sleeps in the bottom-left corner, or the bottom-right
# where something quiet is on the left.

# MOY64 indices: 0 black, 6 light grey, 7 cream, 13 lavender, 60 midnight navy.
NAVY = 60

# Moy, asleep (the boot-logo mascot art; '.'=sky, hex nibble = palette index).
MOY = (
    "................", "...0000000......", "..0ddddddd0.....", ".0d66ddddd0.....",
    ".0dddddddd0.....", ".0dddddddd0000..", ".0dd00d00ddddd0.", ".0dddddddddddd0.",
    ".0dddddddddddd0.", ".0dd0ddd0ddddd0.", ".0ddd000dddddd0.", ".0dddddddddddd0.",
    ".0dddddddddddd0.", "..022222222220..", "..02220002220...", "...000...000....",
)

# The ground's dither, sparse to dense (fillp: a set bit is a hole).
GROUND = (0x7FDF, 0x5F5F, 0x5A5A, 0xA050)

stars = []
accent = 10
show_moy = 1
QUIET = ()                 # the shell's, on a glass; nothing where it runs as a cart
SUBJECT = None
spot = None                # (s, top, floor, gy, gh, Moy's x, Moy's y): _place's


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
    # (x, y) in thousandths of the sky, and the kind: 0 a dim speck, 1 a cream
    # dot, 2 a sparkle. A bigger glass shows more of the same sky.
    stars = []
    for _i in range(n * 4):
        stars.append((r(1000), r(1000), 2 if r(10) == 0 else (1 if r(3) == 0 else 0)))
    accent = col(cfg("accent", "yellow"))
    try:
        show_moy = int(cfg("moy", 1))
    except (TypeError, ValueError):
        show_moy = 1
    _place()                   # SUBJECT is known before the first frame


def _hits(x, y, w, h):
    for q in QUIET:
        if x < q[0] + q[2] and q[0] < x + w and y < q[1] + q[3] and q[1] < y + h:
            return True
    return False


def _edges():
    """(top, floor): the sky's top under any bar along the top edge, and the
    ground's floor over anything quiet along the foot (a bar, the hint strip)."""
    top, floor = 0, H
    for q in QUIET:
        if q[2] * 2 < W:
            continue
        if q[1] <= 0:
            top = max(top, q[1] + q[3])
        elif q[1] + q[3] >= H:
            floor = min(floor, q[1])
    return top, floor


def _place():
    """Lay the scene out on this glass around QUIET: Moy's pixel `s` (2 on 240
    rows, 4 on 600), the sky's top and the ground's floor, the ground band, and
    where Moy sleeps -- bottom left where nothing quiet is, else bottom right.
    SUBJECT is Moy and its zZ, or None while Moy is away."""
    global SUBJECT, spot
    s = max(1, (H + 60) // 150)
    top, floor = _edges()
    gh = max(8, min(H // 8, H * H // 4800))   # the ground band: 12 rows on 240, 75 on 600
    gy = floor - (H // 24 if floor == H else s) - gh
    mw = 16 * s
    my = gy + gh - 15 * s                 # standing in the band, feet on its rim
    top_z = my - 6 * s                    # the zZ rising over its head
    m = 6 * s
    for x in (m, W - m - mw):
        if not _hits(x, top_z, mw, my + 16 * s - top_z):
            break
    spot = (s, top, floor, gy, gh, x, my)
    SUBJECT = (x, top_z, mw, my + 16 * s - top_z) if show_moy else None


def _draw():
    _place()
    s, top, floor, gy, gh, x, my = spot
    cls(0)
    # -- stars, in the sky above the ground ---------------------------------
    sky = max(1, gy - top)
    count = len(stars) * W * H // (4 * 320 * 240)
    for i in range(min(len(stars), count)):
        sx, sy, kind = stars[i]
        px, py = sx * W // 1000, top + sy * sky // 1000
        if kind == 0:
            pix(px, py, 13)
        elif kind == 1:
            pix(px, py, 7)
        else:                              # a plus-shaped sparkle
            pix(px, py, 7)
            pix(px - 1, py, 6)
            pix(px + 1, py, 6)
            pix(px, py - 1, 6)
            pix(px, py + 1, 6)
    # -- the crescent moon, top right under the bar -------------------------
    r = 6 * s
    mx, mcy = W - 12 * s - r // 2, top + 10 * s + r // 2
    circ(mx, mcy, r, accent)
    circ(mx - r // 2, mcy - r // 3, r * 4 // 5, 0)   # the sky eats the crescent
    # -- the ground: a dither thickening towards a dotted rim ---------------
    band = gh // len(GROUND)
    for i in range(len(GROUND)):
        fillp(GROUND[i])
        rect(0, gy + i * band, W, band, NAVY)
    fillp(0x5F5F)
    rect(0, gy + band * len(GROUND), W, max(1, s // 2), NAVY)
    fillp()
    # -- Moy, asleep on the ground ------------------------------------------
    if not show_moy:
        return
    _moy(x, my, s)
    u = max(1, s // 2)
    _z(x + 10 * s, my - 3 * s, u, 7)
    _z(x + 13 * s, my - 6 * s, u, 13)


def _z(x, y, u, c):
    """A sleeping 'z' in rects (cart text would scale with the font)."""
    rect(x, y, 4 * u, u, c)
    rect(x + 2 * u, y + u, u, u, c)
    rect(x + u, y + 2 * u, u, u, c)
    rect(x, y + 3 * u, 4 * u, u, c)


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
