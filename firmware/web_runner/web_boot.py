"""Moybyte web runner boot (#151): the SHARED console under MicroPython-WASM,
browser-as-GPU.

This is the third console backend, and the thinnest one: the system canvas IS a
`web_view.CommandCanvas` (the recording canvas the host web console swaps in),
so the console never rasterizes a pixel -- each frame's draw-command list is
handed straight to the page's JS replayer by a local call (no WebSocket, no
72KB/s ceiling; the "Zero / browser is the GPU" thesis realized as a WASM
target). The cart API is the shared pure-Python `host_api.make_api` -- the same
backend the PC sim runs -- and input replays through the shared
`web_view.apply_events` decode, so host == device == browser stays one code
path.

Scope: the FULL console, both presentation tiers (owner call, superseding #151's
player-only runner) -- `can_manage=True`, so the Make tile and the editors are
reachable in the browser. pmem/journal writes land in the browser's MEMFS, so
they are ephemeral: a reload resets the machine.

JS contract (see the driver page / harness):
    boot(carts_root, cart=None)   -> build the Workstation over the VFS store
    assets_json()                 -> the /assets payload as a JSON string
    step_frame_json(dt)           -> tick one frame; "" when the redraw was
                                     skipped (#44 dirty gate), else the
                                     frame_payload JSON string
    apply_events_json(text)       -> feed an {"events":[...]} JSON text batch
    open_cart(name)               -> select+run a cart by folder name or title
"""

import json

import console
import host_api
import moy_carts
import palette
import web_view
from input import InputState

_S = {}          # the runner singletons: ws / canvas / driver / served / sink
_AUDIO_RATE = [0]
# [cache key, serialised payload, image names the page holds, full-payload key]
# -- see assets_json() (the incremental-image path).
_ASSETS = [None, None, None, None]


# The page keeps ~this many seconds of PCM scheduled ahead of the audio clock.
# Deep enough to ride out a late frame (wasm GC pause, phone jank -- the 20ms
# scheduling margin alone was the owner-reported crackle), shallow enough that
# a game sfx mixed into the stream isn't noticeably late.
_AUDIO_TARGET = 0.12
_AUDIO_MAX_STEP = 0.20      # bound one frame's top-up render (seconds)


