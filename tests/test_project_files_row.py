"""The Config tab's Files -- a project's own files, as files (step 5 of
docs/text_editing_2026-09.md).

The rule the whole step exists to keep: **the loader stays in the loop.** A
project file is never reached straight from a listing, because JSON mode is
allowed to WRITE a document that does not parse (a hard exit always saves), so
something has to re-read the folder afterwards and say what happened. Going
through the Editor is that something -- opening a project file opens its
project first and returns to its Config tab, and the return re-loads.

Every case drives the real Workstation: what is asserted is where the console
ended up and what is on disk, never which method was called.
"""

import json
from pathlib import Path

from runtime import host_app, moy_carts, text_modes

from ws_helpers import build_ws

ROOT = Path(__file__).resolve().parent.parent


_SRC = "def _init():\n    pass\n\n\ndef _draw():\n    cls(1)\n"

_EDIT = [{"key": "lives", "type": "int", "min": 1, "max": 9, "card": "LIVES {value}"}]


def _project(ws, title="Testbed", edit=None, **kw):
    """A fresh project in `ws`'s store, adopted by the live shelf. Matched back
    by PATH, never by title -- the seeded shelf has titles of its own."""
    made = moy_carts.create(title, ws.carts_root, src=_SRC, type="game",
                            edit=_EDIT if edit is None else edit, **kw)
    ws.carts.rescan()
    return next(c for c in ws.carts.all if c["path"] == made["path"])


def _on_config(ws, cart):
    ws.open_in_editor(cart)
    ws.editor_app.set_tab("cards")
    return ws.cards_layer


def _frame(ws):
    ws.input.begin_frame()
    ws.frame(1 / 30)


def _row_named(cl, ws, name):
    """The Files table's row for file `name`, as its last frame drew it."""
    from runtime import ui_draw
    _frame(ws)
    names = cl.files["names"]
    if name not in names:
        raise AssertionError("%r not listed: %r" % (name, cl.files["rows"]))
    y0, rh, view, _px, _side = ui_draw._C.api_table_rows(cl._files_host.ctx)
    return (view[0], y0 + names.index(name) * rh, view[2], rh)


def _tap(cl, ws, rect):
    """A tap in `rect` of the Config tab, pressed and released over frames."""
    x, y = rect[0] + rect[2] // 2, rect[1] + rect[3] // 2
    ws.pointer.down = True
    cl.handle_pointer(x, y, True)
    _frame(ws)
    ws.pointer.down = False
    cl.handle_pointer(x, y, False)
    _frame(ws)
    _frame(ws)


def _press(ws, cl, name):
    """Button `name` pressed for one frame, through the tab's input."""
    ws.input.begin_frame()
    ws.input.set_held(name, True)
    ws.input.begin_frame()
    cl.handle_input(ws.input)
    ws.input.set_held(name, False)


# -- the door -------------------------------------------------------------


def test_files_is_the_last_door_on_the_page(tmp_path):
    from ws_helpers import settings_box
    ws = build_ws(tmp_path)
    _on_config(ws, _project(ws))
    card = settings_box(ws, "lives")
    files = settings_box(ws, "Files")
    assert card is not None and files is not None
    assert files[1] > card[1]


def test_a_cart_with_no_edit_schema_still_has_files(tmp_path):
    from ws_helpers import settings_box
    ws = build_ws(tmp_path)
    _on_config(ws, _project(ws, "Bare", edit=[]))
    assert settings_box(ws, "Files") is not None


def test_the_row_opens_on_a_tap_and_on_A_and_backs_out(tmp_path):
    """Files, the Settings page's door (look step 30), opens the list on a
    tap and on A, and BACK and B return to the page."""
    from ws_helpers import settings_box
    ws = build_ws(tmp_path)
    cl = _on_config(ws, _project(ws))
    x, y, w, h = settings_box(ws, "Files")
    cl.handle_pointer(x + 4, y + 2, True)
    ws.pointer.down = False
    for _ in range(2):
        cl.handle_pointer(x + 4, y + 2, False)
        _frame(ws)
    assert cl.files is not None and cl.files["rows"]

    # back returns to the cards
    ws.input.begin_frame()
    ws.input.set_held("b", True)
    ws.input.begin_frame()
    cl.handle_input(ws.input)
    ws.input.set_held("b", False)
    assert cl.files is None

    cl.page().host.focus("Files")
    _frame(ws)
    ws.input.begin_frame()
    ws.input.set_held("a", True)
    ws.input.begin_frame()
    cl.handle_input(ws.input)
    _frame(ws)
    assert cl.files is not None
    assert ws.wm.top_kind() == "menu"             # A opened the list, it did not PLAY


