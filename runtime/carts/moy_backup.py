# Map (grep -n a name to jump there):
#   members          what an archive holds, as paths under the store's folder
#   archive          one call writes one archive
#   restore          one call restores it
"""The store's backup (#127): a kid's work in one archive, and back.

An archive is a plain zip of the store's layout, written by the store's own
packer (native/moy_store/moy_pack.h's `moy_pack_paths`, a member of any size
streamed through one chunk), all under one top folder:

  backup.json        the manifest: {"format": "moybyte-backup-v1", "store":
                     the store's format stamp (moy_seed.STORE_FORMAT), "firmware":
                     the image's version or null, "carts": the carts folder's name}
  store.ver          the store's stamp
  carts/<cart>.moy   every cart that is not a built-in, and each built-in that
                     carries a kid's work: an edit's journal or a save
                     (pmem.json) -- whole, journal included
  files/...          the user files, their trash and history; not Backups/,
                     where archives land
  apps/<id>/...      what each compiled cart wrote
  installed.json     the shelf's record of installed carts

Thumbnails are left out (the wire's rule, moy_store_skip); they are made
again. The archive lands in the user files' `Backups/`, where the USB drive
and SFTP reach it. A restore unpacks into a stage beside the store, takes an
archive of an older format through the store's door (moy_seed.step_store),
then moves each member into place, a cart or an app folder replacing the
store's. The seed's rules hold after it: a restored built-in keeps its
manifest's version, so a seed at the same version leaves it as restored and a
newer one replaces its code and keeps its saves.

`tools/console_backup.py` pulls and pushes an archive from a host; the dev
channel's `backup` and `restore` words are these two calls.
MicroPython-safe.
"""

import json

try:
    import os
except ImportError:  # pragma: no cover
    os = None

from moy_store_base import (APPS_DIR, CART_EXT, CARTS_DIR, FILES_DIR, _listing, _rmtree,
                            _sibling_path, builtin_ns, cart_path)
from moy_fs import _exists, _mkdir, _read, _remove, _write
from moy_fs import _native as _store
import moy_seed

FORMAT = "moybyte-backup-v1"
MANIFEST = "backup.json"
TOP = "moybyte"
BACKUPS = "Backups"
NAME = "moybyte-backup.zip"
RECORD = "installed.json"
_JOURNAL = "journal"
_SAVES = "pmem.json"


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


def _manifest(stage):
    """The archive's manifest from its stage; ValueError for one that is not
    a backup, or is a newer store's."""
    try:
        man = json.loads(_read(stage + "/" + MANIFEST))
    except OSError:
        raise ValueError("not a backup: no manifest")
    if type(man) is not dict or man.get("format") != FORMAT:
        raise ValueError("not a backup")
    fmt, carts = man.get("store"), man.get("carts")
    if type(fmt) is not int or fmt < 1 or type(carts) is not str or not carts \
            or "/" in carts:
        raise ValueError("not a backup")
    if fmt > moy_seed.STORE_FORMAT:
        raise ValueError("a newer console's backup")
    return man


def _merge(src, dst):
    """Every file under `src` moved to the same place under `dst`, replacing
    what is there; folders made as needed."""
    _mkdir(dst)
    for name, is_dir in (_listing(src) or {}).items():
        s, d = src + "/" + name, dst + "/" + name
        if is_dir:
            _merge(s, d)
        else:
            _remove(d)
            os.rename(s, d)


def _place(src, dst):
    """The folder `src` in place of `dst`, whatever was there gone."""
    if _exists(dst):
        _store.adopt(src, dst)
    else:
        os.rename(src, dst)


def restore(path, root=CARTS_DIR):
    """The archive at `path` restored into the store at `root`. Returns the
    files it held. ValueError for an archive that is not a backup or is a
    newer console's; the store is untouched then."""
    base = root.rstrip("/").rsplit("/", 1)[0]
    _mkdir(base)
    _mkdir(root)
    _mkdir(_sibling_path(root, "install"))
    stage = _sibling_path(root, "install") + "/restore"
    _rmtree(stage)
    try:
        n, _top = _store.unpack(path, stage, True)
        man = _manifest(stage)
        sroot = stage + "/" + man["carts"]
        ver = moy_seed.load_store_version(sroot) or (man["store"], 0)
        if ver[0] < moy_seed.STORE_FORMAT:
            moy_seed.step_store(sroot, ver, None)
        for name, is_dir in (_listing(sroot) or {}).items():
            if is_dir and name.endswith(CART_EXT):
                _place(sroot + "/" + name, cart_path(root, name))
        sapps = stage + "/" + APPS_DIR
        _mkdir(_sibling_path(root, APPS_DIR))
        for name, is_dir in (_listing(sapps) or {}).items():
            if is_dir:
                _place(sapps + "/" + name, _sibling_path(root, APPS_DIR) + "/" + name)
        if _exists(stage + "/" + FILES_DIR):
            _merge(stage + "/" + FILES_DIR, _sibling_path(root, FILES_DIR))
        for name in (RECORD, moy_seed.STORE_VER_NAME):  # the store's own, if it has one
            if _exists(stage + "/" + name) and not _exists(_sibling_path(root, name)):
                os.rename(stage + "/" + name, _sibling_path(root, name))
    finally:
        _rmtree(stage)
    return n
