# Map (grep -n a name to jump there):
#   journal_list      a file's entries (#136's timeline)
#   journal_snap      one entry's snapshot
#   journal_restore   an old snapshot appended as a new commit
#   journal_append    record a commit: snapshot and op batch
#   journal_undo      restore the previous snapshot
#   journal_redo      re-apply the next snapshot
#   journal_can_undo  can undo, read-only
#   journal_compact   drop the oldest entries to the budget
# The per-project undo/redo journal (#7), moy_carts re-exports every name here.
# Its body is native/moy_store/moy_journal.c on every tier (the module
# `moy_store`; CPython's face of it is runtime/host/moy_store_binding.py): what
# is here is the face and the format every tier's files are in.

import json

try:
    import time as _time
except ImportError:  # pragma: no cover
    _time = None

import moy_store as _native

#
# A durable, per-project, reboot-surviving undo history stored on SD BESIDE the
# cart's files, in <cart>.moy/journal/:
#
#   journal.jsonl  APPEND-ONLY -- one JSON line per commit event:
#                  {"seq": N, "ts": ..., "file": "main.py", "snap": "s/000N-main.py",
#                   "len": C, "crc": X}
#                  the entry points at a FULL-FILE snapshot under journal/s/ and
#                  carries that snapshot's STAMP (chars + crc32). Full snapshots, not
#                  diffs: MicroPython-safe (no difflib), and one bad snapshot loses one
#                  step, never the whole history. The stamp is what lets the NEXT
#                  commit's no-op check answer without reading the snapshot back.
#   cursor.json    {"cursors": {file: seq, ...}, "seq": N, "bytes": B} -- a PER-FILE
#                  undo position map (#111: one cursor per journaled file, so an undo
#                  on one file/tab never walks another file's timeline, and redo only
#                  lights up on the tab that actually has something ahead). Plus a
#                  legacy scalar `seq` (= the max applied seq across files, kept purely
#                  for old readers/tools). A cursor with no "cursors" map, missing AND
#                  unrecoverable, or torn beyond recovery, defaults every file to its
#                  newest entry -- the safe 'everything applied' state, and no older
#                  cursor shape is read.
#                  The file EXISTS ONLY WHILE SOMETHING IS REWOUND: a commit whose map
#                  is that same default removes it instead of writing it, because an
#                  absent cursor already says exactly that and saying it costs three
#                  file opens on a card (#154). When it is written it goes through
#                  _write_atomic and comes back through _read_recover, which is the
#                  pair that makes it torn-write-proof -- the write alone is not: a
#                  plain read of a torn cursor never opens the backup beside it.
#   s/000N-<file>  the per-commit full-file snapshots. Usually not written at all:
#                  every commit_* verb publishes the same bytes through _write_atomic
#                  first, which leaves them in the live file's spent crash backup, and
#                  the snapshot CLAIMS that file with a rename (`_claim_bak`). Such an
#                  entry is marked `"stamped": 1` and keeps moy_fs's stamp line as its
#                  first line, so it is read back through `_read_stamped`. A caller
#                  whose bytes were never published -- the graduation BASELINE entry --
#                  fails the stamp check and gets a plain written snapshot.
#
# CADENCE (v1.1 pinned): the line APPEND is a raw open(path, "a") -- O(1), one line
# appended per commit -- and NEVER _write_atomic (which rewrites the whole file, so
# every commit would be O(n) in the journal's length). The only non-append rewrites
# are the RARE per-file redo-tail truncation (a commit of file F after an undo of F)
# and journal_compact (rotation) -- both between-frames like every SD op.
#
# TORN-WRITE ORDERING GUARANTEE (must survive any edit to journal_append): a commit
# puts the SNAPSHOT down first, THEN raw-appends the log line, THEN settles the cursor.
# So a power loss can only ever leave (a) an UNREFERENCED orphan snapshot or (b) a torn
# last log line -- both dropped at load (json.loads-guarded), never a cursor pointing at
# a half-written entry. Neither the per-file cursor MAP nor the two cheaper shapes the
# steps now take change that: the snapshot may arrive as a RENAME of the publish backup
# rather than a write, and the cursor may be REMOVED rather than written (an absent one
# reads as the same map) -- but each still happens in this order, and the cursor is only
# ever advanced to seqs whose snapshot and log line are already durable.
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
# ROTATION: a per-project cap of 64 entries OR 512KB of snapshots (whichever first,
# measured by summing the entries' own recorded `len` -- derived, so it cannot drift
# and costs no os.stat per snapshot);
# journal_compact drops the OLDEST entries + their snapshots (a full journal.jsonl
# rewrite + snapshot deletes -- the one place the journal is not append-only). It
# never drops any file's current-state snapshot or the redo tail.

