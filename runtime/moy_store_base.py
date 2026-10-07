"""The `.moy` store's base leaf: its on-card LAYOUT and the small rules every
store module shares.

The directory and extension names, the sibling-path formula (system state
lives BESIDE the carts dir), `ensure_dirs`, the cart NAME rule (`slug`), the
manifest canvas-field codec, the rows of a sprite sheet a launcher icon is cut
from (`icon_rows`, which the shelf's scan and the sheet codec both read) and
the directory primitives `moy_fs` lacks: a folder's listing, what it proves
absent, and a scan's walk into a folder (`_enter`/`_leave`).
`moy_carts` (the store core), `moy_seed`, `moy_files` and `moy_file_ops` all
import from here and never from each other's callers, so any of them can be
imported first. `moy_carts` re-exports every name under its old spelling.
"""

try:
    import os
except ImportError:  # pragma: no cover
    os = None

try:
    from moy_fs import (_exists, _mkdir, set_publish_root, _native as _store)
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime.moy_fs import (_exists, _mkdir, set_publish_root,
                                _native as _store)
if _store is not None and not hasattr(_store, "rmtree"):
    _store = None


CARTS_DIR = "/sd/moybyte/carts"
CART_FORMAT = "moybyte-cart-v1"

# The closed set of cart canvas sizes (SPEC.md 1/3.1). Closed so a host still
# provisions for a fixed-size machine and can pick its scaler per size ahead of
# time; 128x128 exists to inherit the PICO-8 back catalogue at native res.
CANVAS_SIZES = {"320x240": (320, 240), "160x120": (160, 120),
                "128x128": (128, 128)}


def _normalize_canvas(value):
    """A manifest "canvas" (SPEC.md 1/3.1) -> (w, h) from the closed set, None
    when absent, or the raw declared value when OUT OF SET. Unlike an icon this
    is a CAPABILITY field: a bad value is not dropped here, because the loader
    has no way to refuse -- the evidence is carried so Player.start can refuse
    the cart by name (like an unknown `runtime`) instead of running it at
    dimensions it did not ask for, which would break every coordinate in it.

    The dict form ({"width": W, "height": H, ...}) is the LEGACY moybyte shape
    -- carts already seeded on boards carry it (a stale celeste on the P4
    refused the day this normalizer shipped without it), so it normalizes like
    the string when its size is in the set."""
    if value is None:
        return None
    if isinstance(value, str):
        wh = CANVAS_SIZES.get(value)
        if wh is not None:
            return wh
    elif isinstance(value, dict):
        try:
            wh = (int(value.get("width")), int(value.get("height")))
        except (TypeError, ValueError):
            return value
        if wh in CANVAS_SIZES.values():
            return wh
    return value


def _canvas_str(value):
    """The manifest wire form of a canvas value: a loaded (w, h) tuple goes back
    to its "WxH" string; anything else (including an out-of-set original) is
    written back verbatim, so a copy stays lossless."""
    if isinstance(value, (list, tuple)) and len(value) == 2:
        return "%dx%d" % (value[0], value[1])
    return value


# Paint-image assets (#63 Fold 3) live in a per-cart images/ subfolder as
# <name>.moyimg files -- the THIRD asset type (a 64-colour MOY64 index bitmap from
# the paint app), alongside sprites.moygfx and map.moymap. A .moyimg is a small JSON
# header {format,w,h,data} over deflated indices, one byte per pixel -- ONE format,
# whoever wrote it (runtime/moyimg.py holds the codec and the argument).
IMAGES_DIR = "images"
IMAGE_EXT = ".moyimg"

# Tile flags (SPEC.md 3.5): the FIFTH asset, one byte per tile for all 512 tiles
# of the SPEC.md 3.2 sheet, as hex pairs in tile order. The tile-tagging idiom --
# solid, spike, coin, layer 2 -- read by fget, written by fset and consulted by
# map(..., layers). A sidecar rather than a manifest field because it is data
# with the sheet's shape. PICO-8's __gff__ is its first 256 tiles byte for byte.
FLAGS_NAME = "flags.moyflags"
TILE_FLAGS = 512

# A cart's COVER (SPEC.md 3.6, visual identity v1 Section 11.4): `cover.png` in
# the folder's root, beside the manifest -- a 128x128 PNG the shelf draws
# (runtime/cover_cache.py) and Paint edits, read through runtime/cover_png.py.
# A file outside the profile is no cover, and the card draws the cart's icon or
# type glyph instead. tools/gen_covers.py writes the seed games' covers. The one
# binary file in a cart that is not a compiled module, and the one the store,
# the seed roster and the sync wire carry as bytes.
COVER_FILE = "cover.png"
COVER_MAX_BYTES = 65536               # cover_png.MAX_BYTES (pinned equal)

# A cart's regenerable preview cache: `thumbs/`, the wallpaper-preview
# sidecars runtime/moy_image.py writes. Never copied with a cart, never synced.
THUMBS_DIR = "thumbs"

