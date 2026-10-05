"""`tools/mpy_tree.sh`: the MicroPython tree a build compiles is at the tag the
build names, or the build fails.

Every build keeps its `.build/micropython` across runs. Bumping `MPY_TAG` on a
warm one used to change nothing -- the clone ran only when the directory was
absent -- so the patchers ran over the OLD sources and the image shipped them,
with no error. These run the shell against a local repository that stands in
for upstream (`MOYBYTE_MPY_URL`), with two tags, so the whole path runs here:
clone, no-op, move, fetch of a tag the clone lacks, and every refusal.
"""

import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "tools" / "mpy_tree.sh"
LIB = ROOT / "tools" / "esp32_build_lib.sh"
NOWHERE = "file:///no/such/upstream"

GIT_ENV = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}


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


def pin(tree, tag, seed=None, url=NOWHERE):
    args = ["bash", str(SCRIPT), str(tree), tag] + ([str(seed)] if seed else [])
    return subprocess.run(args, env=_env(MOYBYTE_MPY_URL=url),
                          capture_output=True, text=True)


def head(tree):
    return git(tree, "rev-parse", "HEAD")


def tag_commit(repo, tag):
    return git(repo, "rev-parse", "refs/tags/%s^{commit}" % tag)


def write(repo, rel, text):
    p = Path(repo) / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


@pytest.fixture
def upstream(tmp_path):
    """Two tags: v1 (annotated) and v2 (lightweight, like a plain `git tag`).
    `same.h` is identical in both; `main.c` differs; `only_v2.c` is new in v2."""
    up = tmp_path / "upstream"
    up.mkdir()
    git(up, "init", "-q", "-b", "master")
    write(up, "py/main.c", "stock one\n")
    write(up, "ports/esp32/same.h", "same\n")
    git(up, "add", "-A")
    git(up, "commit", "-q", "-m", "one")
    git(up, "tag", "-a", "-m", "one", "v1")
    write(up, "py/main.c", "stock two\n")
    write(up, "ports/esp32/only_v2.c", "two\n")
    git(up, "add", "-A")
    git(up, "commit", "-q", "-m", "two")
    git(up, "tag", "v2")
    return up


@pytest.fixture
def url(upstream):
    return "file://%s" % upstream


def shallow_clone(url, dst, tag):
    """What the build scripts made: `git clone --depth 1 -b TAG`."""
    subprocess.run(["git", "clone", "-q", "--depth", "1", "-b", tag, url,
                    str(dst)], check=True, env=_env())


def patch(tree):
    """What the patchers leave: tracked files edited, one untracked file added."""
    write(tree, "py/main.c", "patched\n")
    write(tree, "ports/esp32/same.h", "patched same\n")
    write(tree, "ports/esp32/staged.csv", "staged\n")


# -- the clone ----------------------------------------------------------------


def test_an_absent_tree_is_cloned_at_the_tag(tmp_path, upstream, url):
    tree = tmp_path / "mpy"
    r = pin(tree, "v1", url=url)
    assert r.returncode == 0, r.stderr
    assert head(tree) == tag_commit(upstream, "v1")
    assert "cloning micropython v1" in r.stdout


def test_a_seed_is_cloned_from_and_still_ends_at_the_tag(tmp_path, upstream, url):
    """The web runner seeds from the P4's tree to skip the network; that tree is
    at whatever commit it is, and the new one still ends at the tag."""
    seed = tmp_path / "seed"
    subprocess.run(["git", "clone", "-q", url, str(seed)], check=True, env=_env())
    git(seed, "checkout", "-q", "v2")
    tree = tmp_path / "mpy"
    r = pin(tree, "v1", seed=seed)             # upstream unreachable: seed + its tags
    assert r.returncode == 0, r.stderr
    assert head(tree) == tag_commit(upstream, "v1")
    assert (tree / "py" / "main.c").read_text() == "stock one\n"
    assert not (tree / "ports" / "esp32" / "only_v2.c").exists()


