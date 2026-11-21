"""The C engine's board build mode (`uitree_c.BUILD = "direct"`): the build's
calls keep each template's and condition's source and C compiles it at
lowering. Every build-stage mistake the reference raises is raised there too,
with the same code: the template and condition codes from C's compiler, the
rest from the Python checks the build still makes.

- the planted mistakes of tests/test_uitree_mistakes.py's PY table that the
  reference raises at build, each built on both;
- CASES: one mistake per check the reference's compiler makes (`tmpl`,
  `cond`, `_ref`, `_lit`, `_prop`) on a kind the C engine draws.

C_DIFFERS lists each case where C answers otherwise, with what each answers:
a case listed must still differ (so the list only shrinks).
"""
import importlib.util
import sys

import pytest

STATE = '''import uitree as ui

s = ui.state(n=0, x=1.5, t="", b=False, e=ui.enum("a", "b", init="a"),
             m=ui.model(key="id", id="", label="", k=0), sel=ui.row_of("m"))


def go(row):
    pass


ui.action("go", "Go", go, takes="row")
ui.labels("L_e", {"a": "A", "b": "B"})
ui.labels("L_e1", {"a": "A"})
ui.labels("L_e3", {"a": "A", "b": "B", "c": "C"})
ui.labels("L_b", {True: "On", False: "Off"})
ui.labels("L_n", {"_": "some", 1: "one"})
ui.labels("L_n0", {1: "one"})


def build():
    with ui.page("P"):
'''

ROWS = 'with ui.rows("m", cur="sel"):\n            '

# id: the page's body (one line, inside the page)
CASES = {
    # templates
    "open brace": 'ui.label("{n")',
    "lone close": 'ui.label("n}")',
    "no state": 'ui.label("{nope}")',
    "no system value": 'ui.label("{@nope}")',
    "retired system value": 'ui.label("{@w}")',
    "field outside a list": 'ui.label("{.label}")',
    "current outside a list": 'ui.label("{@current}")',
    "a bool alone": 'ui.label("{b}")',
    "a bool in a text": 'ui.label("at {b}")',
    "a model alone": 'ui.label("{m}")',
    "a field of an int": 'ui.label("{n.x}")',
    "no field of a row_of": 'ui.label("{sel.nope}")',
    "no transform": 'ui.label("{n|nope}")',
    "an empty transform": 'ui.label("{n|}")',
    "two transforms": 'ui.label("{n|fmt %d|plural a,b}")',
    "count of an int": 'ui.label("{n|count}")',
    "bytes of a text": 'ui.label("{t|bytes}")',
    "bytes not last": 'ui.label("{n|bytes|fmt %d}")',
    "fmt with no %": 'ui.label("{n|fmt d}")',
    "fmt of no conversion": 'ui.label("{n|fmt %q}")',
    "fmt %d of a text": 'ui.label("{t|fmt %d}")',
    "fmt of two specs": 'ui.label("{n|fmt %d%d}")',
    "fmt %s of an int": 'ui.label("{n|fmt %5s}")',
    "fmt %.1f of a number": 'ui.label("{x|fmt %.1f}")',
    "count then plural": 'ui.label("{m|count|plural row,rows}")',
    "no label table": 'ui.label("{n|map Nope}")',
    "a table short of the enum": 'ui.label("{e|map L_e1}")',
    "a table past the enum": 'ui.label("{e|map L_e3}")',
    "an enum's table": 'ui.label("{e|map L_e}")',
    "a bool's table without True": 'ui.label("{b|map L_n}")',
    "a bool's table": 'ui.label("{b|map L_b}")',
    "an int's table without _": 'ui.label("{n|map L_n0}")',
    "an int's table": 'ui.label("{n|map L_n}")',
    "plural of a text": 'ui.label("{t|plural a,b}")',
    "plural of one form": 'ui.label("{n|plural a}")',
    "plural with an empty form": 'ui.label("{n|plural a,}")',
    "a choice of one branch": 'ui.label("{b?yes}")',
    "a choice of three branches": 'ui.label("{b?a:b:c}")',
    "a choice in a branch": 'ui.label("{b?x {n=1?y:z} w:v}")',
    "a choice of references": 'ui.label("{b?{t}:{n}}")',
    "an enum's truth": 'ui.label("{e?a:b}")',
    "an enum's no value": 'ui.label("{e=c?a:b}")',
    "an enum's value": 'ui.label("{e=b?a:b}")',
    "a bool compared": 'ui.label("{b=1?a:b}")',
    "an int compared to a word": 'ui.label("{n=x?a:b}")',
    "a number compared to a word": 'ui.label("{x=a?a:b}")',
    "a model compared": 'ui.label("{m=1?a:b}")',
    "a text compared": 'ui.label("{t=x?a:b}")',
    "a non-text label": 'ui.label(3)',
    "an info's bool value": 'ui.info("Name", "{b}")',
    "a toggle's bool label": 'ui.toggle("{b}", "b")',
    "an escaped colon": 'ui.label("{b?a\\\\:b:c}")',
    # conditions
    "a template condition": 'ui.label("x", shown="{b}")',
    "a choice condition": 'ui.label("x", shown="b?x")',
    "&& and || mixed": 'ui.label("x", shown="b && n || t")',
    "a single &": 'ui.label("x", shown="b & n")',
    "brackets": 'ui.label("x", shown="(b && n)")',
    "an empty condition": 'ui.label("x", shown="")',
    "a number condition": 'ui.label("x", shown=3)',
    "brackets in a condition": 'ui.label("x", shown="(b)")',
    "no state in a condition": 'ui.label("x", shown="nope")',
    "an enum's truth in a condition": 'ui.label("x", shown="e")',
    "an enum's no value in a condition": 'ui.label("x", shown="e=z")',
    "a bool compared in a condition": 'ui.label("x", shown="b=1")',
    "an int compared to a word in a condition": 'ui.label("x", shown="n=x")',
    "a number compared to a word in a condition": 'ui.label("x", shown="x=abc")',
    "@input's truth": 'ui.label("x", shown="@input")',
    "@input's no value": 'ui.label("x", shown="@input=foo")',
    "@input's value": 'ui.label("x", shown="@input=touch")',
    "an empty term": 'ui.label("x", shown="b && ")',
    "a field outside a list in a condition": 'ui.label("x", shown=".id")',
    "a negated comparison": 'ui.label("x", shown="!n!=3")',
    # props and rows
    "a slider's text lo": 'ui.slider("S", "n", "{t}", 10)',
    "a slider's choice lo": 'ui.slider("S", "n", "{b?1:2}", 10)',
    "a slider's int-text lo": 'ui.slider("S", "n", "5", 10)',
    "a row's missing field": ROWS + 'ui.link("go", label="{.nope}")',
    "a row's field": ROWS + 'ui.link("go", label="{.k|plural tap,taps}")',
    "a row's bool": ROWS + 'ui.link("go", label="{.label} {b}")',
    "a list's bool empty": 'ui.rows("m", empty="{b}")',
}

