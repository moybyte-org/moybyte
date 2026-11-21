# Map (grep -n a name to jump there):
#   row                               a permission's row of the table
#   line                              a permission's line on the install prompt
#   asked                             the permissions a prompt asks for, on a tier
#   wire_tiers                        which tiers this console runs
#   tier_of                           the one router: the tier a cart runs on here
#   native_choice                     whether Settings offers 'Run as native code'
#   run_tier                          tier_of with the cart's consent record
#   perms_of                          the permissions a cart needs where it runs
#   tiers_of                          the tiers a cart's permissions are read on
#   check                             (granted, absent, refusal) for running or installing a cart
#   lacking                           the first required permission the console has nothing for
#   manifest_cart                     a manifest read as the table reads a cart
#   app_id                            the id a consent record is kept under
#   prompt                            the install prompt's lines: the publisher, TRUSTED or UNREVIEWED
#   warned                            whether a prompt carries the unreviewed warning
#   regrant                           a live run's grants redone after its app's permissions changed
#   Consent                           the consent records over the system store, and this console's checks
#   Consent.opt                       turn one permission on or off, as Settings does
#   Consent.revoke                    mark an app its store took back, so it will not launch
"""The permission table (`native/os_calls.json`'s "permissions") and the
consent the install prompt records against it (docs/os_2026-11/processes.md
§2). One table: the install prompt's lines, the install check and the launch
check all read `TABLE`, which tools/gen_os_table.py writes from the source
(`tests/test_os_table.py` holds it to it), and whose `role:` rows are
native/moy_app's allowlist (`moy_app.perms()`).

A row is `(perm, grants, tiers, prompt, needs, py)`:

  perm    the manifest name; `files` also matches `files:<folder>`, `lease`
          only `lease:<row>`, a row of the board's resource table
  grants  `floor` (ungated), `role:<role>` (the app ABI's grant policy),
          `api:<service>` (a device API the Player hands the run), `boot`
          (started with the console), `lease` (a raw lease, taken through
          `moybyte.lease`, runtime/system/leases.py) or `marker` (names a
          shipped app's cart, grants nothing)
  tiers   the cart runtimes that may hold it: derived from the tiers that
          serve the calls it hands an app (the source's rows), so a prompt
          never lists what the tier cannot do
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
the cart's tier may not hold it. A `files:<folder>` naming a folder this
console has none of refuses the launch by name, required or not: the cart
would otherwise run with no `files` and fail on its first use. Turned off or unconsented, each of its `py`
names reads `None`; on a console without the service, it has no name, as
the console has no verb. An absent permission the manifest's `"required"`
lists refuses the launch with a message naming it.

A cart in a folder with no consent record -- one that arrived as files:
copied onto the card, over SFTP or USB, pushed by a paired browser -- is
asked on its first run, with the install prompt's lines (`asks_first`): the
home's launch asks (`Notices.ask_first`), a command asks in its terminal
(`sh_door`), and until a yes every launch of it is refused (`UNASKED`). A
yes records the consent an install would. Never asked: a built-in (known by
its seed, `builtin_name`), a cart with no folder (a script), a cart whose
prompt would ask nothing, and a cart made on this console, whose record the
making writes (`Consent.made`) and which runs on its manifest as written,
less what Settings turned off.

A PYTHON cart shares the shell's MicroPython, so its manifest binds
nothing and its trust comes from review (#261): from a reviewed index
(cart_index.is_reviewed) its prompt says it is a trusted app (TRUSTED);
from any other it carries the UNREVIEWED warning, asked again only when an
update comes from another source or adds a permission. The prompt names the
cart's publisher first (FROM) where it came from an index.

The consent record lives in system/settings under `KEY`, one entry per app id
(`moy_app.id_for`, the grant's key): `{"perms": [...], "off": [...]}`, the
manifest permissions consented at install or first run (None for a cart
made here, and for a Settings switch on a cart with no record) and the ones
turned off since; `"unreviewed"`, the index whose unreviewed warning the
user accepted for it; and `"revoked": true` once that cart's index withdrew
the installed version (cart_index's "AN INDEX MAY REVOKE"), which refuses
every launch (REVOKED_REFUSAL) until an install or a removal writes the
record again. A malformed entry reads as no entry.

A compiled cart carrying a native module beside its wasm one (`is_dual`)
asks for `full_access` too where its native module runs (`perms_of`,
`tier_of`); where that is the user's choice (`native_choice`), the record's
`full_access`, consented and on, is the choice (`native_opted`)."""

