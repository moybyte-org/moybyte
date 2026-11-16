"""tools/vendor_advisories.py reads its sources and reaches its verdict as it says.

No network: the three sources' answers are real responses recorded under
tests/fixtures/vendor_advisories/ (trimmed to the fields the tool reads), served
through a fake fetch. What stays executable here is the parsing, the version
ranges, the merge across sources, the acknowledgement file and the exit status.
"""

import json
import os

import pytest

import tools.vendor_advisories as va

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIX = os.path.join(ROOT, "tests", "fixtures", "vendor_advisories")


def fixture(name):
    with open(os.path.join(FIX, name)) as f:
        return json.load(f)


class FakeFetch:
    """Answers each request from a table of (method, url-substring or body) -> response."""

    def __init__(self, routes):
        self.routes = routes
        self.asked = []

    def __call__(self, method, url, body, headers):
        self.asked.append((method, url, body))
        for match, answer in self.routes:
            if callable(match) and match(method, url, body):
                break
            if isinstance(match, str) and match in url + json.dumps(body or {}):
                break
        else:
            return [] if "api.github.com" in url else {}
        if isinstance(answer, Exception):
            raise answer
        return answer


def subject(tmp_path, pin, **kw):
    path = tmp_path / "x_vendor.json"
    path.write_text(json.dumps(pin))
    return dict({"pin": "x_vendor.json", "name": "X"}, **kw)


# -- versions ---------------------------------------------------------------

@pytest.mark.parametrize("version,spec,hit", [
    ("2.4.5", "<=2.4.4", False),
    ("2.4.4", "<=2.4.4", True),
    ("2.4.2", " 2.4.2", True),
    ("2.4.3", "2.4.2", False),
    ("2.1.0", "< 2.2.0", True),
    ("1.5", ">= 1.0, < 2.0", True),
    ("2.0", ">= 1.0, < 2.0", False),
    ("2026.94", "< 2025.88", False),
    ("2024.85", "< 2025.88", True),
])
def test_github_version_ranges(version, spec, hit):
    assert va.in_range(version, spec) is hit


def test_version_key_reads_tags_and_releases_alike():
    assert va.vkey("WAMR-2.4.5") == va.vkey("2.4.5") == va.vkey("v2.4.5") == (2, 4, 5)
    assert va.vkey("DROPBEAR_2024.85") < va.vkey("2025.88")


# -- the three parsers over recorded responses ------------------------------

def test_osv_response_keeps_ids_and_aliases():
    found = va.osv_findings(fixture("osv_dropbear_2024.85.json"))
    ids = sorted(i for f in found for i in f["ids"])
    assert "CVE-2025-47203" in ids and "CVE-2025-14282" in ids
    assert all(f["summary"] for f in found)
    assert va.osv_findings({}) == []


def test_nvd_response_drops_rejected_cves():
    r = fixture("nvd_mbedtls_3.6.4.json")
    found = va.nvd_findings(r)
    assert len(found) == len(r["vulnerabilities"]) > 0
    assert {"CVE-2026-34876"} in [f["ids"] for f in found]
    r["vulnerabilities"][0]["cve"]["vulnStatus"] = "Rejected"
    assert len(va.nvd_findings(r)) == len(found) - 1


def test_github_advisories_match_on_range_then_patched():
    adv = fixture("github_wamr.json")
    assert va.github_findings(adv, "2.4.5") == []
    hit = sorted(i for f in va.github_findings(adv, "2.4.1") for i in f["ids"])
    assert "CVE-2025-58749" in hit and "GHSA-xj5p-r8jq-pw47" in hit
    assert "CVE-2025-54126" not in hit


def test_github_advisory_without_range_or_version_is_shown():
    bare = [{"ghsa_id": "GHSA-aaaa-bbbb-cccc", "cve_id": None, "summary": "s",
             "vulnerabilities": []}]
    assert len(va.github_findings(bare, "9.9")) == 1
    ranged = [{"ghsa_id": "GHSA-aaaa-bbbb-cccc", "summary": "s",
               "vulnerabilities": [{"vulnerable_version_range": "<= 1.0"}]}]
    assert len(va.github_findings(ranged, None)) == 1
    patched_only = [{"ghsa_id": "GHSA-aaaa-bbbb-cccc", "summary": "s",
                     "vulnerabilities": [{"vulnerable_version_range": "",
                                          "patched_versions": "2.0"}]}]
    assert va.github_findings(patched_only, "2.0") == []
    assert len(va.github_findings(patched_only, "1.9")) == 1
    withdrawn = [dict(bare[0], withdrawn_at="2026-01-01T00:00:00Z")]
    assert va.github_findings(withdrawn, "9.9") == []


