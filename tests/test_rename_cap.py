"""The rename field: Files' `ui.field` takes at most RENAME_MAX bytes (the
cap `app_shell.ListShellApp` declares), and the app SEEDS it from the
current name. The two used to be
hand-synced through a per-module MAX_NAME constant, so a class that set one
without the other seeded a name longer than typing could ever reproduce -- the
tail could be backspaced away but never typed back."""

from runtime import host_app, moy_carts
from runtime.app_shell import ListShellApp
from runtime.files_app import FilesAppLayer
from ws_helpers import select_on_home  # noqa: E402


LONG = "a_very_long_user_file_name_indeed"


def _open(ws, title):
    select_on_home(ws, title)
    ws.open()
    ws.input.begin_frame()
    ws.frame(1 / 30)


def _frames(drv, n=2):
    for _ in range(n):
        drv.ws.mark_dirty()
        drv.frame(1 / 30)


def _type_one(drv, ch):
    """One typed key, then a frame with none: each press its own edge."""
    drv.type_char(ord(ch))
    _frames(drv)


def _files(tmp_path):
    carts = str(tmp_path / "carts")
    moy_carts.save_file("docs", LONG, "x", carts)
    ws = host_app.build_workstation(carts)
    _open(ws, "Files")
    app = ws.files_app
    app.open_place("docs")
    app._act("NAME", LONG)
    drv = host_app.ConsoleDriver(ws)
    _frames(drv, 3)
    return app, drv


OPENERS = (("files", _files),)


def test_every_rename_seed_is_typable(tmp_path):
    """The invariant: a seeded field is a string the app's OWN typing could
    have produced. A seed longer than RENAME_MAX is un-retypable."""
    assert len(LONG) > ListShellApp.RENAME_MAX
    for name, open_app in OPENERS:
        app, drv = open_app(tmp_path / name)
        assert app.dialog == "rename", name
        assert len(app.rename_text) == app.RENAME_MAX, name
        # Full: one more key is refused. One backspace: the same key is taken.
        _type_one(drv, "z")
        assert len(app.rename_text) == app.RENAME_MAX, name
        _type_one(drv, "\b")
        _type_one(drv, "z")
        assert app.rename_text == LONG[:app.RENAME_MAX - 1] + "z", name


def test_rename_cap_lives_only_on_the_class(tmp_path):
    """No second copy of the cap: each app declares RENAME_MAX (or inherits the
    base's) and the seed slices THAT, so the two cannot drift apart."""
    assert isinstance(FilesAppLayer.RENAME_MAX, int)
    assert FilesAppLayer.RENAME_MAX > 0
    # Files inherits the base's cap. It carried its own 20 until 2026-08-22
    # and renamed the same docs the notebook app did, so it silently
    # truncated names that one accepted.
    assert FilesAppLayer.RENAME_MAX == ListShellApp.RENAME_MAX
    import runtime.files_app
    assert not hasattr(runtime.files_app, "MAX_NAME")
