from kidcode import *

player = None
coin = None
score = 0


def setup():
    global player, coin, score
    player = sprite("player", x=60, y=60)
    coin = sprite("coin", x=24, y=24)
    score = 0


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

    if player.x < 2:
        player.x = 2
    if player.x > 118:
        player.x = 118
    if player.y < 2:
        player.y = 2
    if player.y > 118:
        player.y = 118

    if player.touching(coin):
        score += 1
        coin.x = random_int(8, 112)
        coin.y = random_int(8, 112)


def draw():
    clear(0)
    draw_sprite(player)
    draw_sprite(coin)
    text("score " + str(score), 4, 4, 1)
