"""Stage 7 (#7, docs/history/shell_ux_technical_plan_v1.md Section 3): the durable, per-project,
reboot-surviving undo/redo journal in runtime/moy_carts.py.

Exercises the storage logic directly (no console): the append->undo->redo->new-commit-
truncates-tail walk, torn-tail recovery, the cap/compaction (oldest dropped + their
snapshots deleted), reboot survival (reload purely from the SD files), and the
discipline proof that the per-commit line append is a RAW open("a") -- O(1), NOT the
whole-file-rewriting _write_atomic.
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _cart(tmp_path):
    """A minimal .moy folder with a live main.py to journal over."""
    from runtime import moy_carts
    root = str(tmp_path / "carts")
    moy_carts.ensure_dirs(root)
    c = moy_carts.create("Journal Me", root, src="v0\n", type="app")
    return moy_carts, c["path"]


def _journal_mod():
    """The journal implementation module (moy_journal since the moy_carts split):
    caps + private helpers are patched/inspected THERE; moy_carts re-exports the
    public verbs so `mc.journal_*` keeps working."""
    from runtime import moy_journal
    return moy_journal


def _live(path, file="main.py"):
    return (Path(path) / file).read_text()


def _cursor(mc, path):
    _jdir, _log, cur, _snap = mc._journal_paths(path)
    return json.loads(Path(cur).read_text())["seq"]


# -- (a) the walk: append -> undo -> redo -> new commit truncates the redo tail ----

def test_journal_walk_append_undo_redo_truncate(tmp_path):
    mc, path = _cart(tmp_path)

    # three durable commits of main.py
    mc.journal_append(path, "main.py", "v1\n"); (Path(path) / "main.py").write_text("v1\n")
    mc.journal_append(path, "main.py", "v2\n"); (Path(path) / "main.py").write_text("v2\n")
    mc.journal_append(path, "main.py", "v3\n"); (Path(path) / "main.py").write_text("v3\n")
    assert _live(path) == "v3\n"

    # undo twice: v3 -> v2 -> v1 (the live file is rewritten each step)
    assert mc.journal_undo(path) == "main.py"
    assert _live(path) == "v2\n"
    assert mc.journal_undo(path) == "main.py"
    assert _live(path) == "v1\n"

    # undo past the floor (the first snapshot) is a no-op
    assert mc.journal_undo(path) is None
    assert _live(path) == "v1\n"

    # redo steps forward again
    assert mc.journal_redo(path) == "main.py"
    assert _live(path) == "v2\n"

    # a NEW commit while rewound truncates the redo tail (Google-Docs rule): v3 is gone
    mc.journal_append(path, "main.py", "v4\n"); (Path(path) / "main.py").write_text("v4\n")
    assert _live(path) == "v4\n"
    assert mc.journal_redo(path) is None                # nothing ahead of v4 anymore

    # and undo now walks v4 -> v2 -> v1 (v3 was dropped, its snapshot deleted)
    assert mc.journal_undo(path) == "main.py" and _live(path) == "v2\n"
    _jdir, _log, _cur, snap_dir = mc._journal_paths(path)
    snaps = sorted(p.name for p in Path(snap_dir).iterdir())
    assert not any("v3" in _read_snap(snap_dir, n) for n in snaps)


def _read_snap(snap_dir, name):
    return (Path(snap_dir) / name).read_text()


def test_journal_walk_is_multi_file(tmp_path):
    # ONE journal spans all of a cart's files: undo steps the GLOBAL timeline in reverse
    # chronological order and restores the file of the commit it's undoing to that file's
    # PREVIOUS snapshot -- leaving the other files untouched. Each file's floor is its own
    # first snapshot (finer undo stays in the editor's RAM).
    mc, path = _cart(tmp_path)
    (Path(path) / "sprites.moygfx").write_text("SP0\n")

    mc.journal_append(path, "main.py", "A\n"); (Path(path) / "main.py").write_text("A\n")
    mc.journal_append(path, "sprites.moygfx", "SP1\n"); (Path(path) / "sprites.moygfx").write_text("SP1\n")
    mc.journal_append(path, "main.py", "B\n"); (Path(path) / "main.py").write_text("B\n")
    mc.journal_append(path, "sprites.moygfx", "SP2\n"); (Path(path) / "sprites.moygfx").write_text("SP2\n")

    assert mc.journal_undo(path) == "sprites.moygfx"     # newest commit: SP2 -> SP1
    assert _live(path, "sprites.moygfx") == "SP1\n"
    assert _live(path, "main.py") == "B\n"               # main untouched

    assert mc.journal_undo(path) == "main.py"            # next back: B -> A
    assert _live(path, "main.py") == "A\n"
    assert _live(path, "sprites.moygfx") == "SP1\n"      # sprites untouched

    assert mc.journal_undo(path) is None                 # sprites' first commit = its floor


def test_journal_dedup_writes_nothing_when_unchanged(tmp_path):
    # A commit identical to the current state is a no-op (the snapshot-ceiling's core:
    # "a debounce that fires with nothing changed writes nothing").
    mc, path = _cart(tmp_path)
    assert mc.journal_append(path, "main.py", "same\n") == 1
    assert mc.journal_append(path, "main.py", "same\n") is None    # no new seq
    _jdir, _log, _cur, snap_dir = mc._journal_paths(path)
    assert len(list(Path(snap_dir).iterdir())) == 1               # only ONE snapshot


# -- (b) torn-tail recovery: a corrupt last jsonl line is dropped at load ----------

def test_journal_torn_tail_line_is_dropped(tmp_path):
    mc, path = _cart(tmp_path)
    mc.journal_append(path, "main.py", "one\n")
    mc.journal_append(path, "main.py", "two\n")
    _jdir, log_path, _cur, _snap = mc._journal_paths(path)

    # Simulate a power loss mid-append: a truncated final JSON line with no newline.
    with open(log_path, "a") as f:
        f.write('{"seq": 3, "ts": 0, "file": "main.py", "sn')   # torn -- no closing brace

    entries = mc._journal_load_entries(log_path)
    assert [e["seq"] for e in entries] == [1, 2]        # the torn line was dropped
    # And undo still walks the two intact commits.
    (Path(path) / "main.py").write_text("two\n")
    assert mc.journal_undo(path) == "main.py"
    assert _live(path) == "one\n"


# -- (b2) F1: a torn/truncated snapshot is REFUSED, never copied over good work ------

def test_journal_undo_refuses_torn_snapshot(tmp_path):
    # A device power loss can leave a snapshot 0-byte / truncated (non-atomic writes,
    # no fsync). undo must validate it against the recorded `len` and REFUSE rather than
    # overwrite the live file with garbage -- "one bad snapshot loses one step, never the
    # whole history."
    mc, path = _cart(tmp_path)
    mc.journal_append(path, "main.py", "good-one\n"); (Path(path) / "main.py").write_text("good-one\n")
    mc.journal_append(path, "main.py", "good-two\n"); (Path(path) / "main.py").write_text("good-two\n")

    # Corrupt the snapshot undo WOULD restore (seq 1 = good-one): truncate it to garbage.
    _jdir, log_path, _cur, snap_dir = mc._journal_paths(path)
    seq1 = mc._journal_load_entries(log_path)[0]
    (Path(snap_dir) / Path(seq1["snap"]).name).write_text("go")   # len 2 != recorded 9

    assert mc.journal_undo(path) is None                # refused -- snapshot damaged
    assert _live(path) == "good-two\n"                  # the good live file is UNTOUCHED


def test_journal_undo_refuses_empty_snapshot(tmp_path):
    mc, path = _cart(tmp_path)
    mc.journal_append(path, "main.py", "keep-me\n"); (Path(path) / "main.py").write_text("keep-me\n")
    mc.journal_append(path, "main.py", "current\n"); (Path(path) / "main.py").write_text("current\n")
    _jdir, log_path, _cur, snap_dir = mc._journal_paths(path)
    seq1 = mc._journal_load_entries(log_path)[0]
    (Path(snap_dir) / Path(seq1["snap"]).name).write_text("")     # 0-byte snapshot

    assert mc.journal_undo(path) is None
    assert _live(path) == "current\n"                   # not blanked out


def test_journal_redo_refuses_torn_snapshot(tmp_path):
    mc, path = _cart(tmp_path)
    mc.journal_append(path, "main.py", "r1\n"); (Path(path) / "main.py").write_text("r1\n")
    mc.journal_append(path, "main.py", "r2\n"); (Path(path) / "main.py").write_text("r2\n")
    assert mc.journal_undo(path) == "main.py" and _live(path) == "r1\n"

    # Corrupt the redo target (seq 2 = r2) and confirm redo won't overwrite r1 with junk.
    _jdir, log_path, _cur, snap_dir = mc._journal_paths(path)
    seq2 = mc._journal_load_entries(log_path)[1]
    (Path(snap_dir) / Path(seq2["snap"]).name).write_text("XYZLONGER")   # len != recorded
    assert mc.journal_redo(path) is None
    assert _live(path) == "r1\n"                          # untouched


def test_journal_undo_restores_a_legit_empty_snapshot(tmp_path):
    # An entry that snapshotted a genuinely EMPTY file (len == 0) still restores -- the
    # length check must not reject a valid empty state.
    mc, path = _cart(tmp_path)
    mc.journal_append(path, "notes.txt", ""); (Path(path) / "notes.txt").write_text("")
    mc.journal_append(path, "notes.txt", "hello\n"); (Path(path) / "notes.txt").write_text("hello\n")
    assert mc.journal_undo(path) == "notes.txt"
    assert _live(path, "notes.txt") == ""                # restored the empty state cleanly


# -- (c) the cap / compaction: oldest entries + their snapshots are dropped ---------

def test_journal_compaction_drops_oldest(tmp_path, monkeypatch):
    mc, path = _cart(tmp_path)
    monkeypatch.setattr(_journal_mod(), "JOURNAL_MAX_ENTRIES", 3)   # tiny cap so the test is quick

    for i in range(1, 7):                               # 6 commits, cap 3
        mc.journal_append(path, "main.py", "V%d\n" % i)
        (Path(path) / "main.py").write_text("V%d\n" % i)

    entries = mc._journal_load_entries(mc._journal_paths(path)[1])
    seqs = [e["seq"] for e in entries]
    assert len(seqs) <= 3                               # rotated down to the cap
    assert seqs[-1] == 6                                # the newest commit survives
    assert 1 not in seqs and 2 not in seqs             # the oldest were dropped

    # the dropped entries' snapshots are gone from disk (not just unlinked from the log)
    _jdir, _log, _cur, snap_dir = mc._journal_paths(path)
    on_disk = sorted(p.name for p in Path(snap_dir).iterdir())
    assert not any(n.startswith("0001-") or n.startswith("0002-") for n in on_disk)
    assert any(n.startswith("0006-") for n in on_disk)

    # the current state still undoes cleanly within the surviving window
    assert mc.journal_undo(path) == "main.py"
    assert _live(path) == "V5\n"


def test_journal_bytes_cap_triggers_compaction(tmp_path, monkeypatch):
    # The 512KB cap fires independently of the 64-entry cap (whichever first).
    mc, path = _cart(tmp_path)
    monkeypatch.setattr(_journal_mod(), "JOURNAL_MAX_ENTRIES", 1000)  # count cap out of the way
    monkeypatch.setattr(_journal_mod(), "JOURNAL_MAX_BYTES", 300)     # ~3 x 100-byte snapshots

    for i in range(6):
        mc.journal_append(path, "main.py", ("%03d" % i) * 33 + "\n")   # 100 bytes each
        (Path(path) / "main.py").write_text(("%03d" % i) * 33 + "\n")

    _jdir, log_path, _cur, snap_dir = mc._journal_paths(path)
    total = mc._journal_total_bytes(_jdir, mc._journal_load_entries(log_path))
    assert total <= 300                                   # held under the byte cap
    assert len(mc._journal_load_entries(log_path)) < 6    # oldest rolled off


def test_journal_compact_keeps_current_state(tmp_path, monkeypatch):
    # Even under a brutal cap, compaction never drops the CURRENT state's snapshot --
    # undo/current always resolves.
    mc, path = _cart(tmp_path)
    monkeypatch.setattr(_journal_mod(), "JOURNAL_MAX_ENTRIES", 1)
    for i in range(4):
        mc.journal_append(path, "main.py", "S%d\n" % i)
        (Path(path) / "main.py").write_text("S%d\n" % i)
    # The live file matches the last commit, and its snapshot is still readable.
    entries = mc._journal_load_entries(mc._journal_paths(path)[1])
    assert entries                                        # at least the current survives
    cur = mc._journal_current_snap(entries, _cursor(mc, path), "main.py")
    _jdir = mc._journal_paths(path)[0]
    assert (Path(_jdir) / cur).read_text() == "S3\n"


# -- (d) reboot survival: a fresh load sees only the SD files -----------------------

def test_journal_survives_reboot(tmp_path):
    import importlib
    mc, path = _cart(tmp_path)
    mc.journal_append(path, "main.py", "boot1\n"); (Path(path) / "main.py").write_text("boot1\n")
    mc.journal_append(path, "main.py", "boot2\n"); (Path(path) / "main.py").write_text("boot2\n")
    assert _cursor(mc, path) == 2

    # "Reboot": re-import the module fresh (no in-RAM state carries over) and keep going
    # purely off the on-SD journal/ files.
    import runtime.moy_carts as reloaded
    reloaded = importlib.reload(reloaded)
    assert reloaded.journal_undo(path) == "main.py"
    assert _live(path) == "boot1\n"
    assert reloaded.journal_redo(path) == "main.py"
    assert _live(path) == "boot2\n"


# -- (e) discipline proof: the per-commit line append is O(1), NOT _write_atomic ----

def test_journal_append_is_raw_open_not_write_atomic(tmp_path, monkeypatch):
    # The BINDING v1.1 rule: journal.jsonl grows via a raw open(path,"a") -- one line
    # appended, O(1) -- and must NEVER go through _write_atomic (which rewrites the
    # whole file, making each commit O(n)). Prove it two ways.
    mc, path = _cart(tmp_path)
    _jdir, log_path, _cur, _snap = mc._journal_paths(path)

    # (1) _write_atomic is NEVER called with the journal log as its target on the
    #     per-commit append path (cursor.json IS atomic -- that's allowed). The spy
    #     lands on moy_journal (the implementation module since the split).
    mj = _journal_mod()
    real_atomic = mj._write_atomic
    atomic_targets = []

    def _spy_atomic(p, data):
        atomic_targets.append(p)
        return real_atomic(p, data)
    monkeypatch.setattr(mj, "_write_atomic", _spy_atomic)

    mc.journal_append(path, "main.py", "a\n")
    mc.journal_append(path, "main.py", "b\n")
    assert log_path not in atomic_targets                 # the LOG never rewritten atomically
    assert any(t.endswith("cursor.json") for t in atomic_targets)   # cursor IS atomic

    # (2) O(1): appending a line only ADDS bytes -- the existing prefix is byte-identical
    #     (a whole-file rewrite could reorder/rewrite it; an append cannot).
    before = Path(log_path).read_bytes()
    mc.journal_append(path, "main.py", "c\n")
    after = Path(log_path).read_bytes()
    assert after.startswith(before)                       # pure append: prefix preserved
    assert len(after) > len(before)


def test_journal_no_dir_until_first_append(tmp_path):
    # A cart that is only opened/played (never committed) grows no journal/ folder --
    # the journal is lazy, so a read-only session costs zero SD.
    mc, path = _cart(tmp_path)
    assert not (Path(path) / "journal").exists()
    mc.journal_append(path, "main.py", "x\n")
    assert (Path(path) / "journal" / "journal.jsonl").exists()


def test_noop_append_does_not_mkdir(tmp_path, monkeypatch):
    # F3: a no-op append (content-dedup skip) must touch NOTHING -- the mkdir happens
    # only once we're committing to a write, so an idle tick that changes nothing leaves
    # no empty folders (and does no filesystem work at all beyond the dedup read).
    mc, path = _cart(tmp_path)
    mc.journal_append(path, "main.py", "same\n")   # first real append creates the dirs
    calls = []
    real_mkdir = mc._mkdir
    monkeypatch.setattr(mc, "_mkdir", lambda p: (calls.append(p), real_mkdir(p))[1])
    assert mc.journal_append(path, "main.py", "same\n") is None   # no-op (deduped)
    assert calls == []                              # no mkdir on a no-op append


# -- (f) #111 PER-FILE cursors: a scoped undo/redo walks only the tab's file(s) --------

def _two_file_cart(tmp_path):
    """A cart with TWO journaled files, each committed twice (main.py: A,B; the map:
    M1,M2), the common shape behind the per-tab scoping tests below."""
    mc, path = _cart(tmp_path)
    mc.journal_append(path, "main.py", "A\n"); (Path(path) / "main.py").write_text("A\n")
    mc.journal_append(path, "main.py", "B\n"); (Path(path) / "main.py").write_text("B\n")
    mc.journal_append(path, "map.moymap", "M1\n"); (Path(path) / "map.moymap").write_text("M1\n")
    mc.journal_append(path, "map.moymap", "M2\n"); (Path(path) / "map.moymap").write_text("M2\n")
    return mc, path


def test_scoped_undo_on_one_file_never_touches_the_other(tmp_path):
    # The core #111 fix: an undo scoped to main.py reverts ONLY main.py, leaving the
    # map's newest commit intact -- even though the map committed LAST (higher seq).
    mc, path = _two_file_cart(tmp_path)
    assert mc.journal_undo(path, ("main.py",)) == "main.py"
    assert _live(path, "main.py") == "A\n"           # code stepped back B -> A
    assert _live(path, "map.moymap") == "M2\n"       # the map is UNTOUCHED

    # A second scoped undo hits main.py's floor (its first snapshot) -> None, and the
    # map is STILL untouched (a floor on one file never spills onto another).
    assert mc.journal_undo(path, ("main.py",)) is None
    assert _live(path, "map.moymap") == "M2\n"


def test_scoped_redo_dim_is_per_file(tmp_path):
    # The exact reported symptom, at the storage layer: undo on one file lights ITS redo
    # while the OTHER file's redo stays dim (nothing was undone there).
    mc, path = _two_file_cart(tmp_path)
    assert mc.journal_can_redo(path, ("main.py",)) is False    # nothing undone yet
    assert mc.journal_can_redo(path, ("map.moymap",)) is False

    mc.journal_undo(path, ("main.py",))              # rewind ONLY main.py
    assert mc.journal_can_redo(path, ("main.py",)) is True      # its redo armed
    assert mc.journal_can_redo(path, ("map.moymap",)) is False  # the map stays DIM
    # ...and the whole-project (files=None) view sees the redo (compat).
    assert mc.journal_can_redo(path) is True


def test_per_file_truncation_preserves_the_other_files_redo_tail(tmp_path):
    # A commit of file F drops only F's redo tail; another file rewound in the same
    # session keeps its redo tail (they don't share one global cursor anymore).
    mc, path = _two_file_cart(tmp_path)
    mc.journal_undo(path, ("map.moymap",))           # map: M2 -> M1 (map redo = M2 armed)
    mc.journal_undo(path, ("main.py",))              # main: B -> A (main redo = B armed)

    # A NEW main.py commit truncates main's redo tail (B) -- but NOT the map's (M2).
    mc.journal_append(path, "main.py", "C\n"); (Path(path) / "main.py").write_text("C\n")
    assert mc.journal_can_redo(path, ("main.py",)) is False     # main's B was truncated
    assert mc.journal_can_redo(path, ("map.moymap",)) is True   # the map's M2 survived
    assert mc.journal_redo(path, ("map.moymap",)) == "map.moymap"
    assert _live(path, "map.moymap") == "M2\n"


def test_old_single_seq_cursor_migrates_per_file(tmp_path):
    # TOLERANT MIGRATION: a pre-#111 cursor.json ({"seq": N}) loads as "each file's
    # cursor = its newest entry seq <= N". Interleaved commits main(1),map(2),main(3),
    # map(4) with an old seq=2 -> main's cursor = 1 (seq3 is redo tail), map's = 2 (seq4
    # is redo tail): each file rewound to its own newest-applied-<=-2.
    mc, path = _cart(tmp_path)
    mc.journal_append(path, "main.py", "A\n")            # seq 1
    mc.journal_append(path, "map.moymap", "M1\n")        # seq 2
    mc.journal_append(path, "main.py", "B\n")            # seq 3
    mc.journal_append(path, "map.moymap", "M2\n")        # seq 4
    _jdir, _log, cur, _snap = mc._journal_paths(path)
    Path(cur).write_text(json.dumps({"seq": 2, "bytes": 999}))   # legacy single-seq shape

    mj = _journal_mod()
    entries = mj._journal_load_entries(_log)
    cursors = mj._journal_cursors(cur, entries)
    assert cursors == {"main.py": 1, "map.moymap": 2}    # each file's newest seq <= 2

    # And the walk honors it: both files have a commit AHEAD of their migrated cursor.
    assert mc.journal_can_redo(path, ("main.py",)) is True       # seq3 (B) ahead
    assert mc.journal_can_redo(path, ("map.moymap",)) is True    # seq4 (M2) ahead
    assert mc.journal_can_undo(path, ("main.py",)) is False      # main is at its floor (seq1)
    assert mc.journal_redo(path, ("main.py",)) == "main.py"      # steps main 1 -> 3

    # A migrating write persists the NEW cursor-map format (no more bare `seq` walk).
    saved = json.loads(Path(cur).read_text())
    assert "cursors" in saved and saved["cursors"]["main.py"] == 3


def test_missing_and_torn_cursor_default_to_newest(tmp_path):
    # A missing/torn cursor.json defaults every file to its newest entry (the safe
    # 'everything applied' state) -- so undo works and redo is dim.
    mc, path = _two_file_cart(tmp_path)
    _jdir, _log, cur, _snap = mc._journal_paths(path)
    Path(cur).write_text("{ this is torn json")       # unparseable cursor

    mj = _journal_mod()
    entries = mj._journal_load_entries(_log)
    assert mj._journal_cursors(cur, entries) == {"main.py": 2, "map.moymap": 4}
    assert mc.journal_can_redo(path, ("main.py",)) is False   # already at the top
    assert mc.journal_can_undo(path, ("main.py",)) is True
