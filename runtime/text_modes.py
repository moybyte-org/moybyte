"""The editor MODE table: what a file's NAME says about how to edit it.

One console-wide answer to "how is this text edited", so the Editor's Code tab
and the shell's text page cannot disagree about a file they both open. A mode
is four facts:

    wrap   soft-wrap long lines (prose) or scroll them sideways (code)
    lang   which syntax highlighter and symbol palette
    gate   what a SOFT save (the idle debounce) refuses to publish

`md` is prose and `code` is a program because `moy_carts.FILE_KINDS` and
`DOC_EXT` say so -- nothing here spells an extension the store already owns, so
a kind that changes its extension keeps its mode for free.

The GATE is the #154 split, generalized: a soft save refuses a document that
does not parse and badges it, and a HARD exit (going home, a tab switch, the
power button) writes it anyway and KEEPS the badge, because a kid's text is
theirs and a broken document is caught the next time something loads it. Code
asks the cart's runtime (`moy_carts.runtime_compile_check`); JSON asks
`json.loads`, which every tier has.

`wrap` is DECLARED here and is not yet pixels: today's text page has no
wrapping view, so nothing reads it but the tests. The editor handle (step 3 of
docs/text_editing_2026-09.md) is what consumes it.
"""

import json as _json

try:
    import moy_carts as _store
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime import moy_carts as _store


MD = "md"
CODE = "code"
JSON = "json"
TEXT = "text"


class Mode:
    """One mode's facts. `__slots__` because every editor open reads all four
    and a dict lookup per frame is the thing the app-context hoist rule bans."""

    __slots__ = ("name", "wrap", "lang", "gate")

    def __init__(self, name, wrap, lang, gate):
        self.name = name
        self.wrap = wrap
        self.lang = lang
        self.gate = gate


MODES = {
    MD:   Mode(MD, True, None, None),
    CODE: Mode(CODE, False, "python", "runtime"),
    JSON: Mode(JSON, False, None, "json"),
    TEXT: Mode(TEXT, False, None, None),
}


# A file whose NAME decides, whatever its extension. A cart's manifest and
# config are the shipped entries: they are the two files a project's ADVANCED
# row will offer, and reading them as anything but JSON would let a kid save a
# cart that no longer loads.
BY_NAME = {
    "manifest.json": JSON,
    "config.json": JSON,
}

# Extension -> mode. `.md` is asked of the store (DOC_EXT), never spelled.
BY_EXT = {
    _store.DOC_EXT: MD,
    ".py": CODE,
    ".lua": CODE,
    ".json": JSON,
}

# The per-extension highlighter, for a file edited on its own (a script in the
# vault). A cart's Code tab asks `cart_lang` instead -- the RUNTIME is the
# authority there, because it is what picks the main file's name in the first
# place.
BY_EXT_LANG = {
    ".lua": "lua",
    ".py": "python",
}

# Not a mode: the drawings kind is not text at all, and Paint owns it. Here so
# that ONE table answers "what is this file" for the Files router too.
IMAGE_EXT = _store.FILE_KINDS["drawings"][0]


def ext_of(filename):
    """`filename`'s lowercased extension including the dot, or "" -- a leading
    dot is a whole name (`.gitignore`), never an extension."""
    name = str(filename)
    dot = name.rfind(".")
    return name[dot:].lower() if dot > 0 else ""


def mode_for(filename):
    """The mode `filename` edits in -- one of MD/CODE/JSON/TEXT.

    Answers for anything, because anything a person opens in a text editor IS
    text once it is open; ask `is_image` first for the files that are not."""
    name = str(filename)
    slash = max(name.rfind("/"), name.rfind("\\"))
    base = name[slash + 1:] if slash >= 0 else name
    named = BY_NAME.get(base.lower())
    if named is not None:
        return named
    return BY_EXT.get(ext_of(base), TEXT)


def mode(filename):
    """`mode_for`'s `Mode` object."""
    return MODES[mode_for(filename)]


def file_name(kind, name):
    """A user-files item's on-disk file name: the kind's own extension from
    `moy_carts.FILE_KINDS`. An unknown kind contributes no extension, so a
    caller that already holds a real file name can pass `kind=None`."""
    spec = _store.FILE_KINDS.get(kind) if kind else None
    return str(name) + (spec[0] if spec else "")


def mode_for_kind(kind, name=""):
    """The mode a user-files kind's items edit in -- `docs` is markdown because
    DOC_EXT says so, not because anything here spells `.md`."""
    return mode_for(file_name(kind, name))


def is_image(filename):
    """True when `filename` is a drawing (the drawings kind's extension), which
    is the one thing the router sends somewhere other than a text page."""
    return ext_of(filename) == IMAGE_EXT


def lang_for(filename):
    """The highlighter/palette language for a standalone source file."""
    return BY_EXT_LANG.get(ext_of(filename), MODES[CODE].lang)


def cart_lang(cart):
    """The Code tab's language: the cart's declared RUNTIME (#67 Phase 5).

    Deliberately NOT the main file's extension. The runtime is what chooses
    that name, and it is also what `runtime_compile_check` gates on, so asking
    it here keeps the highlighter, the symbol palette and the parse gate on one
    answer for a cart whose manifest names an unexpected main."""
    return "lua" if (cart or {}).get("runtime") == "lua" else "python"


def check(mode_name, text, cart=None):
    """`(ok, message)` for `mode_name`'s SOFT-save gate -- `compile_check`'s
    shape, including its "line N: reason" message, so one badge renders them
    all. A mode with no gate always answers ok.

    A board's `json` raises a bare ValueError with no position, so the line is
    reported when the tier can say it and dropped when it cannot -- the same
    degradation `compile_check` already makes for a `SyntaxError` without a
    `lineno`."""
    gate = MODES[mode_name].gate
    if gate == "json":
        try:
            _json.loads(text)
            return True, ""
        except Exception as exc:  # noqa: BLE001 -- MicroPython raises ValueError
            return False, _located(exc)
    if gate == "runtime":
        return _store.runtime_compile_check(cart, text)
    return True, ""


def _located(exc):
    line = getattr(exc, "lineno", None)
    msg = getattr(exc, "msg", None) or str(exc)
    return ("line %d: %s" % (line, msg)) if line else str(msg)
