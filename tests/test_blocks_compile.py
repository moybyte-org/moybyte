import os

from kidcode_blocks.compiler import compile_blocks, compile_project
from kidcode_sim.headless_backend import HeadlessSimulator


def test_compile_blocks_generates_readable_python():
    code = compile_blocks(
        {
            "schema": "kidcode.blocks.v1",
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

    assert "from kidcode import *" in code
    assert "def update(dt):" in code
    assert "if button('right')" in code


def test_compile_project_and_run_generated_code():
    out_path = compile_project("examples/blocks_demo.kcproj")

    assert os.path.exists(out_path)
    sim = HeadlessSimulator("examples/blocks_demo.kcproj", entry="generated/main.generated.py")
    context = sim.run(frames=5)
    assert context.frame == 5
