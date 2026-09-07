"""The Desk-Lab rename field: `app_shell.ListShellApp` types into it up to
RENAME_MAX, and the app SEEDS it from the current name. The two used to be
hand-synced through a per-module MAX_NAME constant, so a class that set one
without the other seeded a name longer than typing could ever reproduce -- the
tail could be backspaced away but never typed back."""

from runtime import host_app, moy_carts
from runtime.app_shell import ListShellApp
from runtime.files_app import FilesAppLayer


LONG = "a_very_long_user_file_name_indeed"


class _FakeInp:
    def __init__(self, key=0):
        self.last_key = key

    def pressed(self, _name):
        return False


def _open(ws, title):
    for i, cart in enumerate(ws.launcher.items):
        if cart.get("title") == title:
            ws.launcher.sel = i
            break
    ws.open()
    ws.input.begin_frame()
    ws.frame(1 / 30)


def _type_one(app, ch):
    app._typed_rename(_FakeInp(ord(ch)))
    app._typed_rename(_FakeInp(0))


def _files(tmp_path):
    carts = str(tmp_path / "carts")
    moy_carts.save_file("docs", LONG, "x", carts)
    ws = host_app.build_workstation(carts)
    _open(ws, "Files")
    app = ws.files_app
    app._enter_kind("docs")
    app._act("NAME", LONG)
    return app


OPENERS = (("files", _files),)


def test_every_rename_seed_is_typable(tmp_path):
    """The invariant: a seeded field is a string the app's OWN typing could
    have produced. A seed longer than RENAME_MAX is un-retypable."""
    assert len(LONG) > ListShellApp.RENAME_MAX
    for name, open_app in OPENERS:
        app = open_app(tmp_path / name)
        assert app.mode == "rename", name
        assert len(app.rename_text) == app.RENAME_MAX, name
        # Full: one more key is refused. One backspace: the same key is taken.
        _type_one(app, "z")
        assert len(app.rename_text) == app.RENAME_MAX, name
        app._typed_rename(_FakeInp(0x08))
        app._typed_rename(_FakeInp(0))
        _type_one(app, "z")
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
