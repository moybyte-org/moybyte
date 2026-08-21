#!/usr/bin/env python3
"""Pull the current firmware images down for the site's web flasher.

The website flashes a board from the browser (Web Serial), which means the
browser has to be able to FETCH the .bin -- and it cannot fetch one from
GitHub at all:

  * Actions artifacts need an authenticated API call and only come as a zip.
  * Release assets are public, but their download responses carry no
    `access-control-allow-origin`, so a cross-origin fetch() is blocked.

So the images have to be served from the site's own origin, and something has
to carry them there. That is this script. It is not subject to either problem:
it runs server-side with a token, and CORS is a browser rule.

Two sources, in order:

  release    the rolling `firmware-latest` release, which is where the build
             workflow publishes (tools/publish_firmware_release.py). Durable --
             it does not expire, which is the point.
  artifacts  the newest successful `Firmware build` run that still has a live
             artifact. The fallback: it covers a board built before the release
             existed, and lets a specific run be pulled by hand.

Either way the output is the same tree, which site/build.py reads:

    dist/ci-firmware/<variant>/
      tdeck/moybyte_tdeck.bin
      tdeck/source.json                        <- run id/url, commit, date
      tdeck/manifest.json                      <- the OTA manifest (its version)
      p4/moybyte_p4.bin
      p4/source.json

Boards are resolved independently: the build workflow is dispatched per board,
so the T-Deck and P4 images are often from different commits, and each card on
the site states its own.

    python3 tools/fetch_ci_firmware.py                  # both boards
    python3 tools/fetch_ci_firmware.py --board p4       # just one
    python3 tools/fetch_ci_firmware.py --source artifacts
    python3 tools/fetch_ci_firmware.py --out /tmp/fw

Needs the `gh` CLI, authenticated (in Actions: GH_TOKEN, `actions: read` for
the artifact path). Stdlib only otherwise. A board with no image anywhere is
NOT an error -- the firmware workflow is dispatched by hand, so "nothing
published for this board yet" is an ordinary outcome the site renders as such.
"""

import argparse
import json
import os
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

BOARDS = ("tdeck", "p4", "guition_s3")
WORKFLOW = "firmware-build.yml"
ARTIFACT = "moybyte-firmware-%s"
RELEASE_TAG = "firmware-latest"
# How far back to look. The workflow is manual, so successful runs are sparse;
# 40 covers many months of dispatches and still costs one API call.
RUN_SCAN = 40


class GhError(RuntimeError):
    """`gh` itself is missing, unauthenticated or erroring -- not a soft miss."""


def gh(*args, check=True):
    """Run `gh` and return stdout. Raises GhError so a broken CLI is loud.

    check=False turns a non-zero exit into None instead -- for the lookups
    where "not there" is an answer (no release yet), not a failure.
    """
    try:
        p = subprocess.run(("gh",) + args, capture_output=True, text=True)
    except FileNotFoundError:
        raise GhError("the `gh` CLI is not installed")
    if p.returncode != 0:
        if not check:
            return None
        raise GhError("gh %s failed: %s" % (" ".join(args), p.stderr.strip()))
    return p.stdout


def gh_json(*args, check=True):
    return json.loads(gh(*args, check=check) or "null")


def repo_slug():
    """owner/name -- the Actions env first, so CI never shells out for it."""
    slug = os.environ.get("GITHUB_REPOSITORY")
    if slug:
        return slug
    return gh_json("repo", "view", "--json", "nameWithOwner")["nameWithOwner"]


def successful_runs(slug, workflow, branch):
    """Newest-first successful runs of `workflow` on `branch`."""
    path = ("repos/%s/actions/workflows/%s/runs"
            "?status=success&branch=%s&per_page=%d" % (slug, workflow, branch, RUN_SCAN))
    return gh_json("api", path).get("workflow_runs") or []


def live_artifact(slug, run_id, name):
    """The named artifact of a run, if it exists and has not expired."""
    got = gh_json("api", "repos/%s/actions/runs/%s/artifacts" % (slug, run_id))
    for art in got.get("artifacts") or []:
        if art.get("name") == name and not art.get("expired"):
            return art
    return None


def board_dir(out_dir, board):
    """A clean folder for one board (never a mix of two sources)."""
    dest = os.path.join(out_dir, board)
    if os.path.isdir(dest):
        shutil.rmtree(dest)
    os.makedirs(dest)
    return dest


