#!/usr/bin/env python3
"""Round 10 of the 2026-10 look, layered over round 9: the owner's
decisions of 2026-10-15 drawn.

- Moy is the canonical pixel Moy in every theme, integer-scaled: 16 px on
  the handheld bar, 32 px on the desk's 36x34 button.
- Moy is the system menu button and is not in the focus order: Super opens
  the menu from anywhere; open, the button shows the current plate, no ring.
- A small icon button's focus is the hugging ring (`focus_small`).
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


def focus_small(cv, T, x, y, w, h, r):
    """Focus on a part under 28 px: a plate tinted `accent` and a 1.5 px
    `accent` ring on its edge, inside the part's own box. Draw the glyph
    after it."""
    cv.rrect(x, y, w, h, r, T["accent"], 0.22 if T["mode"] == "dark" else 0.14)
    cv.ring(x, y, w, h, r, 1.5, T["accent"])


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
    ra = T["ring_alpha"]
    T["ring_alpha"] = 0.0
    cv = M.hh_home(T)
    T["ring_alpha"] = ra
    cv.rect(0, 28, 320, 212, "#000000", 0.18)
    hh_sys(cv, T, "open")
    R9.menu_popover(cv, T, 4, 30, 150, False)
    R9.hint_strip(cv, T, (("enter", "Choose"), ("Esc", "Close")))
    return cv


def desk_menu(T):
    ra = T["ring_alpha"]
    T["ring_alpha"] = 0.0
    cv = M.desktop(T)
    T["ring_alpha"] = ra
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
        R7.draw_icon(cv, T, slot, x + pad + 2, yy + (rh - s) // 2, s, T["dim"])
        cv.text(x + pad + s + (14 if desk else 9), M.mid_base(fr, yy, rh), label, fr, T["text"])
        if i == 0:
            M.focus_ring(cv, T, x + pad, yy + 2, w - 2 * pad, rh - 4, 6 if desk else 4)
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


def focus_marks():
    sc = 3
    rows = (("Moy Day, the Code tab's bar: undo focused", LIGHT, "bar"),
            ("Moy Night, the Code tab's bar: undo focused", DARK, "bar"),
            ("Moy Day, a window's title buttons: close focused", LIGHT, "win"),
            ("Moy Night, a window's title buttons: close focused", DARK, "win"))
    cw, ch = 150 * sc, 34 * sc
    W, H = 20 + 2 * (cw + 20), 70 + len(rows) * (ch + 40) + 28 * sc + 64
    out = M.Cv(W, H, "#e9eaee")
    fb, f, fs = Face("inter", 15, 700, 4), Face("inter", 13, 500, 4), Face("inter", 11, 500, 2)
    out.text(12, 26, "Focus on small targets, at 3x: the ring as large parts take it, and the hugging ring",
             fb, "#15161d")
    for i, head in enumerate(("(a) the ring: 2.5 px, a 3 px gap", "(b) the hugging ring: a tinted plate, "
                              "1.5 px, no gap (chosen under 28 px)")):
        out.text(20 + i * (cw + 20), 58, head, f, "#4c3fd6" if i else "#33364a")
    y = 70
    for label, T, what in rows:
        out.text(20, y + 14, label, fs, "#4a4d5c")
        for i in range(2):
            if what == "bar":
                cv = M.new_cv(T, False, T["bg"])
                R8.ladder_bar(cv, T, "code")
                ux = bar_icon_xs(T)[0]
                box = (ux - 3, 2, 22, 22, 6)
                glyph = ("undo", ux, 5, 16, T["hhbar"]["dim"] or T["dim"])
                crop = (ux - 60, 0, ux + 90, 34)
            else:
                cv = M.new_cv(T, True)
                M.backdrop(cv, T, "desk")
                M.window(cv, T, 40, 40, 400, 200, "Calc", "calc", True)
                hh = T["win"]["h"]
                s = min(20, hh - 8)
                fw = (T["win"]["frame"] or (0,))[0]
                cx = 40 + 400 - s - 14 - fw
                box = (cx - 5, 40 + (hh - s) // 2 - 5, s + 10, s + 10, 8)
                glyph = ("close", cx, 40 + (hh - s) // 2, s, R8.title_ink(T, True))
                crop = (440 - 150, 40, 440, 40 + 34)
            x0, y0, w0, h0, r0 = box
            if i == 0:
                M.focus_ring(cv, T, x0 + 3, y0 + 3, w0 - 6, h0 - 6, r0 - 2)
            else:
                focus_small(cv, T, x0, y0, w0, h0, r0)
                R7.draw_icon(cv, T, *glyph)
            im = cv.image().crop(crop).resize((cw, ch), Image.NEAREST)
            out.img.paste(im, (20 + i * (cw + 20), y + 24))
        y += ch + 40
    out.text(20, y + 14, "Moy is not in the focus order (Super reaches it): at rest, and open "
             "(the current plate)", fs, "#4a4d5c")
    x = 20
    for T in (LIGHT, DARK):
        for st in ("rest", "open"):
            cv = M.hh_home(T)
            if st == "open":
                hh_sys(cv, T, "open")
            im = cv.image().crop((0, 0, 60, 28)).resize((60 * sc, 28 * sc), Image.NEAREST)
            out.img.paste(im, (x, y + 24))
            x += 60 * sc + 12
    out.text(12, H - 12, "(b) stays inside a 26 px bar and off its neighbours; (a) is 27 px across a 16 px "
             "glyph and overlaps them.", fs, "#4a4d5c")
    out.save("focus_marks", "Focus on small targets at 3x: the ring against the hugging ring on the "
             "handheld bar and a window's title buttons, in Moy Day and Night; and the Moy button "
             "at rest and open")


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
