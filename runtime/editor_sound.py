# Map (grep -n a name to jump there):
#   WAVES           the wave brush's order, names and inks
#   geometry        the pane's rows at its size: brush, graph, waves, ladder, ops
#   header          the pane's header tools: rename, loop, speed, play
#   draw            the brush, the pitch graph, each step's wave, the ladder, the ops
#   pointer         a press on the graph, the wave row or the ladder
#   keys            the pane's own keys while it is focused
#   rename          the popover that names the sound
"""The desk Editor's Sound pane (docs/media/look_2026-10/EDITOR_LAYOUT.md's
Sound workspace): one sound of the cart's bank as a pitch graph, 16 steps a
page over two octaves, the selected step's note named; each step's wave
under it, painted with the wave brush; the volume ladder; the step ops.

It draws inside `EditorDesk`'s frame over the `MusicEditor` core the
handheld Music tab edits (`ws.music_ui.musicedit`), so every change is the
core's own undo step, reaches the running game at once (the bank is edited
in place, E-02) and is saved with the bank (`ws.save_sounds`). The rows,
tools and buttons are the verbs; the graph, the wave marks and the ladder
are drawn in the theme's roles and the waves' MOY64 inks, and answer the
pointer here."""

try:
    import ui
    from audio import SFX_NAME_MAX
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime import ui
    from runtime.audio import SFX_NAME_MAX

# The brush's order (EDITOR_LAYOUT.md's pictures), as wave ids
# (`audio.WAVE_*`): square, triangle, saw, pulse, organ, noise, phaser, tilted.
WAVES = (0, 1, 2, 4, 5, 3, 7, 6)
WAVE_NAMES = ("SQ", "TRI", "SAW", "NOI", "PLS", "ORG", "TLT", "PHA")       # by id
WAVE_WORDS = ("Square", "Triangle", "Saw", "Noise", "Pulse", "Organ", "Tilted", "Phaser")
# A wave's ink, a MOY64 index by wave id: on a light ground the deeper
# kin that holds 3:1 on white, on a dark one the colour itself.
WAVE_INK_DAY = (42, 3, 8, 5, 4, 45, 35, 13)
WAVE_INK_NIGHT = (12, 11, 8, 6, 9, 14, 10, 13)

NOTE_NAMES = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")
PAGE = 16                   # steps a page
SPAN = 24                   # semitones the graph shows: two octaves
LEVELS = 7                  # the ladder's lit levels; 0 is silence

# The ops under the ladder: (icon, word, op), the delete a danger.
OPS = (("new", "Add a step\tIns", "add"), ("trash", "Delete the step\tDel", "del"),
       ("copy", "Copy the step\tCtrl+C", "copy"), ("paste", "Paste\tCtrl+V", "paste"),
       ("dup", "Duplicate the sound\tCtrl+D", "dup"),
       ("arrow_left", "Move earlier\tAlt+Left", "earlier"),
       ("arrow_right", "Move later\tAlt+Right", "later"),
       ("mute", "Rest\tR", "rest"))

_LEFT, _RIGHT, _UP, _DOWN = 0x110, 0x111, 0x112, 0x113
_F2 = 0x101 + 1
_CTRL, _SHIFT, _ALT = 1, 2, 4


