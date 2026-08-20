# The per-project undo/redo journal (#7, Stage 7 of docs/history/shell_ux_technical_plan_v1.md),
# extracted from moy_carts.py (which re-exports every name here, so `store.journal_*`
# call sites and tests are unchanged). MicroPython-safe (json + os only); file
# primitives come from the shared moy_fs leaf. The ONE journal->store edge
# (_manifest_set_graduated, the Stage 8 graduation rider) is imported LAZILY inside
# _set_graduated_flag to keep the module graph acyclic.

import json

try:
    import os
except ImportError:  # pragma: no cover
    os = None

try:
    import time as _time
except ImportError:  # pragma: no cover
    _time = None

try:
    from moy_fs import _mkdir, _read, _write, _write_atomic, _remove
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime.moy_fs import _mkdir, _read, _write, _write_atomic, _remove


def _set_graduated_flag(cart_dir, value):
    """Set the manifest's `graduated` flag via moy_carts (bare on device, package
    on host). Lazy: moy_carts re-exports this module's verbs, so a top-level
    import here would be circular."""
    try:
        from moy_carts import _manifest_set_graduated
    except ImportError:
        from runtime.moy_carts import _manifest_set_graduated
    _manifest_set_graduated(cart_dir, value)

#
# A durable, per-project, reboot-surviving undo history stored on SD BESIDE the
# cart's files, in <cart>.moy/journal/:
#
#   journal.jsonl  APPEND-ONLY -- one JSON line per commit event:
#                  {"seq": N, "ts": ..., "file": "main.py", "snap": "s/000N-main.py"}
#                  the entry points at a FULL-FILE snapshot under journal/s/. Full
#                  snapshots, not diffs: MicroPython-safe (no difflib), and one bad
#                  snapshot loses one step, never the whole history.
#   cursor.json    {"cursors": {file: seq, ...}, "seq": N, "bytes": B} -- a PER-FILE
#                  undo position map (#111: one cursor per journaled file, so an undo
#                  on one file/tab never walks another file's timeline, and redo only
#                  lights up on the tab that actually has something ahead). Plus a
#                  legacy scalar `seq` (= the max applied seq across files, kept purely
#                  for old readers/tools + the reboot-cursor test) and the running total
#                  snapshot bytes (B, the rotation gate). Written via _write_atomic: a
#                  tiny fixed-size file whose atomic rename is what makes the cursor
#                  torn-write-proof. TOLERANT MIGRATION: an OLD single-`seq` cursor
#                  (pre-#111, no "cursors" key) loads as "each file's cursor = its
#                  newest entry seq <= the old seq"; a missing/torn cursor defaults
#                  every file to its newest entry (the safe 'everything applied' state).
#   s/000N-<file>  the per-commit full-file snapshots.
#
# CADENCE (v1.1 pinned): the line APPEND is a raw open(path, "a") -- O(1), one line
# appended per commit -- and NEVER _write_atomic (which rewrites the whole file, so
# every commit would be O(n) in the journal's length). The only non-append rewrites
# are the RARE per-file redo-tail truncation (a commit of file F after an undo of F)
# and journal_compact (rotation) -- both between-frames like every SD op.
#
# TORN-WRITE ORDERING GUARANTEE (must survive any edit to journal_append): a commit
# writes the SNAPSHOT first, THEN raw-appends the log line, THEN atomically rewrites the
# cursor. So a power loss can only ever leave (a) an UNREFERENCED orphan snapshot or
# (b) a torn last log line -- both dropped at load (json.loads-guarded), never a cursor
# pointing at a half-written entry. Moving to a per-file cursor MAP does not change this:
# the map is still written LAST, via _write_atomic, so it is only ever advanced to seqs
# whose snapshot + log line are already durable.
#
# WALK (#111 PER-FILE): each file F carries its own cursor C[F] = the seq of F's applied
#   (live) snapshot. A walk takes an OPTIONAL `files` filter (a tuple of journal file
#   names; None = the legacy whole-project walk over every file):
#   undo  = among the filtered files that CAN step back (C[F] is not F's first snapshot),
#           restore the NEWEST one's (max C[F]) PREVIOUS snapshot over the live file and
#           step ONLY that file's cursor back one F-entry (floor = a file's first
#           journaled snapshot; finer, in-session undo stays in the editor's RAM).
#   redo  = among the filtered files, re-apply the chronologically-nearest next commit
#           (the smallest F-entry seq > C[F]) and step that file's cursor forward.
#   a NEW commit of file F while F is rewound TRUNCATES only F's redo tail (Google-Docs
#   rule, PER-FILE -- other files' redo tails survive).
#
# ROTATION: a per-project cap of 64 entries OR 512KB of snapshots (whichever first);
# journal_compact drops the OLDEST entries + their snapshots (a full journal.jsonl
# rewrite + snapshot deletes -- the one place the journal is not append-only). It
# never drops any file's current-state snapshot or the redo tail.

