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
import importlib.abc
import importlib.machinery
import os
import sys

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


sys.meta_path.insert(0, _SharedRuntimeAliasFinder())
