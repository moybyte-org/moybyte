"""The device WEB VIEW controller (#41/#22), extracted from moy_runtime.py.

WebView drives the device console to a browser on the same WiFi via the shared
draw-command protocol (moy_webserver): it records the running cart's per-frame
draw calls (a DrawRecorder fed by a TeeCanvas over the real DeviceCanvas) and
services the persistent WebSocket / HTTP-poll fallback BETWEEN frames from
run_desktop's single-threaded loop (begin_frame/commit_frame/poll). STREAM MODE
drives the panel headless (comp.skip_flush) while a browser is connected. Off by
default -- Settings -> WEB VIEW swaps the Tee in.

Self-contained controller: imports the leaf device_util (_diag_note) + the shared
console primitives (NAMES / Pointer / _cursor_delta -- console does not import any
device module, so no cycle), and lazily `moy_webserver` / `tdeck_display` inside
its methods. Device-only module (modules/, auto-frozen). WiFi<->LCD-DMA coexistence
(#38/#40) + the socket/WebSocket layer remain UNVERIFIED on hardware.
"""
from device_util import _diag_note
from device_wifi import autoconnect_wifi
from console import NAMES, Pointer, _cursor_delta


class WebView:
    """Device web view controller (#41/#22): owns the draw-command recorder, the
    non-blocking HTTP + WebSocket server, and the browser-input injection, and is the small
    object the shared console's Settings "WEB VIEW" row toggles (it reads .enabled +
    .url() and calls .toggle()).

    Lifecycle, all driven from run_desktop's single-threaded loop:
      * It starts OFF: ws.canvas stays the RAW DeviceCanvas, so the normal (no-browser)
        path has ZERO per-draw overhead. Turning the view ON swaps a recording TeeCanvas
        in as ws.canvas (and rebinds the wallpaper/running cart to it); even then the
        recorder only records while a browser's WebSocket is connected.
      * toggle() brings WiFi up (reusing the saved-credential autoconnect), reads the
        STA IP, and starts/stops the server. It needs WiFi already joined via the WiFi
        cart; with no saved network it stays OFF and surfaces the reason.
      * Each loop iteration: begin_frame() (start a recording if a WS client is live)
        BEFORE the render, feed_input() to inject queued browser events BEFORE
        inp.begin_frame(), commit_frame() AFTER ws.frame(), and poll() once BETWEEN frames
        to accept new connections + service the persistent WebSocket (drain its input ->
        apply, push the latest committed frame down it). None of these block the render loop.

    TRANSPORT (#41): the live channel is a persistent WebSocket -- frames PUSH down, input
    pushes up, on one socket (no per-frame HTTP handshake). The page + assets still load over
    plain HTTP, and the legacy GET/POST /frame + POST /input endpoints remain as a fallback.

    NEEDS ON-DEVICE VERIFICATION: the socket server + WiFi<->LCD-DMA RAM coexistence
    (#38/#40) are unproven on hardware here."""

    def __init__(self, ws, canvas, inp, pointer, wifi, port=8080):
        self._ws = ws
        self._canvas = canvas          # the REAL DeviceCanvas (panel draws here)
        self._inp = inp
        self._pointer = pointer
        self._wifi = wifi
        self._port = port
        self.enabled = False
        self._url = ""
        self._server = None
        self._rec = None
        self._tee = None
        # Browser one-shot button presses pulsed for exactly one frame (so pressed()
        # fires once), and the held set the browser drives via {type:"hold"}. The
        # trackball-style pan accumulates between feeds.
        self._press_queue = []
        self._pulsed = []
        self._pan = [0, 0]
        self._key_queue = []
        self._held = set()         # browser-held buttons (joystick/WASD), re-asserted each
        self._held_last = set()    # frame in feed_input AFTER keyboard.poll clears them
        # Browser pointer intent, applied AFTER the physical touch read each frame so
        # it isn't clobbered. _br_active True while a browser finger is down (so the
        # cursor follows the browser drag); _br_click latches a tap edge to consume once.
        self._br_x = pointer.x
        self._br_y = pointer.y
        self._br_active = False
        self._br_click = False
        # The cart title whose bitmaps the recorder's atlas currently holds. When the open
        # cart changes the atlas must reset (a new cart's Images mustn't collide with stale
        # id()-keyed indices), mirroring the browser refetching /assets + clearing caches.
        self._atlas_cart = None
        # STREAM MODE (#41 30fps lever): True while the device is headless for a browser
        # that's actively playing (skip the panel rasterize + flush; the cart still runs
        # logic + records cheap commands). Tracked here so begin_frame can detect the
        # enter/exit EDGE: on enter, paint a one-time "playing in browser" notice + flush
        # it; on exit, force a full redraw + re-light so the device panel resumes cleanly.
        self._streaming = False
        try:
            import moy_webserver
            self._web = moy_webserver
            self._rec = moy_webserver.DrawRecorder(canvas.w, canvas.h)
            self._tee = moy_webserver.TeeCanvas(canvas, self._rec)
        except Exception as exc:  # noqa: BLE001 -- no module -> the controller stays inert
            print("Moybyte web: module unavailable:", exc)
            self._web = None

    def install(self):
        """Boot wiring: the web view starts OFF, so ws.canvas stays the RAW DeviceCanvas
        and there is ZERO per-draw overhead in the normal (no-browser) path -- the Tee is
        only swapped in when Settings turns the view ON (_bind), and swapped back out when
        it's turned OFF (_unbind). Returns the canvas the loop calls sync_back() on (the
        raw DeviceCanvas, which both the Tee -- via delegation -- and the off path share).
        """
        return self._canvas

    def _bind(self):
        """Swap the TeeCanvas in as ws.canvas (panel still renders through it -> the Tee
        forwards every call to the real DeviceCanvas) and rebind the live drawers to it so
        their draws reach the recorder: recompile the wallpaper, and restart a running cart.
        Without the rebind the wallpaper/cart draw funcs stay bound to the raw canvas and the
        browser sees nothing on the home/cart screen (the same gotcha the host web console
        guards against by recompiling the wallpaper)."""
        if self._tee is None:
            return
        self._ws.canvas = self._tee
        try:
            wp = getattr(self._ws, "wallpaper_id", None)
            if wp:
                self._ws.select_wallpaper(wp, persist=False)   # rebind backdrop to the Tee
        except Exception:  # noqa: BLE001 -- a rebind hiccup must not crash the toggle
            pass
        self._rebind_running_cart()

    def _unbind(self):
        """Swap the raw DeviceCanvas back in as ws.canvas (zero per-draw overhead again)
        and rebind the wallpaper/cart to it, the mirror of _bind."""
        self._ws.canvas = self._canvas
        try:
            wp = getattr(self._ws, "wallpaper_id", None)
            if wp:
                self._ws.select_wallpaper(wp, persist=False)
        except Exception:  # noqa: BLE001
            pass
        self._rebind_running_cart()

    def _rebind_running_cart(self):
        """If a cart is open, re-run it so its namespace recompiles against the current
        ws.canvas (apply() -> _start() rebuilds make_api). No-op on the home/editor screens
        (only a running cart binds the draw API). Guarded so a cart restart can't crash the
        toggle -- if it fails the cart simply isn't mirrored until reopened."""
        try:
            if getattr(self._ws, "cart", None) is not None and self._ws.screen == "desktop":
                self._ws.apply()
        except Exception:  # noqa: BLE001
            pass

    def available(self):
        return self._web is not None

    def url(self):
        return self._url

    # -- Settings toggle -----------------------------------------------------
    def toggle(self):
        if not self.available():
            return
        if self.enabled:
            self._stop()
        else:
            self._start()

    def _start(self):
        # Bring WiFi up (reuse the saved-credential autoconnect: only joins a network
        # the kid already added via the WiFi cart). No creds -> stay OFF with a reason.
        ip = None
        try:
            connected, _ssid, ip = self._wifi.status()
            if not connected:
                if autoconnect_wifi(self._wifi):
                    _conn, _ssid, ip = self._wifi.status()
        except Exception as exc:  # noqa: BLE001
            print("Moybyte web: wifi check failed:", exc)
        if not ip:
            self._url = "no wifi"
            self.enabled = False
            _diag_note("web", "start aborted: no wifi (join via WiFi cart first)")
            return
        try:
            self._server = self._web.WebServer(self._rec, _WebProvider(self), self._port)
            if self._server.start(ip):
                self.enabled = True
                self._url = self._server.url()
                self._bind()                 # swap the Tee in (records when a browser polls)
                print("Moybyte web view ON:", self._url)
                _diag_note("web", "serving at %s" % self._url)
            else:
                self.enabled = False
                self._url = "bind failed"
        except Exception as exc:  # noqa: BLE001
            print("Moybyte web: start failed:", exc)
            self.enabled = False
            self._url = "error"

    def _stop(self):
        if self._server is not None:
            try:
                self._server.stop()
            except Exception:  # noqa: BLE001
                pass
        self._server = None
        self.enabled = False
        if self._rec is not None:
            self._rec.enabled = False
            self._rec.record_only = False
        # If we were mid-stream when the view was turned off, resume the panel cleanly
        # (clears comp.skip_flush, forces a redraw, re-lights) -- no-op if not streaming.
        self._apply_stream_mode(False)
        self._unbind()                       # swap the raw canvas back (zero overhead again)
        print("Moybyte web view OFF")
        _diag_note("web", "stopped")

    # -- loop hooks (guarded; never block the render loop) -------------------
    def begin_frame(self):
        if self._server is None:
            return
        was = self._rec.enabled
        self._server.begin_frame()
        # STREAM MODE edge (#41): the server set recorder.record_only for this frame
        # (True only when a browser is live + this frame is recorded). Drive the panel:
        # skip the flush this frame while streaming, and handle the enter/exit transition
        # so a glance at the device isn't a confusing frozen screen.
        self._apply_stream_mode(self._rec.record_only)
        if not self._rec.enabled:
            return
        # Reset the recorder's sprite atlas when the open cart changes: a new cart's tile
        # Images are fresh objects whose id() could coincide with a freed one's, so a
        # stale index would mis-map. The browser does the matching reset (it refetches
        # /assets + clears its caches on a cart change), so the two stay in lock-step.
        cart = getattr(self._ws, "cart", None)
        title = cart.get("title") if cart else None
        if title != self._atlas_cart:
            self._atlas_cart = title
            self._rec.reset_atlas()
        # When a browser (re)connects, force ONE redraw so it gets a full frame even on
        # an idle screen (the redraw-on-change gate #44 would otherwise record nothing
        # until something changes). A running cart redraws every frame regardless.
        if not was:
            try:
                self._ws.mark_dirty()
            except Exception:  # noqa: BLE001
                pass

    def _comp(self):
        """The device compositor (owns the panel flush). None on a host/no-comp build."""
        return getattr(self._ws, "comp", None)

    def _apply_stream_mode(self, streaming):
        """Drive the panel for STREAM MODE this frame (#41): set comp.skip_flush so the
        flush inside ws.frame() is a no-op while headless, and handle the enter/exit EDGE.
        Idempotent + fully guarded -- a transition hiccup must never crash the loop."""
        comp = self._comp()
        if comp is not None:
            try:
                comp.skip_flush = streaming
            except Exception:  # noqa: BLE001
                pass
        if streaming == self._streaming:
            return                               # no edge this frame
        self._streaming = streaming
        if streaming:
            self._enter_stream()
        else:
            self._exit_stream()

    def _enter_stream(self):
        """ENTER stream mode: the device goes headless (the panel will freeze on whatever
        it last showed). Paint a one-time notice + flush it ONCE so a glance at the device
        reads 'playing in browser', not a confusing frozen frame. The notice is drawn
        straight on the REAL canvas (not the Tee) and flushed with skip_flush forced off,
        so it's the last thing the panel shows until the browser disconnects."""
        try:
            comp = self._comp()
            cv = self._canvas
            cv.cls(NAMES["dark_blue"])
            cv.rect(0, 104, cv.w, 36, NAMES["indigo"])
            cv.print("WEB VIEW", 96, 96, NAMES["yellow"], 2)
            cv.print("playing in browser", 70, 124, NAMES["white"], 1)
            if comp is not None:
                save = getattr(comp, "skip_flush", False)
                comp.skip_flush = False          # force the notice out, once
                comp.flush()
                comp.skip_flush = save
        except Exception as exc:  # noqa: BLE001 -- the notice is cosmetic; never crash
            print("Moybyte web: stream notice failed:", exc)
        _diag_note("web", "stream mode ON (device headless)")

    def _exit_stream(self):
        """EXIT stream mode: the browser disconnected, so resume the device panel cleanly.
        skip_flush is already cleared by _apply_stream_mode; force a full redraw (the cart/
        UI rasterizes again next frame) and re-light the backlight in case it was off."""
        try:
            self._ws.mark_dirty()
        except Exception:  # noqa: BLE001
            pass
        try:
            import tdeck_display
            tdeck_display.set_backlight(True)
        except Exception:  # noqa: BLE001 -- host / display-less: ignore
            pass
        _diag_note("web", "stream mode OFF (panel resumed)")

    def commit_frame(self):
        if self._server is not None:
            self._server.commit_frame()

    def poll(self):
        if self._server is not None:
            try:
                self._server.poll()
            except Exception as exc:  # noqa: BLE001 -- a bad request never bricks the loop
                print("Moybyte web: poll error:", exc)

    def feed_input(self, now):
        """Apply queued browser input. Called once per loop iteration, just BEFORE
        inp.begin_frame() so a browser button press registers a clean one-frame edge:
        last frame's pulsed buttons are released first, then this frame's queued ones
        are held (begin_frame then computes pressed = held - last). The pan nudges the
        cursor like the trackball does. No-op when nothing's queued (the common path)."""
        # Release last frame's one-shot presses.
        for name in self._pulsed:
            try:
                self._inp.set_button(name, False)
            except Exception:  # noqa: BLE001
                pass
        self._pulsed = []
        # Hold this frame's queued one-shot presses (released next feed).
        if self._press_queue:
            for name in self._press_queue:
                try:
                    self._inp.set_button(name, True)
                except Exception:  # noqa: BLE001
                    pass
            self._pulsed = self._press_queue
            self._press_queue = []
        # Re-assert browser-held buttons (joystick / WASD) on top of the physical keyboard.
        # feed_input runs AFTER keyboard.poll(), which clears any button with no physical key
        # down -- so without this the web holds never reach the cart's btn(). Clear ones
        # released since last frame; assert the current held set.
        for name in self._held_last:
            if name not in self._held:
                try:
                    self._inp.set_button(name, False)
                except Exception:  # noqa: BLE001
                    pass
        for name in self._held:
            try:
                self._inp.set_button(name, True)
            except Exception:  # noqa: BLE001
                pass
        self._held_last = set(self._held)
        # Browser trackball pan -> cursor move (mirrors the device loop's _cursor_delta).
        if self._pan[0] or self._pan[1]:
            self._pointer.move(self._pan[0] * 4, self._pan[1] * 4)
            self._pan = [0, 0]
        # Browser typed key -> last_key for THIS frame. Applied here (after the loop's
        # keyboard.poll() which would otherwise reset last_key to 0) so a cart in
        # textmode()/the code editor sees it; consumed so it's one byte for one frame.
        if self._key_queue:
            try:
                self._inp.last_key = self._key_queue[-1]
            except Exception:  # noqa: BLE001
                pass
            self._key_queue = []

    def feed_pointer(self, physical_active):
        """Merge browser pointer intent into the real Pointer. Called in the loop AFTER
        the physical touch read so it isn't clobbered, and only when the physical touch
        is NOT active (a real finger on the device wins). Places the cursor at the
        browser finger and OR-s in a tap edge; returns True if a browser tap fired this
        frame (so the loop sets pointer.click)."""
        clicked = False
        if not physical_active and (self._br_active or self._br_click):
            self._pointer.place(self._br_x, self._br_y)
            self._pointer.down = self._br_active
            if self._br_click:
                self._br_click = False         # consume the tap edge once
                clicked = True
        return clicked

    # -- input event hooks handed to moy_webserver.apply_events ---------------
    def _on_press(self, name):
        self._press_queue.append(name)

    def _on_pan(self, dx, dy):
        self._pan[0] += dx
        self._pan[1] += dy

    def _on_hold(self, name, down):
        # Track a browser-held button (joystick/WASD). feed_input re-asserts the held set
        # AFTER the loop's keyboard.poll() (which clears buttons -- no physical key is down),
        # so the cart's btn() actually sees it. (Setting it here, in poll(), gets wiped by
        # the next keyboard.poll before the cart runs -- the joystick/WASD not-reacting bug.)
        if down:
            self._held.add(name)
        else:
            self._held.discard(name)

    def _on_key(self, code):
        # Queue a typed key; feed_input applies it AFTER the loop's keyboard.poll() so it
        # isn't reset to 0 before the cart reads last_key. One byte per frame, like the
        # T-Deck keyboard's own ASCII path.
        self._key_queue.append(code)

    def _on_esc(self):
        # Leave an open editor/menu panel back to the desktop (mirrors the host esc).
        try:
            if self._ws.screen == "menu":
                self._ws._leave_menu()
        except Exception:  # noqa: BLE001
            pass

    # -- data the server asks for --------------------------------------------
    def assets(self):
        # PAL565 / _decode_moyimg live in moy_runtime's canvas cluster and AUDIO_RATE
        # in its audio consts (neither extracted yet). Lazy import here (call time,
        # after both modules are loaded) to reach them without a load-time cycle --
        # repoint to device_canvas / device_audio when those extract.
        from moy_runtime import AUDIO_RATE, _decode_moyimg, PAL565
        ws = self._ws
        cart = getattr(ws, "cart", None)
        title = cart.get("title") if cart else None
        rate = AUDIO_RATE
        # Paint images (#63 Fold 4): decode the open cart's .moyimg text -> (w, h, index bytes)
        # so /assets ships them ONCE (browser-cached), and the per-frame stream references each
        # by name via ["imgref", ...]. Decoded here (not in the recorder) so a fat base64 blob
        # never rides a frame and starve the defspr budget. Threaded through like the sheet.
        decoded = {}
        raw = getattr(ws, "images", None)
        if raw:
            for name in raw:
                dec = _decode_moyimg(raw[name])
                if dec is not None:
                    decoded[name] = dec
        return self._web.assets_payload(self._canvas.w, self._canvas.h, PAL565,
                                        getattr(ws, "sheet", None),
                                        getattr(ws, "tilemap", None), title, rate,
                                        decoded or None)

    def frame(self):
        cart = getattr(self._ws, "cart", None)
        title = cart.get("title") if cart else None
        cmds = self._rec.frame() if self._rec is not None else []
        return (cmds, title)

    def apply(self, events):
        # Route pointer events through a sink (captured into browser-pointer state and
        # merged later by feed_pointer, so the per-frame physical touch read doesn't
        # clobber them); buttons/keys/pan go through the hooks. apply_events guards each
        # event, so a malformed one is skipped, never raised.
        sink = _PointerSink(self)
        self._web.apply_events(events, self._inp, sink,
                               on_press=self._on_press, on_pan=self._on_pan,
                               on_key=self._on_key, on_esc=self._on_esc,
                               on_hold=self._on_hold)


class _PointerSink:
    """A Pointer-shaped target for moy_webserver.apply_events: place()/down/click write
    into the WebView's browser-pointer intent instead of the live cursor, so the loop's
    physical touch read can't clobber a browser tap (feed_pointer merges it later)."""

    def __init__(self, view):
        self._v = view

    def place(self, x, y):
        self._v._br_x = int(x)
        self._v._br_y = int(y)

    @property
    def down(self):
        return self._v._br_active

    @down.setter
    def down(self, v):
        self._v._br_active = bool(v)

    @property
    def click(self):
        return self._v._br_click

    @click.setter
    def click(self, v):
        if v:
            self._v._br_click = True


class _WebProvider:
    """Thin adapter so moy_webserver.WebServer never holds console refs directly: it
    asks this for /assets, /frame, and to apply /input -- all delegated to the WebView."""

    def __init__(self, view):
        self._v = view

    def assets(self):
        return self._v.assets()

    def frame(self):
        return self._v.frame()

    def apply(self, events):
        self._v.apply(events)