JOURNAL_DIR = "journal"
JOURNAL_LOG = "journal.jsonl"
JOURNAL_CURSOR = "cursor.json"
JOURNAL_SNAP_DIR = "s"
JOURNAL_MAX_ENTRIES = 64
JOURNAL_MAX_BYTES = 512 * 1024


def _journal_paths(cart_dir):
    jdir = cart_dir + "/" + JOURNAL_DIR
    return jdir, jdir + "/" + JOURNAL_LOG, jdir + "/" + JOURNAL_CURSOR, jdir + "/" + JOURNAL_SNAP_DIR


def _journal_ts():
    if _time is None:
        return 0
    try:
        return int(_time.time())
    except Exception:  # noqa: BLE001 -- ts is informational; never let it break a commit
        return 0


def _journal_snap_name(seq, file):
    # A subfolder asset (#85: scenes/<name>.moyscene) has a "/" in its `file`; flatten
    # it in the SNAPSHOT filename so the snapshot lands directly under journal/s/ (a
    # "/" would open into a non-existent subdir). The entry's `file` field keeps the
    # real relative path, so undo/redo still restore to cart_dir/scenes/<name>.moyscene.
    # No-op for the existing top-level files (main.py/sprites.moygfx/... have no "/").
    return "%04d-%s" % (seq, file.replace("/", "_"))


def _journal_load_entries(log_path):
    """Parse journal.jsonl into a list of entry dicts sorted by seq. A torn/corrupt
    line (the append-only log's only failure mode -- a torn LAST line from a power
    loss mid-append) fails json.loads and is DROPPED; every well-formed entry before
    it survives. Missing log -> []."""
    entries = []
    try:
        raw = _read(log_path)
    except OSError:
        return entries
    for line in raw.split("\n"):
        if not line.strip():
            continue
        try:
            e = json.loads(line)
        except ValueError:
            continue                       # torn / corrupt line -> drop it, keep the rest
        if isinstance(e, dict) and "seq" in e and "file" in e and "snap" in e:
            entries.append(e)
    entries.sort(key=lambda e: e["seq"])
    return entries


def _journal_newest_by_file(entries):
    """{file: seq} of each file's NEWEST entry (its latest seq). The safe
    'everything applied' default for the per-file cursor map, and the base the
    migration + validation fill from."""
    newest = {}
    for e in entries:                      # ascending -> the last seq per file wins
        newest[e["file"]] = e["seq"]
    return newest


