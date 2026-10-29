---
name: release
description: Cut a Moybyte firmware release or reason about branches and OTA channels -- what dev and master mean, what a push to each builds, and how `make release` merges, names, bumps and tags. Use before merging into master, pushing master, or touching FIRMWARE_VERSION.
---

# Branches, channels, releases

**`dev` is where work lands; `master` is what users get.** A change reaches
master only after a human has tested it on the boards it touches, and nothing a
board can run is ever pushed straight to master.

**The two branches ARE the two OTA channels.** A push to `dev` builds a beta
(channel `unstable` → the `firmware-beta` release); a push to `master` builds a
stable (→ `firmware-latest`, which the site's flasher writes and the stable OTA
offers). Host CI runs on both. The project site is two pages in one deploy
(`.github/workflows/pages.yml`): master's tree at `/moybyte/`, dev's at
`/moybyte/dev/`, and a push to either branch rebuilds both. Firmware
builds are path-filtered, and the workflow's per-ref `cancel-in-progress`
collapses a burst of dev pushes into one build of the last commit.

**The merge into master IS the release**:

```bash
make release NAME=0.7                 # merge dev -> master, name it, bump, commit, tag; stops
make release NAME=0.7 NOTES="..."     # ...with why, beside the constant
make release NAME=0.7 PUSH=1          # ...and push master + the tag
```

`tools/release.py` checks the tree is clean and both branches are level with
origin, runs `tools/preflight.sh`, merges `--no-ff dev`, bumps
`FIRMWARE_VERSION` and sets `FIRMWARE_NAME`, commits `release: v<NAME>`, tags
`v<NAME>`, and stops, printing the push command. Pushing master is the moment a
device somewhere is offered the build, so it stays a separate, deliberate
keystroke.

**Master can carry commits dev lacks** (a fix made there, a Pages change),
and then the merge conflicts and `tools/release.py` stops mid-merge. v0.15
(2026-10-28) hit this with two such commits that dev already carried in a
newer form: the resolution was dev's side of each file, after which the
merged tree must equal dev's but for `device/moy_ota.py`. Check that with
`git diff dev` before finishing. Cut from a worktree on master
(`tools/worktree.py new rel`, then check out master there), never in the
shared checkout. After the push, watch the CI run on master: preflight is
not the whole of it (CLAUDE.md, "push anything").

**`NAME` is the release** (`MAJOR.MINOR`; a third component only for a pure fix
release): the tag, the manifest `label`, the string on the kid's update screen.
`FIRMWARE_VERSION` is an opaque monotonic counter the device compares with `>`
— signed as an int, and betas stamp a build epoch into it, labelled
`beta <date>`. **Never bump it by hand**: a hand bump desynchronises the stamp
CI reads back out of the artifact. Re-cutting a name already tagged is refused,
which is the prompt to pick a fix release.

The OTA install machine, the signature policy and the channel stamp are
`.claude/rules/ota.md`.
