"""Tests for issue #48 -- CUSTOM BLOCKS (My Blocks / procedures with parameters),
the last remaining #48 piece (lists / operators / control already shipped).

A kid defines a reusable block ("define NAME p1 p2 ..." with a body) and calls it
("call NAME arg0 arg1 ..."). Coverage, in the same spirit as tests/test_blocks_v48.py:

  * compiler -- define/call compile to plain readable `def`/call Python; params are
    LOCALS of the generated function (never `global`-hoisted) while a global var the
    body reassigns is still hoisted; args pad/truncate to the current param count;
    RECURSION (direct self-call or mutual) degrades the cycle-closing call to `pass`;
    a call to a DELETED definition degrades to `pass`; generated code stays portable
    (no eval/exec/getattr/imports); everything round-trips through blocks.json;
    a pre-#48 program with no "procs" key is unaffected; bad names raise BlockError.
  * editor core (BlockEditor) -- create/name/rename (rewrites calls) + add/remove a
    parameter, unique names across vars/lists/procs, insert_call pre-fills args, a
    deleted definition leaves stray calls compiling to `pass`, params are in scope
    only inside their body, and a param used as a variable compiles + runs.
  * editor surface (Workstation.block_ui) -- the My Blocks palette creates a proc and
    places a call end-to-end, and the PROC menu adds an input.
"""

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

from runtime import blocks              # noqa: E402
from runtime.editors import BlockEditor  # noqa: E402

mk = blocks.make_block


# ----------------------------------------------------------------------------
# Helpers (mirror tests/test_blocks_v48.py)
# ----------------------------------------------------------------------------

def _program(vars_=None, lists_=None, procs_=None, scripts=None):
    p = {"vars": list(vars_ or []), "scripts": scripts or []}
    if lists_ is not None:
        p["lists"] = list(lists_)
    if procs_ is not None:
        p["procs"] = list(procs_)
    return p


def _proc(name, params=None, body=None):
    return mk("proc_def", {"name": name, "params": list(params or [])},
              children=body or [])


def _call(name, args=None):
    return mk("call", {"name": name, "args": list(args or [])})


from blocks_helpers import run_cart as _run_cart  # noqa: E402


_FORBIDDEN = {"eval", "exec", "getattr", "setattr", "compile", "open",
              "__import__", "globals", "locals", "vars", "dir", "input"}


def _assert_portable(src):
    assert "f'" not in src and 'f"' not in src, "f-string in generated source"
    tree = ast.parse(src)
    for node in ast.walk(tree):
        assert not isinstance(node, ast.JoinedStr), "f-string node"
        assert not isinstance(node, (ast.Import, ast.ImportFrom)), "generated import"
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            assert node.func.id not in _FORBIDDEN, "forbidden call: " + node.func.id


# ============================================================================
# Compiler
# ============================================================================

def test_define_and_call_compile_to_plain_functions():
    prog = _program(
        vars_=["score"],
        procs_=[_proc("draw_star", ["px", "py"], [
            mk("spr", {"id": 0, "x": mk("var", {"var": "px"}),
                       "y": mk("var", {"var": "py"})})])],
        scripts=[mk("on_draw", children=[_call("draw_star", [10, 20])])])
    src = blocks.compile_blocks(prog)
    assert "def draw_star(px, py):" in src        # a plain readable function
    assert "spr(0, px, py)" in src                # params read as locals
    assert "draw_star(10, 20)" in src             # a plain positional call
    _assert_portable(src)
    fake = _run_cart(src)
    assert ("spr", (0, 10, 20), {}) in fake.calls


def test_params_are_locals_global_var_is_hoisted():
    # bump(amount): score = score + amount   -- score is a GLOBAL (hoisted), amount
    # is a PARAM (a local, never hoisted).
    prog = _program(
        vars_=["score"],
        procs_=[_proc("bump", ["amount"], [
            mk("change_var", {"var": "score", "value": mk("var", {"var": "amount"})})])],
        scripts=[mk("on_update", children=[_call("bump", [5])])])
    src = blocks.compile_blocks(prog)
    fn = src.split("def bump(amount):")[1].split("def ")[0]
    assert "global score" in fn                   # the global var is hoisted
    assert "amount" not in fn.split("global")[1].split("\n")[0]  # param NOT in `global`
    assert "score = score + (amount)" in fn
    _assert_portable(src)
    fake = _run_cart(src, frames=3)
    assert fake["score"] == 15                     # 5 per frame x3


