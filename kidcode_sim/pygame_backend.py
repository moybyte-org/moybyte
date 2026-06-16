"""Optional pygame simulator backend."""

from kidcode.manifest import Manifest, resolve_project_file
from kidcode.runtime import RuntimeContext

PALETTE = [
    (0, 0, 0),
    (255, 255, 255),
    (224, 64, 64),
    (64, 192, 96),
    (80, 120, 224),
    (240, 210, 80),
    (220, 96, 180),
    (80, 210, 220),
    (80, 80, 80),
    (160, 160, 160),
    (120, 80, 48),
    (255, 150, 80),
    (80, 48, 120),
    (48, 120, 96),
    (160, 48, 80),
    (220, 220, 180),
]


class PygameBackend:
    def __init__(self, canvas, scale=4):
        import pygame

        self.pygame = pygame
        pygame.init()
        self.canvas = canvas
        self.scale = scale
        self.key_map = {
            pygame.K_UP: "up",
            pygame.K_DOWN: "down",
            pygame.K_LEFT: "left",
            pygame.K_RIGHT: "right",
            pygame.K_z: "a",
            pygame.K_j: "a",
            pygame.K_x: "b",
            pygame.K_k: "b",
            pygame.K_a: "x",
            pygame.K_u: "x",
            pygame.K_s: "y",
            pygame.K_i: "y",
            pygame.K_RETURN: "run",
            pygame.K_ESCAPE: "stop",
        }
        self.surface = pygame.display.set_mode((canvas.width * scale, canvas.height * scale))
        pygame.display.set_caption("KidCode")
        self.font = pygame.font.Font(None, 8 * scale)
        self.clock = pygame.time.Clock()

    def pump_input(self, input_state):
        pygame = self.pygame
        running = True
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                running = False
            elif event.type in (pygame.KEYDOWN, pygame.KEYUP):
                button = self.key_map.get(event.key)
                if button is not None:
                    input_state.set_button(button, event.type == pygame.KEYDOWN)
        return running

    def present(self, commands):
        pygame = self.pygame
        scale = self.scale
        for command in commands:
            kind = command["type"]
            if kind == "clear":
                self.surface.fill(PALETTE[command["color"] % len(PALETTE)])
            elif kind in ("rect", "sprite"):
                color = command.get("color", 3)
                if kind == "sprite":
                    color = 3
                rect = pygame.Rect(
                    int(command["x"] * scale),
                    int(command["y"] * scale),
                    int(command["w"] * scale),
                    int(command["h"] * scale),
                )
                if command.get("fill", True):
                    pygame.draw.rect(self.surface, PALETTE[color % len(PALETTE)], rect)
                else:
                    pygame.draw.rect(self.surface, PALETTE[color % len(PALETTE)], rect, scale)
            elif kind == "text":
                image = self.font.render(command["value"], False, PALETTE[command["color"] % len(PALETTE)])
                self.surface.blit(image, (int(command["x"] * scale), int(command["y"] * scale)))
            elif kind == "line":
                pygame.draw.line(
                    self.surface,
                    PALETTE[command["color"] % len(PALETTE)],
                    (int(command["x1"] * scale), int(command["y1"] * scale)),
                    (int(command["x2"] * scale), int(command["y2"] * scale)),
                    max(1, scale),
                )
            elif kind == "circle":
                width = 0 if command.get("fill", False) else max(1, scale)
                pygame.draw.circle(
                    self.surface,
                    PALETTE[command["color"] % len(PALETTE)],
                    (int(command["x"] * scale), int(command["y"] * scale)),
                    int(command["r"] * scale),
                    width,
                )
        pygame.display.flip()

    def tick(self, fps):
        return self.clock.tick(fps) / 1000.0


def run_pygame(project_path, entry=None, frames=None, fps=30):
    import os

    project_path = os.path.abspath(project_path)
    manifest = Manifest.load(project_path)
    backend = PygameBackend(manifest.canvas, manifest.canvas.scale)
    context = RuntimeContext(manifest, project_path, backend)
    context.load_entry(resolve_project_file(project_path, entry or manifest.entry, "entry"))

    count = 0
    running = True
    while running:
        running = backend.pump_input(context.input)
        dt = backend.tick(fps)
        context.step(dt)
        count += 1
        if frames is not None and count >= frames:
            running = False
    context.state = "STOPPED"
    return context
