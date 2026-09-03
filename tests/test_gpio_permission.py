"""Physical pins are gated by CAPABILITY *and* CONSENT (#9).

`pin_write`/`pin_read` are the third capability service beside `wifi` (#38) and
`net` (#65), and until 2026-08-30 they were the odd one out: the browser tier
injected the backend itself, the moment the host serving the page answered the
pin probe, for EVERY cart. That is the capability half of the question -- does
this console have pins -- with the consent half missing, while `cart_api`'s own
comment claimed pins were gated "the same way and for the same reason as wifi
and net", which have both halves.

It stopped being theoretical when a dropped `.p8` could land in a board's own
store: a cart nobody wrote could move a pin nobody declared. The pin ALLOWLIST
bounds which pins may move, never which carts may move them.

So the backend is `ws.gpio` -- one seam, gated once in `player.start` beside its
two siblings -- and these tests pin both halves, because either alone is a bug:
a cart that asks and gets nothing is broken, and a cart that never asked and
gets the pins is the thing this closes.
"""

import json
from pathlib import Path

import pytest


class _FakeGpio:
    """Stands in for whichever backend the tier supplied. Two exist by design:
    the browser's queue-and-coalesce `gpio_link.GpioLink` over the wire, and a
    direct driver on a board with pins of its own. The seam is the same, which
    is the whole point of gating it here rather than in a tier."""

    def __init__(self):
        self.writes = []

    def write(self, n, v):
        self.writes.append((int(n), int(v)))
        return True

    def read(self, n):
        return None


def _cart(carts_dir, title, perms):
    from runtime import moy_carts
    cart = moy_carts.create(title, carts_dir, type="game", edit=[],
                            src="def _update(dt):\n    pass\ndef _draw():\n    cls(0)\n")
    man_path = Path(cart["path"]) / "manifest.json"
    man = json.loads(man_path.read_text())
    man["permissions"] = perms
    man_path.write_text(json.dumps(man))
    return moy_carts.scan(carts_dir)


def _run(ws, title):
    ws.launcher.sel = next(i for i, it in enumerate(ws.launcher.items)
                           if it["title"] == title)
    ws.open()


@pytest.mark.parametrize("perms,named", [
    (["graphics", "input", "pins"], True),
    (["graphics", "input"], False),
    ([], False),
])
def test_the_pin_verbs_need_the_pins_permission(tmp_path, perms, named):
    """A console WITH pins, and two carts that differ only in what they asked
    for. The declaring one gets the names; the others have never heard of them.

    `named` is asserted both ways round on purpose -- a gate that refuses
    everything passes a one-sided test, and would look exactly like this one if
    it only checked the negative."""
    from runtime import host_app
    carts_dir = str(tmp_path / "carts")
    ws = host_app.build_workstation(carts_dir)
    ws.gpio = _FakeGpio()                     # the console HAS pins
    ws.launcher.set_items(_cart(carts_dir, "Pinny", perms))
    _run(ws, "Pinny")
    assert ("pin_write" in ws.ns) is named, sorted(ws.ns)[:0] or perms
    assert ("pin_read" in ws.ns) is named


def test_no_pins_on_the_console_means_no_names_however_loudly_a_cart_asks(tmp_path):
    """The capability half, still enforced. A cart may declare "pins" all it
    likes; on a console whose host has none (moybyte.com, a T-Deck, a page
    opened from a file) `ws.gpio` is None and the verbs are ABSENT rather than
    stubbed -- a `pin_write` that quietly does nothing is the worst answer a kid
    can get, because the cart looks right and the light never moves."""
    from runtime import host_app
    carts_dir = str(tmp_path / "carts")
    ws = host_app.build_workstation(carts_dir)
    assert ws.gpio is None                    # the default everywhere
    ws.launcher.set_items(_cart(carts_dir, "Hopeful", ["graphics", "pins"]))
    _run(ws, "Hopeful")
    assert "pin_write" not in ws.ns and "pin_read" not in ws.ns


def test_a_declaring_cart_actually_reaches_the_backend(tmp_path):
    """The gate is not just a name check: the verb the cart gets must be the
    backend the tier supplied, or this would pass over a stub."""
    from runtime import host_app
    carts_dir = str(tmp_path / "carts")
    ws = host_app.build_workstation(carts_dir)
    gpio = _FakeGpio()
    ws.gpio = gpio
    ws.launcher.set_items(_cart(carts_dir, "Driver", ["graphics", "pins"]))
    _run(ws, "Driver")
    ws.ns["pin_write"](21, 0)
    assert gpio.writes == [(21, 0)]
