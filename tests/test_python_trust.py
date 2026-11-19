"""A Python cart's trust comes from review (#261, runtime/carts/cart_index.py's
module docstring): an index signed by the release's or moybyte-org/carts'
review key is REVIEWED and its Python carts install as trusted apps; from any
other index a Python cart installs behind the unreviewed warning, which the
console's password guards where one is set, and which the consent record
remembers per cart and source; an index's `revoked` refuses a withdrawn
version at install and stops an installed copy launching; Lua and wasm carts
from the same index ask as ever."""

import json
import os
import sys

import pytest

from cart_store_fixtures import MemNet, Repo
from runtime import app_perms, cart_index as ci, pkg
from test_ota_signing import TEST_KEYS
from test_pkg import BASE, Inst, _sign, done

OTHER = "https://friend.example/carts"


@pytest.fixture
def review(monkeypatch):
    """The test key is moybyte-org/carts' review key, and the image's own
    keys verify nothing."""
    from runtime import net_binding
    monkeypatch.setitem(sys.modules, "moy_net", net_binding)
    monkeypatch.setattr(ci, "INDEX_KEYS", ())
    monkeypatch.setattr(ci, "REVIEW_KEYS", TEST_KEYS)


def _python_repo(base=BASE, name="Test carts"):
    repo = Repo(base, name)
    repo.add("snake", name="Snake", runtime="python", modules=(),
             moybyte={"permissions": ["prefs"]})
    repo.add("mesh", name="Mesh", runtime="lua", modules=(),
             moybyte={"permissions": ["prefs"]})
    repo.add("dm", name="Dungeon", moybyte={"permissions": ["prefs"]})
    return repo


def _by_id(carts):
    return dict((c["id"], c) for c in carts)


# -- 1. the index's trust ------------------------------------------------------------


def test_an_index_signed_by_the_review_key_is_reviewed(review):
    repo = _python_repo()
    got = _by_id(ci.parse_index(_sign(repo.index()), repo.url("index.json")))
    assert got["snake"]["reviewed"] is True and got["snake"]["signed"] is True
    assert ci.is_reviewed(got["mesh"]) and ci.is_reviewed(got["dm"])
    plain = _by_id(ci.parse_index(repo.index(), repo.url("index.json")))
    assert plain["snake"]["reviewed"] is False, "an unsigned index is read, not reviewed"


def test_a_bad_signature_is_refused_and_an_entry_cannot_vouch_for_itself(review):
    from test_ota_signing import TEST_D
    repo = _python_repo()
    for bad in (_sign(repo.index()).replace(b'"Snake"', b'"Snakf"'),
                _sign(repo.index(), d=TEST_D + 2)):
        with pytest.raises(ci.InstallError) as exc:
            ci.parse_index(bad, repo.url("index.json"))
        assert exc.value.text == ci.BAD_SIGNATURE
    repo.carts[0].update(reviewed=True, official=True)
    snake = _by_id(ci.parse_index(repo.index(), repo.url("index.json")))["snake"]
    assert not ci.is_reviewed(snake) and not ci.is_official(snake)


def test_the_release_key_reviews_too_and_no_review_key_reviews_nothing(monkeypatch):
    from runtime import net_binding
    monkeypatch.setitem(sys.modules, "moy_net", net_binding)
    monkeypatch.setattr(ci, "INDEX_KEYS", TEST_KEYS)
    monkeypatch.setattr(ci, "REVIEW_KEYS", ())
    repo = _python_repo(OTHER)
    snake = _by_id(ci.parse_index(_sign(repo.index()), repo.url("index.json")))["snake"]
    assert ci.is_reviewed(snake), "the image's own key is the owner's"


def test_the_browser_reviews_moybyte_org_by_its_url_alone(monkeypatch):
    """No verifier (the browser): a signed index from moybyte-org/carts'
    url is reviewed, its fetch having checked the certificate; one from
    anywhere else is not."""
    monkeypatch.setattr(ci, "_verifier", lambda: None)
    repo = _python_repo()
    data = _sign(repo.index())
    assert _by_id(ci.parse_index(data, ci.DEFAULT_INDEXES[0]))["snake"]["reviewed"]
    assert not _by_id(ci.parse_index(data, OTHER + "/index.json"))["snake"]["reviewed"]
    assert not _by_id(ci.parse_index(repo.index(), ci.DEFAULT_INDEXES[0]))["snake"]["reviewed"]


def test_a_python_cart_runs_on_every_console_whatever_its_index():
    assert ci.runs_on({"runtime": "python"}, ())


