"""The permission table and the install prompt (docs/os_2026-11/processes.md
§2, runtime/shell/app_perms.py): one table, `native/os_calls.json`'s
"permissions", that the prompt, the install check and the launch check read;
consent recorded at install, all or nothing; a permission turned off in
Settings absent (`None`) from the next launch, and a `required` one refusing
it; a cart written in the Editor never asked."""

import glob
import json
import os
import re
from pathlib import Path

import pytest

from cart_store_fixtures import MemNet, Repo
from runtime import app_perms as P
from tools import gen_os_table
from runtime import cart_index as ci
from runtime import host_app
from ws_helpers import build_ws
import moy_app

ROOT = Path(__file__).resolve().parent.parent
DT = 1.0 / 30


def _src():
    return gen_os_table.load()


# -- the one table ---------------------------------------------------------------

def test_the_python_table_is_the_source_row_for_row():
    src = _src()
    rows = src["permissions"]
    assert len(rows) == len(P.TABLE)
    for j, r in zip(rows, P.TABLE):
        assert (j["perm"], j["grants"], gen_os_table.perm_tiers(j, src), j["prompt"],
                j["needs"], tuple(j["py"])) == r


def test_a_permission_s_tiers_are_the_tiers_that_serve_what_it_grants():
    """No install prompt lists what the cart's tier cannot do: a permission
    with a line is held on a tier only where a call it hands an app is served
    there (the source's rows, by their tiers' names for them), or moy-spec's
    verbs serve it; what no call serves (a native module's run, a service's
    start) states its tiers, and nothing else may."""
    src = _src()
    served = {}
    for r in src["rows"]:
        for perm in gen_os_table.perms_of(r, src):
            served.setdefault(perm, set()).update(gen_os_table.row_tiers(r, src))
    for p in src["permissions"]:
        tiers = set(next(r for r in P.TABLE if r[P.PERM] == p["perm"])[P.TIERS])
        kind = p["grants"].split(":")[0]
        if kind in ("floor", "marker"):
            continue
        if kind in ("full_access", "boot"):
            assert tiers == set(p["tiers"]), p["perm"]
            continue
        want = served.get(p["perm"], set()) | (set(gen_os_table.SPEC_TIERS)
                                               if p.get("spec") else set())
        assert tiers == want, (p["perm"], sorted(tiers), sorted(want))
        assert "tiers" not in p, p["perm"]


def test_a_tier_that_serves_no_row_of_a_permission_is_not_asked_for_it():
    """A Lua cart asking for Wi-Fi (no Lua name serves it) is not prompted
    for it, and holds it absent; a Python cart is."""
    lua = {"runtime": "lua", "permissions": ["network", "prefs"]}
    assert P.prompt(lua) == ["Keep its own settings"]
    assert "network" not in P.check(lua, None, ())[0]
    assert P.prompt({"runtime": "python", "permissions": ["network"]})[0] == \
        "Use Wi-Fi and the internet"


def test_the_table_is_well_formed():
    seen = set()
    for perm, grants, tiers, prompt, needs, py in P.TABLE:
        assert perm not in seen, perm
        seen.add(perm)
        kind = grants.split(":")[0]
        assert kind in ("floor", "role", "api", "boot", "lease", "full_access", "marker"), grants
        assert tiers and set(tiers) <= set(P.ALL), tiers
        assert needs in (None, "wifi", "net", "gpio", "lease", "mic", "lora", "ble", "usb", "midi", "camera",
                         "media"), needs
        assert (kind == "lease") == (prompt == "full") == (needs == "lease"), perm
        # a line is what can be consented to and turned off: never the floor
        # or a marker
        assert (prompt is None) == (kind in ("floor", "marker")) or kind == "api", perm


def test_the_role_rows_are_the_c_allowlist():
    """`role:` rows are native/moy_app's permission map, both ways: a role
    the table grants is one the C policy grants, and nothing else."""
    table = {r[P.PERM]: r[P.GRANTS][5:] for r in P.TABLE if r[P.GRANTS].startswith("role:")}
    assert table == dict(moy_app.perms())


def test_every_permission_the_console_reads_is_a_row():
    """A name a seed cart's manifest lists, or the console tests a cart for,
    is the table's: a new gate without a row is a prompt that cannot name it."""
    named = set()
    for f in glob.glob(str(ROOT / "system_carts" / "*" / "manifest.json")):
        named |= set((json.load(open(f)).get("moybyte") or {}).get("permissions") or ())
    for f in glob.glob(str(ROOT / "runtime" / "**" / "*.py"), recursive=True):
        named |= set(re.findall(r'_cart_has_perm\("([a-z_:]+)"\)', open(f).read()))
    missing = sorted(p for p in named if P.row(p) is None)
    assert not missing, missing


