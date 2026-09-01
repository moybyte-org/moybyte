"""What `provision.sh` puts on a Zero, and what it mints there.

WHAT THIS FILE USED TO BE, because the change is the point. Until 2026-08-29
the Zero ran stock MicroPython and its module set WAS this script's `cp` list --
a hand-written list in a shell script, which is exactly the thing that silently
falls behind the code it is a list OF. It had already happened once on this
board: `moy_store.mjs` became an asset the worker statically imports and the
hand-list missed it the same day, which is a console that cannot boot. So this
file derived the module set from the script and asserted the sync stack's whole
import closure against it.

That job is gone, and it did not go by deletion. The board has a frozen image
now, `board.toml` is the declaration of what it runs, and
`tests/test_staging_closure.py` derives the Zero's frozen set from that file and
asserts every import of every staged module resolves on this target -- the same
net all three console boards ride, doing strictly more than the old grep could
(it reads real import ladders, not a regex over a `cp`). The first test below is
the handover being CHECKED rather than assumed: an untaken path is this class of
change's most likely failure mode.

What is left here is what is genuinely this script's:

  * it must not grow a hand-written module list again -- the push it still
    offers is a DEV LOOP, and its file list comes from board.toml;
  * the pushed copies SHADOW the image (MicroPython searches / before .frozen),
    so the push is opt-in and there is a way to undo it;
  * the pin, because since 2026-08-25 a board with no pin is a board serving its
    whole store to the network.
"""

import re
from pathlib import Path

from tools import board_config

ROOT = Path(__file__).resolve().parent.parent
ZERO = ROOT / "firmware" / "seeed_xiao_esp32s3_zero"
SCRIPT = (ZERO / "provision.sh").read_text()


def test_the_zeros_module_set_is_on_the_staging_closure_net():
    """THE HANDOVER, asserted rather than assumed.

    This file's old import-closure test is deleted, and deleting a net is only
    safe if the replacement is actually taken. So: the Zero must be a target of
    `test_staging_closure.py`, that target's frozen set must be derived from
    `board.toml`, and it must actually contain the sync stack. A rename, a typo
    in BOARD_DIR, or a board quietly dropped from TARGETS all land here.
    """
    closure = (ROOT / "tests" / "test_staging_closure.py").read_text()
    assert '"zero"' in closure and "seeed_xiao_esp32s3_zero" in closure, (
        "the Zero is not a target of the staging-closure suite -- which is the "
        "net this file's import-closure test was retired in favour of")
    assert re.search(r"TARGETS\s*=\s*\([^)]*\"zero\"", closure, re.S), (
        "the Zero is in the closure suite's tables but not in TARGETS, so "
        "nothing parametrizes over it")
    staged = set(board_config.staged_modules(ZERO, ROOT))
    for name in ("moy_sync.py", "moy_carts.py", "moy_journal.py",
                 "moy_webhost.py", "moy_ota.py"):
        assert name in staged, "the Zero's declaration lost %s" % name


def test_provisioning_never_regrows_a_hand_written_module_list():
    """The push survives as a dev loop; the LIST does not.

    `board.toml` is the module set of record now, so the script derives its
    file list from `board_config.staged_modules` -- the same call the build
    stages from and the same call the closure suite reads. A `cp` naming
    `${REPO}/runtime/...` or `${REPO}/device/...` here would be a second
    statement of the module set, which is the state this board just left.
    """
    assert "board_config" in SCRIPT and "staged_modules" in SCRIPT, (
        "provision.sh no longer derives its push list from board.toml")
    stale = [line for line in SCRIPT.splitlines()
             if re.search(r'\$\{REPO\}/(runtime|device)/\S+\.py', line)]
    assert not stale, (
        "provision.sh hand-names shared modules again: %s" % stale)


def test_the_module_push_is_opt_in_and_undoable():
    """Because a pushed copy WINS, forever, and says nothing.

    MicroPython searches the filesystem root before `.frozen`, so a `.py` left
    at `/` by a development session outranks the image's own copy on every boot
    after it -- while every diagnostic still points at the firmware. That is the
    same trade the web bundle makes one level up (storage wins, so the image is
    the guarantee and not the ceiling), and it needs the same three things: the
    push is a flag rather than the default, there is a way to take it back, and
    the board says out loud when it is happening.
    """
    assert "--modules" in SCRIPT and "PUSH_MODULES=0" in SCRIPT, (
        "the module push is no longer opt-in")
    assert "--clean" in SCRIPT and "os.remove" in SCRIPT, (
        "there is no way to remove pushed copies that shadow the image")
    host = (ZERO / "modules" / "zero_host.py").read_text()
    assert "SHADOWING the image" in host, (
        "zero_host no longer reports pushed copies shadowing the image")


def test_the_browser_console_rides_this_boards_image():
    """The console this board serves comes out of its own firmware and nowhere
    else -- there is no copy on storage to push, and provisioning must not
    grow one back."""
    assert "moy_web" in board_config.native_modules(ZERO, ROOT), (
        "the Zero stopped baking the browser console into its image")
    assert "/moy/web" not in SCRIPT, "provisioning re-grew a storage bundle"


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
