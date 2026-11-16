"""The upstream releases are named once, in tools/upstream_pins.env, and every
patch in patches/ is tried against them by tools/patch_check.py.

Every board's build.sh takes MicroPython's tag and ESP-IDF's release from the
build lib, which sources the pin file; the web runner's build.sh sources it,
the Makefile includes it and CI reads it. A copy of a release in any of them
is the drift this file exists to stop: seven copies of the tag once agreed by
luck alone."""

import os
import re
import subprocess

import pytest

from tools import patch_check, upstream_pins

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PINS = upstream_pins.load()
RELEASE = re.compile(r"\bv\d+\.\d+\.\d+\b")


def _tracked(*patterns):
    out = subprocess.check_output(("git", "-C", ROOT, "ls-files") + patterns)
    return sorted(out.decode().split())


def _read(rel):
    with open(os.path.join(ROOT, rel), encoding="utf-8") as f:
        return f.read()


def test_the_pin_file_names_both_releases():
    assert set(PINS) == {"MOYBYTE_MPY_TAG", "MOYBYTE_IDF_VER"}
    for v in PINS.values():
        assert RELEASE.fullmatch(v), v


def test_a_line_bash_and_make_would_read_differently_is_refused(tmp_path):
    p = tmp_path / "pins.env"
    p.write_text('MOYBYTE_MPY_TAG="v1.0.0"\n', encoding="utf-8")
    with pytest.raises(ValueError):
        upstream_pins.load(str(p))


@pytest.mark.parametrize("rel", _tracked("*build.sh", ".github/workflows/*.yml", "Makefile"))
def test_no_build_script_or_workflow_spells_a_release(rel):
    text = _read(rel)
    for v in PINS.values():
        assert v not in text and v[1:] not in text, (
            "%s spells %s; read it from tools/upstream_pins.env" % (rel, v))
    hit = RELEASE.search(text)
    assert not hit, "%s pins a release itself: %s" % (rel, hit.group(0))


def test_the_build_lib_takes_both_releases_from_the_pin_file():
    lib = os.path.join(ROOT, "tools", "esp32_build_lib.sh")
    r = subprocess.run(
        ["bash", "-c", 'set -euo pipefail; unset MPY_TAG; source "%s"; '
         'echo "$MPY_TAG $MOYBYTE_IDF_VER"' % lib],
        capture_output=True, text=True, cwd=ROOT)
    assert r.returncode == 0, r.stderr
    assert r.stdout.split() == [PINS["MOYBYTE_MPY_TAG"], PINS["MOYBYTE_IDF_VER"]]
    assert not re.search(r"^\s*(MOYBYTE_IDF_VER|MOYBYTE_MPY_TAG)=", _read("tools/esp32_build_lib.sh"), re.M)


def test_an_MPY_TAG_in_the_environment_still_overrides_one_build():
    lib = os.path.join(ROOT, "tools", "esp32_build_lib.sh")
    r = subprocess.run(["bash", "-c", 'source "%s"; echo "$MPY_TAG"' % lib],
                       capture_output=True, text=True, cwd=ROOT,
                       env=dict(os.environ, MPY_TAG="v9.9.9"))
    assert r.stdout.strip() == "v9.9.9"


def test_every_board_build_reads_the_lib_and_the_web_runner_the_pin_file():
    boards = [b for b in _tracked("firmware/*/build.sh") if "web_runner" not in b]
    assert len(boards) >= 5
    for b in boards:
        assert 'source "${REPO_ROOT}/tools/esp32_build_lib.sh"' in _read(b), b
        assert not re.search(r"^\s*MPY_TAG=", _read(b), re.M), b
    assert 'source "${REPO_ROOT}/tools/upstream_pins.env"' in _read("firmware/web_runner/build.sh")


def test_make_builds_the_unix_micropython_at_the_pinned_tag():
    r = subprocess.run(["make", "--no-print-directory", "-s",
                        "--eval=moy-print-tag: ; @echo $(UNIX_MP_TAG)", "moy-print-tag"],
                       capture_output=True, text=True, cwd=ROOT,
                       env={k: v for k, v in os.environ.items() if k != "UNIX_MP_TAG"})
    assert r.returncode == 0, r.stderr
    assert r.stdout.split()[-1] == PINS["MOYBYTE_MPY_TAG"]


