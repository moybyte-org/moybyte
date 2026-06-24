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
from .editors import CodeEditor, PaintEditor
from .engine import DesktopRuntime

_C = _pal.NAMES

# Code editor panel (host 480x270, 3x5 font at scale 2 -> 8px cells).
_ED_X, _ED_Y = 40, 30
_ED_CELL = 8
# Paint editor: zoomed 8x8 grid + 16-color palette (2x8) + sprite preview.
_PG_X, _PG_Y, _PG_CELL = 60, 70, 22
_PSW_X, _PSW_Y, _PSW = 260, 70, 22


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
        self.view = "cards"            # menu sub-view: "cards" | "code" | "paint"
        self.sel = 0
        self.status = "DESKTOP"
        self.last_save = None
        # The same shared editor cores the device uses (runtime.editors), so the
        # PC sim exercises identical edit logic -- only this glue + canvas differ.
        self.editor = None             # CodeEditor over cart.main_source
        self.sheet = cart.sheet        # SpriteSheet (always present on a Cartridge)
        self.paint = None              # PaintEditor over the sheet

    # back-compat: callers/tests still read `code_view` as "code panel showing".
    @property
    def code_view(self):
        return self.mode == "menu" and self.view == "code"

    # -- input ---------------------------------------------------------------

    def _set_view(self, view):
        self.view = view
        if view == "code" and self.editor is None:
            self.editor = CodeEditor(self.cart.main_source)
        elif view == "paint" and self.paint is None:
            self.paint = PaintEditor(self.sheet)

    def press(self, btn):
        if btn == "menu":
            self.mode = "menu" if self.mode == "desktop" else "desktop"
            self._set_view("cards")
            self.status = "MAKE IT MINE" if self.mode == "menu" else "DESKTOP"
            return
        if self.mode != "menu":
            return
        if btn == "code":
            self._set_view("cards" if self.view == "code" else "code")
            self.status = "EDIT CODE" if self.view == "code" else "MAKE IT MINE"
            return
        if btn == "paint":
            self._set_view("paint")
            self.status = "PAINT"
            return
        if self.view == "code":
            if btn == "run":
                self._run_code()
            elif btn == "save":
                self._save_code()
            elif btn == "left":
                self.editor.move(0, -1)
            elif btn == "right":
                self.editor.move(0, 1)
            elif btn == "up":
                self.editor.move(-1, 0)
            elif btn == "down":
                self.editor.move(1, 0)
            return
        if self.view == "paint":
            if btn == "save":
                self._save_sprites()
            elif btn == "left":
                self.paint.select(-1)
            elif btn == "right":
                self.paint.select(1)
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

    # -- editor input (typed chars + pointer clicks; same cores as the device) --

    def type_char(self, code):
        """Feed a typed ASCII byte to the code editor (no-op outside code view)."""
        if self.code_view and self.editor is not None:
            self.editor.key(code)

    def click(self, x, y):
        """Route a pointer click while a menu panel is open."""
        if self.mode != "menu":
            return
        if self.view == "code" and self.editor is not None:
            col = (int(x) - _ED_X - 4) // _ED_CELL
            row = (int(y) - _ED_Y - 28) // (_ED_CELL + 2)
            if col >= 0 and row >= 0:
                self.editor.place(col, row)
        elif self.view == "paint" and self.paint is not None:
            self._paint_click(int(x), int(y))

    def _paint_click(self, x, y):
        pe = self.paint
        if _PG_X <= x < _PG_X + 8 * _PG_CELL and _PG_Y <= y < _PG_Y + 8 * _PG_CELL:
            pe.paint((x - _PG_X) // _PG_CELL, (y - _PG_Y) // _PG_CELL)
        elif _PSW_X <= x < _PSW_X + 2 * _PSW and _PSW_Y <= y < _PSW_Y + 8 * _PSW:
            idx = ((y - _PSW_Y) // _PSW) * 2 + ((x - _PSW_X) // _PSW)
            if 0 <= idx < 16:
                pe.color = idx

    def _run_code(self):
        self.cart.main_source = self.editor.text()   # apply in RAM
        self._save_code(quiet=True)                  # persist if writable
        self.rt.load(self.cart)
        self.mode = "desktop"
        self.status = "RAN!"

    def _save_code(self, quiet=False):
        try:
            self.cart.save_main(self.editor.text())
            self.editor.dirty = False
            if not quiet:
                self.status = "SAVED CODE"
        except Exception:  # noqa: BLE001 - system carts refuse; keep running
            if not quiet:
                self.status = "CANT SAVE SYSTEM CART"

    def _save_sprites(self):
        try:
            self.cart.save_sprites()
            self.status = "SAVED SPRITES"
        except Exception:  # noqa: BLE001
            self.status = "CANT SAVE SYSTEM CART"

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
        if self.view == "code":
            self._draw_code_editor()
        elif self.view == "paint":
            self._draw_paint()
        else:
            self._draw_cards()

    def _draw_cards(self):
        cv = self.rt.canvas
        x, y, w, h = 40, 30, cv.w - 80, cv.h - 60
        cv.rect(x, y, w, h, _C["dark_purple"])
        cv.rectb(x, y, w, h, _C["pink"])
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

    def _draw_code_editor(self):
        cv = self.rt.canvas
        ed = self.editor
        x, y, w, h = _ED_X, _ED_Y, cv.w - 80, cv.h - 60
        cv.rect(x, y, w, h, _C["black"])
        cv.rectb(x, y, w, h, _C["green"])
        cv.print("EDIT CODE" + (" *" if ed is not None and ed.dirty else ""),
                 x + 6, y + 8, _C["green"], 2)
        if ed is not None:
            vis = ed.visible_lines()
            for i in range(len(vis)):
                ly = y + 28 + i * (_ED_CELL + 2)
                cv.print(vis[i][:CodeEditor.COLS], x + 4, ly, _C["light_grey"], 2)
                if ed.top + i == ed.row:        # caret on the cursor's line
                    cx = x + 4 + min(ed.col, CodeEditor.COLS) * _ED_CELL
                    cv.rect(cx, ly, 1, _ED_CELL, _C["yellow"])
        cv.print(self.status, x + 6, y + h - 22, _C["peach"], 2)

    def _draw_paint(self):
        cv = self.rt.canvas
        pe = self.paint
        x, y, w, h = _ED_X, _ED_Y, cv.w - 80, cv.h - 60
        cv.rect(x, y, w, h, _C["black"])
        cv.rectb(x, y, w, h, _C["orange"])
        cv.print("PAINT  SPR " + str(pe.n if pe else 0) + (" *" if self.sheet.dirty else ""),
                 x + 6, y + 8, _C["orange"], 2)
        if pe is None:
            return
        for ly in range(8):                      # zoomed 8x8 pixel grid
            for lx in range(8):
                gx, gy = _PG_X + lx * _PG_CELL, _PG_Y + ly * _PG_CELL
                cv.rect(gx, gy, _PG_CELL, _PG_CELL, self.sheet.tget(pe.n, lx, ly))
                cv.rectb(gx, gy, _PG_CELL, _PG_CELL, _C["dark_grey"])
        for idx in range(16):                    # 16-color palette (2x8)
            sx, sy = _PSW_X + (idx % 2) * _PSW, _PSW_Y + (idx // 2) * _PSW
            cv.rect(sx, sy, _PSW, _PSW, idx)
            cv.rectb(sx, sy, _PSW, _PSW, _C["white"] if idx == pe.color else _C["dark_grey"])
        cv.print("L/R SPRITE  SAVE  CLICK=PAINT", x + 6, y + h - 22, _C["peach"], 2)
