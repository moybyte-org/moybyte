"""Storybook (#78): decks of pages that compile to real story cartridges."""

import json
from pathlib import Path

from runtime import host_app, moy_carts
from runtime.storybook_app import (StorybookAppLayer, StorybookLayout,
                                   deck_to_code, _fit_art, MAX_PAGES)


ROOT = Path(__file__).resolve().parent.parent


def _open_storybook(ws):
    for i, cart in enumerate(ws.launcher.items):
        if cart.get("title") == "Storybook":
            ws.launcher.sel = i
            break
    ws.open()
    ws.input.begin_frame()
    ws.frame(1 / 30)
    assert ws.wm.top_kind() == "storybook"
    return ws.storybook_app


def _type(app, inp, text):
    for ch in text:
        inp.last_key = 10 if ch == "\n" else ord(ch)
        app._typed_keys(inp)
        inp.last_key = 0
        app._typed_keys(inp)


def test_storybook_cart_is_versioned_system_app():
    folder = ROOT / "system_carts" / "storybook.moy"
    man = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    assert man["version"] >= 1
    assert man["type"] == "app"
    assert man["system"] is True
    assert "storybook" in man["permissions"]
    compile((folder / "main.py").read_text(encoding="utf-8"),
            str(folder / "main.py"), "exec")


def test_storybook_identity_rejects_copies_and_impostors():
    real = {"title": "Storybook", "permissions": ["storybook"],
            "path": "/x/storybook.moy"}
    assert StorybookAppLayer.is_app(real)
    assert not StorybookAppLayer.is_app(dict(real, title="My Storybook"))
    assert not StorybookAppLayer.is_app(dict(real, permissions=[]))
    assert not StorybookAppLayer.is_app(dict(real, path="/x/storybook_2.moy"))


def test_deck_compiles_to_valid_readable_cart_code():
    deck = {"pages": [{"bg": "indigo", "art": "pg1", "text": ["Hello", "World"]},
                      {"bg": "black", "art": None, "text": ["The end."]}]}
    src = deck_to_code(deck, "My Tale")
    compile(src, "<story>", "exec")            # valid python
    assert "My Tale" in src and "PAGES" in src
    assert "'pg1'" in src and "'The end.'" in src
    assert "def _update(dt):" in src           # the Player's tick signature
    # An EMPTY deck still compiles to a playable one-page story.
    empty = deck_to_code({"pages": []})
    compile(empty, "<story>", "exec")
    assert "My story starts here." in empty


def test_new_story_becomes_a_launcher_cart_and_plays(tmp_path):
    ws = host_app.build_workstation(str(tmp_path / "carts"))
    app = _open_storybook(ws)
    app._tap_row(0)                            # + NEW STORY
    assert app.mode == "pages" and not app.read_only
    cart = app.cart
    assert cart["type"] == "story"
    assert (Path(cart["path"]) / "deck.json").exists()
    # The story is a REAL cart on the launcher run grid.
    assert "Story 1" in [c.get("title") for c in ws.launcher.items]
    # Type more words onto page 1, then PLAY it under the real Player.
    app._tap_row(1)
    _type(app, ws.input, " A dragon!")
    app._play_story()
    drv = host_app.ConsoleDriver(ws)
    drv.frame(1 / 30)
    assert ws.wm.top_kind() == "desktop" and ws.cart_error is None
    assert ws.ns["page"] == 0
    # Add a second page so the A button visibly turns the page.
    ws.go_home()
    app2 = _open_storybook(ws)
    stories = app2._stories()
    app2._open_story(stories[0])
    app2._new_page()
    _type(app2, ws.input, "Page two!")
    app2._back_to_pages()
    app2._play_story()
    drv.frame(1 / 30)
    drv.press("a")
    drv.frame(1 / 30)
    assert ws.cart_error is None
    assert ws.ns["page"] == 1                  # A turned the page


