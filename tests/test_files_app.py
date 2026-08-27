"""The Files system app (#108): the user-files gallery, its verbs (rename /
copy / trash / restore), the copy-on-use reuse actions, and Paint's shared
OPEN picker + autosave riding the same layer."""

from pathlib import Path

from runtime import moy_carts


ROOT = Path(__file__).resolve().parent.parent


from ws_helpers import build_ws as _ws


def _open_app(ws, title):
    for i, cart in enumerate(ws.launcher.items):
        if cart.get("title") == title:
            ws.launcher.sel = i
            break
    ws.open()
    ws.input.begin_frame()
    ws.frame(1 / 30)


def _seed_drawing(carts, name, w=320, h=240, color=14):
    blob = moy_carts.encode_moyimg(w, h, bytes((color,)) * (w * h))
    return moy_carts.save_file("drawings", name, blob, carts)


class _FakeInp:
    def __init__(self, key=0):
        self.last_key = key

    def pressed(self, _name):
        return False


def test_files_cart_is_well_formed_and_the_app_opens(tmp_path):
    import json
    man = json.loads((ROOT / "system_carts" / "files.moy" / "manifest.json")
                     .read_text(encoding="utf-8"))
    assert man["type"] == "app" and man["version"] >= 1
    assert "files" in man["permissions"]
    compile((ROOT / "system_carts" / "files.moy" / "main.py").read_text(),
            "main.py", "exec")

    ws = _ws(tmp_path)
    _open_app(ws, "Files")
    assert ws.cart_error is None
    assert ws.wm.top_kind() == "files"
    assert ws.files_app.mode == "kinds"


def test_gallery_lists_rename_copy_trash_restore(tmp_path):
    carts = str(tmp_path / "carts")
    ws = _ws(tmp_path)
    _seed_drawing(carts, "dragon")
    app = ws.files_app
    _open_app(ws, "Files")
    app._enter_kind("drawings")
    assert "dragon" in app.grid.names

    # COPY numbers upward
    app.grid.select("dragon")
    app._act("COPY", "dragon")
    assert "dragon_2" in app.grid.names

    # RENAME through the typed-key flow (edge idiom: one insert per press)
    app.grid.select("dragon_2")
    app._act("NAME", "dragon_2")
    assert app.mode == "rename"
    app.rename_text = ""
    for ch in "castle":
        app._typed_rename(_FakeInp(ord(ch)))
        app._typed_rename(_FakeInp(0))
    app._typed_rename(_FakeInp(0x0D))
    assert app.mode == "grid"
    assert "castle" in app.grid.names
    assert "dragon_2" not in app.grid.names

    # DELETE -> restorable trash -> RESTORE
    app.grid.select("castle")
    app._act("DEL", "castle")
    assert "castle" not in app.grid.names
    app._refresh_counts()
    assert ("drawings", "castle") in app.trash
    app.mode = "trash"
    app._restore(app.trash.index(("drawings", "castle")))
    assert moy_carts.load_file("drawings", "castle", carts) is not None
    assert ("drawings", "castle") not in app.trash


def test_reuse_actions_are_copies(tmp_path):
    carts = str(tmp_path / "carts")
    ws = _ws(tmp_path)
    _seed_drawing(carts, "dragon", color=20)
    app = ws.files_app
    _open_app(ws, "Files")
    app._enter_kind("drawings")
    app.grid.select("dragon")

    # WALL: copy-on-set -- the wallpaper copy + My Art bg hold the pixels
    # (plus a #108 phase-2 provenance stamp, so compare decoded pixels).
    app._act("WALL", "dragon")
    assert ws.wallpaper_id == "my_art"
    px = moy_carts.decode_moyimg(moy_carts.load_file("drawings", "dragon", carts))
    assert moy_carts.decode_moyimg(moy_carts.load_artwork(carts)) == px
    wall = next(c for c in moy_carts.scan(carts) if c["title"] == "My Art")
    assert moy_carts.decode_moyimg(wall["images"]["bg"]) == px
    assert moy_carts.read_provenance(moy_carts.load_artwork(carts))[0] == \
        "drawings/dragon"

    # Editing the drawing afterwards changes NOTHING until sent again.
    _seed_drawing(carts, "dragon", color=33)
    assert moy_carts.decode_moyimg(moy_carts.load_artwork(carts)) == px

    # GAME: a 320x240 copy lands in the picked project.
    app.grid.select("dragon")
    app._act("GAME", "dragon")
    assert app.mode == "game"
    target_i = app.project_names.index("Star Catcher")
    app._game_pick(target_i)
    target = next(c for c in moy_carts.scan(carts) if c["title"] == "Star Catcher")
    assert "bg" in target["images"]
    # System-app identities are never offered as projects.
    assert "Files" not in app.project_names
    assert "Paint" not in app.project_names
    assert "Calc" not in app.project_names


