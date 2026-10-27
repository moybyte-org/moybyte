"""The Files ROUTER (step 2 of docs/text_editing_2026-09.md): an item opens by
WHAT IT IS -- a `.moy` folder is a project, a cart's main file is code, an
image is Paint, anything else textual is a page in its mode -- and the carts
root sits on the Files shelf beside the user-files kinds.

Every case here drives the real Workstation: the assertion is where the
console ended up, not which method was called."""

from runtime import (files_app, host_app, moy_carts, system_api,
                     text_modes)
from ws_helpers import select_on_home  # noqa: E402
import pytest
from ws_helpers import boot_on_fixture

@pytest.fixture(autouse=True)
def _on_the_night_fixture(monkeypatch):
    """Every workstation here boots on the `night` fixture (ws_helpers): a
    palette theme with no look, drawn in the default's look flat."""
    boot_on_fixture(monkeypatch)


def _open_files(ws):
    select_on_home(ws, "Files")
    ws.open()
    ws.input.begin_frame()
    ws.frame(1 / 30)
    return ws.files_app


def _a_project(ws):
    return ws.files_app._nav.projects()[0]


def _seed_drawing(carts, name):
    blob = moy_carts.encode_moyimg(8, 8, bytes((14,)) * 64)
    return moy_carts.save_file("drawings", name, blob, carts)


# -- the shelf ---------------------------------------------------------------

def test_the_shelf_carries_the_three_roots_a_person_touches(tmp_path):
    ws = host_app.build_workstation(str(tmp_path / "carts"))
    app = _open_files(ws)
    shown = app.shown_places()
    assert [p[0] for p in shown[:3]] == [files_app.PROJECTS, "docs", files_app.ART]
    assert [p[1] for p in shown[:3]] == ["Projects", "Notes", "Art"]
    assert dict((p[0], p[3]) for p in shown)[files_app.ART] == ("drawings", "sprites")
    # the carts root is NOT a files kind and is never asked of the files store
    assert files_app.PROJECTS not in moy_carts.FILE_KINDS


def test_the_projects_root_lists_one_row_per_moy_folder(tmp_path):
    ws = host_app.build_workstation(str(tmp_path / "carts"))
    app = _open_files(ws)
    app.open_place(files_app.PROJECTS)
    carts = app._nav.projects()
    assert app.rows, "the seeded store has projects"
    assert len(app.rows) == len(carts) == app.counts[files_app.PROJECTS]
    for name, kind, _label, _icon, cart in app.rows:
        assert kind == files_app.PROJECTS and cart in carts   # a folder, ONE item
        assert "/" not in name and not name.endswith(".moy")  # shown, not its path
    # and the roster is the pickers' -- system apps are not projects
    titles = {c.get("title") for c in carts}
    assert "Files" not in titles and "Paint" not in titles


# -- the rules, in order -----------------------------------------------------

def test_a_moy_folder_opens_the_project_editor_and_never_a_listing(tmp_path):
    ws = host_app.build_workstation(str(tmp_path / "carts"))
    app = _open_files(ws)
    app.open_place(files_app.PROJECTS)
    cart = app.rows[0][4]

    assert app.door(app.rows[0][0], kind=files_app.PROJECTS) == ("editor", cart)
    app._open_row(0)

    assert ws.wm.top_kind() == "menu"          # the Editor, not a Files listing
    assert ws.cart is cart
    assert app.place == files_app.PROJECTS      # Files did not become a browser


def test_a_cart_main_lands_on_the_code_tab(tmp_path):
    ws = host_app.build_workstation(str(tmp_path / "carts"))
    app = _open_files(ws)
    cart = _a_project(ws)
    main = cart.get("main", "main.py")

    assert app.door(main, cart=cart) == ("code", cart)
    assert app.route(main, cart=cart) == "code"

    assert ws.wm.top_kind() == "menu"
    assert ws.cart is cart
    assert ws.menu_view == "code"
    assert ws.editor is not None and ws.editor.text() == cart["src"]


def test_a_lua_carts_main_lands_on_the_code_tab_too(tmp_path):
    ws = host_app.build_workstation(str(tmp_path / "carts"))
    app = _open_files(ws)
    cart = dict(_a_project(ws), runtime="lua", main="main.lua")
    assert app.door("main.lua", cart=cart) == ("code", cart)
    assert app.door("main.py", cart=cart)[0] == text_modes.CODE   # not this cart's


