#!/usr/bin/env python3
"""Ask public advisory sources which known vulnerabilities affect each vendor pin.

    python3 tools/vendor_advisories.py              # every pin; stdlib only
    python3 tools/vendor_advisories.py --only dropbear

Every `*_vendor.json` in the tree is a pin: the upstream release or commit a
vendored copy was made from. SUBJECTS below says, per pin, where its version
and commit sit in that file and which sources can speak for it:

  osv     OSV.dev's query API (https://api.osv.dev/v1/query): by git tag in the
          upstream repository and by the pinned commit. Its C coverage is the
          repositories that appear in some advisory's git ranges.
  nvd     NVD's CVE API, matched on the product's CPE at the pinned version.
          OSV leaves most of mbedTLS's CVEs out of its git ranges; NVD has them.
  github  The upstream repository's published security advisories, matched on
          their version ranges. A C project's advisory reaches neither OSV nor
          GitHub's global database without a package ecosystem.

The findings of one pin are merged across sources by their aliases (a CVE and
the GHSA that names it are one finding). A finding is acknowledged when
`tools/vendor_advisories_ack.json` lists one of its ids for that pin, with a
reason and a date: `{"pin": <the pin file>, "id": <CVE, GHSA or OSV id>,
"date": "YYYY-MM-DD", "reason": <why it does not reach the console>}`. Exit status: 0 when every finding is acknowledged, 1 when
one is not, 2 when a source could not be asked or the acknowledgements do not
parse. `.github/workflows/vendor-advisories.yml` runs it weekly;
`tests/test_vendor_advisories.py` holds the parsing and the verdict to
recorded responses, with no network.

GITHUB_TOKEN (or GH_TOKEN) raises GitHub's rate limit; NVD_API_KEY shortens
NVD's pacing from 6.5 s to under a second between requests.
"""

import argparse
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ACK_FILE = os.path.join(ROOT, "tools", "vendor_advisories_ack.json")

OSV_URL = "https://api.osv.dev/v1/query"
NVD_URL = "https://services.nvd.nist.gov/rest/json/cves/2.0"
GITHUB_API = "https://api.github.com"

# Per pin: `version` and `commit` are dotted paths into the pin file (a
# `(path, regex)` pair takes the regex's group 1 of that field). `osv_git` is
# (repository URL, tag templates); `nvd` CPE templates and `github` repositories
# as named above. `{v}` is the pinned version. `package` keeps only a GitHub
# advisory that names no package or a package containing it.
SUBJECTS = [
    {"pin": "native/moy_net/mbedtls_vendor.json", "name": "mbedTLS",
     "version": "upstream.version", "commit": "upstream.commit",
     "osv_git": ("https://github.com/mbed-tls/mbedtls", ["mbedtls-{v}", "v{v}"]),
     "nvd": ["cpe:2.3:a:arm:mbed_tls:{v}", "cpe:2.3:a:trustedfirmware:mbed_tls:{v}"],
     "github": ["Mbed-TLS/mbedtls"]},
    {"pin": "native/moy_ssh/dropbear_vendor.json", "name": "Dropbear",
     "version": "version",
     "osv_git": ("https://github.com/mkj/dropbear", ["DROPBEAR_{v}"]),
     "nvd": ["cpe:2.3:a:dropbear_ssh_project:dropbear_ssh:{v}"],
     "github": ["mkj/dropbear"]},
    {"pin": "native/moy_wasm/wamr_vendor.json", "name": "WAMR",
     "version": ("upstream.branch", r"(\d+(?:\.\d+)+)$"),
     "osv_git": ("https://github.com/bytecodealliance/wasm-micro-runtime", ["WAMR-{v}"]),
     "nvd": ["cpe:2.3:a:bytecodealliance:webassembly_micro_runtime:{v}"],
     "github": ["bytecodealliance/wasm-micro-runtime", "moybyte-org/wasm-micro-runtime"]},
    {"pin": "native/moy_net/monocypher_vendor.json", "name": "Monocypher",
     "version": "version",
     "osv_git": ("https://github.com/LoupVaillant/Monocypher", ["{v}"]),
     "github": ["LoupVaillant/Monocypher"]},
    {"pin": "native/moy_sh/dash_vendor.json", "name": "dash",
     "commit": "upstream.commit"},
    {"pin": "native/moy_elf/elf_loader_vendor.json", "name": "elf_loader",
     "version": "version", "commit": "commit",
     "github": ["espressif/esp-iot-solution"], "package": "elf_loader"},
    {"pin": "native/moy_lua/lua_vendor.json", "name": "Lua",
     "version": "upstream.version",
     "osv_git": ("https://github.com/lua/lua", ["v{v}"]),
     "nvd": ["cpe:2.3:a:lua:lua:{v}"]},
    {"pin": "native/p4/moy_dsi/ek79007_vendor.json", "name": "esp_lcd_ek79007",
     "version": "upstream.version", "commit": "upstream.commit",
     "github": ["espressif/esp-iot-solution"], "package": "esp_lcd_ek79007"},
    {"pin": "native/p4/moy_dsi/jd9365_vendor.json", "name": "esp_lcd_jd9365",
     "github": ["espressif/esp-iot-solution"], "package": "esp_lcd_jd9365"},
    {"pin": "native/libmoy_vendor.json", "name": "libmoy",
     "commit": "upstream.commit", "github": ["moybyte-org/moy-spec"]},
    {"pin": "tools/p8_import_vendor.json", "name": "p8_import",
     "commit": "upstream.commit", "github": ["moybyte-org/moy-spec"]},
    {"pin": "ports/jet/jet_vendor.json", "name": "Jet",
     "commit": "upstream.commit", "commits": ["upstream.examples.commit"],
     "github": ["CubeCoders/Jet", "CubeCoders/JetExamples"]},
    {"pin": "ports/jet/jet_carts_vendor.json", "name": "Jet carts",
     "commit": "upstream.commit", "github": ["moybyte-org/carts"]},
]


