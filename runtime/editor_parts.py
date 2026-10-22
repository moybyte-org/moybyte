# Map (grep -n a name to jump there):
#   PARTS           the panes the desk Editor draws on the verbs
#   actor           the World's Actor pane: the selected actor's props
#   stamps_label    the Stamps pane's line: the tag the stamp gives
#   sounds          the Sound workspace's list: the song, then a row a sound
#   song            the Song pane: the tracks, the current one marked
#   empty           a pane whose part is not built: what it holds
"""The desk Editor's own panes on the public UI API
(docs/media/look_2026-10/EDITOR_LAYOUT.md's World and Sound): each draws in
its pane's body inside `EditorDesk`'s frame, over the editor cores the
handheld tabs edit (`SceneEditor`, `MusicEditor`), and answers its taps and
keys through the verbs. A change is the core's own (its undo step), synced
to the running game as the tab's is."""

try:
    import ui
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime import ui

PARTS = ("actor", "sounds", "song", "log", "maps", "palette", "made")

_ROW = 36


def draw(desk, c, r, u):
    """Part `c` in body rect `r` (the frame is begun)."""
    if c == "actor":
        actor(desk, r, u)
    elif c == "sounds":
        sounds(desk, r, u)
    elif c == "song":
        song(desk, r, u)
    else:
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
    """The Sound workspace's list: the Song, then a row a sound, by its
    number and its name where it has one (moy-spec's optional `name`)."""
    mu, me = _music(desk)
    if me is None:
        return
    names = ["Song"]
    for k, s in enumerate(me.bank.sfx):
        name = getattr(s, "name", None)
        names.append("%d  %s" % (k, name) if name else "Sound %d" % k)
    song = me.view == me.SONG_VIEW
    cur = 0 if song else me.sfx_idx + 1
    ui.at(*r)
    got = ui.list("Sounds", names, cur, {"id": "sound list"})
    if got is not None and got != cur and got >= 0:
        if got == 0:
            if not song:
                me.toggle_view()
        else:
            if song:
                me.toggle_view()
            me.sfx_idx = got - 1
            me.step = 0
        desk.ws._dirty = True


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
