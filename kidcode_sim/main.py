"""Simulator entry helpers."""

from .headless_backend import HeadlessSimulator


def run_project(project_path, headless=False, frames=None, entry=None, fps=30, scale=None):
    if headless:
        sim = HeadlessSimulator(project_path, entry=entry)
        return sim.run(frames=frames or 60, fps=fps)

    try:
        from .pygame_backend import run_pygame
    except Exception as exc:
        if frames is None:
            raise RuntimeError("pygame is not available; use --headless") from exc
        sim = HeadlessSimulator(project_path, entry=entry)
        return sim.run(frames=frames, fps=fps)

    return run_pygame(project_path, entry=entry, frames=frames, fps=fps, scale=scale)