import moy_app as _moy_app
from moy_store_base import builtin_name, id_in_folder, vendor, api_of, api_above, API_LEVEL

ALL = ("python", "lua", "wasm", "elf")

# `full_access`'s line by board (`[board] ota`), where the board's isolation
# lets the prompt say less than the table's "Full access to the console"
# (docs/os_2026-11/tiers.md section 1): the P4s run a native module in
# U-mode, confined to its own memory, but an unsigned compiled (AOT) module
# runs with the kernel, unconfined, and the line says so. Every other board
# says the table's line.
_P4_FULL = "Run its own machine code. Unsigned code has full access to the console"
FULL_ACCESS_LINES = {"p4": _P4_FULL, "guition_p4": _P4_FULL}


def _board():
    try:
        import moy_ota
    except ImportError:
        return None
    return getattr(moy_ota, "BOARD", None)

# GENERATED by tools/gen_os_table.py from native/os_calls.json. Do not edit.
TABLE = (
    ("graphics", "floor", ("python", "lua", "wasm", "elf"), None, None, ()),
    ("input", "floor", ("python", "lua", "wasm", "elf"), None, None, ()),
    ("audio", "floor", ("python", "lua", "wasm", "elf"), None, None, ()),
    ("files", "role:files", ("python", "lua", "wasm", "elf"),
     "Read and save your %s", None, ("files", "open_editor")),
    ("prefs", "role:prefs", ("python", "lua", "wasm", "elf"),
     "Keep its own settings", None, ("prefs",)),
    ("appearance", "role:theme", ("python", "lua", "wasm"),
     "Change the console's theme", None, ("set_theme", "themes")),
    ("launch", "role:nav", ("python", "lua", "wasm", "elf"),
     "Open other apps", None, ("open_app",)),
    ("clipboard", "role:clipboard", ("python", "lua", "wasm", "elf"),
     "Use the clipboard", None, ("clipboard",)),
    ("network", "api:wifi", ("python", "elf"), "Use Wi-Fi and the internet", "wifi", ("wifi",)),
    ("multiplayer", "api:net", ("python", "lua", "wasm", "elf"),
     "Talk to nearby consoles", "net", ("net", "on_net")),
    ("pins", "api:gpio", ("python", "lua", "wasm", "elf"),
     "Switch the console's pins", "gpio", ("pin_write", "pin_read")),
    ("console", "api:console", ("python", "lua"), None, None, ()),
    ("notifications", "api:notify", ("python", "lua", "wasm", "elf"),
     "Show notifications", None, ()),
    ("front", "api:front", ("python", "lua", "wasm", "elf"),
     "Take over the screen from the background", None, ()),
    ("autostart", "boot", ("python", "lua", "wasm", "elf"),
     "Start when the console starts", None, ()),
    ("lease", "lease", ("python", "lua", "wasm", "elf"), "full", "lease", ()),
    ("full_access", "full_access", ("wasm", "elf"), "Full access to the console", None, ()),
    ("http", "api:http", ("python", "lua", "wasm", "elf"),
     "Fetch pages from the internet and your network", "wifi", ()),
    ("mic", "api:mic", ("python", "lua", "wasm", "elf"), "Record with the microphone", "mic", ()),
    ("lora", "api:lora", ("python", "lua", "wasm", "elf"),
     "Send and receive on the LoRa radio", "lora", ()),
    ("ble", "api:ble", ("python", "lua", "wasm", "elf"), "Use Bluetooth devices", "ble", ()),
    ("usb", "api:usb", ("python", "lua", "wasm", "elf"),
     "Use USB devices plugged into the console", "usb", ()),
    ("midi", "api:midi", ("python", "lua", "wasm", "elf"),
     "Play and hear MIDI instruments", "midi", ()),
    ("camera", "api:camera", ("python", "lua", "wasm", "elf"),
     "Take pictures with the camera", "camera", ()),
    ("media", "api:media", ("python", "lua", "wasm", "elf"),
     "Play music in the background", "media", ()),
    ("media:url", "api:media", ("python", "lua", "wasm", "elf"),
     "Play radio streams from the internet", "wifi", ()),
    ("alarms", "api:alarms", ("python", "lua", "wasm", "elf"),
     "Wake the console at set times", None, ()),
    ("editor", "marker", ("python",), None, None, ()),
    ("calc", "marker", ("python",), None, None, ()),
    ("terminal", "marker", ("python",), None, None, ()),
    ("getcarts", "marker", ("python",), None, None, ()),
    ("storybook", "marker", ("python",), None, None, ()),
    ("myart", "marker", ("python",), None, None, ()),
    ("artwork", "marker", ("python",), None, None, ()),
    ("music", "marker", ("python",), None, None, ()),
)
# END GENERATED

