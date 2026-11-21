"""What the toolkit's verbs are asked to say while a block runs, on either
engine: both reach the toolkit's C verbs, and `moy_ui_api_said` taps them
there (the reference engine through `ui`, the C engine directly).

    with said() as got:
        drv.frame(dt)
    assert ("info", "Name", "Moy") in got

Each entry: ("label", text, face or None), ("info", label, value),
("link", label, value), ("group", caption), ("button", label),
("hints", (verb, text, ...)), ("icon", name, whether the theme's set draws
it), ("slider", label, lo, hi, steps), ("progress", label, value, max).
"""

import contextlib

_ROLES = ("ui", "title", "mono", "caption", "key", "op", "result")
_VERBS = {1: "label", 2: "info", 3: "link", 4: "group", 5: "button", 6: "hints", 7: "icon", 8: "slider",
          9: "progress"}


@contextlib.contextmanager
def said():
    from runtime import ui
    got = []

    def tap(verb, a, b, role):
        v = _VERBS[verb]
        if v == "hints":
            got.append((v, tuple(a.split("\0")) if a else ()))
        elif v == "label":
            got.append((v, a, _ROLES[role] if 0 <= role < len(_ROLES) else None))
        elif v == "icon":
            got.append((v, a, bool(role)))
        elif v == "slider":
            lo, hi, steps = b.split()
            got.append((v, a, int(lo), int(hi), steps == "1"))
        elif v == "progress":
            value, hi = b.split()
            got.append((v, a, int(value), int(hi)))
        elif v in ("info", "link"):
            got.append((v, a, b))
        else:
            got.append((v, a))
    ui._C.said_tap(tap)
    try:
        yield got
    finally:
        ui._C.said_tap(None)