def test_the_row_lists_the_project_folder(tmp_path):
    ws = build_ws(tmp_path)
    cart = _project(ws, "Assets")
    path = Path(cart["path"])
    (path / "sprites.moygfx").write_text("0" * 8)
    (path / "sounds.json").write_text("{}")
    (path / "scenes").mkdir()
    (path / "scenes" / "opening.moyscene").write_text("{}")
    (path / "images").mkdir()
    (path / "images" / "sky.moyimg").write_text("x")
    # the atomic-write machinery's leftovers are not files a person edits
    (path / "manifest.json.bak").write_text("{}")

    cl = _on_config(ws, ws.carts.reload(cart))
    cl._open_files()
    rows = list(cl.files["rows"])
    assert rows[:2] == ["manifest.json", "config.json"]   # the documents lead
    assert "main.py" in rows
    for name in ("sprites.moygfx", "sounds.json",
                 "scenes/opening.moyscene", "images/sky.moyimg"):
        assert name in rows, rows
    assert not [r for r in rows if r.endswith(".bak")]
    _frame(ws)                                   # the panel draws


def test_the_list_is_a_table_of_scripts_then_the_rest(tmp_path):
    """One row a file: its icon, its name and the tab it opens in, the
    cart's scripts first."""
    ws = build_ws(tmp_path)
    cart = _project(ws, "Order")
    (Path(cart["path"]) / "sprites.moygfx").write_text("0" * 8)
    cl = _on_config(ws, ws.carts.reload(cart))
    cl._open_files()
    names = cl.files["names"]
    assert names[0] == cart["main"]
    assert sorted(names) == sorted(cl.files["rows"])
    cells = dict(zip(names, cl.files["cells"]))
    assert cells[cart["main"]] == ("code", cart["main"], "Code")
    assert cells["sprites.moygfx"] == ("sprite", "Sprites", "Art")
    assert cells["config.json"] == ("doc", "config.json", "Text")
    _row_named(cl, ws, "config.json")            # the table draws its rows


def test_the_keys_and_the_trackball_step_the_list_and_open_a_row(tmp_path):
    """Down and the trackball move the current row; A, Enter and the
    trackball's click open it; a frame's keys never open a row they only
    moved to."""
    ws = build_ws(tmp_path)
    cart = _project(ws)
    cl = _on_config(ws, cart)
    drv = host_app.ConsoleDriver(ws)
    cl._open_files()
    drv.frame(1 / 30)
    want = cl.files["names"].index("config.json")
    assert want > 1
    drv.press("down")
    drv.frame(1 / 30)
    assert cl.files["cur"] == 1 and cl.files is not None
    drv.pan(0, 1)                                # the trackball, rolled down
    while cl.files["cur"] < want:
        drv.frame(1 / 30)
    drv.pan(0, 0)
    drv.frame(1 / 30)
    assert cl.files is not None and cl.files["cur"] == want
    drv.ball_click()
    drv.frame(1 / 30)
    drv.frame(1 / 30)
    assert ws.wm.top_is_player() and ws.player.ns["ed"].name() == "config.json"

    ws._exit_to_caller()
    cl._open_files()
    drv.frame(1 / 30)
    drv.press("a")                               # A on the first row: the main script
    drv.frame(1 / 30)
    assert cl.files is None and ws.editor_app.tab == "code"


