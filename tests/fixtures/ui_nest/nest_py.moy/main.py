# A nest opened past the engine's depth (moy_ui_api.h's MOY_UI_API_NEST) is
# an error, as a Python app. nest_lua.moy and tests/fixtures/wasm/app_nest.moy
# make the same calls; tests/test_ui_nest.py reads their answers. Into pmem:
# 0..3 1 where ui.scroll, ui.text_view, ui.dialog and ui.popover raised
# inside two open scroll regions, 4 1 where a scroll region opened after
# them did not, 5 frames.

frames = 0


def _deep(i, verb):
    try:
        verb()
    except RuntimeError as e:
        pmem(i, 1 if "nested too deep" in str(e) else 2)
        return
    pmem(i, 0)


def _draw():
    global frames
    ui.page("Nest")
    ui.scroll("a")
    ui.scroll("b")
    _deep(0, lambda: ui.scroll("c"))
    _deep(1, lambda: ui.text_view("t", ["x"]))
    _deep(2, lambda: ui.dialog("D", ["OK"]))
    _deep(3, lambda: ui.popover("p", 100))
    ui.scroll()
    ui.scroll()
    ui.scroll("d")
    ui.label("after")
    ui.scroll()
    pmem(4, 1)
    frames += 1
    pmem(5, frames)
