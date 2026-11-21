"""Conformance fixtures: one small app written once in Python and once in Lua
(tests/uitree_conformance/NAME/), driven by one input script
(NAME/script.json) on one golden configuration per size (uitree_conf.CONFIGS).

What each fixture holds the two layers to:

- the same events: every action that ran and every state write a handler or
  a control made, with its values (`test_the_same_events`);
- the same tree: kinds and nesting of the built pages (`test_the_same_tree`);
- the same pixels in the page's content, the bar and the hints band left out
  (`test_the_same_content`), and on the whole glass (`test_the_same_glass`);
- the reference's trace and dump verbatim (`test_lua_trace_is_the_references`,
  `test_lua_dump_is_the_references`): the reference is the Python layer (spec
  §22), its output kept in NAME/trace.txt and NAME/dump.txt.

A check that does not hold today is in KNOWN with the spec's ruling; it runs
as a strict xfail, so it fails the day it holds and its line must go.
"""
import functools
import json
import os
import re

import pytest

import uitree_conf as C

NAMES = ("counter", "list", "pages", "gates", "kinds", "lists")
# the fixtures whose Lua half only the C engine draws (level 2's kinds): on the
# reference engine the Lua layer refuses them (E00), so the checks that run it skip
C_ONLY = ("kinds", "lists")
ON_REFERENCE = os.environ.get("MOYBYTE_UI_ENGINE") == "reference"
LUA_CHECKS = ("events", "content", "glass", "trace")
SMALL, DESK = C.CONFIGS

# the rows of the glass outside the page's content: the bar, the hints band
CHROME = {SMALL: ((0, 28), (220, 240))}

# (check, fixture, layer or config): why it does not hold, and who the spec sides with
LUA_SCALE = ("the Lua half is an app cart, and an app cart's desk window (every tier's, tests/test_app_layout.py) "
             "is the player's window, its canvas at font scale 1 (ui_face reads the canvas's) and its content "
             "rect 22 px narrower, where the Python half is a system app's window at the desk's scale 2; the "
             "spec's sizes scale with the text scale (§15): the spec sides with Python; the console's app "
             "window, not the engine (open)")
BAR_AND_BAND = ("the bar's title (the Python app's is empty, the Lua cart's is its manifest title) and "
                "the hints band (Python draws the engine's control hints, Lua none for a toggle or a "
                "link): F-38 leaves the control hints open; the bar's title is the app declaration's (§19.1)")
# The trace lines the two layers still spell apart, each an open item or a
# Python gap: the Lua trace is held to the reference with these taken out.
TRACE_OPEN = (
    # §24.1's `build` names what was built and counts its builds: Python names the page
    # function and counts builds of the app, Lua names the page and counts its own (open);
    # `open` and a hook's `action` are host py+lua, and Python writes neither
    (re.compile(r"^(build|open) "), None),
    (re.compile(r"^action opened from "), None),
    # §24.1's engine write is `(in engine: ...)`; Python writes `(focus)`
    (re.compile(r" \(focus\)$"), " (in engine: focus)"),
    # a control's write: `(from toggle ...)` or `(in toggle ...)`, not spelled (open)
    (re.compile(r" \(from (toggle|slider|field) "), r" (in \1 "),
    # a row_of no row has: Python writes '', Lua (empty) (§24.1 has `(empty)` for a record; open)
    (re.compile(r"^(state \S+ )\(empty\)( -> )"), r"\1''\2"),
)


def trace_open(lines):
    out = []
    for line in lines:
        for pat, sub in TRACE_OPEN:
            if pat.search(line):
                line = None if sub is None else pat.sub(sub, line)
                if line is None:
                    break
        if line is not None:
            out.append(line)
    return out
LUA_DUMP = ("the dump's grammar is open (F-30); Lua keys nodes by kind (`info`), Python by kind and "
            "number (`info#0`, §7); Lua prints the head and the props' sources, Python each bound "
            "prop's value now (§24.2's example is Python's)")
KNOWN = {
    ("dump", "counter", None): LUA_DUMP, ("dump", "list", None): LUA_DUMP,
    ("dump", "pages", None): LUA_DUMP, ("dump", "gates", None): LUA_DUMP, ("dump", "kinds", None): LUA_DUMP,
    ("dump", "lists", None): LUA_DUMP,
}
for _n in NAMES:
    KNOWN[("content", _n, DESK)] = LUA_SCALE
    KNOWN[("glass", _n, SMALL)] = BAR_AND_BAND
    KNOWN[("glass", _n, DESK)] = LUA_SCALE


def _params(check, names=NAMES, configs=(None,)):
    out = []
    for n in names:
        for c in configs:
            why = KNOWN.get((check, n, c))
            marks = [pytest.mark.xfail(strict=True, reason=why)] if why else []
            if ON_REFERENCE and n in C_ONLY and check in LUA_CHECKS:
                marks = [pytest.mark.skip(reason="the reference back end draws no level-2 kind (E00)")]
            out.append(pytest.param(n, c, marks=marks, id="%s-%s" % (n, c) if c else n))
    return out


def _script(name):
    with open(os.path.join(C.FIX, name, "script.json")) as f:
        return json.load(f)


@functools.lru_cache(maxsize=None)
def _run(layer, name, config):
    fn = C.run_python if layer == "python" else C.run_lua
    return fn(name, config, _script(name))


