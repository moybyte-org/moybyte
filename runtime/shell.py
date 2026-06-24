"""DesktopShell: the interactive v0.4 fantasy-workstation shell (host).

Wraps a DesktopRuntime running a wallpaper cartridge and adds the **Make it mine**
loop: open a panel, adjust the cartridge's editable config, press Run to apply
(the wallpaper re-runs with the new values), and Save to keep it as a user
cartridge. Driven by discrete `press(button)` events so it works identically from
a live pygame window or a headless script.

The editable fields come from the cartridge manifest's `edit` schema, so the
panel is cartridge-driven, not hard-coded -- the seed of the v0.4 "cards" editor.
"""

import os

from . import palette as _pal
from .engine import DesktopRuntime

_C = _pal.NAMES


def _label_value(field, value):
    if field["type"] == "choice":
        return str(value).replace("_", " ").upper()
    return str(value)


class DesktopShell:
    def __init__(self, cart, save_dir=None):
        self.cart = cart
        self.rt = DesktopRuntime()
        self.rt.load(cart)
        self.edit = cart.manifest.get("edit", [])
        self.save_dir = save_dir
        self.mode = "desktop"          # "desktop" | "menu"
        self.sel = 0
        self.status = "DESKTOP"
        self.last_save = None
        self.code_view = False         # menu: cards vs "see the code"

    # -- input ---------------------------------------------------------------

    def press(self, btn):
        if btn == "menu":
            self.mode = "menu" if self.mode == "desktop" else "desktop"
            self.code_view = False
            self.status = "MAKE IT MINE" if self.mode == "menu" else "DESKTOP"
            return
        if self.mode != "menu":
            return
        if btn == "code":
            self.code_view = not self.code_view
            self.status = "THE CODE" if self.code_view else "MAKE IT MINE"
            return
        if not self.edit:
            return
        if btn == "up":
            self.sel = (self.sel - 1) % len(self.edit)
        elif btn == "down":
            self.sel = (self.sel + 1) % len(self.edit)
        elif btn == "left":
            self._adjust(-1)
        elif btn == "right":
            self._adjust(1)
        elif btn == "run":
            self._apply()
        elif btn == "save":
            self._save()

    def _adjust(self, d):
        field = self.edit[self.sel]
        key = field["key"]
        cur = self.cart.config.get(key, field.get("default"))
        if field["type"] == "int":
            step = field.get("step", 1)
            val = int(cur) + d * step
            if "min" in field:
                val = max(field["min"], val)
            if "max" in field:
                val = min(field["max"], val)
            self.cart.config[key] = val
        elif field["type"] == "choice":
            choices = field["choices"]
            idx = choices.index(cur) if cur in choices else 0
            self.cart.config[key] = choices[(idx + d) % len(choices)]
        self.status = "EDITED - PRESS RUN"

    def _apply(self):
        self.rt.load(self.cart)        # re-run the cartridge with new config
        self.mode = "desktop"
        self.status = "RAN!"

    def _save(self):
        if self.cart.system:
            if not self.save_dir:
                self.status = "NO SAVE DIR"
                return
            os.makedirs(self.save_dir, exist_ok=True)
            slug = self.cart.title.lower().replace(" ", "_")
            dest = os.path.join(self.save_dir, slug + ".kcart")
            n = 1
            while os.path.exists(dest):
                n += 1
                dest = os.path.join(self.save_dir, "%s_%d.kcart" % (slug, n))
            dup = self.cart.duplicate(dest, new_title="My " + self.cart.title)
            dup.save_config(self.cart.config)
            self.cart = dup            # subsequent saves edit the user copy
            self.last_save = dest
        else:
            self.cart.save_config()
            self.last_save = self.cart.path
        self.status = "SAVED"

    # -- frame ---------------------------------------------------------------

    def frame(self, dt):
        if self.mode == "desktop":
            self.rt.step(dt)
        else:
            self.rt.step(0.0)          # keep the wallpaper drawn, frozen
            self._draw_panel()

    def rgb888(self):
        return self.rt.canvas.to_rgb888()

    def card_text(self, i):
        """The natural-language card for editable field i (no typing required)."""
        field = self.edit[i]
        value = _label_value(field, self.cart.config.get(field["key"], field.get("default")))
        tmpl = field.get("card")
        if tmpl:
            return tmpl.replace("{value}", str(value))
        return "%s: %s" % (field.get("label", field["key"]), value)

    def code_lines(self):
        """"See the code": the data behind the desktop as readable assignments."""
        lines = ["CARTRIDGE = " + self.cart.title.upper(), "WHEN START:"]
        for field in self.edit:
            val = _label_value(field, self.cart.config.get(field["key"], field.get("default")))
            lines.append("  " + field["key"].upper() + " = " + str(val))
        return lines

    def _draw_panel(self):
        if self.code_view:
            self._draw_code()
        else:
            self._draw_cards()

    def _draw_cards(self):
        cv = self.rt.canvas
        x, y, w, h = 40, 30, cv.w - 80, cv.h - 60
        cv.rectfill(x, y, w, h, _C["dark_purple"])
        cv.rect(x, y, w, h, _C["pink"])
        cv.print("MAKE IT MINE", x + 16, y + 12, _C["white"], 3)
        row_y = y + 50
        for i in range(len(self.edit)):
            sel = (i == self.sel)
            fg = _C["yellow"] if sel else _C["light_grey"]
            if sel:
                cv.print(">", x + 8, row_y, _C["yellow"], 2)
            cv.print(self.card_text(i), x + 26, row_y, fg, 2)
            row_y += 24
        cv.print(self.status, x + 16, y + h - 42, _C["green"], 2)
        cv.print("L/R CHANGE  RUN APPLY  CODE SEE  SAVE", x + 16, y + h - 22, _C["peach"], 2)

    def _draw_code(self):
        cv = self.rt.canvas
        x, y, w, h = 40, 30, cv.w - 80, cv.h - 60
        cv.rectfill(x, y, w, h, _C["black"])
        cv.rect(x, y, w, h, _C["green"])
        cv.print("SEE THE CODE", x + 16, y + 12, _C["green"], 3)
        ly = y + 48
        for ln in self.code_lines():
            cv.print(ln, x + 16, ly, _C["light_grey"], 2)
            ly += 22
        cv.print("CODE BACK TO CARDS   RUN APPLY", x + 16, y + h - 22, _C["peach"], 2)
