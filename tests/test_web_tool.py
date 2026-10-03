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
