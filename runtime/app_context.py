"""`AppContext` -- the narrowed shell interface a SYSTEM APP is handed
(docs/app_api_v1.md, ui_refactor_2026-08 Phase 6).

Before this, every app held a `self.ws` back-reference into `Workstation` and
reached through it for whatever it needed, private members included: 41
distinct names / ~371 uses across the seven shipped apps, 13 of them private.
That is a service locator, and it is why nothing can say what an app is allowed
to do -- which is exactly what Phase 7 (user apps) has to answer.

An app now gets ONE object with a small number of ROLES on it, and it declares
which roles it uses:

    class CalcAppLayer:
        NEEDS = ("surface", "theme", "damage")

`AppContext` is a FILTER over the role set: it attaches only the declared
roles, so touching an undeclared one is an `AttributeError` on the spot rather
than a coupling nobody notices. `tests/test_app_context.py` pins both
directions -- every role an app's source names must be declared, and every role
it declares must be named (an over-declaration is a permission nobody needs).
Phase 7's `make_system_api(ctx, cart)` is then the same filter keyed on a
cart's manifest permissions rather than on a class constant.

## The roles

    ctx.damage      the whole-system-surface invalidation flag
    ctx.surface     the system canvas, its font scale, chrome mode, pointer
    ctx.theme       the live token set + the theme/variant verbs
    ctx.files       the USER-FILES store (#108: drawings/docs/tables/...)
    ctx.carts       the CART store (a cart is a project, not a document)
    ctx.nav         open another app, run a cart, keyboard text mode
    ctx.prefs       persisted per-app settings (system.json, namespaced)
    ctx.notify      achievement events
    ctx.wallpaper   the desktop backdrop capability (Appearance + Paint)
    ctx.artwork     the ArtworkService capability handle (Paint's model)
    ctx.clipboard   the system cut/copy/paste buffer (#132)
    ctx.shell       THE ESCAPE HATCH -- see below

`ctx.files` and `ctx.carts` are deliberately two roles and not one. Apps use
the first almost exclusively (a document is a user file); only Storybook
authors CARTS. Conflating them was the source doc's biggest wrong call, because
it hides which apps can write executable content.

`ctx.clipboard` is here against the source plan, which ruled it "one comment
and zero consumers" and deferred it. That reading came from grepping for
`ws` followed by a dot,
which cannot see `getattr(self.ws, "clipboard", None)` -- and that is how all
FIVE of its live consumers are written (Sheets' Ctrl+C/Ctrl+V cell verbs, plus
the `clip=` argument Writer, Sheets and Storybook hand their CodeEditor). The
same blind spot hid Storybook's `getattr(self.ws, "artwork", None)`. When a
count says zero and the feature ships, suspect the grep.

`ctx.shell` is the un-narrowed `Workstation`, and it exists for exactly one
reason: shared WIDGETS (`file_widgets.FileGridView`) still duck-type on
`ws.carts_store` / `ws.carts_root` / `ws._with_sd`. It is a declared NEED like
any other, so the four apps that still pass it are counted rather than hidden,
and it is the one role `make_system_api` will never grant. Reaching through it
for anything a real role covers is the bug this module exists to remove.

## Conventions that are not negotiable

**No `property` forwards.** Measured on this repo's unix MicroPython build and
scaled by the P4 factor (ui_refactor_2026-08 Section 2.4): a plain attribute hop
costs +0.5us, the same forward written as a `@property` costs +5.1us. There is
not one `property` in this file, and `tests/test_app_context.py` asserts it.
Live shell state is read through a METHOD (`surface.canvas()`), which is never
stale and is cheaper than a descriptor; identity (`ctx.app_id`, the role
objects themselves) is a plain attribute.

**Hoist.** The tree already writes `ws = self.ws` 220 times. Under a context
that idiom becomes `surf = ctx.surface` / `cv = surf.canvas()` ONCE at the top
of `draw()`, and roles used on every frame are bound in `__init__`
(`self._surf = ctx.surface`). Reading `ctx.surface.canvas()` per drawn widget
adds a hop per access and is the one way this refactor could cost performance.

**Storage returns `(value, err)`, it does not raise.** `err` is `None` on
success, the `NO_STORE` singleton when there is no writable store, else the
exception's text -- which is precisely the contract `app_shell._persist`
hand-rolled in three apps. Compound sequences that must share ONE storage
session go through `batch(fn)`, whose `fn` receives a RAW view of the same
verbs (bare values, exceptions propagate) so that no verb has two return
shapes.
"""


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
         "notify", "wallpaper", "artwork", "clipboard", "shell")


