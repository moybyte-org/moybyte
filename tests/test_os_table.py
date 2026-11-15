"""The one source of Moybyte's app and OS calls (native/os_calls.json,
docs/os_2026-11/tiers.md section 1) and everything made from it:

- every tier's generated table (the kernel's, the SDK's, the app ABI's, the
  wasm and Lua names, the Python console's regions and the docs table) is
  tools/gen_os_table.py's output for the tree's source, byte for byte (the
  drift test);
- the ELF rows are append-only: the OS function table is the frozen copy
  (tests/fixtures/os_table_frozen.txt) line for line, so a row changed, moved
  or removed fails here and a new one is written into both;
- the table's own rules (gen_os_table.check) hold, and a retired row answers
  through a typed stub;
- the SDK's copies of the kernel's numbers (the log levels, the event record)
  are the kernel's;
- a module the SDK builds for each chip whose toolchain is here carries the
  table version and the rows it calls, imports nothing, and passes the
  console's verdict for its chip and no other.
"""

import ctypes
import json
import os
import re
import shutil
import subprocess

import pytest

from tools import gen_os_table, elf_cart

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FROZEN = os.path.join(ROOT, "tests", "fixtures", "os_table_frozen.txt")
FIXTURES = os.path.join(ROOT, "tests", "fixtures", "elf")
MACHINE = {"esp32s3": 94, "esp32p4": 243}


def _table():
    """The OS function table, by ordinal."""
    return gen_os_table.elf(gen_os_table.load())


def test_the_generated_files_are_the_source_s():
    for path, text in gen_os_table.outputs():
        with open(path) as f:
            assert f.read() == text, "%s is stale: run tools/gen_os_table.py" % path


def test_every_whole_generated_file_is_marked_generated_for_github():
    paths = [os.path.relpath(p, ROOT) for p in gen_os_table.whole(gen_os_table.outputs())]
    got = subprocess.run(["git", "check-attr", "linguist-generated", "--"] + paths, cwd=ROOT,
                         capture_output=True, text=True, check=True).stdout.splitlines()
    unmarked = [line.split(":")[0] for line in got if not line.endswith(": true")]
    assert unmarked == [], "give each a .gitattributes pattern: %s" % unmarked


def test_the_http_prompt_says_it_reaches_the_local_network():
    """`http` reaches any host, the LAN's included, with no address filter
    (owner, 2026-11-09): its install prompt line, generated from the
    source's permission, says so."""
    from runtime import app_perms
    src = next(p for p in gen_os_table.load()["permissions"] if p["perm"] == "http")
    assert src["prompt"] == "Fetch pages from the internet and your network"
    assert app_perms.row("http")[app_perms.PROMPT] == src["prompt"]
    assert app_perms.prompt({"runtime": "lua", "permissions": ["http"]}) == [src["prompt"]]
    assert app_perms.prompt({"runtime": "wasm", "permissions": ["http"]}) == [src["prompt"]]


def test_the_rows_are_the_frozen_copy_line_for_line():
    frozen = [ln for ln in open(FROZEN).read().splitlines() if ln and not ln.startswith("#")]
    rows = _table()["rows"]
    have = ["%d %s | %s" % (i, r["name"], r["c"]) for i, r in enumerate(rows)]
    assert have[:len(frozen)] == frozen, "a shipped row changed: rows are appended, never edited"
    assert have == frozen, "a new row is a new last line of %s too" % FROZEN


def test_the_table_s_rules():
    src = gen_os_table.load()
    gen_os_table.check(src)
    assert src["version"] == len(_table()["rows"])
    bad = json.loads(json.dumps(src))
    row = dict(bad["rows"][0], name="open2", elf=src["version"])
    bad["rows"].append(row)
    with pytest.raises(gen_os_table.TableError):
        gen_os_table.check(bad)             # an append that does not bump the version
    bad["version"] += 1
    with pytest.raises(gen_os_table.TableError):
        gen_os_table.check(bad)             # its prototype names another function
    row["elf"] = 0
    row["c"] = row["c"].replace("open(", "open2(")
    with pytest.raises(gen_os_table.TableError):
        gen_os_table.check(bad)             # an ordinal twice


