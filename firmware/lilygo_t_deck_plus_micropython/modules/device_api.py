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
from widgets import rotate_indices          # #85/#93 all-around sprite rotation

# The logical buttons a CART may read (moy SPEC.md 7.3) -- host twin:
# runtime/host_api.py. This board's InputState carries x/y/select/start/save/
# share/stop/home besides; those are the console's, not a cart's. "home" most
# of all: §7.3 gives exit to the HOST, so the cart never sees it.
CART_BUTTONS = ("left", "right", "up", "down", "a", "b", "run")


def make_api(canvas, input, config, sheet=None, audio=None, tilemap=None,
             pmem=None, wifi=None, images=None, scenes=None, tables=None,
             texts=None, net=None, owner="cart"):
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

    def spans(n):
        # spans(n) -> a reusable int16 span buffer for rect_batch (#167), n*5 slots
        # laid out [x, y, w, h, c] per span. Allocate it ONCE in _init and refill it
        # by index every frame: the native fill_rects gate takes a BUFFER (it calls
        # mp_get_buffer_raise, so a plain list raises), and a per-frame allocation of
        # a few-hundred-span pack is exactly the churn that costs a collect. Carts
        # have no imports, so this is the only way for one to hold a buffer.
        from array import array as _array
        return _array("h", bytearray(2 * 5 * int(n)))

    def rect_batch(items, n=-1, ox=0, oy=0, c=-1):
        # rect_batch(items[, n, ox, oy, c]): draw MANY filled rects in ONE call
        # (#167) -- the rect analogue of spr_batch, riding the #163 span-batch gate
        # so N spans are one MP->C crossing instead of N. `items` is FLAT: x, y, w,
        # h, c repeated (a flat sequence is ONE allocation instead of N tuples,
        # which is what makes a few-hundred-span software-3D frame affordable).
        # `n` limits how many quints are read (-1 = all), ox/oy shift every rect,
        # c >= 0 overrides every colour slot. Host twin: host_app.rect_batch.
        canvas.fill_rects(items, n, ox, oy, c)

    def sspr(sx, sy, sw, sh, dx, dy, dw=None, dh=None, colorkey=-1, flip=0):
        # sspr(sx, sy, sw, sh, dx, dy[, dw, dh, colorkey, flip]): stretch a sw x sh
        # PIXEL region of the sheet into a dw x dh destination rect (#167) --
        # arbitrary scale, unlike spr()'s integer one. Source coords are sheet
        # PIXELS, not tile ids. Per-destination-pixel until it gets a native
        # kernel, so this is correctness, not a frame-loop verb yet.
        if sheet is None:
            return
        canvas.sspr(sheet, sx, sy, sw, sh, dx, dy, dw, dh, colorkey, flip)

    def tline(x0, y0, x1, y1, u, v, du, dv, colorkey=-1):
        # tline(x0, y0, x1, y1, u, v, du, dv[, colorkey]): SPEC.md 6.1's textured
        # line -- exactly line()'s pixels, sampling the MAP as a virtual texture
        # in 16.16 FIXED POINT (ints: float * 65536 is the cart's job). One call
        # per scanline is a Mode 7 floor; one per column textures a raycaster.
        # Native moy_gfx.tline on this backend. Host twin: host_app.tline.
        if sheet is None or tilemap is None:
            return
        canvas.tline(tilemap, sheet, x0, y0, x1, y1, u, v, du, dv, colorkey)

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

    def view(w=0, h=0):
        # view(w, h) -> declare the cart's LOGICAL viewport: the composite
        # scales this centered w x h region of the 320x240 canvas to the glass
        # at the biggest integer scale that fits (celeste's 128x128 -> 4x on
        # the P4). view() / view(0, 0) restores the full canvas. ADDITIVE like
        # textmode/quit; rides InputState, cleared by Player.start. Host twin:
        # host_app.view.
        input.game_view = (int(w), int(h)) if w and h else None

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

    # Multiplayer input (#65, host == device): btn/btnp take an optional player
    # slot. Player 0 is the local console -- it calls input.held/pressed DIRECTLY,
    # byte-for-byte as before, so every existing single-player cart is unchanged.
    # Higher slots read the PlayerRouter attached to the InputState (in
    # console.wire_workstation_core); with no extra controller registered they are
    # always "not held" and players() is 1. `input` may be a bare stub with no
    # router (probes / tests) -- fall back to the local path.
    _prouter = getattr(input, "players", None)
    _held = input.held        # bound once: btn is the hottest verb a cart calls,
    _pressed = input.pressed  # and LOAD_ATTR per call is pure dispatch tax (#66)

    def btn(name, player=0):
        if name not in CART_BUTTONS:
            return False
        if player:
            return _prouter.held(name, player) if _prouter is not None else False
        return _held(name)

    def btnp(name, player=0):
        if name not in CART_BUTTONS:
            return False
        if player:
            return _prouter.pressed(name, player) if _prouter is not None else False
        return _pressed(name)

    def players():
        # The connected player count (>=1) so a cart can offer a 2P/co-op mode.
        return _prouter.count() if _prouter is not None else 1

    ns = {
        "W": canvas.w, "H": canvas.h,
        "cls": canvas.cls, "pix": canvas.pix,
        "line": canvas.line, "rect": canvas.rect, "rectb": canvas.rectb,
        "circ": canvas.circ, "circb": canvas.circb, "spr": _spr_entry,
        "tri": canvas.tri, "trib": canvas.trib,
        "rect_batch": rect_batch, "spans": spans, "sspr": sspr, "tline": tline,
        "spr_batch": spr_batch,
        "background": background, "_moy_restore_bg": _restore_bg,
        "make_layer": make_layer, "draw_layer": draw_layer,
        "map": map_, "mget": mget, "mset": mset,
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
    # cart's namespace never carries `wifi` (the base key-set is identical here and
    # on the host).
    if wifi is not None:
        ns["wifi"] = wifi
    # Capability-gated multiplayer message API (#65, host == device): net.send(data)
    # / on_net(fn), injected ONLY for a cart whose manifest permissions include
    # "multiplayer" (the Player passes a non-None backend then, like the wifi gate).
    # on_net registers the handler the Player pumps each frame -- the old radio
    # contract. A normal kid cart's namespace never carries `net`/`on_net`.
    if net is not None:
        ns["net"] = net

        def on_net(fn):
            net.on_message(fn)
            return fn

        ns["on_net"] = on_net
    # Scene accessors (#85): scene()/scene(name)/load_scene(name) over the cart's
    # placed-actor scenes. Pure DATA (no drawing) -- the logic lives once in the
    # shared widgets.Scenes and make_api just binds its methods (host == device). The
    # Player always passes a Scenes object (empty for a scene-less cart); a
    # make_layer/probe caller omits it, so a layer's ns simply carries no scene names.
    if scenes is not None:
        ns["scene"] = scenes.scene
        ns["load_scene"] = scenes.load_scene
        # Actor-aware helpers (#109 / #85 Section 8): the live mutable actor world +
        # its verbs, the cart-API mirror of the actor blocks. The world + logic are
        # shared with the host (widgets.SceneWorld); only draw_scene is per-backend
        # because it draws -- here through the native spr entry (fast path). The world
        # resets per run via scenes.reset() (Player.start).
        _world = scenes.world()
        ns["actors"] = _world.actors
        ns["touching"] = _world.touching
        ns["move_actor"] = _world.move
        ns["move_actor_to"] = _world.move_to
        ns["remove_actor"] = _world.remove

        _rot_cache = {}

        def _rot_sprite(_tile, _deg):
            if sheet is None:
                return None
            _key = (_tile, int(_deg) % 360)
            _im = _rot_cache.get(_key)
            if _im is None:
                _base = sheet.tile_image(_tile, -1)
                if _base is None:
                    return None
                _rp, _rw, _rh = rotate_indices(
                    _base.pix, _base.w, _base.h, _deg, _base.transparent)
                _t = _base.transparent if _base.transparent is not None else -1
                _im = Image(_rw, _rh, _rp, _t)
                _rot_cache[_key] = _im
            return _im

        def draw_scene():
            # #85/#93 Looks + rotation (Scratch rotation styles): hide / size / direction
            # (all-around=rotate, left-right=flip, none) / say. No `dir` -> draw as placed.
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
                    else:
                        _im = _rot_sprite(_a.tile, _dir - 90)
                        if _im is None:
                            _spr_entry(_a.tile, _a.x, _a.y, -1, _sc, _a.flip)
                        else:
                            canvas.spr(_im, _a.x + 4 * _sc - (_im.w * _sc) // 2,
                                       _a.y + 4 * _sc - (_im.h * _sc) // 2, _sc)
                _sy = _f.get("say")
                if _sy:
                    _t = str(_sy)[:10]
                    _bw = len(_t) * 8 + 4
                    _by = _a.y - 11 if _a.y >= 11 else _a.y + 9
                    canvas.rect(_a.x, _by, _bw, 10, 7)
                    canvas.rectb(_a.x, _by, _bw, 10, 0)
                    canvas.print(_t, _a.x + 2, _by + 1, 0)

        ns["draw_scene"] = draw_scene
    return ns
