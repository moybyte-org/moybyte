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
    """The kid drawing API, backed by a framebuf over the compositor buffer."""

    def __init__(self, compositor):
        import framebuf

        self._comp = compositor
        self.w, self.h = compositor.size()
        self._fb = framebuf.FrameBuffer(compositor.framebuffer(), self.w, self.h, framebuf.RGB565)

    def _col(self, c):
        return PAL565[c & 63]

    def cls(self, c=0):
        self._fb.fill(self._col(c))

    def pix(self, x, y, c=None):
        # TIC-80 pix: read the index with two args, set it with three.
        if c is None:
            return self._fb.pixel(int(x), int(y))
        self._fb.pixel(int(x), int(y), self._col(c))

    def line(self, x1, y1, x2, y2, c):
        self._fb.line(int(x1), int(y1), int(x2), int(y2), self._col(c))

    def rect(self, x, y, w, h, c):
        # TIC-80 rect = FILLED rectangle.
        self._fb.fill_rect(int(x), int(y), int(w), int(h), self._col(c))

    def rectb(self, x, y, w, h, c):
        # TIC-80 rectb = rectangle outline.
        self._fb.rect(int(x), int(y), int(w), int(h), self._col(c))

    def circ(self, cx, cy, r, c):
        # TIC-80 circ = FILLED circle.
        cx = int(cx); cy = int(cy); r = int(r)
        col = self._col(c)
        for dy in range(-r, r + 1):
            span = int((r * r - dy * dy) ** 0.5)
            self._fb.fill_rect(cx - span, cy + dy, 2 * span + 1, 1, col)

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

    def print(self, s, x, y, c, scale=2):
        self._fb.text(str(s), int(x), int(y), self._col(c))


