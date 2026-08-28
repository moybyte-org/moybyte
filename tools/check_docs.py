#!/usr/bin/env python3
"""Hold the documents to the tree they describe.

    .venv/bin/python tools/check_docs.py     # or plain python3; stdlib only

CLAUDE.md is 10,000 words of specific claims and it is the first thing every
session reads, so a sentence that has gone stale in it does not mislead one
reader -- it misleads every future session until somebody notices. A review on
2026-08-08 found it saying six verbs were libmoy's when nine were (the reversal
was recorded in `native/moy_gfx/libmoy/UPSTREAM.md` the same day), pointing at
`tools/command_canvas.py` four commits after that file was deleted, and sending
readers to a vendor tree without saying it is untracked.

Two checks, both cheap, both for failures that actually happened:

  1. A backticked path with a slash in it resolves to something on disk.
  2. No two documents grow a shared paragraph. CLAUDE.md's own text says "See
     `runtime/README.md` for the per-file map; don't duplicate it" and "Read that
     dir's README before touching the P4" -- and it currently shares 118 runs of
     ten words with the six documents it points at. This check does not demand
     that be fixed; it PINS it, so the number can only come down.

The rule the second check encodes, which is the one worth remembering: this file
is a MAP. When it explains something a linked document already explains, the two
copies drift and the reader cannot tell which is current. Prefer one sentence of
orientation plus the pointer.
"""

import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROBLEMS = []


def tracked(*pats):
    out = subprocess.run(["git", "-C", ROOT, "ls-files"] + list(pats),
                         capture_output=True, text=True).stdout.split()
    return [p for p in out if os.path.isfile(os.path.join(ROOT, p))]


def read(rel):
    with open(os.path.join(ROOT, rel), encoding="utf-8", errors="replace") as f:
        return f.read()


def docs():
    """Every document that describes the CURRENT tree.

    docs/history/ is excluded by definition -- an archived plan describes a tree
    that no longer exists and is not wrong for it. AGENTS.md is a symlink to
    CLAUDE.md, so checking it would double every finding.
    """
    return [d for d in tracked("*.md")
            if not d.startswith("docs/history/") and d != "AGENTS.md"]


# --- 1. backticked paths resolve ---------------------------------------------
#
# Only paths with a slash: a bare `manifest.json` or `sounds.json` is a cart file
# by NAME, not a path to one particular file, and demanding those resolve would
# bury the real findings. Resolution is tried from the repo root, from the
# document's own directory (UPSTREAM.md files legitimately say `../foo`), and
# against docs/history/ for links into the archive.

# The leading `+` is the PLANNED MARKER: `+runtime/_bootstrap.py` means "a file
# this document proposes to create". It lives AT the reference rather than in a
# list over here, which is the whole point -- a central allowlist (this file
# carried one until 2026-08-14) puts the fact that a path is aspirational
# somewhere the reader of the document never looks.
PATH_RE = re.compile(
    r"`(\+?)([A-Za-z0-9_./-]*/[A-Za-z0-9_./-]+"
    r"\.(?:py|md|c|h|lua|json|sh|cmake|mk|yml|jsonl|service|patch|txt))`")

# The same check for a filename with NO directory in it -- `moy.py`, `lupa.py`,
# `canvas.py`. PATH_RE requires a slash, and that gap is exactly how a batch of
# stale references survived until 2026-08-28: `moy.py` was named as a live
# command four months after it was deleted, because nothing here could see a
# name without a directory. A bare name cannot be rooted, so it resolves
# against every BASENAME in the tree -- if no tracked file is called that, the
# document is naming something that does not exist. Restricted to `.py`: a
# bare `.c`/`.h` is almost always upstream MicroPython or ESP-IDF, which lives
# in the untracked .build/ clones and cannot be resolved here.
# Generated at build time -- real on a built tree, absent in a clean checkout.
GENERATED_RE = re.compile(r"^(_ota_build|carts_data|app_decls)\.py$")

# "deleted", "removed", "gone"... -- the vocabulary of a removal note.
BURIED_RE = re.compile(
    r"\b(delet|remov|retir|gone|no longer|used to|gits? histor|gitignor"
    r"|superseded|promoted|until |were all still|the fork's)", re.I)

BARE_RE = re.compile(
    r"`(\+?)([A-Za-z0-9_-]{3,}\.py)`")

