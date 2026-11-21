"""The console API level (docs/app_api_v1.md section 2, "The API level").

The guard: tools/api_level.py's fingerprint of the app-facing tables is
pinned beside moy_store_base.API_LEVEL, and a table that changes while the
level stands is red, with the fix in the message. The gate: a cart whose
`"moybyte": {"api": N}` is above this console's level is refused at launch
and in every run path, and one that names none needs the lowest."""

import json
import os
import sys

import pytest

from runtime import app_perms, host_app, moy_store_base, player_fit
from ws_helpers import open_cart

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, os.path.join(ROOT, "tools"))
import api_level  # noqa: E402

NEWER_TITLE = "Needs a newer console."


def _level_text():
    with open(api_level.LEVEL_FILE, encoding="utf-8") as f:
        return f.read()


def _frozen_text():
    return _level_text().replace("API_FROZEN = False\n", "API_FROZEN = True\n")


def _tables_with(name, value):
    return [[n, value if n == name else t] for n, t in api_level.tables()]


# -- the guard ---------------------------------------------------------------------


def test_the_pinned_fingerprint_is_the_tables_at_this_level():
    why = api_level.check(_level_text(), api_level.fingerprint())
    assert why is None, why
    level, pin = api_level.pinned(_level_text())
    assert level == moy_store_base.API_LEVEL and pin[0] == level


def test_a_table_that_changes_without_a_bump_is_red_once_frozen_and_names_the_fix():
    calls = api_level.os_calls_table()
    calls["rows"] = calls["rows"] + [{"name": "moybyte.new_verb", "in": [], "ret": "int"}]
    fp = api_level.fingerprint(_tables_with("os_calls", calls))
    why = api_level.check(_frozen_text(), fp)
    assert why is not None
    assert "bump the API level in runtime/carts/moy_store_base.py" in why
    assert "API_LEVEL = %d" % (moy_store_base.API_LEVEL + 1) in why
    assert "re-pin the fingerprint with tools/api_level.py" in why
    with pytest.raises(api_level.PinError, match="bump the API level"):
        api_level.repin(_frozen_text(), fp)


def test_unfrozen_a_table_change_re_pins_without_a_bump_and_a_stale_pin_fails_the_check():
    text = _level_text()
    assert api_level.frozen(text) is moy_store_base.API_FROZEN is False
    assert api_level.frozen(_frozen_text()) is True
    calls = api_level.os_calls_table()
    calls["rows"] = calls["rows"] + [{"name": "moybyte.new_verb", "in": [], "ret": "int"}]
    fp = api_level.fingerprint(_tables_with("os_calls", calls))
    why = api_level.check(text, fp)
    assert why == "re-pin the fingerprint with tools/api_level.py"
    new = api_level.repin(text, fp)
    assert api_level.check(new, fp) is None
    assert api_level.pinned(new) == (moy_store_base.API_LEVEL, (moy_store_base.API_LEVEL, fp))


def test_a_bump_re_pins_and_the_check_then_holds():
    fp = api_level.fingerprint(_tables_with("manifest", api_level.manifest_table() + ["x"]))
    level = moy_store_base.API_LEVEL
    bumped = _frozen_text().replace("API_LEVEL = %d\n" % level, "API_LEVEL = %d\n" % (level + 1))
    assert "re-pin" in api_level.check(bumped, api_level.fingerprint()), \
        "a bump alone is not pinned yet"
    new = api_level.repin(bumped, fp)
    assert api_level.check(new, fp) is None
    assert api_level.pinned(new) == (level + 1, (level + 1, fp))


def test_every_public_table_moves_the_fingerprint_and_prose_does_not():
    base = api_level.fingerprint()
    calls = api_level.os_calls_table()
    calls["roles"][0] = dict(calls["roles"][0], grant="floor")
    assert api_level.fingerprint(_tables_with("os_calls", calls)) != base
    assert api_level.fingerprint(_tables_with("manifest", ["type"])) != base
    assert api_level.fingerprint(_tables_with("format", 2)) != base
    with open(api_level.OS_CALLS, encoding="utf-8") as f:
        doc = json.load(f)
    doc["about"] = "other words"
    doc["rows"][0]["doc"] = "other words"
    doc["rows"][0]["server"] = "shell"
    same = api_level.os_calls_table(json.dumps(doc))
    assert api_level.fingerprint(_tables_with("os_calls", same)) == base


def test_the_tables_are_the_ones_the_level_names():
    names = [n for n, _ in api_level.TABLES]
    assert names == ["os_calls", "manifest", "format"]
    assert api_level.manifest_table() == sorted(moy_store_base.VENDOR_FIELDS)
    assert api_level.format_table() == player_fit.FORMAT_LEVEL
    assert "api" in moy_store_base.VENDOR_FIELDS


# -- the gate ----------------------------------------------------------------------


def test_a_level_above_this_console_s_is_newer_and_none_is_the_lowest():
    top = moy_store_base.API_LEVEL
    assert moy_store_base.api_above(top + 1) == top + 1
    for v in (None, top, moy_store_base.API_LOWEST, 0, -3, True, "9", 2.5):
        assert moy_store_base.api_above(v) is None, v
    assert moy_store_base.api_of({}) is None
    assert moy_store_base.api_of({"api": top + 1}) is None, "a top-level field reads as absent"
    assert moy_store_base.api_of({"moybyte": {"api": top + 1}}) == top + 1
    assert player_fit._api_newer({"api": top + 1}) == "API level %d" % (top + 1)
    assert player_fit._api_newer({}) is None


def test_every_run_path_refuses_a_newer_level(tmp_path):
    top = moy_store_base.API_LEVEL
    man = {"title": "Future", "moybyte": {"type": "app", "api": top + 1}}
    cart = app_perms.manifest_cart(man, "future.moy")
    assert cart["api"] == top + 1
    granted, _absent, refusal = app_perms.check(cart, None, ())
    assert granted == [] and refusal == app_perms.NEWER_REFUSAL % (top + 1, top)
    now = app_perms.manifest_cart({"title": "Now", "moybyte": {"type": "app"}}, "now.moy")
    assert app_perms.check(now, None, ())[2] is None


def _store(tmp_path, api):
    carts = tmp_path / "carts"
    host_app.moy_carts.ensure_dirs(str(carts))
    d = carts / "future_py.moy"
    d.mkdir()
    man = {"format": "moybyte-cart-v1", "title": "Future Py", "runtime": "python",
           "main": "main.py", "moybyte": {"type": "game"}}
    if api is not None:
        man["moybyte"]["api"] = api
    (d / "manifest.json").write_text(json.dumps(man))
    (d / "main.py").write_text("raise RuntimeError('ran')\n")
    return str(carts), str(d)


def test_the_catalogue_carries_the_level(tmp_path):
    top = moy_store_base.API_LEVEL
    _carts, d = _store(tmp_path, top + 1)
    assert host_app.moy_carts.load(d)["api"] == top + 1
    _carts, d = _store(tmp_path / "none", None)
    assert host_app.moy_carts.load(d)["api"] is None


def test_the_launcher_refuses_a_cart_from_a_newer_console_before_it_runs(tmp_path):
    top = moy_store_base.API_LEVEL
    carts, _d = _store(tmp_path, top + 1)
    ws = host_app.build_workstation(carts)
    open_cart(ws, "Future Py")
    p = ws.run.player
    assert p.notice_title == NEWER_TITLE, p.cart_error
    assert "API level %d" % (top + 1) in p.notice
    assert p.update is None and p.draw is None
    assert "ran" not in (p.cart_error or "")
