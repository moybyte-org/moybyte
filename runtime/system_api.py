"""`make_system_api` -- the globals a USER APP cart gets, keyed on the
permissions its manifest declares (#181, ui_refactor_2026-08 Phase 7).

## What this is, and what it deliberately is NOT

A SHIPPED system app (Calc, Files, Writer ...) is shell code: a Layer class in
`runtime/` that declares a `NEEDS` tuple and is handed an `AppContext`
(`runtime/app_context.py`) carrying exactly those roles. A USER APP is a
`.moy` CART -- editable in the project picker like any other cart, written by
whoever owns the console -- that asks for shell capabilities in its
`manifest.json`:

    "type": "app",
    "permissions": ["graphics", "input", "files:docs", "prefs"]

`make_system_api` is the FILTER between the two. It is not a second interface:
it maps each declared permission to a role on the SAME `AppContext`, builds one
context with only those roles, and returns the cart globals they publish. The
Player merges that dict into the cart namespace, so a capability the cart did
not declare has **no name at all** -- exactly how `wifi` has been gated on the
`"network"` permission since #38, and `net` on `"multiplayer"` since #65.

That "no name" property is the model, and it is worth being explicit about what
it is and is not. An ungranted verb is a `NameError` inside the cart, not a stub
that quietly returns None and not an object that raises a nicer message -- so
there is nothing to check and nothing to forget to check, and a cart that never
mentions a capability provably cannot use it.

## This is NOT a sandbox, and saying so is not a caveat -- it is the design

A cart runs `exec` in a plain namespace with the real builtins: it can
`import`, walk `gc.get_objects()`, or reach an attribute on anything it was
handed. The role objects hold their `Workstation` in a name-mangled slot
(`self.__ws` -> `_Theme__ws`), and `ScopedFiles` holds its unscoped role the
same way, so the obvious reach-through -- `files._files.save("drawings", ...)`,
`prefs._ws.carts_store` -- fails. That is a SPEED BUMP: it turns an accident
into a deliberate act, and it makes the honest API the easy one. It is not
containment, and two things say so plainly. A cart that goes looking will find
a path, and **on the boards it does not even bump**: MicroPython implements no
name mangling (measured on this repo's unix build -- `self.__x` stays the
literal attribute `__x`, readable from outside), so the mangling is a host-side
speed bump on device-side code.

What the permission filter actually buys, then, is not confinement but
LEGIBILITY: a manifest states what an app is for, the shell hands it exactly
that, and an app that quietly wanted more has to say so in a file the owner can
read. The threat model that goes with it is the household one -- a kid's cart,
a cart a friend sent, a cart off a card -- not hostile code auditing itself for
escapes. A cart you would not run is a cart you should not install; nothing
here changes that, and no amount of wrapper would.

## The map

    permission        cart globals                    AppContext role
    ---------------   -----------------------------   ---------------
    "files"           files.*  (kind "docs")          ctx.files
    "files:<kind>"    files.*  (that kind only)       ctx.files
    "prefs"           prefs.get / prefs.set           ctx.prefs (namespaced)
    "appearance"      set_theme() / themes()          ctx.theme
    "launch"          open_app(id)                    ctx.nav

Every `type: "app"` cart also gets `ui`, `theme()`, `screen()` and `bar_h()` --
see UNGATED below. Everything else a manifest lists (`"graphics"`, `"input"`,
`"sound"`, an app's own marker permission like `"calc"`) is not a grant here and
is silently ignored, which is correct: the kid API is already the ungated floor.

## What is NEVER grantable, and why the list is a positive one

`NEVER_GRANTED` names the roles a cart cannot have under any manifest. It is
documentation and a test target -- the ENFORCEMENT is that `_ROLE_FOR` is an
ALLOWLIST, so a role absent from it is ungrantable by construction and a new
role added to `app_context.ROLES` is ungrantable until someone deliberately
maps a permission onto it. (An allowlist is the direction that fails safe. The
same choice went the other way for moycore's verb table, for the opposite
reason: there, what is enumerable is what libmoy OWNS, and a missed name is a
lost feature rather than a granted capability.)

  * `shell` -- the un-narrowed `Workstation`. It exists for four shipped apps
    that construct `file_widgets.FileGridView`, its consumer list can only
    shrink, and handing it to a cart would make every other line of this module
    decoration.
  * `carts` -- the CART store. A cart that can author carts can write
    executable content, i.e. escalate itself; this is the single most important
    entry in the list and it is why `ctx.files` and `ctx.carts` were split into
    two roles in the first place.
  * `wallpaper` / `artwork` -- capability handles held by exactly one shipped
    app each. `artwork` stays IDENTITY-gated in the Player (Paint's own cart,
    by title+permission+slug), which a renamed copy cannot inherit.
  * `damage` / `surface` -- the shell's invalidation epoch and its live canvas
    plumbing. A cart repaints because the Player ticked it; a cart that could
    dirty the shell every frame would defeat the redraw gate on every tier.
  * `clipboard` / `notify` -- not refused on principle, just not mapped yet. A
    grant with no consumer is a capability granted for nothing.
  * Firmware update and reboot are not roles at all -- they live on
    `ws.updater` / `machine`, reachable only through `shell`.

## UNGATED: `ui`, `theme()`, `screen()`, `bar_h()`

These four are published to every `type: "app"` cart with no permission,
because they are not capabilities -- they are how an app draws.

  * `ui` is the REAL `runtime/ui.py`, the same leaf the shipped apps use: the
    rect algebra, the six-state widgets (`button`/`chip`/`row`/`cell`/
    `tab_row`/`status_row`/`panel`/`dialog`/`text_field`), `ScrollRegion` and
    `Hits`. It takes `(cv, th, rect)` and imports nothing from the shell, so it
    needed no port to cross this boundary.
  * `screen()` returns the canvas the cart is drawing on -- what `ui`'s `cv`
    argument wants. It is the SAME surface the kid API's `rect`/`print` already
    write to, so it grants no new reach; it just names the object.
  * `theme()` returns the live panel-theme token dict, so an app looks like the
    rest of the console and follows a theme change without knowing one
    happened. READING the theme is not a privilege; CHANGING it is
    (`"appearance"`).
  * `bar_h()` is the height of the exitable strip the HOST paints over the top
    of the app's surface (`docs/app_api_v1.md` "the bar contract"). An app draws
    below it. This is published because the alternative is what the seed carts
    actually did: hardcode 18 and get it wrong at font scale 2, which is a
    documented defect in several of them.

## Storage returns `(value, err)` and never raises

Inherited unchanged from `AppContext` (see its module docstring): `err` is
`None`, the `NO_STORE` singleton, or the failure's text. A cart written by a kid
must not be able to crash on a missing SD card, and a `try/except` around every
save is not a thing to teach.
"""