# Where a relative path in a document may be rooted. The two board directories
# are here because CLAUDE.md says `native/moy_gfx/...` and `modules/moy_ota.py`
# throughout -- shorthand for "inside the firmware tree being discussed", which
# is idiomatic in that file and not worth expanding a hundred times.
ROOTS = ("", "firmware/lilygo_t_deck_plus_mainline",
         "firmware/esp32_p4_wifi6_touch_lcd_7b", "firmware/web_runner",
         "firmware/seeed_xiao_esp32s3_zero", "docs/history")

# Paths that are deliberately not in this repo. Each is a fact about the world,
# not a reference we can resolve -- and each is documented where it appears.
NOT_OURS = (
    "docs/issues/",          # the gitignored issue mirror (make sync-issues)
    "/sd/", "/moy/", "/moybyte/",   # paths ON A DEVICE's filesystem
    "firmware/lilygo_t_deck_plus_reference/",   # untracked vendor reference
    "firmware/reference_tulipcc/",              # ditto (THIRD_PARTY.md scope)
    "ports/webassembly/",    # upstream MicroPython's tree, not ours
    "boards/T-Deck.json",    # upstream LilyGO's repo
    "examples/UnitTest/", "examples/I2SPlay/", "examples/Keyboard_ESP32C3",
    "extmod/font_petme128_8x8.h",               # upstream MicroPython
    "modules/_ota_build.py", "native/.staged/",  # generated at build time
    "dist/", ".build/",
    "ports/celeste.moy",     # gitignored: CC BY-NC-SA, never committed
    "p8_lua_port.py",        # lives in the moy-spec repo, not here
    # The streaming web view, deleted 2026-08-12 (moycore plan 3.2 sunset), and
    # the recording stack that outlived it by a day (stage 4: the wasm head
    # rasterizes, so the recorder + the page's JS replayer went too). The plan,
    # CLAUDE.md and several READMEs name the dead files on purpose, to say they
    # are gone -- git history has them.
    "tools/web_console.py", "modules/device_webview.py", "device_webview.py",
    "runtime/web_view.py", "runtime/web_view_page.py", "tests/webharness.py",
    "tests/test_web_recording.py",
)


def _candidates(rel, p):
    """Every place `p` could legitimately be rooted, from document `rel`."""
    here = os.path.dirname(rel)
    out = [os.path.normpath(os.path.join(r, p)) for r in ROOTS]
    out.append(os.path.normpath(os.path.join(here, p)))
    # A doc inside a vendored directory says `vendor/foo.c` meaning its own
    # directory as the parent sees it.
    out.append(os.path.normpath(os.path.join(os.path.dirname(here), p)))
    out.append(os.path.join("docs/history", os.path.basename(p)))
    return out


_BASENAMES = []


def _basenames():
    """Every tracked file's basename, for resolving a name with no directory."""
    if not _BASENAMES:
        out = subprocess.run(["git", "ls-files"], cwd=ROOT,
                             capture_output=True, text=True)
        _BASENAMES.append({os.path.basename(l) for l in
                           out.stdout.splitlines() if l})
    return _BASENAMES[0]


def check_paths():
    for rel in docs():
        lines = read(rel).splitlines()
        for i, line in enumerate(lines, 1):
            for m in PATH_RE.finditer(line):
                planned, p = m.group(1), m.group(2)
                if any(t in p for t in NOT_OURS) or "*" in p or "..." in p:
                    continue
                if planned:
                    # A document naming the file it intends to create is not
                    # stale, it is doing its job -- so a missing one is fine.
                    # But the marker has to EXPIRE, or "planned" quietly becomes
                    # a lie about shipped code: once the file lands, say so.
                    if any(os.path.exists(os.path.join(ROOT, c))
                           for c in _candidates(rel, p)):
                        PROBLEMS.append(
                            "%s:%d marks %s as planned (+), but it exists now "
                            "-- drop the +" % (rel, i, p))
                    continue
                # A two-letter stem is an attribute, not a file: `cv.w/cv.h` is
                # a canvas's width and height, and the regex cannot know that.
                if len(os.path.basename(p).split(".")[0]) < 3:
                    continue
                cands = _candidates(rel, p)
                if any(os.path.exists(os.path.join(ROOT, c)) for c in cands):
                    continue
                # If the LEADING directory does not exist anywhere either, this
                # is prose that happens to contain slashes -- runtime/README.md
                # lists sibling modules as `editors_base/_code/_sheet/...`, which
                # is six filenames, not one path. Only complain when the
                # directory is real and the file inside it is not, which is what
                # a deleted or renamed module actually looks like.
                lead = p.split("/")[0]
                if not any(os.path.isdir(os.path.join(ROOT, c))
                           for c in _candidates(rel, lead)):
                    continue
                PROBLEMS.append("%s:%d references %s, which is not in the tree"
                                % (rel, i, p))
            # A removal note wraps, and in either direction: the names sit on
            # one line and "was removed" on the next, or the qualifier trails a
            # sentence or two later. So the window is the line plus a couple
            # each side rather than the line alone.
            context = " ".join(lines[max(0, i - 3):i + 2])
            for m in BARE_RE.finditer(line):
                planned, name = m.group(1), m.group(2)
                if planned or any(t in name for t in NOT_OURS):
                    continue
                if name in _basenames() or GENERATED_RE.search(name):
                    continue
                # A document naming a file in order to say it is GONE is doing
                # its job -- that is the passing mention a removal is supposed
                # to get. Only an unqualified reference is a stale claim.
                if BURIED_RE.search(context):
                    continue
                PROBLEMS.append(
                    "%s:%d names %s as if it exists; no file in the tree is "
                    "called that" % (rel, i, name))