def _journal_cursors(cur_path, entries):
    """The PER-FILE undo position map {file: seq} (#111). Every journaled file has an
    entry; a file's cursor is the seq of its applied (live) snapshot. Resolution order:

      * a NEW-format cursor.json ({"cursors": {...}}) -> use it, validated to ints and
        BACKFILLED so any file present in the journal but missing from the map defaults
        to its newest entry (never leaves a file cursor-less);
      * an OLD single-`seq` cursor (pre-#111) -> TOLERANT MIGRATION: each file's cursor
        = its newest entry seq <= the old seq (a file with no entry <= old seq falls to
        the safe default: its newest entry, 'everything applied');
      * a missing/torn cursor -> every file defaults to its newest entry (safe state).
    """
    default = _journal_newest_by_file(entries)
    try:
        data = json.loads(_read(cur_path))
    except (OSError, ValueError, TypeError):
        return default
    if not isinstance(data, dict):
        return default
    raw = data.get("cursors")
    if isinstance(raw, dict):
        out = dict(default)                # backfill: unknown/new files -> newest
        for f, s in raw.items():
            try:
                if f in default:           # ignore stale files no longer in the journal
                    out[f] = int(s)
            except (TypeError, ValueError):
                pass
        return out
    # -- old single-seq cursor: migrate to newest-entry-<=-old per file --------
    try:
        old = int(data["seq"])
    except (KeyError, TypeError, ValueError):
        return default
    out = dict(default)                    # files with no entry <= old stay at newest
    migrated = {}
    for e in entries:                      # ascending -> newest <= old per file wins
        if e["seq"] <= old:
            migrated[e["file"]] = e["seq"]
    for f, s in migrated.items():
        out[f] = s
    return out


def _journal_max_applied(cursors):
    """The legacy scalar cursor seq = the max applied seq across files (0 for an empty
    map). Kept in cursor.json's `seq` field for old readers/tools + the reboot test."""
    best = 0
    for s in cursors.values():
        if s and s > best:
            best = s
    return best


def _journal_cursor(cur_path, entries):
    """BACK-COMPAT scalar cursor (= max applied seq across files; 0 = pre-journal).
    The per-file map (_journal_cursors) is the source of truth now; this stays for any
    old reader/tool that still asks for the single position."""
    return _journal_max_applied(_journal_cursors(cur_path, entries))


def _journal_bytes(cur_path):
    """The running total snapshot bytes recorded in cursor.json (0 when absent), the
    cheap rotation gate so a normal append never has to stat every snapshot."""
    try:
        data = json.loads(_read(cur_path))
        return int(data.get("bytes", 0))
    except (OSError, ValueError, TypeError, AttributeError):
        return 0


def _journal_prune_cursors(cursors, entries):
    """Drop cursor entries for files that no longer have any journal entry (after a
    truncation/compaction removed them), so the map never carries dead files."""
    live = {}
    for e in entries:
        live[e["file"]] = True
    out = {}
    for f, s in cursors.items():
        if f in live:
            out[f] = s
    return out


def _journal_write_cursors(cur_path, cursors, total_bytes):
    # cursor.json is tiny + fixed-shape -> _write_atomic (its atomic rename is the
    # torn-write proofing the append deliberately skips). Writes the per-file map plus a
    # legacy scalar `seq` (max applied) for old readers. This is the LAST write of a
    # commit (see the TORN-WRITE ORDERING GUARANTEE at the top).
    cmap = {}
    for f, s in cursors.items():
        cmap[f] = int(s)
    _write_atomic(cur_path, json.dumps(
        {"cursors": cmap, "seq": _journal_max_applied(cmap), "bytes": int(total_bytes)}))


def _journal_rewrite(log_path, entries):
    # The ONLY non-append writes of the log: redo-tail truncation + compaction. Rare,
    # so _write_atomic (crash-safe) is fine here -- it is NOT on the per-commit path.
    _write_atomic(log_path, "".join(json.dumps(e) + "\n" for e in entries))


def _journal_current_snap(entries, cursor, file):
    """The snapshot representing `file`'s current live state = the latest entry for
    that file with seq <= cursor (or None if the file has no snapshot yet)."""
    best = None
    for e in entries:                      # ascending -> the last match <= cursor wins
        if e["file"] == file and e["seq"] <= cursor:
            best = e
    return best["snap"] if best else None


