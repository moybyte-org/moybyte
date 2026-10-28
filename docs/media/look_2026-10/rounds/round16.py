#!/usr/bin/env python3
"""Round 16 of the 2026-10 look, over round 13: Achievements and About
(`docs/look_2026-10.md` step 32).

- Achievements is a page of Settings (its About group's Achievements row):
  a progress row, then the found badges and the ones still to find as card
  grids in fixed columns, a badge's picture its icon on a tile tinted from
  `value`, a locked one veiled with a lock badge and a secret one named
  "Secret" until found. Enter or a tap opens a badge's popover. The unlock
  is the system's banner, centred at the top, a secret's with confetti.
- About is the system menu's card over the washed content: Moy, the
  wordmark, the version and the board, then Updates and Licences as rows.
  Licences is a page of Settings (the About group's last row), a licence's
  text a page under it; on the desk the two sit side by side.
- Each is drawn on the T-Deck (320x240), the Guition S3 (480x320, touch: no
  hint strip, rows at the tap floor) and the 1024x600 desk, in Moy Day and
  Night, and in Contrast.

A layer of the target, not an entry point: `../draw.py` draws every picture.
"""
import math
import os
import sys

from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import round13 as R13  # noqa: E402

M, R11, R8, R7 = R13.M, R13.R11, R13.R8, R13.R7
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
# the catalogue: runtime/widgets.py's ACHIEVEMENTS, each with how it is
# found, in one voice (what to do)
# ==========================================================================
ACH = (("first_open", "First Steps", "app", False, "Open a cart"),
       ("first_run", "Lift Off!", "run", False, "Run a game"),
       ("first_paint", "Little Artist", "paint", False, "Save a drawing"),
       ("first_map", "Map Maker", "map", False, "Save a map"),
       ("first_code", "Code Wizard", "code", False, "Save some code"),
       ("play_five", "Cart Explorer", "star", False, "Play 5 carts"),
       ("toolbox", "Toolbox Master", "settings", False, "Use 3 editors"),
       ("decorator", "Home Decorator", "heart", False, "Pick a wallpaper"),
       ("konami", "Secret Coder", "spark", True, "Type the secret code"),
       ("clock_tinker", "Time Traveler", "smile", True, "Tap the clock 7 times"),
       ("secret_door", "Secret Finder", "key", True, "Knock on Settings 5 times"))
