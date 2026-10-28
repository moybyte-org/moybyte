#!/usr/bin/env python3
"""Round 8 of the 2026-10 look, layered over round 7: the Moy mark's
candidates, the refined 16x16 pixel icon set, minimize, maximize and close
on every window, and the editors in their current layout, restyled.

A layer of the target, not an entry point: `../draw.py` draws every picture.
"""
import os
import sys

from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "..", "..", "..", "native", "moy_ui", "icons",
                                "pixel"))
import round7 as R7  # noqa: E402
import pixel_icons as PX  # noqa: E402

M = R7.M
Face, F, HH, DK, mix = M.Face, M.F, M.HH, M.DK, M.mix
LIGHT, DARK, PICO = R7.LIGHT, R7.DARK, R7.PICO
LIGHT["name"], DARK["name"] = "Moy Day", "Moy Night"
INDEX = M.INDEX
del INDEX[:]
PAL = R7.PAL
OLD_PIXEL = R7.pixel_icon

# ==========================================================================
# 1. the Moy mark: three candidates
# ==========================================================================
BODY = ("M2.5 6a3 3 0 0 1 3-3H13a2 2 0 0 1 2 2V8.5h4.5a2 2 0 0 1 2 2v5"
        "a2 2 0 0 1-2 2H4.5a2 2 0 0 1-2-2z")
FACE = ('<rect x="6" y="9.4" width="2.4" height="3.2" rx="1.1" fill="#000"/>'
        '<rect x="10.4" y="9.4" width="2.4" height="3.2" rx="1.1" fill="#000"/>')
SMILE = '<path d="M6.8 14.2q2.6 1.9 5.2 0" stroke="#000" stroke-width="1.6" fill="none" stroke-linecap="round"/>'
FEET = ('<rect x="4.5" y="18.6" width="5.5" height="2.9" rx="1.2"/>'
        '<rect x="12.5" y="18.6" width="5.5" height="2.9" rx="1.2"/>')


def _cut(inner):
    return inner.replace('fill="#000"', 'fill="#000"').replace("#000", "#000")


MARKS = {
    "A, the bitten block": (
        '<mask id="m"><rect width="24" height="24" fill="#fff"/>'
        + FACE.replace("#000", "#000") + SMILE + '</mask>'
        f'<path d="{BODY}" mask="url(#m)"/>' + FEET),
    "B, outline": (
        f'<path d="{BODY}" fill="none" stroke="#000" stroke-width="2" stroke-linejoin="round"/>'
        + FACE + SMILE.replace('stroke-width="1.6"', 'stroke-width="1.5"')
        + '<path d="M6.5 20.5v1M15.5 20.5v1" stroke="#000" stroke-width="2.4" stroke-linecap="round"/>'),
    "C, face only": (
        '<mask id="m"><rect width="24" height="24" fill="#fff"/>' + FACE + '</mask>'
        '<path d="M2.5 7a4 4 0 0 1 4-4H12.5a2 2 0 0 1 2 2V9.5h5a2 2 0 0 1 2 2V17a4 4 0 0 1-4 4h-11'
        'a4 4 0 0 1-4-4z" mask="url(#m)"/>'),
}
# the mask needs white cut-outs inverted: features are black inside a white mask
for k in list(MARKS):
    MARKS[k] = MARKS[k].replace('<rect width="24" height="24" fill="#fff"/>',
                                '<rect width="24" height="24" fill="#fff"/>')
CHOSEN = "A, the bitten block"


def mark_svg(name):
    return ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="#000">'
            + MARKS[name] + "</svg>")


_svg7 = R7.svg


def svg(slot, set_name, layer):
    if slot == "moy":
        return mark_svg(CHOSEN)
    return _svg7(slot, set_name, layer)


R7.svg = svg
R7.GLYPHS["minimize"] = [("line", "M6 17.5h12")]
R7.GLYPHS["maximize"] = [("shape", R7.R(5, 5, 14, 14, 2.2))]
for g, slots in R7.SLOTS:
    if g == "controls":
        slots[1:1] = ["minimize", "maximize"]
