"""Repo-wide pytest setup: bare imports of SHARED runtime modules must always
resolve to the canonical runtime/ source, never a build-staged copy.

Several test files put the firmware staging tree
(firmware/lilygo_t_deck_plus_micropython/modules) on sys.path to import the
AUTHORED device modules (moy_webserver, device canvas parity, diag, ...). That
same directory also holds BUILD-STAGED copies of the shared runtime/ sources
(console.py, editors.py, web_view.py, web_view_page.py, ... -- gitignored,
re-staged by every firmware build), and both the device modules and the shared
modules themselves import each other bare-first (the device freeze has no
`runtime` package). So once the staging dir is on sys.path, the FIRST bare
import of a shared name binds whatever the LAST firmware build staged --
yesterday's artifact silently shadowing today's runtime/ edit, with the winner
decided by pytest's collection order.

This bit for real (2026-07-10): a runtime/web_view_page.py fix made its
page-source guard (test_browser_page_sends_neutral_pan_on_arrow_release) pass
alone but fail in every full-suite run, because test_moy_webserver.py's
collection had already cached the pre-fix staged page under the bare
`web_view_page` name, and runtime/web_view.py's bare-first import picked it up.

The meta-path finder below routes any bare import whose name matches a
runtime/*.py module to that runtime module object itself. sys.meta_path is
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

_RUNTIME_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "runtime")
_SHARED = {
    f[:-3] for f in os.listdir(_RUNTIME_DIR)
    if f.endswith(".py") and f != "__init__.py"
}
# Shared modules the build stages under a DIFFERENT bare name.
_RENAMED = {"moy_font": "font"}


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
    "firmware", "lilygo_t_deck_plus_micropython", "modules")
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
    dir on sys.path itself (test_moy_webserver does, at import time, for the
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
