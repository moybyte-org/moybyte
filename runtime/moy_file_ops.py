"""What happens to a user file over its life (#108/#111): the per-file
op-history sidecars (keyframe + op segments under `files/.history/`), rename /
duplicate / delete-to-trash / restore over `moy_files`, the trash's bounded
prune, and the provenance stamp a copy carries back to its source.
`moy_carts` re-exports every name under its old spelling.
"""

import json

try:
    import os
except ImportError:  # pragma: no cover
    os = None

try:
    from moy_fs import (_copy, _exists, _forget_bak, _mkdir, _read, _remove, _write_atomic)
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime.moy_fs import (_copy, _exists, _forget_bak, _mkdir, _read, _remove, _write_atomic)
try:
    from moy_image import (cover_sig)
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime.moy_image import (cover_sig)
try:
    from moy_store_base import (CARTS_DIR, _is_dir, _rmtree, ensure_dirs)
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime.moy_store_base import (CARTS_DIR, _is_dir, _rmtree, ensure_dirs)
try:
    from moy_files import (FILE_KINDS, TRASH_DIR, TRASH_KEEP, _ensure_kind_dir, _item_ext, _kind_entries, _kind_spec, _slug_item, _unique_name, _whole_exts, file_path, files_root, project_folder)
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime.moy_files import (FILE_KINDS, TRASH_DIR, TRASH_KEEP, _ensure_kind_dir, _item_ext, _kind_entries, _kind_spec, _slug_item, _unique_name, _whole_exts, file_path, files_root, project_folder)


# --- op-history sidecars (#111): keyframe + op segments per user file --------
#
# The #111 keyframe+ops undo model for the document surfaces (Paint, the
# editor handle). A
# per-file history lives in a HIDDEN sibling of the kind dirs --
# files/.history/<kind>/<name>.jsonl -- one append-only JSONL of records:
#
#   {"t":"kf","doc": <snapshot blob>}   a full keyframe (the replay base). Comes
#                                       from an op_history.History.keyframe().
#   {"t":"seg","ops": [ ... ]}          a batch of fine-grained ops (History.flush())
#                                       that transforms the previous keyframe forward.
#
# CADENCE mirrors the journal (#7): a raw open(path,"a") per record -- O(1), never
# _write_atomic -- flushed on the SAME #108 autosave debounce as the file itself,
# so nothing writes per-stroke (the pmem SD lesson, #66). A torn last line fails
# json.loads and is dropped at load, exactly like journal.jsonl. Pruned to the
# newest keyframe + the last HISTORY_KEEP segments (History forces a fresh keyframe
# every <=256 ops, so segments never grow unbounded). The .history dir is a SIBLING
# of the kind dirs, NOT a kind -- list_files/trash_list/FileGridView are all
# registry-driven (they scan files/<kind>, never files/), so it is invisible by
# construction, and _kind_spec(".history") is a loud ValueError.

HISTORY_DIR = ".history"
HISTORY_EXT = ".jsonl"
HISTORY_KEEP = 32          # keep the newest keyframe + this many trailing op-segments


def _history_dir(kind, root):
    _kind_spec(kind)                          # validate -- ".history" is never a kind
    return files_root(root) + "/" + HISTORY_DIR + "/" + kind


def _history_path(kind, name, root):
    return _history_dir(kind, root) + "/" + name + HISTORY_EXT


def _history_trash_dir(kind, root):
    _kind_spec(kind)
    return files_root(root) + "/" + TRASH_DIR + "/" + HISTORY_DIR + "/" + kind


def _history_trash_path(kind, name, root):
    return _history_trash_dir(kind, root) + "/" + name + HISTORY_EXT


def _ensure_history_dir(kind, root):
    ensure_dirs(root)
    _mkdir(files_root(root))
    _mkdir(files_root(root) + "/" + HISTORY_DIR)
    d = _history_dir(kind, root)
    _mkdir(d)
    return d


def _ensure_history_trash_dir(kind, root):
    _mkdir(files_root(root))
    _mkdir(files_root(root) + "/" + TRASH_DIR)
    _mkdir(files_root(root) + "/" + TRASH_DIR + "/" + HISTORY_DIR)
    d = _history_trash_dir(kind, root)
    _mkdir(d)
    return d


def _sidecar_move(src, dst):
    """Move a history sidecar to follow its file (rename/trash/restore). A
    best-effort no-op when the file was never edited under op-history (no
    sidecar). The dst's dir must already exist (callers ensure it)."""
    if not _exists(src):
        return
    try:
        os.rename(src, dst)
    except OSError:
        _copy(src, dst)
        _remove(src)


