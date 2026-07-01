"""Simple logical drawing command buffer."""


class Screen:
    def __init__(self, width=128, height=128):
        self.width = width
        self.height = height
        self.commands = []

    def clear(self, color=0):
        self.commands = [{"type": "clear", "color": color}]

    def text(self, value, x, y, color=1):
        self.commands.append({"type": "text", "value": str(value), "x": x, "y": y, "color": color})

    def rect(self, x, y, w, h, color=1, fill=True):
        self.commands.append(
            {"type": "rect", "x": x, "y": y, "w": w, "h": h, "color": color, "fill": fill}
        )

    def circle(self, x, y, r, color=1, fill=False):
        self.commands.append(
            {"type": "circle", "x": x, "y": y, "r": r, "color": color, "fill": fill}
        )

    def line(self, x1, y1, x2, y2, color=1):
        self.commands.append(
            {"type": "line", "x1": x1, "y1": y1, "x2": x2, "y2": y2, "color": color}
        )

    def draw_sprite(self, item):
        if item.visible:
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
