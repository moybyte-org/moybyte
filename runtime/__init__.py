"""Moybyte userland runtime (the HOST end of the console).

The "other end" of the stack from the native graphics core: the fantasy
workstation surface a cartridge runs on. The drawing API is language-neutral by
design (indices in, the same verbs everywhere) so the same `.moy` runs on both
boards, in the browser and here.

There is no host canvas CLASS to export any more: the raster is the boards' own
`device_canvas.DeviceCanvas`, built for CPython by `runtime/host_canvas.py`
(`make_canvas` / `make_system_canvas`).
"""

import sys as _sys


def _native_spine():
    """The kernel's spine is C on every tier (docs/kernel_spine_2026-10.md):
    on the CPython host it is native/moy_spine's host library over ctypes
    (`tools/moy_spine_binding.py`), registered as `moy_spine` and
    `runtime.moy_spine` before anything imports either name. Its Python twin
    is the suites' oracle (tests/spine_twin.py), never the host's."""
    if "moy_spine" in _sys.modules:
        mod = _sys.modules["moy_spine"]
    else:
        from tools import moy_spine_binding
        mod = moy_spine_binding.binding()
        if mod is None:
            raise ImportError("the host's spine needs a C compiler "
                              "(native/moy_spine over ctypes)")
        _sys.modules["moy_spine"] = mod
    _sys.modules["runtime.moy_spine"] = mod
    return mod


moy_spine = _native_spine()


def _native_app():
    """The app ABI's C rows (native/moy_app) over the same host library as
    the spine, registered as `moy_app` like it."""
    if "moy_app" in _sys.modules:
        return _sys.modules["moy_app"]
    from tools import moy_app_binding
    mod = moy_app_binding.binding()
    if mod is None:
        raise ImportError("the host's app ABI needs a C compiler "
                          "(native/moy_app over ctypes)")
    _sys.modules["moy_app"] = mod
    return mod


moy_app = _native_app()

from . import palette  # noqa: E402
from .editors import CodeEditor, PaintEditor, SpriteSheet  # noqa: E402
from .input import InputState  # noqa: E402
from .moy_image import Image  # noqa: E402

__all__ = [
    "Image",
    "CodeEditor",
    "PaintEditor",
    "SpriteSheet",
    "InputState",
    "palette",
]