def _sidecar_copy(src, dst):
    """Copy a history sidecar alongside a duplicated file (best-effort)."""
    if not _exists(src):
        return
    _copy(src, dst)


def history_path(kind, name, root=CARTS_DIR):
    """The op-history sidecar path for a user file (phase 2/3 read this)."""
    return _history_path(kind, name, root)


def load_history(kind, name, root=CARTS_DIR):
    """Parse a file's history sidecar into a list of records (keyframes +
    segments) in file order. A torn/corrupt line is DROPPED (append-only's only
    failure mode), every good record before it survives; a missing sidecar -> [].

    A PROJECT kind has no sidecar by design (see PROJECT_KIND): its durable undo
    is the cart's own journal, so the handle opens with an empty seed and
    records live ops from there."""
    if project_folder(kind):
        return []
    _kind_spec(kind)
    out = []
    try:
        raw = _read(_history_path(kind, name, root))
    except OSError:
        return out
    for line in raw.split("\n"):
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
        except ValueError:
            continue                          # torn / corrupt line -> drop, keep the rest
        if isinstance(rec, dict) and rec.get("t") in ("kf", "seg"):
            out.append(rec)
    return out


def _last_keyframe(recs):
    """Index of the newest "kf" record in `recs`, or -1 when there is none."""
    last = -1
    for i in range(len(recs)):
        if recs[i].get("t") == "kf":
            last = i
    return last


def ops_since_keyframe(recs):
    """Every op recorded AFTER the last keyframe in `recs` (a load_history()
    list), flattened oldest..newest -- the window prune_history keeps on disk,
    and the seed for an op_history.History undo stack. A keyframe supersedes
    the records before it, so a reader that skips this scan seeds ops the
    keyframe already accounts for and over-counts History's keyframe cadence."""
    recs = recs or []
    ops = []
    for rec in recs[_last_keyframe(recs) + 1:]:
        if rec.get("t") == "seg":
            ops.extend(rec.get("ops") or [])
    return ops


def history_write_keyframe(kind, name, doc_blob, root=CARTS_DIR):
    """Append a full keyframe record (the replay base) and prune. `doc_blob` is
    a JSON-able snapshot (an op_history.History.keyframe())."""
    _ensure_history_dir(kind, root)
    with open(_history_path(kind, name, root), "a") as f:   # RAW append -- O(1)
        f.write(json.dumps({"t": "kf", "doc": doc_blob}) + "\n")
    prune_history(kind, name, root)


def history_append_segment(kind, name, ops, root=CARTS_DIR):
    """Append one op-segment record (a History.flush() batch) and prune. An empty
    batch writes nothing (a debounce that fires with no ops must not touch SD)."""
    if not ops:
        return
    _ensure_history_dir(kind, root)
    with open(_history_path(kind, name, root), "a") as f:   # RAW append -- O(1)
        f.write(json.dumps({"t": "seg", "ops": list(ops)}) + "\n")
    prune_history(kind, name, root)


_PRUNE_FAILS = 0


def history_prune_fails():
    """Sidecar prunes that failed since boot. Must stay 0. history_commit
    swallows the failure to keep the commit honest, so this is the only place
    a store that has stopped pruning is visible."""
    return _PRUNE_FAILS


def history_commit(kind, name, ops, keyframe=None, root=CARTS_DIR):
    """The one-call adapter for op_history: at the #108 autosave debounce a Desk
    Lab app passes History.flush() as `ops` and, when History.needs_keyframe(),
    History.keyframe() as `keyframe`. Writes the keyframe first (so it precedes
    the segment it bases), then the segment, then prunes once. A pure no-op
    (no keyframe, empty ops) never touches SD.

    The APPEND is the commit; the prune is housekeeping. A failed prune is
    returned, never raised: raising made the caller skip mark_keyframe() for a
    keyframe that was already on disk, so every later flush wrote another one.
    Leaving records unpruned is safe because ops_since_keyframe reads only the
    window after the last keyframe."""
    if project_folder(kind):
        return None            # journaled by save_project_file -- see PROJECT_KIND
    if keyframe is None and not ops:
        return None
    _ensure_history_dir(kind, root)
    path = _history_path(kind, name, root)
    with open(path, "a") as f:
        if keyframe is not None:
            f.write(json.dumps({"t": "kf", "doc": keyframe}) + "\n")
        if ops:
            f.write(json.dumps({"t": "seg", "ops": list(ops)}) + "\n")
    try:
        prune_history(kind, name, root)
    except (OSError, ValueError) as exc:
        global _PRUNE_FAILS
        _PRUNE_FAILS += 1
        return str(exc)
    return None


