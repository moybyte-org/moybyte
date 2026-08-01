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


# -- seed(): rebuilding undo depth from persisted ops (#111 phase 3) ------------

def test_seed_rebuilds_undo_depth_without_touching_pending_or_redo():
    # The doc is already loaded at the state these ops produced (a reopened
    # document's normal load path) -- seed() only makes undo() able to walk
    # back through them, it never re-applies anything.
    doc = [5, 7, 0]
    h = History(doc, InvertCodec())
    a = ("set", 0, 0, 5)
    b = ("set", 1, 0, 7)
    h.seed([a, b])
    assert h.can_undo() is True
    assert h.can_redo() is False
    assert h.peek() == []                  # seeded ops are NOT pending (already on disk)
    assert h.flush() == []
    assert h.undo() == b
    assert doc == [5, 0, 0]
    assert h.undo() == a
    assert doc == [0, 0, 0]
    assert h.undo() is None                # floor


def test_seed_sets_the_keyframe_cap_counter():
    h = History([0, 0, 0, 0], InvertCodec(), max_ops=2)
    h.seed([("set", 0, 0, 1), ("set", 1, 0, 2)])
    assert h.needs_keyframe() is True       # 2 >= max_ops=2
    h.mark_keyframe()
    assert h.needs_keyframe() is False


def test_seed_of_empty_or_none_is_a_no_op():
    h = History([0], InvertCodec())
    h.seed([])
    h.seed(None)
    assert not h.can_undo()


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


# -- text_diff_op: the #183 quadratic (device-only, invisible on CPython) --------


def _char_space_diff(before, after):
    """The original character-indexed implementation, kept as the ORACLE. It is
    correct on CPython and O(n^2) on MicroPython -- the fast version must agree
    with it exactly, or undo ops shift."""
    n = min(len(before), len(after))
    i = 0
    while i < n and before[i] == after[i]:
        i += 1
    max_suffix = n - i
    j = 0
    while j < max_suffix and before[len(before) - 1 - j] == after[len(after) - 1 - j]:
        j += 1
    deleted = before[i:len(before) - j]
    inserted = after[i:len(after) - j]
    if not deleted and not inserted:
        return None
    return ("edit", i, deleted, inserted)


@pytest.mark.parametrize("before,after", [
    ("", ""),
    ("", "abc"),
    ("abc", ""),
    ("abc", "abc"),
    ("abc", "abd"),
    ("def _draw():\n    cls(1)\n", "def _draw():\n    cls(2)\n"),
    ("hello world", "hello brave world"),          # pure insert, mid-string
    ("hello brave world", "hello world"),          # pure delete, mid-string
    ("aaa", "aaaa"),                               # ambiguous run: prefix/suffix overlap
    ("aaaa", "aaa"),
    ("x" * 200, "x" * 200 + "y"),                  # append at the very end
    ("y" + "x" * 200, "x" * 200),                  # delete at the very start
    ("line1\nline2\nline3\n", "line1\nCHANGED\nline3\n"),
    ("héllo wörld", "héllo wärld"),                # multi-byte, changed mid-string
    ("héllo", "héllo!"),                           # multi-byte prefix, ASCII append
    ("日本語のテキスト", "日本語のテキスト!"),
    ("日本語", "日語"),                             # delete a whole multi-byte char
    # Chunk-boundary cases: the scan strides in _DIFF_STEP blocks, so an edit
    # landing exactly ON a block edge (or one byte either side of it) is where a
    # striding implementation goes wrong. Also a doc shorter than one block, and
    # one whose total length is an exact multiple of it.
    ("a" * 256 + "b" * 256, "a" * 256 + "X" + "b" * 255),
    ("a" * 255 + "b" * 257, "a" * 255 + "X" + "b" * 256),
    ("a" * 257 + "b" * 255, "a" * 257 + "X" + "b" * 254),
    ("a" * 512, "a" * 512 + "z"),
    ("a" * 512 + "z", "a" * 512),
    ("a" * 10, "a" * 10 + "z"),
    ("héllo" + "x" * 300, "héllo" + "x" * 150 + "Z" + "x" * 150),
])
def test_text_diff_op_matches_the_character_space_oracle(before, after):
    assert op_history.text_diff_op(before, after) == _char_space_diff(before, after)


@pytest.mark.parametrize("before,after", [
    ("héllo wörld", "héllo wärld"),
    ("日本語のテキスト", "日本語のテキスト!"),
    ("日本語", "日語"),
    ("x" * 200, "x" * 200 + "y"),
])
def test_text_diff_op_slices_land_on_character_boundaries(before, after):
    """The byte-space scan must never split a codepoint: applying the op's own
    (pos, deleted, inserted) to `before` in CHARACTER space has to rebuild
    `after` exactly -- that is precisely what TextEditCodec.apply does."""
    op = op_history.text_diff_op(before, after)
    if op is None:
        assert before == after
        return
    _, pos, deleted, inserted = op
    assert before[pos:pos + len(deleted)] == deleted
    assert before[:pos] + inserted + before[pos + len(deleted):] == after


def test_text_diff_op_never_indexes_the_str_per_character():
    """The #183 REGRESSION GUARD, and it has to be behavioural: on CPython the
    quadratic version is fast, so no timing test can catch a reintroduction --
    only the device pays, 36 seconds of frozen console per autosave.

    So count the actual `str.__getitem__` calls. A byte-space scan makes ZERO
    (it indexes the encoded bytes); any per-character loop makes one per
    character scanned."""
    hits = []

    class _CountingStr(str):
        def __getitem__(self, k):
            hits.append(k)
            return str.__getitem__(self, k)

    body = "def _draw():\n" + "    cls(1)\n" * 500          # ~9KB, edit at the end
    before = _CountingStr(body)
    after = _CountingStr(body + "x = 1\n")

    op = op_history.text_diff_op(before, after)

    assert op == ("edit", len(body), "", "x = 1\n")
    assert not hits, "text_diff_op indexed the str %d times (the #183 O(n^2))" % len(hits)
