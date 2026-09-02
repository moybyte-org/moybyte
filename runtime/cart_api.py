"""THE cart-API namespace builder (make_api) -- one body for every tier.

Until 2026-08-17 this function existed twice: `runtime/host_api.py` (host sim +
the web runner) and `device/device_api.py` (both boards), ~80% line-identical
and drifting exactly as twins do. The measured deltas when they were merged,
each resolved here on its merits:

  * the device's multi-tile sprite CACHE (tile_cache keyed on the sheet's gen
    counter, #63) -- the host rebuilt the span Image every call, a per-frame
    allocation for zero benefit. The cached form is now everyone's.
  * the device's NATIVE spr gate probe (#63 make_spr_gate) -- the probe is
    self-guarding (a canvas without a native gfx returns None), so every tier
    asks and takes what its canvas can give. Pixels are identical either way,
    pinned by the parity suites.
  * the host's `_Layer` verb list carried `tline`; the device's did NOT -- so
    `layer.tline(...)` worked on the host and raised AttributeError on a
    board. The superset is now everyone's.
  * make_layer: the device passed `owner=` (the #63 layer-loan leak fix) and
    tables/texts through to the layer's namespace; the host passed neither.
    Both now do both.
  * time() reached the tick helpers through three different lanes; it rides
    `runtime/ticks.py` now, like everything else.

`host_api` and `device_api` still exist -- as the HOMES of what genuinely
differs per side (host: FakeAudio/FakeWifi/ConsoleDriver; device: nothing but
the re-export) -- and both re-export this make_api, so every import site and
frozen name is unchanged. tests/test_cart_api_unified.py pins the identity:
one function OBJECT, not three agreeing copies.
"""

import json
import random

try:                                    # staged/frozen flat namespace (boards, web)
    from moy_image import Image
except ImportError:                     # host: the runtime package
    from runtime.moy_image import Image
try:
    from ticks import _ticks_ms, _ticks_diff
except ImportError:
    from runtime.ticks import _ticks_ms, _ticks_diff


def _rotate_indices(*args):
    """widgets.rotate_indices (#85/#93 all-around sprite rotation), bound
    lazily for the same leaf-module reason as color() above -- only the scene
    rotation path needs it, and widgets is not leaf-weight."""
    try:
        from widgets import rotate_indices
    except ImportError:
        from runtime.widgets import rotate_indices
    return rotate_indices(*args)

# The logical buttons a CART may read (moy SPEC.md 7.3): four directions, a/b,
# and the optional `run`. The InputState button sets are deliberately wider --
# the host carries "home" and the device adds x/y/select/start/save/... -- but
# those belong to the console, not to carts. "home" especially: 7.3 gives exit
# to the HOST ("no cart is required to provide one... the cart never sees it"),
# so a cart that polled btn("home") could watch the player reaching for the
# exit. Anything outside this set reads as not-pressed on every tier, which is
# also what a conforming console with different hardware would report.
CART_BUTTONS = ("left", "right", "up", "down", "a", "b", "run")

_color = None


def color(name_or_index):
    """Resolve a color name or MOY64 index -- `chrome.color`, bound LAZILY.

    chrome is the device-safe home of the name map (its twin, palette.color,
    needs CPython colorsys at import time), but importing chrome pulls the
    code-editor chain in behind it -- and this module is a LEAF the device
    imports at boot. By the time a cart calls col()/background() the console
    has long imported chrome, so the lazy bind costs one getattr ever."""
    global _color
    if _color is None:
        try:
            from chrome import color as _c
        except ImportError:
            from runtime.chrome import color as _c
        _color = _c
    return _color(name_or_index)


