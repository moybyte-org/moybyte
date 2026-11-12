# Map (grep -n a name to jump there):
#   InputVerbs                  the key and pointer routers, nav, the trackball, the bar's keys
#   InputVerbs.handle_input     the frame's keys down the layer stack
#   InputVerbs.handle_pointer   the pointer published and routed down the same stack
#   InputVerbs.ball             a trackball roll as W A S D steps
#   InputVerbs._bar_keys        the bar's own keys
"""The Workstation's INPUT verbs: the per-frame key router (`handle_input`,
which walks the layer stack top-down and hands the keys to the first layer
that claims them), the pointer router (`handle_pointer`, which publishes the
game-space pointer and routes taps through the same stack, and its staleness
clock `_tick_pointer_dt`), directional input (`nav`), the trackball as
W A S D (`ball_steps`/`ball` and the roll/coast steppers under them), the
bar's key routing (`bar_where`/`_bar_keys`/`bar_open_x`), the caret step,
the per-frame keyboard mode (`_menu_keys`, a menu over a text surface taking
the keys as a list does; `_sync_cart_text_mode`, a running cart's text mode
on the keyboard) and `focused_cart_editor`. A mixin of `Workstation`; the state it reads is
created in console.py's `_init_state`.
"""

from moy_input import SHIFT as _SHIFT, CTRL as _CTRL, ALT as _ALT
from editor_app import COMMIT_TABS, THEME_TABS
from chrome import _ticks_ms, _ticks_us, _ticks_diff
import ui_draw as _ui_draw


# How long the console's key is held to open the task manager, ms.
START_HOLD_MS = 500


