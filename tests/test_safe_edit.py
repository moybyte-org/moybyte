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
    from runtime import moy_carts
    root = str(tmp_path / "carts")
    moy_carts.ensure_dirs(root)
    c = moy_carts.create("My Cart", root, src="def _draw():\n    cls(1)\n", type="app")

    status, msg = moy_carts.save_code(c, "def _draw():\n    cls(2)\n")
    assert status == moy_carts.SAVE_OK and msg == ""
    # The new source is on disk and the in-RAM cart was updated.
    assert "cls(2)" in moy_carts.load(c["path"])["src"]
    assert "cls(2)" in c["src"]
    # No temp file is left behind; a backup of the previous good version is kept.
    main = Path(c["path"]) / "main.py"
    assert not (Path(c["path"]) / "main.py.tmp").exists()
    assert (Path(c["path"]) / "main.py.bak").read_text().endswith("cls(1)\n")
    assert main.read_text().endswith("cls(2)\n")


# -- (b) invalid Python is refused and leaves the original intact -----------

def test_save_code_rejects_invalid_python_keeps_original(tmp_path):
    from runtime import moy_carts
    root = str(tmp_path / "carts")
    moy_carts.ensure_dirs(root)
    good = "def _draw():\n    cls(3)\n"
    c = moy_carts.create("Keep Me", root, src=good, type="app")

    status, msg = moy_carts.save_code(c, "def _draw(:\n    cls(\n")  # won't parse
    assert status == moy_carts.SAVE_BAD_SYNTAX
    assert msg                                   # a human-readable reason is returned
    # The good file on disk is untouched -- no truncation, no garbage.
    assert moy_carts.load(c["path"])["src"] == good
    assert (Path(c["path"]) / "main.py").read_text() == good
    assert c["src"] == good                      # in-RAM source not clobbered either
    # No half-written temp file survives the refusal.
    assert not (Path(c["path"]) / "main.py.tmp").exists()

    # compile_check itself is the contract the Workstation relies on.
    assert moy_carts.compile_check(good)[0] is True
    assert moy_carts.compile_check("def _draw(:\n")[0] is False


def test_save_sprites_is_atomic(tmp_path):
    from runtime import moy_carts
    root = str(tmp_path / "carts")
    moy_carts.ensure_dirs(root)
    c = moy_carts.create("Spr", root, src="def _draw():\n    cls(1)\n", type="app")
    moy_carts.save_sprites(c, "0011\n2233\n")
    assert moy_carts.load(c["path"])["sprites"].startswith("0011")
    assert not (Path(c["path"]) / "sprites.moygfx.tmp").exists()


# -- (c) scan() skips a deliberately corrupt cart without raising -----------

def test_scan_skips_corrupt_cart(tmp_path):
    from runtime import moy_carts
    root = str(tmp_path / "carts")
    moy_carts.ensure_dirs(root)
    good = moy_carts.create("Good", root, src="def _draw():\n    cls(1)\n", type="app")

    # A folder with garbage manifest JSON.
    bad1 = Path(root) / "broken1.moy"
    bad1.mkdir()
    (bad1 / "manifest.json").write_text("{ this is not json")
    (bad1 / "main.py").write_text("def _draw():\n    pass\n")

    # A folder with a valid manifest but no main.py.
    bad2 = Path(root) / "broken2.moy"
    bad2.mkdir()
    (bad2 / "manifest.json").write_text('{"title": "B2", "main": "main.py"}')

    # A manifest that is valid JSON but not an object.
    bad3 = Path(root) / "broken3.moy"
    bad3.mkdir()
    (bad3 / "manifest.json").write_text("[1, 2, 3]")
    (bad3 / "main.py").write_text("x = 1\n")

    titles = [c["title"] for c in moy_carts.scan(root)]   # must not raise
    assert "Good" in titles
    assert all(t not in ("B2",) for t in titles)
    # Exactly the good cart survived (system seeds aren't added in this bare root).
    assert titles == ["Good"]
    assert moy_carts.load(str(bad1)) is None
    assert moy_carts.load(str(bad2)) is None
    assert moy_carts.load(str(bad3)) is None
    assert good["title"] == "Good"


# -- (d) a cart that raises mid-frame shows an error, no exception escapes ---

