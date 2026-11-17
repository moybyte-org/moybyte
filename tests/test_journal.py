"""The durable, per-project, reboot-surviving undo/redo journal (#7):
native/moy_store/moy_journal.c, through moy_carts' re-exported verbs.

Exercises the storage directly (no console): the append->undo->redo->new-commit-
truncates-tail walk, torn-tail recovery, the cap/compaction (oldest dropped + their
snapshots deleted), reboot survival (reload purely from the SD files), and that the
per-commit line append only adds bytes to the log.
"""

import json
from tests import journal_files as jf
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# native/moy_store/moy_journal.h's budget.
MAX_ENTRIES = 64
MAX_BYTES = 512 * 1024


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
    """The scalar undo position. Read the way the journal reads it, because
    cursor.json exists only while something is REWOUND (#154): at rest there is
    no file, and the position is every file's newest entry."""
    return jf.cursor(path)


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
    _jdir, _log, _cur, snap_dir = jf.paths(path)
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
    _jdir, _log, _cur, snap_dir = jf.paths(path)
    assert len(list(Path(snap_dir).iterdir())) == 1               # only ONE snapshot


# -- (b) torn-tail recovery: a corrupt last jsonl line is dropped at load ----------

def test_journal_torn_tail_line_is_dropped(tmp_path):
    mc, path = _cart(tmp_path)
    mc.journal_append(path, "main.py", "one\n")
    mc.journal_append(path, "main.py", "two\n")
    _jdir, log_path, _cur, _snap = jf.paths(path)

    # Simulate a power loss mid-append: a truncated final JSON line with no newline.
    with open(log_path, "a") as f:
        f.write('{"seq": 3, "ts": 0, "file": "main.py", "sn')   # torn -- no closing brace

    entries = jf.entries(log_path)
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
    _jdir, log_path, _cur, snap_dir = jf.paths(path)
    seq1 = jf.entries(log_path)[0]
    (Path(snap_dir) / Path(seq1["snap"]).name).write_text("go")   # len 2 != recorded 9

    assert mc.journal_undo(path) is None                # refused -- snapshot damaged
    assert _live(path) == "good-two\n"                  # the good live file is UNTOUCHED


def test_journal_undo_refuses_empty_snapshot(tmp_path):
    mc, path = _cart(tmp_path)
    mc.journal_append(path, "main.py", "keep-me\n"); (Path(path) / "main.py").write_text("keep-me\n")
    mc.journal_append(path, "main.py", "current\n"); (Path(path) / "main.py").write_text("current\n")
    _jdir, log_path, _cur, snap_dir = jf.paths(path)
    seq1 = jf.entries(log_path)[0]
    (Path(snap_dir) / Path(seq1["snap"]).name).write_text("")     # 0-byte snapshot

    assert mc.journal_undo(path) is None
    assert _live(path) == "current\n"                   # not blanked out


def test_journal_redo_refuses_torn_snapshot(tmp_path):
    mc, path = _cart(tmp_path)
    mc.journal_append(path, "main.py", "r1\n"); (Path(path) / "main.py").write_text("r1\n")
    mc.journal_append(path, "main.py", "r2\n"); (Path(path) / "main.py").write_text("r2\n")
    assert mc.journal_undo(path) == "main.py" and _live(path) == "r1\n"

    # Corrupt the redo target (seq 2 = r2) and confirm redo won't overwrite r1 with junk.
    _jdir, log_path, _cur, snap_dir = jf.paths(path)
    seq2 = jf.entries(log_path)[1]
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

def test_journal_compaction_drops_oldest(tmp_path):
    mc, path = _cart(tmp_path)
    n = MAX_ENTRIES + 3
    for i in range(1, n + 1):
        mc.journal_append(path, "main.py", "V%d\n" % i)
        (Path(path) / "main.py").write_text("V%d\n" % i)

    entries = jf.entries(jf.paths(path)[1])
    seqs = [e["seq"] for e in entries]
    assert len(seqs) <= MAX_ENTRIES                     # rotated down to the cap
    assert seqs[-1] == n                                # the newest commit survives
    assert 1 not in seqs and 2 not in seqs             # the oldest were dropped

    # the dropped entries' snapshots are gone from disk (not just unlinked from the log)
    _jdir, _log, _cur, snap_dir = jf.paths(path)
    on_disk = sorted(p.name for p in Path(snap_dir).iterdir())
    assert not any(x.startswith("0001-") or x.startswith("0002-") for x in on_disk)
    assert any(x.startswith("%04d-" % n) for x in on_disk)

    # the current state still undoes cleanly within the surviving window
    assert mc.journal_undo(path) == "main.py"
    assert _live(path) == "V%d\n" % (n - 1)


