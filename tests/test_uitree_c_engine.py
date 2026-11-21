"""The conformance fixtures on the C engine (native/moy_uitree, driven by
runtime/uitree/uitree_c.py): the same app file, the same input script, held
to the reference (the Python replay, `uitree_ref.Engine`):

- the reference's trace verbatim (NAME/trace.txt), and the reference run's;
- the same pixels on the whole glass, every frame, at both sizes;
- the tree as the engine's store holds it, printed as the reference's dump
  (NAME/dump.txt).
"""
import functools
import json
import os

import pytest

import uitree_conf as C
import uitree_reference as R

NAMES = ("counter", "list", "pages", "gates")


def _script(name):
    with open(os.path.join(C.FIX, name, "script.json")) as f:
        return json.load(f)


def _expected(name, what):
    with open(os.path.join(C.FIX, name, what), encoding="utf-8") as f:
        return f.read().splitlines()


@functools.lru_cache(maxsize=None)
def _run(engine, name, config):
    import uitree_app
    import uitree_ref
    import uitree_c
    import uitree_parity as P
    app_py, cart = C.fixture(name)
    mod = C._module(app_py)
    eng = uitree_c.CEngine if engine == "c" else uitree_ref.Engine
    cls = uitree_app.app_layer("conf_" + name, C._title(cart), mod.build, engine=eng)
    seen = {}

    def opened(ws):
        seen["app"] = P.host(ws, cls)
    frames = P.frames(config, opened, _script(name))
    return C.norm_trace(seen["app"].engine.trace), frames, type(seen["app"].engine).__name__


@pytest.mark.parametrize("name", NAMES)
def test_the_c_trace_is_the_references(name):
    trace, _, kind = _run("c", name, C.CONFIGS[0])
    assert kind == "CEngine"
    assert trace == _expected(name, "trace.txt")


@pytest.mark.parametrize("config", C.CONFIGS)
@pytest.mark.parametrize("name", NAMES)
def test_the_c_glass_is_the_references(name, config):
    tc, fc, _ = _run("c", name, config)
    tp, fp, _ = _run("python", name, config)
    R.held(__file__, "glass/%s/%s" % (name, config), [tp, fp])
    assert tc == tp
    assert len(fc) == len(fp)
    bad = [(a.step, C.differing(a, b)) for a, b in zip(fp, fc) if a.buf != b.buf]
    assert not bad, "frames differ (step, (box, pixels)): %r" % bad


class _Host:
    def reset(self):
        pass


@pytest.mark.parametrize("name", NAMES)
def test_the_c_store_dumps_the_references_tree(name):
    import uitree as S
    import uitree_c
    app_py, _ = C.fixture(name)
    mod = C._module(app_py)
    eng = uitree_c.CEngine(S.decl_of(S.module_of(mod.build)), mod.build, _Host(), None, None)
    pages = [mod.build] + [getattr(mod, n) for n in ("details",) if hasattr(mod, n)]
    got = []
    for p in pages:
        eng.pages = [p]
        eng.root = None
        eng._ready()
        got += eng.dump().splitlines()
    assert got == _expected(name, "dump.txt")


def test_a_form_the_engine_lacks_is_refused_by_name():
    """Every kind of the schema is the C engine's; a placed kind outside a
    placed page is a form it does not draw, refused by name."""
    import uitree as S
    import uitree_c
    app_py, _ = C.fixture("counter")
    mod = C._module(app_py)

    def build():
        with S.page("P"):
            with S.row("r"):
                pass
    eng = uitree_c.CEngine(S.decl_of(S.module_of(mod.build)), build, _Host(), None, None)
    with pytest.raises(uitree_c.EngineGap, match="row node outside a placed page"):
        eng._ready()


PUSHED = '''
import uitree as ui

s = ui.state(n=0)


def more():
    ui.push(details)


def add():
    s.n += 1


ui.action("more", "Details", more)
ui.action("add", "Add", add)


def build():
    with ui.page("First"):
        with ui.group("A"):
            ui.link("add", focus="n=0")
            ui.link("more")


def details():
    with ui.page("Details"):
        with ui.group("B"):
            ui.info("Count", "{n}")
            ui.link("add")
            ui.link("more", focus="n=0")
'''


@pytest.mark.parametrize("engine", ("python", "c"))
def test_a_pushed_page_opens_on_its_own_focus_node(engine, tmp_path):
    """Spec §9: a fresh page's first frame lands focus on its focus= node,
    the pushed page's own, whatever the page under it drew."""
    import uitree_app
    import uitree_ref
    import uitree_c
    import uitree_parity as P
    app_py = tmp_path / "pushed" / "app.py"
    app_py.parent.mkdir()
    app_py.write_text(PUSHED)
    mod = C._module(str(app_py))
    cls = uitree_app.app_layer("pushed_" + engine, "Pushed", mod.build,
                               engine=uitree_c.CEngine if engine == "c" else uitree_ref.Engine)
    seen = {}

    def opened(ws):
        seen["app"] = P.host(ws, cls)
    focus = P.Surface(opened, probe=lambda ws: seen["app"].engine.focus_key)
    fs = P.frames(C.CONFIGS[0], focus, ["k:down", "k:a"])
    assert [f.state for f in fs] == ["add", "more", "more"], [f.state for f in fs]


