"""ONE block-tree walk, and one root set.

There were four hand-rolled walkers -- `blocks._uses`, `blocks._calls_in`,
`editors_block._rewrite_name_refs`, `editors_block._rewrite_call_names` -- with
the same ~10-line skeleton (list branch -> dict guard -> recurse "p" values ->
recurse "c" children), and they had already drifted: the two in `blocks.py`
recursed c-then-p, the two in `editors_block.py` p-then-c. Every schema
extension had to land in each copy by hand (#48's list-valued args in all four,
#85/#93's object roots in two), so a fifth container key would silently skip
whichever walker nobody edited -- and a rename that misses a reference compiles
fine and does the wrong thing.

The failure this guards is invisible: no exception, no changed pixel, just a
program that means something else. So the net is per-CONSUMER: one fixture that
puts the target in every container position, driven through all four consumers.
"""

import json

import pytest

from runtime import blocks
from runtime.editors_block import BlockEditor

mk = blocks.make_block


def _program():
    """A program with the rename targets in EVERY container the schema has:
    a c-body statement, a nested expression param, a call's list-valued args
    (#48, including a call nested inside those args), a proc-def body, and a
    per-object hat (#85/#93)."""
    return {
        "vars": ["score"],
        "lists": ["bag"],
        "scripts": [{"t": "on_start", "c": [
            mk("set_var", {"var": "score", "value": mk("var", {"var": "score"})}),
            {"t": blocks.CALL, "p": {"name": "helper", "args": [
                mk("var", {"var": "score"}),
                {"t": blocks.CALL, "p": {"name": "helper",
                                         "args": [mk("var", {"var": "score"})]}},
            ]}},
            mk("list_add", {"list": "bag", "item": mk("var", {"var": "score"})}),
            mk("repeat", {"n": mk("var", {"var": "score"})},
               [mk("change_var", {"var": "score", "value": 1})]),
        ]}],
        "procs": [{"t": blocks.PROC_DEF, "p": {"name": "helper", "params": ["q"]},
                   "c": [mk("set_var", {"var": "score",
                                        "value": mk("var", {"var": "score"})})]}],
        "objects": [{"tag": "hero", "scripts": [{"t": "on_tap", "c": [
            mk("set_var", {"var": "score", "value": mk("var", {"var": "score"})}),
            {"t": blocks.CALL, "p": {"name": "helper",
                                     "args": [mk("var", {"var": "score"})]}},
        ]}]}],
    }


def _positions(program):
    """Every (container, node) the fixture deliberately hides a target in, so a
    walker that skips one container fails a NAMED case instead of a hash."""
    scripts = program["scripts"][0]["c"]
    call = scripts[1]
    return {
        "c-body statement": scripts[0],
        "nested expression param": scripts[0]["p"]["value"],
        "list-valued call arg (#48)": call["p"]["args"][0],
        "call nested in a call arg": call["p"]["args"][1],
        "c-body under a c-block": scripts[3]["c"][0],
        "proc-def body (#48)": program["procs"][0]["c"][0],
        "per-object hat (#85/#93)": program["objects"][0]["scripts"][0]["c"][0],
    }


# -- the walk reaches every container ----------------------------------------

@pytest.mark.parametrize("where", sorted(_positions(_program())))
def test_walk_tree_visits_every_container(where):
    program = _program()
    target = _positions(program)[where]
    seen = []
    blocks.walk_tree(blocks.all_roots(program), seen.append)
    assert any(n is target for n in seen), where


def test_walk_tree_visits_each_node_once():
    program = _program()
    seen = []
    blocks.walk_tree(blocks.all_roots(program), seen.append)
    assert len(seen) == len({id(n) for n in seen})


def test_walk_tree_tolerates_junk():
    """A hand-edited blocks.json must not crash the walk."""
    seen = []
    blocks.walk_tree([None, 7, "x", [], {}, {"c": None, "p": None},
                      {"c": [None], "p": {"a": None}}], seen.append)
    assert len(seen) == 3


# -- all four consumers see all of it ----------------------------------------

def test_uses_sees_a_block_in_every_container():
    for where, node in _positions(_program()).items():
        program = _program()
        _positions(program)[where]["t"] = "move_steps"
        assert blocks._uses(blocks.all_roots(program), "move_steps"), where