def test_a_missing_seed_falls_back_to_upstream(tmp_path, upstream, url):
    tree = tmp_path / "mpy"
    r = pin(tree, "v2", seed=tmp_path / "no-seed", url=url)
    assert r.returncode == 0, r.stderr
    assert head(tree) == tag_commit(upstream, "v2")


# -- the warm tree --------------------------------------------------------------


def test_a_tree_at_the_tag_is_left_alone_and_silent(tmp_path, upstream, url):
    """A warm rebuild keeps its patches (and so its object files): no output,
    no network, no touched file."""
    tree = tmp_path / "mpy"
    shallow_clone(url, tree, "v1")
    patch(tree)
    before = (tree / "py" / "main.c").stat().st_mtime_ns
    r = pin(tree, "v1")                        # upstream unreachable
    assert r.returncode == 0, r.stderr
    assert r.stdout == "" and r.stderr == ""
    assert (tree / "py" / "main.c").read_text() == "patched\n"
    assert (tree / "py" / "main.c").stat().st_mtime_ns == before


def test_a_bumped_tag_resets_the_tree_to_stock(tmp_path, upstream, url):
    """THE BUG. The tree is at v1 with the patchers' edits on it; the build now
    names v2. It ends at v2's commit with v2's sources, every patch gone --
    including the one on a file v1 and v2 share, which a plain checkout of
    changed paths would have kept -- and the patchers' untracked files stay."""
    tree = tmp_path / "mpy"
    shallow_clone(url, tree, "v1")
    patch(tree)
    r = pin(tree, "v2", url=url)
    assert r.returncode == 0, r.stderr
    assert head(tree) == tag_commit(upstream, "v2")
    assert (tree / "py" / "main.c").read_text() == "stock two\n"
    assert (tree / "ports" / "esp32" / "same.h").read_text() == "same\n"
    assert (tree / "ports" / "esp32" / "only_v2.c").read_text() == "two\n"
    assert (tree / "ports" / "esp32" / "staged.csv").exists()
    assert git(tree, "status", "--porcelain", "--untracked-files=no") == ""
    assert "not v2" in r.stdout and "patches re-apply" in r.stdout


def test_moving_back_to_an_older_tag_works_too(tmp_path, upstream, url):
    tree = tmp_path / "mpy"
    shallow_clone(url, tree, "v2")
    patch(tree)
    assert pin(tree, "v1", url=url).returncode == 0
    assert head(tree) == tag_commit(upstream, "v1")
    assert (tree / "py" / "main.c").read_text() == "stock one\n"
    assert not (tree / "ports" / "esp32" / "only_v2.c").exists()


def test_a_tag_the_clone_lacks_comes_from_origin_before_upstream(
        tmp_path, upstream, url):
    """A worktree's tree is a local clone of the main checkout's: it names that
    tree as origin, and a tag the main tree holds needs no network."""
    main = tmp_path / "main"
    subprocess.run(["git", "clone", "-q", url, str(main)], check=True, env=_env())
    wt = tmp_path / "wt"
    subprocess.run(["git", "clone", "-q", "--no-checkout", str(main), str(wt)],
                   check=True, env=_env())
    git(wt, "checkout", "-q", "--detach", tag_commit(upstream, "v1"))
    git(wt, "tag", "-d", "v2")                 # a clone that does not hold it
    r = pin(wt, "v2")                          # upstream unreachable
    assert r.returncode == 0, r.stderr
    assert head(wt) == tag_commit(upstream, "v2")


def test_a_tag_the_clone_and_its_origin_lack_comes_from_upstream(
        tmp_path, upstream, url):
    """A tree cloned from another tree that holds only v1 (a worktree of a
    main checkout at the old tag): v2 is fetched from upstream, shallow."""
    other = tmp_path / "other"
    shallow_clone(url, other, "v1")
    assert git(other, "tag") == "v1"
    tree = tmp_path / "mpy"
    subprocess.run(["git", "clone", "-q", str(other), str(tree)], check=True,
                   env=_env())
    assert git(tree, "tag") == "v1"
    r = pin(tree, "v2", url=url)
    assert r.returncode == 0, r.stderr
    assert head(tree) == tag_commit(upstream, "v2")
    assert (tree / "py" / "main.c").read_text() == "stock two\n"


