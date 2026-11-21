"""A window's title strip drawn and placed from the strip page
(`wm_strip_ui`): `draw` is a frame of the page onto the strip of a frame the
window manager drew bare, `buttons` the strip's buttons as the window
manager's tap resolution reads them, `fire` a button's action run as the
page runs it. One page serves every window: what a window shows is written
into its states before each draw, a write that changes nothing marking
nothing.

`stamp` is the window manager's draw of a strip: the engine draws the
strip (its plate and the page) into a layer kept for what it shows, and
every frame that strip is on the glass -- a drag's above all -- stamps the
layer at the window's place, inside the canvas's clip, with no page work
and nothing allocated. `CAP` layers are kept, the least recently stamped
given back first."""

import wm_strip_ui

_PAD = wm_strip_ui.PAD

s = wm_strip_ui.s
_PAGE = [None]
_DIRTY = [False]                # a state written since the page's store last took the writes
_CTX = [None]                   # the strip's own toolkit context
_D = [None]
_AT = [0, 0, 0, 0]              # the page's area: the strip's size at 0, 0
NAMES = ("back", "min", "max", "close")


def _page():
    p = _PAGE[0]
    if p is None:
        import uitree_editor
        p = _PAGE[0] = uitree_editor.page(wm_strip_ui.strip, None, None)
        forget()
    return p


def forget():
    """The next draw writes every state again (a new page, a new console)."""
    _DIRTY[0] = True


def _put(nm, v):
    if s._v[nm] != v:
        s._v[nm] = v
        s._ver[nm] += 1
        _DIRTY[0] = True


def _states(title, icon, tone, back, unit, inset, full=False):
    page = _page()
    page._bind()
    _put("title", title)
    _put("icon", icon)
    _put("tone", tone)
    _put("back_on", back)
    _put("max_icon", "restore" if full else "maximize")
    p = _PAD * unit             # a bound pad is pixels (a literal one is units)
    _put("pad", p if p > inset else inset)
    if _DIRTY[0]:
        _DIRTY[0] = False
        pushed = getattr(page, "committed", None)
        if pushed is not None:
            pushed()            # the C engine's store takes the writes (the reference reads them)
    return page


def _place(rect, unit, title, icon, tone, back, inset, full=False):
    page = _states(title, icon, tone, back, unit, inset, full)
    page.unit = unit
    _AT[2] = rect[2]
    _AT[3] = rect[3]
    page.place(_AT, desk=True)
    return page


def buttons(rect, unit, title, icon, tone, back, inset, out, full=False):
    """The strip at `rect`'s buttons into `out` (cleared), left to right:
    [(name, rect, right)] on the canvas the strip lies on, `right` False
    for the back chevron alone, as `ui_draw.frame_buttons` answers.
    `inset`: the columns at each end the frame's corners take, which the
    strip's pad keeps clear. `full`: a full-screen window's row."""
    page = _place(rect, unit, title, icon, tone, back, inset, full)
    del out[:]
    x, y = rect[0], rect[1]
    for name in NAMES:
        r = page.rect_of(name)
        if r is not None and (name != "back" or back):
            out.append((name, (x + r[0], y + r[1], r[2], r[3]), name != "back"))
    return out


def head(rect, unit, title, icon, tone, back, inset):
    """The icon and title's cell on the canvas the strip lies on."""
    page = _place(rect, unit, title, icon, tone, back, inset)
    r = page.rect_of("head")
    return None if r is None else (rect[0] + r[0], rect[1] + r[1], r[2], r[3])


def fire(name):
    """Strip button `name`'s action, run as the page runs it."""
    page = _page()
    page._bind()
    page.run_action(page.actions[name], (), "the window's strip")


class _NoDamage:
    """The strip is drawn again when the window's chrome signature moves
    (`WindowChrome._win_chrome`), never because the page asked."""

    def all(self):
        pass

    def add(self, *a):
        pass


CAP = 4
_KEYS = [[None] * 13 for _ in range(CAP)]     # what each kept layer shows
_LAYS = [None] * CAP
_USED = [0] * CAP
_TICK = [0]
_WIN = [0, 0, 0, 0]
_STAMPS = [None, False]         # the canvas last stamped onto, and whether it takes a clipped stamp


def _hit(k, cv, w, h, title_h, title, focused, btns, fs, unit, icon, tone, back, look):
    return (k[0] is cv and k[1] == w and k[2] == h and k[3] == title_h and k[4] == title
            and k[5] == focused and k[6] == btns and k[7] == fs and k[8] == unit and k[9] == icon
            and k[10] == tone and k[11] == back and k[12] == look)


