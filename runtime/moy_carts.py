# Moybyte SD cartridge store.
#
# Cartridges live as .moy folders under /sd/moybyte/carts/<name>.moy/:
#   manifest.json   title, type, runtime, main, edit-schema
#   main.py         the cartridge source (_init/_update/_draw + kid API)
#   config.json     user-edited values (the Make-it-mine surface)
#   (sprites later)
#
# Mirrors the host runtime/cartridge.py model, but MicroPython-friendly (no
# shutil; os-only). Functions take a `root` so the format/seed/scan logic is
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

# A single shared sprite sheet lives alongside the carts dir (one level up, so
# it sits beside every <name>.moy folder). Tiles painted here are reusable
# across carts; the import-tile primitive copies tiles between any two sheets.
SHARED_SHEET_NAME = "shared.moygfx"


def _b64_encode(data):
    """MicroPython/CPython-compatible base64 text without a trailing newline."""
    try:
        import ubinascii as _binascii
    except ImportError:  # pragma: no cover - CPython
        import binascii as _binascii
    out = _binascii.b2a_base64(data)
    if not isinstance(out, str):
        out = out.decode("ascii")
    return out.strip()


def _b64_decode(text):
    try:
        import ubinascii as _binascii
    except ImportError:  # pragma: no cover - CPython
        import binascii as _binascii
    return _binascii.a2b_base64(text)


def encode_moyimg(width, height, indices):
    """Encode an indexed bitmap as a portable ``moyimg-v1`` blob.

    Paint uses a tiny RLE codec instead of zlib so saving works in the shared
    runtime without depending on a board-specific compressor. Existing zlib
    assets remain valid; decoders dispatch on the optional ``codec`` field.
    Runs are stored as ``count, palette_index`` byte pairs.
    """
    w = int(width)
    h = int(height)
    if w <= 0 or h <= 0 or len(indices) != w * h:
        raise ValueError("bad artwork size")
    packed = bytearray()
    pos = 0
    total = len(indices)
    while pos < total:
        value = int(indices[pos]) & 63
        count = 1
        while pos + count < total and count < 255 \
                and (int(indices[pos + count]) & 63) == value:
            count += 1
        packed.append(count)
        packed.append(value)
        pos += count
    return json.dumps({
        "format": "moyimg-v1", "w": w, "h": h,
        "codec": "rle", "data": _b64_encode(packed),
    })


def moyimg_runs(text):
    """Parse a ``.moyimg`` into ``(w, h, packed_rle_bytes)`` WITHOUT decoding
    the pixels -- the JSON header + base64 only. The Library shelf's
    time-sliced cover builder (console._CoverJob) walks the returned
    (count, value) run pairs incrementally across frames; ``decode_moyimg``
    below stays the one-shot decoder. None on any malformed input."""
    try:
        meta = json.loads(text)
        w = int(meta["w"])
        h = int(meta["h"])
        if w <= 0 or h <= 0 or meta.get("codec") != "rle":
            return None
        packed = _b64_decode(meta["data"])
        if len(packed) & 1:
            return None
        return (w, h, packed)
    except Exception:  # noqa: BLE001 -- a corrupt drawing is treated as absent
        return None


def decode_moyimg(text):
    """Decode Paint's RLE ``.moyimg`` form into ``(w, h, bytes)``.

    The host/device drawing backends retain their legacy-zlib fallback. Keeping
    the shared-store decoder focused on RLE avoids importing compression support
    merely to load Paint's own persisted artwork.
    """
    try:
        meta = json.loads(text)
        w = int(meta["w"])
        h = int(meta["h"])
        if w <= 0 or h <= 0 or meta.get("codec") != "rle":
            return None
        packed = _b64_decode(meta["data"])
        out = bytearray()
        if len(packed) & 1:
            return None
        for i in range(0, len(packed), 2):
            count = packed[i]
            value = packed[i + 1]
            if count < 1 or value > 63 or len(out) + count > w * h:
                return None
            out.extend(bytes((value,)) * count)
        if len(out) != w * h:
            return None
        return (w, h, bytes(out))
    except Exception:  # noqa: BLE001 -- a corrupt drawing is treated as absent
        return None