def test_a_row_s_floor_grant_drops_its_role_s_permission():
    """Every app is handed the theme's reads (system_api.make_system_api and
    the Player's grant of a compiled app), so they need no permission; setting
    the look keeps the role's."""
    src = gen_os_table.load()
    rows = {r["name"]: r for r in src["rows"]}
    for name in ("theme.colors", "theme.token", "theme.gen", "theme.light",
                 "theme.name", "theme.variant"):
        assert gen_os_table.perms_of(rows[name], src) == [], name
    for name in ("theme.set", "theme.set_variant", "theme.edit_file"):
        assert gen_os_table.perms_of(rows[name], src) == ["appearance"], name
    for wrong in ({"grant": "never"}, {"perm": "files"}):
        bad = json.loads(json.dumps(src))
        next(r for r in bad["rows"] if r["name"] == "theme.gen").update(wrong)
        with pytest.raises(gen_os_table.TableError):
            gen_os_table.check(bad)
    bad = json.loads(json.dumps(src))
    next(r for r in bad["rows"] if r["name"] == "carts.readable")["grant"] = "floor"
    with pytest.raises(gen_os_table.TableError):
        gen_os_table.check(bad)             # under a role no permission grants


def test_a_retired_row_answers_through_a_typed_stub():
    t = _table()
    t["rows"][2]["retired"] = "2027-01-01: an example"
    t["rows"][12]["retired"] = "2027-01-01: an example"
    c = gen_os_table.gen_kernel_c(t)
    assert "static ssize_t moy_os_retired_read(int fd, void *buf, size_t n) {" in c
    assert "static DIR *moy_os_retired_opendir(const char *path) {" in c
    assert "(void *)moy_os_retired_read," in c and "(void *)moy_os_read," not in c
    assert "    return NULL;" in c and "moy_os_errno_set(ENOSYS);" in c


def test_the_retired_rows_keep_their_slots_and_answer_enosys():
    """A service's timers and input readers went with the one service model
    (2026-11-08): their ordinals stay, each a typed stub, and neither the SDK's
    header nor its archive offers them to a module built now."""
    rows = _table()["rows"]
    c = open(gen_os_table.KERNEL_C).read()
    sdk = open(gen_os_table.SDK_H).read()
    stubs = open(gen_os_table.STUBS_C).read()
    for i, name in ((38, "moy_timer_every"), (39, "moy_timer_cancel"), (40, "moy_events")):
        assert rows[i]["name"] == name and rows[i]["retired"].startswith("2026-11-08")
        assert "(void *)moy_os_retired_%s," % name in c
        assert name + "(" not in sdk and "int %s(" % name not in stubs


def test_the_sdk_s_log_levels_and_event_record_are_the_kernel_s():
    sdk = open(gen_os_table.SDK_H).read()
    log = open(os.path.join(ROOT, "native", "moy_kernel", "moy_log.h")).read()
    names = re.search(r"MOY_LOG_EMERG = 0,([^}]*)MOY_LOG_LEVELS", log).group(1)
    order = ["MOY_LOG_EMERG"] + [n.strip() for n in names.split(",") if n.strip()]
    for name, value in re.findall(r"#define (MOY_LOG_\w+) (\d+)", sdk):
        assert order.index(name) == int(value), name
    assert gen_os_table.evq_block() in sdk


