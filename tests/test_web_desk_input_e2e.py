"""A desk keyboard's shortcuts in the hosted console, in a real browser
(the host and BLE paths are tests/test_desk_keys.py's): Chrome's own key
events, with their modifiers, through page_core.html's handler, the worker
and the console.

    MOYBYTE_WEB_E2E=1 .venv/bin/python -m pytest tests/test_web_keys_e2e.py

One Chrome boot a test.
"""

import json
import os
import subprocess
import sys
import time
import urllib.request

import pytest

import web_e2e
from web_e2e import RUNNER

pytestmark = pytest.mark.skipif(
    not os.environ.get("MOYBYTE_WEB_E2E"),
    reason="MOYBYTE_WEB_E2E not set (spawns headless Chrome)")

CART = "moybyte.star_catcher.moy"


@pytest.fixture
def base():
    web_e2e.require()
    port = web_e2e.free_port()
    p = subprocess.Popen([sys.executable, "serve.py", str(port), "dist"], cwd=RUNNER,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    url = "http://127.0.0.1:%d" % port
    try:
        for _ in range(50):
            try:
                urllib.request.urlopen(url + "/index.html", timeout=1).read(64)
                break
            except OSError:
                time.sleep(0.1)
        else:
            pytest.fail("serve.py never answered")
        yield url
    finally:
        p.terminate()
        p.wait(timeout=10)


def _probe(tag):
    return {"js": "window.__moyState().then(s => '%s ' + JSON.stringify("
                  "{edit: s.edit, wins: s.wins, focus: s.focus}))" % tag}


def _edit(tab):
    return [
        {"note": "the console up", "wait": 4000},
        {"js": "window.__moyEdited = null; WORKER.postMessage({t: 'edit', cart: %s, "
               "tab: %s}); 'sent'" % (json.dumps(CART), json.dumps(tab))},
        {"waitFor": "window.__moyEdited", "timeout": 20000},
        {"wait": 1500},
        {"js": "document.getElementById('cv').focus(); 'focused'"},
    ]


def _tagged(js):
    out = {}
    for j in js:
        if isinstance(j, str) and " " in j and j.split(" ", 1)[0].isupper():
            tag, body = j.split(" ", 1)
            out[tag] = json.loads(body)
    return out


def _run(tmp_path, base, name, steps):
    out, js = web_e2e.run(tmp_path, name, "?desktop=1&dev=1", steps, base,
                          tmp_path / ("chrome_" + name), boot_ms=30000)
    got = _tagged(js)
    return out, got


def test_the_sprite_tabs_keys_in_chrome(tmp_path, base):
    """S selects nothing and shifts nothing; Ctrl+Z / Ctrl+Y walk the
    history; Ctrl+C then Del then Ctrl+V puts the sprite back."""
    steps = _edit("paint") + [
        _probe("START"),
        {"key": "f"}, {"wait": 300}, _probe("FLIPPED"),
        {"key": "s"}, {"key": "w"}, {"wait": 300}, _probe("LETTERS"),
        {"key": "z", "mods": ["ctrl"]}, {"wait": 300}, _probe("UNDONE"),
        {"key": "y", "mods": ["ctrl"]}, {"wait": 300}, _probe("REDONE"),
        {"key": "c", "mods": ["ctrl"]}, {"key": "Delete"}, {"wait": 300}, _probe("CLEARED"),
        {"key": "v", "mods": ["ctrl"]}, {"wait": 300}, _probe("PASTED"),
    ]
    out, got = _run(tmp_path, base, "keys_sprite", steps)
    sprite = {t: got[t]["edit"]["sprite"] for t in got}
    assert got["START"]["edit"]["tab"] == "paint", out[-3000:]
    assert sprite["FLIPPED"] != sprite["START"], "F did not flip"
    assert sprite["LETTERS"] == sprite["FLIPPED"], "a letter moved the sprite"
    assert sprite["UNDONE"] == sprite["START"], "Ctrl+Z did not undo"
    assert sprite["REDONE"] == sprite["FLIPPED"], "Ctrl+Y did not redo"
    assert set(sprite["CLEARED"]) == {"0"}, "Del did not clear"
    assert sprite["PASTED"] == sprite["FLIPPED"], "Ctrl+V did not paste the copy"


def test_shift_tab_super_and_f11_in_chrome(tmp_path, base):
    """Shift+Tab outdents in the Code tab, a lone Meta tap opens the system
    menu and Meta+key does not, and F11 fills the screen with the window."""
    steps = _edit("code") + [
        _probe("START"),
        {"key": "a", "mods": ["ctrl"]},
        {"key": "Tab"}, {"wait": 300}, _probe("TABBED"),
        {"key": "Tab", "mods": ["shift"]}, {"wait": 300}, _probe("UNTABBED"),
        {"key": "F11"}, {"wait": 600}, _probe("FULL"),
        {"key": "F11"}, {"wait": 600}, _probe("BACK"),
        {"key": "Meta"}, {"wait": 600}, _probe("MENU"),
        {"key": "Escape"}, {"wait": 400},
        {"key": "ArrowRight", "mods": ["meta"]}, {"wait": 600}, _probe("CHORD"),
    ]
    out, got = _run(tmp_path, base, "keys_desk", steps)
    start = got["START"]["edit"]["code"]
    tabbed, untabbed = got["TABBED"]["edit"]["code"], got["UNTABBED"]["edit"]["code"]
    assert got["START"]["edit"]["tab"] == "code", out[-3000:]
    assert tabbed == ["  " + ln for ln in start], (start, tabbed)
    assert untabbed == start, (start, untabbed)
    focus = got["FULL"]["focus"]
    assert got["FULL"]["wins"][focus][2:4] != got["BACK"]["wins"][focus][2:4], out[-3000:]
    assert got["MENU"]["edit"]["sysmenu"], "a Meta tap opened no system menu"
    assert not got["CHORD"]["edit"]["sysmenu"], "Meta+Right opened the system menu"


# -- the mouse ------------------------------------------------------------------

_HASH = ("(function(){var c=document.getElementById('cv');var d=c.getContext('2d')"
         ".getImageData(0,0,c.width,c.height).data;var h=0;for(var i=0;i<d.length;i+=4)"
         "{h=(h*31+d[i]+d[i+1]*7+d[i+2]*13)|0;}return '%s '+JSON.stringify(h);})()")


def test_the_wheel_scrolls_files_list_in_chrome(tmp_path, base):
    """Over Files' list a notch scrolls it and the notch back returns it,
    the pointer resting where it was."""
    at = [700, 330]
    steps = [
        {"note": "Files up", "wait": 6000},
        {"move": at}, {"wait": 800},
        {"js": _HASH % "BEFORE"}, {"shot": "files_before"},
        {"wheel": at + [0, 300]}, {"wait": 800},
        {"js": _HASH % "DOWN"}, {"shot": "files_down"},
        {"wheel": at + [0, -300]}, {"wait": 800},
        {"js": _HASH % "BACK"},
    ]
    out, js = web_e2e.run(tmp_path, "wheel_files", "?desktop=1&cart=moybyte.files.moy&dev=1",
                          steps, base, tmp_path / "chrome_wheel", boot_ms=30000)
    got = _tagged(js)
    assert got["DOWN"] != got["BEFORE"], "the wheel moved nothing:\n" + out[-2000:]
    assert got["BACK"] == got["BEFORE"], "the notch back did not return it"


def _grid(tmp_path, base):
    """Where the Code tab's text lies in this browser: (x of column 0, y of
    the top row, a cell's width, a line's height)."""
    out, got = _run(tmp_path, base, "code_grid", _edit("code") + [_probe("GRID")])
    return got["GRID"]["edit"]["grid"]


def test_the_mouse_selects_and_autoscrolls_the_code_in_chrome(tmp_path, base):
    """A drag selects, a double click takes a word, Shift+click extends; a
    middle click latches an autoscroll that leaves the caret, and a key
    ends it."""
    x0, y0, cell, lh = _grid(tmp_path, base)

    def at(row, col):
        return [x0 + col * cell + cell // 2, y0 + row * lh + lh // 2]

    steps = _edit("code") + [
        _probe("START"),
        {"move": at(0, 0)},
        {"drag": at(0, 0) + at(0, 4) + [4]}, {"wait": 300}, _probe("DRAGGED"),
        {"click": at(1, 2), "settle": 60}, {"click": at(1, 2)}, _probe("WORD"),
        {"click": at(0, 0)}, {"click": at(0, 3), "mods": ["shift"]}, _probe("SHIFTED"),
        {"move": at(4, 10)},
        {"middle": at(4, 10) + at(4, 10)}, {"move": at(4, 10)},
        {"move": [at(4, 10)[0], at(4, 10)[1] + 8 * lh]}, {"wait": 1500},
        _probe("SCROLLED"),
        {"key": "ArrowRight"}, {"wait": 300}, _probe("STOPPED"),
    ]
    out, got = _run(tmp_path, base, "code_mouse", steps)
    start = got["START"]["edit"]["code"]
    assert got["DRAGGED"]["edit"]["selected"] == start[0][:4], out[-3000:]
    word = got["WORD"]["edit"]["selected"]
    assert word and word in start[1], (word, start[1])
    assert got["SHIFTED"]["edit"]["selected"] == start[0][:3]
    s = got["SCROLLED"]["edit"]
    assert s["autoscroll"] and s["top"] > 0, s
    assert s["caret"] == got["SHIFTED"]["edit"]["caret"], "the caret moved"
    assert not got["STOPPED"]["edit"]["autoscroll"]
