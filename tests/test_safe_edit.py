"""Issue #14: safe cart editing -- atomic/validated saves, resilient scan, and an
on-canvas error panel so a broken cart fails LOUDLY (visibly) instead of silently
(the device's native run loop starves USB, so a print() never reaches serial).

These exercise the SOFTWARE hardening only; the underlying SD/SPI bus-sharing
hang is a hardware-only concern (see CLAUDE.md "Hard device constraints") and is
out of scope here."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


# -- (a) atomic save_code roundtrip ----------------------------------------

def test_save_code_atomic_roundtrip(tmp_path):
    from runtime import kid_carts
    root = str(tmp_path / "carts")
    kid_carts.ensure_dirs(root)
    c = kid_carts.create("My Cart", root, src="def _draw():\n    cls(1)\n", type="app")

    status, msg = kid_carts.save_code(c, "def _draw():\n    cls(2)\n")
    assert status == kid_carts.SAVE_OK and msg == ""
    # The new source is on disk and the in-RAM cart was updated.
    assert "cls(2)" in kid_carts.load(c["path"])["src"]
    assert "cls(2)" in c["src"]
    # No temp file is left behind; a backup of the previous good version is kept.
    main = Path(c["path"]) / "main.py"
    assert not (Path(c["path"]) / "main.py.tmp").exists()
    assert (Path(c["path"]) / "main.py.bak").read_text().endswith("cls(1)\n")
    assert main.read_text().endswith("cls(2)\n")


# -- (b) invalid Python is refused and leaves the original intact -----------

def test_save_code_rejects_invalid_python_keeps_original(tmp_path):
    from runtime import kid_carts
    root = str(tmp_path / "carts")
    kid_carts.ensure_dirs(root)
    good = "def _draw():\n    cls(3)\n"
    c = kid_carts.create("Keep Me", root, src=good, type="app")

    status, msg = kid_carts.save_code(c, "def _draw(:\n    cls(\n")  # won't parse
    assert status == kid_carts.SAVE_BAD_SYNTAX
    assert msg                                   # a human-readable reason is returned
    # The good file on disk is untouched -- no truncation, no garbage.
    assert kid_carts.load(c["path"])["src"] == good
    assert (Path(c["path"]) / "main.py").read_text() == good
    assert c["src"] == good                      # in-RAM source not clobbered either
    # No half-written temp file survives the refusal.
    assert not (Path(c["path"]) / "main.py.tmp").exists()

    # compile_check itself is the contract the Workstation relies on.
    assert kid_carts.compile_check(good)[0] is True
    assert kid_carts.compile_check("def _draw(:\n")[0] is False


def test_save_sprites_is_atomic(tmp_path):
    from runtime import kid_carts
    root = str(tmp_path / "carts")
    kid_carts.ensure_dirs(root)
    c = kid_carts.create("Spr", root, src="def _draw():\n    cls(1)\n", type="app")
    kid_carts.save_sprites(c, "0011\n2233\n")
    assert kid_carts.load(c["path"])["sprites"].startswith("0011")
    assert not (Path(c["path"]) / "sprites.kgfx.tmp").exists()


# -- (c) scan() skips a deliberately corrupt cart without raising -----------

def test_scan_skips_corrupt_cart(tmp_path):
    from runtime import kid_carts
    root = str(tmp_path / "carts")
    kid_carts.ensure_dirs(root)
    good = kid_carts.create("Good", root, src="def _draw():\n    cls(1)\n", type="app")

    # A folder with garbage manifest JSON.
    bad1 = Path(root) / "broken1.kcart"
    bad1.mkdir()
    (bad1 / "manifest.json").write_text("{ this is not json")
    (bad1 / "main.py").write_text("def _draw():\n    pass\n")

    # A folder with a valid manifest but no main.py.
    bad2 = Path(root) / "broken2.kcart"
    bad2.mkdir()
    (bad2 / "manifest.json").write_text('{"title": "B2", "main": "main.py"}')

    # A manifest that is valid JSON but not an object.
    bad3 = Path(root) / "broken3.kcart"
    bad3.mkdir()
    (bad3 / "manifest.json").write_text("[1, 2, 3]")
    (bad3 / "main.py").write_text("x = 1\n")

    titles = [c["title"] for c in kid_carts.scan(root)]   # must not raise
    assert "Good" in titles
    assert all(t not in ("B2",) for t in titles)
    # Exactly the good cart survived (system seeds aren't added in this bare root).
    assert titles == ["Good"]
    assert kid_carts.load(str(bad1)) is None
    assert kid_carts.load(str(bad2)) is None
    assert kid_carts.load(str(bad3)) is None
    assert good["title"] == "Good"


# -- (d) a cart that raises mid-frame shows an error, no exception escapes ---

def _make_ws_with_cart(tmp_path, src, title="Boom", type="app", edit=None):
    """Build the shared console with a single hand-authored cart, like
    test_v04_userland drives it (host_app + ConsoleDriver), and open it."""
    from runtime import host_app
    carts_dir = str(tmp_path / "carts")
    host_app.kid_carts.ensure_dirs(carts_dir)
    host_app.kid_carts.create(title, carts_dir, src=src, type=type, edit=edit or [])
    ws = host_app.build_workstation(carts_dir)
    for i, c in enumerate(ws.launcher.items):
        if c["title"] == title:
            ws.launcher.sel = i
            break
    ws.open()
    return ws


def test_cart_that_raises_in_draw_shows_error_panel(tmp_path):
    # _draw raises every frame -> the workstation must trap it, keep running, and
    # paint an error panel. The exception must never escape ws.frame().
    ws = _make_ws_with_cart(tmp_path, "def _draw():\n    raise ValueError('kaboom')\n")
    assert ws.screen == "desktop"
    blank = list(ws.canvas.buf)
    for _ in range(5):
        ws.frame(1 / 30)                  # must not raise
    assert ws.cart_error is not None
    assert "kaboom" in ws.cart_error and "ValueError" in ws.cart_error
    assert ws.canvas.buf != blank         # the panel drew something
    # The editor stays reachable so the kid can fix the cart.
    ws._open_menu()
    assert ws.screen == "menu"


def test_cart_that_raises_in_update_shows_error_panel(tmp_path):
    ws = _make_ws_with_cart(
        tmp_path,
        "def _update(dt):\n    raise RuntimeError('bad update')\n"
        "def _draw():\n    cls(1)\n",
    )
    for _ in range(3):
        ws.frame(1 / 30)                  # must not raise
    assert ws.cart_error is not None and "bad update" in ws.cart_error


def test_cart_with_syntax_error_at_start_shows_panel_not_silence(tmp_path):
    # A cart whose source won't even exec (e.g. saved-around our guard, or shipped
    # broken) must land on the desktop with an error panel, not a dead launcher.
    ws = _make_ws_with_cart(tmp_path, "def _draw(:\n    cls(1)\n")
    assert ws.screen == "desktop"          # not stuck silently on the launcher
    assert ws.cart_error is not None
    for _ in range(3):
        ws.frame(1 / 30)                  # must not raise
    assert ws.cart_error is not None


def test_run_code_refuses_invalid_source_and_keeps_editor(tmp_path):
    # Editing a cart into invalid Python and hitting RUN must keep the kid in the
    # editor with the syntax error surfaced -- and must NOT overwrite the good file.
    from runtime import kid_carts
    good = "def _draw():\n    cls(5)\n"
    ws = _make_ws_with_cart(tmp_path, good, title="Editable")
    assert ws.cart_error is None
    path = ws.cart["path"]

    ws.set_menu_view("code")
    ws.screen = "menu"
    ws.editor.set_text("def _draw(:\n    cls(\n")   # break it
    ws.run_code()

    assert ws.screen == "menu" and ws.menu_view == "code"   # stayed in the editor
    assert ws.save_status and "SYNTAX" in ws.save_status
    # The good source on disk is intact (refused write).
    assert kid_carts.load(path)["src"] == good

    # Now fix it and RUN -> it persists and runs.
    ws.editor.set_text("def _draw():\n    cls(6)\n")
    ws.run_code()
    assert ws.screen == "desktop" and ws.cart_error is None
    assert "cls(6)" in kid_carts.load(path)["src"]
