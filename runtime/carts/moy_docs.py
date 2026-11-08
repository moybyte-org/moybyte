# Map (grep -n a name to jump there):
#   _load_store_json        a system JSON store's value, healed from its backup
#   -- known WiFi networks  load_wifi, save_wifi, remember_wifi, forget_wifi
#   -- system settings      parse_settings, settings_text, load_system, save_system(_steps)
"""The console's own documents beside the carts folder: the known WiFi
networks, a JSON file read back through `_load_store_json`, and the settings, the plain-text `system/settings`; each
written atomically and read back through a reader that heals a truncated
store from the backup `_write_atomic` leaves.

moy_carts re-exports every name here (`moy_carts.load_system` and the
rest), so its callers are unchanged. A leaf over moy_store_base and moy_fs;
MicroPython-safe (json + os only).
"""

import json

try:
    from moy_store_base import CARTS_DIR, _sibling_path, ensure_dirs
    from moy_fs import (_mkdir, _read, _read_bak, _read_recover, _write, _write_atomic,
                        _write_atomic_steps)
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime.moy_store_base import CARTS_DIR, _sibling_path, ensure_dirs
    from runtime.moy_fs import (_mkdir, _read, _read_bak, _read_recover, _write,
                                _write_atomic, _write_atomic_steps)


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


# --- system settings: system/settings (#28; docs/os_2026-11/system.md §5) -----
#
# The console's settings (the theme, the font scale, the toggles, the
# consent record, favorites and recents: every row `ws.system` holds) live in
# ONE plain-text file under the console's `system/` folder BESIDE the carts dir,
# so `cat`, the shell's `settings` and an editor all reach it:
#
#   brightness = 7
#   theme = "blocks"
#   favorites = ["/sd/moybyte/carts/moybyte.paint.moy"]
#
# One `key = value` a line, sorted by key; the value is the row's JSON on one
# line. The format's one reader and one writer are C, the settings rows'
# (native/moy_spine/moy_settings.h, `Settings.load_file` and `Settings.file`):
# the functions here call them, on every tier, and the C shell reads and
# writes the file through the same two while no VM runs. A line that is not
# `key = <JSON>` (a blank line and a `#` comment aside) is passed over and
# logged, the rest still read. The legacy `system.json` beside the carts dir
# reads as ABSENT (CLAUDE.md, no store migrations): a console with only that
# file starts on the defaults and writes this one. Written atomically and read
# through `_read_recover`, so an interrupted publish heals from its backup like
# every store write.

SYSTEM_DIR = "system"
SETTINGS_NAME = SYSTEM_DIR + "/settings"


def system_store_path(root=CARTS_DIR):
    """Well-known path of the settings file."""
    return _sibling_path(root, SETTINGS_NAME)


def _rows():
    try:
        import moy_spine
    except ImportError:  # pragma: no cover - the host: the runtime package's
        from runtime import moy_spine
    return moy_spine.Settings()


def parse_settings(text):
    """The rows a settings file's `text` holds, {key: value}."""
    rows = _rows()
    passed = rows.load_file(text)
    if passed:
        try:
            print("Moybyte settings: %d line(s) passed over" % passed)
        except Exception:  # noqa: BLE001 -- a log line never stops a read
            pass
    return {key: rows.get(key) for key in rows.keys()}


def settings_text(settings):
    """A settings file's text for `settings`: a dict of plain JSON-able data,
    or the rows' JSON object text (what the settings store's rows dump)."""
    rows = _rows()
    if isinstance(settings, str):
        rows.load(settings if settings.strip() else "{}")
    else:
        for key in settings:
            if key:
                rows.set(key, settings[key], False)
    return rows.file()


def load_system(root=CARTS_DIR):
    """The settings rows, {key: value}; {} when the file was never written (a
    corrupt store must never crash boot)."""
    try:
        text = _read_recover(system_store_path(root))
    except OSError:
        return {}
    return parse_settings(text)


def save_system(settings, root=CARTS_DIR):
    """Persist the settings, atomically. Ensures `system/` exists."""
    ensure_dirs(root)
    _mkdir(_sibling_path(root, SYSTEM_DIR))
    _write_atomic(system_store_path(root), settings_text(settings))


def save_system_steps(settings, root=CARTS_DIR):
    """`save_system` as two store ops, a resume each (moy_fs.
    _write_atomic_steps)."""
    ensure_dirs(root)
    _mkdir(_sibling_path(root, SYSTEM_DIR))
    yield from _write_atomic_steps(system_store_path(root), settings_text(settings))
