# Map (grep -n a name to jump there):
#   parse_flags                a flags blob -> 512 bytes
#   load_cover                 a cart's cover.png bytes
#   decode_cover               cover.png bytes -> 128x128 indices
#   encode_cover               128x128 indices -> cover.png bytes
#   load_images                a cart's paint-image assets
#   -- sibling stores          load_artwork
#   -- the document codec      encode_text, decode_text
#   -- the catalogue           PAYLOADS, entry, load, catalogue, scan
#   -- manifest metadata       save_manifest_meta, add_source, compile_check
#   -- a cart's SCRIPTS        cart_sources, source_text, set_source, save_code
#   -- block source            load_blocks, save_blocks
#   -- persistent cart memory  load_pmem, save_pmem, save_pmem_later
#   -- shared sprite sheet     load_shared_sheet, save_shared_sheet
#   -- cart management         store_full, create, new_from_template, duplicate
#   -- the modules split off this file  moy_seed; moy_docs (wifi, system, achievements); moy_pack
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
#   moyimg           the moyimg codec
#   moy_journal      the per-project undo journal
#   moy_seed         seeding, the packed roster, the retired-seed sweep
#   moy_ufiles       the #108 user-files layer, its sidecars, the trash and
#                    provenance: native/moy_store/moy_ufiles.h, reached by name
#                    (`moy_carts.list_files`) through the module's __getattr__
#                    below, never imported here, so a board without it (the
#                    Zero) loads this module

import json

try:
    import os
except ImportError:  # pragma: no cover
    os = None

try:
    import time as _time
except ImportError:  # pragma: no cover
    _time = None

from moy_fs import _native

from moy_store_base import (FILE_KIND_NAMES, FILE_KIND_DIRS, SHARED_FOLDERS, files_root, CARTS_DIR,
                            CART_FORMAT, CANVAS_SIZES, IMAGES_DIR,
                            IMAGE_EXT, FLAGS_NAME, TILE_FLAGS, SCENES_DIR,
                            SCENE_EXT, _normalize_canvas, _canvas_str,
                            _sibling_path, slug, ensure_dirs,
                            _rmtree, COVER_FILE, COVER_MAX_BYTES,
                            SPRITES_NAME, CART_EXT,
                            cart_path, cart_folder,
                            USER_NS, BUILTIN_NS, VENDOR_KEY, vendor,
                            is_cart_id, builtin_ns, THEME_NAME_MAX, theme_name_ok)


ARTWORK_NAME = "artwork.moyimg"

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


# The moyimg codec and the crash-safe file primitives live in their own leaf
# modules (moyimg / moy_fs); imported back + re-exported under these names so
# every `store.X` lookup reaches them. Same bare-or-package fallback as every
# shared module.
from moy_store_base import THUMBS_DIR
from moyimg import _b64_encode, encode_moyimg, decode_moyimg, text_sig
from moy_fs import (_mkdir, _exists, _read, _write, _remove, _copy,
                    _write_atomic, _read_recover, _read_bak, _forget_bak,
                    set_publish_root, _read_bytes, _write_bytes, _pmem_read,
                    _pmem_save, _save_wait, _pmem_tidy)


def load_cover(path):
    """The cart at `path`'s cover.png bytes, or None -- no file, or one larger
    than any cover can be (cover_png.MAX_BYTES). The shelf reads covers through
    this, so a slimmed cart (#66 live-set diet) never rehydrates for its card.
    The bytes are not checked here: cover_png decides what is a cover."""
    try:
        return _read_bytes(path + "/" + COVER_FILE, COVER_MAX_BYTES)
    except OSError:
        return None


def save_cover(cart, data):
    """Publish `data` as the cart's cover.png, whole (moy_fs._write_bytes)."""
    _write_bytes(cart["path"] + "/" + COVER_FILE, data)


def _cover_png():
    # Imported where it is used: the headless Zero carries this store and no
    # cover reader, and never asks for one.
    import cover_png
    return cover_png