class SourceError(Exception):
    pass


# -- versions ---------------------------------------------------------------

def vkey(text):
    """A comparable key for a version string: its runs of digits."""
    return tuple(int(n) for n in re.findall(r"\d+", text or ""))


def in_range(version, spec):
    """Whether `version` satisfies a GitHub range ('<= 2.4.4', '>= 1, < 2', '2.4.1')."""
    v = vkey(version)
    for clause in spec.split(","):
        m = re.match(r"\s*(<=|>=|<|>|=)?\s*(\S+)\s*$", clause)
        if not m:
            return True
        op, bound = m.group(1) or "=", vkey(m.group(2))
        ok = {"<": v < bound, "<=": v <= bound, ">": v > bound,
              ">=": v >= bound, "=": v == bound}[op]
        if not ok:
            return False
    return True


# -- the pin file -----------------------------------------------------------

def field(data, path):
    regex = None
    if isinstance(path, tuple):
        path, regex = path
    for part in path.split("."):
        if not isinstance(data, dict) or part not in data:
            return None
        data = data[part]
    if regex is not None:
        m = re.search(regex, str(data))
        return m.group(1) if m else None
    return data


def read_pin(subject, root=ROOT):
    with open(os.path.join(root, subject["pin"])) as f:
        data = json.load(f)
    commits = [field(data, p) for p in [subject.get("commit")] + subject.get("commits", []) if p]
    return {"version": field(data, subject["version"]) if subject.get("version") else None,
            "commits": [c for c in commits if c]}


def tracked_pins(root=ROOT):
    out = subprocess.check_output(["git", "-C", root, "ls-files", "*_vendor.json"])
    return sorted(out.decode().split())


# -- the sources ------------------------------------------------------------

def http_fetch(method, url, body=None, headers=None, attempts=3):
    data = json.dumps(body).encode() if body is not None else None
    hdrs = {"Accept": "application/json", "User-Agent": "vendor-advisories"}
    hdrs.update(headers or {})
    if data is not None:
        hdrs["Content-Type"] = "application/json"
    last = None
    for i in range(attempts):
        try:
            req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            last = "%s %s: HTTP %d" % (method, url, e.code)
            if e.code not in (403, 429) and e.code < 500:
                break
        except (urllib.error.URLError, TimeoutError, ValueError) as e:
            last = "%s %s: %s" % (method, url, e)
        time.sleep(6 * (i + 1))
    raise SourceError(last)


def osv_findings(response):
    out = []
    for v in response.get("vulns", []):
        if v.get("withdrawn"):
            continue
        out.append({"ids": {v["id"], *v.get("aliases", [])},
                    "summary": v.get("summary") or first_line(v.get("details"))})
    return out


def nvd_findings(response):
    out = []
    for item in response.get("vulnerabilities", []):
        cve = item["cve"]
        if cve.get("vulnStatus") == "Rejected":
            continue
        desc = next((d["value"] for d in cve.get("descriptions", []) if d.get("lang") == "en"), "")
        out.append({"ids": {cve["id"]}, "summary": first_line(desc)})
    return out


def github_affects(advisory, version, package=None):
    """Whether a published repository advisory reaches the pinned version.

    No pinned version, or an advisory that states no range, counts as affected:
    the finding is shown and a person acknowledges it.
    """
    vulns = advisory.get("vulnerabilities") or []
    if package:
        vulns = [x for x in vulns
                 if not ((x.get("package") or {}).get("name"))
                 or package in x["package"]["name"]]
        if advisory.get("vulnerabilities") and not vulns:
            return False
    if version is None or not vulns:
        return True
    for x in vulns:
        rng = (x.get("vulnerable_version_range") or "").strip()
        if rng:
            if in_range(version, rng):
                return True
            continue
        patched = [p for p in re.split(r"[,\s]+", x.get("patched_versions") or "") if p]
        if not patched or vkey(version) < min(vkey(p) for p in patched):
            return True
    return False


