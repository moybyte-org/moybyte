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
