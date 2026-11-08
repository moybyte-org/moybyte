"""The permission table (`native/moy_app/roles.json`'s "permissions") and the
consent the install prompt records against it (docs/os_2026-11/processes.md
§2). One table: the install prompt's lines, the install check and the launch
check all read `TABLE`, which `tests/test_app_perms.py` holds equal to the
JSON row for row, and whose `role:` rows are native/moy_app's allowlist
(`moy_app.perms()`).

A row is `(perm, grants, tiers, prompt, needs, py)`:

  perm    the manifest name; `files` also matches `files:<folder>`, `lease`
          only `lease:<row>`, a row of the board's resource table
  grants  `floor` (ungated), `role:<role>` (the app ABI's grant policy),
          `api:<service>` (a device API the Player hands the run), `boot`
          (started with the console), `lease` (a raw lease, taken through
          `moybyte.lease`, runtime/system/leases.py) or `marker` (names a
          shipped app's cart, grants nothing)
  tiers   the cart runtimes that may hold it
  prompt  its line in the install prompt (`%s` takes a files folder), `full`
          (FULL, naming the lease's row: "full access" to it), or None: no
          line, nothing to consent to, and nothing Settings can turn off
  needs   the console's service it needs (`wifi`, `net`, `gpio`), a device
          API the board has (`mic`, `lora`, `ble`, `usb`, `midi`, `camera`, `media`: runtime/system/devices.py),
          `lease` (the board offers the row: `services` names each as
          `lease:<row>`), or None
  py      the names a Python cart's namespace holds while it is granted

A permission is ABSENT for a run when Settings turned it off, when an update
added it and nobody consented, when the console lacks what it needs or when
the cart's tier may not hold it. Turned off or unconsented, each of its `py`
names reads `None`; on a console without the service, it has no name, as
the console has no verb. An absent permission the manifest's `"required"`
lists refuses the launch with a message naming it. A cart with no consent record -- one written in the
Editor, a seed, a cart brought in by hand -- never prompts and runs on its
manifest as written, less what Settings turned off.

The consent record lives in system/settings under `KEY`, one entry per app id
(`moy_app.id_for`, the grant's key): `{"perms": [...], "off": [...]}`, the
manifest permissions consented at install (None where nothing was
installed: a Settings switch on an Editor cart) and the ones turned off
since. A malformed entry reads as no entry."""

try:
    import moy_app as _moy_app
except ImportError:  # pragma: no cover - host fallback
    from runtime import moy_app as _moy_app
try:
    from moy_store_base import is_cart_id, vendor
except ImportError:  # pragma: no cover - host fallback
    from runtime.moy_store_base import is_cart_id, vendor

ALL = ("python", "lua", "wasm", "elf")

# A native module's one line (the overview's section 4): it runs at the
# kernel's trust, so its prompt says so, and every native app asks for it; a
# wasm cart asks for it to run an AOT module that carries no signature
# (`admits_unsigned`).
NATIVE = "Full access to the console"
PY = ("python",)

