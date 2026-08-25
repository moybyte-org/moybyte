"""The 3.4 sync push's two halves in isolation (runtime/moy_sync.py).

The convergence harness (test_sync_convergence.py) proves two whole stores
agree; this file proves the PIECES are trustworthy on their own -- above all
`apply_ops`, which is the one function that takes paths from the NETWORK and
writes flash with them. Every refusal here is a line of defense the board
relies on, so each is pinned by name.
"""

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from runtime import moy_sync                                    # noqa: E402
from runtime.moy_sync import (StoreWatcher, apply_ops, parse_batch,  # noqa: E402
                              safe_segments, PART_MAX, BATCH_BUDGET)


def _store(tmp_path, name="carts"):
    root = tmp_path / name
    (root / "hop.moy").mkdir(parents=True)
    (root / "hop.moy" / "main.py").write_text("def _draw():\n    cls(1)\n")
    (root / "hop.moy" / "manifest.json").write_text('{"title": "Hop"}')
    (root / "hop.moy" / "scenes").mkdir()
    (root / "hop.moy" / "scenes" / "a.moyscene").write_text("{}")
    return root


def _bump_mtime(path):
    """Make a rewrite LOOK changed to the stat fast path -- the sweep's
    same-second hot-set handles real same-second writes, but tests should not
    depend on wall-clock races."""
    st = os.stat(path)
    os.utime(path, (st.st_atime, st.st_mtime + 2))


# ---------------------------------------------------------------------------
# safe_segments -- the path trust boundary.
# ---------------------------------------------------------------------------


def test_paths_from_the_network_are_shape_checked():
    assert safe_segments("hop.moy/main.py") == ["hop.moy", "main.py"]
    assert safe_segments("hop.moy/scenes/a.moyscene") is not None
    for bad in ("", "/abs/path", "a//b", "a/../b", "..", ".", "a/.", "a/",
                "a\\b/c", "a/b\0c", "a/b\nc", None, 3, "x" * 300):
        assert safe_segments(bad) is None, bad


def test_the_skip_set_is_refused_as_a_path_too():
    """A client asking to write journal/, a .bak or a .tmp is malformed by
    definition -- those never cross the wire in either direction, and .tmp is
    also the receiver's own chunk staging."""
    for bad in ("hop.moy/journal/journal.jsonl", "hop.moy/journal.jsonl",
                "hop.moy/main.py.bak", "hop.moy/main.py.tmp",
                "hop.moy/thumbs/wp.mct"):
        assert safe_segments(bad) is None, bad


# ---------------------------------------------------------------------------
# apply_ops -- the receiving half.
# ---------------------------------------------------------------------------


def test_apply_writes_atomically_and_makes_parent_dirs(tmp_path):
    root = _store(tmp_path)
    applied, errors, shelf = apply_ops(str(root), [
        {"p": "hop.moy/main.py", "t": "def _draw():\n    cls(7)\n"},
        {"p": "new.moy/sub/deep.json", "t": "{}"},
    ])
    assert (applied, errors) == (2, [])
    assert (root / "hop.moy" / "main.py").read_text().endswith("cls(7)\n")
    assert (root / "new.moy" / "sub" / "deep.json").read_text() == "{}"
    assert shelf, "a NEW cart folder appeared -- the shelf must re-scan"


def test_a_bad_op_skips_and_never_aborts_the_batch(tmp_path):
    """The client clears an answered batch whatever `err` says, so an abort
    would let one poison op starve its neighbours forever."""
    root = _store(tmp_path)
    applied, errors, _ = apply_ops(str(root), [
        {"p": "../escape.py", "t": "evil"},
        {"p": "hop.moy/ok.py", "t": "fine"},
        {"p": "system.json", "t": "not yours"},     # top-level = system state
        {"p": "hop.moy/journal/j.jsonl", "t": "no"},
        "not even an op",
        {"p": "hop.moy/huge.py", "t": "x" * (PART_MAX * 2 + 1)},
    ])
    assert applied == 1
    assert [i for i, _ in errors] == [0, 2, 3, 4, 5]
    assert (root / "hop.moy" / "ok.py").read_text() == "fine"
    assert not (tmp_path / "escape.py").exists()
    assert not (root.parent / "escape.py").exists()


