"""Tests for issue #48 -- deepening the v0.4 native block language beyond
single-object games:

  1. Lists       -- a list data type + create/clear/add/remove/set/get/length and
                    a for-each loop, compiled to portable Python lists.
  2. Operators   -- mod / round / abs / min / max, the comparisons <= >= !=, sqrt,
                    and simple string ops (join / length / letter of).
  3. Control     -- repeat until, wait until, stop, break out of loop (and the
                    forever/wait frame-yielding semantics stay intact).

Each new block is asserted to compile to correct, MicroPython-safe / portable
Python, to EXECUTE with the right behaviour, and to round-trip through blocks.json.
Backward compatibility (a pre-#48 program with no "lists" key still compiles) is
covered too. The editor-side list affordances (create/name/rename + the picker)
are exercised against the BlockEditor core."""

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

from runtime import blocks  # noqa: E402
from runtime import moy_carts  # noqa: E402
from runtime.editors import BlockEditor  # noqa: E402

mk = blocks.make_block


# ----------------------------------------------------------------------------
# Helpers (mirror tests/test_blocks.py so the new carts run the same way)
# ----------------------------------------------------------------------------

def _program(vars_=None, lists_=None, scripts=None):
    p = {"vars": list(vars_ or []), "scripts": scripts or []}
    if lists_ is not None:
        p["lists"] = list(lists_)
    return p


from blocks_helpers import run_cart as _run_cart  # noqa: E402


# The portable subset / MicroPython-safe gate (same spirit as moybyte_cli/portable.py
# and tests/test_blocks.py): no f-strings, no forbidden builtins, only moybyte-style
# names. compile_blocks imports nothing, so an Import node would be a red flag too.
_FORBIDDEN = {"eval", "exec", "getattr", "setattr", "compile", "open",
              "__import__", "globals", "locals", "vars", "dir", "input"}


def _assert_portable(src):
    assert "f'" not in src and 'f"' not in src, "f-string in generated source"
    tree = ast.parse(src)
    for node in ast.walk(tree):
        assert not isinstance(node, ast.JoinedStr), "f-string node"
        assert not isinstance(node, (ast.Import, ast.ImportFrom)), "generated cart imports"
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            assert node.func.id not in _FORBIDDEN, "forbidden call: " + node.func.id


# ============================================================================
# 1. LISTS
# ============================================================================

def test_lists_declared_init_empty_at_module_level():
    prog = _program(["x"], ["enemies", "scores"],
                    [mk("on_start", children=[mk("list_add", {"item": 1, "list": "enemies"})])])
    src = blocks.compile_blocks(prog)
    assert "enemies = []" in src and "scores = []" in src
    assert "x = 0" in src                       # variables still init to 0
    _assert_portable(src)
    _run_cart(src)


def test_list_add_and_clear_and_globals():
    # add mutates in place (no global); clear reassigns (needs `global`).
    prog = _program([], ["bag"], [
        mk("on_start", children=[
            mk("list_clear", {"list": "bag"}),
            mk("list_add", {"item": 10, "list": "bag"}),
            mk("list_add", {"item": 20, "list": "bag"})])])
    src = blocks.compile_blocks(prog)
    assert "bag.append(10)" in src and "bag.append(20)" in src
    assert "bag = []" in src
    assert "    global bag" in src              # clear reassigns -> hoisted
    _assert_portable(src)
    fake = _run_cart(src)
    assert fake["bag"] == [10, 20]


def test_list_add_only_needs_no_global():
    # a function that ONLY appends (never clears) doesn't hoist the list global.
    prog = _program([], ["log"], [
        mk("on_update", children=[mk("list_add", {"item": 1, "list": "log"})])])
    src = blocks.compile_blocks(prog)
    upd = src.split("def _update")[1]
    assert "global" not in upd                  # append mutates in place
    assert "log.append(1)" in upd
    fake = _run_cart(src, frames=3)
    assert fake["log"] == [1, 1, 1]