def decode_cover(data):
    """`(128, 128, indices)` for a cover.png's bytes -- each pixel the nearest
    console-palette colour, exact for a cover written in that palette -- or
    None when the bytes are no cover. What Paint opens a cart's cover as."""
    cp = _cover_png()
    pix = cp.decode(data, 1, cp.INDEX, cp.moy64())
    return None if pix is None else (cp.SIDE, cp.SIDE, pix)


def encode_cover(indices):
    """128 x 128 console-palette indices -> the bytes of a cover.png: indexed,
    the palette as its PLTE. What Paint saves a cart's cover as."""
    cp = _cover_png()
    return cp.encode_indexed(indices, cp.moy64())


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


def _manifest_add_scene(cart_dir, name):
    """Register scene `name` in manifest.json's moybyte.assets.scenes list (#85), preserving
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
    ven = man.get(VENDOR_KEY)
    if not isinstance(ven, dict):
        ven = {}
        man[VENDOR_KEY] = ven
    assets = ven.get("assets")
    if not isinstance(assets, dict):
        assets = {}
        ven["assets"] = assets
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
# they aren't tied to any single cart: the wallpaper's artwork copy, the shared
# sprite sheet, the system icon theme, wifi/system/achievements JSON. The path formula,
# the read-or-None, and the ensure-dirs + atomic write are each written ONCE here;
# the per-store `X_path`/`load_X`/`save_X` wrappers keep their public names (and
# any store-specific parse/sanitize logic).


def _read_sibling(root, name):
    """The store's raw text, or None if it has never been saved."""
    try:
        return _read(_sibling_path(root, name))
    except OSError:
        return None


def _write_sibling(root, name, text):
    """Persist a sibling store atomically (an interrupted write must never
    truncate system state). Ensures the parent dir exists."""
    ensure_dirs(root)
    _write_atomic(_sibling_path(root, name), text)


def load_artwork(root=CARTS_DIR):
    return _read_sibling(root, ARTWORK_NAME)


# --- the document codec ---
#
# One doc in the #108 user-files vault, encoded and decoded. The decoder is
# guarded so a missing/bad file degrades to an empty list, never a crash
# (image()'s degrade-don't-throw contract).

def encode_text(body):
    """A doc body string -> the bytes its file holds.

    A document IS its own text now (`files/Documents/<name>.md`), so this is `str`
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


# --- the catalogue and the whole cart ---
#
# The shelf holds each cart as its catalogue ENTRY -- the manifest's metadata,
# the config, the tile flags and the scene names -- and reads a cart WHOLE only
# to open it. `catalogue(root)` is the shelf's read, one `entry` per folder;
# `load(path)` is the whole cart, which the shelf merges into the entry it holds
# (CartManager.rehydrate) and drops again when the workspace moves on (reslim).

# What `load` reads beyond an entry: the cart's scripts, its assets, its tile
# flags and its config. Only a loaded cart reads the last two (the Project's
# flag table, a wallpaper after `rehydrate`), and the shelf draws neither.
PAYLOADS = ("src", "src_before", "src_after", "sprites", "sounds", "map",
            "images", "blocks", "scenes", "flags", "cfg")


def entry(path):
    """One .moy folder's catalogue ENTRY, or None when it is no cart: the
    manifest's metadata, the config, the tile flags and the scene names, read
    from inside the folder (native/moy_store/moy_cat.c). None of the PAYLOADS:
    those are `load`'s. A folder whose manifest will not read is still an
    entry, `broken` saying why.

    One field is the shelf's, and lives until the shelf takes the entry:
    `icon_rows`, the rows of the sprite sheet the cart's launcher icon is cut
    from (`moy_store_base.icon_rows`; None for no sheet or no art), read only
    as far as the icon needs. `CartManager.slim` bakes the icon from it and
    drops it."""
    return _native.entry(path)


def load(path):
    """Load one .moy folder WHOLE into a cart dict, or None on error -- its
    `entry` and every one of its PAYLOADS. What opening a cart reads, and what
    the shelf's rehydrate merges into the entry it holds.

    A corrupt cart (bad manifest.json, missing main.py, or anything else
    unexpected) returns None instead of throwing, so one broken folder can never
    take down the gallery or the boot path.

    Every optional asset is carried in its SERIALISED form -- `sprites`, `map`
    and `flags` are the file text, `None` when the file is absent -- and the
    live objects (SpriteSheet / TileMap / the 512-byte flag table) are built
    from them by `Project`. An absent `flags.moyflags` is `None` here and
    all-zero there, which is SPEC.md 3.5's own reading of a missing file.
    native/moy_store/moy_cat.c's moy_cat_load: one arena, freed once the dict
    is built."""
    return _native.load(path)


def entries(root=CARTS_DIR):
    """Every cart folder's `entry` under root, sorted by folder name, or None
    when root will not list, which a caller keeping state per folder must not
    read as "every cart is gone" (moy_catalogue.catalogue). A `.moy` FILE
    beside the folders is an archive -- how a cart travels, not how it is
    stored -- and is no entry."""
    return _native.catalogue(root)


def catalogue(root=CARTS_DIR):
    """The shelf: every cart folder's `entry` under root, sorted by folder
    name. moy_catalogue.catalogue is the shelf's read: this one, with a
    handle on every entry."""
    return entries(root) or []


def scan(root=CARTS_DIR):
    """Every cart under root, loaded WHOLE, sorted by folder name -- for a
    caller that wants every cart's payloads (a host tool, a test); the shelf
    reads the `catalogue`."""
    out = []
    for e in entries(root) or []:
        c = load(e["path"])
        if c:
            out.append(c)
    return out


def save_config(cart):
    """Persist a cart's edited config back to its config.json (needs cart['path'])."""
    _write_atomic(cart["path"] + "/config.json", json.dumps(cart["cfg"]))


