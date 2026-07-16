"""The device cart NAMESPACE builder (make_api), extracted from moy_runtime.py so
the second device target (the P4, #58) can stage the identical kid API without
duplicating it -- moy_runtime re-imports it (`from device_api import make_api`),
and the P4's moy_runtime does the same.

make_api binds the frozen TIC-80-style cart API (cls/pix/rect/circ/spr/map/print/
btn/touch/... -- see docs/moy_cart_api.md) to a DeviceCanvas + InputState + the
injected audio/wifi backends. It is backend-shape-agnostic: everything device-
specific rides on the `canvas` (any DeviceCanvas) and `input` objects, so the SAME
function serves the T-Deck (320x240 panel canvas) and the P4 (320x240 offscreen
game canvas under the windowed WM).

Imports only the leaf device_util tick helpers, device_canvas's Image/_decode_
moyimg/_Layer, and console's color -- no moy_runtime cycle. Device-only module
(authored in modules/, staged to other device targets at build)."""

from console import color
from device_util import _ticks_ms, _ticks_diff
from device_canvas import Image, _decode_moyimg, _Layer


def make_api(canvas, input, config, sheet=None, audio=None, tilemap=None,
             pmem=None, wifi=None, images=None, scenes=None, tables=None,
             texts=None, owner="cart"):
    import random

    _img_cache = {}        # name -> decoded paint Image (see image() below), so a
                           # repeated image(name) returns the SAME Image (#63) and its
                           # RGB565 bake cache survives across frames.
    tile_cache = {}        # (tile id, colorkey) -> Image, so a redrawn sheet sprite
                           # reuses one Image (and its RGB565 blit cache) every frame
                           # instead of rebuilding it. Invalidated when the sheet's
                           # gen counter changes (a paint edit), so a live sprite edit
                           # shows fresh art instead of stale cached pixels.
    _cache_gen = [None]

    def cfg(key, default=None):
        return config.get(key, default)

    # Audio (#16): same names/signature as the host make_api. Bound to the injected
    # device audio backend (DeviceAudio / a silent fallback); no-op if absent so a
    # cart's sfx()/beep()/music() never crash when audio isn't wired.
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

    def map_(mx=0, my=0, w=None, h=None, sx=0, sy=0, colorkey=-1, scale=1):
        # TIC-80 map(): blit a region of the cart's tilemap over the sheet (#32).
        # Same signature/semantics as the host make_api -- one native blit_map call.
        if tilemap is None or sheet is None:
            return
        canvas.map(tilemap, sheet, mx, my, w, h, sx, sy, colorkey, scale)

    def spr_batch(items, colorkey=-1, scale=1):
        # spr_batch(items[, colorkey, scale]): draw MANY sheet tiles in ONE native
        # call (#43) -- the sprite analogue of map(). `items` is a sequence of
        # (tile, x, y) or (tile, x, y, flip) tuples (flip 0=none/1=h/2=v/3=both,
        # like spr()); `colorkey` + `scale` apply uniformly to the whole batch. Coords
        # are world space (the camera offsets each; the clip rect is honoured) and the
        # tiles come from the cart's sheet -- the SAME RGB565 atlas map() uses, so the
        # cost is one C walk over the items instead of N per-sprite MP->C blits. This
        # is the lever for explosion-heavy frames (the per-sprite draw-call count is
        # the device's FPS bottleneck). SHEET TILES ONLY, 1x1 tiles: Image sprites and
        # multi-tile (w/h>1) sprites still use spr(). No-op when the cart has no sheet.
        if sheet is None:
            return
        # No tile-cache refresh needed (unlike spr()): the atlas is keyed on sheet.gen,
        # so _sheet_atlas rebakes itself after a live paint edit.
        canvas.spr_batch(sheet, items, colorkey, scale)

    def mget(x, y):
        return tilemap.mget(x, y) if tilemap is not None else -1

    def mset(x, y, tile):
        if tilemap is not None:
            tilemap.mset(x, y, tile)

    def touch():
        # GT911 pointer exposed to touch-driven carts: (x, y, tapped, held) this
        # frame, or None when there is no pointer. `tapped` is the press edge so a
        # cart scores at most one hit per tap; `held` stays True while the finger
        # is on the glass (run_desktop drives pointer.down from the GT911 poll), so
        # a cart can track a DRAG (drawing, sliders). Same contract as the host.
        # Two-domain seam (#39): prefer the game-space pointer publication when the
        # console provides one (a distinct big system canvas -- the P4's windowed
        # WM), so a cart reads 320x240 viewport coords, not panel coords.
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
        # aliasing touch(): tap -> left button. The touchscreen has no
        # middle/right/scroll, so those are constant 0/False.
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
        # By default a running cart is in GAME mode: the T-Deck keyboard is in raw
        # matrix mode so a held WASD/arrow keeps driving btn() (true hold-to-move),
        # but it yields no clean typeable ASCII. Call textmode(True) to switch to
        # text mode -- the Workstation flips the keyboard to clean 1-byte ASCII so
        # key()/keyp() return typeable bytes (a password, a name); textmode(False)
        # restores game mode. Same name + behavior on the host (host_app). Resets to
        # game mode automatically when the cart exits. (On older keyboard firmware
        # that ignores raw mode the keyboard is always ASCII; textmode is then a
        # no-op flip but key()/keyp() still work via the hold-latch path.)
        input.text_mode = bool(on)

    def _quit():
        # quit() -> END this cart and return to whoever launched it (the launcher, or
        # the Editor). A cart calls it from a key or an on-screen affordance it draws.
        # This is how a TEXT-mode cart exits: once it calls textmode(True), the console's
        # hold-BACKSPACE game-exit can't reach it (BACKSPACE is a typed 0x08 the cart
        # reads as delete, and the T-Deck keyboard has no autorepeat, so the ~700ms hold
        # never accumulates) -- so a textmode(True) cart MUST provide its own exit via
        # quit(). ADDITIVE to the frozen kid API, works for ANY cart type, same name +
        # behavior on the host (host_app). Sets a flag the Player honors AFTER this
        # frame's _update runs (player.tick), popping to the run caller via
        # ws._exit_to_caller(). `quit` shadows the site builtin inside the cart's exec
        # namespace, resolving to this closure.
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
                       tables=tables, texts=texts, owner=owner)
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
        # Two forms, dispatched on the first arg (str vs ASCII rows) -- host==device:
        #   image("bg")          -> the cart's paint-image asset images/bg.moyimg as a
        #     big Image (a 64-colour MOY64 index bitmap), placed with spr(img, x, y).
        #     The #63 Fold 3 background path; DeviceCanvas.spr bakes it index->565 ONCE
        #     via blit_indices. None when the cart has no such image; the SAME Image is
        #     returned across calls (memoised) so its 565 bake survives frames.
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
                im._name = a                   # web view (#63 Fold 4): spr() ships ["imgref",
                                               # x, y, name]; the pixels ride /assets, not the frame
                _img_cache[a] = im
            return im
        return Image.from_ascii(a, mapping, transparent)

    def table(name):
        # Desk Lab interop (#78, host==device): a Sheets sheet placed in the cart's
        # folder (tables/<name>.moysheet) read as ROWS -- a list of lists of computed
        # values. Missing name -> [] (image()'s degrade-don't-throw contract). The
        # rows were decoded once at cart-load (moy_carts.decode_table).
        rows = tables.get(name) if tables else None
        return rows if rows is not None else []

    def text(name):
        # Desk Lab interop (#78): a Writer doc in the cart's folder
        # (docs/<name>.moytext) read as LINES. Missing name -> [].
        lines = texts.get(name) if texts else None
        return lines if lines is not None else []

    # #63: hand the kid the NATIVE spr fast path when available. The C gate parses
    # (n, x, y[, colorkey[, scale[, flip]]]) and appends to the canvas batch array
    # with no Python call frame -- the fix for the warm-heap frame-spill pathology
    # that made a 120-sprite kid loop cost ~150ms/frame (see make_spr_gate). The
    # Python closure above stays as its fallback (Image sprites, w/h spans, kwargs)
    # and as the whole path off-gfx (host parity, web TeeCanvas), so pixels and
    # semantics are identical either way. Kid code never changes: it's still spr().
    _spr_entry = spr
    _mkgate = getattr(canvas, "make_spr_gate", None)
    if _mkgate is not None:
        _gate = _mkgate(sheet, spr)
        if _gate is not None and callable(_gate):
            _spr_entry = _gate

    # Declared background (#63 fast-by-default -- the "software PPU layer 0", mirrors
    # host_app.make_api): the cart names its backdrop ONCE (a color or a painted
    # Image); the engine restores it at the START of every frame via the Player's
    # ns["_moy_restore_bg"] hook. An Image bakes into a hidden full-screen layer once;
    # the per-frame restore is one draw_layer window copy -- the full-screen shape the
    # async GDMA restore predicts, so on-device the backdrop costs ~0 visible ms.
    _bg = [None]

    def background(x=None):
        if x is None:
            _bg[0] = None
        elif isinstance(x, Image):
            lay = make_layer(canvas.w, canvas.h)
            lay.spr(x, 0, 0)               # bake once (paint images take blit_indices)
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

    ns = {
        "W": canvas.w, "H": canvas.h,
        "cls": canvas.cls, "pix": canvas.pix,
        "line": canvas.line, "rect": canvas.rect, "rectb": canvas.rectb,
        "circ": canvas.circ, "circb": canvas.circb, "spr": _spr_entry,
        "spr_batch": spr_batch,
        "background": background, "_moy_restore_bg": _restore_bg,
        "make_layer": make_layer, "draw_layer": draw_layer,
        "map": map_, "mget": mget, "mset": mset,
        "print": canvas.print, "touch": touch, "mouse": mouse,
        "clip": canvas.clip, "camera": canvas.camera,
        "pal": canvas.pal, "palt": canvas.palt,
        "btn": input.held, "btnp": input.pressed,
        "key": key, "keyp": keyp, "time": time, "pmem": pmem_fn,
        "textmode": textmode, "quit": _quit,
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
    # cart's namespace never carries `wifi` (the base key-set is identical here and
    # on the host).
    if wifi is not None:
        ns["wifi"] = wifi
    # Scene accessors (#85): scene()/scene(name)/load_scene(name) over the cart's
    # placed-actor scenes. Pure DATA (no drawing) -- the logic lives once in the
    # shared widgets.Scenes and make_api just binds its methods (host == device). The
    # Player always passes a Scenes object (empty for a scene-less cart); a
    # make_layer/probe caller omits it, so a layer's ns simply carries no scene names.
    if scenes is not None:
        ns["scene"] = scenes.scene
        ns["load_scene"] = scenes.load_scene
    return ns


# --- #67 Phase 1: the device Lua cart runtime (native moy_lua bridge) ---------
#
# Shared by BOTH boards (this file stages to the P4 tree): each board's
# run_desktop wires ws.lua_runtime = make_lua_runtime(ws) IFF the moy_lua
# native module is in its build. One moy_lua VM per run: hot spr() appends
# quads to the canvas's _batch_arr from C (the moy_gfx spr_gate protocol, its
# own token), every other verb IS the registered Python make_api closure
# (semantic parity by construction), and layers/images never cross the VM
# boundary -- they live in a Python-side registry spoken to through int
# handles by the prelude wrappers. The cart's whole Lua heap sits in PSRAM
# OUTSIDE the gc heap, freed wholesale at close() (#66: cart churn can't
# fragment the console).

_LUA_TOKEN = 0x7A11   # the Lua writer's batch token: never 0 (the Python
                      # writer) and outside the spr_gate sequence (1..0x4000),
                      # so interleaved runs always break via begin_batch.

_LUA_PRELUDE = """
do
  local layer_new, layer_spr_img = __layer_new, __layer_spr_img
  local layer_spr, layer_cls = __layer_spr, __layer_cls
  local draw_layer_h, image_h = __draw_layer, __image_handle
  __layer_new, __layer_spr_img, __layer_spr = nil, nil, nil
  __layer_cls, __draw_layer, __image_handle = nil, nil, nil
  function make_layer(w, h)
    local l = { __id = layer_new(w, h), W = w, H = h }
    l.spr = function(self, img, x, y, ck, sc, fl)
      if type(img) == "table" then
        layer_spr_img(self.__id, img.__img, x or 0, y or 0)
      else
        layer_spr(self.__id, img, x or 0, y or 0, ck or -1, sc or 1, fl or 0)
      end
    end
    l.cls = function(self, c) layer_cls(self.__id, c or 0) end
    return l
  end
  function draw_layer(l, cx, cy)
    draw_layer_h(l.__id, cx or 0, cy or 0)
  end
  local cache = {}
  function image(name)
    local t = cache[name]
    if t ~= nil then
      if t == false then return nil end
      return t
    end
    local h = image_h(name)
    if h < 0 then
      cache[name] = false
      return nil
    end
    t = { __img = h }
    cache[name] = t
    return t
  end
end
"""


class LuaCartRun:
    """One running lua cart: the moy_lua VM + captured cart verbs (the
    ws.lua_runtime handle shape Player._start_lua drives: .init/.update/.draw
    callables-or-None + .close())."""

    def __init__(self, ws, ns, src):
        import moy_lua
        self._moy_lua = moy_lua
        canvas = ws.canvas
        sheet = ws.project.sheet if ws.project is not None else None
        # The web-view TeeCanvas __getattr__-forwards _batch_arr to the REAL
        # canvas, so a plain getattr would hand the C spr a bypass around the
        # recorder (the exact bug TeeCanvas.make_spr_gate shadows against --
        # sprites the browser never sees). Its `_r` recorder attr marks it:
        # decline the fast path there, like the gate does.
        is_tee = getattr(canvas, "_r", None) is not None
        arr = None if is_tee else getattr(canvas, "_batch_arr", None)
        direct = arr is not None and sheet is not None
        if not direct:
            # No writable batch array (web-view Tee / no sheet): bind a dummy
            # so init() succeeds, then the Python spr closure replaces the C
            # fast path below -- the deliberate slow lane, still correct.
            from array import array
            arr = array("h", bytearray(2 * 8))
        moy_lua.init(canvas, sheet, arr, _LUA_TOKEN)
        try:
            for name in ns:
                v = ns[name]
                if name != "spr" and name != "Image" and callable(v):
                    moy_lua.register(name, v)
            moy_lua.exec("W=%d H=%d"
                         % (int(ns.get("W", 320)), int(ns.get("H", 240))), "glue")
            if not direct:
                moy_lua.register("spr", ns["spr"])
            self._install_handles(ns)
            moy_lua.exec(_LUA_PRELUDE, "prelude")
            # "@cart" so error positions render `cart:12:` -- the chunkname
            # player._lua_cart_line parses for the drop-on-the-bad-line panel
            # (#24), matching the host runner's loadstring(src, "@cart").
            moy_lua.exec(src, "@cart")
            self.init = ((lambda: moy_lua.call("_init"))
                         if moy_lua.has("_init") else None)
            self.update = ((lambda dt: moy_lua.call("_update", dt))
                           if moy_lua.has("_update") else None)
            self.draw = ((lambda: moy_lua.call("_draw"))
                         if moy_lua.has("_draw") else None)
        except Exception:
            moy_lua.close()               # a broken load never strands a VM
            raise

    def _install_handles(self, ns):
        # The object-valued API entries (layers, paint images) stay in these
        # Python-side registries; _LUA_PRELUDE's wrappers speak the int handles.
        # The registries also PIN the objects for the run's lifetime.
        layers = []
        images = []
        make_layer = ns.get("make_layer")
        draw_layer = ns.get("draw_layer")
        image = ns.get("image")
        reg = self._moy_lua.register

        def _layer_new(w, h):
            layers.append(make_layer(int(w), int(h)))
            return len(layers) - 1

        def _layer_spr_img(lid, ih, x, y):
            layers[int(lid)].spr(images[int(ih)], int(x), int(y))

        def _layer_spr(lid, tile, x, y, ck, sc, fl):
            layers[int(lid)].spr(int(tile), int(x), int(y), int(ck),
                                 int(sc), int(fl))

        def _layer_cls(lid, c):
            layers[int(lid)].cls(int(c))

        def _draw_layer(lid, cx, cy):
            draw_layer(layers[int(lid)], cx, cy)

        def _image_handle(name):
            img = image(name) if image is not None else None
            if img is None:
                return -1
            images.append(img)
            return len(images) - 1

        reg("__layer_new", _layer_new)
        reg("__layer_spr_img", _layer_spr_img)
        reg("__layer_spr", _layer_spr)
        reg("__layer_cls", _layer_cls)
        reg("__draw_layer", _draw_layer)
        reg("__image_handle", _image_handle)
        self._layers = layers
        self._images = images

    def close(self):
        self.init = None
        self.update = None
        self.draw = None
        self._layers = None
        self._images = None
        try:
            self._moy_lua.close()
        except Exception:  # noqa: BLE001 -- close must never block an exit
            pass


def make_lua_runtime(ws):
    """The ws.lua_runtime factory (Player._start_lua's seam), bound to `ws`."""
    def make(ns, src):
        return LuaCartRun(ws, ns, src)
    return make
