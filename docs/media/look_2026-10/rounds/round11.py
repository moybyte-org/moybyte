#!/usr/bin/env python3
"""Round 11 of the 2026-10 look, layered over round 10: the screens that
`pixel_round3/` laid out, drawn in the modern rendering (plan step 2).

- Settings on the handheld: the working rows and an About group, a caption
  on every group. CHROME SIZE is Appearance's.
- Appearance on the handheld and as a desk window: the theme cards, each
  the theme's own home (handheld) or desk in miniature, named in its own
  face; then the rows.
- Calc on the handheld: the tape on a display inset beside the 4x5 keypad.
- The console home on the 1024x600 board at 1x: hero, details, shelf.
- The home and the desk over the default wallpaper, Moy's night: the
  wallpaper cart's pixel art at whole scales. A light theme's on-backdrop
  ink turns light over it, and the handheld bar takes the desk bar's fill.
- Contrast's light mode.

A layer of the target, not an entry point: `../draw.py` draws every picture.
"""
import copy
import math
import os
import sys

from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import round10 as R10  # noqa: E402

R9, R8, R7, M = R10.R9, R10.R8, R10.R7, R10.M
Face, F, HH, DK, mix = M.Face, M.F, M.HH, M.DK, M.mix
LIGHT, DARK = R10.LIGHT, R10.DARK


CONTRAST_LIGHT = M.theme(
    key="contrast", name="Contrast (light)",
    bg="#ffffff", surface="#ffffff", raised="#ffffff", sunk="#e6e6e6", line="#000000",
    dim="#333333", text="#000000", accent="#ffe600", value="#000000", value_ink="#ffffff",
    danger="#b3261e", fam="atkinson", w_med=600, w_semi=700, w_bold=800,
    sizes=copy.deepcopy(M.CONTRAST["sizes"]),
    focus=dict(style="invert", card="ring", small="invert",
               ring=dict(w=3, gap=2, keyline="#000000")),
    win_r=4, card_r=4, key_r=4,
    win=dict(header="fill", frame=(2, "#000000"), frame_back=(2, "#000000"), rule=2),
    bar=dict(fill="#ffffff", line=(2, "#000000"), chip_cur="#e6e6e6"),
    buttons={"normal": {"border": (2, "#000000")},
             "tonal": {"border": (2, "#000000"), "fill": "#e6e6e6", "ink": "#000000"},
             "danger": {"border": (2, "#000000")}},
    lcd="#ffffff", lcd_border=(2, "#000000"), lcd_ink="#000000",
)


# ==========================================================================
# rows: link, info, cycle, segmented, swatches
# ==========================================================================
def _sw_list(T):
    if T["mode"] == "light":
        return (T["accent"], "#d13f2a", "#c2306f", "#6f4fd8", "#0a72bd", "#0d8160", "#4c8417",
                T["text"])
    return (T["accent"], "#ff7a59", "#f2719f", "#a98bff", "#5cc4ff", "#55d6a6", "#9fd36a",
            T["text"])


def _value(cv, T, kind, val, xr, y, h, fv, desk):
    """A row's value, right-aligned at xr."""
    base = M.mid_base(fv, y, h)
    dim, ink = T["dim"], T["text"]
    if kind == "link":
        w = cv.text(xr, base, "›", fv, dim, align="right")
        cv.text(xr - w - (8 if desk else 5), base, val, fv, dim, align="right")
    elif kind == "info":
        cv.text(xr, base, val, fv, dim, align="right")
    elif kind == "cycle":
        w = cv.text(xr, base, "›", fv, dim, align="right")
        g = 10 if desk else 6
        w2 = cv.text(xr - w - g, base, val, fv, ink, align="right")
        cv.text(xr - w - w2 - 2 * g, base, "‹", fv, dim, align="right")
    elif kind == "seg":
        opts, cur = val
        g, ph = (18, h - 8) if desk else (12, h - 4)
        x = xr
        for i, o in reversed(list(enumerate(opts))):
            ow = fv.width(o)
            x -= ow
            c = ink if i == cur else dim
            if i == cur:
                c = M.current_mark(cv, T, x - g // 2, y + (h - ph) // 2, ow + g, ph,
                                   6 if desk else 4) or c
                if M.cur_style(T) == "bar":
                    cv.rrect(x, y + h - 4, ow, 3, 1.5, T["value"])
            cv.text(x, base, o, fv, c)
            x -= g + (4 if desk else 2)
    elif kind == "swatch":
        cols, cur = val
        d, p = (16, 24) if desk else (11, 15)
        x = xr - d
        for i, c in reversed(list(enumerate(cols))):
            cy = y + (h - d) // 2
            if i == cur:
                M.current_mark(cv, T, x - 3, cy - 3, d + 6, d + 6, (d + 6) / 2)
            cv.shape(x, cy, d, d, lambda dr, s: dr.ellipse((0, 0, d * s - 1, d * s - 1), fill=255), c)
            x -= p


