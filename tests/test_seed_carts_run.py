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