def test_github_package_filter_skips_other_components():
    adv = [{"ghsa_id": "GHSA-aaaa-bbbb-cccc", "summary": "s",
            "vulnerabilities": [{"package": {"name": "usb_host_uvc"},
                                 "vulnerable_version_range": "<= 9"}]}]
    assert va.github_findings(adv, "2.1.0", package="elf_loader") == []
    adv[0]["vulnerabilities"][0]["package"]["name"] = "espressif/elf_loader"
    assert len(va.github_findings(adv, "2.1.0", package="elf_loader")) == 1


# -- one pin, every source --------------------------------------------------

def test_pin_merges_one_cve_seen_by_two_sources(tmp_path):
    s = subject(tmp_path, {"version": "2024.85"},
                version="version",
                osv_git=("https://github.com/mkj/dropbear", ["DROPBEAR_{v}"]),
                nvd=["cpe:2.3:a:p:dropbear:{v}"], github=["mkj/dropbear"])
    nvd = {"resultsPerPage": 1, "startIndex": 0, "totalResults": 1,
           "vulnerabilities": [{"cve": {"id": "CVE-2025-47203", "descriptions": []}}]}
    fetch = FakeFetch([("DROPBEAR_2024.85", fixture("osv_dropbear_2024.85.json")),
                       ("virtualMatchString", nvd)])
    r = va.check_pin(s, va.Sources(fetch, pace=False), root=str(tmp_path))
    assert r["errors"] == []
    byid = {f["id"]: f for f in r["findings"]}
    assert set(byid) == {"CVE-2025-47203", "CVE-2025-14282"}
    assert byid["CVE-2025-47203"]["sources"] == {"osv", "nvd"}
    assert len(r["asked"]) == 3


def test_ghsa_and_its_cve_are_one_finding():
    merged = va.merge([{"ids": {"GHSA-aaaa-bbbb-cccc"}, "summary": "", "sources": {"github"}},
                       {"ids": {"CVE-2026-1", "GHSA-aaaa-bbbb-cccc"}, "summary": "x",
                        "sources": {"osv"}},
                       {"ids": {"CVE-2026-1"}, "summary": "", "sources": {"nvd"}}])
    assert len(merged) == 1
    assert merged[0]["id"] == "CVE-2026-1"
    assert merged[0]["sources"] == {"github", "osv", "nvd"}


def test_paging_follows_every_source(tmp_path):
    s = subject(tmp_path, {"v": "1.0", "c": "abc"}, version="v", commit="c",
                nvd=["cpe:{v}"], github=["o/r"])
    page = [{"ghsa_id": "GHSA-%04d-bbbb-cccc" % i, "summary": "", "vulnerabilities": []}
            for i in range(100)]
    nvd1 = {"resultsPerPage": 1, "startIndex": 0, "totalResults": 2,
            "vulnerabilities": [{"cve": {"id": "CVE-2026-1"}}]}
    nvd2 = {"resultsPerPage": 1, "startIndex": 1, "totalResults": 2,
            "vulnerabilities": [{"cve": {"id": "CVE-2026-2"}}]}
    osv1 = {"vulns": [{"id": "OSV-1"}], "next_page_token": "t"}
    osv2 = {"vulns": [{"id": "OSV-2"}]}
    fetch = FakeFetch([
        ("&page=1", page), ("&page=2", page[:1]),
        ("startIndex=0", nvd1), ("startIndex=1", nvd2),
        (lambda m, u, b: b and "page_token" not in b, osv1),
        (lambda m, u, b: b and "page_token" in b, osv2)])
    r = va.check_pin(s, va.Sources(fetch, pace=False), root=str(tmp_path))
    ids = {f["id"] for f in r["findings"]}
    assert {"CVE-2026-1", "CVE-2026-2", "OSV-1", "OSV-2"} <= ids
    assert len([i for i in ids if i.startswith("GHSA")]) == 100


def test_a_failed_source_is_named_and_others_still_answer(tmp_path):
    s = subject(tmp_path, {"v": "1.0"}, version="v", nvd=["cpe:{v}"], github=["o/r"])
    fetch = FakeFetch([("virtualMatchString", va.SourceError("HTTP 503"))])
    r = va.check_pin(s, va.Sources(fetch, pace=False), root=str(tmp_path))
    assert len(r["errors"]) == 1 and "503" in r["errors"][0]
    assert r["asked"] == ["github o/r"]


# -- acknowledgements and the verdict ---------------------------------------

def ack(pin="x_vendor.json", id="CVE-2026-1", **kw):
    return dict({"pin": pin, "id": id, "date": "2026-11-15",
                 "reason": "the vendored subset has no such code"}, **kw)


