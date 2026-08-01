"""No source file may hardcode a path inside somebody's home directory.

This exists because one did, and it cost a green local run and a red CI one:
`firmware/web_runner/replayer_view_test.mjs` read the repo through an absolute
path under the author's home. On the machine it was written on that path is
real, so the whole suite passed; everywhere else -- CI, a fresh clone, a second
checkout -- the file simply is not there.

The lesson is not "remember to use relative paths". It is that this class of bug
is INVISIBLE to the test suite that should catch it, because the suite runs on
the machine where the path happens to work. So the check is structural: scan the
sources for an absolute path under a user's home and fail.

UNTRACKED files are scanned too (ignored ones are not). That is not tidiness --
it is the same asymmetry again: the first version of this test used `git
ls-files`, so it could not see itself until it was committed, passed locally,
and went red in CI. A guard that only wakes up after you push is half a guard.

This file must therefore not contain the pattern it forbids -- it is scanned
like everything else. The examples below are spelled with a <placeholder>, which
the regex deliberately cannot match.

Deliberately narrow. Only *code* is scanned (docs and issue text quote real
paths all the time and should keep doing so), and only home directories -- a
system path like /usr/bin/bash or /dev/ttyACM0 is a legitimate constant.
"""

import os
import re
import subprocess

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Extensions that get EXECUTED. A path here is a runtime dependency on one
# person's filesystem; the same string in a .md is documentation.
CODE = ("*.py", "*.mjs", "*.js", "*.sh", "*.yml", "*.yaml")

# /home/<user>/ and macOS /Users/<user>/. The trailing separator matters: it is
# what distinguishes a path from a prose mention of "/home". The character class
# excludes < and >, so a written-out /home/<placeholder>/ never trips it.
HOME_PATH = re.compile(r"(?:/home/[A-Za-z0-9._-]+/|/Users/[A-Za-z0-9._-]+/)")

# Provisioning scripts create service accounts, so a home path is the subject
# matter rather than an accident. Listed one by one, never a glob: the point of
# this test is that adding an exemption should be a visible decision.
ALLOWED = {
    "deploy/proxmox-setup.sh",      # creates and populates the `moybyte` user
}


def _code_files():
    """Tracked files plus untracked-but-not-ignored ones, so a bad path fails
    before it is ever committed rather than on someone else's machine."""
    out = subprocess.run(
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard",
         "--", *CODE],
        cwd=ROOT, capture_output=True, text=True, check=True).stdout
    return sorted(set(p for p in out.split("\0") if p))


def test_no_source_file_points_into_a_home_directory():
    offenders = []
    for rel in _code_files():
        if rel in ALLOWED:
            continue
        path = os.path.join(ROOT, rel)
        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                for n, line in enumerate(f, 1):
                    if HOME_PATH.search(line):
                        offenders.append("%s:%d: %s" % (rel, n, line.strip()[:110]))
        except OSError:                      # a tracked-but-absent file is not our bug
            continue
    assert not offenders, (
        "source files hardcode a path inside a home directory -- they will work "
        "only on the machine they were written on:\n  " + "\n  ".join(offenders))