def _journal_total_bytes(jdir, entries):
    total = 0
    for e in entries:
        try:
            total += os.stat(jdir + "/" + e["snap"])[6]   # [6] = st_size (host + MicroPython)
        except OSError:
            pass
    return total


def _journal_read_snap(jdir, entry):
    """Read + INTEGRITY-CHECK an entry's snapshot before it is copied over a live file.
    Returns the snapshot text, or None when it is missing or torn -- so undo/redo can
    refuse a damaged snapshot instead of overwriting good work with garbage/empty.

    Validated against the recorded `len`: a length mismatch (truncated / 0-byte from a
    device power loss -- snapshots are non-atomic, no fsync) is rejected; an entry that
    legitimately snapshotted an empty file (len == 0) still restores cleanly. Legacy
    entries without a recorded `len` fall back to "reject an empty read as likely-torn"."""
    try:
        data = _read(jdir + "/" + entry["snap"])
    except OSError:
        return None                            # missing snapshot -> refuse
    exp = entry.get("len")
    if exp is None:
        return data if data else None          # unlabelled: an empty read is likely torn
    if len(data) != int(exp):
        return None                            # truncated / torn -> refuse
    return data


def _journal_apply_grad(cart_dir, entry):
    """Sync the manifest's `graduated` flag to `entry`'s grad rider (Stage 8) after an
    undo/redo restores its snapshot. Only main.py entries carry `grad`; an entry
    without one leaves the flag untouched (never guesses). Best-effort -- a manifest
    hiccup must not fail the walk (the live file is already restored)."""
    if "grad" not in entry:
        return
    try:
        _set_graduated_flag(cart_dir, int(entry["grad"]))
    except Exception as exc:  # noqa: BLE001
        print("Moybyte graduation flag walk failed:", exc)


def journal_entry_ops(entry):
    """The fine-grained op batch (#111) an entry carries between its snapshot and
    the previous one, or [] for a pre-#111 entry (the field is additive, so old
    journals -- and every non-ops commit -- simply have no `ops` key). Guarded so
    a hand-mangled value never crashes a walk: anything but a list reads as []."""
    if not isinstance(entry, dict):
        return []
    ops = entry.get("ops")
    return ops if isinstance(ops, list) else []


