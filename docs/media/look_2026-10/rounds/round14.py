#!/usr/bin/env python3
"""Round 14 of the 2026-10 look, layered over round 12: what MAXIMISE
means, drawn three ways for review (`MAXIMISE_VARIANTS.md`).

- E-05, tools in the title strip: round 12's Concept B as drawn
  (`EDITOR_LAYOUT.md`), re-rendered under these names to sit beside the
  others.
- V1, the bar takes the tools: a maximised window has no strip; the shell
  bar carries Moy, the app's chip (its window menu), the app's tools, a
  bare stretch and the status.
- V2, full screen: no bar and no chrome; the app's row is the top 40 px,
  the bar's height, so the bar, pulled down over it, covers it exactly.

Each draws the Editor's Art and Code workspaces at 1024x600 Day, Art at
800x480, Code in Night and Files maximised; V1 adds its window menu, V2
the bar revealed by the pointer and by a swipe. The panes take back what
each variant frees, every zoom whole; `editor_maximise_compare` stacks the
three Art pictures.

The resizable panes, on E-05's frame: dividers that move to any pixel,
each panel at the largest whole zoom that fits and the rest its own
ground, a divider stopping at its neighbour's minimum
(`docs/studio_2026-09.md` §4.2): Art and Code with their dividers moved,
Code mid-drag at Run's minimum, the Map at its T-Deck minimum, and Art at
800x480 mid-drag at Sprite's minimum.

A layer of the target, not an entry point: `../draw.py` draws every picture.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import round12 as R12  # noqa: E402

M, R7, R10, R11 = R12.M, R12.R7, R12.R10, R12.R11
LIGHT, DARK = R12.LIGHT, R12.DARK
mix, fx, ic, pane = M.mix, R12.fx, R12.ic, R12.pane
PH, G, STRIP, TITLE_W, PLAY_W, RUN_H = R12.PH, R12.G, R12.STRIP, R12.TITLE_W, R12.PLAY_W, R12.RUN_H
SKY = R12.SKY

BAR = 40         # the shell bar
ROW = 40         # V2's row: the bar's height, so the bar pulled over it covers it exactly
BAND = 32        # a tool's height on the bar and the row: the desk's touch floor
BARE = 120       # the bare stretch V1's bar keeps to drag and double-tap
CELL = 32        # a glyph tool's cell on the bar
BAR_PLAY = 64    # V1's Play slot: two bare glyph cells (Play, or Restart and Stop)

R12.NEW_GLYPHS.update({
    # four corners pulled in: leave full screen
    "fullscreen_exit": f'<path d="M9 4.5V9H4.5M15 4.5V9h4.5M9 19.5V15H4.5M15 19.5V15h4.5" {R12._S}/>',
})

# Files with more files than any variant shows, so the list's reach shows.
MORE_FILES = tuple(sorted(M.FILES + (("jump_lab", "Game", "10 KB"), ("my_song", "Music", "5 KB"),
                                     ("pond", "Art", "8 KB"), ("rocket_run", "Game", "15 KB"),
                                     ("snake", "Game", "6 KB"))))


# ==========================================================================
# the shell bar's parts, and the app's row on a bar or a strip
# ==========================================================================
def bar_ground(cv, T, y=0):
    b = T["bar"]
    cv.rect(0, y, cv.W, BAR, b["fill"] or T["surface"], b["alpha"])
    if b["line"]:
        cv.rect(0, y + BAR - b["line"][0], cv.W, b["line"][0], b["line"][1])
    return b["fill"] or T["surface"]


def moy_button(cv, T, y=0, state=None):
    sys_fill, body, _eye = T["bar"]["sys"] or (T["raised"], T["value"], T["raised"])
    cv.rrect(6, y + 3, 36, 34, 9, R10.open_plate(T, sys_fill) if state == "open" else sys_fill)
    M.moy(cv, 8, y + 4, 32, body, sys_fill)


def status(cv, T, y=0):
    """Wi-Fi, battery and the clock at the bar's right; returns their left edge."""
    W = cv.W
    tw = cv.text(W - 14, y + 26, "14:32", M.F(T, "status", M.DK), T["text"], align="right")
    ic(cv, T, "battery", W - 14 - tw - 30, y + 9, 22, T["text"])
    ic(cv, T, "wifi", W - 14 - tw - 58, y + 8, 21, T["text"])
    return W - 14 - tw - 58


def ws_pills(cv, T, cur, x, y, h, xmax, pad=8):
    """The five workspaces from x: labelled where they fit before xmax,
    else icons with the current one labelled (EDITOR_LAYOUT.md's rule)."""
    f = fx(T, 15, "med")
    full = sum(18 + 6 + f.width(lb) + 2 * pad + 2 for _k, _s, lb in R12.WORKSPACES)
    fits = x + full <= xmax
    for key, slot, label in R12.WORKSPACES:
        on = key == cur
        x += R12.pill(cv, T, x, y, h, slot, label if (fits or on) else None, on, f, pad=pad) + 2
    return x