def _make_ws_with_cart(tmp_path, src, title="Boom", type="app", edit=None):
    """Build the shared console with a single hand-authored cart, like
    test_v04_userland drives it (host_app + ConsoleDriver), and open it."""
    from runtime import host_app
    carts_dir = str(tmp_path / "carts")
    host_app.moy_carts.ensure_dirs(carts_dir)
    host_app.moy_carts.create(title, carts_dir, src=src, type=type, edit=edit or [])
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
    # broken) must never die silently: the crash-to-code throw (owner 2026-07-23)
    # lands the kid in the CODE editor on the offending line with the error popup
    # armed -- no parked OOPS screen, no manual TAP-CODE step.
    ws = _make_ws_with_cart(tmp_path, "def _draw(:\n    cls(1)\n")
    assert ws.screen == "menu" and ws.menu_view == "code"
    assert ws.cart_error is not None
    assert ws.crash_popup is not None          # the dismissible popup is up
    assert ws.code_err_row == 0                # caret marked line 1
    for _ in range(3):
        ws.frame(1 / 30)                  # must not raise
    assert ws.cart_error is not None


def test_run_code_refuses_invalid_source_and_keeps_editor(tmp_path):
    # Editing a cart into invalid Python and hitting RUN must keep the kid in the
    # editor with the syntax error surfaced -- and must NOT overwrite the good file.
    from runtime import moy_carts
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
    assert moy_carts.load(path)["src"] == good

    # Now fix it and RUN -> it persists and runs.
    ws.editor.set_text("def _draw():\n    cls(6)\n")
    ws.run_code()
    assert ws.screen == "desktop" and ws.cart_error is None
    assert "cls(6)" in moy_carts.load(path)["src"]


# -- (e) [BLOCKER] a cart whose exception's __str__ itself raises -----------

# The evil cart: _draw raises an exception whose own __str__ raises. If frame()
# (or _start) ever re-stringifies the RAW exc, that secondary RuntimeError
# escapes the loop -> on device the panel never paints and the board hangs.
_EVIL_SRC = (
    "class Evil(Exception):\n"
    "    def __str__(self):\n"
    "        raise RuntimeError('str blew up')\n"
    "def _draw():\n"
    "    raise Evil()\n"
)

_EVIL_AT_START_SRC = (
    "class Evil(Exception):\n"
    "    def __str__(self):\n"
    "        raise RuntimeError('str blew up')\n"
    "def _init():\n"
    "    raise Evil()\n"
    "def _draw():\n"
    "    cls(1)\n"
)


def test_evil_str_cart_does_not_escape_frame(tmp_path):
    ws = _make_ws_with_cart(tmp_path, _EVIL_SRC)
    assert ws.screen == "desktop"
    for _ in range(5):
        ws.frame(1 / 30)                  # MUST NOT raise (incl. the print path)
    assert ws.cart_error is not None      # panel text was still produced via _err_text
    assert "Evil" in ws.cart_error        # the type name survives even when __str__ dies
    # The broken cart was stopped so it isn't re-run every frame.
    assert ws._update is None and ws._draw is None


def test_evil_str_cart_at_start_does_not_escape(tmp_path):
    # The same hostile exception, but raised in _init during _start(): open() must
    # throw to the code editor with the error captured, never propagate out of
    # _start's print (crash-to-code, owner 2026-07-23).
    ws = _make_ws_with_cart(tmp_path, _EVIL_AT_START_SRC)   # open() runs _start()
    assert ws.screen == "menu" and ws.menu_view == "code"
    assert ws.cart_error is not None and "Evil" in ws.cart_error
    assert ws.crash_popup is not None
    for _ in range(3):
        ws.frame(1 / 30)                  # must not raise


