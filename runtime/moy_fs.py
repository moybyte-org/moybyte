# Crash-safe file primitives shared by the .moy store (moy_carts) and the
# undo/redo journal (moy_journal) -- extracted from moy_carts.py so the journal
# can be its own module without a circular import. MicroPython-safe (os +
# binascii only; no shutil).
#
# THE CRASH-SAFETY STORY (the ONE every durable store write in the console rides
# on; #154). A save is THREE writes and nothing else -- no stat, no remove, no
# rename:
#
#   1. `<root>/.publish` <- ONE line naming the file about to be published and
#      its stamp: "#moyfs1 <chars> <crc32> <path>\n". One marker per store root,
#      overwritten in place, never cleared -- the next save overwrites it. Only
#      one publish is ever in flight, so one line says everything.
#   2. `<path>.bak`      <- a STAMP LINE ("#moyfs1 <chars> <crc32>\n") + the new
#      text. The stamp describes the text that follows it, so the backup can say
#      whether it is whole.
#   3. `<path>`          <- the new text, overwritten in place, byte for byte
#      what every other reader in the system expects. Nothing is ever added to
#      the published file: a cart's main.py, a manifest, a sprite blob are
#      exactly their own bytes, so the store scan, the web sync RPC and tools/
#      see no header and no trailer.
#
# WHY THE MARKER EXISTS, AND IT IS NOT BELT AND BRACES. Without it the reader has
# to open `<path>.bak` on EVERY read to learn whether the file beside it is whole
# -- and on littlefs a path lookup is the expensive op (#198: 98 block reads), so
# a P4 boot, which reads every manifest in the store through `_read_recover`,
# measured +6.0s of boot-to-desk and +25% of store scan for it (75 carts). The
# marker turns that per-file lookup into ONE read per process: the reader loads
# the line lazily, and a path the line does not name is returned with no extra
# lookup at all. The T-Deck's FAT never showed the cost -- two reads per lookup
# there -- which is exactly why this is a marker and not a per-board tune.
#
# The `.bak` is a redo log for ONE failure, and `_read_recover` is deliberately
# narrow about which. For the file the marker names, it asks whether the
# published text is a strict PREFIX of the backup's payload -- because that is
# exactly what an interrupted publish leaves: FAT truncates on open-for-write and
# then grows the file, so a power loss lands it somewhere between empty and
# whole, and never anywhere else. Only then does the backup win. A power loss
# lands in one of these places:
#
#   * mid step 1  -- the marker is torn, so it does not parse (the line must end
#                    in a newline) and names nothing; on littlefs it is
#                    copy-on-write and still names the PREVIOUS publish, which
#                    completed. Either way nothing else was touched: `path` holds
#                    the previous save, whole, and the reader trusts it.
#   * mid step 2  -- the marker names `path`, `path` still holds the previous
#                    save and the backup fails its own stamp. A torn backup is
#                    refused rather than published, so the reader keeps `path`.
#   * mid step 3  -- `path` is a prefix of the backup's payload. The reader
#                    republishes the backup, and the save completes. This is the
#                    window the old rename dance could not see: `_read_recover`
#                    used to fall back only when `path` was MISSING, so a short
#                    file read as good.
#   * between 2+3 -- the backup is stamped and whole, `path` still holds the
#                    previous save, which is generally not a prefix of the new
#                    one. THE PREVIOUS SAVE IS WHAT SURVIVES -- the same
#                    guarantee the pre-#154 rename dance gave, and the same one
#                    littlefs gives on its own (it is copy-on-write, so an
#                    interrupted publish there leaves the old file rather than a
#                    torn one, which is this case and not the one above). The
#                    redo log's value is the FAT torn write, which nothing else
#                    can see.
#
# The marker is best-effort at the writing end: if it cannot be written the save
# still goes through, one save without torn-write detection rather than a save
# refused on a medium that is already failing. With NO root registered
# (`set_publish_root`), the reader falls back to opening `<path>.bak` per read --
# which is the pre-marker behaviour, correct and slower, and what a bare
# `moy_journal` on a tmp dir gets.
#
# Anything else at `path` was put there DELIBERATELY by someone that is not this
# module -- `tools/push_cart.py` places a file with remove+rename over the dev
# channel, and a board's kid may have saved that same file first. Reverting a
# push to a stale backup would be a silent undo of the developer's write, so the
# reader trusts `path` and drops the stale `.bak` on the spot. That is also why
# a same-length wrong-content publish is not recoverable: it is indistinguishable
# from a foreign write, and guessing wrong costs more than it saves.
#
# A torn `.bak` is refused rather than published -- garbage never overwrites a
# whole file. When the stamp is absent entirely the file is a LEGACY backup (the
# pre-#154 rename dance, or `moy_sync._publish`'s rotation) and is read as raw
# text, which is what those writers left behind.
#
# `_forget_bak` is the same drop, done by the writer rather than the next reader:
# a foreign writer that calls it leaves nothing stale behind at all.