PERM, GRANTS, TIERS, PROMPT, NEEDS, NAMES = range(6)

# The install prompt's line for a Python app: its manifest is legible, not
# binding (processes.md §2), so the prompt says what it is.
TRUSTED = "A trusted app: Python can reach the whole console"

# The install prompt's lines for a Python cart from an index nobody
# reviewed (cart_index.is_reviewed): words a parent reads before saying yes.
UNREVIEWED = ("Moybyte has not checked this cart.",
              "It can do anything on this console: read what's kept on it, "
              "change it, go online.")

# The install prompt's first line: who publishes the cart (cart_index.publisher).
FROM = "From %s"

# The install prompt's line for a service (processes.md §5, owner
# 2026-11-08): it holds its app's grant and uses it with no window open.
BACKGROUND = "Runs in the background, and can do all of this with no window open"

KEY = "consent"

# The record a launch reads for a cart that must be asked first and has not
# been (`Consent.for_run`): every permission absent and the run refused, the
# refusal saying where it is asked.
UNASKED = {"perms": [], "off": []}
UNASKED_REFUSAL = "needs your OK - open it to say yes"

# A launch of a cart whose installed version its store withdrew.
REVOKED_REFUSAL = "was taken back by its store - remove it, or update it in Get Carts"

# A run or an install of a cart that needs a console API level above this one's.
NEWER_REFUSAL = "needs API level %d - this console has %d: update it in Settings"

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
    if perm == "full_access":
        return FULL_ACCESS_LINES.get(_board(), r[PROMPT])
    if "%s" in r[PROMPT]:
        if _moy_app.policy((perm,))[1] is None:
            return None             # a folder no grant is made for: nothing to ask
        kind = perm[6:] if perm.startswith("files:") else "documents"
        return r[PROMPT] % kind
    return r[PROMPT]


def asked(perms, tier=None):
    """The permissions of `perms` the prompt names, in the manifest's order,
    each once; for a cart of `tier` (a tier, or the tiers `tiers_of` names),
    only those one of them serves (TIERS): the rest are absent whatever is
    consented, so nothing asks for them."""
    tiers = (tier,) if isinstance(tier, str) else tier
    out = []
    for p in perms or ():
        if line(p) is not None and p not in out:
            if tiers is None or any(t in row(p)[TIERS] for t in tiers):
                out.append(p)
    return out


# The compiled tiers this console runs, as its runtimes are wired
# (`wire_tiers`, console.wire_workstation_core): the chip its native modules
# are built for (None: no native tier) and whether it runs wasm.
_HERE = {"chip": None, "wasm": False}

