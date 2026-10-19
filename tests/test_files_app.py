"""The Files system app (#108, docs/look_2026-10.md step 21): the places and
a place's files in columns, drawn with the public UI API's verbs; its verbs
(rename in a dialog, copy, trash, restore, and emptying the Trash, which asks
first), the copy-on-use reuse actions, and Paint's shared OPEN picker and
autosave riding the same layer."""

from pathlib import Path

from runtime import files_app, host_app, moy_carts
from ws_helpers import quiesce, shelf, select_on_home  # noqa: E402
from ws_helpers import build_ws as _ws

ROOT = Path(__file__).resolve().parent.parent
_DT = 1 / 30
# The handheld's table geometry at fs 1 (style.json's `group` caption, the
# `value_row` header and the `row` stack).
CAPTION, HEAD, ROW = 19, 18, 20


def _open_app(ws, title):
    select_on_home(ws, title)
    ws.open()
    ws.input.begin_frame()
    ws.frame(_DT)


def _seed_drawing(carts, name, w=320, h=240, color=14):
    blob = moy_carts.encode_moyimg(w, h, bytes((color,)) * (w * h))
    return moy_carts.save_file("drawings", name, blob, carts)


def _files(ws):
    """Files opened on the home, its first frames drawn: (app, driver)."""
    _open_app(ws, "Files")
    drv = host_app.ConsoleDriver(ws)
    _frames(drv)
    return ws.files_app, drv


def _frames(drv, n=3):
    for _ in range(n):
        drv.ws.mark_dirty()
        drv.frame(_DT)


def _press(drv, *keys):
    for k in keys:
        drv.press(k)
        _frames(drv)


def _type(drv, text):
    """Typed keys, each its own press: a frame with no key between two."""
    for ch in text:
        drv.type_char(ord(ch) if isinstance(ch, str) else ch)
        _frames(drv, 2)


def _row_xy(app, i, id="files", top=CAPTION + HEAD):
    """The middle of row `i` of a table the app drew last frame, its rows
    `top` under the table's box (a caption and a header row)."""
    x, y, w, _h = app._ui.box(id)
    return x + w // 2, y + top + i * ROW + ROW // 2


def _tap(drv, x, y):
    drv.click(x, y)
    _frames(drv)


def _art(ws, drv):
    """The Art place's page, reached as a person does: down twice, Enter."""
    app = ws.files_app
    _press(drv, "down", "down", "a")
    assert (app.place, app.level) == (files_app.ART, "files")
    return app


# -- the app and its pages ---------------------------------------------------------

def test_files_cart_is_well_formed_and_the_app_opens(tmp_path):
    import json
    man = json.loads((ROOT / "system_carts" / "moybyte.files.moy" / "manifest.json")
                     .read_text(encoding="utf-8"))
    assert man["moybyte"]["type"] == "app" and man["version"] >= 1
    assert "files" in man["moybyte"]["permissions"]
    compile((ROOT / "system_carts" / "moybyte.files.moy" / "main.py").read_text(),
            "main.py", "exec")

    ws = _ws(tmp_path)
    app, _drv = _files(ws)
    assert ws.cart_error is None
    assert ws.wm.top_kind() == "files"
    assert (app.place, app.level) == (files_app.PROJECTS, "places")
    assert app._focused == "Places"


def test_the_handheld_walks_places_then_files_and_b_comes_back(tmp_path):
    carts = str(tmp_path / "carts")
    ws = _ws(tmp_path)
    _seed_drawing(carts, "dragon")
    app, drv = _files(ws)
    _art(ws, drv)
    assert app.names_shown() == ["dragon"]
    assert app._focused == "files"
    _press(drv, "b")
    assert app.level == "places" and app.place == files_app.ART
    assert ws.wm.top_kind() == "files", "b left the page, not the app"


def test_a_tap_on_a_place_opens_it(tmp_path):
    ws = _ws(tmp_path)
    app, drv = _files(ws)
    keys = [p[0] for p in app.shown_places()]
    _tap(drv, *_row_xy(app, keys.index(files_app.TRASH), "Places", 0))
    assert (app.place, app.level) == (files_app.TRASH, "files")


