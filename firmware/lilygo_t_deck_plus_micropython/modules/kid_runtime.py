# KidCode v0.4 workstation -- DEVICE side.
#
# Boots the fantasy workstation on the T-Deck: a cartridge launcher + the carts,
# navigated with the keyboard/trackball, each cart drawn through the native
# kc_compositor. The drawing API (cls/pix/rect/rectb/circ/circb/spr/print/btn/...,
# TIC-80 style: rect/circ are filled, rectb/circb are outlines) matches the host
# `runtime/` reference, so cartridges are portable; only the
# canvas backend differs (framebuf over the compositor buffer + palette->RGB565).
#
# v1 embeds the cart sources; loading real .kcart files from SD is the follow-on.

import time

# Editor cores (CodeEditor / SpriteSheet / PaintEditor) are backend-agnostic and
# shared verbatim with the host (canonical: runtime/editors.py; build.sh stages a
# copy into modules/ so it freezes here as the top-level module `editors`).
from editors import CodeEditor, PaintEditor, SpriteSheet
from console import NAMES, Pointer, Workstation, _cursor_delta, color
from carts_data import CARTS  # build-time generated from system_carts/ (tools/gen_device_carts.py)

# KID64 palette as RGB565 (generated from runtime/palette.py; no colorsys here).
PAL565 = (
    0x0000, 0x194A, 0x792A, 0x042A, 0xAA86, 0x5AA9, 0xC618, 0xFF9D,
    0xF809, 0xFD00, 0xFF64, 0x0726, 0x2D7F, 0x83B3, 0xFBB5, 0xFE75,
    0x70E3, 0x71E3, 0x72E3, 0x6383, 0x4383, 0x2383, 0x1B86, 0x1B8A,
    0x1B8E, 0x1A8E, 0x198E, 0x20EE, 0x40EE, 0x60EE, 0x70EB, 0x70E7,
    0xB165, 0xB2E5, 0xB485, 0xA585, 0x6D85, 0x3D85, 0x2D89, 0x2D90,
    0x2D96, 0x2C16, 0x2A76, 0x3976, 0x6976, 0xA176, 0xB172, 0xB16B,
    0xF1E7, 0xF407, 0xF627, 0xDF87, 0x9787, 0x5787, 0x3F8D, 0x3F95,
    0x3F9E, 0x3D7E, 0x3B5E, 0x51FE, 0x91FE, 0xD9FE, 0xF1F8, 0xF1F0,
)

# RGB565 colour-key for native sprite blits: transparent sprite pixels are baked
# to this value so kc_gfx.blit565 skips them. Magenta is absent from KID64; a
# visible pixel that happens to equal it is nudged by one LSB when the cache is
# built (see DeviceCanvas._cache_rgb), so it can never read as transparent.
_RGB_KEY = 0xF81F

# Flip to False to force the slow Python per-pixel drawing path (no native kc_gfx)
# for an FPS A/B comparison against the native-blit build.
_USE_GFX = True


class Image:
    def __init__(self, width, height, pix, transparent=-1):
        self.w = width
        self.h = height
        self.pix = pix
        self.transparent = transparent

    @classmethod
    def from_ascii(cls, rows, mapping, transparent="."):
        h = len(rows)
        w = max(len(r) for r in rows) if rows else 0
        pix = []
        for y in range(h):
            row = rows[y]
            for x in range(w):
                ch = row[x] if x < len(row) else transparent
                pix.append(-1 if ch == transparent else (mapping[ch] & 63))
        return cls(w, h, pix, -1)


