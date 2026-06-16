"""Game callback registration."""


class Game:
    def __init__(self):
        self.update_fn = None
        self.draw_fn = None
        self.button_handlers = {}

    def reset(self):
        self.update_fn = None
        self.draw_fn = None
        self.button_handlers = {}

    def update(self, fn):
        self.update_fn = fn
        return fn

    def draw(self, fn):
        self.draw_fn = fn
        return fn

    def on_button(self, name):
        def decorate(fn):
            self.button_handlers[name] = fn
            return fn

        return decorate


game = Game()