def test_param_shadows_and_never_declared_global():
    # a param NAMED like a would-be global is still a local: assigning it inside the
    # proc must not add a `global` (it's the function's own parameter).
    prog = _program(
        procs_=[_proc("tweak", ["n"], [
            mk("set_var", {"var": "n", "value": 7})])],   # writes the PARAM
        scripts=[mk("on_start", children=[_call("tweak", [1])])])
    src = blocks.compile_blocks(prog)
    fn = src.split("def tweak(n):")[1].split("def ")[0]
    assert "global" not in fn                      # n is a local param, not a global
    assert "n = 7" in fn
    _assert_portable(src)
    _run_cart(src)


def test_call_args_pad_and_truncate_to_param_count():
    prog = _program(
        procs_=[_proc("f", ["a", "b"], [
            mk("spr", {"id": 0, "x": mk("var", {"var": "a"}),
                       "y": mk("var", {"var": "b"})})])],
        scripts=[mk("on_draw", children=[
            _call("f", [1]),          # missing 2nd arg -> 0
            _call("f", [3, 4, 9])])])  # extra 3rd arg -> ignored
    src = blocks.compile_blocks(prog)
    assert "f(1, 0)" in src
    assert "f(3, 4)" in src
    _assert_portable(src)
    _run_cart(src)


def test_direct_recursion_degrades_to_pass():
    prog = _program(
        procs_=[_proc("loopy", [], [_call("loopy")])],
        scripts=[mk("on_start", children=[_call("loopy")])])
    src = blocks.compile_blocks(prog)
    body = src.split("def loopy():")[1].split("def ")[0]
    assert "loopy()" not in body                   # the self-call is degraded...
    assert "pass" in body
    assert "loopy()" in src.split("def _init")[1]  # ...but the outer call still emits
    _assert_portable(src)
    _run_cart(src)                                  # and it does not hang


def test_mutual_recursion_fully_degrades():
    # a -> b -> a is a cycle; EVERY call on the cycle degrades to pass (the rule is
    # "a call participating in a cycle can't run"), so neither body recurses. The
    # program compiles and never recurses unbounded.
    prog = _program(
        procs_=[_proc("a", [], [_call("b")]),
                _proc("b", [], [_call("a")])],
        scripts=[mk("on_start", children=[_call("a")])])
    src = blocks.compile_blocks(prog)
    a_body = src.split("def a():")[1].split("def ")[0]
    b_body = src.split("def b():")[1].split("def ")[0]
    assert "b()" not in a_body and "a()" not in b_body   # both cycle edges degraded
    assert "a()" in src.split("def _init")[1]            # the outer, acyclic call emits
    _assert_portable(src)
    _run_cart(src)


def test_non_recursive_call_chain_emits_both():
    # a -> b (no cycle back): both calls emit normally.
    prog = _program(
        vars_=["hits"],
        procs_=[_proc("a", [], [_call("b")]),
                _proc("b", [], [mk("change_var", {"var": "hits", "value": 1})])],
        scripts=[mk("on_start", children=[_call("a")])])
    src = blocks.compile_blocks(prog)
    assert "b()" in src.split("def a():")[1].split("def ")[0]
    _assert_portable(src)
    fake = _run_cart(src)
    assert fake["hits"] == 1


def test_call_to_deleted_definition_degrades_to_pass():
    prog = _program(
        scripts=[mk("on_update", children=[_call("ghost", [1, 2])])])
    src = blocks.compile_blocks(prog)
    assert "ghost(" not in src
    body = src.split("def _update(dt):")[1]
    assert "pass" in body
    _assert_portable(src)
    _run_cart(src)


