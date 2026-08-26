#!/usr/bin/env python3
"""Re-vendor the PICO-8 asset converter from a moy-spec checkout.

    make vendor-p8-import                          # ../moy-spec beside this repo
    make vendor-p8-import SPEC=/path/to/moy-spec
    python3 tools/vendor_p8_import.py --check      # what would change, touch nothing

`p8_import.py` is moy-spec's `.p8`/`.p8.png` -> moy asset converter: sheet, SFX
bank and music tracks. It lives upstream because SPEC.md is what says what the
output must mean -- 8.1 pins `57 = A4 = 440 Hz`, which is what fixes PICO-8's
pitch offset at 24, and 8.1's keyed rest is what makes a ported slide glide from
the right note. libmoy's synth implements the other end of that same contract
(`moy_audio.c`: `p8key = pitch - 24.0f`), and it is vendored here too, so the
converter and the synth must come from ONE upstream version or they agree by
luck.

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

WHAT STAYS OURS: `tools/import_p8.py`, the moybyte driver on top -- the CLI, the
`.moy` folder writer, the guided PICO-8 -> Python port notes (#36). It imports
the converter from here; it never re-implements a line of it.
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