# -- 2. revocation ---------------------------------------------------------------------


def test_a_revoked_version_is_refused_at_install(tmp_path):
    repo = _python_repo()
    repo.revoke("snake", [1])
    repo.revoke("gone", [3])
    revoked = {}
    url = repo.url("index.json")
    snake = _by_id(ci.parse_index(repo.index(), url, None, revoked))["snake"]
    assert revoked == {url: {"snake": [1], "gone": [3]}}
    assert ci.is_revoked(snake) and not ci.is_revoked(snake, 2)
    inst = Inst(str(tmp_path), MemNet(repo.routes()))
    p = ci.plan(snake, "esp32s3", 2)
    assert pkg.blocker(inst, snake, p, None, True) == ci.REVOKED
    job = ci.Install(snake, p, MemNet(repo.routes()), str(tmp_path), lambda fn: fn())
    while job.step():
        pass
    assert job.error == ci.REVOKED and not os.path.exists(str(tmp_path / "snake.moy"))


def test_a_malformed_revoked_member_is_read_as_none():
    doc = json.dumps({"version": 1, "carts": [], "revoked": {"a": "1", "b": [2]}})
    revoked = {}
    ci.parse_index(doc, "https://x/index.json", None, revoked)
    assert revoked == {"https://x/index.json": {"b": [2]}}
    revoked = {}
    ci.parse_index(json.dumps({"version": 1, "carts": [], "revoked": [1]}),
                   "https://x/index.json", None, revoked)
    assert revoked == {"https://x/index.json": {}}


def test_an_installed_copy_of_a_revoked_version_is_blocked_until_updated(tmp_path):
    repo = _python_repo()
    root = str(tmp_path / "carts")
    os.makedirs(root)
    ci.save_sources(root, [repo.url("index.json")])
    inst = Inst(root, MemNet(repo.routes()))
    p = pkg.Pkg(inst, [].append, [].append, ask=lambda q: True)
    assert done(p.install(["mesh"], yes=True)) == 0
    cart = {"id": "mesh", "runtime": "lua", "permissions": ["prefs"],
            "path": root + "/mesh.moy"}
    consent = inst.consent()
    assert app_perms.check(cart, consent.for_run(cart), ())[2] is None

    repo.revoke("mesh", [1])
    inst._net = MemNet(repo.routes())
    err = []
    p = pkg.Pkg(inst, [].append, err.append, ask=lambda q: True)
    assert done(p.update()) == 0
    assert any("took it back" in e for e in err), err
    assert consent.get("mesh")["revoked"] is True
    assert app_perms.check(cart, consent.for_run(cart), ())[2] == app_perms.REVOKED_REFUSAL
    rec = ci.load_record(root)
    listed = _by_id(ci.parse_index(repo.index(), repo.url("index.json")))["mesh"]
    assert ci.cart_state(listed, ci.plan(listed), rec["mesh.moy"], True) == "revoked"

    repo.add("mesh", version=2, name="Mesh", runtime="lua", modules=(),
             moybyte={"permissions": ["prefs"]})
    inst._net = MemNet(repo.routes())
    listed = _by_id(ci.parse_index(repo.index(), repo.url("index.json")))["mesh"]
    assert ci.cart_state(listed, ci.plan(listed), rec["mesh.moy"], True) == "update", \
        "a newer version it did not withdraw is the way out"
    p = pkg.Pkg(inst, [].append, [].append, ask=lambda q: True)
    assert done(p.update()) == 0
    assert done(p.upgrade(yes=True)) == 0
    assert "revoked" not in consent.get("mesh")
    assert app_perms.check(cart, consent.for_run(cart), ())[2] is None


def test_an_index_revokes_only_the_carts_installed_from_it(tmp_path):
    repo = _python_repo()
    root = str(tmp_path / "carts")
    os.makedirs(root)
    ci.save_sources(root, [repo.url("index.json")])
    inst = Inst(root, MemNet(repo.routes()))
    assert done(pkg.Pkg(inst, [].append, [].append).install(["mesh"], yes=True)) == 0
    assert pkg.revocations(inst, {OTHER + "/index.json": {"mesh": [1]}}) == []
    assert "revoked" not in inst.consent().get("mesh")
    assert pkg.revocations(inst, {repo.url("index.json"): {"mesh": [1]}}) == ["mesh.moy"]
    assert pkg.revocations(inst, {repo.url("index.json"): {}}) == []
    assert "revoked" not in inst.consent().get("mesh"), "a revocation taken back"


