#!/usr/bin/env python3
"""Round 13 of the 2026-10 look, over round 11: the apps the screen steps
had not reached -- Paint, My Art, Get Carts, Notes and Storybook -- drawn
with the parts the restyled apps use (Settings, Appearance, Files, Calc,
the sprite editor) and the public UI verbs (`docs/ui_api_2026-10.md`).

`APPS_RESTYLE.md` beside `../draw.py` maps each app's functions to the
parts drawn here; a part the verbs lack is marked there and in GAPS.md.
`round15.py` draws Storybook and Paint's 1024x600 drawings over these
parts.

A layer of the target, not an entry point: `../draw.py` draws every picture.
"""
import io
import os
import sys

from PIL import Image, ImageDraw

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import round11 as R11  # noqa: E402

R10, R9, R8, R7, M = R11.R10, R11.R9, R11.R8, R11.R7, R11.M
Face, F, HH, DK, mix = M.Face, M.F, M.HH, M.DK, M.mix
LIGHT, DARK = R11.LIGHT, R11.DARK
SMOOTH = os.path.join(R7.REPO, "native", "moy_ui", "icons", "smooth", "default")

import resvg_py  # noqa: E402


# ==========================================================================
# icons: the shipped smooth set, and the slots this round adds (GAPS 32)
# ==========================================================================
def _p(d, fill=False):
    f = ' fill="#000"' if fill else ' fill="none"'
    return (f'<path d="{d}"{f} stroke="#000" stroke-width="2" stroke-linecap="round" '
            f'stroke-linejoin="round"/>')


NEW_ICONS = {
    "text": _p("M5 7V5h14v2M12 5v14M9 19h6"),
    "shapes": (_p("M5 9.5h7.5a1.5 1.5 0 0 1 1.5 1.5v7.5a1.5 1.5 0 0 1-1.5 1.5H5a1.5 1.5 0 0 1"
                  "-1.5-1.5V11A1.5 1.5 0 0 1 5 9.5z")
               + '<circle cx="15.5" cy="8.5" r="5" fill="none" stroke="#000" stroke-width="2"/>'),
    "to_front": (_p("M3.5 3.5h11v11h-11z") + _p("M9.5 9.5h11v11h-11z", True)),
    "to_back": (_p("M9.5 9.5h11v11h-11z") + _p("M3.5 3.5h11v6h-5v5h-6z", True)),
    "spray": (_p("M5.5 11.5a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v9h-8z") + _p("M8 9.5V6.5h3v3")
              + _p("M11 7.5h2.5")
              + "".join(f'<circle cx="{x}" cy="{y}" r="1.4" fill="#000"/>'
                        for x, y in ((17.5, 5), (20, 8.5), (17.5, 11)))),
    "picker": (_p("M16.5 3.5a2.5 2.5 0 0 1 3.5 3.5l-2.5 2.5-3.5-3.5z", True)
               + _p("M14.5 7.5l-8 8v2.5h2.5l8-8")
               + _p("M5 20.5c0-1 .8-1.8 .8-1.8s.8.8 .8 1.8a.8 .8 0 0 1-1.6 0z", True)),
    "pan": (_p("M12 3.5v5M12 15.5v5M3.5 12h5M15.5 12h5") + _p("M9.5 6L12 3.5 14.5 6")
            + _p("M9.5 18l2.5 2.5 2.5-2.5") + _p("M6 9.5L3.5 12 6 14.5")
            + _p("M18 9.5l2.5 2.5-2.5 2.5") + '<circle cx="12" cy="12" r="1.6" fill="#000"/>'),
    "cut": ('<circle cx="6.5" cy="17.5" r="2.7" fill="none" stroke="#000" stroke-width="2"/>'
            '<circle cx="17.5" cy="17.5" r="2.7" fill="none" stroke="#000" stroke-width="2"/>'
            + _p("M8.5 15.5L17 3.5M15.5 15.5L7 3.5")),
    "paste": (_p("M8.5 5H6.5a2 2 0 0 0-2 2v12a2 2 0 0 0 2 2h3.5M15.5 5h2a2 2 0 0 1 2 2v2.5")
              + _p("M9.5 3h5v4h-5z") + _p("M13 12h8v9.5h-8z", True)),
    "background": (_p("M5.5 4.5h13a2 2 0 0 1 2 2v11a2 2 0 0 1-2 2h-13a2 2 0 0 1-2-2v-11a2 2 0 0 1 2-2z")
                   + _p("M7.5 8.5h9v7h-9z", True)),
    "trans_none": _p("M5.5 6.5h13v11h-13z"),
    "trans_fade": (_p("M5.5 6.5h13v11h-13z") + _p("M12 6.5h6.5v11H12z", True)),
    "trans_swipe": (_p("M3.5 6.5h9v11h-9z") + _p("M15 9.5l2.5 2.5-2.5 2.5M12.5 12h8")),
    "down": _p("M7 10l5 5 5-5"),
    "layout": _p("M5.5 4.5h13a2 2 0 0 1 2 2v11a2 2 0 0 1-2 2h-13a2 2 0 0 1-2-2v-11a2 2 0 0 1 2-2z"
                 "M3.5 9h17M12 9v10.5"),
    "check": _p("M5 12.5l4.5 4.5L19 7.5"),
    "slides": _p("M4 4.5h6.5v6H4zM13.5 4.5H20v6h-6.5zM4 13.5h6.5v6H4zM13.5 13.5H20v6h-6.5z"),
    "bubble": _p("M5 4.5h14a1.5 1.5 0 0 1 1.5 1.5v8.5a1.5 1.5 0 0 1-1.5 1.5h-7.5l-4.5 3.5V16H5"
                 "a1.5 1.5 0 0 1-1.5-1.5V6A1.5 1.5 0 0 1 5 4.5z"),
    "warn": _p("M12 4L21 19.5H3z") + _p("M12 10v4.5") + '<circle cx="12" cy="17" r="1.1" fill="#000"/>',
    "align_l": _p("M4 5.5h16M4 10h10M4 14.5h16M4 19h10"),
    "align_c": _p("M4 5.5h16M7 10h10M4 14.5h16M7 19h10"),
    "align_r": _p("M4 5.5h16M10 10h10M4 14.5h16M10 19h10"),
}


def _svg(slot):
    if slot in NEW_ICONS:
        return ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">'
                + NEW_ICONS[slot] + "</svg>")
    with open(os.path.join(SMOOTH, slot + ".svg")) as fh:
        return fh.read()


_MASKS = {}


def sicon(cv, T, slot, x, y, px, ink, alpha=1.0):
    """A slot of the default smooth set (or of this round's new slots) as a
    2 bpp (16 px and under) or 4 bpp mask, tinted `ink`."""
    key = (slot, px)
    if key not in _MASKS:
        png = bytes(resvg_py.svg_to_bytes(svg_string=_svg(slot), width=px, height=px))
        a = Image.open(io.BytesIO(png)).convert("RGBA").split()[3]
        _MASKS[key] = M.levels(a, 4 if px <= 16 else 16)
    cv._blend(cv.L(x), cv.L(y), _MASKS[key], ink, alpha)


# ==========================================================================
# the kid's drawings: MOY64 art at 160x120, doubled (Paint's chunky pixels)
# ==========================================================================
P64 = [M.q(c) for c in R7.MOY64]
N = R7.NAMES


def _c(name):
    return P64[N[name]] if isinstance(name, str) else P64[name]


