from moybyte import *

paddle = sprite("paddle", x=8, y=52, w=4, h=24)
ball = sprite("ball", x=64, y=64, w=4, h=4)
status = "ready"
announced = False


@radio.on_message
def receive(message):
    global status
    status = "rx"


@game.update
def update(dt):
    global announced, status

    if not announced:
        radio.send({"type": "join", "project": "radio_pong_stub"})
        announced = True
        status = "sent"

    if button("up"):
        paddle.y -= 2
    if button("down"):
        paddle.y += 2

    if button_pressed("a"):
        radio.send({"type": "paddle", "y": paddle.y})
        status = "hit"


@game.draw
def draw():
    clear()
    draw_sprite(paddle)
    draw_sprite(ball)
    text("Radio Pong", 4, 4)
    text(status, 4, 16)


run()
