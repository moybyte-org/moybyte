"""The placed layout on the C engine (native/moy_uitree's `stack`, the one
rule a container gives its children rects by): a page on the Editor's
placed kinds draws the pixels the reference engine recorded
(tests/uitree_reference/) on every size, its regions and cells read back at
the recorded rects, and a host's placement with nothing drawn (`place`)
agrees with it too."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent / "uitree_fixtures"))

import uitree_parity as P  # noqa: E402
import uitree_reference as R  # noqa: E402

CONFIGS = ("tdeck_320x240_fs1_dark", "p4_1024x600_fs2_windowed", "big_800x480_fs3_dark",
           "p4_1024x600_cs2_fs1_windowed")
KEYS = ("find_row", "q", "bump", "flip", "look", "tool_row", "copy", "paste", "status_row", "pad", "note", "dock")
# focus walks the row, the case and the tools; Aa marks; the switch flips to its other case; a key typed
SCRIPT = ["k:right", "k:a", "k:right", "k:a", "k:down", "k:a", "k:down", "k:up", "k:up", "k:a", "k:left",
          "k:left", "t:x"]


def _run(engine, config):
    import uitree_placed as F
    seen = {}
    cls = F.layer(engine)

    def opened(ws):
        seen["app"] = P.host(ws, cls)

    def probe(ws):
        e = seen["app"].engine
        return "%s %s %r" % (e.focus_key, e.s._v["mode"], [(k, e.rect_of(k)) for k in KEYS])
    return seen, P.frames(config, P.Surface(opened, probe=probe), SCRIPT)


@pytest.mark.parametrize("config", CONFIGS)
def test_a_placed_page_draws_and_places_as_the_reference_did(config):
    import uitree_c
    seen, c = _run(uitree_c.CEditorEngine, config)
    assert "rows" in {f.state.split()[1] for f in c}, "the script never reached the switch's other case"
    assert "('q', (" in c[0].state and "('pad', (" in c[0].state, c[0].state
    R.held(__file__, "placed/" + config, c)


def test_a_placement_draws_nothing_and_gives_the_regions_rects():
    """`place` lays the regions out over a host's area with no frame begun:
    the stacked rows, the regions a host places and the cells along a row
    (a bar's host reads them), drawing none of them."""
    import uitree_c
    import uitree_placed as F
    import ui_draw
    e = uitree_c.CEditorPage(F.build, ui_draw.AppHost(), type("D", (), {"all": lambda self: None})())
    e.open()
    e.fs = 1                # the scale a host's placement reads (its frames set it)
    e.place((10, 20, 300, 200), desk=False)
    got = [(k, e.rect_of(k)) for k in KEYS]
    R.held(__file__, "place", got)
    rects = dict(got)
    assert rects["find_row"] is not None and rects["q"] is not None, rects
    fx, _, fw, _ = rects["find_row"]
    assert fx <= rects["q"][0] and rects["q"][0] + rects["q"][2] <= fx + fw, rects
