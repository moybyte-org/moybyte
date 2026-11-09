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
from runtime import app_perms, crash_guard, moy_carts, system_api  # noqa: E402
import moy_app                                 # noqa: E402
from runtime.crash_guard import CrashGuard    # noqa: E402
from runtime.moy_spine import Settings        # noqa: E402
from ws_helpers import build_ws, select_on_home  # noqa: E402

DT = 1.0 / 30

# The shipped demo app, whose source both halves of the permission test share.
NOTES_DIR = ROOT / "system_carts" / "moybyte.notes.moy"
NOTES_SRC = (NOTES_DIR / "main.py").read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _write_cart(carts_dir, name, src, perms=(), type="app", canvas="320x240"):
    d = Path(carts_dir) / (name + ".moy")
    d.mkdir(parents=True, exist_ok=True)
    man = {"title": name, "canvas": canvas,
           "moybyte": {"type": type, "permissions": list(perms)}}
    (d / "manifest.json").write_text(json.dumps(man))
    (d / "main.py").write_text(src)
    (d / "config.json").write_text("{}")
    return d


def _open(ws, title):
    """Run a cart from the launcher shelf, the way a kid reaches it."""
    if select_on_home(ws, title) is None:
        raise AssertionError("cart not on the launcher: " + title)
    ws.open()


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


# ---------------------------------------------------------------------------
# the permission -> role map
# ---------------------------------------------------------------------------

# The roles no permission maps to: a cart never has them, whatever its
# manifest says.
NEVER_GRANTED = ("carts", "install", "wallpaper", "artwork", "damage",
                 "surface", "notify")


def test_every_role_is_either_grantable_or_named_ungrantable():
    """The ratchet: adding a role to the table forces a DECISION about
    whether a cart may have it. Without this, a new role defaults to
    "ungrantable by omission" -- correct today and invisible tomorrow, when
    somebody maps a permission onto it without noticing there was a policy.
    The policy is C's (native/moy_app): its permission table is an allowlist."""
    grantable = {role for _perm, role in moy_app.perms()}
    # ...and the permission table's role rows are that allowlist
    # (runtime/shell/app_perms.py, the table the install prompt reads)
    assert grantable == {r[app_perms.GRANTS][5:] for r in app_perms.TABLE
                         if r[app_perms.GRANTS].startswith("role:")}
    never = set(NEVER_GRANTED)
    assert grantable | never == set(_ac.ROLES), (
        "roles classified by neither moy_app.perms() nor NEVER_GRANTED: %s"
        % sorted(set(_ac.ROLES) - (grantable | never)))
    assert not (grantable & never), "a role is both grantable and never"


def test_the_dangerous_roles_are_not_grantable():
    granted = {role for _perm, role in moy_app.perms()}
    for role in ("carts", "wallpaper", "artwork"):
        assert role in NEVER_GRANTED and role not in granted


def test_a_manifest_asking_for_shell_or_carts_gets_neither():
    roles, kind = moy_app.policy(["shell", "carts", "ota", "reboot", "graphics"])
    assert roles == ()
    assert kind is None


@pytest.mark.parametrize("perms,roles,kind", [
    (["graphics", "input"], (), None),
    (["files"], ("files",), "docs"),
    (["files:music"], ("files",), "music"),
    (["files:recordings"], ("files",), "recordings"),
    (["files:pictures"], ("files",), "drawings"),   # a folder names its own kind
    (["files:docs"], (), None),            # a kind is no folder: nothing
    (["files:nonsense"], (), None),        # a typo NARROWS, it never widens
    (["prefs"], ("prefs",), None),
    (["appearance"], ("theme",), None),
    (["launch"], ("nav",), None),
    (["files:documents", "prefs"], ("files", "prefs"), "docs"),
    (["prefs", "files:documents"], ("files", "prefs"), "docs"),  # the table's order
    (["files:documents", "files:documents"], ("files",), "docs"),   # a repeat is one kind
    # TWO kinds is a manifest error (below); the residual here fails CLOSED
    # rather than keeping whichever was declared last.
    (["files:documents", "files:music"], (), None),
    (["files", "files:music", "prefs"], ("prefs",), None),
])
def test_the_policy_reads_the_manifest(perms, roles, kind):
    assert moy_app.policy(perms) == (roles, kind)


