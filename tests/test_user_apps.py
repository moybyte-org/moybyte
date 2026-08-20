"""USER APPS: a `.moy` cart with shell capabilities (#181, ui_refactor_2026-08
Phases 7 + 8).

Three things are pinned here, and the SECOND one is the important one:

1. `make_system_api` is a FILTER over the same `AppContext` the shipped apps
   get -- the only difference is that the `needs` tuple comes from a manifest's
   permissions instead of a class constant.
2. **An ungranted capability has no NAME.** Not a stub that returns None, not an
   object that raises a friendlier error -- absent, so touching it is a
   `NameError` in the cart. Both halves are asserted against the SAME cart
   source, so the difference is provably the manifest and nothing else.
3. A cart that crashes on every open stops being offered (`CrashGuard`), because
   otherwise a bad AUTO-RUN cart is a boot loop and a boot loop on this hardware
   is a brick.

Driven through the shared console (`runtime.host_app`), so this exercises the
plumbing the boards freeze: the store's permission carry, the Player's namespace
construction, the responsive canvas bind, and the shell's own exitable bar over
a running cart.
"""

import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

from runtime import app_context as _ac        # noqa: E402
from runtime import bar_layer as _bar         # noqa: E402
from runtime import crash_guard, host_app, moy_carts, system_api  # noqa: E402
from runtime.crash_guard import CrashGuard    # noqa: E402

DT = 1.0 / 30

# The shipped demo app, whose source both halves of the permission test share.
NOTES_DIR = ROOT / "system_carts" / "notes.moy"
NOTES_SRC = (NOTES_DIR / "main.py").read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _write_cart(carts_dir, name, src, perms=(), type="app", canvas="320x240"):
    d = Path(carts_dir) / (name + ".moy")
    d.mkdir(parents=True, exist_ok=True)
    man = {"title": name, "type": type, "canvas": canvas,
           "permissions": list(perms)}
    (d / "manifest.json").write_text(json.dumps(man))
    (d / "main.py").write_text(src)
    (d / "config.json").write_text("{}")
    return d


def _ws(tmp_path, **kw):
    return host_app.build_workstation(str(tmp_path / "carts"), **kw)


def _open(ws, title):
    """Run a cart from the launcher shelf, the way a kid reaches it."""
    for i, it in enumerate(ws.launcher.items):
        if it.get("title") == title:
            ws.launcher.sel = i
            ws.open()
            return
    raise AssertionError("cart not on the launcher: " + title)


def _frames(ws, n=3):
    for _ in range(n):
        ws.input.begin_frame()
        ws.frame(DT)


def _tap(ws, x, y):
    """A tap the running cart sees, through `touch()`."""
    ws.pointer.place(x, y)
    ws.pointer.click = True
    ws.input.begin_frame()
    ws.frame(DT)
    ws.pointer.click = False


