"""Deterministic headless simulator backend."""

import os

from moybyte.manifest import Manifest, resolve_project_file
from moybyte.runtime import RuntimeContext


class HeadlessBackend:
    def __init__(self):
        self.frames = []

    def present(self, commands):
        self.frames.append([dict(command) for command in commands])


class HeadlessSimulator:
    def __init__(self, project_path, entry=None, backend=None):
        self.project_path = os.path.abspath(project_path)
        self.manifest = Manifest.load(self.project_path)
        self.entry = entry or self.manifest.entry
        self.backend = backend or HeadlessBackend()
        self.context = RuntimeContext(self.manifest, self.project_path, self.backend)
        self.loaded = False

    def load(self):
        entry_path = resolve_project_file(self.project_path, self.entry, "entry")
        self.context.load_entry(entry_path)
        self.loaded = True

    def press(self, name):
        self.context.input.press(name)

    def release(self, name):
        self.context.input.release(name)

    def step(self, frames=1, fps=30):
        if not self.loaded:
            self.load()
        dt = 1.0 / float(fps)
        for _ in range(frames):
            self.context.step(dt)
        return self.context

    def run(self, frames=60, fps=30):
        if not self.loaded:
            self.load()
        self.context.run_frames(frames, fps=fps)
        return self.context

    def get_sprite(self, name):
        return self.context.get_sprite(name)
