"""The LOOK (#209 landing D) -- `Workstation.look`.

Every verb that changes how the desktop looks: the panel theme and its
dark/light variant, the widget skin, the system font scale, the desktop
wallpaper and the top-bar icon sheet. Three axes plus a backdrop plus the bar
art, which until this landing were five unrelated stretches of the kernel that
happened to share a Settings screen.

Not to be confused with `appearance_app.py`, which is the PICKER a kid taps --
that app keeps its name and its place in the app registry, and reaches these
verbs through `ctx.theme` / `ctx.wallpaper` like any other app.

## `select_wallpaper` is why this object exists

It is the one verb in the shell that crosses four owners in nine lines: it asks
the ROSTER for the wallpaper carts and rehydrates the chosen one out of the #66
live-set diet (`ws.carts`), drives the backdrop COMPONENT's compile
(`ws.wallpaper`), writes the choice through the settings funnel (`ws.prefs`),
and notes an achievement (`ws.ach`). Rev 1 of the architecture doc left it
unassigned for exactly that reason. It lands here as a COORDINATOR: it calls
the other four objects and owns none of them, which is what "the look" is --
policy over components, not a component.

## The tokens stay on the kernel, and they are written from here

`ws.theme_colors` is the one piece of this cluster's state that did NOT move.
It is read per DRAW by ~70 sites across twenty surface modules (usually hoisted
once per draw method as `th = ws.theme_colors`), and doc 3e's temperature table
pins that row as "token reads stay flat; the cluster owns the verbs". So the
dict stays a plain kernel attribute with ONE author -- `set_theme` below is the
only thing in the tree that ever writes it -- which is the same shape rev 3
gave the achievement overlays: the collaborator generates the value, the kernel
holds the flat field the frame path reads.

`ws.system`'s in-place trick is not available here and must not be attempted:
`launcher_layer._statics_key` and `_pseudo_key` fold `id(ws.theme_colors)` into
their cache keys, so a theme swap invalidates the shelf statics BECAUSE the
dict is rebound. Mutating it in place would leave the launcher painting the old
colorway until something else happened to dirty it.

## What stays on the kernel, and why

`_relayout` (the font-scale verb's cascade) is the shell's whole responsive
re-derivation -- eight layout heads, every registered app, the WM hook. It is
kernel policy that a dozen things besides a font change would call, so
`set_font_scale` reaches back for it rather than owning it.

`_bar_img_cache` stays kernel too: it backs `ws._bar_image` / `ws._icon`, which
are the shared DRAW TOOLKIT (doc 4 keeps that on the kernel by decision), and
bar_layer's own header already says so. `set_icon_sheet` clears it, the one
kernel field this object writes besides the tokens.

`_fat_cart` is the OPEN WORKSPACE's cart, not a look setting; `select_wallpaper`
reads it through `ws` to decide whether a compiled wallpaper cart may be
re-slimmed.

## Reading late-injected things through `ws`, per call

Same rule the other four collaborators follow (doc 3c). This object is built in
`Workstation.__init__`, before the store is wired, before `system.json` is
read, before the layer stack exists -- so `carts_store`, `carts_root`,
`_with_sd`, `launcher`, `picker` and `bar_layer` are all reached through `ws`
at the moment of use and never captured here.
"""

try:
    from chrome import (_default_icon_sheet, _err_text, _ICON_VERSION,
                        DEFAULT_THEME, DEFAULT_VARIANT, theme_colors, THEMES,
                        THEME_VARIANTS)
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime.chrome import (_default_icon_sheet, _err_text, _ICON_VERSION,
                                DEFAULT_THEME, DEFAULT_VARIANT, theme_colors,
                                THEMES, THEME_VARIANTS)
try:
    from editors import IconSheet
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime.editors import IconSheet
# The skin CATALOG. This module is the OWNER the ratchet in tests/test_skin.py
# names (`_SKIN_OWNERS`): the one place `skin.use` is called, and the only
# module besides the picker allowed to import it at all.
try:
    import skin as _skin
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime import skin as _skin
try:
    import ui as _uimod
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime import ui as _uimod
_ui_is_light = _uimod.is_light


