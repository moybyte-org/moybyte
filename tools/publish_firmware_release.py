#!/usr/bin/env python3
"""Publish this build's firmware images to a rolling per-CHANNEL release.

The website flashes boards from images it serves itself, and it has to get them
from somewhere durable. Actions artifacts expire; committing 7.4 MB of .bin per
build would outgrow the whole repository within a month. A release costs a clone
nothing, keeps the images forever, and gives humans a download page.

TWO releases, one per branch/channel, and they never mix (CLAUDE.md ->
"Branches and releases"):

    master -> `firmware-latest`   stable: the site's flasher + the stable OTA
    dev    -> `firmware-beta`     beta:   the unstable OTA channel only

The release is ROLLING and per-board: each board's asset is replaced when that
board is rebuilt, so the T-Deck and P4 images can be from different commits.
That is deliberate -- the build workflow also runs per board -- and each image
carries its own provenance beside it:

    firmware-latest/
      tdeck-moybyte_tdeck.bin
      tdeck-source.json          <- commit, run, date for THAT image
      p4-moybyte_p4.bin
      p4-source.json
      tdeck-moybyte_tdeck_app.bin  <- the OTA payload (app slot, not 0x0)
      latest-tdeck.json            <- the OTA manifest pointing at it

The `<board>-` prefix is how flat release assets keep the per-board layout that
tools/fetch_ci_firmware.py rebuilds on the way back down; board ids therefore
must not contain a dash.

Which image each board publishes is NOT decided here: it is read out of
site/build.py's BOARDS table, the same table the page's flasher writes from, so
there is one list of "the image we flash" and not two.

    tools/publish_firmware_release.py --artifacts artifacts --channel stable

Needs the `gh` CLI with `contents: write`. Boards absent from the artifacts
folder are left untouched on the release -- a single-board dispatch must not
delete the other board's image.
"""

import argparse
import datetime
import importlib.util
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ota_sign  # noqa: E402
from gen_ota_manifest import build_manifest, read_firmware_version  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TAG = "firmware-latest"
TITLE = "Firmware — latest build"

# channel -> the release it rolls, and the branch that produces it. The channel
# ids are the device's (moy_ota.FIRMWARE_CHANNEL, Settings -> CHANNEL): kept as
# stable/unstable rather than renamed to master/dev because they are baked into
# shipped images and persisted settings on boards already in the world.
CHANNELS = {
    "stable": {
        "tag": TAG,
        "title": TITLE,
        "branch": "master",
        "blurb": "the tested branch — this is what the site's flasher writes",
    },
    "unstable": {
        "tag": "firmware-beta",
        "title": "Firmware — beta (dev)",
        "branch": "dev",
        "blurb": "**untested** builds off `dev`, published on every push — "
                 "opt in from Settings → CHANNEL, and note the bootloader "
                 "rolls a bad image back on its own",
    },
}

# The OTA payload is NOT the image the site flashes. The flasher writes a whole
# chip at 0x0 (0x2000 on the P4); an OTA writes the APP into the inactive slot,
# so the manifest points at the app image, which each board names differently.
#
# PER BOARD, and strictly so: an app image is Xtensa on the T-Deck and RISC-V on
# the P4, so a board given the other one writes a well-formed image that cannot
# boot. Hence a manifest per (channel, board) rather than one per channel --
# moy_ota.default_manifest_url builds the same name from the board stamped into
# the running image.
OTA_IMAGES = {
    "tdeck": "moybyte_tdeck_app.bin",
    "p4": "moybyte_p4_app.bin",
    "guition_s3": "moybyte_guition_s3_app.bin",
}
OTA_STAMP = "ota_build.json"     # build.sh's baked identity, carried in the artifact


def manifest_name(board):
    return "latest-%s.json" % board

NOTES_HEAD = """\
The current **{channel}** firmware for each board, rebuilt by the **Firmware
build** workflow from `{branch}` — {blurb}.

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

NOTES_OTA = """
### Over the air

`latest-<board>.json` is this channel's OTA manifest for that board, beside the
app image it describes — the device's Settings → UPDATE ONLINE reads them
directly, so no host of your own is needed. One per board on purpose: an OTA
payload is an app-partition image, so the T-Deck's is Xtensa and the P4's is
RISC-V, and the board is inside the signature.

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