try:
    import ui
    from app_context import NO_STORE
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime import ui
    from runtime.app_context import NO_STORE


# -- the permission -> role allowlist ----------------------------------------
#
# A permission name (or its `name:arg` form) maps to ONE AppContext role. Order
# is irrelevant; membership is everything.

_ROLE_FOR = {
    "files": "files",
    "prefs": "prefs",
    "appearance": "theme",
    "launch": "nav",
}


# Roles a cart is never handed, whatever its manifest says. Enforced by
# `_ROLE_FOR` being an allowlist; named here so the refusal is READABLE and so
# `tests/test_user_apps.py` can pin it against `app_context.ROLES`.
NEVER_GRANTED = ("shell", "carts", "wallpaper", "artwork",
                 "damage", "surface", "clipboard", "notify")


# The user-files kinds a `"files:<kind>"` permission may name. A closed set, and
# `recordings` is deliberately outside it: it is the one FOLDER-valued kind
# (moy_carts.FILE_KINDS), so `save(name, blob)` does not mean anything there.
FILE_KINDS = ("docs", "tables", "drawings", "sprites", "music")

# `"files"` with no kind means the kid's DOCUMENTS -- the same `docs` kind
# Writer authors and Files browses, so an app's notes show up where a kid would
# look for them.
DEFAULT_FILE_KIND = "docs"


def slug(title):
    """`moy_carts.slug` without the import.

    Duplicated on purpose, and it is three lines: this module must stay a leaf
    (it is reached from `player.py`, which imports nothing back into the store),
    and the value is only ever a PREFS NAMESPACE -- if the two ever disagreed
    the cost would be an app reading an empty settings dict, not a corrupted
    one. `tests/test_user_apps.py` pins them equal anyway."""
    out = ""
    for ch in str(title).lower():
        if ch.isalpha() or ch.isdigit():
            out += ch
        elif ch in " -_":
            out += "_"
    return out or "cart"


def app_id_for(cart):
    """The identity a user app's prefs namespace and crash-guard strikes are
    keyed by: the manifest TITLE's slug.

    The title and not the folder, because the two differ by tier -- the device
    seeds a folder from the title slug while the host copies the SOURCE folder
    (`theme_picker.moy` vs `appearance.moy`, the mismatch that broke
    `AppearanceAppLayer.is_app` on device and is now pinned by
    `tests/test_device_seed_parity.py`). Keying on the title makes an app's
    saved settings survive the crossing."""
    if not cart:
        return "app"
    return slug(cart.get("title") or "app")