def github_findings(response, version, package=None):
    out = []
    for a in response:
        if a.get("state", "published") != "published" or a.get("withdrawn_at"):
            continue
        if github_affects(a, version, package):
            ids = {a["ghsa_id"]} | ({a["cve_id"]} if a.get("cve_id") else set())
            out.append({"ids": ids, "summary": a.get("summary") or ""})
    return out


def first_line(text):
    return (text or "").strip().splitlines()[0] if (text or "").strip() else ""


class Sources:
    """The three sources over one fetch(method, url, body, headers)."""

    def __init__(self, fetch=http_fetch, nvd_key=None, github_token=None, pace=True):
        self.fetch = fetch
        self.nvd_key = nvd_key
        self.github_token = github_token
        self.pace = pace
        self._nvd_last = 0.0

    def osv(self, body):
        found, body = [], dict(body)
        while True:
            r = self.fetch("POST", OSV_URL, body, None)
            found += osv_findings(r)
            if not r.get("next_page_token"):
                return found
            body["page_token"] = r["next_page_token"]

    def nvd(self, cpe):
        found, start = [], 0
        while True:
            if self.pace:
                gap = 0.7 if self.nvd_key else 6.5
                wait = self._nvd_last + gap - time.monotonic()
                if wait > 0:
                    time.sleep(wait)
            url = NVD_URL + "?" + urllib.parse.urlencode(
                {"virtualMatchString": cpe, "startIndex": start})
            r = self.fetch("GET", url, None, {"apiKey": self.nvd_key} if self.nvd_key else None)
            self._nvd_last = time.monotonic()
            found += nvd_findings(r)
            start += r.get("resultsPerPage", 0)
            if not r.get("resultsPerPage") or start >= r.get("totalResults", 0):
                return found

    def github(self, repo, version, package=None):
        headers = {"Accept": "application/vnd.github+json"}
        if self.github_token:
            headers["Authorization"] = "Bearer " + self.github_token
        found, page = [], 1
        while True:
            url = "%s/repos/%s/security-advisories?state=published&per_page=100&page=%d" % (
                GITHUB_API, repo, page)
            r = self.fetch("GET", url, None, headers)
            found += github_findings(r, version, package)
            if len(r) < 100:
                return found
            page += 1


# -- one pin ----------------------------------------------------------------

def queries(subject, pin):
    """(label, kind, argument) for every question this pin can be asked."""
    v = pin["version"]
    out = []
    if v and subject.get("osv_git"):
        repo, tags = subject["osv_git"]
        for t in tags:
            tag = t.format(v=v)
            out.append(("osv %s@%s" % (repo.split("github.com/")[-1], tag), "osv",
                        {"package": {"name": repo, "ecosystem": "GIT"}, "version": tag}))
    for c in pin["commits"]:
        out.append(("osv commit %s" % c[:12], "osv", {"commit": c}))
    if v:
        for cpe in subject.get("nvd", []):
            cpe = cpe.format(v=v)
            out.append(("nvd %s" % cpe.split(":", 3)[-1], "nvd", cpe))
    for repo in subject.get("github", []):
        out.append(("github %s" % repo, "github", repo))
    return out


def merge(findings):
    """Join findings that share an id; each keeps every id and source it was seen under."""
    groups = []
    for f in findings:
        hit = [g for g in groups if g["ids"] & f["ids"]]
        g = {"ids": set(f["ids"]), "summary": f["summary"], "sources": set(f["sources"])}
        for h in hit:
            groups.remove(h)
            g["ids"] |= h["ids"]
            g["sources"] |= h["sources"]
            g["summary"] = h["summary"] or g["summary"]
        groups.append(g)
    for g in groups:
        g["id"] = canonical(g["ids"])
    return sorted(groups, key=lambda g: g["id"])


def canonical(ids):
    for prefix in ("CVE-", "GHSA-"):
        named = sorted(i for i in ids if i.startswith(prefix))
        if named:
            return named[0]
    return sorted(ids)[0]


def check_pin(subject, sources, root=ROOT):
    pin = read_pin(subject, root)
    result = {"subject": subject, "pin": pin, "asked": [], "errors": [], "findings": []}
    raw = []
    for label, kind, arg in queries(subject, pin):
        try:
            if kind == "osv":
                got = sources.osv(arg)
            elif kind == "nvd":
                got = sources.nvd(arg)
            else:
                got = sources.github(arg, pin["version"], subject.get("package"))
        except SourceError as e:
            result["errors"].append("%s: %s" % (label, e))
            continue
        result["asked"].append(label)
        for f in got:
            raw.append(dict(f, sources={kind}))
    result["findings"] = merge(raw)
    return result


