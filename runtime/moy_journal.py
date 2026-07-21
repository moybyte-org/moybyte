# The per-project undo/redo journal (#7, Stage 7 of docs/shell_ux_technical_plan_v1.md),
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
#   cursor.json    {"seq": N, "bytes": B} -- the undo position (N) + the running
#                  total snapshot bytes (B, the rotation gate). Written via
#                  _write_atomic: a tiny fixed-size file whose atomic rename is what
#                  makes the cursor torn-write-proof.
#   s/000N-<file>  the per-commit full-file snapshots.
#
# CADENCE (v1.1 pinned): the line APPEND is a raw open(path, "a") -- O(1), one line
# appended per commit -- and NEVER _write_atomic (which rewrites the whole file, so
# every commit would be O(n) in the journal's length). The only non-append rewrites
# are the RARE redo-tail truncation (a commit after an undo) and journal_compact
# (rotation) -- both between-frames like every SD op. Torn-write recovery: a torn
# last jsonl line fails json.loads and is dropped at load; the cursor is atomic.
#
# WALK: cursor N = "live files reflect commit seq N applied" (0 = pre-journal).
#   undo  = restore the same file's PREVIOUS snapshot over the live file, step the
#           cursor back one entry (floor = a file's first journaled snapshot; finer,
#           in-session undo stays in the editor's RAM).
#   redo  = re-apply the next commit's snapshot, step the cursor forward.
#   a NEW commit while the cursor is rewound TRUNCATES the redo tail (Google-Docs rule).
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


def _journal_cursor(cur_path, entries):
    """The undo position (a seq value; 0 = pre-journal). A missing/torn cursor.json
    defaults to the TOP (the latest entry's seq, i.e. everything applied) -- the safe
    'live files reflect the last commit' state."""
    try:
        data = json.loads(_read(cur_path))
        return int(data["seq"])
    except (OSError, ValueError, TypeError, KeyError):
        return entries[-1]["seq"] if entries else 0


def _journal_bytes(cur_path):
    """The running total snapshot bytes recorded in cursor.json (0 when absent), the
    cheap rotation gate so a normal append never has to stat every snapshot."""
    try:
        data = json.loads(_read(cur_path))
        return int(data.get("bytes", 0))
    except (OSError, ValueError, TypeError, AttributeError):
        return 0


def _journal_write_cursor(cur_path, seq, total_bytes):
    # cursor.json is tiny + fixed-shape -> _write_atomic (its atomic rename is the
    # torn-write proofing the append deliberately skips).
    _write_atomic(cur_path, json.dumps({"seq": int(seq), "bytes": int(total_bytes)}))


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
    cursor = _journal_cursor(cur_path, entries)
    total = _journal_bytes(cur_path)
    # -- ceiling / no-op dedup: identical to the current state -> write NOTHING (a
    #    debounce that fires with nothing changed must not touch the card). Checked
    #    BEFORE any _mkdir so a no-op append leaves no empty journal/ folder behind.
    cur_snap = _journal_current_snap(entries, cursor, file)
    if cur_snap is not None:
        try:
            if _read(jdir + "/" + cur_snap) == new_bytes:
                return None
        except OSError:
            pass
    # We are committing to a WRITE now -> create the journal dirs lazily.
    _mkdir(jdir)
    _mkdir(snap_dir)
    # -- Google-Docs rule: a commit while rewound truncates the redo tail. This is the
    #    ONE non-append rewrite on the commit path (rare -- only right after an undo).
    tail = [e for e in entries if e["seq"] > cursor]
    if tail:
        for e in tail:
            _remove(jdir + "/" + e["snap"])
        entries = [e for e in entries if e["seq"] <= cursor]
        _journal_rewrite(log_path, entries)
        total = _journal_total_bytes(jdir, entries)   # recompute exactly after the cut
    # -- assign the next seq, write the full-file snapshot, then RAW-append one line.
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
    _journal_write_cursor(cur_path, seq, total)       # cursor advances (atomic)
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


