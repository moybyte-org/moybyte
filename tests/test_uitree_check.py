"""The checker (tools/uitree_check.py): every ported surface checks clean,
and one run reports every planted mistake with its code and file:line."""

import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tests" / "uitree_fixtures"))

import uitree_check  # noqa: E402
from board_config import runtime_file  # noqa: E402

# Every module written against uitree. A port adds its module here.
PORTED = (
    "uitree_counter",
    "uitree_roles",
    "uitree_kinds",
    "uitree_foot",
    "perms_ui",
    "settings_ui",
    "uitree_focus",
    "update_ui",
    "settings_rows",
    "log_ui",
    "web_console_ui",
    "about_ui",
    "tasks_ui",
    "achievements_ui",
    "wifi_ui",
    "uitree_editor_app",
    "code_ui",
    "editor_ui",
    "uitree_bar_row",
    "uitree_legacy_app",
    "uitree_placed",
    "uitree_text_editor",
)


@pytest.mark.parametrize("module", PORTED)
def test_every_ported_surface_checks_clean(module):
    r = uitree_check.check(module)
    assert not r.errors, r.text()
    assert r.pages and r.nodes


def test_one_run_reports_every_planted_mistake_at_its_line():
    path = ROOT / "tests" / "uitree_fixtures" / "uitree_mistakes.py"
    want = {}
    for n, line in enumerate(path.read_text().splitlines(), 1):
        m = re.search(r"# ([A-Z]\d\d)$", line)
        if m:
            want[(m.group(1), n)] = line.strip()
    r = uitree_check.check(str(path))
    got = {(e.code, e.where[1]) for e in r.errors if e.where[0] == path.name}
    assert set(want) <= got, "missed:\n%s\n\nreported:\n%s" % (
        "\n".join("%s:%d %s" % (c, n, want[(c, n)]) for c, n in sorted(set(want) - got)), r.text())
    for e in r.errors:
        assert e.fix, "an error without its fix:\n" + e.text()


def test_every_ported_module_is_listed():
    """A module that imports uitree is listed in PORTED, so the checker runs over it."""
    mods = set()
    for p in list((ROOT / "runtime").rglob("*.py")) + list((ROOT / "tests" / "uitree_fixtures").glob("*.py")):
        if p.parent.name == "uitree" or p.name == "uitree_mistakes.py":
            continue
        if re.search(r"^import uitree as ui$", p.read_text(encoding="utf-8"), re.M):
            mods.add(p.stem)
    assert mods == set(PORTED), sorted(mods ^ set(PORTED))


def test_every_error_the_layer_raises_carries_its_fix():
    """R3: an error says how to fix it. Each `UIError(...)` in the layer and
    the checker names `fix=`, and never a bare None."""
    import ast
    bad = []
    for p in sorted(runtime_file("uitree.py").parent.glob("*.py")) + [ROOT / "tools" / "uitree_check.py"]:
        for n in ast.walk(ast.parse(p.read_text(encoding="utf-8"))):
            if isinstance(n, ast.Call) and getattr(n.func, "id", getattr(n.func, "attr", "")) == "UIError":
                fix = [k.value for k in n.keywords if k.arg == "fix"]
                code = n.args[0].value if n.args and isinstance(n.args[0], ast.Constant) else "?"
                if not fix or isinstance(fix[0], ast.Constant) and fix[0].value is None or (
                        isinstance(fix[0], ast.IfExp) and isinstance(fix[0].orelse, ast.Constant)
                        and fix[0].orelse.value is None):
                    bad.append("%s:%d %s" % (p.name, n.lineno, code))
    assert not bad, bad
