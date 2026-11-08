# Map (grep -n a name to jump there):
#   Run                      one run's state; `ws.run` the run the shell serves
#   _hold                    a minimised game held; _unhold gives it back
#   Proc                     a running game or app beside the front one
#   Procs                    the running set (`ws.procs`)
#   -- the running set       handheld, _front_live, _detachable, running, find
#   -- launching             launch, refuse, title_of
#   -- runs                  _fresh, detach, minimise, switch, promote, close
#   -- the frame             frame, _step, _front_state, _pace
#   -- the accounting        account, rows, granted, runs, stop_service, forget, _meter
"""The running set: several apps at once (docs/os_2026-11/processes.md §1, §6).

A RUN is one object, `Run`: everything the shell holds of a run -- its
Player, its Project, its audio session, its game canvas (and the run-canvas
binding over it), its compiled frame, its view and its clock -- and nothing
of it lives on the Workstation. `ws.run` is the run the shell serves: the
FRONT run -- the game the user focused last, whose window is the WM's
"desktop" -- and every other running game is a Proc here, holding its own
Run. Stepping a Proc, or painting its window, serves its run for that call
(`_install` points `ws.run` at it, `_uninstall` back at the front's), so
the Player, the cart API and the WM read `ws.run`, or the run they were
handed (`Player.run`), and act on that run alone. One VM steps them all,
cooperatively, in the frame loop (processes.md §3).

A Proc's window is a game window (`_Win.kind` "desktop") under its own key,
`run:<n>`. Focusing it PROMOTES it: its run becomes the front one and the
front's goes to the Proc, the two window records trading keys, so the
focused game is always the front one and gets the keys, the pointer, the
audible session, the pause menu and the FPS chip.

The window states (processes.md §1, as built):

  running     the front game: its frames as ever, beside a focused app
              window too (its canvas is the one the desk's live wallpaper
              shared until the first Proc, so it keeps no frame between
              draws to present);
  throttled   a Proc whose window shows: its `update` at its own rate with a
              true dt, its `draw` on every Nth of its draw frames, N 2 while
              the loop keeps the front's period and rising to THROTTLE_MAX
              while it does not; between draws its window presents the
              canvas's last frame (`owed`);
  minimised   a window hidden to its chip, covered by a full-screen window
              or parked by console mode, and every Proc on a handheld: a
              game is held (no `update`, no `draw`, its clock stopped as the
              pause menu stops it), an app (`"type": "app"`) keeps its
              `update` every loop frame and never draws. Entering it calls
              the cart's `_minimise()`, leaving it `_restore()`
              (Player.window_event).

A handheld (a WM with no desk) keeps the same set with no windows: the
pause card's Minimise detaches the front game and goes home (a launch
replaces a front run that plays, as before), and `switch` brings a Proc to
the front (the task manager's Switch to, or launching its cart again), the
run that played joining the set.

`account` samples every run's and every service's CPU each METER_MS, for
the task manager (runtime/system/tasks_ui.py) and `rows`.

A Proc reads no input: its namespace's input verbs answer nothing while it
is detached (`MUTED`), and its Player keeps no edges and pulls no mix.
Admission (`launch`) is player_fit.admit over the running set; a refusal is
the confirm dialog naming what to close.
"""

try:
    from ticks import _ticks_ms, _ticks_diff
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime.ticks import _ticks_ms, _ticks_diff
try:
    import player_fit as _fit
    import audio_session as _aud
    from player import BAR_TYPES
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime import player_fit as _fit
    from runtime import audio_session as _aud
    from runtime.player import BAR_TYPES
try:
    import moy_log as _L
except ImportError:  # pragma: no cover - an image without the kernel
    _L = None
try:
    import moy_loop as _loop
except ImportError:  # host: no kernel loop to pace
    _loop = None
try:
    import moy_app as _moy_app
except ImportError:  # host: the runtime package
    from runtime import moy_app as _moy_app
try:
    import scheduler as _sched
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime import scheduler as _sched

RUN = "run:"                    # a Proc window's key prefix
FRONT = "desktop"               # the front run's window key
OWNERS = ("cart", "cart2", "cart3", "cart4")    # audio_session.OWNERS' runs
THROTTLE_MIN = 2                # a throttled game's draw divisor, least...
THROTTLE_MAX = 4                # ...and most
PACE_MS = 500                   # how often the divisor moves, at most
METER_MS = 2000                 # the PROCS line's period under PERF DIAG