class Appearance:
    """The look: theme, variant, skin, font scale, wallpaper, icon sheet.

    Plain methods, no properties (a property forward measured +5.1us against a
    plain hop's +0.5us on this codebase). Nothing here runs per frame -- the
    per-draw surface is `light_chrome()` and the `ws.theme_colors` dict it
    reads, both of which stay one cheap call and one flat attribute."""

    # -- desktop wallpaper (#28) ---------------------------------------------
    #
    # The home screen renders a chosen wallpaper-type cart as a live backdrop:
    # exactly the Picotron model where a wallpaper is just a fullscreen cart. We
    # reuse the cart-run machinery (compile + _init/_update/_draw) but in a SEPARATE
    # namespace so it never collides with the foreground cart. Fallback options are
    # plain solid MOY64 fills ("fill:<color>"), so there's always a valid choice
    # even with zero wallpaper carts installed (and a cheap option for the device).

    FILL_WALLPAPERS = ("fill:dark_blue", "fill:black", "fill:indigo", "fill:dark_purple")

    # -- system font scale (#39) ---------------------------------------------
    #
    # The system-UI font is settings-resizable (petme128 nearest-neighbor x1/x2/x3),
    # persisted in system.json (mirroring the #28 wallpaper setting) and applied live.
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
        # Panel THEME (Appearance -> THEMES): the chrome token set the panels/window
        # chrome/selection accents read each draw. Default = the moybyte "night"
        # colorway (today's exact colors); load_system applies the persisted pick.
        self.theme_name = DEFAULT_THEME
        self.theme_variant = DEFAULT_VARIANT
        # The live token dict lives on the KERNEL, not here -- see the module
        # docstring. `set_theme` is its only author; this is the first write.
        ws.theme_colors = theme_colors(DEFAULT_THEME)
        # The widget SKIN (Appearance -> THEMES -> the skin chips) is the third
        # axis, and unlike the two above it is not this object's state: it is
        # installed INTO `ui`, process-wide. A fresh Workstation therefore
        # ADOPTS what is installed rather than asserting the default over it --
        # on a board the two readings are the same (one Workstation, and `ui`'s
        # own tables are the default), and on a host that builds several in one
        # process an unrelated boot must not silently restyle the others.
        self.skin_name = _skin.active()
        self.wallpaper_id = None      # chosen wallpaper: cart slug or "fill:<color>" --
                                      # the single source; select_wallpaper drives it.
        # Unified top bar (Stage 1): the editable 16x16 IconSheet the bar draws its
        # chrome icons from. Loaded by `load_icon_sheet` at the end of the boot wiring
        # (from system_icons.moygfx, else the baked default theme); None falls back to
        # the kernel's _glyph.
        self.icon_sheet = None

    # -- wallpaper discovery -------------------------------------------------

    def wallpaper_carts(self):
        """The wallpaper-type carts available as backdrops (discovery by type, Moybyte's
        equivalent of Picotron's wallpapers folder). Reads the FULL scanned list, not the
        launcher grid -- wallpapers are a backdrop category chosen in the Appearance app,
        so they leave the launcher RUN-grid (spec shell_ux_v1.md) but stay discoverable
        here."""
        return [c for c in self.ws.carts.all if c.get("type") == "wallpaper"]

    def wallpaper_options(self):
        """All selectable wallpaper ids: each wallpaper cart's slug, then the
        built-in solid fills (always present so there's a valid pick)."""
        out = []
        for c in self.wallpaper_carts():
            out.append(self.wp_id_for(c))
        out.extend(self.FILL_WALLPAPERS)
        return out

    def wp_id_for(self, cart):
        # A stable id for a wallpaper cart: its folder name (slug) so the choice
        # survives a reboot. Embedded/path-less carts fall back to the title slug.
        path = cart.get("path")
        if path:
            name = path.rsplit("/", 1)[-1]
            if name.endswith(".moy"):
                name = name[:-4]
            return name
        store = self.ws.carts_store
        return store.slug(cart["title"]) if store else cart["title"]

    def wp_cart_by_id(self, wp_id):
        for c in self.wallpaper_carts():
            if self.wp_id_for(c) == wp_id:
                return c
        return None

    # -- the wallpaper CHOICE (the coordinator) ------------------------------

    def select_wallpaper(self, wp_id, persist=True):
        """Choose the desktop backdrop. `wp_id` is a wallpaper cart slug or a
        "fill:<color>" built-in; an unknown/None id falls back to the first
        available option. Compiles the chosen cart into its own namespace (or sets
        a solid fill) and, when persist, writes the choice to system.json."""
        ws = self.ws
        opts = self.wallpaper_options()
        if wp_id not in opts:
            wp_id = opts[0] if opts else self.FILL_WALLPAPERS[0]
        self.wallpaper_id = wp_id
        ws.wallpaper.clear()
        if not (isinstance(wp_id, str) and wp_id.startswith("fill:")):
            cart = self.wp_cart_by_id(wp_id)
            if cart is not None:
                # #66 live-set diet: a slimmed wallpaper cart rehydrates for the
                # compile (which bakes src/sheet into the wallpaper's own ns), then
                # re-slims -- unless it IS the open project's cart (stays fat).
                ws.carts.rehydrate(cart)
                ws.wallpaper.compile(cart)   # compile into the backdrop component (#28)
                if cart is not getattr(ws, "_fat_cart", None):
                    ws.carts.reslim(cart)
        if persist:
            self._persist_wallpaper()
            # "Home Decorator": any persisted pick counts -- the Appearance app,
            # Paint's WALL, the cycle verb. Boot restore (persist=False) doesn't.
            ws.ach.note("wallpaper_change")   # (#21)

    def _persist_wallpaper(self):
        self.ws.system["wallpaper"] = self.wallpaper_id
        self.ws.prefs.persist()

    def cycle_wallpaper(self, d):
        """Step the wallpaper choice by d (programmatic verb; the UI pick is the
        Appearance app); applies + persists immediately."""
        opts = self.wallpaper_options()
        if not opts:
            return
        cur = self.wallpaper_id if self.wallpaper_id in opts else opts[0]
        nxt = opts[(opts.index(cur) + d) % len(opts)]
        self.select_wallpaper(nxt, persist=True)

    # -- the top-bar icon sheet (#52) ----------------------------------------

    def set_icon_sheet(self, sheet):
        """Adopt the top-bar IconSheet (Stage 1) and drop the per-kind image cache so
        the next frame rebuilds its sprites (and, on the device, their RGB565 copies)
        from the new theme. None reverts the bar to the _glyph fallback."""
        self.icon_sheet = sheet
        # The kernel's cache, cleared from here: it backs ws._bar_image/_icon, the
        # shared draw toolkit, and a stale entry would keep blitting old pixels.
        self.ws._bar_img_cache = {}
        self.ws.bar_layer.invalidate()   # repaint the cached cart bar with the new theme (#43)

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
        return self.font_scale if self.ws._sys_canvas is not None else 1

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
        self.ws.system["font_scale"] = self.font_scale
        self.ws.prefs.persist()

    # -- panel theme + widget skin -------------------------------------------

    def set_theme(self, name, persist=True, variant=None):
        """Pick the panel THEME (Appearance app -> THEMES): swap the chrome token set
        (chrome.THEMES) the panels/window chrome/selection accents read each draw,
        and persist the choice. An unknown name falls back to the default.
        `variant` picks the theme's dark/light presentation (None keeps the
        current variant, so existing name-only callers are untouched)."""
        ws = self.ws
        if not any(n == name for n, _t in THEMES):
            name = DEFAULT_THEME
        if variant is not None:
            self.theme_variant = variant if variant in THEME_VARIANTS \
                else DEFAULT_VARIANT
        self.theme_name = name
        # REBOUND, never mutated in place: the launcher's statics keys fold
        # id(ws.theme_colors), so the new dict IS the invalidation.
        ws.theme_colors = theme_colors(name, self.theme_variant)
        # The launcher grids read the accent for their selection ring/pill.
        ws.launcher.theme = ws.theme_colors
        if getattr(ws, "picker", None) is not None:
            ws.picker.theme = ws.theme_colors
        # The cached bar strip now paints theme-colored pixels (the launcher zone's
        # PLAY/CHANGE chips), and its cache key doesn't fold the theme name -- bump
        # the explicit generation so a theme switch repaints it.
        if getattr(ws, "bar_layer", None) is not None:
            ws.bar_layer.invalidate()
        ws._dirty = True
        if persist:
            ws.system["theme"] = self.theme_name
            ws.system["theme_variant"] = self.theme_variant
            ws.prefs.persist()

    def set_theme_variant(self, variant, persist=True):
        """Flip the current theme between its dark and light presentation
        (Appearance app -> THEMES -> DARK/LIGHT)."""
        self.set_theme(self.theme_name, persist=persist, variant=variant)

    def cycle_theme(self, d):
        """Step the panel theme through chrome.THEMES (programmatic verb; the UI
        pick is the Appearance app). Applies + persists."""
        names = [n for n, _t in THEMES]
        cur = self.theme_name if self.theme_name in names else names[0]
        self.set_theme(names[(names.index(cur) + d) % len(names)], persist=True)

    def skin_names(self):
        """The widget skins a picker may offer, in presentation order."""
        return _skin.names()

    def set_skin(self, name, persist=True):
        """Install the widget SKIN (Appearance -> THEMES -> the skin chips) and
        persist the choice, exactly as `set_theme` does for the colorway.

        A skin is a delta over `ui`'s widget tables -- fields, edges, label
        alignment -- so every surface changes at once and none of them knows:
        this is the ONLY place the catalog is installed. An unknown name
        resolves to the default (`skin.use`), and the RESOLVED name is what
        gets stored, so a store that names a skin this build dropped heals
        itself on the next pick instead of re-failing every boot."""
        ws = self.ws
        self.skin_name = _skin.use(name)
        # Same two invalidations a theme change needs: the cached top-bar strip
        # paints widget pixels and its key does not fold the skin, and every
        # other surface repaints from the damage epoch.
        if getattr(ws, "bar_layer", None) is not None:
            ws.bar_layer.invalidate()
        ws._dirty = True
        if persist:
            ws.system["skin"] = self.skin_name
            ws.prefs.persist()

    # -- the per-draw gate ---------------------------------------------------

    def light_chrome(self):
        """True when the live theme's tool surface is LIGHT (visual identity v1
        Phase 3) -- THE gate every surface's light branch reads (ui.is_light over
        the live tokens). Read per DRAW by five editor surfaces and the bar, so it
        stays one bound call over a flat comparison and nothing else."""
        return _ui_is_light(self.ws.theme_colors)
