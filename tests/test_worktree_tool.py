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
    commit(main, ".gitignore", ".venv\n.build\nfirmware/*/.build\n", "ignores")
    commit(main, "README", "hi\n", "first")
    (main / ".venv" / "bin").mkdir(parents=True)
    (main / ".venv" / "bin" / "python").write_text("venv")
    (main / ".build" / "host").mkdir(parents=True)
    (main / ".build" / "host" / "x.so").write_text("so")
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
    for rel in (".venv", ".build/host", "firmware/a/.build/esp-idf"):
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
    assert (repo / ".build" / "host" / "x.so").read_text() == "so"
    assert (repo / "firmware" / "a" / ".build" / "esp-idf" / "export.sh").exists()
    assert (repo / MPY / "py" / "main.c").read_text() == "patched\n"


def test_rm_refuses_what_is_not_one_of_its_worktrees(repo, capsys):
    assert rm(repo, "nothing") == 2
    assert "is not a worktree" in capsys.readouterr().err


def prune(main, *more):
    return worktree.main(["prune", "--min-age", "0"] + list(more),
                         start=str(main))


def test_rm_counts_a_landing_rebased_through_a_conflict_as_merged(repo, capsys):
    new(repo)
    commit(wt_path(repo), "README", "theirs\n", "the change")
    commit(repo, "README", "ours\n", "dev moved")
    commit(repo, "README", "ours and theirs\n", "the change")
    assert rm(repo) == 0
    assert "branch wt deleted" in capsys.readouterr().out


def test_rm_reads_dev_as_the_remote_branch_when_there_is_one(repo, capsys):
    """A worktree pushes dev without moving the main checkout's dev."""
    remote = repo.parent / "remote.git"
    git(repo.parent, "init", "-q", "--bare", str(remote))
    git(repo, "remote", "add", "origin", str(remote))
    git(repo, "push", "-q", "origin", "dev")
    new(repo)
    commit(wt_path(repo), "new.txt", "work\n", "work")
    git(wt_path(repo), "push", "-q", "origin", "HEAD:dev")
    assert rm(repo) == 0
    assert "branch wt deleted" in capsys.readouterr().out


def test_prune_removes_landed_worktrees_and_keeps_the_rest(repo, capsys):
    for name in ("landed", "changed", "ahead"):
        new(repo, name)
    (wt_path(repo, "changed") / "README").write_text("changed\n")
    commit(wt_path(repo, "ahead"), "new.txt", "work\n", "work")
    capsys.readouterr()
    assert prune(repo, "--dry-run") == 0
    out = capsys.readouterr().out
    assert "would remove landed" in out
    assert "kept changed: 1 tracked file(s) changed" in out
    assert "kept ahead: 1 commit(s) not on dev" in out
    assert os.path.isdir(wt_path(repo, "landed"))
    assert prune(repo) == 0
    assert not os.path.lexists(wt_path(repo, "landed"))
    assert os.path.isdir(wt_path(repo, "changed"))
    assert os.path.isdir(wt_path(repo, "ahead"))


def test_prune_keeps_a_worktree_a_process_works_in(repo, capsys):
    new(repo, "live")
    p = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"],
                         cwd=str(wt_path(repo, "live")))
    try:
        capsys.readouterr()
        assert prune(repo) == 0
        assert "kept live: in use by pid %d" % p.pid in capsys.readouterr().out
        assert os.path.isdir(wt_path(repo, "live"))
    finally:
        p.kill()
        p.wait()


def test_prune_keeps_a_worktree_whose_tools_a_process_runs(repo, capsys):
    new(repo, "tools")
    script = wt_path(repo, "tools") / "wait.py"
    script.write_text("import time; time.sleep(30)\n")
    p = subprocess.Popen([sys.executable, str(script)], cwd=str(repo))
    try:
        capsys.readouterr()
        assert prune(repo) == 0
        assert "in use by pid %d" % p.pid in capsys.readouterr().out
        assert os.path.isdir(wt_path(repo, "tools"))
    finally:
        p.kill()
        p.wait()


def test_prune_keeps_a_worktree_made_moments_ago(repo, capsys):
    new(repo, "fresh")
    capsys.readouterr()
    assert worktree.main(["prune"], start=str(repo)) == 0
    assert "kept fresh: made 0 min ago" in capsys.readouterr().out
    assert os.path.isdir(wt_path(repo, "fresh"))


def test_prune_keeps_a_worktree_with_untracked_files(repo, capsys):
    new(repo, "notes")
    (wt_path(repo, "notes") / "notes.txt").write_text("a finding\n")
    capsys.readouterr()
    assert prune(repo) == 0
    assert "kept notes: 1 untracked file(s)" in capsys.readouterr().out
    assert os.path.isdir(wt_path(repo, "notes"))