def prune_history(kind, name, root=CARTS_DIR, keep=HISTORY_KEEP):
    """Keep the newest keyframe and the last `keep` op-segments after it; drop
    everything older (the keyframe supersedes the records before it). A full
    rewrite, so it rides _write_atomic -- but it is O(records) and rare (only
    when a sidecar exceeds keep+1), NOT on the per-record append path (like
    journal_compact). No-op when nothing needs dropping."""
    recs = load_history(kind, name, root)
    if not recs:
        return 0
    last_kf = _last_keyframe(recs)
    if last_kf >= 0:
        head = [recs[last_kf]]
        segs = [r for r in recs[last_kf + 1:] if r.get("t") == "seg"]
    else:
        head = []                             # no keyframe yet -> just cap the segments
        segs = [r for r in recs if r.get("t") == "seg"]
    kept = head + (segs[-keep:] if keep and len(segs) > keep else segs)
    if len(kept) == len(recs):
        return 0                              # nothing to drop
    _write_atomic(_history_path(kind, name, root),
                  "".join(json.dumps(r) + "\n" for r in kept))
    return len(recs) - len(kept)


def clear_history(kind, name, root=CARTS_DIR):
    """Drop a file's history sidecar entirely (a hard reset / the file is gone
    forever). Best-effort; a missing sidecar is a no-op."""
    _kind_spec(kind)
    _remove(_history_path(kind, name, root))


def rename_file(kind, name, new_title, root=CARTS_DIR):
    """Rename an item to (the slug of) `new_title`, unique-ified against the
    kind's dir. Returns the final name (a contentless or unchanged title is a
    no-op -- slug()'s "cart" fallback must never fire from a rename). The op-
    history sidecar (#111) moves with the file."""
    for ch in str(new_title):
        if ch.isalpha() or ch.isdigit():
            break
    else:
        return name
    new = _slug_item(kind, new_title)
    if new == name:
        return name
    new = _unique_name(kind, new, root)
    os.rename(file_path(kind, name, root), file_path(kind, new, root))
    _forget_bak(file_path(kind, name, root))   # the old name's crash backup, #154
    _ensure_history_dir(kind, root)
    _sidecar_move(_history_path(kind, name, root), _history_path(kind, new, root))
    return new


def _copytree(src, dst):
    _mkdir(dst)
    for n in os.listdir(src):
        s = src + "/" + n
        d = dst + "/" + n
        if _is_dir(s):
            _copytree(s, d)
        else:
            _copy(s, d)


def duplicate_file(kind, name, root=CARTS_DIR):
    """Copy an item to the next free name_2/name_3 slot; returns the new name.
    The op-history sidecar (#111) is copied alongside it, so a duplicate opens
    with its source's undo history intact."""
    folder_valued = _kind_spec(kind)[1]
    new = _unique_name(kind, name, root)   # the source exists, so this yields name_2, name_3, ...
    src = file_path(kind, name, root)
    if folder_valued:
        _copytree(src, file_path(kind, new, root))
    else:
        _write_atomic(file_path(kind, new, root), _read(src))
    _ensure_history_dir(kind, root)
    _sidecar_copy(_history_path(kind, name, root), _history_path(kind, new, root))
    return new


def _trash_dir(kind, root):
    return files_root(root) + "/" + TRASH_DIR + "/" + kind


def _trash_path(kind, name, root):
    return _trash_dir(kind, root) + "/" + name + _item_ext(kind, name)


def delete_file(kind, name, root=CARTS_DIR):
    """Move an item to files/trash/<kind>/ (never destroy -- trash trains
    recovery, confirms train click-through), then prune the trash's oldest
    entries beyond TRASH_KEEP. Returns the name it holds in the trash."""
    _kind_spec(kind)
    _mkdir(files_root(root))
    _mkdir(files_root(root) + "/" + TRASH_DIR)
    _mkdir(_trash_dir(kind, root))
    new = _unique_name(kind, name, root, _trash_path)
    os.rename(file_path(kind, name, root), _trash_path(kind, new, root))
    _forget_bak(file_path(kind, name, root))   # the backup does not follow it, #154
    # The op-history sidecar (#111) follows the file into the trash under the
    # SAME trashed name, so a restore brings the undo history back with it.
    _ensure_history_trash_dir(kind, root)
    _sidecar_move(_history_path(kind, name, root), _history_trash_path(kind, new, root))
    prune_trash(root)
    return new


