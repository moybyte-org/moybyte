"""The layer's own refusals, raised at the offending call outside the
checker: a build that reads, writes or misplaces (B01-B03), a declaration
with no type (S13), a model field named `key` (S07), an enum's `init=`
(E01-E03), a retired `takes="change"` (N04) and a command outside a
handler (A10)."""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import runtime  # noqa: E402,F401  -- the flat module namespace
import uitree as ui  # noqa: E402


class _App(ui.Builder):
    def __init__(self, decl, build):
        self.decl = decl
        self.actions = decl.actions
        self.init_builder(decl.state, build)


def _code(fn):
    with pytest.raises(ui.UIError) as e:
        fn()
    assert e.value.fix, e.value.text()
    return e.value.code


def _build(page):
    return _App(ui.decl_of(__name__), page)._build()


def nothing():
    pass


def test_a_build_reads_writes_and_misplaces():
    s = ui.state(count=0, name="")
    ui.action("add", "Add", nothing)

    def reads():
        with ui.page("P"):
            ui.label(s.name)

    def writes():
        with ui.page("P"):
            s.count = 1

    def commands():
        with ui.page("P"):
            ui.focus("add")

    def pushes():
        with ui.page("P"):
            ui.push(reads)

    def asks():
        with ui.page("P"):
            ui.text_size("x")

    def declares():
        with ui.page("P"):
            ui.hook("late", nothing)

    def no_page():
        ui.label("x")

    assert _code(lambda: _build(reads)) == "B01"
    assert _code(lambda: _build(asks)) == "B01"
    assert _code(lambda: _build(writes)) == "B02"
    assert _code(lambda: _build(commands)) == "B02"
    assert _code(lambda: _build(pushes)) == "B02"
    assert _code(lambda: _build(declares)) == "B03"
    assert _code(lambda: _build(no_page)) == "B03"
    assert _code(lambda: ui.label("x")) == "B03"


def test_a_declaration_with_no_type_is_s13():
    assert _code(lambda: ui.state(pick=None)) == "S13"
    assert _code(lambda: ui.state(items=[])) == "S13"
    assert _code(lambda: ui.state(fn=nothing)) == "S13"
    assert _code(lambda: ui.state(m=ui.opt(ui.model(key="id", id="")))) == "S13"


def test_a_model_field_named_key_is_refused():
    with pytest.raises(TypeError):          # the spelling cannot say it
        ui.model("id", id="", **{"key": ""})
    assert _code(lambda: ui.Model("id", {"id": "", "key": ""})) == "S07"


def test_an_enum_names_its_initial_value():
    assert _code(lambda: ui.enum("a", "b")) == "E01"
    assert _code(lambda: ui.enum("a", "b", init="c")) == "E02"
    assert _code(lambda: ui.enum("a", init="a")) == "E03"
    assert ui.enum("a", "b", init="b").init == "b"


def test_takes_change_is_retired():
    ui.state(on=False)
    assert _code(lambda: ui.hook("moved", lambda new, old: None, takes="change")) == "N04"


def test_a_command_outside_a_handler_is_a10():
    ui.state(on=False)
    for call in (lambda: ui.focus("x"), lambda: ui.push(nothing), ui.pop,
                 lambda: ui.after(10, "x"), lambda: ui.text_size("x")):
        assert _code(call) == "A10"