@pytest.mark.parametrize("perms,bad", [
    (["files:documents"], False),
    (["files"], False),
    (["files", "files:documents"], False),           # the same folder, spelled twice
    (["files:nonsense", "files:documents"], False),  # the typo already narrowed away
    (["files:documents", "files:music"], True),
    (["files", "files:music"], True),          # bare `files` IS documents
])
def test_two_file_kinds_is_a_manifest_error(perms, bad):
    """`files` is ONE kind-bound handle, so a second kind has nowhere to go.
    It used to be kept silently -- last declaration wins, order-dependent, no
    diagnostic -- which put an app's documents in `music` and looked like a
    save that did not happen."""
    err = moy_app.manifest_error(perms)
    if not bad:
        assert err is None, err
    else:
        assert err and "file folders" in err and "pick one" in err, err


def test_a_two_kind_manifest_is_refused_before_the_cart_runs(tmp_path):
    """End to end: the ordinary error panel names the manifest, the cart body
    never runs, and the refusal costs no crash-guard strike (a mis-declared
    permission is not a crash)."""
    carts = str(tmp_path / "carts")
    _write_cart(carts, "Greedy", "raise SystemExit\n",
                perms=["files:documents", "files:music"])
    ws = build_ws(tmp_path)
    _open(ws, "Greedy")
    assert ws.run.player.cart_error is not None
    assert "file folders" in ws.run.player.cart_error, ws.run.player.cart_error
    assert ws.app_guard.strikes("Greedy") == 0


def test_the_grant_is_keyed_by_the_carts_id():
    """The cart's id (#162, SPEC.md 3.1): the manifest's `<author>.<name>`, or
    its folder less `.moy` -- what the store's cart dict carries as `id`. A
    renamed cart keeps its prefs and strikes; a path-less built-in has no id
    and is keyed by its title's slug (ASCII letters and digits, the C rule)."""
    assert moy_app.id_for("moybyte.notes", "Notes") == "moybyte.notes"
    assert moy_app.id_for("moybyte.notes", "Renamed") == "moybyte.notes"
    assert moy_app.id_for(None, "My Notes!") == "my_notes"
    assert moy_app.id_for(None, None) == "app"
    assert moy_app.id_for(None, "!!!") == "cart"
    for title in ("My Notes!", "Calc", "Tap Only Red"):
        assert moy_app.id_for(None, title) == moy_carts.slug(title)


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
    ws = build_ws(tmp_path)
    _open(ws, "Notes")
    _frames(ws)
    assert ws.run.player.cart_error is None, ws.run.player.cart_error
    ns = ws.run.player.ns
    # granted
    assert isinstance(ns["files"], system_api.ScopedFiles)
    assert ns["files"].kind == "docs"
    assert ns["prefs"] is not None
    # ungated: how an app draws
    assert ns["ui"] is not None and ns["theme"]() is not None
    assert ns["screen"]() is ws.run.canvas
    assert ns["bar_h"]() == ws.app_bar_h()
    # NOT declared -> not present. Every one of these is a real capability the
    # shell has and this cart may not touch.
    for absent in ("carts", "shell", "set_theme", "themes", "open_app",
                   "wifi", "net", "artwork"):
        assert absent not in ns, absent + " leaked into a user app"


def test_the_demo_app_saves_a_document_the_rest_of_the_console_can_read(tmp_path):
    """Notes types through the EDITOR HANDLE and its note lands as a plain
    `.md` the rest of the console reads -- the cart holds no text of its own."""
    ws = build_ws(tmp_path)
    _open(ws, "Notes")
    _frames(ws)
    ns = ws.run.player.ns
    ns["_ask"]("new")                          # New note asks a name...
    _frames(ws)
    ns["_answer"]()                            # ...and Make takes the auto-name
    _frames(ws)
    ed = ns["ed"]
    assert ed is not None and ws.run.player.cart_error is None, ws.run.player.cart_error
    ed.set_text("HELLO\nWORLD")
    ed.save()
    # A real user-files document, in the kind Files browses -- a plain `.md` on
    # the card holding exactly what the handle held.
    names = moy_carts.list_files("docs", ws.carts_root)
    assert names, "nothing landed in files/Documents"
    blob = moy_carts.load_file("docs", names[0], ws.carts_root)
    assert blob == "HELLO\nWORLD"
    assert moy_carts.file_path("docs", names[0], ws.carts_root).endswith(".md")
    # ...and the cart reads its own note back through a fresh handle.
    again = ns["open_editor"](names[0])
    assert again.text() == "HELLO\nWORLD"
    # ...and its own prefs slot remembers it, namespaced under the app id.
    assert ws.system.get("moybyte.notes_last") == names[0]


def test_the_demo_apps_prefs_cannot_see_the_shells_own_settings(tmp_path):
    ws = build_ws(tmp_path)
    _open(ws, "Notes")
    _frames(ws)
    prefs = ws.run.player.ns["prefs"]
    ws.system.set("theme", "night")
    assert prefs.get("theme") is None       # reads moybyte.notes_theme, not theme
    prefs.set("theme", "hacked")
    assert ws.system.get("theme") == "night"    # ...and writes moybyte.notes_theme
    assert ws.system.get("moybyte.notes_theme") == "hacked"


