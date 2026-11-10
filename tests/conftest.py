"""Repo-wide pytest setup: bare imports of SHARED runtime modules must always
resolve to the canonical runtime/ source, never a build-staged copy.

Several test files put the firmware staging tree
(device) on sys.path to import the
AUTHORED device modules (moy_webhost, device canvas parity, diag, ...). That
same directory also holds BUILD-STAGED copies of the shared runtime/ sources
(console.py, editors_code.py, web_view.py, web_view_page.py, ... -- gitignored,
re-staged by every firmware build), and both the device modules and the shared
modules themselves import each other bare-first (the device freeze has no
`runtime` package). So once the staging dir is on sys.path, the FIRST bare
import of a shared name binds whatever the LAST firmware build staged --
yesterday's artifact silently shadowing today's runtime/ edit, with the winner
decided by pytest's collection order.

This bit for real (2026-07-10): a runtime/web_view_page.py fix made its
page-source guard (test_browser_page_sends_neutral_pan_on_arrow_release) pass
alone but fail in every full-suite run, because a since-deleted suite's
collection had already cached the pre-fix staged page under the bare
`web_view_page` name, and runtime/web_view.py's bare-first import picked it up.

The meta-path finder below routes any bare import whose name matches a
runtime/ module (any subfolder) to that runtime module object itself. sys.meta_path is
consulted before sys.path, so the repo source always wins on the host no
matter what staging dirs a test added or what order files were collected in.
Authored device-only modules (no runtime/ counterpart) are untouched, and the
device itself is untouched by definition (conftest never ships).
"""

import importlib
import importlib.util
import importlib.abc
import importlib.machinery
import os
import sys

import pytest

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# The machine's timing log (tools/timings.py) records real runs only: a test
# that runs a timed tool writes nowhere unless it names its own log.
os.environ["MOYBYTE_TIMINGS"] = os.devnull
# ONE repo-root sys.path insert for the whole suite (2026-08-18). 83 test
# files each carried the same four-line header for this; pytest imports
# conftest before collecting any of them, so this is the only copy now. Test
# files keep their OWN inserts only for EXTRA dirs some need (ROOT/"tools", a
# board modules tree, an experiments dir).
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

_RUNTIME_DIR = os.path.join(_REPO_ROOT, "runtime")
# Every module under runtime/, subfolders included: the package's __path__
# spans them, so `runtime.<name>` resolves whichever folder holds the file.
_SHARED = {
    f[:-3] for _base, _dirs, _files in os.walk(_RUNTIME_DIR) for f in _files
    if f.endswith(".py") and f != "__init__.py"
}
# Shared modules the build stages under a DIFFERENT bare name, and native
# modules whose host face is a ctypes binding under runtime/.
_RENAMED = {"moy_font": "font", "moy_net": "net_binding"}


class _AliasLoader(importlib.abc.Loader):
    """Hand the import machinery an EXISTING module object (the runtime one)."""

    def __init__(self, module):
        self._module = module

    def create_module(self, spec):
        return self._module

    def exec_module(self, module):
        pass                      # already executed as runtime.<name>


class _SharedRuntimeAliasFinder(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        name = _RENAMED.get(fullname, fullname)
        if name not in _SHARED:
            return None           # not a shared runtime module -> normal lookup
        module = importlib.import_module("runtime." + name)
        return importlib.machinery.ModuleSpec(fullname, _AliasLoader(module))


# Device-only modules: authored in the T-Deck modules/ tree with no runtime/
# counterpart, so the finder above deliberately ignores them. They import each
# other by bare name the way the frozen device build does (device_api does
# `from moy_lua_glue import ...`), which on the host resolves only if something
# already put that name in sys.modules.
#
# Test files used to do that by hand, listing the modules they expected to
# need. A list like that is wrong the moment a device module gains an import:
# test_device_seed_parity.py's omitted moy_lua_glue, so it passed in a full run
# -- where an earlier-collected file happened to have registered it -- and
# failed with 11 errors when run alone. That is the worst failure shape there
# is: it punishes exactly the person running the one file they just touched.
#
# Resolving them from the tree instead means the next device module needs no
# test change. Runtime modules still win: _SHARED is subtracted, so a staged
# copy can never shadow the source of truth.
_DEVICE_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "device")
# `_ota_build` is EXCLUDED: build.sh writes it (gitignored) with this machine's
# last build channel/version/label, and moy_ota imports it when present. Resolving
# it here made the suite read differently on a machine that had built firmware than
# on one that had not -- which is how a stale `LABEL="v2"` masked the FIRMWARE_NAME
# change locally and only failed in CI. Host tests see the committed source's
# identity, always; a test that wants a stamp sets the module globals itself.
_BUILD_STAMPED = {"_ota_build"}
_DEVICE_ONLY = {
    f[:-3] for f in os.listdir(_DEVICE_DIR)
    if f.endswith(".py") and f != "__init__.py"
} - _SHARED - set(_RENAMED) - _BUILD_STAMPED


