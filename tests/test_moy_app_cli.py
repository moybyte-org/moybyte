"""`moy app` (sdk/moybyte_sdk, moy-spec's `moy` runs it as the plugin `moy-app`):
each verb on the host.

  * new: every tier's cart, and an app with its own service in every
    service tier, checks with no error and no permission warning;
  * check: the manifest as the readers take it (a Moybyte field at the top
    level, an unknown permission, `required` outside `permissions`, a native
    cart without `full_access`), and the permissions a program uses against
    the declared ones -- a Python or Lua name, a wasm module's moybyte.app
    import, a native module's .moy.rows -- each undeclared one warned, each
    declared one nothing uses warned, a row the SDK's C library links only
    noted;
  * build: a compiled template built into its own folder on the host chip
    checks with nothing left to build; a script cart has nothing to build,
    and a native one needs a chip;
  * stubs: what it writes is tools/gen_os_table.py's output byte for byte;
  * push: into a simulator store, the stubs left behind, a cart that does
    not check refused;
  * the package: pyproject's entry point is the CLI's main.
"""

import glob
import json
import os
import shutil
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "sdk"))

from moybyte_sdk import cli, os_table  # noqa: E402
from tools import gen_os_table, wat  # noqa: E402

PERM_WORDS = ("needs", "declared and nothing")


def _new(tmp_path, name, **kw):
    return cli.new_cart(name, into=str(tmp_path), **kw)


def _manifest(cart, edit):
    path = os.path.join(cart, "manifest.json")
    with open(path) as f:
        man = json.load(f)
    edit(man)
    with open(path, "w") as f:
        json.dump(man, f)


def _perm_warnings(warnings):
    return [w for w in warnings if any(k in w for k in PERM_WORDS)]


@pytest.mark.parametrize("tier,service", [
    ("python", None), ("lua", None), ("wasm", None), ("wasi", None), ("c", None),
    ("python", "lua"), ("lua", "python"), ("python", "wasi"), ("lua", "c"), ("wasm", "wasi"),
])
def test_a_new_cart_checks_clean(tmp_path, tier, service):
    cart = _new(tmp_path, "My Cart", tier=tier, service=service)
    errors, warnings, _notes = cli.check_cart(cart)
    assert errors == []
    assert _perm_warnings(warnings) == []
    built = [w for w in warnings if "not built" in w]
    assert warnings == built                    # only a compiled program's build is left
    if tier in ("python", "lua") and service in (None, "python", "lua"):
        assert warnings == []
    man = cli.read_manifest(cart)
    assert man["id"] == "local.my_cart"
    if service is not None:
        assert man["moybyte"]["entries"] == {"service": cli.SERVICE_FILE[service]}
        assert man["moybyte"]["type"] == "app"


def test_new_refuses_what_no_cart_is(tmp_path):
    with pytest.raises(cli.Refused):
        _new(tmp_path, "x", tier="wasi", typ="app")
    with pytest.raises(cli.Refused):
        _new(tmp_path, "x", tier="python", typ="game", service="lua")
    _new(tmp_path, "x")
    with pytest.raises(cli.Refused):
        _new(tmp_path, "x")                     # it exists


def test_an_undeclared_script_use_is_warned(tmp_path):
    py = _new(tmp_path, "py")
    with open(os.path.join(py, "main.py"), "a") as f:
        f.write('\n\ndef _update():\n    moybyte.notify("hi")  # clipboard.text() in a comment\n'
                '    s = "files.load in a string"\n')
    _errors, warnings, _n = cli.check_cart(py)
    assert _perm_warnings(warnings) == [
        'main uses moybyte.notify, which needs "notifications": not in "permissions" '
        '(read from its source: build it for the exact list)']
    lua = _new(tmp_path, "lua", tier="lua")
    with open(os.path.join(lua, "main.lua"), "a") as f:
        f.write('\nfunction _update()\n  clipboard.put_text("x") -- moybyte.notify()\nend\n')
    _errors, warnings, _n = cli.check_cart(lua)
    assert [w.split(",")[0] for w in _perm_warnings(warnings)] == [
        "main uses clipboard.put_text"]


def test_a_declared_permission_nothing_uses_is_warned(tmp_path):
    cart = _new(tmp_path, "py")
    _manifest(cart, lambda m: m["moybyte"]["permissions"].extend(["clipboard", "autostart"]))
    _errors, warnings, _n = cli.check_cart(cart)
    # autostart grants what no call shows: never called unused
    assert _perm_warnings(warnings) == [
        '"clipboard" is declared and nothing the cart calls needs it']


def test_front_is_needed_only_from_the_background(tmp_path):
    cart = _new(tmp_path, "py")
    with open(os.path.join(cart, "main.py"), "a") as f:
        f.write('\nmoybyte.open_with("x", 0)\n')
    assert _perm_warnings(cli.check_cart(cart)[1]) == []
    _manifest(cart, lambda m: m["moybyte"].update(type="service"))
    assert [w.split(",")[0] for w in _perm_warnings(cli.check_cart(cart)[1])] == [
        "main uses moybyte.open_with"]