# -- damage ------------------------------------------------------------------

class Damage:
    """Whole-surface invalidation.

    There is exactly ONE granularity today and this role says so honestly:
    `ws._dirty` is a global epoch flag. `docs/surface_model_v1.md` Section 3
    explicitly RETRACTS a mechanical migration to per-surface attribution, and
    `runtime/surface.py` is denied on two of the three boards, so this is a
    plain leaf and NOT a `SurfaceSet` wrapper (ui_refactor_2026-08 Section 1.2
    cut that). When opt-in attribution arrives it arrives as `damage.at(sid)`;
    `all()` keeps meaning what it means."""

    def __init__(self, ws):
        self.__ws = ws

    def all(self):
        """Repaint the whole system surface next frame."""
        self.__ws._dirty = True


# -- surface -----------------------------------------------------------------

class Surface:
    """The system canvas an app draws on, and the state its geometry depends
    on. Every verb is live: a canvas promote/degrade (#39) or a world flip
    (#105) is picked up on the next call, so nothing here can go stale."""

    def __init__(self, ws):
        self.__ws = ws

    def canvas(self):
        """The SYSTEM canvas (#39) -- a distinct SystemCanvas where the tier
        has one, else the game canvas itself."""
        return self.__ws.sys_canvas

    def font_scale(self):
        """The EFFECTIVE system font scale (1 on a shared 320x240 canvas whose
        framebuf text cannot scale, regardless of the setting)."""
        return self.__ws._effective_font_scale()

    def windowed(self):
        """True while the app is a WINDOW on the desk (#105): the WM's title
        strip carries the close, so the app's own layout reserves no bar band."""
        return self.__ws.windowed_chrome

    def pointer(self):
        """The live `Pointer` (x/y/down/click/visible) or None.

        `handle_pointer(px, py, click)` carries no press state, so a
        drag-based app could not be written on the declared API at all -- which
        is why four shipped apps reach for `ws.pointer` today. This is that
        seam."""
        return self.__ws.pointer

    def glyph(self, kind, rect, c, cv=None):
        """Draw a centred `chrome._GLYPHS` icon. Pass this bound method as
        `glyph_draw=` to `ui.chip` -- the icon vocabulary bridge."""
        self.__ws._glyph(kind, rect, c, cv)


# -- theme -------------------------------------------------------------------

class Theme:
    """The live panel-theme tokens and the verbs that change them
    (docs/visual_identity_v1.md Section 4.3)."""

    def __init__(self, ws):
        self.__ws = ws

    def colors(self):
        """The live token dict. HOIST IT: `th = ctx.theme.colors()` once per
        draw, never per widget."""
        return self.__ws.theme_colors

    def light(self):
        """True when the live theme's tool surface is LIGHT -- THE gate every
        surface's light branch reads."""
        return self.__ws.light_chrome()

    def name(self):
        return self.__ws.theme_name

    def variant(self):
        return self.__ws.theme_variant

    def skin(self):
        """The installed WIDGET skin's name (`runtime/skin.py`) -- the third
        axis of the look, beside the theme family and its dark/light variant.

        The active value is a role read, like `name()`; the CATALOG is not
        (see below)."""
        return self.__ws.skin_name

    # A theme PICKER wants the OTHER themes' tokens too. That is not a role:
    # `chrome.THEMES` / `THEME_VARIANTS` / `theme_colors()` are a pure leaf
    # module an app imports directly, exactly as `ui` is imported directly.
    # `skin.names()` is the same shape of thing and travels the same way.

    def set(self, name, persist=True, variant=None):
        self.__ws.set_theme(name, persist=persist, variant=variant)

    def set_variant(self, variant, persist=True):
        self.__ws.set_theme_variant(variant, persist=persist)

    def set_skin(self, name, persist=True):
        """Install a widget skin and remember it. The Workstation owns the
        install because the skin is process-wide state in `ui` and its name is
        a persisted setting -- exactly like the theme."""
        self.__ws.set_skin(name, persist=persist)


