"""The `.moy` store's base leaf: its on-card LAYOUT and the small rules every
store module shares.

The directory and extension names, the sibling-path formula (system state
lives BESIDE the carts dir), `ensure_dirs`, the cart NAME rule (`slug`), the
manifest canvas-field codec and the two directory primitives `moy_fs` lacks.
`moy_carts` (the store core), `moy_seed`, `moy_files` and `moy_file_ops` all
import from here and never from each other's callers, so any of them can be
imported first. `moy_carts` re-exports every name under its old spelling.
"""

try:
    import os
except ImportError:  # pragma: no cover
    os = None

try:
    from moy_fs import (_mkdir, set_publish_root)
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime.moy_fs import (_mkdir, set_publish_root)


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
# whoever wrote it (runtime/moy_image.py holds the codec and the argument).
IMAGES_DIR = "images"
IMAGE_EXT = ".moyimg"

# Tile flags (SPEC.md 3.5): the FIFTH asset, one byte per tile for all 512 tiles
# of the SPEC.md 3.2 sheet, as hex pairs in tile order. The tile-tagging idiom --
# solid, spike, coin, layer 2 -- read by fget, written by fset and consulted by
# map(..., layers). A sidecar rather than a manifest field because it is data
# with the sheet's shape. PICO-8's __gff__ is its first 256 tiles byte for byte.
FLAGS_NAME = "flags.moyflags"
TILE_FLAGS = 512

# Placed-actor scenes (#85) live in a per-cart scenes/ subfolder, one .moyscene
# actor table per scene.
SCENES_DIR = "scenes"
SCENE_EXT = ".moyscene"


def _sibling_path(root, name):
    """A system-state document's path: BESIDE the carts dir, one level up from
    `root`, so it is tied to no single cart."""
    parent = root.rsplit("/", 1)[0]
    return (parent + "/" + name) if parent else name


def slug(title):
    out = ""
    for ch in str(title).lower():
        if ch.isalpha() or ch.isdigit():
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


def _rmtree(path):
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
