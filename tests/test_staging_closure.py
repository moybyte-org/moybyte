"""Every module a target freezes must be able to IMPORT on that target.

This is `docs/backend_contract_v1.md` §8's never-built prerequisite, and it
exists because the tree has shipped this bug more than once in the same
quarter:

  * The 2026-08 streaming sunset dropped `web_view_ws.py` from the P4's staging
    list (06506ab). Three days later the web console re-added its DEPENDENT,
    `moy_webserver.py`, without it (a0a9d21). `moy_webserver` imports
    `web_view_ws` through a two-branch ladder whose other branch is
    `from runtime import ...` -- and there is no `runtime` package on device,
    so BOTH branches fail, `import moy_webhost` raises, the caller catches it,
    and the WEB CONSOLE row silently does not exist. Nobody saw it because
    `modules/` is gitignored and never cleaned, so every developer's board kept
    running a pre-sunset copy; a fresh clone would have lost the feature.

  * `runtime/palette.py` imports CPython's `colorsys` at module scope. The web
    runner hit this and solved it by GENERATING a literal twin
    (web_runner/build.sh, "which needs CPython colorsys"). Both boards staged the
    raw file, so `import palette` -- and therefore `import canvas`, its only
    device consumer -- could not succeed there. (Neither file reaches a board
    now, and `runtime/canvas.py` is deleted outright.)

The shape of the defect is always the same: an import that fails on device
turns into a MISSING FEATURE rather than a crash, because the callers are
guarded and the UI is capability-gated. Nothing goes red. So the frozen set is
derived from what a FRESH build produces -- every target's `board.toml`
(#161 Phase 3 for the boards, 2026-08-17 for the web runner) -- and never
from `modules/` on disk, which is precisely where the staleness hides.

The rule is deliberately weak enough not to be noisy: for each import site, at
least ONE branch of its try/except-ImportError ladder must resolve. That accepts
both ladder orderings -- most shared modules put the bare device name in `try`
and `from runtime import ...` in `except`, `moy_webserver.py` does the opposite
-- while still failing when no branch can possibly work.

What it CANNOT see is a ladder whose branches all resolve on the tier running
the test and none of them on device, which is how the wallpaper preview stayed
black on both boards (#31): `runtime.host_canvas` then `web_canvas`, host-green
and board-dead, no import site to complain about because the whole ladder was
the bug. That one is gone -- the preview asks `ws.make_game_canvas`, an injected
service, and tests/test_wallpaper_preview.py pins the absence of the ladder --
but the blind spot is the reason this file is not the only net.
"""

import ast
import re
import subprocess
from pathlib import Path

import pytest

from tools import board_config

ROOT = Path(__file__).resolve().parent.parent
TDECK = ROOT / "firmware" / "lilygo_t_deck_plus_mainline"
# One T-Deck target since the fork's deletion (2026-08-17). The alias survives
# because half this file's assertions were written against the mainline port by
# name, and a rename would churn them for nothing.
TDECK_MAINLINE = TDECK
P4 = ROOT / "firmware" / "esp32_p4_wifi6_touch_lcd_7b"
GUITION = ROOT / "firmware" / "guition_jc3248w535"
ZERO = ROOT / "firmware" / "seeed_xiao_esp32s3_zero"
WEB = ROOT / "firmware" / "web_runner"
BOARD_DIR = {"tdeck-mainline": TDECK_MAINLINE, "p4": P4,
             "guition-s3": GUITION, "zero": ZERO}

# What MicroPython itself provides. Explicit and module-level on purpose: adding
# a name here is a visible diff that says "the port supplies this", which is a
# claim someone can check, rather than a silently widening regex.
MICROPYTHON_BUILTINS = {
    "array", "binascii", "builtins", "cmath", "collections", "errno", "gc",
    "hashlib", "heapq", "io", "json", "math", "os", "platform", "random", "re",
    "select", "socket", "ssl", "struct", "sys", "time",
    # NOT `zlib`: MicroPython dropped it in v1.21 for `deflate`, and this table
    # said otherwise until 2026-08-29 -- a false entry in a list whose whole
    # purpose is claims somebody can check. The web target reaches zlib through
    # firmware/web_runner/shims/zlib.py, which it STAGES as a module (below);
    # any other target importing it would now be a finding, which is right.
    # MicroPython-specific
    "machine", "micropython", "network", "esp", "esp32", "bluetooth",
    "framebuf", "neopixel", "uctypes", "_thread", "uasyncio", "asyncio",
    "vfs", "deflate",
}

