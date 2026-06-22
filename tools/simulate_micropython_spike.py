#!/usr/bin/env python3
"""Headless host simulator for the T-Deck MicroPython spike.

This intentionally exercises the frozen KidCode Python stack, not the ESP32
bootloader, display driver, or LVGL bindings.
"""

import argparse
import json
import sys
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULES = ROOT / "firmware" / "lilygo_t_deck_plus_micropython" / "modules"
FIRMWARE_IMPORT_ROOTS = (
    "kidcode",
    "projects",
    "kidcode_sd",
    "kidcode_project_loader",
    "kidcode_lvgl_renderer",
    "tdeck_display",
)


class RecordingRenderer:
    def __init__(self):
        self.status = ""
        self.status_history = []
        self.frames = []

    def set_status(self, value):
        self.status = str(value)
        self.status_history.append(self.status)

    def render_message(self, status, lines):
        commands = [{"type": "clear", "color": 0}]
        y = 12
        for line in lines:
            commands.append({"type": "text", "value": line, "x": 6, "y": y, "color": 1})
            y += 14
        self.set_status(status)
        self.render(commands)

    def render(self, commands):
        self.frames.append([dict(command) for command in commands])


class FakeLVGL:
    class ALIGN:
        TOP_MID = "top_mid"

    class OPA:
        COVER = 255

    class obj:
        class FLAG:
            SCROLLABLE = "scrollable"
            HIDDEN = "hidden"

        def __init__(self, parent=None):
            self.parent = parent
            self.children = []
            self.flags = set()
            self.deleted = False
            self.text = ""
            self.pos = (0, 0)
            self.size = (0, 0)
            self.styles = {}
            self.alignments = []
            if parent is not None and hasattr(parent, "children"):
                parent.children.append(self)

        def set_size(self, width, height):
            self.size = (width, height)

        def set_pos(self, x, y):
            self.pos = (x, y)

        def align(self, align, x, y):
            self.alignments.append((align, x, y))

        def set_text(self, value):
            self.text = str(value)

        def set_style_bg_color(self, value, part):
            self.styles[("bg_color", part)] = value

        def set_style_bg_opa(self, value, part):
            self.styles[("bg_opa", part)] = value

        def set_style_border_color(self, value, part):
            self.styles[("border_color", part)] = value

        def set_style_border_width(self, value, part):
            self.styles[("border_width", part)] = value

        def set_style_radius(self, value, part):
            self.styles[("radius", part)] = value

        def set_style_pad_all(self, value, part):
            self.styles[("pad_all", part)] = value

        def set_style_text_color(self, value, part):
            self.styles[("text_color", part)] = value

        def remove_flag(self, flag):
            self.flags.discard(flag)

        def add_flag(self, flag):
            self.flags.add(flag)

        def delete(self):
            self.deleted = True
            if self.parent is not None and hasattr(self.parent, "children"):
                try:
                    self.parent.children.remove(self)
                except ValueError:
                    pass

    label = obj

    def __init__(self):
        self.screen = self.obj()

    def screen_active(self):
        return self.screen

    def color_hex(self, value):
        return value


class FakeLVGLRenderer:
    def __init__(self):
        if str(MODULES) not in sys.path:
            sys.path.insert(0, str(MODULES))

        from kidcode_lvgl_renderer import ConsoleRenderer

        self.lv = FakeLVGL()
        self.console = ConsoleRenderer(self.lv)
        self.frames = []
        self.status_history = []

    @property
    def status(self):
        return self.console.title.text

    def set_status(self, value):
        self.console.set_status(value)
        self.status_history.append(self.status)

    def render_message(self, status, lines):
        self.console.render_message(status, lines)
        self.status_history.append(self.status)
        self.frames.append(self.snapshot())

    def render(self, commands):
        self.console.render(commands)
        self.frames.append(self.snapshot())

    def snapshot(self):
        objects = {}
        for name, obj in self.console.objects.items():
            objects[name] = {
                "pos": obj.pos,
                "size": obj.size,
                "hidden": self.lv.obj.FLAG.HIDDEN in obj.flags,
                "styles": dict(obj.styles),
            }
        return {
            "title": self.console.title.text,
            "objects": objects,
            "text": [obj.text for obj in self.console.text_objects if not obj.deleted],
            "child_count": len(self.lv.screen.children),
        }


