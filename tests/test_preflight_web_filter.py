"""preflight.sh's `web_touched` is web-e2e.yml's `paths:` list, and its
`firmware_touched` firmware-build.yml's, word for word."""

import re
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
