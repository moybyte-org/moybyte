"""The #111 universal op-history CORE: runtime/op_history.py (the in-RAM
undo/redo primitive shared by every editor + Desk Lab app) and the journal
persistence adapter's additive `ops` field. Same shared modules the device
freezes -- MicroPython-safe (plain classes, json-able ops)."""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from runtime import op_history  # noqa: E402
from runtime.op_history import History, OpCodec  # noqa: E402

import pytest  # noqa: E402


# -- toy documents + codecs ------------------------------------------------------
#
# The doc is a tiny mutable "canvas": a list of ints. An op is a JSON-able tuple
# ("set", index, old_value, new_value) -- it carries its OWN pre-image (old), so
# the invert codec can reverse it with no extra state. The surface applies the op
# to the doc BEFORE calling record() (exactly how a real editor draws first, then
# logs the stroke), so the toy helper set_op() does the same.


class InvertCodec(OpCodec):
    """The preferred path: reverse an op in place using its carried pre-image."""

    def apply(self, doc, op):
        _, i, _old, new = op
        doc[i] = new

    def invert(self, doc, op):
        _, i, old, _new = op
        doc[i] = old


class ReplayCodec(OpCodec):
    """The fallback path: no cheap invert, but snapshot/restore the whole doc."""

    def apply(self, doc, op):
        _, i, _old, new = op
        doc[i] = new

    def snapshot(self, doc):
        return list(doc)

    def restore(self, doc, blob):
        doc[:] = list(blob)


def _set_op(doc, i, new):
    """Apply a set to the doc (as the surface would) and return the op to record."""
    op = ("set", i, doc[i], new)
    doc[i] = new
    return op


# -- record / undo / redo semantics ---------------------------------------------

@pytest.mark.parametrize("codec_cls", [InvertCodec, ReplayCodec])
def test_record_undo_redo_roundtrip(codec_cls):
    doc = [0, 0, 0]
    h = History(doc, codec_cls())
    assert not h.can_undo() and not h.can_redo()

    h.record(_set_op(doc, 0, 5))
    h.record(_set_op(doc, 1, 7))
    assert doc == [5, 7, 0]
    assert h.can_undo() and not h.can_redo()

    assert h.undo() == ("set", 1, 0, 7)
    assert doc == [5, 0, 0]                # invert or replay both land here
    assert h.can_redo()
    assert h.undo() == ("set", 0, 0, 5)
    assert doc == [0, 0, 0]
    assert not h.can_undo()

    assert h.undo() is None                # floor: nothing more in the session
    assert doc == [0, 0, 0]

    assert h.redo() == ("set", 0, 0, 5)
    assert doc == [5, 0, 0]
    assert h.redo() == ("set", 1, 0, 7)
    assert doc == [5, 7, 0]
    assert h.redo() is None


@pytest.mark.parametrize("codec_cls", [InvertCodec, ReplayCodec])
def test_record_clears_the_redo_stack(codec_cls):
    doc = [0, 0]
    h = History(doc, codec_cls())
    h.record(_set_op(doc, 0, 1))
    h.record(_set_op(doc, 1, 2))
    h.undo()                                # redo now has one op
    assert h.can_redo()
    h.record(_set_op(doc, 0, 9))           # a new action forks the timeline
    assert not h.can_redo()                # redo cleared (Google-Docs rule)
    assert doc == [9, 0]


def test_replay_path_reconstructs_from_the_base_snapshot():
    # The replay codec never inverts -- undo restores the session base and
    # re-applies the surviving ops, so a non-trivial interleave still lands right.
    doc = [1, 2, 3]
    h = History(doc, ReplayCodec())
    h.record(_set_op(doc, 0, 10))
    h.record(_set_op(doc, 2, 30))
    h.record(_set_op(doc, 0, 11))
    assert doc == [11, 2, 30]
    h.undo()                                # drop the last set of index 0
    assert doc == [10, 2, 30]
    h.undo()
    assert doc == [10, 2, 3]
    h.undo()
    assert doc == [1, 2, 3]                # all the way back to the base


def test_invert_is_preferred_when_the_codec_offers_both():
    # A codec with invert AND snapshot uses invert for undo (snapshot stays for
    # keyframes). We prove it by making snapshot() explode -- undo must not call it.
    class Boom(InvertCodec):
        def snapshot(self, doc):
            raise AssertionError("undo must use invert, not snapshot")

        def restore(self, doc, blob):
            raise AssertionError("undo must use invert, not restore")

    doc = [0]
    h = History(doc, Boom())
    h.record(_set_op(doc, 0, 4))
    h.undo()
    assert doc == [0]


def test_codec_without_undo_path_is_rejected():
    class ApplyOnly(OpCodec):
        def apply(self, doc, op):
            pass

    with pytest.raises(ValueError):
        History([], ApplyOnly())


