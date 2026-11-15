# Map (grep -n a name to jump there):
#   seed_builtins           a roster of cart dicts written to the store, by version
#   _make_room              the version rule a seed is written by, everywhere
#   seed_folders            cart folders seeded into a store (the browser's, the host's)
#   adopt_bundle            a served bundle taken whole on a first visit
#   seed_packed             the packed roster, one cart inflated at a time
#   prune_retired           the folders of seeds that no longer ship, once per store
#   sweep_store             the store's format stamp (STORE_FORMAT) and its one door
#   seed_any                the seed a board's boot runs
"""Seeding the `.moy` store: the built-in roster, its PACKED form, and the
once-per-store sweep of seeds that no longer ship.

`seed_any` is the seed a board's boot runs, after its scan (moy_catalogue.seed).
Everything here writes carts
through the store core's rules (`moy_store_base`) and nothing here is read by
the core, so `moy_carts` re-exports these names rather than the reverse.
"""

import json

from moy_fs import (_exists, _mkdir, _read, _read_bytes, _read_recover,
                    _remove, _write, _write_bytes)
from moy_store_base import (CARTS_DIR, _has, _listing, _rmtree, _sibling_path, cart_path,
                            cart_folder, store_path, BUILTIN_NS, CART_EXT, VENDOR_KEY)
from moy_fs import _native as _store


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


def _make_room(d, version):
    """Whether a seed at `version` is written into the cart folder `d`, the
    folder made ready for it when it is: one that is absent takes it, one
    whose manifest reads at `version` or newer does not, an older one is
    pruned to the kid's saves and tuning, and one with no manifest that reads
    (a seed a power cut stopped: nothing in it is the kid's) is removed."""
    if not _exists(d):
        return True
    whole = _has_manifest(d)
    if whole and version <= _cart_version(d):
        return False
    if whole:
        _prune_for_reseed(d)
    else:
        _rmtree(d)
    return True


def seed_builtins(seed_list, root=CARTS_DIR, progress=None, ns=BUILTIN_NS):
    """Write missing/outdated built-in carts (cart dicts) to the store as
    editable .moy folders: the folders written, in roster order.

    The writer is the store's (native/moy_store/moy_seed.c), the one body a
    seed goes through on every tier: each dict travels to it as a packed
    roster entry (`_pack`). Its rules: the manifest is REGENERATED from the
    cart's fields, in their key order, so the "Make it mine" cards render on
    device as on the host; every payload the cart carries is written (a sheet,
    sounds, map, flags, cover, images, blocks, scenes, the other scripts) and
    the manifest LAST, published, so a folder whose manifest reads is whole.

    Versioning (the re-seed, #47): a cart already in the store is left as it
    is UNLESS the built-in's "version" is newer -- then its CODE + ART are
    replaced and the kid's data (pmem.json saves, config.json tuning) stays.
    Pre-versioning carts read as version 0. A folder with no manifest that
    reads is a seed a power cut stopped, and is written again from nothing.

    `progress(done, total, title)` is called once per seed considered, so a
    boot screen can show a bar; a hook that raises is dropped, never fatal."""
    return _store.seed(root, [_pack(c) for c in seed_list], {}, progress, ns)


def _pack(cart):
    """A cart dict as a packed roster entry: (title, version, its JSON as raw
    deflate at _SEED_WBITS), what tools/gen_device_carts.py --packed writes."""
    text = json.dumps(cart).encode()
    try:
        import deflate
    except ImportError:                  # CPython (host suites, the simulator)
        import zlib
        comp = zlib.compressobj(1, zlib.DEFLATED, -_SEED_WBITS)
        blob = comp.compress(text) + comp.flush()
    else:
        import io as _io
        buf = _io.BytesIO()
        stream = deflate.DeflateIO(buf, deflate.RAW, _SEED_WBITS)
        stream.write(text)
        stream.close()
        blob = buf.getvalue()
    return (cart["title"], int(cart.get("version", 0)), blob)


