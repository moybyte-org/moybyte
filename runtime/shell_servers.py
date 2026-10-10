# Map (grep -n a name to jump there):
#   SurfaceServer    surface.glyph, the toolkit's icon callable
#   ThemeServer      theme.set*, the verbs that change the look
#   _Store           the (value, err) storage wrappers the storage servers share
#   _Session         the readiness pair and the session of the cart store
#   CartsServer      the cart store: projects, not documents
#   NavServer        where the console goes next
#   NotifyServer     achievement events
#   WallpaperServer  the desktop backdrop
#   InstallServer    carts from outside: the radio lease, the store, the engine
#   RoleDoor         the ROLE upcall's Python half: a compiled app's call
#   serve_all        register every server on the console's app ABI
"""The console's servers for the app ABI's rows it serves (native/moy_app/
roles.json's "shell" rows, docs/kernel_appabi_2026-10.md section 2.1): the
verbs whose state is the Python console's -- the look, the live cart list, the
wallpaper cart, the achievements, the routes, Get Carts' transport. The files
role and the wallpaper's copy are C over the user-files layer
(native/moy_store/moy_ufiles.h) the console binds (`StoreHandle.bind`), and
none of theirs is here.

The Workstation builds one of each and registers it per role
(`app.serve(role, server)`, `serve_all`); an app's role object (native/
moy_app's `Carts`, `Nav`, ...) checks the grant, counts the call and calls
`server.<verb>(grant, *args)`. So every verb here takes the calling grant
first, and the few that need the caller read it from the grant
(`nav.play` returns to the grant's app). A compiled app reaches the same
servers through the ROLE upcall.

Storage verbs answer `(value, err)` and never raise: `err` is None, the
`NO_STORE` singleton, or the failure's text. Each verb is ONE store op, taking
the bus gate around itself (`StoreHandle.call`; on the T-Deck the card shares
the panel's SPI host); a session (`begin`/`end`) is readiness and nothing else
-- it holds no gate, so one left open cannot stop the panel flushing, and the
console ends any left open at the frame's end and at a run's end
(`StoreHandle.end_all`).

A cart crosses the Python binding as the shell's cart dict (the live list's own
entry, which carries its store handle as "h"); the ROLE door names it by its
folder.

A compiled app's call of one of these rows arrives through the kernel's ROLE
upcall (native/moy_kernel/moy_loop.h's moy_loop_role, counted ROLE) as the
table row and its packed arguments (native/moy_app/moy_app.h's field
encoding); `RoleDoor` decodes them, calls the same server and answers what the
door copies back.
"""

try:
    from app_context import NO_STORE
except ImportError:  # pragma: no cover - host package lane
    from runtime.app_context import NO_STORE


# -- surface and theme ---------------------------------------------------------

class SurfaceServer:
    """`surface.glyph`, the role's Python-only row: the toolkit's icon
    callable. Every other surface verb is the grant's C surface row, which the
    console writes (`Workstation._surface_write`)."""

    def __init__(self, ws):
        self.__ws = ws

    def glyph(self, g, kind, rect, c, cv=None):
        """Draw a centred `chrome._GLYPHS` icon. Pass `ctx.surface.glyph` as
        `glyph_draw=` to `ui.chip` -- the icon vocabulary bridge."""
        self.__ws._glyph(kind, rect, c, cv)


class ThemeServer:
    """`theme.set`, `set_variant` and `set_skin`: the look coordinates its
    caches, the skin and the wallpaper, so the verbs that change it are the
    look's (docs/visual_identity_v1.md Section 4.3). The reads are the C token
    table the look writes at every switch (`Appearance.publish`).

    A theme PICKER wants the OTHER themes' tokens too. That is not a role:
    `chrome.THEMES` / `THEME_VARIANTS` / `theme_colors()` are a pure leaf
    module an app imports directly, exactly as `ui` is imported directly."""

    def __init__(self, ws):
        self.__ws = ws

    def set(self, g, name, variant=None):
        self.__ws.look.set_theme(name, variant=variant)

    def set_variant(self, g, variant):
        self.__ws.look.set_theme_variant(variant)

    def set_skin(self, g, name):
        """Install a widget skin and remember it: the skin is process-wide
        state in `ui` and its name a persisted setting, like the theme."""
        self.__ws.look.set_skin(name)


# -- the storage servers' shared machinery --------------------------------------