def read_stamp(artifacts, board_id):
    """build.sh's `ota_build.json` for this run's image: {channel, version, label}.

    The image's identity is a BUILD-time fact (a beta's version is the build's
    epoch), so it travels with the artifact. Re-deriving it here would let the
    manifest advertise a version the image does not carry -- and a manifest
    whose version is higher than the image it installs offers that same install
    forever."""
    path = os.path.join(artifacts, "moybyte-firmware-%s" % board_id, OTA_STAMP)
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def asset_url(tag, name, repo=None):
    """The public download URL of a release asset (a 302 to the CDN, which the
    device's updater follows -- see moy_ota._http_open)."""
    repo = repo or os.environ.get("GITHUB_REPOSITORY") or "moybyte-org/moybyte"
    return "https://github.com/%s/releases/download/%s/%s" % (repo, tag, name)


def stage_ota_all(tag, channel, artifacts, workdir, repo=None):
    """Stage every built board's OTA app image + its manifest.

    Returns {board: manifest}. A board absent from this run keeps whatever is
    already published for it, exactly like its flashable image."""
    # A board that BUILT (its artifact folder is here) but is missing from
    # OTA_IMAGES would silently never get a manifest -- the miss that kept the
    # Guition's first beta off the channel on 2026-08-20 (the build emitted the
    # app image and the stamp; this dict was two boards long). Say so loudly.
    prefix = "moybyte-firmware-"
    if os.path.isdir(artifacts):
        for entry in sorted(os.listdir(artifacts)):
            if entry.startswith(prefix) and entry[len(prefix):] not in OTA_IMAGES:
                print("::warning::%s built images but has no OTA_IMAGES entry "
                      "-- no OTA manifest will be published for it"
                      % entry[len(prefix):])
    out = {}
    for board in sorted(OTA_IMAGES):
        got = stage_ota(tag, channel, artifacts, workdir, repo, board)
        if got:
            out[board] = got
    return out


