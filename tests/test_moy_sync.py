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
                              safe_segments, PART_MAX, BATCH_BUDGET,
                              FILES_ROOT_ID)


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


def _drain_batches(w, pin=None):
    """Every wire batch the watcher produces, acked ok -- the worker's pump."""
    out = []
    while True:
        body = w.take_json(pin)
        if not body:
            return out
        out.append(json.loads(body))
        w.ack(True)


def test_no_batch_overshoots_the_transport_request_cap(tmp_path):
    """A whole-file op subtracts from the budget AFTER it is appended, so an
    unguarded take() could pack ~48KB into one batch -- which, JSON-escaped, can
    breach the receiver's 64KB request cap and wedge the client on a permanent
    400/requeue. Each batch's ENCODED body must stay under the cap, and every
    file must still arrive."""
    root = tmp_path / "carts"
    (root / "big.moy").mkdir(parents=True)
    w = StoreWatcher(str(root))              # baseline is the empty cart dir
    for i in range(6):
        # 15KB each: two fit a 32KB batch, a third would overshoot.
        (root / "big.moy" / ("f%d.txt" % i)).write_text("x" * 15000)
    w.sweep()
    batches = _drain_batches(w)
    assert len(batches) >= 3, "the files must span batches, or the test is moot"
    for doc in batches:
        body = json.dumps(doc)
        assert len(body) <= 65536, "a batch breached the 64KB transport cap: %d" % len(body)
    got = set()
    for doc in batches:
        for op in doc["ops"]:
            if op.get("t") is not None and op.get("part") is None:
                got.add(op["p"])
    assert got == {"big.moy/f%d.txt" % i for i in range(6)}, got


def test_a_delete_cart_is_not_re_emitted_across_a_multi_batch_file(tmp_path):
    """A dc emitted in a batch that then starts a big file must not ride again
    in the next batch -- a no-op if the cart stays gone, but a wrong re-delete
    if it was recreated between the two batches."""
    root = tmp_path / "carts"
    (root / "keep.moy").mkdir(parents=True)
    (root / "gone.moy").mkdir()
    (root / "gone.moy" / "main.py").write_text("z")
    w = StoreWatcher(str(root))              # baseline: keep.moy empty, gone.moy present
    # A big new file that spans batches AND a whole-cart delete, both pending
    # in the same sweep -- the batch that starts the file must not re-ship the dc.
    (root / "keep.moy" / "big.lua").write_text("y" * (BATCH_BUDGET + PART_MAX * 2))
    import shutil
    shutil.rmtree(root / "gone.moy")
    w.sweep()
    dc_ops = 0
    for doc in _drain_batches(w):
        for op in doc["ops"]:
            if op.get("dc"):
                assert op["p"] == "gone.moy", op
                dc_ops += 1
    assert dc_ops == 1, "the dc rode %d times, not once" % dc_ops


# ---------------------------------------------------------------------------
# Store-walk resilience -- a removable card can EIO under a socket-paced pull.
# ---------------------------------------------------------------------------


def test_a_transient_card_read_is_retried_not_dropped(tmp_path):
    """A removable store (the Guition's TF card) can EIO a read that lands
    seconds into a socket-paced pull; the walk must RETRY, because an aborted
    store stream truncates the browser's whole carts.json. A permanent
    non-OSError (a binary file) is NOT retried -- it is skipped as before."""
    import builtins
    import pytest

    calls = [0]

    def flaky():
        calls[0] += 1
        if calls[0] < moy_sync._IO_RETRIES:
            raise OSError(5, "EIO")
        return "recovered"
    assert moy_sync._retry_io(flaky, "DEFAULT") == "recovered"
    assert moy_sync._retry_io(
        lambda: (_ for _ in ()).throw(OSError(5, "EIO")), "DEFAULT") == "DEFAULT"
    with pytest.raises(ValueError):          # only I/O is transient
        moy_sync._retry_io(lambda: (_ for _ in ()).throw(ValueError()), "D")

    # _read_text retries a real flaky open and returns the bytes, not None.
    p = tmp_path / "hop.moy" / "boom.py"
    p.parent.mkdir(parents=True)
    p.write_text("def _draw():\n    cls(3)\n")
    state = {"n": 0}
    orig = builtins.open

    def eio_once(path, *a, **k):
        if str(path).endswith("boom.py") and state["n"] < 1:
            state["n"] += 1
            raise OSError(5, "EIO")
        return orig(path, *a, **k)
    builtins.open = eio_once
    try:
        assert moy_sync._read_text(str(p)).endswith("cls(3)\n")
    finally:
        builtins.open = orig


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
    none = (None, None, None)
    assert parse_batch(b"not json") == none
    assert parse_batch(b"[1,2]") == none
    assert parse_batch(json.dumps({"v": 99, "ops": []})) == none
    assert parse_batch(json.dumps({"v": 1, "ops": {}})) == none
    ops, pin, root = parse_batch(json.dumps({"v": 1, "pin": "77", "ops": []}))
    assert (ops, pin, root) == ([], "77", "carts")


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


