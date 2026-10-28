#!/usr/bin/env python3
"""Round 15 of the 2026-10 look, over round 13: Storybook and Paint at the
desk's document size, for the owner's decisions of 2026-10-21
(`APPS_RESTYLE.md`).

- Storybook is an app with its own story document. A slide is 1024x600 and
  its text is drawn in the shell's faces at any size, so one renderer draws
  a slide at any zoom: 1/2 in a window, 5/8 full screen, 1:1 presenting on
  1024x600 glass, 1/4 viewed on a handheld and 5/16 presented there. The
  desk edits; a handheld lists, views and presents.
- Paint's desk drawing is 1024x600: Fit (1/2) in a window, 1x panned full
  screen. A handheld opens such a drawing to view: Fit is 1/4, and its More
  keeps Use as wallpaper and Put in a story.

A maximised app is full screen (`MAXIMISE_VARIANTS.md`'s V2): no shell bar,
no title strip, the app's row at the top edge with minimise, restore and
close at its right.

A layer of the target, not an entry point: `../draw.py` draws every picture.
"""
import math
import os
import sys

from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import round13 as R13  # noqa: E402

M, R11 = R13.M, R13.R11
Face, F, HH, DK, mix = M.Face, M.F, M.HH, M.DK, M.mix
LIGHT, DARK = R13.LIGHT, R13.DARK
P64, sicon, ART = R13.P64, R13.sicon, R13.ART

R13.NEW_ICONS["restore"] = (
    '<rect x="4" y="9" width="11" height="11" rx="2" fill="none" stroke="#000" stroke-width="2"/>'
    '<path d="M9 9V6a2 2 0 0 1 2-2h7a2 2 0 0 1 2 2v7a2 2 0 0 1-2 2h-3" fill="none" stroke="#000" '
    'stroke-width="2" stroke-linecap="round"/>')

SW, SH = 1024, 600              # a slide, and a desk drawing


# ==========================================================================
# the desk's drawings: the kid's art carried to 1024x600 in 4 px brush pixels
# ==========================================================================
def _wide(kind):
    small = ART[kind].resize((160, 120), Image.NEAREST)
    ox, oy = 48, 15
    im = Image.new("RGB", (256, 150))
    sp, px = small.load(), im.load()
    for y in range(150):
        for x in range(256):
            px[x, y] = sp[min(max(x - ox, 0), 159), min(max(y - oy, 0), 119)]
    from PIL import ImageDraw
    d = ImageDraw.Draw(im)
    c = R13._c
    if kind == "garden":
        for fx, col in ((14, "pink"), (30, "orange"), (226, "red"), (242, "pink")):
            d.line((fx, oy + 88, fx, oy + 104), fill=c("dark_green"), width=2)
            d.ellipse((fx - 6, oy + 80, fx + 6, oy + 92), fill=c(col))
            d.ellipse((fx - 2, oy + 84, fx + 2, oy + 88), fill=c("yellow"))
        for cx, cy in ((206, 30), (220, 26), (232, 32)):
            d.ellipse((cx - 10, cy - 6, cx + 10, cy + 6), fill=c("white"))
    elif kind == "rocket":
        for x0, x1 in ((0, ox), (ox + 160, 256)):
            d.rectangle((x0, 0, x1 - 1, oy + 69), fill=c("dark_blue"))
        d.rectangle((0, 0, 255, oy - 1), fill=c("dark_blue"))
        for i in range(36):
            x, y = (i * 71 + 7) % 256, (i * 29 + 3) % 110
            if not (ox <= x < ox + 160 and oy <= y < oy + 120):
                d.point((x, y), fill=c("white" if i % 3 else "yellow"))
    elif kind == "dragon":
        for y in range(oy + 96, 150):
            for x in range(256):
                if px[x, y] == c("dark_green"):
                    px[x, y] = c("dark_blue")
    return im.resize((SW, SH), Image.NEAREST)


WIDE = {k: _wide(k) for k in ("garden", "house", "dragon", "rocket", "fish")}


# ==========================================================================
# a slide: objects in 1024x600 slide pixels, drawn at any zoom k
# ==========================================================================
FACES = {"sans": ("atkinson", 500), "sans_bold": ("atkinson", 700), "mono": ("jbmono", 500)}
FACE_NAMES = {"sans": "Sans", "sans_bold": "Sans bold", "mono": "Mono"}
SIZES = (("S", 32), ("M", 48), ("L", 64), ("XL", 96))
WIDTHS = (("Thin", 4), ("Thick", 8))


def face(font, px):
    fam, wt = FACES[font]
    return Face(fam, max(5, int(round(px))), wt, 4)


def wrap(text, f, w):
    lines, cur = [], ""
    for word in text.split():
        t = (cur + " " + word).strip()
        if cur and f.width(t) > w:
            lines.append(cur)
            cur = word
        else:
            cur = t
    return lines + [cur]


def text_lines(o, k):
    """Where a text box's lines fall at zoom k: (face, [(x, base, line)], height)."""
    f = face(o.get("font", "sans_bold"), o["size"] * k)
    x, y, w = o["x"] * k, o["y"] * k, o["w"] * k
    lh = o["size"] * k * 1.2
    out = []
    for i, ln in enumerate(wrap(o["text"], f, w)):
        tw = f.width(ln)
        lx = {"left": x, "centre": x + (w - tw) / 2, "right": x + w - tw}[o.get("align", "centre")]
        out.append((lx, y + o["size"] * k * 0.95 + i * lh, ln))
    return f, out, lh * len(out) + o["size"] * k * 0.2


