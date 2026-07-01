# Tap Only Red -- a TOUCHSCREEN mini-game. Colored bubbles float up the screen.
# TAP the RED ones to score (consecutive reds build a COMBO); tapping any other
# color, or letting a red float off the top, costs a MISS. Miss too many and the
# round resets. The bubbles speed up the higher you score. Uses the touch() api
# (mouse = touch on the host sim, GT911 on device).
#
# AUTOPLAY (in "Make it mine") is OFF by default -- YOU tap. Flip it on to watch
# an auto-player pop the reds (attract mode).

score = 0
combo = 0
misses = 0
bubbles = []      # [x, y, r, color_index, is_red]
pops = []         # pop particles: [x, y, vx, vy, life, color]
t = 0.0
flash = 0.0       # >0 = recent good tap glow ; <0 = recent bad tap (red flash)
auto = 0.0
spawn_t = 0.0

COLORS = ["orange", "yellow", "green", "blue", "pink", "indigo"]   # the non-red lures
MAX_MISS = 5


def _init():
    global score, combo, misses, bubbles, pops, t, flash, auto, spawn_t
    score = 0
    combo = 0
    misses = 0
    bubbles = []
    pops = []
    t = 0.0
    flash = 0.0
    auto = 0.0
    spawn_t = 0.0
    for _i in range(4):
        _spawn()


def _spawn():
    red = rnd(1.0) < float(cfg("red_share", 40)) / 100.0
    ci = col("red") if red else col(COLORS[int(rnd(len(COLORS)))])
    r = 12 + int(rnd(8))
    bubbles.append([rnd(W - 2 * r) + r, H + r, r, ci, red])


def _burst(x, y, ci):
    for _i in range(7):
        pops.append([x, y, (rnd(2.0) - 1.0) * 90, (rnd(2.0) - 1.0) * 90, 0.4, ci])


def _pop(b, good):
    global score, combo, misses, flash
    if good:
        score += 1 + combo // 4      # combo bonus: every 4 reds in a row scores more
        combo += 1
        flash = 0.25
    else:
        misses += 1
        combo = 0
        flash = -0.25
    _burst(b[0], b[1], b[3])
    bubbles.remove(b)


def _hit(x, y):
    # topmost bubble under (x, y); pop it (good if red, else a miss)
    for b in reversed(bubbles):
        dx = x - b[0]
        dy = y - b[1]
        if dx * dx + dy * dy <= (b[2] + 4) * (b[2] + 4):
            _pop(b, b[4])
            return True
    return False


def _nearest_red():
    best = None
    for b in bubbles:
        if b[4] and (best is None or b[1] < best[1]):
            best = b
    return best


def _update(dt):
    global t, flash, auto, spawn_t, misses, combo
    t += dt
    if flash > 0:
        flash = max(0.0, flash - dt)
    elif flash < 0:
        flash = min(0.0, flash + dt)
    # rise speed ramps a little with score so it gets harder over a good run
    rise = float(cfg("rise", 45)) * (1.0 + score * 0.01)
    for b in bubbles:
        b[1] -= rise * dt
    # off the top: red escaped = a miss (and breaks the combo); lures just vanish
    keep = []
    for b in bubbles:
        if b[1] + b[2] < 0:
            if b[4]:
                misses += 1
                combo = 0
        else:
            keep.append(b)
    bubbles[:] = keep
    # pop particles
    pk = []
    for p in pops:
        p[4] -= dt
        if p[4] > 0.0:
            p[0] += p[2] * dt
            p[1] += p[3] * dt
            pk.append(p)
    pops[:] = pk
    spawn_t += dt
    if spawn_t > 0.6 and len(bubbles) < int(cfg("max_bubbles", 8)):
        spawn_t = 0.0
        _spawn()
    # real touch input
    tapped = False
    tp = touch()
    if tp is not None and tp[2]:
        _hit(tp[0], tp[1])
        tapped = True
    # AUTOPLAY: no taps -> auto-pop the nearest red bubble now and then
    auto += dt
    if cfg("autoplay", 0) and not tapped and auto > 0.7:
        auto = 0.0
        b = _nearest_red()
        if b is not None:
            _pop(b, True)
    if misses >= MAX_MISS:
        _init()


def _draw():
    bg = "dark_blue"
    if flash > 0:
        bg = "dark_green"
    elif flash < 0:
        bg = "dark_purple"
    cls(col(bg))
    for b in bubbles:
        circ(int(b[0]), int(b[1]), b[2], b[3])
        circb(int(b[0]), int(b[1]), b[2], col("white"))
        if b[4]:                                   # a little shine marks the reds
            pix(int(b[0]) - b[2] // 3, int(b[1]) - b[2] // 3, col("white"))
    for p in pops:
        pix(int(p[0]), int(p[1]), p[5])
    print("TAP THE RED", 8, 8, col("red"), 2)
    print("SCORE " + str(score), 8, 26, col("white"), 1)
    if combo >= 2:
        print("X" + str(combo), 70, 26, col("yellow"), 1)
    # miss pips
    for i in range(MAX_MISS):
        x = W - 14 - i * 12
        c = "red" if i < misses else "dark_grey"
        rect(x, 10, 9, 9, col(c))
        rectb(x, 10, 9, 9, col("white"))
    print("MISS", W - 14 - (MAX_MISS - 1) * 12, 22, col("light_grey"), 1)
