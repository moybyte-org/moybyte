"""The Workstation's CANVAS binding: a run's own game canvas and its release.
`bind_run_canvas` gives a cart whose manifest declares a smaller raster a
canvas of that size (SPEC.md 3.1), `bind_app_canvas` gives a responsive app
cart the whole system surface (#181) -- or, on the desk, a window surface of
its own (`_bind_window_canvas`) -- and `release_run_canvas` undoes either at
the run's death. `release_held` frees one of the layers a modal holds the
surface under it in. A mixin of `Workstation`; the composite and the
coordinates the frame reads every frame (`_composite_game`, `game_view`,
`_game_xy`, `app_window_size`) stay on the class.
"""

try:
    from chrome import _err_text
    import ui_draw as _ui_draw
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime.chrome import _err_text
    from runtime import ui_draw as _ui_draw


class CanvasBinding:

    def bind_run_canvas(self, w, h):
        """Bind a per-run GAME canvas for a cart whose manifest declares a
        smaller raster: the cart draws (and W/H report) the declared size, and
        the WM's viewport()/composite scale it up exactly like a cart-declared
        view -- on a shared-canvas tier (the T-Deck, where the boot canvas IS
        the glass) the boot canvas is promoted to SYSTEM canvas for the run, so
        `sc is gc` stops short-circuiting and the composite runs. Returns True
        when bound (or already native-size); False when this backend has no
        factory -- the caller refuses the cart cleanly (SPEC.md 3.1), never
        runs it at dimensions it did not ask for."""
        stock = self.canvas
        if w == stock.w and h == stock.h:
            return True                 # declared == native raster: nothing to bind
        mk = self.make_game_canvas
        if mk is None:
            return False
        # The run's own: its pixels are on loan to the run ("cart"), so the
        # run's end returns them with its layers, after moycore has let go.
        try:
            small = mk(w, h, "cart")
        except Exception as exc:  # noqa: BLE001 -- an alloc failure refuses, not crashes
            print("Moybyte run canvas failed:", _err_text(exc))
            small = None
        if small is None:
            return False
        self._run_canvas = small
        self._run_canvas_stock = stock
        self._run_canvas_shared = self._sys_canvas is None
        if self._run_canvas_shared:
            self._sys_canvas = stock    # promote: the boot canvas is the glass
            self._surface_write()
        self.canvas = small
        return True

    def bind_app_canvas(self):
        """Bind the SYSTEM canvas as a RESPONSIVE app cart's draw surface (#181).

        The sibling of `bind_run_canvas`, and its opposite direction: that one
        gives a cart a SMALLER raster than the glass, this one gives an app cart
        the whole responsive system surface so `_layout(w, h, fs)` means
        something. Because `ws.canvas` then IS `ws.sys_canvas`, every downstream
        consumer degrades for free -- `composite_game` short-circuits on the
        identity check it has always had, `viewport()` reads (0, 0, 1) and
        `game_xy` becomes the identity, so the pointer arrives in the same
        coordinates the app drew in.

        Returns False on a SHARED-canvas tier (the T-Deck, where the boot canvas
        IS the glass and the two are already one object): there is nothing to
        bind and nothing to change, which is exactly why the handheld's pixels
        cannot move. Released by `release_run_canvas`, on every exit path.

        In the windowed DESK world the system canvas is never bound: a cart
        lives in a WINDOW, and `wm_paint`'s `_draw_player_window` blits
        `ws.canvas` into it, so with the system canvas bound the window blits
        the screen into a rectangle OF that screen (measured).
        There the app takes a window surface of its own instead
        (`_bind_window_canvas`). In console mode (`windowed_chrome` False) it
        gets the whole system surface."""
        sc = self._sys_canvas
        if self.windowed_chrome:
            return self._bind_window_canvas()
        if sc is None or sc is self.canvas or self._run_canvas is not None:
            # ...or a cart-declared small canvas already holds the slot: a
            # manifest asking for 128x128 AND a _layout is contradictory, and
            # the declared size is the one with a SPEC contract behind it.
            return False
        self._run_canvas = sc
        self._run_canvas_stock = self.canvas
        self._run_canvas_shared = False
        self.canvas = sc
        self.app_full_canvas = True
        self._surface_write()             # the strip is the responsive one now
        return True

    def _bind_window_canvas(self):
        """A responsive app cart's surface on the desk: a canvas of the
        desk's work area (`WindowedWM.app_surface`) on loan to the run, of
        which its window shows the content rect (`app_window_size`). True
        when bound; False where the tier has no factory or the WM no desk."""
        mk = self.make_game_canvas
        size = getattr(self.wm, "app_surface", None)
        if mk is None or size is None or self._run_canvas is not None:
            return False
        w, h = size()
        try:
            cv = mk(w, h, "cart")
        except Exception as exc:  # noqa: BLE001 -- an alloc failure refuses, not crashes
            print("Moybyte window canvas failed:", _err_text(exc))
            cv = None
        if cv is None:
            return False
        self._run_canvas = cv
        self._run_canvas_stock = self.canvas
        self._run_canvas_shared = False
        self.canvas = cv
        self.app_full_canvas = True
        self.app_window_canvas = True
        self._surface_write()
        return True

    def release_run_canvas(self):
        """Undo bind_run_canvas / bind_app_canvas at run death
        (Player.release_world) -- the cart_quit pattern, idempotent.
        Identity-guarded so a backend that swapped ws.canvas mid-run (the
        web-view Tee) is never clobbered."""
        self.app_full_canvas = False
        self.app_window_canvas = False
        stock = self._run_canvas_stock
        if stock is None:
            self._surface_write()
            return
        if self.canvas is self._run_canvas:
            self.canvas = stock
        if self._run_canvas_shared and self._sys_canvas is stock:
            self._sys_canvas = None     # back to the shared-canvas degradation
        self._run_canvas = self._run_canvas_stock = None
        self._run_canvas_shared = False
        self._surface_write()

    def release_held(self):
        """Free one of the layers a modal holds what is under it in -- a
        page held washed, the frame under the pause card (`ui_draw.Hold`) --
        the fit check's last releases: True when there was one."""
        return _ui_draw.release_held(self)