def rows(cv, T, x, y, w, rh, items, focus_i, fl, fv, desk, group=True):
    """A group of rows: on the handheld a `surface` card, on a window's body
    plain rules. items: (label, kind, value)."""
    pad = 14 if desk else 10
    if group:
        r = 8
        cv.rrect(x, y, w, rh * len(items), r, T["surface"])
        if T["mode"] == "dark":
            cv.ring(x, y, w, rh * len(items), r, 1, T["line"])
    for i, (label, kind, val) in enumerate(items):
        ry = y + i * rh
        if i:
            cv.rect(x + (pad if group else 0), ry, w - (2 * pad if group else 0), 1, T["line"])
        ink = M.focus_mark(cv, T, (x, ry, w, rh, 0), "part") if i == focus_i else None
        cv.text(x + (pad if group else 10), M.mid_base(fl, ry, rh), label, fl, ink or T["text"])
        _value(cv, T, kind, val, x + w - (pad if group else 10), ry, rh, fv, desk)
    return y + rh * len(items)


def hints(cv, T, items, base, x=8, bg=None):
    fk, fh = F(T, "key", HH), F(T, "hint", HH)
    for k, label in items:
        x += M.keycap(cv, T, x, base, k, fk, bg or T["bg"]) + 4
        x += cv.text(x, base, label, fh, T["dim"]) + 11
    return x


def hh_page(T, title, body=None):
    cv = M.new_cv(T, False, body or T["bg"])
    if T["hhbar"]["style"] == "none" and body:
        cv.rect(0, 0, 320, 28, T["bg"] if T["mode"] == "dark" else T["surface"])
    M.hh_bar(cv, T, title=title)
    return cv


def mode_name(T):
    return "Night" if T["mode"] == "dark" else "Day"


# ==========================================================================
# Settings, handheld
# ==========================================================================
def hh_settings(T):
    cv = hh_page(T, "Settings")
    fl, fv, fc = Face(T["fam"], 13, T["w_med"], 2), F(T, "status", HH), F(T, "status", HH)
    cv.text(16, 42, "General", fc, T["dim"])
    rows(cv, T, 6, 47, 308, 20, (("Wi-Fi", "link", "Home"),
                                 ("Appearance", "link", f"Moy {mode_name(T)}"),
                                 ("Text size", "cycle", "Medium")), 1, fl, fv, False)
    cv.text(16, 124, "About", fc, T["dim"])
    rows(cv, T, 6, 129, 308, 20, (("Firmware", "info", "5"), ("Board", "info", "T-Deck Plus"),
                                  ("Storage", "info", "1.2 MB free"), ("Battery", "info", "82%")),
         None, fl, fv, False)
    hints(cv, T, (("enter", "Open"), ("Esc", "Back")), 233)
    return cv


# ==========================================================================
# Appearance: the theme cards and the rows
# ==========================================================================
def card_themes(T):
    """(name, its theme, its modes) for the three built-in themes, each in
    the page's mode where it has one."""
    dark = T["mode"] == "dark"
    return (("Moy", T, "Day · Night"), ("Blocks", M.C, "Light"),
            ("Contrast", M.CONTRAST if dark else CONTRAST_LIGHT, "Dark · Light"))


_PREVIEW = {}


def preview(Tc, desk, w, h):
    key = (id(Tc), desk, w, h)
    if key not in _PREVIEW:
        with M.focus_off():
            src = (M.desktop(Tc) if desk else M.hh_home(Tc)).image()
        _PREVIEW[key] = src.resize((w, h), Image.LANCZOS)
    return _PREVIEW[key]