def test_the_places_count_their_files_and_themes_show_once_there_are_any(tmp_path):
    carts = str(tmp_path / "carts")
    ws = _ws(tmp_path)
    _seed_drawing(carts, "one")
    _seed_drawing(carts, "two")
    app, _drv = _files(ws)
    assert app._count(files_app.ART) == 2
    assert app._count(files_app.PROJECTS) == len(app._nav.projects())
    assert files_app.THEMES not in [p[0] for p in app.shown_places()]


def test_a_tap_on_the_current_row_opens_it_and_on_another_makes_it_current(tmp_path):
    carts = str(tmp_path / "carts")
    ws = _ws(tmp_path)
    _seed_drawing(carts, "one")
    _seed_drawing(carts, "two")
    app, drv = _files(ws)
    _art(ws, drv)
    assert app.cur == 0
    _tap(drv, *_row_xy(app, 1))
    assert app.sel_name() == app.names_shown()[1]
    assert ws.wm.top_kind() == "files", "the first tap only made it current"
    _tap(drv, *_row_xy(app, 1))
    assert ws.wm.top_kind() == "artwork"
    assert ws.artwork.doc_name() == app.names_shown()[1]


def test_the_verbs_are_the_current_rows(tmp_path):
    carts = str(tmp_path / "carts")
    ws = _ws(tmp_path)
    _seed_drawing(carts, "dragon")
    moy_carts.save_file("docs", "note", "hi", carts)
    moy_carts.save_file("docs", "tool.py", "print(1)", carts)
    app, _drv = _files(ws)
    app.open_place(files_app.ART)
    assert app._action_labels() == app.PICTURE_ACTIONS
    app.open_place("docs", keep="note")
    assert app._action_labels() == app.DOC_ACTIONS
    app.select("tool.py")
    assert app._action_labels()[0] == "RUN"
    app.open_place(files_app.PROJECTS)
    assert app._action_labels() == app.PROJECT_ACTIONS
    app.open_place(files_app.TRASH)
    assert app._action_labels() == app.TRASH_ACTIONS


# -- the verbs ---------------------------------------------------------------------

def test_gallery_lists_rename_copy_trash_restore(tmp_path):
    carts = str(tmp_path / "carts")
    ws = _ws(tmp_path)
    _seed_drawing(carts, "dragon")
    app, drv = _files(ws)
    _art(ws, drv)
    assert "dragon" in app.names_shown()

    # COPY numbers upward, and the copy is current
    app._act("COPY", "dragon")
    assert "dragon_2" in app.names_shown() and app.sel_name() == "dragon_2"
    assert app.status == "Duplicated", "the verb's word, Duplicate, in its report (R4-24)"

    # RENAME in a dialog: focus opens in its field, the keys type, Enter commits
    app._act("NAME")
    _frames(drv)
    assert app.dialog == "rename" and app.rename_text == "dragon_2"
    assert app._focused == "Name"
    _type(drv, "\b\b")
    assert app.rename_text == "dragon"
    _type(drv, "_tower")
    _type(drv, "\r")
    assert app.dialog is None
    assert "dragon_tower" in app.names_shown() and "dragon_2" not in app.names_shown()
    assert app.sel_name() == "dragon_tower"

    # DELETE -> the restorable Trash -> RESTORE
    app._act("DEL", "dragon_tower")
    assert "dragon_tower" not in app.names_shown()
    assert ("drawings", "dragon_tower") in app.trash
    app.open_place(files_app.TRASH)
    assert app.names_shown() == ["dragon_tower"]
    app._act("RESTORE", "dragon_tower")
    assert moy_carts.load_file("drawings", "dragon_tower", carts) is not None
    assert ("drawings", "dragon_tower") not in app.trash


