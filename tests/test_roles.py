"""The app ABI's role table (the role rows of `native/os_calls.json`, #224
sprint 5) and the nets held to it.

The table is one row per verb an app reaches through its `AppContext`: the
role, the verb, where it is served, the permission that grants it to a user
app, and its wasm import type. It is to the app ABI what moy-spec's
`wasm-imports.json` is to the cart verbs, and these tests keep everything that
restates it equal to it:

- `app_context.ROLES` is the table's roles, in its order;
- every role object `AppContext` builds answers exactly the table's verbs, and
  every shell row is a method of the server the console registers for it;
- a `proc:<name>` row is the process's (system_carts/moybyte.<name>.moy): a
  message it wakes on, or a function of its main, and a method of the link the
  console registers for the role;
- the grant column is the C policy's permission map (`moy_app.perms()`);
- the C rows are native/moy_app's, in the table's order, and its role
  objects answer exactly them;
- `docs/app_api_v1.md`'s role table lists exactly the rows;
- each shipped app's drawn frame calls each verb no more than its budget.

The roles' semantic trace (`tests/test_semantic_traces.py`) drives every row
over a real store on CPython and on the T-Deck's own desktop; its "uncalled"
line is the coverage ratchet.
"""

import re
from pathlib import Path

import pytest

from ws_helpers import build_ws

from runtime import app_context as _ac
from tools import gen_os_table
import moy_app

ROOT = Path(__file__).resolve().parent.parent
SERVERS = ("shell", "c", "call")
# A kernel or Python row a compiled app imports is the door's too.
DOOR_ONLY = ("kernel", "python")
PROC = "proc:"


def _rows():
    return gen_os_table.role_table()


def _verbs():
    """Each role's verbs but those of the roles no AppContext object serves:
    the toolkit's (app_context.TOOLKIT; tests/test_ui_wasm.py drives its
    rows) and the console calls' (shell_servers.ConsoleCalls)."""
    out = {}
    for r in _rows():
        if r["role"] not in _ac.ROLES:
            continue
        out.setdefault(r["role"], []).append(r["verb"])
    return out


def test_the_table_is_well_formed():
    seen = set()
    for r in _rows():
        assert set(r) == {"role", "verb", "server", "grant", "wasm"}, r
        key = (r["role"], r["verb"])
        assert key not in seen, "row twice: %s.%s" % key
        seen.add(key)
        assert r["server"] in SERVERS or r["server"].startswith(PROC) or (
            r["server"] in DOOR_ONLY and r["wasm"] is not None), r
        assert r["wasm"] is None or isinstance(r["wasm"], str), r


def test_roles_is_the_tables_roles_in_order():
    assert tuple(_verbs()) == _ac.ROLES


# What AppContext builds for each role: native/moy_app's role type, every one.
_CLASSES = {"damage": moy_app.Damage, "surface": moy_app.Surface,
            "theme": moy_app.Theme, "files": moy_app.Files,
            "carts": moy_app.Carts, "nav": moy_app.Nav, "prefs": moy_app.Prefs,
            "notify": moy_app.Notify, "wallpaper": moy_app.Wallpaper,
            "artwork": moy_app.Artwork, "clipboard": moy_app.Clipboard,
            "install": moy_app.Install}


def test_every_role_has_its_type():
    assert set(_CLASSES) == set(_ac.ROLES)


def _public(role, cls):
    return {n for n in dir(cls) if not n.startswith("_")
            and callable(getattr(cls, n))}


@pytest.mark.parametrize("role", sorted(_CLASSES))
def test_each_role_object_answers_exactly_its_rows(role):
    verbs = set(_verbs()[role])
    have = _public(role, _CLASSES[role])
    assert have - verbs == set(), "%s verbs with no row: %s" % (role, sorted(have - verbs))
    assert verbs - have == set(), "%s rows with no verb: %s" % (role, sorted(verbs - have))


def test_the_c_rows_are_native_moy_apps_in_the_tables_order():
    c_rows = [r["role"] + "." + r["verb"] for r in _rows() if r["server"] == "c"]
    assert c_rows == list(moy_app.rows())
    assert tuple(moy_app.roles()) == tuple(gen_os_table.roles(gen_os_table.load()))
    assert _ac.TOOLKIT in moy_app.roles()