def test_an_image_lands_in_paint(tmp_path):
    carts = str(tmp_path / "carts")
    _seed_drawing(carts, "dragon")
    ws = host_app.build_workstation(carts)
    app = _open_files(ws)
    app.open_place(files_app.ART)

    assert app.door("dragon", kind="drawings") == ("paint", "dragon")
    app._act("OPEN", "dragon")

    assert ws.wm.top_kind() == "artwork"
    assert ws.artwork.doc_name() == "dragon"


def test_a_note_opens_the_text_page_in_markdown_mode(tmp_path):
    """The text door is the NOTES CART now (step 3), reached by its `editor`
    marker permission -- so what this asserts is where the console ended up and
    what the cart's handle holds, not which app was poked."""
    carts = str(tmp_path / "carts")
    moy_carts.save_file("docs", "story", "once upon a time", carts)
    ws = host_app.build_workstation(carts)
    app = _open_files(ws)
    app.open_place("docs")

    assert app.door("story", kind="docs") == (text_modes.MD, "story")
    assert app.route("story", kind="docs") == text_modes.MD

    ws.input.begin_frame()
    ws.frame(1 / 30)
    assert ws.wm.top_is_player()
    assert ws.cart.get("title") == "Notes"
    ed = ws.player.ns["ed"]
    assert (ed.name(), ed.mode()) == ("story", text_modes.MD)
    assert ed.text() == "once upon a time"


def test_a_json_file_is_routed_to_json_mode(tmp_path):
    """A project's own files are json by NAME (the manifest and the config) and
    open through that PROJECT's Editor (step 5) -- never straight into the
    handle, because the return is what re-reads the folder."""
    ws = host_app.build_workstation(str(tmp_path / "carts"))
    app = _open_files(ws)
    cart = _a_project(ws)

    for name in ("config.json", "manifest.json", "scores.json"):
        assert app.door(name, cart=cart) == (text_modes.JSON, name)

    assert app.route("manifest.json", cart=cart) == text_modes.JSON
    ws.input.begin_frame()
    ws.frame(1 / 30)
    assert ws.wm.top_is_player()
    assert ws.cart.get("title") == "Notes"
    ed = ws.player.ns["ed"]
    assert (ed.name(), ed.mode()) == ("manifest.json", text_modes.JSON)
    assert ed.kind == moy_carts.project_kind(cart["path"])
    # the project was opened on the way, so leaving comes back to its Config tab
    assert ws._project_return is cart


def test_anything_else_textual_falls_through_to_plain_text(tmp_path):
    ws = host_app.build_workstation(str(tmp_path / "carts"))
    app = _open_files(ws)
    cart = _a_project(ws)
    assert app.door("notes.txt", cart=cart) == (text_modes.TEXT, "notes.txt")
    assert app.door("sheet", kind="tables") == (text_modes.TEXT, "sheet")
    assert app.door("song", kind="music") == (text_modes.TEXT, "song")


def test_a_missing_app_degrades_to_the_status_line(tmp_path):
    ws = host_app.build_workstation(str(tmp_path / "carts"))
    app = _open_files(ws)
    ws.carts.apply([c for c in ws.carts.all
                    if not system_api.is_text_app(c)])
    assert app.route("story", kind="docs") is None
    assert app.status == "No text app"
    ws._apps_by_id.pop("artwork")
    assert app.route("dragon", kind="drawings") is None
    assert app.status == "No Paint app"
    assert app.route("gone.moy", kind=files_app.PROJECTS) is None
    assert app.status == "Can't open"


# -- the return: what Files opens comes back to Files -------------------------
#
# Files is a cart app, so everything the router opens REPLACES it. Each door
# below is driven to its real exit gesture and asserted on where the console
# ended up plus what Files was still showing -- the return must land on the
# shelf the person left, never on the app's root and never at home.