# Native usermods compiled into a given target (USER_C_MODULES / ext_mod).
NATIVE = {
    # The same shared usermods, plus this board's own panel backend -- and
    # NONE of the fork's lvgl/lcd_bus family, which is the point of the port.
    # moy_flush is the odd member: it registers no MicroPython module at all
    # (it is the banded-flush engine moy_lcd and moy_axs link), but it is
    # STAGED like one, so it is declared like one.
    "tdeck-mainline": {"moy_gfx", "moy_alloc", "moy_sd", "moy_audio", "moy_lua",
                       "moycore", "moy_web", "moy_flush", "moy_lcd"},
    # The P4 has no banded flush to feed -- DPI scans PSRAM continuously -- so
    # it denies moy_flush along with moy_sd and moy_audio.
    # moy_c6 is the ESP-NOW-over-hosted shim + C6 plumbing (#7, the espnow
    # track -- docs/history/espnow_p4_2026-08.md).
    "p4": {"moy_gfx", "moy_alloc", "moy_lua", "moycore", "moy_web", "moy_dsi",
           "moy_ppa", "moy_ble_hid", "moy_c6"},
    # The Guition denies moy_sd + moy_audio for now (stage 4/5 of its bring-up,
    # see its board.toml); moy_axs is its board-authored QSPI panel backend,
    # and moy_flush is the engine under it.
    "guition-s3": {"moy_gfx", "moy_alloc", "moy_lua", "moycore", "moy_web",
                   "moy_flush", "moy_axs"},
    # The Zero is HEADLESS (#41): no panel, no touch, no frame loop, no carts
    # running on it. `moy_web` is the only shared C module it compiles in, and
    # it is the module that justifies the board having an image at all -- the
    # browser console rides the firmware so the page a board serves cannot
    # drift behind the board serving it. The other seven are denied in its
    # board.toml, each with the hardware or the workload that is missing.
    "zero": {"moy_web"},
    "web": {"moy_gfx", "moy_lua", "moy_audio", "moycore", "js", "jsffi"},
}

# Host-only modules that must NEVER reach a given target: staging one is the
# mirror-image bug, a module that imports fine and then cannot work. Per target
# and not global, because `host_api` is genuinely the WEB head's cart API
# (web_boot.py imports it by name) while being meaningless on a board.

# The only .py files in firmware/web_runner/ that must NOT be frozen: host dev
# tools with no place in the image. Everything else there is an authored module
# and ships -- see the derivation below, and why it is a derivation.
WEB_HOST_ONLY = frozenset({"serve", "moy"})
#
# The boards' rows used to be four names shorter than the web's -- no
# `gfx_binding`, `native_build`, `host_canvas` or `input` -- which is to say the
# tripwire was WEAKER on the two targets that actually ship in a kid's hands, for
# no recorded reason. Found 2026-08-15 by cross-checking this table against the
# boards' new `board.toml` denials (#161 Phase 3), which is what that check is
# for: two statements of one truth are only worth having if something compares
# them.
HOST_ONLY = {
    "tdeck-mainline": {"host_app", "host_api", "host_canvas", "lua_host",
                       "input", "audio_binding", "lua_binding", "gfx_binding",
                       "native_build", "simulate_desktop"},
    "p4": {"host_app", "host_api", "host_canvas", "lua_host", "input",
           "audio_binding", "lua_binding", "gfx_binding", "native_build",
           "simulate_desktop"},
    "guition-s3": {"host_app", "host_api", "host_canvas", "lua_host", "input",
                   "audio_binding", "lua_binding", "gfx_binding",
                   "native_build", "simulate_desktop"},
    # Same list as the console boards, and it is worth having even though the
    # Zero ALLOWLISTS `runtime/` and could not stage one of these by omission:
    # the failure this catches is somebody adding a name to a group, and a
    # tripwire that only works on boards with denylists is a tripwire that
    # stops working the moment a second allowlist board appears.
    "zero": {"host_app", "host_api", "host_canvas", "lua_host", "input",
             "audio_binding", "lua_binding", "gfx_binding",
             "native_build", "simulate_desktop"},
    # The browser reaches libmoy through its compiled-in usermods, so every
    # ctypes/subprocess host binding is dead weight there -- and gfx_binding is
    # the one that would look most plausible to stage, because it is the host's
    # half of the very module device_canvas imports.
    "web": {"host_app", "lua_host", "simulate_desktop",
            "audio_binding", "lua_binding",
            "gfx_binding", "native_build", "host_canvas"},
}

# Modules a build GENERATES into the frozen tree. They are not in git and not
# staged by a `cp`, so the extraction cannot see them -- but they are real, and
# leaving them out would report every importer of them as broken.
GENERATED = {
    "tdeck-mainline": {"carts_data", "_ota_build"},
    "p4": {"carts_data", "_ota_build"},
    "guition-s3": {"carts_data", "_ota_build"},
    # `carts_data` too since 2026-08-30, and it is a DIFFERENT file here: the
    # console boards freeze the plain `CARTS = [...]` (732 KB of source, the
    # fallback for a missing card), and this board freezes the PACKED form
    # (`CARTS_Z`, 202 KB of raw-deflate blobs) that `zero_host.seed_carts`
    # inflates into an empty store on first boot. It did not carry one at all
    # until that day -- the plain roster left 51 KB of a 2.8 MB slot, under the
    # #168 warning floor -- so a flashed Zero came up an empty console and its
    # carts arrived over a USB cable, or never.
    "zero": {"carts_data", "_ota_build"},
    # The web GENERATES its palette (a literal twin of runtime/palette.py, which
    # needs CPython colorsys) -- which is exactly the fix the boards lack below.
    "web": {"palette"},
}