def _shape(kind, w, h, i=0.0):
    def fn(d, s):
        x0, y0, x1, y1 = i * s, i * s, (w - i) * s - 1, (h - i) * s - 1
        if kind == "rect":
            d.rectangle((x0, y0, x1, y1), fill=255)
        elif kind == "round":
            d.rounded_rectangle((x0, y0, x1, y1), max(1, (min(w, h) * 0.22 - i) * s), fill=255)
        elif kind == "ellipse":
            d.ellipse((x0, y0, x1, y1), fill=255)
        elif kind == "bubble":
            bh = h * 0.76
            d.rounded_rectangle((x0, y0, x1, (bh - i) * s), max(1, (min(w, bh) * 0.24 - i) * s), fill=255)
            R13._poly(d, s, ((w * 0.16 + i, bh - i - 2), (w * 0.08 + i * 0.6, h - i * 1.8),
                             (w * 0.34 - i, bh - i - 2)))
        elif kind == "star":
            r0 = min(w, h) / 2 - i
            R13._poly(d, s, [(w / 2 + (r0 if j % 2 == 0 else r0 * 0.42) * math.cos(-math.pi / 2 + j * math.pi / 5),
                              h / 2 + 0.05 * h + (r0 if j % 2 == 0 else r0 * 0.42)
                              * math.sin(-math.pi / 2 + j * math.pi / 5)) for j in range(10)])
    return fn


def _picture(o, k):
    src = WIDE.get(o["image"]) if o["image"] in WIDE else ART[o["image"]]
    w, h = round(src.width * o["scale"] * k), round(src.height * o["scale"] * k)
    im = src.resize((w, h), Image.NEAREST)
    mask = None
    if o.get("clear"):
        key = im.getpixel((0, 0))
        mask = Image.eval(im.convert("L"), lambda v: 255)
        mp, pp = mask.load(), im.load()
        for yy in range(h):
            for xx in range(w):
                if pp[xx, yy] == key:
                    mp[xx, yy] = 0
    return im, mask


def render(slide, k, caret=False):
    """Slide `slide` at zoom k: what edit, the thumbnails and Present all draw."""
    bg, objs = slide
    cv = M.Cv(round(SW * k), round(SH * k), "#000000" if isinstance(bg, str) else P64[bg])
    if isinstance(bg, str):
        cv.img.paste(WIDE[bg].resize((cv.W, cv.H), Image.NEAREST), (0, 0))
    for o in objs:
        if o["type"] == "picture":
            im, mask = _picture(o, k)
            cv.img.paste(im, (round(o["x"] * k), round(o["y"] * k)), mask)
            continue
        if o["type"] == "shape":
            x, y, w, h = (v * k for v in (o["x"], o["y"], o["w"], o["h"]))
            lw = o.get("width", 4) * k
            if o.get("line") is not None:
                cv.shape(x, y, w, h, _shape(o["shape"], w, h), P64[o["line"]])
            if o.get("fill") is not None:
                cv.shape(x, y, w, h, _shape(o["shape"], w, h, lw if o.get("line") is not None else 0),
                         P64[o["fill"]])
            if o.get("text"):
                body = o["h"] * (0.76 if o["shape"] == "bubble" else 1)
                t = dict(o, y=o["y"] + (body - o["size"] * 1.2) / 2 - o["size"] * 0.05)
                f, lines, _h = text_lines(t, k)
                for lx, base, ln in lines:
                    cv.text(lx, base, ln, f, P64[o["colour"]])
            continue
        f, lines, th = text_lines(o, k)
        if o.get("strip") is not None:
            cv.rect(0 if o["w"] >= SW else o["x"] * k, o["y"] * k, o["w"] * k, th, P64[o["strip"]])
        for lx, base, ln in lines:
            cv.text(lx, base, ln, f, P64[o["colour"]])
        if caret:
            lx, base, ln = lines[-1]
            cv.rect(lx + f.width(ln) + 2, base - o["size"] * k * 0.8, max(2, 3 * k), o["size"] * k * 0.95,
                    "#4c3fd6")
    return cv.img.copy()


def text(t, x, y, w, size, colour, font="sans_bold", align="centre", strip=None, step=0):
    return dict(type="text", text=t, x=x, y=y, w=w, size=size, colour=colour, font=font,
                align=align, strip=strip, step=step)


def shape(kind, x, y, w, h, fill, line=None, width=4, step=0, **words):
    return dict(type="shape", shape=kind, x=x, y=y, w=w, h=h, fill=fill, line=line, width=width,
                step=step, **words)


def picture(image, x, y, scale, clear=False, step=0):
    return dict(type="picture", image=image, x=x, y=y, scale=scale, clear=clear, step=step)


DRAGON = picture("dragon", 64, 64, 0.5)
BUBBLE = shape("bubble", 608, 64, 352, 180, 7, 0, 8, step=1, text="Hello!", size=64, colour=0,
               font="sans_bold")
ONCE = text("Once upon a time...", 64, 416, 896, 72, 1)
STORY = (
    (1, (shape("star", 120, 92, 88, 88, 10), shape("star", 836, 412, 64, 64, 10),
         shape("star", 228, 452, 40, 40, 7),
         text("The Brave Dragon", 64, 196, 896, 96, 7),
         text("by Mia", 64, 350, 896, 48, 6, font="sans"))),
    (15, (DRAGON, BUBBLE, ONCE)),
    (1, (picture("rocket", 0, 40, 0.5), picture("fish", 512, 40, 0.5),
         text("Up and away!", 64, 416, 896, 72, 7))),
    ("house", (text("Home at last.", 0, 500, SW, 64, 7, strip=0),)),
    (2, (shape("round", 232, 180, 560, 240, 8, 7, 8, text="The End", size=96, colour=7,
               font="sans_bold"),)),
)
TRANS = ("none", "fade", "swipe", "fade", "none")
CLEARED = (15, (picture("dragon", 64, 64, 0.5, clear=True), BUBBLE, ONCE))

_FULL = {}


def thumb(n, w, h):
    """A slide's frame at a card's size: its 1:1 render, averaged down."""
    if n not in _FULL:
        _FULL[n] = render(STORY[n], 1)
    return _FULL[n].resize((w, h), Image.BOX).point(M.LUT565)


# ==========================================================================
# shared: a full-screen app's row, a chip, the slide on its pasteboard
# ==========================================================================
ROW = 40