def test_a_site_mode_watcher_sweeps_the_journal_and_a_board_one_never_does(
        tmp_path):
    """THE JOURNAL LIVES WITH THE STORE OF RECORD (2026-08-25), and the whole
    of the site-mode half is this one argument.

    In mode 1 the browser's OPFS is where the cart durably lives, so its undo
    history has to ride the same sweep or die at the next reload. In board mode
    the board is of record and journals the pushes itself -- so the DEFAULT
    watcher must stay byte-identical, which is what the second half asserts.
    A regression either way is silent: a lost history, or a board handed
    somebody else's."""
    root = _store(tmp_path)
    (root / "hop.moy" / "journal").mkdir()
    (root / "hop.moy" / "journal" / "journal.jsonl").write_text('{"seq": 1}\n')
    (root / "hop.moy" / "journal" / "s").mkdir()
    (root / "hop.moy" / "journal" / "s" / "0001-main.py").write_text("x = 1\n")
    (root / "hop.moy" / "main.py.bak").write_text("crash-safety leftovers")
    (root / "hop.moy" / "thumbs").mkdir()
    (root / "hop.moy" / "thumbs" / "wp1x1.mct").write_text("cache")

    board = StoreWatcher(str(root))
    board.sweep()
    assert board.take() is None, "a board must never be sent a journal"

    site = StoreWatcher(str(root), skip=moy_sync.skip_keep_journal)
    assert not site.sweep(), "the baseline adopts the store as it stands"
    p = root / "hop.moy" / "journal" / "journal.jsonl"
    p.write_text('{"seq": 1}\n{"seq": 2}\n')
    _bump_mtime(p)
    site.sweep()
    paths = [op["p"] for op in _drain(site)]
    assert "hop.moy/journal/journal.jsonl" in paths
    # ...and only the journal was let through: the crash-safety artifacts and
    # the regenerable cache stay home in BOTH modes.
    assert not any(x.endswith(".bak") or "/thumbs/" in x for x in paths), paths


def test_the_receiver_journals_a_push_and_only_when_asked(tmp_path):
    """The board half of the same doctrine: a browser commit lands in the
    cart's own journal, in the shape `Project.commit_*` writes, so the
    console's on-glass UNDO can walk back through it.

    `journal=False` is the default and stays the default -- a receiver that is
    a scratch directory should not grow a history nobody walks."""
    from runtime import moy_journal

    root = _store(tmp_path)
    cart = str(root / "hop.moy")

    apply_ops(str(root), [{"p": "hop.moy/main.py", "t": "cls(2)\n"}])
    assert not (root / "hop.moy" / "journal").exists(), "unasked-for history"

    apply_ops(str(root), [{"p": "hop.moy/main.py", "t": "cls(3)\n"}],
              journal=True)
    apply_ops(str(root), [{"p": "hop.moy/main.py", "t": "cls(4)\n"}],
              journal=True)
    entries = moy_journal._journal_load_entries(
        str(root / "hop.moy" / "journal" / "journal.jsonl"))
    assert [e["file"] for e in entries] == ["main.py", "main.py"], entries
    # The commit is REAL: an undo walks the live file back a step.
    assert moy_journal.journal_can_undo(cart, ("main.py",))
    assert moy_journal.journal_undo(cart, ("main.py",)) == "main.py"
    assert (root / "hop.moy" / "main.py").read_text() == "cls(3)\n"


def test_a_chunked_publish_is_journaled_from_what_landed(tmp_path):
    """The parts never were all resident here at once, so the snapshot has to
    come from the published FILE. Getting this wrong journals one chunk."""
    from runtime import moy_journal

    root = _store(tmp_path)
    big = "y" * 40
    apply_ops(str(root), [
        {"p": "hop.moy/big.lua", "t": big[:20], "part": 0},
        {"p": "hop.moy/big.lua", "t": big[20:], "part": 1},
        {"p": "hop.moy/big.lua", "pub": 1}], journal=True)
    assert (root / "hop.moy" / "big.lua").read_text() == big
    entries = moy_journal._journal_load_entries(
        str(root / "hop.moy" / "journal" / "journal.jsonl"))
    assert len(entries) == 1 and entries[0]["file"] == "big.lua"
    snap = root / "hop.moy" / "journal" / entries[0]["snap"]
    assert snap.read_text() == big, "the snapshot is one chunk, not the file"