class _RunnerAudio(host_api.FakeAudio):
    """FakeAudio with the calls list capped, plus two web-runner twists
    (#170 round 3 -- the owner's "crackle and slowdown" report):

    * TOP-UP rendering (the crackle fix): the page reports how many seconds
      of PCM it still has scheduled (step_frame_json's audio_ahead); tick()
      renders whatever refills that to _AUDIO_TARGET instead of exactly
      rate*dt. One late frame now eats cushion, not the stream -- the browser
      twin of the device feed's ring top-up (device_audio.py). Without the
      report (ahead < 0: a transport that never sends it) the per-dt render
      stays, unchanged.
    * The synth is the NATIVE moy_audio module when the wasm was built with the
      usermod -- i.e. libmoy, moy-spec's own SPEC.md 8 implementation, compiled
      in (#97). It owns the bank, both sequencers and the mixer; this class only
      forwards the 8.2 verbs and pulls finished blocks. That is also the
      slowdown fix: the Python per-sample loop cost whole milliseconds per frame
      under wasm. Without the usermod it falls back to the shared Python engine,
      which is a twin of the same libmoy source, so browser == device == host
      stays one audible behaviour either way.

    step_frame_json drains take_pcm() into the frame payload and the page's
    playPCM plays the FINISHED samples (no JS synth), as before."""

    def __init__(self, engine):
        host_api.FakeAudio.__init__(self, engine)
        self.ahead = -1.0           # page-reported queue depth; <0 = no report
        try:
            import moy_audio
            self._ka = moy_audio
        except ImportError:
            self._ka = None
        self._bank_rev = None
        self._buf = bytearray(int(engine.rate * _AUDIO_MAX_STEP) * 2 + 64)
        if self._ka is not None:
            self._ka.set_rate(engine.rate)
            self._push_bank()
            self._ka.volume(engine.master)

    # -- the bank: one crossing per cart, re-pushed when the editor moves it --

    def _push_bank(self):
        import json
        bank = self.engine.bank
        self._ka.bank_load(json.dumps(bank.to_dict()))
        self._bank_rev = bank.rev

    def _sync_bank(self):
        if self._ka is not None and self.engine.bank.rev != self._bank_rev:
            self._push_bank()

    # -- SPEC.md 8.2, forwarded (FakeAudio still records every call) ----------

    def sfx(self, n, chan=None):
        self.calls.append(("sfx", int(n), chan))
        if self._ka is not None:
            self._sync_bank()
            self._ka.sfx(int(n), -1 if chan is None else int(chan))
        else:
            self.engine.play_sfx(n, chan)

    def beep(self, freq, dur=0.15):
        self.calls.append(("beep", freq, dur))
        if self._ka is not None:
            self._ka.beep(float(freq), float(dur))
        else:
            self.engine.play_beep(freq, dur)

    def music(self, track, loop=True):
        self.calls.append(("music", int(track), bool(loop)))
        if self._ka is not None:
            self._sync_bank()
            self._ka.music(int(track), 1 if loop else 0)
        else:
            self.engine.play_music(track, loop)

    def music_stop(self):
        self.calls.append(("music_stop",))
        if self._ka is not None:
            self._ka.music_stop()
        else:
            self.engine.stop_music()

    def sound_stop(self, chan=None):
        self.calls.append(("sound_stop", chan))
        if self._ka is not None:
            self._ka.sound_stop(-1 if chan is None else int(chan))
        else:
            self.engine.stop(chan)

    def volume(self, level):
        self.calls.append(("volume", level))
        self.engine.set_volume(level)      # keep the model in step
        if self._ka is not None:
            self._ka.volume(self.engine.master)

    def is_active(self):
        if self._ka is not None:
            return bool(self._ka.active())
        return self.engine.is_active()

    def tick(self, dt):
        if len(self.calls) > 64:
            del self.calls[:]
        eng = self.engine
        if self.ahead >= 0.0:
            want = _AUDIO_TARGET - self.ahead
            if want > _AUDIO_MAX_STEP:
                want = _AUDIO_MAX_STEP
            n = int(eng.rate * want) if want > 0 else 0
        else:
            n = int(eng.rate * dt) if dt > 0 else 0
        cap = len(self._buf) // 2
        if n > cap:
            n = cap
        if n <= 0 or not self.is_active():
            return
        if self._ka is not None:
            buf = memoryview(self._buf)[:n * 2]
            self._ka.render(buf, n)
            self.last_pcm = bytes(buf)
        else:
            self.last_pcm = eng.render(n)
        self.rendered += n


def _make_audio(engine):
    return _RunnerAudio(engine)


class _PointerSink:
    """Adapts the ConsoleDriver's DEFERRED touch state to the Pointer-shaped
    interface web_view.apply_events expects -- the same adapter the host web
    console (_DriverPointerSink) and the device webview present, so all three
    transports drive the ONE shared event-decode path."""

    def __init__(self, driver):
        self._d = driver

    def place(self, x, y):
        self._d.pointer.place(int(x), int(y))

    @property
    def down(self):
        return self._d._down

    @down.setter
    def down(self, v):
        self._d._down = bool(v)

    @property
    def click(self):
        return self._d._click

    @click.setter
    def click(self, v):
        if v:
            self._d._click = True


