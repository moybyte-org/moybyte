"""A row from a model on both engines (`ui.rows` in a placed row:
native/moy_uitree's row_kids / fit_cells, uitree_editor's `_row_kids` /
`_fit_cells`): the same cells at the same rects (`rect_of("ROWKEY/KEY")`)
and the same pixels at every size, as the list grows past the row's room,
its titles lengthen and the marked row moves."""

import ast
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent / "uitree_fixtures"))

import uitree_parity as P  # noqa: E402

CONFIGS = ("tdeck_320x240_fs1_dark", "p4_1024x600_fs2_windowed", "big_800x480_fs3_dark",
           "p4_1024x600_cs2_fs1_windowed")
CELLS = ["w%d/chip" % i for i in range(12)]
# Grow the list past the row's room, walk along it pressing (a chip marks its row, Long
# lengthens every title), walk back
SCRIPT = ["k:a", "k:a", "k:a", "k:right", "k:a", "k:right", "k:a", "k:right", "k:a", "k:right",
          "k:a", "k:left", "k:left", "k:a"]


def _run(engine, config):
    import uitree_model_row as F
    seen = {}
    cls = F.layer(engine)

    def opened(ws):
        seen["app"] = P.host(ws, cls)

    def probe(ws):
        e = seen["app"].engine
        return "%s %r" % (e.focus_key, [(k, e.rect_of(k)) for k in CELLS])
    return P.frames(config, P.Surface(opened, probe=probe), SCRIPT)


@pytest.mark.parametrize("config", CONFIGS)
def test_a_row_from_a_model_draws_alike_on_both_engines(config, tmp_path):
    import uitree_c
    import uitree_editor
    ref = _run(uitree_editor.Engine, config)
    c = _run(uitree_c.CEditorEngine, config)
    assert [f.state for f in c] == [f.state for f in ref]
    P.compare(ref, c, tmp_path, "model_row")


def _cells(f):
    return dict((k, r) for k, r in ast.literal_eval(f.state.split(" ", 1)[1]) if r)


def test_a_model_s_cells_take_their_text_then_cut_the_widest_then_drop():
    import uitree_editor
    f = _run(uitree_editor.Engine, "tdeck_320x240_fs1_dark")
    first = _cells(f[0])
    assert sorted(first) == ["w0/chip", "w1/chip"], first
    assert first["w0/chip"][2] > first["w1/chip"][2], first          # Settings wider than Editor
    cut = _cells(f[3])                                                # eight rows: one width each
    assert len(cut) == 8 and len({r[2] for r in cut.values()}) == 1, cut
    assert next(iter(cut.values()))[2] < first["w1/chip"][2], cut
    last = _cells(f[-1])                                              # ten rows: the last dropped
    assert 8 <= len(last) < 10, last
    assert all(r[2] >= r[3] for r in last.values()), last             # never under the row's height


def test_a_row_from_a_model_outside_a_placed_row_is_b03():
    import uitree as S
    import uitree_app

    def build():
        with S.page("P"):
            with S.rows("wins", dir="row"):
                S.button("chip", label="{.title}")
    import uitree_model_row as F
    eng = uitree_app.Engine(S.decl_of(F), build, None, None, None)
    with pytest.raises(S.UIError) as e:
        eng._build()
    assert e.value.code == "B03", e.value
