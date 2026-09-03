# Tiny Runner -- an endless-runner game. The hero runs automatically; you only
# JUMP. Press UP / A / RUN to hop over the cactus obstacles. The longer you
# survive the FASTER it gets, so your score keeps climbing the pressure. Hitting a
# cactus ends the run (your BEST is kept). Pure indexed canvas + btn input.
#
# AUTOPLAY (in "Make it mine") is OFF by default -- YOU jump. Flip it on to watch
# the console auto-jump forever (attract mode).

GROUND = 26          # ground height from the bottom
HERO_X = 40
HERO_W = 16
HERO_H = 20
# A run picks its obstacles from these four: (sheet tile, width, height). Each is
# a 1x2 tile span drawn at 2x whose painted art is exactly w x h, so the picture
# IS the collision box.
CACTI = ((2, 10, 14), (3, 12, 20), (4, 12, 26), (5, 14, 18))
HILL = 32            # sheet tiles: a 4x2 span, 26x13 of art = a 52x26 hill at 2x

hero_y = 0.0         # offset above the ground (0 = standing)
vel = 0.0
score = 0.0
best = 0
obs = []             # obstacles: [x, w, h, tile]
spawn_x = 0.0
t = 0.0
hero = 0             # the chosen hero sprite tile (0 or 1 -- editable in paint)
dust = []            # jump/run dust: [x, y, vx, vy, life]
flash = 0.0          # white near-miss/landing pop, decays
squash = 0.0         # >0 = hero landing squash, decays

# The hero runs from the cart's sprite sheet (sprites.moygfx): tile 0 and tile 1.
# Pick one in "Make it mine" and edit it in the paint editor.


def _ground_y():
    return H - GROUND


def _hero_tile():
    h = cfg("hero", 0)               # tile id (tolerate a stale color-name config)
    try:
        return int(h)
    except (TypeError, ValueError):
        return 0


def _speed():
    # speed ramps up the longer you run, so the game gets harder over time
    return float(cfg("speed", 130)) * (1.0 + score * 0.004)


def _init():
    global hero_y, vel, score, obs, spawn_x, t, hero, dust, flash, squash
    hero_y = 0.0
    vel = 0.0
    score = 0.0
    obs = []
    spawn_x = W
    t = 0.0
    dust = []
    flash = 0.0
    squash = 0.0
    hero = _hero_tile()


def _spawn():
    global spawn_x
    c = CACTI[int(rnd(len(CACTI)))]
    obs.append([spawn_x, c[1], c[2], c[0]])
    spawn_x = W + 40 + rnd(120)


def _puff(x, y, n):
    for _i in range(n):
        dust.append([x, y, (rnd(2.0) - 1.0) * 30 - 40, -rnd(40), 0.4])


def _jump():
    global vel
    if hero_y <= 0.1:                 # only when on the ground
        vel = -float(cfg("jump", 220))
        _puff(HERO_X + HERO_W // 2, _ground_y(), 4)


def _reset_run():
    global score, best, obs, hero_y, vel, flash
    if int(score) > best:
        best = int(score)
    score = 0.0
    obs = []
    hero_y = 0.0
    vel = 0.0
    flash = 0.3


def _danger():
    # nearest obstacle just ahead of the hero (for attract-mode auto-jump)
    for o in obs:
        if o[0] + o[1] > HERO_X and o[0] < HERO_X + 60:
            return True
    return False


def _update(dt):
    global hero_y, vel, score, spawn_x, t, flash, squash
    t += dt
    spd = _speed()
    score += spd * dt * 0.1
    if flash > 0.0:
        flash = max(0.0, flash - dt)
    if squash > 0.0:
        squash = max(0.0, squash - dt * 6.0)
    # gravity / jump arc
    was_air = hero_y > 0.1
    vel += 700.0 * dt
    hero_y -= vel * dt
    if hero_y < 0.0:
        hero_y = 0.0
        if was_air:                   # just landed: squash + a little dust
            squash = 1.0
            _puff(HERO_X + HERO_W // 2, _ground_y(), 3)
        vel = 0.0
    # input: jump on UP / A / RUN; with AUTOPLAY on, auto-jump when danger is near
    if btn("up") or btn("a") or btn("run"):
        _jump()
    elif cfg("autoplay", 0) and _danger():
        _jump()
    # move + recycle obstacles
    spawn_x -= spd * dt
    for o in obs:
        o[0] -= spd * dt
    while obs and obs[0][0] + obs[0][1] < 0:
        obs.pop(0)
    if spawn_x <= W:
        _spawn()
    # dust particles
    keep = []
    for p in dust:
        p[4] -= dt
        if p[4] > 0.0:
            p[0] += p[2] * dt
            p[1] += p[3] * dt
            p[3] += 200.0 * dt
            keep.append(p)
    dust[:] = keep
    # collision -> reset
    gy = _ground_y()
    hx0 = HERO_X
    hx1 = HERO_X + HERO_W
    hy0 = gy - HERO_H - int(hero_y)
    hy1 = gy - int(hero_y)
    for o in obs:
        ox0 = int(o[0])
        ox1 = int(o[0]) + o[1]
        oy0 = gy - o[2]
        if hx1 > ox0 and hx0 < ox1 and hy1 > oy0 and hy0 < gy:
            _reset_run()
            break


def _draw():
    cls(col("white") if flash > 0.0 else col(cfg("sky", "dark_blue")))
    gy = _ground_y()
    rect(0, gy, W, GROUND, col("brown"))
    rect(0, gy, W, 2, col("dark_green"))
    # parallax hills (cheap moving scenery)
    hx = int(-(t * 20) % 120)
    for i in range(-1, W // 120 + 2):
        spr(HILL, hx + i * 120 + 34, gy - 26, 0, 2, 0, 4, 2)
    # ground speckle that scrolls (sells the run speed)
    sx = int(-(t * 120) % 24)
    for i in range(-1, W // 24 + 2):
        pix(sx + i * 24, gy + 8, col("dark_grey"))
    # dust
    for p in dust:
        pix(int(p[0]), int(p[1]), col("light_grey"))
    # obstacles (cacti)
    for o in obs:
        spr(o[3], int(o[0]), gy - o[2], 0, 2, 0, 1, 2)
    # hero (8x8 tile at 2x = 16px, from the cart sheet); squashed flat on landing
    hsc = 2
    hy = gy - HERO_H - int(hero_y)
    if squash > 0.0:
        hy = gy - 13                  # sit lower so the squash reads as a flatten
    spr(hero, HERO_X, hy, 0, hsc)
    print("SCORE " + str(int(score)), 8, 8, col("white"), 2)
    print("BEST " + str(best), 8, 24, col("yellow"), 1)
    print("UP=JUMP", W - 60, 8, col("light_grey"), 1)