# The chips whose consoles confine a native module (U-mode, PMP; docs/os_2026-11/
# tiers.md section 1): a compiled cart's native module is what they run.
CONFINING = ("esp32p4",)

# Settings' switch for a compiled cart's native module on a console that does
# not confine it (perms_ui): the user's choice, remembered as consent to
# `full_access` (`Consent.opt`).
NATIVE_LINE = "Run as native code: faster, with full access to the console"


def wire_tiers(runtimes):
    """This console's compiled tiers from its runtimes (`ws.runtimes`): the
    native one's chip, and whether a wasm one is there."""
    elf = (runtimes or {}).get("elf")
    _HERE["chip"] = getattr(getattr(elf, "engine", None), "CHIP", None)
    _HERE["wasm"] = (runtimes or {}).get("wasm") is not None


def is_dual(cart):
    """Whether `cart` is a compiled cart that carries a native module beside
    its wasm one (`"runtime": "wasm"` and `"moybyte": {"native": [chips]}`,
    sdk/include/moybyte/cart.h)."""
    return bool(cart) and bool(cart.get("native")) and cart.get("runtime") == "wasm"


def native_opted(rec):
    """Whether consent record `rec` holds `full_access` on: the user's choice
    of a compiled cart's native module (`NATIVE_LINE`)."""
    return (isinstance(rec, dict) and isinstance(rec.get("perms"), list)
            and "full_access" in rec["perms"] and "full_access" not in rec["off"])


def _wants_native(rec, chip):
    """A compiled cart's native module is wanted on a console that confines
    it, unless Settings turned its `full_access` off, and elsewhere only by
    the user's choice (`native_opted`)."""
    if chip in CONFINING:
        return not (isinstance(rec, dict) and "full_access" in rec["off"])
    return native_opted(rec)


def tier_of(cart, rec=None):
    """The tier `cart` runs on here, under its consent record `rec`: `elf`
    for a native cart (its manifest's `"moybyte": {"native": [chips]}`),
    else its runtime, Python by default. A compiled cart that carries both
    modules (`is_dual`) runs its native one where it is wanted
    (`_wants_native`) and this console has it, else its wasm one, else its
    native one; with neither here, the one the console would want, whose
    absence the Player says. The Player, the wallpaper and the consent all
    route by this."""
    if not cart:
        return "python"
    if not cart.get("native"):
        return cart.get("runtime") or "python"
    if cart.get("runtime") != "wasm":
        return "elf"
    chip = _HERE["chip"]
    elf = chip is not None and chip in cart["native"]
    if elf and _wants_native(rec, chip):
        return "elf"
    if _HERE["wasm"] or not elf:
        return "wasm"
    return "elf"


def native_choice(cart):
    """Whether running `cart`'s native module is the user's choice here: a
    compiled cart carrying a module for this console's chip, on a console
    that does not confine it (`NATIVE_LINE`, Settings' switch)."""
    chip = _HERE["chip"]
    return (is_dual(cart) and chip is not None and chip not in CONFINING
            and chip in cart["native"])


def run_tier(ws, cart):
    """`tier_of` for a launch of `cart` on console `ws`, under the record it
    runs under (`Consent.for_run`)."""
    consent = getattr(ws, "consent", None) if ws is not None else None
    rec = consent.for_run(cart) if consent is not None and is_dual(cart) else None
    return tier_of(cart, rec)


def perms_of(cart, rec=None):
    """The permissions `cart` asks for here: its manifest's, and
    `full_access` where it is a compiled cart running its native module
    (`tier_of`), the consent that module runs under."""
    perms = list((cart.get("permissions") if cart else None) or ())
    if is_dual(cart) and "full_access" not in perms and tier_of(cart, rec) == "elf":
        perms.append("full_access")
    return perms


