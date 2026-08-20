"""The browser tab shows Moy, and it is the SAME Moy the console draws.

The favicon is generated from `runtime/chrome._ICON_ART["moy"]` -- the art the
boot splash and the launcher header already use -- and embedded in the page as a
data: URI. That is the point: a hand-drawn PNG checked in beside it would be a
second copy of the mascot, free to drift the moment anyone touches the art, and
nothing would notice because a favicon is the one image nobody looks at closely.

Regenerate with `python tools/gen_favicon.py --write`.
"""

import base64
import re
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "runtime"))

PAGE = ROOT / "firmware" / "web_runner" / "page_core.html"
SITE = ROOT / "site" / "build.py"


def _embedded(path=None):
    m = re.search(r'rel=?"?icon"? href="data:image/png;base64,([^"]+)"',
                  (path or PAGE).read_text(encoding="utf-8"))
    assert m, "no favicon in %s" % (path or PAGE).name
    return base64.b64decode(m.group(1))


def test_the_page_has_a_favicon_and_it_is_a_real_png():
    raw = _embedded()
    assert raw[:8] == b"\x89PNG\r\n\x1a\n"
    w, h, depth, ctype = struct.unpack(">IIBB", raw[16:26])
    assert w == h == 32                      # 16x16 art at 2x
    assert (depth, ctype) == (8, 6), "must be 8-bit RGBA -- Moy has transparency"


def test_it_is_byte_for_byte_what_the_mascot_art_generates():
    """The anti-drift pin. If someone edits _ICON_ART["moy"], this fails and the
    fix is one command -- which is the whole reason it is generated."""
    from tools.gen_favicon import data_uri
    embedded = "data:image/png;base64," + base64.b64encode(_embedded()).decode()
    assert embedded == data_uri(), \
        "favicon is stale -- run: python tools/gen_favicon.py --write"


def test_moy_is_transparent_where_the_art_says_he_is():
    """`.` in the art means TRANSPARENT, and getting that wrong is not subtle:
    index 0 is also Moy's OUTLINE colour, so treating blanks as index 0 boxes
    him in a black square (the bug console.splash_image exists to avoid)."""
    from tools.gen_favicon import mascot_rgba
    rows, size = mascot_rgba(1)
    alphas = {row[i + 3] for row in rows for i in range(0, len(row), 4)}
    assert 0 in alphas, "nothing is transparent -- the mascot is boxed"
    assert 255 in alphas, "nothing is opaque -- the mascot is missing"


def test_the_marketing_site_wears_the_same_face():
    """moybyte.com and the console must not have different mascots -- it is the
    same product, and the site already generates its whole palette from
    runtime/palette.py for exactly this reason."""
    assert _embedded(SITE) == _embedded(PAGE)