BY_ID = {a[0]: a for a in ACH}
FOUND = {
    "empty": (),
    "few": ("first_open", "first_run", "first_paint"),
    "many": ("first_open", "first_run", "first_paint", "first_map", "first_code", "play_five",
             "toolbox", "konami", "clock_tinker"),
    "all": tuple(a[0] for a in ACH),
}
FOUND_ON = {"first_map": "Found 12 October", "play_five": "Found 9 October"}
BOARD = {"tdeck": "T-Deck Plus", "s3": "Guition 3.5″", "p4": "Waveshare P4 7″"}
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
    Guition S3's touch glass with no strip and rows at its tap floor. A
    badge column is `col` wide, its picture `pw` x `ph`."""

    def __init__(self, name, W, H, touch, cols, pw, ph):
        self.name, self.W, self.H, self.touch = name, W, H, touch
        self.foot = H if touch else 218          # the content's foot
        self.row = 28 if touch else 20
        self.cols, self.pw, self.ph = cols, pw, ph
        self.col = (W - 32) // cols


TDECK = Glass("tdeck", 320, 240, False, 3, 84, 62)
S3 = Glass("s3", 480, 320, True, 4, 100, 72)


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


def wrap(face, text, w, lines=None):
    """Words broken to `w`; with `lines`, at most that many, an ellipsis
    after the last."""
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
    if lines and len(out) > lines:
        last = " ".join(out[lines - 1:])
        while last and face.width(last + "…") > w:
            last = last[:-1]
        out = out[:lines - 1] + [last.rstrip() + "…"]
    return out


def progress(cv, T, x, y, w, label, v, hi, desk=False):
    """`ui.progress` with `o.count` (GAPS 48): the label, no percent, a
    track in its own role (Contrast's an outline the fill keeps 3:1 or more
    against), no fill at nothing."""
    fl = F(T, "row", DK) if desk else Face(T["fam"], 13, T["w_med"], 2)
    rh = 28 if desk else 20
    cv.text(x, M.mid_base(fl, y, rh), label, fl, T["text"])
    by = y + rh + (2 if desk else 1)
    bh = 8 if desk else 6
    if contrast(T):
        cv.rrect(x, by, w, bh, bh / 2, T["bg"])
        cv.ring(x, by, w, bh, bh / 2, 1, T["dim"])
    else:
        cv.rrect(x, by, w, bh, bh / 2, mix(T["line"], T["dim"], 0.25))
    if v:
        cv.rrect(x, by, max(bh, w * v // hi), bh, bh / 2, T["value"])
    return by + bh


# ==========================================================================
# a badge: `ui.card` with its `icon` tile, veiled while it is locked
# ==========================================================================
def tile(T, slot, w, h, got, r):
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
    tmp = M.Cv(w, h, fill)
    s = min(w, h) * 9 // 16
    sicon(tmp, T, slot, (w - s) // 2, (h - s) // 2, s, ink)
    if contrast(T) and not got:
        tmp.ring(0, 0, w, h, r, 2, T["dim"])
    return tmp.image()


def lock_badge(cv, T, x, y, desk=False):
    """A card's badge (GAPS 34) for a locked badge: the lock in Get Carts'
    badge ink, `value`, on a disc that reads on the veiled tile: `surface`
    in a light mode, `raised` ringed in a dark one."""
    d = 26 if desk else 18
    dark = T["mode"] == "dark"
    M.soft(cv, T, x - d // 2, y - d // 2, d, d, d / 2, 0.4)
    fill = T["bg"] if contrast(T) else (T["raised"] if dark else T["surface"])
    cv.shape(x - d // 2, y - d // 2, d, d,
             lambda dr, s: dr.ellipse((0, 0, d * s - 1, d * s - 1), fill=255), fill)
    if dark:
        cv.ring(x - d // 2, y - d // 2, d, d, d / 2, 1,
                T["dim"] if contrast(T) else mix(T["line"], T["dim"], 0.55))
    s = 16 if desk else 12
    sicon(cv, T, "lock", x - s // 2, y - s // 2, s, T["value"])


def faces(T, desk):
    if desk:
        return F(T, "row", DK), F(T, "meta", DK), 20
    return Face(T["fam"], 12, T["w_semi"], 2), Face(T["fam"], 11, T["w_body"], 2), 13


def caption_lines(T, a, got, colw, desk):
    """A badge's name, at most two lines broken at a word, and its how-to
    line, ellipsized to the column."""
    ft, fs, _lh = faces(T, desk)
    secret = a[3] and not got
    name = wrap(ft, "Secret" if secret else a[1], colw - 8, 2)
    how = wrap(fs, "Keep exploring" if secret else a[4], colw - 8, 1)[0]
    return name, how


def card_h(G_ph, n_lines, desk):
    lh = 20 if desk else 13
    return G_ph + (16 if desk else 14) + (n_lines - 1) * lh + lh + (16 if desk else 8)


def card_focus(cv, T, im, x, y, w, h, r):
    """The theme's focus mark on a card whose picture is not square: the
    lift grows and raises the picture, a ring rings it."""
    fo = T["focus"]
    st = fo["card"] or fo["style"]
    if st == "lift":
        lf = fo["lift"]
        d, up = lf["grow"], lf["rise"]
        bx, by, bw, bh = x - d, y - d - up, w + 2 * d, h + 2 * d
        cv.shadow(bx, by, bw, bh, r, ("soft", "#000000", 0.7 if T["mode"] == "dark" else 0.32, 10, 5))
        R13.paste_rect(cv, im, bx, by, bw, bh, r + 1)
        if lf["rim"]:
            rc, ra = lf["rim"]
            cv.ring(bx - 1, by - 1, bw + 2, bh + 2, r + 2, 1, T.get(rc, rc), ra)
        return
    if st == "ring":
        M.ring_mark(cv, T, x, y, w, h, r)
    else:
        M.focus_mark(cv, T, (x, y, w, h, r), "card")
    R13.paste_rect(cv, im, x, y, w, h, r)


def badge_card(cv, T, a, got, x, y, colw, pw, ph, focus, desk=False, current=False):
    secret = a[3] and not got
    r = 10 if desk else 8
    px = x + (colw - pw) // 2
    im = tile(T, "secret" if secret else a[2], pw, ph, got, r)
    if current:
        M.current_mark(cv, T, px - 4, y - 4, pw + 8, ph + 8, r + 4)
    if focus:
        card_focus(cv, T, im, px, y, pw, ph, r)
    else:
        R13.paste_rect(cv, im, px, y, pw, ph, r)
        if not contrast(T):
            cv.ring(px, y, pw, ph, r, 1, R13.edge(T))
    if not got:
        o = 16 if desk else 11
        lock_badge(cv, T, px + pw - o, y + o, desk)
    ft, fs, lh = faces(T, desk)
    name, how = caption_lines(T, a, got, colw, desk)
    cx = x + colw // 2
    by = y + ph + (22 if desk else 15)
    for ln in name:
        cv.text(cx, by, ln, ft, T["text"], align="center")
        by += lh
    cv.text(cx, by, how, fs, T["dim"], align="center")


def badge_rows(T, items, got, cols, colw, ph, desk):
    """The grid's rows: (items, height), a row as tall as its tallest
    caption."""
    out = []
    for i in range(0, len(items), cols):
        row = items[i:i + cols]
        n = max(len(caption_lines(T, a, got, colw, desk)[0]) for a in row)
        out.append((row, card_h(ph, n, desk)))
    return out


def badge_grid(cv, T, items, got, x0, y, colw, pw, ph, cols, focus_id, desk=False,
               stop=None, current_id=None):
    for row, h in badge_rows(T, items, got, cols, colw, ph, desk):
        if stop is None or y < stop:
            for i, a in enumerate(row):
                badge_card(cv, T, a, got, x0 + i * colw, y, colw, pw, ph, a[0] == focus_id, desk,
                           current=a[0] == current_id)
        y += h
    return y


def head(n):
    return ("None found yet" if n == 0 else
            "All %d found!" % len(ACH) if n == len(ACH) else
            "%d of %d found" % (n, len(ACH)))


def where(T, G, state, card):
    """Where badge `card`'s picture lands on the unscrolled handheld page."""
    return layout(T, G, state)[1].get(card)