class _Store:
    """The readiness pair, the session and the `(value, err)` wrappers the
    storage servers share: ONE store op per verb inside the one try/except, so
    nothing here raises. The shell's `StoreHandle` (runtime/system_store.py)
    is the guard, read per call."""

    def __init__(self, ws):
        self._ws = ws
        self._h = ws.store

    def _op(self, fn):
        try:
            return (self._h.call(fn), None)
        except Exception as exc:  # noqa: BLE001 -- surface, never crash the shell
            return (None, str(exc))

    def _read(self, fn):
        if not self._h.ready():
            return (None, NO_STORE)
        return self._op(fn)

    def _write(self, fn):
        if not self._h.writable():
            return (None, NO_STORE)
        return self._op(fn)

    def _codec(self):
        """The cart-store module (`moy_carts`), whose codecs are pure
        functions, or None where no store is wired."""
        return self._ws.carts_store


class _Session(_Store):
    """The readiness pair and the session of the role an app walks the cart
    store with (`carts`; the files role's are C, the same rules)."""

    def readable(self, g):
        """A store exists to READ from."""
        return self._h.ready()

    def ready(self, g):
        """A store exists AND writes are enabled."""
        return self._h.writable()

    def begin(self, g):
        """Open a session: `(True, None)`, or `(None, NO_STORE)` with no
        writable store. It holds no gate; `end` closes it, and the console ends
        any left open at the frame's end and at a run's end."""
        if not self._h.writable():
            return (None, NO_STORE)
        self._h.begin()
        return (True, None)

    def end(self, g):
        self._h.end()


# -- the cart store ------------------------------------------------------------

def _catalogue():
    """The store's cart interface (`moy_catalogue`), imported at the call."""
    try:
        import moy_catalogue
    except ImportError:  # pragma: no cover - host package lane
        from runtime import moy_catalogue
    return moy_catalogue


class CartsServer(_Session):
    """The CART store -- projects, not documents: a cart is executable
    content, so authoring one is a role of its own. Storybook is the one
    shipped author; Paint and Files copy pictures into a project's images."""

    def all(self, g):
        """Every scanned cart (the FULL list, not the launcher run-grid)."""
        return self._ws.carts.all

    def can_journal(self, g):
        """True when there is a store to journal into (#111)."""
        return self._codec() is not None

    def slug(self, g, text):
        store = self._codec()
        return store.slug(text) if store is not None else text

    def create(self, g, title, src=None, type=None):
        """A new cart in the store: `(cart, err)`. The live list is not
        changed until `rescan`."""
        root = self._ws.carts_root
        return self._write(lambda: _catalogue().create(title, root, src=src,
                                                       type=type))

    def journal(self, g, cart, main, src, grad=0):
        """Append `src` to `cart`'s undo journal for its file `main` (#111),
        flipping its manifest's graduation to `grad`."""
        h = cart.get("h") if cart else None
        if not h:
            return (None, "NO CART")
        return self._write(lambda: _catalogue().journal_append(h, main, src,
                                                               grad=grad))

    def rescan(self, g):
        """Re-read the store and adopt it as the live cart list."""
        self._ws.carts.rescan()

    def hydrate(self, g, cart):
        """Load a slimmed cart's full payloads back IN PLACE (#66)."""
        return self._ws.carts.rehydrate(cart)

    def load_deck(self, g, cart):
        ws = self._ws
        return self._read(lambda: ws.carts_store.load_deck(cart))

    def save_deck(self, g, cart, blob):
        ws = self._ws
        return self._write(lambda: ws.carts_store.save_deck(cart, blob))

    def save_code(self, g, cart, src):
        ws = self._ws
        return self._write(lambda: ws.carts_store.save_code(cart, src))

    def images(self, g, cart):
        """The cart's image assets by name."""
        ws = self._ws
        path = cart.get("path") if isinstance(cart, dict) else cart
        return self._read(lambda: ws.carts_store.load_images(path))

    def save_image(self, g, cart, name, blob):
        ws = self._ws
        return self._write(lambda: ws.carts_store.save_image(cart, name, blob))

    def encode_image(self, g, w, h, indices):
        """The .moyimg encoder, mirrored from `files`: Storybook puts a
        painting on a page without the user-files store."""
        store = self._codec()
        return store.encode_moyimg(w, h, indices) if store is not None else None


# -- navigation ------------------------------------------------------------------