def tiers_of(cart, rec=None):
    """The tiers `cart`'s programs run on: its own (`tier_of`), and its own
    service's where it declares one (scheduler.entry_of), whose permissions
    its one consent grants too."""
    own = tier_of(cart, rec)
    if not cart or cart.get("type") == "service":
        return (own,)
    import scheduler
    got = scheduler.entry_of(cart)
    return (own,) if got is None or got[1] == own else (own, got[1])


def services(ws):
    """The services of the console `ws` the table's `needs` names, present,
    the device APIs its board has, and `lease:<row>` for each row the board
    leases."""
    out = []
    for name in ("wifi", "net", "gpio"):
        if getattr(ws, name, None) is not None:
            out.append(name)
    import devices
    return tuple(out) + devices.apis() + leasable()


def leasable():
    """`lease:<row>` for each row this console leases: none where it has no
    machine objects to hand out (a host)."""
    from leases import LEASES
    if LEASES.machine is None:
        return ()
    return tuple("lease:" + o[0] for o in LEASES.offers())


def _why_absent(perm, tier, have, rec):
    """Why `perm` is absent for this run, or None when it is granted."""
    r = row(perm)
    if r is None:
        return "unknown"
    if perm.startswith("files:") and _moy_app.policy((perm,))[1] is None:
        return "folder"
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
    trust, so it takes the consent that says so: `full_access`, the install
    prompt's "Full access to the console". There is no console-wide switch
    (docs/os_2026-11.md section 1); a signature that is present must verify
    whatever this says."""
    return "full_access" in (granted or ())


def run_admits_unsigned(ws, cart):
    """`admits_unsigned` for `cart` on console `ws`, under the record its
    launch reads (`Consent.for_run`; none: its manifest as written)."""
    consent = getattr(ws, "consent", None)
    granted = consent.check(cart)[0] if consent is not None else check(cart, None, ())[0]
    return admits_unsigned(granted)


def board_words():
    """This board's name as Settings' About says it, or None (the host)."""
    from settings_layer import board_name
    return board_name()


def _refusal(perm, why, tier):
    if why == "off":
        return 'needs "%s" - turned off in Settings' % perm
    if why == "unconsented":
        return 'needs "%s" - install it again to allow it' % perm
    if why == "needs":
        return 'needs "%s" - the %s has none' % (perm, board_words() or "console")
    if why == "folder":
        return 'needs "%s" - this console has no such folder' % perm
    if why == "tier":
        if perm == "network":
            return ('asks for "network", which is a Python or native cart\'s: a %s cart '
                    'reaches the internet through "http"' % tier)
        return 'asks for "%s", which a %s cart cannot hold' % (perm, tier)
    return 'needs "%s" - this console does not know it' % perm


def check(cart, rec, have):
    """`(granted, absent, refusal)` for running or installing `cart` on a
    console holding the services `have`, under consent record `rec` (None:
    none; UNASKED: every permission absent and the run refused).
    `granted` and `absent` split the manifest's permissions; `refusal` is
    the message naming the first `required` permission that is absent, or
    the first that no tier of the cart's serves (`_served`: a Lua or wasm
    cart's `network`, whose door is `http`), else None. A cart whose `api`
    names a console API level above this one's is refused first."""
    perms = perms_of(cart, rec if rec is not UNASKED else None)
    newer = api_above(cart.get("api")) if cart else None
    if newer is not None:
        return [], list(perms), NEWER_REFUSAL % (newer, API_LEVEL)
    if rec is UNASKED:
        return [], list(perms), UNASKED_REFUSAL
    if rec is not None and rec.get("revoked"):
        return [], list(perms), REVOKED_REFUSAL
    req = (cart.get("required") if cart else None) or ()
    tier = tier_of(cart, rec)
    granted, absent = [], []
    refusal = None
    for p in perms:
        why = _why_absent(p, tier, have, rec)
        if why is None:
            granted.append(p)
            continue
        absent.append(p)
        if refusal is None and (p in req or why == "folder" or (
                why == "tier" and not _served(p, cart, rec))):
            refusal = _refusal(p, why, tier)
    if refusal is None:
        for p in req:
            if p not in perms:
                refusal = 'requires "%s" but does not ask for it' % p
                break
    if refusal is None and tier == "elf" and "full_access" not in granted:
        refusal = (_refusal("full_access", _why_absent("full_access", tier, have, rec), tier)
                   if "full_access" in perms else 'a native cart must ask for "full_access"')
    return granted, absent, refusal