# -- the refusals ---------------------------------------------------------------


def test_a_tag_that_cannot_be_had_fails_and_leaves_the_tree_alone(
        tmp_path, upstream, url):
    tree = tmp_path / "mpy"
    shallow_clone(url, tree, "v1")
    patch(tree)
    r = pin(tree, "v9", url=url)
    assert r.returncode == 1
    assert "!! mpy_tree" in r.stderr and "v9" in r.stderr
    assert head(tree) == tag_commit(upstream, "v1")
    assert (tree / "py" / "main.c").read_text() == "patched\n"


def test_an_unreachable_upstream_fails_a_bump(tmp_path, upstream, url):
    tree = tmp_path / "mpy"
    shallow_clone(url, tree, "v1")
    git(tree, "remote", "set-url", "origin", NOWHERE)
    r = pin(tree, "v2")
    assert r.returncode == 1 and "!! mpy_tree" in r.stderr
    assert head(tree) == tag_commit(upstream, "v1")


def test_a_directory_that_is_not_a_checkout_is_refused(tmp_path, url):
    tree = tmp_path / "mpy"
    tree.mkdir()
    (tree / "stray").write_text("x", encoding="utf-8")
    r = pin(tree, "v1", url=url)
    assert r.returncode == 1 and "not a git checkout" in r.stderr
    assert sorted(p.name for p in tree.iterdir()) == ["stray"]


def test_a_failed_clone_fails(tmp_path):
    r = pin(tmp_path / "mpy", "v1")
    assert r.returncode == 1 and "!! mpy_tree" in r.stderr


# -- the callers ----------------------------------------------------------------


def _lib(script, **env):
    return subprocess.run(
        ["bash", "-c", "set -euo pipefail\nsource '%s'\n%s" % (LIB, script)],
        cwd=str(ROOT), env=_env(REPO_ROOT=ROOT, **env),
        capture_output=True, text=True)


def test_the_build_lib_moves_a_warm_tree_to_the_tag(tmp_path, upstream, url):
    tree = tmp_path / "mpy"
    shallow_clone(url, tree, "v1")
    patch(tree)
    r = _lib("moybyte_clone_micropython", MPY_DIR=tree, MPY_TAG="v2",
             MOYBYTE_MPY_URL=url)
    assert r.returncode == 0, r.stderr
    assert head(tree) == tag_commit(upstream, "v2")
    assert (tree / "py" / "main.c").read_text() == "stock two\n"


def test_the_build_lib_stops_the_build_when_the_tag_cannot_be_had(
        tmp_path, upstream, url):
    """The step under `set -e` is what makes the failure a stopped build."""
    tree = tmp_path / "mpy"
    shallow_clone(url, tree, "v1")
    r = _lib("moybyte_clone_micropython; echo PATCHING", MPY_DIR=tree,
             MPY_TAG="v9", MOYBYTE_MPY_URL=url)
    assert r.returncode != 0 and "PATCHING" not in r.stdout


def test_every_build_goes_through_the_one_script():
    """Five boards, the web runner and the desktop binary each keep a tree. A
    sixth place that clones on `[ ! -d ]` is the bug again."""
    callers = {
        "tools/esp32_build_lib.sh": "tools/mpy_tree.sh",
        "firmware/web_runner/build.sh": "tools/mpy_tree.sh",
        "Makefile": "tools/mpy_tree.sh",
    }
    for rel, needle in callers.items():
        assert needle in (ROOT / rel).read_text(), rel
    for build in sorted((ROOT / "firmware").glob("*/build.sh")):
        text = build.read_text()
        assert "micropython/micropython" not in text, (
            "%s clones MicroPython itself" % build.relative_to(ROOT))
        if build.parent.name != "web_runner":
            assert "moybyte_clone_micropython" in text, (
                "%s never pins its MicroPython tree" % build.relative_to(ROOT))
    for rel in ("tools/esp32_build_lib.sh", "Makefile"):
        assert "micropython/micropython" not in (ROOT / rel).read_text(), rel