def test_runtime_crash_throws_to_code_line_with_popup(tmp_path):
    """A mid-frame crash = the same throw: straight to the code editor, caret on
    the raising line, popup up; a tap anywhere dismisses it (and is consumed),
    typing a fix dismisses it too (crash-to-code, owner 2026-07-23)."""
    ws = _make_ws_with_cart(tmp_path,
                            "def _draw():\n    raise ValueError('boom')\n")
    assert ws.screen == "desktop"              # started clean
    ws.frame(1 / 30)                           # first frame raises
    assert ws.screen == "menu" and ws.menu_view == "code"
    assert "boom" in (ws.crash_popup or "")
    assert ws.code_err_row == 1                # the raise is on line 2
    assert ws.editor is not None and ws.editor.row == 1
    ws.frame(1 / 30)                           # popup draws without error
    ws.code_layer.handle_pointer(50, 100, True)
    assert ws.crash_popup is None              # tap anywhere closes it
    # A fresh crash re-arms it; the first EDIT dismisses it with the marker.
    ws.cart["src"] = "def _draw():\n    raise ValueError('again')\n"
    ws._start()
    ws.run(ws.project, ws.editor_app)
    assert ws.screen == "desktop"
    ws.frame(1 / 30)
    assert ws.screen == "menu" and "again" in (ws.crash_popup or "")
    # A FIXING edit retires the marker via the re-check (the popup goes with
    # the first edit either way).
    ws.editor.set_text("def _draw():\n    cls(7)\n")
    ws.code_layer._recheck_err()
    assert ws.crash_popup is None and ws.code_err_row is None


def test_marker_follows_until_the_error_is_actually_fixed(tmp_path):
    """The re-check rule (owner 2026-07-23): an edit that does NOT fix the
    error keeps the red underline (it follows the live syntax error, without
    yanking the caret); only code that parses again retires it."""
    ws = _make_ws_with_cart(tmp_path, "def _draw(:\n    cls(1)\n")
    assert ws.screen == "menu" and ws.code_err_row == 0     # crash-to-code
    ed = ws.editor
    # An unrelated edit leaves it broken: the marker STAYS on the bad line
    # and the caret is not yanked back to it.
    ed.goto_row(1, 4)
    ed.key(ord("#"))
    ws.code_layer._recheck_err()
    assert ws.code_err_row == 0 and ws.code_err
    assert ws.editor.row == 1                               # caret left alone
    # The actual fix retires it.
    ed.set_text("def _draw():\n    cls(1)\n")
    ws.code_layer._recheck_err()
    assert ws.code_err_row is None and ws.code_err is None


def test_undo_of_breaking_change_clears_marker_and_keeps_place(tmp_path):
    """Undoing the breaking change re-checks the marker: it retires because
    the restored code PARSES again (a still-broken restore would keep it) --
    and an undo must not throw the caret to the top of the file (owner report
    2026-07-23). Covers BOTH walk tiers: the in-RAM burst undo and the
    journal-commit fallback."""
    ws = _make_ws_with_cart(tmp_path, "def _draw():\n    cls(1)\n",
                            title="Undoable")
    ws.set_menu_view("code")
    ws.screen = "menu"
    ed = ws.editor
    # -- local tier: a typing burst breaks line 2, PLAY crashes, undo fixes.
    ws._code_burst_open()
    ed.set_text("def _draw():\n    boom_undefined()\n")
    ws.cart["src"] = ed.text()
    ws._start()
    ws.run(ws.project, ws.editor_app)
    ws.frame(1 / 30)                            # crash -> thrown back to code
    assert ws.screen == "menu" and ws.code_err_row == 1
    assert ws.crash_popup is not None
    assert ws.undo() is True                    # unwinds the burst
    assert "boom_undefined" not in ws.editor.text()
    assert ws.code_err_row is None and ws.code_err is None
    assert ws.crash_popup is None               # popup retired with the marker
    # -- journal tier: two commits, caret parked low, undo reloads the file.
    ed = ws.editor
    ed.set_text("def _draw():\n    cls(2)\n    cls(3)\n    cls(4)\n")
    assert ws.save_code() is True               # commit 1
    ed.set_text("def _draw():\n    cls(5)\n    cls(6)\n    cls(7)\n")
    assert ws.save_code() is True               # commit 2
    ed.goto_row(2, 4)
    ws.code_err = "stale"                       # a stale marker must not survive
    ws.code_err_row = 1
    assert ws.undo() is True                    # no local ops -> journal walk
    assert "cls(2)" in ws.editor.text()         # commit 2 reverted
    assert ws.code_err is None and ws.code_err_row is None
    assert (ws.editor.row, ws.editor.col) == (2, 4)   # place kept, not (0, 0)


# -- (f) [MAJOR] fixing a crashed cart + SAVE clears the stale panel --------