EVENT = re.compile(r"^(action|state|page|push|pop) (\S+) ?(.*)$")


def events(trace):
    """What ran: actions by name, and the writes of handlers and controls with
    their values (a bool's spelling and the cause dropped)."""
    out = []
    for line in trace:
        m = EVENT.match(line)
        if not m:
            continue
        src, name, detail = m.groups()
        if src == "action":
            if name != "opened":
                out.append("action " + name)
        elif src == "state":
            cause = re.search(r"\((in|from) (\w+)", detail)
            if cause is None or cause.group(2) == "engine":
                continue
            value = re.sub(r"\s*\((in|from) .*$", "", detail)
            value = re.sub(r"\b(True|False)\b", lambda b: b.group(1).lower(), value)
            value = re.sub(r"\s*moved \d+$", "", value)
            out.append("state %s %s" % (name, value))
        elif src in ("push", "pop") or (src == "page" and name in ("push", "back")):
            out.append("pop" if name in ("back", "pop") or src == "pop" else "push")
    return out


def shape(dump):
    """The tree's kinds and nesting: (depth, kind), nothing under a list."""
    out, cut = [], None
    for line in dump.splitlines():
        depth = (len(line) - len(line.lstrip(" "))) // 2
        if cut is not None and depth > cut:
            continue
        cut = None
        kind = line.split()[0]
        if kind.startswith("["):
            continue
        out.append("%s%s" % ("  " * depth, kind))
        if kind in ("rows", "table", "cards"):
            cut = depth
    return out


@pytest.mark.parametrize("name,config", _params("events"))
def test_the_same_events(name, config):
    py, _ = _run("python", name, SMALL)
    lua, _ = _run("lua", name, SMALL)
    assert events(lua) == events(py)


@pytest.mark.parametrize("name,config", _params("tree"))
def test_the_same_tree(name, config):
    perr, pdump = C.dump_python(name)
    lerr, ldump = C.dump_lua(name)
    assert perr == [] and lerr == []
    assert shape(ldump) == shape(pdump)


def _frames_equal(name, config, rows_out=()):
    _, fp = _run("python", name, config)
    _, fl = _run("lua", name, config)
    assert len(fp) == len(fl)
    bad = []
    for a, b in zip(fp, fl):
        bpp = len(a.buf) // (a.w * a.h)
        stride = a.w * bpp
        for y in range(a.h):
            if any(y0 <= y < y1 for y0, y1 in rows_out):
                continue
            if a.buf[y * stride:(y + 1) * stride] != b.buf[y * stride:(y + 1) * stride]:
                bad.append((a.step, y))
                break
    assert not bad, "frames differ (step, first row): %r; see `.venv/bin/python tests/uitree_conf.py %s %s`" % (
        bad, name, config)


@pytest.mark.parametrize("name,config", _params("content", configs=C.CONFIGS))
def test_the_same_content(name, config):
    _frames_equal(name, config, CHROME.get(config, ()))


@pytest.mark.parametrize("name,config", _params("glass", configs=C.CONFIGS))
def test_the_same_glass(name, config):
    _frames_equal(name, config)


def _expected(name, what):
    with open(os.path.join(C.FIX, name, what), encoding="utf-8") as f:
        return f.read().splitlines()


@pytest.mark.parametrize("name,config", _params("reference"))
def test_the_reference_matches_its_records(name, config):
    """The Python layer's trace and dump are the records the Lua half is held to."""
    py, _ = _run("python", name, SMALL)
    assert py == _expected(name, "trace.txt")
    assert C.dump_python(name)[1].splitlines() == _expected(name, "dump.txt")


@pytest.mark.parametrize("name,config", _params("trace"))
def test_lua_trace_is_the_references(name, config):
    lua, _ = _run("lua", name, SMALL)
    assert trace_open(lua) == trace_open(_expected(name, "trace.txt"))


@pytest.mark.parametrize("name,config", _params("trace"))
def test_lua_on_the_c_engine_writes_the_references_trace(name, config):
    """On the C engine the Lua layer's trace is the engine's and the
    reference's, line for line: only the layer's own build and open lines and
    its opened hook's action line (host py+lua, which Python writes none of)
    are left out."""
    import uitree_app
    if uitree_app.engine_in_use() != "c":
        pytest.skip("the Lua layer runs on the reference here")
    own = re.compile(r"^(build|open) |^action opened from ")
    lua, _ = _run("lua", name, SMALL)
    assert [l for l in lua if not own.match(l)] == [l for l in _expected(name, "trace.txt") if not own.match(l)]


@pytest.mark.parametrize("name,config", _params("dump"))
def test_lua_dump_is_the_references(name, config):
    assert C.dump_lua(name)[1].splitlines() == _expected(name, "dump.txt")


def test_every_fixture_has_both_halves_and_a_script():
    for n in NAMES:
        app_py, cart = C.fixture(n)
        for p in (app_py, os.path.join(cart, "main.lua"), os.path.join(cart, "manifest.json"),
                  os.path.join(C.FIX, n, "script.json"), os.path.join(C.FIX, n, "trace.txt"),
                  os.path.join(C.FIX, n, "dump.txt")):
            assert os.path.exists(p), p
    assert sorted(NAMES) == sorted(d for d in os.listdir(C.FIX) if os.path.isdir(os.path.join(C.FIX, d)))