def test_shelf_dirty_names_exactly_what_the_launcher_shows(tmp_path):
    root = _store(tmp_path)
    _, _, shelf = apply_ops(str(root), [{"p": "hop.moy/main.py", "t": "x=1"}])
    assert not shelf, "a code edit changes nothing the shelf renders"
    _, _, shelf = apply_ops(str(root), [
        {"p": "hop.moy/manifest.json", "t": '{"title": "Hop!"}'}])
    assert shelf
    _, _, shelf = apply_ops(str(root), [{"p": "hop.moy", "dc": 1}])
    assert shelf


def test_cart_delete_takes_the_journal_and_thumbs_with_it(tmp_path):
    """dc removes what never crossed the wire too -- a deleted cart's history
    dies with it, same as the on-device picker's delete."""
    root = _store(tmp_path)
    (root / "hop.moy" / "journal").mkdir()
    (root / "hop.moy" / "journal" / "journal.jsonl").write_text("{}\n")
    applied, errors, _ = apply_ops(str(root), [{"p": "hop.moy", "dc": 1}])
    assert (applied, errors) == (1, [])
    assert not (root / "hop.moy").exists()
    # ...and dc is CART-shaped only: never a nested dir, never the root.
    _, errors, _ = apply_ops(str(root), [{"p": "a/b", "dc": 1}])
    assert errors


def test_chunked_files_assemble_and_publish_atomically(tmp_path):
    root = _store(tmp_path)
    big = "line\n" * 5000
    a, b = big[:9000], big[9000:]
    applied, errors, _ = apply_ops(str(root), [
        {"p": "hop.moy/big.lua", "t": a, "part": 0},
        {"p": "hop.moy/big.lua", "t": b, "part": 1},
    ])
    assert (applied, errors) == (2, [])
    assert not (root / "hop.moy" / "big.lua").exists(), \
        "nothing publishes until the pub op"
    applied, errors, _ = apply_ops(str(root), [
        {"p": "hop.moy/big.lua", "pub": 1}])
    assert (applied, errors) == (1, [])
    assert (root / "hop.moy" / "big.lua").read_text() == big
    assert not (root / "hop.moy" / "big.lua.tmp").exists()
    # A retry restarts at part 0: the first part TRUNCATES the staging tmp.
    apply_ops(str(root), [{"p": "hop.moy/big.lua", "t": "v2", "part": 0},
                          {"p": "hop.moy/big.lua", "pub": 1}])
    assert (root / "hop.moy" / "big.lua").read_text() == "v2"


def test_publish_without_staged_tmp_is_an_error_not_a_truncation(tmp_path):
    root = _store(tmp_path)
    applied, errors, _ = apply_ops(str(root), [{"p": "hop.moy/main.py",
                                                "t": "", "pub": 1}])
    assert applied == 0 and errors
    assert (root / "hop.moy" / "main.py").read_text().endswith("cls(1)\n")


def test_parse_batch_refuses_malformed_bodies():
    assert parse_batch(b"not json") == (None, None)
    assert parse_batch(b"[1,2]") == (None, None)
    assert parse_batch(json.dumps({"v": 99, "ops": []})) == (None, None)
    assert parse_batch(json.dumps({"v": 1, "ops": {}})) == (None, None)
    ops, pin = parse_batch(json.dumps({"v": 1, "pin": "77", "ops": []}))
    assert (ops, pin) == ([], "77")


# ---------------------------------------------------------------------------
# StoreWatcher -- the browser half.
# ---------------------------------------------------------------------------


def _drain(w):
    """Every pending op, acked ok -- what the worker's pump does over time."""
    out = []
    while True:
        ops = w.take()
        if not ops:
            return out
        out.extend(ops)
        w.ack(True)


def test_watcher_baseline_is_quiet_and_a_commit_is_noticed(tmp_path):
    root = _store(tmp_path)
    w = StoreWatcher(str(root))
    assert not w.sweep(), "the freshly pulled store has nothing to tell"
    p = root / "hop.moy" / "main.py"
    p.write_text("def _draw():\n    cls(9)\n")
    _bump_mtime(p)
    assert w.sweep()
    ops = _drain(w)
    assert ops == [{"p": "hop.moy/main.py", "t": "def _draw():\n    cls(9)\n"}]
    assert not w.sweep(), "shipped means settled"


