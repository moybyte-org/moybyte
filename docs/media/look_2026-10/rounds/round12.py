#!/usr/bin/env python3
"""Round 12 of the 2026-10 look, layered over round 11: the Editor on the
desk shows several of its parts at once (`EDITOR_LAYOUT.md`), and every
handheld tab in the look.

- Concept B, Workspaces, the reviewed design: five workspaces (Make it
  mine, Code, Art, World, Sound), each a fixed arrangement of panes, at
  1024x600 (P4 metrics, the desk class) in Day and Night, and at 800x480.
  The pane and tool sizes are EDITOR_LAYOUT.md's.
- Concepts A (Project) and C (Splits) were not chosen; their pictures stay
  as the first revision drew them (fb049301) and are indexed, not redrawn.
- The handheld keeps one tab at a time under one bar: every tab in the
  look, in Day and Night, with the sprite tab's more menu and the scene's
  stamp palette.
- The panels draw a real project: Sky Run's code, sheet, map and scene
  (Coin Quest's blocks and scene for Blocks); the sound bank past Sky
  Run's two effects is illustrative.
- Icons are the shipped default set (native/moy_ui/icons/smooth/default/);
  the glyphs that set lacks are drawn here (`NEW_GLYPHS`).

A layer of the target, not an entry point: `../draw.py` draws every picture.
"""
import io
import json
import os
import sys

from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import round11 as R11  # noqa: E402

R10, R9, R8, R7, M = R11.R10, R11.R9, R11.R8, R11.R7, R11.M
Face, F, HH, DK, mix = M.Face, M.F, M.HH, M.DK, M.mix
LIGHT, DARK = R11.LIGHT, R11.DARK
CARTS = os.path.join(R7.REPO, "system_carts")
SHIPPED = os.path.join(R7.REPO, "native", "moy_ui", "icons", "smooth", "default")


# ==========================================================================
# icons: the shipped set, and the slots it lacks
# ==========================================================================
_S = 'fill="none" stroke="#000" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"'
NEW_GLYPHS = {
    # a rounded square: Stop
    "stop": '<rect x="6" y="6" width="12" height="12" rx="2.5" fill="#000"/>',
    # two vertical bars: Pause
    "pause": '<rect x="6.5" y="5" width="4" height="14" rx="1.5" fill="#000"/>'
             '<rect x="13.5" y="5" width="4" height="14" rx="1.5" fill="#000"/>',
    # a pane grown to its corners: grow a pane
    "grow": f'<path d="M14 4.5h5.5V10M10 19.5H4.5V14M19.5 4.5l-6 6M4.5 19.5l6-6" {_S}/>',
    # two arrows round a loop: Loop
    "loop": f'<path d="M5 11V9.5A3.5 3.5 0 0 1 8.5 6H18M15.5 3.5 18 6l-2.5 2.5M19 13v1.5A3.5 3.5 0 0 1 '
            f'15.5 18H6M8.5 20.5 6 18l2.5-2.5" {_S}/>',
    # a 3x3 grid: snap to the grid
    "grid": '<path d="M4.5 4.5h15v15h-15zM9.5 4.5v15M14.5 4.5v15M4.5 9.5h15M4.5 14.5h15" fill="none" '
            'stroke="#000" stroke-width="1.6" stroke-linejoin="round"/>',
    # a clipboard with a down arrow: Paste
    "paste": f'<path d="M9 4.5h6v2.5H9zM7.5 5.5H6A1.5 1.5 0 0 0 4.5 7v12A1.5 1.5 0 0 0 6 20.5h12a1.5 1.5 '
             f'0 0 0 1.5-1.5V7A1.5 1.5 0 0 0 18 5.5h-1.5M12 10v7M9 14l3 3 3-3" {_S}/>',
    # a box with a corner pulled out: resize the map
    "resize": f'<path d="M4.5 9.5V6A1.5 1.5 0 0 1 6 4.5h12A1.5 1.5 0 0 1 19.5 6v12a1.5 1.5 0 0 1-1.5 '
              f'1.5h-3.5M4.5 13.5h6v6h-6zM13 11l4-4M13.5 7H17v3.5" {_S}/>',
    # a speaker with a wave: volume
    "volume": f'<path d="M4.5 9.5h3l4.5-4v13l-4.5-4h-3zM15.5 9a4 4 0 0 1 0 6M18 6.5a7.5 7.5 0 0 1 0 '
              f'11" {_S}/>',
    # a cursor arrow: pick and move
    "pointer": '<path d="M6 3.5v15l4-3.8 2.6 5.8 2.6-1.2-2.6-5.6H18z" fill="none" stroke="#000" '
               'stroke-width="2" stroke-linejoin="round"/>',
    # a padlock: read only
    "lock": '<rect x="5.5" y="10.5" width="13" height="9.5" rx="2" fill="none" stroke="#000" '
            'stroke-width="2"/><path d="M8.5 10.5V8a3.5 3.5 0 0 1 7 0v2.5" fill="none" stroke="#000" '
            'stroke-width="2"/>',
    # an eyedropper: pick a colour
    "picker": f'<path d="M14.5 4.5l5 5-2 2-5-5zM13.5 8.5 5.5 16.5 4.5 19.5l3-1 8-8" {_S}/>',
    # an arrow into a bracket: go to definition
    "jump": f'<path d="M4.5 12h10M11 8.5 14.5 12 11 15.5M18.5 5v14" {_S}/>',
    # a square with a dot: the brush's size
    "brush": f'<rect x="5" y="5" width="14" height="14" rx="2" {_S}/>'
             '<rect x="9.5" y="9.5" width="5" height="5" rx="1" fill="#000"/>',
    # a screen with a stand: the game
    "screen": f'<rect x="3.5" y="4.5" width="17" height="12" rx="2" {_S}/><path d="M9 20h6M12 16.5V20" {_S}/>',
    # a flag: tile flags
    "flag": f'<path d="M6 20.5V4.5M6 5h10.5l-2.5 4 2.5 4H6" {_S}/>',
    # a list with a pen: the log
    "log": f'<path d="M5 6.5h14M5 11.5h14M5 16.5h8" {_S}/>',
}

_SHIPPED_ON = [False]
_MASKS = {}
_base_mask = R7.mask


def mask(slot, set_name, layer, px, bpp):
    """While this round draws, the shipped set's mask (or a new glyph's);
    the older rounds keep their own sources."""
    if not _SHIPPED_ON[0] or set_name != "default":
        return _base_mask(slot, set_name, layer, px, bpp)
    key = (slot, px, bpp)
    if key not in _MASKS:
        path = os.path.join(SHIPPED, slot + ".svg")
        if slot in NEW_GLYPHS:
            src = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">'
                   + NEW_GLYPHS[slot] + "</svg>")
        elif os.path.exists(path):
            src = open(path).read()
        else:
            return _base_mask(slot, set_name, layer, px, bpp)
        im = Image.open(io.BytesIO(bytes(R7.resvg_py.svg_to_bytes(svg_string=src, width=px,
                                                                   height=px))))
        _MASKS[key] = M.levels(im.convert("RGBA").split()[3], 1 << bpp)
    return _MASKS[key]


R7.mask = mask


def danger(T):
    """The `danger` ink: the theme's, else a red held to 4.5:1 on the mode's surface."""
    return T["danger"] or ("#ff7a6e" if T["mode"] == "dark" else "#c4231b")


def ic(cv, T, slot, x, y, px, ink):
    R7.draw_icon(cv, T, slot, x, y, px, ink)


# ==========================================================================
# the project: Sky Run's sheet, map, scene and code; Coin Quest's blocks
# ==========================================================================
PALX = [M.q(c) for c in R7.MOY64]


def colour(name):
    return PALX[R7.NAMES[name]]


def load_sheet(cart):
    rows = [ln.strip() for ln in open(os.path.join(CARTS, cart, "sprites.moygfx")) if ln.strip()]
    pix = [[int(ch, 16) for ch in r[:128]] for r in rows[:128]]
    return pix + [[0] * 128 for _ in range(128 - len(pix))]


def load_map(cart):
    lines = open(os.path.join(CARTS, cart, "map.moymap")).read().split()
    w, h = int(lines[0]), int(lines[1])
    return w, h, [[int(r[i:i + 2], 16) - 1 for i in range(0, 2 * w, 2)] for r in lines[2:2 + h]]


def load_scene(cart):
    return json.load(open(os.path.join(CARTS, cart, "scenes", "main.moyscene")))


SKY = load_sheet("moybyte.sky_run.moy")
COIN_SHEET = load_sheet("moybyte.coin_quest.moy")
MW, MH, MAP = load_map("moybyte.sky_run.moy")
ACTORS = load_scene("moybyte.sky_run.moy")
COIN_ACTORS = load_scene("moybyte.coin_quest.moy")
BLOCKS = json.load(open(os.path.join(CARTS, "moybyte.coin_quest.moy", "blocks.json")))