def test_a_user_app_is_always_exitable_through_the_hosts_bar(tmp_path):
    """The bar contract reaches carts too: the shell draws the strip over a
    running app cart and routes its way out (the chevron, or the context-X a
    layout places), so a user app cannot trap a kid
    even if its own input handling is broken."""
    ws = build_ws(tmp_path)
    _open(ws, "Notes")
    _frames(ws)
    assert ws.wm.top_is_player()
    bl = ws.bar_layer
    where = "tool" if bl.cell("tool", "zone") is not None else "app"
    rect = bl.back_rect(where)
    if rect is None:
        bar, (x, y, w, h) = bl.cell(where, "close")
        rect = (bar[1][0] + x, bar[1][1] + y, w, h)
    _tap_bar(ws, rect)
    assert not ws.wm.top_is_player(), "the bar's way out did not exit the app"


# ---------------------------------------------------------------------------
# THE NEGATIVE TEST: the same source, one permission less
# ---------------------------------------------------------------------------

def test_without_the_permission_the_files_name_is_ABSENT(tmp_path):
    """The same cart body, minus `files:documents` from its manifest.

    Two assertions, and both matter: the name is not in the namespace at all
    (not a stub, not a disabled object), and the cart consequently dies with a
    NameError that says `files`. A capability you were not granted has no NAME
    to probe or unwrap -- which is a stronger property than the granted objects
    themselves have (those are a speed bump, not a sandbox; see
    `test_the_scope_is_a_speed_bump_not_a_sandbox` and system_api's docstring)."""
    carts = str(tmp_path / "carts")
    _write_cart(carts, "Sneaky", NOTES_SRC, perms=["graphics", "input", "prefs"])
    ws = build_ws(tmp_path)
    cart = next(c for c in ws.carts.all if c["title"] == "Sneaky")
    ws._open_workspace(cart)
    # Player.start directly, because the launcher path throws a failed start
    # straight into the code editor (crash-to-code) and RELEASES the world --
    # which is correct product behaviour and would take the namespace with it.
    assert ws.run.player.start(ws.run.project) is False
    ns = ws.run.player.ns
    assert ns is not None
    assert "files" not in ns
    assert "prefs" in ns                     # the one it DID declare is there
    assert ws.run.player.cart_error is not None
    assert "files" in ws.run.player.cart_error, ws.run.player.cart_error
    assert "NameError" in ws.run.player.cart_error, ws.run.player.cart_error


def test_the_grant_is_the_only_difference_between_the_two_namespaces(tmp_path):
    """The same source and the same factory, one manifest line apart: the key
    sets differ by exactly `files` and by nothing else."""
    ws = build_ws(tmp_path)
    with_perm = system_api.make_system_api(
        ws.app_context, {"title": "N", "permissions": ["files:documents", "prefs"]})
    without = system_api.make_system_api(
        ws.app_context, {"title": "N", "permissions": ["prefs"]})
    assert set(with_perm) - set(without) == {"files"}
    assert set(without) - set(with_perm) == set()


def test_with_the_permission_the_same_source_runs(tmp_path):
    """The control arm: identical bytes, one manifest line back."""
    carts = str(tmp_path / "carts")
    _write_cart(carts, "Sneaky", NOTES_SRC,
                perms=["graphics", "input", "files:documents", "prefs"])
    ws = build_ws(tmp_path)
    _open(ws, "Sneaky")
    _frames(ws)
    assert ws.run.player.cart_error is None, ws.run.player.cart_error
    assert ws.run.player.ns["files"].kind == "docs"


def test_a_scoped_grant_cannot_reach_another_kind(tmp_path):
    carts = str(tmp_path / "carts")
    _write_cart(carts, "Tabby", "def _update(dt):\n    pass\n\n\ndef _draw():\n"
                                "    cls(0)\n", perms=["files:music"])
    ws = build_ws(tmp_path)
    _open(ws, "Tabby")
    f = ws.run.player.ns["files"]
    assert f.kind == "music"
    # The kind is bound at construction and is never an argument, so no
    # ARGUMENT to `save` reaches the kid's drawings.
    f.save_text("NOTE", "hi")
    assert moy_carts.list_files("drawings", ws.carts_root) == []
    assert moy_carts.list_files("music", ws.carts_root) == ["note"]


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
                                "    cls(0)\n", perms=["files:music", "prefs"])
    ws = build_ws(tmp_path)
    _open(ws, "Tabby")
    f = ws.run.player.ns["files"]
    for casual in ("_files", "files", "ws", "_ws"):
        assert not hasattr(f, casual), (
            "ScopedFiles.%s hands the unscoped role back in one hop" % casual)
    p = ws.run.player.ns["prefs"]
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
                perms=["files:documents", "prefs", "appearance"], type="game")
    ws = build_ws(tmp_path)
    _open(ws, "Gamey")
    _frames(ws)
    ns = ws.run.player.ns
    for absent in ("files", "prefs", "set_theme", "ui", "screen", "theme",
                   "bar_h"):
        assert absent not in ns, absent + " reached a GAME"


