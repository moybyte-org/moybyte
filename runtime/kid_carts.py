# KidCode SD cartridge store.
#
# Cartridges live as .kcart folders under /sd/kidcode/carts/<name>.kcart/:
#   manifest.json   title, type, runtime, main, edit-schema
#   main.py         the cartridge source (_init/_update/_draw + kid API)
#   config.json     user-edited values (the Make-it-mine surface)
#   (sprites later)
#
# Mirrors the host runtime/cartridge.py model, but MicroPython-friendly (no
# shutil; os-only). Functions take a `root` so the format/seed/scan logic is
# host-testable against a temp dir. SD shares the SPI bus with the display, so
# the caller mounts SD (kidcode_sd) with the LoRa/TFT CS deselected first.

import json

try:
    import os
except ImportError:  # pragma: no cover
    os = None

CARTS_DIR = "/sd/kidcode/carts"
CART_FORMAT = "kidcode-cart-v1"

# A single shared sprite sheet lives alongside the carts dir (one level up, so
# it sits beside every <name>.kcart folder). Tiles painted here are reusable
# across carts; the import-tile primitive copies tiles between any two sheets.
SHARED_SHEET_NAME = "shared.kgfx"


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


def _preserve_kid_data(path):
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
    """Write missing/outdated built-in carts to SD as editable .kcart folders.

    A seed dict that carries a non-empty "sprites" hex blob also gets a
    sprites.kgfx written, so the device's paint editor (and the cart's spr()
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
    automatically -- no more "clear /sd/kidcode/carts by hand". Bump a built-in's
    manifest "version" whenever you change its content.

    (Migration note: a preserved config.json keeps the kid's old values, so a
    NEW default for an EXISTING config key won't apply to an already-seeded cart;
    a brand-new key just falls back to its code default via cfg(key, default).)"""
    for cart in seed_list:
        d = root + "/" + slug(cart["title"]) + ".kcart"
        seed_ver = int(cart.get("version", 0))
        preserved = None
        if _exists(d):
            if seed_ver <= _cart_version(d):
                continue
            preserved = _preserve_kid_data(d)   # keep saves + tuning across the wipe
            _rmtree(d)            # newer built-in: replace code+art wholesale
        _mkdir(d)
        manifest = {
            "format": CART_FORMAT, "title": cart["title"], "type": cart["type"],
            "runtime": "python", "main": "main.py", "edit": cart.get("edit", []),
            "version": seed_ver,
        }
        if cart.get("canvas") is not None:
            manifest["canvas"] = cart["canvas"]
        if cart.get("permissions") is not None:
            manifest["permissions"] = cart["permissions"]
        _write(d + "/manifest.json", json.dumps(manifest))
        _write(d + "/main.py", cart["src"])
        _write(d + "/config.json", json.dumps(cart["cfg"]))
        sprites = cart.get("sprites")
        if sprites:
            _write(d + "/sprites.kgfx", sprites)
        sounds = cart.get("sounds")               # AudioBank dict, optional (#16)
        if sounds:
            _write(d + "/sounds.json", json.dumps(sounds))
        tilemap = cart.get("map")                 # TileMap.to_hex() blob, optional (#32)
        if tilemap:
            _write(d + "/map.kmap", tilemap)
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
    """Load one .kcart folder into a cart dict, or None on error.

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
            print("KidCode cart manifest bad:", path, exc)
            return None
        if not isinstance(man, dict):
            print("KidCode cart manifest not an object:", path)
            return None
        try:
            src = _read_recover(path + "/" + man.get("main", "main.py"))
        except OSError as exc:
            print("KidCode cart main missing:", path, exc)
            return None
        cfg = dict(man.get("config", {}))
        try:
            cfg.update(json.loads(_read(path + "/config.json")))
        except (OSError, ValueError):
            pass
        try:
            sprites = _read(path + "/sprites.kgfx")   # PICO-8 __gfx__-style hex, optional
        except OSError:
            sprites = None
        try:
            sounds = json.loads(_read(path + "/sounds.json"))  # AudioBank, optional (#16)
        except (OSError, ValueError):
            sounds = None
        try:
            tilemap = _read(path + "/map.kmap")   # TileMap blob (#32), optional
        except OSError:
            tilemap = None
        try:
            blocks = json.loads(_read(path + "/blocks.json"))  # block source (#29), optional
        except (OSError, ValueError):
            blocks = None
        return {
            "path": path,
            "title": man.get("title", "cart"),
            "type": man.get("type", "app"),
            "version": int(man.get("version", 0)),   # 0 = pre-versioning (re-seedable)
            "src": src,
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
        }
    except Exception as exc:  # noqa: BLE001  -- never let one bad cart escape
        print("KidCode cart unreadable:", path, exc)
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
        if name.endswith(".kcart"):
            try:
                c = load(root + "/" + name)
            except Exception as exc:  # noqa: BLE001  -- belt-and-braces over load()
                print("KidCode cart scan skipped:", name, exc)
                c = None
            if c:
                carts.append(c)
    return carts


def save_config(cart):
    """Persist a cart's edited config back to its config.json (needs cart['path'])."""
    _write_atomic(cart["path"] + "/config.json", json.dumps(cart["cfg"]))


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
    when `src` won't parse, so a kid's broken edit can never truncate the cart."""
    ok, msg = compile_check(src)
    if not ok:
        return SAVE_BAD_SYNTAX, msg
    _write_atomic(cart["path"] + "/main.py", src)
    cart["src"] = src
    return SAVE_OK, ""


def save_sprites(cart, hex_text):
    """Persist the sprite sheet (PICO-8 __gfx__-style hex) to sprites.kgfx,
    atomically so an interrupted write can't truncate the real file."""
    _write_atomic(cart["path"] + "/sprites.kgfx", hex_text)
    cart["sprites"] = hex_text


