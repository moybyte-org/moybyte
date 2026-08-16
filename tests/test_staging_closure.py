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
derived from what a FRESH build produces -- each board's `board.toml` (#161
Phase 3), and the web runner's inline `DENY=` -- and never from `modules/` on
disk, which is precisely where the staleness hides.

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
TDECK = ROOT / "firmware" / "lilygo_t_deck_plus_micropython"
# The SAME board as TDECK, built on mainline MicroPython instead of the
# lvgl_micropython fork. It is a separate target here rather than an assumed
# copy: it stages from TWO sources (runtime/ and the fork build's modules/),
# and the second one is exactly where a shared-tree assumption would go wrong.
TDECK_MAINLINE = ROOT / "firmware" / "lilygo_t_deck_plus_mainline"
P4 = ROOT / "firmware" / "esp32_p4_wifi6_touch_lcd_7b"
WEB = ROOT / "firmware" / "web_runner"
BOARD_DIR = {"tdeck": TDECK, "tdeck-mainline": TDECK_MAINLINE, "p4": P4}

# What MicroPython itself provides. Explicit and module-level on purpose: adding
# a name here is a visible diff that says "the port supplies this", which is a
# claim someone can check, rather than a silently widening regex.
MICROPYTHON_BUILTINS = {
    "array", "binascii", "builtins", "cmath", "collections", "errno", "gc",
    "hashlib", "heapq", "io", "json", "math", "os", "platform", "random", "re",
    "select", "socket", "ssl", "struct", "sys", "time", "zlib",
    # MicroPython-specific
    "machine", "micropython", "network", "esp", "esp32", "bluetooth",
    "framebuf", "neopixel", "uctypes", "_thread", "uasyncio", "asyncio",
    "vfs", "deflate",
}

# Native usermods compiled into a given target (USER_C_MODULES / ext_mod).
NATIVE = {
    "tdeck": {"moy_gfx", "moy_alloc", "moy_sd", "moy_audio", "moy_lua",
              "moycore", "lvgl", "lcd_bus", "st7789", "task_handler",
              "spi3wire", "i2c", "rgb_bus"},
    # The same shared usermods, plus this board's own panel backend -- and
    # NONE of the fork's lvgl/lcd_bus family, which is the point of the port.
    "tdeck-mainline": {"moy_gfx", "moy_alloc", "moy_sd", "moy_audio", "moy_lua",
                       "moycore", "moy_web", "moy_lcd"},
    "p4": {"moy_gfx", "moy_alloc", "moy_lua", "moycore", "moy_dsi", "moy_ppa",
           "moy_ble_hid"},
    "web": {"moy_gfx", "moy_lua", "moy_audio", "moycore", "js", "jsffi"},
}

# Host-only modules that must NEVER reach a given target: staging one is the
# mirror-image bug, a module that imports fine and then cannot work. Per target
# and not global, because `host_api` is genuinely the WEB head's cart API
# (web_boot.py imports it by name) while being meaningless on a board.
#
# The boards' rows used to be four names shorter than the web's -- no
# `gfx_binding`, `native_build`, `host_canvas` or `input` -- which is to say the
# tripwire was WEAKER on the two targets that actually ship in a kid's hands, for
# no recorded reason. Found 2026-08-15 by cross-checking this table against the
# boards' new `board.toml` denials (#161 Phase 3), which is what that check is
# for: two statements of one truth are only worth having if something compares
# them.
HOST_ONLY = {
    "tdeck": {"host_app", "host_api", "host_canvas", "lua_host", "input",
              "audio_binding", "lua_binding", "gfx_binding", "native_build",
              "simulate_desktop"},
    "tdeck-mainline": {"host_app", "host_api", "host_canvas", "lua_host",
                       "input", "audio_binding", "lua_binding", "gfx_binding",
                       "native_build", "simulate_desktop"},
    "p4": {"host_app", "host_api", "host_canvas", "lua_host", "input",
           "audio_binding", "lua_binding", "gfx_binding", "native_build",
           "simulate_desktop"},
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
    "tdeck": {"carts_data", "_ota_build"},
    "tdeck-mainline": {"carts_data", "_ota_build"},
    "p4": {"carts_data", "_ota_build"},
    # The web GENERATES its palette (a literal twin of runtime/palette.py, which
    # needs CPython colorsys) -- which is exactly the fix the boards lack below.
    "web": {"palette"},
}

# Directory-shaped modules: a package staged wholesale rather than file by file.
PACKAGES = {"tdeck": {"moybyte"}, "tdeck-mainline": {"moybyte"},
            "p4": {"moybyte"}, "web": set()}

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


def _cp_staged(sh, board_dir):
    """`cp "${REPO_ROOT}/runtime/a.py" "${SCRIPT_DIR}/modules/b.py"`.

    The RENAME matters: font.py is staged as moy_font.py, and a rule that keyed
    on the source name would think `import moy_font` was unsatisfied.
    """
    found = {}
    pat = r'cp\s+"\$\{(\w+)\}/([^"]+?)"\s+"\$\{\w+\}/([^"]+\.py)"'
    for m in re.finditer(pat, sh):
        src_var, src_path, dst = m.group(1), m.group(2), m.group(3)
        # A destination nested below modules/ is a PACKAGE member (moybyte/
        # input.py). Its importable name is the package's, not the file's, so
        # keying it by basename would invent a top-level `input` module and then
        # report it as an untracked stray. PACKAGES covers these.
        tail = dst.split("modules/", 1)[-1]
        if "/" in tail:
            continue
        base = ROOT if src_var in ("REPO_ROOT",) else board_dir
        p = base / src_path
        if p.exists():
            found[Path(tail).stem] = p
    return found