def test_an_identity_cart_of_a_shipped_app_is_not_a_user_app(tmp_path):
    """`moybyte.calc.moy` is `type: "app"` too, but the launcher dispatches it to the
    shell's CalcAppLayer -- its `main.py` is only the older-shell fallback. It
    must not be handed the user-app surface, and it must not take crash
    strikes for a body nobody runs."""
    ws = build_ws(tmp_path)
    calc = next(c for c in ws.carts.all if c["title"] == "Calc")
    assert ws.is_user_app(calc) is False
    assert ws.cart_broken(calc) is False


def _copy_of_calc(carts_dir, folder):
    """A cart in `folder` whose manifest is the built-in Calc's, word for
    word: its id, its title, its marker and `"system": true`."""
    import shutil
    d = Path(carts_dir) / folder
    shutil.copytree(ROOT / "system_carts" / "moybyte.calc.moy", d)
    man = json.loads((d / "manifest.json").read_text(encoding="utf-8"))
    man["id"] = "moybyte.calc"
    man.setdefault("moybyte", {})["system"] = True
    (d / "manifest.json").write_text(json.dumps(man), encoding="utf-8")
    return d


def test_a_user_cart_copying_a_built_ins_manifest_gets_no_built_in_privileges(tmp_path):
    """A built-in is known by where it came from, the firmware's seed, not by
    anything its manifest says: a cart installed beside it with Calc's
    manifest, and one copied onto the card into a folder of the built-ins'
    namespace the seed never wrote, are neither Calc nor any built-in, and
    share none of Calc's grant key (its prefs and consent)."""
    from runtime.moy_store_base import builtin_name
    carts = tmp_path / "carts"
    carts.mkdir()
    _copy_of_calc(carts, "my_calc.moy")
    _copy_of_calc(carts, "moybyte.calc_too.moy")
    ws = build_ws(tmp_path)
    by = {Path(c["path"]).name: c for c in ws.carts.all if c.get("path")}
    calc = by["moybyte.calc.moy"]
    assert ws.is_system_app(calc) and system_api.draws_in_look(calc)
    for folder in ("my_calc.moy", "moybyte.calc_too.moy"):
        fake = by[folder]
        assert fake["title"] == "Calc" and "calc" in fake["permissions"]
        assert fake["id"] == folder[:-4]
        assert app_perms.app_id(fake) != app_perms.app_id(calc)
        assert builtin_name(fake["path"]) is None
        assert not ws.is_system_app(fake)
        assert not system_api.draws_in_look(fake)
        assert ws.is_user_app(fake)


def test_a_text_marker_names_the_text_app_only_on_a_built_in(tmp_path):
    """`editor` is the marker the Files router opens a text file with; a cart
    the kid made that claims it is not the console's text app."""
    ws = build_ws(tmp_path)
    notes = next(c for c in ws.carts.all if c.get("path", "").endswith("moybyte.notes.moy"))
    assert system_api.is_text_app(notes)
    mine = dict(notes, path=str(tmp_path / "carts" / "local.notes.moy"))
    assert not system_api.is_text_app(mine)


def test_the_built_ins_namespace_is_the_seeds_alone(tmp_path):
    """No door but the seed writes a folder of the built-ins' namespace: a
    carts index naming one (or a built-in's id) is refused, and a cart made
    under an author named after it is made as the kid's."""
    from runtime import cart_index
    entry = {"id": "calc", "name": "Calc", "folder": "calc.moy", "runtime": "python",
             "version": 1}
    assert cart_index.check_cart(dict(entry, folder="moybyte.calc.moy")) \
        == "folder 'moybyte.calc.moy'"
    assert cart_index.check_cart(dict(entry, id="moybyte.calc")) == "id 'moybyte.calc'"
    root = str(tmp_path / "carts")
    moy_carts.ensure_dirs(root)
    made = moy_carts.create("Calc", root, ns="moybyte")
    assert Path(made["path"]).name == "local.calc.moy"


