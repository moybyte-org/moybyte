# Moybyte SD cartridge store.
#
# Cartridges live as .moy folders under /sd/moybyte/carts/<name>.moy/:
#   manifest.json   title, type, runtime, main, edit-schema
#   main.py         the cartridge source (_init/_update/_draw + kid API)
#   config.json     user-edited values (the Make-it-mine surface)
#   (sprites later)
#
# MicroPython-friendly by construction (no shutil; os-only). Functions take a `root` so the format/seed/scan logic is
# host-testable against a temp dir. SD shares the SPI bus with the display, so
# the caller mounts SD (moybyte_sd) with the LoRa/TFT CS deselected first.

import json

try:
    import os
except ImportError:  # pragma: no cover
    os = None

try:
    import time as _time
except ImportError:  # pragma: no cover
    _time = None

CARTS_DIR = "/sd/moybyte/carts"
CART_FORMAT = "moybyte-cart-v1"

# Input-kind hint (#42 Thread 3): a manifest MAY declare which of the three cart-API
# input groups it actually reads -- "buttons" (btn/btnp), "touch" (touch()), "keyboard"
# (key/keyp/textmode) -- so a surface (today: the web view) can show only the controls
# a cart needs (e.g. skip the virtual d-pad for a touch()-only game). OPTIONAL and
# purely advisory: absent/invalid -> None, and every consumer treats None as "show
# everything" (today's behaviour), so an undeclared cart is a zero-regression no-op.
INPUT_KINDS = ("buttons", "touch", "keyboard")


def _int_or(value, default):
    """`value` as an int, or `default` when it is absent or malformed. Manifest
    fields are hand-editable, so a bad one must DEGRADE -- an exception here
    escapes to load()'s blanket handler and drops the whole cart out of the
    gallery, which is a far worse failure than losing one field."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


# SPEC.md 3.4: an icon is at most 4x4 tiles (32x32px). The bound is what makes the
# field safe to honour -- a launcher decodes MANY icons at once, and an unbounded
# block would let one cart name the whole 512-tile sheet.
ICON_MAX_TILES = 4


def _normalize_icon(value):
    """A manifest "icon" (SPEC.md 3.4) -> (tile, w, h), or None when absent, malformed
    or out of range. Accepts a bare tile id (1x1) or [tile, w, h]. Never raises and
    never refuses: an icon is COSMETIC, so a bad one costs the cart its picture (the
    host picks instead), not its place in the gallery -- unlike `extensions`/`runtime`,
    where running the cart at all would be wrong."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return (value, 1, 1) if value >= 0 else None
    if not isinstance(value, (list, tuple)) or not 1 <= len(value) <= 3:
        return None
    try:
        n = int(value[0])
        w = int(value[1]) if len(value) > 1 else 1
        h = int(value[2]) if len(value) > 2 else 1
    except (TypeError, ValueError):
        return None
    if n < 0 or not 1 <= w <= ICON_MAX_TILES or not 1 <= h <= ICON_MAX_TILES:
        return None
    return (n, w, h)


def _normalize_input_kinds(value):
    """A manifest "input" value -> a tuple of known kinds, or None when absent/not a
    list/empty after filtering -- never raises on a malformed manifest."""
    if not isinstance(value, list):
        return None
    kinds = tuple(k for k in value if k in INPUT_KINDS)
    return kinds or None


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
# header {format,w,h,data}. Existing assets use zlib-compressed indices; Paint writes
# a MicroPython-safe RLE form selected by `codec:"rle"`. Both remain one byte/pixel
# after decode and are accepted by the host/device image accessors.
IMAGES_DIR = "images"
IMAGE_EXT = ".moyimg"
ARTWORK_NAME = "artwork.moyimg"
# Cartridge COVER ART (visual identity v1 Section 11.4): a cart folder may carry
# images/cover.moyimg -- static authored cover art the Library shelf draws
# full-bleed on the card. The deterministic fallback when absent is the cart's
# sprite tile 0 / type glyph (the pre-cover look). tools/gen_covers.py captures a
# gameplay frame for the seed games; Paint art or any moyimg works the same.
COVER_IMAGE = "cover"
NOTES_NAME = "notes.json"
DECK_NAME = "deck.json"

# Desk Lab interop assets (#78): the tiny cart-folder documents a game reads back
# through the table(name)/text(name) cart verbs -- the Sheets + Writer analogue of
# Paint's images/<name>.moyimg. A .moysheet is the moysheet-v1 JSON blob (formula +
# computed value per cell, from Sheets); a .moytext is the moytext-v1 blob (a
# Writer doc's body). Kid-greppable, engine-free (the v0.4 portability contract).
TABLES_DIR = "tables"
TABLE_EXT = ".moysheet"
TEXTS_DIR = "docs"
TEXT_EXT = ".moytext"
# The Sheets app's own workbook (a list of sheets), beside the carts dir exactly
# like Writer's notes.json / Paint's artwork.moyimg.
SHEETS_NAME = "sheets.json"

# A single shared sprite sheet lives alongside the carts dir (one level up, so
# it sits beside every <name>.moy folder). Tiles painted here are reusable
# across carts; the import-tile primitive copies tiles between any two sheets.
SHARED_SHEET_NAME = "shared.moygfx"

# Tile flags (SPEC.md 3.5): the FIFTH asset, one byte per tile for all 512 tiles
# of the SPEC.md 3.2 sheet, as hex pairs in tile order. The tile-tagging idiom --
# solid, spike, coin, layer 2 -- read by fget, written by fset and consulted by
# map(..., layers). A sidecar rather than a manifest field because it is data
# with the sheet's shape. PICO-8's __gff__ is its first 256 tiles byte for byte.
FLAGS_NAME = "flags.moyflags"
TILE_FLAGS = 512