# --- graduation flag (#29 / spec Section 8): a stored, one-way project fact ---
#
# The `graduated` boolean lives in manifest.json's moybyte object (a project fact, not per-file
# data). _manifest_set_graduated is the low-level read-modify-write that the public
# setter AND the undo journal (journal_undo/redo, via the entry's `grad` rider)
# both use, so a graduation and its undo touch the manifest through one code path.
# It PRESERVES every other manifest field (title/version/edit/permissions/...) and
# writes atomically like every other save. A missing/bad manifest is a no-op.

def _manifest_set_graduated(cart_dir, value):
    """Set manifest.json's `moybyte.graduated` flag to `value` (a bool), preserving all
    other fields. Returns True iff the manifest was rewritten (changed), False on a
    no-op or an unreadable/bad manifest. Atomic (its rename is the torn-write
    proofing). The store's (moy_journal_graduate)."""
    return _native.graduate(cart_dir, bool(value))


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
# a cart's "network" grant hands it the wifi service), so turning it into a free-form kid toggle
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
# every cart. This is reached from the Config tab's Files, which is
# where a project's own folder already lives (docs/history/text_editing_2026-09.md
# step 5) -- and it is offered only where a second script would actually RUN.

def multi_script(cart):
    """Whether THIS cart's runtime loads more than its main file.

    A Lua cart does -- SPEC.md 4, and `lua_ext.cart_chunks` builds the list --
    while the console's Python tier runs `main` and nothing else, and a
    compiled cart is one module (`sources` does not apply to it). So a second
    script listed on either would be a file that silently never runs, which is
    worse than not offering to make one."""
    return (cart or {}).get("runtime", "python") == "lua"


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


def is_compiled(cart):
    """True for a compiled cart: a `"runtime": "wasm"` module, or a native one
    (its manifest's `"moybyte": {"native": [chips]}`, main `main.<chip>.elf`).
    Its main is a module, never text, rebuilt from its `src/` on a
    computer: no code write reaches it or its `src/`, the Code tab shows its
    `src/` to read, and a crash has no line to open."""
    cart = cart or {}
    return cart.get("runtime") == "wasm" or bool(cart.get("native"))


def is_python(cart):
    """Whether `cart` runs on the console's Python tier: the one tier the
    Python parse gate checks and the only one blocks can author, since the
    block compiler emits Python (docs/moy_cart_api.md)."""
    return (not is_compiled(cart)
            and ((cart or {}).get("runtime") or "python") == "python")


