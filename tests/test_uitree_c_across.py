"""The layout rule along x (a placed row: fixed cells take their width, the
fills share what is left, a region stacks its own children in the row's
whole height, a row of regions fills its stack): a page of a bar and a row
of regions draws the same pixels and places every cell at the same rect on
the reference (`uitree_editor.Engine`) and on the C engine, on every size."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent / "uitree_fixtures"))

import uitree_parity as P  # noqa: E402

CONFIGS = ("tdeck_320x240_fs1_dark", "p4_1024x600_fs2_windowed", "big_800x480_fs3_dark",
           "p4_1024x600_cs2_fs1_windowed")
KEYS = ("bar", "logo", "flip", "meter", "bump", "body", "left", "switch:mode", "note", "side", "foot")
# focus walks the bar; Flip turns the switch to its other case and back; Bump counts
SCRIPT = ["k:right", "k:a", "k:right", "k:a", "k:down", "k:a", "k:up", "k:left", "k:a"]


def _run(engine, config):
    import uitree_across as F
    seen = {}
    cls = F.layer(engine)

    def opened(ws):
        seen["app"] = P.host(ws, cls)

    def probe(ws):
        e = seen["app"].engine
        return "%s %s %r" % (e.focus_key, e.s._v["mode"], [(k, e.rect_of(k)) for k in KEYS])
    return P.frames(config, P.Surface(opened, probe=probe), SCRIPT)


@pytest.mark.parametrize("config", CONFIGS)
def test_a_row_lays_its_cells_out_by_the_rule_alike_on_both_engines(config, tmp_path):
    import uitree_c
    import uitree_editor
    ref = _run(uitree_editor.Engine, config)
    c = _run(uitree_c.CEditorEngine, config)
    assert "rows" in {f.state.split()[1] for f in ref}, "the script never reached the switch's other case"
    first = ref[0].state
    for key in ("logo", "meter", "left", "side"):
        assert "('%s', (" % key in first, first
    assert [f.state for f in c] == [f.state for f in ref]
    P.compare(ref, c, tmp_path, "across")


def test_fixed_cells_take_their_width_and_the_fills_share_the_rest():
    """The bar on the desk: the logo 40 wide and the side 56 (w= at the
    desk's scale 1), the meter and the status line splitting what is left;
    the body's regions the row's whole height, the body a fill of the page."""
    import ast
    import uitree_editor
    f = _run(uitree_editor.Engine, "p4_1024x600_cs2_fs1_windowed")[0]
    rects = dict(ast.literal_eval(f.state.split(" ", 2)[2]))
    assert rects["logo"][2] == 40 and rects["side"][2] == 56, rects
    assert rects["left"][3] == rects["body"][3] == rects["side"][3], rects
    assert rects["body"][3] > rects["bar"][3], rects
