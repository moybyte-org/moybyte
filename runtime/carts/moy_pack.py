# Map (grep -n a name to jump there):
#   save_package            a theme package's folder, its files written whole
#   rename_package          a theme package's folder moved by one rename
#   pack                    a cart folder -> a .moy archive
#   unpack                  a .moy archive -> a folder
#   adopt                   a staged folder made the target, the old one set aside
#   import_archive          a .moy archive installed as a cart or a theme
"""A cart as it travels (#127, #122): the `.moy` archive it is packed into
and unpacked from, the staged folder adopted in place of the one it
replaces, an archive imported as a cart or a theme, and a theme package's
folder written and renamed. Where the native store is linked
(native/moy_store/moy_pack.c) it does the work; these are its twins.

moy_carts re-exports every name here, so its callers are unchanged. A leaf
over moy_store_base and moy_fs; MicroPython-safe.
"""

import json

try:
    import os
except ImportError:  # pragma: no cover
    os = None

from moy_store_base import (CARTS_DIR, CART_EXT, THEME_NAME_MAX, USER_NS, _listing,
                            _rmtree, _sibling_path, cart_path, theme_name_ok)
from moy_fs import (_exists, _mkdir, _read, _read_bytes, _remove, _write,
                    _write_bytes)
from moy_fs import _native as _store
if _store is not None and not hasattr(_store, "catalogue"):
    _store = None              # the same native store moy_carts reaches


def save_package(path, files, remove=(), fresh=False):
    """Publish a theme package's folder (`custom`, docs/history/theming_2026-09.md
    §9 phase 6 step 5): each of `files` (name -> bytes) written whole, each
    of `remove` deleted. `fresh` empties the folder first, so a package
    replaced by another keeps nothing of the one before."""
    if fresh:
        _rmtree(path)
    _mkdir(path)
    for name in files:
        _write_bytes(path + "/" + name, files[name])
    for name in remove:
        _remove(path + "/" + name)


def rename_package(path, dest, files, replace=False):
    """A theme package's folder `path` moved to `dest` by one rename (NAME…,
    docs/history/theming_2026-09.md §9 phase 6 step 8), then each of `files` written
    whole into it. A folder at `dest` is replaced only with `replace`
    (`adopt`'s aside-and-remove), else OSError(EEXIST)."""
    if _exists(dest):
        if not replace:
            raise OSError(17, "exists")
        adopt(path, dest)
    else:
        os.rename(path, dest)
    for name in files:
        _write_bytes(dest + "/" + name, files[name])


# --- a cart as it travels (#127, #122): the archive and adopt ---------------
#
# A `.moy` archive, the browser's export and import too (its VM runs these):
# STORED entries under `<folder>/`, a fixed 1980-01-01 date,
# files in name order at most six folders deep, nothing the wire skips (the
# journal too, unless `history`). The native store writes and reads it
# wherever it is linked (native/moy_store/moy_pack.c); these are the twins.

_PACK_DEPTH = 6
_SKIP_DIRS = ("thumbs", "__pycache__", "journal")
_SKIP_FILES = ("journal.jsonl",)


def pack_skip(name, history=False):
    """`moy_sync._skip`'s rule (journal/ and journal.jsonl let through with
    `history`): what never crosses the wire or goes into an archive."""
    if _store is not None:
        return _store.skip(name, history)
    if name in ("thumbs", "__pycache__") or (not history and (
            name in _SKIP_DIRS or name in _SKIP_FILES)):
        return True
    return name.endswith(".bak") or name.endswith(".tmp")


def pack(path, dest, history=False, folder=None):
    """The cart folder at `path` written to `dest` as a `.moy` archive whose
    entries sit under `folder` (the cart's own folder name). Returns the
    number of files."""
    if folder is None:
        folder = path.rstrip("/").rsplit("/", 1)[-1]
    if _store is not None:
        return _store.pack(path, folder, dest, history)
    import struct
    import binascii
    out = []
    cen = []
    at = [0]

    def emit(b):
        out.append(b)
        at[0] += len(b)

    def walk(p, rel, depth):
        if depth > _PACK_DEPTH:
            return
        names = _listing(p)
        for n in sorted(names or ()):
            if pack_skip(n, history):
                continue
            if names[n]:
                walk(p + "/" + n, rel + "/" + n, depth + 1)
                continue
            try:
                data = _read_bytes(p + "/" + n, 1 << 30)
            except OSError:
                continue
            name = (rel + "/" + n).encode("utf-8")
            crc = binascii.crc32(data) & 0xFFFFFFFF
            cen.append((name, crc, len(data), at[0]))
            emit(struct.pack("<IHHHHHIIIHH", 0x04034B50, 20, 0, 0, 0, 0x21, crc,
                             len(data), len(data), len(name), 0) + name + data)

    walk(path, folder, 0)
    start = at[0]
    for name, crc, size, off in cen:
        emit(struct.pack("<IHHHHHHIIIHHHHHII", 0x02014B50, 20, 20, 0, 0, 0, 0x21,
                         crc, size, size, len(name), 0, 0, 0, 0, 0, off) + name)
    emit(struct.pack("<IHHHHIIH", 0x06054B50, 0, 0, len(cen), len(cen),
                     at[0] - start, start, 0))
    _write_bytes(dest, b"".join(out))
    return len(cen)


