#!/usr/bin/env python3
# Map (grep -n a name to jump there):
#   Sheet               the state sheet: one surface a page, drawn with the ui verbs
#   _sheet_setup        a sheet frame's setup (SURFACES lists each page's frames)
#   _cc                 the control center opened (SHELL lists the shell's screens)
#   _proposal_theme     the proposed grammar as a seed's package (PROPOSAL's data)
#   _overlay            what the proposal draws that the engine does not yet
#   _cc_inset           the control center's inset, as the proposal draws it
#   shoot               one run's renders
#   sheets              the contact sheets
#   findings            the README's numbered crops (FINDINGS)
#   main                the command
"""The focus and selection audit (docs/media/focus_2026-11/README.md): every
surface that wears a focus or a selected mark, in every state, in every
theme-mode the shell holds, drawn by the real toolkit on the goldens'
workstation (`tests/test_shell_goldens.py`, through `tools/look_shots.py`'s
`render`), written as PNGs.

usage: .venv/bin/python tools/focus_shots.py OUT [FILTER ...] [--work DIR]
                                             [--runs today,proposal,findings]

Two kinds of render:

- the STATE SHEET: an app drawing one surface a page with the public UI
  API's verbs (a group card, a plain list, a table, a menu, buttons, a
  dialog, tabs and segments, cards and tools, fields), each item labelled
  with its state, drawn once per state -- the focus elsewhere, a stop
  focused, a selected stop focused, a press held, the same under touch --
  on the T-Deck (320x240) and in a window on the desk (1024x600);
- the SHELL: the real screens (Settings, the home and its bar, the system
  menu, the control center, Files, Calc, the desk's PROJECTS window, the
  delete's question), each with a stop focused.

Two runs draw them: `today` as dev draws them, and `proposal` with the
proposed grammar -- each theme's focus and current marks as look data
(`PROPOSAL`, installed as a store package over the seed) and the engine
changes the data cannot say drawn over the render by `_overlay` and
`_cc_inset` the way the build is to draw them. Each run writes OUT/RUN/
surface_SURFACE_{tdeck,desk,shell}.png (one surface across the
theme-modes) and theme_THEME-MODE_{tdeck,shell}.png (one theme-mode's
surfaces), every cell captioned with its theme-mode, surface and state;
`findings` crops FINDINGS' panels from both runs into OUT/findings/. The
single frames and whole renders stay in --work (a temporary directory by
default).
"""

import argparse
import copy
import fnmatch
import json
import os
import sys
import tempfile
from array import array

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))

import look_shots as L  # noqa: E402  -- the review kit's renderer

MODES = L.KIT_MODES
SEEDS = os.path.join(ROOT, "native", "moy_ui", "themes")

# --------------------------------------------------------------------------
# the state sheet
# --------------------------------------------------------------------------

FILES = ("Beach trip", "Road trip mix", "Birthday song", "Dragon theme", "Sleepy time")
COLS = ("Name", "Size")
ROWS = (("Beach trip", "3 KB"), ("Road trip mix", "12 KB"), ("Birthday song", "2 KB"),
        ("Dragon theme", "4 KB"))
MENU = ("Resume", "Restart", "Settings", "Delete cart")

_BTN = {"left": 1, "right": 2, "up": 4, "down": 8, "a": 16, "b": 32}


