# Map (grep -n a name to jump there):
#   Spine                the run and exit verbs, apps, the WiFi lease
#   Spine.load_system    read the system settings and apply them
#   Spine.play           show the front run's cart, recording its caller
#   Spine.exit           the taskbar app's X: back toward the launcher
#   Spine.register_app   a system app into the registry
#   Spine.open_app       the one app-launch dispatch
#   Spine.wifi_hold      take a radio hold by tag (and pid)
#   Spine.wifi_release   let a hold go
#   Spine.wifi_settle    the service's view let go when nothing holds
"""The kernel spine's half of the Workstation (docs/history/kernel_spine_2026-10.md):
the run and exit verbs, app registration and resolution, the WiFi lease and
the settings wiring: the console's `ws.spine`.

The state these verbs keep lives in C -- the spine's components
(native/moy_spine): `self.apps` (AppRegistry), `self.wm.stack` (BackStack),
`self.returns` (Returns) and `self.prefs.rows` (Settings), and the lease
table's radio row (native/moy_kernel/moy_lease.h): `self.leases` (Wifi) --
which hold ids, kinds, tags and JSON text, never a Python object. What stays here is the
Python side of each verb: the app objects (`app_layers`, `_apps_by_id`), the
surfaces a route lands on, and the radio service the lease powers.
"""

from app_decls import APPS
from chrome import DEFAULT_VARIANT, NAMES
from settings_layer import SETTINGS_TOGGLES
from moy_spine import (EDITOR, ROUTE_APP, ROUTE_EDITOR, ROUTE_WINDOW)
from ui_draw import AppHost
from layers import Layer
from achievements import is_proc as _is_ach_proc
import moy_app as _moy_app

# The radio's holders: a process's tags, each held under its pid, and the
# owner of the console's own (moy_lease.SHELL, the shell's pid).
PROC_TAGS = ("cart", "http", "media", "native", "net")
_SHELL = 1


def _resolve_app_entry(entry):
    """Resolve an app declaration's "module:Class" to the class itself, by
    the module's flat name (`import files_app`), which is `runtime.files_app`
    on the host."""
    mod_name, _, cls_name = entry.partition(":")
    return getattr(__import__(mod_name, None, None, (cls_name,)), cls_name)