# Directory-shaped modules: a package staged wholesale rather than file by file.
# The Zero's is EMPTY and is the only board's that is: `moybyte/` is the boards'
# real InputState (plus the T-Deck keyboard matrix and the #69 poller), and the
# other three freeze it whole because they all construct one. This board never
# does -- its inputs arrive as HTTP requests.
PACKAGES = {"tdeck-mainline": {"moybyte"}, "p4": {"moybyte"},
            "guition-s3": {"moybyte"}, "zero": set(), "web": set()}

# Real, reproduced defects that are not fixed here because the FIX is a decision
# someone has to make, not a line someone forgot. An entry is a tracked gap, not
# a pass -- and `test_no_known_gap_has_quietly_been_fixed` deletes the excuse the
# moment it stops being true, so this cannot rot into a dumping ground.
#
# It is empty, and the first entry it ever held is the reason the table exists:
# `palette -> colorsys` on both boards, which turned out not to be a staging slip
# but a feature that had never worked, and whose fix was a product call rather
# than a missing line. Resolved 2026-08-15 by removing canvas.py + palette.py
# from both boards (see NEVER_ON_A_BOARD below).
KNOWN_GAPS = {}

# The mirror of GENERATED: modules that must NOT reach a board, by name, with
# the reason. A denylist entry in a build script is easy to undo by accident --
# somebody adds a module to a staging list and nothing objects -- so the claim
# lives here too, where undoing it is a failing test rather than a silent
# re-import of a file that cannot load.
NEVER_ON_A_BOARD = {
    "canvas": (
        "the pure-Python indexed raster, DELETED 2026-08-15 -- the host draws "
        "on device_canvas.DeviceCanvas like everything else, so the file this "
        "names does not exist to stage. Its only device consumer had been "
        "wallpaper._ensure_preview, which could never import it there anyway "
        "(see `palette`). Kept as a tripwire: the preview it was wanted for "
        "renders on the boards' OWN raster now (#31), so a second one in "
        "frozen flash would buy nothing and should not come back."),
    "palette": (
        "builds indices 16-63 with CPython's `colorsys` at IMPORT time, and "
        "MicroPython has none. VERIFIED ON P4 GLASS 2026-08-15, on firmware "
        "built before this file was unstaged: `import colorsys`, `import "
        "palette` and `import canvas` all raise ImportError('no module named "
        "colorsys'), and the consequence was visible one level up -- "
        "wallpaper._ensure_preview() returned False and _static_preview() "
        "returned None, so the Appearance screen's cart-wallpaper panel drew "
        "its black fill and nothing else. Staging this means a module that "
        "raises on import, which reads as a missing FEATURE rather than a "
        "crash because every caller is guarded -- and that panel is exactly "
        "how long such a thing survives unreported. (The preview itself is "
        "FIXED, #31: it renders on device_canvas through the backend's own "
        "make_game_canvas, needing neither of these two files.) The web head "
        "needs the same table and GENERATES a literal twin instead; a board "
        "that ever needs MOY64 should do the same, never stage this file."),
}


# -- deriving each target's frozen set ---------------------------------------


def _tracked(modules_dir):
    """Board-authored modules: tracked in git, directly under the board's
    modules/.

    Package members (`modules/moybyte/input.py`) are skipped: their importable
    name is `moybyte.input`, so keying them by basename would invent top-level
    `input` and `__init__` modules and then report them as strays. PACKAGES
    carries the package itself.
    """
    rel = modules_dir.relative_to(ROOT)
    out = subprocess.run(["git", "ls-files", str(rel)],
                         cwd=ROOT, capture_output=True, text=True).stdout
    found = {}
    for line in out.split():
        if not line.endswith(".py"):
            continue
        tail = Path(line).relative_to(rel)
        if len(tail.parts) > 1:            # inside a package
            continue
        found[tail.stem] = ROOT / line
    return found


