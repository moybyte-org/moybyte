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


def test_input_is_a_system_value_of_four_words():
    """@input (spec r3 §14): the input in use, an enum of the toolkit's
    words, read by templates and conditions; an engine's replay sets it."""
    ui.state(on=False)
    t, get = ui._ref("@input", ui.Ctx(), ("t", 1))
    assert isinstance(t, ui.Enum) and t.values == ("keys", "tdeck", "pad", "touch")
    assert get(None) == "keys"
    s = ui.state(on=False)

    def page():
        with ui.page("P"):
            ui.label("{@input}")
            ui.label("Tap", shown="@input=touch")
    app = _App(ui.decl_of(__name__), page)
    app._build()
    assert app.root.kids[1].p["shown"](None) is False
    app.input = "touch"
    ui._NOW.app = app                   # as the replay evaluates: with its engine in force
    try:
        assert app.root.kids[0].p["text"](None) == "touch"
        assert app.root.kids[1].p["shown"](None) is True
    finally:
        ui._NOW.app = None
    del s


def test_the_retired_names_are_n04_with_their_replacement():
    """§7.9: each retired verb, prop and system value names what replaced it."""
    ui.state(on=False)
    ui.action("go", "Go", nothing)
    assert _code(lambda: ui.text("x")) == "N04"
    assert _code(lambda: ui.scope("k", ["on"], nothing)) == "N04"
    assert _code(ui.has_symbols) == "N04"
    with pytest.raises(ui.UIError) as e:
        ui.action("stop", "Stop", nothing, key="s")
    assert e.value.code == "N04" and "keys=('s',)" in e.value.fix
    for page in (lambda: ui.button("go", disabled="on"), lambda: ui.link("go", ink="dim"),
                 lambda: ui.label("{@symbols?×:x}")):
        def build(page=page):
            with ui.page("P"):
                page()
        assert _code(lambda: _build(build)) == "N04"


def test_keys_take_letters_chords_and_roles_and_one_name_is_one_thing():
    """§13.2: a letter is a key; §5.1: a state and an action of one name is
    U02; an action's icon is a name, never a template (I02)."""
    ui.state(go_word="", n=0)
    ui.action("mark", "Mark", nothing, keys=("m", "ctrl+m", "role:left"), icon="check")
    assert ui._d().actions["mark"].letters == "m"
    assert _code(lambda: ui.action("other", "Other", nothing, keys="m")) == "K03"
    assert _code(lambda: ui.action("other", "Other", nothing, keys=("mm",))) == "K03"
    assert _code(lambda: ui.action("n", "N", nothing)) == "U02"
    assert _code(lambda: ui.action("other", "Other", nothing, icon="{n}")) == "I02"
    assert _code(lambda: ui.action("other", "Other", nothing, nope=1)) == "N01"


def test_the_display_kinds_check_their_props():
    ui.state(n=0, log=ui.model(key="n", n=0), text="")

    def bad(call):
        def build():
            with ui.page("P"):
                call()
        return _code(lambda: _build(build))
    assert bad(lambda: ui.label("x", size="big")) == "N01"
    assert bad(lambda: ui.label("x", lines=0)) == "N01"
    assert bad(lambda: ui.label("x", align="left")) == "N01"
    assert bad(lambda: ui.label("x", face="huge")) == "N01"
    assert bad(lambda: ui.progress("n", max=5, look="bar")) == "N01"
    assert bad(lambda: ui.note("one line")) == "N01"
    assert bad(lambda: ui.text_view("log")) == "T03"
    assert bad(lambda: ui.text_view("n")) == "T03"
