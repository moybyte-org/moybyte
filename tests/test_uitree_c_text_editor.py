"""The Editor's text editor on the C engine: the store places it by the
one rule (whole lines), its document stays Python's and the front end
draws its rows in the rect (`uitree_editor.Engine._r_text_editor`), what
its templates read of the document (`{code.line}`) is a row the store
holds, and the arrows and typed bytes reach the document ahead of the
store's keys. It draws the pixels and leaves the state the reference engine
recorded (tests/uitree_reference/)."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent / "uitree_fixtures"))

import uitree_parity as P  # noqa: E402
import uitree_reference as R  # noqa: E402

CONFIGS = ("tdeck_320x240_fs1_dark", "p4_1024x600_fs2_windowed", "big_800x480_fs3_dark",
           "p4_1024x600_cs2_fs1_windowed")
# the caret down and across, letters typed into the document (a bare letter an action answers
# too: the text editor takes typed bytes first), the gutter off and on by its button (focused),
# the find shown by its chord, its matches marked as the caret walks, closed by Back; the menu
# popover opened at the caret, a row walked to and taken, opened again and closed by Back, opened
# and left by a typed letter, which the document takes
SCRIPT = ["k:down", "k:down", "k:right", "k:right", "t:z", "t:q", "k:down", "t:g", "t:g", "k:up",
          "k:a", "k:down", "k:a", "t:\x06", "k:down", "k:down", "back", "k:down", "t:\x05", "k:down", "enter", "t:\x05", "back",
          "t:\x05", "t:w"]


def _run(engine, config):
    import uitree_text_editor as F
    seen = {}
    cls = F.layer(engine)

    def opened(ws):
        seen["app"] = P.host(ws, cls)

    def probe(ws):
        e = seen["app"].engine
        d = e.s._v["code"]
        return "%s L%d:%d %s gutter=%s finding=%s picking=%s %r" % (
            e.focus_key, d.line, d.col, d.text().count("zq") + 10 * d.text().count("repr"),
            e.s._v["gutter_on"], e.s._v["finding"], e.s._v["picking"],
            [(k, e.rect_of(k)) for k in ("find_strip", "code", "status_row")])
    return seen, P.frames(config, P.Surface(opened, probe=probe), SCRIPT)


@pytest.mark.parametrize("config", CONFIGS)
def test_a_text_editor_draws_and_types_as_the_reference_did(config):
    import uitree_c
    _, c = _run(uitree_c.CEditorEngine, config)
    states = [f.state for f in c]
    assert " L1:1 0 " in states[0] and any("gutter=False" in t for t in states), states
    assert any("finding=True" in t and "('find_strip', (" in t for t in states), states
    assert any("picking=True" in t for t in states) and "picking=False" in states[-1], states
    assert any(t.split()[0].startswith("repr") or t.split()[0].startswith("range") for t in states), states
    R.held(__file__, "text_editor/" + config, c)


def test_two_shown_units_answering_one_chord_is_u08():
    """A typed chord two live actions answer: the checker proves no page
    shows both, and the C engine refuses it by name if one does."""
    import types
    import uitree as S
    import uitree_c
    eng = uitree_c.CEditorEngine.__new__(uitree_c.CEditorEngine)
    eng._acts = [types.SimpleNamespace(name=n, keys=("ctrl+s",), live=lambda: True) for n in ("save", "store")]
    with pytest.raises(S.UIError) as e:
        eng._chord(S._chord_byte("ctrl+s"))
    assert e.value.code == "U08" and "save and store" in str(e.value)