def frozen_set(target):
    """The modules a FRESH build of `target` freezes, and where each comes from.

    Deliberately NOT `os.listdir(modules/)`: that directory is gitignored and
    never cleaned, so it still holds files that no build produces any more --
    which is the precise blind spot that let the P4 web console break with
    every developer's board still working.
    """
    if target == "web":
        # The web runner DECLARES its staging too now (board.toml -- the last
        # hand-rolled DENY list to convert, 2026-08-17; the boards went in
        # #161 Phase 3). The shell extractors that used to parse its build.sh
        # died with the inline list.
        mods = {Path(name).stem: path for name, path
                in board_config.staged_modules(WEB, ROOT).items()}
        # The runner's AUTHORED modules, DERIVED from the directory rather
        # than listed. A hand-written copy of this list lived here and another
        # in build.sh, and they were wrong in the SAME WAY: `update_link.py`
        # was in neither, so it was never staged, never frozen, and
        # web_boot.update_enable raised ImportError on every build for as long
        # as the feature existed -- a headless Zero with no update row, behind
        # two green lists. Anything ending .py that is not a host-only dev tool
        # ships, so a new module cannot be forgotten in one place or both.
        for p in sorted(WEB.glob("*.py")):
            if p.stem in WEB_HOST_ONLY:
                continue
            mods[p.stem] = p
        # The p8 importer (#194) is the one thing this build stages out of
        # `tools/`: moy-spec's two vendored files -- the asset converter and the
        # Lua porter that makes a dropped cart RUN -- and the guards/report file
        # the CLI shares with them, carried in UNCHANGED (editing a vendored
        # file is a red test). Plus the `zlib.decompress` shim over
        # MicroPython's `deflate`, which is what lets the `.p8.png` inflate
        # happen in the SAME converter the desktop runs instead of a second
        # reader in JS.
        for name, src in (("p8_import", ROOT / "tools" / "p8_import.py"),
                          ("p8_lua_port", ROOT / "tools" / "p8_lua_port.py"),
                          ("p8_writer", ROOT / "tools" / "p8_writer.py"),
                          ("zlib", WEB / "shims" / "zlib.py")):
            if src.exists():
                mods[name] = src
    else:
        # The boards DECLARE their staging (#161 Phase 3): board.toml holds
        # the denylist over runtime/ and the allowlist over the shared device
        # tree, and build.sh does nothing but call the stager. This reads the
        # declaration rather than re-deriving it from shell syntax -- one
        # source for what a build produces, on every target now.
        board = BOARD_DIR[target]
        mods = dict(_tracked(board / "modules"))
        mods.update({Path(name).stem: path for name, path
                     in board_config.staged_modules(board, ROOT).items()})
    for name in GENERATED[target] | PACKAGES[target]:
        mods.setdefault(name, None)        # real, but with no single source file
    return mods


# -- import extraction --------------------------------------------------------


def _base(name):
    return (name or "").split(".")[0]


def _imports_of(node):
    """Every module a single statement could pull in, as base names."""
    out = set()
    for n in ast.walk(node):
        if isinstance(n, ast.Import):
            out |= {_base(a.name) for a in n.names}
        elif isinstance(n, ast.ImportFrom):
            if n.level:                    # `from . import x` -- package lane
                out.add("__relative__")
            elif n.module:
                out.add(_base(n.module))
    return out


def _swallows(handler):
    """True when this handler treats the import's ABSENCE as a supported state.

    This is the distinction the whole test turns on. Two shapes look identical
    to a grep and could not be more different:

        try: import moy_gfx                 # a PROBE. The board may not have
        except ImportError: moy_gfx = None  # it; the caller checks. Fine.

        try: import web_view_ws             # a LADDER. Two ways to reach ONE
        except ImportError:                 # module, and if neither works the
            from runtime import web_view_ws # importer is simply broken.

    A handler that imports is offering another route; a handler that assigns a
    fallback, passes, or returns is accepting the loss. Only the former makes
    its module mandatory.
    """
    if any(isinstance(n, (ast.Import, ast.ImportFrom))
           for n in ast.walk(handler)):
        return False
    return not any(isinstance(n, ast.Raise) for n in ast.walk(handler))


def import_groups(src, path):
    """Alternative-groups: each group is satisfied if ANY member resolves.

    A try/except-ImportError forms ONE group across its body and ALL of its
    handlers, however deeply the ladder nests -- `canvas.py` is three levels
    deep, and treating each level as its own group reports the inner rungs as
    unreachable when the outer one already resolved. A group whose chain
    swallows anywhere is dropped: absence is supported there.
    """
    tree = ast.parse(src, filename=str(path))
    groups, covered = [], set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Try) or id(node) in covered:
            continue
        if not any(h.type is None or "ImportError" in ast.dump(h.type)
                   or "Exception" in ast.dump(h.type) for h in node.handlers):
            continue
        # Claim the whole nested chain so inner rungs never form their own group.
        handlers, alts = [], set()
        for n in ast.walk(node):
            if isinstance(n, ast.Try):
                covered.add(id(n))
                handlers.extend(n.handlers)
            elif isinstance(n, (ast.Import, ast.ImportFrom)):
                covered.add(id(n))
        alts |= _imports_of(node)
        if not alts or any(_swallows(h) for h in handlers):
            continue
        groups.append(alts)
    for n in ast.walk(tree):
        if isinstance(n, (ast.Import, ast.ImportFrom)) and id(n) not in covered:
            got = _imports_of(n)
            if got:
                groups.append(got)
    return groups


TARGETS = ("tdeck-mainline", "p4", "guition-s3", "zero", "web")

