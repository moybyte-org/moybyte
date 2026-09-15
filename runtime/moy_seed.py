"""Seeding the `.moy` store: the built-in roster, its PACKED form, and the
once-per-store sweep of seeds that no longer ship.

`seed_any` is the one door a board's boot calls. Everything here writes carts
through the store core's rules (`moy_store_base`) and nothing here is read by
the core, so `moy_carts` re-exports these names rather than the reverse.
"""

import json

try:
    from moy_fs import (_exists, _mkdir, _read, _read_recover, _write)
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime.moy_fs import (_exists, _mkdir, _read, _read_recover, _write)
try:
    from moy_store_base import (CARTS_DIR, CART_FORMAT, FLAGS_NAME, IMAGES_DIR, IMAGE_EXT, SCENES_DIR, SCENE_EXT, _canvas_str, _rmtree, _sibling_path, slug)
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime.moy_store_base import (CARTS_DIR, CART_FORMAT, FLAGS_NAME, IMAGES_DIR, IMAGE_EXT, SCENES_DIR, SCENE_EXT, _canvas_str, _rmtree, _sibling_path, slug)


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
        _write(d + "/manifest.json", json.dumps(manifest))
        _write(d + "/" + cart.get("main", "main.py"), cart["src"])
        # The cart's other scripts beside it (SPEC.md 4), and `sources` in the
        # manifest above naming the order -- a port's main.lua cannot run
        # without its shim chunk, and nothing else says where that goes.
        for name, text in list(cart.get("src_before") or ()) \
                + list(cart.get("src_after") or ()):
            _write(d + "/" + name, text)
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
RETIRED_GEN = 4
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


def prune_retired(root=CARTS_DIR, titles=RETIRED, generation=RETIRED_GEN):
    """Remove the folders of seeds that no longer ship, once per store.

    Returns the number of folders removed (0 when the store is already at this
    generation, which is the warm-boot path and costs one small file read)."""
    if load_retired_version(root) >= generation:
        return 0
    gone = 0
    for title in titles:
        d = root + "/" + slug(title) + ".moy"
        if _exists(d):
            _rmtree(d)
            gone += 1
    try:
        _write(retired_version_path(root), str(int(generation)))
    except OSError:
        return gone          # a read-only store: sweep again next boot, harmless
    return gone


def sweep_store(root=CARTS_DIR):
    """The one-shot pass a store OPENING runs, behind one door. Returns the
    number of retired folders removed.

    The door is kept for the next sweep that earns it, and the bar it has to
    clear is `prune_retired`'s: gated on a generation sidecar, so the warm path
    is one small read and the cold one is bounded. A FORMAT change does not
    clear it and does not belong here -- readers are strict and a bumped seed
    version re-seeds the content (CLAUDE.md, 2026-09-07)."""
    return prune_retired(root)


def seed_any(seed, root=CARTS_DIR, progress=None):
    """Seed a roster of either form. The one call a board's boot makes."""
    sweep_store(root)
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
