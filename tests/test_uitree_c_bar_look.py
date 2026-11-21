"""The bar's look in the toolkit (`button kind="bar"`, moy_ui_bar_item): a
row of bar items draws the same pixels and places every cell at the same
rect on the reference (`uitree_ref_editor.Engine`) and on the C engine, at
every size, its focus walked across them and their marks moved."""

import ast
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent / "uitree_fixtures"))

import uitree_parity as P  # noqa: E402

CONFIGS = ("tdeck_320x240_fs1_dark", "p4_1024x600_fs2_windowed", "big_800x480_fs3_dark",
           "p4_1024x600_cs2_fs1_windowed")
KEYS = ("bar", "menu", "zone", "tray", "wifi", "battery", "tick", "body")
# Menu marked and unmarked, the walk over the tray, the clock widened
SCRIPT = ["k:a", "k:right", "k:right", "k:right", "k:right", "k:a", "k:left", "k:a", "k:left", "k:a", "k:left", "k:left"]


def _run(engine, config):
    import uitree_bar_look as F
    seen = {}
    cls = F.layer(engine)

    def opened(ws):
        seen["app"] = P.host(ws, cls)

    def probe(ws):
        e = seen["app"].engine
        return "%s %r" % (e.focus_key, [(k, e.rect_of(k)) for k in KEYS])
    return P.frames(config, P.Surface(opened, probe=probe), SCRIPT)


@pytest.mark.parametrize("config", CONFIGS)
def test_bar_items_draw_alike_on_both_engines(config, tmp_path):
    import uitree_c
    import uitree_ref_editor
    ref = _run(uitree_ref_editor.Engine, config)
    c = _run(uitree_c.CEditorEngine, config)
    assert {f.state.split()[0] for f in ref} >= {"menu", "wifi", "tick"}, [f.state for f in ref]
    assert [f.state for f in c] == [f.state for f in ref]
    P.compare(ref, c, tmp_path, "bar_look")


def test_a_bar_item_takes_its_row_s_height_and_its_own_width():
    import uitree_ref_editor
    f = _run(uitree_ref_editor.Engine, "p4_1024x600_cs2_fs1_windowed")
    r = dict(ast.literal_eval(f[0].state.split(" ", 1)[1]))
    assert r["menu"][1] == r["bar"][1] and r["menu"][3] == r["bar"][3], r
    assert r["wifi"][2] == 16 and r["tick"][2] == 40, r
    later = [dict(ast.literal_eval(x.state.split(" ", 1)[1])) for x in f]
    assert any(x["tick"][2] == 52 for x in later), later
