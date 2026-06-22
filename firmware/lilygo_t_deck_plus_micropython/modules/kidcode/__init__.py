from . import runtime as _runtime
from .runtime import (
    Game,
    Sprite,
    bind_runtime,
    button,
    button_pressed,
    button_released,
    clear,
    draw_sprite,
    line,
    random_int,
    rect,
    run,
    sprite,
    text,
)

game = _runtime.game


def reset_api():
    global game
    _runtime.reset_api()
    game = _runtime.game


colors = {
    "black": 0,
    "white": 1,
    "green": 2,
    "yellow": 3,
    "red": 4,
    "blue": 5,
}

__all__ = [
    "Game",
    "Sprite",
    "bind_runtime",
    "button",
    "button_pressed",
    "button_released",
    "clear",
    "colors",
    "draw_sprite",
    "game",
    "line",
    "random_int",
    "rect",
    "reset_api",
    "run",
    "sprite",
    "text",
]