try:
    import os
except ImportError:  # pragma: no cover
    os = None

try:
    from binascii import crc32 as _crc32
except ImportError:  # pragma: no cover -- every target ships binascii
    def _crc32(data, seed=0):
        h = seed
        for b in data:
            h = (h * 31 + b) & 0xFFFFFFFF
        return h


_STAMP = "#moyfs1 "
# The stamp is folded over slices rather than one `data.encode()` so a save never
# needs a second full-size buffer beside the text it is already holding.
_CRC_CHUNK = 2048
_PUBLISH = ".publish"
# Registered store roots, longest first (longest prefix wins, so a root nested
# inside another resolves to the inner one). Bounded because a host test session
# registers one per tmp store and `_root_for` walks the list on every recovered
# read; a board has one or two. Falling off the end costs the per-file check, not
# correctness.
_ROOT_MAX = 8
_roots = []
# root -> the marker's (path, chars, crc), or None for "nothing in flight here".
# A root absent from this dict has not been read yet; present means read (or
# written) by us, so the marker file is opened at most once per root per process.
_marks = {}


def _mkdir(path):
    try:
        os.mkdir(path)
    except OSError:
        pass


def _exists(path):
    try:
        os.stat(path)
        return True
    except OSError:
        return False


def _read(path):
    with open(path, "r") as f:
        return f.read()


def _write(path, data):
    with open(path, "w") as f:
        f.write(data)


def _remove(path):
    try:
        os.remove(path)
    except OSError:
        pass


def _copy(src, dst):
    """Copy a file by read/write (no shutil on MicroPython). Overwrites the
    destination in place, so the previous good file is never deleted ahead of a
    successful copy."""
    _write(dst, _read(src))


def _text_crc(text):
    crc = 0
    for i in range(0, len(text), _CRC_CHUNK):
        crc = _crc32(text[i:i + _CRC_CHUNK].encode("utf-8"), crc)
    return crc & 0xFFFFFFFF


def _stamp_of(text):
    return len(text), _text_crc(text)


def _stamp_line(text):
    n, crc = _stamp_of(text)
    return "%s%d %d\n" % (_STAMP, n, crc)


# -- the store-wide publish marker (#154) -----------------------------------

def set_publish_root(root):
    """Register a store root, so `_read_recover` under it costs no extra lookup.
    Idempotent; the store's dir setup calls it. Everything below a registered
    root -- cart folders, their journals, the sibling stores -- shares its one
    marker, because only one publish is ever in flight."""
    if not root:
        return
    root = root.rstrip("/")
    if root in _roots:
        _roots.remove(root)
    _roots.insert(0, root)
    _roots.sort(key=len, reverse=True)
    while len(_roots) > _ROOT_MAX:
        _marks.pop(_roots.pop(), None)


def _root_for(path):
    for r in _roots:
        if path.startswith(r + "/"):
            return r
    return None


def _parse_mark(line):
    """(path, chars, crc) from a marker line, or None when it isn't one. A torn
    marker has no trailing newline and so can never parse as naming a file."""
    if not line.startswith(_STAMP) or not line.endswith("\n"):
        return None
    bits = line[len(_STAMP):-1].split(" ", 2)
    if len(bits) != 3 or not bits[2]:
        return None
    try:
        return bits[2], int(bits[0]), int(bits[1])
    except ValueError:
        return None


def _mark(root):
    """The marker under `root`, read at most once per process."""
    try:
        return _marks[root]
    except KeyError:
        pass
    m = None
    try:
        with open(root + "/" + _PUBLISH, "r") as f:
            m = _parse_mark(f.readline())
    except OSError:                   # no marker: nothing was ever in flight here
        pass
    _marks[root] = m
    return m


