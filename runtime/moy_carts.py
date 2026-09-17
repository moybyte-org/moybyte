# Moybyte SD cartridge store.
#
# Cartridges live as .moy folders under /sd/moybyte/carts/<name>.moy/:
#   manifest.json   title, type, runtime, main, edit-schema
#   main.py         the cartridge source (_init/_update/_draw + kid API)
#   config.json     user-edited values (the Make-it-mine surface)
#   <other>.lua     further scripts, when the manifest lists them in `sources`
#                   (SPEC.md 4): each its own chunk, run in that order. A
#                   PICO-8 port uses it for p8.lua, its generated half (data
#                   tables + compat shim), so main.lua is the cart's own code
#   (sprites later)
#
# MicroPython-friendly by construction (no shutil; os-only). Functions take a `root` so the format/seed/scan logic is
# host-testable against a temp dir. SD shares the SPI bus with the display, so
# the caller mounts SD (moybyte_sd) with the LoRa/TFT CS deselected first.
#
# This file is the store CORE: load/scan/save_*, manifests, sources, the
# sibling stores (wifi/system/achievements/icons/shared sheet), pmem and
# create/duplicate/delete. Its leaves and the modules split off it are
# imported back and re-exported under their old names, so `moy_carts.X` is
# one namespace for every caller:
#   moy_store_base   the on-card layout, slug, ensure_dirs, the dir primitives
#   moy_fs           crash-safe file primitives
#   moy_image        the moyimg codec
#   moy_journal      the per-project undo journal
#   moy_seed         seeding, the packed roster, the retired-seed sweep
#   moy_files        the #108 user-files layer
#   moy_file_ops     per-file history sidecars, the trash, provenance

import gc
import json

try:
    import os
except ImportError:  # pragma: no cover
    os = None

try:
    import time as _time
except ImportError:  # pragma: no cover
    _time = None

try:
    from moy_store_base import (CARTS_DIR, CART_FORMAT, CANVAS_SIZES, IMAGES_DIR,
                                IMAGE_EXT, FLAGS_NAME, TILE_FLAGS, SCENES_DIR,
                                SCENE_EXT, _normalize_canvas, _canvas_str,
                                _sibling_path, slug, ensure_dirs, _is_dir,
                                _rmtree)
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime.moy_store_base import (CARTS_DIR, CART_FORMAT, CANVAS_SIZES,
                                        IMAGES_DIR, IMAGE_EXT, FLAGS_NAME,
                                        TILE_FLAGS, SCENES_DIR, SCENE_EXT,
                                        _normalize_canvas, _canvas_str,
                                        _sibling_path, slug, ensure_dirs,
                                        _is_dir, _rmtree)


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


ARTWORK_NAME = "artwork.moyimg"
# Cartridge COVER ART (visual identity v1 Section 11.4): a cart folder may carry
# images/cover.moyimg -- static authored cover art the Library shelf draws
# full-bleed on the card. The deterministic fallback when absent is the cart's
# sprite tile 0 / type glyph (the pre-cover look). tools/gen_covers.py captures a
# gameplay frame for the seed games; Paint art or any moyimg works the same.
COVER_IMAGE = "cover"
DECK_NAME = "deck.json"