def layout(T, G, state):
    """(the page's end, {id: (x, y)}) on a handheld, unscrolled."""
    got, left = split(FOUND[state])
    y = 34 + 20 + 1 + 6 + 4 + (22 if not got else 0)
    pos = {}
    for items, ok in ((got, True), (left, False)):
        if not items:
            continue
        y += 24
        for row, h in badge_rows(T, items, ok, G.cols, G.col, G.ph, False):
            for i, a in enumerate(row):
                pos[a[0]] = (16 + i * G.col, y)
            y += h
    return y, pos


# ==========================================================================
# Achievements on a handheld
# ==========================================================================
def achievements(T, G=TDECK, state="many", scroll=0, focus=None, current=None):
    if scroll == "end":
        scroll = layout(T, G, state)[0] + 4 - G.foot
    got, left = split(FOUND[state])
    cv = hh_page(T, G, "Achievements")
    m = 16
    y = 34 - scroll
    y = progress(cv, T, m, y, G.W - 2 * m, head(len(got)), len(got), len(ACH)) + 4
    if not got:
        fl = Face(T["fam"], 12, T["w_body"], 2)
        cv.text(m, y + 16, "Play, draw and make things to find them.", fl, T["dim"])
        y += 22
    for cap, items, ok in (("Found", got, True), ("To find", left, False)):
        if not items:
            continue
        R13.caption(cv, T, m, y + 12, cap)
        y = badge_grid(cv, T, items, ok, m, y + 24, G.col, G.pw, G.ph, G.cols, focus,
                       current_id=current)
    if y > G.foot + 2:
        foot_fade(cv, T, G)
    elif not G.touch:
        cv.rect(0, 218, G.W, 22, T["bg"])
    hh_bar_over(cv, T, "Achievements")
    hints(cv, T, G, (("enter", "Open"), ("Esc", "Back")) if current is None else
          (("Esc", "Close"),))
    return cv


