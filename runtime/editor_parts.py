# Map (grep -n a name to jump there):
#   PARTS           the panes the desk Editor draws on the verbs
#   actor           the World's Actor pane: the selected actor's props
#   stamps_label    the Stamps pane's line: the tag the stamp gives
#   sounds          the Sound workspace's list: the song, then a row a sound
#   song            the Song pane: the tracks, the current one marked
#   palette         the Blocks workspace's palette: the categories, then their blocks
#   made            the code the blocks make, read only
#   empty           a pane whose part is not built: what it holds
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
        sounds(desk, r, u)
    elif c == "song":
        song(desk, r, u)
    elif c == "palette":
        palette(desk, r, u)
    elif c != "made":               # the made code is drawn before the frame
        empty(c, r)


def empty(c, r):
    """A part not built yet: its name and what it will hold."""
    what = {"log": ("print() and errors, linking to their line",),
            "maps": ("The map, to pick a place from",),
            "palette": ("The blocks, by category",),
            "made": ("The code your blocks make",)}.get(c, ())
    ui.at(*r)
    ui.empty({"log": "doc", "maps": "map", "palette": "blocks", "made": "code"}.get(c, "doc"),
             {"log": "Log", "maps": "Map", "palette": "Blocks", "made": "Code"}.get(c, c),
             what)


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
        empty("palette", r)
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
        part = _CATS[side::2]
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
    in the Code tab's type and colours, numbered; the wheel scrolls it.
    Drawn before the frame begins, as the Code tab draws."""
    ws = desk.ws
    _bu, be = _blocks(desk)
    if be is None:
        return
    lines = made_lines(be)
    x, y, w, h = r
    pad = 8 * u
    rows = max(1, (h - 2 * pad) // ws.code_layout.lh)
    st = desk.part_state
    first = max(0, min(st.get("made top", 0), len(lines) - rows))
    st["made top"] = first
    ws.code_layer.draw_lines(lines, first, (x + pad, y + pad, w - 2 * pad, h - 2 * pad))


def scroll(desk, c, d):
    """The wheel over a part that scrolls by rows: `d` notches."""
    st = desk.part_state
    if c == "made":
        st["made top"] = max(0, st.get("made top", 0) + 3 * d)
        desk.ws._dirty = True
        return True
    return False
