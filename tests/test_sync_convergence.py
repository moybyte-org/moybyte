"""The 3.4 sync pin: two REAL store instances over a fake transport.

moycore plan 3.4 names this harness as the RPC's landing condition: scripted
edit/commit sequences -- drop-and-reattach and a deliberate two-sided
collision included -- with both stores asserted CONVERGED and both journals
still replayable afterwards. "Real" is load-bearing: the browser side edits
through moy_carts' own verbs and commits through moy_journal, the board side
applies through the same moy_sync.apply_ops the WebHost endpoint calls, so
what converges here is the shipped machinery, not a model of it.

The transport is the wire shape itself: take_json -> parse_batch -> apply_ops,
with a drop being an unanswered batch (ack False). What this deliberately does
NOT model is the socket -- moy_webserver has its own suite, and the on-glass
suites drive the real one.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from runtime import moy_carts, moy_journal, moy_sync            # noqa: E402
from runtime.moy_sync import StoreWatcher, BATCH_BUDGET          # noqa: E402


def _mkstore(tmp_path, name):
    root = tmp_path / name
    root.mkdir()
    return str(root)


def _seed(root, title="Hop Quest"):
    cart = moy_carts.create(title, root=root)
    return cart


def _visible(root):
    """{rel: text} for everything the sync protocol owns: cart dirs only,
    skip-filtered -- the set both stores must agree on."""
    out = {}
    for cart, isdir in sorted(moy_sync._entries(root)):
        if not isdir or moy_sync._skip(cart):
            continue
        _walk(root, cart, out)
    return out


def _walk(root, rel, out):
    for name, isdir in sorted(moy_sync._entries(root + "/" + rel)):
        if moy_sync._skip(name):
            continue
        sub = rel + "/" + name
        if isdir:
            _walk(root, sub, out)
            continue
        text = moy_sync._read_text(root + "/" + sub)
        if text is not None:
            out[sub] = text
    return out


class Wire:
    """The fake transport: batches from A's watcher applied into B's root.
    `down=True` models the dropped link -- the batch goes unanswered and the
    watcher requeues, exactly what the worker's failed fetch does."""

    def __init__(self, watcher, dst_root):
        self.w = watcher
        self.dst = dst_root
        self.down = False
        self.batches = 0

    def pump(self, sweeps=1):
        """Sweep + ship until quiet (or until the down link swallows one)."""
        for _ in range(sweeps):
            self.w.sweep()
        while True:
            body = self.w.take_json()
            if not body:
                return
            if self.down:
                self.w.ack(False)
                return
            ops, _pin = moy_sync.parse_batch(body)
            assert ops is not None, "the wire shape must parse"
            applied, errors, _ = moy_sync.apply_ops(self.dst, ops)
            assert not errors, errors
            self.w.ack(True)
            self.batches += 1


def _commit_code(root, cart, src):
    """A real #111 commit: the file lands atomically AND the journal appends
    (what Project.commit_code does), so the store this harness syncs carries
    the exact sidecar shape a real one does."""
    status, msg = moy_carts.save_code(cart, src)
    assert status == moy_carts.SAVE_OK, msg
    moy_journal.journal_append(cart["path"], cart.get("main", "main.py"), src)


def test_edit_commit_sync_converges(tmp_path):
    a = _mkstore(tmp_path, "browser")
    b = _mkstore(tmp_path, "board")
    wire = Wire(StoreWatcher(a), b)
    cart = _seed(a)
    wire.pump()
    assert _visible(a) == _visible(b)
    _commit_code(a, cart, "def _draw():\n    cls(3)\n")
    wire.pump()
    va, vb = _visible(a), _visible(b)
    assert va == vb
    assert any(v.endswith("cls(3)\n") for v in vb.values())
    # The journal stayed HOME: A committed, B carries no copy of A's history.
    assert not (Path(b) / Path(cart["path"]).name / "journal").exists()


