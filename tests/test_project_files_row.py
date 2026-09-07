"""The Config tab's ADVANCED row -- a project's own files, as files (step 5 of
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


def _row_named(cl, name):
    for rect, got, k in cl._files_rects():
        if got == name:
            return rect, k
    raise AssertionError("%r not listed: %r" % (name, cl.files["rows"]))


# -- the row --------------------------------------------------------------


def test_the_advanced_row_is_the_last_row_of_the_card_column(tmp_path):
    ws = build_ws(tmp_path)
    cl = _on_config(ws, _project(ws))
    rows = cl._card_layout()
    assert [r["advanced"] for r in rows] == [False, True]
    assert cl.card_text(rows[-1]["i"]) == "ADVANCED: FILES IN THIS PROJECT"
    # it is a door, not a stepper: nothing about the config moves when it is
    # selected and the -/+ arrives.
    cl.msel = rows[-1]["i"]
    before = dict(ws.project.config)
    ws.adjust(1)
    ws.adjust(-1)
    assert dict(ws.project.config) == before


def test_a_cart_with_no_edit_schema_still_has_the_row(tmp_path):
    """The card column used to be empty for such a cart, so the ADVANCED row
    would have had nowhere to live."""
    ws = build_ws(tmp_path)
    cl = _on_config(ws, _project(ws, "Bare", edit=[]))
    rows = cl._card_layout()
    assert len(rows) == 1 and rows[0]["advanced"]
    _frame(ws)                                   # and it draws


def test_the_row_opens_on_a_tap_and_on_A_and_backs_out(tmp_path):
    ws = build_ws(tmp_path)
    cl = _on_config(ws, _project(ws))
    row = cl._card_layout()[-1]
    cl.handle_pointer(row["x"] + 4, row["y"] + 2, True)
    assert cl.files is not None and cl.files["rows"]

    # BACK returns to the cards, and B does the same from the keyboard
    cl._files_pointer(cl.layout.info_btn[0] + 2, cl.layout.info_btn[1] + 2, True)
    assert cl.files is None

    cl.msel = row["i"]
    ws.input.begin_frame()
    ws.input.set_held("a", True)
    ws.input.begin_frame()
    cl.handle_input(ws.input)
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

    # An image asset has no tab that edits it in place, and the row SAYS so
    # rather than opening a `.moyimg` blob as text.
    (path / "images").mkdir()
    (path / "images" / "sky.moyimg").write_text("x")
    cl = _on_config(ws, ws.carts.reload(cart))
    cl._open_files()
    cl._files_open("images/sky.moyimg")
    assert cl.files["msg"] == "NO EDITOR FOR THIS"
    assert ws.wm.top_kind() == "menu"


def test_a_text_file_opens_in_the_handle_in_its_mode(tmp_path):
    ws = build_ws(tmp_path)
    cart = _project(ws)
    (Path(cart["path"]) / "notes.md").write_text("# how it works\n")
    cart = ws.carts.reload(cart)
    cl = _on_config(ws, cart)
    cl._open_files()

    rect, _k = _row_named(cl, "config.json")
    cl._files_pointer(rect[0] + 2, rect[1] + 2, True)
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


def test_a_broken_manifest_keeps_the_project_openable_with_a_way_back(tmp_path):
    """The failure this step had to remove: `load()` used to return None for an
    unparseable manifest, so the project left the shelf and the file that broke
    it became unreachable."""
    ws = build_ws(tmp_path)
    cart = _project(ws, "Fixme")
    (Path(cart["path"]) / "manifest.json").write_text("{ oops")
    ws.carts.rescan()

    cart = next(c for c in ws.carts.all if c["path"].endswith("fixme.moy"))
    assert cart["broken"].startswith("manifest.json: ")
    assert cart["title"] == "fixme"                  # its folder, since nothing else
    assert cart in ws.files_app._nav.projects()      # still an editable project

    # a launcher TAP does not run it (there is no manifest to run it BY) -- it
    # opens where it can be fixed.
    ws.launcher.sel = ws.launcher.items.index(cart)
    ws.open()
    assert ws.wm.top_kind() == "menu"
    assert ws.editor_app.tab == "cards"              # Config, whatever the schema
    cl = ws.cards_layer
    assert cl._broken() == cart["broken"]
    _frame(ws)                                       # the banner draws

    # and the ADVANCED row leads back to the file that broke it
    cl._open_files()
    assert "manifest.json" in cl.files["rows"]
    cl._files_open("manifest.json")
    _frame(ws)
    assert ws.player.ns["ed"].mode() == text_modes.JSON


# -- the store kind -------------------------------------------------------


def test_the_project_kind_is_root_relative_and_refuses_a_climbing_name(tmp_path):
    ws = build_ws(tmp_path)
    cart = _project(ws)
    kind = moy_carts.project_kind(cart["path"])
    assert kind == "project:testbed.moy"
    assert moy_carts.project_folder(kind) == "testbed.moy"
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
