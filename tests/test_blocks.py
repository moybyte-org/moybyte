"""Tests for the v0.4 block-programming model + blocks->Python compiler (issue #29,
Part 1). Covers: the schema round-trips through blocks.json; the vocabulary catalog
is well-formed; compile_blocks emits valid, MicroPython-safe Python that runs as a
cart; and every Scratch-style category has at least one compiled-and-runs case. No
editor UI is exercised here (that's Part 2)."""

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from runtime import blocks  # noqa: E402
from runtime import kid_carts  # noqa: E402


# ----------------------------------------------------------------------------
# Helpers: build programs from the model, exec generated carts against a fake API
# ----------------------------------------------------------------------------

mk = blocks.make_block


def _program(vars_, scripts):
    return {"vars": list(vars_), "scripts": scripts}


class _FakeAPI(dict):
    """A cart namespace: the injected v0.4 API verbs as recording stubs, so a
    compiled cart can exec + run _init/_update/_draw headlessly with no display.
    Every draw/input/sound verb the catalog can emit is present here."""

    def __init__(self):
        super().__init__()
        self.calls = []
        # screen dims (carts read W/H like the real namespace)
        self["W"] = 320
        self["H"] = 240
        for name in ("cls", "pix", "line", "rect", "rectb", "circ", "circb",
                     "spr", "print", "sfx", "beep", "music"):
            self[name] = self._rec(name)
        from runtime import palette
        self["col"] = palette.color        # faithful name/index -> 0-63 resolution
        self["btn"] = lambda d=None: self._btn.get(d, False)
        self["btnp"] = lambda d=None: self._btnp.get(d, False)
        self["touch"] = lambda: self._touch
        self["rnd"] = lambda n=1.0: 0.0       # deterministic for tests
        self["flr"] = lambda x: int(x // 1)
        self._btn = {}
        self._btnp = {}
        self._touch = None

    def _rec(self, name):
        def fn(*a, **k):
            self.calls.append((name, a, k))
        return fn


def _run_cart(src, frames=1, fake=None):
    """Compile-check, exec the cart source, run _init once and _update/_draw for
    `frames`. Returns (namespace, fake_api). Raises on any error (the test asserts
    it runs clean)."""
    code = compile(src, "<cart>", "exec")     # (a) it parses
    fake = fake or _FakeAPI()
    exec(code, fake)                          # module-level defs + var inits
    if fake.get("_init"):
        fake["_init"]()
    for _ in range(frames):
        if fake.get("_update"):
            fake["_update"](1 / 30)
        if fake.get("_draw"):
            fake["_draw"]()
    return fake, fake


def _assert_micropython_safe(src):
    """(b) MicroPython-safe: no f-strings and no forbidden builtins, asserted via
    a source/AST scan (the same spirit as kidcode_cli/portable.py)."""
    assert "f'" not in src and 'f"' not in src, "f-string in generated source"
    tree = ast.parse(src)
    forbidden = {"eval", "exec", "getattr", "setattr", "compile", "open",
                 "__import__", "globals", "locals", "vars", "dir", "input"}
    for node in ast.walk(tree):
        # no f-strings anywhere (belt-and-braces over the textual check)
        assert not isinstance(node, ast.JoinedStr), "f-string node in generated source"
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            assert node.func.id not in forbidden, "forbidden call: " + node.func.id


# ----------------------------------------------------------------------------
# Catalog / schema
# ----------------------------------------------------------------------------

def test_catalog_is_wellformed():
    # every block has a category in the known set, a known shape, and the right
    # emit/expr key for its shape; every slot has a name + a known type.
    cats = set(blocks.CATEGORY_ORDER)
    for tid, d in blocks.CATALOG.items():
        assert d["category"] in cats, tid
        assert d["shape"] in (blocks.SHAPE_HAT, blocks.SHAPE_STATEMENT,
                              blocks.SHAPE_CBLOCK, blocks.SHAPE_EXPR), tid
        if d["shape"] == blocks.SHAPE_EXPR:
            assert "expr" in d, tid
        elif d["shape"] in (blocks.SHAPE_STATEMENT, blocks.SHAPE_CBLOCK):
            assert "emit" in d, tid
        for slot in d["slots"]:
            assert slot["name"] and slot["type"] in (
                blocks.SLOT_NUMBER, blocks.SLOT_TEXT, blocks.SLOT_VARIABLE,
                blocks.SLOT_DROPDOWN, blocks.SLOT_EXPR), (tid, slot)


def test_every_category_has_blocks():
    for cat in blocks.categories():
        assert blocks.blocks_in_category(cat), cat


def test_catalog_query_api():
    assert blocks.is_expr("op_add") and not blocks.is_expr("cls")
    assert blocks.is_cblock("if") and not blocks.is_cblock("spr")
    assert blocks.block_def("spr")["category"] == blocks.CAT_DRAW
    # a dropdown slot resolves its named option set
    color_slot = blocks.block_def("cls")["slots"][0]
    assert "white" in blocks.slot_options(color_slot)


def test_make_block_fills_defaults():
    b = mk("repeat")
    assert b["t"] == "repeat" and b["p"]["times"] == 10 and b["c"] == []
    # explicit params win; unknown-to-the-block keys are ignored
    s = mk("set_var", {"var": "x", "value": 5})
    assert s["p"] == {"var": "x", "value": 5} and "c" not in s


def test_schema_roundtrips_through_blocks_json():
    prog = _program(
        ["score"],
        [mk("on_start", children=[mk("set_var", {"var": "score", "value": 0})]),
         mk("on_draw", children=[mk("cls", {"color": "black"})])],
    )
    text = blocks.dumps(prog)
    again = blocks.loads(text)
    assert again == prog                      # exact round-trip
    # and a second pass is stable
    assert blocks.loads(blocks.dumps(again)) == prog


# ----------------------------------------------------------------------------
# Compiler: structure of the generated cart
# ----------------------------------------------------------------------------

def test_empty_program_compiles_to_runnable_cart():
    src = blocks.compile_blocks(blocks.empty_program())
    _assert_micropython_safe(src)
    fake, _ = _run_cart(src, frames=2)
    # all three lifecycle functions exist even when empty (bodies are `pass`)
    assert callable(fake["_init"]) and callable(fake["_draw"])
    assert fake["_update"].__code__.co_argcount == 1     # _update(dt)


def test_globals_hoisted_only_where_assigned():
    # x is assigned in _update -> _update gets `global x`; _draw only reads it -> none.
    prog = _program(
        ["x"],
        [mk("on_start", children=[mk("set_var", {"var": "x", "value": 0})]),
         mk("on_update", children=[
             mk("change_var", {"var": "x", "value": 2})]),
         mk("on_draw", children=[
             mk("spr", {"id": 0, "x": mk("var", {"var": "x"}), "y": 100})])],
    )
    src = blocks.compile_blocks(prog)
    assert "x = 0" in src                              # module-level init
    assert "    global x" in src                       # hoisted in _init/_update
    # _draw reads x but never assigns it -> no global there
    draw = src.split("def _draw")[1]
    assert "global" not in draw
    _assert_micropython_safe(src)
    _run_cart(src, frames=3)


# ----------------------------------------------------------------------------
# Compiler: control flow (if/else, nested repeat, forever, wait)
# ----------------------------------------------------------------------------

def test_if_else_compiles_and_branches():
    prog = _program(
        ["hit"],
        [mk("on_draw", children=[
            mk("if_else", {"cond": mk("op_gt", {"a": mk("var", {"var": "hit"}), "b": 0})},
               children=[
                   mk("cls", {"color": "red"}),
                   mk("else"),                          # boundary marker
                   mk("cls", {"color": "black"})])])],
    )
    src = blocks.compile_blocks(prog)
    assert "if (hit > 0):" in src and "    else:" in src
    _assert_micropython_safe(src)
    fake, _ = _run_cart(src)
    # hit defaults to 0, so the else branch ran: cls(col("black")) -> col -> 0
    assert ("cls", (0,), {}) in fake.calls


def test_nested_repeat_indentation():
    prog = _program(
        [],
        [mk("on_draw", children=[
            mk("repeat", {"times": 2}, children=[
                mk("repeat", {"times": 3}, children=[
                    mk("pix", {"x": 1, "y": 1, "color": "white"})])])])],
    )
    src = blocks.compile_blocks(prog)
    # distinct loop vars per nesting level, properly indented
    assert "    for _i1 in range(int(2)):" in src
    assert "        for _i2 in range(int(3)):" in src
    assert "            pix(1, 1, col(" in src
    _assert_micropython_safe(src)
    fake, _ = _run_cart(src)
    # 2 * 3 pix() calls
    assert sum(1 for c in fake.calls if c[0] == "pix") == 6


def test_forever_is_bounded_and_wait_is_noop():
    prog = _program(
        [],
        [mk("on_start", children=[mk("wait", {"secs": 1})]),
         mk("on_draw", children=[
             mk("forever", children=[mk("cls", {"color": "black"})])])],
    )
    src = blocks.compile_blocks(prog)
    assert "while True" not in src                     # never an unbounded loop
    assert "range(100000)" in src
    assert "def _wait(" in src                         # wait helper emitted
    _assert_micropython_safe(src)


# ----------------------------------------------------------------------------
# Compiler: expressions (operators, var refs, input readers, random)
# ----------------------------------------------------------------------------

def test_expression_tree_renders_with_precedence_parens():
    # (a + b) * c, nested expression blocks
    expr = mk("op_mul", {
        "a": mk("op_add", {"a": mk("var", {"var": "a"}), "b": mk("var", {"var": "b"})}),
        "b": mk("var", {"var": "c"})})
    prog = _program(["a", "b", "c", "out"],
                    [mk("on_update", children=[mk("set_var", {"var": "out", "value": expr})])])
    src = blocks.compile_blocks(prog)
    assert "out = ((a + b) * c)" in src
    _assert_micropython_safe(src)
    _run_cart(src)


def test_input_reader_expression_in_condition():
    prog = _program(
        ["x"],
        [mk("on_update", children=[
            mk("if", {"cond": mk("btn", {"dir": "left"})},
               children=[mk("change_var", {"var": "x", "value": -2})])])],
    )
    src = blocks.compile_blocks(prog)
    assert 'if btn("left"):' in src
    _assert_micropython_safe(src)
    # drive it with left held -> x moves
    fake = _FakeAPI()
    fake._btn = {"left": True}
    ns, _ = _run_cart(src, frames=1, fake=fake)
    assert ns["x"] == -2


def test_touched_helper_and_random():
    prog = _program(
        ["r"],
        [mk("on_update", children=[
            mk("if", {"cond": mk("touched")}, children=[
                mk("set_var", {"var": "r", "value": mk("op_rnd", {"n": 5})})])])],
    )
    src = blocks.compile_blocks(prog)
    assert "def _touched():" in src and "rnd(5)" in src
    _assert_micropython_safe(src)
    fake = _FakeAPI()
    fake._touch = (10, 10, True)               # a tap this frame
    _run_cart(src, frames=1, fake=fake)


# ----------------------------------------------------------------------------
# Every category: at least one compiled-and-runs case (the representative game)
# ----------------------------------------------------------------------------

def _the_little_game():
    """on_start sets a var; on_update moves it on a button; on_draw cls + spr.
    Touches events, control, draw, input, variables, operators, and sound."""
    return _program(
        ["x", "score"],
        [
            mk("on_start", children=[
                mk("set_var", {"var": "x", "value": 100}),
                mk("set_var", {"var": "score", "value": 0})]),
            mk("on_update", children=[
                # control + input + variables + operators
                mk("if", {"cond": mk("btn", {"dir": "right"})}, children=[
                    mk("change_var", {"var": "x", "value": 2}),
                    mk("change_var", {"var": "score", "value": 1})]),
                mk("if", {"cond": mk("btn", {"dir": "left"})}, children=[
                    mk("change_var", {"var": "x", "value": -2})]),
                # sound on a pressed edge
                mk("if", {"cond": mk("btnp", {"dir": "a"})}, children=[
                    mk("sfx", {"n": 0}),
                    mk("beep", {"freq": 440})])]),
            mk("on_draw", children=[
                mk("cls", {"color": "black"}),
                mk("spr", {"id": 0, "x": mk("var", {"var": "x"}), "y": 120}),
                mk("print", {"text": "HI", "x": 8, "y": 8, "color": "white"})]),
        ],
    )


def test_representative_game_compiles_safe_and_runs():
    src = blocks.compile_blocks(_the_little_game())
    # (a) parses, (b) MicroPython-safe
    _assert_micropython_safe(src)
    # (c) runs headlessly as a cart, moving on input
    fake = _FakeAPI()
    fake._btn = {"right": True}
    fake._btnp = {"a": True}
    ns, _ = _run_cart(src, frames=5, fake=fake)
    assert ns["x"] == 100 + 2 * 5              # moved right each frame
    assert ns["score"] == 5
    # sound + draw verbs actually fired
    names = {c[0] for c in fake.calls}
    assert {"cls", "spr", "print", "sfx", "beep"} <= names


def test_each_category_emits_runnable_code():
    # one block from each category, all in one program, compiled + run clean.
    samples = {
        blocks.CAT_EVENTS: None,   # the hats themselves below
        blocks.CAT_CONTROL: mk("repeat", {"times": 1}, children=[
            mk("pix", {"x": 0, "y": 0, "color": "white"})]),
        blocks.CAT_DRAW: mk("circ", {"x": 10, "y": 10, "r": 4, "color": "green"}),
        blocks.CAT_INPUT: mk("if", {"cond": mk("btnp", {"dir": "b"})}, children=[
            mk("beep", {"freq": 220})]),
        blocks.CAT_VARIABLES: mk("set_var", {"var": "v", "value": mk("var", {"var": "v"})}),
        blocks.CAT_OPERATORS: mk("set_var", {"var": "v", "value": mk("op_div", {"a": 6, "b": 2})}),
        blocks.CAT_SOUND: mk("sfx", {"n": 1}),
    }
    body = [b for b in samples.values() if b is not None]
    prog = _program(["v"], [mk("on_update", children=body)])
    src = blocks.compile_blocks(prog)
    _assert_micropython_safe(src)
    _run_cart(src, frames=2)


# ----------------------------------------------------------------------------
# Compiler: robustness / safety guards
# ----------------------------------------------------------------------------

def test_text_literal_is_escaped_not_fstring():
    prog = _program([], [mk("on_draw", children=[
        mk("print", {"text": 'he said "hi"\nbye', "x": 0, "y": 0, "color": "white"})])])
    src = blocks.compile_blocks(prog)
    assert r'\"hi\"' in src and r"\n" in src            # escaped, still a plain str
    _assert_micropython_safe(src)
    _run_cart(src)


def test_unknown_block_and_bad_var_raise_blockerror():
    import pytest
    with pytest.raises(blocks.BlockError):
        blocks.compile_blocks(_program([], [mk("on_draw", children=[{"t": "nope", "p": {}}])]))
    with pytest.raises(blocks.BlockError):
        blocks.compile_blocks(_program(["x; import os"], []))   # not an identifier
    with pytest.raises(blocks.BlockError):
        # a set_var referencing an undeclared variable
        blocks.compile_blocks(_program([], [mk("on_draw", children=[
            mk("set_var", {"var": "ghost", "value": 1})])]))


# ----------------------------------------------------------------------------
# Store glue: load_blocks / save_blocks (atomic blocks.json + compiled main.py)
# ----------------------------------------------------------------------------

def test_save_blocks_persists_tree_and_compiled_main(tmp_path):
    root = str(tmp_path / "carts")
    kid_carts.ensure_dirs(root)
    cart = kid_carts.create("Block Cart", root)
    prog = _the_little_game()
    status, msg = kid_carts.save_blocks(cart, prog)
    assert status == kid_carts.SAVE_OK, msg
    # both files landed; main.py is the compiled, runnable source
    reloaded = kid_carts.load(cart["path"])
    assert reloaded["blocks"] == prog                  # blocks.json round-trips
    assert reloaded["src"].startswith("# Made with KidCode blocks")
    assert "def _draw():" in reloaded["src"]
    # load_blocks reads the tree directly too (by path or cart)
    assert kid_carts.load_blocks(cart["path"]) == prog
    # the compiled main.py actually runs as a cart
    _run_cart(reloaded["src"], frames=2)


def test_save_blocks_rejects_corrupt_program_without_touching_files(tmp_path):
    root = str(tmp_path / "carts")
    kid_carts.ensure_dirs(root)
    cart = kid_carts.create("Block Cart", root, src="def _draw():\n    cls(1)\n")
    good_main = kid_carts.load(cart["path"])["src"]
    status, msg = kid_carts.save_blocks(cart, _program([], [
        mk("on_draw", children=[{"t": "bogus", "p": {}}])]))
    assert status == kid_carts.SAVE_BAD_SYNTAX and msg
    # neither blocks.json nor main.py was written/truncated
    assert kid_carts.load_blocks(cart["path"]) is None
    assert kid_carts.load(cart["path"])["src"] == good_main


def test_code_authored_cart_has_no_blocks():
    # a normal cart (no blocks.json) loads with blocks == None (back-compat).
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        root = d + "/carts"
        kid_carts.ensure_dirs(root)
        c = kid_carts.create("Plain", root, src="def _draw():\n    cls(0)\n")
        assert kid_carts.load(c["path"])["blocks"] is None


# ----------------------------------------------------------------------------
# End-to-end: a block-compiled cart runs through the real host Workstation
# ----------------------------------------------------------------------------

def test_block_cart_runs_through_workstation(tmp_path):
    from runtime import host_app
    ws = host_app.build_workstation(str(tmp_path / "carts"))
    # author a cart from blocks, save (compiles main.py), rescan so the launcher sees it
    cart = kid_carts.create("My Block Game", str(tmp_path / "carts"), type="game")
    status, msg = kid_carts.save_blocks(cart, _the_little_game())
    assert status == kid_carts.SAVE_OK, msg
    ws.launcher.items = kid_carts.scan(str(tmp_path / "carts"))
    for i, c in enumerate(ws.launcher.items):
        if c["title"] == "My Block Game":
            ws.launcher.sel = i
            break
    ws.open()
    assert ws.cart_error is None and ws.ns is not None  # compiled cleanly + started
    for _ in range(10):
        ws.frame(1 / 30)
    assert ws.cart_error is None                        # ran 10 frames clean
    assert ws.ns["x"] == 100                            # _init set it; no input held


# ----------------------------------------------------------------------------
# Typed literals in expr slots (#29 blocking gap) + the number helpers
# ----------------------------------------------------------------------------

def test_literal_in_expr_slot_compiles_bare_and_runs():
    # an integer literal in an expr slot emits the BARE value; a kid can finally
    # `set score to 0` / `set x to 50` / compare `> 100`.
    prog = _program(["score", "x"], [mk("on_start", children=[
        mk("set_var", {"var": "score", "value": 0}),
        mk("set_var", {"var": "x", "value": 50}),
    ]), mk("on_update", children=[
        mk("if", {"cond": mk("op_gt", {"a": mk("var", {"var": "x"}), "b": 100})},
           children=[mk("change_var", {"var": "score", "value": 1})]),
    ])])
    src = blocks.compile_blocks(prog)
    assert "score = 0" in src and "x = 50" in src
    assert "(x > 100)" in src
    _assert_micropython_safe(src)
    fake = _run_cart(src)[0]
    assert fake["score"] == 0 and fake["x"] == 50


def test_float_literal_in_expr_slot():
    prog = _program(["g"], [mk("on_start", children=[
        mk("set_var", {"var": "g", "value": 4.5})])])
    src = blocks.compile_blocks(prog)
    assert "g = 4.5" in src
    assert _run_cart(src)[0]["g"] == 4.5


def test_parse_number_literal_coerces_and_sanitizes():
    p = blocks.parse_number_literal
    assert p("5") == 5 and isinstance(p("5"), int)
    assert p("-3") == -3
    assert p("4.5") == 4.5
    assert p("1.2.3") == 1.23         # second dot dropped, its digits kept
    assert p("x9y") == 9              # letters dropped
    assert p("", 7) == 7 and p("-", 7) == 7 and p(".", 7) == 7   # nothing usable -> default
    # a sanitized literal can never carry code through to the compiler
    assert p("0); import os #") == 0


def test_is_literal_value():
    assert blocks.is_literal_value(0) and blocks.is_literal_value("hi")
    assert blocks.is_literal_value(None)
    assert not blocks.is_literal_value(mk("op_add", {"a": 1, "b": 2}))


def test_op_rnd_is_an_integer_and_takes_an_expression():
    # op_rnd now emits a whole number and its bound is an expr slot (so `random to W`
    # or `random to (x + 5)` works, not just a literal).
    prog = _program(["r"], [mk("on_start", children=[
        mk("set_var", {"var": "r", "value": mk("op_rnd", {"n": 10})})])])
    src = blocks.compile_blocks(prog)
    assert "int(rnd(10))" in src
    _run_cart(src)
    # an expression as the bound compiles too
    prog2 = _program(["r"], [mk("on_start", children=[
        mk("set_var", {"var": "r", "value": mk("op_rnd", {"n": mk("op_add", {"a": 100, "b": 5})})})])])
    assert "int(rnd((100 + 5)))" in blocks.compile_blocks(prog2)


def test_tap_position_readers_compile_and_hit_test():
    # touch_x / touch_y let a tap game know WHERE the tap landed.
    prog = _program(["hit"], [mk("on_update", children=[
        mk("if", {"cond": mk("op_gt", {"a": mk("touch_x"), "b": 100})},
           children=[mk("set_var", {"var": "hit", "value": 1})])])])
    src = blocks.compile_blocks(prog)
    assert "_touch_x()" in src and "def _touch_x():" in src
    _assert_micropython_safe(src)
    # with a tap at x=150, the branch fires
    fake = _FakeAPI()
    fake._touch = (150, 50, True)
    fake, _ = _run_cart(src, frames=1, fake=fake)
    assert fake["hit"] == 1
    # no tap -> _touch_x returns -100, branch does not fire
    fake2 = _FakeAPI()
    fake2._touch = None
    fake2, _ = _run_cart(src, frames=1, fake=fake2)
    assert fake2.get("hit", 0) == 0


# ----------------------------------------------------------------------------
# The shipped tap_game.kcart: its blocks.json is the source of truth, compiles to
# the shipped main.py, and the cart plays (tap the target -> score).
# ----------------------------------------------------------------------------

def _tap_game_dir():
    return str(ROOT / "system_carts" / "tap_game.kcart")


def test_tap_game_blocks_json_compiles_to_shipped_main():
    import json
    base = _tap_game_dir()
    with open(base + "/blocks.json") as f:
        prog = json.loads(f.read())
    with open(base + "/main.py") as f:
        shipped = f.read()
    # the cart is block-authored: blocks.json compiles to EXACTLY the shipped main.py
    assert blocks.compile_blocks(prog) == shipped
    assert blocks.is_block_authored_source(shipped)


def test_tap_game_cart_loads_blocks_and_runs():
    base = _tap_game_dir()
    cart = kid_carts.load(base)
    assert cart is not None and cart["blocks"] is not None
    # opening it in the block editor would see real blocks
    assert cart["blocks"]["vars"]                       # declared variables present
    _run_cart(cart["src"], frames=3)                    # plays clean headlessly


def test_tap_game_scores_on_a_tap_on_the_target(tmp_path):
    from runtime import host_app
    base = _tap_game_dir()
    cart = kid_carts.load(base)
    ws = host_app.build_workstation(str(tmp_path / "carts"))
    ws.launcher.items = [cart]
    ws.launcher.sel = 0
    ws.open()
    assert ws.cart_error is None
    ws.input.game_pointer = None
    for _ in range(3):
        ws.frame(1 / 30)
    tx, ty = ws.ns["tx"], ws.ns["ty"]
    before = ws.ns["score"]
    ws.input.game_pointer = (int(tx) + 14, int(ty) + 14, True)   # tap the target center
    ws.frame(1 / 30)
    assert ws.ns["score"] == before + 1                 # scored
    # tapping empty space does not score
    ws.ns["tx"], ws.ns["ty"] = 250, 180
    ws.input.game_pointer = (5, 18, True)
    s = ws.ns["score"]
    ws.frame(1 / 30)
    assert ws.ns["score"] == s
    # the clock runs out -> game over
    ws.input.game_pointer = None
    ws.ns["timer"] = 2
    for _ in range(4):
        ws.frame(1 / 30)
    assert ws.ns["over"] == 1


def test_tap_game_is_in_the_device_cart_bundle():
    # gen_device_carts must pick the new cart up (it scans system_carts/), and carry
    # its blocks.json so the on-device block editor opens it as blocks.
    import sys as _sys
    _sys.path.insert(0, str(ROOT / "tools"))
    import gen_device_carts
    carts = gen_device_carts.build_carts(str(ROOT / "system_carts"))
    tg = [c for c in carts if c["title"] == "Tap Game"]
    assert tg, "Tap Game not in the device bundle (add it to CART_ORDER)"
    assert tg[0].get("blocks"), "Tap Game must carry blocks.json into the bundle"
    # the rendered module is valid Python (repr round-trips)
    compile(gen_device_carts.render_module(carts), "<carts_data>", "exec")