# -- the storage roles' shared machinery -------------------------------------

class _StoreRole:
    """What `Files`, `Carts` and `WallpaperRole` all are underneath: a
    readiness pair, a `batch(fn)` session, and the `(value, err)` wrappers that
    turn ONE `_with_sd` call plus a try/except into the module's contract.

    It was written twice verbatim (`Files` and `Carts` carried identical
    `readable`/`ready`/`batch`/`_read`/`_write` bodies) and hand-rolled twice
    more, in `Files.history_commit` and in the wallpaper role's copy verbs --
    four copies of a try/except whose whole job is that nothing here raises.
    One of them going quietly wrong is not a thing a caller could notice: it
    would surface as a save that did not happen.

    NOT a role: `ROLES` does not name it and `AppContext` never builds one.
    Subclasses call `_StoreRole.__init__(self, ws)`, set `self.raw` to their
    in-session view, and reach the shell through `_store()` / `_shell()` -- the
    Workstation is held HERE, once, so there is a single mangled reference
    rather than one per subclass."""

    def __init__(self, ws):
        self.__ws = ws

    # -- the shell, for subclasses ------------------------------------------

    def _store(self):
        """The cart-store MODULE (`moy_carts`), or None where no store is
        wired. The codec verbs are pure functions on it."""
        return self.__ws.carts_store

    def _shell(self):
        """The Workstation. Module-internal, for the handful of verbs that
        reach past the store itself (`_all_carts`, `_rehydrate_cart`,
        `wallpaper_id`, ...)."""
        return self.__ws

    # -- readiness -----------------------------------------------------------

    def readable(self):
        """A store exists to READ from."""
        ws = self.__ws
        return ws.carts_store is not None and ws.carts_root is not None

    def ready(self):
        """A store exists AND writes are enabled (the `_store_ready` predicate
        every Desk-Lab app used to spell out)."""
        return self.readable() and bool(self.__ws.can_manage)

    # -- the session ---------------------------------------------------------

    def _session(self, fn):
        """`fn()` inside ONE storage session, as `(value, err)`. The single
        try/except in this module's storage path."""
        try:
            return (self.__ws._with_sd(fn), None)
        except Exception as exc:  # noqa: BLE001 -- surface, never crash the shell
            return (None, str(exc))

    def batch(self, fn):
        """Run several verbs under ONE storage session -- the SD mount is the
        expensive part, and every app that reads a directory listing then N
        files does so in one `_with_sd` today.

        `fn(raw)` gets the RAW view (bare returns, exceptions propagate);
        `batch` returns `(fn's value, err)`."""
        if not self.ready():
            return (None, NO_STORE)
        return self._session(lambda: fn(self.raw))

    def _read(self, verb, *args):
        if not self.readable():
            return (None, NO_STORE)
        return self._session(lambda: verb(*args))

    def _write(self, verb, *args):
        if not self.ready():
            return (None, NO_STORE)
        return self._session(lambda: verb(*args))


# -- the user-files store (#108) ---------------------------------------------