TABLE = (
    ("graphics", "floor", ALL, None, None, ()),
    ("input", "floor", ALL, None, None, ()),
    ("audio", "floor", ALL, None, None, ()),
    ("files", "role:files", ALL, "Read and save your %s", None, ("files", "open_editor")),
    ("prefs", "role:prefs", ALL, "Keep its own settings", None, ("prefs",)),
    ("appearance", "role:theme", ALL, "Change the console's theme", None,
     ("set_theme", "themes")),
    ("launch", "role:nav", ALL, "Open other apps", None, ("open_app",)),
    ("clipboard", "role:clipboard", ALL, "Use the clipboard", None, ()),
    ("network", "api:wifi", ALL, "Use Wi-Fi and the internet", "wifi", ("wifi",)),
    ("multiplayer", "api:net", ALL, "Talk to nearby consoles", "net", ("net", "on_net")),
    ("pins", "api:gpio", ALL, "Switch the console's pins", "gpio", ("pin_write", "pin_read")),
    ("console", "api:console", ALL, None, None, ()),
    ("notifications", "api:notify", ALL, "Show notifications", None, ()),
    ("front", "api:front", ALL, "Take over the screen from the background", None, ()),
    ("autostart", "boot", ALL, "Start when the console starts", None, ()),
    ("lease", "lease", PY, "full", "lease", ()),
    ("native", "native", ("elf", "wasm"), NATIVE, None, ()),
    ("http", "api:http", PY, "Fetch pages from the internet", "wifi", ()),
    ("mic", "api:mic", PY, "Record with the microphone", "mic", ()),
    ("lora", "api:lora", PY, "Send and receive on the LoRa radio", "lora", ()),
    ("ble", "api:ble", PY, "Use Bluetooth devices", "ble", ()),
    ("usb", "api:usb", PY, "Use USB devices plugged into the console", "usb", ()),
    ("midi", "api:midi", PY, "Play and hear MIDI instruments", "midi", ()),
    ("camera", "api:camera", PY, "Take pictures with the camera", "camera", ()),
    ("media", "api:media", PY, "Play music in the background", "media", ()),
    ("media:url", "api:media", PY, "Play radio streams from the internet", "wifi", ()),
    ("alarms", "api:alarms", PY, "Wake the console at set times", None, ()),
    ("editor", "marker", PY, None, None, ()),
    ("calc", "marker", PY, None, None, ()),
    ("terminal", "marker", PY, None, None, ()),
    ("getcarts", "marker", PY, None, None, ()),
    ("storybook", "marker", PY, None, None, ()),
    ("myart", "marker", PY, None, None, ()),
    ("artwork", "marker", PY, None, None, ()),
    ("music", "marker", PY, None, None, ()),
)

PERM, GRANTS, TIERS, PROMPT, NEEDS, NAMES = range(6)

# The install prompt's line for a Python app: its manifest is legible, not
# binding (processes.md §2), so the prompt says what it is.
TRUSTED = "A trusted app: Python can reach the whole console"

# The install prompt's line for a service (processes.md §5, owner
# 2026-11-08): it holds its app's grant and uses it with no window open.
BACKGROUND = "Runs in the background, and can do all of this with no window open"

KEY = "consent"

# A raw lease's line: full access to one of the board's resources (the
# overview's section 4).
FULL = "Full access to the console's %s"

_BY = {r[PERM]: r for r in TABLE}


def row(perm):
    """The table's row for `perm` (`files:<folder>` is the files row), or None
    for a name the table does not hold."""
    if not isinstance(perm, str):
        return None
    r = _BY.get(perm)
    if r is None and perm.startswith("files:"):
        r = _BY["files"]
    elif perm == "lease" or (r is None and perm.startswith("lease:") and len(perm) > 6):
        r = None if perm == "lease" else _BY["lease"]
    return r


def line(perm):
    """`perm`'s line in the install prompt, or None where it has none."""
    r = row(perm)
    if r is None or r[PROMPT] is None:
        return None
    if r[PROMPT] == "full":
        return FULL % perm[6:]
    if "%s" in r[PROMPT]:
        if _moy_app.policy((perm,))[1] is None:
            return None             # a folder no grant is made for: nothing to ask
        kind = perm[6:] if perm.startswith("files:") else "documents"
        return r[PROMPT] % kind
    return r[PROMPT]


def asked(perms):
    """The permissions of `perms` the prompt names, in the manifest's order,
    each once."""
    out = []
    for p in perms or ():
        if line(p) is not None and p not in out:
            out.append(p)
    return out


def tier_of(cart):
    """The cart's tier: `elf` for a native cart (its manifest's
    `"moybyte": {"native": [chips]}`), else its runtime, Python by default."""
    if cart and cart.get("native"):
        return "elf"
    return (cart.get("runtime") if cart else None) or "python"


def services(ws):
    """The services of the console `ws` the table's `needs` names, present,
    the device APIs its board has, and `lease:<row>` for each row the board
    leases."""
    out = []
    for name in ("wifi", "net", "gpio"):
        if getattr(ws, name, None) is not None:
            out.append(name)
    try:
        import devices
    except ImportError:  # pragma: no cover - host fallback
        from runtime import devices
    return tuple(out) + devices.apis() + leasable()


def leasable():
    """`lease:<row>` for each row this console leases: none where it has no
    machine objects to hand out (a host)."""
    try:
        from leases import LEASES
    except ImportError:  # pragma: no cover - host fallback
        from runtime.leases import LEASES
    if LEASES.machine is None:
        return ()
    return tuple("lease:" + o[0] for o in LEASES.offers())