# --- 2. the duplication ratchet ----------------------------------------------
#
# Measured 2026-08-08 and pinned. Lower a number when you fold a copy away;
# raising one should be rare and should say why in the commit. The licence pair
# is exempt: two licence texts share their boilerplate by law, not by accident.

SHINGLE = 10
DUP_BUDGET = {
    ("CLAUDE.md", "docs/shell_ux_v1.md"): 31,
    ("CLAUDE.md", "runtime/README.md"): 27,
    ("CLAUDE.md", "moybyte_console_plan_2026-07.md"): 18,
    ("CLAUDE.md",
     "native/moy_audio/libmoy/UPSTREAM.md"): 16,
    ("CLAUDE.md", "firmware/esp32_p4_wifi6_touch_lcd_7b/README.md"): 11,
    ("CLAUDE.md", "firmware/lilygo_t_deck_plus_mainline/README.md"): 10,
    ("CLAUDE.md", "docs/perf_native_gap_v1.md"): 5,
    ("docs/moy_cart_api.md", "runtime/README.md"): 14,
    ("CONTRIBUTING.md", "README.md"): 12,
    ("LICENSE.md", "README.md"): 7,
    ("docs/backend_contract_v1.md", "docs/surface_model_v1.md"): 8,
    # Each vendored directory's UPSTREAM.md must stand alone beside its code, so
    # they share the vendoring rules deliberately.
    ("native/moy_audio/libmoy/UPSTREAM.md",
     "native/moy_gfx/libmoy/UPSTREAM.md"): 21,
    ("experiments/lua_bridge/components/lua/MODIFICATIONS.md",
     "native/moy_lua/lua/MODIFICATIONS.md"): 8,
}
DUP_FLOOR = 4
DUP_EXEMPT_DIRS = ("LICENSES/",)


def check_prose_duplication():
    import collections
    pool = [d for d in docs()
            if not d.startswith(DUP_EXEMPT_DIRS) and not d.endswith("THIRD_PARTY.md")]
    seen = collections.defaultdict(set)
    for rel in pool:
        text = re.sub(r"```.*?```", "", read(rel), flags=re.S)
        text = re.sub(r"[`*_#|>]", "", " ".join(text.split()))
        for sentence in re.split(r"(?<=[.!?])\s+", text):
            words = [w.lower().strip(",;:()—-") for w in sentence.split()]
            if len(words) < SHINGLE:
                continue
            for i in range(len(words) - SHINGLE + 1):
                seen[" ".join(words[i:i + SHINGLE])].add(rel)
    counts = collections.Counter()
    for holders in seen.values():
        if len(holders) > 1:
            ordered = sorted(holders)
            for i, a in enumerate(ordered):
                for b in ordered[i + 1:]:
                    counts[(a, b)] += 1
    for pair, n in sorted(counts.items()):
        cap = DUP_BUDGET.get(pair, DUP_FLOOR)
        if n > cap:
            PROBLEMS.append(
                "%s and %s share %d runs of %d words (pinned at %d). Fold one "
                "copy into a pointer, or raise the pin with a reason."
                % (pair[0], pair[1], n, SHINGLE, cap))


def main():
    check_paths()
    check_prose_duplication()
    if PROBLEMS:
        print("check_docs: %d problem%s"
              % (len(PROBLEMS), "" if len(PROBLEMS) == 1 else "s"))
        for p in PROBLEMS:
            print("  " + p)
        return 1
    print("check_docs: the documents match the tree, and no copy has grown")
    return 0


if __name__ == "__main__":
    sys.exit(main())