class Spine:
    """The spine's verbs on the Workstation. `Workstation.__init__` creates the
    state they touch."""

    def __init__(self, ws):
        self.ws = ws

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

    _broken = None                  # (the settings rows' gen, the guard's broken ids)

    def broken_cart_ids(self):
        """The ids the crash guard has turned off (`app_guard.broken_ids`),
        read again only when the settings rows the guard keeps them in move:
        the home's cards ask it a paint."""
        ws = self.ws
        g = ws.system.gen()
        b = self._broken
        if b is None or b[0] != g:
            b = self._broken = (g, tuple(ws.app_guard.broken_ids()))
        return b[1]

    def cart_broken(self, cart):
        """True when the crash guard has turned this app cart OFF (#160): the
        home's Make tab marks its card (`home_grid._draw_broken_badge`).

        NOT what the Player reads -- it refuses through `app_guard.arm()`
        returning False, which is the same answer taken on the path that also
        records the strike. The cart stays on the home's Make tab either way,
        because editing it is how it gets fixed."""
        if not self.is_user_app(cart):
            return False
        return self.app_cart_id(cart) in self.broken_cart_ids()

    def forgive_app(self, cart):
        """Clear `cart`'s crash strikes -- the kid changed its CODE (#160).

        The other half of three-strikes, and without it the refusal panel's
        "EDIT it" was a dead end: nothing called `CrashGuard.forgive`, so the
        only ways back were renaming the cart or hand-editing `system/settings`.

        Called from `Project.commit_code`, and deliberately from there ALONE.
        A code commit is the one edit that can change whether the cart hangs,
        faults or exhausts the heap -- the failures the guard exists for, none
        of which a sprite, a map or a config tweak can fix or cause. Forgiving
        on every asset save would hand a boot-looping app a fresh set of
        strikes for repainting a tile; forgiving only on a hand-edited
        `system/settings` is what we had.

        Strikes are cleared, not decremented: the kid's next open starts from
        zero and gets the full three, exactly like a cart the guard has never
        seen. False when there was nothing to forgive."""
        ws = self.ws
        if not self.is_user_app(cart):
            return False
        return ws.app_guard.forgive(self.app_cart_id(cart))

    def forgive_wallpaper(self, cart):
        """Clear `cart`'s crash strikes AS THE BACKDROP (#160) -- `forgive_app`'s
        twin for the wallpaper role, called beside it from
        `Project.commit_code` for the same reason. The ledger is keyed by the
        wallpaper id; a cart with nothing on it writes nothing. The fixed code
        runs at the next boot or the next pick of it in Appearance."""
        ws = self.ws
        if cart is None or cart.get("type") != "wallpaper":
            return False
        return ws.wallpaper_guard.forgive(ws.look.wp_id_for(cart))

    def load_system(self):
        """Read the system settings (`self.prefs`) and APPLY them -- the font
        scale, the theme (which brings its wallpaper, D6) and every persisted
        toggle (#39).

        The read is the store's; this cascade is kernel policy and stays here.
        What a setting MEANS is the shell's business -- each one relays through
        the same `set_*` verb the Settings row calls, with persist=False so
        loading never re-writes what it just read."""
        ws = self.ws
        ws.prefs.load()
        # System font scale (#39): apply the persisted choice (1/2/3) so the desktop
        # boots at the saved text size. look.set_font_scale relays it into the system
        # canvas + relayouts; persist=False so loading doesn't re-write the store.
        # The chrome scale is read first so that one relayout carries both.
        ws.look.chrome_scale = ws.look.valid_chrome_scale(
            ws.system.get("chrome_scale", 0))
        ws.look.set_font_scale(ws.system.get("font_scale", ws.look.font_scale),
                                 persist=False)
        # Paint's shared document lives outside the re-seeded built-in cart:
        # restore My Art's bg asset (its cover on the shelf) from it.
        if ws.artwork is not None:
            ws.artwork.sync_wallpaper()
        # The theme brings the wallpaper (`Appearance.theme_wallpaper`); a SAFE
        # start (the kernel's recovery screen) runs no wallpaper cart.
        # A stored name no theme has (a seed step 27 of docs/history/look_2026-10.md
        # deleted, a package since removed) reads as absent: the default, in
        # its own mode, with nothing rewritten.
        name = ws.system.get("theme", ws.look.theme_name)
        ws.look.set_theme(name, persist=False,
                            variant=ws.system.get("theme_variant",
                                                    ws.look.theme_variant))
        if ws.look.theme_name != name:
            ws.look.set_theme_variant(DEFAULT_VARIANT, persist=False)
        # The kernel lights the next boot's glass on this theme's ground.
        from console import keep_splash
        keep_splash(ws.theme_colors)
        # Every persisted ON/OFF setting, through the verb its registry entry
        # names (#209 section 7): the key is the system/settings key, the default is
        # declared beside it, and a board that cannot serve one gets the honest
        # answer from the setter rather than a silent flag. A new toggle is one
        # registry entry, not a line here.
        for key, _label, default, setter, _gate, _dev in SETTINGS_TOGGLES:
            getattr(ws.toggles, setter)(ws.system.get(key, default), persist=False)
        # The bar's clock reads the stored time zone (UTC with none).
        ws.toggles.set_time_zone(ws.system.get("tz"), persist=False)
        # A key a retired setting left behind (#217 took FRAMESKIP) would ride
        # every write forever; drop it once.
        ws.system.delete("frameskip")

    def apply_system_keys(self, keys):
        """Re-apply the Settings rows `keys` after something other than this
        console changed them (the page a board serves: moy_sync.merge_system),
        through the verbs `load_system` calls, persist=False. The rows the
        console reads where it uses them (the name, the favorites, an app's
        prefs) need nothing; what shows changes at the next painted frame."""
        ws = self.ws
        if "theme" in keys or "theme_variant" in keys:
            name = ws.system.get("theme", ws.look.theme_name)
            ws.look.set_theme(name, persist=False,
                                variant=ws.system.get(
                                    "theme_variant", ws.look.theme_variant))
            if ws.look.theme_name != name:   # as at boot: the default's mode
                ws.look.set_theme_variant(DEFAULT_VARIANT, persist=False)
        if "layout_theme" in keys:
            ws.look.apply_layout()
        for key, _label, default, setter, _gate, _dev in SETTINGS_TOGGLES:
            if key in keys:
                getattr(ws.toggles, setter)(ws.system.get(key, default),
                                      persist=False)
        ws.frame_state.dirty = True

    def play(self, caller):
        """Show the front run's cart on the desktop, recording `caller` so the exit
        gesture knows where to return (spec Section 2's run/return -- a stack discipline,
        not a blocking call, since the frame loop can't block). The cart itself is started
        by the explicit _start() at each call site (open/apply/run_code/_leave_menu);
        play() makes the desktop layer active + records the caller. The launcher home root
        is one caller (pop == go_home); the Editor is the second (Stage 3), so PLAY
        returns to the same tab -- proving the Player is caller-agnostic."""
        ws = self.ws
        if ws.run.player.cart_error is not None and ws.web.parked and ws.crash_to_code():
            return                     # a parked console goes home; else the stop card
        if ws.run.player.notice is not None:
            ws.pause.stop()          # a cart this console cannot run: its card over
            return                     # the screen it was opened from
        ws.crash_popup = None        # a clean launch retires any stale popup
        ws.pause.dismiss()           # ...and a stop card a run left
        kind = self._caller_kind(caller)
        ws.returns.run(kind)
        # #178: the caller already says WHY this run is starting, so the windowed
        # tier can size the playtest window for the job -- an Editor PLAY is a dev
        # action (small, beside the code), a desk/Library run is play (as big as
        # fits). Stamped BEFORE the push that creates the window.
        ws.wm.set_play_intent("dev" if kind == EDITOR else "play")
        ws.wm.goto("desktop")        # Stage 6e: push the Player process onto the back-stack

    def _caller_kind(self, caller):
        """The kind a run started by `caller` records: the Editor's for the
        Editor, a surface's own id otherwise, None for anything without one."""
        ws = self.ws
        if caller is None:
            return None
        if isinstance(caller, str):
            return caller                  # already a kind (`_return_to_runner`)
        if caller is ws.editor_app:
            return EDITOR
        kind = getattr(caller, "id", None)
        if isinstance(kind, str) and 0 < len(kind.encode()) <= 15:
            return kind
        return None

    def exit_to_caller(self):
        """Pop the running cart back to whoever launched it (run()'s recorded caller,
        spec Section 2's launch-and-return). The pause menu's Quit, quit()
        and a tool's bar X call this. The Editor is the second caller
        (Stage 3b): a cart run from PLAY returns to the Editor on the tab it left
        (screen -> "menu"; editor_app.tab is preserved -> the SAME tab), proving the
        Player has zero knowledge of who launched it. A registered APP is the
        third (Files opening a note in the Notes cart, step 3 of
        docs/theme_and_shell_2026-11.md §10): it pops back to that app's own surface,
        on the shelf the person was standing on. Any other caller (the launcher
        home root, or None) pops all the way home. A PROJECT FILE edit (step 5)
        is the Editor arm with a reload in front of it -- see
        `_return_to_project`."""
        # Drop the dead run's world NOW (#66 repeat-run fragmentation fix): the
        # next cart must build into a compact heap, not around this one's corpse
        # (see Player.release_world's docstring for the measured mechanism).
        ws = self.ws
        ws.run.player.release_world()
        ws.pause.dismiss()           # a stop card goes with its run
        ran = ws._script_return
        if ran is not None:
            # A script an editor handle ran: back into the cart that ran it,
            # whose own caller (and `_project_return`) this run left alone.
            ws._script_return = None
            ws.returns.spend()
            ws._return_to_runner(ran)
            return
        back = ws._project_return
        ws._project_return = None
        # A run's caller is SPENT by its return. Leaving it set is what wedged
        # Files (#108, on glass): the returned-to APP's own context X
        # routes here too, and a stale caller made it `goto` the app already on
        # top -- a no-op, so Files could never be left again once it had opened
        # a note.
        rt = ws.returns
        if back is not None:
            rt.spend()
            ws._return_to_project(back)
            return
        # Windowed WM (#73): closing the playtest must never truncate unrelated
        # windows stacked above it (e.g. Settings) -- the WM removes ONLY the
        # player and refocuses the caller's window. Desktop mode only: a
        # console-mode game (launched from the fullscreen Library) pops by the
        # fullscreen rules below, landing back in the Library via go_home.
        _cp = getattr(ws.wm, "close_player", None)
        route = rt.route(_cp is not None and ws.wm.desk_open())
        if route == ROUTE_WINDOW:
            ws.frame_state.dirty = True
            _cp()                          # reads the caller to refocus
            rt.spend()
            return
        caller = rt.spend()
        if route == ROUTE_EDITOR:
            ws.frame_state.dirty = True             # screen change repaints (#44)
            ws.wm.goto("menu")           # Stage 6e: pop the Player, back to the Editor tab
            return
        if route == ROUTE_APP and self._return_to_app(caller):
            return
        self._go_home_or_back()

    def _return_to_app(self, app_id):
        """Land back on registered app `app_id`'s own surface, on the row it was
        showing. A back-stack RETURN and never a re-open: `open_app` would call
        the app's `open()`, which resets it to its root -- and coming back to a
        different screen than you left is exactly what a return must not do."""
        ws = self.ws
        app = ws.app(app_id)
        if app is None:
            return False
        ws.frame_state.dirty = True
        ws.wm.goto(app_id)
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
        ws = self.ws
        back = ws.returns.take_back()
        ws.home.go_home()                     # the leaving surface's save + teardown
        if back is not None and ws.wm.top_is("launcher"):
            self._return_to_app(back)

    def note_app_caller(self):
        """Record the registered APP this navigation is leaving, so whatever it
        opens comes back to it. Called at the app-to-app jumps -- `open_app`
        and the two `ctx.nav` editor doors.

        It only ever SETS. A jump from somewhere that is not an app deepens a
        journey rather than starting one (Files -> a project's Editor -> that
        cart's cover in Paint is still on its way back to Files), and clearing
        there would strand the shelf. `go_home` is the one reset, because the
        launcher root is where every return path ends."""
        ws = self.ws
        ws.returns.note(ws.wm.top_kind())

    def exit(self):
        """Exit the active TASKBAR app back toward the launcher root (spec Section 9's
        context X, Stage 5): BarLayer.handle_bar_tap routes a tap on the right-zone X
        here. The launcher IS the back-stack root and never exits (its bar draws no X,
        so this is never reached with screen == "launcher"). A pre-Stage-6 shim over the
        screen strings: Settings closes via its own resume-or-home rule; the Editor (and
        any other taskbar app) goes home -- or back into the app that opened it
        (`_go_home_or_back`: the Editor Files opened on a project returns to the
        Files shelf, not to the launcher)."""
        ws = self.ws
        if ws.wm.top_is(ws.settings_layer.id):  # Stage 6d: ask the stack top
            ws.exit_settings()
        elif not ws.wm.top_is("launcher"):
            self._go_home_or_back()

    def _init_apps(self):
        """Construct and register every SYSTEM APP from its declaration.

        There is no per-app line in this file. `app_decls.APPS` is GENERATED
        from the `app` blocks in `system_carts/*/manifest.json`, so adding an
        app is a manifest plus a module -- never an edit here
        (docs/app_api_v1.md). The shell addresses an app as `ws.app(id)`
        (`settings_layer` opens `ws.app("appearance")`).

        Declaration order IS dispatch precedence, which is why `order` lives in
        the manifest rather than being implied by a dict.
        """
        for app, ms, cart in self._declared_apps():
            self.register_app(app, min_size=ms, cart=cart)

    def _declared_apps(self):
        """(app, min_size, cart) for each declared app, built as it is
        reached, then the shell's own two, which no cart is: Settings (its
        context carries the radios and the USB drive) and the task manager."""
        ws = self.ws
        for d in APPS:
            cls = _resolve_app_entry(d["entry"])
            ctx = ws.app_context(d["id"], getattr(cls, "NEEDS", ()),
                                   prefs_ns=getattr(cls, "PREFS_NS", None))
            if getattr(cls, "UI", False):
                # an app on the public UI API's verbs
                ctx.ui = AppHost(d["id"], getattr(ctx, "surface", None))
                ws._app_hosts[d["id"]] = ctx.ui
            app = cls(ctx, NAMES)
            # Paint's document model is the console's reach into Paint (the
            # backdrop's My Art, the image door, the Paint cart's identity).
            service = getattr(app, "service", None)
            if service is not None:
                ws.artwork = service
            ms = d.get("min_size")
            yield app, (tuple(ms) if ms else None), True
        yield ws.settings_layer, None, False
        yield ws.tasks_layer, None, False

    def register_app(self, app, min_size=None, cart=True):
        """Register a SYSTEM APP (docs/app_api_v1.md). `app` is a content Layer
        `layers.AppLayer`, exposing:

          id            -- the process kind (router / back-stack / window key)
          is_app(cart)  -- claim a launcher cart as this app's identity
          open()        -- (re)enter the app on every launch
          relayout(w, h, fs)  -- adopt a new canvas size / font scale

        and the input protocol `layers.Layer` defaults (its text mode is what
        its `typing` answers); `min_size=(w, h)` is the windowed-WM resize minimum in fs-scaled
        units (the ui.py convention). When omitted, MIN_W/MIN_H on
        the app's layout are adopted. TITLE supplies window/taskbar text. A
        launcher tap on the claimed cart opens the app instead of the Player;
        everything else (window chrome, theme tokens, toolkit) comes free.
        `cart` False: no cart is the app (Settings, the task manager), so it
        joins the router, the app table and `ws.app`, but not `app_layers`,
        the apps a launcher tap, the desk's tiles and the Apps tab walk."""
        ws = self.ws
        if min_size is None:
            layout = getattr(app, "layout", None)
            min_w = getattr(layout, "MIN_W", None)
            min_h = getattr(layout, "MIN_H", None)
            if min_w is not None and min_h is not None:
                min_size = (int(min_w), int(min_h))
        if not isinstance(app, Layer):
            raise TypeError("an app is a layers.AppLayer: " + str(app.id))
        ws.apps.register(app.id, getattr(app, "TITLE", app.id.upper()), min_size)
        if cart:
            ws.app_layers.append(app)
        ws._apps_by_id[app.id] = app
        ws._content_layers[app.id] = app
        hook = getattr(ws.wm, "on_app_registered", None)
        if hook is not None:
            hook(app)
        # Make HIDES carts an app claims (_make_items, temporary until #181),
        # and an app can register after the home window's grid was built: re-
        # derive it, or it keeps the app carts it was built without knowing about.
        _g = getattr(ws, "projects_grid", None)
        if _g is not None:
            _g.set_items(ws.home.launcher_view_items(_g))

    def app_min_size(self, kind):
        """The registered windowed resize minimum for app `kind`, or None.
        The Editor's window stops at its workspace's combined minimum while
        the desk Editor shows workspaces (`EditorDesk.min_window`)."""
        ws = self.ws
        if kind == "menu":
            desk = ws.editor_desk
            return desk.min_window() if desk is not None and desk.active() else None
        h = ws.apps.find(kind)
        return ws.apps.min_size(h) if h else None

    def app_title(self, kind):
        """The registered app's requested window/taskbar title, or None."""
        ws = self.ws
        h = ws.apps.find(kind)
        return ws.apps.title(h) if h else None

    def open_app(self, app, cart=None):
        """Spawn a registered system app on `cart` (default: the cart its
        is_app claims) -- the ONE app-launch dispatch, used by the launcher
        tap above and by app-to-app jumps (e.g. Files' OPEN -> Paint). A jump
        in console mode leaves the surface on top (`leave_surface`). On the desk an
        app whose window is open and no cart named comes forward as it was
        (`wm.bring_forward`); one opened on a cart opens it in that window,
        the windows above left open (`wm.activate`). Returns False
        when no cart carries the app's identity."""
        ws = self.ws
        if ws.usb_drive_refuses():
            return False
        ws.close_popover()
        if cart is None:
            if ws.wm.bring_forward(app.id):
                return True            # its window, as it was, in front
            for c in ws.carts.all:
                if app.is_app(c):
                    cart = c
                    break
            if cart is None:
                return False
        if not ws.windowed_chrome:
            ws.leave_surface(ws.wm.top_kind())   # an app switch is an exit
        self.note_app_caller()        # a jump OUT of an app comes back to it
        ws.app_carts[app.id] = cart
        if not self._project_held():
            ws.run.project.cart = cart
        ws.search_typing = False     # #105: an app jump ends any in-progress search typing
        app.open()
        ws.wm.activate(app.id)
        ws.ach.played(cart.get("path") or cart.get("title"))
        return True

    def _project_held(self):
        """Whether the open project is another surface's: a run plays it (in
        a desk window, parked behind console mode, or on a handheld's
        back-stack) or the Editor has it open or parked. An app opened then
        leaves it as it is."""
        ws = self.ws
        wm = ws.wm
        if ws.procs._front_live():
            return True
        pl = ws.run.player
        if (wm.is_parked("desktop") and pl.cart_error is None
                and (pl.update is not None or pl.draw is not None)):
            return True
        ed = getattr(ws, "editor_app", None)
        if ed is None or ed.project is not ws.run.project:
            return False
        wins = getattr(wm, "windows", None)
        return (("menu" in wins) if wins is not None else wm.stack.has("menu")) \
            or wm.is_parked("menu")

    def is_system_app(self, cart):
        """True when a registered system app's identity claims `cart`, or it
        is the achievements process's -- the registry-side predicate
        (services use it to keep the console's own carts out of project
        lists and App permissions)."""
        ws = self.ws
        if _is_ach_proc(cart):
            return True
        for app in getattr(ws, "app_layers", ()):
            if app.is_app(cart):
                return True
        return False

    def wifi_hold(self, tag, pid=None):
        """Take the radio for `tag`: the WiFi is OFF unless
        something holds it. The hold is a row of the lease table's radio book,
        and on a board the kernel's WiFi driver powers the station from that
        book (native/moy_net/moy_net.h's THE BOOK), as it does for a hold a
        compiled program or a C service takes; `radio_on` then makes the
        service's view of the interface at once -- every holder scans or
        connects next, and the ESP-NOW link must find the service owning the
        interface it activates.
        Idempotent per tag and holder. The console's own holders: "web" (wasm
        mode, released when its socket actually closes), "update" (the online
        update screen), "settings" (the WIFI panel, through its wifi role),
        "link" (a match), "carts" (the Get Carts app while it fetches), "ssh"
        (the SSH server while it
        serves, ssh_service.py). A process's, held with its `pid`
        (`PROC_TAGS`): "cart" (a run with the "network" permission), "http"
        (its `moybyte.http` requests while one runs, dev_http.py, or a
        compiled program's http handles from its first to its last, held in
        the kernel, native/moy_net/moy_fetch_dev.c), "media"
        (the media service's run, under the run's pid, while a URL of its
        list may play, dev_media.py or a compiled program's media handle,
        native/moy_media/moy_media_dev.c), "native"
        (a native process from its first socket to its end, elf_run.py),
        "net" (its radio link's ports, from the first open to the last
        close, dev_handles.py); its
        end lets exactly its holds go (the lease table's sweep, then
        `wifi_settle`). A new consumer of the network takes a tag here and
        releases it on its way out, or the radio never goes off again. The
        holders are the lease table's radio rows (`moy_lease.Wifi`), its tags
        `moy_lease.WIFI_TAGS`, and any other is refused (ValueError), as is a
        process's tag with no pid; a table with no row left is a radio that
        cannot start (False)."""
        ws = self.ws
        owner = self._wifi_owner(tag, pid)
        w = ws.wifi
        if w is None:
            ws.leases.held(tag)          # refused even with no radio to hold
            return False
        try:
            ws.leases.hold(tag, owner)
        except OSError:
            return False
        on = getattr(w, "radio_on", None)
        if on is None:
            return True
        try:
            return bool(on())
        except Exception:  # noqa: BLE001 -- a radio that will not start is offline
            return False

    def wifi_release(self, tag, pid=None):
        """Let the radio go for `tag` (a process's tag: process `pid`'s hold,
        or with pid 0 every process's, for a run that ended in the kernel);
        the last holder out powers it down (the book's, on a board). Releasing
        a tag never held is fine (every exit path releases without asking),
        and an empty set lets the service's view go even so (`radio_off`,
        which on the host and a port's network.WLAN powers down too)."""
        ws = self.ws
        if ws.leases.release(tag, self._wifi_owner(tag, pid, True)):
            return
        self._radio_off()

    def wifi_settle(self):
        """The service's view let go when nothing holds the radio: what a
        process's end runs after the lease table let its holds go (the
        book powered the station down with them, on a board)."""
        ws = self.ws
        if not ws.leases.mask():
            self._radio_off()

    def _wifi_owner(self, tag, pid, release=False):
        if tag not in PROC_TAGS:
            return _SHELL
        if pid is None or (pid == 0 and not release):
            raise ValueError("the radio's %s tag is a process's: name it" % tag)
        return pid

    def _radio_off(self):
        ws = self.ws
        off = getattr(ws.wifi, "radio_off", None)
        if off is not None:
            try:
                off()
            except Exception:  # noqa: BLE001 -- a radio that will not stop is a diag line
                pass
