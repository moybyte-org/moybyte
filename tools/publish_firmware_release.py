#!/usr/bin/env python3
"""Publish this build's firmware images to a rolling per-CHANNEL release.

The website flashes boards from images it serves itself, and it has to get them
from somewhere durable. Actions artifacts expire; committing 7.4 MB of .bin per
build would outgrow the whole repository within a month. A release costs a clone
nothing, keeps the images forever, and gives humans a download page.

TWO releases, one per branch/channel, and they never mix (the `release` skill,
.claude/skills/release/SKILL.md):

    master -> `firmware-latest`   stable: the site's flasher + the stable OTA
    dev    -> `firmware-beta`     beta:   the unstable OTA channel only

The release is ROLLING and per-board: each board's asset is replaced when that
board is rebuilt, so the T-Deck and P4 images can be from different commits.
That is deliberate -- the build workflow also runs per board -- and each image
carries its own provenance beside it:

    firmware-latest/
      tdeck-moybyte_tdeck_install.bin  <- the website's install: the image,
                                          then a store holding the browser
                                          console (tools/store_image.py)
      tdeck-source.json          <- commit, run, date for THAT image
      p4-moybyte_p4_install.bin
      p4-source.json
      c3_zero-moybyte_c3_zero.bin  <- the C3's update: its app, carts kept
      tdeck-moybyte_tdeck_app.bin  <- the OTA payload (app slot, not 0x0)
      latest-tdeck.json            <- the OTA manifest pointing at it
      moybyte_web-<digest>.moy.zip <- the browser console add-on cart an image
                                      names; every one ever published stays
      moybyte.<cart>-<hex>.zip     <- each official cart (a seed cart but a
                                      test fixture), and
      moybyte.<cart>-<hex>.png        its cover: one asset for every board,
                                      kept while an index references it
      carts-tdeck.json             <- the index listing this build's official
      moybyte_web-licence.md          carts and every add-on for that board
                                      (tools/web_pack.py)

The `<board>-` prefix is how flat release assets keep the per-board layout that
tools/fetch_ci_firmware.py rebuilds on the way back down; board ids therefore
must not contain a dash.

Which images each board publishes is NOT decided here: they are read out of
site/build.py's BOARDS table (`downloads`), the same table the page's flasher
writes from, so there is one list of "the image we flash" and not two.

    tools/publish_firmware_release.py --artifacts artifacts --channel stable

Needs the `gh` CLI with `contents: write`. Boards absent from the artifacts
folder are left untouched on the release -- a single-board dispatch must not
delete the other board's image.
"""

import argparse
import datetime
import html
import importlib.util
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ota_sign  # noqa: E402
import web_pack  # noqa: E402
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
# The KEY is the board's `[board] ota` id -- the string stamped into the image
# by build.sh, named by the CI matrix row, and asked for by the device as
# `latest-<board>.json`. It is not the board's short name and not its directory.
OTA_IMAGES = {
    "tdeck": "moybyte_tdeck_app.bin",
    "p4": "moybyte_p4_app.bin",
    "guition_s3": "moybyte_guition_s3_app.bin",
    # The Zero (#41), OTA-wired 2026-08-29 and given its flasher card the same
    # day, so it publishes both halves like every other board.
    "xiao_zero": "moybyte_zero_app.bin",
    # The Guition P4 (2026-09-06): OTA-shaped from its first flash like the
    # Waveshare.
    "guition_p4": "moybyte_guition_p4_app.bin",
}
# Boards whose partition table has ONE app slot, so no OTA payload can install
# on them: their build is no miss in OTA_IMAGES, and the release publishes
# only their browser console add-on and its index, which a cable flash
# installs from (tools/board_flash.py). The C3 Zero (its board.toml).
NO_OTA = {"c3_zero": "moybyte_c3_zero_app.bin"}
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

Flash it from the site, which installs a board fresh (its internal store
erased, an SD card not touched), or over a cable, which keeps the store:

{cable}
"""

NOTES_OTA = """
### Over the air

