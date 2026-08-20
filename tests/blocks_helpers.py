"""Shared blocks-test kit (2026-08-18).

Four block suites (test_blocks / test_blocks_v48 / test_blocks_custom /
test_block_editor) each hand-copied this recording cart namespace and the
headless run loop -- and one copy's `btn()`/`btnp()` had drifted to constant
False where the others were settable. The settable superset behaves
identically until a test sets `_btn`, so it is the one body now.
"""


class _FakeAPI(dict):
    """A cart namespace: the injected v0.4 API verbs as recording stubs, so a
    compiled cart can exec + run _init/_update/_draw headlessly with no
    display. Every draw/input/sound verb the catalog can emit is present;
    input is settable per test via `_btn`/`_btnp`/`_touch`."""

    def __init__(self):
        super().__init__()
        self.calls = []
        # screen dims (carts read W/H like the real namespace)
        self["W"] = 320
        self["H"] = 240
        for name in ("cls", "pix", "line", "rect", "rectb", "circ", "circb",
                     "spr", "print", "sfx", "beep", "music"):
            self[name] = self._rec(name)
        from runtime import palette
        self["col"] = palette.color        # faithful name/index -> 0-63 resolution
        self["btn"] = lambda d=None: self._btn.get(d, False)
        self["btnp"] = lambda d=None: self._btnp.get(d, False)
        self["touch"] = lambda: self._touch
        self["rnd"] = lambda n=1.0: 0.0       # deterministic for tests
        self["flr"] = lambda x: int(x // 1)
        self._btn = {}
        self._btnp = {}
        self._touch = None

    def _rec(self, name):
        def fn(*a, **k):
            self.calls.append((name, a, k))
        return fn


def run_cart(src, frames=1, fake=None):
    """Compile-check, exec the cart source, run _init once and _update/_draw
    for `frames`. Returns the namespace (which IS the fake API). Raises on any
    error (the test asserts it runs clean)."""
    code = compile(src, "<cart>", "exec")     # (a) it parses
    fake = fake or _FakeAPI()
    exec(code, fake)                          # module-level defs + var inits
    if fake.get("_init"):
        fake["_init"]()
    for _ in range(frames):
        if fake.get("_update"):
            fake["_update"](1 / 30)
        if fake.get("_draw"):
            fake["_draw"]()
    return fake