def test_files_kinds_share_the_files_row_and_name_their_kind():
    assert P.row("files:music") is P.row("files")
    assert P.line("files:music") == "Read and save your music"
    assert P.line("files") == "Read and save your documents"
    assert P.line("files:pictures") == "Read and save your pictures"
    assert P.line("files") == "Read and save your documents"
    assert P.line("files:downloads") is None and P.line("files:docs") is None
    assert P.line("graphics") is None and P.line("editor") is None


# -- the prompt ------------------------------------------------------------------

def test_the_prompt_is_the_tables_lines_in_the_manifests_order():
    cart = {"runtime": "lua", "permissions": ["graphics", "pins", "prefs", "pins"]}
    assert P.prompt(cart) == ["Switch the console's pins", "Keep its own settings"]


def test_full_access_says_the_board_s_line(monkeypatch):
    """`full_access` says the table's line on every board, and a board's own
    line where FULL_ACCESS_LINES names one (the P4s', whose native modules
    run in U-mode: docs/os_2026-11/tiers.md section 1), which says that
    unsigned code, an AOT module's, is not confined."""
    cart = {"id": "a.b", "native": ["esp32p4"], "permissions": ["full_access"]}
    monkeypatch.setattr(P, "_board", lambda: None)
    assert P.prompt(cart) == ["Full access to the console"]
    p4 = "Run its own machine code. Unsigned code has full access to the console"
    monkeypatch.setattr(P, "_board", lambda: "p4")
    assert P.prompt(cart) == [p4]
    monkeypatch.setattr(P, "_board", lambda: "guition_p4")
    assert P.prompt(cart) == [p4]
    monkeypatch.setattr(P, "_board", lambda: "tdeck")
    assert P.prompt(cart) == ["Full access to the console"]


def test_a_cart_that_asks_for_no_more_than_a_game_is_not_asked_about():
    assert P.prompt({"runtime": "lua", "permissions": ["graphics", "input", "audio"]}) is None


def test_a_python_app_says_it_is_trusted():
    lines = P.prompt({"runtime": "python", "permissions": ["graphics", "prefs"]})
    assert lines == ["Keep its own settings", P.TRUSTED]
    assert P.prompt({"permissions": []}) == [P.TRUSTED]


def test_an_update_is_asked_about_only_for_what_it_adds():
    rec = {"perms": ["graphics", "prefs"], "off": []}
    assert P.prompt({"runtime": "lua", "permissions": ["graphics", "prefs"]}, rec) is None
    assert P.prompt({"runtime": "lua", "permissions": ["prefs", "pins"]}, rec) == [
        "Keep its own settings", "Switch the console's pins"]


# -- the checks ------------------------------------------------------------------

def test_a_turned_off_permission_is_absent_and_a_required_one_refuses():
    cart = {"runtime": "python", "permissions": ["prefs", "network"], "required": []}
    rec = {"perms": ["prefs", "network"], "off": ["prefs"]}
    assert P.check(cart, rec, ("wifi",)) == (["network"], ["prefs"], None)
    cart["required"] = ["prefs"]
    g, a, why = P.check(cart, rec, ("wifi",))
    assert a == ["prefs"] and why == 'needs "prefs" - turned off in Settings'


def test_what_the_console_lacks_is_absent_and_refuses_only_when_required():
    cart = {"runtime": "lua", "permissions": ["pins"], "required": []}
    assert P.check(cart, None, ()) == ([], ["pins"], None)
    cart["required"] = ["pins"]
    assert P.check(cart, None, ())[2] == (
        'needs "pins" - the %s has none' % (P.board_words() or "console"))
    assert P.check(cart, None, ("gpio",)) == (["pins"], [], None)


def test_an_added_permission_nobody_consented_to_is_absent():
    cart = {"runtime": "lua", "permissions": ["prefs", "pins"], "required": []}
    rec = {"perms": ["prefs"], "off": []}
    assert P.check(cart, rec, ("gpio",)) == (["prefs"], ["pins"], None)


def test_a_required_name_the_manifest_does_not_list_refuses():
    cart = {"permissions": ["prefs"], "required": ["network"]}
    assert P.check(cart, None, ("wifi",))[2] == 'requires "network" but does not ask for it'