# -- a store of RECORD seeded from cart folders --------------------------------
#
# A browser on a static host keeps its carts in its own storage, and that store
# is the RECORD, like the Zero's: a seed game the kid deleted stays deleted. The
# page hands the VM the served bundle as folders, and this decides what of it
# the store takes: everything when the store has no cart yet (a first visit),
# else only the SYSTEM carts (manifest `moybyte.system`) and a torn seed of
# any, by the rules a board seeds its built-ins by (`_make_room`). The page writes what this reports into
# its storage, removals first and in the order given.

_PACK_SKIP = ("thumbs", "__pycache__")


def _is_system(man):
    ven = man.get(VENDOR_KEY) if isinstance(man, dict) else None
    return isinstance(ven, dict) and ven.get("system") is True


def _folder_files(path, rel, out, depth=0):
    """Each file under the folder at `path` as `rel/<its path>`, sorted."""
    if depth > 6:
        return out
    names = _listing(path) or {}
    for n in sorted(names):
        if n in _PACK_SKIP:
            continue
        if names[n]:
            _folder_files(path + "/" + n, rel + "/" + n, out, depth + 1)
        else:
            out.append(rel + "/" + n)
    return out


def _copy_cart(src, d, rel, keep):
    """The cart folder `src` copied into `d`, each of `keep` already there left
    as it is and the manifest last: the store-relative paths written."""
    files = _folder_files(src, "", [])
    files = [f for f in files if f != "/manifest.json"] + (
        ["/manifest.json"] if "/manifest.json" in files else [])
    _mkdir(d)
    wrote = []
    for f in files:
        name = f[1:]
        if name in keep and _exists(d + "/" + name):
            continue
        segs = name.split("/")
        for k in range(1, len(segs)):
            _mkdir(d + "/" + "/".join(segs[:k]))
        _write_bytes(d + "/" + name, _read_bytes(src + f, 1 << 30))
        wrote.append(rel + f)
    return wrote


def seed_folders(src, root=CARTS_DIR, record=True):
    """The cart folders under `src` seeded into the store `root`: {"first":
    whether the store had no cart, "wrote": the store-relative paths written,
    each cart's manifest last, "removed": those a re-seed removed, "served":
    every cart folder `src` holds, taken or not}. A store of `record` takes
    every cart only on its first visit; one that is a cache of the built-ins
    (the host's, as a board's) takes every cart every time."""
    have = _listing(root) or {}
    first = not any(have[n] and n.endswith(CART_EXT) for n in have)
    wrote, removed, served = [], [], []
    names = _listing(src) or {}
    for name in sorted(names):
        if not (names[name] and name.endswith(CART_EXT)):
            continue
        try:
            man = json.loads(_read(src + "/" + name + "/manifest.json"))
        except (OSError, ValueError):
            continue                  # not a manifest anything reads: not a cart
        if not isinstance(man, dict):
            continue
        served.append(name)
        d = cart_path(root, name)
        there = _exists(d)
        # A non-system cart after the first visit: only a torn seed of one.
        if not (first or not record or _is_system(man)
                or (there and not _has_manifest(d))):
            continue
        before = _folder_files(d, name, []) if there else []
        try:
            version = int(man.get("version", 0))
        except (TypeError, ValueError):
            version = 0
        if not _make_room(d, version):
            continue
        wrote += _copy_cart(src + "/" + name, d, name, _RESEED_PRESERVE)
        removed += [r for r in before if not _exists(store_path(root, r))]
    return {"first": first, "wrote": wrote, "removed": removed,
            "served": served}