`latest-<board>.json` is this channel's OTA manifest for that board, beside the
app image it describes and the browser console add-on cart that image names
(`moybyte_web-<digest>.moy.zip`, listed by `carts-<board>.json`; every one is
kept, so an earlier image still finds its own) — the device's Settings → UPDATE ONLINE reads them
directly, so no host of your own is needed. One per board on purpose: an OTA
payload is an app-partition image, so the T-Deck's is Xtensa and the P4's is
RISC-V, and the board is inside the signature.

"""


def label(board):
    """A board's name as MARKDOWN. site/build.py's labels are HTML source --
    `3.5&Prime;`, `&mdash;` -- because their home is a web page, and release
    notes are rendered as Markdown, where an entity is just the literal text.
    Every release before 2026-08-30 named the Guition `3.5&Prime;`."""
    return html.unescape(board["label"])


def row_name(board):
    return label(board) + (" (beta)" if board.get("beta") else "")


def cable_block(boards):
    """The `make firmware-flash-*` lines, DERIVED from the same BOARDS table the
    row list above comes from.

    Hand-written until 2026-08-30, and it had drifted the way a hand-written
    copy of a growing list always does: it named the T-Deck and the P4 while
    four boards were being published, so every release told a Guition or a Zero
    owner there was no cable command for their board. The `cli` field is
    already the browser flasher's counterpart -- there was never a second list
    to keep, only a second COPY.
    """
    width = max(len(b["cli"]) for b in boards)
    lines = ["%-*s  # %s" % (width, b["cli"], label(b)) for b in boards]
    return "```\n" + "\n".join(lines) + "\n```"


def _site():
    path = os.path.join(ROOT, "site", "build.py")
    spec = importlib.util.spec_from_file_location("_moy_site_build", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def boards_table():
    """site/build.py's BOARDS -- the one list of what we flash per board."""
    return _site().BOARDS