# --- cover thumbnails (#66 launcher shelf): decoded-crop sidecars -------------
#
# Decoding a 320x240 RLE cover costs 0.5-1.7s interpreted on the T-Deck, so the
# console (console._cover_for) builds each card-sized crop ONCE and persists it
# here as raw indexed pixels: <cart>/thumbs/<w>x<h>.mct = b"MCT1" + a 4-byte LE
# stamp of the cover blob it was built from (cover_sig) + the w*h pix bytes.
# An edited cover changes the stamp -> the stale thumb is ignored and rebuilt;
# a deleted cart takes its thumbs with it; a re-seed wipe just regenerates.
# Regenerable cache, so: plain writes (no atomic dance), best-effort saves, and
# every reader validates magic + size + stamp before trusting a byte.

THUMBS_DIR = "thumbs"


def cover_sig(text):
    """A cheap content stamp for a cover blob (NOT a hash): its length mixed
    with head+tail character sums -- a paint edit virtually always moves one of
    them. A collision only ever means one stale thumbnail, never a crash."""
    s = 0
    for ch in text[:64]:
        s += ord(ch)
    for ch in text[-64:]:
        s = (s * 3 + ord(ch)) & 0xFFFFFF
    return (len(text) * 2654435761 + s) & 0xFFFFFFFF


def _thumb_file(path, w, h):
    return (path + "/" + THUMBS_DIR + "/"
            + str(int(w)) + "x" + str(int(h)) + ".mct")


def load_cover_thumb(path, w, h, sig):
    """The pre-decoded (w, h) cover crop for the cart at `path` -- the raw
    indexed pix bytes (len == w*h) -- or None when absent, stale or corrupt."""
    try:
        with open(_thumb_file(path, w, h), "rb") as f:
            data = f.read()
    except OSError:
        return None
    if (len(data) != 8 + int(w) * int(h) or data[:4] != b"MCT1"
            or int.from_bytes(data[4:8], "little") != (sig & 0xFFFFFFFF)):
        return None
    return data[8:]


def save_cover_thumb(path, w, h, sig, pix):
    """Persist a finished cover crop. Best-effort and never raises: a full SD
    just means that crop decodes again next session."""
    try:
        _mkdir(path + "/" + THUMBS_DIR)
        with open(_thumb_file(path, w, h), "wb") as f:
            f.write(b"MCT1" + (sig & 0xFFFFFFFF).to_bytes(4, "little"))
            f.write(pix)
    except Exception:  # noqa: BLE001 -- regenerable cache
        pass


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


def artwork_path(root=CARTS_DIR):
    """The shared Paint document, beside the carts directory."""
    parent = root.rsplit("/", 1)[0]
    return (parent + "/" + ARTWORK_NAME) if parent else ARTWORK_NAME


def load_artwork(root=CARTS_DIR):
    try:
        return _read(artwork_path(root))
    except OSError:
        return None


def save_artwork(text, root=CARTS_DIR):
    ensure_dirs(root)
    _write_atomic(artwork_path(root), text)


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
    parent = root.rsplit("/", 1)[0]
    return (parent + "/" + NOTES_NAME) if parent else NOTES_NAME


def load_notes(root=CARTS_DIR):
    try:
        return _read(notes_path(root))
    except OSError:
        return None


def save_notes(text, root=CARTS_DIR):
    ensure_dirs(root)
    _write_atomic(notes_path(root), text)


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


def seed_builtins(seed_list, root=CARTS_DIR):
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
    for cart in seed_list:
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
            "format": CART_FORMAT, "title": cart["title"], "type": cart["type"],
            # #67 dual-runtime passthrough: a baked "lua" built-in seeds with its
            # runtime + main.lua intact (this manifest is REGENERATED, not copied).
            "runtime": cart.get("runtime", "python"),
            "main": cart.get("main", "main.py"),
            "edit": cart.get("edit", []),
            "version": seed_ver,
        }
        if cart.get("fps"):               # frame pacing (#63): "fps": 60 opt-out
            manifest["fps"] = cart["fps"]
        if cart.get("canvas") is not None:
            manifest["canvas"] = cart["canvas"]
        if cart.get("permissions") is not None:
            manifest["permissions"] = cart["permissions"]
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
        if preserved:
            # restore the kid's saves + tuning AFTER the seed write, so config.json
            # holds their values (not the freshly-seeded defaults) and pmem survives.
            for name, data in preserved.items():
                _write(d + "/" + name, data)