class _RawFiles:
    """The user-files verbs with NO session and NO error wrapping -- what runs
    INSIDE `Files.batch()`, where the batch already owns the try/except. Bare
    return values; a failure raises. Kept as a separate class so no verb name
    ever has two return shapes."""

    def __init__(self, ws):
        self.__ws = ws

    # -- named documents ----------------------------------------------------

    def list(self, kind):
        return self.__ws.carts_store.list_files(kind, self.__ws.carts_root)

    def count(self, kind):
        return self.__ws.carts_store.count_files(kind, self.__ws.carts_root)

    def load(self, kind, name):
        return self.__ws.carts_store.load_file(kind, name, self.__ws.carts_root)

    def save(self, kind, name, blob):
        return self.__ws.carts_store.save_file(kind, name, blob,
                                              self.__ws.carts_root)

    def delete(self, kind, name):
        return self.__ws.carts_store.delete_file(kind, name, self.__ws.carts_root)

    def duplicate(self, kind, name):
        return self.__ws.carts_store.duplicate_file(kind, name,
                                                   self.__ws.carts_root)

    def rename(self, kind, name, new):
        return self.__ws.carts_store.rename_file(kind, name, new,
                                                self.__ws.carts_root)

    def new_name(self, kind):
        return self.__ws.carts_store.new_file_name(kind, self.__ws.carts_root)

    # -- the restorable trash ------------------------------------------------

    def trash_list(self):
        return self.__ws.carts_store.trash_list(self.__ws.carts_root)

    def restore(self, kind, name):
        return self.__ws.carts_store.restore_file(kind, name, self.__ws.carts_root)

    def empty_trash(self):
        return self.__ws.carts_store.empty_trash(self.__ws.carts_root)

    # -- one-shot layout migrations (#108) -----------------------------------

    def migrate(self, kind=None):
        """The `files/` migrations. `None` = the whole user-files layer;
        `"docs"`/`"tables"` = that kind's own one-shot move."""
        store = self.__ws.carts_store
        if kind == "docs":
            return store.migrate_docs(self.__ws.carts_root)
        if kind == "tables":
            return store.migrate_tables(self.__ws.carts_root)
        return store.migrate_user_files(self.__ws.carts_root)

    # -- the #111 op-history sidecars ---------------------------------------

    def history(self, kind, name):
        return self.__ws.carts_store.load_history(kind, name, self.__ws.carts_root)

    def history_ops(self, kind, name):
        store = self.__ws.carts_store
        return store.ops_since_keyframe(
            store.load_history(kind, name, self.__ws.carts_root))

    def history_commit(self, kind, name, ops, keyframe=None):
        return self.__ws.carts_store.history_commit(
            kind, name, ops, keyframe=keyframe, root=self.__ws.carts_root)


class Files(_StoreRole):
    """The USER-FILES store (#108): `files/<kind>/` beside the carts dir, with
    a restorable trash, auto-naming and the op-history sidecars.

    Every verb returns `(value, err)`; `err` is `None`, `NO_STORE`, or the
    failure's text. Nothing here raises -- that is the contract
    `app_shell._persist` already hand-rolled three times. The readiness pair,
    `batch` and the `_read`/`_write` wrappers are `_StoreRole`'s."""

    def __init__(self, ws):
        _StoreRole.__init__(self, ws)
        self.raw = _RawFiles(ws)

    # -- named documents -----------------------------------------------------

    def list(self, kind):
        return self._read(self.raw.list, kind)

    def count(self, kind):
        return self._read(self.raw.count, kind)

    def load(self, kind, name):
        return self._read(self.raw.load, kind, name)

    def save(self, kind, name, blob):
        return self._write(self.raw.save, kind, name, blob)

    def delete(self, kind, name):
        return self._write(self.raw.delete, kind, name)

    def duplicate(self, kind, name):
        return self._write(self.raw.duplicate, kind, name)

    def rename(self, kind, name, new):
        return self._write(self.raw.rename, kind, name, new)

    def new_name(self, kind):
        return self._read(self.raw.new_name, kind)

    # -- the restorable trash ------------------------------------------------

    def trash_list(self):
        return self._read(self.raw.trash_list)

    def restore(self, kind, name):
        return self._write(self.raw.restore, kind, name)

    def empty_trash(self):
        return self._write(self.raw.empty_trash)

    def migrate(self, kind=None):
        return self._write(self.raw.migrate, kind)

    # -- the #111 op-history sidecars ---------------------------------------

    def history(self, kind, name):
        """The raw sidecar records. To SEED an undo stack use `history_ops` --
        the records before the last keyframe are superseded by it."""
        return self._read(self.raw.history, kind, name)

    def history_ops(self, kind, name):
        """The ops after the sidecar's last keyframe -- an op_history.History
        seed. The ONE reader of that window (moy_carts.ops_since_keyframe)."""
        return self._read(self.raw.history_ops, kind, name)

    def history_commit(self, kind, name, ops, keyframe=None):
        # `keyframe` rides as a positional through `_write` (the raw verb takes
        # it fourth); it used to be a hand-rolled copy of `_write` for exactly
        # that one keyword.
        return self._write(self.raw.history_commit, kind, name, ops, keyframe)

    # -- the image codec + provenance stamps (#108 phase 2) ------------------
    #
    # Pure functions on the store module: no session, no failure mode beyond a
    # bad blob, so they return the value directly rather than a pair.

    def encode_image(self, w, h, indices):
        store = self._store()
        return store.encode_moyimg(w, h, indices) if store is not None else None

    def decode_image(self, blob):
        store = self._store()
        return store.decode_moyimg(blob) if (store is not None and blob) else None

    def sig(self, blob):
        """The content signature a copy is stamped with, so a later edit can
        offer "your drawing changed -> UPDATE"."""
        store = self._store()
        return store.content_sig(blob) if store is not None else None

    def stamp(self, blob, kind, name, sig):
        """Stamp `blob` with where it was copied FROM and that source's sig."""
        return self._store().stamp_provenance(blob, kind, name, sig)

    # -- the moytext codec (#181) --------------------------------------------
    #
    # Pure functions on the store, same shape as the image codec above. Here
    # because a USER APP cart saving a note must write the blob Writer and Files
    # can READ -- a plain string in a `.moytext` decodes to nothing, silently,
    # and looks exactly like a save that did not happen.

    def encode_text(self, body):
        store = self._store()
        return store.encode_text(body) if store is not None else None

    def decode_text(self, blob):
        """The doc body as a list of LINES ([] on anything malformed)."""
        store = self._store()
        return store.decode_text(blob) if (store is not None and blob) else []

    def provenance(self, blob):
        """`(src_key, sig)` off a stamped copy, or `(None, None)`."""
        store = self._store()
        if store is None or not blob:
            return (None, None)
        return store.read_provenance(blob)