def _served(perm, cart, rec=None):
    """Whether one of the tiers `cart`'s programs run on (`tiers_of`: its
    own and its service's) serves `perm`. One none of them serves is the
    cart's mistake, refused at install and at launch with what to ask for
    instead, never a name that is silently absent."""
    r = row(perm)
    return r is not None and any(t in r[TIERS] for t in tiers_of(cart, rec))


def lacking(cart, have):
    """The first permission `cart`'s manifest requires that a console holding
    the services `have` has nothing for -- `check`'s refusal where the
    console lacks what it needs, whatever was consented -- or None. A cart
    lacking one is no run tile: the home's shelves and the desk column leave
    it out (`Consent.runnable`), and Files and Make still list it."""
    req = (cart.get("required") if cart else None) or ()
    if not req:
        return None
    perms = cart.get("permissions") or ()
    tier = tier_of(cart)
    for p in req:
        if p in perms and _why_absent(p, tier, have, None) == "needs":
            return p
    return None


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


def manifest_cart(man, folder, root=None):
    """What the checks read of a cart, from its manifest `man` (a dict) and
    its `folder` name in the store `root`: id (`id_in_folder`'s, the
    shelf's), title, runtime, type, permissions, required, native, entries
    and api, as the store's cart dict carries them."""
    ven = vendor(man)
    cid = man.get("id") if isinstance(man, dict) else None
    cid = id_in_folder(cid, (root.rstrip("/") + "/" if root else "") + folder)
    perms = ven.get("permissions")
    req = ven.get("required")
    chips = ven.get("native")
    return {"id": cid, "title": man.get("title") if isinstance(man, dict) else None,
            "runtime": (man.get("runtime") if isinstance(man, dict) else None) or "python",
            "type": ven.get("type"),
            "permissions": perms if isinstance(perms, list) else [],
            "required": req if isinstance(req, list) else [],
            "native": chips if isinstance(chips, list) else None,
            "entries": ven.get("entries"), "api": api_of(man)}


def app_id(cart):
    """The app id consent is recorded under: the grant's key."""
    return _moy_app.id_for(cart.get("id") if cart else None,
                           cart.get("title") if cart else None)


def prompt(cart, rec=None, origin=None):
    """The install prompt's lines for `cart`, or None when there is nothing
    to ask: no permission it lists has a line that `rec` does not already
    hold, it is not a Python app installed for the first time, and it is no
    unreviewed Python app whose warning `rec` has not accepted from this
    source. A service's lines, and an app's that declares its own service,
    end with BACKGROUND: what it is granted, it uses with no window open.

    `origin` is where an install comes from (`pkg.origin_of`: `publisher`,
    `reviewed`, `index`), None for a cart that arrived as files: with one,
    the first line names the publisher (FROM), and a Python cart from an
    index that is not reviewed carries UNREVIEWED in place of TRUSTED."""
    perms = perms_of(cart, rec)
    tiers = tiers_of(cart, rec)
    want = asked(perms, tiers)
    held = rec["perms"] if rec is not None and rec["perms"] is not None else None
    new = want if held is None else [p for p in want if p not in held]
    first = held is None
    python = "python" in tiers
    unreviewed = python and origin is not None and not origin.get("reviewed")
    again = unreviewed and (rec is None or rec.get("unreviewed") != origin.get("index"))
    if not new and not (python and first) and not again:
        return None
    lines = [FROM % origin["publisher"]] if origin and origin.get("publisher") else []
    lines.extend(line(p) for p in want)
    if want and (cart.get("type") == "service" or _has_service(cart)):
        lines.append(BACKGROUND)
    if unreviewed:
        lines.extend(UNREVIEWED)
    elif python:
        lines.append(TRUSTED)
    return lines


