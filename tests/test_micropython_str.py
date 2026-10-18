"""Code the boards and the browser run never calls a str method MicroPython
lacks. CPython has them, so every host test passes and the first call on a
board or in the browser is an AttributeError: Files' `kind.capitalize()`
crashed the browser console on open (2026-10-18)."""

import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# str methods CPython has and MicroPython's str does not, unambiguous enough
# to grep for (`title` is left out: the shell names its own `title()` verbs).
MISSING = ("capitalize", "swapcase", "casefold", "zfill", "expandtabs")
CALL = re.compile(r"\.(%s)\(" % "|".join(MISSING))


def test_shipped_code_calls_no_str_method_micropython_lacks():
    hits = []
    for top in ("runtime", "device"):
        for name in sorted(os.listdir(os.path.join(ROOT, top))):
            if not name.endswith(".py"):
                continue
            path = os.path.join(ROOT, top, name)
            with open(path, encoding="utf-8") as f:
                for n, line in enumerate(f, 1):
                    code = line.split("#", 1)[0]
                    if CALL.search(code):
                        hits.append("%s/%s:%d: %s" % (top, name, n, line.strip()))
    assert not hits, "\n".join(hits)
