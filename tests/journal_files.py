"""The undo journal's files as a test reads them: the format
runtime/carts/moy_journal.py describes and native/moy_store/moy_journal.c
writes, read back with no store call but the crash-safe readers."""

import json
import os


def paths(cart_dir):
    """(journal/, journal.jsonl, cursor.json, s/) of a cart folder."""
    jdir = cart_dir + "/journal"
    return jdir, jdir + "/journal.jsonl", jdir + "/cursor.json", jdir + "/s"


def entries(log_path):
    """The log's entries sorted by seq; a line that is not an entry is dropped."""
    out = []
    try:
        with open(log_path) as f:
            raw = f.read()
    except OSError:
        return out
    for line in raw.split("\n"):
        if not line.strip():
            continue
        try:
            e = json.loads(line)
        except ValueError:
            continue
        if isinstance(e, dict) and "seq" in e and "file" in e and "snap" in e:
            out.append(e)
    out.sort(key=lambda e: e["seq"])
    return out


def of(cart_dir):
    return entries(paths(cart_dir)[1])


def newest_by_file(ents):
    newest = {}
    for e in ents:
        newest[e["file"]] = e["seq"]
    return newest


def cursors(cur_path, ents):
    """The per-file cursor map: cursor.json's "cursors" (recovered from its
    backup when torn), backfilled with each file's newest entry."""
    from runtime.moy_fs import _read_recover
    default = newest_by_file(ents)
    try:
        data = json.loads(_read_recover(cur_path))
    except (OSError, ValueError, TypeError):
        return default
    raw = data.get("cursors") if isinstance(data, dict) else None
    if not isinstance(raw, dict):
        return default
    out = dict(default)
    for f, s in raw.items():
        if f in default and isinstance(s, int):
            out[f] = s
    return out


def cursor(cart_dir):
    """The scalar position: the largest applied seq across files."""
    _jdir, log, cur, _snap = paths(cart_dir)
    return max(cursors(cur, entries(log)).values() or [0])


def total_len(ents):
    return sum(int(e.get("len") or 0) for e in ents)


def current_snap(ents, at, file):
    """`file`'s snapshot path (relative to journal/) at cursor `at`, or None."""
    best = None
    for e in ents:
        if e["file"] == file and e["seq"] <= at:
            best = e
    return best["snap"] if best else None


def read_snap(jdir, entry):
    """An entry's snapshot text, or None when it is missing or does not match
    its recorded length (and, stamped, its crc)."""
    from runtime.moy_fs import _read_stamped
    exp = entry.get("len")
    p = jdir + "/" + entry["snap"]
    if entry.get("stamped"):
        data = _read_stamped(p)
    elif os.path.exists(p):
        with open(p) as f:
            data = f.read()
    else:
        data = None
    if data is None or (exp is not None and len(data) != int(exp)):
        return None
    return data
