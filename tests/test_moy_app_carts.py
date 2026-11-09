"""`moy app check` over every cart the repository carries: each seed in
system_carts/ and each fixture cart under tests/. None has an error -- a
manifest the console's readers misread (a Moybyte field at the top level,
an unknown permission, a type no tier runs) -- and a warning stands only
where the lists below give its reason.
"""

import glob
import os
import re
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "sdk"))

from moybyte_sdk import cli  # noqa: E402


def _carts():
    out = []
    for pattern in ("system_carts/*.moy", "tests/**/*.moy"):
        for d in glob.glob(os.path.join(ROOT, pattern), recursive=True):
            if os.path.isfile(os.path.join(d, "manifest.json")):
                out.append(os.path.relpath(d, ROOT))
    return sorted(out)


CARTS = _carts()

# Warnings every fixture under tests/ may give, by kind.
FIXTURE_KINDS = (
    # a fixture carries its source; the test that runs it builds it
    (r"not built: `moy app build",),
    # a spec fixture or a minimal game: keyed by its folder, no permissions
    (r'^no "moybyte" object: a game with no permissions$',),
    (r'^no "id" of the form <author>\.<name>',),
)

# The warnings one cart may give, each with its reason.
LISTED = {
    # The shell presents these as system processes, which use the permission;
    # the cart body is the fallback an older shell runs.
    "system_carts/moybyte.appearance.moy": (r'^"appearance" is declared and nothing',),
    "system_carts/moybyte.files.moy": (r'^"files" is declared and nothing',),
    "system_carts/moybyte.music.moy": (r'^"media" is declared and nothing',
                                       r'^"media:url" is declared and nothing',
                                       r'^"http" is declared and nothing'),
    # Notes' editor handle (open_editor) copies and pastes for it.
    "system_carts/moybyte.notes.moy": (r'^"clipboard" is declared and nothing',),
    # head.c's fopen reaches open through the SDK's C library, which a scan of
    # the source does not follow (a built module's rows show it).
    "tests/fixtures/elf/elf_head.moy": (r'^"files" is declared and nothing',),
    # tests/test_elf_commands.py pins both refused: "at -1 EACCES",
    # "nav.open_app -1 EACCES".
    "tests/fixtures/elf/elf_app.moy": (r"^main uses moy_at, which needs \"alarms\"",
                                       r"^main uses moy_nav_open_app, which needs \"launch\""),
    # tests/test_wasi_commands.py sets this service's permissions per test and
    # pins the calls they refuse.
    "tests/fixtures/wasm/wasi_svc.moy": (r"^main uses moybyte\.os\.",),
    # The theme's reads reach every compiled app (runtime/run/player.py hands a
    # run the theme beside its roles); the table puts the role under
    # "appearance", whose verbs set the look.
    "tests/fixtures/wasm/app_roles.moy": (r"^main uses moybyte\.app\.theme_(gen|token), "
                                          r"which needs \"appearance\"",),
}


def _allowed(cart, warning):
    pats = list(LISTED.get(cart, ()))
    if cart.startswith("tests" + os.sep):
        pats += [p for kind in FIXTURE_KINDS for p in kind]
    return any(re.search(p, warning) for p in pats)


def test_the_corpus_is_found():
    assert sum(c.startswith("system_carts") for c in CARTS) > 20
    assert sum(c.startswith("tests") for c in CARTS) > 50
    assert set(LISTED) <= set(CARTS), "a listed cart is gone: drop its entry"


@pytest.mark.parametrize("cart", CARTS)
def test_every_cart_checks_with_no_error_and_only_its_listed_warnings(cart):
    errors, warnings, _notes = cli.check_cart(os.path.join(ROOT, cart))
    assert errors == []
    assert [w for w in warnings if not _allowed(cart, w)] == []


@pytest.mark.parametrize("cart", sorted(LISTED))
def test_every_listed_warning_still_stands(cart):
    _errors, warnings, _notes = cli.check_cart(os.path.join(ROOT, cart))
    for p in LISTED[cart]:
        assert any(re.search(p, w) for w in warnings), (
            "%s no longer warns %r: drop it from the list" % (cart, p))