def _tap_bar(ws, rect):
    """A tap on the shell's own chrome. Goes through `handle_pointer` (the
    router), which is where the bar contract lives -- the driver calls it, the
    frame loop does not."""
    x, y, w, h = rect
    ws.pointer.place(x + w // 2, y + h // 2)
    ws.pointer.click = True
    ws.handle_pointer()
    ws.pointer.click = False


def _hit_rect(ns, verb, arg=None):
    """The rect the cart's own draw pass registered for one widget -- so a tap
    test aims at what the app drew rather than at a hardcoded number."""
    for rect, v, a in ns["hits"]._items:
        if v == verb and a == arg:
            return rect
    raise AssertionError("the cart registered no %r hit" % (verb,))


# ---------------------------------------------------------------------------
# the permission -> role map
# ---------------------------------------------------------------------------

def test_every_role_is_either_grantable_or_named_ungrantable():
    """The ratchet: adding a role to `AppContext` forces a DECISION about
    whether a cart may have it. Without this, a new role defaults to
    "ungrantable by omission" -- correct today and invisible tomorrow, when
    somebody maps a permission onto it without noticing there was a policy."""
    grantable = set(system_api._ROLE_FOR.values())
    never = set(system_api.NEVER_GRANTED)
    assert grantable | never == set(_ac.ROLES), (
        "roles classified by neither _ROLE_FOR nor NEVER_GRANTED: %s"
        % sorted(set(_ac.ROLES) - (grantable | never)))
    assert not (grantable & never), "a role is both grantable and never"


def test_the_dangerous_roles_are_not_grantable():
    for role in ("shell", "carts", "wallpaper", "artwork"):
        assert role in system_api.NEVER_GRANTED
        assert role not in system_api._ROLE_FOR.values()


def test_a_manifest_asking_for_shell_or_carts_gets_neither():
    roles, kind = system_api.granted_roles(
        {"permissions": ["shell", "carts", "ota", "reboot", "graphics"]})
    assert roles == ()
    assert kind is None


@pytest.mark.parametrize("perms,roles,kind", [
    (["graphics", "input"], (), None),
    (["files"], ("files",), "docs"),
    (["files:tables"], ("files",), "tables"),
    (["files:recordings"], (), None),      # folder-valued: not a text/blob kind
    (["files:nonsense"], (), None),        # a typo NARROWS, it never widens
    (["prefs"], ("prefs",), None),
    (["appearance"], ("theme",), None),
    (["launch"], ("nav",), None),
    (["files:docs", "prefs"], ("files", "prefs"), "docs"),
    (["files:docs", "files:docs"], ("files",), "docs"),   # a repeat is one kind
    # TWO kinds is a manifest error (below); the residual here fails CLOSED
    # rather than keeping whichever was declared last.
    (["files:docs", "files:tables"], (), None),
    (["files", "files:tables", "prefs"], ("prefs",), None),
])
def test_granted_roles_reads_the_manifest(perms, roles, kind):
    assert system_api.granted_roles({"permissions": perms}) == (roles, kind)


@pytest.mark.parametrize("perms,bad", [
    (["files:docs"], False),
    (["files"], False),
    (["files", "files:docs"], False),           # the same kind, spelled twice
    (["files:nonsense", "files:docs"], False),  # the typo already narrowed away
    (["files:docs", "files:tables"], True),
    (["files", "files:tables"], True),          # bare `files` IS the docs kind
])
def test_two_file_kinds_is_a_manifest_error(perms, bad):
    """`files` is ONE kind-bound handle, so a second kind has nowhere to go.
    It used to be kept silently -- last declaration wins, order-dependent, no
    diagnostic -- which put an app's documents in `tables` and looked like a
    save that did not happen."""
    err = system_api.manifest_error({"permissions": perms})
    if not bad:
        assert err is None, err
    else:
        assert err and "file kinds" in err and "pick one" in err, err


def test_a_two_kind_manifest_is_refused_before_the_cart_runs(tmp_path):
    """End to end: the ordinary error panel names the manifest, the cart body
    never runs, and the refusal costs no crash-guard strike (a mis-declared
    permission is not a crash)."""
    carts = str(tmp_path / "carts")
    _write_cart(carts, "Greedy", "raise SystemExit\n",
                perms=["files:docs", "files:tables"])
    ws = _ws(tmp_path)
    _open(ws, "Greedy")
    assert ws.player.cart_error is not None
    assert "file kinds" in ws.player.cart_error, ws.player.cart_error
    assert ws.app_guard.strikes("greedy") == 0


def test_the_prefs_namespace_is_the_title_slug_and_matches_the_store():
    """Keyed on the TITLE, not the folder: the device seeds a folder from the
    title slug while the host copies the source folder, and an app whose saved
    settings changed identity across that crossing would lose them."""
    assert system_api.app_id_for({"title": "My Notes!"}) == "my_notes"
    for title in ("My Notes!", "Calc", "Tap Only Red", ""):
        assert system_api.slug(title) == moy_carts.slug(title)


def test_wants_layout_only_fires_on_a_top_level_def():
    assert system_api.wants_layout("def _layout(w, h, fs):\n    pass\n")
    assert system_api.wants_layout("x = 1\ndef _layout(w, h, fs):\n    pass\n")
    assert not system_api.wants_layout("# def _layout(w, h, fs) one day\n")
    assert not system_api.wants_layout("def f():\n    def _layout(a): pass\n")
    assert not system_api.wants_layout("")
    assert not system_api.wants_layout(None)


# ---------------------------------------------------------------------------
# the shipped demo app
# ---------------------------------------------------------------------------

def test_the_demo_app_opens_and_gets_exactly_what_it_declared(tmp_path):
    ws = _ws(tmp_path)
    _open(ws, "Notes")
    _frames(ws)
    assert ws.player.cart_error is None, ws.player.cart_error
    ns = ws.player.ns
    # granted
    assert isinstance(ns["files"], system_api.ScopedFiles)
    assert ns["files"].kind == "docs"
    assert ns["prefs"] is not None
    # ungated: how an app draws
    assert ns["ui"] is not None and ns["theme"]() is not None
    assert ns["screen"]() is ws.canvas
    assert ns["bar_h"]() == ws.app_bar_h()
    # NOT declared -> not present. Every one of these is a real capability the
    # shell has and this cart may not touch.
    for absent in ("carts", "shell", "set_theme", "themes", "open_app",
                   "wifi", "net", "artwork"):
        assert absent not in ns, absent + " leaked into a user app"


def test_the_demo_app_saves_a_document_the_rest_of_the_console_can_read(tmp_path):
    ws = _ws(tmp_path)
    _open(ws, "Notes")
    _frames(ws)
    ns = ws.player.ns
    ns["lines"][:] = ["HELLO", "WORLD"]
    x, y, w, h = _hit_rect(ns, "save")
    _tap(ws, x + w // 2, y + h // 2)
    assert ws.player.cart_error is None, ws.player.cart_error
    assert ns["status"].startswith("SAVED"), ns["status"]
    # It is a real user-files document, in the kind Writer and Files browse --
    # and a `moytext-v1` blob, not a bare string (a bare string decodes to
    # nothing, silently, and looks exactly like a save that never happened).
    names = moy_carts.list_files("docs", ws.carts_root)
    assert names, "nothing landed in files/docs"
    blob = moy_carts.load_file("docs", names[0], ws.carts_root)
    assert moy_carts.decode_text(blob) == ["HELLO", "WORLD"]
    # ...and its own prefs slot remembers it, namespaced under the app id.
    assert ws.system["notes_last"] == names[0]


def test_the_demo_apps_prefs_cannot_see_the_shells_own_settings(tmp_path):
    ws = _ws(tmp_path)
    _open(ws, "Notes")
    _frames(ws)
    prefs = ws.player.ns["prefs"]
    ws.system["theme"] = "night"
    assert prefs.get("theme") is None       # reads notes_theme, not theme
    prefs.set("theme", "hacked")
    assert ws.system["theme"] == "night"    # ...and writes notes_theme
    assert ws.system["notes_theme"] == "hacked"


def test_a_user_app_is_always_exitable_through_the_hosts_bar(tmp_path):
    """The bar contract reaches carts too: the shell draws the strip over a
    running app cart and routes its context-X, so a user app cannot trap a kid
    even if its own input handling is broken."""
    ws = _ws(tmp_path)
    _open(ws, "Notes")
    _frames(ws)
    assert ws.wm.top_is_player()
    _tap_bar(ws, _bar._ZONE_CONTEXT_X)
    assert not ws.wm.top_is_player(), "the context-X did not exit the app"


# ---------------------------------------------------------------------------
# THE NEGATIVE TEST: the same source, one permission less
# ---------------------------------------------------------------------------

def test_without_the_permission_the_files_name_is_ABSENT(tmp_path):
    """The same cart body, minus `files:docs` from its manifest.

    Two assertions, and both matter: the name is not in the namespace at all
    (not a stub, not a disabled object), and the cart consequently dies with a
    NameError that says `files`. A capability you were not granted has no NAME
    to probe or unwrap -- which is a stronger property than the granted objects
    themselves have (those are a speed bump, not a sandbox; see
    `test_the_scope_is_a_speed_bump_not_a_sandbox` and system_api's docstring)."""
    carts = str(tmp_path / "carts")
    _write_cart(carts, "Sneaky", NOTES_SRC, perms=["graphics", "input", "prefs"])
    ws = _ws(tmp_path)
    cart = next(c for c in ws._all_carts if c["title"] == "Sneaky")
    ws._open_workspace(cart)
    # Player.start directly, because the launcher path throws a failed start
    # straight into the code editor (crash-to-code) and RELEASES the world --
    # which is correct product behaviour and would take the namespace with it.
    assert ws.player.start(ws.project) is False
    ns = ws.player.ns
    assert ns is not None
    assert "files" not in ns
    assert "prefs" in ns                     # the one it DID declare is there
    assert ws.player.cart_error is not None
    assert "files" in ws.player.cart_error, ws.player.cart_error
    assert "NameError" in ws.player.cart_error, ws.player.cart_error


def test_the_grant_is_the_only_difference_between_the_two_namespaces(tmp_path):
    """The same source and the same factory, one manifest line apart: the key
    sets differ by exactly `files` and by nothing else."""
    ws = _ws(tmp_path)
    with_perm = system_api.make_system_api(
        ws.app_context, {"title": "N", "permissions": ["files:docs", "prefs"]})
    without = system_api.make_system_api(
        ws.app_context, {"title": "N", "permissions": ["prefs"]})
    assert set(with_perm) - set(without) == {"files"}
    assert set(without) - set(with_perm) == set()


def test_with_the_permission_the_same_source_runs(tmp_path):
    """The control arm: identical bytes, one manifest line back."""
    carts = str(tmp_path / "carts")
    _write_cart(carts, "Sneaky", NOTES_SRC,
                perms=["graphics", "input", "files:docs", "prefs"])
    ws = _ws(tmp_path)
    _open(ws, "Sneaky")
    _frames(ws)
    assert ws.player.cart_error is None, ws.player.cart_error
    assert ws.player.ns["files"].kind == "docs"


def test_a_scoped_grant_cannot_reach_another_kind(tmp_path):
    carts = str(tmp_path / "carts")
    _write_cart(carts, "Tabby", "def _update(dt):\n    pass\n\n\ndef _draw():\n"
                                "    cls(0)\n", perms=["files:tables"])
    ws = _ws(tmp_path)
    _open(ws, "Tabby")
    f = ws.player.ns["files"]
    assert f.kind == "tables"
    # The kind is bound at construction and is never an argument, so no
    # ARGUMENT to `save` reaches the kid's drawings.
    f.save_text("NOTE", "hi")
    assert moy_carts.list_files("drawings", ws.carts_root) == []
    assert moy_carts.list_files("tables", ws.carts_root) == ["note"]


def test_the_scope_is_a_speed_bump_not_a_sandbox(tmp_path):
    """What the narrowing IS, pinned so the docstrings cannot drift back into
    claiming containment.

    The published handles hold their internals name-mangled, so the one-hop
    reach-through (`files._files`, `prefs._ws`) is not there to be found by
    accident -- that is the property worth keeping, and it is asserted here.
    What is NOT claimed: that a determined cart cannot get out. It runs `exec`
    with real builtins, and MicroPython does not implement mangling at all
    (measured on the unix build), so on a board the same attribute is plainly
    readable. The value of the filter is a manifest that states what an app is
    for -- see system_api's module docstring."""
    carts = str(tmp_path / "carts")
    _write_cart(carts, "Tabby", "def _update(dt):\n    pass\n\n\ndef _draw():\n"
                                "    cls(0)\n", perms=["files:tables", "prefs"])
    ws = _ws(tmp_path)
    _open(ws, "Tabby")
    f = ws.player.ns["files"]
    for casual in ("_files", "files", "ws", "_ws"):
        assert not hasattr(f, casual), (
            "ScopedFiles.%s hands the unscoped role back in one hop" % casual)
    p = ws.player.ns["prefs"]
    for casual in ("_ws", "ws", "shell"):
        assert not hasattr(p, casual), "Prefs.%s is the Workstation" % casual
    # The roles the shipped apps get are mangled the same way.
    ctx = ws.app_context("probe", ("theme", "surface", "files"))
    for role in (ctx.theme, ctx.surface, ctx.files):
        assert not hasattr(role, "_ws"), type(role).__name__


def test_a_game_gets_no_app_api_at_all(tmp_path):
    carts = str(tmp_path / "carts")
    _write_cart(carts, "Gamey", "def _update(dt):\n    pass\n\n\ndef _draw():\n"
                                "    cls(0)\n",
                perms=["files:docs", "prefs", "appearance"], type="game")
    ws = _ws(tmp_path)
    _open(ws, "Gamey")
    _frames(ws)
    ns = ws.player.ns
    for absent in ("files", "prefs", "set_theme", "ui", "screen", "theme",
                   "bar_h"):
        assert absent not in ns, absent + " reached a GAME"


def test_an_identity_cart_of_a_shipped_app_is_not_a_user_app(tmp_path):
    """`calc.moy` is `type: "app"` too, but the launcher dispatches it to the
    shell's CalcAppLayer -- its `main.py` is only the older-shell fallback. It
    must not be handed the user-app surface, and it must not take crash
    strikes for a body nobody runs."""
    ws = _ws(tmp_path)
    calc = next(c for c in ws._all_carts if c["title"] == "Calc")
    assert ws.is_user_app(calc) is False
    assert ws.cart_broken(calc) is False


# ---------------------------------------------------------------------------
# the canvas: fixed by default, responsive by opt-in
# ---------------------------------------------------------------------------

RESPONSIVE_SRC = """
seen = []


def _layout(w, h, fs):
    seen.append((w, h, fs))


def _update(dt):
    pass


def _draw():
    cls(0)
"""

FIXED_SRC = """
def _update(dt):
    pass


def _draw():
    cls(0)
"""


def test_a_fixed_app_cart_draws_on_the_game_canvas(tmp_path):
    carts = str(tmp_path / "carts")
    _write_cart(carts, "Fixy", FIXED_SRC)
    ws = _ws(tmp_path, sys_size=(800, 480), font_scale=2)
    _open(ws, "Fixy")
    _frames(ws)
    assert ws.player.cart_error is None, ws.player.cart_error
    assert ws.app_full_canvas is False
    assert ws.canvas is not ws.sys_canvas
    assert (ws.canvas.w, ws.canvas.h) == (320, 240)
    assert ws.player.ns["W"] == 320


def test_a_responsive_app_cart_draws_on_the_system_canvas(tmp_path):
    carts = str(tmp_path / "carts")
    _write_cart(carts, "Flexy", RESPONSIVE_SRC)
    ws = _ws(tmp_path, sys_size=(800, 480), font_scale=2)
    _open(ws, "Flexy")
    _frames(ws)
    assert ws.player.cart_error is None, ws.player.cart_error
    assert ws.app_full_canvas is True
    assert ws.canvas is ws.sys_canvas
    assert ws.player.ns["seen"] == [(800, 480, 2)]
    assert ws.player.ns["W"] == 800
    # The composite degrades on the identity check it has always had, so the
    # pointer arrives in the coordinates the app drew in.
    assert ws.wm.viewport() == (0, 0, 1)
    assert ws._game_xy(11, 22) == (11, 22)


def test_a_responsive_app_cart_is_told_when_the_surface_changes(tmp_path):
    carts = str(tmp_path / "carts")
    _write_cart(carts, "Flexy", RESPONSIVE_SRC)
    ws = _ws(tmp_path, sys_size=(800, 480), font_scale=2)
    _open(ws, "Flexy")
    _frames(ws)
    seen = ws.player.ns["seen"]
    assert seen == [(800, 480, 2)]
    ws.set_font_scale(3, persist=False)
    _frames(ws, 2)
    assert seen[-1] == (800, 480, 3)
    _frames(ws, 3)
    assert len(seen) == 2, "a steady frame re-ran _layout: " + repr(seen)


def test_a_responsive_run_gives_the_bar_back_the_responsive_geometry(tmp_path):
    """The exitable strip has to follow the canvas. On the fixed path it is the
    frozen 320-wide cluster; on a 800px system canvas that would leave the
    context-X stranded mid-screen."""
    carts = str(tmp_path / "carts")
    _write_cart(carts, "Flexy", RESPONSIVE_SRC)
    ws = _ws(tmp_path, sys_size=(800, 480), font_scale=2)
    _open(ws, "Flexy")
    _frames(ws)
    assert ws.bar_layer._zone_is_game("tool") is False
    assert ws.app_bar_h() == ws.layout.status_h
    # The X is where the RESPONSIVE layout puts it (right-aligned on an 800px
    # canvas), not where the frozen 320-wide cluster would have left it.
    assert ws.layout.context_x_btn[0] > _bar._ZONE_CONTEXT_X[0]
    _tap_bar(ws, ws.layout.context_x_btn)
    assert not ws.wm.top_is_player(), "the responsive strip's X did not exit"


def test_the_run_canvas_is_given_back_on_exit(tmp_path):
    carts = str(tmp_path / "carts")
    _write_cart(carts, "Flexy", RESPONSIVE_SRC)
    ws = _ws(tmp_path, sys_size=(800, 480), font_scale=2)
    stock = ws.canvas
    _open(ws, "Flexy")
    _frames(ws)
    assert ws.canvas is ws.sys_canvas
    ws._exit_to_caller()
    _frames(ws)
    assert ws.canvas is stock
    assert ws.app_full_canvas is False
    assert ws.bar_layer._zone_is_game("tool") is True


def test_the_desk_world_keeps_a_responsive_cart_on_the_fixed_raster(tmp_path):
    """MEASURED, 2026-08-19: in the windowed desk world a cart lives in a
    WINDOW, and `_draw_player_window` blits `ws.canvas` into it -- so with the
    system canvas bound the window blits the screen into a rectangle of that
    screen and the desktop renders as a recursive smear of its own bar. A cart
    window is a `wm_windowed` change and out of this phase's scope, so the desk
    world keeps the fixed raster and the cart is TOLD (320, 240)."""
    carts = str(tmp_path / "carts")
    _write_cart(carts, "Flexy", RESPONSIVE_SRC)
    ws = _ws(tmp_path, sys_size=(1024, 600), font_scale=2, windowed=True)
    ws.open_desk()
    assert ws.windowed_chrome is True
    cart = next(c for c in ws._all_carts if c["title"] == "Flexy")
    ws._open_workspace(cart)
    ws.run(ws.project, ws.launcher_layer)
    assert ws.player.start(ws.project) is True
    _frames(ws)
    assert ws.player.cart_error is None, ws.player.cart_error
    assert ws.app_full_canvas is False
    assert ws.canvas is not ws.sys_canvas
    assert ws.player.ns["seen"] == [(320, 240, 1)]


def test_the_play_world_gives_a_responsive_cart_the_whole_surface(tmp_path):
    """From the fullscreen Library the desk is POPPED (`windowed_chrome` False),
    so the same cart gets the desktop-sized surface it asked for."""
    carts = str(tmp_path / "carts")
    _write_cart(carts, "Flexy", RESPONSIVE_SRC)
    ws = _ws(tmp_path, sys_size=(1024, 600), font_scale=2, windowed=True)
    ws.go_home()                          # the play world's Library
    assert ws.windowed_chrome is False
    _open(ws, "Flexy")
    _frames(ws)
    assert ws.player.cart_error is None, ws.player.cart_error
    assert ws.app_full_canvas is True
    assert ws.player.ns["seen"] == [(1024, 600, 2)]


def test_a_declared_small_canvas_wins_over_the_responsive_probe(tmp_path):
    """A manifest asking for 128x128 AND defining `_layout` is contradictory.
    The declared size has a SPEC contract behind it, so it wins -- and the cart
    is still told the truth about what it got."""
    carts = str(tmp_path / "carts")
    _write_cart(carts, "Both", RESPONSIVE_SRC, canvas="128x128")
    ws = _ws(tmp_path, sys_size=(800, 480), font_scale=2)
    _open(ws, "Both")
    _frames(ws)
    assert ws.player.cart_error is None, ws.player.cart_error
    assert ws.app_full_canvas is False
    assert (ws.canvas.w, ws.canvas.h) == (128, 128)
    assert ws.player.ns["seen"] == [(128, 128, 1)]


# ---------------------------------------------------------------------------
# Phase 8: the crash-loop guard
# ---------------------------------------------------------------------------

def test_the_guard_counts_a_strike_per_open_and_forgives_a_healthy_run():
    store = {}
    saves = []
    g = CrashGuard(store, lambda: saves.append(dict(store)))
    assert g.arm("app") is True
    assert g.strikes("app") == 1
    assert g.last_open() == "app"
    for _ in range(g.HEAL_FRAMES - 1):
        assert g.frame() is False
    assert g.frame() is True
    assert g.strikes("app") == 0
    assert g.last_open() is None
    assert g.frame() is False              # nothing armed: no further writes
    assert len(saves) == 2, "expected exactly one arm + one heal write"


def test_the_guard_disables_after_three_unhealed_opens():
    g = CrashGuard({})
    for i in range(g.STRIKES):
        assert g.arm("app") is True, i
        g.release()                        # died before the heal
    assert g.disabled("app") is True
    assert g.arm("app") is False
    assert g.broken_ids() == ["app"]
    assert g.forgive("app") is True
    assert g.disabled("app") is False
    assert g.arm("app") is True


def test_the_guard_holds_state_across_a_rebound_store():
    """`load_system()` REBINDS `ws.system`, so the guard must read the store
    through a callable. A guard holding the boot-time dict counts strikes into
    an object nobody persists -- i.e. silently does nothing."""
    holder = {"system": {}}
    g = CrashGuard(lambda: holder["system"])
    g.arm("app")
    carried = holder["system"][crash_guard.KEY]
    holder["system"] = {crash_guard.KEY: carried}      # what load_system does
    assert g.strikes("app") == 1


def test_a_corrupt_guard_slot_cannot_disable_everything():
    for junk in ("nonsense", 7, [1, 2], None):
        g = CrashGuard({crash_guard.KEY: junk})
        assert g.disabled("app") is False
        assert g.arm("app") is True


def test_a_healthy_app_leaves_no_strike_behind(tmp_path):
    ws = _ws(tmp_path)
    _open(ws, "Notes")
    _frames(ws, CrashGuard.HEAL_FRAMES + 1)
    assert ws.player.cart_error is None, ws.player.cart_error
    assert ws.app_guard.strikes("notes") == 0
    assert ws.app_guard.last_open() is None
    assert ws.cart_broken(ws.cart) is False


def test_a_game_is_not_guarded(tmp_path):
    """Two writes per open is not free, and a game that always crashes shows
    the panel and is not a brick. The guard is for content the shell runs on
    the kid's behalf."""
    ws = _ws(tmp_path)
    _open(ws, "Star Catcher")
    _frames(ws)
    assert ws.app_guard.last_open() is None
    assert ws.system.get(crash_guard.KEY, {"strikes": {}})["strikes"] == {}


BOOM_SRC = """
def _init():
    raise ValueError("always broken")


def _update(dt):
    pass


def _draw():
    cls(0)
"""


def test_a_cart_that_raises_on_every_open_is_disabled_after_three(tmp_path):
    """The Phase 8 gate. Three failed opens and the console stops running it;
    the fourth tap lands on the ordinary error panel, whose top bar carries
    EDIT/CODE -- so "it is broken" and "here is how you fix it" are one screen.
    The cart's own code is never reached on that fourth open."""
    carts = str(tmp_path / "carts")
    _write_cart(carts, "Boomy", BOOM_SRC)
    ws = _ws(tmp_path)
    for i in range(3):
        ws.go_home()
        _open(ws, "Boomy")
        assert ws.player.cart_error is not None, i
        assert "always broken" in ws.player.cart_error, ws.player.cart_error
        assert ws.app_guard.strikes("boomy") == i + 1
    boomy = next(c for c in ws._all_carts if c["title"] == "Boomy")
    assert ws.cart_broken(boomy) is True
    ws.go_home()
    _open(ws, "Boomy")
    assert ws.player.cart_error is not None
    assert "turned off" in ws.player.cart_error, ws.player.cart_error
    assert "always broken" not in ws.player.cart_error, "the cart RAN again"


def test_the_strikes_survive_a_reboot(tmp_path):
    """The strike lives in `system.json`, which is the whole point: an
    in-process counter cannot outlive the hang it is counting."""
    carts = str(tmp_path / "carts")
    _write_cart(carts, "Boomy", BOOM_SRC)
    for _ in range(3):
        ws = _ws(tmp_path)                 # a fresh boot each time
        _open(ws, "Boomy")
        assert ws.player.cart_error is not None
    ws = _ws(tmp_path)
    assert ws.app_guard.strikes("boomy") == 3
    boomy = next(c for c in ws._all_carts if c["title"] == "Boomy")
    assert ws.cart_broken(boomy) is True


def test_a_broken_app_stays_editable_in_the_picker(tmp_path):
    """Editing it is how it gets fixed, so the picker must still offer it --
    which also means the temporary #181 app-cart hide must not catch it."""
    carts = str(tmp_path / "carts")
    _write_cart(carts, "Boomy", BOOM_SRC)
    ws = _ws(tmp_path)
    for _ in range(3):
        ws.go_home()
        _open(ws, "Boomy")
    ws.go_home()
    titles = [it.get("title") for it in ws._picker_items(ws._all_carts)]
    assert "Boomy" in titles


FIXED_SRC = """
def _update(dt):
    pass


def _draw():
    cls(0)
"""


def test_editing_the_code_forgives_a_struck_out_app(tmp_path):
    """The other half of three strikes: the refusal panel says "EDIT it", so
    editing it has to be a way back.

    Nothing called `CrashGuard.forgive` when the guard shipped, and saving
    fixed code cleared nothing -- the only escapes were renaming the cart or
    hand-editing `system.json`, i.e. the panel's one instruction was a lie.
    A COMMITTED code change is the hook (`Project.commit_code` ->
    `ws.forgive_app`), because code is the only edit that can change whether
    the cart hangs, faults or eats the heap."""
    carts = str(tmp_path / "carts")
    _write_cart(carts, "Boomy", BOOM_SRC)
    ws = _ws(tmp_path)
    for _ in range(3):
        ws.go_home()
        _open(ws, "Boomy")
    boomy = next(c for c in ws._all_carts if c["title"] == "Boomy")
    assert ws.cart_broken(boomy) is True

    # The fourth open is refused -- and lands on the panel whose bar edits it.
    ws.go_home()
    _open(ws, "Boomy")
    assert "turned off" in ws.player.cart_error, ws.player.cart_error

    # The kid fixes the code and it commits.
    ws.set_menu_view("code")
    ws.screen = "menu"
    ws.editor.set_text(FIXED_SRC)
    assert ws.save_code() is True
    assert ws.app_guard.strikes("boomy") == 0
    assert ws.cart_broken(boomy) is False

    # ...and the app opens and runs again, with a full three strikes back.
    ws.go_home()
    _open(ws, "Boomy")
    _frames(ws, CrashGuard.HEAL_FRAMES + 1)
    assert ws.player.cart_error is None, ws.player.cart_error