def test_a_files_push_is_never_journaled(tmp_path):
    """The #108 layer's undo is `files/.history/` op sidecars -- a different
    mechanism with a different shape, and deliberately not grown here. A
    drawing pushed from a browser lands as a file and nothing else."""
    _store(tmp_path)
    files = _files_store(tmp_path)
    apply_ops(str(files), [{"p": "drawings/sunset.moyimg", "t": "0,1,"}],
              root_id=moy_sync.FILES_ROOT_ID, journal=True)
    assert (files / "drawings" / "sunset.moyimg").read_text() == "0,1,"
    assert not (files / "drawings" / "journal").exists()
    assert not any(p.name == "journal.jsonl" for p in files.rglob("*"))


def test_a_journal_that_cannot_be_written_never_costs_the_write(tmp_path):
    """The file has already landed by the time the journal is touched, so a
    journal failure is a missing history entry and must never be a lost edit."""
    root = _store(tmp_path)
    real = moy_sync._moy_journal()
    assert real is not None

    class Broken:
        @staticmethod
        def journal_append(*a, **kw):
            raise OSError("no space left on device")

    moy_sync._JOURNAL[:] = [Broken]
    try:
        applied, errors, _ = apply_ops(
            str(root), [{"p": "hop.moy/main.py", "t": "cls(5)\n"}],
            journal=True)
    finally:
        moy_sync._JOURNAL[:] = [real]
    assert (applied, errors) == (1, [])
    assert (root / "hop.moy" / "main.py").read_text() == "cls(5)\n"


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
    assert "root" not in doc, "a carts batch stays byte-shaped as v1 boards read it"
    ops, pin, root = parse_batch(json.dumps(doc))
    assert pin == "1234" and ops == doc["ops"] and root == "carts"


# ---------------------------------------------------------------------------
# The #108 files root: the second store the same protocol carries. Everything
# below is a refusal or a routing rule, which is the whole of what makes a
# files batch safe -- it is the same apply reaching a different tree.
# ---------------------------------------------------------------------------


def _files_store(tmp_path, name="files"):
    """A files root shaped like a real one: two kinds, a folder-valued
    recording, and the two directories that must never travel."""
    root = tmp_path / name
    (root / "drawings").mkdir(parents=True)
    (root / "drawings" / "sunset.moyimg").write_text("0,1,")
    (root / "recordings" / "take_1").mkdir(parents=True)
    (root / "recordings" / "take_1" / "part0.json").write_text("[1]")
    (root / ".history" / "drawings").mkdir(parents=True)
    (root / ".history" / "drawings" / "sunset.jsonl").write_text('{"t": "kf"}\n')
    (root / "trash" / "drawings").mkdir(parents=True)
    (root / "trash" / "drawings" / "gone.moyimg").write_text("9,")
    return root


def _files_batch(*ops):
    doc = {"v": moy_sync.PROTOCOL_V_ROOTED, "root": FILES_ROOT_ID,
           "ops": list(ops)}
    return json.dumps(doc)


def test_a_files_batch_names_its_root_and_a_v1_board_refuses_it():
    """The back-compat mechanism, and it is a SAFETY property. A board flashed
    before files sync checks `v != 1` and answers "bad batch"; without the bump
    its apply would read `drawings/sunset.moyimg` as a cart-relative path and
    write it into the CARTS root, inventing a `drawings` cart on the shelf."""
    body = _files_batch({"p": "drawings/sunset.moyimg", "t": "0,"})
    ops, _pin, root = parse_batch(body)
    assert root == FILES_ROOT_ID and len(ops) == 1

    def old_parse_batch(doc):                # the shipped v1 receiver, verbatim
        return doc.get("v") == moy_sync.PROTOCOL_V

    assert not old_parse_batch(json.loads(body)), "a v1 board must refuse this"
    assert old_parse_batch({"v": 1, "ops": []}), \
        "...while the carts batch it already speaks keeps working"


def test_a_files_batch_misrouted_to_carts_would_have_made_a_bogus_cart(tmp_path):
    """The concrete damage the version bump prevents, pinned so nobody
    'simplifies' the two versions back into one."""
    root = _store(tmp_path)
    applied, errors, shelf = apply_ops(
        str(root), [{"p": "drawings/sunset.moyimg", "t": "0,"}], "carts")
    assert (applied, errors) == (1, []) and shelf
    assert (root / "drawings" / "sunset.moyimg").exists(), \
        "a carts receiver has no way to know this was never a cart"


