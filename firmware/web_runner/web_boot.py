"""Moybyte web runner boot (#151): the SHARED console under MicroPython-WASM,
browser-as-GPU.

This is the third console backend, and the thinnest one: the system canvas IS a
`web_view.CommandCanvas` (the recording canvas the host web console swaps in),
so the console never rasterizes a pixel -- each frame's draw-command list is
handed straight to the page's JS replayer by a local call (no WebSocket, no
72KB/s ceiling; the #59 "Zero / browser is the GPU" thesis realized as a WASM
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


class _RunnerAudio(host_api.FakeAudio):
    """FakeAudio with the calls list capped: it grows per sfx for host test
    assertions, which a long browser session doesn't want. The per-frame PCM
    render is inherited -- step_frame_json drains take_pcm() into the frame
    payload and the page's playPCM plays the FINISHED samples (no JS synth),
    the same seam the host web console streams."""

    def tick(self, dt):
        if len(self.calls) > 64:
            del self.calls[:]
        host_api.FakeAudio.tick(self, dt)


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
    driver = host_api.ConsoleDriver(ws)
    _S["ws"] = ws
    _S["canvas"] = canvas
    _S["driver"] = driver
    _S["served"] = web_view.ServedState(canvas._rec)
    _S["sink"] = _PointerSink(driver)
    if cart:
        open_cart(cart)
    return True


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
        _S["canvas"].w, _S["canvas"].h, palette.MOY64, sheet, tilemap,
        _cart_title(), _audio_rate(), decoded or None, kinds))


def step_frame_json(dt):
    """Advance the console one frame; return the frame_payload JSON, or ""
    when the console skipped the redraw (#44 dirty gate: static screen) -- the
    page then just retains its last frame."""
    canvas = _S["canvas"]
    canvas.take_commands()               # drop anything stale (defensive)
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
    web_view.apply_events(
        events, d.input, _S["sink"],
        on_press=d.press, on_pan=d.pan, on_key=d.type_char,
        on_esc=d.escape, on_hold=d.hold)


def apply_events_json(text):
    """Feed a browser {"events":[...]} JSON text batch through the ONE shared
    decode path (the same wire format the WS transports speak)."""
    web_view.apply_ws_text(text, _apply)
