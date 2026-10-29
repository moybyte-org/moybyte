# Map (grep -n a name to jump there):
#   PARTS           the panes the desk Editor draws on the verbs
#   actor           the World's Actor pane: the selected actor's props
#   stamps_label    the Stamps pane's line: the tag the stamp gives
#   sounds          the Sound workspace's list: the song, then a row a sound
#   song            the Song pane: the tracks, the current one marked
#   palette         the Blocks workspace's palette: the categories, then their blocks
#   made            the code the blocks make, read only
#   ref_sounds      the Reference's Sounds: a pick puts its sfx() at the caret
#   map_view        the Reference's Map: a pick puts its mget() at the caret
#   log             the Reference's Log: the run's and the code's errors, to their line
#   pointer, keys   a press on the map, the arrows over it
"""The desk Editor's own panes on the public UI API
(docs/media/look_2026-10/EDITOR_LAYOUT.md's World and Sound): each draws in
its pane's body inside `EditorDesk`'s frame, over the editor cores the
handheld tabs edit (`SceneEditor`, `MusicEditor`), and answers its taps and
keys through the verbs. A change is the core's own (its undo step), synced
to the running game as the tab's is."""

try:
    import ui
    import editor_sound as Sound
    import blocks as B
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime import ui
    from runtime import editor_sound as Sound
    from runtime import blocks as B

PARTS = ("actor", "sounds", "song", "log", "maps", "palette", "made")

_ROW = 36
_LIST_ROW = 28              # a list's row on the desk (style.json's stack_desk)


def draw(desk, c, r, u):
    """Part `c` in body rect `r` (the frame is begun)."""
    if c == "actor":
        actor(desk, r, u)
    elif c == "sounds":
        if desk.key == "sound":
            sounds(desk, r, u)
        else:
            ref_sounds(desk, r, u)
    elif c == "song":
        song(desk, r, u)
    elif c == "palette":
        palette(desk, r, u)
    elif c == "maps":
        map_foot(desk, r, u)
    elif c == "log":
        log(desk, r, u)
    elif c == "made":
        made(desk, r, u)


def drawn_before(desk, c, cv, th, r, u):
    """The parts drawn on the canvas before the frame begins: the
    Reference's map."""
    if c == "maps":
        map_view(desk, cv, th, r, u)


def _rows(r, u, n):
    """`n` rows of the part's height down `r`, inset."""
    x, y, w, _h = r
    pad = 10 * u
    h = _ROW * u
    return [(x + pad, y + pad + k * (h + 6 * u), w - 2 * pad, h) for k in range(n)]


def actor(desk, r, u):
    """The selected actor (EDITOR_LAYOUT.md's World): its tag, the tags the
    scene uses, Flip, Front and Back, its tile and place, and Remove. No
    selection: what to do."""
    ws = desk.ws
    su = ws.scene_ui
    se = su.sceneedit
    a = se.selected() if se is not None else None
    if a is None:
        ui.at(*r)
        ui.empty("scene", "No actor picked", ("Tap one in the scene to change it",))
        return
    rows = _rows(r, u, 4)
    st = desk.part_state
    if st.get("tag_of") != se.sel:
        st["tag_of"] = se.sel
        st["tag"] = str(a.get("tag") or "")
    ui.at(*rows[0])
    text, enter = ui.field("Tag", st["tag"], 16, {"id": "actor tag"})
    st["tag"] = text
    changed = False
    if enter and text and text != a.get("tag"):
        changed = se.set_tag(text)
    tags = []
    for row in se.rows:
        t = row.get("tag")
        if t and t not in tags:
            tags.append(str(t))
    tags = tags[:5]
    if tags:
        cur = tags.index(a.get("tag")) if a.get("tag") in tags else -1
        ui.at(*rows[1])
        got = ui.seg("Tags", tags, cur, {"id": "actor tags"})
        if got is not None and got != cur and got >= 0:
            changed = se.set_tag(tags[got]) or changed
            st["tag"] = tags[got]
    x, y, w, h = rows[2]
    third = (w - 16 * u) // 3
    ui.at(x, y, third, h)
    flip = bool(a.get("flip"))
    if ui.toggle("Flip", flip, {"id": "actor flip"}) != flip:
        changed = se.toggle_flip() or changed
    ui.at(x + third + 8 * u, y, third, h)
    if ui.button("Front", "normal", {"id": "actor front"}):
        changed = se.front_sel() or True
    ui.at(x + 2 * (third + 8 * u), y, third, h)
    if ui.button("Back", "normal", {"id": "actor back"}):
        changed = se.back_sel() or True
    x, y, w, h = rows[3]
    ui.at(x, y, w - third - 8 * u, h)
    ui.label("Tile %d   x %d   y %d" % (a.get("tile", 0), a.get("x", 0), a.get("y", 0)),
             {"tone": "dim"})
    ui.at(x + w - third, y, third, h)
    if ui.button("Remove", "danger", {"id": "actor remove"}):
        se.delete_sel()
        changed = True
    if changed:
        su._sync_live()
        ws._dirty = True
        desk._owe_restart("scene")