def save_sounds(cart, bank_dict):
    """Persist a cart's sound bank (AudioBank.to_dict()) to sounds.json (#16),
    atomically so an interrupted write can't truncate the real file. `bank_dict` is
    plain JSON-able data ({"sfx": [...], "music": [...]})."""
    _write_atomic(cart["path"] + "/sounds.json", json.dumps(bank_dict))
    cart["sounds"] = bank_dict


def save_map(cart, hex_text):
    """Persist a cart's tilemap (TileMap.to_hex() blob) to map.kmap (#32),
    atomically so an interrupted write can't truncate the real file."""
    _write_atomic(cart["path"] + "/map.kmap", hex_text)
    cart["map"] = hex_text


def _import_blocks():
    """Import the blocks compiler under whichever name it's known by: bare
    `blocks` on the device (frozen top-level) and on the host once host_app has
    aliased it, or `runtime.blocks` when a test imports kid_carts directly. The
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
    none / it's unreadable. Accepts a cart dict or a .kcart folder path."""
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
    _write_atomic(cart["path"] + "/blocks.json", json.dumps(program))
    cart["blocks"] = program
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
    """Read a cart's pmem (path = the .kcart folder). Returns a list of 256 ints,
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

# A friendly starter cartridge: an editable colored dot on a wallpaper.
NEW_TEMPLATE = {
    "type": "wallpaper",
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
    d = root + "/" + base + ".kcart"
    if not _exists(d):
        return d
    i = 2
    while _exists(root + "/" + base + "_" + str(i) + ".kcart"):
        i += 1
    return root + "/" + base + "_" + str(i) + ".kcart"


def create(title, root=CARTS_DIR, src=None, cfg=None, edit=None, type="app"):
    """Create a new .kcart folder and return its loaded cart dict."""
    d = _unique_dir(root, slug(title))
    _mkdir(d)
    manifest = {
        "format": CART_FORMAT, "title": title, "type": type,
        "runtime": "python", "main": "main.py", "edit": edit or [],
    }
    _write(d + "/manifest.json", json.dumps(manifest))
    _write(d + "/main.py", src if src is not None else NEW_TEMPLATE["src"])
    _write(d + "/config.json", json.dumps(cfg or {}))
    return load(d)


def new_from_template(root=CARTS_DIR, title="New Cart"):
    return create(title, root, src=NEW_TEMPLATE["src"], cfg=dict(NEW_TEMPLATE["cfg"]),
                  edit=NEW_TEMPLATE["edit"], type=NEW_TEMPLATE["type"])


def duplicate(cart, root=CARTS_DIR, new_title=None):
    return create(new_title or (cart["title"] + " copy"), root,
                  src=cart["src"], cfg=dict(cart["cfg"]), edit=cart["edit"], type=cart["type"])


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
