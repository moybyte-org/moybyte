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
    """A carts root with its OWN parent directory, because the #108 files root
    is a SIBLING of the carts dir: two carts roots side by side under tmp_path
    would share one files/, and the two sides of a sync must not be the same
    folder."""
    root = tmp_path / name / "carts"
    root.mkdir(parents=True)
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


def _files_visible(carts_root):
    """{rel: text} for everything the FILES protocol owns: known kind dirs
    only, skip-filtered -- the set both stores must agree on. Anything outside
    a kind (.history/, trash/) is invisible here by the same rule that keeps it
    off the wire."""
    root = moy_carts.files_root(carts_root)
    out = {}
    try:
        entries = sorted(moy_sync._entries(root))
    except OSError:
        return out                            # nothing made yet: no files root
    for kind, isdir in entries:
        if isdir and kind in moy_carts.FILE_KINDS:
            _walk(root, kind, out)
    return out


def _files_wire(a, b):
    """A Wire over A's FILES root into B's -- the second watched root, same
    protocol, its own batches."""
    return Wire(StoreWatcher(moy_carts.files_root(a),
                             root_id=moy_sync.FILES_ROOT_ID),
                moy_carts.files_root(b))


def _record(carts_root, name, clips):
    """A folder-valued item (#70 recordings): one DIRECTORY per recording, its
    parts written in place by the recorder rather than through save_file."""
    d = Path(moy_carts.files_root(carts_root)) / "recordings" / name
    d.mkdir(parents=True)
    for clip, text in clips.items():
        (d / clip).write_text(text)
    return d


class Wire:
    """The fake transport: batches from A's watcher applied into B's root.
    `down=True` models the dropped link -- the batch goes unanswered and the
    watcher requeues, exactly what the worker's failed fetch does.

    The batch's own `root` decides where it lands, exactly as moy_webhost's
    endpoint does -- the harness never tells the receiver which store it is
    holding."""

    def __init__(self, watcher, dst_root):
        self.w = watcher
        self.dst = dst_root
        self.down = False
        self.batches = 0
        self.sent = []               # every op that crossed, in order

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
            ops, _pin, root_id = moy_sync.parse_batch(body)
            assert ops is not None, "the wire shape must parse"
            assert root_id == self.w.root_id, "a batch must name its own root"
            applied, errors, _ = moy_sync.apply_ops(self.dst, ops, root_id)
            assert not errors, errors
            self.w.ack(True)
            self.sent.extend(ops)
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


# ---------------------------------------------------------------------------
# The #108 user files as a SECOND watched root (2026-08-25). Same watcher, same
# apply, its own batches -- so what is proved here is that the one protocol
# carries the kid's drawings as well as her carts, and that the two pieces of
# the files layer that must NOT travel do not.
# ---------------------------------------------------------------------------


def test_user_files_converge_including_a_folder_item(tmp_path):
    a = _mkstore(tmp_path, "browser")
    b = _mkstore(tmp_path, "board")
    wire = _files_wire(a, b)
    moy_carts.save_file("drawings", "sunset", "0,1,2,3,", root=a)
    moy_carts.save_file("docs", "story", "once upon a time\n", root=a)
    _record(a, "take_1", {"part0.json": "[1]", "part1.json": "[2]"})
    wire.pump()
    va, vb = _files_visible(a), _files_visible(b)
    assert va == vb != {}
    assert vb["drawings/sunset.moyimg"] == "0,1,2,3,"
    assert vb["recordings/take_1/part1.json"] == "[2]"


def test_a_folder_item_dies_as_one_dc_and_a_kind_dir_never_does(tmp_path):
    a = _mkstore(tmp_path, "browser")
    b = _mkstore(tmp_path, "board")
    wire = _files_wire(a, b)
    _record(a, "take_1", {"part0.json": "[1]", "part1.json": "[2]"})
    moy_carts.save_file("drawings", "sunset", "0,", root=a)
    wire.pump()
    assert _files_visible(a) == _files_visible(b) != {}
    # The whole recording folder goes: ONE dc, not a spray of file deletes.
    import shutil
    mark = len(wire.sent)
    shutil.rmtree(Path(moy_carts.files_root(a)) / "recordings" / "take_1")
    wire.pump()
    assert wire.sent[mark:] == [{"p": "recordings/take_1", "dc": 1}]
    assert _files_visible(a) == _files_visible(b)
    assert not (Path(moy_carts.files_root(b)) / "recordings" / "take_1").exists()
    # ...and emptying a KIND dir travels as plain file deletes: a kind dir is
    # never a dc unit, or one lost item would wipe the peer's whole drawings.
    mark = len(wire.sent)
    moy_carts.delete_file("drawings", "sunset", root=a)
    wire.pump()
    assert wire.sent[mark:] == [{"p": "drawings/sunset.moyimg", "d": 1}]
    assert _files_visible(a) == _files_visible(b)
    assert (Path(moy_carts.files_root(b)) / "drawings").is_dir()


