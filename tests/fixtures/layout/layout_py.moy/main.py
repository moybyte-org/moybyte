# The responsive fixture app (docs/app_api_v1.md section 3) as a Python app:
# its page names how many times `_layout` was called and the arguments of
# the last call. layout_lua.moy, layout_wasm.moy, layout_wasi.moy and
# layout_elf.moy are the same app on the other tiers, and
# tests/test_app_layout.py holds them to the same calls and pixels. Into
# pmem 0: the calls `_layout` had heard when `_init` ran.

calls = 0
last = "none"


def _layout(w, h, fs):
    global calls, last
    calls += 1
    last = "%d %d %d" % (w, h, fs)


def _init():
    pmem(0, calls)


def _draw():
    ui.page("Layout")
    ui.label("calls %d" % calls)
    ui.label(last)