def stamps_label(desk, r, u):
    """Under the Stamps pane's sheet: the tag a stamp gives its actors."""
    se = desk.ws.scene_ui.sceneedit
    if se is None:
        return
    x, y, w, h = r
    ui.at(x + 10 * u, y + h - 30 * u, w - 20 * u, 24 * u)
    ui.label("A stamp makes a %s" % (getattr(se, "stamp_tag", None) or "thing"),
             {"tone": "dim"})


def _music(desk):
    mu = desk.ws.music_ui
    if mu.musicedit is None:
        mu.build()
    return mu, mu.musicedit


def sounds(desk, r, u):
    """The Sound workspace's list: the Song and its length, then a row a
    sound, by its number and its name where it has one (moy-spec's optional
    `name`), a sketch of its notes and its play tool."""
    mu, me = _music(desk)
    if me is None:
        return
    ws = desk.ws
    th = ws.theme_colors
    cv = ws.sys_canvas
    items = ["Song"] + [Sound.row_label(me, k) for k in range(len(me.bank.sfx))]
    song = me.view == me.SONG_VIEW
    cur = 0 if song else me.sfx_idx + 1
    x, y, w, h = r
    pad = 6 * u
    cell = 32 * u
    rh = _LIST_ROW * u
    lr = (x + pad, y + 4 * u, w - 2 * pad - cell, h - 8 * u)
    ui.at(*lr)
    got = ui.list("Sounds", items, cur, {"id": "sound list"})
    if got is not None and got != cur and got >= 0:
        if got == 0:
            if not song:
                me.toggle_view()
        else:
            if song:
                me.toggle_view()
            me.sfx_idx = got - 1
            me.step = 0
        ws._dirty = True
    if len(items) * rh > lr[3]:
        return                      # the list scrolls in its rect: no sketches over it
    t = me.cur_track()
    n = len(t.pattern) if t is not None else 0
    slots = "1 slot" if n == 1 else "%d slots" % n
    sw = ui.text_size(slots)[0] + 8 * u
    ui.at(lr[0] + lr[2] - sw, lr[1], sw, rh)
    ui.label(slots, {"tone": "dim"})
    playing = mu.music_preview
    for k, s in enumerate(me.bank.sfx):
        ry = lr[1] + (k + 1) * rh
        _sketch(cv, th, s, lr[0] + lr[2] - 52 * u, ry + 6 * u, 44 * u, rh - 12 * u, u)
        ui.at(x + w - pad - cell, ry + (rh - cell) // 2, cell, cell)
        on = playing is not None and playing == ("sfx", k)
        if ui.tool("run", "Play", {"id": "sound play %d" % k, "current": on}):
            if on:
                mu._stop_music_preview()
            else:
                if me.view != me.SFX_VIEW:
                    me.toggle_view()
                me.sfx_idx = k
                mu._play_music_preview()
            ws._dirty = True


def _sketch(cv, th, s, x, y, w, h, u):
    """A sound's sketch: a bar a step at its pitch in its first wave's ink,
    a rest none."""
    steps = s.steps
    heard = [st[0] for st in steps if st[0] >= 0 and st[2]]
    if not heard:
        cv.rect(x, y + h - 1 * u, w, 1 * u, th["border"])
        return
    lo, hi = min(heard) - 2, max(heard) + 2
    bw = max(2 * u, w // max(8, len(steps)))
    ink = Sound.wave_ink(th, steps[0][1])
    for i, st in enumerate(steps[: w // bw]):
        if st[0] >= 0 and st[2]:
            bh = max(2 * u, (st[0] - lo) * h // max(1, hi - lo))
            cv.rect(x + i * bw, y + h - bh, bw - 1 * u, bh, ink)


def sounds_header(desk, pid, tx, y, hh, u):
    """The Sounds pane's ＋: a new sound at the end of the bank."""
    _mu, me = _music(desk)
    if me is None:
        return tx
    cell = 32 * u
    tx -= cell
    ui.at(tx, y, cell, hh)
    if ui.tool("new", "New sound", {"id": pid + " new sound"}):
        if me.view != me.SFX_VIEW:
            me.toggle_view()
        me.select_sfx(len(me.bank.sfx) - me.sfx_idx)
        desk.ws._dirty = True
    return tx


def song(desk, r, u):
    """The song's tracks, four a row, the one being edited marked."""
    mu, me = _music(desk)
    if me is None:
        return
    x, y, w, _h = r
    pad = 10 * u
    cw, ch = 72 * u, 56 * u
    gap = max(4 * u, (w - 2 * pad - 4 * cw) // 3)
    for k in range(max(8, len(me.bank.music))):
        cx = x + pad + (k % 4) * (cw + gap)
        cy = y + pad + (k // 4) * (ch + 8 * u)
        ui.at(cx, cy, cw, ch)
        if ui.button(str(k + 1), "normal", {"id": "track %d" % k,
                                            "current": k == me.track_idx,
                                            "disabled": k >= len(me.bank.music)}):
            if k < len(me.bank.music):
                me.track_idx = k
                me.slot = 0
                if me.view != me.SONG_VIEW:
                    me.toggle_view()
                desk.ws._dirty = True


# -- the block palette ---------------------------------------------------------------

# The palette's categories (the insert menu's, block_editor_ui._CAT_LABEL)
# in the pictures' order, read across two columns, and the rows heading
# three of them.
_CATS = (("events", "When..."), ("control", "Control"), ("draw", "Draw"),
         ("input", "Buttons"), ("variables", "Variables"), ("lists", "Lists"),
         ("actors", "Sprite"), ("looks", "Looks"), ("operators", "Math"),
         ("sound", "Sound"), ("myblocks", "My Blocks"))
# The palette's two columns: the categories alternately (a board's MicroPython
# slices with no step).
_CAT_SIDES = (tuple(_CATS[i] for i in range(0, len(_CATS), 2)),
              tuple(_CATS[i] for i in range(1, len(_CATS), 2)))
_NEW = {"variables": ("\x00new_var", "+ new variable"),
        "lists": ("\x00new_list", "+ new list"),
        "myblocks": ("\x00new_proc", "+ new block")}
_CALL = "\x00call:"


def _blocks(desk):
    bu = desk.ws.block_ui
    if bu.blocks_ed is None:
        bu.build()
    return bu, bu.blocks_ed


def palette_items(be, cat):
    """(item, label) for the blocks category `cat` offers: the catalog's,
    headed by its make-one row; My Blocks' calls; none for the events,
    which head every script already."""
    if cat == "events":
        return []
    out = []
    if cat in _NEW:
        out.append(_NEW[cat])
    if cat == "myblocks":
        for n in (be.proc_names() if be is not None else []):
            out.append((_CALL + n, "call " + n))
        return out
    for t in B.blocks_in_category(cat):
        d = B.block_def(t)
        out.append((t, _filled(d) if d else t))
    return out


def _filled(d):
    """A catalog block's label as a new block reads: each slot its default,
    one with none its name ("move by {dx} {dy}" reads "move by 2 0")."""
    label = d["label"]
    for slot in d.get("slots", ()):
        v = slot.get("default")
        if v is not None:
            label = label.replace("{%s}" % slot["name"], str(v))
    label = label.replace("{", "").replace("}", "")
    while "  " in label:
        label = label.replace("  ", " ")
    return label.strip()


def palette(desk, r, u):
    """The Blocks workspace's palette (EDITOR_LAYOUT.md): the eleven
    categories in two columns, each by its colour, then the current one's
    blocks; activating a block puts it at the Blocks pane's cursor."""
    ws = desk.ws
    bu, be = _blocks(desk)
    if be is None:
        ui.at(*r)
        ui.empty("blocks", "No blocks", ("This cart's blocks did not open",))
        return
    th = ws.theme_colors
    cv = ws.sys_canvas
    cols = ws.look.content_colors()
    st = desk.part_state
    cat = st.get("block cat", "actors")
    x, y, w, h = r
    pad = 6 * u
    rh = _LIST_ROW * u
    half = (w - 2 * pad - 4 * u) // 2
    rows = (len(_CATS) + 1) // 2
    for side in (0, 1):
        part = _CAT_SIDES[side]
        cx = x + pad + side * (half + 4 * u)
        names = ["   " + label for _k, label in part]
        cur = [k for k, _l in part].index(cat) if cat in [k for k, _l in part] else -1
        ui.at(cx, y + 6 * u, half, rows * rh)
        got = ui.list("block categories %d" % side, names, cur,
                      {"id": "block categories %d" % side})
        if got is not None and got >= 0 and got != cur:
            cat = st["block cat"] = part[got][0]
            ws._dirty = True
        for k, (key, _l) in enumerate(part):
            d = 8 * u
            cv.rrect(cx + 5 * u, y + 6 * u + k * rh + (rh - d) // 2, d, d, 2 * u,
                     cols.get("block_" + key, th["border"]))
    top = y + 6 * u + rows * rh + 8 * u
    ui.at(x + pad, top, w - 2 * pad, 1)
    ui.rule()
    top += 8 * u
    items = palette_items(be, cat)
    room = (y + h - pad) - top
    if not items:
        ui.at(x + pad, top, w - 2 * pad, room)
        ui.label("Every script starts with one: they head the Blocks pane",
                 {"tone": "dim", "wrap": True, "lines": 4})
        return
    if bu.blk_graduated:
        ui.at(x + pad, top, w - 2 * pad, room)
        ui.label("This cart is code now: its blocks only read",
                 {"tone": "dim", "wrap": True, "lines": 4})
        return
    ui.at(x + pad, top, w - 2 * pad, room)
    got = ui.list("blocks of " + cat, ["  " + label for _i, label in items], -1,
                  {"id": "palette blocks"})
    if got is not None and 0 <= got < len(items):
        add_block(desk, items[got][0])
    if len(items) * rh <= room:
        ink = cols.get("block_" + cat, th["border"])
        for k in range(len(items)):
            cv.rect(x + pad + 2 * u, top + k * rh + 4 * u, 3 * u, rh - 8 * u, ink)


def add_block(desk, item):
    """A palette row activated: the block at the Blocks pane's cursor (on a
    block, the next "+ add block" after it), or the category's make-one row."""
    bu, be = _blocks(desk)
    if be is None or bu.blk_graduated:
        return
    if not item.startswith("\x00new") and not be.at_insert():
        for k in range(be.cur + 1, len(be.rows)):
            if be.rows[k].kind == "insert":
                be.cur = k
                break
    if item == _NEW["variables"][0]:
        bu._blk_new_variable()
    elif item == _NEW["lists"][0]:
        bu._blk_new_list()
    elif item == _NEW["myblocks"][0]:
        bu._blk_new_proc()
    elif item.startswith(_CALL):
        bu._blk_insert_call(item[len(_CALL):])
    else:
        bu._blk_insert_chosen(item)
    desk.ws._dirty = True


# -- the code the blocks make ----------------------------------------------------------

_made = [None, None]            # [the program's text it was made from, its lines]


def made_lines(be):
    """The blocks' code as lines, made again only when the program moved."""
    key = repr(be.program)
    if _made[0] != key:
        try:
            src = B.compile_blocks(be.program)
        except B.BlockError as e:
            src = "# " + str(e)
        _made[0] = key
        _made[1] = src.rstrip("\n").split("\n")
    return _made[1]


def made(desk, r, u):
    """The code the blocks make (EDITOR_LAYOUT.md's blocks cart), read only,
    numbered, in the Code tab's colours: a text view, which a drag and the
    wheel scroll."""
    ws = desk.ws
    _bu, be = _blocks(desk)
    if be is None:
        return
    x, y, w, h = r
    pad = 8 * u
    ui.at(x, y + pad, w, h - 2 * pad)
    ui.text_view("made", made_lines(be), {"gutter": True, "syntax": ws.code_layer._lang()})


def scroll(desk, c, d):
    """The wheel over a part that scrolls by rows: `d` notches."""
    st = desk.part_state
    if c == "maps":
        vx, vy = st.get("ref view", (0, 0))
        cx, cy = st.get("ref cell", (0, 0))
        st["ref view"] = (vx, max(0, vy + 3 * d))
        st["ref cell"] = (cx, max(0, cy + 3 * d))
        desk.ws._dirty = True
        return True
    return False


# -- the Reference (the Code workspace's context pane) ---------------------------------

_FOOT = 40                      # a Reference tab's foot: the call a pick puts at the caret


def _foot(desk, r, u, call, id):
    """The call a pick puts at the Code pane's caret, and Insert."""
    ws = desk.ws
    x, y, w, h = r
    row = 30 * u
    ui.at(x + 10 * u, y + h - row - 4 * u, w - 110 * u, row)
    ui.label(call, {"tone": "dim"})
    ui.at(x + w - 96 * u, y + h - row - 4 * u, 88 * u, row)
    if ui.button("Insert", "normal", {"id": id}) and ws.editor is not None:
        ws.history.code_burst_open()
        ws.editor.insert_text(call)
        ws._dirty = True


def ref_sounds(desk, r, u):
    """The Reference's Sounds (EDITOR_LAYOUT.md): a row a sound, its number,
    name, sketch and play tool; the picked one's `sfx(n)` goes at the caret."""
    mu, me = _music(desk)
    if me is None:
        ui.at(*r)
        ui.empty("music", "No sounds", ("This cart has no sound bank",))
        return
    ws = desk.ws
    th = ws.theme_colors
    cv = ws.sys_canvas
    st = desk.part_state
    n = len(me.bank.sfx)
    cur = max(0, min(n - 1, st.get("ref sound", 0)))
    x, y, w, h = r
    pad = 6 * u
    cell = 32 * u
    rh = _LIST_ROW * u
    lr = (x + pad, y + 4 * u, w - 2 * pad - cell, h - 8 * u - _FOOT * u)
    ui.at(*lr)
    got = ui.list("Sound calls", [Sound.row_label(me, k) for k in range(n)], cur,
                  {"id": "ref sounds"})
    if got is not None and 0 <= got < n and got != cur:
        cur = st["ref sound"] = got
        ws._dirty = True
    if n * rh <= lr[3]:
        playing = mu.music_preview
        for k, s in enumerate(me.bank.sfx):
            ry = lr[1] + k * rh
            _sketch(cv, th, s, lr[0] + lr[2] - 52 * u, ry + 6 * u, 44 * u, rh - 12 * u, u)
            ui.at(x + w - pad - cell, ry + (rh - cell) // 2, cell, cell)
            on = playing is not None and playing == ("sfx", k)
            if ui.tool("run", "Play", {"id": "ref play %d" % k, "current": on}):
                if on:
                    mu._stop_music_preview()
                else:
                    au = ws.audio
                    if au is not None:
                        au.focus()
                        au.sound_stop()
                        au.sfx(k)
                        mu.music_preview = ("sfx", k)
                ws._dirty = True
    _foot(desk, r, u, "sfx(%d)" % cur, "insert sfx")


def _map_rect(desk, r, u):
    x, y, w, h = r
    pad = 6 * u
    tw = 8 * u
    cols = max(1, (w - 2 * pad) // tw)
    rows = max(1, (h - _FOOT * u - pad) // tw)
    return (x + pad, y + pad, cols * tw, rows * tw), cols, rows, tw


def _map_view_at(desk, cols, rows):
    """The picked cell, and the view's corner kept round it."""
    ws = desk.ws
    tm = ws.tilemap
    st = desk.part_state
    if "ref cell" not in st:
        st["ref cell"] = _first_cell(tm)
    cx, cy = st["ref cell"]
    cx = max(0, min(tm.w - 1, cx))
    cy = max(0, min(tm.h - 1, cy))
    vx, vy = st.get("ref view", (0, 0))
    vx = max(min(vx, cx), cx - cols + 1)
    vy = max(min(vy, cy), cy - rows + 1)
    vx = max(0, min(vx, max(0, tm.w - cols)))
    vy = max(0, min(vy, max(0, tm.h - rows)))
    st["ref cell"] = (cx, cy)
    st["ref view"] = (vx, vy)
    return cx, cy, vx, vy


def _first_cell(tm):
    """Where the Reference's map opens: the first cell holding a tile."""
    for y in range(tm.h):
        for x in range(tm.w):
            if tm.mget(x, y):
                return (x, y)
    return (0, 0)


def map_view(desk, cv, th, r, u):
    """The Reference's Map: the cart's map at 1x from the view's corner, the
    picked cell under the cursor."""
    ws = desk.ws
    tm, sh = ws.tilemap, ws.sheet
    if tm is None or sh is None:
        return
    (mx, my, mw, mh), cols, rows, tw = _map_rect(desk, r, u)
    cx, cy, vx, vy = _map_view_at(desk, cols, rows)
    cv.rect(mx, my, mw, mh, th["surface_alt"])
    cv.clip(mx, my, mw, mh)
    for row in range(min(rows, tm.h - vy)):
        for col in range(min(cols, tm.w - vx)):
            t = tm.mget(vx + col, vy + row)
            if t:
                img = sh.tile_image(t, 0)
                if img is not None:
                    cv.spr(img, mx + col * tw, my + row * tw, u)
    cv.clip()
    desk._cursors.append((mx + (cx - vx) * tw, my + (cy - vy) * tw, tw, tw))


def map_foot(desk, r, u):
    ws = desk.ws
    if ws.tilemap is None:
        ui.at(*r)
        ui.empty("map", "No map", ("This cart has no map yet",))
        return
    cx, cy = desk.part_state.get("ref cell", (0, 0))
    _foot(desk, r, u, "mget(%d, %d)" % (cx, cy), "insert mget")


def log_rows(desk):
    """(text, file, row) for each error the Log names: the run's crash at its
    line, the code's own error."""
    ws = desk.ws
    out = []
    err, line = ws.cart_error, ws.crash_line
    if err:
        f = ws.player.crash_file or ws.code_file_name()
        if line:
            out.append(("%s:%d  %s" % (f, line, err), f, line - 1))
        else:
            out.append((str(err), None, None))
    if ws.code_err and ws.code_err_row is not None:
        f = ws.code_file_name()
        out.append(("%s:%d  %s" % (f, ws.code_err_row + 1, ws.code_err), f, ws.code_err_row))
    return out


def log(desk, r, u):
    """The Reference's Log: each error with its file and line; activating one
    puts the Code pane's caret there."""
    rows = log_rows(desk)
    if not rows:
        ui.at(*r)
        ui.empty("doc", "Nothing logged", ("Your game's errors show here, with their line",))
        return
    x, y, w, h = r
    ui.at(x + 6 * u, y + 4 * u, w - 12 * u, h - 8 * u)
    got = ui.list("Log", [t for t, _f, _r in rows], -1, {"id": "log"})
    if got is not None and 0 <= got < len(rows):
        goto(desk, rows[got][1], rows[got][2])


def goto(desk, name, row):
    """The Code pane on file `name`, its caret on `row`, and the keys."""
    ws = desk.ws
    if row is None:
        return
    if name and name != ws.code_file_name():
        ws.open_code_file(name)
    ed = ws.editor
    if ed is not None:
        ed.goto_row(row)
    for n, (pane, _r, _p) in enumerate(desk.panes()):
        if "code" in pane:
            desk.focus_pane(n)
    ws._dirty = True


def pointer(desk, c, r, u, px, py, click, down):
    """A press a part takes itself: a cell of the Reference's map. True
    where it took it."""
    if c != "maps" or desk.ws.tilemap is None or not (click or down):
        return False
    (mx, my, mw, mh), cols, rows, tw = _map_rect(desk, r, u)
    if not (mx <= px < mx + mw and my <= py < my + mh):
        return False
    st = desk.part_state
    vx, vy = st.get("ref view", (0, 0))
    st["ref cell"] = (vx + (px - mx) // tw, vy + (py - my) // tw)
    desk.ws._dirty = True
    return True


def keys(desk, c, i):
    """The arrows over the Reference's map move its cell. True where they
    acted."""
    if c != "maps" or desk.ws.tilemap is None:
        return False
    dx = i.steps("right") - i.steps("left")
    dy = i.steps("down") - i.steps("up")
    if not (dx or dy):
        return False
    st = desk.part_state
    cx, cy = st.get("ref cell", (0, 0))
    st["ref cell"] = (cx + dx, cy + dy)
    desk.ws._dirty = True
    return True
