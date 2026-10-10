"""`AppContext` -- the narrowed shell interface a SYSTEM APP is handed
(docs/app_api_v1.md, ui_refactor_2026-08 Phase 6).

An app gets ONE object with a small number of ROLES on it, and it declares
which roles it uses:

    class CalcAppLayer:
        NEEDS = ("surface", "theme", "damage")

`AppContext` is a FILTER over the role set: it attaches only the declared
roles, so touching an undeclared one is an `AttributeError` on the spot rather
than a coupling nobody notices. `tests/test_app_context.py` pins both
directions -- every role an app's source names must be declared, and every role
it declares must be named (an over-declaration is a permission nobody needs).
`make_system_api(ctx, cart)` (runtime/system_api.py) is the same filter keyed
on a cart's manifest permissions rather than on a class constant.

## The roles

    ctx.damage      the whole-system-surface invalidation flags
    ctx.surface     the canvas the app draws on, its size, scales, window, bar,
                    pointer; the toolkit's glyph
    ctx.theme       the live token set + the theme/variant/skin verbs
    ctx.files       the USER-FILES store (#108: drawings/docs/...)
    ctx.carts       the CART store (a cart is a project, not a document)
    ctx.nav         open another app, run a cart, keyboard text mode
    ctx.prefs       persisted per-app settings (system.json, namespaced)
    ctx.notify      achievement events
    ctx.wallpaper   the desktop backdrop (Appearance, Paint, Files' WALL)
    ctx.artwork     Paint's open picture: which it is, and following a rename
    ctx.clipboard   the system cut/copy/paste buffer (#132)
    ctx.install     carts from outside: the network, its lease, the store (#124)

No role hands out the console itself: an app reaches the shell only through
the roles above (`tests/test_app_context.py` holds it).

## The grant, and where each row is served

Every context holds a GRANT (`ctx.grant`): a row of native/moy_app's grant
table, keyed by the app's id, carrying its role mask, its files kind and its
prefs namespace (docs/kernel_appabi_2026-10.md section 2.2). Every role object
is native/moy_app's over that grant, one method per row of
`native/moy_app/roles.json`: a C row reads the kernel's state (the damage
flags, the grant's surface row, the live token table, the settings rows, Paint's
open picture, the clipboard's 4 KiB of text); a shell row calls the server the
console registers for its role (runtime/shell_servers.py) with the grant first.
A shipped app's grant is idempotent by its id, so a start re-registering the
app finds the row it had; a user app's (`run=True`) is its run's, ended when
the run ends (`AppContext.end`).

`ctx.files` and `ctx.carts` are deliberately two roles and not one. Apps use
the first almost exclusively (a document is a user file); an app that writes a
cart -- Storybook authoring a story, Paint and Files copying a picture into a
project's images -- holds the second, which is what says it can write
executable content.

## Conventions that are not negotiable

**No `property` forwards.** A plain attribute hop costs +0.5us on this repo's
MicroPython scaled to the P4, the same forward as a `@property` +5.1us
(ui_refactor_2026-08 Section 2.4). Live shell state is read through a METHOD
(`surface.canvas()`); identity (`ctx.app_id`, the role objects) is a plain
attribute.

**Hoist.** Roles used on every frame are bound in `__init__`
(`self._surf = ctx.surface`) and live values read ONCE at the top of `draw()`
(`cv = surf.canvas()`), never per drawn widget.

**Storage returns `(value, err)`, it does not raise.** `err` is `None` on
success, the `NO_STORE` singleton when there is no writable store, else the
failure's text -- the difference between CAN'T SAVE HERE and CAN'T SAVE <why>.
Each storage verb is one store op; `begin`/`end` bracket a session, which holds
nothing a frame waits on (runtime/shell_servers.py).
"""


import moy_app as _moy_app


class _NoStore:
    """The `err` value meaning "there is no writable store here" -- distinct
    from any exception text, so a caller can tell "storage is off" from "the
    write failed", which is the difference between CAN'T SAVE HERE and
    CAN'T SAVE <why>."""

    def __str__(self):
        return "NO STORAGE"


NO_STORE = _NoStore()


# The complete role vocabulary. `AppContext` refuses an unknown NEED, so a typo
# in a NEEDS tuple fails at construction instead of at the first draw.
ROLES = ("damage", "surface", "theme", "files", "carts", "nav", "prefs",
         "notify", "wallpaper", "artwork", "clipboard", "install")


# -- the context itself ------------------------------------------------------

class AppContext:
    """The object a system app is constructed with, carrying ONLY the roles it
    declared in `NEEDS`.

    Building the roles is a handful of tiny objects at boot (seven apps x a few
    roles), never per frame -- and after construction every access an app makes
    is a plain attribute hop plus one method call, with no descriptor in the
    path."""

    def __init__(self, ws, app_id, needs=(), prefs_ns=None, run=False,
                 kind=None):
        self.app_id = str(app_id)
        for name in needs:
            if name not in ROLES:
                raise ValueError("unknown app context role: " + str(name))
        app = ws.app_abi
        self._app = app
        self.grant = app.grant(self.app_id, needs, kind=kind,
                               ns=prefs_ns or self.app_id, run=run)
        if "damage" in needs:
            self.damage = _moy_app.Damage(app, self.grant)
        if "surface" in needs:
            self.surface = _moy_app.Surface(app, self.grant)
            # The new grant's surface row, from what the shell shows now.
            ws._surface_write()
        if "theme" in needs:
            self.theme = _moy_app.Theme(app, self.grant)
        if "files" in needs:
            self.files = _moy_app.Files(app, self.grant)
        if "carts" in needs:
            self.carts = _moy_app.Carts(app, self.grant)
        if "nav" in needs:
            self.nav = _moy_app.Nav(app, self.grant)
        if "prefs" in needs:
            self.prefs = _moy_app.Prefs(app, self.grant)
        if "notify" in needs:
            self.notify = _moy_app.Notify(app, self.grant)
        if "wallpaper" in needs:
            self.wallpaper = _moy_app.Wallpaper(app, self.grant)
        if "artwork" in needs:
            self.artwork = _moy_app.Artwork(app, self.grant)
        if "clipboard" in needs:
            # The consumers PASS it on (CodeEditor takes `clip=`).
            self.clipboard = _moy_app.Clipboard(app, self.grant)
        if "install" in needs:
            self.install = _moy_app.Install(app, self.grant)

    def copy_gen(self):
        """The wallpaper copy's generation (`wallpaper.save_copy` moves it):
        what a decode of the copy is keyed on (runtime/artwork.py)."""
        return self._app.copy_gen()

    def adopt(self, other):
        """End `other` (a context made for this one's run) when this one ends."""
        self._also = getattr(self, "_also", ()) + (other,)

    def end(self):
        """End this context's grant, and those it adopted: a user app's run
        is over. Its role objects answer a grant that ended from here on."""
        self._app.end(self.grant)
        for other in getattr(self, "_also", ()):
            other.end()
