#!/usr/bin/env python3
"""Publish this build's firmware images to the rolling `firmware-latest` release.

The website flashes boards from images it serves itself, and it has to get them
from somewhere durable. Actions artifacts expire; committing 7.4 MB of .bin per
build would outgrow the whole repository within a month. A release costs a clone
nothing, keeps the images forever, and gives humans a download page.

The release is ROLLING and per-board: each board's asset is replaced when that
board is rebuilt, so the T-Deck and P4 images can be from different commits.
That is deliberate -- the build workflow is dispatched per board -- and each
image carries its own provenance beside it:

    firmware-latest/
      tdeck-moybyte-current-full-dio-0x0.bin
      tdeck-source.json        <- commit, run, date for THAT image
      p4-moybyte_p4.bin
      p4-source.json

The `<board>-` prefix is how flat release assets keep the per-board layout that
tools/fetch_ci_firmware.py rebuilds on the way back down; board ids therefore
must not contain a dash.

Which image each board publishes is NOT decided here: it is read out of
site/build.py's BOARDS table, the same table the page's flasher writes from, so
there is one list of "the image we flash" and not two.

    tools/publish_firmware_release.py --artifacts artifacts   # in CI

Needs the `gh` CLI with `contents: write`. Boards absent from the artifacts
folder are left untouched on the release -- a single-board dispatch must not
delete the other board's image.
"""

import argparse
import datetime
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TAG = "firmware-latest"
TITLE = "Firmware — latest build"

NOTES_HEAD = """\
The current firmware for each board, rebuilt by the **Firmware build** workflow.
This is exactly what the [project site]({site})'s flasher writes, and what
`tools/fetch_ci_firmware.py` pulls down.

"""

NOTES_TAIL = """
Each board's image is replaced independently, when that board is rebuilt, so the
two can come from different commits — `<board>-source.json` beside each image
records which. The tag itself is a fixed anchor, not the commit the images were
built from.

Flash it from the site, or over a cable:

```
make firmware-flash-lilygo-micropython-full PORT=/dev/ttyACM0   # T-Deck
make firmware-flash-p4 PORT=/dev/ttyACM0                        # ESP32-P4
```
"""


def boards_table():
    """site/build.py's BOARDS -- the one list of what we flash per board."""
    path = os.path.join(ROOT, "site", "build.py")
    spec = importlib.util.spec_from_file_location("_moy_site_build", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.BOARDS


def gh(*args, check=True):
    p = subprocess.run(("gh",) + args, capture_output=True, text=True)
    if check and p.returncode != 0:
        raise RuntimeError("gh %s failed: %s" % (" ".join(args), p.stderr.strip()))
    return p


def stage(boards, artifacts, workdir):
    """Copy each built board's image + a provenance sidecar into workdir."""
    now = datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0)
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    run_id = os.environ.get("GITHUB_RUN_ID", "")
    staged = []
    for board in boards:
        assert "-" not in board["id"], "board ids carry the asset prefix"
        folder = os.path.join(artifacts, "moybyte-firmware-%s" % board["id"])
        name = next((n for n in board["images"]
                     if os.path.exists(os.path.join(folder, n))), None)
        if not name:
            print("%-6s not in this run's artifacts -- leaving the release's "
                  "copy alone" % board["id"])
            continue
        shutil.copyfile(os.path.join(folder, name),
                        os.path.join(workdir, "%s-%s" % (board["id"], name)))
        source = {
            "board": board["id"],
            "image": name,
            "commit": os.environ.get("GITHUB_SHA", ""),
            "branch": os.environ.get("GITHUB_REF_NAME", ""),
            "run_id": int(run_id) if run_id.isdigit() else run_id,
            "run_number": os.environ.get("GITHUB_RUN_NUMBER", ""),
            "run_url": ("https://github.com/%s/actions/runs/%s" % (repo, run_id)
                        if repo and run_id else ""),
            "built": now.isoformat().replace("+00:00", "Z"),
        }
        with open(os.path.join(workdir, "%s-source.json" % board["id"]),
                  "w", encoding="utf-8") as f:
            json.dump(source, f, indent=2, sort_keys=True)
            f.write("\n")
        staged.append((board, name, source))
        print("%-6s staged %s" % (board["id"], name))
    return staged


def existing_source(tag, board_id, workdir):
    """The provenance of a board we did NOT rebuild, off the release itself."""
    got = gh("release", "download", tag, "-p", "%s-source.json" % board_id,
             "-D", workdir, "--clobber", check=False)
    path = os.path.join(workdir, "%s-source.json" % board_id)
    if got.returncode != 0 or not os.path.exists(path):
        return None
    try:
        return json.load(open(path, encoding="utf-8"))
    except ValueError:
        return None


def notes(tag, boards, workdir, site):
    """A release body that states each board's REAL provenance, including the
    board this run did not rebuild (read back off the release)."""
    rows = ["| board | image | flash at | built | commit |",
            "|---|---|---|---|---|"]
    for board in boards:
        src = existing_source(tag, board["id"], workdir)
        if not src:
            rows.append("| %s | — | — | not published yet | — |" % board["label"])
            continue
        commit = src.get("commit", "")
        rows.append("| %s | `%s-%s` | `0x%x` | %s | %s |"
                    % (board["label"], board["id"], src.get("image", "?"),
                       board["offset"], src.get("built", "?")[:10],
                       ("`%s`" % commit[:7]) if commit else "—"))
    return NOTES_HEAD.format(site=site) + "\n".join(rows) + "\n" + NOTES_TAIL


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--artifacts", default="artifacts",
                    help="actions/download-artifact's output folder")
    ap.add_argument("--tag", default=TAG)
    ap.add_argument("--site", default="https://moybyte-org.github.io/moybyte/")
    ap.add_argument("--dry-run", action="store_true",
                    help="stage and print, upload nothing")
    args = ap.parse_args()

    tag = args.tag
    boards = boards_table()
    workdir = tempfile.mkdtemp(prefix="moy-release-")
    try:
        staged = stage(boards, os.path.abspath(args.artifacts), workdir)
        if not staged:
            print("nothing built in this run -- the release is unchanged")
            return 0
        if args.dry_run:
            print("would upload: %s" % ", ".join(sorted(os.listdir(workdir))))
            return 0

        # Create on first use; after that every publish just replaces assets.
        if gh("release", "view", tag, check=False).returncode != 0:
            gh("release", "create", tag, "--title", TITLE, "--prerelease",
               "--notes", "Publishing…")
            print("created the %s release" % tag)

        # Everything staged, and only what was staged: a board this run did not
        # build keeps the image already on the release.
        files = [os.path.join(workdir, n) for n in sorted(os.listdir(workdir))]
        gh("release", "upload", tag, *files, "--clobber")
        print("uploaded: %s" % ", ".join(os.path.basename(f) for f in files))

        # Written AFTER the upload, so the table describes what is now there.
        body = os.path.join(workdir, "notes.md")
        with open(body, "w", encoding="utf-8") as f:
            f.write(notes(tag, boards, workdir, args.site))
        gh("release", "edit", tag, "--notes-file", body)
        return 0
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
