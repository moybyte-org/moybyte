"""The shell's bar laid out by the engine (runtime/shell/bar_engine.py over the page bar_ui.py): for
the themes' bar, on every glass a desk runs and with any count of the apps'
status items, the cells `bar_engine.pack` places are `shell_layout.pack`'s, on
the reference and on the C engine."""

import pytest

import layout_base as LB
import shell_layout as SL

THEMES = (("top", LB.BAR_ROWS, ("menu", "chips", "zone"), (), ("wifi", "battery", "clock"), ()),)
GLASS = ((1024, 600, 40, 2, 1), (1024, 600, 40, 2, 2), (1280, 800, 40, 2, 1), (800, 480, 40, 2, 3),
         (1366, 768, 40, 3, 2), (1024, 600, 36, 2, 1))
WITHOUT = ((), ("wifi",), ("wifi", "battery"), ("chips",), ("wifi", "battery", "clock"), ("menu",))


@pytest.mark.parametrize("engine", ("reference", "c"))
def test_the_engine_places_the_themes_bar_where_pack_does(engine):
    import uitree_app
    import bar_engine
    uitree_app.use_engine(engine)
    bar_engine._PAGE[0] = None
    try:
        assert type(bar_engine._page()).__name__ == ("CEditorPage" if engine == "c" else "Page")
        for w, h, rows, cs, fs in GLASS:
            for items in (0, 1, 3):
                for gone in WITHOUT:
                    of = SL.without(THEMES, gone)
                    for sys_len in (0, 44):
                        a = (w, h, rows, cs, fs, 5 * 8 * fs)
                        want = SL.pack(*a, of=of, sys_len=sys_len, cluster=False, items=items)
                        got = bar_engine.pack(*a, of=of, sys_len=sys_len, cluster=False, items=items)
                        assert got == want, (w, h, cs, fs, items, gone, sys_len)
    finally:
        uitree_app.use_engine("reference")
        bar_engine._PAGE[0] = None


def test_a_layout_the_page_does_not_hold_is_pack_s():
    import bar_engine
    a = (1024, 600, 40, 2, 1, 40)
    assert bar_engine.pack(*a, of=LB.DEFAULT_BARS, cluster=False, items=0) == \
        SL.pack(*a, of=LB.DEFAULT_BARS, cluster=False, items=0)
    assert not bar_engine.placed(LB.DEFAULT_BARS[0]) and bar_engine.placed(THEMES[0])