def play_slot(cv, T, px, y, h, playing, waiting, labelled, ground):
    """Play, or Restart and Stop, in `value` from px; labelled on a strip or
    a row, bare glyphs in 36 px cells on the bar. A dot on Restart while an
    edit waits for the next Play."""
    fp = fx(T, 15, "semi")
    gy = y + (h - 18) // 2
    glyphs = (("rotate", "Restart"), ("stop", "Stop")) if playing else (("play", "Play"),)
    x = px + 4 if labelled else px + (CELL - 18) // 2
    for g, label in glyphs:
        ic(cv, T, g, x, gy, 18, T["value"])
        if g == "rotate" and waiting:
            cv.shape(x + 13, gy - 4, 8, 8, lambda d, s_: d.ellipse((0, 0, 8 * s_ - 1, 8 * s_ - 1),
                                                                   fill=255), T["value"])
            cv.ring(x + 12, gy - 5, 10, 10, 5, 1.5, ground)
        if labelled:
            x += 24 + cv.text(x + 24, M.mid_base(fp, y, h), label, fp, T["value"]) + 16
        else:
            x += CELL


def editor_tools(cur, playing=False, waiting=False, labelled=True):
    """The Editor's row in [x, xr): the workspaces, then undo and redo at a
    place fixed by xr, a hairline, the Play slot ending at xr."""
    def draw(cv, T, x, y, h, xr, ground):
        px = xr - (PLAY_W if labelled else BAR_PLAY)
        play_slot(cv, T, px, y, h, playing, waiting, labelled, ground)
        cv.rect(px - 6, y + 6, 1, h - 12, T["line"])
        ux = px - 10 - 2 * CELL
        ic(cv, T, "undo", ux + 6, y + (h - 20) // 2, 20, T["dim"])
        ic(cv, T, "redo", ux + CELL + 6, y + (h - 20) // 2, 20, mix(T["dim"], ground, 0.55))
        ws_pills(cv, T, cur, x, y, h, ux - 6, pad=8 if labelled else 6)
    return draw


def files_tools(cv, T, x, y, h, xr, ground):
    """Files' row: the current row's verbs (Open, the primary, in `value`;
    Play), and search at the row's right end."""
    f = fx(T, 15, "med")
    for slot, label, ink in (("folder_open", "Open", T["value"]), ("play", "Play", T["text"])):
        ic(cv, T, slot, x + 8, y + (h - 18) // 2, 18, ink)
        x += 8 + 18 + 6 + cv.text(x + 32, M.mid_base(f, y, h), label, f, ink) + 8 + 2
    ic(cv, T, "search", xr - CELL + 6, y + (h - 20) // 2, 20, T["dim"])


def app_chip(cv, T, slot, title, y=0, open_=False):
    """V1: the maximised app's chip after Moy, in the fixed title slot: its
    icon (at 1024), its name and ▾, on the current plate. It opens the
    window menu."""
    w = TITLE_W.get(cv.W, 144)
    x, cy = 52, y + (BAR - BAND) // 2
    cv.rrect(x, cy, w, BAND, 8, T["bar"]["chip_cur"] or T["raised"])
    if open_:
        cv.rrect(x, cy, w, BAND, 8, R10.open_plate(T, T["bar"]["chip_cur"] or T["raised"]))
    else:
        M.current_mark(cv, T, x, cy, w, BAND, 8)
    fc = M.F(T, "chip", M.DK)
    tx = x + 12
    if w >= 144:
        ic(cv, T, slot, x + 10, cy + 7, 18, T["text"])
        tx = x + 34
    cv.text(tx, M.mid_base(fc, cy, BAND), title, fc, T["text"], maxw=x + w - 26 - tx)
    ic(cv, T, "down", x + w - 22, cy + 9, 14, T["dim"])
    return x + w


def v1_bar(cv, T, slot, title, tools, open_=False):
    """V1: Moy; the app's chip; its tools, right-anchored a bare stretch
    before the status so undo never moves; the status."""
    ground = bar_ground(cv, T)
    moy_button(cv, T)
    sx = status(cv, T)
    x = app_chip(cv, T, slot, title, open_=open_) + 6
    tools(cv, T, x, (BAR - BAND) // 2, BAND, sx - BARE, ground)
    return BAR


def strip(cv, T, y, slot, title, tools, controls=("minimize", "maximize", "close"), h=STRIP):
    """A maximised window's strip (E-05), or V2's row at the top edge: the
    icon and the title in the fixed slot, the app's row, the controls."""
    W = cv.W
    cv.rect(0, y, W, h, T["surface"])
    cv.rect(0, y + h - 1, W, 1, T["line"])
    ft = M.F(T, "wtitle", M.DK)
    ic(cv, T, slot, 16, y + (h - 20) // 2, 20, T["text"])
    slot_w = TITLE_W.get(W, 144)
    cv.text(46, M.mid_base(ft, y, h), title, ft, T["text"], maxw=slot_w - 8)
    s = 20
    for i, g in enumerate(reversed(controls)):
        ic(cv, T, g, W - 14 - s - 34 * i, y + (h - s) // 2, s, T["text"])
    xr = W - 14 - s - 34 * (len(controls) - 1) - 12
    band = h - 8 if h == STRIP else BAND
    tools(cv, T, 46 + slot_w, y + (h - band) // 2, band, xr, T["surface"])
    return y + h


V2_CONTROLS = ("fullscreen_exit", "close")


def v2_row(cv, T, slot, title, tools):
    return strip(cv, T, 0, slot, title, tools, V2_CONTROLS, h=ROW)


# ==========================================================================
# the frames
# ==========================================================================
def frame(T, variant, cur, W=1024, H=600, playing=False, waiting=False, title="Sky Run"):
    """The canvas and the panes' box under V1's bar or V2's row."""
    cv = R12.new_canvas(T, W, H)
    tools = editor_tools(cur, playing, waiting, labelled=variant != "v1")
    top = v1_bar(cv, T, "code", title, tools) if variant == "v1" else v2_row(cv, T, "code", title, tools)
    R12.body_bg(cv, T, top)
    return cv, G, top + G, W - 2 * G, H - top - 2 * G


# ==========================================================================
# the workspaces, re-laid for the 44 px the strip gave back
# ==========================================================================
def preview(cv, T, x, y, t):
    """The sprite at 1x, 2x and 4x on the checker, as the game draws it."""
    fm = fx(T, 12, "med")
    for s in (1, 2, 4):
        n = 8 * s
        R7.checker(cv, x, y + 32 - n, n, n, 2)
        R12.put(cv, R12.span(SKY, t, 1, 1, s), x, y + 32 - n)
        x += n + 6 + cv.text(x + n + 6, y + 31, f"{s}×", fm, T["dim"]) + 16
    R12.caption(cv, T, x + 4, y + 12, "in the game")


def art(cv, T, x, y, w, h):
    """1024: the Sprite pane gains a preview row (1x, 2x, 4x) under its
    flags; the Map's view gains the rest (288 -> 332 at 2x, 18 -> 20¾ rows)."""
    extra = h - 504
    sw = 400
    bx, by, _bw, _bh = R12.sprite_panel(cv, T, x, y, sw, h, t=12)
    preview(cv, T, bx + 14, by + 46 + 288 + 10 + 36 + 6 + 36 + 12, 12)
    mx, mw = x + sw + G, w - sw - G
    mh = 336 + extra
    R12.map_panel(cv, T, mx, y, mw, mh, view_h=288 + extra, camy=min(96, 240 - (288 + extra) // 2))
    R12.sheet_both(cv, T, mx, y + mh + G, mw, h - mh - G, sel=12)


def sprite_panel_at(cv, T, x, y, w, h, t=12, cell=30):
    """The 800 Sprite pane with the canvas a whole step up: 8x8 at 30
    (16x16 at 15, 24x24 at 10), its palette and flags at 30."""
    bx, by, bw, bh = pane(cv, T, x, y, w, h, title=f"Sprite {t}", slot="sprite", focus=True,
                          tools=(("flip", None), ("rotate", None)))
    pad = 12
    R12.tool_row(cv, T, bx + pad - 2, by + 4, 36, px=18)
    gy = by + 36 + 10
    R12.sprite_grid(cv, T, bx + pad, gy, cell, SKY, t, live=True)
    px_ = bx + pad + 8 * cell + 8
    R12.palette(cv, T, px_, gy, cell)
    fr = fx(T, 14)
    yy = gy + 8 * cell + 10
    sw_ = R12.stepper(cv, T, bx + pad, yy, 32, str(t), fr)
    R12.small_button(cv, T, bx + pad + sw_ + 8, yy, fr.width("1×1") + 24, 32, None, "1×1", face=fr)
    R12.small_button(cv, T, px_, yy, 2 * cell, 32, None, "64", face=fr)
    fy = yy + 32 + 6
    R12.flag_row(cv, T, bx + pad, fy, cell, on=(0,))
    R12.caption(cv, T, px_ + 6, fy + cell // 2 - 6, "Flags", ink=T["dim"])


def art800(cv, T, x, y, w, h):
    """800: the Sprite pane widens 306 -> 330 and its canvas goes 8x8 at
    24 -> 30; the Map's view takes the height (204 -> 248 at 2x, 12¾ ->
    15½ rows) and gives 24 px of width (29 -> 27½ columns)."""
    sw = 330
    sprite_panel_at(cv, T, x, y, sw, h)
    mx, mw = x + sw + G, w - sw - G
    bx, by, bw, bh = pane(cv, T, mx, y, mw, h, tabs=(("map", "Map"), ("screen", "Game")),
                          tools=R12.map_tools(2, brush=False))
    vh = 204 + h - 384
    R12.map_view(cv, T, bx + 6, by + 4, bw - 12, vh, 2, 72, min(120, 240 - vh // 2), cursor=(26, 22),
                 live=False)
    sy = by + 4 + vh + 6
    R12.sheet_view(cv, T, bx + 6, sy, SKY, 2, 0, 8, sel=12)
    R12.page_chips(cv, T, bx + 6 + 256 + 10, sy, 2, 0)
    fm = fx(T, 13, "med")
    tx = bx + 6 + 256 + 54
    for i, (g, s) in enumerate((("brush", "Brush 1×1"), ("search", "Zoom ‹ 2× ›"))):
        ic(cv, T, g, tx, sy + 4 + i * 34, 18, T["dim"])
        cv.text(tx + 26, sy + 18 + i * 34, s, fm, T["text"])
    cv.text(tx, sy + 86, "Cell 26, 22", fx(T, 12), T["dim"])
    cv.text(tx, sy + 104, "tile 12", fx(T, 12), T["dim"])


def reference_tall(cv, T, x, y, w, h):
    """Code's Reference, 216 -> 260: the sheet page at 2x, then the pick
    line, what Enter puts at the caret."""
    bx, by, bw, bh = pane(cv, T, x, y, w, h, tabs=(("sprite", "Sprites"), ("music", "Sounds"),
                                                   ("map", "Map"), ("log", "Log")), grow=False)
    total = 128 + 14 + 34
    sx, sy = bx + (bw - 256 - 44) // 2, by + (bh - total) // 2
    R12.sheet_view(cv, T, sx, sy, SKY, 2, 0, 8, sel=6)
    R12.page_chips(cv, T, sx + 256 + 14, sy + 30, 2, 0)
    ly = sy + 128 + 14
    cv.rect(bx, ly, bw, 1, T["line"])
    f, S = R12.mono(), R12.syn(T)
    adv = f.glyph("m")[3]
    tx, base = bx + 12, ly + 22
    for s, c in (("spr(", S["fn"]), ("6", S["num"]), (", x, y)", S["text"])):
        cv.text(tx, base, s, f, c)
        tx += len(s) * adv
    cv.text(tx + 10, base, "6  coin", fx(T, 12), T["dim"])
    hw = R11.hints_width(T, (("enter", "Insert"),), True)
    R12.keyhints(cv, T, (("enter", "Insert"),), base + 2, bx + bw - 12 - hw)


def code(cv, T, x, y, w, h):
    """Code gains 3 lines (31 -> 34) and the Reference its pick line."""
    cw = w - 340 - G
    rows = (h - PH - 26 - 6) // 14
    R12.code_panel(cv, T, x, y, cw, h, first=len(R12.SKY_CODE) + 1 - rows, cur=189, col=12, sig=True)
    R12.run_panel(cv, T, x + cw + G, y, 340, RUN_H)
    reference_tall(cv, T, x + cw + G, y + RUN_H + G, 340, h - RUN_H - G)


def ws_art(T, variant):
    cv, x, y, w, h = frame(T, variant, "art")
    art(cv, T, x, y, w, h)
    return cv


def ws_code(T, variant):
    cv, x, y, w, h = frame(T, variant, "code", playing=True, waiting=True)
    code(cv, T, x, y, w, h)
    return cv


def ws_art800(T, variant):
    cv, x, y, w, h = frame(T, variant, "art", W=800, H=480)
    art800(cv, T, x, y, w, h)
    return cv


# ==========================================================================
# Files, an ordinary app, maximised
# ==========================================================================
def files_body(cv, T, x, y, w, h):
    """Files' places and list (mockups.files_window) with no frame, focused."""
    window, row, files = M.window, M.current_row, M.FILES

    def plain(cv_, T_, x_, y_, w_, h_, *_a):
        cv_.rect(x_, y_, w_, h_, T_["surface"])
        return y_

    M.window, M.FILES = plain, MORE_FILES
    M.current_row = lambda cv_, T_, x_, y_, w_, h_, r, _focus: row(cv_, T_, x_, y_, w_, h_, r, True)
    try:
        M.files_window(cv, T, x, y, w, h)
    finally:
        M.window, M.current_row, M.FILES = window, row, files


def files(T, variant):
    cv = R12.new_canvas(T, 1024, 600)
    if variant == "e05":
        R12.desk_bar(cv, T, (("folder", "Files"), ("code", "Editor"), ("calc", "Calc")))
        top = strip(cv, T, BAR, "folder", "Files", files_tools)
    elif variant == "v1":
        top = v1_bar(cv, T, "folder", "Files", files_tools)
    else:
        top = v2_row(cv, T, "folder", "Files", files_tools)
    files_body(cv, T, 0, top, 1024, 600 - top)
    return cv


# ==========================================================================
# V1's window menu; V2's bar, revealed
# ==========================================================================
WINDOW_MENU = (("maximize", "Restore", "Super ↓"), ("minimize", "Minimise", "Super M"),
               ("close", "Close", "Super Q"), ("-", None, None), ("header", "Windows", None),
               ("folder", "Files", None), ("calc", "Calc", None), ("-", None, None),
               ("home", "Show the desk", "Super D"))


def menu(cv, T, x, y, w, rows):
    """A popover of 34 px rows hung from its control, the first focused."""
    rh = 34
    h = 10 + sum(9 if s == "-" else rh for s, _l, _k in rows)
    M.soft(cv, T, x, y, w, h, 10, 0.8)
    cv.rrect(x, y, w, h, 10, T["surface"])
    if T["mode"] == "dark":
        cv.ring(x, y, w, h, 10, 1, T["line"])
    fr, fc, fk = M.F(T, "row", M.DK), M.F(T, "col", M.DK), fx(T, 13, "med")
    yy, first = y + 5, True
    for slot, label, keys in rows:
        if slot == "-":
            cv.rect(x + 12, yy + 4, w - 24, 1, T["line"])
            yy += 9
            continue
        if slot == "header":
            cv.text(x + 12, M.mid_base(fc, yy, rh), label, fc, T["dim"])
        else:
            ink = M.focus_mark(cv, T, (x + 9, yy + 2, w - 18, rh - 4, 5), "part") if first else None
            first = False
            ic(cv, T, slot, x + 12, yy + (rh - 18) // 2, 18, ink or T["dim"])
            cv.text(x + 38, M.mid_base(fr, yy, rh), label, fr, ink or T["text"])
            if keys:
                cv.text(x + w - 14, M.mid_base(fk, yy, rh), keys, fk, T["dim"], align="right")
        yy += rh


def v1_windows(T):
    cv = R12.new_canvas(T, 1024, 600)
    v1_bar(cv, T, "code", "Sky Run", editor_tools("art", labelled=False), open_=True)
    R12.body_bg(cv, T, BAR)
    art(cv, T, G, BAR + G, 1024 - 2 * G, 600 - BAR - 2 * G)
    menu(cv, T, 52, BAR + 4, 250, WINDOW_MENU)
    return cv


def shell_bar(T, W=1024):
    """The shell bar over a full-screen app: Moy, the window chips (the
    full-screen app's current), then leave full screen and close, then the
    status."""
    cv = R12.new_canvas(T, W, BAR)
    R12.desk_bar(cv, T, (("code", "Editor"), ("folder", "Files"), ("calc", "Calc")))
    sx = W - 14 - M.F(T, "status", M.DK).width("14:32") - 58
    f = fx(T, 15, "med")
    xr = sx - 16
    ic(cv, T, "close", xr - CELL + 6, 10, 20, T["text"])
    xr -= CELL + 4
    w = 18 + 6 + f.width("Exit full screen") + 16
    R12.pill(cv, T, xr - w, 4, BAND, "fullscreen_exit", "Exit full screen", False, f)
    cv.rect(xr - w - 10, 10, 1, 20, T["line"])
    return cv.image()


def pointer(cv, x, y):
    pts = [(0, 0), (0, 17), (4.5, 13), (7.5, 20), (10.5, 18.8), (7.4, 12.2), (12.8, 12.2)]

    def outline(d, s):
        p = [((2 + a) * s, (2 + b) * s) for a, b in pts]
        d.polygon(p, fill=255)
        d.line(p + [p[0]], fill=255, width=int(3 * s), joint="curve")

    def body(d, s):
        d.polygon([((2 + a) * s, (2 + b) * s) for a, b in pts], fill=255)
    cv.shape(x - 2, y - 2, 18, 25, outline, "#ffffff")
    cv.shape(x - 2, y - 2, 18, 25, body, "#000000")


def v2_revealed(T):
    """The pointer pushed to the top edge: the bar slides over the row."""
    cv = ws_art(T, "v2")
    cv.shadow(0, 0, cv.W, BAR, 0, ("soft", "#000000", 0.22, 10, 4))
    cv.paste(shell_bar(T, cv.W), 0, 0)
    pointer(cv, 588, 1)
    return cv


def v2_touch(T):
    """A swipe down from the top edge: the bar follows the finger in."""
    cv = ws_art(T, "v2")
    shown = 26
    cv.shadow(0, shown - BAR, cv.W, BAR, 0, ("soft", "#000000", 0.22, 10, 4))
    cv.paste(shell_bar(T, cv.W).crop((0, BAR - shown, cv.W, BAR)), 0, 0)
    fx_, fy_ = 600, shown + 10
    cv.rrect(fx_ - 9, 0, 18, fy_, 9, "#000000", 0.16)
    cv.shape(fx_ - 22, fy_ - 22, 44, 44, lambda d, s: d.ellipse((0, 0, 44 * s - 1, 44 * s - 1), fill=255),
             "#000000", 0.30)
    cv.ring(fx_ - 22, fy_ - 22, 44, 44, 22, 3, "#ffffff", 0.95)
    return cv


# ==========================================================================
# resizable panes (MAXIMISE_VARIANTS.md, Resize): free dividers, whole-zoom
# content, the leftover a margin of the pane's own ground; a divider stops
# at its neighbour's minimum (docs/studio_2026-09.md §4.2). On E-05's frame.
# ==========================================================================
HANDLE = (6, 36)     # a divider's handle as drawn; its touch target is 32 x 48
TDECK_BAR = 26       # the handheld bar's height: a pane's tab bar at chrome scale 1


def handle(cv, T, x, y, w, h, live=False):
    """A divider's handle at its middle: faint at rest, `value` while held,
    with the gutter itself tinted."""
    vertical = h > w
    if live:
        cv.rect(x, y, w, h, T["value"], 0.18)
    hw, hh = HANDLE if vertical else HANDLE[::-1]
    cv.rrect(x + w // 2 - hw // 2, y + h // 2 - hh // 2, hw, hh, 3,
             T["value"] if live else mix(T["dim"], T["bg"], 0.45))


def handles(cv, T, gs, live=None):
    for i, g in enumerate(gs):
        handle(cv, T, *g, live=i == live)


def at_min(cv, T, x, y, w, h):
    """The pane a held divider has stopped at: a `value` ring."""
    cv.ring(x - 1, y - 1, w + 2, h + 2, 9, 2, T["value"])


def tag(cv, T, x, y, s, align="left"):
    """What a drag gives, in a dark tag beside the pointer."""
    f = fx(T, 13, "semi")
    w = f.width(s) + 20
    if align == "right":
        x -= w
    M.soft(cv, T, x, y, w, 28, 8, 0.8)
    cv.rrect(x, y, w, 28, 8, T["text"])
    cv.text(x + 10, M.mid_base(f, y, 28), s, f, T["surface"])


def col_cursor(cv, x, y):
    """The pointer over a divider: a double arrow across it."""
    pts = [(0, 6), (6, 0), (6, 4), (14, 4), (14, 0), (20, 6), (14, 12), (14, 8), (6, 8), (6, 12)]

    def shape(grow):
        def fn(d, s):
            p = [((3 + a) * s, (3 + b) * s) for a, b in pts]
            d.polygon(p, fill=255)
            if grow:
                d.line(p + [p[0]], fill=255, width=int(3 * s), joint="curve")
        return fn
    cv.shape(x - 13, y - 9, 26, 18, shape(True), "#ffffff")
    cv.shape(x - 13, y - 9, 26, 18, shape(False), "#000000")


def finger(cv, x, y):
    cv.shape(x - 22, y - 22, 44, 44, lambda d, s: d.ellipse((0, 0, 44 * s - 1, 44 * s - 1), fill=255),
             "#000000", 0.30)
    cv.ring(x - 22, y - 22, 44, 44, 22, 3, "#ffffff", 0.95)


def sprite_free(cv, T, x, y, w, h, t=12):
    """The Sprite pane at any size: the canvas at the largest whole zoom
    its box allows (8x8 at c, the palette at c beside it), the column
    centred, the rest the pane's ground; its zoom named at the foot."""
    bx, by, bw, bh = pane(cv, T, x, y, w, h, title=f"Sprite {t}", slot="sprite", focus=True,
                          tools=(("flip", None), ("rotate", None)))

    def fits(c):
        sh, fc = (36, 36) if c >= 36 else (32, c)
        return 10 * c + 32 <= bw and 46 + 8 * c + 10 + sh + 6 + fc + 30 <= bh
    c = next(c for c in range(64, 7, -1) if fits(c))
    sh, fc = (36, 36) if c >= 36 else (32, c)
    cx = bx + (bw - (10 * c + 8)) // 2
    R12.tool_row(cv, T, cx - 2, by + 4, 36, px=20 if c >= 36 else 18)
    gy = by + 46
    R12.sprite_grid(cv, T, cx, gy, c, SKY, t, live=True)
    px_ = cx + 8 * c + 8
    R12.palette(cv, T, px_, gy, c)
    fr = fx(T, 15 if c >= 36 else 14)
    yy = gy + 8 * c + 10
    sw_ = R12.stepper(cv, T, cx, yy, sh, str(t), fr)
    R12.small_button(cv, T, cx + sw_ + 8, yy, fr.width("1×1") + 24, sh, None, "1×1", face=fr)
    R12.small_button(cv, T, px_, yy, 2 * c, sh, None, "64", face=fr)
    fy = yy + sh + 6
    R12.flag_row(cv, T, cx, fy, fc, on=(0,))
    R12.caption(cv, T, bx + 12, by + bh - 20, f"8×8 at {c}, the largest whole zoom here")
    return c


def sheet_pane(cv, T, x, y, w, h):
    """The Sheet narrower than both pages at 2x: one page at 2x, its chips."""
    bx, by, bw, bh = pane(cv, T, x, y, w, h, title="Sheet", slot="sprite")
    R12.sheet_page(cv, T, bx, by, bw, bh, sel=12)


def tdeck_pane(cv, T, x, y, page, cur, tabs):
    """A pane at its minimum, 320 x 240: the panel as the T-Deck draws it,
    at chrome scale 1, under the pane's own 26 px tab bar."""
    pane(cv, T, x, y, 320, 240, header=False, focus=True)
    cv.paste(page.image().crop((0, TDECK_BAR, 320, 240)), x, y + TDECK_BAR)
    cv.rect(x, y + 3, 320, TDECK_BAR - 3, T["surface"])
    cv.rect(x, y + TDECK_BAR - 1, 320, 1, T["line"])
    lf = fx(T, 11, "semi", 2)
    tx = x + 4
    for i, (slot, label) in enumerate(tabs):
        if i == cur:
            tw = 16 + 3 + lf.width(label) + 9
            ink = M.current_mark(cv, T, tx, y + 4, tw, 20, 5) or T["text"]
            ic(cv, T, slot, tx + 3, y + 6, 16, ink)
            cv.text(tx + 22, M.mid_base(lf, y + 4, 20), label, lf, ink)
        else:
            tw = 22
            ic(cv, T, slot, tx + 3, y + 6, 16, T["dim"])
        tx += tw + 2
    ic(cv, T, "grow", x + 320 - 22, y + 6, 14, T["dim"])


def map_column(cv, T, mx, y, mw, h, view_h):
    """The 800 Map · Game pane at any width: the view, one sheet page at
    2x, the brush and the zoom beside it."""
    bx, by, bw, bh = pane(cv, T, mx, y, mw, h, tabs=(("map", "Map"), ("screen", "Game")),
                          tools=R12.map_tools(2, brush=False))
    R12.map_view(cv, T, bx + 6, by + 4, bw - 12, view_h, 2, 72, min(120, 240 - view_h // 2),
                 cursor=(26, 22), live=False)
    sy = by + 4 + view_h + 6
    R12.sheet_view(cv, T, bx + 6, sy, SKY, 2, 0, 8, sel=12)
    R12.page_chips(cv, T, bx + 6 + 256 + 10, sy, 2, 0)
    fm = fx(T, 13, "med")
    tx = bx + 6 + 256 + 54
    for i, (g, s) in enumerate((("brush", "Brush 1×1"), ("search", "Zoom ‹ 2× ›"))):
        ic(cv, T, g, tx, sy + 4 + i * 34, 18, T["dim"])
        cv.text(tx + 26, sy + 18 + i * 34, s, fm, T["text"])


def _code_cols(w):
    adv = R12.mono().glyph("m")[3]
    return (w - (12 + 3 * adv + 10) - 6) // adv


def resize_art(T):
    """Art's divider moved right, Sprite 400 -> 470: the canvas 36 -> 38,
    held by the pane's height, centred; the Sheet, too narrow for both
    pages at 2x, shows one."""
    cv, x, y, w, h = R12.frame_b(T, "art")
    sw = 470
    sprite_free(cv, T, x, y, sw, h)
    mx, mw = x + sw + G, w - sw - G
    R12.map_panel(cv, T, mx, y, mw, 336)
    sheet_pane(cv, T, mx, y + 336 + G, mw, h - 336 - G)
    handles(cv, T, ((x + sw, y, G, h), (mx, y + 336, mw, G)))
    return cv


def resize_code(T):
    """Code's divider moved left, the right column 340 -> 452: the game
    stays 1x, centred, the rest the Run pane's ground."""
    cv, x, y, w, h = R12.frame_b(T, "code", playing=True, waiting=True)
    rw = 452
    cw = w - rw - G
    R12.code_panel(cv, T, x, y, cw, h, first=172, cur=189, col=12)
    R12.run_panel(cv, T, x + cw + G, y, rw, RUN_H)
    R12.reference(cv, T, x + cw + G, y + RUN_H + G, rw, h - RUN_H - G)
    handles(cv, T, ((x + cw, y, G, h), (x + cw + G, y + RUN_H, rw, G)))
    return cv


def resize_code_drag(T):
    """By touch, mid-drag: the finger has gone on, the divider has stopped
    where Run reaches its minimum (the game at 1x, 320 wide)."""
    cv, x, y, w, h = R12.frame_b(T, "code", playing=True, waiting=True)
    rw = 320
    cw = w - rw - G
    R12.code_panel(cv, T, x, y, cw, h, first=172, cur=189, col=12)
    rx = x + cw + G
    R12.run_panel(cv, T, rx, y, rw, RUN_H)
    R12.reference(cv, T, rx, y + RUN_H + G, rw, h - RUN_H - G)
    at_min(cv, T, rx, y, rw, RUN_H)
    handles(cv, T, ((x + cw, y, G, h), (rx, y + RUN_H, rw, G)), live=0)
    finger(cv, x + cw + 74, y + 230)
    tag(cv, T, x + cw - 16, y + 252, "Run stops at its minimum: the game at 1×, 320 wide", align="right")
    return cv


def resize_art_min(T):
    """Map at its minimum, 320 x 240, drawn as on the T-Deck at chrome
    scale 1; the Sprite pane takes the rest, its column centred."""
    cv, x, y, w, h = R12.frame_b(T, "art")
    mw = 320
    sw = w - mw - G
    sprite_free(cv, T, x, y, sw, h)
    mx = x + sw + G
    tdeck_pane(cv, T, mx, y, R12.hh_map(T), 0, (("map", "Map"), ("screen", "Game")))
    sheet_pane(cv, T, mx, y + 240 + G, mw, h - 240 - G)
    handles(cv, T, ((x + sw, y, G, h), (mx, y + 240, mw, G)))
    return cv


def resize_art800(T):
    """800, mid-drag by pointer: the divider dragged left has stopped at
    Sprite's minimum, 320 wide (the design's 306 widens to it); the canvas
    8x8 at 25; the Map takes the rest."""
    cv, x, y, w, h = R12.frame_b(T, "art", W=800, H=480)
    sw = 320
    sprite_free(cv, T, x, y, sw, h)
    mx, mw = x + sw + G, w - sw - G
    map_column(cv, T, mx, y, mw, h, 204)
    at_min(cv, T, x, y, sw, h)
    handles(cv, T, ((x + sw, y, G, h),), live=0)
    col_cursor(cv, x + sw - 46, y + 190)
    tag(cv, T, x + sw - 30, y + 206, "Sprite stops at its minimum, 320 wide")
    return cv


RESIZE = (("resize-art", resize_art, "Art with its divider moved: Sprite 470 wide, the canvas 8×8 at "
                                     "38, centred; the Sheet one page at 2×"),
          ("resize-code", resize_code, "Code with its divider moved: the right column 452 wide, the "
                                       "game at 1× centred in the Run pane"),
          ("resize-code-drag", resize_code_drag, "Code mid-drag by touch: the divider stopped at "
                                                 "Run's minimum, the finger gone on"),
          ("resize-art-min", resize_art_min, "Art with the Map at its minimum, 320×240, drawn as on "
                                             "the T-Deck"),
          ("resize-art-800x480", resize_art800, "Art on an 800x480 desk mid-drag: the divider "
                                                "stopped at Sprite's minimum, 320 wide"))


# ==========================================================================
# the pictures
# ==========================================================================
VARIANTS = (("e05", "E-05, tools in the title strip (as drawn)"),
            ("v1", "V1, the bar takes the tools"),
            ("v2", "V2, full screen"))


def _e05(name, T):
    return {"art": R12.b_art, "code": R12.b_code, "art800": lambda T_: R12.b800(T_, "art")}[name](T)


def _draw(variant, name, T):
    if variant == "e05":
        return _e05(name, T)
    return {"art": ws_art, "code": ws_code, "art800": ws_art800}[name](T, variant)


def scenes():
    R12._SHIPPED_ON[0] = True
    try:
        _scenes()
    finally:
        R12._SHIPPED_ON[0] = False


def _scenes():
    arts = []
    for v, what in VARIANTS:
        pre = f"Maximise {what}"
        im = _draw(v, "art", LIGHT)
        arts.append((what, im.image()))
        im.save(f"moy-light_desktop_editor-max-{v}-art",
                f"{pre}: Art, the sprite, the map, the sheet; Moy Day")
        _draw(v, "code", LIGHT).save(f"moy-light_desktop_editor-max-{v}-code",
                                     f"{pre}: Code, the game with a dot on Restart, the reference; Moy Day")
        _draw(v, "art800", LIGHT).save(f"moy-light_desktop_editor-max-{v}-art-800x480",
                                       f"{pre}: Art on an 800x480 desk; Moy Day")
        _draw(v, "code", DARK).save(f"moy-dark_desktop_editor-max-{v}-code", f"{pre}: Code; Moy Night")
        files(LIGHT, v).save(f"moy-light_desktop_files-max-{v}",
                             f"{pre}: Files maximised, its verbs and search in the app's row; Moy Day")
    v1_windows(LIGHT).save("moy-light_desktop_editor-max-v1-windows",
                           "Maximise V1: the app's chip open, its window menu: Restore, Minimise, "
                           "Close, the other windows, the desk; Moy Day")
    v2_revealed(LIGHT).save("moy-light_desktop_editor-max-v2-revealed",
                            "Maximise V2: the pointer at the top edge, the shell bar over the app's "
                            "row with Exit full screen and close; Moy Day")
    v2_touch(LIGHT).save("moy-light_desktop_editor-max-v2-touch",
                         "Maximise V2: a swipe down from the top edge, the bar following the finger "
                         "in; Moy Day")
    for name, fn, what in RESIZE:
        fn(LIGHT).save(f"moy-light_desktop_editor-{name}", f"Resizable panes, free dividers: {what}; Moy Day")
    compare(arts).save("editor_maximise_compare",
                       "Maximise, the three variants' Art workspace stacked: E-05, V1, V2; Moy Day")


def compare(arts):
    cap = 32
    cv = R12.new_canvas(LIGHT, 1024, len(arts) * (600 + cap))
    f = fx(LIGHT, 15, "semi")
    for i, (what, im) in enumerate(arts):
        y = i * (600 + cap)
        cv.rect(0, y, 1024, cap, LIGHT["text"])
        cv.text(14, M.mid_base(f, y, cap), what, f, LIGHT["surface"])
        cv.paste(im, 0, y + cap)
    return cv
