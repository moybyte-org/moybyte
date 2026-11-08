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


def test_a_retired_row_answers_through_a_typed_stub():
    t = _table()
    t["rows"][2]["retired"] = "2027-01-01: an example"
    t["rows"][12]["retired"] = "2027-01-01: an example"
    c = gen_os_table.gen_kernel_c(t)
    assert "static ssize_t moy_os_retired_read(int fd, void *buf, size_t n) {" in c
    assert "static DIR *moy_os_retired_opendir(const char *path) {" in c
    assert "(void *)moy_os_retired_read," in c and "(void *)moy_os_read," not in c
    assert "    return NULL;" in c and "moy_os_errno_set(ENOSYS);" in c


def test_the_sdk_s_log_levels_and_event_record_are_the_kernel_s():
    sdk = open(gen_os_table.SDK_H).read()
    log = open(os.path.join(ROOT, "native", "moy_kernel", "moy_log.h")).read()
    names = re.search(r"MOY_LOG_EMERG = 0,([^}]*)MOY_LOG_LEVELS", log).group(1)
    order = ["MOY_LOG_EMERG"] + [n.strip() for n in names.split(",") if n.strip()]
    for name, value in re.findall(r"#define (MOY_LOG_\w+) (\d+)", sdk):
        assert order.index(name) == int(value), name
    assert gen_os_table._event_block() in sdk


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