def runtime_compile_check(cart, src):
    """The parse gate for THIS cart's runtime -- (ok, message), compile_check's
    shape. Every commit path asks here (the store's save_code, ws.saves.save_code,
    the idle autosave, the live error marker), so "does this compile" cannot
    mean the PYTHON compiler on one path and something else on the next.

    A python cart is compile()d. A "lua" cart (#67) answers ok UNCHECKED:
    neither tier has a syntax-only Lua entry -- the host's hl_exec and the
    device's moycore run_chunk both load (text only) AND lua_pcall in one step,
    over a run that needs a framebuffer under it, which an Editor tab has not
    got. So the gate degrades to COMMIT rather than to never-commit, and a Lua
    syntax error surfaces where it always did, at PLAY. Gating Lua means a
    compile-only entry on BOTH tiers; one alone would have the host and a board
    disagree about what saves. A compiled cart's `src/` is not what runs, so
    it commits unchecked too."""
    if not is_python(cart):
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
    no `sources` answers `[main]`, which is what its absence MEANS.

    A compiled cart answers its `src/` files and never its main: the module is
    not text, and an empty answer is what takes the Code tab away."""
    if not cart:
        return []
    if is_compiled(cart):
        return [n for n, _ in cart.get("src_after") or ()]
    return ([n for n, _ in cart.get("src_before") or ()]
            + [cart.get("main", "main.py")]
            + [n for n, _ in cart.get("src_after") or ()])


def source_text(cart, name=None):
    """The in-RAM text of ONE of `cart`'s scripts, or None when it has no such
    file. `name` None -- or main's own name -- is `src`."""
    if not cart:
        return None
    if is_compiled(cart):
        srcs = cart.get("src_after") or ()
        name = name if name is not None else (srcs[0][0] if srcs else None)
    elif name is None or name == cart.get("main", "main.py"):
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
    if is_compiled(cart):
        # A compiled cart runs its module, which is rebuilt from `src/` on a
        # computer: a write here would be an edit Play never runs, and no
        # text write may ever replace the module.
        return SAVE_BAD_SYNTAX, "a compiled cart's code is rebuilt on a computer"
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
    _write_atomic(cart["path"] + "/" + SPRITES_NAME, hex_text)
    cart["sprites"] = hex_text


def save_flags(cart, flags):
    """Persist a cart's 512 tile flag bytes (SPEC.md 3.5) to flags.moyflags,
    atomically so an interrupted write can't truncate the real file."""
    text = flags_to_hex(flags)
    _write_atomic(cart["path"] + "/flags.moyflags", text)
    cart["flags"] = text


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
from moy_journal import (JOURNAL_DIR, JOURNAL_LOG, JOURNAL_CURSOR,
                         JOURNAL_SNAP_DIR, journal_append, journal_undo,
                         journal_redo, journal_can_undo, journal_can_redo,
                         journal_list, journal_snap, journal_restore)


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
    if not is_python(cart):
        return SAVE_BAD_SYNTAX, "blocks make Python, and this cart is not Python"
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
# A cart's pmem is 256 x SIGNED 32-bit ints (SPEC.md 9) in pmem.bin beside
# main.py: TIC-80's pmem(i)/pmem(i,v) for high scores and save state. The
# file is native/moy_store/moy_pmem.h's record, two slots written in turn, so
# a cut save reads back as the cells before it or the ones it wrote.

PMEM_CELLS = 256
PMEM_FILE = "pmem.bin"


def load_pmem(path):
    """A cart's pmem (path = the .moy folder): 256 ints, all zero where it has
    no record yet."""
    try:
        return _pmem_read(path + "/" + PMEM_FILE)
    except OSError:
        return [0] * PMEM_CELLS


def pmem_path(cart):
    """The file a cart's pmem lives in (needs cart['path'])."""
    return cart["path"] + "/" + PMEM_FILE