def adopt_bundle(src, dest, top=None, skip=None):
    """The bundle staged at `src` copied whole into the store `dest` when the
    store holds none of its files yet (a first visit): with `top`, its named
    files (paths in it) alone, else every file in a folder of it, nothing
    `skip` names. The report `seed_folders` gives."""
    if top is not None:
        first = not any(_exists(dest + "/" + t) for t in top)
        wrote = []
        if first:
            for t in top:
                if _exists(src + "/" + t):
                    segs = t.split("/")
                    for k in range(1, len(segs)):
                        _mkdir(dest + "/" + "/".join(segs[:k]))
                    _write_bytes(dest + "/" + t, _read_bytes(src + "/" + t, 1 << 30))
                    wrote.append(t)
        return {"first": first, "wrote": wrote, "removed": []}
    have = _listing(dest) or {}
    first = not any(have[n] and not (skip and skip(n)) for n in have)
    wrote = []
    if first:
        names = _listing(src) or {}
        _mkdir(dest)
        for n in sorted(names):
            if names[n] and not (skip and skip(n)):
                wrote += _copy_cart(src + "/" + n, dest + "/" + n, n, ())
    return {"first": first, "wrote": wrote, "removed": []}


# -- the PACKED seed roster (2026-08-30) -------------------------------------
#
# A roster of cart DICTS, as the console boards once froze it, comes
# straight out of a frozen `carts_data.CARTS` -- 732 KB of literal source whose
# strings live in ROM and cost no heap. That representation is free on a board
# with the flash for it, and the Zero is the board without. Both forms were
# BUILT: the plain roster makes a 2,830,672 B image of a 2,883,584 B OTA slot,
# 51 KB left -- it fits, by less than 2%, under the board's headroom floor and
# so a failed build, in a slot the board pays for TWICE.
#
# So the Zero froze `carts_data.CARTS_Z` instead: the SAME carts, one raw
# deflate stream each (tools/gen_device_carts.py --packed), which builds to
# 2,399,232 B and leaves 473 KB. The unit of work is ONE CART -- inflate it,
# write it, drop it -- because the roster inflates to 732 KB and no board
# should ever hold that at once.
#
# EVERY BOARD FREEZES THE PACKED ROSTER since 2026-08-30, and the argument on
# the three that fit is not the fit, it is the MARGIN: the roster only grows and
# a slot does not, so the board with the least room decides the form for all of
# them, and a lever that lives on one target is the lever the next port forgets.
# `seed_any` below is the one door a boot calls, so nothing else had to change.
#
# Nothing about the seed CONTRACT changes: the #47 version rules, the manifest
# regeneration and the preserved pmem/config are native/moy_store/moy_seed.c's,
# the one body that writes a seed to a store, on every tier.

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
    """One packed blob -> the cart dict it packs.

    `json.load` over the inflating stream, NOT `json.loads(stream.read())`:
    read() materializes the whole inflated document beside the objects parsed
    out of it, and the parse allocates those anyway. MEASURED under the desktop
    MicroPython, as the smallest heap the whole roster seeds in -- 680 KB
    streaming against 896 KB for the read-all version, which is also ~40%
    slower. tests/test_seed_pack.py asserts at 768 KB, between the two.
    """
    return json.load(_packed_stream(blob))