def test_rename_cancels_by_its_button_and_by_b(tmp_path):
    carts = str(tmp_path / "carts")
    ws = _ws(tmp_path)
    _seed_drawing(carts, "dragon")
    app, drv = _files(ws)
    _art(ws, drv)
    app._act("NAME")
    _frames(drv)
    _type(drv, "xyz")
    _press(drv, "down")                    # the field to the buttons: Cancel first
    assert app._focused == "Cancel"
    _press(drv, "a")
    assert app.dialog is None and app.names_shown() == ["dragon"]
    assert app._focused == "files", "focus went back to the list"
    app._act("NAME")
    _frames(drv)
    _press(drv, "b")
    assert app.dialog is None and app.names_shown() == ["dragon"]


def test_nothing_under_a_dialog_takes_input(tmp_path):
    carts = str(tmp_path / "carts")
    ws = _ws(tmp_path)
    _seed_drawing(carts, "one")
    _seed_drawing(carts, "two")
    app, drv = _files(ws)
    _art(ws, drv)
    row = _row_xy(app, 1)
    app._act("NAME")
    _frames(drv)
    _tap(drv, *row)
    assert app.cur == 0 and app.dialog == "rename", "the list under it took the tap"


def test_backspace_on_the_list_moves_the_current_file_to_the_trash(tmp_path):
    carts = str(tmp_path / "carts")
    ws = _ws(tmp_path)
    _seed_drawing(carts, "dragon")
    app, drv = _files(ws)
    _art(ws, drv)
    _type(drv, "\b")
    assert app.names_shown() == []
    assert ("drawings", "dragon") in app.trash
    assert app.status == "Moved to the Trash"


def test_emptying_the_trash_asks_first_and_cancel_is_where_focus_opens(tmp_path):
    carts = str(tmp_path / "carts")
    ws = _ws(tmp_path)
    _seed_drawing(carts, "dragon")
    app, drv = _files(ws)
    _art(ws, drv)
    app._act("DEL", "dragon")
    app.open_place(files_app.TRASH)
    app._act("EMPTY")
    _frames(drv)
    assert app.dialog == "empty" and app._focused == "Cancel"
    assert app.trash, "nothing goes before the answer"
    _press(drv, "a")
    assert app.dialog is None and app.trash
    app._act("EMPTY")
    _frames(drv)
    _press(drv, "right", "a")
    assert app.dialog is None and not app.trash
    assert moy_carts.load_file("drawings", "dragon", carts) is None


def test_reuse_actions_are_copies(tmp_path):
    carts = str(tmp_path / "carts")
    ws = _ws(tmp_path)
    _seed_drawing(carts, "dragon", color=20)
    app, drv = _files(ws)
    app.open_place(files_app.ART, keep="dragon")

    # WALL: copy-on-set -- the wallpaper copy + My Art bg hold the pixels
    # (plus a #108 phase-2 provenance stamp, so compare decoded pixels).
    app._act("WALL", "dragon")
    ws.look.tick(0.0)                      # the change shows at the next frame
    assert ws.look.wallpaper_id == "moybyte.my_art" == ws.look.PICTURE
    px = moy_carts.decode_moyimg(moy_carts.load_file("drawings", "dragon", carts))
    # ...and the theme carries it: My Art's copy is custom's picture.
    assert moy_carts.decode_moyimg(ws.look.picture().decode()) == px
    assert moy_carts.decode_moyimg(moy_carts.load_artwork(carts)) == px
    wall = next(c for c in moy_carts.scan(carts) if c["title"] == "My Art")
    assert moy_carts.decode_moyimg(wall["images"]["bg"]) == px
    assert moy_carts.read_provenance(moy_carts.load_artwork(carts))[0] == \
        "drawings/dragon"

    # Editing the drawing afterwards changes NOTHING until sent again.
    _seed_drawing(carts, "dragon", color=33)
    assert moy_carts.decode_moyimg(moy_carts.load_artwork(carts)) == px

    # GAME: a 320x240 copy lands in the project picked in the dialog.
    app._act("GAME", "dragon")
    assert app.dialog == "game"
    # System-app identities are never offered as projects.
    assert "Files" not in app.project_names
    assert "Paint" not in app.project_names
    assert "Calc" not in app.project_names
    app.target = app.project_names.index("Star Catcher")
    _frames(drv)
    _press(drv, "a")                       # Enter on the focused target adds it
    assert app.dialog is None
    target = next(c for c in moy_carts.scan(carts) if c["title"] == "Star Catcher")
    assert "bg" in target["images"]
    assert app.status == "Added to Star Catcher"