def save_pmem_later(cart, cells):
    """Hand a cart's pmem to the store's saver (native/moy_store/moy_save.h):
    queued where a task writes its medium, else written now."""
    _pmem_save(pmem_path(cart), cells)


def wait_pmem(cart):
    """Until no save of the cart's pmem is queued or being written."""
    _save_wait(pmem_path(cart))


def save_pmem(cart, cells):
    """Persist a cart's pmem (256 ints): written when this returns."""
    path = pmem_path(cart)
    _pmem_save(path, cells)
    _save_wait(path)


def tidy_pmem():
    """One step of the pmem log's quiet-moment work (native/moy_store/
    moy_plog.h: a logged save written back to its pmem.bin, else a sector of
    the log erased): True when it did one. What an idle desk frame calls."""
    return bool(_pmem_tidy())


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


# --- cart management (create / duplicate / delete) --------------------------

# A friendly starter cartridge: an editable colored dot, as a GAME project. The
# home's Make "+ New" card creates one of these and opens it in the Editor
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


# errno ENOSPC: what every store's filesystem (FAT on a card, littlefs on the
# P4s' flash, the host's) raises for a write that does not fit.
ENOSPC = 28


def store_full(exc):
    """True for an error that says the store has no room left. A copy or an
    install that fails this way is a notice to the kid ("the store is full"), never
    a silent half-cart: the writers below remove what they had written."""
    if not isinstance(exc, OSError):
        return False
    code = getattr(exc, "errno", None)
    if code is None and exc.args:
        code = exc.args[0]
    return code == ENOSPC


def _whole_or_none(d, fn):
    """`fn()`, which writes a new cart folder `d`. When the store runs out of
    room part way, the folder goes with it -- a cart missing its module or its
    main is one the shelf would list and the Player could never run -- and the
    error is raised for the caller to say so."""
    try:
        return fn()
    except OSError as exc:
        if store_full(exc):
            try:
                _rmtree(d)
            except OSError:
                pass
        raise


def _unique_dir(root, base, ns=USER_NS):
    """A free folder for a made cart, in `ns`; a made cart never takes the
    built-ins' namespace (an author named after it makes USER_NS's)."""
    if ns and slug(ns) == BUILTIN_NS:
        ns = USER_NS
    d = cart_path(root, cart_folder(base, ns))
    if not _exists(d):
        return d
    i = 2
    while _exists(cart_path(root, cart_folder(base + "_" + str(i), ns))):
        i += 1
    return cart_path(root, cart_folder(base + "_" + str(i), ns))


def create(title, root=CARTS_DIR, src=None, cfg=None, edit=None, type="app",
           runtime="python", main="main.py", scenes=None, scene_order=None,
           author=None, palette=None, extensions=None, format=None, fps=None,
           icon=None, canvas=None, listing=None, ns=USER_NS, glass=False,
           pictures=None, vendor_kept=None):
    """Create a new .moy folder and return its loaded cart dict. `runtime`/`main`
    default to a python cart; duplicate() passes a source cart's through so a
    copied "lua" cart (#67) stays a lua cart with its source in main.lua. `scenes`
    ({name: .moyscene text}) + `scene_order` copy a source cart's scene assets (#85),
    registered in manifest.moybyte.assets.scenes and written under scenes/. `author` (#94)
    is optional -- omitted entirely when blank, so a fresh/duplicated cart's
    manifest stays as clean as before this field existed. `format`/`fps` are
    duplicate()'s passthrough (SPEC.md 3.1): copying a spec cart must yield
    another "moy-1" cart at its declared tick, not a restamped moybyte one.
    `listing` is a cart dict whose description/genre/tags (SPEC.md 3.7) the
    manifest keeps, the PICO-8 tag included; `vendor_kept` the Moybyte fields
    a copy keeps (`COPY_KEEPS`), written into its "moybyte" object."""
    d = _unique_dir(root, slug(title), ns)
    _mkdir(d)
    return _whole_or_none(d, lambda: _create_in(
        d, title, src, cfg, edit, type, runtime, main, scenes, scene_order,
        author, palette, extensions, format, fps, icon, canvas, listing, glass,
        pictures, vendor_kept))