class DeviceCanvas:
    """The kid drawing API. The hot ops (cls/rect/circ/spr) go through the native
    kc_gfx C kernel writing straight into the compositor's RGB565 framebuffer --
    this is what keeps complex carts off the slow per-pixel Python path. framebuf
    over the same buffer still serves text/lines/pixels and is the fallback on an
    image built without kc_gfx."""

    def __init__(self, compositor):
        import framebuf

        self._comp = compositor
        self.w, self.h = compositor.size()
        self._buf = compositor.framebuffer()          # raw RGB565 bytearray (for kc_gfx)
        self._fb = framebuf.FrameBuffer(self._buf, self.w, self.h, framebuf.RGB565)
        self._gfx = compositor.gfx() if _USE_GFX else None   # native kernel, or None

    def _col(self, c):
        return PAL565[c & 63]

    def _fill(self, x, y, w, h, col):
        # Filled rect of a pre-resolved RGB565 colour; native (clamped in C) when
        # kc_gfx is present, else framebuf. Shared by rect() and circ().
        if self._gfx is not None:
            self._gfx.fill_rect(self._buf, self.w, x, y, w, h, col)
        else:
            self._fb.fill_rect(x, y, w, h, col)

    def cls(self, c=0):
        col = self._col(c)
        if self._gfx is not None:
            self._gfx.fill(self._buf, self.w * self.h, col)
        else:
            self._fb.fill(col)

    def pix(self, x, y, c=None):
        # TIC-80 pix: read the index with two args, set it with three.
        if c is None:
            return self._fb.pixel(int(x), int(y))
        self._fb.pixel(int(x), int(y), self._col(c))

    def line(self, x1, y1, x2, y2, c):
        self._fb.line(int(x1), int(y1), int(x2), int(y2), self._col(c))

    def rect(self, x, y, w, h, c):
        # TIC-80 rect = FILLED rectangle.
        self._fill(int(x), int(y), int(w), int(h), self._col(c))

    def rectb(self, x, y, w, h, c):
        # TIC-80 rectb = rectangle outline.
        self._fb.rect(int(x), int(y), int(w), int(h), self._col(c))

    def circ(self, cx, cy, r, c):
        # TIC-80 circ = FILLED circle.
        cx = int(cx); cy = int(cy); r = int(r)
        col = self._col(c)
        for dy in range(-r, r + 1):
            span = int((r * r - dy * dy) ** 0.5)
            self._fill(cx - span, cy + dy, 2 * span + 1, 1, col)

    def circb(self, cx, cy, r, c):
        # TIC-80 circb = circle outline.
        cx = int(cx); cy = int(cy); r = int(r)
        col = self._col(c)
        x = r; y = 0; err = 0
        fb = self._fb
        while x >= y:
            for px, py in ((x, y), (y, x), (-y, x), (-x, y), (-x, -y), (-y, -x), (y, -x), (x, -y)):
                fb.pixel(cx + px, cy + py, col)
            y += 1
            if err <= 0:
                err += 2 * y + 1
            else:
                x -= 1
                err -= 2 * x + 1

    def spr(self, img, x, y, scale=1):
        x = int(x); y = int(y); scale = int(scale)
        if scale < 1:
            scale = 1
        if self._gfx is None:
            self._spr_py(img, x, y, scale)
            return
        # Blit a cached, pre-scaled RGB565 copy of the sprite in one C call. The
        # cache lives on the Image (sheet tiles are reused across frames via the
        # make_api tile cache, so the rebuild is once-per-sprite, not per-frame).
        if getattr(img, "_rgb", None) is None or getattr(img, "_rgb_scale", 0) != scale:
            self._cache_rgb(img, scale)
        self._gfx.blit565(self._buf, self.w, self.h, x, y,
                          img._rgb, img._rgb_w, img._rgb_h, _RGB_KEY)

    def _cache_rgb(self, img, scale):
        # Bake the indexed sprite into an RGB565 buffer at `scale`, transparent
        # pixels set to _RGB_KEY so blit565 skips them. Built rarely (cached), so
        # the per-pixel loop here is fine -- it's the per-frame blit that matters.
        import framebuf

        w = img.w * scale
        h = img.h * scale
        buf = bytearray(w * h * 2)
        fb = framebuf.FrameBuffer(buf, w, h, framebuf.RGB565)
        fb.fill(_RGB_KEY)
        pal = PAL565
        t = img.transparent
        pix = img.pix
        iw = img.w
        for sy in range(img.h):
            base = sy * iw
            for sx in range(iw):
                p = pix[base + sx]
                if p == t or p < 0:
                    continue
                col = pal[p & 63]
                if col == _RGB_KEY:
                    col ^= 0x20          # nudge a visible pixel off the colour-key
                fb.fill_rect(sx * scale, sy * scale, scale, scale, col)
        img._rgb = buf
        img._rgb_w = w
        img._rgb_h = h
        img._rgb_scale = scale

    def _spr_py(self, img, x, y, scale):
        # Per-pixel fallback when kc_gfx is absent (image built without it).
        fb = self._fb
        pal = PAL565
        t = img.transparent
        for sy in range(img.h):
            base = sy * img.w
            for sx in range(img.w):
                p = img.pix[base + sx]
                if p == t or p < 0:
                    continue
                fb.fill_rect(x + sx * scale, y + sy * scale, scale, scale, pal[p & 63])

    def map(self, tilemap, sheet, mx=0, my=0, w=None, h=None,
            sx=0, sy=0, colorkey=-1, scale=1):
        # TIC-80 map(): blit a w x h cell region of the tilemap over `sheet` to
        # screen (sx, sy) in ONE native kc_gfx.blit_map call (issue #32). The sheet
        # is baked once into an RGB565 tile atlas (cached on the sheet, rebuilt only
        # on a paint edit via sheet.gen), so per-frame cost is just the C walk.
        mx = int(mx); my = int(my); sx = int(sx); sy = int(sy); scale = int(scale)
        if scale < 1:
            scale = 1
        if w is None:
            w = tilemap.w - mx
        if h is None:
            h = tilemap.h - my
        tile = sheet.TILE
        if self._gfx is None:
            self._map_py(tilemap, sheet, mx, my, int(w), int(h), sx, sy, colorkey, scale)
            return
        atlas, ntiles = self._sheet_atlas(sheet, colorkey)
        self._gfx.blit_map(self._buf, self.w, self.h, sx, sy,
                           tilemap.cells, tilemap.w, tilemap.h,
                           mx, my, int(w), int(h),
                           atlas, ntiles, tile, scale, _RGB_KEY)

    def _sheet_atlas(self, sheet, colorkey):
        # Bake the whole sheet into a contiguous RGB565 tile atlas (ntiles tiles of
        # TILE x TILE, tile-major) for kc_gfx.blit_map. Cached on the sheet and keyed
        # by (gen, colorkey) so a paint edit or a different colorkey rebakes; this is
        # the map() analogue of _cache_rgb. Transparent indices (== colorkey) bake to
        # _RGB_KEY so blit_map skips them.
        gen = getattr(sheet, "gen", 0)
        if (getattr(sheet, "_atlas", None) is not None
                and sheet._atlas_gen == gen and sheet._atlas_key == colorkey):
            return sheet._atlas, sheet._atlas_n
        tile = sheet.TILE
        ntiles = sheet.count
        tpx = tile * tile
        buf = bytearray(ntiles * tpx * 2)
        pal = PAL565
        cols = sheet.cols
        sw = sheet.w
        spix = sheet.pix
        key = _RGB_KEY
        pos = 0
        for n in range(ntiles):
            ox = (n % cols) * tile
            oy = (n // cols) * tile
            for ly in range(tile):
                base = (oy + ly) * sw + ox
                for lx in range(tile):
                    p = spix[base + lx]
                    if p == colorkey:
                        col = key
                    else:
                        col = pal[p & 63]
                        if col == key:
                            col ^= 0x20      # nudge a visible pixel off the key
                    buf[pos] = col & 0xFF
                    buf[pos + 1] = (col >> 8) & 0xFF
                    pos += 2
        sheet._atlas = buf
        sheet._atlas_n = ntiles
        sheet._atlas_gen = gen
        sheet._atlas_key = colorkey
        return buf, ntiles

    def _map_py(self, tilemap, sheet, mx, my, w, h, sx, sy, colorkey, scale):
        # Per-tile fallback when kc_gfx is absent: draw each non-empty cell via the
        # framebuf spr path. Tile images cached by id so a repeat tile builds once.
        tile = sheet.TILE
        step = tile * scale
        cache = {}
        for cy in range(h):
            ty = my + cy
            py = sy + cy * step
            for cx in range(w):
                tid = tilemap.mget(mx + cx, ty)
                if tid < 0:
                    continue
                img = cache.get(tid)
                if img is None:
                    img = sheet.tile_image(tid, colorkey)
                    cache[tid] = img if img is not None else False
                if not img:
                    continue
                self._spr_py(img, sx + cx * step, py, scale)

    def print(self, s, x, y, c, scale=2):
        self._fb.text(str(s), int(x), int(y), self._col(c))


def make_api(canvas, input, config, sheet=None, audio=None, tilemap=None,
             pmem=None, wifi=None):
    import random

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

    def spr(n, x, y, colorkey=-1, scale=1, w=1, h=1):
        # TIC-80 spr(id, x, y[, colorkey, scale, w, h]) from the cart's sheet. w/h
        # are the tile span: spr(n, x, y, w=2, h=2) draws the 16x16 multi-tile sprite
        # whose top-left is tile n (#30); w=h=1 is the plain 8x8 sprite. Also accepts
        # an Image directly (ASCII-art sprites); then a 4th positional is treated as
        # scale, e.g. spr(pet, x, y, scale=4).
        if isinstance(n, Image):
            return canvas.spr(n, x, y, colorkey if colorkey != -1 else scale)
        if sheet is None:
            return
        g = getattr(sheet, "gen", 0)
        if g != _cache_gen[0]:
            tile_cache.clear()
            _cache_gen[0] = g
        ck = (int(n), colorkey, int(w), int(h))
        img = tile_cache.get(ck)
        if img is None:
            if w > 1 or h > 1:
                img = sheet.tile_span_image(int(n), int(w), int(h), colorkey)
            else:
                img = sheet.tile_image(int(n), colorkey)
            if img is None:
                return
            tile_cache[ck] = img
        canvas.spr(img, x, y, scale)

    def map_(mx=0, my=0, w=None, h=None, sx=0, sy=0, colorkey=-1, scale=1):
        # TIC-80 map(): blit a region of the cart's tilemap over the sheet (#32).
        # Same signature/semantics as the host make_api -- one native blit_map call.
        if tilemap is None or sheet is None:
            return
        canvas.map(tilemap, sheet, mx, my, w, h, sx, sy, colorkey, scale)

    def mget(x, y):
        return tilemap.mget(x, y) if tilemap is not None else -1

    def mset(x, y, tile):
        if tilemap is not None:
            tilemap.mset(x, y, tile)

    def touch():
        # GT911 pointer exposed to touch-driven carts: (x, y, tapped) this frame,
        # or None when there is no pointer. `tapped` is the press edge so a cart
        # scores at most one hit per tap. Same contract as the host make_api.
        p = getattr(input, "pointer", None)
        if p is None:
            return None
        return (p.x, p.y, bool(p.click))

    def mouse():
        # TIC-80-shaped 7-tuple (x, y, left, middle, right, scrollx, scrolly)
        # aliasing touch(): tap -> left button. The touchscreen has no
        # middle/right/scroll, so those are constant 0/False.
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

    def pmem_fn(index, value=None):
        # TIC-80 pmem(i[, v]): read pmem(i) -> int, write pmem(i, v) -> persists.
        if pmem is None:
            return 0
        return pmem.cell(index, value)

    ns = {
        "W": canvas.w, "H": canvas.h,
        "cls": canvas.cls, "pix": canvas.pix,
        "line": canvas.line, "rect": canvas.rect, "rectb": canvas.rectb,
        "circ": canvas.circ, "circb": canvas.circb, "spr": spr,
        "map": map_, "mget": mget, "mset": mset,
        "print": canvas.print, "touch": touch, "mouse": mouse,
        "btn": input.held, "btnp": input.pressed,
        "key": key, "keyp": keyp, "time": time, "pmem": pmem_fn,
        "cfg": cfg, "col": color,
        "sfx": _sfx, "beep": _beep, "music": _music,
        "music_stop": _music_stop, "sound_stop": _sound_stop, "volume": _volume,
        "rnd": lambda n=1.0: random.random() * n,
        "flr": lambda x: int(x // 1),
        "Image": Image,
        "image": lambda rows, mapping, transparent=".": Image.from_ascii(rows, mapping, transparent),
    }
    # Capability-gated network API (#38): the shared Workstation passes a non-None
    # wifi backend ONLY for a cart with the "network" permission, so a normal kid
    # cart's namespace never carries `wifi` (the base key-set is identical here and
    # on the host).
    if wifi is not None:
        ns["wifi"] = wifi
    return ns


# --- Audio backend (#16) -- I2S to the MAX98357 amp -------------------------
# NEEDS ON-DEVICE VERIFICATION. The T-Deck Plus has an I2S class-D amp + speaker
# on a SEPARATE peripheral from the shared display/SD SPI host, so audio does NOT
# collide with the SD/display bus-takeover constraints (see CLAUDE.md). The pin
# map is from the LilyGO reference (examples/I2SPlay/utilities.h):
#     I2S_BCK = GPIO 7, I2S_WS = GPIO 5, I2S_DOUT = GPIO 6
# The shared AudioEngine (frozen `audio` module) renders signed-16-bit mono PCM via
# render(n); this backend just streams those bytes to the I2S DMA buffer once per
# frame, in the single-threaded desktop loop (the same place SD ops run -- no
# background task in v1; see docs/audio_design_v04.md sec 6).
#
# UNVERIFIED until a hardware spike confirms: (1) these pins/format actually drive
# the amp (check the schematic for an amp SD-mode/gain pin); (2) the pure-Python
# mixer fits the per-frame CPU budget at 30 FPS (else drop the rate, or move the
# mixer to a native kc_audio C module like kc_gfx); (3) non-blocking write() never
# stalls a frame. Do NOT claim this is tested on hardware.

I2S_BCK = 7
I2S_WS = 5
I2S_DOUT = 6
AUDIO_RATE = 11025
AUDIO_IBUF = 4096


class DeviceAudio:
    """I2S audio backend for the T-Deck. Wraps the shared AudioEngine and feeds
    its rendered PCM to a machine.I2S TX stream once per frame. Constructed behind
    a try/except so a board/build without I2S degrades to silence, never a crash.

    STUB / NEEDS ON-DEVICE VERIFICATION -- the I2S init + per-frame feed below are
    the intended path but are unproven on hardware in this environment."""

    def __init__(self, engine):
        self.engine = engine
        self.i2s = None
        try:
            from machine import I2S, Pin
            self.i2s = I2S(
                0,
                sck=Pin(I2S_BCK),
                ws=Pin(I2S_WS),
                sd=Pin(I2S_DOUT),
                mode=I2S.TX,
                bits=16,
                format=I2S.MONO,
                rate=AUDIO_RATE,
                ibuf=AUDIO_IBUF,
            )
        except Exception as exc:  # noqa: BLE001 -- no amp / no I2S -> stay silent
            print("KidCode audio: I2S unavailable, silent:", exc)
            self.i2s = None

    # control surface (mirrors host FakeAudio / _SilentAudio) -------------
    def sfx(self, n, chan=None):
        self.engine.play_sfx(n, chan)

    def beep(self, freq, dur=0.15):
        self.engine.play_beep(freq, dur)

    def music(self, track, loop=True):
        self.engine.play_music(track, loop)

    def music_stop(self):
        self.engine.stop_music()

    def sound_stop(self, chan=None):
        self.engine.stop(chan)

    def volume(self, level):
        self.engine.set_volume(level)

    def tick(self, dt):
        """Render this frame's PCM and stream it to the I2S DMA buffer. Skips work
        when nothing is playing so a silent cart costs almost nothing. write() is
        the MicroPython non-blocking I2S write (returns early if ibuf is full) --
        never let it stall the single-threaded desktop loop."""
        if self.i2s is None:
            return
        if not self.engine.is_active():
            return
        n = int(self.engine.rate * dt)
        if n <= 0:
            return
        try:
            pcm = self.engine.render(n)
            self.i2s.write(pcm)        # NEEDS ON-DEVICE VERIFICATION (non-blocking)
        except Exception as exc:  # noqa: BLE001 -- audio must never crash the loop
            print("KidCode audio tick failed:", exc)
            self.i2s = None


def make_audio(engine):
    """Injected backend factory (#16): wrap an AudioEngine in the device I2S
    backend. run_desktop hands this to the shared Workstation, the mirror of the
    host's make_audio. STUB -- DeviceAudio playback is UNVERIFIED on hardware."""
    return DeviceAudio(engine)


# --- WiFi service (#38) -- network.WLAN STA, the device backend -------------
# NEEDS ON-DEVICE VERIFICATION. This wraps MicroPython's network.WLAN(STA_IF) and
# is the LIVE counterpart of the host FakeWifi -- same scan/connect/status/forget/
# known surface, so the WiFi-manager cart is byte-identical on host and device.
# It is a SYSTEM service: the connection persists when the manager cart exits, so
# the web editor (#22) and the AI helper (#8) can bind to / make requests over the
# IP it reports (`status()` -> ip). Credentials persist to the kid_carts wifi.json
# store and are used by autoconnect_wifi() at boot.
#
# Radio coexistence caveat: WiFi shares the ESP32-S3 radio with BLE (#26) and is a
# different mode from LoRa / ESP-NOW (#7) -- only one radio user can be active at a
# time. WiFi STA and the display SPI bus are SEPARATE peripherals (unlike SD), so
# there is no SPI-host fight, but ALL of this is UNVERIFIED on hardware here. The
# whole class is wrapped in try/except so a board/build without WiFi degrades to a
# never-connected service instead of crashing the console.


class DeviceWifi:
    """network.WLAN(STA_IF) wrapper. `store`/`root` are the kid_carts credential
    store + carts dir; connect()/forget() persist there so the next boot can
    autoconnect. UNVERIFIED on hardware -- treat the WLAN calls as a sketch."""

    def __init__(self, store=None, root=None):
        self._store = store
        self._root = root
        self.wlan = None
        try:
            import network
            self.wlan = network.WLAN(network.STA_IF)
            self.wlan.active(True)
        except Exception as exc:  # noqa: BLE001 -- no radio / no network module -> degrade
            print("KidCode wifi: WLAN unavailable, offline:", exc)
            self.wlan = None

    # -- the injected `wifi` API surface (host == device) ----------------
    def scan(self):
        """Nearby networks as (ssid, signal%, locked?) -- NEEDS ON-DEVICE VERIFICATION.
        WLAN.scan() returns (ssid, bssid, channel, RSSI, security, hidden) tuples;
        map RSSI (~-100..-30 dBm) to a 0..100 bar and security!=0 to locked."""
        if self.wlan is None:
            return []
        try:
            out = []
            for net in self.wlan.scan():
                ssid = net[0].decode() if isinstance(net[0], (bytes, bytearray)) else str(net[0])
                rssi = net[3] if len(net) > 3 else -100
                sig = max(0, min(100, 2 * (int(rssi) + 100)))   # -100->0%, -50->100%
                locked = bool(net[4]) if len(net) > 4 else False
                if ssid:
                    out.append((ssid, sig, locked))
            return out
        except Exception as exc:  # noqa: BLE001 -- a scan failure must not crash the cart
            print("KidCode wifi scan failed:", exc)
            return []

    def connect(self, ssid, password=""):
        """Associate with `ssid`, remember the creds, and report whether the link
        came up. NEEDS ON-DEVICE VERIFICATION (the connect()/isconnected() poll
        timing below is a sketch -- a real impl waits on a status callback/timeout)."""
        ssid = str(ssid)
        ok = False
        if self.wlan is not None:
            try:
                self.wlan.connect(ssid, password)
                # Brief poll for association. The single-threaded desktop loop calls
                # this between frames, so keep the budget small; a real impl should
                # spread this across frames rather than block.
                for _ in range(40):
                    if self.wlan.isconnected():
                        ok = True
                        break
                    time.sleep_ms(100)
            except Exception as exc:  # noqa: BLE001
                print("KidCode wifi connect failed:", exc)
                ok = False
        if self._store is not None and self._root is not None:
            try:
                self._store.remember_wifi(ssid, password, self._root)
            except Exception as exc:  # noqa: BLE001 -- save failure must not crash the cart
                print("KidCode wifi remember failed:", exc)
        return ok

    def disconnect(self):
        if self.wlan is not None:
            try:
                self.wlan.disconnect()
            except Exception:  # noqa: BLE001
                pass

    def status(self):
        """(connected, ssid, ip): the live link state #22/#8 read to use the net."""
        if self.wlan is None:
            return (False, None, None)
        try:
            if self.wlan.isconnected():
                ip = self.wlan.ifconfig()[0]
                ssid = None
                try:
                    ssid = self.wlan.config("essid") or None
                except Exception:  # noqa: BLE001 -- essid not always queryable
                    ssid = None
                return (True, ssid, ip)
        except Exception as exc:  # noqa: BLE001
            print("KidCode wifi status failed:", exc)
        return (False, None, None)

    def forget(self, ssid):
        ssid = str(ssid)
        if self._store is not None and self._root is not None:
            try:
                self._store.forget_wifi(ssid, self._root)
            except Exception as exc:  # noqa: BLE001
                print("KidCode wifi forget failed:", exc)
        # If we're on that network, drop it.
        try:
            if self.wlan is not None and self.wlan.isconnected():
                self.disconnect()
        except Exception:  # noqa: BLE001
            pass
        return True

    def known(self):
        if self._store is not None and self._root is not None:
            try:
                return [n["ssid"] for n in self._store.load_wifi(self._root)]
            except Exception as exc:  # noqa: BLE001
                print("KidCode wifi known failed:", exc)
        return []


def make_wifi(store=None, root=None):
    """Injected backend factory (#38): the device network.WLAN service over the
    kid_carts store. run_desktop hands this to the shared Workstation -- the mirror
    of the host's make_wifi. NEEDS ON-DEVICE VERIFICATION (DeviceWifi is a sketch)."""
    return DeviceWifi(store, root)


def autoconnect_wifi(wifi):
    """Boot-time autoconnect (#38): try the most-recently-remembered known network
    first (kid_carts stores it at the front), so the kid joins once and the console
    is online thereafter. Best-effort + guarded: a no-WiFi build or no saved creds
    just no-ops. NEEDS ON-DEVICE VERIFICATION -- the credential store round-trip is
    host-tested, but the actual WLAN association at boot is unproven on hardware."""
    if wifi is None:
        return False
    try:
        connected, _ssid, _ip = wifi.status()
        if connected:
            return True
        nets = []
        store = getattr(wifi, "_store", None)
        root = getattr(wifi, "_root", None)
        if store is not None and root is not None:
            nets = store.load_wifi(root)
        for n in nets:                      # front-of-list = last joined
            if wifi.connect(n["ssid"], n.get("password", "")):
                print("KidCode wifi autoconnected:", n["ssid"])
                return True
    except Exception as exc:  # noqa: BLE001 -- autoconnect must never block/crash boot
        print("KidCode wifi autoconnect failed:", exc)
    return False


# --- Embedded cartridges (v1) -----------------------------------------------




class TrackBall:
    """T-Deck trackball: 4 direction GPIOs pulse low when rolled; GPIO0 = click.
    Falling-edge IRQs count pulses; poll() consumes them into nav moves."""

    DIRS = (("up", 3), ("down", 15), ("left", 1), ("right", 2))
    CLICK_PIN = 0

    def __init__(self):
        self.available = False
        self._counts = [0, 0, 0, 0]
        self._click = None
        self._click_prev = 1
        try:
            from machine import Pin

            self._pins = []
            for idx, (_name, gpio) in enumerate(self.DIRS):
                p = Pin(gpio, Pin.IN, Pin.PULL_UP)
                p.irq(self._handler(idx), Pin.IRQ_FALLING)
                self._pins.append(p)
            self._click = Pin(self.CLICK_PIN, Pin.IN, Pin.PULL_UP)
            self.available = True
        except Exception as exc:  # noqa: BLE001
            print("KidCode trackball unavailable:", exc)

    def _handler(self, idx):
        counts = self._counts
        def _h(pin):
            counts[idx] += 1   # list item + small int: ISR-safe (no allocation)
        return _h

    def poll(self):
        # Returns per-direction pulse counts [up, down, left, right] + click edge,
        # so the cursor moves proportionally to how far the ball was rolled.
        counts = [0, 0, 0, 0]
        for idx in range(4):
            counts[idx] = self._counts[idx]
            self._counts[idx] = 0
        click = False
        if self._click is not None:
            lvl = self._click.value()
            if lvl == 0 and self._click_prev == 1:
                click = True
            self._click_prev = lvl
        return counts, click


# Touch -> canvas mapping, calibrated on hardware (RUN_TOUCH_CALIBRATE byte dump).
# This T-Deck's GT911 already reports landscape coords matching the 320x240 canvas
# (x ~0..320, y ~0..240), so no axis swap is needed -- only the Y axis is inverted
# (raw top=240, bottom=0). read_raw() handles the byte order (y in bytes 0-1, x in
# bytes 2-3); these just scale + flip into canvas space.
TOUCH_SWAP = False      # raw axes already match the landscape canvas
TOUCH_FLIP_X = False
TOUCH_FLIP_Y = True     # GT911 Y runs opposite the screen
TOUCH_RAW_W = 320       # GT911 reported max along x
TOUCH_RAW_H = 240       # GT911 reported max along y


class Touch:
    """T-Deck GT911 capacitive touch over I2C0 (the same bus as the keyboard,
    off the SPI bus -- no display contention). poll() returns an absolute
    (x, y, tap) in canvas coords, where tap is True only on the press edge."""

    ADDRS = (0x5D, 0x14)      # GT911 default / alternate I2C addresses
    REG_STATUS = 0x814E       # touch status: bit7 ready, low nibble = point count
    REG_POINT0 = 0x8150       # point 0: [track, xl, xh, yl, yh, sizel, ...]

    def __init__(self, w, h, i2c=None):
        self.w = w
        self.h = h
        self.available = False
        self.addr = None
        self._i2c = i2c
        self._down = False
        try:
            from machine import I2C, Pin

            if self._i2c is None:
                self._i2c = I2C(0, scl=Pin(8), sda=Pin(18), freq=400000)
            for a in self.ADDRS:
                try:
                    self._i2c.readfrom(a, 1)
                    self.addr = a
                    self.available = True
                    break
                except Exception:
                    pass
            if not self.available:
                print("KidCode touch: GT911 not found on I2C0")
        except Exception as exc:  # noqa: BLE001
            print("KidCode touch unavailable:", exc)

    def read_raw(self):
        """One GT911 read. Returns (rx, ry) when a finger is down, False when the
        controller reports a fresh sample with no touch (finger up), or None when
        no new sample is ready (state unknown -- keep whatever we had). Clears the
        status register after a ready read so the next sample is produced."""
        if not self.available:
            return None
        try:
            status = self._i2c.readfrom_mem(self.addr, self.REG_STATUS, 1, addrsize=16)[0]
        except Exception:
            return None
        if not (status & 0x80):
            return None  # buffer not ready yet -- do NOT clear, do NOT change state
        raw = False      # ready sample, default "finger up"
        if (status & 0x0F) >= 1:
            try:
                d = self._i2c.readfrom_mem(self.addr, self.REG_POINT0, 4, addrsize=16)
                # This GT911 lays the point out as y(lo,hi) then x(lo,hi) -- see
                # the touch calibration byte dump. Return (x_raw, y_raw) for _map.
                raw = (d[2] | (d[3] << 8), d[0] | (d[1] << 8))
            except Exception:
                raw = None
        try:
            self._i2c.writeto_mem(self.addr, self.REG_STATUS, b"\x00", addrsize=16)
        except Exception:
            pass
        return raw

    def debug_read(self):
        """Calibration only: return (status, 8 raw point bytes) and clear, or None
        when no fresh sample. Lets us see the exact GT911 byte layout."""
        if not self.available:
            return None
        try:
            status = self._i2c.readfrom_mem(self.addr, self.REG_STATUS, 1, addrsize=16)[0]
        except Exception:
            return None
        if not (status & 0x80):
            return None
        data = None
        if (status & 0x0F) >= 1:
            try:
                data = self._i2c.readfrom_mem(self.addr, self.REG_POINT0, 8, addrsize=16)
            except Exception:
                data = None
        try:
            self._i2c.writeto_mem(self.addr, self.REG_STATUS, b"\x00", addrsize=16)
        except Exception:
            pass
        return (status, data)

    def _map(self, rx, ry):
        if TOUCH_SWAP:
            rx, ry = ry, rx
        if TOUCH_FLIP_X:
            rx = TOUCH_RAW_W - 1 - rx
        if TOUCH_FLIP_Y:
            ry = TOUCH_RAW_H - 1 - ry
        x = rx * self.w // TOUCH_RAW_W
        y = ry * self.h // TOUCH_RAW_H
        return max(0, min(self.w - 1, x)), max(0, min(self.h - 1, y))

    def poll(self):
        raw = self.read_raw()
        if not raw:                 # None (no new sample) or False (finger up)
            if raw is False:        # only a confirmed "up" clears the press state
                self._down = False
            return None
        x, y = self._map(raw[0], raw[1])
        tap = not self._down        # press edge -> single tap/click
        self._down = True
        return (x, y, tap)


def _ticks_ms():
    try:
        return time.ticks_ms()
    except AttributeError:
        return int(time.time() * 1000)


def _ticks_diff(a, b):
    try:
        return time.ticks_diff(a, b)
    except AttributeError:
        return a - b


def _load_carts(session=None):
    """Load cartridges from SD (seeding the built-ins on first boot). Returns
    (carts, carts_root); carts_root is None (management disabled) on fallback to
    the embedded carts if the SD card is missing/unreadable.

    `session` is the SD lifecycle wrapper to mount under. Default is the
    pre-display machine.SDCard path (used by the boot prefetch); pass
    kidcode_sd.with_sd_live for the post-display native path."""
    try:
        import kidcode_sd
        import kid_carts

        if session is None:
            session = kidcode_sd.with_sd

        def _seed_and_scan():
            kid_carts.ensure_dirs()
            kid_carts.seed_builtins(CARTS)
            return kid_carts.scan()

        # Mount only for the seed+scan, then unmount: the render loop must own
        # the shared SPI bus with no SDCard device attached, or flushes hang.
        carts = session(_seed_and_scan)
        if carts:
            print("KidCode loaded %d carts from SD" % len(carts))
            return carts, kid_carts.CARTS_DIR
    except Exception as exc:  # noqa: BLE001
        print("KidCode SD carts unavailable:", exc)
    print("KidCode using built-in carts")
    return [dict(c) for c in CARTS], None


def run_desktop(handler, prefetched=None, fps_cap=30):
    """Boot the workstation on the device: launcher + carts + keyboard.

    `prefetched` is the (carts, carts_root) tuple read from SD BEFORE display
    init (see kidcode_shell._prefetch_carts). SD shares the panel's SPI bus, so
    mounting after the panel runs hard-hangs the device -- never call _load_carts
    here once the display is live."""
    if handler is not None:
        try:
            handler.deinit()  # stop the LVGL TaskHandler; the compositor owns the bus
        except Exception as exc:
            print("KidCode desktop: takeover failed:", exc)
    try:
        from tdeck_display import get_display_bus
        from kc_compositor import make_compositor
        from kidcode.input import InputState, TDeckKeyboard
    except Exception as exc:
        print("KidCode desktop unavailable:", exc)
        return
    comp = make_compositor(get_display_bus(), 320, 240, strip_h=40)
    if comp is None:
        print("KidCode desktop: no compositor")
        return

    canvas = DeviceCanvas(comp)
    inp = InputState()
    keyboard = TDeckKeyboard(inp)
    ball = TrackBall()
    touch = Touch(canvas.w, canvas.h, i2c=getattr(keyboard, "_i2c", None))
    pointer = Pointer(canvas.w, canvas.h)
    inp.pointer = pointer         # touch-driven carts read it via the api touch()
    import kidcode_sd
    # Carts are read from SD before display init; only fall back to a post-display
    # mount (now safe via the native kc_sd path) if the shell didn't prefetch.
    carts, carts_root = (prefetched if prefetched is not None
                         else _load_carts(kidcode_sd.with_sd_live))
    import kid_carts
    ws = Workstation(comp, canvas, inp, carts)
    ws.make_api = make_api        # device cart namespace (DeviceCanvas + Image + color)
    ws.make_audio = make_audio    # device I2S audio backend (#16, NEEDS HW VERIFICATION)
    ws.carts_store = kid_carts    # SD .kcart store (scan/load/save/create/dup/delete)
    ws.carts_root = carts_root
    # Writes are enabled on-device via kc_sd: it attaches the SD card to the SPI
    # host esp_lcd already initialized (instead of machine.SDCard re-initializing
    # it, which hangs the live bus). with_sd_live mounts the card once and keeps
    # it resident -- tearing it down per op silent-hangs the next panel flush.
    # can_manage falls back off if the SD root is unknown (booted on embedded carts).
    ws.can_manage = carts_root is not None
    ws._with_sd = kidcode_sd.with_sd_live
    ws.pointer = pointer
    ws.keyboard = keyboard        # lets the code editor switch to text (ASCII) mode
    # WiFi (#38): one SYSTEM service (network.WLAN STA) shared across carts, so the
    # connection persists when the WiFi-manager cart exits and #22/#8 can use it.
    # Injected into a cart's namespace ONLY when its manifest grants "network".
    # Autoconnect from the saved creds at boot. NEEDS ON-DEVICE VERIFICATION.
    ws.wifi = make_wifi(kid_carts, carts_root)
    autoconnect_wifi(ws.wifi)
    print("KidCode desktop running (kb=%d ball=%d touch=%d)"
          % (1 if keyboard.available else 0, 1 if ball.available else 0,
             1 if touch.available else 0))

    frame_ms = 1000 // fps_cap
    last = _ticks_ms()
    while True:
        now = _ticks_ms()
        dt = max(0.0, min(0.1, _ticks_diff(now, last) / 1000.0))
        last = now
        try:
            keyboard.poll()
        except Exception:
            pass
        inp.begin_frame()                       # keyboard edges (still a fallback)
        counts, click = ball.poll()             # trackball
        nx = counts[3] - counts[2]              # right - left (raw pulses)
        ny = counts[1] - counts[0]              # down - up
        if ws.screen == "menu" and ws.menu_view == "code":
            ws.nav(nx, ny)                      # in the code editor the trackball moves the caret
        else:
            dx = _cursor_delta(nx)
            dy = _cursor_delta(ny)
            if dx or dy:
                pointer.move(dx, dy)            # elsewhere it moves the cursor
        tp = touch.poll()                       # touch -> absolute position + tap
        pointer.down = tp is not None           # held finger drives drag-scroll
        if tp is not None:
            pointer.place(tp[0], tp[1])
            if tp[2]:                           # press edge = tap = click
                click = True
        pointer.click = click
        pointer.tick(now)                       # auto-hide the idle trackball cursor
        ws.handle_input()                       # keyboard W/A/S/D etc.
        ws.handle_pointer()                     # cursor hover + click
        ws.frame(dt)
        elapsed = _ticks_diff(_ticks_ms(), now)
        if elapsed < frame_ms:
            time.sleep_ms(frame_ms - elapsed)


def run_touch_calibrate(handler):
    """Touch bring-up aid (kidcode_shell.RUN_TOUCH_CALIBRATE). Draws corner
    targets and prints each GT911 sample (raw + current mapping) over serial.

    It flushes the panel only ONCE up front and then just polls + prints, so USB
    serial keeps draining -- the normal desktop loop's continuous flush starves
    USB and you'd see nothing. Touch each yellow corner, read the raw coords over
    serial, then set TOUCH_SWAP / TOUCH_FLIP_X / TOUCH_FLIP_Y / TOUCH_RAW_* above
    so the mapped value lands on that corner, and rebuild."""
    if handler is not None:
        try:
            handler.deinit()
        except Exception as exc:  # noqa: BLE001
            print("KidCode touch-cal: takeover failed:", exc)
    try:
        from tdeck_display import get_display_bus
        from kc_compositor import make_compositor
        from kidcode.input import InputState, TDeckKeyboard
    except Exception as exc:  # noqa: BLE001
        print("KidCode touch-cal unavailable:", exc)
        return
    comp = make_compositor(get_display_bus(), 320, 240, strip_h=40)
    if comp is None:
        print("KidCode touch-cal: no compositor")
        return
    canvas = DeviceCanvas(comp)
    inp = InputState()
    keyboard = TDeckKeyboard(inp)
    touch = Touch(canvas.w, canvas.h, i2c=getattr(keyboard, "_i2c", None))
    canvas.cls(NAMES["black"])
    for (cx, cy) in ((8, 8), (canvas.w - 9, 8), (8, canvas.h - 9),
                     (canvas.w - 9, canvas.h - 9), (canvas.w // 2, canvas.h // 2)):
        canvas.rectb(cx - 6, cy - 6, 12, 12, NAMES["yellow"])
    canvas.print("TOUCH CORNERS", 100, canvas.h // 2 - 24, NAMES["white"], 2)
    canvas.print("watch serial", 108, canvas.h // 2 + 8, NAMES["light_grey"], 1)
    comp.flush()
    print("KidCode touch-cal start avail=%d addr=%s"
          % (1 if touch.available else 0, hex(touch.addr) if touch.addr else "?"))
    while True:
        r = touch.debug_read()
        if r and r[1]:  # (status, 8 raw bytes) on a real touch
            status, d = r
            print("KidCode touch-cal status=0x%02x bytes=%s"
                  % (status, " ".join("%02x" % b for b in d)))
        time.sleep_ms(50)


def run_keyboard_probe(handler):
    """Keyboard bring-up aid (kidcode_shell.RUN_KEYBOARD_PROBE): read the T-Deck
    keyboard over I2C0 and print the byte each key returns -- the code-editor's
    1-byte ASCII read path. No panel takeover/flush, so USB serial stays alive
    (the desktop loop's continuous flush would starve it).

    Tap each key left->right, top->bottom; each new key prints one `KEY ...` line.
    We deliberately do NOT send the raw-matrix command (0x03) so this shows the
    keyboard's plain ASCII protocol -- exactly what the editor should consume."""
    if handler is not None:
        try:
            handler.deinit()
        except Exception as exc:  # noqa: BLE001
            print("KidCode kb-probe: takeover failed:", exc)
    try:
        from machine import I2C, Pin
    except Exception as exc:  # noqa: BLE001
        print("KidCode kb-probe unavailable:", exc)
        return
    addr = 0x55
    try:
        i2c = I2C(0, scl=Pin(8), sda=Pin(18), freq=400000)
    except Exception as exc:  # noqa: BLE001
        print("KidCode kb-probe i2c failed:", exc)
        return
    found = []
    try:
        found = i2c.scan()
    except Exception:  # noqa: BLE001
        pass
    print("KidCode keyboard probe start; i2c scan=%s addr=0x%02x"
          % ([hex(a) for a in found], addr))
    print("KidCode kb-probe: tap keys L->R, T->B. lines = KEY <n> 0x<hex> <dec> '<char>'")
    prev = 0
    n = 0
    beat = 0
    while True:
        try:
            d = i2c.readfrom(addr, 1)
            k = d[0] if d else 0
        except Exception as exc:  # noqa: BLE001
            print("KidCode kb-probe read err:", exc)
            time.sleep_ms(300)
            continue
        if k and k != prev:
            n += 1
            ch = chr(k) if 0x20 <= k <= 0x7E else "."
            print("KEY %d 0x%02x %d '%s'" % (n, k, k, ch))
        prev = k
        beat += 1
        if beat % 250 == 0:        # ~5s heartbeat so you know it's alive
            print("KidCode kb-probe alive (keys so far: %d)" % n)
        time.sleep_ms(20)