JOURNAL_DIR = "journal"
JOURNAL_LOG = "journal.jsonl"
JOURNAL_CURSOR = "cursor.json"
JOURNAL_SNAP_DIR = "s"


# Seconds from 1970 to `time.time()`'s epoch (2000 on a board's MicroPython),
# so an entry's "ts" is Unix seconds on every tier, as a synced file's entry
# is (native/moy_net/moy_net.h, moy_net_time).
_EPOCH = 946684800 if _time is not None and _time.gmtime(0)[0] == 2000 else 0


def _journal_ts():
    if _time is None:
        return 0
    try:
        return int(_time.time()) + _EPOCH
    except Exception:  # noqa: BLE001 -- ts is informational; never let it break a commit
        return 0


def journal_entry_ops(entry):
    """The fine-grained op batch (#111) an entry carries between its snapshot and
    the previous one, or [] for a pre-#111 entry (the field is additive, so old
    journals -- and every non-ops commit -- simply have no `ops` key). Guarded so
    a hand-mangled value never crashes a walk: anything but a list reads as []."""
    if not isinstance(entry, dict):
        return []
    ops = entry.get("ops")
    return ops if isinstance(ops, list) else []


def _files(files):
    return None if files is None else list(files)


def journal_append(cart_dir, file, new_bytes, grad=None, ops=None):
    """Record a commit of `file`'s `new_bytes`: its seq, or None when the file's
    current snapshot already holds them (or there are none). `grad` is the
    graduation rider (a bool, None for none), `ops` the op batch."""
    if new_bytes is None:
        return None
    return _native.journal_append(cart_dir, file, new_bytes,
                                  None if grad is None else int(grad),
                                  json.dumps(list(ops)) if ops else None,
                                  _journal_ts())


def journal_undo(cart_dir, files=None):
    """The newest of `files` (None: every file) stepped back one snapshot: the
    file's name, or None at the floor or over a damaged snapshot."""
    return _native.journal_undo(cart_dir, _files(files))


def journal_redo(cart_dir, files=None):
    """The nearest next snapshot of `files` re-applied: the file's name, or None."""
    return _native.journal_redo(cart_dir, _files(files))


def journal_can_undo(cart_dir, files=None):
    return _native.journal_can(cart_dir, False, _files(files))


def journal_can_redo(cart_dir, files=None):
    return _native.journal_can(cart_dir, True, _files(files))


def journal_can_both(cart_dir, files=None):
    """(can undo, can redo) of one read."""
    bits = _native.journal_can(cart_dir, 2, _files(files))
    return (bool(bits & 1), bool(bits & 2))


def journal_compact(cart_dir):
    """The oldest entries dropped to the budget: how many."""
    return _native.journal_compact(cart_dir)


def journal_list(cart_dir, file=None):
    """The entries of `file` (None: every file), oldest first."""
    return _native.journal_list(cart_dir, file)


def journal_snap(cart_dir, seq):
    """Entry `seq`'s snapshot, checked as an undo checks it, or None."""
    return _native.journal_snap(cart_dir, seq)


def journal_restore(cart_dir, seq):
    """Entry `seq`'s snapshot published over its file and appended as a new
    commit: the new seq, or None when the file already held it or the snapshot
    is gone."""
    return _native.journal_restore(cart_dir, seq, _journal_ts())