class NavServer:
    """Where the console goes next. `open_app` is the app-to-app seam,
    resolved by registered id, so an app never holds another app's class."""

    def __init__(self, ws):
        self.__ws = ws

    def open_app(self, g, app_id, cart=None):
        """Spawn the registered app `app_id`. False when this build does not
        carry it or no cart carries its identity."""
        app = self.__ws._apps_by_id.get(app_id)
        if app is None:
            return False
        return bool(self.__ws.open_app(app, cart))

    def is_system_app(self, g, cart):
        """True when a registered app's identity claims `cart` -- what keeps
        app carts out of project lists."""
        return self.__ws.is_system_app(cart)

    def projects(self, g):
        """The editable PROJECTS: every scanned cart a system app does not
        claim, the Editor's project-picker roster. Navigation and not `carts`:
        a list of places to GO is not permission to author one."""
        ws = self.__ws
        return [c for c in ws.carts.all if not ws.is_system_app(c)]

    def edit(self, g, cart, tab=None):
        """Open `cart` in the project EDITOR, optionally on one tab. False when
        there is nothing to edit. Leaving the Editor comes back to the app
        that opened it (`Workstation._go_home_or_back`)."""
        if cart is None:
            return False
        ws = self.__ws
        ws._note_app_caller()
        ws.open_in_editor(cart)
        if ws.project is None or ws.project.cart is not cart:
            return False
        if tab:
            ws.set_menu_view(tab)
        return True

    def open_image(self, g, name, kind=None, cart=None):
        """Open a PICTURE in Paint: a gallery drawing, or a cart's OWN image
        (`cart` given). False when the build carries no Paint."""
        return self.__ws.open_image(name, kind, cart)

    def open_text(self, g, name, kind=None, mode=None):
        """Open a user-files TEXT document in the console's text app, in
        `mode`. False when the build carries no text app."""
        return self.__ws.open_text_cart(name, kind, mode)

    def edit_file(self, g, cart, name, mode=None):
        """Open one of `cart`'s OWN files in the editor, through that
        project's Editor, so the return re-reads the folder."""
        if cart is None:
            return False
        ws = self.__ws
        ws._note_app_caller()
        return bool(ws.open_project_file(cart, name, mode))

    def play(self, g, cart):
        """Open `cart` as a workspace and RUN it, returning to the calling
        grant's app on exit (home when the grant is no registered app)."""
        ws = self.__ws
        caller = ws._apps_by_id.get(ws.app_abi.grant_id(g))
        if caller is None:
            caller = ws.launcher_layer
        ws._open_workspace(cart)
        ws.run(ws.project, caller)

    def run_script(self, g, kind, name):
        """RUN a vault SCRIPT (a bare `.py`/`.lua` file): `(ok, why)`."""
        return self.__ws.run_script(kind, name)

    def text_mode(self, g, on):
        """Flip the keyboard between typing and game mode, for an app that
        changes mode mid-session (Storybook's page editor)."""
        self.__ws._set_text_mode(bool(on))


# -- notifications ---------------------------------------------------------------

class NotifyServer:
    """Achievement events (#21)."""

    def __init__(self, ws):
        self.__ws = ws

    def achieve(self, g, kind, key=None):
        """Note an achievement event. Silent when the build carries none."""
        ach = getattr(self.__ws, "ach", None)
        if ach is not None:
            ach.note(kind, key)


# -- the desktop backdrop ----------------------------------------------------------

class WallpaperServer(_Store):
    """The desktop backdrop (#28): the look's choice, the wallpaper carts and
    the built-in fills. Its backing copy (`artwork.moyimg`, which Paint's and
    Files' WALL write) is the role's C rows; the copy's decoded picture is
    the backdrop's own (Paint's model draws My Art from it), keyed on the copy's
    generation (`app.copy_gen`), and the thumbnail is that same decode's."""

    def current(self, g):
        """The active wallpaper id (a cart slug or `fill:<color>`): the
        look's, which a boot fallback may hold apart from the settings row."""
        return self._ws.look.wallpaper_id

    def carts(self, g):
        """The wallpaper-type carts available as backdrops."""
        return self._ws.look.wallpaper_carts()

    def fills(self, g):
        """The built-in solid fills, always present."""
        return self._ws.look.FILL_WALLPAPERS

    def id_for(self, g, cart):
        return self._ws.look.wp_id_for(cart)

    def title(self, g, wp_id):
        """The title of wallpaper `wp_id`'s cart, or None (a fill, or none)."""
        cart = self._ws.look.wp_cart_by_id(wp_id)
        return cart.get("title") if cart else None

    def select(self, g, wp_id):
        self._ws.look.select_wallpaper(wp_id)

    def preview(self, g, cv, rect, dt):
        """Composite the live backdrop into `rect` (the Appearance preview)."""
        self._ws.wallpaper.draw_preview(cv, rect, dt)

    def thumbnail(self, g, w, h):
        """My Art's copy as a `w` x `h` bitmap, or None: the backdrop's
        decode, scaled once per size."""
        art = getattr(self._ws, "artwork", None)
        return art.thumbnail(w, h) if art is not None else None


