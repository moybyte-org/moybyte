"""Moybyte userland runtime (the HOST end of the console).

The "other end" of the stack from the native graphics core: the fantasy
workstation surface a cartridge runs on. The drawing API is language-neutral by
design (indices in, the same verbs everywhere) so the same `.moy` runs on both
boards, in the browser and here.

There is no host canvas CLASS to export any more: the raster is the boards' own
`device_canvas.DeviceCanvas`, built for CPython by `runtime/host/host_canvas.py`
(`make_canvas` / `make_system_canvas`).
"""

import os as _os
import sys as _sys


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


def _native_spine():
    """The kernel's spine is C on every tier (docs/history/kernel_spine_2026-10.md):
    on the CPython host it is native/moy_spine in the host's one library
    (runtime/host/host_native.py) over ctypes
    (`runtime/host/moy_spine_binding.py`), registered as `moy_spine` and
    `runtime.moy_spine` before anything imports either name. Its Python twin
    is the suites' oracle (tests/spine_twin.py), never the host's."""
    if "moy_spine" in _sys.modules:
        mod = _sys.modules["moy_spine"]
    else:
        from . import moy_spine_binding
        mod = moy_spine_binding.binding()
        if mod is None:
            raise ImportError("the host's spine needs a C compiler "
                              "(native/moy_spine over ctypes)")
        _sys.modules["moy_spine"] = mod
    _sys.modules["runtime.moy_spine"] = mod
    return mod


moy_spine = _native_spine()


def _native_app():
    """The app ABI's C rows (native/moy_app) over the host's one library,
    registered as `moy_app` like the spine."""
    if "moy_app" in _sys.modules:
        return _sys.modules["moy_app"]
    from . import moy_app_binding
    mod = moy_app_binding.binding()
    if mod is None:
        raise ImportError("the host's app ABI needs a C compiler "
                          "(native/moy_app over ctypes)")
    _sys.modules["moy_app"] = mod
    return mod


moy_app = _native_app()


def _native_ufiles():
    """The user-files layer (native/moy_store/moy_ufiles.h) over ctypes,
    registered as `moy_ufiles` like the boards' module. Its library is built at
    the first verb a caller uses (runtime/host/moy_ufiles_binding.py)."""
    if "moy_ufiles" in _sys.modules:
        return _sys.modules["moy_ufiles"]
    from . import moy_ufiles_binding
    mod = moy_ufiles_binding.binding()
    _sys.modules["moy_ufiles"] = mod
    return mod


moy_ufiles = _native_ufiles()

def _native_ui():
    """The toolkit's C (native/moy_ui) over the host's one library,
    registered as `moy_ui` like the boards' module (runtime/host/moy_ui_binding.py)."""
    if "moy_ui" in _sys.modules:
        return _sys.modules["moy_ui"]
    from . import moy_ui_binding
    mod = moy_ui_binding.binding()
    if mod is None:
        raise ImportError("the host's toolkit needs a C compiler "
                          "(native/moy_ui over ctypes)")
    _sys.modules["moy_ui"] = mod
    return mod


moy_ui = _native_ui()

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
