# Map (grep -n a name to jump there):
#   Home                     the home's verbs: launch, the desk, Projects, the tabs, search
#   Home.launch_named        run a cart by name and answer its title
#   Home.go_home             land on this mode's root
#   Home.launcher_items      the home's tab over the roster
#   Home._home_grid          the card model for an arrangement
#   -- the resume record          where a reboot puts the home back
#   -- the desk                   desktop mode's root, and PLAY into console mode
#   -- the home in a window       the desk's PROJECTS
#   -- the home's cards           the tabs, the search, the arrangement
"""The Workstation's HOME verbs: launching from a card (`launch_selected`,
`launch_named`), the resume record a reboot lands on, the desk and the home
in a window (`open_desk`/`open_library`/`open_projects`, `new_cart_and_edit`),
the HOME key and the landing on this mode's root (`home_key`/`go_home`), and
the home's cards -- each tab's items over the roster, the arrangement's card
model, the tab and search state and the title the bar shows. The console's
`ws.home`; the home's instance (`ws.launcher`) and its layers are built in
console.py.
"""

from home_icons import IconHome
from home_carousel import Carousel
from home_grid import (Launcher, new_tile, NEW_TILE_TYPE, PSEUDO_TILE_TYPES,
                       HOME_TABS, tab_count_title, PROJECTS_VERB_SETS)
from project import Project
from chrome import NAMES
import scheduler as _scheduler
import cron as _cron
from achievements import is_proc as _is_ach_proc

RESUME_MAX = 512        # the kernel's resume record's bytes (MOY_KERNEL_RESUME_MAX)


def _with_cron(rec, json):
    """Record `rec`'s text with what cron carries through the stop
    (cron.held), as many at jobs as fit RESUME_MAX, soonest first; one
    that does not fit is logged and left behind."""
    held = _cron.CRON.held()
    jobs = held["at"]
    if held["due"] is None and not jobs:
        return json.dumps(rec)
    rec["cron"] = c = {"due": held["due"], "at": []}
    text = json.dumps(rec)
    for i, job in enumerate(jobs):
        c["at"].append(job)
        more = json.dumps(rec)
        if len(more.encode()) > RESUME_MAX:
            c["at"].pop()
            print("Moybyte resume: %d at jobs do not fit the record" % (len(jobs) - i))
            break
        text = more
    return text


# The home's arrangements by the layout's `home` value
# (docs/theme_and_shell_2026-11.md §8.10); an unknown one is the carousel.
_HOME_CLASSES = {"carousel": Carousel, "grid": Launcher, "icons": IconHome}


