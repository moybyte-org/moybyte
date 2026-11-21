"""`ui.grid` on the C engine (native/moy_uitree's draw_grid) held to the
reference (`uitree_ref.Engine._r_grid`): the same pixels and the same focus
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
def test_a_grid_draws_alike_on_both_engines(config, tmp_path):
    import uitree_ref
    import uitree_c
    ref = _run(uitree_ref.Engine, config)
    R.held(__file__, "grid/" + config, ref)
    c = _run(uitree_c.CEngine, config)
    states = [f.state for f in ref]
    assert any(s.endswith(" 1 True") for s in states) and any(s.endswith(" False") for s in states), states
    assert [f.state for f in c] == states
    P.compare(ref, c, tmp_path, "grid")
