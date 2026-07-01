import os

import pytest

from moybyte_blocks.compiler import compile_blocks, compile_project
from moybyte_blocks.schema import BlockValidationError
from moybyte_sim.headless_backend import HeadlessSimulator


def test_compile_blocks_generates_readable_python():
    code = compile_blocks(
        {
            "schema": "moybyte.blocks.v1",
            "variables": [{"name": "score", "initial": 0}],
            "sprites": [{"name": "player", "asset": "player", "x": 1, "y": 2}],
            "scripts": [
                {
                    "event": {"type": "update"},
                    "body": [
                        {
                            "type": "if_button",
                            "button": "right",
                            "body": [{"type": "move_sprite", "sprite": "player", "dx": 2, "dy": 0}],
                        }
                    ],
                }
            ],
        }
    )

    assert "from moybyte import *" in code
    assert "def update(dt):" in code
    assert "# Update script" in code
    assert 'if button("right")' in code
    assert 'player = sprite("player", x=1, y=2, w=8, h=8)' in code


def test_compile_project_and_run_generated_code():
    out_path = compile_project("examples/blocks_demo.moyproj")

    assert os.path.exists(out_path)
    sim = HeadlessSimulator("examples/blocks_demo.moyproj", entry="generated/main.generated.py")
    context = sim.run(frames=5)
    assert context.frame == 5


def test_compile_blocks_rejects_unknown_sprite_reference():
    with pytest.raises(BlockValidationError) as err:
        compile_blocks(
            {
                "schema": "moybyte.blocks.v1",
                "sprites": [{"name": "player"}],
                "scripts": [
                    {
                        "event": {"type": "draw"},
                        "body": [{"type": "draw_sprite", "sprite": "coin"}],
                    }
                ],
            }
        )

    assert "unknown sprite 'coin'" in str(err.value)


def test_compile_blocks_rejects_unsafe_text_template():
    with pytest.raises(BlockValidationError) as err:
        compile_blocks(
            {
                "schema": "moybyte.blocks.v1",
                "variables": [{"name": "score", "initial": 0}],
                "scripts": [
                    {
                        "event": {"type": "draw"},
                        "body": [{"type": "text", "value": "{score.__class__}", "x": 0, "y": 0}],
                    }
                ],
            }
        )

    assert "must be a simple variable name" in str(err.value)


def test_compile_blocks_rejects_unknown_button():
    with pytest.raises(BlockValidationError) as err:
        compile_blocks(
            {
                "schema": "moybyte.blocks.v1",
                "sprites": [{"name": "player"}],
                "scripts": [
                    {
                        "event": {"type": "update"},
                        "body": [{"type": "if_button", "button": "turbo", "body": []}],
                    }
                ],
            }
        )

    assert "known button" in str(err.value)