class Home:

    def __init__(self, ws):
        self.ws = ws

    def launch_selected(self, grid=None):
        """The home's tap on `grid`'s selected card (the home's, ws.launcher,
        by default), by tab (docs/theme_and_shell_2026-11.md §8.10): Games and
        Mine RUN it, Apps opens the app (`open` dispatches an app's cart to
        it), Make opens it in the Editor (`open_in_editor`: a window of its
        own on a board with a desk, from console mode too). The "+ New"
        card -- Make's first, an empty Mine's only -- makes a game and opens
        it in the Editor."""
        ws = self.ws
        grid = grid or ws.launcher
        sel = grid.selected()
        if sel is not None and sel.get("type") == NEW_TILE_TYPE:
            self.new_cart_and_edit(grid)
            return
        if grid.tab == "make":
            if sel is not None:
                ws.open_in_editor(sel)
            return
        ws.open(sel)

    def launch_named(self, name):
        """Run the cart `name` names, and return its TITLE (None if none match).

        The ONE lookup behind every remote launch: the serial dev channel's
        `run`, and the browser's PLAY ON DEVICE (moy_webhost's POST /run). Both
        arrive with a string somebody else chose, and the two obvious strings
        disagree -- the browser knows the cart by its TITLE (that is what rides
        every frame payload) and a human at a serial prompt types part of a
        folder name (`star_catcher` for `moybyte.star_catcher.moy`). So both
        are accepted, exact before partial:

            exact title -> exact folder (with or without .moy) -> title or
            folder substring, in shelf order

        An empty `name` runs the first real cart, which is the dev channel's
        established `run` with no argument. The "+ New" card (it carries no
        store path) is never a candidate."""
        # str(): `name` comes off a JSON body a browser wrote, so it is only a
        # string by convention -- a number there must miss, not raise inside the
        # frame loop's tail.
        ws = self.ws
        want = str(name or "").strip().lower()
        items = self.runnable_items()
        exact_title = None
        exact_folder = None
        partial = None
        for i in range(len(items)):
            it = items[i]
            path = it.get("path")
            if not path:
                continue                       # a pseudo tile is not a cart
            if not want:
                partial = i
                break
            title = str(it.get("title") or "").lower()
            folder = path.rsplit("/", 1)[-1].lower()
            if title == want and exact_title is None:
                exact_title = i
            elif (folder == want or folder == want + ".moy") \
                    and exact_folder is None:
                exact_folder = i
            elif (want in title or want in folder) and partial is None:
                partial = i
        pick = exact_title
        if pick is None:
            pick = exact_folder
        if pick is None:
            pick = partial
        if pick is None:
            return None
        self.select_cart(items[pick])
        ws.open()
        # Return something TRUTHY whenever a launch actually happened, so a
        # caller (moy_webhost's /run) can tell "nothing matched" (None, above)
        # from "launched a cart whose title is empty" -- a titleless cart used to
        # report as a no-match AFTER it had already started on the glass.
        it = items[pick]
        return it.get("title") or it.get("path", "").rsplit("/", 1)[-1] or "cart"

    # -- the resume record (docs/history/kernel_cartpath_2026-10.md 5.4) --------------

    def resume_record(self):
        """The launcher's place as the resume record's text: the selection,
        the shelf's scroll, the search and the selected cart's folder (the
        selection is found by it again, in case the shelf moved), the paths
        of the Python services running, which the console after the stop
        starts again (scheduler.pause), and the services on tasks of their
        own, which run on through it and that console takes back
        (`kept_rows`, scheduler.take_back)."""
        ws = self.ws
        import json
        lw = ws.launcher
        sel = lw.selected() or {}
        rec = {"tab": lw.tab, "sel": int(lw.sel),
               "search": ws.search_query, "path": sel.get("path"),
               "svc": _scheduler.SCHED.running_paths(),
               "keep": _scheduler.SCHED.kept_rows()}
        return _with_cron(rec, json)

    def resume_launcher(self, text):
        """Land the launcher where the resume record left it. A record that
        will not read changes nothing."""
        ws = self.ws
        import json
        try:
            rec = json.loads(text)
            tab = rec.get("tab")
            if tab in [t for t, _l in HOME_TABS]:
                self.set_home_tab(tab)
            q = rec.get("search") or ""
            if q:
                self.set_search_query(q)
            items = ws.launcher.items or []
            sel = int(rec.get("sel", 0))
            path = rec.get("path")
            if path:
                for i in range(len(items)):
                    if items[i].get("path") == path:
                        sel = i
                        break
            ws.launcher.sel = max(0, min(sel, len(items) - 1))
            ws.frame_state.dirty = True
            svc = rec.get("svc")
            if isinstance(svc, list):
                _scheduler.SCHED.resumed = tuple(p for p in svc if isinstance(p, str))
            _scheduler.SCHED.kept = _scheduler.kept_of(rec)
            _cron.CRON.carry(rec.get("cron"))
        except (ValueError, TypeError, AttributeError) as exc:
            print("Moybyte resume: %s" % exc)

    # -- the desk (the windowed tier's DESKTOP mode, and the way to console) --

    def open_desk(self):
        """Show the DESK -- desktop mode's root: wallpaper + system icons +
        taskbar, every app a window, and no X (nothing is beneath it). From
        console mode it is the home's X (`wm.leave_console`), which brings the
        parked windows back; on the desk it closes every window. The boot's
        landing on the windowed tier; fullscreen tiers never call this."""
        ws = self.ws
        ws.frame_state.dirty = True
        ws.search_typing = False     # leaving the home ends any query typing
        # Re-derive the shelf under the desk-tier filter (system apps are
        # desk icons here): the first call runs before the WM swap finished
        # populating anything, so the boot-time entry settles the filter.
        ws.launcher.set_items(self.launcher_view_items())
        if ws.wm.mode == "console":
            ws.wm.leave_console()
        else:
            ws.wm.goto("desk")

    def open_library(self):
        """The desk's PLAY icon: console mode over the home
        (docs/theme_and_shell_2026-11.md §8.1). The WM ends a running playtest
        and parks the desk's windows, and go_home lands on the home without
        touching them. On a console-only WM it is go_home."""
        ws = self.ws
        ws._editor_from = None
        ws.wm.enter_console()
        self.go_home()

    def change_selected(self, grid=None):
        """CHANGE (visual identity v1 Sections 1.2-1.3): open `grid`'s (the home's
        by default) selected cartridge IN PLACE in the Studio/Editor, landing on
        Config (or the gentlest editable tab when the cart has no edit schema --
        editor_app.open decides, deterministically per project). The bridge from
        playing to making: same Project, same persistence path, never a surprise
        duplicate (COPY stays an explicit Make verb). No-op for the "+ New" card
        (one verb: its tap)."""
        ws = self.ws
        sel = (grid or ws.launcher).selected()
        if sel is None or sel.get("type") in PSEUDO_TILE_TYPES:
            return
        ws.open_in_editor(sel)

    # -- the home in a window (the desk's PROJECTS) --------------------------

    def open_projects(self):
        """The desk's PROJECTS (docs/theme_and_shell_2026-11.md §8.10): the home in a
        window on its Make tab, the console home's design (a `Carousel`: the
        hero, its details, the shelf) whatever the fullscreen home's
        arrangement. Its card model is minted on the first open; a later
        open brings the window back to Make on the card that tab last had.
        Where no desk is open (console mode, a console-only board) it is the
        home's own Make tab."""
        ws = self.ws
        if not ws.wm.desk_open():
            self.go_home()
            self.set_home_tab("make")
            return
        ws.frame_state.dirty = True             # screen change repaints (#44)
        ws.search_typing = False     # the query is the fullscreen home's
        g = ws.projects_grid
        if g is None:
            g = ws.projects_grid = self._home_grid(
                "carousel", self.launcher_items(ws.carts.all, "make"))
            g.tab = "make"
            g.verb_sets = PROJECTS_VERB_SETS   # Make's Play, its pane's rows
        else:
            self.set_home_tab("make", g)
        ws.projects_layer._tab_focus = False
        ws.wm.activate("projects")

    def new_cart_and_edit(self, grid=None):
        """The "+ New" card: create a fresh GAME cart (`carts.new()` does the
        create, the re-scan and the grids), then open it in the Editor. A no-op
        on a read-only store (a device without SD writes) or a failed write --
        `carts.new()` returns None and the home simply stays up."""
        ws = self.ws
        new = ws.carts.new()
        if new is None:
            return
        # Select the new cart on the home that made it, so backing out of the
        # Editor lands on the freshly-created project.
        grid = grid or ws.launcher
        for i, it in enumerate(grid.items):
            if it.get("path") == new.get("path"):
                grid.sel = i
                break
        ws.open_in_editor(new)

    def home_key(self):
        """The HOME key (owner, 2026-10-13: back out one level, everywhere).
        In desktop mode it shows the desktop -- every window minimised to its
        chip, kept, and HOME again brings them back (`wm.show_desk`); it never
        closes a window. In console mode it goes home, and from the home on a
        board whose desk waits beneath it, to the desktop."""
        ws = self.ws
        wm = ws.wm
        if wm.mode == "desktop":
            wm.show_desk()
            ws.frame_state.dirty = True
        elif not wm.top_is("launcher"):
            self.go_home()
        elif ws.bar_layer.show_x("home"):
            self.open_desk()

    def go_home(self):
        """Land on this mode's root (`wm.root_kind`): the home in console mode,
        the desk in desktop mode, closing what stood above it. Every leaving
        surface is persisted first. In console mode a parked Editor that still
        holds the workspace is not torn down -- go_home touches no parked
        window (docs/theme_and_shell_2026-11.md §8.1)."""
        ws = self.ws
        ws.frame_state.dirty = True             # screen change repaints (#44)
        ws.close_popover()
        # The launcher root ends every return path: an app-to-app return that
        # outlived its journey here would drag an unrelated later exit into
        # Files. `_go_home_or_back` pops the slot BEFORE calling this.
        ws.returns.take_back()
        ws._script_return = None
        ws._project_return = None    # the project-file edit it guarded is over too
        # (#111) autosave-only: going home is an exit path for every persistent
        # system app + the Editor, so each is persisted BEFORE the state below is
        # torn down (self.editor/self.run.project etc.) -- a HOME-key tap reaches
        # here directly, bypassing any per-app CLOSE affordance, and must never
        # drop an edit still sitting in its idle-debounce window.
        #
        # This used to be a hand-written ladder naming four apps and four
        # DIFFERENT verbs (writer.flush / the story's deck commit / sheets.flush /
        # artwork._save) -- the bar-contract bug class one level down: an app that
        # persists on a debounce and is not on the list loses the kid's work,
        # silently. `close()` is the uniform LEAVING hook now
        # (docs/app_api_v1.md), reached through `leave_surface`; it is
        # change-gated, so this costs nothing for a surface with nothing
        # pending, and a future app gets it by implementing one method
        # instead of editing this file.
        for _app in ws.app_layers:
            ws.leave_surface(_app.id)
        ws.leave_surface("settings")    # a Name still typed, its radio lease
        _editor_app = getattr(ws, "editor_app", None)
        if _editor_app is not None and (ws.text.editing_icons
                                        or _editor_app.project is ws.run.project):
            ws.leave_surface("menu")
        # The parked Editor's workspace (console mode): its window comes back
        # to it, so neither it nor the run state it holds is torn down.
        keep = ws.wm.is_parked("menu") and (
            ws.text.editing_icons or (_editor_app is not None
                                    and _editor_app.project is ws.run.project))
        if not keep:
            ws.editor = None
            ws.paint = None
            if _editor_app is not None:
                _editor_app._drop_theme()  # never carry the theme mode home
            for _ui in ("map_ui", "scene_ui", "block_ui"):
                _ui = ws.built(_ui)        # one never built is fresh already
                if _ui is not None:
                    _ui.reset()
        ws.wm.goto(ws.wm.root_kind())   # this mode's root, everything above it gone
        if not keep:
            ws.cart = None
            # #66 fragmentation fix: ns = None alone kept the world alive through
            # player.update's closure; release clears the dict in place + collects.
            ws.run.player.release_world()
            # #66 pin-field fix (the repeat-run CLIFF's other half): going HOME drops
            # the whole workspace. The fat cart re-slims NOW (not at the next open --
            # reopening the SAME cart otherwise stays fat forever, and its rehydrated
            # src/sprites strings are mid-heap pins that fragment every later run's
            # churn arena; reopen costs one SD read) and a FRESH empty Project
            # replaces the old one's sheet/tilemap/images/pmem (ws.run.project is
            # never-None by design, so surfaces keep their invariant). The EDITOR
            # return path (\exit_to_caller -> menu) keeps both -- that's live
            # editing state, not a corpse.
            _fat = ws.run.fat_cart
            if _fat is not None:
                ws.carts.reslim(_fat)
                ws.run.fat_cart = None
            ws.run.project = Project(ws)
        ws.cart_error = None
        ws.ach_ui._konami_pos = 0          # fresh Konami run on the home desktop (#21)
        ws.ach_ui._clock_taps = 0
        # #197: while WASM MODE is on, HOME is the connection screen. Here and
        # not at each exit site, because "return to the launcher" has many doors
        # -- the bar's context X, the pause menu's Quit, an app's own
        # close, a crash -- and every one of them funnels through this method. A
        # per-door re-park is the bug class that left the T-Deck without a web
        # console at all; this is the last line of the one door they share.
        if ws.web.parked:
            ws.wm.goto("webconsole")

    # -- the home's cards: the tabs, the search, the arrangement --------------

    def launcher_items(self, carts, tab=None):
        """The home's `tab` (the active one by default) over `carts`
        (docs/theme_and_shell_2026-11.md §8.10), the four without overlap:

          games  the installed carts that play -- the seeds and the store's --
                 that are not the kid's own (`CartManager.is_own`), not a
                 registered app's and not app- or tool-typed;
          mine   the kid's own carts (made, or taken to make their own);
          apps   the registered apps' carts (minus the WM's HIDDEN_APPS),
                 then the installed app- and tool-typed carts no app claims;
          make   "+ New" first, then every editable cart (`_make_items`).

        The first three hold only what runs here (`runs_here`): a cart
        requiring a device this console lacks is no run tile.

        WALLPAPERS play in none of the first three: they are a backdrop chosen
        in Settings (Make lists them). THEMES are in none of the four: a theme
        is data the Appearance app picks (docs/history/theming_2026-09.md §9 phase 6
        step 2). An empty Mine holds
        one "+ New" card. No "+ New" on a read-only store (#151: the web
        runner's can_manage=False, where project management is out of scope)."""
        ws = self.ws
        if tab is None:
            tab = getattr(getattr(ws, "launcher", None), "tab", "games")
        manage = getattr(ws, "can_manage", True)
        if tab == "make":
            items = self._make_items(carts)
            return items if manage else items[1:]
        carts = self.runs_here(carts)
        own = ws.carts.is_own
        if tab == "apps":
            hidden = getattr(ws.wm, "HIDDEN_APPS", ())
            out = []
            for app in getattr(ws, "app_layers", ()):
                if app.id in hidden:
                    continue
                for c in carts:
                    if app.is_app(c):
                        out.append(c)
                        break
            out.extend(self.unclaimed_apps(carts))
            return out
        out = []
        for c in carts:
            if c.get("type") in ("wallpaper", "theme", "service") or ws.spine.is_system_app(c):
                continue
            if tab == "mine":
                if own(c):
                    out.append(c)
            elif not own(c) and c.get("type") not in ("app", "tool"):
                out.append(c)
        if tab == "mine" and not out and manage:
            out.append(new_tile())
        return out

    def runs_here(self, carts):
        """`carts` less those whose manifest requires a device this console
        lacks, the launch's refusal (`app_perms.lacking`): the run tiles
        (Games, Mine, Apps, the desk column) leave them out, and Files and
        Make still list them."""
        ws = self.ws
        consent = getattr(ws, "consent", None)
        return consent.runnable(carts) if consent is not None else list(carts)

    def unclaimed_apps(self, carts):
        """The installed app- and tool-typed carts of `carts` that no
        registered app claims and the kid did not make: the Apps tab's tail
        and the desk column's (`wm_desk`)."""
        ws = self.ws
        own = ws.carts.is_own
        return [c for c in carts if c.get("type") in ("app", "tool")
                and not own(c) and not ws.spine.is_system_app(c)]

    def runnable_items(self):
        """Every cart a tap on the home RUNS or opens -- Games, Mine and Apps,
        in that order: what a remote run (`launch_named`) chooses from."""
        ws = self.ws
        carts = ws.carts.all
        out = []
        for tab in ("games", "mine", "apps"):
            for c in self.launcher_items(carts, tab):
                if c.get("path"):
                    out.append(c)
        return out

    def launcher_view_items(self, grid=None):
        """`grid`'s (the home's by default) current contents: its active tab,
        the home's narrowed by the search query (#105), if any -- a plain
        case-insensitive substring match on the title; a "+ New" card survives
        it. The single place a rescan (carts.apply), a query edit
        (set_search_query) and a tab switch all call, so they can never desync
        on which list is "current"."""
        ws = self.ws
        if grid is not None and grid is not ws.launcher:
            return self.launcher_items(ws.carts.all, grid.tab)
        base = self.launcher_items(ws.carts.all)
        q = ws.search_query.strip().lower()
        if not q:
            return base
        return [it for it in base
                if it.get("type") in PSEUDO_TILE_TYPES
                or q in it.get("title", "").lower()]

    def _home_grid(self, arrangement, items):
        """The home's card model for `arrangement` (docs/theme_and_shell_2026-11.md
        §8.10): the Carousel (the home window's, always), the shelf grid or
        the icon grid."""
        ws = self.ws
        cls = _HOME_CLASSES.get(arrangement, Carousel)
        g = cls(items, ws.layout, NAMES)
        # The "+ New" card draws the live icon set's plus (iconsets.plus).
        g.icon_spec = ws.icon_spec
        g.cover_for = ws.covers.cover_for
        g.cover_ready = ws.covers.ready_for
        g.tinted_for = ws.covers.tinted
        # The favorite-star badge on the selected card (#105).
        g.favorite_for = ws.carts.is_favorite
        # The crash guard's mark on a Make card it turned off (#160).
        g.broken_for = ws.spine.cart_broken
        g.broken_ids = ws.spine.broken_cart_ids
        g.own_for = ws.carts.is_own
        g.plays_for = ws.carts.plays
        g.today_for = ws.notices.today_ymd
        g.theme = getattr(ws, "theme_colors", None)
        return g

    def set_home_arrangement(self, arrangement):
        """Show the home as `arrangement` -- "carousel", "grid" or "icons",
        the layout's `home` key (look.apply_layout) -- on the same tab and
        card."""
        ws = self.ws
        old = ws.launcher
        if type(old) is _HOME_CLASSES.get(arrangement, Carousel):
            return
        g = self._home_grid(arrangement, old.items)
        g.tab = old.tab
        g.tab_sel = old.tab_sel
        g.sel = old.sel
        g._scroll_to_sel()
        old.release_faces()
        ws.launcher = g
        ws.frame_state.dirty = True

    def set_home_tab(self, tab, grid=None):
        """Switch the home (`grid`, ws.launcher by default) to `tab`, back on
        the card it last had."""
        ws = self.ws
        lw = grid or ws.launcher
        if tab == lw.tab:
            return
        sel = lw.selected()
        lw.tab_sel[lw.tab] = sel.get("path") if sel else None
        lw.tab = tab
        lw.set_items(self.launcher_view_items(lw))
        want = lw.tab_sel.get(tab)
        i = 0
        for k in range(len(lw.items)):
            if want and lw.items[k].get("path") == want:
                i = k
                break
        lw.sel = i
        lw._scroll_to_sel()
        ws.frame_state.dirty = True

    def step_home_tab(self, d, grid=None):
        """The tab `d` steps from the active one, clamped (the keys' tab row)."""
        ws = self.ws
        grid = grid or ws.launcher
        ids = [t for t, _l in HOME_TABS]
        i = ids.index(grid.tab) + d
        self.set_home_tab(ids[max(0, min(len(ids) - 1, i))], grid)

    def select_cart(self, cart):
        """Bring `cart` up on the home: the tab whose list holds it, then its
        card. False when no tab holds it."""
        ws = self.ws
        path = cart.get("path") if cart else None
        tabs = [ws.launcher.tab] + [t for t, _l in HOME_TABS
                                      if t != ws.launcher.tab and t != "make"]
        for tab in tabs:
            items = self.launcher_items(ws.carts.all, tab)
            for k in range(len(items)):
                if items[k] is cart or (path and items[k].get("path") == path):
                    if tab != ws.launcher.tab or ws.search_query:
                        ws.search_query = ""
                        self.set_home_tab(tab)
                        ws.launcher.set_items(self.launcher_view_items())
                    lw = ws.launcher
                    for j in range(len(lw.items)):
                        if lw.items[j] is cart or (path and lw.items[j].get("path") == path):
                            lw.sel = j
                            return True
        return False

    def home_title(self, grid=None):
        """The bar's title on the home (`grid`, ws.launcher by default): the
        active tab's count."""
        ws = self.ws
        grid = grid or ws.launcher
        n = 0
        for it in grid.items:
            if it.get("path"):
                n += 1
        return tab_count_title(grid.tab, n)

    def open_search(self):
        """Enter the launcher search box (≡ -> SEARCH, #105): capture ASCII
        keystrokes into search_query, filtering the run-grid live. The keyboard
        is in clean ASCII while a query is typed (the home's `typing`) -- plain
        printable range only, so it works on the T-Deck's keyboard with no
        `=[]{}<>%` keys."""
        ws = self.ws
        ws.search_typing = True
        ws.frame_state.dirty = True

    def close_search(self, clear=True):
        """Leave the search box (ENTER/ESC while typing, or the sysmenu CLEAR
        SEARCH toggle). `clear=True` also drops the query and restores the full
        grid; `clear=False` (ENTER) just stops capturing keystrokes, leaving the
        filtered results up for d-pad/trackball browsing."""
        ws = self.ws
        ws.search_typing = False
        if clear:
            ws.search_query = ""
            ws.launcher.set_items(self.launcher_view_items())
        ws.frame_state.dirty = True

    def toggle_search(self):
        """The sysmenu SEARCH row's action: open the box if idle, else close (and
        clear) it -- one control both opens and dismisses search."""
        ws = self.ws
        if ws.search_typing or ws.search_query:
            self.close_search(clear=True)
        else:
            self.open_search()

    def set_search_query(self, q):
        """Replace the search query and re-filter the launcher grid (#105). The
        idle-frame perf floor stays intact: this only runs on a keystroke, never
        per-frame, and an unchanged (empty) query is the byte-identical original
        grid."""
        ws = self.ws
        ws.search_query = q
        ws.launcher.set_items(self.launcher_view_items())
        ws.frame_state.dirty = True

    def _make_items(self, carts):
        """The Make tab's entries: the "+ New" card first (create a game + open
        it), then every editable cart (wallpapers + built-ins included).

        SYSTEM-APP carts are the one exclusion, and it is TEMPORARY (owner call
        2026-07-31). Files/Paint/Calc/Appearance are not really
        carts: the app is a shell MODULE (runtime/apps/*_app.py, frozen on device),
        and the `.moy` only carries identity, icon art and a few-line fallback
        body for an older shell. Listing them offered a project whose "code" was
        that stub -- editing it changes nothing the kid can see. Hiding them is
        a placeholder for the real fix: #181, "privileged system carts"
        (runtime/shell/system_api.py), where each app becomes a genuine cart running
        under make_system_api and this filter comes back out.

        Deliberately narrow: only carts an app CLAIMS as its identity are hidden
        (the same is_app() check the desk icons and launcher dispatch use), so a
        kid-made cart of type "app" -- which no shell app claims -- still shows.
        Note this departs from shell_ux_v1's "everything is editable"; the spec
        line is right again once #181 lands. The achievements process's cart
        is the console's own as well (docs/os_2026-11/processes.md §8)."""
        ws = self.ws
        carts = [c for c in carts if c.get("type") != "theme" and not _is_ach_proc(c)]
        apps = getattr(ws, "app_layers", ())
        if apps:
            keep = []
            for c in carts:
                claimed = False
                for _app in apps:
                    try:
                        if _app.is_app(c):
                            claimed = True
                            break
                    except Exception:  # noqa: BLE001 -- a bad claim never hides a cart
                        pass
                if not claimed:
                    keep.append(c)
            carts = keep
        return [new_tile()] + list(carts)

    def leave_or_home(self, leave):
        """The B-leaves-the-sub-view / HOME-backs-out (`home_key`) shape shared by
        several menu_view input branches (theme/map/music/the default card editor)
        and the block-editor outline: B calls the view's own `leave` callback (it
        differs per view), HOME always goes
        to the HOME key's verb (`home_key`). Verified identical at every call site
        before extracting."""
        ws = self.ws
        i = ws.input
        if i.pressed("b"):
            leave()
        elif i.pressed("home"):
            self.home_key()
