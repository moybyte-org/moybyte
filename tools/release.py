#!/usr/bin/env python3
"""Cut a release: merge `dev` into `master` and bump the firmware version.

The two branches are the two OTA channels (CLAUDE.md -> "Branches and
releases"). `dev` is where work lands and where CI publishes beta images from;
`master` is what users get -- the site's flasher and the stable OTA channel both
read the release that a master build publishes. The merge IS the release event,
so the version bump belongs to it and not to the commit that happened to be
last on dev.

    make release                  # merge dev -> master, bump, commit, tag
    make release NOTES="..."      # ... and record why, beside the constant
    make release PUSH=1           # ... and push (master + the tag)

What it does, in order, stopping at the first thing that looks wrong:

    1. clean tree, and `dev` and `master` both level with origin
    2. the host suite (`make test`) -- the gate the release is entitled to
    3. checkout master, `merge --no-ff dev`
    4. FIRMWARE_VERSION N -> N+1 in moy_ota.py, with NOTES as its comment
    5. commit "release: vN+1" + tag "vN+1"
    6. print the push command -- it does NOT push unless asked

Step 6 is deliberate. Pushing master is the moment a device somewhere is
offered this build, so it is a separate keystroke, taken knowingly.

The version is a single monotonic int, the one moy_ota compares against a
manifest (`FIRMWARE_VERSION`, the T-Deck module -- the P4 has no OTA updater
yet, #58). Betas do not use it: a dev build stamps a build-time epoch, so beta
testers always see the newest.
"""

import argparse
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MOY_OTA = os.path.join(ROOT, "firmware", "lilygo_t_deck_plus_micropython",
                       "modules", "moy_ota.py")
DEV = "dev"
MAIN = "master"
VERSION_RE = re.compile(r"^(FIRMWARE_VERSION\s*=\s*)(\d+)(.*)$", re.MULTILINE)


class Stop(Exception):
    """A precondition failed; the message is for the human, not a traceback."""


def git(*args, capture=True, check=True):
    p = subprocess.run(("git",) + args, cwd=ROOT, text=True,
                       capture_output=capture)
    if check and p.returncode != 0:
        raise Stop("git %s failed:\n%s" % (" ".join(args),
                                           (p.stderr or p.stdout or "").strip()))
    return (p.stdout or "").strip()


def bump_text(text, notes=None):
    """FIRMWARE_VERSION N -> N+1 in moy_ota.py's source. Returns (new_text, N+1).

    Kept a pure string transform so it is testable without a git tree -- the
    constant's trailing comment is release notes, so a fresh one replaces it
    when given and the old one is dropped either way (it described the version
    that is no longer current)."""
    m = VERSION_RE.search(text)
    if not m:
        raise Stop("FIRMWARE_VERSION not found in %s" % MOY_OTA)
    new = int(m.group(2)) + 1
    comment = "            # v%d" % new
    if notes:
        comment += ": " + notes.strip()
    line = "%s%d%s" % (m.group(1), new, comment)
    # The constant's comment can run over following lines (indented, code-free);
    # they describe the version that just stopped being current, so they go with
    # the comment they belong to. What must NOT be lost is standing guidance --
    # "bump on every release" and friends live in the block comment ABOVE the
    # constant, which this never touches.
    start, end = m.start(), m.end()
    tail = re.sub(r"\A(\n[ \t]+#[^\n]*)+", "", text[end:])
    return text[:start] + line + tail, new


def read_version():
    with open(MOY_OTA, encoding="utf-8") as f:
        text = f.read()
    m = VERSION_RE.search(text)
    if not m:
        raise Stop("FIRMWARE_VERSION not found in %s" % MOY_OTA)
    return int(m.group(2))


def preflight(skip_tests):
    if git("status", "--porcelain"):
        raise Stop("the working tree has changes -- commit or stash them first "
                   "(a release must be exactly what is on the branch)")
    git("fetch", "origin", capture=True)
    for branch in (DEV, MAIN):
        if not git("rev-parse", "--verify", "--quiet", branch, check=False):
            raise Stop("no local `%s` branch" % branch)
        remote = "origin/" + branch
        if not git("rev-parse", "--verify", "--quiet", remote, check=False):
            print("note: %s has no upstream yet" % branch)
            continue
        ahead, behind = git("rev-list", "--left-right", "--count",
                            "%s...%s" % (branch, remote)).split()
        if int(behind):
            raise Stop("%s is %s commit(s) behind %s -- pull first"
                       % (branch, behind, remote))
        if branch == MAIN and int(ahead):
            raise Stop("%s is %s commit(s) ahead of origin -- push or reset "
                       "before cutting a release" % (branch, ahead))
    pending = git("rev-list", "--count", "%s..%s" % (MAIN, DEV))
    if pending == "0":
        raise Stop("%s has nothing %s does not -- nothing to release"
                   % (DEV, MAIN))
    print("%s commit(s) to release" % pending)

    if skip_tests:
        print("SKIPPING the test suite (--no-tests)")
        return
    print("running the host suite ...")
    p = subprocess.run(["make", "test"], cwd=ROOT)
    if p.returncode != 0:
        raise Stop("tests failed -- that is the release gate, so stopping here")


def cut(notes, push, skip_tests):
    started = git("rev-parse", "--abbrev-ref", "HEAD")
    preflight(skip_tests)

    version = read_version() + 1
    tag = "v%d" % version
    if git("tag", "--list", tag):
        raise Stop("tag %s already exists" % tag)

    git("checkout", MAIN)
    try:
        git("merge", "--no-ff", DEV, "-m", "Merge dev into master for %s" % tag)

        with open(MOY_OTA, encoding="utf-8") as f:
            text = f.read()
        new_text, bumped = bump_text(text, notes)
        assert bumped == version
        with open(MOY_OTA, "w", encoding="utf-8") as f:
            f.write(new_text)

        git("add", MOY_OTA)
        msg = "release: %s" % tag
        if notes:
            msg += "\n\n%s" % notes.strip()
        git("commit", "-m", msg)
        git("tag", "-a", tag, "-m", msg)
    except Exception:
        print("\nsomething went wrong mid-release. `master` is left as it is so "
              "you can look; `git merge --abort` or `git reset --hard "
              "origin/master` undoes it.", file=sys.stderr)
        raise

    print("\n%s cut on %s (was on %s)" % (tag, MAIN, started))
    if push:
        git("push", "origin", MAIN, capture=False)
        git("push", "origin", tag, capture=False)
        print("pushed -- CI builds the stable images and publishes "
              "firmware-latest + the site")
        return 0
    print("\nNothing has been pushed. When you are ready:\n\n"
          "    git push origin %s %s\n\n"
          "That is what publishes it: CI builds both boards, replaces the\n"
          "`firmware-latest` release the site's flasher and the stable OTA\n"
          "channel read, and republishes the page." % (MAIN, tag))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--notes", default=os.environ.get("NOTES") or None,
                    help="one line recorded beside FIRMWARE_VERSION and in the "
                         "commit -- what changed for a device owner")
    ap.add_argument("--push", action="store_true",
                    default=bool(os.environ.get("PUSH")),
                    help="push master + the tag when it all worked")
    ap.add_argument("--no-tests", action="store_true",
                    help="skip `make test` (you have just run it)")
    args = ap.parse_args(argv)
    try:
        return cut(args.notes, args.push, args.no_tests)
    except Stop as exc:
        print("\nrelease stopped: %s" % exc, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
