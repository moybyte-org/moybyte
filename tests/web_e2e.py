"""What the two browser end-to-end suites need, and what an absence MEANS.

`tests/test_web_sync_e2e.py` and `tests/test_web_persist_e2e.py` are the only
checks in the tree that drive the HOSTED CONSOLE -- the wasm head a visitor to
moybyte.com touches, and the same page a board serves over WiFi -- in a real
browser. Both are gated on `MOYBYTE_WEB_E2E` because they cost a Chrome window
and a couple of minutes, and both used to carry their own copy of the same
prerequisite ladder (chrome, node, a dist/ new enough to have the thing under
test) with a bare `pytest.skip` at every rung.

The skip is RIGHT on a bench: a laptop with no emsdk build is a bench fact, not
a regression, and failing there would only teach people to stop running the
suite. In CI that same skip INVERTS into the hazard it was protecting against
-- a job that asks for these suites and then skips every one of them is a green
check that proves nothing, which is exactly how the compiled-vs-compiled raster
check was absent from every runner for months (tests/unix_mp.py's docstring).

So the absence is loud in the shape this repo already uses for the desktop
MicroPython (`MOYBYTE_REQUIRE_UNIX_MP`), the moy_flush harness
(`MOYBYTE_REQUIRE_MOY_FLUSH`) and the baked web bundle
(`MOYBYTE_REQUIRE_WEB_BUNDLE`): warn locally, FAIL under `CI` or
`MOYBYTE_REQUIRE_WEB_E2E`. Note the two switches are not the same one --
`MOYBYTE_WEB_E2E` asks for the suite to RUN, this one says a prerequisite for
it is a broken job rather than a missing toolchain.

The dist/ probes are STALENESS probes, and they are per-feature on purpose: a
build from before the pin prompt can still prove the sync loop, and reporting
it as unable to would be a false red on the half that works.
"""

import os
import shutil
import warnings
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RUNNER = ROOT / "firmware" / "web_runner"
DIST = RUNNER / "dist"
CHROME = os.environ.get("MOY_CHROME", "google-chrome")

BUILD = "firmware/web_runner/build.sh"

# Feature -> (is it in this dist?, what its absence is). Every probe reads a
# file out of dist/, so `missing()` never reaches them without one.
FEATURES = {
    "sync": (lambda: "syncPump" in (DIST / "worker.js").read_text(),
             "dist/worker.js predates the sync client"),
    "store": (lambda: (DIST / "moy_store.mjs").exists(),
              "dist/ predates the browser store (no moy_store.mjs)"),
    "pin": (lambda: "__moyPinRestore" in (DIST / "index.html").read_text(),
            "dist/index.html predates the in-page pin prompt"),
}


def missing(*features):
    """Every unmet prerequisite, as ready-to-print lines. Empty means ready."""
    out = []
    if shutil.which(CHROME) is None:
        out.append("no %s on PATH -- install Google Chrome, or point "
                   "MOY_CHROME at a Chromium binary" % CHROME)
    if shutil.which("node") is None:
        out.append("no node on PATH -- browsershot.mjs drives Chrome over the "
                   "DevTools Protocol with node 22's own WebSocket client")
    if not (DIST / "index.html").exists():
        out.append("no built firmware/web_runner/dist -- run %s (it clones "
                   "emsdk, ~1.7GB, the first time)" % BUILD)
        return out                  # every probe below reads a file in dist/
    for name in features:
        probe, why = FEATURES[name]
        if not probe():
            out.append("%s -- rebuild it with %s" % (why, BUILD))
    return out


def require(*features):
    """Ready to drive a real browser, or a LOUD absence."""
    import pytest                   # lazy, to match tests/unix_mp.py

    problems = missing(*features)
    if not problems:
        return
    text = "\n".join(["the browser end-to-end run did not happen:", ""]
                     + ["  - " + p for p in problems])
    if os.environ.get("CI") or os.environ.get("MOYBYTE_REQUIRE_WEB_E2E"):
        pytest.fail(text + "\n\nMOYBYTE_WEB_E2E asked for this suite, so a "
                    "missing prerequisite here is a broken job and not a bench "
                    "fact: a run that skips every browser check is a green "
                    "tick over an untested hosted console.")
    warnings.warn(UserWarning(text), stacklevel=2)
    pytest.skip("browser e2e prerequisites missing (see the warning above)")