def boot(carts_root="/moy/carts", cart=None, width=320, height=240,
         windowed=False, font_scale=0, hud=True):
    """Build the shared Workstation over the recording canvas + the VFS store.
    The page/harness wrote the cart bundle into `carts_root` before calling.

    `hud=False` suppresses the perf HUD -- see the note at the ws.show_fps
    assignment below for why a bundle would want that.

    Two presentation TIERS out of one wasm binary (the page picks per tab):
      windowed=False -- the HANDHELD tier. One recording canvas: the system
        canvas IS the game canvas (the 320x240 degradation path the T-Deck
        runs), so the wasm rasterizes nothing at all.
      windowed=True  -- the DESKTOP tier (#73/#105). A distinct BIG system
        canvas records the desk and its app windows as commands, while a real
        320x240 rasterizer stays behind ws.canvas, because composite_game
        reads that canvas's PIXELS for a cart running inside a desk window.
        Boots onto the desk with project management on (Make tile + editors).
    """
    # Layers record ONLY -- no rasterizing (see RecordingLayer.RECORD_ONLY). This
    # target has no panel, so the tee's raster half painted pixels nothing ever
    # read: ~90% of a windowed picker drag. Set BEFORE the Workstation is built,
    # because _bind captures the mode when each layer is constructed.
    web_view.RecordingLayer.RECORD_ONLY = True
    # GC POLICY (surface model Phase B, the gate-0 finding): the port hardcodes
    # gc_alloc_threshold to 16KB, so EVERY painted frame schedules a FULL
    # collect at the next JS->Python boundary -- measured on the desktop tier:
    # ~67ms against its live set, i.e. the entire 70ms-vs-2.8ms window-drag gap
    # (quiet frames stay under 16KB and skip it, which is why idle was always
    # free). Raise the trigger so collects are RARE, and let the worker land
    # them on IDLE frames via idle_collect() below (SPLIT_HEAP_AUTO grows the
    # heap instead of collecting, so somebody must still collect eventually).
    #
    # 4MB (revised 2026-07-31 after measuring the collect itself). Earlier cuts
    # of this knob guessed in both directions and one regressed play: 24MB is
    # ABOVE the 16MB heap, so the threshold could never fire -- the split heap
    # just grew (16.3MB live after one drag, ~150MB reserved) and every later
    # collect scanned more. The measurement that settles it: an idle_collect
    # costs 5.7ms on the desk, 11ms after a 120-frame drag -- cheap. So collects
    # do not need to be rare, only OFF the gesture path: 4MB (~90 drag frames of
    # headroom at the post-attribution ~45KB/f) keeps memory bounded while the
    # worker's idle collect still lands them on quiet frames.
    import gc
    gc.threshold(4 * 1024 * 1024)
    carts = moy_carts.scan(carts_root)
    if windowed:
        # The system canvas is never smaller than the 320x240 game canvas it
        # composites in as a viewport (host_app.build_workstation clamps the
        # same way, for the same reason).
        width = max(320, int(width))
        height = max(240, int(height))
    rec = web_view.CommandCanvas(width, height, palette=palette.MOY64)
    inp = InputState()
    if not font_scale:
        # 1x on BOTH tiers (owner call): at 1024x600 the 2x system font ate the
        # desktop's density for no legibility win on a real monitor. The page can
        # still pass an explicit scale.
        font_scale = 1
    if windowed:
        # The game canvas RECORDS (web_view.ViewCanvas -> the same recorder as the
        # system canvas) rather than rasterizing: the cart's commands ship and the
        # WM places them with a ["view", ...] bracket (#175). A pure-Python
        # rasterizer here cost ~85 ms/f and ~102 KB/f for one 320x240 frame.
        ws = console.Workstation(
            host_api._NullComp(), web_view.ViewCanvas(rec, 320, 240),
            inp, carts, sys_canvas=rec, font_scale=font_scale)
    else:
        ws = console.Workstation(host_api._NullComp(), rec, inp, carts)
    # Lua carts (#67): the SAME moy_lua native module + moy_lua_glue the boards
    # run, third architecture. The CommandCanvas has no _batch_arr, so
    # LuaCartRun's no-batch fallback registers the Python spr closure -- every
    # sprite reaches the recorder (the deliberate slow lane, still correct).
    # Guarded like the boards: a build without the usermod still boots (a lua
    # cart opens the runtime-missing panel).
    lua_runtime = None
    try:
        import moy_lua  # noqa: F401 -- availability probe only
        from moy_lua_glue import make_lua_runtime
        lua_runtime = make_lua_runtime(ws)
    except ImportError:
        pass
    # The board-agnostic service wiring, in the one canonical order (host + both
    # boards + this runner). Runner-only: can_manage=False (no Make tile / no
    # project management); the FakeWifi keeps any wifi UI harmless.
    console.wire_workstation_core(
        ws, moy_carts, carts_root, host_api.make_api,
        host_api.make_wifi(moy_carts, carts_root),
        make_audio=_make_audio, lua_runtime=lua_runtime, can_manage=True,
        pointer=console.Pointer(rec.w, rec.h), inp=inp)
    # AUTHORING IS ON, BOTH TIERS (owner call): the browser build is the whole
    # console, not the player-only runner #151 originally scoped -- the Make tile
    # and the editors are the point. Nothing persists across a reload (the VFS is
    # in-memory), which the page says out loud.
    # can_manage is wired AFTER the launcher was built, so rebuild the shelf for it
    # to appear.
    ws.launcher.items = ws._launcher_items(ws._all_carts)
    # The Moybyte shell's achievements are gamification for the kid console,
    # not part of a cart player (and doubly not of the brand-neutral spec
    # bundle). The REAL trigger is the Achievements core's note() (e.g.
    # ach.note("open", ...) -> toast "First Steps" -- console.py's own
    # _draw_toast renders it, so stubbing achievements_ui alone was not
    # enough, as the first owner session proved). Kill the core's verbs.
    ws.ach.note = lambda *a, **k: None
    ws.ach.toast = None
    ws._achievement_unlocked = lambda *a, **k: None
    # The Pointer boots visible (a device shows the trackball cursor until its
    # idle timeout) -- in the browser that composited an arrow over the first
    # game frames (owner report: "a cursor flashes at open"). Start hidden; a
    # real trackball-style pan on a system surface re-shows it, a mouse/touch
    # place() keeps it hidden as always.
    ws.pointer.visible = False
    if windowed and ws._sys_canvas is not None:
        # Installed LAST so it anchors its root context -- and its per-window
        # buffers, via root.new_layer -> RecordingLayer -- to the RECORDING
        # canvas, exactly as tools/web_console.py --windowed does over the same
        # CommandCanvas. Two worlds (#105): boot onto the desk.
        from wm_windowed import WindowedWM
        ws.wm = WindowedWM(ws)
        ws.open_desk()
    # HUD (perf_hud.PerfHud): the bottom-right FPS chip draws onto the GAME
    # canvas, so on the handheld tier -- where the system canvas IS the game
    # canvas -- it lands inside the cart's own 320x240 raster and ships in the
    # cart's own command stream. That is host chrome sitting in a frame the cart
    # owns, and it is wrong anywhere the frame IS the product: a spec
    # conformance capture (moy SPEC.md 11 makes this player the golden-frame
    # tiebreaker, and a golden frame must not contain an FPS counter) or a
    # published web export of somebody's game.
    #
    # Found by moy-spec's conformance harness, which measured this player against
    # the suite and got exactly 200 differing pixels on every scene -- a 20x10
    # box at (299, 229), which is the chip.
    #
    # Default stays True, so the device, the dev page and the desktop tier are
    # unchanged. A caller that wants clean frames passes hud=False.
    ws.show_fps = bool(hud)
    if not hud:
        ws.perf_hud = False
    driver = host_api.ConsoleDriver(ws)
    _S["ws"] = ws
    _S["canvas"] = rec
    _S["driver"] = driver
    _S["served"] = web_view.ServedState(rec._rec)
    # Stage 9 per-surface slicing + the #76 delta. The runner has exactly ONE
    # "client" (this page), so one WsClientState mirrors its SURF cache.
    rec._rec.surfaces_on = True
    _S["client"] = web_view.WsClientState()
    _S["sink"] = _PointerSink(driver)
    _S["root"] = carts_root
    _S["exit"] = ws._exit_to_caller     # the real exit (reload_cart uses it)
    if cart:
        open_cart(cart)
    return True


