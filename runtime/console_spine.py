"""The kernel spine's half of the Workstation (docs/kernel_spine_2026-10.md):
the run and exit verbs, app registration and resolution, the WiFi lease and
the settings wiring, as a mixin over `self`.

The state these verbs keep lives in the spine's components (native/moy_spine) --
`self.apps` (AppRegistry), `self.wm.stack` (BackStack), `self.returns`
(Returns), `self.leases` (Leases) and `self.prefs.rows` (Settings) -- which
hold ids, kinds and JSON text, never a Python object. What stays here is the
Python side of each verb: the app objects (`_apps`, `_apps_by_id`), the
surfaces a route lands on, and the radio service the lease powers.
"""

try:
    from app_decls import APPS
    from chrome import NAMES
    from settings_layer import SETTINGS_TOGGLES
    from moy_spine import (EDITOR, ROUTE_APP, ROUTE_EDITOR, ROUTE_WINDOW)
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime.app_decls import APPS
    from runtime.chrome import NAMES
    from runtime.settings_layer import SETTINGS_TOGGLES
    from runtime.moy_spine import (EDITOR, ROUTE_APP, ROUTE_EDITOR,
                                   ROUTE_WINDOW)
import moy_app as _moy_app


def _resolve_app_entry(entry):
    """Resolve an app declaration's "module:Class" to the class itself.

    Two namespaces, as everywhere in this tree: the boards and the wasm head
    freeze `runtime/` FLAT (`import files_app`), the host imports the package
    (`runtime.files_app`). Same ladder every module header here writes by
    hand -- resolved from data instead of once per app.
    """
    mod_name, _, cls_name = entry.partition(":")
    try:
        mod = __import__(mod_name, None, None, (cls_name,))
    except ImportError:
        mod = __import__("runtime." + mod_name, None, None, (cls_name,))
    return getattr(mod, cls_name)


