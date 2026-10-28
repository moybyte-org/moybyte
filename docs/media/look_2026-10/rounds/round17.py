#!/usr/bin/env python3
"""Round 17 of the 2026-10 look, over round 16: the surfaces still in the
old look (`docs/look_2026-10.md` step 33).

- The boot splash, and the loading pill an open shows while it loads.
- The bar widgets' popovers: the calendar, the power rows and the Wi-Fi
  page in the popover's card.
- A stopped run: the run's card (a sibling of the pause card), the notice a
  cart this console cannot run shows, and the Code tab's stopped line.
- The Code tab's overlays: find, completion, go to and the files.
- The compact bar a tool cart keeps on a separate game canvas.
- The dialogs: Blocks' name and number prompts, Make it mine's cart info
  and new script, and the project's files as a page.
- The saver, the web console, the text console, the home's grid and icons
  arrangements, Edit icons, the perf HUD, and a theme with no look.

Each is drawn on the T-Deck (320x240), the Guition S3 where touch changes it
(480x320, no hint strip, rows at the tap floor) and the 1024x600 desk, in Moy
Day and Night, and in Contrast where its marks or fills move.

A layer of the target, not an entry point: `../draw.py` draws every picture.
"""
import contextlib
import copy
import os
import sys

from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import round16 as R16  # noqa: E402

R13, M, R11, R8, R7 = R16.R13, R16.M, R16.R11, R16.R8, R16.R7
R12 = R13.R12 if hasattr(R13, "R12") else None
if R12 is None:
    import round12 as R12  # noqa: E402
Face, F, HH, DK, mix = M.Face, M.F, M.HH, M.DK, M.mix
LIGHT, DARK, CONTRAST = R16.LIGHT, R16.DARK, R16.CONTRAST
sicon = R13.sicon
TDECK, S3 = R16.TDECK, R16.S3
contrast = R16.contrast


# ==========================================================================
# shared parts
# ==========================================================================
def flat_card(cv, T, x, y, w, h, r):
    """A card in flat fills: a 2 px hard drop in an opaque shade of the
    ground (no blur, no alpha over what is under it), the surface, the
    edge. What repaints with a keystroke or over a running game draws
    this, never a soft shadow."""
    if not contrast(T):
        drop = mix(T["bg"], "#000000", 0.45 if T["mode"] == "dark" else 0.12)
        cv.rrect(x, y + 2, w, h, r, drop)
    cv.rrect(x, y, w, h, r, T["surface"])
    if contrast(T):
        cv.ring(x, y, w, h, r, 2, T["text"])
    elif T["mode"] == "dark":
        cv.ring(x, y, w, h, r, 1, mix(T["line"], T["text"], 0.3))
    else:
        cv.ring(x, y, w, h, r, 1, mix(T["line"], T["dim"], 0.2))


def pop_card(cv, T, x, y, w, h, r=10, clip=None):
    """`ui.popover`'s card, flat (`flat_card`)."""
    flat_card(cv, T, x, y, w, h, r)


def modal_card(cv, T, x, y, w, h, desk=False):
    """A system modal's card (About's, the pause card's), flat over its
    wash."""
    flat_card(cv, T, x, y, w, h, 14 if desk else 10)


WASH = {"light": 0.4, "dark": 0.6}


def wash(cv, x, y, w, h, T=None):
    """The look's scrim (D-01): a flat dim over what a modal covers, about
    40% in a light mode and 60% in a dark one, where 40% did not read."""
    a = WASH["dark" if T is None or T["mode"] == "dark" else "light"]
    cv.rect(x, y, w, h, "#000000", a)


@contextlib.contextmanager
def no_focus():
    """Draw what a modal covers with its focus hidden (the page's focus
    mark would read as a second focus beside the modal's)."""
    keep = R12.focus_plate
    R12.focus_plate = lambda *a, **k: None
    try:
        with M.focus_off():
            yield
    finally:
        R12.focus_plate = keep


def faces(T, desk):
    """(title, row, body, meta) faces for a size class."""
    if desk:
        return (Face(T["fam"], 18, T["w_semi"], 4), F(T, "row", DK), F(T, "row", DK),
                F(T, "meta", DK))
    return (Face(T["fam"], 14, T["w_semi"], 4), Face(T["fam"], 13, T["w_med"], 2),
            Face(T["fam"], 12, T["w_body"], 2), F(T, "status", HH))


def mono(T, desk):
    return R7.code_face(T, desk)


ARROWS = {"←": "arrow_left", "→": "arrow_right", "↑": "arrow_up", "↓": "arrow_down"}
_KEYCAP = R11.keycap


BALLS = ("ball_lr", "ball_ud", "ball")


def own_cap(T, label):
    return (label in ARROWS and contrast(T)) or label == "shift+enter" or label in BALLS


def ball_sign(cv, x, y, w, h, ink, kind):
    """The T-Deck's trackball in a cap (moy_ui_parts.c's `ball_sign`): a
    ball between two heads, left and right (`ball_lr`) or up and down
    (`ball_ud`); `ball` alone is rolling and clicking it."""
    cx, cy = x + w / 2, y + h / 2
    d = h * 0.36
    cv.shape(int(cx - d / 2), int(cy - d / 2), int(d) + 1, int(d) + 1,
             lambda dr, k: dr.ellipse((0, 0, d * k, d * k), fill=255), ink)
    if kind == "ball":
        return
    a = h * 0.16
    o = d / 2 + a * 1.6
    for sgn in (-1, 1):
        if kind == "ball_lr":
            tx, ty = cx + sgn * o, cy
            pts = ((tx + sgn * a, ty), (tx - sgn * a * 0.2, ty - a), (tx - sgn * a * 0.2, ty + a))
        else:
            tx, ty = cx, cy + sgn * o
            pts = ((tx, ty + sgn * a), (tx - a, ty - sgn * a * 0.2), (tx + a, ty - sgn * a * 0.2))
        x0, y0 = int(min(p[0] for p in pts)) - 1, int(min(p[1] for p in pts)) - 1
        cv.shape(x0, y0, 2 * int(a) + 4, 2 * int(a) + 4,
                 lambda dr, k, pts=pts, x0=x0, y0=y0: dr.polygon(
                     [((px - x0) * k, (py - y0) * k) for px, py in pts], fill=255), ink)


def keycap(cv, T, x, base, label, face, bg, h):
    """R11's keycap, and the caps it lacks: an arrow drawn as the set's
    arrow icon where the theme's face has no arrows (Contrast's),
    Shift+Enter as one cap, and the T-Deck's trackball."""
    if own_cap(T, label):
        K = T["keycap"]
        y = base - h + 4
        s = h - 4
        fill = K["fill"] or mix(bg, T["text"], 0.16 if T["mode"] == "dark" else 0.10)
        ink = K["ink"] or T["text"]
        w = _cap_w(face, label, h)
        cv.rrect(x, y, w, h, K["r"] + 1, fill, K["alpha"])
        if K["border"]:
            cv.ring(x, y, w, h, K["r"] + 1, K["border"][0], K["border"][1])
        if label == "shift+enter":
            tw = face.width("Shift+")
            cv.text(x + 4, M.mid_base(face, y, h), "Shift+", face, ink)
            M.icon(cv, "enter", x + 4 + tw + 1, y + 2, s, ink, stroke=2.2)
        elif label in BALLS:
            ball_sign(cv, x, y, w, h, ink, label)
        else:
            sicon(cv, T, ARROWS[label], x + 2, y + 2, s, ink)
        return w
    return _KEYCAP(cv, T, x, base, label, face, bg, h)


def _cap_w(face, label, h):
    if label in ARROWS or label == "ball":
        return h
    if label in BALLS:
        return h + 8 if label == "ball_lr" else h
    if label == "shift+enter":
        return face.width("Shift+") + h - 4 + 10
    return _CAP_W(face, label, h)


_CAP_W = R11._cap_w


@contextlib.contextmanager
def caps():
    """Round 17's keycaps in R11's hints, for this round's pictures alone."""
    R11.keycap, R11._cap_w = keycap, _cap_w
    keep = M.keycap
    M.keycap = lambda cv, T, x, base, label, face, bg: keycap(cv, T, x, base, label, face, bg, 15) \
        if own_cap(T, label) else keep(cv, T, x, base, label, face, bg)
    try:
        yield
    finally:
        R11.keycap, R11._cap_w, M.keycap = _KEYCAP, _CAP_W, keep


# The T-Deck's keys for an action (moy_ui_parts.c's ACT_KEYS, the `tdeck`
# row): back is K where nothing types and a field's cancel Mic where letters
# do; the trackball rolls (`ball_lr`, `ball_ud`) and its roll and click on
# the bar is how a tab is chosen, as in the regular Editor; Search is Sym+G.
BACK, CANCEL = "K", "Mic"
LR, UD, BALL = "ball_lr", "ball_ud", "ball"
SEARCH = ("Sym", "G")


def hh_hints(cv, T, G, items, bg=None):
    """The handheld's hint strip, on the T-Deck alone (touch has none)."""
    if not G.touch:
        cv.rect(0, 218, G.W, 22, bg or T["bg"])
        R13.hh_hints(cv, T, items, bg=bg)


def foot_hints(cv, T, items, x, base, bg, desk=False):
    """Hints inside a card's foot (the pause card's, About's on the desk)."""
    R11.hints(cv, T, items, base, x=x, bg=bg, desk=desk)


