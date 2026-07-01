"""Runtime context for Moybyte projects."""

import os
import runpy
import sys

from .app import game
from .audio import AudioService
from .errors import MoybyteRuntimeError, friendly_from_exception
from .files import FileService
from .input import InputState
from .radio import RadioService
from .screen import Screen
from .sprites import Sprite

_current = None


def current_context():
    if _current is None:
        raise RuntimeError("Moybyte runtime is not active")
    return _current


def set_current_context(context):
    global _current
    _current = context


class RuntimeContext:
    def __init__(self, manifest=None, project_path=None, backend=None):
        self.manifest = manifest
        self.project_path = project_path
        self.backend = backend
        self.state = "CREATED"
        self.sprites = []
        width = 128
        height = 128
        permissions = None
        if manifest is not None:
            width = manifest.canvas.width
            height = manifest.canvas.height
            permissions = manifest.permissions
        self.input = InputState()
        self.screen = Screen(width, height)
        self.audio = AudioService(permissions)
        self.radio = RadioService(permissions)
        self.files = FileService(project_path, permissions)
        self.frame = 0
        self.error = None
        self.auto_run_requested = False

    def reset_api(self):
        game.reset()
        self.sprites = []
        self.screen.clear(0)
        self.frame = 0
        self.error = None
        self.auto_run_requested = False

    def create_sprite(self, name, x=0, y=0, w=8, h=8):
        item = Sprite(name, x=x, y=y, w=w, h=h)
        self.sprites.append(item)
        return item

    def get_sprite(self, name):
        for item in self.sprites:
            if item.name == name:
                return item
        return None

    def register_run(self, update=None, draw=None):
        if update is not None:
            game.update(update)
        if draw is not None:
            game.draw(draw)
        self.auto_run_requested = True

    def load_entry(self, entry_path):
        self.state = "LOADING"
        self.reset_api()
        set_current_context(self)
        project_dir = self.project_path
        old_path = list(sys.path)
        old_cwd = os.getcwd()
        try:
            if project_dir is not None:
                sys.path.insert(0, project_dir)
                os.chdir(project_dir)
            runpy.run_path(entry_path, run_name="__moybyte_project__")
            self.state = "READY"
        except Exception as exc:
            self.error = friendly_from_exception(exc, entry_path)
            self.state = "ERROR"
            raise MoybyteRuntimeError(self.error) from exc
        finally:
            sys.path = old_path
            os.chdir(old_cwd)

    def begin_frame(self):
        self.input.begin_frame()
        for name, handler in list(game.button_handlers.items()):
            if self.input.pressed(name):
                handler()

    def step(self, dt=1.0 / 30.0):
        if self.state in ["CREATED", "LOADING", "ERROR", "STOPPED"]:
            return
        self.state = "RUNNING"
        try:
            self.begin_frame()
            if game.update_fn is not None:
                game.update_fn(dt)
            if game.draw_fn is not None:
                game.draw_fn()
            if self.backend is not None:
                self.backend.present(self.screen.commands)
            self.frame += 1
        except Exception as exc:
            self.error = friendly_from_exception(exc)
            self.state = "ERROR"
            raise MoybyteRuntimeError(self.error) from exc

    def run_frames(self, frames, fps=30):
        dt = 1.0 / float(fps)
        for _ in range(frames):
            self.step(dt)
            if self.state == "ERROR":
                break
        self.state = "STOPPED"