def _art(kind):
    im = Image.new("RGB", (160, 120), _c("white"))
    d = ImageDraw.Draw(im)
    if kind == "garden":
        d.rectangle((0, 0, 159, 84), fill=_c("blue"))
        d.ellipse((118, 10, 142, 34), fill=_c("yellow"))
        for cx, cy in ((30, 22), (44, 18), (56, 24)):
            d.ellipse((cx - 10, cy - 6, cx + 10, cy + 6), fill=_c("white"))
        d.rectangle((0, 84, 159, 119), fill=_c("green"))
        d.rectangle((0, 100, 159, 119), fill=_c("dark_green"))
        for i, (fx, col) in enumerate(((20, "red"), (46, "pink"), (74, "yellow"), (100, "orange"),
                                        (128, "indigo"))):
            d.line((fx, 88, fx, 104), fill=_c("dark_green"), width=2)
            d.ellipse((fx - 6, 80, fx + 6, 92), fill=_c(col))
            d.ellipse((fx - 2, 84, fx + 2, 88), fill=_c("yellow" if col != "yellow" else "orange"))
    elif kind == "rocket":
        im.paste(_c("dark_blue"), (0, 0, 160, 120))
        for i in range(40):
            x, y = (i * 53) % 160, (i * 37) % 100
            d.point((x, y), fill=_c("white" if i % 3 else "yellow"))
        d.ellipse((12, 70, 72, 130), fill=_c("dark_grey"))
        d.ellipse((24, 84, 34, 94), fill=_c("light_grey"))
        d.polygon(((100, 20), (114, 44), (114, 84), (86, 84), (86, 44)), fill=_c("white"))
        d.polygon(((86, 64), (74, 88), (86, 84)), fill=_c("red"))
        d.polygon(((114, 64), (126, 88), (114, 84)), fill=_c("red"))
        d.ellipse((93, 46, 107, 60), fill=_c("blue"))
        d.polygon(((90, 84), (100, 108), (110, 84)), fill=_c("orange"))
        d.polygon(((94, 84), (100, 98), (106, 84)), fill=_c("yellow"))
    elif kind == "cat":
        im.paste(_c("peach"), (0, 0, 160, 120))
        d.rectangle((0, 88, 159, 119), fill=_c("brown"))
        d.ellipse((30, 96, 130, 116), fill=_c("red"))
        d.ellipse((52, 50, 108, 104), fill=_c("orange"))
        d.ellipse((60, 28, 100, 64), fill=_c("orange"))
        d.polygon(((62, 36), (64, 18), (76, 30)), fill=_c("orange"))
        d.polygon(((98, 36), (96, 18), (84, 30)), fill=_c("orange"))
        d.ellipse((69, 40, 75, 46), fill=_c("black"))
        d.ellipse((85, 40, 91, 46), fill=_c("black"))
        d.polygon(((78, 50), (82, 50), (80, 53)), fill=_c("pink"))
        d.line((104, 90, 130, 70, 136, 56), fill=_c("orange"), width=5)
    elif kind == "fish":
        im.paste(_c("blue"), (0, 0, 160, 120))
        d.rectangle((0, 100, 159, 119), fill=_c("yellow"))
        for x in (14, 30, 138):
            d.line((x, 100, x - 4, 70, x + 2, 54), fill=_c("green"), width=3)
        d.ellipse((50, 40, 106, 76), fill=_c("orange"))
        d.polygon(((104, 58), (126, 42), (126, 74)), fill=_c("orange"))
        d.ellipse((60, 50, 68, 58), fill=_c("white"))
        d.ellipse((62, 52, 66, 56), fill=_c("black"))
        for bx, by, r in ((44, 30, 3), (38, 18, 2), (46, 8, 3)):
            d.ellipse((bx - r, by - r, bx + r, by + r), outline=_c("white"))
    elif kind == "house":
        im.paste(_c("peach"), (0, 0, 160, 120))
        d.rectangle((0, 0, 159, 80), fill=_c("blue"))
        d.rectangle((0, 90, 159, 119), fill=_c("green"))
        d.rectangle((40, 54, 110, 104), fill=_c("white"))
        d.polygon(((32, 56), (75, 22), (118, 56)), fill=_c("red"))
        d.rectangle((66, 76, 82, 104), fill=_c("brown"))
        d.rectangle((48, 62, 60, 74), fill=_c("yellow"))
        d.rectangle((90, 62, 102, 74), fill=_c("yellow"))
        d.ellipse((126, 70, 150, 94), fill=_c("dark_green"))
        d.rectangle((136, 92, 140, 104), fill=_c("brown"))
    elif kind == "dragon":
        im.paste(_c("dark_blue"), (0, 0, 160, 120))
        d.rectangle((0, 96, 159, 119), fill=_c("dark_green"))
        d.ellipse((40, 52, 112, 100), fill=_c("green"))
        d.ellipse((90, 30, 128, 62), fill=_c("green"))
        d.polygon(((96, 34), (100, 16), (106, 32)), fill=_c("yellow"))
        d.polygon(((112, 32), (118, 16), (120, 36)), fill=_c("yellow"))
        d.ellipse((110, 40, 116, 46), fill=_c("white"))
        d.ellipse((112, 42, 115, 45), fill=_c("black"))
        d.polygon(((56, 58), (50, 24), (80, 54)), fill=_c("dark_green"))
        d.line((44, 80, 20, 92, 10, 80), fill=_c("green"), width=6)
        d.polygon(((128, 50), (150, 44), (146, 56)), fill=_c("orange"))
    elif kind == "night":
        return R11.WALL_HH.copy()
    return im.resize((320, 240), Image.NEAREST)


ART = {k: _art(k) for k in ("garden", "rocket", "cat", "fish", "house", "dragon", "night")}
DRAWINGS = (("garden", "Garden"), ("rocket", "Rocket"), ("cat", "Ginger"), ("fish", "Fish tank"),
            ("house", "My house"), ("night", "Night sky"), ("dragon", "Dragon"))


def thumb(kind, w, h):
    """A drawing at a card's size (the cover cache's any-ratio blit)."""
    return ART[kind].resize((w, h), Image.BOX).point(M.LUT565)


# ==========================================================================
# shared parts
# ==========================================================================
def paste_rect(cv, im, x, y, w, h, r=0):
    if im.size != (w, h):
        im = im.resize((w, h), Image.NEAREST)
    cv.paste(im, x, y, r=r)


def edge(T):
    """The 1 px edge a picture wears on its ground (Appearance's cards)."""
    return T["line"] if T["mode"] == "dark" else mix(T["line"], T["dim"], 0.25)


DANGER = {"light": "#b3261e", "dark": "#ff8f8f"}
SEL = {"light": ("#4c3fd6", "#ffffff"), "dark": ("#b6a8f7", "#15123a")}
DISABLED = {"light": "#9a9eb0", "dark": "#686c92"}


def danger(T):
    """The danger ink: the theme's, else moy.json's `danger_ink`."""
    return T["danger"] or DANGER[T["mode"]]


def own_ring(cv, T, x, y, w, h, r):
    """Focus on a part whose fill is the mark's own colour (GAPS 41): a 2 px
    `accent` ring at a 2 px gap of `surface`."""
    cv.ring(x - 2, y - 2, w + 4, h + 4, r + 2, 2, T["surface"])
    cv.ring(x - 4, y - 4, w + 8, h + 8, r + 4, 2, T["accent"])


def card_pic(cv, T, im, x, y, w, h, r, focus=False, current=False, pad=4):
    """A card's picture (`ui.card`): the current plate behind it, the lift
    when it holds the focus, else the picture with its 1 px edge."""
    if current:
        M.current_mark(cv, T, x - pad, y - pad, w + 2 * pad, h + 2 * pad, r + pad)
    if focus and M.FOCUS_ON[0]:
        lf = T["focus"]["lift"]
        d, up = lf["grow"], lf["rise"]
        bx, by, bw, bh = x - d, y - d - up, w + 2 * d, h + 2 * d
        dark = T["mode"] == "dark"
        cv.shadow(bx, by, bw, bh, r, ("soft", "#000000", 0.7 if dark else 0.32, 10, 5))
        paste_rect(cv, im, bx, by, bw, bh, r + 1)
        rc, ra = lf["rim"]
        cv.ring(bx - 1, by - 1, bw + 2, bh + 2, r + 2, 1, T.get(rc, rc), ra)
        return
    paste_rect(cv, im, x, y, w, h, r)
    cv.ring(x, y, w, h, r, 1, edge(T))


def fade(cv, T, x, y, w, h, c=None):
    """`ui.end()`'s fade: rows that run on below dissolve into the ground."""
    c = c or T["bg"]
    for i in range(h):
        cv.rect(x, y + i, w, 1, c, (i + 1) / h)


def scrim(cv, desk=False):
    """A modal's wash over the content between the bars: under the top bar,
    above the hint strip (DESIGN, Components)."""
    top = 40 if desk else 28
    cv.rect(0, top, cv.W, (cv.H if desk else 218) - top, "#000000", 0.4)


def card_surface(cv, T, x, y, w, h, r, clip=None):
    """A popover's or a dialog's card; `clip` keeps its shadow inside the
    content it hangs over."""
    if clip:
        cv.set_clip(*clip)
    M.soft(cv, T, x, y, w, h, r, 0.8)
    cv.clip = None
    cv.rrect(x, y, w, h, r, T["surface"])
    if T["mode"] == "dark":
        cv.ring(x, y, w, h, r, 1, T["line"])


def bar_tools(cv, T, title, slots, disabled=(), play=False):
    """The handheld bar's lent zone after the title: icons in the chrome
    ink (redo at half strength while there is nothing to redo), Play bare
    in `value` (DESIGN, Moy and the icons)."""
    ft = F(T, "htitle", HH)
    x = 52 + ft.width(title) + 12
    for s in slots:
        ink = T["value"] if s == "play" else T["text"]
        sicon(cv, T, s, x, 6, 16, ink, 0.4 if s in disabled else 1.0)
        x += 22
    return x


def ibtn(cv, T, slot, x, y, s, state="rest", desk=False, ink=None):
    """An icon `toggle` or `normal` button: the current mark when on, the
    focus mark by size class, the disabled ink when off."""
    r = 6 if s < 30 else 8
    ink = ink or T["text"]
    if state in ("current", "current+focus"):
        ink = M.current_mark(cv, T, x, y, s, s, r) or ink
    if state in ("focus", "current+focus"):
        ink = M.focus_mark(cv, T, (x, y, s, s, r), "small" if s < 28 else "part") or ink
    px = 16 if s < 30 else (20 if s < 40 else 24)
    sicon(cv, T, slot, x + (s - px) // 2, y + (s - px) // 2, px, ink,
          0.4 if state == "disabled" else 1.0)