def _create_in(d, title, src, cfg, edit, type, runtime, main, scenes,
               scene_order, author, palette, extensions, format, fps, icon,
               canvas, listing, glass=False, pictures=None, vendor_kept=None):
    name = d.replace("\\", "/").rsplit("/", 1)[-1]
    manifest = {
        "format": format or CART_FORMAT, "title": title,
        "id": name[:-len(CART_EXT)],
        "runtime": runtime, "main": main,
    }
    ven = {"type": type, "edit": edit or []}
    ven.update(vendor_kept or {})
    if glass:                         # a per-glass wallpaper stays one
        ven["glass"] = True
    if pictures is not None:          # and a picture wallpaper
        ven["pictures"] = pictures
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
    if listing:                       # description/genre/tags (SPEC.md 3.7) survive a copy
        for k in ("description", "genre", "tags"):
            if listing.get(k):
                manifest[k] = listing[k]
    if scenes:                        # scene assets (#85): register + write (see above)
        ven["assets"] = {"scenes": list(scene_order or sorted(scenes.keys()))}
    manifest[VENDOR_KEY] = ven
    _write(d + "/manifest.json", json.dumps(manifest))
    _write(d + "/" + main, src if src is not None else NEW_TEMPLATE["src"])
    _write(d + "/config.json", json.dumps(cfg or {}))
    if scenes:
        _mkdir(d + "/" + SCENES_DIR)
        for sname, sblob in scenes.items():
            _write(d + "/" + SCENES_DIR + "/" + sname + SCENE_EXT, sblob)
    return load(d)


def new_from_template(root=CARTS_DIR, title="New Cart", ns=USER_NS):
    return create(title, root, src=NEW_TEMPLATE["src"], cfg=dict(NEW_TEMPLATE["cfg"]),
                  edit=NEW_TEMPLATE["edit"], type=NEW_TEMPLATE["type"], ns=ns)


def _copy_cart_files(src, dst, main):
    """Copy a cart folder's asset files (one level of subfolders -- images/,
    scenes/, docs/) into a fresh copy, leaving out what the copy writes itself
    or must not inherit: its manifest, config and `main`, the original's saves
    and undo journal, thumbs/, and the crash-safety leftovers (a copied .bak
    would "recover" the source's bytes over the copy's own file, #154). An
    unreadable entry is skipped; a store that is full raises: a copy that ran
    out of room is not a copy. native/moy_store/moy_cat.c's moy_cat_copy."""
    _native.copy_files(src, dst, main)


# What a copy's program does, which the copy's regenerated manifest keeps:
# the permissions it asks for (its consent is its own, under its new id), the
# MIME types its app opens, the messages it takes, its badges, its entry
# points and whether it draws every frame. A seed cart's own fields (`system`,
# `order`, `targets`, `app`) do not follow a copy: the copy is the kid's.
COPY_KEEPS = ("permissions", "required", "handles", "receives", "achievements",
              "entries", "animates")


def _kept(cart):
    return {k: cart[k] for k in COPY_KEEPS if cart.get(k)}


def duplicate(cart, root=CARTS_DIR, new_title=None, ns=USER_NS):
    if is_compiled(cart) and cart.get("path"):
        return _duplicate_compiled(cart, root, new_title, ns)
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
                 canvas=cart.get("canvas"),                  # spec 1/3.1 cart canvas
                 listing=cart,                               # spec 3.7 description/genre/tags
                 ns=ns, glass=cart.get("glass", False),      # a per-glass wallpaper
                 pictures=cart.get("pictures"),              # a picture wallpaper
                 vendor_kept=_kept(cart))                    # what its program does
    # create() writes the CODE side of a project (manifest/main/config/scenes).
    # Everything else a cart owns -- sprites.moygfx, map.moymap, sounds.json,
    # blocks.json, images/, docs/ -- lives only in the source FOLDER, so a copy
    # takes the files themselves, not a re-serialised dict: that keeps the
    # sprite sheet, tilemap, sounds and cover art, and assets this module
    # doesn't model, which is the only lossless answer for a cart authored by a
    # newer build.
    src_path = cart.get("path")
    if src_path and dup is not None:
        _whole_or_none(dup["path"], lambda: _copy_cart_files(
            src_path, dup["path"], dup["main"]))
        if cart.get("src_before") or cart.get("src_after"):
            _keep_sources(dup["path"], cart_sources(cart))
        return load(dup["path"])
    return dup