def test_a_wasm_module_s_imports_are_its_uses(tmp_path):
    cart = _new(tmp_path, "w", tier="wasm")
    os.remove(os.path.join(cart, "src", "main.c"))
    module = wat.assemble(
        '(module (import "moybyte.app" "moybyte_notify" '
        '(func (param i32 i32 i32 i32) (result i32)))'
        ' (import "moybyte.app" "prefs_get" (func (param i32 i32 i32 i32) (result i32)))'
        ' (import "moy" "cls" (func (param i32)))'
        ' (memory (export "memory") 1) (func (export "_draw")))')
    with open(os.path.join(cart, "main.wasm"), "wb") as f:
        f.write(module)
    assert cli.wasm_imports(module) == [("moybyte.app", "moybyte_notify"),
                                        ("moybyte.app", "prefs_get"), ("moy", "cls")]
    errors, warnings, notes = cli.check_cart(cart)
    assert errors == []
    assert _perm_warnings(warnings) == [
        'main uses moybyte.app.moybyte_notify, which needs "notifications": '
        'not in "permissions"']
    assert "main uses prefs: moybyte.app.prefs_get" in notes
    _manifest(cart, lambda m: m.pop("extensions"))
    assert any("does not declare the extension" in e for e in cli.check_cart(cart)[0])


def _module(tmp_path, ordinals):
    """A relocatable ELF whose .moy.rows names `ordinals`, as the SDK's stubs
    record them."""
    c = tmp_path / "rows.c"
    c.write_text("".join('__asm__(".pushsection .moy.rows,\\"\\",@progbits\\n.4byte %d\\n'
                         '.popsection");\n' % o for o in ordinals) + "int x;\n")
    out = tmp_path / "rows.o"
    cc = shutil.which("cc") or shutil.which("gcc") or shutil.which("clang")
    if cc is None:
        pytest.skip("no C compiler")
    subprocess.run([cc, "-c", str(c), "-o", str(out)], check=True)
    return out.read_bytes()


def test_a_native_module_s_rows_are_its_uses(tmp_path):
    ordinal = {name: o for o, (name, _p) in os_table.ELF.items()}
    data = _module(tmp_path, [ordinal["moy_notify"], ordinal["open"], ordinal["moy_prefs_get"]])
    assert cli.elf_rows(data) == sorted([ordinal["moy_notify"], ordinal["open"],
                                         ordinal["moy_prefs_get"]])
    cart = _new(tmp_path, "n", tier="c")
    shutil.rmtree(os.path.join(cart, "src"))
    with open(os.path.join(cart, "main.host.elf"), "wb") as f:
        f.write(data)
    _manifest(cart, lambda m: m["moybyte"].update(native=["host"],
                                                   permissions=["full_access", "prefs"]))
    errors, warnings, notes = cli.check_cart(cart)
    assert errors == []
    assert _perm_warnings(warnings) == [
        'main uses moy_notify, which needs "notifications": not in "permissions"']
    # open is the C library's (stdio links it): noted, never warned
    assert 'main links open through the C library: "files" where it reaches them' in notes
    with open(os.path.join(cart, "main.host.elf"), "wb") as f:
        f.write(_module(tmp_path, [os_table.RETIRED[0], 4000]))
    errors = cli.check_cart(cart)[0]
    assert any("retired" in e for e in errors)
    assert any("needs a newer console" in e for e in errors)


@pytest.mark.parametrize("edit,want", [
    (lambda m: m.update(permissions=["prefs"]), '"permissions" is at the top level'),
    (lambda m: m["moybyte"].update(permision=["prefs"]), '"moybyte.permision" is no Moybyte'),
    (lambda m: m["moybyte"]["permissions"].append("prefz"), '"prefz" is no permission'),
    (lambda m: m["moybyte"].update(required=["files"]), '"required" names "files"'),
    (lambda m: m["moybyte"].update(type="widget"), '"type" "widget" is none of'),
    (lambda m: m["moybyte"].update(entries={"service": "../x.py"}), '"entries" does not read'),
    (lambda m: m["moybyte"].update(native=["host"]), "main.host.elf is missing"),
])
def test_a_manifest_the_readers_would_misread_is_an_error(tmp_path, edit, want):
    cart = _new(tmp_path, "m")
    _manifest(cart, edit)
    errors = cli.check_cart(cart)[0]
    assert any(want in e for e in errors), errors


def test_a_native_cart_must_ask_for_full_access(tmp_path):
    cart = _new(tmp_path, "c", tier="c")
    _manifest(cart, lambda m: m["moybyte"].update(permissions=[]))
    assert any('"full_access"' in e for e in cli.check_cart(cart)[0])


def test_the_command_line_exits_by_its_verdict(tmp_path):
    cart = _new(tmp_path, "m")
    run = [sys.executable, "-m", "moybyte_sdk"]
    env = dict(os.environ, PYTHONPATH=os.path.join(ROOT, "sdk") + os.pathsep + ROOT)
    assert subprocess.run(run + ["check", cart], env=env, capture_output=True).returncode == 0
    _manifest(cart, lambda m: m["moybyte"]["permissions"].append("prefz"))
    p = subprocess.run(run + ["check", cart], env=env, capture_output=True, text=True)
    assert p.returncode == 1 and '"prefz" is no permission' in p.stdout