def test_delete_of_the_open_drawing_cannot_break_paint(tmp_path):
    carts = str(tmp_path / "carts")
    ws = _ws(tmp_path)
    name = _seed_drawing(carts, "doomed")
    ws.artwork.open_named(name)
    app, _drv = _files(ws)
    app.open_place(files_app.ART)
    app._act("DEL", name)
    # Paint now opens a fresh canvas under the stale pointer -- no crash.
    _open_app(ws, "Paint")
    assert ws.cart_error is None
    assert ws.wm.top_kind() == "artwork"


def test_paint_open_picker_switches_documents(tmp_path):
    carts = str(tmp_path / "carts")
    ws = _ws(tmp_path)
    _seed_drawing(carts, "one", color=11)
    _seed_drawing(carts, "two", color=12)
    _open_app(ws, "Paint")
    app = ws.artwork_app
    app._action(3)                       # OPEN
    assert app.mode == "open"
    assert set(app.grid.names) >= {"one", "two"}
    ws.frame(_DT)                        # a draw pass lays the grid out
    cell = app.grid._cell_rect(0)
    app._open_tap(cell[0] + 2, cell[1] + 2)
    assert app.mode == "paint"
    assert ws.artwork.doc_name() == app.grid.names[0]


def test_paint_autosaves_on_the_idle_debounce(tmp_path):
    carts = str(tmp_path / "carts")
    ws = _ws(tmp_path)
    _open_app(ws, "Paint")
    app = ws.artwork_app
    assert ws.artwork.doc_name() is None       # nothing saved yet
    app.doc.snapshot()
    app.doc.put(5, 5, 14)
    app.doc.finish()
    app._mark_changed()
    app.draw(app.AUTOSAVE_S + 0.1)             # the debounce flushes
    name = ws.artwork.doc_name()
    assert name is not None
    data = moy_carts.decode_moyimg(moy_carts.load_file("drawings", name, carts))
    assert data[2][5 * app.doc.W + 5] == 14


def test_new_drawing_keeps_the_old_file(tmp_path):
    carts = str(tmp_path / "carts")
    ws = _ws(tmp_path)
    _open_app(ws, "Paint")
    app = ws.artwork_app
    app.doc.snapshot()
    app.doc.put(1, 1, 22)
    app.doc.finish()
    app._mark_changed()
    app._action(0)
    app._action(0)                             # armed double-tap = NEW
    assert app.status == "NEW DRAWING"
    names = moy_carts.list_files("drawings", carts)
    assert len(names) == 1                     # the old drawing kept its file
    old = moy_carts.decode_moyimg(moy_carts.load_file("drawings", names[0], carts))
    assert old[2][1 * 320 + 1] == 22
    assert ws.artwork.doc_name() not in names  # fresh doc, unwritten until drawn on


def test_files_is_app_rejects_lookalikes(tmp_path):
    ws = _ws(tmp_path)
    real = next(c for c in ws.carts.all if c.get("title") == "Files")
    assert ws.files_app.is_app(real)
    fake = dict(real)
    fake["title"] = "files"
    assert not ws.files_app.is_app(fake)
    fake2 = dict(real)
    fake2["permissions"] = ["graphics", "input"]
    assert not ws.files_app.is_app(fake2)


# -- provenance: "used in:" + the one-tap UPDATE (#108 phase 2) -------------------

