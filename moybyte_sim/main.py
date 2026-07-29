"""Simulator entry helpers."""

from .headless_backend import HeadlessSimulator


def run_project(project_path, headless=False, frames=None, entry=None, fps=30, scale=None):
    if headless:
        sim = HeadlessSimulator(project_path, entry=entry)
        return sim.run(frames=frames or 60, fps=fps)

    try:
        # Probe pygame itself: PygameBackend imports it lazily (inside __init__),
        # so importing the backend module succeeds even with no pygame installed
        # and the fallback below never fired -- you got a ModuleNotFoundError
        # traceback several frames deep instead.
        import pygame  # noqa: F401
        from .pygame_backend import run_pygame
    except ImportError as exc:
        if frames is None:
            raise RuntimeError(
                "pygame is not installed, so there is no window to draw in; "
                "install it with `make setup` (or pip install -e '.[sim]'), "
                "or run with --headless") from exc
        sim = HeadlessSimulator(project_path, entry=entry)
        return sim.run(frames=frames, fps=fps)

    return run_pygame(project_path, entry=entry, frames=frames, fps=fps, scale=scale)
