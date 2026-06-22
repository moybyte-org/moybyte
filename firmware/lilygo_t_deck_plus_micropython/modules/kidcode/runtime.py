try:
    import random
except ImportError:
    random = None


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

    def touching(self, other):
        if not self.visible or not other.visible:
            return False
        return not (
            self.x + self.w <= other.x
            or other.x + other.w <= self.x
            or self.y + self.h <= other.y
            or other.y + other.h <= self.y
        )

    def move_to(self, x, y):
        self.x = x
        self.y = y


class Game:
    def __init__(self):
        self.update_handler = None
        self.draw_handler = None
        self.button_handlers = {}

    def update(self, fn):
        self.update_handler = fn
        return fn

    def draw(self, fn):
        self.draw_handler = fn
        return fn

    def on_button(self, name):
        def decorate(fn):
            self.button_handlers[name] = fn
            return fn

        return decorate


class CanvasCommands:
    def __init__(self):
        self.commands = []
        self._fbuf = None
        self._ctbl = None
        self._stbl = None
        self._sdef = 0

    def bind_framebuf(self, fbuf, color_table_565, sprite_table_565, default_sprite_565):
        """Bind a framebuffer for direct rendering, bypassing the command list."""
        self._fbuf = fbuf
        self._ctbl = color_table_565
        self._stbl = sprite_table_565
        self._sdef = default_sprite_565

    def clear(self, color=0):
        fb = self._fbuf
        if fb is not None:
            c = self._ctbl.get(color, 0xFFFF) if isinstance(color, int) else 0xFFFF
            fb.fill(c)
            self.commands = []
        else:
            self.commands = [{"type": "clear", "color": color}]

    def rect(self, x, y, w, h, color=1, fill=True):
        fb = self._fbuf
        if fb is not None:
            c = self._ctbl.get(color, 0xFFFF) if isinstance(color, int) else 0xFFFF
            if fill:
                fb.fill_rect(int(x), int(y), int(w), int(h), c)
            else:
                fb.rect(int(x), int(y), int(w), int(h), c)
        else:
            self.commands.append(
                {"type": "rect", "x": x, "y": y, "w": w, "h": h, "color": color, "fill": fill}
            )

    def text(self, value, x, y, color=1):
        fb = self._fbuf
        if fb is not None:
            c = self._ctbl.get(color, 0xFFFF) if isinstance(color, int) else 0xFFFF
            fb.text(str(value), int(x), int(y), c)
        else:
            self.commands.append({"type": "text", "value": str(value), "x": x, "y": y, "color": color})

    def line(self, x1, y1, x2, y2, color=1):
        fb = self._fbuf
        if fb is not None:
            c = self._ctbl.get(color, 0xFFFF) if isinstance(color, int) else 0xFFFF
            fb.line(int(x1), int(y1), int(x2), int(y2), c)
        else:
            self.commands.append(
                {"type": "line", "x1": x1, "y1": y1, "x2": x2, "y2": y2, "color": color}
            )

    def draw_sprite(self, item):
        if not item.visible:
            return
        fb = self._fbuf
        if fb is not None:
            c = self._stbl.get(item.name, self._sdef)
            fb.fill_rect(int(item.x), int(item.y), int(item.w), int(item.h), c)
        else:
            self.commands.append(
                {
                    "type": "sprite",
                    "name": item.name,
                    "x": item.x,
                    "y": item.y,
                    "w": item.w,
                    "h": item.h,
                    "frame": item.frame,
                }
            )


class Runtime:
    def __init__(self, input_state):
        self.input = input_state
        self.canvas = CanvasCommands()


game = Game()
_runtime = None


def reset_api():
    global game
    game = Game()


def bind_runtime(runtime):
    global _runtime
    _runtime = runtime


def _need_runtime():
    if _runtime is None:
        raise RuntimeError("KidCode runtime is not bound")
    return _runtime


def sprite(name, x=0, y=0, w=8, h=8):
    return Sprite(name, x, y, w, h)


def clear(color=0):
    _need_runtime().canvas.clear(color)


def rect(x, y, w, h, color=1, fill=True):
    _need_runtime().canvas.rect(x, y, w, h, color, fill)


def text(value, x, y, color=1):
    _need_runtime().canvas.text(value, x, y, color)


def line(x1, y1, x2, y2, color=1):
    _need_runtime().canvas.line(x1, y1, x2, y2, color)


def draw_sprite(item):
    _need_runtime().canvas.draw_sprite(item)


def button(name):
    return _need_runtime().input.held(name)


def button_pressed(name):
    return _need_runtime().input.pressed(name)


def button_released(name):
    return _need_runtime().input.released(name)


def random_int(low, high):
    if random is None:
        return low
    return random.randint(low, high)


def run(update=None, draw=None):
    if update is not None:
        game.update(update)
    if draw is not None:
        game.draw(draw)