def test_fix_and_save_clears_stale_crash_panel_and_reruns(tmp_path):
    from runtime import moy_carts
    ws = _make_ws_with_cart(tmp_path, "def _draw():\n    raise ValueError('boom')\n",
                            title="Fixable")
    path = ws.cart["path"]

    ws.frame(1 / 30)                      # crash -> panel set, cart stopped
    assert ws.cart_error is not None and ws._draw is None

    # Open the code editor, fix the source, and hit SAVE (not RUN).
    ws.set_menu_view("code")
    ws.screen = "menu"
    ws.editor.set_text("def _draw():\n    cls(7)\n")
    assert ws.save_code() is True
    assert ws.cart_error is None          # SAVE_OK cleared the stale crash text
    assert ws.save_status == "SAVED"
    assert "cls(7)" in moy_carts.load(path)["src"]

    # Closing the editor (the X button) returns to the desktop and the FIXED cart
    # actually runs again -- _draw is re-bound, no leftover panel.
    ws._leave_menu()
    assert ws.screen == "desktop"
    assert ws.cart_error is None
    assert ws._draw is not None           # re-_start() rebound the (now valid) cart
    for _ in range(3):
        ws.frame(1 / 30)                  # runs clean, no panel
    assert ws.cart_error is None


def test_save_sprites_failure_surfaces_error(tmp_path, monkeypatch):
    # A failed sprite save must set save_status/cart_error (visible on device),
    # mirroring save_code -- not fail silently.
    ws = _make_ws_with_cart(tmp_path, "def _draw():\n    cls(1)\n", title="Sprites")
    assert ws.sheet is not None
    ws.sheet.dirty = True

    def _boom(cart, hexs):
        raise OSError("disk full")
    monkeypatch.setattr(ws.carts_store, "save_sprites", _boom)
    ws.save_sprites()
    assert ws.save_status == "CAN'T SAVE"
    assert ws.cart_error is not None and "sprites" in ws.cart_error.lower()
    assert ws.sheet.dirty is True         # not marked clean on failure


# -- (g) [MAJOR] atomic-write crash window is recoverable via .bak ----------

def test_crash_between_renames_is_recoverable_via_bak(tmp_path, monkeypatch):
    # Simulate a crash AFTER the good file is moved to .bak but BEFORE .tmp is
    # published: there is no main.py on disk, only main.py.bak. load() must heal.
    from runtime import moy_carts
    root = str(tmp_path / "carts")
    moy_carts.ensure_dirs(root)
    good = "def _draw():\n    cls(1)  # GOOD\n"
    c = moy_carts.create("Crashy", root, src=good, type="app")
    path = c["path"]
    main = Path(path) / "main.py"

    # Make the SECOND rename (tmp -> path) "crash" mid-_write_atomic.
    real_rename = moy_carts.os.rename
    calls = {"n": 0}

    def flaky_rename(src, dst):
        calls["n"] += 1
        if calls["n"] == 2:               # path->bak ran; now blow up the tmp->path swap
            raise KeyboardInterrupt("power lost")
        return real_rename(src, dst)

    monkeypatch.setattr(moy_carts.os, "rename", flaky_rename)
    try:
        moy_carts.save_code(c, "def _draw():\n    cls(2)  # NEW\n")
    except KeyboardInterrupt:
        pass
    monkeypatch.setattr(moy_carts.os, "rename", real_rename)

    # The damage we expect: main.py is gone, only main.py.bak (the GOOD copy) remains.
    assert not main.exists()
    assert (Path(path) / "main.py.bak").exists()

    # load() must NOT return None here -- it heals from .bak.
    loaded = moy_carts.load(path)
    assert loaded is not None
    assert "GOOD" in loaded["src"]        # recovered the last-known-good source
    assert main.exists()                  # and republished it on disk
    assert "GOOD" in main.read_text()


# -- (h) [MAJOR] rename-unsupported fallback keeps the data -----------------

def test_rename_unsupported_fallback_keeps_data(tmp_path, monkeypatch):
    # On a FAT VFS where os.rename raises, _write_atomic must fall back to copy and
    # NEVER delete the good file before publishing -> no data loss.
    from runtime import moy_carts
    root = str(tmp_path / "carts")
    moy_carts.ensure_dirs(root)
    good = "def _draw():\n    cls(1)  # OLD\n"
    c = moy_carts.create("NoRename", root, src=good, type="app")
    path = c["path"]
    main = Path(path) / "main.py"

    def no_rename(src, dst):
        raise OSError("rename not supported on FAT")
    monkeypatch.setattr(moy_carts.os, "rename", no_rename)

    status, msg = moy_carts.save_code(c, "def _draw():\n    cls(2)  # NEW\n")
    assert status == moy_carts.SAVE_OK
    # The new bytes published via copy; the real file is present and correct.
    assert main.exists() and "NEW" in main.read_text()
    # No orphan tmp survives the fallback.
    assert not (Path(path) / "main.py.tmp").exists()
    # And load() reads the new content.
    assert "NEW" in moy_carts.load(path)["src"]


