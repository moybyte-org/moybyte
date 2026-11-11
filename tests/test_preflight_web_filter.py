"""preflight.sh's `web_touched` is web-e2e.yml's `paths:` list, and its
`firmware_touched` firmware-build.yml's, word for word; its browser lane runs
the suites web-e2e.yml's drive step names, as that step runs them; and that
step names every suite gated on MOYBYTE_WEB_E2E."""

import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _workflow(name="web-e2e.yml"):
    text = (ROOT / ".github" / "workflows" / name).read_text()
    out = []
    for block in re.findall(r"^    paths:\n((?:      (?:- .*|#.*)\n)+)", text, re.M):
        out.append(set(re.findall(r'- "([^"]+)"', block)))
    return out


def _script(fn="web_touched"):
    text = (ROOT / "tools" / "preflight.sh").read_text()
    body = text[text.index(fn + "() {"):]
    body = body[:body.index("\n}\n")]
    pats = set()
    for line in re.findall(r"^      (\S.*\)) return 0 ;;$", body, re.M):
        pats.update(line.rstrip(")").split("|"))
    return pats


def test_the_web_lane_filter_is_the_workflows():
    push, pull = _workflow()
    assert push == pull
    want = {p[:-1] if p.endswith("/**") else p for p in push}
    assert _script() == want


def test_the_firmware_lane_filter_is_the_workflows():
    (push,) = _workflow("firmware-build.yml")
    want = {p[:-1] if p.endswith("/**") else p for p in push}
    assert _script("firmware_touched") == want


def test_the_browser_lane_runs_the_workflows_suites():
    text = (ROOT / ".github" / "workflows" / "web-e2e.yml").read_text()
    drive = text[text.index("- name: Drive the hosted console in real Chrome"):]
    drive = drive[:drive.index("- name:", 1)]
    want = re.findall(r"tests/\w+\.py", drive)
    assert len(want) > 5 and 'MOYBYTE_REQUIRE_WEB_E2E: "1"' in text
    script = (ROOT / "tools" / "preflight.sh").read_text()
    fn = script[script.index("web_suites() {"):]
    fn = fn[:fn.index("\n}\n") + 3]
    got = subprocess.run(["bash", "-c", fn + "web_suites"], cwd=ROOT, check=True,
                         capture_output=True, text=True).stdout.split()
    assert got == want
    lane = script[script.index("browser_suites() {"):]
    lane = lane[:lane.index("\n}\n")]
    assert "< <(web_suites)" in lane and '"${suites[@]}"' in lane
    assert "MOYBYTE_REQUIRE_WEB_E2E=1" in lane and "skipped" in lane
    assert 'step "browser suites in real Chrome, none skipped" browser_suites' in script


def test_every_browser_suite_is_on_the_drive_step():
    """A suite gated on MOYBYTE_WEB_E2E runs nowhere else: one left off the
    drive step rots unseen (first_run and desk_input did)."""
    text = (ROOT / ".github" / "workflows" / "web-e2e.yml").read_text()
    drive = text[text.index("- name: Drive the hosted console in real Chrome"):]
    drive = drive[:drive.index("- name:", 1)]
    named = set(re.findall(r"tests/(\w+\.py)", drive))
    gated = {p.name for p in (ROOT / "tests").glob("test_*.py")
             if p.name != Path(__file__).name
             and "MOYBYTE_WEB_E2E" in p.read_text(encoding="utf-8")}
    assert gated - named == set(), "add them to web-e2e.yml's drive step"
    assert {p.name for p in (ROOT / "tests").glob("test_web_*_e2e.py")} <= named