def test_a_v1_batch_may_not_smuggle_another_root():
    """The version is what a v1 receiver checks, so the two must never be able
    to disagree about the same bytes."""
    assert parse_batch(json.dumps(
        {"v": 1, "root": "files", "ops": []})) == (None, None, None)
    assert parse_batch(json.dumps(
        {"v": 2, "root": "wifi", "ops": []})) == (None, None, None)
    assert parse_batch(json.dumps({"v": 2, "ops": []})) == (None, None, None)


def test_files_paths_must_start_with_a_kind_the_store_knows(tmp_path):
    """An ALLOWLIST over moy_carts.FILE_KINDS, which is also the ONE rule that
    keeps .history/ and trash/ off the wire -- neither is a kind."""
    root = _files_store(tmp_path)
    applied, errors, _ = apply_ops(str(root), [
        {"p": ".history/drawings/sunset.jsonl", "t": "no"},
        {"p": "trash/drawings/gone.moyimg", "t": "no"},
        {"p": "wifi.json", "t": "no"},
        {"p": "carts/hop.moy/main.py", "t": "no"},
        {"p": "drawings/ok.moyimg", "t": "1,"},
    ], FILES_ROOT_ID)
    assert applied == 1
    assert [i for i, _ in errors] == [0, 1, 2, 3]
    assert (root / "drawings" / "ok.moyimg").read_text() == "1,"
    assert (root / ".history" / "drawings" / "sunset.jsonl").read_text() \
        == '{"t": "kf"}\n'


def test_dc_deletes_an_item_and_never_a_whole_kind(tmp_path):
    """A recording is folder-valued and must die as a unit; a kind dir must
    not, or one item leaving our copy would wipe the peer's whole drawings."""
    root = _files_store(tmp_path)
    _, errors, _ = apply_ops(str(root), [{"p": "drawings", "dc": 1}],
                             FILES_ROOT_ID)
    assert errors and (root / "drawings").is_dir()
    applied, errors, shelf = apply_ops(
        str(root), [{"p": "recordings/take_1", "dc": 1}], FILES_ROOT_ID)
    assert (applied, errors) == (1, [])
    assert not (root / "recordings" / "take_1").exists()
    assert (root / "recordings").is_dir(), "the kind dir survives its item"
    assert not shelf, "the launcher renders no recordings"


def test_a_files_batch_never_dirties_the_shelf(tmp_path):
    root = _files_store(tmp_path)
    for ops in ([{"p": "drawings/new.moyimg", "t": "0,"}],
                [{"p": "drawings/sunset.moyimg", "d": 1}],
                [{"p": "docs/story.moytext", "t": "hi"}]):
        _, errors, shelf = apply_ops(str(root), ops, FILES_ROOT_ID)
        assert not errors and not shelf


def test_the_files_root_is_created_on_demand(tmp_path):
    """The carts root always exists; the files root does not until the kid
    makes something -- and the first thing a peer sends may BE that."""
    root = tmp_path / "files"
    applied, errors, _ = apply_ops(
        str(root), [{"p": "drawings/first.moyimg", "t": "0,"}], FILES_ROOT_ID)
    assert (applied, errors) == (1, [])
    assert (root / "drawings" / "first.moyimg").read_text() == "0,"


def test_the_files_watcher_walks_kinds_and_nothing_else(tmp_path):
    root = _files_store(tmp_path)
    w = StoreWatcher(str(root), root_id=FILES_ROOT_ID)
    assert not w.sweep(), "the freshly pulled files root has nothing to tell"
    shipped = {rel for rel in w._snap}
    assert shipped == {"drawings/sunset.moyimg", "recordings/take_1/part0.json"}
    assert not any(r.startswith(".history/") or r.startswith("trash/")
                   for r in shipped)
    # A write inside either stays invisible, sweep after sweep.
    p = root / "trash" / "drawings" / "gone.moyimg"
    p.write_text("still here")
    _bump_mtime(p)
    assert not w.sweep()
    assert w.take() is None


def test_the_files_watcher_stamps_the_rooted_protocol(tmp_path):
    root = _files_store(tmp_path)
    w = StoreWatcher(str(root), root_id=FILES_ROOT_ID)
    p = root / "drawings" / "sunset.moyimg"
    p.write_text("2,")
    _bump_mtime(p)
    w.sweep()
    doc = json.loads(w.take_json("1234"))
    assert doc["v"] == moy_sync.PROTOCOL_V_ROOTED
    assert doc["root"] == FILES_ROOT_ID and doc["pin"] == "1234"
    ops, pin, root_id = parse_batch(json.dumps(doc))
    assert (pin, root_id) == ("1234", FILES_ROOT_ID)
    assert ops == [{"p": "drawings/sunset.moyimg", "t": "2,"}]