def load(path):
    """Load one .moy folder into a cart dict, or None on error.

    A corrupt cart (bad manifest.json, missing main.py, or anything else
    unexpected) returns None instead of throwing, so one broken folder can never
    take down the gallery or the boot path. The whole body is also guarded so a
    surprise (e.g. a weird VFS error) still degrades to a skip, not a crash."""
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
        try:
            src = _read_recover(path + "/" + man.get("main", "main.py"))
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
            blocks = json.loads(_read(path + "/blocks.json"))  # block source (#29), optional
        except (OSError, ValueError):
            blocks = None
        images = load_images(path)                # paint-image assets (#63), {} if none
        return {
            "path": path,
            "title": man.get("title", "cart"),
            "type": man.get("type", "app"),
            # The #67 dual-runtime seam: which VM runs this cart ("python" today,
            # "lua" via the injected runtime), and which file `src` came from --
            # save_code/duplicate/seed must write THAT file back, never main.py.
            "runtime": man.get("runtime", "python"),
            "main": man.get("main", "main.py"),
            "version": int(man.get("version", 0)),   # 0 = pre-versioning (re-seedable)
            # Graduation (#29 / spec Section 8): a STORED, one-way project fact. Set
            # when a block-authored cart's code commit diverges past the block
            # vocabulary; makes the block editor read-only. Default False (absent =
            # not graduated). Un-set only through the undo journal (the grad rider).
            "graduated": bool(man.get("graduated", False)),
            "src": src,
            # Frame pacing (#63): a GAME cart locks to 30fps unless its manifest
            # says "fps": 60 (only carts that SUSTAIN 60 should -- frame_cap_fps).
            "fps": man.get("fps", 0),
            "cfg": cfg,
            "edit": man.get("edit", []),
            # Manifest capability permissions (#38): a cart only gets a gated API
            # (e.g. the injected `wifi`) when its permission is listed here. A
            # normal kid cart has just ["graphics","input"] (or none) and stays
            # network-less -- the sandbox is preserved.
            "permissions": man.get("permissions", []),
            "sprites": sprites,
            "sounds": sounds,
            "map": tilemap,
            # Block source (#29): the program tree a cart was authored from in the
            # block editor, or None for a code-authored cart. main.py stays the
            # runnable source either way; blocks.json is the editable origin.
            "blocks": blocks,
            # Paint-image assets (#63 Fold 3): {name: .moyimg text} from images/, or {}.
            # A cart references one via the api's image(name) accessor and places it
            # with spr(img, x, y) -- a big MOY64 index bitmap (a painted background).
            "images": images,
        }
    except Exception as exc:  # noqa: BLE001  -- never let one bad cart escape
        print("Moybyte cart unreadable:", path, exc)
        return None


def scan(root=CARTS_DIR):
    """All carts found under root, sorted by folder name. Corrupt carts are
    skipped (load() returns None), and any per-entry surprise is swallowed so a
    single bad folder can't break the launcher."""
    carts = []
    try:
        names = sorted(os.listdir(root))
    except OSError:
        return carts
    for name in names:
        if name.endswith(".moy"):
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


# --- the undo/redo journal (#7, Stage 7 of docs/shell_ux_technical_plan_v1.md) ---
#
# A durable, per-project, reboot-surviving undo history stored on SD BESIDE the
# cart's files, in <cart>.moy/journal/:
#
#   journal.jsonl  APPEND-ONLY -- one JSON line per commit event:
#                  {"seq": N, "ts": ..., "file": "main.py", "snap": "s/000N-main.py"}
#                  the entry points at a FULL-FILE snapshot under journal/s/. Full
#                  snapshots, not diffs: MicroPython-safe (no difflib), and one bad
#                  snapshot loses one step, never the whole history.
#   cursor.json    {"seq": N, "bytes": B} -- the undo position (N) + the running
#                  total snapshot bytes (B, the rotation gate). Written via
#                  _write_atomic: a tiny fixed-size file whose atomic rename is what
#                  makes the cursor torn-write-proof.
#   s/000N-<file>  the per-commit full-file snapshots.
#
# CADENCE (v1.1 pinned): the line APPEND is a raw open(path, "a") -- O(1), one line
# appended per commit -- and NEVER _write_atomic (which rewrites the whole file, so
# every commit would be O(n) in the journal's length). The only non-append rewrites
# are the RARE redo-tail truncation (a commit after an undo) and journal_compact
# (rotation) -- both between-frames like every SD op. Torn-write recovery: a torn
# last jsonl line fails json.loads and is dropped at load; the cursor is atomic.
#
# WALK: cursor N = "live files reflect commit seq N applied" (0 = pre-journal).
#   undo  = restore the same file's PREVIOUS snapshot over the live file, step the
#           cursor back one entry (floor = a file's first journaled snapshot; finer,
#           in-session undo stays in the editor's RAM).
#   redo  = re-apply the next commit's snapshot, step the cursor forward.
#   a NEW commit while the cursor is rewound TRUNCATES the redo tail (Google-Docs rule).
#
# ROTATION: a per-project cap of 64 entries OR 512KB of snapshots (whichever first);
# journal_compact drops the OLDEST entries + their snapshots (a full journal.jsonl
# rewrite + snapshot deletes -- the one place the journal is not append-only). It
# never drops any file's current-state snapshot or the redo tail.

