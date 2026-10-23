#!/usr/bin/env python3
"""Round 16 of the 2026-10 look, over round 13: Achievements and About
(`docs/look_2026-10.md` step 32).

- Achievements is a page of Settings (its About group's Achievements row):
  a progress row, then the found badges and the ones still to find as card
  grids, a badge's picture its icon on a tile tinted from `value`, a locked
  one veiled with a lock badge and a secret one named "Secret" until found.
  The unlock is the system's banner under the bar, a secret's with confetti.
- About is the system menu's card over the washed content: Moy, the
  wordmark, the version and the board, then Updates and Licences as rows.
  Licences is a page of Settings (the About group's last row), a licence's
  text a page under it; on the desk the two sit side by side.
- Each is drawn on the T-Deck (320x240), the Guition S3 (480x320, touch: no
  hint strip, its rows at the tap floor) and the 1024x600 desk, in Moy Day
  and Night, and in Contrast.

A layer of the target, not an entry point: `../draw.py` draws every picture.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import round13 as R13  # noqa: E402

M, R11, R8 = R13.M, R13.R11, R13.R8
Face, F, HH, DK, mix = M.Face, M.F, M.HH, M.DK, M.mix
LIGHT, DARK = R13.LIGHT, R13.DARK
P64, sicon, _p = R13.P64, R13.sicon, R13._p
CONTRAST = M.CONTRAST


# ==========================================================================
# icons: the slots this round adds (GAPS 45)
# ==========================================================================
def _o(cx, cy, r, fill=False):
    f = "#000" if fill else "none"
    return (f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="{f}" stroke="#000" '
            f'stroke-width="{0 if fill else 2}"/>')


R13.NEW_ICONS.update({
    "trophy": (_p("M7.5 4h9v5.5a4.5 4.5 0 0 1-9 0z") + _p("M7.5 6H5a2.5 2.5 0 0 0 3 4.3")
               + _p("M16.5 6H19a2.5 2.5 0 0 1-3 4.3") + _p("M12 14v4M8.5 20h7")),
    "lock": (_p("M6.5 11h11a1 1 0 0 1 1 1v7a1 1 0 0 1-1 1h-11a1 1 0 0 1-1-1v-7a1 1 0 0 1 1-1z")
             + _p("M8.5 11V8a3.5 3.5 0 0 1 7 0v3")),
    "heart": _p("M12 19.5s-7.5-4.4-7.5-9.6A4 4 0 0 1 12 7.6a4 4 0 0 1 7.5 2.3c0 5.2-7.5 9.6-7.5 9.6z"),
    "spark": (_p("M12 3.5l1.9 6.6 6.6 1.9-6.6 1.9L12 20.5l-1.9-6.6L3.5 12l6.6-1.9z")
              + _p("M19 3.5v3M17.5 5h3")),
    "smile": (_o(12, 12, 8.5) + _p("M8.5 14a4 4 0 0 0 7 0") + _o(9.2, 10, 1.2, True)
              + _o(14.8, 10, 1.2, True)),
    "key": (_o(7.5, 16.5, 3.5) + _p("M10 14L19.5 4.5M16 8l2.5 2.5M13.5 10.5l2 2")),
    "secret": (_p("M9.2 9.2a2.9 2.9 0 1 1 4.3 2.5c-1 .6-1.5 1.2-1.5 2.3v.5") + _o(12, 18, 1.2, True)),
})


# ==========================================================================
# the catalogue: runtime/widgets.py's ACHIEVEMENTS, each with how it is found
# ==========================================================================
ACH = (("first_open", "First Steps", "app", False, "Open a cart"),
       ("first_run", "Lift Off!", "run", False, "Run a game"),
       ("first_paint", "Little Artist", "paint", False, "Save a drawing"),
       ("first_map", "Map Maker", "map", False, "Save a map"),
       ("first_code", "Code Wizard", "code", False, "Save some code"),
       ("play_five", "Cart Explorer", "star", False, "Play 5 carts"),
       ("toolbox", "Toolbox Master", "settings", False, "Use 3 editors"),
       ("decorator", "Home Decorator", "heart", False, "Pick a wallpaper"),
       ("konami", "Secret Coder", "spark", True, "Knew the code"),
       ("clock_tinker", "Time Traveler", "smile", True, "Tapped the clock"),
       ("secret_door", "Secret Finder", "key", True, "Knocked on Settings"))
FOUND = {
    "empty": (),
    "few": ("first_open", "first_run", "first_paint"),
    "many": ("first_open", "first_run", "first_paint", "first_map", "first_code", "play_five",
             "toolbox", "konami", "clock_tinker"),
}
BOARD = {"tdeck": "T-Deck Plus", "s3": "JC3248W535", "p4": "ESP32-P4 7B"}
VERSION = "Version 0.8 · Stable"


def split(found):
    got = [a for a in ACH if a[0] in found]
    left = [a for a in ACH if a[0] not in found]
    return got, left


# ==========================================================================
# the glass: a handheld page at 320x240 or the S3's 480x320, the desk
# ==========================================================================
class Glass:
    """A handheld's metrics: the T-Deck's keys and hint strip, or the
    Guition S3's touch glass with no strip and rows at its tap floor."""

    def __init__(self, name, W, H, touch):
        self.name, self.W, self.H, self.touch = name, W, H, touch
        self.foot = H if touch else 218          # the content's foot
        self.row = 28 if touch else 20
        self.cols = 4 if W >= 480 else 3
        self.pic = 80 if W >= 480 else 64


