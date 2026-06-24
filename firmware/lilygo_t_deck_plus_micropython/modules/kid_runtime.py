# KidCode v0.4 workstation -- DEVICE side.
#
# Boots the fantasy workstation on the T-Deck: a cartridge launcher + the carts,
# navigated with the keyboard/trackball, each cart drawn through the native
# kc_compositor. The drawing API (cls/pset/rect/rectfill/circ/spr/text/btn/...)
# matches the host `runtime/` reference, so cartridges are portable; only the
# canvas backend differs (framebuf over the compositor buffer + palette->RGB565).
#
# v1 embeds the cart sources; loading real .kcart files from SD is the follow-on.

import time

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

NAMES = {
    "black": 0, "dark_blue": 1, "dark_purple": 2, "dark_green": 3, "brown": 4,
    "dark_grey": 5, "light_grey": 6, "white": 7, "red": 8, "orange": 9,
    "yellow": 10, "green": 11, "blue": 12, "indigo": 13, "pink": 14, "peach": 15,
}
_TYPE_COLOR = {"wallpaper": 12, "game": 8, "app": 11, "tool": 9}  # index by type


def color(name_or_index):
    if isinstance(name_or_index, str):
        return NAMES.get(name_or_index, 7)
    return int(name_or_index) & 63


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


# Mouse-style pointer sprite (O=black outline, F=white fill), hotspot at top-left.
CURSOR = Image.from_ascii([
    "O.......", "OO......", "OFO.....", "OFFO....", "OFFFO...", "OFFFFO..",
    "OFFFFFO.", "OFFFFFFO", "OFFFOOO.", "OFOOFO..", "OO..OFO.", "O...OFO.", "....OO..",
], {"O": 0, "F": 7}, ".")


class Pointer:
    """A screen-space cursor driven by trackball pulses; click is its button."""

    def __init__(self, w, h):
        self.w = w
        self.h = h
        self.x = w // 2
        self.y = h // 2
        self.click = False

    def move(self, dx, dy):
        self.x = max(0, min(self.w - 1, self.x + dx))
        self.y = max(0, min(self.h - 1, self.y + dy))


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

    def pset(self, x, y, c):
        self._fb.pixel(int(x), int(y), self._col(c))

    def pget(self, x, y):
        return self._fb.pixel(int(x), int(y))

    def rect(self, x, y, w, h, c):
        self._fb.rect(int(x), int(y), int(w), int(h), self._col(c))

    def rectfill(self, x, y, w, h, c):
        self._fb.fill_rect(int(x), int(y), int(w), int(h), self._col(c))

    def line(self, x1, y1, x2, y2, c):
        self._fb.line(int(x1), int(y1), int(x2), int(y2), self._col(c))

    def circfill(self, cx, cy, r, c):
        cx = int(cx); cy = int(cy); r = int(r)
        col = self._col(c)
        for dy in range(-r, r + 1):
            span = int((r * r - dy * dy) ** 0.5)
            self._fb.fill_rect(cx - span, cy + dy, 2 * span + 1, 1, col)

    def circ(self, cx, cy, r, c):
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


