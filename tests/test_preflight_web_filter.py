"""preflight.sh's `web_touched` is web-e2e.yml's `paths:` list, word for word."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _workflow():
    text = (ROOT / ".github" / "workflows" / "web-e2e.yml").read_text()
    out = []
    for block in re.findall(r"^    paths:\n((?:      - .*\n)+)", text, re.M):
        out.append(set(re.findall(r'- "([^"]+)"', block)))
    return out


def _script():
    text = (ROOT / "tools" / "preflight.sh").read_text()
    body = text[text.index("web_touched() {"):]
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