# a case where C answers otherwise: (the reference's code, C's code or None
# for no error, or "gap" for an EngineGap)
C_DIFFERS = {
    # the reference refuses the TEXT "5" as an int prop; a lowered int literal
    # is that same text, so C takes it
    "a slider's int-text lo": ("T03", None),
}


def _load(name, src, tmp_path):
    path = tmp_path / (name + ".py")
    path.write_text(src)
    sys.modules.pop(name, None)
    spec = importlib.util.spec_from_file_location(name, str(path))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


class _Damage:
    def all(self):
        pass


def _answer(engine, mod):
    """The code a build of `mod`'s page raises on `engine` ("reference" or
    "c": the C engine in the direct build), and its line; (None, 0) when it
    builds."""
    import ui_draw
    import uitree as S
    import uitree_app
    import uitree_c
    cls = uitree_c.CEngine if engine == "c" else uitree_app.Engine
    eng = cls(S.decl_of(mod), mod.build, ui_draw.AppHost(), None, _Damage())
    try:
        if engine == "c":
            assert eng.direct
            eng._ready()
        else:
            eng._build()
    except uitree_c.EngineGap:
        return "gap", 0
    except S.UIError as e:
        return e.code, (e.where or ("?", 0))[1]
    return None, 0


def _both(name, src, tmp_path):
    return (_answer("reference", _load(name + "_ref", src, tmp_path)),
            _answer("c", _load(name + "_c", src, tmp_path)))


@pytest.mark.parametrize("case", sorted(CASES))
def test_a_mistake_raises_its_code_on_the_direct_build(case, tmp_path):
    src = STATE + "        " + CASES[case] + "\n"
    (ref, ref_line), (c, c_line) = _both("direct_%d" % sorted(CASES).index(case), src, tmp_path)
    if case in C_DIFFERS:
        assert (ref, c) == C_DIFFERS[case], "C now answers as the reference: drop the case from C_DIFFERS"
        return
    assert c == ref, (ref, c)
    if ref is not None:
        assert c_line == ref_line, "the direct build names the mistake's line on the host"


def _build_stage():
    import test_uitree_contract as K
    import test_uitree_mistakes as M
    return sorted(code for code in M.PY if "build" in K.TABLE["codes"][code]["stage"])


@pytest.mark.parametrize("code", _build_stage())
def test_the_planted_mistakes_raise_their_codes_on_the_direct_build(code, tmp_path):
    import test_uitree_mistakes as M
    import uitree as S
    src, line = M.PY[code]
    try:
        (ref, ref_line), (c, c_line) = _both("planted_" + code.lower(), src, tmp_path)
    except S.UIError as e:            # the module's declarations: the same Python on every engine
        assert e.code == code
        return
    if ref != code:
        return                        # raised at another stage than the page's build
    assert (c, c_line) == (ref, ref_line)


def test_the_direct_build_skips_the_python_compile(tmp_path):
    """A direct build's nodes keep their sources and compile nothing in Python."""
    import ui_draw
    import uitree as S
    import uitree_c
    mod = _load("direct_skip", STATE + '        ui.label("{n|plural tap,taps}", shown="b")\n', tmp_path)
    eng = uitree_c.CEngine(S.decl_of(mod), mod.build, ui_draw.AppHost(), None, _Damage())
    eng._build()
    label = eng.root.kids[0]
    assert label.p == {} and label.src == {"text": "{n|plural tap,taps}", "shown": "b"}