# -- the cart store ----------------------------------------------------------

class _RawCarts:
    """`Carts`' in-session view -- same split, same reason as `_RawFiles`."""

    def __init__(self, ws):
        self.__ws = ws

    def create(self, title, src=None, type=None):
        return self.__ws.carts_store.create(title, self.__ws.carts_root, src=src,
                                           type=type)

    def scan(self):
        return self.__ws.carts_store.scan(self.__ws.carts_root)

    def load_deck(self, cart):
        return self.__ws.carts_store.load_deck(cart)

    def save_deck(self, cart, blob):
        return self.__ws.carts_store.save_deck(cart, blob)

    def save_code(self, cart, src):
        return self.__ws.carts_store.save_code(cart, src)

    def images(self, cart):
        """The cart's image assets by name (`moy_carts.load_images` takes the
        cart's PATH, so a path string is accepted too)."""
        path = cart.get("path") if isinstance(cart, dict) else cart
        return self.__ws.carts_store.load_images(path)

    def save_image(self, cart, name, blob):
        return self.__ws.carts_store.save_image(cart, name, blob)

    def save_table(self, cart, name, blob):
        return self.__ws.carts_store.save_table(cart, name, blob)

    def journal_append(self, path, main, src, grad=0):
        return self.__ws.carts_store.journal_append(path, main, src, grad=grad)