def warned(lines):
    """Whether install prompt `lines` carry the unreviewed warning: accepting
    it takes the console's password where one is set (`pkg.gated`)."""
    return bool(lines) and UNREVIEWED[0] in lines


def asks_first(cart, rec):
    """True when `cart`, under its consent record `rec`, is asked before it
    runs: it has no record, it lives in a folder and is no built-in (by its
    seed, `builtin_name`), and its install prompt would ask something
    (`prompt`)."""
    path = cart.get("path") if cart else None
    if rec is not None or not path or builtin_name(path) is not None:
        return False
    return prompt(cart) is not None


def made_here(ws, cart):
    """`cart` (a cart dict, or None), just made on console `ws`: its consent
    record is a made cart's (`Consent.made`)."""
    consent = getattr(ws, "consent", None)
    if consent is not None and cart:
        consent.made(app_id(cart))


def show(ui, lines):
    """The install prompt's body, drawn into the dialog the public UI API
    `ui` has open: the publisher (FROM) dim, "It can:" over the prompt's
    permission lines, TRUSTED and UNREVIEWED in the danger tone. Get Carts'
    install prompt and the shell's first-run one
    (`shell_cards.DeleteConfirm`) both draw it."""
    head = FROM.split("%")[0]
    said = False
    for ln in lines:
        if ln.startswith(head):
            ui.label(ln, {"tone": "dim"})
            continue
        danger = ln == TRUSTED or ln in UNREVIEWED
        if not danger and not said:
            ui.label("It can:", {"tone": "dim"})
            said = True
        ui.label(ln, {"wrap": True, "lines": 3, "tone": "danger"}
                 if danger else {"wrap": True, "lines": 2})


def _has_service(cart):
    """An app cart declaring its own service (scheduler.entry_of)."""
    import scheduler
    return scheduler.entry_of(cart) is not None


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
    out = {"perms": perms, "off": off}
    if isinstance(v.get("unreviewed"), str):
        out["unreviewed"] = v["unreviewed"]
    if v.get("revoked") is True:
        out["revoked"] = True
    return out


def regrant(ws, aid):
    """App `aid`'s permissions changed (Settings' switch, `perm on|off`):
    every live run grant of it, its foreground runs' and its services'
    whatever their pids, recomputed from what its cart's permissions now
    grant each kind of run (native/moy_app's moy_app_regrant), so a
    permission turned off is absent at a running program's next call. The
    one door a change of what an app may reach takes. The rows recomputed."""
    app = getattr(ws, "app_abi", None)
    consent = getattr(ws, "consent", None)
    carts = getattr(ws, "carts", None)
    if app is None or consent is None or carts is None:
        return 0
    cart = None
    for c in carts.all or ():
        if app_id(c) == aid:
            cart = c
            break
    if cart is None:
        return 0
    roles, kind = _moy_app.policy(consent.check(cart)[0])
    svc = roles
    if cart.get("type") != "service":
        import scheduler
        got = scheduler.entry_of(cart)
        if got is not None and got[1] != tier_of(cart):
            # its service runs on a tier of its own, which may grant less
            own = dict(cart, runtime=got[1], native=True if got[1] == "elf" else None)
            svc = _moy_app.policy(consent.check(own)[0])[0]
    return app.regrant(aid, roles, svc, kind)