def _wasi_sdk_or_skip():
    from tools import jet_cart
    try:
        jet_cart.wasi_sdk(fetch=True)
    except Exception as exc:  # noqa: BLE001
        pytest.skip("no wasi-sdk: %s" % exc)


@pytest.mark.parametrize("tier,service", [("c", None), ("lua", "c"), ("wasi", None),
                                          ("wasm", "c"), ("python", "wasi")])
def test_build_leaves_a_template_nothing_to_build(tmp_path, tier, service):
    if "wasi" in (tier, service) or tier == "wasm":
        _wasi_sdk_or_skip()
    cart = _new(tmp_path, "b", tier=tier, service=service)
    built = dict(cli.build_cart(cart, ["host"]))
    for path in built.values():
        assert os.path.dirname(path) == cart and os.path.getsize(path) > 0
    if tier == "c":
        assert built["host"] == os.path.join(cart, "main.host.elf")
        assert cli.read_manifest(cart)["moybyte"]["native"] == ["host"]
    if service == "c":
        assert built["service.host"] == os.path.join(cart, "service.host.elf")
    if tier in ("wasm", "wasi"):
        assert built["main"] == os.path.join(cart, "main.wasm")
        assert not os.path.exists(os.path.join(cart, "main.host.elf"))
    errors, warnings, _notes = cli.check_cart(cart)
    assert errors == [] and warnings == []


def test_build_has_nothing_for_a_script_and_needs_a_chip_for_a_module(tmp_path, capsys):
    cart = _new(tmp_path, "s", tier="lua")
    assert cli.build_cart(cart) == []
    assert cli.main(["build", cart]) == 0
    assert "runs from its source: nothing to build" in capsys.readouterr().out
    native = _new(tmp_path, "n", tier="c")
    with pytest.raises(cli.Refused, match="--chip"):
        cli.build_cart(native)
    assert cli.main(["build", native]) == 1
    assert not glob.glob(os.path.join(native, "*.elf"))


@pytest.mark.parametrize("tier,service", [("python", None), ("lua", None), ("wasm", None),
                                          ("wasi", None), ("c", None), ("lua", "c")])
def test_the_stubs_are_the_generator_s(tmp_path, tier, service):
    cart = _new(tmp_path, "s", tier=tier, service=service)
    wrote = cli.write_stubs(cart)
    made = dict(gen_os_table.outputs())
    want = [rel for t in cli.stub_tiers(cli.read_manifest(cart), cart)
            for rel, _src in cli.STUB_FILES[t]]
    assert sorted(set(wrote) - {"compile_flags.txt"}) == sorted(set(want))
    for t in cli.stub_tiers(cli.read_manifest(cart), cart):
        for rel, src in cli.STUB_FILES[t]:
            if src in made:
                want_text = made[src]
            else:                                    # a header the SDK keeps as it is
                with open(src) as f:
                    want_text = f.read()
            with open(os.path.join(cart, rel)) as f:
                assert f.read() == want_text, rel
    assert ("compile_flags.txt" in wrote) == (tier in ("wasm", "wasi", "c") or service == "c")


def test_push_copies_the_cart_without_its_stubs_into_a_store(tmp_path):
    cart = _new(tmp_path, "p", tier="lua", service="c")
    store = tmp_path / "store"
    ns = type("A", (), {"cart": cart, "board": None, "store": str(store)})
    assert cli.cmd_push(ns) == 0
    dst = store / "p.moy"
    got = sorted(str(p.relative_to(dst)) for p in dst.rglob("*") if p.is_file())
    assert got == ["main.lua", "manifest.json", "src/service/main.c"]
    with open(os.path.join(cart, "main.lua")) as f:
        assert (dst / "main.lua").read_text() == f.read()
    # a cart that does not check is not pushed
    _manifest(cart, lambda m: m["moybyte"]["permissions"].append("prefz"))
    shutil.rmtree(dst)
    assert cli.cmd_push(ns) == 1
    assert not dst.exists()


def test_the_simulator_s_store_is_the_simulator_s():
    sys.path.insert(0, os.path.join(ROOT, "tools"))
    import simulate_desktop
    assert cli.SIM_STORE == simulate_desktop.DEFAULT_SAVE_DIR


def test_the_package_puts_moy_app_on_path():
    with open(os.path.join(ROOT, "pyproject.toml")) as f:
        text = f.read()
    scripts = text.split("[project.scripts]", 1)[1].split("\n[", 1)[0]
    assert 'moy-app = "moybyte_sdk.cli:main"' in scripts
    found = text.split("[tool.setuptools.packages.find]", 1)[1].split("\n[", 1)[0]
    assert '"sdk"' in found and '"moybyte_sdk*"' in found
    assert callable(cli.main)
