# Generated from Moybyte Blocks. Edits may be overwritten.
from moybyte import *

score = 0

player = sprite("player", x=60, y=60, w=8, h=8)
coin = sprite("coin", x=30, y=30, w=8, h=8)

# Update script
def update(dt):
    global score
    if button("right"):
        player.x += 2
    if player.touching(coin):
        score += 1
        beep()

# Draw script
def draw():
    clear(0)
    draw_sprite(player)
    draw_sprite(coin)
    text(f"Score: {score}", 4, 4)

run(update=update, draw=draw)
