"""A console's first-run theme variant (`wire_workstation_core(first_variant=)`).

The browser passes its colour scheme (dark -> Moy Night, light -> Moy Day) as the
variant a console with NO saved `theme_variant` boots in; the boards pass
nothing and boot Day as always. A saved choice wins on every later boot. This is
the shared path (`load_system`'s absent-row default), so the host pins it; the
browser end to end is tests/test_web_first_run_e2e.py.
"""

from runtime import host_app


def _ws(tmp_path, **kw):
    return host_app.build_workstation(str(tmp_path / "carts"), **kw)


def test_a_console_with_no_hint_boots_day(tmp_path):
    assert _ws(tmp_path).look.theme_variant == "light"


def test_a_first_run_takes_the_hinted_variant(tmp_path):
    assert _ws(tmp_path, first_variant="dark").look.theme_variant == "dark"
    assert _ws(tmp_path, first_variant="light").look.theme_variant == "light"


def test_a_hint_that_is_no_variant_is_ignored(tmp_path):
    assert _ws(tmp_path, first_variant="sepia").look.theme_variant == "light"


def test_a_saved_choice_wins_over_the_hint(tmp_path):
    ws = _ws(tmp_path, first_variant="dark")
    assert ws.look.theme_variant == "dark"
    ws.look.set_theme_variant("light")          # the Settings pick, persisted
    again = _ws(tmp_path, first_variant="dark")
    assert again.look.theme_variant == "light"


def test_an_unpicked_variant_is_not_saved_by_booting(tmp_path):
    """Following the page's scheme must not write it down as the visitor's
    choice: the next visit follows the scheme again."""
    ws = _ws(tmp_path, first_variant="dark")
    assert _ws(tmp_path, first_variant="light").look.theme_variant == "light"