class Carts(_StoreRole):
    """The CART store -- projects, not documents.

    Deliberately a SEPARATE role from `ctx.files`: a cart is executable
    content, and an app that can author one is doing something categorically
    different from an app that saves a drawing. Storybook is the only shipped
    consumer of the authoring half; Sheets and Paint use only the
    copy-into-a-project verbs. Same `(value, err)` contract as `Files`, off the
    same `_StoreRole` machinery."""

    def __init__(self, ws):
        _StoreRole.__init__(self, ws)
        self.raw = _RawCarts(ws)

    def all(self):
        """Every scanned cart (the FULL list, not the launcher run-grid)."""
        return self._shell()._all_carts

    def can_journal(self):
        """True when the store carries the #111 journal verbs (an older store
        module simply does not, and the graduation path degrades)."""
        store = self._store()
        return store is not None and hasattr(store, "journal_append")

    def slug(self, text):
        store = self._store()
        return store.slug(text) if store is not None else text

    def hydrate(self, cart):
        """Load a slimmed cart's full payloads back IN PLACE (#66)."""
        return self._shell()._rehydrate_cart(cart)

    def apply(self, items):
        """Adopt a fresh scan as the live cart list (re-derives both grids)."""
        self._shell()._apply_items(items)

    def load_deck(self, cart):
        return self._read(self.raw.load_deck, cart)

    def save_deck(self, cart, blob):
        return self._write(self.raw.save_deck, cart, blob)

    def save_code(self, cart, src):
        return self._write(self.raw.save_code, cart, src)

    def images(self, cart):
        return self._read(self.raw.images, cart)

    def save_image(self, cart, name, blob):
        return self._write(self.raw.save_image, cart, name, blob)

    def save_table(self, cart, name, blob):
        return self._write(self.raw.save_table, cart, name, blob)

    # The .moyimg ENCODER, mirrored from `Files`. Deliberately on both roles: a
    # `.moyimg` blob is the same bytes whether it lands in `files/drawings/` or
    # in a cart's `images/`, and Storybook (which needs it to put a painting on a
    # page) must not be handed the whole user-files store to reach it. There is
    # no decoder here on purpose -- `images()` hands back the stored blobs and
    # nothing reads a cart image through this role.

    def encode_image(self, w, h, indices):
        store = self._store()
        return store.encode_moyimg(w, h, indices) if store is not None else None


# -- navigation --------------------------------------------------------------

class Nav:
    """Where the console goes next.

    `app()`/`open_app()` are the APP-TO-APP seam. `docs/app_api_v1.md` listed
    app-to-app as an explicit v1 NON-GOAL and it shipped anyway -- `files_app`
    reaches `ws.writer_app.open_named(...)` across five sites, because "open
    this table in Sheets" is a real product need and there was no seam for it.
    This is the seam. It resolves by registered ID, so an app never holds a
    hard reference to another app's class."""

    def __init__(self, ws):
        self.__ws = ws

    def app(self, app_id):
        """The registered app layer for `app_id`, or None when this build does
        not carry it."""
        return self.__ws._apps_by_id.get(app_id)

    def open_app(self, app, cart=None):
        """Spawn a registered app (an id string or the layer itself). False
        when no cart carries that app's identity."""
        if isinstance(app, str):
            app = self.__ws._apps_by_id.get(app)
            if app is None:
                return False
        return bool(self.__ws.open_app(app, cart))

    def is_system_app(self, cart):
        """True when a registered app's identity claims `cart` -- what keeps
        app carts out of project lists."""
        return self.__ws.is_system_app(cart)

    def play(self, cart, caller):
        """Open `cart` as a workspace and RUN it, returning to `caller` on
        exit -- the Storybook PLAY verb."""
        ws = self.__ws
        ws._open_workspace(cart)
        ws.run(ws.project, caller)

    def text_mode(self, on):
        """Flip the keyboard between typing (clean ASCII) and game (raw
        matrix) mode. A TYPING app gets this from its registration; this is for
        an app that changes mode mid-session (Storybook's page editor)."""
        self.__ws._set_text_mode(bool(on))


# -- persisted per-app settings ---------------------------------------------

class Prefs:
    """Per-app settings on the shell's own `system.json` (the store Settings
    already uses for theme/wallpaper/font/OTA channel).

    NAMESPACED: keys are written as `<ns>_<key>`, where `ns` defaults to the
    app id. It is a constructor argument and not a hard-wired `app_id` because
    the shipped keys predate this role -- Paint's document pointer has been
    `paint_doc` on real cards since #108, and silently renaming it would lose
    every kid's open drawing on the next boot."""

    def __init__(self, ws, ns):
        self.__ws = ws
        self._prefix = str(ns) + "_"

    def get(self, key, default=None):
        return self.__ws.system.get(self._prefix + key, default)

    def set(self, key, value, persist=True):
        self.__ws.system[self._prefix + key] = value
        if persist:
            self.__ws._persist_system()

    def clear(self, key, persist=True):
        self.__ws.system.pop(self._prefix + key, None)
        if persist:
            self.__ws._persist_system()


# -- notifications -----------------------------------------------------------

class Notify:
    """Achievements (#21) and the system notice banner."""

    def __init__(self, ws):
        self.__ws = ws

    def achieve(self, kind, key=None):
        """Note an achievement event. Silent when the build carries none."""
        ach = getattr(self.__ws, "ach", None)
        if ach is not None:
            ach.note(kind, key)