def badge_popover(cv, T, a, got, x, y, w, desk=False, found_on=None, point=None):
    """A badge's popover, hung from its card (`ui.popover` holding a
    `ui.part` and `ui.label`s, no wash): the badge large, its name, how it
    is found and the day it was."""
    s = 2 if desk else 1
    pad = 10 * s
    tw, th = 64 * s, 48 * s
    h = th + 2 * pad
    r = 12 if desk else 10
    R13.card_surface(cv, T, x, y, w, h, r)
    if contrast(T):
        cv.ring(x, y, w, h, r, 2, T["text"])
    if point is not None:
        px, down = point
        ty = y + h if down else y
        d = 6 * s
        cv.shape(px - d, ty - (0 if down else d), 2 * d, d,
                 (lambda dr, k: dr.polygon(((0, 0), (2 * d * k, 0), (d * k, d * k)), fill=255))
                 if down else
                 (lambda dr, k: dr.polygon(((0, d * k), (2 * d * k, d * k), (d * k, 0)), fill=255)),
                 T["surface"])
    secret = a[3] and not got
    R13.paste_rect(cv, tile(T, "secret" if secret else a[2], tw, th, got, 8), x + pad, y + pad,
                   tw, th, 8)
    if not got:
        lock_badge(cv, T, x + pad + tw - 11 * s, y + pad + 11 * s, desk)
    tx = x + pad + tw + pad
    ft = Face(T["fam"], 14 * s if not desk else 18, T["w_semi"], 4)
    fs = F(T, "meta", DK) if desk else Face(T["fam"], 12, T["w_body"], 2)
    cv.text(tx, y + pad + (20 if desk else 13), "Secret" if secret else a[1], ft, T["text"])
    cv.text(tx, y + pad + (44 if desk else 29), "Keep exploring" if secret else a[4], fs, T["dim"])
    when = found_on if got else "Not found yet"
    cv.text(tx, y + pad + (66 if desk else 44), when, fs,
            T["value"] if got and not contrast(T) else T["dim"])
    return h


def achievements_popover(T):
    """Map Maker's popover over the page scrolled to show its row whole,
    hung above the card (the card current behind it)."""
    G = TDECK
    x, y0 = where(T, G, "many", "first_map")
    scroll = y0 - 120
    cv = achievements(T, G, "many", scroll=scroll, current="first_map")
    cx = x + G.col // 2
    badge_popover(cv, T, BY_ID["first_map"], True, 16, 120 - 8 - 68, 240,
                  found_on=FOUND_ON["first_map"], point=(cx, True))
    return cv


# ==========================================================================
# Achievements on the desk: a page of the Settings window
# ==========================================================================
DESK_WIN = (226, 52, 680, 536)
DCOLS, DCOL, DPW, DPH = 6, 104, 92, 68


