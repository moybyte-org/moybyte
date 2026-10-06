"""Seeding the `.moy` store: the built-in roster, its PACKED form, and the
once-per-store sweep of seeds that no longer ship.

`seed_any` is the seed a board's boot runs, after its scan (moy_catalogue.seed).
Everything here writes carts
through the store core's rules (`moy_store_base`) and nothing here is read by
the core, so `moy_carts` re-exports these names rather than the reverse.
"""

import json

try:
    from moy_fs import (_exists, _mkdir, _read, _read_recover, _remove,
                        _write, _write_atomic, _write_bytes)
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime.moy_fs import (_exists, _mkdir, _read, _read_recover, _remove,
                                _write, _write_atomic, _write_bytes)
try:
    from moyimg import _b64_decode
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime.moyimg import _b64_decode
try:
    from moy_store_base import (CARTS_DIR, CART_FORMAT, COVER_FILE, FLAGS_NAME, IMAGES_DIR, IMAGE_EXT, SCENES_DIR, SCENE_EXT, _canvas_str, _has, _listing, _rmtree, _sibling_path, cart_path, cart_folder, BUILTIN_NS)
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime.moy_store_base import (CARTS_DIR, CART_FORMAT, COVER_FILE, FLAGS_NAME, IMAGES_DIR, IMAGE_EXT, SCENES_DIR, SCENE_EXT, _canvas_str, _has, _listing, _rmtree, _sibling_path, cart_path, cart_folder, BUILTIN_NS)
try:
    from moy_fs import _native as _store
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime.moy_fs import _native as _store
if _store is not None and not hasattr(_store, "seed"):
    _store = None


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


def _has_manifest(path):
    """Whether the cart folder at `path` has a manifest that reads as one: a
    seeded folder is whole once it does (seed_builtins writes it last)."""
    try:
        return isinstance(json.loads(_read_recover(path + "/manifest.json")), dict)
    except Exception:  # noqa: BLE001 -- unreadable is "not whole"
        return False


def _prune_for_reseed(path):
    """Everything in a cart folder a re-seed replaces, removed: all but the
    kid's saves and config (_RESEED_PRESERVE, their crash backups too) and
    the manifest, which the new one replaces last."""
    keep = _RESEED_PRESERVE + ("manifest.json",)
    for name in _listing(path) or {}:
        if name in keep or (name.endswith(".bak") and name[:-4] in keep):
            continue
        p = path + "/" + name
        _rmtree(p)
        _remove(p)


