import json

import pytest

from moybyte.errors import MoybyteRuntimeError, ManifestError
from moybyte.input import InputState
from moybyte_sim.headless_backend import HeadlessSimulator


def test_headless_run_steps_frames():
    sim = HeadlessSimulator("examples/tiny_runner.moyproj")
    context = sim.run(frames=10)

    assert context.frame == 10
    assert sim.get_sprite("player") is not None


def test_player_moves_right_in_headless_simulator():
    sim = HeadlessSimulator("examples/tiny_runner.moyproj")
    sim.load()
    player = sim.get_sprite("player")
    x0 = player.x

    sim.press("right")
    sim.step(frames=5)

    assert player.x > x0


def test_input_edge_detection():
    state = InputState()
    state.press("a")
    state.begin_frame()
    assert state.held("a")
    assert state.pressed("a")

    state.begin_frame()
    assert state.held("a")
    assert not state.pressed("a")

    state.release("a")
    state.begin_frame()
    assert state.released("a")


def test_entry_override_cannot_escape_project():
    sim = HeadlessSimulator("examples/tiny_runner.moyproj", entry="../outside.py")

    with pytest.raises(ManifestError):
        sim.load()


def test_user_code_crash_becomes_friendly_error(tmp_path):
    project = tmp_path / "crash.moyproj"
    project.mkdir()
    (project / "manifest.json").write_text(
        json.dumps(
            {
                "schema": "moybyte.project.v1",
                "id": "crash",
                "title": "Crash",
                "kind": "game",
                "age_mode": "text",
                "entry": "main.py",
                "canvas": {"width": 128, "height": 128, "scale": 4},
                "permissions": {"files": "project", "audio": True},
            }
        ),
        encoding="utf-8",
    )
    (project / "main.py").write_text(
        "from moybyte import *\n"
        "\n"
        "def update(dt):\n"
        "    missing_name += 1\n"
        "\n"
        "run(update=update)\n",
        encoding="utf-8",
    )

    sim = HeadlessSimulator(str(project))
    sim.load()
    with pytest.raises(MoybyteRuntimeError) as err:
        sim.step()

    assert sim.context.state == "ERROR"
    assert "missing_name" in err.value.friendly_error.message