def fs_row(cv, T, slot, title, back=False):
    """A maximised app, full screen: its row at the top edge -- ‹ while a
    page of the app is open, the icon and the title -- then, past a
    hairline, minimise, restore and close at the right. Returns the span
    its tools take."""
    W = cv.W
    cv.rect(0, 0, W, ROW, T["surface"])
    cv.rect(0, ROW - 1, W, 1, T["line"])
    x = 16
    if back:
        sicon(cv, T, "back", 12, 10, 20, T["text"])
        x = 42
    sicon(cv, T, slot, x, 10, 20, T["text"])
    ft = F(T, "wtitle", DK)
    tw = cv.text(x + 30, M.mid_base(ft, 0, ROW), title, ft, T["text"])
    for i, g in enumerate(("close", "restore", "minimize")):
        sicon(cv, T, g, W - 34 - 34 * i, 10, 20, T["text"])
    cv.rect(W - 34 - 68 - 14, 10, 1, ROW - 20, T["line"])
    return x + 30 + tw + 16, W - 34 - 68 - 30


def chip(cv, T, x, label):
    """The handheld bar's zoom chip (a `raised` chip with the structure line)."""
    fz = Face(T["fam"], 12, T["w_semi"], 2)
    w = fz.width(label) + 14
    cv.rrect(x, 5, w, 18, 5, T["raised"])
    cv.ring(x, 5, w, 18, 5, 1, mix(T["line"], T["dim"], 0.25))
    cv.text(x + w / 2, M.mid_base(fz, 5, 18), label, fz, T["text"], align="center")
    return x + w


def on_board(cv, T, im, x, y, shadow=0.6):
    """A document on its pasteboard: the soft shadow, the image, its edge."""
    M.soft(cv, T, x, y, im.width, im.height, 2, shadow)
    cv.paste(im.point(M.LUT565), x, y)
    cv.ring(x, y, im.width, im.height, 0, 1, R13.edge(T))


def scroll_thumbs(cv, T, x, y, w, h, fx, fy, fw, fh):
    """A panned view's thumbs: where the view sits in the document."""
    cv.rrect(x + 6 + fx * (w - 18), y + h - 6, fw * (w - 18), 3, 1.5, T["text"], 0.35)
    cv.rrect(x + w - 6, y + 6 + fy * (h - 18), 3, fh * (h - 18), 1.5, T["text"], 0.35)


def labelled_w(T, label, slot=True):
    return F(T, "chip", DK).width(label) + (48 if slot else 28)


# ==========================================================================
# Paint: the 1024x600 drawing on the desk; a handheld views it
# ==========================================================================
PAINT_VERBS = (("Show", "maximize"), ("Wallpaper", "wallpaper"), ("Put in a story", "storybook"))


