"""The public UI API's parts sheet (docs/look_2026-10.md step 12): a system
app drawing the Settings and Appearance targets' rows with the verbs, as the
shell's apps will. tests/test_shell_goldens.py registers it for its
`parts` configs, and tools/look_shots.py --parts draws it beside the
targets."""

from array import array

from runtime import ui, ui_draw

SWATCHES = {"light": (0x4f46e5, 0xd45f00, 0xd13f2a, 0xc2306f, 0x0a72bd, 0x0d8160,
                      0x4c8417, 0x15161d),
            "dark": (0xa79bff, 0xffc23d, 0xff7a59, 0xf2719f, 0x5cc4ff, 0x55d6a6,
                     0x9fd36a, 0xeef0ff)}


class PartsApp:
    id = "parts"
    domain = "system"
    TITLE = "Parts"

    def __init__(self, ws, page="settings"):
        self.ws = ws
        self.page = page
        self.ctx = ui_draw.ui_context()
        self.inp = array("i", [0] * ui_draw.UI_IN_LEN)
        self.first = True
        self.sound = True
        self.vol = 6
        self.size = 1
        self.tint = 0
        self.font = 0
        self.accent = 0
        self.mode = 0

    def is_app(self, cart):
        return False

    def open(self):
        self.ctx = ui_draw.ui_context()
        self.first = True

    def relayout(self, w, h, fs, cs=None):
        pass

    def handle_input(self, i):
        b = 0
        for n, k in enumerate(("left", "right", "up", "down", "a", "b", "run")):
            if i.pressed(k):
                b |= 1 << n
        self.inp[0] = self.inp[1] = b
        self.inp[5] = int(getattr(i, "last_key", 0) or 0)
        return True

    def handle_pointer(self, px, py, click):
        return True

    def draw(self, dt):
        ws = self.ws
        cv = ws.sys_canvas
        L = ws.layout                   # the shell's content rect, under its bars
        rect = (L.cx, L.cy, L.cw, L.ch)
        cv.cls(ws.theme_colors["panel"])    # the canvas is the app's, as Calc's is
        ui_draw.ui_frame(self.ctx, cv, ws.theme_colors, rect, self.inp)
        self.inp[0] = self.inp[1] = self.inp[5] = 0
        getattr(self, "_" + self.page)()
        self.first = False

    def _settings(self):
        ui.page("")
        if self.first:
            ui.focus("Appearance")
        ui.hints(("confirm", "Open", "back", "Back"))
        ui.group("General")
        ui.link("Wi-Fi", "Home")
        ui.link("Appearance", "Moy " + ("Day" if self.ws.look.theme_variant == "light" else "Night"))
        self.size = ui.cycle("Text size", ("Small", "Medium", "Large"), self.size)
        ui.group("About")
        ui.info("Firmware", "0.8")
        ui.info("Board", "T-Deck Plus")
        ui.info("Storage", "1.2 MB free")
        ui.info("Battery", "82%")
        ui.group()

    def _appearance(self):
        ui.page("")
        if self.first:
            ui.focus("Tint")
        ui.hints(("change", "Change", "back", "Back"))
        light = self.ws.look.theme_variant == "light"
        ui.group("Sound")
        self.sound = ui.toggle("Sound", self.sound)
        self.vol = ui.slider("Volume", self.vol, 0, 10)
        ui.group(" ")
        self.mode = ui.seg("Mode", ("Day", "Night"), 0 if light else 1)
        self.accent = ui.swatches("Accent", SWATCHES["light" if light else "dark"], self.accent)
        self.tint = ui.cycle("Tint", ("Cool", "Warm", "Plain"), self.tint)
        ui.link("Wallpaper", "Backdrop")
        self.font = ui.cycle("Code font", ("JetBrains Mono", "petme128"), self.font)
        ui.group()
