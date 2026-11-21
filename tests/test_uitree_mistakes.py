"""One mistake per error code both layers raise, made once in Python and once
in Lua: each layer's checker reports that code at the mistake's line, in the
spec's one-line message (`error[CODE] FILE:LINE: WHAT; fix: FIX ...`, §25.1).

The Lua halves are tests/test_uitree_lua.py's CASES (one per code the Lua
layer raises); the Python halves are PY below. SHARED is every code of the
contract table (tests/uitree_contract/contract.json) both layers' sources
raise, and every one of them has a pair here, or is listed in NO_PAIR with
the reason.
"""
import re

import pytest

import test_uitree_contract as K
import test_uitree_lua as L
import uitree_lua_check as lua_checker

H = "import uitree as ui\n"
NOTHING = "def nothing():\n    pass\n"
PAGE = "def build():\n    with ui.page(\"P\"):\n"

# code: (app.py, the line its error names)
PY = {
    "A01": (H + "s = ui.state(n=0)\n" + PAGE + "        ui.link(\"nope\")\n", 5),
    "A02": (H + NOTHING + "s = ui.state(m=ui.model(key=\"id\", id=\"\"), cur=ui.row_of(\"m\"))\n"
            "ui.action(\"go\", \"Go\", nothing)\n" + PAGE + "        with ui.rows(\"m\", cur=\"cur\"):\n"
            "            ui.link(\"go\", label=\"{.id}\")\n", 9),
    "A03": (H + "s = ui.state(n=0)\ndef go(a, b):\n    pass\nui.action(\"go\", \"Go\", go)\n", 5),
    "A08": (H + NOTHING + "s = ui.state(n=0)\nui.action(\"go\", \"\", nothing)\n", 5),
    "A10": (H + NOTHING + "s = ui.state(n=0)\nui.action(\"go\", \"Go\", nothing)\nui.after(100, \"go\")\n", 6),
    "B01": (H + "s = ui.state(n=\"\")\n" + PAGE + "        ui.label(s.n)\n", 5),
    "B02": (H + "s = ui.state(n=0)\n" + PAGE + "        s.n = 1\n", 5),
    "B03": (H + NOTHING + "s = ui.state(n=0)\n" + PAGE + "        ui.hook(\"late\", nothing)\n", 7),
    "C01": (H + "s = ui.state(n=0)\n" + PAGE + "        ui.label(\"x\", shown=\"{n}\")\n", 5),
    "C02": (H + "s = ui.state(a=False, b=False, c=False)\n" + PAGE
            + "        ui.label(\"x\", shown=\"a && b || c\")\n", 5),
    "C03": (H + "s = ui.state(a=False, b=False, c=False)\n" + PAGE
            + "        ui.label(\"x\", shown=\"a && (b || c)\")\n", 5),
    "E01": (H + "s = ui.state(e=ui.enum(\"a\", \"b\"))\n", 2),
    "E02": (H + "s = ui.state(e=ui.enum(\"a\", \"b\", init=\"c\"))\n", 2),
    "E03": (H + "s = ui.state(e=ui.enum(\"a\", init=\"a\"))\n", 2),
    "H01": (H + NOTHING + "s = ui.state(n=0)\nui.hook(\"tick\", nothing)\n" + PAGE + "        ui.link(\"tick\")\n", 8),
    "K02": (H + NOTHING + "s = ui.state(n=0)\nui.action(\"go\", \"Go\", nothing, keys=[\"s\"])\n"
            "ui.action(\"stop\", \"Stop\", nothing, keys=[\"s\"])\n", 6),
    "I02": (H + NOTHING + "s = ui.state(n=0)\nui.action(\"go\", \"Go\", nothing, icon=3)\n", 5),
    "K03": (H + NOTHING + "s = ui.state(n=0)\nui.action(\"go\", \"Go\", nothing, keys=[\"ctrl+shift+x\"])\n", 5),
    "N01": (H + "s = ui.state(n=0)\n" + PAGE + "        ui.label(\"x\", nope=1)\n", 5),
    "N02": (H + NOTHING + "s = ui.state(n=0)\nui.action(\"go\", \"Go\", nothing)\n" + PAGE
            + "        ui.link(\"go\")\n        ui.link(\"go\")\n", 9),
    "N04": (H + NOTHING + "s = ui.state(n=0)\nui.hook(\"moved\", nothing, takes=\"change\")\n", 5),
    "O01": (H + NOTHING + "s = ui.state(a=False)\nui.action(\"go\", \"Go\", nothing, when=\"a\")\n", 5),
    "O02": (H + NOTHING + "s = ui.state(a=False)\nui.action(\"go\", \"Go\", nothing, off=\"grey\")\n", 5),
    "S01": (H + "s = ui.state(n=0)\nx = s.nope\n", 3),
    "S03": (H + "s = ui.state(m=ui.model(key=\"id\", id=\"\", label=\"\"))\ndef pick(row):\n"
            "    row.label = \"x\"\nui.action(\"pick\", \"Pick\", pick, takes=\"row\")\n", 4),
    "S07": (H + "s = ui.state(m=ui.model(key=\"id\", name=\"\"))\n", 2),
    "S11": (H + "s = ui.state(cur=ui.row_of(\"nets\"))\n", 2),
    "S13": (H + "s = ui.state(pick=None)\n", 2),
    "T01": (H + "s = ui.state(n=0)\n" + PAGE + "        ui.label(\"{n|nope}\")\n", 5),
    "T02": (H + "s = ui.state(n=0)\n" + PAGE + "        ui.label(\"{nope}\")\n", 5),
    "T03": (H + "s = ui.state(n=0)\n" + PAGE + "        ui.toggle(\"On\", \"n\")\n", 5),
    "T04": (H + "s = ui.state(e=ui.enum(\"a\", \"b\", init=\"a\"))\nui.labels(\"L\", {\"a\": \"A\"})\n" + PAGE
            + "        ui.label(\"{e|map L}\")\n", 6),
    "T05": (H + "s = ui.state(n=0)\n" + PAGE + "        ui.label(\"{.x}\")\n", 5),
    "U02": (H + NOTHING + "s = ui.state(go=0)\nui.action(\"go\", \"Go\", nothing)\n", 5),
    "D01": (H + "s = ui.state(n=0)\nui.app(\"T\", icon=\"calc\")\nui.app(\"T2\", icon=\"calc\")\n", 4),
    "D02": (H + "s = ui.state(n=0)\nui.app(\"T\", icon=\"calc\", size=3)\n", 3),
    "A09": (H + NOTHING + "s = ui.state(n=0)\nui.action(\"go\", \"Go\", nothing, takes=\"all\")\n", 5),
}

