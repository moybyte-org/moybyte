"""Moybyte v0.4 userland runtime (host reference implementation).

The "other end" of the stack from the native graphics core: the fantasy
workstation surface a cartridge runs on. 480x270 indexed canvas, PICO-8-style
drawing API, the .moy cartridge model, and a Run loop with friendly errors.
The drawing API is language-neutral by design so it can later sit on the native
moy_compositor (device) or a Lua VM, not just this host Python reference.
"""

from . import palette
from .canvas import Canvas, Image
from .editors import CodeEditor, PaintEditor, SpriteSheet
from .input import InputState

__all__ = [
    "Canvas",
    "Image",
    "CodeEditor",
    "PaintEditor",
    "SpriteSheet",
    "InputState",
    "palette",
]