def test_ci_keys_its_caches_on_the_pins_and_checks_the_patches():
    fw = _read(".github/workflows/firmware-build.yml")
    assert "tools/upstream_pins.env >> \"$GITHUB_OUTPUT\"" in fw
    assert "idf-${{ matrix.board }}-${{ runner.os }}-${{ steps.pins.outputs.MOYBYTE_IDF_VER }}" in fw
    assert "tools/patch_check.py" in fw
    for wf in ("firmware-build.yml", "web-e2e.yml", "pages.yml"):
        for key in re.findall(r"key: (web-runner-.*)", _read(".github/workflows/" + wf)):
            assert "upstream_pins.env" in key, (wf, key)


# -- the patch check -------------------------------------------------------------

def _git(tree, *args):
    env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t",
               GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")
    subprocess.run(("git", "-C", str(tree)) + args, check=True, capture_output=True, env=env)


def _repo(path, files, tag=None):
    path.mkdir(parents=True)
    _git(path, "init", "-q")
    for rel, text in files.items():
        f = path / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(text, encoding="utf-8")
    _git(path, "add", ".")
    _git(path, "commit", "-q", "-m", "stock")
    if tag:
        _git(path, "tag", tag)
    return path


def _patch(path, rel, old, new):
    path.write_text("--- a/%s\n+++ b/%s\n@@ -1 +1 @@\n-%s\n+%s\n" % (rel, rel, old, new),
                    encoding="utf-8")
    return str(path)


CMAKE = ("set(IDF_VERSION_MAJOR 5)\nset(IDF_VERSION_MINOR 5)\n"
         "set(IDF_VERSION_PATCH 6)\n")
TEST_PINS = {"MOYBYTE_MPY_TAG": "v9.9.9", "MOYBYTE_IDF_VER": "v5.5.6"}


@pytest.fixture
def trees(tmp_path):
    mpy = _repo(tmp_path / "mpy", {"ports/esp32/main.c": "stock\n"}, tag="v9.9.9")
    idf = _repo(tmp_path / "idf", {"tools/cmake/version.cmake": CMAKE,
                                   "components/heap/x.c": "stock\n"})
    return {"mpy": str(mpy), "idf": str(idf)}


def test_patches_are_tried_against_the_index_so_a_patched_tree_still_answers(tmp_path, trees):
    p1 = _patch(tmp_path / "a.patch", "ports/esp32/main.c", "stock", "ours")
    p2 = _patch(tmp_path / "b.patch", "components/heap/x.c", "stock", "ours")
    (tmp_path / "mpy" / "ports" / "esp32" / "main.c").write_text("ours\n", encoding="utf-8")
    got = patch_check.check(trees, [p1, p2], TEST_PINS)
    assert got == [(p1, "mpy", None), (p2, "idf", None)]


def test_a_patch_the_release_moved_under_is_named(tmp_path, trees):
    p = _patch(tmp_path / "a.patch", "components/heap/x.c", "gone", "ours")
    [(_, kind, err)] = patch_check.check(trees, [p], TEST_PINS)
    assert kind == "idf" and err and "x.c" in err


def test_a_tree_off_its_pin_fails_every_patch_into_it(tmp_path, trees):
    p1 = _patch(tmp_path / "a.patch", "ports/esp32/main.c", "stock", "ours")
    p2 = _patch(tmp_path / "b.patch", "components/heap/x.c", "stock", "ours")
    pins = {"MOYBYTE_MPY_TAG": "v1.0.0", "MOYBYTE_IDF_VER": "v5.4.2"}
    [(_, _, e1), (_, _, e2)] = patch_check.check(trees, [p1, p2], pins)
    assert "not at MicroPython v1.0.0" in e1
    assert "is ESP-IDF v5.5.6, not v5.4.2" in e2


def test_a_staged_change_is_not_the_release(tmp_path, trees):
    f = tmp_path / "idf" / "components" / "heap" / "x.c"
    f.write_text("ours\n", encoding="utf-8")
    _git(tmp_path / "idf", "add", ".")
    p = _patch(tmp_path / "a.patch", "components/heap/x.c", "stock", "ours")
    [(_, _, err)] = patch_check.check(trees, [p], TEST_PINS)
    assert "staged changes" in err


@pytest.mark.parametrize("rel", _tracked("patches/*.patch"))
def test_every_patch_in_the_tree_belongs_to_one_upstream(rel):
    assert patch_check.tree_of(_read(rel)) in ("mpy", "idf")
