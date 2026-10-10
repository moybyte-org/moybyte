"""The run path's calls into Python, held to native/moy_play/upcall_sites.txt
(docs/kernel_cartpath_2026-10.md §4), as tests/test_mp_task_calls.py holds
the kernel's copy of mp_task to its record: a site added, moved or removed in
those trees fails here until its row says what it is."""

import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RECORD = os.path.join(ROOT, "native", "moy_play", "upcall_sites.txt")
TREES = ("native/moy_kernel", "native/moy_play", "native/moycore", "native/moy_wasm",
         "native/moy_store", "native/moy_net", "native/moy_input")
# Vendored or host-only trees inside them: no crossing of ours lives there.
SKIP_DIRS = {"lua", "libmoy", "wamr", "host"}
CALL = re.compile(r"\b(mp_call_\w+|mp_sched_schedule)\s*\(")
DEF = re.compile(r"^(?:static\s+|inline\s+)*[A-Za-z_][\w\s\*]*?\b(\w+)\s*\([^;]*$")
KINDS = ("counted ", "inside ", "binding:", "vm:")


def scan(root=ROOT):
    """{(file, function): sites} over the trees, comments left out."""
    out = {}
    for tree in TREES:
        for dp, dn, fn in os.walk(os.path.join(root, tree)):
            dn[:] = sorted(d for d in dn if d not in SKIP_DIRS)
            for f in sorted(fn):
                if not f.endswith(".c"):
                    continue
                path = os.path.join(dp, f)
                rel = os.path.relpath(path, root).replace(os.sep, "/")
                fun = None
                with open(path, errors="replace") as fh:
                    for line in fh:
                        if line[:1].isalpha() and DEF.match(line):
                            fun = DEF.match(line).group(1)
                        n = len(CALL.findall(line.split("//")[0]))
                        if n:
                            out[(rel, fun)] = out.get((rel, fun), 0) + n
    return out


def read_record():
    rows = {}
    with open(RECORD) as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            f, fun, n, what = line.split(None, 3)
            assert (f, fun) not in rows, "listed twice: %s %s" % (f, fun)
            rows[(f, fun)] = (int(n), what)
    return rows


def test_every_site_is_listed_and_every_row_is_a_site():
    have = scan()
    rows = read_record()
    listed = {k: v[0] for k, v in rows.items()}
    added = sorted("%s %s %d" % (f, fn, n) for (f, fn), n in have.items()
                   if listed.get((f, fn)) != n)
    gone = sorted("%s %s" % k for k in listed if k not in have)
    assert not added and not gone, (
        "upcall_sites.txt is not the tree: write each new or changed site's row "
        "(and count it if it crosses)\n  tree: %s\n  record only: %s" % (added, gone))


def test_every_row_says_what_it_is():
    for (f, fun), (n, what) in read_record().items():
        assert n > 0 and what.startswith(KINDS), (f, fun, what)
        if what.startswith("inside "):
            host = what[len("inside "):].split(":")[0]
            src = open(os.path.join(ROOT, f)).read()
            assert re.search(r"\b%s\s*\(" % host, src), (f, fun, host)


def test_every_counted_row_names_a_class_the_loop_counts():
    """A `counted` row's classes are moy_loop.h's MOY_UPC_* (REFUSED is no
    crossing of its own), so a class renamed or a door added without its
    class fails here."""
    src = open(os.path.join(ROOT, "native", "moy_kernel", "moy_loop.h")).read()
    classes = set(re.findall(r"\bMOY_UPC_([A-Z]+) = \d+", src)) - {"CLASSES", "REFUSED"}
    assert "ROLE" in classes, classes
    seen = set()
    for (f, fun), (n, what) in read_record().items():
        if what.startswith("counted "):
            for cls in what[len("counted "):].split(":")[0].split():
                assert cls in classes, (f, fun, cls)
                seen.add(cls)
    assert "ROLE" in seen, "the ROLE door's site is not in the record"


def test_the_scanner_finds_a_site_it_was_not_told_of(tmp_path):
    d = tmp_path / "native" / "moy_play"
    d.mkdir(parents=True)
    (d / "x.c").write_text("static int f(void) {\n    return mp_call_function_0(o); // x\n}\n"
                           "// mp_call_function_0(no)\n")
    assert scan(str(tmp_path)) == {("native/moy_play/x.c", "f"): 1}