# A single shared sprite sheet lives alongside the carts dir (one level up, so
# it sits beside every <name>.moy folder). Tiles painted here are reusable
# across carts; the import-tile primitive copies tiles between any two sheets.
SHARED_SHEET_NAME = "shared.moygfx"


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
    from moy_image import (THUMBS_DIR, _b64_encode, encode_moyimg, moyimg_runs,
                           decode_moyimg, cover_sig)
    from moy_fs import (_mkdir, _exists, _read, _write, _remove, _copy,
                        _write_atomic, _read_recover, _read_bak, _forget_bak,
                        set_publish_root)
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime.moy_image import (THUMBS_DIR, _b64_encode, encode_moyimg,
                                   moyimg_runs, decode_moyimg, cover_sig)
    from runtime.moy_fs import (_mkdir, _exists, _read, _write, _remove, _copy,
                                _write_atomic, _read_recover, _read_bak,
                                _forget_bak, set_publish_root)


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
    BECAUSE of that .bak, so its window leaves the store truncated -- and a loader
    that reads a truncated store as "nothing saved yet" makes the loss permanent:
    the next save overwrites the one surviving good copy.
    """
    def _pick(text):
        try:
            return pick(json.loads(text)) if text is not None else None
        except (ValueError, TypeError, AttributeError):
            return None

    try:
        got = _pick(_read(path))
    except OSError:
        got = None
    if got is not None:
        return got
    text = _read_bak(path)
    got = _pick(text)
    if got is None:
        return None
    try:
        _write(path, text)            # heal: republish the last known-good copy
    except Exception:                 # noqa: BLE001 -- still return what we recovered
        pass
    return got


def _write_sibling(root, name, text):
    """Persist a sibling store atomically (an interrupted write must never
    truncate system state). Ensures the parent dir exists."""
    ensure_dirs(root)
    _write_atomic(_sibling_path(root, name), text)


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


# --- the document codec ---
#
# One doc in the #108 user-files vault, encoded and decoded. The decoder is
# guarded so a missing/bad file degrades to an empty list, never a crash
# (image()'s degrade-don't-throw contract).

def encode_text(body):
    """A doc body string -> the bytes its file holds.

    A document IS its own text now (`files/docs/<name>.md`), so this is `str`
    and nothing else. It stays a named seam because `ctx.files.encode_text` and
    `system_api.ScopedFiles` reach it (#181): a cart writing a document goes
    through one place wherever the format lands next."""
    return str(body)


def decode_text(blob):
    """A document's stored text -> its lines ([] when there are none).

    The blob IS the body, which is what a `.md` always holds -- so this splits
    and nothing else. It stays a named seam for `encode_text`'s reason."""
    if not isinstance(blob, str):
        return []
    return blob.split("\n") if blob else []


def _read_main(path, name):
    """A cart's source, read once more after a collect if the heap said no.

    The source is the largest single allocation a load makes -- tens of KB as
    one contiguous string -- so on a fragmented heap it is the read that fails
    while the memory to serve it exists: the caller that re-opens a cart drops
    the previous one's payloads a few statements earlier (CartManager.reslim)
    and nothing has collected since. A `big=75k` heap against a 77KB source is
    what this was written from (#66).
    """
    full = path + "/" + name
    try:
        return _read_recover(full)
    except MemoryError:
        gc.collect()
        return _read_recover(full)


def _project_title(path):
    """A cart folder's own name as a title -- what a cart is called when its
    manifest can no longer say."""
    cut = max(path.rfind("/"), path.rfind("\\"))
    name = path[cut + 1:] if cut >= 0 else path
    return name[:-4] if name.endswith(".moy") else (name or "cart")


def load(path, src=True):
    """Load one .moy folder into a cart dict, or None on error.

    `src=False` reads everything BUT the source -- what a shelf scan wants.
    The source is the one allocation that fails on a fragmented heap while the
    memory to serve it exists (tens of KB as one string), and a scan never
    uses it: the manager slims every scanned cart straight away and the
    source is read back at open (rehydrate). A mid-session rescan that read
    it dropped every big cart from the shelf until the next boot (the Guition
    S3, 2026-09-08: ten of forty-four carts "unreadable" at 79KB apiece). The
    file must still EXIST, or the cart is as broken as it ever was.

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
        broken = ""
        try:
            # _read_recover falls back to manifest.json.bak so a crash mid-save
            # (or an interrupted atomic write) doesn't make the cart unreadable.
            man = json.loads(_read_recover(path + "/manifest.json"))
        except (OSError, ValueError) as exc:
            broken = "manifest.json: " + str(exc)[:48]
        else:
            if not isinstance(man, dict):
                broken = "manifest.json: not an object"
        if broken and not _exists(path + "/manifest.json"):
            # Nothing to repair: the folder was pulled, or never held a cart.
            # Only a manifest that IS there and will not parse is recoverable.
            print("Moybyte cart manifest bad:", path, broken)
            return None
        if broken:
            # A manifest a kid BROKE does not take the project off the shelf
            # (step 5 of docs/text_editing_2026-09.md). JSON mode writes an
            # invalid document on a hard exit by design, so this is the reload
            # that design implies: the cart comes back on format defaults,
            # carrying `broken`, and the Editor opens it on the Config tab with
            # the repair path. It is not RUNNABLE -- Workstation.open sends a
            # launcher tap to the Editor instead, and the loader re-validates
            # every time the folder is read again.
            print("Moybyte cart manifest bad:", path, broken)
            man = {"title": _project_title(path)}
        # A moy-spec cart (SPEC.md 3.1, "format": "moy-1") is Lua-by-definition
        # with spec defaults: main.lua, 30fps, a game. Its manifest never carries
        # the moybyte fields, so the defaults below flip on this flag -- moybyte's
        # own carts ("moybyte-cart-v1", or no format at all) keep theirs.
        spec = man.get("format") == "moy-1"
        mainf = man.get("main", "main.lua" if spec else "main.py")
        if broken and not _exists(path + "/" + mainf):
            # No manifest to name the program, so take whichever is there.
            for alt in ("main.py", "main.lua"):
                if _exists(path + "/" + alt):
                    mainf = alt
                    break
        if src:
            try:
                src = _read_main(path, mainf)
            except OSError as exc:
                if not broken:
                    print("Moybyte cart main missing:", path, exc)
                    return None
                src = ""        # a broken cart still opens; it just cannot run
        elif not broken and not _exists(path + "/" + mainf):
            print("Moybyte cart main missing:", path)
            return None
        else:
            src = None          # not read: the cart carries no "src" key
        # THE CART'S OTHER SCRIPTS (SPEC.md 4). `sources` is the whole load
        # order with `main` among them; absent it is [main], which is every
        # cart that is not a PICO-8 port. Split at main because that is how the
        # tiers use it: `src` is main's text and travels on its own (the Editor
        # edits it, the crash panel maps its lines), so what is left is the
        # pieces either side.
        #
        # A port's p8.lua is the standing case -- the generated half, data
        # tables and compat shim, 61% of what the importer used to write into
        # main.lua. Its own chunk works because the shim publishes 96 GLOBALS
        # and globals cross a chunk boundary; the four per-cart upvalue
        # captures (__p8_gff, __music_map, __p8_map_raw, __p8_sheet) sit beside
        # the data tables IN THAT FILE, which is why the cut is there.
        #
        # Read on the same terms as `src` -- the #66 live-set diet drops them
        # together on a slim scan.
        before = []
        after = []
        if src is not None:
            names = man.get("sources") or ()
            if names and mainf not in names:
                # SPEC.md 4 requires it. Running main last (which is where an
                # unfound marker would put it) is worse than not opening: the
                # cart would fail inside code the author did write.
                print("Moybyte cart sources without main:", path)
                return None
            seen_main = False
            for n in names:
                if n == mainf:
                    seen_main = True
                    continue
                try:
                    text = _read(path + "/" + n)
                except OSError as exc:
                    if not broken:
                        print("Moybyte cart source missing:", path, n, exc)
                        return None
                    continue        # a broken cart still opens; it cannot run
                if seen_main:
                    after.append((n, text))
                else:
                    before.append((n, text))
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
        cart = {
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
            "main": mainf,
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
            # SPEC.md 4's other scripts, as (filename, text) in load order.
            # Empty lists, never None, for a cart that has none -- the tiers
            # iterate them unconditionally.
            "src_before": before,
            "src_after": after,
            # The cart's LOGIC rate (#217): the Player ticks a GAME at 60 only
            # when its manifest says so, else at the 30 SPEC.md 5 guarantees.
            # Spec carts default to that tick explicitly.
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
        }
        if broken:
            # Set ONLY on a cart whose manifest would not parse, so every reader
            # is a `.get` and a repaired cart simply stops carrying the key.
            cart["broken"] = broken
        if src is None:
            del cart["src"]     # absent, never "": the open path reads absent as slim
        return cart
    except Exception as exc:  # noqa: BLE001  -- never let one bad cart escape
        print("Moybyte cart unreadable:", path, exc)
        return None


def scan(root=CARTS_DIR, src=True):
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
                c = load(root + "/" + name, src)
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


# The one door that ADDS a script to a cart (#89). Deliberately narrow, and
# deliberately not on the Code tab: the file switcher there is a switcher, and
# a console for eight-year-olds does not want a New File button two taps from
# every cart. This is reached from the Config tab's ADVANCED row, which is
# where a project's own folder already lives (docs/text_editing_2026-09.md
# step 5) -- and it is offered only where a second script would actually RUN.

def multi_script(cart):
    """Whether THIS cart's runtime loads more than its main file.

    A Lua cart does -- SPEC.md 4, and `lua_ext.cart_chunks` builds the list --
    while the console's Python tier runs `main` and nothing else. So a second
    script listed on a python cart would be a file that silently never runs,
    which is worse than not offering to make one."""
    return (cart or {}).get("runtime", "python") != "python"


def add_source(cart, name, text=""):
    """Create `name` in the cart's folder and list it in `sources` after every
    script the cart already has. Returns the name, or None when it could not.

    Writing `sources` in FULL rather than appending to whatever is there: an
    absent `sources` MEANS `[main]` (SPEC.md 4), so a cart getting its second
    script is exactly the case where there is nothing to append to.

    Refuses a name the folder already holds. Overwriting a file to "create" it
    is how a kid loses a cart, and a cart's own asset names (manifest.json,
    sprites.moygfx) are in that folder too."""
    path = (cart or {}).get("path")
    if not path or not name or "/" in name or name in cart_sources(cart):
        return None
    if _exists(path + "/" + name):
        return None
    mpath = path + "/manifest.json"
    try:
        man = json.loads(_read_recover(mpath))
    except (OSError, ValueError):
        return None
    if not isinstance(man, dict):
        return None
    _write_atomic(path + "/" + name, text)
    man["sources"] = cart_sources(cart) + [name]
    _write_atomic(mpath, json.dumps(man))
    # The in-RAM cart follows, so the Editor can open the new file without a
    # re-read -- after main, which is where the manifest just put it.
    cart["src_after"] = list(cart.get("src_after") or ()) + [(name, text)]
    return name


# save_code() outcomes -- the caller (Workstation) surfaces these to the kid:
SAVE_OK = "ok"            # source parsed and was written atomically
SAVE_BAD_SYNTAX = "bad"   # source won't compile; the good file was left untouched
SAVE_KEPT = "kept"        # source won't compile but was written ANYWAY (`force`):
                          # it was written, and it still carries a syntax message


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


def runtime_compile_check(cart, src):
    """The parse gate for THIS cart's runtime -- (ok, message), compile_check's
    shape. Every commit path asks here (the store's save_code, ws.save_code,
    the idle autosave, the live error marker), so "does this compile" cannot
    mean the PYTHON compiler on one path and something else on the next.

    A python cart is compile()d. A "lua" cart (#67) answers ok UNCHECKED:
    neither tier has a syntax-only Lua entry -- the host's hl_exec and the
    device's moycore run_chunk both luaL_loadbuffer AND lua_pcall in one step,
    over a run that needs a framebuffer under it, which an Editor tab has not
    got. So the gate degrades to COMMIT rather than to never-commit, and a Lua
    syntax error surfaces where it always did, at PLAY. Gating Lua means a
    compile-only entry on BOTH tiers; one alone would have the host and a board
    disagree about what saves."""
    if (cart or {}).get("runtime", "python") != "python":
        return True, ""
    return compile_check(src)


# -- a cart's SCRIPTS (SPEC.md 4) -------------------------------------------
#
# `load` splits the manifest's `sources` at main into `src_before`/`src_after`,
# because that is how the tiers use it -- `src` is main's text and travels on
# its own. Everything that asks "which files is this cart's CODE in" (the
# Editor's file switcher, the crash router, the store's own writer) needs the
# list back in LOAD order, and three readers reassembling it from three keys is
# three places to get the order wrong.

def cart_sources(cart):
    """A cart's scripts in load order, `main` among them. A cart that declares
    no `sources` answers `[main]`, which is what its absence MEANS."""
    if not cart:
        return []
    return ([n for n, _ in cart.get("src_before") or ()]
            + [cart.get("main", "main.py")]
            + [n for n, _ in cart.get("src_after") or ()])


def source_text(cart, name=None):
    """The in-RAM text of ONE of `cart`'s scripts, or None when it has no such
    file. `name` None -- or main's own name -- is `src`."""
    if not cart:
        return None
    if name is None or name == cart.get("main", "main.py"):
        return cart.get("src")
    for key in ("src_before", "src_after"):
        for n, text in cart.get(key) or ():
            if n == name:
                return text
    return None


def set_source(cart, name, src):
    """Write `src` into the in-RAM slot `name` came out of, so a commit leaves
    the loaded cart agreeing with the folder without a re-read."""
    if name == cart.get("main", "main.py"):
        cart["src"] = src
        return
    for key in ("src_before", "src_after"):
        lst = cart.get(key)
        for i in range(len(lst or ())):
            if lst[i][0] == name:
                lst[i] = (name, src)
                return


def save_code(cart, src, force=False, name=None):
    """Persist edited source to one of the cart's scripts, ATOMICALLY and only
    if it passes its runtime's gate. Returns (status, message): status is
    SAVE_OK on success, or SAVE_BAD_SYNTAX with a message (and the previous
    good file is left intact) when `src` won't parse, so a kid's broken edit can
    never truncate the cart.

    `name` is WHICH script (SPEC.md 4), None meaning main -- which is every
    cart with one file, and the file the Editor opens on. A ported cart's
    PICO-8 tabs and its generated p8.lua are the other kind; they take the same
    gate and the same atomic write, because a half-typed tab must not truncate
    the file the cart loads from either.

    `force` is the HARD-EXIT half of the split gate (#154): a kid who goes home
    or powers off mid-line must not lose the line for not having finished it, so
    the exit paths write regardless and get SAVE_KEPT plus the syntax message to
    badge with. The gate still refuses on every SOFT path -- the idle debounce
    and the PLAY gate -- so half-typed source is never published mid-typing and
    a broken cart is never RUN; it surfaces at the next run as crash-to-code."""
    ok, msg = runtime_compile_check(cart, src)
    if not ok and not force:
        return SAVE_BAD_SYNTAX, msg
    if name is None:
        name = cart.get("main", "main.py")
    _write_atomic(cart["path"] + "/" + name, src)
    set_source(cart, name, src)
    return (SAVE_OK, "") if ok else (SAVE_KEPT, msg)


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
                             JOURNAL_SNAP_DIR, journal_append, journal_undo,
                             journal_redo, journal_can_undo, journal_can_redo,
                             _journal_paths, _journal_load_entries,
                             _journal_current_snap, _journal_total_bytes)
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime.moy_journal import (JOURNAL_DIR, JOURNAL_LOG, JOURNAL_CURSOR,
                                     JOURNAL_SNAP_DIR, journal_append,
                                     journal_undo, journal_redo,
                                     journal_can_undo, journal_can_redo,
                                     _journal_paths, _journal_load_entries,
                                     _journal_current_snap,
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
#   *.bak / *.tmp        -- moy_fs's crash-safety artifacts. A copied .bak stamps
#                           content the copy's own file does not hold, and the next
#                           read would "recover" the SOURCE's bytes over it (#154).
def _dup_skip(name, main):
    return (name in ("manifest.json", "config.json",
                     "pmem.json", main, THUMBS_DIR, JOURNAL_DIR, JOURNAL_LOG,
                     JOURNAL_CURSOR, JOURNAL_SNAP_DIR)
            or name.startswith("journal")
            or name.endswith(".bak") or name.endswith(".tmp"))


def _copy_cart_files(src, dst, main):
    """Copy a cart folder's asset files (one level of subfolders -- images/,
    scenes/, docs/) into a fresh copy. Degrade-don't-throw like load():
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
    # blocks.json, images/, docs/ -- lived only in the source FOLDER, so
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


# --- the modules split off this file, re-exported under their old names -------
#
# Nothing in the core above reads any of these; every caller reaches them as
# `moy_carts.X`, so the umbrella is the import site and the leaves stay leaves.
try:
    from moy_seed import (
        _cart_version, _RESEED_PRESERVE, _preserve_moy_data, seed_builtins,
        _SEED_WBITS, _packed_stream, unpack_seed, seed_packed, is_packed,
        RETIRED, RETIRED_GEN, RETIRED_VER_NAME, retired_version_path,
        load_retired_version, prune_retired, sweep_store, seed_any,
        embedded_floor)
    from moy_files import (
        FILES_DIR, TRASH_DIR, TRASH_KEEP, DOC_EXT, SCRIPT_EXTS, VAULT_EXTS,
        script_ext, vault_ext, _ext_from, _item_ext, _whole_exts, _split_item,
        _slug_item, FILE_KINDS, _kind_spec, PROJECT_KIND, PROJECT_ORDER,
        PROJECT_SUBDIRS, _PROJECT_SKIP_EXT, project_kind, project_folder,
        project_dir, project_file_path, list_project_files, load_project_file,
        save_project_file, files_root, file_kind_dir, file_path,
        _ensure_kind_dir, _mtime, _kind_entries, _ends_any, list_files,
        count_files, load_file, _unique_name, new_file_name, free_file_name,
        save_file)
    from moy_file_ops import (
        HISTORY_DIR, HISTORY_EXT, HISTORY_KEEP, _history_dir, _history_path,
        _history_trash_dir, _history_trash_path, _ensure_history_dir,
        _ensure_history_trash_dir, _sidecar_move, _sidecar_copy, history_path,
        load_history, _last_keyframe, ops_since_keyframe,
        history_write_keyframe, history_append_segment, history_prune_fails,
        history_commit, prune_history, clear_history, rename_file, _copytree,
        duplicate_file, _trash_dir, _trash_path, delete_file, trash_list,
        restore_file, _remove_trash_entry, prune_trash, empty_trash,
        content_sig, stamp_provenance, read_provenance)
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime.moy_seed import (
        _cart_version, _RESEED_PRESERVE, _preserve_moy_data, seed_builtins,
        _SEED_WBITS, _packed_stream, unpack_seed, seed_packed, is_packed,
        RETIRED, RETIRED_GEN, RETIRED_VER_NAME, retired_version_path,
        load_retired_version, prune_retired, sweep_store, seed_any,
        embedded_floor)
    from runtime.moy_files import (
        FILES_DIR, TRASH_DIR, TRASH_KEEP, DOC_EXT, SCRIPT_EXTS, VAULT_EXTS,
        script_ext, vault_ext, _ext_from, _item_ext, _whole_exts, _split_item,
        _slug_item, FILE_KINDS, _kind_spec, PROJECT_KIND, PROJECT_ORDER,
        PROJECT_SUBDIRS, _PROJECT_SKIP_EXT, project_kind, project_folder,
        project_dir, project_file_path, list_project_files, load_project_file,
        save_project_file, files_root, file_kind_dir, file_path,
        _ensure_kind_dir, _mtime, _kind_entries, _ends_any, list_files,
        count_files, load_file, _unique_name, new_file_name, free_file_name,
        save_file)
    from runtime.moy_file_ops import (
        HISTORY_DIR, HISTORY_EXT, HISTORY_KEEP, _history_dir, _history_path,
        _history_trash_dir, _history_trash_path, _ensure_history_dir,
        _ensure_history_trash_dir, _sidecar_move, _sidecar_copy, history_path,
        load_history, _last_keyframe, ops_since_keyframe,
        history_write_keyframe, history_append_segment, history_prune_fails,
        history_commit, prune_history, clear_history, rename_file, _copytree,
        duplicate_file, _trash_dir, _trash_path, delete_file, trash_list,
        restore_file, _remove_trash_entry, prune_trash, empty_trash,
        content_sig, stamp_provenance, read_provenance)