def test_the_import_adapter_is_the_tables_wasm_column():
    """A row with a wasm type is one native of native/moy_app/moy_app_wasm.c,
    imported from module moybyte.app as <role>_<verb> at that type, in the
    table's order; a row without one has no native."""
    want = [(r["role"] + "_" + r["verb"], r["wasm"], i)
            for i, r in enumerate(_rows()) if r["wasm"] is not None]
    assert list(moy_app.wasm_rows()) == want


def test_the_ui_fixture_imports_every_ui_row():
    """tests/fixtures/wasm/app_ui.moy imports every `ui` row, at the table's
    type: a step that adds a widget adds its row, and the fixture imports it
    (docs/ui_api_2026-10.md §8)."""
    src = (ROOT / "tests" / "fixtures" / "wasm" / "app_ui.moy" / "src" / "main.wat").read_text()
    want = {"ui_" + r["verb"]: r["wasm"] for r in _rows() if r["role"] == _ac.TOOLKIT}
    got = dict(re.findall(r'\(import "moybyte\.app" "(ui_\w+)" \(func \$\w+\s*'
                          r'((?:\(param[^)]*\)\s*)?(?:\(result[^)]*\))?)', src))
    assert set(got) == set(want)
    for name, sig in want.items():
        params = sig[1:sig.index(")")].replace("*~", "ii")
        decl = got[name]
        m = re.search(r"\(param ([^)]*)\)", decl)
        assert len(m.group(1).split()) if m else 0 == len(params), name
        assert ("(result i32)" in decl) == sig.endswith("i"), name


def test_the_role_doors_table_is_the_tables_rows():
    """The ROLE door names a row by its index here (moy_app_role), so the C's
    table is the source's row for row."""
    assert list(moy_app.table()) == [r["role"] + "." + r["verb"] for r in _rows()]


def test_the_doors_shell_rows_take_their_servers_arguments():
    """Each row the door decodes in Python (shell_servers.DOOR_ARGS) is a shell
    row, and its fields are its server's arguments after the grant: one each,
    an optional one where it defaults."""
    import inspect
    from runtime import shell_servers as ss
    rows = {r["role"] + "." + r["verb"]: r for r in _rows()}
    from runtime import achievements
    servers = {"theme": ss.ThemeServer, "nav": ss.NavServer,
               "notify": achievements.NotifyRole, "wallpaper": ss.WallpaperServer,
               "moybyte": ss.ConsoleCalls}
    for name, kinds in ss.DOOR_ARGS.items():
        r = rows[name]
        assert r["server"] in ("shell", "call") + DOOR_ONLY or r["server"].startswith(PROC), name
        role, verb = name.split(".")
        params = list(inspect.signature(getattr(servers[role], verb)).parameters.values())[2:]
        assert len(params) >= len(kinds), (name, kinds)
        for p, k in zip(params, kinds):
            assert (p.default is not inspect.Parameter.empty) == (k == "o"), (name, p.name, k)
        assert all(p.default is not inspect.Parameter.empty for p in params[len(kinds):]), name


def test_the_console_calls_are_the_door_rows_of_the_moybyte_role():
    """Each `moybyte` row the door decodes is a method of the door's
    console-call server, which runs the `moybyte` module's function of the
    same name where a Python program holds one (the rest are a compiled
    app's own: its queue's read, its granted items' and its device
    handles', runtime/system/dev_handles.py, and its status item's, a
    Python program's being `moybyte.status`); a method no row
    names is a call nothing can reach."""
    from runtime import shell_servers as ss
    from runtime import moybyte
    rows = [r for r in _rows() if r["server"] == "call"]
    assert all(r["role"] == "moybyte" for r in rows)
    door = {n.split(".")[1] for n in ss.DOOR_ARGS if n.startswith("moybyte.")}
    have = {n for n in dir(ss.ConsoleCalls) if not n.startswith("_")}
    assert have == door
    py = {r["name"][8:] for r in gen_os_table.load()["rows"]
          if r["name"] in ss.DOOR_ARGS and r["name"] in (r.get("py") or ())}
    assert py == door - {"recv", "item_read", "item_write"} - {
        v for v in door if v.startswith("dev_") or v.startswith("status_")}
    assert all(callable(getattr(moybyte, v)) for v in py)


