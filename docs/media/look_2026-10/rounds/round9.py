#!/usr/bin/env python3
"""Round 9 of the 2026-10 look, layered over round 8: the canonical pixel
Moy, Play without a plate, redo as undo's mirror, and Moy as the system
menu button.

A layer of the target, not an entry point: `../draw.py` draws every picture.
"""
import os
import sys

from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import round8 as R8  # noqa: E402

R7, M, PX = R8.R7, R8.M, R8.PX
Face, F, HH, DK = M.Face, M.F, M.HH, M.DK
LIGHT, DARK, PICO = R8.LIGHT, R8.DARK, R8.PICO
INDEX = M.INDEX
PAL = R7.PAL
PAL.append((0, 0, 0))  # index 16: Moy's black outline (written as 0 in the .moygfx)

# ---- 1. the canonical Moy: the website's 16x16 (docs/visual_identity_v1.md 6.5)
SITE_MOY = (
    "................", "...0000000......", "..0ddddddd0.....", ".0d66ddddd0.....",
    ".0dddddddd0.....", ".0dddddddd0000..", ".0dd77d77ddddd0.", ".0dd70d70ddddd0.",
    ".0dddddddddddd0.", ".0dd0ddd0ddddd0.", ".0ddd000dddddd0.", ".0dddddddddddd0.",
    ".0dddddddddddd0.", "..022222222220..", "..02220002220...", "...000...000....",
)
PX.ICONS["moy"] = PX.hand(SITE_MOY)

# the smooth Moy: the same silhouette traced in the 16-grid, scaled to 24
SMOOTH_MOY = (
    '<g transform="scale(1.5)">'
    '<mask id="m"><rect width="16" height="16" fill="#fff"/>'
    '<rect x="4" y="6" width="2" height="2" rx=".5" fill="#000"/>'
    '<rect x="7" y="6" width="2" height="2" rx=".5" fill="#000"/>'
    '<path d="M4.4 9.2l1 1.2h2.2l1-1.2" stroke="#000" stroke-width="1" fill="none" '
    'stroke-linejoin="round" stroke-linecap="round"/></mask>'
    '<path d="M1 3.6Q1 1 3.6 1H9.2Q11 1 11 2.8V5h2.4Q15 5 15 6.6V12.6H1z" mask="url(#m)"/>'
    '<rect x="2.3" y="13.3" width="4.6" height="2" rx=".8"/><rect x="9.1" y="13.3" width="4.6" height="2" rx=".8"/>'
    '</g>')
R8.MARKS["D, the site Moy, traced"] = SMOOTH_MOY
OLD_MARK = R8.CHOSEN
R8.CHOSEN = "D, the site Moy, traced"

# ---- 3. redo is undo, mirrored
_svg8 = R7.svg


def svg(slot, set_name, layer):
    if slot == "redo":
        s = _svg8("undo", set_name, layer)
        a, b = s.index(">") + 1, s.rindex("</svg>")
        return s[:a] + '<g transform="matrix(-1 0 0 1 24 0)">' + s[a:b] + "</g></svg>"
    return _svg8(slot, set_name, layer)


R7.svg = svg
R7._MASKS.clear()
_undo = PX.icon("undo")
PX.ICONS["redo"] = [row[::-1] for row in _undo]


# ---- the bar's Moy: pixel art (option a) or the smooth trace (option b)
MOY_STYLE = {"style": "pixel"}