def _why_absent(perm, tier, have, rec):
    """Why `perm` is absent for this run, or None when it is granted."""
    r = row(perm)
    if r is None:
        return "unknown"
    if tier not in r[TIERS]:
        return "tier"
    if r[NEEDS] == "lease":
        if perm not in have:
            return "needs"
    elif r[NEEDS] is not None and r[NEEDS] not in have:
        return "needs"
    if rec is not None:
        if perm in rec["off"]:
            return "off"
        if rec["perms"] is not None and perm not in rec["perms"] and r[PROMPT] is not None:
            return "unconsented"
    return None


def admits_unsigned(granted):
    """Whether a run granted the permissions `granted` may load a compiled
    module that carries no signature (an AOT or a native module: the
    `consented` of native/moy_net/moy_ota.h's moy_ota_admit_module, the one
    admission both tiers make). An unsigned module runs at the kernel's
    trust, so it takes the consent that says so: `native`, the install
    prompt's "Full access to the console". There is no console-wide switch
    (docs/os_2026-11.md section 1); a signature that is present must verify
    whatever this says."""
    return "native" in (granted or ())


def run_admits_unsigned(ws, cart):
    """`admits_unsigned` for `cart` on console `ws`, under its consent record
    (none: its manifest as written)."""
    consent = getattr(ws, "consent", None)
    granted = consent.check(cart)[0] if consent is not None else check(cart, None, ())[0]
    return admits_unsigned(granted)


def board_words():
    """This board's name as Settings' About says it, or None (the host)."""
    try:
        from settings_layer import board_name
    except ImportError:  # pragma: no cover - host fallback
        from runtime.settings_layer import board_name
    return board_name()


def _refusal(perm, why, tier):
    if why == "off":
        return 'needs "%s" - turned off in Settings' % perm
    if why == "unconsented":
        return 'needs "%s" - install it again to allow it' % perm
    if why == "needs":
        return 'needs "%s" - the %s has none' % (perm, board_words() or "console")
    if why == "tier":
        return 'needs "%s" - not for a %s cart' % (perm, tier)
    return 'needs "%s" - this console does not know it' % perm


def check(cart, rec, have):
    """`(granted, absent, refusal)` for running or installing `cart` on a
    console holding the services `have`, under consent record `rec` (None:
    none). `granted` and `absent` split the manifest's permissions; `refusal`
    is the message naming the first `required` permission that is absent,
    else None."""
    perms = (cart.get("permissions") if cart else None) or ()
    req = (cart.get("required") if cart else None) or ()
    tier = tier_of(cart)
    granted, absent = [], []
    refusal = None
    for p in perms:
        why = _why_absent(p, tier, have, rec)
        if why is None:
            granted.append(p)
            continue
        absent.append(p)
        if refusal is None and p in req:
            refusal = _refusal(p, why, tier)
    if refusal is None:
        for p in req:
            if p not in perms:
                refusal = 'requires "%s" but does not ask for it' % p
                break
    if refusal is None and tier == "elf" and "native" not in granted:
        refusal = (_refusal("native", _why_absent("native", tier, have, rec), tier)
                   if "native" in perms else 'a native cart must ask for "native"')
    return granted, absent, refusal


def absent_names(absent, rec):
    """The names a Python cart's namespace holds as None for the absent
    permissions `absent` under consent record `rec`: the ones turned off in
    Settings or never consented to. A permission absent because the console
    lacks what it needs leaves no name, as the console has no verb."""
    out = []
    if rec is None:
        return out
    for p in absent:
        r = row(p)
        if r is not None and (p in rec["off"] or (
                rec["perms"] is not None and p not in rec["perms"])):
            out.extend(r[NAMES])
    return out


def manifest_cart(man, folder):
    """What the checks read of a cart, from its manifest `man` (a dict) and
    its `folder` name: id, title, runtime, type, permissions, required and native,
    as the store's cart dict carries them."""
    ven = vendor(man)
    cid = man.get("id") if isinstance(man, dict) else None
    if not is_cart_id(cid):
        cid = folder[:-4] if folder.endswith(".moy") else folder
    perms = ven.get("permissions")
    req = ven.get("required")
    chips = ven.get("native")
    return {"id": cid, "title": man.get("title") if isinstance(man, dict) else None,
            "runtime": (man.get("runtime") if isinstance(man, dict) else None) or "python",
            "type": ven.get("type"),
            "permissions": perms if isinstance(perms, list) else [],
            "required": req if isinstance(req, list) else [],
            "native": chips if isinstance(chips, list) else None}