def seed_packed(packed, root=CARTS_DIR, progress=None, only_new=False,
                ns=BUILTIN_NS, room=None):
    """A PACKED roster seeded, one cart inflated at a time (`seed_builtins`'
    rules).

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

    `room`, where given, is asked before each cart and again once it has
    landed: False before stops the seed, False after removes the cart just
    written and stops it, so the room it keeps is never spent on a seed (the
    Zero's SEED_RESERVE).

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
        if room is not None and not room():
            break
        # One cart in flight: the store's seed with a ONE-element roster and
        # no progress hook, because the counting is this loop's.
        _store.seed(root, [entry], {}, None, ns)
        if room is not None and not room():
            _rmtree(d)
            break
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
# is the one text app (docs/theme_and_shell_2026-11.md §10).
RETIRED = ("Ray Test", "Ray Lua", "Layer Test", "Battle City", "Bubble Trouble",
           "Sheets", "Beeper", "Writer")
# Generation 5 is #162's rename: every built-in moved from `<slug>.moy` into
# its namespace, `moybyte.<slug>.moy`, so the sweep also takes the roster's
# own titles' bare folders (`sweep_store`'s `seed`). Generation 6 takes
# RETIRED_FOLDERS: the names a host store gave the built-ins whose source
# folder was not their title's slug, before system_carts/ shipped each one
# under its id. Generation 7 takes the Wi-Fi tool, deleted 2026-10-13 when
# Settings' panel became the one Wi-Fi implementation. Generation 8 takes the
# seed Mesh, which left the seed set 2026-11-10 for moybyte-org/carts: by its
# folder, never its title, whose bare folder (mesh.moy) is the installed one.
# It also takes RETIRED_SIBLINGS, folders beside the carts folder: `written/`
# held a compiled cart's written files until they moved to `apps/<id>/`
# (bd4d890a9, 2026-11-08), and nothing reads it. The generation lives in the
# store's stamp (`store.ver`, below); `retired.ver`, which held it alone
# before the stamp, is a sibling the sweep takes.
RETIRED_FOLDERS = tuple("moybyte.%s.moy" % s for s in (
    "ocean", "pet", "platformer", "scroll_demo", "tap_red", "theme_picker",
    "wallpaper_space", "wifi", "mesh"))
RETIRED_SIBLINGS = ("written", "retired.ver")
RETIRED_GEN = 8
# -- the store's format and its one door (docs/refactor_2026-11.md, phase 13) --
#
# A store carries a FORMAT STAMP: the sidecar `store.ver` beside the carts
# folder, one line "moystore <format> <generation>\n", the format the store's
# layout and files are in and the RETIRED generation it was swept at. One file,
# so the warm path of the door below stays one small read. It is written the
# first time a store opens (`sweep_store` on a store with none: a new store, at
# STORE_FORMAT) and after every sweep or step; its reader is strict, and a line
# it does not read is no stamp.
#
# STORE_FORMAT 1 is the trial's (2026-11-15): tests/fixtures/store_trial/ is a
# store in it, and tests/test_store_one_body.py opens it on every tree.
#
# THE DOOR. A format change ships as a STEP in FORMAT_STEPS -- `step(root)`
# takes the store at `root` from its key's format to the next -- and a bumped
# STORE_FORMAT, in the same commit as a test that takes the fixture through it.
# `sweep_store` runs the steps of a store behind this firmware, in order, each
# stamped as it lands, and BEFORE THE FIRST, the export of a kid's work: the
# backup archive (runtime/carts/moy_backup.py) in the user files. An export
# that cannot be written runs no step: the store stays in its format, readable
# by nothing that changed, and the next boot tries again. A store AHEAD of this
# firmware (a newer image's, then a downgrade) is not touched. No step exists:
# none is written until a format changes (CLAUDE.md, "No store migrations
# until there are users", of which this door is the one exception).
STORE_FORMAT = 1
STORE_VER_NAME = "store.ver"
FORMAT_STEPS = {}
_VER_HEAD = "moystore "


def store_version_path(root=CARTS_DIR):
    """The store's stamp: a sibling of the carts folder."""
    return _sibling_path(root, STORE_VER_NAME)


def load_store_version(root=CARTS_DIR):
    """The store's (format, generation), or None when it has no stamp this
    reads: absent, unreadable, or any other line."""
    try:
        line = _read(store_version_path(root))
    except (OSError, ValueError):
        return None
    if not line.startswith(_VER_HEAD) or not line.endswith("\n"):
        return None
    bits = line[len(_VER_HEAD):-1].split(" ")
    if len(bits) != 2 or not all(b.isdigit() for b in bits):
        return None
    fmt, gen = int(bits[0]), int(bits[1])
    return (fmt, gen) if fmt > 0 else None


def _save_store_version(root, fmt, gen):
    _write(store_version_path(root), "%s%d %d\n" % (_VER_HEAD, fmt, gen))


def load_retired_version(root=CARTS_DIR):
    """The generation the store was swept at -- 0 with no stamp, so a store
    that has none sweeps once."""
    v = load_store_version(root)
    return 0 if v is None else v[1]