def _file_kinds(cart):
    """Every user-files kind `cart`'s manifest asks for, in declaration order
    and de-duplicated. Unknown kinds are dropped here (a typo narrows), so what
    comes back is what could actually be granted."""
    out = []
    for perm in (cart.get("permissions") or ()) if cart else ():
        perm = str(perm)
        colon = perm.find(":")
        arg = perm[colon + 1:] if colon >= 0 else None
        if (perm[:colon] if colon >= 0 else perm) != "files":
            continue
        # A scoped grant names its kind; an unscoped one takes the default. An
        # unknown kind is NOT a fallback to the default -- a typo must narrow
        # to nothing, never widen to the kid's documents.
        if arg is None:
            arg = DEFAULT_FILE_KIND
        elif arg not in FILE_KINDS:
            continue
        if arg not in out:
            out.append(arg)
    return out


def manifest_error(cart):
    """A refusal string when the manifest cannot be honoured as written, else
    None. `Player.start` shows it on the ordinary cart-error panel and does not
    run the cart.

    ONE rule today: **a cart gets at most one user-files kind.** `files` is
    published as a single kind-bound handle (`ScopedFiles`, whose verbs take a
    name and never a kind), so there is nowhere for a second kind to go. Until
    this check existed, `["files:docs", "files:tables"]` silently kept the LAST
    one -- an order-dependent grant, with the cart's docs quietly landing in
    tables and no diagnostic anywhere. Refusing beats guessing: a manifest that
    asks for two kinds is asking for something this build does not have, and
    the author is the only one who can say which kind they meant.

    (If multi-kind is ever wanted, it is an API change -- `files.docs.save()`
    style namespacing -- not a widening of this function.)"""
    kinds = _file_kinds(cart)
    if len(kinds) > 1:
        return ("manifest asks for %d file kinds (%s) - pick one"
                % (len(kinds), ", ".join(kinds)))
    return None


def granted_roles(cart):
    """`(roles, file_kind)` for `cart`'s manifest permissions.

    `roles` is the de-duplicated tuple of `AppContext` role names this cart has
    earned; `file_kind` is the user-files kind its `files` grant is scoped to
    (None when it has none). An unknown permission, an unknown file kind and a
    permission naming an ungrantable role all resolve to "no grant" -- a
    manifest can ask for anything and get only what this table says.

    TWO or more file kinds is a manifest ERROR (`manifest_error`), which the
    Player refuses before it ever reaches this function. If some other caller
    skips that check, the files grant is dropped entirely rather than resolved
    to one of them: the residual behaviour of a rule this function cannot
    express has to fail closed, not pick a winner by declaration order."""
    kinds = _file_kinds(cart)
    file_kind = kinds[0] if len(kinds) == 1 else None
    roles = []
    for perm in (cart.get("permissions") or ()) if cart else ():
        perm = str(perm)
        colon = perm.find(":")
        if colon >= 0:
            perm = perm[:colon]
        role = _ROLE_FOR.get(perm)
        if role is None:
            continue
        if role == "files" and file_kind is None:
            continue                  # unknown kind, or the multi-kind refusal
        if role not in roles:
            roles.append(role)
    return (tuple(roles), file_kind)


# -- the kind-scoped user-files handle ---------------------------------------

class ScopedFiles:
    """`ctx.files` narrowed to ONE user-files kind (#108).

    The role itself takes `(kind, name)` on every verb, which would let an app
    granted `"files:docs"` read the kid's drawings by passing another kind.
    This binds the kind at construction and never takes it as an argument, so
    no ARGUMENT reaches another kind: every published verb spells one kind, the
    granted one.

    The unscoped role is held name-mangled (`self.__files`) so the one-hop
    reach-through does not work by accident -- but see the module docstring:
    that is a speed bump on the host and nothing at all on MicroPython, which
    does not mangle. The scope is honest, not enforced.

    Same `(value, err)` contract as the role -- nothing here raises."""

    def __init__(self, files, kind):
        self.__files = files
        self.kind = kind

    def ready(self):
        """True when a writable store is present -- what an app checks before
        offering a SAVE."""
        return self.__files.ready()

    def list(self):
        return self.__files.list(self.kind)

    def load(self, name):
        return self.__files.load(self.kind, name)

    def save(self, name, blob):
        return self.__files.save(self.kind, name, blob)

    def delete(self, name):
        return self.__files.delete(self.kind, name)

    def rename(self, name, new):
        return self.__files.rename(self.kind, name, new)

    def duplicate(self, name):
        return self.__files.duplicate(self.kind, name)

    def new_name(self):
        return self.__files.new_name(self.kind)

    # -- text documents ------------------------------------------------------
    #
    # A `.moytext` on disk is a `moytext-v1` JSON blob, not a bare string, and a
    # cart that writes the string instead produces a file Writer and Files
    # decode to NOTHING -- silently, looking exactly like a save that did not
    # happen. So the codec lives on this side of the boundary and a cart deals
    # in text. `save`/`load` above stay raw for the kinds that are not text.

    def save_text(self, name, text):
        """Write `text` as a document. `(name, err)` -- the name it was saved
        under, so a caller that passed a fresh `new_name()` can remember it."""
        blob = self.__files.encode_text(text)
        if blob is None:
            return (None, NO_STORE)
        value, err = self.__files.save(self.kind, name, blob)
        return (name if err is None else value, err)

    def load_text(self, name):
        """Read a document back as ONE string (lines joined by newlines).
        `("", err)` on failure -- never a raise, never None."""
        blob, err = self.__files.load(self.kind, name)
        if err is not None:
            return ("", err)
        return ("\n".join(self.__files.decode_text(blob)), None)


