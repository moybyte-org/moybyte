"""`make_system_api` -- the globals a USER APP cart gets, keyed on the
permissions its manifest declares (#181, ui_refactor_2026-08 Phase 7).

## What this is, and what it deliberately is NOT

A SHIPPED system app (Calc, Files, Paint ...) is shell code: a Layer class in
`runtime/` that declares a `NEEDS` tuple and is handed an `AppContext`
(`runtime/app_context.py`) carrying exactly those roles. A USER APP is a
`.moy` CART -- editable in the project picker like any other cart, written by
whoever owns the console -- that asks for shell capabilities in its
`manifest.json`, in its `"moybyte"` object (Moybyte's own fields):

    "moybyte": {"type": "app",
                "permissions": ["graphics", "input", "files:docs", "prefs"]}

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

The policy is C's (native/moy_app, `moy_app.policy`): the permission each
role is granted by, the files kinds, the manifest refusal
(`moy_app.manifest_error`) and the key a cart's grant is made under
(`moy_app.id_for`: the cart's `id`, #162, else its title's slug).

    permission        cart globals                    AppContext role
    ---------------   -----------------------------   ---------------
    "files"           files.*  + open_editor()        ctx.files
    "files:<kind>"    the same, that kind only        ctx.files
    "clipboard"       (the editor handle's cut/copy)  ctx.clipboard
    "prefs"           prefs.get / prefs.set           ctx.prefs (namespaced)
    "appearance"      set_theme() / themes()          ctx.theme
    "launch"          open_app(id)                    ctx.nav

`open_editor` rides the `files` grant because a text editor IS a document
handle: it opens `(kind, name)` through the same role `files.*` narrows, and
the cart can never NAME a kind -- it gets the one it was granted, or the one
the console was already asked to open (the Files router's door). The engine
behind it stays in the shell; `runtime/editor_handle.py` is the whole of it.

Every `type: "app"` cart also gets `ui`, `theme()`, `screen()` and `bar_h()` --
see UNGATED below. Everything else a manifest lists (`"graphics"`, `"input"`,
`"audio"`, an app's own marker permission like `"calc"`) is not a grant here and
is silently ignored, which is correct: the kid API is already the ungated floor.

## What is NEVER grantable, and why the list is a positive one

The policy's permission table (`moy_app.perms()`) is an ALLOWLIST, so a role
absent from it is ungrantable by construction and a new role added to the role
table is ungrantable until someone deliberately maps a permission onto it;
`tests/test_user_apps.py` names the roles it leaves out. (An allowlist is the direction that fails safe. The
same choice went the other way for moycore's verb table, for the opposite
reason: there, what is enumerable is what libmoy OWNS, and a missed name is a
lost feature rather than a granted capability.)

  * `carts` -- the CART store. A cart that can author carts can write
    executable content, i.e. escalate itself; this is the single most important
    entry in the list and it is why `ctx.files` and `ctx.carts` were split into
    two roles in the first place.
  * `install` -- carts from the internet, written into the store. It is
    `carts`' reason twice over: executable content, and somebody else's.
  * `wallpaper` / `artwork` -- capability handles held by exactly one shipped
    app each. `artwork` stays IDENTITY-gated in the Player (Paint's own cart,
    by title+permission+slug), which a renamed copy cannot inherit.
  * `damage` / `surface` -- the shell's invalidation epoch and its live canvas
    plumbing. A cart repaints because the Player ticked it; a cart that could
    dirty the shell every frame would defeat the redraw gate on every tier.
  * `notify` -- not refused on principle, just not mapped yet. A grant with no
    consumer is a capability granted for nothing. `clipboard` sat here for the
    same reason until the editor handle became its consumer; it is mapped now,
    and a cart still never touches the buffer -- the handle's `cut`/`copy`/
    `paste` are the only things that read it.
  * Firmware update and reboot are not roles at all -- they live on
    `ws.updater` / `machine`, and no role hands out the console.

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


import moy_app as _moy_app


# The marker permission the console's TEXT APP claims its cart with -- the
# `.md`/`.json`/`.txt` door the Files router opens (docs/text_editing_2026-09).
# Not a permission the grant policy maps, so it grants nothing; it only says
# which cart this is, the way a shipped app claims its cart by title +
# `APP_PERM`.
TEXT_APP_PERM = "editor"


def is_text_app(cart):
    """True when `cart` is the console's text app."""
    perms = (cart.get("permissions") or ()) if cart else ()
    return TEXT_APP_PERM in perms


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

    def new_name(self, title=None):
        """A free name for a NEW item. With no `title` the kind auto-names, so
        making a thing is never gated on naming it; with one it is that title,
        slugged and unique-ified by the STORE -- which is also what keeps an
        extension a person typed (`todo.txt`, `hi.py`) and so picks the mode
        the editor opens it in."""
        return self.__files.new_name(self.kind, title)

    def badge(self, name):
        """The short label a LISTING puts beside `name` -- MD / TXT / JSON /
        PY / LUA. The console-wide mode table's answer (`text_modes`), not a
        second reading of the extension: the vault holds notes, plain text,
        data and scripts side by side, and the skin that lists them may not
        have its own opinion about which is which.

        Imported at the CALL, like `_themes` below and for the same reason:
        this module is a leaf on the Player's start path for every cart, and
        the mode table reaches the store behind it."""
        try:
            import text_modes
        except ImportError:  # pragma: no cover - host fallback
            from runtime import text_modes
        return text_modes.badge_for_kind(self.kind, name)

    # -- text documents ------------------------------------------------------
    #
    # A document on disk is plain Markdown (`files/docs/<name>.md`), so these
    # two are thin -- but they stay the verbs a cart calls, because they are
    # what says a document is TEXT and not a blob. `save`/`load` above stay raw
    # for the kinds that are not text.

    def save_text(self, name, text):
        """Write `text` as a document. `(name, err)` -- the name it was
        actually saved under, which is the STORE's answer and not the one that
        was passed: a title is slugged on the way in, so a caller that typed
        one has to be told what it became."""
        blob = self.__files.encode_text(text)
        if blob is None:
            return (None, NO_STORE)
        return self.__files.save(self.kind, name, blob)

    def load_text(self, name):
        """Read a document back as ONE string (lines joined by newlines).
        `("", err)` on failure -- never a raise, never None."""
        blob, err = self.__files.load(self.kind, name)
        if err is not None:
            return ("", err)
        return ("\n".join(self.__files.decode_text(blob)), None)