# -- the persistence seam: flush / segment cap ----------------------------------

def test_flush_drains_the_pending_batch():
    doc = [0, 0]
    h = History(doc, InvertCodec())
    a = _set_op(doc, 0, 1)
    b = _set_op(doc, 1, 2)
    h.record(a)
    h.record(b)
    assert h.peek() == [a, b]              # peek does NOT drain
    assert h.flush() == [a, b]
    assert h.flush() == []                 # drained
    # A record after a flush starts a fresh batch.
    c = _set_op(doc, 0, 9)
    h.record(c)
    assert h.flush() == [c]


def test_undo_net_cancels_an_unflushed_op():
    # record A, record B, undo B -> the batch the adapter persists is just [A]
    # (B never happened as far as the commit is concerned).
    doc = [0, 0]
    h = History(doc, InvertCodec())
    a = _set_op(doc, 0, 1)
    b = _set_op(doc, 1, 2)
    h.record(a)
    h.record(b)
    h.undo()
    assert h.flush() == [a]


def test_segment_cap_forces_a_keyframe():
    doc = [0] * 8
    h = History(doc, InvertCodec(), max_ops=4)
    for i in range(3):
        h.record(_set_op(doc, i, i + 1))
    assert not h.needs_keyframe()          # 3 < 4
    h.record(_set_op(doc, 3, 4))
    assert h.needs_keyframe()              # >= max_ops -> the adapter must keyframe
    h.mark_keyframe()                      # adapter persisted a full snapshot
    assert not h.needs_keyframe()          # counter reset -> ops ride on top again
    h.record(_set_op(doc, 4, 5))
    assert not h.needs_keyframe()


def test_keyframe_snapshot_available_for_the_adapter():
    doc = [1, 2, 3]
    h = History(doc, ReplayCodec())
    doc[0] = 9
    assert h.keyframe() == [9, 2, 3]       # a live full snapshot for a sidecar keyframe
    # An invert-only codec can't snapshot -> keyframe() is None (the persistence
    # layer keeps its own full-file snapshots regardless).
    assert History([0], InvertCodec()).keyframe() is None


def test_clear_rebaselines_the_history():
    doc = [0, 0]
    h = History(doc, ReplayCodec())
    h.record(_set_op(doc, 0, 5))
    h.clear()
    assert not h.can_undo() and not h.can_redo()
    assert h.flush() == []
    # A subsequent undo replays from the NEW base (doc as it was at clear()).
    h.record(_set_op(doc, 1, 7))
    h.undo()
    assert doc == [5, 0]                   # back to the post-clear baseline, not [0,0]


# -- journal adapter: the additive `ops` field (#111) ---------------------------

def _cart(tmp_path):
    from runtime import moy_carts
    root = str(tmp_path / "carts")
    moy_carts.ensure_dirs(root)
    c = moy_carts.create("Op Journal", root, src="v0\n", type="app")
    return moy_carts, c["path"]


def test_journal_ops_field_roundtrips(tmp_path):
    from runtime import moy_journal
    mc, path = _cart(tmp_path)
    ops = [["set", 0, 0, 5], ["set", 1, 0, 7]]
    seq = mc.journal_append(path, "main.py", "v1\n", ops=ops)
    assert seq is not None
    entries = moy_journal._journal_load_entries(
        moy_journal._journal_paths(path)[1])
    entry = [e for e in entries if e["seq"] == seq][0]
    assert entry["ops"] == ops
    assert moy_journal.journal_entry_ops(entry) == ops


def test_journal_without_ops_writes_no_key(tmp_path):
    from runtime import moy_journal
    mc, path = _cart(tmp_path)
    seq = mc.journal_append(path, "main.py", "v1\n")     # no ops -> additive absence
    entries = moy_journal._journal_load_entries(
        moy_journal._journal_paths(path)[1])
    entry = [e for e in entries if e["seq"] == seq][0]
    assert "ops" not in entry
    assert moy_journal.journal_entry_ops(entry) == []     # accessor tolerates absence


def test_old_journal_line_without_ops_loads_unchanged(tmp_path):
    # A pre-#111 journal line (no `ops` key) must load + walk exactly as before.
    from runtime import moy_journal
    mc, path = _cart(tmp_path)
    jdir, log, _cur, _snap = moy_journal._journal_paths(path)
    legacy = {"seq": 1, "ts": 0, "file": "main.py", "snap": "s/0001-main.py", "len": 3}
    moy_journal._mkdir(jdir)
    with open(log, "w") as f:
        f.write(json.dumps(legacy) + "\n")
    entries = moy_journal._journal_load_entries(log)
    assert entries[0]["seq"] == 1
    assert moy_journal.journal_entry_ops(entries[0]) == []
