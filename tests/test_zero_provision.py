"""What provision.sh puts on a Zero, and what it mints there.

The Zero runs STOCK MicroPython with the shared modules pushed as plain files,
so its module set is a hand-written list in a shell script -- and a list is
exactly the thing that silently falls behind the code it is a list OF. That has
already happened once on this board: `moy_store.mjs` became an asset the worker
statically imports and the hand-list missed it the same day, which is a console
that cannot boot at all.

So the asset half is derived from `moy_webhost.ASSETS` in the script itself,
and the MODULE half is derived here: every module the sync stack imports must
be one the script pushes. That is the whole subject of this file, plus the pin
the script mints -- because since 2026-08-25 a board with no pin is a board
serving its whole store to the network.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ZERO = ROOT / "firmware" / "seeed_xiao_esp32s3_zero"
SCRIPT = (ZERO / "provision.sh").read_text()


def _pushed():
    """The module basenames `provision.sh` copies onto the board."""
    block = SCRIPT[SCRIPT.index('echo "== modules"'):SCRIPT.index('echo "== dirs"')]
    return {Path(m).name for m in re.findall(r'"\$\{\w+\}(/[^"]+\.py)"', block)}


def test_the_zero_carries_the_whole_sync_stack():
    """THE ZERO SUPPORTS ALL FEATURES (owner call 2026-08-25). Before that it
    carried the minimum that boots, which meant `moy_sync.file_kinds()` found no
    store module and answered None -- read as "refuse" everywhere, so the board
    404'd /files.json and the kid's drawings could not travel. Every name here
    is a feature, not a dependency detail."""
    pushed = _pushed()
    for name in ("moy_webserver.py", "moy_webhost.py",   # the transport + host
                 "moy_sync.py", "moy_fs.py",             # the 3.4 RPC
                 "moy_carts.py",                         # #108 files sync
                 "moy_journal.py",                       # the store of record
                 "moy_image.py",                         # moy_carts' own import
                 "web_view_ws.py", "ticks.py",
                 "zero_host.py", "zero_setup.py", "zero_gpio.py", "main.py"):
        assert name in pushed, name


def test_the_pushed_set_is_import_closed():
    """A module imported at module scope by something on the board and missing
    from the push is an ImportError at boot -- on a headless board, over
    serial, at somebody's desk. `blocks` is the one deliberate absence: moy_carts
    imports it LAZILY inside a function only a console calls, and this board has
    no console."""
    pushed = _pushed()
    lazy = {"blocks"}
    for name in sorted(pushed):
        src = _source(name)
        for imported in re.findall(r"^\s*(?:from|import) (\w+)", src, re.M):
            if imported + ".py" in pushed or imported in lazy:
                continue
            # Anything else must be a stdlib/port module, not a repo one.
            assert not _repo_module(imported), \
                "%s imports %s, which nothing pushes" % (name, imported)


def _repo_module(name):
    for d in ("runtime", "device"):
        if (ROOT / d / (name + ".py")).exists():
            return True
    return False


def _source(name):
    for d in (ZERO, ROOT / "runtime", ROOT / "device"):
        p = d / name
        if p.exists():
            return p.read_text()
    raise AssertionError("no source for " + name)


def test_provisioning_mints_a_pin_when_the_board_has_none():
    """A USB-provisioned Zero never went through the AP setup form that mints
    one, so until 2026-08-25 it had none -- and with the pin now gating every
    read, none means the whole store is public. Minted ON THE BOARD so the
    "is there one?" check and the write are the same read of the same
    filesystem, and KEPT when there is one: re-running this script must never
    rotate a pin somebody has already scanned into a phone."""
    assert "== pairing pin" in SCRIPT
    assert "zero.json" in SCRIPT
    assert "ZEROPIN new" in SCRIPT and "ZEROPIN kept" in SCRIPT
    # The kept branch is what makes it idempotent; assert it is guarded on the
    # EXISTING value rather than on the file merely being there (a zero.json
    # written by an older setup carries a name and no pin).
    assert "if not d.get('pin'):" in SCRIPT


def test_provisioning_prints_the_paired_url_and_says_so_when_it_cannot():
    """The pin is the whole pairing gesture: a page that does not carry it sees
    a prompt with nothing to type into it. And a board that ended up without one
    must SAY so -- silence there reads as success."""
    tail = SCRIPT[SCRIPT.index("== reboot into the host"):]
    assert "?pin=${PIN}" in tail
    assert "PAIRED URL" in tail
    assert "WARNING" in tail and "anyone on the network" in tail