@pytest.fixture(autouse=True)
def _no_local_build_stamp(monkeypatch):
    """The SECOND door onto the same bug the `_ota_build` exclusion above closed.

    build.sh stamps its identity in two places: the `_ota_build` module the device
    imports (excluded above) and `dist/current/ota_build.json`, which
    tools/gen_ota_manifest reads as `CLI > stamp > FIRMWARE_VERSION`. Nothing
    neutralized the second, so on a machine that had built firmware the manifest
    defaulted to THAT build's version and test_main_defaults_version_from_moy_ota
    failed -- while CI, with no dist/ tree, passed. Green or red by whether you
    had run build.sh, which is precisely what 867e676 set out to end.

    Stubbing the READER (not the OTA_BUILD_JSON constant) is deliberate:
    `def read_ota_build(path=OTA_BUILD_JSON)` binds that default at def time, so
    monkeypatching the constant is accepted in silence and changes nothing. A
    test that wants a stamp sets this back to `lambda *_: {...}` -- explicit,
    like the `_ota_build` rule above.

    THIRD door (2026-08-03): the finder exclusion above only stops THIS conftest
    from resolving `_ota_build` -- a test file that puts the firmware modules/
    dir on sys.path itself (a suite that does so at import time, for the
    whole process) re-opens it, and under xdist whichever test execs moy_ota.py
    on that worker afterwards reads the machine's last build stamp again
    (spotted as version_label()=='v2' from a stale bisect build). sys.modules
    [name]=None makes `import _ota_build` raise ImportError no matter what the
    path says, and moy_ota's try/except then keeps the committed identity."""
    monkeypatch.setitem(sys.modules, "_ota_build", None)
    try:
        import gen_ota_manifest
    except ImportError:      # tools/ not on the path for this test module
        return
    monkeypatch.setattr(gen_ota_manifest, "read_ota_build", lambda *a, **k: {})


class _DeviceModuleFinder(importlib.abc.MetaPathFinder):
    """Resolve a bare device-only import from the authored modules/ tree."""

    def find_spec(self, fullname, path=None, target=None):
        if fullname not in _DEVICE_ONLY:
            return None
        return importlib.util.spec_from_file_location(
            fullname, os.path.join(_DEVICE_DIR, fullname + ".py"))


# Runtime aliasing first: a device module must never shadow a shared one.
sys.meta_path.insert(0, _DeviceModuleFinder())
sys.meta_path.insert(0, _SharedRuntimeAliasFinder())

# A module this checkout no longer has fails here as it fails in CI: neither
# the shared venv's editable finder (which maps `runtime` to the MAIN
# checkout) nor a stale build-staged copy may stand in for it
# (tests/import_guard.py, pinned by tests/test_import_guard.py).
import import_guard  # noqa: E402  (tests/ is on sys.path for its conftest)

import_guard.drop_foreign_editable_finders(_REPO_ROOT)
sys.meta_path.insert(2, import_guard.StagedCopyRefusal(
    import_guard.staging_dirs(_REPO_ROOT)))


def pytest_addoption(parser):
    """`--update-goldens` -- the explicit re-baseline switch for the shell
    pixel goldens (tests/test_shell_goldens.py, UI refactor Phase 0).

    Re-baselining must be a deliberate act, never a side effect of a run, so
    the harness rewrites tests/shell_goldens/hashes.json ONLY when this flag
    (or MOYBYTE_UPDATE_GOLDENS=1) is given. Registered here because pytest
    accepts pytest_addoption in initial conftests only, and pyproject's
    `testpaths = ["tests"]` makes this one initial for a bare `pytest` too."""
    parser.addoption("--update-goldens", action="store_true", default=False,
                     help="rewrite tests/shell_goldens/hashes.json from this "
                          "run (shell pixel goldens; run with -p no:xdist)")


@pytest.fixture(autouse=True)
def _audio_console_level_back():
    """The kernel's audio is one per process (native/moy_audio's session table,
    the console's level, the mix's rate), as it is one per board. A test that
    turns the console down would silence every later test on the worker, so
    the level goes back to full after each."""
    yield
    try:
        from runtime import audio_session
    except ImportError:
        return
    na = audio_session._NA[0]
    if na:
        na.volume(7)


