"""The layout rule along x (a placed row: fixed cells take their width, the
fills share what is left, a region stacks its own children in the row's
whole height, a row of regions fills its stack): a page of a bar and a row
of regions draws the same pixels and places every cell at the same rect on
the reference (`uitree_ref_editor.Engine`) and on the C engine, on every size."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent / "uitree_fixtures"))

import uitree_parity as P  # noqa: E402
import uitree_reference as R  # noqa: E402

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
    import uitree_ref_editor
    ref = _run(uitree_ref_editor.Engine, config)
    R.held(__file__, "row/" + config, ref)
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
    import uitree_ref_editor
    f = _run(uitree_ref_editor.Engine, "p4_1024x600_cs2_fs1_windowed")[0]
    rects = dict(ast.literal_eval(f.state.split(" ", 2)[2]))
    assert rects["logo"][2] == 40 and rects["side"][2] == 56, rects
    assert rects["left"][3] == rects["body"][3] == rects["side"][3], rects
    assert rects["body"][3] > rects["bar"][3], rects


BAR_KEYS = ("bar", "zone", "chips", "chip", "clock", "bump", "tray", "wifi", "batt", "mid_bar", "lfill",
            "centre", "title", "flip", "rfill", "rfix", "ends", "lead", "space", "tail", "body")
# Bump (focused first) widens the clock (w= a state) and the tray's gap (gap= a state)
BAR_SCRIPT = ["k:a", "k:right", "k:a", "k:down", "k:right", "k:left", "k:a"]


def _run_bar(engine, config):
    import uitree_across_bar as F
    seen = {}
    cls = F.layer(engine)

    def opened(ws):
        seen["app"] = P.host(ws, cls)

    def probe(ws):
        e = seen["app"].engine
        return "%s %r" % (e.focus_key, [(k, e.rect_of(k)) for k in BAR_KEYS])
    return P.frames(config, P.Surface(opened, probe=probe), BAR_SCRIPT)


@pytest.mark.parametrize("config", CONFIGS)
def test_a_bar_s_pieces_lay_out_alike_on_both_engines(config, tmp_path):
    """pad=, gap= (a literal and a state), w= from a state, a row in a row,
    a spacer, a mid of fills and a mid of fixed cells: the same rects and
    pixels on both engines."""
    import uitree_c
    import uitree_ref_editor
    ref = _run_bar(uitree_ref_editor.Engine, config)
    R.held(__file__, "bar/" + config, ref)
    c = _run_bar(uitree_c.CEditorEngine, config)
    assert [f.state for f in c] == [f.state for f in ref]
    P.compare(ref, c, tmp_path, "across_bar")


def test_the_bar_s_pieces_place_by_their_rule():
    """On the desk (unit 1): the pad and gap literal, the clock as wide as
    its state, the tray's gap its state, the chips centred on the bar and
    clearing both sides, the centre run centred with each side's fill
    sharing its own side, the spacer pushing the tail to the row's end."""
    import ast
    import uitree_ref_editor
    f = _run_bar(uitree_ref_editor.Engine, "p4_1024x600_cs2_fs1_windowed")
    r = dict(ast.literal_eval(f[0].state.split(" ", 1)[1]))
    bx, _, bw, _ = r["bar"]
    assert r["bump"][0] == bx + 2 and r["zone"][0] == r["bump"][0] + r["bump"][2] + 2, r
    assert r["clock"][2] == 40 and r["wifi"][2] == 16, r
    assert r["batt"][0] - (r["wifi"][0] + 16) == 6, r
    assert r["batt"][0] + 16 == bx + bw - 2, r
    cx = bx + bw // 2
    assert r["chips"][0] + r["chips"][2] // 2 == cx, r
    assert r["chips"][0] + r["chips"][2] + 2 == r["clock"][0], r
    assert r["zone"][0] + r["zone"][2] + 2 == r["chips"][0], r
    mx, _, mw, _ = r["mid_bar"]
    assert abs(r["centre"][0] + r["centre"][2] // 2 - (mx + mw // 2)) <= 1, r
    assert r["lfill"][0] == mx + 4 and r["lfill"][0] + r["lfill"][2] + 4 == r["centre"][0], r
    assert r["rfill"][0] == r["centre"][0] + r["centre"][2] + 4, r
    assert r["rfix"][0] + 24 == mx + mw - 4, r
    ex, _, ew, _ = r["ends"]
    assert r["tail"][0] + r["tail"][2] == ex + ew, r
    later = [dict(ast.literal_eval(x.state.split(" ", 1)[1])) for x in f]
    later = next(x for x in later if x["clock"][2] != 40)
    assert later["clock"][2] == 52 and later["batt"][0] - (later["wifi"][0] + 16) == 9, later