JOURNAL_DIR = "journal"
JOURNAL_LOG = "journal.jsonl"
JOURNAL_CURSOR = "cursor.json"
JOURNAL_SNAP_DIR = "s"
JOURNAL_MAX_ENTRIES = 64
JOURNAL_MAX_BYTES = 512 * 1024


def _journal_paths(cart_dir):
    jdir = cart_dir + "/" + JOURNAL_DIR
    return jdir, jdir + "/" + JOURNAL_LOG, jdir + "/" + JOURNAL_CURSOR, jdir + "/" + JOURNAL_SNAP_DIR


def _journal_ts():
    if _time is None:
        return 0
    try:
        return int(_time.time())
    except Exception:  # noqa: BLE001 -- ts is informational; never let it break a commit
        return 0


def _journal_snap_name(seq, file):
    return "%04d-%s" % (seq, file)


def _journal_load_entries(log_path):
    """Parse journal.jsonl into a list of entry dicts sorted by seq. A torn/corrupt
    line (the append-only log's only failure mode -- a torn LAST line from a power
    loss mid-append) fails json.loads and is DROPPED; every well-formed entry before
    it survives. Missing log -> []."""
    entries = []
    try:
        raw = _read(log_path)
    except OSError:
        return entries
    for line in raw.split("\n"):
        if not line.strip():
            continue
        try:
            e = json.loads(line)
        except ValueError:
            continue                       # torn / corrupt line -> drop it, keep the rest
        if isinstance(e, dict) and "seq" in e and "file" in e and "snap" in e:
            entries.append(e)
    entries.sort(key=lambda e: e["seq"])
    return entries


def _journal_cursor(cur_path, entries):
    """The undo position (a seq value; 0 = pre-journal). A missing/torn cursor.json
    defaults to the TOP (the latest entry's seq, i.e. everything applied) -- the safe
    'live files reflect the last commit' state."""
    try:
        data = json.loads(_read(cur_path))
        return int(data["seq"])
    except (OSError, ValueError, TypeError, KeyError):
        return entries[-1]["seq"] if entries else 0


def _journal_bytes(cur_path):
    """The running total snapshot bytes recorded in cursor.json (0 when absent), the
    cheap rotation gate so a normal append never has to stat every snapshot."""
    try:
        data = json.loads(_read(cur_path))
        return int(data.get("bytes", 0))
    except (OSError, ValueError, TypeError, AttributeError):
        return 0


def _journal_write_cursor(cur_path, seq, total_bytes):
    # cursor.json is tiny + fixed-shape -> _write_atomic (its atomic rename is the
    # torn-write proofing the append deliberately skips).
    _write_atomic(cur_path, json.dumps({"seq": int(seq), "bytes": int(total_bytes)}))


def _journal_rewrite(log_path, entries):
    # The ONLY non-append writes of the log: redo-tail truncation + compaction. Rare,
    # so _write_atomic (crash-safe) is fine here -- it is NOT on the per-commit path.
    _write_atomic(log_path, "".join(json.dumps(e) + "\n" for e in entries))


def _journal_current_snap(entries, cursor, file):
    """The snapshot representing `file`'s current live state = the latest entry for
    that file with seq <= cursor (or None if the file has no snapshot yet)."""
    best = None
    for e in entries:                      # ascending -> the last match <= cursor wins
        if e["file"] == file and e["seq"] <= cursor:
            best = e
    return best["snap"] if best else None


def _journal_total_bytes(jdir, entries):
    total = 0
    for e in entries:
        try:
            total += os.stat(jdir + "/" + e["snap"])[6]   # [6] = st_size (host + MicroPython)
        except OSError:
            pass
    return total