def theme_cards(cv, T, x0, y, cw, ch, gap, desk):
    """The cards: the current theme carries the current plate, which holds
    the card and its name."""
    fs = 15 if desk else 13
    fm = F(T, "meta", DK) if desk else None
    for i, (name, Tc, modes) in enumerate(card_themes(T)):
        x = x0 + i * (cw + gap)
        fn = Face(Tc["fam"], fs, Tc["w_semi"], 4 if desk else 2)
        p = 6 if desk else 4
        if i == 0:
            M.current_mark(cv, T, x - p - 1, y - p, cw + 2 * p + 2, ch + (58 if desk else 22),
                           12 if desk else 8)
        r = 8 if desk else 6
        cv.paste(preview(Tc, desk, cw, ch), x, y, r=r)
        cv.ring(x, y, cw, ch, r, 1, T["line"] if T["mode"] == "dark" else mix(T["line"], T["dim"], 0.25))
        cv.text(x + cw // 2, y + ch + (24 if desk else 14), name, fn, T["text"], align="center")
        if desk:
            cv.text(x + cw // 2, y + ch + 43, modes, fm, T["dim"], align="center")


def appearance_rows(T, desk):
    dark = T["mode"] == "dark"
    out = [("Mode", "seg", (("Day", "Night"), 1 if dark else 0)),
           ("Accent", "swatch", (_sw_list(T), 0)),
           ("Tint", "cycle", "Indigo" if dark else "Cool"),
           ("Wallpaper", "link", "Backdrop")]
    if desk:
        out.append(("Window style", "cycle", "Soft"))
    out.append(("Code font", "cycle", "JetBrains Mono"))
    if desk:
        out.append(("Chrome size", "cycle", "1×"))
    return out


def hh_appearance(T):
    cv = hh_page(T, "Appearance")
    fc = F(T, "status", HH)
    cv.text(16, 39, "Theme", fc, T["dim"])
    theme_cards(cv, T, 30, 46, 66, 50, 31, False)
    fl, fv = Face(T["fam"], 13, T["w_med"], 2), F(T, "status", HH)
    rows(cv, T, 6, 117, 308, 20, appearance_rows(T, False), 2, fl, fv, False)
    hints(cv, T, (("enter", "Open"), ("Esc", "Back")), 234)
    return cv


def desk_appearance(T):
    cv = M.new_cv(T, True)
    M.backdrop(cv, T, "desk")
    M.desk_icons(cv, T)
    x, y, w, h = 152, 58, 680, 514
    top = M.window(cv, T, x, y, w, h, "Appearance", "settings", True)
    R7.draw_icon(cv, T, "settings", x + 16, y + (T["win"]["h"] - 20) // 2, 20, R8.title_ink(T, True))
    fc = F(T, "col", DK)
    cv.text(x + 24, top + 26, "Theme", fc, T["dim"])
    cw, ch, gap = 192, 112, 24
    x0 = x + (w - 3 * cw - 2 * gap) // 2
    theme_cards(cv, T, x0, top + 40, cw, ch, gap, True)
    ry = rows(cv, T, x + 16, top + 214, w - 32, 28, appearance_rows(T, True), 2,
              F(T, "row", DK), F(T, "meta", DK), True, group=False)
    fy = y + h - 44
    cv.rect(x + 16, fy, w - 32, 1, T["line"])
    fm = F(T, "meta", DK)
    cv.text(x + 26, fy + 28, f"Moy {mode_name(T)} · built in", fm, T["dim"])
    hints(cv, T, (("enter", "Open"), ("Esc", "Close")), fy + 28, x=x + w - 176, bg=T["surface"])
    assert ry < fy
    R8.chips_bar(cv, T, (("settings", "Appearance"), ("folder", "Files")))
    return cv


# ==========================================================================
# Calc, handheld: the tape beside the keypad
# ==========================================================================
TAPE = (("120 − 36", "84"), ("36 + 48", "84"), ("84 ÷ 7", "12"), ("12 × 12", "144"))


def hh_calc(T):
    cv = hh_page(T, "Calc", T["win"]["body"] or T["surface"])
    ft, fr = F(T, "htape", HH), F(T, "hres", HH)
    lx, ly, lw, lh = 8, 32, 112, 200
    lr = min(8, T["key_r"])
    cv.rrect(lx, ly, lw, lh, lr, T["lcd"] or T["sunk"])
    if T["lcd_border"]:
        cv.ring(lx, ly, lw, lh, lr, *T["lcd_border"])
    dim, ink = T["lcd_dim"] or T["dim"], T["lcd_ink"] or T["value"]
    xr = lx + lw - 10
    y = ly + 4
    for i, (expr, res) in enumerate(TAPE):
        cv.text(xr, y + 13, expr, ft, dim, align="right")
        cv.text(xr, y + 27, "= " + res, ft, T["text"], align="right")
        if i < len(TAPE) - 1:
            cv.rect(lx + 10, y + 32, lw - 20, 1, T["line"])
        y += 35
    cv.text(xr, ly + lh - 44, "12 × 7", ft, dim, align="right")
    cv.text(xr, ly + lh - 10, "84", fr, ink, align="right")
    kx, kw_all, g = 128, 184, 6
    kw = (kw_all - 3 * g) // 4
    kh = (lh - 4 * g) // 5
    fonts = {"key": F(T, "hkey", HH), "op": F(T, "hop", HH)}
    for r, rowk in enumerate(M.KEYS):
        for c, lab in enumerate(rowk):
            if lab is None:
                continue
            ww = kw * 2 + g if lab == "0" else kw
            M.button(cv, T, "calc", M.key_kind(lab), kx + c * (kw + g), ly + r * (kh + g), ww, kh,
                     lab, fonts, focus=lab == "5")
    return cv


# ==========================================================================
# the console home on the 1024x600 board
# ==========================================================================
SHELF = ("pixel_pet", "star_catcher", "sky_run", "hop_quest", "harpoon_pop", "brick_siege",
         "bullet_storm")


def console_bar(cv, T):
    b = T["bar"]
    cv.rect(0, 0, 1024, b["h"], b["fill"] or T["surface"], b["alpha"])
    if b["line"]:
        cv.rect(0, b["h"] - b["line"][0], 1024, b["line"][0], b["line"][1])
    sys_fill = (b["sys"] or (T["raised"],))[0]
    cv.rrect(6, 3, 36, 34, 9, sys_fill)
    M.moy(cv, 8, 4, 32, None, None)
    fc = F(T, "chip", DK)
    x = 64
    for i, t in enumerate(M.TABS):
        tw = fc.width(t)
        c = T["dim"]
        if i == 0:
            c = M.current_mark(cv, T, x - 12, 6, tw + 24, 28, 8) or T["text"]
            if M.cur_style(T) == "bar":
                cv.rrect(x, 31, tw, 3, 1.5, T["value"])
        cv.text(x, 25, t, fc, c)
        x += tw + 30
    R7.draw_icon(cv, T, "close", 1024 - 34, 9, 22, T["text"])
    fs = F(T, "status", DK)
    tw = cv.text(1024 - 52, 26, "14:32", fs, T["text"], align="right")
    R7.draw_icon(cv, T, "battery", 1024 - 52 - tw - 30, 9, 22, T["text"])
    R7.draw_icon(cv, T, "wifi", 1024 - 52 - tw - 58, 8, 21, T["text"])


def home_button(cv, T, kind, x, y, w, h, label, slot=None):
    st = M.bstyle(T, "home", kind)
    r = min(st["r"], h / 2)
    if kind != "primary":
        M.soft(cv, T, x, y, w, h, r, 0.35)
    cv.rrect(x, y, w, h, r, M.paint(st["fill"], y, h))
    if st["border"]:
        cv.ring(x, y, w, h, r, st["border"][0], st["border"][1])
    f = F(T, "wtitle", DK) if kind == "primary" else F(T, "chip", DK)
    lw = f.width(label) + (26 if slot else 0)
    lx = x + (w - lw) // 2
    if slot:
        R7.draw_icon(cv, T, slot, lx, y + (h - 18) // 2, 18, st["ink"])
        lx += 26
    cv.text(lx, M.mid_base(f, y, h), label, f, st["ink"])


def console_home(T):
    cv = M.new_cv(T, True, T["home_bg"] or T["bg"])
    M.backdrop(cv, T, "home")
    hx, hy, hs = 48, 64, 256
    M.soft(cv, T, hx, hy, hs, hs, 14, 0.8)
    cv.paste(M.cover("sky_run", 64, 4), hx, hy, r=14)
    x = 336
    ink, dim = T["on_backdrop"]["ink"] or T["text"], T["on_backdrop"]["dim"] or T["dim"]
    cv.text(x, 102, "Sky Run", Face(T["fam"], 40, T["w_bold"], 4), ink)
    fm, fr = F(T, "meta", DK), F(T, "row", DK)
    cv.text(x, 130, "Platformer  ·  Built in  ·  Played today", fm, dim)
    cv.text(x, 164, "Run and jump across the hills, grab every coin before the", fr, ink)
    cv.text(x, 186, "clouds roll in, and find the flag at the end of the day.", fr, ink)
    fh, fv = F(T, "col", DK), F(T, "chip", DK)
    for i, (k, v) in enumerate((("Played", "12 times"), ("Last", "Today"), ("Size", "21 KB"),
                                ("By", "Moybyte"))):
        cv.text(x + i * 150, 224, k, fh, dim)
        cv.text(x + i * 150, 246, v, fv, ink)
    home_button(cv, T, "primary", x, 272, 148, 42, "Play", "play")
    home_button(cv, T, "normal", x + 162, 272, 136, 42, "Change")
    home_button(cv, T, "normal", x + 312, 272, 112, 42, "Copy")
    cv.text(48, 378, "Games  ·  11", fh, dim)
    cr = T["card_r"]
    for i, name in enumerate(SHELF):
        sx = 48 + i * 148
        M.soft(cv, T, sx, 392, 128, 128, cr, 0.5)
        if name == "sky_run":
            M.focus_mark(cv, T, (sx, 392, 128, 128, cr), "card", image=M.cover(name, 64, 2))
        else:
            cv.paste(M.cover(name, 64, 2), sx, 392, r=cr)
    hints(cv, T, (("enter", "Play"), ("C", "Change"), ("/", "Search")), 584, x=24)
    fhh, fk = F(T, "hint", HH), F(T, "key", HH)
    rx = 1000 - fhh.width("Tabs")
    cv.text(rx, 584, "Tabs", fhh, T["dim"])
    M.keycap(cv, T, rx - 19, 584, "R", fk, T["bg"])
    M.keycap(cv, T, rx - 37, 584, "L", fk, T["bg"])
    console_bar(cv, T)
    return cv


# ==========================================================================
# the default wallpaper, Moy's night: a cart's pixel art in MOY64
# ==========================================================================
MOY_ART = ("................", "...0000000......", "..0ddddddd0.....", ".0d66ddddd0.....",
           ".0dddddddd0.....", ".0dddddddd0000..", ".0dd00d00ddddd0.", ".0dddddddddddd0.",
           ".0dddddddddddd0.", ".0dd0ddd0ddddd0.", ".0ddd000dddddd0.", ".0dddddddddddd0.",
           ".0dddddddddddd0.", "..022222222220..", "..02220002220...", "...000...000....")
ZZ = (("#####", "...#.", "..#..", ".#...", "#####"), ("####", "..#.", ".#..", "####"))


def night_wallpaper(W, H, moon, moy, ridge, quiet, band):
    """Black sky with sparse stars, a dithered band to dark blue at the
    horizon, a black hill, the crescent, and Moy asleep on the ridge. Stars
    keep out of the quiet rectangles."""
    P = lambda i: M.q(R7.MOY64[i])  # noqa: E731
    im = Image.new("RGB", (W, H), P(0))
    px = im.load()

    def put(x, y, c):
        if 0 <= x < W and 0 <= y < H:
            px[x, y] = c
    bayer = ((0, 8, 2, 10), (12, 4, 14, 6), (3, 11, 1, 9), (15, 7, 13, 5))
    for yy in range(H):
        t = max(0.0, min(1.0, (yy - (ridge - band)) / band))
        for xx in range(W):
            if t * 15 > bayer[yy & 3][xx & 3]:
                px[xx, yy] = P(1)
    seed = [20261015]

    def rnd(n):
        seed[0] = (seed[0] * 1103515245 + 12345) & 0x7FFFFFFF
        return seed[0] % n
    for _ in range(W * H // 700):
        x, y = rnd(W), 24 + rnd(max(1, ridge - 32))
        if any(qx <= x < qx + qw and qy <= y < qy + qh for qx, qy, qw, qh in quiet):
            continue
        k = rnd(14)
        if k == 0:
            put(x, y, P(7))
            for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                put(x + dx, y + dy, P(13))
        else:
            put(x, y, P(7) if k < 4 else P(13))
    mx, my, mr = moon
    for yy in range(-mr - 3, mr + 4):
        for xx in range(-mr - 3, mr + 4):
            d = (xx * xx + yy * yy) ** 0.5
            bite = ((xx + mr * 0.45) ** 2 + (yy + mr * 0.35) ** 2) ** 0.5
            if d <= mr and bite > mr * 0.85:
                put(mx + xx, my + yy, P(10))
            elif mr < d <= mr + 2 and bite > mr * 0.85 and (xx + yy) & 1:
                put(mx + xx, my + yy, P(9) if d <= mr + 1 else P(60))
    ox, s = moy
    for xx in range(W):
        bump = int(4 * math.cos((xx - ox - 8 * s) / (W * 0.12)))
        h1 = ridge - max(0, bump)
        for yy in range(h1, H):
            px[xx, yy] = P(0)
        put(xx, h1, P(60))
        put(xx, h1 + 1, P(1) if xx & 1 else P(0))
    oy = ridge - 4 - 16 * s + 2
    for r, line in enumerate(MOY_ART):
        for c, ch in enumerate(line):
            if ch != ".":
                for dy in range(s):
                    for dx in range(s):
                        put(ox + c * s + dx, oy + r * s + dy, P(int(ch, 16)))
    zx, zy = ox + 9 * s, oy - 14
    for (glyph, col, ddx, ddy) in ((ZZ[0], P(6), 0, 0), (ZZ[1], P(13), 7, -8)):
        for r, line in enumerate(glyph):
            for c, ch in enumerate(line):
                if ch == "#":
                    put(zx + ddx + c, zy + ddy + r, col)
    return im


WALL_HH = night_wallpaper(320, 240, moon=(296, 44, 8), moy=(10, 2), ridge=216, band=18,
                          quiet=((0, 0, 320, 30), (16, 150, 288, 64)))
WALL_DK = night_wallpaper(512, 300, moon=(486, 44, 12), moy=(472, 2), ridge=284, band=40,
                          quiet=((0, 0, 512, 24), (0, 24, 52, 270))).resize((1024, 600), Image.NEAREST)

_backdrop = M.backdrop


def backdrop(cv, T, surface):
    """A wallpaper replaces the theme's backdrop."""
    wall = T.get("_wall")
    if wall is None:
        return _backdrop(cv, T, surface)
    cv.paste(wall, 0, 0)


M.backdrop = backdrop


def on_wallpaper(T, wall):
    T2 = copy.deepcopy(T)
    T2["_wall"] = wall
    T2["home_bg"] = None
    T2["on_backdrop"]["shadow"] = ("#000000", 0.8, 1, 1)
    if T["mode"] == "light":
        T2["on_backdrop"].update(ink="#ffffff", dim="#c9cce0")
        T2["label"] = dict(ink="#ffffff", shadow=None)
        T2["hhbar"].update(style="glass", line=T["bar"]["line"])
    return T2


def hh_home_wallpaper(T):
    return M.hh_home(on_wallpaper(T, WALL_HH))


def desk_wallpaper(T):
    T2 = on_wallpaper(T, WALL_DK)
    T2["layout"]["calc"] = (592, 96, 344, 488)
    return M.desktop(T2)


# ==========================================================================
# the pictures
# ==========================================================================
def scenes():
    src = R10.scenes()
    for T, tag in ((LIGHT, "moy-light"), (DARK, "moy-dark")):
        n = T["name"]
        hh_settings(T).save(f"{tag}_handheld_settings",
                            f"Settings: the working rows and About, a caption on every group; "
                            f"focus on Appearance; {n}", x3=True)
        hh_appearance(T).save(f"{tag}_handheld_appearance",
                              f"Appearance: the theme cards (Moy current), then the rows; focus on "
                              f"Tint; {n}", x3=True)
        desk_appearance(T).save(f"{tag}_desktop_appearance",
                                f"Appearance as a window on the desk, with Chrome size; focus on Tint; "
                                f"{n}")
        hh_calc(T).save(f"{tag}_handheld_calc",
                        f"Calc fullscreen: the tape on a display inset beside the 4x5 keypad in "
                        f"kinds; focus on 5; {n}", x3=True)
        console_home(T).save(f"{tag}_desktop_home",
                             f"The console-mode home on the 1024x600 board at 1x: hero, details, "
                             f"shelf; the bar's X returns to the desk; {n}")
        hh_home_wallpaper(T).save(f"{tag}_handheld_home_wallpaper",
                                  f"Handheld home over the default wallpaper, Moy's night; {n}",
                                  x3=True)
        desk_wallpaper(T).save(f"{tag}_desktop_wallpaper",
                               f"Desk over the default wallpaper, Moy's night: Calc keeps clear of "
                               f"the sleeping Moy; {n}")
    return src