# Placed-actor scenes (#85) live in a per-cart scenes/ subfolder, one .moyscene
# actor table per scene.
SCENES_DIR = "scenes"
SCENE_EXT = ".moyscene"

# A cart's sprite sheet (SPEC.md 3.2): `sprites.moygfx`, one hex digit a pixel
# and one line a pixel row of 16 x 32 tiles of 8. Blank lines are skipped, and
# a short blob leaves the rows it lacks blank.
SPRITES_NAME = "sprites.moygfx"
# The hex digits of a pixel with colour: `set(line) & SHEET_INK` asks whether a
# whole line has art in one C-level pass, decoding nothing.
SHEET_INK = set("123456789abcdefABCDEF")


def icon_rows(lines, n=0, tw=1, th=1, cols=16, rows=32, tile=8):
    """The rows of a sheet a launcher icon is cut from: `(pw, ph, want)`,
    `want` holding each of the icon's `ph` pixel rows as its slice of the hex
    line (None where the blob ends first), or None when the sheet carries no
    art at all, so the card draws its type glyph.

    The icon is tiles `n` .. spanning `tw` x `th` (SPEC.md 3.4), clamped to the
    sheet, and an `n` off the sheet is tile 0. `lines` is any iterable of the
    blob's lines -- the split text or a file read a piece at a time -- and is
    read only until the art is found and the icon's rows are in hand, so the
    shelf's scan of a cart touches the top of its sheet and no more."""
    w, h = cols * tile, rows * tile
    if n < 0 or n >= cols * rows:
        n, tw, th = 0, 1, 1
    if tw < 1:
        tw = 1
    if th < 1:
        th = 1
    ox, oy = (n % cols) * tile, (n // cols) * tile
    tw = min(tw, (w - ox) // tile)
    th = min(th, (h - oy) // tile)
    pw, ph = tw * tile, th * tile
    want = [None] * ph
    ink = False
    y = 0
    for line in lines:
        line = line.strip()
        if not line:
            continue
        if y >= h:
            break
        if not ink and (set(line[:w]) & SHEET_INK):
            ink = True
        if oy <= y < oy + ph:
            want[y - oy] = line[ox:ox + pw]
        y += 1
        if ink and y >= oy + ph:
            break
    return (pw, ph, want) if ink else None


def _sibling_path(root, name):
    """A system-state document's path: BESIDE the carts dir, one level up from
    `root`, so it is tied to no single cart."""
    parent = root.rsplit("/", 1)[0]
    return (parent + "/" + name) if parent else name


# A cart is a folder whose name ends in CART_EXT. Its path is composed here and
# nowhere else (docs/kernel_store_2026-10.md section 4): a store that shards its
# root or keeps a cart elsewhere changes these and no caller.
# tests/test_store_paths.py fails any other module that composes one.
CART_EXT = ".moy"


def cart_path(root, folder):
    """The path of the cart folder named `folder` in the store `root`."""
    return root + "/" + folder


# A cart's id is `<author>.<name>` (#162): the folder is the id and the
# extension. The built-ins' author is BUILTIN_NS; a cart a kid makes takes its
# author from the Settings field USER_NS_KEY, which is USER_NS until profiles
# exist (docs/kernel_store_2026-10.md section 12).
BUILTIN_NS = "moybyte"
USER_NS = "local"
USER_NS_KEY = "author"


# Moybyte's own manifest fields -- everything SPEC.md 3.1's table does not
# name -- live under ONE top-level key named after the implementation
# (`"moybyte": {"type": "app", ...}`), never loose at the top level. A field
# of these at the top level reads as absent: the readers are strict.
VENDOR_KEY = "moybyte"
VENDOR_FIELDS = ("type", "system", "order", "permissions",
                 "config", "edit", "targets", "age_mode", "app", "assets",
                 "graduated")


def vendor(man):
    """The manifest's Moybyte object, or {} when it has none (or `man` is not
    an object)."""
    v = man.get(VENDOR_KEY) if isinstance(man, dict) else None
    return v if isinstance(v, dict) else {}


def _id_part(s):
    if not s:
        return False
    for ch in s:
        if not ("a" <= ch <= "z" or "0" <= ch <= "9" or ch == "_"):
            return False
    return True


def is_cart_id(s):
    """True for a cart id as SPEC.md 3.1 spells it: `<author>.<name>`, two
    parts of a-z, 0-9 and _ joined by one dot."""
    if not isinstance(s, str) or s.count(".") != 1:
        return False
    a, n = s.split(".")
    return _id_part(a) and _id_part(n)


def builtin_name(path):
    """The folder of the built-in at `path` (`moybyte.files.moy` for
    `.../moybyte.files.moy`), or None for any other cart: an app's identity
    cart is a built-in, never a cart a kid made or copied. The folder is the
    one name a built-in has: system_carts/ ships it, every store seeds it."""
    name = str(path).replace("\\", "/").rsplit("/", 1)[-1]
    return name if name.startswith(BUILTIN_NS + ".") else None


def cart_folder(name, ns=None):
    """The folder a cart named `name` (a title, or a slug already) is stored
    in: `<ns>.<slug>` and the extension, or the bare slug with no `ns`."""
    return (slug(ns) + "." if ns else "") + slug(name) + CART_EXT


def store_path(root, rel):
    """The path of `rel`, a path relative to the store `root` as the sync wire
    and the webhost name one: a cart folder's files under `cart_path`, and
    anything else beside them."""
    i = rel.find("/")
    head = rel if i < 0 else rel[:i]
    if head.endswith(CART_EXT):
        return cart_path(root, head) + rel[len(head):]
    return root + "/" + rel


def slug(title):
    """`title` in a cart id's characters (SPEC.md 3.1): lowercase a-z, 0-9 and
    _, a space or a dash becoming _ and anything else dropped."""
    out = ""
    for ch in str(title).lower():
        if "a" <= ch <= "z" or "0" <= ch <= "9":
            out += ch
        elif ch in " -_":
            out += "_"
    return out or "cart"


def ensure_dirs(root=CARTS_DIR):
    parent = root.rsplit("/", 1)[0]
    if parent:
        _mkdir(parent)
    _mkdir(root)
    # The publish marker (#154) goes at the PARENT, because that is what covers
    # the whole store in one file: the cart folders and their journals under
    # `root`, the #108 files layer and the sibling stores (system.json,
    # wifi.json, shared.moygfx) beside it. One marker, one read per boot.
    set_publish_root(parent or root)


def _is_dir(path):
    try:
        return (os.stat(path)[0] & 0x4000) != 0
    except OSError:
        return False


# A path lookup is the expensive operation on every store medium: FAT on a
# card reads each directory on the path again, one sector at a time, and
# littlefs walks its metadata pairs. A cart's files are therefore checked
# against ONE listing of its folder, never looked up one by one, and on FAT
# the reads that remain are made from inside the folder (#224).
_S_IFDIR = 0x4000
_S_IFREG = 0x8000


def _listing(path):
    """The folder at `path` as {name: is_dir}, or None when it will not list.
    "" is the working directory."""
    try:
        it = os.ilistdir(path) if path else os.ilistdir()
    except AttributeError:                 # CPython: scandir says the same
        try:
            with os.scandir(path or ".") as it:
                return {e.name: e.is_dir() for e in it}
        except OSError:
            return None
    except OSError:
        return None
    names = {}
    try:
        for e in it:
            kind = e[1]
            if kind == _S_IFDIR or kind == _S_IFREG:
                names[e[0]] = kind == _S_IFDIR
            else:                          # a listing that does not say (DT_UNKNOWN)
                names[e[0]] = _is_dir(path + "/" + e[0] if path else e[0])
    except OSError:
        return None
    return names


def _plain(name):
    """Whether FAT reads `name` literally: one path component of ASCII letters,
    digits, `_`, `-` and inner dots. FAT folds case, strips trailing dots and
    spaces and answers to 8.3 aliases, so any other name may open where a
    listing does not show it."""
    if not name or name[0] in ". " or name[-1] in ". ":
        return False
    for c in name:
        if ord(c) > 127 or not (c.isalpha() or c.isdigit() or c in "_-."):
            return False
    return True


def _absent(names, name):
    """True when the listing `names` proves `name` is not in its folder: no
    entry by that name in any case, and a name every medium reads literally.
    False when it is there, or when only the file system can say."""
    if names is None or name in names:
        return False
    low = name.lower()
    for n in names:
        if n.lower() == low:
            return False
    return _plain(name)


def _has(folder, path, name):
    """Whether the folder at `path` holds `name`, answered from `folder`, its
    listing, when the listing can say and by a lookup when it cannot."""
    if folder is not None and name in folder:
        return True
    if _absent(folder, name):
        return False
    return _exists(path + "/" + name)


def _cwd():
    try:
        return os.getcwd()
    except (OSError, AttributeError):
        return None


def _enter(path):
    """Move the working directory into the folder at `path` and return its
    listing, or None when either fails -- the caller then reads by full path.
    On FAT a name opened from inside the folder is looked up in that folder
    alone, where a full path walks every directory above it again. Nothing may
    import while a scan is inside a folder: `""` on sys.path is the working
    directory, so a cart's own files would be searched, and could shadow a
    module."""
    try:
        os.chdir(path)
    except (OSError, AttributeError):
        return None
    return _listing("")


def _leave(here):
    try:
        os.chdir(here)
    except (OSError, AttributeError):
        pass


def _rmtree(path):
    if _store is not None:
        _store.rmtree(path)
        return
    try:
        names = os.listdir(path)
    except OSError:
        return
    for n in names:
        p = path + "/" + n
        if _is_dir(p):
            _rmtree(p)
        else:
            try:
                os.remove(p)
            except OSError:
                pass
    try:
        os.rmdir(path)
    except OSError:
        pass
