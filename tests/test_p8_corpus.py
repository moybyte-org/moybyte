"""Imported p8 carts on THIS host -- a TIER-PARITY check, not the porter's gate.

The porter's own gate is upstream, in moy-spec: `make -C libmoy p8-carts` runs
the same corpus through run_cart, which is the real C console on the real
LUA_32BITS VM, and measures runs/animates/responds there. That is where a
porter bug should turn something red, because that is where the porter lives.

What THIS file adds is the PLAYER'S FRAME PACING, and that is the whole of it.

It is not a second console: `runtime/lua_host` runs carts through
`runtime/lua_binding`, which compiles libmoy's own binding over the same
vendored Lua 5.4 with the same LUA_32BITS, and libmoy rasterises into this
canvas. Same VM, same verb table, same raster. Two earlier versions of this
docstring claimed otherwise -- first that only the host could feed input
(libmoy has always had an input API; run_cart just did not use it), then that
this was a second implementation for tier parity (it is not). Both were
rationalisations of what had already been built.

What run_cart genuinely does not model is TIME. It calls update once per frame
at a fixed 1/30. The Player runs a cart's own rate against the host's -- a
60fps cart on a 30fps host, the catch-up loop, and the btnp latch that spans
console frames -- and that is exactly where two real bugs lived: a 60fps cart
whose update never ran, and a tap that produced two menu edges because two
cart ticks fell inside one console frame. Nothing upstream can see either.

Why either exists at all: every porter bug found on 2026-09-01 -- fifteen
dialect rules, a 60fps cart whose update never ran, a tap that moved two menu
slots -- was found by importing a famous cart and LOOKING at it. Not one was
caught by the unit suite, which stayed green throughout. A cart that never
ticks never errors either, so "no exception" is not a measurement; the
measurement is whether the screen changes. `tests/p8_corpus_expected.json` records what each
cart currently does, and this test fails when a cart does LESS. A cart that
does more fails too, loudly, asking for the file to be updated -- a ratchet
that only moves one way is the point.

THE CARTS ARE NOT IN THE REPO. They are other people's work, several under
licences that forbid redistribution, so the corpus lives outside the tree and
this test SKIPS without it:

    python tools/fetch_p8_corpus.py            # downloads to the default dir
    MOYBYTE_P8_CORPUS=~/.cache/moybyte/p8 pytest tests/test_p8_corpus.py

CI can set that variable against a cached corpus; a laptop without one still
gets a green suite, and the skip says why.

WHAT THE TWO NUMBERS PROVE, because it is easy to over-read them (both checked
against deliberately dead and deliberately live probe carts, 2026-09-01):

  `distinct`  -- how many different frames the cart drew. A frozen cart scores
                 1 and a live one scores 25+, so this catches "nothing runs".
                 It does NOT prove the cart is playable: a title screen that
                 animates from time() keeps scoring high while the cart's
                 update is dead, which is exactly what a mutation of the
                 _update60 driver showed.
  `responds`  -- the pixel history with a direction held differs from the one
                 without. This is the strong signal, and it is not fooled by
                 rnd(): a cart that ignores input and draws random pixels
                 scores False, because the runs are seeded alike.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
EXPECTED = HERE / "p8_corpus_expected.json"
RUNNER = HERE / "p8_corpus_runner.py"

# A cart that spins takes the whole run with it otherwise -- `terra` does, in
# its own world generation, and nothing inside the process can stop it.
TIMEOUT_S = 240


def _corpus_dir():
    env = os.environ.get("MOYBYTE_P8_CORPUS")
    if env:
        return Path(env).expanduser()
    return Path.home() / ".cache" / "moybyte" / "p8"


def _load_expected():
    return json.loads(EXPECTED.read_text(encoding="utf-8"))


def _find(corpus, stem):
    for suffix in (".p8.png", ".p8"):
        p = corpus / (stem + suffix)
        if p.exists():
            return p
    return None


@pytest.fixture(scope="module")
def corpus():
    d = _corpus_dir()
    if not d.is_dir():
        pytest.skip("no p8 corpus at %s -- see this file's docstring "
                    "(tools/fetch_p8_corpus.py)" % d)
    return d


def _run_one(corpus, stem, tmp_path):
    cart = _find(corpus, stem)
    if cart is None:
        pytest.skip("%s not in the corpus at %s" % (stem, corpus))
    proc = subprocess.run(
        [sys.executable, str(RUNNER), str(cart), str(tmp_path), stem],
        capture_output=True, text=True, timeout=TIMEOUT_S, cwd=str(ROOT))
    lines = [l for l in proc.stdout.splitlines() if l.startswith("{")]
    assert lines, ("the runner printed no result for %s\nstdout: %s\nstderr: %s"
                   % (stem, proc.stdout[-800:], proc.stderr[-800:]))
    return json.loads(lines[-1])


@pytest.mark.parametrize("stem", sorted(_load_expected()["carts"]))
def test_a_corpus_cart_still_does_what_it_did(corpus, stem, tmp_path):
    """Boots, animates and takes input -- each pinned, none allowed to regress."""
    want = _load_expected()["carts"][stem]
    if want.get("hangs"):
        pytest.xfail("%s: %s" % (stem, want.get("why", "hangs")))
    got = _run_one(corpus, stem, tmp_path)

    if want["boots"]:
        assert got["error"] is None, (
            "%s used to boot and now stops:\n  %s" % (stem, got["error"]))
    if want.get("unstable"):
        # It flapped across the runs its pin was taken from -- petal_quest
        # gets past its title only sometimes inside the frame budget, then
        # stops on cocreate. Still gated on BOOTING, which is stable; naming
        # the one flaky cart beats loosening the gate for the eleven that
        # are not.
        return
    assert got["distinct"] >= want["distinct"], (
        "%s animated %d distinct frames, expected at least %d -- something it "
        "used to draw stopped drawing" % (stem, got["distinct"], want["distinct"]))
    if want.get("responds"):
        assert got["responds"], (
            "%s stopped responding to input: holding a direction produced the "
            "same pixels as not holding one" % stem)


def test_the_ratchet_is_not_behind_reality(corpus, tmp_path):
    """A cart doing BETTER than recorded fails too, and says so.

    A ratchet that only tightens when someone remembers to tighten it is a
    ratchet that never tightens. This is the nag."""
    expected = _load_expected()["carts"]
    behind = []
    for stem, want in sorted(expected.items()):
        if want.get("hangs"):
            continue
        got = _run_one(corpus, stem, tmp_path)
        if want.get("unstable"):
            continue
        if got["error"] is None and not want["boots"]:
            behind.append("%s BOOTS now (was: %s)" % (stem, want.get("why", "no")))
        # A wide margin on purpose. `distinct` swings with what a title
        # screen happens to animate, and a ratchet that nags on noise is a
        # ratchet someone deletes.
        elif got["distinct"] > want["distinct"] * 1.3 + 12:
            behind.append("%s animates %d frames, pinned at %d"
                          % (stem, got["distinct"], want["distinct"]))
    assert not behind, (
        "the corpus improved -- raise tests/p8_corpus_expected.json:\n  "
        + "\n  ".join(behind))