def menu_rows(cv, T, x, y, w, rh, items, focus_i, desk=False):
    """A card's rows (the pause card's): an icon in `dim`, the label, the
    focus mark by the theme on the focused one."""
    fr = F(T, "row", DK) if desk else Face(T["fam"], 13, T["w_semi"], 2)
    s = 18 if desk else 14
    for i, (slot, label) in enumerate(items):
        ry = y + i * rh
        ink = M.focus_mark(cv, T, (x, ry + 1, w, rh - 2, 6), "part") if i == focus_i else None
        sicon(cv, T, slot, x + 10, ry + (rh - s) // 2, s, ink or T["dim"])
        cv.text(x + 18 + s, M.mid_base(fr, ry, rh), label, fr, ink or T["text"])
    return y + rh * len(items)


def home_under(T, G):
    """What a handheld popover or modal opens over: the T-Deck's home, the
    S3's Settings."""
    with M.focus_off():
        if G is TDECK:
            return M.hh_home(T)
        return R16.settings_about(T, G, focus=None)


def field(cv, T, x, y, w, h, text, placeholder=None, focus=True, desk=False, caret=True,
          face=None):
    """`ui.field`: the field fill, its outline at 3:1 (`field.border`), the
    focus mark by the theme, the text or its placeholder in the disabled
    ink after the caret's own room, the caret in `accent`."""
    r = 8 if desk else 6
    fill = T["bg"] if T["mode"] == "light" or contrast(T) else mix(T["bg"], T["surface"], 0.4)
    cv.rrect(x, y, w, h, r, fill)
    border = T["text"] if contrast(T) else mix(T["dim"], T["surface"], 0.15)
    cv.ring(x, y, w, h, r, 2 if contrast(T) else 1, border)
    if focus:
        cv.ring(x - 2, y - 2, w + 4, h + 4, r + 2, 2, T["accent"])
    f = face or (F(T, "row", DK) if desk else Face(T["fam"], 13, T["w_body"], 2))
    pad = 12 if desk else 8
    base = M.mid_base(f, y, h)
    cw = (2 if desk else 1)
    if text:
        tw = cv.text(x + pad, base, text, f, T["text"])
    else:
        tw = 0
        if placeholder:
            cv.text(x + pad + cw + 3, base, placeholder, f, R13.DISABLED.get(T["mode"], T["dim"])
                    if not contrast(T) else T["dim"])
    if caret and focus:
        cv.rect(x + pad + tw + (1 if text else 0), y + (h - f.cap) // 2 - 3, cw, f.cap + 6,
                T["accent"])
    return tw


def dialog(cv, T, cx, cy, w, title, body=None, fields=(), buttons=(), desk=False, extra=None,
           touch=False, hints=None, always_extra=False):
    """`ui.dialog` (the delete question's): the title, a line, its fields
    (a caption over each), then the buttons right-aligned, the one Enter
    does last -- on a glass that is touched or pointed at. On the T-Deck the
    keys are the hint strip's and the dialog has no button row; on the desk
    its keys sit at the left of its foot. Centred on (cx, cy)."""
    ft, fl, fb, fm = faces(T, desk)
    pad = 18 if desk else 12
    fh = 34 if desk else (30 if touch else 24)
    bh = 34 if desk else (30 if touch else 24)
    keys = desk or touch
    h = pad + (26 if desk else 18)
    if body:
        h += (24 if desk else 18) * len(body)
    for _cap, _t, _p in fields:
        h += (24 if desk else 16) + fh + (12 if desk else 8)
    h += ((8 if desk else 4) + bh + pad) if keys or always_extra else pad - 6
    x, y = cx - w // 2, cy - h // 2
    modal_card(cv, T, x, y, w, h, desk)
    yy = y + pad + (18 if desk else 13)
    cv.text(x + pad, yy, title, ft, T["text"])
    yy += 8 if desk else 6
    for ln in body or ():
        yy += 24 if desk else 18
        cv.text(x + pad, yy - (6 if desk else 5), ln, fb, T["dim"] if not contrast(T) else T["text"])
    yy += 4
    for i, (cap, text, ph) in enumerate(fields):
        yy += 20 if desk else 14
        cv.text(x + pad, yy, cap, fm, T["dim"])
        yy += 6 if desk else 4
        field(cv, T, x + pad, yy, w - 2 * pad, fh, text, ph, focus=i == 0, desk=desk)
        yy += fh + (12 if desk else 8)
    by = y + h - pad - bh
    f = F(T, "chip", DK) if desk else Face(T["fam"], 12, T["w_semi"], 2)
    if not keys:
        if always_extra and extra:
            bw = f.width(extra[0]) + 40
            R13.button(cv, T, "normal", x + pad, by, bw, bh, extra[0], extra[1])
        return x, y, w, h
    xr = x + w - pad
    for label, kind, focus in reversed(buttons):
        bw = max(f.width(label) + (32 if desk else 24), 76 if desk else 56)
        xr -= bw
        R13.button(cv, T, kind, xr, by, bw, bh, label, focus=focus, desk=desk)
        xr -= 8 if desk else 6
    if extra:
        label, slot = extra[:2]
        bw = f.width(label) + (54 if desk else 40)
        R13.button(cv, T, "normal", x + pad, by, bw, bh, label, slot, desk=desk)
    elif desk and hints:
        R11.hints(cv, T, hints, by + bh - 9, x=x + pad, bg=T["surface"], desk=True)
    return x, y, w, h


# ==========================================================================
# the boot splash
# ==========================================================================
def splash(T, W=320, H=240, frac=None, status=None):
    """Moy at the largest whole scale of a 16-unit tile that fits a
    quarter of the glass, the wordmark in the title face under it, on the
    stored theme's ground (Moy Day before there is one); a slow boot adds a
    track under the wordmark and a line in `dim` saying what it does."""
    desk = W >= 800
    cv = M.Cv(W, H, T["bg"], k=1, aa=True)
    scale = max(3, min(W, H) // 56)
    n = 16 * scale
    fw = Face(T["fam"], 40 if desk else (26 if W > 320 else 22), T["w_bold"], 4)
    fm = Face(T["fam"], 17, T["w_body"], 4) if desk else Face(T["fam"], 12, T["w_body"], 2)
    gap = n // 4
    block = n + gap + fw.cap
    if frac is not None:
        block += (28 if desk else 18) + (6 if desk else 4)
    if status:
        block += (30 if desk else 20)
    top = (H - block) // 2
    M.moy(cv, (W - n) // 2, top, n, T["value"], T["bg"])
    y = top + n + gap + fw.cap
    cv.text(W // 2, y, "moybyte", fw, T["text"], align="center")
    if frac is not None:
        tw = W * 3 // 8 if not desk else 320
        th = 6 if desk else 4
        y += 28 if desk else 18
        tx = (W - tw) // 2
        if contrast(T):
            cv.rrect(tx, y, tw, th, th / 2, T["bg"])
            cv.ring(tx, y, tw, th, th / 2, 1, T["text"])
        else:
            cv.rrect(tx, y, tw, th, th / 2, mix(T["line"], T["dim"], 0.25))
        cv.rrect(tx, y, max(th, int(tw * frac)), th, th / 2, T["value"] if not contrast(T)
                 else T["text"])
        y += th
    if status:
        y += 30 if desk else 20
        cv.text(W // 2, y, status, fm, T["dim"], align="center")
    return cv


# ==========================================================================
# the loading pill: the frame between an open and its load
# ==========================================================================
def loading_pill(cv, T, cx, y, slot, label, desk=False):
    """The toast's pill (GAPS 51) in flat fills: the cart's kind as an icon,
    then what is opening, in the ground's ink. Drawn once on the frame the
    open is asked for; the panel keeps it through the load."""
    f = F(T, "meta", DK) if desk else Face(T["fam"], 12, T["w_med"], 2)
    h = 34 if desk else 24
    s = 20 if desk else 14
    w = f.width(label) + s + (44 if desk else 30)
    x = cx - w // 2
    if contrast(T):
        cv.rrect(x, y, w, h, h / 2, T["bg"])
        cv.ring(x, y, w, h, h / 2, 2, T["text"])
        ink = T["text"]
    else:
        cv.rrect(x, y, w, h, h / 2, T["text"])
        ink = T["bg"]
    px = x + (16 if desk else 11)
    sicon(cv, T, slot, px, y + (h - s) // 2, s, ink)
    cv.text(px + s + (8 if desk else 6), M.mid_base(f, y, h), label, f, ink)
    return x, y, w, h


def loading(T, G=TDECK):
    """Over the cover being opened, on purpose: the pill sits on the hero's
    middle, and the hint strip goes quiet (a load cannot be cancelled)."""
    with M.focus_off():
        cv = M.hh_home(T) if G is TDECK else R16.settings_about(T, G, focus=None)
    loading_pill(cv, T, G.W // 2, 88, "game", "Opening Sky Run…")
    if G is TDECK:
        cv.rect(0, 218, 320, 22, T["bg"])
    return cv


def loading_desk(T):
    """The desk's pill in its own lane: the middle of the bar, between the
    window chips and the status, the one band no window covers."""
    with M.focus_off():
        cv = M.desktop(T)
    loading_pill(cv, T, 512, 4, "game", "Opening Sky Run…", desk=True)
    return cv


# ==========================================================================
# the bar widgets' popovers: hung from the widget, no wash (D-01)
# ==========================================================================
MONTH = ("October 2026", 3, 31, 23)     # its name, the 1st's weekday (Mon 0), days, today
WEEK = ("M", "T", "W", "T", "F", "S", "S")


def calendar(cv, T, xr, y, desk=False, touch=False):
    """The calendar: the month and year in the title face, the weekdays in
    the caption, the month's days in seven columns, today on the current
    disc of `value` in `value_ink`, bold; Saturday and Sunday in `dim`. ← and → page a month; a touch glass
    adds the two chevrons as its buttons."""
    name, first, days, today = MONTH
    cw = 34 if desk else (32 if touch else 26)
    rh = 30 if desk else (28 if touch else 21)
    pad = 14 if desk else 10
    head = 40 if desk else 28
    weeks = (first + days + 6) // 7
    w = 7 * cw + 2 * pad
    foot = 40 if desk else 0
    h = pad + head + (22 if desk else 16) + weeks * rh + pad // 2 + foot
    x = xr - w
    pop_card(cv, T, x, y, w, h, 12 if desk else 10)
    ft = Face(T["fam"], 18 if desk else 14, T["w_semi"], 4)
    fc = R11.caption_dk(T) if desk else F(T, "status", HH)
    fd = F(T, "row", DK) if desk else Face(T["fam"], 12, T["w_med"], 2)
    cv.text(x + pad, y + pad + (head - 12) // 2 + ft.cap // 2, name, ft, T["text"])
    if touch or desk:
        for i, slot in enumerate(("back", "forward")):
            R13.ibtn(cv, T, slot, x + w - pad - (2 - i) * 30 + 2, y + pad - 3 + (4 if desk else 0), 28)
    gy = y + pad + head
    for k, d in enumerate(WEEK):
        cv.text(x + pad + k * cw + cw // 2, gy + (8 if desk else 6), d, fc,
                T["dim"], align="center")
    gy += 22 if desk else 16
    for day in range(1, days + 1):
        i = first + day - 1
        cx = x + pad + (i % 7) * cw + cw // 2
        ry = gy + (i // 7) * rh
        ink = T["dim"] if i % 7 >= 5 else T["text"]
        face = fd
        if day == today:
            d = rh if not touch else rh - 2
            fill, ink = (T["text"], T["bg"]) if contrast(T) else (T["value"], T["value_ink"])
            cv.rrect(cx - d // 2, ry + (rh - d) // 2, d, d, d / 2, fill)
            face = Face(T["fam"], fd.size if hasattr(fd, "size") else 12, T["w_bold"], 4)
        cv.text(cx, M.mid_base(face, ry, rh), str(day), face, ink, align="center")
    if desk:
        R11.hints(cv, T, ((("←", "→"), "Month"), ("Esc", "Close")), y + h - 14,
                  x=x + pad, bg=T["surface"], desk=True)
    return x, y, w, h


def level_row(cv, T, x, y, w, rh, label, slot, v, n, focus, desk=False):
    """A level row (Settings' Volume and Brightness): the icon and label,
    then the level as `n` steps on a track at the right, its value filled."""
    fl = F(T, "row", DK) if desk else Face(T["fam"], 13, T["w_med"], 2)
    ink = M.focus_mark(cv, T, (x, y, w, rh, 6), "part") if focus else None
    s = 18 if desk else 14
    sicon(cv, T, slot, x + 10, y + (rh - s) // 2, s, ink or T["dim"])
    cv.text(x + 18 + s, M.mid_base(fl, y, rh), label, fl, ink or T["text"])
    tw = 150 if desk else 84
    tx = x + w - 10 - tw
    ty = y + rh // 2 - (3 if desk else 2)
    th = 6 if desk else 4
    if contrast(T):
        cv.rrect(tx, ty, tw, th, th / 2, T["bg"])
        cv.ring(tx, ty, tw, th, th / 2, 1, T["text"])
    else:
        rest = mix(T["line"], T["dim"], 0.35)
        cv.rrect(tx, ty, tw, th, th / 2, rest)
    fw = tw * v // n
    if fw:
        cv.rrect(tx, ty, fw, th, th / 2, T["value"] if not contrast(T) else T["text"])
    d = 14 if desk else 10
    kx = tx + fw - d // 2
    kc = "#ffffff" if T["mode"] == "light" else T["text"]
    cv.shape(kx, ty + th // 2 - d // 2, d, d, lambda dr, s_: dr.ellipse((0, 0, d * s_ - 1, d * s_ - 1),
                                                                        fill=255), kc)
    cv.ring(kx, ty + th // 2 - d // 2, d, d, d / 2, 1,
            T["text"] if contrast(T) else mix(T["dim"], T["surface"], 0.3))


def power(cv, T, xr, y, desk=False, touch=False, focus=1):
    """The power popover: the battery's level and state as a line, then
    Volume and Brightness as level rows, stepped by ← and →."""
    rh = 40 if desk else (28 if touch else 26)
    pad = 10 if desk else 6
    w = 320 if desk else (260 if touch else 236)
    head = 46 if desk else 30
    h = pad + head + 2 * rh + pad
    x = xr - w
    pop_card(cv, T, x, y, w, h, 12 if desk else 10)
    ft, fl, fb, fm = faces(T, desk)
    s = 20 if desk else 16
    sicon(cv, T, "battery", x + pad + 10, y + pad + (head - s) // 2, s, T["text"])
    cv.text(x + pad + 18 + s, M.mid_base(ft, y + pad, head), "82%", ft, T["text"])
    cv.text(x + w - pad - 10, M.mid_base(fm, y + pad, head), "About 5 hours left", fm, T["dim"],
            align="right")
    yy = y + pad + head
    cv.rect(x + pad + 8, yy - 1, w - 2 * pad - 16, 1, T["line"])
    level_row(cv, T, x + pad, yy, w - 2 * pad, rh, "Volume", "volume", 3, 8, focus == 0, desk)
    level_row(cv, T, x + pad, yy + rh, w - 2 * pad, rh, "Brightness", "spark", 6, 8, focus == 1, desk)
    return x, y, w, h


NETS = (("Home", True, False, 3), ("Moy Lab", False, True, 2), ("Cafe guest", False, False, 1))


def wifi_bars(cv, T, x, y, n, ink, desk=False):
    """A network's signal: three rising bars, the ones it lacks faded."""
    bw, g = (4, 2) if desk else (3, 2)
    hs = (5, 9, 13) if desk else (4, 7, 10)
    for i, bh in enumerate(hs):
        c = ink if i < n else mix(ink, T["surface"], 0.7)
        cv.rrect(x + i * (bw + g), y + hs[-1] - bh, bw, bh, 1, c)


def wifi(cv, T, xr, y, desk=False, touch=False, focus=1):
    """The Wi-Fi popover: its card holding Settings' Wi-Fi page -- the
    radio's toggle, the joined network with its check, the others with
    their signal and a lock where they ask a password, Other network."""
    rh = 34 if desk else (28 if touch else 22)
    pad = 8 if desk else 6
    w = 320 if desk else (280 if touch else 232)
    n = 1 + len(NETS) + 1
    h = pad + rh + (16 if desk else 12) + 6 + (n - 1) * rh + pad
    x = xr - w
    pop_card(cv, T, x, y, w, h, 12 if desk else 10)
    ft, fl, fb, fm = faces(T, desk)
    yy = y + pad
    cv.text(x + pad + 10, M.mid_base(ft, yy, rh), "Wi-Fi", ft, T["text"])
    R11._value(cv, T, "seg", (("Off", "On"), 1), x + w - pad - 10, yy + 2, rh - 4, fm, desk)
    yy += rh + (16 if desk else 12)
    R13.caption(cv, T, x + pad + 10, yy, "Networks", desk=desk)
    yy += 6
    s = 18 if desk else 14
    for i, (name, joined, locked, sig) in enumerate(NETS):
        ink = M.focus_mark(cv, T, (x + pad, yy, w - 2 * pad, rh, 6), "part") if i == focus else None
        cv.text(x + pad + 10, M.mid_base(fl, yy, rh), name, fl, ink or T["text"])
        xr2 = x + w - pad - 10
        wifi_bars(cv, T, xr2 - (16 if desk else 13), yy + (rh - (13 if desk else 10)) // 2, sig,
                  ink or T["dim"], desk)
        xr2 -= (16 if desk else 13) + 8
        if locked:
            sicon(cv, T, "lock", xr2 - s, yy + (rh - s) // 2, s, ink or T["dim"])
            xr2 -= s + 6
        if joined:
            sicon(cv, T, "check", xr2 - s, yy + (rh - s) // 2, s, ink or T["value"])
        yy += rh
    cv.rect(x + pad + 10, yy, w - 2 * pad - 20, 1, T["line"])
    cv.text(x + pad + 10, M.mid_base(fl, yy, rh), "Other network…", fl, T["text"])
    return x, y, w, h


def popover_hh(T, G, which):
    cv = home_under(T, G)
    touch = G.touch
    if which == "calendar":
        calendar(cv, T, G.W - 4, 30, touch=touch)
        hh_hints(cv, T, G, ((LR, "Month"), (BACK, "Close")))
    elif which == "power":
        power(cv, T, G.W - 4, 30, touch=touch)
        hh_hints(cv, T, G, ((LR, "Change"), (UD, "Row"), (BACK, "Close")))
    else:
        wifi(cv, T, G.W - 4, 30, touch=touch)
        hh_hints(cv, T, G, (("enter", "Join"), (BACK, "Close")))
    return cv


def popover_desk(T, which):
    with M.focus_off():
        cv = M.desktop(T)
    if which == "calendar":
        calendar(cv, T, 1024 - 8, 46, desk=True)
    elif which == "power":
        power(cv, T, 1024 - 52, 46, desk=True)
    else:
        wifi(cv, T, 1024 - 84, 46, desk=True)
    return cv


# ==========================================================================
# a stopped run: the run's card, a sibling of the pause card (D-01: it
# washes the whole frozen game)
# ==========================================================================
ERROR = "TypeError: unsupported types for __mul__: 'NoneType', 'float'"

def frozen_game(G=TDECK, desk=False, img=None):
    """The run's last frame, washed whole: the T-Deck's glass, the S3's
    320x240 canvas at 1x in its letterbox, the desk's console mode at 2x."""
    img = img or R7.GAME
    if desk:
        cv = M.Cv(1024, 600, "#000000")
        cv.img.paste(img.resize((640, 480), Image.NEAREST), (192, 60))
    elif G is TDECK:
        cv = M.Cv(320, 240, "#000000")
        cv.img.paste(img, (0, 0))
    else:
        cv = M.Cv(G.W, G.H, "#000000")
        cv.img.paste(img, ((G.W - 320) // 2, (G.H - 240) // 2))
    wash(cv, 0, 0, cv.W, cv.H)
    return cv


def stop_card(cv, T, cx, top, w, title, state, sentence, text, rows, desk=False, touch=False,
              mono_text=True, focus=0, foot=True):
    """The stopped run's card: Moy, the cart's title and its state, the
    sentence that says what happened (os_voice §5, no alarm), the cart's own
    error exact in the code face on a `sunk` inset (a notice's text in the
    body face), then the rows the pause card's way, the first focused."""
    s = 2 if desk else 1
    pad = 12 * s if desk else 12
    head = 44 if desk else 30
    rh = 34 if desk else (28 if touch else 22)
    ft = Face(T["fam"], 18 if desk else 13, T["w_semi"], 4)
    fs = Face(T["fam"], 17 if desk else 13, T["w_med"], 4 if desk else 2)
    fm = F(T, "meta", DK) if desk else F(T, "status", HH)
    fc = mono(T, desk) if mono_text else (F(T, "meta", DK) if desk else Face(T["fam"], 12, T["w_body"], 2))
    inner = w - 2 * pad - ((24 if desk else 16) if mono_text else 0)
    lines = R16.wrap(fc, text, inner, 3)
    lh = 22 if desk else (14 if mono_text else 15)
    box_h = len(lines) * lh + ((20 if desk else 12) if mono_text else 0)
    foot = 0 if touch or not foot else (40 if desk else 24)
    h = pad // 2 + head + (14 if desk else 8) + (26 if desk else 18) + box_h + (12 if desk else 8) \
        + rh * len(rows) + foot + (8 if desk else 6)
    x = cx - w // 2
    modal_card(cv, T, x, top, w, h, desk)
    y = top + pad // 2
    n = 32 if desk else 18
    M.moy(cv, x + pad, y + (head - n) // 2, n, T["value"], T["surface"])
    cv.text(x + pad + n + (12 if desk else 8), M.mid_base(ft, y, head), title, ft, T["text"])
    if state:
        cv.text(x + w - pad, M.mid_base(fm, y, head), state, fm, T["dim"], align="right")
    y += head
    cv.rect(x + pad, y, w - 2 * pad, 1, T["line"])
    y += (14 if desk else 8) + (18 if desk else 13)
    cv.text(x + pad, y, sentence, fs, T["text"])
    y += (12 if desk else 6)
    box = T["sunk"] if T["mode"] == "light" else mix(T["bg"], T["surface"], 0.5)
    inset = (12 if desk else 8) if mono_text else 0
    if not mono_text:
        pass
    elif contrast(T):
        cv.rrect(x + pad, y, w - 2 * pad, box_h, 6, T["bg"])
        cv.ring(x + pad, y, w - 2 * pad, box_h, 6, 1, T["text"])
    else:
        cv.rrect(x + pad, y, w - 2 * pad, box_h, 8 if desk else 6, box)
    ty = y + ((10 if desk else 6) if mono_text else 2) + (lh + fc.cap) // 2 - 1
    for ln in lines:
        cv.text(x + pad + inset, ty, ln, fc, T["text"] if mono_text else T["dim"])
        ty += lh
    y += box_h + (12 if desk else 8)
    menu_rows(cv, T, x + pad // 2, y, w - pad, rh, rows, focus, desk)
    y += rh * len(rows)
    if foot:
        cv.rect(x + pad, y + 2, w - 2 * pad, 1, T["line"])
        foot_hints(cv, T, (("enter", "Choose"), ("Esc" if desk else BACK, "Home")), x + pad,
                   y + (30 if desk else 18),
                   T["surface"], desk)
    return x, top, w, h


CRASH_ROWS = (("code", "See why"), ("rotate", "Restart"), ("home", "Home"))


def centred(cv, T, top, bottom, *a, **k):
    """A stop card centred between `top` and `bottom`: measured on a
    scratch canvas first."""
    scratch = M.Cv(cv.W, cv.H, "#000000")
    h = stop_card(scratch, T, *a[:1], 0, *a[1:], **k)[3]
    return stop_card(cv, T, a[0], top + (bottom - top - h) // 2, *a[1:], **k)


def crash(T, G=TDECK):
    cv = frozen_game(G)
    if G is TDECK:
        centred(cv, T, 0, 240, 160, 288, "Star Catcher", "Stopped", "Your game stopped on line 42.",
                ERROR, CRASH_ROWS)
    else:
        centred(cv, T, 0, G.H, G.W // 2, 340, "Star Catcher", "Stopped",
                "Your game stopped on line 42.", ERROR, CRASH_ROWS, touch=True)
    return cv


def crash_compiled(T):
    """A compiled cart's trap (Star Catcher built for the compiled tier):
    no source line behind it, so no See why."""
    cv = frozen_game()
    centred(cv, T, 0, 240, 160, 288, "Star Catcher", "Stopped", "Your game stopped.",
            "wasm trap: out of bounds memory access", CRASH_ROWS[1:])
    return cv


NOTICE = "It uses tilemap2. Update in Settings, then try again."


def notice(T, G=TDECK):
    """A cart this console cannot run (fit, newer, no load): the same card
    over the home it was opened from, washed whole, its keys in its foot
    as the stop card's are; its words only what the sentence does not say."""
    cv = home_under(T, G)
    if not G.touch:
        cv.rect(0, 218, G.W, 22, T["bg"])
    wash(cv, 0, 28, G.W, G.H - 28, T)
    rows = (("settings", "Open Updates"), ("home", "Home"))
    centred(cv, T, 28, G.H, G.W // 2, 288 if G is TDECK else 340, "Sky Run", None,
            "Needs a newer console.", NOTICE, rows, touch=G.touch, mono_text=False)
    return cv


def crash_desk(T):
    cv = frozen_game(desk=True)
    centred(cv, T, 0, 600, 512, 520, "Star Catcher", "Stopped", "Your game stopped on line 42.",
            ERROR, CRASH_ROWS, desk=True)
    return cv


# ==========================================================================
# the Code tab: the stopped line, and the overlays (find, completion, go
# to, the files)
# ==========================================================================
def code_tab(T, first=38, line=42, col=13, top_gap=0, text=None):
    """The handheld Code tab on Star Catcher, the caret at (line, col);
    `text` stands in for the caret's line (a word being typed)."""
    old = R7.CUR_LINE, R7.CUR_COL
    R7.CUR_LINE, R7.CUR_COL = line, col
    keep = R7.ROWS[line - 1]
    if text is not None:
        R7.ROWS[line - 1] = text
    try:
        cv, top = R12.hh_canvas(T, "code", T["bg"])
        R7.draw_code(cv, T, 0, top + top_gap, 320, 240 - top - 38 - top_gap, False, first=first)
        R8.status_and_keys(cv, T, 240 - 37, 320, False)
    finally:
        R7.CUR_LINE, R7.CUR_COL = old
        R7.ROWS[line - 1] = keep
    return cv, top


def status_text(cv, T, text, hints=None):
    """The Code tab's status line, said again (`R8.status_and_keys`)."""
    fm = F(T, "meta", HH)
    cv.rect(0, 204, 320, 15, T["bg"])
    x = 8 + cv.text(8, M.mid_base(fm, 203, 16), text, fm, T["dim"]) + 10 if text else 8
    if hints:
        R11.hints(cv, T, hints, 215, x=x, bg=T["bg"])


def line_y(top, first, ln, top_gap=0):
    return top + top_gap + 4 + (ln - first) * 14


def stopped_popover(cv, T, x, y, w, desk=False):
    """The crash-to-code popover, hung under the stopped line: what stopped
    it in the code face, exact, and that the first edit closes it; Esc too
    (Mic on the T-Deck, where letters type)."""
    fh = Face(T["fam"], 16 if desk else 12, T["w_semi"], 4 if desk else 2)
    fc = mono(T, desk)
    fm = F(T, "meta", DK) if desk else Face(T["fam"], 11, T["w_body"], 2)
    pad = 14 if desk else 10
    lines = R16.wrap(fc, ERROR, w - 2 * pad, 3)
    lh = 21 if desk else 14
    h = pad + (20 if desk else 14) + len(lines) * lh + (30 if desk else 22) + pad // 2
    pop_card(cv, T, x, y, w, h, 10 if desk else 8)
    s = 18 if desk else 14
    yy = y + pad
    sicon(cv, T, "warn", x + pad, yy - 2, s, T["value"] if not contrast(T) else T["text"])
    cv.text(x + pad + s + 6, yy + (12 if desk else 9), "Stopped on this line", fh, T["text"])
    yy += (20 if desk else 14) + (lh + fc.cap) // 2 - 1
    for ln in lines:
        cv.text(x + pad, yy, ln, fc, T["text"])
        yy += lh
    yy += 4
    cv.text(x + pad, yy + (8 if desk else 4), "Your first edit closes this.", fm, T["dim"])
    close = (("Esc" if desk else CANCEL, "Close"),)
    R11.hints(cv, T, close, yy + (8 if desk else 5),
              x=x + w - pad - R11.hints_width(T, close, desk), bg=T["surface"],
              desk=desk)
    return h


def crash_code(T):
    cv, top = code_tab(T, first=38, line=42, col=8)
    y = line_y(top, 38, 42)
    stopped_popover(cv, T, 24, y + 16, 292)
    status_text(cv, T, "Ln 42, Col 9  ·  Stopped here")
    return cv


def _defs():
    out = []
    for i, row in enumerate(R7.ROWS):
        s = "".join(t for _r, t in row)
        if s.startswith("def "):
            out.append((s[4:].split("(")[0], i + 1))
    return out


def find_marks(cv, T, top, first, word, cur_ln, top_gap=0, gut=26, adv=None, lh=14):
    """The find's matches over the code: each a tint of `value` behind
    the word, the current one a stronger tint with a bar under it."""
    f = mono(T, False)
    adv = adv or f.glyph("m")[3]
    n = 0
    hits = []
    for i in range((240 - top - 38 - top_gap - 4) // lh):
        ln = first + i
        if ln > len(R7.ROWS):
            break
        s = "".join(t for _r, t in R7.ROWS[ln - 1])
        k = s.find(word)
        while k >= 0:
            hits.append((ln, k))
            k = s.find(word, k + 1)
    for ln, k in hits:
        y = line_y(top, first, ln, top_gap)
        x = gut + k * adv
        on = ln == cur_ln
        cv.rrect(x - 1, y + 1, len(word) * adv + 2, lh - 2, 2, T["value"], 0.42 if on else 0.18)
        if on:
            cv.rect(x - 1, y + lh - 2, len(word) * adv + 2, 2, T["value"])
        n += 1
    return n


def find_bar(cv, T, x, y, w, h, query, count, desk=False):
    """The find bar: a field with the search icon and the query, the count,
    and Aa, the case toggle (a state, shown, not a key's stand-in)."""
    cv.rect(x, y, w, h + (8 if desk else 6), T["bg"] if not desk else T["surface"])
    fx = x + (8 if desk else 6)
    fy = y + (4 if desk else 3)
    fw = w - (300 if desk else 112)
    fh = h
    field(cv, T, fx, fy, fw, fh, query, desk=desk,
          face=mono(T, desk))
    fm = F(T, "meta", DK) if desk else F(T, "status", HH)
    cx = fx + fw + (16 if desk else 10)
    cv.text(cx, M.mid_base(fm, fy, fh), count, fm, T["dim"])
    tw = 34 if desk else 26
    tx = x + w - (8 if desk else 6) - tw
    if desk:
        for i, slot in enumerate(("close", "arrow_down", "arrow_up")):
            R13.ibtn(cv, T, slot, tx - 4 - (i + 1) * (fh + 2), fy, fh, desk=True)
        tx -= 0
    ink = M.current_mark(cv, T, tx, fy, tw, fh, 6) if False else T["dim"]
    cv.ring(tx, fy, tw, fh, 6, 1, mix(T["line"], T["dim"], 0.2))
    cv.text(tx + tw // 2, M.mid_base(fm, fy, fh), "Aa", fm, ink, align="center")
    cv.rect(x, y + h + (8 if desk else 6) - 1, w, 1, T["line"])


def code_find(T):
    """The second of five matches is the current one: the caret and the
    current line's tint are on it, where Enter moves from."""
    gap = 30
    line = "".join(t for _r, t in R7.ROWS[34])
    cv, top = code_tab(T, first=34, line=35, col=line.find("speed") + 5, top_gap=gap)
    find_marks(cv, T, top, 34, "speed", 35, top_gap=gap)
    find_bar(cv, T, 0, top, 320, 22, "speed", "2 of 5")
    status_text(cv, T, "", (("enter", "Next"), ("shift+enter", "Back"), (CANCEL, "Close")))
    return cv


def completion(cv, T, x, y, prefix, items, focus=0, desk=False):
    """Completion: `ui.menu` in a popover under the caret, the names in the
    code face, the typed part in `value`, each name's kind at the right."""
    f = mono(T, desk)
    fm = F(T, "meta", DK) if desk else Face(T["fam"], 11, T["w_body"], 2)
    rh = 26 if desk else 18
    pad = 4
    w = max(f.width(n) for n, _k in items) + (110 if desk else 70)
    h = 2 * pad + rh * len(items)
    pop_card(cv, T, x, y, w, h, 8 if desk else 6)
    adv = f.glyph("m")[3]
    for i, (name, kind) in enumerate(items):
        ry = y + pad + i * rh
        ink = M.focus_mark(cv, T, (x + 2, ry, w - 4, rh, 4), "part") if i == focus else None
        base = M.mid_base(f, ry, rh)
        tx = x + (12 if desk else 8)
        cv.text(tx, base, prefix, f, ink or T["value"])
        cv.text(tx + len(prefix) * adv, base, name[len(prefix):], f, ink or T["text"])
        cv.text(x + w - (12 if desk else 8), M.mid_base(fm, ry, rh), kind, fm, ink or T["dim"],
                align="right")
    return w, h


COMPLETE = (("_base_speed", "def"), ("_ball_x", "var"), ("_bar_h", "var"))


TYPING = [("text", "    s[2] = "), ("text", "_ba"), ("text", "\n")]


def code_complete(T):
    """A word being typed, `_ba` up to the caret: the list under it."""
    cv, top = code_tab(T, first=38, line=42, col=14, text=TYPING)
    f = mono(T, False)
    adv = f.glyph("m")[3]
    y = line_y(top, 38, 42)
    completion(cv, T, 26 + 11 * adv - 8, y + 16, "_ba", COMPLETE)
    status_text(cv, T, "", ((UD, "Pick"), ("enter", "Insert"), (CANCEL, "Close")))
    return cv


def goto(cv, T, x, y, w, query, items, focus=0, desk=False, rows=6):
    """Go to: a popover with its own field (what is typed filters the
    cart's definitions), each one's line at the right."""
    pad = 10 if desk else 8
    fh = 32 if desk else 22
    rh = 30 if desk else 20
    f = mono(T, desk)
    fm = F(T, "meta", DK) if desk else F(T, "status", HH)
    shown = items[:rows]
    h = pad + fh + 6 + rh * len(shown) + pad // 2
    pop_card(cv, T, x, y, w, h, 10 if desk else 8)
    field(cv, T, x + pad, y + pad, w - 2 * pad, fh, query, "Go to a definition", desk=desk,
          face=f if query else None)
    yy = y + pad + fh + 6
    for i, (name, ln) in enumerate(shown):
        ink = M.focus_mark(cv, T, (x + 4, yy, w - 8, rh, 6), "part") if i == focus else None
        cv.text(x + pad + 4, M.mid_base(f, yy, rh), name, f, ink or T["text"])
        cv.text(x + w - pad - 4, M.mid_base(fm, yy, rh), "line %d" % ln, fm, ink or T["dim"],
                align="right")
        yy += rh
    return h


def code_goto(T):
    cv, top = code_tab(T, first=38, line=42, col=13)
    goto(cv, T, 40, 32, 240, "", _defs())
    status_text(cv, T, "", ((UD, "Pick"), ("enter", "Go"), (CANCEL, "Close")))
    return cv


FILES = (("main.py", "code", "4.1 KB"), ("helpers.py", "code", "0.6 KB"))


def code_files(T):
    """The cart's scripts, hung from the bar's Code tab: the open one
    marked with a check in its trailing slot (focus keeps the plate and
    bar), then New script."""
    cv, top = code_tab(T, first=38, line=42, col=13)
    x, y, w = 50, 28, 200
    rh = 22
    pad = 5
    h = pad + 18 + rh * 3 + 9 + pad
    pop_card(cv, T, x, y, w, h, 8)
    R13.caption(cv, T, x + 12, y + pad + 13, "Scripts")
    fl = Face(T["fam"], 13, T["w_med"], 2)
    fm = F(T, "status", HH)
    yy = y + pad + 18
    for i, (name, slot, size) in enumerate(FILES):
        ink = None
        if i == 1:
            ink = M.focus_mark(cv, T, (x + 4, yy + 1, w - 8, rh - 2, 6), "part")
        sicon(cv, T, slot, x + 12, yy + 4, 14, ink or T["dim"])
        cv.text(x + 32, M.mid_base(fl, yy, rh), name, fl, ink or T["text"])
        if i == 0:
            sicon(cv, T, "check", x + w - 26, yy + 4, 14, T["value"] if not contrast(T) else T["text"])
        cv.text(x + w - 32, M.mid_base(fm, yy, rh), size, fm, ink or T["dim"], align="right")
        yy += rh
    cv.rect(x + 10, yy + 4, w - 20, 1, T["line"])
    yy += 9
    sicon(cv, T, "new", x + 12, yy + 4, 14, T["dim"])
    cv.text(x + 32, M.mid_base(fl, yy, rh), "New script", fl, T["text"])
    status_text(cv, T, "", ((UD, "Pick"), ("enter", "Open"), (CANCEL, "Close")))
    return cv


def desk_code(T, overlay):
    """The desk Editor's Code workspace (round 12's concept B) with an
    overlay over its code pane."""
    G_ = R12.G
    cv, x, y, w, h = R12.frame_b(T, "code", playing=False)
    cw = w - 340 - G_
    bx, by, bw, bh = R12.pane(cv, T, x, y, cw, h, focus=True, tabs=(("code", "main.py"),),
                              tools=R12.CODE_TOOLS)
    sh = 26
    first, cur, col = 172, 189, 12
    gap = 48 if overlay == "find" else 0
    if overlay == "crash":
        cur, col = 189, 4
    if overlay == "find":
        cur, col = 182, 8
    keep = R12.SKY_CODE[188]
    if overlay == "complete":
        R12.SKY_CODE[188], col = [("text", "        "), ("text", "sp"), ("text", "\n")], 10
    try:
        gut, adv = R12.code_view(cv, T, bx, by + gap, bw, bh - sh - gap, R12.SKY_CODE, first, cur,
                                 col)
    finally:
        R12.SKY_CODE[188] = keep
    status = {"crash": "Ln 189, Col 5  ·  Stopped here",
              "find": "Ln 182, Col 9  ·  Python"}.get(overlay, "Ln 189, Col 11  ·  202 lines  ·  "
                                                               "No issues  ·  Python")
    hints = {"find": (("enter", "Next"), ("shift+enter", "Back"), ("Esc", "Close")),
             "complete": ((("↑", "↓"), "Pick"), ("Tab", "Insert"), ("Esc", "Close")),
             "goto": ((("↑", "↓"), "Pick"), ("enter", "Go"), ("Esc", "Close"))}.get(
        overlay, (("F6", "Next pane"),))
    R12.status_line(cv, T, bx, by + bh - sh, bw, sh, status, hints)
    R12.run_panel(cv, T, x + cw + G_, y, 340, R12.RUN_H, still=overlay == "crash")
    R12.reference(cv, T, x + cw + G_, y + R12.RUN_H + G_, 340, h - R12.RUN_H - G_)
    ly = by + gap + 4 + (cur - first) * 14
    if overlay == "crash":
        cv.shape(bx + 2, ly + 4, 6, 6, lambda d, s: d.ellipse((0, 0, 6 * s - 1, 6 * s - 1), fill=255),
                 T["value"])
        stopped_popover(cv, T, bx + gut - 8, ly + 18, bw - gut + 2, desk=True)
    elif overlay == "find":
        f = R12.mono()
        a = f.glyph("m")[3]
        for i in range((bh - sh - gap - 6) // 14):
            ln = first + i
            s = "".join(t for _r, t in R12.SKY_CODE[ln - 1]) if ln <= len(R12.SKY_CODE) else ""
            k = s.find("spin")
            while k >= 0:
                yy = by + gap + 4 + i * 14
                on = ln == 182
                cv.rrect(bx + gut + k * a - 1, yy + 1, 4 * a + 2, 12, 2, T["value"], 0.42 if on else 0.18)
                if on:
                    cv.rect(bx + gut + k * a - 1, yy + 12, 4 * a + 2, 2, T["value"])
                k = s.find("spin", k + 1)
        find_bar(cv, T, bx, by, bw, 32, "spin", "1 of 3", desk=True)
    elif overlay == "complete":
        completion(cv, T, bx + gut + 8 * adv - 12, ly + 18, "sp",
                   (("spin", "var"), ("spr", "verb"), ("sprites", "var"), ("spawn_at", "def")), desk=True)
    elif overlay == "goto":
        defs = [(n, ln) for n, ln in ((("_init", 12), ("_spawn", 40), ("_hit", 77), ("_update", 102),
                                       ("_draw", 179), ("_hero", 205)))]
        goto(cv, T, bx + bw - 380, by - 2, 360, "", defs, focus=4, desk=True)
    return cv


# ==========================================================================
# the compact bar: a tool cart on a separate game canvas keeps 18 rows
# ==========================================================================
def compact_bar(cv, T, x, y, w, h, title="Egg Timer", desk=False):
    """The bar a tool cart keeps on a separate game canvas, drawn by the
    shell at the glass's resolution and metrics: the ground, the hairline,
    the bar's Moy tile, ‹ and the cart's title, the battery and the clock.
    The cart still lays out under its 18 rows; the band is `h` tall, the
    canvas's 18 rows at 2x on the desk, and on the S3 those rows and as
    much of the letterbox above them, so ‹ and the title are a 36 px tap."""
    cv.rect(x, y, w, h, T["bg"])
    cv.rect(x, y + h - 1, w, 1, T["line"])
    t = h - 6
    sys_fill, body, eye = T["hhbar"]["sys"] or T["bar"]["sys"] or (T["raised"], T["value"], T["raised"])
    cv.rrect(x + 4, y + 3, t + 2, t, 8, sys_fill)
    M.moy(cv, x + 5, y + 4, t - 2, body, eye)
    ft = Face(T["fam"], 20 if desk else 15, T["w_semi"], 4)
    fs = Face(T["fam"], 17 if desk else 13, T["w_med"], 4 if desk else 2)
    s = 20 if desk else 16
    ix = x + t + 14
    sicon(cv, T, "back", ix, y + (h - s) // 2, s, T["text"])
    cv.text(ix + s + 4, M.mid_base(ft, y, h), title, ft, T["text"])
    tw = cv.text(x + w - 8, M.mid_base(fs, y, h), "14:32", fs, T["text"], align="right")
    sicon(cv, T, "battery", x + w - 8 - tw - s - 6, y + (h - s) // 2, s, T["text"])


def egg_timer(s=1):
    """A tool cart's 320x222 picture under its bar: a kid's egg timer in
    MOY64 colours (content)."""
    P = R13.P64
    Nn = R13.N
    cv = M.Cv(320, 240, P[Nn["dark_blue"]])
    f = M.Petme(4) if hasattr(M, "Petme") else Face("inter", 40, 700, 4)
    cv.text(160, 128, "03:20", f, P[Nn["yellow"]], align="center")
    g = M.Petme(1)
    cv.text(160, 168, "A START   B RESET", g, P[Nn["light_grey"]], align="center")
    cv.shape(144, 40, 32, 40, lambda d, k: d.ellipse((0, 0, 32 * k - 1, 40 * k - 1), fill=255),
             P[Nn["white"]])
    return cv.image()


def tool_canvas(T, desk=False):
    if desk:
        cv = M.Cv(1024, 600, "#000000")
        cv.img.paste(egg_timer().resize((640, 480), Image.NEAREST), (192, 60))
        compact_bar(cv, T, 192, 60, 640, 36, desk=True)
        return cv
    G = S3
    cv = M.Cv(G.W, G.H, "#000000")
    cv.img.paste(egg_timer(), (80, 40))
    compact_bar(cv, T, 80, 22, 320, 36)
    return cv


# ==========================================================================
# the dialogs: `ui.dialog` with its fields, dimming its app's surface
# ==========================================================================
def blocks_under(T, G=TDECK):
    with no_focus():
        cv = R12.hh_blocks(T) if G is TDECK else R16.hh_page(T, G, "Blocks")
    wash(cv, 0, 28, G.W, G.H - 28, T)
    return cv


def quiet_foot(cv, y0, y1):
    """A desk page's foot under a modal: the page's own key hints go (the
    modal's are the ones that work), the row above them carried down."""
    row = cv.img.crop((0, cv.L(y0 - 1), cv.img.width, cv.L(y0)))
    for yy in range(cv.L(y0), cv.L(y1)):
        cv.img.paste(row, (0, yy))


NAME_LINE = ("Use letters, numbers and underscores.",)
OK = (("Cancel", "normal", False), ("OK", "primary", False))


def blocks_name(T, G=TDECK):
    """Blocks' name prompt (a variable; a block, an input and a text say so
    in the title): one field, its name as typed. The T-Deck's keys are its
    hint strip; the S3 adds Cancel and OK."""
    cv = blocks_under(T, G)
    dialog(cv, T, G.W // 2, 112 if G is TDECK else 160, 296 if G is TDECK else 380,
           "Name your variable", body=NAME_LINE, fields=(("Name", "score", None),), buttons=OK,
           touch=G.touch)
    hh_hints(cv, T, G, (("enter", "OK"), (CANCEL, "Cancel")))
    return cv


def blocks_number(T, G=TDECK):
    """The number prompt: the field with the slot's value as its
    placeholder, and Use a block where the slot takes one -- a button on
    every board, since no key is free for it (the T-Deck's letters type, it
    has no Tab), which the trackball rolls down to. The keys type the
    digits, so no pad."""
    cv = blocks_under(T, G)
    dialog(cv, T, G.W // 2, 102 if G is TDECK else 160, 306 if G is TDECK else 380,
           "Type a number", fields=(("Move by", "", "10"),), buttons=OK,
           extra=("Use a block", "blocks"), touch=G.touch, always_extra=True)
    hh_hints(cv, T, G, (("enter", "OK"), (UD, "Use a block"), (CANCEL, "Cancel")))
    return cv


def blocks_desk(T):
    with no_focus():
        cv = R12.b_blocks(T)
    quiet_foot(cv, 561, 591)
    wash(cv, 0, 40, 1024, 560, T)
    dialog(cv, T, 512, 300, 440, "Name your variable", body=NAME_LINE,
           fields=(("Name", "score", None),), buttons=OK, desk=True,
           hints=(("enter", "OK"), ("Esc", "Cancel")))
    return cv


def cart_info(T, desk=False):
    """Make it mine's Cart info: the title and who made it."""
    with no_focus():
        cv = R12.b_config(T) if desk else R12.hh_config(T)
    if desk:
        quiet_foot(cv, 561, 591)
        wash(cv, 0, 40, 1024, 560, T)
        cx, cy, w = 512, 300, 460
    else:
        wash(cv, 0, 26, 320, 214, T)
        cx, cy, w = 160, 112, 296
    dialog(cv, T, cx, cy, w, "Cart info",
           fields=(("Title", "Sky Run", None), ("Made by", "", "Your name (you can leave it)")),
           buttons=(("Cancel", "normal", False), ("Save", "primary", False)), desk=desk,
           hints=(("Tab", "Next"), ("enter", "Save"), ("Esc", "Cancel")))
    if not desk:
        hh_hints(cv, T, TDECK, ((UD, "Field"), ("enter", "Save"), (CANCEL, "Cancel")))
    return cv


def new_script(T):
    with no_focus():
        cv = R12.hh_config(T)
    wash(cv, 0, 26, 320, 214, T)
    dialog(cv, T, 160, 112, 296, "New script", body=("A Python file beside main.py",),
           fields=(("Name", "", "helpers"),),
           buttons=(("Cancel", "normal", False), ("Add", "primary", False)))
    hh_hints(cv, T, TDECK, (("enter", "Add"), (CANCEL, "Cancel")))
    return cv


PROJECT = (("Code", (("main.py", "code", "Code"), ("helpers.py", "code", "Code"),
                     ("New script", "new", None))),
           ("Art and sound", (("Sprites", "sprite", "Art"), ("Map", "map", "World"),
                              ("Sounds", "music", "Sound"), ("cover.png", "art", "Paint"))))


def project_files(T, G=TDECK):
    """The project's files as a page of Make it mine (its This project
    group's Files row): a group a kind, each file's icon, its name and the
    tab it opens in; New script ends the Code group where the cart takes
    more than one."""
    cv = R16.hh_page(T, G, "Files")
    fl = Face(T["fam"], 13, T["w_med"], 2)
    fv = F(T, "status", HH)
    rh = G.row
    y = 34
    first = True
    for cap, rows in PROJECT:
        R13.caption(cv, T, 16, y + 10, cap)
        y += 14
        cv.rrect(6, y, G.W - 12, rh * len(rows), 8, T["surface"])
        if T["mode"] == "dark" or contrast(T):
            cv.ring(6, y, G.W - 12, rh * len(rows), 8, 1 if not contrast(T) else 2,
                    T["line"] if not contrast(T) else T["text"])
        for i, (name, slot, where) in enumerate(rows):
            ry = y + i * rh
            if i:
                cv.rect(16, ry, G.W - 32, 1, T["line"])
            ink = M.focus_mark(cv, T, (6, ry, G.W - 12, rh, 0), "part") if first and i == 0 else None
            new = where is None
            sicon(cv, T, slot, 16, ry + (rh - 14) // 2, 14,
                  ink or (T["value"] if new and not contrast(T) else T["dim"]))
            cv.text(38, M.mid_base(fl, ry, rh), name, fl, ink or T["text"])
            if not new:
                R11._value(cv, T, "link", where, G.W - 16, ry, rh, fv, False)
        first = False
        y += rh * len(rows) + 4
    hh_hints(cv, T, G, (("enter", "Open"), (BACK, "Back")))
    return cv


# ==========================================================================
# the saver: a game's cover every six seconds, the clock, on black
# ==========================================================================
SAVER_INK, SAVER_DIM, SAVER_LINE = "#eef0fb", "#8d92b8", "#3a3f6b"


def saver(G=TDECK, cart="star_catcher", title="Star Catcher"):
    """The saver in every theme: black, the cover at its largest whole
    scale in a 1 px frame (a cover's own black would melt into the
    ground), its title under it, the clock small in the corner. One flat frame
    every six seconds, nothing between."""
    cv = M.Cv(G.W, G.H, "#000000")
    sc = 2 if G.H <= 240 else 3
    n = 64 * sc
    x = (G.W - n) // 2
    y = (G.H - n - 34) // 2
    cv.paste(M.cover(cart, 64, sc), x, y, r=10)
    cv.ring(x - 1, y - 1, n + 2, n + 2, 11, 1, SAVER_LINE)
    ft = Face("inter", 17 if G.H <= 240 else 20, 600, 4)
    cv.text(G.W // 2, y + n + 26, title, ft, SAVER_INK, align="center")
    fm = Face("inter", 12, 500, 2)
    cv.text(G.W - 10, 18, "14:32", fm, SAVER_DIM, align="right")
    return cv


# ==========================================================================
# the web console: the QR, the address and its pin, and the way out
# ==========================================================================
URL = "http://192.168.1.42/?pin=4821"


def qr(cv, x, y, side):
    """The QR at a whole module size inside `side`, on its white quiet
    zone whatever the theme (a scanner needs dark on light)."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "moy_qr", os.path.join(R7.REPO, "runtime", "moy_qr.py"))
    moy_qr = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(moy_qr)
    m = moy_qr.encode(URL)
    n = len(m)
    mod = side // (n + 4)
    full = mod * (n + 4)
    ox, oy = x + (side - full) // 2, y + (side - full) // 2
    cv.rrect(ox, oy, full, full, 6, "#ffffff")
    for r, row in enumerate(m):
        for c, v in enumerate(row):
            if v:
                cv.rect(ox + (c + 2) * mod, oy + (r + 2) * mod, mod, mod, "#000000")
    return full


def web_console(T, G=TDECK, desk=False):
    """The glass while the web console is on: the QR to scan, the address
    and pin to type, and Turn off (Esc on a keyboard; a button where the
    glass is touched or pointed at)."""
    if desk:
        cv = M.new_cv(T, True)
        R11.console_bar(cv, T)
        W, H, top = 1024, 600, 40
    else:
        cv = R16.hh_page(T, G, "Web console")
        W, H, top = G.W, G.foot, 28
    big = desk or G.touch
    side = 300 if desk else (180 if G.touch else 140)
    qx = (W - side - (380 if desk else (230 if G.touch else 160))) // 2
    qy = top + (H - top - side) // 2 - (0 if desk else 2)
    qr(cv, qx, qy, side)
    tx = qx + side + (40 if desk else 18)
    ft = Face(T["fam"], 26 if desk else (17 if G.touch else 14), T["w_semi"], 4)
    fb = F(T, "row", DK) if desk else Face(T["fam"], 13 if G.touch else 12, T["w_body"], 2)
    fa = Face("jbmono", 20 if desk else (14 if G.touch else 12), 500, 4)
    fp = Face(T["fam"], 40 if desk else (26 if G.touch else 22), T["w_bold"], 4)
    lh = 26 if desk else (18 if G.touch else 15)
    y = qy + (30 if desk else 18)
    cv.text(tx, y, "Open in a browser", ft, T["text"])
    y += lh + (8 if desk else 4)
    for ln in ("Scan this with a phone,", "or on a computer go to"):
        cv.text(tx, y, ln, fb, T["dim"] if not contrast(T) else T["text"])
        y += lh
    y += 6 if desk else 4
    cv.text(tx, y, "192.168.1.42", fa, T["value"] if not contrast(T) else T["text"])
    y += lh + (18 if desk else 10)
    R13.caption(cv, T, tx, y, "Pin", desk=desk)
    y += (44 if desk else 28)
    cv.text(tx, y, "4821", fp, T["text"])
    if big:
        bw, bh = (150, 40) if desk else (120, 30)
        R13.button(cv, T, "normal", tx, y + (24 if desk else 14), bw, bh, "Turn off", "power",
                   desk=desk)
    hh_hints(cv, T, G, ((BACK, "Turn off"),)) if not desk else None
    return cv


# ==========================================================================
# the text console: a script's screen
# ==========================================================================
SCRIPT = (("out", "Hello! I'm Ada's first script."), ("in", "What is your name? ", "Ada"),
          ("out", "Nice to meet you, Ada."), ("in", "How old are you? ", "9"),
          ("out", "In 10 years you will be 19."), ("out", "Counting stars: 1 2 3 4 5 6 7 8"),
          ("out", "That is a lot of stars."))


def text_console_body(cv, T, x, y, w, h, desk=False, done=False):
    """The scrollback in the code face on the code surface: what the script
    prints in the text ink, what was typed in `value`; the prompt line on a
    field-like band at the foot, the caret in `accent`."""
    S = R12.syn(T)
    cv.rect(x, y, w, h, S["bg"])
    f = mono(T, desk)
    adv = f.glyph("m")[3]
    lh = 22 if desk else 14
    ph = 34 if desk else 22
    yy = y + (12 if desk else 8)
    pad = 16 if desk else 8
    for item in SCRIPT:
        base = yy + (lh + f.cap) // 2 - 1
        if item[0] == "out":
            cv.text(x + pad, base, item[1], f, T["text"])
        else:
            cv.text(x + pad, base, item[1], f, T["dim"])
            cv.text(x + pad + len(item[1]) * adv, base, item[2], f, T["value"])
        yy += lh
    by = y + h - ph
    cv.rect(x, by, w, 1, T["line"])
    if done:
        fm = F(T, "meta", DK) if desk else F(T, "status", HH)
        cv.text(x + pad, M.mid_base(fm, by, ph), "The script ended.", fm, T["dim"])
        return
    cv.rect(x, by + 1, w, ph - 1, mix(S["bg"], T["text"], 0.05))
    base = M.mid_base(f, by, ph)
    q = "Pick a number: "
    cv.text(x + pad, base, q, f, T["dim"])
    cv.text(x + pad + len(q) * adv, base, "7", f, T["value"])
    cv.rect(x + pad + (len(q) + 1) * adv + 1, by + (ph - f.cap) // 2 - 3, 2 if desk else 1, f.cap + 6,
            T["accent"])


def text_console(T, done=False):
    cv = R16.hh_page(T, TDECK, "first.py")
    text_console_body(cv, T, 0, 28, 320, 190, done=done)
    hh_hints(cv, T, TDECK, (("enter", "Run again"), (BACK, "Back")) if done else
             (("enter", "Send"), (CANCEL, "Stop")))
    return cv


def text_console_desk(T):
    cv = R13.desk(T, (("code", "first.py"),))
    x, y, w, h = 212, 60, 600, 420
    top = R13.win(cv, T, x, y, w, h, "first.py", "code")
    body = T["win"]["body"] or T["surface"]
    text_console_body(cv, T, x + 8, top, w - 16, y + h - top - 44, desk=True)
    R13.desk_foot(cv, T, x, y + h - 44, w, "Running  ·  Python",
                  verbs=(("Stop", None, "normal"),))
    return cv


# ==========================================================================
# the home's arrangements: grid (a shelf of cards) and icons
# ==========================================================================
GRID = (("sky_run", "Sky Run"), ("star_catcher", "Star Catcher"), ("hop_quest", "Hop Quest"),
        ("pixel_pet", "Pixel Pet"), ("harpoon_pop", "Harpoon Pop"), ("brick_siege", "Brick Siege"),
        ("bullet_storm", "Bullet Storm"), ("letter_blitz", "Letter Blitz"), ("tiny_runner", "Tiny Runner"),
        ("sky_run", "Sky Run 2"))


def flat_focus(cv, T, im, x, y, n, r, grow=3):
    """Focus on a card that moves with every focus step, flat: the picture
    grown a few pixels, ringed in `accent` at a gap of the ground, and no
    soft shadow over its neighbours."""
    g = grow
    R13.paste_rect(cv, im, x - g, y - g, n + 2 * g, n + 2 * g, r + g)
    cv.ring(x - g - 4, y - g - 4, n + 2 * g + 8, n + 2 * g + 8, r + g + 4, 2.5 if n > 48 else 2,
            T["text"] if contrast(T) else T["accent"])


def grid_card(cv, T, x, y, n, name, title, focus, ft, desk=False):
    """A cart's card on the shelf: `ui.card` -- the cover at a whole scale
    with the card radius, its title under it cut to the card's width --
    the focused one grown and ringed, its title bold."""
    sc = n // 64
    im = M.cover(name, 64, sc) if sc > 1 else M.cover(name, 64)
    r = 10 if desk else 8
    if focus:
        flat_focus(cv, T, im, x, y, n, r, 4 if desk else 3)
        ft = Face(T["fam"], ft.size, T["w_bold"], ft.bpp)
    else:
        R13.paste_rect(cv, im, x, y, n, n, r)
        cv.ring(x, y, n, n, r, 1, R13.edge(T))
    t = R16.wrap(ft, title, n, 1)[0]
    cv.text(x + n // 2, y + n + (26 if desk else 17), t, ft, T["text"], align="center")


def home_grid(T, G=TDECK):
    """The grid arrangement: the shelf as cards in rows that scroll
    sideways, the last column cut at the edge so the shelf reads as going
    on; Enter plays, C changes (no buttons on the card)."""
    cv = M.Cv(G.W, G.H, T["home_bg"] or T["bg"], k=1, aa=True)
    M.backdrop(cv, T, "home")
    M.hh_bar(cv, T)
    if G is TDECK:
        n, pitch, rows, y0, rh, x0 = 64, 88, 2, 42, 92, 16
    else:
        n, pitch, rows, y0, rh, x0 = 64, 84, 3, 42, 92, 18
    ft = Face(T["fam"], 12, T["w_semi"], 2)
    k = 0
    cols = (G.W - x0) // pitch + 1
    for c in range(cols):
        for r in range(rows):
            if k >= len(GRID):
                k = 0
            name, title = GRID[(c * rows + r) % len(GRID)]
            grid_card(cv, T, x0 + c * pitch, y0 + r * rh, n, name, title, c == 1 and r == 0, ft)
    if not G.touch:
        R13.fade(cv, T, 0, 206, G.W, 12)
        cv.rect(0, 218, G.W, 22, T["bg"])
        R13.hh_hints(cv, T, (("enter", "Play"), ("C", "Change"), (SEARCH, "Search")))
    return cv


ICON_META = {"Bullet Storm": "Shooter  ·  Built in"}


def icon_cells(G, desk):
    """(tile, column pitch, row pitch, columns, rows, x0, y0, foot) for the
    icons arrangement on a glass."""
    if desk:
        t, p, rp, y0, foot = 64, 96, 92, 64, 600 - 64
    elif G is TDECK:
        t, p, rp, y0, foot = 40, 58, 48, 36, 190
    else:
        t, p, rp, y0, foot = 52, 70, 76, 44, 284
    W = 1024 if desk else G.W
    cols = (W - 16) // p
    rows = (foot - y0) // rp + 1
    x0 = (W - cols * p) // 2 + (p - t) // 2
    return t, p, rp, cols, rows, x0, y0, foot


def icons_canvas(T, G, desk):
    if desk:
        cv = M.new_cv(T, True, T["home_bg"] or T["bg"])
        M.backdrop(cv, T, "home")
        R11.console_bar(cv, T)
    else:
        cv = M.Cv(G.W, G.H, T["home_bg"] or T["bg"], k=1, aa=True)
        M.backdrop(cv, T, "home")
        M.hh_bar(cv, T)
    return cv


def home_icons(T, G=TDECK, desk=False, variant="caption"):
    """The icons arrangement: each cart its icon (the cover's reduction) in
    a tile with the card radius, the focused one grown and ringed. Three
    ways to name it:

    - `caption` (the handheld's and the S3's): no label on any tile; the
      focused cart's name and its line sit in one caption band at the grid's
      foot, which clips the top of the next row (the grid scrolls) and which a
      focus move repaints with the two tiles and nothing else.
    - `labels` (the desk's): every tile its title under it, whole at the
      desk's ten columns, the focused one's bold.
    - `pop`: the focused tile popped out, its name's pill drawn over the
      row below it (a focus move then repaints the tiles the pill covers
      too)."""
    cv = icons_canvas(T, G, desk)
    t, p, rp, cols, rows, x0, y0, foot = icon_cells(G, desk)
    if variant == "labels":
        rp += 16 if desk else 14
        rows = (foot - y0 + (24 if desk else 18)) // rp + 1
    fl = F(T, "label", DK) if desk else Face(T["fam"], 11, T["w_med"], 2)
    fp = Face(T["fam"], 15 if desk else 12, T["w_semi"], 4 if desk else 2)
    focus_k = cols + 1
    pop = None
    k = 0
    for r in range(rows):
        for c in range(cols):
            name, title = GRID[k % len(GRID)]
            x, y = x0 + c * p, y0 + r * rp
            im = M.cover(name, 64).resize((t, t), Image.BOX) if t != 64 else M.cover(name, 64)
            if k == focus_k:
                flat_focus(cv, T, im, x, y, t, 12 if desk else 10)
                pop = (x, y, title)
            else:
                R13.paste_rect(cv, im, x, y, t, t, 12 if desk else 10)
                cv.ring(x, y, t, t, 12 if desk else 10, 1, R13.edge(T))
            if variant == "labels":
                f = Face(T["fam"], fl.size, T["w_bold"], fl.bpp) if k == focus_k else fl
                lab = R16.wrap(f, title, p - 4, 1)[0]
                cv.text(x + t // 2, y + t + (20 if desk else (17 if G.touch else 14)), lab, f, T["text"],
                        align="center")
            k += 1
    if variant == "labels" and not desk:
        R13.fade(cv, T, 0, (218 if not G.touch else G.H) - 14, G.W, 14)
    if variant == "pop" and pop:
        x, y, title = pop
        fw = fp.width(title) + (28 if desk else 18)
        ph = 28 if desk else 20
        px, py = x + t // 2 - fw // 2, y + t + (10 if desk else 8)
        cv.rrect(px, py + 2, fw, ph, ph / 2, mix(T["bg"], "#000000", 0.45))
        cv.rrect(px, py, fw, ph, ph / 2, T["text"])
        cv.text(x + t // 2, M.mid_base(fp, py, ph), title, fp, T["bg"], align="center")
    if variant == "caption" and pop:
        title = pop[2]
        W = 1024 if desk else G.W
        by = foot + (6 if desk else 2)
        cv.rect(0, foot, W, (600 if desk else (G.H if G.touch else 218)) - foot, T["home_bg"] or T["bg"])
        cv.rect(0, by - 2, W, 1, T["line"])
        ft = Face(T["fam"], 20 if desk else (15 if G.touch else 14), T["w_semi"], 4)
        fm = F(T, "meta", DK) if desk else Face(T["fam"], 12, T["w_body"], 2)
        base = by + (34 if desk else 20)
        w = cv.text(x0, base, title, ft, T["text"])
        cv.text(x0 + w + (14 if desk else 10), base, ICON_META.get(title, "Built in"), fm, T["dim"])
    if not desk and not G.touch:
        cv.rect(0, 218, G.W, 22, T["bg"])
        R13.hh_hints(cv, T, (("enter", "Play"), ("C", "Change"), (SEARCH, "Search")))
    return cv


def home_grid_desk(T):
    """The console mode's home in the grid arrangement: 128 px cards, three
    rows, the shelf bleeding off the right edge."""
    cv = M.new_cv(T, True, T["home_bg"] or T["bg"])
    M.backdrop(cv, T, "home")
    R11.console_bar(cv, T)
    ft = F(T, "row", DK)
    n, pitch, rh = 128, 160, 172
    for c in range(7):
        for r in range(3):
            name, title = GRID[(c * 3 + r) % len(GRID)]
            grid_card(cv, T, 48 + c * pitch, 64 + r * rh, n, name, title, c == 1 and r == 0,
                      ft, desk=True)
    return cv


# ==========================================================================
# the Editor in theme mode: a theme package opened as Config (its
# theme.json) and Sprites (its icon sheet), nothing a cart has
# ==========================================================================
THEME_JSON = (
    "{",
    '  "format": 2,',
    '  "name": "custom",',
    '  "look": { "from": "moy", "accent": "#e0517a" },',
    '  "colors": {',
    '    "light": { "accent": "#e0517a" },',
    '    "dark": { "accent": "#ff8fb0" }',
    "  },",
    '  "icons": {',
    '    "set": "default",',
    '    "pixels": "icons.moygfx",',
    '    "pixel_slots": ["play", "home"]',
    "  },",
    '  "wallpapers": ["garden.moy"]',
    "}",
)
SLOT_NAMES = {"wifi": "Wi-Fi", "wifi_off": "Wi-Fi off", "getcarts": "Get Carts", "cart_py": "Python cart",
              "cart_lua": "Lua cart", "cart_wasm": "Compiled cart", "cart_p8": "PICO-8 cart"}
SHEET = [sl for sl in R7.ALL if sl not in ("default", "coast", "luna")]


def slot_name(sl):
    return SLOT_NAMES.get(sl, sl.replace("_", " ").capitalize())


def json_tokens(line, S):
    """A theme.json line as (text, colour): keys in `fn`, strings in `str`,
    numbers in `num`, the rest in `text`."""
    out, i = [], 0
    while i < len(line):
        ch = line[i]
        if ch == '"':
            j = line.index('"', i + 1) + 1
            rest = line[j:].lstrip()
            out.append((line[i:j], S["fn"] if rest.startswith(":") else S["str"]))
            i = j
        elif ch.isdigit():
            j = i
            while j < len(line) and line[j].isdigit():
                j += 1
            out.append((line[i:j], S["num"]))
            i = j
        else:
            out.append((ch, S["text"]))
            i += 1
    return out


def json_view(cv, T, x, y, w, h, desk=False, cur=4, col=24):
    """theme.json in the console's text app's json mode: the code face on
    the code surface, the gutter, the current line's mark."""
    S = R12.syn(T)
    cv.rect(x, y, w, h, S["bg"])
    f = mono(T, desk)
    adv = f.glyph("m")[3]
    lh = 22 if desk else 14
    gut = 34 if desk else 22
    cv.set_clip(x, y, w, h)
    for i, line in enumerate(THEME_JSON):
        yy = y + 4 + i * lh
        if yy + lh > y + h:
            break
        base = yy + (lh + f.cap) // 2 - 1
        on = i + 1 == cur
        if on:
            M.current_mark(cv, T, x, yy, w, lh, 0)
            cv.rect(x, yy, 2, lh, T["value"])
        cv.text(x + gut - 8, base, str(i + 1), f, S["text"] if on else S["gutter"], align="right")
        cx = x + gut
        for t, c in json_tokens(line, S):
            cv.text(cx, base, t, f, c)
            cx += len(t) * adv
        if cx > x + w - 8:
            for j_, al in enumerate((0.35, 0.6, 0.85)):
                cv.rect(x + w - 8 - (3 - j_) * 4, yy, 4, lh, S["bg"], al)
            cv.rect(x + w - 8, yy, 8, lh, S["bg"])
        if on:
            cv.rect(x + gut + col * adv, yy + 2, 2 if desk else 1, lh - 4, T["accent"])
    cv.clip = None


def theme_bar(cv, T, cur, W=320, h=26, touch=False, edited=False):
    """The Editor's bar in theme mode, by the regular Editor's rule: Moy, ‹
    (Done, a 28 px target), the two tabs as icons with the open one
    labelled on the current plate, the theme's name, undo and redo. No
    Play: an edit applies live and there is nothing to run."""
    cv.rect(0, 0, W, h, T["bg"])
    cv.rect(0, h - 1, W, 1, T["line"])
    sys_fill, body, eye = T["hhbar"]["sys"] or T["bar"]["sys"] or (T["raised"], T["value"], T["raised"])
    t = h - 6
    cv.rrect(4, 3, t + 2, t, 7, sys_fill)
    M.moy(cv, 6, 4, t - 2, body, eye)
    x = t + 8
    lf = Face(T["fam"], 13 if touch else 11, T["w_semi"], 2)
    s = 16
    sicon(cv, T, "back", x + (6 if touch else 2), (h - s) // 2, s, T["text"])
    x += 28 if touch else 20
    for tab, slot, label in (("config", "sliders", "Config"), ("sprites", "sprite", "Sprites")):
        th = h - 6
        if tab == cur:
            tw = s + 6 + lf.width(label) + 8 + (8 if touch else 0)
            ink = M.current_mark(cv, T, x, 3, tw, th, 5) or T["text"]
            sicon(cv, T, slot, x + 4, (h - s) // 2, s, ink)
            cv.text(x + 4 + s + 4, M.mid_base(lf, 3, th), label, lf, ink)
        else:
            tw = s + (12 if touch else 6)
            sicon(cv, T, slot, x + (tw - s) // 2, (h - s) // 2, s, T["text"])
        x += tw + 2
    fs = Face(T["fam"], 12, T["w_med"], 2)
    cv.rect(x + 4, 7, 1, h - 14, T["line"])
    if W > 320:
        sicon(cv, T, "palette", x + 10, (h - 14) // 2, 14, T["dim"])
        x += 18
    cv.text(x + 10, M.mid_base(fs, 0, h), "custom", fs, T["dim"])
    w = cv.text(W - 4, M.mid_base(fs, 0, h), "14:32", fs, T["text"], align="right")
    bx = W - 4 - w - 19
    sicon(cv, T, "battery", bx, (h - 16) // 2, 16, T["text"])
    ax = bx - 6 - 2 * (s + 6)
    sicon(cv, T, "undo", ax, (h - s) // 2, s, T["text"] if edited else mix(T["dim"], T["bg"], 0.55))
    sicon(cv, T, "redo", ax + s + 6, (h - s) // 2, s, mix(T["dim"], T["bg"], 0.55))
    cv.rect(ax - 6, 7, 1, h - 14, T["line"])


TAB_KEYS = (BALL, "Tabs and ‹")


def theme_config(T, G=TDECK, copied=True):
    """Config: the theme's theme.json in the json mode (Tab indents; the
    Editor's Ctrl+Tab changes tab, the bar's tabs on a T-Deck), a save
    written through the look
    (`Appearance.file_write`), never the project save, so the package keeps
    no journal/; opened on a seed, the status line says once whose copy it
    is, in `value`, and settles to the file's state."""
    touch = G.touch
    W, H = G.W, G.H
    cv = M.Cv(W, H, T["bg"])
    bh = 32 if touch else 26
    theme_bar(cv, T, "config", W, bh, touch)
    foot = H if touch else 218
    sh = 18
    json_view(cv, T, 0, bh, W, foot - bh - sh)
    fm = F(T, "meta", HH)
    cv.rect(0, foot - sh, W, 1, T["line"])
    if copied:
        sicon(cv, T, "dup", 8, foot - sh + 3, 12, T["value"] if not contrast(T) else T["text"])
        cv.text(24, M.mid_base(fm, foot - sh, sh), "Moy is built in: this is your copy, custom",
                fm, T["value"] if not contrast(T) else T["text"])
    else:
        cv.text(8, M.mid_base(fm, foot - sh, sh), "theme.json  ·  1.9 of 4 KB  ·  Saved", fm, T["dim"])
    hh_hints(cv, T, G, (TAB_KEYS, ("Mic", "Menu")))
    return cv


PIXELS_DONE = ("play",)
RED = 8                     # the pencil's colour in these pictures: the roof's red


def sheet_ink(T):
    """A smooth slot's ink in the sheet: the text's, eased in a dark mode so
    a filled glyph (Moy) is not the loudest thing on it."""
    return mix(T["text"], T["bg"], 0.3) if T["mode"] == "dark" and not contrast(T) else T["text"]


def slot_tile(cv, T, sl, x, y, cell, pixels, focus=False, sc=1, ring=False):
    """A slot of the sheet: a smooth slot drawn from its mask, a slot in
    pixels drawn from the sheet with a `value` dot in its corner; the
    focused one on the focus mark (a ring on the desk)."""
    if focus:
        if ring:
            cv.ring(x - 4, y - 4, cell + 8, cell + 8, 7, 2, T["text"] if contrast(T) else T["accent"])
        else:
            M.focus_mark(cv, T, (x - 2, y - 2, cell + 4, cell + 4, 5), "small")
    o = (cell - 16 * sc) // 2
    if sl in pixels and R7.pixel_icon(sl) is not None:
        im = R7.pixel_icon(sl)
        if sc > 1:
            im = im.resize((16 * sc, 16 * sc), Image.NEAREST)
        cv.img.paste(im, (cv.L(x + o), cv.L(y + o)), im)
        pixel_dot(cv, T, x + cell - (5 if sc > 1 else 4), y, 4 if sc > 1 else 3)
    else:
        sicon(cv, T, sl, x + o, y + o, 16 * sc, sheet_ink(T))


def pixel_dot(cv, T, x, y, d):
    cv.rrect(x, y, d, d, d / 2, T["value"] if not contrast(T) else T["text"])


def mask_grid(cv, T, x, y, cell, sl, pixels):
    """The slot at its zoom. In pixels: its tiles' colours on the grey
    checker. Still smooth: its mask on a plain ground in solid ink, a cell
    set where the canvas would set it, so what painting starts from is
    plain."""
    if sl in pixels and R7.pixel_icon(sl) is not None:
        R7.checker(cv, x, y, 16 * cell, 16 * cell, cell)
        im = R7.pixel_icon(sl)
        for py in range(16):
            for px in range(16):
                c = im.getpixel((px, py))
                if c[3]:
                    cv.rect(x + px * cell, y + py * cell, cell, cell, "#%02x%02x%02x" % c[:3])
    else:
        cv.rect(x, y, 16 * cell, 16 * cell, T["surface"])
        cv.ring(x, y, 16 * cell, 16 * cell, 0, 1, mix(T["line"], T["dim"], 0.2))
        tmp = M.Cv(16, 16, "#000000")
        sicon(tmp, T, sl, 0, 0, 16, "#ffffff")
        im = tmp.image().convert("L")
        for py in range(16):
            for px in range(16):
                if im.getpixel((px, py)) >= 128:
                    cv.rect(x + px * cell, y + py * cell, cell, cell, T["text"])
    line = mix(T["line"], T["dim"], 0.3)
    cv.rect(x + 8 * cell, y, 1, 16 * cell, line)
    cv.rect(x, y + 8 * cell, 16 * cell, 1, line)
    cx, cy = x + 6 * cell, y + 3 * cell
    if sl in pixels:
        cv.rect(cx, cy, cell, cell, "#%02x%02x%02x" % R7.PAL[RED])
    R13.ring_cursor(cv, T, cx, cy, cell, cell)


def slot_state(sl, pixels):
    return "%s  ·  pixels  ·  Undo brings it back" % slot_name(sl) if sl in pixels else \
        "%s  ·  smooth" % slot_name(sl)


def preview(cv, T, sl, x, y, pixels, sc=1):
    """The slot at 1x as the shell draws it (2x beside it on the desk)."""
    if sl in pixels and R7.pixel_icon(sl) is not None:
        im = R7.pixel_icon(sl)
        cv.img.paste(im, (cv.L(x), cv.L(y)), im)
    else:
        sicon(cv, T, sl, x, y, 16, T["text"])


def theme_sprites(T, G=TDECK, edited=False):
    """Sprites: the theme's icon sheet as 16x16 tiles. A smooth set's slot is
    a 2-bit mask; painting it replaces the slot with a pixel tile in the
    package's sheet (`pixel_slots`), which the sheet marks with a dot (the
    legend under the sheet says so). Undo steps back within the session; a
    save is the look's write, with no history."""
    touch = G.touch
    W, H = G.W, G.H
    cv = M.Cv(W, H, T["bg"])
    bh = 32 if touch else 26
    theme_bar(cv, T, "sprites", W, bh, touch, edited)
    pixels = PIXELS_DONE + (("home",) if edited else ())
    cell = 13 if touch else 9
    gx, gy = 8, bh + 8
    mask_grid(cv, T, gx, gy, cell, "home", pixels)
    rx = gx + 16 * cell + 12
    step, tc = (32, 28) if touch else (19, 16)
    cols = (W - rx - 4) // step
    rows = 4
    fm = Face(T["fam"], 11, T["w_body"], 2)
    fn = Face(T["fam"], 13, T["w_semi"], 2)
    for i, sl in enumerate(SHEET[:cols * rows]):
        slot_tile(cv, T, sl, rx + (i % cols) * step, gy + (i // cols) * step, tc, pixels,
                  focus=sl == "home")
    yy = gy + rows * step + 2
    if touch:
        cv.text(W - 8, yy + 10, "1 of 2  ›", F(T, "status", HH), T["dim"], align="right")
    pixel_dot(cv, T, rx, yy + 6, 4)
    cv.text(rx + 8, yy + 11, "pixel art", fm, T["dim"])
    yy += 16
    preview(cv, T, "home", rx, yy + 2, pixels)
    cv.text(rx + 22, yy + 14, "Home", fn, T["text"])
    cv.text(rx, yy + 31, slot_state("home", pixels).split("  ·  ", 1)[1], fm, T["dim"])
    d = 26 if touch else 9
    swatch_row(cv, T, rx + 2, yy + (44 if touch else 40), d, RED, gap=5 if touch else 3, per=8)
    ty = gy + 16 * cell + 6
    tsz = 30 if touch else 22
    for i, slot in enumerate(("pencil", "bucket", "line", "rect", "select")):
        R13.ibtn(cv, T, slot, gx + i * (tsz + 4), ty, tsz, "current" if i == 0 else "rest")
    hh_hints(cv, T, G, ((("F", "V"), "Flip"), ("R", "Rotate"), (BALL, "Colour"), (BACK, "Done")))
    return cv


def theme_desk(T, edited=True):
    """The desk Editor in theme mode, maximised: the strip with the theme's
    name, Config and Sprites as its two workspaces' pills and as the panes'
    names, the open one the focused pane's (the tab follows focus, F6 moves
    it); undo and redo act on the focused pane; no Play. theme.json at the
    left, the sheet at the right with every slot's name under its tile, the
    open one ringed and at 16x beside its 1x and 2x."""
    W, H = 1024, 600
    cv = R12.new_canvas(T, W, H)
    R12.desk_bar(cv, T, (("palette", "Theme"),))
    y = T["bar"]["h"]
    S_ = R12.STRIP
    cv.rect(0, y, W, S_, T["surface"])
    cv.rect(0, y + S_ - 1, W, 1, T["line"])
    ft = F(T, "wtitle", DK)
    sicon(cv, T, "palette", 16, y + 12, 20, T["text"])
    cv.text(46, M.mid_base(ft, y, S_), "custom", ft, T["text"])
    f = Face(T["fam"], 15, T["w_med"], 4)
    x = 46 + 144
    for key, slot, label in (("config", "sliders", "Config"), ("sprites", "sprite", "Sprites")):
        x += R12.pill(cv, T, x, y + 4, S_ - 8, slot, label, key == "sprites", f) + 2
    s = 20
    for i, g in enumerate(("close", "maximize", "minimize")):
        sicon(cv, T, g, W - 14 - s - 34 * i, y + (S_ - s) // 2, s, T["text"])
    ux = W - 14 - s - 68 - 60
    sicon(cv, T, "undo", ux - 30, y + 12, 20, T["text"] if edited else T["dim"])
    sicon(cv, T, "redo", ux, y + 12, 20, mix(T["dim"], T["surface"], 0.55))
    top = y + S_
    R12.body_bg(cv, T, top)
    G_ = R12.G
    px, py, pw, ph = G_, top + G_, 352, H - top - 2 * G_
    bx, by, bw, bh = R12.pane(cv, T, px, py, pw, ph, title="Config", slot="sliders")
    json_view(cv, T, bx, by, bw, bh - 26, desk=True, cur=12, col=31)
    R12.status_line(cv, T, bx, by + bh - 26, bw, 26, "theme.json  ·  1.9 of 4 KB  ·  Saved")
    qx = px + pw + G_
    qw = W - G_ - qx
    bx, by, bw, bh = R12.pane(cv, T, qx, py, qw, ph, title="Sprites", slot="sprite", focus=True)
    pixels = PIXELS_DONE + (("home",) if edited else ())
    mask_grid(cv, T, bx + 16, by + 14, 16, "home", pixels)
    ty = by + 14 + 256 + 12
    for i, slot in enumerate(("pencil", "bucket", "line", "rect", "select")):
        R13.ibtn(cv, T, slot, bx + 16 + i * 38, ty, 32, "current" if i == 0 else "rest", desk=True)
    swatch_row(cv, T, bx + 18, ty + 46, 14, RED, gap=6, per=8)
    fl = Face(T["fam"], 11, T["w_med"], 2)
    pvx = bx + 16 + 176
    preview(cv, T, "home", pvx, ty + 48, pixels)
    big = R7.pixel_icon("home").resize((32, 32), Image.NEAREST) if "home" in pixels else None
    if big is not None:
        cv.img.paste(big, (cv.L(pvx + 26), cv.L(ty + 40)), big)
    else:
        sicon(cv, T, "home", pvx + 26, ty + 40, 32, T["text"])
    cv.text(pvx + 76, ty + 61, "1x  ·  2x", fl, T["dim"])
    sx = bx + 16 + 256 + 24
    cw, chh = 64, 56
    cols = (bx + bw - sx - 8) // cw
    for i, sl in enumerate(SHEET[:cols * 6]):
        cx, cy = sx + (i % cols) * cw, by + 12 + (i // cols) * chh
        slot_tile(cv, T, sl, cx + (cw - 32) // 2, cy, 32, pixels, focus=sl == "home", sc=2, ring=True)
        cv.text(cx + cw // 2, cy + 46, R16.wrap(fl, slot_name(sl), cw - 4, 1)[0], fl,
                T["text"] if sl == "home" else T["dim"], align="center")
    ly = by + 12 + 6 * chh + 4
    pixel_dot(cv, T, sx + 4, ly, 5)
    cv.text(sx + 14, ly + 6, "pixel art", fl, T["dim"])
    R12.status_line(cv, T, bx, by + bh - 26, bw, 26, slot_state("home", pixels),
                    ((("Q", "W"), "Icon"), ("F6", "Next pane")))
    return cv


def appearance_door(T, G=TDECK):
    """The door: Appearance's last row, Edit theme… (today's EDIT FILE…),
    which on a seed says it makes a copy."""
    cv = R16.hh_page(T, G, "Appearance")
    fl, fv = Face(T["fam"], 13, T["w_med"], 2), F(T, "status", HH)
    rh = G.row
    rows = R11.appearance_rows(T, False) + [("Edit theme…", "link", "Makes a copy")]
    R13.caption(cv, T, 16, 42, "Moy")
    R11.rows(cv, T, 6, 47, G.W - 12, rh, rows, len(rows) - 1, fl, fv, False)
    hh_hints(cv, T, G, (("enter", "Open"), (BACK, "Back")))
    return cv


def swatch_row(cv, T, x, y, d, cur, gap=2, per=16):
    """The palette; the chosen colour ringed white inside a dark ring, so
    it reads on any swatch."""
    for i, c in enumerate(R7.PAL[:16]):
        sx = x + (i % per) * (d + gap)
        y_ = y + (i // per) * (d + gap)
        cv.rrect(sx, y_, d, d, 3, "#%02x%02x%02x" % c)
        if i == cur:
            cv.ring(sx, y_, d, d, 3, 2, "#ffffff")
            cv.ring(sx - 2, y_ - 2, d + 4, d + 4, 4, 2, "#15161d")


# ==========================================================================
# the perf HUD: a chip a frame over the game, the breakdown on demand
# ==========================================================================
PERF = (("Draw", 14.2), ("Flush", 9.6), ("Logic", 5.1), ("Idle", 4.1))


def perf_chip(cv, T, xr, yb, desk=False, fps=30):
    """The fps chip: a flat pill (no shadow, no alpha: it repaints with
    every frame), the surface's polarity in every theme -- light in Day,
    dark in Night and Contrast -- with its edge, so it reads the same way
    over any game; the number in tabular digits."""
    f = Face("jbmono", 15 if desk else 11, 600, 4 if desk else 2)
    fl = Face(T["fam"], 12 if desk else 10, T["w_med"], 2)
    h = 26 if desk else 16
    num = "%d" % fps
    w = f.width(num) + fl.width(" fps") + (20 if desk else 12)
    x, y = xr - w, yb - h
    fill, ink = T["surface"], T["text"]
    cv.rrect(x, y, w, h, h / 2, fill)
    cv.ring(x, y, w, h, h / 2, 1, T["text"] if contrast(T) else mix(T["line"], T["dim"], 0.3))
    tw = cv.text(x + (10 if desk else 6), M.mid_base(f, y, h), num, f, ink)
    cv.text(x + (10 if desk else 6) + tw, M.mid_base(f, y, h), " fps", fl, T["dim"])
    return x, y, w, h


PERF_HUES = {"light": ("#138a7e", "#b7791f"), "dark": ("#5fd0c0", "#f0b85a")}


def perf_fill(cv, T, i, x, y, w, h, c):
    """A part's fill: its hue; in Contrast solid, hatched, or an outline."""
    if not contrast(T):
        cv.rect(x, y, w, h, c)
    elif i == 0:
        cv.rect(x, y, w, h, T["text"])
    elif i == 1:
        for k in range(0, w + h, 3):
            for yy in range(h):
                xx = k - yy
                if 0 <= xx < w:
                    cv.rect(x + xx, y + yy, 1, 1, T["text"])
    else:
        cv.ring(x, y, w, h, 0, 1, T["text"])


def perf_card(cv, T, xr, yb, desk=False):
    """The breakdown over the chip: the frame against its 33 ms budget as a
    bar of its parts, each part a hue of its own and its row in ms."""
    w = 176 if desk else 150
    rh = 20 if desk else 13
    f = Face("jbmono", 12 if desk else 10, 500, 4 if desk else 2)
    fl = Face(T["fam"], 13 if desk else 11, T["w_med"], 4 if desk else 2)
    pad = 10 if desk else 7
    bh = 6 if desk else 4
    h = pad + rh + bh + (8 if desk else 6) + rh * len(PERF) + pad
    x, y = xr - w, yb - h
    cv.rrect(x, y, w, h, 8 if desk else 6, T["surface"])
    cv.ring(x, y, w, h, 8 if desk else 6, 1 if not contrast(T) else 2,
            T["line"] if not contrast(T) else T["text"])
    tot = sum(v for _n, v in PERF)
    cv.text(x + pad, M.mid_base(fl, y + pad, rh), "Frame", fl, T["text"])
    cv.text(x + w - pad, M.mid_base(f, y + pad, rh), "%.1f ms" % tot, f, T["text"], align="right")
    by = y + pad + rh
    bw = w - 2 * pad
    teal, amber = PERF_HUES["dark" if T["mode"] == "dark" else "light"]
    cols = (T["value"], teal, amber)
    bx = x + pad
    cv.rect(bx, by, bw, bh, mix(T["line"], T["surface"], 0.2) if not contrast(T) else T["bg"])
    for i, ((_n, v), c) in enumerate(zip(PERF[:3], cols)):
        seg = int(bw * v / 33.3)
        perf_fill(cv, T, i, bx, by, seg - 1, bh, c)
        bx += seg
    yy = by + bh + (8 if desk else 6)
    for i, (name, v) in enumerate(PERF):
        d = 8 if desk else 6
        if i < 3:
            perf_fill(cv, T, i, x + pad, yy + (rh - d) // 2, d, d, cols[i])
        cv.text(x + pad + d + (8 if desk else 5), M.mid_base(fl, yy, rh), name, fl,
                T["dim"] if not contrast(T) else T["text"])
        cv.text(x + w - pad, M.mid_base(f, yy, rh), "%.1f" % v, f, T["text"], align="right")
        yy += rh


def perf_hud(T, detail=False):
    cv = M.Cv(320, 240, "#000000")
    cv.img.paste(R7.GAME, (0, 0))
    x, y, w, h = perf_chip(cv, T, 316, 236)
    if detail:
        perf_card(cv, T, 316, y - 4)
    return cv


def perf_hud_desk(T):
    cv = M.Cv(1024, 600, "#000000")
    cv.img.paste(R7.GAME.resize((640, 480), Image.NEAREST), (192, 60))
    x, y, w, h = perf_chip(cv, T, 1016, 592, desk=True)
    perf_card(cv, T, 1016, y - 8, desk=True)
    return cv


# ==========================================================================
# a theme with no look: a colours-only package draws the look flat
# ==========================================================================
def mint():
    """A store theme that carries its colours and nothing of the look: it
    takes the default look (Moy's geometry and marks) flat -- no shadow, no
    backdrop -- with every colour the look derives from its roles (the
    display, the operators, the Moy tile), never the kernel's pixel
    fallbacks."""
    T = copy.deepcopy(LIGHT)
    T.update(name="Mint (colours only)", key="mint", bg="#e6f2ec", surface="#ffffff",
             raised="#f0f7f3", sunk="#d9eae1", line="#c2d8cb", dim="#4d695b", text="#0f2419",
             accent="#0f7a58", value="#0f7a58", value_ink="#ffffff", shadow=None, backdrop={},
             home_bg=None)
    T["lcd"], T["lcd_ink"] = T["sunk"], T["text"]
    T["buttons"] = {"tonal": {"fill": T["sunk"]}}
    T["app_buttons"] = {}
    T["hhbar"] = dict(T["hhbar"], sys=(T["surface"], T["accent"], T["surface"]))
    T["bar"] = dict(T["bar"], sys=(T["sunk"], T["accent"], T["surface"]))
    return T


def mint_home():
    T = mint()
    cv = M.hh_home(T)
    hh_hints(cv, T, TDECK, (("enter", "Play"), ("C", "Change"), (SEARCH, "Search"),
                            (("Q", "E"), "Tabs")))
    return cv


def mint_settings():
    T = mint()
    cv = R16.settings_about(T, TDECK, focus=1)
    hh_hints(cv, T, TDECK, (("enter", "Open"), (BACK, "Back")))
    return cv


def mint_desk():
    return M.desktop(mint())


# ==========================================================================
# the kernel's own screens: recovery and its web console, drawn before the
# shell exists (the kernel's font path: moy_ui's built-in faces, flat fills)
# ==========================================================================
RECOVERY_ROWS = (("rotate", "Try again"), ("home", "Safe start"), ("code", "Developer console"))
DIAG = ("Last: LoadProhibited in moy_flush", "PC 400d1a2c at 400d19f0  ·  Up 12 s  ·  Boot 3",
        "Firmware 0.8.2")


def recovery(G=TDECK, desk=False, reason="It restarted 3 times in a row."):
    """The recovery screen in Moy Night whatever the stored theme (the store
    may be what failed): Moy, what happened in a sentence, the three ways
    on as rows (Try again, Safe start: the built-in carts alone, the
    developer console), what the kernel knows in the code face, and the
    serial words that do the same. One frame; nothing animates."""
    T = DARK
    W, H = (1024, 600) if desk else (G.W, G.H)
    cv = M.Cv(W, H, T["bg"])
    touch = G.touch and not desk
    s = 2 if desk else 1
    cw = 560 if desk else (W - 32)
    x = (W - cw) // 2
    y = 72 if desk else (22 if not touch else 30)
    n = 48 if desk else 24
    M.moy(cv, x, y, n, T["value"], T["bg"])
    ft = Face(T["fam"], 26 if desk else (17 if touch else 15), T["w_semi"], 4)
    fb = F(T, "row", DK) if desk else Face(T["fam"], 13 if touch else 12, T["w_body"], 2)
    tx = x + n + (16 if desk else 10)
    cv.text(tx, y + (22 if desk else 12), "Moybyte didn't start", ft, T["text"])
    cv.text(tx, y + (48 if desk else 29), reason, fb, T["dim"])
    y += n + (28 if desk else 12)
    rh = 40 if desk else (28 if touch else 22)
    cv.rrect(x, y, cw, rh * 3, 8, T["surface"])
    cv.ring(x, y, cw, rh * 3, 8, 1, T["line"])
    for i in range(1, 3):
        cv.rect(x + 10, y + i * rh, cw - 20, 1, T["line"])
    menu_rows(cv, T, x, y, cw, rh, RECOVERY_ROWS, 0, desk)
    y += rh * 3 + (24 if desk else 12)
    fc = Face("jbmono", 14 if desk else 10, 400, 4 if desk else 2)
    lh = 22 if desk else 13
    for ln in DIAG:
        cv.text(x, y + lh - 4, ln, fc, T["dim"])
        y += lh
    fm = F(T, "meta", DK) if desk else Face(T["fam"], 11, T["w_body"], 2)
    if touch:
        cv.text(x, H - 12, "Tap a choice, or type retry, safe or repl on serial.", fm, T["dim"])
    else:
        base = H - (20 if desk else 7)
        R11.hints(cv, T, ((("↑", "↓") if desk else UD, "Choose"), ("enter", "Start")), base, x=x,
                  bg=T["bg"], desk=desk)
        if desk:
            cv.text(x + cw, base, "Serial: retry, safe or repl", fm, T["dim"], align="right")
        else:
            cv.text(x, y + 8, "Serial: retry, safe or repl", fm, T["dim"])
    return cv


def kernel_web(T, G=TDECK, desk=False, state="ready"):
    """The kernel's web console screen (the shell's is `web_console`): the
    address in the code face and the pin, large, without the QR (the
    kernel has no encoder); while it waits for an address, or while the
    console restarts, a sentence says so in its place."""
    W, H = (1024, 600) if desk else (G.W, G.H)
    cv = M.Cv(W, H, T["bg"])
    ft = Face(T["fam"], 28 if desk else (18 if G.touch else 16), T["w_semi"], 4)
    fb = F(T, "row", DK) if desk else Face(T["fam"], 13, T["w_body"], 2)
    fa = Face("jbmono", 26 if desk else 15, 500, 4)
    fp = Face(T["fam"], 44 if desk else 26, T["w_bold"], 4)
    cx = W // 2
    n = 48 if desk else 28
    y = H // 2 - (150 if desk else 92)
    M.moy(cv, cx - n // 2, y, n, T["value"], T["bg"])
    y += n + (44 if desk else 26)
    cv.text(cx, y, "Web console", ft, T["text"], align="center")
    y += 32 if desk else 22
    if state == "ready":
        cv.text(cx, y, "Open this in a browser:", fb, T["dim"], align="center")
        y += 40 if desk else 24
        cv.text(cx, y, "192.168.1.42", fa, T["value"] if not contrast(T) else T["text"], align="center")
        y += 30 if desk else 20
        R13.caption(cv, T, cx, y, "Pin", desk=desk, align="center")
        y += 52 if desk else 30
        cv.text(cx, y, "4821", fp, T["text"], align="center")
    else:
        line = "Waiting for an address…" if state == "waiting" else "The console is restarting…"
        cv.text(cx, y + (12 if desk else 6), line, fb, T["dim"], align="center")
    return cv


# ==========================================================================
# the pictures
# ==========================================================================
BOOT = "Setting up carts  ·  12 of 40"
HANDHELD = (
    (lambda T: splash(T), "splash",
     "The boot splash: Moy at 4x over the wordmark, on the stored theme's ground"),
    (lambda T: splash(T, frac=0.3, status=BOOT), "splash-boot",
     "A first boot's splash: the track under the wordmark and what it is doing in dim"),
    (lambda T: loading(T), "loading",
     "The loading pill on purpose over the cover being opened, flat, the hint strip quiet (a "
     "load cannot be cancelled); kept by the panel through the load"),
    (lambda T: popover_hh(T, TDECK, "calendar"), "calendar",
     "The clock's calendar, hung from the bar, flat, no wash: the month, the weekdays, today a "
     "bold number on a value disc, the weekend in dim; ← → page a month"),
    (lambda T: popover_hh(T, TDECK, "power"), "power",
     "The battery's popover: the level and the time left, then Volume and Brightness as slider "
     "rows; focus on Brightness"),
    (lambda T: popover_hh(T, TDECK, "wifi"), "wifi",
     "The Wi-Fi popover: the popover's card holding Settings' Wi-Fi page -- the radio, the joined "
     "network checked, the others' signal and lock, Other network"),
    (lambda T: crash(T), "crash",
     "A game stopped: the stop card centred over the frozen game washed 60% -- Moy, the title and "
     "Stopped, the sentence, the error exact in the code face, See why, Restart and Home, its "
     "keys in its foot"),
    (crash_compiled, "crash-compiled",
     "A compiled cart's trap: the same card with no See why (no source line behind it)"),
    (lambda T: notice(T), "notice",
     "A cart this console cannot run, over the home washed whole: the same card, its words only "
     "what the sentence does not say, Open Updates and Home, its keys in its foot"),
    (crash_code, "crash-code",
     "See why: the Code tab on the stopped line, its popover hung under it after the gutter, the "
     "error exact; the first edit or Mic closes it"),
    (code_find, "code-find",
     "Find in the Code tab: the field under the bar, the count, Aa; the matches tinted, the "
     "current one (2 of 5) stronger with a bar and the caret on it; the keys in the status line"),
    (code_complete, "code-complete",
     "Completion under _ba as it is typed: the names in the code face, the typed part in value, "
     "each one's kind; a flat popover"),
    (code_goto, "code-goto",
     "Go to a definition: a popover with its own field over the cart's defs and their lines"),
    (code_files, "code-files",
     "The cart's scripts hung from the bar's Code tab: main.py checked as the open one, "
     "helpers.py focused, New script"),
    (lambda T: blocks_name(T), "blocks-name",
     "Blocks' name prompt as a dialog over the washed editor: the field; its keys in the hint "
     "strip, no button row on the T-Deck"),
    (lambda T: blocks_number(T), "blocks-number",
     "Blocks' number prompt: the field with the slot's value as its placeholder; Use a block a "
     "button the trackball rolls to; the keys type the digits, so no pad"),
    (lambda T: cart_info(T), "cart-info",
     "Make it mine's Cart info: Title and Made by; the trackball moves between them, Enter "
     "saves, Mic cancels"),
    (new_script, "new-script", "Make it mine's New script: the name; Enter adds, Mic cancels"),
    (lambda T: project_files(T), "project-files",
     "The project's files as a page of Make it mine: Code (New script last) and Art and sound, "
     "each file's icon, name and the tab it opens in"),
    (lambda T: web_console(T), "web-console",
     "The web console: the QR on its white quiet zone, the address and the pin, K turns it "
     "off"),
    (lambda T: text_console(T), "text-console",
     "A script's text console: the scrollback in the code face, what was typed in value (the "
     "live answer too), the prompt line at the foot"),
    (lambda T: text_console(T, done=True), "text-console-done",
     "A script that ended: The script ended. in the prompt line's place"),
    (lambda T: home_grid(T), "home-grid",
     "The home's grid arrangement: two rows of cards scrolling sideways, the last column cut by "
     "more than a third, titles cut to their card; focus grows and rings Hop Quest, its title bold"),
    (lambda T: home_icons(T), "home-icons",
     "The home's icons arrangement: icons alone, the focused one ringed, its name and line in the "
     "caption band at the grid's foot, which clips the top of a fourth row (the grid scrolls)"),
    (appearance_door, "theme-door",
     "The door to the Editor's theme mode: Appearance's last row, Edit theme…, which on a seed "
     "makes a copy"),
    (lambda T: theme_config(T), "theme-config",
     "The Editor in theme mode, Config: custom's theme.json in the json mode, opened from Moy, the "
     "status line saying whose copy it is; ‹, the open tab labelled, the theme's name, no Play; "
     "long lines fade at the edge"),
    (lambda T: theme_sprites(T), "theme-sprites",
     "The Editor in theme mode, Sprites: Home, a smooth slot, its mask in solid ink on a plain "
     "ground at 9x, its 1x preview, the sheet with the pixel-art dot's legend, the tools, the "
     "palette"),
    (lambda T: theme_sprites(T, edited=True), "theme-sprites-edited",
     "Sprites after a stroke in the red the palette holds: Home is pixel art now, dotted in the "
     "sheet; Home · pixels · Undo brings it back"),
    (lambda T: perf_hud(T), "perf-hud",
     "The perf HUD over Star Catcher: the fps chip, a flat pill in the surface's polarity"),
    (lambda T: perf_hud(T, detail=True), "perf-hud-detail",
     "The perf HUD's breakdown: the frame against its 33 ms budget, each part its own hue"),
    (lambda T: kernel_web(T), "kernel-web",
     "The kernel's web console screen: the address and the pin, no QR"),
    (lambda T: kernel_web(T, state="restarting"), "kernel-web-restarting",
     "The kernel's web console screen while the console restarts"),
)
TOUCH = (
    (lambda T: splash(T, 480, 320, 0.3, BOOT), "splash-s3",
     "The splash on the Guition S3 (480x320): Moy at 5x"),
    (lambda T: popover_hh(T, S3, "calendar"), "calendar-s3",
     "The calendar on the Guition S3 (touch): 28 px rows and the two chevrons as its buttons"),
    (lambda T: popover_hh(T, S3, "power"), "power-s3",
     "The power popover on the Guition S3: rows at the tap floor, no hint strip"),
    (lambda T: crash(T, S3), "crash-s3",
     "A game stopped on the Guition S3: the 320x240 canvas in its letterbox, the card centred at "
     "the glass's metrics with 28 px rows and no hints"),
    (lambda T: blocks_number(T, S3), "blocks-number-s3",
     "Blocks' number prompt on the Guition S3: 30 px field, Use a block, Cancel and OK"),
    (lambda T: web_console(T, S3), "web-console-s3",
     "The web console on the Guition S3: a larger QR and Turn off as a button"),
    (lambda T: home_grid(T, S3), "home-grid-s3",
     "The grid arrangement on the Guition S3: three rows"),
    (lambda T: tool_canvas(T), "tool-bar-s3",
     "A tool cart on the Guition S3's separate 320x240 canvas: the compact bar over its 18 rows "
     "and as much of the letterbox, so ‹ and the title are a 36 px tap"),
    (lambda T: home_icons(T, S3), "home-icons-s3",
     "The icons arrangement on the Guition S3: 52 px tiles, the caption band at the foot over the "
     "top of a fourth row"),
    (lambda T: theme_config(T, S3, copied=False), "theme-config-s3",
     "The theme mode's Config on the Guition S3: ‹ (Done) and 28 px tabs"),
    (lambda T: theme_sprites(T, S3), "theme-sprites-s3",
     "The theme mode's Sprites on the Guition S3: 28 px slots, swatches and tools, the sheet's "
     "page mark"),
)
DESKS = (
    (lambda T: splash(T, 1024, 600, 0.3, BOOT), "splash",
     "The splash on the 1024x600 desk: Moy at 10x, the wordmark at 40 px"),
    (loading_desk, "loading", "The loading pill on the desk, in its own lane: the middle of the bar"),
    (lambda T: popover_desk(T, "calendar"), "calendar",
     "The calendar hung from the desk bar's clock, its keys in its foot"),
    (lambda T: popover_desk(T, "power"), "power", "The power popover hung from the battery"),
    (lambda T: popover_desk(T, "wifi"), "wifi", "The Wi-Fi popover hung from the Wi-Fi"),
    (crash_desk, "crash",
     "A game stopped in console mode: the frozen game at 2x washed 60%, the stop card at the "
     "desk's metrics"),
    (lambda T: desk_code(T, "crash"), "crash-code",
     "See why in the desk Editor: the stopped line and its popover across the code pane"),
    (lambda T: desk_code(T, "find"), "code-find",
     "Find in the desk Editor: the field under the code pane's header, the current match on the "
     "caret's line"),
    (lambda T: desk_code(T, "complete"), "code-complete",
     "Completion under sp as it is typed on the desk"),
    (lambda T: desk_code(T, "goto"), "code-goto", "Go to, hung from the code pane's jump tool"),
    (blocks_desk, "blocks-name",
     "Blocks' name prompt on the desk over the washed Editor, the dialog's keys at its foot's "
     "left, the page's gone"),
    (lambda T: cart_info(T, desk=True), "cart-info",
     "Cart info on the desk over Make it mine, the dialog's keys in its foot"),
    (lambda T: web_console(T, desk=True), "web-console",
     "The web console on the desk: the QR, the address, the pin and Turn off"),
    (text_console_desk, "text-console", "A script's text console as a window on the desk"),
    (home_grid_desk, "home-grid",
     "The console mode's home in the grid arrangement: 128 px cards in three rows, the shelf "
     "bleeding off the right edge, the focused card ringed with its title bold"),
    (lambda T: tool_canvas(T, desk=True), "tool-bar",
     "A tool cart in console mode at 2x: the compact bar drawn at the glass's resolution over "
     "its 18 rows, the bar's Moy tile"),
    (lambda T: home_icons(T, desk=True, variant="labels"), "home-icons",
     "The console mode's home in the icons arrangement: ten columns of 64 px tiles, each named in "
     "full under it, the focused one ringed with its name bold"),
    (theme_desk, "theme-editor",
     "The Editor in theme mode on the desk: Config's theme.json beside Sprites, the focused pane "
     "and its tab, every slot named under its tile, Home ringed and painted to pixels at 16x "
     "beside its 1x and 2x"),
    (perf_hud_desk, "perf-hud",
     "The perf HUD in console mode, inside the letterbox's corner"),
    (lambda T: kernel_web(T, desk=True), "kernel-web", "The kernel's web console screen on the desk"),
)
CONTRAST_SLUGS = (("handheld", "splash-boot"), ("handheld", "calendar"), ("handheld", "crash"),
                  ("handheld", "loading"), ("handheld", "blocks-name"), ("handheld", "web-console"),
                  ("handheld", "perf-hud-detail"), ("desktop", "crash"))
CONTRAST_SCENES = tuple((kind, next(e for e in (HANDHELD if kind == "handheld" else DESKS)
                                    if e[1] == slug)) for kind, slug in CONTRAST_SLUGS)


def scenes():
    R12._SHIPPED_ON[0] = True
    try:
        with caps():
            _scenes()
    finally:
        R12._SHIPPED_ON[0] = False


def _scenes():
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
    n = "Moy Night"
    for slug, what in (("pop", "the focused tile popped out, its name's pill over the row "
                               "below (rejected: the pill covers the next row's tiles, which a focus "
                               "move must then repaint)"),
                       ("labels", "every tile its title (rejected on a handheld: names cut to "
                                  "Star Ca…, the focused one's too; the desk's pick)")):
        home_icons(DARK, variant=slug).save(f"moy-dark_handheld_home-icons-{slug}",
                                            f"The icons arrangement, variant {slug}: {what}; {n}",
                                            x3=True)
        home_icons(DARK, S3, variant=slug).save(f"moy-dark_handheld_home-icons-{slug}-s3",
                                                f"The icons arrangement, variant {slug}, on the "
                                                f"Guition S3; {n}")
    for slug, what in (("pop", "the focused name's pill over the next row (rejected)"),
                       ("caption", "the focused name in a band at the foot (rejected on the desk: "
                                   "the band sits far from a focus high on the grid)")):
        home_icons(DARK, desk=True, variant=slug).save(f"moy-dark_desktop_home-icons-{slug}",
                                                       f"The icons arrangement in console mode, "
                                                       f"variant {slug}: {what}; {n}")
    saver().save("moy-dark_handheld_saver",
                 "The saver, the same in every theme: a game's cover at 2x in a 1 px frame on black, "
                 "its title, the clock small; one flat frame every six seconds", x3=True)
    saver(S3).save("moy-dark_handheld_saver-s3", "The saver on the Guition S3: the cover at 3x")
    rec = ("The kernel's recovery screen, in Moy Night whatever the stored theme: Moy, what "
           "happened, Try again, Safe start and the developer console as rows, what the kernel "
           "knows in the code face, the serial words")
    recovery().save("moy-dark_handheld_recovery", f"{rec}; Moy Night", x3=True)
    recovery(S3).save("moy-dark_handheld_recovery-s3",
                      f"{rec}, on the Guition S3: 28 px rows, a tap chooses; Moy Night")
    recovery(desk=True).save("moy-dark_desktop_recovery", f"{rec}, on the desk; Moy Night")
    what = ("A colours-only store theme (Mint) with no look block: the default look's geometry "
            "and marks, flat, every derived colour from its roles, never the kernel's pixel "
            "fallbacks")
    mint_home().save("mint-light_handheld_home-fallback", f"{what}: the home; Mint", x3=True)
    mint_settings().save("mint-light_handheld_settings-fallback", f"{what}: Settings; Mint", x3=True)
    mint_desk().save("mint-light_desktop_fallback", f"{what}: the desk; Mint")


if __name__ == "__main__":
    scenes()