def settings_window(T, title, chips=(("settings", "Settings"), ("folder", "Files"))):
    """The Settings window with a page open: the chevron, the app's icon
    and the page's title in the title strip (step 27a), on the strip's own
    fill whatever the theme's header style."""
    cv = R13.desk(T, chips)
    x, y, w, h = DESK_WIN
    top = M.window(cv, T, x, y, w, h, "", "none", True)
    hh = T["win"]["h"]
    head_c = "#%02x%02x%02x" % cv.img.getpixel((cv.L(x + w - 160), cv.L(y + hh // 2)))
    cv.rect(x + 10, y + 6, 240, hh - 12, head_c)
    ink = R8.title_ink(T, True)
    sicon(cv, T, "back", x + 12, y + (hh - 20) // 2, 20, ink)
    sicon(cv, T, "settings", x + 42, y + (hh - 20) // 2, 20, ink)
    ft = F(T, "wtitle", DK)
    cv.text(x + 72, M.mid_base(ft, y, hh), title, ft, ink)
    return cv, top


def achievements_desk(T, state="many", scroll=0, focus="play_five", pop=None):
    got, left = split(FOUND[state])
    cv, top = settings_window(T, "Achievements")
    x, y, w, h = DESK_WIN
    body = T["win"]["body"] or T["surface"]
    fy = y + h - 44
    x0 = x + (w - DCOLS * DCOL) // 2
    above = (x - 30, 0, x + w + 30, top)
    below = (x - 30, fy, x + w + 30, 600)
    keep = [(b, cv.img.crop(b)) for b in (above, below)]
    yy = top + 14 - scroll
    yy = progress(cv, T, x0 + 8, yy, DCOLS * DCOL - 16, head(len(got)), len(got), len(ACH),
                  desk=True)
    pos = {}
    for cap, items, ok in (("Found", got, True), ("To find", left, False)):
        if not items:
            continue
        R13.caption(cv, T, x0 + 8, yy + 24, cap, desk=True)
        yy += 36
        for row, rh in badge_rows(T, items, ok, DCOLS, DCOL, DPH, True):
            for i, a in enumerate(row):
                pos[a[0]] = (x0 + i * DCOL, yy)
            yy += rh
        badge_grid(cv, T, items, ok, x0, yy - sum(r[1] for r in badge_rows(
            T, items, ok, DCOLS, DCOL, DPH, True)), DCOL, DPW, DPH, DCOLS,
            None if pop else focus, desk=True, current_id=pop)
    for b, im in keep:
        cv.img.paste(im, b[:2])
    R13.fade(cv, T, x + 8, fy - 30, w - 16, 30, body)
    if scroll:
        for i in range(14):
            cv.rect(x + 8, top + 1 + i, w - 16, 1, body, (14 - i) / 14)
    cv.rect(x + 8, fy, w - 16, 36, body)
    a = BY_ID[pop or focus]
    status = "%s · %s · %s" % (a[1], a[4], "found" if a[0] in FOUND[state] else "not found yet")
    R13.desk_foot(cv, T, x, fy, w, status,
                  hint=(("Esc", "Close"),) if pop else (("enter", "Open"), ("Esc", "Back")))
    if pop:
        px, py = pos[pop]
        bw = 420
        bx = px + DCOL // 2 - 60
        badge_popover(cv, T, a, a[0] in FOUND[state], bx, py - 12 - 136, bw, desk=True,
                      point=(px + DCOL // 2, True))
    return cv


# ==========================================================================
# the unlock: the system's banner, centred at the top (GAPS 47)
# ==========================================================================
def unlock_banner(cv, T, a, cx, y, desk=False, secret=False):
    fh = F(T, "meta", DK) if desk else Face(T["fam"], 11, T["w_semi"], 2)
    fn = Face(T["fam"], 17 if desk else 14, T["w_semi"], 4)
    n = 44 if desk else 32
    pad = 10 if desk else 6
    head_t = "You found a secret!" if secret else "You found a badge!"
    w = max(fh.width(head_t), fn.width(a[1])) + n + 3 * pad + (14 if desk else 10)
    h = n + 2 * pad
    x = cx - w // 2
    r = 12 if desk else 10
    R13.card_surface(cv, T, x, y, w, h, r)
    if contrast(T):
        cv.ring(x, y, w, h, r, 2, T["text"])
    R13.paste_rect(cv, tile(T, a[2], n, n, True, 8), x + pad, y + pad, n, n, 8 if desk else 6)
    tx = x + pad + n + pad + 2
    cv.text(tx, y + h // 2 - (3 if desk else 2), head_t, fh,
            T["text"] if contrast(T) else T["value"])
    cv.text(tx, y + h // 2 + (19 if desk else 14), a[1], fn, T["text"])
    return x, y, w, h


def confetti(cv, box, s=1):
    """A secret's celebration: square bits in the MOY64 colours bursting
    from the banner's edges, all within a short reach of it (cosmetic; the
    colours are content)."""
    x, y, w, h = box
    cols = [P64[R13.N[c]] for c in ("red", "yellow", "green", "blue", "pink", "orange")]
    cx, cy = x + w / 2, y + h / 2
    for k in range(34):
        ang = k * 2.39996
        reach = (6 + (k * 7) % 12) * s
        ex = cx + math.cos(ang) * (w / 2 + reach)
        ey = cy + math.sin(ang) * (h / 2 + reach * 0.8)
        d = (3 if k % 3 else 2) * s
        cv.rect(int(ex - d / 2), int(ey - d / 2), d, d, cols[k % len(cols)])


def unlock(T, G=TDECK, secret=False):
    with M.focus_off():
        cv = M.hh_home(T) if G is TDECK else settings_about(T, G, focus=None)
    a = BY_ID["konami"] if secret else BY_ID["first_map"]
    box = unlock_banner(cv, T, a, G.W // 2, 34, secret=secret)
    if secret:
        confetti(cv, box)
    return cv


def unlock_desk(T):
    cv = M.desktop(T)
    unlock_banner(cv, T, BY_ID["first_map"], 512, 52, desk=True)
    return cv


def unlock_game(T):
    """Over a fullscreen game (no bar): top centre, between the corners a
    game's HUD keeps (Star Catcher's hearts and score)."""
    cv = M.Cv(320, 240, "#000000")
    cv.img.paste(R7.GAME, (0, 0))
    unlock_banner(cv, T, BY_ID["first_run"], 160, 6)
    return cv


def unlock_game_desk(T):
    """A fullscreen game in console mode: the banner top centre, mostly in
    the letterbox over the game."""
    cv = M.Cv(1024, 600, "#000000")
    cv.img.paste(R7.GAME.resize((640, 480), Image.NEAREST), (192, 60))
    unlock_banner(cv, T, BY_ID["first_run"], 512, 8, desk=True)
    return cv


# ==========================================================================
# Settings' About group, where Achievements and Licences open
# ==========================================================================
def settings_about(T, G=TDECK, focus=4):
    cv = hh_page(T, G, "Settings")
    fl = Face(T["fam"], 13, T["w_med"], 2)
    fv = F(T, "status", HH)
    rh = G.row
    about = (("Version", "info", "0.8 · Stable"), ("Board", "info", BOARD[G.name]),
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
def about_card(cv, T, cx, top, w, board, update=False, desk=False, focus=0, touch=False):
    s = 2 if desk else 1
    n = 32 * s
    pad = 12 * s
    rh = 34 if desk else (28 if touch else 22)
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
    elif T["mode"] == "dark":
        cv.ring(x, top, w, h, r, 1, mix(T["line"], T["text"], 0.3))
    if touch:
        R13.ibtn(cv, T, "close", x + w - 6 - 28, top + 6, 28)
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
        items = (("enter", "Open"), ("Esc", "Close"))
        R11.hints(cv, T, items, fy + 26, x=cx - R11.hints_width(T, items, True) // 2,
                  bg=T["surface"], desk=True)
    return h


def about(T, G=TDECK, update=False):
    with M.focus_off():
        cv = M.hh_home(T) if G is TDECK else settings_about(T, G, focus=None)
    scrim(cv, G)
    w = 248 if G is TDECK else 300
    top = 36 if G is TDECK else 44
    about_card(cv, T, G.W // 2, top, w, BOARD[G.name], update, touch=G.touch)
    if not G.touch:
        cv.rect(0, 218, 320, 22, T["bg"])
        R13.hh_hints(cv, T, (("enter", "Open"), ("Esc", "Close")))
    return cv


def about_desk(T, update=False):
    with M.focus_off():
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
LEAD = "Free to use and change; each release becomes MIT after two years."
MIT = ("Copyright (c) 2013-2024 Damien P. George",
       "Permission is hereby granted, free of charge, to any person obtaining a copy of this "
       "software and associated documentation files (the \"Software\"), to deal in the Software "
       "without restriction, including without limitation the rights to use, copy, modify, "
       "merge, publish, distribute, sublicense, and/or sell copies of the Software, and to permit "
       "persons to whom the Software is furnished to do so, subject to the following conditions:",
       "The above copyright notice and this permission notice shall be included in all copies or "
       "substantial portions of the Software.",
       "THE SOFTWARE IS PROVIDED \"AS IS\", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, "
       "INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR "
       "PURPOSE AND NONINFRINGEMENT.")


def text_paras(cv, T, face, paras, x, y, w, lh, gap):
    for p in paras:
        for ln in wrap(face, p, w):
            cv.text(x, y, ln, face, T["text"])
            y += lh
        y += gap
    return y


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
    R13.caption(cv, T, 16, 44, "MIT License")
    text_paras(cv, T, fb, MIT, 16, 62, G.W - 32, 16, 8)
    foot_fade(cv, T, G)
    hh_bar_over(cv, T, "MicroPython")
    hints(cv, T, G, ((("↑", "↓"), "Scroll"), ("Esc", "Back")))
    return cv


def licences_desk(T):
    cv, top = settings_window(T, "Licences")
    x, y, w, h = DESK_WIN
    body = T["win"]["body"] or T["surface"]
    fy = y + h - 44
    fl, fv, fb = F(T, "row", DK), F(T, "meta", DK), F(T, "meta", DK)
    lw = 268
    yy = top + 24
    for ln in wrap(fb, LEAD, lw - 24):
        cv.text(x + 24, yy, ln, fb, T["dim"])
        yy += 19
    yy += 14
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
    text_paras(cv, T, fb, MIT, px + 8, top + 88, x + w - px - 36, 21, 10)
    R13.fade(cv, T, x + 8, fy - 30, w - 16, 30, body)
    cv.rect(x + 8, fy, w - 16, 36, body)
    R13.desk_foot(cv, T, x, fy, w, "MicroPython · MIT",
                  hint=((("↑", "↓"), "Move"), (("PgUp", "PgDn"), "Scroll"), ("Esc", "Back")))
    return cv


# ==========================================================================
# the pictures
# ==========================================================================
HANDHELD = (
    (lambda T: achievements(T, TDECK, "empty", focus="first_open"), "achievements-empty",
     "Achievements with none found: the progress row (no fill), one line on how they are "
     "found, every badge under To find veiled with a lock, the secrets named Secret; focus on "
     "First Steps, its caption whole, the next row peeking"),
    (lambda T: achievements(T, TDECK, "few", focus="first_paint"), "achievements-few",
     "Achievements with three found: Found and To find as card grids in three 96 px columns, "
     "To find's heading with its first row peeking; focus on Little Artist"),
    (lambda T: achievements(T, TDECK, "many", focus="first_run"), "achievements",
     "Achievements with nine of eleven found, two secrets among them; focus on Lift Off!"),
    (lambda T: achievements(T, TDECK, "many", scroll="end", focus="decorator"),
     "achievements-scrolled", "Achievements scrolled to the end: the last found row under the "
     "bar, To find with Home Decorator and an unfound secret; names wrap at a word inside "
     "their columns; focus on Home Decorator"),
    (lambda T: achievements(T, TDECK, "all", focus="first_open"), "achievements-all",
     "Achievements with all eleven found: All 11 found!, the bar full, no To find"),
    (achievements_popover, "achievement-popover",
     "A badge opened (Enter or a tap): its popover hung above Map Maker, the card current "
     "behind it -- the badge large, its name, how it is found and the day it was"),
    (lambda T: unlock(T), "achievement-unlocked",
     "A badge found over the home: the system's banner centred under the bar, the badge's "
     "tile, You found a badge! and its name"),
    (lambda T: unlock(T, secret=True), "achievement-secret",
     "A secret found (the Konami code): the banner says so, confetti bursting from its edges"),
    (unlock_game, "achievement-unlocked-game",
     "A badge found over a fullscreen game (no bar): the banner top centre, between the "
     "corners the game's hearts and score keep"),
    (lambda T: settings_about(T), "settings-about",
     "Settings scrolled to its About group: Version, Board, Storage, Battery, then Achievements "
     "(9 of 11) and Licences as rows; focus on Achievements"),
    (lambda T: about(T), "about",
     "About from the system menu over the washed home (no focus under it): Moy, the wordmark, "
     "the version and the board, then Updates (up to date) and Licences; focus on Updates"),
    (lambda T: about(T, update=True), "about-update",
     "About with an update ready: Updates says 0.9 is ready in value"),
    (lambda T: licences(T), "licences",
     "Licences: Moybyte's licence in a line, then Moybyte's parts and what it is made with, "
     "each with its licence; focus on The console"),
    (lambda T: licence(T), "licence",
     "A licence's text, MicroPython's MIT licence in its paragraphs, scrolling under the bar"),
)
TOUCH = (
    (lambda T: achievements(T, S3, "many", focus="first_run"), "achievements-s3",
     "Achievements on the Guition S3 (480x320, touch): four 112 px columns, no hint strip; "
     "focus on Lift Off!"),
    (lambda T: unlock(T, S3), "achievement-unlocked-s3",
     "A badge found on the Guition S3, over Settings"),
    (lambda T: settings_about(T, S3), "settings-about-s3",
     "Settings' About group on the Guition S3: rows at the tap floor, no hint strip"),
    (lambda T: about(T, S3), "about-s3",
     "About on the Guition S3 over washed Settings: 28 px rows and a 28 px close in the corner"),
)
DESKS = (
    (achievements_desk, "achievements",
     "Achievements as a page of the Settings window: six badges a row, To find peeking under "
     "Found, the focused badge's name, how it is found and its state in the foot; focus on "
     "Cart Explorer"),
    (lambda T: achievements_desk(T, scroll=150, pop="decorator"), "achievement-popover",
     "A locked badge opened on the desk: its popover above Home Decorator -- Not found yet"),
    (unlock_desk, "achievement-unlocked",
     "A badge found on the desk: the banner centred under the bar"),
    (unlock_game_desk, "achievement-unlocked-game",
     "A badge found over a fullscreen game in console mode (no bar): the banner top centre"),
    (about_desk, "about",
     "About from the system menu over the washed desk: Moy at 64 px, the wordmark, version, "
     "board, Updates and Licences; Open and Close"),
    (lambda T: about_desk(T, update=True), "about-update", "About on the desk with an update ready"),
    (licences_desk, "licences",
     "Licences as a page of the Settings window: the line and the parts at the left, the "
     "focused one's text at the right, which PgUp and PgDn scroll"),
)
CONTRAST_SCENES = (
    ("handheld", HANDHELD[2]), ("handheld", HANDHELD[6]), ("handheld", HANDHELD[10]),
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