def test_still_answers_a_frame_that_would_draw_what_the_last_drew(tmp_path, monkeypatch):
    """`uit_still`, asked inside each frame the host draws before the page's
    walk: False on the first frame, on a frame that took a key and on the
    frame after an action moved state; True on a frame nothing reached."""
    import uitree_app
    import uitree_c
    import uitree_parity as P
    app_py = tmp_path / "still" / "app.py"
    app_py.parent.mkdir()
    app_py.write_text(PUSHED)
    mod = C._module(str(app_py))
    cls = uitree_app.app_layer("still_c", "Still", mod.build, engine=uitree_c.CEngine)
    seen, said, step = {}, [], ["(open)"]
    paint = uitree_c.CEngine.paint

    def watched(self, dt, cv, th, content, desk):
        said.append((step[0], self.still(content, desk) if self._x is not None else None))
        paint(self, dt, cv, th, content, desk)
        step[0] = "after " + step[0].split(" ")[-1]
    monkeypatch.setattr(uitree_c.CEngine, "paint", watched)

    def opened(ws):
        seen["app"] = P.host(ws, cls)

    def probe(ws):
        return ""
    script = ["f:3", "k:down", "f:3", "k:a", "f:3"]
    orig = P._step

    def stepped(ws, driver, s):
        step[0] = s
        orig(ws, driver, s)
    monkeypatch.setattr(P, "_step", stepped)
    P.frames(C.CONFIGS[0], P.Surface(opened, probe=probe), script)
    print(said)
    assert said[0][1] in (None, False)
    by = {}
    for k, v in said:
        by.setdefault(k, []).append(v)
    assert by["k:down"][0] is False and by["k:a"][0] is False, said
    assert by["after k:a"][0] is False, said      # the action's commit moved n
    assert True in by["f:3"], said


LONG = '''
import uitree as ui

s = ui.state(items=ui.model(key="k", k="", name="", size=""), cur=ui.row_of("items"), got="")


def opened():
    s.items = [{"k": "r%d" % i, "name": "Item %d" % i, "size": "%d KB" % (i * 7 % 97)} for i in range(@N@)]


def go(row):
    s.got = row.k


ui.hook("opened", opened)
ui.action("go", "Open", go, takes="row")


def build():
    with ui.page("List"):
        ui.info("Opened", "{got}")
        with ui.group("Items"):
            with ui.rows("items", cur="cur"):
                ui.link("go", "{.name}")
'''


def _long(tmp_path, engine, n, script, config=None):
    import uitree_app
    import uitree_ref
    import uitree_c
    import uitree_parity as P
    app_py = tmp_path / ("long_%s_%d" % (engine, n)) / "app.py"
    app_py.parent.mkdir()
    app_py.write_text(LONG.replace("@N@", str(n)))
    mod = C._module(str(app_py))
    cls = uitree_app.app_layer("long_%s_%d" % (engine, n), "Long", mod.build,
                               engine=uitree_c.CEngine if engine == "c" else uitree_ref.Engine)
    seen = {}

    def opened(ws):
        seen["app"] = P.host(ws, cls)

    def probe(ws):
        e = seen["app"].engine
        return "%s %s" % (e.focus_key, e.s._v["got"])
    return P.frames(config or C.CONFIGS[0], P.Surface(opened, probe=probe), script)


@pytest.mark.parametrize("n", (50, 120))
def test_a_long_list_walks_past_its_fortieth_row_alike_on_both_engines(tmp_path, n):
    """A rows list of more than the toolkit's 40 stops a frame lays out its
    rows in view (uitree_ref.VROWS_MIN; native/moy_uitree's draw_vrows):
    the keys walk focus past the 40th row and open it, a swipe scrolls it
    back, and both engines draw every frame alike."""
    import uitree_parity as P
    script = ["k:down"] * 46 + ["k:a", "swipe:200", "f:20", "k:down", "swipe:-300", "f:20", "k:up"]
    ref = _long(tmp_path, "python", n, script)
    R.held(__file__, "long/%d" % n, ref)
    c = _long(tmp_path, "c", n, script)
    assert ref[46].state.split()[0] == "r46/go", ref[46].state
    assert ref[47].state.split() == ["r46/go", "r46"], ref[47].state
    assert [f.state for f in c] == [f.state for f in ref]
    P.compare(ref, c, tmp_path, "long%d" % n)