def seed_builtins(seed_list, root=CARTS_DIR, progress=None, ns=BUILTIN_NS):
    """Write missing/outdated built-in carts to SD as editable .moy folders.

    A seed dict that carries a non-empty "sprites" hex blob also gets a
    sprites.moygfx written, so the device's paint editor (and the cart's spr()
    tile draws) have the real art -- without this the device seeds blank sheets
    and the games fall back to nothing. The manifest is COMPLETE (canvas +
    permissions + full edit schema + version) so the visual "Make it mine" cards
    render on device exactly as on host.

    Versioning (the re-seed): a cart already on SD is left untouched UNLESS the
    built-in's "version" is newer than the on-SD one -- then its CODE + ART are
    REPLACED (everything in the folder but the kid's data is removed first),
    and the kid's data (pmem.json saves + config.json tuning, see
    _RESEED_PRESERVE) stays where it is. So a content update keeps high scores
    and settings; on-device edits to a built-in's *code/sprites* are discarded.
    Pre-versioning carts read as version 0, so bumping a built-in to >=1
    refreshes stale copies automatically. Bump a built-in's manifest "version"
    whenever you change its content. A folder with no manifest that reads is a
    seed a power cut stopped, and is written again from nothing.

    (Migration note: a preserved config.json keeps the kid's old values, so a
    NEW default for an EXISTING config key won't apply to an already-seeded cart;
    a brand-new key just falls back to its code default via cfg(key, default).)"""
    # `progress(done, total, title)` is called once per seed considered, so a
    # boot screen can show a bar. Measured on a full-erase P4 boot: seeding all
    # 32 built-ins takes 17.5 of the 25 seconds before the desktop composes, and
    # it is the only stage of that boot with a countable unit of work. Optional
    # and best-effort -- a progress callback must never be able to fail a seed.
    _total = len(seed_list)
    wrote = []
    for _seeded, cart in enumerate(seed_list):
        if progress is not None:
            try:
                progress(_seeded, _total, cart.get("title", ""))
            except Exception:                 # noqa: BLE001
                progress = None               # broken hook: drop it, keep seeding
        name = cart_folder(cart["title"], ns)
        d = cart_path(root, name)
        seed_ver = int(cart.get("version", 0))
        if _exists(d):
            whole = _has_manifest(d)
            if whole and seed_ver <= _cart_version(d):
                continue
            if whole:
                _prune_for_reseed(d)   # newer built-in: its saves + tuning stay
            else:
                _rmtree(d)             # a seed the power cut: nothing is the kid's
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
        # SPEC.md 4's load order, rebuilt from the two lists: `main` sits
        # between them and must appear, so a reader gets the same order this
        # cart was written with. Omitted entirely for a one-script cart, which
        # is what [main] means.
        _pre = list(cart.get("src_before") or ())
        _post = list(cart.get("src_after") or ())
        if _pre or _post:
            manifest["sources"] = ([n for n, _ in _pre]
                                   + [manifest["main"]]
                                   + [n for n, _ in _post])
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
        _write(d + "/" + cart.get("main", "main.py"), cart["src"])
        # The cart's other scripts beside it (SPEC.md 4), and `sources` in the
        # manifest above naming the order -- a port's main.lua cannot run
        # without its shim chunk, and nothing else says where that goes.
        for script, text in list(cart.get("src_before") or ()) \
                + list(cart.get("src_after") or ()):
            _write(d + "/" + script, text)
        if not _exists(d + "/config.json"):      # the kid's config stands
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
        cover = cart.get("cover")                 # cover.png as base64, optional (SPEC.md 3.6)
        if cover:
            _write_bytes(d + "/" + COVER_FILE, _b64_decode(cover))
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
        # The manifest LAST, and published: a folder whose manifest reads is
        # whole. A power cut before it leaves the older version's manifest (a
        # re-seed) or none (a first seed), so the next boot's seed writes the
        # cart again; a manifest first would carry the seed's version over a
        # folder missing its payloads, and the version check would keep it so.
        _write_atomic(d + "/manifest.json", json.dumps(manifest))
        wrote.append(name)
    return wrote


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


def seed_packed(packed, root=CARTS_DIR, progress=None, only_new=False,
                ns=BUILTIN_NS):
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
    # Which seeds are there at all, from one listing of the store: a lookup
    # per seed walks the card's directories again for each.
    names = _listing(root)
    for index, entry in enumerate(packed):
        title, version, blob = entry
        if progress is not None:
            try:
                progress(index, total, title)
            except Exception:             # noqa: BLE001 -- as in seed_builtins
                progress = None
        name = cart_folder(title, ns)
        d = cart_path(root, name)
        if _has(names, root, name) and (only_new
                                        or int(version) <= _cart_version(d)):
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
        seed_builtins([unpack_seed(blob)], root, ns=ns)
        if names is not None:
            names[name] = True
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


# -- seeds that no longer ship (2026-09-06) ----------------------------------
#
# A seed is written to the store once and then LIVES there: nothing in the #47
# version rules can express "this cart is gone", so a retired built-in stayed
# on every flashed board's shelf forever and only a hand-deleted folder took it
# off. RETIRED is that expression -- the titles a roster used to carry -- and
# `prune_retired` removes their folders once per store.
#
# ONCE is the whole design. The generation counter is written into the store
# after a sweep, so the pass runs when a store is behind and never again: a kid
# who later makes their own cart under a retired title keeps it. Bump
# RETIRED_GEN in the same commit that adds titles, or the new ones never sweep.
#
# The Zero is deliberately NOT a caller (it seeds `seed_packed(only_new=True)`
# directly): its store is the RECORD -- the only copy of a cart made in a
# browser -- where a console board's store is a CACHE of the image's built-ins.
#
# The list is the 2026-09-06 bench fold (three benches became phases of Bench
# and Bench Lua) plus the 2026-07-29 RENAME's leftovers: b4cc0d8 renamed the
# folders as well as the titles, so every board seeded before it has carried a
# second, stale copy of Brick Siege and Harpoon Pop ever since. Sheets and
# Beeper are the 2026-09-07 deletions: the spreadsheet app, and the audio demo
# whose verbs the cart API now shows off instead. Writer is the same day's: the
# notebook app is gone and Notes -- a CART over the shell's editor handle --
# is the one text app (docs/text_editing_2026-09.md).
RETIRED = ("Ray Test", "Ray Lua", "Layer Test", "Battle City", "Bubble Trouble",
           "Sheets", "Beeper", "Writer")