TDECK = Glass("tdeck", 320, 240, False)
S3 = Glass("s3", 480, 320, True)


def hh_page(T, G, title):
    cv = M.Cv(G.W, G.H, T["bg"], k=1, aa=T["render"]["shapes"] == "smooth")
    M.hh_bar(cv, T, title=title)
    return cv


def hh_bar_over(cv, T, title):
    """The bar again over a page that scrolled under it."""
    cv.rect(0, 0, cv.W, 28, T["bg"])
    M.hh_bar(cv, T, title=title)


def hints(cv, T, G, items):
    if not G.touch:
        R13.hh_hints(cv, T, items)


def foot_fade(cv, T, G, h=16):
    """`ui.end()`'s fade where the page runs on under the foot."""
    R13.fade(cv, T, 0, G.foot - h, G.W, h)
    if G.foot < G.H:
        cv.rect(0, G.foot, G.W, G.H - G.foot, T["bg"])


def scrim(cv, G):
    cv.rect(0, 28, G.W, G.foot - 28, "#000000", 0.4)


def contrast(T):
    return T["key"] == "contrast"


# ==========================================================================
# a badge: `ui.card` with its `icon` tile, veiled while it is locked
# ==========================================================================
def tile(T, slot, n, got, r):
    """The card's picture: the icon on a tile tinted from `value` (a card's
    `icon`), or, locked, on `sunk` in the disabled ink (GAPS 46)."""
    if contrast(T):
        fill = T["value"] if got else T["bg"]
        ink = T["value_ink"] if got else T["dim"]
    elif got:
        fill = mix(T["value"], T["surface"], 0.82 if T["mode"] == "light" else 0.7)
        ink = T["value"]
    else:
        fill = T["sunk"] if T["mode"] == "light" else mix(T["raised"], T["bg"], 0.5)
        ink = R13.DISABLED[T["mode"]]
    tmp = M.Cv(n, n, fill)
    s = n // 2
    sicon(tmp, T, slot, (n - s) // 2, (n - s) // 2, s, ink)
    if contrast(T) and not got:
        tmp.ring(0, 0, n, n, r, 2, T["dim"])
    return tmp.image()


def badge_card(cv, T, a, got, x, y, n, focus, desk=False):
    aid, title, slot, hidden, how = a
    secret = hidden and not got
    r = 10 if desk else 8
    im = tile(T, "secret" if secret else slot, n, got, r)
    if focus:
        M.focus_mark(cv, T, (x, y, n, n, r), "card", image=im)
    else:
        R13.paste_rect(cv, im, x, y, n, n, r)
        if not contrast(T):
            cv.ring(x, y, n, n, r, 1, R13.edge(T))
    if not got:
        o = 16 if desk else 11
        R13.badge(cv, T, x + n - o, y + o, "lock", desk, T["dim"])
    ft = F(T, "row", DK) if desk else Face(T["fam"], 12, T["w_semi"], 2)
    fs = F(T, "meta", DK) if desk else Face(T["fam"], 11, T["w_body"], 2)
    name = "Secret" if secret else title
    sub = "Keep exploring" if secret else how
    mw = n + (28 if desk else 40)
    cv.text(x + n // 2, y + n + (24 if desk else 16), name, ft, T["text"], align="center", maxw=mw)
    cv.text(x + n // 2, y + n + (44 if desk else 29), sub, fs, T["dim"], align="center", maxw=mw)


def badge_grid(cv, T, items, got, x0, y, pitch, n, cols, focus_id, row_h, desk=False, stop=None):
    for i, a in enumerate(items):
        if stop is not None and y + (i // cols) * row_h >= stop:
            continue
        badge_card(cv, T, a, got, x0 + (i % cols) * pitch, y + (i // cols) * row_h, n,
                   a[0] == focus_id, desk)
    rows = (len(items) + cols - 1) // cols
    return y + rows * row_h


def head(n):
    return ("None found yet" if n == 0 else
            "All %d found!" % len(ACH) if n == len(ACH) else
            "%d of %d found" % (n, len(ACH)))


# ==========================================================================
# Achievements on a handheld
# ==========================================================================
def achievements(T, G=TDECK, state="many", scroll=0, focus=None):
    if scroll == "end":
        end = achievements(T, G, state, 0, focus).end
        scroll = end + 6 - G.foot
    found = FOUND[state]
    got, left = split(found)
    cv = hh_page(T, G, "Achievements")
    m = 16
    y = 34 - scroll
    R13.progress(cv, T, m, y, G.W - 2 * m, head(len(got)), len(got), len(ACH))
    y += 40
    n, cols = G.pic, G.cols
    pitch = (G.W - 2 * m - n) // (cols - 1)
    row_h = n + 40
    if not got:
        fl = Face(T["fam"], 12, T["w_body"], 2)
        cv.text(m, y + 4, "Play, draw and make things", fl, T["dim"])
        cv.text(m, y + 19, "to find them.", fl, T["dim"])
        y += 36
    else:
        R13.caption(cv, T, m, y + 6, "Found")
        y = badge_grid(cv, T, got, True, m, y + 18, pitch, n, cols, focus, row_h)
        y += 4
    R13.caption(cv, T, m, y + 6, "To find")
    end = badge_grid(cv, T, left, False, m, y + 18, pitch, n, cols, focus, row_h) - 10
    if end > G.foot:
        foot_fade(cv, T, G)
    elif not G.touch:
        cv.rect(0, 218, G.W, 22, T["bg"])
    hh_bar_over(cv, T, "Achievements")
    hints(cv, T, G, (("Esc", "Back"),))
    cv.end = end
    return cv


# ==========================================================================
# Achievements on the desk: a page of the Settings window
# ==========================================================================
DESK_WIN = (226, 52, 680, 536)


def settings_window(T, title, chips=(("settings", "Settings"), ("folder", "Files"))):
    """The Settings window with a page open: the chevron, the app's icon
    and the page's title in the title strip (step 27a), on the strip's own
    fill whatever the theme's header style."""
    cv = R13.desk(T, chips)
    x, y, w, h = DESK_WIN
    top = M.window(cv, T, x, y, w, h, "", "none", True)
    hh = T["win"]["h"]
    head = "#%02x%02x%02x" % cv.img.getpixel((cv.L(x + w - 160), cv.L(y + hh // 2)))
    cv.rect(x + 10, y + 6, 240, hh - 12, head)
    ink = R8.title_ink(T, True)
    sicon(cv, T, "back", x + 12, y + (hh - 20) // 2, 20, ink)
    sicon(cv, T, "settings", x + 42, y + (hh - 20) // 2, 20, ink)
    ft = F(T, "wtitle", DK)
    cv.text(x + 72, M.mid_base(ft, y, hh), title, ft, ink)
    return cv, top


def achievements_desk(T, state="many"):
    found = FOUND[state]
    got, left = split(found)
    cv, top = settings_window(T, "Achievements")
    x, y, w, h = DESK_WIN
    body = T["win"]["body"] or T["surface"]
    fy = y + h - 44
    cv.set_clip(x, top, w, fy - top)
    R13.progress(cv, T, x + 32, top + 14, w - 64, head(len(got)), len(got), len(ACH), desk=True)
    n, cols = 96, 5
    pitch = (w - 64 - n) // (cols - 1)
    yy = top + 76
    R13.caption(cv, T, x + 32, yy, "Found", desk=True)
    yy = badge_grid(cv, T, got, True, x + 32, yy + 16, pitch, n, cols, "play_five", n + 64,
                    desk=True)
    R13.caption(cv, T, x + 32, yy + 4, "To find", desk=True)
    badge_grid(cv, T, left, False, x + 32, yy + 20, pitch, n, cols, None, n + 64, desk=True,
               stop=fy - 30)
    cv.clip = None
    R13.fade(cv, T, x + 8, fy - 30, w - 16, 30, body)
    cv.rect(x + 8, fy, w - 16, 36, body)
    R13.desk_foot(cv, T, x, fy, w, "Cart Explorer · Play 5 carts · found",
                  hint=(("Esc", "Back"),))
    return cv


# ==========================================================================
# the unlock: the system's banner under the bar (GAPS 47)
# ==========================================================================
def unlock_banner(cv, T, a, cx=None, y=34, desk=False, secret=False, xr=None):
    aid, title, slot, hidden, how = a
    fh = F(T, "meta", DK) if desk else Face(T["fam"], 11, T["w_semi"], 2)
    fn = Face(T["fam"], 17 if desk else 14, T["w_semi"], 4)
    n = 44 if desk else 32
    pad = 10 if desk else 6
    head_t = "You found a secret!" if secret else "Achievement unlocked"
    w = max(fh.width(head_t), fn.width(title)) + n + 3 * pad + (14 if desk else 10)
    h = n + 2 * pad
    x = (xr - w) if xr is not None else cx - w // 2
    r = 12 if desk else 10
    R13.card_surface(cv, T, x, y, w, h, r)
    if contrast(T):
        cv.ring(x, y, w, h, r, 2, T["text"])
    R13.paste_rect(cv, tile(T, slot, n, True, 8), x + pad, y + pad, n, n, 8 if desk else 6)
    tx = x + pad + n + pad + 2
    cv.text(tx, y + h // 2 - (3 if desk else 2), head_t, fh,
            T["text"] if contrast(T) else T["value"])
    cv.text(tx, y + h // 2 + (19 if desk else 14), title, fn, T["text"])
    return x, y, w, h


def confetti(cv, box, seed=0):
    """A secret's celebration round its banner: sparks and bits in the
    MOY64 colours, drifting down (cosmetic; the colours are content)."""
    x, y, w, h = box
    cols = [P64[R13.N[c]] for c in ("red", "yellow", "green", "blue", "pink", "orange")]
    for k in range(22):
        sx = x - 26 + (k * 53 + seed) % (w + 52)
        sy = y - 4 + (k * 37 + k * k) % (h + 34)
        if x - 4 < sx < x + w + 4 and y - 4 < sy < y + h + 4:
            continue
        c = cols[k % len(cols)]
        if k % 3 == 0:
            sicon(cv, None, "spark", sx - 5, sy - 5, 10, c)
        else:
            cv.rrect(sx, sy, 4 if k % 2 else 3, 3 if k % 2 else 5, 1, c)


def unlock(T, G=TDECK, secret=False):
    cv = M.hh_home(T) if G is TDECK else settings_about(T, G, focus=None)
    a = ACH[8] if secret else ACH[3]
    box = unlock_banner(cv, T, a, cx=G.W // 2, secret=secret)
    if secret:
        confetti(cv, box)
    return cv


def unlock_desk(T):
    cv = M.desktop(T)
    unlock_banner(cv, T, ACH[3], y=52, desk=True, xr=1024 - 16)
    return cv


# ==========================================================================
# Settings' About group, where Achievements and Licences open
# ==========================================================================
def settings_about(T, G=TDECK, focus=4):
    cv = hh_page(T, G, "Settings")
    fl = Face(T["fam"], 13, T["w_med"], 2)
    fv = F(T, "status", HH)
    rh = G.row
    about = (("Firmware", "info", "0.8 · Stable"), ("Board", "info", BOARD[G.name]),
             ("Storage", "info", "1.2 MB free"), ("Battery", "info", "82%"),
             ("Achievements", "link", "9 of 11"), ("Licences", "link", ""))
    up = (("Channel", "cycle", "Stable"), ("Update online", "link", ""))
    y = G.foot - 8 - rh * len(about)
    if 50 + 22 + rh * len(up) <= y:
        y = 50 + 22 + rh * len(up)
    cv.set_clip(0, 28, G.W, G.foot - 28)
    uy = y - 22 - rh * len(up)
    R11.rows(cv, T, 6, uy, G.W - 12, rh, up, None, fl, fv, False)
    R13.caption(cv, T, 16, uy - 5, "Updates")
    R13.caption(cv, T, 16, y - 5, "About")
    R11.rows(cv, T, 6, y, G.W - 12, rh, about, focus, fl, fv, False)
    cv.clip = None
    hh_bar_over(cv, T, "Settings")
    hints(cv, T, G, (("enter", "Open"), ("Esc", "Back")))
    return cv


# ==========================================================================
# About: the system menu's card over the washed content
# ==========================================================================
def about_card(cv, T, cx, top, w, board, update=False, desk=False, focus=0):
    s = 2 if desk else 1
    n = 32 * s
    pad = 12 * s
    rh = 34 if desk else 22
    ft = Face(T["fam"], 30 if desk else 19, T["w_bold"], 4)
    fm = F(T, "meta", DK) if desk else F(T, "status", HH)
    h = pad + n + (52 if desk else 30) + (2 * 22 if desk else 2 * 15) + 10 * s + 2 * rh + pad // 2
    if desk:
        h += 40
    x = cx - w // 2
    r = 14 if desk else 10
    R13.card_surface(cv, T, x, top, w, h, r)
    if contrast(T):
        cv.ring(x, top, w, h, r, 2, T["text"])
    y = top + pad
    M.moy(cv, cx - n // 2, y, n, T["value"], T["surface"])
    y += n + (40 if desk else 24)
    cv.text(cx, y, "moybyte", ft, T["text"], align="center")
    y += 26 if desk else 17
    cv.text(cx, y, VERSION, fm, T["dim"], align="center")
    y += 22 if desk else 15
    cv.text(cx, y, board, fm, T["dim"], align="center")
    y += 10 * s + (6 if desk else 2)
    fl = F(T, "row", DK) if desk else Face(T["fam"], 13, T["w_med"], 2)
    fv = F(T, "meta", DK) if desk else F(T, "status", HH)
    rx, rw = x + (8 if desk else 6), w - (16 if desk else 12)
    cv.rect(rx + 6, y - 1, rw - 12, 1, T["line"])
    for i, (label, val, slot) in enumerate(
            (("Updates", "0.9 is ready" if update else "Up to date", "getcarts" if update else "check"),
             ("Licences", "", None))):
        ry = y + i * rh
        if i:
            cv.rect(rx + 6, ry, rw - 12, 1, T["line"])
        ink = M.focus_mark(cv, T, (rx, ry + 1, rw, rh - 2, 6), "part") if i == focus else None
        cv.text(rx + (14 if desk else 10), M.mid_base(fl, ry, rh), label, fl, ink or T["text"])
        xr = rx + rw - (14 if desk else 10)
        base = M.mid_base(fv, ry, rh)
        cw = cv.text(xr, base, "›", fv, ink or T["dim"], align="right")
        if val:
            vink = ink or (T["value"] if update and i == 0 and not contrast(T) else T["dim"])
            vw = cv.text(xr - cw - (8 if desk else 5), base, val, fv, vink, align="right")
            if slot:
                si = 18 if desk else 14
                sicon(cv, T, slot, xr - cw - vw - (8 if desk else 5) - si - (6 if desk else 4),
                      ry + (rh - si) // 2, si, vink)
    if desk:
        fy = y + 2 * rh + 8
        R11.hints(cv, T, (("Esc", "Close"),), fy + 26,
                  x=cx - R11.hints_width(T, (("Esc", "Close"),), True) // 2, bg=T["surface"], desk=True)
    return h


def about(T, G=TDECK, update=False):
    if G is TDECK:
        cv = M.hh_home(T)
    else:
        cv = settings_about(T, G, focus=None)
    scrim(cv, G)
    w = 248 if G is TDECK else 272
    top = 36 if G is TDECK else 54
    about_card(cv, T, G.W // 2, top, w, BOARD[G.name], update)
    if not G.touch:
        cv.rect(0, 218, 320, 22, T["bg"])
        R13.hh_hints(cv, T, (("enter", "Open"), ("Esc", "Close")))
    return cv


def about_desk(T, update=False):
    cv = M.desktop(T)
    cv.rect(0, 40, 1024, 560, "#000000", 0.4)
    about_card(cv, T, 512, 92, 420, BOARD["p4"], update, desk=True)
    return cv


# ==========================================================================
# Licences: a page of Settings, a licence's text a page under it
# ==========================================================================
MOYBYTE_LIC = (("The console", "FSL-1.1-MIT"), ("Cart format", "MIT"))
INSIDE = (("MicroPython", "MIT"), ("Lua 5.4", "MIT"), ("WAMR", "Apache-2.0"), ("Inter", "OFL-1.1"),
          ("Day Sans", "OFL-1.1"), ("Atkinson Hyperlegible", "OFL-1.1"),
          ("JetBrains Mono", "OFL-1.1"), ("Pixelarticons", "MIT"), ("PICO-8 palette", "CC0"))
LEAD = "Moybyte is free to use, change and share. Every release becomes MIT after two years."
MIT = ("Copyright (c) 2013-2024 Damien P. George. Permission is hereby granted, free of charge, to any "
       "person obtaining a copy of this software and associated documentation files (the "
       "\"Software\"), to deal in the Software without restriction, including without limitation "
       "the rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell "
       "copies of the Software, and to permit persons to whom the Software is furnished to do so, "
       "subject to the following conditions: The above copyright notice and this permission "
       "notice shall be included in all copies or substantial portions of the Software. THE "
       "SOFTWARE IS PROVIDED \"AS IS\", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED.")


def wrap(face, text, w):
    out, line = [], ""
    for word in text.split():
        t = (line + " " + word).strip()
        if line and face.width(t) > w:
            out.append(line)
            line = word
        else:
            line = t
    if line:
        out.append(line)
    return out


def licences(T, G=TDECK):
    cv = hh_page(T, G, "Licences")
    fl = Face(T["fam"], 13, T["w_med"], 2)
    fv = F(T, "status", HH)
    fb = Face(T["fam"], 12, T["w_body"], 2)
    rh = G.row
    cv.set_clip(0, 28, G.W, G.foot - 28)
    y = 44
    for ln in wrap(fb, LEAD, G.W - 32):
        cv.text(16, y, ln, fb, T["dim"])
        y += 15
    y += 6
    R13.caption(cv, T, 16, y, "Moybyte")
    y = R11.rows(cv, T, 6, y + 5, G.W - 12, rh, [(a, "link", b) for a, b in MOYBYTE_LIC], 0,
                 fl, fv, False)
    y += 17
    R13.caption(cv, T, 16, y, "Made with")
    R11.rows(cv, T, 6, y + 5, G.W - 12, rh, [(a, "link", b) for a, b in INSIDE], None,
             fl, fv, False)
    cv.clip = None
    foot_fade(cv, T, G)
    hh_bar_over(cv, T, "Licences")
    hints(cv, T, G, (("enter", "Read"), ("Esc", "Back")))
    return cv


def licence(T, G=TDECK):
    cv = hh_page(T, G, "MicroPython")
    fb = Face(T["fam"], 12, T["w_body"], 2)
    cv.set_clip(0, 28, G.W, G.foot - 28)
    R13.caption(cv, T, 16, 44, "MIT License")
    y = 62
    for ln in wrap(fb, MIT, G.W - 32):
        cv.text(16, y, ln, fb, T["text"])
        y += 16
    cv.clip = None
    foot_fade(cv, T, G)
    hints(cv, T, G, ((("↑", "↓"), "Scroll"), ("Esc", "Back")))
    return cv


def licences_desk(T):
    cv, top = settings_window(T, "Licences")
    x, y, w, h = DESK_WIN
    body = T["win"]["body"] or T["surface"]
    fy = y + h - 44
    fl, fv, fb = F(T, "row", DK), F(T, "meta", DK), F(T, "meta", DK)
    lw = 268
    cv.set_clip(x, top, w, fy - top)
    yy = top + 26
    R13.caption(cv, T, x + 24, yy, "Moybyte", desk=True)
    yy = R11.rows(cv, T, x + 12, yy + 8, lw, 28, [(a, "info", b) for a, b in MOYBYTE_LIC], None,
                  fl, fv, True, group=False)
    yy += 26
    R13.caption(cv, T, x + 24, yy, "Made with", desk=True)
    R11.rows(cv, T, x + 12, yy + 8, lw, 28, [(a, "info", b) for a, b in INSIDE], 0,
             fl, fv, True, group=False)
    px = x + lw + 36
    cv.rect(px - 12, top, 1, fy - top, T["line"])
    ft = Face(T["fam"], 20, T["w_semi"], 4)
    cv.text(px + 8, top + 34, "MicroPython", ft, T["text"])
    cv.text(px + 8, top + 56, "MIT License", fv, T["dim"])
    ty = top + 88
    for ln in wrap(fb, MIT, x + w - px - 36):
        cv.text(px + 8, ty, ln, fb, T["text"])
        ty += 21
    cv.clip = None
    R13.fade(cv, T, x + 8, fy - 30, w - 16, 30, body)
    cv.rect(x + 8, fy, w - 16, 36, body)
    R13.desk_foot(cv, T, x, fy, w, "MicroPython · MIT",
                  hint=((("↑", "↓"), "Move"), ("Esc", "Back")))
    return cv


# ==========================================================================
# the pictures
# ==========================================================================
HANDHELD = (
    (lambda T: achievements(T, TDECK, "empty", focus="first_open"), "achievements-empty",
     "Achievements with none found: the progress row, a line on how they are found, every badge "
     "veiled under To find, the secrets named Secret; focus on First Steps"),
    (lambda T: achievements(T, TDECK, "few", focus="first_paint"), "achievements-few",
     "Achievements with three found: Found and To find as card grids, a found badge its icon on "
     "a tile tinted from value, a locked one veiled with a lock badge; focus on Little Artist"),
    (lambda T: achievements(T, TDECK, "many", focus="first_run"), "achievements",
     "Achievements with nine of eleven found, two secrets among them; focus on Lift Off!"),
    (lambda T: achievements(T, TDECK, "many", scroll="end", focus="decorator"),
     "achievements-scrolled", "Achievements scrolled to the end: the last found row under the "
     "bar, To find with Home Decorator and an unfound secret; focus on Home Decorator"),
    (lambda T: unlock(T), "achievement-unlocked",
     "An achievement unlocked over the home: the system's banner under the bar, the badge's "
     "tile, what happened and its name"),
    (lambda T: unlock(T, secret=True), "achievement-secret",
     "A secret found (the Konami code): the banner says so, with confetti round it"),
    (lambda T: settings_about(T), "settings-about",
     "Settings scrolled to its About group: the facts, then Achievements (9 of 11) and Licences "
     "as rows; focus on Achievements"),
    (lambda T: about(T), "about",
     "About from the system menu over the washed home: Moy, the wordmark, the version and the "
     "board, then Updates (up to date) and Licences as rows; focus on Updates"),
    (lambda T: about(T, update=True), "about-update",
     "About with an update ready: Updates says 0.9 is ready in value"),
    (lambda T: licences(T), "licences",
     "Licences: a line on Moybyte's licence, then Moybyte's parts and what it is made with, "
     "each with its licence; focus on The console"),
    (lambda T: licence(T), "licence",
     "A licence's text, MicroPython's MIT licence, scrolling under the bar"),
)
TOUCH = (
    (lambda T: achievements(T, S3, "many", focus="first_run"), "achievements-s3",
     "Achievements on the Guition S3 (480x320, touch): four badges a row at 80 px, no hint "
     "strip; focus on Lift Off!"),
    (lambda T: unlock(T, S3), "achievement-unlocked-s3",
     "An achievement unlocked on the Guition S3, over Settings"),
    (lambda T: settings_about(T, S3), "settings-about-s3",
     "Settings' About group on the Guition S3: rows at the tap floor, no hint strip"),
    (lambda T: about(T, S3), "about-s3",
     "About on the Guition S3 over washed Settings, the wash to the glass's foot"),
)
DESKS = (
    (achievements_desk, "achievements",
     "Achievements as a page of the Settings window: five badges a row at 96 px, the focused "
     "badge's name, how it is found and its state in the foot; focus on Cart Explorer"),
    (unlock_desk, "achievement-unlocked",
     "An achievement unlocked on the desk: the banner at the top right under the bar"),
    (about_desk, "about",
     "About from the system menu over the washed desk: Moy at 64 px, the wordmark, version, "
     "board, Updates and Licences, Esc Close"),
    (lambda T: about_desk(T, update=True), "about-update", "About on the desk with an update ready"),
    (licences_desk, "licences",
     "Licences as a page of the Settings window: the parts at the left, the focused one's text "
     "at the right"),
)
CONTRAST_SCENES = (
    ("handheld", HANDHELD[2]), ("handheld", HANDHELD[4]), ("handheld", HANDHELD[7]),
    ("desktop", DESKS[0]),
)


def scenes():
    for T, tag in ((LIGHT, "moy-light"), (DARK, "moy-dark")):
        n = T["name"]
        for fn, slug, what in HANDHELD:
            fn(T).save(f"{tag}_handheld_{slug}", f"{what}; {n}", x3=True)
        for fn, slug, what in TOUCH:
            fn(T).save(f"{tag}_handheld_{slug}", f"{what}; {n}")
        for fn, slug, what in DESKS:
            fn(T).save(f"{tag}_desktop_{slug}", f"{what}; {n}")
    for kind, (fn, slug, what) in CONTRAST_SCENES:
        fn(CONTRAST).save(f"contrast-dark_{kind}_{slug}", f"{what}; Contrast (dark)",
                          x3=kind == "handheld")


if __name__ == "__main__":
    scenes()
