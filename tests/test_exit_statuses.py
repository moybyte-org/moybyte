"""The exit statuses the OS gives a process are defined once, in the process
table's header (native/moy_kernel/moy_proc_exit.h): the C includes it, and
Python reads the values through moy_log's binding."""

import re
from pathlib import Path

from runtime import moy_log
from runtime import cron, scheduler, sh_door, sh_roles

ROOT = Path(__file__).resolve().parents[1]
HEADER = ROOT / "native" / "moy_kernel" / "moy_proc_exit.h"
NAMES = ("NOT_RUN", "INTERRUPTED", "TRAPPED", "KILLED")

# Where a process's end is decided: none of these may spell a status itself.
C_FILES = (sorted((ROOT / "native" / "moy_sh").glob("moy_*.[ch]"))
           + sorted((ROOT / "native" / "moy_wasm").glob("*.[ch]"))
           + sorted((ROOT / "native" / "moy_elf").glob("*.[ch]"))
           + sorted((ROOT / "native" / "moy_kernel").glob("*.[ch]"))
           + sorted((ROOT / "native" / "moycore").glob("moycore_svc.[ch]")))
PY_FILES = (sorted(Path(sh_door.__file__).parent.glob("sh_*.py"))
            + [Path(cron.__file__), Path(scheduler.__file__)])


def header():
    return {m.group(1): int(m.group(2)) for m in re.finditer(
        r"#define MOY_PROC_(\w+) (\d+)", HEADER.read_text())}


def test_the_header_holds_the_shell_s_codes():
    assert header() == {"NOT_RUN": 126, "INTERRUPTED": 130, "TRAPPED": 134, "KILLED": 137}


def test_python_reads_them_through_the_binding():
    want = header()
    for name in NAMES:
        assert getattr(moy_log, name) == want[name], name
    assert (sh_door.NOT_RUN, sh_door.INTERRUPTED, sh_door.TRAPPED, sh_door.KILLED) == \
        tuple(want[n] for n in NAMES)
    assert sh_roles.INTERRUPTED == want["INTERRUPTED"]
    assert cron.NOT_RUN == want["NOT_RUN"]
    from runtime import scheduler
    assert scheduler.EXIT_KILLED == want["KILLED"]


def test_no_other_file_defines_one():
    codes = "|".join(str(v) for v in header().values())
    c_def = re.compile(r"#define \w+ \(?(%s)\)?\b|return (%s);" % (codes, codes))
    py_def = re.compile(r"^\s*\w+(\s*,\s*\w+)*\s*=\s*(%s)\b|return (%s)$" % (codes, codes))
    found = []
    for path in C_FILES:
        if path == HEADER:
            continue
        for n, line in enumerate(path.read_text(errors="replace").splitlines(), 1):
            if c_def.search(line):
                found.append("%s:%d: %s" % (path.relative_to(ROOT), n, line.strip()))
    for path in PY_FILES:
        for n, line in enumerate(path.read_text().splitlines(), 1):
            if py_def.search(line.split("#")[0].rstrip()):
                found.append("%s:%d: %s" % (path.relative_to(ROOT), n, line.strip()))
    assert not found, "\n".join(found)