def _journal_read_snap(jdir, entry):
    """Read + INTEGRITY-CHECK an entry's snapshot before it is copied over a live file.
    Returns the snapshot text, or None when it is missing or torn -- so undo/redo can
    refuse a damaged snapshot instead of overwriting good work with garbage/empty.

    Validated against the recorded `len`: a length mismatch (truncated / 0-byte from a
    device power loss -- snapshots are non-atomic, no fsync) is rejected; an entry that
    legitimately snapshotted an empty file (len == 0) still restores cleanly. Legacy
    entries without a recorded `len` fall back to "reject an empty read as likely-torn"."""
    try:
        data = _read(jdir + "/" + entry["snap"])
    except OSError:
        return None                            # missing snapshot -> refuse
    exp = entry.get("len")
    if exp is None:
        return data if data else None          # unlabelled: an empty read is likely torn
    if len(data) != int(exp):
        return None                            # truncated / torn -> refuse
    return data


def _journal_apply_grad(cart_dir, entry):
    """Sync the manifest's `graduated` flag to `entry`'s grad rider (Stage 8) after an
    undo/redo restores its snapshot. Only main.py entries carry `grad`; an entry
    without one leaves the flag untouched (never guesses). Best-effort -- a manifest
    hiccup must not fail the walk (the live file is already restored)."""
    if "grad" not in entry:
        return
    try:
        _manifest_set_graduated(cart_dir, int(entry["grad"]))
    except Exception as exc:  # noqa: BLE001
        print("Moybyte graduation flag walk failed:", exc)


def journal_append(cart_dir, file, new_bytes, grad=None):
    """Record a durable commit event for `file`: snapshot `new_bytes` under journal/s/
    and RAW-append one line to journal.jsonl (O(1)). Returns the new seq, or None when
    nothing was written (a no-op: the content already matches the current state).

    Order (torn-write safe): snapshot first, THEN the log line, THEN the cursor -- so a
    crash never leaves a log line pointing at a torn snapshot (the orphan snapshot is
    simply unreferenced). A commit made while the cursor is rewound truncates the redo
    tail first (Google-Docs rule). Rotation runs at the end when over cap.

    `grad` (Stage 8, spec Section 8): an optional 0/1 GRADUATION rider that rides a
    main.py commit -- the graduated state of the cart AT this commit. When an entry is
    actually appended with a grad rider, the manifest's `graduated` flag is set to it
    (so the one-way flip rides the exact same durable step as the source), and
    journal_undo/redo re-apply the target entry's grad -- which is how an undo past a
    graduating commit restores BOTH the source and graduated:false."""
    if new_bytes is None:
        return None
    jdir, log_path, cur_path, snap_dir = _journal_paths(cart_dir)
    entries = _journal_load_entries(log_path)   # empty when there's no journal/ yet
    cursor = _journal_cursor(cur_path, entries)
    total = _journal_bytes(cur_path)
    # -- ceiling / no-op dedup: identical to the current state -> write NOTHING (a
    #    debounce that fires with nothing changed must not touch the card). Checked
    #    BEFORE any _mkdir so a no-op append leaves no empty journal/ folder behind.
    cur_snap = _journal_current_snap(entries, cursor, file)
    if cur_snap is not None:
        try:
            if _read(jdir + "/" + cur_snap) == new_bytes:
                return None
        except OSError:
            pass
    # We are committing to a WRITE now -> create the journal dirs lazily.
    _mkdir(jdir)
    _mkdir(snap_dir)
    # -- Google-Docs rule: a commit while rewound truncates the redo tail. This is the
    #    ONE non-append rewrite on the commit path (rare -- only right after an undo).
    tail = [e for e in entries if e["seq"] > cursor]
    if tail:
        for e in tail:
            _remove(jdir + "/" + e["snap"])
        entries = [e for e in entries if e["seq"] <= cursor]
        _journal_rewrite(log_path, entries)
        total = _journal_total_bytes(jdir, entries)   # recompute exactly after the cut
    # -- assign the next seq, write the full-file snapshot, then RAW-append one line.
    seq = (entries[-1]["seq"] + 1) if entries else 1
    snap = JOURNAL_SNAP_DIR + "/" + _journal_snap_name(seq, file)
    _write(jdir + "/" + snap, new_bytes)              # snapshot BEFORE the log line
    # `len` is the snapshot's recorded length: undo/redo validate the on-disk snapshot
    # against it before copying it over the live file, so a torn/truncated snapshot (a
    # device power loss + FAT cache reordering -- snapshots are non-atomic) is REFUSED
    # rather than silently overwriting good work with garbage/empty.
    entry = {"seq": seq, "ts": _journal_ts(), "file": file, "snap": snap, "len": len(new_bytes)}
    if grad is not None:
        entry["grad"] = int(grad)                     # Stage 8 graduation rider
    with open(log_path, "a") as f:                    # RAW append -- O(1), NOT _write_atomic
        f.write(json.dumps(entry) + "\n")
    total += len(new_bytes)
    _journal_write_cursor(cur_path, seq, total)       # cursor advances (atomic)
    # -- graduation flip rides this exact durable step (Stage 8): the manifest's
    #    `graduated` follows the appended entry's grad. Guarded -- a manifest hiccup
    #    must not undo the append that just succeeded (the entry is already durable).
    if grad is not None:
        try:
            _manifest_set_graduated(cart_dir, int(grad))
        except Exception as exc:  # noqa: BLE001
            print("Moybyte graduation flag write failed:", exc)
    # -- rotation: keep within the per-project cap (drops oldest, between frames).
    if len(entries) + 1 > JOURNAL_MAX_ENTRIES or total > JOURNAL_MAX_BYTES:
        journal_compact(cart_dir)
    return seq