def test_rename_unsupported_keeps_path_if_publish_copy_fails(tmp_path, monkeypatch):
    # Even when BOTH os.rename and the publish copy fail, the original good file
    # must remain (the old code did _remove(path) first -> total loss). We never
    # delete path early, so the data is always still recoverable.
    from runtime import moy_carts
    root = str(tmp_path / "carts")
    moy_carts.ensure_dirs(root)
    good = "def _draw():\n    cls(1)  # KEEPME\n"
    c = moy_carts.create("Keep", root, src=good, type="app")
    main_path = c["path"] + "/main.py"
    main = Path(main_path)

    monkeypatch.setattr(moy_carts.os, "rename",
                        lambda s, d: (_ for _ in ()).throw(OSError("no rename")))
    # Make the publish copy (tmp -> the real main.py) fail too -- the worst case.
    # (_write_atomic's internals live in moy_fs since the split -- patch there.)
    from runtime import moy_fs
    real_copy = moy_fs._copy

    def boom_copy(src, dst):
        if dst == main_path:              # only the publish copy of main.py blows up
            raise OSError("write failed")
        return real_copy(src, dst)
    monkeypatch.setattr(moy_fs, "_copy", boom_copy)

    try:
        moy_carts.save_code(c, "def _draw():\n    cls(2)\n")
    except OSError:
        pass
    monkeypatch.setattr(moy_fs, "_copy", real_copy)

    # path was never deleted before publishing -> the original good file is intact.
    assert main.exists() and "KEEPME" in main.read_text()
    # And load() still returns the last-known-good cart (no data loss).
    loaded = moy_carts.load(c["path"])
    assert loaded is not None and "KEEPME" in loaded["src"]


# -- (i) [MINOR] orphan .tmp is cleaned on a partial/failed write -----------

def test_orphan_tmp_cleaned_on_failed_write(tmp_path, monkeypatch):
    from runtime import moy_carts
    root = str(tmp_path / "carts")
    moy_carts.ensure_dirs(root)
    c = moy_carts.create("Tmp", root, src="def _draw():\n    cls(1)\n", type="app")
    path = c["path"]

    # Force the tmp write to fail (e.g. ENOSPC) AFTER it would have created the file.
    # (_write_atomic's internals live in moy_fs since the split -- patch there.)
    from runtime import moy_fs
    real_write = moy_fs._write

    def failing_write(p, data):
        if p.endswith(".tmp"):
            real_write(p, data[: len(data) // 2])   # partial bytes land...
            raise OSError("ENOSPC")                  # ...then the write dies
        return real_write(p, data)
    monkeypatch.setattr(moy_fs, "_write", failing_write)

    try:
        moy_carts.save_code(c, "def _draw():\n    cls(2)\n")
    except OSError:
        pass
    monkeypatch.setattr(moy_fs, "_write", real_write)

    assert not (Path(path) / "main.py.tmp").exists()   # orphan cleaned up
    # The original file is untouched (the failure happened before any swap).
    assert "cls(1)" in (Path(path) / "main.py").read_text()


def test_save_shared_sheet_is_atomic(tmp_path):
    from runtime import moy_carts
    root = str(tmp_path / "carts")
    moy_carts.ensure_dirs(root)
    moy_carts.save_shared_sheet("0011\n2233\n", root)
    assert moy_carts.load_shared_sheet(root).startswith("0011")
    # _write_atomic used -> no orphan tmp left behind beside the sheet.
    sheet = Path(moy_carts.shared_sheet_path(root))
    assert not Path(str(sheet) + ".tmp").exists()
    # A second save keeps a .bak of the previous version (recoverable).
    moy_carts.save_shared_sheet("4455\n", root)
    assert moy_carts.load_shared_sheet(root).startswith("4455")
    assert Path(str(sheet) + ".bak").read_text().startswith("0011")
