# Tiny Runner -- an endless-runner game cartridge. The hero runs automatically;
# you only JUMP. Press UP / A / RUN to hop over the cactus obstacles. Hitting one
# resets the run. With no input it auto-jumps (attract mode) so the simulator GIF
# stays lively. Pure indexed canvas + btn input -- portable host<->device.

GROUND = 26          # ground height from the bottom
HERO_X = 40
HERO_W = 16
HERO_H = 20

hero_y = 0.0         # offset above the ground (0 = standing)
vel = 0.0
score = 0.0
best = 0
obs = []             # obstacles: [x, w, h]
spawn_x = 0.0
t = 0.0
hero = 0             # the chosen hero sprite tile (0 or 1 -- editable in paint)

# The hero runs from the cart's sprite sheet (sprites.kgfx): tile 0 and tile 1.
# Pick one in "Make it mine" and edit it in the paint editor.


def _ground_y():
    return H - GROUND


def _hero_tile():
    h = cfg("hero", 0)               # tile id (tolerate a stale color-name config)
    try:
        return int(h)
    except (TypeError, ValueError):
        return 0


def _init():
    global hero_y, vel, score, obs, spawn_x, t, hero
    hero_y = 0.0
    vel = 0.0
    score = 0.0
    obs = []
    spawn_x = W
    t = 0.0
    hero = _hero_tile()


def _spawn():
    global spawn_x
    h = 12 + int(rnd(16))
    obs.append([spawn_x, 10 + int(rnd(6)), h])
    spawn_x = W + 40 + rnd(120)


def _jump():
    global vel
    if hero_y <= 0.1:                 # only when on the ground
        vel = -float(cfg("jump", 220))


def _reset_run():
    global score, best, obs, hero_y, vel
    if int(score) > best:
        best = int(score)
    score = 0.0
    obs = []
    hero_y = 0.0
    vel = 0.0


def _danger():
    # nearest obstacle just ahead of the hero (for attract-mode auto-jump)
    for o in obs:
        if o[0] + o[1] > HERO_X and o[0] < HERO_X + 60:
            return True
    return False


def _update(dt):
    global hero_y, vel, score, spawn_x, t
    t += dt
    spd = float(cfg("speed", 130))
    score += spd * dt * 0.1
    # gravity / jump arc
    vel += 700.0 * dt
    hero_y -= vel * dt
    if hero_y < 0.0:
        hero_y = 0.0
        vel = 0.0
    # input: jump on UP / A / RUN; otherwise auto-jump when danger is near
    if btn("up") or btn("a") or btn("run"):
        _jump()
    elif _danger():
        _jump()
    # move + recycle obstacles
    spawn_x -= spd * dt
    for o in obs:
        o[0] -= spd * dt
    while obs and obs[0][0] + obs[0][1] < 0:
        obs.pop(0)
    if spawn_x <= W:
        _spawn()
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
    cls(col(cfg("sky", "dark_blue")))
    gy = _ground_y()
    rect(0, gy, W, GROUND, col("brown"))
    rect(0, gy, W, 2, col("dark_green"))
    # parallax hills (cheap moving scenery)
    hx = int(-(t * 20) % 120)
    for i in range(-1, W // 120 + 2):
        circ(hx + i * 120 + 60, gy, 26, col("dark_green"))
    # obstacles (cacti)
    for o in obs:
        rect(int(o[0]), gy - o[2], o[1], o[2], col("green"))
        rectb(int(o[0]), gy - o[2], o[1], o[2], col("dark_green"))
    # hero (8x8 tile at 2x = 16px, from the cart sheet)
    spr(hero, HERO_X, gy - HERO_H - int(hero_y), 0, 2)
    print("SCORE " + str(int(score)), 8, 8, col("white"), 2)
    print("BEST " + str(best), 8, 24, col("yellow"), 1)
    print("UP=JUMP", W - 60, 8, col("light_grey"), 1)
