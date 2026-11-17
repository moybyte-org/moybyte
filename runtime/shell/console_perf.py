"""The Workstation's perf METERS: the per-frame timing fields and the query
API the device diag reads (`perf_sample`/`perf_breakdown`/`perf_backdrop`/
`perf_pointer`/`perf_batch`/...), the perf-capture frame tail that fills
them, and the expensive-event counter `note_cost`. The console's `ws.perf`;
every field it writes is a flat attribute the frame loop and both WMs read
directly, but for what changes every frame, which lives on `FrameState`
(console_frame.py).
"""

from chrome import (_ticks_ms, _ticks_us, _ticks_diff)
from settings_layer import SETTINGS_TOGGLES


def _ema(cur, sample):
    """One-pole EMA (alpha 0.15) with a <=0 "unseeded" bootstrap -- the perf
    readouts' smoothing, written once (frame() applies it to ~10 fields)."""
    return float(sample) if cur <= 0 else cur + (sample - cur) * 0.15


# The PERF fields the console's own frame measures (perf_push).
_CONSOLE_METERS = ("draw", "flush", "logic", "render", "chrome", "wmr", "wmw",
                   "wms", "home", "tick", "miss")


class PerfMeters:

    def __init__(self, ws):
        self.ws = ws
        self._perf_sched = None     # the scheduler whose misses the PERF line counts
        self._perf_miss = 0

    def _init_perf(self):
        """The perf/diag measurement fields (#43/#44/#66/#68)."""
        # The persisted ON/OFF settings, at the registry's declared defaults
        # (#209 section 7 -- SETTINGS_TOGGLES in settings_layer.py carries each
        # one's prose). FLAT ATTRIBUTES on purpose and forever: both WMs read
        # show_fps per painted game frame, and the Player reads `steady` at
        # every run start. load_system replaces these with the store's values
        # at boot.
        ws = self.ws
        for _key, _label, _default, _setter, _gate, _dev in SETTINGS_TOGGLES:
            setattr(ws, _key, _default)
        # Frame-time breakdown HUD (#43/#44 perf): off by default; tap the FPS
        # readout (bottom-right, while a cart runs) to toggle it. When on, frame()
        # records the per-frame split in ms -- flush_ms is the compositor's panel
        # DMA flush (comp.flush(); ~0 on the host's _NullComp), draw_ms is the
        # rest (cart _update/_draw + the console's own draw = total minus flush).
        # All EMA-smoothed like frame_state.fps so the numbers read steady, not
        # single-frame jitter. This tells us whether the wall is the SPI flush or the per-frame
        # MicroPython draw cost on device. Measurement only -- no render-path change.
        ws.perf_hud = False         # frame-time breakdown HUD shown? (tap FPS to toggle)
        ws.uncap = False           # the serial `uncap` diag (the tick model): a cart
                                      # started while it is on draws every loop frame
        # perf_capture decouples the per-frame timing MEASUREMENT from drawing the
        # HUD: when either perf_hud OR perf_capture is set, frame() records the
        # flush/draw split (the two cheap ticks calls below). The device backend
        # (moy_runtime.run_desktop) sets perf_capture=True so it can SAMPLE these
        # numbers into the offline diag log without painting the HUD on screen.
        # Default False -> host behaviour is byte-identical (no extra ticks calls).
        ws.perf_capture = False     # measure flush/draw without drawing the HUD
        ws.flush_ms = 0.0          # smoothed comp.flush() ms (panel DMA)
        ws.draw_ms = 0.0           # smoothed draw ms (total frame - flush)
        # DRAWBRK phase split of draw_ms (#43 follow-up): where the per-frame draw
        # cost actually goes -- cart _update, cart _draw, and the console chrome
        # (bar + cursor + overlays, the remainder). Surfaced via perf_breakdown().
        ws.upd_ms = 0.0            # smoothed cart _update(dt) ms (game LOGIC)
        ws.cart_ms = 0.0           # smoothed cart _draw() ms (RENDERING)
        ws._audio_ms = 0.0          # smoothed audio.tick(dt) ms (mixer feed)
        ws.chrome_ms = 0.0         # smoothed chrome ms (= draw - upd - cart - audio)
        # The hitch logger's hp() detail (#184): the pointer walk, split.
        # _pf_ptr is a fixed 6-slot scratch overwritten in place by
        # handle_pointer -- [total_us, pre_us, worst_us, worst_id, claim_id,
        # n_visited].
        ws._pf_ptr = [0, 0, 0, None, None, 0]
        # The DRAWBRK split's per-frame scratch, MICROSECONDS: Player.tick
        # writes it on the cart path under capture, frame() zeroes it on every
        # captured frame, _frame_perf_end converts once at the EMA.
        ws.pf_upd = 0      # cart _update(dt) (game LOGIC)
        ws.pf_cart = 0     # cart _draw() + the backdrop restore (RENDERING)
        ws.pf_audio = 0    # audio.tick(dt) (mixer feed)
        ws.pf_bg = 0       # the backdrop restore's share of _pf_cart
        ws._ptr_last_x = -1     # handle_pointer's idle fast-path: last routed
        ws._ptr_last_y = -1     # pointer position (ints -- no per-frame tuple)
        ws._ptr_was_down = False  # ...and whether it was held (release edge)
        ws._gp_idle = None      # the idle game_pointer we last published, and
        ws._gp_key = [None] * 8  # the geometry it was mapped through: the
                                   # declared view, both canvases, their dims
                                   # and, on a desk, the viewport's answer
        # Per-frame method probes, cached by the object they were taken on
        # (#66 lever 1): getattr on a method allocates a bound method each call.
        ws._rs_cv = None        # _reset_canvas_state's canvas / reset_state
        ws._rs_fn = None
        ws._fb_cv = None        # _flush_batches' game canvas / flush_batch
        ws._fb_cv_fn = None
        ws._fb_sc = None        # ...and the system canvas's
        ws._fb_sc_fn = None
        ws._lb_wm = None        # frame()'s WM / letterbox_inplace
        ws._lb_fn = None
        ws._loading_host = None  # the loading pill's UI context (_draw_loading_toast)
        ws._loading_rect = None  # ...and the rect it drew last
        ws._dsf_comp = None     # _disarm_fn's compositor / disarm_scale_fold
        ws._dsf_fn = None
        ws._probe_sc = None     # frame()'s system-canvas probe (view) and the
                                  # game canvas's `buf`, re-taken only when the
                                  # object changes
        ws._probe_view = None
        ws._probe_gc = None
        ws._probe_buf = None
        # #184 deferred transitions: [armed, fn] entries queued by defer().
        # A tap handler schedules its heavy transition here instead of running
        # it inside the pointer walk; frame() paints the acknowledgment first
        # (arming the entry after the flush), then runs it at the next frame's
        # top -- so the pressed state is ON GLASS during the load stall.
        ws.deferred = []
        ws._bg_ms = 0.0   # #172: backdrop restore, a SUB-slice of _cart_ms
        # (The clock-text cache moved to self.bar_layer with the rest of the bar #66.)

    def note_cost(self, what):
        """Count one EXPENSIVE event: a cache build, or a call into storage.

        Every performance bug found in the 2026-07-26 session was a violated
        assumption that produced NO SIGNAL -- a cache silently missing 100% of the
        time (the bar strip keyed on canvas identity while the WM alternated
        destinations: 72ms of an 86ms frame, twice per gesture), an accessor
        silently rebuilding per row, storage reads silently landing on drag
        frames. None of them broke anything; they just made two frames in
        thirty-one five times slower, which only shows up if you happen to measure
        the exact frame. Each took hours to find, and three wrong models died on
        the way.

        So the expensive paths say so. Deliberately counted on the BUILD side
        only, never on the hit side: a cache hit is the hot path and stays
        untouched, while a build already costs 15-100ms, so one dict increment
        there is free. Hit RATE is not the interesting number anyway -- "rebuilt
        44 times in 44 frames" is the thing that screams, and a bare build count
        says it.

        Read it two ways: the P4's `state` serial command reports it, so a glass
        session sees a thrashing cache immediately; and tests assert BUDGETS over a
        run of frames (tests/test_top_bar.py, tests/test_cover_pipeline.py), which
        turns this whole bug class from a perf mystery into a test failure."""
        ws = self.ws
        d = ws.costs
        d[what] = d.get(what, 0) + 1

    def _frame_perf_end(self, frame_t0):
        """The perf-capture frame tail: time the panel DMA flush in isolation,
        back out the draw span, and EMA the DRAWBRK split. Only called when
        perf_hud/perf_capture is on -- the kid-mode path flushes directly, so the
        render path itself is unchanged. The timing fields stay on the
        Workstation (the device diag contract -- perf_sample/perf_breakdown
        read them).

        EVERY BRACKET IN HERE IS MICROSECONDS (2026-08-14), converted to ms once,
        at the EMA. It used to be ticks_ms, and that quietly broke the one number
        the shell's frame budget was being argued from: `chrome` is a residual
        (draw - upd - cart - audio), and integer-ms differences truncate toward
        zero with every term's loss landing in the last one."""
        ws = self.ws
        _upd = ws.pf_upd                     # us
        _cart = ws.pf_cart                   # us
        _audio = ws.pf_audio                 # us
        _flush_t0 = _ticks_us()
        ws.comp.flush()
        _flush = _ticks_diff(_ticks_us(), _flush_t0)
        _total = _ticks_diff(_ticks_us(), frame_t0)
        _draw = _total - _flush
        if _draw < 0:
            _draw = 0
        ws.flush_ms = _ema(ws.flush_ms, _flush / 1000.0)
        ws.draw_ms = _ema(ws.draw_ms, _draw / 1000.0)
        # Everything below is the DEEP tail (the DRAWBRK split + the HITCH
        # logger's raw copies): diag-session data, and 6 boxed floats + ~8 EMA
        # calls of churn per frame -- perf_hud alone stops here (the chip shows
        # fps/draw/flush, all set above).
        if not ws.perf_capture:
            return
        # DRAWBRK split: cart _update (logic) / cart _draw (render) / audio.tick /
        # console chrome (remainder = bar + cursor + overlays).
        _chrome = _draw - _upd - _cart - _audio
        if _chrome < 0:
            _chrome = 0
        ws.upd_ms = _ema(ws.upd_ms, _upd / 1000.0)
        ws.cart_ms = _ema(ws.cart_ms, _cart / 1000.0)
        ws._audio_ms = _ema(ws._audio_ms, _audio / 1000.0)
        ws.chrome_ms = _ema(ws.chrome_ms, _chrome / 1000.0)
        # #172: the declared-backdrop restore. NOT a fourth peer of the split --
        # it is already inside cart_ms (Player.tick charges it to render, where
        # the cart's own cls would have landed). Tracked separately only so
        # DRAWBRK can say how much of render is the backdrop.
        ws._bg_ms = _ema(ws._bg_ms, ws.pf_bg / 1000.0)

    def perf_sample(self):
        """Snapshot of the current per-frame perf numbers for offline sampling:
        (cart_name, fps, flush_ms, draw_ms). Used by the device backend's diag
        sampler (moy_runtime.run_desktop) to log a PERF line every few seconds
        while a cart runs. flush_ms/draw_ms are only meaningful when perf_capture
        (or perf_hud) is on -- run_desktop sets perf_capture=True at boot. Backend-
        agnostic + host-safe: pure reads, no drawing, no hardware. Returns None
        when no cart is actively running (nothing useful to sample)."""
        ws = self.ws
        running = (ws.wm.top_is_player() and ws.run.project.cart is not None  # Stage 6d
                   and ws.run.player.cart_error is None)
        if not running:
            return None
        cart = ws.run.project.cart
        name = cart.get("title") or cart.get("path") or "?"
        return (name, ws.frame_state.fps, ws.flush_ms, ws.draw_ms)

    def perf_push(self, loop, front=False):
        """The console's half of the PERF line into the kernel's sampler
        (native/moy_kernel/moy_perf.c), on the frame a sample is due: the
        cart, the phase EMAs, the windowed WM's and the launcher's splits,
        the lockstep rate and the tick model. The kernel adds what it counts
        itself (fps, busy, gc, the compositor's overlap) and writes the line.

        The windowed WM's meters are TAKEN: only a frame that drew that layer
        this window may answer, so a fullscreen cart never inherits the
        desktop's last number. `net` is spent by its one reader, here. The
        tick model's misses are this window's, against a baseline that
        belongs to the scheduler that counted them (a new run counts from 0).

        `front`: a kernel run took the frame (moy_play's front), so no
        console frame runs to measure: the half names the cart and leaves
        every meter of the console's absent."""
        ws = self.ws
        cart = ws.run.project.cart
        loop.perf_cart(cart.get("title") if cart else None)
        if front:
            for name in _CONSOLE_METERS:
                loop.perf(name, None)
            loop.perf("net", self.perf_net())
            loop.perf("lock", self.perf_lock())
            return
        loop.perf("draw", ws.draw_ms)
        loop.perf("flush", ws.flush_ms)
        loop.perf("logic", ws.upd_ms)
        loop.perf("render", ws.cart_ms)
        loop.perf("chrome", ws.chrome_ms)
        v = getattr(ws, "pf_wm_restore", None)
        if v is not None:
            ws.pf_wm_restore = None
        loop.perf("wmr", v)
        v = getattr(ws, "pf_wm_windows", None)
        if v is not None:
            ws.pf_wm_windows = None
        loop.perf("wmw", v)
        v = getattr(ws, "pf_wm_stamp", None)
        if v is not None:
            ws.pf_wm_stamp = None
        loop.perf("wms", v)
        loop.perf("home", getattr(ws, "pf_home", None))
        loop.perf("net", self.perf_net())
        loop.perf("lock", self.perf_lock())
        pl = ws.run.player
        if pl is None or not pl.tick_ms:
            self._perf_sched = None
            self._perf_miss = 0
            loop.perf("tick", None)
            loop.perf("miss", None)
            return
        sc = pl.sched
        if sc is not self._perf_sched:
            self._perf_sched = sc
            self._perf_miss = 0
        miss = sc.misses - self._perf_miss
        self._perf_miss = sc.misses
        loop.perf("tick", (sc.rate, sc.div))
        loop.perf("miss", miss)

    def perf_net(self):
        """The PERF line's `net=` witness: the #65 lockstep tick rate in ticks/s,
        or **None when no session is gating frames at all**.

        None is not "zero ticks" and must never be printed as 0 -- a board with
        no lever reports absence (the 2026-08-22 doctrine; `EspNowLink.status`
        answers the same way for the same reason). A running match reports a
        real rate: ~30 while it is healthy, lower under stall pressure, 0 while
        it is matched but frozen.

        This is the PERF emitters' ONE entry to the meter, because the meter
        CONSUMES its sample window (netplay.LockstepSession.tps) -- perf_sample()
        stays the `is a cart running?` probe half a dozen diag helpers call, and
        must not carry a number that a second caller would spend."""
        ws = self.ws
        np = ws.netplay
        return None if np is None else np.tps(_ticks_ms())

    def perf_lock(self):
        """The PERF line's `lock=`: where a match's tick rate went
        (LockstepSession.lock), None with no session. Its one reader, beside
        perf_net."""
        ws = self.ws
        np = ws.netplay
        return None if np is None else np.lock(_ticks_ms())

    def perf_breakdown(self):
        """(_upd_ms, _cart_ms, _audio_ms, _chrome_ms): the EMA phase split of draw_ms --
        cart _update (game LOGIC), cart _draw (RENDERING), audio.tick (mixer feed), and
        console chrome (bar + cursor + overlays, the remainder). Used by the device
        diag's DRAWBRK line to find where the per-frame draw cost actually goes (cart
        logic vs rendering vs audio vs chrome). Only meaningful while a cart runs with
        perf_capture/perf_hud on."""
        ws = self.ws
        return (ws.upd_ms, ws.cart_ms, ws._audio_ms, ws.chrome_ms)

    def perf_backdrop(self):
        """The EMA ms of the declared-backdrop restore (#172) -- `background()`'s
        per-frame repaint, run by Player.tick before the cart's _draw.

        A SUB-slice of perf_breakdown()'s render, not a fourth bucket: it is the
        cart's own drawing, standing in for the cls() it would otherwise make
        first thing. Measured on its own so that it cannot surface as CHROME
        (on the T-Deck it read as ~4.7ms of shell cost while it did). Feeds
        DRAWBRK's `bg=`."""
        ws = self.ws
        return ws._bg_ms

    def perf_pointer(self):
        """(total_ms, pre_ms, worst_ms, worst_id, claim_id, n) for the last
        handle_pointer call (#184), or None if it never ran under capture.

        `pre` is the bookkeeping before the routing walk (_tick_pointer_dt +
        _game_xy + the focus probe), `worst`/`worst_id` the dearest single
        layer.handle_pointer in the walk, `claim_id` the layer that consumed the
        tap, `n` how many layers were visited. total - pre - worst says whether
        the cost was one layer or spread; a total far BELOW the loop's own hp=
        says the time went somewhere outside this method entirely."""
        ws = self.ws
        pf = ws._pf_ptr
        if not pf[5]:
            return None
        return (pf[0] / 1000.0, pf[1] / 1000.0, pf[2] / 1000.0,
                pf[3] or "-", pf[4] or "-", pf[5])

    def perf_batch(self):
        """(flushes, sprites, maxrun) for the auto-batch this frame (#63 profiling). N
        sprites coalesced into ONE blit_batch read flushes=1 / maxrun=N; drawn one-by-one
        read flushes=N / maxrun=1 -- so this PROVES the batch stayed intact at runtime,
        which pixel-parity can't. Counters reset per frame in frame() when perf capture is
        on; a lone item still counts as a (flushes=1, maxrun=1) direct blit."""
        ws = self.ws
        cv = ws.run.canvas
        return (getattr(cv, "_batch_flushes", 0),
                getattr(cv, "_batch_sprites", 0),
                getattr(cv, "_batch_maxrun", 0))