def test_helper_used_only_inside_a_proc_is_still_emitted():
    # a helper (here _touched) referenced ONLY from a custom-block body must still be
    # emitted -- helper detection scans proc bodies too.
    prog = _program(
        vars_=["t"],
        procs_=[_proc("check", [], [
            mk("set_var", {"var": "t", "value": mk("touched")})])],
        scripts=[mk("on_update", children=[_call("check")])])
    src = blocks.compile_blocks(prog)
    assert "def _touched():" in src
    _assert_portable(src)
    _run_cart(src)


def test_generated_custom_block_code_is_portable():
    prog = _program(
        vars_=["s"], lists_=["xs"],
        procs_=[_proc("work", ["k"], [
            mk("list_add", {"item": mk("var", {"var": "k"}), "list": "xs"}),
            mk("change_var", {"var": "s", "value": mk("var", {"var": "k"})})])],
        scripts=[mk("on_start", children=[_call("work", [3]), _call("work", [4])])])
    src = blocks.compile_blocks(prog)
    _assert_portable(src)
    fake = _run_cart(src)
    assert fake["xs"] == [3, 4] and fake["s"] == 7   # s accumulates 0+3+4


def test_procs_roundtrip_through_blocks_json():
    prog = _program(
        vars_=["n"],
        procs_=[_proc("greet", ["who", "times"], [
            mk("print", {"text": mk("var", {"var": "who"}), "x": 0, "y": 0,
                         "color": "white"})])],
        scripts=[mk("on_draw", children=[_call("greet", [1, 2])])])
    again = blocks.loads(blocks.dumps(prog))
    assert again == prog                           # exact round-trip incl. "procs"
    assert blocks.loads(blocks.dumps(again)) == prog


def test_pre_v48_program_without_procs_is_unaffected():
    prog = {"vars": ["score"], "scripts": [
        mk("on_start", children=[mk("set_var", {"var": "score", "value": 0})]),
        mk("on_draw", children=[mk("cls", {"color": "black"})])]}
    assert "procs" not in prog
    src = blocks.compile_blocks(prog)
    assert "def draw_" not in src and "def bump" not in src  # no phantom procs
    # an empty program declares no procs either
    assert "def " in src  # (only lifecycle funcs)
    _assert_portable(src)

# (the tap_game byte-for-byte pin lives ONCE, in test_blocks.py --
# test_tap_game_blocks_json_compiles_to_shipped_main; two more verbatim
# copies of it lived here until 2026-08-18)


def test_bad_custom_block_names_raise_blockerror():
    import pytest
    # non-identifier name
    with pytest.raises(blocks.BlockError):
        blocks.compile_blocks(_program(procs_=[_proc("has space", [], [])]))
    # reserved (would shadow the cart API `spr`)
    with pytest.raises(blocks.BlockError):
        blocks.compile_blocks(_program(procs_=[_proc("spr", [], [])]))
    # duplicate proc names
    with pytest.raises(blocks.BlockError):
        blocks.compile_blocks(_program(procs_=[_proc("f", [], []), _proc("f", [], [])]))
    # clash with a declared variable (both are module globals)
    with pytest.raises(blocks.BlockError):
        blocks.compile_blocks(_program(vars_=["dup"], procs_=[_proc("dup", [], [])]))
    # bad / reserved parameter name
    with pytest.raises(blocks.BlockError):
        blocks.compile_blocks(_program(procs_=[_proc("g", ["1bad"], [])]))
    with pytest.raises(blocks.BlockError):
        blocks.compile_blocks(_program(procs_=[_proc("g", ["int"], [])]))


def test_myblocks_category_present_and_colored():
    assert blocks.CAT_PROCS in blocks.categories()
    assert blocks.CAT_PROCS in blocks.CATEGORY_COLOR
    # proc_def + call live in the My Blocks category
    assert set(blocks.blocks_in_category(blocks.CAT_PROCS)) == {"proc_def", "call"}


# ============================================================================
# Editor core (BlockEditor)
# ============================================================================

def _be():
    return BlockEditor(blocks)