def _theme_reset():
    try:
        from runtime import ui_draw
    except ImportError:
        return
    ui_draw.use_shape(None)
    ui_draw.use_depth(None, False)
    ui_draw.theme_uninstall()
    ui_draw.cart_text(False)
    for role in ("ui", "title", "mono", "caption", "key", "op", "result"):
        ui_draw.use_font(role, 0)
    ui_draw.use_hero(0)
    ui_draw.print_font(0)
    # ...and the bars a look placed (its layout, its bar heights) are the
    # default's again, as the depth is
    from runtime import layout_base
    layout_base.place_bar_h()
    layout_base.place_bars(layout_base.DEFAULT_BARS)
    layout_base.place_class(layout_base.HANDHELD)
    layout_base.in_window(False)
    layout_base.in_pane(False)
    ui_draw.input_kind("keys")
    ui_draw.marks(True, True)
    # the frames' clock is the console's, set each loop pass: a test that
    # begins frames itself starts with none (every glide lands at once)
    ui_draw.frame_dt(0)
    ui_draw.moving_take()


@pytest.fixture(autouse=True)
def _theme_shape_back():
    """The toolkit's installed shape is one per process (native/moy_ui), as it
    is one per board, and a workstation installs its theme's at boot. A test
    that built one would restyle every widget a later test on the worker
    draws, so before and after each test the empty shape is in force, the
    depth and the marks are the default theme's in its dark mode (the shape
    does not carry them; a workstation's `_go_live` installs its own), no
    theme package is installed (a later test's Settings would list its
    layout), every font role and the print font are petme128, the bars are
    the default's, and the input in use, the marks' focus switch and the
    metric class are a fresh process's (keys, on, the handheld's): a
    workstation places its class as it boots, and a test that left the
    desk's or touch in force would lay out and mark every later test's parts
    as that one's."""
    _theme_reset()
    yield
    _theme_reset()


@pytest.fixture(autouse=True)
def _console_state_fresh():
    """The toolkit's per-console state (the painted frame, what was drawn
    over it, the windowed WM's word, the host holding the keys) is one per
    process, as the console is one per board, and every workstation a test
    builds counts its frames from 0. A frame number a test left behind
    names a frame of the next test's: the FPS chip one drew over frame 2
    read as drawn over the next one's page at frame 2, whose frame then drew
    whole, so a draw budget or a shift frame's kind held only on the workers
    no such test had run on. Each test starts with a fresh process's
    (`ui_draw.console_reset`)."""
    try:
        from runtime import ui_draw
    except ImportError:
        yield
        return
    ui_draw.console_reset()
    yield
    ui_draw.console_reset()


def pytest_configure(config):
    config.addinivalue_line(
        "markers", "first_run: the test's carts are asked before their first run "
        "(app_perms.asks_first), as a cart that arrived as files is")


# Whether the running test is marked `first_run` (`_carts_are_made_here`).
_FIRST_RUN = [False]


@pytest.fixture(autouse=True, scope="session")
def _carts_are_made_here():
    """A test writes its carts as files (`moy_carts.create`, a fixture copied
    into the store), which on a console is a cart that arrived as files and
    is asked for its permissions on its first run (`app_perms.asks_first`).
    Every test's carts, a module fixture's included, stand for carts made on
    the console, never asked; a test of the first-run prompt is marked
    `first_run` (`_first_run_marked`)."""
    try:
        from runtime import app_perms
    except ImportError:
        yield
        return
    real = app_perms.asks_first
    app_perms.asks_first = lambda cart, rec: _FIRST_RUN[0] and real(cart, rec)
    yield
    app_perms.asks_first = real


@pytest.fixture(autouse=True)
def _first_run_marked(request):
    _FIRST_RUN[0] = request.node.get_closest_marker("first_run") is not None
    yield
    _FIRST_RUN[0] = False


# What `_cover_budget_unbounded` raises the cover slice to: every cover builds
# inside the frame that asks for it.
COVER_SLICE_UNBOUNDED = 10 ** 9


@pytest.fixture(autouse=True)
def _cover_budget_unbounded(monkeypatch):
    """A cover build spends a per-frame TIME slice (`cover_cache._COVER_SLICE_MS`),
    so on a loaded machine a cover lands a frame or three later than on an idle
    one, and two renders a test compares (a live edit and a cold boot, a window
    and its fresh render, an idle frame count) differ by how busy the machine
    was. Every test builds each cover in its own frame; a test of the budget
    sets the slice itself."""
    try:
        from runtime import cover_cache
    except ImportError:
        return
    monkeypatch.setattr(cover_cache, "_COVER_SLICE_MS", COVER_SLICE_UNBOUNDED)