def app_id(cart):
    """The app id consent is recorded under: the grant's key."""
    return _moy_app.id_for(cart.get("id") if cart else None,
                           cart.get("title") if cart else None)


def prompt(cart, rec=None):
    """The install prompt's lines for `cart`, or None when there is nothing
    to ask: no permission it lists has a line that `rec` does not already
    hold, and it is not a Python app installed for the first time. A
    service's lines end with BACKGROUND: what it is granted, it uses with no
    window open."""
    perms = (cart.get("permissions") if cart else None) or ()
    want = asked(perms)
    held = rec["perms"] if rec is not None and rec["perms"] is not None else None
    new = want if held is None else [p for p in want if p not in held]
    first = held is None
    python = tier_of(cart) == "python"
    if not new and not (python and first):
        return None
    lines = [line(p) for p in want]
    if lines and cart.get("type") == "service":
        lines.append(BACKGROUND)
    if python:
        lines.append(TRUSTED)
    return lines


def _entry(v):
    """A stored entry, read strictly: None unless it is the record's shape."""
    if not isinstance(v, dict):
        return None
    perms = v.get("perms")
    off = v.get("off", [])
    if perms is not None and not (isinstance(perms, list)
                                  and all(isinstance(p, str) for p in perms)):
        return None
    if not (isinstance(off, list) and all(isinstance(p, str) for p in off)):
        return None
    return {"perms": perms, "off": off}


class Consent:
    """The consent records over the console's system store (`Settings`),
    read once and written on every change, and the checks of this console:
    `services()` answers the services it holds."""

    def __init__(self, store, services=None, key=KEY, saving=None):
        self._store = store
        self._services = services
        self._key = key
        self._saving = saving         # () -> the store's stepped save (SystemStore.save_steps)
        self._d = None

    def check(self, cart, install=False):
        """`check` for `cart` on this console: at launch under its record,
        at install under none (what Settings turned off is the launch's)."""
        have = self._services() if self._services is not None else ()
        return check(cart, None if install else self.get(app_id(cart)), have)

    def _all(self):
        if self._d is None:
            try:
                d = self._store.get(self._key)
            except Exception:  # noqa: BLE001 -- an unreadable slot is no record
                d = None
            self._d = d if isinstance(d, dict) else {}
        return self._d

    def _put(self):
        self._store.set(self._key, self._d)

    def _put_steps(self):
        """`yield from` it: the records written a store op a frame where the
        store steps its save, else at once."""
        if self._saving is None:
            self._put()
            return
        self._store.set(self._key, self._d, False)
        yield from self._saving()

    def get(self, aid):
        """The record for app id `aid`, or None."""
        return _entry(self._all().get(aid))

    def ids(self):
        return sorted(k for k in self._all() if self.get(k) is not None)

    def record(self, aid, perms):
        """Consent given at install to every permission of `perms`: what was
        turned off before stays off."""
        for _ in self.record_steps(aid, perms):
            pass

    def record_steps(self, aid, perms):
        """`record`, its write stepped (`yield from` it)."""
        old = self.get(aid)
        off = [p for p in (old["off"] if old else ()) if p in perms]
        self._all()[aid] = {"perms": list(perms), "off": off}
        yield from self._put_steps()

    def forget(self, aid):
        for _ in self.forget_steps(aid):
            pass

    def forget_steps(self, aid):
        """`forget`, its write stepped (`yield from` it)."""
        if self._all().pop(aid, None) is not None:
            yield from self._put_steps()

    def turn(self, aid, perm, on):
        """Settings' switch for one permission of app `aid`."""
        rec = self.get(aid) or {"perms": None, "off": []}
        off = [p for p in rec["off"] if p != perm]
        if not on:
            off.append(perm)
        if off == rec["off"] and aid in self._all():
            return
        self._all()[aid] = {"perms": rec["perms"], "off": off}
        self._put()

    def is_on(self, aid, perm):
        rec = self.get(aid)
        return rec is None or perm not in rec["off"]