def kiosk(name):
    """Single-cart bundle mode (the spec export): the game IS the page. The
    exit gesture RESTARTS the cart instead of dropping into the console shell
    -- a PICO-8 web export has no shell, and neither should this."""
    ws = _S["ws"]
    real_exit = _S["exit"]

    def _restart():
        real_exit()
        open_cart(name)
    ws._exit_to_caller = _restart


def reload_cart(name=None):
    """Dev hot-reload (the moy CLI's watch loop): the page rewrote changed cart
    files in the VFS; pop any running cart (flushes pmem via release_world),
    re-scan the store, and restart `name` (default: the cart that was running).
    Returns True when a cart restarted."""
    ws = _S["ws"]
    cart = getattr(ws, "cart", None)
    if name is None and cart is not None:
        name = (cart.get("path") or "").rsplit("/", 1)[-1]
    if cart is not None:
        _S["exit"]()          # the REAL exit (kiosk wraps ws._exit_to_caller)
    ws._all_carts = moy_carts.scan(_S["root"])
    ws.launcher.items = ws._launcher_items(ws._all_carts)
    ws.slim_carts()
    ws._dirty = True
    return open_cart(name) if name else True


def open_cart(name):
    """Select + run a cart by folder name ('star_catcher.moy', extension
    optional) or manifest title, skipping the launcher -- the single-cart-embed
    path (?cart=...). Unknown name -> False (the shelf stays up)."""
    ws = _S["ws"]
    want = str(name).lower()
    if want.endswith(".moy"):
        want = want[:-4]
    for i, c in enumerate(ws.launcher.items):
        path = c.get("path") or ""       # synthetic tiles (Make) carry no path
        folder = path.rsplit("/", 1)[-1].lower()
        if folder.endswith(".moy"):
            folder = folder[:-4]
        if want in (folder, c.get("title", "").lower()):
            ws.launcher.sel = i
            ws.open()
            return True
    return False