# codes both layers raise that have no pair here, and why
NO_PAIR = {
    "P05": "raised by a handler's ui.pop() on the first page, which no checker run fires; cased in "
           "tests/test_uitree_codes.py (Python) and tests/test_uitree_lua.py (Lua)",
    "N06": "Lua raises it in a frame, for a popover's anchor no drawn node has (cased on the console in "
           "tests/test_uitree_lua.py); Python's anchor is checked at its own stage: no checker run reaches both",
    "N08": "Lua raises it in a frame (cased on the console in tests/test_uitree_lua.py), Python at run for "
           "two shown nodes of one key: no checker run reaches either",
    "D04": "Lua's checker raises it for a cart with no app declaration; Python's at hosting "
           "(uitree_app.app_layer with no title), which its checker never reaches: a Python module "
           "is not an app until something hosts it",
    "N03": "the two layers raise it for different misplacements (Python: a node after the page's scroll; "
           "Lua: rows in rows); no one mistake reaches both",
    "P01": "Python's checker raises it for a module with no page function, Lua's for a push naming no page: "
           "Python pages are functions, not names (L2)",
    "S02": "Lua raises it at run (ui._fire), Python's checker reads it from a literal write; one mistake, "
           "two stages: paired when a Python run case exists",
    "S04": "Lua raises it at run for a record's field; Python's at run for a row's: no shared spelling yet",
    "S06": "Python raises it for a write from a draw or a service; Lua for a top-level write: no shared "
           "mistake yet",
}


def shared():
    t = set(K.TABLE["codes"])
    return sorted(K._codes(K.PY_FILES) & K._codes(K.LUA_FILES) & t)


def test_every_shared_code_has_a_pair_or_a_reason():
    lua = {c.split()[0] for c in L.CASES}
    for code in shared():
        assert code in NO_PAIR or (code in PY and code in lua), code
    assert not set(NO_PAIR) & set(PY)


ONE_LINE = re.compile(r"^error\[[A-Z]\d\d\] [^:\s]+:\d+: .+; fix: .+$")


@pytest.mark.parametrize("code", sorted(PY))
def test_python_reports_the_mistake_at_its_line(code, tmp_path):
    import uitree_check
    src, line = PY[code]
    path = tmp_path / ("m_%s.py" % code.lower())
    path.write_text(src)
    r = uitree_check.check(str(path))
    got = [(e.code, e.where[1]) for e in r.errors]
    assert (code, line) in got, "%s:%d not in %r\n%s" % (code, line, got, r.text())
    for e in r.errors:
        assert ONE_LINE.match(e.text()), e.text()


@pytest.mark.parametrize("code", sorted(PY))
def test_lua_reports_the_mistake_at_its_line(code, tmp_path):
    main, line, fire = L.CASES[code]
    d = L._cart(str(tmp_path), "case", main, {"fire.lua": fire} if fire else None)
    errors = [e for e in lua_checker.check(d, after=["fire.lua"] if fire else ()) if e.startswith("error[")]
    where = "load" if line == "load" else "main.lua:%d" % line
    assert any(e.startswith("error[%s] %s:" % (code, where)) for e in errors), errors
    for e in errors:
        assert ONE_LINE.match(e), e