def test_the_shell_rows_are_the_servers_methods():
    """Each shell row is a method of the server the console registers for its
    role (runtime/shell/shell_servers.py), taking the grant first; a server method
    that is no row is a verb no app can reach."""
    import inspect
    from runtime import shell_servers as ss
    servers = {"theme": ss.ThemeServer,
               "carts": ss.CartsServer,
               "nav": ss.NavServer,
               "wallpaper": ss.WallpaperServer, "install": ss.InstallServer}
    shell = {}
    for r in _rows():
        if r["server"] == "shell":
            shell.setdefault(r["role"], set()).add(r["verb"])
    assert set(shell) == set(servers)
    for role, cls in servers.items():
        have = {n for n in dir(cls) if not n.startswith("_")
                and callable(getattr(cls, n))}
        assert have == shell[role], (role, sorted(have ^ shell[role]))
        for v in have:
            params = list(inspect.signature(getattr(cls, v)).parameters)
            assert params[:2] == ["self", "g"], (role, v, params)


def _proc_rows():
    out = {}
    for r in _rows():
        if r["server"].startswith(PROC):
            out.setdefault(r["server"][len(PROC):], []).append(r)
    return out


def test_the_achievements_process_serves_the_notify_role():
    """The notify role's rows moved to the achievements process
    (docs/os_2026-11/processes.md section 8): every one of them, and none
    else, is `proc:achievements`, and the grant stays `never`."""
    procs = _proc_rows()
    assert set(procs) == {"achievements"}
    assert [r["verb"] for r in procs["achievements"]] == ["award", "groups", "today"]
    assert all(r["role"] == "notify" and r["grant"] == "never" and r["wasm"] is None
               for r in procs["achievements"])


def test_the_proc_rows_are_the_processs_and_the_links():
    """Each `proc:<name>` row is a method of the role's link, taking the
    grant first (the link is what the console registers,
    runtime/system/achievements.py): a message the process wakes on
    (`moybyte.on(verb, fn)`), or a read of what the process keeps."""
    import ast
    import inspect
    from runtime import achievements
    links = {"achievements": achievements.NotifyRole}
    messages = {"achievements": {"award", "progress"}}
    for name, rows in _proc_rows().items():
        src = (ROOT / "system_carts" / ("moybyte.%s.moy" % name) / "main.py").read_text()
        tree = ast.parse(src)
        ons = {c.args[0].value for c in ast.walk(tree)
               if isinstance(c, ast.Call) and getattr(c.func, "attr", None) == "on"
               and isinstance(c.args[0], ast.Constant)}
        assert ons == messages[name]
        verbs = {r["verb"] for r in rows}
        cls = links[name]
        have = {n for n in dir(cls) if not n.startswith("_") and callable(getattr(cls, n))}
        assert have == verbs, sorted(have ^ verbs)
        for v in have:
            assert list(inspect.signature(getattr(cls, v)).parameters)[:2] == ["self", "g"]


# The roles every grant holds (moy_app.h's MOY_ROLE_FLOOR): no permission
# grants them, and the C's holding check passes them for every grant.
_FLOOR = {x["role"]: "floor" for x in gen_os_table.load()["roles"] if x["grant"] == "floor"}


def test_the_grant_column_is_the_permission_map():
    perm_for = {}
    for perm, role in moy_app.perms():
        perm_for[role] = perm
    for r in _rows():
        want = perm_for.get(r["role"], _FLOOR.get(r["role"], "never"))
        assert r["grant"] == want, "%s.%s grant %r, the map says %r" % (
            r["role"], r["verb"], r["grant"], want)


def test_the_grant_column_is_the_permission_tables():
    """A role a manifest permission grants is the permission table's
    `role:<role>` row (runtime/shell/app_perms.py): the install prompt names
    exactly the grants the policy can make."""
    by_role = {}
    for r in gen_os_table.load()["permissions"]:
        if r["grants"].startswith("role:"):
            by_role[r["grants"][5:]] = r["perm"]
    for r in _rows():
        assert r["grant"] == by_role.get(r["role"], _FLOOR.get(r["role"], "never")), (
            r["role"], r["verb"])


