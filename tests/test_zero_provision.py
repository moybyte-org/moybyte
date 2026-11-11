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
  * the board's password, because a Zero has no screen for a pairing code: a
    board with none is one no browser can pair with.
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


def test_provisioning_sets_the_password_as_a_hash_and_keeps_one_already_there():
    """`--password` asks twice, never echoed, and writes the HASH where the
    sign-in reads it (the system folder's `password`, as `passwd` writes it);
    without the flag a password already on the board is left alone."""
    assert "--password) SET_PASSWORD=1" in SCRIPT
    assert 'read -rsp' in SCRIPT
    assert "ssh_service.hashed(" in SCRIPT
    assert "/moy/system/password" in SCRIPT
    body = SCRIPT[SCRIPT.index('if [ "${SET_PASSWORD}" = "1" ]'):SCRIPT.index("== pairing")]
    assert "PW1}" not in body.split("HASH=")[1].split("board_write")[1], \
        "the password itself reached the board"


def test_a_write_the_board_refuses_stops_provisioning():
    """A full store refuses a mkdir and the write under it (2026-11-07: the
    password never landed and the script said done). Every write is checked
    on the board, and a refusal exits non-zero with what the board said."""
    helper = SCRIPT[SCRIPT.index("board_write() {"):SCRIPT.index("board_mkdir() {")]
    assert "grep -qx 'MOYOK'" in helper and "exit 1" in helper
    assert "if e.args[0] != 17: raise" in SCRIPT
    body = SCRIPT[SCRIPT.index('echo "== dirs"'):SCRIPT.index("== pairing")]
    assert "except OSError: pass" not in body
    for what in ('board_write "wifi.json"', 'board_write "the password"',
                 "board_mkdir /moy/system"):
        assert what in body, what


def test_provisioning_says_how_to_pair_and_says_so_when_it_cannot():
    """A board with a password is paired from its address; one without must
    SAY that no browser can pair -- silence there reads as success."""
    tail = SCRIPT[SCRIPT.index("== reboot into the host"):]
    assert "http://${NAME}.local/" in tail
    assert "WARNING" in tail and "no browser can pair" in tail