def stamp(cv, th, x, y, w, h, title_h, title, focused, btns, fs, unit, icon, tone, back, look, inset):
    """Window (x, y, w, h)'s strip onto `cv`, whose frame the caller drew
    bare (`ui_draw.FRAME_BARE` in `btns`): the strip's layer, made by the
    engine where none kept shows this, stamped at (x + 1, y + 1) inside
    `cv`'s clip, `inset` columns at each end left to the frame (a depth's
    rounded corners, which the strip's pad keeps its items out of). `look` is the look's generation. A canvas with no
    clipped stamp (`blit_strip_rect`: the web's recording layer) has the
    page drawn straight onto it."""
    if _STAMPS[0] is not cv:
        _STAMPS[0], _STAMPS[1] = cv, hasattr(cv, "blit_strip_rect") and hasattr(cv, "_clip_x0")
    if not _STAMPS[1]:
        _WIN[0], _WIN[1], _WIN[2], _WIN[3] = x + 1, y + 1, w - 2, title_h
        draw(cv, th, _WIN, unit, title, icon, tone, back, inset)
        return
    _TICK[0] += 1
    i = -1
    for j in range(CAP):
        if _hit(_KEYS[j], cv, w, h, title_h, title, focused, btns, fs, unit, icon, tone, back, look):
            i = j
            break
    if i < 0:
        i = 0
        for j in range(1, CAP):
            if _USED[j] < _USED[i]:
                i = j
        _render(i, cv, th, w, h, title_h, title, focused, btns, fs, unit, icon, tone, back, look, inset)
    _USED[i] = _TICK[0]
    lay = _LAYS[i]
    ox, oy = cv._ox, cv._oy
    x0, y0 = cv._clip_x0 - ox, cv._clip_y0 - oy
    x1, y1 = cv._clip_x1 - ox, cv._clip_y1 - oy
    sx0, sx1 = x + 1 + inset, x + w - 1 - inset
    if sx0 > x0:
        x0 = sx0
    if sx1 < x1:
        x1 = sx1
    if y + 1 > y0:
        y0 = y + 1
    if y + 1 + title_h < y1:
        y1 = y + 1 + title_h
    if x1 > x0 and y1 > y0:
        cv.blit_strip_rect(lay, x + 1, y + 1, x0, y0, x1 - x0, y1 - y0)


def _render(i, cv, th, w, h, title_h, title, focused, btns, fs, unit, icon, tone, back, look, inset):
    lay = _LAYS[i]
    if lay is None or lay.w != w - 2 or lay.h != title_h or _KEYS[i][0] is not cv:
        release(i)
        lay = _LAYS[i] = cv.new_layer(w - 2, title_h)
    D = _D[0]
    if D is None:
        import ui_draw as D
        _D[0] = D
    _WIN[0], _WIN[1], _WIN[2], _WIN[3] = -1, -1, w, h
    D.frame(lay, th, _WIN, title_h, title, focused, btns, fs)
    draw(lay, th, (0, 0, w - 2, title_h), unit, title, icon, tone, back, inset)
    k = _KEYS[i]
    k[0], k[1], k[2], k[3], k[4], k[5], k[6], k[7] = cv, w, h, title_h, title, focused, btns, fs
    k[8], k[9], k[10], k[11], k[12] = unit, icon, tone, back, look


def release(i=None):
    """Kept layer `i` given back (every one where None): a layer is off
    the gc heap on a board and nothing collects it."""
    for j in (range(CAP) if i is None else (i,)):
        lay = _LAYS[j]
        _LAYS[j] = None
        _KEYS[j][0] = None
        rel = getattr(lay, "release", None)
        if rel is not None:
            rel()


def reset():
    """Everything the strip keeps of a console given back: its layers, the
    canvas it last stamped onto, its toolkit context and its page (a new
    console, a console whose windows all went)."""
    release()
    _STAMPS[0] = None
    _CTX[0] = None
    _PAGE[0] = None


def draw(cv, th, rect, unit, title, icon, tone, back, inset, full=False):
    """A frame of the strip page onto `cv` at `rect` (the strip of a frame
    drawn bare, or a full-screen window's row: `full`, its maximise drawn
    as restore), the toolkit's target the host's frame set given back."""
    page = _states(title, icon, tone, back, unit, inset, full)
    if page._damage is None:
        page._damage = _NoDamage()
    D = _D[0]
    if D is None:
        import ui_draw as D
        _D[0] = D
    ctx = _CTX[0]
    if ctx is None:
        ctx = _CTX[0] = D.ui_context()
    t = D.UI_TARGET
    a, b, c, d = t[0], t[1], t[2], t[3]
    try:
        D.ui_frame(ctx, cv, th, rect=rect, desk=True, elsewhere=True)
        page.unit = unit
        page.bare = True
        page.band = False
        page.paint(0, cv, th, rect, True)
    finally:
        t[0], t[1], t[2], t[3] = a, b, c, d
    return page
