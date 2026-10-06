"""`tools/rust_tree.sh`: a Rust build compiles with the toolchain its pin names,
or it does not compile.

The Rust twin of the store's index (native/moy_index/rust/) is built by three
toolchains: upstream for every target but the S3s (rust-toolchain.toml),
Espressif's fork for the S3s, and a dated nightly for Miri and the sanitizer
build (both pinned in its build.sh). These run the script against stub
`rustup`, `rustc` and `espup` programs keeping their state in a directory, so
the no-op, every install path and every refusal run here without a download.
"""

import os
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "tools" / "rust_tree.sh"
CRATE = ROOT / "native" / "moy_index" / "rust"
HOST = "x86_64-unknown-linux-gnu"

RUSTUP = r'''#!/usr/bin/env bash
s="${STUB_STATE}"
echo "rustup $*" >> "${s}/calls.log"
case "$1 $2" in
  "toolchain list") cat "${s}/toolchains" 2>/dev/null; exit 0 ;;
  "target list") cat "${s}/${5}.targets" 2>/dev/null; exit 0 ;;
  "component list") cat "${s}/${5}.components" 2>/dev/null; exit 0 ;;
  "toolchain install")
    [ -e "${s}/fail" ] && { echo "error: no release" >&2; exit 1; }
    tc="$3"; shift 3
    echo "${tc}-x86_64-unknown-linux-gnu" >> "${s}/toolchains"
    while [ $# -gt 0 ]; do
      case "$1" in
        --target) echo "$2" >> "${s}/${tc}.targets"; shift 2 ;;
        --component)
          [ "$2" = rust-src ] && echo "$2" >> "${s}/${tc}.components" \
            || echo "$2-x86_64-unknown-linux-gnu" >> "${s}/${tc}.components"
          shift 2 ;;
        *) shift ;;
      esac
    done ;;
esac
'''

RUSTC = r'''#!/usr/bin/env bash
s="${STUB_STATE}"
[ "$1" = "+esp" ] && [ -f "${s}/esp.version" ] || exit 1
case "$2" in
  --version) cat "${s}/esp.version" ;;
  --print) echo "${s}/esp" ;;
esac
'''

ESPUP = r'''#!/usr/bin/env bash
s="${STUB_STATE}"
echo "espup $*" >> "${s}/calls.log"
[ -e "${s}/espup_noop" ] && exit 0
while [ $# -gt 0 ]; do
  [ "$1" = --toolchain-version ] && v="$2"
  shift
done
echo "rustc 1.97.0-nightly (8ea53bcd7 2026-07-08) (${v})" > "${s}/esp.version"
mkdir -p "${s}/esp/lib/rustlib/src/rust/library"
'''


def stubs(tmp_path):
    state = tmp_path / "state"
    bin_ = tmp_path / "bin"
    state.mkdir()
    bin_.mkdir()
    for name, text in (("rustup", RUSTUP), ("rustc", RUSTC), ("espup", ESPUP)):
        p = bin_ / name
        p.write_text(text)
        p.chmod(0o755)
    return state, bin_


def run(tmp_path, *args, **env):
    state, bin_ = tmp_path / "state", tmp_path / "bin"
    e = {"PATH": "%s:%s" % (bin_, os.environ.get("PATH", "/usr/bin:/bin")),
         "HOME": str(tmp_path), "STUB_STATE": str(state),
         "MOYBYTE_ESPUP": str(bin_ / "espup"), "LANG": "C"}
    e.update(env)
    return subprocess.run(["bash", str(SCRIPT)] + list(args), env=e,
                          capture_output=True, text=True)


def calls(tmp_path):
    p = tmp_path / "state" / "calls.log"
    lines = p.read_text().splitlines() if p.exists() else []
    return [ln for ln in lines if " list" not in ln]


def have(tmp_path, tc, targets=(), components=()):
    state = tmp_path / "state"
    with open(state / "toolchains", "a") as f:
        f.write("%s-%s\n" % (tc, HOST))
    (state / ("%s.targets" % tc)).write_text("".join(t + "\n" for t in (HOST,) + tuple(targets)))
    (state / ("%s.components" % tc)).write_text("".join(c + "\n" for c in components))