class Consent:
    """The consent records over the console's system store (`Settings`),
    read once and written on every change, and the checks of this console:
    `services()` answers the services it holds."""

    def __init__(self, store, services=None, key=KEY, saving=None, changed=None):
        self._store = store
        self._services = services
        self._key = key
        self._saving = saving         # () -> the store's stepped save (SystemStore.save_steps)
        self._changed = changed       # (aid) -> None: an app's permissions changed (`regrant`)
        self._d = None

    def check(self, cart, install=False):
        """`check` for `cart` on this console: at launch under its record
        (`for_run`), at install under none (what Settings turned off is the
        launch's)."""
        have = self._services() if self._services is not None else ()
        return check(cart, None if install else self.for_run(cart), have)

    def runnable(self, carts):
        """`carts` less those requiring what this console lacks (`lacking`):
        the ones a tile may run. The services are asked once, and only when
        a cart requires something."""
        have = None
        out = []
        for c in carts:
            if c.get("required"):
                if have is None:
                    have = self._services() if self._services is not None else ()
                if lacking(c, have) is not None:
                    continue
            out.append(c)
        return out

    def for_run(self, cart):
        """The record a launch of `cart` runs under: its own, else UNASKED
        where it is asked first (`asks_first`), else None (its manifest as
        written)."""
        rec = self.get(app_id(cart))
        return UNASKED if asks_first(cart, rec) else rec

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

    def record(self, aid, perms, unreviewed=None):
        """Consent given at install to every permission of `perms`, and with
        `unreviewed` (an index url) to the unreviewed warning of a Python
        cart from that index: what was turned off before stays off, and a
        revocation goes with the version it was about."""
        for _ in self.record_steps(aid, perms, unreviewed):
            pass

    def record_steps(self, aid, perms, unreviewed=None):
        """`record`, its write stepped (`yield from` it). The user's choice of
        a compiled cart's native module (`native_opted`) outlives a reinstall."""
        old = self.get(aid)
        perms = list(perms)
        if native_opted(old) and "full_access" not in perms:
            perms.append("full_access")
        off = [p for p in (old["off"] if old else ()) if p in perms]
        rec = {"perms": perms, "off": off}
        if unreviewed:
            rec["unreviewed"] = unreviewed
        self._all()[aid] = rec
        yield from self._put_steps()

    def revoke(self, aid, on):
        """Mark app `aid` revoked (`on`) or not: a revoked app's every launch
        is refused (REVOKED_REFUSAL). Written only when it changes. True when
        it did."""
        rec = self.get(aid)
        if bool(rec is not None and rec.get("revoked")) == bool(on):
            return False
        rec = dict(rec or {"perms": None, "off": []})
        if on:
            rec["revoked"] = True
        else:
            rec.pop("revoked", None)
        self._all()[aid] = rec
        self._put()
        return True

    def made(self, *aids):
        """Carts made on this console, as apps `aids`: each a record
        consenting to nothing in particular (`perms` None), so it runs on its
        manifest as written and is never asked. A record one has already
        stays; the records are written once."""
        new = [aid for aid in aids if self.get(aid) is None]
        for aid in new:
            self._all()[aid] = {"perms": None, "off": []}
        if new:
            self._put()

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
        self._all()[aid] = dict(rec, off=off)
        self._put()
        if self._changed is not None:
            self._changed(aid)

    def opt(self, aid, perm, on, asked=()):
        """Settings' switch for a permission app `aid` holds by the user's
        choice rather than its manifest (`NATIVE_LINE`'s `full_access`): on
        consents to it, off turns it off. A record that consents to nothing
        in particular (a cart made here) consents to `asked`, its manifest's,
        beside it."""
        rec = self.get(aid) or {"perms": None, "off": []}
        perms = list(rec["perms"] if rec["perms"] is not None else asked)
        off = [p for p in rec["off"] if p != perm]
        if on and perm not in perms:
            perms.append(perm)
        if not on:
            off.append(perm)
        self._all()[aid] = dict(rec, perms=perms, off=off)
        self._put()
        if self._changed is not None:
            self._changed(aid)

    def is_on(self, aid, perm):
        rec = self.get(aid)
        return rec is None or perm not in rec["off"]