RUNNING, THROTTLED, MINIMISED = "running", "throttled", "minimised"
PAUSED, CRASHED = "paused", "crashed"
SVC = "svc:"                    # a service row's key prefix
NOTIFY = "notifications"        # the permission a post needs
# The scheduler's rows that carry the grant their start was bound with.
_GRANTED = (_sched.SERVICE, _sched.COMMAND)

def _nokey(code=None):
    return 0 if code is None else False


def _nobtn(name, player=None):
    return False


def _notouch():
    return None


def _nomouse():
    return (0, 0, False, False, False, 0, 0)


def _nomode(on=True):
    return None


# A detached run's input verbs: a background game reads nothing.
MUTED = {"btn": _nobtn, "btnp": _nobtn, "key": _nokey, "keyp": _nokey,
         "touch": _notouch, "mouse": _nomouse, "textmode": _nomode}


class Run:
    """One run's state, all of it: `ws.run` the one the shell serves, a
    Proc's the one it holds. Plain attributes (never properties): the
    frame reads them every frame."""

    def __init__(self, canvas=None):
        self.player = None          # its Player (player.Player, `Player.run` back)
        self.project = None         # its Project, the open cart's workspace
        self.audio = None           # its AudioSession, built on a start (#16)
        # The game canvas it draws on: the boot canvas, a canvas made for a
        # Proc (`canvas_tag` names it), or one a bind gave it.
        self.canvas = canvas
        # A compiled cart's frame that goes to the glass from the cart's own
        # memory (device/moycore_glue.CartFrame), set by the run for its
        # life on a system canvas that `presents_frames`; None everywhere else.
        self.cart_frame = None
        # The run-canvas binding (console_canvas.CanvasBinding): the canvas
        # bound for the run, what `canvas` was before it, and whether the
        # bind promoted that stock canvas to the system canvas.
        self.bound = None
        self.stock = None
        self.promoted = False
        # A RESPONSIVE app cart (#181) draws on the SYSTEM canvas instead of
        # the fixed game one, which the chrome reads once per frame. False
        # for every game, every fixed app cart, and every shipped system app,
        # which is what keeps the pixel goldens where they are.
        self.app_full_canvas = False
        # ...and in the windowed desk its window is its surface: a canvas the
        # desk's work area large, of which the window's content shows
        # (`app_window_size`), so a resize moves its content rect and no
        # pixels. No host strip is drawn on it; the window's title strip is
        # the way out.
        self.app_window_canvas = False
        self.fat_cart = None        # #66 live-set diet: the one rehydrated cart
        self.canvas_tag = None      # the owner tag of a canvas made for it
        # The cart's `view(w, h)`: (w, h), or None for the full canvas
        # (`Workstation.game_view` turns it into the source rect).
        self.view = None
        self.cart_start_ms = None   # the Player's start stamp, the cart's time() counts from; None before one


def _is_app(cart):
    return (cart or {}).get("type") == "app"


def _hold(pl):
    """A minimised game held: its clock stopped as the pause menu stops it
    (`Player.pause`), what held it marked so `_unhold` gives back only its
    own hold. A pause the card made already counts as the hold."""
    if pl._paused_at:
        pl._min_held = True
        return
    if pl.detached:
        pl._paused_at = _ticks_ms() or 1    # a Proc's session is not the audible one
        pl._audio_held = 0
    else:
        pl.pause()
    pl._min_held = True


def _unhold(pl):
    if getattr(pl, "_min_held", False):
        pl._min_held = False
        pl.resume()


def _sample(pl, el):
    pl.cpu = (pl.us_upd + pl.us_draw) // el
    pl.us_upd = pl.us_draw = 0


def _grant(pl):
    ctx = getattr(pl, "_app_ctx", None) if pl is not None else None
    return ctx.grant if ctx is not None else None


def _poster(cart, granted):
    return (str((cart or {}).get("title") or "App"), cart, NOTIFY in (granted or ()))


class Proc:
    """A running game beside the front one: its window's key and its Run."""

    def __init__(self, key, run):
        self.key = key
        self.run = run
        self.state = THROTTLED
        self.skip = 0               # its draw frames since the last it drew
        self.owed = 0               # painted frames its last draw is owed
        self.draws = self.ups = 0   # since it joined the set
        self._draws0 = self._ups0 = 0   # ...at the meter's last sample
        self.fps = self.rate = 0.0  # the meter's last sample: draws, updates /s
        self.name = "Game"          # its cart's title (`named`)
        self.app = False            # an `"type": "app"` cart: updated while minimised

    @property
    def player(self):
        return self.run.player

    def named(self):
        """Take the title of the cart its run plays."""
        c = self.run.project.cart if self.run.project is not None else None
        self.name = str((c or {}).get("title") or "Game")
        self.app = _is_app(c)

    def title(self):
        return self.name