def _keep_sources(path, names):
    """A copy's manifest names its scripts in the original's load order
    (`sources`), which create() does not write."""
    mpath = path + "/manifest.json"
    try:
        man = json.loads(_read_recover(mpath))
    except (OSError, ValueError):
        return
    if isinstance(man, dict):
        man["sources"] = list(names)
        _write_atomic(mpath, json.dumps(man))


def _duplicate_compiled(cart, root, new_title, ns=USER_NS):
    """A compiled cart's copy: its own manifest under the new title, its module
    as the bytes it is, and the rest of the folder as `_copy_cart_files` takes
    it. create() is for carts whose main is text."""
    src_path = cart["path"]
    try:
        man = json.loads(_read_recover(src_path + "/manifest.json"))
    except (OSError, ValueError):
        return None
    title = new_title or (cart["title"] + " copy")
    d = _unique_dir(root, slug(title), ns)
    _mkdir(d)
    man["title"] = title
    main = cart["main"]

    def _fill():
        _write(d + "/manifest.json", json.dumps(man))
        _write(d + "/config.json", json.dumps(dict(cart.get("cfg") or {})))
        _copy_bytes(src_path + "/" + main, d + "/" + main)
        _copy_cart_files(src_path, d, main)
    _whole_or_none(d, _fill)
    return load(d)


def _copy_bytes(src, dst, chunk=4096):
    """Copy a file that is not text, in chunks."""
    with open(src, "rb") as fi:
        with open(dst, "wb") as fo:
            while True:
                b = fi.read(chunk)
                if not b:
                    break
                fo.write(b)


def delete(cart):
    _rmtree(cart["path"])


# --- the modules split off this file, re-exported under their old names -------
#
# Nothing in the core above reads any of these; every caller reaches them as
# `moy_carts.X`, so the umbrella is the import site and the leaves stay leaves.
from moy_docs import (_load_store_json, WIFI_STORE_NAME, wifi_store_path,
                      _wifi_networks, load_wifi, save_wifi, remember_wifi, forget_wifi,
                      SYSTEM_DIR, SETTINGS_NAME, system_store_path, parse_settings,
                      settings_text, load_system, save_system, save_system_steps)
from moy_pack import (save_package, rename_package, pack_skip, pack, unpack, adopt,
                      import_archive, import_base, import_target, install_staged)
from moy_seed import (
    seed_builtins, seed_folders,
    _SEED_WBITS, unpack_seed, seed_packed, is_packed,
    RETIRED, RETIRED_GEN, STORE_FORMAT, FORMAT_STEPS, store_version_path,
    load_store_version, load_retired_version, prune_retired, step_store, sweep_store,
    BOARD_STAMP_NAME, board_id, stamp_board, forget_device, keep_record,
    CORE, REMOVED_NAME, is_core, removable, load_removed, note_removed, unremove,
    unremoved, put_back, ADDON_LINE, is_addon, recorded, addon_removed, bring_addon,
    seed_any,
    embedded_floor)


# The user-files layer's names, `moy_carts.list_files` as before, served by
# the native layer (native/moy_store/moy_ufiles.h). A name is resolved at its
# use, so neither this module's import nor a tool that never touches a user file
# builds or needs the layer; FILE_KINDS is the registry read afresh. Where the
# image has no layer the names are absent (AttributeError), as any other is.
def __getattr__(name):
    try:
        import moy_ufiles as uf
    except ImportError:                  # a board without the layer (the Zero)
        uf = None
    if uf is None:
        raise AttributeError(name)
    if name == "FILE_KINDS":
        return uf.kinds()
    return getattr(uf, name)