def button(cv, T, kind, x, y, w, h, label, slot=None, focus=False, desk=False, disabled=False):
    """A labelled `ui.button` of its kind, an icon before its label
    (`ui.icon`); a `normal` one on a surface carries the structure line."""
    st = M.bstyle(T, "app", kind)
    r = min(8 if desk else 6, h / 2)
    cv.rrect(x, y, w, h, r, st["fill"])
    if kind != "primary":
        cv.ring(x, y, w, h, r, 1, mix(T["line"], T["dim"], 0.15))
    ink = danger(T) if kind == "danger" else st["ink"]
    if focus and kind == "primary" and M.ratio(T["accent"], st["fill"]) < 3:
        own_ring(cv, T, x, y, w, h, r)
    elif focus:
        ink = M.focus_mark(cv, T, (x, y, w, h, r), "part") or ink
    f = F(T, "chip", DK) if desk else Face(T["fam"], 12, T["w_semi"], 2)
    s = 18 if desk else 14
    lw = f.width(label) + ((s + 6) if slot else 0)
    lx = x + (w - lw) // 2
    a = 0.45 if disabled else 1.0
    if slot:
        sicon(cv, T, slot, lx, y + (h - s) // 2, s, ink, a)
        lx += s + 6
    cv.text(lx, M.mid_base(f, y, h), label, f, mix(ink, T["surface"], 0.55) if disabled else ink)


def caption(cv, T, x, base, text, desk=False, align="left"):
    f = R11.caption_dk(T) if desk else F(T, "status", HH)
    return cv.text(x, base, text, f, T["dim"], align=align)


def toast(cv, T, text, cx, y, desk=False):
    """A toast (GAPS 33): a pill in the text's colour over the content, in
    the ground's ink, for two seconds."""
    f = F(T, "meta", DK) if desk else Face(T["fam"], 12, T["w_med"], 2)
    h = 30 if desk else 20
    w = f.width(text) + (36 if desk else 24) + (22 if desk else 16)
    x = cx - w // 2
    M.soft(cv, T, x, y, w, h, h / 2, 0.6)
    cv.rrect(x, y, w, h, h / 2, T["text"], 0.94)
    s = 18 if desk else 14
    sicon(cv, T, "check", x + (12 if desk else 8), y + (h - s) // 2, s, T["bg"])
    cv.text(x + (12 if desk else 8) + s + 6, M.mid_base(f, y, h), text, f, T["bg"])


def progress(cv, T, x, y, w, label, v, hi, desk=False):
    """`ui.progress(label, v, hi)`: a row with a bar and its percent."""
    fl = F(T, "row", DK) if desk else Face(T["fam"], 13, T["w_med"], 2)
    fv = F(T, "meta", DK) if desk else F(T, "status", HH)
    rh = 28 if desk else 20
    if label:
        cv.text(x, M.mid_base(fl, y, rh), label, fl, T["text"])
    cv.text(x + w, M.mid_base(fv, y, rh), "%d%%" % (100 * v // hi), fv, T["dim"], align="right")
    by = y + rh + (2 if desk else 1)
    bh = 8 if desk else 6
    cv.rrect(x, by, w, bh, bh / 2, mix(T["line"], T["dim"], 0.25))
    cv.rrect(x, by, max(bh, w * v // hi), bh, bh / 2, T["value"])
    return by + bh


def empty_state(cv, T, cx, cy, slot, title, line, verb, verb_slot, desk=False, more=None):
    """An empty page: the app's icon on a plate, what is missing, and the
    one verb that fills it (`ui.label`s and a primary `ui.button`)."""
    d = 72 if desk else 52
    cv.shape(cx - d // 2, cy, d, d, lambda dr, s: dr.ellipse((0, 0, d * s - 1, d * s - 1), fill=255),
             T["raised"] if T["mode"] == "dark" else mix(T["value"], T["bg"], 0.9))
    s = 36 if desk else 26
    sicon(cv, T, slot, cx - s // 2, cy + (d - s) // 2, s, T["value"])
    ft = F(T, "wtitle", DK) if desk else F(T, "htitle", HH)
    fl = F(T, "meta", DK) if desk else Face(T["fam"], 12, T["w_body"], 2)
    y = cy + d + (32 if desk else 22)
    cv.text(cx, y, title, ft, T["text"], align="center")
    y += 22 if desk else 17
    for ln in line:
        cv.text(cx, y, ln, fl, T["dim"], align="center")
        y += 20 if desk else 15
    if verb:
        bw, bh = (180, 40) if desk else (124, 26)
        by = y + (6 if desk else 3)
        if more:
            g = 10 if desk else 8
            mw = bw - (40 if desk else 30)
            x0 = cx - (mw + g + bw) // 2
            button(cv, T, "normal", x0, by, mw, bh, more[0], more[1], desk=desk)
            button(cv, T, "primary", x0 + mw + g, by, bw, bh, verb, verb_slot, focus=True, desk=desk)
            return
        button(cv, T, "primary", cx - bw // 2, by, bw, bh, verb, verb_slot,
               focus=True, desk=desk)


def menu(cv, T, x, y, w, items, focus_i, desk=False, head=None, clip=None):
    """`ui.menu` in a `ui.popover`: rows with an icon, the label and the key
    each answers to; a danger row set apart at the foot; `head` names what
    the menu acts on."""
    rh = 34 if desk else 22
    seps = sum(1 for it in items if it[0] == "-")
    hh = (28 if desk else 20) if head else 0
    h = 10 + hh + (len(items) - seps) * rh + seps * 9
    card_surface(cv, T, x, y, w, h, 10 if desk else 8, clip)
    fr = F(T, "row", DK) if desk else Face(T["fam"], 13, T["w_med"], 2)
    fk = F(T, "meta", DK) if desk else F(T, "status", HH)
    s = 18 if desk else 14
    yy = y + 5
    if head:
        cv.text(x + 12, yy + hh - 6, head, F(T, "col", DK) if desk else F(T, "status", HH), T["dim"])
        yy += hh
    n = 0
    for it in items:
        if it[0] == "-":
            cv.rect(x + 10, yy + 4, w - 20, 1, T["line"])
            yy += 9
            continue
        slot, label, key = it[:3]
        is_danger = len(it) > 3 and it[3] == "danger"
        base = danger(T) if is_danger else T["text"]
        ink = M.focus_mark(cv, T, (x, yy + 1, w, rh - 2, 0), "part") if n == focus_i else None
        sicon(cv, T, slot, x + 12, yy + (rh - s) // 2, s, ink or (base if is_danger else T["dim"]))
        cv.text(x + 20 + s, M.mid_base(fr, yy, rh), label, fr, ink or base)
        if key:
            cv.text(x + w - 12, M.mid_base(fk, yy, rh), key, fk, T["dim"], align="right")
        yy += rh
        n += 1
    return h


def seg(cv, T, xr, y, h, opts, cur, desk=False, focus=False):
    """`ui.seg` right-aligned at xr (Appearance's Mode)."""
    fv = F(T, "meta", DK) if desk else F(T, "status", HH)
    R11._value(cv, T, "seg", (opts, cur), xr, y, h, fv, desk)


def swatches(cv, T, xr, y, h, cols, cur, desk=False, d=None, p=None):
    """`ui.swatches`, right-aligned at xr; None is the clear swatch."""
    d = d or (16 if desk else 11)
    p = p or (22 if desk else 15)
    x = xr - d
    for i, c in reversed(list(enumerate(cols))):
        cy = y + (h - d) // 2
        if c is None:
            cv.shape(x, cy, d, d, lambda dr, s: dr.ellipse((0, 0, d * s - 1, d * s - 1), fill=255),
                     T["surface"])
            cv.ring(x, cy, d, d, d / 2, 1, T["dim"])
            cv.shape(x, cy, d, d, lambda dr, s: dr.line((d * s * 0.25, d * s * 0.75, d * s * 0.75,
                                                         d * s * 0.25), fill=255, width=int(s * 1.4)),
                     danger(T))
        else:
            cv.shape(x, cy, d, d, lambda dr, s: dr.ellipse((0, 0, d * s - 1, d * s - 1), fill=255), c)
            if M.ratio(c, T["surface"]) < 1.3:
                cv.ring(x, cy, d, d, d / 2, 1, edge(T))
        if i == cur:
            cv.ring(x - 3, cy - 3, d + 6, d + 6, (d + 6) / 2, 2, T["value"])
        x -= p


def row_items(cv, T, x, y, w, rh, items, focus_i, desk=False, group=True):
    """R11.rows with this round's value kinds: (label, kind, value) where
    kind adds `seg2` (opts, cur), `sw` (cols, cur), `badge` and `none`."""
    pad = 14 if desk else 10
    fl = F(T, "row", DK) if desk else Face(T["fam"], 13, T["w_med"], 2)
    fv = F(T, "meta", DK) if desk else F(T, "status", HH)
    if group:
        cv.rrect(x, y, w, rh * len(items), 8, T["surface"])
        if T["mode"] == "dark":
            cv.ring(x, y, w, rh * len(items), 8, 1, T["line"])
    for i, (label, kind, val) in enumerate(items):
        ry = y + i * rh
        if i:
            cv.rect(x + (pad if group else 0), ry, w - (2 * pad if group else 0), 1, T["line"])
        ink = M.focus_mark(cv, T, (x, ry, w, rh, 0), "part") if i == focus_i else None
        lx = x + (pad if group else 10)
        cv.text(lx, M.mid_base(fl, ry, rh), label, fl, ink or T["text"])
        xr = x + w - (pad if group else 10)
        if kind == "seg2":
            seg(cv, T, xr, ry, rh, val[0], val[1], desk)
        elif kind == "sw":
            swatches(cv, T, xr, ry, rh, val[0], val[1], desk)
        elif kind == "badge":
            cv.text(xr, M.mid_base(fv, ry, rh), val, fv, T["dim"], align="right")
        elif kind != "none":
            R11._value(cv, T, kind, val, xr, ry, rh, fv, desk)
    return y + rh * len(items)


def desk(T, chips):
    cv = M.new_cv(T, True)
    M.backdrop(cv, T, "desk")
    M.desk_icons(cv, T)
    R8.chips_bar(cv, T, chips)
    return cv


def win(cv, T, x, y, w, h, title, slot, back=False):
    """A focused window; its line icon from the set, the chevron first
    while a page of its app is open (step 27a)."""
    top = M.window(cv, T, x, y, w, h, title, "none", True)
    ink = R8.title_ink(T, True)
    hh = T["win"]["h"]
    tx = x + 16
    if back:
        sicon(cv, T, "back", tx - 4, y + (hh - 20) // 2, 20, ink)
        cv.rect(tx + 20, y + 4, 34, hh - 8, T["win"]["body"] or T["surface"])
        tx += 26
    sicon(cv, T, slot, tx, y + (hh - 20) // 2, 20, ink)
    if back:
        ft = F(T, "wtitle", DK)
        cv.rect(tx + 24, y + 6, ft.width(title) + 40, hh - 12, T["win"]["body"] or T["surface"])
        cv.text(tx + 30, M.mid_base(ft, y, hh), title, ft, ink)
    return top


def desk_foot(cv, T, x, y, w, status, verbs=(), hint=()):
    """A window's foot: a rule, the status at the left, the verbs (the one
    Enter does last and primary) or the hints at the right."""
    cv.rect(x + 16, y, w - 32, 1, T["line"])
    fm = F(T, "meta", DK)
    cv.text(x + 26, y + 28, status, fm, T["dim"])
    xr = x + w - 16
    for label, slot, kind in reversed(verbs):
        f = F(T, "chip", DK)
        bw = f.width(label) + (48 if slot else 28)
        xr -= bw
        button(cv, T, kind, xr, y + 8, bw, 30, label, slot, desk=True)
        xr -= 8
    if hint:
        R11.hints(cv, T, hint, y + 29, x=x + w - 26 - R11.hints_width(T, hint, True),
                  bg=T["surface"], desk=True)


def hh_hints(cv, T, items, bg=None, lead=None):
    """The hint strip; `lead` names the part the keys act on (Paint's tool).
    It ends 4 px or more short of the glass's edge."""
    x = 8
    if lead:
        x += cv.text(x, 233, lead, Face(T["fam"], 12, T["w_semi"], 2), T["text"]) + 10
    R11.hints(cv, T, items, 233, x=x, bg=bg)


def bar_wifi_off(cv, T):
    """The bar's Wi-Fi as it shows with no link: `wifi_off`."""
    fs = F(T, "status", HH)
    ix = 320 - 7 - fs.width("14:32") - 41
    cv.rect(ix - 1, 3, 18, 20, T["bg"])
    sicon(cv, T, "wifi_off", ix, 6, 15, T["text"])


def tooltip(cv, T, x, y, text):
    """A desk tooltip under a pointer: the label and its keys."""
    f = F(T, "meta", DK)
    w = f.width(text) + 20
    M.soft(cv, T, x, y, w, 26, 6, 0.5)
    cv.rrect(x, y, w, 26, 6, T["text"])
    cv.text(x + 10, M.mid_base(f, y, 26), text, f, T["bg"])


def ring_cursor(cv, T, x, y, w, h):
    """The canvas cursor: the theme's ring around a black keyline, edged in
    `surface` (the sprite editor's, `MOY_UI_MARK_RING`)."""
    cv.ring(x - 1, y - 1, w + 2, h + 2, 0, 1, "#000000")
    cv.ring(x - 2, y - 2, w + 4, h + 4, 1, 1, T["surface"])
    cv.ring(x - 4, y - 4, w + 8, h + 8, 3, 2, T["accent"])
    cv.ring(x - 5, y - 5, w + 10, h + 10, 4, 1, T["surface"], 0.8)


# ==========================================================================
# Paint: the canvas and its tools, the pickers, the menu
# ==========================================================================
TOOLS = (("pencil", "Pencil", "P"), ("paint", "Brush", "B"), ("eraser", "Eraser", "E"),
         ("bucket", "Fill", "F"), ("rect", "Shape", "S"), ("spray", "Spray", "A"),
         ("picker", "Pick", "I"), ("pan", "Move", "Space"))
COLOUR = 12                     # blue, the colour in use in every Paint scene
VIEW = (32, 31, 256, 184)       # the handheld's drawing view


def two_tone(cv, T, x, y, w, h):
    """The colour in use: a line of `ink` and one of `surface` within, never
    a palette colour (the sprite editor's swatch)."""
    cv.ring(x, y, w, h, 0, 1, T["text"])
    cv.ring(x + 1, y + 1, w - 2, h - 2, 0, 1, T["surface"])


def size_dot(cv, T, x, y, w, h, size, ink):
    """The brush button: a dot as wide as the brush (1, 2 or 4 px at 1x)."""
    cv.shape(x + (w - size) / 2, y + (h - size) / 2, size, size,
             lambda dr, k: dr.ellipse((0, 0, size * k - 1, size * k - 1), fill=255), ink)


def tool_cells(cv, T, x0, y0, s, pitch, cols, cur, desk=False):
    """The tools as toggles; Pick carries the colour it would take as a drip."""
    px = 16 if s < 30 else 20
    for i, (slot, _n, _k) in enumerate(TOOLS):
        x, y = x0 + (i % cols) * pitch, y0 + (i // cols) * pitch
        ibtn(cv, T, slot, x, y, s, "current" if i == cur else "rest", desk=desk)
        if slot == "picker":
            o, k = (s - px) / 2, px / 24
            d = max(3, px // 6)
            cv.shape(x + o + 5.8 * k - d / 2, y + o + 20 * k - d / 2, d, d,
                     lambda dr, q: dr.ellipse((0, 0, d * q - 1, d * q - 1), fill=255), P64[COLOUR])


def zoom_chip(cv, T, x):
    fz = Face(T["fam"], 12, T["w_semi"], 2)
    cv.rrect(x, 5, 28, 18, 5, T["raised"])
    cv.ring(x, 5, 28, 18, 5, 1, mix(T["line"], T["dim"], 0.25))
    cv.text(x + 14, M.mid_base(fz, 5, 18), "1×", fz, T["text"], align="center")


def drawing_view(cv, T, vx, vy, vw, vh, cursor=None):
    """The drawing at 1x, opened centred on where it was last drawn on."""
    paste_rect(cv, ART["garden"].crop((20, 34, 20 + vw, 34 + vh)), vx, vy, vw, vh, 6)
    cv.ring(vx, vy, vw, vh, 6, 1, edge(T))
    cv.rrect(vx + vw - 5, vy + 30, 3, max(20, vh - 144), 1.5, T["text"], 0.35)
    cv.rrect(vx + 50, vy + vh - 5, vw - 66, 3, 1.5, T["text"], 0.35)
    if cursor:
        ring_cursor(cv, T, cursor[0], cursor[1], 2, 2)


def paint_handheld(T, state="main"):
    """The T-Deck (keys and trackball)."""
    cv = R11.hh_page(T, "Paint")
    zoom_chip(cv, T, bar_tools(cv, T, "Paint", ("undo", "redo"), disabled=("redo",)))
    cur = 4 if state == "brush" else 0
    tool_cells(cv, T, 4, 31, 22, 23, 1, cur)
    drawing_view(cv, T, *VIEW, cursor=(152, 104) if state == "main" else None)
    rx = 292
    cv.rect(rx, 31, 24, 22, P64[COLOUR])
    two_tone(cv, T, rx, 31, 24, 22)
    for i in range(16):
        sx, sy = rx + (i % 2) * 12, 58 + (i // 2) * 12
        cv.rect(sx, sy, 12, 12, P64[i])
        if i == COLOUR:
            two_tone(cv, T, sx, sy, 12, 12)
    cv.ring(rx, 58, 24, 96, 0, 1, edge(T))
    if state == "brush":
        M.current_mark(cv, T, rx, 160, 24, 22, 6)
    size_dot(cv, T, rx, 160, 24, 22, 4 if state == "brush" else 2, T["text"])
    ibtn(cv, T, "more", rx, 186, 24, "current" if state == "more" else "rest")
    if state == "main":
        toast(cv, T, "Saved Garden", 160, 186)
        hh_hints(cv, T, (("enter", "Draw"), ("C", "Colours"), ("M", "More")), lead="Pencil ·")
        return cv
    hh_hints(cv, T, (("Esc", "Close"),))
    clip = VIEW
    if state == "colours":
        px, py, pw = 124, 31, 162
        card_surface(cv, T, px, py, pw, 184, 8, clip)
        ft = Face(T["fam"], 13, T["w_semi"], 2)
        cv.text(px + 10, py + 18, "Colour", ft, T["text"])
        cv.text(px + pw - 10, py + 18, "14 · Pink", F(T, "status", HH), T["dim"], align="right")
        for i in range(64):
            sx, sy = px + 10 + (i % 8) * 18, py + 30 + (i // 8) * 18
            cv.rect(sx, sy, 16, 16, P64[i])
            if i == COLOUR:
                two_tone(cv, T, sx, sy, 16, 16)
        cv.ring(px + 9, py + 29, 8 * 18, 8 * 18, 0, 1, edge(T))
        ring_cursor(cv, T, px + 10 + 6 * 18, py + 30 + 18, 16, 16)
    elif state == "brush":
        px, pw, ph = 92, 194, 98
        py = VIEW[1] + VIEW[3] - ph
        card_surface(cv, T, px, py, pw, ph, 8, clip)
        ft = Face(T["fam"], 13, T["w_semi"], 2)
        cv.text(px + 10, py + 17, "Shape", ft, T["text"])
        row_items(cv, T, px, py + 24, pw, 22,
                  (("Size", "seg2", (("1", "2", "4"), 2)),
                   ("Shape", "seg2", (("Line", "Rect", "Oval"), 1)),
                   ("Fill", "seg2", (("Outline", "Filled"), 0))), 0, group=False)
    elif state == "more":
        menu(cv, T, 104, 79, 182,
             (("new", "New drawing", "N"), ("folder_open", "Open…", "O"), ("-",),
              ("wallpaper", "Use as wallpaper", "W"), ("export", "Put in a game…", "G"),
              ("maximize", "Show", "V")), 3, clip=clip)
    return cv


def paint_touch(T, state="main"):
    """A touch board's Paint (the Guition S3): every target 28 px or more,
    the tools two by four, the colour in use the one colour target, no hint
    strip."""
    cv = R11.hh_page(T, "Paint")
    zoom_chip(cv, T, bar_tools(cv, T, "Paint", ("undo", "redo"), disabled=("redo",)))
    tool_cells(cv, T, 4, 30, 28, 32, 2, 0)
    if state == "colours":
        M.current_mark(cv, T, 1, 157, 34, 34, 8)
    cv.rect(4, 160, 28, 28, P64[COLOUR])
    two_tone(cv, T, 4, 160, 28, 28)
    size_dot(cv, T, 36, 160, 28, 28, 2, T["text"])
    cv.ring(36, 160, 28, 28, 6, 1, mix(T["line"], T["dim"], 0.25))
    ibtn(cv, T, "more", 4, 196, 28)
    view = (68, 30, 248, 206)
    drawing_view(cv, T, *view)
    if state == "colours":
        px, py, pw, ph = view
        card_surface(cv, T, px, py, pw, ph, 6, view)
        ft = Face(T["fam"], 13, T["w_semi"], 2)
        cv.text(px + 12, py + 20, "Colour", ft, T["text"])
        cv.text(px + pw - 12, py + 20, "12 · Blue", F(T, "status", HH), T["dim"], align="right")
        for i in range(28):
            sx, sy = px + 12 + (i % 7) * 32, py + 28 + (i // 7) * 32
            cv.rect(sx, sy, 30, 30, P64[i])
            if i == COLOUR:
                two_tone(cv, T, sx, sy, 30, 30)
        cv.ring(px + 11, py + 27, 7 * 32, 4 * 32, 0, 1, edge(T))
        seg(cv, T, px + pw // 2 + 62, py + ph - 40, 36, ("1–28", "29–56", "57–64"), 0)
    return cv


def paint_game(T):
    """Put in a game: the projects, a picture each (Files' table)."""
    cv = R11.hh_page(T, "Put in a game")
    caption(cv, T, 16, 42, "Garden becomes the background of…")
    fl = Face(T["fam"], 13, T["w_med"], 2)
    names = (("star_catcher", "Star Catcher"), ("sky_run", "Sky Run"), ("hop_quest", "Hop Quest"),
             ("harpoon_pop", "Harpoon Pop"), ("brick_siege", "Brick Siege"),
             ("pixel_pet", "Pixel Pet"), ("bullet_storm", "Bullet Storm"),
             ("letter_blitz", "Letter Blitz"))
    x, y, w, rh = 6, 48, 308, 22
    cv.rrect(x, y, w, rh * len(names), 8, T["surface"])
    if T["mode"] == "dark":
        cv.ring(x, y, w, rh * len(names), 8, 1, T["line"])
    for i, (key, title) in enumerate(names):
        ry = y + i * rh
        if i:
            cv.rect(x + 10, ry, w - 20, 1, T["line"])
        ink = M.focus_mark(cv, T, (x, ry, w, rh, 0), "part") if i == 1 else None
        M.file_thumb(cv, T, key, x + 12, ry + 3, 16)
        cv.text(x + 36, M.mid_base(fl, ry, rh), title, fl, ink or T["text"])
    fade(cv, T, 0, 204, 320, 14)
    cv.rect(0, 218, 320, 22, T["bg"])
    hh_hints(cv, T, (("enter", "Add"), ("Esc", "Back")))
    return cv


# ==========================================================================
# My Art: the drawings as cards (Paint's Open, Files' Art)
# ==========================================================================
def badge(cv, T, x, y, slot, desk=False, ink=None):
    """A card's badge (GAPS 34): a disc on the picture's corner."""
    d = 26 if desk else 18
    M.soft(cv, T, x - d // 2, y - d // 2, d, d, d / 2, 0.4)
    cv.shape(x - d // 2, y - d // 2, d, d,
             lambda dr, s: dr.ellipse((0, 0, d * s - 1, d * s - 1), fill=255), T["surface"])
    s = 16 if desk else 12
    sicon(cv, T, slot, x - s // 2, y - s // 2, s, ink or T["value"])


def new_card(cv, T, x, y, w, h, desk=False, focus=False):
    """The grid's first card, the verb that adds one."""
    r = 8 if desk else 6
    cv.rrect(x, y, w, h, r, T["surface"] if T["mode"] == "light" else T["raised"])
    cv.ring(x, y, w, h, r, 1.5, mix(T["line"], T["dim"], 0.3))
    s = 28 if desk else 20
    sicon(cv, T, "new", x + (w - s) // 2, y + (h - s) // 2, s, T["value"])
    if focus:
        M.focus_mark(cv, T, (x, y, w, h, r), "card")


def art_cards(cv, T, x0, y0, cw, ch, gap, cols, rows_, focus_i, desk=False, menu_open=False):
    fn = F(T, "row", DK) if desk else Face(T["fam"], 12, T["w_med"], 2)
    r = 8 if desk else 6
    items = [None] + list(DRAWINGS)
    for i, it in enumerate(items[:cols * rows_]):
        cx, cy = x0 + (i % cols) * (cw + gap), y0 + (i // cols) * (ch + (44 if desk else 30))
        if it is None:
            new_card(cv, T, cx, cy, cw, ch, desk)
            name = "New drawing"
        else:
            card_pic(cv, T, thumb(it[0], cw, ch), cx, cy, cw, ch, r,
                     focus=(i == focus_i and not menu_open),
                     current=(i == focus_i and menu_open))
            name = it[1]
            if it[0] == "garden":
                badge(cv, T, cx + cw - (18 if desk else 12), cy + (18 if desk else 12),
                      "wallpaper", desk)
        cv.text(cx + cw // 2, cy + ch + (24 if desk else 15), name, fn,
                T["value"] if it is None else T["text"], align="center", maxw=cw)


def my_art(T, state="grid"):
    cv = R11.hh_page(T, "My Art")
    if state == "empty":
        empty_state(cv, T, 160, 40, "art", "No drawings yet",
                    ("Draw something in Paint", "and it lands here."), "New drawing", "new")
        hh_hints(cv, T, (("enter", "New drawing"), ("Esc", "Back")))
        return cv
    caption(cv, T, 16, 42, "7 drawings")
    caption(cv, T, 304, 42, "Wallpaper: Garden", align="right")
    art_cards(cv, T, 16, 54, 88, 66, 12, 3, 3, 2, menu_open=state == "menu")
    fade(cv, T, 0, 204, 320, 14)
    cv.rect(0, 218, 320, 22, T["bg"])
    if state == "menu":
        scrim(cv)
        menu(cv, T, 120, 46, 186,
             (("paint", "Open in Paint", "Enter"), ("wallpaper", "Use as wallpaper", "W"),
              ("export", "Put in a game…", "G"), ("pencil", "Rename…", "R"),
              ("dup", "Duplicate", "Ctrl D"), ("-",), ("trash", "Move to Trash", "Del", "danger")),
             0, head="Rocket")
        hh_hints(cv, T, (("Esc", "Close"),))
    else:
        hh_hints(cv, T, (("enter", "Open"), ("N", "New"), ("M", "Menu"), ("Esc", "Back")))
    return cv


def my_art_desk(T):
    cv = desk(T, (("art", "My Art"), ("paint", "Paint")))
    x, y, w, h = 112, 52, 900, 536
    top = win(cv, T, x, y, w, h, "My Art", "art")
    caption(cv, T, x + 24, top + 26, "7 drawings", desk=True)
    caption(cv, T, x + w - 24, top + 26, "Wallpaper: Garden", desk=True, align="right")
    art_cards(cv, T, x + 32, top + 44, 152, 114, 22, 5, 3, 2, desk=True)
    body = T["win"]["body"] or T["surface"]
    fade(cv, T, x + 8, y + h - 74, w - 16, 30, body)
    cv.rect(x + 8, y + h - 44, w - 16, 36, body)
    desk_foot(cv, T, x, y + h - 44, w, "Rocket · 320 × 240 · today",
              verbs=(("Move to Trash", "trash", "danger"), ("Use as wallpaper", "wallpaper", "normal"),
                     ("Put in a game", "export", "normal"), ("Open", "paint", "primary")))
    return cv


# ==========================================================================
# Get Carts: the shelf as cards, a cart's page, the download, offline
# ==========================================================================
STORE = (("Pixel Pet", "pixel_pet", "On console", "installed"),
         ("Hop Quest", "hop_quest", "Update ready", "update"),
         ("Bullet Storm", "bullet_storm", "96 KB", "get"),
         ("Doom", None, "Too big", "too_big"),
         ("Letter Blitz", "letter_blitz", "22 KB", "get"),
         ("Tiny Runner", "tiny_runner", "Can't play", "too_big"),
         ("Star Catcher", "star_catcher", "On console", "installed"),
         ("Harpoon Pop", "harpoon_pop", "31 KB", "get"))
STATE_BADGE = {"installed": "check", "update": "getcarts", "too_big": "warn"}


def cover_img(key, n, title="?", T=None):
    """The whole 128 px cover at a clean scale (GAPS 21), else its genre's
    icon on a tile tinted from `value`."""
    if key is not None:
        im = Image.open(M._src(key)).convert("RGB")
        im = im.resize((n, n), Image.BOX if n < 128 else Image.NEAREST)
        return im.point(M.LUT565)
    T = T or LIGHT
    tint = mix(T["value"], T["surface"], 0.82 if T["mode"] == "light" else 0.7)
    tmp = M.Cv(n, n, tint)
    s = n // 2
    sicon(tmp, T, "game", (n - s) // 2, (n - s) // 2, s, T["value"])
    return tmp.image()


def store_card(cv, T, i, x, y, n, focus, desk=False):
    title, key, sub, st = STORE[i]
    im = cover_img(key, n, title, T)
    if st == "too_big":
        im = Image.blend(im, Image.new("RGB", im.size, M.q(T["bg"])), 0.55)
    card_pic(cv, T, im, x, y, n, n, 10 if desk else 8, focus=focus)
    if st in STATE_BADGE:
        o = 16 if desk else 11
        badge(cv, T, x + n - o, y + o, STATE_BADGE[st], desk,
              danger(T) if st == "too_big" else None)
    ft = F(T, "row", DK) if desk else Face(T["fam"], 12, T["w_semi"], 2)
    fs = F(T, "meta", DK) if desk else Face(T["fam"], 11, T["w_body"], 2)
    cv.text(x + n // 2, y + n + (24 if desk else 14), title, ft, T["text"], align="center",
            maxw=n + (16 if desk else 32))
    ink = T["value"] if st == "update" else (danger(T) if st == "too_big" else T["dim"])
    cv.text(x + n // 2, y + n + (44 if desk else 27), sub, fs, ink, align="center",
            maxw=n + (16 if desk else 32))


def getcarts(T, state="grid"):
    title = {"grid": "Get Carts", "cart": "Hop Quest", "getting": "Hop Quest",
             "offline": "Get Carts"}[state]
    cv = R11.hh_page(T, title)
    if state == "grid":
        caption(cv, T, 16, 42, "24 carts")
        caption(cv, T, 304, 42, "3.1 MB free", align="right")
        for i in range(6):
            store_card(cv, T, i, 16 + (i % 3) * 112, 52 + (i // 3) * 104, 64, i == 1)
        fade(cv, T, 0, 202, 320, 16)
        cv.rect(0, 218, 320, 22, T["bg"])
        hh_hints(cv, T, (("enter", "Open"), ("R", "Check"), ("Esc", "Back")))
    elif state == "cart":
        card_pic(cv, T, cover_img("hop_quest", 64), 12, 36, 64, 64, 8)
        fm = F(T, "status", HH)
        fd = Face(T["fam"], 12, T["w_body"], 2)
        cv.text(88, 46, "Platformer · moybyte-org", fm, T["dim"])
        sicon(cv, T, "getcarts", 88, 52, 14, T["value"])
        cv.text(106, 63, "Update ready: 1.2 → 1.3", Face(T["fam"], 12, T["w_semi"], 2), T["value"])
        cv.text(88, 81, "Hop across the hills, grab every", fd, T["text"])
        cv.text(88, 95, "coin and find the flag.", fd, T["text"])
        row_items(cv, T, 6, 108, 308, 20,
                  (("Licence", "info", "MIT"), ("Size", "info", "18 KB here · 12 KB to fetch"),
                   ("From", "info", "carts.moybyte.org")), None)
        button(cv, T, "danger", 6, 178, 92, 26, "Remove", "trash")
        button(cv, T, "normal", 104, 178, 92, 26, "Play", "play")
        button(cv, T, "primary", 202, 178, 112, 26, "Update", "getcarts", focus=True)
        hh_hints(cv, T, (("enter", "Update"), ("Esc", "Back")))
    elif state == "getting":
        card_pic(cv, T, cover_img("hop_quest", 64), 128, 36, 64, 64, 8)
        ft = F(T, "htitle", HH)
        cv.text(160, 122, "Getting Hop Quest", ft, T["text"], align="center")
        progress(cv, T, 40, 128, 240, "Downloading", 62, 100)
        fm = F(T, "status", HH)
        cv.text(160, 172, "11 KB of 18 KB · 140 KB/s", fm, T["dim"], align="center")
        button(cv, T, "danger", 112, 184, 96, 26, "Cancel", "close", focus=True)
        hh_hints(cv, T, (("enter", "Cancel"),))
    else:
        bar_wifi_off(cv, T)
        empty_state(cv, T, 160, 36, "wifi_off", "Wi-Fi is off",
                    ("Join a network and Get Carts", "looks again by itself."), "Wi-Fi", "wifi",
                    more=("Try again", "rotate"))
        hh_hints(cv, T, (("enter", "Wi-Fi"), ("Esc", "Back")))
    return cv


def getcarts_desk(T, state="cart"):
    cv = desk(T, (("getcarts", "Get Carts"), ("folder", "Files")))
    x, y, w, h = 112, 52, 900, 536
    top = win(cv, T, x, y, w, h, "Get Carts", "getcarts")
    caption(cv, T, x + 24, top + 26, "24 carts · 3.1 MB free", desk=True)
    button(cv, T, "normal", x + 486, top + 8, 104, 30, "Check", "rotate", desk=True)
    for i in range(8):
        store_card(cv, T, i, x + 24 + (i % 4) * 144, top + 48 + (i // 4) * 196, 128, i == 1,
                   desk=True)
    # the cart's page beside the shelf
    px = x + 620
    cv.rect(px - 10, top, 1, h - 44 - 44, T["line"])
    card_pic(cv, T, cover_img("hop_quest", 128), px + 8, top + 16, 128, 128, 10)
    ft = Face(T["fam"], 22, T["w_semi"], 4)
    fm, fr = F(T, "meta", DK), F(T, "row", DK)
    cv.text(px + 150, top + 40, "Hop Quest", ft, T["text"])
    cv.text(px + 150, top + 62, "Platformer", fm, T["dim"])
    cv.text(px + 150, top + 82, "moybyte-org", fm, T["dim"])
    if state == "cart":
        sicon(cv, T, "getcarts", px + 150, top + 98, 18, T["value"])
        cv.text(px + 174, top + 113, "Update ready", Face(T["fam"], 14, T["w_semi"], 4), T["value"])
    for j, ln in enumerate(("Hop across the hills, grab every coin", "before the clouds roll in, and find",
                            "the flag at the end of the day.")):
        cv.text(px + 8, top + 172 + j * 20, ln, fr, T["text"])
    if state == "cart":
        row_items(cv, T, px - 2, top + 236, 272, 28,
                  (("Licence", "info", "MIT"), ("Version", "info", "1.2 → 1.3"),
                   ("Takes", "info", "18 KB here"), ("Fetch", "info", "12 KB"),
                   ("From", "info", "carts.moybyte.org")), None, desk=True, group=False)
        desk_foot(cv, T, x, y + h - 44, w, "Hop Quest · update ready",
                  verbs=(("Remove", "trash", "danger"), ("Play", "play", "normal"),
                         ("Update", "getcarts", "primary")))
    else:
        cv.text(px + 8, top + 262, "Getting Hop Quest", Face(T["fam"], 16, T["w_semi"], 4), T["text"])
        progress(cv, T, px + 8, top + 270, 252, "Downloading", 62, 100, desk=True)
        cv.text(px + 8, top + 334, "11 KB of 18 KB · 140 KB/s", fm, T["dim"])
        desk_foot(cv, T, x, y + h - 44, w, "Getting Hop Quest",
                  verbs=(("Cancel", "close", "danger"),))
    return cv


# ==========================================================================
# Notes: the vault as a list, a note open, the empty vault, a new name
# ==========================================================================
NOTES = (("Shopping list", "MD"), ("Story ideas", "MD"), ("Dragon facts", "MD"),
         ("todo.txt", "TXT"), ("scores.json", "JSON"), ("hello.py", "PY"), ("Party plan", "MD"),
         ("maze.lua", "LUA"))
NOTE_ICON = {"MD": "notes", "TXT": "doc", "JSON": "doc", "PY": "code", "LUA": "code"}


def note_rows(cv, T, x, y, w, rh, focus_i, current_i=None, desk=False, group=True):
    """`ui.table`: New note first (a link in `value`), then a row a note:
    its kind's icon and its name (a note's bare, another file's with its
    extension)."""
    fl = F(T, "row", DK) if desk else Face(T["fam"], 13, T["w_med"], 2)
    items = [("New note", None)] + list(NOTES)
    n = len(items)
    if group:
        cv.rrect(x, y, w, rh * n, 8, T["surface"])
        if T["mode"] == "dark":
            cv.ring(x, y, w, rh * n, 8, 1, T["line"])
    s = 18 if desk else 14
    pad = 12 if desk else 10
    for i, (name, kind) in enumerate(items):
        ry = y + i * rh
        if i and group:
            cv.rect(x + pad, ry, w - 2 * pad, 1, T["line"])
        ink = None
        if i == current_i:
            ink = M.current_mark(cv, T, x + 4, ry + 2, w - 8, rh - 4, 6)
        if i == focus_i:
            ink = M.focus_mark(cv, T, (x, ry, w, rh, 0), "part") or ink
        base = T["value"] if kind is None else T["text"]
        sicon(cv, T, "new" if kind is None else NOTE_ICON[kind], x + pad, ry + (rh - s) // 2, s,
              ink or (T["value"] if kind is None else T["dim"]))
        cv.text(x + pad + s + 8, M.mid_base(fl, ry, rh), name, fl, ink or base)
    return y + rh * n


def note_doc(cv, T, x, y, w, h, desk=False, selecting=True):
    """The note as the editor handle draws it (`ui.part`): Markdown in the
    mono cells -- a heading, checkboxes, a link, a drawing -- on the paper."""
    cv.rrect(x, y, w, h, 8 if not desk else 0, T["surface"])
    if T["mode"] == "dark" and not desk:
        cv.ring(x, y, w, h, 8, 1, T["line"])
    f = R7.code_face(T, desk)
    fb = Face("jbmono", f.size if hasattr(f, "size") else (15 if desk else 11), 700, 4 if desk else 2)
    adv = f.glyph("m")[3] * cv.k
    lh = 22 if desk else 15
    px = x + (16 if desk else 10)
    yy = y + (12 if desk else 6)

    def base(i):
        return yy + i * lh + (lh + f.cap) // 2 - 1
    cv.text(px, base(0), "# ", f, DISABLED[T["mode"]])
    cv.text(px + 2 * adv, base(0), "Story ideas", fb, T["text"])
    sel, sel_ink = SEL[T["mode"]]
    for i, (done, words) in enumerate(((True, "A dragon who is scared of the dark"),
                                       (False, "Moy finds a map in the sand"),
                                       (False, "A race to the moon"))):
        b = base(2 + i)
        bx, bs = px + 2, 9 if not desk else 13
        by = b - f.cap // 2 - bs // 2 - 1
        if done:
            cv.rrect(bx, by, bs, bs, 2, T["value"])
            sicon(cv, T, "check", bx - 1, by - 1, bs + 2, T["value_ink"])
        else:
            cv.ring(bx, by, bs, bs, 2, 1.2, T["dim"])
        tx = px + 3 * adv
        cv.text(tx, b, words, f, T["dim"] if done else T["text"])
        if selecting and i == 0:
            s0 = words.index("scared")
            cv.rect(tx + s0 * adv, yy + (2 + i) * lh + 1, (len(words) - s0) * adv, lh - 2, sel)
            cv.text(tx + s0 * adv, b, words[s0:], f, sel_ink)
        if selecting and i == 0:
            cv.rect(tx + len(words) * adv, yy + (2 + i) * lh + 2, 2, lh - 4, T["accent"])
    b = base(6)
    cv.text(px, b, "Read ", f, T["text"])
    lx = px + 5 * adv
    cv.text(lx, b, "Dragon facts", f, T["value"])
    cv.rect(lx, b + 2, 12 * adv, 1, T["value"])
    cv.text(lx + 12 * adv, b, " first.", f, T["text"])
    iw, ih = (192, 144) if desk else (64, 48)
    iy = yy + 7 * lh + 4
    if iy + ih <= y + h - 4:
        card_pic(cv, T, thumb("dragon", iw, ih), px, iy, iw, ih, 4)


def notes(T, state="list"):
    if state == "edit":
        cv = R11.hh_page(T, "Story ideas")
        bar_tools(cv, T, "Story ideas", ("undo", "redo"), disabled=("redo",))
        note_doc(cv, T, 6, 31, 308, 172)
        fl = Face(T["fam"], 11, T["w_med"], 2)
        for i, (slot, label, st) in enumerate((("select", "Select", "current"), ("copy", "Copy", "rest"),
                                               ("cut", "Cut", "rest"), ("paste", "Paste", "rest"))):
            cx = 6 + i * 46
            ink = T["text"]
            if st == "current":
                ink = M.current_mark(cv, T, cx, 206, 42, 32, 6) or ink
            sicon(cv, T, slot, cx + 13, 207, 16, ink)
            cv.text(cx + 21, 235, label, fl, ink if st == "current" else T["dim"], align="center")
        fm = F(T, "status", HH)
        cv.text(314, 227, "18 letters", fm, T["dim"], align="right")
        return cv
    cv = R11.hh_page(T, "Notes")
    if state == "empty":
        empty_state(cv, T, 160, 40, "notes", "No notes yet",
                    ("A note can be a list, a story,", "a plan or a little program."),
                    "New note", "new")
        hh_hints(cv, T, (("enter", "New note"), ("Esc", "Back")))
        return cv
    caption(cv, T, 16, 42, "8 notes")
    note_rows(cv, T, 6, 48, 308, 20, 2 if state == "list" else None)
    fade(cv, T, 0, 204, 320, 14)
    cv.rect(0, 218, 320, 22, T["bg"])
    if state == "list":
        hh_hints(cv, T, (("enter", "Open"), ("N", "New"), ("M", "Menu"), ("Del", "Trash")))
        return cv
    scrim(cv)
    dx, dy, dw, dh = 26, 46, 268, 142
    card_surface(cv, T, dx, dy, dw, dh, 10)
    cv.text(dx + 14, dy + 24, "New note", F(T, "htitle", HH), T["text"])
    fy = dy + 36
    fl = Face(T["fam"], 13, T["w_med"], 2)
    cv.rrect(dx + 14, fy, dw - 28, 26, 6, T["surface"])
    cv.ring(dx + 14, fy, dw - 28, 26, 6, 1.5, T["accent"])
    cv.rect(dx + 23, fy + 6, 2, 14, T["accent"])
    cv.text(dx + 28, M.mid_base(fl, fy, 26), "my note", fl, DISABLED[T["mode"]])
    caption(cv, T, dx + 14, fy + 44, "End the name with .py or .lua")
    caption(cv, T, dx + 14, fy + 58, "to make a program.")
    button(cv, T, "normal", dx + dw - 14 - 168, dy + dh - 38, 80, 26, "Cancel")
    button(cv, T, "primary", dx + dw - 14 - 80, dy + dh - 38, 80, 26, "Make")
    hh_hints(cv, T, (("enter", "Make"), ("Esc", "Cancel")))
    return cv


def notes_desk(T):
    cv = desk(T, (("notes", "Notes"), ("folder", "Files")))
    x, y, w, h = 112, 52, 900, 536
    top = win(cv, T, x, y, w, h, "Notes · Story ideas", "notes")
    side = 280
    caption(cv, T, x + 24, top + 26, "8 notes", desk=True)
    note_rows(cv, T, x + 8, top + 38, side - 16, 30, None, current_i=2, desk=True, group=False)
    cv.rect(x + side, top, 1, h - 44 - 8, T["line"])
    tx = x + side + 12
    cv.rrect(tx + 216, top + 8, 36, 36, 8, mix(T["text"], T["win"]["body"] or T["surface"], 0.9))
    for bx, slot, st in ((0, "undo", "rest"), (40, "redo", "disabled"), (96, "select", "current"),
                         (136, "copy", "rest"), (176, "cut", "rest"), (216, "paste", "rest")):
        ibtn(cv, T, slot, tx + bx, top + 8, 36, st, desk=True)
    cv.rect(tx + 86, top + 14, 1, 24, T["line"])
    cv.text(x + w - 24, top + 32, "Markdown", F(T, "meta", DK), T["dim"], align="right")
    cv.rect(x + side + 1, top + 52, w - side - 9, 1, T["line"])
    note_doc(cv, T, x + side + 1, top + 53, w - side - 9, h - 44 - 53 - 44 - 8, desk=True)
    tooltip(cv, T, tx + 234 - F(T, "meta", DK).width("Paste  Ctrl+V") // 2 - 10, top + 48,
            "Paste  Ctrl+V")
    desk_foot(cv, T, x, y + h - 44, w, "Story ideas · saved",
              hint=((("Ctrl", "C"), "Copy"), (("Ctrl", "X"), "Cut"), (("Ctrl", "V"), "Paste")))
    return cv


# ==========================================================================
# Storybook's parts: the chosen object, the snap guide, the style rows
# (round15.py draws the slides and the screens)
# ==========================================================================
def _poly(d, s, pts):
    d.polygon([(px * s, py * s) for px, py in pts], fill=255)


def chosen(cv, T, x, y, w, h, desk=False, touch=False):
    """The chosen object (GAPS 35): a 1.5 px `value` line inside a 1 px dark
    keyline, so it reads on any picture; eight handles (keys and pointer) or
    one corner knob (touch), white with a dark border."""
    dark = "#15161d"
    cv.ring(x - 1.5, y - 1.5, w + 3, h + 3, 0, 1, dark)
    cv.ring(x - 0.5, y - 0.5, w + 1, h + 1, 0, 1.5, T["value"])
    if touch:
        k = 10
        cv.shape(x + w - k / 2, y + h - k / 2, k, k,
                 lambda dr, s: dr.ellipse((0, 0, k * s - 1, k * s - 1), fill=255), dark)
        cv.shape(x + w - k / 2 + 1.5, y + h - k / 2 + 1.5, k - 3, k - 3,
                 lambda dr, s: dr.ellipse((0, 0, (k - 3) * s - 1, (k - 3) * s - 1), fill=255), "#ffffff")
        return
    s = 8 if desk else 6
    for hx in (x, x + w / 2, x + w):
        for hy in (y, y + h / 2, y + h):
            if hx == x + w / 2 and hy == y + h / 2:
                continue
            cv.rect(hx - s / 2, hy - s / 2, s, s, dark)
            cv.rect(hx - s / 2 + 1, hy - s / 2 + 1, s - 2, s - 2, "#ffffff")


def guide(cv, T, x0, y, x1):
    """A snap guide while an object moves: a dashed `accent` line."""
    x = x0
    while x < x1:
        cv.rect(x, y, min(5, x1 - x), 1, T["accent"])
        x += 9


def toggle(cv, T, xr, y, h, on, desk=False):
    """`ui.toggle`'s switch, right-aligned at xr."""
    tw, th = (36, 20) if desk else (28, 16)
    x, ty = xr - tw, y + (h - th) // 2
    cv.rrect(x, ty, tw, th, th / 2, T["value"] if on else mix(T["line"], T["dim"], 0.3))
    k = th - 4
    kx = x + tw - k - 2 if on else x + 2
    cv.shape(kx, ty + 2, k, k, lambda dr, s: dr.ellipse((0, 0, k * s - 1, k * s - 1), fill=255),
             T["surface"] if T["mode"] == "light" else "#ffffff")


def align_seg(cv, T, xr, y, h, cur, desk=False):
    """A seg of icons (left, centre, right; GAPS 38)."""
    s = 18 if desk else 14
    p = 30 if desk else 26
    x = xr - 3 * p + (p - s) // 2
    for i, slot in enumerate(("align_l", "align_c", "align_r")):
        ink = T["dim"]
        if i == cur:
            ink = M.current_mark(cv, T, x - (p - s) // 2 + 1, y + 3, p - 2, h - 6, 5) or T["text"]
        sicon(cv, T, slot, x, y + (h - s) // 2, s, ink)
        x += p


def trans_seg(cv, T, xr, y, h, cur, desk=False):
    """The transition: None, Fade, Swipe, each with its icon."""
    f = F(T, "meta", DK) if desk else F(T, "status", HH)
    s = 16 if desk else 12
    x = xr
    for i, (slot, label) in reversed(list(enumerate((("trans_none", "None"), ("trans_fade", "Fade"),
                                                     ("trans_swipe", "Swipe"))))):
        w = s + 4 + f.width(label) + 14
        x -= w
        ink = T["dim"]
        if i == cur:
            ink = M.current_mark(cv, T, x, y + 3, w, h - 6, 6) or T["text"]
        sicon(cv, T, slot, x + 7, y + (h - s) // 2, s, ink)
        cv.text(x + 11 + s, M.mid_base(f, y, h), label, f, ink)
        x -= 2


def value_chip(cv, T, xr, y, h, colour, name, desk=False):
    """A colour row's value: its swatch and name, then › (it opens the
    colours)."""
    f = F(T, "meta", DK) if desk else F(T, "status", HH)
    w = cv.text(xr, M.mid_base(f, y, h), "›", f, T["dim"], align="right")
    w2 = cv.text(xr - w - 8, M.mid_base(f, y, h), name, f, T["text"], align="right")
    d = 16 if desk else 14
    sx = xr - w - w2 - 14 - d
    cv.rrect(sx, y + (h - d) // 2, d, d, 4, P64[colour])
    cv.ring(sx, y + (h - d) // 2, d, d, 4, 1, edge(T))


def sheet_rows(cv, T, x, y, w, rh, items, focus_i, desk=False):
    """The style's rows: (label, kind, value), kinds `cycle`, `colour`,
    `seg2`, `align`, `appears`, `toggle`."""
    fl = F(T, "row", DK) if desk else Face(T["fam"], 13, T["w_med"], 2)
    pad = 12 if desk else 16
    for i, (label, kind, val) in enumerate(items):
        ry = y + i * rh
        if i:
            cv.rect(x + pad, ry, w - 2 * pad, 1, T["line"])
        ink = M.focus_mark(cv, T, (x, ry, w, rh, 0), "part") if i == focus_i else None
        cv.text(x + pad, M.mid_base(fl, ry, rh), label, fl, ink or T["text"])
        xr = x + w - pad
        if kind == "colour":
            value_chip(cv, T, xr, ry, rh, val[0], val[1], desk)
        elif kind == "seg2":
            seg(cv, T, xr, ry, rh, val[0], val[1], desk)
        elif kind == "align":
            align_seg(cv, T, xr, ry, rh, val, desk)
        elif kind == "trans":
            trans_seg(cv, T, xr, ry, rh, val, desk)
        elif kind == "toggle":
            toggle(cv, T, xr, ry, rh, val, desk)
        elif kind == "sw":
            swatches(cv, T, xr, ry, rh, val[0], val[1], desk)
        else:
            R11._value(cv, T, kind, val, xr, ry, rh, F(T, "meta", DK) if desk else F(T, "status", HH),
                       desk)
    return y + rh * len(items)


# ==========================================================================
# the pictures
# ==========================================================================
HANDHELD = (
    (paint_handheld, "main", "paint", "Paint on the T-Deck: the drawing at 1x centred on the last "
     "mark, the tools down the left, the colour in use and its page of sixteen, the brush's size "
     "dot; the tool's name leads the hints; a toast for a new drawing's first save"),
    (paint_handheld, "colours", "paint-colours", "Paint: the 64 colours in a popover hung from "
     "the colour in use, inside the drawing's frame; the cursor's ring on 14, the caption naming it"),
    (paint_handheld, "brush", "paint-brush", "Paint with the Shape tool: Size, Shape and Fill "
     "(Outline, Filled) in the brush popover; focus on Size"),
    (paint_handheld, "more", "paint-more", "Paint: the more menu hung from its button (no wash) "
     "-- new, open, use as wallpaper, put in a game, show -- each with its key"),
    (paint_touch, "main", "paint-touch", "Paint on a touch board (the Guition S3): tools two by "
     "four at 28 px, the colour in use the one colour target, no hint strip"),
    (paint_touch, "colours", "paint-touch-colours", "Paint on a touch board: the colours "
     "filling the drawing's frame, 28 a page at 30x30 on a 32 px pitch, a 36 px page seg"),
    (paint_game, None, "paint-game", "Paint's Put in a game: the projects with their pictures; "
     "focus on Sky Run"),
    (my_art, "grid", "my-art", "My Art: the drawings as cards, New drawing first, the wallpaper's "
     "badge; focus on Rocket"),
    (my_art, "menu", "my-art-menu", "My Art: Rocket's menu over a washed page, the card current "
     "behind it; Move to Trash set apart in the danger ink"),
    (my_art, "empty", "my-art-empty", "My Art with no drawings: what is missing and the one verb, "
     "focused with the ring a mark on its own colour takes"),
    (getcarts, "grid", "getcarts", "Get Carts: the shelf as cover cards three a row, badges for "
     "on console, update ready and too big (Doom, no cover: its genre on a tile); focus on Hop "
     "Quest"),
    (getcarts, "cart", "getcarts-cart", "Get Carts: a cart's page with an update ready -- "
     "the facts as rows, Remove (danger), Play, Update"),
    (getcarts, "getting", "getcarts-getting", "Get Carts: a download in progress, Cancel"),
    (getcarts, "offline", "getcarts-offline", "Get Carts with Wi-Fi off: the bar says so, Wi-Fi "
     "is the primary, Try again beside it"),
    (notes, "list", "notes", "Notes: New note first, then the vault, a note's name bare and "
     "another file's with its extension; focus on Story ideas"),
    (notes, "edit", "notes-edit", "Notes: a note open, Markdown in the mono cells, a selection; "
     "Select, Copy, Cut and Paste labelled at the foot"),
    (notes, "new", "notes-new", "Notes: New note's name in a dialog, the list washed between "
     "the bars"),
    (notes, "empty", "notes-empty", "Notes with an empty vault"),
)
DESKS = (
    (my_art_desk, None, "my-art", "My Art as a window: five cards a row, the foot's verbs for "
     "the focused drawing, Open primary, Move to Trash in the danger ink"),
    (getcarts_desk, "cart", "getcarts", "Get Carts as a window: the shelf beside the focused "
     "cart's page, its verbs in the foot"),
    (getcarts_desk, "getting", "getcarts-getting", "Get Carts as a window: the download in the "
     "cart's page, Cancel"),
    (notes_desk, None, "notes", "Notes as a window (no handheld bar inside it): the vault beside "
     "the open note, its tools with a tooltip, the clipboard's keys in the foot"),
)


def scenes():
    for T, tag in ((LIGHT, "moy-light"), (DARK, "moy-dark")):
        n = T["name"]
        for fn, arg, slug, what in HANDHELD:
            cv = fn(T, arg) if arg else fn(T)
            cv.save(f"{tag}_handheld_{slug}", f"{what}; {n}", x3=True)
        for fn, arg, slug, what in DESKS:
            cv = fn(T, arg) if arg else fn(T)
            cv.save(f"{tag}_desktop_{slug}", f"{what}; {n}")
