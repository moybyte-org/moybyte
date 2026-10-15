#!/usr/bin/env python3
"""Round 10 of the 2026-10 look, layered over round 9: the owner's
decisions of 2026-10-15 drawn.

- Moy is the canonical pixel Moy in every theme, integer-scaled: 16 px on
  the handheld bar, 32 px on the desk's 36x34 button.
- Moy is the system menu button and is not in the focus order: Super opens
  the menu from anywhere; open, the button shows the current plate, no ring.
- Focus and current are theme properties (`focus`, `current`): the
  engine's `focus_mark` draws focus by size class, `current_mark` the
  current plate or fill.
- Super during a fullscreen game opens the pause menu.
- Luna's Play reads at full size; Bench's undo and redo take a palette
  swap that holds on its white bar.

A layer of the target, not an entry point: `../draw.py` draws every picture.
"""
import os
import sys

from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import round9 as R9  # noqa: E402

R8, R7, M, PX = R9.R8, R9.R7, R9.M, R9.PX
Face, F, HH, DK, mix = M.Face, M.F, M.HH, M.DK, M.mix
LIGHT, DARK, PICO = R9.LIGHT, R9.DARK, R9.PICO
PAL = R7.PAL


# ---- the canonical Moy, integer-scaled
def moy_glyph(cv, x, y, px, body, eye):
    s = max(1, px // 16)
    im = PX.image(PX.icon("moy"), PAL, s)
    o = (px - 16 * s) // 2
    cv.img.paste(im.convert("RGB"), (cv.L(x + o), cv.L(y + o)), im.split()[3])


M.moy = moy_glyph


# ---- icons: a pixel set takes the theme's palette swap; Luna's Play
_draw_icon = R7.draw_icon


def draw_icon(cv, T, slot, x, y, px, ink, fill=None):
    if slot == "moy":
        moy_glyph(cv, x, y, px, ink, None)
        return
    kind, set_name = R7.theme_icons(T)
    swap = T.get("icon_swap")
    if kind == "pixel" and swap and slot in PX.ICONS:
        pix = [[swap.get(c, c) for c in row] for row in PX.icon(slot)]
        im = PX.image(pix, PAL)
        s = max(1, px // 16)
        im = im.resize((16 * s, 16 * s), Image.NEAREST)
        cv.img.paste(im.convert("RGB"), (cv.L(x), cv.L(y)), im.split()[3])
        return
    if slot == "play" and set_name == "luna":
        # the green body is the primary; its outline is the bar's ink, so the
        # whole glyph reads on the blue bar
        _draw_icon(cv, T, slot, x, y, px, T["hhbar"]["ink"] or "#ffffff", fill)
        return
    _draw_icon(cv, T, slot, x, y, px, ink, fill)


R7.draw_icon = draw_icon
# Bench's bar is white: the blue ramp's base (12, light blue) becomes 1 (dark blue)
M.WB["icon_swap"] = {12: 1}


# ---- marks
def open_plate(T, tile):
    """The open state of a bar button: the current mark as a fill."""
    return T["sel"] or mix(tile, T["value"], 0.30 if T["mode"] == "dark" else 0.16)


def hh_sys(cv, T, state):
    tile = (T["hhbar"]["sys"] or T["bar"]["sys"])[0]
    if state == "open":
        cv.rrect(5, 4, 22, 20, 7, open_plate(T, tile))
        moy_glyph(cv, 8, 6, 16, None, None)


def dk_sys(cv, T, state):
    tile = T["bar"]["sys"][0]
    if state == "open":
        cv.rrect(6, 3, 36, 34, 9, open_plate(T, tile))
        moy_glyph(cv, 8, 4, 32, None, None)


# ---- the system menu
def handheld_menu(T):
    with M.focus_off():
        cv = M.hh_home(T)
    cv.rect(0, 28, 320, 212, "#000000", 0.18)
    hh_sys(cv, T, "open")
    R9.menu_popover(cv, T, 4, 30, 150, False)
    R9.hint_strip(cv, T, (("enter", "Choose"), ("Esc", "Close")))
    return cv


def desk_menu(T):
    with M.focus_off():
        cv = M.desktop(T)
    dk_sys(cv, T, "open")
    R9.menu_popover(cv, T, 8, 46, 220, True)
    return cv


# ---- the pause menu
PAUSE = (("play", "Resume"), ("rotate", "Restart"), ("settings", "Options"), ("close", "Quit"))


def pause_card(cv, T, x, y, w, desk):
    rh, hd, ft = (36, 54, 40) if desk else (22, 32, 24)
    h = hd + len(PAUSE) * rh + 8 + ft
    r = 12 if desk else 9
    M.soft(cv, T, x, y, w, h, r, 1.0)
    cv.rrect(x, y, w, h, r, T["surface"])
    if T["mode"] == "dark":
        cv.ring(x, y, w, h, r, 1, T["line"])
    ft_title = F(T, "wtitle", DK) if desk else F(T, "tab", HH)
    fm = F(T, "meta", DK) if desk else F(T, "meta", HH)
    fr = F(T, "row", DK) if desk else F(T, "tab", HH)
    mp = 32 if desk else 16
    pad = 14 if desk else 9
    moy_glyph(cv, x + pad, y + (hd - mp) // 2, mp, None, None)
    tx = x + pad + mp + (10 if desk else 6)
    cv.text(tx, M.mid_base(ft_title, y, hd), "Star Catcher", ft_title, T["text"])
    cv.text(x + w - pad, M.mid_base(fm, y, hd), "Paused", fm, T["dim"], align="right")
    cv.rect(x + pad, y + hd - 1, w - 2 * pad, 1, T["line"])
    s = 18 if desk else 14
    yy = y + hd + 4
    for i, (slot, label) in enumerate(PAUSE):
        box = (x + pad, yy + 2, w - 2 * pad, rh - 4, 6 if desk else 4)
        ink = M.focus_mark(cv, T, box, "part") if i == 0 else None
        R7.draw_icon(cv, T, slot, x + pad + 2, yy + (rh - s) // 2, s, ink or T["dim"])
        cv.text(x + pad + s + (14 if desk else 9), M.mid_base(fr, yy, rh), label, fr, ink or T["text"])
        yy += rh
    fy = y + h - ft
    cv.rect(x + pad, fy, w - 2 * pad, 1, T["line"])
    fk, fh = F(T, "key", HH), F(T, "hint", HH)
    base = fy + (ft + 12) // 2 + (2 if desk else 1)
    hx = x + pad
    for k, label in (("enter", "Choose"), ("Esc", "Resume")):
        hx += M.keycap(cv, T, hx, base, k, fk, T["surface"]) + 4
        hx += cv.text(hx, base, label, fh, T["dim"]) + 12
    return h


def handheld_pause(T):
    cv = M.Cv(320, 240, "#000000")
    cv.img.paste(R7.GAME, (0, 0))
    cv.rect(0, 0, 320, 240, "#000000", 0.55)
    pause_card(cv, T, 66, 34, 188, False)
    return cv


def desk_pause(T):
    cv = M.Cv(1024, 600, "#000000")
    cv.img.paste(R7.GAME.resize((640, 480), Image.NEAREST), (192, 60))
    cv.rect(0, 0, 1024, 600, "#000000", 0.55)
    pause_card(cv, T, 362, 150, 300, True)
    return cv


# ---- the sheets
def bar_icon_xs(T):
    """x of the ladder bar's undo, redo and Play on the Code tab."""
    s, x = 16, 3
    lf = Face(T["fam"], 12, T["w_semi"], 2)
    for tab, _slot, label in R8.LADDER:
        x += (s + 4 + lf.width(label) + 6) if tab == "code" else s + 2
    return x + 2, x + 20, x + 38


def mark_list(T, focus, current):
    """Three rows on a surface: one focused, one current, or both on one."""
    cv = M.new_cv(T, False, T["bg"])
    px, py, pw, rh = 6, 6, 170, 26
    cv.rrect(px, py, pw, 3 * rh + 8, 10, T["surface"])
    if T["mode"] == "dark":
        cv.ring(px, py, pw, 3 * rh + 8, 10, 1, T["line"])
    fr, fm = F(T, "tab", HH), F(T, "meta", HH)
    yy = py + 4
    for i, (name, size) in enumerate((("brick_siege", "14 KB"), ("calc", "6 KB"), ("hop_quest", "18 KB"))):
        box = (px + 4, yy + 1, pw - 8, rh - 2, 6)
        ink = M.current_row(cv, T, *box, True) if i == current else T["text"]
        if i == focus:
            ink = M.focus_mark(cv, T, box, "part") or ink
            if i == current and M.cur_style(T) == "bar" and ink != T["text"]:
                cv.rrect(box[0] + 4, box[1] + (box[3] - 16) // 2, 3, 16, 1.5, ink)
        R7.draw_icon(cv, T, "projects", px + 12, yy + (rh - 14) // 2, 14, ink if ink != T["text"] else T["dim"])
        cv.text(px + 32, M.mid_base(fr, yy, rh), name, fr, ink)
        cv.text(px + pw - 10, M.mid_base(fm, yy, rh), size, fm, ink if ink != T["text"] else T["dim"],
                align="right")
        yy += rh
    return cv.image().crop((0, 0, 182, 3 * rh + 20))


def mark_bar(T):
    """The handheld bar on the Code tab (current), undo focused."""
    cv = M.new_cv(T, False, T["bg"])
    R8.ladder_bar(cv, T, "code")
    ux = bar_icon_xs(T)[0]
    hb = T["hhbar"]
    ink = M.focus_mark(cv, T, (ux - 3, 2, 22, 22, 6), "small")
    R7.draw_icon(cv, T, "undo", ux, 5, 16, ink or hb["dim"] or T["dim"])
    return cv.image().crop((0, 0, ux + 60, 28))


def focus_marks():
    sc = 2
    themes = (LIGHT, DARK, M.C, M.CONTRAST)
    rowh = 150 * sc + 40
    W, H = 20 + 160 * sc + 20 + 2 * (182 * sc + 20), 60 + len(themes) * rowh + 28
    out = M.Cv(W, H, "#e9eaee")
    fb, f, fs = Face("inter", 15, 700, 4), Face("inter", 13, 600, 4), Face("inter", 11, 500, 2)
    out.text(12, 26, "The marks by theme, at 2x: focus by size class (card, part, small) and current, "
             "from each theme's focus and current fields", fb, "#15161d")
    y = 50
    for T in themes:
        fo, cu = T["focus"], T["current"]
        out.text(20, y + 14, f"{T['name'].split(' (')[0]}: focus card {fo['card'] or fo['style']}, part "
                 f"{fo['style']}, small {fo['small'] or fo['style']}; current {cu['style']}", f, "#33364a")
        im = M.hh_home(T).image().crop((80, 26, 240, 176))
        out.img.paste(im.resize((160 * sc, 150 * sc), Image.NEAREST), (20, y + 24))
        x = 20 + 160 * sc + 20
        for j, (fi, ci, what) in enumerate(((2, 0, "hop_quest focused, brick_siege current"),
                                            (1, 1, "calc focused and current"))):
            im = mark_list(T, fi, ci)
            out.img.paste(im.resize((im.width * sc, im.height * sc), Image.NEAREST), (x + j * (182 * sc + 20), y + 24))
            out.text(x + j * (182 * sc + 20), y + 24 + im.height * sc + 14, what, fs, "#4a4d5c")
        im = mark_bar(T)
        out.img.paste(im.resize((im.width * sc, im.height * sc), Image.NEAREST), (x, y + 24 + 98 * sc + 26))
        out.text(x, y + 24 + 98 * sc + 26 + 28 * sc + 14, "the bar: Code current, undo focused", fs, "#4a4d5c")
        y += rowh
    out.save("focus_marks", "The marks by theme at 2x: each theme's focus by size class (the hero card, a "
             "row, the bar's undo) and its current mark, focused and current apart and on one row, in "
             "Moy Day, Moy Night, Blocks and Contrast")


def moy_mark():
    W, H = 20 + 4 * 216, 320
    out = M.Cv(W, H, "#e9eaee")
    fb, fs = Face("inter", 15, 700, 4), Face("inter", 11, 500, 2)
    out.text(12, 26, "Moy, the canonical pixel Moy, integer-scaled: 16 px on the handheld bar, 32 px on "
             "the desk's 36x34 button", fb, "#15161d")
    x, y = 20, 44
    for T in (LIGHT, DARK):
        for st in ("rest", "open"):
            cv = M.hh_home(T)
            if st == "open":
                hh_sys(cv, T, st)
            im = cv.image().crop((0, 0, 64, 28)).resize((128, 56), Image.NEAREST)
            out.img.paste(im, (x, y + 16))
            out.text(x, y + 10, f"{T['name'].split(' (')[0]}, handheld, {st}", fs, "#4a4d5c")
            x += im.width + 16
    x, y = 20, 150
    for T in (LIGHT, DARK):
        for st in ("rest", "open"):
            cv = M.desktop(T)
            if st == "open":
                dk_sys(cv, T, st)
            im = cv.image().crop((0, 0, 100, 40)).resize((200, 80), Image.NEAREST)
            out.img.paste(im, (x, y + 16))
            out.text(x, y + 10, f"{T['name'].split(' (')[0]}, desk, {st}", fs, "#4a4d5c")
            x += im.width + 16
    big = PX.image(PX.icon("moy"), PAL, 4)
    out.rect(20, 250, 64, 64, "#ffffff")
    out.img.paste(big.convert("RGB"), (20, 250), big.split()[3])
    out.text(100, 286, "docs/visual_identity_v1.md 6.5: outline 0 (black), body 13, feet and shade 2, "
             "sheen 6, eyes 7. Its outline is index 0, which an IconSheet keys clear (GAPS 18).",
             fs, "#4a4d5c")
    out.save("moy_mark", "Moy, the canonical pixel Moy: handheld bar (16 px) and desk button (32 px on "
             "36x34), at rest and open, in Moy Day and Night, at 2x")


def editor_bars():
    R9.editor_bars()


def scenes():
    for T, tag in ((LIGHT, "moy-light"), (DARK, "moy-dark")):
        handheld_menu(T).save(f"{tag}_handheld_sysmenu",
                              f"The system menu, open from Super: Moy shows the current plate, no ring; "
                              f"handheld home; {T['name']}", x3=True)
        desk_menu(T).save(f"{tag}_desktop_sysmenu",
                          f"The system menu, open from Super, on the desk; {T['name']}")
        handheld_pause(T).save(f"{tag}_handheld_pause",
                               f"The pause menu over a fullscreen game (Super): resume, restart, options, "
                               f"quit; {T['name']}", x3=True)
        desk_pause(T).save(f"{tag}_desktop_pause",
                           f"The pause menu over a fullscreen game at 2x on the desk; {T['name']}")
    for T, tag in ((LIGHT, "moy-light"), (DARK, "moy-dark"), (PICO, "pico-light")):
        R8.hh_code(T).save(f"{tag}_handheld_code-editor",
                           f"Code tab on Star Catcher in today's layout, restyled: Play bare beside undo "
                           f"and redo; {T['name']}", x3=True)
        R8.hh_sprites(T).save(f"{tag}_handheld_sprite-editor",
                              f"Sprites tab on Star Catcher's frog in today's layout, restyled; {T['name']}",
                              x3=True)
    for T, tag in ((LIGHT, "moy-light"), (DARK, "moy-dark")):
        R8.desk_editor(T, "code").save(f"{tag}_desktop_code-editor",
                                       f"The Editor window on the desk, Code tab, today's layout; {T['name']}")
        R8.desk_editor(T, "paint").save(f"{tag}_desktop_sprite-editor",
                                        f"The Editor window on the desk, Sprites tab, today's layout; "
                                        f"{T['name']}")
    R8.desk_playtest(LIGHT).save("moy-light_desktop_playtest",
                                 f"The playtest window (2x) over the Editor, on the desk; {LIGHT['name']}")
    editor_bars()
    focus_marks()
    moy_mark()
    R7.icons_sheet(None)
    R8.pixel_sheet()
    return R7.write_sources()