# -- the desktop backdrop capability ----------------------------------------

class WallpaperRole(_StoreRole):
    """The desktop backdrop (#28). A CAPABILITY, not a core role -- two apps
    use it (Appearance chooses one, Paint publishes into one) and nothing else
    should.

    A `_StoreRole` for its two COPY verbs only: the backdrop has a backing file
    and therefore the same session + `(value, err)` contract the storage roles
    have. Everything above the copy verbs is plain shell state. It sets no
    `raw` view (there is no directory of backdrops to walk), so `batch` is not
    part of this role's surface."""

    def __init__(self, ws):
        _StoreRole.__init__(self, ws)

    def current(self):
        """The active wallpaper id (a cart slug or `fill:<color>`)."""
        return self._shell().wallpaper_id

    def carts(self):
        """The wallpaper-type carts available as backdrops."""
        return self._shell().wallpaper_carts()

    def fills(self):
        """The built-in solid fills -- always present, so there is always a
        valid pick even with zero wallpaper carts installed."""
        return self._shell()._FILL_WALLPAPERS

    def id_for(self, cart):
        return self._shell()._wp_id_for(cart)

    def cart_by_id(self, wp_id):
        return self._shell()._wp_cart_by_id(wp_id)

    def select(self, wp_id, persist=True):
        self._shell().select_wallpaper(wp_id, persist=persist)

    def preview(self, cv, rect, dt):
        """Composite the live backdrop into `rect` -- the Appearance preview."""
        self._shell().wallpaper.draw_preview(cv, rect, dt)

    # The backdrop's own backing file (the legacy artwork.moyimg, now the
    # wallpaper COPY -- #108 copy-on-set). Same (value, err) contract as Files,
    # and now the same MACHINERY: `_read`/`_write` carry the readiness gate and
    # the one try/except, and the two `_raw_*` bodies below are this role's
    # in-session view (the `raw` split every storage role uses).

    def load_copy(self):
        return self._read(self._raw_load_copy)

    def save_copy(self, blob):
        return self._write(self._raw_save_copy, blob)

    def _raw_load_copy(self):
        ws = self._shell()
        return ws.carts_store.load_artwork(ws.carts_root)

    def _raw_save_copy(self, blob):
        ws = self._shell()
        return ws.carts_store.save_artwork(blob, ws.carts_root)


# -- the context itself ------------------------------------------------------

class AppContext:
    """The object a system app is constructed with, carrying ONLY the roles it
    declared in `NEEDS`.

    Building the roles is a handful of tiny objects at boot (seven apps x a few
    roles), never per frame -- and after construction every access an app makes
    is a plain attribute hop plus one method call, with no descriptor in the
    path."""

    def __init__(self, ws, app_id, needs=(), prefs_ns=None):
        self.app_id = str(app_id)
        for name in needs:
            if name not in ROLES:
                raise ValueError("unknown app context role: " + str(name))
        if "damage" in needs:
            self.damage = Damage(ws)
        if "surface" in needs:
            self.surface = Surface(ws)
        if "theme" in needs:
            self.theme = Theme(ws)
        if "files" in needs:
            self.files = Files(ws)
        if "carts" in needs:
            self.carts = Carts(ws)
        if "nav" in needs:
            self.nav = Nav(ws)
        if "prefs" in needs:
            self.prefs = Prefs(ws, prefs_ns or self.app_id)
        if "notify" in needs:
            self.notify = Notify(ws)
        if "wallpaper" in needs:
            self.wallpaper = WallpaperRole(ws)
        if "artwork" in needs:
            # The ArtworkService instance itself, not a wrapper: it is already
            # a narrow capability object with its own vocabulary, and wrapping
            # it would only add a hop (ui_refactor_2026-08 Section 4).
            self.artwork = ws.artwork
        if "clipboard" in needs:
            # The Clipboard object itself (#132): a plain leaf with copy/paste,
            # and the consumers PASS it on (CodeEditor takes `clip=`), so a
            # wrapper would have to be unwrapped again.
            self.clipboard = getattr(ws, "clipboard", None)
        if "shell" in needs:
            self.shell = ws