# -- the cart installer --------------------------------------------------------------

class InstallServer:
    """Carts from outside (#124): the network a store fetches with, the radio
    lease it fetches under, the store it installs into and the engine's
    sizing. `runtime/cart_index.py` does the installing; Get Carts drives it.
    Its verbs raise: the installer turns every failure into a screen of its
    own. Never a cart's (no permission maps to it)."""

    def __init__(self, ws):
        self.__ws = ws
        self.__store = ws.store

    def net(self, g):
        """The transport (`online()`, `open(url)`), or None."""
        return self.__ws.cart_net

    def home(self, g):
        """Where this console's carts are got instead, when not here: "board"
        on a page a console serves, "headless" on one a console with no screen
        serves, else None."""
        return self.__ws.cart_home

    def keep(self, g):
        """The store of record's keeper where the files an install writes are
        not it (the browser's OPFS), else None."""
        return self.__ws.cart_keep

    def can_pick(self, g):
        """True where the player can hand this console a file of their own."""
        return self.__ws.cart_pick is not None

    def pick(self, g, name, size, host):
        """Ask the player for their own copy of `name`: a handle whose
        `poll()` is None until they answer, then ("file", path) or ("cancel",);
        `close()` takes the question away. None where nothing can ask."""
        ask = self.__ws.cart_pick
        return None if ask is None else ask(name, size, host)

    def hold(self, g):
        """Take the radio under the "carts" lease. True when it came up."""
        return self.__ws.wifi_hold("carts")

    def release(self, g):
        self.__ws.wifi_release("carts")

    def root(self, g):
        return self.__ws.carts_root

    def writable(self, g):
        return self.__store.writable()

    def op(self, g, fn):
        """`fn()` as ONE store op, inside the bus gate (on the T-Deck the card
        shares the panel's SPI host): the installer's unit of work -- a buffer
        written, a file ended, the staging swapped -- never a whole install."""
        return self.__store.call(fn)

    def rescan(self, g):
        """Re-read the store, so the shelf shows what came and went."""
        self.__ws.carts.rescan()

    def free(self, g):
        """`(free bytes, block size)` of the store the carts live on, or None:
        the keeper's answer where there is one."""
        keep = self.__ws.cart_keep
        if keep is not None:
            return keep.free()
        try:
            import os
            st = self.__store.call(lambda: os.statvfs(self.__ws.carts_root))
        except (ImportError, OSError, AttributeError, TypeError):
            return None
        return st[0] * st[4], st[0]

    def find(self, g, folder):
        """The scanned cart whose `.moy` folder is `folder`, or None."""
        tail = "/" + folder
        for c in self.__ws.carts.all:
            if str(c.get("path") or "").replace("\\", "/").endswith(tail):
                return c
        return None

    def runtimes(self, g):
        """The cart runtimes this build carries ("lua", "wasm")."""
        return tuple(self.__ws.runtimes)

    def fit(self, g, runtime, pages, module_len, interp):
        """`(need, have)` for a cart of `runtime` declaring `pages` of memory
        whose module is `module_len` bytes, by the engine's own sizing, or
        None when the runtime cannot say."""
        rt = self.__ws.runtimes.get(runtime)
        of = getattr(rt, "footprint_of", None)
        mem = getattr(rt, "memory", None)
        if of is None or mem is None or not pages:
            return None
        try:
            need = of(pages, module_len, interp)
            return None if need is None else (need, mem())
        except Exception:  # noqa: BLE001 -- a report is advisory
            return None

    def memory(self, g):
        """`(free, largest block)` of the memory a compiled cart loads into,
        or None where no engine says."""
        mem = getattr(self.__ws.runtimes.get("wasm"), "memory", None)
        if mem is None:
            return None
        try:
            return mem()
        except Exception:  # noqa: BLE001
            return None

    def chip(self, g):
        """`(chip, compiled-code format)` of this console's compiled tier, or
        `(None, None)` where there is none (the host)."""
        try:
            import moy_wasm
            return moy_wasm.CHIP, moy_wasm.FORMAT
        except (ImportError, AttributeError):
            return None, None


# -- the ROLE door ----------------------------------------------------------------

# moy_app.h's codes the door answers with, negated.
_DOOR_ABSENT, _DOOR_BAD = -8, -7

