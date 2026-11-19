# Made with Moybyte blocks. Edit in the block editor (or graduate to code).

score = 0
misses = 0

def _ay(a):
    return a.y if a else 0

def _chay(a, v):
    if a is not None:
        a.y = a.y + v

def _taphit(a):
    t = touch()
    if not a or not t or not t[2]:
        return False
    s = 8 * max(1, a.flags.get('size', 100) // 100)
    return a.x <= t[0] < a.x + s and a.y <= t[1] < a.y + s

def _setsize(a, n):
    if a is not None:
        a.flags['size'] = n
def _chsize(a, n):
    if a is not None:
        a.flags['size'] = a.flags.get('size', 100) + n
def _getsize(a):
    return a.flags.get('size', 100) if a else 100


def _init():
    global score, misses
    score = 0
    misses = 0
    for _self in actors("red"):
        _setsize(_self, 400)
    for _self in actors("lure"):
        _setsize(_self, 400)


def _update(dt):
    global score, misses
    if (misses > 4):
        score = 0
        misses = 0
    for _self in actors("red"):
        _chay(_self, -2)
        if (_ay(_self) < -32):
            misses = misses + (1)
            move_actor_to(_self, int(rnd(288)), 240)
    for _self in actors("red"):
        if _taphit(_self):
            score = score + (1)
            beep(880)
            move_actor_to(_self, int(rnd(288)), 240)
    for _self in actors("lure"):
        _chay(_self, -2)
        if (_ay(_self) < -32):
            move_actor_to(_self, int(rnd(288)), 240)
    for _self in actors("lure"):
        if _taphit(_self):
            misses = misses + (1)
            beep(220)
            move_actor_to(_self, int(rnd(288)), 240)


def _draw():
    cls(col("black"))
    draw_scene()
    print("SCORE", 8, 8, col("white"))
    print(score, 56, 8, col("yellow"))
    print("MISSES", 224, 8, col("white"))
    print(misses, 280, 8, col("red"))
    print("TAP ONLY THE RED", 96, 224, col("light_grey"))
