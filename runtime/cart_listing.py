"""cart_listing -- how a host reads what a manifest says about a cart in a
list (moy-spec SPEC.md 3.7): `description`, `genre` and `tags`.

Every field is COSMETIC and degrades to absent; none refuses a cart. Each
reader takes the raw value of a manifest (or of an index entry) and returns what
the launcher and Get Carts show: a description (a string, or None), a genre (one
of GENRES, or None) and the tags (a list, empty when none). The rules are the
spec's, and `tests/test_cart_listing.py` runs them over moy-spec's
`conformance/listing.json` (`tests/listing_vectors/`).

Leaf module, no imports, so `moy_carts` (the manifest) and `cart_index` (an
index entry) read a cart the same way on the host and on a board.
"""

GENRES = ("action", "adventure", "arcade", "music", "platformer", "puzzle",
          "racing", "rpg", "shooter", "sports", "strategy", "toy")
DESCRIPTION_MAX = 240                # bytes of UTF-8
TAGS_MAX = 8                         # entries read
TAG_MAX = 16                         # characters in one tag
_TAG_CHARS = "abcdefghijklmnopqrstuvwxyz0123456789-"
PICO8_TAG = "pico8"                  # reserved: converted from PICO-8


def description(value):
    """A manifest `description` as a host reads it, or None: its first
    DESCRIPTION_MAX bytes, ending on a character boundary, every control
    character a space, the ends trimmed of spaces. Not a string, or nothing
    but spaces, is absent."""
    if not isinstance(value, str):
        return None
    try:
        raw = value.encode("utf-8")
    except UnicodeError:                     # a lone surrogate: not text
        return None
    if len(raw) > DESCRIPTION_MAX:
        raw = raw[:DESCRIPTION_MAX]
        lead = len(raw) - 1
        while lead > 0 and (raw[lead] & 0xC0) == 0x80:
            lead -= 1
        b = raw[lead]
        need = 4 if b >= 0xF0 else 3 if b >= 0xE0 else 2 if b >= 0xC0 else 1
        if len(raw) - lead < need:           # the cut split a character
            raw = raw[:lead]
        value = raw.decode("utf-8")
    value = "".join(" " if ord(c) < 32 or ord(c) == 127 else c for c in value).strip(" ")
    return value or None


def genre(value):
    """A manifest `genre`, or None unless it is exactly one of GENRES."""
    return value if isinstance(value, str) and value in GENRES else None


def _tag_ok(tag):
    if not isinstance(tag, str) or not 1 <= len(tag) <= TAG_MAX:
        return False
    for c in tag:
        if c not in _TAG_CHARS:
            return False
    return True


def tags(value):
    """A manifest `tags`: of the first TAGS_MAX entries the ones that are
    tags, each once, in order. Not an array is no tags."""
    if not isinstance(value, (list, tuple)):
        return []
    out = []
    for tag in value[:TAGS_MAX]:
        if _tag_ok(tag) and tag not in out:
            out.append(tag)
    return out


def genre_word(g):
    """A genre as the launcher says it: title-cased, `rpg` as RPG."""
    if not g:
        return None
    return "RPG" if g == "rpg" else g[0].upper() + g[1:]


def listing(man):
    """(description, genre, tags) of a manifest or an index entry."""
    return (description(man.get("description")), genre(man.get("genre")),
            tags(man.get("tags")))