def journal_append(cart_dir, file, new_bytes, grad=None, ops=None):
    """Record a durable commit event for `file`: snapshot `new_bytes` under journal/s/
    and RAW-append one line to journal.jsonl (O(1)). Returns the new seq, or None when
    nothing was written (a no-op: the content already matches the current state).

    Order (torn-write safe): snapshot first, THEN the log line, THEN the cursor -- so a
    crash never leaves a log line pointing at a torn snapshot (the orphan snapshot is
    simply unreferenced). A commit made while the cursor is rewound truncates the redo
    tail first (Google-Docs rule). Rotation runs at the end when over cap.

    `ops` (#111): an OPTIONAL list of fine-grained, JSON-able ops (from an editor's
    op_history.History.flush()) that transform the PREVIOUS keyframe into this one --
    embedded additively in the commit line so an undo can cross the stroke->commit
    boundary without a coarse snapshot jump. Snapshots stay the source of truth; a
    reader that doesn't know about ops ignores the key, and an empty/None batch writes
    no key at all (old journals + non-ops commits are byte-for-byte as before).

    `grad` (Stage 8, spec Section 8): an optional 0/1 GRADUATION rider that rides a
    main.py commit -- the graduated state of the cart AT this commit. When an entry is
    actually appended with a grad rider, the manifest's `graduated` flag is set to it
    (so the one-way flip rides the exact same durable step as the source), and
    journal_undo/redo re-apply the target entry's grad -- which is how an undo past a
    graduating commit restores BOTH the source and graduated:false."""
    if new_bytes is None:
        return None
    jdir, log_path, cur_path, snap_dir = _journal_paths(cart_dir)
    entries = _journal_load_entries(log_path)   # empty when there's no journal/ yet
    cursors = _journal_cursors(cur_path, entries)   # #111: per-file cursor map
    total = _journal_bytes(cur_path)
    # -- ceiling / no-op dedup: identical to THIS FILE's current state -> write NOTHING
    #    (a debounce that fires with nothing changed must not touch the card). Checked
    #    BEFORE any _mkdir so a no-op append leaves no empty journal/ folder behind.
    cf = cursors.get(file)
    cur_snap = _journal_current_snap(entries, cf, file) if cf is not None else None
    if cur_snap is not None:
        try:
            if _read(jdir + "/" + cur_snap) == new_bytes:
                return None
        except OSError:
            pass
    # We are committing to a WRITE now -> create the journal dirs lazily.
    _mkdir(jdir)
    _mkdir(snap_dir)
    # -- Google-Docs rule, PER-FILE: a commit of `file` while `file` is rewound truncates
    #    only THIS FILE's redo tail (other files' redo tails survive). The ONE non-append
    #    rewrite on the commit path (rare -- only right after an undo of this file).
    tail = [e for e in entries if e["file"] == file and (cf is None or e["seq"] > cf)]
    if tail:
        cut = {}
        for e in tail:
            cut[e["seq"]] = True
            _remove(jdir + "/" + e["snap"])
        entries = [e for e in entries if e["seq"] not in cut]
        _journal_rewrite(log_path, entries)
        total = _journal_total_bytes(jdir, entries)   # recompute exactly after the cut
        cursors = _journal_prune_cursors(cursors, entries)  # drop any now-empty file
    # -- assign the next seq (global-monotonic: max remaining + 1, so surviving other-file
    #    entries above the cut keep unique seqs), write the snapshot, then RAW-append.
    seq = (entries[-1]["seq"] + 1) if entries else 1
    snap = JOURNAL_SNAP_DIR + "/" + _journal_snap_name(seq, file)
    _write(jdir + "/" + snap, new_bytes)              # snapshot BEFORE the log line
    # `len` is the snapshot's recorded length: undo/redo validate the on-disk snapshot
    # against it before copying it over the live file, so a torn/truncated snapshot (a
    # device power loss + FAT cache reordering -- snapshots are non-atomic) is REFUSED
    # rather than silently overwriting good work with garbage/empty.
    entry = {"seq": seq, "ts": _journal_ts(), "file": file, "snap": snap, "len": len(new_bytes)}
    if grad is not None:
        entry["grad"] = int(grad)                     # Stage 8 graduation rider
    if ops:
        entry["ops"] = list(ops)                      # #111 fine-grained op batch (additive)
    with open(log_path, "a") as f:                    # RAW append -- O(1), NOT _write_atomic
        f.write(json.dumps(entry) + "\n")
    total += len(new_bytes)
    cursors[file] = seq                               # this file now applied at the new commit
    _journal_write_cursors(cur_path, cursors, total)  # cursor map advances (atomic, LAST)
    # -- graduation flip rides this exact durable step (Stage 8): the manifest's
    #    `graduated` follows the appended entry's grad. Guarded -- a manifest hiccup
    #    must not undo the append that just succeeded (the entry is already durable).
    if grad is not None:
        try:
            _set_graduated_flag(cart_dir, int(grad))
        except Exception as exc:  # noqa: BLE001
            print("Moybyte graduation flag write failed:", exc)
    # -- rotation: keep within the per-project cap (drops oldest, between frames).
    if len(entries) + 1 > JOURNAL_MAX_ENTRIES or total > JOURNAL_MAX_BYTES:
        journal_compact(cart_dir)
    return seq


