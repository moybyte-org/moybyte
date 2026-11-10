"""The browser's JavaScript does I/O; the console's decisions are the VM's
(owner, 2026-11-08; docs/moycore_direction.md section 3, the audit).

A rule copied into JavaScript is a second implementation that drifts: Notes'
NameError came from the browser's own re-seed rule disagreeing with the
board's. `moy_store.mjs` holds no rule of its own: what a path, a name, a song
or a root is comes from the VM through the worker's `bind` (bindStore). This
holds it there:

  * `EXPORTS` -- the store's whole exported surface. The list only SHRINKS: a
    new export is new surface for a rule to grow back on.
  * `INTERNAL` -- its unexported names, the OPFS plumbing.
  * `WORDS` -- every string literal the store holds. A file, folder or suffix
    named here would be a layout rule, and the VM's registry hands those in;
    the store's own words are its op reasons, its keeper's file names (OPFS
    has no rename: `commitFile`, `commitInstall`) and the browser's API.
  * no regular expression but the channel's word: a channel is a page's
    (site/build.py), and no console has one.

And worker.js may call only exported names on the store, and no rule that
moved into the VM comes back under its old name.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "firmware" / "web_runner"

EXPORTS = (
    # the VM's rules, handed in (worker.js, bindStore)
    "bind", "skipName", "skipLocal", "roots", "rootById",
    # bytes
    "fromBase64", "toBase64", "fileData",
    # the mode probe and the browser's keep
    "probeMode", "requestPersistence", "storageNote", "CHANNEL_DIR_PREFIX", "channelNav",
    # the stores
    "openStore", "applyOps", "readAll", "seed", "removeFile",
    "songs", "songFile", "keepSong",
    "commitInstall", "recoverInstalls", "writeRecord", "readRecord",
    "commitWritten", "dropWritten", "readWritten",
)

INTERNAL = (
    "rules", "partsIn", "mbytes", "enc", "strict", "dirFor", "namedAt", "writeText",
    "writeIn", "removeAt", "applyOne", "asValue", "walk", "readMarker",
    "rollForward", "PART", "rollFile", "hasFile", "commitFile", "dropFile",
    "settleFiles",
)

WORDS = {
    "", " ", "/", "MB", "utf-8",
    # the browser's vocabulary
    "string", "number", "object", "function", "file", "directory",
    "GET", "POST", "Content-Type", "application/json", "sync",
    "granted", "denied", "unsupported", "kept", "evictable",
    "evictable (cannot ask here)",
    # the two modes, and the store a store opens by default
    "board", "site", "carts",
    # the keeper's own files in OPFS, and the channel's directory
    "~part", "~done", ".commit", "channel-",
    # an op's or a keeper's refusal
    "not an op", "bad path", "bad dc target", "no staged parts", "no bytes",
    "no text", "bad folder ", "bad file ", "bad written file ",
}

CHANNEL_WORD = r"/^[a-z][a-z0-9-]{0,31}$/"

# The rules that moved into the VM; none may come back under its old name.
MOVED = (
    "systemSeeds", "cartVersion", "preserved", "RESEED_PRESERVE",
    "crc32", "CRC_TABLE", "zipStore", "inflateRaw", "unzip", "zipTopDir",
    "cartBase", "uniqueCartDir", "themeOf", "themeName", "THEME_NAME_MAX",
    "uniqueThemeDir", "landedTheme", "renamedTheme", "p8Base", "walkVfs",
    "SKIP_DIRS", "SKIP_FILES", "SKIP_SUFFIXES", "SITE_SKIP_DIRS", "SITE_SKIP_FILES",
    "skipIn", "BINARY_FILES", "BINARY_SUFFIXES", "isBinary", "ROOTS", "isEmpty",
    "safeSegments", "localParts", "plainName", "writtenName", "isSong",
    "MUSIC_DIR", "RECORD_NAME", "STAGE_DIR", "WRITTEN_ROOT", "RECORD_VFS",
    "WRITTEN_VFS", "openWritten", "setSkip", "setRoots",
)

_TOP = re.compile(r"^(export\s+)?(?:(?:async\s+)?function\*?|const|let|var)\s+"
                  r"([A-Za-z_$][\w$]*)", re.M)


def _store():
    return (WEB / "moy_store.mjs").read_text(encoding="utf-8")


def _code(src):
    """`src` without its comments (a line comment is never inside a literal
    in these modules: none holds a "//")."""
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    return re.sub(r"(^|[^:\"'])//.*$", r"\1", src, flags=re.M)


def _names(path):
    return {m.group(2) for m in _TOP.finditer(path.read_text(encoding="utf-8"))}


def test_the_store_exports_only_its_filed_surface():
    found = _TOP.findall(_store())
    exported = {n for e, n in found if e}
    internal = {n for e, n in found if not e}
    assert exported - set(EXPORTS) == set(), (
        "a new export of moy_store.mjs: the store's surface only shrinks, and a "
        "rule it would carry is the VM's (bind)")
    assert set(EXPORTS) - exported == set(), "an export left the store: drop it here too"
    assert internal - set(INTERNAL) == set(), (
        "a new name in moy_store.mjs: file it in INTERNAL when it is plumbing")
    assert set(INTERNAL) - internal == set(), "a name left the store: drop it here too"


def test_the_store_names_no_file_and_matches_no_pattern():
    code = _code(_store())
    words = set(re.findall(r'"((?:[^"\\]|\\.)*)"', code))
    assert words - WORDS == set(), (
        "a new string in moy_store.mjs: a file, folder or suffix it names is a "
        "layout rule the VM's registry hands in (moy_sync.roots_json)")
    bare = re.sub(r'"(?:[^"\\]|\\.)*"', '""', code)
    assert "'" not in bare, "the store quotes with \""
    assert "`" not in bare, "no template strings in the store"
    regexes = re.findall(r"(?<![\w)\]\s])\s*(/(?![/*])(?:[^/\\\n]|\\.)+/[gimsuy]*)", bare)
    assert [r.strip() for r in regexes] == [CHANNEL_WORD], regexes


def test_no_moved_rule_comes_back():
    for f in ("moy_store.mjs", "worker.js", "page_tail.js"):
        back = _names(WEB / f) & set(MOVED)
        assert not back, (f, back)


def test_the_worker_calls_only_exported_store_names():
    worker = (WEB / "worker.js").read_text(encoding="utf-8")
    used = set(re.findall(r"\bstore\.([A-Za-z_$][\w$]*)", worker))
    assert used - {"mjs"} <= set(EXPORTS), used - set(EXPORTS)
