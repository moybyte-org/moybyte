# Map (grep -n a name to jump there):
#   _load_store_json        a system JSON store's value, healed from its backup
#   -- known WiFi networks  load_wifi, save_wifi, remember_wifi, forget_wifi
#   -- system settings      load_system, save_system
#   -- achievements         achievements_doc, load_achievements, save_achievements
"""The console's own documents beside the carts folder: the known WiFi
networks, the system settings and the achievements, each one JSON file
written atomically and read back through `_load_store_json`, which heals a
truncated store from the backup `_write_atomic` leaves.

moy_carts re-exports every name here (`moy_carts.load_system` and the
rest), so its callers are unchanged. A leaf over moy_store_base and moy_fs;
MicroPython-safe (json + os only).
"""

import json

try:
    from moy_store_base import CARTS_DIR, _sibling_path, ensure_dirs
    from moy_fs import _read, _read_bak, _write, _write_atomic
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime.moy_store_base import CARTS_DIR, _sibling_path, ensure_dirs
    from runtime.moy_fs import _read, _read_bak, _write, _write_atomic


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
# A single system-level config (the theme, the font scale, the toggles, later
# volume/brightness/name) that is NOT tied to any one cart -- today config is per-cart only
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
    """Persist the system settings, atomically. Ensures the parent dir exists.
    `settings` is a dict of plain JSON-able data, or the object's JSON text
    (what the settings store's rows dump)."""
    ensure_dirs(root)
    _write_atomic(system_store_path(root), settings if isinstance(settings, str)
                  else json.dumps(dict(settings)))


# --- achievements (#21) -----------------------------------------------------
#
# The badges a kid found (milestones hit naturally, plus the hidden Easter-egg
# rewards) persist to a single achievements.json that lives BESIDE the carts
# dir (a sibling of `root`, like system.json/wifi.json/the shared sheet) -- it
# is system state, not tied to any cart. Stored as
#   {"v": 2, "found": {id: YYYYMMDD, ...}, "count": {badge: [key, ...]}}
# -- the day each was found (0 where the console had no clock) and a counter
# badge's progress (Cart Explorer's carts, Toolbox Master's editors); the
# catalog of what each id MEANS lives in the shared console (host == device).
# The reader is strict: any other document, the ids-only one before it
# included, reads as ABSENT (CLAUDE.md, no store migrations). Written
# atomically (same crash-safe path as the cart saves) so an interrupted write
# can never lose a kid's earned badges. MicroPython-safe (json + os only).

ACHIEVEMENTS_STORE_NAME = "achievements.json"
ACHIEVEMENTS_FORMAT = 2


def achievements_store_path(root=CARTS_DIR):
    """Well-known path of the achievements store."""
    return _sibling_path(root, ACHIEVEMENTS_STORE_NAME)


def achievements_doc(data):
    """achievements.json's document out of its parsed JSON, `{"found": {id:
    day}, "count": {badge: [key, ...]}}`, or None when it is not one of
    this format (so _load_store_json falls through to the backup)."""
    if not isinstance(data, dict) or data.get("v") != ACHIEVEMENTS_FORMAT:
        return None
    found, count = data.get("found"), data.get("count", {})
    if not isinstance(found, dict) or not isinstance(count, dict):
        return None
    out = {}
    for k, v in found.items():
        if not isinstance(k, str) or type(v) is not int or v < 0:
            return None
        out[k] = v
    keys = {}
    for k, v in count.items():
        if not isinstance(k, str) or not isinstance(v, list):
            return None
        got = []
        for i in v:
            if not isinstance(i, str):
                return None
            if i not in got:
                got.append(i)
        keys[k] = got
    return {"found": out, "count": keys}


def achievements_text(doc):
    """achievements.json's text for `doc` (an `Achievements.doc()`)."""
    return json.dumps({"v": ACHIEVEMENTS_FORMAT, "found": doc.get("found", {}),
                       "count": doc.get("count", {})})


def load_achievements(root=CARTS_DIR):
    """The badges' document (`achievements_doc`), or None when nothing of this
    format has been saved (a corrupt store must never crash boot); recovers
    from the .bak like load_wifi."""
    return _load_store_json(achievements_store_path(root), achievements_doc)


def save_achievements(doc, root=CARTS_DIR):
    """Persist the badges' document (`Achievements.doc()`), atomically.
    Ensures the parent dir exists."""
    ensure_dirs(root)
    _write_atomic(achievements_store_path(root), achievements_text(doc))