@pytest.fixture(scope="module")
def check():
    """moy_elf_check from the host's one library."""
    from runtime import host_native
    path = host_native.build()
    if path is None:
        pytest.skip("no host library")
    d = ctypes.CDLL(path)
    f = d.moy_elf_check
    f.argtypes = [ctypes.c_char_p, ctypes.c_size_t, ctypes.c_uint16, ctypes.c_uint32,
                  ctypes.c_int, ctypes.c_void_p, ctypes.c_char_p, ctypes.c_size_t]
    f.restype = ctypes.c_int

    def run(data, machine, rows):
        info = (ctypes.c_uint32 * 3)()
        err = ctypes.create_string_buffer(160)
        r = f(data, len(data), machine, rows, 0, info, err, len(err))
        return (None if r == 0 else err.value.decode()), tuple(info)
    return run


@pytest.mark.parametrize("chip", sorted(MACHINE))
def test_a_module_for_each_chip_passes_its_verdict(chip, check, tmp_path):
    if not elf_cart.available(chip):
        pytest.skip("no %s toolchain" % chip)
    src = sorted(os.path.join(FIXTURES, "elf_probe.moy", "src", n)
                 for n in os.listdir(os.path.join(FIXTURES, "elf_probe.moy", "src")))
    mod = elf_cart.build_module(src, chip, str(tmp_path / "m.elf"), signed=False)
    data = open(mod, "rb").read()
    rows = len(_table()["rows"])
    why, (built, highest, n) = check(data, MACHINE[chip], rows)
    assert why is None and built == rows and 0 < n and highest < rows
    other = [m for c, m in MACHINE.items() if c != chip][0]
    assert check(data, other, rows)[0] == "built for another chip"
    assert check(data, MACHINE[chip], highest)[0].startswith("needs a console update")
    # It imports nothing: everything it calls in the OS is a row.
    _cc, _ar, strip = elf_cart.toolchain(chip)
    readelf = strip[:-len("strip")] + "readelf"
    syms = subprocess.run([readelf, "--dyn-syms", "-W", mod], capture_output=True,
                          text=True).stdout
    assert [ln for ln in syms.splitlines() if re.search(r"\sUND\s+\S+$", ln)] == []


@pytest.mark.parametrize("chip", sorted(MACHINE))
def test_a_signed_module_carries_the_native_scheme(chip, tmp_path):
    if not elf_cart.available(chip):
        pytest.skip("no %s toolchain" % chip)
    from tools import wasm_module
    src = [os.path.join(FIXTURES, "elf_hello.moy", "src", "main.c")]
    signed = []
    mod = elf_cart.build_module(src, chip, str(tmp_path / "m.elf"), signed=True,
                                sign_with=lambda text: signed.append(text) or b"s" * 256)
    body, sig = wasm_module.split(open(mod, "rb").read())
    assert sig == b"s" * 256
    assert signed == [wasm_module.signed_text(body, chip, elf_cart.sig_scheme())]
    assert signed[0].startswith(b"moybyte-elf-sig 1\n%s\n" % chip.encode())


def test_a_service_s_device_permissions_are_the_handle_rows():
    """A service granted only devices still gets a grant to open them under
    (system_api.service_api): its list is the dev rows' permissions."""
    from runtime.shell import system_api
    row = next(r for r in gen_os_table.load()["rows"] if r["name"] == "moybyte.dev_open")
    assert set(system_api.DEVICE_PERMS) == {p.split(":")[0] for p in row["perm"]}