def journal_undo(cart_dir):
    """Restore `file`'s PREVIOUS snapshot over the live file and step the cursor back
    one entry. Returns the restored file name, or None at a floor (cursor 0, or the
    file has no earlier snapshot). The live write goes through _write_atomic exactly
    like a normal save."""
    jdir, log_path, cur_path, snap_dir = _journal_paths(cart_dir)
    entries = _journal_load_entries(log_path)
    if not entries:
        return None
    cursor = _journal_cursor(cur_path, entries)
    idx = None
    for k in range(len(entries)):
        if entries[k]["seq"] == cursor:
            idx = k
            break
    if idx is None:
        return None                        # cursor at 0 (or not found) -> nothing to undo
    file = entries[idx]["file"]
    target = None
    for k in range(idx - 1, -1, -1):       # nearest earlier snapshot of the SAME file
        if entries[k]["file"] == file:
            target = entries[k]
            break
    if target is None:
        return None                        # first snapshot of this file -> the floor
    data = _journal_read_snap(jdir, target)
    if data is None:
        return None                        # snapshot missing/torn -> REFUSE, live file intact
    _write_atomic(cart_dir + "/" + file, data)
    new_cursor = entries[idx - 1]["seq"] if idx > 0 else 0
    _journal_write_cursor(cur_path, new_cursor, _journal_bytes(cur_path))
    _journal_apply_grad(cart_dir, target)  # Stage 8: un-graduate past a graduating commit
    return file


def journal_redo(cart_dir):
    """Re-apply the next commit's snapshot over the live file and step the cursor
    forward. Returns the restored file name, or None at the top (nothing to redo)."""
    jdir, log_path, cur_path, snap_dir = _journal_paths(cart_dir)
    entries = _journal_load_entries(log_path)
    if not entries:
        return None
    cursor = _journal_cursor(cur_path, entries)
    nxt = None
    for e in entries:                      # ascending -> the smallest seq > cursor
        if e["seq"] > cursor:
            nxt = e
            break
    if nxt is None:
        return None                        # at the top -> nothing to redo
    data = _journal_read_snap(jdir, nxt)
    if data is None:
        return None                        # snapshot missing/torn -> REFUSE, live file intact
    _write_atomic(cart_dir + "/" + nxt["file"], data)
    _journal_write_cursor(cur_path, nxt["seq"], _journal_bytes(cur_path))
    _journal_apply_grad(cart_dir, nxt)     # Stage 8: re-graduate on redo past the commit
    return nxt["file"]