def test_delete_of_the_open_drawing_cannot_break_paint(tmp_path):
    carts = str(tmp_path / "carts")
    ws = _ws(tmp_path)
    name = _seed_drawing(carts, "doomed")
    ws.artwork.open_named(name)
    app = ws.files_app
    _open_app(ws, "Files")
    app._enter_kind("drawings")
    app.grid.select(name)
    app._act("DEL", name)
    # Paint now opens a fresh canvas under the stale pointer -- no crash.
    _open_app(ws, "Paint")
    assert ws.cart_error is None
    assert ws.wm.top_kind() == "artwork"


def test_migration_surfaces_legacy_artwork_in_the_gallery(tmp_path):
    carts = str(tmp_path / "carts")
    ws = _ws(tmp_path)
    blob = moy_carts.encode_moyimg(320, 240, bytes((9,)) * (320 * 240))
    moy_carts.ensure_dirs(carts)
    moy_carts.save_artwork(blob, carts)
    app = ws.files_app
    _open_app(ws, "Files")
    app._enter_kind("drawings")
    assert "my_art" in app.grid.names


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
    ws.frame(1 / 30)                     # a draw pass lays the grid out
    gx, gy, _gw, _gh = app.grid._rect
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
    ti = ws.artwork.targets().index("Star Catcher")
    assert ws.artwork.attach(ti, "dragon")
    assert ws.artwork.set_wallpaper("dragon")

    rows = ws.artwork.usage("dragon")
    labels = {r["label"] for r in rows}
    assert "Star Catcher" in labels and "WALLPAPER" in labels
    assert all(not r["stale"] for r in rows)          # fresh copies

    # Edit the source drawing: every copy is now stale (pull-based detection).
    _seed_drawing(carts, "dragon", color=33)
    rows = ws.artwork.usage("dragon")
    assert all(r["stale"] for r in rows)

    # UPDATE (send again) the project copy -> only it clears.
    game_row = next(r for r in rows if r["label"] == "Star Catcher")
    assert ws.artwork.resend(game_row, "dragon")
    rows = {r["label"]: r["stale"] for r in ws.artwork.usage("dragon")}
    assert rows["Star Catcher"] is False
    assert rows["WALLPAPER"] is True                   # not re-sent yet


def test_files_app_use_action_lists_and_resends(tmp_path):
    carts = str(tmp_path / "carts")
    ws = _ws(tmp_path)
    _seed_drawing(carts, "dragon", color=20)
    ti = ws.artwork.targets().index("Star Catcher")
    ws.artwork.attach(ti, "dragon")
    app = ws.files_app
    _open_app(ws, "Files")
    app._enter_kind("drawings")
    app.grid.select("dragon")
    app._act("USE", "dragon")
    assert app.mode == "used"
    assert any(r["label"] == "Star Catcher" for r in app.used_rows)
    # Make it stale, then tap the row -> re-sends (UPDATE).
    _seed_drawing(carts, "dragon", color=33)
    app._act("USE", "dragon")
    idx = next(i for i, r in enumerate(app.used_rows)
               if r["label"] == "Star Catcher")
    assert app.used_rows[idx]["stale"] is True
    app._resend(idx)
    assert app.used_rows[idx]["stale"] is False


def test_docs_and_tables_kinds_get_the_open_action(tmp_path):
    import json
    carts = str(tmp_path / "carts")
    ws = _ws(tmp_path)
    moy_carts.save_file("docs", "note",
                        json.dumps({"format": "moytext-v1", "body": "hi"}), carts)
    app = ws.files_app
    _open_app(ws, "Files")
    app._enter_kind("docs")
    assert "OPEN" in app._action_labels()


def test_send_sprites_to_files_producer(tmp_path):
    carts = str(tmp_path / "carts")
    ws = _ws(tmp_path)
    # Open a project so ws.project.sheet is a real sprite sheet.
    cart = ws.carts_store.create("Doodle", carts, type="game")
    ws.carts.apply(ws.carts_store.scan(carts))
    ws.open_in_editor(next(c for c in ws.carts.all if c.get("title") == "Doodle"))
    ws.project.sheet.pset(0, 0, 9)
    name = ws.send_sprites_to_files()
    assert name in moy_carts.list_files("sprites", carts)
    assert moy_carts.load_file("sprites", name, carts) == ws.project.sheet.to_hex()