class ScriptedKeyboard:
    KEY_TO_BUTTON = {
        "left": "left",
        "right": "right",
        "up": "up",
        "down": "down",
        "a": "a",
        "b": "b",
        "run": "run",
        "stop": "stop",
        "home": "home",
    }
    LAST_KEY = {
        "left": ord("a"),
        "right": ord("d"),
        "up": ord("w"),
        "down": ord("s"),
        "a": ord("z"),
        "b": ord("x"),
        "run": ord("r"),
        "stop": ord("e"),
        "home": ord("q"),
    }

    def __init__(self, input_state, script):
        self.input = input_state
        self.available = True
        self.raw_mode = True
        self.frame = 0
        self.script = script

    def poll(self):
        self.input.release_all()
        button = self._button_for_frame(self.frame)
        self.frame += 1
        if button is None:
            self.input.last_key = 0
            return
        self.input.set_button(button, True)
        self.input.last_key = self.LAST_KEY.get(button, 0)

    def _button_for_frame(self, frame):
        cursor = 0
        for button, count in self.script:
            if frame < cursor + count:
                return button
            cursor += count
        return None


def parse_script(value):
    script = []
    if not value:
        return script
    for chunk in value.split(","):
        name, sep, count_text = chunk.partition(":")
        if not sep:
            raise ValueError("input chunks must look like button:frames")
        name = name.strip()
        if name not in ScriptedKeyboard.KEY_TO_BUTTON:
            raise ValueError("unknown input button: " + name)
        count = int(count_text)
        if count < 0:
            raise ValueError("input frame count must be non-negative")
        script.append((ScriptedKeyboard.KEY_TO_BUTTON[name], count))
    return script


def run_simulation(
    frames,
    dt,
    script,
    project="projects.tiny_runner",
    source_path=None,
    realtime=False,
    renderer_mode="recording",
):
    saved_modules = begin_firmware_imports()
    try:
        from kidcode.input import InputState
        from kidcode_project_loader import ProjectRunner

        input_state = InputState()
        keyboard = ScriptedKeyboard(input_state, script)
        renderer = make_renderer(renderer_mode)
        runner = ProjectRunner(input_state, keyboard, renderer)
        if source_path is None:
            runner.load_frozen_project(project)
        else:
            runner.load_source(str(source_path), Path(source_path).read_text(encoding="utf-8"))

        started = time.monotonic()
        for _index in range(frames):
            runner.step(dt)
            if realtime:
                time.sleep(dt)
        elapsed = max(time.monotonic() - started, 0.000001)

        commands = renderer.frames[-1] if renderer.frames else []
        if renderer_mode == "fake-lvgl":
            commands = runner.runtime.canvas.commands
        sprites = {
            command.get("name"): command
            for command in commands
            if command.get("type") == "sprite"
        }
        return {
            "frames": frames,
            "dt": dt,
            "project": str(source_path) if source_path is not None else project,
            "source_path": str(source_path) if source_path is not None else None,
            "elapsed_s": elapsed,
            "sim_fps": frames / elapsed,
            "status": renderer.status,
            "status_history": renderer.status_history[-8:],
            "rendered_frames": len(renderer.frames),
            "renderer_mode": renderer_mode,
            "renderer_snapshot": renderer.frames[-1] if renderer_mode == "fake-lvgl" and renderer.frames else None,
            "last_key": input_state.last_key,
            "held_mask": held_mask(input_state),
            "sprites": sprites,
            "last_commands": commands,
        }
    finally:
        restore_imports(saved_modules)