def fetch_release(slug, board, assets, out_dir, tag=None):
    """Take one board's image out of a release. -> source or None.

    Release assets are flat, so they are named `<board>-<image>` and
    `<board>-source.json` (tools/publish_firmware_release.py); this puts the
    per-board layout back.

    `tag` is which release to read. It defaults to the rolling stable one, but
    the site offers a CHANNEL choice now, so the same code pulls `firmware-beta`
    -- and a version tag (`v0.10`), whose assets are the archive that makes a
    deliberate rollback possible at all.
    """
    tag = tag or RELEASE_TAG
    prefix = board + "-"
    mine = [a for a in assets if a.startswith(prefix) and a.endswith(".bin")]
    if not mine:
        return None
    dest = board_dir(out_dir, board)
    gh("release", "download", tag, "-R", slug, "-p", prefix + "*",
       "-D", dest)
    source = {}
    for name in sorted(os.listdir(dest)):
        landed = os.path.join(dest, name)
        # `<board>-source.json` -> source.json, `<board>-x.bin` -> x.bin
        plain = name[len(prefix):] if name.startswith(prefix) else name
        os.rename(landed, os.path.join(dest, plain))
        if plain == "source.json":
            try:
                source = json.load(open(os.path.join(dest, plain), encoding="utf-8"))
            except ValueError:
                source = {}
    # The OTA manifest too, as `manifest.json`. It is the only place the build's
    # human VERSION is written ("0.10", "beta 2026-08-20 10:25"), and the site's
    # build picker names its options with it -- a channel without a version is
    # the one thing a person choosing between builds actually wants to know. It
    # is not prefixed `<board>-`, which is why the pattern above misses it, and
    # a release without one (the artifacts path) is not an error.
    if gh("release", "download", tag, "-R", slug,
          "-p", "latest-%s.json" % board, "-D", dest, check=False) is not None:
        landed = os.path.join(dest, "latest-%s.json" % board)
        if os.path.exists(landed):
            os.rename(landed, os.path.join(dest, "manifest.json"))

    files = sorted(n for n in os.listdir(dest) if n.endswith(".bin"))
    print("%-6s release %s (%s, %s) -> %s" %
          (board, tag, (source.get("commit") or "?")[:8],
           (source.get("built") or "")[:10], ", ".join(files)))
    return source or {"board": board}


def fetch_board(slug, board, runs, out_dir):
    """Download the newest live artifact for one board. -> source dict or None."""
    name = ARTIFACT % board
    for run in runs:
        art = live_artifact(slug, run["id"], name)
        if not art:
            continue
        dest = board_dir(out_dir, board)
        # `gh run download` unzips into -D for us.
        gh("run", "download", str(run["id"]), "-R", slug, "-n", name, "-D", dest)
        source = {
            "board": board,
            "run_id": run["id"],
            "run_url": run.get("html_url", ""),
            "run_number": run.get("run_number"),
            "commit": run.get("head_sha", ""),
            "branch": run.get("head_branch", ""),
            "built": art.get("created_at") or run.get("updated_at") or "",
            "artifact": name,
            "workflow": WORKFLOW,
        }
        with open(os.path.join(dest, "source.json"), "w", encoding="utf-8") as f:
            json.dump(source, f, indent=2, sort_keys=True)
            f.write("\n")
        files = sorted(n for n in os.listdir(dest) if n.endswith(".bin"))
        print("%-6s run %s (%s, %s) -> %s" %
              (board, run["id"], (source["commit"] or "?")[:8],
               source["built"][:10], ", ".join(files) or "no .bin!"))
        return source
    return None


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", default=os.path.join(ROOT, "dist", "ci-firmware"),
                    help="where to put the per-board folders")
    ap.add_argument("--board", action="append", choices=BOARDS,
                    help="only this board (repeatable); default: all")
    ap.add_argument("--repo", help="owner/name (default: this checkout's)")
    ap.add_argument("--branch", default="master",
                    help="only builds of this branch (default: master)")
    ap.add_argument("--release", default=RELEASE_TAG, metavar="TAG",
                    help="which release to read (default: %s). `firmware-beta` "
                         "is the dev channel; a version tag (v0.10) is the "
                         "archive a rollback comes from." % RELEASE_TAG)
    ap.add_argument("--source", choices=("auto", "release", "artifacts"),
                    default="auto",
                    help="where to read images from (default: the release, "
                         "falling back to run artifacts per board)")
    args = ap.parse_args()

    boards = args.board or list(BOARDS)
    out = os.path.abspath(args.out)
    os.makedirs(out, exist_ok=True)

    try:
        slug = args.repo or repo_slug()
        assets = []
        if args.source in ("auto", "release"):
            got = gh_json("release", "view", args.release, "-R", slug,
                          "--json", "assets", check=False)
            assets = [a["name"] for a in (got or {}).get("assets") or []]
            if not assets:
                print("no `%s` release assets%s" %
                      (args.release,
                       " -- falling back to run artifacts" if args.source == "auto"
                       else ""))
        runs = []
        if args.source in ("auto", "artifacts"):
            runs = successful_runs(slug, WORKFLOW, args.branch)
    except GhError as exc:
        print("!! %s" % exc, file=sys.stderr)
        return 1

    found = 0
    for board in boards:
        try:
            source = None
            if assets:
                source = fetch_release(slug, board, assets, out, args.release)
            # Per board, not per run: a board published only as an artifact so
            # far still gets one while the other comes off the release.
            if source is None and runs:
                source = fetch_board(slug, board, runs, out)
            if source is None:
                print("%-6s nothing published (no release asset, no live "
                      "artifact)" % board)
            else:
                found += 1
        except GhError as exc:
            print("!! %s: %s" % (board, exc), file=sys.stderr)
            return 1

    print("-> %s (%d/%d board%s)" %
          (out, found, len(boards), "" if len(boards) == 1 else "s"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
