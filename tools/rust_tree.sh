#!/usr/bin/env bash
# Put a Rust toolchain at its pin:
#
#   tools/rust_tree.sh CHANNEL [--target T]... [--component C]...
#   tools/rust_tree.sh esp@VERSION
#
# A Rust build compiles with the toolchain its pin names, never with whatever
# a machine last installed (native/moy_index/rust/build.sh names the pins).
# Everything lands user-level, under ~/.rustup and ~/.cargo; nothing here
# touches a system path.
#
#   CHANNEL      a rustup channel that names one release (`1.97.1`,
#                `nightly-2026-10-05`). Installed with the minimal profile and
#                the targets and components asked for when any of them is
#                missing; present, nothing happens, silently, and nothing is
#                fetched.
#   esp@VERSION  Espressif's fork of rustc for the Xtensa targets, which rustup
#                cannot install: espup installs it as the toolchain `esp`, and
#                VERSION is the fork's release (`1.97.0.0`, what `rustc +esp
#                --version` ends with). At VERSION with its rust-src, nothing
#                happens; anywhere else, espup ESPUP_VERSION (fetched once into
#                ~/.cache/moybyte) installs VERSION over it.
#
# Either way the result is read back, and a toolchain still not at its pin
# exits 1. MOYBYTE_ESPUP names an espup binary to use instead (the tests point
# it at a stub).

set -euo pipefail

ESPUP_VERSION="${ESPUP_VERSION:-0.17.1}"

die() { echo "!! rust_tree: $*" >&2; exit 1; }

[ $# -ge 1 ] || die "usage: rust_tree.sh CHANNEL [--target T]... [--component C]... | esp@VERSION"
pin="$1"; shift
case "${pin}" in
  [0-9]*.[0-9]*.[0-9]* | nightly-[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9] \
    | beta-[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9] | esp@[0-9]*.[0-9]*) ;;
  *) die "${pin} floats: a pin names one release (1.97.1, nightly-2026-10-05, esp@1.97.0.0)" ;;
esac

esp_at() {
  local v
  v="$(rustc +esp --version 2>/dev/null)" || return 1
  case "${v}" in *"(${1})") ;; *) return 1 ;; esac
  [ -d "$(rustc +esp --print sysroot)/lib/rustlib/src/rust/library" ]
}

if [ "${pin#esp@}" != "${pin}" ]; then
  want="${pin#esp@}"
  esp_at "${want}" && exit 0
  have="$(rustc +esp --version 2>/dev/null || echo "no esp toolchain")"
  echo "== the esp toolchain is ${have}, not ${want}: installing it with espup ${ESPUP_VERSION}"
  espup="${MOYBYTE_ESPUP:-${XDG_CACHE_HOME:-${HOME}/.cache}/moybyte/espup-v${ESPUP_VERSION}}"
  if [ ! -x "${espup}" ]; then
    mkdir -p "$(dirname "${espup}")"
    url="https://github.com/esp-rs/espup/releases/download/v${ESPUP_VERSION}/espup-$(uname -m)-unknown-linux-gnu"
    curl -fsSL -o "${espup}.part" "${url}" || die "cannot fetch ${url}"
    chmod +x "${espup}.part" && mv "${espup}.part" "${espup}"
  fi
  "${espup}" install --toolchain-version "${want}" --targets esp32s3 \
    --export-file "$(dirname "${espup}")/export-esp.sh" \
    || die "espup cannot install the esp toolchain ${want}"
  esp_at "${want}" || die "the esp toolchain is $(rustc +esp --version 2>/dev/null || echo absent), not ${want}, after installing it"
  exit 0
fi

targets=() components=()
while [ $# -gt 0 ]; do
  case "$1" in
    --target) targets+=("$2"); shift 2 ;;
    --component) components+=("$2"); shift 2 ;;
    *) die "unknown argument $1" ;;
  esac
done

missing() {
  rustup toolchain list | grep -q "^${pin}-" || { echo toolchain; return; }
  local have
  have="$(rustup target list --installed --toolchain "${pin}") $(rustup component list --installed --toolchain "${pin}")"
  local t
  for t in "${targets[@]}"; do
    grep -qw -- "${t}" <<<"${have}" || { echo "target ${t}"; return; }
  done
  for t in "${components[@]}"; do
    grep -qE -- "(^|[[:space:]])${t}(-[a-z0-9_]+-[a-z0-9_]+-[a-z0-9_]+(-[a-z0-9_]+)?)?($|[[:space:]])" <<<"${have}" \
      || { echo "component ${t}"; return; }
  done
}

what="$(missing)"
[ -z "${what}" ] && exit 0
echo "== rust ${pin}: no ${what}; installing"
args=()
for t in "${targets[@]}"; do args+=(--target "${t}"); done
for t in "${components[@]}"; do args+=(--component "${t}"); done
rustup toolchain install "${pin}" --profile minimal "${args[@]}" \
  || die "rustup cannot install ${pin}"
what="$(missing)"
[ -z "${what}" ] || die "rust ${pin} still has no ${what} after installing it"