def span(sheet, t, w=1, h=1, scale=1, flip=False):
    """Tile t and the w x h tiles from it, colour 0 clear, at a whole scale."""
    tx, ty = (t % 16) * 8, (t // 16) * 8
    im = Image.new("RGBA", (8 * w, 8 * h), (0, 0, 0, 0))
    for r in range(8 * h):
        for c in range(8 * w):
            v = sheet[ty + r][tx + c]
            if v:
                im.putpixel((8 * w - 1 - c if flip else c, r), PALX[v] + (255,))
    return im.resize((im.width * scale, im.height * scale), Image.NEAREST) if scale > 1 else im


def put(cv, im, x, y):
    """An RGBA image at logical (x, y), inside the canvas's clip."""
    x0, y0 = cv.L(x), cv.L(y)
    cx0, cy0, cx1, cy1 = cv.clip or (0, 0, cv.img.width, cv.img.height)
    bx0, by0 = max(x0, cx0), max(y0, cy0)
    bx1, by1 = min(x0 + im.width, cx1), min(y0 + im.height, cy1)
    if bx1 <= bx0 or by1 <= by0:
        return
    im = im.crop((bx0 - x0, by0 - y0, bx1 - x0, by1 - y0))
    cv.img.paste(im.convert("RGB"), (bx0, by0), im.split()[3])


GY = 23 * 8
TAKEN = 2


def sky_world(hud=True, cam=0, runner=200):
    """A frame of Sky Run as its _draw paints it: the layer, then the coins,
    the flag and the runner; `cam` is the camera's x."""
    g = M.Cv(800, 240, colour("dark_blue"))
    g.rect(0, GY - 64, 800, 64, colour("blue"))
    for a in ACTORS:
        if a["tag"] == "cloud":
            put(g, span(SKY, 34, 3, 2, 2), a["x"], a["y"])
    for hx, r, c in ((50, 70, "indigo"), (210, 92, "dark_purple"), (410, 80, "indigo"),
                     (620, 100, "dark_purple"), (780, 75, "indigo")):
        g.shape(hx - r, GY - r, 2 * r, 2 * r,
                lambda d, s, r=r: d.ellipse((0, 0, 2 * r * s - 1, 2 * r * s - 1), fill=255), colour(c))
    for my in range(MH):
        for mx in range(MW):
            t = MAP[my][mx]
            if t >= 0:
                put(g, span(SKY, t), mx * 8, my * 8)
    for x in range(6, 800, 11):
        if MAP[23][x // 8] >= 0:
            g.rect(x, GY - 4, 1, 4, colour("green"))
    for a in ACTORS:
        if a["tag"] == "tree":
            put(g, span(SKY, 32, 2, 3, 2), a["x"], a["y"])
    n = 0
    for a in ACTORS:
        if a["tag"] == "coin":
            n += 1
            if n > TAKEN:
                put(g, span(SKY, 6), a["x"], a["y"])
        elif a["tag"] == "goal":
            put(g, span(SKY, 38, 2, 4, 2), a["x"], GY - 60)
    put(g, span(SKY, 0, 1, 2, 2), runner - 2, GY - 23)
    im = g.image().crop((cam, 0, cam + 320, 240))
    if hud:
        h = M.Cv(320, 240, "#000000")
        h.img.paste(im, (0, 0))
        h.text(6, 11, "COINS %d/6" % TAKEN, M.Petme(1, 1), PALX[7])
        im = h.image()
    return im


GAME = sky_world(cam=96, runner=226)


def coin_frame():
    g = M.Cv(320, 240, colour("dark_blue"))
    for a in COIN_ACTORS:
        put(g, span(COIN_SHEET, a["tile"], 1, 1, 2), a["x"] - 4, a["y"] - 4)
    pf = M.Petme(1, 1)
    g.text(4, 11, "SCORE", pf, PALX[7])
    g.text(44, 11, "2", pf, colour("yellow"))
    return g.image()


COIN_GAME = coin_frame()


def code_rows(cart):
    saved = R7.SRC
    R7.SRC = os.path.join(CARTS, cart, "main.py")
    try:
        return R7.tokens_by_line()
    finally:
        R7.SRC = saved


SKY_CODE = code_rows("moybyte.sky_run.moy")


# ==========================================================================
# type and the common parts
# ==========================================================================
def fx(T, size, w="med", bpp=4):
    return Face(T["fam"], size, T["w_" + w], bpp)


def mono(desk=True):
    return Face("jbmono", 11, 400, 4 if desk else 2)


def syn(T):
    return R7.SYN["moy-dark" if T["mode"] == "dark" else "moy-light"]


PH = 32          # a pane's header, and its tool cells
G = 6            # the gutter between panes
STRIP = 44       # the window's strip, which carries the Editor's tools
TITLE_W = {1024: 144, 800: 112}     # the strip's title slot: the workspaces start after it
PLAY_W = 170     # the strip's Play slot: Play, or Restart and Stop, never moving undo


def new_canvas(T, W, H):
    return M.Cv(W, H, T["bg"], k=M.kscale(T, True), aa=T["render"]["shapes"] == "smooth")


def focus_plate(cv, T, x, y, w, h, r=0):
    """Moy's focus on a row or card: the 4 px `accent` edge over a light
    `accent` plate (0.12 Day, 0.14 Night)."""
    cv.rrect(x, y, w, h, r, T["accent"], 0.14 if T["mode"] == "dark" else 0.12)
    cv.rrect(x, y, 4, h, 2, T["accent"])
    return T["text"]


def desk_bar(cv, T, chips):
    """The desk bar: Moy, the window chips (the first current), the status."""
    W = cv.W
    b = T["bar"]
    cv.rect(0, 0, W, b["h"], b["fill"] or T["surface"], b["alpha"])
    if b["line"]:
        cv.rect(0, b["h"] - b["line"][0], W, b["line"][0], b["line"][1])
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
            M.current_mark(cv, T, x, 6, w, 28, 8)
        ic(cv, T, slot, x + 12, 11, 18, c)
        cv.text(x + 38, 25, name, fc, c)
        x += w + 6
    fs = F(T, "status", DK)
    tw = cv.text(W - 14, 26, "14:32", fs, T["text"], align="right")
    ic(cv, T, "battery", W - 14 - tw - 30, 9, 22, T["text"])
    ic(cv, T, "wifi", W - 14 - tw - 58, 8, 21, T["text"])


def pill(cv, T, x, y, h, slot, label, on, face, pad=8, icon_px=18):
    """A labelled tab or button: an icon and a word, on the current plate
    when on. Returns its width."""
    w = icon_px + (6 + face.width(label) if label else 0) + 2 * pad
    c = T["dim"]
    if on:
        c = M.current_mark(cv, T, x, y, w, h, 8) or T["text"]
    ic(cv, T, slot, x + pad, y + (h - icon_px) // 2, icon_px, c)
    if label:
        cv.text(x + pad + icon_px + 6, M.mid_base(face, y, h), label, face, c)
    return w


def editor_strip(cv, T, title, mid=None, playing=False, waiting=False):
    """The maximised Editor's strip (no top accent: the focused pane wears
    it): the icon and the title in a fixed slot, the Editor's row from the
    slot's end, then undo and redo at a fixed place, the Play slot (Play,
    or Restart and Stop; a dot on Restart while an edit waits for the next
    Play), and minimise, restore and close."""
    W = cv.W
    y = T["bar"]["h"]
    cv.rect(0, y, W, STRIP, T["surface"])
    cv.rect(0, y + STRIP - 1, W, 1, T["line"])
    ft = F(T, "wtitle", DK)
    ic(cv, T, "code", 16, y + 12, 20, T["text"])
    slot_w = TITLE_W.get(W, 144)
    cv.text(46, M.mid_base(ft, y, STRIP), title, ft, T["text"], maxw=slot_w - 8)
    s = 20
    for i, g in enumerate(("close", "maximize", "minimize")):
        ic(cv, T, g, W - 14 - s - 34 * i, y + (STRIP - s) // 2, s, T["text"])
    px = W - 14 - s - 68 - 12 - PLAY_W
    fp = fx(T, 15, "semi")
    if playing:
        x = px + 4
        for g, label in (("rotate", "Restart"), ("stop", "Stop")):
            ic(cv, T, g, x, y + 13, 18, T["value"])
            if g == "rotate" and waiting:
                cv.shape(x + 13, y + 9, 8, 8, lambda d, s_: d.ellipse((0, 0, 8 * s_ - 1, 8 * s_ - 1),
                                                                       fill=255), T["value"])
                cv.ring(x + 12, y + 8, 10, 10, 5, 1.5, T["surface"])
            x += 24 + cv.text(x + 24, M.mid_base(fp, y, STRIP), label, fp, T["value"]) + 16
    else:
        ic(cv, T, "play", px + 4, y + 13, 18, T["value"])
        cv.text(px + 28, M.mid_base(fp, y, STRIP), "Play", fp, T["value"])
    cv.rect(px - 8, y + 12, 1, STRIP - 24, T["line"])
    ic(cv, T, "redo", px - 40, y + 12, 20, mix(T["dim"], T["surface"], 0.55))
    ic(cv, T, "undo", px - 70, y + 12, 20, T["dim"])
    if mid:
        mid(cv, T, 46 + slot_w, y, px - 80)
    return y + STRIP


def body_bg(cv, T, y):
    cv.rect(0, y, cv.W, cv.H - y, T["bg"])


def pane(cv, T, x, y, w, h, title=None, slot=None, focus=False, tabs=None, tools=(), grow=True,
         header=True, compact=False, status=None, drop=False, cur=0):
    """A pane: a `surface` card on the Editor's ground. Its header holds an
    icon and a name (and a dim status), or its tabs (`compact`: the others
    as icons), then its tools in 32 px cells and the grow button; the
    focused pane wears the `value` edge along its top. Returns the body."""
    r = 8
    cv.rrect(x, y, w, h, r, T["surface"])
    if T["mode"] == "dark":
        cv.ring(x, y, w, h, r, 1, T["line"])
    if focus:
        cv.set_clip(x, y, w, 3)
        cv.rrect(x, y, w, 2 * r, r, T["value"])
        cv.clip = None
    if not header:
        return x, y, w, h
    ft = fx(T, 13, "semi")
    ink = T["text"] if focus else T["dim"]
    tx = x + 10
    if tabs:
        for i, (tslot, label) in enumerate(tabs):
            on = i == cur
            show = label if (on or not compact or label == "Game") else None
            tw = 16 + (6 + ft.width(show) if show else 0) + 16
            c = T["dim"]
            if on:
                c = M.current_mark(cv, T, tx - 4, y + 4, tw, PH - 8, 6) or T["text"]
            ic(cv, T, tslot, tx + 4, y + (PH - 16) // 2, 16, c)
            if show:
                cv.text(tx + 26, M.mid_base(ft, y, PH), show, ft, c)
            tx += tw + 2
    elif title:
        if slot:
            ic(cv, T, slot, tx + 2, y + (PH - 16) // 2, 16, ink)
            tx += 24
        tx += cv.text(tx, M.mid_base(ft, y, PH), title, ft, ink)
        if drop:
            ic(cv, T, "down", tx + 4, y + 9, 14, ink)
            tx += 20
    if status:
        fm = fx(T, 12)
        tx += 10 + cv.text(tx + 10, M.mid_base(fm, y, PH), status, fm, T["dim"])
    xr = x + w - 4
    if grow:
        xr -= PH
        ic(cv, T, "grow", xr + 8, y + 8, 16, T["dim"])
    if tools and tools_right(cv, T, xr, y, PH, tools, draw=False) < tx + 4:
        cv.rect(x, y + PH - 1, w, 1, T["line"])
        tools_right(cv, T, x + w - 4, y + PH, PH, tools)
        cv.rect(x, y + 2 * PH - 1, w, 1, T["line"])
        return x, y + 2 * PH, w, h - 2 * PH
    tools_right(cv, T, xr, y, PH, tools)
    cv.rect(x, y + PH - 1, w, 1, T["line"])
    return x, y + PH, w, h - PH


def tools_right(cv, T, xr, y, h, tools, cell=PH, px=18, draw=True):
    """Tools from the right edge leftwards, 32 px cells: (slot, state) with
    state None, 'cur', 'off', or a text: '2×' a ‹ value › stepper after its
    glyph ('~16' a stepper with no ×), any other text a chip ('1×1',
    'Edit map') after its glyph.
    Returns the left edge they reach."""
    fm = fx(T, 13, "med")
    for slot, state in reversed(list(tools)):
        if slot == "|":
            xr -= 5
            if draw:
                cv.rect(xr, y + 8, 1, h - 16, T["line"])
            xr -= 5
            continue
        if isinstance(state, str) and state not in ("cur", "off"):
            stepper_ = (state.endswith("×") and state != "1×1") or state.startswith("~")
            state = state.lstrip("~")
            tw = fm.width(state)
            w = tw + (34 if stepper_ else 14) + (px + 6 if slot else 0)
            xr -= w
            if draw:
                ink = T["dim"] if slot != "map" else T["value"]
                if slot:
                    ic(cv, T, slot, xr + 4, y + (h - px) // 2, px, ink)
                tx = xr + (px + 10 if slot else 4)
                if stepper_:
                    cv.text(tx, M.mid_base(fm, y, h), "‹", fm, T["dim"])
                    cv.text(tx + 12, M.mid_base(fm, y, h), state, fm, T["text"])
                    cv.text(tx + 16 + tw, M.mid_base(fm, y, h), "›", fm, T["dim"])
                else:
                    cv.text(tx, M.mid_base(fm, y, h), state, fm,
                            T["value"] if slot == "map" else T["text"])
            xr -= 2
            continue
        xr -= cell
        if draw:
            c = T["dim"]
            if state == "cur":
                c = M.current_mark(cv, T, xr + 1, y + (h - cell) // 2 + 1, cell - 2, cell - 2, 7) \
                    or T["text"]
            elif state == "off":
                c = mix(T["dim"], T["surface"], 0.55)
            ic(cv, T, slot, xr + (cell - px) // 2, y + (h - px) // 2, px, c)
    return xr


def caption(cv, T, x, y, s, face=None, ink=None):
    face = face or fx(T, 12, "semi")
    cv.text(x, y + face.cap, s, face, ink or T["dim"])


def two_tone(cv, T, x, y, w, h, t=1):
    """The selection mark: a line of `ink` outside, one of `surface` within."""
    cv.ring(x - t, y - t, w + 2 * t, h + 2 * t, 0, t, T["text"])
    cv.ring(x, y, w, h, 0, t, T["surface"])


def cursor_mark(cv, T, x, y, s, live):
    """The cursor: the theme's ring round a black keyline in the focused
    pane; a 1 px dim keyline in a pane without the keys."""
    if live:
        cv.ring(x - 1, y - 1, s + 2, s + 2, 0, 1, "#000000")
        M.ring_mark(cv, T, x - 1, y - 1, s + 2, s + 2, 0)
    else:
        cv.ring(x, y, s, s, 0, 1, mix(T["dim"], "#ffffff", 0.2), 0.9)


def dashed(cv, x, y, w, h, c, d=6, t=1):
    for xx in range(x, x + w, 2 * d):
        cv.rect(xx, y, min(d, x + w - xx), t, c)
        cv.rect(xx, y + h - t, min(d, x + w - xx), t, c)
    for yy in range(y, y + h, 2 * d):
        cv.rect(x, yy, t, min(d, y + h - yy), c)
        cv.rect(x + w - t, yy, t, min(d, y + h - yy), c)


def keyhints(cv, T, items, base, x):
    R11.hints(cv, T, items, base, x=x, bg=T["surface"], desk=True)


def status_line(cv, T, x, y, w, h, left, hints=None):
    fm = fx(T, 12, "med")
    cv.rect(x, y, w, 1, T["line"])
    cv.text(x + 12, M.mid_base(fm, y, h), left, fm, T["dim"])
    if hints:
        hw = R11.hints_width(T, hints, True)
        keyhints(cv, T, hints, y + h - 5, x + w - 12 - hw)


def small_button(cv, T, x, y, w, h, slot, label, kind="normal", face=None):
    st = M.bstyle(T, "editor", kind)
    cv.rrect(x, y, w, h, 8, st["fill"])
    face = face or fx(T, 13, "med")
    ink = danger(T) if kind == "danger" else st["ink"]
    lw = (18 + 6 if slot else 0) + (face.width(label) if label else 0)
    lx = x + (w - lw) // 2
    if slot:
        ic(cv, T, slot, lx, y + (h - 18) // 2, 18, ink)
        lx += 24
    if label:
        cv.text(lx, M.mid_base(face, y, h), label, face, ink)


def chip(cv, T, x, y, label, on=False, face=None, h=24, dot=None):
    face = face or fx(T, 13, "med")
    w = face.width(label) + 20 + (14 if dot else 0)
    if on:
        M.current_mark(cv, T, x, y, w, h, h / 2)
    else:
        cv.rrect(x, y, w, h, h / 2, M.bstyle(T, "editor", "normal")["fill"])
    tx = x + 10
    if dot:
        cv.rrect(tx, y + (h - 9) // 2, 9, 9, 3, dot)
        tx += 14
    cv.text(tx, M.mid_base(face, y, h), label, face, T["text"])
    return w


# ==========================================================================
# panels
# ==========================================================================
def code_view(cv, T, x, y, w, h, rows, first, cur, col, desk=True, lh=14, read_only=False):
    S = syn(T)
    f = mono(desk)
    adv = f.glyph("m")[3] * cv.k
    digits = len(str(first + h // lh))
    gut = 12 + digits * adv + 10
    for i in range((h - 6) // lh):
        ln = first + i
        if ln > len(rows):
            break
        yy = y + 4 + i * lh
        base = yy + (lh + f.cap) // 2 - 1
        on = ln == cur
        if on:
            M.current_mark(cv, T, x + 4, yy, w - 8, lh, 0)
            cv.rect(x + 4, yy, 2, lh, T["value"])
        cv.text(x + gut - 10, base, str(ln), f, S["text"] if on else S["gutter"], align="right")
        cx = x + gut
        cv.set_clip(x + gut, yy, w - gut - 6, lh)
        for role, s in rows[ln - 1]:
            c = S[role]
            if read_only:
                c = mix(c, T["surface"], 0.25)
            cv.text(cx, base, s, f, c)
            cx += len(s) * adv
        cv.clip = None
        if cx > x + w - 8:
            bg = T["surface"]
            for j, a in enumerate((0.35, 0.6, 0.85)):
                cv.rect(x + w - 8 - (3 - j) * 4, yy, 4, lh, bg, a)
            cv.rect(x + w - 8, yy, 8, lh, bg)
        if on and col is not None:
            cv.rect(x + gut + col * adv - 1, yy + 2, 2, lh - 4, T["accent"])
    return gut, adv


CODE_TOOLS = (("search", None), ("jump", None), ("more", None))


def code_panel(cv, T, x, y, w, h, first, cur, col, focus=True, sig=False, hints=True):
    bx, by, bw, bh = pane(cv, T, x, y, w, h, focus=focus, tabs=(("code", "main.py"),), tools=CODE_TOOLS)
    sh = 26
    gut, adv = code_view(cv, T, bx, by, bw, bh - sh, SKY_CODE, first, cur, col)
    status_line(cv, T, bx, by + bh - sh, bw, sh,
                f"Ln {cur}, Col {col + 1}  ·  {len(SKY_CODE)} lines  ·  No issues  ·  Python",
                (("F6", "Next pane"),) if hints else None)
    if sig:
        signature(cv, T, bx + gut + (col + 1) * adv - 30, by + 4 + (cur - first + 1) * 14 + 4)


def signature(cv, T, x, y):
    """The signature popover under `spr(`: the verb's arguments, its line,
    and the cart's own sprites to pick from (docs/studio_2026-09.md §5.1)."""
    w, h = 300, 150
    M.soft(cv, T, x, y, w, h, 10, 0.8)
    cv.rrect(x, y, w, h, 10, T["raised"] if T["mode"] == "dark" else T["surface"])
    cv.ring(x, y, w, h, 10, 1, T["line"])
    f = mono()
    S = syn(T)
    xx = x + 12
    for s, c in (("spr(", S["fn"]), ("n", T["text"]), (", x, y, colkey, scale, flip, w, h)", S["gutter"])):
        cv.text(xx, y + 20, s, f, c)
        xx += len(s) * f.glyph("m")[3]
    cv.rect(x + 12 + 4 * f.glyph("m")[3], y + 23, f.glyph("m")[3], 2, T["value"])
    cv.text(x + 12, y + 40, "Draws sprite n of the sheet at x, y.", fx(T, 12), T["dim"])
    cv.rect(x + 1, y + 50, w - 2, 1, T["line"])
    cs = 24
    for i in range(10):
        t = i if i < 8 else 24 + i
        tx, ty = x + 12 + i * (cs + 4), y + 60
        R7.checker(cv, tx, ty, cs, cs, 4)
        put(cv, span(SKY, t, 1, 1, 3), tx, ty)
        if t == 6:
            two_tone(cv, T, tx, ty, cs, cs)
    cv.text(x + 12, y + 104, "6  coin", fx(T, 12), T["text"])
    keyhints(cv, T, ((("←", "→"), "Pick"), ("enter", "Insert"), ("Esc", "Close")), y + 136, x + 12)


def game_view(cv, T, bx, by, bw, bh, img, scale, top=None):
    gw, gh = 320 * scale, 240 * scale
    gx = bx + (bw - gw) // 2
    gy = by + (bh - gh) // 2 if top is None else by + top
    cv.paste(img.resize((gw, gh), Image.NEAREST) if scale > 1 else img, gx, gy, r=4)
    return gx, gy


def run_panel(cv, T, x, y, w, h, img=None, scale=1, focus=False, header=True, tabs=None, still=False):
    """The Run slot: the game at a whole scale under a header that reads the
    scale; a still frame, dimmed under a Play glyph, when no game runs."""
    img = img or GAME
    tools = (("pause", "off" if still else None),) if header else ()
    if tabs:
        bx, by, bw, bh = pane(cv, T, x, y, w, h, tabs=tabs, focus=focus, tools=tools, status=f"{scale}×",
                              compact=len(tabs) > 2)
    else:
        bx, by, bw, bh = pane(cv, T, x, y, w, h, title="Game", slot="screen", focus=focus, tools=tools,
                              header=header, status=f"{scale}×" if header else None)
    gx, gy = game_view(cv, T, bx, by, bw, bh, img, scale, top=5 if header else None)
    if still:
        cv.rect(gx, gy, 320 * scale, 240 * scale, "#000000", 0.45)
        ic(cv, T, "play", gx + 160 * scale - 20, gy + 120 * scale - 20, 40, "#ffffff")
    return bx, by, bw, bh


def sheet_view(cv, T, x, y, sheet, scale, r0, nrows, sel=None, sel_wh=(1, 1), cols=16):
    c = 8 * scale
    R7.checker(cv, x, y, cols * c, nrows * c, max(2, c // 4))
    for r in range(nrows):
        for k in range(cols):
            put(cv, span(sheet, (r0 + r) * 16 + k, 1, 1, scale), x + k * c, y + r * c)
    if sel is not None and r0 <= sel // 16 < r0 + nrows:
        sx, sy = x + (sel % 16) * c, y + (sel // 16 - r0) * c
        two_tone(cv, T, sx, sy, c * sel_wh[0], c * sel_wh[1])
    return cols * c, nrows * c


def page_chips(cv, T, x, y, n, cur, vertical=True):
    fm = fx(T, 13, "semi")
    for i in range(n):
        cx, cy = (x, y + i * 34) if vertical else (x + i * 34, y)
        if i == cur:
            M.current_mark(cv, T, cx, cy, 30, 30, 8)
        else:
            cv.rrect(cx, cy, 30, 30, 8, M.bstyle(T, "editor", "normal")["fill"])
        cv.text(cx + 15, M.mid_base(fm, cy, 30), str(i + 1), fm, T["text"] if i == cur else T["dim"],
                align="center")


def sheet_page(cv, T, bx, by, bw, bh, sel=6, page=0):
    """One page of the sheet at 2x with its page chips at the right."""
    sx = bx + (bw - 256 - 44) // 2
    sy = by + (bh - 128) // 2
    sheet_view(cv, T, sx, sy, SKY, 2, page * 8, 8, sel=sel)
    page_chips(cv, T, sx + 256 + 14, sy + 30, 2, page)


def sheet_both(cv, T, x, y, w, h, sel=12, focus=False):
    """Art's Sheet: both pages at 2x side by side, each captioned."""
    bx, by, bw, bh = pane(cv, T, x, y, w, h, title="Sheet", slot="sprite", focus=focus,
                          status="one pick: the sprite and the map's brush")
    fl = fx(T, 13, "semi")
    total = 2 * (18 + 256) + 16
    sx = bx + (bw - total) // 2
    sy = by + (bh - 128) // 2
    for p in range(2):
        px = sx + p * (18 + 256 + 16)
        cv.text(px + 6, sy + 12, str(p + 1), fl, T["dim"], align="center")
        sheet_view(cv, T, px + 18, sy, SKY, 2, p * 8, 8, sel=sel)


TOOLS = ("pencil", "bucket", "oval", "select", "stamp", "picker", "more")


def tool_row(cv, T, x, y, cell, cur=0, px=20, slots=TOOLS, off=("stamp",)):
    for i, slot in enumerate(slots):
        bx = x + i * cell
        c = T["dim"]
        if i == cur:
            c = M.current_mark(cv, T, bx + 2, y + 2, cell - 4, cell - 4, 8) or T["text"]
        elif slot in off:
            c = mix(T["dim"], T["surface"], 0.55)
        ic(cv, T, slot, bx + (cell - px) // 2, y + (cell - px) // 2, px, c)
    return len(slots) * cell


def sprite_grid(cv, T, x, y, cell, sheet, t, cursor=(3, 2), live=True):
    tx, ty = (t % 16) * 8, (t // 16) * 8
    cv.rect(x - 1, y - 1, 8 * cell + 1, 8 * cell + 1, T["line"])
    for r in range(8):
        for c in range(8):
            v = sheet[ty + r][tx + c]
            if v == 0:
                R7.checker(cv, x + c * cell, y + r * cell, cell - 1, cell - 1, max(3, cell // 3))
            else:
                cv.rect(x + c * cell, y + r * cell, cell - 1, cell - 1, PALX[v])
    if cursor:
        cursor_mark(cv, T, x + cursor[0] * cell, y + cursor[1] * cell, cell - 1, live)


def palette(cv, T, x, y, sw, cols=2, sel=11):
    for i in range(16):
        sx, sy = x + (i % cols) * sw, y + (i // cols) * sw
        if i == 0:
            R7.checker(cv, sx, sy, sw, sw, max(3, sw // 4))
        else:
            cv.rect(sx, sy, sw, sw, PALX[i])
    sx, sy = x + (sel % cols) * sw, y + (sel // cols) * sw
    cv.ring(sx, sy, sw, sw, 0, 2, T["text"])
    cv.ring(sx + 2, sy + 2, sw - 4, sw - 4, 0, 1, T["surface"])


def stepper(cv, T, x, y, h, value, face, w=None):
    """‹ value ›: the compact stepper (D-10)."""
    fill = M.bstyle(T, "editor", "normal")["fill"]
    cv.rrect(x, y, h, h, 8, fill)
    ic(cv, T, "back", x + (h - 16) // 2, y + (h - 16) // 2, 16, T["text"])
    vw = w or face.width(value) + 24
    cv.text(x + h + vw // 2, M.mid_base(face, y, h), value, face, T["text"], align="center")
    cv.rrect(x + h + vw, y, h, h, 8, fill)
    ic(cv, T, "forward", x + h + vw + (h - 16) // 2, y + (h - 16) // 2, 16, T["text"])
    return 2 * h + vw


FLAG_COL = (8, 9, 10, 11, 12, 13, 14, 15)


def flag_row(cv, T, x, y, cell, on=(0,)):
    """The 8 flags of the sprite in use (fget/fset): a toggle each in its
    PICO-8 colour, filled when set."""
    d = cell - 14 if cell >= 28 else cell - 6
    for i in range(8):
        fx_, fy = x + i * cell + (cell - d) // 2, y + (cell - d) // 2
        if i in on:
            cv.shape(fx_, fy, d, d, lambda dr, s, d=d: dr.ellipse((0, 0, d * s - 1, d * s - 1), fill=255),
                     PALX[FLAG_COL[i]])
        cv.ring(fx_, fy, d, d, d / 2, 2, PALX[FLAG_COL[i]] if i in on else mix(T["dim"], T["surface"], 0.4))


def sprite_panel(cv, T, x, y, w, h, t=12, focus=True, big=True):
    """The sprite tab's parts at 1024 (big) or 800: the tool row, the
    canvas box (8x8 at 36 or 24), the palette's two columns right of it,
    ‹ n › and Size, the eight flags, the hints."""
    tools = (("flip", None), ("rotate", None))
    bx, by, bw, bh = pane(cv, T, x, y, w, h, title=f"Sprite {t}", slot="sprite", focus=focus, tools=tools)
    cell, sw, tc = (36, 36, 36) if big else (24, 24, 36)
    pad = 14
    tool_row(cv, T, bx + pad - 2, by + 4, tc, px=20 if big else 18)
    gy = by + tc + 10
    sprite_grid(cv, T, bx + pad, gy, cell, SKY, t, live=focus)
    px_ = bx + pad + 8 * cell + 8
    palette(cv, T, px_, gy, sw)
    fr = fx(T, 15 if big else 14)
    yy = gy + 8 * cell + 10
    sh = 36 if big else 32
    sw_ = stepper(cv, T, bx + pad, yy, sh, str(t), fr)
    small_button(cv, T, bx + pad + sw_ + 8, yy, fr.width("1×1") + 24, sh, None, "1×1", face=fr)
    small_button(cv, T, px_, yy, 2 * sw, sh, None, "64", face=fr)
    fy = yy + sh + 6
    fc_ = 36 if big else 24
    flag_row(cv, T, bx + pad, fy, fc_, on=(0,))
    caption(cv, T, px_ + 6, fy + fc_ // 2 - 6, "Flags", ink=T["dim"])
    if big:
        keyhints(cv, T, ((("Q", "W"), "Sprite"), ("M", "More"), ("F6", "Next pane")), by + bh - 8, bx + pad)
    return bx, by, bw, bh


def map_view(cv, T, x, y, w, h, z, camx, camy, actors=None, grid=True, frame=True, sel=None,
             cursor=None, live=True, coin=False, ghost=None):
    """The map at z pixels per world pixel from world (camx, camy): the
    tiles on the sky, the grid, the one-screen frame; `actors` the scene's,
    each drawn as its tile; `ghost` a stamp under the cursor."""
    cv.set_clip(x, y, w, h)
    c = 8 * z
    mw_, mh_ = (40, 30) if coin else (MW, MH)
    cv.rect(x, y, w, h, mix(T["bg"], T["text"], 0.08))
    cv.rect(x - camx * z, y - camy * z, mw_ * c, mh_ * c, colour("dark_blue"))
    if not coin:
        for my in range(MH):
            for mx in range(MW):
                t = MAP[my][mx]
                sx, sy = x + mx * c - camx * z, y + my * c - camy * z
                if t >= 0 and x - c < sx < x + w and y - c < sy < y + h:
                    put(cv, span(SKY, t, 1, 1, z), sx, sy)
    if grid:
        for gx in range(x - (camx * z) % c, x + w, c):
            cv.rect(gx, y, 1, h, "#ffffff", 0.10)
        for gy in range(y - (camy * z) % c, y + h, c):
            cv.rect(x, gy, w, 1, "#ffffff", 0.10)
    sheet = COIN_SHEET if coin else SKY
    for i, a in enumerate(actors or ()):
        sx, sy = x + (a["x"] - camx) * z, y + (a["y"] - camy) * z
        put(cv, span(sheet, a["tile"], 1, 1, z, flip=bool(a.get("flip"))), sx, sy)
        if sel == i:
            two_tone(cv, T, sx, sy, c, c, 2)
            fm = fx(T, 12, "semi")
            tw = fm.width(a["tag"]) + 12
            cv.rrect(sx, sy + c + 6, tw, 20, 6, T["text"])
            cv.text(sx + 6, sy + c + 20, a["tag"], fm, T["surface"])
    if ghost:
        gx_, gy_, t = ghost
        im = span(sheet, t, 1, 1, z)
        a_ = im.split()[3].point(lambda v: v * 0.6)
        im.putalpha(a_)
        put(cv, im, x + (gx_ - camx) * z, y + (gy_ - camy) * z)
        cursor_mark(cv, T, x + (gx_ - camx) * z, y + (gy_ - camy) * z, c, True)
    if frame:
        dashed(cv, x - camx * z, y - camy * z, 320 * z, 240 * z, "#ffffff", d=8, t=2)
    if cursor:
        cursor_mark(cv, T, x + cursor[0] * c - camx * z, y + cursor[1] * c - camy * z, c, live)
    cv.clip = None


def map_tools(z, brush=True):
    out = [("pencil", "cur"), ("rect", None), ("bucket", None), ("select", None), ("eraser", None)]
    if brush:
        out += [("|", None), ("brush", "1×1"), ("search", f"{z}×")]
    return tuple(out)


def map_panel(cv, T, x, y, w, h, z=2, camx=64, camy=96, focus=False, cursor=(26, 22), live=False,
              view_h=288, status=True, brush=True):
    """Art's Map, a Game tab beside it: the tools, the view, a status line."""
    bx, by, bw, bh = pane(cv, T, x, y, w, h, focus=focus, tabs=(("map", "Map"), ("screen", "Game")),
                          tools=map_tools(z, brush))
    vw = bw - 14
    map_view(cv, T, bx + 7, by, vw, view_h, z, camx, camy, cursor=cursor, live=live)
    if status:
        fm = fx(T, 12, "med")
        cv.text(bx + 12, by + view_h + 12, f"Cell {cursor[0]}, {cursor[1]}  ·  tile 12  ·  "
                f"{MW}×{MH}  ·  hold a cell to pick its tile", fm, T["dim"])
    return bx, by, bw, bh


def scene_tools(z, tool="pointer", full=True):
    out = [("pointer", "cur" if tool == "pointer" else None), ("stamp", "cur" if tool == "stamp" else None)]
    if full:
        out += [("|", None), ("flip", None), ("arrow_up", None), ("arrow_down", None), ("del", None)]
    out += [("|", None), ("grid", "cur"), ("search", f"{z}×")]
    return tuple(out)


def field(cv, T, x, y, w, h, text, face, focus=False):
    cv.rrect(x, y, w, h, 7, T["surface"])
    cv.ring(x, y, w, h, 7, 1.5 if focus else 1, T["accent"] if focus else mix(T["dim"], T["surface"], 0.35))
    cv.text(x + 10, M.mid_base(face, y, h), text, face, T["text"])
    if focus:
        cv.rect(x + 10 + face.width(text) + 1, y + 7, 2, h - 14, T["accent"])


def toggle(cv, T, x, y, on):
    w, h = 36, 20
    cv.rrect(x, y, w, h, h / 2, T["value"] if on else mix(T["dim"], T["surface"], 0.6))
    kx = x + w - h + 2 if on else x + 2
    cv.shape(kx, y + 2, h - 4, h - 4, lambda d, s: d.ellipse((0, 0, (h - 4) * s - 1, (h - 4) * s - 1),
                                                           fill=255),
             "#ffffff" if T["mode"] == "light" else T["text"])


TAGS = ("coin", "tree", "cloud", "spawn", "goal")


def world_tabs(cur, compact):
    """World's context tabs in one order, Actor then Stamps (the 800 pane
    leads with Game); the tool selects the tab, never moves it."""
    tabs = [("pointer", "Actor"), ("stamp", "Stamps")]
    if compact:
        tabs = [("screen", "Game")] + tabs
    return tabs, [t[1] for t in tabs].index(cur)


def actor_pane(cv, T, x, y, w, h, sel=17, compact=False):
    """World's context in the pick tool: the selected actor. Tag (a field and
    the tags the scene and the code use), flip and order, its tile and
    place, Remove; Edit map jumps to Art with the map on this place. Every
    control is 32 px tall, its hit area."""
    a = ACTORS[sel]
    tabs, cur = world_tabs("Actor", compact)
    bx, by, bw, bh = pane(cv, T, x, y, w, h, tabs=tabs, cur=cur, compact=compact,
                          tools=(("map", "Edit map"),), grow=not compact)
    fl, fv, fb = fx(T, 14), fx(T, 14, "med"), fx(T, 13, "med")
    pad = 12
    yy = by + 4
    cv.text(bx + pad, M.mid_base(fl, yy, 32), "Tag", fl, T["dim"])
    field(cv, T, bx + 64, yy, bw - 64 - pad, 32, a["tag"], fv)
    yy += 36
    cx = bx + 64
    for tg in TAGS:
        if cx + fx(T, 13, "med").width(tg) + 20 > bx + bw - pad:
            break
        cx += chip(cv, T, cx, yy, tg, on=tg == a["tag"], h=32) + 4
    yy += 38
    cv.rect(bx + pad, yy - 3, bw - 2 * pad, 1, T["line"])
    cv.text(bx + pad, M.mid_base(fl, yy, 32), "Flip", fl, T["dim"])
    toggle(cv, T, bx + 64, yy + 6, False)
    small_button(cv, T, bx + bw - pad - 80, yy, 80, 32, "arrow_down", "Back", face=fb)
    small_button(cv, T, bx + bw - pad - 168, yy, 84, 32, "arrow_up", "Front", face=fb)
    yy += 38
    cv.rect(bx + pad, yy - 3, bw - 2 * pad, 1, T["line"])
    R7.checker(cv, bx + pad, yy + 4, 24, 24, 4)
    put(cv, span(SKY, a["tile"], 1, 1, 3), bx + pad, yy + 4)
    cv.text(bx + pad + 32, M.mid_base(fv, yy, 32), f"{a['tile']}   x {a['x']}  y {a['y']}", fv,
            T["text"])
    small_button(cv, T, bx + bw - pad - 104, yy, 104, 32, "del", "Remove", "danger", face=fb)
    yy += 38
    if compact and yy + 40 < by + bh:
        keyhints(cv, T, (("Del", "Remove"), ("F6", "Next pane")), by + bh - 10, bx + pad)


def stamps_pane(cv, T, x, y, w, h, sel=6, compact=False):
    """World's context in the stamp tool: the sheet to stamp from, each
    stamp carrying the tag its actors get."""
    tabs, cur = world_tabs("Stamps", compact)
    bx, by, bw, bh = pane(cv, T, x, y, w, h, tabs=tabs, cur=cur, compact=compact, grow=not compact)
    sx = bx + 12
    sy = by + 6
    sheet_view(cv, T, sx, sy, SKY, 2, 0, 8, sel=sel)
    page_chips(cv, T, sx + 256 + 14, sy + 30, 2, 0)
    fl, fv = fx(T, 14), fx(T, 14, "med")
    yy = sy + 128 + 6
    cv.text(bx + 12, M.mid_base(fl, yy, 32), "Tag", fl, T["dim"])
    field(cv, T, bx + 56, yy, 120, 32, "coin", fv)
    cv.text(bx + 188, M.mid_base(fx(T, 12), yy, 32) - 7, "each stamp places", fx(T, 12), T["dim"])
    cv.text(bx + 188, M.mid_base(fx(T, 12), yy, 32) + 7, "a tagged actor", fx(T, 12), T["dim"])


# ---- sound ---------------------------------------------------------------
WAVES = ("SQ", "TRI", "SAW", "PLS", "ORG", "NOI", "PHA", "TLT")
WAVE_COL = (12, 11, 8, 9, 14, 6, 13, 10)
NOTE_NAMES = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")


def note_name(n):
    return NOTE_NAMES[n % 12] + str(n // 12)


def wave_ink(T, wv, fill=False):
    """A wave's colour as a mark: darkened in Day to hold 3:1 on white, a
    deeper fill under it; in Night the colour, its fill toward the ground."""
    c = PALX[WAVE_COL[wv]]
    if T["mode"] == "light":
        cap = mix(c, "#000000", 0.42)
        return mix(cap, "#ffffff", 0.65) if fill else cap
    return mix(c, T["surface"], 0.55) if fill else c


def _seq(notes, wave, vol, speed=16, waves=None):
    st = []
    for i, n in enumerate(notes):
        wv = waves[i] if waves else wave
        st.append([n, wv, vol] if n else [0, wv, 0])
    return dict(speed=speed, loop=True, steps=st)


# Sky Run's own two effects, and an illustrative tune for the mockups.
BANK = [
    dict(name="jump", speed=20, loop=False, steps=[[52, 0, 5], [61, 0, 5]]),
    dict(name="coin", speed=26, loop=False, steps=[[64, 1, 4], [71, 1, 4]]),
    dict(name="tune", **_seq((60, 64, 67, 72, 71, 67, 64, 62, 60, 62, 64, 67, 69, 67, 64, 0), 1, 5,
                             waves=(1, 1, 1, 3, 3, 1, 1, 1, 1, 1, 1, 1, 3, 3, 1, 1))),
    dict(name="bass", **_seq((36, 0, 36, 43, 41, 0, 41, 43, 36, 0, 36, 43, 45, 43, 41, 38), 2, 6)),
    dict(name="drums", **_seq((48, 0, 72, 0, 48, 48, 72, 0, 48, 0, 72, 0, 48, 72, 72, 72), 5, 4)),
    dict(name="win", **_seq((72, 76, 79, 84, 0, 84, 0, 84), 3, 6, speed=12)),
]
SONG = [2, 2, 3, 2, 4, 2, 3, 5]


def spark(cv, T, x, y, w, h, sfx, ink):
    """A sound's sketch: a bar a step, its pitch, 2 px at the least."""
    st = sfx["steps"]
    heard = [n for n, _w, v in st if v] or [60]
    lo, hi = min(heard) - 2, max(heard) + 2
    bw = max(2, w // max(8, len(st)))
    for i, (n, wv, v) in enumerate(st[: w // bw]):
        if v:
            bh = max(2, int((n - lo) / max(1, hi - lo) * h))
            cv.rect(x + i * bw, y + h - bh, bw - 1, bh, ink)


def sketch_ink(T, s):
    return wave_ink(T, s["steps"][0][1])


def sounds_list(cv, T, x, y, w, h, sel=2, playing=None):
    bx, by, bw, bh = pane(cv, T, x, y, w, h, title="Sounds", slot="music", tools=(("new", None),), grow=False)
    rh = 40
    fl, fm = fx(T, 14, "med"), fx(T, 12)
    ry = by + 4
    rows = [("Song", None)] + [(f"{i}  {s['name']}", s) for i, s in enumerate(BANK)]
    for i, (label, s) in enumerate(rows):
        on = (i - 1) == sel
        ink = T["text"]
        if on:
            ink = M.current_mark(cv, T, bx + 6, ry, bw - 12, rh - 4, 8) or T["text"]
        if s is None:
            ic(cv, T, "music", bx + 16, ry + (rh - 4 - 18) // 2, 18, T["dim"])
            cv.text(bx + 42, M.mid_base(fl, ry, rh - 4), label, fl, ink)
            cv.text(bx + bw - 16, M.mid_base(fm, ry, rh - 4), f"{len(SONG)} slots", fm, T["dim"],
                    align="right")
        else:
            cv.text(bx + 16, M.mid_base(fl, ry, rh - 4), label, fl, ink)
            spark(cv, T, bx + bw - 78, ry + 8, 42, rh - 20, s, sketch_ink(T, s))
            ic(cv, T, "play", bx + bw - 30, ry + (rh - 4 - 16) // 2, 16,
               T["value"] if playing == i - 1 else T["dim"])
        ry += rh
        if i == 0:
            cv.rect(bx + 12, ry - 2, bw - 24, 1, T["line"])
            ry += 4


def sound_panel(cv, T, x, y, w, h, n=2, focus=True, cursor=4, big=True, dropdown=False):
    """A sound: the wave brush, the pitch graph (two octaves, the selected
    step's note named), each step's wave, the volume ladder, the step ops;
    16 steps a page."""
    s = BANK[n]
    tools = (("loop", "cur" if s["loop"] else None), (None, "~%d" % s["speed"]), ("|", None),
             ("play", None))
    if dropdown:
        bx, by, bw, bh = pane(cv, T, x, y, w, h, title=f"Sound {n}  ·  {s['name']}", slot="music",
                              focus=focus, tools=tools, drop=True)
    else:
        bx, by, bw, bh = pane(cv, T, x, y, w, h, title=f"Sound {n}  ·  {s['name']}", slot="music",
                              focus=focus, tools=tools)
    fm = fx(T, 12, "semi")
    semi = 12 if big else 8
    gh = 24 * semi
    pad = 12
    # the wave brush, then speed and the octave
    wy = by + 4
    xx = bx + pad
    cur_wave = s["steps"][cursor][1]
    for i, wv in enumerate(WAVES):
        on = i == cur_wave
        ww = fm.width(wv) + 24
        c = T["dim"]
        if on:
            c = M.current_mark(cv, T, xx, wy + 2, ww, 28, 6) or T["text"]
        cv.rrect(xx + 6, wy + 12, 8, 8, 2, wave_ink(T, i))
        cv.text(xx + 18, M.mid_base(fm, wy + 2, 28), wv, fm, c)
        xx += ww + (2 if big else 0)
    # the graph
    gx0 = bx + pad + 28
    gy = wy + 36
    lo = 12 * (min(n_ for n_, _w, v in s["steps"] if v) // 12)
    bars = 16
    cw = 26 if big else 22
    gw = bars * cw
    cv.rrect(gx0, gy, gw, gh, 6, T["surface"] if T["mode"] == "light" else T["bg"])
    cv.ring(gx0, gy, gw, gh, 6, 1, T["line"])
    fl = fx(T, 11, "semi")
    for k in range(25):
        yy = gy + gh - k * semi
        if k % 12 == 0:
            cv.rect(gx0, yy, gw, 1, mix(T["line"], T["dim"], 0.3))
            if k < 24:
                cv.text(gx0 - 6, yy - 1, note_name(lo + k), fl, T["dim"], align="right")
        elif k % 12 in (1, 3, 6, 8, 10):
            cv.rect(gx0 + 1, yy - semi, gw - 2, semi, T["text"], 0.03)
    st = s["steps"]
    for i in range(bars):
        cx = gx0 + i * cw
        if i == cursor:
            M.current_mark(cv, T, cx + 1, gy + 1, cw - 2, gh - 2, 4)
        if i < len(st) and st[i][2]:
            nn, wv, v = st[i]
            yy = gy + gh - (nn - lo + 1) * semi
            cv.rect(cx + 4, yy + semi, cw - 8, gy + gh - yy - semi - 1, wave_ink(T, wv, fill=True))
            cv.rrect(cx + 3, yy, cw - 6, semi, 3, wave_ink(T, wv))
            if i == cursor:
                lb = note_name(nn)
                lw = fl.width(lb) + 10
                cv.rrect(cx + cw // 2 - lw // 2, yy - 20, lw, 17, 5, T["text"])
                cv.text(cx + cw // 2, yy - 7, lb, fl, T["surface"], align="center")
    # each step's wave
    sy = gy + gh + 4
    for i in range(bars):
        if i < len(st):
            cv.rrect(gx0 + i * cw + cw // 2 - 4, sy + 4, 8, 8, 2, wave_ink(T, st[i][1]))
    # the volume ladder: 8 levels, 8 px a level
    vh = 8 if big else 5
    vy = sy + 16 + (4 if big else 2)
    for i in range(bars):
        if i < len(st):
            v = st[i][2]
            for k in range(8):
                cv.rect(gx0 + i * cw + 4, vy + (7 - k) * vh, cw - 8, vh - 2,
                        T["value"] if k < v else mix(T["line"], T["surface"], 0.2))
    cv.text(gx0 - 6, vy + 4 * vh + 4, "Vol", fl, T["dim"], align="right")
    # the step ops, and the octave and page steppers
    oy = by + bh - (40 if big else 36)
    ops = (("new", None), ("del", None), ("copy", None), ("paste", None), ("dup", None),
           ("arrow_up", None), ("arrow_down", None), ("mute", None))
    ob = 36
    xx = bx + pad
    for slot, _ in ops:
        small_button(cv, T, xx, oy, ob, ob, slot, None, "danger" if slot == "del" else "normal")
        xx += ob + 2
    fp = fx(T, 13, "med")
    if big:
        cv.text(bx + bw - pad, M.mid_base(fp, oy, ob), "Oct ‹ 5 ›   1–16 ›", fp, T["dim"],
                align="right")
    else:
        cv.text(bx + bw - pad, M.mid_base(fp, oy, ob), "Oct ‹ 5 ›  1–16 ›", fp, T["dim"], align="right")
    return bx, by, bw, bh


def song_panel(cv, T, x, y, w, h, at=3, tabs=None, compact=False):
    if tabs:
        bx, by, bw, bh = pane(cv, T, x, y, w, h, tabs=tabs, compact=compact, grow=not compact,
                              tools=(("loop", "cur"), ("play", None)))
    else:
        bx, by, bw, bh = pane(cv, T, x, y, w, h, title="Song", slot="music",
                              tools=(("loop", "cur"), ("play", None)))
    caption(cv, T, bx + 14, by + 8, "The phrase loops: one sound a slot")
    sw, shh, gap = 72, 56, 8
    x0 = bx + (bw - (4 * sw + 3 * gap)) // 2
    sy0 = by + 28
    fm, fl = fx(T, 12), fx(T, 15, "semi")
    for i, n in enumerate(SONG):
        sx = x0 + (i % 4) * (sw + gap)
        sy = sy0 + (i // 4) * (shh + gap)
        if i == at:
            M.current_mark(cv, T, sx, sy, sw, shh, 8)
        else:
            cv.rrect(sx, sy, sw, shh, 8, T["raised"] if T["mode"] == "light" else T["bg"])
        cv.text(sx + 8, sy + 19, str(n), fl, T["text"])
        cv.text(sx + 22, sy + 19, BANK[n]["name"], fm, T["dim"], maxw=sw - 26)
        if i == at:
            ic(cv, T, "play", sx + sw - 18, sy + 6, 12, T["value"])
        spark(cv, T, sx + 8, sy + 28, sw - 16, 20, BANK[n],
              wave_ink(T, BANK[n]["steps"][0][1]) if T["mode"] == "dark" else
              mix(wave_ink(T, BANK[n]["steps"][0][1]), T["surface"], 0.1))
    return bx, by, bw, bh


# ---- blocks --------------------------------------------------------------
CAT = {  # the insert menu's categories (block_editor_ui._CAT_LABEL) and their colours
    "events": ("When...", "#ffcc00"), "control": ("Control", "#ff9a3c"), "draw": ("Draw", "#29adff"),
    "input": ("Buttons", "#ff77a8"), "variables": ("Variables", "#ff6f3c"), "lists": ("Lists", "#e86a17"),
    "actors": ("Sprite", "#4c8df6"), "looks": ("Looks", "#a070ff"), "operators": ("Math", "#00c26b"),
    "sound": ("Sound", "#d65cc6"), "myblocks": ("My Blocks", "#ff4d6d"),
}
# The labels and categories of the blocks Coin Quest uses (runtime/blocks.py's
# CATALOG), copied so the renderer imports no runtime.
BLOCK_LABELS = {
    "on_start": ("when program starts", "events"), "on_update": ("every frame (update)", "events"),
    "on_draw": ("every frame (draw)", "events"), "if": ("if {cond}", "control"),
    "cls": ("clear screen to {color}", "draw"), "print": ("write {text} at x {x} y {y} in {color}", "draw"),
    "btn": ("button {dir} held", "input"), "set_var": ("set {var} to {value}", "variables"),
    "change_var": ("change {var} by {value}", "variables"), "actor_touching": ("touching {tag}?", "actors"),
    "move_actor_by": ("move by {dx} {dy}", "actors"), "remove_actor": ("remove me", "actors"),
    "draw_scene": ("draw scene", "actors"), "sfx": ("play sound {n}", "sound"), "var": ("{var}", "variables"),
}


def _label(b):
    out, cat = BLOCK_LABELS[b["t"]]
    p = b.get("p", {})

    def v(k):
        x = p.get(k, "")
        if isinstance(x, dict):
            return _label(x)[0]
        return {"left": "←", "right": "→", "up": "↑", "down": "↓"}.get(x, str(x))
    for k in p:
        out = out.replace("{%s}" % k, v(k))
    return out, cat


def tint(T, col):
    return 0.16 if T["mode"] == "light" else 0.28


def add_pill(cv, T, x, y, face, h=24):
    """+ add block: a `normal` pill under a hat or at a script's end."""
    w = face.width("add block") + 34
    cv.rrect(x, y, w, h, h / 2, M.bstyle(T, "editor", "normal")["fill"])
    ic(cv, T, "new", x + 8, y + (h - 14) // 2, 14, T["dim"])
    cv.text(x + 26, M.mid_base(face, y, h), "add block", face, T["dim"])
    return w


def blocks_view(cv, T, x, y, w, h, script, sel=None):
    """A script as the editor's outline: each event a hat in its category's
    colour, each statement a row on its category's tint and edge."""
    fl, fp = fx(T, 14, "med"), fx(T, 13, "med")
    rh = 30
    yy = y + 10
    i = 0

    def stmt(b, depth):
        nonlocal yy, i
        label, cat = _label(b)
        col = CAT.get(cat, ("", "#888888"))[1]
        bx = x + 14 + depth * 22
        bw = min(w - (bx - x) - 14, fl.width(label) + 40)
        if yy + rh > y + h:
            return
        if sel == i:
            focus_plate(cv, T, bx - 6, yy - 2, bw + 12, rh, 6)
        cv.rrect(bx, yy, bw, rh - 4, 6, col, tint(T, col))
        cv.rrect(bx, yy, 4, rh - 4, 2, col)
        cv.text(bx + 14, M.mid_base(fl, yy, rh - 4), label, fl, T["text"])
        i += 1
        yy += rh
        for c in b.get("c", ()):
            stmt(c, depth + 1)

    for ev in script:
        label, cat = _label(ev)
        hw = fl.width(label) + 32
        cv.rrect(x + 14, yy, hw, rh, 8, CAT["events"][1])
        cv.text(x + 28, M.mid_base(fl, yy, rh), label, fl, "#1a1530")
        yy += rh + 4
        for c in ev.get("c", ()):
            stmt(c, 1)
        add_pill(cv, T, x + 36, yy, fp, h=32)
        yy += 42


def blocks_panel(cv, T, x, y, w, h, obj="coin", focus=True, sel=1):
    objs = [o for o in BLOCKS["objects"] if o["tag"] == obj]
    tabs = [("sprite", obj), ("scene", "Stage")] + [("sprite", o["tag"]) for o in BLOCKS["objects"]
                                                    if o["tag"] != obj]
    tools = (("new", None), ("del", None), ("arrow_up", None), ("arrow_down", None), ("more", None))
    bx, by, bw, bh = pane(cv, T, x, y, w, h, focus=focus, tabs=tabs, tools=tools)
    blocks_view(cv, T, bx, by, bw, bh - 28, objs[0]["scripts"], sel=sel)
    keyhints(cv, T, (("enter", "Edit"), ("Del", "Delete"), ("F6", "Next pane")), by + bh - 10, bx + 14)


def block_palette(cv, T, x, y, w, h, cur="actors"):
    """The palette: the categories as a grid of 32 px chips, then the
    current category's blocks as 32 px rows."""
    bx, by, bw, bh = pane(cv, T, x, y, w, h, title="Blocks", slot="blocks", grow=False)
    fc = fx(T, 13, "med")
    cw = (bw - 20) // 2
    keys = list(CAT)
    for i, key in enumerate(keys):
        label, col = CAT[key]
        cx, cy = bx + 8 + (i % 2) * (cw + 4), by + 6 + (i // 2) * 32
        on = key == cur
        if on:
            M.current_mark(cv, T, cx, cy, cw, 30, 7)
        cv.rrect(cx + 8, cy + 10, 10, 10, 3, col)
        cv.text(cx + 24, M.mid_base(fc, cy, 30), label, fc, T["text"])
    yy = by + 6 + 6 * 32 + 6
    cv.rect(bx + 10, yy, bw - 20, 1, T["line"])
    yy += 8
    col = CAT[cur][1]
    for label in ("move by 2 0", "touching player?", "remove me", "flip me", "go to x y",
                  "point at player", "hide me"):
        if yy + 32 > by + bh:
            break
        bw_ = min(bw - 20, fc.width(label) + 26)
        cv.rrect(bx + 10, yy + 2, bw_, 28, 6, col, tint(T, col))
        cv.rrect(bx + 10, yy + 2, 4, 28, 2, col)
        cv.text(bx + 22, M.mid_base(fc, yy + 2, 28), label, fc, T["text"])
        yy += 32


def coin_code():
    import keyword
    import tokenize
    src = ("def coin_update(a):\n"
           "    if touching(a, \"player\"):\n"
           "        remove(a)\n"
           "        score += 1\n"
           "        sfx(0)\n"
           "\n"
           "def _draw():\n"
           "    cls(col(\"dark_blue\"))\n"
           "    draw_scene()\n"
           "    print(\"SCORE\", 4, 4, col(\"white\"))\n")
    out = []
    for ln in src.split("\n"):
        row, col = [], 0
        try:
            toks = list(tokenize.generate_tokens(io.StringIO(ln + "\n").readline))
        except tokenize.TokenError:
            toks = []
        for k, t in enumerate(toks):
            if t.type in (tokenize.NEWLINE, tokenize.NL, tokenize.ENDMARKER, tokenize.INDENT,
                          tokenize.DEDENT):
                continue
            nxt = toks[k + 1] if k + 1 < len(toks) else None
            role = ("str" if t.type == tokenize.STRING else "num" if t.type == tokenize.NUMBER else
                    "kw" if t.type == tokenize.NAME and keyword.iskeyword(t.string) else
                    "fn" if t.type == tokenize.NAME and nxt is not None and nxt.string == "(" else "text")
            if t.start[1] > col:
                row.append(("text", ln[col:t.start[1]]))
            row.append((role, t.string))
            col = t.end[1]
        out.append(row)
    return out


# ---- Make it mine ---------------------------------------------------------
def slider(cv, T, x0, x1, y, f):
    cv.rrect(x0, y, x1 - x0, 4, 2, mix(T["dim"], T["surface"], 0.6))
    cv.rrect(x0, y, int((x1 - x0) * f), 4, 2, T["value"])
    kx = x0 + int((x1 - x0) * f)
    cv.shape(kx - 9, y - 7, 18, 18, lambda d, s: d.ellipse((0, 0, 18 * s - 1, 18 * s - 1), fill=255),
             T["value"])


def cards_panel(cv, T, x, y, w, h, focus=True):
    """Make it mine's cards, titled Settings: Run at (one slider, its value
    at the right), Hero (sprite tiles), Autorun (a toggle); This project."""
    bx, by, bw, bh = pane(cv, T, x, y, w, h, title="Settings", slot="sliders", focus=focus)
    fl, fv = fx(T, 15, "med"), fx(T, 15, "semi")
    pad = 16
    yy = by + 8
    focus_plate(cv, T, bx + 6, yy, bw - 12, 66, 8)
    cv.text(bx + pad + 4, yy + 24, "Run at", fl, T["text"])
    cv.text(bx + bw - pad, yy + 24, "130", fv, T["text"], align="right")
    slider(cv, T, bx + pad + 4, bx + bw - pad - 6, yy + 44, (130 - 60) / (220 - 60))
    yy += 74
    cv.rect(bx + pad, yy, bw - 2 * pad, 1, T["line"])
    yy += 8
    cv.text(bx + pad, M.mid_base(fl, yy, 64), "Hero", fl, T["text"])
    for i, t in enumerate((0, 3)):
        hx = bx + bw - pad - 136 + i * 72
        if i == 0:
            M.current_mark(cv, T, hx, yy, 64, 64, 10)
        else:
            cv.rrect(hx, yy, 64, 64, 10, T["bg"])
        put(cv, span(SKY, t, 1, 2, 3), hx + 20, yy + 8)
    yy += 72
    cv.rect(bx + pad, yy, bw - 2 * pad, 1, T["line"])
    cv.text(bx + pad, M.mid_base(fl, yy, 44), "Autorun", fl, T["text"])
    toggle(cv, T, bx + bw - pad - 36, yy + 12, False)
    yy += 44
    cv.rect(bx + pad, yy, bw - 2 * pad, 1, T["line"])
    yy += 16
    caption(cv, T, bx + pad, yy, "This project")
    yy += 20
    for label, val in (("Cart info", "Sky Run"), ("Files", "6")):
        cv.text(bx + pad, M.mid_base(fl, yy, 38), label, fl, T["text"])
        w2 = cv.text(bx + bw - pad, M.mid_base(fl, yy, 38), "›", fl, T["dim"], align="right")
        cv.text(bx + bw - pad - w2 - 8, M.mid_base(fl, yy, 38), val, fl, T["dim"], align="right")
        yy += 38
        cv.rect(bx + pad, yy, bw - 2 * pad, 1, T["line"])
    keyhints(cv, T, ((("←", "→"), "Change"), ("F6", "Next pane")), by + bh - 12, bx + pad)


# ==========================================================================
# Concept B: Workspaces
# ==========================================================================
WORKSPACES = (("cards", "sliders", "Make it mine"), ("code", "code", "Code"), ("art", "sprite", "Art"),
              ("world", "scene", "World"), ("sound", "music", "Sound"))


def ws_switcher(cur):
    def draw(cv, T, x, y, xmax):
        """Every workspace labelled where the strip has room, else the
        handheld ladder's rule: icons, the current one labelled."""
        f = fx(T, 15, "med")
        full = sum(18 + 6 + f.width(lb) + 16 + 2 for _k, _s, lb in WORKSPACES)
        fits = x + full <= xmax
        for key, slot, label in WORKSPACES:
            on = key == cur
            w = pill(cv, T, x, y + 4, STRIP - 8, slot, label if (fits or on) else None, on, f)
            x += w + 2
    return draw


def frame_b(T, cur, title="Sky Run", playing=False, waiting=False, W=1024, H=600):
    cv = new_canvas(T, W, H)
    desk_bar(cv, T, (("code", "Editor"),))
    y = editor_strip(cv, T, title, ws_switcher(cur), playing=playing, waiting=waiting)
    body_bg(cv, T, y)
    return cv, G, y + G, W - 2 * G, H - y - 2 * G


RUN_H = PH + 240 + 10


def b_config(T):
    cv, x, y, w, h = frame_b(T, "cards", playing=True)
    cards_panel(cv, T, x, y, 360, h)
    run_panel(cv, T, x + 360 + G, y, w - 360 - G, h, scale=2, header=False)
    return cv


def reference(cv, T, x, y, w, h):
    bx, by, bw, bh = pane(cv, T, x, y, w, h, tabs=(("sprite", "Sprites"), ("music", "Sounds"),
                                                   ("map", "Map"), ("log", "Log")), grow=False)
    sheet_page(cv, T, bx, by, bw, bh, sel=6)


def b_code(T):
    cv, x, y, w, h = frame_b(T, "code", playing=True, waiting=True)
    cw = w - 340 - G
    code_panel(cv, T, x, y, cw, h, first=172, cur=189, col=12, sig=True)
    run_panel(cv, T, x + cw + G, y, 340, RUN_H)
    reference(cv, T, x + cw + G, y + RUN_H + G, 340, h - RUN_H - G)
    return cv


def b_blocks(T):
    cv, x, y, w, h = frame_b(T, "code", title="Coin Quest")
    pw = 200
    block_palette(cv, T, x, y, pw, h)
    bw = w - 340 - pw - 2 * G
    blocks_panel(cv, T, x + pw + G, y, bw, h, obj="coin", sel=1)
    rx = x + pw + bw + 2 * G
    sx, sy, sw, sh = pane(cv, T, rx, y, 340, RUN_H, tabs=(("scene", "Scene"), ("screen", "Game")),
                          tools=(("pointer", "cur"), ("stamp", None)))
    map_view(cv, T, sx + 10, sy + 5, 320, 240, 1, 0, 0, actors=COIN_ACTORS, sel=1, coin=True, grid=False)
    gx, gy, gw, gh = pane(cv, T, rx, y + RUN_H + G, 340, h - RUN_H - G,
                          title="Code", slot="lock", status="made by your blocks")
    code_view(cv, T, gx, gy, gw, gh, coin_code(), 1, None, None, read_only=True)
    return cv


def b_art(T, playing=False):
    cv, x, y, w, h = frame_b(T, "art", playing=playing)
    sw = 400
    sprite_panel(cv, T, x, y, sw, h, t=12)
    mx, mw = x + sw + G, w - sw - G
    map_panel(cv, T, mx, y, mw, 336)
    sheet_both(cv, T, mx, y + 336 + G, mw, h - 336 - G, sel=12)
    return cv


def world_scene(cv, T, x, y, w, h, tool="pointer", sel=17):
    bx, by, bw, bh = pane(cv, T, x, y, w, h, title="Scene  ·  main", slot="scene", focus=True,
                          status="coin at 211, 150" if tool == "pointer" else "stamp: coin",
                          tools=scene_tools(2, tool))
    vw, vh = 640, 464
    ghost = (248, 150, 6) if tool == "stamp" else None
    map_view(cv, T, bx + (bw - vw) // 2, by + (bh - vh) // 2, vw, vh, 2, 0, 8, actors=ACTORS,
             sel=sel if tool == "pointer" else None, ghost=ghost)


def b_world(T, tool="pointer"):
    cv, x, y, w, h = frame_b(T, "world")
    cw = w - 340 - G
    world_scene(cv, T, x, y, cw, h, tool)
    run_panel(cv, T, x + cw + G, y, 340, RUN_H, still=True)
    if tool == "pointer":
        actor_pane(cv, T, x + cw + G, y + RUN_H + G, 340, h - RUN_H - G)
    else:
        stamps_pane(cv, T, x + cw + G, y + RUN_H + G, 340, h - RUN_H - G)
    return cv


def b_sound(T):
    cv, x, y, w, h = frame_b(T, "sound", playing=True)
    lw = 200
    sounds_list(cv, T, x, y, lw, h, sel=2, playing=2)
    sw = w - lw - 340 - 2 * G
    sound_panel(cv, T, x + lw + G, y, sw, h, n=2, cursor=4)
    run_panel(cv, T, x + lw + sw + 2 * G, y, 340, RUN_H)
    song_panel(cv, T, x + lw + sw + 2 * G, y + RUN_H + G, 340, h - RUN_H - G)
    return cv


# ---- 800x480: one tabbed right pane ---------------------------------------
def b800(T, which):
    W, H = 800, 480
    cur = {"config": "cards"}.get(which, which)
    cv, x, y, w, h = frame_b(T, cur, W=W, H=H, playing=which in ("code", "sound", "config"))
    lw = w - 340 - G
    rx = x + lw + G
    if which == "config":
        cw = 300
        cards_panel(cv, T, x, y, cw, h)
        run_panel(cv, T, x + cw + G, y, w - cw - G, h, header=False)
    elif which == "code":
        code_panel(cv, T, x, y, lw, h, first=176, cur=189, col=12, hints=False)
        bx, by, bw, bh = run_panel(cv, T, rx, y, 340, h, tabs=(("screen", "Game"), ("sprite", "Sprites"),
                                                               ("music", "Sounds"), ("map", "Map"),
                                                               ("log", "Log")))
        keyhints(cv, T, (("F6", "Next pane"), (("Ctrl", "Shift", "enter"), "Grow")), by + bh - 12, bx + 12)
    elif which == "art":
        sw = 306
        sprite_panel(cv, T, x, y, sw, h, t=12, big=False)
        mx, mw = x + sw + G, w - sw - G
        bx, by, bw, bh = pane(cv, T, mx, y, mw, h, tabs=(("map", "Map"), ("screen", "Game")),
                              tools=map_tools(2, brush=False))
        map_view(cv, T, bx + 6, by + 4, 464, 204, 2, 72, 120, cursor=(26, 22), live=False)
        sy = by + 4 + 204 + 6
        sheet_view(cv, T, bx + 6, sy, SKY, 2, 0, 8, sel=12)
        page_chips(cv, T, bx + 6 + 256 + 10, sy, 2, 0)
        fm = fx(T, 13, "med")
        tx = bx + 6 + 256 + 54
        for i, (g, s) in enumerate((("brush", "Brush 1×1"), ("search", "Zoom ‹ 2× ›"))):
            ic(cv, T, g, tx, sy + 4 + i * 34, 18, T["dim"])
            cv.text(tx + 26, sy + 18 + i * 34, s, fm, T["text"])
        cv.text(tx, sy + 86, "Cell 26, 22", fx(T, 12), T["dim"])
        cv.text(tx, sy + 104, "tile 12", fx(T, 12), T["dim"])
    elif which == "world":
        bx, by, bw, bh = pane(cv, T, x, y, lw, h, title="Scene", slot="scene", focus=True,
                              tools=scene_tools(2, full=False))
        map_view(cv, T, bx + 7, by + 4, bw - 14, bh - 8, 2, 120, 64, actors=ACTORS, sel=17)
        actor_pane(cv, T, rx, y, 340, h, compact=True)
    elif which == "sound":
        sound_panel(cv, T, x, y, lw, h, n=2, cursor=4, big=False, dropdown=True)
        bx, by, bw, bh = run_panel(cv, T, rx, y, 340, h, tabs=(("screen", "Game"), ("music", "Song")))
        keyhints(cv, T, (("F6", "Next pane"),), by + bh - 12, bx + 12)
    return cv


# ==========================================================================
# the handheld: one tab at a time, one bar
# ==========================================================================
LADDER = (("cards", "sliders", "Config"), ("blocks", "blocks", "Blocks"), ("code", "code", "Code"),
          ("paint", "sprite", "Sprites"), ("map", "map", "Map"), ("scene", "scene", "Scene"),
          ("music", "music", "Music"))


def hh_editor_bar(cv, T, cur):
    """One bar for every tab: Moy, the seven tabs in 18 px cells (the open
    one on the current plate with its name), a hairline, undo, redo, Play;
    the battery and the clock. Esc steps back."""
    h = 26
    cv.rect(0, 0, 320, h, T["bg"])
    cv.rect(0, h - 1, 320, 1, T["line"])
    sys_fill, body, eye = T["hhbar"]["sys"] or T["bar"]["sys"] or (T["raised"], T["value"], T["raised"])
    cv.rrect(4, 3, 22, 20, 7, sys_fill)
    M.moy(cv, 6, 4, 18, body, eye)
    ink, dim = T["text"], T["dim"]
    lf = fx(T, 11, "semi", 2)
    x = 28
    s = 16
    for tab, slot, label in LADDER:
        if tab == cur:
            tw = s + 3 + lf.width(label) + 6
            M.current_mark(cv, T, x, 3, tw, 20, 5)
            ic(cv, T, slot, x + 3, 5, s, ink)
            cv.text(x + 3 + s + 3, M.mid_base(lf, 3, 20), label, lf, ink)
            x += tw + 1
        else:
            ic(cv, T, slot, x + 1, 5, s, ink)
            x += 18
    # undo, redo and Play anchor to the battery, so no label moves them
    fs = fx(T, 12, "med", 2)
    w = cv.text(316, M.mid_base(fs, 0, h), "14:32", fs, ink, align="right")
    bx = 316 - w - 19
    ic(cv, T, "battery", bx, 5, 16, ink)
    ax = bx - 2 - 3 * 18
    ic(cv, T, "undo", ax + 1, 5, s, dim)
    ic(cv, T, "redo", ax + 19, 5, s, mix(dim, T["bg"], 0.55))
    ic(cv, T, "play", ax + 37, 5, s, T["value"])
    cv.rect(ax - 2, 7, 1, 12, T["line"])
    return h


def hh_canvas(T, cur, body=None):
    cv = M.new_cv(T, False, body or T["surface"])
    return cv, hh_editor_bar(cv, T, cur)


def hh_hints(cv, T, items, bg=None):
    R11.hints(cv, T, items, 233, bg=bg or T["surface"])


def hh_code(T):
    cv, top = hh_canvas(T, "code", T["bg"])
    R7.draw_code(cv, T, 0, top, 320, 240 - top - 38, False, first=34)
    R8.status_and_keys(cv, T, 240 - 37, 320, False)
    return cv


def hh_sprites(T, more=False):
    """Step 24's arrangement as built, under the one bar; `more` opens the
    more menu, which gains the sprite's flags."""
    cv, top = hh_canvas(T, "paint")
    sheet = R7.SHEET
    fm = fx(T, 12, "med", 2)
    cv.text(6, top + 14, "Sprite 0", fm, T["dim"])
    gx, gy, cell = 6, top + 20, 18
    sprite_grid(cv, T, gx, gy, cell, sheet, 0, live=not more)
    palette(cv, T, gx + 8 * cell + 8, gy, 11)
    rx = 184
    sw_ = stepper(cv, T, rx, gy, 18, "0", fm, w=26)
    cv.text(rx + sw_ + 6, M.mid_base(fm, gy, 18), "Size 1×1", fm, T["dim"])
    sheet_view(cv, T, rx, gy + 24, sheet, 1, 0, 16, sel=0)
    ty = gy + 8 * cell + 8
    tool_row(cv, T, gx - 2, ty - 4, 24, px=16, slots=TOOLS[:5] + ("more",), off=("stamp",),
             cur=5 if more else 0)
    for i, g in enumerate(("folder_open", "save", "export")):
        small_button(cv, T, rx + i * 26, ty - 2, 22, 20, g, None)
    if not more:
        hh_hints(cv, T, ((("F", "V"), "Flip"), ("R", "Rotate"), ("M", "More")))
        return cv
    # the more menu, a popover from ⋯: the operations, the flags, Clear
    mx, my, mw = 102, 30, 212
    rows = (("copy", "Copy", "C"), ("flip", "Flip across", "F"), ("flip_v", "Flip over", "V"),
            ("rotate", "Rotate", "R"), ("forward", "Shift", "›"), ("mirror", "Mirror", ""),
            ("flag", "Flags", None), ("del", "Clear", "Del"))
    rh = 20
    mh = len(rows) * rh + 8
    M.soft(cv, T, mx, my, mw, mh, 8, 0.8)
    cv.rrect(mx, my, mw, mh, 8, T["raised"] if T["mode"] == "dark" else T["surface"])
    cv.ring(mx, my, mw, mh, 8, 1, T["line"])
    fk = fx(T, 11, "semi", 2)
    for i, (g, label, key) in enumerate(rows):
        ry = my + 4 + i * rh
        ink = danger(T) if g == "del" else T["text"]
        if g == "flag":
            ink = focus_plate(cv, T, mx + 2, ry, mw - 4, rh, 0)
        if g == "del":
            cv.rect(mx + 8, ry - 1, mw - 16, 1, T["line"])
        ic(cv, T, g, mx + 8, ry + 2, 16, ink if g == "del" else T["dim"])
        cv.text(mx + 30, M.mid_base(fm, ry, rh), label, fm, ink)
        if key is None:
            flag_row(cv, T, mx + 80, ry - 1, 16, on=(0, 3))
        elif key:
            cv.text(mx + mw - 10, M.mid_base(fk, ry, rh), key, fk, T["dim"], align="right")
    hh_hints(cv, T, ((("←", "→"), "Flag"), ("enter", "Set"), ("Esc", "Close")))
    return cv


def hh_config(T):
    cv, top = hh_canvas(T, "cards", T["bg"])
    fl, fv, fc = fx(T, 13, "med", 2), fx(T, 12, "med", 2), fx(T, 12, "semi", 2)
    y = top + 6
    cv.rrect(6, y, 308, 106, 8, T["surface"])
    if T["mode"] == "dark":
        cv.ring(6, y, 308, 106, 8, 1, T["line"])
    cv.set_clip(6, y, 308, 38)
    focus_plate(cv, T, 6, y, 308, 46, 8)
    cv.clip = None
    cv.text(16, y + 16, "Run at", fl, T["text"])
    cv.text(304, y + 16, "130", fl, T["text"], align="right")
    tx0, tx1 = 16, 304
    cv.rrect(tx0, y + 26, tx1 - tx0, 3, 1.5, mix(T["dim"], T["surface"], 0.6))
    f = (130 - 60) / (220 - 60)
    cv.rrect(tx0, y + 26, int((tx1 - tx0) * f), 3, 1.5, T["value"])
    kx = tx0 + int((tx1 - tx0) * f)
    cv.shape(kx - 6, y + 21, 12, 12, lambda d, s: d.ellipse((0, 0, 12 * s - 1, 12 * s - 1), fill=255),
             T["value"])
    cv.rect(16, y + 38, 288, 1, T["line"])
    cv.text(16, M.mid_base(fl, y + 38, 42), "Hero", fl, T["text"])
    for i, t in enumerate((0, 3)):
        hx = 228 + i * 40
        if i == 0:
            M.current_mark(cv, T, hx, y + 42, 34, 34, 6)
        else:
            cv.rrect(hx, y + 42, 34, 34, 6, T["bg"])
        put(cv, span(SKY, t, 1, 2, 2), hx + 9, y + 43)
    cv.rect(16, y + 80, 288, 1, T["line"])
    cv.text(16, M.mid_base(fl, y + 80, 26), "Autorun", fl, T["text"])
    w = 30
    cv.rrect(304 - w, y + 85, w, 16, 8, mix(T["dim"], T["surface"], 0.6))
    cv.shape(304 - w + 2, y + 87, 12, 12, lambda d, s: d.ellipse((0, 0, 12 * s - 1, 12 * s - 1), fill=255),
             "#ffffff" if T["mode"] == "light" else T["text"])
    y += 112
    cv.text(12, y + 14, "This project", fc, T["dim"])
    R11.rows(cv, T, 6, y + 19, 308, 22, (("Cart info", "link", "Sky Run"), ("Files", "link", "6")),
             None, fl, fv, False)
    hh_hints(cv, T, ((("←", "→"), "Change"), (("↑", "↓"), "Card")), bg=T["bg"])
    return cv


def hh_blocks(T):
    cv, top = hh_canvas(T, "blocks")
    fl = fx(T, 12, "med", 2)
    fs = fx(T, 12, "semi", 2)
    x = 8
    for i, label in enumerate(("coin", "Stage", "player")):
        tw = fs.width(label) + 16
        c = T["dim"]
        if i == 0:
            c = M.current_mark(cv, T, x, top + 5, tw, 18, 5) or T["text"]
        cv.text(x + 8, M.mid_base(fs, top + 5, 18), label, fs, c)
        x += tw + 4
    obj = [o for o in BLOCKS["objects"] if o["tag"] == "coin"][0]
    yy = top + 30
    rh = 21
    i = 0
    for ev in obj["scripts"][1:2]:
        label, _ = _label(ev)
        cv.rrect(8, yy, fl.width(label) + 20, rh, 6, CAT["events"][1])
        cv.text(18, M.mid_base(fl, yy, rh), label, fl, "#1a1530")
        yy += rh + 3

        def stmt(b, depth):
            nonlocal yy, i
            lb, cat = _label(b)
            col = CAT.get(cat, ("", "#888"))[1]
            bx = 8 + depth * 14
            bw = min(312 - bx, fl.width(lb) + 22)
            if i == 1:
                focus_plate(cv, T, bx - 4, yy - 1, bw + 8, rh, 5)
            cv.rrect(bx, yy, bw, rh - 2, 5, col, tint(T, col))
            cv.rrect(bx, yy, 3, rh - 2, 1.5, col)
            cv.text(bx + 9, M.mid_base(fl, yy, rh - 2), lb, fl, T["text"])
            i += 1
            yy += rh
            for c in b.get("c", ()):
                stmt(c, depth + 1)
        for c in ev["c"]:
            stmt(c, 1)
    add_pill(cv, T, 22, yy + 2, fl, h=20)
    ty = 240 - 30
    cv.rect(0, ty - 4, 320, 1, T["line"])
    xx = 6
    for slot in ("new", "del", "arrow_up", "arrow_down", "more"):
        ic(cv, T, slot, xx + 4, ty + 3, 16, danger(T) if slot == "del" else T["dim"])
        xx += 26
    fs2 = fx(T, 12, "semi", 2)
    lw = fs2.width("See code") + 34
    st = M.bstyle(T, "editor", "normal")
    cv.rrect(320 - lw - 6, ty, lw, 22, 6, st["fill"])
    ic(cv, T, "code", 320 - lw + 2, ty + 3, 16, T["text"])
    cv.text(320 - lw + 22, M.mid_base(fs2, ty, 22), "See code", fs2, T["text"])
    return cv


def hh_chip(cv, T, x, y, glyph, label, fm, on=False):
    w = fm.width(label) + 30
    if on:
        M.current_mark(cv, T, x, y, w, 20, 6)
    else:
        cv.rrect(x, y, w, 20, 6, M.bstyle(T, "editor", "normal")["fill"])
    ic(cv, T, glyph, x + 5, y + 2, 16, T["dim"])
    cv.text(x + 24, M.mid_base(fm, y, 20), label, fm, T["text"])
    return w


def hh_map(T):
    cv, top = hh_canvas(T, "map")
    vw, vh = 216, 240 - top - 28
    map_view(cv, T, 0, top, vw, vh, 1, 40, 240 - vh, cursor=(24, 22))
    px = vw + 6
    cs = 22
    for i in range(16):
        t = 8 + i
        tx, ty = px + (i % 4) * cs, top + 6 + (i // 4) * cs
        R7.checker(cv, tx, ty, cs - 2, cs - 2, 4)
        put(cv, span(SKY, t, 1, 1, 2), tx + 2, ty + 2)
        if t == 12:
            two_tone(cv, T, tx, ty, cs - 2, cs - 2)
    fm = fx(T, 12, "med", 2)
    py = top + 6 + 4 * cs + 4
    ic(cv, T, "back", px, py, 16, T["dim"])
    cv.text(px + 44, M.mid_base(fm, py, 16), "1 / 4", fm, T["text"], align="center")
    ic(cv, T, "forward", px + 72, py, 16, T["dim"])
    cv.text(px, py + 36, "Tile 12", fm, T["text"])
    cv.text(px, py + 52, "Cell 24, 22", fm, T["dim"])
    cv.text(px, py + 68, "Hold: pick", fm, T["dim"])
    ty = 240 - 26
    cv.rect(0, ty - 1, 320, 1, T["line"])
    tool_row(cv, T, 2, ty + 1, 24, cur=0, px=16, slots=("pencil", "rect", "bucket", "select", "eraser"),
             off=())
    x = 2 + 5 * 24 + 4
    cv.rect(x, ty + 6, 1, 14, T["line"])
    x += 5
    x += hh_chip(cv, T, x, ty + 3, "brush", "1×1", fm) + 4
    hh_chip(cv, T, x, ty + 3, "search", "1×", fm)
    ic(cv, T, "resize", 320 - 22, ty + 5, 16, T["dim"])
    return cv


def hh_scene(T, stamp=False):
    cv, top = hh_canvas(T, "scene")
    vw, vh = 216, 240 - top - 28
    map_view(cv, T, 0, top, vw, vh, 1, 0, 240 - vh, actors=ACTORS, sel=None if stamp else 16, grid=False,
             ghost=(152, 150, 6) if stamp else None)
    px = vw + 6
    fm, fs = fx(T, 12, "med", 2), fx(T, 12, "semi", 2)
    if stamp:
        # the stamp palette: the sheet's tiles, paged, the stamp current and its tag
        cs = 22
        for i in range(16):
            t = i
            tx, ty = px + (i % 4) * cs, top + 6 + (i // 4) * cs
            R7.checker(cv, tx, ty, cs - 2, cs - 2, 4)
            put(cv, span(SKY, t, 1, 1, 2), tx + 2, ty + 2)
            if t == 6:
                two_tone(cv, T, tx, ty, cs - 2, cs - 2)
        py = top + 6 + 4 * cs + 4
        ic(cv, T, "back", px, py, 16, T["dim"])
        cv.text(px + 44, M.mid_base(fm, py, 16), "1 / 4", fm, T["text"], align="center")
        ic(cv, T, "forward", px + 72, py, 16, T["dim"])
        cv.text(px, py + 34, "Tag", fm, T["dim"])
        field(cv, T, px + 26, py + 22, 66, 18, "coin", fm)
        cv.text(px, py + 60, "Tap to place", fm, T["dim"])
    else:
        a = ACTORS[16]
        R7.checker(cv, px + 68, top + 6, 24, 24, 4)
        put(cv, span(SKY, 6, 1, 1, 3), px + 68, top + 6)
        cv.text(px, top + 16, "coin", fs, T["text"])
        cv.text(px, top + 44, f"x {a['x']}  y {a['y']}", fm, T["dim"])
        cv.rect(px, top + 52, 92, 1, T["line"])
        yy = top + 58
        for slot, label in (("notes", "Tag"), ("flip", "Flip"), ("arrow_up", "Front"),
                            ("arrow_down", "Back"), ("del", "Remove")):
            c = danger(T) if slot == "del" else T["text"]
            ic(cv, T, slot, px + 2, yy + 2, 16, c if slot == "del" else T["dim"])
            cv.text(px + 24, M.mid_base(fm, yy, 20), label, fm, c)
            yy += 22
    ty = 240 - 26
    cv.rect(0, ty - 1, 320, 1, T["line"])
    tool_row(cv, T, 2, ty + 1, 24, cur=1 if stamp else 0, px=16, slots=("pointer", "stamp"), off=())
    x = 2 + 2 * 24 + 4
    cv.rect(x, ty + 6, 1, 14, T["line"])
    x += 5
    x += hh_chip(cv, T, x, ty + 3, "grid", "Snap", fm, on=True) + 4
    hh_chip(cv, T, x, ty + 3, "search", "1×", fm)
    cv.text(316, M.mid_base(fm, ty + 3, 20), "main", fm, T["dim"], align="right")
    return cv


def hh_music(T):
    cv, top = hh_canvas(T, "music")
    s = BANK[2]
    fm, fs = fx(T, 12, "med", 2), fx(T, 12, "semi", 2)
    f = mono(False)
    x = 6
    for i, label in enumerate(("Sound", "Song")):
        tw = fs.width(label) + 16
        c = T["dim"]
        if i == 0:
            c = M.current_mark(cv, T, x, top + 4, tw, 18, 5) or T["text"]
        cv.text(x + 8, M.mid_base(fs, top + 4, 18), label, fs, c)
        x += tw + 2
    ic(cv, T, "back", x + 8, top + 5, 16, T["dim"])
    cv.text(x + 44, M.mid_base(fs, top + 4, 18), "2 tune", fs, T["text"], align="center")
    ic(cv, T, "forward", x + 64, top + 5, 16, T["dim"])
    cv.text(314, M.mid_base(fm, top + 4, 18), "Speed 16", fm, T["dim"], align="right")
    ly = top + 26
    rh = 17
    for i in range(9):
        nn, wv, v = s["steps"][i + 1]
        ry = ly + i * rh
        if i + 1 == 4:
            M.current_mark(cv, T, 4, ry, 176, rh, 4)
            cv.rect(4, ry, 2, rh, T["value"])
        b = M.mid_base(f, ry, rh)
        cv.text(26, b, f"{i + 1:02d}", f, T["text"] if i + 1 == 4 else syn(T)["gutter"], align="right")
        cv.text(34, b, note_name(nn) if v else "··", f, T["text"])
        cv.text(66, b, WAVES[wv], f, T["dim"])
        cv.text(98, b, str(v), f, T["text"])
        for k in range(7):
            cv.rect(112 + k * 9, ry + 5, 7, rh - 10, T["value"] if k < v else mix(T["line"], T["surface"], 0.2))
    px, py = 188, ly
    pads = (("arrow_down", "Note"), ("arrow_up", "Note"), ("loop", "Wave"), ("volume", "Vol"),
            ("mute", "Rest"), ("new", "Add"), ("dup", "Dup"), ("del", "Del"), ("copy", "Copy"),
            ("paste", "Paste"))
    st = M.bstyle(T, "editor", "normal")
    for i, (slot, label) in enumerate(pads):
        bx, by = px + (i % 2) * 64, py + (i // 2) * 28
        cv.rrect(bx, by, 60, 24, 6, st["fill"])
        c = danger(T) if slot == "del" else T["text"]
        ic(cv, T, slot, bx + 5, by + 4, 16, c if slot == "del" else T["dim"])
        cv.text(bx + 25, M.mid_base(fm, by, 24), label, fm, c)
    ty = 240 - 26
    cv.rect(0, ty - 1, 320, 1, T["line"])
    xx = 8
    for slot, label, on in (("volume", "Hear", False), ("loop", "Loop", True)):
        w = fm.width(label) + 34
        if on:
            M.current_mark(cv, T, xx, ty + 3, w, 20, 6)
        else:
            cv.rrect(xx, ty + 3, w, 20, 6, st["fill"])
        ic(cv, T, slot, xx + 6, ty + 5, 16, T["dim"])
        cv.text(xx + 26, M.mid_base(fm, ty + 3, 20), label, fm, T["text"])
        xx += w + 6
    cv.text(314, M.mid_base(fm, ty + 3, 20), "16 steps", fm, T["dim"], align="right")
    return cv


# ==========================================================================
# the scenes
# ==========================================================================
# Concepts A and C, not chosen (EDITOR_LAYOUT.md): their pictures stay as the
# first revision drew them (fb049301) and are indexed as they are.
KEPT = [
    ("moy-light_desktop_editor-a-%s" % k, "Desk Editor, concept A (Project, not chosen): %s" % what)
    for k, what in (("code", "coding"), ("art", "the map with the brush's sprite beside it"),
                    ("world", "building a scene"), ("sound", "making music"))
] + [
    ("moy-light_desktop_editor-c-%s" % k, "Desk Editor, concept C (Splits, not chosen): %s" % what)
    for k, what in (("code", "Code beside the game, the map split under it"),
                    ("art", "Sprites beside the Map, a tab dragged over the map: its drop zones"),
                    ("world", "the Scene, the actor and the stamps, split by hand"),
                    ("sound", "the Scene and the game over a sound's steps"))
]
REDRAWN = ("moy-light_handheld_code-editor", "moy-dark_handheld_code-editor",
           "moy-light_handheld_sprite-editor", "moy-dark_handheld_sprite-editor")


def scenes():
    src = R11.scenes()
    M.INDEX[:] = [ln for ln in M.INDEX if not ln.split(".png")[0].split("@3x")[0] in REDRAWN]
    _SHIPPED_ON[0] = True
    try:
        _scenes()
    finally:
        _SHIPPED_ON[0] = False
    return src


def _scenes():
    for T, tag in ((LIGHT, "moy-light"), (DARK, "moy-dark")):
        n = T["name"]
        for name, fn, what in (
                ("config", hh_config, "Config tab (Make it mine) on Sky Run: the cards, focus on Run at"),
                ("blocks", hh_blocks, "Blocks tab on Coin Quest's coin: the object picker, the outline, "
                                      "add block, the tools and the door to Code"),
                ("code", hh_code, "Code tab on Star Catcher under the one bar"),
                ("sprite", hh_sprites, "Sprites tab on Star Catcher's frog, step 24's arrangement under "
                                       "the one bar"),
                ("map", hh_map, "Map tab on Sky Run: the map at 1x, the brush palette, the tools"),
                ("scene", hh_scene, "Scene tab on Sky Run: a coin selected and its props"),
                ("music", hh_music, "Music tab on Sky Run: a sound's steps, the pad, Hear")):
            fn(T).save(f"{tag}_handheld_{name}-editor", f"{what}; {n}", x3=True)
        hh_sprites(T, more=True).save(f"{tag}_handheld_sprite-editor_more",
                                      f"Sprites tab with its more menu open: the operations, the "
                                      f"sprite's flags, Clear; {n}", x3=True)
        hh_scene(T, stamp=True).save(f"{tag}_handheld_scene-editor_stamp",
                                     f"Scene tab with the stamp tool: the stamp palette and its tag; "
                                     f"{n}", x3=True)
    for T, tag in ((LIGHT, "moy-light"), (DARK, "moy-dark")):
        n = T["name"]
        for name, fn, what in (
                ("config", b_config, "Make it mine: Settings beside the game at 2x"),
                ("code", b_code, "Code: the signature popover over spr(, the game with a dot on "
                                 "Restart (an edit waits), the reference"),
                ("blocks", b_blocks, "Code on a blocks cart (Coin Quest): the palette, the coin's "
                                     "blocks, the scene with its Game tab, the code they make"),
                ("art", b_art, "Art: the sprite with its flags, the map with its Game tab, the whole "
                               "sheet"),
                ("world", b_world, "World: the scene at 2x over the map, the game, the selected actor"),
                ("world-stamp", lambda T: b_world(T, "stamp"),
                 "World with the stamp tool: the stamps and their tag"),
                ("sound", b_sound, "Sound: the sounds, a sound's two octaves, the game, the song")):
            fn(T).save(f"{tag}_desktop_editor-b-{name}", f"Desk Editor, concept B (Workspaces): {what}; {n}")
    for which in ("config", "code", "art", "world", "sound"):
        b800(LIGHT, which).save(f"moy-light_desktop_editor-b-{which}-800x480",
                                f"Concept B on an 800x480 desk: {which}, one tabbed pane at the right; "
                                f"Moy Day")
    for name, what in KEPT:
        M.INDEX.append(f"{name}.png — {what}; Moy Day; 1024x600")