def trash_list(root=CARTS_DIR):
    """Every trashed item as (kind, name), newest first across kinds."""
    out = []
    for kind in FILE_KINDS:
        ext, folder_valued, _base = FILE_KINDS[kind]
        # `_whole_exts` here too, or a trashed `todo.txt` (or a script) is
        # invisible in the trash and can never be restored: the listing is what
        # `restore_file` is offered from.
        for n, m in _kind_entries(_trash_dir(kind, root), ext, folder_valued,
                                  _whole_exts(kind)):
            out.append((kind, n, m))
    out.sort(key=lambda e: (-e[2], e[0], e[1]))
    return [(k, n) for k, n, _m in out]


def restore_file(kind, name, root=CARTS_DIR):
    """Move a trashed item back into its kind dir (unique-ified against what
    was made since). Returns the restored name."""
    _ensure_kind_dir(kind, root)
    new = _unique_name(kind, name, root)
    os.rename(_trash_path(kind, name, root), file_path(kind, new, root))
    _forget_bak(_trash_path(kind, name, root))
    # Bring the op-history sidecar (#111) back out of the trash with the file.
    _ensure_history_dir(kind, root)
    _sidecar_move(_history_trash_path(kind, name, root), _history_path(kind, new, root))
    return new


def _remove_trash_entry(kind, name, root):
    p = _trash_path(kind, name, root)
    if _is_dir(p):
        _rmtree(p)
    else:
        _remove(p)
    _forget_bak(p)
    _remove(_history_trash_path(kind, name, root))   # drop the sidecar too (#111)


def prune_trash(root=CARTS_DIR, keep=TRASH_KEEP):
    """Drop the trash's oldest entries beyond `keep` (mtime best-effort -- the
    quota-pressure half of the trash story; there is no wall-clock retention
    because the device RTC may never be set). A cheap listdir count gates the
    stat+sort pass, so the every-delete call usually costs six listdirs."""
    total = 0
    for kind in FILE_KINDS:
        try:
            total += len(os.listdir(_trash_dir(kind, root)))
        except OSError:
            pass
    if total <= keep:
        return
    for kind, name in trash_list(root)[keep:]:
        _remove_trash_entry(kind, name, root)


def empty_trash(root=CARTS_DIR):
    prune_trash(root, keep=0)


# --- provenance stamps (#108 phase 2): a copy remembers its source ----------
#
# When a user file is COPIED into a consuming cart (a drawing -> a project's
# images/bg, or the wallpaper copy), the copied JSON blob gains two optional
# keys: `src` ("<kind>/<name>", the origin file) and `sig` (a content signature
# of the source blob at copy time -- the cover_sig stamp pattern from #86).
# PURE METADATA: never resolved at runtime, ignored by every decoder (they read
# only format/w/h/data/cells/body). It powers two PULL-BASED affordances --
# "your drawing changed -> UPDATE" (re-read the source; a differing sig offers a
# one-tap re-copy; a missing/renamed source simply never matches, so the
# affordance vanishes) and the File Manager's "used in:" list (scan the
# consumers for a matching src). No reverse index is kept, so a stale/deleted
# source can never break anything.

def content_sig(text):
    """A cheap content stamp for a user-file blob (reuses the #86 cover_sig)."""
    return cover_sig(text) if text else 0


def stamp_provenance(blob, kind, name, sig):
    """Return `blob` (a JSON object string) with src/sig provenance keys added.
    A non-object / unparseable blob passes through unchanged (never a crash)."""
    try:
        data = json.loads(blob)
    except (ValueError, TypeError):
        return blob
    if not isinstance(data, dict):
        return blob
    data["src"] = str(kind) + "/" + str(name)
    data["sig"] = int(sig) & 0xFFFFFFFF
    return json.dumps(data)


def read_provenance(blob):
    """(src, sig) from a stamped blob -- ("<kind>/<name>", int) -- or
    (None, None) when there is no stamp / the blob is unreadable."""
    try:
        data = json.loads(blob)
    except (ValueError, TypeError):
        return (None, None)
    if not isinstance(data, dict):
        return (None, None)
    src = data.get("src")
    if not isinstance(src, str) or "/" not in src:
        return (None, None)
    try:
        sig = int(data.get("sig", 0))
    except (TypeError, ValueError):
        sig = 0
    return (src, sig)