def test_a_tier_a_row_excludes_is_absent():
    cart = {"runtime": "lua", "permissions": ["editor"], "required": ["editor"]}
    assert P.check(cart, None, ())[2] == 'needs "editor" - not for a lua cart'


def test_a_files_folder_the_console_has_none_of_refuses_by_name():
    """A cart whose `files:<folder>` names no folder would run with no
    `files` at all and stop on its first use with a NameError: the launch
    names it instead, required or not (a seed kept from before its folder was
    renamed: Notes' `files:docs`)."""
    cart = {"runtime": "python", "permissions": ["files:docs", "prefs"], "required": []}
    g, a, why = P.check(cart, None, ())
    assert g == ["prefs"] and a == ["files:docs"]
    assert why == 'needs "files:docs" - this console has no such folder'
    cart["permissions"] = ["files:documents", "prefs"]
    assert P.check(cart, None, ()) == (["files:documents", "prefs"], [], None)


class _Store(dict):
    def set(self, k, v):
        self[k] = json.loads(json.dumps(v))


def test_consent_records_turns_and_forgets():
    store = _Store()
    c = P.Consent(store)
    assert c.get("a.b") is None and c.is_on("a.b", "prefs")
    c.record("a.b", ["graphics", "prefs"])
    assert store["consent"] == {"a.b": {"perms": ["graphics", "prefs"], "off": []}}
    c.turn("a.b", "prefs", False)
    assert not c.is_on("a.b", "prefs")
    c.record("a.b", ["graphics", "prefs", "network"])    # an update keeps it off
    assert c.get("a.b")["off"] == ["prefs"]
    c.turn("a.b", "prefs", True)
    assert c.is_on("a.b", "prefs")
    c.turn("mine", "network", False)                     # an Editor cart's switch
    assert c.get("mine") == {"perms": None, "off": ["network"]}
    c.forget("a.b")
    assert c.get("a.b") is None and c.ids() == ["mine"]


@pytest.mark.parametrize("bad", ["x", 3, {"perms": "prefs"}, {"perms": [1]},
                                 {"perms": [], "off": "prefs"}])
def test_a_malformed_record_reads_as_none(bad):
    c = P.Consent(_Store(consent={"a": bad}))
    assert c.get("a") is None and c.ids() == []


# -- the launch, end to end --------------------------------------------------------

APP_SRC = "def _update(dt):\n    pass\n\ndef _draw():\n    pass\n"


def _write_cart(carts_dir, name, perms, required=(), src=APP_SRC):
    d = Path(carts_dir) / (name.lower() + ".moy")
    d.mkdir(parents=True, exist_ok=True)
    ven = {"type": "app", "permissions": list(perms)}
    if required:
        ven["required"] = list(required)
    (d / "manifest.json").write_text(json.dumps({"title": name, "canvas": "320x240",
                                                 "moybyte": ven}))
    (d / "main.py").write_text(src)
    (d / "config.json").write_text("{}")
    return d


def _start(ws, title):
    cart = next(c for c in ws.carts.all if c["title"] == title)
    ws._open_workspace(cart)
    ok = ws.run.player.start(ws.run.project)
    return ok, cart


ROLE_PERMS = ["files:documents", "prefs", "appearance", "launch"]


def test_every_granted_permissions_names_are_in_the_namespace(tmp_path):
    _write_cart(str(tmp_path / "carts"), "Holder", ROLE_PERMS)
    ws = build_ws(tmp_path)
    ok, _cart = _start(ws, "Holder")
    assert ok, ws.run.player.cart_error
    for p in ROLE_PERMS:
        for name in P.row(p)[P.NAMES]:
            assert ws.run.player.ns.get(name) is not None, (p, name)


def test_a_turned_off_permission_reads_none_from_the_next_launch(tmp_path):
    _write_cart(str(tmp_path / "carts"), "Holder", ROLE_PERMS)
    ws = build_ws(tmp_path)
    cart = next(c for c in ws.carts.all if c["title"] == "Holder")
    aid = P.app_id(cart)
    for p in ROLE_PERMS:
        ws.consent.turn(aid, p, False)
    ok, _cart = _start(ws, "Holder")
    assert ok, ws.run.player.cart_error
    ns = ws.run.player.ns
    for p in ROLE_PERMS:
        for name in P.row(p)[P.NAMES]:
            assert name in ns and ns[name] is None, (p, name)
    assert ws.run.player.absent == tuple(ROLE_PERMS)
    # the grant holds none of the roles they map to
    assert ws.run.player.granted == ()