def test_one_error_table():
    """native/os_calls.json's errors are the app ABI's codes and every face's
    answer to one: the C enum and the texts are generated (moy_app_err.h,
    runtime/system/face_errors.py), so no face restates a message by hand, and
    the console's NO_STORE says the table's text."""
    from runtime import face_errors
    from runtime.shell import app_context
    errs = gen_os_table.load()["errors"]
    texts = {e[k] for e in errs for k in ("msg", "lua", "err") if e.get(k)}
    faces = [os.path.join(ROOT, *p.split("/")) for p in (
        "native/moy_app/modmoy_app.c", "native/moy_app/moy_app_lua.c",
        "runtime/host/moy_app_binding.py")]
    for path in faces:
        src = open(path).read()
        for t in texts:
            assert '"%s"' % t not in src, "%s restates %r: take it from the errors" % (path, t)
    for e in errs:
        assert getattr(face_errors, e["code"]) == e["n"]
        if e["code"] == "OK":
            continue
        exc = face_errors.exception(e["n"])
        assert type(exc).__name__ == e["py"], e["code"]
        if e["py"] == "OSError":
            assert exc.errno == getattr(__import__("errno"), e["errno"])
    nostore = next(e for e in errs if e["code"] == "NOSTORE")
    assert str(app_context.NO_STORE) == nostore["err"]
    assert type(face_errors.exception(99)) is type(face_errors.exception(face_errors.BAD))


# What the call table's codes and rows guard (docs/refactor_2026-11.md, phase
# 10): the sources the tree writes, the vendored copies and the generated
# files apart.
VENDORED = re.compile(r"/(lua|dash|dropbear|mbedtls|wamr|libmoy|monocypher|elf_loader)/")


def _our_c():
    """{path: text} of every C file of native/ the tree writes by hand."""
    made = {os.path.relpath(p, ROOT) for p, _t in gen_os_table.outputs()}
    files = subprocess.run(["git", "ls-files", "native"], cwd=ROOT, capture_output=True,
                           text=True, check=True).stdout.split()
    out = {}
    for f in files:
        if f.endswith((".c", ".h")) and not VENDORED.search("/" + f) and f not in made:
            with open(os.path.join(ROOT, f), errors="replace") as fh:
                out[f] = fh.read()
    return out


def test_no_hand_code_dispatches_a_row_or_maps_an_app_code():
    """The door's rows are MOY_APP_DOOR_ROWS, a code's errno and texts the
    errors' columns: no hand switch names a C row or an app code."""
    bad = ["%s:%d" % (f, t[:m.start()].count("\n") + 1)
           for f, t in _our_c().items() for m in re.finditer(r"case MOY_(ROW|APP)_[A-Z]", t)]
    assert bad == []


def test_every_module_s_ok_is_zero():
    """moy_kernel.h's RESULT CODES: every MOY_<M>_OK is 0 (or another's OK)."""
    bad = []
    for f, t in _our_c().items():
        for m in re.finditer(r"\b(MOY\w*_OK)\s*=\s*([^,}\s]+)", t):
            if m.group(2) != "0" and not m.group(2).endswith("_OK"):
                bad.append("%s: %s = %s" % (f, m.group(1), m.group(2)))
    assert bad == []


def test_lua_s_role_binding_has_no_per_row_function_but_the_hand_ones():
    """A Lua name is l_row over its row (moy_app_lua.c), but the rows the table
    marks hand; the rest of moy_app_lua.c's functions are io's and os's."""
    hand = {cfn for _r, _t, _n, cfn, row, _rd in gen_os_table.lua_names(gen_os_table.load())
            if any(r["name"] == row and r.get("lua_ret") == "hand"
                   for r in gen_os_table.load()["rows"])}
    with open(os.path.join(ROOT, "native", "moy_app", "moy_app_lua.c")) as f:
        fns = set(re.findall(r"^static int (l_\w+)\(lua_State \*L\)", f.read(), re.M))
    assert hand and hand <= fns
    assert {f for f in fns - hand if not f.startswith(("l_io_", "l_os_"))} == {"l_row"}


def test_the_evq_errors_are_moy_evq_h_s_codes():
    """Every code moy_evq.h numbers has its row in evq_errors, and no other."""
    with open(os.path.join(ROOT, "native", "moy_kernel", "moy_evq.h")) as f:
        codes = set(re.findall(r"^\s+MOY_EVQ_([A-Z]+) = \d+,", f.read(), re.M)) - {"OK"}
    assert {e["code"] for e in gen_os_table.load()["evq_errors"]} == codes