def test_hand_edited_code_graduates_the_story_to_read_only(tmp_path):
    ws = host_app.build_workstation(str(tmp_path / "carts"))
    app = _open_storybook(ws)
    app._tap_row(0)
    cart = app.cart
    app._back_to_shelf()
    # The kid levels up: hand-edit the generated code in the Editor.
    hacked = (Path(cart["path"]) / "main.py").read_text() + "\nSPEED = 99\n"
    ws.carts_store.save_code(cart, hacked)
    ws._rehydrate_cart(cart)
    app._open_story(cart)
    assert app.read_only
    assert "LEVELED UP" in app.status
    # #78 full integration: the divergence GRADUATED the real manifest fact,
    # not just a local in-app refusal -- it round-trips through a fresh load.
    assert cart["graduated"] is True
    assert moy_carts.load(cart["path"])["graduated"] is True
    # A commit must NEVER clobber the hand-written code.
    app._deck_dirty = True
    app._commit_deck()
    assert (Path(cart["path"]) / "main.py").read_text() == hacked


def test_paint_art_attaches_to_a_page_and_ships_in_the_code(tmp_path):
    ws = host_app.build_workstation(str(tmp_path / "carts"))
    # Seed the shared Paint drawing (the artwork service, as Paint would).
    ws.artwork.save(bytes([8] * (32 * 24)), 32, 24)
    app = _open_storybook(ws)
    app._tap_row(0)
    app._tap_row(1)                            # open page 1
    app._attach_art()
    assert app._pages()[0]["art"] == "pg1"
    app._back_to_pages()
    src = (Path(app.cart["path"]) / "main.py").read_text()
    assert "'pg1'" in src
    assert (Path(app.cart["path"]) / "images" / "pg1.moyimg").exists()
    # And the story still runs (image() resolves the art).
    app._play_story()
    drv = host_app.ConsoleDriver(ws)
    drv.frame(1 / 30)
    assert ws.cart_error is None


def test_fit_art_shrinks_desktop_paintings_to_the_cart_canvas():
    idx = bytes(range(256)) * (512 * 300 // 256)
    w, h, out = _fit_art(512, 300, idx)
    assert w <= 320 and h <= 240
    assert len(out) == w * h
    # Small art passes through untouched.
    small = bytes([5] * (100 * 80))
    assert _fit_art(100, 80, small) == (100, 80, small)


def test_story_full_and_page_caps(tmp_path):
    ws = host_app.build_workstation(str(tmp_path / "carts"))
    app = _open_storybook(ws)
    app._tap_row(0)
    app.deck["pages"] = [{"bg": "black", "art": None, "text": []}
                         for _ in range(MAX_PAGES)]
    app._new_page()
    assert len(app._pages()) == MAX_PAGES
    assert app.status == "STORY FULL"


def test_bar_x_exit_commits_the_open_story(tmp_path):
    ws = host_app.build_workstation(str(tmp_path / "carts"))
    app = _open_storybook(ws)
    app._tap_row(0)
    app._tap_row(1)
    _type(app, ws.input, "\nSaved by the X.")   # a fresh line (the 34-char line cap)
    xb = ws.layout.context_x_btn
    app.handle_pointer(xb[0] + 2, xb[1] + 2, True)
    assert ws.wm.top_kind() == "launcher"
    assert ws.input.text_mode is False
    deck = json.loads((Path(app.cart["path"]) / "deck.json").read_text())
    assert "Saved by the X." in " ".join(deck["pages"][0]["text"])
    src = (Path(app.cart["path"]) / "main.py").read_text()
    assert "Saved by the X." in src
    # A plain deck-driven save must never graduate the story on its own -- only
    # a hand-edit past the deck's vocabulary does (#78, see the graduation
    # tests below).
    assert not app.cart.get("graduated")
    assert moy_carts.load(app.cart["path"])["graduated"] is False


def test_layout_reflows():
    small = StorybookLayout(320, 240, 1)
    big = StorybookLayout(960, 600, 1)
    assert big.list_rows > small.list_rows
    assert big.preview[3] > small.preview[3]
    assert StorybookLayout(480, 300, 1, windowed=True).bar_h == 0