def _go_to_insert(be, depth, which=-1):
    found = [i for i, r in enumerate(be.rows) if r.kind == "insert" and r.depth == depth]
    assert found, "no insert row at depth %d" % depth
    be.cur = found[which]


def test_editor_new_proc_unique_across_vars_lists_procs():
    be = _be()
    a = blocks.proc_name(be.new_proc("block"))
    b = blocks.proc_name(be.new_proc("block"))
    assert a == "block" and b == "block2"          # de-duplicated
    be.add_var("block")
    assert "block" not in be.variables()           # refused (a proc owns the name)
    be.add_list("block")
    assert "block" not in be.lists()               # refused
    # a proc can't take a variable/list name either
    be.add_var("score")
    p = be.new_proc("score")
    assert blocks.proc_name(p) != "score"          # bumped to a free name


def test_editor_add_and_remove_param():
    be = _be()
    pd = be.new_proc("f")
    assert be.add_param(pd, "x") == "x"
    assert be.add_param(pd, "y!!") == "y"           # sanitized
    assert be.add_param(pd, "x") is None            # duplicate rejected
    assert be.add_param(pd, "int") is None          # reserved rejected
    assert blocks.proc_params(pd) == ["x", "y"]
    assert be.remove_last_param(pd) is True
    assert blocks.proc_params(pd) == ["x"]


def test_editor_rename_proc_rewrites_calls():
    be = _be()
    pd = be.new_proc("go")
    _go_to_insert(be, 1)
    call = be.insert_call("go")
    assert blocks.proc_name(call) == "go"
    applied = be.rename_proc("go", "run!!")
    assert applied == "run"                          # sanitized
    assert blocks.proc_name(call) == "run"           # the call followed the rename
    # rename refuses a clash / reserved
    be.add_var("score")
    assert be.rename_proc("run", "score") is None
    assert be.rename_proc("run", "spr") is None


def test_editor_insert_call_prefills_args_by_param_count():
    be = _be()
    pd = be.new_proc("f")
    be.add_param(pd, "a")
    be.add_param(pd, "b")
    _go_to_insert(be, 1)
    call = be.insert_call("f")
    assert blocks.call_args(call) == [0, 0]          # one default per param
    assert [s["name"] for s in be.slots(call)] == ["arg0", "arg1"]
    # setting an arg via the dynamic slot writes into the positional list
    be.set_slot("arg1", 42, call)
    assert blocks.call_args(call) == [0, 42]


def test_editor_delete_proc_leaves_stray_calls_as_pass():
    be = _be()
    pd = be.new_proc("f")
    _go_to_insert(be, 1)
    be.insert_call("f")
    assert be.delete_proc(pd) is True
    assert be.proc_names() == []
    src = blocks.compile_blocks(be.program)
    assert "def f(" not in src and "f()" not in src   # stray call degraded to pass
    _assert_portable(src)
    _run_cart(src)


def test_editor_current_params_only_inside_body():
    be = _be()
    pd = be.new_proc("f")
    be.add_param(pd, "px")
    # cursor parks on the define-hat after creation -> not "inside" the body
    assert be.current_params() == []
    # move to the trailing insert inside the proc body
    ins = [i for i, r in enumerate(be.rows) if r.kind == "insert" and r.depth == 1]
    be.cur = ins[-1]
    assert be.enclosing_proc() is pd
    assert be.current_params() == ["px"]
    # a top-level script body is outside any proc
    be.cur = ins[0]
    assert be.current_params() == []


def test_editor_param_used_as_variable_compiles_and_runs():
    # Build a proc through the core, read its param via a `var` block in the body,
    # and confirm it compiles + runs (param scoping end-to-end).
    be = _be()
    be.add_var("out")
    pd = be.new_proc("store")
    be.add_param(pd, "v")
    # put `set out = v` inside the PROC body (the insert whose enclosing proc is pd)
    for i, r in enumerate(be.rows):
        if r.kind == "insert" and r.depth == 1:
            be.cur = i
            if be.enclosing_proc() is pd:
                break
    be.insert_block("set_var", {"var": "out", "value": mk("var", {"var": "v"})})
    # call store(7) from a top-level script body (enclosing proc is None there)
    for i, r in enumerate(be.rows):
        if r.kind == "insert" and r.depth == 1:
            be.cur = i
            if be.enclosing_proc() is None:
                break
    call = be.insert_call("store")
    be.set_slot("arg0", 7, call)
    src = blocks.compile_blocks(be.program)
    assert "def store(v):" in src and "out = v" in src
    _assert_portable(src)
    fake = _run_cart(src)
    assert fake["out"] == 7


