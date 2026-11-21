"""The Editor's half of `uitree`: the document a `ui.doc` declares
(runtime/uitree/uitree_doc.py) under its verbs, and the Editor's replay
(runtime/uitree/uitree_ref_editor.py) hosting an app built from tabs, a text
editor, a find strip, a popover hung from the caret and panes, on every
size the shell goldens draw."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent / "uitree_fixtures"))

import uitree_parity as P  # noqa: E402
import test_shell_goldens as G  # noqa: E402

SIZES = sorted({(c["sys_size"], c["font_scale"], c["windowed"]): name
                for name, c in sorted(G.CONFIGS.items(), reverse=True)}.values())


def _buf(text="a = 1\nb = 2\n", lang="python"):
    import uitree_doc
    return uitree_doc.DocBuf(text, lang)


def test_typing_is_one_burst_that_one_undo_takes_back():
    d = _buf()
    assert (d.line, d.col, d.lines) == (1, 1, 3)
    assert not d.can_undo and not d.can_redo
    for c in b"xy":
        assert d.key(c)
    assert d.text().startswith("xya = 1") and d.can_undo
    assert d.undo() and d.text() == "a = 1\nb = 2\n"
    assert d.can_redo and d.redo() and d.text().startswith("xya")


def test_enter_and_punctuation_close_a_burst():
    d = _buf("")
    for c in b"ab.cd":
        d.key(c)
    assert d.text() == "ab.cd"
    d.undo()
    assert d.text() == "ab."
    d.undo()
    assert d.text() == ""


def test_find_counts_against_the_caret_and_moves_it():
    d = _buf("x\nx x\nx\n")
    d.find_q = "x"
    assert (d.match, d.matches) == (1, 4)
    assert d.find("x", True)
    assert (d.line, d.col) == (2, 1) and d.match == 2
    d.go(3)
    assert (d.line, d.col, d.match) == (3, 1, 4)


def test_go_place_select_and_the_word_before_the_caret():
    d = _buf("def spawn():\n    spa\n")
    d.go(2, 8)
    assert (d.line, d.col) == (2, 8) and d.word == "spa"
    d.complete("spawn")
    assert d.text().splitlines()[1] == "    spawn"
    d.select(1, 1, 1, 4)
    assert d.sel == 3
    d.place(1, 99)
    assert (d.line, d.col, d.sel) == (1, 13, 0)
    assert d.defs() == [("spawn", 1)]


def test_set_text_clears_the_journal():
    d = _buf()
    d.key(ord("z"))
    d.set_text("new\n")
    assert d.text() == "new\n" and not d.can_undo and d.lines == 2


def test_a_handler_changes_a_document_only_through_its_verbs():
    import uitree as S
    import uitree_editor_app as E
    S._NOW.building = False
    with pytest.raises(S.UIError) as e:
        E.s.code.line = 3
    assert e.value.code == "S03"
    with pytest.raises(S.UIError) as e:
        E.s.code.no_such
    assert e.value.code == "S04"


def test_app_layer_takes_only_an_engine_class():
    import uitree as S
    import uitree_app
    import uitree_ref_editor
    import uitree_editor_app as E
    cls = E.layer()
    assert cls.ENGINE == (uitree_ref_editor.Engine,)
    with pytest.raises(S.UIError) as e:
        uitree_app.app_layer("x", "X", E.build, engine=object)
    assert e.value.code == "N01"


def _open(seen):
    import uitree_editor_app as E

    def opened(ws):
        seen["app"] = P.host(ws, E.layer())
    return opened


@pytest.mark.parametrize("config", SIZES)
def test_an_editor_app_types_finds_completes_and_plays(config, tmp_path):
    """The text editor takes typed keys and arrows with no app code, Ctrl+F
    opens the find strip whose field searches as it is typed and Back
    closes it, Ctrl+E hangs completion from the caret's word and A uses
    the row, and Play marks a syntax error's line."""
    import uitree_editor_app as E
    seen = {}
    script = ["k:down", "k:down", "k:right", "k:right", "k:right", "k:right",
              "t:\x06", "t:x", "back",
              "k:down", "k:down", "k:down", "k:down", "k:down", "k:down", "k:down",
              "t:\x0dsp", "t:\x05", "k:a",
              "t:(", "t:\x12"]
    fs = P.frames(config, _open(seen), script, str(tmp_path / "carts"))
    eng = seen["app"].engine
    trace = eng.trace_text()
    assert type(eng).__name__ == "Engine" and type(eng).__module__.endswith("uitree_ref_editor")
    assert "action  find " in trace and "action  find_close from back on row 'find_strip'" in trace, trace
    assert "action  complete_open" in trace and "action  complete" in trace, trace
    assert "action  play" in trace and E.s.played == 1, trace
    lines = E.s.code.text().splitlines()
    assert lines[9:] == ["", "spr(    spr(1, x, 110)"], (lines, trace)       # Enter, "sp", completed, "("
    assert len(E.s.marks) == 1 and E.s.marks[0].kind == "error", trace
    assert not E.s.finding and not E.s.completing
    for i in (1, 7, 8, 9, 17, 18, 19, 20, 21):      # each of these steps drew a new frame
        assert fs[i].buf != fs[i - 1].buf, (i, fs[i].step, trace)


@pytest.mark.parametrize("config", [c for c in SIZES if G.CONFIGS[c]["windowed"]])
def test_a_desk_editor_lays_its_panes_and_keeps_the_held_size(config, tmp_path):
    import uitree_editor_app as E
    seen = {}
    P.frames(config, _open(seen), ["f:1"], str(tmp_path / "carts"))
    eng = seen["app"].engine
    assert "code_pane.head" in eng.anchors and "code" in eng.views
    (sid, divs), = eng.dividers.items()
    assert sid == "split:code_pane/side" and len(divs) == 1
    assert E.s.side_w is None