def _tap_x(ws):
    """The OS bar's context-X, through the ROUTER (where the bar contract is);
    in the Editor, whose one bar has no way back (look step 30), B."""
    if ws.wm.top_kind() == "menu":
        drv = host_app.ConsoleDriver(ws)
        drv.press("b")
        drv.frame(1 / 30)
        drv.frame(1 / 30)
        return
    x, y, w, h = ws.layout.context_x_btn
    ws.pointer.place(x + w // 2, y + h // 2)
    ws.pointer.click = True
    ws.handle_pointer()
    ws.pointer.click = False
    ws.input.begin_frame()
    ws.frame(1 / 30)


def test_a_note_comes_back_to_files_and_files_still_exits(tmp_path):
    """The wedge (T-Deck, 2026-09-07): the note returned fine, and then the X
    on Files was dead -- the spent run caller made it pop Files back to Files."""
    carts = str(tmp_path / "carts")
    moy_carts.save_file("docs", "story", "once upon a time", carts)
    ws = host_app.build_workstation(carts)
    app = _open_files(ws)
    _key(ws, app, "down")
    ws.frame(1 / 30)
    _key(ws, app, "a")                         # Notes
    ws.frame(1 / 30)
    assert (app.place, app.level) == ("docs", "files")
    app._act("OPEN", "story")
    ws.input.begin_frame()
    ws.frame(1 / 30)
    assert ws.cart.get("title") == "Notes"

    _tap_x(ws)
    assert ws.wm.top_kind() == "files"
    assert (app.place, app.level, app.sel_name()) == ("docs", "files", "story")

    _tap_x(ws)
    assert ws.wm.top_kind() == "launcher"


def test_a_drawing_comes_back_from_paint_to_the_same_shelf(tmp_path):
    carts = str(tmp_path / "carts")
    _seed_drawing(carts, "dragon")
    ws = host_app.build_workstation(carts)
    app = _open_files(ws)
    app.open_place(files_app.ART)
    app.level = "files"
    app._act("OPEN", "dragon")
    ws.input.begin_frame()
    ws.frame(1 / 30)
    assert ws.wm.top_kind() == "artwork"

    _tap_x(ws)
    assert ws.wm.top_kind() == "files"
    assert (app.place, app.level) == (files_app.ART, "files")


def test_a_project_comes_back_from_the_editor_to_the_projects_root(tmp_path):
    ws = host_app.build_workstation(str(tmp_path / "carts"))
    app = _open_files(ws)
    app.open_place(files_app.PROJECTS)
    app.level = "files"
    app.cur = 1
    app._open_row(1)
    assert ws.wm.top_kind() == "menu"

    _tap_x(ws)
    assert ws.wm.top_kind() == "files"
    assert (app.place, app.level, app.cur) == (files_app.PROJECTS, "files", 1)


def test_a_project_file_returns_through_the_editor_and_on_to_files(tmp_path):
    """The full step-5 chain from the Files side: the file comes back to the
    Config tab THROUGH the loader, and leaving that Editor comes back here."""
    ws = host_app.build_workstation(str(tmp_path / "carts"))
    app = _open_files(ws)
    app.open_place(files_app.PROJECTS)
    cart = app.rows[0][4]
    app.route("manifest.json", cart=cart)
    ws.input.begin_frame()
    ws.frame(1 / 30)
    assert ws.cart.get("title") == "Notes"

    _tap_x(ws)
    assert ws.wm.top_kind() == "menu" and ws.menu_view == "cards"

    _tap_x(ws)
    assert ws.wm.top_kind() == "files"
    assert app.place == files_app.PROJECTS


def test_files_itself_exits_to_the_launcher(tmp_path):
    ws = host_app.build_workstation(str(tmp_path / "carts"))
    _open_files(ws)
    _tap_x(ws)
    assert ws.wm.top_kind() == "launcher"


def test_going_home_from_inside_ends_the_return(tmp_path):
    """HOME is HOME: a kid who leaves Paint by the bar's home button lands on
    the launcher, and the NEXT unrelated exit must not be dragged into Files."""
    carts = str(tmp_path / "carts")
    _seed_drawing(carts, "dragon")
    ws = host_app.build_workstation(carts)
    app = _open_files(ws)
    app.open_place(files_app.ART)
    app._act("OPEN", "dragon")
    ws.input.begin_frame()
    ws.frame(1 / 30)

    ws.go_home()
    assert ws.wm.top_kind() == "launcher"
    assert ws.returns.back() is None
    ws.open_settings()
    ws.exit()
    assert ws.wm.top_kind() == "launcher"


# -- scrolling a place's list -------------------------------------------------
#
# The list is a `ui.table` placed in the page's list rect: the engine keeps
# its offset by the table's id, scrolls it by a drag over its rows and keeps
# the current row in view under the keys. What a test can see of the offset
# is which row a tap lands on.

CAPTION, HEAD, ROW = 19, 18, 20     # the handheld's, at fs 1


def _drv(ws):
    return host_app.ConsoleDriver(ws)


def _frames(drv, n=3):
    for _ in range(n):
        drv.ws.mark_dirty()
        drv.frame(1 / 30)


def _key(ws, app, name):
    """One press edge, and the frames that answer it."""
    drv = _drv(ws)
    drv.press(name)
    _frames(drv)


def _page(ws, place=files_app.PROJECTS):
    """The handheld's page of `place`'s files, focus on its rows."""
    app = ws.files_app
    app.open_place(place)
    app.level = "files"
    app._focus = "files"
    _frames(_drv(ws))
    return app


def _view(app):
    """The rows' viewport: the table's box under its caption and header."""
    x, y, w, h = app._ui.box("files")
    return x, y + CAPTION + HEAD, w, h - CAPTION - HEAD


def _drag(ws, x, y0, y1, steps=6):
    """A finger drag, sample by sample -- a scroll is made of samples that are
    not clicks, so it can only be driven this way."""
    drv = _drv(ws)
    drv.touch(x, y0)
    drv.frame(1 / 30)
    for k in range(1, steps + 1):
        drv.touch_drag(x, y0 + (y1 - y0) * k // steps)
        drv.frame(1 / 30)
    drv.touch_up()
    _frames(drv)


def _tap_at(ws, x, y):
    drv = _drv(ws)
    drv.click(x, y)
    _frames(drv)


def test_the_projects_list_scrolls_by_drag_and_clamps(tmp_path):
    ws = host_app.build_workstation(str(tmp_path / "carts"))
    _open_files(ws)
    app = _page(ws)
    x, vy, w, vh = _view(app)
    n = len(app.rows)
    assert n * ROW > vh, "the seeded store overflows one screen"
    cx = x + w // 2

    _drag(ws, cx, vy + vh - 5, vy + vh - 145)   # 140 px: seven rows, from inside the view
    _tap_at(ws, cx, vy + ROW // 2)
    assert app.cur == 7, "the first row in view is the eighth"
    assert ws.wm.top_kind() == "files"

    for _ in range(9):                          # past the end
        _drag(ws, cx, vy + vh - 10, vy + 10)
    _tap_at(ws, cx, vy + vh - ROW // 2)
    assert app.cur == n - 1, "the last row in view is the last"

    for _ in range(9):                          # and back past the start
        _drag(ws, cx, vy + 10, vy + vh - 10)
    _tap_at(ws, cx, vy + ROW // 2)
    assert app.cur == 0


def test_a_drag_never_opens_the_row_it_let_go_of(tmp_path):
    ws = host_app.build_workstation(str(tmp_path / "carts"))
    _open_files(ws)
    app = _page(ws)
    x, vy, w, _vh = _view(app)
    _drag(ws, x + w // 2, vy + ROW // 2, vy + ROW // 2 + 60)   # down from the top
    assert ws.wm.top_kind() == "files"          # scrolled (nowhere), not opened
    assert app.cur == 0
    _tap_at(ws, x + w // 2, vy + ROW + ROW // 2)
    assert app.cur == 1 and ws.wm.top_kind() == "files"   # the first tap picks it
    _tap_at(ws, x + w // 2, vy + ROW + ROW // 2)
    assert ws.wm.top_kind() == "menu"           # a second, clean tap opens it


def test_the_keys_reach_every_row_and_the_view_follows(tmp_path):
    ws = host_app.build_workstation(str(tmp_path / "carts"))
    _open_files(ws)
    app = _page(ws)
    x, vy, w, vh = _view(app)
    vis = vh // ROW
    for _ in range(vis + 2):
        _key(ws, app, "down")
    assert app.cur == vis + 2
    _key(ws, app, "up")
    assert app.cur == vis + 1
    _key(ws, app, "down")
    cart = app.rows[vis + 2][4]
    _tap_at(ws, x + w // 2, vy + vh - ROW // 2)  # the current row, at the view's foot
    assert ws.wm.top_kind() == "menu" and ws.cart is cart, "the view followed it"


def test_the_arrows_reach_every_row(tmp_path):
    ws = host_app.build_workstation(str(tmp_path / "carts"))
    _open_files(ws)
    app = _page(ws)
    for _ in range(len(app.rows) + 2):
        _key(ws, app, "down")
    assert app.cur == len(app.rows) - 1
    assert app._focused.startswith("verb "), "past the last row, down reaches the verbs"


def test_a_shrinking_list_never_strands_the_view(tmp_path):
    ws = host_app.build_workstation(str(tmp_path / "carts"))
    _open_files(ws)
    app = _page(ws)
    x, vy, w, vh = _view(app)
    for _ in range(9):
        _drag(ws, x + w // 2, vy + vh - 10, vy + 10)
    app.rows = app.rows[:3]                    # as if the list had shrunk under it
    app.cur = 2
    _frames(_drv(ws))
    _tap_at(ws, x + w // 2, vy + ROW // 2)
    assert app.cur == 0, "the view came back to the rows there are"


# -- a cart's own image is a PICTURE ------------------------------------------

def _cart_image(cart, name="bg.moyimg", w=4, h=4, value=12):
    import os
    d = os.path.join(cart["path"], "images")
    if not os.path.isdir(d):
        os.mkdir(d)
    with open(os.path.join(d, name), "w") as f:
        f.write(moy_carts.encode_moyimg(w, h, bytes((value,)) * (w * h)))
    return "images/" + name


def test_a_cart_image_opens_in_paint_from_the_projects_root(tmp_path):
    """A cart's own image is a PICTURE, and a picture always has an editor."""
    ws = host_app.build_workstation(str(tmp_path / "carts"))
    app = _open_files(ws)
    cart = _a_project(ws)
    name = _cart_image(cart)

    assert app.door(name, cart=cart) == ("paint", name)
    assert app.route(name, cart=cart) == "paint"

    assert ws.wm.top_kind() == "artwork"
    assert ws.artwork.doc_name() == name
    assert ws.artwork.doc_kind() == moy_carts.project_kind(cart["path"])
    assert ws.artwork.editable()
    assert ws.artwork.load() == (4, 4, bytes((12,)) * 16)


def test_paint_writes_a_cart_image_back_into_the_cart(tmp_path):
    ws = host_app.build_workstation(str(tmp_path / "carts"))
    app = _open_files(ws)
    cart = _a_project(ws)
    name = _cart_image(cart)
    app.route(name, cart=cart)

    assert ws.artwork.save(bytes((3,)) * 16, 4, 4)
    kind = moy_carts.project_kind(cart["path"])
    assert moy_carts.decode_moyimg(
        moy_carts.load_file(kind, name, ws.carts_root)) == (4, 4, bytes((3,)) * 16)
    assert "bg" not in moy_carts.list_files("drawings", ws.carts_root)


def test_a_gallery_drawing_still_opens_on_the_drawings_kind(tmp_path):
    """The cart-image door must not leak: NEW, and the gallery picker, put the
    document back where the auto-naming and the trash can reach it."""
    carts = str(tmp_path / "carts")
    _seed_drawing(carts, "dragon")
    ws = host_app.build_workstation(carts)
    app = _open_files(ws)
    cart = _a_project(ws)
    app.route(_cart_image(cart), cart=cart)
    assert ws.artwork.doc_kind() != "drawings"

    ws.artwork.new_doc(320, 240)
    assert ws.artwork.doc_kind() == "drawings"
    app.open_place(files_app.ART)
    app._act("OPEN", "dragon")
    assert ws.artwork.doc_kind() == "drawings"


def test_a_picture_paint_cannot_read_opens_read_only_from_files(tmp_path):
    ws = host_app.build_workstation(str(tmp_path / "carts"))
    app = _open_files(ws)
    cart = _a_project(ws)
    import os
    os.mkdir(os.path.join(cart["path"], "images"))
    with open(os.path.join(cart["path"], "images", "odd.moyimg"), "w") as f:
        f.write("not a picture")

    assert app.route("images/odd.moyimg", cart=cart) == "paint"
    assert ws.wm.top_kind() == "artwork"        # opened, not refused
    assert not ws.artwork.editable()
    assert ws.artwork.why_read_only()


def test_the_deep_chain_still_finds_its_way_back_to_files(tmp_path):
    """Files -> a project -> that cart's image -> Paint. Every X walks one step
    back out: the Editor's Config tab, then the Files shelf, then the launcher.
    The app return has to survive the two hops that are not apps."""
    ws = host_app.build_workstation(str(tmp_path / "carts"))
    app = _open_files(ws)
    app.open_place(files_app.PROJECTS)
    app._open_row(0)
    cart = ws.project.cart
    assert ws.wm.top_kind() == "menu"

    name = _cart_image(cart)
    ws.carts.reload(cart)
    ws.cards_layer._open_files()
    ws.cards_layer._files_open(name)
    assert ws.wm.top_kind() == "artwork"

    _tap_x(ws)
    assert ws.wm.top_kind() == "menu"          # back into the project
    _tap_x(ws)
    assert ws.wm.top_kind() == "files"         # ...and on to the shelf
    assert app.place == files_app.PROJECTS
    _tap_x(ws)
    assert ws.wm.top_kind() == "launcher"


def test_b_leaves_a_places_list_instead_of_being_swallowed(tmp_path):
    """A list that eats B is a room with no door on a keyboard board: on the
    handheld, B goes back from a place's files to the places."""
    ws = host_app.build_workstation(str(tmp_path / "carts"))
    _open_files(ws)
    app = _page(ws)
    _key(ws, app, "b")
    assert app.level == "places" and app._focused == "Places"
    assert ws.wm.top_kind() == "files"
