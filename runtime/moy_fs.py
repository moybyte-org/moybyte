# Crash-safe file primitives shared by the .moy store (moy_carts) and the
# undo/redo journal (moy_journal) -- extracted from moy_carts.py so the journal
# can be its own module without a circular import. MicroPython-safe (os only;
# no shutil). The atomic-write/.bak-recover discipline documented on
# _write_atomic/_read_recover is the ONE crash-safety story every durable
# store write in the console rides on.

try:
    import os
except ImportError:  # pragma: no cover
    os = None


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
    """Copy a file by read/write (no shutil on MicroPython). Used as the FAT
    rename-unsupported fallback so the destination is overwritten in place and
    the previous good file is never deleted ahead of a successful publish."""
    _write(dst, _read(src))


def _write_atomic(path, data):
    """Write `data` to `path` without ever leaving a truncated real file, and so
    that ANY crash mid-write is recoverable by load() (which falls back to .bak).

    Strategy (crash-safe, MicroPython/FAT friendly -- os.rename can't clobber an
    existing target on FAT, so we move the good file aside to .bak first):
      1. write the new bytes to `path.tmp`            (a crash here leaves path intact)
      2. rotate the current good file to `path.bak`   (path momentarily gone)
      3. rename `path.tmp` -> `path`                  (the atomic swap)
    If a crash lands between steps 2 and 3 there is NO `path`, but the previous
    good copy survives as `path.bak`, and load() restores from it.

    If os.rename is unsupported (some FAT VFS configs raise), we COPY `tmp`->`path`
    instead of renaming -- and we NEVER delete `path` before that copy publishes,
    so even a failed fallback leaves the last-known-good `path` (or its `.bak`)
    intact. A partial/failed `_write(tmp)` (e.g. ENOSPC) cleans up its own orphan
    `.tmp` before re-raising."""
    tmp = path + ".tmp"
    bak = path + ".bak"
    try:
        _write(tmp, data)             # full new file lands in tmp first
    except Exception:                 # noqa: BLE001 -- ENOSPC etc.: leave no orphan tmp
        _remove(tmp)
        raise
    if _exists(path):
        _remove(bak)                  # FAT rename won't overwrite -> clear stale bak
        try:
            os.rename(path, bak)      # keep the last-known-good copy aside
        except OSError:
            # rename unsupported: keep `path` until the new bytes are safely in
            # place -- do NOT delete it. Best-effort copy it to .bak for recovery.
            try:
                _copy(path, bak)
            except Exception:         # noqa: BLE001
                pass
    try:
        os.rename(tmp, path)          # atomic publish of the new contents
    except OSError:
        # rename(tmp -> path) unsupported: copy tmp over path (path is either gone,
        # in which case we recreate it, or still present, in which case we overwrite
        # in place), then drop the now-redundant tmp. `path` is never left missing
        # by a successful copy.
        _copy(tmp, path)
        _remove(tmp)


def _read_recover(path):
    """Read `path`; if it's missing but a `<path>.bak` sibling exists, RESTORE the
    cart from the backup (heal it on disk) and return that. This closes the
    _write_atomic crash window: a crash between `rename(path -> .bak)` and
    `rename(.tmp -> path)` leaves no `path`, only the previous good `.bak`; without
    this the cart would silently vanish from the gallery. Re-raises the original
    error if there's no usable backup."""
    try:
        return _read(path)
    except OSError:
        bak = path + ".bak"
        if _exists(bak):
            data = _read(bak)         # the last-known-good copy survived the crash
            try:
                _copy(bak, path)      # heal: republish it as the real file
            except Exception:         # noqa: BLE001 -- still return the recovered data
                pass
            return data
        raise
