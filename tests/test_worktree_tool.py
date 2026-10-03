"""`tools/worktree.py` against a throwaway repository: what `new` links and
what it makes its own, and the work `rm` refuses to throw away."""

import os
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))

import worktree                                                 # noqa: E402

MPY = "firmware/b/.build/micropython"


def git(cwd, *args):
    return subprocess.run(["git", "-c", "protocol.file.allow=always"] + list(args),
                          cwd=cwd, check=True, capture_output=True,
                          text=True).stdout.strip()


def commit(cwd, path, text, msg):
    p = os.path.join(cwd, path)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w") as f:
        f.write(text)
    git(cwd, "add", path)
    git(cwd, "commit", "-q", "-m", msg)
    return git(cwd, "rev-parse", "HEAD")


@pytest.fixture
def repo(tmp_path, monkeypatch):
    """A main checkout on `dev` with a venv, a host cache, an IDF, and a
    MicroPython tree that has a submodule and a patched (dirty) file."""
    cfg = tmp_path / "gitconfig"
    cfg.write_text("[user]\n\tname = t\n\temail = t@t\n[init]\n\tdefaultBranch = dev\n")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(cfg))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    main = tmp_path / "main"
    main.mkdir()
    git(main, "init", "-q")
    commit(main, "README", "hi\n", "first")
    (main / ".venv" / "bin").mkdir(parents=True)
    (main / ".venv" / "bin" / "python").write_text("venv")
    (main / ".build" / "host_gfx").mkdir(parents=True)
    (main / ".build" / "host_gfx" / "x.so").write_text("so")
    (main / "firmware" / "a" / ".build" / "esp-idf").mkdir(parents=True)
    (main / "firmware" / "a" / ".build" / "esp-idf" / "export.sh").write_text("")
    sub = tmp_path / "sublib"
    sub.mkdir()
    git(sub, "init", "-q")
    commit(sub, "lib.c", "int x;\n", "lib")
    mpy = main / MPY
    mpy.mkdir(parents=True)
    git(mpy, "init", "-q")
    commit(mpy, "py/main.c", "stock\n", "v1.28")
    git(mpy, "submodule", "add", "-q", str(sub), "lib/sub")
    git(mpy, "commit", "-q", "-m", "submodule")
    (mpy / "py" / "main.c").write_text("patched\n")
    return main


def new(main, name="wt", *more):
    return worktree.main(["new", name] + list(more), start=str(main))


def rm(main, name="wt", *more):
    return worktree.main(["rm", name] + list(more), start=str(main))


def wt_path(main, name="wt"):
    return main / ".claude" / "worktrees" / name


def test_new_links_the_toolchains_and_caches(repo):
    assert new(repo) == 0
    wt = wt_path(repo)
    for rel in (".venv", ".build/host_gfx", "firmware/a/.build/esp-idf"):
        assert os.path.islink(wt / rel)
        assert os.path.realpath(wt / rel) == os.path.realpath(repo / rel)
    assert git(wt, "rev-parse", "--abbrev-ref", "HEAD") == "wt"


def test_new_gives_each_micropython_tree_its_own_stock_checkout(repo):
    new(repo)
    mine = wt_path(repo) / MPY
    assert not os.path.islink(mine) and os.path.isdir(mine / ".git")
    assert git(mine, "rev-parse", "HEAD") == git(repo / MPY, "rev-parse", "HEAD")
    assert (mine / "py" / "main.c").read_text() == "stock\n"
    assert (mine / "lib" / "sub" / "lib.c").read_text() == "int x;\n"
    (mine / "py" / "main.c").write_text("mine\n")
    assert (repo / MPY / "py" / "main.c").read_text() == "patched\n"


def test_new_refuses_a_name_in_use(repo, capsys):
    assert new(repo) == 0
    assert new(repo) == 2
    assert "already exists" in capsys.readouterr().err
    assert worktree.main(["new", "a/b"], start=str(repo)) == 2


def test_rm_refuses_changed_tracked_files_unless_forced(repo, capsys):
    new(repo)
    (wt_path(repo) / "README").write_text("changed\n")
    assert rm(repo) == 2
    assert "tracked file(s) changed" in capsys.readouterr().err
    assert os.path.isdir(wt_path(repo))
    assert rm(repo, "wt", "--force") == 0
    assert not os.path.lexists(wt_path(repo))


def test_rm_refuses_unmerged_commits_and_force_keeps_the_branch(repo, capsys):
    new(repo)
    commit(wt_path(repo), "new.txt", "work\n", "work")
    assert rm(repo) == 2
    assert "1 commit(s) not on dev" in capsys.readouterr().err
    assert rm(repo, "wt", "--force") == 0
    assert "branch wt kept" in capsys.readouterr().out
    assert git(repo, "branch", "--list", "wt")


def test_rm_counts_a_cherry_picked_landing_as_merged(repo, capsys):
    new(repo)
    sha = commit(wt_path(repo), "new.txt", "work\n", "work")
    commit(repo, "other.txt", "x\n", "other")
    git(repo, "cherry-pick", sha)
    assert rm(repo) == 0
    out = capsys.readouterr().out
    assert "freed" in out and "branch wt deleted" in out
    assert not git(repo, "branch", "--list", "wt")


def test_rm_leaves_what_the_links_point_at(repo):
    new(repo)
    os.makedirs(wt_path(repo) / MPY / "ports" / "build-X")
    assert rm(repo) == 0
    assert (repo / ".venv" / "bin" / "python").read_text() == "venv"
    assert (repo / ".build" / "host_gfx" / "x.so").read_text() == "so"
    assert (repo / "firmware" / "a" / ".build" / "esp-idf" / "export.sh").exists()
    assert (repo / MPY / "py" / "main.c").read_text() == "patched\n"


def test_rm_refuses_what_is_not_one_of_its_worktrees(repo, capsys):
    assert rm(repo, "nothing") == 2
    assert "is not a worktree" in capsys.readouterr().err
