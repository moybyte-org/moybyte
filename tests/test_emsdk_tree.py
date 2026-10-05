"""`tools/emsdk_tree.sh`: the emscripten the web runner compiles with is the
version the build names, or the build fails.

emcc's output is not byte-reproducible across versions and the bundle rides
every board image, so `firmware/web_runner/build.sh` names one `EMSDK_VERSION`
and a machine's emsdk is moved to it. These run the shell against a stub
`emsdk` script (committed to a local repository standing in for upstream,
`MOYBYTE_EMSDK_URL`), so clone, no-op, move, the update-and-retry, and every
refusal run here without a download.
"""

import os
import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "tools" / "emsdk_tree.sh"
BUILD = ROOT / "firmware" / "web_runner" / "build.sh"
NOWHERE = "file:///no/such/emsdk"

GIT_ENV = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}

# What the real one does that the script reads: `install V` fails for a version
# its checkout does not list; `activate V` writes `.emscripten` and the version
# file. EMSDK_STUB_NO_ACTIVATE makes `activate` succeed without taking effect.
STUB = r'''#!/usr/bin/env bash
here="$(cd "$(dirname "$0")" && pwd)"
echo "$@" >> "${here}/calls.log"
case "$1" in
  install)
    grep -qx "$2" "${here}/available" || { echo "error: tool or SDK not found: '$2'" >&2; exit 1; } ;;
  activate)
    [ -n "${EMSDK_STUB_NO_ACTIVATE:-}" ] && exit 0
    mkdir -p "${here}/upstream/emscripten"
    echo "EMSCRIPTEN_ROOT = '\$CFGDIR/upstream/emscripten'" > "${here}/.emscripten"
    echo "\"$2\"" > "${here}/upstream/emscripten/emscripten-version.txt" ;;
esac
'''


def _env(**over):
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"),
           "HOME": os.environ.get("HOME", "/tmp"), "LANG": "C"}
    env.update(GIT_ENV)
    env.update({k: str(v) for k, v in over.items()})
    return env


def git(cwd, *args):
    r = subprocess.run(["git", "-C", str(cwd)] + list(args), env=_env(),
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return r.stdout.strip()


def pin(tree, version, url=NOWHERE, **env):
    return subprocess.run(["bash", str(SCRIPT), str(tree), version],
                          env=_env(MOYBYTE_EMSDK_URL=url, **env),
                          capture_output=True, text=True)


def version(tree):
    p = Path(tree) / "upstream" / "emscripten" / "emscripten-version.txt"
    return p.read_text().strip().strip('"') if p.exists() else None


def calls(tree):
    p = Path(tree) / "calls.log"
    return p.read_text().splitlines() if p.exists() else []


def publish(repo, *versions):
    (repo / "available").write_text("".join(v + "\n" for v in versions))
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "versions " + " ".join(versions))


@pytest.fixture
def upstream(tmp_path):
    """A repository holding the stub `emsdk`, which knows 6.0.4 and 6.0.7."""
    up = tmp_path / "upstream"
    up.mkdir()
    git(up, "init", "-q", "-b", "main")
    (up / "emsdk").write_text(STUB)
    (up / "emsdk").chmod(0o755)
    publish(up, "6.0.4", "6.0.7")
    return up


@pytest.fixture
def url(upstream):
    return "file://%s" % upstream


@pytest.fixture
def tree(tmp_path, url):
    """A clone activated at 6.0.4, the state of a machine that ran `latest`
    when 6.0.4 was it."""
    t = tmp_path / "emsdk"
    subprocess.run(["git", "clone", "-q", url, str(t)], check=True, env=_env())
    assert pin(t, "6.0.4").returncode == 0
    (t / "calls.log").unlink()
    return t


def test_an_absent_tree_is_cloned_and_ends_at_the_version(tmp_path, url):
    t = tmp_path / "emsdk"
    r = pin(t, "6.0.7", url=url)
    assert r.returncode == 0, r.stderr
    assert version(t) == "6.0.7"
    assert calls(t) == ["install 6.0.7", "activate 6.0.7"]
    assert "cloning emsdk" in r.stdout


def test_a_tree_at_the_version_is_left_alone_and_silent(tree):
    """A warm build pays nothing: no output, no network, no emsdk call."""
    r = pin(tree, "6.0.4")                     # upstream unreachable
    assert r.returncode == 0, r.stderr
    assert r.stdout == "" and r.stderr == ""
    assert calls(tree) == []


def test_a_bumped_version_moves_the_tree(tree):
    """THE BUG: a machine whose emsdk was cloned under `latest` kept compiling
    with it. The tree is at 6.0.4; the build now names 6.0.7."""
    r = pin(tree, "6.0.7")
    assert r.returncode == 0, r.stderr
    assert version(tree) == "6.0.7"
    assert calls(tree) == ["install 6.0.7", "activate 6.0.7"]
    assert "not 6.0.7" in r.stdout


def test_moving_back_to_an_older_version_works_too(tree):
    assert pin(tree, "6.0.7").returncode == 0
    assert pin(tree, "6.0.4").returncode == 0
    assert version(tree) == "6.0.4"


def test_a_version_the_clone_predates_comes_with_one_update(tree, upstream, url):
    """The clone's manifest does not list 6.0.9; upstream's does now. The
    install fails once, emsdk's own checkout is pulled, and the retry works."""
    publish(upstream, "6.0.4", "6.0.7", "6.0.9")
    r = pin(tree, "6.0.9", url=url)
    assert r.returncode == 0, r.stderr
    assert version(tree) == "6.0.9"
    assert calls(tree) == ["install 6.0.9", "install 6.0.9", "activate 6.0.9"]
    assert "updating its checkout" in r.stdout


def test_a_version_nobody_has_fails_and_leaves_the_tree_alone(tree):
    r = pin(tree, "9.9.9")
    assert r.returncode == 1
    assert "emsdk_tree" in r.stderr and "9.9.9" in r.stderr
    assert version(tree) == "6.0.4", "a failed move must not compile with a third version"
    assert "activate 9.9.9" not in calls(tree)


def test_an_activation_that_does_not_take_fails(tree):
    r = pin(tree, "6.0.7", EMSDK_STUB_NO_ACTIVATE="1")
    assert r.returncode == 1
    assert "not 6.0.7" in r.stderr


def test_a_directory_that_is_not_an_emsdk_checkout_fails(tmp_path):
    d = tmp_path / "emsdk"
    d.mkdir()
    r = pin(d, "6.0.7")
    assert r.returncode == 1
    assert "not an emsdk checkout" in r.stderr


def test_an_unreachable_upstream_fails_to_clone(tmp_path):
    r = pin(tmp_path / "emsdk", "6.0.7")
    assert r.returncode == 1
    assert "cannot clone" in r.stderr


# -- the build names ONE version ------------------------------------------------


def test_the_web_build_pins_its_emscripten_through_the_helper():
    """`emsdk install latest` is how the web bundle came to depend on the day a
    machine first built it. The build names a version once and has the helper
    hold the tree to it; nothing else installs emsdk."""
    sh = BUILD.read_text()
    assert re.search(r'^EMSDK_VERSION="\$\{EMSDK_VERSION:-\d+\.\d+\.\d+\}"$', sh, re.M)
    assert 'tools/emsdk_tree.sh" "${EMSDK_DIR}" "${EMSDK_VERSION}"' in sh
    assert not re.search(r"emsdk\s+(install|activate)\s+latest", sh)
    for wf in (ROOT / ".github" / "workflows").glob("*.yml"):
        assert not re.search(r"emsdk\s+(install|activate)\s+latest", wf.read_text()), wf