class Sheet:
    """A system app drawing one surface of the sheet (its `page`), each item
    labelled with the state it is drawn in. `keys` are the buttons pressed
    one a frame once focus is placed; `press` the stop a pointer holds."""
    id = "focus_sheet"
    domain = "system"
    TITLE = "Focus"

    def __init__(self, ws, page):
        from runtime import ui_draw
        self.ws = ws
        self.page = page
        self.ctx = ui_draw.ui_context()
        self.inp = array("i", [0] * ui_draw.UI_IN_LEN)
        self.keys = []
        self.press = None
        self.pressing = 0
        self.foot = 0
        self.vals = {"tabs": 1, "seg": 0, "list": 1, "table": 1, "toggle": True,
                     "field": "", "named": "Sky Run", "bad": "http"}

    def is_app(self, cart):
        return False

    def open(self):
        from runtime import ui_draw
        self.ctx = ui_draw.ui_context()

    def relayout(self, w, h, fs, cs=None):
        pass

    def handle_input(self, i):
        return True

    def handle_pointer(self, px, py, click):
        return True

    def _script(self):
        from runtime import ui_draw
        inp = self.inp
        for k in range(len(inp)):
            inp[k] = 0
        if self.keys:
            b = _BTN[self.keys.pop(0)]
            inp[0] = inp[1] = b
            self.ws._dirty = True
        elif self.press is not None:
            box = ui_draw._C.api_box(self.ctx, self.press)
            if box is not None:
                inp[2] = box[0] + box[2] // 2
                inp[3] = box[1] + box[3] // 2
                inp[4] = 1 | 2 | (4 if self.pressing == 0 else 0)
                self.pressing += 1

    def draw(self, dt):
        from runtime import ui, ui_draw
        ws = self.ws
        cv = ws.sys_canvas
        lay = ws.layout
        rect = (lay.cx, lay.cy, lay.cw, lay.ch)
        cv.cls(ws.theme_colors["panel"])
        self._script()
        ui_draw.ui_frame(self.ctx, cv, ws.theme_colors, rect, self.inp)
        ui.page("")
        last = ui_draw._C.api_box(self.ctx, LAST.get(self.page, ""))
        self.foot = rect[1] + 120 * lay.fs if last is None else last[1] + last[3]
        getattr(self, "_" + self.page)(ui, rect)
        if self.page not in ("dialog", "menu"):
            fs = lay.fs
            ui.at(rect[0] + rect[2] - 62 * fs, self.foot + 6 * fs, 54 * fs, 20 * fs)
            ui.button("Other")

    # each page: one surface, every item named by its state

    def _group(self, ui, rect):
        ui.hints(("confirm", "Open", "back", "Back"))
        ui.group("Group card")
        ui.link("Rest", "Home")
        ui.link("Selected", "Night", {"current": True})
        ui.link("Disabled", "Off", {"disabled": True})
        self.vals["toggle"] = ui.toggle("Toggle", self.vals["toggle"])
        ui.slider("Slider", 3, 0, 7, {"steps": True})
        ui.group()
        from runtime import ui_draw
        BOXES["Selected"] = ui_draw._C.api_box(self.ctx, "Selected") or BOXES.get("Selected")

    def _list(self, ui, rect):
        ui.hints(("confirm", "Open", "back", "Back"))
        self.vals["list"] = ui.list("files", FILES, self.vals["list"])

    def _table(self, ui, rect):
        ui.hints(("confirm", "Open", "back", "Back"))
        got = ui.table("", COLS, ROWS, self.vals["table"])
        self.vals["table"] = got[0]

    def _menu(self, ui, rect):
        ui.hints(("confirm", "Choose", "back", "Back"))
        ui.menu("", MENU, ("play", "undo", "gear", "trash"), None,
                ("", "", "", "danger"))

    def _buttons(self, ui, rect):
        ui.hints(("confirm", "Press", "back", "Back"))
        x, y, w, _h = rect
        fs = self.ws.layout.fs
        bw, bh, gap = 70 * fs, 24 * fs, 6 * fs
        x0, y0 = x + 8 * fs, y + 10 * fs
        names = (("Normal", "normal", None), ("Primary", "primary", None),
                 ("Danger", "danger", None), ("Disabled", "normal", {"disabled": True}),
                 ("Tonal", "tonal", None), ("Toggle on", "toggle", {"current": True}))
        for i, (label, kind, o) in enumerate(names):
            ui.at(x0 + (i % 4) * (bw + gap), y0 + (i // 4) * (bh + 3 * gap), bw, bh)
            ui.button(label, kind, o)
        ui.at(x0, y0 + 2 * (bh + 3 * gap), 3 * bw, bh)
        ui.button("Wide button")

    def _dialog(self, ui, rect):
        ui.hints(("confirm", "Choose", "back", "Back"))
        ui.label("The page under the dialog")
        ui.dialog("Delete Sky Run?", ("Cancel", "Delete"), "danger")
        ui.label("This can't be undone.", {"wrap": True})
        ui.dialog()

    def _tabs(self, ui, rect):
        ui.hints(("change", "Change", "back", "Back"))
        self.vals["tabs"] = ui.tabs("tabs", ("Games", "Make", "Art"), self.vals["tabs"])
        ui.label(" ")
        ui.group("Segments")
        self.vals["seg"] = ui.seg("Mode", ("Day", "Night"), self.vals["seg"])
        ui.group()

    def _cells(self, ui, rect):
        ui.hints(("confirm", "Open", "back", "Back"))
        fs = self.ws.layout.fs
        ui.grid("cards", 56 * fs, 40 * fs)
        for name, o in (("Rest", {"icon": "play"}), ("Selected", {"icon": "paint",
                                                                   "current": True}),
                        ("Locked", {"icon": "calc", "locked": True})):
            ui.card(name, "", o)
        ui.grid()
        x, y, w, h = rect
        tx, ty = x + 8 * fs, y + 84 * fs
        for i, (icon, label, o) in enumerate((("undo", "Undo", None),
                                              ("pencil", "Pen", {"current": True}),
                                              ("redo", "Redo", {"disabled": True}))):
            ui.at(tx + i * 30 * fs, ty, 26 * fs, 26 * fs)
            ui.tool(icon, label, o)

    def _fields(self, ui, rect):
        ui.hints(("back", "Back",))
        ui.group("Fields")
        self.vals["field"] = ui.field("Name", self.vals["field"], 16,
                                      {"hint": "Your name"})[0]
        self.vals["named"] = ui.field("Title", self.vals["named"], 16)[0]
        self.vals["bad"] = ui.field("Address", self.vals["bad"], 32,
                                    {"border": "danger"})[0]
        ui.group()


# each page's last stop: the "Other" stop (the focus a rest frame holds)
# stands under it
LAST = {"group": "Slider", "list": "files", "table": "", "buttons": "Wide button",
        "tabs": "Mode", "cells": "Redo", "fields": "Address"}

# where the desk's sheet window stands, in glass pixels
DESK_WIN = (112, 52, 440, 250)


# the boxes the last sheet frame drew its marked stops in (the overlay's)
BOXES = {}


# surface -> [(state, focus stop or None, keys, press stop, input)]
# A state's name says what the frame shows; an item's label says its own.
SURFACES = {
    "group": [("rest", "Other", (), None, None), ("focus-rest", "Rest", (), None, None),
              ("focus-selected", "Selected", (), None, None),
              ("focus-toggle", "Toggle", (), None, None),
              ("focus-slider", "Slider", (), None, None),
              ("pressed", None, (), "Rest", "touch"),
              ("touch-focus", "Rest", (), None, "touch")],
    "list": [("rest", "Other", (), None, None), ("focus-selected", "files", (), None, None),
             ("focus-rest", "files", ("down",), None, None),
             ("touch-focus", "files", ("down",), None, "touch")],
    "table": [("rest", "Other", (), None, None), ("focus-selected", "", (), None, None),
              ("focus-down", "", ("down",), None, None)],
    "menu": [("focus-first", "Resume", (), None, None),
             ("focus-danger", "Delete cart", (), None, None),
             ("touch", "Resume", (), None, "touch")],
    "buttons": [("rest", "Other", (), None, None), ("focus-normal", "Normal", (), None, None),
                ("focus-primary", "Primary", (), None, None),
                ("focus-danger", "Danger", (), None, None),
                ("focus-toggle-on", "Toggle on", (), None, None),
                ("focus-wide", "Wide button", (), None, None),
                ("pressed", None, (), "Normal", "touch")],
    "dialog": [("focus-cancel", "Cancel", (), None, None),
               ("focus-verb", "Cancel", ("right",), None, None)],
    "tabs": [("rest", "Other", (), None, None), ("focus-tabs", "tabs", (), None, None),
             ("focus-seg", "Mode", (), None, None)],
    "cells": [("rest", "Other", (), None, None), ("focus-rest", "Rest", (), None, None),
              ("focus-selected", "Selected", (), None, None),
              ("focus-tool", "Undo", (), None, None),
              ("focus-tool-selected", "Pen", (), None, None)],
    "fields": [("rest", "Other", (), None, None), ("focus-empty", "Name", (), None, None),
               ("focus-filled", "Title", (), None, None),
               ("focus-error", "Address", (), None, None)],
}

SHEET_CONFIGS = (("tdeck", L.HANDHELD), ("desk", L.DESK))


def _sheet_setup(page, focus, keys, press):
    def setup(g, ws):
        from runtime import ui_draw
        app = Sheet(ws, page)
        ws.register_app(app)
        app.open()
        ws.wm.goto(app.id)
        L._settle(g, ws, 2)
        if app.id in getattr(ws.wm, "_wins", {}):
            L._place(ws, app.id, *DESK_WIN)
            L._settle(g, ws, 2)
        ui_draw._C.api_focus(app.ctx, focus)
        app.keys = list(keys)
        app.press = press
        if keys or press:
            for _ in range(len(keys) + 3):
                L._settle(g, ws, 1)
    return setup



# --------------------------------------------------------------------------
# the real shell's screens
# --------------------------------------------------------------------------

def _open(app_id, then=None):
    def setup(g, ws):
        ws.open_app(ws._apps_by_id[app_id])
        L._settle(g, ws, 2)
        if then is not None:
            then(g, ws)
    return setup


def _cc(focus=None, touch=False):
    """The handheld's control center, opened from the bar's cluster, `focus`
    its stop focused (None: where it opens)."""
    def setup(g, ws):
        L._home(g, ws)
        L._settle(g, ws, 2)
        ws._golden_popover = True
        ws.widget_tap("battery")
        L._settle(g, ws, 2)
        if focus is not None:
            ws.sysmenu.comp.host.focus(focus)
    return setup


def _bar_focus(name):
    """The home with the bar's stop `name` focused (the cluster, a tab)."""
    def setup(g, ws):
        L._home(g, ws)
        L._settle(g, ws, 2)
        items = ws.bar_layer.focus_items("home")
        names = [i[2] for i in items]
        k = names.index(name) if name in names else 0
        ws.bar_layer.focus_enter("home", "row", k=k)
    return setup


def _desk_power(g, ws):
    ws.open_desk()
    L._settle(g, ws, 2)
    ws._golden_popover = True
    ws.widget_tap("battery")


# surface -> [(state, config, setup)]
SHELL = {
    "group": [("settings-volume", L.HANDHELD, L._settings_at("volume")),
              ("settings-toggle", L.HANDHELD, L._settings_at("steady")),
              ("desk-appearance-seg", L.DESK, L._desk_appearance_at("Mode"))],
    "control-center": [("open", L.HANDHELD, _cc()),
                       ("focus-brightness", L.HANDHELD, _cc("Brightness")),
                       ("focus-wifi", L.HANDHELD, _cc("Wi-Fi"))],
    "list": [("files", L.HANDHELD, _open("files")),
             ("desk-files-calc", L.DESK, L._desk)],
    "menu": [("sysmenu", L.HANDHELD, L._sysmenu_home),
             ("pause", L.HANDHELD, L._pause),
             ("desk-sysmenu", L.DESK, L._sysmenu_desk),
             ("desk-power", L.DESK, _desk_power)],
    "dialog": [("delete", L.HANDHELD, L._delete_asks(L._make_own)),
               ("desk-delete", L.DESK, L._delete_asks(L._projects_own(), True))],
    "bar": [("focus-cluster", L.HANDHELD, _bar_focus("cluster")),
            ("focus-menu-button", L.HANDHELD, _bar_focus("menu"))],
    "cells": [("home-tiles", L.HANDHELD, L._home),
              ("calc", L.HANDHELD, _open("calc", L._key_calc)),
              ("desk-projects", L.DESK, L._projects())],
    "fields": [("settings-name", L.HANDHELD, L._settings_at("name"))],
}


# --------------------------------------------------------------------------
# rendering, cropping, the sheets
# --------------------------------------------------------------------------

def _ground(img, box):
    """The commonest colour in `box` of `img`: the page's ground."""
    crop = img.crop(box)
    return max(crop.getcolors(1 << 20), key=lambda c: c[0])[1]


def _bbox(imgs, box, pad):
    """The union of what differs from the ground in `box` over `imgs`."""
    from PIL import ImageChops, Image
    x0, y0, x1, y1 = box
    out = None
    for im in imgs:
        crop = im.crop(box)
        g = Image.new("RGB", crop.size, _ground(im, box))
        bb = ImageChops.difference(crop, g).convert("L").point(lambda v: 255 if v > 6 else 0) \
            .getbbox()
        if bb is None:
            continue
        out = bb if out is None else (min(out[0], bb[0]), min(out[1], bb[1]),
                                      max(out[2], bb[2]), max(out[3], bb[3]))
    if out is None:
        return box
    return (max(x0, x0 + out[0] - pad), max(y0, y0 + out[1] - pad),
            min(x1, x0 + out[2] + pad), min(y1, y0 + out[3] + pad))


def _content(ws_size, config):
    w, h = ws_size
    if config == L.HANDHELD:
        return (0, 22, w, h - 24)
    x, y, ww, hh = DESK_WIN
    return (x - 8, y - 8, x + ww + 8, y + hh + 8)


# every render whole, in glass pixels (the findings crop from these)
FULL = {}

# The findings (README): id, title, panels (run, (theme-mode, config tag,
# surface, state), crop in glass pixels or None, scale).
_TMS = ["%s-%s" % m for m in MODES]
_CARD = (0, 42, 320, 152)
_CC = (80, 22, 320, 150)


def _across(run, tag, surface, state, crop, scale, tms=None):
    return [(run, (tm, tag, surface, state), crop, scale) for tm in (tms or _TMS)]


FINDINGS = [
    ("01_focus-is-a-fill-in-two-themes",
     "1. One focused row, three marks: Moy draws a bar, Blocks and Contrast fill the row",
     _across("today", "tdeck", "group", "focus-rest", _CARD, 2)),
    ("02_focus-and-selection-swap-meaning",
     "2. A fill means SELECTED in Moy and FOCUSED in Blocks/Contrast; a table row, "
     "focused and selected",
     _across("today", "tdeck", "list", "focus-rest", (0, 40, 320, 140), 2)),
    ("03_selected-group-row-draws-nothing",
     "3. A group row given `current` draws no mark in any theme ('Selected' == 'Rest')",
     _across("today", "tdeck", "group", "rest", _CARD, 2,
             ["moy-dark", "blocks-light", "contrast-dark"])
     + _across("proposal", "tdeck", "group", "rest", _CARD, 2,
               ["moy-dark", "blocks-light", "contrast-dark"])),
    ("04_rules-vanish-beside-the-bar",
     "4. The rules above and below a focused row vanish (the old plate's leftover); "
     "the bar sits on the card's frame",
     [("today", ("moy-dark", "shell-tdeck", "control-center", "focus-brightness"), _CC, 3),
      ("proposal", ("moy-dark", "shell-tdeck", "control-center", "focus-brightness"), _CC, 3),
      ("today", ("moy-light", "tdeck", "group", "focus-selected"), (0, 52, 170, 112), 4),
      ("proposal", ("moy-light", "tdeck", "group", "focus-selected"), (0, 52, 170, 112), 4)]),
    ("05_control-center-flush-top",
     "5. The control center's card sits flush on the panel's top, with an empty foot",
     [("today", ("moy-dark", "shell-tdeck", "control-center", "open"), _CC, 2),
      ("proposal", ("moy-dark", "shell-tdeck", "control-center", "open"), _CC, 2),
      ("today", ("moy-light", "shell-tdeck", "control-center", "open"), _CC, 2),
      ("proposal", ("moy-light", "shell-tdeck", "control-center", "open"), _CC, 2)]),
    ("06_the-shape-decides-the-mark",
     "6. The mark follows the box's shape, not the part: a focused tab strip wears a row's "
     "bar (Moy) or a filled strip",
     _across("today", "tdeck", "tabs", "focus-tabs", (0, 22, 320, 70), 2)),
    ("07_focus-hides-the-part",
     "7. A focus that fills hides what the part is: the danger verb loses its red, a "
     "selected tool its mark",
     _across("today", "tdeck", "dialog", "focus-verb", (0, 70, 320, 180), 2,
             ["moy-dark", "blocks-light", "contrast-dark", "contrast-light"])),
    ("08_a-field-wears-two-marks",
     "8. A focused field wears two marks: the row's bar and an accent box",
     [("today", ("moy-dark", "tdeck", "fields", "focus-filled"), (0, 42, 320, 120), 3),
      ("proposal", ("moy-dark", "tdeck", "fields", "focus-filled"), (0, 42, 320, 120), 3)]),
    ("09_pressed-looks-like-primary-or-focus",
     "9. Pressed fills a button with the accent: it reads as Primary (Moy, Blocks) or as "
     "focus (Contrast)",
     _across("today", "tdeck", "buttons", "pressed", (0, 22, 320, 80), 2,
             ["moy-dark", "blocks-light", "contrast-dark"])),
    ("10_cards-lift-in-moy-ring-elsewhere",
     "10. A focused card lifts in Moy and rings in Blocks and Contrast; every other cell "
     "rings in Moy",
     _across("today", "tdeck", "cells", "focus-rest", (0, 22, 320, 130), 2,
             ["moy-dark", "blocks-light", "contrast-dark"])),
    ("11_the-proposal-in-every-theme",
     "11. The proposal: one focused row (bar), one selected row (fill), rules kept, "
     "in every theme-mode",
     _across("proposal", "tdeck", "group", "focus-rest", _CARD, 2)),
    ("12_the-proposal-on-cells",
     "12. The proposal on cells: the ring outside, the part's own fill and ink kept",
     _across("proposal", "tdeck", "dialog", "focus-verb", (0, 70, 320, 180), 2,
             ["moy-dark", "blocks-light", "contrast-dark", "contrast-light"])
     + _across("proposal", "tdeck", "tabs", "focus-tabs", (0, 22, 320, 70), 2,
               ["moy-light", "blocks-light", "contrast-dark", "contrast-light"])),
]


def findings(work, out):
    """Each FINDINGS entry as one captioned image: its panels cropped from
    the whole renders of `work`'s runs, each labelled."""
    from PIL import Image, ImageDraw
    os.makedirs(out, exist_ok=True)
    for fid, title, panels in FINDINGS:
        cells = []
        for run, key, crop, scale in panels:
            path = os.path.join(work, run, "full", "%s_%s_%s_%s.png" % key)
            if not os.path.exists(path):
                continue
            im = Image.open(path).convert("RGB")
            if crop is not None:
                im = im.crop(crop)
            im = im.resize((im.width * scale, im.height * scale), Image.NEAREST)
            cells.append(("%s: %s, %s, %s" % (run.upper(), key[0], key[2], key[3]), im))
        if not cells:
            continue
        f, hf = _font(14), _font(18)
        gap = 12
        cols = min(len(cells), 3 if cells[0][1].width < 700 else 2)
        rows = [cells[i:i + cols] for i in range(0, len(cells), cols)]
        W = max(sum(im.width + gap for _c, im in r) for r in rows) + gap
        H = 44 + sum(max(im.height for _c, im in r) + 24 + gap for r in rows)
        sheet = Image.new("RGB", (max(W, 700), H), (236, 236, 236))
        d = ImageDraw.Draw(sheet)
        d.text((gap, 10), title, fill=(0, 0, 0), font=hf)
        y = 44
        for r in rows:
            x = gap
            for cap, im in r:
                d.text((x, y), cap, fill=(0, 0, 0), font=f)
                sheet.paste(im, (x, y + 20))
                x += im.width + gap
            y += max(im.height for _c, im in r) + 24 + gap
        _save(sheet, os.path.join(out, fid + ".png"))


def shoot(out, filters=(), proposal=False, shell=True, sheets=True):
    g = L._goldens()
    frames = {}         # (tm, config tag, surface, state) -> PIL image (cropped)
    FULL.clear()
    _cc_inset(proposal)
    for theme, mode in MODES:
        tm = "%s-%s" % (theme, mode)
        name = _proposal_theme(theme) if proposal else theme
        if sheets:
            for surface, states in SURFACES.items():
                if filters and not any(fnmatch.fnmatch("%s_%s" % (tm, surface), f)
                                       or f in "%s_%s" % (tm, surface) for f in filters):
                    continue
                for tag, config in SHEET_CONFIGS:
                    got = []
                    for state, focus, keys, press, inputs in states:
                        img, _ok, _r = L.render(g, config, name, mode,
                                                _sheet_setup(surface, focus, keys, press),
                                                False, inputs)
                        if proposal:
                            img = _overlay(img, surface, state, theme, mode)
                        got.append((state, img))
                    box = _content(got[0][1].size, config)
                    if surface != "dialog" and config == L.HANDHELD:
                        box = _bbox([i for _s, i in got], box, 6)
                    for state, img in got:
                        frames[(tm, tag, surface, state)] = img.crop(box)
                        FULL[(tm, tag, surface, state)] = img
        if shell:
            for surface, scenes in SHELL.items():
                for state, config, setup in scenes:
                    key = "%s_%s_%s" % (tm, surface, state)
                    if filters and not any(fnmatch.fnmatch(key, f) or f in key
                                           for f in filters):
                        continue
                    BOXES.clear()
                    img, _ok, _r = L.render(g, config, name, mode, setup, False)
                    if proposal:
                        img = _overlay(img, surface, state, theme, mode)
                    tag = "tdeck" if config == L.HANDHELD else "desk"
                    frames[(tm, "shell-" + tag, surface, state)] = img
                    FULL[(tm, "shell-" + tag, surface, state)] = img
    _write(out, frames)
    return frames


def _scale(tag):
    return 3 if tag == "tdeck" else 2 if tag == "shell-tdeck" else 1


def _write(out, frames):
    """Each frame as THEME-MODE_[shell-]CONFIG_SURFACE_STATE.png, the T-Deck's
    scaled up nearest."""
    from PIL import Image
    fdir = os.path.join(out, "frames")
    os.makedirs(fdir, exist_ok=True)
    full = os.path.join(out, "full")
    os.makedirs(full, exist_ok=True)
    for (tm, tag, surface, state), img in frames.items():
        s = _scale(tag)
        im = img.resize((img.width * s, img.height * s), Image.NEAREST) if s > 1 else img
        name = "%s_%s_%s_%s.png" % (tm, tag, surface, state)
        im.save(os.path.join(fdir, name))
        FULL[(tm, tag, surface, state)].save(os.path.join(full, name))


def _save(img, path):
    """A sheet as a 256-colour PNG (flat chrome and its type's ramps hold in
    256; the sheets stay reviewable at a quarter of the bytes)."""
    from PIL import Image
    img.convert("RGB").quantize(colors=256, method=Image.Quantize.FASTOCTREE,
                                dither=Image.Dither.NONE).save(path, optimize=True)


def _font(size):
    from PIL import ImageFont
    for p in ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
              "/usr/share/fonts/TTF/DejaVuSans.ttf"):
        if os.path.exists(p):
            return ImageFont.truetype(p, size)
    return ImageFont.load_default()


def _grid(cells, cols, title, scale, gap=10):
    """A captioned grid: `cells` [(caption, image)], `cols` a row; None
    caption-only cells start a new row with their caption as a heading."""
    from PIL import Image, ImageDraw
    f, hf = _font(13), _font(20)
    rows, row = [], []
    for c in cells:
        if c[1] is None:
            if row:
                rows.append(row)
            rows.append(c[0])
            row = []
            continue
        row.append(c)
        if len(row) == cols:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    ims = [[(cap, im.resize((im.width * scale, im.height * scale), Image.NEAREST))
            for cap, im in r] if not isinstance(r, str) else r for r in rows]
    W = max([sum(i.width + gap for _c, i in r) for r in ims if not isinstance(r, str)] + [600])
    H = 40 + sum(30 if isinstance(r, str) else max(i.height for _c, i in r) + 22 + gap
                 for r in ims)
    sheet = Image.new("RGB", (W + gap, H), (236, 236, 236))
    d = ImageDraw.Draw(sheet)
    d.text((gap, 8), title, fill=(0, 0, 0), font=hf)
    y = 40
    for r in ims:
        if isinstance(r, str):
            d.text((gap, y + 6), r, fill=(30, 30, 160), font=_font(16))
            y += 30
            continue
        x = gap
        for cap, im in r:
            d.text((x, y), cap, fill=(0, 0, 0), font=f)
            sheet.paste(im, (x, y + 18))
            x += im.width + gap
        y += max(i.height for _c, i in r) + 22 + gap
    return sheet


def sheets(out, frames, label):
    """A sheet per theme-mode (its T-Deck state sheet, its shell screens) and
    one per surface across the theme-modes (the T-Deck's and the desk's state
    sheets, the shell's screens)."""
    sdir = out
    os.makedirs(sdir, exist_ok=True)
    tms = ["%s-%s" % m for m in MODES]
    surfaces = list(SURFACES) + [s for s in SHELL if s not in SURFACES]
    for tm in tms:
        for tag, scale in (("tdeck", 2),):
            cells = []
            for surface in SURFACES:
                st = [(s, im) for (t, c, su, s), im in frames.items()
                      if t == tm and c == tag and su == surface]
                if st:
                    cells.append(("%s -- %s" % (surface, tag), None))
                    cells += [("%s %s %s" % (tm, surface, s), im) for s, im in st]
            if cells:
                _save(_grid(cells, 7, "%s: %s, the state sheet on the %s" % (label, tm, tag),
                            scale), os.path.join(sdir, "theme_%s_%s.png" % (tm, tag)))
        cells = []
        for surface in SHELL:
            st = [(s, c, im) for (t, c, su, s), im in frames.items()
                  if t == tm and c.startswith("shell") and su == surface]
            if st:
                cells.append(("%s -- the shell" % surface, None))
                cells += [("%s %s %s" % (tm, surface, s), im) for s, c, im in st]
        if cells:
            _save(_grid(cells, 4, "%s: %s, the shell's screens (1x)" % (label, tm), 1),
                  os.path.join(sdir, "theme_%s_shell.png" % tm))
    for surface in surfaces:
        for tag, scale in (("tdeck", 2), ("desk", 2), ("shell", 1)):
            cells = []
            for tm in tms:
                st = [(s, im) for (t, c, su, s), im in frames.items()
                      if t == tm and su == surface and (c == tag or (tag == "shell"
                                                                     and c.startswith("shell")))]
                if st:
                    cells.append((tm, None))
                    cells += [("%s %s %s" % (tm, surface, s), im) for s, im in st]
            if cells:
                _save(_grid(cells, 7 if tag != "shell" else 4,
                            "%s: %s on the %s, every theme-mode" % (label, surface, tag), scale),
                      os.path.join(sdir, "surface_%s_%s.png" % (surface, tag)))


# --------------------------------------------------------------------------
# the proposal
# --------------------------------------------------------------------------

#: The proposed grammar as each seed's look data (README, "The package
#: entries"): every theme's focus is the row's bar and the cell's ring, its
#: card a ring too; its selection a fill.
PROPOSAL = {
    "moy": {"look": {"focus": {"style": "edge", "card": "ring", "small": "edge",
                               "ring": {"w": 2, "gap": 2}, "edge": {"w": 4}}}},
    "blocks": {"look": {"focus": {"style": "edge", "card": "ring", "small": "edge",
                                  "ring": {"w": 3, "gap": 2}, "edge": {"w": 4}},
                        "current": {"style": "fill", "row": "plate"}}},
    "contrast": {"look": {"focus": {"style": "edge", "card": "ring", "small": "edge",
                                    "ring": {"w": 3, "gap": 2}, "edge": {"w": 4}}}},
}

_PROP_DIRS = {}


def _merge(a, b):
    for k, v in b.items():
        if isinstance(v, dict) and isinstance(a.get(k), dict):
            _merge(a[k], v)
        else:
            a[k] = copy.deepcopy(v)


def _proposal_package(theme):
    """The seed with PROPOSAL's entries over it, as a store package's
    theme.json text, named THEMEp."""
    with open(os.path.join(SEEDS, theme + ".json")) as f:
        d = json.load(f)
    d["name"] = theme + "p"
    _merge(d, PROPOSAL[theme])
    return d


def _proposal_theme(theme):
    """The store package the proposal draws `theme` with (installed by
    `render` from its directory, as a kid's theme is)."""
    name = theme + "p"
    if name not in _PROP_DIRS:
        d = tempfile.mkdtemp(prefix="focus_shots_")
        with open(os.path.join(d, "theme.json"), "w") as f:
            json.dump(_proposal_package(theme), f)
        _PROP_DIRS[name] = d
        L.STORE_THEMES[name] = d
    return name


# -- what the proposal draws that the engine does not yet ----------------------

def _hex(c):
    """A theme colour as the glass shows it: through RGB565, widened as the
    renders' PNGs widen it."""
    c = c.lstrip("#")
    r, g, b = int(c[0:2], 16) >> 3, int(c[2:4], 16) >> 2, int(c[4:6], 16) >> 3
    return (r * 255 // 31, g * 255 // 63, b * 255 // 31)


def _colours(theme, mode):
    with open(os.path.join(SEEDS, theme + ".json")) as f:
        d = json.load(f)
    c = dict(d["colors"].get(mode) or d["colors"][d["modes"][0]])
    look = dict(d.get("look", {}))
    _merge(look, look.get(mode, {}))
    _merge(look, PROPOSAL[theme]["look"])
    return c, look


def _bars(img, f, w):
    """Each run of the focus colour `f` exactly `w` wide and 6 to 40 tall: the
    rows' bars, as (x, y, w, h)."""
    W, H = img.size
    px = img.load()
    seen = set()
    out = []
    for y in range(H):
        for x in range(W):
            if (x, y) in seen or px[x, y] != f:
                continue
            x1 = x
            while x1 < W and px[x1, y] == f:
                x1 += 1
            y1 = y
            while y1 < H and px[x, y1] == f:
                y1 += 1
            for yy in range(y, y1):
                for xx in range(x, x1):
                    seen.add((xx, yy))
            if x1 - x == w and 12 <= y1 - y <= 40 and (x < 60 or y1 - y >= 14):
                out.append((x, y, w, y1 - y))
    return out


def _run(px, y, x0, x1, c, W):
    """The run of colour `c` on row y through the first such pixel in
    [x0, x1): (start, end) or None."""
    for x in range(max(0, x0), min(W, x1)):
        if px[x, y] == c:
            a = x
            while a > 0 and px[a - 1, y] == c:
                a -= 1
            b = x
            while b < W and px[b, y] == c:
                b += 1
            return a, b
    return None


def _span(px, x, y, c, dx, dy, W, H):
    """How long the run of colour c through (x, y) is along (dx, dy)."""
    n = 1
    for s in (1, -1):
        i, j = x + s * dx, y + s * dy
        while 0 <= i < W and 0 <= j < H and px[i, j] == c:
            n += 1
            i, j = i + s * dx, j + s * dy
    return n


def _lerp(a, b, t):
    return tuple(int(round(a[i] + (b[i] - a[i]) * t)) for i in range(3))


def _t_on(p, a, b):
    """Where p lies on the line a -> b (0 at a, 1 at b), clamped."""
    d = [b[i] - a[i] for i in range(3)]
    n = sum(v * v for v in d)
    if n == 0:
        return 0.0
    t = sum((p[i] - a[i]) * d[i] for i in range(3)) / n
    return 0.0 if t < 0 else 1.0 if t > 1 else t


# the surfaces whose rows wear the bar (a theme whose focus colour is its ink
# has glyph stems the bar's shape elsewhere: Blocks)
ROWED = ("group", "list", "table", "menu", "fields", "tabs", "control-center")


def _overlay(img, surface, state, theme, mode):
    """The engine changes the look data cannot say, drawn the way the build
    is to draw them (README, change list 2-6): the selected fill on a group
    row; the rules kept beside a row's bar; the bar 2 px clear of a card's
    frame; the theme's outline round the bar; a field's own border kept
    under the bar; and the ring round the current tab in place of a bar
    beside the strip. The control center's inset is `_cc_inset`'s."""
    from PIL import ImageDraw
    img = img.copy()
    c, look = _colours(theme, mode)
    f = _hex(c.get("focus") or c["accent"])
    border = _hex(c.get("border") or c["edge"])
    px = img.load()
    W, H = img.size
    d = ImageDraw.Draw(img)
    box = BOXES.get("Selected")
    if surface == "group" and box is not None and state != "pressed" and \
            not state.startswith("touch") and box[2] > 0:
        _selected_fill(img, box, c, look, f)
    for (bx, by, bw, bh) in (_bars(img, f, 4) if surface in ROWED else ()):
        g = px[bx + bw + 1, by + bh // 2]
        if surface == "tabs" and state == "focus-tabs":
            d.rectangle((bx, by, bx + bw - 1, by + bh - 1), fill=g)
            _tab_ring(img, by, bh, g, f, look)
            continue
        # the rules above and below the row, where a rule of the same card
        # shows their extent
        ext = None
        for dy in range(1, 60):
            for y in (by - dy, by + bh + dy):
                if 0 <= y < H:
                    r = _run(px, y, bx + bw + 2, bx + 40, border, W)
                    if r is not None and r[1] - r[0] > 40:
                        ext = r
                        break
            if ext is not None:
                break
        if ext is not None:
            # a row stands there when the card's edge beside the bar runs on
            # past the rule (on a first or last row it turns into the corner)
            ex = max(0, bx - 1)
            side = px[ex, by + bh // 2]
            for y, beyond in ((by - 2, by - 5), (by + bh + 2, by + bh + 5)):
                if 0 <= beyond < H and px[ext[0] + 3, y] != border and px[ex, beyond] == side:
                    d.line((ext[0], y, ext[1] - 1, y), fill=border)
        # the bar stands 2 px clear of a card's frame, as the ring stands clear
        left = px[bx - 1, by + bh // 2] if bx > 0 else g
        nx = bx
        if left != g and bx > 2:
            d.rectangle((bx, by, bx + bw - 1, by + bh - 1), fill=g)
            nx = bx + 2
            d.rectangle((nx, by, nx + bw - 1, by + bh - 1), fill=f)
        out = look.get("focus", {}).get("outline")
        if out:
            d.rectangle((nx - 1, by - 1, nx + bw, by + bh), outline=_hex(out[1]))
        # a field on the row keeps its own border: the bar is its focus
        if surface not in ("fields",) and "name" not in state:
            continue
        fb = look.get("field", {}).get("border")
        fc = _hex(fb) if fb else border
        # the box: the focus colour's long runs across, then its sides
        ys = range(max(0, by - 5), min(H, by + bh + 5))
        hits = [(x, y) for y in ys for x in range(nx + bw + 12, W)
                if px[x, y] == f and _span(px, x, y, f, 1, 0, W, H) >= 12]
        if hits:
            x0, x1 = min(x for x, _y in hits), max(x for x, _y in hits)
            y0, y1 = min(y for _x, y in hits), max(y for _x, y in hits)
            hits += [(x, y) for y in range(y0, y1 + 1)
                     for x in list(range(x0 - 3, x0 + 3)) + list(range(x1 - 2, x1 + 4))
                     if 0 <= x < W and px[x, y] == f]
        for x, y in hits:
            px[x, y] = fc
    return img


def _selected_fill(img, box, c, look, f):
    """The selected fill on the group row in `box`: Moy's `value` plate,
    Blocks' selection fill with its ink, Contrast's solid plate and its ink,
    each the look's own `current`, the text recoloured along its ground."""
    px = img.load()
    x, y, w, h = box
    g = px[x + w // 2, y + 3]
    ink = _hex(c["ink"])
    cur = look.get("current", {})
    style = cur.get("style", "bar")
    if style == "fill":
        fill, ink2 = _hex(c["selection"]), _hex(c["selection_ink"])
    else:
        val = _hex(c.get("value") or c["accent"])
        a = float(cur.get("alpha", 1 if style == "plate" and cur.get("ink") == "value_ink"
                          else 0.24 if c is not None and _lum(g) < 0.5 else 0.14))
        fill = _lerp(g, val, a if a <= 1 else a / 100)
        ink2 = _hex(c[cur["ink"]]) if cur.get("ink") in c else ink
    for yy in range(y + 1, y + h - 1):
        for xx in range(x + 2, x + w - 2):
            p = px[xx, yy]
            if p == f and xx < x + 12:
                continue
            t = _t_on(p, g, ink)
            px[xx, yy] = _lerp(fill, ink2, t)


def _lum(c):
    return (0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]) / 255


def _tab_ring(img, by, bh, g, f, look):
    """The ring round the current tab (its pill, the band's commonest colour
    besides the ground), `ring.w` wide `ring.gap` out, and the theme's
    outline round it."""
    from PIL import ImageDraw
    W, _H = img.size
    px = img.load()
    best = None
    for yy in range(by - 2, by + bh + 2):
        x = 0
        while x < W:
            c = px[x, yy]
            x1 = x
            while x1 < W and px[x1, yy] == c:
                x1 += 1
            if c != g and 16 <= x1 - x <= 90 and (best is None or x1 - x > best[1] - best[0]):
                best = (x, x1, yy, c)
            x = x1
    x0, x1, yy, pill = best
    x1 -= 1
    y0 = yy
    while px[x0 + 1, y0 - 1] == pill:
        y0 -= 1
    y1 = yy
    while px[x0 + 1, y1 + 1] == pill:
        y1 += 1
    ring = look.get("focus", {}).get("ring", {})
    rw, gap = int(ring.get("w", 2)), int(ring.get("gap", 2))
    o = gap + rw
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((x0 - o, y0 - o, x1 + o, y1 + o), radius=4 + o, outline=f, width=rw)
    out = look.get("focus", {}).get("outline")
    if out:
        e = o + int(out[0])
        d.rounded_rectangle((x0 - e, y0 - e, x1 + e, y1 + e), radius=4 + e,
                            outline=_hex(out[1]), width=int(out[0]))


def _cc_inset(on):
    """The control center's console card stands as far in from the panel's
    top and foot as from its sides (the group's inset), in the proposal."""
    import importlib
    popover = importlib.import_module("runtime.popover")
    cls = popover.ControlCenter
    if not on:
        if hasattr(cls, "_focus_shots_draw"):
            cls.draw, cls.size = cls._focus_shots_draw, cls._focus_shots_size
            del cls._focus_shots_draw, cls._focus_shots_size
        return
    if hasattr(cls, "_focus_shots_draw"):
        return
    cls._focus_shots_draw, cls._focus_shots_size = cls.draw, cls.size
    draw, size = cls.draw, cls.size

    def at(self):
        from runtime import ui_draw
        return ui_draw.metrics("group")[0] * self.ws.layout.fs

    def new_draw(self, cv, rect):
        x, y, w, h = rect
        real = popover._ui.at
        i = at(self)

        def shifted(ax, ay, aw, ah):
            real(ax, ay + i, aw, ah - i)
        popover._ui.at = shifted
        try:
            draw(self, cv, rect)
        finally:
            popover._ui.at = real

    def new_size(self):
        w, h = size(self)
        if not popover._ITEMS.shown:
            fs = self.ws.layout.fs
            rows = 3 * 20 * fs + 2 * fs
            h = 2 * fs + 2 * at(self) + rows
        return (w, h)
    cls.draw, cls.size = new_draw, new_size


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("out")
    ap.add_argument("filters", nargs="*")
    ap.add_argument("--work", help="where the frames and whole renders go (default: a "
                    "temporary directory)")
    ap.add_argument("--runs", default="today,proposal,findings")
    a = ap.parse_args(argv)
    work = a.work or tempfile.mkdtemp(prefix="focus_shots_")
    runs = a.runs.split(",")
    for run in ("today", "proposal"):
        if run in runs:
            frames = shoot(os.path.join(work, run), a.filters, run == "proposal")
            os.makedirs(os.path.join(a.out, run), exist_ok=True)
            sheets(os.path.join(a.out, run), frames, run.upper())
    if "findings" in runs:
        findings(work, os.path.join(a.out, "findings"))
    print("frames and whole renders in", work)
    return 0


if __name__ == "__main__":
    sys.exit(main())