def test_a_tap_on_a_row_opens_it(tmp_path):
    ws = build_ws(tmp_path)
    cart = _project(ws)
    cl = _on_config(ws, cart)
    drv = host_app.ConsoleDriver(ws)
    cl._open_files()
    x, y, w, h = _row_named(cl, ws, cart["main"])
    drv.click(x + w // 2, y + h // 2)
    drv.frame(1 / 30)
    drv.frame(1 / 30)
    assert cl.files is None and ws.editor_app.tab == "code"


def test_a_row_that_does_not_open_says_so_under_the_list(tmp_path):
    ws = build_ws(tmp_path)
    cart = _project(ws, "Odd")
    path = Path(cart["path"])
    (path / "extras").mkdir()
    (path / "extras" / "notes.bin").write_text("x")
    cl = _on_config(ws, ws.carts.reload(cart))
    cl._open_files()
    cl._files_open("extras/notes.bin")           # a name no tab claims
    assert cl.files["msg"] == "Nothing on this console opens that file."
    _row_named(cl, ws, "config.json")            # the list draws over its line
    assert ws.wm.top_kind() == "menu" and ws.cart_error is None


def test_an_unreadable_folder_lists_nothing_and_says_why(tmp_path):
    ws = build_ws(tmp_path)
    cl = _on_config(ws, _project(ws))

    def _boom(*_a):
        raise OSError("gone")
    ws.carts_store = type("S", (), {"project_kind": staticmethod(_boom),
                                    "multi_script": staticmethod(lambda c: False),
                                    "cart_sources": staticmethod(lambda c: ())})()
    cl._open_files()
    assert cl.files["names"] == () and cl.files["msg"]
    _frame(ws)                                   # the empty page draws
    assert ws.cart_error is None


# -- the router -----------------------------------------------------------


def test_the_main_file_routes_to_the_code_tab(tmp_path):
    ws = build_ws(tmp_path)
    cart = _project(ws)
    cl = _on_config(ws, cart)
    cl._open_files()
    cl._files_open(cart["main"])
    assert cl.files is None
    assert ws.editor_app.tab == "code"
    assert ws.wm.top_kind() == "menu"             # never a cart run


def test_an_asset_routes_to_its_own_tab(tmp_path):
    ws = build_ws(tmp_path)
    cart = _project(ws, "Tabs")
    path = Path(cart["path"])
    (path / "sprites.moygfx").write_text("0" * 8)
    (path / "map.moymap").write_text("2 2\n0000\n0000\n")
    (path / "sounds.json").write_text("{}")
    (path / "scenes").mkdir()
    (path / "scenes" / "opening.moyscene").write_text("{}")
    cart = ws.carts.reload(cart)

    for name, tab in (("sprites.moygfx", "paint"), ("map.moymap", "map"),
                      ("sounds.json", "music"),
                      ("scenes/opening.moyscene", "scene")):
        cl = _on_config(ws, cart)
        cl._open_files()
        cl._files_open(name)
        assert ws.editor_app.tab == tab, name
        assert ws.wm.top_kind() == "menu", name

    # A subfolder no tab claims still says so rather than opening a blob as
    # text. An IMAGE is the exception and has its own test below: a picture is
    # never refused for its shape.
    (path / "extras").mkdir()
    (path / "extras" / "notes.bin").write_text("x")
    cl = _on_config(ws, ws.carts.reload(cart))
    cl._open_files()
    cl._files_open("extras/notes.bin")
    assert cl.files["msg"] == "Nothing on this console opens that file."
    assert ws.wm.top_kind() == "menu"


def test_a_cart_image_opens_in_paint_on_the_projects_kind(tmp_path):
    """A cart's own image is a PICTURE, so it takes the picture door -- Paint,
    on this project's kind, writing back into the cart's own folder."""
    ws = build_ws(tmp_path)
    cart = _project(ws, "Cover")
    path = Path(cart["path"])
    (path / "images").mkdir()
    (path / "images" / "bg.moyimg").write_text(
        moy_carts.encode_moyimg(4, 4, bytes((12,)) * 16))
    cart = ws.carts.reload(cart)
    cl = _on_config(ws, cart)
    cl._open_files()
    cl._files_open("images/bg.moyimg")

    assert ws.wm.top_kind() == "artwork"
    assert ws.artwork.doc_name() == "images/bg.moyimg"
    assert ws.artwork.doc_kind() == moy_carts.project_kind(cart["path"])
    assert ws.artwork.editable()

    # ...and Paint writes it back to the CART, not into the gallery.
    ws.artwork.save(bytes((9,)) * 16, 4, 4)
    blob = (path / "images" / "bg.moyimg").read_text()
    assert moy_carts.decode_moyimg(blob) == (4, 4, bytes((9,)) * 16)
    assert not moy_carts.list_files("drawings", ws.carts_root)


def test_a_picture_paint_cannot_read_opens_read_only(tmp_path):
    """The shape rule: a picture Paint has no editor for still OPENS, saying
    why, instead of being refused."""
    ws = build_ws(tmp_path)
    cart = _project(ws, "Odd")
    path = Path(cart["path"])
    (path / "images").mkdir()
    (path / "images" / "sky.moyimg").write_text('{"format":"moyimg-v1","w":8}')
    cart = ws.carts.reload(cart)
    cl = _on_config(ws, cart)
    cl._open_files()
    cl._files_open("images/sky.moyimg")

    assert ws.wm.top_kind() == "artwork"
    assert not ws.artwork.editable()
    assert ws.artwork.why_read_only() == "Can't read this picture"
    assert ws.artwork.save(bytes((9,)) * 16, 4, 4) is False
    assert (path / "images" / "sky.moyimg").read_text() \
        == '{"format":"moyimg-v1","w":8}'


def test_a_text_file_opens_in_the_handle_in_its_mode(tmp_path):
    ws = build_ws(tmp_path)
    cart = _project(ws)
    (Path(cart["path"]) / "notes.md").write_text("# how it works\n")
    cart = ws.carts.reload(cart)
    cl = _on_config(ws, cart)
    cl._open_files()

    rect = _row_named(cl, ws, "config.json")
    cl._files_pointer(rect[0] + 2, rect[1] + 2, True)
    _frame(ws)
    cl._files_pointer(rect[0] + 2, rect[1] + 2, False)
    _frame(ws)
    _frame(ws)
    assert ws.wm.top_is_player() and ws.cart.get("title") == "Notes"
    ed = ws.player.ns["ed"]
    assert (ed.name(), ed.mode()) == ("config.json", text_modes.JSON)
    assert ed.kind == moy_carts.project_kind(cart["path"])
    assert json.loads(ed.text()) == {}          # the file's real bytes

    # and a `.md` beside the main opens in MARKDOWN, not as JSON
    ws._exit_to_caller()
    cl = ws.cards_layer
    cl._open_files()
    cl._files_open("notes.md")
    _frame(ws)
    assert ws.player.ns["ed"].mode() == text_modes.MD


def test_leaving_the_handle_returns_to_the_config_tab(tmp_path):
    ws = build_ws(tmp_path)
    cart = _project(ws)
    cl = _on_config(ws, cart)
    cl._open_files()
    cl._files_open("config.json")
    _frame(ws)
    assert ws.wm.top_is_player()

    ws._exit_to_caller()
    assert ws.wm.top_kind() == "menu"
    assert ws.editor_app.tab == "cards"
    assert ws.project.cart["path"] == cart["path"]   # the PROJECT, not Notes
    assert ws._project_return is None                # the slot is popped once
    _frame(ws)


def test_the_write_goes_through_the_project_and_its_journal(tmp_path):
    ws = build_ws(tmp_path)
    cart = _project(ws)
    cl = _on_config(ws, cart)
    cl._open_files()
    cl._files_open("config.json")
    _frame(ws)
    ed = ws.player.ns["ed"]
    ed.set_text('{"lives": 4}')
    ok, badge = ed.save()
    assert ok and badge == ""

    on_disk = Path(cart["path"]) / "config.json"
    assert json.loads(on_disk.read_text()) == {"lives": 4}
    # written atomically, and recorded in the PROJECT's journal (#111) --
    # a project file has no files/.history sidecar, by design.
    assert not (Path(cart["path"]) / "config.json.tmp").exists()
    log = Path(cart["path"]) / "journal" / "journal.jsonl"
    assert log.is_file()
    files = [json.loads(ln)["file"] for ln in log.read_text().splitlines() if ln]
    assert "config.json" in files


# -- JSON mode's rule, on a project file ----------------------------------


def test_an_invalid_manifest_is_refused_on_the_soft_save_and_written_on_exit(
        tmp_path):
    ws = build_ws(tmp_path)
    cart = _project(ws)
    cl = _on_config(ws, cart)
    cl._open_files()
    cl._files_open("manifest.json")
    _frame(ws)
    ed = ws.player.ns["ed"]
    good = ed.text()
    ed.set_text("{ this is not json")

    ok, badge = ed.save(soft=True)               # the idle debounce REFUSES
    assert ok is False and badge.startswith("INVALID")
    on_disk = Path(cart["path"]) / "manifest.json"
    assert json.loads(on_disk.read_text())["title"] == "Testbed"  # untouched

    ok, badge = ed.save()                        # a HARD exit writes it anyway
    assert ok is True and badge.startswith("INVALID")
    assert on_disk.read_text() == "{ this is not json"

    # and the LOADER re-validates on the very next read -- which is what the
    # return from the handle does.
    ws._exit_to_caller()
    assert ws.project.cart.get("broken", "").startswith("manifest.json: ")
    assert ws.editor_app.tab == "cards"

    # putting it back clears the note on the next open, with no restart
    on_disk.write_text(good)
    ws.carts.reload(ws.project.cart)
    assert "broken" not in ws.project.cart


def _broken(ws):
    """A project whose manifest will not parse, opened from the launcher."""
    cart = _project(ws, "Fixme")
    (Path(cart["path"]) / "manifest.json").write_text("{ oops")
    ws.carts.rescan()
    cart = next(c for c in ws.carts.all if c["path"].endswith("fixme.moy"))
    assert ws.select_cart(cart)
    ws.open()
    return cart


def test_a_broken_manifest_keeps_the_project_openable_with_a_way_back(tmp_path):
    """The failure this step had to remove: `load()` used to return None for an
    unparseable manifest, so the project left the shelf and the file that broke
    it became unreachable."""
    ws = build_ws(tmp_path)
    cart = _broken(ws)
    assert cart["broken"].startswith("manifest.json: ")
    assert cart["title"] == "local.fixme"            # its folder, since nothing else
    assert cart in ws.files_app._nav.projects()      # still an editable project

    # a launcher TAP does not run it (there is no manifest to run it BY) -- it
    # opens where it can be fixed.
    assert ws.wm.top_kind() == "menu"
    assert ws.editor_app.tab == "cards"              # Config, whatever the schema
    cl = ws.cards_layer
    assert cl._broken() == cart["broken"]
    _frame(ws)                                       # the page draws: why, and Files
    assert ws.cart_error is None
    assert cl._why[0] == cart["broken"] and cl._why[2]
    assert cl._fix_host.box("Files") is not None

    # and Files leads back to the file that broke it
    cl._open_files()
    assert "manifest.json" in cl.files["rows"]
    cl._files_open("manifest.json")
    _frame(ws)
    assert ws.player.ns["ed"].mode() == text_modes.JSON


def test_a_broken_manifests_files_opens_on_a_tap_and_on_A_and_B_comes_back(tmp_path):
    ws = build_ws(tmp_path)
    _broken(ws)
    cl = ws.cards_layer
    _frame(ws)
    _tap(cl, ws, cl._fix_host.box("Files"))
    assert cl.files is not None and "manifest.json" in cl.files["names"]

    _press(ws, cl, "b")                              # B: back to the broken page
    assert cl.files is None
    _frame(ws)
    assert cl._fix_host.box("Files") is not None

    _press(ws, cl, "a")                              # A: its one door, never PLAY
    assert cl.files is not None
    assert ws.wm.top_kind() == "menu"
    _press(ws, cl, "b")
    _frame(ws)

    _press(ws, cl, "b")                              # B on the page leaves the Editor
    _frame(ws)
    assert ws.wm.top_kind() != "menu"


def test_a_broken_manifest_draws_on_the_desk(tmp_path):
    from ws_helpers import build_desktop_ws
    ws = build_desktop_ws(tmp_path)
    _broken(ws)
    cl = ws.cards_layer
    for _ in range(3):
        _frame(ws)
    assert ws.cart_error is None
    assert cl._fix_host.box("Files") is not None


# -- the store kind -------------------------------------------------------


def test_the_project_kind_is_root_relative_and_refuses_a_climbing_name(tmp_path):
    ws = build_ws(tmp_path)
    cart = _project(ws)
    kind = moy_carts.project_kind(cart["path"])
    assert kind == "project:local.testbed.moy"
    assert moy_carts.project_folder(kind) == "local.testbed.moy"
    assert moy_carts.project_folder("docs") == ""      # a user-files kind
    for bad in ("../secret", "/etc/passwd", "a/b/c", ""):
        try:
            moy_carts.project_file_path(kind, bad, ws.carts_root)
        except ValueError:
            continue
        raise AssertionError("resolved a bad project file name: " + repr(bad))


def test_a_project_kind_has_no_files_history_sidecar(tmp_path):
    """Its durable undo is the project's own journal; two histories over one
    file would double-count every edit."""
    ws = build_ws(tmp_path)
    kind = moy_carts.project_kind(_project(ws)["path"])
    assert moy_carts.load_history(kind, "config.json", ws.carts_root) == []
    assert moy_carts.history_commit(kind, "config.json", [{"a": 1}],
                                    root=ws.carts_root) is None
    assert not (Path(ws.carts_root).parent / "files" / ".history").exists()


def test_leaving_paint_returns_to_the_config_tab_through_the_loader(tmp_path):
    """The picture door keeps the loader in the loop exactly as the text door
    does: the picture is written, the folder is re-read, and the Editor is
    where the X lands."""
    ws = build_ws(tmp_path)
    cart = _project(ws, "Cover2")
    path = Path(cart["path"])
    (path / "images").mkdir()
    (path / "images" / "bg.moyimg").write_text(
        moy_carts.encode_moyimg(4, 4, bytes((12,)) * 16))
    cart = ws.carts.reload(cart)
    cl = _on_config(ws, cart)
    cl._open_files()
    cl._files_open("images/bg.moyimg")
    assert ws.wm.top_kind() == "artwork"
    assert ws._project_return is cart

    ws.artwork.save(bytes((5,)) * 16, 4, 4)
    ws._exit_to_caller()                       # the context-X out of Paint

    assert ws.wm.top_kind() == "menu" and ws.editor_app.tab == "cards"
    assert ws.project.cart.get("path") == cart["path"]
    blob = (path / "images" / "bg.moyimg").read_text()
    assert moy_carts.decode_moyimg(blob) == (4, 4, bytes((5,)) * 16)


# -- the cart's cover (SPEC.md 3.6) ---------------------------------------------

def _cover_project(ws, name, data):
    cart = _project(ws, name)
    Path(cart["path"], "cover.png").write_bytes(data)
    cart = ws.carts.reload(cart)
    cl = _on_config(ws, cart)
    cl._open_files()
    cl._files_open("cover.png")
    return cart, Path(cart["path"], "cover.png")


def test_a_carts_cover_opens_in_paint_as_a_128_canvas_in_the_palette(tmp_path):
    from ws_helpers import cover_bytes
    ws = build_ws(tmp_path)
    cart, path = _cover_project(ws, "Covered", cover_bytes(12, stripes=4))
    assert ws.wm.top_kind() == "artwork"
    assert ws.artwork.doc_name() == "cover.png"
    assert ws.artwork.doc_kind() == moy_carts.project_kind(cart["path"])
    w, h, pix = ws.artwork.load()
    assert (w, h) == (128, 128)
    assert pix[:128] == bytes((12,)) * 128 and pix[128:256] == bytes((4,)) * 128

    # ...and Paint writes it back as an indexed cover.png, in the profile.
    drawn = bytes((i // 128) % 64 for i in range(128 * 128))
    assert ws.artwork.save(drawn, 128, 128)
    data = path.read_bytes()
    from runtime import cover_png
    assert data[25] == 3                                  # colour type: indexed
    assert cover_png.decode(data, 1, cover_png.INDEX, cover_png.moy64()) == drawn
    assert not moy_carts.list_files("drawings", ws.carts_root)


def test_a_direct_colour_cover_maps_to_the_palette_and_is_written_only_when_drawn(tmp_path):
    """An RGB cover opens with each pixel its nearest palette colour, and
    leaving Paint without drawing writes nothing -- the file the author made
    stays as it is."""
    ws = build_ws(tmp_path)
    teapot = (ROOT / "ports" / "jet" / "teapot.moy" / "cover.png").read_bytes()
    cart, path = _cover_project(ws, "Teapotish", teapot)
    w, h, pix = ws.artwork.load()
    assert (w, h) == (128, 128) and max(pix) < 64
    layer = ws._apps_by_id["artwork"]
    layer.close()
    assert path.read_bytes() == teapot


def test_a_cover_outside_the_profile_opens_blank_and_saving_replaces_it(tmp_path):
    ws = build_ws(tmp_path)
    wrong = (ROOT / "tests" / "cover_vectors" / "size_127x128.png").read_bytes()
    cart, path = _cover_project(ws, "Wrong", wrong)
    w, h, pix = ws.artwork.load()
    assert (w, h) == (128, 128) and len(set(pix)) == 1
    assert ws.artwork.editable()
    assert path.read_bytes() == wrong              # nothing written by opening
    assert ws.artwork.save(bytes((8,)) * (128 * 128), 128, 128)
    from runtime import cover_png
    assert cover_png.decode(path.read_bytes()) is not None