def test_create_and_delete_travel(tmp_path):
    a = _mkstore(tmp_path, "browser")
    b = _mkstore(tmp_path, "board")
    wire = Wire(StoreWatcher(a), b)
    cart = _seed(a, "Fresh Cart")
    wire.pump()
    assert _visible(a) == _visible(b) != {}
    moy_carts.delete(cart)
    wire.pump()
    assert _visible(a) == _visible(b) == {}
    assert list(Path(b).iterdir()) == [], "dc removes the folder itself"


def test_drop_and_reattach_loses_nothing(tmp_path):
    a = _mkstore(tmp_path, "browser")
    b = _mkstore(tmp_path, "board")
    wire = Wire(StoreWatcher(a), b)
    cart = _seed(a)
    wire.pump()
    wire.down = True
    _commit_code(a, cart, "def _draw():\n    cls(4)\n")
    wire.pump()                              # swallowed: the link is down
    _commit_code(a, cart, "def _draw():\n    cls(5)\n")
    wire.pump()                              # still down; edits keep piling
    assert _visible(a) != _visible(b)
    wire.down = False
    wire.pump()
    assert _visible(a) == _visible(b)
    assert any(v.endswith("cls(5)\n") for v in _visible(b).values()), \
        "reattach ships the FRESHEST bytes, not a replay of the backlog"


def test_two_sided_collision_is_lww_and_both_journals_replay(tmp_path):
    a = _mkstore(tmp_path, "browser")
    b = _mkstore(tmp_path, "board")
    wire = Wire(StoreWatcher(a), b)
    cart_a = _seed(a)
    wire.pump()
    # The board side commits TWICE on its own copy (a kid on the glass)...
    cart_b = moy_carts.scan(b)[0]
    _commit_code(b, cart_b, "def _draw():\n    cls(8)\n")
    _commit_code(b, cart_b, "def _draw():\n    cls(9)\n")
    # ...while the browser commits the same file (twice, so its own journal
    # has somewhere to walk back to) and pushes.
    _commit_code(a, cart_a, "def _draw():\n    cls(1)\n")
    _commit_code(a, cart_a, "def _draw():\n    cls(6)\n")
    wire.pump()
    # LWW: the push is the last writer, both sides now read cls(6).
    assert _visible(a) == _visible(b)
    main_rel = Path(cart_b["path"]).name + "/" + cart_b.get("main", "main.py")
    assert _visible(b)[main_rel].endswith("cls(6)\n")
    # The board's OWN journal survived the overwrite and still replays: undo
    # walks back to its first commit -- the kid's history is not corrupted by
    # a sync landing on top of it.
    assert moy_journal.journal_can_undo(cart_b["path"])
    moy_journal.journal_undo(cart_b["path"])
    text = (Path(cart_b["path"]) / cart_b.get("main", "main.py")).read_text()
    assert text.endswith("cls(8)\n")
    # ...and the browser's journal replays too.
    assert moy_journal.journal_can_undo(cart_a["path"])
    moy_journal.journal_undo(cart_a["path"])


def test_a_giant_file_converges_across_many_batches(tmp_path):
    a = _mkstore(tmp_path, "browser")
    b = _mkstore(tmp_path, "board")
    wire = Wire(StoreWatcher(a), b)
    cart = _seed(a)
    wire.pump()
    big = "-- line %d\n" % 7 * (3 * BATCH_BUDGET // 10)
    (Path(cart["path"]) / "big.lua").write_text(big)
    wire.pump()
    assert wire.batches >= 3, "a giant file must actually span batches"
    assert _visible(a) == _visible(b)


def test_reapplying_a_batch_is_idempotent(tmp_path):
    """The client clears an ANSWERED batch; the failure mode left is an
    answer lost on the wire, after which the same batch is sent again."""
    a = _mkstore(tmp_path, "browser")
    b = _mkstore(tmp_path, "board")
    w = StoreWatcher(a)
    cart = _seed(a)
    w.sweep()
    body = w.take_json()
    ops, _pin = moy_sync.parse_batch(body)
    moy_sync.apply_ops(b, ops)
    once = _visible(b)
    applied, errors, _ = moy_sync.apply_ops(b, ops)
    assert not errors
    assert _visible(b) == once == _visible(a)
    w.ack(True)
    assert cart is not None