# -- acknowledgements -------------------------------------------------------

def load_acks(path=ACK_FILE, subjects=SUBJECTS):
    """The acknowledgements, or a list of what is wrong with the file."""
    with open(path) as f:
        data = json.load(f)
    pins = {s["pin"] for s in subjects}
    acks, problems = data.get("acknowledged"), []
    if not isinstance(acks, list):
        return [], ["%s: 'acknowledged' is not a list" % path]
    for i, a in enumerate(acks):
        where = "%s: acknowledged[%d]" % (os.path.basename(path), i)
        if a.get("pin") not in pins:
            problems.append("%s: pin %r is not a vendor pin" % (where, a.get("pin")))
        if not re.match(r"^(CVE-\d{4}-\d+|GHSA(-[23456789cfghjmpqrvwx]{4}){3}|[A-Z][A-Za-z]*-[\w.-]+)$",
                        str(a.get("id", ""))):
            problems.append("%s: id %r is not an advisory id" % (where, a.get("id")))
        if not re.match(r"^\d{4}-\d{2}-\d{2}$", str(a.get("date", ""))):
            problems.append("%s: date %r is not YYYY-MM-DD" % (where, a.get("date")))
        if len(str(a.get("reason", "")).strip()) < 10:
            problems.append("%s: the reason says nothing" % where)
    return acks, problems


def verdict(results, acks):
    """Mark each finding acknowledged or new; return (new count, stale acks)."""
    new, stale = 0, []
    for r in results:
        mine = [a for a in acks if a["pin"] == r["subject"]["pin"]]
        used = set()
        for f in r["findings"]:
            f["ack"] = next((a for a in mine if a["id"] in f["ids"]), None)
            if f["ack"]:
                used.add(f["ack"]["id"])
            else:
                new += 1
        if not r["errors"]:
            stale += [a for a in mine if a["id"] not in used]
    return new, stale


# -- the report -------------------------------------------------------------

def report(results, new, stale, out=None):
    out = out or sys.stdout
    for r in results:
        s, pin = r["subject"], r["pin"]
        what = pin["version"] or (pin["commits"][0][:12] if pin["commits"] else "?")
        print("%s %s  (%s)" % (s["name"], what, s["pin"]), file=out)
        print("  asked: %s" % (", ".join(r["asked"]) or "nothing"), file=out)
        for e in r["errors"]:
            print("  FAILED %s" % e, file=out)
        for f in r["findings"]:
            others = sorted(f["ids"] - {f["id"]})
            tail = " (%s)" % ", ".join(others) if others else ""
            src = "/".join(sorted(f["sources"]))
            if f["ack"]:
                print("  ack  %s%s [%s] %s: %s" % (f["id"], tail, src, f["ack"]["date"],
                                                   f["ack"]["reason"]), file=out)
            else:
                print("  NEW  %s%s [%s] %s" % (f["id"], tail, src, f["summary"][:160]), file=out)
        if not r["findings"] and not r["errors"]:
            print("  no known advisory", file=out)
    for a in stale:
        print("stale acknowledgement: %s %s is no longer reported" % (a["pin"], a["id"]), file=out)
    failed = sum(1 for r in results if r["errors"])
    print("%d unacknowledged advisor%s over %d pin%s%s" % (
        new, "y" if new == 1 else "ies", len(results), "" if len(results) == 1 else "s",
        "; %d pin%s not fully checked" % (failed, "" if failed == 1 else "s") if failed else ""),
        file=out)


def main(argv=None, sources=None, root=ROOT, subjects=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--only", help="check only the pins whose name contains this")
    ap.add_argument("--ack", default=ACK_FILE, help="the acknowledgements file")
    a = ap.parse_args(argv)

    subjects = SUBJECTS if subjects is None else subjects
    missing = sorted(set(tracked_pins(root)) - {s["pin"] for s in subjects})
    acks, problems = load_acks(a.ack, subjects)
    for m in missing:
        problems.append("%s is a vendor pin with no entry in SUBJECTS" % m)
    if problems:
        print("\n".join(problems), file=sys.stderr)
        return 2

    sources = sources or Sources(nvd_key=os.environ.get("NVD_API_KEY") or None,
                      github_token=os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN") or None)
    chosen = [s for s in subjects if not a.only or a.only.lower() in s["name"].lower()]
    results = [check_pin(s, sources, root) for s in chosen]
    new, stale = verdict(results, acks)
    report(results, new, stale)
    if any(r["errors"] for r in results):
        return 2
    return 1 if new else 0


if __name__ == "__main__":
    sys.exit(main())