# -- 3. the warning ----------------------------------------------------------------------


def _origin(reviewed, index=BASE + "/index.json"):
    return {"publisher": "Test carts", "reviewed": reviewed, "index": index}


def test_an_unreviewed_python_cart_carries_the_warning_and_names_its_publisher():
    cart = {"runtime": "python", "permissions": ["prefs"]}
    lines = app_perms.prompt(cart, None, _origin(False))
    assert lines[0] == "From Test carts"
    assert lines[-2:] == list(app_perms.UNREVIEWED)
    assert app_perms.TRUSTED not in lines and app_perms.warned(lines)
    lines = app_perms.prompt(cart, None, _origin(True))
    assert lines[0] == "From Test carts" and lines[-1] == app_perms.TRUSTED
    assert not app_perms.warned(lines)
    assert app_perms.prompt(cart)[-1] == app_perms.TRUSTED, \
        "a cart that arrived as files asks as it did"


def test_the_acceptance_is_remembered_per_cart_and_source():
    store = {}
    consent = app_perms.Consent(_Store(store))
    cart = {"runtime": "python", "permissions": ["prefs"], "id": "snake"}
    src = BASE + "/index.json"
    consent.record("snake", ["prefs"], src)
    rec = consent.get("snake")
    assert rec["unreviewed"] == src
    assert app_perms.prompt(cart, rec, _origin(False)) is None, "an update asks nothing new"
    more = dict(cart, permissions=["prefs", "clipboard"])
    assert app_perms.warned(app_perms.prompt(more, rec, _origin(False))), \
        "an update that adds a permission asks again, warning and all"
    assert app_perms.warned(app_perms.prompt(cart, rec, _origin(False, OTHER + "/index.json"))), \
        "the same cart from another source asks again"
    consent.record("snake", ["prefs"])
    assert app_perms.warned(app_perms.prompt(cart, consent.get("snake"), _origin(False))), \
        "a reviewed install's consent is no acceptance of the warning"


def test_pkg_installs_an_unreviewed_python_cart_and_records_its_acceptance(tmp_path):
    repo = _python_repo()
    root = str(tmp_path / "carts")
    os.makedirs(root)
    ci.save_sources(root, [repo.url("index.json")])
    inst = Inst(root, MemNet(repo.routes()))
    inst.locked = lambda: False
    out = []
    p = pkg.Pkg(inst, out.append, [].append, ask=lambda q: True)
    assert done(p.install(["snake"])) == 0
    said = "".join(out)
    assert "From Test carts" in said and app_perms.UNREVIEWED[0] in said
    assert inst.consent().get("snake")["unreviewed"] == repo.url("index.json")
    assert os.path.exists(root + "/snake.moy/manifest.json")


class _Store:
    def __init__(self, d):
        self.d = d

    def get(self, key, default=None):
        return self.d.get(key, default)

    def set(self, key, value, now=True):
        self.d[key] = value


# -- 4. Lua and wasm ------------------------------------------------------------------------


def test_lua_and_wasm_from_the_same_store_carry_no_warning(tmp_path):
    for rt in ("lua", "wasm"):
        cart = {"runtime": rt, "permissions": ["prefs"]}
        lines = app_perms.prompt(cart, None, _origin(False))
        assert not app_perms.warned(lines) and app_perms.TRUSTED not in lines
        assert app_perms.prompt({"runtime": rt, "permissions": []}, None, _origin(False)) \
            is None, "a cart that asks for nothing is not asked about"
    repo = _python_repo()
    root = str(tmp_path / "carts")
    os.makedirs(root)
    ci.save_sources(root, [repo.url("index.json")])
    inst = Inst(root, MemNet(repo.routes()))
    inst.locked = lambda: True
    p = pkg.Pkg(inst, [].append, [].append, ask=lambda q: True)
    assert done(p.install(["mesh", "dm"])) == 0, "a locked console asks no password for them"
    assert "unreviewed" not in inst.consent().get("mesh")


# -- 5. the parent gate ------------------------------------------------------------------------


def _locked_pkg(tmp_path, typed):
    repo = _python_repo()
    root = str(tmp_path / "carts")
    os.makedirs(root)
    ci.save_sources(root, [repo.url("index.json")])
    inst = Inst(root, MemNet(repo.routes()))
    inst.locked = lambda: True
    inst.unlock = lambda pw: pw == "hunter22"
    err = []
    p = pkg.Pkg(inst, [].append, err.append, ask=lambda q: True,
                password=None if typed is None else (lambda q: typed))
    return root, inst, p, err