def test_byte_identical_rewrite_ships_nothing(tmp_path):
    """The reload path rewrites every pulled file; the crc is what keeps that
    from re-shipping the whole store."""
    root = _store(tmp_path)
    w = StoreWatcher(str(root))
    p = root / "hop.moy" / "main.py"
    p.write_text(p.read_text())
    _bump_mtime(p)
    w.sweep()
    assert w.take() is None


def test_watcher_skips_what_never_crosses(tmp_path):
    root = _store(tmp_path)
    w = StoreWatcher(str(root))
    (root / "hop.moy" / "journal").mkdir()
    (root / "hop.moy" / "journal" / "journal.jsonl").write_text("{}\n")
    (root / "hop.moy" / "main.py.bak").write_text("old")
    (root / "loose.txt").write_text("top-level system state")
    w.sweep()
    assert w.take() is None


def test_new_and_deleted_files_and_carts(tmp_path):
    root = _store(tmp_path)
    w = StoreWatcher(str(root))
    (root / "hop.moy" / "extra.py").write_text("x=1")
    (root / "hop.moy" / "scenes" / "a.moyscene").unlink()
    w.sweep()
    ops = _drain(w)
    assert {"p": "hop.moy/extra.py", "t": "x=1"} in ops
    assert {"p": "hop.moy/scenes/a.moyscene", "d": 1} in ops
    # Whole-cart delete arrives as ONE dc, not a file spray.
    import shutil
    shutil.rmtree(root / "hop.moy")
    w.sweep()
    ops = _drain(w)
    assert ops == [{"p": "hop.moy", "dc": 1}]


def test_a_failed_send_requeues_everything_it_carried(tmp_path):
    root = _store(tmp_path)
    w = StoreWatcher(str(root))
    p = root / "hop.moy" / "main.py"
    p.write_text("v2")
    _bump_mtime(p)
    w.sweep()
    first = w.take()
    assert first and w.take() is None, "one batch in flight at a time"
    w.ack(False)
    again = w.take()
    assert again == first, "the drop-and-reattach path: nothing is lost"
    w.ack(True)
    assert w.take() is None


def test_a_big_file_spans_batches_and_survives_a_mid_file_drop(tmp_path):
    root = _store(tmp_path)
    w = StoreWatcher(str(root))
    big = "x" * (BATCH_BUDGET + PART_MAX * 2)
    p = root / "hop.moy" / "big.lua"
    p.write_text(big)
    w.sweep()
    ops1 = w.take()
    assert ops1 and all(o.get("part") is not None for o in ops1)
    assert sum(len(o.get("t", "")) for o in ops1) <= BATCH_BUDGET
    w.ack(False)                       # the connection died mid-file
    # The retry starts over at part 0 -- and the reassembly is still whole.
    parts = []
    published = False
    while True:
        ops = w.take()
        if not ops:
            break
        for o in ops:
            if o.get("part") is not None:
                assert o["p"] == "hop.moy/big.lua" and o["part"] == len(parts)
                parts.append(o["t"])
            elif o.get("pub"):
                published = True
        w.ack(True)
    assert published and "".join(parts) == big


def test_take_json_carries_the_protocol_and_the_pin(tmp_path):
    root = _store(tmp_path)
    w = StoreWatcher(str(root))
    p = root / "hop.moy" / "main.py"
    p.write_text("v2")
    _bump_mtime(p)
    w.sweep()
    doc = json.loads(w.take_json("1234"))
    assert doc["v"] == moy_sync.PROTOCOL_V and doc["pin"] == "1234"
    ops, pin = parse_batch(json.dumps(doc))
    assert pin == "1234" and ops == doc["ops"]


def test_same_second_second_write_is_caught_by_the_hot_set(tmp_path):
    """size+mtime alone cannot see a same-second same-size rewrite on a
    second-granularity VFS; files at the sweep's newest second re-read."""
    root = _store(tmp_path)
    w = StoreWatcher(str(root))
    p = root / "hop.moy" / "main.py"
    st = os.stat(p)
    p.write_text("def _draw():\n    cls(2)\n")          # same length as cls(1)
    os.utime(p, (st.st_atime, st.st_mtime))             # freeze size AND mtime
    w._hot = tuple(w._snap)             # what a real sweep marks after a write
    w.sweep()
    ops = _drain(w)
    assert {"p": "hop.moy/main.py", "t": "def _draw():\n    cls(2)\n"} in ops