def paint_row(cv, T, x, y, bh, xr, zoom, seg=True):
    """Paint's tools: new and open, undo and redo; the zoom (in the foot
    when full screen), the drawing's verbs and More (⋯) right-aligned at
    xr."""
    for bx, slot, state in ((0, "new", "rest"), (40, "folder_open", "rest"), (96, "undo", "rest"),
                            (136, "redo", "disabled")):
        R13.ibtn(cv, T, slot, x + bx, y, bh, state, desk=True)
    cv.rect(x + 86, y + 6, 1, bh - 12, T["line"])
    xr -= bh
    R13.ibtn(cv, T, "more", xr, y, bh, desk=True)
    xr -= 8
    for label, slot in reversed(PAINT_VERBS):
        bw = labelled_w(T, label)
        xr -= bw
        R13.button(cv, T, "normal", xr, y + (bh - 32) // 2, bw, 32, label, slot, desk=True)
        xr -= 8
    if seg:
        R13.seg(cv, T, xr - 8, y, bh, ZOOMS, zoom, desk=True)
    return xr


ZOOMS = ("Fit", "1×", "2×", "4×")


def swatch_edge(cv, T, c, x, y, w, h, r=0):
    """A swatch that nearly vanishes into its ground takes a 1 px edge in
    `dim` (3:1 or more to the ground)."""
    if M.ratio(c, T["surface"]) < 1.5:
        cv.ring(x, y, w, h, r, 1, T["dim"])


def colour_panel(cv, T, px, y0):
    R13.caption(cv, T, px, y0 + 14, "Colour", desk=True)
    cv.rect(px, y0 + 26, 44, 28, P64[R13.COLOUR])
    R13.two_tone(cv, T, px, y0 + 26, 44, 28)
    cv.text(px + 56, y0 + 46, "12 · Blue", F(T, "row", DK), T["text"])
    for i in range(64):
        sx, sy = px + (i % 8) * 26, y0 + 64 + (i // 8) * 26
        cv.rect(sx, sy, 24, 24, P64[i])
        swatch_edge(cv, T, P64[i], sx, sy, 24, 24)
        if i == R13.COLOUR:
            R13.two_tone(cv, T, sx, sy, 24, 24)
    R13.caption(cv, T, px, y0 + 290, "Pencil", desk=True)
    R13.row_items(cv, T, px - 10, y0 + 298, 238, 30, (("Size", "seg2", (("2", "4", "8", "16"), 1)),), None,
                  desk=True, group=False)


def paint_desk(T, mode="window"):
    garden = WIDE["garden"]
    if mode == "window":
        cv = R13.desk(T, (("paint", "Paint"), ("folder", "Files")))
        x, y, w, h = 112, 52, 900, 536
        top = R13.win(cv, T, x, y, w, h, "Paint · Garden", "paint")
        paint_row(cv, T, x + 16, top + 8, 36, x + w - 16, 0)
        cv.rect(x + 16, top + 52, w - 32, 1, T["line"])
        R13.tool_cells(cv, T, x + 16, top + 66, 40, 44, 1, 0, desk=True)
        ax, ay, aw, ah = x + 68, top + 62, 568, 384
        cv.rrect(ax, ay, aw, ah, 10, T["bg"])
        dx, dy = ax + (aw - 512) // 2, ay + (ah - 300) // 2
        on_board(cv, T, garden.resize((512, 300), Image.NEAREST), dx, dy)
        R13.ring_cursor(cv, T, dx + 300, dy + 120, 2, 2)
        colour_panel(cv, T, x + 652, top + 62)
        R13.desk_foot(cv, T, x, y + h - 44, w, "Pencil · size 4 · Fit, ½ · saved",
                      hint=(("Z", "Zoom"), ("C", "Colours"), (("Ctrl", "Z"), "Undo")))
        return cv
    cv = M.new_cv(T, True)
    cv.rect(0, 0, 1024, 600, T["surface"])
    x0, xr = fs_row(cv, T, "paint", "Paint · Garden")
    paint_row(cv, T, x0, (ROW - 32) // 2, 32, xr, 1, seg=False)
    R13.tool_cells(cv, T, 12, ROW + 12, 40, 44, 1, 0, desk=True)
    vx, vy, vw, vh = 64, ROW + 8, 712, 500
    sx, sy = 152, 54
    cv.paste(garden.crop((sx, sy, sx + vw, sy + vh)).point(M.LUT565), vx, vy, r=8)
    cv.ring(vx, vy, vw, vh, 8, 1, R13.edge(T))
    scroll_thumbs(cv, T, vx, vy, vw, vh, sx / SW, sy / SH, vw / SW, vh / SH)
    R13.ring_cursor(cv, T, vx + 470, vy + 150, 4, 4)
    colour_panel(cv, T, 790, ROW + 8)
    hint = (("Space", "Move"), ("F11", "Restore"))
    R13.desk_foot(cv, T, 0, 556, 1024, "Pencil · size 4 · 1× · saved", hint=hint)
    R13.seg(cv, T, 1024 - 26 - R11.hints_width(T, hint, True) - 20, 563, 30, ZOOMS, 1, desk=True)
    return cv


def paint_view(T, state="fit"):
    """A handheld opens a 1024x600 drawing to view: no tools, no colours."""
    cv = R11.hh_page(T, "Garden")
    x = R13.bar_tools(cv, T, "Garden", ())
    x = chip(cv, T, x - 2, "¼" if state != "zoom" else "1×") + 8
    sicon(cv, T, "more", x, 6, 16, T["text"])
    garden = WIDE["garden"]
    lead = "View only"
    if state == "zoom":
        vx, vy, vw, vh = 0, 28, 320, 190
        sx, sy = 520, 300
        cv.paste(garden.crop((sx, sy, sx + vw, sy + vh)).point(M.LUT565), vx, vy)
        mx, my = 246, 34
        mini = garden.resize((64, 38), Image.BOX).point(M.LUT565)
        cv.rect(mx - 2, my - 2, 68, 42, T["surface"])
        cv.paste(mini, mx, my)
        cv.ring(mx + sx / 16, my + sy / 16, vw / 16, vh / 16, 0, 1.5, T["accent"])
        cv.ring(mx - 2, my - 2, 68, 42, 0, 1, R13.edge(T))
        R13.hh_hints(cv, T, (("↕↔", "Pan"), ("Z", "Zoom"), ("M", "More")), lead=lead)
        return cv
    on_board(cv, T, garden.resize((256, 150), Image.NEAREST), 32, 34)
    R13.caption(cv, T, 160, 204, "1024 × 600, made on a desk · shown at ¼", align="center")
    if state == "more":
        R13.menu(cv, T, 120, 30, 194,
                 (("folder_open", "Open…", "O"), ("-",), ("wallpaper", "Use as wallpaper", "W"),
                  ("storybook", "Put in a story…", "T"), ("maximize", "Show", "V")), 1,
                 clip=(0, 28, 320, 190))
        R13.hh_hints(cv, T, (("enter", "Use"), ("Esc", "Close")))
        return cv
    R13.toast(cv, T, "From a desk: you can look, not draw", 160, 154)
    R13.hh_hints(cv, T, (("Z", "Zoom"), ("M", "More"), ("Esc", "Back")), lead=lead)
    return cv


# ==========================================================================
# Storybook on the desk: the slide editor, the sorter, Present
# ==========================================================================
def tool_pill(cv, T, x, y, h, slot, label):
    """A labelled tool on a tool row: its icon and its word, bare. Returns
    its width."""
    f = F(T, "chip", DK)
    w = 18 + 6 + f.width(label) + 16
    ink = T["text"]
    sicon(cv, T, slot, x + 8, y + (h - 18) // 2, 18, ink)
    cv.text(x + 32, M.mid_base(f, y, h), label, f, ink)
    return w


def sb_tools(cv, T, x, y, bh, xr, n=2, add_open=False, stepper=True):
    """The slide's tools: Add ▾; Front, Back, Copy and Paste labelled;
    undo and redo; the ‹ 2/5 › stepper (a window's; full screen the slides
    column steps) and Present right-aligned at xr."""
    by = y + (bh - 34) // 2 if bh > 34 else y
    hb = min(bh, 34)
    if add_open:
        fc = F(T, "chip", DK)
        cv.rrect(x, by, 92, hb, 8, R13.R10.open_plate(T, T["raised"]))
        lx = x + (92 - fc.width("Add   ") - 24) // 2
        sicon(cv, T, "new", lx, by + (hb - 18) // 2, 18, T["text"])
        cv.text(lx + 24, M.mid_base(fc, by, hb), "Add", fc, T["text"])
    else:
        R13.button(cv, T, "normal", x, by, 92, hb, "Add   ", "new", desk=True)
    sicon(cv, T, "down", x + 66, by + (hb - 14) // 2, 14, T["dim"])
    bx = x + 104
    for group in ((("to_front", "Front"), ("to_back", "Back")), (("dup", "Copy"), ("paste", "Paste"))):
        cv.rect(bx - 6, y + 6, 1, bh - 12, T["line"])
        for slot, label in group:
            bx += tool_pill(cv, T, bx, by, hb, slot, label)
        bx += 10
    cv.rect(bx - 6, y + 6, 1, bh - 12, T["line"])
    for slot in ("undo", "redo"):
        R13.ibtn(cv, T, slot, bx, y, bh, "disabled" if slot == "redo" else "rest", desk=True)
        bx += bh + 4
    xr -= 124
    R13.button(cv, T, "primary", xr, by, 124, hb, "Present", "play", desk=True)
    if not stepper:
        return
    sx0 = xr - 12 - 104
    f = F(T, "row", DK)
    cv.rrect(sx0, by, 104, hb, 8, T["raised"])
    sicon(cv, T, "back", sx0 + 6, by + (hb - 16) // 2, 16, T["dim"])
    sicon(cv, T, "forward", sx0 + 82, by + (hb - 16) // 2, 16, T["dim"])
    cv.text(sx0 + 52, M.mid_base(f, by, hb), f"{n}/5", f, T["text"], align="center")


def trans_label(cv, T, x, base, i, f):
    """A slide's number and its transition's icon (None, Fade, Swipe)."""
    w = cv.text(x, base, str(i + 1), f, T["text"])
    sicon(cv, T, "trans_" + TRANS[i], x + w + 5, base - 14, 16, T["dim"])


def slides_column(cv, T, x, y, bottom, cur=1):
    """The slides down the side: frames 80x47, each slide's number and its
    transition under it, the one in edit current."""
    fn = F(T, "meta", DK)
    for i in range(len(STORY)):
        cy = y + i * 76
        if cy + 70 > bottom:
            break
        if i == cur:
            M.current_mark(cv, T, x, cy - 6, 96, 76, 8)
        R13.paste_rect(cv, thumb(i, 80, 47), x + 8, cy, 80, 47, 4)
        cv.ring(x + 8, cy, 80, 47, 4, 1, R13.edge(T))
        w = cv.text(x + 9, cy + 64, str(i + 1), fn, T["text"] if i == cur else T["dim"])
        sicon(cv, T, "trans_" + TRANS[i], x + 9 + w + 6, cy + 50, 16, T["dim"])


def guide(cv, T, x0, y, x1):
    """A snap guide: indigo dashes on a 3 px white keyline, in every theme,
    so it reads on any slide."""
    cv.rect(x0, y - 1, x1 - x0, 3, "#ffffff", 0.9)
    x = x0
    while x < x1:
        cv.rect(x, y, min(6, x1 - x), 1, GUIDE)
        x += 10


GUIDE = "#4c3fd6"


def size_row(cv, T, x, y, w, rh, size, focus=False):
    """Text size: S, M, L and XL first, and the size itself in slide
    pixels in a field that takes any number."""
    R13.sheet_rows(cv, T, x, y, w, rh,
                   (("Size", "seg2", (tuple(s for s, _p in SIZES),
                                      next((i for i, (_s, p) in enumerate(SIZES) if p == size), None))),),
                   0 if focus else None, desk=True)
    fl, fm = F(T, "row", DK), F(T, "meta", DK)
    fx_, fw = x + 12 + fl.width("Size") + 6, fm.width(str(size)) + 14
    cv.rrect(fx_, y + 3, fw, rh - 6, 5, T["surface"])
    cv.ring(fx_, y + 3, fw, rh - 6, 5, 1, mix(T["line"], T["dim"], 0.5))
    cv.text(fx_ + fw / 2, M.mid_base(fm, y, rh), str(size), fm, T["text"], align="center")
    return y + rh


def panel_head(cv, T, px, y, slot, title):
    sicon(cv, T, slot, px, y + 2, 20, T["text"])
    cv.text(px + 28, y + 18, title, F(T, "wtitle", DK), T["text"])


def rows(cv, T, x, y, w, rh, items, focus_i, desk=True):
    """The style's rows (round 13's), each colour's swatch edged where it
    nearly vanishes; `bg` is the slide's Background: the last four colours
    used, then › for all 16."""
    plain = [(lb, "link", "") if kind == "bg" else (lb, kind, val) for lb, kind, val in items]
    end = R13.sheet_rows(cv, T, x, y, w, rh, plain, focus_i, desk=True)
    fm = F(T, "meta", DK)
    for i, (_lb, kind, val) in enumerate(items):
        ry, xr = y + i * rh, x + w - 12
        if kind == "colour":
            d = 16
            sx = xr - fm.width("›") - fm.width(val[1]) - 14 - d
            swatch_edge(cv, T, P64[val[0]], sx, ry + (rh - d) // 2, d, d, 4)
        elif kind == "bg":
            cols, cur = val
            xs = xr - fm.width("›") - 10
            R13.swatches(cv, T, xs, ry, rh, [P64[c] for c in cols], cur, True)
            for j, c in enumerate(cols):
                cx = xs - 16 - (len(cols) - 1 - j) * 22
                swatch_edge(cv, T, P64[c], cx, ry + (rh - 16) // 2, 16, 16, 8)
    return end


def style_panel(cv, T, px, y, state):
    """The style panel, 236 wide: the chosen object's rows, or the slide's."""
    pw = 236
    sr = rows
    if state == "edit":
        panel_head(cv, T, px, y, "bubble", "Speech bubble")
        yy = sr(cv, T, px - 12, y + 30, pw, 28,
                (("Shape", "cycle", "Bubble"), ("Fill", "colour", (7, "White")),
                 ("Line", "colour", (0, "Black")), ("Width", "seg2", (("Thin", "Thick"), 1))), 1, desk=True)
        R13.caption(cv, T, px, yy + 18, "Text", desk=True)
        yy = sr(cv, T, px - 12, yy + 24, pw, 28, (("Font", "cycle", "Sans bold"),), None, desk=True)
        yy = size_row(cv, T, px - 12, yy, pw, 28, 64)
        yy = sr(cv, T, px - 12, yy, pw, 28, (("Colour", "colour", (0, "Black")), ("Align", "align", 1)),
                None, desk=True)
        sr(cv, T, px - 12, yy + 8, pw, 28, (("Appears", "seg2", (("With slide", "On tap"), 1)),), None,
           desk=True)
        return "Slide 2 of 5 · moving: lined up with the dragon"
    if state == "write":
        panel_head(cv, T, px, y, "text", "Text")
        yy = sr(cv, T, px - 12, y + 30, pw, 28, (("Font", "cycle", "Sans bold"),), None, desk=True)
        yy = size_row(cv, T, px - 12, yy, pw, 28, 72)
        yy = sr(cv, T, px - 12, yy, pw, 28,
                (("Colour", "colour", (1, "Dark blue")), ("Align", "align", 1),
                 ("Strip behind", "cycle", "None")), None, desk=True)
        sr(cv, T, px - 12, yy + 8, pw, 28, (("Appears", "seg2", (("With slide", "On tap"), 0)),), None,
           desk=True)
        return "Slide 2 of 5 · writing · 19 of 200 letters"
    if state == "picture":
        panel_head(cv, T, px, y, "art", "Picture · Dragon")
        sr(cv, T, px - 12, y + 30, pw, 28,
           (("Size", "seg2", (("½", "1", "2", "3", "4"), 0)), ("Clear background", "toggle", True),
            ("Appears", "seg2", (("With slide", "On tap"), 0))), 1, desk=True)
        return "Slide 2 of 5 · Dragon, half its size, its corner colour clear"
    panel_head(cv, T, px, y, "layout", "Slide 2")
    yy = sr(cv, T, px - 12, y + 30, pw, 28,
            (("Background", "bg", ((15, 7, 10, 1), 0)), ("Picture", "link", "None")), 0)
    R13.caption(cv, T, px, yy + 18, "Transition", desk=True)
    sr(cv, T, px - 12, yy + 24, pw, 28, (("", "trans", 1),), None)
    return "Slide 2 of 5 · nothing chosen"


def slide_on_board(cv, T, ax, ay, aw, ah, k, state):
    """The slide at zoom k, centred on its pasteboard, and what is chosen."""
    cv.rrect(ax, ay, aw, ah, 8, T["bg"])
    w, h = round(SW * k), round(SH * k)
    sx, sy = ax + (aw - w) // 2, ay + (ah - h) // 2
    slide = CLEARED if state == "picture" else STORY[1]
    on_board(cv, T, render(slide, k, caret=state == "write"), sx, sy, 0.4)
    if state == "edit":
        guide(cv, T, sx + 4, sy + 64 * k, sx + w - 4)
        o = BUBBLE
        R13.chosen(cv, T, sx + o["x"] * k, sy + o["y"] * k, o["w"] * k, o["h"] * k, desk=True)
    elif state == "picture":
        o = DRAGON
        R13.chosen(cv, T, sx + o["x"] * k, sy + o["y"] * k, 512 * k, 300 * k, desk=True)
    elif state == "write":
        o = ONCE
        _f, _lines, th = text_lines(o, 1)
        R13.chosen(cv, T, sx + o["x"] * k, sy + o["y"] * k, o["w"] * k, th * k, desk=True)
    return sx, sy


def sb_desk(T, state="edit"):
    if state == "present":
        cv = M.Cv(SW, SH, "#000000")
        cv.paste(render(STORY[1], 1).point(M.LUT565), 0, 0)
        f = Face("inter", 15, 600, 4)
        w = f.width("2 / 5") + 24
        cv.rrect(SW - 16 - w, SH - 16 - 28, w, 28, 14, "#000000", 0.5)
        cv.text(SW - 16 - w / 2, M.mid_base(f, SH - 44, 28), "2 / 5", f, "#ffffff", align="center")
        return cv
    if state == "max":
        cv = M.new_cv(T, True)
        cv.rect(0, 0, SW, SH, T["surface"])
        x0, xr = fs_row(cv, T, "storybook", "The Brave Dragon", back=True)
        sb_tools(cv, T, x0, (ROW - 32) // 2, 32, xr, stepper=False)
        slides_column(cv, T, 8, ROW + 14, 556)
        cv.rect(112, ROW, 1, 556 - ROW, T["line"])
        slide_on_board(cv, T, 120, ROW + 8, 656, 500, 5 / 8, "write")
        status = style_panel(cv, T, 792, ROW + 16, "write")
        R13.desk_foot(cv, T, 0, 556, SW, status + " · shown at 5/8",
                      hint=(("Esc", "Done"), ("F11", "Restore")))
        return cv
    cv = R13.desk(T, (("storybook", "Storybook"), ("art", "My Art")))
    x, y, w, h = 112, 52, 900, 536
    if state == "stories":
        return stories_desk(cv, T, x, y, w, h)
    if state == "pictures":
        return pictures_desk(cv, T, x, y, w, h)
    top = R13.win(cv, T, x, y, w, h, "The Brave Dragon", "storybook", back=True)
    ty = top + 6
    if state == "deck":
        bx = x + 16
        for label, slot, kind in (("New slide", "layout", "normal"), ("Duplicate", "dup", "normal"),
                                  ("Delete", "trash", "danger")):
            bw = labelled_w(T, label)
            R13.button(cv, T, kind, bx, ty + 2, bw, 34, label, slot, desk=True)
            bx += bw + 8
        cv.rect(bx + 2, ty + 8, 1, 24, T["line"])
        for slot in ("undo", "redo"):
            R13.ibtn(cv, T, slot, bx + 12, ty + 1, 36, "disabled" if slot == "redo" else "rest", desk=True)
            bx += 40
        R13.button(cv, T, "primary", x + w - 16 - 124, ty + 2, 124, 34, "Present", "play", desk=True)
        cv.rect(x + 16, top + 50, w - 32, 1, T["line"])
        fn = F(T, "meta", DK)
        cw, ch, gx, gy = 256, 150, 24, 38
        x0, y0 = x + (w - 3 * cw - 2 * gx) // 2, top + 64
        for i in range(len(STORY) + 1):
            cx, cy = x0 + i % 3 * (cw + gx), y0 + i // 3 * (ch + gy)
            if i == len(STORY):
                R13.new_card(cv, T, cx, cy, cw, ch, True)
                cv.text(cx + cw // 2, cy + ch + 24, "New slide", fn, T["value"], align="center")
                continue
            R13.card_pic(cv, T, thumb(i, cw, ch), cx, cy, cw, ch, 8, focus=i == 2)
            trans_label(cv, T, cx + 1, cy + ch + 24, i, fn)
        R13.desk_foot(cv, T, x, y + h - 44, w, "Slide 3 · Up and away! · the slides at ¼",
                      hint=(("enter", "Edit"), (("[", "]"), "Move"), (("Ctrl", "D"), "Duplicate"),
                            ("Del", "Delete")))
        return cv
    add = state == "add"
    sb_tools(cv, T, x + 16, ty + 4, 36, x + w - 16, add_open=add)
    cv.rect(x + 16, top + 50, w - 32, 1, T["line"])
    slides_column(cv, T, x + 12, top + 64, y + h - 44)
    cv.rect(x + 116, top + 51, 1, h - 44 - 51 - 44, T["line"])
    slide_on_board(cv, T, x + 124, top + 58, 528, 384, 0.5, "none" if add else state)
    status = style_panel(cv, T, x + 664, top + 66, "none" if add else state)
    R13.desk_foot(cv, T, x, y + h - 44, w, status + " · shown at ½",
                  hint=((("Ctrl", "D"), "Duplicate"), ("Del", "Delete")))
    if add:
        R13.menu(cv, T, x + 16, ty + 44, 236,
                 (("text", "Text", "T"), ("shapes", "Shape", "S"), ("art", "Picture…", "I"),
                  ("background", "Background…", "B")), 0, desk=True)
    return cv


def tabs(cv, T, x, y, items, cur):
    """`ui.tabs`: a choice row, the current tab under the current mark."""
    f = F(T, "chip", DK)
    for i, t in enumerate(items):
        tw = f.width(t)
        ink = T["dim"]
        if i == cur:
            ink = M.current_mark(cv, T, x, y, tw + 20, 30, 6) or T["text"]
        cv.text(x + 10, M.mid_base(f, y, 30), t, f, ink)
        x += tw + 26


def fitted(im, w, h, ground):
    """A drawing fitted whole into a w x h card, centred on `ground`."""
    k = min(w / im.width, h / im.height)
    out = Image.new("RGB", (w, h), M.q(ground))
    fit = im.resize((round(im.width * k), round(im.height * k)), Image.BOX)
    out.paste(fit, ((w - fit.width) // 2, (h - fit.height) // 2))
    return out.point(M.LUT565)


PICTURES = (("dragon", "Dragon", "1024 × 600"), ("garden", "Garden", "1024 × 600"),
            ("house", "My house", "1024 × 600"), ("rocket", "Rocket", "320 × 240"),
            ("cat", "Ginger", "320 × 240"), ("fish", "Fish tank", "320 × 240"),
            ("night", "Night sky", "320 × 240"))


def pictures_desk(cv, T, x, y, w, h):
    """Add a picture: My Art and Sprites as tabs, the drawings as cards,
    each fitted whole and named with its size."""
    top = R13.win(cv, T, x, y, w, h, "Add a picture", "storybook", back=True)
    tabs(cv, T, x + 16, top + 10, ("My Art", "Sprites"), 0)
    cv.rect(x + 16, top + 50, w - 32, 1, T["line"])
    ft, fs = F(T, "row", DK), F(T, "meta", DK)
    cw, chh, gx = 152, 114, 22
    x0 = x + (w - 5 * cw - 4 * gx) // 2
    for i, (key, name, size) in enumerate(PICTURES):
        src = WIDE[key] if size.startswith("1024") else ART[key]
        k = min(cw / src.width, chh / src.height)
        pw, ph = round(src.width * k), round(src.height * k)
        cx, cy = x0 + i % 5 * (cw + gx), top + 66 + i // 5 * 178
        px, py = cx + (cw - pw) // 2, cy + (chh - ph) // 2
        R13.card_pic(cv, T, src.resize((pw, ph), Image.BOX).point(M.LUT565), px, py, pw, ph, 6,
                     focus=i == 0)
        cv.text(cx + cw // 2, cy + chh + 24, name, ft, T["text"], align="center")
        cv.text(cx + cw // 2, cy + chh + 42, size, fs, T["dim"], align="center")
    R13.desk_foot(cv, T, x, y + h - 44, w, "Dragon · it goes on slide 2, chosen",
                  verbs=(("Add", "new", "primary"),))
    return cv


def stories_desk(cv, T, x, y, w, h):
    """The desk's Stories: New story first, then a card a story at ¼."""
    top = R13.win(cv, T, x, y, w, h, "Storybook", "storybook")
    R13.caption(cv, T, x + 24, top + 26, "3 stories", desk=True)
    ft, fs = F(T, "row", DK), F(T, "meta", DK)
    cw, ch, gx = 256, 150, 24
    x0 = x + (w - 3 * cw - 2 * gx) // 2
    for i in range(4):
        cx, cy = x0 + i % 3 * (cw + gx), top + 36 + i // 3 * 200
        if i == 0:
            R13.new_card(cv, T, cx, cy, cw, ch, True)
            cv.text(cx + cw // 2, cy + ch + 26, "New story", ft, T["value"], align="center")
            continue
        n, name, sub = STORIES[i - 1]
        R13.card_pic(cv, T, thumb(n, cw, ch), cx, cy, cw, ch, 8, focus=i == 1)
        cv.text(cx + cw // 2, cy + ch + 26, name, ft, T["text"], align="center")
        cv.text(cx + cw // 2, cy + ch + 46, sub, fs, T["dim"], align="center")
    R13.desk_foot(cv, T, x, y + h - 44, w, "The Brave Dragon · 5 slides",
                  verbs=(("Present", "play", "normal"), ("Open", "folder_open", "primary")))
    return cv


# ==========================================================================
# Storybook on a handheld: the stories, a slide at 1/4, Present at 5/16
# ==========================================================================
STORIES = ((0, "The Brave Dragon", "5 slides"), (2, "Space trip", "3 slides"),
           (3, "Our holiday", "8 slides"))


def sb_handheld(T, state="shelf"):
    if state == "present":
        cv = M.Cv(320, 240, "#000000")
        im = render(STORY[1], 5 / 16)
        cv.paste(im.point(M.LUT565), 0, (240 - im.height) // 2)
        f = Face("inter", 12, 600, 2)
        cv.text(312, 233, "2 / 5", f, "#8a8fa8", align="right")
        return cv
    if state == "view":
        cv = R11.hh_page(T, "The Brave Dragon")
        R13.bar_tools(cv, T, "The Brave Dragon", ("play",))
        on_board(cv, T, render(STORY[1], 0.25), 32, 30)
        f = Face(T["fam"], 12, T["w_semi"], 2)
        sicon(cv, T, "back", 116, 183, 16, T["dim"])
        sicon(cv, T, "forward", 188, 183, 16, T["dim"])
        cv.text(160, 196, "2 / 5", f, T["text"], align="center")
        R13.caption(cv, T, 160, 211, "Edit stories on a desk board or the web", align="center")
        R13.hh_hints(cv, T, (("←→", "Slides"), ("P", "Present"), ("Esc", "Back")), lead="View only")
        return cv
    cv = R11.hh_page(T, "Storybook")
    if state == "empty":
        R13.empty_state(cv, T, 160, 44, "storybook", "No stories yet",
                        ("Stories are made on a desk board", "or in the web build."), None, None)
        R13.hh_hints(cv, T, (("Esc", "Back"),))
        return cv
    R13.caption(cv, T, 16, 42, "3 stories")
    ft = Face(T["fam"], 12, T["w_semi"], 2)
    fs = Face(T["fam"], 11, T["w_body"], 2)
    for i, (n, name, sub) in enumerate(STORIES):
        cx, cy = 16 + i % 2 * 160, 58 + i // 2 * 116
        R13.card_pic(cv, T, thumb(n, 128, 75), cx, cy, 128, 75, 6, focus=i == 0)
        cv.text(cx + 64, cy + 91, name, ft, T["text"], align="center")
        cv.text(cx + 64, cy + 105, sub, fs, T["dim"], align="center")
    R13.fade(cv, T, 0, 204, 320, 14)
    cv.rect(0, 218, 320, 22, T["bg"])
    R13.hh_hints(cv, T, (("enter", "Open"), ("P", "Present"), ("Esc", "Back")))
    return cv


# ==========================================================================
# the pictures
# ==========================================================================
HANDHELD = (
    (paint_view, "fit", "paint-view", "Paint on the T-Deck opening a 1024x600 drawing: view only, "
     "Fit at 1/4 (256x150), no tools or colours; the hints lead with View only; Enter answered "
     "with a toast"),
    (paint_view, "zoom", "paint-view-zoom", "Paint viewing a 1024x600 drawing at 1x (Z twice): "
     "panned, the whole drawing at 1/16 in the corner with the view marked"),
    (paint_view, "more", "paint-view-more", "Paint viewing a 1024x600 drawing: More, hung from the "
     "bar's ⋯, keeps Open, Use as wallpaper, Put in a story and Show"),
    (sb_handheld, "shelf", "storybook", "Storybook on the T-Deck: the stories as cards at 1/8 "
     "(128x75), two a row, no New story (stories are made on the desk); focus on The Brave Dragon"),
    (sb_handheld, "empty", "storybook-empty", "Storybook on the T-Deck with no stories: where "
     "they are made, and no verb"),
    (sb_handheld, "view", "storybook-view", "Storybook on the T-Deck: a story open on slide 2 at "
     "1/4 (256x150), view only, as Paint's view says it; the arrows step, P presents"),
)
DESKS = (
    (paint_desk, "window", "paint", "Paint as a window: the 1024x600 drawing at Fit (1/2, "
     "512x300) on its pasteboard, the tools, More, the colours and the Pencil's sizes 2 to 16"),
    (paint_desk, "max", "paint-max", "Paint full screen: its row at the top edge, a hairline, "
     "minimise, restore and close; the drawing at 1x panned (712x500 of it), the colours beside it"),
    (sb_desk, "stories", "storybook-stories", "Storybook as a window: the stories at 1/4, New "
     "story first; Open and Present for the focused story in the foot"),
    (sb_desk, "edit", "storybook", "Storybook as a window: the tool row with Front, Back, Copy and "
     "Paste labelled, the slides at 80x47, the 1024x600 slide at 1/2 with the bubble moving on a "
     "two-toned snap guide, its style always open"),
    (sb_desk, "add", "storybook-add", "Storybook as a window: Add's popover hung from it -- text, "
     "shape, picture, background, each with its key; nothing chosen"),
    (sb_desk, "pictures", "storybook-pictures", "Storybook's Add a picture as a window page: My "
     "Art and Sprites as tabs, each drawing fitted whole and named with its size; focus on Dragon"),
    (sb_desk, "picture", "storybook-picture", "Storybook as a window: a picture chosen, its size, "
     "Clear background on (the dragon's sky drawn clear) and Appears"),
    (sb_desk, "none", "storybook-slide", "Storybook as a window with nothing chosen: the slide's "
     "background (the last four colours used, › for all 16) and its transition"),
    (sb_desk, "deck", "storybook-deck", "Storybook as a window: the slide sorter at 1/4 "
     "(256x150), New slide, Duplicate, Delete, undo and redo, Present"),
    (sb_desk, "max", "storybook-max", "Storybook full screen: ‹ and the tools in its row, the "
     "slide at 5/8 (640x375), a text box being written at 72 px (its size in a field), its style "
     "beside it"),
)


def scenes():
    for T, tag in ((LIGHT, "moy-light"), (DARK, "moy-dark")):
        n = T["name"]
        for fn, arg, slug, what in HANDHELD:
            fn(T, arg).save(f"{tag}_handheld_{slug}", f"{what}; {n}", x3=True)
        for fn, arg, slug, what in DESKS:
            fn(T, arg).save(f"{tag}_desktop_{slug}", f"{what}; {n}")
    sb_handheld(LIGHT, "present").save("storybook_present", "Storybook presenting on the T-Deck: "
                                       "the 1024x600 slide at 5/16 (320x188) on black, its number "
                                       "in the margin", x3=True)
    sb_desk(LIGHT, "present").save("storybook_present_desk", "Storybook presenting on the "
                                   "1024x600 board: the slide 1:1, the whole glass, its number "
                                   "in a half-black pill")