def _cart_title():
    cart = getattr(_S["ws"], "cart", None)
    return cart.get("title") if cart is not None else None


def _audio_rate():
    if not _AUDIO_RATE[0]:
        from audio import AudioEngine
        _AUDIO_RATE[0] = AudioEngine().rate
    return _AUDIO_RATE[0]


def assets_json():
    """The static render assets (palette + font + the open cart's sheet/
    tilemap/images + shelf covers), mirroring the host web console's assets().
    Also arms the dirty gate so the next step records one full keyframe for the
    (re)loaded page."""
    ws = _S["ws"]
    _S["served"].reset()
    ws._dirty = True
    sheet = getattr(ws, "sheet", None)
    tilemap = getattr(ws, "tilemap", None)
    decoded = {}
    raw = dict(getattr(ws, "images", None) or {})
    # The WALLPAPER cart's images too (its draws ship as commands on this tier,
    # so their imgrefs need the pictures). Open-cart names win on a collision:
    # the running cart is what the kid is looking at.
    _wc = getattr(ws.wallpaper, "cart_images", None)
    if _wc is not None:
        for _n, _b in (_wc() or {}).items():
            raw.setdefault(_n, _b)
    if raw:
        for name in raw:
            dec = host_api._decode_moyimg(raw[name])
            if dec is not None:
                decoded[name] = dec
    pb = getattr(ws, "prebuild_covers", None)
    if pb is not None:
        pb()
    decoded.update(ws.cover_assets())
    kinds = web_view.effective_input_kinds(ws)
    # MEMOISE THE SERIALISED PAYLOAD. Measured: cover_assets() 0ms (already cached),
    # assets_payload() 7ms, json.dumps() 514ms -- serialising 644KB is the whole
    # cost. The page re-requests assets whenever a draw references an image it
    # lacks (imgWant), which is EVERY frame while a cover is still missing, so
    # rebuilding each time froze the console for half a second at a time.
    # `_cover_gen` is the console's own cover-change counter, so a genuinely new
    # cover still rebuilds exactly once.
    sheet_gen = getattr(sheet, "gen", 0)
    tm_gen = getattr(tilemap, "gen", 0)
    key = (getattr(ws, "_cover_gen", 0), _cart_title(), _S["canvas"].w,
           _S["canvas"].h, len(decoded), id(sheet), sheet_gen,
           id(tilemap), tm_gen, tuple(kinds or ()))
    if _ASSETS[0] == key:
        return _ASSETS[1]
    # INCREMENTAL IMAGES. Covers are built LAZILY, so `decoded` grows one entry
    # at a time and the memo key above misses once per new cover -- and each miss
    # re-serialised the WHOLE image set. Measured in the worker: 360-560ms per
    # rebuild (json.dumps over ~644KB is the entire cost; the payload build is
    # ~7ms), which blocked the frame loop hard enough to drop the browser to
    # recv 1-7fps while the page still rendered at 60 (owner console log,
    # 2026-07-31). Only ship images this client does NOT have: the page MERGES a
    # payload tagged "partial" and replaces on a full one.
    #
    # The shipped set survives a CART CHANGE (2026-07-31, second pass). Keying it
    # on cart identity meant launching a game re-serialised every shelf cover as
    # well as the new cart's art -- measured in the worker at 3.3s and 5.2s,
    # which IS the owner's "games take a second or two to load and look frozen".
    # Covers do not change when the cart does, so there is nothing to re-send.
    # `_ASSETS[2]` therefore maps name -> (w, h, len(bytes)): a name re-used by a
    # different cart (two carts both shipping "bg") differs in that stamp and is
    # re-sent, so the page can never keep a stale picture under a live name.
    shipped = _ASSETS[2]
    partial = shipped is not None
    if partial:
        ship = {}
        for name in decoded:
            w_, h_, px_ = decoded[name]
            if shipped.get(name) != (w_, h_, len(px_)):
                ship[name] = decoded[name]
        # NB: no early return when `ship` is empty. "No new images" is not "no
        # change" -- we only get here on a memo MISS, so something in the key
        # moved: the cart title, the palette, the sheet/tilemap, or the input
        # hint. Returning the previous payload shipped those stale, and the input
        # hint is user-visible: on a phone, playing a buttons-only cart, every
        # asset re-fetch answered with the LAUNCHER's payload (input=null), which
        # shows the ⌨ button; the next frame's hint hid it again -- a button
        # blinking every couple of seconds (owner, 2026-07-31). Rebuilding with
        # zero images is cheap: the images ARE the bytes (the 360-560ms this
        # incremental path exists to avoid), the rest is a few KB.
    else:
        ship = decoded
    payload = web_view.assets_payload(
        _S["canvas"].w, _S["canvas"].h,
        # the LIVE table, not the constant: a cart-supplied palette (spec 2.2)
        # swapped in by Player.start must reach the page's index->RGB blit
        getattr(_S["canvas"], "palette", None) or palette.MOY64, sheet, tilemap,
        _cart_title(), _audio_rate(), ship or None, kinds)
    if partial:
        payload["partial"] = 1
    out = json.dumps(payload)
    _ASSETS[0] = key
    _ASSETS[1] = out
    # Stamp every image the page now holds -- the ones just sent PLUS the ones it
    # already had (a partial payload does not un-send them).
    stamped = dict(shipped or {})
    for name in decoded:
        w_, h_, px_ = decoded[name]
        stamped[name] = (w_, h_, len(px_))
    _ASSETS[2] = stamped
    return out


