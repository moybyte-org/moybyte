"""`ui.grid` on the C engine (native/moy_uitree's draw_grid) held to what the
reference engine recorded (tests/uitree_reference/): its pixels and its focus
walk at every size, a span= button two cells wide, a hidden one's cells
left empty."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent / "uitree_fixtures"))

import uitree_parity as P  # noqa: E402
import uitree_reference as R  # noqa: E402

CONFIGS = ("tdeck_320x240_fs1_dark", "p4_1024x600_fs2_windowed", "big_800x480_fs3_dark",
           "p4_1024x600_cs2_fs1_windowed")
# Walk the keys, press the wide key, hide it with Seven, walk again, show it back
SCRIPT = ["k:down", "k:down", "k:down", "k:down", "k:a", "k:up", "k:a", "k:down", "k:down",
          "k:up", "k:a", "k:down", "k:down"]


def _run(engine, config):
    import uitree_grid as F
    seen = {}
    cls = F.layer(engine)

    def opened(ws):
        seen["app"] = P.host(ws, cls)

    def probe(ws):
        e = seen["app"].engine
        return "%s %s %s" % (e.focus_key, e.s._v["n"], e.s._v["wide"])
    return P.frames(config, P.Surface(opened, probe=probe), SCRIPT)


@pytest.mark.parametrize("config", CONFIGS)
def test_a_grid_draws_as_the_reference_did(config):
    import uitree_c
    c = _run(uitree_c.CEngine, config)
    states = [f.state for f in c]
    assert any(s.endswith(" 1 True") for s in states) and any(s.endswith(" False") for s in states), states
    R.held(__file__, "grid/" + config, c)