def make_api(canvas, input, config, sheet=None):
    import random

    def cfg(key, default=None):
        return config.get(key, default)

    def spr(n, x, y, colorkey=-1, scale=1):
        # TIC-80 spr(id, x, y[, colorkey, scale]) from the cart's sheet. Also
        # accepts an Image directly (ASCII-art sprites); then a 4th positional is
        # treated as scale, e.g. spr(pet, x, y, scale=4).
        if isinstance(n, Image):
            return canvas.spr(n, x, y, colorkey if colorkey != -1 else scale)
        if sheet is None:
            return
        img = sheet.tile_image(int(n), colorkey)
        if img is not None:
            canvas.spr(img, x, y, scale)

    return {
        "W": canvas.w, "H": canvas.h,
        "cls": canvas.cls, "pix": canvas.pix,
        "line": canvas.line, "rect": canvas.rect, "rectb": canvas.rectb,
        "circ": canvas.circ, "circb": canvas.circb, "spr": spr,
        "print": canvas.print,
        "btn": input.held, "btnp": input.pressed,
        "cfg": cfg, "col": color,
        "rnd": lambda n=1.0: random.random() * n,
        "flr": lambda x: int(x // 1),
        "Image": Image,
        "image": lambda rows, mapping, transparent=".": Image.from_ascii(rows, mapping, transparent),
    }


# --- Embedded cartridges (v1) -----------------------------------------------
SPACE_SRC = """
stars=[]; pet=None; pet_x=0.0; pet_dir=1; t=0.0
FROG=[".GG...GG.","GWGGGGGWG","GGGGGGGGG","GGKGGGKGG","GGGGGGGGG",".GGGGGGG.","..G.G.G.."]
ROBOT=[".LLLLL.","LKOKOKL","LLLLLLL","LKLLLKL","LLLLLLL",".L...L."]
def _pet(k):
    if k=="robot": return image(ROBOT,{"L":col("light_grey"),"O":col("red"),"K":col("black")})
    return image(FROG,{"G":col("green"),"W":col("white"),"K":col("black")})
def _init():
    global stars,pet,pet_x
    n=int(cfg("star_count",80)); spd=cfg("star_speed",30)
    stars=[[rnd(W),rnd(H),spd*(0.4+rnd(0.6))] for _ in range(n)]
    pet=_pet(cfg("pet","frog")); pet_x=W*0.5
def _update(dt):
    global pet_x,pet_dir,t
    t+=dt
    for s in stars:
        s[1]+=s[2]*dt
        if s[1]>=H: s[1]=0; s[0]=rnd(W)
    pet_x+=pet_dir*40*dt
    if pet_x>W-40 or pet_x<4: pet_dir=-pet_dir
def _draw():
    cls(col(cfg("bg","dark_blue")))
    for s in stars: pix(s[0],s[1],7 if s[2]>25 else 6)
    rect(0,H-24,W,24,col("dark_green"))
    bob=2 if (int(t*4)%2==0) else 0
    spr(pet,int(pet_x),H-24-28-bob,scale=4)
    print("MY SPACE COMPUTER",10,10,col("white"),3)
"""

OCEAN_SRC = """
bubbles=[]; fish=None; fish_x=0.0; fish_dir=1; t=0.0
FISH=["...WWW..",".WWWWWWK","WWWWWWWW",".WWWWWWK","...WWW.."]
def _init():
    global bubbles,fish,fish_x
    n=int(cfg("bubble_count",60)); spd=cfg("rise_speed",25)
    bubbles=[[rnd(W),rnd(H),1+int(rnd(2)),spd*(0.5+rnd(0.8))] for _ in range(n)]
    fish=image(FISH,{"W":col("orange"),"K":col("black")}); fish_x=W*0.5
def _update(dt):
    global fish_x,fish_dir,t
    t+=dt
    for b in bubbles:
        b[1]-=b[3]*dt
        if b[1]<0: b[1]=H; b[0]=rnd(W)
    fish_x+=fish_dir*50*dt
    if fish_x>W-40 or fish_x<4: fish_dir=-fish_dir
def _draw():
    cls(col(cfg("water","blue")))
    for b in bubbles: circb(int(b[0]),int(b[1]),b[2],col("white"))
    rect(0,H-18,W,18,col("brown"))
    wob=2 if (int(t*3)%2==0) else 0
    spr(fish,int(fish_x),H-18-24-wob,scale=4)
    print("OCEAN",10,10,col("white"),3)
"""

STAR_SRC = """
BW=48; BH=14; score=0; bx=0.0; stars=[]; catcher=None
FROG=[".GG...GG.","GWGGGGGWG","GGGGGGGGG","GGKGGGKGG",".GGGGGGG."]
ROBOT=[".LLLLL.","LKOKOKL","LLLLLLL",".L...L."]
def _pet(k):
    if k=="robot": return image(ROBOT,{"L":col("light_grey"),"O":col("red"),"K":col("black")})
    return image(FROG,{"G":col("green"),"W":col("white"),"K":col("black")})
def _spawn(s):
    s[0]=rnd(W-8); s[1]=-rnd(H*0.5)-8; s[2]=cfg("fall_speed",70)*(0.7+rnd(0.6))
def _init():
    global score,bx,stars,catcher
    score=0; bx=W/2-BW/2; stars=[]
    for _ in range(int(cfg("star_count",5))):
        s=[0,0,0]; _spawn(s); stars.append(s)
    catcher=_pet(cfg("basket","frog"))
def _near():
    best=None
    for s in stars:
        if best is None or s[1]>best[1]: best=s
    return best
def _update(dt):
    global bx,score
    sp=160
    if btn("left"): bx-=sp*dt
    elif btn("right"): bx+=sp*dt
    else:
        tg=_near()
        if tg is not None:
            want=tg[0]-BW/2; bx+=max(-sp*dt,min(sp*dt,want-bx))
    if bx<0: bx=0
    if bx>W-BW: bx=W-BW
    by=H-24-BH
    for s in stars:
        s[1]+=s[2]*dt
        if s[1]+6>=by and s[1]<=by+BH and bx<=s[0]<=bx+BW: score+=1; _spawn(s)
        elif s[1]>H: _spawn(s)
def _draw():
    cls(col("black"))
    for s in stars: circ(int(s[0]),int(s[1]),3,col("yellow"))
    by=H-24-BH
    rect(0,H-24,W,24,col("dark_blue"))
    rect(int(bx),by,BW,BH,col("brown"))
    spr(catcher,int(bx)+BW//2-18,by-18,scale=4)
    print("SCORE "+str(score),10,10,col("white"),3)
"""

CARTS = [
    {"title": "Space Desktop", "type": "wallpaper", "src": SPACE_SRC,
     "cfg": {"star_count": 80, "star_speed": 30, "bg": "dark_blue", "pet": "frog"},
     "edit": [
         {"key": "star_count", "type": "int", "min": 10, "max": 300, "step": 10, "card": "ADD {value} STARS"},
         {"key": "star_speed", "type": "int", "min": 5, "max": 90, "step": 5, "card": "SKY MOVES AT {value}"},
         {"key": "pet", "type": "choice", "choices": ["frog", "robot"], "card": "PET IS A {value}"},
         {"key": "bg", "type": "choice", "choices": ["dark_blue", "dark_purple", "black", "indigo"], "card": "SKY IS {value}"},
     ]},
    {"title": "Ocean Desktop", "type": "wallpaper", "src": OCEAN_SRC,
     "cfg": {"bubble_count": 60, "rise_speed": 25, "water": "blue"},
     "edit": [
         {"key": "bubble_count", "type": "int", "min": 10, "max": 200, "step": 10, "card": "ADD {value} BUBBLES"},
         {"key": "rise_speed", "type": "int", "min": 5, "max": 80, "step": 5, "card": "BUBBLES RISE AT {value}"},
         {"key": "water", "type": "choice", "choices": ["blue", "indigo", "dark_blue"], "card": "WATER IS {value}"},
     ]},
    {"title": "Star Catcher", "type": "game", "src": STAR_SRC,
     "cfg": {"star_count": 5, "fall_speed": 70, "basket": "frog"},
     "edit": [
         {"key": "star_count", "type": "int", "min": 1, "max": 20, "step": 1, "card": "DROP {value} STARS"},
         {"key": "fall_speed", "type": "int", "min": 20, "max": 200, "step": 10, "card": "STARS FALL AT {value}"},
         {"key": "basket", "type": "choice", "choices": ["frog", "robot"], "card": "CATCHER IS A {value}"},
     ]},
]




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
    import kidcode_sd
    # Carts are read from SD before display init; only fall back to a post-display
    # mount (now safe via the native kc_sd path) if the shell didn't prefetch.
    carts, carts_root = (prefetched if prefetched is not None
                         else _load_carts(kidcode_sd.with_sd_live))
    import kid_carts
    ws = Workstation(comp, canvas, inp, carts)
    ws.make_api = make_api        # device cart namespace (DeviceCanvas + Image + color)
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