def test_list_remove_set_and_get_are_one_based_and_safe():
    prog = _program(["a", "b", "oob"], ["nums"], [
        mk("on_start", children=[
            mk("list_add", {"item": 11, "list": "nums"}),
            mk("list_add", {"item": 22, "list": "nums"}),
            mk("list_add", {"item": 33, "list": "nums"}),
            mk("list_set_at", {"index": 2, "list": "nums", "item": 99}),   # 1-based
            mk("list_remove_at", {"index": 1, "list": "nums"}),            # drop the 11
            mk("set_var", {"var": "a", "value": mk("list_get", {"index": 1, "list": "nums"})}),
            mk("set_var", {"var": "b", "value": mk("list_get", {"index": 2, "list": "nums"})}),
            # out-of-range get returns 0, out-of-range remove/set is ignored (no crash)
            mk("set_var", {"var": "oob", "value": mk("list_get", {"index": 99, "list": "nums"})}),
            mk("list_remove_at", {"index": 99, "list": "nums"})])])
    src = blocks.compile_blocks(prog)
    assert "_lset(nums, 2, 99)" in src
    assert "_lremove(nums, 1)" in src
    assert "_lget(nums, 1)" in src
    _assert_portable(src)
    fake = _run_cart(src)
    assert fake["nums"] == [99, 33]             # 11 removed, [22->99, 33]
    assert fake["a"] == 99 and fake["b"] == 33
    assert fake["oob"] == 0                     # out-of-range get -> 0


def test_list_len_reporter():
    prog = _program(["n"], ["xs"], [
        mk("on_start", children=[
            mk("list_add", {"item": 1, "list": "xs"}),
            mk("list_add", {"item": 2, "list": "xs"}),
            mk("set_var", {"var": "n", "value": mk("list_len", {"list": "xs"})})])])
    src = blocks.compile_blocks(prog)
    assert "len(xs)" in src
    fake = _run_cart(src)
    assert fake["n"] == 2


def test_for_each_iterates_and_hoists_the_loop_var():
    prog = _program(["total", "item"], ["nums"], [
        mk("on_start", children=[
            mk("list_add", {"item": 5, "list": "nums"}),
            mk("list_add", {"item": 7, "list": "nums"}),
            mk("set_var", {"var": "total", "value": 0}),
            mk("for_each", {"var": "item", "list": "nums"}, children=[
                mk("change_var", {"var": "total", "value": mk("var", {"var": "item"})})])])])
    src = blocks.compile_blocks(prog)
    assert "for item in nums:" in src
    assert "    global" in src and "item" in src.split("def _init")[1].split("\n")[1]
    _assert_portable(src)
    fake = _run_cart(src)
    assert fake["total"] == 12                  # 5 + 7
    assert fake["item"] == 7                     # loop var keeps its last binding


def test_list_program_roundtrips_through_blocks_json():
    prog = _program(["it"], ["scores"], [
        mk("on_start", children=[mk("list_add", {"item": 3, "list": "scores"})]),
        mk("on_draw", children=[
            mk("for_each", {"var": "it", "list": "scores"}, children=[
                mk("print", {"text": mk("var", {"var": "it"}), "x": 0, "y": 0,
                             "color": "white"})])])])
    again = blocks.loads(blocks.dumps(prog))
    assert again == prog                         # exact round-trip incl. "lists"
    assert blocks.loads(blocks.dumps(again)) == prog


def test_unknown_list_raises_blockerror():
    import pytest
    with pytest.raises(blocks.BlockError):
        # a list_add referencing an undeclared list
        blocks.compile_blocks(_program([], [], [mk("on_start", children=[
            mk("list_add", {"item": 1, "list": "ghost"})])]))
    with pytest.raises(blocks.BlockError):
        blocks.compile_blocks(_program([], ["x; import os"], []))   # not an identifier
    with pytest.raises(blocks.BlockError):
        # a list and a variable can't share a name (both are module globals)
        blocks.compile_blocks(_program(["dup"], ["dup"], []))


# ============================================================================
# 2. OPERATORS
# ============================================================================

def test_new_comparison_operators_emit_and_evaluate():
    cases = {
        "op_le": ("({a} <= {b})", lambda a, b: a <= b),
        "op_ge": ("({a} >= {b})", lambda a, b: a >= b),
        "op_ne": ("({a} != {b})", lambda a, b: a != b),
    }
    for tid, (frag, fn) in cases.items():
        prog = _program(["r"], None, [mk("on_start", children=[
            mk("set_var", {"var": "r", "value": mk(tid, {"a": 3, "b": 5})})])])
        src = blocks.compile_blocks(prog)
        assert "(3 " in src                       # rendered as an infix comparison
        _assert_portable(src)
        fake = _run_cart(src)
        assert fake["r"] == fn(3, 5), tid


