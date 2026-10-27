"""My Art (runtime/myart_app.py): the drawings' page, its cards, its menu
and its verbs, on the public UI API (docs/media/look_2026-10/APPS_RESTYLE.md,
My Art)."""

from runtime import artwork, host_app, moy_carts

_DT = 1.0 / 30.0


def _ws(tmp_path, **kw):
    ws = host_app.build_workstation(str(tmp_path / "carts"), **kw)
    ws.pointer.visible = False
    return ws


def _draw(ws, name, w=320, h=240, c=8):
    moy_carts.save_file("drawings", name, moy_carts.encode_moyimg(w, h, bytes([c]) * (w * h)),
                        ws.carts_root)


def _frames(ws, n=3):
    for _ in range(n):
        ws._dirty = True
        ws.frame(_DT)


def _open(ws):
    app = ws._apps_by_id["myart"]
    assert ws.open_app(app)
    _frames(ws)
    assert ws.wm.top_kind() == "myart"
    return app


def _key(ws, k):
    """A typed key, through the host driver the browser and the keyboard use."""
    drv = host_app.ConsoleDriver(ws)
    drv.type_char(k)
    for _ in range(3):
        drv.frame(_DT)


def test_my_art_is_a_registered_app_the_desk_and_home_leave_out(tmp_path):
    ws = _ws(tmp_path)
    assert "myart" in ws._apps_by_id
    assert "myart" in ws.wm.HIDDEN_APPS
    wall = [c for c in ws.carts.all if c.get("type") == "wallpaper"
            and c.get("title") == "Wallpaper"]
    assert len(wall) == 1, "the wallpaper slot is called Wallpaper"
    assert wall[0]["path"].endswith("moybyte.wallpaper.moy")


def test_the_drawings_are_cards_new_drawing_first_and_the_wallpapers_badge(tmp_path):
    ws = _ws(tmp_path)
    _draw(ws, "garden")
    _draw(ws, "rocket", c=1)
    assert ws.artwork.set_wallpaper("garden")
    ws.look.tick(0.0)
    app = _open(ws)
    assert sorted(app.drawings) == ["garden", "rocket"]
    assert app.wallpaper == "garden"
    assert app._ui.box("New drawing") is not None
    for name in ("garden", "rocket"):
        assert app._ui.box("Drawing " + name) is not None
    # the cards' pictures are read at the card's size, never the drawing's
    pic = app.covers.get("garden", 88, 66)
    assert (pic.w, pic.h) == (88, 66) and pic.pix[0] == 8


def test_a_reopen_decodes_only_the_drawings_that_changed(tmp_path, monkeypatch):
    ws = _ws(tmp_path)
    _draw(ws, "garden")
    _draw(ws, "rocket", c=1)
    decoded = []
    real = artwork.read_cover

    def read_cover(blob, pix, w, h):
        decoded.append(blob)
        return real(blob, pix, w, h)
    monkeypatch.setattr(artwork, "read_cover", read_cover)
    app = _open(ws)
    assert len(decoded) == 2
    ws.go_home()
    _frames(ws)
    _open(ws)
    assert len(decoded) == 2, "a drawing no app wrote was decoded again"
    ws.go_home()
    _draw(ws, "rocket", c=2)            # another app writes it meanwhile
    _frames(ws)
    _open(ws)
    assert len(decoded) == 3
    assert app.covers.get("rocket", 88, 66).pix[0] == 2
    assert app.covers.get("garden", 88, 66).pix[0] == 8


def test_an_empty_page_offers_new_drawing(tmp_path):
    ws = _ws(tmp_path)
    app = _open(ws)
    assert app.drawings == []
    assert app._ui.box("New drawing") is not None


def test_del_moves_a_drawing_to_the_trash_without_asking(tmp_path):
    ws = _ws(tmp_path)
    _draw(ws, "rocket")
    app = _open(ws)
    app._focus = app._card_id("rocket")
    _frames(ws)
    assert app.cur == "rocket"
    _key(ws, 0x7F)
    assert app.drawings == []
    assert app.renaming is None and not app.menu, "no question"
    trash, _err = app._files.trash_list()
    assert any("rocket" in str(t) for t in trash)
    assert app.toast is not None and "Trash" in app.toast[0]


def test_ctrl_d_duplicates_and_r_renames_in_a_dialog(tmp_path):
    ws = _ws(tmp_path)
    _draw(ws, "rocket")
    app = _open(ws)
    app._focus = app._card_id("rocket")
    _frames(ws)
    _key(ws, 0x04)
    assert len(app.drawings) == 2
    app._focus = app._card_id("rocket")
    _frames(ws)
    _key(ws, ord("r"))
    assert app.renaming is not None
    app._rename("rocket", "Space ship")
    assert "space_ship" in app.drawings and "rocket" not in app.drawings


def test_the_card_menu_opens_on_m_and_washes(tmp_path):
    ws = _ws(tmp_path)
    _draw(ws, "rocket")
    app = _open(ws)
    app._focus = app._card_id("rocket")
    _frames(ws)
    _key(ws, ord("m"))
    assert app.menu and app.cur == "rocket"
    rows = [r[0] for r in app._menu_rows(True)]
    assert rows == ["open", "wallpaper", "game", "rename", "dup"]
    assert app.back() and not app.menu


def test_put_in_a_game_is_for_a_game_size_drawing_only(tmp_path):
    ws = _ws(tmp_path)
    _draw(ws, "rocket")
    _draw(ws, "wide", 1024, 600)
    app = _open(ws)
    app.cur = "wide"
    assert "game" not in [r[0] for r in app._menu_rows(True)]
    app._verb("game", "wide")
    assert app.game is None
    app._verb("game", "rocket")
    assert app.game is not None and app.game.carts


def test_open_takes_the_drawing_to_paint(tmp_path):
    ws = _ws(tmp_path)
    _draw(ws, "rocket", c=3)
    app = _open(ws)
    app._verb("open", "rocket")
    _frames(ws)
    assert ws.wm.top_kind() == "artwork"
    assert ws.artwork.doc_name() == "rocket"
    assert ws.artwork_app.doc.pix[0] == 3


def test_new_drawing_opens_paint_on_a_fresh_drawing(tmp_path):
    ws = _ws(tmp_path)
    _draw(ws, "rocket")
    app = _open(ws)
    app._verb("new")
    _frames(ws)
    assert ws.wm.top_kind() == "artwork"
    assert ws.artwork.doc_name() not in (None, "rocket")
    assert (ws.artwork_app.doc.W, ws.artwork_app.doc.H) == (320, 240)


def test_the_desk_window_opens_at_its_designed_size(tmp_path):
    from runtime import wm_windowed
    assert wm_windowed.DESK_SIZES["myart"] == (900, 536)
    assert wm_windowed.DESK_SIZES["artwork"] == (900, 536)


def test_a_settled_page_paints_nothing(tmp_path):
    from ws_helpers import quiesce
    ws = _ws(tmp_path)
    _draw(ws, "rocket")
    _open(ws)
    drv = host_app.ConsoleDriver(ws)
    quiesce(ws)
    for _ in range(4):
        drv.frame(_DT)
    quiesce(ws)
    ws._dirty = True
    for _ in range(4):
        drv.frame(_DT)
    drawn = ws._frames_drawn
    for _ in range(10):
        drv.frame(_DT)
    assert ws._frames_drawn == drawn, "an idle page painted"
