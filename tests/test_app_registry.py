"""The system-app registry is DATA -- the ratchet for
docs/history/ui_refactor_2026-08.md §3 Phase 5.

Adding a system app used to cost EIGHT files, five of which held the app's name
in a hand-maintained list:

  * `runtime/console.py`     -- import ladder + construction + register_app(...)
  * `tools/gen_device_carts.py` -- CART_ORDER, the device launcher order
  * `tests/test_device_seed_parity.py` -- TITLE_TO_FOLDER
  * `firmware/web_runner/build.sh`  -- the browser bundle's roster
  * (`runtime/host_app.py` -- the bare-name alias, which only Calc ever had)

FOUR of those five failed SILENTLY, and on device only. Forget CART_ORDER and
the identity cart never seeds, so `is_app` never claims it, so the app is simply
unreachable on hardware -- while working perfectly on the host, which is where
anyone would test it.

They are all derived now, from an `"app"` block in the identity cart's own
manifest -- the same move #161 made for board staging when `board.toml` replaced
hand-edited module lists in a build script.

These tests exist so the hand-written lists cannot grow back. They are
deliberately a MIX of behavioural and structural assertions: a structural one
alone would pass against a file that still constructs apps by hand somewhere
else, and a behavioural one alone would pass against a hand-written list that
happens to agree with the manifests today.
"""

import ast
import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))

import gen_device_carts as gdc  # noqa: E402

from runtime import app_decls  # noqa: E402
from ws_helpers import build_ws  # noqa: E402


def _read(rel):
    with open(os.path.join(ROOT, rel), "r", encoding="utf-8") as f:
        return f.read()


# -- the declaration is the source of truth ---------------------------------

def test_app_decls_matches_the_manifests():
    """`runtime/app_decls.py` is generated. If a manifest's `app` block changes
    and nobody regenerates, the frozen tiers ship a stale registry -- so the
    generated text must equal the committed file, byte for byte."""
    fresh = gdc.render_app_decls(gdc.app_decls())
    on_disk = _read("runtime/app_decls.py")
    assert fresh == on_disk, (
        "runtime/app_decls.py is STALE -- regenerate it:\n"
        "    python tools/gen_device_carts.py --app-decls")


def test_every_declaration_names_a_real_identity_cart():
    for d in app_decls.APPS:
        man = os.path.join(ROOT, "system_carts", d["folder"] + ".moy", "manifest.json")
        assert os.path.exists(man), "%s declares folder %r, which does not exist" % (
            d["id"], d["folder"])
        with open(man, "r", encoding="utf-8") as f:
            assert json.load(f)["title"] == d["title"]


def test_declaration_ids_and_order_are_unique():
    ids = [d["id"] for d in app_decls.APPS]
    orders = [d["order"] for d in app_decls.APPS]
    assert len(set(ids)) == len(ids), "duplicate app id in APPS"
    assert len(set(orders)) == len(orders), "duplicate app order in APPS"
    assert orders == sorted(orders), "APPS must be emitted in registration order"


# -- behavioural: the shell registers exactly what is declared ---------------

def test_the_shell_registers_exactly_the_declared_apps(tmp_path):
    """The load-bearing one. A structural check cannot see a shell that builds
    an app somewhere else; this can."""
    ws = build_ws(tmp_path)
    registered = [a.id for a, _text_mode in ws._apps]
    assert registered == [d["id"] for d in app_decls.APPS]
    text = {a.id: tm for a, tm in ws._apps}
    for d in app_decls.APPS:
        assert text[d["id"]] is bool(d.get("text_mode")), (
            "%s registered with the wrong text_mode" % d["id"])
        # the `<id>_app` attribute the shell and the apps address each other by
        assert getattr(ws, d["id"] + "_app", None) is ws._apps_by_id[d["id"]]


# -- structural: the hand-written lists cannot grow back --------------------

def test_console_has_no_per_app_registration_line():
    """`register_app` may appear exactly twice in console.py: its own definition
    and the ONE call inside the declaration loop."""
    src = _read("runtime/console.py")
    tree = ast.parse(src)
    calls = [n for n in ast.walk(tree)
             if isinstance(n, ast.Call)
             and isinstance(n.func, ast.Attribute)
             and n.func.attr == "register_app"]
    assert len(calls) == 1, (
        "console.py makes %d register_app calls; the declaration loop should "
        "make exactly one. Apps are declared in their manifest, not here." % len(calls))
    defs = [n for n in ast.walk(tree)
            if isinstance(n, ast.FunctionDef) and n.name == "register_app"]
    assert len(defs) == 1


@pytest.mark.parametrize("app_id", [d["id"] for d in app_decls.APPS])
def test_no_app_is_constructed_by_name_in_console(app_id):
    """The seven `self.<id>_app = SomeAppLayer(...)` lines are gone. The class
    IMPORTS stay -- console.py's barrel is deliberately frozen, not migrated
    (docs/history/shell_decoupling_2026-08.md row 3) -- but nothing may assign one."""
    tree = ast.parse(_read("runtime/console.py"))
    attr = app_id + "_app"
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        for tgt in node.targets:
            if (isinstance(tgt, ast.Attribute) and tgt.attr == attr
                    and isinstance(node.value, ast.Call)):
                raise AssertionError(
                    "console.py constructs self.%s by hand at line %d -- it "
                    "should come from the declaration loop" % (attr, node.lineno))


def test_gen_device_carts_holds_no_hand_written_app_list():
    """CART_ORDER is gone. Its name survives only in prose explaining why."""
    tree = ast.parse(_read("tools/gen_device_carts.py"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for tgt in node.targets:
                if isinstance(tgt, ast.Name) and tgt.id == "CART_ORDER":
                    raise AssertionError(
                        "CART_ORDER is back at line %d -- the seed order is "
                        "derived from the manifests' `order`" % node.lineno)


def test_the_web_roster_is_derived_not_pasted():
    """The browser bundle's cart list comes from --roster, so a new cart marked
    for the web reaches it without anyone editing a shell script."""
    sh = _read("firmware/web_runner/build.sh")
    assert "--roster web" in sh, "the web build no longer derives its roster"
    line = [ln for ln in sh.splitlines() if ln.startswith("ROSTER=")]
    assert len(line) == 1
    assert ".moy " not in line[0], (
        "the web roster has been pasted back into build.sh: %s" % line[0])


def test_the_device_and_web_orders_agree():
    """One `order` field drives both, so the browser shelf and the device shelf
    cannot silently disagree the way the two hand-written lists did."""
    web = gdc.roster("web")
    dev = gdc.roster("device")
    assert [c for c in dev if c in set(web)] == web
