#!/usr/bin/env bash
# Run a command in CI's OS: this tree, in the image tools/ci_image/Dockerfile
# builds (ubuntu 26.04 and Python 3.11, as `runs-on: ubuntu-latest` has them).
#
#   tools/ci_image/run.sh make unix-micropython
#   tools/ci_image/run.sh .venv/bin/python -m pytest -q tests/test_x.py
#   tools/ci_image/run.sh --rm-volumes      # drop this tree's container state
#   tools/ci_image/run.sh --check           # is Docker here and reachable?
#
# The tree is mounted at its own path, so the absolute paths a build bakes in
# (the web blob's .incbin, the usermod symlinks) and a worktree's links into the
# main checkout resolve the same inside as out. A worktree's main checkout is
# mounted read-only, its git directory read-write.
#
# What the container BUILDS stays in the container's own named volumes, never
# in the tree: `.venv` (Python 3.11, `make setup`'s, re-made when pyproject.toml
# or the image changes) and `.build` (the host libraries and the desktop
# MicroPython, compiled by the image's gcc against its glibc) are each mounted
# over the tree's, so a native run here and a container run never read each
# other's objects. The volumes are named for the tree's path, so two worktrees
# never share a build; pip's download cache is one volume for all of them.
# `tools/worktree.py rm` drops a worktree's volumes with it.
#
# It runs as the host's UID and GID (baked into the image as user `ci`), so
# what the suite writes into the tree is the owner's. CI=true, as on a runner:
# the checks that skip on a bench without a tool fail here as they do there.
# Everything else in the environment is the image's, not this shell's.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "${HERE}/../.." && pwd -P)"

if ! command -v docker >/dev/null 2>&1; then
  echo "ci_image: no docker on this machine -- the host suite runs in CI's OS" >&2
  echo "          (ubuntu 26.04) through it. Install Docker, or run" >&2
  echo "          tools/preflight.sh --native for the suite on this OS." >&2
  exit 2
fi
if ! docker info >/dev/null 2>&1; then
  echo "ci_image: docker is installed but this user cannot reach its daemon." >&2
  echo "          Start it, or add the user to the docker group:" >&2
  echo "            sudo usermod -aG docker \"\$USER\"   # then log in again" >&2
  echo "          or run tools/preflight.sh --native for the suite on this OS." >&2
  exit 2
fi
[ "${1:-}" = "--check" ] && exit 0

uid="$(id -u)"
gid="$(id -g)"
image_key="$(cat "${HERE}/Dockerfile" | sha256sum | cut -c1-12)"
IMAGE="moybyte-ci:${image_key}-${uid}-${gid}"
tree_key="$(printf '%s' "${ROOT}" | sha256sum | cut -c1-12)"
VOL_VENV="moybyte-ci-${tree_key}-venv"
VOL_BUILD="moybyte-ci-${tree_key}-build"
VOL_PIP="moybyte-ci-pip"

if [ "${1:-}" = "--rm-volumes" ]; then
  docker volume rm -f "${VOL_VENV}" "${VOL_BUILD}" >/dev/null
  echo "ci_image: removed ${VOL_VENV} ${VOL_BUILD}"
  exit 0
fi
[ "$#" -gt 0 ] || { sed -n '2,9p' "${BASH_SOURCE[0]}"; exit 2; }

if ! docker image inspect "${IMAGE}" >/dev/null 2>&1; then
  echo "ci_image: building ${IMAGE} (once per Dockerfile change)" >&2
  docker build -q -t "${IMAGE}" --build-arg HOST_UID="${uid}" \
    --build-arg HOST_GID="${gid}" "${HERE}" >/dev/null
  docker image ls --format '{{.Repository}}:{{.Tag}}' moybyte-ci \
    | grep -v "^${IMAGE}\$" | xargs -r docker image rm >/dev/null 2>&1 || true
fi

# A new volume is created root-owned; hand it to the user once.
fresh=()
for v in "${VOL_VENV}" "${VOL_BUILD}" "${VOL_PIP}"; do
  if ! docker volume inspect "${v}" >/dev/null 2>&1; then
    docker volume create "${v}" >/dev/null
    fresh+=(-v "${v}:/vol/${v}")
  fi
done
if [ "${#fresh[@]}" -gt 0 ]; then
  docker run --rm --user 0 "${fresh[@]}" "${IMAGE}" \
    sh -c "chown ${uid}:${gid} /vol/*" >/dev/null
fi

mounts=(-v "${ROOT}:${ROOT}")
common="$(git -C "${ROOT}" rev-parse --path-format=absolute --git-common-dir)"
case "${common}" in
  "${ROOT}"/*) ;;
  *) main="$(dirname "${common}")"
     mounts=(-v "${main}:${main}:ro" -v "${common}:${common}" "${mounts[@]}") ;;
esac
# A worktree's .venv is a link to the main checkout's: the volume goes where it
# points, inside the container only. A missing mountpoint is made here, as the
# user, or Docker would make it root's in the tree.
mkdir -p "${ROOT}/.build"
[ -e "${ROOT}/.venv" ] || mkdir "${ROOT}/.venv"
mounts+=(-v "${VOL_VENV}:$(readlink -f "${ROOT}/.venv")" -v "${VOL_BUILD}:${ROOT}/.build"
         -v "${VOL_PIP}:/ci/pip")

tty=()
[ -t 0 ] && [ -t 1 ] && tty=(-it)

# Another image wipes both volumes: its compiler built nothing in them. The
# venv is CI's `make setup`, re-run when pyproject.toml moves.
setup='if [ "$(cat .build/.ci_image 2>/dev/null)" != "${CI_IMAGE}" ]; then
  find .build "$(readlink -f .venv)" -mindepth 1 -delete
  echo "${CI_IMAGE}" > .build/.ci_image
fi
stamp="$(sha256sum pyproject.toml | cut -c1-16)"
if [ "$(cat .venv/.ci_stamp 2>/dev/null)" != "${stamp}" ]; then
  make --no-print-directory setup SYSTEM_PYTHON=python3.11 VENV="$(readlink -f .venv)" \
    >/tmp/setup.log 2>&1 || { tail -30 /tmp/setup.log; exit 1; }
  echo "${stamp}" > .venv/.ci_stamp
fi
exec "$@"'

exec docker run --rm --init "${tty[@]}" --shm-size=2g \
  --user "${uid}:${gid}" -e CI=true -e CI_IMAGE="${IMAGE}" -w "${ROOT}" "${mounts[@]}" \
  "${IMAGE}" bash -c "${setup}" ci-run "$@"