def _journal_file_groups(entries, files):
    """{file: [entries ascending]} for the files in scope. `files` is a filter iterable
    of file names, or None = every file present. The one place the scoped walk narrows
    to the active tab's file set (#111)."""
    fset = None if files is None else set(files)
    groups = {}
    for e in entries:
        f = e["file"]
        if fset is not None and f not in fset:
            continue
        groups.setdefault(f, []).append(e)   # entries are already seq-ascending
    return groups


def _journal_undo_target(entries, cursors, files):
    """Pick the file whose undo the bar UNDO should take (#111 scoped walk): among the
    filtered files that CAN step back (their cursor is not their first snapshot),
    the one with the NEWEST applied entry (max cursor seq). Returns (file, target_entry,
    new_cursor_seq) -- target_entry is the earlier snapshot to restore -- or None."""
    best_file = None
    best_target = None
    best_new = None
    best_seq = -1
    for f, elist in _journal_file_groups(entries, files).items():
        cf = cursors.get(f)
        if cf is None:
            continue
        idx = None
        for k in range(len(elist)):
            if elist[k]["seq"] == cf:
                idx = k
                break
        if idx is None or idx == 0:
            continue                       # cursor below/at this file's first snapshot -> floor
        if cf > best_seq:                  # newest applied among the scoped files
            best_seq = cf
            best_file = f
            best_target = elist[idx - 1]   # the previous snapshot of THIS file
            best_new = elist[idx - 1]["seq"]
    if best_file is None:
        return None
    return best_file, best_target, best_new


def _journal_redo_target(entries, cursors, files):
    """Pick the file whose redo the bar REDO should take (#111 scoped walk): among the
    filtered files, the chronologically-nearest next commit (smallest F-entry seq >
    that file's cursor). Returns (file, next_entry) or None."""
    best_file = None
    best_next = None
    best_seq = None
    for f, elist in _journal_file_groups(entries, files).items():
        cf = cursors.get(f)
        nxt = None
        for e in elist:
            if cf is None or e["seq"] > cf:
                nxt = e
                break
        if nxt is None:
            continue
        if best_seq is None or nxt["seq"] < best_seq:
            best_seq = nxt["seq"]
            best_file = f
            best_next = nxt
    if best_file is None:
        return None
    return best_file, best_next


def journal_undo(cart_dir, files=None):
    """Restore the PREVIOUS snapshot of the newest applied file (among `files`) over the
    live file and step ONLY that file's cursor back one entry (#111). `files` is an
    optional tuple of journal file names scoping the walk to the active tab's file(s);
    None = the legacy whole-project walk. Returns the restored file name, or None at a
    floor. The live write goes through _write_atomic exactly like a normal save."""
    jdir, log_path, cur_path, snap_dir = _journal_paths(cart_dir)
    entries = _journal_load_entries(log_path)
    if not entries:
        return None
    cursors = _journal_cursors(cur_path, entries)
    picked = _journal_undo_target(entries, cursors, files)
    if picked is None:
        return None                        # nothing to undo in scope -> floor
    file, target, new_cursor = picked
    data = _journal_read_snap(jdir, target)
    if data is None:
        return None                        # snapshot missing/torn -> REFUSE, live file intact
    _write_atomic(cart_dir + "/" + file, data)
    cursors[file] = new_cursor             # step ONLY this file's cursor back
    _journal_write_cursors(cur_path, cursors, _journal_bytes(cur_path))
    _journal_apply_grad(cart_dir, target)  # Stage 8: un-graduate past a graduating commit
    return file


def journal_can_undo(cart_dir, files=None):
    """Read-only companion to journal_undo (#88/#111): True iff a scoped walk would
    restore something, WITHOUT touching any live file or snapshot -- just entries + the
    cursor map, so the bar can dim the UNDO icon cheaply. Mirrors journal_undo's own
    scoped floor logic exactly (via the shared _journal_undo_target) so the button's
    enabled state never lies about what a tap would do."""
    jdir, log_path, cur_path, snap_dir = _journal_paths(cart_dir)
    entries = _journal_load_entries(log_path)
    if not entries:
        return False
    cursors = _journal_cursors(cur_path, entries)
    return _journal_undo_target(entries, cursors, files) is not None


