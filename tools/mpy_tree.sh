#!/usr/bin/env bash
# Put a MicroPython checkout at a tag:   tools/mpy_tree.sh DIR TAG [SEED]
#
# Every build that compiles MicroPython (the five boards' build.sh through
# `moybyte_clone_micropython`, the web runner's build.sh, `make
# unix-micropython`) keeps its checkout across builds, and until this script
# each of them cloned it only when DIR was absent. Bumping the tag on a warm
# tree therefore changed nothing: the patchers ran over the old sources and the
# image shipped them, with no error. The tag a build names is now a property of
# the tree it compiles, checked on every run:
#
#   * DIR absent  -- cloned at TAG (`--depth 1`), from SEED (a local MicroPython
#                    checkout, which saves the network) when one is given.
#   * DIR at TAG's commit -- nothing happens, silently. The patches the last
#                    build applied stay where they are, so a warm rebuild keeps
#                    its object files.
#   * DIR anywhere else -- TAG is fetched if the clone does not hold it (a
#                    `--depth 1 -b TAG` clone holds only its own tag), then the
#                    tree is reset to stock TAG: tracked files are overwritten,
#                    which drops every patch. Each patcher is idempotent from
#                    stock, so the build re-applies them on top, as it would on
#                    a fresh clone. Untracked files stay (they are what the
#                    build stages and overwrites).
#   * TAG cannot be had, or DIR is not a git checkout -- exit 1 and say so.
#                    Compiling whatever is there is the one thing never done.
#
# SUBMODULES are the caller's: each build runs `git submodule update` (or the
# port's `make submodules`) after this, which moves them to the pins of the
# tree's new commit.
#
# The tag is fetched from the clone's own origin first (a worktree's tree is a
# local clone of the main checkout's, which may hold it) and then from upstream.
# MOYBYTE_MPY_URL replaces upstream (the tests point it at a local repository).

set -euo pipefail

UPSTREAM="${MOYBYTE_MPY_URL:-https://github.com/micropython/micropython}"

die() { echo "!! mpy_tree: $*" >&2; exit 1; }

[ $# -ge 2 ] && [ $# -le 3 ] || die "usage: mpy_tree.sh DIR TAG [SEED]"
dir="$1"; tag="$2"; seed="${3:-}"

if [ ! -e "${dir}" ]; then
  echo "== cloning micropython ${tag}"
  if [ -n "${seed}" ] && [ -e "${seed}/.git" ]; then
    git clone --quiet "${seed}" "${dir}" || die "cannot clone ${seed}"
  else
    git clone --depth 1 -b "${tag}" "${UPSTREAM}" "${dir}" \
      || die "cannot clone ${tag} from ${UPSTREAM}"
  fi
fi
[ -e "${dir}/.git" ] || die "${dir} exists and is not a git checkout (remove it to have it cloned)"

g() { git -C "${dir}" "$@"; }

tag_commit() { g rev-parse -q --verify "refs/tags/${tag}^{commit}" 2>/dev/null || true; }

want="$(tag_commit)"
if [ -z "${want}" ]; then
  depth=""
  if [ "$(g rev-parse --is-shallow-repository)" = "true" ]; then depth="--depth=1"; fi
  for src in origin "${UPSTREAM}"; do
    # shellcheck disable=SC2086  # depth is one word or nothing
    if g fetch --quiet ${depth} "${src}" "+refs/tags/${tag}:refs/tags/${tag}" 2>/dev/null; then
      break
    fi
  done
  want="$(tag_commit)"
  [ -n "${want}" ] || die "tag ${tag} is not in ${dir} and could not be fetched from its origin or ${UPSTREAM}"
fi

have="$(g rev-parse -q --verify HEAD)" || die "${dir} has no commit checked out"
if [ "${have}" != "${want}" ]; then
  echo "== micropython tree ${dir} is at ${have:0:12}, not ${tag} (${want:0:12}): resetting it to stock ${tag}, patches re-apply"
  g checkout --quiet --force --detach "${want}" || die "cannot check out ${tag} in ${dir}"
  g reset --quiet --hard "${want}" || die "cannot reset ${dir} to ${tag}"
fi
[ "$(g rev-parse HEAD)" = "${want}" ] || die "${dir} is not at ${tag} after resetting it"