def test_calls_in_sees_a_call_in_every_root_set():
    program = _program()
    assert blocks._calls_in(blocks.all_roots(program), {"helper"}) == {"helper"}


def test_rename_var_reaches_every_container():
    be = BlockEditor(blocks, _program())
    be.rename_var("score", "points")
    dumped = json.dumps(be.program)
    assert '"score"' not in dumped, dumped
    assert '"points"' in dumped


def test_rename_list_reaches_every_container():
    be = BlockEditor(blocks, _program())
    be.rename_list("bag", "sack")
    dumped = json.dumps(be.program)
    assert '"bag"' not in dumped and '"sack"' in dumped


def test_rename_proc_reaches_every_container():
    be = BlockEditor(blocks, _program())
    be.rename_proc("helper", "aider")
    dumped = json.dumps(be.program)
    assert '"helper"' not in dumped, dumped
    assert '"aider"' in dumped


# -- the p/c recursion order is not observable -------------------------------

def test_recursion_order_is_not_observable(monkeypatch):
    """The two orders the four copies had disagreed on. Pinned because the
    walker's docstring claims it: every visitor acts on the node it is handed,
    so nothing downstream may depend on p-before-c."""
    def c_first(roots, visit):
        def walk(node):
            if isinstance(node, list):
                for it in node:
                    walk(it)
                return
            if not isinstance(node, dict):
                return
            visit(node)
            for c in node.get("c", []) or []:
                walk(c)
            for v in (node.get("p", {}) or {}).values():
                walk(v)
        for r in roots:
            walk(r)

    def renamed():
        be = BlockEditor(blocks, _program())
        be.rename_var("score", "points")
        be.rename_list("bag", "sack")
        be.rename_proc("helper", "aider")
        return json.dumps(be.program, sort_keys=True)

    shipped = renamed()
    monkeypatch.setattr(blocks, "walk_tree", c_first)
    assert renamed() == shipped


# -- one root set ------------------------------------------------------------

def test_all_roots_is_scripts_then_procs_then_object_hats():
    program = _program()
    roots = blocks.all_roots(program)
    assert roots == (program["scripts"] + program["procs"]
                     + program["objects"][0]["scripts"])


def test_all_roots_yields_the_live_dicts_so_a_rename_writes_through():
    program = _program()
    for r in blocks.all_roots(program):
        r["_touched"] = True
    assert program["scripts"][0]["_touched"]
    assert program["procs"][0]["_touched"]
    assert program["objects"][0]["scripts"][0]["_touched"]


def test_the_editor_root_set_is_raw_and_never_raises():
    """The live editor walks mid-edit, so it must not run the compiler's
    validation: collect_procs RAISES on a bad name and collect_objects DROPS an
    untagged entry, and a rename must still reach both."""
    program = _program()
    program["procs"].append({"t": blocks.PROC_DEF, "p": {"name": "9bad",
                                                         "params": []}, "c": []})
    program["objects"].append({"tag": "", "scripts": [
        mk("on_tap", None, [mk("set_var", {"var": "score", "value": 1})])]})
    with pytest.raises(blocks.BlockError):
        blocks.collect_procs(program)
    assert [o["tag"] for o in blocks.collect_objects(program)] == ["hero"]

    be = BlockEditor(blocks, program)
    be.rename_var("score", "points")
    untagged = program["objects"][1]["scripts"][0]["c"][0]
    assert untagged["p"]["var"] == "points"


def test_the_compiler_root_set_takes_the_validated_collections():
    program = _program()
    procs = blocks.collect_procs(program)
    objects = blocks.collect_objects(program)
    assert blocks.all_roots(program, procs, objects) == blocks.all_roots(program)


# -- ratchet -----------------------------------------------------------------

def test_only_blocks_py_knows_the_container_keys():
    """A re-grown hand-rolled walk is what this whole file exists to stop, so no
    other module may recurse the schema's container keys. (`_clone_tree` is not a
    hit and must not become one: it copies any dict/list and names neither key,
    which is why a new container needs no edit there.)"""
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent / "runtime"
    hits = []
    for path in sorted(root.glob("*.py")):
        src = path.read_text(encoding="utf-8")
        if 'get("c", [])' in src and 'get("p", {})' in src:
            hits.append(path.name)
    assert hits == ["blocks.py"], hits