def test_math_operator_reporters():
    prog = _program(["m", "rnd", "ab", "mn", "mx", "sq"], None, [
        mk("on_start", children=[
            mk("set_var", {"var": "m", "value": mk("op_mod", {"a": 17, "b": 5})}),
            mk("set_var", {"var": "rnd", "value": mk("op_round", {"a": 2.6})}),
            mk("set_var", {"var": "ab", "value": mk("op_abs", {"a": -8})}),
            mk("set_var", {"var": "mn", "value": mk("op_min", {"a": 4, "b": 9})}),
            mk("set_var", {"var": "mx", "value": mk("op_max", {"a": 4, "b": 9})}),
            mk("set_var", {"var": "sq", "value": mk("op_sqrt", {"a": 16})})])])
    src = blocks.compile_blocks(prog)
    assert "(17 % 5)" in src
    assert "round(2.6)" in src
    assert "abs(-8)" in src
    assert "min(4, 9)" in src and "max(4, 9)" in src
    assert "** 0.5" in src                        # sqrt without importing math
    _assert_portable(src)
    fake = _run_cart(src)
    assert fake["m"] == 2 and fake["rnd"] == 3 and fake["ab"] == 8
    assert fake["mn"] == 4 and fake["mx"] == 9 and fake["sq"] == 4.0


def test_string_operators():
    prog = _program(["j", "ln", "ch", "oob"], None, [
        mk("on_start", children=[
            mk("set_var", {"var": "j", "value": mk("op_join", {"a": "sc:", "b": 7})}),
            mk("set_var", {"var": "ln", "value": mk("op_text_len", {"a": "hello"})}),
            mk("set_var", {"var": "ch", "value": mk("op_letter", {"n": 2, "a": "abc"})}),
            mk("set_var", {"var": "oob", "value": mk("op_letter", {"n": 9, "a": "abc"})})])])
    src = blocks.compile_blocks(prog)
    assert '(str("sc:") + str(7))' in src
    assert 'len(str("hello"))' in src
    assert '_letter("abc", 2)' in src             # note: letter helper takes (text, n)
    assert "def _letter(" in src
    _assert_portable(src)
    fake = _run_cart(src)
    assert fake["j"] == "sc:7"
    assert fake["ln"] == 5
    assert fake["ch"] == "b"                       # 1-based: 2nd letter of "abc"
    assert fake["oob"] == ""                       # out of range -> ""


# ============================================================================
# 3. CONTROL
# ============================================================================

def test_repeat_until_is_bounded_and_breaks_when_true():
    prog = _program(["n"], None, [
        mk("on_update", children=[
            mk("repeat_until", {"cond": mk("op_ge", {"a": mk("var", {"var": "n"}), "b": 5})},
               children=[mk("change_var", {"var": "n", "value": 1})])])])
    src = blocks.compile_blocks(prog)
    assert "while True" not in src                 # never an unbounded loop
    assert "range(100000)" in src                  # bounded, like forever
    assert "(n >= 5)" in src and "break" in src
    _assert_portable(src)
    fake = _run_cart(src, frames=1)
    assert fake["n"] == 5                           # incremented until the cond held


def test_break_out_of_loop_inside_a_loop_emits_break():
    prog = _program(["i"], None, [
        mk("on_update", children=[
            mk("repeat", {"times": 10}, children=[
                mk("change_var", {"var": "i", "value": 1}),
                mk("if", {"cond": mk("op_eq", {"a": mk("var", {"var": "i"}), "b": 3})},
                   children=[mk("break_loop")])])])])
    src = blocks.compile_blocks(prog)
    assert "break" in src
    _assert_portable(src)
    fake = _run_cart(src, frames=1)
    assert fake["i"] == 3                           # broke out at i == 3


def test_stray_break_outside_a_loop_degrades_to_pass():
    # a break_loop placed directly in an event body (no surrounding loop) would be a
    # SyntaxError as a bare `break`; the compiler degrades it to `pass` so the cart
    # always compiles.
    prog = _program([], None, [
        mk("on_update", children=[mk("break_loop")])])
    src = blocks.compile_blocks(prog)
    assert "break" not in src                       # no stray break
    _assert_portable(src)                           # and it still parses/compiles
    _run_cart(src)


def test_stop_returns_from_the_script():
    prog = _program(["ran"], None, [
        mk("on_update", children=[
            mk("set_var", {"var": "ran", "value": 1}),
            mk("stop"),
            mk("set_var", {"var": "ran", "value": 2})])])   # unreachable after stop
    src = blocks.compile_blocks(prog)
    assert "return" in src
    _assert_portable(src)
    fake = _run_cart(src, frames=1)
    assert fake["ran"] == 1                          # stopped before the second set