def _decode_moyimg(text):
    """Decode a .moyimg paint-image asset (#63 Fold 3) into (w, h, index_bytes),
    or None on any error (a bad image just doesn't draw). The blob is a JSON
    header {w, h, data} where `data` is base64 of the zlib-compressed MOY64
    index bitmap (1 byte/pixel). The shared moy_carts.decode_moyimg handles the
    current codec on every target; the legacy envelope inflates through zlib
    where CPython provides it and MicroPython's `deflate` where it doesn't."""
    try:
        try:
            import moy_carts
        except ImportError:
            from runtime import moy_carts
        shared = moy_carts.decode_moyimg(text)
        if shared is not None:
            return shared
        import binascii
        meta = json.loads(text)
        w = int(meta["w"])
        h = int(meta["h"])
        raw = binascii.a2b_base64(meta["data"])
        try:
            import zlib
            idx = zlib.decompress(raw)
        except ImportError:
            # MicroPython target: `deflate` is its zlib. Without this the
            # legacy zlib envelope (sakura's bg) silently decoded to None,
            # so the Lua/paint background never drew in the browser.
            import deflate
            import io
            idx = deflate.DeflateIO(io.BytesIO(raw), deflate.ZLIB).read()
        return (w, h, idx)
    except Exception:  # noqa: BLE001 -- bad/absent image -> caller gets None
        return None


class _Layer:
    """A scroll background (#54): a wider off-screen canvas the cart pre-renders
    a level into ONCE, then window-copies to the screen per frame via
    draw_layer. Exposes the draw verbs (sheet/tilemap-aware, pixel-identical to
    the main api) bound to its OWN canvas, plus W/H. Built by make_layer(w, h).

    The verb list is the union the two copies had drifted apart on: `tline`
    was on the host's and missing from the device's, so a layer's textured
    line worked on one tier and raised on the other."""

    _VERBS = ("cls", "pix", "line", "rect", "rectb", "circ", "circb",
              "tri", "trib", "sspr", "tline",
              "spr", "map", "mget", "mset", "print",
              "camera", "clip", "pal", "palt")

    def __init__(self, canvas, ns):
        self._canvas = canvas
        self.W = canvas.w
        self.H = canvas.h
        for k in _Layer._VERBS:
            setattr(self, k, ns[k])


