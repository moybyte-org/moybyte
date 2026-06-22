p = sprite("player", x=40, y=40)


def update(dt):
    if button("left"):
        p.x -= 2
    if button("right"):
        p.x += 2
    if button("up"):
        p.y -= 2
    if button("down"):
        p.y += 2

    if p.x < 0:
        p.x = 0
    if p.x > 120:
        p.x = 120
    if p.y < 14:
        p.y = 14
    if p.y > 120:
        p.y = 120


def draw():
    clear(0)
    text("SD project", 4, 4, 1)
    draw_sprite(p)