@pytest.mark.parametrize("typed, why", [(None, pkg.NO_PASSWORD),
                                        ("nope", pkg.WRONG_PASSWORD)])
def test_a_locked_console_refuses_the_warning_without_its_password(tmp_path, typed, why):
    root, inst, p, err = _locked_pkg(tmp_path, typed)
    assert done(p.install(["snake"], yes=True)) == 1, "-y never answers the password"
    assert "snake: " + why in err
    assert not os.path.exists(root + "/snake.moy") and inst.consent().get("snake") is None


def test_a_locked_console_takes_the_warning_with_its_password(tmp_path):
    root, inst, p, err = _locked_pkg(tmp_path, "hunter22")
    assert done(p.install(["snake"])) == 0, err
    assert os.path.exists(root + "/snake.moy/manifest.json")


def test_the_gate_is_the_console_s_passwd(tmp_path):
    from runtime import ssh_service

    class WS:
        carts_root = str(tmp_path / "moy" / "carts")
    os.makedirs(WS.carts_root)
    ws = WS()
    assert not ssh_service.is_set(ws) and not ssh_service.is_password(ws, "hunter22")
    os.makedirs(ssh_service.system_dir(ws), exist_ok=True)
    with open(ssh_service.system_dir(ws) + "/" + ssh_service.PASSWORD, "w") as f:
        f.write(ssh_service.hashed("hunter22", "ab" * 16) + "\n")
    assert ssh_service.is_set(ws)
    assert ssh_service.is_password(ws, "hunter22")
    assert not ssh_service.is_password(ws, "hunter23") and not ssh_service.is_password(ws, "")


def test_get_carts_asks_for_the_console_s_password_to_accept_the_warning(tmp_path):
    from runtime import ssh_service
    from test_app_perms import _frames, _get, _settle, _store_ws
    repo = _python_repo("https://mit.example/carts")
    ws, app = _store_ws(tmp_path, repo)
    os.makedirs(ssh_service.system_dir(ws), exist_ok=True)
    with open(ssh_service.system_dir(ws) + "/" + ssh_service.PASSWORD, "w") as f:
        f.write(ssh_service.hashed("hunter22", "cd" * 16) + "\n")
    _get(ws, app, "snake")
    assert app.phase == "consent" and app_perms.warned(app.question), app.why
    assert app.question[0] == "From Test carts"
    _frames(ws, 2)
    assert app._ui.box("Password") is not None or app._focused == "password"
    app.password = "nope"
    app._answer(True)
    _frames(ws, 2)
    assert app.phase == "consent" and app.password_bad and app.password == ""
    assert not (tmp_path / "carts" / "snake.moy").exists()
    app.password = "hunter22"
    app._answer(True)
    _settle(ws, app)
    assert app.phase == "done", app.why
    assert (tmp_path / "carts" / "snake.moy" / "manifest.json").exists()
    assert ws.consent.get("snake")["unreviewed"] == repo.url("index.json")


def test_get_carts_asks_no_password_where_the_console_has_none(tmp_path):
    from test_app_perms import _get, _settle, _store_ws
    repo = _python_repo("https://mit.example/carts")
    ws, app = _store_ws(tmp_path, repo)
    _get(ws, app, "snake")
    assert app.phase == "consent" and app_perms.warned(app.question)
    app._answer(True)
    _settle(ws, app)
    assert app.phase == "done", app.why


def test_a_secret_field_draws_a_star_a_character_and_answers_the_text(tmp_path):
    """The toolkit's one text field under `secret` (the prompt's password):
    its pixels are a field holding as many `*`s, and what it answers is the
    text itself."""
    from runtime import ui, ui_draw
    from runtime.host_canvas import make_system_canvas
    from ws_helpers import build_ws
    th = build_ws(tmp_path).theme_colors

    def shot(text, o):
        cv = make_system_canvas(320, 240)
        host = ui_draw.AppHost()
        got = None
        for n in range(3):
            ui_draw.paint_frame(n, False)
            host.begin(cv, th, (0, 0, 320, 240))
            ui.page("Probe")
            got = ui.field("Password", text, 64, o)
        return bytes(cv._buf), got

    masked, got = shot("hunter22", {"id": "pw", "secret": True})
    assert got[0] == "hunter22"
    assert masked == shot("********", {"id": "pw"})[0]
    assert masked != shot("hunter22", {"id": "pw"})[0]