# Generation 5 is #162's rename: every built-in moved from `<slug>.moy` into
# its namespace, `moybyte.<slug>.moy`, so the sweep also takes the roster's
# own titles' bare folders (`sweep_store`'s `seed`).
RETIRED_GEN = 5
RETIRED_VER_NAME = "retired.ver"


def retired_version_path(root=CARTS_DIR):
    """Sidecar (a sibling of the carts dir, like system_icons.ver) holding the
    RETIRED generation this store has already been swept for."""
    return _sibling_path(root, RETIRED_VER_NAME)


def load_retired_version(root=CARTS_DIR):
    """The generation the store was swept at -- 0 when absent/unreadable, so a
    store that predates this sweeps once."""
    try:
        return int(_read(retired_version_path(root)).strip())
    except (OSError, ValueError, AttributeError):
        return 0


def prune_retired(root=CARTS_DIR, titles=RETIRED, generation=RETIRED_GEN,
                  folders=()):
    """Remove the folders of seeds that no longer ship, once per store: each
    title's bare folder, and each of `folders` by name.

    Returns the number of folders removed (0 when the store is already at this
    generation, which is the warm-boot path and costs one small file read)."""
    if load_retired_version(root) >= generation:
        return 0
    gone = 0
    names = [cart_folder(t) for t in titles] + list(folders)
    for name in names:
        d = cart_path(root, name)
        if _exists(d):
            _rmtree(d)
            gone += 1
    try:
        _write(retired_version_path(root), str(int(generation)))
    except OSError:
        return gone          # a read-only store: sweep again next boot, harmless
    return gone


def sweep_store(root=CARTS_DIR, seed=None, folders=()):
    """The one-shot pass a store OPENING runs, behind one door. Returns the
    number of retired folders removed. `seed` is the roster the store is
    seeded from and `folders` any other bare built-in folders (the host's):
    generation 5 takes their folders from before #162's namespace.

    The door is kept for the next sweep that earns it, and the bar it has to
    clear is `prune_retired`'s: gated on a generation sidecar, so the warm path
    is one small read and the cold one is bounded. A FORMAT change does not
    clear it and does not belong here -- readers are strict and a bumped seed
    version re-seeds the content (CLAUDE.md, 2026-09-07)."""
    titles = list(RETIRED)
    if seed:
        titles += [item[0] if is_packed(seed) else item["title"] for item in seed]
    return prune_retired(root, titles, folders=folders)


def seed_any(seed, root, present, progress=None):
    """Seed a roster of either form AFTER the scan of `root`, the one seed a
    console board's boot runs (moy_catalogue.seed).

    `present` is each cart folder the scan found and its version ({folder:
    version}), so deciding what to write reads no manifest and lists nothing:
    a built-in absent from it, or present and older, goes to `seed_builtins`,
    which writes it whole and keeps its saves and config across a re-seed. A
    packed roster is inflated one cart at a time, and only for a cart that is
    written. `progress(done, total, title)` is called once per built-in.
    Returns the folders written, for the caller to read again. A packed
    roster is the native store's to write wherever it is linked (moy_seed.c,
    the same files byte for byte); each built-in's folder is
    `moybyte.<slug>.moy` (#162)."""
    packed = is_packed(seed)
    if packed and _store is not None:
        return _store.seed(root, seed, present, progress, BUILTIN_NS)
    total = len(seed)
    wrote = []
    for index, item in enumerate(seed):
        if packed:
            title, version = item[0], item[1]
        else:
            title, version = item["title"], item.get("version", 0)
        if progress is not None:
            try:
                progress(index, total, title)
            except Exception:             # noqa: BLE001 -- as in seed_builtins
                progress = None
        name = cart_folder(title, BUILTIN_NS)
        if name in present and int(version) <= present[name]:
            continue
        wrote += seed_builtins([unpack_seed(item[2]) if packed else item], root)
    return wrote


def embedded_floor(seed):
    """The read-only carts a board falls back to when it has NO writable store.

    Nearly unreachable since 2026-08-30: every board now retries on internal
    flash before it gets here (boot_carts.load_carts `fallback_root`), so
    reaching this means the internal VFS itself is gone -- a board that cannot
    save anything at all. That is the only reason inflating the WHOLE roster is
    acceptable here: ~732 KB held at once, which every console board has in
    PSRAM and none should ever spend on a warm path. The Zero, whose store is
    the only thing it has, has no floor to fall to and does not call this.
    """
    if is_packed(seed):
        return [unpack_seed(blob) for _title, _version, blob in seed]
    return [dict(c) for c in seed]
