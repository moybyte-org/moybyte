# Map (grep -n a name to jump there):
#   Appearance                       `ws.look`: theme, variant, font scale, wallpaper, icons, layout
#   -- the wallpaper: the theme's    theme_wallpaper, the picture, the coordinator
#   -- custom: a change              change / tick / commit, the one write path
#   -- the top-bar icon sheet        the bar's IconSheet
#   Appearance.set_font_scale        the system font scale, relayout
#   Appearance.set_chrome_scale      the chrome scale above the tap floor, relayout
#   Appearance.set_theme             the panel theme: tokens, shape, fonts, layout
#   -- the shell layout              the bars in force: the pick, resolve, place
#   Appearance.apply_layout          resolve and move the host's bar
#   -- the per-draw gate             what a draw reads
"""The LOOK (#209 landing D) -- `Workstation.look`.

Every verb that changes how the desktop looks: the panel theme and its
dark/light variant, the system font scale, the desktop wallpaper and the
top-bar icon sheet. Two axes plus a backdrop plus the bar art, which until this landing were five unrelated stretches of the kernel that
happened to share a Settings screen. And `custom`, the theme a change makes
(`change`, docs/theming_2026-09.md §9 phase 6 step 5): the one write path of
the Appearance rows, applied once a frame (`tick`) and written once the
changes settle or at an exit (`commit`), through Paint's mechanism (#108).

Not to be confused with `appearance_app.py`, which is the PICKER a kid taps --
that app keeps its name and its place in the app registry, and reaches these
verbs through `ctx.theme` / `ctx.wallpaper` like any other app.

## The wallpaper is the theme's, shown by a coordinator

The wallpaper is a key of the active theme (`wallpapers` names a cart,
`images` a picture in its folder; docs/theming_2026-09.md D6), so a theme
switch brings it and a pick is a change to the theme, landing in `custom`;
there is no setting beside it. Showing one crosses four owners: it asks the
ROSTER for the wallpaper carts and rehydrates the chosen one out of the #66
live-set diet (`ws.carts`), drives the backdrop COMPONENT's compile
(`ws.wallpaper`), and a pick notes an achievement (`ws.ach`). It lives here as
a COORDINATOR: it calls the other objects and owns none of them, which is what
"the look" is -- policy over components, not a component.

## The tokens stay on the kernel, and they are written from here

`ws.theme_colors` is the one piece of this cluster's state that did NOT move.
It is read per DRAW by ~70 sites across twenty surface modules (usually hoisted
once per draw method as `th = ws.theme_colors`), and doc 3e's temperature table
pins that row as "token reads stay flat; the cluster owns the verbs". So the
dict stays a plain kernel attribute with ONE author -- `set_theme` below is the
only thing in the tree that ever writes it -- which is the same shape rev 3
gave the achievement overlays: the collaborator generates the value, the kernel
holds the flat field the frame path reads.

## A switch is one dirty plus one integer

`look_gen` is the look's generation: a plain small int, set by `publish()`
from native/moy_app's token-table generation, which every write of the live
look moves (a switch, a variant, an install, an icon sheet). Every cache whose
pixels read the look folds it into its key and REPLACES its entry when it
moves (docs/theming_2026-09.md §6); none keys on a dict's or a sheet's
identity. The token dicts are shared and read-only: `chrome.theme_colors`
hands out one per (theme, variant) until an install drops them.

## What stays on the kernel, and why

`_relayout` (the font-scale verb's cascade) is the shell's whole responsive
re-derivation -- eight layout heads, every registered app, the WM hook. It is
kernel policy that a dozen things besides a font change would call, so
`set_font_scale` reaches back for it rather than owning it.

`_bar_img_cache` stays kernel too: it backs `ws._bar_image` / `ws._icon`, which
are the shared DRAW TOOLKIT (doc 4 keeps that on the kernel by decision), and
bar_layer's own header already says so. `set_icon_sheet` clears it, the one
kernel field this object writes besides the tokens.

`_fat_cart` is the OPEN WORKSPACE's cart, not a look setting; `_show_wallpaper`
reads it through `ws` to decide whether a compiled wallpaper cart may be
re-slimmed.

## Reading late-injected things through `ws`, per call

Same rule the other four collaborators follow (doc 3c). This object is built in
`Workstation.__init__`, before the store is wired, before `system.json` is
read, before the layer stack exists -- so `carts_store`, `carts_root`,
`_with_sd`, `launcher`, `projects_grid` and `bar_layer` are all reached through `ws`
at the moment of use and never captured here.
"""

try:
    from moy_store_base import BUILTIN_NS
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime.moy_store_base import BUILTIN_NS

try:
    from chrome import (_default_icon_sheet, _err_text, _ICON_VERSION,
                        DEFAULT_THEME, DEFAULT_VARIANT, forget_themes,
                        theme_colors, theme_content, theme_known, THEME_NAMES,
                        THEME_VARIANTS, text_scale_floor, adopt_packages,
                        package, theme_names, forget_package)
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime.chrome import (_default_icon_sheet, _err_text, _ICON_VERSION,
                                DEFAULT_THEME, DEFAULT_VARIANT, forget_themes,
                                theme_colors, theme_content, theme_known,
                                THEME_NAMES, THEME_VARIANTS, text_scale_floor,
                                adopt_packages, package, theme_names,
                                forget_package)
try:
    from theme_custom import (Custom, CUSTOM, DROP, MANIFEST, PICTURE_FILE,
                              font_files, keys_of, manifest, overlay,
                              read_look, wall_keys)
    from moy_store_base import cart_path
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime.theme_custom import (Custom, CUSTOM, DROP, MANIFEST,
                                      PICTURE_FILE, font_files, keys_of,
                                      manifest, overlay, read_look, wall_keys)
    from runtime.moy_store_base import cart_path
try:
    from editors_sheet import IconSheet
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime.editors_sheet import IconSheet
try:
    import ui as _uimod
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime import ui as _uimod
try:
    import shell_layout as _shell_layout
    import layout_base as _layout_base
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime import shell_layout as _shell_layout
    from runtime import layout_base as _layout_base
_ui_is_light = _uimod.is_light

def _ui_text_w(t):
    return _uimod.text_width(t, 1, "ui")


_FONT_ROLES = ("ui", "title", "mono")
_FONT_FULL = 6          # moy_font_load's MOY_FONT_FULL: every slot holds a font
_FONT_BYTES = 8192      # moy_text.h's MOY_FONT_BYTES: the largest font file
_THEME_BYTES = 4096     # style.json's theme.bytes: the largest theme.json
_THEME_MAIN = "theme.json"
_PICTURE_BYTES = 262144  # a theme's picture: a 512x300 moyimg with room to spare
_CHANGES = ("accent", "tint", "wallpaper", "frame", "font")
# The loader's refusals (moy_theme.h), by rc from 1: each one's string.
_THEME_WHY = ("big", "syntax", "newer", "name", "taken", "chain", "nomem")
# The theme fonts loaded, (package, file) -> id, the package None for a
# seed's file: one table for the process, as the loader's four slots are.
_FONTS = {}


