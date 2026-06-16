import json

import pytest

from kidcode.errors import KidCodeRuntimeError
from kidcode_sim.headless_backend import HeadlessSimulator


def test_music_player_stub_logs_audio_call():
    sim = HeadlessSimulator("examples/music_player_stub.kcproj")
    context = sim.run(frames=3)

    assert ("play", "assets/music/song1.mp3") in context.audio.calls


def test_radio_pong_stub_sends_join_message():
    sim = HeadlessSimulator("examples/radio_pong_stub.kcproj")
    context = sim.run(frames=3)

    assert {"type": "join", "project": "radio_pong_stub"} in context.radio.messages


def test_radio_permission_failure_is_friendly(tmp_path):
    project = tmp_path / "no_radio.kcproj"
    project.mkdir()
    (project / "manifest.json").write_text(
        json.dumps(
            {
                "schema": "kidcode.project.v1",
                "id": "no_radio",
                "title": "No Radio",
                "kind": "game",
                "age_mode": "text",
                "entry": "main.py",
                "canvas": {"width": 128, "height": 128, "scale": 4},
                "permissions": {"files": "project", "audio": False, "radio": False},
            }
        ),
        encoding="utf-8",
    )
    (project / "main.py").write_text(
        "from kidcode import *\n"
        "\n"
        "def update(dt):\n"
        "    radio.send('ping')\n"
        "\n"
        "run(update=update)\n",
        encoding="utf-8",
    )

    sim = HeadlessSimulator(str(project))
    sim.load()
    with pytest.raises(KidCodeRuntimeError) as err:
        sim.step()

    assert "radio is not enabled" in err.value.friendly_error.message