def test_a_copied_cart_cannot_take_another_apps_id(tmp_path):
    """An id names a cart only where no other folder owns it: a cart copied
    in claiming an installed app's id takes its own folder's name, and with
    it a grant key, prefs and consent record of its own; an id no folder
    owns stays the manifest's. A carts index installs a cart only into its
    id's folder, so an installed app owns its id."""
    from runtime import cart_index
    carts = Path(tmp_path / "carts")
    for folder, cid in (("ann.pong.moy", "ann.pong"), ("pong_copy.moy", "ann.pong"),
                        ("solo.moy", "ann.solo")):
        d = _write_cart(str(carts), folder[:-4], "def _draw():\n    cls(0)\n",
                        perms=["prefs"])
        man = json.loads((d / "manifest.json").read_text())
        man["id"] = cid
        (d / "manifest.json").write_text(json.dumps(man))
    ws = build_ws(tmp_path)
    ids = dict((Path(c["path"]).name, c["id"]) for c in ws.carts.all)
    assert (ids["ann.pong.moy"], ids["pong_copy.moy"], ids["solo.moy"]) \
        == ("ann.pong", "pong_copy", "ann.solo")
    ws.consent.record("ann.pong", ["prefs"])
    copy = next(c for c in ws.carts.all if c["path"].endswith("/pong_copy.moy"))
    assert app_perms.app_id(copy) == "pong_copy" and ws.consent.get("pong_copy") is None
    entry = {"id": "ann.pong", "name": "Pong", "folder": "ann.pong.moy",
             "runtime": "python", "version": 1}
    assert cart_index.check_cart(dict(entry, folder="pong.moy")) == "id 'ann.pong'"


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
    ws = build_ws(tmp_path, sys_size=(800, 480), font_scale=2)
    _open(ws, "Fixy")
    _frames(ws)
    assert ws.run.player.cart_error is None, ws.run.player.cart_error
    assert ws.run.app_full_canvas is False
    assert ws.run.canvas is not ws.sys_canvas
    assert (ws.run.canvas.w, ws.run.canvas.h) == (320, 240)
    assert ws.run.player.ns["W"] == 320


def test_a_responsive_app_cart_draws_on_the_system_canvas(tmp_path):
    carts = str(tmp_path / "carts")
    _write_cart(carts, "Flexy", RESPONSIVE_SRC)
    ws = build_ws(tmp_path, sys_size=(800, 480), font_scale=2)
    _open(ws, "Flexy")
    _frames(ws)
    assert ws.run.player.cart_error is None, ws.run.player.cart_error
    assert ws.run.app_full_canvas is True
    assert ws.run.canvas is ws.sys_canvas
    assert ws.run.player.ns["seen"] == [(800, 480, 2)]
    assert ws.run.player.ns["W"] == 800
    # The composite degrades on the identity check it has always had, so the
    # pointer arrives in the coordinates the app drew in.
    assert ws.wm.viewport() == (0, 0, 1)
    assert ws._game_xy(11, 22) == (11, 22)


def test_a_responsive_app_cart_is_told_when_the_surface_changes(tmp_path):
    carts = str(tmp_path / "carts")
    _write_cart(carts, "Flexy", RESPONSIVE_SRC)
    ws = build_ws(tmp_path, sys_size=(800, 480), font_scale=2)
    _open(ws, "Flexy")
    _frames(ws)
    seen = ws.run.player.ns["seen"]
    assert seen == [(800, 480, 2)]
    ws.look.set_font_scale(3, persist=False)
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
    ws = build_ws(tmp_path, sys_size=(800, 480), font_scale=2)
    _open(ws, "Flexy")
    _frames(ws)
    assert ws.bar_layer._zone_is_game("tool") is False
    assert ws.app_bar_h() == ws.layout.status_h
    # The back is where the RESPONSIVE layout puts it (the head of its zone,
    # past the Moy tile), in the responsive bar's rows.
    back = ws.bar_layer.back_rect("tool")
    assert back is not None and back[3] > _bar._STATUS_H - 2
    _tap_bar(ws, back)
    assert not ws.wm.top_is_player(), "the responsive strip's back did not exit"


def test_the_run_canvas_is_given_back_on_exit(tmp_path):
    carts = str(tmp_path / "carts")
    _write_cart(carts, "Flexy", RESPONSIVE_SRC)
    ws = build_ws(tmp_path, sys_size=(800, 480), font_scale=2)
    stock = ws.run.canvas
    _open(ws, "Flexy")
    _frames(ws)
    assert ws.run.canvas is ws.sys_canvas
    ws._exit_to_caller()
    _frames(ws)
    assert ws.run.canvas is stock
    assert ws.run.app_full_canvas is False
    assert ws.bar_layer.tool_band() is not None     # the compact band's again