def stage_ota(tag, channel, artifacts, workdir, repo=None, board="tdeck"):
    """Stage one board's OTA app image + `latest-<board>.json`, or None."""
    image = OTA_IMAGES[board]
    folder = os.path.join(artifacts, "moybyte-firmware-%s" % board)
    src = os.path.join(folder, image)
    if not os.path.exists(src):
        print("%-6s no %s in this run -- OTA manifest unchanged" % (board, image))
        return None

    name = "%s-%s" % (board, image)
    shutil.copyfile(src, os.path.join(workdir, name))

    stamp = read_stamp(artifacts, board)
    baked = stamp.get("channel")
    if baked and baked != channel:
        # The image says one thing and the publisher another: trust the image
        # (it is what the device will run) and say so loudly.
        print("WARNING: image was built for channel %r but publishing to %r -- "
              "using the image's" % (baked, channel))
        channel = baked
    version = stamp.get("version")
    if version is None:
        version = read_firmware_version()
        print("no %s in the artifact -- falling back to FIRMWARE_VERSION=%d"
              % (OTA_STAMP, version))
    manifest = build_manifest(os.path.join(workdir, name),
                              asset_url(tag, name, repo),
                              version, channel, stamp.get("label"), board)
    stage_c6(manifest, folder, tag, workdir, repo)

    # Sign it, or say plainly that we did not. A device checking a BAKED channel
    # url refuses an unsigned manifest (moy_ota._require_signature), so an
    # unsigned publish is not a security hole -- it is an update nobody can
    # install, which is worth a loud line in the log rather than a silent one.
    key_pem = ota_sign.read_key()
    if key_pem:
        manifest["sig"] = ota_sign.sign(manifest, key_pem)
        if manifest.get("c6"):
            # Its OWN signature: the app `sig` deliberately covers only the app
            # fields (widening it would break every deployed verifier), and an
            # unsigned c6 block inside a signed manifest would hand a network
            # attacker the radio co-processor. See ota_sign.C6_SCHEME.
            manifest["c6_sig"] = ota_sign.sign_c6(manifest, key_pem)
        print("%-6s signed the manifest%s" % (
            board, " (+ c6 block)" if manifest.get("c6") else ""))
    else:
        print("WARNING: no $%s -- publishing an UNSIGNED manifest, which a "
              "device on a baked channel url will refuse" % ota_sign.ENV_KEY)

    out = manifest_name(board)
    with open(os.path.join(workdir, out), "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, sort_keys=True)
        f.write("\n")
    print("%-6s staged %s + %s (channel=%s version=%s)"
          % (board, name, out, channel, manifest["version"]))
    return manifest


C6_IMAGE = "c6_network_adapter.bin"
C6_STAMP = "c6_build.json"


def stage_c6(manifest, folder, tag, workdir, repo=None):
    """Stage the P4's C6 co-processor image beside its app image, and describe
    it in the manifest's `c6` block (#7/#58: the radio is a second processor
    with its own firmware, updated FROM the console over SDIO --
    device/moy_c6_update.py is the consumer). A run whose artifacts carry no
    C6 image publishes a manifest without the block, and devices simply see
    nothing to offer; `version` comes from the artifact's stamp, which the
    slave build read out of the proto header the image itself was compiled
    from, so the number a device is offered is the number the flashed slave
    answers over MOYC6_V_VERSION."""
    src = os.path.join(folder, C6_IMAGE)
    stamp_path = os.path.join(folder, C6_STAMP)
    if not os.path.exists(src) or not os.path.exists(stamp_path):
        print("%-6s no %s in this run -- manifest carries no c6 block"
              % (manifest.get("board", "?"), C6_IMAGE))
        return
    try:
        stamp = json.load(open(stamp_path, encoding="utf-8"))
        version = int(stamp["version"])
    except (ValueError, KeyError, TypeError) as exc:
        print("WARNING: unreadable %s (%s) -- manifest carries no c6 block"
              % (C6_STAMP, exc))
        return
    shutil.copyfile(src, os.path.join(workdir, C6_IMAGE))
    data = open(src, "rb").read()
    manifest["c6"] = {
        "version": version,
        "hosted": stamp.get("hosted") or "",
        "url": asset_url(tag, C6_IMAGE, repo),
        "filename": C6_IMAGE,
        "size": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
    }
    print("%-6s staged %s (shim v%d, %d B)"
          % (manifest.get("board", "?"), C6_IMAGE, version, len(data)))


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


def notes(tag, channel, boards, workdir, site, manifests=None):
    """A release body that states each board's REAL provenance, including the
    board this run did not rebuild (read back off the release)."""
    spec = CHANNELS[channel]
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
    head = NOTES_HEAD.format(channel="beta" if channel == "unstable" else channel,
                             branch=spec["branch"], blurb=spec["blurb"], site=site)
    body = head + "\n".join(rows) + "\n" + NOTES_TAIL
    if manifests:
        body += NOTES_OTA
        for board in sorted(manifests):
            m = manifests[board]
            body += ("- **%s** — `%s` described by `%s`, running **%s** "
                     "(version `%s`)\n"
                     % (board, "%s-%s" % (board, OTA_IMAGES[board]),
                        manifest_name(board), m.get("label", "?"),
                        m.get("version", "?")))
    return body


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--artifacts", default="artifacts",
                    help="actions/download-artifact's output folder")
    ap.add_argument("--channel", default="stable", choices=sorted(CHANNELS),
                    help="which channel/release to roll (default: stable)")
    ap.add_argument("--tag", help="override the channel's release tag")
    ap.add_argument("--site", default="https://moybyte-org.github.io/moybyte/")
    ap.add_argument("--dry-run", action="store_true",
                    help="stage and print, upload nothing")
    args = ap.parse_args()

    channel = args.channel
    spec = CHANNELS[channel]
    tag = args.tag or spec["tag"]
    boards = boards_table()
    workdir = tempfile.mkdtemp(prefix="moy-release-")
    try:
        staged = stage(boards, os.path.abspath(args.artifacts), workdir)
        manifests = stage_ota_all(tag, channel, os.path.abspath(args.artifacts),
                                  workdir)
        if not staged:
            print("nothing built in this run -- the release is unchanged")
            return 0
        if args.dry_run:
            print("would upload to %s: %s"
                  % (tag, ", ".join(sorted(os.listdir(workdir)))))
            return 0

        # Create on first use; after that every publish just replaces assets.
        if gh("release", "view", tag, check=False).returncode != 0:
            gh("release", "create", tag, "--title", spec["title"], "--prerelease",
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
            f.write(notes(tag, channel, boards, workdir, args.site, manifests))
        gh("release", "edit", tag, "--notes-file", body)
        return 0
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
