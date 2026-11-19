"""Where a cart's written files (moy-spec SPEC.md 9.1, 16.12) are kept, and
the name each path is kept under.

The store of them is native/moy_store/moy_files.c on every tier: the boards'
and the browser's moycore on the MicroPython task (native/moycore/
moycore_wasm.c, through moy_wasm_on_vm, and moycore_files.c for a Lua run
and a Python cart's CartFiles), the host's run by ctypes
(runtime/host/moyhost_wasm.c; `moy_play.Files` is the suites' view of it).
moy_files.h has its rules: a write whole or not at all, one writer, the
listing.

WHERE: beside the carts store, the app's private folder `apps/<id>/`
(docs/os_2026-11/between_apps.md section 2) -- <id> the cart folder's
name less ".moy" (`cart_id`) -- so an install, an update or a push replaces
the cart's folder and never touches them, and Get Carts' REMOVE takes them
with the cart (native/moy_store/moy_shelf.h, runtime/carts/cart_index.py's
`remove`).

ONE FLAT FOLDER PER CART: a path is one file named by its key (`key`), the
rule moy-spec's libmoy/port/moy_files.c keeps for the desktop player: a-z,
0-9, '_', '-' and an inner '.' as they are, every other byte '%' and two
lowercase hex digits, and a name Windows keeps for a device escaped at its
first byte. A key is lowercase, so the T-Deck's FAT card keeps "Save" and
"save" apart. runtime/system/app_keys.py names an app's files by it.

MicroPython-safe: no os.path.
"""

from moy_store_base import APPS_DIR, _sibling_path

WRITTEN_DIR = APPS_DIR

_KEPT = b"abcdefghijklmnopqrstuvwxyz0123456789_-"
_DEVICES = ("con", "prn", "aux", "nul")


def _encode(path, first_out):
    out = []
    n = len(path)
    for i in range(n):
        c = path[i]
        kept = c in _KEPT or (c == 0x2E and 0 < i < n - 1)
        if kept and not (i == 0 and first_out):
            out.append(chr(c))
        else:
            out.append("%%%02x" % c)
    return "".join(out)


def key(path):
    """The file name the written path `path` (bytes) is kept under."""
    k = _encode(path, False)
    stem = k.split(".", 1)[0]
    if stem in _DEVICES or (len(stem) == 4 and stem[:3] in ("com", "lpt")
                            and "1" <= stem[3] <= "9"):
        k = _encode(path, True)
    return k


def cart_id(folder):
    """The name a cart's files are kept under: its folder's, less ".moy"."""
    return folder[:-4] if folder.endswith(".moy") and len(folder) > 4 else folder


def written_root(carts_root):
    return _sibling_path(carts_root, WRITTEN_DIR)


def folder_of(cart_path):
    """The folder the cart at `cart_path` keeps its written files in."""
    parts = cart_path.rstrip("/").rsplit("/", 1)
    root, folder = (parts[0], parts[1]) if len(parts) == 2 else ("", parts[0])
    return written_root(root) + "/" + cart_id(folder)