class Procs:
    """The running set beyond the front run (`ws.procs`)."""

    def __init__(self, ws):
        self.ws = ws
        self.list = []
        self.by_key = {}
        self.spare = None           # an emptied Run, kept for the next one
        self.n = THROTTLE_MIN       # the throttled games' draw divisor
        self._seq = 1
        self._ema = 0               # the loop period, smoothed (ms)
        self._paced = 0             # loop ms since the divisor last moved
        self._meter_t = 0
        self._acct_t = 0            # the last accounting sample
        self.gen = 0                # moves with every sample and every change
        self._front_min = None      # the front Player the last frame minimised
        self._back = None           # the run `_install` serves again after

    # -- the running set ----------------------------------------------------

    def handheld(self):
        """The WM has no desk: the running set keeps no windows."""
        return not getattr(self.ws.wm, "has_desk", False)

    def _front_live(self):
        """The front run is a live game in a desk window, or on a
        handheld's back-stack."""
        ws = self.ws
        pl = ws.run.player
        if pl.cart_error is not None or (pl._update is None and pl._draw is None):
            return False
        if self.handheld():
            return ws.wm.stack.has(FRONT)
        wins = getattr(ws.wm, "_wins", None)
        return wins is not None and FRONT in wins

    def _detachable(self):
        """The front run can join the running set: a Python or Lua game in
        a desk window of its own -- not a playtest the Editor holds, not a
        tool, not a linked match, not a run the kernel's front drives."""
        ws = self.ws
        wm = ws.wm
        hand = self.handheld()
        if not (hand or getattr(wm, "mode", None) == "desktop") or not self._front_live():
            return False
        pl = ws.run.player
        if not hand:
            win = wm._wins[FRONT]
            if win.docked is not None or getattr(win, "own", False) \
                    or getattr(wm, "_play_intent", None) == "dev":
                return False
        if (pl._is_tool and not (hand and _is_app(ws.cart))) or pl._script \
                or not _fit.shares_runtime(ws.cart) or pl._front \
                or pl._netplay is not None:
            return False
        ed = getattr(ws, "editor_app", None)
        return ed is None or ed.project is not ws.run.project

    def running(self):
        """The running set as admission counts it, oldest first:
        (key, need, shares) a run, the front's last. A handheld's front run
        is the one a launch replaces, so it does not count there."""
        ws = self.ws
        rts = getattr(ws, "runtimes", None) or {}
        out = []
        for p in self.list:
            c = p.run.project.cart
            out.append((p.key, _fit.app_need(c), _fit.shares_runtime(c)))
        if self.handheld():
            return out
        if self._detachable():
            c = ws.cart or {}
            rt = c.get("runtime", "python")
            out.append((FRONT, _fit.app_need(c, rts.get(rt)), _fit.shares_runtime(c)))
        elif self.list and self._front_live():
            # a front run that cannot join (a compiled game, a playtest the
            # kernel runs): it is replaced by the launch unless admission
            # counts it
            c = ws.cart or {}
            rt = c.get("runtime", "python")
            if rt != "python":
                out.append((FRONT, _fit.app_need(c, rts.get(rt)), False))
        return out

    def need(self):
        """The PSRAM the running set's apps hold against THE SHARES' PROCS
        row, the front run's included wherever a game plays: what a
        service's start is counted beside."""
        ws = self.ws
        rts = getattr(ws, "runtimes", None) or {}
        total = 0
        for p in self.list:
            total += _fit.app_need(p.run.project.cart)
        c = ws.cart
        if c is not None and (self._front_live() or getattr(ws.run.player, "_front", False)):
            total += _fit.app_need(c, rts.get(c.get("runtime", "python")))
        return total

    def room(self, held):
        """Why services holding `held` bytes do not fit THE SHARES' PROCS
        row beside the running set (the scheduler's `room`), or None where
        they fit or the build has no share."""
        share = _fit.board_share()
        if share is None:
            return None
        used = self.need()
        if used + held <= share:
            return None
        return "no room: services would hold %d KiB beside the running set's %d KiB, " \
               "over the %d KiB share" % (held >> 10, used >> 10, share >> 10)

    def held(self):
        """The services' PSRAM as admission counts it: (key, bytes)."""
        return [(SVC + str(pid), n) for pid, n in _sched.SCHED.holding()]

    def _rows(self):
        """The audio table's rows a run's session may take: those the
        other owners (the Music editor) do not hold."""
        n = _aud.SESSIONS
        runs = _aud.OWNERS
        for s in _aud._LIVE:
            if s.owner in runs and s.owner not in OWNERS:
                n -= 1
        return n

    def find(self, cart):
        """The Proc running `cart`, or None."""
        path = (cart or {}).get("path")
        if path is None:
            return None
        for p in self.list:
            c = p.run.project.cart
            if c is not None and c.get("path") == path:
                return p
        return None

    # -- launching ----------------------------------------------------------

    def launch(self, cart):
        """Called by `Workstation.open` before it opens `cart` in the front
        run: True when it goes on (the front run detached into the
        running set when one plays), False when it was handled here -- a
        Proc already running it brought to the front, or a refusal."""
        ws = self.ws
        hand = self.handheld()
        if not hand and getattr(ws.wm, "mode", None) != "desktop":
            return True
        p = self.find(cart)
        if p is not None:
            if hand:
                self.switch(p)
            else:
                self.promote(p)
                ws.wm._bring_forward(FRONT)
            return False
        cart = cart or {}
        if cart.get("broken") or (cart.get("type") in BAR_TYPES
                                  and not (hand and _is_app(cart))):
            return True
        front = ws.cart
        if front is not None and front.get("path") == cart.get("path"):
            return True             # the front's own cart: a re-run, as ever
        running = self.running()
        held = self.held()
        if not running and not held:
            return True
        rt = cart.get("runtime", "python")
        rts = getattr(ws, "runtimes", None) or {}
        close = _fit.admit((_fit.app_need(cart, rts.get(rt)), _fit.shares_runtime(cart)),
                           running, _fit.board_share(), self._rows(), held)
        if close:
            self.refuse(cart, close)
            return False
        if not hand and self._detachable():
            self.detach()
        return True

    def refuse(self, cart, keys):
        """The admission's refusal: the confirm dialog naming what to close,
        whose Close closes it and opens `cart`."""
        ws = self.ws
        names = []
        for k in keys:
            names.append(self.title_of(k)[:14])
        title = "Close %s?" % " and ".join(names)
        line = "%s doesn't fit beside %s." % (
            str(cart.get("title") or "This game")[:14],
            "them" if len(names) > 1 else "it")

        def then():
            for k in keys:
                self.close_key(k)
            ws.open(cart)
        ws.confirm.ask_close(title, line, then)

    def title_of(self, key):
        if key == FRONT:
            c = self.ws.cart or {}
            return str(c.get("title") or "Game")
        if key.startswith(SVC):
            sp = _sched.SCHED.procs.get(int(key[len(SVC):]))
            return str(((sp.cart or {}).get("title") or sp.name) if sp is not None else key)
        p = self.by_key.get(key)
        return p.title() if p is not None else key

    # -- runs ---------------------------------------------------------------

    def _owner(self):
        used = [self.ws.run.player.owner]
        for p in self.list:
            used.append(p.run.player.owner)
        for o in OWNERS:
            if o not in used:
                return o
        return None

    def _fresh(self):
        """An empty Run for the next front run: the spare, else one made,
        with a game canvas of its own; None where none can be made."""
        w = self.spare
        if w is not None:
            self.spare = None
            return w
        ws = self.ws
        mk = getattr(ws, "make_game_canvas", None)
        owner = self._owner()
        if mk is None or owner is None:
            return None
        base = ws.run.stock or ws.run.canvas
        if self.handheld():
            # every run draws on the one glass: only the front draws there
            return self._made(base, owner)
        tag = owner + ".world"
        try:
            cv = mk(base.w, base.h, tag)
        except Exception:  # noqa: BLE001 -- no room: the launch replaces as ever
            cv = None
        if cv is None:
            return None
        w = self._made(cv, owner)
        w.canvas_tag = tag
        return w

    def _made(self, canvas, owner):
        ws = self.ws
        front = ws.run
        w = Run(canvas)
        w.player = type(front.player)(ws, front.player.NAMES, w)
        w.player.owner = owner
        w.project = type(front.project)(ws)
        return w

    def _retire(self, w):
        """An emptied Run: kept as the spare, or its session closed and
        the canvas made for it given back."""
        if w.audio is not None:
            try:
                w.audio.close()
            except Exception:  # noqa: BLE001 -- teardown never raises
                pass
            w.audio = None
        if self.spare is None:
            self.spare = w
            return
        tag = w.canvas_tag
        rl = getattr(w.canvas, "reclaim_layers", None)
        if tag is not None and rl is not None:
            try:
                rl(tag)
            except Exception:  # noqa: BLE001
                pass

    def _mute(self, pl):
        pl.detached = True
        m = getattr(pl._lua, "mute", None)
        if m is not None:
            m(True)                 # a Lua run's snapshot: no input table
        ns = pl.ns
        held = {}
        if ns:
            for name in MUTED:
                if name in ns:
                    held[name] = ns[name]
                    ns[name] = MUTED[name]
        pl._held_verbs = held
        pl._held_edges = (pl._tick_edges, pl._keep_edges)
        pl._tick_edges = pl._keep_edges = None
        pl._keyp_latch = 0

    def _unmute(self, pl):
        pl.detached = False
        m = getattr(pl._lua, "mute", None)
        if m is not None:
            m(False)
        pl.quit_asked = False
        ns = pl.ns
        held = getattr(pl, "_held_verbs", None)
        if ns and held:
            for name in held:
                ns[name] = held[name]
        pl._held_verbs = None
        e = getattr(pl, "_held_edges", None)
        if e is not None:
            pl._tick_edges, pl._keep_edges = e
            pl._held_edges = None
        pl.park()

    def _install(self, p):
        """Serve `p`'s run (`ws.run`) for a step or a paint, until
        `_uninstall`; answers what the log's current process was."""
        was = None
        if _L is not None:
            was = _L.current()
            pid = p.run.player.pid
            if pid is not None:
                _L.current(pid)
        ws = self.ws                    # last: nothing above leaves it served
        self._back = ws.run
        ws.run = p.run
        return was

    def _uninstall(self, p, was):
        self.ws.run = self._back
        self._back = None
        if _L is not None and was is not None:
            _L.current(was)

    def _pace_front(self):
        if _loop is not None:
            _loop.tick(self.ws.run.player.tick_ms)

    def detach(self):
        """The front run joins the running set: its window keeps its place
        under a Proc's key, and a fresh Run is the front for the run about
        to open. False where no Run could be made."""
        ws = self.ws
        hand = self.handheld()
        w = self._fresh()
        if w is None:
            return False
        if not hand:
            rehome = getattr(ws.wallpaper, "rehome", None)
            if rehome is not None:
                rehome()
        key = RUN + str(self._seq)
        self._seq += 1
        pl = ws.run.player
        minimised = hand or getattr(pl, "_win_state", None) == MINIMISED
        if hand:
            pl.window_event("_minimise")
        p = Proc(key, ws.run)
        ws.run = w
        p.named()
        self._mute(pl)
        if minimised:
            p.state = MINIMISED
            if not p.app:
                _hold(pl)
            pl._audio_held = 0          # the front's session is the next run's
        self.list.append(p)
        self.by_key[key] = p
        self.gen += 1
        if not hand:
            ws.wm.rekey(FRONT, key)
            if ws.wm._focus == key:
                ws.wm._focus = None     # the run about to open takes it
        if ws.run.audio is not None:
            ws.run.audio.focus()
        return True

    def minimise(self):
        """A handheld's pause card's Minimise: the front run joins the
        running set, held where it is, and the console goes home. False
        where it cannot join (then the card stays)."""
        ws = self.ws
        if not self._detachable() or not self.detach():
            return False
        ws.go_home()
        return True

    def switch(self, p):
        """A handheld brings Proc `p` to the front: a front run that plays
        joins the running set first, `p`'s run is the front one and shown,
        the home its caller."""
        ws = self.ws
        if p not in self.list:
            return
        if self._front_live():
            if not self._detachable() or not self.detach():
                ws._exit_to_caller()    # a run that cannot join ends, as a launch ends it
        self.promote(p)
        ws.close_popover()
        ws.play(ws.launcher_layer)

    def promote(self, p):
        """Focus reached Proc `p`'s window: its run becomes the front one.
        A front game in a window takes its place as a Proc; an emptied front
        Run becomes the spare."""
        ws = self.ws
        wm = ws.wm
        live = FRONT in getattr(wm, "_wins", {})
        held = p.state == MINIMISED
        front = ws.run
        ws.run = p.run
        p.run = front
        pl = ws.run.player
        self._unmute(pl)
        pl._win_state = RUNNING
        if held:
            _unhold(pl)
            pl.window_event("_restore")
        self.gen += 1
        if live:
            p.named()
            wm.swap_keys(FRONT, p.key)
            self._mute(p.player)
            p.state = THROTTLED
            p.skip = p.owed = 0
        else:
            old = p.run.player
            if old.ns or old._update is not None or old._draw is not None:
                was = self._install(p)
                try:
                    old.release_world()
                finally:
                    self._uninstall(p, was)
            self.list.remove(p)
            del self.by_key[p.key]
            if not self.handheld():
                wm.rekey(p.key, FRONT)
            self._retire(p.run)
        self._pace_front()
        if ws.run.audio is not None:
            ws.run.audio.focus()
        ws._dirty = True

    def close(self, p, window=True):
        """End Proc `p`: its run released as an exit releases one and
        retired, its window gone with its key (`window`)."""
        if p not in self.list:
            return
        self.list.remove(p)
        del self.by_key[p.key]
        pl = p.player
        was = self._install(p)
        try:
            pl.release_world()
        except Exception as exc:  # noqa: BLE001 -- a close always closes
            print("Moybyte proc close:", exc)
        finally:
            self._uninstall(p, was)
        pl.detached = False
        pl._min_held = False
        self._retire(p.run)
        self._pace_front()
        self.gen += 1
        if window and not self.handheld():
            self.ws.wm._remove_kind(p.key)
        self.ws._dirty = True

    def close_key(self, key):
        """Close the run `key` names: a Proc, the front's by its exit, or a
        service by its stop."""
        if key == FRONT:
            self.ws._exit_to_caller()
            return
        if key.startswith(SVC):
            self.stop_service(int(key[len(SVC):]))
            return
        p = self.by_key.get(key)
        if p is not None:
            self.close(p)

    def dropped(self, key):
        """The WM dropped window `key` (its kind left the back-stack)."""
        p = self.by_key.get(key)
        if p is not None:
            self.close(p, window=False)

    # -- the frame ----------------------------------------------------------

    def frame(self, dt):
        """Once a loop frame, before the front's: promote a Proc the focus
        reached, step every Proc by its state and the front by its own when
        minimised, move the divisor by the loop's period and sample the
        accounting. Allocates nothing on a frame with no Proc, and with no
        Proc and no window to minimise the front reads a clock and two
        flags."""
        now = _ticks_ms()
        if _ticks_diff(now, self._acct_t) >= METER_MS:
            self.account(now)
        ws = self.ws
        wm = ws.wm
        if not self.list:
            if self._front_min is not None or getattr(wm, "_wins", None) is not None:
                self._front_state(wm, dt)
            return
        f = getattr(wm, "_focus", None)
        if f is not None and f in self.by_key:
            wm._sync_windows()          # a window pushed this frame first
            f = wm._focus
        if f is not None and f in self.by_key:
            self.promote(self.by_key[f])
            if not self.list:
                self._front_state(wm, dt)
                return
        self._pace(dt)
        self._front_state(wm, dt)
        horizon = wm._retained_n() if hasattr(wm, "_retained_n") else 1
        i = 0
        while i < len(self.list):
            p = self.list[i]
            i += 1
            shown = wm.game_shown(p.key) if hasattr(wm, "game_shown") else False
            if not shown and p.state == MINIMISED and not p.app:
                continue                # a held game: nothing to step
            was = self._install(p)
            try:
                self._step(p, shown, dt, horizon)
            finally:
                self._uninstall(p, was)
            pl = p.player
            if pl.cart_error is not None or pl.quit_asked:
                if pl.cart_error is not None:
                    note = getattr(ws, "notice", None)
                    if note is not None:
                        note("STOPPED", "%s stopped" % p.title()[:20], "warn")
                self.close(p)
                i -= 1
        self._meter(now)

    def _step(self, p, shown, dt, horizon):
        pl = self.ws.run.player
        state = THROTTLED if shown else MINIMISED
        if state != p.state:
            p.state = state
            self.gen += 1
            if shown:
                _unhold(pl)
                pl.window_event("_restore")
            else:
                pl.window_event("_minimise")
                if not p.app:
                    _hold(pl)
            if pl.cart_error is not None or not (shown or p.app):
                return
        if shown:
            draw = pl.frame_plan(dt)
            if draw and pl._netplay is None:   # a match draws every tick it simulates
                p.skip += 1
                if p.skip >= self.n:
                    p.skip = 0
                else:
                    draw = False
            pl.tick(dt, render=draw)
            p.ups += pl._n_ticks if pl.tick_ms else 1
            if draw:
                p.draws += 1
                p.owed = horizon
            return
        pl.frame_plan(dt)               # a minimised app: its update, no draw
        pl.tick(dt, render=False)
        p.ups += pl._n_ticks if pl.tick_ms else 1

    def _front_state(self, wm, dt):
        """The front's window minimised (the console's frame does not tick
        it): a game held, an app's update every frame; and its events."""
        ws = self.ws
        pl = ws.run.player
        wins = getattr(wm, "_wins", None)
        win = wins.get(FRONT) if wins is not None else None
        hidden = (win is not None and win.minimized and self._front_live())
        if hidden:
            app = _is_app(ws.cart)
            if self._front_min is not pl:
                self._front_min = pl
                self.gen += 1
                pl._win_state = MINIMISED
                pl.window_event("_minimise")
                if not app:
                    _hold(pl)
                return
            if app:
                pl.frame_plan(dt)
                pl.tick(dt, render=False)
        elif self._front_min is not None:
            was = self._front_min
            self._front_min = None
            self.gen += 1
            if was is pl and getattr(pl, "_win_state", None) == MINIMISED:
                pl._win_state = RUNNING
                _unhold(pl)
                pl.window_event("_restore")

    def front_minimised(self):
        """The front's window is hidden: the console's frame leaves its
        ticks to `frame`."""
        return self._front_min is not None and self._front_min is self.ws.run.player

    def _pace(self, dt):
        """The divisor follows the loop: THROTTLE_MIN while a frame keeps
        the front's period (its tick, else 30 a second), up to THROTTLE_MAX
        while it does not. The period is the loop's own `dt`, the clock
        every other part of the frame steps by."""
        per = int(dt * 1000)
        if per <= 0:
            return
        self._ema = per if not self._ema else (self._ema * 7 + per) // 8
        self._paced += per
        if self._paced < PACE_MS:
            return
        self._paced = 0
        budget = self.ws.run.player.tick_ms or 33
        if self._ema * 100 > budget * 115 and self.n < THROTTLE_MAX:
            self.n += 1
        elif self._ema * 100 < budget * 90 and self.n > THROTTLE_MIN:
            self.n -= 1

    # -- the accounting (the task manager's) --------------------------------

    def account(self, now):
        """Every METER_MS: each run's update, draw and window events, and
        each process's callbacks (the scheduler's rows), as busy microseconds
        a second -- per mille of a core -- on `Player.cpu` and the
        scheduler's `Proc.cpu`. Allocates nothing between samples."""
        el = _ticks_diff(now, self._acct_t)
        if el < METER_MS:
            return
        self._acct_t = now
        _sample(self.ws.run.player, el)
        for p in self.list:
            _sample(p.run.player, el)
        rows = _sched.SCHED.procs
        if rows:
            _sched.SCHED.figures()      # the services on tasks: read, not polled
            for pid in rows:            # a dict's own iteration: values() is an object
                sp = rows[pid]
                d = (sp.cost_us - sp.seen_us) & _sched.ALLOC_WRAP
                sp.seen_us = sp.cost_us
                sp.cpu = d // el
        self.gen += 1

    def _run_row(self, key, pl, cart, state, out):
        if pl.cart_error is not None:
            state = CRASHED
        cpu = pl.cpu
        sp = _sched.SCHED.procs.get(pl.pid) if pl.pid is not None else None
        if sp is not None and sp.cpu is not None:
            cpu = (cpu or 0) + sp.cpu
        mem = pl.mem
        heap = getattr(pl._lua, "heap", None)
        if heap is not None:
            mem = heap()            # a Lua run's own heap, exact
        out.append((key, str((cart or {}).get("title") or "Game"),
                    "app" if _is_app(cart) else "game", state, cpu, mem, pl.pid))

    def rows(self):
        """The task manager's rows, front run first, then the running set
        oldest first, then the services: (key, name, kind, state, cpu, mem,
        pid), `kind` "game", "app" or "service", `cpu` per mille of a core and
        `mem` bytes, each None where unmeasured: a Lua run's the heap of its
        own state, exact; a Python run's what its start allocated. A
        compiled run has no row yet (phase 5 of docs/os_2026-11.md)."""
        ws = self.ws
        out = []
        pl = ws.run.player
        cart = ws.cart
        if cart is not None and _fit.shares_runtime(cart) and not pl._script \
                and (pl._update is not None or pl._draw is not None
                     or pl.cart_error is not None) \
                and (ws.wm.stack.has(FRONT) or FRONT in getattr(ws.wm, "_wins", {})):
            if self.front_minimised():
                state = MINIMISED
            elif pl.paused or not ws.wm.top_is(FRONT) and self.handheld():
                state = PAUSED
            else:
                state = RUNNING
            self._run_row(FRONT, pl, cart, state, out)
        for p in self.list:
            w = p.run
            self._run_row(p.key, w.player, w.project.cart if w.project else None,
                          p.state, out)
        for sp in _sched.SCHED.services():
            st = sp.state
            if st == _sched.EXITED:
                if not sp.code:
                    continue
                st = CRASHED
            name = (sp.cart or {}).get("title") or sp.name
            out.append((SVC + str(sp.pid), str(name), "service", st, sp.cpu,
                        sp.mem, sp.pid))
        return out

    # -- notifications -------------------------------------------------------

    def who(self, pid):
        """Process `pid` as a notification names it (notifications.py's
        `who`): (title, cart, may_post), may_post whether its run or its
        service was granted `notifications`; the shell always may."""
        if pid == _sched.SHELL:
            return ("Moybyte", None, True)
        ws = self.ws
        pl = ws.run.player
        if pl is not None and pl.pid == pid:
            return _poster(ws.cart, pl.granted)
        for p in self.list:
            pl = p.run.player
            if pl.pid == pid:
                return _poster(p.run.project.cart if p.run.project else None,
                               pl.granted)
        sp = _sched.SCHED.procs.get(pid)
        if sp is not None and sp.kind in _GRANTED and sp.state != _sched.EXITED:
            return _poster(sp.cart, sp.granted)
        return (None, None, False)

    def app_id(self, pid):
        """The app id process `pid` runs under (the grant's key,
        `moy_app.id_for`): its run's cart's or its service's; None for the
        shell and a pid with no live run."""
        if pid == _sched.SHELL:
            return None
        cart = self.who(pid)[1]
        if cart is None:
            return None
        return _moy_app.id_for(cart.get("id"), cart.get("title"))

    def granted(self, pid):
        """The permissions process `pid`'s run, service or command was granted, as a
        lease checks them (runtime/system/leases.py): None for the shell,
        which is not gated, () for a pid with no live run."""
        if pid == _sched.SHELL:
            return None
        pl = self.ws.run.player
        if pl is not None and pl.pid == pid:
            return pl.granted
        for p in self.list:
            if p.run.player.pid == pid:
                return p.run.player.granted
        sp = _sched.SCHED.procs.get(pid)
        if sp is not None and sp.kind in _GRANTED and sp.state != _sched.EXITED:
            return sp.granted
        return ()

    def pid_of(self, g):
        """The pid of the run whose app grant is `g` (a role's caller), the
        shell's for a shell app's grant, a service's or a compiled command's
        for the grant its start made, None for a grant none holds."""
        if g in self.ws.shell_grants:
            return _sched.SHELL
        pl = self.ws.run.player
        if _grant(pl) == g:
            return pl.pid
        for p in self.list:
            if _grant(p.run.player) == g:
                return p.run.player.pid
        for pid, sp in _sched.SCHED.procs.items():
            ctx = sp.ctx
            if ctx is not None and ctx.grant == g and sp.state != _sched.EXITED:
                return pid
        return None

    def runs(self):
        """[(pid, name, kind, state)]: the task manager's rows that are runs,
        as /proc names them (runtime/system/devfs.py)."""
        return [(r[6], r[1], r[2], r[3]) for r in self.rows() if r[2] != "service"]

    def stop_service(self, pid):
        """The task manager's Stop: the service's TERM, then KILL (§5)."""
        _sched.SCHED.stop(pid)
        self.gen += 1

    def forget(self, pid):
        """A crashed service's row goes (its log stays)."""
        rows = _sched.SCHED.procs
        sp = rows.get(pid)
        if sp is not None and sp.state == _sched.EXITED:
            del rows[pid]
            self.gen += 1

    def _meter(self, now):
        """Each Proc's draws and updates a second, every METER_MS; under
        PERF DIAG a PROCS line names them beside the PERF line's front."""
        el = _ticks_diff(now, self._meter_t)
        if el < METER_MS:
            return
        self._meter_t = now
        ws = self.ws
        say = (getattr(ws, "perf_capture", False) or getattr(ws, "perf_hud", False)
               or getattr(ws, "diag_live", False))
        parts = []
        for p in self.list:
            p.fps = (p.draws - p._draws0) * 1000.0 / el
            p.rate = (p.ups - p._ups0) * 1000.0 / el
            p._draws0, p._ups0 = p.draws, p.ups
            if say:
                parts.append("%s=%s/%.1f/%.1f" % (p.title().replace(" ", "_"),
                                                  p.state, p.fps, p.rate))
        if say:
            print("PROCS n=%d %s" % (self.n, " ".join(parts)))
