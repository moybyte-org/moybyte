#!/usr/bin/env python3
"""Re-vendor the PICO-8 converter and Lua porter from a moy-spec checkout.

    make vendor-p8-import                          # ../moy-spec beside this repo
    make vendor-p8-import SPEC=/path/to/moy-spec
    python3 tools/vendor_p8_import.py --check      # what would change, touch nothing

TWO FILES, ONE UPSTREAM.

`p8_import.py` is moy-spec's `.p8`/`.p8.png` -> moy asset converter: sheet, SFX
bank and music tracks. It lives upstream because SPEC.md is what says what the
output must mean -- 8.1 pins `57 = A4 = 440 Hz`, which is what fixes PICO-8's
pitch offset at 24, and 8.1's keyed rest is what makes a ported slide glide from
the right note. libmoy's synth implements the other end of that same contract
(`moy_audio.c`: `p8key = pitch - 24.0f`), and it is vendored here too, so the
converter and the synth must come from ONE upstream version or they agree by
luck.

`p8_lua_port.py` is the other half: the same cart's CODE, mechanically converted
p8-Lua -> Lua 5.4 under a generated PICO-8 compat shim, so an imported cart RUNS
instead of arriving as a porting exercise (owner call 2026-08-29 -- transcribing
Lua that is almost identical in Python teaches syntax, not game-making). It is
upstream for a stronger reason than the converter: SPEC.md is what the shim is
written AGAINST, verb for verb, and a shim maintained here would drift from the
verb table the whole point of `.moy` is to share. It is also what emits the
manifest, the `map.moymap` and the assets now, through the converter's own
functions -- so the two files re-vendor together or the shim and the bank it
plays disagree about the same cart.

WHY THIS FILE EXISTS AT ALL. It used to be a hand-copy. Upstream corrected the
pitch offset (0 -> 24, two octaves) and the copy here never heard about it; this
repo's own tests had meanwhile pinned the wrong model, so re-syncing would have
meant deliberately breaking a green test, and nobody did. Every imported cart
played two octaves flat for ten days. Copying by hand worked exactly once; this
makes it a command, and leaves a stamp behind so `tests/test_p8_import_vendor.py`
can notice both halves of the failure -- the copy edited here, and upstream
edited without a re-vendor.

WHY NOT FOLD IT INTO tools/vendor_libmoy.py: that script vendors C that gets
COMPILED into the two boards' firmware, and its test's argument is about audio
having no pixel-conformance golden. This is a host-side Python tool with its own
tests. Same upstream, same idea, different consumers -- and one script whose
--check answer means two different things is worse than two small ones. The
mechanism they do share -- checkout probe, change report, stamp -- is
`tools/vendor_common.py`.

WHAT STAYS OURS: `tools/import_p8.py` (the CLI) and `tools/p8_writer.py` (the
input guards a frozen opt=3 build needs, the `os.path.basename` shim, and the
compatibility report). Both DRIVE these two files; neither re-implements a line
of either, and neither writes a byte of a cart any more.
"""

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from tools.vendor_common import (copy_if_changed, open_spec, parse_args,  # noqa: E402
                                 report_changes, sha256, stamp)

TOOLS = os.path.join(ROOT, "tools")
MANIFEST = os.path.join(TOOLS, "p8_import_vendor.json")
SPEC_PROBE = "p8_import.py"

# {vendored name: path in moy-spec}. Explicit, like vendor_libmoy.py's table:
# what we execute should be a decision somebody made, not whatever a glob found.
VENDOR = {
    "p8_import.py": "p8_import.py",
    "p8_lua_port.py": "p8_lua_port.py",
}


def main(argv):
    args = parse_args(__doc__, argv)

    found = open_spec("vendor-p8-import", SPEC_PROBE, args.spec)
    if not found:
        return 2
    spec, commit, date, dirty = found

    changed, missing = [], []
    for name, rel in sorted(VENDOR.items()):
        src, dst = os.path.join(spec, rel), os.path.join(TOOLS, name)
        if not os.path.isfile(src):
            missing.append(rel)
            continue
        if copy_if_changed(src, dst, args.check):
            changed.append(os.path.relpath(dst, ROOT))

    code = report_changes(changed, missing, args.check)
    if code is not None:
        return code

    stamp(MANIFEST, commit, date, dirty,
          {("tools/" + name): sha256(os.path.join(TOOLS, name))
           for name in sorted(VENDOR)})
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