def test_the_desk_world_gives_a_responsive_cart_its_window(tmp_path):
    """MEASURED, 2026-08-19: in the windowed desk world a cart lives in a
    WINDOW, and `_draw_player_window` blits `ws.run.canvas` into it -- so with the
    system canvas bound the window blits the screen into a rectangle of that
    screen and the desktop renders as a recursive smear of its own bar. The
    cart's surface is its window's instead: a canvas of its own, never the
    system one, and the cart is told the window's content size."""
    carts = str(tmp_path / "carts")
    _write_cart(carts, "Flexy", RESPONSIVE_SRC)
    ws = build_ws(tmp_path, sys_size=(1024, 600), font_scale=2, windowed=True)
    ws.open_desk()
    assert ws.windowed_chrome is True
    cart = next(c for c in ws.carts.all if c["title"] == "Flexy")
    ws._open_workspace(cart)
    ws.play(ws.launcher_layer)
    assert ws.run.player.start(ws.run.project) is True
    _frames(ws)
    assert ws.run.player.cart_error is None, ws.run.player.cart_error
    assert ws.run.app_full_canvas is True and ws.run.app_window_canvas is True
    assert ws.run.canvas is not ws.sys_canvas
    w, h = ws.app_window_size()
    assert ws.run.player.ns["seen"][-1][:2] == (w, h)
    assert ws.wm._wins["desktop"].content_rect()[2:] == (w, h)


def test_console_mode_gives_a_responsive_cart_the_whole_surface(tmp_path):
    """In console mode (`windowed_chrome` False) the same cart gets the
    desktop-sized surface it asked for."""
    carts = str(tmp_path / "carts")
    _write_cart(carts, "Flexy", RESPONSIVE_SRC)
    ws = build_ws(tmp_path, sys_size=(1024, 600), font_scale=2, windowed=True)
    ws.open_library()                     # the desk's PLAY: console mode
    assert ws.windowed_chrome is False
    _open(ws, "Flexy")
    _frames(ws)
    assert ws.run.player.cart_error is None, ws.run.player.cart_error
    assert ws.run.app_full_canvas is True
    assert ws.run.player.ns["seen"] == [(1024, 600, 2)]


def test_a_declared_small_canvas_wins_over_the_responsive_probe(tmp_path):
    """A manifest asking for 128x128 AND defining `_layout` is contradictory.
    The declared size has a SPEC contract behind it, so it wins -- and the cart
    is still told the truth about what it got."""
    carts = str(tmp_path / "carts")
    _write_cart(carts, "Both", RESPONSIVE_SRC, canvas="128x128")
    ws = build_ws(tmp_path, sys_size=(800, 480), font_scale=2)
    _open(ws, "Both")
    _frames(ws)
    assert ws.run.player.cart_error is None, ws.run.player.cart_error
    assert ws.run.app_full_canvas is False
    assert (ws.run.canvas.w, ws.run.canvas.h) == (128, 128)
    assert ws.run.player.ns["seen"] == [(128, 128, 1)]


# ---------------------------------------------------------------------------
# Phase 8: the crash-loop guard
# ---------------------------------------------------------------------------

def test_the_guard_counts_a_strike_per_open_and_forgives_a_healthy_run():
    saves = []
    store = Settings(lambda text: saves.append(text))
    g = CrashGuard(store)
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
    g = CrashGuard(Settings())
    for i in range(g.STRIKES):
        assert g.arm("app") is True, i
        g.release()                        # died before the heal
    assert g.disabled("app") is True
    assert g.arm("app") is False
    assert g.broken_ids() == ["app"]
    assert g.forgive("app") is True
    assert g.disabled("app") is False
    assert g.arm("app") is True


def test_the_guard_holds_state_across_a_reload():
    """The guard holds the settings STORE, not a callable that fetches it -- so
    a reload has to arrive IN that object. `SystemStore.load()` replaces the
    rows in place (`adopt`) for exactly this reason (#209 landing B); a load
    that rebound the name instead would leave the guard counting strikes into an
    orphan nobody persists, silently."""
    settings = Settings()
    g = CrashGuard(settings)
    g.arm("app")
    carried = settings.get(crash_guard.KEY)
    settings.adopt({crash_guard.KEY: carried})         # what SystemStore.load does
    assert g.strikes("app") == 1


def test_a_corrupt_guard_slot_cannot_disable_everything():
    for junk in ("nonsense", 7, [1, 2], None):
        s = Settings()
        s.adopt({crash_guard.KEY: junk})
        g = CrashGuard(s)
        assert g.disabled("app") is False
        assert g.arm("app") is True


