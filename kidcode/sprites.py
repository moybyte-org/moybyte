"""Sprite primitives."""


class Sprite:
    def __init__(self, name, x=0, y=0, w=8, h=8):
        self.name = name
        self.x = x
        self.y = y
        self.w = w
        self.h = h
        self.visible = True
        self.frame = 0
        self.flip_x = False
        self.flip_y = False

    def move_to(self, x, y):
        self.x = x
        self.y = y

    def touching(self, other):
        if not self.visible or not other.visible:
            return False
        return (
            self.x < other.x + other.w
            and self.x + self.w > other.x
            and self.y < other.y + other.h
            and self.y + self.h > other.y
        )