def make_api(canvas, input, config):
    import random

    def cfg(key, default=None):
        return config.get(key, default)

    return {
        "W": canvas.w, "H": canvas.h,
        "cls": canvas.cls, "pset": canvas.pset, "pget": canvas.pget,
        "line": canvas.line, "rect": canvas.rect, "rectfill": canvas.rectfill,
        "circ": canvas.circ, "circfill": canvas.circfill, "spr": canvas.spr,
        "text": canvas.print,
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
    for s in stars: pset(s[0],s[1],7 if s[2]>25 else 6)
    rectfill(0,H-24,W,24,col("dark_green"))
    bob=2 if (int(t*4)%2==0) else 0
    spr(pet,int(pet_x),H-24-28-bob,4)
    text("MY SPACE COMPUTER",10,10,col("white"),3)
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
    for b in bubbles: circ(int(b[0]),int(b[1]),b[2],col("white"))
    rectfill(0,H-18,W,18,col("brown"))
    wob=2 if (int(t*3)%2==0) else 0
    spr(fish,int(fish_x),H-18-24-wob,4)
    text("OCEAN",10,10,col("white"),3)
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
    for s in stars: circfill(int(s[0]),int(s[1]),3,col("yellow"))
    by=H-24-BH
    rectfill(0,H-24,W,24,col("dark_blue"))
    rectfill(int(bx),by,BW,BH,col("brown"))
    spr(catcher,int(bx)+BW//2-18,by-18,4)
    text("SCORE "+str(score),10,10,col("white"),3)
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


# --- Pointer UI layout (320x240) -------------------------------------------
_MENU_BTN = (4, 4, 76, 18)        # desktop overlay: open Make-it-mine
_HOME_BTN = (240, 4, 76, 18)      # desktop overlay: back to launcher
_RUN_BTN = (28, 188, 70, 24)
_CODE_BTN = (104, 188, 84, 24)
_CLOSE_BTN = (194, 188, 96, 24)
_CARD_X = 24
_CARD_W = 272
_CARD_Y0 = 52
_CARD_DY = 22
_CARD_H = 20
# Launcher action bar (pointer): create / duplicate / delete a cartridge.
_NEW_BTN = (12, 206, 92, 28)
_DUP_BTN = (114, 206, 92, 28)
_DEL_BTN = (216, 206, 92, 28)
_CURSOR_BASE = 4


def _cursor_delta(n):
    # n = net pulses this frame on one axis. Precise on a slow roll
    # (1 pulse -> _CURSOR_BASE+1 px), accelerates super-linearly on a fast roll.
    a = n if n >= 0 else -n
    if a == 0:
        return 0
    d = a * _CURSOR_BASE + a * a
    return d if n > 0 else -d


def _in(px, py, rect):
    x, y, w, h = rect
    return x <= px < x + w and y <= py < y + h


class Launcher:
    TILE_Y0 = 36
    TILE_H = 34
    TILE_PITCH = 40
    VISIBLE = 4

    def __init__(self, items):
        self.items = items
        self.sel = 0
        self.top = 0

    def move(self, d):
        n = len(self.items)
        if n:
            self.sel = (self.sel + d) % n
            self._scroll()

    def _scroll(self):
        if self.sel < self.top:
            self.top = self.sel
        elif self.sel >= self.top + self.VISIBLE:
            self.top = self.sel - self.VISIBLE + 1

    def selected(self):
        return self.items[self.sel] if self.items else None

    def _visible(self):
        return range(self.top, min(len(self.items), self.top + self.VISIBLE))

    def tile_rect(self, i):
        if i < self.top or i >= self.top + self.VISIBLE:
            return None
        return (10, self.TILE_Y0 + (i - self.top) * self.TILE_PITCH, 300, self.TILE_H)

    def tile_at(self, px, py):
        for i in self._visible():
            r = self.tile_rect(i)
            if r and _in(px, py, r):
                return i
        return None

    def draw(self, cv):
        cv.cls(NAMES["dark_blue"])
        cv.print("CARTRIDGES", 12, 8, NAMES["white"], 2)
        for i in self._visible():
            x, y, w, h = self.tile_rect(i)
            it = self.items[i]
            sel = (i == self.sel)
            cv.rectfill(x, y, w, h, NAMES["dark_purple"] if sel else NAMES["black"])
            cv.rect(x, y, w, h, NAMES["yellow"] if sel else NAMES["dark_grey"])
            cv.rectfill(x + 6, y + 6, 10, h - 12, _TYPE_COLOR.get(it["type"], NAMES["indigo"]))
            cv.print(it["title"], x + 24, y + 5, NAMES["white"], 2)
            cv.print(it["type"].upper(), x + 24, y + 19, NAMES["peach"], 2)
        if self.top > 0:
            cv.print("^", 300, self.TILE_Y0, NAMES["light_grey"], 2)
        if self.top + self.VISIBLE < len(self.items):
            cv.print("v", 300, self.TILE_Y0 + (self.VISIBLE - 1) * self.TILE_PITCH, NAMES["light_grey"], 2)


class Workstation:
    def __init__(self, comp, canvas, input, carts=None):
        self.comp = comp
        self.canvas = canvas
        self.input = input
        self.launcher = Launcher(carts if carts else CARTS)
        self.screen = "launcher"      # "launcher" | "desktop" | "menu"
        self.cart = None
        self.config = None
        self.ns = None
        self._update = None
        self._draw = None
        self.msel = 0                 # selected card in the menu
        self.code_view = False
        self.pointer = None           # set by run_desktop
        self.carts_root = None        # SD carts dir (reads); set by run_desktop
        self.can_manage = True        # writes enabled? device disables until the
                                      # panel-release write path lands (SD writes
                                      # while the panel is live hang the bus)
        # SD session wrapper: mounts the (display-shared) SPI bus for the
        # duration of fn(), then unmounts+deselects so the render loop's flushes
        # never collide with a mounted SDCard. Default is a host passthrough.
        self._with_sd = lambda fn: fn()

    def _start(self):
        ns = make_api(self.canvas, self.input, self.config)
        try:
            exec(self.cart["src"], ns)
            if ns.get("_init"):
                ns["_init"]()
        except Exception as exc:  # noqa: BLE001
            print("KidCode cart error:", exc)
            return False
        self.ns = ns
        self._update = ns.get("_update")
        self._draw = ns.get("_draw")
        return True

    def open(self):
        self.cart = self.launcher.selected()
        self.config = dict(self.cart["cfg"])
        self.msel = 0
        self.code_view = False
        if self._start():
            self.screen = "desktop"

    def apply(self):
        if self._start():
            self.screen = "desktop"
            self._save_config()

    def _save_config(self):
        # Persist edits to the SD cartridge (embedded fallback carts have no path).
        if self.cart and self.cart.get("path"):
            self.cart["cfg"] = dict(self.config)   # in-RAM sync (always)
            if not self.can_manage:
                return                             # writes deferred on device
            try:
                import kid_carts
                self._with_sd(lambda: kid_carts.save_config(self.cart))
            except Exception as exc:  # noqa: BLE001
                print("KidCode save failed:", exc)

    def go_home(self):
        self.screen = "launcher"
        self.cart = None
        self.ns = None

    # -- cart management (SD) ------------------------------------------------
    #
    # Each action mounts the SD card, mutates, and re-scans within a single
    # _with_sd session, then the card is unmounted before the next flush.

    def _apply_items(self, items):
        if items:
            self.launcher.items = items
            if self.launcher.sel >= len(items):
                self.launcher.sel = len(items) - 1
            self.launcher._scroll()

    def new_cart(self):
        if not self.carts_root or not self.can_manage:
            return
        try:
            import kid_carts
            self._apply_items(self._with_sd(lambda: (
                kid_carts.new_from_template(self.carts_root),
                kid_carts.scan(self.carts_root))[1]))
        except Exception as exc:  # noqa: BLE001
            print("KidCode new cart failed:", exc)

    def dup_cart(self):
        if not self.carts_root or not self.can_manage or not self.launcher.selected():
            return
        sel = self.launcher.selected()
        try:
            import kid_carts
            self._apply_items(self._with_sd(lambda: (
                kid_carts.duplicate(sel, self.carts_root),
                kid_carts.scan(self.carts_root))[1]))
        except Exception as exc:  # noqa: BLE001
            print("KidCode duplicate failed:", exc)

    def del_cart(self):
        if not self.carts_root or not self.can_manage or len(self.launcher.items) <= 1:
            return  # keep at least one cartridge
        sel = self.launcher.selected()
        try:
            import kid_carts
            self._apply_items(self._with_sd(lambda: (
                kid_carts.delete(sel),
                kid_carts.scan(self.carts_root))[1]))
        except Exception as exc:  # noqa: BLE001
            print("KidCode delete failed:", exc)

    def adjust(self, d):
        f = self.cart["edit"][self.msel]
        key = f["key"]
        cur = self.config.get(key, f.get("default"))
        if f["type"] == "int":
            v = int(cur) + d * f.get("step", 1)
            if "min" in f:
                v = max(f["min"], v)
            if "max" in f:
                v = min(f["max"], v)
            self.config[key] = v
        elif f["type"] == "choice":
            ch = f["choices"]
            idx = ch.index(cur) if cur in ch else 0
            self.config[key] = ch[(idx + d) % len(ch)]

    def card_text(self, i):
        f = self.cart["edit"][i]
        v = self.config.get(f["key"], f.get("default"))
        if f["type"] == "choice":
            v = str(v).replace("_", " ").upper()
        t = f.get("card")
        return t.replace("{value}", str(v)) if t else "%s: %s" % (f["key"].upper(), v)

    def code_lines(self):
        out = ["WHEN START:"]
        for f in self.cart["edit"]:
            v = self.config.get(f["key"], f.get("default"))
            if f["type"] == "choice":
                v = str(v).replace("_", " ").upper()
            out.append("  " + f["key"].upper() + " = " + str(v))
        return out

    def handle_input(self):
        i = self.input
        if self.screen == "launcher":
            if i.pressed("up") or i.pressed("left"):
                self.launcher.move(-1)
            if i.pressed("down") or i.pressed("right"):
                self.launcher.move(1)
            if i.pressed("a") or i.pressed("run"):
                self.open()
        elif self.screen == "desktop":
            if i.pressed("home") or i.pressed("stop"):
                self.go_home()
            elif i.pressed("b") and self.cart.get("edit"):
                self.screen = "menu"
        elif self.screen == "menu":
            ed = self.cart["edit"]
            if i.pressed("up"):
                self.msel = (self.msel - 1) % len(ed)
            if i.pressed("down"):
                self.msel = (self.msel + 1) % len(ed)
            if i.pressed("left"):
                self.adjust(-1)
            if i.pressed("right"):
                self.adjust(1)
            if i.pressed("a"):
                self.code_view = not self.code_view
            if i.pressed("run"):
                self.apply()
            elif i.pressed("b"):
                self.screen = "desktop"
            elif i.pressed("home"):
                self.go_home()

    # -- pointer (trackball-as-mouse) ----------------------------------------

    def _card_at(self, px, py):
        for i in range(len(self.cart["edit"])):
            y = _CARD_Y0 + i * _CARD_DY
            if _CARD_X <= px < _CARD_X + _CARD_W and y <= py < y + _CARD_H:
                return i
        return None

    def handle_pointer(self):
        p = self.pointer
        if p is None:
            return
        px, py, click = p.x, p.y, p.click
        if self.screen == "launcher":
            i = self.launcher.tile_at(px, py)
            if i is not None:
                self.launcher.sel = i          # hover highlights
            if click:
                if self.can_manage and _in(px, py, _NEW_BTN):
                    self.new_cart()
                elif self.can_manage and _in(px, py, _DUP_BTN):
                    self.dup_cart()
                elif self.can_manage and _in(px, py, _DEL_BTN):
                    self.del_cart()
                elif i is not None:
                    self.open()
        elif self.screen == "desktop":
            if click:
                if self.cart.get("edit") and _in(px, py, _MENU_BTN):
                    self.screen = "menu"
                elif _in(px, py, _HOME_BTN):
                    self.go_home()
        elif self.screen == "menu":
            if not self.code_view:
                ci = self._card_at(px, py)
                if ci is not None:
                    self.msel = ci             # hover highlights
            if click:
                if _in(px, py, _RUN_BTN):
                    self.apply()
                elif _in(px, py, _CODE_BTN):
                    self.code_view = not self.code_view
                elif _in(px, py, _CLOSE_BTN):
                    self.screen = "desktop"
                elif not self.code_view:
                    ci = self._card_at(px, py)
                    if ci is not None:
                        self.msel = ci
                        self.adjust(-1 if px < _CARD_X + _CARD_W // 2 else 1)

    # -- frame + drawing -----------------------------------------------------

    def frame(self, dt):
        if self.screen == "launcher":
            self.launcher.draw(self.canvas)
            if self.can_manage:
                self._btn("NEW", _NEW_BTN, NAMES["green"])
                self._btn("DUP", _DUP_BTN, NAMES["blue"])
                self._btn("DEL", _DEL_BTN, NAMES["red"])
        elif self.screen == "desktop":
            try:
                if self._update:
                    self._update(dt)
                if self._draw:
                    self._draw()
            except Exception as exc:  # noqa: BLE001
                print("KidCode frame error:", exc)
                self.go_home()
            else:
                self._draw_desktop_buttons()
        else:  # menu: frozen cart behind the panel
            try:
                if self._draw:
                    self._draw()
            except Exception:
                pass
            if self.code_view:
                self._draw_code()
            else:
                self._draw_cards()
        self._draw_cursor()
        self.comp.flush()

    def _btn(self, label, rect, fill):
        x, y, w, h = rect
        cv = self.canvas
        cv.rectfill(x, y, w, h, fill)
        cv.rect(x, y, w, h, NAMES["white"])
        cv.print(label, x + 6, y + (h - 8) // 2, NAMES["black"], 2)

    def _draw_desktop_buttons(self):
        if self.cart.get("edit"):
            self._btn("EDIT", _MENU_BTN, NAMES["dark_purple"])
        self._btn("HOME", _HOME_BTN, NAMES["dark_grey"])

    def _draw_cursor(self):
        if self.pointer is not None:
            self.canvas.spr(CURSOR, self.pointer.x, self.pointer.y, 1)

    def _draw_cards(self):
        cv = self.canvas
        cv.rectfill(20, 16, 280, 206, NAMES["dark_purple"])
        cv.rect(20, 16, 280, 206, NAMES["pink"])
        cv.print("MAKE IT MINE", 30, 22, NAMES["white"], 2)
        for i in range(len(self.cart["edit"])):
            y = _CARD_Y0 + i * _CARD_DY
            if i == self.msel:
                cv.rectfill(_CARD_X, y - 1, _CARD_W, _CARD_H, NAMES["indigo"])
            cv.print("-", _CARD_X + 4, y, NAMES["yellow"], 2)
            cv.print(self.card_text(i), _CARD_X + 22, y,
                     NAMES["white"] if i == self.msel else NAMES["light_grey"], 2)
            cv.print("+", _CARD_X + _CARD_W - 12, y, NAMES["yellow"], 2)
        self._btn("RUN", _RUN_BTN, NAMES["green"])
        self._btn("CODE", _CODE_BTN, NAMES["blue"])
        self._btn("CLOSE", _CLOSE_BTN, NAMES["red"])

    def _draw_code(self):
        cv = self.canvas
        cv.rectfill(20, 16, 280, 206, NAMES["black"])
        cv.rect(20, 16, 280, 206, NAMES["green"])
        cv.print("SEE THE CODE", 30, 22, NAMES["green"], 2)
        y = 44
        for ln in self.code_lines():
            cv.print(ln, 30, y, NAMES["light_grey"], 2)
            y += 15
        self._btn("RUN", _RUN_BTN, NAMES["green"])
        self._btn("CARDS", _CODE_BTN, NAMES["blue"])
        self._btn("CLOSE", _CLOSE_BTN, NAMES["red"])


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


def _load_carts():
    """Load cartridges from SD (seeding the built-ins on first boot). Returns
    (carts, carts_root); carts_root is None (management disabled) on fallback to
    the embedded carts if the SD card is missing/unreadable."""
    try:
        import kidcode_sd
        import kid_carts

        def _seed_and_scan():
            kid_carts.ensure_dirs()
            kid_carts.seed_builtins(CARTS)
            return kid_carts.scan()

        # Mount only for the seed+scan, then unmount: the render loop must own
        # the shared SPI bus with no SDCard device attached, or flushes hang.
        carts = kidcode_sd.with_sd(_seed_and_scan)
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
    pointer = Pointer(canvas.w, canvas.h)
    # Carts are read from SD before display init; only fall back to a (risky)
    # post-display mount if the shell didn't prefetch.
    carts, carts_root = prefetched if prefetched is not None else _load_carts()
    ws = Workstation(comp, canvas, inp, carts)
    ws.carts_root = carts_root
    # Reads work (carts were prefetched before display init). Writes are disabled
    # on-device for now: mounting SD while the panel owns the shared SPI bus hangs
    # it (see README). _with_sd stays the no-op passthrough so any stray write
    # fails gracefully instead of bricking. Re-enable once the write path releases
    # the panel (deinit display bus -> mount -> write -> unmount -> reinit).
    ws.can_manage = False
    ws.pointer = pointer
    print("KidCode desktop running (kb=%d ball=%d)"
          % (1 if keyboard.available else 0, 1 if ball.available else 0))

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
        counts, click = ball.poll()             # trackball -> move the cursor
        dx = _cursor_delta(counts[3] - counts[2])   # right - left
        dy = _cursor_delta(counts[1] - counts[0])   # down - up
        if dx or dy:
            pointer.move(dx, dy)
        pointer.click = click
        ws.handle_input()                       # keyboard W/A/S/D etc.
        ws.handle_pointer()                     # cursor hover + click
        ws.frame(dt)
        elapsed = _ticks_diff(_ticks_ms(), now)
        if elapsed < frame_ms:
            time.sleep_ms(frame_ms - elapsed)