# -- the factory -------------------------------------------------------------

def make_system_api(ctx_factory, cart, canvas=None, bar_h=None,
                    editor=None, request=None, keep=None):
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
    is chrome geometry, not a shell role.

    `editor` is the shell's editor-handle factory (`Player._open_cart_editor`)
    and `request` is the `(kind, name, mode)` the console was asked to open --
    both passed the same way and for the same reason: they are the running
    shell, not a role. Without them `open_editor` simply has no name.

    The context is a RUN grant (native/moy_app), keyed by the cart's id and
    made from its permissions by the C policy (`moy_app.policy`); `keep` is
    handed the context so the run can end its grant when it ends."""
    perms = cart.get("permissions") if cart else None
    roles, kind = _moy_app.policy(perms)
    # `theme` is always needed: `theme()` is ungated. Requesting it twice is
    # harmless (AppContext just attaches the role), but keep the tuple clean so
    # a test can read the grant back off it.
    needs = roles if "theme" in roles else roles + ("theme",)
    app_id = _moy_app.id_for(cart.get("id") if cart else None,
                             cart.get("title") if cart else None)
    ctx = ctx_factory(app_id, needs, app_id, run=True, kind=kind)
    if keep is not None:
        keep(ctx)

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
        if editor is not None:
            # The editor handle (docs/text_editing_2026-09.md step 3). The cart
            # passes a NAME and never a kind: with one it edits an item of the
            # kind it was granted; with none it picks up whatever document the
            # console was already asked to open, which may be another kind
            # because a PERSON chose that file in Files.
            files = ctx.files
            clip = getattr(ctx, "clipboard", None)

            def _open_editor(name=None, mode=None):
                """An editor over one document, or None when there is none."""
                if name is None:
                    if not request:
                        return None
                    r_kind, r_name, r_mode = request
                    return editor(files, r_kind, r_name, mode or r_mode,
                                  canvas, clip)
                return editor(files, kind, str(name), mode, canvas, clip)

            ns["open_editor"] = _open_editor
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
            # NAMES, not the (name, tokens) pairs: `set_theme` beside this takes
            # a name and silently falls back to DEFAULT_THEME on anything else,
            # so returning pairs made the documented `set_theme(themes()[0])`
            # always select "night", with nothing reporting it.
            return [n for n, _tokens in chrome.THEMES]

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