def test_wait_until_is_a_documented_noop_helper():
    prog = _program(["x"], None, [
        mk("on_update", children=[
            mk("wait_until", {"cond": mk("op_gt", {"a": mk("var", {"var": "x"}), "b": 0})})])])
    src = blocks.compile_blocks(prog)
    assert "def _wait_until(" in src and "while True" not in src
    _assert_portable(src)
    _run_cart(src, frames=2)                         # never blocks the frame loop


def test_forever_and_wait_semantics_unchanged():
    # the pre-#48 frame-yielding semantics for forever (bounded) and wait (no-op) must
    # not have regressed.
    prog = _program([], None, [
        mk("on_start", children=[mk("wait", {"secs": 1})]),
        mk("on_draw", children=[
            mk("forever", children=[mk("cls", {"color": "black"})])])])
    src = blocks.compile_blocks(prog)
    assert "while True" not in src and "range(100000)" in src
    assert "def _wait(" in src
    _assert_portable(src)


# ============================================================================
# Backward compatibility
# ============================================================================

def test_pre_v48_program_still_compiles_unchanged():
    # a program saved before #48 has NO "lists" key; it must still compile, with no
    # list inits leaking in.
    prog = {"vars": ["score"], "scripts": [
        mk("on_start", children=[mk("set_var", {"var": "score", "value": 0})]),
        mk("on_draw", children=[mk("cls", {"color": "black"})])]}
    assert "lists" not in prog
    src = blocks.compile_blocks(prog)
    assert "score = 0" in src
    assert "= []" not in src                         # no phantom list inits
    _assert_portable(src)
    _run_cart(src, frames=2)

# (the tap_game byte-for-byte pin lives ONCE, in test_blocks.py --
# test_tap_game_blocks_json_compiles_to_shipped_main; two more verbatim
# copies of it lived here until 2026-08-18)


def test_empty_program_unaffected():
    src = blocks.compile_blocks(blocks.empty_program())
    assert "= []" not in src                         # empty_program declares no lists
    _assert_portable(src)
    _run_cart(src, frames=2)


# ============================================================================
# Editor-side list affordances (BlockEditor core)
# ============================================================================

def _be():
    return BlockEditor(blocks)


def _go_to_insert(be, depth, which=-1):
    found = [i for i, r in enumerate(be.rows) if r.kind == "insert" and r.depth == depth]
    assert found, "no insert row at depth %d" % depth
    be.cur = found[which]


def test_editor_new_list_creates_unique_names_across_vars_and_lists():
    be = _be()
    a = be.new_list("list")
    b = be.new_list("list")
    assert a == "list" and b == "list2"          # de-duplicated
    be.add_var("score")
    # a var can't reuse a list name and vice versa
    be.add_var("list")
    assert "list" not in be.variables()          # refused (it's a list)
    be.add_list("score")
    assert "score" not in be.lists()             # refused (it's a variable)


def test_editor_rename_list_rewrites_references():
    be = _be()
    name = be.new_list("list")
    _go_to_insert(be, 1)
    be.insert_block("list_add", {"item": 1, "list": name})
    applied = be.rename_list(name, "enemies!!")
    assert applied == "enemies"                  # sanitized
    assert "enemies" in be.lists() and name not in be.lists()
    # the list_add slot followed the rename
    blk = [r.block for r in be.rows if (r.block or {}).get("t") == "list_add"][0]
    assert blk["p"]["list"] == "enemies"
    src = blocks.compile_blocks(be.program)
    assert "enemies = []" in src and "enemies.append(1)" in src


def test_editor_inserts_list_blocks_then_compiles_and_runs():
    be = _be()
    be.add_var("it")
    name = be.new_list("nums")
    _go_to_insert(be, 1)                          # on_start trailing insert
    be.insert_block("list_add", {"item": 42, "list": name})
    # a for_each in on_draw
    _go_to_insert(be, 1)
    fe = be.insert_block("for_each", {"var": "it", "list": name})
    _go_to_insert(be, 2)                          # inside the for_each body
    be.insert_block("spr", {"id": 0, "x": mk("var", {"var": "it"}), "y": 0})
    src = blocks.compile_blocks(be.program)
    assert "nums.append(42)" in src and "for it in nums:" in src
    _assert_portable(src)
    _run_cart(src, frames=2)


def test_lists_category_and_color_present():
    assert blocks.CAT_LISTS in blocks.categories()
    assert blocks.blocks_in_category(blocks.CAT_LISTS)   # has blocks
    assert blocks.CAT_LISTS in blocks.CATEGORY_COLOR