def unpack(archive, dest):
    """The `.moy` archive at `archive` unpacked into the folder `dest`, the one
    top folder its entries sit under stripped and any path that climbs or
    names what the wire skips refused: (files, top folder or "")."""
    if _store is not None:
        return _store.unpack(archive, dest)
    import io
    try:
        import zipfile          # CPython; a board reads through the native store
    except ImportError:  # pragma: no cover
        zipfile = None
    if zipfile is None:  # pragma: no cover
        raise OSError(19, "no archive reader")
    with open(archive, "rb") as f:
        z = zipfile.ZipFile(io.BytesIO(f.read()))
    names = [i for i in z.infolist() if not i.filename.endswith("/")]
    tops = set(i.filename.split("/", 1)[0] if "/" in i.filename else None
               for i in names)
    top = tops.pop() if len(tops) == 1 and None not in tops else ""
    _mkdir(dest)
    n = 0
    for i in names:
        rel = i.filename[len(top) + 1:] if top else i.filename
        segs = rel.replace("\\", "/").split("/")
        if (not rel or rel[0] in "/\\"
                or any(s in ("", ".", "..") or pack_skip(s) for s in segs)):
            continue
        for k in range(1, len(segs)):
            _mkdir(dest + "/" + "/".join(segs[:k]))
        try:
            data = z.read(i)
        except Exception:  # noqa: BLE001 -- a damaged entry is left out
            continue
        with open(dest + "/" + rel, "wb") as f:
            f.write(data)
        n += 1
    return n, top


def adopt(stage, target):
    """The staged cart folder `stage` moved to `target` by one rename; a cart
    already there goes aside to `<stage>.old` first and is removed after
    (cart_index.recover puts it back if the power goes between)."""
    if _store is not None:
        return _store.adopt(stage, target)
    aside = stage + ".old"
    if _exists(target):
        if _exists(aside):
            _rmtree(aside)
        os.rename(target, aside)
        os.rename(stage, target)
        _rmtree(aside)
    else:
        os.rename(stage, target)


# A dropped file's name loses these, in this order, before it names a folder.
_DROP_EXTS = (".p8.png", ".p8", ".zip", ".moy")


def _id_part(s):
    """`s` in a cart id's characters: lowercase a-z, 0-9 and _, each run of
    anything else one _, none at either end."""
    out = ""
    for c in s.lower():
        if "a" <= c <= "z" or "0" <= c <= "9" or c == "_":
            out += c
        elif not out.endswith("_"):
            out += "_"
    return out.strip("_")


def import_base(name):
    """The folder stem a dropped file or an archive's top folder named `name`
    lands under: the cart's id, `<author>.<name>`, each part in the id's
    characters, the local author's (USER_NS) when it names none."""
    n = name.rsplit("/", 1)[-1]
    for ext in _DROP_EXTS:
        if n.lower().endswith(ext):
            n = n[:-len(ext)]
    dot = n.find(".")
    who = _id_part(n[:dot]) if dot >= 0 else ""
    return (who or USER_NS) + "." + (_id_part(n[dot + 1:]) or "cart")


def import_target(root, base, theme=False):
    """The free folder for `base` in the store `root`: `base.moy`, else the
    next `base_2.moy`, `base_3.moy`... -- a theme's stem cut so its name
    stays THEME_NAME_MAX long."""
    target = cart_path(root, base + CART_EXT)
    i = 2
    while _exists(target):
        stem = base[:THEME_NAME_MAX - len(str(i)) - 1] if theme else base
        target = cart_path(root, stem + "_" + str(i) + CART_EXT)
        i += 1
    return target


def import_archive(archive, root=CARTS_DIR, stage=None, name=None):
    """A `.moy` archive unpacked into a staging folder and adopted onto the
    shelf: the new cart's path, or None when it held no files. A cart lands
    under `import_base` of its top folder (else of `name`, else of the
    archive's file name), a theme under its own name (`_theme_of`)."""
    if stage is None:
        stage = _sibling_path(root, "install") + "/import"
    _mkdir(_sibling_path(root, "install"))
    _rmtree(stage)
    n, top = unpack(archive, stage)
    if not n:
        _rmtree(stage)
        return None
    base = top or name or archive.rsplit("/", 1)[-1]
    theme = _theme_of(stage, base)
    target = import_target(root, theme[0] if theme else import_base(base),
                           theme is not None)
    if theme is not None:
        landed = target.rsplit("/", 1)[-1][:-len(CART_EXT)]
        if landed != theme[0] or theme[2]:
            d = json.loads(theme[3])
            d["name"] = landed
            _write(stage + "/" + theme[1], json.dumps(d))
    adopt(stage, target)
    return target


def _theme_of(folder, stem):
    """A theme package's (name, file, renamed, text) in unpacked `folder`,
    or None for a cart. A theme
    lands under its theme.json's `name`, else its folder's `stem` in the
    grammar (`renamed`), since the loader takes it only from `<name>.moy`."""
    try:
        man = json.loads(_read(folder + "/manifest.json"))
        if man["moybyte"]["type"] != "theme":
            return None
    except (OSError, ValueError, KeyError, TypeError):
        return None
    main = man.get("main")
    file = main if type(main) is str and main else "theme.json"
    try:
        text = _read(folder + "/" + file)
        name = json.loads(text).get("name")
    except (OSError, ValueError, AttributeError):
        return None
    if theme_name_ok(name):
        return (name, file, False, text)
    for ext in _DROP_EXTS:
        if stem.lower().endswith(ext):
            stem = stem[:-len(ext)]
    alt = ""
    for c in stem.lower():
        if "a" <= c <= "z" or "0" <= c <= "9" or c in "_-":
            alt += c
        elif not alt.endswith("_"):
            alt += "_"
    alt = alt.lstrip("_-")[:THEME_NAME_MAX]
    return (alt if theme_name_ok(alt) else "theme", file, True, text)