def journal_can_redo(cart_dir, files=None):
    """Read-only companion to journal_redo (#88/#111): True iff there is a next commit
    to re-apply WITHIN the scoped files. Mirrors journal_redo's own ceiling logic (the
    shared _journal_redo_target), so REDO lights up only on the tab that has one."""
    jdir, log_path, cur_path, snap_dir = _journal_paths(cart_dir)
    entries = _journal_load_entries(log_path)
    if not entries:
        return False
    cursors = _journal_cursors(cur_path, entries)
    return _journal_redo_target(entries, cursors, files) is not None


def journal_redo(cart_dir, files=None):
    """Re-apply the next commit's snapshot over the live file and step ONLY that file's
    cursor forward (#111 scoped). `files` scopes the walk to the active tab's file(s);
    None = whole-project. Returns the restored file name, or None at the top."""
    jdir, log_path, cur_path, snap_dir = _journal_paths(cart_dir)
    entries = _journal_load_entries(log_path)
    if not entries:
        return None
    cursors = _journal_cursors(cur_path, entries)
    picked = _journal_redo_target(entries, cursors, files)
    if picked is None:
        return None                        # nothing ahead in scope -> at the top
    file, nxt = picked
    data = _journal_read_snap(jdir, nxt)
    if data is None:
        return None                        # snapshot missing/torn -> REFUSE, live file intact
    _write_atomic(cart_dir + "/" + file, data)
    cursors[file] = nxt["seq"]             # step ONLY this file's cursor forward
    _journal_write_cursors(cur_path, cursors, _journal_bytes(cur_path))
    _journal_apply_grad(cart_dir, nxt)     # Stage 8: re-graduate on redo past the commit
    return file


def journal_compact(cart_dir):
    """Drop the OLDEST entries + their snapshots until the journal is within the cap
    (JOURNAL_MAX_ENTRIES entries AND JOURNAL_MAX_BYTES of snapshots). A full
    journal.jsonl rewrite + snapshot deletes -- the one non-append-only path, run
    between frames like every SD op. NEVER drops any file's current-state snapshot
    (the entry AT its cursor) or its redo tail (seq > its cursor), so the current +
    every reachable redo survive; only the deep undo history BELOW each file's cursor
    is droppable. Returns the number of entries dropped."""
    jdir, log_path, cur_path, snap_dir = _journal_paths(cart_dir)
    entries = _journal_load_entries(log_path)
    if not entries:
        return 0
    cursors = _journal_cursors(cur_path, entries)   # #111: per-file cursor map
    keep = {}
    for e in entries:
        cf = cursors.get(e["file"])
        if cf is None or e["seq"] >= cf:   # this file's cursor entry + its redo tail
            keep[e["seq"]] = True          # (droppable = only entries strictly below a cursor)
    droppable = [e for e in entries if e["seq"] not in keep]
    droppable.sort(key=lambda e: e["seq"])  # oldest first
    remaining = list(entries)
    total = _journal_total_bytes(jdir, remaining)
    dropped = []
    di = 0
    while ((len(remaining) > JOURNAL_MAX_ENTRIES or total > JOURNAL_MAX_BYTES)
           and di < len(droppable)):
        victim = droppable[di]
        di += 1
        try:
            total -= os.stat(jdir + "/" + victim["snap"])[6]
        except OSError:
            pass
        remaining = [e for e in remaining if e["seq"] != victim["seq"]]
        dropped.append(victim)
    if not dropped:
        return 0
    for e in dropped:
        _remove(jdir + "/" + e["snap"])
    _journal_rewrite(log_path, remaining)
    cursors = _journal_prune_cursors(cursors, remaining)
    _journal_write_cursors(cur_path, cursors, _journal_total_bytes(jdir, remaining))
    return len(dropped)