def test_editor_proc_def_cannot_be_copied_or_duplicated():
    be = _be()
    pd = be.new_proc("f")
    # cursor is on the define-hat
    assert be.copy_block() is False
    assert be.duplicate() is None
    assert be.start_move() is False


# ============================================================================
# Editor surface (Workstation.block_ui)
# ============================================================================

def _ws_with_block_cart(tmp_path, title="Custom Block Cart"):
    from runtime import host_app, moy_carts
    root = str(tmp_path / "carts")
    ws = host_app.build_workstation(root)
    moy_carts.create(title, root, type="game")
    ws.launcher.items = moy_carts.scan(root)
    for i, c in enumerate(ws.launcher.items):
        if c["title"] == title:
            ws.launcher.sel = i
            break
    ws.open()
    return ws


def test_ui_my_blocks_palette_creates_proc_and_places_call(tmp_path):
    import runtime.block_editor_ui as BUI
    ws = _ws_with_block_cart(tmp_path)
    ws._open_blocks()
    bui = ws.block_ui
    be = bui.blocks_ed

    # open the My Blocks category -> "+ new block" heads it
    bui._blk_open_blocks(blocks.CAT_PROCS)
    assert bui.blk_menu["items"][0] == BUI._NEW_PROC_ITEM
    bui.blk_menu["sel"] = 0
    bui._blk_menu_select()                          # create the proc + open name prompt
    assert bui.blk_kbd is not None and bui.blk_kbd["kind"] == "proc"
    bui._blk_kbd_commit()                           # accept the default name
    assert be.proc_names(), "a custom block was created"
    name = be.proc_names()[0]

    # reopen My Blocks -> a "call NAME" row now exists
    bui._blk_open_blocks(blocks.CAT_PROCS)
    call_items = [it for it in bui.blk_menu["items"]
                  if it[:len(BUI._CALL_PREFIX)] == BUI._CALL_PREFIX]
    assert call_items == [BUI._CALL_PREFIX + name]

    # park on an insert point in a script body and place the call
    _go_to_insert(be, 1)
    bui.blk_menu["sel"] = bui.blk_menu["items"].index(BUI._CALL_PREFIX + name)
    bui._blk_menu_select()
    assert bui.blk_menu is None
    assert any((r.block or {}).get("t") == "call" for r in be.rows)

    # the program compiles + the cart runs without error
    assert bui.save_blocks() is True
    for _ in range(2):
        ws.frame(1 / 30)
    assert ws.cart_error is None


def test_ui_proc_menu_adds_an_input(tmp_path):
    import runtime.block_editor_ui as BUI
    ws = _ws_with_block_cart(tmp_path)
    ws._open_blocks()
    bui = ws.block_ui
    be = bui.blocks_ed
    pd = be.new_proc("f")
    # select the define-hat, press A -> the PROC ACTIONS menu opens
    for i, r in enumerate(be.rows):
        if (r.block or {}).get("t") == "proc_def":
            be.cur = i
            break
    bui.blk_slot = 0
    bui._blk_a()
    assert bui.blk_menu is not None and bui.blk_menu["mode"] == "proc"
    # choose "Add an input" -> a name prompt opens; commit a name
    bui.blk_menu["sel"] = bui.blk_menu["items"].index(BUI._PROC_ADD)
    bui._blk_menu_select()
    assert bui.blk_kbd is not None and bui.blk_kbd["kind"] == "param"
    bui.blk_kbd["text"] = "size"
    bui._blk_kbd_commit()
    assert blocks.proc_params(pd) == ["size"]