def test_a_turned_off_device_api_reads_none(tmp_path):
    _write_cart(str(tmp_path / "carts"), "Linked", ["multiplayer"])
    ws = build_ws(tmp_path)
    ok, cart = _start(ws, "Linked")
    assert ok and ws.run.player.ns["net"] is not None
    ws.consent.turn(P.app_id(cart), "multiplayer", False)
    ok, _cart = _start(ws, "Linked")
    assert ok and ws.run.player.ns["net"] is None and ws.run.player.ns["on_net"] is None


def test_a_turned_off_required_permission_refuses_the_launch(tmp_path):
    _write_cart(str(tmp_path / "carts"), "Needy", ["prefs"], required=["prefs"])
    ws = build_ws(tmp_path)
    cart = next(c for c in ws.carts.all if c["title"] == "Needy")
    assert cart["required"] == ["prefs"]
    ws.consent.turn(P.app_id(cart), "prefs", False)
    ok, _cart = _start(ws, "Needy")
    assert ok is False
    assert ws.run.player.cart_error == 'needs "prefs" - turned off in Settings'
    assert ws.app_guard.strikes(P.app_id(cart)) == 0     # a refusal is no crash
    ws.consent.turn(P.app_id(cart), "prefs", True)
    ok, _cart = _start(ws, "Needy")
    assert ok, ws.run.player.cart_error


def test_a_files_folder_the_console_has_none_of_refuses_the_launch(tmp_path):
    _write_cart(str(tmp_path / "carts"), "Stale", ["files:docs"],
                src="got, err = files.list()\n" + APP_SRC)
    ws = build_ws(tmp_path)
    ok, cart = _start(ws, "Stale")
    assert ok is False
    assert ws.run.player.cart_error == 'needs "files:docs" - this console has no such folder'
    assert ws.app_guard.strikes(P.app_id(cart)) == 0


@pytest.mark.first_run
def test_a_cart_written_in_the_editor_runs_on_its_manifest_unasked(tmp_path):
    """+ New makes the cart, and its record a made cart's (`Consent.made`:
    nothing consented in particular); its author gives it permissions; PLAY
    grants them, with no prompt."""
    ws = build_ws(tmp_path)
    ws.new_cart_and_edit()
    new = ws.run.project.cart
    man_path = Path(new["path"]) / "manifest.json"
    man = json.loads(man_path.read_text())
    man.setdefault("moybyte", {}).update({"type": "app", "permissions": ["prefs"]})
    man_path.write_text(json.dumps(man))
    (Path(new["path"]) / "main.py").write_text(APP_SRC)
    ws.carts.rescan()
    cart = next(c for c in ws.carts.all if c["path"] == new["path"])
    ws._open_workspace(cart)
    assert ws.run.player.start(ws.run.project), ws.run.player.cart_error
    assert ws.run.player.ns["prefs"] is not None
    assert not ws.confirm.open
    assert ws.consent.get(P.app_id(cart)) == {"perms": None, "off": []}
    assert ws.consent.ids() == [P.app_id(cart)]


# -- the install, end to end ---------------------------------------------------------

class _In:
    def __init__(self, *names):
        self.names = set(names)
        self.last_key = 0

    def steps(self, n):
        return int(bool(self.pressed(n)))

    def pressed(self, name):
        return name in self.names


def _store_ws(tmp_path, repo):
    ws = host_app.build_workstation(str(tmp_path / "carts"))
    ci.save_sources(str(tmp_path / "carts"), [repo.url("index.json")])
    ws.cart_net = MemNet(repo.routes())
    ws.pointer.visible = False
    return ws, ws._apps_by_id["getcarts"]


def _frames(ws, n=3):
    for _ in range(n):
        ws._dirty = True
        ws.frame(DT)


def _settle(ws, app):
    """Frames until the app's landing or removal, a store op a frame, ends."""
    for _ in range(40):
        _frames(ws, 1)
        if app._tail is None and app.phase not in ("getting",):
            return
    raise AssertionError("still busy: %s" % app.phase)


def _get(ws, app, cid):
    """Open Get Carts, open `cid`'s page and press Get; frames until the
    install asks or ends."""
    ws.open_app(app)
    for _ in range(40):
        _frames(ws, 1)
        if app.phase != "checking":
            break
    i = next(i for i, r in enumerate(app.rows) if r["cart"]["id"] == cid)
    app._tap_row(i)
    _frames(ws, 1)
    app._press("UPDATE" if app.rows[i]["state"] == "update" else "GET")
    for _ in range(60):
        _frames(ws, 1)
        if app.phase in ("consent", "done", "failed", "cart"):
            break


