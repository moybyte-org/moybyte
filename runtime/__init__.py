"""Moybyte userland runtime (the HOST end of the console).

The "other end" of the stack from the native graphics core: the fantasy
workstation surface a cartridge runs on. The drawing API is language-neutral by
design (indices in, the same verbs everywhere) so the same `.moy` runs on both
boards, in the browser and here.

There is no host canvas CLASS to export any more: the raster is the boards' own
`device_canvas.DeviceCanvas`, built for CPython by `runtime/host/host_canvas.py`
(`make_canvas` / `make_system_canvas`).
"""

import importlib as _importlib
import os as _os
import sys as _sys
from importlib.machinery import ModuleSpec as _ModuleSpec


def _folders():
    """runtime/ is ONE flat module namespace kept in folders. The boards stage
    every file flat into `modules/` (tools/board_config.py), so `import
    console` names one module wherever its file sits; here the package path
    spans every subfolder, so `runtime.console` does too. Basenames are unique
    across the tree: the stager refuses a duplicate."""
    here = __path__[0]
    out = []
    for base, dirs, _files in _os.walk(here):
        dirs[:] = sorted(d for d in dirs
                         if d != "__pycache__" and not d.startswith("."))
        if base != here:
            out.append(base)
    return out


__path__.extend(_folders())


def _flat_names():
    """Every module's basename across the folders."""
    return frozenset(f[:-3] for folder in __path__ for f in _os.listdir(folder)
                     if f.endswith(".py") and f != "__init__.py")


class _SameModule:
    """Hands the import machinery the module `runtime.<name>` is. It imports
    that module at create time, not while the finder looks: a module whose
    own imports reach its flat name again then meets itself half-built, as
    any circular import does, and is never executed a second time."""

    def __init__(self, name):
        self._name = "runtime." + name
        self._spec = None

    def create_module(self, spec):
        module = _importlib.import_module(self._name)
        self._spec = module.__spec__
        return module

    def exec_module(self, module):
        if getattr(self._spec, "name", None) == self._name:
            module.__spec__ = self._spec      # it stays `runtime.<name>`


class FlatFinder:
    """`import console` is `runtime.console`, one module object, as a board's
    flat image has one: the boards and the browser freeze every runtime file
    by basename, and here a bare runtime name resolves to the package's
    module. It sits first on `sys.meta_path`, so a build-staged copy on
    `sys.path` never stands in for the source."""

    names = frozenset()

    def find_spec(self, fullname, path=None, target=None):
        if path is not None or fullname not in self.names:
            return None
        return _ModuleSpec(fullname, _SameModule(fullname))


FlatFinder.names = _flat_names()
_sys.meta_path[:] = [f for f in _sys.meta_path
                     if type(f).__qualname__ != "FlatFinder"]
_sys.meta_path.insert(0, FlatFinder())


def _register(name, face, binding):
    """The native module `name` on the CPython host: its face in
    runtime/host (`face`), or what that face's binding() builds, registered
    as `name` and `runtime.<name>` before anything imports either."""
    mod = _sys.modules.get(name)
    if mod is None:
        mod = _importlib.import_module(face)
        if binding:
            mod = mod.binding()
            if mod is None:
                raise ImportError("the host's %s needs a C compiler" % name)
        _sys.modules[name] = mod
    _sys.modules["runtime." + name] = mod
    globals()[name] = mod


# The host faces, from native/*/face.json (runtime/host/faces.py, generated):
# the spine first, which every other's imports may reach.
from .faces import FACES as _FACES  # noqa: E402

for _face in _FACES:
    _register(*_face)


from .editors_code import CodeEditor  # noqa: E402
from .editors_paint_map import PaintEditor  # noqa: E402
from .editors_sheet import SpriteSheet  # noqa: E402
from .input import InputState  # noqa: E402
from .moy_image import Image  # noqa: E402

__all__ = [
    "Image",
    "CodeEditor",
    "PaintEditor",
    "SpriteSheet",
    "InputState",
]
