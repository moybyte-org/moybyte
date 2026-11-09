"""The browser's JavaScript does I/O; the console's decisions are the VM's
(owner, 2026-11-08; docs/moycore_direction.md section 3, the audit).

A rule copied into JavaScript is a second implementation that drifts: Notes'
NameError came from the browser's own re-seed rule disagreeing with the
board's. This pins every top-level name in `moy_store.mjs` to one of two
lists, so a new one fails here until it is filed:

  * `MIRRORS` -- the JavaScript copies of a Python or C rule that remain, each
    with its twin and the test that holds the two together. The list only
    SHRINKS: a new mirror is a decision for the owner, and the way off it is
    to ask the VM (or export the C into the wasm) instead.
  * `IO` -- the OPFS and browser plumbing, which decides nothing the VM has a
    rule for. A name belongs here only when that is true.

And worker.js may call only those names on the store.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "firmware" / "web_runner"

MIRRORS = {
    # what never crosses the wire (test_web_store: the two skip predicates)
    "SKIP_DIRS": "moy_sync.SKIP_DIRS / moy_fs.h moy_store_skip",
    "SKIP_FILES": "moy_sync.SKIP_FILES",
    "SKIP_SUFFIXES": "moy_sync.SKIP_SUFFIXES",
    "skipIn": "moy_sync._skip",
    "skipName": "moy_sync._skip",
    # what never reaches the local store (test_web_store)
    "SITE_SKIP_DIRS": "moy_sync.SITE_SKIP_DIRS",
    "SITE_SKIP_FILES": "moy_sync.SITE_SKIP_FILES",
    "skipLocal": "moy_sync.Root.watch_skip(site=True)",
    # which files are bytes (test_web_store: the binary rule)
    "BINARY_FILES": "moy_sync.BINARY_FILES",
    "BINARY_SUFFIXES": "moy_sync.BINARY_SUFFIXES",
    "isBinary": "moy_sync.is_binary",
    # the root registry (test_web_store: the sync root registry)
    "ROOTS": "moy_sync.SYNC_ROOTS",
    "rootById": "moy_sync.SYNC_ROOTS",
    # the receiving path rule (native/moy_net/moy_sync_apply.c)
    "safeSegments": "moy_sync_apply.c's path rule",
    "localParts": "moy_sync.Root's per-root shape",
    # names beside the carts folder (test_web_store: Get Carts layout)
    "RECORD_NAME": "cart_index.RECORD_NAME",
    "STAGE_DIR": "moy_pack's install/ sibling",
    "WRITTEN_ROOT": "cart_files' apps/ sibling",
    "MUSIC_DIR": "media_link.MUSIC",
    "isSong": "media_link's song listing",
    # first visit of a root other than carts: the store adopts the bundle
    "isEmpty": "moy_seed.seed_folders' `first` (carts)",
}

IO = (
    "fromBase64", "toBase64", "fileData", "probeMode", "requestPersistence",
    "mbytes", "storageNote", "enc", "strict", "CHANNEL_DIR_PREFIX", "channelNav",
    "openStore", "dirFor", "namedAt", "writeText", "writeIn", "removeAt",
    "applyOps", "applyOne", "readAll", "asValue", "walk", "songs", "songFile",
    "keepSong", "removeFile", "seed", "plainName", "commitInstall", "readMarker",
    "rollForward", "recoverInstalls", "writeRecord", "readRecord", "PART",
    "rollFile", "hasFile", "commitFile", "dropFile", "settleFiles", "openWritten",
    "writtenName", "commitWritten", "dropWritten", "readWritten",
)

# The rules that moved into the VM; none may come back under its old name.
MOVED = (
    "systemSeeds", "cartVersion", "preserved", "RESEED_PRESERVE",
    "crc32", "CRC_TABLE", "zipStore", "inflateRaw", "unzip", "zipTopDir",
    "cartBase", "uniqueCartDir", "themeOf", "themeName", "THEME_NAME_MAX",
    "uniqueThemeDir", "landedTheme", "renamedTheme", "p8Base", "walkVfs",
)

_TOP = re.compile(r"^(?:export\s+)?(?:(?:async\s+)?function\*?|const|let|var)\s+"
                  r"([A-Za-z_$][\w$]*)", re.M)


def _names(path):
    return set(_TOP.findall(path.read_text(encoding="utf-8")))


def test_every_store_name_is_filed():
    names = _names(WEB / "moy_store.mjs")
    filed = set(MIRRORS) | set(IO)
    assert not set(MIRRORS) & set(IO)
    assert names - filed == set(), (
        "a new name in moy_store.mjs: file it in IO when it is plumbing; a "
        "rule the VM has belongs in the VM, not in a new MIRRORS entry")
    assert filed - names == set(), "a name left the store: drop it here too"


def test_no_moved_rule_comes_back():
    for f in ("moy_store.mjs", "worker.js", "page_tail.js"):
        back = _names(WEB / f) & set(MOVED)
        assert not back, (f, back)


def test_the_worker_calls_only_filed_store_names():
    worker = (WEB / "worker.js").read_text(encoding="utf-8")
    used = set(re.findall(r"\bstore\.([A-Za-z_$][\w$]*)", worker))
    exported = set(re.findall(r"^export\s+(?:(?:async\s+)?function|const|let)\s+(\w+)",
                              (WEB / "moy_store.mjs").read_text(encoding="utf-8"), re.M))
    assert used - {"mjs"} <= exported, used - exported