def make_api(canvas, input, config, sheet=None, audio=None, tilemap=None,
             pmem=None, wifi=None, images=None, scenes=None, tables=None,
             texts=None, net=None, gpio=None, flags=None, owner="cart"):
    """The cartridge global namespace: the frozen TIC-80-style kid API
    (cls/pix/rect/circ/spr/map/print/btn/touch/... -- docs/moy_cart_api.md)
    bound to a canvas + InputState + the injected audio/wifi backends.
    Backend-shape-agnostic: everything tier-specific rides on the `canvas` and
    `input` objects, so the SAME function serves the host sim, the web runner,
    the T-Deck (320x240 panel canvas) and the P4 (offscreen game canvas under
    the windowed WM).

    `wifi` is the capability-gated network backend (#38): the Workstation
    passes it ONLY for a cart whose manifest permissions include "network", and
    the `wifi` name enters the namespace iff it is non-None -- a normal kid
    cart gets no network access at all (the base key-set is identical either
    way). `net` is the same gate for "multiplayer" (#65). `gpio` is the third
    such gate (#9): physical pins, which only exist where a host with pins is
    on the other end of the page -- so far the Zero. `owner` tags layer
    loans for the device's #63 leak-fix reclaim; a gc-heap canvas ignores it.
    """
    _img_cache = {}        # name -> decoded paint Image (see image() below), so a
                           # repeated image(name) returns the SAME Image (#63) and its
                           # RGB565 bake cache survives across frames.
    tile_cache = {}        # (tile id, colorkey, w, h) -> Image, so a redrawn span
                           # sprite reuses one Image (and its blit cache) every frame.
                           # Invalidated when the sheet's gen counter changes (a paint
                           # edit), so a live sprite edit shows fresh art.
    _cache_gen = [None]
    # SPEC.md 3.5's tile flags, always a table so fget/fset/map(..., layers) are
    # callable for every cart. `flags` is the project's live 512-byte one; a run
    # without a project (a bare namespace, a test) gets a private zero table,
    # which reads exactly as the spec's "an absent file is all zero".
    tile_flags = flags if flags is not None else bytearray(512)

    def cfg(key, default=None):
        return config.get(key, default)

    # Audio (#16): bound to the injected backend (FakeAudio on the host,
    # DeviceAudio on a board); no-op if absent so a cart's sfx()/beep()/music()
    # never crash when audio isn't wired.
    def _sfx(n, chan=None):
        if audio is not None:
            audio.sfx(n, chan)

    def _beep(freq, dur=0.15):
        if audio is not None:
            audio.beep(freq, dur)

    def _music(track, loop=True):
        if audio is not None:
            audio.music(track, loop)

    def _music_stop():
        if audio is not None:
            audio.music_stop()

    def _sound_stop(chan=None):
        if audio is not None:
            audio.sound_stop(chan)

    def _volume(level):
        if audio is not None:
            audio.volume(level)

    def spr(n, x, y, colorkey=-1, scale=1, flip=0, w=1, h=1):
        # TIC-80 spr(id, x, y[, colorkey, scale, flip, w, h]) from the cart's sheet.
        # w/h are the tile span: spr(n, x, y, w=2, h=2) draws the 16x16 multi-tile
        # sprite whose top-left is tile n (#30). flip (0=none, 1=h, 2=v, 3=both, #11)
        # mirrors the sprite. w=h=1, flip=0 is the plain 8x8 sprite. Also accepts an
        # Image directly (ASCII-art sprites); then a 4th positional is treated as
        # scale, e.g. spr(pet, x, y, scale=4).
        if isinstance(n, Image):
            return canvas.spr(n, x, y, colorkey if colorkey != -1 else scale, flip)
        if sheet is None:
            return
        if w > 1 or h > 1:
            # Multi-tile sprite: resolve the span Image and draw it immediately (the
            # canvas flushes any pending 1x1 auto-batch first). Cached per (n,ck,w,h).
            g = getattr(sheet, "gen", 0)
            if g != _cache_gen[0]:
                tile_cache.clear()
                _cache_gen[0] = g
            ck = (int(n), colorkey, int(w), int(h))
            img = tile_cache.get(ck)
            if img is None:
                img = sheet.tile_span_image(int(n), int(w), int(h), colorkey)
                if img is None:
                    return
                tile_cache[ck] = img
            canvas.spr(img, x, y, scale, flip)
            return
        # Plain 1x1 sheet tile: auto-batch (#63). The canvas queues it and coalesces a
        # contiguous run into one native blit_batch, flushing on any state break.
        canvas.spr_tile(sheet, int(n), x, y, colorkey, scale, flip)

    def map_(mx=0, my=0, w=None, h=None, sx=0, sy=0, colorkey=-1, scale=1,
             layers=0):
        # TIC-80 map(mx, my, w, h, sx, sy, colorkey, scale): blit a w x h region of
        # the cart's tilemap (top-left cell mx,my) to screen (sx,sy). Tiles are the
        # 8x8 sheet sprites; `scale` enlarges each (so scale=2 => 16px world tiles).
        # `layers` (SPEC.md 7.2) is a FLAG MASK: non-zero, a cell draws only when
        # its tile's flag byte shares a bit with it -- the ground with mask 1, the
        # foreground with mask 2 after the sprites, from one map and one call each.
        # It filters on the TILE's flags, so tagging a tile once tags every cell
        # that uses it, and a cart with no flags at all draws nothing under a
        # non-zero mask.
        if tilemap is None or sheet is None:
            return
        canvas.map(tilemap, sheet, mx, my, w, h, sx, sy, colorkey, scale,
                   layers, tile_flags)

    # spr_batch / spans / rect_batch were cart verbs here until 2026-08-14. They are
    # DELETED, not moved: the Bench twins measured the draw paths of the two languages
    # indistinguishable at 300 calls/frame on the S3 (plan 6.10), so the batch verbs
    # bought <=1ms at realistic call counts while costing every kid two vocabularies
    # -- Lua could not call them at all (a trampoline cannot marshal a list or a span
    # buffer), which is why every Lua twin already drew the same frame with a plain
    # loop. A `for ...: spr(...)` run still leaves as ONE native blit_batch: the
    # canvas's auto-batch gate (#63, spr_tile -> the batch flush) coalesces it.

    def sspr(sx, sy, sw, sh, dx, dy, dw=None, dh=None, colorkey=-1, flip=0):
        # sspr(sx, sy, sw, sh, dx, dy[, dw, dh, colorkey, flip]): stretch a sw x sh
        # PIXEL region of the sheet into a dw x dh destination rect (#167). Unlike
        # spr()'s integer `scale` this is an arbitrary stretch -- the textured
        # wall-slice verb for software 3D, and non-integer sprite scaling. Source
        # coords are sheet PIXELS, not tile ids.
        if sheet is None:
            return
        canvas.sspr(sheet, sx, sy, sw, sh, dx, dy, dw, dh, colorkey, flip)

    def tline(x0, y0, x1, y1, u, v, du, dv, colorkey=-1):
        # tline(x0, y0, x1, y1, u, v, du, dv[, colorkey]): SPEC.md 6.1's textured
        # line -- exactly line()'s pixels, sampling the MAP as a virtual texture
        # in 16.16 FIXED POINT (u/v/du/dv are ints: float * 65536 is the cart's
        # job). One call per scanline is a Mode 7 floor; one per column textures
        # a raycaster. Wraps modulo the map's pixel size; empty cells draw
        # nothing.
        if sheet is None or tilemap is None:
            return
        canvas.tline(tilemap, sheet, x0, y0, x1, y1, u, v, du, dv, colorkey)

    def mget(x, y):
        return tilemap.mget(x, y) if tilemap is not None else -1

    def mset(x, y, tile):
        if tilemap is not None:
            tilemap.mset(x, y, tile)

    # SPEC.md 3.5/7.1: one flag byte per tile, 512 of them. The table is the
    # PROJECT's (moy_carts loads flags.moyflags into it) and it is shared with
    # every layer namespace below, so `fset` from inside a layer's pre-render is
    # the same write the screen's next map(..., layers) reads. A run with no
    # project gets a private zero table rather than a None to guard at each verb.
    def fget(n, b=None):
        n = int(n)
        v = tile_flags[n] if 0 <= n < len(tile_flags) else 0
        if b is None:
            return v
        return bool((v >> (int(b) & 7)) & 1)

    def fset(n, b, on=None):
        # fset(n, byte) writes the whole byte; fset(n, bit, on) sets or clears
        # one bit of it. Off the sheet is a DROPPED write, not an error -- the
        # same truthful degrade fget's 0 is.
        n = int(n)
        if not (0 <= n < len(tile_flags)):
            return
        if on is None:
            tile_flags[n] = int(b) & 0xFF
            return
        bit = 1 << (int(b) & 7)
        tile_flags[n] = (tile_flags[n] | bit) if on else (tile_flags[n] & ~bit & 0xFF)

    def touch():
        # Pointer (touch glass on a board, mouse on the host) exposed to
        # touch-driven carts: (x, y, tapped, held) this frame, or None when there
        # is no pointer. `tapped` is the press edge so a cart scores at most one
        # hit per tap; `held` stays True while the finger/button is down, so a
        # cart can track a DRAG (drawing, sliders). Two-domain seam (#39): prefer
        # the game-space pointer publication when the console provides one (a
        # distinct big system canvas), so a cart reads 320x240 viewport coords.
        # A LINKED MATCH HAS NO POINTER. Only buttons cross the radio, so a
        # touch read here would move this screen's player and not the other
        # one's -- a divergence the lockstep exchange cannot see and cannot
        # heal, which is the same class of bug as drawing from the shared random
        # stream. Reporting "no pointer" makes a touch-driven cart fall back to
        # its button path, which is the honest answer while two consoles share
        # one game.
        if getattr(input, "netplay_live", False):
            return None
        gp = getattr(input, "game_pointer", None)
        if gp is not None:
            held = bool(gp[3]) if len(gp) > 3 else False
            return (gp[0], gp[1], bool(gp[2]), held)
        p = getattr(input, "pointer", None)
        if p is None:
            return None
        return (p.x, p.y, bool(p.click), bool(getattr(p, "down", False)))

    def mouse():
        # TIC-80-shaped 7-tuple (x, y, left, middle, right, scrollx, scrolly)
        # aliasing touch(): tap -> left button. Neither the touchscreen nor the
        # host pointer has middle/right/scroll, so those are constant 0/False.
        gp = getattr(input, "game_pointer", None)
        if gp is not None:
            return (gp[0], gp[1], bool(gp[2]), False, False, 0, 0)
        p = getattr(input, "pointer", None)
        if p is None:
            return (0, 0, False, False, False, 0, 0)
        return (p.x, p.y, bool(p.click), False, False, 0, 0)

    def time():
        # Milliseconds since the cart started (set by Workstation._start).
        start = getattr(input, "cart_start_ms", 0)
        return _ticks_diff(_ticks_ms(), start)

    def key(code=None):
        # key([code]) -> is that ASCII key held this frame (key(ord("a"))). The
        # T-Deck keyboard reports one byte per frame, so key() tracks that single
        # last key, not a full held-set: only one key reads as down at a time. With
        # no arg, returns the last key code (0 when nothing is down).
        cur = getattr(input, "cart_key", 0)
        if code is None:
            return cur
        return cur == int(code)

    def keyp(code=None):
        # keyp([code]) -> pressed THIS frame (the 0->key edge). Same single-key
        # limitation as key(); no auto-repeat hold/period args.
        edge = getattr(input, "cart_keyp", 0)
        if code is None:
            return edge
        return edge == int(code)

    def textmode(on=True):
        # textmode([on]) -> opt a RUNNING cart into TEXT-keyboard input (#38/#42).
        # By default a running cart is in GAME mode: a held WASD/arrow keeps driving
        # btn() (true hold-to-move) but the keyboard yields no clean typeable ASCII.
        # textmode(True) switches to text mode so key()/keyp() return clean 1-byte
        # ASCII for typing (a password, a name); textmode(False) restores game mode.
        # The Workstation applies it: on the host it gates char routing to the
        # cart's key(); on the device it flips the T-Deck keyboard ASCII<->raw.
        # Resets to game mode automatically when the cart exits.
        input.text_mode = bool(on)

    def view(w=0, h=0):
        # view(w, h) -> declare the cart's LOGICAL viewport: the composite scales
        # this centered w x h region of the 320x240 canvas to the glass at the
        # biggest integer scale that fits (celeste's 128x128 -> 4x on the P4).
        # view() / view(0, 0) restores the full canvas. ADDITIVE like
        # textmode/quit; rides InputState, cleared by Player.start each run.
        input.game_view = (int(w), int(h)) if w and h else None

    def _quit():
        # quit() -> END this cart and return to whoever launched it (the launcher, or
        # the Editor). A cart calls it from a key or an on-screen affordance it draws.
        # This is how a TEXT-mode cart exits: once it calls textmode(True), the console's
        # hold-BACKSPACE game-exit can't reach it (BACKSPACE is a typed 0x08 the cart
        # reads as delete, and the T-Deck keyboard has no autorepeat, so the ~700ms hold
        # never accumulates) -- so a textmode(True) cart MUST provide its own exit via
        # quit(). ADDITIVE to the frozen kid API, works for ANY cart type. Sets a flag
        # the Player honors AFTER this frame's _update runs (player.tick), popping to
        # the run caller via ws._exit_to_caller(). `quit` shadows the site builtin
        # inside the cart's exec namespace, resolving to this closure.
        input.cart_quit = True

    def pmem_fn(index, value=None):
        # TIC-80 pmem(i[, v]): read pmem(i) -> int, write pmem(i, v) -> persists.
        if pmem is None:
            return 0
        return pmem.cell(index, value)

    def make_layer(w, h):
        # make_layer(w, h) -> a scroll background (#54): a wider off-screen canvas the
        # cart pre-renders a level into ONCE (with the SAME verbs -- cls/map/spr/rect/
        # circ/print/...), then window-copies to the screen each frame via draw_layer.
        # Replaces a per-frame full-background re-render (map() over a scrolling level,
        # ~12-14ms) with a flat memory copy (~7ms) -- the lever for ~60fps scrollers.
        lc = canvas.new_layer(w, h, owner=owner)   # #63: lent to this program (leak fix)
        lns = make_api(lc, input, config, sheet, audio, tilemap, pmem, wifi, images,
                       tables=tables, texts=texts, flags=tile_flags, owner=owner)
        return _Layer(lc, lns)

    def draw_layer(layer, cam_x=0, cam_y=0):
        # draw_layer(layer, cam_x, cam_y): blit the visible W x H window of `layer` at
        # the camera offset into the framebuffer (this frame's background; draw actors
        # on top afterwards). The camera is clamped to [0, layer - screen] so the full
        # window always lands -- no torn edge at the world boundary.
        lc = layer._canvas
        cx = int(cam_x)
        cy = int(cam_y)
        maxx = lc.w - canvas.w
        maxy = lc.h - canvas.h
        if cx < 0:
            cx = 0
        elif maxx > 0 and cx > maxx:
            cx = maxx
        if cy < 0:
            cy = 0
        elif maxy > 0 and cy > maxy:
            cy = maxy
        canvas.blit_window_from(lc, cx, cy)

    def image(a, mapping=None, transparent="."):
        # Two forms, dispatched on the first arg (str vs ASCII rows):
        #   image("bg")          -> the cart's paint-image asset images/bg.moyimg as a
        #     big Image (a 64-colour MOY64 index bitmap), placed with spr(img, x, y).
        #     The #63 Fold 3 background path. None when the cart has no such image;
        #     the SAME Image is returned across calls (memoised) so its bake cache
        #     survives frames.
        #   image(rows, mapping) -> build a small Image from ASCII art (kid convenience).
        if isinstance(a, str):
            im = _img_cache.get(a)
            if im is None:
                blob = images.get(a) if images else None
                if blob is None:
                    return None
                dec = _decode_moyimg(blob)
                if dec is None:
                    return None
                w, h, idx = dec
                im = Image(w, h, idx, -1)      # opaque (no transparent index)
                im._paint = True               # marks the paint-image bake/ship fast paths
                im._name = a                   # spr() can ship ["imgref", x, y, name]
                _img_cache[a] = im
            return im
        return Image.from_ascii(a, mapping, transparent)

    def table(name):
        # Desk Lab interop (#78): a Sheets sheet placed in the cart's folder
        # (tables/<name>.moysheet) read as ROWS -- a list of lists of computed
        # values. Missing name -> [] (image()'s degrade-don't-throw contract).
        # The rows were decoded once at cart-load (moy_carts.decode_table).
        rows = tables.get(name) if tables else None
        return rows if rows is not None else []

    def text(name):
        # Desk Lab interop (#78): a Writer doc in the cart's folder
        # (docs/<name>.moytext) read as LINES. Missing name -> [].
        lines = texts.get(name) if texts else None
        return lines if lines is not None else []

    # #63: hand the kid the NATIVE spr fast path when the canvas has one. The C
    # gate parses (n, x, y[, colorkey[, scale[, flip]]]) and appends to the
    # canvas batch array with no Python call frame -- the fix for the warm-heap
    # frame-spill pathology that made a 120-sprite kid loop cost ~150ms/frame.
    # The Python closure above stays as its fallback (Image sprites, w/h spans,
    # kwargs) and as the whole path on a canvas without the gate, so pixels and
    # semantics are identical either way. Kid code never changes: it's spr().
    _spr_entry = spr
    _mkgate = getattr(canvas, "make_spr_gate", None)
    if _mkgate is not None:
        _gate = _mkgate(sheet, spr)
        if _gate is not None and callable(_gate):
            _spr_entry = _gate

    # Declared background (#63 fast-by-default -- the "software PPU layer 0"):
    # the cart names its backdrop ONCE (a color or a painted Image); the engine
    # restores it at the START of every frame via the Player's
    # ns["_moy_restore_bg"] hook. An Image bakes into a hidden full-screen layer
    # once; the per-frame restore is one draw_layer window copy -- the
    # full-screen shape the async GDMA restore predicts, so on-device the
    # backdrop costs ~0 visible ms.
    _bg = [None]

    def background(x=None):
        if x is None:
            _bg[0] = None
        elif isinstance(x, Image):
            lay = make_layer(canvas.w, canvas.h)
            lay.spr(x, 0, 0)               # bake once (paint images take the fast path)
            _bg[0] = ("l", lay)
        else:
            _bg[0] = ("c", color(x))

    def _restore_bg():
        b = _bg[0]
        if b is not None:
            if b[0] == "c":
                canvas.cls(b[1])
            else:
                draw_layer(b[1], 0, 0)

    # Multiplayer input (#65): btn/btnp take an optional player slot. Player 0 is
    # the local console -- it calls input.held/pressed DIRECTLY, byte-for-byte as
    # before, so every existing single-player cart is unchanged. Higher slots read
    # the PlayerRouter attached to the InputState (console.wire_workstation_core);
    # with no extra controller registered they are always "not held" and players()
    # is 1. `input` may be a bare stub (make_api probes / unit tests) with no
    # router, so fall back to the local path.
    _prouter = getattr(input, "players", None)
    _held = input.held        # bound once: btn is the hottest verb a cart calls,
    _pressed = input.pressed  # and LOAD_ATTR per call is pure dispatch tax (#66)

    # NO player argument means ANY controller (the union of every source) --
    # a one-player cart must respond to whatever is plugged in, which is the
    # owner's requirement for the whole multi-source model. An EXPLICIT player
    # addresses that slot, 0 included; with nothing assigned the two answers
    # are the same set, so this only starts to differ once a source is given a
    # player of its own.
    def btn(name, player=None):
        if name not in CART_BUTTONS:
            return False
        if player is None:
            return _held(name)
        if _prouter is None:
            return _held(name) if not player else False
        return _prouter.held(name, player)

    def btnp(name, player=None):
        if name not in CART_BUTTONS:
            return False
        if player is None:
            return _pressed(name)
        if _prouter is None:
            return _pressed(name) if not player else False
        return _prouter.pressed(name, player)

    def players():
        # The connected player count (>=1) so a cart can offer a 2P/co-op mode.
        return _prouter.count() if _prouter is not None else 1

    ns = {
        "W": canvas.w, "H": canvas.h,
        "cls": canvas.cls, "pix": canvas.pix,
        "line": canvas.line, "rect": canvas.rect, "rectb": canvas.rectb,
        "circ": canvas.circ, "circb": canvas.circb, "spr": _spr_entry,
        "tri": canvas.tri, "trib": canvas.trib,
        "sspr": sspr, "tline": tline,
        "background": background, "_moy_restore_bg": _restore_bg,
        "make_layer": make_layer, "draw_layer": draw_layer,
        "map": map_, "mget": mget, "mset": mset,
        "fget": fget, "fset": fset,
        "print": canvas.print, "touch": touch, "mouse": mouse,
        "clip": canvas.clip, "camera": canvas.camera,
        "pal": canvas.pal, "palt": canvas.palt,
        "btn": btn, "btnp": btnp, "players": players,
        "key": key, "keyp": keyp, "time": time, "pmem": pmem_fn,
        "textmode": textmode, "quit": _quit, "view": view,
        "cfg": cfg, "col": color,
        "sfx": _sfx, "beep": _beep, "music": _music,
        "music_stop": _music_stop, "sound_stop": _sound_stop, "volume": _volume,
        "rnd": lambda n=1.0: random.random() * n,
        "flr": lambda x: int(x // 1),
        "Image": Image,
        "image": image,
        "table": table, "text": text,
    }
    # Capability-gated network API (#38): the shared Workstation passes a non-None
    # wifi backend ONLY for a cart with the "network" permission, so a normal kid
    # cart's namespace never carries `wifi` (the base key-set is identical on
    # every tier).
    if wifi is not None:
        ns["wifi"] = wifi
    # Capability-gated multiplayer message API (#65): net.send(data)/on_net(fn),
    # injected ONLY for a cart whose manifest permissions include "multiplayer"
    # (the Player passes a non-None backend then, mirroring the wifi gate).
    # on_net registers the handler the Player pumps each frame (net.on_message)
    # -- the old radio contract. A normal kid cart's namespace never carries
    # `net`/`on_net`.
    if net is not None:
        ns["net"] = net

        def on_net(fn):
            net.on_message(fn)
            return fn

        ns["on_net"] = on_net
    # Capability-gated PHYSICAL I/O (#9): pin_write/pin_read, injected only when
    # the thing serving this page has pins and answered the probe -- today the
    # Zero, over POST /gpio. Gated the same way and for the same reason as wifi
    # and net: a verb that cannot work must not have a NAME. A stubbed
    # `pin_write` that quietly does nothing is the worst of the three answers a
    # kid can get, because the cart looks right and the LED never lights, and
    # there is nothing to search for.
    #
    # The queue behind these is gpio_link.GpioLink; a write returns as soon as
    # it is queued and `pin_read` answers from the last batch, so neither verb
    # can stall a frame on the network. That latency is documented in
    # docs/moy_cart_api.md, because it is the one thing about them a cart
    # author has to hold in their head.
    if gpio is not None:
        ns["pin_write"] = gpio.write
        ns["pin_read"] = gpio.read
    # Scene accessors (#85): scene()/scene(name)/load_scene(name) over the cart's
    # placed-actor scenes. Pure DATA (no drawing) -- the logic lives once in the
    # shared widgets.Scenes and make_api just binds its methods. The Player
    # always passes a Scenes object (empty for a scene-less cart); a
    # make_layer/probe caller omits it, so a layer's ns carries no scene names.
    if scenes is not None:
        ns["scene"] = scenes.scene
        ns["load_scene"] = scenes.load_scene
        # Actor-aware helpers (#109 / #85 Section 8): the live mutable actor world +
        # its verbs, the cart-API mirror of the actor blocks. The world + logic are
        # shared (widgets.SceneWorld); only draw_scene is here because it draws --
        # through the native spr entry when the canvas has one. The world resets
        # per run via scenes.reset() (Player.start).
        _world = scenes.world()
        ns["actors"] = _world.actors
        ns["touching"] = _world.touching
        ns["move_actor"] = _world.move
        ns["move_actor_to"] = _world.move_to
        ns["remove_actor"] = _world.remove

        _rot_cache = {}

        def _rot_sprite(_tile, _deg):
            # A cached rotated copy of a sheet sprite (1-degree buckets, #85/#93
            # all-around rotation). rotate_indices fills the exposed corners with
            # -1, which spr always skips, so rotation keeps clean transparent edges.
            if sheet is None:
                return None
            _key = (_tile, int(_deg) % 360)
            _im = _rot_cache.get(_key)
            if _im is None:
                _base = sheet.tile_image(_tile, -1)
                if _base is None:
                    return None
                _rp, _rw, _rh = _rotate_indices(
                    _base.pix, _base.w, _base.h, _deg, _base.transparent)
                _t = _base.transparent if _base.transparent is not None else -1
                _im = Image(_rw, _rh, _rp, _t)
                _rot_cache[_key] = _im
            return _im

        def draw_scene():
            # #85/#93 Looks + rotation (Scratch rotation styles): hide / size /
            # direction (all-around=rotate, left-right=flip, none) / say. An actor
            # with no `dir` draws as placed (the common case, unchanged).
            for _a in _world.actors():
                _f = _a.flags
                if _f.get("hidden"):
                    continue
                _sc = _f.get("size", 100) // 100
                if _sc < 1:
                    _sc = 1
                _dir = _f.get("dir")
                if _dir is None:
                    _spr_entry(_a.tile, _a.x, _a.y, -1, _sc, _a.flip)
                else:
                    _style = _f.get("rot", "all")
                    if _style == "none":
                        _spr_entry(_a.tile, _a.x, _a.y, -1, _sc, _a.flip)
                    elif _style == "leftright":
                        _spr_entry(_a.tile, _a.x, _a.y, -1, _sc,
                                   1 if (_dir % 360) > 180 else 0)
                    else:                             # "all" around: rotate to heading
                        _im = _rot_sprite(_a.tile, _dir - 90)
                        if _im is None:
                            _spr_entry(_a.tile, _a.x, _a.y, -1, _sc, _a.flip)
                        else:
                            _cx = _a.x + 4 * _sc      # centre the (larger) rotated
                            _cy = _a.y + 4 * _sc      # sprite on the 8x8 actor's centre
                            canvas.spr(_im, _cx - (_im.w * _sc) // 2,
                                       _cy - (_im.h * _sc) // 2, _sc)
                _sy = _f.get("say")
                if _sy:
                    _t = str(_sy)[:10]
                    _bw = len(_t) * 8 + 4
                    _by = _a.y - 11 if _a.y >= 11 else _a.y + 9
                    canvas.rect(_a.x, _by, _bw, 10, 7)      # white bubble
                    canvas.rectb(_a.x, _by, _bw, 10, 0)     # black outline
                    canvas.print(_t, _a.x + 2, _by + 1, 0)  # black text

        ns["draw_scene"] = draw_scene
    return ns
