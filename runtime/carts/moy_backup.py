# Map (grep -n a name to jump there):
#   members          what an archive holds, as paths under the store's folder
#   archive          one call writes one archive
"""The format door's export: the store as one archive, written before a
format step (moy_seed.step_store, CLAUDE.md "A store format change goes
through ONE door"). A backup of a console is a copy of its whole folder, made
off the device (#127, tools/console_backup.py); this archive guards a step,
not a lost card.

An archive is a plain zip of the store's layout, written by the store's own
packer (native/moy_store/moy_pack.h's `moy_pack_paths`, a member of any size
streamed through one chunk), all under one top folder:

  backup.json        the manifest: {"format": "moybyte-backup-v1", "store":
                     the store's format stamp (moy_seed.STORE_FORMAT), "firmware":
                     the image's version or null, "carts": the carts folder's name}
  store.ver          the store's stamp
  carts/<cart>.moy   every cart that is not a built-in, and each built-in that
                     carries a kid's work: an edit's journal or a save
                     (pmem.bin) -- whole, journal included
  files/...          the user files, their trash and history; not Backups/,
                     where archives land
  apps/<id>/...      what each compiled cart wrote
  installed.json     the shelf's record of installed carts

Thumbnails are left out (the wire's rule, moy_store_skip); they are made
again. The archive lands in the user files' `Backups/`, where the USB drive
and SFTP reach it. Putting it back is unzipping its top folder over the
store's folder and booting: the door steps its older format again.
MicroPython-safe.
"""

import json

try:
    import os
except ImportError:  # pragma: no cover
    os = None

from moy_store_base import (APPS_DIR, CART_EXT, CARTS_DIR, FILES_DIR, _listing,
                            _sibling_path, builtin_ns, cart_path)
from moy_fs import _mkdir, _remove, _write
from moy_fs import _native as _store
import moy_seed

FORMAT = "moybyte-backup-v1"
MANIFEST = "backup.json"
TOP = "moybyte"
BACKUPS = "Backups"
NAME = "moybyte-backup.zip"
RECORD = "installed.json"
_JOURNAL = "journal"
_SAVES = "pmem.bin"


def _firmware():
    try:
        import moy_ota
        return moy_ota.FIRMWARE_VERSION
    except Exception:  # noqa: BLE001 -- the host and the web stage no OTA
        return None


def _kids(path):
    """Whether the built-in at `path` carries a kid's work: an edit's journal
    or a save. A tuning (config.json) alone is the seed's."""
    names = _listing(path) or {}
    return _JOURNAL in names or _SAVES in names


def backups_dir(root=CARTS_DIR):
    """Where an archive lands: the user files' Backups/."""
    return _sibling_path(root, FILES_DIR) + "/" + BACKUPS


def members(root=CARTS_DIR):
    """What an archive of the store at `root` holds, as paths under the
    store's folder (the carts folder's parent), in order."""
    carts = root.rstrip("/").rsplit("/", 1)[-1]
    out = [MANIFEST, moy_seed.STORE_VER_NAME]
    shelf = _listing(root) or {}
    for name in sorted(shelf):
        if not shelf[name] or not name.endswith(CART_EXT):
            continue
        if builtin_ns(name) and not _kids(cart_path(root, name)):
            continue
        out.append(carts + "/" + name)
    files = _listing(_sibling_path(root, FILES_DIR)) or {}
    out += [FILES_DIR + "/" + n for n in sorted(files) if n != BACKUPS]
    out += [APPS_DIR, RECORD]
    return out


def archive(root=CARTS_DIR, dest=None, name=NAME):
    """The store at `root` written as one archive: at `dest`, else `name` in
    backups_dir(). Returns (its path, the files in it). It is written beside
    its place and renamed over it, so a cut leaves the previous archive."""
    base = root.rstrip("/").rsplit("/", 1)[0]
    if dest is None:
        _mkdir(_sibling_path(root, FILES_DIR))
        _mkdir(backups_dir(root))
        dest = backups_dir(root) + "/" + name
    ver = moy_seed.load_store_version(root)
    man = {"format": FORMAT, "store": ver[0] if ver else moy_seed.STORE_FORMAT,
           "firmware": _firmware(), "carts": root.rstrip("/").rsplit("/", 1)[-1]}
    _write(base + "/" + MANIFEST, json.dumps(man))
    part = dest + ".part"
    try:
        n = _store.pack_paths(base, members(root), TOP, part, True)
    finally:
        _remove(base + "/" + MANIFEST)
    _remove(dest)
    os.rename(part, dest)
    return dest, n
