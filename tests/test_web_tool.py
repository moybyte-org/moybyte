"""`tools/web.py` with no browser: what a shot asks for, and what it prints."""

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))

import web                                                      # noqa: E402


class FakeServer:
    returncode = None

    def __init__(self):
        self.stopped = False

    def poll(self):
        return None

    def terminate(self):
        self.stopped = True

    def wait(self, timeout=None):
        return 0


def test_a_path_is_this_trees_console_served_for_the_shot(monkeypatch, capsys,
                                                          tmp_path):
    server, seen = FakeServer(), {}
    monkeypatch.setattr(web, "free_port", lambda: 4321)
    monkeypatch.setattr(web, "start_server", lambda port, extra=(), quiet=True: server)
    monkeypatch.setattr(web, "wait_listening", lambda port, proc: None)

    def shoot(url, out, width, height, wait_for, clicks, settle):
        seen.update(url=url, clicks=clicks, wait_for=wait_for, width=width)
        return ["pageerror: boom"]
    monkeypatch.setattr(web, "shoot", shoot)
    out = str(tmp_path / "s.png")
    assert web.main(["shot", "/?x=1", "--out", out, "--click", "#a",
                     "--click", "#b", "--wait-for", "canvas",
                     "--width", "640"]) == 0
    assert seen == {"url": "http://127.0.0.1:4321/?x=1", "clicks": ["#a", "#b"],
                    "wait_for": "canvas", "width": 640}
    assert server.stopped
    assert capsys.readouterr().out.splitlines() == [out, "pageerror: boom"]


def test_a_url_is_shot_as_it_is(monkeypatch, capsys):
    seen = []
    monkeypatch.setattr(web, "start_server", lambda *a, **k: 1 / 0)
    monkeypatch.setattr(web, "shoot", lambda url, *a: seen.append(url) or [])
    assert web.main(["shot", "http://example.test/", "--out", "/tmp/x.png"]) == 0
    assert seen == ["http://example.test/"]
    assert capsys.readouterr().out == "/tmp/x.png\n"


def test_no_console_built_says_how_to_build_one(monkeypatch, capsys):
    monkeypatch.setattr(web, "DIST", "/nonexistent/dist")
    assert web.main(["serve"]) == 2
    assert "build.sh" in capsys.readouterr().err


def _git(root, *args, when=None):
    import subprocess
    env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t",
               GIT_COMMITTER_EMAIL="t@t")
    if when is not None:
        env["GIT_COMMITTER_DATE"] = env["GIT_AUTHOR_DATE"] = "@%d +0000" % when
    subprocess.run(["git", "-C", str(root)] + list(args), check=True, env=env,
                   capture_output=True)


def test_a_dist_built_before_a_commit_to_what_it_bakes_is_named_stale(tmp_path):
    """A browser console served from a dist/ the tree has moved past shows
    code the tree no longer has (2026-10-31: Notes without its Run, reported
    against the commit the tree was at)."""
    root = tmp_path / "tree"
    (root / "runtime").mkdir(parents=True)
    (root / "docs").mkdir()
    dist = root / "firmware" / "web_runner" / "dist"
    dist.mkdir(parents=True)
    (dist / "index.html").write_text("<html>")
    os.utime(dist / "index.html", (1_700_000_000, 1_700_000_000))
    _git(root, "init", "-q")
    (root / "runtime" / "a.py").write_text("a = 1\n")
    _git(root, "add", "runtime/a.py")
    _git(root, "commit", "-q", "-m", "before", when=1_699_990_000)
    assert web.dist_behind(str(dist), str(root)) is None, "built after every commit"
    (root / "docs" / "d.md").write_text("words\n")
    _git(root, "add", "docs/d.md")
    _git(root, "commit", "-q", "-m", "a doc", when=1_700_010_000)
    assert web.dist_behind(str(dist), str(root)) is None, "a doc is not what it bakes"
    (root / "runtime" / "a.py").write_text("a = 2\n")
    _git(root, "commit", "-q", "-am", "after", when=1_700_020_000)
    got = web.behind_note(str(dist), str(root))
    assert got is not None and got.startswith("STALE: firmware/web_runner/dist was built before ")
    assert web.dist_behind(str(tmp_path / "none"), str(root)) is None, "no build, no note"