def test_a_missing_files_root_is_an_empty_watcher_not_a_crash(tmp_path):
    """web_boot only builds this watcher when the pull made the directory, but
    a root can also vanish under a running sweep."""
    root = tmp_path / "nothing"
    w = StoreWatcher(str(root), root_id=FILES_ROOT_ID)
    assert not w.sweep() and w.take() is None


# ---------------------------------------------------------------------------
# The browser's TWO-ROOT pump (firmware/web_runner/web_boot.py). One batch is
# in flight at a time across BOTH roots, so which watcher took the batch the
# worker is answering is a fact somebody has to remember -- and the files
# watcher existing at all is a capability the pull decided.
# ---------------------------------------------------------------------------


def _web_boot():
    """web_boot imported for its sync verbs only -- no VM, no canvas."""
    import pytest
    for p in ("firmware/web_runner", "device", "runtime"):
        d = str(ROOT / p)
        if d not in sys.path:
            sys.path.insert(0, d)
    try:
        import web_boot
    except Exception as exc:  # noqa: BLE001 -- a wasm-only dep would skip, not fail
        pytest.skip("web_boot not importable on the host: %s" % exc)
    return web_boot


def _wired(tmp_path, files=True):
    """web_boot's `_S` wired the way boot() wires it: a carts watcher always, a
    files watcher only when the pull left a files root behind."""
    wb = _web_boot()
    carts = tmp_path / "carts"
    (carts / "hop.moy").mkdir(parents=True)
    (carts / "hop.moy" / "main.py").write_text("x = 1\n")
    if files:
        _files_store(tmp_path)
    wb._S["sync"] = StoreWatcher(str(carts))
    wb._S["sync_files"] = wb._files_watcher(moy_sync, str(carts))
    wb._S.pop("sync_took", None)
    wb._S.pop("sync_pin", None)
    return wb, carts


def test_no_files_root_means_no_files_watcher(tmp_path):
    """The capability probe, and the reason one 404 cannot disable the carts
    push: worker.js creates the files root only when GET /files.json answered,
    so a board that predates files sync leaves nothing here to watch."""
    wb, _carts = _wired(tmp_path, files=False)
    assert wb._S["sync"] is not None
    assert wb._S["sync_files"] is None
    assert wb.sync_poll_json() == "", "a quiet store says nothing either way"


def test_the_pump_drains_carts_first_then_files(tmp_path):
    """A cart edit is what the kid is looking at on the glass; a drawing
    landing a second later costs nobody anything."""
    wb, carts = _wired(tmp_path)
    p = carts / "hop.moy" / "main.py"
    p.write_text("x = 2\n")
    _bump_mtime(p)
    d = tmp_path / "files" / "drawings" / "sunset.moyimg"
    d.write_text("9,")
    _bump_mtime(d)
    first = json.loads(wb.sync_poll_json())
    assert first["v"] == moy_sync.PROTOCOL_V and "root" not in first
    assert wb.sync_poll_json() == "", "one batch in flight across BOTH roots"
    wb.sync_ack(True)
    second = json.loads(wb.sync_poll_json())
    assert second["root"] == FILES_ROOT_ID
    assert second["ops"] == [{"p": "drawings/sunset.moyimg", "t": "9,"}]
    wb.sync_ack(True)
    assert wb.sync_poll_json() == ""


def test_the_ack_goes_back_to_the_watcher_that_took(tmp_path):
    """Acking the wrong root would strand its batch in flight forever and
    requeue nothing -- the edit would be silently dropped."""
    wb, _carts = _wired(tmp_path)
    d = tmp_path / "files" / "drawings" / "sunset.moyimg"
    d.write_text("9,")
    _bump_mtime(d)
    batch = json.loads(wb.sync_poll_json())
    assert batch["root"] == FILES_ROOT_ID
    wb.sync_ack(False)                       # the POST never got an answer
    again = json.loads(wb.sync_poll_json())
    assert again["ops"] == batch["ops"], "the files edit requeued, not vanished"
    wb.sync_ack(True)
    assert wb.sync_poll_json() == ""


def test_sync_off_stops_both_roots(tmp_path):
    """This is the "there is no push half HERE" answer (a static host), which
    is a different thing from a board that has /sync and no files layer."""
    wb, _carts = _wired(tmp_path)
    wb.sync_off()
    assert wb._S["sync"] is None and wb._S["sync_files"] is None
    assert wb.sync_poll_json() == ""


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