def note_name(p):
    return NOTE_NAMES[p % 12] + str(p // 12)


def wave_ink(th, wv):
    t = WAVE_INK_DAY if th.get("surface_light", False) else WAVE_INK_NIGHT
    return t[wv & 7]


def wave_rgb(th, wv):
    """A wave's ink as 0xRRGGBB, what `ui.swatches` takes."""
    try:
        from palette import MOY64
    except ImportError:  # pragma: no cover - host fallback
        from runtime.palette import MOY64
    r, g, b = MOY64[wave_ink(th, wv)]
    return (r << 16) | (g << 8) | b


def editor(desk):
    """The MusicEditor over the open cart's bank, built where it is not."""
    mu = desk.ws.music_ui
    if mu.musicedit is None:
        mu.build()
    return mu.musicedit


def _sfx_view(me):
    """The pane edits a sound: the core's active object is its SFX."""
    if me.view != me.SFX_VIEW:
        me.toggle_view()


def _state(desk):
    st = desk.part_state
    if "brush" not in st:
        st["brush"] = 1
        st["oct"] = {}
    return st


def low(desk, me):
    """The graph's lowest note, a C: as the pane left it for this sound, else
    the octave of the sound's lowest note, kept where the cursor's note shows."""
    st = _state(desk)
    s = me.cur_sfx()
    lo = st["oct"].get(me.sfx_idx)
    if lo is None:
        heard = [p for p, _w, v in ((t[0], t[1], t[2]) for t in s.steps) if p >= 0 and v] \
            if s is not None else []
        lo = 12 * (min(heard) // 12) if heard else 48
    cur = me.cur_step()
    if cur is not None and cur[0] >= 0:
        if cur[0] < lo:
            lo = 12 * (cur[0] // 12)
        elif cur[0] >= lo + SPAN:
            lo = 12 * (cur[0] // 12) - 12
    lo = max(0, min(96 - SPAN, lo))
    st["oct"][me.sfx_idx] = lo
    return lo


def geometry(r, u):
    """The pane's rows in body rect `r`: a dict of rects and sizes. The graph
    is 12 px a semitone where the pane holds it, else 8 (800 x 480), 6 at
    the least; a step 26 px where 16 fit, else narrower."""
    x, y, w, h = r
    pad = 12 * u
    brush = (x + pad, y + 4 * u, w - 2 * pad, 32 * u)
    ops_h = 36 * u
    fixed = 4 * u + 32 * u + 6 * u + 20 * u + 4 * u + 8 * LEVELS * u + 8 * u + ops_h + 8 * u
    semi = 6 * u
    for s in (12, 8):
        if fixed + SPAN * s * u <= h:
            semi = s * u
            break
    gx = x + pad + 30 * u
    cw = max(10 * u, min(26 * u, (x + w - pad - gx) // PAGE))
    gy = brush[1] + brush[3] + 6 * u
    gh = SPAN * semi
    wy = gy + gh + 4 * u
    vh = 8 * u if semi >= 12 * u else 6 * u
    vy = wy + 20 * u
    oy = y + h - ops_h - 8 * u
    return {"brush": brush, "graph": (gx, gy, cw * PAGE, gh), "semi": semi, "cw": cw,
            "waves": (gx, wy, cw * PAGE, 16 * u), "ladder": (gx, vy, cw * PAGE, vh * LEVELS),
            "vh": vh, "ops": (x + pad, oy, w - 2 * pad, ops_h), "pad": pad}


def title(desk):
    me = editor(desk)
    if me is None:
        return "Sound"
    s = me.cur_sfx()
    name = getattr(s, "name", None) if s is not None else None
    return "Sound %d · %s" % (me.sfx_idx, name) if name else "Sound %d" % me.sfx_idx


# -- the header ----------------------------------------------------------------

def header(desk, pid, tx, y, hh, u):
    """The header's tools right to left from `tx` (grow's left edge): play,
    the speed stepper, loop, rename, and where no Sounds list shows, the
    sounds as a menu. The x left of them."""
    me = editor(desk)
    if me is None:
        return tx
    ws = desk.ws
    mu = ws.music_ui
    s = me.cur_sfx()
    cell = 32 * u
    tx -= cell
    ui.at(tx, y, cell, hh)
    playing = mu.music_preview is not None and mu.music_preview[0] == "sfx"
    if ui.tool("run", "Stop\tSpace" if playing else "Play\tSpace",
               {"id": pid + " sound play", "current": playing}):
        play(desk)
    tx -= cell
    ui.at(tx, y, cell, hh)
    if ui.tool("forward", "Faster\t]", {"id": pid + " sound faster"}):
        _do(desk, "faster")
    sp = "%d" % int(s.speed) if s is not None else ""
    lw = ui.text_size(sp)[0] + 12 * u
    tx -= lw
    _centred(sp, tx, y, lw, hh, None, u)
    tx -= cell
    ui.at(tx, y, cell, hh)
    if ui.tool("back", "Slower\t[", {"id": pid + " sound slower"}):
        _do(desk, "slower")
    tx -= cell + 4 * u
    ui.at(tx, y, cell, hh)
    if ui.tool("rotate", "Loop\tL", {"id": pid + " sound loop",
                                     "current": bool(s is not None and s.loop)}):
        _do(desk, "loop")
    tx -= cell
    ui.at(tx, y, cell, hh)
    if ui.tool("pencil", "Rename\tF2", {"id": pid + " sound rename"}):
        open_rename(desk)
    if not desk.shows("sounds"):
        tx -= cell
        ui.at(tx, y, cell, hh)
        if ui.tool("down", "Sounds", {"id": pid + " sound menu"}):
            _state(desk)["menu"] = True
    return tx


# -- drawing ---------------------------------------------------------------------

def draw(desk, cv, th, r, u):
    """The pane's body in `r` (the frame is begun)."""
    me = editor(desk)
    if me is None:
        ui.at(*r)
        ui.empty("music", "No sound", ("This cart has no sound bank",))
        return
    ws = desk.ws
    if ws.audio_out is not None:
        ws.audio_out.tick(desk.dt)
    mu = ws.music_ui
    if mu.music_preview is not None and not mu._music_preview_active():
        mu.music_preview = None
        ws._dirty = True
    st = _state(desk)
    s = me.cur_sfx()
    g = geometry(r, u)
    cur = me.cur_step()
    if cur is not None:
        st["brush"] = cur[1]
    # the wave brush
    ui.at(*g["brush"])
    order = [WAVES.index(w) for w in range(8)]
    pick = ui.swatches("Wave  " + WAVE_WORDS[st["brush"]],
                       [wave_rgb(th, w) for w in WAVES], order[st["brush"]],
                       {"id": "wave brush"})
    if pick is not None and 0 <= pick < 8 and WAVES[pick] != st["brush"]:
        st["brush"] = WAVES[pick]
        if cur is not None:
            _set_wave(desk, me, st["brush"])
    _graph(desk, me, s, cv, th, g, u)
    # the ops, and the octave and page steppers
    ox, oy, ow, oh = g["ops"]
    cell = oh
    for k, (icon, word, op) in enumerate(OPS):
        ui.at(ox + k * (cell + 2 * u), oy, cell, oh)
        ui.icon(icon)
        if ui.button("", "danger" if op == "del" else "normal", {"id": "sound " + op}):
            _do(desk, op)
    rx = ox + len(OPS) * (cell + 2 * u) + 8 * u
    n = me.step_count()
    page = me.step // PAGE
    lo = low(desk, me)
    room = ox + ow - rx
    paged = n > PAGE
    if not paged or room >= 2 * _STEPPER_W * u:
        _stepper(desk, "oct", "Oct %d" % (lo // 12), rx, oy, oh, u)
        rx += _STEPPER_W * u
    if paged:
        _stepper(desk, "page", "%d\u2013%d" % (page * PAGE + 1, min(n, page * PAGE + PAGE)),
                 rx, oy, oh, u)
    if st.get("rename") is not None:
        rename(desk, u)
    elif st.get("menu"):
        sounds_menu(desk, u)


_STEPPER_W = 96             # a stepper: its two chevrons and its value


def _centred(text, x, y, w, h, o, u):
    """A label centred in (x, y, w, h): a label's row pads its start by
    style.json's row pad."""
    tw = ui.text_size(text)[0]
    pad = (w - tw) // 2
    ui.at(x + pad - 4 * u, y, tw + 8 * u, h)
    ui.label(text, o)


def _stepper(desk, what, text, x, y, h, u):
    cell = 24 * u
    ui.at(x, y + (h - 32 * u) // 2, cell, 32 * u)
    if ui.tool("back", "Lower" if what == "oct" else "Earlier steps", {"id": "sound %s down" % what}):
        _do(desk, what + "-")
    tw = _STEPPER_W * u - 2 * cell
    _centred(text, x + cell, y, tw, h, {"tone": "dim"}, u)
    ui.at(x + cell + tw, y + (h - 32 * u) // 2, cell, 32 * u)
    if ui.tool("forward", "Higher" if what == "oct" else "Later steps", {"id": "sound %s up" % what}):
        _do(desk, what + "+")


def _graph(desk, me, s, cv, th, g, u):
    """Two octaves from `low`, a row a semitone (the sharps shaded, each C
    ruled and named), a column a step: its note as a capped bar in its
    wave's ink, the selected step's column on the value plate and its note
    named over it; each step's wave under it; the volume ladder."""
    gx, gy, gw, gh = g["graph"]
    semi, cw = g["semi"], g["cw"]
    lo = low(desk, me)
    light = th.get("surface_light", False)
    cv.rrect(gx, gy, gw, gh, 6 * u, th["surface"] if light else th["panel"], th["border"])
    cv.clip(gx + 1, gy + 1, gw - 2, gh - 2)
    for k in range(SPAN):
        if (lo + k) % 12 in (1, 3, 6, 8, 10):
            cv.shadow(gx, gy + gh - (k + 1) * semi, gw, semi, 0, th["ink"], 10, 0)
    for k in range(1, SPAN):
        if (lo + k) % 12 == 0:
            cv.rect(gx, gy + gh - k * semi, gw, 1, th["border"])
    steps = s.steps if s is not None else []
    first = (me.step // PAGE) * PAGE
    sel = me.step - first
    if 0 <= sel < PAGE:
        cv.shadow(gx + sel * cw + 1, gy + 1, cw - 2, gh - 2, 4 * u, th["value"], 40, 0)
    named = None
    for i in range(PAGE):
        n = first + i
        if n >= len(steps):
            break
        p, wv, v = steps[n][0], steps[n][1], steps[n][2]
        if p < 0 or not v:
            continue
        k = max(0, min(SPAN - 1, p - lo))
        top = gy + gh - (k + 1) * semi
        cx = gx + i * cw
        ink = wave_ink(th, wv)
        body = gy + gh - top - semi - 1
        if body > 0:
            cv.rect(cx + 4 * u, top + semi, cw - 8 * u, body, ink)
            cv.shadow(cx + 4 * u, top + semi, cw - 8 * u, body, 0, th["surface"], 150, 0)
        cv.rrect(cx + 3 * u, top, cw - 6 * u, semi, 3 * u, ink)
        if i == sel:
            named = (cx, top, p)
    cv.clip()
    for k in range(0, SPAN, 12):
        ui.at(gx - 30 * u, gy + gh - k * semi - 14 * u, 28 * u, 14 * u)
        ui.label(note_name(lo + k), {"tone": "dim"})
    if named is not None:
        cx, top, p = named
        t = note_name(p)
        tw = ui.text_size(t)[0] + 10 * u
        th_ = 18 * u
        nx = max(gx, min(gx + gw - tw, cx + (cw - tw) // 2))
        ny = max(gy + 2 * u, top - th_ - 2 * u)
        cv.rrect(nx, ny, tw, th_, 5 * u, th["surface"], th["value"])
        ui.at(nx + 5 * u, ny, tw - 10 * u, th_)
        ui.label(t, {"tone": "value"})
    # each step's wave, then the ladder
    wx, wy, _ww, wh = g["waves"]
    lx, ly, _lw, _lh = g["ladder"]
    vh = g["vh"]
    for i in range(PAGE):
        n = first + i
        cx = wx + i * cw
        if n >= len(steps):
            cv.rect(cx + cw // 2 - 1 * u, wy + 7 * u, 2 * u, 2 * u, th["border"])
            continue
        p, wv, v = steps[n][0], steps[n][1], steps[n][2]
        d = 8 * u
        cv.rrect(cx + (cw - d) // 2, wy + (wh - d) // 2, d, d, 2 * u,
                 wave_ink(th, wv) if p >= 0 else th["border"])
        for k in range(LEVELS):
            on = p >= 0 and k < v
            cv.rect(cx + 4 * u, ly + (LEVELS - 1 - k) * vh, cw - 8 * u, vh - 2 * u,
                    th["value"] if on else th["border"])
    ui.at(gx - 30 * u, ly + (LEVELS * vh - 14 * u) // 2, 28 * u, 14 * u)
    ui.label("Vol", {"tone": "dim"})


# -- acting ----------------------------------------------------------------------

def play(desk):
    """Play the sound, or stop it."""
    mu = desk.ws.music_ui
    me = editor(desk)
    if me is None:
        return
    if mu.music_preview is not None:
        mu._stop_music_preview()
        return
    _sfx_view(me)
    mu._play_music_preview()


def _set_wave(desk, me, wv):
    st = me.cur_step()
    if st is not None and st[1] != wv:
        _sfx_view(me)
        me.cycle_wave((wv - st[1]) % 8)
        desk.ws._dirty = True


def _do(desk, op):
    """One of the pane's verbs on the core: an undo step each."""
    me = editor(desk)
    if me is None:
        return
    _sfx_view(me)
    st = _state(desk)
    if op == "add":
        me.add_step()
    elif op == "del":
        me.del_step()
    elif op == "copy":
        me.copy()
    elif op == "paste":
        me.paste()
    elif op == "dup":
        me.duplicate_sfx()
    elif op == "earlier":
        me.move_step(-1)
    elif op == "later":
        me.move_step(1)
    elif op == "rest":
        me.toggle_rest(low(desk, me) + 12)
    elif op == "loop":
        me.toggle_loop()
    elif op == "faster":
        me.nudge_speed(1)
    elif op == "slower":
        me.nudge_speed(-1)
    elif op in ("oct+", "oct-"):
        lo = low(desk, me) + (12 if op == "oct+" else -12)
        st["oct"][me.sfx_idx] = max(0, min(96 - SPAN, lo))
        cur = me.cur_step()
        if cur is not None and cur[0] >= 0:
            me.nudge_pitch(12 if op == "oct+" else -12)
    elif op in ("page+", "page-"):
        n = me.step_count()
        to = (me.step // PAGE + (1 if op == "page+" else -1)) * PAGE
        if 0 <= to < n:
            me.select_cursor(to)
    desk.ws._dirty = True


# -- the pointer -----------------------------------------------------------------

def pointer(desk, r, u, px, py, click, down):
    """A press on the graph (the step and its note), the wave row (the
    brush's wave) or the ladder (the step's volume). True where it took it."""
    me = editor(desk)
    if me is None or (not click and not down):
        return False
    st = _state(desk)
    if st.get("rename") is not None or st.get("menu"):
        return False
    g = geometry(r, u)
    cw = g["cw"]
    first = (me.step // PAGE) * PAGE
    hit = None
    for key in ("graph", "waves", "ladder"):
        x, y, w, h = g[key]
        if x <= px < x + w and y <= py < y + h:
            hit = key
            break
    if hit is None:
        return False
    x, y, w, h = g[hit]
    n = first + (px - x) // cw
    if n >= me.step_count():
        return True
    _sfx_view(me)
    if n != me.step:
        me.select_cursor(n)
    stp = me.cur_step()
    if hit == "graph":
        k = (y + h - 1 - py) // g["semi"]
        p = low(desk, me) + max(0, min(SPAN - 1, k))
        if stp[0] != p:
            me.set_pitch(p)
            if stp[2] == 0:
                me.nudge_vol(5)
    elif hit == "waves":
        if click:
            _set_wave(desk, me, st["brush"])
    else:
        lv = max(0, min(LEVELS, LEVELS - (py - y) // g["vh"]))
        if stp[2] != lv:
            me.nudge_vol(lv - stp[2])
    desk.ws._dirty = True
    return True


# -- the keys --------------------------------------------------------------------

def keys(desk, i, ev):
    """The pane's keys while it is focused (EDITOR_LAYOUT.md: a pane keeps its
    own keys): left and right pick a step, up and down move its note
    (Shift an octave), W the next wave, R a rest, [ and ] the speed, L
    loop, Space plays, + and - the volume, Del deletes, Ctrl+C/V/D copy,
    paste and duplicate, Alt+Left/Right move the step, F2 renames. `i` is
    the input (an arrow alone is the d-pad, no key event), `ev` this
    frame's new key event or 0. True where one acted."""
    me = editor(desk)
    if me is None:
        return False
    st = _state(desk)
    if st.get("rename") is not None or st.get("menu"):
        return False
    code, mods = ev & 0xFFFF, ev >> 16
    if not ev:
        mods = getattr(i, "mods", 0) or 0
        for name, c in (("left", _LEFT), ("right", _RIGHT), ("up", _UP), ("down", _DOWN)):
            if i.pressed(name):
                code = c
                break
        if not code:
            return False
    ctrl, shift, alt = mods & _CTRL, mods & _SHIFT, mods & _ALT
    _sfx_view(me)
    if code in (_LEFT, _RIGHT):
        d = -1 if code == _LEFT else 1
        if alt:
            _do(desk, "earlier" if d < 0 else "later")
        else:
            me.move_cursor(d)
    elif code in (_UP, _DOWN) and not alt:
        cur = me.cur_step()
        if cur is not None and cur[0] < 0:
            me.toggle_rest(low(desk, me) + 12)
        else:
            me.nudge_pitch((12 if shift else 1) * (1 if code == _UP else -1))
    elif ctrl and code in (ord("c"), ord("C"), 3):
        _do(desk, "copy")
    elif ctrl and code in (ord("v"), ord("V"), 22):
        _do(desk, "paste")
    elif ctrl and code in (ord("d"), ord("D"), 4):
        _do(desk, "dup")
    elif ctrl or alt:
        return False
    elif code in (ord("w"), ord("W")):
        _set_wave(desk, me, WAVES[(WAVES.index(me.cur_step()[1]) + (7 if shift else 1)) % 8]
                  if me.cur_step() is not None else 0)
    elif code in (ord("r"), ord("R")):
        _do(desk, "rest")
    elif code in (ord("l"), ord("L")):
        _do(desk, "loop")
    elif code == ord("["):
        _do(desk, "slower")
    elif code == ord("]"):
        _do(desk, "faster")
    elif code == ord(" "):
        play(desk)
    elif code in (ord("+"), ord("=")):
        me.nudge_vol(1)
    elif code in (ord("-"), ord("_")):
        me.nudge_vol(-1)
    elif code == 0x7F:
        _do(desk, "del")
    elif code == _F2:
        open_rename(desk)
    else:
        return False
    desk.ws._dirty = True
    return True


# -- naming ----------------------------------------------------------------------

def open_rename(desk):
    me = editor(desk)
    if me is None:
        return
    s = me.cur_sfx()
    st = _state(desk)
    st["rename"] = (getattr(s, "name", None) or "") if s is not None else ""
    st["menu"] = False
    desk.ws._dirty = True


def rename(desk, u):
    """The name popover, hung under the Sound pane's header: a field whose
    Enter names the sound (empty: no name, the list shows its number)."""
    me = editor(desk)
    st = _state(desk)
    box = desk.header_rect("music")
    if box is None or me is None:
        st["rename"] = None
        return
    ui.at(box[0] + 8 * u, box[1] + box[3], min(box[2] - 16 * u, 300 * u), 0)
    if not ui.popover("sound name", min(box[2] - 16 * u, 300 * u)):
        st["rename"] = None
        ui.popover()
        desk.ws._dirty = True
        return
    text, enter = ui.field("Name", st["rename"], SFX_NAME_MAX,
                           {"id": "sound name field", "hint": "no name"})
    st["rename"] = text
    if enter:
        _sfx_view(me)
        me.set_name(text)
        st["rename"] = None
        desk.ws._dirty = True
    ui.popover()


def sounds_menu(desk, u):
    """Where no Sounds list shows (800 x 480): the bank as a menu from the
    pane's title."""
    me = editor(desk)
    st = _state(desk)
    box = desk.header_rect("music")
    if box is None or me is None:
        st["menu"] = False
        return
    ui.at(box[0] + 8 * u, box[1] + box[3], 220 * u, 0)
    if not ui.popover("sound menu", 220 * u):
        st["menu"] = False
        ui.popover()
        desk.ws._dirty = True
        return
    items = [row_label(me, k) for k in range(len(me.bank.sfx))] + ["New sound"]
    got = ui.menu("", items, ["music"] * len(me.bank.sfx) + ["new"])
    ui.popover()
    if got is not None and 0 <= got < len(items):
        k = got
        _sfx_view(me)
        if k >= len(me.bank.sfx):
            me.select_sfx(len(me.bank.sfx) - me.sfx_idx)
        else:
            me.sfx_idx = k
            me.step = 0
        st["menu"] = False
        desk.ws._dirty = True


def row_label(me, k):
    """A sound's row: its number, and its name where it has one."""
    name = getattr(me.bank.sfx[k], "name", None)
    return "%d  %s" % (k, name) if name else "%d" % k