def journal_compact(cart_dir):
    """Drop the OLDEST entries + their snapshots until the journal is within the cap
    (JOURNAL_MAX_ENTRIES entries AND JOURNAL_MAX_BYTES of snapshots). A full
    journal.jsonl rewrite + snapshot deletes -- the one non-append-only path, run
    between frames like every SD op. NEVER drops any file's current-state snapshot
    (latest seq <= cursor) or the redo tail (seq > cursor), so the current + every
    reachable redo survive. Returns the number of entries dropped."""
    jdir, log_path, cur_path, snap_dir = _journal_paths(cart_dir)
    entries = _journal_load_entries(log_path)
    if not entries:
        return 0
    cursor = _journal_cursor(cur_path, entries)
    keep = {}
    current = {}
    for e in entries:
        if e["seq"] > cursor:
            keep[e["seq"]] = True          # redo tail: never drop
        else:
            current[e["file"]] = e["seq"]  # ascending -> latest <= cursor per file
    for s in current.values():
        keep[s] = True                     # each file's current-state snapshot: never drop
    droppable = [e for e in entries if e["seq"] not in keep]
    droppable.sort(key=lambda e: e["seq"])  # oldest first
    remaining = list(entries)
    total = _journal_total_bytes(jdir, remaining)
    dropped = []
    di = 0
    while ((len(remaining) > JOURNAL_MAX_ENTRIES or total > JOURNAL_MAX_BYTES)
           and di < len(droppable)):
        victim = droppable[di]
        di += 1
        try:
            total -= os.stat(jdir + "/" + victim["snap"])[6]
        except OSError:
            pass
        remaining = [e for e in remaining if e["seq"] != victim["seq"]]
        dropped.append(victim)
    if not dropped:
        return 0
    for e in dropped:
        _remove(jdir + "/" + e["snap"])
    _journal_rewrite(log_path, remaining)
    _journal_write_cursor(cur_path, cursor, _journal_total_bytes(jdir, remaining))
    return len(dropped)


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
# A cart's pmem is 256 x 32-bit unsigned ints stored as a JSON list in pmem.json
# beside main.py. TIC-80 gives carts pmem(i)/pmem(i,v) for high scores / save
# state; this is the per-cart backing store. Reads default to all-zero; saves
# are atomic (same crash-safe path as sprites/config) so an interrupted write
# can never truncate a kid's save.

PMEM_CELLS = 256
PMEM_MASK = 0xFFFFFFFF


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
                cells[i] = int(data[i]) & PMEM_MASK
            except (TypeError, ValueError):
                cells[i] = 0
    return cells


def save_pmem(cart, cells):
    """Persist a cart's pmem list (256 ints) to pmem.json, atomically so an
    interrupted write can't truncate it (needs cart['path'])."""
    _write_atomic(cart["path"] + "/pmem.json", json.dumps(list(cells)))


# --- shared sprite sheet (cross-cart sprite reuse, #18) ---------------------

def shared_sheet_path(root=CARTS_DIR):
    """Well-known path of the shared sprite sheet: a sibling of the carts dir
    (one level up from `root`), so it isn't tied to any single cart."""
    parent = root.rsplit("/", 1)[0]
    return (parent + "/" + SHARED_SHEET_NAME) if parent else SHARED_SHEET_NAME


def load_shared_sheet(root=CARTS_DIR):
    """Read the shared sprite sheet's hex (PICO-8 __gfx__-style), or None if it
    has never been saved. Caller turns it into a SpriteSheet via from_hex."""
    try:
        return _read(shared_sheet_path(root))
    except OSError:
        return None


def save_shared_sheet(hex_text, root=CARTS_DIR):
    """Persist the shared sprite sheet's hex. Ensures the parent dir exists.
    Written atomically (like the per-cart saves) -- it's the highest-value shared
    asset, so an interrupted write must never truncate it."""
    ensure_dirs(root)
    _write_atomic(shared_sheet_path(root), hex_text)


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
    """Well-known path of the system icon theme: a sibling of the carts dir (one
    level up from `root`), so it isn't tied to any single cart."""
    parent = root.rsplit("/", 1)[0]
    return (parent + "/" + SYSTEM_ICONS_NAME) if parent else SYSTEM_ICONS_NAME


def system_icons_version_path(root=CARTS_DIR):
    """Sidecar holding the icon-set version the saved theme was written at (a sibling
    of system_icons.moygfx). Lets a newer baked icon set re-seed a stale saved theme."""
    parent = root.rsplit("/", 1)[0]
    return (parent + "/" + SYSTEM_ICONS_VER_NAME) if parent else SYSTEM_ICONS_VER_NAME


def load_system_icons(root=CARTS_DIR):
    """Read the system icon theme's hex (PICO-8 __gfx__-style), or None if it has
    never been saved -- in which case the caller uses the baked default IconSheet.
    Caller turns the hex into an IconSheet via IconSheet.from_hex."""
    try:
        return _read(system_icons_path(root))
    except OSError:
        return None


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
    """Well-known path of the WiFi credential store: a sibling of the carts dir
    (one level up from `root`), so it isn't tied to any single cart."""
    parent = root.rsplit("/", 1)[0]
    return (parent + "/" + WIFI_STORE_NAME) if parent else WIFI_STORE_NAME