def journal_undo(cart_dir):
    """Restore `file`'s PREVIOUS snapshot over the live file and step the cursor back
    one entry. Returns the restored file name, or None at a floor (cursor 0, or the
    file has no earlier snapshot). The live write goes through _write_atomic exactly
    like a normal save."""
    jdir, log_path, cur_path, snap_dir = _journal_paths(cart_dir)
    entries = _journal_load_entries(log_path)
    if not entries:
        return None
    cursor = _journal_cursor(cur_path, entries)
    idx = None
    for k in range(len(entries)):
        if entries[k]["seq"] == cursor:
            idx = k
            break
    if idx is None:
        return None                        # cursor at 0 (or not found) -> nothing to undo
    file = entries[idx]["file"]
    target = None
    for k in range(idx - 1, -1, -1):       # nearest earlier snapshot of the SAME file
        if entries[k]["file"] == file:
            target = entries[k]
            break
    if target is None:
        return None                        # first snapshot of this file -> the floor
    data = _journal_read_snap(jdir, target)
    if data is None:
        return None                        # snapshot missing/torn -> REFUSE, live file intact
    _write_atomic(cart_dir + "/" + file, data)
    new_cursor = entries[idx - 1]["seq"] if idx > 0 else 0
    _journal_write_cursor(cur_path, new_cursor, _journal_bytes(cur_path))
    _journal_apply_grad(cart_dir, target)  # Stage 8: un-graduate past a graduating commit
    return file


def journal_can_undo(cart_dir):
    """Read-only companion to journal_undo (#88): True iff a walk would actually
    restore something (an earlier snapshot of the cursor's file exists), WITHOUT
    touching any live file or snapshot -- just entries + the cursor, so the bar can
    dim the UNDO icon cheaply. Mirrors journal_undo's own floor logic exactly (kept
    in lock-step deliberately) so the button's enabled state never lies about what a
    tap would do."""
    jdir, log_path, cur_path, snap_dir = _journal_paths(cart_dir)
    entries = _journal_load_entries(log_path)
    if not entries:
        return False
    cursor = _journal_cursor(cur_path, entries)
    idx = None
    for k in range(len(entries)):
        if entries[k]["seq"] == cursor:
            idx = k
            break
    if idx is None:
        return False                       # cursor at 0 (or not found) -> nothing to undo
    file = entries[idx]["file"]
    for k in range(idx - 1, -1, -1):       # nearest earlier snapshot of the SAME file
        if entries[k]["file"] == file:
            return True
    return False                           # first snapshot of this file -> the floor


def journal_can_redo(cart_dir):
    """Read-only companion to journal_redo (#88): True iff there is a next commit to
    re-apply. Mirrors journal_redo's own ceiling logic."""
    jdir, log_path, cur_path, snap_dir = _journal_paths(cart_dir)
    entries = _journal_load_entries(log_path)
    if not entries:
        return False
    cursor = _journal_cursor(cur_path, entries)
    for e in entries:                      # ascending -> the smallest seq > cursor
        if e["seq"] > cursor:
            return True
    return False


def journal_redo(cart_dir):
    """Re-apply the next commit's snapshot over the live file and step the cursor
    forward. Returns the restored file name, or None at the top (nothing to redo)."""
    jdir, log_path, cur_path, snap_dir = _journal_paths(cart_dir)
    entries = _journal_load_entries(log_path)
    if not entries:
        return None
    cursor = _journal_cursor(cur_path, entries)
    nxt = None
    for e in entries:                      # ascending -> the smallest seq > cursor
        if e["seq"] > cursor:
            nxt = e
            break
    if nxt is None:
        return None                        # at the top -> nothing to redo
    data = _journal_read_snap(jdir, nxt)
    if data is None:
        return None                        # snapshot missing/torn -> REFUSE, live file intact
    _write_atomic(cart_dir + "/" + nxt["file"], data)
    _journal_write_cursor(cur_path, nxt["seq"], _journal_bytes(cur_path))
    _journal_apply_grad(cart_dir, nxt)     # Stage 8: re-graduate on redo past the commit
    return nxt["file"]


def journal_compact(cart_dir):
    """Drop the OLDEST entries + their snapshots until the journal is within the cap
    (JOURNAL_MAX_ENTRIES entries AND JOURNAL_MAX_BYTES of snapshots). A full
    journal.jsonl rewrite + snapshot deletes -- the one non-append-only path, run
    between frames like every SD op. NEVER drops any file's current-state snapshot
    (latest seq <= cursor) or the redo tail (seq > cursor), so the current + every
    reachable redo survive. Returns the number of entries dropped."""
    jdir, log_path, cur_path, snap_dir = _journal_paths(cart_dir)
    entries = _journal_load_entries(log_path)
    if not entries:
        return 0
    cursor = _journal_cursor(cur_path, entries)
    keep = {}
    current = {}
    for e in entries:
        if e["seq"] > cursor:
            keep[e["seq"]] = True          # redo tail: never drop
        else:
            current[e["file"]] = e["seq"]  # ascending -> latest <= cursor per file
    for s in current.values():
        keep[s] = True                     # each file's current-state snapshot: never drop
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
    _journal_write_cursor(cur_path, cursor, _journal_total_bytes(jdir, remaining))
    return len(dropped)