# The floor each target's extraction must clear -- a smoke test on the
# EXTRACTION, not on the board: a parser that silently found nothing would
# otherwise pass every check below by having nothing to check. One number for
# the consoles (they freeze the whole shared tree) and a much smaller one for
# the Zero, whose thirteen-module allowlist is the point of it. A specific
# number rather than `> 0`, so the Zero losing half its set still fails here.
MIN_MODULES = {"zero": 12}
DEFAULT_MIN_MODULES = 40


def _unresolved(target):
    """[(module, path, missing-alternatives)] for `target`, gaps included."""
    mods = frozen_set(target)
    available = set(mods) | MICROPYTHON_BUILTINS | NATIVE[target]
    # Imports the board has DECLARED unreachable (board.toml's
    # [[modules.shared.lazy]]). Exactly one exists: `moy_carts.save_blocks`
    # reaches the block compiler through a real try/except ladder, so this
    # suite calls it mandatory and is right to on any board that can reach that
    # function -- and its only callers are the block editor's UI and the Editor
    # app, neither of which the Zero freezes. Read from the board file rather
    # than listed here, so the reason lives beside the declaration and adding
    # one is a visible diff in a board's own file (#161).
    if target in BOARD_DIR:
        available |= set(board_config.lazy_imports(BOARD_DIR[target]))
    out = []
    for name, path in sorted(mods.items()):
        if path is None:                   # generated / package: no one source
            continue
        try:
            src = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            continue
        for group in import_groups(src, path):
            # A relative import is the host PACKAGE lane. It never resolves on a
            # flat device tree, but it is a legitimate rung of a ladder.
            if group & available or group == {"__relative__"}:
                continue
            out.append((name, path, group))
    return out


@pytest.mark.parametrize("target", TARGETS)
def test_every_frozen_module_can_import_on_its_target(target):
    mods = frozen_set(target)
    floor = MIN_MODULES.get(target, DEFAULT_MIN_MODULES)
    assert len(mods) >= floor, (
        "%s: staging extraction found only %d modules (floor %d)"
        % (target, len(mods), floor))
    failures = ["%s (%s) -> %s" % (name, path.relative_to(ROOT),
                                   ", ".join(sorted(group)))
                for name, path, group in _unresolved(target)
                if (target, name) not in KNOWN_GAPS]
    assert not failures, (
        "%s: frozen modules whose imports cannot resolve on that target:\n  %s"
        % (target, "\n  ".join(failures)))


def test_no_known_gap_has_quietly_been_fixed():
    """The ratchet that stops KNOWN_GAPS becoming a place to hide things.

    Every entry must still reproduce. When one stops failing, this test says so
    and the entry -- with its paragraph of reasoning -- gets deleted rather than
    quietly outliving the defect it described.
    """
    live = {(t, name) for t in TARGETS for name, _, _ in _unresolved(t)}
    stale = sorted(set(KNOWN_GAPS) - live)
    assert not stale, (
        "KNOWN_GAPS entries that no longer reproduce -- delete them: %s" % stale)


BOARDS = ("tdeck-mainline", "p4", "guition-s3", "zero")
# The boards whose `[modules.shared]` is a DENYLIST. The Zero's is an
# allowlist, on the argument its board.toml makes in full: a denylist is right
# when the source tree's default answer is YES, and `runtime/` IS the console,
# which that board is not. The two checks below read denials and would pass
# vacuously there, so they name this tuple instead of BOARDS and the Zero gets
# its own pair further down -- an allowlist's failure mode is the opposite one
# and needs the opposite test.
DENYLIST_BOARDS = ("tdeck-mainline", "p4", "guition-s3")


@pytest.mark.parametrize("target", BOARDS)
def test_modules_that_cannot_load_on_a_board_are_not_frozen_onto_one(target):
    """A module that RAISES on import is worse than one that is missing.

    Every caller in this tree is guarded, so the failure surfaces as a feature
    that quietly does not exist -- which is how the wallpaper preview stayed
    black on both boards for as long as it has. Keeping the claim in a test as
    well as in the build script means re-adding one of these is a red run and
    not a silent re-import.
    """
    frozen = set(frozen_set(target))
    for name, why in sorted(NEVER_ON_A_BOARD.items()):
        assert name not in frozen, "%s freezes %s -- %s" % (target, name, why)


@pytest.mark.parametrize("target", TARGETS)
def test_no_host_only_module_is_frozen_onto_a_target(target):
    """The mirror-image bug: a module that imports cleanly and then cannot
    work, because it needs a compiler, ctypes, lupa, or a subprocess."""
    leaked = sorted(set(frozen_set(target)) & HOST_ONLY[target])
    assert not leaked, "%s freezes host-only modules: %s" % (target, leaked)


