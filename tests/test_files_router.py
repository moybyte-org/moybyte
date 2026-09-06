"""The Files ROUTER (step 2 of docs/text_editing_2026-09.md): an item opens by
WHAT IT IS -- a `.moy` folder is a project, a cart's main file is code, an
image is Paint, anything else textual is a page in its mode -- and the carts
root sits on the Files shelf beside the user-files kinds.

Every case here drives the real Workstation: the assertion is where the
console ended up, not which method was called."""

from runtime import files_app, host_app, moy_carts, text_modes


def _open_files(ws):
    for i, cart in enumerate(ws.launcher.items):
        if cart.get("title") == "Files":
            ws.launcher.sel = i
            break
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
    shown = [kind for kind, _label in app._shown_kinds()]
    assert shown[:3] == [files_app.PROJECTS, "docs", "drawings"]
    labels = dict(files_app.KIND_LABELS)
    assert labels[files_app.PROJECTS] == "PROJECTS"
    assert labels["docs"] == "NOTES"
    # the carts root is NOT a files kind and is never asked of the files store
    assert files_app.PROJECTS not in moy_carts.FILE_KINDS


def test_the_projects_root_lists_one_row_per_moy_folder(tmp_path):
    ws = host_app.build_workstation(str(tmp_path / "carts"))
    app = _open_files(ws)
    app._enter_rows(files_app.PROJECTS)
    assert app._rows, "the seeded store has projects"
    assert len(app._rows) == len(app.project_carts) == app.counts[files_app.PROJECTS]
    for row in app._rows:
        assert row.endswith(".moy")           # a folder, shown as ONE item
        assert "/" not in row                 # never expanded into its files
    # and the roster is the pickers' -- system apps are not projects
    titles = {c.get("title") for c in app.project_carts}
    assert "Files" not in titles and "Paint" not in titles


# -- the rules, in order -----------------------------------------------------

def test_a_moy_folder_opens_the_project_editor_and_never_a_listing(tmp_path):
    ws = host_app.build_workstation(str(tmp_path / "carts"))
    app = _open_files(ws)
    app._enter_rows(files_app.PROJECTS)
    cart = app.project_carts[0]

    assert app.door(app._rows[0], kind=files_app.PROJECTS)[0] == "editor"
    app._tap_row(0)

    assert ws.wm.top_kind() == "menu"          # the Editor, not the Files grid
    assert ws.cart is cart
    assert app.mode == files_app.PROJECTS       # Files did not become a browser


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
    app._enter_kind("drawings")

    assert app.door("dragon", kind="drawings") == ("paint", "dragon")
    app._pick("dragon")

    assert ws.wm.top_kind() == "artwork"
    assert ws.artwork.doc_name() == "dragon"


def test_a_note_opens_the_text_page_in_markdown_mode(tmp_path):
    carts = str(tmp_path / "carts")
    moy_carts.save_file("docs", "story", "once upon a time", carts)
    ws = host_app.build_workstation(carts)
    app = _open_files(ws)
    app._enter_kind("docs")

    assert app.door("story", kind="docs") == (text_modes.MD, "story")
    assert app.route("story", kind="docs") == text_modes.MD

    assert ws.wm.top_kind() == "writer"
    writer = ws.writer_app
    assert writer.doc_name == "story"
    assert writer.doc_mode == text_modes.MD
    assert writer.editor.text() == "once upon a time"


def test_a_json_file_is_routed_to_json_mode(tmp_path):
    """A project's own files are CLASSIFIED here -- json for the manifest and
    the config, by name -- but their door is the Config tab's ADVANCED row
    (step 5), so the router says so rather than opening the wrong page."""
    ws = host_app.build_workstation(str(tmp_path / "carts"))
    app = _open_files(ws)
    cart = _a_project(ws)

    for name in ("config.json", "manifest.json", "scores.json"):
        assert app.door(name, cart=cart) == (text_modes.JSON, name)
        assert app.route(name, cart=cart) is None
        assert app.status == "NOT YET"
    assert ws.wm.top_kind() == "files"          # and nothing opened


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
    ws._apps_by_id.pop("writer")
    assert app.route("story", kind="docs") is None
    assert app.status == "NO TEXT APP"
    ws._apps_by_id.pop("artwork")
    assert app.route("dragon", kind="drawings") is None
    assert app.status == "NO PAINT APP"
    assert app.route("gone.moy", kind=files_app.PROJECTS) is None
    assert app.status == "CAN'T OPEN"
