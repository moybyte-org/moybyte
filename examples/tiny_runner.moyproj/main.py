from moybyte import *

player = sprite("player", x=60, y=60, w=8, h=8)
coin = sprite("coin", x=24, y=24, w=8, h=8)
score = 0


@game.update
def update(dt):
    global score

    if button("left"):
        player.x -= 2
    if button("right"):
        player.x += 2
    if button("up"):
        player.y -= 2
    if button("down"):
        player.y += 2

    if player.touching(coin):
        score += 1
        coin.move_to(random_x(), random_y())
        beep()


@game.draw
def draw():
    clear(0)
    rect(player.x, player.y, player.w, player.h, color=3)
    rect(coin.x, coin.y, coin.w, coin.h, color=5)
    text("Score: " + str(score), 4, 4)


run()