def test_a_healthy_app_leaves_no_strike_behind(tmp_path):
    ws = build_ws(tmp_path)
    _open(ws, "Notes")
    _frames(ws, CrashGuard.HEAL_FRAMES + 1)
    assert ws.run.player.cart_error is None, ws.run.player.cart_error
    assert ws.app_guard.strikes("moybyte.notes") == 0
    assert ws.app_guard.last_open() is None
    assert ws.cart_broken(ws.cart) is False


def test_a_game_is_not_guarded(tmp_path):
    """Two writes per open is not free, and a game that always crashes shows
    the panel and is not a brick. The guard is for content the shell runs on
    the kid's behalf."""
    ws = build_ws(tmp_path)
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
    ws = build_ws(tmp_path)
    for i in range(3):
        ws.go_home()
        _open(ws, "Boomy")
        assert ws.run.player.cart_error is not None, i
        assert "always broken" in ws.run.player.cart_error, ws.run.player.cart_error
        assert ws.app_guard.strikes("Boomy") == i + 1
    boomy = next(c for c in ws.carts.all if c["title"] == "Boomy")
    assert ws.cart_broken(boomy) is True
    ws.go_home()
    _open(ws, "Boomy")
    assert ws.run.player.cart_error is not None
    assert "turned off" in ws.run.player.cart_error, ws.run.player.cart_error
    assert "always broken" not in ws.run.player.cart_error, "the cart RAN again"


def test_the_strikes_survive_a_reboot(tmp_path):
    """The strike lives in `system/settings`, which is the whole point: an
    in-process counter cannot outlive the hang it is counting."""
    carts = str(tmp_path / "carts")
    _write_cart(carts, "Boomy", BOOM_SRC)
    for _ in range(3):
        ws = build_ws(tmp_path)                 # a fresh boot each time
        _open(ws, "Boomy")
        assert ws.run.player.cart_error is not None
    ws = build_ws(tmp_path)
    assert ws.app_guard.strikes("Boomy") == 3
    boomy = next(c for c in ws.carts.all if c["title"] == "Boomy")
    assert ws.cart_broken(boomy) is True