def _unmark(path):
    """Forget the in-RAM marker for `path`'s root once its one question has been
    answered, so the rest of this process reads that root for free."""
    root = _root_for(path)
    if root is not None:
        _marks[root] = None


def _stamp_for(path):
    """What the published `path` should look like, or None for "nothing says".
    Under a registered root this is a dict lookup; outside one it falls back to
    opening the backup, which is what costs a path lookup per read."""
    root = _root_for(path)
    if root is None:
        return _bak_stamp(path)
    m = _mark(root)
    if m is None or m[0] != path:
        return None
    return m[1], m[2]


def _parse_stamp(line):
    """(chars, crc) from a backup's first line, or None when it isn't one."""
    if not line.startswith(_STAMP) or not line.endswith("\n"):
        return None
    bits = line[len(_STAMP):-1].split(" ")
    if len(bits) != 2:
        return None
    try:
        return int(bits[0]), int(bits[1])
    except ValueError:
        return None


def _fits(text, stamp):
    return len(text) == stamp[0] and _text_crc(text) == stamp[1]


def _bak_stamp(path):
    """The stamp beside `path`, or None when there is no stamped backup. Reads the
    one line, never the payload -- this runs on every recovered read."""
    try:
        with open(path + ".bak", "r") as f:
            return _parse_stamp(f.readline())
    except OSError:
        return None


def _read_bak(path):
    """The backup's text, or None when it cannot be trusted.

    A stamped backup is returned only if it matches its own stamp (a torn one is
    refused). An UNSTAMPED backup is legacy -- the pre-#154 rename rotation, or
    `moy_sync._publish`'s -- and is returned whole, which is all those writers
    ever promised."""
    try:
        with open(path + ".bak", "r") as f:
            head = f.readline()
            rest = f.read()
    except OSError:
        return None
    stamp = _parse_stamp(head)
    if stamp is None:
        return head + rest
    if _fits(rest, stamp):
        return rest
    return None                       # torn backup: refuse it, never publish garbage


def _write_atomic(path, data):
    """Write `data` to `path` so that any crash mid-write is recoverable by
    `_read_recover`. Three writes, no stat/remove/rename -- see the module
    docstring for the crash-safety argument and the invariant it asks of other
    writers."""
    bak = path + ".bak"
    n, crc = _stamp_of(data)
    root = _root_for(path)
    if root is not None:
        try:
            _write(root + "/" + _PUBLISH,
                   "%s%d %d %s\n" % (_STAMP, n, crc, path))
        except Exception:             # noqa: BLE001 -- best effort, see the docstring
            _marks[root] = None       # this save publishes without detection
        else:
            _marks[root] = (path, n, crc)
    try:
        with open(bak, "w") as f:     # the redo log, stamped
            f.write("%s%d %d\n" % (_STAMP, n, crc))
            f.write(data)
    except Exception:                 # noqa: BLE001 -- ENOSPC etc.
        _remove(bak)                  # free the half-written backup; `path` is untouched
        raise
    _write(path, data)                # publish in place


def _forget_bak(path):
    """Drop the backup beside `path`. For a writer that publishes different bytes
    without going through `_write_atomic`: a stamp left describing content that is
    no longer there would make the next read 'recover' over the new file."""
    _remove(path + ".bak")


def _heal(path, data):
    """Republish recovered bytes so the recovery is paid once rather than on every
    read. Best-effort: a read-only or full medium must not turn a read that DID
    recover into a failure."""
    try:
        _write(path, data)
    except Exception:                 # noqa: BLE001
        pass
    return data


def _read_recover(path):
    """Read `path`, healing it from `<path>.bak` when the published file is
    missing or is a half-written publish. Re-raises the original error if there is
    no usable backup. See the module docstring for why a mismatch that is NOT a
    truncation is read as a foreign write rather than as damage."""
    try:
        data = _read(path)
    except OSError:
        rec = _read_bak(path)         # never published, or lost after the backup landed
        if rec is None:
            raise
        return _heal(path, rec)
    stamp = _stamp_for(path)
    if stamp is None or _fits(data, stamp):
        return data                   # nothing in flight here, or it checks out
    rec = _read_bak(path)
    if rec is None:
        _unmark(path)                 # a torn backup -- keep the file we can read
        return data
    if rec.startswith(data):
        return _heal(path, rec)       # a truncated publish: finish it
    _forget_bak(path)                 # someone else published here; the stamp is stale
    _unmark(path)
    return data