class Appearance:
    """The look: theme, variant, font scale, wallpaper, icon sheet.

    Plain methods, no properties (a property forward measured +5.1us against a
    plain hop's +0.5us on this codebase). Nothing here runs per frame -- the
    per-draw surface is `light_chrome()` and the `ws.theme_colors` dict it
    reads, both of which stay one cheap call and one flat attribute."""

    # -- the wallpaper: the theme's (D6, docs/theming_2026-09.md §4.9) --------
    #
    # The desk and the home draw a wallpaper-type cart as a live backdrop (the
    # Picotron model: a wallpaper is a fullscreen cart, compiled into its own
    # namespace), the theme's picture, or the theme's `desktop` fill. Which one
    # is the active theme's: its `wallpapers` names a cart, its `images` a
    # picture in its folder. A pick is a change to the theme (`change`), so it
    # lands in `custom` and travels with it.

    # The wallpaper id of the theme's picture: the My Art entry, which stands
    # for Paint's picture (the wallpaper copy) wherever wallpapers are picked.
    PICTURE = BUILTIN_NS + ".my_art"

    # Paint's idle debounce (artwork.PaintAppLayer.AUTOSAVE_S, #108): `custom`
    # is written once the changes have settled this long; a failing store
    # retries slowly.
    AUTOSAVE_S = 2.5
    RETRY_S = 30.0
    # change()'s refusal when a file the change needs cannot be read (Paint's
    # picture before a save, a font no package carries).
    NO_FILE = -1

    # -- system font scale (#39) ---------------------------------------------
    #
    # The system-UI font is settings-resizable (petme128 nearest-neighbor x1/x2/x3),
    # persisted in system.json and applied live.
    # The GAME canvas keeps plain 8x8 text regardless -- scaling lives in the system
    # canvas + the responsive Layout, so a cart is never affected.

    FONT_SCALES = (1, 2, 3)

    def __init__(self, ws, font_scale=1):
        self.ws = ws
        # `font_scale` is the REQUESTED system-UI scale (persisted). It only takes
        # visible effect on a distinct SYSTEM canvas that can render scaled text; in
        # the degradation case (no system canvas -- e.g. the T-Deck, whose framebuf
        # text can't scale) the effective scale is 1, so the chrome layout matches the
        # 8px text actually drawn. The requested value is still kept + persisted, so a
        # bigger panel later honours it.
        self.font_scale = max(1, int(font_scale))
        # The text-size floor (chrome.text_scale_floor): the font scale the
        # theme's `ui` font needs on this board's glass, 1 for petme128. It
        # lifts the effective scale, never the requested one.
        self.text_floor = 1
        # The REQUESTED chrome scale (Settings -> CHROME SIZE, persisted), one
        # of `ws.chrome_scales` or 0 for the board's default. It lifts chrome
        # above the tap floor; it can never take it below.
        self.chrome_scale = 0
        # Panel THEME (Appearance -> THEMES): the chrome token set the panels/window
        # chrome/selection accents read each draw. Default = the moybyte "night"
        # colorway (today's exact colors); load_system applies the persisted pick.
        self.theme_name = DEFAULT_THEME
        self.theme_variant = DEFAULT_VARIANT
        # The live token dict lives on the KERNEL, not here -- see the module
        # docstring. `set_theme` is its only author; this is the first write.
        ws.theme_colors = theme_colors(DEFAULT_THEME)
        # The wallpaper in force: a wallpaper cart's id, PICTURE, or None for
        # the theme's `desktop` fill -- the theme's (`_apply_wallpaper`),
        # shown by `_show_wallpaper`. A picture is named (theme, epoch), the
        # epoch moving when a theme's picture may have; `_pic_key` is the one
        # shown, `_pic_last` the last one shown, and `picture_gen` moves when
        # a picture other than that one is shown.
        self.wallpaper_id = None
        self.picture_gen = 0
        self._pic_key = None
        self._pic_last = None
        self._pic_epoch = 0
        self._wall_force = False
        # `custom` while it is the theme being changed (theme_custom.Custom),
        # whether its last change is unwritten, and the debounce's clock.
        self._cust = None
        self._unsaved = False
        self._idle = 0.0
        # Unified top bar (Stage 1): the editable 16x16 IconSheet the bar draws its
        # chrome icons from. Loaded by `load_icon_sheet` at the end of the boot wiring
        # (from system_icons.moygfx, else the baked default theme); None falls back to
        # the kernel's _glyph.
        self.icon_sheet = None
        # The shell layout (docs/shell_layout_2026-10.md §9-§11): the bars as
        # this tier, glass and mode resolved them, what the composer noted,
        # and the loader's answers per package (cleared by a theme switch).
        self.bars = _shell_layout.FALLBACK
        self.layout_notes = ()
        self.home = "carousel"          # the home's arrangement the layout names
        self._layouts = {}
        self._choices = None
        self.layout_gen = 0             # moves with every resolve: the Settings row's memo
        self.look_gen = 0               # moves with every publish(); see the module doc
        # The store's theme packages (docs/theming_2026-09.md §9 phase 6 step
        # 2): stem -> folder, as of the roster generation `_pkg_gen`; the
        # bytes the install slot holds when a store package filled it (None
        # for an install from bytes, `install_theme`); the packages whose
        # refusal was shown.
        self._pkg_paths = {}
        self._pkg_gen = None
        self._slot_src = None
        self._slot_gen = None
        self._refused = set()
        self.theme_note = None          # the last refusal: (name, rc)

    # -- wallpaper discovery -------------------------------------------------

    def wallpaper_carts(self):
        """The wallpaper-type carts available as backdrops (discovery by type, Moybyte's
        equivalent of Picotron's wallpapers folder). Reads the FULL scanned list, not the
        launcher grid -- wallpapers are a backdrop category chosen in the Appearance app,
        so they leave the launcher RUN-grid (spec shell_ux_v1.md) but stay discoverable
        here."""
        return [c for c in self.ws.carts.all if c.get("type") == "wallpaper"]

    def wallpaper_options(self):
        """Every wallpaper cart's id (My Art's is PICTURE). NONE, the
        theme's `desktop` fill, is None."""
        return [self.wp_id_for(c) for c in self.wallpaper_carts()]

    def wp_id_for(self, cart):
        # A stable id for a wallpaper cart: its folder name without the
        # extension, so the choice survives a reboot. A path-less cart is an
        # embedded built-in, named as its seed would be.
        path = cart.get("path")
        if path:
            name = path.rsplit("/", 1)[-1]
            return name[:-4] if name.endswith(".moy") else name
        store = self.ws.carts_store
        return (BUILTIN_NS + "." + store.slug(cart["title"]) if store
                else cart["title"])

    def wp_cart_by_id(self, wp_id):
        for c in self.wallpaper_carts():
            if self.wp_id_for(c) == wp_id:
                return c
        return None

    # -- the wallpaper CHOICE (the coordinator) ------------------------------

    def select_wallpaper(self, wp_id, persist=True):
        """Pick the desk's wallpaper: a wallpaper cart's id, PICTURE (Paint's
        picture, copied into the theme) or None (NONE: the theme's `desktop`
        fill). A pick is a change to the theme (`change`), shown at the next
        frame; persist=False only shows `wp_id` now, until the next theme
        switch (a bench, a test), an id no cart has showing the default. 0,
        or change()'s refusal."""
        if persist:
            return self.change("wallpaper", wp_id)
        if wp_id is not None and wp_id != self.PICTURE \
                and self.wp_cart_by_id(wp_id) is None:
            wp_id = self._default_wallpaper()
        self._show_wallpaper(wp_id)
        return 0

    def cycle_wallpaper(self, d):
        """Step the wallpaper by d through the carts and NONE (programmatic
        verb; the UI pick is the Appearance app): a change, like a pick. A
        step that is refused (My Art before Paint has a picture) is passed."""
        opts = self.wallpaper_options() + [None]
        at = opts.index(self.wallpaper_id) if self.wallpaper_id in opts else 0
        rc = self.NO_FILE
        for _ in range(len(opts) - 1):
            at = (at + d) % len(opts)
            rc = self.select_wallpaper(opts[at])
            if rc == 0:
                break
        return rc

    def _default_wallpaper(self):
        """The wallpaper of a theme that names none: the first wallpaper cart
        (Moy's night on a seeded store), else the fill."""
        for wp in self.wallpaper_options():
            if wp != self.PICTURE:
                return wp
        return None

    def theme_wallpaper(self):
        """The wallpaper the active theme gives: the cart its `wallpapers`
        names where it is installed, else its picture, else None where it
        names its wallpapers and no cart among them (NONE), else the
        default. A SAFE start runs no wallpaper cart."""
        cart, img, said = wall_keys(self._theme_bytes())
        safe = getattr(getattr(self.ws, "prefs", None), "safe", False)
        if cart is not None and cart != self.PICTURE and not safe \
                and self.wp_cart_by_id(cart) is not None:
            return cart
        if img is not None:
            return self.PICTURE
        if (said and cart is None) or safe:
            return None
        return self._default_wallpaper()

    def _theme_bytes(self):
        """The active theme's theme.json bytes where it is a package (the
        installed slot's, or custom's as changed), else None: a seed names
        no wallpaper."""
        if self.theme_name in THEME_NAMES:
            return None
        c = self._cust
        if c is not None and self.theme_name == CUSTOM:
            return c.theme
        if self._slot_src is not None:
            return self._slot_src
        return self._theme_src(self.theme_name)

    def picture(self):
        """The active theme's picture (the file its `images` names for the
        desk) as bytes, or None."""
        img = wall_keys(self._theme_bytes())[1]
        if img is None:
            return None
        got = self._package_file(self.theme_name, img, _PICTURE_BYTES)
        return got if got is not None and len(got) <= _PICTURE_BYTES else None

    def _apply_wallpaper(self):
        """Show the active theme's wallpaper (`theme_wallpaper`) when it is not
        what is shown -- a theme switch, a re-take -- and at a wallpaper pick
        always (a pick of a struck-out one is the kid's "try again", #160).
        A MODE switch keeps the same one and costs nothing."""
        if getattr(self.ws, "wallpaper", None) is None:
            return
        wp = self.theme_wallpaper()
        key = (self.theme_name, self._pic_epoch) if wp == self.PICTURE else None
        force, self._wall_force = self._wall_force, False
        if not force and wp == self.wallpaper_id and key == self._pic_key:
            return
        self._show_wallpaper(wp)

    def _show_wallpaper(self, wp):
        """Put wallpaper `wp` on the desk: compile its cart into the backdrop
        component, or the picture or the fill, which need none."""
        ws = self.ws
        self.wallpaper_id = wp
        if wp == self.PICTURE:
            key = (self.theme_name, self._pic_epoch)
            if key != self._pic_last:
                self._pic_last = key
                self.picture_gen += 1          # a picture the desk has not decoded
            self._pic_key = key
        else:
            self._pic_key = None
        ws.wallpaper.clear()
        if wp is not None and wp != self.PICTURE:
            cart = self.wp_cart_by_id(wp)
            if cart is not None:
                # #66 live-set diet: a slimmed wallpaper cart rehydrates for the
                # compile (which bakes src/sheet into the wallpaper's own ns), then
                # re-slims -- unless it IS the open project's cart (stays fat).
                ws.carts.rehydrate(cart)
                ws.wallpaper.compile(cart)   # compile into the backdrop component (#28)
                if cart is not getattr(ws, "_fat_cart", None):
                    ws.carts.reslim(cart)
        ws._dirty = True

    # -- custom: a change to the theme (docs/theming_2026-09.md §9 phase 6
    #    step 5, §4.10) -----------------------------------------------------

    def change(self, kind, value):
        """Change the theme, the one write path of every Appearance row but
        THEME, MODE and CHROME SIZE. `kind` and `value`:

          "accent"     an accent's A (`ui.theme_accents`), None the source's own
          "tint"       a tint's name (`ui.theme_tints`), None for none
          "wallpaper"  a wallpaper cart's id, PICTURE (Paint's picture,
                       copied into the package) or None (NONE)
          "frame"      a `frame` value, None the source's
          "font"       a `fonts` value (role -> file), None the source's;
                       the files it names are copied in

        On a seed or an installed package the first change makes `custom`
        from it (any `custom` before replaced) and switches to it; on
        `custom` it changes it in place, re-derived from its source, never
        from its last result. The look moves at the next frame (`tick`),
        once however many changes came, and the package is written once the
        changes settle (AUTOSAVE_S) or at an exit (`commit`). 0, else the
        loader's refusal or NO_FILE, the theme as it was."""
        if kind not in _CHANGES:
            raise ValueError(kind)
        c = self._cust
        if c is None:
            c = self._load_custom() if self.theme_name == CUSTOM \
                else Custom({"from": self.theme_name}, fresh=True)
            if c is None:
                return self.NO_FILE
        look, over = dict(c.look), dict(c.over)
        pic = None
        if kind == "accent" or kind == "tint":
            if value is None:
                look.pop(kind, None)
            else:
                look[kind] = value
        elif kind == "frame" or kind == "font":
            key = "frame" if kind == "frame" else "fonts"
            if value is None:
                over.pop(key, None)
            else:
                over[key] = value
        elif value == self.PICTURE:
            pic = self._paint_picture()
            if pic is None:
                return self.NO_FILE
            over["images"] = {"desk": PICTURE_FILE}
            over["wallpapers"] = DROP
        else:
            over["wallpapers"] = {"desk": value} if value is not None else {}
            over["images"] = DROP
        rc, theme = self._derive(c, look, over)
        if rc != 0:
            return rc
        files = None
        if kind in ("wallpaper", "font") or c.theme is None:
            files = self._gather(c, look, theme, pic)
            if files is None:
                return self.NO_FILE
        if self._cust is not c:
            self._adopt_custom(c)
        c.look, c.over, c.theme = look, over, theme
        if files is not None:
            need, fetched = files
            for n in c.files:
                if n not in need:
                    c.drop(n)
            for n in fetched:
                c.put(n, fetched[n])
            c.files = need
        c.dirty = True
        c.font_moved = c.font_moved or kind == "font"
        c.wall_moved = c.wall_moved or kind == "wallpaper"
        self._unsaved = True
        self._idle = 0.0
        if kind == "wallpaper":
            ws = self.ws
            if value is not None and value != self.PICTURE:
                # A deliberate pick is the kid's "try again" (#160): a
                # struck-out wallpaper gets a fresh three strikes.
                guard = getattr(ws, "wallpaper_guard", None)
                if guard is not None:
                    guard.forgive(value)
            ach = getattr(ws, "ach", None)
            if ach is not None:
                ach.note("wallpaper_change")   # "Home Decorator" (#21)
        return 0

    def custom_look(self):
        """custom's `look` ({"from", "accent", "tint"}, each absent for the
        source's own) while `custom` is the active theme, else None: what
        the ACCENT and TINT rows show."""
        if self.theme_name != CUSTOM:
            return None
        c = self._cust
        if c is None:
            c = self._load_custom()
            if c is None:
                return {}
            self._adopt_custom(c)
        return dict(c.look)

    def tick(self, dt):
        """Once per frame, before the redraw gate (`Workstation.frame`): apply
        the latest change -- one install from bytes however many came since
        the last frame -- and write `custom` once the changes have settled
        AUTOSAVE_S. A frame with nothing changed costs one attribute read."""
        c = self._cust
        if c is None:
            return
        if c.dirty:
            self._apply_custom(c)
        if self._unsaved:
            self._idle += dt
            if self._idle >= self.AUTOSAVE_S:
                self.commit()

    def commit(self):
        """Write `custom` now if a change is unwritten: the debounce's write
        and every exit path's (going home, an app switch, a theme switch),
        so a change is never lost to an exit. The package's folder, then the
        roster (once, when the folder is new) and the `theme` row. False
        when the store refused; the change stays unwritten."""
        c = self._cust
        if c is None or not self._unsaved:
            return True
        ws = self.ws
        store = getattr(ws, "store", None)
        save = getattr(ws.carts_store, "save_package", None)
        if store is None or save is None or not store.writable():
            self._unsaved = False              # a store-less console keeps it live
            return True
        path = self._custom_path()
        files = dict(c.pending)
        files[_THEME_MAIN] = c.theme
        files[MANIFEST] = manifest(c.look)
        stale = tuple(c.stale)
        fresh = c.fresh
        try:
            store.call(lambda: save(path, files, stale, fresh))
        except Exception as exc:  # noqa: BLE001 -- a failed write is retried
            print("Moybyte custom save failed:", _err_text(exc))
            self._idle = self.AUTOSAVE_S - self.RETRY_S
            return False
        c.pending = {}
        c.stale = set()
        c.fresh = False
        self._unsaved = False
        self._idle = 0.0
        if fresh or self._pkg_paths.get(CUSTOM) is None \
                or not self._in_roster(path):
            ws.carts.adopt(path)
        rows = ws.system
        if (self.theme_name == CUSTOM or c.dirty) and (
                rows.get("theme") != CUSTOM
                or rows.get("theme_variant") != self.theme_variant):
            rows.set("theme", CUSTOM, persist=False)
            rows.set("theme_variant", self.theme_variant)
        return True

    def _custom_path(self):
        return cart_path(self.ws.carts_root, CUSTOM + ".moy")

    def _in_roster(self, path):
        for cart in self.ws.carts.all:
            if cart.get("path") == path:
                return True
        return False

    def _source(self, name):
        """What `ui.theme_custom` derives from for source `name`: a seed's
        name, a store package's bytes, or None."""
        if name in THEME_NAMES:
            return name
        if name != CUSTOM and name in self._packages():
            return self._theme_src(name)
        return None

    def _derive(self, c, look, over):
        """(rc, bytes): custom's theme.json for `look` and `over`, from the
        source `look` names (else c.src, the file as it was saved)."""
        src = self._source(look["from"]) if "from" in look else c.src
        if src is None:
            return 6, None                     # the loader's CHAIN: no source
        rc, b = _uimod.theme_custom(src, look.get("accent"), look.get("tint"))
        if rc != 0:
            return rc, None
        b = overlay(b, over)
        if len(b) > _THEME_BYTES:
            return 1, None                     # BIG
        rc = _uimod.theme_resolve(b)[0]
        return rc, (b if rc == 0 else None)

    def _gather(self, c, look, theme, pic):
        """(need, fetched): the files custom's `theme` names (its fonts with
        their licences, its picture), and those of them it does not carry
        yet, read from where they come from -- `pic` for the picture a pick
        copies, a seed's from the image, else the source package's folder.
        None when a font or the picture cannot be read."""
        keys = keys_of(theme)
        names = font_files(keys["fonts"] if keys["fonts"] is not DROP else None)
        img = wall_keys(theme)[1]
        if img is not None:
            names.append(img)
        need = set()
        fetched = {}
        for n in names:
            if n == img and pic is not None:
                fetched[n] = pic
                need.add(n)
                continue
            if n in c.files:
                need.add(n)
                continue
            data = self._fetch(look, n)
            if data is None:
                if n.endswith(".txt"):
                    continue                   # a font with no licence beside it
                return None
            fetched[n] = data
            need.add(n)
        return need, fetched

    def _fetch(self, look, name):
        data = _uimod.font_file(name)
        if data is None and look.get("from") not in (None, CUSTOM) \
                and look.get("from") not in THEME_NAMES:
            data = self._package_file(look["from"], name, _PICTURE_BYTES)
        return data if data is None or len(data) <= _PICTURE_BYTES else None

    def _paint_picture(self):
        """Paint's picture, the wallpaper copy WALL wrote (stamped with where
        it came from), as bytes; None before there is one."""
        ws = self.ws
        load = getattr(ws.carts_store, "load_copy", None)
        store = getattr(ws, "store", None)
        if load is None or store is None or not store.ready():
            return None
        try:
            blob = store.call(lambda: load(ws.carts_root))
        except Exception:  # noqa: BLE001 -- an unreadable copy is no picture
            return None
        if isinstance(blob, str):
            blob = blob.encode()
        return blob or None

    def _load_custom(self):
        """custom as the store holds it: its bytes, its manifest's `look`, and
        what its keys set over its source's. A `from` that no longer resolves
        is dropped: the file as saved is the source from then on. None when
        its theme.json cannot be read."""
        self._packages()
        src = self._theme_src(CUSTOM)
        if src is None or len(src) > _THEME_BYTES:
            return None
        look = read_look(self._package_file(CUSTOM, MANIFEST, _THEME_BYTES))
        c = Custom(look)
        c.theme = src
        mine = keys_of(src)
        files = font_files(mine["fonts"] if mine["fonts"] is not DROP else None)
        img = wall_keys(src)[1]
        if img is not None:
            files.append(img)
        c.files = set(files)
        base = None
        if "from" in look:
            frm = self._source(look["from"])
            if frm is not None:
                rc, base = _uimod.theme_custom(frm, None, None)
                if rc != 0:
                    base = None
        if base is None:
            look.pop("from", None)
            look.pop("accent", None)
            look.pop("tint", None)
            c.src = src
            return c
        theirs = keys_of(base)
        for k in mine:
            if mine[k] != theirs[k]:
                c.over[k] = mine[k]
        return c

    def _adopt_custom(self, c):
        """Make `c` the custom being changed: a new one drops the fonts the
        one before it loaded, and the store's packages are read again with
        it among them (it reads from memory until it is written)."""
        if c.fresh:
            self._pic_epoch += 1               # a new custom, maybe a new picture
            for key in list(_FONTS):
                if key[0] == CUSTOM:
                    _uimod.font_free(_FONTS.pop(key))
        self._cust = c
        self._pkg_gen = None
        self._packages()

    def _apply_custom(self, c):
        """Install custom's bytes and put the look on them: the full switch
        the first time and on a FONT change (fonts, relayout, the layout),
        else the shape and the tokens alone."""
        c.dirty = False
        if _uimod.theme_install(c.theme)[0] != 0:
            return
        self._slot_src = c.theme
        self._slot_gen = self._pkg_gen
        forget_package(CUSTOM)
        if c.wall_moved:
            c.wall_moved = False
            self._pic_epoch += 1
            self._wall_force = True
        if self.theme_name != CUSTOM or c.font_moved:
            c.font_moved = False
            self.set_theme(CUSTOM, persist=False)
            return
        ws = self.ws
        _uimod.use_shape(CUSTOM)
        ws.theme_colors = theme_colors(CUSTOM, self.theme_variant)
        self.publish()
        ws.launcher.theme = ws.theme_colors
        if getattr(ws, "projects_grid", None) is not None:
            ws.projects_grid.theme = ws.theme_colors
        ws._dirty = True
        self._apply_wallpaper()

    # -- the top-bar icon sheet (#52) ----------------------------------------

    def set_icon_sheet(self, sheet):
        """Adopt the top-bar IconSheet (Stage 1) and drop the per-kind image cache so
        the next frame rebuilds its sprites (and, on the device, their RGB565 copies)
        from the new theme. None reverts the bar to the _glyph fallback."""
        self.icon_sheet = sheet
        # The kernel's cache, cleared from here: it backs ws._bar_image/_icon, the
        # shared draw toolkit, and a stale entry would keep blitting old pixels.
        self.ws._bar_img_cache = {}
        self.publish()
        self.ws._dirty = True

    def load_icon_sheet(self):
        """Build the top-bar IconSheet (Stage 1): use the saved system_icons.moygfx theme
        only if its stored version is >= the baked _ICON_VERSION; otherwise bake the
        default theme. A saved theme older than _ICON_VERSION is STALE (the shipped
        icons changed) -> re-seed it: bake the new default and overwrite the saved theme
        + version, so an already-themed device/desktop picks up new icons automatically
        (mirrors cart versioning, #47). A missing theme stays write-free (the common
        "absent = default" case). Safe on an embedded/no-store boot (baked default)."""
        ws = self.ws
        hexs, saved_ver = None, 0
        store = ws.carts_store
        load = getattr(store, "load_system_icons", None) if store is not None else None
        if load is not None and ws.carts_root is not None:
            loadver = getattr(store, "load_system_icons_version", None)

            def _read_theme():
                return (load(ws.carts_root),
                        loadver(ws.carts_root) if loadver is not None else _ICON_VERSION)
            try:
                hexs, saved_ver = ws._with_sd(_read_theme)
            except Exception as exc:  # noqa: BLE001 -- a bad theme falls back to default
                print("Moybyte icons load failed:", _err_text(exc))
                hexs = None
        sheet = None
        if hexs and saved_ver >= _ICON_VERSION:        # current/newer saved theme -> keep it
            try:
                sheet = IconSheet.from_hex(hexs)
            except Exception:  # noqa: BLE001
                sheet = None
        if sheet is None:
            sheet = _default_icon_sheet()
            # Re-seed a STALE (or corrupt) saved theme to the new default so the new
            # icons land; skip when nothing was saved (no churn) or the store predates
            # versioning (no loadver -> _read_theme reported current, never stale).
            if hexs and ws.carts_root is not None \
                    and getattr(store, "save_system_icons", None) is not None:
                try:
                    ws._with_sd(lambda: store.save_system_icons(
                        sheet.to_hex(), ws.carts_root, _ICON_VERSION))
                except Exception as exc:  # noqa: BLE001
                    print("Moybyte icons re-seed failed:", _err_text(exc))
        self.set_icon_sheet(sheet)

    def save_icons(self):
        """Persist the edited system icon sheet to system_icons.moygfx (Stage 2 / #52),
        the exact mirror of the cart-sprite save: to_hex -> the SAME SD wrapper
        (host: direct write; device: with_sd_live). Then invalidate the bar caches so
        the NEXT bar draw shows the new pixels live: set_icon_sheet drops the per-kind
        _SheetSprite cache (and with it the device's per-Image RGB565 blit cache), and
        the sheet's gen already bumped on each pset so any gen-keyed cache rebuilds
        too. Surfaces a save status like the cart paint editor. A bad store/no SD root
        is a no-op (writes deferred).

        The theme editor has no SAVE tap (#111): this is the hard-commit verb its
        every exit path calls -- ThemeLayer.leave, the windowed WM's window-X and
        go_home."""
        ws = self.ws
        sheet = self.icon_sheet
        if not (sheet and ws.carts_root and ws.can_manage):
            return
        hexs = sheet.to_hex()
        try:
            ws._with_sd(lambda: ws.carts_store.save_system_icons(
                hexs, ws.carts_root, _ICON_VERSION))
            sheet.dirty = False
            ws.save_status = None           # clear stale failure text (see commit_code)
            # Re-adopt the (same) sheet so the bar's per-kind image cache is dropped and
            # the next _draw_status_strip rebuilds its sprites from the freshest pixels.
            self.set_icon_sheet(sheet)
            ws.ach.note("paint_save")       # "Little Artist": a theme saved (#21)
        except Exception as exc:  # noqa: BLE001
            # A failed save must be VISIBLE on device (no serial in the run loop), not
            # silent. _err_text-guarded so a weird __str__ can't escape.
            txt = _err_text(exc)
            ws.save_status = "CAN'T SAVE"
            ws.cart_error = "Could not save icons -- " + txt
            print("Moybyte save icons failed:", txt)

    # -- system font scale (#39) ---------------------------------------------

    def effective_font_scale(self):
        """The scale actually applied to the system canvas + layout. It is the
        requested font_scale ONLY when a distinct system canvas exists (one that can
        render scaled text); in the degradation case (the T-Deck / a shared 320x240
        canvas, whose framebuf text can't scale) it is 1, so the chrome geometry
        always matches the 8px text actually drawn -- no mis-laid-out desktop."""
        if self.ws._sys_canvas is None:
            return 1
        return self.font_scale if self.font_scale > self.text_floor else self.text_floor

    def effective_chrome_scale(self):
        """The scale INTERACTIVE CHROME geometry is laid out at (#203) -- the bar
        and its buttons, the ≡ menu's rows, the Settings rows, the code editor's
        symbol keys. Never below the font scale (text must still fit) and never
        below the board's tap-target floor.

        It sits beside `effective_font_scale` because every caller needs both and
        `_relayout` is the one cascade that applies them. The user's CHROME SIZE
        (`chrome_scale`) can lift it; the FLOOR is not a setting: it comes from
        `ws.chrome_floor`, resolved once from the board's declared glass, so the
        whole point of #203 -- a size a kid's finger can rely on -- is not one a
        stored choice can lose."""
        return max(self.effective_font_scale(), self.ws.chrome_floor,
                   self.chrome_scale)

    def valid_chrome_scale(self, scale):
        """`scale` if it is an int this panel can take (`ws.chrome_scales`),
        else 0 -- the board's default. The one reader of a stored or requested
        chrome scale, and a strict one: the store holds what `set_chrome_scale`
        wrote, so anything else reads as absent."""
        if type(scale) is not int or scale not in self.ws.chrome_scales:
            return 0
        return scale

    def set_chrome_scale(self, scale, persist=True):
        """Set the requested chrome scale (Settings -> CHROME SIZE), relayout the
        desktop through the same cascade a font-scale change takes, and (by
        default) persist it. Text is untouched: the system canvas keeps its font
        scale."""
        self.chrome_scale = self.valid_chrome_scale(scale)
        self.ws._relayout()
        if persist:
            self.ws.system.set("chrome_scale", self.chrome_scale)

    def cycle_chrome_scale(self, d):
        """Step CHROME SIZE by d through the panel's choices, from the scale the
        chrome is at now; applies + persists immediately."""
        opts = self.ws.chrome_scales
        if not opts:
            return
        cur = self.effective_chrome_scale()
        i = opts.index(cur) if cur in opts else 0
        self.set_chrome_scale(opts[(i + d) % len(opts)], persist=True)

    def set_font_scale(self, scale, persist=True):
        """Set the system-UI font scale (clamped to FONT_SCALES), relay the effective
        scale into the system canvas + relayout the desktop, and (by default) persist
        it. The game canvas text is always 8px; the effective scale is 1 without a
        distinct system canvas (so the choice is remembered but only shows on a panel
        that can render it)."""
        try:
            scale = int(scale)
        except (TypeError, ValueError):
            scale = 1
        if scale not in self.FONT_SCALES:
            scale = self.FONT_SCALES[0]
        self.font_scale = scale
        target = self._font_scale_canvas()
        if target is not None:
            target.set_font_scale(self.effective_font_scale())
        # The responsive re-derivation stays KERNEL: eight layout heads, every
        # registered app and the WM's re-anchor hook, none of it about the look.
        self.ws._relayout()
        if persist:
            self._persist_font_scale()

    def _font_scale_canvas(self):
        """The system canvas that OWNS the shell's font scale.

        NOT simply `ws._sys_canvas`: on the windowed tier that attribute is
        whatever WINDOW BUFFER is installed while a window's content draws or
        handles input -- and changing the font size from Settings is exactly
        that. The new scale then landed on a buffer that on_relayout immediately
        throws away, leaving the REAL canvas (and every future window buffer,
        which clones its font_scale from the root in new_layer) at the OLD size:
        layout reflowed to 1x with text still rendering at 2x, which is the
        owner's "changing it while running messes it up, if I change and reboot
        it looks great" (2026-07-26). The fullscreen tier has no _root_canvas and
        falls through to the ambient canvas, which IS the root there."""
        ws = self.ws
        wm = getattr(ws, "wm", None)
        root = getattr(wm, "_root_canvas", None) if wm is not None else None
        return root if root is not None else ws._sys_canvas

    def cycle_font_scale(self, d):
        """Step the font scale by d through FONT_SCALES (Settings < / > stepper);
        applies + persists immediately so the desktop text resizes live."""
        scales = self.FONT_SCALES
        cur = self.font_scale if self.font_scale in scales else scales[0]
        nxt = scales[(scales.index(cur) + d) % len(scales)]
        self.set_font_scale(nxt, persist=True)

    def _persist_font_scale(self):
        self.ws.system.set("font_scale", self.font_scale)

    # -- panel theme -----------------------------------------------------------

    def set_theme(self, name, persist=True, variant=None):
        """Pick the panel THEME (Appearance app -> THEMES): swap the chrome token set
        (chrome.theme_colors) the panels/window chrome/selection accents read each draw,
        and persist the choice. An unknown name falls back to the default.
        `variant` picks the theme's dark/light presentation (None keeps the
        current variant, so existing name-only callers are untouched)."""
        ws = self.ws
        if name != CUSTOM and self._cust is not None:
            self.commit()                      # leaving custom is an exit
            self._cust = None
            self._pkg_gen = None
        self._packages()
        if name not in THEME_NAMES:
            name = self._take_package(name)
        if variant is not None:
            self.theme_variant = variant if variant in THEME_VARIANTS \
                else DEFAULT_VARIANT
        self.theme_name = name
        _uimod.use_shape(name)
        refont = self._install_fonts(name)
        ws.theme_colors = theme_colors(name, self.theme_variant)
        self.publish()
        # The launcher grids read the accent for their selection ring/pill.
        ws.launcher.theme = ws.theme_colors
        if getattr(ws, "projects_grid", None) is not None:
            ws.projects_grid.theme = ws.theme_colors
        # The layouts read the fonts' metrics when they are built, and the
        # floor can move the effective font scale.
        if refont and getattr(ws, "layout", None) is not None \
                and getattr(ws, "launcher", None) is not None:
            target = self._font_scale_canvas()
            if target is not None:
                target.set_font_scale(self.effective_font_scale())
            ws._relayout()
        ws._dirty = True
        if persist:
            ws.system.set("theme", self.theme_name, persist=False)
            ws.system.set("theme_variant", self.theme_variant)
        self._layouts = {}
        self._choices = None
        self.apply_layout()
        self._apply_wallpaper()

    def install_theme(self, src):
        """Install theme package `src` (a theme.json's bytes) as the one
        beside the seeds (`moy_ui.theme_install`) and re-apply the live
        theme over it: an in-place edit is a re-install under its own name.
        (rc, notes), the loader's; a refusal leaves everything as it was."""
        rc, notes = _uimod.theme_install(src)
        if rc == 0:
            self._slot_src = None
            self._packages_moved()
        return rc, notes

    def uninstall_theme(self):
        """Drop the installed theme package: only the seeds remain."""
        _uimod.theme_uninstall()
        self._slot_src = None
        self._packages_moved()

    # -- the store's theme packages ---------------------------------------------

    def theme_names(self):
        """The seeds, then the store's theme packages that resolve, read once
        per store change (`chrome.theme_names`; THEME_NAMES stays the
        seeds')."""
        self._packages()
        return theme_names()

    def _packages(self):
        """stem -> folder of every theme package the roster holds: a cart
        whose manifest's type is "theme", named by its folder's stem. Re-read
        when the roster's generation moves, which drops every answer read
        from the old store (chrome's resolved packages, their fonts, the
        layouts)."""
        carts = getattr(self.ws, "carts", None)
        gen = getattr(carts, "gen", None)
        if carts is None or gen == self._pkg_gen:
            return self._pkg_paths
        self._pkg_gen = gen
        paths = {}
        for c in carts.all:
            if c.get("type") != "theme":
                continue
            path = str(c.get("path") or "").replace("\\", "/")
            stem = path.rsplit("/", 1)[-1]
            if stem.endswith(".moy"):
                stem = stem[:-4]
            if stem and stem not in THEME_NAMES:
                paths[stem] = path
        if self._cust is not None and CUSTOM not in paths:
            paths[CUSTOM] = self._custom_path()   # changed, not yet written
        self._pkg_paths = paths
        adopt_packages(tuple(paths), self._theme_src)
        used = [_uimod.font(role)[0] for role in _FONT_ROLES]
        for key in list(_FONTS):
            if key[0] is not None and _FONTS[key] not in used:
                _uimod.font_free(_FONTS.pop(key))
        self._layouts = {}
        self._choices = None
        return paths

    def _package_file(self, name, file, cap):
        """Up to cap + 1 bytes of file `file` of store package `name` (one
        over the cap, so the loader refuses an oversized file as BIG), or
        None when it cannot be read. `custom` while it is being changed reads
        its unwritten files from memory."""
        c = self._cust
        if name == CUSTOM and c is not None:
            got = c.theme if file == _THEME_MAIN else c.pending.get(file)
            if got is not None:
                return got[:cap + 1]
            if c.fresh:
                return None
        path = self._pkg_paths.get(name)
        store = getattr(self.ws, "store", None)
        if path is None or store is None or not store.ready():
            return None

        def read():
            with open(path + "/" + file, "rb") as f:
                return f.read(cap + 1)

        try:
            return store.call(read)
        except Exception:  # noqa: BLE001 -- an unreadable package resolves as refused
            return None

    def _theme_src(self, name):
        return self._package_file(name, _THEME_MAIN, _THEME_BYTES)

    def store_moved(self):
        """The roster changed (`CartManager.apply`): re-read the packages and,
        when the active theme is one, re-take it -- its bytes may have moved
        with a sync or an update, or it may be gone (then the default seed)."""
        self._packages()
        if self.theme_name not in THEME_NAMES and self._slot_src is not None \
                and self._slot_gen != self._pkg_gen:
            if self.theme_name not in self._pkg_paths:
                self.set_theme(DEFAULT_THEME)       # removed: the default, kept
            elif self._theme_src(self.theme_name) == self._slot_src:
                self._slot_gen = self._pkg_gen
            else:
                self.set_theme(self.theme_name, persist=False)

    def _take_package(self, name):
        """Put store package `name` in the install slot and answer the name
        set_theme switches to: `name`, or the default seed when the package
        will not load (its refusal noted once). A slot an install from bytes
        filled under `name` is kept: an in-place edit outlives a re-apply."""
        if type(name) is not str:
            return DEFAULT_THEME
        self._packages()
        if name == _uimod.theme_installed() and (
                self._slot_src is None or self._slot_gen == self._pkg_gen):
            return name
        got = package(name)
        if got is None:
            return DEFAULT_THEME
        rc = got[0]
        src = self._theme_src(name) if rc == 0 else None
        if src is not None and src == self._slot_src \
                and _uimod.theme_installed() == name:
            self._slot_gen = self._pkg_gen
            return name
        if src is not None:
            rc = _uimod.theme_install(src)[0]
        if rc != 0 or src is None:
            self._refuse(name, rc or 2)
            return DEFAULT_THEME
        self._pic_epoch += 1                   # its picture may have moved too
        self._slot_src = src
        self._slot_gen = self._pkg_gen
        forget_themes()
        return name

    def _refuse(self, name, rc):
        """A package that will not load: the row falls back to the default
        seed, and the loader's reason is shown once per package."""
        self.theme_note = (name, rc)
        if name in self._refused:
            return
        self._refused.add(name)
        notice = getattr(self.ws, "notice", None)
        if notice is not None:
            notice(_uimod.s("notice.theme_refused") % name.upper(),
                   _uimod.s("theme.why_" + _THEME_WHY[min(max(rc, 1), 7) - 1]), "warn")

    def _packages_moved(self):
        # Every theme resolves along `inherits` to the default, so an install
        # can move any (theme, variant)'s tokens: the shared dicts all go.
        forget_themes()
        self.set_theme(self.theme_name, persist=False)

    # -- the shell layout (docs/shell_layout_2026-10.md §9-§11) ---------------

    def layout_theme(self):
        """The `layout_theme` row, read strictly: the name of a theme (a
        seed, a store package that resolves, or the one installed beside
        them), else None -- the active theme's layout. A value of any other
        shape reads as absent."""
        v = self.ws.system.get("layout_theme")
        if type(v) is str:
            self._packages()
            if theme_known(v):
                return v
        return None

    def _theme_layout(self, name):
        """(refused, bars, home): theme `name`'s layout as the loader resolves
        it -- a store package's from its bytes (`chrome.package`), so a row
        naming one other than the active theme needs no install."""
        got = self._layouts.get(name)
        if got is None:
            pkg = None
            if name not in THEME_NAMES and name != _uimod.theme_installed():
                pkg = package(name)
            if pkg is not None and pkg[0] == 0:
                got = pkg[6]
            else:
                got = _uimod.theme_layout(name) + (_uimod.theme_home(name),)
            self._layouts[name] = got
        return got

    def layout(self):
        """(bars, notes): the bars in force -- the `layout_theme` package's
        if the row names one, else the active theme's, else the default's
        (the loader's precedence along `inherits`) -- resolved for this
        tier's WM in its current mode, on its glass at the chrome scale
        (shell_layout.resolve). Never read per frame."""
        ws = self.ws
        wm = ws.wm
        refused, bars, _home = self._theme_layout(self.layout_theme() or self.theme_name)
        notes = ("the theme's layout was refused",) if refused else ()
        root = getattr(wm, "_root_canvas", None) or ws.sys_canvas
        return _shell_layout.resolve(
            bars, self._theme_layout(DEFAULT_THEME)[1], root.w, root.h,
            self.effective_chrome_scale(), wm.layout_catalogue(), wm.LAYOUT_EDGES,
            _ui_text_w, notes)

    def apply_layout(self):
        """Resolve `layout()` and put the host's bars where it says
        (`Workstation.place_bars`, which relayouts). Runs at a theme switch,
        a `layout_theme` write and a mode switch; a no-op before the WM and
        the bar exist. True when the bars moved (the shell relayouted)."""
        ws = self.ws
        self.layout_gen += 1
        if getattr(ws, "wm", None) is None or getattr(ws, "bar_layer", None) is None:
            return False
        bars, self.layout_notes = self.layout()
        home = self._theme_layout(self.layout_theme() or self.theme_name)[2]
        if home != self.home:
            self.home = home
            ws.set_home_arrangement(home)
        if bars != self.bars:
            self.bars = bars
            ws._dirty = True
        if bars != _layout_base.bars_in_force():
            ws.place_bars(bars)
            return True
        return False

    def layout_choices(self):
        """The packages whose layout differs from the active theme's: the
        values the Settings LAYOUT row steps through after THEME. Cached until
        the next theme switch."""
        if self._choices is None:
            names = self.theme_names()
            extra = _uimod.theme_installed()
            if extra is not None and extra not in names:
                names = names + (extra,)
            mine = self._theme_layout(self.theme_name)[1:]
            self._choices = tuple(n for n in names if self._theme_layout(n)[1:] != mine)
        return self._choices

    def cycle_layout_theme(self, d):
        """Step the `layout_theme` row by d through THEME (the row deleted),
        then each of `layout_choices()`; applies and persists."""
        pick = self.layout_theme()
        vals = (None,) + self.layout_choices()
        if pick is not None and pick not in vals:
            vals = vals + (pick,)
        nxt = vals[(vals.index(pick) + d) % len(vals)]
        if nxt is None:
            self.ws.system.delete("layout_theme")
        else:
            self.ws.system.set("layout_theme", nxt)
        self.apply_layout()

    def _install_fonts(self, name):
        """Load theme `name`'s font files, install each for its role and the
        `ui` role's as the print font, free every font no role keeps, and
        take the text-size floor the `ui` font asks of this board's glass
        (`chrome.text_scale_floor`; docs/theming_2026-09.md §4.7). A font
        that will not load leaves its role on petme128. True when a role's
        font or the floor changed."""
        files = {}
        for role, f in _uimod.theme_fonts(name):
            files[role] = (None, f)
        if self._slot_src is not None and name == _uimod.theme_installed():
            got = package(name)
            for role, f in (got[5] if got is not None and got[0] == 0 else ()):
                files[role] = (name, f)
        ids = {}
        for role in _FONT_ROLES:
            f = files.get(role)
            ids[role] = self._font_id(f) if f else 0
        changed = False
        for role in _FONT_ROLES:
            if _uimod.font(role)[0] != ids[role]:
                changed = True
            _uimod.use_font(role, ids[role])
        _uimod.print_font(ids["ui"])
        keep = set(ids.values())
        for key in list(_FONTS):
            if _FONTS[key] not in keep:
                _uimod.font_free(_FONTS.pop(key))
        ws = self.ws
        floor = 1
        if ids["ui"]:
            panel = ws._sys_canvas if ws._sys_canvas is not None else ws.canvas
            floor = text_scale_floor(panel.w, panel.h, ws.panel_diagonal_in,
                                     _uimod.font("ui")[3])
        if floor != self.text_floor:
            self.text_floor = floor
            changed = True
        return changed

    def _font_id(self, key):
        """The loaded id of font `key`, (package, file): a store package's
        file from its folder, a seed's (package None) from the image; loaded
        once, 0 when it cannot be."""
        fid = _FONTS.get(key)
        if fid is not None and _uimod.font_info(fid) is not None:
            return fid
        pkg, f = key
        if pkg is None:
            src = _uimod.font_file(f)
        else:
            src = self._package_file(pkg, f, _FONT_BYTES)
        if src is None:
            return 0
        rc, fid = _uimod.font_load(src)
        if rc == _FONT_FULL:
            # The slots are the process's: where no kernel frees them with
            # the VM (a host, a test), the theme's are all they keep.
            _uimod.font_reset()
            _FONTS.clear()
            rc, fid = _uimod.font_load(src)
        if fid is None:
            return 0
        _FONTS[key] = fid
        return fid

    def set_theme_variant(self, variant, persist=True):
        """Flip the current theme between its dark and light presentation
        (Appearance app -> THEMES -> DARK/LIGHT)."""
        self.set_theme(self.theme_name, persist=persist, variant=variant)

    def cycle_theme(self, d):
        """Step the panel theme through `theme_names()` (programmatic verb; the UI
        pick is the Appearance app). Applies + persists."""
        names = self.theme_names()
        cur = self.theme_name if self.theme_name in names else names[0]
        self.set_theme(names[(names.index(cur) + d) % len(names)], persist=True)

    # -- the per-draw gate ---------------------------------------------------

    def publish(self):
        """Write the live look into the app ABI's token table (native/moy_app):
        the theme's name, its variant and every token of `ws.theme_colors`,
        under a new generation -- what `ctx.theme`'s rows answer and
        its colour dict is rebuilt from. The console calls it once its app
        state exists; every switch after that comes through here."""
        abi = getattr(self.ws, "app_abi", None)
        gen = 0
        if abi is not None:
            gen = abi.theme_write(self.theme_name, self.theme_variant,
                                  self.ws.theme_colors)
        # Strictly rising: the console's look may move before its ABI exists.
        self.look_gen = gen if gen > self.look_gen else self.look_gen + 1
        _uimod.look_moved()

    def content_colors(self):
        """The live theme's CONTENT roles (code syntax, block colours):
        `chrome.theme_content` for the current theme and variant."""
        return theme_content(self.theme_name, self.theme_variant)

    def light_chrome(self):
        """True when the live theme's tool surface is LIGHT (visual identity v1
        Phase 3) -- THE gate every surface's light branch reads (ui.is_light over
        the live tokens). Read per DRAW by five editor surfaces and the bar, so it
        stays one bound call over a flat comparison and nothing else."""
        return _ui_is_light(self.ws.theme_colors)