# -- the tables above vs. the board files ------------------------------------
#
# `board.toml` and the two tables above describe the same truth twice, and the
# obvious move -- derive the tables from the board files -- is WRONG, so the
# reason is recorded rather than rediscovered.
#
# `HOST_ONLY` and `NEVER_ON_A_BOARD` are tripwires: they exist to go red when
# somebody REMOVES a denial. Derive them from the denials and removing a denial
# removes its own assertion, and the tripwire fires never. So the names stay
# stated independently here, the PROSE lives once (in the board file, beside
# the line that encodes it, per #161), and the two are checked against each
# other in both directions below. Disagreement is the finding either way: a
# board that stopped denying something the policy forbids, or a board denying
# something for a reason nobody wrote down here.


def _kinds(target):
    """{filename: kind} from a board's declared denials."""
    out = {}
    for name, entry in board_config.denials(BOARD_DIR[target]).items():
        out[name] = entry.get("kind", "")
    return out


@pytest.mark.parametrize("target", DENYLIST_BOARDS)
def test_board_toml_denies_everything_the_policy_tables_forbid(target):
    declared = _kinds(target)
    # HOST_ONLY carries names that are not runtime modules at all
    # (`simulate_desktop` is a tool); only the ones a denylist could stage are
    # the board file's business.
    runtime_names = {p.stem for p in (ROOT / "runtime").glob("*.py")}
    for name in sorted(HOST_ONLY[target] & runtime_names):
        assert declared.get(name + ".py") == "host-only", (
            "%s/board.toml does not deny %s.py as host-only -- the policy "
            "table in this file says it must never be frozen there" % (target, name))
    for name in sorted(NEVER_ON_A_BOARD):
        assert name + ".py" in declared, (
            "%s/board.toml does not name %s.py at all. NEVER_ON_A_BOARD wants "
            "it denied WITH its reason, and a denylist may name a file that no "
            "longer exists -- that is what makes it a tripwire" % (target, name))


@pytest.mark.parametrize("target", DENYLIST_BOARDS)
def test_every_board_toml_denial_is_explained_and_classified(target):
    """#161: the prose rationale moves WITH the data. An unexplained denial is
    the state this phase existed to leave behind -- a staging list whose
    exclusions were invisible because they were silence."""
    kinds = {"host-only", "never-on-a-board", "tier", "no-consumer"}
    for name, entry in sorted(board_config.denials(BOARD_DIR[target]).items()):
        assert entry.get("kind") in kinds, (
            "%s/board.toml denies %s with kind=%r; expected one of %s"
            % (target, name, entry.get("kind"), sorted(kinds)))
        assert len(entry.get("why", "").split()) >= 8, (
            "%s/board.toml denies %s without saying why" % (target, name))
        if entry["kind"] == "host-only":
            assert Path(name).stem in HOST_ONLY[target], (
                "%s/board.toml calls %s host-only but HOST_ONLY in this file "
                "does not -- one of the two is wrong" % (target, name))
        if entry["kind"] == "never-on-a-board":
            assert Path(name).stem in NEVER_ON_A_BOARD, (
                "%s/board.toml classes %s never-on-a-board but this file's "
                "table does not carry it" % (target, name))
        if entry["kind"] != "never-on-a-board":
            assert (ROOT / "runtime" / name).exists(), (
                "%s/board.toml denies %s, which is not a runtime module. Only "
                "a never-on-a-board TRIPWIRE may name a file that is not there"
                % (target, name))


# -- the allowlist half (the Zero, 2026-08-29) --------------------------------
#
# The two checks above are a denylist's contract: every EXCLUSION is explained.
# An allowlist's contract is the mirror image -- every INCLUSION is explained,
# because there the silent failure is a module that quietly stopped being
# staged, not one that quietly started. Both are the same #161 rule ("the
# rationale moves with the data") applied to the shape the board actually has.


def test_the_zero_declares_its_staging_shape_and_the_others_keep_theirs():
    """Which shape a board uses is a CLAIM ABOUT THE BOARD, and it is declared.

    `runtime/` is the console. A console board denies by exception because the
    default answer there is yes; the Zero has no console, so its default answer
    is no and forty denials saying "there is no console" would be noise with a
    hole in it (the forty-first module nobody adds). Pinned in both directions
    so a board silently changing shape -- which would silently change what it
    freezes -- is a failing test rather than a diff nobody reads.
    """
    for target in DENYLIST_BOARDS:
        assert board_config.shared_strategy(BOARD_DIR[target]) == "denylist", (
            "%s stopped denying by exception -- read its board.toml diff"
            % target)
    assert board_config.shared_strategy(ZERO) == "allowlist"