def step_frame_json(dt, audio_ahead=-1.0):
    """Advance the console one frame; return the frame_payload JSON, or ""
    when the console skipped the redraw (#44 dirty gate: static screen) -- the
    page then just retains its last frame. `audio_ahead` is the page's
    scheduled-ahead audio depth in seconds (-1 = not reported): _RunnerAudio
    tops the cushion back up to target each frame (the crackle fix, #170).
"""
    canvas = _S["canvas"]
    canvas.take_commands()               # drop anything stale (defensive)
    au = getattr(_S["ws"], "audio", None)
    if au is not None and hasattr(au, "ahead"):
        au.ahead = float(audio_ahead)
    _S["driver"].frame(dt)
    flat = canvas.take_commands()
    cart = _cart_title()
    # Drain the engine's FINISHED PCM (rendered by _RunnerAudio.tick during the
    # frame) -- the page plays it via playPCM, like the host web console.
    au = getattr(_S["ws"], "audio", None)
    pcm = au.take_pcm() if (au is not None and hasattr(au, "take_pcm")) else b""
    audio_b64 = ""
    if pcm and any(pcm):
        import binascii
        audio_b64 = binascii.b2a_base64(pcm).decode().strip()
    if not flat:
        if not audio_b64:
            return ""
        # Redraw skipped but sound still playing: ship an empty-cmds frame so
        # the audio tail is never dropped on a static screen.
        return json.dumps(web_view.frame_payload(
            [], cart, canvas._rec.atlas_gen, audio=audio_b64,
            input_kinds=web_view.effective_input_kinds(_S["ws"])))
    gen = canvas._rec.atlas_gen
    kinds = web_view.effective_input_kinds(_S["ws"])
    # PER-WM-SURFACE + DELTA (Stage 9 / #76), the same path the host web console
    # serves: slice the frame into one stream per WM surface and ship UNCHANGED
    # ones as ~30-byte {"same":1} stubs the page replays from its SURF cache.
    # This runner shipped flat frames -- every surface, every frame -- so a desk
    # whose wallpaper and window chrome had not moved still re-encoded them.
    # Two wins from one change: fewer bytes (what #76 needs for the device's
    # ~72KB/s wire) and less garbage per frame, which is what actually drives the
    # wasm's GC cost -- collections here are threshold-driven, so allocating less
    # means sweeping less often. Bounded, unlike raising the threshold.
    surfaces = canvas.take_surfaces()
    if surfaces is not None:
        _, surfs = _S["served"].served_surfaces(flat, surfaces)
        delta = _S["client"].delta
        enc = delta.encode(surfs, gen=gen)
        if delta.need_keyframe:
            # A skip stub had nothing client-side to replay (fresh cache, an
            # atlas wipe): force the next frame to draw EVERY surface in full
            # (§5.4 keyframe production) and keep the redraw gate open for it.
            delta.need_keyframe = False
            _S["ws"].mark_dirty()
            _akf = getattr(_S["ws"].wm, "arm_surface_keyframe", None)
            if _akf is not None:
                _akf()
        # Surface metadata (§6, protocol v2): placement + gens per entry, from
        # the WM's surface registry. Additive -- a page that ignores them
        # composites exactly as before (streams are still root-space).
        ss = getattr(_S["ws"].wm, "surfaces", None)
        if ss is not None:
            for e in enc:
                s = ss.surfaces.get(e.get("id"))
                if s is not None:
                    e["place"] = s.place()
                    e["gen"] = ss.content_gen(s.sid)
                    e["pgen"] = s.place_gen
        return json.dumps(web_view.frame_payload(
            [], cart, gen, audio=audio_b64, surfaces=enc,
            input_kinds=kinds))
    return json.dumps(web_view.frame_payload(
        _S["served"].served_frame(flat), cart, gen, audio=audio_b64,
        input_kinds=kinds))