def prune_retired(root=CARTS_DIR, titles=RETIRED, generation=RETIRED_GEN,
                  folders=(), siblings=(), ver=None):
    """Remove the folders of seeds that no longer ship, once per store: each
    title's bare folder, each of `folders` by name, and each of `siblings`
    beside the carts folder. `ver` is the stamp the caller already read.

    Returns the number of folders removed (0 when the store is already at this
    generation, which is the warm-boot path and costs one small file read)."""
    if ver is None:
        ver = load_store_version(root)
    fmt, gen = ver if ver is not None else (STORE_FORMAT, 0)
    if gen >= generation:
        return 0
    gone = 0
    names = [cart_folder(t) for t in titles] + list(folders)
    paths = [cart_path(root, n) for n in names] + [_sibling_path(root, n) for n in siblings]
    for d in paths:
        if _exists(d):
            _rmtree(d)
            _remove(d)
            gone += 1
    try:
        _save_store_version(root, fmt, generation)
    except OSError:
        return gone          # a read-only store: sweep again next boot, harmless
    return gone


def _export(root, fmt):
    """The export before a step: the backup archive, named for the format the
    store leaves."""
    import moy_backup
    return moy_backup.archive(root, name="moybyte-format-%d.zip" % fmt)


def step_store(root, ver, export=_export):
    """The store at `root`, stamped `ver`, taken to STORE_FORMAT through
    FORMAT_STEPS, each step stamped as it lands; `export(root, format)` runs
    before the first (None skips it: a restore's stage, whose archive is the
    export). Returns the stamp it leaves."""
    fmt, gen = ver
    if export is not None and fmt < STORE_FORMAT:
        export(root, fmt)
    while fmt < STORE_FORMAT:
        FORMAT_STEPS[fmt](root)
        fmt += 1
        _save_store_version(root, fmt, gen)
    return (fmt, gen)


def sweep_store(root=CARTS_DIR, seed=None, export=_export):
    """The one-shot pass a store OPENING runs, behind one door. Returns the
    number of retired folders removed. `seed` is the roster the store is
    seeded from, whose titles' bare folders generation 5 takes.

    The door is gated on the store's stamp, so the warm path is one small
    read: a store behind STORE_FORMAT is stepped (the format door above, its
    export first), one ahead of it is left alone, and a store behind
    RETIRED_GEN is swept (`prune_retired`)."""
    ver = load_store_version(root)
    if ver is not None and ver[0] > STORE_FORMAT:
        return 0
    if ver is not None and ver[0] < STORE_FORMAT:
        try:
            ver = step_store(root, ver, export)
        except OSError as exc:
            print("store: format %d kept, no export (%r)" % (ver[0], exc))
            return 0
    titles = list(RETIRED)
    if seed:
        titles += [item[0] if is_packed(seed) else item["title"] for item in seed]
    return prune_retired(root, titles, folders=RETIRED_FOLDERS, siblings=RETIRED_SIBLINGS,
                         ver=ver)


def seed_any(seed, root, present, progress=None):
    """Seed a roster of either form AFTER the scan of `root`, the one seed a
    console board's boot runs (moy_catalogue.seed).

    `present` is each cart folder the scan found and its version ({folder:
    version}), so deciding what to write reads no manifest and lists nothing:
    a built-in absent from it, or present and older, is written whole, its
    saves and config kept across a re-seed (`seed_builtins`' rules). A packed
    roster is inflated one cart at a time, and only for a cart that is
    written; a roster of dicts is packed first. `progress(done, total,
    title)` is called once per built-in. Returns the folders written, for the
    caller to read again; each built-in's folder is `moybyte.<slug>.moy`
    (#162). native/moy_store/moy_seed.c writes them."""
    if not is_packed(seed):
        seed = [_pack(c) for c in seed]
    return _store.seed(root, seed, present, progress, BUILTIN_NS)


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