def moy_glyph(cv, x, y, px, body, eye):
    """M.moy's replacement: the system button's mark."""
    if MOY_STYLE["style"] == "pixel":
        s = 2 if px >= 24 else 1
        im = PX.image(PX.icon("moy"), PAL, s)
        cv.img.paste(im.convert("RGB"), (cv.L(x + (px - 16 * s) // 2), cv.L(y + (px - 16 * s) // 2)),
                     im.split()[3])
    else:
        bpp = 2 if px <= 16 else 4
        cv._blend(cv.L(x), cv.L(y), R7.mask("moy", "default", "ink", px, bpp), body)


M.moy = moy_glyph
R7.pixel_icon = R8.pixel_icon_new


def options_sheet():
    """moy_options_r9.png: (a) pixel Moy everywhere, (b) the smooth trace,
    and the r8 mark for reference, on Moy Day and Night bars, handheld and desk."""
    cols = [("r8 mark (replaced)", "r8"), ("(a) pixel Moy, 1x / 2x", "pixel"), ("(b) smooth trace", "smooth")]
    cw, rh = 300, 96
    W, H = 170 + cw * 3, 66 + 4 * rh + 26
    cv = M.Cv(W, H, "#e9eaee")
    fb, f, fs = Face("inter", 15, 700, 4), Face("inter", 13, 500, 4), Face("inter", 11, 500, 2)
    cv.text(12, 26, "Moy in the smooth themes: the canonical pixel Moy, or its smooth trace", fb, "#15161d")
    for i, (name, _) in enumerate(cols):
        cv.text(170 + i * cw + 12, 56, name + ("   (recommended)" if _ == "pixel" else ""), f,
                "#4c3fd6" if _ == "pixel" else "#33364a")
    y = 66
    for label, T, where in (("Moy Day, handheld", LIGHT, "hh"), ("Moy Night, handheld", DARK, "hh"),
                            ("Moy Day, desk", LIGHT, "dk"), ("Moy Night, desk", DARK, "dk")):
        cv.text(12, y + 52, label, f, "#33364a")
        bar = T["bar"]["fill"] if T is DARK else "#ffffff"
        tile, body, eye = (T["hhbar"]["sys"] or T["bar"]["sys"]) if where == "hh" else T["bar"]["sys"]
        for i, (_, style) in enumerate(cols):
            x0 = 170 + i * cw
            cv.rect(x0 + 4, y + 4, cw - 8, rh - 8, bar)
            tw, th, px = (22, 20, 16) if where == "hh" else (36, 34, 32 if style == "pixel" else 24)
            tx, ty = x0 + 20, y + (rh - th) // 2
            cv.rrect(tx, ty, tw, th, 7 if where == "hh" else 9, tile)
            mx, my = tx + (tw - px) // 2, ty + (th - px) // 2
            if style == "r8":
                cv._blend(mx, my, R8.mark_mask(OLD_MARK, px if px != 32 else 24, 2 if px <= 16 else 4), body)
            else:
                MOY_STYLE["style"] = style
                moy_glyph(cv, mx, my, px, body, eye)
            big = 64
            if style == "pixel":
                im = PX.image(PX.icon("moy"), PAL, 4)
                cv.img.paste(im.convert("RGB"), (x0 + 150, y + (rh - big) // 2), im.split()[3])
            else:
                m = R8.mark_mask(OLD_MARK, big, 4) if style == "r8" else R7.mask("moy", "default", "ink", big, 4)
                cv._blend(x0 + 150, y + (rh - big) // 2, m, body)
        y += rh
    cv.text(12, H - 10, "(a) is the website's 16x16 Moy, integer-scaled: 16 px in the handheld bar, "
            "32 px (2x) on the desk bar's 36 px button.", fs, "#4a4d5c")
    cv.save("moy_options", "Moy in the smooth themes: the r8 mark, (a) the canonical pixel Moy "
            "at 1x/2x, (b) its smooth trace at 16/24; Moy Day and Night, handheld and desk bars")
    MOY_STYLE["style"] = "pixel"


# ---- 2. Play with no plate, every theme
def editor_bars():
    themes = (LIGHT, DARK, M.C, M.LUNA, M.COAST, PICO, M.WB, M.ADW, M.CONTRAST)
    sc = 2
    W, H = 320 * sc + 220, len(themes) * (26 * sc + 10) + 40
    out = M.Cv(W, H, "#e9eaee")
    f = Face("inter", 13, 500, 4)
    out.text(10, 24, "The editor bar in every theme: Play beside undo and redo, no plate", Face("inter", 15, 700, 4),
             "#15161d")
    y = 40
    for T in themes:
        cv = M.new_cv(T, False, T["bg"])
        R8.ladder_bar(cv, T, "code")
        im = cv.image().crop((0, 0, 320, 26)).resize((320 * sc, 26 * sc), Image.NEAREST)
        out.img.paste(im, (210, y))
        out.text(10, y + 32, T["name"], f, "#33364a", maxw=195)
        y += 26 * sc + 10
    out.save("editor_bar_all_themes", "The handheld editor bar in all nine themes at 2x: "
             "Play as a bare value-coloured glyph in the undo/redo row")


# ---- 4. Moy, the system menu button
MENU = (("header", "System"), ("search", "Search"), ("settings", "Settings"), ("notes", "About"),
        ("rotate", "Restart"))


def menu_popover(cv, T, x, y, w, desk):
    rh = 34 if desk else 22
    h = 10 + len(MENU) * rh
    M.soft(cv, T, x, y, w, h, 10 if desk else 8, 0.8)
    cv.rrect(x, y, w, h, 10 if desk else 8, T["surface"])
    if T["mode"] == "dark":
        cv.ring(x, y, w, h, 10 if desk else 8, 1, T["line"])
    fr = F(T, "row", DK) if desk else F(T, "tab", HH)
    fc = F(T, "col", DK) if desk else F(T, "meta", HH)
    s = 18 if desk else 14
    yy = y + 5
    for i, (slot, label) in enumerate(MENU):
        if slot == "header":
            cv.text(x + 12, M.mid_base(fc, yy, rh), label, fc, T["dim"])
        else:
            ink = M.focus_mark(cv, T, (x + 9, yy + 2, w - 18, rh - 4, 5 if desk else 4), "part") if i == 1 else None
            R7.draw_icon(cv, T, slot, x + 12, yy + (rh - s) // 2, s, ink or T["dim"])
            cv.text(x + 20 + s, M.mid_base(fr, yy, rh), label, fr, ink or T["text"])
        yy += rh


def sys_states(cv, T, tile_box, state):
    """focus: the ring round the button; open: the button sunk, the current
    bar under it (the menu is the button's own open state)."""
    x, y, w, h, r = tile_box
    if state == "focus":
        M.focus_mark(cv, T, (x, y, w, h, r), "small" if h < 28 else "part")
    elif state == "open":
        cv.rrect(x, y, w, h, r, T["sunk"])
        M.moy(cv, x + (w - 16) // 2 if w < 30 else x + (w - 32) // 2, y + 1 + ((h - 16) // 2 if w < 30 else (h - 32) // 2),
              16 if w < 30 else 32, T["value"], T["sunk"])
        cv.rrect(x + 3, y + h + 1, w - 6, 2 if w < 30 else 3, 1, T["value"])


def hint_strip(cv, T, items, right=None):
    fk, fh = F(T, "key", HH), F(T, "hint", HH)
    bg = T["home_bg"] or T["bg"]
    cv.rect(0, 216, 320, 24, bg)
    x, base = 8, 233
    for k, label in items:
        x += M.keycap(cv, T, x, base, k, fk, bg) + 4
        x += cv.text(x, base, label, fh, T["dim"]) + 11
    if right:
        cv.text(312, base, right, fh, T["dim"], align="right")


def handheld_menu(state):
    T = LIGHT
    with M.focus_off():
        cv = M.hh_home(T)
    sys_states(cv, T, (5, 4, 22, 20, 7), state)
    if state == "focus":
        hint_strip(cv, T, (("enter", "Open menu"), ("Esc", "Menu, from anywhere")))
    else:
        cv.rect(0, 28, 320, 212, "#000000", 0.18)
        sys_states(cv, T, (5, 4, 22, 20, 7), "open")
        menu_popover(cv, T, 4, 30, 150, False)
        hint_strip(cv, T, (("enter", "Choose"), ("Esc", "Close")))
    return cv


def desk_menu(state):
    T = LIGHT
    with M.focus_off():
        cv = M.desktop(T)
    box = (8, 4, 36, 32, 9)
    sys_states(cv, T, box, state)
    if state == "open":
        menu_popover(cv, T, 8, 44, 220, True)
    return cv
