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

Runner-only scope (#151): read-only store (`can_manage=False` -> no Make tile,
no editors reachable), pmem/journal writes land in the browser's MEMFS
(ephemeral -- gone on reload).

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
    * The NATIVE moy_audio kernel does the per-sample mix when the wasm was
      built with the usermod (the slowdown fix: the Python sample loop costs
      whole milliseconds per frame under wasm). Same voice_set / render /
      voice_read per-block pattern as the device's legacy feed -- Python keeps
      the model, the scheduler and all triggering; C only mixes the block, so
      host == device == browser stays one audible behaviour.

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
        self._buf = bytearray(int(engine.rate * _AUDIO_MAX_STEP) * 2 + 64)

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
        if n <= 0 or not eng.is_active():
            return
        if self._ka is not None:
            self.last_pcm = self._render_native(n)
        else:
            self.last_pcm = eng.render(n)
        self.rendered += n

    def _render_native(self, n):
        """device_audio._render_native's pattern: music scheduler in Python,
        the per-sample mix in C, advanced voice state read back so the Python
        engine stays the single source of truth."""
        eng = self.engine
        ka = self._ka
        eng._advance_music(n / float(eng.rate))
        voices = eng.voices
        for c in range(len(voices)):
            v = voices[c]
            ka.voice_set(c, v.active, v.steps, v.step_dur, v.loop,
                         v.idx, v.t, v.phase, v.noise,
                         v.phase2, v.prev_pitch, v.prev_vol, v.loop_start)
        buf = memoryview(self._buf)[:n * 2]
        ka.render(buf, n, eng.rate, eng.volume)
        for c in range(len(voices)):
            st = ka.voice_read(c)
            if st is not None:
                v = voices[c]
                v.active = st[0]
                v.idx = st[1]
                v.t = st[2]
                v.phase = st[3]
                v.noise = st[4]
                if len(st) > 7:
                    v.phase2 = st[5]
                    v.prev_pitch = st[6]
                    v.prev_vol = st[7]
        return bytes(buf)


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


def boot(carts_root="/moy/carts", cart=None, width=320, height=240):
    """Build the shared Workstation over the recording canvas + the VFS store.
    The page/harness wrote the cart bundle into `carts_root` before calling."""
    carts = moy_carts.scan(carts_root)
    canvas = web_view.CommandCanvas(width, height, palette=palette.MOY64)
    inp = InputState()
    ws = console.Workstation(host_api._NullComp(), canvas, inp, carts)
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
        make_audio=_make_audio, lua_runtime=lua_runtime, can_manage=False,
        pointer=console.Pointer(canvas.w, canvas.h), inp=inp)
    # can_manage was wired False AFTER the launcher was built, so rebuild the
    # shelf without the Make tile (the Editor entry point is out of the
    # runner's scope -- #151).
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
    driver = host_api.ConsoleDriver(ws)
    _S["ws"] = ws
    _S["canvas"] = canvas
    _S["driver"] = driver
    _S["served"] = web_view.ServedState(canvas._rec)
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
    raw = getattr(ws, "images", None)
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
    return json.dumps(web_view.assets_payload(
        _S["canvas"].w, _S["canvas"].h,
        # the LIVE table, not the constant: a cart-supplied palette (spec 2.2)
        # swapped in by Player.start must reach the page's index->RGB blit
        getattr(_S["canvas"], "palette", None) or palette.MOY64, sheet, tilemap,
        _cart_title(), _audio_rate(), decoded or None, kinds))


def step_frame_json(dt, audio_ahead=-1.0):
    """Advance the console one frame; return the frame_payload JSON, or ""
    when the console skipped the redraw (#44 dirty gate: static screen) -- the
    page then just retains its last frame. `audio_ahead` is the page's
    scheduled-ahead audio depth in seconds (-1 = not reported): _RunnerAudio
    tops the cushion back up to target each frame (the crackle fix, #170)."""
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
    cmds = _S["served"].served_frame(flat)
    return json.dumps(web_view.frame_payload(
        cmds, cart, canvas._rec.atlas_gen, audio=audio_b64,
        input_kinds=web_view.effective_input_kinds(_S["ws"])))


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


def apply_events_json(text):
    """Feed a browser {"events":[...]} JSON text batch through the ONE shared
    decode path (the same wire format the WS transports speak)."""
    web_view.apply_ws_text(text, _apply)
