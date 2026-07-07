"""Stage 7 (#7, docs/shell_ux_technical_plan_v1.md Section 3): the durable, per-project,
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


# -- (c) the cap / compaction: oldest entries + their snapshots are dropped ---------

def test_journal_compaction_drops_oldest(tmp_path, monkeypatch):
    mc, path = _cart(tmp_path)
    monkeypatch.setattr(mc, "JOURNAL_MAX_ENTRIES", 3)   # tiny cap so the test is quick

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
    monkeypatch.setattr(mc, "JOURNAL_MAX_ENTRIES", 1000)  # count cap out of the way
    monkeypatch.setattr(mc, "JOURNAL_MAX_BYTES", 300)     # ~3 x 100-byte snapshots

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
    monkeypatch.setattr(mc, "JOURNAL_MAX_ENTRIES", 1)
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
    #     per-commit append path (cursor.json IS atomic -- that's allowed).
    real_atomic = mc._write_atomic
    atomic_targets = []

    def _spy_atomic(p, data):
        atomic_targets.append(p)
        return real_atomic(p, data)
    monkeypatch.setattr(mc, "_write_atomic", _spy_atomic)

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
