"""The one-buffer compositor: what a browser and a desktop have instead of a panel.

`device_canvas.DeviceCanvas` asks a compositor for four things (`size`,
`framebuffer`, `back_buffer`, `gfx`) and probes for the rest. On the boards
the answers involve DMA-capable PSRAM, a double or triple buffer and a
driver; in the browser and on the host the buffer is a plain bytearray read
straight out of memory, so there is nothing to swap and nothing to flush:
`back_buffer` returns the one buffer, which makes `DeviceCanvas.sync_back` a
no-op.

The three last-frame verbs (docs/surface_model_v1.md §14.5) are the boards':
the one buffer IS the last frame's, so `copy_last` has nothing to copy,
`shift_last` moves the rect in place and `copy_last_into` copies out of the
one buffer. The host's subclass (`runtime/host/host_canvas.py`) names another
buffer as the last frame's for a test's ring of buffers presented in turn.

Deliberately ABSENT, each one a `getattr` probe in DeviceCanvas that must
fail here: `pump_if_pending` (the T-Deck's SRAM-bounce flush pump),
`fold_supported`/`fold_fence`/`arm_scale_fold` (the #190 S3 composite fold),
and any `_fbs` list (the P4's DPI buffer rotation, which is what sets
RETAINED_FRAMES > 1 there).

The raster module is handed in: `moy_gfx` in the browser, the host's ctypes
binding of the same C (`runtime/host/gfx_binding.py`). The boards deny this
module; it is not theirs.
"""


class OneBufferCompositor:

    def __init__(self, w, h, gfx):
        self._w = int(w)
        self._h = int(h)
        self._buf = bytearray(self._w * self._h * 2)
        self._gfx = gfx

    def size(self):
        return (self._w, self._h)

    def framebuffer(self):
        return self._buf

    def back_buffer(self):
        return self._buf

    def gfx(self):
        return self._gfx

    def _last(self):
        """The buffer the last frame drew into: this one."""
        return self._buf

    def copy_last(self, x, y, w, h):
        """The rect of the buffer the last frame drew into, into the one
        this frame draws into (docs/surface_model_v1.md §14.5)."""
        src = self._last()
        if src is self._buf:
            return True
        W, H = self._w, self._h
        x0, y0 = max(0, x), max(0, y)
        x1, y1 = min(W, x + w), min(H, y + h)
        buf = self._buf
        for r in range(y0, y1):
            a = (r * W + x0) * 2
            b = (r * W + x1) * 2
            buf[a:b] = src[a:b]
        return True

    def shift_last(self, x, y, w, h, dx, dy):
        """The rect of the buffer the last frame drew into, moved by (dx, dy)
        inside it, into the one this frame draws into (moy_gfx's shift_rect);
        in place on the one buffer (docs/theme_and_shell_2026-11.md §9.6)."""
        self._gfx.shift_rect(self._buf, self._last(), self._w, x, y, w, h, dx, dy)
        return True

    def copy_last_into(self, buf, stride, x, y, w, h, lx=0, ly=0):
        """The rect of the buffer the last frame drew into, at (lx, ly) of
        `buf` (a layer `stride` px a row), clipped to both."""
        self._gfx.copy_block(buf, stride, self._last(), self._w, lx, ly, x, y, w, h)
        return True