@pytest.fixture(autouse=True)
def _webhost_switched_off():
    """The kernel's webhost and its switch are one per process (native/moy_net),
    as they are one per board, and a host's `handle_http` keeps whatever store,
    sign-in and routes a switch still on its port was configured with ("never
    re-point a kernel host that serves"). A test that left the switch joining,
    serving or in its goodbye window handed a later test on the worker another
    host's sign-in and routes (a 401 for an unsealed /gpio batch, a 404 for
    /console.json), or a 503 `closing`. After each test the switch is off, the
    host stopped now with its sign-in off (its sessions, keys and wrong tries
    forgotten) and the browser's end (tests/seal_client.py) signed out, as a
    fresh process has them; a process that never loaded the library has
    neither."""
    yield
    nb = sys.modules.get("runtime.net_binding")
    if nb is None or nb._LIB[0] is None:
        return
    nb.wc_off()
    nb.web_stop()
    nb.web_auth(None)            # the next folder starts its backoff afresh
    nb._LIB[0].moy_sw_reset()
    nb.web_events()
    nb.wc_poll()                 # a switch in its goodbye sees the host stopped


def _processes_reset():
    L = sys.modules.get("runtime.moy_log")
    if L is not None:
        L.current(L.SHELL)
    for name in ("runtime.leases", "runtime.devices"):
        m = sys.modules.get(name)
        if m is not None:
            gate = getattr(m, "LEASES", m)
            gate.grants = None
    F = sys.modules.get("runtime.front")
    if F is not None:
        F.FRONT.bind(None, None)
    N = sys.modules.get("runtime.notifications")
    if N is not None and (N.NOTES.list or N.NOTES.banner is not None):
        N.NOTES.reset()
    E = sys.modules.get("runtime.moy_evq")
    if E is not None and E._LIB[0] is not None:
        E.notes_reset()             # the kernel's notifications and badges (moy_notes.h)
    SI = sys.modules.get("runtime.status_items")
    if SI is not None:
        SI.ITEMS.sync()
        for it in SI.ITEMS.slots:
            if it.pid:
                SI.ITEMS._free(it)
    DM = sys.modules.get("runtime.dev_media")
    if DM is not None:
        DM._HEARD[0] = None
    SL = sys.modules.get("runtime.shell_layout")
    if SL is not None:
        SL.ITEMS[0] = 0
    A = sys.modules.get("runtime.achievements")
    if A is not None:
        A.end()
    S = sys.modules.get("runtime.scheduler")
    if S is not None:
        sched = S.SCHED
        w = sched.wakes
        w.ended, w.opener = sched._ended, sched._open
        w.service = sched.own
        for pid in [p.pid for p in sched.procs.values() if p.state == S.EXITED]:
            del sched.procs[pid]


@pytest.fixture(autouse=True)
def _processes_fresh():
    """Whose code runs, who grants what and what the processes posted are
    one per process, as they are one per board: the kernel's current process
    (`moy_log.current`), which a run makes itself until it ends; the lease
    and device gates (`leases.LEASES.grants`, `devices.grants`), which a
    Workstation binds to its running set as it boots; the notifications
    (`notifications.NOTES`), whose banner stays up BANNER_MS of wall time;
    the apps' status items (`status_items.ITEMS`) with the count the bar
    packs for them (`shell_layout.ITEMS`), which a console brings level only
    at its next frame; and the achievements process (`achievements.end`),
    which serves its queue on whichever console frames next. A test that left a game running handed every later
    test on the worker that game's pid, one that built a Workstation handed
    it that console's grants, so the shell's own reads (a /dev file) and a
    bare process's lease were refused as another's; a badge an earlier
    test's achievements process posted into the next test's console stayed
    up as a banner, which keeps a console animating and draws over the bar;
    and an item freed after its console's last frame left the count at 1, so
    the next console's first layout placed the bar's cells around a status
    item it did not have; a badge the shell sent as a test ended was served
    in the next test's console, whose banner took a tap meant for the page. Each test starts, and leaves, the shell current,
    the gates unbound (the front's among them, `front.FRONT`), no
    notification, no status item and no achievements process, as a fresh VM has them before its
    console boots. The scheduler is one per VM too: a `Scheduler` built over
    the shared wakes takes their hooks (a badge's goal read from ITS rows, so
    a game of the next test's coalesced with no goal), and a crashed
    service's row stays until the task manager's Close (a later test's task
    list held another's crashed service); each test starts with `SCHED`'s
    hooks and no exited row."""
    _processes_reset()
    yield
    _processes_reset()
