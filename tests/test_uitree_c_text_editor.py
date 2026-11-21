"""The Editor's text editor on the C engine: the store places it by the
one rule (whole lines), its document stays Python's and the front end
draws its rows in the rect (`uitree_editor.Engine._r_text_editor`), what
its templates read of the document (`{code.line}`) is a row the store
holds, and the arrows and typed bytes reach the document ahead of the
store's keys. Both engines draw the same pixels and leave the same state."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent / "uitree_fixtures"))

import uitree_parity as P  # noqa: E402

CONFIGS = ("tdeck_320x240_fs1_dark", "p4_1024x600_fs2_windowed", "big_800x480_fs3_dark",
           "p4_1024x600_cs2_fs1_windowed")
# the caret down and across, letters typed into the document (a bare letter an action answers
# too: the text editor takes typed bytes first), the gutter off and on by its button (focused),
# the find shown by its chord, its matches marked as the caret walks, hidden
SCRIPT = ["k:down", "k:down", "k:right", "k:right", "t:z", "t:q", "k:down", "t:g", "t:g", "k:up",
          "k:a", "k:down", "k:a", "t:\x06", "k:down", "k:down", "t:\x06"]


def _run(engine, config):
    import uitree_text_editor as F
    seen = {}
    cls = F.layer(engine)

    def opened(ws):
        seen["app"] = P.host(ws, cls)

    def probe(ws):
        e = seen["app"].engine
        d = e.s._v["code"]
        return "%s L%d:%d %s gutter=%s finding=%s %r" % (
            e.focus_key, d.line, d.col, d.text().count("zq"), e.s._v["gutter_on"], e.s._v["finding"],
            [(k, e.rect_of(k)) for k in ("find_strip", "code", "status_row")])
    return seen, P.frames(config, P.Surface(opened, probe=probe), SCRIPT)


@pytest.mark.parametrize("config", CONFIGS)
def test_a_text_editor_draws_and_types_alike_on_both_engines(config, tmp_path):
    import uitree_c
    import uitree_editor
    _, ref = _run(uitree_editor.Engine, config)
    _, c = _run(uitree_c.CEditorEngine, config)
    states = [f.state for f in ref]
    assert " L1:1 0 " in states[0] and any("gutter=False" in t for t in states), states
    assert any("finding=True" in t and "('find_strip', (" in t for t in states), states
    assert [f.state for f in c] == [f.state for f in ref]
    P.compare(ref, c, tmp_path, "text_editor")
