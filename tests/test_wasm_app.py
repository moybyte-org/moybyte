"""A compiled app reaching its roles through imports (docs/kernel_appabi_2026-10.md
section 4): the app ABI's import adapter (native/moy_app/moy_app_wasm.h) on
the host's WAMR, the load's grant check and the VM-free verdict's clauses.

  * tests/fixtures/wasm/app_roles.moy imports prefs and theme rows from module
    "moybyte.app" (moy-spec SPEC.md 16.2), keeps a run count in prefs and paints
    the theme's first token, with no upcall of any class;
  * the same module one permission short is refused at load, the import named;
  * tests/fixtures/wasm/app_shell.moy imports a shell-served row, and the
    verdict keeps the VM for it with the import as its `why` (moy_play's
    census, over native/moy_app's table as on a board).
"""

import json
import os

import pytest

from runtime import host_app, moy_loop
from tools import wasm_cart
from ws_helpers import open_cart

import moy_app

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIXTURES = os.path.join(ROOT, "tests", "fixtures", "wasm")
ROLES = os.path.join(FIXTURES, "app_roles.moy")
SHELL = os.path.join(FIXTURES, "app_shell.moy")
_DT = 1.0 / 30.0


def _binding_or_skip():
    from runtime import wasm_binding, wasm_host
    if wasm_host.available():
        return
    why = wasm_binding.why_unavailable() or "unknown"
    if os.environ.get("CI") or os.environ.get("MOYBYTE_REQUIRE_HOST_WASM"):
        pytest.fail("the host wasm binding did not build: %s" % why)
    pytest.skip("no host wasm binding: %s" % why)


def _store(tmp_path, perms=None):
    root = str(tmp_path / "carts")
    host_app.moy_carts.ensure_dirs(root)
    dst = os.path.join(root, "app_roles.moy")
    wasm_cart.build(ROLES, dst)
    if perms is not None:
        p = os.path.join(dst, "manifest.json")
        with open(p) as f:
            man = json.load(f)
        man["moybyte"]["permissions"] = perms
        with open(p, "w") as f:
            json.dump(man, f)
    return root


def _frames(ws, n):
    for _ in range(n):
        ws._dirty = True
        ws.frame(_DT)


def _runs(ws):
    """The count the app kept, read back through a grant of its own id."""
    app = ws.app_abi
    g = app.grant(moy_app.id_for("app_roles", "App Roles Wasm"), ("prefs",), run=True)
    try:
        return moy_app.Prefs(app, g).get("runs", None)
    finally:
        app.end(g)


def test_a_compiled_app_reaches_prefs_and_theme_with_no_upcall(tmp_path):
    _binding_or_skip()
    ws = host_app.build_workstation(_store(tmp_path))
    t0 = moy_loop.upcalls()[1]
    open_cart(ws, "App Roles Wasm")
    assert ws.player.cart_error is None, ws.player.cart_error
    _frames(ws, 10)
    assert ws.player.cart_error is None, ws.player.cart_error
    from runtime import moy_play
    books = moy_play.info()[5]
    assert tuple(books) == (0, 0, 0, 0, 0, 0), "the run crossed into Python: %r" % (books,)
    assert moy_loop.upcalls()[1][5] == t0[5], "a C row went through the ROLE door"
    # The band the cart paints is the live theme's first token.
    g = ws.app_abi.grant("probe.theme", ("theme",), run=True)
    token = moy_app.Theme(ws.app_abi, g).token(0)
    ws.app_abi.end(g)
    cv = ws.sys_canvas
    i = 2 * (20 * cv.w + 160)
    assert cv._buf[i] | cv._buf[i + 1] << 8 == cv._wire[token & 63]
    ws.exit()
    assert _runs(ws) == 1
    open_cart(ws, "App Roles Wasm")
    _frames(ws, 2)
    ws.exit()
    assert _runs(ws) == 2, "the count kept is the app's, run to run"


def test_the_same_module_one_permission_short_is_refused_naming_the_import(tmp_path):
    _binding_or_skip()
    ws = host_app.build_workstation(_store(tmp_path, perms=[]))
    open_cart(ws, "App Roles Wasm")
    err = ws.player.cart_error or ""
    assert "moybyte.app.prefs_get" in err, err
    assert "prefs" in err.split("moybyte.app.prefs_get", 1)[1], err


def test_a_module_that_declares_no_extension_reaches_no_role(tmp_path):
    _binding_or_skip()
    root = _store(tmp_path)
    p = os.path.join(root, "app_roles.moy", "manifest.json")
    with open(p) as f:
        man = json.load(f)
    del man["extensions"]
    with open(p, "w") as f:
        json.dump(man, f)
    ws = host_app.build_workstation(root)
    open_cart(ws, "App Roles Wasm")
    err = ws.player.cart_error or ""
    assert "moybyte.app.prefs_get" in err and "extensions it declares" in err, err


def test_the_verdict_keeps_the_vm_for_a_shell_import(tmp_path):
    """moy_play's VM-free rule over the two fixtures, as a board's census
    reads them: every import C-served is free; one shell-served row keeps the
    VM and is named."""
    from runtime import moy_play
    roles = str(tmp_path / "app_roles.moy")
    shell = str(tmp_path / "app_shell.moy")
    wasm_cart.build(ROLES, roles)
    wasm_cart.build(SHELL, shell)
    assert moy_play.census(roles) == ("wasm", True, "free")
    assert moy_play.census(shell) == ("wasm", False,
                                      "import moybyte.app.theme_set_variant")
    # A role whose permission the manifest does not name is no VM-free pass:
    # the native set's rule is unchanged for every other permission.
    p = os.path.join(roles, "manifest.json")
    with open(p) as f:
        man = json.load(f)
    man["moybyte"]["permissions"] = ["prefs", "net"]
    with open(p, "w") as f:
        json.dump(man, f)
    assert moy_play.census(roles) == ("wasm", False, "permission")