def test_usage_tracks_stale_copies_and_update_clears_them(tmp_path):
    carts = str(tmp_path / "carts")
    ws = _ws(tmp_path)
    _seed_drawing(carts, "dragon", color=20)
    # Copy it into a project (with a provenance stamp) + set as wallpaper.
    copies = ws.files_app._copies
    ti = copies.targets().index("Star Catcher")
    assert copies.attach(ti, "dragon")
    assert copies.set_wallpaper("dragon")

    rows = copies.usage("dragon")
    labels = {r["label"] for r in rows}
    assert "Star Catcher" in labels and "WALLPAPER" in labels
    assert all(not r["stale"] for r in rows)          # fresh copies

    # Edit the source drawing: every copy is now stale (pull-based detection).
    _seed_drawing(carts, "dragon", color=33)
    rows = copies.usage("dragon")
    assert all(r["stale"] for r in rows)

    # UPDATE (send again) the project copy -> only it clears.
    game_row = next(r for r in rows if r["label"] == "Star Catcher")
    assert copies.resend(game_row, "dragon")
    rows = {r["label"]: r["stale"] for r in copies.usage("dragon")}
    assert rows["Star Catcher"] is False
    assert rows["WALLPAPER"] is True                   # not re-sent yet


def test_files_app_use_action_lists_and_resends(tmp_path):
    carts = str(tmp_path / "carts")
    ws = _ws(tmp_path)
    _seed_drawing(carts, "dragon", color=20)
    ti = ws.artwork.targets().index("Star Catcher")
    ws.artwork.attach(ti, "dragon")
    app, drv = _files(ws)
    app.open_place(files_app.ART, keep="dragon")
    app._act("USE", "dragon")
    assert app.dialog == "used"
    assert any(r["label"] == "Star Catcher" for r in app.used_rows)
    # Make it stale, then send it again from the dialog.
    _seed_drawing(carts, "dragon", color=33)
    app._act("USE", "dragon")
    idx = next(i for i, r in enumerate(app.used_rows)
               if r["label"] == "Star Catcher")
    assert app.used_rows[idx]["stale"] is True
    app._resend(idx)
    assert app.used_rows[idx]["stale"] is False
    _frames(drv)
    _press(drv, "b")
    assert app.dialog is None


def test_docs_and_tables_kinds_get_the_open_action(tmp_path):
    carts = str(tmp_path / "carts")
    ws = _ws(tmp_path)
    moy_carts.save_file("docs", "note", "hi", carts)
    app, _drv = _files(ws)
    app.open_place("docs")
    assert "OPEN" in app._action_labels()


def test_send_sprites_to_files_producer(tmp_path):
    carts = str(tmp_path / "carts")
    ws = _ws(tmp_path)
    # Open a project so ws.project.sheet is a real sprite sheet.
    ws.carts_store.create("Doodle", carts, type="game")
    ws.carts.apply(shelf(carts))
    ws.open_in_editor(next(c for c in ws.carts.all if c.get("title") == "Doodle"))
    ws.project.sheet.pset(0, 0, 9)
    name = ws.send_sprites_to_files()
    assert name in moy_carts.list_files("sprites", carts)
    assert moy_carts.load_file("sprites", name, carts) == ws.project.sheet.to_hex()


# -- painting ----------------------------------------------------------------------

def test_a_settled_files_page_paints_nothing(tmp_path):
    """The verbs' settled answer, end to end: with no input the redraw gate
    draws no frame, and a press that moves nothing draws one frame of the
    same pixels and asks for none after it."""
    ws = _ws(tmp_path)
    _open_app(ws, "Files")
    quiesce(ws)
    drv = host_app.ConsoleDriver(ws)
    for _ in range(4):
        drv.frame(_DT)
    drawn = ws._frames_drawn
    for _ in range(10):
        drv.frame(_DT)
    assert ws._frames_drawn == drawn, "an idle Files page painted"
    before = bytes(ws.sys_canvas._buf)
    drv.press("up")                     # on the first place: nothing to move to
    for _ in range(6):
        drv.frame(_DT)
    assert ws._frames_drawn == drawn + 1, "one frame for the press, then none"
    assert bytes(ws.sys_canvas._buf) == before


# -- the desk ----------------------------------------------------------------------