class InputVerbs:

    _start_down = 0               # when the console's key went down; 0: not armed

    def handle_input(self):
        # Router (docs/history/shell_layers_refactor_v1.md §3): walk the z-ordered layer stack
        # top -> bottom and hand the frame's keys to the first layer that claims them.
        # A modal overlay (About / system menu, #52) sits above the content, so it eats
        # this frame's keys before they can leak to the screen underneath; the active
        # content layer is at the bottom and always consumes.
        i = self.input
        # Whether a change was reported since the last painted frame, before
        # this input's own (`Hold.opened`). Stored only on a change: a store
        # on the Workstation looks through all its classes for a property,
        # about 60 us on a T-Deck.
        d = self._dirty or self._press_owed
        if d != self._dirty_in:
            self._dirty_in = d
        # Multiplayer (#65): advance every extra player slot's press-edge for this
        # frame, aligned with the local InputState.begin_frame() the driver already
        # ran. A no-op (empty loop) with no extra controllers registered, so the
        # single-player path costs one attribute read.
        _pr = getattr(i, "players", None)
        if _pr is not None:
            _pr.begin_frame()
        # The input in use follows the last one used (the hints' keys).
        if self._news is not None:
            src = self._news()
            if src is not None:
                self.input_used(src)
        # A desk keyboard's letters are a GAME's buttons only while it owns
        # the keys (moy_input.h's CART KEYS; an app's or a tool's are keys);
        # a producer reads it next frame.
        kc = self.wm.keys_to_cart()
        ck = kc and not self.run.player._is_tool
        if ck != self._cart_keys:
            self._cart_keys = ck
            i.cart_keys = ck
        if self._menu_over or self.pause.open or self.confirm.open or self.sysmenu.open:
            self._menu_keys()
        # Super (the `start` button) is the console's from every surface, before
        # any layer, and acts when it comes up: a tap is the system menu, or
        # the pause menu over a fullscreen game (a T-Deck text field's Cancel);
        # held START_HOLD_MS it opens the task manager, and its release is
        # spent. Another key going down while it is held spends it too (a
        # chord), as the host's Super does.
        if i.pressed("start"):
            desk = self._editor_desk
            if desk is not None and i.held("select") and desk.select_start():
                return                  # the desk Editor's Select+Start: Play
            self._start_down = _ticks_ms() or 1
            return
        if self._start_down:
            if i.held("start"):
                if i.last_key or i.any_pressed():
                    self._start_down = 0        # a chord: the key does nothing
                elif _ticks_diff(_ticks_ms(), self._start_down) >= START_HOLD_MS:
                    self._start_down = 0
                    self.open_tasks()
                    return
            else:
                self._start_down = 0
                if not (_ui_draw.input_kind() == "tdeck" and self.cancel_field()):
                    self.system_key()
                return
        # Redraw-on-change (#44): a button PRESS edge or a typed key this frame may
        # change visible state (nav, select, screen/menu switch, an edit), so request a
        # repaint. Only the press edge (not release, not a steady hold) is marked: every
        # UI handler acts on i.pressed()/the typed key, never on the release, so a press
        # draws exactly one frame and the UI is static again -- a release/hold that
        # changes nothing costs nothing. Pointer-driven changes (click/drag/cursor move)
        # are caught separately in frame() via the pointer-state snapshot. Conservative
        # but never stale: a press that's a no-op costs one redraw, not a wrong screen.
        # EXCEPT when the keys belong to a RUNNING cart (wm.keys_to_cart): the cart's
        # viewport animates on its own and the chrome around it is unchanged by
        # cart-bound keys, so the mark would only force the windowed desktop's FULL
        # 1024x600 repaint instead of the quiet game-window blit. That mattered
        # because a BLE keyboard reports last_key as LEVEL state (a held byte, not
        # the T-Deck's one-shot press edge): ANY held/mashed key collapsed P4 play
        # from ~30fps to ~10 (measured; the "every keypress slows the game" bug).
        # A step is a press here: a held key's repeats and the trackball's
        # steps (`steps`) move what a press moves.
        # A held key's steps that the page taking the keys found moved
        # nothing last frame (the end of a list) mark nothing: no frame.
        rw = getattr(i, "rep_word", 0)
        pressed = (i.any_pressed() or bool(getattr(i, "key_repeats", 0))
                   or (rw != 0 and not _ui_draw.steps_dead(rw, self)))
        # The arrows of a keyboard with arrows (the host's, the browser's, a
        # BLE one) or a d-pad step a caret where one has the keys -- the Code
        # tab, a cart's focused editor handle -- as the T-Deck's ball does
        # (`nav`), Shift extending the selection; the step is theirs alone.
        if pressed and self._caret_step(i):
            return
        if (pressed or i.last_key or getattr(i, "wheel_y", 0) or getattr(i, "wheel_x", 0)
                or getattr(i, "pointer_buttons", 0)):
            if not kc and not self.wm.popover_takes_keys():
                if pressed or i.last_key or getattr(i, "wheel_y", 0) \
                        or getattr(i, "wheel_x", 0):
                    self._dirty = True
                else:
                    # A pointer press alone: frame() marks it, unless what
                    # it pressed was a popover's change the WM paints alone
                    # (`popover_touch`) -- a bar widget opening its popover.
                    self._press_owed = True
        # Undo journal (Stage 7): activity in an EDITOR TAB (re)arms the idle
        # autosave-commit debounce -- frame() fires the durable commit once the kid
        # stops for _edit_debounce_ms, so the store write lands in a gap. Marked
        # here (before routing) so it tracks the last keystroke regardless of who
        # consumes it; frame() only actually commits a tab that is dirty. The touch
        # tabs re-arm from handle_pointer -- paint/map/scene/music are drawn at
        # rather than typed at, and a debounce they could not arm would fire
        # mid-stroke.
        if ((i.last_key or pressed)
                and (self.menu_view in COMMIT_TABS or self.menu_view in THEME_TABS)
                and self.wm.top_is("menu")):
            self.history.edit_ms = _ticks_ms()
        bl = self.bar_layer
        edge = _ui_draw.edge_take()
        if ((bl.focus is not None or edge or (pressed and i.steps("up")))
                and self._bar_keys(i, pressed, edge)):
            return
        # Walk the MEMOIZED visible stack top -> bottom (Stage 6c): the WM caches it
        # pre-reversed, so this hot per-frame routing allocates neither the list nor a
        # reversed() iterator on a static top-of-stack.
        # B and Esc on a registered app's fullscreen surface are the bar's
        # back chevron (`app_back`): a page of the app, else out of it; on a
        # running app's, while it has a view of its own open (`has_page`).
        _apps = (self._apps_by_id if pressed and not self.windowed_chrome
                 and (i.pressed("b") or i.pressed("stop")) else None)
        for layer in self.wm.visible_stack_rev():
            if _apps is not None and layer.id in _apps:
                self.app_back(layer)
                return
            if (_apps is not None and layer.id == "desktop"
                    and self.run.player.has_page()):
                self.app_back(self.run.player)
                return
            if layer.handle_input(i):
                if getattr(layer, "leave_asked", False):
                    layer.leave_asked = False         # the app's own way out (a page's button)
                    self.app_back(layer)
                return

    # (The "desktop" content layer routes the running cart's frame to
    # Player.handle_input (player.py) via _PlayerLayer.)

    # -- pointer (trackball-as-mouse) ----------------------------------------

    def handle_pointer(self):
        # Router (docs/history/shell_layers_refactor_v1.md §3): publish the game-space pointer
        # (so a cart's touch()/mouse() reads the 320x240 viewport, not the panel, #39),
        # then walk the z-ordered stack top -> bottom and let the first layer that
        # claims the tap handle it. A modal overlay (About / system menu) sits above the
        # content, so it consumes the tap (and clears the game pointer's tap so a running
        # cart never also sees a tap the menu swallowed) before it can leak underneath.
        p = self.pointer
        if p is None:
            return
        # #184: this whole call is ONE lump (`hp`) to the device loop, and a
        # 1.7-1.9s stall lives inside it that no stage timer names. Split it the
        # way #183's 37s `ws` lump was split -- pre-walk bookkeeping vs the
        # per-layer routing walk, with the dearest layer named. Overwritten in
        # place (never reallocated): this runs every loop iteration, and a fresh
        # list per frame would be measurement that creates the churn it measures.
        _perf = self.perf_capture      # the hp split is diag data; the HUD
        _pt0 = 0                       # must not pay the 6 stamps per frame
        if _perf:
            _pt0 = _ticks_us()
            _pf = self._pf_ptr
            _pf[0] = _pf[1] = _pf[2] = 0
            _pf[3] = _pf[4] = None
            _pf[5] = 0
        self._tick_pointer_dt(p)
        px, py, click = p.x, p.y, p.click
        # Windowed WM (#73): while a window OTHER than the playtest holds input
        # focus, the game-space pointer publishes with click/down stripped, so a
        # background running cart never eats the taps meant for the editor beside
        # it. The fullscreen-stack WM has no hook -> unchanged.
        _pp = getattr(self.wm, "player_has_pointer", None)
        # The idle publish is REUSED, not rebuilt (#66 lever 1, 2026-09-08).
        # While a healthy GAME owns the glass and the pointer did nothing this
        # frame -- no click, no held finger, not moved -- the game pointer this
        # frame would publish equals the one standing there, PROVIDED that one
        # is our own idle publish (anything else that wrote it -- an overlay
        # stripping a tap, a wallpaper -- replaced the object) and the geometry
        # it was mapped through has not moved. Rebuilding it anyway cost the
        # mapping's tuples every loop iteration of every play frame (~0.4 ms
        # a loop on the S3, see wm.py). The fullscreen tiers only: the
        # windowed WM's focus can strip a tap without the pointer moving, so
        # it keeps the unconditional publish. Under an app the publish is kept
        # the same way, and the layer walk below still runs.
        kept = False
        if (_pp is None and not click and not p.down and not self._ptr_was_down
                and px == self._ptr_last_x and py == self._ptr_last_y
                and self.cart_error is None
                and self.input.game_pointer is self._gp_idle):
            # Same geometry? These seven reads are exactly what the fullscreen
            # WM's game_xy consumes; its memo checks the same key, but through
            # two calls and a property (~0.2 ms a loop on the S3).
            k = self._gp_key
            run = self.run
            gc = run.canvas
            sc = self._sys_canvas
            if sc is None:
                sc = gc
            if (gc is k[1] and sc is k[2]
                    and run.view is k[0]
                    and gc.w == k[3] and gc.h == k[4]
                    and sc.w == k[5] and sc.h == k[6]):
                if self.wm.top_is_player():
                    if _perf:
                        self._pf_ptr[1] = _ticks_diff(_ticks_us(), _pt0)
                    return
                kept = True
        if not kept:
            gx, gy = self._game_xy(px, py)
            _live = _pp() if _pp is not None else True
            gp = (gx, gy, click and _live, p.down and _live)
            self.input.game_pointer = gp
            if click or p.down:
                self._gp_idle = None              # a tap/hold: next frame republishes
            else:
                self._gp_idle = gp
                k = self._gp_key
                run = self.run
                gc = run.canvas
                sc = self._sys_canvas
                if sc is None:
                    sc = gc
                k[0] = run.view
                k[1] = gc
                k[2] = sc
                k[3] = gc.w
                k[4] = gc.h
                k[5] = sc.w
                k[6] = sc.h
        if _perf:
            self._pf_ptr[1] = _ticks_diff(_ticks_us(), _pt0)   # pre-walk share
        # Idle fast-path (2026-08-03): while a healthy GAME owns the glass and
        # the pointer is doing NOTHING (no click, no held finger, not moved
        # since last frame), the layer walk below routes nothing -- the cart
        # already got its touch state via game_pointer above. Skipping it
        # reclaims ~1ms/frame on the S3. Any edge (move/click/down) walks, so
        # taps, drags and hover all behave exactly as before; the crash panel
        # (cart_error) always walks so EDIT stays reachable.
        if (not click and not p.down and not self._ptr_was_down
                and px == self._ptr_last_x and py == self._ptr_last_y
                and self.cart_error is None and self.wm.top_is_player()):
            return
        # _ptr_was_down makes the RELEASE frame walk: the windowed WM moves
        # focus on down->up, and that frame has no click, no down, no movement.
        self._ptr_was_down = p.down
        self._ptr_last_x = px
        self._ptr_last_y = py
        # ...and a touch in an editor tab re-arms that same debounce. Past the idle
        # fast-path above, so a frame the pointer did nothing in pays nothing. The
        # held-finger flag is stored UNCONDITIONALLY (the pointer is global, not a
        # tab's) so it can never be left stale True by a gesture that ended
        # somewhere else; the router refuses to commit under it.
        self.history.pointer_down = p.down
        if (click or p.down) and (self.menu_view in COMMIT_TABS
                                  or self.menu_view in THEME_TABS) \
                and self.wm.top_is("menu"):
            self.history.edit_ms = _ticks_ms()
        # THE APP BAR CONTRACT, input half (docs/app_api_v1.md): a tap in a
        # REGISTERED app's bar band belongs to the bar, and the router routes it
        # BEFORE the app's own handle_pointer -- checked inside the walk (not
        # ahead of it) so an open overlay above the content still gets the tap
        # first, exactly as when each app hand-wrote this as its first statement.
        # Only a CLICK can reach the bar, so a move/hover frame pays one compare.
        _appbar = self._apps_by_id if (click and not self.windowed_chrome) else None
        # Memoized, pre-reversed visible stack (Stage 6c) -- no per-frame allocation.
        for layer in self.wm.visible_stack_rev():
            if _appbar is not None:
                _app = _appbar.get(layer.id)
                if _app is not None and not getattr(_app, "glass", False):
                    _bar = self._app_bar_route(_app, px, py)
                    if _bar is not None:            # the band is the bar's
                        if _bar:
                            return
                        continue
            if not _perf:
                if layer.handle_pointer(px, py, click):
                    return
                continue
            _tk = _ticks_us()
            _claimed = layer.handle_pointer(px, py, click)
            _lus = _ticks_diff(_ticks_us(), _tk)
            _pf = self._pf_ptr
            _pf[5] += 1
            if _lus > _pf[2]:
                _pf[2], _pf[3] = _lus, layer.id
            if _claimed:
                _pf[4] = layer.id
                _pf[0] = _ticks_diff(_ticks_us(), _pt0)
                return
        if _perf:
            self._pf_ptr[0] = _ticks_diff(_ticks_us(), _pt0)

    def _tick_pointer_dt(self, p):
        """Charge this frame's loop tick to the pointer SAMPLE, for kinetic
        scrolling (#113).

        A backend whose touch controller can't produce a sample every frame
        marks the repeat frames `pointer.fresh = False` (the T-Deck's GT911
        stalls 20-45ms on most finger-down reads, #74, so a drag yields ~20-30
        samples/s against a 30-60fps loop). Two rules follow:

        * a stale frame gets `None`, which tells ScrollRegion.drag_move to leave
          the release velocity alone -- the finger did not stand still, we
          simply learned nothing about it, and averaging in a zero would decay
          a real fling toward a stop;
        * its time is BANKED and handed to the next real sample, so a finger
          delta spanning three frames is divided by three frames of time. Charge
          one frame's dt to it instead and the velocity reads ~3x too fast.

        On a backend that samples every frame (the host's mouse, the scripted
        remote gestures) every frame is fresh, so this is exactly
        `self.frame_state.frame_dt_ms`. Every touch driver holds + flags
        (native/moy_input/moy_touch.c).

        (A "third rule" -- charge stale stretches past a threshold as real dt
        so a silent still finger decays the fling -- was built here on
        2026-08-19 and REVERTED the same day, on data: the theory said the
        Guition's AXS15231 goes quiet when a finger rests, and a 5s
        held-still trace said otherwise -- 88% of hold frames FRESH, ~55Hz of
        same-position reports whose zero deltas decay the velocity exactly
        like the GT911's. The controller's only true silence is after a LIFT,
        and the defect that motivated the rule was the driver waiting the
        GT911's 400ms hold bound to believe one -- fixed where it belongs, in
        the AXS driver's per-controller bound. The rule meanwhile killed
        real flicks dead: a lift's 400ms silence crossed the threshold and
        decayed the velocity to zero before the release ever fired.)"""
        fs = self.frame_state
        dt = fs.frame_dt_ms
        if getattr(p, "fresh", True):
            fs.pointer_dt_ms = min(dt + fs.stale_ptr_ms, 100.0)
            fs.stale_ptr_ms = 0.0
        else:
            fs.pointer_dt_ms = None
            fs.stale_ptr_ms = min(fs.stale_ptr_ms + dt, 100.0)

    # (The desktop/running-cart pointer handler -- the pause QUIT/CONTINUE + FPS-chip
    # tap + top-bar tool-switcher routing -- moved to Player.handle_pointer (Stage 2,
    # player.py); the "desktop" content layer routes to it via _PlayerLayer. The tool
    # bar's draw/tap stay on the shell, reached via _draw_tool_bar / _tool_bar_tap.)

    def nav(self, dx, dy):
        # Directional input (host arrows / device trackball). It belongs to
        # whichever surface holds a CARET -- the Editor's code tab, and a
        # cart's focused editor handle (#181: Notes is a cart, and the T-Deck's
        # ball IS its arrow keys, so a note whose caret the ball could not
        # reach would be unwritable past the first screen). Elsewhere the
        # launcher/desktop are pointer-driven and this is a no-op.
        #
        # Returns True when it was CONSUMED, which is the board loop's
        # question: the same pulses drive the cursor when no caret wants them.
        if not (dx or dy):
            return False
        # Shift held extends the selection, as SELECT mode does.
        select = True if getattr(self.input, "mods", 0) & _SHIFT else None
        view = self.menu_view
        if (self.wm.top_is("menu") and view in ("code", "theme_config")    # Stage 6d
                and self.editor is not None):
            cl = self.code_layer if view == "code" else self.theme_config_layer
            cl.stop_autoscroll()
            if dy and cl.overlay_step(dy):
                self._dirty = True         # a row of an open overlay picked
                return True
            self.editor.move(dy, dx, select)
            self._dirty = True             # caret moved -> redraw (#44)
            return True
        handle = self.focused_cart_editor()
        if handle is not None:
            handle.nav(dx, dy, select)
            self._dirty = True
            return True
        return False

    BALL_STEP = 2           # trackball pulses a step, on the roll's stronger axis
    BALL_TURN = 4           # pulses a step across a roll takes while it rolls
    BALL_ROLLING_MS = 150   # a roll stops this long after its axis's last pulse
    BALL_IDLE_MS = 300      # a pause this long forgets a partial step
    # A step's length on the coast's scroller: the scroller's fling rests
    # below MOY_UI_STOP_VEL (moy_ui.h, 0.02 a ms), a step a second here.
    COAST_UNIT = 20

    def ball_steps(self):
        """Whether the trackball is the W A S D of the surface that holds
        the keys: every popover and menu, About, and every fullscreen
        surface -- the home, the apps (a cart's page too), the Editor's tabs --
        but one whose `ball_steps` says otherwise: a running game, a canvas whose cursor
        it moves (the sprite tab and Paint until a popover opens, the map,
        the scene) and a caret, which takes it as arrows (`nav`). The desk's
        windows are pointed at."""
        if self._modal_layer() is not None or self._about:
            return True
        if self.windowed_chrome:
            return False
        if self.bar_layer.focus is not None:
            return True
        steps = getattr(self._content_layer(), "ball_steps", True)
        return bool(steps() if callable(steps) else steps)

    def ball(self, dx, dy, click=False):
        """The trackball this frame -- the T-Deck's, and the host's arrows
        standing in for it: (dx, dy) pulses and its click's press. Where
        the surface that holds the keys steps a selection (`ball_steps`),
        every BALL_STEP pulses on the roll's stronger axis are a step of the
        direction W A S D make there, added to the input table's counts
        this frame (`add_steps`: a surface reads them as `steps`, never as a
        press, all of a fast roll's in the frame they came), the click is
        Enter's `run` (a press on the "ball" source, let go the next frame)
        and the cursor stays hidden; else a caret takes the roll (`nav`),
        else the roll is the cursor's and the click the pointer's. Answers
        bit 1 when the roll was taken, bit 2 when the click was (the pointer
        then sees no click)."""
        took = 0
        steps = (dx or dy or click) and self.ball_steps()
        if dx or dy:
            if steps:
                self._ball_roll(dx, dy)
                took = 1
            elif self.nav(dx, dy):
                took = 1
        if click and steps:
            took |= 2
        if took:
            p = self.pointer
            if p is not None:
                if took & 2:
                    p.click = False
                if p.visible:
                    p.visible = False
                    self._dirty = True
        src = self._ball_src
        if self._ball_down is not None:
            src.set_button(self._ball_down, False)
            self._ball_down = None
        if took & 2:
            if src is None:
                src = self._ball_src = self.input.source("ball")
            src.set_button("run", True)
            self._ball_down = "run"
        r = self._coast_r
        if r.flinging:
            self._ball_coast_tick(self.ball_steps())
        self.ball_owed = self._ball_down is not None or r.flinging
        return took

    def _ball_coast_tick(self, on):
        """A coasting roll's steps this frame: its fling on the toolkit's
        scroller advanced by the loop's dt (none the frame a roll fed it),
        decaying as a page's fling does; off a surface that steps, or below
        a step a second, it stops."""
        r = self._coast_r
        if not on:
            r.stop()
            return
        r.tick(0.0 if self._coast_fed else self.frame_state.frame_dt_ms)
        self._coast_fed = False
        k = r.offset // self.COAST_UNIT - self._coast_got
        if k > 0:
            self._coast_got += k
            self._ball_steps(self._coast_dir, k, coast=False)

    def _ball_roll(self, dx, dy):
        """Pulses toward the steps a roll owes: a reversal or a pause starts
        the axis over, and a step on one axis forgets the other's. While a
        roll steps on one axis (a pulse on it in the last BALL_ROLLING_MS) a
        step across it takes BALL_TURN pulses, so a roll along a row that
        drifts a pulse or two across it stays on the row (a sideways roll
        along the home's row never drops to its cards); once it stops, a
        turn steps as any roll does. The steps go to the input table as this
        frame's counts (`_ball_steps`)."""
        now = _ticks_ms()
        if _ticks_diff(now, self._ball_at) > self.BALL_IDLE_MS:
            self._ball_ax = self._ball_ay = 0
        self._ball_at = now
        if (dx if self._ball_axis == 1 else dy if self._ball_axis == 2 else 0):
            self._ball_axis_at = now
        elif _ticks_diff(now, self._ball_axis_at) > self.BALL_ROLLING_MS:
            self._ball_axis = 0
        ax, ay = self._ball_ax, self._ball_ay
        ax = dx if (dx > 0) != (ax > 0) and dx else ax + dx
        ay = dy if (dy > 0) != (ay > 0) and dy else ay + dy
        n = self.BALL_STEP
        nx = self.BALL_TURN if self._ball_axis == 2 else n
        ny = self.BALL_TURN if self._ball_axis == 1 else n
        if abs(ax) >= abs(ay) and abs(ax) >= nx:
            self._ball_axis, self._ball_axis_at = 1, now
            k = int(abs(ax) // n)
            self._ball_steps("right" if ax > 0 else "left", k)
            ax = ax - k * n if ax > 0 else ax + k * n
            ay = 0
        elif abs(ay) >= ny and abs(ay) > abs(ax):
            self._ball_axis, self._ball_axis_at = 2, now
            k = int(abs(ay) // n)
            self._ball_steps("down" if ay > 0 else "up", k)
            ay = ay - k * n if ay > 0 else ay + k * n
            ax = 0
        self._ball_ax, self._ball_ay = ax, ay

    def _ball_steps(self, name, k, coast=True):
        """`k` steps of `name` into this frame's counts; `ball_stepped`
        counts every one delivered. Under BALL_COAST (moy_input.h; 0, none)
        the steps a roll makes above BALL_COAST_RATE a second, measured over
        the time since its last step, become the coast's velocity instead:
        BALL_COAST thousandths of a step a ms each, delivered on by
        `_ball_coast_tick` until it decays."""
        if k <= 0:
            return
        if coast and self._coast:
            now = _ticks_ms()
            ms = max(1, _ticks_diff(now, self._ball_rate_at))
            self._ball_rate_at = now
            keep = max(1, self._coast_rate * ms // 1000)
            r = self._coast_r
            if name != self._coast_dir:
                r.stop()
            if k > keep:
                self._coast_dir = name
                if not r.flinging:
                    r.offset = self._coast_got = 0
                r.fling((k - keep) * self._coast * self.COAST_UNIT / 1000.0)
                self._coast_fed = True
                k = keep
        self.input.add_steps(name, k)
        self.ball_stepped += k

    def bar_where(self):
        """The `where` of the bar over the fullscreen surface that holds the
        keys -- the home, Settings, the Editor, an app -- or None: the desk,
        a running cart, a surface with no bar."""
        if self.windowed_chrome:
            return None
        if self.pause.open and self.pause.shows_bar():
            return "pause"
        kind = self.wm.top_kind()
        if kind == "launcher":
            return "home"
        if kind in ("settings", "menu"):
            return kind
        if kind in self._apps_by_id:
            return "app"
        return None

    def _bar_keys(self, i, pressed, edge):
        """The keys on the bar (`BarLayer.focus_keys`), and the ways onto
        it: up from a surface at its top (its `at_top`: a home's tab row
        below the bar, a list's first row; the handheld home's tabs walk on
        to it sideways, `BarLayer.focus_beside_tabs`) or an up that went past the top of a page drawn
        last frame (`edge`, this frame's `ui_draw.edge_take`). Neither under a menu, a popover or
        a dialog, which hold the keys. True where the bar took them.
        `handle_input` calls it only while the bar holds the keys or one of
        those ways is taken."""
        bl = self.bar_layer
        if bl.focus is not None and self.bar_where() != bl.focus_where:
            bl.focus_leave()            # the surface under it went (a tap, a door)
            return False
        held = (self._modal_layer() is not None and not self._pause_bar()) or self._about
        if bl.focus is not None:
            took = False
            if pressed and not held:
                if bl.focus_where == "home":
                    self.ach_ui.konami_watch(i)
                took = bl.focus_keys(i)
            # the strips name the bar's keys while it holds them, not under
            # a popover one of its items opened
            _ui_draw.on_bar(bl.focus is not None and self._modal_layer() is None
                            and not self._about)
            return took
        if held:
            return False
        where = self.bar_where() if (edge or (pressed and i.steps("up"))) else None
        if where is None:
            return False
        layer = self.pause if where == "pause" else self._content_layer()
        if edge and where != "pause":
            # a page's: not a canvas's or a caret's, whose own popovers draw pages
            steps = getattr(layer, "ball_steps", True)
            if not (steps() if callable(steps) else steps):
                return False
        else:
            top = getattr(layer, "at_top", None)
            if top is None or not top():
                return False
        if not bl.focus_enter(where, "tabs" if where == "home" else "page",
                              self.bar_open_x(where)) or not pressed:
            return False
        if where == "home":
            self.ach_ui.konami_watch(i)
        return True

    def _pause_bar(self):
        """The pause menu holds the keys and the bar shows over it, nothing
        else over them: the bar's keys are the pause menu's to share."""
        return (self.pause.open and self.pause.shows_bar() and not self.sysmenu.open
                and not self.confirm.open)

    def bar_open_x(self, where):
        """Where on `where`'s bar the keys land (an x in its bar's
        coordinates): the Editor's open tab's chip; None, the first item."""
        if where != "menu":
            return None
        box = self.editor_app.tab_box(self.editor_app.tab)
        got = self.bar_layer.cell("menu", "zone")
        if box is None or got is None:
            return None
        return box[0] - got[0][1][0] + box[2] // 2

    def _caret_step(self, i):
        """A direction pressed while a caret has the keys: `nav` it, and
        no layer sees the press. False where no caret wants it."""
        dx = i.steps("right") - i.steps("left")
        dy = i.steps("down") - i.steps("up")
        if (getattr(i, "mods", 0) & (_CTRL | _ALT)) == _CTRL | _ALT:
            return False              # Ctrl+Alt+arrows move the desk Editor's dividers
        if not (dx or dy) or self.pause.open or self.confirm.open or self.sysmenu.open \
                or self.wm.popover_takes_keys():
            return False
        if not ((self.wm.top_is("menu") and self.menu_view in ("code", "theme_config")
                 and self.editor is not None)
                or self.focused_cart_editor() is not None):
            return False
        self.nav(dx, dy)
        i.spend_edges()
        return True

    def _menu_keys(self):
        """A menu over a text surface takes the keys as a list does: while
        the pause menu, the delete confirm or the system menu shows on the
        handheld, the keyboard is out of text mode, so the T-Deck's W A S D
        step its rows (moy_kbd.c, THE MOVE KEYS) and K is its back. The
        surface's text mode comes back as the menu closes, unless what the
        menu did set a mode of its own (a row that opened another surface).
        `handle_input` calls it only while one of the three shows or
        `_menu_over` says one did."""
        over = not self.windowed_chrome and self._modal_layer() is not None
        if over == self._menu_over:
            return
        self._menu_over = over
        if over:
            self._text_back = None
            if self.input.text_mode:
                self._set_text_mode(False)
                self._text_back = self._text_sets
        elif self._text_back is not None:
            if self._text_back == self._text_sets:
                self._set_text_mode(True)
            self._text_back = None

    def _sync_cart_text_mode(self):
        # Cart text input (#38/#42): a RUNNING cart opts into text-keyboard mode by
        # calling textmode(True) (make_api), which sets input.text_mode. Games leave it
        # off (the default) and keep the raw/game keyboard so a held direction keeps
        # firing btn(). When a cart asks for text mode we flip the keyboard to clean
        # 1-byte ASCII (set_game_mode(False)) so key()/keyp() yield typeable bytes;
        # when it turns text mode back off we restore game mode. Idempotent (the
        # keyboard's set_game_mode only talks to the HW on a real transition), called
        # each running-cart frame so a mid-cart textmode() toggle takes effect. No-op
        # on the host (no keyboard) -- there the same flag gates type_char routing in
        # ConsoleDriver. On older keyboard firmware set_game_mode(True) is a no-op
        # (stays ASCII) and the hold-latch fallback applies, so this is safe.
        want_text = bool(getattr(self.input, "text_mode", False))
        kb = self.keyboard
        if kb is not None:
            kb.set_game_mode(not want_text)

    def focused_cart_editor(self):
        """The cart editor handle holding the keyboard, or None."""
        return self.run.player.focused_editor()
