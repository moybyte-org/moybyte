"""Every seed cart survives real frames on a real console.

WHY THIS EXISTS. `Pin Light` shipped in four firmware images and on four boards
before anyone ran it, and it crashed on its first frame: it passed colour NAMES
to `cls`/`rect`/`print`, which take a palette INDEX and mask it, so every frame
raised `unsupported operand type(s) for &: 'str' and 'int'`. Nothing caught it,
because nothing here ran a seed cart at all -- `tests/test_device_seed_parity.py`
compares the embedded roster to `system_carts/` byte for byte, which proves the
right bytes reach a board and says nothing whatever about whether they work.

That gap is worse than it sounds, because these carts are the ONLY thing on a
freshly flashed board. A kid's first tap can be a crash panel, and the compressed
roster now bakes whatever is in `system_carts/` into every image.

So: build one host Workstation over a store holding the whole roster, open each
cart through the real launcher, pump frames through the real driver, and read
`ws.cart_error` -- the same field the on-canvas crash panel reads. Six frames is
enough for the shape of failure this exists to catch (a draw verb that raises
does it on frame one) and cheap enough to run on every commit.

It is NOT a correctness test. A cart that draws the wrong thing passes here; the
pixel goldens are that net. This one only asks whether the cart RUNS.
"""

import os
import shutil

import pytest

import host_app
import moy_carts

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SYSTEM_CARTS = os.path.join(ROOT, "system_carts")

# One frame is where a bad draw verb raises; six covers a cart whose first frame
# is a title card and whose second is the game, and any _init/_update ordering
# between them.
FRAMES = 6


@pytest.fixture(scope="module")
def console(tmp_path_factory):
    """One Workstation over a store holding the whole roster.

    Module-scoped because building it is the expensive part and opening a cart
    is not -- and because a shared console is closer to the thing being modelled
    (a board that boots once and has every cart opened on it) than a fresh one
    per cart would be.
    """
    store = tmp_path_factory.mktemp("carts")
    for name in sorted(os.listdir(SYSTEM_CARTS)):
        if name.endswith(".moy"):
            shutil.copytree(os.path.join(SYSTEM_CARTS, name),
                            os.path.join(str(store), name))
    ws = host_app.build_workstation(str(store))
    driver = host_app.ConsoleDriver(ws)
    ws.launcher.items = moy_carts.scan(ws.carts_root)
    assert ws.launcher.items, "no carts in the store -- this would pass vacuously"
    return ws, driver


def _titles():
    out = []
    for name in sorted(os.listdir(SYSTEM_CARTS)):
        if name.endswith(".moy"):
            out.append(name[:-4])
    return out


def test_the_roster_this_runs_is_the_one_that_ships():
    """The guard against passing vacuously. If this file ever ran against a
    subset -- a filtered scan, a store that failed to populate -- every test
    below would go green while covering less than it claims."""
    on_disk = set(_titles())
    assert len(on_disk) >= 30, on_disk
    import sys
    sys.path.insert(0, os.path.join(ROOT, "tools"))
    import gen_device_carts as gen
    # roster() yields bare folder names (the `.moy` is CART_SUFFIX, added by
    # the CLI, not by this).
    shipped = set(gen.roster("device"))
    assert shipped <= on_disk, sorted(shipped - on_disk)
    assert len(shipped) >= 30, sorted(shipped)


@pytest.mark.parametrize("folder", _titles())
def test_a_seed_cart_survives_its_first_frames(console, folder):
    """Open it the way a kid does and let it draw.

    `ws.cart_error` is the field the on-canvas crash panel reads, so asserting
    on it is asserting on exactly what a person would see.
    """
    ws, driver = console
    target = os.path.join(ws.carts_root, folder + ".moy")
    for i, cart in enumerate(ws.launcher.items):
        if os.path.abspath(cart["path"]) == os.path.abspath(target):
            ws.launcher.sel = i
            break
    else:
        pytest.fail("%s is in system_carts/ but not on the launcher" % folder)

    ws.cart_error = None
    ws.open()
    for _ in range(FRAMES):
        driver.frame(1.0 / 30)
    err = ws.cart_error
    ws.cart_error = None
    assert err is None, "%s crashes on a real console: %s" % (folder, err)


# -- and their Config cards are cards --------------------------------------


def _check(field):
    """`CardsLayer._validate_field` against the real class attributes.

    A bare `_validate_field(None, f)` reaches `self._DISPLAYS` and dies, so the
    shim carries the two tuples the method reads -- taken FROM the class, not
    copied, so a new display kind cannot make this quietly permissive. Building
    a whole CardsLayer would need a Workstation for a pure function.
    """
    from cards_layer import CardsLayer

    class _Shim:
        _DISPLAYS = CardsLayer._DISPLAYS
        _CELL_DISPLAYS = CardsLayer._CELL_DISPLAYS

    return CardsLayer._validate_field(_Shim(), field)


def _edit_fields(folder):
    import json
    man = os.path.join(SYSTEM_CARTS, folder + ".moy", "manifest.json")
    with open(man, encoding="utf-8") as f:
        return json.load(f).get("edit") or []


@pytest.mark.parametrize("folder", _titles())
def test_a_seed_carts_config_cards_are_valid(folder):
    """Run every shipped `edit` schema through the console's OWN validator.

    `pin_light` shipped with `"type": "number"`, which is not a type -- the set
    is `int` and `choice` -- so its one tunable rendered as an inline "!" card
    and could not be stepped. It is the kind of mistake that reads fine to a
    person writing JSON and is invisible until someone opens the Config tab on a
    board, which is the last place to find out.

    Asserting through `_validate_field` rather than against a list of type names
    keeps this honest: the validator also catches min > max, a zero step, empty
    choices and a `display` that does not match its type, and it cannot drift
    from the console because it IS the console.
    """
    fields = _edit_fields(folder)
    if not fields:
        pytest.skip("%s declares no tunables" % folder)
    for field in fields:
        why = _check(field)
        assert why is None, "%s: card %r is %s" % (
            folder, field.get("key", field), why)


def test_the_validator_this_leans_on_still_rejects_things():
    """The check above is only worth its line if the validator has teeth -- a
    `_validate_field` that returned None for everything would make every cart
    pass while proving nothing."""
    assert _check({"key": "pin", "type": "number"}), "an unknown type"
    assert _check({"key": "pin", "type": "int", "min": 9, "max": 1})
    assert _check({"key": "pin", "type": "int", "step": 0})
    assert _check({"type": "int"}), "a missing key"
    assert _check({"key": "k", "type": "choice", "choices": []})
    assert _check({"key": "k", "type": "choice", "choices": ["a"],
                   "display": "gauge"}), "a display that needs type int"
    # ...and the shape pin_light actually ships now must pass.
    assert _check({"key": "pin", "type": "int", "min": 1, "max": 21,
                   "card": "DRIVES PIN {value}"}) is None