def _repo(**kw):
    repo = Repo("https://mit.example/carts", "Test carts")
    repo.add("radio", name="Radio", runtime="lua", modules=(), **kw)
    return repo


def test_install_asks_with_the_tables_lines_and_no_has_the_focus(tmp_path):
    repo = _repo(moybyte={"permissions": ["graphics", "pins", "prefs"]})
    ws, app = _store_ws(tmp_path, repo)
    _get(ws, app, "radio")
    assert app.phase == "consent"
    assert app.question == ["Switch the console's pins", "Keep its own settings"]
    assert app._ui.box("No") is not None and app._ui.box("Install") is not None
    _frames(ws, 2)
    assert app._focused == "No"
    assert not (tmp_path / "carts" / "radio.moy").exists()
    app.handle_input(_In("a"))                      # a kid's A lands on No
    _frames(ws, 2)
    assert app.phase == "cart" and not (tmp_path / "carts" / "radio.moy").exists()


def test_install_is_a_tap_away(tmp_path):
    """Touch alone: a tap on Install puts the cart on the shelf."""
    repo = _repo(moybyte={"permissions": ["pins"]})
    ws, app = _store_ws(tmp_path, repo)
    _get(ws, app, "radio")
    x, y, w, h = app._ui.box("Install")
    app.handle_pointer(x + w // 2, y + h // 2, True)
    _frames(ws, 1)
    app.handle_pointer(x + w // 2, y + h // 2, False)
    for _ in range(20):
        _frames(ws, 1)
        if app.phase == "done":
            break
    assert app.phase == "done" and (tmp_path / "carts" / "radio.moy").exists()


def test_a_denied_install_leaves_nothing(tmp_path):
    repo = _repo(moybyte={"permissions": ["pins"]})
    ws, app = _store_ws(tmp_path, repo)
    _get(ws, app, "radio")
    assert app.phase == "consent"
    job = app.job
    app.handle_input(_In("b"))                      # No
    for _ in range(40):                             # the build goes a store op a frame
        assert app.phase == "cart" and app.toast[0] == "Not installed. Nothing changed."
        if job.finished and app._sweep is None:
            break
        _frames(ws, 1)
    assert job.finished and app._sweep is None, "the stopped build is still going"
    assert not (tmp_path / "carts" / "radio.moy").exists()
    assert not os.listdir(ci.stage_root(str(tmp_path / "carts")))
    assert "radio.moy" not in ci.load_record(str(tmp_path / "carts"))
    assert ws.consent.ids() == []


def test_an_accepted_install_records_the_consent(tmp_path):
    repo = _repo(moybyte={"permissions": ["pins", "prefs"]})
    ws, app = _store_ws(tmp_path, repo)
    _get(ws, app, "radio")
    app._answer(True)
    for _ in range(20):
        _frames(ws, 1)
        if app.phase == "done":
            break
    assert app.phase == "done"
    assert (tmp_path / "carts" / "radio.moy").exists()
    assert ws.consent.get("radio") == {"perms": ["pins", "prefs"], "off": []}


def test_a_cart_asking_for_nothing_installs_unasked_and_is_recorded(tmp_path):
    repo = _repo(moybyte={"permissions": ["graphics", "input"]})
    ws, app = _store_ws(tmp_path, repo)
    _get(ws, app, "radio")
    assert app.phase == "done"
    assert ws.consent.get("radio") == {"perms": ["graphics", "input"], "off": []}


def test_an_update_that_adds_a_permission_asks_again(tmp_path):
    repo = _repo(moybyte={"permissions": ["prefs"]})
    ws, app = _store_ws(tmp_path, repo)
    _get(ws, app, "radio")
    app._answer(True)
    _settle(ws, app)
    assert ws.consent.get("radio")["perms"] == ["prefs"]
    repo.add("radio", version=2, name="Radio", runtime="lua", modules=(),
             moybyte={"permissions": ["prefs", "pins"]})
    ws.cart_net = MemNet(repo.routes())
    app.fetched_at = None
    _get(ws, app, "radio")
    assert app.phase == "consent"
    assert app.question == ["Keep its own settings", "Switch the console's pins"]


def test_a_required_permission_the_console_lacks_is_not_installed(tmp_path):
    repo = _repo(moybyte={"permissions": ["pins"], "required": ["pins"]})
    ws, app = _store_ws(tmp_path, repo)
    ws.gpio = None
    _get(ws, app, "radio")
    assert app.phase == "failed"
    assert app.why == ('Not for this console: needs "pins" - the %s has none'
                       % (P.board_words() or "console"))
    assert not (tmp_path / "carts" / "radio.moy").exists()


def test_closing_get_carts_mid_landing_records_the_consent_and_the_shelf(tmp_path):
    """The app closed while the build lands: the cart lands, its consent is
    recorded and the shelf shows it (pkg.land_now), not the cart alone."""
    repo = _repo(moybyte={"permissions": ["prefs"]})
    ws, app = _store_ws(tmp_path, repo)
    _get(ws, app, "radio")
    app._answer(True)
    for _ in range(40):
        _frames(ws, 1)
        if app.job.landing:
            break
    assert app.job.landing and not app.job.finished
    app.close()
    assert (tmp_path / "carts" / "radio.moy").exists()
    assert ws.consent.get("radio") == {"perms": ["prefs"], "off": []}
    assert any(str(c.get("path", "")).endswith("/radio.moy") for c in ws.carts.all)


def test_remove_forgets_the_consent(tmp_path):
    repo = _repo(moybyte={"permissions": ["prefs"]})
    ws, app = _store_ws(tmp_path, repo)
    _get(ws, app, "radio")
    app._answer(True)
    _settle(ws, app)
    assert ws.consent.get("radio") is not None
    app._remove()
    _settle(ws, app)
    assert ws.consent.get("radio") is None


# -- Settings' App permissions page ----------------------------------------------------

def test_settings_lists_each_carts_permissions_and_a_switch_turns_one_off(tmp_path):
    _write_cart(str(tmp_path / "carts"), "Holder", ["graphics", "prefs", "pins"])
    ws = build_ws(tmp_path)
    cart = next(c for c in ws.carts.all if c["title"] == "Holder")
    aid = P.app_id(cart)
    ws.open_settings()
    sl = ws.settings_layer
    sl.open_page("permissions")
    ws.frame(DT)
    assert sl.page_title() == "App permissions"
    rows = {title: perms for title, _aid, perms in sl.permits.rows()}
    assert rows["Holder"] == (("prefs", "Keep its own settings"),
                              ("pins", "Switch the console's pins"))
    sid = aid + " prefs"
    x, y, w, h = sl._sub.box(sid)
    sl.handle_pointer(x + w // 2, y + h // 2, True)
    ws.mark_dirty()
    ws.frame(DT)
    sl.handle_pointer(x + w // 2, y + h // 2, False)
    ws.mark_dirty()
    ws.frame(DT)
    assert not ws.consent.is_on(aid, "prefs") and ws.consent.is_on(aid, "pins")
    sl.back()
    assert sl.page is None


FILES_CASES = (
    ["files"], ["files:documents"], ["files:pictures"], ["files:music"],
    ["files:recordings"], ["files:downloads"], ["files:docs"], ["files:nonsense"],
    ["files", "files:documents"], ["files", "files:pictures"],
    ["files:pictures", "files:downloads", "prefs"], ["prefs"], [],
)


@pytest.mark.parametrize("perms", FILES_CASES)
def test_an_app_and_a_compiled_command_reach_the_same_folder(perms):
    """One policy for both tiers: the folder an app's files grant is made for
    (`moy_app.policy`'s kind, its place on disk) is the one root a WASI or
    native command is preopened, and a manifest the app tier refuses for two
    folders the command tier refuses too."""
    from runtime import moy_store_base, sh_door
    kind = moy_app.policy(perms)[1]
    app = [] if kind is None else ["files/" + moy_store_base.FILE_KIND_DIRS[kind]]
    assert sh_door.wasi_roots(perms) == app
    for root in app:
        assert root.split("/")[1] in moy_store_base.SHARED_FOLDERS


def test_a_compiled_command_reaches_one_shared_folder_and_never_the_whole_tree():
    """Bare `files` is Documents on every tier; Downloads, `.Trash` and
    `.history` are no grant's, and no permission preopens `files/` itself."""
    from runtime import sh_door
    assert sh_door.wasi_roots(["files"]) == ["files/Documents"]
    assert sh_door.wasi_roots(["files:pictures", "prefs"]) == ["files/Pictures"]
    assert sh_door.wasi_roots(["files:downloads"]) == []
    for perms in FILES_CASES:
        assert "files" not in sh_door.wasi_roots(perms)
    assert moy_app.manifest_error(["files", "files:pictures"]) is not None
