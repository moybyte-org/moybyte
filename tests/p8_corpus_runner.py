"""Import ONE p8 cart, boot it, press start, and report what happened.

Run as a child process by `test_p8_corpus.py`, for two reasons that are both
load-bearing:

  * a cart can HANG. `terra` spins in its own world-generation loop, and the
    console's Lua opens no debug library, so nothing inside the process can
    interrupt it. A child with a timeout is the only thing that survives that.
  * a cart can take the interpreter down in ways a pytest process should not
    have to absorb.

`--json` prints one line the parent parses.

    python tests/p8_corpus_runner.py <cart.p8|cart.p8.png> <out_dir> <name>
"""
import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for _p in (_HERE, os.path.join(_ROOT, "tools"), _ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import canvas_probe                                            # noqa: E402
import import_p8                                               # noqa: E402
from runtime import moy_carts                                   # noqa: E402

# Most carts want a button before anything moves, and they do not agree on
# WHICH button -- so press several, spaced out, and give the cart time between.
# A start screen that never gets its button looks exactly like a cart that does
# not run, which is the confusion this whole file exists to prevent.
_PRESSES = ("a", "b", "a", "b", "up", "a")
START = {}
for _i, _b in enumerate(_PRESSES):
    START[30 + _i * 40] = (_b, True)
    START[34 + _i * 40] = (_b, False)

MOVE = dict(START)
MOVE.update({300: ("right", True), 360: ("right", False),
             370: ("left", True), 430: ("left", False),
             440: ("down", True), 470: ("down", False)})

FRAMES = 520
DT = 1.0 / 60


def _hash(cv):
    return hashlib.blake2b(bytes(canvas_probe.buffer_of(cv)),
                           digest_size=6).hexdigest()


def _run(moy_dir, script):
    from ws_helpers import build_ws
    ws = build_ws(Path(tempfile.mkdtemp()))
    cart = moy_carts.load(moy_dir)
    ws.launcher.items.append(cart)
    ws.launcher.sel = len(ws.launcher.items) - 1
    ws.open()
    if ws.player.cart_error:
        return str(ws.player.cart_error), []
    seen = []
    for f in range(FRAMES):
        if f in script:
            ws.input.set_held(*script[f])
        ws.input.begin_frame()
        ws.frame(DT)
        if ws.player.cart_error:
            return str(ws.player.cart_error), seen
        if f % 4 == 0:
            seen.append(_hash(ws.canvas))
    return None, seen


def check(cart_path, out_root, name):
    out = os.path.join(out_root, name + ".moy")
    res = {"name": name}
    try:
        summary = import_p8.import_p8(cart_path, out)
    except Exception as exc:                       # noqa: BLE001 - reported
        res["error"] = "import %s: %s" % (type(exc).__name__, exc)
        return res
    res["unsupported"] = summary.get("unsupported", [])
    res["approximated"] = summary.get("lossy", [])
    err, moved = _run(out, MOVE)
    idle_err, idle = _run(out, START)
    res["error"] = err or idle_err
    res["distinct"] = len(set(moved))
    # `responds` needs two runs of the same cart to compare, which is why this
    # is a runner and not an assertion: input reaching the cart is the
    # difference between the two pixel histories, not a property of one.
    res["responds"] = bool(moved and moved != idle)
    return res


def main(argv):
    if len(argv) < 4:
        print(__doc__)
        return 2
    print(json.dumps(check(argv[1], argv[2], argv[3])))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