def _doc_table():
    text = (ROOT / "docs" / "app_api_v1.md").read_text()
    out = {}
    for line in text.splitlines():
        m = re.match(r"\| `ctx\.([a-z]+)` \|.*\| (.*) \|$", line)
        if m:
            out[m.group(1)] = [v.rstrip("()") for v in re.findall(r"`([a-z_]+(?:\(\))?)`", m.group(2))]
    return out


def test_the_doc_table_lists_exactly_the_rows():
    doc = _doc_table()
    verbs = _verbs()
    assert list(doc) == list(verbs), "the doc's roles are not the table's"
    for role, vs in verbs.items():
        assert doc[role] == vs, "docs/app_api_v1.md's %s verbs drift: %s vs %s" % (
            role, doc[role], vs)


# -- per-frame budgets ---------------------------------------------------------
#
# Each shipped app's drawn frame, as `ws.frame` paints it with the app on top,
# and the most calls of each row it makes per frame. The caps are the MEASURED
# counts with no slack, so a role read added inside a per-widget helper fails
# here instead of costing a call per widget on glass. A row absent from an
# app's budget is never called by its frame. `surface.canvas` is read at least
# once per frame by every app: the lower bound that keeps the counter honest.

BUDGETS = {
    "artwork": {"surface.canvas": 1, "surface.pointer": 1, "theme.colors": 25},
    "appearance": {"surface.canvas": 1, "theme.colors": 3, "theme.gen": 1,
                   "wallpaper.preview": 1, "wallpaper.title": 1},
    "storybook": {"surface.canvas": 1, "theme.colors": 2, "carts.all": 1},
    "files": {"surface.canvas": 1, "theme.colors": 6},
    "calc": {"surface.canvas": 1, "theme.colors": 1, "theme.light": 1},
    "getcarts": {"surface.canvas": 1, "theme.colors": 1},
}


def _count_rows(ws, app):
    """Wrap every row's verb on the role objects the app's context holds (the
    shared artwork and clipboard objects included), on the instances."""
    counts = {}
    ctx = app.ctx
    for r in _rows():
        if r["role"] == "ui":                # the toolkit's rows: no role object
            continue
        obj = getattr(ctx, r["role"], None)
        if obj is None:
            continue
        real = getattr(obj, r["verb"])
        key = r["role"] + "." + r["verb"]

        def counted(*a, _real=real, _key=key, **kw):
            counts[_key] = counts.get(_key, 0) + 1
            return _real(*a, **kw)

        setattr(obj, r["verb"], counted)
    return counts