def test_journal_bytes_cap_triggers_compaction(tmp_path):
    # The 512 KiB cap fires independently of the 64-entry cap (whichever first).
    mc, path = _cart(tmp_path)
    size = 100 * 1024
    for i in range(8):
        text = ("%03d" % i) * (size // 3) + "\n"
        mc.journal_append(path, "main.py", text)
        (Path(path) / "main.py").write_text(text)

    _jdir, log_path, _cur, snap_dir = jf.paths(path)
    assert jf.total_len(jf.entries(log_path)) <= MAX_BYTES   # held under the byte cap
    assert len(jf.entries(log_path)) < 8                     # oldest rolled off


def test_journal_compact_keeps_current_state(tmp_path):
    # Compaction never drops the CURRENT state's snapshot -- undo/current always
    # resolves.
    mc, path = _cart(tmp_path)
    for i in range(MAX_ENTRIES + 4):
        mc.journal_append(path, "main.py", "S%d\n" % i)
        (Path(path) / "main.py").write_text("S%d\n" % i)
    entries = jf.entries(jf.paths(path)[1])
    assert entries
    cur = jf.current_snap(entries, _cursor(mc, path), "main.py")
    _jdir = jf.paths(path)[0]
    assert (Path(_jdir) / cur).read_text() == "S%d\n" % (MAX_ENTRIES + 3)


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

def test_journal_append_only_adds_to_the_log(tmp_path):
    # journal.jsonl grows by one line a commit: the existing prefix is byte-identical,
    # and a commit with nothing rewound writes no cursor at all (#154).
    mc, path = _cart(tmp_path)
    _jdir, log_path, cur, _snap = jf.paths(path)
    mc.journal_append(path, "main.py", "a\n")
    mc.journal_append(path, "main.py", "b\n")
    assert not Path(cur).exists()
    before = Path(log_path).read_bytes()
    mc.journal_append(path, "main.py", "c\n")
    after = Path(log_path).read_bytes()
    assert after.startswith(before)
    assert len(after) > len(before)


def test_journal_no_dir_until_first_append(tmp_path):
    # A cart that is only opened/played (never committed) grows no journal/ folder --
    # the journal is lazy, so a read-only session costs zero SD.
    mc, path = _cart(tmp_path)
    assert not (Path(path) / "journal").exists()
    mc.journal_append(path, "main.py", "x\n")
    assert (Path(path) / "journal" / "journal.jsonl").exists()


def test_a_no_op_append_writes_nothing(tmp_path):
    # A no-op append (the content-dedup skip) touches nothing in the journal.
    mc, path = _cart(tmp_path)
    mc.journal_append(path, "main.py", "same\n")
    jdir = Path(jf.paths(path)[0])
    before = sorted((str(p), p.stat().st_mtime_ns) for p in jdir.rglob("*"))
    assert mc.journal_append(path, "main.py", "same\n") is None
    assert sorted((str(p), p.stat().st_mtime_ns) for p in jdir.rglob("*")) == before


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
    # The bar's one read answers both, as the two checks do.
    from runtime import moy_journal
    for scope in (("main.py",), ("map.moymap",), None):
        assert moy_journal.journal_can_both(path, scope) == (
            mc.journal_can_undo(path, scope), mc.journal_can_redo(path, scope))


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


def test_a_cursor_without_a_map_loads_as_everything_applied(tmp_path):
    # No older cursor shape is read: a cursor.json carrying only a scalar `seq`
    # (the pre-#111 form) is the same as a missing one -- every file sits at its
    # newest entry, the safe 'everything applied' state.
    mc, path = _cart(tmp_path)
    mc.journal_append(path, "main.py", "A\n")            # seq 1
    mc.journal_append(path, "map.moymap", "M1\n")        # seq 2
    mc.journal_append(path, "main.py", "B\n")            # seq 3
    _jdir, _log, cur, _snap = jf.paths(path)
    Path(cur).write_text(json.dumps({"seq": 2, "bytes": 999}))

    entries = jf.entries(_log)
    assert jf.cursors(cur, entries) == {"main.py": 3, "map.moymap": 2}
    assert mc.journal_can_redo(path, ("main.py",)) is False

def test_missing_and_torn_cursor_default_to_newest(tmp_path):
    # A missing/torn cursor.json defaults every file to its newest entry (the safe
    # 'everything applied' state) -- so undo works and redo is dim.
    mc, path = _two_file_cart(tmp_path)
    _jdir, _log, cur, _snap = jf.paths(path)
    Path(cur).write_text("{ this is torn json")       # unparseable cursor

    entries = jf.entries(_log)
    assert jf.cursors(cur, entries) == {"main.py": 2, "map.moymap": 4}
    assert mc.journal_can_redo(path, ("main.py",)) is False   # already at the top
    assert mc.journal_can_undo(path, ("main.py",)) is True


# -- (h) the snapshot is the publish backup, claimed (#154) -----------------------

def _commit(store, path, text, file="main.py"):
    """Publish `text` the way every commit_* verb does -- _write_atomic first,
    then the journal line -- so the backup the snapshot claims is there."""
    from runtime import moy_fs
    moy_fs._write_atomic(str(Path(path) / file), text)
    return store.journal_append(path, file, text)


def test_a_published_commit_claims_its_backup_instead_of_writing_the_payload(tmp_path):
    """The bytes are already on the medium: _write_atomic left a stamped copy in
    <file>.bak, and that file is spent the moment the publish completes. The
    snapshot RENAMES it rather than putting a 20KB main.py down a third time
    (#154) -- so the commit writes no snapshot payload at all, and the entry
    says `stamped` so the reader knows to come back through the stamp."""
    store, path = _cart(tmp_path)
    src = "def _draw():\n    cls(3)\n"

    _commit(store, path, "v0\n")               # the first commit builds journal/ + s/
    _commit(store, path, src)

    jdir = path + "/journal"
    entries = jf.entries(jdir + "/journal.jsonl")
    assert len(entries) == 2
    e = entries[-1]
    assert e.get("stamped") == 1, "a published commit claims its backup"
    assert not Path(path + "/main.py.bak").exists(), "the spent backup MOVED"
    # and what an undo would restore is exactly the source, stamp line stripped
    assert jf.read_snap(jdir, e) == src


def test_bytes_that_were_never_published_get_a_written_snapshot(tmp_path):
    """The graduation BASELINE entry journals source that is NOT what the live
    file holds, so there is no backup of it to claim. It must fall through to
    writing its own copy rather than claiming the WRONG bytes."""
    store, path = _cart(tmp_path)
    _commit(store, path, "live\n")                 # the published state
    store.journal_append(path, "main.py", "a baseline that was never published\n")

    jdir = path + "/journal"
    entries = jf.entries(jdir + "/journal.jsonl")
    assert entries[-1].get("stamped") is None
    assert jf.read_snap(jdir, entries[-1]) == \
        "a baseline that was never published\n"
    assert jf.read_snap(jdir, entries[0]) == "live\n"


def test_both_snapshot_kinds_walk(tmp_path):
    """A claimed snapshot and a written one sit in the same history; undo must
    cross from one to the other and restore each exactly."""
    store, path = _cart(tmp_path)
    _commit(store, path, "v1\n")                      # claimed
    store.journal_append(path, "main.py", "v2\n")     # written (never published)
    (Path(path) / "main.py").write_text("v2\n")
    _commit(store, path, "v3\n")                      # claimed

    assert store.journal_undo(path) == "main.py"
    assert _live(path) == "v2\n"
    assert store.journal_undo(path) == "main.py"
    assert _live(path) == "v1\n"
    assert store.journal_redo(path) == "main.py"
    assert _live(path) == "v2\n"


def test_a_torn_claimed_snapshot_is_refused(tmp_path):
    """The claimed snapshot's own stamp is a crc over its payload -- strictly more
    than `len` can see. A snapshot whose text was mangled without changing its
    length must NOT be copied over the kid's live file."""
    store, path = _cart(tmp_path)
    _commit(store, path, "v1-good\n")
    _commit(store, path, "v2\n")

    jdir = path + "/journal"
    entries = jf.entries(jdir + "/journal.jsonl")
    snap = Path(jdir) / entries[0]["snap"]
    head, rest = snap.read_text().split("\n", 1)
    snap.write_text(head + "\n" + "v1-BAD!\n")        # same length, wrong bytes
    assert jf.read_snap(jdir, entries[0]) is None
    assert store.journal_undo(path) is None            # refused
    assert _live(path) == "v2\n"                       # the live file is untouched


def test_a_torn_cursor_is_recovered_not_defaulted(tmp_path):
    """The cursor is written with the three-write publish; this is the read half
    that makes that mean something. A cursor torn mid-publish -- the FAT case:
    truncated, so a strict PREFIX of the backup -- must come back as the position
    it held, not as the safe 'everything applied' default (#154)."""
    store, path = _cart(tmp_path)
    for text in ("v1\n", "v2\n", "v3\n"):
        _commit(store, path, text)
    assert store.journal_undo(path) == "main.py"     # rewind: cursor is no longer newest
    assert _live(path) == "v2\n"

    _jdir, log, cur, _snap = jf.paths(path)
    whole = Path(cur).read_text()
    Path(cur).write_text(whole[:len(whole) // 2])    # a torn publish of the cursor

    entries = jf.entries(log)
    cursors = jf.cursors(cur, entries)
    assert cursors != jf.newest_by_file(entries), \
        "a recoverable cursor must not fall back to the default"
    assert cursors == {"main.py": 2}
    assert store.journal_can_redo(path, ("main.py",)) is True


def test_a_rewound_cursor_is_written(tmp_path):
    """The other half of the cursor rule (#154): a commit with nothing rewound
    writes no cursor, but an undo leaves a position nothing can recompute."""
    mc, path = _cart(tmp_path)
    _jdir, _log, cur, _snap = jf.paths(path)
    for text in ("a\n", "b\n"):
        mc.journal_append(path, "main.py", text)
        (Path(path) / "main.py").write_text(text)
    assert not Path(cur).exists(), "at rest the cursor is absent, not written"

    assert mc.journal_undo(path) == "main.py"
    assert Path(cur).exists()

    # ...and stepping back to the top removes it again -- the default needs no file
    assert mc.journal_redo(path) == "main.py"
    assert not Path(cur).exists()
    assert _live(path) == "b\n"