def parse_flags(text):
    """A flags.moyflags blob -> a 512-byte bytearray (SPEC.md 3.5).

    Two hex digits per tile in tile order, whitespace ignored; a SHORT file
    leaves the remaining tiles zero. The mirror of moy-spec's
    `moycore.cart.parse_flags` -- an odd digit count or more than 512 pairs is
    the file being something else, and raises rather than guessing at an
    alignment.
    """
    hexd = "".join(text.split())
    if len(hexd) % 2 or len(hexd) > TILE_FLAGS * 2:
        raise ValueError("expected up to %d hex byte pairs, got %d digits"
                         % (TILE_FLAGS, len(hexd)))
    out = bytearray(TILE_FLAGS)
    for i in range(0, len(hexd), 2):
        out[i // 2] = int(hexd[i:i + 2], 16)
    return out


def flags_to_hex(flags):
    """The file form: sixteen lines of thirty-two tiles."""
    return "\n".join(
        "".join("%02x" % flags[row * 32 + i] for i in range(32))
        for row in range(16)) + "\n"


# The moyimg codec + cover-thumb sidecars and the crash-safe file primitives
# moved to their own leaf modules (moy_image / moy_fs); imported back + re-exported
# under their pre-extraction names so every caller, test and `store.X` lookup is
# unchanged. Same bare-or-package fallback as every shared module.
try:
    from moy_image import (THUMBS_DIR, _b64_encode, _b64_decode, encode_moyimg,
                           moyimg_runs, decode_moyimg, cover_sig, _thumb_file)
    from moy_fs import (_mkdir, _exists, _read, _write, _remove, _copy,
                        _write_atomic, _read_recover)
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime.moy_image import (THUMBS_DIR, _b64_encode, _b64_decode,
                                   encode_moyimg, moyimg_runs, decode_moyimg,
                                   cover_sig, _thumb_file)
    from runtime.moy_fs import (_mkdir, _exists, _read, _write, _remove, _copy,
                                _write_atomic, _read_recover)


def load_image(path, name):
    """One paint-image blob (images/<name>.moyimg) for the cart at `path`, or
    None. The Library shelf reads covers through this (COVER_IMAGE) so a
    slimmed cart (#66 live-set diet) never needs rehydrating for its card."""
    try:
        return _read(path + "/" + IMAGES_DIR + "/" + name + IMAGE_EXT)
    except OSError:
        return None


def load_images(path):
    """A cart's paint-image assets: {name: text} of every images/<name>.moyimg blob
    (name = filename without the extension), or {} when the cart has no images/ dir.
    Kept as raw text (like the sprites/map blobs); the console decodes each into an
    Image via the make_api image() accessor. Guarded so a missing dir / bad entry just
    yields fewer images, never a crash (mirrors load()'s degrade-don't-throw contract)."""
    out = {}
    d = path + "/" + IMAGES_DIR
    try:
        names = os.listdir(d)
    except OSError:
        return out                     # no images/ subfolder -> the common case
    for name in names:
        if name.endswith(IMAGE_EXT):
            try:
                out[name[:-len(IMAGE_EXT)]] = _read(d + "/" + name)
            except OSError:
                pass                   # skip an unreadable entry, keep the rest
    return out


def save_image(cart, name, text):
    """Persist one paint-image asset to images/<name>.moyimg (atomically, like the
    sprite/map saves) and update cart['images']. Ensures the images/ subfolder exists.
    `text` is the .moyimg JSON blob ({format,w,h,data})."""
    _mkdir(cart["path"] + "/" + IMAGES_DIR)
    _write_atomic(cart["path"] + "/" + IMAGES_DIR + "/" + name + IMAGE_EXT, text)
    imgs = cart.get("images")
    if not isinstance(imgs, dict):
        imgs = {}
        cart["images"] = imgs
    imgs[name] = text


# Scene assets (#85) live in a per-cart scenes/ subfolder as <name>.moyscene files --
# the FOURTH asset type (a WYSIWYG-placed table of actors), alongside sprites.moygfx,
# map.moymap and the images/. A .moyscene is plain compact JSON: an ordered list of
# actor rows {tag, tile, x, y, flip, flags} (order = spawn order = draw order; no
# compression -- these tables are small, unlike the bitmaps). The manifest's
# assets.scenes list is the authoritative ORDERED set (its first entry is the default
# active scene, the one bare scene() iterates); the folder scan is the safety net. The
# cart consumes them once in _init via scene()/load_scene() (data-only, #85 Variant A)
# -- Project builds a widgets.Scenes from the raw blobs. json+os only, like the rest.
SCENES_DIR = "scenes"
SCENE_EXT = ".moyscene"


def load_scene(path, name):
    """One scene blob (scenes/<name>.moyscene) for the cart at `path`, or None."""
    try:
        return _read(path + "/" + SCENES_DIR + "/" + name + SCENE_EXT)
    except OSError:
        return None


def load_scenes(path):
    """A cart's scene assets (#85): {name: text} of every scenes/<name>.moyscene blob
    (name = filename without the extension), or {} when the cart has no scenes/ dir.
    Kept as raw JSON text (like the map/image blobs); Project builds the widgets.Scenes
    the cart reads. Guarded so a missing dir / bad entry just yields fewer scenes, never
    a crash (mirrors load_images)."""
    out = {}
    d = path + "/" + SCENES_DIR
    try:
        names = os.listdir(d)
    except OSError:
        return out                     # no scenes/ subfolder -> the common case
    for name in names:
        if name.endswith(SCENE_EXT):
            try:
                out[name[:-len(SCENE_EXT)]] = _read(d + "/" + name)
            except OSError:
                pass                   # skip an unreadable entry, keep the rest
    return out


def scene_names(man, blobs):
    """The ordered scene names for a cart (#85): the manifest's assets.scenes order
    (filtered to scenes that actually exist on disk), then any on-disk scene the
    manifest forgot, appended sorted -- so the loader is authoritative but robust to a
    hand-added file. `man` is the parsed manifest, `blobs` the load_scenes() dict.
    Element 0 is the default active scene."""
    order = []
    seen = {}
    assets = man.get("assets") if isinstance(man, dict) else None
    listed = assets.get("scenes") if isinstance(assets, dict) else None
    if isinstance(listed, list):
        for n in listed:
            if n in blobs and n not in seen:
                order.append(n)
                seen[n] = True
    for n in sorted(blobs.keys()):     # any on-disk scene the manifest didn't list
        if n not in seen:
            order.append(n)
            seen[n] = True
    return order


def _manifest_add_scene(cart_dir, name):
    """Register scene `name` in manifest.json's assets.scenes list (#85), preserving
    every other manifest field (title/version/edit/permissions/assets.*). Idempotent
    -- a name already listed writes nothing. Atomic (its rename is the torn-write
    proofing), like _manifest_set_graduated. Returns the ordered scene-name list after
    the change (so save_scene can sync cart['scene_names']), or None on a missing/bad
    manifest."""
    path = cart_dir + "/manifest.json"
    try:
        man = json.loads(_read_recover(path))
    except (OSError, ValueError):
        return None
    if not isinstance(man, dict):
        return None
    assets = man.get("assets")
    if not isinstance(assets, dict):
        assets = {}
        man["assets"] = assets
    scenes = assets.get("scenes")
    if not isinstance(scenes, list):
        scenes = []
        assets["scenes"] = scenes
    if name not in scenes:
        scenes.append(name)
        _write_atomic(path, json.dumps(man))
    return list(scenes)


def save_scene(cart, name, text):
    """Persist one scene to scenes/<name>.moyscene (#85) -- the mirror of save_map,
    atomically so an interrupted write can't truncate the real file. Registers the
    scene in manifest.json's assets.scenes (so load() finds it) and updates the live
    cart dict (cart['scenes'] + cart['scene_names']). `text` is the compact .moyscene
    JSON blob (an ordered actor list)."""
    _mkdir(cart["path"] + "/" + SCENES_DIR)
    _write_atomic(cart["path"] + "/" + SCENES_DIR + "/" + name + SCENE_EXT, text)
    scenes = cart.get("scenes")
    if not isinstance(scenes, dict):
        scenes = {}
        cart["scenes"] = scenes
    scenes[name] = text
    order = _manifest_add_scene(cart["path"], name)
    if order is not None:
        cart["scene_names"] = order
    elif name not in (cart.get("scene_names") or []):
        # No/bad manifest to update -- keep the live ordered list consistent anyway.
        cart["scene_names"] = list(cart.get("scene_names") or []) + [name]


# --- sibling stores ----------------------------------------------------------
#
# System-state documents live BESIDE the carts dir (one level up from `root`), so
# they aren't tied to any single cart: artwork/notes/sheets, the shared sprite
# sheet, the system icon theme, wifi/system/achievements JSON. The path formula,
# the read-or-None, and the ensure-dirs + atomic write are each written ONCE here;
# the per-store `X_path`/`load_X`/`save_X` wrappers keep their public names (and
# any store-specific parse/sanitize logic).

def _sibling_path(root, name):
    parent = root.rsplit("/", 1)[0]
    return (parent + "/" + name) if parent else name


def _read_sibling(root, name):
    """The store's raw text, or None if it has never been saved."""
    try:
        return _read(_sibling_path(root, name))
    except OSError:
        return None


def _load_store_json(path, pick):
    """A system JSON store's value, or None when nothing usable is on disk.

    `pick(parsed)` pulls the value out of the parsed document and returns None for
    "this isn't one", so MISSING, unparseable and wrong-shape all fall through to
    the `<path>.bak` copy `_write_atomic` leaves behind; a usable backup is
    republished as `path` (the heal `_read_recover` does for manifests) so the
    recovery is durable rather than re-run on every read.

    Every sibling store must read through this. `_write_atomic` is crash-safe only
    BECAUSE of that .bak, so its window leaves the store missing or truncated -- and
    a loader that reads either as "nothing saved yet" makes the loss permanent: the
    next save rotates the one surviving good copy into .bak and the save after that
    deletes it.
    """
    for src in (path, path + ".bak"):
        try:
            got = pick(json.loads(_read(src)))
        except (OSError, ValueError, TypeError, AttributeError):
            got = None
        if got is None:
            continue
        if src != path:
            try:
                _copy(src, path)      # heal: republish the last known-good copy
            except Exception:         # noqa: BLE001 -- still return what we recovered
                pass
        return got
    return None


def _write_sibling(root, name, text):
    """Persist a sibling store atomically (an interrupted write must never
    truncate system state). Ensures the parent dir exists."""
    ensure_dirs(root)
    _write_atomic(_sibling_path(root, name), text)


def artwork_path(root=CARTS_DIR):
    """The shared Paint document, beside the carts directory."""
    return _sibling_path(root, ARTWORK_NAME)


def load_artwork(root=CARTS_DIR):
    return _read_sibling(root, ARTWORK_NAME)


def save_artwork(text, root=CARTS_DIR):
    _write_sibling(root, ARTWORK_NAME, text)


def load_deck(cart):
    """The Storybook deck (a `moydeck-v1` JSON blob) inside a story cart's
    folder, or None for carts that were never decks (#78)."""
    try:
        return _read(cart["path"] + "/" + DECK_NAME)
    except OSError:
        return None


def save_deck(cart, text):
    _write_atomic(cart["path"] + "/" + DECK_NAME, text)


def notes_path(root=CARTS_DIR):
    """The Writer app's notebook (a `moynotes-v1` JSON blob), beside the carts
    directory like Paint's shared artwork.moyimg."""
    return _sibling_path(root, NOTES_NAME)


def load_notes(root=CARTS_DIR):
    return _read_sibling(root, NOTES_NAME)


def save_notes(text, root=CARTS_DIR):
    _write_sibling(root, NOTES_NAME, text)


# --- Desk Lab interop (#78): table(name) / text(name) cart-folder documents ---
#
# A game reads a Sheets sheet or a Writer doc placed in ITS OWN cart folder, the
# exact mirror of Paint's image(name) -> images/<name>.moyimg. The decoders turn a
# tiny JSON blob into the plain-Python shape the cart verb hands the kid (rows of
# values / lines of text); both are guarded so a missing/bad file degrades to an
# empty list, never a crash (image()'s degrade-don't-throw contract).

def decode_table(blob):
    """A moysheet-v1 blob -> rows (a list of lists of computed values). Numbers
    stay numbers, text stays strings, a blank cell is "". The grid is trimmed to
    the last populated row/column, so a mostly-empty sheet reads as a tight table.
    Anything malformed yields []."""
    try:
        data = json.loads(blob) if isinstance(blob, str) else blob
    except (ValueError, TypeError):
        return []
    if not isinstance(data, dict):
        return []
    cells = data.get("cells")
    if not isinstance(cells, dict) or not cells:
        return []
    max_c = -1
    max_r = -1
    parsed = {}
    for key, entry in cells.items():
        cr = _ref_to_rc(key)
        if cr is None:
            continue
        col, row = cr
        if isinstance(entry, dict):
            val = entry.get("v", "")
        else:
            val = entry
        parsed[(row, col)] = val
        if col > max_c:
            max_c = col
        if row > max_r:
            max_r = row
    if max_r < 0:
        return []
    rows = []
    for r in range(max_r + 1):
        row = []
        for c in range(max_c + 1):
            v = parsed.get((r, c), "")
            row.append(v if v is not None else "")
        rows.append(row)
    return rows


def encode_text(body):
    """A doc body string -> the `moytext-v1` blob a `.moytext` file holds.

    The inverse of `decode_text`, put here in the store beside it (#181) so a
    USER APP cart can write a document Writer and Files can actually read --
    `ctx.files.encode_text` is what reaches it, and `system_api.ScopedFiles`
    what a cart calls. `writer_app._encode` is the same two lines and predates
    this; it is a de-duplication waiting for someone who owns that file."""
    return json.dumps({"format": "moytext-v1", "body": str(body)})


def decode_text(blob):
    """A moytext-v1 blob -> the doc body split into a list of lines. A blank/absent
    body is []. Anything malformed yields []."""
    try:
        data = json.loads(blob) if isinstance(blob, str) else blob
    except (ValueError, TypeError):
        return []
    if not isinstance(data, dict):
        return []
    body = data.get("body", "")
    if not isinstance(body, str) or body == "":
        return []
    return body.split("\n")


def _ref_to_rc(ref):
    """"B3" -> (col_index, row_index), both 0-based; None if malformed. A tiny
    self-contained A1 parser so this module keeps its json+os-only footprint (no
    formula.py import on the device asset path)."""
    ref = str(ref).upper()
    i = 0
    while i < len(ref) and "A" <= ref[i] <= "Z":
        i += 1
    if i == 0 or i >= len(ref):
        return None
    col = 0
    for ch in ref[:i]:
        col = col * 26 + (ord(ch) - 64)
    for ch in ref[i:]:
        if not ("0" <= ch <= "9"):
            return None
    row = int(ref[i:])
    if row < 1:
        return None
    return (col - 1, row - 1)


def load_tables(path):
    """A cart's Sheets assets: {name: rows} for every tables/<name>.moysheet blob
    (name = filename without the extension), decoded to rows. {} when the cart has
    no tables/ dir. Mirrors load_images' degrade-don't-throw contract."""
    return _load_docs(path, TABLES_DIR, TABLE_EXT, decode_table)


def load_texts(path):
    """A cart's Writer assets: {name: lines} for every docs/<name>.moytext blob,
    decoded to lines. {} when the cart has no docs/ dir."""
    return _load_docs(path, TEXTS_DIR, TEXT_EXT, decode_text)


def _load_docs(path, subdir, ext, decode):
    out = {}
    d = path + "/" + subdir
    try:
        names = os.listdir(d)
    except OSError:
        return out                     # no subfolder -> the common case
    for name in names:
        if name.endswith(ext):
            try:
                out[name[:-len(ext)]] = decode(_read(d + "/" + name))
            except OSError:
                pass                   # skip an unreadable entry, keep the rest
    return out


def save_table(cart, name, text):
    """Attach a sheet to a cart as tables/<name>.moysheet (atomically, like
    save_image). `text` is the moysheet-v1 JSON blob; the cart then reads it via
    the table(name) verb."""
    _mkdir(cart["path"] + "/" + TABLES_DIR)
    _write_atomic(cart["path"] + "/" + TABLES_DIR + "/" + name + TABLE_EXT, text)


def save_text(cart, name, text):
    """Attach a Writer doc to a cart as docs/<name>.moytext (atomically). `text`
    is the moytext-v1 JSON blob; the cart reads it via the text(name) verb."""
    _mkdir(cart["path"] + "/" + TEXTS_DIR)
    _write_atomic(cart["path"] + "/" + TEXTS_DIR + "/" + name + TEXT_EXT, text)


# --- the Sheets app's own workbook (a list of sheets), beside the carts dir ---

def sheets_path(root=CARTS_DIR):
    return _sibling_path(root, SHEETS_NAME)


def load_sheets(root=CARTS_DIR):
    return _read_sibling(root, SHEETS_NAME)


def save_sheets(text, root=CARTS_DIR):
    _write_sibling(root, SHEETS_NAME, text)


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


def _cart_version(path):
    """The integer "version" of an on-SD cart's manifest, or 0 when it has none
    (or is unreadable). A pre-versioning cart therefore counts as the oldest, so
    a versioned built-in always supersedes it on the next boot."""
    try:
        man = json.loads(_read_recover(path + "/manifest.json"))
        if isinstance(man, dict):
            return int(man.get("version", 0))
    except Exception:  # noqa: BLE001 -- a bad manifest just reads as version 0
        pass
    return 0


# The per-kid files kept across a destructive re-seed: pmem.json is the cart's
# save state / high scores (TIC-80 pmem), config.json is the kid's "Make it mine"
# tuning. A version bump replaces CODE + ART but restores these over the fresh
# copy, so updating a cart never wipes a kid's progress or settings.
_RESEED_PRESERVE = ("pmem.json", "config.json")


def _preserve_moy_data(path):
    """Snapshot an on-SD cart's per-kid files (saves + config) before a re-seed
    wipes the folder. Returns {name: text} for those present (crash-safe read)."""
    kept = {}
    for name in _RESEED_PRESERVE:
        try:
            kept[name] = _read_recover(path + "/" + name)
        except OSError:
            pass                  # not written yet (no saves / default config) -> skip
    return kept


def seed_builtins(seed_list, root=CARTS_DIR, progress=None):
    """Write missing/outdated built-in carts to SD as editable .moy folders.

    A seed dict that carries a non-empty "sprites" hex blob also gets a
    sprites.moygfx written, so the device's paint editor (and the cart's spr()
    tile draws) have the real art -- without this the device seeds blank sheets
    and the games fall back to nothing. The manifest is COMPLETE (canvas +
    permissions + full edit schema + version) so the visual "Make it mine" cards
    render on device exactly as on host.

    Versioning (the re-seed): a cart already on SD is left untouched UNLESS the
    built-in's "version" is newer than the on-SD one -- then its CODE + ART are
    REPLACED wholesale (the old folder is removed first), but the kid's data
    (pmem.json saves + config.json tuning, see _RESEED_PRESERVE) is preserved
    over the fresh copy. So a content update keeps high scores and settings;
    on-device edits to a built-in's *code/sprites* are discarded. Pre-versioning
    carts read as version 0, so bumping a built-in to >=1 refreshes stale copies
    automatically -- no more "clear /sd/moybyte/carts by hand". Bump a built-in's
    manifest "version" whenever you change its content.

    (Migration note: a preserved config.json keeps the kid's old values, so a
    NEW default for an EXISTING config key won't apply to an already-seeded cart;
    a brand-new key just falls back to its code default via cfg(key, default).)"""
    # `progress(done, total, title)` is called once per seed considered, so a
    # boot screen can show a bar. Measured on a full-erase P4 boot: seeding all
    # 32 built-ins takes 17.5 of the 25 seconds before the desktop composes, and
    # it is the only stage of that boot with a countable unit of work. Optional
    # and best-effort -- a progress callback must never be able to fail a seed.
    _total = len(seed_list)
    for _seeded, cart in enumerate(seed_list):
        if progress is not None:
            try:
                progress(_seeded, _total, cart.get("title", ""))
            except Exception:                 # noqa: BLE001
                progress = None               # broken hook: drop it, keep seeding
        d = root + "/" + slug(cart["title"]) + ".moy"
        seed_ver = int(cart.get("version", 0))
        preserved = None
        if _exists(d):
            if seed_ver <= _cart_version(d):
                continue
            preserved = _preserve_moy_data(d)   # keep saves + tuning across the wipe
            _rmtree(d)            # newer built-in: replace code+art wholesale
        _mkdir(d)
        manifest = {
            # This manifest is REGENERATED, not copied, so every field a built-in
            # declares has to be passed through explicitly or the seeded copy
            # loses it. `format` especially: the device seeds from the baked blob
            # while the host copies the source folder, so hardcoding CART_FORMAT
            # here restamped a "moy-1" built-in on device and nowhere else.
            "format": cart.get("format", CART_FORMAT),
            "title": cart["title"], "type": cart["type"],
            # #67 dual-runtime passthrough: a baked "lua" built-in seeds with its
            # runtime + main.lua intact.
            "runtime": cart.get("runtime", "python"),
            "main": cart.get("main", "main.py"),
            "edit": cart.get("edit", []),
            "version": seed_ver,
        }
        if cart.get("fps"):               # frame pacing (#63): "fps": 60 opt-out
            manifest["fps"] = cart["fps"]
        if cart.get("icon"):              # launcher icon tiles (SPEC.md 3.4)
            manifest["icon"] = list(cart["icon"])
        if cart.get("canvas") is not None:
            # A baked seed carries the manifest string; a load()ed cart carries
            # the normalized (w, h) -- both serialize back to the "WxH" form.
            manifest["canvas"] = _canvas_str(cart["canvas"])
        if cart.get("permissions") is not None:
            manifest["permissions"] = cart["permissions"]
        if cart.get("input") is not None:               # #42 Thread 3 input-kind hint
            manifest["input"] = list(cart["input"])
        scenes = cart.get("scenes")               # {name: .moyscene blob}, optional (#85)
        if scenes:
            # Register the ordered set in manifest.assets.scenes (element 0 = default
            # active) BEFORE the manifest is written, so load() finds them. A seed may
            # pin the order via "scene_order"; else sorted names (bump the built-in's
            # version, #47, whenever a seed's scenes change -- like any other content).
            manifest["assets"] = {"scenes": list(cart.get("scene_order")
                                                 or sorted(scenes.keys()))}
        _write(d + "/manifest.json", json.dumps(manifest))
        _write(d + "/" + cart.get("main", "main.py"), cart["src"])
        _write(d + "/config.json", json.dumps(cart["cfg"]))
        sprites = cart.get("sprites")
        if sprites:
            _write(d + "/sprites.moygfx", sprites)
        sounds = cart.get("sounds")               # AudioBank dict, optional (#16)
        if sounds:
            _write(d + "/sounds.json", json.dumps(sounds))
        tilemap = cart.get("map")                 # TileMap.to_hex() blob, optional (#32)
        if tilemap:
            _write(d + "/map.moymap", tilemap)
        flags = cart.get("flags")                 # tile flags (SPEC.md 3.5), optional
        if flags:
            _write(d + "/" + FLAGS_NAME, flags)
        images = cart.get("images")               # {name: .moyimg blob}, optional (#63)
        if images:
            _mkdir(d + "/" + IMAGES_DIR)
            for iname, iblob in images.items():
                _write(d + "/" + IMAGES_DIR + "/" + iname + IMAGE_EXT, iblob)
        blocks = cart.get("blocks")               # block program tree, optional (#29)
        if blocks:
            # a block-authored seed (tap_game) ships its blocks.json so it opens in
            # the on-device block editor as blocks, not just compiled code.
            _write(d + "/blocks.json", json.dumps(blocks))
        if scenes:                                # scene assets (#85), written last
            _mkdir(d + "/" + SCENES_DIR)
            for sname, sblob in scenes.items():
                _write(d + "/" + SCENES_DIR + "/" + sname + SCENE_EXT, sblob)
        if preserved:
            # restore the kid's saves + tuning AFTER the seed write, so config.json
            # holds their values (not the freshly-seeded defaults) and pmem survives.
            for name, data in preserved.items():
                _write(d + "/" + name, data)


# -- the PACKED seed roster (2026-08-30) -------------------------------------
#
# `seed_builtins` above takes cart DICTS, and on the console boards those come
# straight out of a frozen `carts_data.CARTS` -- 732 KB of literal source whose
# strings live in ROM and cost no heap. That representation is free on a board
# with the flash for it, and the Zero is the board without. Both forms were
# BUILT: the plain roster makes a 2,830,672 B image of a 2,883,584 B OTA slot,
# 51 KB left -- it fits, by less than 2%, under the #168 warning floor and one
# cart from a build failure, in a slot the board pays for TWICE.
#
# So the Zero froze `carts_data.CARTS_Z` instead: the SAME carts, one raw
# deflate stream each (tools/gen_device_carts.py --packed), which builds to
# 2,399,232 B and leaves 473 KB. The unit of work is ONE CART -- inflate it,
# hand it to `seed_builtins`, drop it -- because the roster inflates to 732 KB
# and no board should ever hold that at once.
#
# EVERY BOARD FREEZES THE PACKED ROSTER since 2026-08-30, and the argument on
# the three that fit is not the fit, it is the MARGIN: the roster only grows and
# a slot does not, so the board with the least room decides the form for all of
# them, and a lever that lives on one target is the lever the next port forgets.
# `seed_any` below is the one door a boot calls, so nothing else had to change.
#
# Nothing about the seed CONTRACT changes: the #47 version rules, the manifest
# regeneration, the preserved pmem/config all stay in `seed_builtins`, which is
# the one body that writes a cart to a store. This is a decoder in front of it.

# The deflate window the roster was compressed with. `tools/gen_device_carts.py`
# holds the writer's copy (SEED_WBITS) and tests/test_seed_pack.py pins the two
# equal -- a mismatch is not a crash, it is a wrong-looking inflate.
_SEED_WBITS = 15


def _packed_stream(blob):
    """A readable stream over one cart's raw-deflate blob.

    `deflate` is MicroPython's built-in inflater and the reason there is no
    `zlib` on a board at all (it replaced it in v1.21). CPython has no
    `deflate`, so the host reaches the same bytes through zlib's raw mode --
    which is what lets every host suite exercise THIS body rather than a twin.
    """
    import io as _io

    try:
        import deflate
    except ImportError:                  # CPython (host suites, the simulator)
        import zlib
        return _io.BytesIO(zlib.decompress(blob, -_SEED_WBITS))
    return deflate.DeflateIO(_io.BytesIO(blob), deflate.RAW, _SEED_WBITS)


def unpack_seed(blob):
    """One packed blob -> the cart dict `seed_builtins` takes.

    `json.load` over the inflating stream, NOT `json.loads(stream.read())`:
    read() materializes the whole inflated document beside the objects parsed
    out of it, and the parse allocates those anyway. MEASURED under the desktop
    MicroPython, as the smallest heap the whole roster seeds in -- 680 KB
    streaming against 896 KB for the read-all version, which is also ~40%
    slower. tests/test_seed_pack.py asserts at 768 KB, between the two.
    """
    return json.load(_packed_stream(blob))


def seed_packed(packed, root=CARTS_DIR, progress=None, only_new=False):
    """`seed_builtins` over a PACKED roster, one cart inflated at a time.

    `packed` is `[(title, version, blob)]`. The title and the version ride
    outside the blob so the #47 already-there check can be answered WITHOUT
    inflating: a board that is already seeded walks the whole roster doing 35
    directory stats and no decompression at all, which is what keeps this off
    the warm-boot path rather than merely cheap on it.

    `only_new` changes the skip rule from "already CURRENT" to "already THERE",
    and it is the Zero's (2026-08-30). On a console board the store is a CACHE
    of the image's built-ins, so #47 replaces a cart whose baked version is
    newer and accepts that on-device edits to a built-in's code are lost. On the
    Zero the store is the RECORD -- the only copy of a cart made in a browser,
    with a `moy_journal` history behind it -- and `seed_builtins` names a folder
    by the TITLE slug, so a version bump is exactly what would overwrite a kid's
    edited "Hop Quest". A cart that is not there yet has nothing to overwrite,
    which is the whole difference: this seeds what is MISSING and never rewrites
    what is present.

    Returns the number of carts actually written.
    """
    total = len(packed)
    written = 0
    for index, entry in enumerate(packed):
        title, version, blob = entry
        if progress is not None:
            try:
                progress(index, total, title)
            except Exception:             # noqa: BLE001 -- as in seed_builtins
                progress = None
        d = root + "/" + slug(title) + ".moy"
        if _exists(d) and (only_new or int(version) <= _cart_version(d)):
            continue
        # One cart in flight. seed_builtins gets a ONE-element list so every
        # rule it owns still applies -- and no progress hook, because the
        # counting is this loop's (it would report 1-of-1, 35 times).
        #
        # NO `gc.collect()` here, and that is MEASURED rather than assumed. The
        # obvious version collects after each cart to "make peak heap be one
        # cart"; under the desktop MicroPython the smallest heap the whole
        # roster seeds in is 680 KB either way, and the collecting version is
        # ~10% slower for it. The allocator already collects at the moment
        # peak matters -- when an allocation cannot be served -- and on a board
        # whose heap is megabytes of PSRAM a full scan per cart is a real cost
        # paid 35 times for a bound it does not move.
        seed_builtins([unpack_seed(blob)], root)
        written += 1
    return written


# -- which roster is this? ----------------------------------------------------
#
# Since every console board freezes the PACKED roster (2026-08-30), the two
# seeders both exist on every board and something has to choose. That choice
# lives HERE, in the module that owns both bodies, and not in the boot spine:
# it is a property of the DATA -- what `carts_data` was generated as -- and a
# board passing the wrong flag beside the right roster is a failure mode worth
# not having. A packed entry is a `(title, version, blob)` tuple; a plain one is
# a cart dict. Nothing else has ever been in a roster.


def is_packed(seed):
    """True if `seed` is a packed roster (`carts_data.CARTS_Z`)."""
    return bool(seed) and not isinstance(seed[0], dict)


def seed_any(seed, root=CARTS_DIR, progress=None):
    """Seed a roster of either form. The one call a board's boot makes."""
    if is_packed(seed):
        return seed_packed(seed, root, progress=progress)
    return seed_builtins(seed, root, progress=progress)


def embedded_floor(seed):
    """The read-only carts a board falls back to when it has NO writable store.

    Nearly unreachable since 2026-08-30: every board now retries on internal
    flash before it gets here (device_boot.load_carts `fallback_root`), so
    reaching this means the internal VFS itself is gone -- a board that cannot
    save anything at all. That is the only reason inflating the WHOLE roster is
    acceptable here: ~732 KB held at once, which every console board has in
    PSRAM and none should ever spend on a warm path. The Zero, whose store is
    the only thing it has, has no floor to fall to and does not call this.
    """
    if is_packed(seed):
        return [unpack_seed(blob) for _title, _version, blob in seed]
    return [dict(c) for c in seed]


def load(path):
    """Load one .moy folder into a cart dict, or None on error.

    A corrupt cart (bad manifest.json, missing main.py, or anything else
    unexpected) returns None instead of throwing, so one broken folder can never
    take down the gallery or the boot path. The whole body is also guarded so a
    surprise (e.g. a weird VFS error) still degrades to a skip, not a crash.

    Every optional asset is carried in its SERIALISED form -- `sprites`, `map`
    and `flags` are the file text, `None` when the file is absent -- and the
    live objects (SpriteSheet / TileMap / the 512-byte flag table) are built
    from them by `Project`. An absent `flags.moyflags` is `None` here and
    all-zero there, which is SPEC.md 3.5's own reading of a missing file."""
    try:
        try:
            # _read_recover falls back to manifest.json.bak so a crash mid-save
            # (or an interrupted atomic write) doesn't make the cart unreadable.
            man = json.loads(_read_recover(path + "/manifest.json"))
        except (OSError, ValueError) as exc:
            print("Moybyte cart manifest bad:", path, exc)
            return None
        if not isinstance(man, dict):
            print("Moybyte cart manifest not an object:", path)
            return None
        # A moy-spec cart (SPEC.md 3.1, "format": "moy-1") is Lua-by-definition
        # with spec defaults: main.lua, 30fps, a game. Its manifest never carries
        # the moybyte fields, so the defaults below flip on this flag -- moybyte's
        # own carts ("moybyte-cart-v1", or no format at all) keep theirs.
        spec = man.get("format") == "moy-1"
        try:
            src = _read_recover(path + "/" +
                                man.get("main", "main.lua" if spec else "main.py"))
        except OSError as exc:
            print("Moybyte cart main missing:", path, exc)
            return None
        cfg = dict(man.get("config", {}))
        try:
            cfg.update(json.loads(_read(path + "/config.json")))
        except (OSError, ValueError):
            pass
        try:
            sprites = _read(path + "/sprites.moygfx")   # PICO-8 __gfx__-style hex, optional
        except OSError:
            sprites = None
        try:
            sounds = json.loads(_read(path + "/sounds.json"))  # AudioBank, optional (#16)
        except (OSError, ValueError):
            sounds = None
        try:
            tilemap = _read(path + "/map.moymap")   # TileMap blob (#32), optional
        except OSError:
            tilemap = None
        try:
            flags = _read(path + "/" + FLAGS_NAME)  # tile flags (SPEC.md 3.5), optional
        except OSError:
            flags = None
        try:
            blocks = json.loads(_read(path + "/blocks.json"))  # block source (#29), optional
        except (OSError, ValueError):
            blocks = None
        images = load_images(path)                # paint-image assets (#63), {} if none
        scenes = load_scenes(path)                # scene assets (#85), {} if none
        tables = load_tables(path)                # Sheets docs (#78), {name: rows}, {} if none
        texts = load_texts(path)                  # Writer docs (#78), {name: lines}, {} if none
        return {
            "path": path,
            "title": man.get("title", "cart"),
            # Manifest metadata (#94 -- the Config-tab "CART INFO" editor):
            # optional, kid-editable. Absent on every seed cart + every
            # pre-#94 hand-authored one, so this defaults to "" (a blank author
            # reads as "not set", never crashes a display that prints it).
            "author": man.get("author", ""),
            "type": man.get("type", "game" if spec else "app"),
            # The #67 dual-runtime seam: which VM runs this cart ("python" today,
            # "lua" via the injected runtime), and which file `src` came from --
            # save_code/duplicate/seed must write THAT file back, never main.py.
            "runtime": man.get("runtime", "lua" if spec else "python"),
            "main": man.get("main", "main.lua" if spec else "main.py"),
            # 0 = pre-versioning (re-seedable). SPEC.md 3.1 leaves `version` to
            # the author, so a hand-typed "1.2" must read as unversioned rather
            # than take the cart down with it.
            "version": _int_or(man.get("version"), 0),
            # The manifest's declared format, carried so a rewrite puts back what
            # it found: a moy-spec cart duplicated here must stay a "moy-1" cart.
            # Restamping it "moybyte-cart-v1" would make it unrecognisable to
            # every other conforming host -- and silently reset its fps default.
            "format": man.get("format", CART_FORMAT),
            # Graduation (#29 / spec Section 8): a STORED, one-way project fact. Set
            # when a block-authored cart's code commit diverges past the block
            # vocabulary; makes the block editor read-only. Default False (absent =
            # not graduated). Un-set only through the undo journal (the grad rider).
            "graduated": bool(man.get("graduated", False)),
            "src": src,
            # Frame pacing (#63): a GAME cart locks to 30fps unless its manifest
            # says "fps": 60 (only carts that SUSTAIN 60 should -- frame_cap_fps).
            # Spec carts default to the spec's guaranteed tick (SPEC.md 5): 30.
            "fps": man.get("fps", 30 if spec else 0),
            # Cart-supplied palette (SPEC.md 2.2): 64 "RRGGBB" strings replacing
            # the default table for this cart's run, or None. Player applies it.
            "palette": man.get("palette"),
            # Required optional features (SPEC.md 10): Player refuses the cart
            # cleanly when one isn't implemented, instead of crashing mid-frame.
            "extensions": man.get("extensions", []),
            # The sheet tiles a launcher shows this cart by (SPEC.md 3.4):
            # (tile, w, h) or None to let the host choose. A POINTER into art the
            # cart already has -- no image, no codec, no reserved tiles.
            "icon": _normalize_icon(man.get("icon")),
            "cfg": cfg,
            "edit": man.get("edit", []),
            # Manifest capability permissions (#38): a cart only gets a gated API
            # (e.g. the injected `wifi`) when its permission is listed here. A
            # normal kid cart has just ["graphics","input"] (or none) and stays
            # network-less -- the sandbox is preserved.
            "permissions": man.get("permissions", []),
            # Input-kind hint (#42 Thread 3): which cart-API input groups this cart
            # actually reads, or None when undeclared (show every control -- see
            # _normalize_input_kinds above).
            "input": _normalize_input_kinds(man.get("input")),
            # Cart canvas (SPEC.md 1/3.1): (w, h) from the closed set, None for
            # the 320x240 default, or the raw out-of-set value Player.start
            # refuses by name -- see _normalize_canvas above.
            "canvas": _normalize_canvas(man.get("canvas")),
            "sprites": sprites,
            "sounds": sounds,
            "map": tilemap,
            # Tile flags (SPEC.md 3.5): the flags.moyflags text, or None when the
            # cart has no such file -- carried in the SERIALISED form like
            # sprites/map, and turned into the live 512-byte table by
            # Project._build_flags (absent -> all zero, which is what the spec
            # says an absent file means).
            "flags": flags,
            # Block source (#29): the program tree a cart was authored from in the
            # block editor, or None for a code-authored cart. main.py stays the
            # runnable source either way; blocks.json is the editable origin.
            "blocks": blocks,
            # Paint-image assets (#63 Fold 3): {name: .moyimg text} from images/, or {}.
            # A cart references one via the api's image(name) accessor and places it
            # with spr(img, x, y) -- a big MOY64 index bitmap (a painted background).
            "images": images,
            # Scene assets (#85 Variant A): {name: .moyscene text} from scenes/, or {}.
            # The manifest's assets.scenes is the ordered set (element 0 = default
            # active); Project builds a widgets.Scenes the cart reads via scene()/
            # load_scene() in _init. scene_names is that order (files not in the
            # manifest are appended sorted, so a hand-added scene still loads).
            "scenes": scenes,
            "scene_names": scene_names(man, scenes),
            # Desk Lab interop (#78): Sheets sheets ({name: rows}) + Writer docs
            # ({name: lines}) placed in the cart folder, read via table()/text().
            "tables": tables,
            "texts": texts,
        }
    except Exception as exc:  # noqa: BLE001  -- never let one bad cart escape
        print("Moybyte cart unreadable:", path, exc)
        return None


def scan(root=CARTS_DIR):
    """All carts found under root, sorted by folder name. Corrupt carts are
    skipped (load() returns None), and any per-entry surprise is swallowed so a
    single bad folder can't break the launcher.

    A cart is a FOLDER. A `.moy` FILE beside them is an archive -- how a cart
    travels, not how it is stored -- and is skipped silently: unpacking belongs
    to whatever brought it here, because a cart in an archive can't be edited,
    can't take an autosave commit, and can't hold its own undo journal or saves.
    (Before this, a `.moy` file was handed to load(), which opened it as a
    directory, failed, and reported it as a CORRUPT CART.)"""
    carts = []
    try:
        names = sorted(os.listdir(root))
    except OSError:
        return carts
    for name in names:
        if name.endswith(".moy") and _is_dir(root + "/" + name):
            try:
                c = load(root + "/" + name)
            except Exception as exc:  # noqa: BLE001  -- belt-and-braces over load()
                print("Moybyte cart scan skipped:", name, exc)
                c = None
            if c:
                carts.append(c)
    return carts


def save_config(cart):
    """Persist a cart's edited config back to its config.json (needs cart['path'])."""
    _write_atomic(cart["path"] + "/config.json", json.dumps(cart["cfg"]))


# --- graduation flag (#29 / spec Section 8): a stored, one-way project fact ---
#
# The `graduated` boolean lives in manifest.json (a project fact, not per-file
# data). _manifest_set_graduated is the low-level read-modify-write that the public
# setter AND the undo journal (journal_undo/redo, via the entry's `grad` rider)
# both use, so a graduation and its undo touch the manifest through one code path.
# It PRESERVES every other manifest field (title/version/edit/permissions/...) and
# writes atomically like every other save. A missing/bad manifest is a no-op.

def _manifest_set_graduated(cart_dir, value):
    """Set manifest.json's `graduated` flag to `value` (a bool), preserving all
    other fields. Returns True iff the manifest was rewritten (changed), False on a
    no-op or an unreadable/bad manifest. Atomic (its rename is the torn-write
    proofing)."""
    path = cart_dir + "/manifest.json"
    try:
        man = json.loads(_read_recover(path))
    except (OSError, ValueError):
        return False
    if not isinstance(man, dict):
        return False
    want = bool(value)
    if bool(man.get("graduated", False)) == want:
        return False                         # already at the target -> write nothing
    if want:
        man["graduated"] = True
    else:
        man.pop("graduated", None)           # absent == not graduated (keep manifests clean)
    _write_atomic(path, json.dumps(man))
    return True


def set_graduated(cart_or_path, value=True):
    """Public one-way graduation setter (spec Section 8). Persists the manifest flag
    and, when passed a cart dict, syncs cart['graduated'] so the open workspace
    reflects it immediately. `cart_or_path` is a cart dict or a .moy folder path.
    Returns True iff the manifest changed."""
    path = cart_or_path["path"] if isinstance(cart_or_path, dict) else cart_or_path
    changed = _manifest_set_graduated(path, value)
    if isinstance(cart_or_path, dict):
        cart_or_path["graduated"] = bool(value)
    return changed


# --- manifest metadata (title/author) editing (#94) --------------------------
#
# The Config tab's "CART INFO" mini-editor (cards_layer.py): a kid-editable
# title + author, the tracker's gap 2 ("Cart manifest / metadata editing --
# title, author, permissions not editable here"). `permissions` stays
# READ-ONLY -- it gates privileged system-app identity + the network
# capability (app_shell.py/artwork.py/appearance_app.py/calc_app.py check it,
# wifi.moy is the one network cart), so turning it into a free-form kid toggle
# is a separate, security-sensitive design question the tracker doesn't settle;
# title/author are the unambiguous, low-risk half of the gap.
#
# save_manifest_meta is the low-level read-modify-write, same shape as
# _manifest_set_graduated: preserves every other manifest field (version/edit/
# permissions/runtime/main/graduated/assets/...) and writes atomically. `title`/
# `author` are each optional (None = leave alone); an empty/whitespace title is
# the CALLER's job to reject (Project.commit_manifest does, before this ever
# runs) so an on-disk manifest can never lose its title.

def save_manifest_meta(cart_dir, title=None, author=None):
    """Update manifest.json's `title`/`author` fields, preserving every other
    field. Returns True iff the manifest was rewritten (changed), False on a
    no-op (both None / already match) or an unreadable/bad manifest."""
    path = cart_dir + "/manifest.json"
    try:
        man = json.loads(_read_recover(path))
    except (OSError, ValueError):
        return False
    if not isinstance(man, dict):
        return False
    changed = False
    if title is not None and man.get("title") != title:
        man["title"] = title
        changed = True
    if author is not None and man.get("author", "") != author:
        man["author"] = author
        changed = True
    if not changed:
        return False
    _write_atomic(path, json.dumps(man))
    return True


# save_code() outcomes -- the caller (Workstation) surfaces these to the kid:
SAVE_OK = "ok"            # source parsed and was written atomically
SAVE_BAD_SYNTAX = "bad"   # source won't compile; the good file was left untouched


def compile_check(src):
    """Return (ok, message). ok is True when `src` is valid Python; otherwise
    message is a short human-readable syntax-error string. Uses compile() (no
    exec), which exists on both CPython and MicroPython."""
    try:
        compile(src, "<cart>", "exec")
        return True, ""
    except SyntaxError as exc:
        msg = getattr(exc, "msg", None) or str(exc)
        lineno = getattr(exc, "lineno", None)
        if lineno:
            return False, "line %d: %s" % (lineno, msg)
        return False, str(msg)
    except Exception as exc:  # noqa: BLE001  -- MicroPython may raise plain ValueError
        return False, str(exc)


def save_code(cart, src):
    """Persist edited source to the cart's main file, ATOMICALLY and only if it
    compiles. Returns (status, message): status is SAVE_OK on success, or
    SAVE_BAD_SYNTAX with a message (and the previous good file is left intact)
    when `src` won't parse, so a kid's broken edit can never truncate the cart.

    compile_check is the PYTHON compiler, so it only gates python-runtime carts;
    a "lua" cart (#67) saves unchecked -- its syntax errors surface at PLAY
    through the runtime's own load error -> the cart-error panel. (A Lua-side
    pre-save check is the Phase 5 polish, needs the runtime present to check.)"""
    if cart.get("runtime", "python") == "python":
        ok, msg = compile_check(src)
        if not ok:
            return SAVE_BAD_SYNTAX, msg
    _write_atomic(cart["path"] + "/" + cart.get("main", "main.py"), src)
    cart["src"] = src
    return SAVE_OK, ""


def save_sprites(cart, hex_text):
    """Persist the sprite sheet (PICO-8 __gfx__-style hex) to sprites.moygfx,
    atomically so an interrupted write can't truncate the real file."""
    _write_atomic(cart["path"] + "/sprites.moygfx", hex_text)
    cart["sprites"] = hex_text


def save_sounds(cart, bank_dict):
    """Persist a cart's sound bank (AudioBank.to_dict()) to sounds.json (#16),
    atomically so an interrupted write can't truncate the real file. `bank_dict` is
    plain JSON-able data ({"sfx": [...], "music": [...]})."""
    _write_atomic(cart["path"] + "/sounds.json", json.dumps(bank_dict))
    cart["sounds"] = bank_dict


def save_map(cart, hex_text):
    """Persist a cart's tilemap (TileMap.to_hex() blob) to map.moymap (#32),
    atomically so an interrupted write can't truncate the real file."""
    _write_atomic(cart["path"] + "/map.moymap", hex_text)
    cart["map"] = hex_text


# --- the undo/redo journal (#7) -- extracted to moy_journal.py ---------------
#
# The durable, per-project, reboot-surviving undo history (journal.jsonl +
# full-file snapshots + the atomic cursor). Imported back + re-exported so the
# `store.journal_*` call sites (project.py / console.py) and the tests are
# unchanged; the design doc lives at the top of moy_journal.py.
try:
    from moy_journal import (JOURNAL_DIR, JOURNAL_LOG, JOURNAL_CURSOR,
                             JOURNAL_SNAP_DIR, JOURNAL_MAX_ENTRIES,
                             JOURNAL_MAX_BYTES, journal_append, journal_undo,
                             journal_redo, journal_can_undo, journal_can_redo,
                             journal_compact, journal_entry_ops,
                             _journal_paths, _journal_load_entries,
                             _journal_cursor, _journal_current_snap,
                             _journal_total_bytes)
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime.moy_journal import (JOURNAL_DIR, JOURNAL_LOG, JOURNAL_CURSOR,
                                     JOURNAL_SNAP_DIR, JOURNAL_MAX_ENTRIES,
                                     JOURNAL_MAX_BYTES, journal_append,
                                     journal_undo, journal_redo,
                                     journal_can_undo, journal_can_redo,
                                     journal_compact, journal_entry_ops,
                                     _journal_paths, _journal_load_entries,
                                     _journal_cursor, _journal_current_snap,
                                     _journal_total_bytes)


def _import_blocks():
    """Import the blocks compiler under whichever name it's known by: bare
    `blocks` on the device (frozen top-level) and on the host once host_app has
    aliased it, or `runtime.blocks` when a test imports moy_carts directly. The
    device path is plain `import blocks` (MicroPython has no packages here)."""
    try:
        import blocks
        return blocks
    except ImportError:
        from runtime import blocks
        return blocks


# --- block source (#29: the block editor's blocks.json) ---------------------
#
# A cart authored in the block editor carries its program tree as blocks.json
# beside main.py. blocks.json is the EDITABLE origin; main.py is the runnable
# source the compiler emits from it. load_blocks reads the tree (None if a cart
# has no block source -- i.e. it was code-authored); save_blocks compiles the
# tree to main.py and persists BOTH (atomically, same crash-safe path as the
# other saves) so the block source and the runnable code can never drift.

def load_blocks(cart_or_path):
    """Read a cart's block program (the blocks.json tree), or None if there is
    none / it's unreadable. Accepts a cart dict or a .moy folder path."""
    path = cart_or_path["path"] if isinstance(cart_or_path, dict) else cart_or_path
    try:
        return json.loads(_read(path + "/blocks.json"))
    except (OSError, ValueError):
        return None


def save_blocks(cart, program):
    """Persist a cart's block program to blocks.json AND compile it to main.py,
    so the block source and the runnable code stay in lockstep (compile-on-save).

    Returns (status, message): SAVE_OK once both files are written, or
    SAVE_BAD_SYNTAX with a message if the compiled source won't parse or the
    program is malformed (a BlockError) -- in which case NEITHER file is touched,
    so a corrupt edit can never truncate the cart or strand a broken main.py.
    Writes blocks.json first then main.py; both are atomic."""
    blocks = _import_blocks()
    try:
        src = blocks.compile_blocks(program)
    except Exception as exc:            # noqa: BLE001 -- BlockError / bad tree
        return SAVE_BAD_SYNTAX, str(exc)
    ok, msg = compile_check(src)        # belt-and-braces: the emitted code must parse
    if not ok:
        return SAVE_BAD_SYNTAX, msg
    text = json.dumps(program)
    _write_atomic(cart["path"] + "/blocks.json", text)
    # A DEEP COPY, never the live tree (#93): the block editor keeps mutating
    # `program` in place after this save, and its undo/redo REBINDS its own
    # program to restored snapshots -- an aliased cart["blocks"] would drift to a
    # state matching neither the disk file nor the editor (and the graduation
    # compare reads cart["blocks"]). The json round-trip reuses the text already
    # serialized for the file, so the snapshot is exactly what was written.
    cart["blocks"] = json.loads(text)
    _write_atomic(cart["path"] + "/main.py", src)
    cart["src"] = src
    return SAVE_OK, ""


# --- persistent cart memory (pmem, TIC-80-style) ----------------------------
#
# A cart's pmem is 256 x SIGNED 32-bit ints stored as a JSON list in pmem.json
# beside main.py. TIC-80 gives carts pmem(i)/pmem(i,v) for high scores / save
# state; this is the per-cart backing store. Reads default to all-zero; saves
# are atomic (same crash-safe path as sprites/config) so an interrupted write
# can never truncate a kid's save.

PMEM_CELLS = 256
PMEM_MASK = 0xFFFFFFFF
PMEM_SIGN = 0x80000000       # slots are SIGNED 32-bit (SPEC.md 9, matching 4.2)


def load_pmem(path):
    """Read a cart's pmem (path = the .moy folder). Returns a list of 256 ints,
    all zero when there's no pmem.json yet or it's unreadable/short (padded)."""
    cells = [0] * PMEM_CELLS
    try:
        data = json.loads(_read(path + "/pmem.json"))
    except (OSError, ValueError):
        return cells
    if isinstance(data, list):
        for i in range(min(PMEM_CELLS, len(data))):
            try:
                v = int(data[i]) & PMEM_MASK
            except (TypeError, ValueError):
                v = 0
            # Slots are signed 32-bit. Reinterpreting is also the migration for
            # saves written before that was pinned down: a negative a cart wrote
            # back then landed as a huge unsigned value, and this restores it.
            cells[i] = v - (PMEM_MASK + 1) if v >= PMEM_SIGN else v
    return cells


def save_pmem(cart, cells):
    """Persist a cart's pmem list (256 ints) to pmem.json, atomically so an
    interrupted write can't truncate it (needs cart['path'])."""
    _write_atomic(cart["path"] + "/pmem.json", json.dumps(list(cells)))


# --- shared sprite sheet (cross-cart sprite reuse, #18) ---------------------

def shared_sheet_path(root=CARTS_DIR):
    """Well-known path of the shared sprite sheet."""
    return _sibling_path(root, SHARED_SHEET_NAME)


def load_shared_sheet(root=CARTS_DIR):
    """Read the shared sprite sheet's hex (PICO-8 __gfx__-style), or None if it
    has never been saved. Caller turns it into a SpriteSheet via from_hex."""
    return _read_sibling(root, SHARED_SHEET_NAME)


def save_shared_sheet(hex_text, root=CARTS_DIR):
    """Persist the shared sprite sheet's hex -- it's the highest-value shared
    asset, so the atomic sibling write matters most here."""
    _write_sibling(root, SHARED_SHEET_NAME, hex_text)


# --- system icon theme (the unified top bar, Stage 1) -----------------------
#
# The top-bar icons render from an EDITABLE 16x16 IconSheet rather than hardcoded
# glyphs (so the bar is themeable). Its theme persists to a single system_icons.moygfx
# that lives BESIDE the carts dir (a sibling of `root`, like shared.moygfx) -- it is
# system state, not tied to any cart. Same PICO-8 __gfx__-style hex format as the
# sprite sheets. NOT seeded eagerly: a missing file means "use the baked default
# theme" (load returns None), so the absent-file case is the common one. A save only
# happens once on-device icon editing lands (Stage 2).

SYSTEM_ICONS_NAME = "system_icons.moygfx"
SYSTEM_ICONS_VER_NAME = "system_icons.ver"   # the saved theme's icon-set version (#47-style)


def system_icons_path(root=CARTS_DIR):
    """Well-known path of the system icon theme."""
    return _sibling_path(root, SYSTEM_ICONS_NAME)


def system_icons_version_path(root=CARTS_DIR):
    """Sidecar holding the icon-set version the saved theme was written at (a sibling
    of system_icons.moygfx). Lets a newer baked icon set re-seed a stale saved theme."""
    return _sibling_path(root, SYSTEM_ICONS_VER_NAME)


def load_system_icons(root=CARTS_DIR):
    """Read the system icon theme's hex (PICO-8 __gfx__-style), or None if it has
    never been saved -- in which case the caller uses the baked default IconSheet.
    Caller turns the hex into an IconSheet via IconSheet.from_hex."""
    return _read_sibling(root, SYSTEM_ICONS_NAME)


def load_system_icons_version(root=CARTS_DIR):
    """The icon-set version of the saved theme (0 when absent/unreadable, so a
    pre-versioning theme always counts as stale and is re-seeded by a versioned set --
    mirrors _cart_version)."""
    try:
        return int(_read(system_icons_version_path(root)).strip())
    except (OSError, ValueError, AttributeError):
        return 0


def save_system_icons(hex_text, root=CARTS_DIR, version=0):
    """Persist the system icon theme's hex (Stage 2 editing / a default re-seed) plus
    its version sidecar. Ensures the parent dir exists. Written atomically (like the
    shared sheet) -- a shared system asset whose interrupted write must never truncate."""
    ensure_dirs(root)
    _write_atomic(system_icons_path(root), hex_text)
    _write_atomic(system_icons_version_path(root), str(int(version)))


# --- known WiFi networks (system credential store, #38) ---------------------
#
# The WiFi service persists known networks (ssid + password) to a single system
# JSON that lives BESIDE the carts dir (a sibling of `root`, like the shared
# sheet) -- it is system state, not tied to any cart. The injected `wifi` API
# (permission-gated) drives load/save here; the device autoconnects from this at
# boot. Written atomically (same crash-safe path as the cart saves) so an
# interrupted write can never truncate a kid's saved passwords. MicroPython-safe
# (json + os only).

WIFI_STORE_NAME = "wifi.json"


def wifi_store_path(root=CARTS_DIR):
    """Well-known path of the WiFi credential store."""
    return _sibling_path(root, WIFI_STORE_NAME)


def _wifi_networks(data):
    """The known-networks list out of a parsed wifi.json, or None when the document
    isn't one (so _load_store_json falls through to the backup). Entries with no
    ssid are dropped (see save_wifi)."""
    nets = data.get("networks") if isinstance(data, dict) else None
    if not isinstance(nets, list):
        return None
    out = []
    for n in nets:
        if isinstance(n, dict) and n.get("ssid"):
            out.append({"ssid": str(n["ssid"]), "password": str(n.get("password", ""))})
    return out


def load_wifi(root=CARTS_DIR):
    """Read the known-networks list: [{"ssid": str, "password": str}, ...].
    Returns [] only when nothing usable has ever been saved: a corrupt store must
    never crash the boot autoconnect, and must not be reported as an empty one
    either -- see _load_store_json."""
    return _load_store_json(wifi_store_path(root), _wifi_networks) or []


def save_wifi(networks, root=CARTS_DIR):
    """Persist the known-networks list, atomically. Ensures the parent dir exists.
    `networks` is a list of {"ssid", "password"} dicts. An entry with a BLANK ssid
    is dropped: there is no such network, and one would sit at the front of the
    list that boot autoconnect walks."""
    ensure_dirs(root)
    clean = [{"ssid": str(n["ssid"]), "password": str(n.get("password", ""))}
             for n in networks if n.get("ssid")]
    _write_atomic(wifi_store_path(root), json.dumps({"networks": clean}))


def remember_wifi(ssid, password, root=CARTS_DIR):
    """Add/replace one network in the store (by ssid) and persist. Returns the
    updated list -- what is actually STORED, so a caller can trust it. The
    most-recently-remembered network is moved to the FRONT, so autoconnect prefers
    the last one the kid joined.

    A blank ssid, and a re-remember of the network already at the front with the
    same password, both write NOTHING: save_wifi would drop the first anyway, the
    second is what the panel's known-network reconnect and every boot autoconnect
    do, and each rewrite is another _write_atomic crash window."""
    ssid = str(ssid)
    nets = load_wifi(root)
    if not ssid:
        return nets
    password = str(password or "")
    if nets and nets[0]["ssid"] == ssid and nets[0]["password"] == password:
        return nets
    nets = [n for n in nets if n["ssid"] != ssid]
    nets.insert(0, {"ssid": ssid, "password": password})
    save_wifi(nets, root)
    return nets


def forget_wifi(ssid, root=CARTS_DIR):
    """Drop one network from the store (by ssid) and persist. Returns the updated
    list (unchanged if the ssid wasn't known). Forgetting a network that isn't
    there writes NOTHING -- a full atomic rewrite, and its crash window, for a
    no-op."""
    ssid = str(ssid)
    nets = load_wifi(root)
    kept = [n for n in nets if n["ssid"] != ssid]
    if len(kept) != len(nets):
        save_wifi(kept, root)
    return kept


# --- system settings (desktop shell, #28) -----------------------------------
#
# A single system-level config (wallpaper choice, later volume/brightness/name/
# theme) that is NOT tied to any one cart -- today config is per-cart only
# (cart["cfg"] / save_config). It lives BESIDE the carts dir (a sibling of
# `root`, like wifi.json and the shared sheet) so it is system state. Written
# atomically (same crash-safe path as the cart saves) so an interrupted write can
# never truncate the kid's settings, and written through the same with_sd_live
# path on device. MicroPython-safe (json + os only).

SYSTEM_STORE_NAME = "system.json"


def system_store_path(root=CARTS_DIR):
    """Well-known path of the system settings store."""
    return _sibling_path(root, SYSTEM_STORE_NAME)


def load_system(root=CARTS_DIR):
    """Read the system settings dict. Returns {} when nothing usable has ever been
    saved (a corrupt store must never crash boot); recovers from the .bak like
    load_wifi, so a half-published file is not read as "no settings"."""
    return _load_store_json(system_store_path(root),
                            lambda d: d if isinstance(d, dict) else None) or {}


def save_system(settings, root=CARTS_DIR):
    """Persist the system settings dict, atomically. Ensures the parent dir
    exists. `settings` is plain JSON-able data."""
    ensure_dirs(root)
    _write_atomic(system_store_path(root), json.dumps(dict(settings)))


# --- achievements (#21) -----------------------------------------------------
#
# The set of unlocked achievement ids (fun milestones a kid hits naturally, plus
# the hidden Easter-egg rewards) persists to a single achievements.json that lives
# BESIDE the carts dir (a sibling of `root`, like system.json/wifi.json/the shared
# sheet) -- it is system state, not tied to any cart. Stored as {"unlocked": [id,
# ...]}; the catalog of what each id MEANS lives in the shared console (host ==
# device). Written atomically (same crash-safe path as the cart saves) so an
# interrupted write can never lose a kid's earned badges. MicroPython-safe (json +
# os only).

ACHIEVEMENTS_STORE_NAME = "achievements.json"


def achievements_store_path(root=CARTS_DIR):
    """Well-known path of the achievements store."""
    return _sibling_path(root, ACHIEVEMENTS_STORE_NAME)


def _unlocked_ids(data):
    """The unlocked-id list out of a parsed achievements.json, or None when the
    document isn't one (so _load_store_json falls through to the backup)."""
    ids = data.get("unlocked") if isinstance(data, dict) else None
    if not isinstance(ids, list):
        return None
    out = []
    seen = {}
    for i in ids:
        if isinstance(i, str) and i not in seen:
            seen[i] = True
            out.append(i)
    return out


def load_achievements(root=CARTS_DIR):
    """Read the unlocked achievement ids as a list. Returns [] when nothing usable
    has ever been saved (a corrupt store must never crash boot); recovers from the
    .bak like load_wifi. Duplicates/non-strings are dropped so the loaded list is
    clean."""
    return _load_store_json(achievements_store_path(root), _unlocked_ids) or []


def save_achievements(unlocked, root=CARTS_DIR):
    """Persist the unlocked achievement ids (a list of strings), atomically.
    Ensures the parent dir exists."""
    ensure_dirs(root)
    clean = []
    seen = {}
    for i in unlocked:
        s = str(i)
        if s not in seen:
            seen[s] = True
            clean.append(s)
    _write_atomic(achievements_store_path(root), json.dumps({"unlocked": clean}))


# --- cart management (create / duplicate / delete) --------------------------

# A friendly starter cartridge: an editable colored dot, as a GAME project. The
# Editor's project-picker "+ New" tile creates one of these and opens it in the Editor
# (spec shell_ux_v1.md) -- `type=="game"` so a kid's brand-new creation is a real
# game project with "Make it mine" cards, not a wallpaper.
NEW_TEMPLATE = {
    "type": "game",
    "src": (
        "def _draw():\n"
        "    cls(col(cfg('bg', 'dark_blue')))\n"
        "    circ(W // 2, H // 2, cfg('size', 24), col(cfg('color', 'yellow')))\n"
        "    print('MY NEW CART', 20, 20, col('white'), 2)\n"
    ),
    "cfg": {"bg": "dark_blue", "color": "yellow", "size": 24},
    "edit": [
        {"key": "bg", "type": "choice", "choices": ["dark_blue", "black", "dark_purple", "indigo"], "card": "SKY IS {value}"},
        {"key": "color", "type": "choice", "choices": ["yellow", "red", "green", "blue", "pink"], "card": "DOT IS {value}"},
        {"key": "size", "type": "int", "min": 4, "max": 80, "step": 4, "card": "DOT SIZE {value}"},
    ],
}


def _is_dir(path):
    try:
        return (os.stat(path)[0] & 0x4000) != 0
    except OSError:
        return False


def _unique_dir(root, base):
    d = root + "/" + base + ".moy"
    if not _exists(d):
        return d
    i = 2
    while _exists(root + "/" + base + "_" + str(i) + ".moy"):
        i += 1
    return root + "/" + base + "_" + str(i) + ".moy"


def create(title, root=CARTS_DIR, src=None, cfg=None, edit=None, type="app",
           runtime="python", main="main.py", scenes=None, scene_order=None,
           author=None, palette=None, extensions=None, format=None, fps=None,
           icon=None, canvas=None):
    """Create a new .moy folder and return its loaded cart dict. `runtime`/`main`
    default to a python cart; duplicate() passes a source cart's through so a
    copied "lua" cart (#67) stays a lua cart with its source in main.lua. `scenes`
    ({name: .moyscene text}) + `scene_order` copy a source cart's scene assets (#85),
    registered in manifest.assets.scenes and written under scenes/. `author` (#94)
    is optional -- omitted entirely when blank, so a fresh/duplicated cart's
    manifest stays as clean as before this field existed. `format`/`fps` are
    duplicate()'s passthrough (SPEC.md 3.1): copying a spec cart must yield
    another "moy-1" cart at its declared tick, not a restamped moybyte one."""
    d = _unique_dir(root, slug(title))
    _mkdir(d)
    manifest = {
        "format": format or CART_FORMAT, "title": title, "type": type,
        "runtime": runtime, "main": main, "edit": edit or [],
    }
    if fps:                # 0 is moybyte's "unset" app default -- never written
        manifest["fps"] = fps
    if author:
        manifest["author"] = author
    if palette:                       # cart-supplied palette (SPEC.md 2.2) survives a copy
        manifest["palette"] = list(palette)
    if extensions:                    # required extensions (SPEC.md 10) survive a copy
        manifest["extensions"] = list(extensions)
    if icon:                          # launcher icon tiles (SPEC.md 3.4) survive a copy
        manifest["icon"] = list(icon)
    if canvas is not None:            # cart canvas (SPEC.md 1/3.1) survives a copy
        manifest["canvas"] = _canvas_str(canvas)
    if scenes:                        # scene assets (#85): register + write (see above)
        manifest["assets"] = {"scenes": list(scene_order or sorted(scenes.keys()))}
    _write(d + "/manifest.json", json.dumps(manifest))
    _write(d + "/" + main, src if src is not None else NEW_TEMPLATE["src"])
    _write(d + "/config.json", json.dumps(cfg or {}))
    if scenes:
        _mkdir(d + "/" + SCENES_DIR)
        for sname, sblob in scenes.items():
            _write(d + "/" + SCENES_DIR + "/" + sname + SCENE_EXT, sblob)
    return load(d)


def new_from_template(root=CARTS_DIR, title="New Cart"):
    return create(title, root, src=NEW_TEMPLATE["src"], cfg=dict(NEW_TEMPLATE["cfg"]),
                  edit=NEW_TEMPLATE["edit"], type=NEW_TEMPLATE["type"])


# Files create() has already written for the copy, or that must NOT follow one.
# Everything else in a cart folder is project content and gets copied verbatim.
#   manifest/config/main -- create() wrote the COPY's own (new title, version, format)
#   journal*             -- the ORIGINAL's undo history; inheriting it would let a
#                           copy "undo" into the source project's edits
#   pmem.json            -- the original's save state, not the new project's
#   thumbs/              -- a regenerable preview cache, stamped against the source
def _dup_skip(name, main):
    return (name in ("manifest.json", "manifest.json.bak", "config.json",
                     "pmem.json", main, THUMBS_DIR, JOURNAL_DIR, JOURNAL_LOG,
                     JOURNAL_CURSOR, JOURNAL_SNAP_DIR)
            or name.startswith("journal"))


def _copy_cart_files(src, dst, main):
    """Copy a cart folder's asset files (one level of subfolders -- images/,
    scenes/, tables/, docs/) into a fresh copy. Degrade-don't-throw like load():
    an unreadable entry is skipped, never fatal, so a copy can lose one asset but
    never fail outright."""
    try:
        names = os.listdir(src)
    except OSError:
        return
    for name in names:
        if _dup_skip(name, main):
            continue
        s, d = src + "/" + name, dst + "/" + name
        try:
            kids = os.listdir(s)         # a subfolder (images/, scenes/, ...)
        except OSError:
            try:
                _write(d, _read(s))      # a plain file
            except (OSError, ValueError, UnicodeError):
                pass
            continue
        _mkdir(d)
        for kid in kids:
            try:
                _write(d + "/" + kid, _read(s + "/" + kid))
            except (OSError, ValueError, UnicodeError):
                pass


def duplicate(cart, root=CARTS_DIR, new_title=None):
    dup = create(new_title or (cart["title"] + " copy"), root,
                 src=cart["src"], cfg=dict(cart["cfg"]), edit=cart["edit"], type=cart["type"],
                 runtime=cart.get("runtime", "python"), main=cart.get("main", "main.py"),
                 scenes=dict(cart.get("scenes") or {}),      # #85: copy scene assets +
                 scene_order=list(cart.get("scene_names") or []),  # their manifest order
                 author=cart.get("author") or None,          # #94: carry the author over
                 palette=cart.get("palette"),                # spec 2.2 palette +
                 extensions=cart.get("extensions"),          # spec 10 extensions carry over
                 format=cart.get("format"),                  # spec 3.1: a moy-1 copy
                 fps=cart.get("fps"),                        # stays moy-1 at its tick
                 icon=cart.get("icon"),                      # spec 3.4 launcher art
                 canvas=cart.get("canvas"))                  # spec 1/3.1 cart canvas
    # create() writes the CODE side of a project (manifest/main/config/scenes).
    # Everything else a cart owns -- sprites.moygfx, map.moymap, sounds.json,
    # blocks.json, images/, tables/, docs/ -- lived only in the source FOLDER, so
    # a copy used to arrive with just its code: the picker's COPY silently threw
    # away the sprite sheet, tilemap, sounds and cover art of every project it
    # duplicated. Copying the files (rather than re-serialising the loaded dict)
    # also keeps assets this module doesn't model, which is the only lossless
    # answer for a cart authored by a newer build.
    src_path = cart.get("path")
    if src_path and dup is not None:
        _copy_cart_files(src_path, dup["path"], dup["main"])
        return load(dup["path"])
    return dup


def delete(cart):
    _rmtree(cart["path"])


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


# --- user files (#108): the kid's creations as real files -------------------
#
# Creations that outlive any one app or cart (a Paint drawing, a Writer doc, a
# recorded voice set) live under ONE visible root BESIDE the carts dir --
# files/<kind>/<name><ext> -- real folders with real names on the card, so the
# same stuff a File Manager shows is what a PC sees on the mounted SD. Kinds
# are flat (no nesting in v1) and kind-homed like every desktop OS's known
# folders. Carts never reference these: reuse is copy-on-use through the
# existing attach verbs (the one exception, recordings, is used-by-name and
# never copied INTO a cart -- #70's privacy rule). Delete moves to
# files/trash/<kind>/ (restorable; pruned by count), never destroys directly.

FILES_DIR = "files"
TRASH_DIR = "trash"
TRASH_KEEP = 50          # prune the trash's oldest entries beyond this many

# kind -> (extension, folder_valued, auto-name base). A folder-valued kind
# (#70 recordings) holds one DIRECTORY per item (the macOS-bundle model); file
# kinds hold one flat file per item. Every store verb below validates against
# this registry, so an unknown kind is a loud ValueError, not a stray dir.
FILE_KINDS = {
    "drawings":   (IMAGE_EXT, False, "drawing"),
    "docs":       (TEXT_EXT, False, "doc"),
    "tables":     (TABLE_EXT, False, "table"),
    "sprites":    (".moygfx", False, "sheet"),
    "music":      (".moysong", False, "song"),
    "recordings": ("", True, "recording"),
}


def _kind_spec(kind):
    try:
        return FILE_KINDS[kind]
    except KeyError:
        raise ValueError("unknown file kind: " + str(kind))


def files_root(root=CARTS_DIR):
    """The user-files root, beside the carts directory (like shared.moygfx)."""
    return _sibling_path(root, FILES_DIR)


def file_kind_dir(kind, root=CARTS_DIR):
    _kind_spec(kind)
    return files_root(root) + "/" + kind


def file_path(kind, name, root=CARTS_DIR):
    ext = _kind_spec(kind)[0]
    return file_kind_dir(kind, root) + "/" + name + ext


def _ensure_kind_dir(kind, root):
    ensure_dirs(root)
    _mkdir(files_root(root))
    d = file_kind_dir(kind, root)
    _mkdir(d)
    return d


def _mtime(path):
    try:
        return os.stat(path)[8]
    except OSError:
        return 0


def _kind_entries(d, ext, folder_valued):
    """[(name, mtime)] of the kind's items in `d`, newest first (mtime is
    best-effort -- 0 on filesystems without one, leaving alphabetical order).
    Skips the atomic-write machinery's .tmp/.bak orphans by construction: a
    file item must end with the kind's extension exactly."""
    try:
        names = os.listdir(d)
    except OSError:
        return []
    out = []
    for n in names:
        p = d + "/" + n
        if folder_valued:
            if _is_dir(p):
                out.append((n, _mtime(p)))
        elif n.endswith(ext) and len(n) > len(ext) and not _is_dir(p):
            out.append((n[:-len(ext)] if ext else n, _mtime(p)))
    out.sort(key=lambda e: (-e[1], e[0]))
    return out


def list_files(kind, root=CARTS_DIR):
    """The kind's item names, newest first."""
    ext, folder_valued, _base = _kind_spec(kind)
    entries = _kind_entries(file_kind_dir(kind, root), ext, folder_valued)
    return [n for n, _m in entries]


def count_files(kind, root=CARTS_DIR):
    """How many items the kind holds -- a bare listdir filter, so the Files
    kinds screen never pays list_files' per-item stat+sort just for a badge."""
    ext, folder_valued, _base = _kind_spec(kind)
    d = file_kind_dir(kind, root)
    try:
        names = os.listdir(d)
    except OSError:
        return 0
    if folder_valued:
        return sum(1 for n in names if _is_dir(d + "/" + n))
    return sum(1 for n in names if n.endswith(ext) and len(n) > len(ext))


def load_file(kind, name, root=CARTS_DIR):
    """A file item's text, or None if missing/unreadable (degrade-don't-throw,
    like every asset loader). Folder-valued kinds have no single blob."""
    if _kind_spec(kind)[1]:
        return None
    try:
        return _read(file_path(kind, name, root))
    except OSError:
        return None


def _unique_name(kind, name, root, path=None):
    """`name` if free under `path` (default: the kind's live dir), else name_2,
    name_3, ... -- the ONE collision probe every rename/duplicate/trash move
    rides on (`path` swaps in _trash_path for the trash side)."""
    path = path or file_path
    if not _exists(path(kind, name, root)):
        return name
    i = 2
    while _exists(path(kind, name + "_" + str(i), root)):
        i += 1
    return name + "_" + str(i)


def new_file_name(kind, root=CARTS_DIR, base=None):
    """The next free auto-name for the kind (drawing_1, drawing_2, ...) --
    creations are auto-named so naming is never a gate; rename is optional."""
    _ext, _fv, kind_base = _kind_spec(kind)
    base = slug(base) if base else kind_base
    i = 1
    while _exists(file_path(kind, base + "_" + str(i), root)):
        i += 1
    return base + "_" + str(i)


def save_file(kind, name, text, root=CARTS_DIR):
    """Persist one file item atomically (folder-valued kinds are written by
    their own tools, never through this). Returns the (slugged) stored name."""
    if _kind_spec(kind)[1]:
        raise ValueError(kind + " items are folders; write them in place")
    name = slug(name)
    _ensure_kind_dir(kind, root)
    _write_atomic(file_path(kind, name, root), text)
    return name


# --- op-history sidecars (#111): keyframe + op segments per user file --------
#
# The #111 keyframe+ops undo model for Desk Lab apps (Paint/Writer/Sheets). A
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
    failure mode), every good record before it survives; a missing sidecar -> []."""
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
    new = slug(new_title)
    if new == name:
        return name
    new = _unique_name(kind, new, root)
    os.rename(file_path(kind, name, root), file_path(kind, new, root))
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
    return _trash_dir(kind, root) + "/" + name + _kind_spec(kind)[0]


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
        for n, m in _kind_entries(_trash_dir(kind, root), ext, folder_valued):
            out.append((kind, n, m))
    out.sort(key=lambda e: (-e[2], e[0], e[1]))
    return [(k, n) for k, n, _m in out]


def restore_file(kind, name, root=CARTS_DIR):
    """Move a trashed item back into its kind dir (unique-ified against what
    was made since). Returns the restored name."""
    _ensure_kind_dir(kind, root)
    new = _unique_name(kind, name, root)
    os.rename(_trash_path(kind, name, root), file_path(kind, new, root))
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


def migrate_user_files(root=CARTS_DIR):
    """One-shot #108 migration: the legacy single-slot artwork.moyimg becomes
    files/drawings/my_art.moyimg. Runs only while files/drawings/ does not
    exist yet (its existence is the migrated marker), so a kid who later
    empties the kind never sees the legacy drawing resurrected. The legacy
    file is left in place -- older builds keep booting against it."""
    if _exists(file_kind_dir("drawings", root)):
        return None
    blob = load_artwork(root)
    if not blob:
        return None
    return save_file("drawings", "my_art", blob, root)


def migrate_docs(root=CARTS_DIR):
    """One-shot #108 migration: the legacy single-file Writer notebook
    (notes.json, a list of {title, body}) becomes one files/docs/<name>.moytext
    per note. Gated on files/docs/ not existing yet (its own marker, like
    drawings/), so an emptied Docs kind is never resurrected. The legacy
    notes.json is left in place (older builds keep reading it). No-op when there
    is nothing to migrate; returns the list of made names, or None."""
    if _exists(file_kind_dir("docs", root)):
        return None
    blob = load_notes(root)
    if not blob:
        return None
    try:
        data = json.loads(blob)
    except (ValueError, TypeError):
        return None
    notes = data.get("notes") if isinstance(data, dict) else None
    if not isinstance(notes, list) or not notes:
        return None
    made = []
    for n in notes:
        if not isinstance(n, dict):
            continue
        body = str(n.get("body", ""))
        if not body.strip():
            continue
        title = ""
        for ln in body.split("\n"):
            if ln.strip():
                title = ln.strip()
                break
        name = new_file_name("docs", root, base=title or None)
        text = json.dumps({"format": "moytext-v1", "body": body})
        made.append(save_file("docs", name, text, root))
    return made or None


def migrate_tables(root=CARTS_DIR):
    """One-shot #108 migration: the legacy single-file Sheets workbook
    (sheets.json, a list of moysheet-v1 dicts) becomes one
    files/tables/<name>.moysheet per sheet. Gated on files/tables/ not existing
    yet. Each entry is already a moysheet-v1 blob, so it is written verbatim (it
    stays readable by table() unchanged). No-op when there is nothing to
    migrate; returns the list of made names, or None."""
    if _exists(file_kind_dir("tables", root)):
        return None
    blob = load_sheets(root)
    if not blob:
        return None
    try:
        data = json.loads(blob)
    except (ValueError, TypeError):
        return None
    sheets = data.get("sheets") if isinstance(data, dict) else None
    if not isinstance(sheets, list) or not sheets:
        return None
    made = []
    for entry in sheets:
        if not isinstance(entry, dict):
            continue
        base = entry.get("name") if isinstance(entry.get("name"), str) else None
        name = new_file_name("tables", root, base=base)
        made.append(save_file("tables", name, json.dumps(entry), root))
    return made or None


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