# The shell rows whose arguments and answers are numbers and text, by the
# arguments each takes in the door's fields: `s` text, `o` text where empty is
# None, `i` a number, `c` a cart named by its folder. A shell row not here (an
# object, a callable, the cart store's and the installer's) answers BAD.
DOOR_ARGS = {
    "theme.set": "so", "theme.set_variant": "s", "theme.set_skin": "s",
    "nav.open_app": "s", "nav.is_system_app": "c", "nav.projects": "",
    "nav.edit": "co", "nav.open_image": "so", "nav.open_text": "soo",
    "nav.edit_file": "cso", "nav.play": "c", "nav.run_script": "ss",
    "nav.text_mode": "i",
    "notify.achieve": "so",
    "wallpaper.current": "", "wallpaper.carts": "", "wallpaper.fills": "",
    "wallpaper.id_for": "c", "wallpaper.title": "s", "wallpaper.select": "s",
}


def _fields(b):
    """The packed fields: each a little-endian uint32 length and its bytes."""
    out, at = [], 0
    while at < len(b):
        if len(b) - at < 4:
            raise ValueError("a torn field")
        n = b[at] | b[at + 1] << 8 | b[at + 2] << 16 | b[at + 3] << 24
        at += 4
        if n > len(b) - at:
            raise ValueError("a torn field")
        out.append(bytes(b[at:at + n]))
        at += n
    return out


def _folder(cart):
    """A cart as the door names it: its folder, or a built-in's title."""
    path = cart.get("path")
    if not path:
        return cart.get("title") or ""
    path = path.rstrip("/")
    return path[path.rfind("/") + 1:]


def _answer(v):
    """A server's answer as the door hands it back: a number as it is, text
    and bytes as they are, a cart as its folder, a list as its items each
    NUL-terminated."""
    if v is None or isinstance(v, (bool, int, str, bytes)):
        return v
    if isinstance(v, dict):
        return _folder(v)
    out = []
    for x in v:
        out.append((_folder(x) if isinstance(x, dict) else str(x)) + "\0")
    return "".join(out)


class RoleDoor:
    """The ROLE upcall's Python half: `door(grant, row, args)` for table row
    `row` (native/moy_app/roles.json's index), registered with the kernel's
    loop (`moy_loop.role`). C has checked the grant holds the row's role."""

    def __init__(self, ws, servers, table):
        self._ws = ws
        self._servers = servers
        self._table = table

    def _cart(self, folder):
        for c in self._ws.carts.all:
            if _folder(c) == folder:
                return c
        return None

    def __call__(self, g, row, args):
        name = self._table[row] if 0 <= row < len(self._table) else None
        kinds = DOOR_ARGS.get(name)
        if kinds is None:
            return _DOOR_BAD
        try:
            fields = _fields(args)
        except ValueError:
            return _DOOR_BAD
        if len(fields) != len(kinds):
            return _DOOR_BAD
        call = []
        for k, f in zip(kinds, fields):
            if k == "i":
                if len(f) != 4:
                    return _DOOR_BAD
                v = f[0] | f[1] << 8 | f[2] << 16 | f[3] << 24
                call.append(v - (1 << 32) if v & 0x80000000 else v)
                continue
            text = f.decode("utf-8")
            if k == "c":
                cart = self._cart(text)
                if cart is None:
                    return _DOOR_ABSENT
                call.append(cart)
            else:
                call.append(None if k == "o" and not text else text)
        role, verb = name.split(".")
        return _answer(getattr(self._servers[role], verb)(g, *call))


def _door_open(ws, servers):
    """Register the door: the loop's dispatcher, and the shell rows' door
    in moy_app (where this tier carries both)."""
    try:
        import moy_app
        try:
            import moy_loop
        except ImportError:  # pragma: no cover - host package lane
            from runtime import moy_loop
        moy_loop.role(RoleDoor(ws, servers, moy_app.table()))
    except ImportError:  # a host with no C compiler: no loop, no door
        return
    moy_app.door_bind(True)


def serve_all(ws):
    """Register every server on the console's app ABI (`ws.app_abi`), and
    the ROLE door a compiled app reaches them through."""
    app = ws.app_abi
    servers = {
        "surface": SurfaceServer(ws), "theme": ThemeServer(ws),
        "carts": CartsServer(ws), "nav": NavServer(ws),
        "notify": NotifyServer(ws), "wallpaper": WallpaperServer(ws),
        "install": InstallServer(ws),
    }
    for role, server in servers.items():
        app.serve(role, server)
    _door_open(ws, servers)