def begin_firmware_imports():
    path_added = str(MODULES) not in sys.path
    if path_added:
        sys.path.insert(0, str(MODULES))
    saved = {}
    for name in list(sys.modules):
        if is_firmware_import_name(name):
            saved[name] = sys.modules.pop(name)
    return {"modules": saved, "path_added": path_added}


def restore_imports(state):
    for name in list(sys.modules):
        if is_firmware_import_name(name):
            sys.modules.pop(name)
    sys.modules.update(state["modules"])
    if state["path_added"]:
        try:
            sys.path.remove(str(MODULES))
        except ValueError:
            pass


def is_firmware_import_name(name):
    return any(name == root or name.startswith(root + ".") for root in FIRMWARE_IMPORT_ROOTS)


def make_renderer(mode):
    if mode == "recording":
        return RecordingRenderer()
    if mode == "fake-lvgl":
        return FakeLVGLRenderer()
    raise ValueError("unknown renderer mode: " + mode)


def held_mask(input_state):
    mask = 0
    for bit, name in (
        (1, "left"),
        (2, "right"),
        (4, "up"),
        (8, "down"),
        (16, "a"),
        (32, "b"),
    ):
        if input_state.held(name):
            mask |= bit
    return mask


def ascii_canvas(commands, width=32, height=16):
    pixels = [[" " for _x in range(width)] for _y in range(height)]
    for command in commands:
        kind = command.get("type")
        if kind == "clear":
            fill = "." if command.get("color", 0) else " "
            pixels = [[fill for _x in range(width)] for _y in range(height)]
        elif kind in ("sprite", "rect"):
            char = "#"
            if command.get("name") == "player":
                char = "P"
            elif command.get("name") == "coin":
                char = "C"
            paint_rect(
                pixels,
                command.get("x", 0),
                command.get("y", 0),
                command.get("w", 1),
                command.get("h", 1),
                char,
            )
    return "\n".join("".join(row).rstrip() for row in pixels)


def paint_rect(pixels, x, y, w, h, char):
    height = len(pixels)
    width = len(pixels[0]) if height else 0
    x0 = max(0, x * width // 128)
    y0 = max(0, y * height // 128)
    x1 = min(width, max(x0 + 1, (x + w) * width // 128))
    y1 = min(height, max(y0 + 1, (y + h) * height // 128))
    for row in range(y0, y1):
        for col in range(x0, x1):
            pixels[row][col] = char


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--frames", type=int, default=120)
    parser.add_argument("--dt", type=float, default=1 / 30)
    parser.add_argument(
        "--input",
        default="right:30,down:30,left:30,up:30",
        help="comma-separated button:frames script",
    )
    parser.add_argument("--project", default="projects.tiny_runner")
    parser.add_argument("--source", help="load a KidCode Python source file instead of frozen project")
    parser.add_argument("--renderer", choices=("recording", "fake-lvgl"), default="recording")
    parser.add_argument("--realtime", action="store_true")
    parser.add_argument("--ascii", action="store_true", help="print a coarse final canvas")
    parser.add_argument("--json", action="store_true", help="print machine-readable result")
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    result = run_simulation(
        frames=args.frames,
        dt=args.dt,
        script=parse_script(args.input),
        project=args.project,
        source_path=args.source,
        realtime=args.realtime,
        renderer_mode=args.renderer,
    )
    if args.json:
        print(json.dumps(result, sort_keys=True))
    else:
        print(
            "KidCode simulator frames={frames} rendered={rendered_frames} "
            "sim_fps={sim_fps:.1f} renderer={renderer_mode} status={status}".format(**result)
        )
        player = result["sprites"].get("player", {})
        coin = result["sprites"].get("coin", {})
        print("player=({}, {}) coin=({}, {})".format(player.get("x"), player.get("y"), coin.get("x"), coin.get("y")))
        if args.ascii:
            print(ascii_canvas(result["last_commands"]))


if __name__ == "__main__":
    main()