def load_wifi(root=CARTS_DIR):
    """Read the known-networks list: [{"ssid": str, "password": str}, ...].
    Returns [] when nothing has been saved yet or the file is unreadable/garbage
    (a corrupt store must never crash the boot autoconnect)."""
    try:
        data = json.loads(_read(wifi_store_path(root)))
    except (OSError, ValueError):
        return []
    nets = data.get("networks") if isinstance(data, dict) else None
    if not isinstance(nets, list):
        return []
    out = []
    for n in nets:
        if isinstance(n, dict) and n.get("ssid"):
            out.append({"ssid": str(n["ssid"]), "password": str(n.get("password", ""))})
    return out


def save_wifi(networks, root=CARTS_DIR):
    """Persist the known-networks list, atomically. Ensures the parent dir exists.
    `networks` is a list of {"ssid", "password"} dicts."""
    ensure_dirs(root)
    clean = [{"ssid": str(n["ssid"]), "password": str(n.get("password", ""))}
             for n in networks if n.get("ssid")]
    _write_atomic(wifi_store_path(root), json.dumps({"networks": clean}))


def remember_wifi(ssid, password, root=CARTS_DIR):
    """Add/replace one network in the store (by ssid) and persist. Returns the
    updated list. The most-recently-remembered network is moved to the FRONT, so
    autoconnect prefers the last one the kid joined."""
    ssid = str(ssid)
    nets = [n for n in load_wifi(root) if n["ssid"] != ssid]
    nets.insert(0, {"ssid": ssid, "password": str(password or "")})
    save_wifi(nets, root)
    return nets


def forget_wifi(ssid, root=CARTS_DIR):
    """Drop one network from the store (by ssid) and persist. Returns the updated
    list (unchanged if the ssid wasn't known)."""
    ssid = str(ssid)
    nets = [n for n in load_wifi(root) if n["ssid"] != ssid]
    save_wifi(nets, root)
    return nets


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
    """Well-known path of the system settings store: a sibling of the carts dir
    (one level up from `root`), so it isn't tied to any single cart."""
    parent = root.rsplit("/", 1)[0]
    return (parent + "/" + SYSTEM_STORE_NAME) if parent else SYSTEM_STORE_NAME


def load_system(root=CARTS_DIR):
    """Read the system settings dict. Returns {} when nothing has been saved yet
    or the file is unreadable/garbage (a corrupt store must never crash boot)."""
    try:
        data = json.loads(_read(system_store_path(root)))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


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
    """Well-known path of the achievements store: a sibling of the carts dir
    (one level up from `root`), so it isn't tied to any single cart."""
    parent = root.rsplit("/", 1)[0]
    return (parent + "/" + ACHIEVEMENTS_STORE_NAME) if parent else ACHIEVEMENTS_STORE_NAME


def load_achievements(root=CARTS_DIR):
    """Read the unlocked achievement ids as a list. Returns [] when nothing has
    been earned yet or the file is unreadable/garbage (a corrupt store must never
    crash boot). Duplicates/non-strings are dropped so the loaded list is clean."""
    try:
        data = json.loads(_read(achievements_store_path(root)))
    except (OSError, ValueError):
        return []
    ids = data.get("unlocked") if isinstance(data, dict) else None
    if not isinstance(ids, list):
        return []
    out = []
    seen = {}
    for i in ids:
        if isinstance(i, str) and i not in seen:
            seen[i] = True
            out.append(i)
    return out


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
           runtime="python", main="main.py"):
    """Create a new .moy folder and return its loaded cart dict. `runtime`/`main`
    default to a python cart; duplicate() passes a source cart's through so a
    copied "lua" cart (#67) stays a lua cart with its source in main.lua."""
    d = _unique_dir(root, slug(title))
    _mkdir(d)
    manifest = {
        "format": CART_FORMAT, "title": title, "type": type,
        "runtime": runtime, "main": main, "edit": edit or [],
    }
    _write(d + "/manifest.json", json.dumps(manifest))
    _write(d + "/" + main, src if src is not None else NEW_TEMPLATE["src"])
    _write(d + "/config.json", json.dumps(cfg or {}))
    return load(d)


def new_from_template(root=CARTS_DIR, title="New Cart"):
    return create(title, root, src=NEW_TEMPLATE["src"], cfg=dict(NEW_TEMPLATE["cfg"]),
                  edit=NEW_TEMPLATE["edit"], type=NEW_TEMPLATE["type"])


def duplicate(cart, root=CARTS_DIR, new_title=None):
    return create(new_title or (cart["title"] + " copy"), root,
                  src=cart["src"], cfg=dict(cart["cfg"]), edit=cart["edit"], type=cart["type"],
                  runtime=cart.get("runtime", "python"), main=cart.get("main", "main.py"))


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