def test_every_zero_staging_group_says_what_the_board_does_with_it():
    """An allowlist entry with no reason is a list somebody will prune wrongly.

    Same eight-word floor the denials get, applied to the groups -- and applied
    to the DEVICE allowlist too, which is the shape every board already uses
    and which nothing checked before this board arrived with only four device
    modules to its name.
    """
    cfg = board_config.load(ZERO)
    shared = cfg["modules"]["shared"]
    groups = shared.get("group", [])
    assert groups, "the Zero's [modules.shared] allowlist has no groups"
    for section, entries in (("shared", groups),
                             ("device", cfg["modules"]["device"]["group"])):
        for entry in entries:
            assert entry.get("files"), "an empty %s group" % section
            assert len(entry.get("why", "").split()) >= 8, (
                "the Zero stages %s with no reason recorded"
                % ", ".join(entry["files"]))
    for entry in shared.get("lazy", []):
        assert len(entry.get("why", "").split()) >= 8, (
            "the Zero declares %s lazy with no reason" % entry.get("module"))


def test_the_zero_stages_the_sync_stack_and_nothing_that_draws():
    """The board's whole identity, as an invariant.

    The positive half is the owner call of 2026-08-25 ("the Zero supports all
    features"): before it, the board carried the minimum that BOOTS, which meant
    `moy_sync.file_kinds()` found no store module and answered None -- read as
    "refuse" everywhere, so the board 404'd /files.json and the kid's drawings
    could not travel. Every name below is a feature, not a dependency detail.

    The negative half is what makes two OTA slots fit in 8MB: a module that
    draws is a module this board pays for twice and never calls.
    """
    staged = set(board_config.staged_modules(ZERO, ROOT))
    for name in ("moy_sync.py", "moy_fs.py",          # the 3.4 RPC
                 "moy_carts.py", "moy_image.py",      # #108 files sync
                 "moy_journal.py",                    # the store of record
                 "web_view_ws.py", "ticks.py",        # the transport's leaves
                 "moy_webserver.py", "moy_webhost.py",
                 "moy_ota.py"):                       # #53, wired 2026-08-29
        assert name in staged, "the Zero no longer stages %s" % name
    for name in ("console.py", "wm.py", "wm_windowed.py", "device_canvas.py",
                 "banded_panel.py", "launcher_layer.py", "editors.py",
                 "cart_api.py", "device_boot.py", "blocks.py"):
        assert name not in staged, (
            "the Zero stages %s -- it has no glass and runs no carts; if that "
            "changed, the partition table's slot arithmetic changed with it"
            % name)


def test_the_two_boards_differ_by_exactly_the_presentation_tier():
    """The whole point of the flip, stated as an invariant.

    Two boards, one shared tree: the difference between their frozen consoles
    should be a TIER (a 320x240 fullscreen stack vs a 1024x600 windowed desk),
    not an accident of who remembered to edit which shell script. When this
    fails, read the diff before changing the test -- either a real tier
    difference just appeared, or a module went missing from one board.
    """
    tdeck = set(board_config.denials(TDECK))
    p4 = set(board_config.denials(P4))
    assert tdeck - p4 == {"wm_windowed.py", "surface.py"}, (
        "the S3 denies these and the P4 does not: %s" % sorted(tdeck - p4))
    assert p4 - tdeck == set(), (
        "the P4 denies modules the S3 stages: %s" % sorted(p4 - tdeck))
    # The Guition is the SAME fullscreen tier as the T-Deck, so its shared
    # denials must agree with the T-Deck's exactly -- a third statement of the
    # same tier claim, compared instead of copied.
    guition = set(board_config.denials(GUITION))
    assert guition == tdeck, (
        "the Guition's shared denials differ from the T-Deck's -- guition only:"
        " %s; tdeck only: %s"
        % (sorted(guition - tdeck), sorted(tdeck - guition)))


def test_the_two_tdeck_builds_stage_the_same_shared_console():
    """One board, two build systems, and they must not become two consoles.

    `firmware/lilygo_t_deck_plus_mainline` is the SAME physical T-Deck as
    `firmware/lilygo_t_deck_plus_mainline`, rebuilt on mainline MicroPython.
    Same glass, same 320x240 tier, same `wm.FullscreenStackWM` -- so their
    shared-module DENIALS must agree exactly. A difference here is either a
    module one build forgot or a tier claim nobody wrote down, and both are
    worth failing over while the two targets coexist.

    Their DEVICE staging is deliberately not compared: the two boards' device
    allowlists differ on purpose (the T-Deck drives SD/I2S/diag pieces the P4
    does not), and that difference is each board.toml's to declare.
    """
    fork = board_config.denials(TDECK)
    mainline = board_config.denials(TDECK_MAINLINE)
    assert set(fork) == set(mainline), (
        "the two T-Deck builds deny different shared modules -- fork only: %s; "
        "mainline only: %s"
        % (sorted(set(fork) - set(mainline)), sorted(set(mainline) - set(fork))))
    kinds_differ = sorted(n for n in fork
                          if fork[n].get("kind") != mainline[n].get("kind"))
    assert not kinds_differ, (
        "the two T-Deck builds classify the same denial differently: %s"
        % kinds_differ)