# -- the factory -------------------------------------------------------------

def make_system_api(ctx_factory, cart, canvas=None, bar_h=None):
    """The extra globals `cart` (a `type: "app"` cart) gets, or `{}`.

    `ctx_factory(app_id, needs, prefs_ns)` is `Workstation.app_context` -- the
    SAME constructor the shipped apps go through, which is the point: a user
    app and a system app are handed the same roles built by the same code, and
    the only difference is where the `needs` tuple came from (a manifest here, a
    class constant there). Nothing in this module holds a `Workstation`.

    `canvas` is the surface the cart draws on -- the fixed game canvas by
    default, the system canvas for a responsive app (see `Player.start`). It is
    passed rather than read off `ctx.surface`, because `ctx.surface.canvas()` is
    always the SYSTEM canvas and a fixed app draws on the game one. `bar_h` is a
    zero-argument callable for the host strip's height, for the same reason: it
    is chrome geometry, not a shell role."""
    roles, kind = granted_roles(cart)
    # `theme` is always needed: `theme()` is ungated. Requesting it twice is
    # harmless (AppContext just attaches the role), but keep the tuple clean so
    # a test can read the grant back off it.
    needs = roles if "theme" in roles else roles + ("theme",)
    app_id = app_id_for(cart)
    ctx = ctx_factory(app_id, needs, app_id)

    ns = {"ui": ui}

    # -- UNGATED: how an app draws -------------------------------------------
    theme = ctx.theme

    def _theme():
        """The live panel-theme tokens (`ui`'s `th` argument). HOIST IT once per
        frame like every shipped app does, never per widget."""
        return theme.colors()

    ns["theme"] = _theme

    def _screen():
        """The canvas this app draws on (`ui`'s `cv` argument)."""
        return canvas

    ns["screen"] = _screen

    def _bar_h():
        """Rows at the top of the surface the HOST's exitable strip owns. Draw
        below it; taps inside it never reach `handle_pointer`."""
        return bar_h() if bar_h is not None else 0

    ns["bar_h"] = _bar_h

    # -- GATED ----------------------------------------------------------------
    if "files" in roles:
        ns["files"] = ScopedFiles(ctx.files, kind)
    if "prefs" in roles:
        ns["prefs"] = ctx.prefs
    if "theme" in roles:
        # The theme ROLE also carries read verbs, but only the write half is a
        # privilege -- reading is already ungated above. So publish two bound
        # functions, not the role: an app with "appearance" can restyle the
        # console, and cannot reach anything else through the same object.
        ns["set_theme"] = theme.set

        def _themes():
            """The installed panel-theme names, so a picker has valid choices.
            `chrome` is imported here and not at module scope: this module is
            reached from the Player's hot start path on every cart, and only an
            appearance-granted app ever needs the table."""
            try:
                import chrome
            except ImportError:  # pragma: no cover - host fallback
                from runtime import chrome
            return list(chrome.THEMES)

        ns["themes"] = _themes
    if "nav" in roles:
        nav = ctx.nav

        def _open_app(app_id):
            """Open another app by its REGISTERED id (`ctx.nav.open_app`).
            False when this build does not carry it -- resolution is by id, so
            a cart never holds a reference to an app."""
            return nav.open_app(app_id)

        ns["open_app"] = _open_app
    return ns


# -- the responsive opt-in ---------------------------------------------------

def wants_layout(src):
    """True when `src` defines a top-level `_layout` -- the RESPONSIVE opt-in.

    A source probe and not a manifest field, deliberately. The canvas a cart
    draws on has to be chosen BEFORE `make_api` closes over it, which is before
    the cart body has ever run, so `ns.get("_layout")` cannot answer in time.
    The alternatives were worse: a new manifest key would have to be threaded
    through load/save/duplicate/seed and kept in step with a `def` the author
    can delete, and `"canvas": "responsive"` would put a non-size into
    SPEC.md 3.1's closed size set.

    Anchored at column 0 so a `_layout` mentioned in a comment, a docstring or
    a nested function cannot arm it -- and it is the same shape `_nativize`
    already uses to find the top-level defs it decorates."""
    if not src:
        return False
    return src.startswith("def _layout(") or ("\ndef _layout(" in src)