def site_downloads(board):
    """site/build.py's downloads of `board`: [(kind, file name)], the
    install (tools/store_image.py, which CI's build writes) first."""
    return _site().downloads(board)


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
        names = [n for _kind, n in site_downloads(board)
                 if os.path.exists(os.path.join(folder, n))]
        if not names:
            print("%-6s not in this run's artifacts -- leaving the release's "
                  "copy alone" % board["id"])
            continue
        name = names[0]
        for n in names:
            shutil.copyfile(os.path.join(folder, n),
                            os.path.join(workdir, "%s-%s" % (board["id"], n)))
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
    device's updater follows -- native/moy_net/moy_ota.c's client)."""
    repo = repo or os.environ.get("GITHUB_REPOSITORY") or "moybyte-org/moybyte"
    return "https://github.com/%s/releases/download/%s/%s" % (repo, tag, name)


def stage_ota_all(tag, channel, artifacts, workdir, repo=None, refused=None,
                  earlier=None):
    """Stage every built board's OTA app image + its manifest.

    Returns {board: manifest}. A board absent from this run keeps whatever is
    already published for it, exactly like its flashable image, and so does a
    board whose page cannot be published (web_pack.PageError), which is named
    in `refused` and fails the run once the others are uploaded. `earlier`
    answers a board's published add-on index (its bytes, or None), which the
    new one keeps every entry of."""
    # A board that BUILT (its artifact folder is here) but is missing from
    # OTA_IMAGES would silently never get a manifest -- the miss that kept the
    # Guition's first beta off the channel on 2026-08-20 (the build emitted the
    # app image and the stamp; this dict was two boards long). Say so loudly.
    prefix = "moybyte-firmware-"
    if os.path.isdir(artifacts):
        for entry in sorted(os.listdir(artifacts)):
            if (entry.startswith(prefix) and entry[len(prefix):] not in OTA_IMAGES
                    and entry[len(prefix):] not in NO_OTA):
                print("::warning::%s built images but has no OTA_IMAGES entry "
                      "-- no OTA manifest will be published for it"
                      % entry[len(prefix):])
    out = {}
    for board in sorted(NO_OTA):
        try:
            stage_addon_only(artifacts, workdir, board,
                             earlier(board) if earlier else None)
        except web_pack.PageError as exc:
            print("::error::%s -- its add-on stays as published" % exc)
            if refused is not None:
                refused.append(board)
    for board in sorted(OTA_IMAGES):
        try:
            got = stage_ota(tag, channel, artifacts, workdir, repo, board,
                            earlier(board) if earlier else None)
        except web_pack.PageError as exc:
            print("::error::%s -- its manifest stays as published" % exc)
            if refused is not None:
                refused.append(board)
            continue
        if got:
            out[board] = got
    return out


def stage_addon_only(artifacts, workdir, board, earlier=None):
    """Stage a NO_OTA board's add-on cart and its index, or nothing when this
    run did not build the board. Raises web_pack.PageError like stage_ota."""
    src = os.path.join(artifacts, "moybyte-firmware-%s" % board, NO_OTA[board])
    if not os.path.exists(src):
        print("%-6s no %s in this run -- add-on unchanged" % (board, NO_OTA[board]))
        return None
    page = web_pack.page_beside(src, board)
    version = read_stamp(artifacts, board).get("version")
    if version is None:
        version = read_firmware_version()
    addon = web_pack.publish_addon(page, workdir, board, version,
                                   ota_sign.read_key(), earlier or b"")
    print("%-6s staged %s, %d official carts and %s (no OTA)"
          % (board, addon["cart"], addon["official"], addon["index"]))
    return addon


def stage_ota(tag, channel, artifacts, workdir, repo=None, board="tdeck",
              earlier=None):
    """Stage one board's OTA app image + `latest-<board>.json`, or None, and
    beside them the add-on cart of the page the image names and the board's
    add-on index over `earlier` (the published one's bytes). Raises
    web_pack.PageError when the run carries no page its image names."""
    image = OTA_IMAGES[board]
    folder = os.path.join(artifacts, "moybyte-firmware-%s" % board)
    src = os.path.join(folder, image)
    if not os.path.exists(src):
        print("%-6s no %s in this run -- OTA manifest unchanged" % (board, image))
        return None
    page = web_pack.page_beside(src, board)

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
    # The browser console add-on, beside the payload, named by the digest
    # the image carries: where a console installs it from.
    addon = web_pack.publish_addon(page, workdir, board, manifest["version"],
                                   ota_sign.read_key(), earlier or b"")
    manifest["cart"] = addon["cart"]
    print("%-6s staged %s, %d official carts and %s"
          % (board, addon["cart"], addon["official"], addon["index"]))
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
    the kernel's updater, moy_ota.C6Updater on the P4s, is the consumer). A run whose artifacts carry no
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


def published(tag, name):
    """The bytes of the asset `name` on the release, or None."""
    with tempfile.TemporaryDirectory(prefix="moy-asset-") as d:
        got = gh("release", "download", tag, "-p", name, "-D", d, "--clobber",
                 check=False)
        path = os.path.join(d, name)
        if got.returncode != 0 or not os.path.exists(path):
            return None
        with open(path, "rb") as f:
            return f.read()


def release_assets(tag):
    """The names of the release's assets, or None when it cannot be read."""
    got = gh("release", "view", tag, "--json", "assets", check=False)
    if got.returncode != 0:
        return None
    try:
        return [a["name"] for a in json.loads(got.stdout)["assets"]]
    except (ValueError, KeyError, TypeError):
        return None


def prune_carts(tag, workdir, names, before, dry_run=False):
    """Delete the official carts' zips and covers no index needs, after the
    upload: kept is everything an index on the release references now (the
    ones staged in `workdir`, and a board's this run did not build) or
    referenced before this publish (`before`, each index's bytes by name, a
    console a step behind its channel). The add-on carts are no official
    assets and stay for good. `names` is the release's assets before the
    upload. With `dry_run`, says what it would delete. -> those names."""
    had = set(names)
    names = had | set(os.listdir(workdir))
    indexes = [b for b in before.values() if b is not None]
    for name in sorted(names):
        if not re.match(r"^carts-[^-]+\.json$", name):
            continue
        if name in had and name in before and before[name] is None:
            print("::warning::%s could not be read before this publish "
                  "-- no cart pruned" % name)
            return []
        staged = os.path.join(workdir, name)
        if os.path.exists(staged):
            with open(staged, "rb") as f:
                indexes.append(f.read())
        else:
            data = before[name] if name in before else published(tag, name)
            if data is None:
                print("::warning::%s could not be read -- no cart pruned" % name)
                return []
            indexes.append(data)
    try:
        stale = web_pack.official_assets(names, indexes)
    except ValueError:
        print("::warning::an index on %s is not one -- no cart pruned" % tag)
        return []
    for name in stale:
        if dry_run:
            print("would delete %s" % name)
        else:
            gh("release", "delete-asset", tag, name, "--yes")
            print("deleted %s" % name)
    return stale


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
            rows.append("| %s | — | — | not published yet | — |" % row_name(board))
            continue
        commit = src.get("commit", "")
        rows.append("| %s | `%s-%s` | `0x%x` | %s | %s |"
                    % (row_name(board), board["id"], src.get("image", "?"),
                       board["offset"], src.get("built", "?")[:10],
                       ("`%s`" % commit[:7]) if commit else "—"))
    head = NOTES_HEAD.format(channel="beta" if channel == "unstable" else channel,
                             branch=spec["branch"], blurb=spec["blurb"], site=site)
    betas = "".join("\n**%s is beta:** %s\n" % (label(b), b["beta"])
                    for b in boards if b.get("beta"))
    body = (head + "\n".join(rows) + "\n" + betas
            + NOTES_TAIL.format(cable=cable_block(boards)))
    if manifests:
        body += NOTES_OTA
        for board in sorted(manifests):
            m = manifests[board]
            body += ("- **%s** — `%s` described by `%s`, running **%s** "
                     "(version `%s`)%s\n"
                     % (board, "%s-%s" % (board, OTA_IMAGES[board]),
                        manifest_name(board), m.get("label", "?"),
                        m.get("version", "?"),
                        ", its browser console `%s`" % m["cart"] if m.get("cart") else ""))
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
        refused = []
        before = {}

        def earlier(board):
            name = web_pack.INDEX_NAME % board
            before[name] = published(tag, name)
            return before[name]
        manifests = stage_ota_all(tag, channel, os.path.abspath(args.artifacts),
                                  workdir, refused=refused, earlier=earlier)
        if not staged and not manifests:
            print("nothing built in this run -- the release is unchanged")
            return 1 if refused else 0
        if not staged:
            # A board can have an OTA manifest and NO cable-flash image on the
            # release: `stage()` walks site/build.py's BOARDS (the website's
            # flasher cards) and `stage_ota_all` walks OTA_IMAGES, which are two
            # lists and not one. They happen to name the same boards today
            # -- they did NOT for the few hours between the Zero becoming a
            # build target and its card landing, which is when this branch was
            # written. Bailing on an empty `staged` would throw away a perfectly
            # good manifest whenever the only board built is one the site does
            # not flash, so it stays whether or not the lists agree this week.
            print("no flashable images in this run -- publishing OTA manifests "
                  "only (%s)" % ", ".join(sorted(manifests)))
        names = release_assets(tag)
        if args.dry_run:
            print("would upload to %s: %s"
                  % (tag, ", ".join(sorted(os.listdir(workdir)))))
            if names is not None:
                prune_carts(tag, workdir, names, before, dry_run=True)
            return 1 if refused else 0

        # Create on first use; after that every publish just replaces assets.
        if gh("release", "view", tag, check=False).returncode != 0:
            gh("release", "create", tag, "--title", spec["title"], "--prerelease",
               "--notes", "Publishing…")
            print("created the %s release" % tag)

        # Everything staged, and only what was staged: a board this run did not
        # build keeps the image already on the release, and no page is ever
        # removed, so an image rolled back to or installed by hand still finds
        # the one it names.
        files = [os.path.join(workdir, n) for n in sorted(os.listdir(workdir))]
        gh("release", "upload", tag, *files, "--clobber")
        print("uploaded: %s" % ", ".join(os.path.basename(f) for f in files))
        prune_carts(tag, workdir, names or [], before)

        # Written AFTER the upload, so the table describes what is now there.
        body = os.path.join(workdir, "notes.md")
        with open(body, "w", encoding="utf-8") as f:
            f.write(notes(tag, channel, boards, workdir, args.site, manifests))
        gh("release", "edit", tag, "--notes-file", body)
        if refused:
            print("refused (no page to publish): %s" % ", ".join(refused))
            return 1
        return 0
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