R7.ALL[:] = [s for _, g in R7.SLOTS for s in g]


def mark_mask(name, px, bpp):
    import io
    im = Image.open(io.BytesIO(bytes(R7.resvg_py.svg_to_bytes(svg_string=mark_svg(name),
                                                              width=px, height=px))))
    return M.levels(im.convert("RGBA").split()[3], 1 << bpp)


def moy_sheet():
    cands = list(MARKS)
    bars = [("Moy Day bar", LIGHT, "#ffffff", LIGHT["bar"]["sys"]),
            ("Moy Night bar", DARK, DARK["bar"]["fill"], DARK["bar"]["sys"])]
    cw, rh = 230, 92
    W, H = 160 + cw * len(cands), 70 + rh * 3 + 30
    cv = M.Cv(W, H, "#e9eaee")
    fb, f, fs = Face("inter", 15, 700, 4), Face("inter", 13, 500, 4), Face("inter", 11, 500, 2)
    cv.text(12, 26, "The Moy mark: three candidates at 16 and 24 px on both bars, and the pixel Moy", fb, "#15161d")
    for i, c in enumerate(cands):
        cv.text(160 + i * cw + 10, 56, c + ("   (chosen)" if c == CHOSEN else ""), f,
                "#4c3fd6" if c == CHOSEN else "#33364a")
    y = 70
    for label, T, bg, (tile, body, _eye) in bars:
        cv.text(12, y + 50, label, f, "#33364a")
        for i, c in enumerate(cands):
            x0 = 160 + i * cw
            cv.rect(x0 + 4, y + 4, cw - 8, rh - 8, bg)
            for j, (px, tw) in enumerate(((16, 22), (24, 32))):
                tx, ty = x0 + 20 + j * 60, y + (rh - tw) // 2
                cv.rrect(tx, ty, tw, tw, 7 if tw < 30 else 9, tile)
                cv._blend(tx + (tw - px) // 2, ty + (tw - px) // 2, mark_mask(c, px, 2 if px <= 16 else 4), body)
            m = mark_mask(c, 64, 4)
            cv._blend(x0 + 140, y + 14, m, body)
        y += rh
    cv.text(12, y + 50, "pixel 16×16", f, "#33364a")
    for i, (bg, sc) in enumerate((("#000000", 1), (M.P8["navy"], 2), ("#ffffff", 4))):
        x0 = 160 + i * cw
        cv.rect(x0 + 4, y + 4, cw - 8, rh - 8, bg)
        im = PX.image(PX.icon("moy"), PAL, sc)
        cv.img.paste(im.convert("RGB"), (x0 + (cw - im.width) // 2, y + (rh - im.height) // 2), im.split()[3])
    cv.text(12, H - 10, "The pixel Moy is the boot logo's character redrawn: 1 = outline (0 is clear), "
            "13 body, 2 feet and shade, 6 sheen, 7 eyes.", fs, "#4a4d5c")
    cv.save("moy_mark_r8", "The Moy mark: three smooth candidates on Moy Day and Moy Night bars, "
            "and the pixel Moy at 1x, 2x and 4x")


# ==========================================================================
# 2. the pixel icon set, old against new
# ==========================================================================
def pixel_sheet():
    slots = R7.ALL
    bars = (("Pico bar", "#000000"), ("Bench bar", "#ffffff"))
    cw, rh, lw = 120, 74, 120
    H = 66 + rh * len(slots) + 10
    W = lw + cw * 4 + 10
    cv = M.Cv(W, H, "#e9eaee")
    fb, f, fs = Face("inter", 15, 700, 4), Face("inter", 13, 500, 4), Face("inter", 11, 500, 2)
    cv.text(10, 24, f"The pixel icon set: today's art against the refined set, {len(slots)} slots", fb,
            "#15161d")
    heads = ("today, Pico bar", "new, Pico bar", "today, Bench bar", "new, Bench bar")
    for i, h in enumerate(heads):
        cv.text(lw + i * cw + cw // 2, 54, h, fs, "#4a4d5c", align="center")
    y = 62
    for slot in slots:
        cv.text(12, y + 42, slot, f, "#33364a")
        old = OLD_PIXEL(slot)
        new = PX.image(PX.icon(slot), PAL)
        for i in range(4):
            bg = bars[i // 2][1]
            im = new if i % 2 else old
            x0 = lw + i * cw
            cv.rect(x0 + 4, y + 3, cw - 8, rh - 6, bg)
            if im is None:
                cv.text(x0 + cw // 2, y + 42, "none", fs, "#e07b00", align="center")
                continue
            cv.img.paste(im.convert("RGB"), (x0 + 12, y + (rh - 16) // 2), im.split()[3])
            big = im.resize((64, 64), Image.NEAREST)
            cv.img.paste(big.convert("RGB"), (x0 + 40, y + 5), big.split()[3])
        y += rh
    cv.save("icons_pixel", "The pixel icon set: today's art and the refined set for every slot, "
            "at 1x and 4x, on Pico's and Bench's bar colours")
    # the shipped sheet is native/moy_ui/icons/pixel/'s, built by tools/make_icons.py
    _text, cols, rows = PX.moygfx(slots)
    return cols, rows


# the shell draws pixel icons from the new set
def pixel_icon_new(slot):
    return PX.image(PX.icon(slot), PAL) if slot in PX.ICONS else None


# ==========================================================================
# 4. three window controls on every built-in theme
# ==========================================================================
_window = M.window


def title_ink(T, focus):
    Wn = T["win"]
    if Wn["header"] == "fill" and focus and not Wn["ink"]:
        return T["value_ink"]
    return (Wn["ink"] or T["text"]) if focus else (Wn["ink_back"] or T["dim"])


def window3(cv, T, x, y, w, h, title, ic, focus):
    top = _window(cv, T, x, y, w, h, title, ic, focus)
    Wn = T["win"]
    hh = Wn["h"]
    frame = Wn["frame"] if focus else Wn["frame_back"]
    fw = frame[0] if frame else 0
    c = Wn["controls"]
    if c == "close":
        s = min(20, hh - 8)
        cx = x + w - s - 14 - fw
        ink = title_ink(T, focus)
        R7.draw_icon(cv, T, "maximize", cx - 34, y + (hh - s) // 2, s, ink)
        R7.draw_icon(cv, T, "minimize", cx - 68, y + (hh - s) // 2, s, ink)
    elif c == "xp":
        bs = hh - 12
        bx, by = x + w - bs - 8, y + 6
        g = ([(0, "#6aa2f7"), (0.45, "#2f6fe4"), (1, "#1f4fc0")] if focus
             else [(0, "#c4d4f4"), (1, "#9ab0e4")])
        for i, slot in enumerate(("maximize", "minimize")):
            b = bx - (i + 1) * (bs + 3)
            cv.rrect(b, by, bs, bs, 4, M.grad(g, by, bs))
            cv.ring(b, by, bs, bs, 4, 1, "#ffffff", 0.9)
            R7.draw_icon(cv, M.A, slot, b + 4, by + 4, bs - 8, "#ffffff")
    elif c == "adw":
        bs = 24
        bx, by = x + w - bs - 12, y + (hh - bs) // 2
        for i, slot in enumerate(("maximize", "minimize")):
            b = bx - (i + 1) * (bs + 8)
            cv.shape(b, by, bs, bs, lambda d, s: d.ellipse((0, 0, bs * s - 1, bs * s - 1), fill=255),
                     "#e6e6e8" if focus else "#efeff1")
            R7.draw_icon(cv, T, slot, b + 6, by + 6, 12, T["text"] if focus else T["dim"])
    return top


M.window = window3


# ==========================================================================
# 3. the editors in their CURRENT layout, restyled
# ==========================================================================
LADDER = (("cards", "settings", "Config"), ("blocks", "blocks", "Blocks"), ("code", "code", "Code"),
          ("paint", "sprite", "Sprites"), ("map", "map", "Map"), ("scene", "scene", "Scene"),
          ("music", "music", "Music"))


def ladder_bar(cv, T, cur):
    """The handheld bar: the tab ladder (the current tab labelled), undo,
    redo, Play; then the clock, the X, the menu and the status icons."""
    hb = T["hhbar"]
    h = 26
    ink, dim = hb["ink"] or T["text"], hb["dim"] or T["dim"]
    if hb["style"] == "flat":
        cv.rect(0, 0, 320, h, hb["fill"])
    else:
        cv.rect(0, 0, 320, h, T["bg"])
        cv.rect(0, h - 1, 320, 1, T["line"])
    s, mid, x = 16, h // 2, 3
    lf = F(T, "tab", HH)
    if T["render"]["text"] != "crisp":
        lf = Face(T["fam"], 12, T["w_semi"], 2)
    for tab, slot, label in LADDER:
        if tab == cur:
            tw = s + 4 + lf.width(label)
            if M.cur_style(T) == "plate":
                c = M.current_mark(cv, T, x - 3, mid - 10, tw + 6, 20, 5)
            elif T["tabs"]["style"] == "pill":
                cv.rrect(x - 3, mid - 10, tw + 6, 20, 5, T["tabs"]["pill"])
                c = T["tabs"]["pill_ink"]
            else:
                c = ink
                cv.rrect(x, h - 3, tw, 2, 1, T["value"])
            R7.draw_icon(cv, T, slot, x, mid - s // 2, s, c)
            cv.text(x + s + 4, M.mid_base(lf, 0, h), label, lf, c)
            x += tw + 6
        else:
            R7.draw_icon(cv, T, slot, x, mid - s // 2, s, dim)
            x += s + 2
    R7.draw_icon(cv, T, "undo", x + 2, mid - s // 2, s, dim)
    R7.draw_icon(cv, T, "redo", x + 20, mid - s // 2, s, mix(dim, hb["fill"] or T["bg"], 0.55))
    # Play: a bare icon in the undo/redo row, filled in `value` (the primary
    # colour); the glyph carries its own optical centring
    R7.draw_icon(cv, T, "play", x + 38, mid - s // 2, s, T["value"])
    fs = F(T, "status", HH)
    rx = 320 - 4
    R7.draw_icon(cv, T, "battery", rx - 16, mid - 8, 16, ink)
    R7.draw_icon(cv, T, "close", rx - 36, mid - 8, 16, dim)
    cv.text(rx - 40, M.mid_base(fs, 0, h), "14:32", fs, ink, align="right")
    return h


def status_and_keys(cv, T, y, w, desk):
    fm = F(T, "meta", DK if desk else HH)
    cv.rect(0, y, w, 1, T["line"])
    sh = 30 if desk else 16
    b = M.mid_base(fm, y, sh)
    cv.text(8 if not desk else 16, b, f"Ln {R7.CUR_LINE}, Col {R7.CUR_COL}  ·  177 lines  ·  No issues", fm, T["dim"])
    keys = "=()[]{}<>:;,.\"_%"
    kh = 40 if desk else 18
    ky = y + sh
    kw = (w - 4) // len(keys)
    f = R7.code_face(T, desk)
    for i, ch in enumerate(keys):
        M.button(cv, T, "editor", "normal", 3 + i * kw, ky, kw - 3, kh, ch, {"key": f, "op": f})
    return sh + kh + 3


def hh_code(T):
    cv = M.new_cv(T, False, T["bg"])
    top = ladder_bar(cv, T, "code")
    R7.draw_code(cv, T, 0, top, 320, 240 - top - 38, False, first=34)
    status_and_keys(cv, T, 240 - 37, 320, False)
    return cv


ACTIONS = ("dup", "new", "flip", "rotate", "back", "forward", "del")


def sprite_body(cv, T, x, y, w, h, desk):
    """Today's sprite tab: canvas, palette column, sprite controls and
    preview beside, the tool row and the action row under the canvas, Close."""
    m = dict(cell=36, sw=22, tool=40, gap=20, pad=16) if desk else dict(cell=17, sw=11, tool=26, gap=6, pad=6)
    fr = F(T, "row", DK) if desk else F(T, "meta", HH)
    fc = F(T, "col", DK) if desk else F(T, "meta", HH)
    cv.rect(x, y, w, h, T["surface"])
    pad, cell, sw, gap = m["pad"], m["cell"], m["sw"], m["gap"]
    cv.text(x + pad, y + pad + fc.cap, "Sprite 0", fc, T["dim"])
    cx, cy = x + pad, y + pad + fc.cap + (10 if desk else 5)
    pix = R7.tile_pix(0)
    cv.rect(cx - 1, cy - 1, 8 * cell + 1, 8 * cell + 1, T["line"])
    for r in range(8):
        for c in range(8):
            v = pix[r][c]
            if v == 0:
                R7.checker(cv, cx + c * cell, cy + r * cell, cell - 1, cell - 1, max(3, cell // 3))
            else:
                cv.rect(cx + c * cell, cy + r * cell, cell - 1, cell - 1, PAL[v])
    fx, fy = cx + 3 * cell, cy + 2 * cell
    cv.ring(fx - 1, fy - 1, cell + 1, cell + 1, 0, 1, "#000000")
    M.ring_mark(cv, T, fx - 1, fy - 1, cell + 1, cell + 1, 0)
    # the palette column: 2 x 8
    px0 = cx + 8 * cell + gap
    for i in range(16):
        sx, sy = px0 + (i % 2) * sw, cy + (i // 2) * sw
        if i == 0:
            R7.checker(cv, sx, sy, sw, sw, max(3, sw // 4))
            cv.shape(sx, sy, sw, sw, lambda d, s: d.line((sw * s, 0, 0, sw * s), fill=255,
                                                         width=max(1, int(s))), "#ffffff")
        else:
            cv.rect(sx, sy, sw, sw, PAL[i])
    sel = 11
    sx, sy = px0 + (sel % 2) * sw, cy + (sel // 2) * sw
    cv.ring(sx, sy, sw, sw, 0, 2 if desk else 1, "#ffffff")
    cv.rect(sx, sy + sw - 3, sw, 3, T["value"])
    # sprite controls, preview, Get / Put / File
    rx = px0 + 2 * sw + gap
    rw = x + w - pad - rx
    bh = 40 if desk else 18
    fonts = {"key": fr, "op": fr}
    s = 20 if desk else 14
    yy = cy
    half = (rw - (8 if desk else 4)) // 2
    M.button(cv, T, "editor", "normal", rx, yy, half, bh, "‹", fonts)
    M.button(cv, T, "editor", "normal", rx + rw - half, yy, half, bh, "›", fonts)
    yy += bh + (12 if desk else 5)
    cv.text(rx + rw // 2, yy + fr.cap, "Size 1×1", fr, T["text"], align="center")
    yy += fr.cap + (14 if desk else 6)
    ph = 96 if desk else 34
    R7.checker(cv, rx, yy, rw, ph, 6 if desk else 3)
    big = R7.sprite_img(pix, 8 if desk else 3)
    R7.paste_rgba(cv, big, rx + (rw - big.width) // 2, yy + (ph - big.height) // 2)
    yy += ph + (14 if desk else 6)
    for slot, label in (("getcarts", "Get"), ("save", "Put"), ("files", "File")):
        st = M.bstyle(T, "editor", "normal")
        cv.rrect(rx, yy, rw, bh, min(st["r"], 8) if cv.aa else 0, M.paint(st["fill"], yy, bh))
        if st["border"]:
            cv.ring(rx, yy, rw, bh, min(st["r"], 8), *st["border"])
        if st.get("bevel"):
            M.bevel_of(cv, T, rx, yy, rw, bh)
        R7.draw_icon(cv, T, slot, rx + (14 if desk else 6), yy + (bh - s) // 2, s, st["ink"])
        cv.text(rx + (14 if desk else 6) + s + 8, M.mid_base(fr, yy, bh), label, fr, st["ink"])
        yy += bh + (8 if desk else 3)
    # tool row and action row under the canvas
    t = m["tool"]
    ty = cy + 8 * cell + (14 if desk else 5)
    tw_ = (8 * cell) // len(R7.TOOLS[:6])
    for i, name in enumerate(R7.TOOLS[:6]):
        bx = cx + i * tw_
        on = i == 0
        if on:
            if M.cur_style(T) != "bar":
                M.current_mark(cv, T, bx + 1, ty, tw_ - 2, t - 4, 6 if cv.aa else 0)
            else:
                cv.rrect(bx + 1, ty, tw_ - 2, t - 4, 6 if cv.aa else 0,
                         T["sunk"] if T["mode"] == "light" else T["raised"])
                cv.rect(bx + 6, ty + t - 7, tw_ - 12, 2, T["value"])
        R7.draw_icon(cv, T, name, bx + (tw_ - s) // 2, ty + (t - 4 - s) // 2, s,
                     T["text"] if on else T["dim"])
    ay = ty + t
    aw = (8 * cell + 2 * sw + gap) // len(ACTIONS)
    for i, name in enumerate(ACTIONS):
        bx = cx + i * aw
        R7.draw_icon(cv, T, name, bx + (aw - s) // 2, ay + (t - 4 - s) // 2, s,
                     (T["danger"] or "#c4231b") if name == "del" else T["dim"])
    # Close: the tab's way out, at the bottom right
    st = M.bstyle(T, "editor", "normal")
    cy_ = ay
    cv.rrect(rx, cy_, rw, bh, min(st["r"], 8) if cv.aa else 0, M.paint(st["fill"], cy_, bh))
    if st["border"]:
        cv.ring(rx, cy_, rw, bh, min(st["r"], 8), *st["border"])
    cv.text(rx + rw // 2, M.mid_base(fr, cy_, bh), "Close", fr, st["ink"], align="center")


def hh_sprites(T):
    cv = M.new_cv(T, False, T["surface"])
    top = ladder_bar(cv, T, "paint")
    sprite_body(cv, T, 0, top, 320, 240 - top, False)
    return cv


def desk_tabs(cv, T, x, y, w, cur):
    """The desk Editor's chip row: the seven tabs labelled, Play on the right."""
    h = 52
    cv.rect(x, y, w, h, T["surface"])
    cv.rect(x, y + h - 1, w, 1, T["line"])
    lf = F(T, "chip", DK)
    s = 20
    cx = x + 14
    for tab, slot, label in LADDER:
        on = tab == cur
        tw = s + 6 + lf.width(label)
        c = T["text"] if on else T["dim"]
        if on:
            if M.cur_style(T) != "bar":
                c = M.current_mark(cv, T, cx - 8, y + 9, tw + 16, h - 18, 8)
            elif T["tabs"]["style"] == "pill":
                cv.rrect(cx - 8, y + 9, tw + 16, h - 18, 8, T["tabs"]["pill"])
                c = T["tabs"]["pill_ink"]
            else:
                cv.rrect(cx - 8, y + 9, tw + 16, h - 18, 8, T["sunk"] if T["mode"] == "light" else T["raised"])
                cv.rrect(cx, y + h - 6, tw, 3, 1.5, T["value"])
        R7.draw_icon(cv, T, slot, cx, y + (h - s) // 2, s, c)
        cv.text(cx + s + 6, M.mid_base(lf, y, h), label, lf, c)
        cx += tw + 26
    R7.draw_icon(cv, T, "undo", cx + 4, y + (h - s) // 2, s, T["dim"])
    R7.draw_icon(cv, T, "redo", cx + 34, y + (h - s) // 2, s, mix(T["dim"], T["surface"], 0.55))
    pf = Face(T["fam"], 15, T["w_semi"], 4) if T["render"]["text"] != "crisp" else lf
    pw = s + 6 + pf.width("Play")
    px_ = x + w - pw - 18
    R7.draw_icon(cv, T, "play", px_, y + (h - s) // 2, s, T["value"])
    cv.text(px_ + s + 6, M.mid_base(pf, y, h), "Play", pf, T["value"])
    return h


EDITOR = (112, 54, 900, 534)


def desk_editor(T, cur):
    cv = M.new_cv(T, True)
    M.backdrop(cv, T, "desk")
    M.desk_icons(cv, T)
    x, y, w, h = EDITOR
    top = M.window(cv, T, x, y, w, h, "Editor · Star Catcher", "code", True)
    fw = (T["win"]["frame"] or (0,))[0]
    th = desk_tabs(cv, T, x + fw, top, w - 2 * fw, cur)
    body_y = top + th
    cv.set_clip(x + fw, body_y, w - 2 * fw, y + h - fw - body_y)
    if cur == "code":
        kb = 30 + 40 + 3
        R7.draw_code(cv, T, x + fw, body_y, w - 2 * fw, y + h - fw - body_y - kb, True, first=36)
        sub = M.Cv(w - 2 * fw, kb, T["surface"])
        status_and_keys(sub, T, 0, w - 2 * fw, True)
        cv.img.paste(sub.img, (x + fw, y + h - fw - kb))
    else:
        sprite_body(cv, T, x + fw, body_y, w - 2 * fw, y + h - fw - body_y, True)
    cv.clip = None
    chips_bar(cv, T, (("code", "Editor"),))
    return cv


def desk_playtest(T):
    cv = M.new_cv(T, True)
    M.backdrop(cv, T, "desk")
    M.desk_icons(cv, T)
    x, y, w, h = EDITOR
    top = M.window(cv, T, x, y, w, h, "Editor · Star Catcher", "code", False)
    desk_tabs(cv, T, x + 1, top, w - 2, "code")
    R7.draw_code(cv, T, x + 1, top + 52, w - 2, y + h - top - 53, True, first=36)
    cv.rect(x + 1, top, w - 2, y + h - top - 1, T["bg"], 0.25)
    gw, gh = 640, 480
    wx, wy = 300, 48
    t2 = M.window(cv, T, wx, wy, gw, gh + T["win"]["h"], "Play · Star Catcher", "play", True)
    cv.img.paste(R7.GAME.resize((gw, gh), Image.NEAREST), (cv.L(wx), cv.L(t2)))
    chips_bar(cv, T, (("play", "Play"), ("code", "Editor")))
    return cv


def chips_bar(cv, T, chips):
    """The desk bar with this scene's window chips (the first is current)."""
    b = T["bar"]
    cv.rect(0, 0, 1024, b["h"], b["fill"] or T["surface"], b["alpha"])
    if b["line"]:
        cv.rect(0, b["h"] - b["line"][0], 1024, b["line"][0], b["line"][1])
    sys_fill, body, eye = b["sys"] or (T["raised"], T["value"], T["raised"])
    cv.rrect(6, 3, 36, 34, 9, sys_fill)
    M.moy(cv, 8, 4, 32, body, sys_fill)
    fc = F(T, "chip", DK)
    x = 52
    for i, (slot, name) in enumerate(chips):
        w = 18 + 8 + fc.width(name) + 24
        c = T["text"] if i == 0 else T["dim"]
        if i == 0:
            cv.rrect(x, 6, w, 28, 8, b["chip_cur"])
            if M.current_mark(cv, T, x, 6, w, 28, 8) is None:
                cv.rrect(x + 10, 31, w - 20, 3, 1.5, T["value"])
        R7.draw_icon(cv, T, slot, x + 12, 11, 18, c)
        cv.text(x + 38, 25, name, fc, c)
        x += w + 6
    fs = F(T, "status", DK)
    tw = cv.text(1010, 26, "14:32", fs, T["text"], align="right")
    R7.draw_icon(cv, T, "battery", 1010 - tw - 30, 9, 22, T["text"])
    R7.draw_icon(cv, T, "wifi", 1010 - tw - 58, 8, 21, T["text"])


def desk_calc(T):
    return M.desktop(T)