def test_the_mainline_tdeck_stages_no_module_that_needs_the_fork():
    """Tripwire: names the staged trees must never grow.

    `tdeck_display.py`, `moy_compositor.py` and `moy_canvas.py` drove
    `lcd_bus`/`lvgl` and died with the fork (2026-08-17) -- if one of those
    names reappears in a shared tree it should be by decision, not by drift.
    `moy_runtime.py` and `moybyte_shell.py` are BOARD-AUTHORED boot-spine files
    (tracked in each board's `modules/`), and a shared module of the same name
    would SHADOW them, since staging and authorship freeze into one flat
    namespace. That is why `[modules.device]` in the board file is an ALLOWLIST
    and has to stay one.
    """
    staged = set(board_config.staged_modules(TDECK_MAINLINE, ROOT))
    forbidden = {"tdeck_display.py", "moy_compositor.py", "moy_canvas.py",
                 "moy_runtime.py", "moybyte_shell.py"}
    leaked = sorted(staged & forbidden)
    assert not leaked, (
        "the T-Deck stages modules that would shadow board-authored files "
        "or revive fork-only drivers: %s" % leaked)


def test_the_frozen_set_is_derived_from_the_declaration():
    """The staleness this whole file exists to defeat.

    `modules/` is gitignored on both boards and never cleaned, so a module
    dropped from a staging list keeps working on every machine that has built
    before. Deriving the frozen set from that directory would inherit exactly
    the blind spot that let the P4 web console break unnoticed. (The stager
    PRUNES that directory now -- `board.toml`'s `prune` -- but a build that has
    not run yet cannot have pruned anything, so the derivation still has to
    come from the declaration.)

    Board-AUTHORED modules do legitimately live in `modules/` (they are tracked,
    and the P4 whitelists seven of them in .gitignore). What must never happen is
    reading a STAGED copy -- an untracked file some previous build left behind.
    """
    for target in TARGETS:
        untracked = []
        for name, path in frozen_set(target).items():
            if path is None or "/modules/" not in str(path):
                continue
            rel = str(path.relative_to(ROOT))
            r = subprocess.run(["git", "ls-files", "--error-unmatch", rel],
                               cwd=ROOT, capture_output=True)
            if r.returncode != 0:
                untracked.append(rel)
        assert not untracked, (
            "%s: frozen set includes untracked staged copies (stale-build risk):"
            "\n  %s" % (target, "\n  ".join(untracked)))


def test_the_native_tripwire_agrees_with_the_native_declaration():
    """Two statements of one truth, compared -- the same discipline the shared
    Python denials get, applied to the C modules (#161).

    NATIVE above is a hand-written tripwire: the modules each image's imports
    may resolve against. board.toml [native.shared] is the declaration a build
    actually stages. Each board's row must equal its declared shared modules
    plus the board-AUTHORED ones in its own native/ tree -- derived from the
    tree, not restated, so a new board module joins the comparison by
    existing. The web target has no [native] section (its emscripten staging
    is structurally different) and stays hand-listed."""
    for target, board_dir in (("tdeck-mainline", TDECK_MAINLINE), ("p4", P4),
                              ("guition-s3", GUITION)):
        shared = set(board_config.native_modules(board_dir, ROOT))
        authored = {p.name for p in (board_dir / "native").iterdir()
                    if p.is_dir() and not p.name.startswith(".")
                    and (p / "micropython.cmake").exists()}
        assert shared | authored == NATIVE[target], (
            "%s: NATIVE tripwire %s != declaration %s + board-authored %s"
            % (target, sorted(NATIVE[target]), sorted(shared),
               sorted(authored)))


def test_every_authored_web_module_reaches_the_bundle():
    """build.sh copies the runner's own modules BY NAME, and a name left out of
    that list fails in the quietest way this tree has.

    `update_link.py` was missing from it for the whole life of the feature. The
    import that needs it sits inside a try/except that prints to a WORKER
    console, the page went on reporting an updater because that message is sent
    when the board's PROBE answers rather than when the console binds one, and
    the browser suite asserted on the message. Green everywhere; no update row
    on the one board whose only screen is that page.

    So: every module in the directory is in the copy loop, and the two lists
    that disagreed are one derivation now.
    """
    # COMMENTS STRIPPED. A first version of this grepped the whole file and
    # passed its own mutation test, because the comment above the copy loop
    # names `update_link.py` -- the check has to read what the script DOES.
    sh = "\n".join(line.split("#", 1)[0]
                   for line in (WEB / "build.sh").read_text(encoding="utf-8")
                                                 .splitlines())
    authored = sorted(p.stem for p in WEB.glob("*.py")
                      if p.stem not in WEB_HOST_ONLY)
    assert len(authored) >= 4, authored
    for name in authored:
        assert name in sh, (
            "%s.py is an authored web module and build.sh never copies it into "
            "the stage -- it will ImportError at runtime" % name)
    for name in sorted(WEB_HOST_ONLY):
        if (WEB / (name + ".py")).exists():
            assert name not in sh, (
                "%s.py is a host dev tool and must not enter the image" % name)