def test_the_desk_shows_the_places_beside_the_files(tmp_path):
    carts = str(tmp_path / "carts")
    _seed_drawing(carts, "dragon")
    ws = host_app.build_workstation(carts, sys_size=(1024, 600), panel_diagonal_in=7.0,
                                    font_scale=1, windowed=True)
    ws.open_desk()
    ws.open_app(ws._apps_by_id["files"])
    drv = host_app.ConsoleDriver(ws)
    _frames(drv, 4)
    app = ws.files_app
    assert app.layout.two and app.layout.row_h == 28
    assert app._focused == "files", "the desk opens on the projects' rows"
    assert app._ui.box("Places") is not None and app._ui.box("files") is not None
    app._focus = "Places"
    _frames(drv)
    _press(drv, "down", "down")
    assert app.place == files_app.ART and app.names_shown() == ["dragon"]
    assert app._focused == "Places", "a place opens beside the list, focus stays"


def test_the_desks_list_holds_whole_rows_above_the_verbs(tmp_path):
    """A table's rows view is a whole number of rows at every size, so the
    strip's edge never cuts one (the desk's Contrast window cut a row through
    its x-height); the heights it reckons with are the engine's."""
    import json
    style = json.loads((Path(__file__).resolve().parent.parent / "native" / "moy_ui"
                        / "style.json").read_text())["metrics"]
    vr, grp, row = style["value_row"], style["group"], style["row"]
    assert files_app.HEAD == (vr["head"], vr["head_desk"])
    assert files_app.CAPTION == (grp["caption"], grp["caption_desk"])
    ws = host_app.build_workstation(str(Path(tmp_path) / "carts"), sys_size=(1024, 600),
                                    panel_diagonal_in=7.0, font_scale=1, windowed=True)
    assert ws is not None
    for w in (360, 520):
        for h in range(300, 600, 7):
            lay = files_app.FilesLayout(w, h, 1, windowed=True)
            assert lay.desk
            top = files_app.HEAD[1] + (0 if lay.two else files_app.CAPTION[1])
            assert (lay.list[3] - top) % lay.row_h == 0, (w, h)
            assert lay.row_h == row["stack_desk"]


# -- the projects' rows and the foot (design review D-11, m02, m10) ---------------------

def test_a_projects_row_is_the_carts_title_with_its_cover(tmp_path):
    """A project's row names the cart by its title, as the home does, and its
    picture is its cover (`nav.cover`: False while the decode lands, None
    for a cart with none, which draws its generated one); the row opens the
    cart it names."""
    from runtime import cover_cache, shell_servers
    assert shell_servers._COVER_HALF == cover_cache.HALF
    ws = _ws(tmp_path)
    app, drv = _files(ws)
    _press(drv, "a")
    assert (app.place, app.level) == (files_app.PROJECTS, "files")
    titles = {c.get("title") for c in app._nav.projects()}
    assert set(app.names_shown()) == titles
    assert all(r[3] == "" for r in app.rows), "the picture is the cover, not a kind's icon"
    i = app.names_shown().index("Harpoon Pop")
    assert app.door("Harpoon Pop", kind=files_app.PROJECTS) == ("editor", app.rows[i][4])
    got = None
    for _ in range(40):                       # the covers land over frames
        _frames(drv)
        got = app._nav.cover(app.rows[i][4])
        if got:
            break
    assert got and got.w == 64, "Harpoon Pop's cover at its half"
    coverless = [r[4] for r in app.rows if r[0] == "Coin Quest"]
    for _ in range(40):
        if app._nav.cover(coverless[0]) is not False:
            break
        _frames(drv)
    assert app._nav.cover(coverless[0]) is None


def test_the_foot_is_a_bar_with_the_primary_verb_at_its_right(tmp_path):
    """The current row's verbs sit at the foot's right, the one Enter does
    (Open) last, and no mouse button wears a key's sign."""
    ws = _ws(tmp_path)
    app, drv = _files(ws)
    _press(drv, "a")
    play, opn = app._ui.box("verb PLAY"), app._ui.box("verb OPEN")
    assert play is not None and opn is not None
    assert play[0] < opn[0], "Open, the primary, is right of Play"
    fx, _fy, fw, _fh = app.layout.foot
    assert opn[0] + opn[2] >= fx + fw - app.layout.pad - 1, "the verbs end at the foot's right"
    assert files_app.VERB["OPEN"][1] != "enter"