def test_a_broken_app_stays_editable_on_make(tmp_path):
    """Editing it is how it gets fixed, so Make must still offer it --
    which also means the temporary #181 app-cart hide must not catch it."""
    carts = str(tmp_path / "carts")
    _write_cart(carts, "Boomy", BOOM_SRC)
    ws = build_ws(tmp_path)
    for _ in range(3):
        ws.go_home()
        _open(ws, "Boomy")
    ws.go_home()
    titles = [it.get("title") for it in ws._make_items(ws.carts.all)]
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
    hand-editing `system/settings`, i.e. the panel's one instruction was a lie.
    A COMMITTED code change is the hook (`Project.commit_code` ->
    `ws.forgive_app`), because code is the only edit that can change whether
    the cart hangs, faults or eats the heap."""
    carts = str(tmp_path / "carts")
    _write_cart(carts, "Boomy", BOOM_SRC)
    ws = build_ws(tmp_path)
    for _ in range(3):
        ws.go_home()
        _open(ws, "Boomy")
    boomy = next(c for c in ws.carts.all if c["title"] == "Boomy")
    assert ws.cart_broken(boomy) is True

    # The fourth open is refused -- and lands on the panel whose bar edits it.
    ws.go_home()
    _open(ws, "Boomy")
    assert "turned off" in ws.run.player.cart_error, ws.run.player.cart_error

    # The kid fixes the code and it commits.
    ws.set_menu_view("code")
    ws.screen = "menu"
    ws.editor.set_text(FIXED_SRC)
    assert ws.save_code() is True
    assert ws.app_guard.strikes("Boomy") == 0
    assert ws.cart_broken(boomy) is False

    # ...and the app opens and runs again, with a full three strikes back.
    ws.go_home()
    _open(ws, "Boomy")
    _frames(ws, CrashGuard.HEAL_FRAMES + 1)
    assert ws.run.player.cart_error is None, ws.run.player.cart_error


# ---------------------------------------------------------------------------
# a cart that arrived as files is asked on its first run
# ---------------------------------------------------------------------------

COPIED_SRC = "said = []\ndef _init():\n    said.append('ran')\n\ndef _draw():\n    cls(1)\n"


@pytest.mark.first_run
def test_a_cart_that_arrived_as_files_is_asked_before_its_first_run(tmp_path):
    """A cart copied onto the card has no consent record: the home's launch
    asks with the install prompt's lines, nothing of it runs and nothing is
    recorded until Open, and Open records what it asks for, as an install
    does, and runs it. The next launch asks nothing."""
    carts = str(tmp_path / "carts")
    _write_cart(carts, "Copied", COPIED_SRC, perms=["prefs"])
    ws = build_ws(tmp_path)
    cart = select_on_home(ws, "Copied")
    aid = app_perms.app_id(cart)
    ws.open()
    assert ws.confirm.open and ws.confirm.title == "Open Copied?"
    assert ws.confirm.buttons == ("No", "Open")
    assert ws.confirm.lines == ["Keep its own settings", app_perms.TRUSTED]
    assert (ws.cart or {}).get("title") != "Copied"
    assert ws.consent.get(aid) is None
    act = ws.confirm._act
    ws.confirm.cancel()
    act()
    assert ws.consent.get(aid) == {"perms": ["prefs"], "off": []}
    assert ws.cart["title"] == "Copied" and ws.run.player.cart_error is None
    assert "prefs" in ws.run.player.granted
    assert not app_perms.asks_first(cart, ws.consent.get(aid))


@pytest.mark.first_run
def test_a_refused_first_run_runs_nothing_and_asks_again(tmp_path):
    """No leaves the cart unrun and records nothing; every other way in
    (a launch that does not ask) is refused by name until a yes."""
    carts = str(tmp_path / "carts")
    _write_cart(carts, "Copied", COPIED_SRC, perms=["prefs"])
    ws = build_ws(tmp_path)
    cart = select_on_home(ws, "Copied")
    ws.open()
    ws.confirm.cancel()
    assert ws.consent.get(app_perms.app_id(cart)) is None
    ws._open_workspace(cart)
    assert ws.run.player.cart_error == app_perms.UNASKED_REFUSAL
    assert not app_perms.run_admits_unsigned(ws, cart)
    select_on_home(ws, "Copied")
    ws.open()
    assert ws.confirm.open and ws.confirm.title == "Open Copied?"


@pytest.mark.first_run
def test_a_built_in_and_a_made_cart_are_never_asked(tmp_path):
    """A built-in (known by its seed) and a cart made on the console run
    with no question; a copy of a cart still to be asked about is asked."""
    ws = build_ws(tmp_path)
    notes = next(c for c in ws.carts.all if c["path"].endswith("moybyte.notes.moy"))
    assert ws.consent.get(app_perms.app_id(notes)) is None
    assert not app_perms.asks_first(notes, None)
    made = ws.carts.new()
    assert made is not None
    assert ws.consent.get(app_perms.app_id(made)) == {"perms": None, "off": []}
    assert not app_perms.asks_first(made, ws.consent.get(app_perms.app_id(made)))


@pytest.mark.first_run
def test_a_pages_site_carts_are_its_own_and_a_dropped_cart_is_asked(tmp_path):
    """The browser's console: the carts the seed found in the bundle the site
    served are seeded, recorded as made at boot (web_boot's
    `_site_carts_made`), so they run unasked; a cart dropped onto the page
    arrived as files and is asked. A page served by a board records
    nothing."""
    import os
    import sys
    root = Path(__file__).resolve().parent.parent
    for p in ("firmware/web_runner", "device", "runtime"):
        if str(root / p) not in sys.path:
            sys.path.insert(0, str(root / p))
    try:
        import web_boot
    except Exception as exc:  # noqa: BLE001 -- a wasm-only dep would skip, not fail
        pytest.skip("web_boot not importable on the host: %s" % exc)
    from runtime import moy_sync
    carts = str(tmp_path / "carts")
    _write_cart(carts, "Served", COPIED_SRC, perms=["prefs"])
    _write_cart(carts, "Dropped", COPIED_SRC, perms=["prefs"])
    ws = build_ws(tmp_path)
    served = next(c for c in ws.carts.all if c.get("title") == "Served")
    dropped = next(c for c in ws.carts.all if c.get("title") == "Dropped")
    assert os.path.basename(served["path"]) == "Served.moy"
    saved = (dict(web_boot._S), moy_sync._SERVED[0])
    try:
        moy_sync._SERVED[0] = ("Served.moy",)
        web_boot._S["store_mode"] = "board"
        web_boot._site_carts_made(ws)
        assert ws.consent.for_run(served) is app_perms.UNASKED
        web_boot._S["store_mode"] = "site"
        web_boot._site_carts_made(ws)
        assert ws.consent.get(app_perms.app_id(served)) == {"perms": None, "off": []}
        assert ws.consent.for_run(dropped) is app_perms.UNASKED
        # No seed ran (a store with no OPFS took the bundle whole): every cart
        # on the shelf is the site's.
        moy_sync._SERVED[0] = None
        web_boot._S["store_mode"] = "none"
        web_boot._site_carts_made(ws)
        assert ws.consent.for_run(dropped) is not app_perms.UNASKED
    finally:
        web_boot._S.clear()
        web_boot._S.update(saved[0])
        moy_sync._SERVED[0] = saved[1]
