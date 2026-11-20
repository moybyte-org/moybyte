"""The conformance fixtures on the C engine (native/moy_uitree, driven by
runtime/uitree/uitree_c.py): the same app file, the same input script, held
to the reference (the Python replay, `uitree_app.Engine`):

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
    import uitree_c
    import uitree_parity as P
    app_py, cart = C.fixture(name)
    mod = C._module(app_py)
    eng = uitree_c.CEngine if engine == "c" else None
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


def test_a_kind_the_engine_lacks_is_refused_by_name():
    import uitree as S
    import uitree_c
    app_py, _ = C.fixture("counter")
    mod = C._module(app_py)

    def build():
        with S.page("P"):
            S.rule()
    eng = uitree_c.CEngine(S.decl_of(S.module_of(mod.build)), build, _Host(), None, None)
    with pytest.raises(uitree_c.EngineGap, match="rule"):
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
    import uitree_c
    import uitree_parity as P
    app_py = tmp_path / "pushed" / "app.py"
    app_py.parent.mkdir()
    app_py.write_text(PUSHED)
    mod = C._module(str(app_py))
    cls = uitree_app.app_layer("pushed_" + engine, "Pushed", mod.build,
                               engine=uitree_c.CEngine if engine == "c" else None)
    seen = {}

    def opened(ws):
        seen["app"] = P.host(ws, cls)
    focus = P.Surface(opened, probe=lambda ws: seen["app"].engine.focus_key)
    fs = P.frames(C.CONFIGS[0], focus, ["k:down", "k:a"])
    assert [f.state for f in fs] == ["add", "more", "more"], [f.state for f in fs]
