"""The toolkit's context in groups (docs/refactor_2026-11.md, phase 8):
moy_ui_ctx_t keeps each verb family's state in a named sub-struct, each
declared with the file that owns it (native/moy_ui/moy_ui_api.h), so no
flat field grows back under a family's name and an engine file reaches only
the groups named for it here. And a widget that asks for kept state past
MOY_UI_API_KEEP ids is told, as a stop past MOY_UI_API_STOPS is."""

import collections
import glob
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
H = os.path.join(ROOT, "native", "moy_ui", "moy_ui_api.h")

# The groups an engine file reaches besides its own: the floor and the
# shift frame are every drawing verb's, the table's parts the run face's.
READS = {
    "moy_ui_api.c": {"bar", "fl", "kf", "pop", "sh", "spread", "tc", "tp", "txt"},
    "moy_ui_api_host.c": {"dlg", "dw", "pop"},
    "moy_ui_api_kept.c": {"tp"},
    "moy_ui_api_pop.c": {"fl", "tbl"},
    "moy_ui_api_table.c": {"dlg", "dw", "fl", "kf", "sh"},
    "moy_ui_api_text.c": {"fl", "sh"},
    "moy_ui_run.c": {"tp"},
}


def _groups():
    src = open(H).read()
    return dict(re.findall(r"moy_ui_ctx_t's `(\w+)`,\s*(?:\n//\s*)?owned by (moy_ui_api\w*\.c)",
                           src))


def _sources():
    for pat in ("native/moy_ui/*.c", "native/moy_ui/*.h", "native/moy_sh/*.c",
                "native/moycore/*.h", "sdk/src/*.c"):
        yield from sorted(glob.glob(os.path.join(ROOT, pat)))


def test_every_group_names_an_owner_that_exists():
    groups = _groups()
    assert len(groups) >= 12, groups
    for g, owner in groups.items():
        assert os.path.exists(os.path.join(ROOT, "native", "moy_ui", owner)), (g, owner)
        assert re.search(r"\bmoy_ui_%s_t %s;" % (g, g), open(H).read()), g


def test_no_flat_field_carries_a_groups_name():
    src = open(H).read()
    body = src[src.index("    uint32_t magic;"):src.index("} moy_ui_ctx_t;")]
    code = "\n".join(ln.split("//")[0] for ln in body.splitlines())
    names = "|".join(_groups())
    flat = re.findall(r"\b((?:%s)_\w+)\s*[,;\[]" % names, code)
    assert not flat, "a group's state belongs in its sub-struct: %s" % flat


def test_each_engine_file_reaches_only_its_groups_and_those_named():
    groups = _groups()
    reach = collections.defaultdict(set)
    for path in _sources():
        f = os.path.basename(path)
        src = open(path).read()
        for g, owner in groups.items():
            if owner != f and re.search(r"\bc->%s\." % g, src):
                reach[f].add(g)
    extra = {f: sorted(gs - READS.get(f, set())) for f, gs in reach.items()
             if gs - READS.get(f, set())}
    assert not extra, ("a file reaching into another file's group: move the code to the "
                       "owner, or name the read in READS: %s" % extra)


LISTS = """
LOST = 0


def _draw():
    global LOST
    cls(0)
    ui.page("Keep")
    for i in range(16):
        ui.at(0, 30 + i * 4, 60, 40)
        ui.list("L%d" % i, ["a", "b"], 0)
"""


def test_seventeen_kept_ids_in_one_context_are_told(tmp_path, capsys):
    """The page and sixteen lists ask for seventeen kept ids: the last is
    drawn with none and counted, and the host says so once."""
    from test_app_field_typing import _open
    _ws, drv = _open(tmp_path, "Keep", LISTS, "python")
    for _ in range(3):
        drv.frame(1 / 30)
    said = [ln for ln in capsys.readouterr().out.splitlines() if "kept state" in ln]
    assert said and said[0].startswith("ui: 1 widgets asked for kept state"), said
    assert len(said) == 1, "told once per count"