def _apply(events):
    d = _S["driver"]
    rest = []
    for e in events:
        if e.get("type") == "reset":
            # Esc during play: restart the cart (kiosk wraps the exit into a
            # relaunch; the multi-cart runner pops to the shelf).
            if getattr(_S["ws"], "cart", None) is not None:
                _S["ws"]._exit_to_caller()
        else:
            rest.append(e)
    web_view.apply_events(
        rest, d.input, _S["sink"],
        on_press=d.press, on_pan=d.pan, on_key=d.type_char,
        on_esc=d.escape, on_hold=d.hold, on_key_hold=d.key_hold)


def request_keyframe():
    """Re-seed this client from scratch: forget what it was told it has, and
    draw the next frame in full (§5.4 keyframe production).

    Called when the PAGE reports it dropped a frame. The #76 delta ships
    {"same":1} for a surface the client already holds, which assumes every frame
    reaches the page IN ORDER -- and the runner's transport does not guarantee
    that: page_tail's rAF loop keeps only the newest frame, so a main thread that
    misses a beat silently discards one. Lose the frame that carried a surface in
    full and the page replays its stale cache for that id forever after, because
    every later frame says "same" (owner report 2026-07-31: PLAY appeared to do
    nothing, then a drag brought the Library up with the desktop still showing
    around it -- the wallpaper surface was the one that never landed).

    Cheap and idempotent: one full frame, then delta-encoding resumes."""
    client = _S.get("client")
    if client is None:
        return ""
    client.delta.reset()             # forget every cached stream (need_keyframe)
    _S["ws"].mark_dirty()            # ... and keep the redraw gate open for it
    _akf = getattr(_S["ws"].wm, "arm_surface_keyframe", None)
    if _akf is not None:
        _akf()                       # ... drawn in full, no skip-draw
    return ""


def idle_collect():
    """Run the deferred split-heap collect NOW -- called by the worker on a
    quiet streak (and as a bounded periodic guard), so the full mark+sweep
    lands on an idle frame instead of mid-gesture. Under GC_SPLIT_HEAP_AUTO
    gc.collect() only sets the pending flag; the real collect executes when
    THIS call returns to JS (the top level) -- exactly the boundary we chose."""
    import gc
    gc.collect()
    return ""


def apply_events_json(text):
    """Feed a browser {"events":[...]} JSON text batch through the ONE shared
    decode path (the same wire format the WS transports speak)."""
    web_view.apply_ws_text(text, _apply)