def result(pin, *ids, errors=()):
    return {"subject": {"pin": pin, "name": "X"}, "pin": {"version": "1", "commits": []},
            "asked": [], "errors": list(errors),
            "findings": [{"id": i, "ids": {i}, "summary": "", "sources": {"nvd"}} for i in ids]}


def test_verdict_counts_only_unacknowledged_findings():
    rs = [result("x_vendor.json", "CVE-2026-1", "CVE-2026-2")]
    assert va.verdict(rs, [ack()]) == (1, [])
    assert va.verdict(rs, [ack(), ack(id="CVE-2026-2")]) == (0, [])


def test_an_acknowledgement_holds_for_its_own_pin_only():
    rs = [result("y_vendor.json", "CVE-2026-1")]
    new, stale = va.verdict(rs, [ack()])
    assert new == 1 and stale == []


def test_an_acknowledgement_nobody_reports_is_stale_unless_a_source_failed():
    a = ack(id="CVE-2026-9")
    assert va.verdict([result("x_vendor.json")], [a]) == (0, [a])
    assert va.verdict([result("x_vendor.json", errors=["nvd: down"])], [a]) == (0, [])


def test_an_acknowledgement_may_name_any_alias():
    rs = [result("x_vendor.json", "CVE-2026-1")]
    rs[0]["findings"][0]["ids"].add("GHSA-aaaa-bbbb-cccc")
    assert va.verdict(rs, [ack(id="GHSA-aaaa-bbbb-cccc")])[0] == 0


@pytest.mark.parametrize("bad,what", [
    ({"pin": "nope_vendor.json"}, "not a vendor pin"),
    ({"id": "bug 12"}, "not an advisory id"),
    ({"date": "15.11.2026"}, "YYYY-MM-DD"),
    ({"reason": " "}, "says nothing"),
])
def test_acknowledgement_file_refuses_a_bad_entry(tmp_path, bad, what):
    subjects = [{"pin": "x_vendor.json"}]
    path = tmp_path / "ack.json"
    path.write_text(json.dumps({"acknowledged": [ack(**bad)]}))
    _, problems = va.load_acks(str(path), subjects)
    assert len(problems) == 1 and what in problems[0]


def test_exit_status(tmp_path, monkeypatch, capsys):
    path = tmp_path / "ack.json"
    path.write_text(json.dumps({"acknowledged": []}))
    s = subject(tmp_path, {"v": "1.0"}, version="v", nvd=["cpe:{v}"])
    pins = [s["pin"]]
    monkeypatch.setattr(va, "tracked_pins", lambda root: pins)

    def run(answer):
        fetch = FakeFetch([("virtualMatchString", answer)])
        return va.main(["--ack", str(path)], va.Sources(fetch, pace=False),
                       str(tmp_path), [s])

    one = {"resultsPerPage": 1, "startIndex": 0, "totalResults": 1,
           "vulnerabilities": [{"cve": {"id": "CVE-2026-1"}}]}
    assert run({"resultsPerPage": 0, "totalResults": 0}) == 0
    assert run(one) == 1
    assert "NEW  CVE-2026-1" in capsys.readouterr().out
    path.write_text(json.dumps({"acknowledged": [ack()]}))
    assert run(one) == 0
    assert run(va.SourceError("HTTP 503")) == 2
    pins.append("new_vendor.json")
    assert run(one) == 2
    assert "no entry in SUBJECTS" in capsys.readouterr().err


# -- the checked-in table and file ------------------------------------------

def test_every_vendor_pin_has_a_subject():
    assert set(va.tracked_pins()) == {s["pin"] for s in va.SUBJECTS}


@pytest.mark.parametrize("s", va.SUBJECTS, ids=lambda s: s["name"])
def test_every_subject_reads_its_pin_and_asks_something(s):
    pin = va.read_pin(s)
    if s.get("version"):
        assert pin["version"] and va.vkey(pin["version"])
    if s.get("commit"):
        assert all(len(c) == 40 for c in pin["commits"])
    assert va.queries(s, pin)


def test_the_checked_in_acknowledgements_parse():
    _, problems = va.load_acks()
    assert problems == []


def test_the_workflow_runs_weekly_and_on_demand_never_per_push():
    with open(os.path.join(ROOT, ".github", "workflows", "vendor-advisories.yml")) as f:
        text = f.read()
    on = text.split("\non:\n", 1)[1].split("\n\n", 1)[0]
    triggers = {line.strip().rstrip(":") for line in on.splitlines()
                if line.startswith("  ") and not line.startswith("    ")}
    assert triggers == {"schedule", "workflow_dispatch"}
    assert "tools/vendor_advisories.py" in text
