#!/usr/bin/env python3
"""Pin the console API level to the app-facing tables it names.

The level is runtime/carts/moy_store_base.py's API_LEVEL; the line below it
holds the fingerprint, the sha256 of every table in TABLES, as this writes it.
While API_FROZEN is False (the API is still being built) the level stays and
the fingerprint is re-pinned freely: --check fails only when the pin is stale.
Once API_FROZEN is True, a table that changes without the level going up fails
--check (and tests/test_api_level.py), and writing refuses to re-pin it until
the level is bumped: what an app can rely on and the number a cart names move
together.

    tools/api_level.py           re-pin the fingerprint (after bumping API_LEVEL
                                 once the API is frozen)
    tools/api_level.py --check   exit 1 when the pin does not match the tables
"""

import hashlib
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


def _module_file(name):
    import importlib.util
    return os.path.abspath(importlib.util.find_spec("runtime." + name).origin)


LEVEL_FILE = _module_file("moy_store_base")
OS_CALLS = os.path.join(ROOT, "native", "os_calls.json")
PLAYER_FIT = _module_file("player_fit")

# What os_calls.json says about a row that no app can see: its prose, its
# probes, its install-prompt words and how the console serves it.
OS_CALLS_PRIVATE = ("about", "doc", "example", "prompt", "server", "how", "lua_own", "door")

_LEVEL = re.compile(r"^API_LEVEL = (\d+)$", re.M)
_FROZEN = re.compile(r"^API_FROZEN = (True|False)$", re.M)
_PIN = re.compile(r"^# API fingerprint: level (\d+) sha256 ([0-9a-f]{64})$", re.M)
_FORMAT = re.compile(r"^FORMAT_LEVEL = (\d+)$", re.M)


class PinError(Exception):
    """The pin and the tables disagree, said so a person can act."""


def _read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def _public(v):
    if isinstance(v, dict):
        return {k: _public(x) for k, x in v.items() if k not in OS_CALLS_PRIVATE}
    if isinstance(v, list):
        return [_public(x) for x in v]
    return v


def os_calls_table(text=None):
    """native/os_calls.json as an app sees it: every role, row, permission
    and error code, with OS_CALLS_PRIVATE left out. Every tier's names come
    from it: the Python and Lua app API, the ui rows, the wasm and WASI
    imports, the ELF table, the permissions and the globals each hands."""
    return _public(json.loads(_read(OS_CALLS) if text is None else text))


def manifest_table():
    """The manifest's Moybyte fields (moy_store_base.VENDOR_FIELDS)."""
    from runtime.moy_store_base import VENDOR_FIELDS
    return sorted(VENDOR_FIELDS)


def format_table(text=None):
    """The moy-spec format level this console runs (player_fit.FORMAT_LEVEL):
    a new game level raises the API level with it."""
    return int(_FORMAT.search(_read(PLAYER_FIT) if text is None else text).group(1))


# The public tables, by name, in the fingerprint's order. A new app-facing
# table joins here (the UI tree API's tables, when it lands).
TABLES = (
    ("os_calls", os_calls_table),
    ("manifest", manifest_table),
    ("format", format_table),
)


def tables():
    return [[name, make()] for name, make in TABLES]


def fingerprint(tabs=None):
    """The sha256 (64 hex) of the public tables `tabs` (tables())."""
    doc = json.dumps(tables() if tabs is None else tabs, sort_keys=True,
                     separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(doc.encode()).hexdigest()


def pinned(text):
    """(API_LEVEL, (pinned level, pinned fingerprint)) of the level file's
    text; the pin is None when the line is missing."""
    lv = _LEVEL.search(text)
    if lv is None:
        raise PinError("%s names no API_LEVEL" % LEVEL_FILE)
    pin = _PIN.search(text)
    return int(lv.group(1)), (None if pin is None else (int(pin.group(1)), pin.group(2)))


def frozen(text):
    """True when the level file's `text` has the API frozen (API_FROZEN)."""
    fz = _FROZEN.search(text)
    if fz is None:
        raise PinError("%s names no API_FROZEN" % LEVEL_FILE)
    return fz.group(1) == "True"


def check(text, fp):
    """None when the level file's `text` pins fingerprint `fp` at its own
    API_LEVEL, else what to do."""
    level, pin = pinned(text)
    if pin is not None and pin == (level, fp):
        return None
    if not frozen(text):
        return "re-pin the fingerprint with tools/api_level.py"
    if pin is not None and pin[0] == level:
        return ("an app-facing table changed (native/os_calls.json, "
                "moy_store_base.VENDOR_FIELDS, player_fit.FORMAT_LEVEL or another of "
                "tools/api_level.py's TABLES) and the API level did not: bump the API "
                "level in runtime/carts/moy_store_base.py (API_LEVEL = %d) and re-pin "
                "the fingerprint with tools/api_level.py" % (level + 1))
    return ("API_LEVEL is %d and its fingerprint is not pinned at it: re-pin the "
            "fingerprint with tools/api_level.py" % level)


def repin(text, fp):
    """The level file's `text` with fingerprint `fp` pinned at its API_LEVEL.
    Raises PinError when the API is frozen and the tables moved while the
    level did not."""
    level, pin = pinned(text)
    if pin is None:
        raise PinError("%s has no '# API fingerprint:' line under API_LEVEL" % LEVEL_FILE)
    if frozen(text) and pin[1] != fp and pin[0] >= level:
        raise PinError("the tables changed at API level %d: bump the API level in "
                       "runtime/carts/moy_store_base.py first" % level)
    line = "# API fingerprint: level %d sha256 %s" % (level, fp)
    return _PIN.sub(line, text, count=1)


def main(argv):
    text = _read(LEVEL_FILE)
    fp = fingerprint()
    if "--check" in argv:
        why = check(text, fp)
        if why is not None:
            print(why, file=sys.stderr)
            return 1
        return 0
    try:
        new = repin(text, fp)
    except PinError as exc:
        print(exc, file=sys.stderr)
        return 1
    if new != text:
        with open(LEVEL_FILE, "w", encoding="utf-8") as f:
            f.write(new)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