def test_a_toolchain_at_its_pin_is_a_silent_no_op(tmp_path):
    stubs(tmp_path)
    have(tmp_path, "1.97.1", ["riscv32imafc-unknown-none-elf"])
    have(tmp_path, "nightly-2026-10-05", (),
         ["miri-%s" % HOST, "rust-src", "cargo-%s" % HOST])
    r = run(tmp_path, "1.97.1", "--target", "riscv32imafc-unknown-none-elf")
    assert r.returncode == 0 and r.stdout == "" and r.stderr == ""
    r = run(tmp_path, "nightly-2026-10-05", "--component", "miri",
            "--component", "rust-src")
    assert r.returncode == 0 and r.stdout == ""
    assert calls(tmp_path) == []


def test_a_missing_toolchain_target_or_component_is_installed(tmp_path):
    stubs(tmp_path)
    have(tmp_path, "1.97.1")
    r = run(tmp_path, "1.97.1", "--target", "wasm32-unknown-emscripten")
    assert r.returncode == 0, r.stderr
    assert calls(tmp_path) == ["rustup toolchain install 1.97.1 --profile minimal "
                               "--target wasm32-unknown-emscripten"]
    r = run(tmp_path, "nightly-2026-10-05", "--component", "miri")
    assert r.returncode == 0, r.stderr
    assert calls(tmp_path)[-1] == ("rustup toolchain install nightly-2026-10-05 "
                                   "--profile minimal --component miri")


def test_a_floating_channel_or_a_failed_install_stops_the_build(tmp_path):
    state, _ = stubs(tmp_path)
    for floating in ("stable", "nightly", "1.97", "esp", "esp@latest"):
        r = run(tmp_path, floating)
        assert r.returncode == 1 and "floats" in r.stderr, floating
    (state / "fail").write_text("")
    r = run(tmp_path, "1.97.1")
    assert r.returncode == 1 and "cannot install 1.97.1" in r.stderr


def test_the_esp_fork_at_its_release_is_a_no_op_and_moved_otherwise(tmp_path):
    state, _ = stubs(tmp_path)
    r = run(tmp_path, "esp@1.97.0.0")
    assert r.returncode == 0, r.stderr
    assert calls(tmp_path) == ["espup install --toolchain-version 1.97.0.0 "
                               "--targets esp32s3 --export-file %s/export-esp.sh"
                               % (tmp_path / "bin")]
    assert run(tmp_path, "esp@1.97.0.0").returncode == 0
    assert len(calls(tmp_path)) == 1
    r = run(tmp_path, "esp@1.98.0.0")
    assert r.returncode == 0 and "not 1.98.0.0" in r.stdout
    assert calls(tmp_path)[-1].startswith("espup install --toolchain-version 1.98.0.0 ")


def test_an_install_that_leaves_the_fork_elsewhere_fails(tmp_path):
    state, _ = stubs(tmp_path)
    (state / "espup_noop").write_text("")
    r = run(tmp_path, "esp@1.97.0.0")
    assert r.returncode == 1 and "after installing it" in r.stderr


def test_the_crate_names_three_pins_each_one_release():
    toml = (CRATE / "rust-toolchain.toml").read_text()
    build = (CRATE / "build.sh").read_text()
    upstream = re.search(r'^channel = "([^"]+)"$', toml, re.M).group(1)
    esp = re.search(r"^RUST_ESP=(\S+)", build, re.M).group(1)
    nightly = re.search(r"^RUST_NIGHTLY=(\S+)", build, re.M).group(1)
    assert re.fullmatch(r"\d+\.\d+\.\d+", upstream)
    assert re.fullmatch(r"\d+\.\d+\.\d+\.\d+", esp)
    assert re.fullmatch(r"nightly-\d{4}-\d{2}-\d{2}", nightly)
    # Every cargo the script runs names its toolchain or runs under the toml.
    assert 'bash "${TREE}" "esp@${RUST_ESP}"' in build
    assert all(re.search(r'cargo "(\+\$\{\w+\}|\$\{tc\})"', ln)
               for ln in build.splitlines() if re.match(r"\s*(exec )?cargo ", ln.strip()))