@pytest.mark.parametrize("kind", sorted(BUDGETS))
def test_each_apps_drawn_frame_keeps_its_role_budget(tmp_path, kind):
    ws = build_ws(tmp_path)
    app = ws._apps_by_id[kind]
    assert ws.open_app(app), kind + " has no identity cart"
    for _ in range(3):                       # the open's own settling frames
        ws._dirty = True
        ws.frame(1 / 30.0)
    counts = _count_rows(ws, app)
    frames = 4
    for _ in range(frames):
        ws._dirty = True
        ws.frame(1 / 30.0)
    assert counts.get("surface.canvas", 0) >= frames, \
        "no canvas() read counted in %d frames: is the counter on the app?" % frames
    per = {k: -(-v // frames) for k, v in counts.items()}
    over = {k: (v, BUDGETS[kind].get(k, 0)) for k, v in per.items()
            if v > BUDGETS[kind].get(k, 0)}
    assert not over, "%s's frame exceeds its role budget (per frame, cap): %s" % (
        kind, over)


# -- the rows the shell writes -------------------------------------------------
#
# surface's and theme's C rows answer what the shell WROTE (the grant's surface
# row, the look's token table), not what it shows now, so every change the
# shell makes has to reach them: a writer missed is an app laid out for a
# canvas, a scale or a window it is no longer in. These drive the changes the
# shell makes -- every app opened and drawn, a font-scale step, a theme
# switch, two desk windows of two sizes, a maximize, taps inside a window --
# and fail on any read whose row is not what the shell shows at that moment.

def _checked_rows(monkeypatch, ws):
    bad = []
    S = moy_app.Surface

    def wrap(name, live, same=lambda a, b: a == b):
        real = getattr(S, name)

        def checked(self, *a, **kw):
            v = real(self, *a, **kw)
            if self._app is ws.app_abi:
                want = live()
                if not same(v, want):
                    bad.append((name, v, want))
            return v

        monkeypatch.setattr(S, name, checked)

    wrap("canvas", lambda: ws.sys_canvas, lambda a, b: a is b)
    wrap("size", lambda: (ws.sys_canvas.w, ws.sys_canvas.h))
    wrap("font_scale", lambda: ws.look.effective_font_scale())
    wrap("chrome_scale", lambda: ws.look.effective_chrome_scale())
    wrap("windowed", lambda: ws.windowed_chrome)
    wrap("bar_h", lambda: ws.app_bar_h())
    wrap("pointer", lambda: ws.pointer,
         lambda a, p: (a is None) == (p is None) and (
             a is None or (a.down, a.click, a.visible)
             == (bool(p.down), bool(p.click), bool(p.visible))))
    T = moy_app.Theme
    real_colors = T.colors

    def colors(self):
        v = real_colors(self)
        if self._app is ws.app_abi and v != ws.theme_colors:
            bad.append(("colors", ws.look.theme_name))
        return v

    monkeypatch.setattr(T, "colors", colors)
    return bad


def test_the_surface_and_theme_rows_follow_the_fullscreen_shell(tmp_path, monkeypatch):
    ws = build_ws(tmp_path)
    bad = _checked_rows(monkeypatch, ws)
    for kind in sorted(BUDGETS):
        assert ws.open_app(ws._apps_by_id[kind]), kind
        for _ in range(3):
            ws._dirty = True
            ws.frame(1 / 30.0)
        ws.look.set_theme("berry", variant="light")
        ws.look.cycle_font_scale(1)
        for _ in range(2):
            ws._dirty = True
            ws.frame(1 / 30.0)
        ws.look.set_theme("night", variant="dark")
        ws.exit()
    assert not bad, bad[:5]


def test_the_surface_and_theme_rows_follow_the_desk(tmp_path, monkeypatch):
    from ws_helpers import build_desktop_ws
    from runtime import host_app
    ws = build_desktop_ws(tmp_path)
    drv = host_app.ConsoleDriver(ws)
    bad = _checked_rows(monkeypatch, ws)
    ws.open_desk()
    drv.frame(1 / 30)
    ws.open_app(ws._apps_by_id["calc"])
    for _ in range(4):
        drv.frame(1 / 30)
    calc = ws.wm._wins["calc"]
    ws.wm._resize_window(calc, 360, 300)
    ws.open_app(ws._apps_by_id["files"])
    for _ in range(4):
        drv.frame(1 / 30)
    files = ws.wm._wins["files"]
    assert (calc.w, calc.h) != (files.w, files.h)
    ws.open_app(ws._apps_by_id["calc"])
    for _ in range(3):
        drv.frame(1 / 30)
    cx, cy, cw, ch = calc.content_rect()
    drv.touch(cx + cw // 2, cy + ch // 2)
    drv.frame(1 / 30)
    drv.touch_up()
    drv.frame(1 / 30)
    ws.wm._toggle_max(files)
    ws.look.cycle_font_scale(1)
    ws.look.set_theme("forest")
    for _ in range(4):
        ws._dirty = True
        drv.frame(1 / 30)
    assert not bad, bad[:5]


def test_the_token_vocabulary_is_every_themes_keys():
    """The live token table holds a theme as role ids (native/moy_app's
    vocabulary); a token a theme sets with no role id would be refused by the
    look's write, so the vocabulary is exactly the keys every theme and variant
    resolves to."""
    from runtime import chrome
    keys = set()
    for name in chrome.THEME_NAMES:
        for variant in chrome.THEME_VARIANTS:
            keys |= set(chrome.theme_colors(name, variant))
    assert set(moy_app.tokens()) == keys
    assert len(moy_app.tokens()) == len(keys)