def _for_staged(sh):
    """`for f in a.py b.py \\\n c.py; do cp "${SRC}/${sub}${f}" ...`."""
    found = {}
    pat = (r'for f in ((?:[^;]|\n)*?); do\s*\n\s*cp\s+'
           r'"\$\{(\w+)\}/([^"]*?)\$\{f\}"')
    for m in re.finditer(pat, sh):
        names, src_var, sub = m.group(1), m.group(2), m.group(3)
        base = ROOT if src_var == "REPO_ROOT" else (
            TDECK if src_var == "TDECK_DIR" else ROOT)
        for n in names.replace("\\\n", " ").split():
            if not n.endswith(".py"):
                continue
            p = base / sub / n
            if p.exists():
                found[Path(n).stem] = p
    return found


def _web_staged(sh):
    """The web runner globs runtime/*.py minus an inline DENY list.

    It got there first; both boards followed in #161 Phase 3, where the
    equivalent list moved into `board.toml` so the REASON for each exclusion
    could live beside it. This one is still shell, so it is still parsed.
    """
    m = re.search(r'DENY="([^"]*)"', sh)
    deny = set(m.group(1).split()) if m else set()
    found = {p.stem: p for p in (ROOT / "runtime").glob("*.py")
             if p.name not in deny}
    found.pop("__init__", None)
    return found


def frozen_set(target):
    """The modules a FRESH build of `target` freezes, and where each comes from.

    Deliberately NOT `os.listdir(modules/)`: that directory is gitignored and
    never cleaned, so it still holds files that no build produces any more --
    which is the precise blind spot that let the P4 web console break with
    every developer's board still working.
    """
    if target == "web":
        sh = (WEB / "build.sh").read_text()
        mods = _web_staged(sh)
        mods.update(_cp_staged(sh, WEB))
        mods.update(_for_staged(sh))
        # web_boot/web_canvas are staged by name; the runner's own moy.py and
        # serve.py are HOST dev tools and never enter the image.
        for name in ("web_boot", "web_canvas"):
            p = WEB / (name + ".py")
            if p.exists():
                mods[name] = p
    else:
        # The boards DECLARE their staging (#161 Phase 3): board.toml holds the
        # denylist over runtime/ and the allowlist over the T-Deck's device
        # tree, and build.sh does nothing but call the stager. So this reads the
        # declaration rather than re-deriving it from shell syntax -- one source
        # for what a build produces, and the regex extractors below survive only
        # for the web runner, which still declares its DENY inline.
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


TARGETS = ("tdeck", "tdeck-mainline", "p4", "web")


def _unresolved(target):
    """[(module, path, missing-alternatives)] for `target`, gaps included."""
    mods = frozen_set(target)
    available = set(mods) | MICROPYTHON_BUILTINS | NATIVE[target]
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
    assert len(mods) > 40, "%s: staging extraction found only %d modules" % (
        target, len(mods))
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


@pytest.mark.parametrize("target", ("tdeck", "tdeck-mainline", "p4"))
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


@pytest.mark.parametrize("target", ("tdeck", "tdeck-mainline", "p4"))
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


@pytest.mark.parametrize("target", ("tdeck", "tdeck-mainline", "p4"))
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


def test_the_two_tdeck_builds_stage_the_same_shared_console():
    """One board, two build systems, and they must not become two consoles.

    `firmware/lilygo_t_deck_plus_mainline` is the SAME physical T-Deck as
    `firmware/lilygo_t_deck_plus_micropython`, rebuilt on mainline MicroPython.
    Same glass, same 320x240 tier, same `wm.FullscreenStackWM` -- so their
    shared-module DENIALS must agree exactly. A difference here is either a
    module one build forgot or a tier claim nobody wrote down, and both are
    worth failing over while the two targets coexist.

    Their DEVICE staging is deliberately not compared: the fork build keeps its
    device modules as tracked files in its own `modules/`, while the mainline
    build stages the same files FROM there through board.toml -- one tree, two
    routes to it, which is the arrangement that lets them share a console at all.
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
    """The three modules the mainline build must never pick up.

    `tdeck_display.py`, `moy_compositor.py` and `moy_canvas.py` all drive
    `lcd_bus`/`lvgl`, which do not exist in a mainline image; `moy_runtime.py`
    and `moybyte_shell.py` are the fork build's own boot spine and would SHADOW
    the mainline board's authored ones, since both trees freeze into one flat
    namespace. That is why `[modules.device]` in the board file is an ALLOWLIST
    and has to stay one -- a denylist over a board tree picks all five up the
    moment somebody adds a file.
    """
    staged = set(board_config.staged_modules(TDECK_MAINLINE, ROOT))
    forbidden = {"tdeck_display.py", "moy_compositor.py", "moy_canvas.py",
                 "moy_runtime.py", "moybyte_shell.py"}
    leaked = sorted(staged & forbidden)
    assert not leaked, (
        "the mainline T-Deck stages fork-only modules from the fork's board "
        "tree: %s" % leaked)


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