class SpineVerbs:
    """The spine's verbs on the Workstation. `Workstation.__init__` creates the
    state they touch."""

    # -- user apps: identity + the crash guard (#181 / #160) -----------------

    def app_cart_id(self, cart):
        """The stable identity a USER APP cart's grant, prefs namespace and
        crash strikes are keyed by (`moy_app.id_for`: the cart's `id`, #162,
        so a renamed cart keeps them; a path-less built-in's title slug)."""
        return _moy_app.id_for(cart.get("id"), cart.get("title"))

    def is_user_app(self, cart):
        """True when `cart` is an app cart the Player runs as a USER APP -- a
        `type: "app"` cart that no registered shell app claims as its identity.

        The claim check matters: `moybyte.calc.moy` is also `type: "app"`, but the
        launcher dispatches it to `CalcAppLayer` and its `main.py` is only the
        older-shell fallback body."""
        return (cart is not None and cart.get("type") == "app"
                and not self.is_system_app(cart))

    def cart_broken(self, cart):
        """True when the crash guard has turned this app cart OFF (#160).

        NOT what the Player reads -- it refuses through `app_guard.arm()`
        returning False, which is the same answer taken on the path that also
        records the strike. This is the shell-vocabulary query (is_user_app +
        the title-slug id in one call): `tests/test_user_apps.py` asserts
        against it, and it is half of what the deferred picker BADGE needs
        (docs/history/ui_refactor_2026-08.md, Phase 8's open tails). The cart stays in
        the Editor picker either way, because editing it is how it gets
        fixed."""
        if not self.is_user_app(cart):
            return False
        return self.app_guard.disabled(self.app_cart_id(cart))

    def forgive_app(self, cart):
        """Clear `cart`'s crash strikes -- the kid changed its CODE (#160).

        The other half of three-strikes, and without it the refusal panel's
        "EDIT it" was a dead end: nothing called `CrashGuard.forgive`, so the
        only ways back were renaming the cart or hand-editing `system.json`.

        Called from `Project.commit_code`, and deliberately from there ALONE.
        A code commit is the one edit that can change whether the cart hangs,
        faults or exhausts the heap -- the failures the guard exists for, none
        of which a sprite, a map or a config tweak can fix or cause. Forgiving
        on every asset save would hand a boot-looping app a fresh set of
        strikes for repainting a tile; forgiving only on a hand-edited
        `system.json` is what we had.

        Strikes are cleared, not decremented: the kid's next open starts from
        zero and gets the full three, exactly like a cart the guard has never
        seen. False when there was nothing to forgive."""
        if not self.is_user_app(cart):
            return False
        return self.app_guard.forgive(self.app_cart_id(cart))

    def forgive_wallpaper(self, cart):
        """Clear `cart`'s crash strikes AS THE BACKDROP (#160) -- `forgive_app`'s
        twin for the wallpaper role, called beside it from
        `Project.commit_code` for the same reason. The ledger is keyed by the
        wallpaper id; a cart with nothing on it writes nothing. The fixed code
        runs at the next boot or the next pick of it in Appearance."""
        if cart is None or cart.get("type") != "wallpaper":
            return False
        return self.wallpaper_guard.forgive(self.look.wp_id_for(cart))

    def load_system(self):
        """Read the system settings (`self.prefs`) and APPLY them -- the saved
        wallpaper, font scale, theme, skin and every persisted toggle (#39).

        The read is the store's; this cascade is kernel policy and stays here.
        What a setting MEANS is the shell's business -- each one relays through
        the same `set_*` verb the Settings row calls, with persist=False so
        loading never re-writes what it just read."""
        self.prefs.load()
        # System font scale (#39): apply the persisted choice (1/2/3) so the desktop
        # boots at the saved text size. look.set_font_scale relays it into the system
        # canvas + relayouts; persist=False so loading doesn't re-write the store.
        self.look.set_font_scale(self.system.get("font_scale", self.look.font_scale),
                                 persist=False)
        # Paint's shared document lives outside the re-seeded built-in cart. Restore
        # My Art's bg asset before compiling a persisted My Art wallpaper.
        if self.artwork is not None:
            self.artwork.sync_wallpaper()
        # A SAFE start (the kernel's recovery screen) runs no wallpaper cart:
        # the settings' absence would fall back to the first one there is.
        wallpaper = self.system.get("wallpaper")
        if getattr(self.prefs, "safe", False):
            wallpaper = self.look.FILL_WALLPAPERS[0]
        self.look.select_wallpaper(wallpaper, persist=False)
        self.look.set_theme(self.system.get("theme", self.look.theme_name),
                            persist=False,
                            variant=self.system.get("theme_variant",
                                                    self.look.theme_variant))
        # The widget skin, beside the colorway it belongs with. Applied ONLY
        # when the store names one: `ui`'s tables are already the default, so
        # "no key" means "nothing to install", not "install the default over
        # whatever this process has" -- see the note at Appearance.skin_name.
        _sk = self.system.get("skin")
        if _sk is not None:
            self.look.set_skin(_sk, persist=False)
        # Every persisted ON/OFF setting, through the verb its registry entry
        # names (#209 section 7): the key is the system.json key, the default is
        # declared beside it, and a board that cannot serve one gets the honest
        # answer from the setter rather than a silent flag. Six hand-kept lines
        # used to sit here, and a seventh toggle is now none.
        for key, _label, default, setter, _gate, _dev in SETTINGS_TOGGLES:
            getattr(self, setter)(self.system.get(key, default), persist=False)
        # A key a retired setting left behind (#217 took FRAMESKIP) would ride
        # every write forever; drop it once.
        self.system.delete("frameskip")

    def run(self, project, caller):
        """Show `project`'s running cart on the desktop, recording `caller` so the exit
        gesture knows where to return (spec Section 2's run/return -- a stack discipline,
        not a blocking call, since the frame loop can't block). The cart itself is started
        by the explicit _start() at each call site (open/apply/run_code/_leave_menu);
        run() makes the desktop layer active + records the caller. The launcher home root
        is one caller (pop == go_home); the Editor is the second (Stage 3), so PLAY
        returns to the same tab -- proving the Player is caller-agnostic."""
        if self.cart_error is not None and self._crash_to_code():
            return                     # the start already failed (syntax/init error):
                                       # no parked OOPS screen -- straight to the line
        self.crash_popup = None        # a clean launch retires any stale popup
        kind = self._caller_kind(caller)
        self.returns.run(kind)
        # #178: the caller already says WHY this run is starting, so the windowed
        # tier can size the playtest window for the job -- an Editor PLAY is a dev
        # action (small, beside the code), a desk/Library run is play (as big as
        # fits). Stamped BEFORE the push that creates the window.
        self.wm.set_play_intent("dev" if kind == EDITOR else "play")
        self.wm.goto("desktop")        # Stage 6e: push the Player process onto the back-stack

    def _caller_kind(self, caller):
        """The kind a run started by `caller` records: the Editor's for the
        Editor, a surface's own id otherwise, None for anything without one."""
        if caller is None:
            return None
        if caller is self.editor_app:
            return EDITOR
        kind = getattr(caller, "id", None)
        if isinstance(kind, str) and 0 < len(kind.encode()) <= 15:
            return kind
        return None

    def _exit_to_caller(self):
        """Pop the running cart back to whoever launched it (run()'s recorded caller,
        spec Section 2's launch-and-return). The Player's Stage-5 exit gestures
        (hold-BACKSPACE) calls this. The Editor is the second caller
        (Stage 3b): a cart run from PLAY returns to the Editor on the tab it left
        (screen -> "menu"; editor_app.tab is preserved -> the SAME tab), proving the
        Player has zero knowledge of who launched it. A registered APP is the
        third (Files opening a note in the Notes cart, step 3 of
        docs/text_editing_2026-09.md): it pops back to that app's own surface,
        on the shelf the person was standing on. Any other caller (the launcher
        home root, or None) pops all the way home. A PROJECT FILE edit (step 5)
        is the Editor arm with a reload in front of it -- see
        `_return_to_project`."""
        # Drop the dead run's world NOW (#66 repeat-run fragmentation fix): the
        # next cart must build into a compact heap, not around this one's corpse
        # (see Player.release_world's docstring for the measured mechanism).
        self.player.release_world()
        back = self._project_return
        self._project_return = None
        # A run's caller is SPENT by its return. Leaving it set is what wedged
        # Files (#108, on glass 2026-09-07): the returned-to APP's own context X
        # routes here too, and a stale caller made it `goto` the app already on
        # top -- a no-op, so Files could never be left again once it had opened
        # a note.
        rt = self.returns
        if back is not None:
            rt.spend()
            self._return_to_project(back)
            return
        # Windowed WM (#73): closing the playtest must never truncate unrelated
        # windows stacked above it (e.g. Settings) -- the WM removes ONLY the
        # player and refocuses the caller's window. Desk world only (#105): a
        # play-world game (launched from the fullscreen Library) pops by the
        # fullscreen rules below, landing back in the Library via go_home.
        _cp = getattr(self.wm, "close_player", None)
        route = rt.route(_cp is not None and self.wm.desk_open())
        if route == ROUTE_WINDOW:
            self._dirty = True
            _cp()                          # reads the caller to refocus
            rt.spend()
            return
        caller = rt.spend()
        if route == ROUTE_EDITOR:
            self._dirty = True             # screen change repaints (#44)
            self.wm.goto("menu")           # Stage 6e: pop the Player, back to the Editor tab
            # #80: returning DIRECTLY to the code tab is not a tab CHANGE, so
            # set_menu_view's text-mode flip never fires here -- the keyboard
            # stayed in the cart's raw-matrix mode, where plain letters still
            # map but the sym layer doesn't exist (sym+digit typed NOTHING in
            # the code editor after a PLAY). Restore the returned-to tab's mode.
            self._set_text_mode(getattr(self.editor_app, "tab", None) == "code")
            return
        if route == ROUTE_APP and self._return_to_app(caller):
            return
        self._go_home_or_back()

    def _return_to_app(self, app_id):
        """Land back on registered app `app_id`'s own surface, on the row it was
        showing. A back-stack RETURN and never a re-open: `open_app` would call
        the app's `open()`, which resets it to its root -- and coming back to a
        different screen than you left is exactly what a return must not do."""
        app = self._apps_by_id.get(app_id)
        if app is None:
            return False
        self._dirty = True
        self.wm.goto(app_id)
        h = self.apps.find(app_id)
        if h:
            self._set_text_mode(self.apps.text_mode(h))
        return True

    def _go_home_or_back(self):
        """Leave the top surface for the launcher root -- unless an APP opened
        it, in which case leaving lands back INSIDE that app.

        `returns.back()` is the app-to-app half of the launch-and-return
        contract (#108). The run's caller covers a RUN (the Player pops to
        whoever started it) and `_project_return` a project-file edit (which comes back through
        the loader); this covers the third shape -- Files opening a drawing in
        Paint or a project in the Editor, neither of which is a run. It survives
        a nested run on purpose, so PLAY from a Files-opened Editor still comes
        home to Files. Going home clears it, so a later unrelated exit can never
        inherit it."""
        back = self.returns.take_back()
        self.go_home()                     # the leaving surface's save + teardown
        if back is not None and self.wm.top_is("launcher"):
            self._return_to_app(back)

    def _note_app_caller(self):
        """Record the registered APP this navigation is leaving, so whatever it
        opens comes back to it. Called at the app-to-app jumps -- `open_app`
        and the two `ctx.nav` editor doors.

        It only ever SETS. A jump from somewhere that is not an app deepens a
        journey rather than starting one (Files -> a project's Editor -> that
        cart's cover in Paint is still on its way back to Files), and clearing
        there would strand the shelf. `go_home` is the one reset, because the
        launcher root is where every return path ends."""
        self.returns.note(self.wm.top_kind())

    def exit(self):
        """Exit the active TASKBAR app back toward the launcher root (spec Section 9's
        context X, Stage 5): BarLayer.handle_bar_tap routes a tap on the right-zone X
        here. The launcher IS the back-stack root and never exits (its bar draws no X,
        so this is never reached with screen == "launcher"). A pre-Stage-6 shim over the
        screen strings: Settings closes via its own resume-or-home rule; the Editor (and
        any other taskbar app) goes home -- or back into the app that opened it
        (`_go_home_or_back`: the Editor Files opened on a project returns to the
        Files shelf, not to the launcher)."""
        if self.wm.top_is("settings"):            # Stage 6d: ask the stack top
            self._exit_settings()
        elif not self.wm.top_is("launcher"):
            self._go_home_or_back()

    def _init_apps(self):
        """Construct and register every SYSTEM APP from its declaration.

        There is no per-app line in this file. `app_decls.APPS` is GENERATED
        from the `app` blocks in `system_carts/*/manifest.json`, so adding an
        app is a manifest plus a module -- never an edit here
        (docs/app_api_v1.md). The `<id>_app` attributes are kept because the
        shell and the apps address each other by them (`settings_layer` opens
        `ws.appearance_app`), and because 100+ call sites use them.

        Declaration order IS dispatch precedence, which is why `order` lives in
        the manifest rather than being implied by a dict.
        """
        for d in APPS:
            cls = _resolve_app_entry(d["entry"])
            app = cls(self.app_context(d["id"], getattr(cls, "NEEDS", ()),
                                       prefs_ns=getattr(cls, "PREFS_NS", None)),
                      NAMES)
            setattr(self, str(d["id"]) + "_app", app)
            # Paint's document model is the console's reach into Paint (the
            # backdrop's My Art, the image door, the Paint cart's identity).
            service = getattr(app, "service", None)
            if service is not None:
                self.artwork = service
            ms = d.get("min_size")
            self.register_app(app, text_mode=bool(d.get("text_mode")),
                              min_size=(tuple(ms) if ms else None))

    def register_app(self, app, text_mode=False, min_size=None):
        """Register a SYSTEM APP (docs/app_api_v1.md). `app` is a content Layer
        exposing:

          id            -- the process kind (router / back-stack / window key)
          is_app(cart)  -- claim a launcher cart as this app's identity
          open()        -- (re)enter the app on every launch
          relayout(w, h, fs)  -- adopt a new canvas size / font scale

        `text_mode=True` marks a TYPING app (clean ASCII keyboard);
        `min_size=(w, h)` is the windowed-WM resize minimum in fs-scaled
        units (the ui.py convention). When omitted, MIN_W/MIN_H on
        the app's layout are adopted. TITLE supplies window/taskbar text. A
        launcher tap on the claimed cart opens the app instead of the Player;
        everything else (window chrome, theme tokens, toolkit) comes free."""
        if min_size is None:
            layout = getattr(app, "layout", None)
            min_w = getattr(layout, "MIN_W", None)
            min_h = getattr(layout, "MIN_H", None)
            if min_w is not None and min_h is not None:
                min_size = (int(min_w), int(min_h))
        h = self.apps.register(app.id, getattr(app, "TITLE", app.id.upper()),
                               text_mode, min_size)
        self._apps.append((app, self.apps.text_mode(h)))
        self._apps_by_id[app.id] = app
        self._content_layers[app.id] = app
        hook = getattr(self.wm, "on_app_registered", None)
        if hook is not None:
            hook(app)
        # The picker HIDES carts an app claims (_picker_items, temporary until
        # #181), but apps register AFTER the store scan builds the grid -- so
        # re-derive it here or the first-built grid keeps the app carts it was
        # built without knowing about. Cheap: a handful of app registrations at
        # boot, each a list rebuild over the already-scanned carts.
        _carts = getattr(self, "carts", None)
        if _carts is not None and _carts.all and getattr(self, "picker", None):
            self.picker.set_items(self._picker_items(_carts.all))

    def app_min_size(self, kind):
        """The registered windowed resize minimum for app `kind`, or None."""
        h = self.apps.find(kind)
        return self.apps.min_size(h) if h else None

    def app_title(self, kind):
        """The registered app's requested window/taskbar title, or None."""
        h = self.apps.find(kind)
        return self.apps.title(h) if h else None

    def open_app(self, app, cart=None):
        """Spawn a registered system app on `cart` (default: the cart its
        is_app claims) -- the ONE app-launch dispatch, used by the launcher
        tap above and by app-to-app jumps (e.g. Files' OPEN -> Paint). A
        TYPING app (register_app text_mode=True) gets the clean ASCII
        keyboard after it opens; the rest are set to button mode BOTH ways, so
        a jump out of a typing app restores the raw keyboard. Returns False
        when no cart carries the app's identity."""
        if cart is None:
            for c in self.carts.all:
                if app.is_app(c):
                    cart = c
                    break
            if cart is None:
                return False
        h = self.apps.find(app.id)
        text = self.apps.text_mode(h) if h else False
        self._note_app_caller()        # a jump OUT of an app comes back to it
        self.cart = cart
        self.input.text_mode = False
        self.search_typing = False     # #105: an app jump ends any in-progress search typing
        app.open()
        self.wm.goto(app.id)
        self._set_text_mode(bool(text))
        self.ach.note("open", cart.get("path") or cart.get("title"))
        return True

    def is_system_app(self, cart):
        """True when a registered system app's identity claims `cart` -- the
        registry-side predicate (services use it to keep app carts out of
        project lists)."""
        for app, _text in self._apps:
            if app.is_app(cart):
                return True
        return False

    def wifi_hold(self, tag):
        """Take the radio for `tag` (2026-09-07): the WiFi is OFF unless
        something holds it, and this is the only way to turn it on. Powers the
        interface up at once -- every holder scans or connects next, and the
        ESP-NOW link must find the service owning the interface it activates.
        Idempotent per tag. The holders: "web" (wasm mode, released when its
        socket actually closes), "update" (the online update screen), "settings"
        (the WIFI panel), "cart" (a run with the "network" permission), "link"
        (a match), "carts" (the Get Carts app while it fetches). A new consumer
        of the network takes a tag here and releases it on its way out, or the
        radio never goes off again. The tags are `moy_spine.LEASE_TAGS`, and
        any other is refused (ValueError)."""
        w = self.wifi
        if w is None:
            self.leases.held(tag)          # refused even with no radio to hold
            return False
        self.leases.hold(tag)
        on = getattr(w, "radio_on", None)
        if on is None:
            return True
        try:
            return bool(on())
        except Exception:  # noqa: BLE001 -- a radio that will not start is offline
            return False

    def wifi_release(self, tag):
        """Let the radio go for `tag`; the last holder out powers it down.
        Releasing a tag never held is fine (every exit path releases without
        asking), and an empty set powers down even so: "off in general" is the
        rule, and whatever a serial `py` brought up without a lease goes with
        the next exit."""
        if self.leases.release(tag):
            return
        off = getattr(self.wifi, "radio_off", None)
        if off is not None:
            try:
                off()
            except Exception:  # noqa: BLE001 -- a radio that will not stop is a diag line
                pass
