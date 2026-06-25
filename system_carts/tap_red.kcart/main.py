# Tap Only Red -- a TOUCHSCREEN mini-game cartridge. Colored bubbles float up the
# screen. TAP the RED ones to score; tapping any other color costs a miss. Miss
# too many and the round resets. Uses the touch() api (mouse = touch on the host
# sim, GT911 on device). With no taps an auto-player pops red bubbles (attract
# mode) so the simulator GIF stays lively.

score = 0
misses = 0
bubbles = []      # [x, y, r, color_index, is_red]
t = 0.0
flash = 0.0       # >0 = recent good tap glow ; <0 = recent bad tap (red flash)
auto = 0.0
spawn_t = 0.0

COLORS = ["orange", "yellow", "green", "blue", "pink", "indigo"]   # the non-red lures
MAX_MISS = 5


def _init():
    global score, misses, bubbles, t, flash, auto, spawn_t
    score = 0
    misses = 0
    bubbles = []
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


def _pop(b, good):
    global score, misses, flash
    if good:
        score += 1
        flash = 0.25
    else:
        misses += 1
        flash = -0.25
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
    global t, flash, auto, spawn_t, misses
    t += dt
    if flash > 0:
        flash = max(0.0, flash - dt)
    elif flash < 0:
        flash = min(0.0, flash + dt)
    rise = float(cfg("rise", 45))
    for b in bubbles:
        b[1] -= rise * dt
    # off the top: red escaped = a miss; lures just vanish
    keep = []
    for b in bubbles:
        if b[1] + b[2] < 0:
            if b[4]:
                misses += 1
        else:
            keep.append(b)
    bubbles[:] = keep
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
    # attract mode: no taps -> auto-pop the nearest red bubble now and then
    auto += dt
    if not tapped and auto > 0.7:
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
    print("TAP THE RED", 8, 8, col("red"), 2)
    print("SCORE " + str(score), 8, 26, col("white"), 1)
    # miss pips
    for i in range(MAX_MISS):
        x = W - 14 - i * 12
        c = "red" if i < misses else "dark_grey"
        rect(x, 10, 9, 9, col(c))
        rectb(x, 10, 9, 9, col("white"))
    print("MISS", W - 14 - (MAX_MISS - 1) * 12, 22, col("light_grey"), 1)