def test_the_history_sidecars_and_the_trash_stay_home(tmp_path):
    """`.history/` is the files layer's journal-equivalent and `trash/` is a
    LOCAL recovery bin -- shipping a deletion the kid can still undo here but
    not there is how last-writer-wins turns into data loss."""
    a = _mkstore(tmp_path, "browser")
    b = _mkstore(tmp_path, "board")
    wire = _files_wire(a, b)
    moy_carts.save_file("drawings", "sunset", "0,", root=a)
    moy_carts.history_commit("drawings", "sunset", [{"op": "px"}],
                             keyframe="0,", root=a)
    moy_carts.save_file("drawings", "doomed", "9,", root=a)
    moy_carts.delete_file("drawings", "doomed", root=a)   # -> files/trash/
    wire.pump()
    fa, fb = Path(moy_carts.files_root(a)), Path(moy_carts.files_root(b))
    assert (fa / ".history").is_dir() and (fa / "trash").is_dir(), \
        "the local store really does hold both"
    assert not (fb / ".history").exists(), "undo history is each side's own"
    assert not (fb / "trash").exists(), "a recovery bin is local, always"
    assert _files_visible(a) == _files_visible(b)
    assert list(_files_visible(b)) == ["drawings/sunset.moyimg"]


def test_a_two_sided_collision_on_a_drawing_is_lww(tmp_path):
    a = _mkstore(tmp_path, "browser")
    b = _mkstore(tmp_path, "board")
    wire = _files_wire(a, b)
    moy_carts.save_file("drawings", "sunset", "0,", root=a)
    wire.pump()
    # The kid paints on the glass...
    moy_carts.save_file("drawings", "sunset", "BOARD,", root=b)
    # ...and in the browser, which pushes: the push is the last writer.
    moy_carts.save_file("drawings", "sunset", "BROWSER,", root=a)
    wire.pump()
    assert _files_visible(a) == _files_visible(b)
    assert _files_visible(b)["drawings/sunset.moyimg"] == "BROWSER,"


def test_files_drop_and_reattach_loses_nothing(tmp_path):
    a = _mkstore(tmp_path, "browser")
    b = _mkstore(tmp_path, "board")
    wire = _files_wire(a, b)
    moy_carts.save_file("drawings", "sunset", "0,", root=a)
    wire.pump()
    wire.down = True
    moy_carts.save_file("drawings", "sunset", "1,", root=a)
    wire.pump()                              # swallowed: the link is down
    moy_carts.save_file("docs", "story", "hello\n", root=a)
    wire.pump()                              # still down; edits keep piling
    assert _files_visible(a) != _files_visible(b)
    wire.down = False
    wire.pump()
    assert _files_visible(a) == _files_visible(b)
    assert _files_visible(b)["drawings/sunset.moyimg"] == "1,", \
        "reattach ships the FRESHEST bytes, not a replay of the backlog"


def test_the_two_roots_ship_separate_batches(tmp_path):
    """A batch never mixes roots -- each watcher stamps its own, and the
    receiver routes on that alone."""
    a = _mkstore(tmp_path, "browser")
    b = _mkstore(tmp_path, "board")
    carts_wire, files_wire = Wire(StoreWatcher(a), b), _files_wire(a, b)
    _seed(a)
    moy_carts.save_file("drawings", "sunset", "0,", root=a)
    carts_wire.pump()
    files_wire.pump()
    assert _visible(a) == _visible(b) != {}
    assert _files_visible(a) == _files_visible(b) != {}
    # The carts side never saw a drawing and the files side never saw a cart.
    assert not any(k.startswith("drawings/") for k in _visible(b))
    assert not any(k.endswith(".moy") for k in _files_visible(b))


def test_reapplying_a_batch_is_idempotent(tmp_path):
    """The client clears an ANSWERED batch; the failure mode left is an
    answer lost on the wire, after which the same batch is sent again."""
    a = _mkstore(tmp_path, "browser")
    b = _mkstore(tmp_path, "board")
    w = StoreWatcher(a)
    cart = _seed(a)
    w.sweep()
    body = w.take_json()
    ops, _pin, _root = moy_sync.parse_batch(body)
    moy_sync.apply_ops(b, ops)
    once = _visible(b)
    applied, errors, _ = moy_sync.apply_ops(b, ops)
    assert not errors
    assert _visible(b) == once == _visible(a)
    w.ack(True)
    assert cart is not None
